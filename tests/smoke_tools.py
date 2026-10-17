"""Headless check of the history tools: tag, report, restore, revert, load an old version.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_tools.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import QgsApplication, QgsProject  # noqa: E402
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from osgeo import ogr  # noqa: E402
from qgis.PyQt import QtGui, QtWidgets  # noqa: E402

from geogit import dock as dock_module  # noqa: E402
from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from test_vector_merge import gpkg, sq  # noqa: E402


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


def owners(path):
    ds = ogr.Open(path)
    out = sorted(f.GetField("owner") for f in ds.GetLayer(0))
    ds = None
    return out


# answer the dialogs the way a user would
Yes = QtWidgets.QMessageBox.StandardButton.Yes
dock_module.QMessageBox.question = staticmethod(lambda *a, **k: Yes)
report_path = os.path.join(tempfile.mkdtemp(), "report.html")
dock_module.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (report_path, "HTML (*.html)"))
opened = []
dock_module.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile()))
answers = iter(["ჩაბარება 2026", "პირველი ჩაბარება"])
dock_module.QInputDialog.getText = staticmethod(lambda *a, **k: (next(answers), True))

folder = os.path.join(tempfile.mkdtemp(), "project")
repo = gb.GitRepo(folder).init()
repo.set_identity("Nino", "nino@example.com")
p = os.path.join(folder, "parcels.gpkg")
gpkg(p, {1: ("a", 1.0, sq(0)), 2: ("b", 1.0, sq(2))})
repo.commit("v1")
gpkg(p, {1: ("ა", 1.0, sq(0)), 2: ("b", 1.0, sq(2)), 3: ("c", 1.0, sq(4))})
sha2 = repo.commit("v2: ნაკვეთი 3")

i18n.set_lang("ka")
dock = dock_module.GeoGitDock(FakeIface())
dock.set_folder(folder)
sha1 = dock._commits[1].sha

dock.tag_version(sha1)
print("tag row:", dock.history.topLevelItem(1).text(1))
assert "🏷 ჩაბარება-2026" in dock.history.topLevelItem(1).text(1)

dock.report_version(sha2)
spin(lambda: not dock._tasks)
html = open(report_path, encoding="utf-8").read()
assert "+1 დამატებული, ~1 შეცვლილი" in html and opened == [report_path]
print("report:", report_path, len(html), "bytes")

dock.load_version("parcels.gpkg", sha1)
spin(lambda: not dock._tasks)
[layer] = [lyr for lyr in QgsProject.instance().mapLayers().values() if sha1[:8] in lyr.name()]
print("loaded:", layer.name(), layer.featureCount(), "features")
assert layer.featureCount() == 2

dock.restore_version("parcels.gpkg", sha1)
assert owners(p) == ["a", "b"]
assert [c.path for c in repo.status()] == ["parcels.gpkg"]
repo.git("checkout", "--", "parcels.gpkg")
print("restore: ok (shown as an unsaved change)")

dock.revert_version(sha2, "v2")
spin(lambda: not dock._tasks)
assert owners(p) == ["a", "b"] and repo.log()[0].subject.startswith("Revert")
print("revert:", repo.log()[0].subject)

dock.cleanup()
print("TOOLS SMOKE OK")
app.exitQgis()
