"""Canvas movement: zoom, wheel, touchpad, touch and panning (a canvas mixin)."""

from __future__ import annotations

import contextlib
import math
import os
import time

from PySide6 import QtCore, QtGui, QtWidgets

#: the Zoom In / Zoom Out ladder (fractions of 100 %)
ZOOM_LEVELS = (
    0.02,
    0.03,
    0.05,
    0.10,
    0.15,
    0.25,
    1.0 / 3.0,
    0.5,
    2.0 / 3.0,
    1.0,
    1.5,
    2.0,
    3.0,
    4.0,
    6.0,
    8.0,
    12.0,
    16.0,
    32.0,
    64.0,
)
#: the zoom limits of every page view (the editor's canvas and the Simulate window's page)
ZOOM_MIN, ZOOM_MAX = ZOOM_LEVELS[0], ZOOM_LEVELS[-1]
# Settings > Canvas > Zoom and scroll > "Fit Page keeps around the page" (view/fit_margin,
# page px): the one fit rule of the editor's Fit Page and the Simulate page's.
# ui/editor/view_consistency.apply_consistency_settings sets it
FIT_MARGIN_DEFAULT = 40
FIT_MARGIN_RANGE = (0, 400)
_FIT = {"margin": FIT_MARGIN_DEFAULT}
#: Settings > Canvas > Navigation > "Double-click the navigator or the ruler corner"
#: (opt/corner_double_click)
CORNER_DOUBLE_CLICK = (("fit_page", "Fit Page"), ("fit_all", "Fit All Objects"), ("nothing", "Nothing"))


def fit_margin() -> int:
    """The page px Fit Page keeps around the page (view/fit_margin)."""
    return _FIT["margin"]


def set_fit_margin(px) -> None:
    """``px`` within FIT_MARGIN_RANGE (None or garbled: the default)."""
    lo, hi = FIT_MARGIN_RANGE
    try:
        n = int(px)
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as the default
        n = FIT_MARGIN_DEFAULT
    _FIT["margin"] = max(lo, min(hi, n))


def fit_rect(page: QtCore.QRectF) -> QtCore.QRectF:
    """The rect Fit Page fits: the page plus :func:`fit_margin` on every side."""
    m = float(fit_margin())
    return page.adjusted(-m, -m, m, m)


def fit_zoom(page: QtCore.QRectF, size: QtCore.QSize) -> float:
    """The zoom Fit Page gives a page of ``page`` in a view of ``size`` (the viewport
    without scroll bars): :func:`fit_rect` in the view less 2 px a side, fitInView's own rule.
    0 with no page or no room."""
    r = fit_rect(page)
    if r.isNull() or r.width() <= 0 or r.height() <= 0 or size.width() <= 4 or size.height() <= 4:
        return 0.0
    return min((size.width() - 4) / r.width(), (size.height() - 4) / r.height())


def corner_double_click(view) -> None:
    """A double-click on the navigator or the ruler corner: Settings > Canvas's choice
    (:data:`CORNER_DOUBLE_CLICK`) on ``view``."""
    from ..prefs.settings import choice

    what = choice("opt/corner_double_click", CORNER_DOUBLE_CLICK, "fit_page")
    if what == "fit_all" and hasattr(view, "fit_all"):
        view.fit_all()
    elif what != "nothing":
        view.fit_page()


#: Settings > Canvas > "Zoom In and Zoom Out": the choices (opt/zoom_steps)
ZOOM_STEPS = (("levels", "Go to round levels"), ("factor", "Zoom by 25 %"))
#: Settings > Canvas > "Middle double-click" (opt/middle_double_click)
MIDDLE_DOUBLE_CLICK = (("fit_all", "Fit All Objects"), ("fit_page", "Fit Page"), ("nothing", "Nothing"))
#: Settings > Canvas > "Touch screen and pen" (view/touch)
TOUCH_MODES = (("auto", "On when there is a touch screen"), ("on", "On"), ("off", "Off"))
#: Settings > Canvas > "Cross hair" (opt/crosshair_style)
CROSSHAIR_STYLES = (("full", "Across the view"), ("short", "Short (40 px)"))
#: Settings > Canvas > "Wheel zoom step" (opt/wheel_zoom_step, percent)
WHEEL_STEP_RANGE = (5, 50)
WHEEL_STEP_DEFAULT = 15

#: the glide of a discrete zoom change
GLIDE_MS = 160
#: wheel / pan events this close together are one entry of the view history
BURST_GAP_S = 0.6
HISTORY_MAX = 50
#: the edge band of the drag scroll and its speeds (screen px)
EDGE_PX = 24
EDGE_SPEED = 18.0
EDGE_SPEED_MAX = 36.0
# Settings > Canvas > Zoom and scroll > Edge scroll speed (opt/edge_scroll_speed: the speeds
# above times its factor) and the drag the edge scroll waits for (screen px: 4, or Settings >
# Canvas > "A drag starts after the pointer moves" when it is longer);
# ui/editor/canvas_options.apply_canvas_settings sets them
EDGE_SPEED_FACTORS = {"slow": 0.5, "normal": 1.0, "fast": 2.0}
_EDGE = {"factor": 1.0, "drag": 4}


