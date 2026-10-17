"""Feature-level comparison of two versions of a vector file with GDAL/OGR.

No QGIS imports, so it runs (and is tested) outside QGIS. Features are matched
by a stable key: the FID in GeoPackage/SQLite, otherwise an id-like field with
unique values. Without one, unchanged features are matched by content and the
rest are paired by row number.
"""

import os
from collections import defaultdict
from dataclasses import dataclass, field

from osgeo import gdal, ogr

FID_DRIVERS = {"GPKG", "SQLite"}
KEY_FIELDS = ("uuid", "guid", "globalid", "fid", "id", "objectid", "gid", "ogc_fid", "osm_id")


@dataclass
class Feature:
    fid: int
    wkb: bytes
    attrs: dict


@dataclass
class Modified:
    old: Feature
    new: Feature
    fields: list          # names of attributes whose value changed
    geometry: bool        # geometry changed


@dataclass
class LayerDiff:
    name: str
    geom_type: int = ogr.wkbUnknown
    srs_wkt: str = ""
    fields: list = field(default_factory=list)       # attribute names, new version order
    key: str = ""                                     # "FID", a field name, or "" (content)
    added: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    fields_added: list = field(default_factory=list)
    fields_removed: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.added or self.deleted or self.modified or self.fields_added or self.fields_removed)


@dataclass
class _Layer:
    name: str
    driver: str
    geom_type: int
    srs_wkt: str
    fields: list
    features: list


def _value(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _read(path):
    """{layer name: _Layer} for a file; {} when path is None or missing."""
    if not path or not os.path.exists(path):
        return {}
    ds = gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_READONLY)
    if ds is None:
        raise ValueError(f"cannot open {path}: {gdal.GetLastErrorMsg()}")
    driver = ds.GetDriver().ShortName
    layers = {}
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        defn = lyr.GetLayerDefn()
        names = [defn.GetFieldDefn(j).GetName() for j in range(defn.GetFieldCount())]
        srs = lyr.GetSpatialRef()
        feats = []
        for f in lyr:
            g = f.GetGeometryRef()
            feats.append(Feature(f.GetFID(), bytes(g.ExportToIsoWkb()) if g else b"",
                                 {n: _value(f.GetField(n)) for n in names}))
        layers[lyr.GetName()] = _Layer(lyr.GetName(), driver, lyr.GetGeomType(),
                                       srs.ExportToWkt() if srs else "", names, feats)
    ds = None
    return layers


def _pick_key(old, new):
    if old and new and old.driver in FID_DRIVERS and new.driver in FID_DRIVERS:
        return "FID"
    sides = [s for s in (old, new) if s]
    common = set.intersection(*(set(s.fields) for s in sides))
    lower = {n.lower(): n for n in common}
    for cand in KEY_FIELDS:
        name = lower.get(cand)
        if name is None:
            continue
        ok = True
        for s in sides:
            values = [f.attrs.get(name) for f in s.features]
            if None in values or len(set(values)) != len(values):
                ok = False
                break
        if ok:
            return name
    return ""


def _compare(old, new, fields):
    changed = [n for n in fields if old.attrs.get(n) != new.attrs.get(n)]
    return changed, old.wkb != new.wkb


def _diff_by_key(diff, old_feats, new_feats, key_of, fields):
    old_map = {key_of(f): f for f in old_feats}
    new_map = {key_of(f): f for f in new_feats}
    for k, f in new_map.items():
        if k not in old_map:
            diff.added.append(f)
        else:
            changed, geom = _compare(old_map[k], f, fields)
            if changed or geom:
                diff.modified.append(Modified(old_map[k], f, changed, geom))
    diff.deleted = [f for k, f in old_map.items() if k not in new_map]


def _diff_by_content(diff, old_feats, new_feats, fields):
    def sig(f):
        return f.wkb, tuple((n, repr(f.attrs.get(n))) for n in fields)

    pool = defaultdict(list)
    for f in old_feats:
        pool[sig(f)].append(f)
    new_only = []
    for f in new_feats:
        bucket = pool.get(sig(f))
        if bucket:
            bucket.pop()
        else:
            new_only.append(f)
    old_only = {f.fid: f for b in pool.values() for f in b}
    for f in new_only:
        o = old_only.pop(f.fid, None)
        if o is None:
            diff.added.append(f)
        else:
            changed, geom = _compare(o, f, fields)
            diff.modified.append(Modified(o, f, changed, geom))
    diff.deleted = sorted(old_only.values(), key=lambda f: f.fid)


def diff_layers(old, new):
    """LayerDiff between two _Layer objects (either may be None)."""
    ref = new or old
    diff = LayerDiff(ref.name, ref.geom_type, ref.srs_wkt or (old.srs_wkt if old else ""), list(ref.fields))
    old_fields = old.fields if old else []
    new_fields = new.fields if new else []
    if old and new:
        diff.fields_added = [n for n in new_fields if n not in old_fields]
        diff.fields_removed = [n for n in old_fields if n not in new_fields]
    common = [n for n in new_fields if n in old_fields] if old and new else list(ref.fields)
    old_feats = old.features if old else []
    new_feats = new.features if new else []

    diff.key = _pick_key(old, new)
    if diff.key == "FID":
        _diff_by_key(diff, old_feats, new_feats, lambda f: f.fid, common)
    elif diff.key:
        _diff_by_key(diff, old_feats, new_feats, lambda f: f.attrs.get(diff.key), common)
    else:
        _diff_by_content(diff, old_feats, new_feats, common)
    return diff


def diff_files(old_path, new_path):
    """[LayerDiff] for every layer in either version; a missing side counts as empty."""
    old_layers, new_layers = _read(old_path), _read(new_path)
    if len(old_layers) == 1 and len(new_layers) == 1:
        # single-layer formats (GeoJSON, shapefile) name the layer after the file
        return [diff_layers(next(iter(old_layers.values())), next(iter(new_layers.values())))]
    names = list(new_layers) + [n for n in old_layers if n not in new_layers]
    return [diff_layers(old_layers.get(n), new_layers.get(n)) for n in names]
