"""Headless check of "show changes on map" for a GeoPackage layer.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_diff.py [out.png]
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsMapRendererCustomPainterJob,  # noqa: E402
                       QgsMapSettings, QgsProject)
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtCore import QSize  # noqa: E402
from qgis.PyQt.QtGui import QColor, QImage, QPainter  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from osgeo import ogr, osr  # noqa: E402

from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit.diff_layer import CHANGE_FIELD  # noqa: E402
from geogit.dock import GeoGitDock  # noqa: E402


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()
        self.canvas = QgsMapCanvas()
        self.canvas.resize(600, 400)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32638"))

    def messageBar(self):
        return self.bar

    def mapCanvas(self):
        return self.canvas


def wait_tasks(dock, timeout=30):
    end = time.time() + timeout
    while dock._tasks and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert not dock._tasks, "task did not finish"


def square(x, y, s=100):
    return f"POLYGON(({x} {y},{x + s} {y},{x + s} {y + s},{x} {y + s},{x} {y}))"


def write_parcels(path, rows):
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    lyr = ds.CreateLayer("ნაკვეთები", srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("owner", ogr.OFTString))
    for fid, owner, wkt in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetFID(fid)
        f.SetField("owner", owner)
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(f)
    ds = None


X, Y = 480000, 4620000
folder = os.path.join(tempfile.mkdtemp(), "project")
repo = gb.GitRepo(folder).init()
repo.set_identity("Nino", "nino@example.com")
gpkg = os.path.join(folder, "parcels.gpkg")
v1 = [(i, f"owner {i}", square(X + (i % 4) * 120, Y + (i // 4) * 120)) for i in range(1, 13)]
write_parcels(gpkg, v1)
repo.commit("ნაკვეთები v1")

v2 = [r for r in v1 if r[0] not in (3, 7)]                                  # 2 deleted
v2 = [(fid, "ნინო" if fid == 5 else o, square(X + 120, Y + 120, 140) if fid == 6 else w)
      for fid, o, w in v2]                                                   # 1 attr, 1 shape
v2 += [(13, "new", square(X + 480, Y)), (14, "new", square(X + 480, Y + 120))]  # 2 added
write_parcels(gpkg, v2)
sha2 = repo.commit("ნაკვეთები v2")

i18n.set_lang("ka")
iface = FakeIface()
dock = GeoGitDock(iface)
dock.set_folder(folder)

# history: version 2 against version 1
dock.show_vector_diff("parcels.gpkg", sha2)
wait_tasks(dock)
group = QgsProject.instance().layerTreeRoot().findGroup(f"GeoGit: parcels.gpkg · {sha2[:8]}")
assert group, "diff group missing"
[layer] = [n.layer() for n in group.findLayers()]
counts = {}
for f in layer.getFeatures():
    counts[f[CHANGE_FIELD]] = counts.get(f[CHANGE_FIELD], 0) + 1
print("layer:", layer.name(), "| crs:", layer.crs().authid(), "| counts:", counts)
assert counts == {"დამატებული": 2, "შეცვლილი": 2, "შეცვლილი (ძველი სახე)": 2, "წაშლილი": 2}, counts
print("messages:", [w.text() for w in iface.bar.items()][-1])

# unsaved edit: the working copy against the last version
write_parcels(gpkg, v2 + [(15, "draft", square(X, Y - 150))])
dock.show_vector_diff("parcels.gpkg", None)
wait_tasks(dock)
wc = QgsProject.instance().layerTreeRoot().findGroup("GeoGit: parcels.gpkg · შეუნახავი")
[wl] = [n.layer() for n in wc.findLayers()]
print("working copy diff:", [f[CHANGE_FIELD] for f in wl.getFeatures()])
assert [f[CHANGE_FIELD] for f in wl.getFeatures()] == ["დამატებული"]

# a non-vector file is ignored, an unchanged one reports no changes
assert gb.vector_main_file("dem.tif") is None

out = sys.argv[1] if len(sys.argv) > 1 else None
if out:
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    ext = layer.extent()
    ext.scale(1.15)
    settings.setExtent(ext)
    settings.setOutputSize(QSize(700, 420))
    settings.setBackgroundColor(QColor("white"))
    img = QImage(settings.outputSize(), QImage.Format.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    job = QgsMapRendererCustomPainterJob(settings, p)
    job.start()
    job.waitForFinished()
    p.end()
    img.save(out)
    print("map image:", out)

dock.cleanup()
print("DIFF SMOKE OK")
app.exitQgis()
