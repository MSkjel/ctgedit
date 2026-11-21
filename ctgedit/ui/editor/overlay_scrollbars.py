"""The canvas scroll bars."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

SCROLLBAR_MODES = (("auto", "Thin, shown while scrolling"), ("always", "Always"), ("never", "Never"))
THIN, WIDE = 6, 10
FADE_AFTER_MS = 1200
#: the pointer this close to an edge shows its bar (px)
NEAR_PX = 16
#: the pointer this close to an edge is on the thin bar: the bar then takes the mouse (px)
HIT_PX = THIN + 2


class OverlayBar(QtWidgets.QWidget):
    """One thin overlay bar bound to ``bar`` (the view's own QScrollBar)."""

    def __init__(
        self,
        viewport: QtWidgets.QWidget,
        bar: QtWidgets.QScrollBar,
        orientation: QtCore.Qt.Orientation,
        active=lambda: True,
    ):
        super().__init__(viewport)
        self._active = active
        self.setObjectName("CanvasOverlayBar")
        self.setAccessibleName(
            "Horizontal scroll bar" if orientation == QtCore.Qt.Horizontal else "Vertical scroll bar"
        )
        self._bar = bar
        self._orient = orientation
        self._hot = False
        self._drag: tuple | None = None  # (press position along, the value then)
        self._alpha = 0.0
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.ArrowCursor)
        # (the mouse passes through to the canvas until the pointer is on the bar: hot)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._fade = QtCore.QVariantAnimation(self)
        self._fade.setDuration(220)
        self._fade.valueChanged.connect(self._set_alpha)
        self._fade.finished.connect(self._faded)
        self._idle = QtCore.QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(FADE_AFTER_MS)
        self._idle.timeout.connect(self.fade_out)
        bar.valueChanged.connect(self.flash)
        # (a method of this bar, not a lambda: Qt drops the connection when the bar goes; a
        # lambda would outlive it and repaint a deleted bar when the view goes at exit)
        bar.rangeChanged.connect(self._range_changed)
        self.hide()

    def _range_changed(self, *_a) -> None:
        self.update()

    # ---- geometry ------------------------------------------------------------------
    def thickness(self) -> int:
        return WIDE if self._hot or self._drag is not None else THIN

    def place(self) -> None:
        vp = self.parentWidget()
        t = WIDE + 2
        if self._orient == QtCore.Qt.Horizontal:
            self.setGeometry(0, vp.height() - t, max(0, vp.width() - t), t)
        else:
            self.setGeometry(vp.width() - t, 0, t, max(0, vp.height() - t))

    def _track(self) -> tuple:
        """(start, length) of the track along the bar."""
        length = self.width() if self._orient == QtCore.Qt.Horizontal else self.height()
        return 2, max(1, length - 4)

    def thumb(self) -> QtCore.QRectF:
        b = self._bar
        start, length = self._track()
        span = max(1, b.maximum() - b.minimum() + b.pageStep())
        size = max(24.0, length * b.pageStep() / span)
        size = min(size, float(length))
        free = length - size
        pos = start + (
            free * (b.value() - b.minimum()) / max(1, b.maximum() - b.minimum())
            if b.maximum() > b.minimum()
            else 0
        )
        t = self.thickness()
        if self._orient == QtCore.Qt.Horizontal:
            return QtCore.QRectF(pos, self.height() - t - 1, size, t)
        return QtCore.QRectF(self.width() - t - 1, pos, t, size)

    def hit(self, pos: QtCore.QPointF) -> bool:
        """``pos`` (viewport coordinates) lies on the thin bar drawn (its strip by the edge)."""
        if not self.isVisible():
            return False
        vp = self.parentWidget()
        g = self.geometry()
        if self._orient == QtCore.Qt.Horizontal:
            return g.left() <= pos.x() <= g.right() and pos.y() >= vp.height() - HIT_PX
        return g.top() <= pos.y() <= g.bottom() and pos.x() >= vp.width() - HIT_PX

    def hot(self) -> bool:
        return self._hot

    def set_hot(self, on: bool) -> None:
        """The pointer is on the bar (it widens and takes the mouse) or left it."""
        on = bool(on)
        if on == self._hot:
            return
        self._hot = on
        self._sync_mouse()
        if on:
            self.flash()
        else:
            self._fade_later()
        self.update()

    def _sync_mouse(self) -> None:
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, not (self._hot or self._drag is not None))

    # ---- showing and fading -------------------------------------------------------------
    def _set_alpha(self, v) -> None:
        self._alpha = float(v)
        self.update()

    def _faded(self) -> None:
        if self._alpha <= 0.01 and not self._hot and self._drag is None:
            self.hide()

    def flash(self, *_args) -> None:
        """Shown now, faded 1.2 s later (a scroll, the pointer near the edge)."""
        try:
            empty = self._bar.maximum() <= self._bar.minimum()
        except RuntimeError:  # swallow-ok: the view is being deleted (its scene went first)
            return
        if empty or not self._active():
            return
        self._fade.stop()
        self._alpha = 1.0
        self.place()
        self.show()
        self.raise_()
        self.update()
        self._fade_later()

    def _fade_later(self) -> None:
        """Fade out FADE_AFTER_MS from now; with the canvas still (the tests: canvas_look.still)
        a bar stays as it is until it is changed."""
        from .canvas_look import still

        if still():
            self._idle.stop()
        else:
            self._idle.start()

    def fade_out(self) -> None:
        if self._hot or self._drag is not None:
            self._fade_later()
            return
        self._fade.stop()
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(0.0)
        self._fade.start()

    # ---- painting ----------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        if self._alpha <= 0.01:
            return
        from ..style.theme import color

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setOpacity(self._alpha)
        if self._hot or self._drag is not None:
            track = QtGui.QColor(color("panel"))
            track.setAlpha(150)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(track)
            t = self.thickness()
            r = QtCore.QRectF(self.rect())
            r = (
                r.adjusted(0, r.height() - t - 2, 0, 0)
                if self._orient == QtCore.Qt.Horizontal
                else r.adjusted(r.width() - t - 2, 0, 0, 0)
            )
            p.drawRoundedRect(r, t / 2.0, t / 2.0)
        c = QtGui.QColor(color("accent") if self._drag is not None else color("scroll_hover"))
        c.setAlpha(230 if self._hot or self._drag is not None else 190)
        edge = color("shadow")
        edge.setAlpha(60)
        p.setPen(QtGui.QPen(edge, 1))
        p.setBrush(c)
        th = self.thumb()
        p.drawRoundedRect(
            th,
            th.width() / 2.0 if self._orient != QtCore.Qt.Horizontal else th.height() / 2.0,
            th.width() / 2.0 if self._orient != QtCore.Qt.Horizontal else th.height() / 2.0,
        )
        p.end()

    # ---- the mouse --------------------------------------------------------------------
    def _along(self, pos: QtCore.QPointF) -> float:
        return pos.x() if self._orient == QtCore.Qt.Horizontal else pos.y()

    def enterEvent(self, e) -> None:
        self.set_hot(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.set_hot(False)
        super().leaveEvent(e)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() != QtCore.Qt.LeftButton:
            e.ignore()
            return
        pos = e.position()
        th = self.thumb()
        b = self._bar
        if th.contains(pos):
            self._drag = (self._along(pos), b.value())
            self._sync_mouse()
        elif self._hot:
            # a click on the track: a page toward it
            before = self._along(pos) < (th.left() if self._orient == QtCore.Qt.Horizontal else th.top())
            b.setValue(b.value() + (-1 if before else 1) * b.pageStep())
        else:
            e.ignore()  # (not on the bar: the canvas's click)
            return
        self.update()
        e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._drag is None:
            return
        start, value = self._drag
        b = self._bar
        _s, length = self._track()
        th = self.thumb()
        size = th.width() if self._orient == QtCore.Qt.Horizontal else th.height()
        free = max(1.0, length - size)
        delta = (self._along(e.position()) - start) / free * (b.maximum() - b.minimum())
        b.setValue(int(round(value + delta)))
        e.accept()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        self._drag = None
        self._sync_mouse()
        self.update()
        self._fade_later()
        e.accept()


class OverlayScrollBars(QtCore.QObject):
    """The canvas's scroll bar mode and its two overlay bars (see the module notes)."""

    def __init__(self, view: QtWidgets.QGraphicsView):
        super().__init__(view)
        self._view = view
        vp = view.viewport()
        self.mode = "always"
        active = lambda: self.mode == "auto"  # noqa: E731
        self.h = OverlayBar(vp, view.horizontalScrollBar(), QtCore.Qt.Horizontal, active)
        self.v = OverlayBar(vp, view.verticalScrollBar(), QtCore.Qt.Vertical, active)
        vp.installEventFilter(self)

    def set_mode(self, mode: str) -> None:
        mode = mode if mode in dict(SCROLLBAR_MODES) else "auto"
        self.mode = mode
        policy = QtCore.Qt.ScrollBarAsNeeded if mode == "always" else QtCore.Qt.ScrollBarAlwaysOff
        self._view.setHorizontalScrollBarPolicy(policy)
        self._view.setVerticalScrollBarPolicy(policy)
        if mode != "auto":
            for bar in (self.h, self.v):
                bar.set_hot(False)
                bar.hide()

    def bars(self) -> tuple:
        return self.h, self.v

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QtCore.QEvent.Resize:
            self.h.place()
            self.v.place()
        elif t == QtCore.QEvent.MouseMove and self.mode == "auto":
            pos = ev.position()
            if pos.y() >= obj.height() - NEAR_PX:
                self.h.flash()
            if pos.x() >= obj.width() - NEAR_PX:
                self.v.flash()
            # on the thin bar (no button held: a drag on the canvas keeps the mouse), the
            # bar takes the mouse; off it, the canvas has it again
            idle = ev.buttons() == QtCore.Qt.NoButton
            for bar in (self.h, self.v):
                bar.set_hot(idle and bar.hit(pos))
        elif t == QtCore.QEvent.Leave:
            for bar in (self.h, self.v):
                if not bar.underMouse():
                    bar.set_hot(False)
        return False

    def flash(self) -> None:
        if self.mode == "auto":
            self.h.flash()
            self.v.flash()
