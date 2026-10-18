"""Headless check of the git-lfs install offer: bar, consent dialog, install, then LFS switched on.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_lfs_install.py

Needs git without git-lfs on PATH; "installing" puts a real git-lfs (GEOGIT_TEST_LFS,
default: the one bundled with Kart) on PATH instead of calling the package manager.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import QgsApplication  # noqa: E402
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from qgis.PyQt.QtWidgets import QApplication, QMessageBox  # noqa: E402

from geogit import dock as dm  # noqa: E402
from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402

LFS = os.environ.get("GEOGIT_TEST_LFS", "/opt/kart/_internal/git-lfs")
if gb.git_mode() == "host":
    # in a Flatpak, git runs on the computer and does not see this sandbox's PATH, so the
    # stand-in "install" below cannot work; a real install puts git-lfs in the host /usr/bin
    print("skipped: Flatpak (git runs on the host)")
    print("LFS INSTALL SMOKE OK")
    sys.exit(0)
if gb.GitRepo(".").lfs_available() or not os.path.exists(LFS):
    print("skipped: needs git-lfs absent from PATH and a binary to install from", LFS)
    print("LFS INSTALL SMOKE OK")
    sys.exit(0)


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()
        self.canvas = QgsMapCanvas()

    def messageBar(self):
        return self.bar

    def mapCanvas(self):
        return self.canvas


class Box(QMessageBox):
    """Clicks the button with the role in Box.answer instead of waiting for a user."""
    answer = QMessageBox.ButtonRole.AcceptRole
    shown = []

    def exec(self):
        Box.shown.append(self.text())
        self._picked = next((b for b in self.buttons() if self.buttonRole(b) == Box.answer), None)
        return 0

    def clickedButton(self):
        return self._picked


warnings = []
dm.QMessageBox = Box
Box.warning = staticmethod(lambda parent, title, text: warnings.append(text))

installed = []


def fake_install(plan):
    """What pkexec apt-get would leave behind: git-lfs on PATH."""
    installed.append(plan["argv"])
    bindir = tempfile.mkdtemp(prefix="geogit-lfsbin-")
    os.symlink(LFS, os.path.join(bindir, "git-lfs"))
    os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
    return "ok"


def spin(cond, timeout=60):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert cond(), "timed out"


i18n.set_lang("ka")
plan = {"command": "sudo apt install git-lfs", "argv": ["pkexec", "apt-get", "install", "-y", "git-lfs"],
        "needs_password": True}
gb.lfs_install_plan = lambda: plan

# 1. failure: the install is refused -> warning with the command, and it is on the clipboard
gb.install_lfs = lambda p: (_ for _ in ()).throw(gb.GitError("cancelled or not authorised"))
folder = os.path.join(tempfile.mkdtemp(), "project")
os.makedirs(folder)
open(os.path.join(folder, "dem.tif"), "wb").write(b"\0raster")
dock = dm.GeoGitDock(FakeIface())
dock.set_folder(folder)
dock._init_repo()
spin(lambda: not dock._tasks)
print("refused install ->", warnings[-1].splitlines()[0][:60], "| clipboard:", QApplication.clipboard().text())
assert QApplication.clipboard().text() == plan["command"] and not gb.GitRepo(folder).is_repo()

# 2. "continue without": a repository without LFS, and the bar offers the install
Box.answer = QMessageBox.ButtonRole.DestructiveRole
dock._init_repo()
assert dock.repo is not None and not dock._lfs and dock.lfs_bar.isVisibleTo(dock)
print("without LFS -> repo created, bar shown:", dock.lfs_label.text()[:50])

# 3. install from the bar -> LFS switched on for this repository
gb.install_lfs = fake_install
Box.answer = QMessageBox.ButtonRole.AcceptRole
dock.btn_lfs_install.click()
spin(lambda: not dock._tasks)
with open(os.path.join(folder, ".gitattributes"), encoding="utf-8") as f:
    rules = f.read()
assert installed and "*.tif filter=lfs" in rules and dock._lfs and not dock.lfs_bar.isVisibleTo(dock)
assert dock.repo.git("config", "lfs.locksverify").strip() == "true"
print("installed ->", installed[0][:2], "| .gitattributes has LFS rules, bar hidden, locksverify on")
dock.repo.set_identity("T", "t@example.com")
dock.repo.commit("v1")
assert dock.repo.git("cat-file", "-p", "HEAD:dem.tif").startswith("version https://git-lfs")
print("dem.tif committed as an LFS pointer")

# 4. new folder: install offered before the first version, then the repository starts with LFS
folder2 = os.path.join(tempfile.mkdtemp(), "p2")
os.makedirs(folder2)
os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if "geogit-lfsbin" not in p)
dock.set_folder(folder2)
dock._init_repo()
spin(lambda: not dock._tasks)
assert gb.GitRepo(folder2).is_repo() and "filter=lfs" in open(os.path.join(folder2, ".gitattributes")).read()
print("init offer -> installed first, repository created with LFS")
print("dialog text:", Box.shown[0].splitlines()[0][:70])

dock.cleanup()
print("LFS INSTALL SMOKE OK")
app.exitQgis()