def set_edge_options(speed=None, drag_px=None) -> None:
    """The edge scroll's speed by name ('slow' / 'normal' / 'fast'; None leaves it, anything
    else: normal) and the drag start in screen px (None leaves it; at least 4)."""
    if speed is not None:
        _EDGE["factor"] = EDGE_SPEED_FACTORS.get(str(speed), 1.0)
    if drag_px is not None:
        _EDGE["drag"] = max(4, int(drag_px))


#: a long press opens the context menu
LONG_PRESS_MS = 600
#: Windows: wheel turns this close after a touchpad-like one are the same touchpad burst
PAD_GAP_S = 0.3
#: whether turns that are not whole notches mean a touchpad (Windows only: elsewhere Qt
#: reports a touchpad with pixel deltas or a scroll phase)
PAD_BY_DELTA = os.name == "nt"


def next_zoom_level(z: float, direction: int) -> float:
    """The ladder's next level above (``direction`` > 0) or below ``z``."""
    eps = 1e-3
    if direction > 0:
        return next((v for v in ZOOM_LEVELS if v > z * (1 + eps)), ZOOM_LEVELS[-1])
    return next((v for v in reversed(ZOOM_LEVELS) if v < z * (1 - eps)), ZOOM_LEVELS[0])


def windows_animations_on() -> bool:
    """Windows' 'Show animations in Windows' (SPI_GETCLIENTAREAANIMATION); True elsewhere or
    when it cannot be read."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        on = wintypes.BOOL(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)
        return bool(on.value) if ok else True
    except OSError, AttributeError, ValueError:  # swallow-ok: not on Windows / no user32
        return True


def touch_screen_present() -> bool:
    """A touch screen is attached (QInputDevice reports one)."""
    try:
        for d in QtGui.QInputDevice.devices():
            if d.type() == QtGui.QInputDevice.DeviceType.TouchScreen:
                return True
    except AttributeError, RuntimeError:  # swallow-ok: an older Qt without the list
        return False
    return False


def touchpad_like(angle: QtCore.QPoint) -> bool:
    """A wheel event's angle delta that is not a whole number of notches (120 units each):
    how a Windows precision touchpad's two-finger scroll arrives."""
    return bool(angle.x() % 120 or angle.y() % 120)


def from_touch_or_pen(e) -> bool:
    """A mouse event that a finger or a pen made (Qt synthesises those from touch and
    tablet input and names the device)."""
    try:
        dev = e.pointingDevice()
        kind = dev.type() if dev is not None else None
    except AttributeError, RuntimeError:  # swallow-ok: an event without a device
        kind = None
    T = QtGui.QInputDevice.DeviceType
    if kind in (T.TouchScreen, T.Stylus, T.Airbrush):
        return True
    try:
        return e.source() != QtCore.Qt.MouseEventNotSynthesized
    except AttributeError, RuntimeError:  # swallow-ok: no source on this event
        return False


def ease(t: float) -> float:
    """OutCubic (the glide's curve; the Simulate window's canvas glides with it too)."""
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def glide_allowed(view: QtWidgets.QWidget, on: bool) -> bool:
    """A zoom change of ``view`` glides: ``on`` (Settings > Canvas > "Animate zoom changes"),
    while it is shown, without CTG_NO_ANIMATION and with Windows' animations on."""
    return bool(
        on and view.isVisible() and not os.environ.get("CTG_NO_ANIMATION") and windows_animations_on()
    )


# ---- the wheel's rules: one for the editor's canvas and the Simulate window's ----------
class WheelPrefs:
    """Settings > Canvas's wheel choices: the wheel scrolls (Ctrl zooms) or zooms, a touchpad
    pans, the zoom step (percent a notch), the stop at 100 %."""

    __slots__ = ("scrolls", "touchpad_pans", "step", "stop_at_100")

    def __init__(self, scrolls=False, touchpad_pans=False, step=WHEEL_STEP_DEFAULT, stop_at_100=True):
        self.scrolls, self.touchpad_pans = bool(scrolls), bool(touchpad_pans)
        self.step, self.stop_at_100 = step, bool(stop_at_100)

    @classmethod
    def from_settings(cls) -> "WheelPrefs":
        from ..prefs.settings import app_settings, option

        try:
            step = int(app_settings().value("opt/wheel_zoom_step", WHEEL_STEP_DEFAULT))
        except TypeError, ValueError:  # swallow-ok: a garbled value reads as the default
            step = WHEEL_STEP_DEFAULT
        lo, hi = WHEEL_STEP_RANGE
        return cls(
            option("wheel_scrolls"),
            option("touchpad_pans"),
            max(lo, min(hi, step)),
            option("wheel_stop_at_100"),
        )


