"""The title bar every dock panel uses."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

#: the drop area is shown while dragging, panels can sit side by side or as tabs
DOCK_OPTIONS = (
    QtWidgets.QMainWindow.AnimatedDocks
    | QtWidgets.QMainWindow.AllowNestedDocks
    | QtWidgets.QMainWindow.AllowTabbedDocks
)
#: Settings > Appearance > Panels > Panel tabs
TAB_POSITIONS = (("top", "Above the panels"), ("bottom", "Below the panels"))
#: the title bar's margins (left, top, right, bottom) and its buttons' icon size (px) when
#: compact (the default) and when not
TITLE_COMPACT = ((8, 2, 2, 2), 16)
TITLE_ROOMY = ((10, 6, 6, 6), 20)


def glyph(kind: str, colour: QtGui.QColor, size: int = 16, dpr: float = 1.0) -> QtGui.QPixmap:
    """A small line drawing: 'dock' (a window with a panel at its bottom), 'float' (two
    windows), 'max' (a window), 'restore' (a window behind another) or 'close' (a cross)."""
    pm = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, kind == "close")
    pen = QtGui.QPen(colour, 1.2 if kind == "close" else 1.0)
    p.setPen(pen)
    if kind == "close":
        p.drawLine(QtCore.QPointF(4.5, 4.5), QtCore.QPointF(11.5, 11.5))
        p.drawLine(QtCore.QPointF(11.5, 4.5), QtCore.QPointF(4.5, 11.5))
    elif kind == "max":
        p.drawRect(QtCore.QRectF(3.5, 3.5, 9, 9))
        p.drawLine(QtCore.QPointF(3.5, 4.5), QtCore.QPointF(12.5, 4.5))
    elif kind == "restore":
        p.drawLine(QtCore.QPointF(5.5, 3.5), QtCore.QPointF(12.5, 3.5))
        p.drawLine(QtCore.QPointF(12.5, 3.5), QtCore.QPointF(12.5, 10.5))
        p.drawRect(QtCore.QRectF(3.5, 5.5, 7, 7))
    elif kind == "float":
        p.drawRect(QtCore.QRectF(5.5, 3.5, 7, 6))
        p.fillRect(QtCore.QRectF(3, 6, 8, 7), QtCore.Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        p.fillRect(QtCore.QRectF(3, 6, 8, 7), QtCore.Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        p.drawRect(QtCore.QRectF(3.5, 6.5, 7, 6))
    else:  # dock
        p.drawRect(QtCore.QRectF(3.5, 3.5, 9, 9))
        p.fillRect(QtCore.QRectF(4, 9, 8, 3), colour)
    p.end()
    return pm


class DockTitleBar(QtWidgets.QWidget):
    """A panel's title: its name, Dock / Float and Close. Mouse presses, drags and
    double-clicks on the title go on to the QDockWidget, which moves, docks and floats it."""

    def __init__(self, dock: QtWidgets.QDockWidget):
        super().__init__(dock)
        self.setObjectName("DockTitleBar")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._dock = dock
        self.compact = True
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(*TITLE_COMPACT[0])
        lay.setSpacing(1)
        self.label = QtWidgets.QLabel(dock.windowTitle())
        self.label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.label, 1)
        self.float_btn = self._button("Float")
        self.float_btn.clicked.connect(self._toggle)
        lay.addWidget(self.float_btn)
        # a floating panel's Maximize / Restore: the geometry it had before, and the
        # geometry maximizing gave it (still that = maximized)
        self._normal_rect = None
        self._max_rect = None
        self.max_btn = self._button("Maximize")
        self.max_btn.clicked.connect(self.toggle_maximized)
        lay.addWidget(self.max_btn)
        self.close_btn = self._button("Close")
        self.close_btn.clicked.connect(dock.close)
        lay.addWidget(self.close_btn)
        dock.windowTitleChanged.connect(lambda _t: self._sync_label())
        dock.topLevelChanged.connect(self._top_level_changed)
        # (a panel tabbed with others shows its name on its tab: its title bar keeps only
        # the buttons, so the name is not shown twice)
        dock.dockLocationChanged.connect(lambda _a: self._sync_label())
        dock.visibilityChanged.connect(lambda _v: self._sync_label())
        dock.featuresChanged.connect(lambda _f: self.sync())
        dock.installEventFilter(self)
        self.sync()

    def _button(self, name: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setAutoRaise(True)
        b.setAccessibleName(name)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        b.setIconSize(QtCore.QSize(16, 16))
        return b

    def set_compact(self, compact: bool) -> None:
        """Settings > Appearance > Panels > Compact panel titles: the margins and the
        buttons' size."""
        compact = bool(compact)
        if compact == self.compact:
            return
        self.compact = compact
        margins, icon = TITLE_COMPACT if compact else TITLE_ROOMY
        self.layout().setContentsMargins(*margins)
        for b in (self.float_btn, self.max_btn, self.close_btn):
            b.setIconSize(QtCore.QSize(icon, icon))
        self.updateGeometry()

    def _toggle(self) -> None:
        self._dock.setFloating(not self._dock.isFloating())

    def tabbed(self) -> bool:
        """The panel is docked in a tab group: its tab shows its name."""
        d = self._dock
        win = d.parentWidget()
        if d.isFloating() or not isinstance(win, QtWidgets.QMainWindow):
            return False
        try:
            return bool(win.tabifiedDockWidgets(d))
        except RuntimeError:  # swallow-ok: the window is being closed
            return False

    def _sync_label(self) -> None:
        self.label.setText("" if self.tabbed() else self._dock.windowTitle())

    def _top_level_changed(self, floating: bool) -> None:
        if not floating:
            self._normal_rect = self._max_rect = None
        self.sync()

    def is_maximized(self) -> bool:
        """The floating panel fills the work area Maximize gave it (not moved or resized
        since)."""
        d = self._dock
        return bool(d.isFloating() and self._max_rect is not None and d.geometry() == self._max_rect)

    def toggle_maximized(self) -> None:
        """Maximize a floating panel to its screen's work area, or give it back the size and
        place it had."""
        d = self._dock
        if not d.isFloating():
            return
        if self.is_maximized():
            rect, self._normal_rect, self._max_rect = self._normal_rect, None, None
            if rect is not None:
                d.setGeometry(rect)
        else:
            scr = d.screen() or QtWidgets.QApplication.primaryScreen()
            if scr is None:
                return
            self._normal_rect = QtCore.QRect(d.geometry())
            d.setGeometry(scr.availableGeometry())
            self._max_rect = QtCore.QRect(d.geometry())
        self.sync()

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if obj is self._dock and ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move):
            self._sync_max()
        return super().eventFilter(obj, ev)

    def _sync_max(self) -> None:
        d = self._dock
        self.max_btn.setVisible(d.isFloating())
        on = self.is_maximized()
        colour = self.palette().color(QtGui.QPalette.WindowText)
        dpr = self.devicePixelRatioF() or 1.0
        self.max_btn.setIcon(QtGui.QIcon(glyph("restore" if on else "max", colour, dpr=dpr)))
        self.max_btn.setToolTip("Give the panel its size again" if on else "Make the panel fill the screen")
        self.max_btn.setAccessibleName("Restore" if on else "Maximize")

    def sync(self) -> None:
        """The buttons for the panel's state and features."""
        d = self._dock
        f = d.features()
        floating = d.isFloating()
        colour = self.palette().color(QtGui.QPalette.WindowText)
        dpr = self.devicePixelRatioF() or 1.0
        self.float_btn.setVisible(bool(f & QtWidgets.QDockWidget.DockWidgetFloatable))
        self.float_btn.setIcon(QtGui.QIcon(glyph("dock" if floating else "float", colour, dpr=dpr)))
        self.float_btn.setToolTip(
            "Dock the panel again (or double-click its title)"
            if floating
            else "Make the panel a window of its own (or drag its title)"
        )
        self.float_btn.setAccessibleName("Dock" if floating else "Float")
        self.close_btn.setVisible(bool(f & QtWidgets.QDockWidget.DockWidgetClosable))
        self.close_btn.setIcon(QtGui.QIcon(glyph("close", colour, dpr=dpr)))
        self.close_btn.setToolTip("Close the panel")
        self._sync_max()
        self._sync_label()

    def changeEvent(self, ev):  # noqa: N802 -- Qt naming
        if ev.type() in (QtCore.QEvent.PaletteChange, QtCore.QEvent.StyleChange):
            self.sync()  # (a theme switch: the glyphs in the new colour)
        super().changeEvent(ev)

    # (no mouse handlers: an ignored press / move / double-click goes to the dock)


