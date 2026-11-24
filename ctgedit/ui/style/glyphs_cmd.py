"""Icons for the ribbon and menu commands."""

from __future__ import annotations

import math
from typing import Callable, Dict

from PySide6 import QtCore, QtGui


# --------------------------------------------------------------------------- helpers
def P(x: float, y: float) -> QtCore.QPointF:
    return QtCore.QPointF(x, y)


def R(x: float, y: float, w: float, h: float) -> QtCore.QRectF:
    return QtCore.QRectF(x, y, w, h)


def lines(p: QtGui.QPainter, segs) -> None:
    for x1, y1, x2, y2 in segs:
        p.drawLine(QtCore.QLineF(x1, y1, x2, y2))


def poly(p: QtGui.QPainter, pts, closed: bool = False) -> None:
    pg = QtGui.QPolygonF([P(x, y) for x, y in pts])
    if closed:
        p.drawPolygon(pg)
    else:
        p.drawPolyline(pg)


def width(p: QtGui.QPainter, w: float) -> None:
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(w)
    p.setPen(pen)


def filled(p: QtGui.QPainter, fg: QtGui.QColor, shape: str, *args) -> None:
    """A shape filled in ``fg`` with no outline ('rect', 'ellipse', 'poly')."""
    p.save()
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    if shape == "rect":
        p.drawRect(R(*args))
    elif shape == "ellipse":
        p.drawEllipse(R(*args))
    elif shape == "rrect":
        x, y, w, h, r = args
        p.drawRoundedRect(R(x, y, w, h), r, r)
    else:
        p.drawPolygon(QtGui.QPolygonF([P(x, y) for x, y in args[0]]))
    p.restore()


def punch(p: QtGui.QPainter, rect: QtCore.QRectF) -> None:
    """Erase under a front shape so the outline behind reads as hidden."""
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.fillRect(rect.adjusted(-0.9, -0.9, 0.9, 0.9), QtCore.Qt.black)
    p.restore()


def punch_ellipse(p: QtGui.QPainter, rect: QtCore.QRectF) -> None:
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtCore.Qt.black)
    p.drawEllipse(rect.adjusted(-1.0, -1.0, 1.0, 1.0))
    p.restore()


def letter(
    p: QtGui.QPainter,
    text: str,
    size: float = 13.0,
    bold: bool = False,
    italic: bool = False,
    rect: QtCore.QRectF = None,
    underline: bool = False,
    strike: bool = False,
) -> None:
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(max(1, int(round(size))))
    font.setBold(bold)
    font.setItalic(italic)
    font.setUnderline(underline)
    font.setStrikeOut(strike)
    p.setFont(font)
    p.drawText(rect or R(0.0, 0.0, 20.0, 20.0), QtCore.Qt.AlignCenter, text)


def accent() -> QtGui.QColor:
    """The active scheme's accent (theme is imported when a glyph paints, never at load)."""
    from . import theme

    return QtGui.QColor(theme.COLORS.get("accent") or theme.DARK["accent"])


def arrow_head(p: QtGui.QPainter, tip, angle_deg: float, size: float = 3.0) -> None:
    """Two strokes of an arrow head at ``tip`` pointing along ``angle_deg`` (0 = right,
    90 = down)."""
    a = math.radians(angle_deg)
    for side in (-1, 1):
        b = a + math.pi + side * math.radians(38)
        p.drawLine(P(*tip), P(tip[0] + size * math.cos(b), tip[1] + size * math.sin(b)))


def arc_arrow(p: QtGui.QPainter, rect, start: float, span: float, head: float = 3.0) -> None:
    """An arc (Qt degrees: 0 = 3 o'clock, positive = anticlockwise) with a head at its end."""
    path = QtGui.QPainterPath()
    path.arcMoveTo(rect, start)
    path.arcTo(rect, start, span)
    p.drawPath(path)
    end = path.currentPosition()
    # the tangent at the end, in screen coordinates (y down)
    ang = math.radians(start + span)
    rx, ry = rect.width() / 2.0, rect.height() / 2.0
    dx, dy = -rx * math.sin(ang), -ry * math.cos(ang)
    if span < 0:
        dx, dy = -dx, -dy
    arrow_head(p, (end.x(), end.y()), math.degrees(math.atan2(dy, dx)), head)


def plus(p: QtGui.QPainter, cx: float, cy: float, r: float = 2.6, colour=None) -> None:
    """A small plus badge (the 'paste' / 'add' corner), cleared under it first."""
    punch_ellipse(p, R(cx - r - 0.6, cy - r - 0.6, 2 * r + 1.2, 2 * r + 1.2))
    p.save()
    pen = QtGui.QPen(colour or accent(), 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    lines(p, [(cx - r, cy, cx + r, cy), (cx, cy - r, cx, cy + r)])
    p.restore()


def badge_arrows(p: QtGui.QPainter, cx: float, cy: float, r: float = 3.2) -> None:
    """A small refresh badge (two arc arrows) at (cx, cy), cleared under it."""
    punch_ellipse(p, R(cx - r - 1.0, cy - r - 1.0, 2 * r + 2.0, 2 * r + 2.0))
    p.save()
    pen = QtGui.QPen(accent(), 1.3)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    rect = R(cx - r, cy - r, 2 * r, 2 * r)
    arc_arrow(p, rect, 100.0, 150.0, 1.8)
    arc_arrow(p, rect, 280.0, 150.0, 1.8)
    p.restore()


def page_outline(
    p: QtGui.QPainter, x: float = 4.5, y: float = 2.8, w: float = 11.0, h: float = 14.4, fold: float = 3.2
) -> None:
    path = QtGui.QPainterPath(P(x, y))
    path.lineTo(x + w - fold, y)
    path.lineTo(x + w, y + fold)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.drawPath(path)


def disk(p: QtGui.QPainter, x: float, y: float, s: float) -> None:
    """The save disk in a box of side ``s`` at (x, y)."""
    k = s / 11.0
    path = QtGui.QPainterPath(P(x, y))
    for dx, dy in ((8.3, 0.0), (11.0, 2.7), (11.0, 11.0), (0.0, 11.0)):
        path.lineTo(x + dx * k, y + dy * k)
    path.closeSubpath()
    p.drawPath(path)
    p.save()
    width(p, max(0.9, 1.2 * k))
    p.drawRect(R(x + 2.7 * k, y, 5.2 * k, 3.4 * k))
    p.drawRect(R(x + 2.3 * k, y + 6.5 * k, 6.4 * k, 4.5 * k))
    p.restore()


def magnifier(p: QtGui.QPainter, cx: float = 8.8, cy: float = 8.8, r: float = 5.0) -> None:
    p.drawEllipse(P(cx, cy), r, r)
    k = r / math.sqrt(2.0)
    p.drawLine(QtCore.QLineF(cx + k, cy + k, cx + k + 4.2 * r / 5.0, cy + k + 4.2 * r / 5.0))


def puzzle_path(x: float, y: float, s: float) -> QtGui.QPainterPath:
    """A puzzle piece (a genie) in a box of side ``s``: knobs on the top and the right."""
    k = s / 12.0
    path = QtGui.QPainterPath(P(x, y + 2.5 * k))
    path.lineTo(x + 3.6 * k, y + 2.5 * k)
    path.arcTo(R(x + 3.6 * k, y, 3.2 * k, 3.6 * k), 200.0, -220.0)
    path.lineTo(x + 9.5 * k, y + 2.5 * k)
    path.lineTo(x + 9.5 * k, y + 5.6 * k)
    path.arcTo(R(x + 9.0 * k, y + 5.6 * k, 3.6 * k, 3.2 * k), 110.0, -220.0)
    path.lineTo(x + 9.5 * k, y + 12.0 * k)
    path.lineTo(x, y + 12.0 * k)
    path.closeSubpath()
    return path


def books(p: QtGui.QPainter, x: float = 3.0, y: float = 4.0, h: float = 12.5) -> None:
    """Three library books on a shelf."""
    p.drawRect(R(x, y + 1.5, 3.0, h - 1.5))
    p.drawRect(R(x + 3.0, y, 3.2, h))
    p.save()
    p.translate(x + 9.2, y + h)
    p.rotate(-16.0)
    p.drawRect(R(0.0, -h + 1.2, 3.0, h - 1.2))
    p.restore()


def picture(p: QtGui.QPainter, x: float = 3.0, y: float = 4.0, w: float = 14.0, h: float = 11.5) -> None:
    p.drawRect(R(x, y, w, h))
    p.save()
    width(p, 1.3)
    poly(
        p,
        [
            (x + 1.6, y + h - 1.6),
            (x + w * 0.38, y + h * 0.45),
            (x + w * 0.58, y + h * 0.7),
            (x + w * 0.72, y + h * 0.55),
            (x + w - 1.6, y + h - 1.6),
        ],
    )
    p.restore()
    filled(p, p.pen().color(), "ellipse", x + w * 0.66, y + 1.8, 2.4, 2.4)


def pencil(p: QtGui.QPainter, x1: float, y1: float, x2: float, y2: float, thick: float = 2.6) -> None:
    """A pencil from (x1, y1) (the tip) to (x2, y2)."""
    ang = math.atan2(y2 - y1, x2 - x1)
    nx, ny = -math.sin(ang) * thick / 2.0, math.cos(ang) * thick / 2.0
    tip_len = thick * 1.2
    bx, by = x1 + math.cos(ang) * tip_len, y1 + math.sin(ang) * tip_len
    p.save()
    width(p, 1.2)
    poly(
        p,
        [(x1, y1), (bx + nx, by + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (bx - nx, by - ny)],
        closed=True,
    )
    p.restore()


def window_frame(p: QtGui.QPainter, x: float, y: float, w: float, h: float, bar: float = 2.6) -> None:
    p.drawRect(R(x, y, w, h))
    filled(p, p.pen().color(), "rect", x, y, w, bar)


# --------------------------------------------------------------------------- files and pages
def g_save_as(p, fg):
    disk(p, 3.0, 3.0, 11.0)
    pencil(p, 11.0, 17.0, 17.4, 10.6)


def g_save_all(p, fg):
    disk(p, 6.0, 2.6, 11.0)
    punch(p, R(2.6, 6.2, 11.6, 11.6))
    disk(p, 3.0, 6.6, 11.0)


def g_close(p, fg):
    lines(p, [(5.0, 5.0, 15.0, 15.0), (15.0, 5.0, 5.0, 15.0)])


def g_exit(p, fg):
    poly(p, [(11.0, 6.0), (11.0, 3.2), (3.6, 3.2), (3.6, 16.8), (11.0, 16.8), (11.0, 14.0)])
    lines(p, [(7.6, 10.0, 17.0, 10.0)])
    arrow_head(p, (17.0, 10.0), 0.0, 3.2)


def g_new_window(p, fg):
    window_frame(p, 2.6, 3.0, 11.0, 9.0)
    punch(p, R(6.6, 7.6, 11.0, 9.0))
    window_frame(p, 6.6, 7.6, 11.0, 9.0)


def g_print(p, fg):
    p.drawRect(R(5.6, 2.8, 8.8, 4.4))
    p.drawRoundedRect(R(2.6, 7.2, 14.8, 6.8), 1.4, 1.4)
    punch(p, R(5.6, 11.4, 8.8, 5.8))
    p.drawRect(R(5.6, 11.4, 8.8, 5.8))
    width(p, 1.1)
    lines(p, [(7.2, 14.0, 12.8, 14.0)])
    filled(p, fg, "ellipse", 13.6, 8.8, 1.6, 1.6)


def g_print_setup(p, fg):
    g_print(p, fg)
    punch_ellipse(p, R(12.0, 11.0, 7.0, 7.0))
    gear(p, 15.5, 14.5, 2.9, 1.2)


def g_export_page(p, fg):
    page_outline(p, 3.0, 2.8, 10.0, 14.4)
    lines(p, [(9.0, 10.0, 17.6, 10.0)])
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(9.0, 10.0, 17.6, 10.0)])
    arrow_head(p, (17.6, 10.0), 0.0, 3.0)
    p.restore()


