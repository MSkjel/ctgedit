"""The navigation bar over the canvas."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import re

from PySide6 import QtCore, QtGui, QtWidgets

from ..ribbon import announce
from ..style.theme import px, set_note_role

#: (id, the window's action attribute or ('tool', tool id), label, overflow rank: the
#: lowest rank goes into the '…' menu first; 0 = always on the bar)
BUTTONS = (
    ("pan", ("tool", "pan"), "Hand", 0),
    ("zoom_box", ("tool", "zoom"), "Zoom Box", 6),
    ("zoom_out", "act_zoom_out", "Zoom Out", 0),
    ("zoom", None, "Zoom level", 0),
    ("zoom_in", "act_zoom_in", "Zoom In", 0),
    ("fit_page", "act_zoom_fit", "Fit Page", 0),
    ("fit_all", "act_zoom_all", "Fit All Objects", 9),
    ("zoom_sel", "act_zoom_sel", "Zoom to Selection", 8),
    ("|", None, "", 0),
    ("grid", "act_grid", "Show Grid", 11),
    ("snap", "act_snap", "Snap to Grid", 10),
    ("smart_guides", "act_smart_guides", "Smart Guides", 7),
    ("rulers", "act_rulers", "Rulers", 5),
    ("navigator", "act_navigator", "Navigator", 4),
    ("guides", "act_show_guides", "Display Guidelines", 3),
    ("crosshair", "act_crosshair", "Cross Hair Cursor", 2),
    ("show_zoom", "act_zoom_window", "Magnifier", 1),
)
#: Settings > Canvas > "Navigation bar" ('view/navbar_pos')
POSITIONS = (
    ("bottom_centre", "Bottom centre"),
    ("bottom_left", "Bottom left"),
    ("bottom_right", "Bottom right"),
    ("top_centre", "Top centre"),
    ("hidden", "Hidden"),
)
#: the zoom menu's levels (percent)
MENU_LEVELS = (10, 25, 50, 100, 200, 400, 800, 1600)
#: faded while a drag runs or the pointer is near, to Settings > Canvas > Navigation >
#: "Faded to" (ui/prefs/timing_options.NAVBAR_FADED_PCT; NEAR_PX: the default of "Fade the
#: navigation bar when the pointer is within", NAVBAR_FADE_PX)
NEAR_PX = 40
OPACITY_RANGE = (40, 100)


def overlay_opacity() -> int:
    """'ui/overlay_opacity' (percent, 100 = opaque)."""
    from ..prefs.settings import app_settings

    try:
        v = int(app_settings().value("ui/overlay_opacity", 100))
    except TypeError, ValueError:  # swallow-ok: a garbled value reads as opaque
        v = 100
    lo, hi = OPACITY_RANGE
    return max(lo, min(hi, v))


def surface_qss(name: str) -> str:
    """The canvas overlays' surface (#<name>): the theme's popup colour, 8 px corners, a 1 px
    border; its buttons with a hover fill and a light accent tint when checked."""
    from ..style.theme import COLORS

    bg = QtGui.QColor(COLORS["popup"])
    acc = QtGui.QColor(COLORS["accent"])
    a = round(overlay_opacity() * 2.55)
    return (
        "/*data*/#%(n)s { background: rgba(%(r)d, %(g)d, %(b)d, %(a)d); "
        "border: 1px solid %(border)s; border-radius: 8px; }"
        "#%(n)s QToolButton { border: none; border-radius: 4px; padding: 2px; }"
        "#%(n)s QToolButton:hover { background: %(hover)s; }"
        "#%(n)s QToolButton:checked { background: rgba(%(ar)d, %(ag)d, %(ab)d, 72); }"
        "#%(n)s QToolButton::menu-indicator { image: none; width: 0px; }"
        '#%(n)s QToolButton[kbdFocus="true"] { border: 2px solid %(accent)s; '
        "padding: 0px; }"
        "#%(n)s QLabel { color: %(text)s; }"
        % {
            "n": name,
            "r": bg.red(),
            "g": bg.green(),
            "b": bg.blue(),
            "a": a,
            "border": COLORS["border"],
            "hover": COLORS["hover"],
            "ar": acc.red(),
            "accent": COLORS["accent"],
            "ag": acc.green(),
            "ab": acc.blue(),
            "text": COLORS["text"],
        }
    )


def tip_for(action: QtGui.QAction) -> str:
    """The action's tool tip with its key: 'Zoom In (Ctrl+=)'."""
    text = action.toolTip() or action.text().replace("&", "")
    keys = action.shortcut().toString(QtGui.QKeySequence.NativeText)
    if keys and keys not in text:
        text = "%s (%s)" % (text.rstrip("."), keys)
    return text


