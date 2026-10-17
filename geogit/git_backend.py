"""Thin wrapper around the git command line for a QGIS project folder.

No QGIS imports here, so the module runs (and is tested) outside QGIS.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field

VECTOR_EXT = {".gpkg", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".geojson", ".json",
              ".kml", ".kmz", ".gml", ".gpx", ".dxf", ".tab", ".map", ".id", ".dat", ".mif",
              ".mid", ".sqlite", ".fgb", ".csv", ".parquet"}
RASTER_EXT = {".tif", ".tiff", ".ecw", ".jp2", ".j2k", ".img", ".asc", ".vrt", ".sid",
              ".dem", ".hgt", ".nc", ".png", ".jpg", ".jpeg", ".bil", ".mbtiles"}
PROJECT_EXT = {".qgz", ".qgs"}
STYLE_EXT = {".qml", ".sld", ".qlr"}

# Binary geodata goes to Git LFS. .shp sidecars travel with the .shp so a
# shapefile is always stored the same way.
LFS_PATTERNS = ["*.gpkg", "*.shp", "*.shx", "*.dbf", "*.qix", "*.sqlite", "*.fgb", "*.parquet",
                "*.kmz", "*.tif", "*.tiff", "*.ecw", "*.jp2", "*.j2k", "*.img", "*.sid",
                "*.dem", "*.hgt", "*.nc", "*.bil", "*.mbtiles", "*.las", "*.laz", "*.zip",
                "*.qgz", "*.pdf", "*.gdb/**"]

# SQLite/GPKG side files and QGIS backups never belong in history.
DEFAULT_IGNORE = ["*.gpkg-wal", "*.gpkg-shm", "*.gpkg-journal", "*.sqlite-journal",
                  "*.qgz~", "*.qgs~", "*.qgd", "*.aux.xml", "__pycache__/", ".DS_Store"]


class GitError(Exception):
    pass


@dataclass
class FileChange:
    path: str
    index: str      # status letter in the index (staged), " " when none
    worktree: str   # status letter in the working tree, "?" for untracked
    orig_path: str = ""

    @property
    def code(self):
        """One letter for the UI: A(dded) M(odified) D(eleted) R(enamed) U(nmerged)."""
        if "U" in (self.index, self.worktree) or (self.index, self.worktree) in (("A", "A"), ("D", "D")):
            return "U"
        if self.worktree == "?":
            return "A"
        for c in (self.index, self.worktree):
            if c in "RDAM" and c != " ":
                return c
        return "M"

    @property
    def kind(self):
        return file_kind(self.path)


@dataclass
class Commit:
    sha: str
    parents: list
    author: str
    email: str
    time: int
    refs: list
    subject: str
    files: list = field(default_factory=list)


@dataclass
class BranchInfo:
    name: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    extra: dict = field(default_factory=dict)


def file_kind(path):
    lower = path.lower()
    if ".gdb/" in lower:
        return "vector"
    ext = os.path.splitext(lower)[1]
    if ext in PROJECT_EXT:
        return "project"
    if ext in STYLE_EXT:
        return "style"
    if ext in VECTOR_EXT:
        return "vector"
    if ext in RASTER_EXT:
        return "raster"
    return "other"


SHP_SIDECARS = [".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx"]
# files that are opened as a vector layer themselves (not sidecars or plain tables)
VECTOR_MAIN_EXT = {".gpkg", ".shp", ".geojson", ".json", ".kml", ".gml", ".gpx", ".sqlite",
                   ".fgb", ".tab", ".mif", ".dxf", ".parquet"}


def vector_main_file(path):
    """The file to open as a layer for path (a .dbf maps to its .shp), or None."""
    base, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext in SHP_SIDECARS:
        return base + ".shp"
    return path if ext in VECTOR_MAIN_EXT else None


RASTER_MAIN_EXT = {".tif", ".tiff", ".img", ".jp2", ".j2k", ".ecw", ".asc", ".sid", ".dem",
                   ".hgt", ".nc", ".bil", ".png", ".jpg", ".jpeg"}
RASTER_SIDECARS = [".tfw", ".tifw", ".wld", ".jgw", ".pgw", ".prj", ".hdr"]


def raster_main_file(path):
    return path if os.path.splitext(path)[1].lower() in RASTER_MAIN_EXT else None


def dataset_members(path):
    """All files that make up the dataset stored at path."""
    base, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext == ".shp":
        return [path] + [base + e for e in SHP_SIDECARS]
    if ext in RASTER_MAIN_EXT:
        return [path, path + ".ovr"] + [base + e for e in RASTER_SIDECARS]
    return [path]


def git_available():
    return shutil.which("git") is not None


def _env():
    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def run_git(args, cwd, check=True, timeout=600, out_file=None):
    """Run git and return its output as text; with out_file, stream stdout there instead."""
    try:
        if out_file:
            with open(out_file, "wb") as f:
                proc = subprocess.run(["git", *args], cwd=cwd, env=_env(), stdout=f,
                                      stderr=subprocess.PIPE, timeout=timeout, stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise GitError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out") from e
    out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or out.strip()
        raise GitError(err or f"git {args[0]} failed ({proc.returncode})")
    return out


def find_repo_root(path):
    """Top folder of the repository containing path, or None."""
    if not path or not os.path.isdir(path):
        return None
    try:
        return os.path.normpath(run_git(["rev-parse", "--show-toplevel"], path).strip())
    except GitError:
        return None


def clone(url, dest):
    parent = os.path.dirname(os.path.abspath(dest)) or "."
    run_git(["clone", url, dest], parent, timeout=3600)
    repo = GitRepo(dest)
    if repo.lfs_available():
        repo.git("lfs", "pull", check=False, timeout=3600)
    return repo


class GitRepo:
    def __init__(self, path):
        self.path = os.path.abspath(path)

    def git(self, *args, check=True, timeout=600, out_file=None):
        return run_git(list(args), self.path, check=check, timeout=timeout, out_file=out_file)

    # --- setup ---------------------------------------------------------
    def is_repo(self):
        return find_repo_root(self.path) is not None

    def lfs_available(self):
        try:
            self.git("lfs", "version")
            return True
        except GitError:
            return False

    def init(self, branch="main", use_lfs=True):
        """Make the folder a repository with geodata-aware .gitignore/.gitattributes."""
        os.makedirs(self.path, exist_ok=True)
        if not self.is_repo():
            self.git("init", "-b", branch)
        self._append_lines(".gitignore", DEFAULT_IGNORE)
        if use_lfs and self.lfs_available():
            self.setup_lfs()
        return self

    def setup_lfs(self):
        self.git("lfs", "install", "--local")
        self._append_lines(".gitattributes", [f"{p} filter=lfs diff=lfs merge=lfs -text"
                                              for p in LFS_PATTERNS])

    def _append_lines(self, name, lines):
        path = os.path.join(self.path, name)
        existing = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                existing = f.read().splitlines()
        new = [ln for ln in lines if ln not in existing]
        if new:
            with open(path, "a", encoding="utf-8") as f:
                if existing and existing[-1] != "":
                    f.write("\n")
                f.write("\n".join(new) + "\n")

    def identity(self):
        name = self.git("config", "user.name", check=False).strip()
        email = self.git("config", "user.email", check=False).strip()
        return name, email

    def set_identity(self, name, email, global_=False):
        scope = ["--global"] if global_ else []
        self.git("config", *scope, "user.name", name)
        self.git("config", *scope, "user.email", email)

    # --- state ---------------------------------------------------------
    def status(self):
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        changes = []
        parts = out.split("\0")
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if len(entry) < 4:
                continue
            x, y, path = entry[0], entry[1], entry[3:]
            orig = ""
            if x in "RC":
                orig = parts[i]
                i += 1
            changes.append(FileChange(path, x, y, orig))
        return changes

    def has_commits(self):
        return self.git("rev-parse", "--verify", "-q", "HEAD", check=False).strip() != ""

    def branch_info(self):
        out = self.git("status", "--porcelain=v2", "--branch", "-z")
        info = BranchInfo()
        for entry in out.split("\0"):
            if not entry.startswith("# branch."):
                continue
            key, _, value = entry[2:].partition(" ")
            if key == "branch.head":
                info.detached = value == "(detached)"
                info.name = value
            elif key == "branch.upstream":
                info.upstream = value
            elif key == "branch.ab":
                a, b = value.split()
                info.ahead, info.behind = int(a), -int(b)
        return info

    def branches(self):
        out = self.git("branch", "--format=%(refname:short)")
        return [b for b in out.splitlines() if b]

    def remotes(self):
        return [r for r in self.git("remote").splitlines() if r]

    def remote_url(self, name="origin"):
        return self.git("remote", "get-url", name, check=False).strip()

    def set_remote(self, url, name="origin"):
        if name in self.remotes():
            self.git("remote", "set-url", name, url)
        else:
            self.git("remote", "add", name, url)

    def log(self, limit=500, all_refs=True, path=None):
        """Newest first. Each commit lists the files it touched.

        With path, only commits touching that file/folder are returned and their
        parents are rewritten to the previous such commit, so the graph stays connected.
        """
        if not self.has_commits():
            return []
        fmt = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%D%x1f%s"
        args = ["-c", "core.quotepath=off", "log", f"--max-count={limit}", "--date-order",
                f"--format={fmt}", "--name-only"]
        if all_refs:
            args.append("--all")
        if path:
            args += ["--parents", "--", path]
        commits = []
        for rec in self.git(*args).split("\x1e"):
            lines = rec.split("\n")
            if not lines[0]:
                continue
            sha, parents, an, ae, at, refs, subject = lines[0].split("\x1f")
            commits.append(Commit(sha, parents.split(), an, ae, int(at),
                                  [r.strip() for r in refs.split(",") if r.strip()], subject,
                                  [ln for ln in lines[1:] if ln]))
        return commits

    def commit_files(self, sha):
        """[(status letter, path)] changed by a commit (against its first parent)."""
        base = ["-c", "core.quotepath=off"]
        if self.git("rev-parse", "--verify", "-q", f"{sha}^1", check=False).strip():
            out = self.git(*base, "diff", "--name-status", "-z", "--no-renames", f"{sha}^1", sha)
        else:
            out = self.git(*base, "show", "--format=", "--name-status", "-z", "--no-renames", sha)
        parts = [p for p in out.split("\0") if p]
        return list(zip(parts[0::2], parts[1::2]))

    # --- file versions -------------------------------------------------
    def cache_dir(self, *parts):
        """Folder inside .git for exported versions and diff results (never committed)."""
        git_dir = self.git("rev-parse", "--absolute-git-dir").strip()
        path = os.path.join(git_dir, "geogit", *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def cached_version(self, rev, path):
        """export_version into the cache, reusing an earlier export of the same commit."""
        sha = self.git("rev-parse", "--verify", "-q", f"{rev}^{{commit}}", check=False).strip()
        if not sha:
            return None
        dest = self.cache_dir("versions", sha[:12])
        target = os.path.join(dest, path)
        done = target + ".geogit-ok"     # an interrupted export is never reused
        if os.path.exists(target) and os.path.exists(done):
            return target
        out = self.export_version(sha, path, dest)
        if out:
            open(done, "w").close()
        return out

    def first_parent(self, sha):
        return self.git("rev-parse", "--verify", "-q", f"{sha}^1", check=False).strip() or None

    def file_exists(self, rev, path):
        try:
            self.git("cat-file", "-e", f"{rev}:{path}", timeout=60)
            return True
        except GitError:
            return False

    def export_version(self, rev, path, dest_dir):
        """Write path as it was in rev into dest_dir (LFS content resolved).

        A shapefile brings its sidecar files along. Returns the exported path,
        or None when the file does not exist in rev.
        """
        if not rev or not self.file_exists(rev, path):
            return None
        for member in dataset_members(path):
            if member != path and not self.file_exists(rev, member):
                continue
            target = os.path.join(dest_dir, member)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.git("cat-file", "--filters", f"{rev}:{member}", out_file=target, timeout=3600)
        return os.path.join(dest_dir, path)

    # --- actions -------------------------------------------------------
    def commit(self, message, paths=None):
        """Stage the given paths (all changes when None) and commit them."""
        if not message.strip():
            raise GitError("empty commit message")
        if paths is None:
            self.git("add", "-A")
        else:
            if not paths:
                raise GitError("nothing selected")
            self.git("add", "-A", "--", *paths)
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def discard(self, paths):
        """Throw away working-tree changes; untracked files are deleted."""
        tracked, untracked = [], []
        for ch in self.status():
            if ch.path in paths:
                (untracked if ch.worktree == "?" else tracked).append(ch.path)
        if tracked:
            if self.has_commits():
                self.git("restore", "--staged", "--worktree", "--source=HEAD", "--", *tracked)
            else:
                self.git("rm", "--cached", "-q", "--", *tracked)
        for p in untracked:
            full = os.path.join(self.path, p)
            if os.path.isfile(full):
                os.remove(full)

    def fetch(self):
        self.git("fetch", "--prune", timeout=3600)

    def pull(self):
        info = self.branch_info()
        args = ["pull", "--rebase=false", "--no-edit"]
        if not info.upstream and not info.detached:
            args += ["origin", info.name]
        self.git(*args, timeout=3600)

    def push(self):
        info = self.branch_info()
        if info.detached:
            raise GitError("not on a branch")
        if info.upstream:
            self.git("push", timeout=3600)
        else:
            self.git("push", "-u", "origin", info.name, timeout=3600)

    def create_branch(self, name, checkout=True):
        self.git("switch", "-c", name) if checkout else self.git("branch", name)

    def switch(self, name):
        self.git("switch", name)