def wheel_is_touchpad(state, e: QtGui.QWheelEvent, ad: QtCore.QPoint, pd: QtCore.QPoint) -> bool:
    """The wheel event came from a touchpad (see the module notes); ``state`` keeps the last
    touchpad-like turn in ``_pad_at`` (a burst that began so stays a touchpad burst)."""
    if not pd.isNull() or e.phase() != QtCore.Qt.NoScrollPhase:
        return True
    try:
        dev = e.pointingDevice()
        if dev is not None and dev.type() == QtGui.QInputDevice.DeviceType.TouchPad:
            return True
    except AttributeError, RuntimeError:  # swallow-ok: an event without a device
        pass
    if not PAD_BY_DELTA:
        return False
    now = time.monotonic()
    if touchpad_like(ad):
        state._pad_at = now
        return True
    if now - state._pad_at < PAD_GAP_S:
        state._pad_at = now  # (a whole notch inside a touchpad burst)
        return True
    return False


def wheel_intent(state, e: QtGui.QWheelEvent, prefs: WheelPrefs) -> tuple:
    """What a wheel event asks of a page view."""
    ad, pd = e.angleDelta(), e.pixelDelta()
    if ad.isNull() and pd.isNull():
        return ("none",)  # (a touchpad's scroll end: nothing to do)
    mods = e.modifiers()
    ctrl = bool(mods & QtCore.Qt.ControlModifier)
    alt = bool(mods & QtCore.Qt.AltModifier)
    shift = bool(mods & QtCore.Qt.ShiftModifier)
    dx, dy = ad.x(), ad.y()
    if alt and dy == 0 and dx:
        dx, dy = 0, dx  # (Qt reports Alt + the wheel as a sideways turn)
    touchpad = wheel_is_touchpad(state, e, ad, pd)
    if ctrl or alt:
        return ("zoom", dy, alt and not ctrl)
    if prefs.scrolls:
        return ("native",)
    if touchpad and prefs.touchpad_pans:
        if not pd.isNull():
            return ("scroll", -pd.x(), -pd.y(), "px")
        return ("scroll", dx, dy, "notch")
    if (dx and not dy) or shift:
        if not pd.isNull() and dx and not dy:
            return ("scroll", -pd.x(), 0, "px")
        return ("scroll", dx if dx and not dy else dy, 0, "notch")
    return ("zoom", dy, False)


#: :func:`wheel_lines` for Windows' 'One screen at a time' (WHEEL_PAGESCROLL)
WHEEL_SCREEN = -1


def wheel_lines(raw: "int | None" = None) -> int:
    """The lines one wheel notch scrolls: Windows' mouse setting as Qt reads it (``raw``, or
    the application's style hints: what the plain wheel scrolls by in Qt's own scroll).
    0 means no scroll, as in Windows; WHEEL_SCREEN is a screen a notch (WHEEL_PAGESCROLL,
    which Qt reports below 0, or more than 100 lines); 3 without an application."""
    if raw is None:
        app = QtCore.QCoreApplication.instance()
        if not isinstance(app, QtGui.QGuiApplication):
            return 3
        raw = int(app.styleHints().wheelScrollLines())
    if raw == 0:
        return 0
    return raw if 0 < raw <= 100 else WHEEL_SCREEN


def notch_px(units: float, bar: QtWidgets.QScrollBar) -> float:
    """A wheel turn of ``units`` (120 = one notch) in screen pixels: Windows' lines a notch
    (:func:`wheel_lines`), a screen a notch, or 0 when Windows says no scroll."""
    lines = wheel_lines()
    if lines == WHEEL_SCREEN:
        return units / 120.0 * max(1, bar.pageStep())
    return units / 120.0 * lines * max(1, bar.singleStep())


def wheel_scroll_px(view, x: float, y: float, unit: str) -> tuple:
    """A ``("scroll", x, y, unit)`` intent in screen pixels for ``view`` (positive = the page
    moves left / up)."""
    if unit == "px":
        return x, y
    return (
        -notch_px(x, view.horizontalScrollBar()) if x else 0.0,
        -notch_px(y, view.verticalScrollBar()) if y else 0.0,
    )


def wheel_zoom_target(state, z: float, units: float, prefs: WheelPrefs, fine: bool = False):
    """The zoom a wheel turn of ``units`` from zoom ``z`` asks for (the step ** (units / 120);
    a quarter of it ``fine``), or None for no change: a zoom crossing 100 % stops there for one
    notch (``prefs.stop_at_100``; ``state`` keeps that stop in ``_detent``, ``_detent_dir``,
    ``_detent_at``)."""
    if not units:
        return None
    step = 1.0 + prefs.step / 100.0
    now = time.monotonic()
    sign = 1 if units > 0 else -1
    if prefs.stop_at_100 and not fine:
        if state._detent and (
            sign != state._detent_dir or now - state._detent_at > 0.8 or abs(z - 1.0) > 1e-6
        ):
            state._detent = 0
        if state._detent:
            # the stop at 100 % holds for one notch (120 units, a touchpad's many small ones
            # added up); what is left of this turn zooms on
            take = min(state._detent, abs(units))
            state._detent -= take
            state._detent_at = now
            units = sign * (abs(units) - take)
            if not units:
                return None
    power = units / 120.0 * (0.25 if fine else 1.0)
    target = z * step**power
    if prefs.stop_at_100 and not fine:
        if (z < 1.0 - 1e-6 and target > 1.0) or (z > 1.0 + 1e-6 and target < 1.0):
            target = 1.0
            state._detent, state._detent_dir, state._detent_at = 120, sign, now
    return target


