"""Thin wrapper around the git command line for a QGIS project folder.

No QGIS imports here, so the module runs (and is tested) outside QGIS.
"""

import json
import os
import re
import shutil
import subprocess
import sys
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
class Lock:
    id: str
    path: str
    owner: str
    locked_at: str
    ours: bool = None    # None when the server could not tell


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


GIT_ENV = {"LC_ALL": "C", "LANG": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
FLATPAK_PERMISSION_CMD = "flatpak override --user --talk-name=org.freedesktop.Flatpak {app}"


def in_flatpak():
    return os.path.exists("/.flatpak-info")


def flatpak_app_id():
    return os.environ.get("FLATPAK_ID", "org.qgis.qgis")


_git_mode = None     # "local" | "host" | "flatpak-no-permission" | "missing"


def git_mode():
    """How git can be reached from this QGIS.

    A Flatpak QGIS has no git inside its sandbox; it can run the computer's
    git through `flatpak-spawn --host`, but only when the Flatpak is allowed
    to talk to org.freedesktop.Flatpak.
    """
    global _git_mode
    if _git_mode is None:
        if shutil.which("git"):
            _git_mode = "local"
        elif in_flatpak() and shutil.which("flatpak-spawn"):
            def host_ok(*argv):
                try:
                    return subprocess.run(["flatpak-spawn", "--host", *argv], capture_output=True,
                                          timeout=20, stdin=subprocess.DEVNULL).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    return False
            if not host_ok("true"):
                _git_mode = "flatpak-no-permission"
            else:
                _git_mode = "host" if host_ok("git", "--version") else "missing"
        else:
            _git_mode = "missing"
    return _git_mode


def git_available():
    return git_mode() in ("local", "host")


def _env():
    env = dict(os.environ)
    env.update(GIT_ENV)
    return env


def _command(args, cwd):
    """The argv to run git with args in cwd (through the host when inside a Flatpak)."""
    if git_mode() == "host":
        return ["flatpak-spawn", "--host", f"--directory={os.path.abspath(cwd)}",
                *[f"--env={k}={v}" for k, v in GIT_ENV.items()], "git", *args]
    return ["git", *args]


def run_host(argv, timeout=60):
    """Run a command on the computer (outside the Flatpak sandbox when QGIS is a Flatpak).

    Returns (exit code, stdout, stderr); exit code 127 when it cannot be started.
    """
    if in_flatpak() and git_mode() != "local":
        if git_mode() == "flatpak-no-permission":
            return 127, "", "no permission to run commands outside the Flatpak"
        argv = ["flatpak-spawn", "--host", *argv]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


# package manager -> (command that installs git-lfs as root, what to show the user)
_LFS_PACKAGES = [
    ("apt-get", ["apt-get", "install", "-y", "git-lfs"], "sudo apt install git-lfs"),
    ("dnf", ["dnf", "install", "-y", "git-lfs"], "sudo dnf install git-lfs"),
    ("pacman", ["pacman", "-S", "--noconfirm", "git-lfs"], "sudo pacman -S git-lfs"),
    ("zypper", ["zypper", "--non-interactive", "install", "git-lfs"], "sudo zypper install git-lfs"),
]


def lfs_install_plan():
    """How git-lfs can be installed on this computer.

    {"command": text to show/copy, "argv": what to run or None when it cannot be
    done from QGIS, "needs_password": bool}.
    """
    if sys.platform == "win32":
        return {"command": "winget install GitHub.GitLFS", "argv": None, "needs_password": False}
    if sys.platform == "darwin":
        ok = run_host(["sh", "-c", "command -v brew"])[0] == 0
        return {"command": "brew install git-lfs", "argv": ["brew", "install", "git-lfs"] if ok else None,
                "needs_password": False}
    found = run_host(["sh", "-c", "for c in apt-get dnf pacman zypper pkexec; do command -v $c >/dev/null && echo $c; done"])
    have = set(found[1].split()) if found[0] == 0 else set()
    for tool, argv, shown in _LFS_PACKAGES:
        if tool in have:
            # pkexec asks for the password in the system's own dialog; QGIS never sees it
            return {"command": shown, "argv": ["pkexec", *argv] if "pkexec" in have else None,
                    "needs_password": True}
    return {"command": "https://git-lfs.com", "argv": None, "needs_password": False}


def install_lfs(plan):
    """Run the plan's install command (raises GitError with its output when it fails)."""
    code, out, err = run_host(plan["argv"], timeout=900)
    if code != 0:
        if code in (126, 127) and plan["argv"][0] == "pkexec":
            raise GitError("cancelled or not authorised")
        raise GitError((err or out).strip()[-800:] or f"exit code {code}")
    return out


def run_git(args, cwd, check=True, timeout=600, out_file=None):
    """Run git and return its output as text; with out_file, stream stdout there instead."""
    if git_mode() == "flatpak-no-permission":
        raise GitError("git is not reachable from this Flatpak QGIS; allow it with: "
                       + FLATPAK_PERMISSION_CMD.format(app=flatpak_app_id()))
    cmd = _command(args, cwd)
    try:
        if out_file:
            with open(out_file, "wb") as f:
                proc = subprocess.run(cmd, cwd=cwd, env=_env(), stdout=f,
                                      stderr=subprocess.PIPE, timeout=timeout, stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True,
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


def clone(url, dest, config=None):
    """Clone url into dest with real LFS content, not pointer files.

    config: extra git settings (e.g. a credential helper) used for the clone
    and kept in the new repository.
    """
    config = config or {}
    parent = os.path.dirname(os.path.abspath(dest)) or "."
    opts = [x for k, v in config.items() for x in ("-c", f"{k}={v}")]
    run_git([*opts, "clone", url, dest], parent, timeout=3600)
    repo = GitRepo(dest)
    for k, v in config.items():
        repo.git("config", k, v)
    if repo.lfs_available():
        # the LFS filters may only be set up per repository, so a fresh clone
        # can hold pointer files: switch LFS on here and fetch the content
        repo.enable_lfs()
        if repo.has_commits():          # an empty repository has nothing to fetch
            repo.git("lfs", "pull", timeout=3600)
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
        self.enable_lfs()
        self._append_lines(".gitattributes", [f"{p} filter=lfs diff=lfs merge=lfs -text"
                                              for p in LFS_PATTERNS])

    def enable_lfs(self):
        """LFS hooks and filters for this repository, with lock checks that really stop a push.

        Without lfs.locksverify git-lfs only warns when you push a file that
        someone else has locked.
        """
        self.git("lfs", "install", "--local")
        self.git("config", "lfs.locksverify", "true")

    def ensure_lfs(self):
        """Switch LFS on for a repository that uses it but was cloned without it.

        Returns True when it had to, so the caller can fetch the real content
        (`lfs_pull`) in place of pointer files.
        """
        if not self.lfs_available():
            return False
        try:
            with open(os.path.join(self.path, ".gitattributes"), encoding="utf-8") as f:
                uses_lfs = "filter=lfs" in f.read()
        except OSError:
            return False
        if not uses_lfs or self.git("config", "--get", "lfs.locksverify", check=False).strip() == "true":
            return False
        self.enable_lfs()
        return True

    def lfs_pull(self):
        if self.has_commits():
            self.git("lfs", "pull", timeout=3600)

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
        """Get updates; returns the conflicted paths ([] when it merged cleanly)."""
        info = self.branch_info()
        args = ["pull", "--rebase=false", "--no-edit"]
        if not info.upstream and not info.detached:
            args += ["origin", info.name]
        return self._merging(args)

    # --- merge and conflicts -------------------------------------------
    def merge(self, branch):
        """Merge branch into the current one; returns the conflicted paths."""
        return self._merging(["merge", "--no-ff", "--no-edit", branch])

    def _merging(self, args):
        try:
            self.git(*args, timeout=3600)
            return []
        except GitError:
            conflicts = self.conflicts() if self.in_merge() else []
            if not conflicts:
                raise
            return conflicts

    def in_merge(self):
        return self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).strip() != ""

    def merge_head_name(self):
        """Short name of what is being merged in: "survey", or "main (server)" for a pull."""
        path = os.path.join(self.git("rev-parse", "--absolute-git-dir").strip(), "MERGE_MSG")
        try:
            with open(path, encoding="utf-8") as f:
                line = f.readline().strip()
        except OSError:
            return ""
        m = re.match(r"Merge (?:remote-tracking )?branch '([^']+)'(?: of (.+?))?(?: into .*)?$", line)
        if not m:
            return line
        name, source = m.group(1), m.group(2)
        if source:
            source = os.path.basename(source.rstrip("/"))
            source = source[:-4] if source.endswith(".git") else source
            return f"{name} ({source})"
        return name

    def conflicts(self):
        out = self.git("-c", "core.quotepath=off", "diff", "--name-only", "-z", "--diff-filter=U")
        return sorted({p for p in out.split("\0") if p})

    def export_stage(self, stage, path, dest_dir):
        """Write one side of a conflict: 1 = common base, 2 = mine (HEAD), 3 = theirs.

        Returns the written path, or None when that side has no such file.
        """
        rev = f":{stage}"
        if not self.file_exists(rev, path):
            return None
        for member in dataset_members(path):
            if member != path and not self.file_exists(rev, member):
                # an unconflicted sidecar sits at stage 0
                if not self.file_exists(":0", member):
                    continue
                src = f":0:{member}"
            else:
                src = f"{rev}:{member}"
            target = os.path.join(dest_dir, member)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.git("cat-file", "--filters", src, out_file=target, timeout=3600)
        return os.path.join(dest_dir, path)

    def resolve(self, path, side):
        """Settle a conflicted file with one whole side: "ours" or "theirs"."""
        if self.file_exists(":2" if side == "ours" else ":3", path):
            self.git("checkout", f"--{side}", "--", path)
            self.git("add", "--", path)
        else:
            self.git("rm", "-q", "--", path)

    def mark_resolved(self, path):
        """The file in the working folder is the agreed result."""
        if os.path.exists(os.path.join(self.path, path)):
            self.git("add", "--", path)
        else:
            self.git("rm", "-q", "--cached", "--", path)

    def abort_merge(self):
        self.git("merge", "--abort")

    def finish_merge(self, message=None):
        if self.conflicts():
            raise GitError("unresolved conflicts")
        self.git("commit", *(["-m", message] if message else ["--no-edit"]))
        return self.git("rev-parse", "HEAD").strip()

    def push(self):
        """Publish the current branch together with its annotated tags."""
        info = self.branch_info()
        if info.detached:
            raise GitError("not on a branch")
        if info.upstream:
            self.git("push", "--follow-tags", timeout=3600)
        else:
            self.git("push", "--follow-tags", "-u", "origin", info.name, timeout=3600)

    # --- tags, restore, revert -----------------------------------------
    def tags(self):
        """{tag name: commit sha}."""
        out = self.git("tag", "--list", "--format=%(refname:short)%09%(*objectname)%(objectname)", check=False)
        tags = {}
        for line in out.splitlines():
            name, _, sha = line.partition("\t")
            # annotated tags print the commit (dereferenced) first, then the tag object
            tags[name] = sha[:40]
        return tags

    def add_tag(self, name, sha, message=""):
        """Annotated tag, so it carries author and date and travels with push."""
        try:
            self.git("check-ref-format", f"refs/tags/{name}")
        except GitError:
            raise GitError(f"bad tag name: {name}") from None
        self.git("tag", "-a", name, sha, "-m", message or name)

    def restore_file(self, sha, path):
        """Put path (and a shapefile's sidecars) back as it was in sha; shows up as a change."""
        members = [m for m in dataset_members(path) if self.file_exists(sha, m)]
        if not members:
            raise GitError(f"{path} does not exist in {sha[:8]}")
        self.git("restore", f"--source={sha}", "--worktree", "--", *members)

    def revert(self, sha):
        """New version that undoes sha. Gives up cleanly if it would conflict."""
        if len(self.git("rev-list", "--parents", "-n", "1", sha).split()) > 2:
            raise GitError("merge versions cannot be undone this way")
        try:
            self.git("revert", "--no-edit", sha)
        except GitError:
            self.git("revert", "--abort", check=False)
            raise
        return self.git("rev-parse", "HEAD").strip()

    # --- LFS file locks ------------------------------------------------
    @staticmethod
    def _lock(d, ours=None):
        return Lock(str(d.get("id", "")), d.get("path", ""), (d.get("owner") or {}).get("name", ""),
                    d.get("locked_at", ""), ours)

    def locks(self):
        """Locks on the server; ours/theirs is filled when the server can verify them."""
        try:
            data = json.loads(self.git("lfs", "locks", "--verify", "--json", timeout=120) or "{}")
            return ([self._lock(d, True) for d in data.get("ours") or []]
                    + [self._lock(d, False) for d in data.get("theirs") or []])
        except (GitError, ValueError, AttributeError):
            data = json.loads(self.git("lfs", "locks", "--json", timeout=120) or "[]")
            return [self._lock(d) for d in data]

    def lock(self, path):
        data = json.loads(self.git("lfs", "lock", "--json", path, timeout=120))
        if isinstance(data, list):      # some git-lfs versions wrap the lock in a list
            data = data[0] if data else {}
        return self._lock(data, True)

    def unlock(self, path, force=False):
        self.git("lfs", "unlock", *(["--force"] if force else []), path, timeout=120)

    def create_branch(self, name, checkout=True):
        self.git("switch", "-c", name) if checkout else self.git("branch", name)

    def switch(self, name):
        self.git("switch", name)
