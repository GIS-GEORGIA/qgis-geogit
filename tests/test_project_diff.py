"""Unit tests for project_diff.py (standard library only).

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import project_diff as pd  # noqa: E402


def layer(lid, name, source, color="red", labels=False, crs="EPSG:32638"):
    return f"""<maplayer type="vector" labelsEnabled="{int(labels)}" hasScaleBasedVisibilityFlag="0" minScale="1e8" maxScale="0">
      <id>{lid}</id><datasource>{source}</datasource><layername>{name}</layername>
      <srs><spatialrefsys><authid>{crs}</authid></spatialrefsys></srs><provider>ogr</provider>
      <renderer-v2 type="singleSymbol"><symbol color="{color}"/></renderer-v2>
    </maplayer>"""


def project(layers, tree, crs="EPSG:32638", layouts=()):
    lays = "".join(f'<Layout name="{n}"><Page w="{w}"/></Layout>' for n, w in layouts)
    return ET.fromstring(f"""<qgis version="3.44">
      <title>t</title>
      <projectCrs><spatialrefsys><authid>{crs}</authid></spatialrefsys></projectCrs>
      <layer-tree-group>{tree}</layer-tree-group>
      <projectlayers>{''.join(layers)}</projectlayers>
      <Layouts>{lays}</Layouts>
    </qgis>""")


def tl(lid, checked=True):
    return f'<layer-tree-layer id="{lid}" checked="Qt::{"Checked" if checked else "Unchecked"}"/>'


def by(changes):
    return {(c.section, c.kind, c.name, c.detail.split("|")[0]) for c in changes}


class ProjectDiffTest(unittest.TestCase):
    def test_layers_tree_layouts(self):
        old = project([layer("a", "გზები", "./roads.gpkg"), layer("b", "ნაკვეთები", "./p.gpkg"),
                       layer("c", "dem", "./dem.tif")],
                      tl("a") + tl("b") + '<layer-tree-group name="Base" checked="Qt::Checked">' + tl("c") + "</layer-tree-group>",
                      layouts=[("A4", 1), ("A3", 1)])
        new = project([layer("a", "გზები (2026)", "./roads.gpkg", color="blue", labels=True),
                       layer("c", "dem", "./dem_2026.tif"), layer("d", "შენობები", "./b.gpkg")],
                      tl("d") + '<layer-tree-group name="Base" checked="Qt::Checked">' + tl("a", False) + tl("c") +
                      "</layer-tree-group>",
                      crs="EPSG:4326", layouts=[("A4", 2), ("ახალი", 1)])
        got = by(pd.diff_projects(old, new))
        expect = {
            ("project", "changed", "crs", "EPSG:32638 → EPSG:4326"),
            ("layers", "added", "შენობები", "./b.gpkg"),
            ("layers", "removed", "ნაკვეთები", "./p.gpkg"),
            ("layers", "changed", "გზები (2026)", "renamed"),
            ("layers", "changed", "გზები (2026)", "style"),
            ("layers", "changed", "გზები (2026)", "labels_on"),
            ("layers", "changed", "dem", "source"),
            ("tree", "changed", "გზები (2026)", "moved"),
            ("tree", "changed", "გზები (2026)", "hidden"),
            ("layouts", "changed", "A4", ""),
            ("layouts", "added", "ახალი", ""),
            ("layouts", "removed", "A3", ""),
        }
        self.assertEqual(got, expect)

    def test_order_only(self):
        layers = [layer("a", "A", "a"), layer("b", "B", "b")]
        got = by(pd.diff_projects(project(layers, tl("a") + tl("b")), project(layers, tl("b") + tl("a"))))
        self.assertEqual(got, {("tree", "changed", "", "order")})

    def test_same_and_new_file(self):
        p = project([layer("a", "A", "a")], tl("a"))
        self.assertEqual(pd.diff_projects(p, p), [])
        got = by(pd.diff_projects(None, p))
        self.assertEqual(got, {("layers", "added", "A", "a")})

    def test_qgz_is_read(self):
        root = project([layer("a", "A", "a")], tl("a"))
        with tempfile.TemporaryDirectory() as d:
            qgz = os.path.join(d, "p.qgz")
            with zipfile.ZipFile(qgz, "w") as z:
                z.writestr("p.qgs", ET.tostring(root))
                z.writestr("p.qgd", b"")
            self.assertEqual(pd.diff_files(qgz, qgz), [])
            self.assertEqual(pd.read_root(qgz).find("title").text, "t")
            self.assertIsNone(pd.read_root(os.path.join(d, "missing.qgz")))


if __name__ == "__main__":
    unittest.main()
