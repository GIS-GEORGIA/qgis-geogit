"""Headless check of raster changes on the map and the swipe tool.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_raster.py [out_dir]
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

import numpy as np  # noqa: E402
from osgeo import gdal, osr  # noqa: E402

from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit.dock import GeoGitDock  # noqa: E402
from geogit.raster_layer import SwipeMapTool  # noqa: E402


class FakeIface:
    def __init__(self):
        self.bar = QgsMessageBar()
        self.canvas = QgsMapCanvas()
        self.canvas.resize(700, 420)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32638"))

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


def write_dem(path, arr):
    ds = gdal.GetDriverByName("GTiff").Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((480000, 5, 0, 4620000, 0, -5))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None


yy, xx = np.mgrid[0:240, 0:400]
v1 = (600 + 0.4 * xx + 25 * np.sin(yy / 18.0)).astype(np.float32)
v2 = v1.copy()
v2[60:110, 80:160] -= 12          # excavation
v2[150:200, 250:330] += 8         # embankment

folder = os.path.join(tempfile.mkdtemp(), "project")
repo = gb.GitRepo(folder).init()
repo.set_identity("Nino", "nino@example.com")
dem = os.path.join(folder, "rasters", "dem.tif")
os.makedirs(os.path.dirname(dem))
write_dem(dem, v1)
repo.commit("DEM 2025")
write_dem(dem, v2)
sha2 = repo.commit("DEM 2026: მიწის სამუშაოები")

i18n.set_lang("ka")
iface = FakeIface()
dock = GeoGitDock(iface)
dock.set_folder(folder)

dock.show_changes("rasters/dem.tif", sha2)
spin(lambda: not dock._tasks)
group = QgsProject.instance().layerTreeRoot().findGroup(f"GeoGit: rasters/dem.tif · {sha2[:8]}")
assert group, "raster group missing"
nodes = group.findLayers()
print("layers:", [(n.layer().name(), n.itemVisibilityChecked()) for n in nodes])
assert len(nodes) == 4
areas, diff_layer, new_layer, old_layer = [n.layer() for n in nodes]
assert areas.featureCount() == 2, areas.featureCount()
assert not nodes[3].itemVisibilityChecked()
msg_widget = iface.bar.currentItem()
print("message:", msg_widget.text())
assert "8 000" in msg_widget.text(), msg_widget.text()      # 50*80 + 50*80 pixels

# the same comparison again comes from the cache
cache = repo.cache_dir("versions")
print("cached versions:", sorted(os.listdir(cache)))

# unsaved edit vs last version
v3 = v2.copy()
v3[10:20, 10:20] = -9999
write_dem(dem, v3)
dock.show_changes("rasters/dem.tif", None)
spin(lambda: not dock._tasks)
print("working copy message:", iface.bar.currentItem().text())
assert "−100" in iface.bar.currentItem().text()

# swipe: old version left of the line
canvas = iface.canvas
canvas.setLayers([new_layer])
canvas.setExtent(new_layer.extent())
canvas.refresh()
spin(lambda: not canvas.isDrawing())
dock.start_swipe(old_layer)
assert isinstance(canvas.mapTool(), SwipeMapTool)
spin(lambda: dock._swipe.item is not None and dock._swipe.item.image is not None)
dock._swipe.item.x = 350
print("swipe image:", dock._swipe.item.image.size())

out_dir = sys.argv[1] if len(sys.argv) > 1 else None
if out_dir:
    settings = QgsMapSettings()
    settings.setLayers([areas, diff_layer, new_layer])
    settings.setDestinationCrs(new_layer.crs())
    settings.setExtent(new_layer.extent())
    settings.setOutputSize(QSize(700, 420))
    img = QImage(settings.outputSize(), QImage.Format.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    job = QgsMapRendererCustomPainterJob(settings, p)
    job.start()
    job.waitForFinished()
    p.end()
    img.save(os.path.join(out_dir, "raster_diff.png"))
    app.processEvents()
    canvas.grab().save(os.path.join(out_dir, "raster_swipe.png"))
    print("images in", out_dir)

dock.cleanup()
assert canvas.mapTool() is None
print("RASTER SMOKE OK")
app.exitQgis()
