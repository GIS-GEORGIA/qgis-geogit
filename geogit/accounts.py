"""Server accounts: details in QgsSettings, the token in the encrypted QGIS auth database.

Tokens are read only on the main thread (the auth database may ask for the
master password) and kept in memory for git's background commands.
"""

import uuid

from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

from . import hosting

GROUP = "geogit/accounts"
_TOKENS = {}          # account id -> token, filled on the main thread
_CREDS = {}           # host -> (git user name, token), what background git commands read


def _unpack(result, fallback):
    """QGIS returns (ok, config) from the auth calls in some versions and just ok in others."""
    if isinstance(result, tuple):
        return bool(result[0]), result[1] if len(result) > 1 else fallback
    return bool(result), fallback


def list_accounts():
    s = QgsSettings()
    s.beginGroup(GROUP)
    ids = s.childGroups()
    s.endGroup()
    out = []
    for aid in ids:
        base = f"{GROUP}/{aid}/"
        out.append({"id": aid, "service": s.value(base + "service", ""), "url": s.value(base + "url", ""),
                    "email": s.value(base + "email", ""), "username": s.value(base + "username", ""),
                    "authcfg": s.value(base + "authcfg", "")})
    return out


def find(service, url):
    host = hosting.host_of(url)
    return next((a for a in list_accounts() if a["service"] == service and hosting.host_of(a["url"]) == host), None)


def save(account):
    """Store (or replace) the account; returns its id. Raises RuntimeError when the
    auth database cannot be opened (e.g. the master password was not given)."""
    am = QgsApplication.authManager()
    if am.isDisabled():         # a QGIS build without authentication method plugins
        raise RuntimeError(am.disabledMessage().strip() or "the QGIS authentication system is disabled")
    if not am.setMasterPassword(True):
        raise RuntimeError("the QGIS authentication database is locked (master password)")
    old = find(account.service, account.url)
    cfg = QgsAuthMethodConfig("Basic")
    cfg.setName(f"GeoGit · {hosting.PROVIDERS[account.service].label} · {hosting.host_of(account.url)}")
    cfg.setConfig("username", account.email or account.username)
    cfg.setConfig("password", account.token)
    if old and old["authcfg"]:
        cfg.setId(old["authcfg"])
        ok, cfg = _unpack(am.updateAuthenticationConfig(cfg), cfg)
    else:
        ok, cfg = _unpack(am.storeAuthenticationConfig(cfg), cfg)
    if not ok:
        raise RuntimeError("could not save the token in the QGIS authentication database")
    aid = old["id"] if old else uuid.uuid4().hex[:12]
    s = QgsSettings()
    base = f"{GROUP}/{aid}/"
    for key, value in (("service", account.service), ("url", account.url), ("email", account.email),
                       ("username", account.username), ("authcfg", cfg.id())):
        s.setValue(base + key, value)
    _TOKENS[aid] = account.token
    _remember(account)
    return aid


def _remember(account):
    _CREDS[hosting.host_of(account.url)] = (hosting.provider(account).git_username(), account.token)


def remove(aid):
    acc = next((a for a in list_accounts() if a["id"] == aid), None)
    if acc and acc["authcfg"]:
        QgsApplication.authManager().removeAuthenticationConfig(acc["authcfg"])
    QgsSettings().remove(f"{GROUP}/{aid}")
    _TOKENS.pop(aid, None)
    if acc:
        _CREDS.pop(hosting.host_of(acc["url"]), None)


def token(acc):
    """The account's token (main thread: may ask for the master password)."""
    if acc["id"] in _TOKENS:
        return _TOKENS[acc["id"]]
    am = QgsApplication.authManager()
    if am.isDisabled() or not acc.get("authcfg"):
        return ""
    cfg = QgsAuthMethodConfig()
    ok, cfg = _unpack(am.loadAuthenticationConfig(acc["authcfg"], cfg, True), cfg)
    value = cfg.config("password") if ok else ""
    if value:
        _TOKENS[acc["id"]] = value
    return value


def to_account(acc, with_token=True):
    return hosting.Account(acc["service"], acc["url"], token(acc) if with_token else "",
                           acc.get("email", ""), acc.get("username", ""))


def prepare(url):
    """Load the token for the server at url into memory (call on the main thread before git runs)."""
    host = hosting.host_of(url)
    for acc in list_accounts():
        if host and hosting.host_of(acc["url"]) == host and token(acc):
            _remember(to_account(acc))


def remember_for_session(account):
    """Use a token for this QGIS session without saving it."""
    _remember(account)


def credentials(url):
    """(git user name, token) for url from memory only — safe in background tasks."""
    return _CREDS.get(hosting.host_of(url))
