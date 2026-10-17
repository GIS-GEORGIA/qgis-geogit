"""Unit tests for vector_diff.py (needs GDAL Python bindings).

    python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import git_backend as gb  # noqa: E402
import vector_diff as vd  # noqa: E402
from osgeo import ogr, osr  # noqa: E402


def geojson(path, feats):
    """feats: [(props, (x, y))]"""
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": p, "geometry": {"type": "Point", "coordinates": list(xy)}}
        for p, xy in feats]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def gpkg(path, rows, layer="parcels"):
    """rows: [(fid, name, wkt)]; rewrites the file each time like an edit session would."""
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    lyr = ds.CreateLayer(layer, srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("name", ogr.OFTString))
    for fid, name, wkt in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetFID(fid)
        f.SetField("name", name)
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(f)
    ds = None


SQ = "POLYGON((0 0,1 0,1 1,0 1,0 0))"
SQ2 = "POLYGON((0 0,2 0,2 2,0 2,0 0))"


class VectorDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, name):
        return os.path.join(self.d, name)

    def test_gpkg_by_fid(self):
        gpkg(self.p("old.gpkg"), [(1, "a", SQ), (2, "b", SQ), (3, "c", SQ)])
        gpkg(self.p("new.gpkg"), [(1, "a", SQ), (2, "B", SQ2), (4, "d", SQ)])
        [d] = vd.diff_files(self.p("old.gpkg"), self.p("new.gpkg"))
        self.assertEqual(d.key, "FID")
        self.assertEqual([f.fid for f in d.added], [4])
        self.assertEqual([f.fid for f in d.deleted], [3])
        [m] = d.modified
        self.assertEqual((m.old.fid, m.fields, m.geometry), (2, ["name"], True))
        self.assertIn("32638", d.srs_wkt)

    def test_geojson_with_id_field(self):
        geojson(self.p("old.geojson"), [({"code": "A1", "id": 10}, (0, 0)), ({"code": "B2", "id": 20}, (1, 1))])
        # row order changes and the first one is deleted: an id field keeps matching right
        geojson(self.p("new.geojson"), [({"code": "B2", "id": 20}, (1, 2)), ({"code": "C3", "id": 30}, (5, 5))])
        [d] = vd.diff_files(self.p("old.geojson"), self.p("new.geojson"))
        self.assertEqual(d.key, "id")
        self.assertEqual([f.attrs["code"] for f in d.added], ["C3"])
        self.assertEqual([f.attrs["code"] for f in d.deleted], ["A1"])
        self.assertEqual([(m.new.attrs["code"], m.geometry, m.fields) for m in d.modified], [("B2", True, [])])

    def test_no_key_matches_by_content(self):
        geojson(self.p("old.geojson"), [({"n": "გზა 1"}, (0, 0)), ({"n": "გზა 2"}, (1, 1)), ({"n": "გზა 3"}, (2, 2))])
        geojson(self.p("new.geojson"), [({"n": "გზა 2"}, (1, 1)), ({"n": "გზა 3"}, (2, 2)), ({"n": "გზა 4"}, (3, 3))])
        [d] = vd.diff_files(self.p("old.geojson"), self.p("new.geojson"))
        self.assertEqual(d.key, "")
        # shifted rows are not reported as changes
        changed = len(d.added) + len(d.deleted) + len(d.modified)
        self.assertLessEqual(changed, 2)
        self.assertEqual({f.attrs["n"] for f in d.deleted} | {m.old.attrs["n"] for m in d.modified}, {"გზა 1"})

    def test_field_added_and_missing_side(self):
        geojson(self.p("old.geojson"), [({"n": "a"}, (0, 0))])
        geojson(self.p("new.geojson"), [({"n": "a", "area": 5}, (0, 0))])
        [d] = vd.diff_files(self.p("old.geojson"), self.p("new.geojson"))
        self.assertEqual(d.fields_added, ["area"])
        self.assertEqual(d.modified, [])
        [d] = vd.diff_files(None, self.p("new.geojson"))
        self.assertEqual(len(d.added), 1)
        [d] = vd.diff_files(self.p("old.geojson"), None)
        self.assertEqual(len(d.deleted), 1)

    def test_unchanged(self):
        gpkg(self.p("a.gpkg"), [(1, "a", SQ)])
        [d] = vd.diff_files(self.p("a.gpkg"), self.p("a.gpkg"))
        self.assertFalse(d.changed)


class ExportVersionTest(unittest.TestCase):
    def test_shapefile_with_sidecars_from_history(self):
        with tempfile.TemporaryDirectory() as root:
            repo = gb.GitRepo(os.path.join(root, "p")).init(use_lfs=False)
            repo.set_identity("T", "t@example.com")
            shp = os.path.join(repo.path, "data", "ნაკვეთები.shp")
            os.makedirs(os.path.dirname(shp))
            for n in (1, 2):
                ds = ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(shp)
                lyr = ds.CreateLayer("ნაკვეთები", None, ogr.wkbPoint)
                for i in range(n):
                    f = ogr.Feature(lyr.GetLayerDefn())
                    f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({i} {i})"))
                    lyr.CreateFeature(f)
                ds = None
                repo.commit(f"v{n}")
            first = repo.log()[-1].sha
            out = repo.export_version(first, "data/ნაკვეთები.shp", os.path.join(root, "old"))
            self.assertTrue(os.path.exists(out[:-4] + ".dbf"))
            [d] = vd.diff_files(out, shp)
            self.assertEqual(len(d.added), 1)
            self.assertIsNone(repo.export_version(first, "nope.shp", os.path.join(root, "x")))
            self.assertEqual(gb.vector_main_file("a/b.DBF"), "a/b.shp")
            self.assertIsNone(gb.vector_main_file("dem.tif"))


if __name__ == "__main__":
    unittest.main()