def install(dock: QtWidgets.QDockWidget) -> DockTitleBar:
    """Give ``dock`` the shared title bar (once)."""
    bar = dock.titleBarWidget()
    if isinstance(bar, DockTitleBar):
        return bar
    bar = DockTitleBar(dock)
    dock.setTitleBarWidget(bar)
    return bar


def set_dock_options(window: QtWidgets.QMainWindow) -> None:
    window.setDockOptions(DOCK_OPTIONS)


def install_all(window: QtWidgets.QMainWindow) -> None:
    """The dock options of ``window``, the title bar on each of its own docks and the
    Settings > Appearance > Panels choices."""
    set_dock_options(window)
    for d in window.findChildren(QtWidgets.QDockWidget, options=QtCore.Qt.FindDirectChildrenOnly):
        install(d)
    apply_settings(window)
    # (the panels are tabbed together after their bars are made: their names once laid out)
    window.tabifiedDockWidgetActivated.connect(lambda _d: refresh_titles(window))
    QtCore.QTimer.singleShot(0, window, lambda: refresh_titles(window))


def refresh_titles(window: QtWidgets.QMainWindow) -> None:
    """Each panel's title bar shows its name only when no tab shows it."""
    for d in window.findChildren(QtWidgets.QDockWidget, options=QtCore.Qt.FindDirectChildrenOnly):
        bar = d.titleBarWidget()
        if isinstance(bar, DockTitleBar):
            bar._sync_label()


