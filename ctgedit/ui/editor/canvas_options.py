"""Settings > Canvas."""

from __future__ import annotations

from PySide6 import QtWidgets

#: Settings > Canvas > Mouse, under "Mouse wheel" (opt/touchpad_pans, off by default): Windows
#: sends a touchpad's two-finger scroll as steps smaller than one notch, and a smooth-scrolling
#: mouse or a Remote Desktop connection sends such steps too (ui/editor/viewnav.wheel_is_touchpad)
TOUCHPAD_TEXT = (
    "Touchpad: two fingers pan, a pinch zooms (a smooth-scrolling mouse or Remote Desktop pans too)"
)
TOUCHPAD_TIP = (
    "A touchpad scrolls in steps smaller than one notch. A smooth-scrolling mouse and Remote "
    "Desktop send such steps too and cannot be told apart. Off: small steps act as whole notches. "
    "A pinch always zooms"
)


def editor_windows(also=None) -> list:
    """The open editor windows (MainWindow.editor_windows), and ``also`` when it is not one
    of them."""
    try:
        from ..main_window import MainWindow

        wins = list(MainWindow.editor_windows())
    except ImportError:  # swallow-ok: no editor windows
        wins = []
    if also is not None and all(w is not also for w in wins):
        wins.append(also)
    return wins


def _combo(choices, current: str, tip: str = "") -> QtWidgets.QComboBox:
    c = QtWidgets.QComboBox()
    for value, label in choices:
        c.addItem(label, value)
    c.setCurrentIndex(max(0, c.findData(current)))
    if tip:
        c.setToolTip(tip)
    return c


