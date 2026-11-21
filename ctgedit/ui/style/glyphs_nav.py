"""Icons for the canvas navigation tools."""

from __future__ import annotations

from typing import Callable, Dict

from PySide6 import QtCore, QtGui


def _pt(x: float, y: float) -> QtCore.QPointF:
    return QtCore.QPointF(x, y)


def _thin(p: QtGui.QPainter, width: float) -> None:
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(width)
    p.setPen(pen)


def _g_hand(p, fg):
    """An open hand: four fingers and a thumb."""
    path = QtGui.QPainterPath(_pt(6.0, 11.5))
    path.lineTo(6.0, 6.2)
    path.arcTo(QtCore.QRectF(6.0, 4.6, 2.4, 3.2), 180, -180)
    path.lineTo(8.4, 10.0)
    path.lineTo(8.4, 4.4)
    path.arcTo(QtCore.QRectF(8.4, 2.8, 2.4, 3.2), 180, -180)
    path.lineTo(10.8, 10.0)
    path.lineTo(10.8, 5.0)
    path.arcTo(QtCore.QRectF(10.8, 3.4, 2.4, 3.2), 180, -180)
    path.lineTo(13.2, 10.4)
    path.lineTo(13.2, 7.0)
    path.arcTo(QtCore.QRectF(13.2, 5.4, 2.4, 3.2), 180, -180)
    path.lineTo(15.6, 12.4)
    path.cubicTo(15.6, 15.8, 13.6, 17.4, 10.8, 17.4)
    path.cubicTo(8.6, 17.4, 7.4, 16.4, 6.2, 14.6)
    path.lineTo(3.6, 10.8)
    path.cubicTo(3.0, 9.8, 4.4, 8.8, 5.2, 9.8)
    path.lineTo(6.0, 11.5)
    _thin(p, 1.3)
    p.drawPath(path)


