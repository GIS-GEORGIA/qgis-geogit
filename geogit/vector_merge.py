"""Three-way, feature-level merge of a GeoPackage (no QGIS imports).

Both sides are compared with the common base by FID. Changes to different
features merge on their own, and so do edits of different attributes of the
same feature. What is left — the same attribute or geometry changed in two
ways, or a feature edited on one side and deleted on the other — comes back
as conflicts; the caller picks "ours" or "theirs" for each and merges again.
"""

import shutil
from dataclasses import dataclass, field

from osgeo import gdal, ogr

try:
    from .vector_diff import FID_DRIVERS, Feature, _read, diff_layers
except ImportError:      # imported as a top-level module by the unit tests
    from vector_diff import FID_DRIVERS, Feature, _read, diff_layers


class MergeUnsupported(Exception):
    """The file cannot be merged feature by feature (format, layers or fields differ)."""


@dataclass
class FeatureConflict:
    layer: str
    fid: int
    kind: str              # both_modified | ours_deleted | theirs_deleted
    ours: Feature = None   # None when that side deleted it
    theirs: Feature = None
    fields: list = field(default_factory=list)   # attributes in conflict ("geometry" too)


@dataclass
class MergeReport:
    applied: int = 0                 # changes from theirs written into the result
    merged_fields: int = 0           # features where both sides' edits were combined
    renumbered: int = 0              # features added on both sides with the same FID
    conflicts: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.conflicts


def _combine(base, ours, theirs, names):
    """Merge two edits of one feature; returns (Feature, conflicting field names)."""
    attrs, bad = dict(ours.attrs), []
    for n in names:
        b, o, t = base.attrs.get(n), ours.attrs.get(n), theirs.attrs.get(n)
        if o == t or t == b:
            continue
        if o == b:
            attrs[n] = t
        else:
            bad.append(n)
    wkb = ours.wkb
    if ours.wkb != theirs.wkb and theirs.wkb != base.wkb:
        if ours.wkb == base.wkb:
            wkb = theirs.wkb
        else:
            bad.append("geometry")
    return Feature(ours.fid, wkb, attrs), bad


def _plan_layer(name, base, ours, theirs, choices, report):
    """[(action, Feature)] to apply on top of ours: set | delete | add | add_new_fid."""
    if (ours.fields != theirs.fields) or (base.fields != ours.fields):
        raise MergeUnsupported(f"fields of layer {name} changed")
    d_ours, d_theirs = diff_layers(base, ours), diff_layers(base, theirs)
    o_mod = {m.old.fid: m for m in d_ours.modified}
    o_del = {f.fid for f in d_ours.deleted}
    o_add = {f.fid: f for f in d_ours.added}
    names = list(ours.fields)
    plan = []

    def conflict(fid, kind, o, t, fields_=()):
        choice = choices.get((name, fid))
        if choice is None:
            report.conflicts.append(FeatureConflict(name, fid, kind, o, t, list(fields_)))
        return choice

    for m in d_theirs.modified:
        fid = m.old.fid
        if fid in o_del:
            if conflict(fid, "ours_deleted", None, m.new) == "theirs":
                plan.append(("add", m.new))
        elif fid in o_mod:
            mine = o_mod[fid].new
            merged, bad = _combine(m.old, mine, m.new, names)
            if bad:
                pick = conflict(fid, "both_modified", mine, m.new, bad)
                if pick == "theirs":
                    plan.append(("set", m.new))
            elif merged.attrs != mine.attrs or merged.wkb != mine.wkb:
                report.merged_fields += 1
                plan.append(("set", merged))
        else:
            plan.append(("set", m.new))
    for f in d_theirs.deleted:
        if f.fid in o_mod:
            if conflict(f.fid, "theirs_deleted", o_mod[f.fid].new, None) == "theirs":
                plan.append(("delete", f))
        elif f.fid not in o_del:
            plan.append(("delete", f))
    for f in d_theirs.added:
        mine = o_add.get(f.fid)
        if mine is None:
            plan.append(("add", f))
        elif mine.wkb != f.wkb or mine.attrs != f.attrs:
            report.renumbered += 1
            plan.append(("add_new_fid", f))
    return plan


def _apply(path, layer_name, plan, report):
    ds = gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_UPDATE)
    if ds is None:
        raise MergeUnsupported(f"cannot open {path} for writing")
    lyr = ds.GetLayerByName(layer_name)
    defn = lyr.GetLayerDefn()
    lyr.StartTransaction()
    for action, f in plan:
        if action == "delete":
            lyr.DeleteFeature(f.fid)
            report.applied += 1
            continue
        feat = ogr.Feature(defn)
        if action != "add_new_fid":
            feat.SetFID(f.fid)
        for k, v in f.attrs.items():
            if v is None:
                feat.SetFieldNull(k)
            else:
                feat.SetField(k, v)
        if f.wkb:
            feat.SetGeometry(ogr.CreateGeometryFromWkb(f.wkb))
        err = lyr.SetFeature(feat) if action == "set" else lyr.CreateFeature(feat)
        if err != 0:
            lyr.RollbackTransaction()
            raise MergeUnsupported(f"could not write feature {f.fid} in {layer_name}")
        report.applied += 1
    lyr.CommitTransaction()
    ds = None


def merge_files(base_path, ours_path, theirs_path, out_path, choices=None):
    """Write the merge of theirs into ours to out_path; returns a MergeReport.

    choices maps (layer, fid) -> "ours" | "theirs" for conflicts found earlier.
    When report.conflicts is not empty, out_path still holds ours plus every
    change that merged cleanly.
    """
    choices = choices or {}
    base, ours, theirs = _read(base_path), _read(ours_path), _read(theirs_path)
    if not (base and ours and theirs):
        raise MergeUnsupported("the file is missing on one side")
    if not all(lyr.driver in FID_DRIVERS for side in (base, ours, theirs) for lyr in side.values()):
        raise MergeUnsupported("feature merge needs GeoPackage or SQLite")
    if set(base) != set(ours) or set(ours) != set(theirs):
        raise MergeUnsupported("layers were added or removed")

    report = MergeReport()
    plans = {n: _plan_layer(n, base[n], ours[n], theirs[n], choices, report) for n in ours}
    if out_path != ours_path:
        shutil.copyfile(ours_path, out_path)
    for n, plan in plans.items():
        if plan:
            _apply(out_path, n, plan, report)
    return report