def g_import(p, fg):
    page_outline(p, 7.0, 2.8, 10.0, 14.4)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(2.0, 10.0, 11.4, 10.0)])
    arrow_head(p, (11.4, 10.0), 0.0, 3.0)
    p.restore()


def g_revert(p, fg):
    page_outline(p)
    punch_ellipse(p, R(8.4, 8.4, 9.6, 9.6))
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    arc_arrow(p, R(9.6, 9.6, 7.2, 7.2), 340.0, -260.0, 2.2)
    p.restore()


def g_restore_backup(p, fg):
    # a clock face with an anticlockwise arrow round it (history)
    arc_arrow(p, R(3.2, 3.2, 13.6, 13.6), 200.0, -300.0, 2.8)
    width(p, 1.4)
    lines(p, [(10.0, 6.6, 10.0, 10.2), (10.0, 10.2, 12.8, 12.0)])


def g_page_defaults(p, fg):
    page_outline(p)
    width(p, 1.2)
    lines(p, [(6.6, 8.0, 13.4, 8.0), (6.6, 12.0, 13.4, 12.0)])
    filled(p, fg, "ellipse", 7.6, 6.6, 2.8, 2.8)
    filled(p, fg, "ellipse", 10.4, 10.6, 2.8, 2.8)


def g_page_properties(p, fg):
    page_outline(p)
    width(p, 1.2)
    for y in (7.6, 10.4, 13.2):
        filled(p, fg, "ellipse", 6.6, y - 0.8, 1.6, 1.6)
        lines(p, [(9.0, y, 13.2, y)])


def g_properties(p, fg):
    p.drawRoundedRect(R(3.0, 3.4, 14.0, 13.2), 1.6, 1.6)
    width(p, 1.2)
    for y in (7.0, 10.0, 13.0):
        filled(p, fg, "rect", 5.4, y - 0.9, 1.8, 1.8)
        lines(p, [(8.6, y, 14.6, y)])


def g_template(p, fg):
    page_outline(p, 3.6, 2.8, 12.8, 14.4, 0.0)
    filled(p, fg, "rect", 3.6, 2.8, 12.8, 3.2)
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.5, 1.5])
    p.setPen(pen)
    p.drawRect(R(6.0, 8.4, 8.0, 6.4))


def g_update_template(p, fg):
    g_template(p, fg)
    badge_arrows(p, 15.0, 15.0)


# --------------------------------------------------------------------------- edit
def g_select_all(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.2)
    pen.setDashPattern([1.6, 1.6])
    p.setPen(pen)
    p.drawRect(R(4.0, 4.0, 12.0, 12.0))
    for x, y in ((4.0, 4.0), (16.0, 4.0), (4.0, 16.0), (16.0, 16.0)):
        filled(p, fg, "rect", x - 1.6, y - 1.6, 3.2, 3.2)


def _dashed_box(p, x, y, w, h, width_=1.2):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(width_)
    pen.setDashPattern([1.6, 1.6])
    p.setPen(pen)
    p.drawRect(R(x, y, w, h))


def g_select_none(p, fg):
    _dashed_box(p, 3.0, 3.0, 14.0, 14.0)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(7.0, 7.0, 13.0, 13.0), (13.0, 7.0, 7.0, 13.0)])
    p.restore()


def g_invert_selection(p, fg):
    _dashed_box(p, 2.6, 2.6, 9.0, 9.0)
    filled(p, fg, "rect", 8.4, 8.4, 9.0, 9.0)
    punch(p, R(8.4, 8.4, 3.1, 3.1))


def g_select_same(p, fg):
    _dashed_box(p, 2.4, 6.4, 7.0, 7.0)
    filled(p, accent(), "rect", 11.0, 3.0, 6.4, 6.4)
    filled(p, accent(), "rect", 11.0, 11.0, 6.4, 6.4)


def g_select_previous(p, fg):
    _dashed_box(p, 6.4, 6.4, 11.0, 11.0)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    path = QtGui.QPainterPath(P(12.0, 3.6))
    path.quadTo(P(4.0, 3.0), P(3.6, 10.0))
    p.drawPath(path)
    arrow_head(p, (3.6, 10.0), 95.0, 2.6)
    p.restore()


def g_hide_selection(p, fg):
    eye(p, fg)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.7, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(3.5, 16.5, 16.5, 3.5)])
    p.restore()


def g_goto(p, fg):
    width(p, 1.3)
    p.drawRect(R(3.2, 3.2, 9.0, 9.0))
    p.drawEllipse(P(12.6, 12.6), 4.0, 4.0)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(
        p,
        [(12.6, 6.6, 12.6, 9.6), (12.6, 15.6, 12.6, 18.6), (6.6, 12.6, 9.6, 12.6), (15.6, 12.6, 18.6, 12.6)],
    )
    p.restore()
    filled(p, accent(), "ellipse", 11.6, 11.6, 2.0, 2.0)


def g_find(p, fg):
    width(p, 1.2)
    lines(p, [(2.6, 4.2, 9.0, 4.2), (2.6, 7.4, 6.4, 7.4)])
    width(p, 1.6)
    magnifier(p, 11.0, 11.0, 4.4)


def g_replace(p, fg):
    width(p, 1.3)
    p.drawRect(R(2.6, 2.6, 6.6, 6.6))
    filled(p, fg, "rect", 10.8, 10.8, 6.6, 6.6)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    path = QtGui.QPainterPath(P(11.0, 4.6))
    path.quadTo(P(15.6, 4.6), P(15.6, 8.6))
    p.drawPath(path)
    arrow_head(p, (15.6, 8.6), 90.0, 2.4)
    path = QtGui.QPainterPath(P(9.0, 15.4))
    path.quadTo(P(4.4, 15.4), P(4.4, 11.4))
    p.drawPath(path)
    arrow_head(p, (4.4, 11.4), -90.0, 2.4)
    p.restore()


def _padlock_body(p, fg):
    filled(p, fg, "rrect", 4.5, 9.0, 11.0, 8.0, 1.4)


def g_unlock(p, fg):
    pen = QtGui.QPen(fg, 1.8)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    path = QtGui.QPainterPath(P(6.5, 9.0))
    path.lineTo(6.5, 6.0)
    path.arcTo(R(6.5, 2.4, 7.0, 7.0), 180.0, -150.0)
    p.drawPath(path)
    _padlock_body(p, fg)