def tab_position() -> str:
    """'top' or 'bottom' (ui/dock_tab_position; anything else is top)."""
    from ..prefs.settings import choice

    return choice("ui/dock_tab_position", TAB_POSITIONS, "top")


def compact_titles() -> bool:
    """ui/compact_dock_titles (on when unset or garbled)."""
    from ..prefs.settings import app_settings, stored_bool

    try:
        return stored_bool(app_settings().value("ui/compact_dock_titles", True), True)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return True


def apply_settings(window: QtWidgets.QMainWindow) -> None:
    """Settings > Appearance > Panels on ``window``: the tabs of panels that share a side
    above or below them, and compact or roomy title bars on its own docks."""
    south = tab_position() == "bottom"
    pos = QtWidgets.QTabWidget.South if south else QtWidgets.QTabWidget.North
    if window.tabPosition(QtCore.Qt.LeftDockWidgetArea) != pos:
        window.setTabPosition(QtCore.Qt.AllDockWidgetAreas, pos)
    compact = compact_titles()
    for d in window.findChildren(QtWidgets.QDockWidget, options=QtCore.Qt.FindDirectChildrenOnly):
        bar = d.titleBarWidget()
        if isinstance(bar, DockTitleBar):
            bar.set_compact(compact)


def _apply_everywhere(window) -> None:
    """The Settings window changed a Panels choice: ``window`` (an editor window) and the
    Simulate window, whose panels have the same title bars."""
    apply_settings(window)
    try:
        from ..simulate.window import simulate_window

        sw = simulate_window(create=False)
    except Exception:  # noqa: BLE001 -- swallow-ok: no Simulate window then
        sw = None
    if sw is not None:
        apply_settings(sw)


def _register_hooks() -> None:
    from ..prefs import settings_pages as SP

    for key in ("ui/dock_tab_position", "ui/compact_dock_titles"):
        if _apply_everywhere not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, _apply_everywhere)


_register_hooks()
