"""The Simulate page view: read-only, with clicks and keys sent to the simulation."""

from __future__ import annotations

import contextlib
import math
import time
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...render.scene import PAGE_INDEX_ROLE
from .sources import InputEvent
from ...format import codes as _codes
from ..editor import viewnav as VN

#: the zoom limits: the editor canvas's
_MIN_ZOOM, _MAX_ZOOM = VN.ZOOM_MIN, VN.ZOOM_MAX
#: the input records a container may own (touch 0x1010, keyboard 0x100B): it takes clicks
_INPUT_RECORDS = _codes.INPUT_TCS
_BUTTON = 0x1001
#: the pointer's position goes to the simulation at most this often while it moves (s)
MOVE_EVERY = 0.05
#: Show on Page marks one object this long, several this long (s), times Settings >
#: Appearance > Messages stay
MARK_S, MARKS_S = 4.0, 6.0
#: the mark's fill (alpha over the selection colour); its outline is this much wider than
#: Settings > Appearance > Selection outlines (thin: 3 px)
MARK_FILL_ALPHA = 40
MARK_EXTRA_PX = 2.0


def mark_ms(seconds: float) -> int:
    """How long a Show on Page mark stays: ``seconds`` times Settings > Appearance > Messages
    stay (ui/notify.TIME_FACTOR: 0.7, 1 or 1.6)."""
    from .. import notify

    return int(round(seconds * 1000 * notify.TIME_FACTOR[notify.message_time()]))


def mark_pen_px() -> float:
    """The width of the Show on Page mark's outline on screen (px)."""
    from ..editor.canvas_look import outline_width

    return MARK_EXTRA_PX + outline_width()


class _BarPress(QtCore.QObject):
    """A press on a thin scroll bar over the page: the user scrolls (the bar moves the view
    by its own setValue, which says nothing of who moved it). Its parent is the canvas:
    SimCanvas._user_scrolled."""

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.MouseButtonPress:
            view = self.parent()
            if isinstance(view, SimCanvas):
                view._user_scrolled()
        return False