def g_lock_mode(p, fg):
    # Break Lock Mode: a padlock struck through
    pen = QtGui.QPen(fg, 1.8)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    path = QtGui.QPainterPath(P(6.5, 9.0))
    path.lineTo(6.5, 6.6)
    path.arcTo(R(6.5, 3.0, 7.0, 7.0), 180.0, -180.0)
    path.lineTo(13.5, 9.0)
    p.drawPath(path)
    _padlock_body(p, fg)
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.setPen(QtGui.QPen(QtCore.Qt.black, 3.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    p.drawLine(P(3.0, 18.0), P(17.0, 2.5))
    p.restore()
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    p.drawLine(P(3.0, 18.0), P(17.0, 2.5))


def g_puzzle(p, fg):
    p.drawPath(puzzle_path(3.4, 3.0, 13.4))


def g_paste_genie(p, fg):
    p.drawPath(puzzle_path(2.6, 2.6, 12.6))
    plus(p, 15.4, 15.4)


def g_composite_genie(p, fg):
    p.drawPath(puzzle_path(2.4, 2.4, 10.0))
    path = puzzle_path(7.6, 7.2, 10.4)
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.setBrush(QtCore.Qt.black)
    p.setPen(QtGui.QPen(QtCore.Qt.black, 2.4))
    p.drawPath(path)
    p.restore()
    p.drawPath(path)


def g_paste_composite(p, fg):
    p.drawPath(puzzle_path(2.0, 2.0, 8.6))
    p.drawPath(puzzle_path(6.6, 6.4, 8.6))
    plus(p, 15.6, 15.6)


def g_super_genie(p, fg):
    p.drawPath(puzzle_path(2.4, 5.0, 12.2))
    punch_ellipse(p, R(12.0, 1.0, 7.0, 7.0))
    star(p, 15.5, 4.5, 3.2, accent())


def g_paste_special(p, fg):
    p.drawRoundedRect(R(3.4, 4.2, 10.8, 13.2), 1.6, 1.6)
    filled(p, fg, "rect", 6.2, 2.8, 5.2, 2.8)
    punch_ellipse(p, R(10.0, 9.0, 9.0, 9.0))
    star(p, 14.5, 13.5, 3.8, accent())


def g_paste_symbol(p, fg):
    width(p, 1.4)
    for x, y in ((3.0, 3.0), (9.6, 3.0), (3.0, 9.6)):
        p.drawRect(R(x, y, 4.6, 4.6))
    plus(p, 14.6, 14.6, 3.0)


def g_flashing(p, fg):
    filled(p, fg, "poly", [(11.6, 2.4), (4.6, 11.2), (9.4, 11.2), (8.0, 17.6), (15.4, 8.4), (10.4, 8.4)])


def g_paste_flashing(p, fg):
    filled(p, fg, "poly", [(10.4, 2.0), (3.6, 10.4), (8.2, 10.4), (6.8, 16.4), (13.8, 7.6), (9.2, 7.6)])
    plus(p, 15.4, 15.4)


def star(p, cx, cy, r, colour, inner=0.45):
    pts = []
    for k in range(10):
        ang = math.radians(-90 + 36 * k)
        rr = r if k % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    filled(p, colour, "poly", pts)


def brush(p, fg, x=10.0, y=2.6):
    """A paint brush: head at the top, handle down."""
    p.drawRoundedRect(R(x - 4.6, y, 9.2, 5.0), 1.2, 1.2)
    filled(p, fg, "rect", x - 4.6, y + 5.0, 9.2, 1.8)
    width(p, 2.4)
    lines(p, [(x, y + 7.0, x, y + 14.4)])


def g_copy_appearance(p, fg):
    brush(p, fg, 8.4, 2.6)
    plus(p, 15.6, 15.4, 2.4)


def g_paste_appearance(p, fg):
    brush(p, fg, 7.0, 2.0)
    filled(p, accent(), "rrect", 10.6, 12.2, 7.4, 4.8, 1.0)


def g_styles(p, fg):
    width(p, 1.3)
    for k, y in enumerate((3.4, 8.4, 13.4)):
        p.drawRect(R(3.0, y, 3.6, 3.6))
        lines(p, [(9.0, y + 1.8, 17.0 - k * 2.0, y + 1.8)])
    filled(p, fg, "rect", 3.0, 8.4, 3.6, 3.6)


def g_save_style(p, fg):
    g_styles(p, fg)
    plus(p, 15.4, 15.4, 2.6)


# --------------------------------------------------------------------------- objects
def g_string(p, fg):
    letter(p, "abc", 9.0, True)
    width(p, 1.2)
    lines(p, [(3.0, 15.6, 17.0, 15.6)])


def g_digital_text(p, fg):
    width(p, 1.3)
    p.drawRoundedRect(R(2.4, 4.4, 15.2, 11.2), 1.6, 1.6)
    letter(p, "01", 8.5, True)


def g_colour_flood(p, fg):
    # a paint bucket pouring
    p.save()
    p.translate(9.0, 10.0)
    p.rotate(-35.0)
    p.drawRoundedRect(R(-4.8, -4.6, 9.6, 9.6), 1.4, 1.4)
    lines(p, [(-4.8, -1.4, 4.8, -1.4)])
    p.restore()
    filled(p, accent(), "poly", [(15.2, 9.6), (17.2, 14.0), (15.2, 15.8), (13.2, 14.0)])


def g_bar_graph(p, fg):
    lines(p, [(2.8, 17.0, 17.2, 17.0)])
    for x, top in ((4.4, 9.0), (8.4, 4.0), (12.4, 11.0)):
        filled(p, fg, "rect", x, top, 3.0, 15.6 - top)


def g_slider(p, fg):
    lines(p, [(3.0, 10.0, 17.0, 10.0)])
    filled(p, fg, "rrect", 9.0, 5.6, 4.0, 8.8, 1.2)
    width(p, 1.1)
    lines(p, [(3.0, 14.6, 3.0, 16.0), (17.0, 14.6, 17.0, 16.0), (10.0, 15.8, 10.0, 16.8)])


def g_animated_symbol(p, fg):
    p.drawRect(R(7.0, 4.0, 10.0, 10.0))
    width(p, 1.2)
    lines(p, [(2.4, 6.4, 5.2, 6.4), (3.4, 9.0, 5.2, 9.0), (2.4, 11.6, 5.2, 11.6)])
    filled(p, fg, "poly", [(10.2, 6.6), (14.6, 9.0), (10.2, 11.4)])


def keyboard(p, fg, y=5.4):
    p.drawRoundedRect(R(2.2, y, 15.6, 9.6), 1.6, 1.6)
    for row, yy in enumerate((y + 2.4, y + 4.8)):
        for k in range(5):
            filled(p, fg, "rect", 4.2 + k * 2.6 + (row * 1.2), yy - 0.6, 1.4, 1.3)
    width(p, 1.3)
    lines(p, [(6.6, y + 7.4, 13.4, y + 7.4)])


def g_keyboard(p, fg):
    keyboard(p, fg)


def g_keyboard_page(p, fg):
    width(p, 1.2)
    page_outline(p, 3.4, 1.6, 13.2, 9.4, 2.4)
    width(p, 1.6)
    punch(p, R(1.8, 9.0, 16.4, 9.8))
    keyboard(p, fg, 8.6)


def g_cicode_object(p, fg):
    width(p, 1.2)
    p.drawRoundedRect(R(2.2, 3.4, 15.6, 13.2), 2.0, 2.0)
    letter(p, "f(x)", 8.6, True, True)


def g_process_analyst(p, fg):
    # a chart pane: a frame with a legend column and a pen trace
    width(p, 1.2)
    p.drawRect(R(2.6, 3.4, 14.8, 13.2))
    lines(p, [(12.6, 3.4, 12.6, 16.6)])
    for y in (6.4, 9.0, 11.6):
        lines(p, [(14.0, y, 16.2, y)])
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(3.8, 13.6), (6.0, 9.2), (8.2, 11.6), (11.2, 6.2)])
    p.restore()


def g_scheduler(p, fg):
    # a calendar
    width(p, 1.3)
    p.drawRoundedRect(R(2.8, 4.0, 14.4, 13.0), 1.4, 1.4)
    filled(p, fg, "rect", 2.8, 4.0, 14.4, 3.2)
    width(p, 1.6)
    lines(p, [(6.6, 2.4, 6.6, 5.4), (13.4, 2.4, 13.4, 5.4)])
    for r, y in enumerate((10.0, 13.6)):
        for k in range(4):
            colour = accent() if (r, k) == (1, 2) else fg
            filled(p, colour, "rect", 4.6 + k * 3.0, y - 0.9, 1.8, 1.8)


def g_db_exchange(p, fg):
    # a database cylinder
    width(p, 1.4)
    p.drawEllipse(R(4.0, 2.8, 12.0, 4.4))
    lines(p, [(4.0, 5.0, 4.0, 15.0), (16.0, 5.0, 16.0, 15.0)])
    for y in (9.6, 14.6):
        path = QtGui.QPainterPath()
        path.arcMoveTo(R(4.0, y - 2.2, 12.0, 4.4), 180.0)
        path.arcTo(R(4.0, y - 2.2, 12.0, 4.4), 180.0, 180.0)
        p.drawPath(path)


def g_pelco(p, fg):
    # a dome camera under a ceiling plate
    lines(p, [(2.6, 5.2, 17.4, 5.2)])
    path = QtGui.QPainterPath(P(4.0, 5.2))
    path.arcTo(R(4.0, -0.8, 12.0, 12.0 + 3.6), 180.0, 180.0)
    p.drawPath(path)
    filled(p, accent(), "ellipse", 10.4, 8.8, 3.4, 3.4)


def g_picture(p, fg):
    picture(p)


def g_polyline(p, fg):
    poly(p, [(3.4, 15.6), (7.2, 5.0), (12.4, 12.2), (16.6, 4.4)])
    for x, y in ((3.4, 15.6), (7.2, 5.0), (12.4, 12.2), (16.6, 4.4)):
        filled(p, fg, "rect", x - 1.5, y - 1.5, 3.0, 3.0)


# --------------------------------------------------------------------------- arrange
def g_rotate_cw(p, fg):
    filled(p, fg, "rect", 2.8, 11.0, 6.6, 6.6)
    arc_arrow(p, R(4.6, 3.0, 12.4, 12.4), 165.0, -165.0, 3.0)


def g_rotate_ccw(p, fg):
    p.translate(20.0, 0.0)
    p.scale(-1.0, 1.0)
    g_rotate_cw(p, fg)


