"""Headless smoke test of the GeoGit panel inside PyQGIS.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_qgis.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# standalone PyQGIS keeps its profile and settings under XDG_DATA_HOME; keep them out of $HOME
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import QgsApplication, QgsProject  # noqa: E402
from qgis.gui import QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit.dock import GeoGitDock  # noqa: E402
from geogit.history_view import GRAPH_ROLE  # noqa: E402
from qgis.PyQt.QtCore import Qt  # noqa: E402


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()

    def messageBar(self):
        return self.bar

    def addProject(self, path):
        return QgsProject.instance().read(path)


def wait_tasks(dock, timeout=30):
    end = time.time() + timeout
    while dock._tasks and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert not dock._tasks, "task did not finish"


tmp = tempfile.mkdtemp()
remote = os.path.join(tmp, "server.git")
gb.run_git(["init", "--bare", "-b", "main", remote], tmp)
folder = os.path.join(tmp, "project")
os.makedirs(folder)
with open(os.path.join(folder, "roads.geojson"), "w") as f:
    f.write('{"type":"FeatureCollection","features":[]}')

i18n.set_lang("ka")
dock = GeoGitDock(FakeIface())
dock.set_folder(folder)
assert dock.stack.currentIndex() == 0, "folder should not be a repo yet"

gb.GitRepo(folder).init()
dock.set_folder(folder)
assert dock.stack.currentIndex() == 1
dock.repo.set_identity("Test User", "test@example.com")
dock.repo.set_remote(remote)
dock.refresh()
paths = [dock.changes.topLevelItem(i).text(2) for i in range(dock.changes.topLevelItemCount())]
print("changes:", paths)
assert "roads.geojson" in paths and ".gitignore" in paths

# untick .gitignore, commit only the layer
for i in range(dock.changes.topLevelItemCount()):
    it = dock.changes.topLevelItem(i)
    if it.text(2) == ".gitignore":
        it.setCheckState(0, Qt.CheckState.Unchecked)
dock.message.setPlainText("გზების ფენა")
dock._commit()
wait_tasks(dock)
left = [dock.changes.topLevelItem(i).text(2) for i in range(dock.changes.topLevelItemCount())]
print("left after commit:", left)
assert left == [".gitignore"], left
assert dock.history.topLevelItemCount() == 1
print("history row:", [dock.history.topLevelItem(0).text(c) for c in range(5)])

dock._push()
wait_tasks(dock)
assert dock.repo.branch_info().upstream == "origin/main"
print("ahead/behind label:", dock.ab_label.text())

dock.history.setCurrentItem(dock.history.topLevelItem(0))
print("files in commit:", dock.commit_files.topLevelItem(0).text(1))

# a second author works on a branch while the first keeps going on main, then merge
repo = dock.repo
repo.create_branch("survey")
repo.set_identity("Giorgi", "giorgi@example.com")
for n in range(2):
    with open(os.path.join(folder, "dem.tif"), "w") as f:
        f.write(f"raster {n}")
    repo.commit(f"DEM განახლება {n}")
repo.switch("main")
repo.set_identity("Test User", "test@example.com")
with open(os.path.join(folder, "roads.geojson"), "w") as f:
    f.write('{"type":"FeatureCollection","features":[1]}')
repo.commit("ახალი გზა")
repo.git("merge", "--no-ff", "-m", "Merge survey", "survey")
dock.refresh()
n_rows = dock.history.topLevelItemCount()
print("history rows:", n_rows, "| authors:", [dock.author_combo.itemText(i) for i in range(dock.author_combo.count())])
assert n_rows == 5
assert dock.history.topLevelItem(0).data(0, GRAPH_ROLE)[0].is_merge

dock.author_combo.setCurrentIndex(dock.author_combo.findData("giorgi@example.com"))
dimmed = [dock.history.topLevelItem(i).data(0, GRAPH_ROLE)[2] for i in range(n_rows)]
print("dimmed for Giorgi:", dimmed)
assert dimmed.count(False) == 2
dock.chk_hide.setChecked(True)
assert sum(not dock.history.topLevelItem(i).isHidden() for i in range(n_rows)) == 2
dock.chk_hide.setChecked(False)
dock.author_combo.setCurrentIndex(0)
dock.kind_combo.setCurrentIndex(dock.kind_combo.findData("raster"))
assert [dock.history.topLevelItem(i).data(0, GRAPH_ROLE)[2] for i in range(n_rows)].count(False) == 2
dock.kind_combo.setCurrentIndex(0)

dock.show_file_history("roads.geojson")
print("roads.geojson history:", [dock.history.topLevelItem(i).text(1) for i in range(dock.history.topLevelItemCount())])
assert dock.history.topLevelItemCount() == 2
dock.show_file_history(None)

dock.tabs.setCurrentIndex(1)
dock.resize(520, 640)
dock.show()
app.processEvents()
shot = os.environ.get("GEOGIT_SHOT")
if shot:
    dock.grab().save(shot)
    print("screenshot:", shot)

dock.lang_combo.setCurrentIndex(1)
print("english title:", dock.windowTitle(), "| commit button:", dock.btn_commit.text())

dock.cleanup()
print("SMOKE OK")
app.exitQgis()