def wheel_steps(units: float, fine: bool = False) -> float:
    """A wheel turn as notches of the zoom step (a quarter ``fine``): a popup's own zoom."""
    return units / 120.0 * (0.25 if fine else 1.0)


class _ViewState:
    """Where the view is: its transform, scroll position and centre (scene point)."""

    __slots__ = ("transform", "h", "v", "centre")

    def __init__(self, transform, h, v, centre):
        self.transform = QtGui.QTransform(transform)
        self.h, self.v = int(h), int(v)
        self.centre = QtCore.QPointF(centre)

    def zoom(self) -> float:
        return self.transform.m11() or 1.0

    def same(self, other) -> bool:
        return (
            other is not None and self.transform == other.transform and (self.h, self.v) == (other.h, other.v)
        )


class CanvasNavigation:
    """The canvas's navigation (see the module notes). Mixed into CanvasView: it uses the
    view's work-surface helpers (_zoom_to, _fit_to, sync_scene_rect, restore_view)."""

    #: Settings > Canvas: the wheel zoom step in percent, a touchpad pans (off by default), the
    #: zoom keys go to round levels, the wheel zoom stops at 100 %, zoom changes glide, what a
    #: middle double-click does, the drag scroll at the edge
    WHEEL_ZOOM_STEP = WHEEL_STEP_DEFAULT
    TOUCHPAD_PANS = False
    ZOOM_STEPS = "levels"
    WHEEL_STOP_AT_100 = True
    ANIMATE_ZOOM = True
    MIDDLE_DOUBLE_CLICK = "fit_all"
    DRAG_AUTOSCROLL = True

    def _init_navigation(self) -> None:
        self._glide = None  # (QVariantAnimation, start, end)
        self._own_history: tuple = ([], [])
        self._history_source = None  # () -> the document's DocumentUiState
        self._burst_at = 0.0  # the last wheel / pan event (monotonic)
        self._burst_start = None  # the view a burst left, until it moves
        self._pad_at = -1.0  # the last touchpad-like wheel turn (Windows)
        self._press_touch = False  # the last press came from a finger or a pen
        self._detent = 0  # the wheel's stop at 100 %: units left
        self._detent_dir = 0
        self._detent_at = 0.0
        self._edge_timer = QtCore.QTimer(self)
        self._edge_timer.setInterval(16)
        self._edge_timer.timeout.connect(self._edge_tick)
        self._edge_vp = QtCore.QPoint()
        self._edge_mods = QtCore.Qt.NoModifier
        self._left_down = False
        self._press_vp = QtCore.QPoint()
        self._dragged = False  # the pointer moved since the press
        self._edge_armed = True  # (a ruler drag: once clear of the edge band)
        self._touch_on = False
        # a burst pushes the view it left once the view really moves
        self.zoomChanged.connect(self._on_view_moved)
        self.horizontalScrollBar().valueChanged.connect(self._on_view_moved)
        self.verticalScrollBar().valueChanged.connect(self._on_view_moved)

    # ---- settings ------------------------------------------------------------------
    def apply_nav_options(self) -> None:
        """Read Settings > Canvas's navigation choices into this canvas, the wheel's too: the
        one place that reads them (the canvas calls it when it is made, Settings for every
        editor window after a change: canvas_options.apply_canvas_nav_everywhere)."""
        from ..prefs.settings import app_settings, choice, option

        st = app_settings()
        self.WHEEL_SCROLLS = option("wheel_scrolls")
        try:
            step = int(st.value("opt/wheel_zoom_step", WHEEL_STEP_DEFAULT))
        except TypeError, ValueError:  # swallow-ok: a garbled value reads as the default
            step = WHEEL_STEP_DEFAULT
        lo, hi = WHEEL_STEP_RANGE
        self.WHEEL_ZOOM_STEP = max(lo, min(hi, step))
        self.TOUCHPAD_PANS = option("touchpad_pans")
        self.ZOOM_STEPS = choice("opt/zoom_steps", ZOOM_STEPS, "levels")
        self.WHEEL_STOP_AT_100 = option("wheel_stop_at_100")
        self.ANIMATE_ZOOM = option("animate_zoom")
        self.MIDDLE_DOUBLE_CLICK = choice("opt/middle_double_click", MIDDLE_DOUBLE_CLICK, "fit_all")
        self.DRAG_AUTOSCROLL = option("drag_autoscroll")
        self.CROSSHAIR_READOUT = option("crosshair_readout")
        self.CROSSHAIR_STYLE = choice("opt/crosshair_style", CROSSHAIR_STYLES, "full")
        self.apply_touch(choice("view/touch", TOUCH_MODES, "auto"))
        bars = getattr(self, "overlay_bars", None)
        if bars is not None:
            from .overlay_scrollbars import SCROLLBAR_MODES

            bars.set_mode(choice("view/canvas_scrollbars", SCROLLBAR_MODES, "auto"))
        self.viewport().update()

    # ---- the zoom ladder -------------------------------------------------------------
    def zoom_step(self, direction: int) -> None:
        """Zoom In (``direction`` > 0) / Zoom Out: the next round level (or x1.25 / x0.8
        with Settings' 'Zoom by 25 %'), keeping the view centre; it glides. A step pressed
        during a glide steps on from where the glide is going."""
        self.finish_view_animation()
        z = self.zoom()
        if self.ZOOM_STEPS == "factor":
            target = z * (1.25 if direction > 0 else 0.8)
        else:
            target = next_zoom_level(z, direction)
        self.set_zoom(target)

    # ---- smooth, discrete view changes -----------------------------------------------
    def can_animate(self) -> bool:
        return glide_allowed(self, self.ANIMATE_ZOOM)

    def _state(self) -> _ViewState:
        vp = self.viewport()
        c = self.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))
        return _ViewState(
            self.transform(), self.horizontalScrollBar().value(), self.verticalScrollBar().value(), c
        )

    def _restore_state(self, st: _ViewState, emit: bool = True) -> None:
        if emit:
            self.restore_view(st.transform, (st.h, st.v))
            return
        blocked = self.blockSignals(True)
        try:
            self.restore_view(st.transform, (st.h, st.v))
        finally:
            self.blockSignals(blocked)

    @contextlib.contextmanager
    def view_change(self, history: bool = True, animate: bool = True):
        """Run a discrete view change inside: it pushes the view it leaves onto Previous
        View and glides (see the module notes)."""
        self.finish_view_animation()
        self._burst_start = None  # (a burst that did not move pushes nothing)
        start = self._state()
        glide = animate and self.can_animate()
        blocked = self.blockSignals(True) if glide else None
        try:
            yield
        finally:
            if glide:
                self.blockSignals(blocked)
        end = self._state()
        if end.same(start):
            return
        if history:
            self._push_history(start)
        if glide:
            self._restore_state(start, emit=False)
            self._start_glide(start, end)

    def _start_glide(self, start: _ViewState, end: _ViewState) -> None:
        anim = QtCore.QVariantAnimation(self)
        anim.setDuration(GLIDE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.Linear)  # (eased in _glide_frame)
        self._glide = (anim, start, end)
        anim.valueChanged.connect(self._glide_frame)
        anim.finished.connect(self.finish_view_animation)
        anim.start()

    def _glide_frame(self, value) -> None:
        g = self._glide
        if g is None:
            return
        _anim, a, b = g
        e = ease(float(value))
        if e >= 1.0:
            return  # (the end lands in finish_view_animation)
        za, zb = a.zoom(), b.zoom()
        z = math.exp(math.log(za) + (math.log(zb) - math.log(za)) * e)
        c = a.centre + (b.centre - a.centre) * e
        busy, self._nav_busy = self._nav_busy, True
        try:
            self.sync_scene_rect(zoom=z, extra=self._visible_at(c, z))
            self._set_scale(z)
            self.centerOn(c)
        finally:
            self._nav_busy = busy
        self.zoomChanged.emit(self.zoom())

    def view_animating(self) -> bool:
        return self._glide is not None

    def target_zoom(self) -> float:
        """The zoom the view shows once a running glide ends (else the zoom now)."""
        g = self._glide
        return g[2].zoom() if g is not None else self.zoom()

    def finish_view_animation(self) -> None:
        """A running glide ends at once, exactly where its change put the view."""
        g, self._glide = self._glide, None
        if g is None:
            return
        anim, _a, end = g
        with contextlib.suppress(RuntimeError, TypeError):
            anim.finished.disconnect(self.finish_view_animation)
        anim.stop()
        anim.deleteLater()
        self._restore_state(end)

    # ---- Previous View / Next View ---------------------------------------------------
    def set_history_source(self, fn) -> None:
        """``fn()`` -> the shown document's DocumentUiState (its view_history / view_future
        lists), or None: then the canvas keeps its own."""
        self._history_source = fn

    def _history(self) -> tuple:
        fn = self._history_source
        st = None
        if fn is not None:
            try:
                st = fn()
            except Exception:  # noqa: BLE001 -- the canvas's own lists then
                from ...infra.logsetup import note_exception

                note_exception("viewnav._history")
                st = None
        past = getattr(st, "view_history", None)
        future = getattr(st, "view_future", None)
        if isinstance(past, list) and isinstance(future, list):
            return past, future
        return self._own_history

    def _push_history(self, st: _ViewState) -> None:
        past, future = self._history()
        entry = (QtGui.QTransform(st.transform), QtCore.QPointF(st.centre))
        if past and past[-1][0] == entry[0] and past[-1][1] == entry[1]:
            return
        past.append(entry)
        del past[:-HISTORY_MAX]
        future.clear()
        self.viewHistoryChanged.emit()

    def _note_burst(self) -> None:
        """A wheel / pan event: the first of a burst remembers the view it leaves; it is
        pushed when the view then moves (:meth:`_on_view_moved`)."""
        self.finish_view_animation()  # (the burst starts where a glide was going)
        now = time.monotonic()
        if now - self._burst_at > BURST_GAP_S:
            self._burst_start = self._state()
        self._burst_at = now

    def _on_view_moved(self, *_args) -> None:
        st = self._burst_start
        if st is None or st.same(self._state()):
            return
        self._burst_start = None
        self._push_history(st)

    def _drop_burst(self) -> None:
        """Another page shows: a burst begun on the one before is forgotten."""
        self._burst_start = None
        self._burst_at = 0.0

    def can_go_back(self) -> bool:
        return bool(self._history()[0])

    def can_go_forward(self) -> bool:
        return bool(self._history()[1])

    def _go_to_entry(self, entry) -> None:
        tr, centre = entry
        z = tr.m11() or 1.0
        with self.view_change(history=False):
            self._auto_fit = False
            busy, self._nav_busy = self._nav_busy, True
            try:
                self.sync_scene_rect(zoom=z, extra=self._visible_at(centre, z))
                self._set_scale(z)
                self.centerOn(centre)
                self.sync_scene_rect()
            finally:
                self._nav_busy = busy
            self.zoomChanged.emit(self.zoom())

    def go_back(self) -> bool:
        """View > Previous View: the view before the last zoom or pan."""
        self.finish_view_animation()
        self._burst_start = None
        past, future = self._history()
        if not past:
            return False
        st = self._state()
        future.append((QtGui.QTransform(st.transform), QtCore.QPointF(st.centre)))
        del future[:-HISTORY_MAX]
        self._go_to_entry(past.pop())
        self.viewHistoryChanged.emit()
        return True

    def go_forward(self) -> bool:
        """View > Next View: back to where Previous View came from."""
        self.finish_view_animation()
        self._burst_start = None
        past, future = self._history()
        if not future:
            return False
        st = self._state()
        past.append((QtGui.QTransform(st.transform), QtCore.QPointF(st.centre)))
        del past[:-HISTORY_MAX]
        self._go_to_entry(future.pop())
        self.viewHistoryChanged.emit()
        return True

    def go_to_view(self, zoom: float, cx: float, cy: float) -> None:
        """Show the page at ``zoom`` centred on (cx, cy) (a saved view): it glides and
        Previous View comes back."""
        z = min(self.ZOOM_MAX, max(self.ZOOM_MIN, float(zoom)))
        self._go_to_entry_with_history(QtGui.QTransform.fromScale(z, z), QtCore.QPointF(float(cx), float(cy)))

    def _go_to_entry_with_history(self, tr, centre) -> None:
        self.finish_view_animation()
        z = tr.m11() or 1.0
        with self.view_change(history=True):
            self._auto_fit = False
            busy, self._nav_busy = self._nav_busy, True
            try:
                self.sync_scene_rect(zoom=z, extra=self._visible_at(centre, z))
                self._set_scale(z)
                self.centerOn(centre)
                self.sync_scene_rect()
            finally:
                self._nav_busy = busy
            self.zoomChanged.emit(self.zoom())

    def view_centre(self) -> QtCore.QPointF:
        """The scene point at the middle of the canvas."""
        return self._state().centre

    def pan_to(self, point: QtCore.QPointF) -> None:
        """Centre the canvas on scene ``point`` at once, at the zoom it has (a row picked
        in a list brings its object into view): Previous View comes back from it as from
        a pan, and moves close together are one step."""
        self._note_burst()
        z = self.zoom()
        self.sync_scene_rect(extra=self._visible_at(QtCore.QPointF(point), z))
        self.centerOn(QtCore.QPointF(point))

    # ---- zoom about a point (the zoom box's click, a pinch) -------------------------
    def zoom_about(
        self, factor: float, anchor: QtCore.QPoint, animate: bool = True, history: bool = True
    ) -> None:
        z = self.zoom()
        target = min(self.ZOOM_MAX, max(self.ZOOM_MIN, z * factor))
        if abs(target - z) < 1e-9:
            return
        with self.view_change(history=history, animate=animate):
            self._auto_fit = False
            self._zoom_to(target, QtCore.QPoint(anchor))
            self.zoomChanged.emit(self.zoom())

    # ---- the wheel, a touchpad -------------------------------------------------------
    def _scroll_by(self, dx: float, dy: float) -> None:
        """Scroll the canvas by (dx, dy) screen pixels (positive = the page moves left /
        up)."""
        if dx:
            hs = self.horizontalScrollBar()
            hs.setValue(int(round(hs.value() + dx)))
        if dy:
            vs = self.verticalScrollBar()
            vs.setValue(int(round(vs.value() + dy)))

    def wheel_prefs(self) -> WheelPrefs:
        """This canvas's wheel choices (Settings > Canvas, read by apply_nav_options into its
        attributes)."""
        return WheelPrefs(
            self.WHEEL_SCROLLS, self.TOUCHPAD_PANS, self.WHEEL_ZOOM_STEP, self.WHEEL_STOP_AT_100
        )

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:
        # (the rules are wheel_intent's: the Simulate window's canvas follows the same ones)
        self.finish_view_animation()
        intent = wheel_intent(self, e, self.wheel_prefs())
        kind = intent[0]
        if kind == "none":
            e.accept()
            return
        self._note_burst()
        if kind == "native":
            super().wheelEvent(e)  # (Shift + the wheel scrolls sideways)
            return
        if kind == "scroll":
            self._scroll_by(*wheel_scroll_px(self, *intent[1:]))
        else:
            self._wheel_zoom(intent[1], e.position().toPoint(), fine=intent[2])
        e.accept()

    def _wheel_zoom(self, units: float, at: QtCore.QPoint, fine: bool = False) -> None:
        z = self.zoom()
        target = wheel_zoom_target(self, z, units, self.wheel_prefs(), fine)
        if target is not None:
            self.zoom_by(target / z, at=at)

    # ---- the drag scroll at the canvas edge -------------------------------------------
    def _drag_active(self) -> bool:
        return bool(
            self._moving
            or self._resizing is not None
            or self._marquee is not None
            or self._draw_start is not None
            or self._node_drag is not None
            or self._guide_drag is not None
            or self._hotspot_drag is not None
            or self.__dict__.get("_zbox") is not None
            or (self._nodes and self._tool in ("polygon", "polyline", "pipe"))
        )

    @staticmethod
    def _edge_speed(p: int, size: int) -> float:
        f = _EDGE["factor"]
        if p < EDGE_PX:
            return -min(EDGE_SPEED_MAX * f, (EDGE_PX - p) / EDGE_PX * EDGE_SPEED * f)
        if p > size - EDGE_PX:
            return min(EDGE_SPEED_MAX * f, (EDGE_PX - (size - p)) / EDGE_PX * EDGE_SPEED * f)
        return 0.0

    def _edge_check(self, vp: QtCore.QPoint, mods, buttons=QtCore.Qt.LeftButton) -> None:
        """A mouse move during a drag: start or stop the edge scroll."""
        self._edge_vp = QtCore.QPoint(vp)
        self._edge_mods = mods
        if not buttons & QtCore.Qt.LeftButton:
            self._left_down = False  # (let go outside the window)
        if not self._left_down:
            self._edge_timer.stop()
            return
        if not self._dragged and (vp - self._press_vp).manhattanLength() >= _EDGE["drag"]:
            self._dragged = True
        if not (self.DRAG_AUTOSCROLL and self._dragged and self._drag_active() and not self._panning):
            self._edge_timer.stop()
            return
        r = self.viewport().rect()
        near = self._edge_speed(vp.x(), r.width()) or self._edge_speed(vp.y(), r.height())
        if near and not self._edge_timer.isActive():
            self._edge_timer.start()
        elif not near:
            self._edge_timer.stop()

    def _edge_ruler_press(self, vp: QtCore.QPoint) -> None:
        """A press on a ruler that drags a guideline out: the edge scroll follows the drag
        (the ruler holds the mouse, so the canvas sees none of its moves)."""
        self._left_down = True
        self._press_vp = QtCore.QPoint(vp)
        self._dragged = False
        self._edge_armed = False

    def _edge_ruler_move(self, vp: QtCore.QPoint, mods) -> None:
        """A move of a guideline dragged out of a ruler (``vp`` in viewport coordinates):
        the edge scroll as for a drag on the canvas, once the pointer has been on the
        canvas clear of its edge band."""
        if not self._edge_armed:
            band = self.viewport().rect().adjusted(EDGE_PX, EDGE_PX, -EDGE_PX, -EDGE_PX)
            self._edge_armed = band.contains(vp)
        if self._edge_armed:
            self._edge_check(vp, mods)

    def _edge_ruler_release(self) -> None:
        self._left_down = False
        self._edge_armed = True
        self._edge_timer.stop()

    def _edge_tick(self) -> None:
        if not (self._left_down and self.DRAG_AUTOSCROLL and self._drag_active()):
            self._edge_timer.stop()
            return
        vp = self._edge_vp
        r = self.viewport().rect()
        sx = self._edge_speed(vp.x(), r.width())
        sy = self._edge_speed(vp.y(), r.height())
        if not sx and not sy:
            self._edge_timer.stop()
            return
        hs, vs = self.horizontalScrollBar(), self.verticalScrollBar()
        if (
            (sx > 0 and hs.value() >= hs.maximum())
            or (sx < 0 and hs.value() <= hs.minimum())
            or (sy > 0 and vs.value() >= vs.maximum())
            or (sy < 0 and vs.value() <= vs.minimum())
        ):
            # at the end of the scroll range: it grows with the drag (within +/- 32000)
            z = max(self.zoom(), 0.01)
            seen = self.mapToScene(r).boundingRect()
            busy, self._nav_busy = self._nav_busy, True
            try:
                self.sync_scene_rect(extra=seen.translated(sx / z * 2, sy / z * 2))
            finally:
                self._nav_busy = busy
        self._scroll_by(sx, sy)
        ev = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(vp),
            QtCore.QPointF(self.viewport().mapToGlobal(vp)),
            QtCore.Qt.NoButton,
            QtCore.Qt.LeftButton,
            self._edge_mods,
        )
        self.mouseMoveEvent(ev)

    # ---- touch screens and pens ------------------------------------------------------
    def apply_touch(self, mode: str) -> None:
        """'on' / 'off' / 'auto' (on when a touch screen is attached): two fingers pan and
        pinch-zoom, a long press opens the context menu."""
        on = mode == "on" or (mode == "auto" and touch_screen_present())
        vp = self.viewport()
        if on == self._touch_on:
            return
        self._touch_on = on
        vp.setAttribute(QtCore.Qt.WA_AcceptTouchEvents, on)
        for g in (QtCore.Qt.PinchGesture, QtCore.Qt.PanGesture, QtCore.Qt.TapAndHoldGesture):
            if on:
                vp.grabGesture(g)
            else:
                vp.ungrabGesture(g)
        if on:
            QtWidgets.QTapAndHoldGesture.setTimeout(LONG_PRESS_MS)

    def touch_enabled(self) -> bool:
        return self._touch_on

    def pinch(self, scale: float, centre: QtCore.QPoint) -> None:
        """A pinch step: zoom by ``scale`` about viewport point ``centre`` (2 % to 6400 %)."""
        if scale > 0:
            self._note_burst()
            self.zoom_by(scale, at=centre)

    def _gesture_event(self, e) -> bool:
        handled = False
        pinch = e.gesture(QtCore.Qt.PinchGesture)
        if pinch is not None:
            c = self.viewport().mapFromGlobal(pinch.centerPoint().toPoint())
            if pinch.changeFlags() & QtWidgets.QPinchGesture.ScaleFactorChanged:
                self.pinch(float(pinch.scaleFactor()), c)
            handled = True
        pan = e.gesture(QtCore.Qt.PanGesture)
        if pan is not None and pinch is None:
            d = pan.delta()
            self._note_burst()
            self._scroll_by(-d.x(), -d.y())
            handled = True
        hold = e.gesture(QtCore.Qt.TapAndHoldGesture)
        if hold is not None and not self._press_touch:
            # (Qt's recogniser also fires on a mouse button held still: only a finger or a
            # pen opens the menu)
            e.ignore(hold)
        elif hold is not None and hold.state() == QtCore.Qt.GestureFinished:
            g = hold.position().toPoint()
            self.long_press(self.viewport().mapFromGlobal(g), g)
            handled = True
        if handled:
            e.accept()
        return handled

    # ---- Space pans wherever the pointer is ---------------------------------------
    def begin_space_pan(self) -> None:
        """Space held (the canvas's own key, or forwarded with the pointer over the
        canvas): a left drag pans."""
        if not self._space_pan:
            self._space_pan = True
            self.viewport().setCursor(QtCore.Qt.OpenHandCursor)

    def end_space_pan(self) -> None:
        if self._space_pan:
            self._space_pan = False
            self._panning = False
            self.viewport().setCursor(self._idle_cursor())


