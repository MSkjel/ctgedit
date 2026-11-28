"""Canvas navigation UI."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxShapes"))
    w.resize(1300, 900)
    w.show()
    _pump()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


# ------------------------------------------------------------------ the zoom field
def test_parse_zoom():
    from ctgedit.ui.editor.navbar import parse_zoom, zoom_text

    assert parse_zoom("150") == 1.5 and parse_zoom("150%") == 1.5
    assert parse_zoom(" 150 % ") == 1.5 and parse_zoom("2x") == 2.0
    assert parse_zoom("66,7") == pytest.approx(0.667)
    for bad in ("", "abc", "0", "-5", "1e3"):
        assert parse_zoom(bad) is None
    assert zoom_text(1.0) == "100 %" and zoom_text(10.0) == "1000 %"
    assert zoom_text(2 / 3) == "66.7 %" and zoom_text(1 / 3) == "33.3 %"


def test_typing_150_and_enter_zooms_to_150(win):
    f = win.navbar.zoom_field
    f.setText("150")
    f.apply()
    assert win.view.zoom() == pytest.approx(1.5)
    assert f.text() == "150 %"
    f.setText("nonsense")
    assert not f.apply()
    assert f.invalid() and "border" in f.styleSheet()
    assert win.view.zoom() == pytest.approx(1.5)
    QtTest.QTest.keyClicks(f, "2")
    assert not f.invalid()


def test_up_down_step_the_levels_and_esc_goes_back(win):
    f = win.navbar.zoom_field
    win.view.set_zoom(0.55)
    QtTest.QTest.keyClick(f, QtCore.Qt.Key_Up)
    assert win.view.zoom() == pytest.approx(2 / 3)
    QtTest.QTest.keyClick(f, QtCore.Qt.Key_Down)
    assert win.view.zoom() == pytest.approx(0.5)
    f.setText("garbage")
    QtTest.QTest.keyClick(f, QtCore.Qt.Key_Escape)
    assert f.text() == "50 %"


def test_at_1000_the_field_shows_the_whole_number(win):
    win.view.set_zoom(10.0)
    f = win.navbar.zoom_field
    assert f.text() == "1000 %"
    fm = QtGui.QFontMetrics(f.font())
    assert fm.horizontalAdvance("6400 %") < f.width()
    w0 = f.width()
    win.view.set_zoom(0.02)
    assert f.width() == w0  # (it never changes size)


def test_the_zoom_menu_checks_the_level_shown(win):
    nav = win.navbar
    menu = nav._zoom_btn.menu()
    assert menu is not None and menu is win.navbar._menu
    texts = [a.text() for a in menu.actions() if a.text()]
    for t in ("10 %", "25 %", "50 %", "100 %", "200 %", "400 %", "800 %", "1600 %"):
        assert t in texts
    assert win.act_zoom_fit in menu.actions() and win.act_zoom_all in menu.actions()
    assert win.act_zoom_sel in menu.actions() and win.act_prev_view in menu.actions()
    assert win.saved_views_menu.menuAction() in menu.actions()
    assert win.act_zoom_100 not in menu.actions()
    win.view.set_zoom(2.0)
    menu.sync()
    checked = [a.text() for a in menu.actions() if a.isCheckable() and a.isChecked()]
    assert checked == ["200 %"]
    win.view.set_zoom(2.003)
    menu.sync()
    assert [a.text() for a in menu.actions() if a.isChecked()] == ["200 %"]
    win.view.set_zoom(1.3)
    menu.sync()
    assert not [a for a in menu.actions() if a.isChecked()]
    menu.level_actions[400].trigger()
    assert win.view.zoom() == pytest.approx(4.0)
    # the arrow does not sit over the number
    assert nav._zoom_btn.geometry().left() >= nav.zoom_field.geometry().right()


# ------------------------------------------------------------------ the buttons
def test_the_bar_has_the_view_buttons_with_their_keys(win):
    nav = win.navbar
    from ctgedit.ui.editor.navbar import NavButton

    acts = [b.defaultAction() for b in nav.findChildren(NavButton)]
    for a in (
        win.tool_actions["pan"],
        win.tool_actions["zoom"],
        win.act_zoom_out,
        win.act_zoom_in,
        win.act_zoom_fit,
        win.act_zoom_all,
        win.act_zoom_sel,
        win.act_grid,
        win.act_snap,
        win.act_smart_guides,
        win.act_rulers,
        win.act_navigator,
        win.act_show_guides,
        win.act_crosshair,
        win.act_zoom_window,
    ):
        assert a in acts, a.text()
    assert acts[0] is win.tool_actions["pan"]  # the Hand first
    b = next(b for b in nav.findChildren(NavButton) if b.defaultAction() is win.act_zoom_in)
    assert b.tip() == "Zoom In (Ctrl+=)"
    grid = next(b for b in nav.findChildren(NavButton) if b.defaultAction() is win.act_grid)
    # (the major lines of the 8 px grid still show at 25 %: the adaptive grid; at 5 %
    # they are 3 px apart too)
    win.view.set_zoom(0.05)
    assert "too fine to show" in grid.tip()
    win.view.set_zoom(2.0)
    assert "too fine" not in grid.tip()
    # every button is a menu command (the menu bar stays the superset)
    from ctgedit.ui.dialog.palette import harvest_actions

    in_menus = {id(a) for a, _c, _t in harvest_actions(win.menuBar())}
    for a in acts:
        assert id(a) in in_menus, a.text()


def test_a_narrow_canvas_puts_the_less_used_buttons_in_the_overflow(win):
    nav = win.navbar
    win.resize(1300, 900)
    _pump()
    nav.reposition()
    wide = nav.overflow_ids()
    win.resize(1000, 700)
    _pump()
    nav.reposition()
    narrow = nav.overflow_ids()
    assert len(narrow) > len(wide)
    assert "show_zoom" in narrow  # the least used goes first
    for keep in ("pan", "zoom_out", "zoom", "zoom_in", "fit_page"):
        assert keep not in narrow
    assert nav._more.isVisibleTo(nav) and nav._more_menu.actions()
    vp = win.view.viewport()
    assert (
        nav.x() >= 0
        and nav.width() + 80 <= vp.width()
        or not [
            bid
            for bid, _s, _l, r in __import__("ctgedit.ui.editor.navbar", fromlist=["BUTTONS"]).BUTTONS
            if r and bid not in narrow and bid in nav._buttons
        ]
    )
    # the field keeps the arrow keys from the canvas's shortcuts
    ev = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_Up, QtCore.Qt.NoModifier)
    nav.zoom_field.event(ev)
    assert ev.isAccepted()


def test_customise_and_position(win):
    nav = win.navbar
    nav.set_chosen(["pan", "zoom_out", "zoom_in", "fit_page"])
    from ctgedit.ui.prefs.settings import app_settings

    assert sorted(app_settings().value("ui/navbar_buttons", [], type=list)) == sorted(
        ["pan", "zoom_out", "zoom_in", "fit_page", "zoom"]
    )
    assert not nav._buttons["grid"].isVisibleTo(nav)
    from ctgedit.ui.editor.navbar import NavBarCustomiseDialog

    dlg = NavBarCustomiseDialog(nav, win)
    assert not dlg._boxes["grid"].isChecked() and dlg._boxes["pan"].isChecked()
    dlg.close()
    nav.set_chosen([b for b in nav._buttons])
    assert app_settings().value("ui/navbar_buttons", [], type=list) in ([], None)
    vp = win.view.viewport()
    for pos in ("top_centre", "bottom_left", "bottom_right", "bottom_centre"):
        nav._store_position(pos)
        g = nav.geometry()
        assert vp.rect().contains(g), pos
        if pos == "top_centre":
            assert g.top() < vp.height() // 2
        if pos == "bottom_left":
            assert g.left() < vp.width() // 3
    nav._store_position("hidden")
    assert not nav.isVisible()
    nav.setVisible(True)  # (the window shows it with a page)
    assert not nav.isVisible()
    nav._store_position("bottom_centre")
    assert nav.isVisible()


def test_it_fades_near_the_pointer_and_during_a_drag(win):
    nav = win.navbar
    vp = win.view.viewport()
    g = nav.geometry()

    def move(p, buttons=QtCore.Qt.NoButton):
        e = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(p),
            QtCore.QPointF(vp.mapToGlobal(p)),
            QtCore.Qt.NoButton,
            buttons,
            QtCore.Qt.NoModifier,
        )
        nav.eventFilter(vp, e)

    move(QtCore.QPoint(g.center().x(), g.top() - 20))
    assert nav.faded()
    assert isinstance(nav.graphicsEffect(), QtWidgets.QGraphicsOpacityEffect)
    move(g.center())
    assert not nav.faded()
    assert isinstance(nav.graphicsEffect(), QtWidgets.QGraphicsDropShadowEffect)
    move(QtCore.QPoint(20, 20), QtCore.Qt.LeftButton)
    assert nav.faded() and nav.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    move(QtCore.QPoint(20, 20))
    assert not nav.faded() and not nav.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)


def test_the_surface_follows_the_theme_and_the_opacity_setting():
    from ctgedit.ui.editor.navbar import surface_qss
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.style.theme import COLORS

    c = QtGui.QColor(COLORS["popup"])
    qss = surface_qss("NavBar")
    assert "rgba(%d, %d, %d, 255)" % (c.red(), c.green(), c.blue()) in qss
    assert "border-radius: 8px" in qss and COLORS["border"] in qss
    app_settings().setValue("ui/overlay_opacity", 60)
    assert ", 153)" in surface_qss("NavBar")


# ------------------------------------------------------------------ menus and keys
def test_the_view_menu_has_the_navigation_commands(win):
    view_menu = next(m for m in win.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&View")
    acts = view_menu.actions()
    for a in (
        win.act_prev_view,
        win.act_next_view,
        win.act_rulers,
        win.act_navigator,
        win.tool_actions["zoom"],
        win.tool_actions["pan"],
    ):
        assert a in acts, a.text()
    assert win.saved_views_menu.menuAction() in acts
    assert win.act_rulers.shortcut().toString() == "Ctrl+R"
    assert win.act_navigator.shortcut().toString() == "Ctrl+Shift+N"
    assert win.tool_actions["zoom"].shortcut().toString() == "Z"
    assert win.tool_actions["pan"].shortcut().toString() == "H"
    draw = next(m for m in win.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "Draw")
    assert win.tool_actions["zoom"] not in draw.actions()
    panels = next(m for m in view_menu.findChildren(QtWidgets.QMenu) if m.title() == "Panels")
    assert win.dock_navigator.toggleViewAction() in panels.actions()
    guides = next(m for m in view_menu.findChildren(QtWidgets.QMenu) if m.title() == "Guidelines")
    assert win.act_clear_guides in guides.actions()


def test_clear_all_guidelines(win):
    win.view.set_guidelines([100], [200, 300])
    win._save_guidelines()
    win.act_clear_guides.trigger()
    assert win.view.guidelines() == ([], [])
    assert win._load_guidelines(win.doc) == ([], [])


def test_the_navigator_panel_dock_hosts_the_same_map(win):
    from ctgedit.ui.editor.navigator import NavigatorMap

    assert isinstance(win.dock_navigator.widget(), NavigatorMap)
    assert isinstance(win.navigator.map, NavigatorMap)
    assert win.dock_navigator.isHidden()


# ------------------------------------------------------------------ Settings > Canvas
def test_settings_canvas_navigation_round_trip(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import app_settings, option

    dlg = SettingsDialog(win)
    try:
        nav = dlg._nav
        nav.wheel_step.setValue(25)
        nav.touchpad.setChecked(True)  # (off by default)
        nav.zoom_steps.setCurrentIndex(nav.zoom_steps.findData("factor"))
        nav.middle.setCurrentIndex(nav.middle.findData("fit_page"))
        nav.scrollbars.setCurrentIndex(nav.scrollbars.findData("always"))
        nav.navbar_pos.setCurrentIndex(nav.navbar_pos.findData("top_centre"))
        nav.cross_style.setCurrentIndex(nav.cross_style.findData("short"))
        nav.autoscroll.setChecked(False)
        dlg._apply(accept=False)
        st = app_settings()
        assert int(st.value("opt/wheel_zoom_step")) == 25 and option("touchpad_pans")
        v = win.view
        assert v.WHEEL_ZOOM_STEP == 25 and v.TOUCHPAD_PANS
        assert v.ZOOM_STEPS == "factor" and v.MIDDLE_DOUBLE_CLICK == "fit_page"
        assert v.CROSSHAIR_STYLE == "short" and not v.DRAG_AUTOSCROLL
        assert v.overlay_bars.mode == "always"
        assert win.navbar.position() == "top_centre"
        dlg.restore_defaults()
        assert nav.wheel_step.value() == 15 and not nav.touchpad.isChecked()
        assert nav.scrollbars.currentData() == "auto"
        dlg._apply(accept=False)
        assert v.WHEEL_ZOOM_STEP == 15 and v.overlay_bars.mode == "auto"
        assert not v.TOUCHPAD_PANS and not option("touchpad_pans")
        assert win.navbar.position() == "bottom_centre"
    finally:
        dlg.close()


def test_canvas_keys_list_the_new_keys():
    from ctgedit.ui.dialog.help_window import CANVAS_KEYS

    keys = " | ".join(k for k, _w in CANVAS_KEYS)
    for k in (
        "Esc while dragging",
        "Hold Z",
        "Middle mouse button double-click",
        "Mouse back / forward buttons",
        "Drag from a ruler",
    ):
        assert k in keys
