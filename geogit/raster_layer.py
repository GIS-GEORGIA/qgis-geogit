"""Raster comparison on the map: old/new layers, a coloured difference layer,
outlines of changed areas, and a swipe tool to slide between the versions."""

from qgis.core import (Qgis, QgsColorRampShader, QgsFillSymbol, QgsMapRendererParallelJob, QgsRasterLayer,
                       QgsRasterShader, QgsSingleBandPseudoColorRenderer, QgsVectorLayer)
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPen
from qgis.PyQt.QtWidgets import QGraphicsItem

from .i18n import t


def diff_renderer(layer, diff):
    """Blue (lower) – red (higher) for one band, yellow – red (how much) for imagery."""
    if diff.old.bands == 1:
        top = max(abs(diff.min_diff), abs(diff.max_diff)) or 1.0
        items = [QgsColorRampShader.ColorRampItem(-top, QColor("#2166ac"), f"{-top:g}"),
                 QgsColorRampShader.ColorRampItem(-top / 4, QColor("#92c5de")),
                 QgsColorRampShader.ColorRampItem(0, QColor(247, 247, 247, 60), "0"),
                 QgsColorRampShader.ColorRampItem(top / 4, QColor("#f4a582")),
                 QgsColorRampShader.ColorRampItem(top, QColor("#b2182b"), f"+{top:g}")]
        lo, hi = -top, top
    else:
        top = diff.max_diff or 1.0
        items = [QgsColorRampShader.ColorRampItem(0, QColor("#fff7bc"), "0"),
                 QgsColorRampShader.ColorRampItem(top / 2, QColor("#fe9929")),
                 QgsColorRampShader.ColorRampItem(top, QColor("#b30000"), f"{top:g}")]
        lo, hi = 0, top
    ramp = QgsColorRampShader(lo, hi)
    ramp.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    ramp.setColorRampItemList(items)
    shader = QgsRasterShader(lo, hi)
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(lo)
    renderer.setClassificationMax(hi)
    layer.setRenderer(renderer)


def build_layers(main, label, old_path, new_path, diff):
    """[(layer, visible)] top to bottom for the layer tree group."""
    name = main.split("/")[-1]
    out = []
    if diff and diff.mask_path:
        areas = QgsVectorLayer(f"{diff.mask_path}|layername=changed_areas", f"{name} · {t('raster_areas')}", "ogr")
        areas.renderer().setSymbol(QgsFillSymbol.createSimple(
            {"style": "no", "outline_color": "#e31a1c", "outline_width": "0.6"}))
        out.append((areas, True))
    if diff and diff.diff_path:
        d = QgsRasterLayer(diff.diff_path, f"{name} · {t('raster_diff')}")
        diff_renderer(d, diff)
        out.append((d, True))
    new_layer = QgsRasterLayer(new_path, f"{name} · {t('raster_new')} ({label})") if new_path else None
    old_layer = QgsRasterLayer(old_path, f"{name} · {t('raster_old')}") if old_path else None
    if new_layer is not None and old_layer is not None and new_layer.isValid() and old_layer.isValid() \
            and new_layer.bandCount() == old_layer.bandCount():
        # same colours and stretch on both sides, or swipe would show contrast, not change
        old_layer.setRenderer(new_layer.renderer().clone())
    if new_layer is not None:
        out.append((new_layer, True))
    if old_layer is not None:
        out.append((old_layer, not new_layer))
    return [(lyr, vis) for lyr, vis in out if lyr.isValid()]


class _SwipeItem(QGraphicsItem):
    """Draws the rendered 'left' image up to the swipe line."""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.image = None
        self.x = canvas.width() / 2
        self.setZValue(1000)

    def boundingRect(self):
        return QRectF(0, 0, self.canvas.width(), self.canvas.height())

    def paint(self, painter, _option, _widget=None):
        h = self.canvas.height()
        if self.image is not None:
            painter.save()
            painter.setClipRect(QRectF(0, 0, self.x, h))
            painter.drawImage(QPointF(0, 0), self.image)
            painter.restore()
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawLine(QPointF(self.x, 0), QPointF(self.x, h))
        painter.setPen(QPen(QColor("#e31a1c"), 1.5))
        painter.drawLine(QPointF(self.x, 0), QPointF(self.x, h))


class SwipeMapTool(QgsMapTool):
    """Left of the mouse: the given layers (old version); right: the map as it is."""

    def __init__(self, canvas, layers):
        super().__init__(canvas)
        self.layers = layers
        self.item = None
        self.job = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def activate(self):
        super().activate()
        self.item = _SwipeItem(self.canvas())
        self.canvas().scene().addItem(self.item)
        self.canvas().mapCanvasRefreshed.connect(self._render)
        self._render()

    def deactivate(self):
        try:
            self.canvas().mapCanvasRefreshed.disconnect(self._render)
        except TypeError:
            pass
        if self.job is not None:
            self.job.cancelWithoutBlocking()
            self.job = None
        if self.item is not None:
            self.canvas().scene().removeItem(self.item)
            self.item = None
        super().deactivate()

    def _render(self):
        old, self.job = self.job, None
        if old is not None:
            old.cancelWithoutBlocking()   # may emit finished right away; _rendered ignores it
        settings = self.canvas().mapSettings()
        settings.setLayers([lyr for lyr in self.layers if lyr is not None])
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        job = QgsMapRendererParallelJob(settings)
        job.finished.connect(lambda j=job: self._rendered(j))
        self.job = job
        job.start()

    def _rendered(self, job):
        if job is not self.job:
            return
        if self.item is not None:
            self.item.image = job.renderedImage()
            self.item.update()
        self.job = None

    def canvasMoveEvent(self, e):
        if self.item is not None:
            self.item.x = e.pixelPoint().x()
            self.item.update()

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.canvas().unsetMapTool(self)

    def keyPressEvent(self, e):
        esc = Qt.Key.Key_Escape
        if e.key() == getattr(esc, "value", esc):   # key() is an int in PyQt6
            self.canvas().unsetMapTool(self)