def g_flip_h(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.6, 1.6])
    p.setPen(pen)
    p.drawLine(P(10.0, 2.4), P(10.0, 17.6))
    width(p, 1.4)
    p.setPen(QtGui.QPen(fg, 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(8.0, 5.0), (8.0, 15.0), (2.6, 15.0)], closed=True)
    filled(p, fg, "poly", [(12.0, 5.0), (12.0, 15.0), (17.4, 15.0)])


def g_flip_v(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.6, 1.6])
    p.setPen(pen)
    p.drawLine(P(2.4, 10.0), P(17.6, 10.0))
    p.setPen(QtGui.QPen(fg, 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(5.0, 8.0), (15.0, 8.0), (15.0, 2.6)], closed=True)
    filled(p, fg, "poly", [(5.0, 12.0), (15.0, 12.0), (15.0, 17.4)])


def g_adjust_position(p, fg):
    p.drawRect(R(6.4, 6.4, 10.2, 10.2))
    width(p, 1.1)
    lines(
        p,
        [
            (6.4, 3.2, 16.6, 3.2),
            (6.4, 2.0, 6.4, 4.4),
            (16.6, 2.0, 16.6, 4.4),
            (3.2, 6.4, 3.2, 16.6),
            (2.0, 6.4, 4.4, 6.4),
            (2.0, 16.6, 4.4, 16.6),
        ],
    )


def g_align_dialog(p, fg):
    filled(p, fg, "rect", 3.4, 3.4, 5.2, 5.2)
    filled(p, fg, "rect", 11.4, 11.4, 5.2, 5.2)
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(6.0, 1.8, 6.0, 18.2), (1.8, 14.0, 18.2, 14.0)])


def g_convert_bitmap(p, fg):
    width(p, 1.3)
    p.drawEllipse(R(2.2, 2.2, 8.0, 8.0))
    for r in range(3):
        for c in range(3):
            if (r + c) % 2 == 0:
                filled(p, fg, "rect", 9.6 + c * 2.8, 9.6 + r * 2.8, 2.8, 2.8)
    width(p, 1.0)
    p.drawRect(R(9.6, 9.6, 8.4, 8.4))


def chain_link(p, cx, cy, ang):
    p.save()
    p.translate(cx, cy)
    p.rotate(ang)
    p.drawRoundedRect(R(-4.4, -2.4, 8.8, 4.8), 2.4, 2.4)
    p.restore()


def g_link(p, fg):
    chain_link(p, 7.4, 12.6, -45.0)
    chain_link(p, 12.6, 7.4, -45.0)


def g_cut_link(p, fg):
    chain_link(p, 6.0, 14.0, -45.0)
    chain_link(p, 14.0, 6.0, -45.0)
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(9.0, 6.2, 9.6, 8.4), (13.8, 11.0, 11.6, 10.4), (6.2, 9.0, 8.4, 9.6), (11.0, 13.8, 10.4, 11.6)])


def g_link_off(p, fg):
    g_cut_link(p, fg)


def g_genie_parameters(p, fg):
    width(p, 1.4)
    for y, x in ((5.0, 12.4), (10.0, 6.4), (15.0, 10.4)):
        lines(p, [(2.6, y, 17.4, y)])
        punch_ellipse(p, R(x - 2.2, y - 2.2, 4.4, 4.4))
        filled(p, fg, "ellipse", x - 2.0, y - 2.0, 4.0, 4.0)


# --------------------------------------------------------------------------- colours
def g_swap_colours(p, fg):
    filled(p, fg, "rect", 2.6, 2.6, 7.0, 7.0)
    p.drawRect(R(10.4, 10.4, 7.0, 7.0))
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    path = QtGui.QPainterPath(P(12.0, 4.2))
    path.quadTo(P(16.0, 4.2), P(16.0, 8.0))
    p.drawPath(path)
    arrow_head(p, (16.0, 8.0), 90.0, 2.2)
    path = QtGui.QPainterPath(P(8.0, 15.8))
    path.quadTo(P(4.0, 15.8), P(4.0, 12.0))
    p.drawPath(path)
    arrow_head(p, (4.0, 12.0), -90.0, 2.2)


def g_adjust_colours(p, fg):
    p.drawEllipse(R(3.0, 3.0, 14.0, 14.0))
    path = QtGui.QPainterPath(P(10.0, 3.0))
    path.arcTo(R(3.0, 3.0, 14.0, 14.0), 90.0, -180.0)
    path.closeSubpath()
    p.save()
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawPath(path)
    p.restore()


def g_favourite_colours(p, fg):
    pts = []
    for k in range(10):
        ang = math.radians(-90 + 36 * k)
        rr = 7.4 if k % 2 == 0 else 3.2
        pts.append((10.0 + rr * math.cos(ang), 10.6 + rr * math.sin(ang)))
    p.setPen(QtGui.QPen(fg, 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, pts, closed=True)


def g_transparent_colour(p, fg):
    for r in range(4):
        for c in range(4):
            if (r + c) % 2 == 0:
                filled(p, fg, "rect", 4.0 + c * 3.0, 4.0 + r * 3.0, 3.0, 3.0)
    width(p, 1.1)
    p.drawRect(R(4.0, 4.0, 12.0, 12.0))


# --------------------------------------------------------------------------- libraries
def g_library(p, fg):
    books(p)


def g_copy_to_library(p, fg):
    books(p, 2.6, 5.4, 11.6)
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(15.6, 1.8, 15.6, 9.6)])
    arrow_head(p, (15.6, 9.6), 90.0, 2.8)


def g_pack_libraries(p, fg):
    width(p, 1.4)
    p.drawRect(R(7.0, 4.0, 2.8, 12.0))
    p.drawRect(R(10.2, 4.0, 2.8, 12.0))
    p.setPen(QtGui.QPen(accent(), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(1.6, 10.0, 5.2, 10.0), (18.4, 10.0, 14.8, 10.0)])
    arrow_head(p, (5.2, 10.0), 0.0, 2.4)
    arrow_head(p, (14.8, 10.0), 180.0, 2.4)


def g_sync(p, fg):
    rect = R(3.6, 3.6, 12.8, 12.8)
    arc_arrow(p, rect, 110.0, 150.0, 2.8)
    arc_arrow(p, rect, 290.0, 150.0, 2.8)


def g_update_pages(p, fg):
    page_outline(p, 3.0, 2.6, 10.6, 13.8)
    width(p, 1.1)
    lines(p, [(5.4, 7.0, 10.6, 7.0), (5.4, 9.6, 9.0, 9.6)])
    width(p, 1.6)
    badge_arrows(p, 14.4, 14.4, 3.6)


def g_used_by(p, fg):
    filled(p, fg, "rect", 2.4, 7.6, 4.8, 4.8)
    width(p, 1.3)
    lines(
        p,
        [
            (7.2, 10.0, 10.4, 10.0),
            (10.4, 4.0, 10.4, 16.0),
            (10.4, 4.0, 12.8, 4.0),
            (10.4, 10.0, 12.8, 10.0),
            (10.4, 16.0, 12.8, 16.0),
        ],
    )
    for y in (4.0, 10.0, 16.0):
        p.drawRect(R(13.0, y - 1.8, 3.8, 3.6))


def g_update_users(p, fg):
    g_used_by(p, fg)
    badge_arrows(p, 6.0, 15.4, 3.2)


def g_genie_form(p, fg):
    window_frame(p, 2.4, 2.8, 15.2, 14.4, 2.6)
    width(p, 1.1)
    for y in (8.6, 12.8):
        lines(p, [(4.4, y, 7.4, y)])
        p.drawRect(R(8.8, y - 1.5, 6.8, 3.0))


def g_hotspot(p, fg):
    width(p, 1.4)
    p.drawEllipse(P(10.0, 10.0), 6.2, 6.2)
    lines(
        p,
        [(10.0, 1.8, 10.0, 6.0), (10.0, 14.0, 10.0, 18.2), (1.8, 10.0, 6.0, 10.0), (14.0, 10.0, 18.2, 10.0)],
    )
    filled(p, accent(), "ellipse", 8.4, 8.4, 3.2, 3.2)


# --------------------------------------------------------------------------- pictures
def g_bitmap_editor(p, fg):
    for r in range(3):
        for c in range(3):
            if (r + c) % 2 == 1:
                filled(p, fg, "rect", 2.6 + c * 3.4, 6.2 + r * 3.4, 3.4, 3.4)
    width(p, 1.0)
    p.drawRect(R(2.6, 6.2, 10.2, 10.2))
    width(p, 1.6)
    punch(p, R(11.0, 2.0, 7.0, 9.0))
    pencil(p, 11.6, 9.6, 17.6, 2.8, 2.8)


def g_replace_picture(p, fg):
    picture(p, 2.4, 2.6, 12.0, 10.0)
    badge_arrows(p, 15.0, 15.0, 3.6)


def g_export_picture(p, fg):
    picture(p, 2.2, 3.6, 11.4, 10.0)
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(10.6, 16.6, 18.0, 16.6)])
    arrow_head(p, (18.0, 16.6), 0.0, 2.8)


def g_resample(p, fg):
    picture(p, 2.4, 2.4, 10.6, 9.0)
    p.setPen(QtGui.QPen(accent(), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(10.4, 10.4, 17.4, 17.4)])
    arrow_head(p, (17.4, 17.4), 45.0, 3.0)
    arrow_head(p, (10.4, 10.4), 225.0, 3.0)


def g_duplicates(p, fg):
    picture(p, 2.2, 2.2, 10.0, 8.4)
    punch(p, R(7.6, 8.6, 10.2, 8.6))
    picture(p, 7.6, 8.6, 10.2, 8.6)


