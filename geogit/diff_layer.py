"""Turn a vector_diff.LayerDiff into a styled memory layer on the map."""

from qgis.core import (Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsField, QgsFields, QgsGeometry, QgsMemoryProviderUtils, QgsProject,
                       QgsRendererCategory, QgsSymbol, QgsWkbTypes)
from qgis.PyQt.QtCore import QMetaType, Qt
from qgis.PyQt.QtGui import QColor

from .i18n import t

CHANGE_FIELD = "geogit_change"
FIELDS_FIELD = "geogit_fields"
# drawn bottom to top (features are added in this order): the old shape of a
# modified feature stays under the new one
CATEGORIES = [("modified_old", "#9e9e9e"), ("deleted", "#d62728"),
              ("modified", "#ff9800"), ("added", "#2ca02c")]


def _symbol(geom_type, key, color):
    sym = QgsSymbol.defaultSymbol(geom_type)
    c = QColor(color)
    if geom_type == Qgis.GeometryType.Polygon:
        fill = QColor(c)
        fill.setAlpha(0 if key == "modified_old" else 90)
        sym.setColor(fill)
        layer = sym.symbolLayer(0)
        layer.setStrokeColor(c)
        layer.setStrokeWidth(0.6)
        if key == "modified_old":
            layer.setStrokeStyle(Qt.PenStyle.DashLine)
    elif geom_type == Qgis.GeometryType.Line:
        sym.setColor(c)
        sym.setWidth(0.5 if key == "modified_old" else 0.9)
        if key == "modified_old":
            sym.symbolLayer(0).setPenStyle(Qt.PenStyle.DashLine)
    else:
        sym.setColor(c)
        sym.setSize(2.4 if key == "modified_old" else 3.2)
        if key == "modified_old":
            sym.symbolLayer(0).setColor(QColor(0, 0, 0, 0))
            sym.symbolLayer(0).setStrokeColor(c)
    return sym


def _geometry(wkb):
    if not wkb:
        return None
    g = QgsGeometry()
    g.fromWkb(wkb)
    return None if g.isNull() else g


def _wkb_type(geoms):
    for g in geoms:
        if g is not None:
            return QgsWkbTypes.multiType(g.wkbType())
    return Qgis.WkbType.NoGeometry


def _string_field(name):
    return QgsField(name, QMetaType.Type.QString)      # QGIS 3.38+ (minimum here is 3.44)


def build_layer(diff, title):
    """Memory layer with one feature per change (modified ones twice: old + new shape)."""
    by_key = {k: [] for k, _ in CATEGORIES}
    by_key["added"] = [(f, []) for f in diff.added]
    by_key["deleted"] = [(f, []) for f in diff.deleted]
    for m in diff.modified:
        changed = list(m.fields) + (["geometry"] if m.geometry else [])
        by_key["modified_old"].append((m.old, changed))
        by_key["modified"].append((m.new, changed))
    rows = [(k, f, ch) for k, _ in CATEGORIES for f, ch in by_key[k]]

    geoms = [_geometry(f.wkb) for _, f, _ in rows]
    wkb_type = _wkb_type(geoms)
    fields = QgsFields()
    fields.append(_string_field(CHANGE_FIELD))
    fields.append(_string_field(FIELDS_FIELD))
    attr_names = list(diff.fields) + [n for n in diff.fields_removed if n not in diff.fields]
    for name in attr_names:
        fields.append(_string_field(name))
    crs = QgsCoordinateReferenceSystem.fromWkt(diff.srs_wkt) if diff.srs_wkt else QgsCoordinateReferenceSystem()
    layer = QgsMemoryProviderUtils.createMemoryLayer(title, fields, wkb_type, crs)

    feats = []
    for (key, f, changed), g in zip(rows, geoms):
        qf = QgsFeature(fields)
        values = [t("change_" + key), ", ".join(changed)]
        values += [None if f.attrs.get(n) is None else str(f.attrs.get(n)) for n in attr_names]
        qf.setAttributes(values)
        if g is not None:
            g.convertToMultiType()
            qf.setGeometry(g)
        feats.append(qf)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    if wkb_type != Qgis.WkbType.NoGeometry:
        gtype = QgsWkbTypes.geometryType(wkb_type)
        cats = [QgsRendererCategory(t("change_" + k), _symbol(gtype, k, c), t("change_" + k))
                for k, c in CATEGORIES]
        layer.setRenderer(QgsCategorizedSymbolRenderer(CHANGE_FIELD, cats))
    return layer


def add_to_project(layers, group_name):
    """Put the diff layers in a fresh group at the top of the layer tree."""
    root = QgsProject.instance().layerTreeRoot()
    old = root.findGroup(group_name)
    if old is not None:      # QGIS 4.0: an empty group counts as False
        root.removeChildNode(old)
    group = root.insertGroup(0, group_name)
    for layer in layers:
        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
    return group
