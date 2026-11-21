"""Copy Image: a picture of a Simulate tab as the runtime shows it."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import winhost

#: a popup's caption in the picture (page pixels), as winhost.PopupFrame draws it
CAPTION = winhost.PopupFrame.CAPTION


def _live(win, sp) -> bool:
    return sp in win.views()


def _child_geometry(c) -> Optional[Tuple[QtCore.QRectF, float, float, float, float]]:
    """(window rect, view w, view h, sx, sy) of a child view, None when it has no size."""
    info = c.engine_info or {}
    pr = c.scene.page_rect()
    pw, ph = (pr.width(), pr.height()) if not pr.isNull() else (0.0, 0.0)
    vw, vh = winhost.view_size(info, pw, ph)
    if vw <= 0 or vh <= 0:
        return None
    sx, sy = winhost.content_scale(info, pw, ph)
    if sx <= 0 or sy <= 0:
        return None
    return winhost.window_rect(info), vw, vh, sx, sy


class _Plan:
    """What the picture draws, in order: ('page', transform, view, source rect) and
    ('frame', transform, rect, title); transforms map a view's page to the tab's page."""

    def __init__(self, win, tab):
        self.win = win
        self.tab = tab
        self.ops: List[tuple] = []

    def build(self) -> "_Plan":
        popups: List[tuple] = []
        pr = self.tab.scene.page_rect()
        self._page(self.tab, QtGui.QTransform(), QtCore.QRectF(pr), popups)
        k = 0
        while k < len(popups) and k < 256:
            self._popup(*popups[k], popups)
            k += 1
        return self

    def _page(self, view, xf: QtGui.QTransform, src: QtCore.QRectF, popups: list) -> None:
        self.ops.append(("page", xf, view, src))
        for c in list(view.child_views):
            if not _live(self.win, c):
                continue
            if c.host == winhost.EMBEDDED:
                g = _child_geometry(c)
                if g is None:
                    continue
                r, vw, vh, sx, sy = g
                cxf = QtGui.QTransform.fromScale(sx, sy) * QtGui.QTransform.fromTranslate(r.x(), r.y()) * xf
                self._page(c, cxf, QtCore.QRectF(0, 0, vw / sx, vh / sy), popups)
            elif c.host == winhost.POPUP:
                popups.append((view, xf, c))

    def _popup(self, owner, owner_xf: QtGui.QTransform, c, popups: list) -> None:
        g = _child_geometry(c)
        if g is None:
            return
        r, vw, vh, sx, sy = g
        info = c.engine_info or {}
        if winhost.is_child(info) and c.parent_view is owner:
            xf, x, y = owner_xf, r.x(), r.y()
        else:
            # measured on the screen: the tab's page, less the tab window's own place there
            rinfo = self.tab.engine_info or {}
            xf = QtGui.QTransform()
            x = r.x() - winhost._int(rinfo.get("x"))
            y = r.y() - winhost._int(rinfo.get("y"))
        frame = QtCore.QRectF(x, y, vw + 2, vh + CAPTION + 2)
        self.ops.append(("frame", xf, frame, c.window_title()))
        cxf = QtGui.QTransform.fromScale(sx, sy) * QtGui.QTransform.fromTranslate(x + 1, y + CAPTION + 1) * xf
        self._page(c, cxf, QtCore.QRectF(0, 0, vw / sx, vh / sy), popups)

    def bounds(self) -> QtCore.QRectF:
        out = QtCore.QRectF()
        for op in self.ops:
            r = op[1].mapRect(op[3] if op[0] == "page" else op[2])
            out = r if out.isNull() else out.united(r)
        return out


def tab_image(win, tab) -> Optional[QtGui.QImage]:
    """The picture of Simulate tab ``tab`` of window ``win`` (see the module), None when the
    tab shows no page."""
    if tab is None or tab.scene.page_rect().isNull():
        return None
    plan = _Plan(win, tab).build()
    b = plan.bounds()
    if b.isNull() or b.width() < 1 or b.height() < 1:
        return None
    left, top = math.floor(b.left()), math.floor(b.top())
    w = int(math.ceil(b.right()) - left)
    h = int(math.ceil(b.bottom()) - top)
    w, h = max(1, min(w, 16000)), max(1, min(h, 16000))
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
    pal = tab.canvas.viewport().palette()
    img.fill(pal.color(QtGui.QPalette.Window))
    base = QtGui.QTransform.fromTranslate(-left, -top)
    p = QtGui.QPainter(img)
    try:
        p.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        for op in plan.ops:
            p.save()
            p.setTransform(op[1] * base)
            if op[0] == "page":
                _kind, _xf, view, src = op
                p.setClipRect(src)
                view.scene.render(p, src, src, QtCore.Qt.IgnoreAspectRatio)
            else:
                _kind, _xf, rect, title = op
                _paint_frame(p, rect, title, pal)
            p.restore()
    finally:
        p.end()
    return img


def _paint_frame(p: QtGui.QPainter, rect: QtCore.QRectF, title: str, pal: QtGui.QPalette) -> None:
    """A popup's frame and caption (its page is drawn inside it afterwards)."""
    p.fillRect(rect, pal.color(QtGui.QPalette.Window))
    pen = QtGui.QPen(pal.color(QtGui.QPalette.Mid))
    pen.setWidth(0)
    p.setPen(pen)
    p.drawRect(rect.adjusted(0, 0, -1, -1))
    cap = QtCore.QRectF(rect.left() + 7, rect.top() + 1, max(0.0, rect.width() - 14), CAPTION)
    p.setPen(pal.color(QtGui.QPalette.WindowText))
    font = QtWidgets.QApplication.font()
    p.setFont(font)
    p.drawText(
        cap,
        QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextSingleLine,
        QtGui.QFontMetrics(font).elidedText(title or "", QtCore.Qt.ElideRight, int(cap.width())),
    )