def parse_zoom(text: str):
    """'150', '150%', '150 %', '1.5x' -> 1.5; None when it is not a zoom."""
    t = (text or "").strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(%|x)?", t)
    if not m:
        return None
    v = float(m.group(1))
    z = v if m.group(2) == "x" else v / 100.0
    return z if z > 0 else None


def zoom_text(z: float) -> str:
    pct = z * 100.0
    if abs(pct - round(pct)) < 0.05 or pct >= 100:
        return "%d %%" % round(pct)
    return ("%.1f %%" % pct).replace(".0 %", " %")


class ZoomMenu(QtWidgets.QMenu):
    """The zoom menu of a window (the NavBar's arrow; the status bar may share it): the
    levels with the one shown checked, Screen Pixels, the fits, Previous View, Saved
    Views."""

    def __init__(self, window):
        super().__init__(window)
        self._w = window
        self.setObjectName("ZoomMenu")
        self.level_actions = {}
        for pct in MENU_LEVELS:
            a = self.addAction("%d %%" % pct, lambda p=pct: self._set(p / 100.0))
            a.setCheckable(True)
            self.level_actions[pct] = a
        self.act_screen_px = self.addAction("Screen Pixels", self._screen_pixels)
        self.act_screen_px.setToolTip("One page pixel per screen pixel, the size the runtime shows")
        self.screen_px_handler = None
        self.addSeparator()
        for name in ("act_zoom_fit", "act_zoom_all", "act_zoom_sel", "act_prev_view"):
            a = getattr(window, name, None)
            if a is not None:
                self.addAction(a)
        sv = getattr(window, "saved_views_menu", None)
        if sv is not None:
            self.addMenu(sv)
        self.aboutToShow.connect(self.sync)

    def _view(self):
        return getattr(self._w, "view", None)

    def screen_ratio(self) -> float:
        v = self._view()
        try:
            return float(v.viewport().devicePixelRatioF() or 1.0) if v is not None else 1.0
        except RuntimeError:  # swallow-ok: the canvas is being destroyed
            return 1.0

    def sync_screen_px(self, ratio: float) -> None:
        self.act_screen_px.setVisible(abs(ratio - 1.0) > 1e-3)
        self.act_screen_px.setText("Screen Pixels (%d%%)" % round(100.0 / ratio))

    def sync(self) -> None:
        """Check the level shown (within 0.5 %); Screen Pixels only at a scale > 100 %."""
        v = self._view()
        z = v.zoom() * 100.0 if v is not None else 100.0
        for pct, a in self.level_actions.items():
            a.setChecked(abs(z - pct) <= 0.5)
        nb = getattr(self._w, "navbar", None)
        ratio = nb.screen_ratio() if nb is not None else self.screen_ratio()
        self.sync_screen_px(ratio)

    def _set(self, z: float) -> None:
        v = self._view()
        if v is not None:
            v.set_zoom(z)

    def _screen_pixels(self) -> None:
        nb = getattr(self._w, "navbar", None)
        if nb is not None:
            nb.zoom_screen_pixels()
        else:
            self._set(1.0 / self.screen_ratio())


def zoom_menu(window) -> ZoomMenu:
    """The window's one zoom menu (made the first time it is asked for)."""
    m = window.__dict__.get("_ctg_zoom_menu")
    if m is None:
        m = window.__dict__["_ctg_zoom_menu"] = ZoomMenu(window)
    return m


