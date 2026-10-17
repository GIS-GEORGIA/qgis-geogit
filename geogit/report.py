"""HTML report of what one version changed, file by file (no QGIS imports)."""

import html
import os
from datetime import datetime

try:
    from . import git_backend as gb
    from . import project_diff, raster_diff, vector_diff
    from .i18n import t
except ImportError:      # imported as a top-level module by the unit tests
    import git_backend as gb
    import project_diff
    import raster_diff
    import vector_diff
    from i18n import t


def _vector(old, new):
    diffs = vector_diff.diff_files(old, new)
    a = sum(len(d.added) for d in diffs)
    m = sum(len(d.modified) for d in diffs)
    d_ = sum(len(d.deleted) for d in diffs)
    text = t("rp_vector", a, m, d_)
    fields = [f for d in diffs for f in d.fields_added]
    if fields:
        text += " · " + t("rp_new_fields", ", ".join(fields))
    return text


def _raster(repo, old, new, key):
    out = repo.cache_dir("diffs", key)
    r = raster_diff.compare(old, new, out)
    if not r.changed:
        return t("rp_raster_same")
    text = t("rp_raster", f"{r.changed_pixels:,}".replace(",", " "), r.changed_percent)
    if r.meta_changes:
        text += " · " + ", ".join(t("meta_" + k) for k in r.meta_changes)
    return text


def _project(old, new):
    changes = project_diff.diff_files(old, new)
    by = {}
    for c in changes:
        by[c.section] = by.get(c.section, 0) + 1
    if not by:
        return t("rp_project_same")
    return ", ".join(f"{t('pd_sec_' + s)}: {n}" for s, n in by.items())


def file_summary(repo, sha, parent, path):
    """Short description of how path changed between parent and sha."""
    old = repo.cached_version(parent, path) if parent else None
    new = repo.cached_version(sha, path)
    try:
        if gb.file_kind(path) == "project":
            return _project(old, new)
        if gb.raster_main_file(path):
            if old and new:
                return _raster(repo, old, new, f"{(parent or '')[:12]}_{sha[:12]}/{path.replace('/', '__')}")
            return ""
        if gb.vector_main_file(path) == path:
            return _vector(old, new)
    except Exception as e:      # a damaged or unreadable file must not stop the report
        return f"– ({e})"
    return ""


def build_report(repo, sha):
    """HTML text describing the version sha against its first parent."""
    commit = next((c for c in repo.log(limit=100000) if c.sha == sha), None)
    if commit is None:
        raise gb.GitError(f"unknown version {sha}")
    parent = repo.first_parent(sha)
    files = repo.commit_files(sha)
    shown = [(s, p) for s, p in files
             if os.path.splitext(p)[1].lower() not in gb.SHP_SIDECARS or gb.vector_main_file(p) not in
             {q for _, q in files}]
    rows = []
    for state, path in shown:
        code = state[0] if state[0] in "AMDR" else "M"
        detail = file_summary(repo, sha, parent, path)
        rows.append(f"<tr class='{code}'><td>{html.escape(t('state_' + code))}</td>"
                    f"<td>{html.escape(t('kind_' + gb.file_kind(path)))}</td>"
                    f"<td class='path'>{html.escape(path)}</td><td>{html.escape(detail)}</td></tr>")
    when = datetime.fromtimestamp(commit.time).strftime("%Y-%m-%d %H:%M")
    tags = [r[5:] for r in commit.refs if r.startswith("tag: ")]
    head = [(t("col_message"), commit.subject), (t("col_author"), f"{commit.author} <{commit.email}>"),
            (t("col_date"), when), (t("col_id"), sha), (t("rp_parent"), parent or "–")]
    if tags:
        head.append((t("rp_tags"), ", ".join(tags)))
    meta = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in head)
    return f"""<!doctype html>
<html lang="{'en' if t('lang') == 'Language' else 'ka'}"><head><meta charset="utf-8">
<title>{html.escape(t('rp_title'))} {sha[:8]}</title>
<style>
body {{ font-family: "DejaVu Sans", "Noto Sans Georgian", sans-serif; margin: 2em; color: #222; }}
h1 {{ font-size: 1.4em; }} table {{ border-collapse: collapse; margin-bottom: 1.5em; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }}
.meta th {{ background: #f3f3f3; }} .path {{ font-family: monospace; }}
tr.A td:first-child {{ color: #2ca02c; }} tr.D td:first-child {{ color: #d62728; }}
tr.M td:first-child {{ color: #e67e00; }} footer {{ color: #888; font-size: .85em; }}
</style></head><body>
<h1>{html.escape(t('rp_title'))}: {html.escape(commit.subject)}</h1>
<table class="meta">{meta}</table>
<h2>{html.escape(t('rp_files', len(shown)))}</h2>
<table><tr><th>{html.escape(t('col_state'))}</th><th>{html.escape(t('col_kind'))}</th>
<th>{html.escape(t('col_path'))}</th><th>{html.escape(t('rp_detail'))}</th></tr>
{''.join(rows)}
</table>
<footer>GeoGit · {datetime.now().strftime('%Y-%m-%d %H:%M')}</footer>
</body></html>
"""