class SpacePanForwarder(QtCore.QObject):
    """A rung of the application's event filter (main_window_base._AppEventFilter): Space
    pressed while the pointer is over a canvas and the keyboard is in a widget that does not
    type (not a text field or a spin box) pans that canvas, as if it had the focus."""

    TYPES = frozenset({QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease})
    #: the widgets that keep Space (it types there)
    KEEP = (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)
    #: a widget with this property set keeps Space as a key of its own (the Objects panel's
    #: tree: Space hides the selection there)
    KEEP_SPACE_PROP = "ctg_keeps_space"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view = None  # the canvas a forwarded Space pans

    def handle(self, obj, ev, t) -> bool:
        if ev.key() != QtCore.Qt.Key_Space or ev.modifiers() & ~QtCore.Qt.KeypadModifier:
            return False
        fw = QtWidgets.QApplication.focusWidget()
        if obj is not fw:
            return False  # (once per key: the focus widget's delivery)
        if t == QtCore.QEvent.Type.KeyRelease:
            view = self._view
            if view is None:
                return False
            if ev.isAutoRepeat():
                return True  # (Space still held: keep the pan and the view)
            self._view = None
            with contextlib.suppress(RuntimeError):
                view.end_space_pan()
            return True
        if self._view is not None:
            return True  # (auto-repeat while held)
        if fw is None or isinstance(fw, self.KEEP) or QtWidgets.QApplication.activePopupWidget():
            return False
        if fw.property(self.KEEP_SPACE_PROP):
            return False  # (Space is the widget's own key)
        if isinstance(fw, QtWidgets.QComboBox) and fw.isEditable():
            return False
        view = self._canvas_under_pointer()
        if view is None or view is fw or view.isAncestorOf(fw):
            return False  # (the canvas has the keyboard: its own key)
        if ev.isAutoRepeat():
            return False
        self._view = view
        view.begin_space_pan()
        return True

    @staticmethod
    def _canvas_under_pointer():
        from .canvas import CanvasView

        w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        while w is not None:
            if isinstance(w, CanvasView):
                win = w.window()
                return w if win is not None and win.isActiveWindow() else None
            w = w.parentWidget()
        return None
