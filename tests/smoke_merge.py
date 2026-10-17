"""Headless check of pull with conflicts, the conflict window and finishing the merge.

    QT_QPA_PLATFORM=offscreen python3 tests/smoke_merge.py [out_dir]
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="geogit-qgis-")

from qgis.core import QgsApplication  # noqa: E402
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402

app = QgsApplication([], True)
app.initQgis()

from osgeo import ogr, osr  # noqa: E402

from geogit import git_backend as gb  # noqa: E402
from geogit import i18n  # noqa: E402
from geogit.dock import GeoGitDock  # noqa: E402
from geogit.merge_dialog import ConflictDialog  # noqa: E402


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


def sq(x, s=100):
    x += 480000
    return f"POLYGON(({x} 4620000,{x + s} 4620000,{x + s} {4620000 + s},{x} {4620000 + s},{x} 4620000))"


def write_rows(path, rows):
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    lyr = ds.CreateLayer("ნაკვეთები", srs, ogr.wkbPolygon)
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


def read_rows(path):
    ds = ogr.Open(path)
    out = {f.GetFID(): (f.GetField("owner"), f.GetField("area")) for f in ds.GetLayer(0)}
    ds = None
    return out


root = tempfile.mkdtemp()
remote = os.path.join(root, "server.git")
gb.run_git(["init", "--bare", "-b", "main", remote], root)

nino = gb.GitRepo(os.path.join(root, "nino")).init()
nino.set_identity("Nino", "nino@example.com")
nino.set_remote(remote)
base = {i: (f"owner {i}", 100.0, sq(i * 150)) for i in range(1, 6)}
gp_n = os.path.join(nino.path, "parcels.gpkg")
write_rows(gp_n, base)
with open(os.path.join(nino.path, "notes.txt"), "w") as f:
    f.write("v1\n")
nino.commit("საწყისი ნაკვეთები")
nino.push()

giorgi = gb.clone(remote, os.path.join(root, "giorgi"))
giorgi.set_identity("Giorgi", "giorgi@example.com")
g = dict(base)
g[1] = ("owner 1", 120.0, base[1][2])          # area of 1
g[3] = ("გიორგი", 100.0, base[3][2])            # owner of 3 -> conflict
g[6] = ("new by giorgi", 50.0, sq(900))         # new parcel
write_rows(os.path.join(giorgi.path, "parcels.gpkg"), g)
with open(os.path.join(giorgi.path, "notes.txt"), "w") as f:
    f.write("v2 giorgi\n")
giorgi.commit("გიორგის ცვლილებები")
giorgi.push()

n = dict(base)
n[1] = ("ნინო", 100.0, base[1][2])              # owner of 1 (combines with giorgi's area)
n[3] = ("ნინოს ნაკვეთი", 100.0, base[3][2])     # owner of 3 -> conflict
del n[5]                                        # deletes 5
write_rows(gp_n, n)
with open(os.path.join(nino.path, "notes.txt"), "w") as f:
    f.write("v2 nino\n")
nino.commit("ნინოს ცვლილებები")

i18n.set_lang("ka")
iface = FakeIface()
dock = GeoGitDock(iface)
dock.set_folder(nino.path)
dock._pull()
spin(lambda: not dock._tasks)
assert nino.in_merge()
print("banner:", dock.merge_label.text(), "| commit button:", dock.btn_commit.text())
[dlg] = [d for d in dock._dialogs if isinstance(d, ConflictDialog)]
print("conflicted files:", [dlg.files.topLevelItem(i).text(0) for i in range(dlg.files.topLevelItemCount())])

# GeoPackage: merge features, one real conflict left
for i in range(dlg.files.topLevelItemCount()):
    if dlg.files.topLevelItem(i).text(0) == "parcels.gpkg":
        dlg.files.setCurrentItem(dlg.files.topLevelItem(i))
dlg._feature_merge()
rows = [[dlg.features.topLevelItem(0).text(c) for c in range(5)]]
print("feature conflicts:", rows)
assert dlg.features.topLevelItemCount() == 1 and rows[0][1] == "3"

out_dir = sys.argv[1] if len(sys.argv) > 1 else None
if out_dir:
    dlg.resize(820, 520)
    dlg.show()
    app.processEvents()
    dlg.grab().save(os.path.join(out_dir, "merge_dialog.png"))

dlg._set_all(1)                                  # take Giorgi's owner for parcel 3
dlg._apply_choices()

# notes.txt: keep mine
for i in range(dlg.files.topLevelItemCount()):
    if dlg.files.topLevelItem(i).text(0) == "notes.txt":
        dlg.files.setCurrentItem(dlg.files.topLevelItem(i))
dlg._take_side("ours")
assert nino.conflicts() == [], nino.conflicts()
print("after resolving:", dlg.info.text())
dlg.close()

if out_dir:
    dock.resize(520, 520)
    dock.tabs.setCurrentIndex(0)
    dock.show()
    app.processEvents()
    dock.grab().save(os.path.join(out_dir, "merge_panel.png"))

dock._commit()                                   # "Finish merge"
spin(lambda: not dock._tasks)
assert not nino.in_merge()
head = nino.log()[0]
print("merge commit:", head.subject, "| parents:", len(head.parents))
got = read_rows(gp_n)
print("result:", got)
assert got == {1: ("ნინო", 120.0), 2: ("owner 2", 100.0), 3: ("გიორგი", 100.0), 4: ("owner 4", 100.0),
               6: ("new by giorgi", 50.0)}, got
with open(os.path.join(nino.path, "notes.txt")) as f:
    assert f.read() == "v2 nino\n"
assert not dock.merge_bar.isVisibleTo(dock)

dock.cleanup()
print("MERGE SMOKE OK")
app.exitQgis()
