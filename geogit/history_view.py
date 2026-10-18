"""Painting of the commit graph column and author colour dots."""

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import QStyle, QStyledItemDelegate

from . import graph

_USER_ROLE = Qt.ItemDataRole.UserRole
# PyQt6 enums are not always ints, so take the number before adding to it
GRAPH_ROLE = getattr(_USER_ROLE, "value", _USER_ROLE) + 1
LANE_W = 14
NODE_R = 4.5


def author_icon(color, size=12):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return QIcon(pix)


def graph_width(rows):
    return max((r.width for r in rows), default=1) * LANE_W + 6


class GraphDelegate(QStyledItemDelegate):
    """Draws one row of the lane graph. Item data: (GraphRow, author colour, dimmed)."""

    def paint(self, painter, option, index):
        data = index.data(GRAPH_ROLE)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        if not data:
            return
        row, color, dimmed = data
        r = option.rect
        left, top, bottom = r.left() + LANE_W / 2 + 2, r.top(), r.bottom() + 1
        mid = r.center().y() + 0.5

        def x(col):
            return left + col * LANE_W

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = 90 if dimmed else 255
        for segs, y0, y1, color_col in ((row.up, top, mid, 0), (row.down, mid, bottom, 1)):
            for a, b in segs:
                c = QColor(graph.lane_color((a, b)[color_col]))
                c.setAlpha(alpha)
                painter.setPen(QPen(c, 2))
                path = QPainterPath(QPointF(x(a), y0))
                if a == b:
                    path.lineTo(QPointF(x(b), y1))
                else:
                    ym = (y0 + y1) / 2
                    path.cubicTo(QPointF(x(a), ym), QPointF(x(b), ym), QPointF(x(b), y1))
                painter.drawPath(path)

        node = QColor(color)
        outline = option.palette.base().color()
        center = QPointF(x(row.col), mid)
        if dimmed:
            node.setAlpha(140)
            painter.setPen(QPen(node, 1.6))
            painter.setBrush(outline)
        else:
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(node)
        radius = NODE_R + (1 if row.is_merge else 0)
        painter.drawEllipse(center, radius, radius)
        if row.is_merge and not dimmed:
            painter.setBrush(outline)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, 1.8, 1.8)
        painter.restore()
