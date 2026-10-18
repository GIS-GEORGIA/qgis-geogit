"""Create repositories on GitHub, GitLab, Bitbucket and Forgejo/Gitea through their APIs.

No QGIS imports. Each provider knows how to check a token, list the places a
repository can be created in (the user and their organisations, groups or
workspaces), create one, and which user name git should use with the token.
"""

import base64
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "GeoGit-QGIS-plugin"      # some hosts refuse the default Python user agent

GEORGIAN = dict(zip("აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
                    ["a", "b", "g", "d", "e", "v", "z", "t", "i", "k", "l", "m", "n", "o", "p", "zh", "r", "s",
                     "t", "u", "p", "k", "gh", "q", "sh", "ch", "ts", "dz", "ts", "ch", "kh", "j", "h"]))


class HostingError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


@dataclass
class Account:
    service: str               # github | gitlab | bitbucket | forgejo
    url: str                   # web address of the server, e.g. https://gitlab.com
    token: str
    email: str = ""            # Bitbucket only: REST calls use e-mail + API token
    username: str = ""         # filled in by check()


@dataclass
class Owner:
    id: str                    # what the API wants (login, namespace id, workspace slug)
    name: str                  # what the user sees
    is_user: bool = False


@dataclass
class Created:
    web_url: str
    clone_url: str


def repo_slug(text):
    """A repository name every service accepts: Georgian is transliterated, the rest made ASCII."""
    text = "".join(GEORGIAN.get(ch, ch) for ch in text.strip().lower())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-.")
    return re.sub(r"-{2,}", "-", text)[:100] or "qgis-project"


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


