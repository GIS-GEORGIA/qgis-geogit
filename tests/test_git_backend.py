"""Unit tests for git_backend.py (standard library + git only).

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import git_backend as gb  # noqa: E402


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def new_repo(path, name="Nino"):
    repo = gb.GitRepo(path).init(use_lfs=False)
    repo.set_identity(name, f"{name.lower()}@example.com")
    return repo


class FileKindTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(gb.file_kind("a/roads.GPKG"), "vector")
        self.assertEqual(gb.file_kind("dem.tif"), "raster")
        self.assertEqual(gb.file_kind("map.qgz"), "project")
        self.assertEqual(gb.file_kind("roads.qml"), "style")
        self.assertEqual(gb.file_kind("data.gdb/a00000001.gdbtable"), "vector")
        self.assertEqual(gb.file_kind("README.md"), "other")


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_writes_ignore_once(self):
        repo = new_repo(os.path.join(self.root, "p"))
        repo.init(use_lfs=False)
        with open(os.path.join(repo.path, ".gitignore"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines.count("*.gpkg-wal"), 1)
        self.assertTrue(repo.is_repo())
        self.assertEqual(repo.log(), [])

    def test_status_commit_log(self):
        repo = new_repo(os.path.join(self.root, "p"))
        write(os.path.join(repo.path, "layers", "roads.geojson"), "{}")
        write(os.path.join(repo.path, "project.qgs"), "<qgis/>")
        write(os.path.join(repo.path, "roads.gpkg-wal"), "tmp")
        paths = {c.path: c for c in repo.status()}
        self.assertIn("layers/roads.geojson", paths)
        self.assertNotIn("roads.gpkg-wal", paths)
        self.assertEqual(paths["layers/roads.geojson"].code, "A")
        self.assertEqual(paths["layers/roads.geojson"].kind, "vector")

        repo.commit("first: only roads", ["layers/roads.geojson"])
        left = {c.path for c in repo.status()}
        self.assertIn("project.qgs", left)
        self.assertNotIn("layers/roads.geojson", left)

        write(os.path.join(repo.path, "layers", "roads.geojson"), '{"a":1}')
        self.assertEqual({c.path: c.code for c in repo.status()}["layers/roads.geojson"], "M")
        repo.commit("second")
        self.assertEqual(repo.status(), [])

        log = repo.log()
        self.assertEqual([c.subject for c in log], ["second", "first: only roads"])
        self.assertEqual(log[0].author, "Nino")
        self.assertEqual(log[0].parents, [log[1].sha])
        self.assertIn("HEAD -> main", log[0].refs)
        files = dict((p, s) for s, p in repo.commit_files(log[0].sha))
        self.assertEqual(files["layers/roads.geojson"], "M")
        self.assertEqual(files["project.qgs"], "A")

    def test_discard(self):
        repo = new_repo(os.path.join(self.root, "p"))
        write(os.path.join(repo.path, "a.csv"), "1")
        repo.commit("a")
        write(os.path.join(repo.path, "a.csv"), "2")
        write(os.path.join(repo.path, "new.txt"), "n")
        repo.discard(["a.csv", "new.txt"])
        self.assertEqual(repo.status(), [])
        with open(os.path.join(repo.path, "a.csv"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "1")

    def test_empty_message_rejected(self):
        repo = new_repo(os.path.join(self.root, "p"))
        write(os.path.join(repo.path, "a.csv"))
        with self.assertRaises(gb.GitError):
            repo.commit("  ")

    def test_push_pull_between_two_users(self):
        remote = os.path.join(self.root, "server.git")
        gb.run_git(["init", "--bare", "-b", "main", remote], self.root)

        nino = new_repo(os.path.join(self.root, "nino"))
        nino.set_remote(remote)
        write(os.path.join(nino.path, "parcels.csv"), "id\n1\n")
        nino.commit("parcels")
        nino.push()
        self.assertEqual(nino.branch_info().upstream, "origin/main")

        giorgi = gb.clone(remote, os.path.join(self.root, "giorgi"))
        giorgi.set_identity("Giorgi", "giorgi@example.com")
        write(os.path.join(giorgi.path, "parcels.csv"), "id\n1\n2\n")
        giorgi.commit("add parcel 2")
        self.assertEqual(giorgi.branch_info().ahead, 1)
        giorgi.push()

        nino.fetch()
        self.assertEqual(nino.branch_info().behind, 1)
        nino.pull()
        authors = [c.author for c in nino.log()]
        self.assertEqual(authors, ["Giorgi", "Nino"])

    def test_log_files_and_path_filter(self):
        repo = new_repo(os.path.join(self.root, "p"))
        write(os.path.join(repo.path, "ფენები", "გზები.geojson"), "1")
        repo.commit("roads")
        write(os.path.join(repo.path, "dem.tif"), "r")
        repo.commit("dem")
        write(os.path.join(repo.path, "ფენები", "გზები.geojson"), "2")
        repo.commit("roads again")

        log = repo.log()
        self.assertEqual(log[0].files, ["ფენები/გზები.geojson"])
        self.assertEqual(log[1].files, ["dem.tif"])

        roads = repo.log(path="ფენები/გზები.geojson")
        self.assertEqual([c.subject for c in roads], ["roads again", "roads"])
        self.assertEqual(roads[0].parents, [roads[1].sha])   # rewritten past "dem"

    def test_branches(self):
        repo = new_repo(os.path.join(self.root, "p"))
        write(os.path.join(repo.path, "a.csv"))
        repo.commit("a")
        repo.create_branch("survey-2026")
        self.assertEqual(repo.branch_info().name, "survey-2026")
        self.assertEqual(sorted(repo.branches()), ["main", "survey-2026"])
        repo.switch("main")
        self.assertEqual(repo.branch_info().name, "main")


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        repo = new_repo(os.path.join(self.tmp.name, "p"))
        write(os.path.join(repo.path, "გზები.gpkg"), "A\0base")
        write(os.path.join(repo.path, "notes.txt"), "base\n")
        write(os.path.join(repo.path, "only_main.txt"), "m\n")
        repo.commit("base")
        repo.create_branch("survey")
        write(os.path.join(repo.path, "გზები.gpkg"), "A\0survey")
        write(os.path.join(repo.path, "new_on_survey.txt"), "s\n")
        repo.commit("survey work")
        repo.switch("main")
        write(os.path.join(repo.path, "გზები.gpkg"), "A\0main")
        repo.commit("main work")
        self.repo = repo

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        with open(os.path.join(self.repo.path, name), encoding="utf-8") as f:
            return f.read()

    def test_conflict_stages_and_resolve(self):
        conflicts = self.repo.merge("survey")
        self.assertEqual(conflicts, ["გზები.gpkg"])
        self.assertTrue(self.repo.in_merge())
        self.assertEqual(self.repo.merge_head_name(), "survey")
        self.assertEqual({c.path: c.code for c in self.repo.status()}["გზები.gpkg"], "U")
        out = os.path.join(self.tmp.name, "stages")
        for stage, text in ((1, "base"), (2, "main"), (3, "survey")):
            with open(self.repo.export_stage(stage, "გზები.gpkg", os.path.join(out, str(stage))), "rb") as f:
                self.assertEqual(f.read(), b"A\0" + text.encode())
        with self.assertRaises(gb.GitError):
            self.repo.finish_merge()
        self.repo.resolve("გზები.gpkg", "theirs")
        self.assertEqual(self.repo.conflicts(), [])
        sha = self.repo.finish_merge()
        self.assertFalse(self.repo.in_merge())
        self.assertEqual(len(self.repo.log()[0].parents), 2)
        self.assertEqual(self.read("გზები.gpkg"), "A\0survey")
        self.assertEqual(self.read("new_on_survey.txt"), "s\n")
        self.assertEqual(self.repo.log()[0].sha, sha)

    def test_abort(self):
        self.repo.merge("survey")
        self.repo.abort_merge()
        self.assertFalse(self.repo.in_merge())
        self.assertEqual(self.read("გზები.gpkg"), "A\0main")

    def test_clean_merge(self):
        repo = self.repo
        repo.git("reset", "-q", "--hard", "HEAD~1")    # main without its gpkg edit
        self.assertEqual(repo.merge("survey"), [])
        self.assertFalse(repo.in_merge())

    def test_resolve_mark_manual(self):
        self.repo.merge("survey")
        write(os.path.join(self.repo.path, "გზები.gpkg"), "A\0merged by hand")
        self.repo.mark_resolved("გზები.gpkg")
        self.repo.finish_merge("Merge survey: roads merged by hand")
        self.assertEqual(self.repo.log()[0].subject, "Merge survey: roads merged by hand")


class FakeLfsRepo(gb.GitRepo):
    """Answers `git lfs ...` with canned JSON, as git-lfs 3.x prints it."""

    def __init__(self, verify_ok=True):
        super().__init__(".")
        self.verify_ok = verify_ok
        self.calls = []

    def git(self, *args, **kw):
        self.calls.append(args)
        if args[:2] == ("lfs", "locks") and "--verify" in args:
            if not self.verify_ok:
                raise gb.GitError("server does not support lock verification")
            return ('{"ours":[{"id":"1","path":"dem.tif","owner":{"name":"nino"},"locked_at":"2026-10-17T10:00:00Z"}],'
                    '"theirs":[{"id":"2","path":"ფენები/გზები.gpkg","owner":{"name":"giorgi"},'
                    '"locked_at":"2026-10-16T09:00:00Z"}]}')
        if args[:2] == ("lfs", "locks"):
            return '[{"id":"2","path":"ფენები/გზები.gpkg","owner":{"name":"giorgi"},"locked_at":"x"}]'
        if args[:2] == ("lfs", "lock"):
            return '{"id":"3","path":"%s","owner":{"name":"nino"},"locked_at":"now"}' % args[-1]
        if args[:2] == ("lfs", "unlock"):
            return ""
        raise AssertionError(args)


class LockTest(unittest.TestCase):
    def test_locks_verified(self):
        locks = FakeLfsRepo().locks()
        self.assertEqual([(lk.path, lk.owner, lk.ours) for lk in locks],
                         [("dem.tif", "nino", True), ("ფენები/გზები.gpkg", "giorgi", False)])

    def test_locks_without_verify(self):
        [lk] = FakeLfsRepo(verify_ok=False).locks()
        self.assertEqual((lk.path, lk.owner, lk.ours), ("ფენები/გზები.gpkg", "giorgi", None))

    def test_lock_unlock(self):
        repo = FakeLfsRepo()
        lk = repo.lock("dem.tif")
        self.assertEqual((lk.id, lk.path, lk.ours), ("3", "dem.tif", True))
        repo.unlock("dem.tif", force=True)
        self.assertEqual(repo.calls[-1], ("lfs", "unlock", "--force", "dem.tif"))


if __name__ == "__main__":
    unittest.main()
