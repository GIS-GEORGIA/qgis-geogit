"""Unit tests for vector_merge.py (needs GDAL Python bindings).

    python3 -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import vector_merge as vm  # noqa: E402
from osgeo import ogr, osr  # noqa: E402


def sq(x, s=1):
    return f"POLYGON(({x} 0,{x + s} 0,{x + s} {s},{x} {s},{x} 0))"


def gpkg(path, rows, layer="parcels"):
    """rows: {fid: (owner, area, wkt)}"""
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    lyr = ds.CreateLayer(layer, srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("owner", ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("area", ogr.OFTReal))
    for fid, (owner, area, wkt) in sorted(rows.items()):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetFID(fid)
        f.SetField("owner", owner)
        f.SetField("area", area)
        f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(f)
    ds = None


def rows_of(path):
    ds = ogr.Open(path)
    lyr = ds.GetLayer(0)
    out = {f.GetFID(): (f.GetField("owner"), f.GetField("area"), f.GetGeometryRef().ExportToWkt()) for f in lyr}
    ds = None
    return out


BASE = {1: ("a", 1.0, sq(0)), 2: ("b", 1.0, sq(2)), 3: ("c", 1.0, sq(4)), 4: ("d", 1.0, sq(6))}


class VectorMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, ours, theirs):
        paths = [os.path.join(self.d, n) for n in ("base.gpkg", "ours.gpkg", "theirs.gpkg")]
        for p, rows in zip(paths, (BASE, ours, theirs)):
            gpkg(p, rows)
        return paths + [os.path.join(self.d, "out.gpkg")]

    def test_different_features_merge_cleanly(self):
        ours = dict(BASE)
        ours[1] = ("ნინო", 1.0, sq(0))                     # ours edits 1
        ours[5] = ("new-ours", 2.0, sq(10))                # ours adds 5
        theirs = dict(BASE)
        theirs[2] = ("b", 1.0, sq(2, 2))                   # theirs reshapes 2
        del theirs[3]                                      # theirs deletes 3
        theirs[6] = ("new-theirs", 3.0, sq(12))            # theirs adds 6
        base, o, t, out = self.files(ours, theirs)
        r = vm.merge_files(base, o, t, out)
        self.assertTrue(r.ok)
        got = rows_of(out)
        self.assertEqual(sorted(got), [1, 2, 4, 5, 6])
        self.assertEqual(got[1][0], "ნინო")
        self.assertEqual(got[2][2], "POLYGON ((2 0,4 0,4 2,2 2,2 0))")
        self.assertEqual(got[6][0], "new-theirs")
        self.assertEqual(r.applied, 3)
        self.assertEqual(sorted(rows_of(o)), [1, 2, 3, 4, 5])  # ours itself is left untouched

    def test_same_feature_different_fields_combines(self):
        ours = dict(BASE)
        ours[1] = ("ნინო", 1.0, sq(0))                     # owner
        theirs = dict(BASE)
        theirs[1] = ("a", 9.5, sq(0, 3))                   # area and shape
        r = vm.merge_files(*self.files(ours, theirs))
        self.assertTrue(r.ok)
        self.assertEqual(r.merged_fields, 1)
        owner, area, wkt = rows_of(os.path.join(self.d, "out.gpkg"))[1]
        self.assertEqual((owner, area), ("ნინო", 9.5))
        self.assertIn("3 3", wkt)

    def test_conflicts_and_choices(self):
        ours = dict(BASE)
        ours[1] = ("ours-owner", 1.0, sq(0))
        del ours[2]
        ours[3] = ("c", 5.0, sq(4))
        theirs = dict(BASE)
        theirs[1] = ("theirs-owner", 1.0, sq(0))           # same field, other value
        theirs[2] = ("b", 2.0, sq(2))                      # edits what ours deleted
        del theirs[3]                                      # deletes what ours edited
        theirs[4] = ("d2", 1.0, sq(6))                     # clean change
        paths = self.files(ours, theirs)
        r = vm.merge_files(*paths)
        self.assertEqual(sorted((c.fid, c.kind) for c in r.conflicts),
                         [(1, "both_modified"), (2, "ours_deleted"), (3, "theirs_deleted")])
        self.assertEqual(r.conflicts[0].fields, ["owner"])
        self.assertEqual(rows_of(paths[3])[4][0], "d2")    # clean part already applied

        choices = {("parcels", 1): "theirs", ("parcels", 2): "theirs", ("parcels", 3): "ours"}
        r = vm.merge_files(*paths, choices=choices)
        self.assertTrue(r.ok)
        got = rows_of(paths[3])
        self.assertEqual(got[1][0], "theirs-owner")
        self.assertEqual(got[2][1], 2.0)                   # brought back
        self.assertEqual(got[3][1], 5.0)                   # ours kept
        self.assertEqual(got[4][0], "d2")

    def test_both_added_same_fid(self):
        ours = dict(BASE)
        ours[5] = ("ours5", 1.0, sq(10))
        theirs = dict(BASE)
        theirs[5] = ("theirs5", 1.0, sq(20))
        paths = self.files(ours, theirs)
        r = vm.merge_files(*paths)
        self.assertTrue(r.ok)
        self.assertEqual(r.renumbered, 1)
        owners = sorted(v[0] for v in rows_of(paths[3]).values())
        self.assertEqual(owners, ["a", "b", "c", "d", "ours5", "theirs5"])

    def test_unsupported(self):
        base, o, t, out = self.files(BASE, BASE)
        gj = os.path.join(self.d, "x.geojson")
        with open(gj, "w") as f:
            f.write('{"type":"FeatureCollection","features":[]}')
        with self.assertRaises(vm.MergeUnsupported):
            vm.merge_files(gj, gj, gj, out)
        shutil.copy(o, t)
        gpkg(t, BASE, layer="other")
        with self.assertRaises(vm.MergeUnsupported):
            vm.merge_files(base, o, t, out)


if __name__ == "__main__":
    unittest.main()
