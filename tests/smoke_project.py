"""Headless check of the project (.qgz) diff dialog and lock markers.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_project.py [out_dir]
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsPrintLayout, QgsProject,  # noqa: E402
                       QgsVectorLayer)
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtGui import QColor  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit.dock import GeoGitDock  # noqa: E402
from geogit.project_dialog import ProjectDiffDialog  # noqa: E402


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()
        self.canvas = QgsMapCanvas()

    def messageBar(self):
        return self.bar

    def mapCanvas(self):
        return self.canvas


def spin(cond, timeout=30):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert cond(), "timed out"


folder = os.path.join(tempfile.mkdtemp(), "project")
repo = gb.GitRepo(folder).init()
repo.set_identity("Nino", "nino@example.com")
for name in ("roads", "parcels"):
    with open(os.path.join(folder, f"{name}.geojson"), "w") as f:
        f.write('{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
                '"geometry":{"type":"Point","coordinates":[44.8,41.7]}}]}')

prj = QgsProject.instance()
prj.setCrs(QgsCoordinateReferenceSystem("EPSG:32638"))
roads = QgsVectorLayer(os.path.join(folder, "roads.geojson"), "გზები", "ogr")
parcels = QgsVectorLayer(os.path.join(folder, "parcels.geojson"), "ნაკვეთები", "ogr")
prj.addMapLayers([roads, parcels])
qgz = os.path.join(folder, "project.qgz")
assert prj.write(qgz)
repo.commit("პროექტი v1")

# version 2: new style and name for roads, parcels hidden, a new group, a layout
roads.renderer().symbol().setColor(QColor("#ff00ff"))
roads.setName("გზები 2026")
prj.layerTreeRoot().findLayer(parcels.id()).setItemVisibilityChecked(False)
prj.layerTreeRoot().addGroup("საბაზისო")
layout = QgsPrintLayout(prj)
layout.initializeDefaults()
layout.setName("A4 რუკა")
prj.layoutManager().addLayout(layout)
assert prj.write(qgz)
sha2 = repo.commit("პროექტი v2")

i18n.set_lang("ka")
iface = FakeIface()
dock = GeoGitDock(iface)
dock.set_folder(folder)
dock.show_changes("project.qgz", sha2)
spin(lambda: not dock._tasks)
[dlg] = [d for d in dock._dialogs if isinstance(d, ProjectDiffDialog)]
rows = []
for i in range(dlg.tree.topLevelItemCount()):
    top = dlg.tree.topLevelItem(i)
    for j in range(top.childCount()):
        c = top.child(j)
        rows.append((top.text(0), c.text(0), c.text(1), c.text(2)))
for r in rows:
    print(" ", r)
texts = " | ".join(" ".join(r) for r in rows)
for expected in ("სახელი: გზები → გზები 2026", "სტილი შეიცვალა", "გამოირთო (დამალული)", "საბაზისო", "A4 რუკა"):
    assert expected in texts, expected

# no real change: saving again only touches save time
assert prj.write(qgz)
sha3 = repo.commit("შენახვა")
dock.show_changes("project.qgz", sha3)
spin(lambda: not dock._tasks)
print("unchanged:", iface.bar.currentItem().text())
assert "ცვლილება ვერ ვიპოვე" in iface.bar.currentItem().text()

# lock markers on the changes list (locks as the server would report them)
with open(os.path.join(folder, "roads.geojson"), "a") as f:
    f.write(" ")
dock._lfs = True
dock._show_locks([gb.Lock("1", "roads.geojson", "giorgi", "2026-10-17T10:00:00Z", False),
                  gb.Lock("2", "parcels.geojson", "nino", "2026-10-17T11:00:00Z", True)])
item = dock.changes.topLevelItem(0)
print("changes row:", item.text(2), "| tooltip:", item.toolTip(2))
assert "giorgi" in item.toolTip(2)
print("locks tab:", dock.tabs.tabText(2), "|", dock.locks_status.text())
assert dock.locks_tree.topLevelItemCount() == 2

out_dir = sys.argv[1] if len(sys.argv) > 1 else None
if out_dir:
    dlg.resize(640, 420)
    dlg.show()
    app.processEvents()
    dlg.grab().save(os.path.join(out_dir, "project_diff.png"))
    dock.tabs.setCurrentIndex(2)
    dock.resize(520, 420)
    dock.show()
    app.processEvents()
    dock.grab().save(os.path.join(out_dir, "locks.png"))
    print("images in", out_dir)

dock.cleanup()
print("PROJECT SMOKE OK")
app.exitQgis()
