"""What changed between two versions of a QGIS project (.qgz / .qgs).

Reads the project XML with the standard library only (no QGIS), so it also
runs outside QGIS. Reports layers, the layer tree, project CRS and layouts.
"""

import hashlib
import os
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# parts of a <maplayer> that make up how it looks / how it is edited
STYLE_TAGS = ("renderer-v2", "pipe", "blendMode", "featureBlendMode", "layerOpacity", "renderer-3d",
              "diagramlayersettings", "SingleCategoryDiagramRenderer", "LinearlyInterpolatedDiagramRenderer",
              "effect")
LABEL_TAGS = ("labeling",)
FORM_TAGS = ("fieldConfiguration", "aliases", "defaults", "constraints", "constraintExpressions",
             "editform", "editorlayout", "attributeEditorForm", "expressionfields", "widgets")


@dataclass
class Change:
    section: str     # project | layers | tree | layouts
    kind: str        # added | removed | changed
    name: str
    detail: str = ""


@dataclass
class _Layer:
    id: str
    name: str
    type: str
    provider: str
    source: str
    crs: str
    style: str
    labels: str
    labels_on: bool
    form: str
    scale: tuple


@dataclass
class _Project:
    crs: str = ""
    title: str = ""
    layers: dict = field(default_factory=dict)        # id -> _Layer
    tree: list = field(default_factory=list)          # [(id, group path, checked)] in order
    groups: dict = field(default_factory=dict)        # group path -> checked
    layouts: dict = field(default_factory=dict)       # name -> hash


def _hash(elems):
    h = hashlib.sha1()
    for e in elems:
        if e is not None:
            h.update(ET.tostring(e))
    return h.hexdigest()


def _text(elem, path):
    found = elem.find(path)
    return (found.text or "").strip() if found is not None else ""


def read_root(path):
    """Project XML root of a .qgz or .qgs file, or None when there is no file."""
    if not path or not os.path.exists(path):
        return None
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".qgs")]
            if not names:
                raise ValueError(f"no .qgs inside {path}")
            return ET.fromstring(z.read(names[0]))
    return ET.parse(path).getroot()


def _parse(root):
    p = _Project()
    if root is None:
        return p
    p.crs = _text(root, "projectCrs/spatialrefsys/authid") or _text(root, "projectCrs/spatialrefsys/description")
    p.title = _text(root, "title")
    for ml in root.iterfind("projectlayers/maplayer"):
        children = {c.tag: c for c in ml}
        lid = _text(ml, "id")
        p.layers[lid] = _Layer(
            lid, _text(ml, "layername"), ml.get("type", ""), _text(ml, "provider"), _text(ml, "datasource"),
            _text(ml, "srs/spatialrefsys/authid"),
            _hash(children.get(t) for t in STYLE_TAGS),
            _hash(children.get(t) for t in LABEL_TAGS),
            ml.get("labelsEnabled") == "1",
            _hash(children.get(t) for t in FORM_TAGS),
            (ml.get("hasScaleBasedVisibilityFlag"), ml.get("minScale"), ml.get("maxScale")))

    def walk(group, path):
        for c in group:
            if c.tag == "layer-tree-layer":
                p.tree.append((c.get("id"), path, c.get("checked") == "Qt::Checked"))
            elif c.tag == "layer-tree-group":
                sub = f"{path}/{c.get('name')}" if path else c.get("name", "")
                p.groups[sub] = c.get("checked") == "Qt::Checked"
                walk(c, sub)

    top = root.find("layer-tree-group")
    if top is not None:
        walk(top, "")
    for lay in root.iterfind("Layouts/Layout"):
        p.layouts[lay.get("name", "")] = _hash([lay])
    return p


def _layer_changes(o, n):
    out = []
    if o.name != n.name:
        out.append(("renamed", f"{o.name} → {n.name}"))
    if o.source != n.source or o.provider != n.provider:
        out.append(("source", n.source))
    if o.crs != n.crs:
        out.append(("crs", f"{o.crs} → {n.crs}"))
    if o.style != n.style:
        out.append(("style", ""))
    if o.labels_on != n.labels_on:
        out.append(("labels_on" if n.labels_on else "labels_off", ""))
    elif o.labels != n.labels:
        out.append(("labels", ""))
    if o.form != n.form:
        out.append(("form", ""))
    if o.scale != n.scale:
        out.append(("scale", ""))
    return out


def diff_projects(old_root, new_root):
    """[Change] between two project XML roots (either may be None)."""
    o, n = _parse(old_root), _parse(new_root)
    changes = []
    if o.crs != n.crs and old_root is not None and new_root is not None:
        changes.append(Change("project", "changed", "crs", f"{o.crs or '–'} → {n.crs or '–'}"))
    if o.title != n.title and old_root is not None and new_root is not None:
        changes.append(Change("project", "changed", "title", f"{o.title or '–'} → {n.title or '–'}"))

    for lid, lyr in n.layers.items():
        if lid not in o.layers:
            changes.append(Change("layers", "added", lyr.name, lyr.source))
        else:
            for what, detail in _layer_changes(o.layers[lid], lyr):
                changes.append(Change("layers", "changed", lyr.name, f"{what}|{detail}"))
    for lid, lyr in o.layers.items():
        if lid not in n.layers:
            changes.append(Change("layers", "removed", lyr.name, lyr.source))

    def name_of(lid):
        lyr = n.layers.get(lid) or o.layers.get(lid)
        return lyr.name if lyr else lid

    old_pos = {lid: (path, checked) for lid, path, checked in o.tree}
    for lid, path, checked in n.tree:
        if lid not in old_pos:
            continue
        old_path, old_checked = old_pos[lid]
        if old_path != path:
            changes.append(Change("tree", "changed", name_of(lid), f"moved|{old_path or '/'} → {path or '/'}"))
        if old_checked != checked:
            changes.append(Change("tree", "changed", name_of(lid), "shown" if checked else "hidden"))
    common_old = [lid for lid, _, _ in o.tree if lid in {x for x, _, _ in n.tree}]
    common_new = [lid for lid, _, _ in n.tree if lid in set(common_old)]
    if common_old != common_new:
        changes.append(Change("tree", "changed", "", "order"))
    for g in n.groups:
        if g not in o.groups:
            changes.append(Change("tree", "added", g, "group"))
    for g in o.groups:
        if g not in n.groups:
            changes.append(Change("tree", "removed", g, "group"))

    for name, h in n.layouts.items():
        if name not in o.layouts:
            changes.append(Change("layouts", "added", name))
        elif o.layouts[name] != h:
            changes.append(Change("layouts", "changed", name))
    for name in o.layouts:
        if name not in n.layouts:
            changes.append(Change("layouts", "removed", name))
    return changes


def diff_files(old_path, new_path):
    return diff_projects(read_root(old_path), read_root(new_path))
