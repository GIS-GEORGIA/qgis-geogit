"""Create a repository on the local Forgejo through hosting.py and publish to it with GIT_ASKPASS only.

    python3 tests/server_hosting.py

Needs server/docker-compose.yml running and server/setup_users.sh done. LFS is
checked when git-lfs is on PATH.
"""
import os
import shutil
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "geogit"))
import git_backend as gb  # noqa: E402
import hosting as h  # noqa: E402

URL = "http://localhost:3000"
with open(os.path.join(HERE, "server", ".env.local"), encoding="utf-8") as f:
    ENV = dict(line.strip().split("=", 1) for line in f if "=" in line)


def api_delete(path, token):
    req = urllib.request.Request(URL + "/api/v1" + path, method="DELETE")
    req.add_header("Authorization", f"token {token}")
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def main():
    token = ENV["NINO_TOKEN"]
    lfs = shutil.which("git-lfs") is not None
    account = h.Account("forgejo", URL, token)
    p = h.provider(account)
    print("• token belongs to:", p.check())
    owners = p.owners()
    print("• can create in:", [o.name for o in owners])
    api_delete("/repos/nino/kartuli-proekti", token)

    # wrong token: a clear error, not a crash
    try:
        h.provider(h.Account("forgejo", URL, "wrong")).check()
        raise AssertionError("a wrong token was accepted")
    except h.HostingError as e:
        print("• wrong token ->", e)

    created = p.create(owners[0], "ქართული პროექტი", "GeoGit test", private=True)
    print("• created:", created.web_url)
    assert created.clone_url.endswith("/nino/kartuli-proekti.git"), created.clone_url

    # git gets the token only through GIT_ASKPASS: no helper, nothing in the URL or config
    gb.CREDENTIALS = lambda url: (p.git_username(), token) if h.host_of(url) == "localhost" else None
    root = tempfile.mkdtemp(prefix="geogit-hosting-")
    repo = gb.GitRepo(os.path.join(root, "project")).init(use_lfs=lfs)
    repo.set_identity("Nino", "nino@example.com")
    assert repo.git("config", "--get-all", "credential.helper", check=False).strip() == ""
    with open(os.path.join(repo.path, "dem.tif"), "wb") as f:
        f.write(b"II*\0" + os.urandom(2048))
    with open(os.path.join(repo.path, "README.md"), "w") as f:
        f.write("# ქართული პროექტი\n")
    repo.commit("პირველი ვერსია")
    repo.set_remote(created.clone_url)
    repo.push()
    print("• pushed with askpass; remote url has no token:", "@" not in repo.remote_url())
    assert token not in open(os.path.join(repo.path, ".git", "config")).read()

    if lfs:
        assert repo.git("cat-file", "-p", "HEAD:dem.tif").startswith("version https://git-lfs")
        repo.lock("dem.tif")
        assert [lk.path for lk in repo.locks()] == ["dem.tif"]
        repo.unlock("dem.tif")
        print("• LFS upload, lock and unlock through askpass: ok")

    copy = gb.clone(created.clone_url, os.path.join(root, "copy"))
    with open(os.path.join(copy.path, "dem.tif"), "rb") as f:
        assert f.read(4) == b"II*\0", "clone holds a pointer, not the raster"
    print("• cloned with askpass, real file content:", copy.log()[0].subject)

    leftovers = [n for n in os.listdir(os.path.expanduser("~/.cache/geogit")) if n.startswith("askpass-")]
    assert not leftovers, leftovers
    print("• no token files left in ~/.cache/geogit")

    api_delete("/repos/nino/kartuli-proekti", token)
    shutil.rmtree(root, ignore_errors=True)
    print("HOSTING E2E OK")


if __name__ == "__main__":
    main()