# --------------------------------------------------------------------------- external tools
def g_studio(p, fg):
    width(p, 1.4)
    p.drawRoundedRect(R(2.2, 3.0, 15.6, 11.0), 1.4, 1.4)
    lines(p, [(7.4, 17.0, 12.6, 17.0), (10.0, 14.0, 10.0, 17.0)])
    for x, y in ((4.6, 5.4), (10.4, 5.4), (4.6, 9.4)):
        filled(p, fg, "rect", x, y, 5.0, 3.0)
    filled(p, accent(), "rect", 10.4, 9.4, 5.0, 3.0)


def g_cicode_editor(p, fg):
    letter(p, "{ }", 13.0, True)


def g_runtime_manager(p, fg):
    width(p, 1.3)
    for y in (3.0, 8.0, 13.0):
        p.drawRoundedRect(R(2.6, y, 14.8, 4.0), 1.0, 1.0)
        filled(p, accent() if y == 3.0 else fg, "ellipse", 4.4, y + 1.2, 1.6, 1.6)
        lines(p, [(8.0, y + 2.0, 14.6, y + 2.0)])


def g_setup_wizard(p, fg):
    width(p, 2.0)
    lines(p, [(3.4, 16.6, 12.0, 8.0)])
    star(p, 14.4, 5.6, 3.6, accent())
    filled(p, fg, "ellipse", 5.0, 3.2, 1.8, 1.8)
    filled(p, fg, "ellipse", 16.2, 13.0, 1.8, 1.8)


def gear(p, cx, cy, r, w=1.4, teeth=8):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(w)
    p.save()
    p.setPen(pen)
    p.drawEllipse(P(cx, cy), r * 0.62, r * 0.62)
    for k in range(teeth):
        a = 2 * math.pi * k / teeth
        p.drawLine(
            P(cx + r * 0.62 * math.cos(a), cy + r * 0.62 * math.sin(a)),
            P(cx + r * math.cos(a), cy + r * math.sin(a)),
        )
    p.drawEllipse(P(cx, cy), r * 0.22, r * 0.22)
    p.restore()


def g_settings(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.drawEllipse(P(10.0, 10.0), 2.4, 2.4)
    path = QtGui.QPainterPath()
    teeth = 8
    for k in range(teeth * 2):
        a0 = 2 * math.pi * k / (teeth * 2)
        a1 = 2 * math.pi * (k + 1) / (teeth * 2)
        rr = 7.2 if k % 2 == 0 else 5.2
        pa = P(10.0 + rr * math.cos(a0), 10.0 + rr * math.sin(a0))
        pb = P(10.0 + rr * math.cos(a1), 10.0 + rr * math.sin(a1))
        if k == 0:
            path.moveTo(pa)
        else:
            path.lineTo(pa)
        path.lineTo(pb)
    path.closeSubpath()
    p.drawPath(path)


def g_setup_editor(p, fg):
    width(p, 1.4)
    p.drawRoundedRect(R(2.2, 3.0, 13.0, 9.6), 1.4, 1.4)
    lines(p, [(5.4, 15.8, 9.8, 15.8), (7.6, 12.6, 7.6, 15.8)])
    punch_ellipse(p, R(10.6, 9.6, 9.0, 9.0))
    gear(p, 15.1, 14.1, 3.6, 1.3)


# --------------------------------------------------------------------------- text
def g_font(p, fg):
    width(p, 1.6)
    lines(p, [(3.6, 16.0, 8.4, 3.6), (8.4, 3.6, 13.2, 16.0), (5.4, 11.6, 11.4, 11.6)])
    p.setPen(QtGui.QPen(accent(), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(14.6, 9.0, 17.4, 9.0), (14.6, 12.4, 17.4, 12.4), (14.6, 15.8, 17.4, 15.8)])


def g_edit_in_place(p, fg):
    width(p, 1.4)
    lines(p, [(2.6, 5.0, 10.0, 5.0), (6.3, 5.0, 6.3, 15.0)])
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    lines(p, [(14.0, 3.6, 14.0, 16.4), (12.2, 3.6, 15.8, 3.6), (12.2, 16.4, 15.8, 16.4)])


def g_bold(p, fg):
    letter(p, "B", 17.0, True)


def g_italic(p, fg):
    letter(p, "I", 17.0, True, True)


def g_underline(p, fg):
    letter(p, "U", 15.0, True, False, R(0.0, -1.6, 20.0, 20.0))
    width(p, 1.6)
    lines(p, [(4.6, 17.2, 15.4, 17.2)])


def g_strikeout(p, fg):
    letter(p, "S", 16.0, True)
    width(p, 1.5)
    lines(p, [(3.4, 10.6, 16.6, 10.6)])


def _justify(p, xs):
    width(p, 1.6)
    for (x1, x2), y in zip(xs, (4.6, 8.2, 11.8, 15.4)):
        lines(p, [(x1, y, x2, y)])


def g_justify_left(p, fg):
    _justify(p, [(3.0, 17.0), (3.0, 12.0), (3.0, 15.0), (3.0, 10.0)])


def g_justify_centre(p, fg):
    _justify(p, [(3.0, 17.0), (5.6, 14.4), (4.2, 15.8), (6.8, 13.2)])


def g_justify_right(p, fg):
    _justify(p, [(3.0, 17.0), (8.0, 17.0), (5.0, 17.0), (10.0, 17.0)])


def g_effect_normal(p, fg):
    letter(p, "A", 16.0, True)


def g_effect_shadowed(p, fg):
    shade = QtGui.QColor(fg)
    shade.setAlphaF(0.45)
    p.setPen(shade)
    letter(p, "A", 16.0, True, rect=R(1.4, 1.4, 20.0, 20.0))
    p.setPen(fg)
    letter(p, "A", 16.0, True, rect=R(-0.7, -0.7, 20.0, 20.0))


def _bevel(p, fg, raised):
    light, dark = QtGui.QColor(fg), QtGui.QColor(fg)
    light.setAlphaF(1.0 if raised else 0.4)
    dark.setAlphaF(0.4 if raised else 1.0)
    p.setPen(QtGui.QPen(light, 2.0, QtCore.Qt.SolidLine, QtCore.Qt.SquareCap))
    poly(p, [(4.0, 16.0), (4.0, 4.0), (16.0, 4.0)])
    p.setPen(QtGui.QPen(dark, 2.0, QtCore.Qt.SolidLine, QtCore.Qt.SquareCap))
    poly(p, [(16.0, 4.0), (16.0, 16.0), (4.0, 16.0)])


def g_effect_raised(p, fg):
    _bevel(p, fg, True)


def g_effect_lowered(p, fg):
    _bevel(p, fg, False)


#: the letter of the Embossed effect icon, in glyph units (icon art, not a text size)
EMBOSSED_LETTER_PX = 15


def g_effect_embossed(p, fg):
    path = QtGui.QPainterPath()
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(EMBOSSED_LETTER_PX)
    font.setBold(True)
    path.addText(P(4.8, 15.6), font, "A")
    p.setPen(QtGui.QPen(fg, 1.0))
    p.drawPath(path)
    shade = QtGui.QColor(fg)
    shade.setAlphaF(0.45)
    p.translate(1.0, 1.0)
    p.setPen(QtGui.QPen(shade, 0.9))
    p.drawPath(path)


def g_aliased_text(p, fg):
    letter(p, "A", 11.0, True, rect=R(-3.0, -3.0, 20.0, 20.0))
    for k in range(4):
        filled(p, fg, "rect", 9.0 + k * 2.0, 15.0 - k * 2.0, 2.0, 2.0)


# --------------------------------------------------------------------------- view
def g_zoom_100(p, fg):
    magnifier(p)
    letter(p, "1", 7.5, True, rect=R(3.8, 3.8, 10.0, 10.0))


def _corners(p, inset=4.0, arm=3.4):
    a, b = inset, 20.0 - inset
    for seg in (
        ((a, a + arm), (a, a), (a + arm, a)),
        ((b - arm, a), (b, a), (b, a + arm)),
        ((b, b - arm), (b, b), (b - arm, b)),
        ((a + arm, b), (a, b), (a, b - arm)),
    ):
        poly(p, seg)


def g_fit_all(p, fg):
    _corners(p, 3.0)
    filled(p, fg, "rect", 6.4, 6.4, 3.6, 3.6)
    filled(p, fg, "ellipse", 10.6, 10.6, 3.2, 3.2)


def g_zoom_selection(p, fg):
    _corners(p, 3.0)
    pen = QtGui.QPen(accent(), 1.2)
    pen.setDashPattern([1.5, 1.5])
    p.setPen(pen)
    p.drawRect(R(6.6, 6.6, 6.8, 6.8))


def g_zoom_box(p, fg):
    magnifier(p, 11.4, 11.4, 4.4)
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.5, 1.5])
    p.setPen(pen)
    p.drawRect(R(2.4, 2.4, 7.0, 6.0))


def g_magnifier(p, fg):
    width(p, 1.2)
    p.drawRect(R(2.4, 3.4, 15.2, 13.2))
    width(p, 1.6)
    magnifier(p, 9.0, 9.4, 3.6)


def g_isolate(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.4, 1.6])
    p.setPen(pen)
    p.drawRect(R(2.4, 2.4, 4.4, 4.4))
    p.drawRect(R(13.2, 13.2, 4.4, 4.4))
    p.drawEllipse(R(13.2, 2.4, 4.4, 4.4))
    filled(p, fg, "rect", 6.6, 6.6, 6.8, 6.8)


