"""Headless check of "Create on server" against the local Forgejo (server/), through the real dialog.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_create_repo.py [out_dir]

Skipped when the local server is not running.
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://localhost:3000"


def api(method, path, token):
    req = urllib.request.Request(URL + "/api/v1" + path, method=method)
    req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")
    except Exception:
        return None


try:
    with open(os.path.join(HERE, "server", ".env.local"), encoding="utf-8") as f:
        TOKEN = dict(line.strip().split("=", 1) for line in f if "=" in line)["NINO_TOKEN"]
    if not api("GET", "/user", TOKEN):          # the server needs sign-in even for /version
        raise RuntimeError("server not reachable or token refused")
except Exception as e:
    print("skipped: local Forgejo not available:", e)
    print("CREATE REPO SMOKE OK")
    sys.exit(0)

from qgis.core import QgsApplication  # noqa: E402
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402

if os.path.exists("/.flatpak-info"):
    QgsApplication.setPrefixPath("/app", True)     # a standalone Python in the Flatpak needs the QGIS prefix
app = QgsApplication([], True)
app.initQgis()
assert QgsApplication.authManager().setMasterPassword("test-master", True), \
    QgsApplication.authManager().disabledMessage()

from geogit import accounts  # noqa: E402
from geogit import dock as dm  # noqa: E402
from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit import server_dialog as sd  # noqa: E402


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()
        self.canvas = QgsMapCanvas()

    def messageBar(self):
        return self.bar

    def mapCanvas(self):
        return self.canvas


def spin(cond, timeout=60):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert cond(), "timed out"


def repo_info(name):
    """The server marks a repository non-empty a moment after the first push, so wait for it."""
    info = None
    for _ in range(40):
        info = api("GET", f"/repos/nino/{name}", TOKEN)
        if info and not info["empty"]:
            break
        time.sleep(0.5)
    return info


SHOT = sys.argv[1] if len(sys.argv) > 1 else None
plan = []        # what the "user" types into each dialog that opens


class ScriptedDialog(sd.CreateRepoDialog):
    def exec(self):
        token, name = plan.pop(0)
        self.service.setCurrentIndex(self.service.findData("forgejo"))
        self.url.setText(URL)
        if token:
            self.token.setText(token)
        assert self.check(), self.status.text()
        print("  dialog:", self.status.text(), "| owners:", [self.owner_box.itemText(i) for i in range(self.owner_box.count())])
        self.name.setText(name)
        if SHOT:
            self.show()
            app.processEvents()
            self.grab().save(os.path.join(SHOT, "create_repo.png"))
        self.create()
        return 1


sd.CreateRepoDialog = ScriptedDialog
dm.QMessageBox.warning = staticmethod(lambda *a: print("  warning:", a[-1]))     # no one to click OK here
i18n.set_lang("ka")
for name in ("geogit-ui-test", "geogit-ui-test-2"):
    api("DELETE", f"/repos/nino/{name}", TOKEN)

root = tempfile.mkdtemp(prefix="geogit-create-")
repo = gb.GitRepo(os.path.join(root, "ჩემი პროექტი")).init()
repo.set_identity("Nino", "nino@example.com")
with open(os.path.join(repo.path, "notes.txt"), "w") as f:
    f.write("ვერსია 1\n")
repo.commit("პირველი ვერსია")

dock = dm.GeoGitDock(FakeIface())
dock.set_folder(repo.path)
assert dock.btn_create_remote.isVisibleTo(dock)

# 1. create with a pasted token: repository made, remote set, first version published, token saved
plan.append((TOKEN, "geogit-ui-test"))
dock.create_on_server()
spin(lambda: not dock._tasks)
info = repo_info("geogit-ui-test")
assert info and not info["empty"] and info["private"], info
print("1. created and published:", info["html_url"], "| remote:", repo.remote_url())
print("   message:", dock.iface.bar.currentItem().text()[:80])
assert TOKEN not in open(os.path.join(repo.path, ".git", "config")).read()

saved = accounts.list_accounts()
assert len(saved) == 1 and saved[0]["service"] == "forgejo" and saved[0]["username"] == "nino", saved
accounts._TOKENS.clear()
accounts._CREDS.clear()
assert accounts.token(saved[0]) == TOKEN          # read back from the encrypted auth database
print("2. token saved in the QGIS auth database and read back")

# 3. second repository: the token field stays empty, the saved token is used
repo2 = gb.GitRepo(os.path.join(root, "second")).init()
repo2.set_identity("Nino", "nino@example.com")
with open(os.path.join(repo2.path, "a.txt"), "w") as f:
    f.write("a\n")
repo2.commit("a")
accounts._TOKENS.clear()
accounts._CREDS.clear()
dock.set_folder(repo2.path)
plan.append(("", "geogit-ui-test-2"))
dock.create_on_server()
spin(lambda: not dock._tasks)
assert not repo_info("geogit-ui-test-2")["empty"]
print("3. second repository created and published with the saved token")

# 4. publishing later works from the panel with the saved token (askpass, no helper)
with open(os.path.join(repo2.path, "a.txt"), "a") as f:
    f.write("b\n")
repo2.commit("b")
accounts._TOKENS.clear()
accounts._CREDS.clear()
dock._push()
spin(lambda: not dock._tasks)
assert repo2.branch_info().ahead == 0
print("4. later Publish used the saved token")

for name in ("geogit-ui-test", "geogit-ui-test-2"):
    api("DELETE", f"/repos/nino/{name}", TOKEN)
dock.cleanup()
print("CREATE REPO SMOKE OK")
app.exitQgis()
