"""Unit tests for raster_diff.py (needs GDAL Python bindings and numpy).

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import git_backend as gb  # noqa: E402
import raster_diff as rd  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

X0, Y0, PX = 480000.0, 4620000.0, 10.0


def tif(path, arrays, nodata=None, px=PX, dtype=gdal.GDT_Float32, epsg=32638):
    arrays = arrays if isinstance(arrays, list) else [arrays]
    h, w = arrays[0].shape
    ds = gdal.GetDriverByName("GTiff").Create(path, w, h, len(arrays), dtype)
    ds.SetGeoTransform((X0, px, 0, Y0, 0, -px))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    for i, a in enumerate(arrays):
        b = ds.GetRasterBand(i + 1)
        if nodata is not None:
            b.SetNoDataValue(nodata)
        b.WriteArray(a)
    ds = None


class RasterDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, name):
        return os.path.join(self.d, name)

    def test_dem_patch_and_nodata(self):
        old = np.full((300, 400), 500.0, dtype=np.float32)
        old[:, :10] = -9999                       # no data strip on the left
        new = old.copy()
        new[100:120, 200:250] += 5                # 20 x 50 pixels raised by 5 m
        new[0:10, 0:10] = 480                     # 10 x 10 gained data
        tif(self.p("old.tif"), old, nodata=-9999)
        tif(self.p("new.tif"), new, nodata=-9999)
        r = rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("out"))
        self.assertTrue(r.compared)
        self.assertEqual(r.meta_changes, [])
        self.assertEqual(r.changed_pixels, 20 * 50 + 100)
        self.assertEqual((r.gained_pixels, r.lost_pixels), (100, 0))
        self.assertAlmostEqual(r.max_diff, 5.0)
        self.assertAlmostEqual(r.mean_abs_diff, 5.0 * 1000 / 1100, places=4)
        self.assertEqual(r.bbox, (X0, Y0 - 120 * PX, X0 + 250 * PX, Y0))
        d = gdal.Open(r.diff_path).ReadAsArray()
        self.assertAlmostEqual(float(d[110, 210]), 5.0)
        self.assertEqual(float(d[200, 300]), 0.0)
        ds = ogr.Open(r.mask_path)
        self.assertGreaterEqual(ds.GetLayer(0).GetFeatureCount(), 2)

    def test_tolerance(self):
        old = np.zeros((50, 50), dtype=np.float32)
        new = old + 0.01
        tif(self.p("old.tif"), old)
        tif(self.p("new.tif"), new)
        self.assertEqual(rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("o1")).changed_pixels, 2500)
        r = rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("o2"), tolerance=0.05)
        self.assertFalse(r.changed)
        self.assertEqual(r.diff_path, "")

    def test_rgb_photo(self):
        bands = [np.full((200, 200), v, dtype=np.uint8) for v in (90, 120, 60)]
        new = [b.copy() for b in bands]
        new[1][50:60, 50:70] = 200                # a new roof shows up in green
        tif(self.p("old.tif"), bands, dtype=gdal.GDT_Byte)
        tif(self.p("new.tif"), new, dtype=gdal.GDT_Byte)
        r = rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("out"))
        self.assertEqual(r.changed_pixels, 200)
        self.assertAlmostEqual(r.max_diff, 80.0)   # largest band change, not new - old of band 1

    def test_resampled_grid_is_warped(self):
        old = np.zeros((100, 100), dtype=np.float32)
        new = np.zeros((50, 50), dtype=np.float32)
        new[10:15, 10:15] = 7                     # 50x50 m block at 20 m pixels
        tif(self.p("old.tif"), old)
        tif(self.p("new.tif"), new, px=20.0)
        r = rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("out"))
        self.assertIn("size", r.meta_changes)
        self.assertIn("grid", r.meta_changes)
        self.assertTrue(r.compared)
        self.assertEqual(r.changed_pixels, 10 * 10)

    def test_band_count_change_is_metadata_only(self):
        tif(self.p("old.tif"), np.zeros((10, 10), dtype=np.float32))
        tif(self.p("new.tif"), [np.zeros((10, 10), dtype=np.float32)] * 2)
        r = rd.compare(self.p("old.tif"), self.p("new.tif"), self.p("out"))
        self.assertFalse(r.compared)
        self.assertIn("bands", r.meta_changes)
        self.assertTrue(r.changed)

    def test_members(self):
        self.assertEqual(gb.raster_main_file("a/DEM.TIF"), "a/DEM.TIF")
        self.assertIsNone(gb.raster_main_file("roads.gpkg"))
        self.assertIn("a/ortho.tfw", gb.dataset_members("a/ortho.tif"))


if __name__ == "__main__":
    unittest.main()