class CanvasNavControls:
    """The controls, read from the settings when made."""

    def __init__(self, dialog=None):
        from .navbar import OPACITY_RANGE, POSITIONS, overlay_opacity
        from .overlay_scrollbars import SCROLLBAR_MODES
        from ..prefs.settings import app_settings, choice, option
        from .viewnav import (
            CROSSHAIR_STYLES,
            MIDDLE_DOUBLE_CLICK,
            TOUCH_MODES,
            WHEEL_STEP_DEFAULT,
            WHEEL_STEP_RANGE,
            ZOOM_STEPS,
        )

        self.wheel_step = QtWidgets.QSpinBox()
        self.wheel_step.setRange(*WHEEL_STEP_RANGE)
        self.wheel_step.setSuffix(" %")
        try:
            self.wheel_step.setValue(int(app_settings().value("opt/wheel_zoom_step", WHEEL_STEP_DEFAULT)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self.wheel_step.setValue(WHEEL_STEP_DEFAULT)
        self.wheel_step.setToolTip("How much one notch of the wheel zooms")
        self.stop_100 = QtWidgets.QCheckBox("Stop the wheel zoom at 100 %")
        self.stop_100.setChecked(option("wheel_stop_at_100"))
        self.touchpad = QtWidgets.QCheckBox(TOUCHPAD_TEXT)
        self.touchpad.setChecked(option("touchpad_pans"))
        self.touchpad.setToolTip(TOUCHPAD_TIP)
        self.zoom_steps = _combo(
            ZOOM_STEPS,
            choice("opt/zoom_steps", ZOOM_STEPS, "levels"),
            "Round levels: 50 %, 66.7 %, 100 %, 150 % and so on",
        )
        self.animate = QtWidgets.QCheckBox("Animate zoom changes")
        self.animate.setChecked(option("animate_zoom"))
        self.animate.setToolTip("Off while Windows' animations are off")
        self.middle = _combo(
            MIDDLE_DOUBLE_CLICK, choice("opt/middle_double_click", MIDDLE_DOUBLE_CLICK, "fit_all")
        )
        self.autoscroll = QtWidgets.QCheckBox("Scroll when dragging near the edge")
        self.autoscroll.setChecked(option("drag_autoscroll"))
        self.touch = _combo(
            TOUCH_MODES,
            choice("view/touch", TOUCH_MODES, "auto"),
            "Two fingers pan and pinch, a long press opens the menu",
        )
        self.scrollbars = _combo(SCROLLBAR_MODES, choice("view/canvas_scrollbars", SCROLLBAR_MODES, "auto"))
        self.navbar_pos = _combo(POSITIONS, choice("view/navbar_pos", POSITIONS, "bottom_centre"))
        self.opacity = QtWidgets.QSpinBox()
        self.opacity.setRange(*OPACITY_RANGE)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(overlay_opacity())
        self.opacity.setToolTip(
            "Below 100 %, the page shows through the navigation bar, the navigator and the selection toolbar"
        )
        self.nav_autohide = QtWidgets.QCheckBox("Hide the navigator while the whole page is visible")
        self.nav_autohide.setChecked(bool(app_settings().value("view/navigator_autohide", True, type=bool)))
        self.cross_style = _combo(CROSSHAIR_STYLES, choice("opt/crosshair_style", CROSSHAIR_STYLES, "full"))
        self.cross_readout = QtWidgets.QCheckBox("Show the position at the pointer")
        self.cross_readout.setChecked(option("crosshair_readout"))

    def lay_out_mouse(self, section) -> None:
        """The touchpad choice in the Canvas page's Mouse section, right under "Mouse wheel"
        (it decides what the small steps of the wheel do)."""
        section.check(self.touchpad)

    def lay_out(self, page, rows=None) -> None:
        """The sections on the Canvas page (after its Mouse section). ``rows(section, sub)``
        adds the Settings window's declared rows of that section (SettingsDialog.ROWS):
        the plain ones after Middle double-click and after the navigator's switch, the
        ones under another (``sub``) under Scroll when dragging near the edge."""

        def declared(sec, sub=None):
            if rows is not None:
                rows(sec, sub)

        s = page.section("Zoom and scroll")
        s.row("Wheel zoom step:", self.wheel_step)
        s.check(self.stop_100)
        s.row("Zoom In and Zoom Out:", self.zoom_steps)
        s.check(self.animate)
        s.row("Middle double-click:", self.middle)
        declared(s, False)
        s.check(self.autoscroll)
        declared(s, True)
        s.row("Scroll bars:", self.scrollbars)
        s.row("Touch screen and pen:", self.touch)
        s = page.section("Navigation")
        s.row("Navigation bar:", self.navbar_pos)
        s.row("Overlay opacity:", self.opacity)
        s.check(self.nav_autohide)
        declared(s)
        s.row("Cross hair:", self.cross_style)
        s.check(self.cross_readout)

    def show(self, get) -> None:
        """The stored choices from ``get(key, default)`` (Import / Restore Defaults)."""

        def on(key, default):
            v = get(key, default)
            return v in (True, "true", "1", 1) if not isinstance(v, bool) else v

        def pick(combo, key, default):
            combo.setCurrentIndex(max(0, combo.findData(get(key, default))))

        try:
            self.wheel_step.setValue(int(get("opt/wheel_zoom_step", 15)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self.wheel_step.setValue(15)
        self.stop_100.setChecked(on("opt/wheel_stop_at_100", True))
        from ..prefs.settings import OPTION_DEFAULTS

        self.touchpad.setChecked(on("opt/touchpad_pans", OPTION_DEFAULTS["touchpad_pans"]))
        pick(self.zoom_steps, "opt/zoom_steps", "levels")
        self.animate.setChecked(on("opt/animate_zoom", True))
        pick(self.middle, "opt/middle_double_click", "fit_all")
        self.autoscroll.setChecked(on("opt/drag_autoscroll", True))
        pick(self.touch, "view/touch", "auto")
        pick(self.scrollbars, "view/canvas_scrollbars", "auto")
        pick(self.navbar_pos, "view/navbar_pos", "bottom_centre")
        try:
            self.opacity.setValue(int(get("ui/overlay_opacity", 100)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self.opacity.setValue(100)
        self.nav_autohide.setChecked(on("view/navigator_autohide", True))
        pick(self.cross_style, "opt/crosshair_style", "full")
        self.cross_readout.setChecked(on("opt/crosshair_readout", True))

    def defaults(self) -> None:
        self.show(lambda key, default: default)

    def choices(self) -> dict:
        return {
            "opt/wheel_zoom_step": self.wheel_step.value(),
            "opt/wheel_stop_at_100": self.stop_100.isChecked(),
            "opt/touchpad_pans": self.touchpad.isChecked(),
            "opt/zoom_steps": self.zoom_steps.currentData(),
            "opt/animate_zoom": self.animate.isChecked(),
            "opt/middle_double_click": self.middle.currentData(),
            "opt/drag_autoscroll": self.autoscroll.isChecked(),
            "view/touch": self.touch.currentData(),
            "view/canvas_scrollbars": self.scrollbars.currentData(),
            "view/navbar_pos": self.navbar_pos.currentData(),
            "ui/overlay_opacity": self.opacity.value(),
            "view/navigator_autohide": self.nav_autohide.isChecked(),
            "opt/crosshair_style": self.cross_style.currentData(),
            "opt/crosshair_readout": self.cross_readout.isChecked(),
        }

    @staticmethod
    def apply(window=None) -> None:
        """The stored choices to every open editor window (after OK / Apply; ``window``, the
        one Settings was opened from, too)."""
        apply_canvas_nav_everywhere(window)


def apply_canvas_nav_everywhere(also=None) -> None:
    """Settings > Canvas's navigation choices (stored) to every open editor window's canvas
    and overlays (:func:`editor_windows`): a second window and a window a tab moved to too."""
    for w in editor_windows(also):
        try:
            apply_canvas_nav(w)
        except RuntimeError:  # swallow-ok: a window being closed
            continue


def apply_canvas_nav(window) -> None:
    """Settings > Canvas's navigation choices (stored) to ``window``'s canvas and overlays."""
    view = getattr(window, "view", None)
    if view is not None and hasattr(view, "apply_nav_options"):
        view.apply_nav_options()
    nb = getattr(window, "navbar", None)
    if nb is not None:
        nb.set_position(nb.stored_position())
        nb._theme_changed()
    hud = getattr(window, "hud", None)
    if hud is not None:
        hud._theme_changed()  # (the selection toolbar: Overlay opacity)
    nav = getattr(window, "navigator", None)
    if nav is not None:
        from ..prefs.settings import app_settings

        nav.autohide = bool(app_settings().value("view/navigator_autohide", True, type=bool))
        nav._theme_changed()
        nav.refresh()


# ------------------------------------------------------------- the canvas settings
#: the canvas settings: Settings > Canvas (Mouse, Selecting, Grid and guidelines, Zoom and
#: scroll, Navigation), Editing (New objects, Moving and resizing, Text) and Appearance >
#: Canvas (Guideline colour). Their rows are in settings_dialog.SettingsDialog.ROWS; a change
#: applies at once (apply_canvas_settings, an apply hook of each), and at a window's start
#: (wire_canvas_settings)
CANVAS_AUDIT_KEYS = (
    "canvas/pointer_reach",
    "canvas/drag_start",
    "opt/mouse_back_forward",
    "opt/page_double_click",
    "view/hover_outline",
    "view/guide_snap",
    "view/guide_colour",
    "view/frame_zoom_max",
    "opt/edge_scroll_speed",
    "view/navigator_size",
    "view/navigator_corner",
    "opt/esc_while_drawing",
    "view/slow_mouse_factor",
    "opt/text_enter",
    "view/control_pictures",
)
#: Zoom to an object zooms in at most (%, '0' = no limit)
FRAME_ZOOM_MAX = (("100", "100 %"), ("200", "200 %"), ("400", "400 %"), ("800", "800 %"), ("0", "No limit"))


def apply_canvas_settings(window) -> None:
    """Read the settings of :data:`CANVAS_AUDIT_KEYS` into the canvas modules and
    ``window``'s canvas and navigator (once at the start, then after each change)."""
    from . import canvas_look, inplace_text, navigator, viewnav
    from ..prefs.settings import app_settings

    st = app_settings()

    def text(key, default):
        try:
            return str(st.value(key, default) or default)
        except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
            return default

    canvas_look.set_pointer_options(text("canvas/pointer_reach", "near"), text("canvas/drag_start", "short"))
    from ...render import control_pictures

    try:
        control_pictures.set_enabled(bool(st.value("view/control_pictures", True, type=bool)))
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        control_pictures.set_enabled(True)
    canvas_look.set_slow_mouse_factor(text("view/slow_mouse_factor", "quarter"))
    canvas_look.set_guide_colour(text("view/guide_colour", "default"))
    viewnav.set_edge_options(speed=text("opt/edge_scroll_speed", "normal"))
    inplace_text.set_enter_stores(text("opt/text_enter", "newline") == "store")
    if isinstance(window, QtWidgets.QWidget):
        # the text fields already built name the keys anew (the others when they show)
        from .caption_edit import CaptionEdit

        for ed in window.findChildren(CaptionEdit):
            ed.refresh_tip()
    navigator.set_panel_options(
        text("view/navigator_size", "medium"), text("view/navigator_corner", "bottom_right")
    )
    v = getattr(window, "view", None)
    if v is not None:
        try:
            v.HOVER_OUTLINE = bool(st.value("view/hover_outline", True, type=bool))
        except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
            v.HOVER_OUTLINE = True
        v.GUIDE_SNAP_PX = v.GUIDE_SNAPS.get(text("view/guide_snap", "normal"), type(v).GUIDE_SNAP_PX)
        cap = text("view/frame_zoom_max", "400")
        cap = cap if cap in dict(FRAME_ZOOM_MAX) else "400"
        v.FRAME_ZOOM_MAX = int(cap) / 100.0 if cap != "0" else v.ZOOM_MAX
        side = text("opt/mouse_back_forward", "view")
        v.SIDE_BUTTONS = side if side in ("view", "tabs", "nothing") else "view"
        v.EMPTY_DOUBLE_CLICK = (
            "page_props" if text("opt/page_double_click", "nothing") == "page_props" else "nothing"
        )
        v.ESC_FINISHES = text("opt/esc_while_drawing", "cancel") == "finish"
        v.viewport().update()
    nav = getattr(window, "navigator", None)
    if nav is not None:
        nav.settings_changed()
    rulers = getattr(window, "rulers", None)
    if rulers is not None:
        for r in (getattr(rulers, "top", None), getattr(rulers, "left", None)):
            if isinstance(r, QtWidgets.QWidget):
                r.update()  # (the guidelines' marks: their colour)


def wire_canvas_settings(window) -> None:
    """At a window's start: its canvas's mouse back / forward buttons and its double-click on
    the empty page reach the window's commands, and the settings apply."""
    v = window.view
    v.pageStepRequested.connect(lambda step: _step_tab(window, step))
    v.pageDoubleClicked.connect(lambda: _trigger(window, "act_page_props"))
    apply_canvas_settings(window)
    from . import view_consistency

    view_consistency.apply_consistency_settings(window)


def _step_tab(window, step: int) -> None:
    """The tab before / after the one shown, along the tabs (Window > Previous / Next Page
    without the Ctrl+Tab order)."""
    fn = getattr(window, "switch_page", None)
    if fn is not None:
        fn(step, via_tab=False)


def _trigger(window, name: str) -> None:
    a = getattr(window, name, None)
    if a is not None and a.isEnabled():
        a.trigger()


class ColourChoice(QtWidgets.QWidget):
    """A Settings row's colour (SettingRow kind 'colour'): a drop-down of the default and
    'Custom', with a colour button beside it while Custom is chosen. Its value is the first
    choice's value or '#rrggbb' (as Selection colour). The Settings row watches its
    drop-down and its button."""

    def __init__(self, choices, title: str = "Colour", parent=None):
        super().__init__(parent)
        from ..colour_picker import ColourButton

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QtWidgets.QComboBox(self)
        for value, label in choices:
            self.combo.addItem(label, value)
        self._default = choices[0][0]
        self.button = ColourButton(0xFF00FF, self, title=title, flash=False, transparent=False)
        lay.addWidget(self.combo)
        lay.addWidget(self.button)
        lay.addStretch(1)
        self.button.setVisible(False)
        self.combo.currentIndexChanged.connect(self._shown)

    def _shown(self, _i=None) -> None:
        self.button.setVisible(self.combo.currentData() == "custom")

    def value(self) -> str:
        if self.combo.currentData() == "custom":
            pair = self.button.pair()
            if pair is not None:
                return "#%06x" % (int(pair[0]) & 0xFFFFFF)
        return self._default

    def set_value(self, value) -> None:
        s = str(value or "").strip().lower()
        custom = len(s) == 7 and s.startswith("#")
        if custom:
            try:
                self.button.set_pair(int(s[1:], 16))
            except ValueError:  # swallow-ok: a garbled colour shows the default
                custom = False
        self.combo.setCurrentIndex(max(0, self.combo.findData("custom" if custom else self._default)))
        self._shown()

    def setToolTip(self, tip: str) -> None:  # noqa: N802 -- Qt naming
        super().setToolTip(tip)
        self.combo.setToolTip(tip)


def _register_settings_hooks() -> None:
    from ..prefs import settings_pages as SP
    from . import view_consistency  # noqa: F401 -- (its own hooks register on import)

    for key in CANVAS_AUDIT_KEYS:
        if apply_canvas_settings not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, apply_canvas_settings)


_register_settings_hooks()
