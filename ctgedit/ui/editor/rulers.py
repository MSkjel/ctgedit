"""View > Rulers."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

RULER_PX = 18
#: tick spacings (page px) the rulers choose from
STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000)
#: labels at least this far apart (screen px)
LABEL_GAP = 50
#: a label runs this far past its tick (screen px): a partial repaint draws the ticks
#: this far before the area it repaints
LABEL_REACH = 64
#: the strip a pointer or cross hair mark repaints on each side of it (screen px)
MARK_HALF = 3


def tick_steps(zoom: float) -> tuple:
    """(major, minor) tick spacing in page px at ``zoom``."""
    z = max(zoom, 1e-6)
    major = next((s for s in STEPS if s * z >= LABEL_GAP), STEPS[-1])
    minor = major
    for div in (10, 5, 2):
        if major % div == 0 and major / div * z >= 8:
            minor = major // div
            break
    return major, minor


class Ruler(QtWidgets.QWidget):
    """One ruler: ``axis`` 'x' (the top one: drags out horizontal guidelines) or 'y'."""

    def __init__(self, view, axis: str, owner: "Rulers"):
        super().__init__(view)
        self._view = view
        self._axis = axis
        self._owner = owner
        self._dragging = False
        self.shown = None  # the state() last asked to be painted
        self.setObjectName("RulerTop" if axis == "x" else "RulerLeft")
        self.setAccessibleName("Horizontal ruler" if axis == "x" else "Vertical ruler")
        self.setToolTip("Drag onto the page to add a guideline. Double-click for Guidelines Setup")
        self.setCursor(QtCore.Qt.SplitVCursor if axis == "x" else QtCore.Qt.SplitHCursor)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(owner.context_menu)

    # ---- mapping -------------------------------------------------------------------
    def _origin(self) -> float:
        sc = self._view.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is None or page.isNull():
            return 0.0
        return page.left() if self._axis == "x" else page.top()

    def _to_screen(self):
        """(a, b): a ruler position = a * scene + b (viewport pixels along the ruler)."""
        v = self._view
        p0 = v.mapFromScene(QtCore.QPointF(0.0, 0.0))
        p1 = v.mapFromScene(QtCore.QPointF(1000.0, 1000.0))
        if self._axis == "x":
            return (p1.x() - p0.x()) / 1000.0, float(p0.x())
        return (p1.y() - p0.y()) / 1000.0, float(p0.y())

    def length(self) -> int:
        return self.width() if self._axis == "x" else self.height()

    def state(self) -> tuple:
        """(what the whole ruler shows, (pointer mark, cross hair mark)): the mapping, the
        size, the page's and the selection's extent and the guidelines along this axis;
        the marks in ruler pixels (None when not shown)."""
        v = self._view
        a, b = self._to_screen()
        horiz = self._axis == "x"
        sc = v.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        pg = None
        if page is not None and not page.isNull():
            pg = (page.left(), page.right()) if horiz else (page.top(), page.bottom())
        sel = v.selection_box()
        sb = None
        if sel is not None:
            sb = (sel.left(), sel.right()) if horiz else (sel.top(), sel.bottom())
        vert, horiz_g = v.guidelines()
        gl = tuple(vert if horiz else horiz_g) if v.guides_visible() else None
        base = (a, b, self.width(), self.height(), pg, sb, gl)
        ptr = v.pointer_scene()
        pp = None
        if ptr is not None and v.viewport().underMouse():
            pp = a * (ptr.x() if horiz else ptr.y()) + b
        cross = v.cross_hair_point()
        cp = None if cross is None else a * (cross.x() if horiz else cross.y()) + b
        return base, (pp, cp)

    def follow(self, force: bool = False) -> None:
        """Repaint what changed since the state last asked for: all of it, or only the
        strips under the pointer's and the cross hairs' old and new marks."""
        st = self.state()
        old, self.shown = self.shown, st
        if force or old is None or old[0] != st[0]:
            self.update()
            return
        if old[1] == st[1]:
            return
        region = QtGui.QRegion()
        for pos in old[1] + st[1]:
            if pos is None:
                continue
            c = int(math.floor(pos))
            if self._axis == "x":
                region += QtCore.QRect(c - MARK_HALF, 0, 2 * MARK_HALF + 2, self.height())
            else:
                region += QtCore.QRect(0, c - MARK_HALF, self.width(), 2 * MARK_HALF + 2)
        if not region.isEmpty():
            self.update(region)

    # ---- painting ------------------------------------------------------------------
    def paintEvent(self, e) -> None:
        from ..style.theme import color

        v = self._view
        p = QtGui.QPainter(self)
        r = self.rect()
        dirty = e.rect()
        p.fillRect(dirty, color("panel"))
        a, b = self._to_screen()
        if a <= 0:
            p.end()
            return
        o = self._origin()
        n = self.length()
        horiz = self._axis == "x"

        def band(lo: float, hi: float, colour: QtGui.QColor) -> None:
            x0, x1 = a * lo + b, a * hi + b
            if x1 < 0 or x0 > n:
                return
            x0, x1 = max(0.0, x0), min(float(n), x1)
            rr = (
                QtCore.QRectF(x0, 0, x1 - x0, r.height())
                if horiz
                else QtCore.QRectF(0, x0, r.width(), x1 - x0)
            )
            p.fillRect(rr, colour)

        sc = v.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is not None and not page.isNull():
            band(page.left() if horiz else page.top(), page.right() if horiz else page.bottom(), color("bg"))
        sel = v.selection_box()
        if sel is not None:
            acc = QtGui.QColor(color("accent"))
            acc.setAlpha(70)
            band(sel.left() if horiz else sel.top(), sel.right() if horiz else sel.bottom(), acc)

        # ticks and labels, in page px from the page's top-left (only those that reach the
        # area repainted: a label runs LABEL_REACH px past its tick)
        major, minor = tick_steps(a)
        d0, d1 = (dirty.left(), dirty.right() + 1) if horiz else (dirty.top(), dirty.bottom() + 1)
        lo = (max(0, d0 - LABEL_REACH) - b) / a - o
        hi = (min(n, d1 + 1) - b) / a - o
        dim = QtGui.QColor(color("text_dim"))
        p.setPen(QtGui.QPen(dim, 1))
        f = QtGui.QFont(self.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() * 0.78))
        p.setFont(f)
        thick = r.height() if horiz else r.width()
        k = math.floor(lo / minor)
        end = math.ceil(hi / minor)
        while k <= end:
            val = k * minor
            pos = int(round(a * (val + o) + b))
            is_major = val % major == 0
            mid = (major % 2 == 0) and val % (major // 2) == 0 if major >= 2 else False
            ln = thick * (0.55 if is_major else 0.35 if mid else 0.22)
            if horiz:
                p.drawLine(pos, int(thick - ln), pos, thick)
            else:
                p.drawLine(int(thick - ln), pos, thick, pos)
            if is_major:
                text = "%d" % val
                if horiz:
                    p.drawText(
                        QtCore.QRectF(pos + 2, 0, 60, thick * 0.62),
                        QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                        text,
                    )
                else:
                    p.save()
                    p.translate(thick * 0.62, pos + 2)
                    p.rotate(90)
                    p.drawText(
                        QtCore.QRectF(0, 0, 60, thick * 0.62),
                        QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                        text,
                    )
                    p.restore()
            k += 1

        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # the guidelines: small triangles pointing at the canvas
        vert, horiz_g = v.guidelines()
        if v.guides_visible():
            from .canvas_look import guide_colour  # (Settings > Appearance > Guideline colour)

            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(guide_colour())
            for g in vert if horiz else horiz_g:
                pos = a * g + b
                if -4 <= pos <= n + 4:
                    if horiz:
                        tri = [
                            QtCore.QPointF(pos - 4, thick - 6),
                            QtCore.QPointF(pos + 4, thick - 6),
                            QtCore.QPointF(pos, thick),
                        ]
                    else:
                        tri = [
                            QtCore.QPointF(thick - 6, pos - 4),
                            QtCore.QPointF(thick - 6, pos + 4),
                            QtCore.QPointF(thick, pos),
                        ]
                    p.drawPolygon(QtGui.QPolygonF(tri))
        # the pointer and the cross hairs
        acc = QtGui.QColor(color("accent"))
        ptr = v.pointer_scene()
        if ptr is not None and v.viewport().underMouse():
            pos = a * (ptr.x() if horiz else ptr.y()) + b
            p.setPen(QtGui.QPen(color("text"), 1))
            if horiz:
                p.drawLine(QtCore.QPointF(pos, 0), QtCore.QPointF(pos, thick))
            else:
                p.drawLine(QtCore.QPointF(0, pos), QtCore.QPointF(thick, pos))
        cross = v.cross_hair_point()
        if cross is not None:
            pos = a * (cross.x() if horiz else cross.y()) + b
            p.setPen(QtGui.QPen(acc, 2))
            if horiz:
                p.drawLine(QtCore.QPointF(pos, 0), QtCore.QPointF(pos, thick))
            else:
                p.drawLine(QtCore.QPointF(0, pos), QtCore.QPointF(thick, pos))
        # the edge toward the canvas
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        p.setPen(QtGui.QPen(color("border"), 1))
        if horiz:
            p.drawLine(0, r.height() - 1, r.width(), r.height() - 1)
        else:
            p.drawLine(r.width() - 1, 0, r.width() - 1, r.height())
        p.end()

    # ---- dragging a guideline out ---------------------------------------------------
    def _vp(self, e) -> QtCore.QPoint:
        return self._view.viewport().mapFromGlobal(e.globalPosition().toPoint())

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(e)
            return
        if not self._owner.has_page():
            e.accept()  # (no page open: no guideline to add)
            return
        self._dragging = True
        # (the top ruler makes a horizontal guideline: y = ..., the left one x = ...)
        self._view.begin_ruler_guide("h" if self._axis == "x" else "v", self._vp(e), e.modifiers())
        e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._view.drag_ruler_guide(self._vp(e), e.modifiers())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if not self._dragging or e.button() != QtCore.Qt.LeftButton:
            super().mouseReleaseEvent(e)
            return
        self._dragging = False
        over_ruler = self.rect().contains(e.position().toPoint())
        vp = self._vp(e)
        off = not self._view.viewport().rect().contains(vp)
        self._view.end_ruler_guide(vp, e.modifiers(), cancel=over_ruler or off)
        e.accept()

    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            self._view.end_ruler_guide(QtCore.QPoint(), e.modifiers(), cancel=True)
            self._view.guidelinesDialogRequested.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class RulerCorner(QtWidgets.QWidget):
    """The square where the rulers meet: a double-click fits the page (Settings > Canvas >
    "Double-click the navigator or the ruler corner": Fit All Objects or nothing instead)."""

    def __init__(self, view, owner: "Rulers"):
        super().__init__(view)
        self._view = view
        self.setObjectName("RulerCorner")
        self.setAccessibleName("Rulers corner")
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(owner.context_menu)

    def event(self, e) -> bool:  # noqa: N802 -- Qt naming
        if e.type() == QtCore.QEvent.ToolTip:
            self.setToolTip(self.tip_text())
        return super().event(e)

    def tip_text(self) -> str:
        """What a double-click does now (the setting)."""
        from ..prefs.settings import choice
        from .viewnav import CORNER_DOUBLE_CLICK

        what = choice("opt/corner_double_click", CORNER_DOUBLE_CLICK, "fit_page")
        if what == "nothing":
            return "The rulers meet here"
        return "Double-click to %s" % ("fit all objects" if what == "fit_all" else "fit the page")

    def paintEvent(self, _e) -> None:
        from ..style.theme import color

        p = QtGui.QPainter(self)
        r = self.rect()
        p.fillRect(r, color("panel"))
        p.setPen(QtGui.QPen(color("border"), 1))
        p.drawLine(r.width() - 1, 0, r.width() - 1, r.height())
        p.drawLine(0, r.height() - 1, r.width(), r.height() - 1)
        p.end()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            from .viewnav import corner_double_click

            corner_double_click(self._view)
            e.accept()


class Rulers(QtCore.QObject):
    """The two rulers and the corner of one canvas (see the module notes). ``window``: the
    editor window whose actions the right-click menu offers."""

    def __init__(self, view, window=None):
        super().__init__(view)
        self._view = view
        self._window = window
        self.top = Ruler(view, "x", self)
        self.left = Ruler(view, "y", self)
        self.corner = RulerCorner(view, self)
        self._on = False
        self._pending = False
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        view.zoomChanged.connect(self.schedule)
        view.horizontalScrollBar().valueChanged.connect(self.schedule)
        view.verticalScrollBar().valueChanged.connect(self.schedule)
        for w in (self.top, self.left, self.corner):
            w.hide()

    def size(self) -> int:
        from ..style.theme import px

        return px(RULER_PX)

    def is_shown(self) -> bool:
        return self._on

    def has_page(self) -> bool:
        """A page (or library element) is open in the window."""
        w = self._window
        return w is None or getattr(w, "doc", None) is not None

    def set_visible(self, on: bool) -> None:
        self._on = bool(on)
        self._view.set_ruler_margin(self.size() if self._on else 0)
        for w in (self.top, self.left, self.corner):
            w.setVisible(self._on)
        self.place()

    def place(self) -> None:
        if not self._on:
            return
        g = self._view.viewport().geometry()
        m = self.size()
        if self._view.viewportMargins().left() != m:
            self._view.set_ruler_margin(m)  # (the text size changed)
            g = self._view.viewport().geometry()
        self.top.setGeometry(g.x(), g.y() - m, g.width(), m)
        self.left.setGeometry(g.x() - m, g.y(), m, g.height())
        self.corner.setGeometry(g.x() - m, g.y() - m, m, m)
        for w in (self.top, self.left, self.corner):
            w.raise_()
        self.schedule()

    def schedule(self, *_args) -> None:
        """The zoom, the scroll or the rulers' place changed: repaint both."""
        if self._on:
            self.top.follow(force=True)
            self.left.follow(force=True)

    def follow(self) -> None:
        """The canvas repaints (the selection, the pointer, a guideline may have moved):
        each ruler repaints what of it changed, if anything."""
        if self._on:
            self.top.follow()
            self.left.follow()

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t in (QtCore.QEvent.Resize, QtCore.QEvent.Move, QtCore.QEvent.LayoutRequest):
            self.place()
        elif (
            t in (QtCore.QEvent.Paint, QtCore.QEvent.Enter, QtCore.QEvent.Leave)
            and obj is self._view.viewport()
        ):
            self.follow()
        return False

    def context_menu(self, pos) -> None:
        w = self._window
        src = self.sender()
        menu = QtWidgets.QMenu(src if isinstance(src, QtWidgets.QWidget) else self._view)
        for name in ("act_rulers", "act_guidelines", "act_clear_guides"):
            a = getattr(w, name, None) if w is not None else None
            if a is not None:
                menu.addAction(a)
        if menu.actions():
            at = src.mapToGlobal(pos) if isinstance(src, QtWidgets.QWidget) else QtGui.QCursor.pos()
            menu.exec(at)