def eye(p, fg, cx=10.0, cy=10.0, w=15.0):
    path = QtGui.QPainterPath(P(cx - w / 2.0, cy))
    path.quadTo(P(cx, cy - w * 0.48), P(cx + w / 2.0, cy))
    path.quadTo(P(cx, cy + w * 0.48), P(cx - w / 2.0, cy))
    p.drawPath(path)
    filled(p, fg, "ellipse", cx - 2.4, cy - 2.4, 4.8, 4.8)


def g_show_hidden(p, fg):
    eye(p, fg)


def g_eye(p, fg):
    """The Objects panel's eye: the object shows in the editor."""
    eye(p, fg)


def g_eye_off(p, fg):
    """Hidden in the editor: the eye struck through (a gap each side of the stroke)."""
    path = QtGui.QPainterPath(P(2.5, 10.0))
    path.quadTo(P(10.0, 2.8), P(17.5, 10.0))
    path.quadTo(P(10.0, 17.2), P(2.5, 10.0))
    p.drawPath(path)
    p.save()
    p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
    p.setPen(QtGui.QPen(QtCore.Qt.black, 3.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    p.drawLine(QtCore.QLineF(4.0, 16.0, 16.0, 4.0))
    p.restore()
    p.setPen(QtGui.QPen(fg, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    p.drawLine(QtCore.QLineF(4.0, 16.0, 16.0, 4.0))


def g_rename(p, fg):
    """Rename: a name field with the text cursor in it."""
    width(p, 1.3)
    p.drawRoundedRect(R(2.4, 6.0, 15.2, 8.0), 1.6, 1.6)
    width(p, 1.5)
    lines(
        p, [(5.2, 10.0, 9.6, 10.0), (12.6, 3.6, 12.6, 16.4), (11.0, 3.6, 14.2, 3.6), (11.0, 16.4, 14.2, 16.4)]
    )


def g_mark_changes(p, fg):
    page_outline(p)
    filled(p, accent(), "ellipse", 7.4, 8.6, 5.2, 5.2)


def _change_step(p, fg, down: bool) -> None:
    """A page with the accent dot and an arrow to the next (previous) change."""
    page_outline(p, 2.6, 2.8, 9.6, 14.4, 2.8)
    filled(p, accent(), "ellipse", 5.0, 8.0, 4.4, 4.4)
    width(p, 1.6)
    y0, y1 = (6.0, 15.6) if down else (15.6, 6.0)
    p.drawLine(QtCore.QLineF(15.6, y0, 15.6, y1))
    arrow_head(p, (15.6, y1), 90.0 if down else -90.0, 3.0)


def g_next_change(p, fg):
    _change_step(p, fg, True)


def g_prev_change(p, fg):
    _change_step(p, fg, False)


def g_select_changed(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setDashPattern([2.0, 1.6])
    p.setPen(pen)
    p.drawRect(R(2.6, 2.6, 14.8, 14.8))
    filled(p, accent(), "ellipse", 6.6, 6.6, 6.8, 6.8)


def g_page_history(p, fg):
    # a clock with an arrow turning back round it
    arc_arrow(p, R(3.0, 3.0, 14.0, 14.0), -60.0, -280.0, 2.8)
    lines(p, [(10.0, 6.4, 10.0, 10.4), (10.0, 10.4, 12.8, 12.2)])


def g_guidelines(p, fg):
    width(p, 1.2)
    p.drawRect(R(2.6, 2.6, 14.8, 14.8))
    pen = QtGui.QPen(accent(), 1.3)
    pen.setDashPattern([1.8, 1.4])
    p.setPen(pen)
    lines(p, [(7.4, 2.6, 7.4, 17.4), (2.6, 12.0, 17.4, 12.0)])


def g_snap_guides(p, fg):
    pen = QtGui.QPen(accent(), 1.3)
    pen.setDashPattern([1.8, 1.4])
    p.setPen(pen)
    lines(p, [(4.6, 1.8, 4.6, 18.2)])
    p.setPen(QtGui.QPen(fg, 2.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    path = QtGui.QPainterPath(P(16.4, 6.2))
    path.lineTo(11.6, 6.2)
    path.arcTo(R(7.8, 6.2, 7.6, 7.6), 90.0, 180.0)
    path.lineTo(16.4, 13.8)
    p.drawPath(path)


def g_crosshair(p, fg):
    width(p, 1.3)
    lines(
        p,
        [(10.0, 1.6, 10.0, 7.6), (10.0, 12.4, 10.0, 18.4), (1.6, 10.0, 7.6, 10.0), (12.4, 10.0, 18.4, 10.0)],
    )
    filled(p, accent(), "ellipse", 9.0, 9.0, 2.0, 2.0)


def g_labels(p, fg):
    width(p, 1.4)
    poly(p, [(2.6, 6.0), (12.6, 6.0), (17.4, 10.0), (12.6, 14.0), (2.6, 14.0)], closed=True)
    filled(p, fg, "ellipse", 11.6, 9.0, 2.0, 2.0)


def g_variable_tags(p, fg):
    width(p, 1.4)
    poly(p, [(2.6, 5.0), (11.6, 5.0), (16.4, 10.0), (11.6, 15.0), (2.6, 15.0)], closed=True)
    letter(p, "#", 8.0, True, rect=R(1.8, 0.4, 12.0, 20.0))


def g_focus_mode(p, fg):
    _corners(p, 2.8, 4.0)
    filled(p, accent(), "ellipse", 7.0, 7.0, 6.0, 6.0)


def g_reset_layout(p, fg):
    width(p, 1.3)
    p.drawRect(R(2.4, 2.4, 11.6, 10.4))
    lines(p, [(6.4, 2.4, 6.4, 12.8)])
    punch_ellipse(p, R(9.4, 9.4, 9.4, 9.4))
    p.setPen(QtGui.QPen(accent(), 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    arc_arrow(p, R(10.6, 10.6, 7.0, 7.0), 340.0, -260.0, 2.2)


def g_help(p, fg):
    width(p, 1.5)
    p.drawEllipse(P(10.0, 10.0), 7.4, 7.4)
    letter(p, "?", 11.0, True, rect=R(0.0, -0.4, 20.0, 20.0))


def g_toolbox(p, fg):
    width(p, 1.4)
    p.drawRoundedRect(R(2.4, 7.0, 15.2, 10.2), 1.4, 1.4)
    poly(p, [(7.0, 7.0), (7.0, 4.0), (13.0, 4.0), (13.0, 7.0)])
    lines(p, [(2.4, 11.0, 17.6, 11.0)])
    filled(p, fg, "rect", 8.8, 9.8, 2.4, 2.6)


def g_status_bar(p, fg):
    width(p, 1.3)
    p.drawRect(R(2.4, 3.0, 15.2, 14.0))
    filled(p, fg, "rect", 2.4, 13.6, 15.2, 3.4)


def g_selection_toolbar(p, fg):
    pen = QtGui.QPen(p.pen())
    pen.setWidthF(1.1)
    pen.setDashPattern([1.5, 1.5])
    p.setPen(pen)
    p.drawRect(R(3.0, 2.6, 14.0, 8.0))
    filled(p, fg, "rrect", 4.4, 12.8, 11.2, 4.8, 2.0)
    for x in (6.6, 9.4, 12.2):
        punch_ellipse(p, R(x, 14.4, 1.6, 1.6))


def g_cascade(p, fg):
    width(p, 1.3)
    window_frame(p, 2.2, 2.2, 10.0, 8.0, 2.0)
    punch(p, R(7.8, 9.0, 10.0, 8.6))
    window_frame(p, 7.8, 9.0, 10.0, 8.6, 2.0)


def g_tile_h(p, fg):
    width(p, 1.3)
    window_frame(p, 3.0, 2.4, 14.0, 6.6, 1.8)
    window_frame(p, 3.0, 11.0, 14.0, 6.6, 1.8)


def g_tile_v(p, fg):
    width(p, 1.3)
    window_frame(p, 2.4, 3.0, 6.6, 14.0, 1.8)
    window_frame(p, 11.0, 3.0, 6.6, 14.0, 1.8)


def g_mouse(p, fg):
    width(p, 1.4)
    p.drawRoundedRect(R(5.4, 2.6, 9.2, 14.8), 4.6, 4.6)
    lines(p, [(10.0, 2.8, 10.0, 7.8), (5.6, 7.8, 14.4, 7.8)])


def g_workspace_check(p, fg):
    width(p, 1.3)
    p.drawRect(R(2.2, 2.6, 12.6, 11.4))
    lines(p, [(7.6, 2.6, 7.6, 14.0), (7.6, 8.2, 14.8, 8.2)])
    punch_ellipse(p, R(10.0, 10.4, 9.4, 9.0))
    p.setPen(QtGui.QPen(accent(), 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(11.6, 14.8), (13.8, 17.0), (18.0, 12.4)])


def g_check_display(p, fg):
    width(p, 1.4)
    p.drawRoundedRect(R(2.2, 3.0, 15.6, 10.8), 1.4, 1.4)
    lines(p, [(7.4, 17.0, 12.6, 17.0), (10.0, 13.8, 10.0, 17.0)])
    p.setPen(QtGui.QPen(accent(), 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(6.4, 8.4), (8.8, 10.8), (13.6, 5.8)])


def g_diagnostics(p, fg):
    p.drawRoundedRect(R(3.6, 4.2, 12.8, 13.2), 1.6, 1.6)
    filled(p, fg, "rect", 7.4, 2.8, 5.2, 2.8)
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(5.4, 11.6), (8.0, 11.6), (9.4, 8.4), (11.0, 14.6), (12.4, 11.6), (14.6, 11.6)])


def g_open_logs(p, fg):
    path = QtGui.QPainterPath(P(2.6, 16.2))
    for x, y in ((2.6, 4.4), (7.6, 4.4), (9.2, 6.6), (17.4, 6.6), (17.4, 16.2)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)
    width(p, 1.1)
    lines(p, [(5.4, 10.0, 14.6, 10.0), (5.4, 13.0, 12.0, 13.0)])


def g_hand(p, fg):
    p.setPen(QtGui.QPen(fg, 2.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    for x, top in ((6.2, 6.4), (8.8, 3.6), (11.4, 3.2), (14.0, 5.2)):
        lines(p, [(x, top, x, 11.0)])
    p.setPen(QtGui.QPen(fg, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    path = QtGui.QPainterPath(P(14.0, 9.6))
    path.lineTo(14.0, 13.0)
    path.quadTo(P(14.0, 17.4), P(10.0, 17.4))
    path.quadTo(P(6.6, 17.4), P(5.2, 14.4))
    path.lineTo(3.0, 10.2)
    path.quadTo(P(4.4, 8.8), P(6.2, 11.0))
    p.drawPath(path)


def g_ruler(p, fg):
    width(p, 1.4)
    p.drawRect(R(2.0, 6.6, 16.0, 6.8))
    width(p, 1.1)
    for k, x in enumerate((4.6, 7.2, 9.8, 12.4, 15.0)):
        lines(p, [(x, 6.6, x, 6.6 + (3.4 if k % 2 == 0 else 2.0))])


def g_navigator(p, fg):
    width(p, 1.3)
    p.drawRect(R(2.4, 3.4, 15.2, 13.2))
    p.setPen(QtGui.QPen(accent(), 1.5))
    p.drawRect(R(8.0, 7.0, 6.4, 5.4))


def g_filter(p, fg):
    p.setPen(QtGui.QPen(fg, 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(3.0, 4.0), (17.0, 4.0), (11.6, 10.6), (11.6, 16.4), (8.4, 14.6), (8.4, 10.6)], closed=True)


def g_pin(p, fg):
    p.save()
    p.translate(10.0, 10.0)
    p.rotate(40.0)
    p.setPen(QtGui.QPen(fg, 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    poly(p, [(-2.6, -7.4), (2.6, -7.4), (2.0, -2.4), (4.4, 0.6), (-4.4, 0.6), (-2.0, -2.4)], closed=True)
    lines(p, [(0.0, 0.6, 0.0, 7.6)])
    p.restore()


def g_bell(p, fg):
    p.setPen(QtGui.QPen(fg, 1.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    path = QtGui.QPainterPath(P(4.0, 14.4))
    path.lineTo(5.4, 12.6)
    path.lineTo(5.4, 8.6)
    path.quadTo(P(5.4, 3.6), P(10.0, 3.6))
    path.quadTo(P(14.6, 3.6), P(14.6, 8.6))
    path.lineTo(14.6, 12.6)
    path.lineTo(16.0, 14.4)
    path.closeSubpath()
    p.drawPath(path)
    lines(p, [(10.0, 2.0, 10.0, 3.6)])
    path = QtGui.QPainterPath()
    path.arcMoveTo(R(8.0, 14.6, 4.0, 3.2), 180.0)
    path.arcTo(R(8.0, 14.6, 4.0, 3.2), 180.0, 180.0)
    p.drawPath(path)


# -- the manual's navigation (Back, Forward, Contents)
def g_nav_back(p, fg):
    """An arrow to the left (Back)."""
    lines(p, [(16.0, 10.0, 4.2, 10.0)])
    poly(p, [(9.2, 5.0), (4.2, 10.0), (9.2, 15.0)])


def g_nav_forward(p, fg):
    """An arrow to the right (Forward)."""
    lines(p, [(4.0, 10.0, 15.8, 10.0)])
    poly(p, [(10.8, 5.0), (15.8, 10.0), (10.8, 15.0)])


def g_home(p, fg):
    """A house (Contents: the first page)."""
    width(p, 1.5)
    poly(p, [(2.6, 9.6), (10.0, 3.2), (17.4, 9.6)])
    poly(p, [(4.8, 8.0), (4.8, 16.8), (15.2, 16.8), (15.2, 8.0)])
    p.drawRect(R(8.4, 11.6, 3.2, 5.2))


# --------------------------------------------------------------------------- new objects' look
def g_eyedropper(p, fg):
    # a pipette, tip at the bottom left
    p.save()
    p.translate(10.0, 10.0)
    p.rotate(45.0)
    p.drawRoundedRect(R(-2.0, -8.4, 4.0, 5.0), 1.2, 1.2)
    lines(p, [(-3.6, -3.2, 3.6, -3.2)])
    p.drawRect(R(-1.4, -3.2, 2.8, 8.4))
    poly(p, [(-1.4, 5.2), (0.0, 7.8), (1.4, 5.2)])
    p.restore()
    filled(p, accent(), "ellipse", 2.0, 16.0, 2.6, 2.6)


def g_fill_colour(p, fg):
    filled(p, accent(), "rect", 2.6, 2.6, 10.0, 10.0)
    p.drawRect(R(2.6, 2.6, 10.0, 10.0))
    width(p, 1.2)
    p.drawRect(R(9.4, 9.4, 8.0, 8.0))


def g_line_colour(p, fg):
    filled(p, fg, "rect", 2.6, 2.6, 8.0, 8.0)
    punch(p, R(7.4, 7.4, 10.0, 10.0))
    p.setPen(QtGui.QPen(accent(), 2.6))
    p.drawRect(R(8.4, 8.4, 8.0, 8.0))


def g_text_colour(p, fg):
    letter(p, "A", 13.0, True, rect=R(0.0, -2.0, 20.0, 17.0))
    filled(p, accent(), "rect", 3.0, 15.0, 14.0, 3.0)


def g_swap_new_colours(p, fg):
    g_swap_colours(p, fg)


def g_no_fill(p, fg):
    p.drawRect(R(3.0, 3.0, 14.0, 14.0))
    from . import theme

    p.setPen(
        QtGui.QPen(
            QtGui.QColor(theme.COLORS.get("danger") or theme.DARK["danger"]),
            1.8,
            QtCore.Qt.SolidLine,
            QtCore.Qt.RoundCap,
        )
    )
    lines(p, [(4.4, 15.6, 15.6, 4.4)])


def g_contrast(p, fg):
    p.drawEllipse(R(3.0, 3.0, 14.0, 14.0))
    path = QtGui.QPainterPath(P(10.0, 3.0))
    path.arcTo(R(3.0, 3.0, 14.0, 14.0), 90.0, 180.0)
    path.closeSubpath()
    p.save()
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(fg)
    p.drawPath(path)
    p.restore()


def g_tool_style(p, fg):
    # a pen over a style swatch
    width(p, 1.3)
    p.drawRect(R(2.6, 10.4, 7.0, 7.0))
    filled(p, accent(), "rect", 2.6, 10.4, 7.0, 7.0)
    p.save()
    p.translate(13.0, 7.0)
    p.rotate(45.0)
    p.drawRect(R(-1.8, -6.6, 3.6, 10.0))
    poly(p, [(-1.8, 3.4), (0.0, 6.4), (1.8, 3.4)])
    p.restore()


def g_update_style(p, fg):
    g_styles(p, fg)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    arc_arrow(p, R(11.6, 11.6, 6.4, 6.4), 90.0, -270.0, 2.0)
    p.restore()


def g_rename_style(p, fg):
    width(p, 1.3)
    p.drawRoundedRect(R(2.0, 6.0, 16.0, 8.0), 1.4, 1.4)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.4))
    lines(p, [(12.6, 4.0, 12.6, 16.0), (11.0, 4.0, 14.2, 4.0), (11.0, 16.0, 14.2, 16.0)])
    p.restore()
    lines(p, [(4.6, 10.0, 9.4, 10.0)])


def g_select_style(p, fg):
    g_styles(p, fg)
    p.save()
    p.setPen(QtGui.QPen(accent(), 1.3, QtCore.Qt.DashLine))
    p.drawRect(R(1.4, 1.4, 17.2, 17.2))
    p.restore()


def g_style_default(p, fg):
    g_styles(p, fg)
    from . import theme

    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(theme.COLORS.get("accent") or theme.DARK["accent"]))
    pts = []
    for k in range(10):
        ang = math.radians(-90 + 36 * k)
        rr = 3.6 if k % 2 == 0 else 1.6
        pts.append(P(15.4 + rr * math.cos(ang), 15.0 + rr * math.sin(ang)))
    p.drawPolygon(QtGui.QPolygonF(pts))


def g_swap_vertical(p, fg):
    # an arrow up beside an arrow down
    lines(p, [(7.0, 16.0, 7.0, 4.0), (13.0, 4.0, 13.0, 16.0)])
    arrow_head(p, (7.0, 4.0), -90.0, 3.2)
    arrow_head(p, (13.0, 16.0), 90.0, 3.2)


def g_hatched(p, fg):
    p.drawRect(R(3.0, 3.0, 14.0, 14.0))
    width(p, 1.0)
    for k in range(-2, 4):
        x = 3.0 + k * 4.6
        lines(
            p,
            [
                (
                    max(3.0, x),
                    min(17.0, 17.0 - (max(3.0, x) - x)),
                    min(17.0, x + 14.0),
                    max(3.0, 3.0 + (x + 14.0 - min(17.0, x + 14.0))),
                )
            ],
        )


# --------------------------------------------------------------------------- the underlay
def _underlay_sheet(p, x: float = 2.4, y: float = 2.4, w: float = 11.0, h: float = 9.6) -> None:
    """The faded sheet behind (dashed, in the accent): the underlay."""
    p.save()
    pen = QtGui.QPen(accent(), 1.3)
    pen.setDashPattern([1.6, 1.4])
    p.setPen(pen)
    p.drawRect(R(x, y, w, h))
    p.restore()


def g_underlay(p, fg):
    _underlay_sheet(p)
    p.drawRect(R(6.6, 7.0, 11.0, 10.4))


def g_underlay_page(p, fg):
    _underlay_sheet(p)
    punch(p, R(6.0, 6.0, 12.2, 12.2))
    page_outline(p, 7.0, 6.8, 10.2, 10.8, 3.0)


def g_underlay_image(p, fg):
    _underlay_sheet(p)
    punch(p, R(5.4, 6.4, 13.0, 11.4))
    picture(p, 6.2, 7.2, 11.4, 9.8)


def g_underlay_version(p, fg):
    _underlay_sheet(p)
    punch_ellipse(p, R(5.6, 5.6, 13.0, 13.0))
    p.drawEllipse(R(6.6, 6.6, 11.0, 11.0))
    lines(p, [(12.1, 8.6, 12.1, 12.1), (12.1, 12.1, 14.6, 13.6)])


def g_underlay_above(p, fg):
    p.drawRect(R(2.4, 2.4, 11.0, 9.6))
    punch(p, R(6.0, 6.4, 12.2, 11.6))
    _underlay_sheet(p, 6.6, 7.0, 11.0, 10.4)


def g_underlay_move(p, fg):
    _underlay_sheet(p)
    punch_ellipse(p, R(5.4, 5.4, 13.4, 13.4))
    width(p, 1.4)
    lines(p, [(12.1, 6.6, 12.1, 17.6), (6.6, 12.1, 17.6, 12.1)])
    for tip, ang in (((12.1, 6.6), -90), ((12.1, 17.6), 90), ((6.6, 12.1), 180), ((17.6, 12.1), 0)):
        arrow_head(p, tip, ang, 2.6)


def g_underlay_settings(p, fg):
    _underlay_sheet(p)
    punch(p, R(6.0, 7.2, 12.4, 10.6))
    lines(p, [(7.0, 10.0, 17.4, 10.0), (7.0, 15.0, 17.4, 15.0)])
    filled(p, fg, "ellipse", 9.0, 8.4, 3.2, 3.2)
    filled(p, fg, "ellipse", 13.4, 13.4, 3.2, 3.2)


def g_underlay_remove(p, fg):
    _underlay_sheet(p)
    punch_ellipse(p, R(7.4, 7.4, 11.0, 11.0))
    width(p, 1.8)
    lines(p, [(9.8, 9.8, 16.0, 16.0), (16.0, 9.8, 9.8, 16.0)])


#: name -> painter(p, fg): every command glyph of this module
GLYPHS: Dict[str, Callable[[QtGui.QPainter, QtGui.QColor], None]] = {
    name[2:]: fn for name, fn in list(globals().items()) if name.startswith("g_") and callable(fn)
}

#: the icon of each editor command (MainWindow attribute -> glyph) that gets none where it is
#: made; MainWindow._build_actions sets them, so the menus, the command palette, the ribbon
#: and the selection toolbar all show the same icon
ACTION_ICONS = {
    "act_save_as": "save_as",
    "act_sel_all": "select_all",
    "act_zoom_sel": "zoom_selection",
    "act_zoom_100": "zoom_100",
    "act_zoom_all": "fit_all",
    "act_guidelines": "guidelines",
    "act_snap_guides": "snap_guides",
    "act_show_guides": "guidelines",
    "act_slow_keys": "keyboard",
    "act_slow_mouse": "mouse",
    "act_isolate": "isolate",
    "act_show_all_roots": "show_hidden",
    "act_mark_changes": "mark_changes",
    "act_focus_mode": "focus_mode",
    "act_reset_ui": "reset_layout",
    "act_rot_cw": "rotate_cw",
    "act_rot_ccw": "rotate_ccw",
    "act_flip_h": "flip_h",
    "act_flip_v": "flip_v",
    "act_adjust": "adjust_position",
    "act_align_dialog": "align_dialog",
    "act_goto": "goto",
    "act_find": "find",
    "act_replace": "replace",
    "act_lock": "lock",
    "act_unlock": "unlock",
    "act_break_lock": "lock_mode",
    "act_print": "print",
    "act_export_page": "export_page",
    "act_print_setup": "print_setup",
    "act_grid_setup": "grid",
    "act_zoom_window": "magnifier",
    "act_import": "import",
    "act_import_flash": "flashing",
    "act_paste_flash": "paste_flashing",
    "act_paste_symbol": "paste_symbol",
    "act_paste_genie": "paste_genie",
    "act_paste_super": "super_genie",
    "act_paste_special": "paste_special",
    "act_copy_appearance": "copy_appearance",
    "act_paste_appearance": "paste_appearance",
    "act_save_style": "save_style",
    "act_manage_styles": "styles",
    "act_to_bitmap": "convert_bitmap",
    "act_options": "settings",
    "act_crosshair": "crosshair",
    "act_selection_hud": "selection_toolbar",
    "act_status_bar": "status_bar",
    "act_refresh": "sync",
    "act_show_tools": "toolbox",
    "act_cascade": "cascade",
    "act_tile_h": "tile_h",
    "act_tile_v": "tile_v",
    "act_save_all": "save_all",
    "act_close_doc": "close",
    "act_revert": "revert",
    "act_restore_backup": "restore_backup",
    "act_cut_link": "cut_link",
    "act_swap_colors": "swap_colours",
    "act_adjust_colors": "adjust_colours",
    "act_fav_colors": "favourite_colours",
    "act_transparent_paste": "transparent_colour",
    "act_bitmap_editor": "bitmap_editor",
    "act_replace_picture": "replace_picture",
    "act_export_picture": "export_picture",
    "act_resample_picture": "resample",
    "act_dup_pictures": "duplicates",
    "act_copy_to_lib": "copy_to_library",
    "act_hotspot": "hotspot",
    "act_used_by": "used_by",
    "act_update_users": "update_users",
    "act_compose_nested": "update_users",
    "act_paste_linked": "link",
    "act_attach_super": "super_genie",
    "act_genie_form": "genie_form",
    "act_paste_composite": "paste_composite",
    "act_replace_genie": "paste_genie",
    "act_var_tags": "variable_tags",
    "act_pack_libs": "pack_libraries",
    "act_update_pages": "update_pages",
    "act_close_builder": "sync",
    "act_update_page_template": "update_template",
    "act_edit_template": "template",
    "act_cut_template_link": "link_off",
    "act_page_defaults": "page_defaults",
    "act_check_workspace": "workspace_check",
    "act_check_display": "check_display",
    "act_pane_labels": "labels",
    "act_aliased_text": "aliased_text",
    "act_page_props": "page_properties",
    "act_genie_params": "genie_parameters",
    "act_obj_props": "properties",
    "act_font_dialog": "font",
    "act_edit_text": "edit_in_place",
    "act_help": "help",
    "act_open_logs": "open_logs",
    "act_copy_diag": "diagnostics",
    "act_check_updates": "import",
    # the tab menu's and the keyboard map's commands (document tabs, keys)
    "act_new_from_last": "template",
    "act_close_others": "close",
    "act_close_right": "close",
    "act_close_saved": "close",
    "act_close_all": "close",
    "act_reopen_closed": "open",
    "act_copy_page_path": "copy",
    "act_copy_page_name": "copy",
    "act_reveal_page": "open",
    "act_show_in_browser": "page",
    "act_repeat": "sync",
    "act_palette": "search",
    "act_palette_here": "search",
    "act_manage_views": "navigator",
    "act_clear_guides": "guidelines",
    "act_go_page": "page",
    "act_go_objects": "select",
    "act_go_library": "library",
    "act_go_tags": "variable_tags",
    "act_go_props": "properties",
    "act_go_history": "undo",
    "act_next_panel": "forward",
    "act_prev_panel": "back",
    # Edit > Select, the lock and hide commands (selecting and transforming)
    "act_deselect": "select_none",
    "act_invert_sel": "invert_selection",
    "act_select_same_element": "select_same",
    "act_select_same_kind": "select_same",
    "act_select_same_fill": "select_same",
    "act_select_same_line_colour": "select_same",
    "act_select_same_line_width": "select_same",
    "act_select_same_font": "select_same",
    "act_select_same_size": "select_same",
    "act_select_same_anim": "select_same",
    "act_select_tag": "variable_tags",
    "act_select_previous": "select_previous",
    "act_lock_toggle": "lock",
    "act_unlock_all": "unlock",
    "act_hide_toggle": "hide_selection",
}

#: the icons of the commands made from lists (by their position in the list)
LIST_ICONS = {
    "text_flag_actions": ("bold", "italic", "underline", "strikeout"),
    "justify_actions": ("justify_left", "justify_centre", "justify_right"),
    "effect_actions": (
        "effect_normal",
        "effect_shadowed",
        "effect_raised",
        "effect_lowered",
        "effect_embossed",
    ),
    "external_actions": ("studio", "cicode_editor", "runtime_manager", "setup_wizard", "setup_editor"),
}

#: the V3/V4 object kinds that are not tools (MainWindow.v34_actions) -> glyph
V34_ICONS = {
    "string": "string",
    "digital_text": "digital_text",
    "color_flood": "colour_flood",
    "bar_graph": "bar_graph",
    "slider": "slider",
    "animated_symbol": "animated_symbol",
    "animation_keyboard": "keyboard",
    "pagewide_keyboard": "keyboard_page",
    "trend": "trend",
    "cicode_object": "cicode_object",
}