class ZoomField(QtWidgets.QLineEdit):
    """The NavBar's zoom: type a zoom and Enter; Up / Down = the zoom levels; Esc = back
    to the canvas; a click selects the text."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view
        self.setObjectName("ZoomField")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setToolTip("Zoom level: type a zoom and press Enter. Up and Down step through the levels")
        self.returnPressed.connect(self.apply)
        self.textEdited.connect(lambda _t: self.set_invalid(False))
        self._invalid = False
        self.fit_width()

    def fit_width(self) -> None:
        fm = QtGui.QFontMetrics(self.font())
        self.setFixedWidth(fm.horizontalAdvance("6400 %") + px(18))

    def set_invalid(self, on: bool) -> None:
        on = bool(on)
        if on == self._invalid:
            return
        self._invalid = on
        from ..style.theme import mark_invalid

        mark_invalid(self, "Not a zoom: type one, for example 150 %." if on else "")

    def invalid(self) -> bool:
        return self._invalid

    def show_zoom(self, z: float) -> None:
        if not self.hasFocus():
            self.setText(zoom_text(z))
            self.set_invalid(False)
        self.setAccessibleName("Zoom %d percent" % round(z * 100))

    def apply(self) -> bool:
        z = parse_zoom(self.text())
        v = self._view
        if z is None:
            self.set_invalid(True)
            return False
        self.set_invalid(False)
        v.set_zoom(z)
        # (the zoom it goes to: a glide is still at its start here, and the field keeps
        # the focus, so the glide's frames never correct the text)
        self.setText(zoom_text(v.target_zoom()))
        self.selectAll()
        return True

    #: keys the field keeps from the canvas's own shortcuts (it is a child of the canvas:
    #: the arrow keys would move the selection, minus delete it)
    KEEP_KEYS = frozenset(
        {
            QtCore.Qt.Key_Up,
            QtCore.Qt.Key_Down,
            QtCore.Qt.Key_Left,
            QtCore.Qt.Key_Right,
            QtCore.Qt.Key_Home,
            QtCore.Qt.Key_End,
            QtCore.Qt.Key_Escape,
            QtCore.Qt.Key_Return,
            QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Delete,
            QtCore.Qt.Key_Backspace,
            QtCore.Qt.Key_Minus,
            QtCore.Qt.Key_PageUp,
            QtCore.Qt.Key_PageDown,
        }
    )

    def event(self, e) -> bool:
        if e.type() == QtCore.QEvent.ShortcutOverride and (
            e.key() in self.KEEP_KEYS
            or (e.text() and e.text().isprintable() and not e.modifiers() & QtCore.Qt.ControlModifier)
        ):
            e.accept()
            return True
        return super().event(e)

    def mousePressEvent(self, e) -> None:
        had = self.hasFocus()
        super().mousePressEvent(e)
        if not had:
            QtCore.QTimer.singleShot(0, self, self.selectAll)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        k = e.key()
        if k in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            self._view.zoom_step(1 if k == QtCore.Qt.Key_Up else -1)
            self._view.finish_view_animation()
            self.setText(zoom_text(self._view.zoom()))
            self.selectAll()
            e.accept()
            return
        if k == QtCore.Qt.Key_Escape:
            self.setText(zoom_text(self._view.zoom()))
            self.set_invalid(False)
            self._view.setFocus(QtCore.Qt.ShortcutFocusReason)
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.setText(zoom_text(self._view.zoom()))


class NavButton(QtWidgets.QToolButton):
    """A NavBar button: its tool tip names the key (and the grid's says when it is too
    fine to show)."""

    def __init__(self, action, parent, extra=None):
        super().__init__(parent)
        self.setDefaultAction(action)
        self._extra = extra
        announce(self, action)
        self.setAutoRaise(True)

    def tip(self) -> str:
        a = self.defaultAction()
        text = tip_for(a) if a is not None else self.toolTip()
        if self._extra is not None:
            more = self._extra()
            if more:
                text += " " + more
        return text

    def event(self, e) -> bool:
        if e.type() == QtCore.QEvent.ToolTip:
            QtWidgets.QToolTip.showText(e.globalPos(), self.tip(), self)
            return True
        return super().event(e)


class NavBar(QtWidgets.QFrame):
    def __init__(self, view, window):
        super().__init__(view.viewport())
        self.setObjectName("NavBar")
        self.setAccessibleName("Zoom and view")
        self._view = view
        self._window = window
        self._faded = False
        self._pos = "bottom_centre"
        self._shown = True
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(0), _sp(1), _sp(0))
        lay.setSpacing(_sp(0))
        self._lay = lay
        self._buttons: dict = {}
        self._seps: list = []
        for bid, src, _label, _rank in BUTTONS:
            if bid == "|":
                sep = QtWidgets.QFrame(self)
                sep.setFrameShape(QtWidgets.QFrame.VLine)
                set_note_role(sep, "border")
                lay.addWidget(sep)
                self._seps.append(sep)
                continue
            if bid == "zoom":
                box = QtWidgets.QWidget(self)
                bl = QtWidgets.QHBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.setSpacing(0)
                self.zoom_field = ZoomField(view, box)
                bl.addWidget(self.zoom_field)
                self._zoom_btn = QtWidgets.QToolButton(box)
                self._zoom_btn.setArrowType(QtCore.Qt.DownArrow)
                self._zoom_btn.setAutoRaise(True)
                self._zoom_btn.setToolTip("Zoom levels")
                self._zoom_btn.setAccessibleName("Zoom levels")
                self._zoom_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
                self._menu = zoom_menu(window)
                self._zoom_btn.setMenu(self._menu)
                bl.addWidget(self._zoom_btn)
                lay.addWidget(box)
                self._buttons[bid] = box
                continue
            action = self._action(src)
            if action is None:
                continue
            extra = self._grid_note if bid == "grid" else None
            b = NavButton(action, self, extra)
            lay.addWidget(b)
            self._buttons[bid] = b
        self._act_screen_px = self._menu.act_screen_px
        self._more = QtWidgets.QToolButton(self)
        from ..style.glyphs_nav import icon as nav_icon

        self._more.setIcon(nav_icon("overflow"))
        self._more.setAutoRaise(True)
        self._more.setToolTip("More view commands")
        self._more.setAccessibleName("More view commands")
        self._more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._more_menu = QtWidgets.QMenu(self._more)
        self._more.setMenu(self._more_menu)
        lay.addWidget(self._more)
        self._more.hide()
        self._chosen = self._chosen_buttons()

        self._theme_changed()
        view.zoomChanged.connect(self._show_zoom)
        self._show_zoom(view.zoom())
        view.viewport().installEventFilter(self)
        self.adjustSize()
        self.set_position(self.stored_position())
        self.show()

    # ---- building -------------------------------------------------------------------
    def _action(self, src):
        w = self._window
        if isinstance(src, tuple):
            return getattr(w, "tool_actions", {}).get(src[1])
        return getattr(w, src, None)

    def _grid_note(self) -> str:
        """'(too fine to show at this zoom)' when the grid is not drawn at this zoom."""
        v = self._view
        sc = v.scene()
        fn = getattr(sc, "grid_visible_at", None)
        if callable(fn):
            try:
                if not fn(v.zoom()):
                    return "(too fine to show at this zoom)"
                return ""
            except Exception:  # noqa: BLE001 -- swallow-ok: the plain tool tip then
                return ""
        gx, gy, _ox, _oy = v.grid()
        if min(gx, gy) * v.zoom() < 4.0:
            return "(too fine to show at this zoom)"
        return ""

    @staticmethod
    def _chosen_buttons() -> set:
        """The buttons Customise… keeps on the bar (all when none were chosen)."""
        from ..prefs.settings import app_settings

        try:
            ids = [str(x) for x in (app_settings().value("ui/navbar_buttons", [], type=list) or [])]
        except Exception:  # noqa: BLE001 -- swallow-ok: a garbled list shows every button
            ids = []
        known = {b for b, _s, _l, _r in BUTTONS if b != "|"}
        chosen = {i for i in ids if i in known}
        return (chosen | {"zoom"}) if chosen else set(known)

    def set_chosen(self, ids) -> None:
        """Customise…: keep ``ids`` on the bar (stored in 'ui/navbar_buttons')."""
        from ..prefs.settings import app_settings

        known = [b for b, _s, _l, _r in BUTTONS if b != "|"]
        ids = [i for i in known if i in set(ids) | {"zoom"}]
        app_settings().setValue("ui/navbar_buttons", [] if set(ids) >= set(known) else ids)
        # (every editor window's bar: the choice is stored once for them all)
        for nb in self._bars():
            try:
                nb._chosen = nb._chosen_buttons()
                nb.reposition()
            except RuntimeError:  # swallow-ok: a window being closed
                continue

    def _bars(self) -> list:
        """The navigation bars of every open editor window, this one with them."""
        from .canvas_options import editor_windows

        bars = []
        for w in editor_windows(self._window):
            nb = getattr(w, "navbar", None)
            if isinstance(nb, NavBar) and all(nb is not b for b in bars):
                bars.append(nb)
        if all(self is not b for b in bars):
            bars.append(self)
        return bars

    def chosen(self) -> set:
        return set(self._chosen)

    def _theme_changed(self) -> None:
        """The surface and the sizes from the theme (also after a theme or text size
        change)."""
        self.setStyleSheet(surface_qss("NavBar"))
        for b in self._buttons.values():
            if isinstance(b, QtWidgets.QToolButton):
                b.setIconSize(QtCore.QSize(px(16), px(16)))
        self._more.setIconSize(QtCore.QSize(px(16), px(16)))
        if hasattr(self, "zoom_field"):
            self.zoom_field.fit_width()
        self._set_effect()
        self.reposition()

    def _set_effect(self) -> None:
        """A soft shadow; while faded, the "Faded to" opacity instead (one effect at a
        time)."""
        if self._faded:
            from ..prefs import timing_options

            eff = QtWidgets.QGraphicsOpacityEffect(self)
            eff.setOpacity(timing_options.NAVBAR_FADED_PCT / 100.0)
        else:
            from ..style.theme import current_scheme

            eff = QtWidgets.QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(16)
            eff.setOffset(0, 4)
            eff.setColor(QtGui.QColor(0, 0, 0, 64 if current_scheme() == "light" else 128))
        self.setGraphicsEffect(eff)

    # ---- the zoom ---------------------------------------------------------------------
    def _show_zoom(self, z: float) -> None:
        self.zoom_field.show_zoom(z)

    def zoom_text(self) -> str:
        return self.zoom_field.text()

    def screen_ratio(self) -> float:
        """Screen pixels per logical pixel where the canvas is (1.5 at 150 %)."""
        try:
            return float(self._view.viewport().devicePixelRatioF() or 1.0)
        except RuntimeError:
            return 1.0

    def _sync_screen_px(self) -> None:
        self._menu.sync_screen_px(self.screen_ratio())

    def zoom_screen_pixels(self) -> None:
        """Zoom so one page pixel covers one screen pixel."""
        self._set_zoom(1.0 / self.screen_ratio())

    def _set_zoom(self, factor: float) -> None:
        """A preset zoom, keeping the view centre."""
        self._view.set_zoom(factor)

    # ---- where it sits ----------------------------------------------------------------
    @staticmethod
    def stored_position() -> str:
        from ..prefs.settings import choice

        return choice("view/navbar_pos", POSITIONS, "bottom_centre")

    def position(self) -> str:
        return self._pos

    def set_position(self, pos: str) -> None:
        self._pos = pos if pos in dict(POSITIONS) else "bottom_centre"
        self.setVisible(self._shown)
        self.reposition()

    def setVisible(self, visible: bool) -> None:
        # (the window shows it when a page is open; 'Hidden' keeps it hidden)
        self._shown = bool(visible)
        super().setVisible(self._shown and self._pos != "hidden")

    def reposition(self) -> None:
        """Lay the buttons out for the canvas's width ('…' takes the less used ones) and put
        the bar where Settings say."""
        vp = self._view.viewport()
        for bid, w in self._buttons.items():
            w.setVisible(bid in self._chosen)
        self._more.hide()
        self.adjustSize()
        overflow = []
        ranked = sorted((r, bid) for bid, _s, _l, r in BUTTONS if r and bid in self._buttons)
        while self.sizeHint().width() + 80 > vp.width() and ranked:
            _r, bid = ranked.pop(0)
            w = self._buttons[bid]
            if w.isVisibleTo(self):
                w.hide()
                overflow.append(bid)
                self._more.show()
                self.adjustSize()
        self._more_menu.clear()
        for bid, src, _l, _r in BUTTONS:
            if bid in overflow:
                a = self._action(src)
                if a is not None:
                    self._more_menu.addAction(a)
        if self._seps:
            right = any(
                self._buttons[b].isVisibleTo(self)
                for b in (
                    "grid",
                    "snap",
                    "smart_guides",
                    "rulers",
                    "navigator",
                    "guides",
                    "crosshair",
                    "show_zoom",
                )
                if b in self._buttons
            )
            for s in self._seps:
                s.setVisible(right)
        self.adjustSize()
        w, h = self.width(), self.height()
        m = 10
        if self._pos == "bottom_left":
            x, y = m, vp.height() - h - m
        elif self._pos == "bottom_right":
            x, y = vp.width() - w - m - 14, vp.height() - h - m
        elif self._pos == "top_centre":
            x, y = (vp.width() - w) // 2, m
        else:
            x, y = (vp.width() - w) // 2, vp.height() - h - m
        self.move(max(0, x), max(0, y))
        self.raise_()

    def overflow_ids(self) -> list:
        return [bid for bid, w in self._buttons.items() if bid in self._chosen and not w.isVisibleTo(self)]

    # ---- fading during drags --------------------------------------------------------
    def set_faded(self, on: bool, dragging: bool = False) -> None:
        on = bool(on)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, bool(dragging))
        if on != self._faded:
            self._faded = on
            self._set_effect()

    def faded(self) -> bool:
        return self._faded

    def enterEvent(self, e) -> None:
        # over the bar it is never faded (the canvas sees no move once the pointer is on
        # one of its children, so the 'near' fade would stay)
        super().enterEvent(e)
        if not self.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents):
            self.set_faded(False)

    def eventFilter(self, obj, event) -> bool:
        if obj is not self._view.viewport():
            return False
        t = event.type()
        if t == QtCore.QEvent.Resize:
            self.reposition()
        elif t in (QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonPress):
            pos = event.position().toPoint()
            g = self.geometry()
            buttons = event.buttons()
            # (a drag is a move with a button held: a click leaves the bar as it is, its
            # effect swapped would repaint the whole canvas twice)
            dragging = buttons != QtCore.Qt.NoButton and t == QtCore.QEvent.MouseMove
            inside = g.contains(pos)
            from ..prefs import timing_options

            px = timing_options.NAVBAR_FADE_PX
            near = g.adjusted(-px, -px, px, px).contains(pos) and not inside
            self.set_faded(dragging or near, dragging)
        elif t == QtCore.QEvent.Leave:
            self.set_faded(False)
        return False

    # ---- the right-click menu ----------------------------------------------------------
    def contextMenuEvent(self, e) -> None:
        menu = QtWidgets.QMenu(self)
        menu.addAction("Customise…", self.customise)
        place = menu.addMenu("Position")
        for value, label in POSITIONS:
            a = place.addAction(label, lambda v=value: self._store_position(v))
            a.setCheckable(True)
            a.setChecked(value == self._pos)
        menu.exec(e.globalPos())

    def _store_position(self, pos: str) -> None:
        """The right-click menu's Position (Settings > Canvas > "Navigation bar"): stored,
        and every editor window's bar goes there."""
        from ..prefs.settings import app_settings

        app_settings().setValue("view/navbar_pos", pos)
        for nb in self._bars():
            try:
                nb.set_position(pos)
            except RuntimeError:  # swallow-ok: a window being closed
                continue

    def customise(self) -> None:
        dlg = NavBarCustomiseDialog(self, self._window)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.set_chosen(dlg.chosen())


class NavBarCustomiseDialog(QtWidgets.QDialog):
    """Customise…: which buttons the navigation bar shows."""

    RESIZE = "none"

    def __init__(self, bar: NavBar, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customise the Navigation Bar")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Buttons on the navigation bar:"))
        self._boxes = {}
        shown = bar.chosen()
        for bid, _src, label, _rank in BUTTONS:
            if bid in ("|", "zoom"):
                continue
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(bid in shown)
            lay.addWidget(cb)
            self._boxes[bid] = cb
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        reset = box.addButton("Show All", QtWidgets.QDialogButtonBox.ResetRole)
        reset.clicked.connect(lambda: [cb.setChecked(True) for cb in self._boxes.values()])
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def chosen(self) -> list:
        return [bid for bid, cb in self._boxes.items() if cb.isChecked()] + ["zoom"]
