"""Build dist/geogit-<version>.zip for the QGIS plugin repository, after checks.

    python3 scripts/package.py
"""
import configparser
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "geogit")
REQUIRED = ["name", "qgisMinimumVersion", "description", "about", "version", "author", "email", "repository"]
SKIP_DIRS = {"__pycache__", "tests", ".git"}
SKIP_EXT = {".pyc", ".pyo", ".orig", ".rej", ".bak"}


def check():
    problems = []
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(os.path.join(PLUGIN, "metadata.txt"), encoding="utf-8")
    meta = cfg["general"]
    for key in REQUIRED:
        if not meta.get(key, "").strip():
            problems.append(f"metadata.txt: {key} is missing")
    version = meta.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        problems.append(f"metadata.txt: version {version!r} is not X.Y.Z")
    if not meta.get("changelog", "").startswith(version + " "):
        problems.append("metadata.txt: changelog does not start with the current version")
    if not os.path.exists(os.path.join(PLUGIN, meta.get("icon", "icon.svg"))):
        problems.append("icon file is missing")
    if not os.path.exists(os.path.join(PLUGIN, "LICENSE")):
        problems.append("geogit/LICENSE is missing (the plugin repository requires it)")
    for name in os.listdir(PLUGIN):
        if name.endswith(".py"):
            with open(os.path.join(PLUGIN, name), encoding="utf-8") as f:
                try:
                    compile(f.read(), name, "exec")
                except SyntaxError as e:
                    problems.append(f"{name}: {e}")
    return version, problems


def build(version):
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"geogit-{version}.zip")
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(PLUGIN):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if os.path.splitext(name)[1] in SKIP_EXT:
                    continue
                full = os.path.join(folder, name)
                z.write(full, os.path.join("geogit", os.path.relpath(full, PLUGIN)))
                count += 1
    return path, count


def main():
    version, problems = check()
    if problems:
        print("not packaged:\n  " + "\n  ".join(problems))
        return 1
    path, count = build(version)
    print(f"{path}  ({count} files, {os.path.getsize(path) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