def _g_zoom_box(p, fg):
    """A dashed box with a magnifier."""
    pen = QtGui.QPen(fg, 1.3)
    pen.setDashPattern([1.6, 1.3])
    p.setPen(pen)
    p.drawRect(QtCore.QRectF(2.5, 2.5, 11.0, 9.0))
    pen = QtGui.QPen(fg, 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawEllipse(_pt(11.5, 11.5), 3.6, 3.6)
    p.drawLine(QtCore.QLineF(14.2, 14.2, 17.6, 17.6))


def _g_prev_view(p, fg):
    path = QtGui.QPainterPath(_pt(16.0, 14.0))
    path.cubicTo(16.0, 8.0, 12.0, 6.5, 6.0, 6.5)
    p.drawPath(path)
    p.drawPolyline(QtGui.QPolygonF([_pt(9.0, 3.4), _pt(5.6, 6.5), _pt(9.0, 9.6)]))


def _g_next_view(p, fg):
    path = QtGui.QPainterPath(_pt(4.0, 14.0))
    path.cubicTo(4.0, 8.0, 8.0, 6.5, 14.0, 6.5)
    p.drawPath(path)
    p.drawPolyline(QtGui.QPolygonF([_pt(11.0, 3.4), _pt(14.4, 6.5), _pt(11.0, 9.6)]))


def _g_rulers(p, fg):
    """An L of two rulers with ticks."""
    _thin(p, 1.3)
    path = QtGui.QPainterPath(_pt(2.5, 17.5))
    path.lineTo(2.5, 2.5)
    path.lineTo(17.5, 2.5)
    path.lineTo(17.5, 6.5)
    path.lineTo(6.5, 6.5)
    path.lineTo(6.5, 17.5)
    path.closeSubpath()
    p.drawPath(path)
    _thin(p, 1.0)
    for x in (9.5, 12.5, 15.5):
        p.drawLine(QtCore.QLineF(x, 2.5, x, 4.6 if x != 12.5 else 5.4))
    for y in (9.5, 12.5, 15.5):
        p.drawLine(QtCore.QLineF(2.5, y, 4.6 if y != 12.5 else 5.4, y))


def _g_navigator(p, fg):
    """A page with the shown area framed."""
    _thin(p, 1.3)
    p.drawRoundedRect(QtCore.QRectF(2.5, 4.0, 15.0, 12.0), 1.5, 1.5)
    p.setBrush(QtGui.QColor(fg.red(), fg.green(), fg.blue(), 90))
    _thin(p, 1.6)
    p.drawRect(QtCore.QRectF(8.5, 7.0, 6.0, 5.0))


def _g_crosshair(p, fg):
    _thin(p, 1.3)
    p.drawLine(QtCore.QLineF(10.0, 2.0, 10.0, 7.2))
    p.drawLine(QtCore.QLineF(10.0, 12.8, 10.0, 18.0))
    p.drawLine(QtCore.QLineF(2.0, 10.0, 7.2, 10.0))
    p.drawLine(QtCore.QLineF(12.8, 10.0, 18.0, 10.0))
    p.drawEllipse(_pt(10.0, 10.0), 2.0, 2.0)


def _g_guidelines(p, fg):
    pen = QtGui.QPen(fg, 1.3)
    pen.setDashPattern([1.8, 1.4])
    p.setPen(pen)
    p.drawLine(QtCore.QLineF(2.0, 7.0, 18.0, 7.0))
    p.drawLine(QtCore.QLineF(12.5, 2.0, 12.5, 18.0))
    _thin(p, 1.3)
    p.setPen(QtGui.QPen(fg, 1.3))
    p.drawRect(QtCore.QRectF(4.0, 9.5, 6.0, 6.0))


def _g_magnifier(p, fg):
    """A magnifier with a dot: the Show Zoom window."""
    p.drawEllipse(_pt(8.8, 8.8), 5.0, 5.0)
    p.drawLine(QtCore.QLineF(12.5, 12.5, 16.6, 16.6))
    p.setBrush(fg)
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(_pt(8.8, 8.8), 1.6, 1.6)


def _g_saved_views(p, fg):
    """A bookmark."""
    _thin(p, 1.4)
    p.drawPolygon(
        QtGui.QPolygonF([_pt(5.5, 3.0), _pt(14.5, 3.0), _pt(14.5, 17.0), _pt(10.0, 13.0), _pt(5.5, 17.0)])
    )


def _corners(p) -> None:
    for seg in (
        ((3.0, 6.5), (3.0, 3.0), (6.5, 3.0)),
        ((13.5, 3.0), (17.0, 3.0), (17.0, 6.5)),
        ((17.0, 13.5), (17.0, 17.0), (13.5, 17.0)),
        ((6.5, 17.0), (3.0, 17.0), (3.0, 13.5)),
    ):
        p.drawPolyline(QtGui.QPolygonF([_pt(x, y) for x, y in seg]))


def _g_fit_all(p, fg):
    """The fit corners round a page and a shape outside it."""
    _corners(p)
    _thin(p, 1.2)
    p.drawRect(QtCore.QRectF(6.0, 7.5, 6.0, 5.0))
    p.drawEllipse(_pt(14.0, 12.0), 1.6, 1.6)


def _g_zoom_sel(p, fg):
    """The fit corners round a selected box."""
    _corners(p)
    pen = QtGui.QPen(fg, 1.2)
    pen.setDashPattern([1.5, 1.2])
    p.setPen(pen)
    p.drawRect(QtCore.QRectF(6.5, 6.5, 7.0, 7.0))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawRect(QtCore.QRectF(8.5, 8.5, 3.0, 3.0))


def _g_overflow(p, fg):
    p.setBrush(fg)
    p.setPen(QtCore.Qt.NoPen)
    for x in (4.5, 10.0, 15.5):
        p.drawEllipse(_pt(x, 10.0), 1.5, 1.5)


GLYPHS: Dict[str, Callable] = {
    "hand": _g_hand,
    "zoom_box": _g_zoom_box,
    "prev_view": _g_prev_view,
    "next_view": _g_next_view,
    "rulers": _g_rulers,
    "navigator": _g_navigator,
    "crosshair": _g_crosshair,
    "guidelines": _g_guidelines,
    "magnifier": _g_magnifier,
    "saved_views": _g_saved_views,
    "overflow": _g_overflow,
    "fit_all": _g_fit_all,
    "zoom_sel": _g_zoom_sel,
}


def paint_glyph(name: str, w: int, h: int, dpr: float, fg: QtGui.QColor) -> QtGui.QPixmap:
    pw, ph = max(1, round(w * dpr)), max(1, round(h * dpr))
    pm = QtGui.QPixmap(pw, ph)
    pm.fill(QtCore.Qt.transparent)
    side = min(pw, ph)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.translate((pw - side) / 2.0, (ph - side) / 2.0)
    painter.scale(side / 20.0, side / 20.0)
    pen = QtGui.QPen(fg, 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    try:
        GLYPHS[name](painter, fg)
    finally:
        painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class _Engine(QtGui.QIconEngine):
    def __init__(self, name: str):
        super().__init__()
        self._name = name

    def _pixmap(self, size: QtCore.QSize, mode, dpr: float) -> QtGui.QPixmap:
        from .theme import color

        role = "disabled" if mode == QtGui.QIcon.Mode.Disabled else "text"
        return paint_glyph(self._name, size.width(), size.height(), dpr, color(role))

    def pixmap(self, size, mode, state):
        return self._pixmap(size, mode, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        return self._pixmap(size, mode, float(scale or 1.0))

    def paint(self, painter, rect, mode, state):
        dev = painter.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        painter.drawPixmap(rect, self._pixmap(rect.size(), mode, dpr))

    def clone(self):
        e = _Engine(self._name)
        _CLONES.append(e)  # (kept alive, as the theme keeps its engines)
        return e

    def key(self):
        return "ctgnavglyph"


#: the icons made, one per name (their engines are Python objects: a reference keeps each
#: alive while Qt paints with it; the colour is read at paint time, nothing goes stale)
_ICONS: Dict[str, tuple] = {}
_CLONES: list = []


def icon(name: str) -> QtGui.QIcon:
    """The navigation glyph ``name`` (see GLYPHS) as an icon; the theme's icon otherwise."""
    if name not in GLYPHS:
        from .theme import icon as theme_icon

        return theme_icon(name)
    held = _ICONS.get(name)
    if held is None:
        engine = _Engine(name)
        held = _ICONS[name] = (QtGui.QIcon(engine), engine)
    return held[0]