class SimCanvas(QtWidgets.QGraphicsView):
    """A page shown live: the wheel as on the editor's canvas (Settings > Canvas: it zooms
    about the pointer, or scrolls with Ctrl + the wheel zooming), middle button or Space +
    drag = pan; the left / right button and the keys go to the simulation (``inputEvent``)."""

    #: an input event for the simulation (sources.InputEvent)
    inputEvent = QtCore.Signal(object)
    #: the zoom changed (factor)
    zoomChanged = QtCore.Signal(float)
    #: the wheel over a fixed view that forwards it nowhere (a popup): steps (+ in, - out)
    zoomRequested = QtCore.Signal(float)
    #: Shift + right-click: the objects under the pointer (ANs, topmost first) and the global
    #: position, for the window's object menu (Edit in Editor ...); a plain right-click is the
    #: page's own (its RBUTTON commands)
    objectMenuRequested = QtCore.Signal(object, object)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setAccessibleName("Simulated page")
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        # the runtime draws its text unsmoothed (GDI): so does the simulated page, unless
        # Simulate Settings say otherwise
        from . import options as _opts

        self.set_aliased_text(bool(_opts.get("aliased_text")), update=False)
        # the scene paints the page; a view brush would short-circuit drawBackground
        self.setBackgroundBrush(QtGui.QBrush(QtCore.Qt.NoBrush))
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.MinimalViewportUpdate)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self._zoom = 1.0
        self._pan: Optional[QtCore.QPoint] = None
        self._space = False
        self._press: Optional[Tuple[InputEvent, QtCore.QPointF]] = None
        self._fitted = False
        self._auto_fit = False
        self._fit_cap: Optional[float] = None  # (the first view's zoom cap, kept on a refit)
        self._moved_at = 0.0
        self._moved_to: Optional[Tuple[int, int]] = None
        # the last position a throttled move left unsent: it goes when the interval is over
        # (else the simulation keeps a pointer position the pointer stopped beyond)
        self._move_pending: Optional[QtCore.QPoint] = None
        self._move_timer = QtCore.QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(self._flush_pointer)
        # debugging: the objects whose code reported errors {AN: (count, last text, level)},
        # drawn while show_errors; the object Show on Page points at (until the timer ends)
        self.show_errors = True
        self.show_numbers = False
        self._errors: dict = {}
        self._shown_an: Optional[int] = None
        self._shown_more: tuple = ()  # show_objects: the other objects marked
        self._shown_timer = QtCore.QTimer(self)
        self._shown_timer.setSingleShot(True)
        self._shown_timer.timeout.connect(self._end_shown)
        # a window drawn inside another (a pinned child) or as a popup: a fixed part of its
        # scene at a fixed scale (set_fixed_view); ``forward`` = the canvas the wheel and the
        # pan go to (the parent window's, so the windows inside it move with it)
        self._fixed = None
        self.forward: Optional["SimCanvas"] = None
        # the wheel's state (ui/editor/viewnav.wheel_intent / wheel_zoom_target): the last
        # touchpad-like turn, the zoom's stop at 100 %
        self._pad_at = -1.0
        # the scene's work area (the page and a margin) decides the room to pan: the view's
        # own rect adds half a viewport of slack (sync_scene_rect), as the editor's canvas
        if hasattr(scene, "workAreaChanged"):
            scene.workAreaChanged.connect(lambda *_a: self.sync_scene_rect())
        self._detent, self._detent_dir, self._detent_at = 0, 0, 0.0
        # Settings > Canvas as on the editor's canvas: its scroll bars (thin ones over the
        # page by default: ui/editor/overlay_scrollbars) and the glide of a zoom change (a Show on
        # Page that zooms out to its marks): (animation, (zoom, centre) start, end)
        self._glide = None
        from ..editor.overlay_scrollbars import OverlayScrollBars

        self.overlay_bars = OverlayScrollBars(self)
        self.apply_canvas_options()
        # a page shown at 100 % (show_actual_size) keeps the runtime's place as the view's
        # size changes, until the user zooms, pans or scrolls (a scroll bar dragged, its
        # track clicked: the thin bars over the page too)
        self._auto_actual = False
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.actionTriggered.connect(self._user_scrolled)
        self._bar_watch = _BarPress(self)
        for bar in self.overlay_bars.bars():
            bar.installEventFilter(self._bar_watch)

    def apply_canvas_options(self) -> None:
        """Settings > Canvas > "Scroll bars" (thin ones shown while the page scrolls, always or
        never); a window drawn inside another or as a popup has none (set_fixed_view)."""
        from ..editor.overlay_scrollbars import SCROLLBAR_MODES
        from ..prefs.settings import choice

        mode = (
            "never" if self._fixed is not None else choice("view/canvas_scrollbars", SCROLLBAR_MODES, "auto")
        )
        self.overlay_bars.set_mode(mode)

    # -- the glide of a zoom change (as ui/editor/viewnav's) ---------------------------------
    def can_glide(self) -> bool:
        """Settings > Canvas > "Animate zoom changes" (not while hidden, with CTG_NO_ANIMATION
        set or with Windows' animations off: ui/editor/viewnav.glide_allowed)."""
        from ..prefs.settings import option

        return VN.glide_allowed(self, option("animate_zoom"))

    def glide_to(self, zoom: float, centre: QtCore.QPointF) -> None:
        """Show scene point ``centre`` in the middle at ``zoom``: a glide as on the editor's
        canvas (the scale blends in log space, the centre in a straight line, the last frame
        lands exactly there), or at once (:meth:`can_glide`)."""
        self.finish_glide()
        end = (max(_MIN_ZOOM, min(_MAX_ZOOM, float(zoom))), QtCore.QPointF(centre))
        if not self.can_glide():
            self._show_view(*end)
            return
        vp = self.viewport().rect()
        start = (self._zoom, self.mapToScene(vp.center()))
        anim = QtCore.QVariantAnimation(self)
        anim.setDuration(VN.GLIDE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        self._glide = (anim, start, end)
        anim.valueChanged.connect(self._glide_frame)
        anim.finished.connect(self.finish_glide)
        anim.start()

    def _glide_frame(self, value) -> None:
        g = self._glide
        if g is None:
            return
        _anim, (za, ca), (zb, cb) = g
        e = VN.ease(float(value))
        if e >= 1.0:
            return  # (the end lands in finish_glide)
        z = math.exp(math.log(za) + (math.log(zb) - math.log(za)) * e)
        self._show_view(z, ca + (cb - ca) * e)

    def finish_glide(self) -> None:
        """A running glide ends at once, where it was going."""
        g, self._glide = self._glide, None
        if g is None:
            return
        anim, _start, end = g
        with contextlib.suppress(RuntimeError, TypeError):  # (not connected any more)
            anim.finished.disconnect(self.finish_glide)
        anim.stop()
        anim.deleteLater()
        self._show_view(*end)

    def view_animating(self) -> bool:
        return self._glide is not None

    def _show_view(self, zoom: float, centre: QtCore.QPointF) -> None:
        self.sync_scene_rect(zoom, centre)
        self.setTransform(QtGui.QTransform.fromScale(zoom, zoom))
        self.centerOn(centre)
        self._zoom = zoom
        self.zoomChanged.emit(zoom)

    # -- the room to pan (as CanvasView.sync_scene_rect) ----------------------------------
    def sync_scene_rect(self, zoom: Optional[float] = None, centre: Optional[QtCore.QPointF] = None) -> None:
        """Set the view's scene rect: the work area plus half a viewport of slack."""
        if self._fixed is not None:
            return
        sc = self.scene()
        work_rect = getattr(sc, "work_rect", None)
        if sc is None or work_rect is None:
            return
        work = work_rect()
        if work.isNull() or work.isEmpty():
            return
        z = float(zoom) if zoom else self._zoom
        if z <= 0:
            return
        size = self.maximumViewportSize()
        sx = 0.5 * max(1, size.width()) / z
        sy = 0.5 * max(1, size.height()) / z
        r = work.adjusted(-sx, -sy, sx, sy)
        vp = self.viewport().rect()
        if vp.width() > 1 and vp.height() > 1:
            r = r.united(self.mapToScene(vp).boundingRect())
        if centre is not None:
            w, h = (vp.width() + 8) / z, (vp.height() + 8) / z
            r = r.united(QtCore.QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h))
        if r != self.sceneRect():
            self.setSceneRect(r)

    # -- debugging -----------------------------------------------------------------
    def note_error(self, an: int, text: str, level: str) -> None:
        """An error (or warning) of the code of object ``an``: marked on the page."""
        n, _t, lv = self._errors.get(an, (0, "", ""))
        if lv == "error":
            level = "error"
        self._errors[an] = (n + 1, text, level)
        if self.show_errors:
            self.viewport().update()

    def clear_errors(self) -> None:
        self._errors = {}
        self.viewport().update()

    def errors(self) -> dict:
        return dict(self._errors)

    def set_aliased_text(self, on: bool, update: bool = True) -> None:
        """Draw the page without smoothing, the way the runtime draws it (GDI's aliased text
        and its unsmoothed slanted edges), or smoothed."""
        self.setRenderHint(QtGui.QPainter.TextAntialiasing, not on)
        self.setRenderHint(QtGui.QPainter.Antialiasing, not on)
        sc = self.scene()
        if hasattr(sc, "set_aliased_text"):
            sc.set_aliased_text(on)
        if update:
            self.viewport().update()

    def set_show_errors(self, on: bool) -> None:
        self.show_errors = bool(on)
        self.viewport().update()

    def _area_of(self, an: int) -> Optional[QtCore.QRectF]:
        sc = self.scene()
        page = getattr(sc, "_page", None)
        layer = sc.sim_layer() if hasattr(sc, "sim_layer") else None
        if page is None or layer is None:
            return None
        i = layer.index_of(an)
        if i is None or not 0 <= i < len(page.objects):
            return None
        r = layer.pick_area(i)
        if r is None:
            g = page.objects[i].geometry
            r = QtCore.QRectF(g.x, g.y, g.extent_x + 1, g.extent_y + 1)
        return r

    def show_object(self, an: int) -> bool:
        """Centre the view on object ``an`` and mark it for a few seconds (:func:`mark_ms`).
        False when the page has no such object."""
        r = self._area_of(an)
        if r is None:
            return False
        self.finish_glide()
        self._shown_an = int(an)
        self.centerOn(r.center())
        self._shown_timer.start(mark_ms(MARK_S))
        self.viewport().update()
        return True

    def show_objects(self, ans, seconds: float = MARKS_S) -> int:
        """Mark every object of ``ans`` for a few seconds (a tag's users, the Inspector;
        :func:`mark_ms`) and bring them into view (centred on them; zoomed out when they do
        not fit, gliding there). The number marked (those the page draws now)."""
        rects = [(int(a), r) for a in ans for r in (self._area_of(a),) if r is not None]
        if not rects:
            return 0
        self.finish_glide()
        self._shown_an = rects[0][0]
        self._shown_more = tuple(a for a, _r in rects[1:])
        box = QtCore.QRectF(rects[0][1])
        for _a, r in rects[1:]:
            box = box.united(r)
        vp = self.mapToScene(self.viewport().rect()).boundingRect()
        if box.width() > vp.width() or box.height() > vp.height():
            # (fitInView's zoom: the box and a margin in the viewport, keeping the aspect)
            fit = box.adjusted(-20, -20, 20, 20)
            vr = self.viewport().rect().adjusted(2, 2, -2, -2)
            z = min(vr.width() / max(fit.width(), 1.0), vr.height() / max(fit.height(), 1.0))
            self._auto_fit = False  # (a view fitted to the marks keeps its place)
            self._auto_actual = False
            self.glide_to(z, box.center())
        else:
            self.centerOn(box.center())
        self._shown_timer.start(mark_ms(seconds))
        self.viewport().update()
        return len(rects)

    def _end_shown(self) -> None:
        self._shown_an = None
        self._shown_more = ()
        self.viewport().update()

    def drawForeground(self, painter, rect):  # noqa: N802 -- Qt naming
        super().drawForeground(painter, rect)
        if not (self._errors and self.show_errors) and self._shown_an is None:
            return
        painter.save()
        try:
            if self.show_errors:
                font = QtGui.QFont(painter.font())
                font.setPixelSize(max(1, int(round(10 / max(self._zoom, 0.05)))))
                font.setBold(True)
                painter.setFont(font)
                for an, (n, _text, level) in self._errors.items():
                    r = self._area_of(an)
                    if r is None or not r.intersects(rect.adjusted(-40, -40, 40, 40)):
                        continue
                    col = QtGui.QColor(220, 30, 30) if level == "error" else QtGui.QColor(230, 140, 0)
                    pen = QtGui.QPen(col, 2)
                    pen.setCosmetic(True)
                    pen.setStyle(QtCore.Qt.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(QtCore.Qt.NoBrush)
                    painter.drawRect(r)
                    label = str(n) if n < 1000 else "999+"
                    fm = QtGui.QFontMetricsF(font)
                    w = fm.horizontalAdvance(label) + fm.height() * 0.6
                    h = fm.height()
                    badge = QtCore.QRectF(r.right() - w / 2, r.top() - h / 2, w, h)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(col)
                    painter.drawRoundedRect(badge, h / 2, h / 2)
                    painter.setPen(QtGui.QColor(255, 255, 255))
                    painter.drawText(badge, QtCore.Qt.AlignCenter, label)
            if self._shown_an is not None:
                # (Settings > Appearance > Selection colour and Selection outlines)
                from ..style.theme import canvas_colour

                pen = QtGui.QPen(canvas_colour("selection"), mark_pen_px())
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(canvas_colour("selection", MARK_FILL_ALPHA))
                pad = 4 / max(self._zoom, 0.05)
                for an in (self._shown_an,) + tuple(self._shown_more):
                    r = self._area_of(an)
                    if r is not None:
                        painter.drawRect(r.adjusted(-pad, -pad, pad, pad))
        finally:
            painter.restore()

    # -- view -------------------------------------------------------------------
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, factor: float, anchor: Optional[QtCore.QPoint] = None) -> None:
        self.finish_glide()  # (from where a glide was going)
        factor = max(_MIN_ZOOM, min(_MAX_ZOOM, float(factor)))
        vc = self.viewport().rect().center()
        if anchor is None:
            anchor = vc
        before = self.mapToScene(anchor)
        # keep the page point under the anchor where it was
        d = QtCore.QPointF(vc - anchor)
        centre = before + QtCore.QPointF(d.x() / factor, d.y() / factor)
        self.sync_scene_rect(factor, centre)
        self.setTransform(QtGui.QTransform.fromScale(factor, factor))
        self.centerOn(centre)
        self._zoom = factor
        self.zoomChanged.emit(factor)

    def view_centre(self) -> QtCore.QPointF:
        """The scene point in the middle of the view."""
        return self.mapToScene(self.viewport().rect().center())

    def fit_zoom(self) -> float:
        """The zoom Fit Page gives now (0: no page or no room): the editor's rule
        (viewnav.fit_zoom: the page plus Settings > Canvas's margin in the viewport)."""
        r = self._page_rect()
        if r.isNull():
            return 0.0
        return VN.fit_zoom(r, self.maximumViewportSize())

    def fit_page(self, animate: bool = False, cap: Optional[float] = None) -> None:
        """Fit Page: the whole page in the view by the editor's rule (:meth:`fit_zoom`), at
        most ``cap`` (a tab's first view: Settings > Canvas > the largest zoom a page opens
        at). It follows the view's size until the user zooms or pans. ``animate``: it glides
        there (the Fit Page command; a refit is at once)."""
        f = self.fit_zoom()
        if f <= 0:
            return
        if cap:
            f = min(f, float(cap))
        self._fit_cap = cap
        r = self._page_rect()
        if animate and self.can_glide():
            self.glide_to(f, r.center())
        else:
            self.set_zoom(f)
            self.centerOn(r.center())
        self._fitted = True
        self._auto_fit = True  # (it follows the view's size until the user zooms)
        self._auto_actual = False

    # -- the editor's zoom commands ----------------------------------------------------
    def reset_zoom(self, animate: bool = True) -> None:
        """Zoom 100 %, keeping the middle of the view (View > Zoom 100% on the canvas)."""
        self._user_view()
        if animate and self.can_glide():
            self.glide_to(1.0, self.view_centre())
        else:
            self.set_zoom(1.0)

    def zoom_step(self, direction: int, animate: bool = True) -> None:
        """Zoom In (``direction`` > 0) / Zoom Out: the next round level of viewnav.ZOOM_LEVELS,
        or x1.25 / x0.8 with Settings > Canvas > "Zoom In and Zoom Out: Zoom by 25 %", keeping
        the middle of the view, as the editor's commands."""
        from ..prefs.settings import choice

        self.finish_glide()
        z = self._zoom
        if choice("opt/zoom_steps", VN.ZOOM_STEPS, "levels") == "factor":
            target = z * (1.25 if direction > 0 else 0.8)
        else:
            target = VN.next_zoom_level(z, direction)
        target = max(_MIN_ZOOM, min(_MAX_ZOOM, target))
        if abs(target - z) < 1e-9:
            return
        self._user_view()
        if animate and self.can_glide():
            self.glide_to(target, self.view_centre())
        else:
            self.set_zoom(target)

    def _user_view(self) -> None:
        """The user zoomed or panned: the view keeps its place as the tab's size changes."""
        self._auto_fit = False
        self._auto_actual = False
        self._fit_cap = None

    def set_fixed_view(self, rect: QtCore.QRectF, sx: float, sy: float) -> bool:
        """Show exactly ``rect`` (scene coordinates) at the scale ``sx`` x ``sy``, with no
        frame and no scroll bars: a window drawn inside its parent window or as a popup (its
        size is set by whoever places it). False when nothing changed."""
        key = (rect.x(), rect.y(), rect.width(), rect.height(), round(sx, 6), round(sy, 6))
        if key == self._fixed:
            return False
        self.finish_glide()
        if self._fixed is None:
            self.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.overlay_bars.set_mode("never")  # (the view's own bars go off too)
            self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self._fixed = key
        self._fitted = True
        self.setSceneRect(rect)
        self.setTransform(QtGui.QTransform.fromScale(max(sx, 1e-3), max(sy, 1e-3)))
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self._zoom = min(sx, sy)
        return True

    #: Simulate Settings > Open a page at ('fit' or 'actual': options.open_zoom). Set on the
    #: class by the Simulate window (every new tab of it)
    open_zoom = "fit"

    def show_actual_size(self) -> None:
        """The page at 100 %, as the runtime shows it: in the middle of the view when it
        fits, else its top left corner in view (8 px in). It stays so as the view's size
        changes (a panel shown or hidden, the window resized: resizeEvent) until the user
        zooms, pans or scrolls."""
        vp = self.viewport().rect()
        if self._page_rect().isNull() or vp.width() < 10 or vp.height() < 10:
            return
        if self._zoom != 1.0:
            self.set_zoom(1.0)
        self._place_actual()
        self._fitted = True
        self._auto_fit = False
        self._auto_actual = True

    def _page_rect(self) -> QtCore.QRectF:
        sc = self.scene()
        return sc.page_rect() if hasattr(sc, "page_rect") else sc.itemsBoundingRect()

    def _place_actual(self) -> None:
        """The 100 % view's place (:meth:`show_actual_size`) for the view's size now."""
        r = self._page_rect()
        vp = self.viewport().rect()
        if r.isNull() or vp.width() < 10 or vp.height() < 10:
            return
        cx = r.center().x() if r.width() + 16 <= vp.width() else r.left() - 8 + vp.width() / 2.0
        cy = r.center().y() if r.height() + 16 <= vp.height() else r.top() - 8 + vp.height() / 2.0
        self.centerOn(QtCore.QPointF(cx, cy))

    def _user_scrolled(self, *_a) -> None:
        """A scroll bar moved by the user: a 100 % view keeps the place they chose."""
        self._auto_actual = False

    def _first_view(self) -> None:
        """A new tab's first view: fitted to it (at most the zoom a page opens at in the
        editor: Settings > Canvas's "Largest zoom when a page opens"), or at 100 %
        (:attr:`open_zoom`)."""
        if self.open_zoom == "actual":
            self.show_actual_size()
        else:
            from ..panels.doc_tabs import open_zoom_cap

            self.fit_page(cap=open_zoom_cap() or None)

    def showEvent(self, ev):  # noqa: N802 -- Qt naming
        super().showEvent(ev)
        if not self._fitted and self._fixed is None:
            # (with the view as context: a tab closed before the timer fires cancels it)
            QtCore.QTimer.singleShot(0, self, self._first_view)

    def wheelEvent(self, ev):  # noqa: N802
        """Wheel handling, by the editor canvas's rules."""
        fwd = self.forward
        if fwd is not None:
            # a window inside its parent: the parent zooms and scrolls (this window with it)
            at = fwd.viewport().mapFromGlobal(self.viewport().mapToGlobal(ev.position().toPoint()))
            fwd.wheelEvent(
                QtGui.QWheelEvent(
                    QtCore.QPointF(at),
                    ev.globalPosition(),
                    ev.pixelDelta(),
                    ev.angleDelta(),
                    ev.buttons(),
                    ev.modifiers(),
                    ev.phase(),
                    ev.inverted(),
                    ev.source(),
                )
            )
            ev.accept()
            return
        self.finish_glide()  # (a wheel turn ends a glide at once)
        prefs = VN.WheelPrefs.from_settings()
        intent = VN.wheel_intent(self, ev, prefs)
        kind = intent[0]
        ev.accept()
        if kind == "none":
            return
        if self._fixed is not None:
            # a popup: its window scales it (its own zoom), not the page under it; it has
            # no scrolling of its own
            if kind == "zoom" and intent[1]:
                self.zoomRequested.emit(VN.wheel_steps(intent[1], intent[2]))
            return
        self._user_view()  # (a view the user moved keeps its place)
        if kind == "native":
            super().wheelEvent(ev)  # (Shift + the wheel scrolls sideways)
        elif kind == "scroll":
            dx, dy = VN.wheel_scroll_px(self, *intent[1:])
            if dx:
                self.horizontalScrollBar().setValue(int(round(self.horizontalScrollBar().value() + dx)))
            if dy:
                self.verticalScrollBar().setValue(int(round(self.verticalScrollBar().value() + dy)))
        else:
            target = VN.wheel_zoom_target(self, self._zoom, intent[1], prefs, intent[2])
            if target is not None:
                self.set_zoom(target, ev.position().toPoint())

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self.sync_scene_rect()
        if getattr(self, "_auto_actual", False) and self._fixed is None:
            # a page at 100 % keeps the runtime's place: its top
            # left corner, or the middle when it fits (the view's centre anchor moved it)
            self._place_actual()
        # a page fitted to the view stays fitted when the view's size changes (a dock
        # resized, a panel shown)
        if getattr(self, "_auto_fit", False) and self._fixed is None:
            t = getattr(self, "_refit_timer", None)
            if t is None:
                t = self._refit_timer = QtCore.QTimer(self)
                t.setSingleShot(True)
                t.setInterval(60)
                t.timeout.connect(
                    lambda: (
                        getattr(self, "_auto_fit", False)
                        and self.fit_page(cap=getattr(self, "_fit_cap", None))
                    )
                )
            t.start()

    # -- hit test -----------------------------------------------------------------
    def records_at(self, viewpos: QtCore.QPoint) -> List[int]:
        """The records drawn under ``viewpos``, topmost first."""
        sc = self.scene()
        layer = sc.sim_layer() if hasattr(sc, "sim_layer") else None
        hits = []
        for item in self.items(viewpos):
            d = item.data(PAGE_INDEX_ROLE) if item.isVisible() else None
            if isinstance(d, int) and not self._passes_clicks(d, item):
                hits.append((d, item))
        out: List[int] = []
        first = set()
        for k, (d, item) in enumerate(hits):
            if layer is None:
                r = d
            elif d not in first:
                # the topmost item of a picture that names a member (a composite's frame or
                # root item names none: the next item of the picture then does)
                first.add(d)
                r = layer.record_under(d, [h for e, h in hits[k:] if e == d])
            else:
                r = layer.record_under(d, [item])
                if r == d:
                    continue
            if r not in out:
                out.append(r)
        return out

    def record_at(self, viewpos: QtCore.QPoint) -> Optional[int]:
        """The record drawn topmost under ``viewpos`` (records_at), or None."""
        recs = self.records_at(viewpos)
        return recs[0] if recs else None

    def _passes_clicks(self, i: int, item) -> bool:
        """Is ``item`` an invisible container anchor that lets clicks through?"""
        if item.data(0) != i or not isinstance(item, QtWidgets.QAbstractGraphicsShapeItem):
            return False
        if item.pen().style() != QtCore.Qt.NoPen or item.brush().style() != QtCore.Qt.NoBrush:
            return False
        page = getattr(self.scene(), "_page", None)
        if page is None or not 0 <= i < len(page.objects):
            return False
        o = page.objects[i]
        if not o.members:
            return False
        for m in o.members:
            if 0 <= m < len(page.objects) and page.objects[m].raw.get("_type_code") in _INPUT_RECORDS:
                return False
        props = o.raw.get("_props") or {}
        return not any(str(props.get(k) or "").strip() for k in ("MVXSL", "MVYSL", "ROTSL"))

    def _input_records(self, page) -> List[int]:
        """The records of the page that take input: a touch / keyboard command record among
        their members, a button, a slider (cached per scene build)."""
        sc = self.scene()
        key = (id(page), getattr(sc, "build_serial", None))
        if getattr(self, "_inputs_key", None) == key:
            return self._inputs
        out = []
        objs = page.objects
        for i, o in enumerate(objs):
            tc = o.raw.get("_type_code")
            if tc in _INPUT_RECORDS:
                continue
            props = o.raw.get("_props") or {}
            if (
                tc == _BUTTON
                or any(
                    0 <= m < len(objs) and objs[m].raw.get("_type_code") in _INPUT_RECORDS
                    for m in (o.members or ())
                )
                or any(str(props.get(k) or "").strip() for k in ("MVXSL", "MVYSL", "ROTSL"))
            ):
                out.append(i)
        self._inputs_key, self._inputs = key, out
        return out

    def ans_at(self, viewpos: QtCore.QPoint) -> Tuple[int, ...]:
        """The ANs under ``viewpos`` for the simulation's hit test."""
        page = getattr(self.scene(), "_page", None)
        if page is None:
            return ()
        recs = [i for i in self.records_at(viewpos) if 0 <= i < len(page.objects)]
        # the objects that take input where the pointer is, also those that draw nothing (a
        # transparent target rect, a link's hot spot, a text shown only at run time): the
        # runtime picks by the object's extent; stacked by record order (a later record lies
        # above an earlier one, a member above its group)
        sp = self.mapToScene(viewpos)
        layer = self.scene().sim_layer() if hasattr(self.scene(), "sim_layer") else None
        if layer is not None:
            extra = []
            for i in self._input_records(page):
                if i in recs:
                    continue
                r = layer.pick_area(i)
                if r is not None and r.contains(sp):
                    extra.append(i)
            if extra:
                drawn = set(recs)
                recs = sorted(set(recs) | set(extra), key=lambda i: (-i, i not in drawn))
        out: List[int] = []
        # the buttons the page's Cicode drew (DspButton): above every page object, they take
        # the click (their AN may be no record of the page)
        drawn = getattr(layer, "_drawn", None) if layer is not None else None
        if drawn is not None and drawn.active():
            for an in drawn.buttons_at(sp):
                if an not in out:
                    out.append(an)
        for i in recs:
            an = page.objects[i].an
            if isinstance(an, int) and an > 0 and an not in out:
                out.append(an)
        for i in recs:
            j, guard = page.objects[i].parent, 0
            while j is not None and 0 <= j < len(page.objects) and guard < 64:
                an = page.objects[j].an
                if isinstance(an, int) and an > 0 and an not in out:
                    out.append(an)
                j = page.objects[j].parent
                guard += 1
        return tuple(out)

    def _event(self, kind: str, ev, **kw) -> InputEvent:
        pos = ev.position().toPoint()
        sp = self.mapToScene(pos)
        ans = self.ans_at(pos)
        mods = ev.modifiers()
        m = (
            (1 if mods & QtCore.Qt.ShiftModifier else 0)
            | (2 if mods & QtCore.Qt.ControlModifier else 0)
            | (4 if mods & QtCore.Qt.AltModifier else 0)
        )
        button = {QtCore.Qt.RightButton: "right", QtCore.Qt.MiddleButton: "middle"}.get(ev.button(), "left")
        return InputEvent(
            kind=kind,
            an=ans[0] if ans else None,
            ans=ans,
            x=sp.x(),
            y=sp.y(),
            modifiers=m,
            button=button,
            **kw,
        )

    # -- the page's control fields (ui/simulate/controls.py) ------------------------------
    def _field_at(self, viewpos: QtCore.QPoint) -> bool:
        """A control's text field (a proxy widget) is under ``viewpos``: it takes the mouse."""
        from .controls import is_field

        return any(is_field(it) for it in self.items(viewpos))

    def _field_focused(self) -> bool:
        from .controls import is_field

        sc = self.scene()
        return sc is not None and is_field(sc.focusItem())

    # -- mouse ----------------------------------------------------------------------
    def mousePressEvent(self, ev):  # noqa: N802
        self.finish_glide()  # (a press acts where the view is going)
        if self._field_at(ev.position().toPoint()) and not self._space:
            # the user types in a control: the field takes the click (no page input)
            self._in_field = True
            super().mousePressEvent(ev)
            return
        self._in_field = False
        if self._field_focused():
            self.scene().setFocusItem(None)
        if ev.button() == QtCore.Qt.MiddleButton or (self._space and ev.button() == QtCore.Qt.LeftButton):
            # a middle press that does not move is a click (the page's MBUTTON commands); one
            # that moves pans the view
            self._mid_click = (
                self._event("down", ev) if ev.button() == QtCore.Qt.MiddleButton and not self._space else None
            )
            self._mid_at = ev.position().toPoint()
            self._pan = ev.position().toPoint()
            self._user_view()  # (a panned view keeps its place)
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            ev.accept()
            return
        if ev.button() == QtCore.Qt.RightButton and ev.modifiers() & QtCore.Qt.ShiftModifier:
            self.objectMenuRequested.emit(self.ans_at(ev.position().toPoint()), ev.globalPosition().toPoint())
            ev.accept()
            return
        if ev.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            e = self._event("down", ev)
            self._press = (e, self.mapToScene(ev.position().toPoint()))
            self.inputEvent.emit(e)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if getattr(self, "_in_field", False) and ev.buttons():
            super().mouseMoveEvent(ev)
            return
        if self._pan is not None:
            p = ev.position().toPoint()
            if (
                getattr(self, "_mid_click", None) is not None
                and (p - self._mid_at).manhattanLength() > self.mid_click_px()
            ):
                self._mid_click = None  # (a pan, not a click)
            d = p - self._pan
            self._pan = p
            view = self.forward or self
            view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() - d.x())
            view.verticalScrollBar().setValue(view.verticalScrollBar().value() - d.y())
            ev.accept()
            return
        if self._press is not None:
            first, start = self._press
            sp = self.mapToScene(ev.position().toPoint())
            self.inputEvent.emit(
                InputEvent(
                    kind="drag",
                    an=first.an,
                    ans=first.ans,
                    x=sp.x(),
                    y=sp.y(),
                    dx=sp.x() - start.x(),
                    dy=sp.y() - start.y(),
                    modifiers=first.modifiers,
                    button=first.button,
                )
            )
            ev.accept()
            return
        self._pointer(ev.position().toPoint())
        super().mouseMoveEvent(ev)

    def _pointer(self, viewpos: Optional[QtCore.QPoint]) -> None:
        """The pointer's page position for the simulation ('move': DspGetMouse, the tool
        tips, the keyboard command cursor), at most every MOVE_EVERY s and only when it moved
        to another page pixel; None = it left the page (-1, -1)."""
        if viewpos is None:
            at = (-1, -1)
        else:
            sp = self.mapToScene(viewpos)
            at = (int(sp.x() // 1), int(sp.y() // 1))
        now = time.monotonic()
        if at == self._moved_to:
            self._move_pending = None
            self._move_timer.stop()
            return
        if viewpos is not None and now - self._moved_at < MOVE_EVERY:
            self._move_pending = QtCore.QPoint(viewpos)
            if not self._move_timer.isActive():
                left = MOVE_EVERY - (now - self._moved_at)
                self._move_timer.start(max(1, int(left * 1000 + 0.5)))
            return
        self._move_pending = None
        self._move_timer.stop()
        self._moved_to, self._moved_at = at, now
        if viewpos is None:
            self.inputEvent.emit(InputEvent(kind="move", x=-1.0, y=-1.0))
            return
        ans = self.ans_at(viewpos)
        self.inputEvent.emit(
            InputEvent(kind="move", an=ans[0] if ans else None, ans=ans, x=float(at[0]), y=float(at[1]))
        )

    def _flush_pointer(self) -> None:
        pos, self._move_pending = self._move_pending, None
        if pos is not None and self._press is None and self._pan is None:
            self._pointer(pos)

    def leaveEvent(self, ev):  # noqa: N802
        self._pointer(None)
        super().leaveEvent(ev)

    @staticmethod
    def mid_click_px() -> int:
        """A middle press that moves at most this far (screen px) is a click (the page's
        MBUTTON commands), further it pans: Settings > Canvas > "A drag starts after the
        pointer moves", as on the editor's canvas."""
        from ..editor.canvas_look import drag_start

        return drag_start()

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if getattr(self, "_in_field", False):
            self._in_field = False
            super().mouseReleaseEvent(ev)
            return
        if self._pan is not None:
            self._pan = None
            self.viewport().setCursor(QtCore.Qt.OpenHandCursor if self._space else QtCore.Qt.ArrowCursor)
            down, self._mid_click = getattr(self, "_mid_click", None), None
            if down is not None and ev.button() == QtCore.Qt.MiddleButton:
                up = self._event("up", ev)
                up.an, up.ans = down.an, down.ans
                self.inputEvent.emit(down)
                self.inputEvent.emit(up)
            ev.accept()
            return
        if self._press is not None:
            first, _start = self._press
            self._press = None
            e = self._event("up", ev)
            # the release goes to the object pressed (where it ends up is in x / y)
            e.an, e.ans, e.button = first.an, first.ans, first.button
            self.inputEvent.emit(e)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        if self._field_at(ev.position().toPoint()):
            self._in_field = True
            super().mouseDoubleClickEvent(ev)
            return
        if (
            ev.button() == QtCore.Qt.MiddleButton
            and not self._space
            and self._fixed is None
            and self._middle_double_click(ev)
        ):
            ev.accept()
            return
        if (
            ev.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton, QtCore.Qt.MiddleButton)
            and not self._space
        ):
            # (every button: the page's LBUTTON_DBL, RBUTTON_DBL, MBUTTON_DBL commands)
            self.inputEvent.emit(self._event("double", ev))
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def input_at(self, viewpos: QtCore.QPoint) -> bool:
        """Is there an object that takes input under ``viewpos``?"""
        ans = self.ans_at(viewpos)
        if not ans:
            return False
        page = getattr(self.scene(), "_page", None)
        if page is None:
            return True
        objs = page.objects
        record_ans = {o.an for o in objs if isinstance(o.an, int) and o.an > 0}
        takes = {objs[i].an for i in self._input_records(page) if 0 <= i < len(objs)}
        return any(an in takes or an not in record_ans for an in ans)

    def _middle_double_click(self, ev) -> bool:
        """Middle double-click on empty space: fit, as on the editor canvas."""
        from ..prefs.settings import choice

        if self.input_at(ev.position().toPoint()):
            return False
        what = choice("opt/middle_double_click", VN.MIDDLE_DOUBLE_CLICK, "fit_all")
        if what == "nothing":
            return False
        self.fit_page(animate=True)
        return True

    # -- keys -------------------------------------------------------------------------
    #: the Simulate window's own shortcuts: they stay the window's while the page has the
    #: focus (Open Page, Inspector, Copy Image: their keys in Settings > Keyboard,
    #: keymap.SIM_RESERVED; set on the class by SimulateWindow.apply_keys); every other key
    #: goes to the page
    RESERVED_KEYS = frozenset(
        ("Ctrl+O", "Ctrl+I", "Ctrl+Shift+C", "Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl++", "Ctrl+-")
    )
    #: Page Keys (the Simulate toolbar, option keys_to_page): False = the keys typed are not
    #: sent to the simulation, they stay ctgedit's (its shortcuts); the mouse still is.
    #: Set on the class by the Simulate window (every page of it).
    keys_to_page = True
    #: Simulate Settings > "Space + drag pans the page" (option space_pans); off: Space is a
    #: key of the page like any other. Set on the class by the Simulate window
    space_pans = True

    def event(self, ev):  # noqa: D401 -- Qt naming
        """The page owns the keyboard while it has the focus: its keyboard commands use F
        keys, letters and Ctrl / Alt / Shift combinations (a project's KEY_S_CTRL,
        KEY_A_CTRL ...: the editor's Ctrl+S, Ctrl+A must not take them). Only the Simulate
        window's own shortcuts (RESERVED_KEYS) reach the window."""
        if ev.type() == QtCore.QEvent.ShortcutOverride:
            # (a control's text field takes Ctrl+C / V / A too)
            seq = QtGui.QKeySequence(ev.keyCombination()).toString()
            if self._field_focused() or (self.keys_to_page and seq not in self.RESERVED_KEYS):
                ev.accept()
                return True
        return super().event(ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if self._field_focused():
            # typing in a control's text field: the field takes the keys, not the page
            super().keyPressEvent(ev)
            return
        if ev.key() == QtCore.Qt.Key_Space and self.space_pans:
            # (held: the repeats are the pan's too, not keys of the page)
            if not ev.isAutoRepeat():
                self._space = True
                self.viewport().setCursor(QtCore.Qt.OpenHandCursor)
            ev.accept()
            return
        if (
            ev.key() in (QtCore.Qt.Key_Shift, QtCore.Qt.Key_Control, QtCore.Qt.Key_Alt, QtCore.Qt.Key_Meta)
            or not self.keys_to_page
        ):
            # (Page Keys off: the key is not the simulation's)
            return super().keyPressEvent(ev)
        name = QtGui.QKeySequence(ev.key()).toString() or ev.text()
        mods = ev.modifiers()
        m = (
            (1 if mods & QtCore.Qt.ShiftModifier else 0)
            | (2 if mods & QtCore.Qt.ControlModifier else 0)
            | (4 if mods & QtCore.Qt.AltModifier else 0)
        )
        pos = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
        ans = self.ans_at(pos) if self.viewport().rect().contains(pos) else ()
        self.inputEvent.emit(
            InputEvent(kind="key", key=name, text=ev.text(), modifiers=m, an=ans[0] if ans else None, ans=ans)
        )
        ev.accept()

    def keyReleaseEvent(self, ev):  # noqa: N802
        if self._field_focused():
            super().keyReleaseEvent(ev)
            return
        if ev.key() == QtCore.Qt.Key_Space and not ev.isAutoRepeat() and (self.space_pans or self._space):
            self._space = False
            self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            ev.accept()
            return
        super().keyReleaseEvent(ev)

    # -- tool tips ----------------------------------------------------------------------
    def viewportEvent(self, ev):  # noqa: N802
        """Tool tips as the runtime shows them: the object's animated tool tip, else its own
        tool tip text (Access > General), else nothing (the editor's object tips are not
        shown here)."""
        if ev.type() == QtCore.QEvent.ToolTip:
            tip = self.tooltip_at(ev.pos())
            if tip:
                QtWidgets.QToolTip.showText(ev.globalPos(), tip, self.viewport())
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().viewportEvent(ev)

    def tooltip_at(self, viewpos: QtCore.QPoint) -> str:
        tip = self._runtime_tip(viewpos)
        extra = []
        if self.show_errors and self._errors:
            for an in self.ans_at(viewpos):
                if an in self._errors:
                    n, text, _lv = self._errors[an]
                    extra.append("AN %d: %d error%s. Last: %s" % (an, n, "" if n == 1 else "s", text))
        if self.show_numbers:
            ans = self.ans_at(viewpos)
            if ans:
                extra.append("Objects here: " + ", ".join("AN %d" % a for a in ans[:8]))
        if extra:
            return "\n\n".join(([tip] if tip else []) + extra)
        return tip

    def _runtime_tip(self, viewpos: QtCore.QPoint) -> str:
        i = self.record_at(viewpos)
        sc = self.scene()
        page = getattr(sc, "_page", None)
        if i is None or page is None:
            return ""
        layer = sc.sim_layer() if hasattr(sc, "sim_layer") else None
        tip = layer.tooltip_at(i) if layer is not None else None  # (the chain's innermost)
        if tip:
            return tip
        # a text still showing the placeholder: nothing fed it a value yet
        obj = page.objects[i]
        dv = getattr(getattr(obj, "animations", None), "display_value", None)
        if dv is not None and layer is not None:
            look = layer._applied.get(i)
            if look is None or look.text is None:
                return (
                    "No value from the simulator yet for this text (Settings > Simulate > "
                    "A text before its value)"
                )
        j, guard = i, 0
        while j is not None and 0 <= j < len(page.objects) and guard < 64:
            props = page.objects[j].raw.get("_props") or {}
            own = props.get("COMMENT")
            if own:
                return str(own).replace("<User>", "")
            j = page.objects[j].parent
            guard += 1
        return ""
