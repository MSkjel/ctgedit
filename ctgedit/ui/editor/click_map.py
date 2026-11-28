"""View > Click Map on the canvas: what takes a click shaded, what takes only keys outlined, an
object a click never reaches marked red; the tip under the pointer says where a click goes
(features/click_map.py)."""

from __future__ import annotations

import weakref
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...features import click_map as CM
from ..style import theme

#: canvas -> its targets
_TARGETS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
#: the commands a tip lists, and the characters of each
TIP_COMMANDS = 6
TIP_CHARS = 90


def set_targets(view, targets: Optional[List[CM.Target]]) -> None:
    """Show ``targets`` on ``view`` (None: the map off)."""
    _install()
    if targets is None:
        if _TARGETS.pop(view, None) is None:
            return  # (it was off: nothing to paint again)
    else:
        _TARGETS[view] = list(targets)
    view.viewport().update()


def targets_of(view) -> Optional[List[CM.Target]]:
    return _TARGETS.get(view)


def _rect(b) -> QtCore.QRectF:
    return QtCore.QRectF(b[0], b[1], max(1.0, b[2] - b[0]), max(1.0, b[3] - b[1]))


def _paint(view, painter, exposed) -> None:
    targets = _TARGETS.get(view)
    if not targets:
        return
    from .canvas_look import mark_width

    accent = theme.color("accent")
    bad = theme.fixed("change_gone")
    boxes = {t.index: _rect(t.box) for t in targets}
    for t in targets:
        r = boxes[t.index]
        if not exposed.intersects(r.adjusted(-4, -4, 4, 4)):
            continue
        if t.blocked:
            pen = QtGui.QPen(bad, mark_width(2.0), QtCore.Qt.DashLine)
            pen.setCosmetic(True)
            wash = QtGui.QColor(bad)
            wash.setAlpha(40)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(wash, QtCore.Qt.BDiagPattern))
            painter.drawRect(r)
            continue
        pen = QtGui.QPen(accent, mark_width(1.5), QtCore.Qt.SolidLine if t.clicks else QtCore.Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        if t.clicks:
            # (the part the objects above it take is left clear)
            path = QtGui.QPainterPath()
            path.addRect(r)
            for j in t.above:
                cut = QtGui.QPainterPath()
                cut.addRect(boxes[j])
                path = path.subtracted(cut)
            wash = QtGui.QColor(accent)
            wash.setAlpha(46)
            painter.setBrush(wash)
            if t.above:
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawPath(path)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(r)
        else:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRect(r)


def _short(text: str) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= TIP_CHARS else one[: TIP_CHARS - 1] + "…"


def tip_at(targets: List[CM.Target], x: float, y: float) -> str:
    """The tip at page point (x, y): where a click goes, what it runs, what lies under it."""
    taker, under = CM.target_at(targets, x, y)
    if taker is None and not under:
        return ""
    lines = []
    if taker is not None:
        lines.append("A click here goes to %s." % taker.label)
        cmds = [c for c in taker.commands]
        for what, text in cmds[:TIP_COMMANDS]:
            lines.append("%s: %s" % (what, _short(text)))
        if len(cmds) > TIP_COMMANDS:
            lines.append("And %d more." % (len(cmds) - TIP_COMMANDS))
        if not cmds:
            lines.append("It has no command.")
    else:
        lines.append("A click here goes nowhere: the objects here take only keys.")
    keys = [t for t in under if t.keys and not t.clicks]
    clicks = [t for t in under if t.clicks]
    if clicks:
        lines.append("")
        lines.append("Under it, a click here never reaches:")
        lines += ["  %s" % t.label for t in clicks[:TIP_COMMANDS]]
    if keys:
        lines.append("")
        lines.append("Keys typed here also go to:")
        lines += ["  %s" % t.label for t in keys[:TIP_COMMANDS]]
    return "\n".join(lines)


class _TipFilter(QtCore.QObject):
    """The canvas's tool tip while the map shows."""

    def __init__(self, view):
        super().__init__(view)
        self._view = weakref.ref(view)

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if ev.type() != QtCore.QEvent.ToolTip:
            return False
        view = self._view()
        targets = _TARGETS.get(view) if view is not None else None
        if not targets:
            return False
        sp = view.mapToScene(ev.pos())
        text = tip_at(targets, sp.x(), sp.y())
        if not text:
            return False
        QtWidgets.QToolTip.showText(ev.globalPos(), text, obj)
        return True


def _install() -> None:
    from . import canvas_hooks

    if "click_map" not in {o.name for o in canvas_hooks.overlays("foreground")}:
        canvas_hooks.register_overlay("click_map", _paint, "foreground", order=45)
        canvas_hooks.register_viewport_filter("click_map", lambda view: _TipFilter(view))
