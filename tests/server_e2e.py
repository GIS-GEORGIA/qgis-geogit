"""End-to-end run against the local Forgejo from server/ (two users, one repository).

    python3 tests/server_e2e.py

Needs server/docker-compose.yml running and server/setup_users.sh done.
LFS and lock checks run only when git-lfs is installed.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "geogit"))
import git_backend as gb  # noqa: E402
import vector_merge as vm  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402

gdal.UseExceptions()
URL = "http://localhost:3000"
REPO = "geo-demo"


def env():
    vals = {}
    with open(os.path.join(HERE, "server", ".env.local"), encoding="utf-8") as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            vals[k] = v
    return vals


ENV = env()


def api(method, path, user, body=None, ok=(200, 201, 204), raw=False):
    req = urllib.request.Request(f"{URL}/api/v1{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"token {ENV[user.upper() + '_TOKEN']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            data = r.read()
            if raw:
                return data
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return None
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:200]}") from None


def checkout(user, root):
    """Clone the demo repository as user, with that user's token stored for git."""
    dest = os.path.join(root, user)
    cred = os.path.join(root, f".cred-{user}")
    with open(cred, "w") as f:
        f.write(f"http://{user}:{ENV[user.upper() + '_TOKEN']}@localhost%3a3000\n")
    os.chmod(cred, 0o600)
    repo = gb.clone(f"{URL}/nino/{REPO}.git", dest, {"credential.helper": f"store --file={cred}"})
    repo.set_identity(user.capitalize(), f"{user}@geogit.test")
    return repo


def write_parcels(path, rows):
    if os.path.exists(path):
        os.remove(path)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32638)
    lyr = ds.CreateLayer("ნაკვეთები", srs, ogr.wkbPoint)
    lyr.CreateField(ogr.FieldDefn("owner", ogr.OFTString))
    for fid, owner in sorted(rows.items()):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetFID(fid)
        f.SetField("owner", owner)
        f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({480000 + fid * 10} 4620000)"))
        lyr.CreateFeature(f)
    ds = None


def owners(path):
    ds = ogr.Open(path)
    out = {f.GetFID(): f.GetField("owner") for f in ds.GetLayer(0)}
    ds = None
    return out


def step(text):
    print(f"• {text}")


def main():
    lfs = shutil.which("git-lfs") is not None
    print(f"server {URL} · git-lfs {'yes' if lfs else 'NO (LFS and lock checks skipped)'}")

    api("DELETE", f"/repos/nino/{REPO}", "nino", ok=(204, 404))
    api("POST", "/user/repos", "nino", {"name": REPO, "private": True, "default_branch": "main"})
    api("PUT", f"/repos/nino/{REPO}/collaborators/giorgi", "nino", {"permission": "write"})
    step("repository nino/geo-demo created, giorgi can write")

    root = tempfile.mkdtemp(prefix="geogit-e2e-")
    nino = checkout("nino", root)
    nino.init(use_lfs=lfs)
    gpkg = os.path.join(nino.path, "parcels.gpkg")
    write_parcels(gpkg, {1: "a", 2: "b", 3: "c"})
    with open(os.path.join(nino.path, "README.md"), "w") as f:
        f.write("# geo-demo\n")
    nino.commit("საწყისი მონაცემები")
    nino.push()
    step("nino: first version published")

    if lfs:
        pointer = nino.git("cat-file", "-p", "HEAD:parcels.gpkg")
        assert pointer.startswith("version https://git-lfs"), pointer[:80]
        # the server marks a new repository non-empty a moment after the first push
        for _ in range(30):
            try:
                content = api("GET", f"/repos/nino/{REPO}/media/parcels.gpkg", "nino", ok=(200,), raw=True)
                break
            except RuntimeError:
                time.sleep(0.5)
        else:
            raise AssertionError("LFS content of parcels.gpkg not served")
        assert content.startswith(b"SQLite format 3"), content[:40]
        step("parcels.gpkg is stored in LFS on the server")

    giorgi = checkout("giorgi", root)
    assert owners(os.path.join(giorgi.path, "parcels.gpkg")) == {1: "a", 2: "b", 3: "c"}
    step("giorgi: cloned, GeoPackage readable")

    write_parcels(os.path.join(giorgi.path, "parcels.gpkg"), {1: "a", 2: "გიორგი", 3: "c", 4: "new"})
    giorgi.commit("გიორგი: ნაკვეთი 2 და 4")
    giorgi.push()
    write_parcels(gpkg, {1: "ნინო", 2: "b", 3: "c"})
    nino.commit("ნინო: ნაკვეთი 1")
    try:
        nino.push()
        raise AssertionError("push should be rejected: the server has newer work")
    except gb.GitError:
        step("nino: push refused until giorgi's work is pulled (as expected)")

    conflicts = nino.pull()
    assert conflicts == ["parcels.gpkg"], conflicts
    work = tempfile.mkdtemp(dir=root)
    sides = [nino.export_stage(s, "parcels.gpkg", os.path.join(work, str(s))) for s in (1, 2, 3)]
    report = vm.merge_files(*sides, gpkg)
    assert report.ok, report.conflicts
    nino.mark_resolved("parcels.gpkg")
    nino.finish_merge()
    nino.push()
    assert owners(gpkg) == {1: "ნინო", 2: "გიორგი", 3: "c", 4: "new"}
    step("nino: conflict merged feature by feature and published")

    assert giorgi.pull() == []
    assert owners(os.path.join(giorgi.path, "parcels.gpkg")) == owners(gpkg)
    authors = [c.author for c in giorgi.log()]
    step(f"giorgi: got the merge; history authors {authors}")

    if lfs:
        nino.lock("parcels.gpkg")
        theirs = {x.path: x for x in giorgi.locks()}
        assert theirs["parcels.gpkg"].ours is False and theirs["parcels.gpkg"].owner == "nino", theirs
        step("nino locked parcels.gpkg; giorgi sees it as someone else's lock")
        write_parcels(os.path.join(giorgi.path, "parcels.gpkg"), {1: "x"})
        giorgi.commit("giorgi edits a locked file")
        try:
            giorgi.push()
            raise AssertionError("push of a file locked by nino should be refused")
        except gb.GitError as e:
            step(f"giorgi: push refused because of the lock ({str(e).splitlines()[0][:70]})")
        giorgi.git("reset", "-q", "--hard", "origin/main")
        nino.unlock("parcels.gpkg")
        assert giorgi.locks() == []
        step("nino unlocked it")

    if lfs:
        # a copy cloned with plain git (no LFS set up) holds pointer files until GeoGit opens it
        plain = os.path.join(root, "plain")
        cred = os.path.join(root, ".cred-giorgi")
        gb.run_git(["-c", f"credential.helper=store --file={cred}", "-c", "filter.lfs.smudge=", "-c",
                    "filter.lfs.process=", "-c", "filter.lfs.required=false", "clone", f"{URL}/nino/{REPO}.git",
                    plain], root)
        repo = gb.GitRepo(plain)
        repo.git("config", "credential.helper", f"store --file={cred}")
        with open(os.path.join(plain, "parcels.gpkg"), "rb") as f:
            assert f.read(40).startswith(b"version https://git-lfs"), "expected a pointer file"
        assert repo.ensure_lfs() is True and repo.ensure_lfs() is False
        repo.lfs_pull()
        assert owners(os.path.join(plain, "parcels.gpkg")) == owners(gpkg)
        step("a plain git clone got LFS switched on and its real GeoPackage fetched")

    print(f"E2E OK  (working copies in {root})")


if __name__ == "__main__":
    main()
