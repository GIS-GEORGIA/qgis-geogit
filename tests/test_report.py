"""Unit tests for report.py (needs GDAL Python bindings).

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import git_backend as gb  # noqa: E402
import i18n  # noqa: E402
import report  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

from test_vector_merge import gpkg, sq  # noqa: E402


def dem(path, arr):
    ds = gdal.GetDriverByName("GTiff").Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((480000, 10, 0, 4620000, 0, -10))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None


class ReportTest(unittest.TestCase):
    def test_report(self):
        i18n.set_lang("ka")
        with tempfile.TemporaryDirectory() as d:
            repo = gb.GitRepo(os.path.join(d, "p")).init(use_lfs=False)
            repo.set_identity("Nino", "nino@example.com")
            p = os.path.join(repo.path, "parcels.gpkg")
            gpkg(p, {1: ("a", 1.0, sq(0)), 2: ("b", 1.0, sq(2))})
            dem(os.path.join(repo.path, "dem.tif"), np.zeros((20, 20), dtype=np.float32))
            shp = os.path.join(repo.path, "roads.shp")
            ds = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(shp)
            ds.CreateLayer("roads", None, ogr.wkbPoint)
            ds = None
            repo.commit("v1")
            gpkg(p, {1: ("ა", 1.0, sq(0)), 3: ("c", 1.0, sq(4))})
            arr = np.zeros((20, 20), dtype=np.float32)
            arr[:5, :4] = 3
            dem(os.path.join(repo.path, "dem.tif"), arr)
            ds = ogr.Open(shp, 1)
            f = ogr.Feature(ds.GetLayer(0).GetLayerDefn())
            f.SetGeometry(ogr.CreateGeometryFromWkt("POINT(1 1)"))
            ds.GetLayer(0).CreateFeature(f)
            ds = None
            with open(os.path.join(repo.path, "notes.txt"), "w") as fh:
                fh.write("<b>x</b>")
            sha = repo.commit("ნინო: ნაკვეთები & DEM")
            repo.add_tag("v1.0", sha, "delivery")

            out = report.build_report(repo, sha)
            self.assertIn("ნინო: ნაკვეთები &amp; DEM", out)
            self.assertIn("+1 დამატებული, ~1 შეცვლილი, −1 წაშლილი ობიექტი", out)
            self.assertIn("20 პიქსელი (5.00%)", out)
            self.assertIn("roads.shp", out)
            self.assertNotIn("roads.dbf", out)                 # sidecars fold into the .shp row
            self.assertIn("v1.0", out)
            self.assertIn("notes.txt", out)
            self.assertNotIn("<b>x</b>", out)


if __name__ == "__main__":
    unittest.main()
