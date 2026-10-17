"""Unit tests for graph.py.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import graph  # noqa: E402

C = namedtuple("C", "sha parents")


class LayoutTest(unittest.TestCase):
    def test_linear(self):
        rows = graph.layout([C("c", ["b"]), C("b", ["a"]), C("a", [])])
        self.assertEqual([r.col for r in rows], [0, 0, 0])
        self.assertEqual(rows[0].up, [])            # tip: nothing above
        self.assertEqual(rows[0].down, [(0, 0)])
        self.assertEqual(rows[1].up, [(0, 0)])
        self.assertEqual(rows[2].down, [])          # root: nothing below
        self.assertEqual(max(r.width for r in rows), 1)

    def test_branch_and_merge(self):
        #   m        merge of x (main) and y (feature)
        #   |\
        #   x |
        #   | y
        #   |/
        #   a
        rows = graph.layout([C("m", ["x", "y"]), C("x", ["a"]), C("y", ["a"]), C("a", [])])
        m, x, y, a = rows
        self.assertTrue(m.is_merge)
        self.assertEqual(m.down, [(0, 0), (0, 1)])
        self.assertEqual(x.col, 0)
        self.assertIn((1, 1), x.up)                 # feature lane passes by x
        self.assertIn((1, 1), x.down)
        self.assertEqual(y.col, 1)
        self.assertIn((0, 0), y.up)                 # main lane (now waiting for a) passes by y
        self.assertEqual(sorted(y.down), [(0, 0), (1, 0)])   # y joins a's lane, main passes by
        self.assertEqual(a.col, 0)
        self.assertEqual(a.up, [(0, 0)])
        self.assertEqual(m.width, 2)

    def test_two_tips(self):
        # two branches not merged yet, both from a
        rows = graph.layout([C("p", ["a"]), C("q", ["a"]), C("a", [])])
        p, q, a = rows
        self.assertEqual((p.col, q.col), (0, 1))
        self.assertEqual(q.up, [(0, 0)])            # p's lane passes q, q has no child
        self.assertEqual(sorted(q.down), [(0, 0), (1, 0)])
        self.assertEqual(sorted(a.up), [(0, 0)])

    def test_missing_parent_ignored(self):
        rows = graph.layout([C("b", ["a"])])        # history cut by --max-count
        self.assertEqual(rows[0].down, [])

    def test_author_color_stable(self):
        self.assertEqual(graph.author_color("Nino@X.ge "), graph.author_color("nino@x.ge"))
        self.assertIn(graph.author_color("a@b"), graph.PALETTE)


if __name__ == "__main__":
    unittest.main()
