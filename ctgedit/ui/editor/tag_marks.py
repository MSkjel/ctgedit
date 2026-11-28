"""A tag's uses marked on the canvas (the Tags panel's Mark on Page): each object that reads
the tag outlined blue, one that writes it amber; the tip names the fields."""

from __future__ import annotations

import weakref
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...features.tag_uses import READS
from ..style import theme

#: canvas -> (tag, [(box, [TagUse])])
_MARKS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def set_marks(view, tag: str, marks: Optional[List[Tuple[tuple, list]]]) -> None:
    """Mark ``marks`` [(box, uses)] of ``tag`` on ``view`` (None or []: none)."""
    _install()
    if marks:
        _MARKS[view] = (tag, list(marks))
    elif _MARKS.pop(view, None) is None:
        return  # (none shown: nothing to paint again)
    view.viewport().update()


def marks_of(view):
    return _MARKS.get(view)


def _rect(b) -> QtCore.QRectF:
    return QtCore.QRectF(b[0], b[1], max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1]))


def _writes(uses) -> bool:
    return any(u.access != READS for u in uses)


def _paint(view, painter, exposed) -> None:
    got = _MARKS.get(view)
    if not got:
        return
    from .canvas_look import mark_width

    read_c = theme.color("accent")
    write_c = theme.fixed("change_edit")
    for box, uses in got[1]:
        r = _rect(box).adjusted(-2, -2, 2, 2)
        if not exposed.intersects(r.adjusted(-4, -4, 4, 4)):
            continue
        c = write_c if _writes(uses) else read_c
        pen = QtGui.QPen(c, mark_width(2.0))
        pen.setCosmetic(True)
        wash = QtGui.QColor(c)
        wash.setAlpha(36)
        painter.setPen(pen)
        painter.setBrush(wash)
        painter.drawRect(r)


def tip_at(view, x: float, y: float) -> str:
    got = _MARKS.get(view)
    if not got:
        return ""
    tag, marks = got
    lines = []
    for box, uses in marks:
        if box[0] - 2 <= x < box[2] + 2 and box[1] - 2 <= y < box[3] + 2:
            for u in uses:
                lines.append("%s: %s" % (u.access.capitalize(), u.label))
    if not lines:
        return ""
    return "%s\n%s" % (
        tag,
        "\n".join(lines[:12] + (["And %d more." % (len(lines) - 12)] if len(lines) > 12 else [])),
    )


class _TipFilter(QtCore.QObject):
    def __init__(self, view):
        super().__init__(view)
        self._view = weakref.ref(view)

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if ev.type() != QtCore.QEvent.ToolTip:
            return False
        view = self._view()
        if view is None or view not in _MARKS:
            return False
        sp = view.mapToScene(ev.pos())
        text = tip_at(view, sp.x(), sp.y())
        if not text:
            return False
        QtWidgets.QToolTip.showText(ev.globalPos(), text, obj)
        return True


def _install() -> None:
    from . import canvas_hooks

    if "tag_marks" not in {o.name for o in canvas_hooks.overlays("foreground")}:
        canvas_hooks.register_overlay("tag_marks", _paint, "foreground", order=46)
        canvas_hooks.register_viewport_filter("tag_marks", lambda view: _TipFilter(view))