class Provider:
    key = ""
    label = ""
    default_url = ""
    needs_email = False
    scopes = ""

    def __init__(self, account):
        self.account = account
        self.web = (account.url or self.default_url).rstrip("/")

    # --- HTTP ------------------------------------------------------------
    def headers(self):
        return {}

    def request(self, method, path, body=None, query=None):
        url = self.api + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in self.headers().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise HostingError(self._error_text(e), e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise HostingError(f"cannot reach {self.web}: {getattr(e, 'reason', e)}") from None
        return json.loads(raw) if raw else None

    @staticmethod
    def _error_text(e):
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        msg = data.get("message") or data.get("error") or ""
        if isinstance(msg, dict):                         # GitLab: {"name": ["has already been taken"]}
            msg = "; ".join(f"{k} {', '.join(v) if isinstance(v, list) else v}" for k, v in msg.items())
        if isinstance(data.get("error"), dict):           # Bitbucket: {"error": {"message": ...}}
            msg = data["error"].get("message", "")
        for err in data.get("errors") or []:              # GitHub: {"errors": [{"message": ...}]}
            if isinstance(err, dict) and err.get("message"):
                msg += f" ({err['message']})"
        return f"HTTP {e.code}: {msg or e.reason}"

    # --- what every provider implements ----------------------------------
    def check(self):
        """Who the token belongs to (sets account.username)."""
        raise NotImplementedError

    def owners(self):
        raise NotImplementedError

    def create(self, owner, name, description="", private=True):
        raise NotImplementedError

    def git_username(self):
        return self.account.username

    def token_page(self):
        return self.web


class GitHub(Provider):
    key, label, default_url = "github", "GitHub", "https://github.com"
    scopes = "classic: repo · fine-grained: Administration + Contents (read and write)"

    @property
    def api(self):
        return "https://api.github.com" if host_of(self.web) == "github.com" else self.web + "/api/v3"

    def headers(self):
        return {"Authorization": f"Bearer {self.account.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def check(self):
        self.account.username = self.request("GET", "/user")["login"]
        return self.account.username

    def owners(self):
        orgs = self.request("GET", "/user/orgs", query={"per_page": 100}) or []
        return [Owner(self.account.username, self.account.username, True)] + \
               [Owner(o["login"], o["login"]) for o in orgs]

    def create(self, owner, name, description="", private=True):
        path = "/user/repos" if owner.is_user else f"/orgs/{owner.id}/repos"
        r = self.request("POST", path, {"name": repo_slug(name), "description": description, "private": private})
        return Created(r["html_url"], r["clone_url"])

    def token_page(self):
        return self.web + "/settings/tokens/new?scopes=repo&description=GeoGit"


class GitLab(Provider):
    key, label, default_url = "gitlab", "GitLab", "https://gitlab.com"
    scopes = "api (write_repository)"

    @property
    def api(self):
        return self.web + "/api/v4"

    def headers(self):
        return {"PRIVATE-TOKEN": self.account.token}

    def check(self):
        self.account.username = self.request("GET", "/user")["username"]
        return self.account.username

    def owners(self):
        spaces = self.request("GET", "/namespaces", query={"per_page": 100}) or []
        out = []
        for ns in spaces:
            is_user = ns.get("kind") == "user"
            out.append(Owner(str(ns["id"]), ns.get("full_path") or ns.get("path"), is_user))
        out.sort(key=lambda o: not o.is_user)
        return out

    def create(self, owner, name, description="", private=True):
        body = {"name": name, "path": repo_slug(name), "description": description,
                "visibility": "private" if private else "public"}
        if not owner.is_user:
            body["namespace_id"] = int(owner.id)
        r = self.request("POST", "/projects", body)
        return Created(r["web_url"], r["http_url_to_repo"])

    def token_page(self):
        return self.web + "/-/user_settings/personal_access_tokens?name=GeoGit&scopes=api,write_repository"


class Bitbucket(Provider):
    key, label, default_url = "bitbucket", "Bitbucket", "https://bitbucket.org"
    needs_email = True
    scopes = "read:user, read:workspace, admin:repository, write:repository"

    @property
    def api(self):
        return "https://api.bitbucket.org/2.0"

    def headers(self):
        basic = base64.b64encode(f"{self.account.email}:{self.account.token}".encode()).decode()
        return {"Authorization": f"Basic {basic}"}

    def check(self):
        me = self.request("GET", "/user")
        self.account.username = me.get("username") or me.get("nickname") or self.account.email
        return self.account.username

    def owners(self):
        data = self.request("GET", "/user/permissions/workspaces", query={"pagelen": 100}) or {}
        slugs = [v["workspace"]["slug"] for v in data.get("values", []) if v.get("workspace")]
        return [Owner(s, s, i == 0) for i, s in enumerate(slugs)]

    def create(self, owner, name, description="", private=True):
        slug = repo_slug(name)
        r = self.request("POST", f"/repositories/{owner.id}/{slug}",
                         {"scm": "git", "is_private": private, "description": description})
        https = next((c["href"] for c in r["links"]["clone"] if c.get("name") == "https"), "")
        parts = urllib.parse.urlsplit(https)      # drop the "user@" the API puts in the address
        clone = urllib.parse.urlunsplit(parts._replace(netloc=parts.hostname))
        return Created(r["links"]["html"]["href"], clone)

    def git_username(self):
        return "x-bitbucket-api-token-auth"   # API tokens use this fixed name for git

    def token_page(self):
        return "https://id.atlassian.com/manage-profile/security/api-tokens"


class Forgejo(Provider):
    key, label, default_url = "forgejo", "Forgejo / Gitea", ""
    scopes = "write:repository, read:user, read:organization"

    @property
    def api(self):
        return self.web + "/api/v1"

    def headers(self):
        return {"Authorization": f"token {self.account.token}"}

    def check(self):
        self.account.username = self.request("GET", "/user")["login"]
        return self.account.username

    def owners(self):
        orgs = self.request("GET", "/user/orgs", query={"limit": 50}) or []
        return [Owner(self.account.username, self.account.username, True)] + \
               [Owner(o["username"], o.get("full_name") or o["username"]) for o in orgs]

    def create(self, owner, name, description="", private=True):
        path = "/user/repos" if owner.is_user else f"/orgs/{owner.id}/repos"
        r = self.request("POST", path, {"name": repo_slug(name), "description": description, "private": private})
        return Created(r["html_url"], r["clone_url"])

    def token_page(self):
        return self.web + "/user/settings/applications"


PROVIDERS = {p.key: p for p in (GitHub, GitLab, Bitbucket, Forgejo)}


def provider(account):
    return PROVIDERS[account.service](account)
