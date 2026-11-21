"""The same view rules in every window."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

M = QtCore.Qt
SMALL = "FxShapes"


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _setting(key, value):
    """What the Settings window does after a change: the key stored, its apply hooks run on
    every editor window."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.prefs.settings_pages import run_apply_hooks

    app_settings().setValue(key, value)
    run_apply_hooks([key])
    _pump(1)


def _wheel_event(widget, dx, dy, mods=M.NoModifier, at=None):
    pos = QtCore.QPointF(at) if at is not None else QtCore.QPointF(widget.rect().center())
    return QtGui.QWheelEvent(
        pos,
        QtCore.QPointF(widget.mapToGlobal(pos.toPoint())),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(dx, dy),
        M.NoButton,
        mods,
        M.NoScrollPhase,
        False,
    )


def _key(widget, key, mods=M.NoModifier, text=""):
    """A key press to ``widget``'s own handler (the shortcut map of an active editor window
    would take Ctrl+- and the like first in the offscreen tests)."""
    widget.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text))
    _pump(1)


@pytest.fixture(autouse=True)
def _no_animation(monkeypatch):
    monkeypatch.setenv("CTG_NO_ANIMATION", "1")
    yield
    from ctgedit.ui.editor import canvas_look as C_look, viewnav

    C_look.set_pointer_options()
    viewnav.set_fit_margin(None)


def _page(w=400, h=300, name="consistency"):
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name=name)
    p.appearance.width, p.appearance.height = w, h
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0x00FF00, 0x00FF00
    o.raw["_type_code"] = 0x0F
    # a button (it takes input) at the bottom right
    b = GraphicObject(
        kind=ObjectKind.BUTTON, an=12, geometry=Geometry(x=w - 100, y=h - 60, extent_x=80, extent_y=40)
    )
    b.raw["_type_code"] = 0x1001
    p.objects = [o, b]
    return p


_KEEP = []


def _scene(w=6000, h=6000):
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(_page(w, h))
    _KEEP.append(sc)
    return sc


@pytest.fixture()
def sim():
    """A Simulate window with one tab on a demo simulation: (window, its page)."""
    _app()
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    sp = win.open_page("", page=_page(), sim=FakeSimulation(), run=False)
    win.resize(900, 700)
    win.show()
    QtTest.QTest.qWait(80)
    yield win, sp
    win.close()
    win.deleteLater()
    _pump()


@pytest.fixture()
def win(tmp_path, fx_page):
    """The editor window on a copy of FxShapes."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    src = fx_page(SMALL)
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1300, 900)
    w.show()
    _pump()
    w.view.fit()
    _pump()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


# ------------------------------------------------------------ 1: the Simulate zoom commands
def test_the_simulate_window_has_the_editors_zoom_commands_on_its_keys(sim):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.editor import viewnav
    from ctgedit.ui.simulate.canvas import SimCanvas

    win, sp = sim
    cv = sp.canvas
    acts = win.sim_actions()
    for cid in ("sim.zoom_fit", "sim.zoom_100", "sim.zoom_in", "sim.zoom_out"):
        assert cid in acts and acts[cid] in win.clock.page_bar.actions()
        assert cid in keymap.SIM_RESERVED
    keys = {cid: ks for cid, (ks, _s) in keymap.sim_resolve()[0].items()}
    assert keys["sim.zoom_fit"] == ["Ctrl+0"] and keys["sim.zoom_100"] == ["Ctrl+1"]
    assert keys["sim.zoom_in"] == ["Ctrl+=", "Ctrl++"] and keys["sim.zoom_out"] == ["Ctrl+-"]
    assert keys["sim.inputs"] == ["Alt+1"] and keys["sim.outputs"] == ["Alt+2"]
    # the keys stay the window's while the page has the keyboard
    assert {"Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl+-"} <= set(SimCanvas.RESERVED_KEYS)
    fitted = cv.zoom()
    assert fitted == pytest.approx(cv.fit_zoom())
    cv.wheelEvent(_wheel_event(cv.viewport(), 0, 240, M.ControlModifier))
    assert cv.zoom() > fitted and not cv._auto_fit
    acts["sim.zoom_fit"].trigger()
    _pump()
    assert cv.zoom() == pytest.approx(cv.fit_zoom()) and cv._auto_fit
    acts["sim.zoom_100"].trigger()
    _pump()
    assert cv.zoom() == pytest.approx(1.0)
    acts["sim.zoom_in"].trigger()
    _pump()
    assert cv.zoom() == pytest.approx(viewnav.next_zoom_level(1.0, 1))
    acts["sim.zoom_out"].trigger()
    acts["sim.zoom_out"].trigger()
    _pump()
    assert cv.zoom() == pytest.approx(viewnav.next_zoom_level(1.0, -1))
    # Zoom by 25 % as the editor's setting says
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/zoom_steps", "factor")
    try:
        cv.reset_zoom()
        acts["sim.zoom_in"].trigger()
        assert cv.zoom() == pytest.approx(1.25)
    finally:
        app_settings().remove("opt/zoom_steps")


# ------------------------------------------------------------ 2, 12, 17: limits, fit, slack
def test_the_simulate_zoom_limits_and_fit_rule_are_the_editors():
    _app()
    from ctgedit.ui.editor import viewnav
    from ctgedit.ui.editor.canvas import CanvasView
    from ctgedit.ui.simulate.canvas import SimCanvas

    ed = CanvasView()
    ed.setScene(_scene())
    ed.apply_nav_options()
    sim = SimCanvas(_scene())
    for v in (ed, sim):
        v.resize(500, 400)
        v.show()
    _pump()
    try:
        assert (
            SimCanvas.__module__
            and CanvasView.ZOOM_MIN == viewnav.ZOOM_MIN
            and CanvasView.ZOOM_MAX == viewnav.ZOOM_MAX
        )
        sim.set_zoom(1000.0)
        assert sim.zoom() == viewnav.ZOOM_MAX == 64.0
        sim.set_zoom(0.0001)
        assert sim.zoom() == viewnav.ZOOM_MIN == 0.02
        # one fit rule: the page plus the margin in the viewport, in both
        ed.fit()
        sim.fit_page()
        _pump()
        assert sim.zoom() == pytest.approx(ed.zoom(), rel=1e-3)
        assert sim.zoom() == pytest.approx(
            viewnav.fit_zoom(QtCore.QRectF(0, 0, 6000, 6000), sim.maximumViewportSize())
        )
        from ctgedit.ui.editor import view_consistency as VC
        from ctgedit.ui.prefs.settings import app_settings

        app_settings().setValue("view/fit_margin", 0)
        VC.apply_consistency_settings()
        assert viewnav.fit_margin() == 0
        ed.fit()
        sim.fit_page()
        _pump()
        assert sim.zoom() == pytest.approx(ed.zoom(), rel=1e-3)
        size = sim.maximumViewportSize()
        assert sim.zoom() == pytest.approx(
            min((size.width() - 4) / 6000, (size.height() - 4) / 6000), rel=1e-3
        )
        app_settings().remove("view/fit_margin")
        VC.apply_consistency_settings()
        assert viewnav.fit_margin() == 40
        # the slack to pan: a page corner comes to the middle at a high zoom, as in the editor
        sim.set_zoom(4.0)
        sim.centerOn(QtCore.QPointF(0, 0))
        c = sim.mapToScene(sim.viewport().rect().center())
        assert abs(c.x()) < 3 and abs(c.y()) < 3, (c.x(), c.y())
    finally:
        for v in (ed, sim):
            v.close()


def test_fit_page_margin_is_a_setting_applied_live(win):
    from ctgedit.ui.editor import viewnav

    v = win.view
    v.fit()
    _pump()
    z40 = v.zoom()
    assert viewnav.fit_margin() == 40
    _setting("view/fit_margin", 0)
    _pump()
    assert viewnav.fit_margin() == 0
    assert v._auto_fit and v.zoom() > z40  # (the fitted view followed at once)
    _setting("view/fit_margin", 200)
    _pump()
    assert v.zoom() < z40
    _setting("view/fit_margin", 40)
    _pump()
    assert v.zoom() == pytest.approx(z40, rel=1e-3)
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    rows = {r.key: r for r in SettingsDialog.ROWS}
    assert rows["view/fit_margin"].span == (0, 400)
    for key in (
        "opt/corner_double_click",
        "opt/bare_zoom_keys",
        "help/zoom_reset_key",
        "opt/card_wheel_step",
    ):
        assert key in rows


def test_a_simulate_tabs_first_view_keeps_the_editors_opening_zoom_cap():
    """A small page opens in a Simulate tab at most at Settings > Canvas's 'Largest zoom
    when a page opens' (as in the editor); the Fit Page command then fits it fully."""
    _app()
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    app_settings().setValue("view/open_zoom_max", "100")
    win = SimulateWindow()
    try:
        sp = win.open_page("", page=_page(120, 80, "tiny"), sim=FakeSimulation(), run=False)
        win.resize(1000, 800)
        win.show()
        QtTest.QTest.qWait(120)
        cv = sp.canvas
        assert cv.zoom() == pytest.approx(1.0) and cv._auto_fit and cv.fit_zoom() > 1.0
        win.resize(1100, 820)  # (a refit keeps the cap)
        QtTest.QTest.qWait(200)
        assert cv.zoom() == pytest.approx(1.0)
        win.sim_actions()["sim.zoom_fit"].trigger()
        _pump()
        assert cv.zoom() == pytest.approx(cv.fit_zoom()) and cv.zoom() > 1.0
    finally:
        app_settings().remove("view/open_zoom_max")
        win.close()
        win.deleteLater()
        _pump()


# ------------------------------------------------------------ 3: the navigator's wheel
def test_the_navigator_takes_the_wheel_by_the_canvas_rules(win):
    from ctgedit.ui.prefs.settings import app_settings

    v = win.view
    nav = win.navigator.map
    v.set_zoom(2.0)
    _pump()
    app_settings().setValue("opt/wheel_scrolls", True)
    try:
        v.apply_nav_options()
        vs = v.verticalScrollBar().value()
        nav.wheelEvent(_wheel_event(nav, 0, -120))
        assert v.zoom() == pytest.approx(2.0)  # (the wheel scrolls now)
        assert v.verticalScrollBar().value() > vs
        hs = v.horizontalScrollBar().value()
        nav.wheelEvent(_wheel_event(nav, 0, -120, M.ShiftModifier))
        assert v.horizontalScrollBar().value() > hs and v.zoom() == pytest.approx(2.0)
        nav.wheelEvent(_wheel_event(nav, 0, 120, M.ControlModifier))
        assert v.zoom() > 2.0  # (Ctrl zooms)
        app_settings().setValue("opt/wheel_scrolls", False)
        v.apply_nav_options()
        v.set_zoom(0.9)
        _pump()
        nav.wheelEvent(_wheel_event(nav, 0, 120))
        assert v.zoom() == pytest.approx(1.0)  # (it stops at 100 % ...)
        nav.wheelEvent(_wheel_event(nav, 0, 120))
        assert v.zoom() == pytest.approx(1.0)  # (... for one notch)
        nav.wheelEvent(_wheel_event(nav, 0, 120))
        assert v.zoom() > 1.0
        z = v.zoom()
        nav.wheelEvent(_wheel_event(nav, 0, 120, M.AltModifier))
        assert 1.0 < v.zoom() / z < 1.0 + v.WHEEL_ZOOM_STEP / 100.0  # (Alt: finely)
    finally:
        app_settings().remove("opt/wheel_scrolls")
        v.apply_nav_options()


# ------------------------------------------------------------ 4, 5: the magnifier, the bitmap editor
def test_the_magnifier_follows_the_wheel_rules_and_the_bare_key_setting(win):
    from ctgedit.ui.dialog.dialogs import ZoomWindow
    from ctgedit.ui.prefs.settings import app_settings

    z = ZoomWindow(win.view, win)
    try:
        z._set_factor(4)
        app_settings().setValue("opt/wheel_scrolls", True)
        z.wheelEvent(_wheel_event(z, 0, 120))
        assert z.factor() == 4  # (the plain wheel scrolls elsewhere)
        z.wheelEvent(_wheel_event(z, 0, 120, M.ControlModifier))
        assert z.factor() == 8
        app_settings().setValue("opt/wheel_scrolls", False)
        z.wheelEvent(_wheel_event(z, 0, -120))
        assert z.factor() == 4
        for _k in range(4):  # (Alt: a quarter notch each)
            z.wheelEvent(_wheel_event(z, 0, 120, M.AltModifier))
        assert z.factor() == 8
        # the editor's keys always, the bare ones while the setting says so
        _key(z, M.Key_Minus, M.ControlModifier, "-")
        assert z.factor() == 4
        _key(z, M.Key_Minus, text="-")
        assert z.factor() == 2
        _setting("opt/bare_zoom_keys", False)
        _key(z, M.Key_Plus, text="+")
        assert z.factor() == 2
        _key(z, M.Key_Equal, M.ControlModifier, "=")
        assert z.factor() == 4
        assert "Num" not in z.btn_in.toolTip()
        _setting("opt/bare_zoom_keys", True)
        assert "Num +" in z.btn_in.toolTip()
    finally:
        app_settings().remove("opt/wheel_scrolls")
        app_settings().remove("opt/bare_zoom_keys")
        z.close()
        z.deleteLater()


def test_the_bitmap_editors_zoom_keys_follow_the_setting(win):
    from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = BitmapEditorDialog(bytes([255, 0, 0, 255]) * 16, 4, 4, win)
    dlg.show()
    _pump()
    try:
        c = dlg.canvas
        c.set_zoom(4)
        keys = {sc.key().toString(): sc for sc in dlg.findChildren(QtGui.QShortcut)}
        assert {"Ctrl+=", "Ctrl++", "Ctrl+-", "+", "=", "-"} <= set(keys)
        keys["Ctrl+-"].activated.emit()
        assert c.zoom == 2  # (the editor's key)
        assert keys["-"].isEnabled()
        keys["-"].activated.emit()
        assert c.zoom == 1  # (the bare key, on by default)
        _setting("opt/bare_zoom_keys", False)
        assert all(not sc.isEnabled() for sc in dlg._bare_zoom_keys)
        assert not keys["-"].isEnabled() and not keys["+"].isEnabled()
        assert keys["Ctrl+="].isEnabled()
        keys["Ctrl+="].activated.emit()
        assert c.zoom == 2
        assert dlg._b_in.toolTip() == "Zoom in (Ctrl+=)"
        _setting("opt/bare_zoom_keys", True)
        assert all(sc.isEnabled() for sc in dlg._bare_zoom_keys)
        assert dlg._b_in.toolTip() == "Zoom in (Ctrl+=, +)"
    finally:
        app_settings().remove("opt/bare_zoom_keys")
        dlg._ctg_dropped = True
        dlg.close()
        dlg.deleteLater()


# ------------------------------------------------------------ 6: the manual's reset key
def test_the_manuals_text_size_reset_key_is_a_setting(win):
    from ctgedit.ui.dialog.help_window import HelpWindow, other_window_keys
    from ctgedit.ui.prefs.settings import app_settings

    hw = HelpWindow()
    try:
        assert hw.act_zoom_reset.shortcut().toString() == "Ctrl+0"
        _setting("help/zoom_reset_key", "ctrl1")
        assert hw.act_zoom_reset.shortcut().toString() == "Ctrl+1"
        assert hw.act_zoom_reset.toolTip() == "Text size 100 % (Ctrl+1)"
        keys = other_window_keys()
        assert ("Ctrl+1", "Text size 100 % in the manual") in keys
        assert ("Ctrl+0", "Simulate window: Fit Page") in keys
        _setting("help/zoom_reset_key", "ctrl0")
        assert hw.act_zoom_reset.shortcut().toString() == "Ctrl+0"
    finally:
        app_settings().remove("help/zoom_reset_key")
        hw.close()
        hw.deleteLater()


# ------------------------------------------------------------ 8: the genie form editor's keys
def test_the_genie_form_editor_takes_the_canvas_keys(tmp_path, fx_install, monkeypatch):
    from ctgedit.ui.dialog.genie_form_editor import GenieFormEditor

    src = os.path.join(fx_install.project("FxLib"), "fxparts.frm")
    lib = tmp_path / "fxparts.ctm"
    lib.write_bytes(b"")
    shutil.copyfile(src, tmp_path / "fxparts.frm")
    _app()
    ed = GenieFormEditor(
        str(lib), "fxparts", "fx_insert_object", {"GUID": "", "EVENTCLASS": "", "W": "", "H": "", "AN": ""}
    )
    ed.show()
    _pump()
    try:
        fields = [it for it in ed.items if it.kind == "field"]
        last = fields[-1]
        ed.design.select([last])
        col = last.col
        _key(ed.design, M.Key_Right)
        assert last.col == col + 1  # (one cell)
        _key(ed.design, M.Key_Right, M.ShiftModifier)
        assert last.col == col + 11  # (one large step: 10 cells)
        n = len(ed.items)
        _key(ed.design, M.Key_Backspace)
        assert len(ed.items) == n  # (Backspace never deletes)
        _key(ed.design, M.Key_Escape)
        assert ed.design.selected_items() == [] and ed.isVisible()  # (Esc: the selection)
        asked = []
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "question",
            lambda *a, **k: (asked.append(a[2]), QtWidgets.QMessageBox.Cancel)[1],
        )
        assert ed.dirty()
        # (sent: the key goes on from the grid to the window, as a real press does)
        QtWidgets.QApplication.sendEvent(
            ed.design, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, M.Key_Escape, M.NoModifier)
        )
        _pump()
        assert asked and ed.isVisible()  # (Esc with nothing selected: asks)
        monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Discard)
        ed.reject()
        assert not ed.isVisible()
    finally:
        ed.deleteLater()


# ------------------------------------------------------------ 9: Ctrl + the wheel on cards
def test_ctrl_wheel_sizes_cards_by_one_setting(fx_install):
    _app()
    from ctgedit.ui.browser.thumbs import card_wheel_delta
    from ctgedit.ui.pick_fields import TemplatePicker
    from ctgedit.ui.prefs.settings import app_settings

    w = QtWidgets.QWidget()
    assert card_wheel_delta(_wheel_event(w, 0, 120)) is None  # (no Ctrl: a scroll)
    assert card_wheel_delta(_wheel_event(w, 0, 120, M.ControlModifier)) == 8
    assert card_wheel_delta(_wheel_event(w, 0, -120, M.ControlModifier)) == -8
    assert card_wheel_delta(_wheel_event(w, 0, 30, M.ControlModifier)) == 2  # (a touchpad)
    assert card_wheel_delta(_wheel_event(w, 120, 0, M.ControlModifier | M.AltModifier)) == 2
    app_settings().setValue("opt/card_wheel_step", 20)
    try:
        assert card_wheel_delta(_wheel_event(w, 0, 120, M.ControlModifier)) == 20
        p = TemplatePicker(fx_install.project("FxMain"))
        p.show()
        _pump()
        try:
            w0 = p.card_width()
            vp = p.list.viewport()
            QtWidgets.QApplication.sendEvent(vp, _wheel_event(vp, 0, 120, M.ControlModifier))
            assert p.card_width() == w0 + 20 and p.list.iconSize().width() == w0 + 20
            assert int(app_settings().value("pagebrowser/template/card_size")) == w0 + 20
            p2 = TemplatePicker(fx_install.project("FxMain"))
            assert p2.card_width() == w0 + 20  # (remembered)
            p2.deleteLater()
            from ctgedit.ui.browser import delegates

            p.set_card_width(10000)  # (the page browser's span, not its own)
            assert p.card_width() == delegates.CARD_MAX
            p.set_card_width(1)
            assert p.card_width() == delegates.CARD_MIN
        finally:
            p.close()
            p.deleteLater()
    finally:
        app_settings().remove("opt/card_wheel_step")
        app_settings().remove("pagebrowser/template/card_size")


# ------------------------------------------------------------ 10: the Simulate drag start
def test_the_simulate_middle_click_follows_the_drag_start_setting():
    _app()
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.simulate.canvas import SimCanvas
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulatedPage

    assert SimCanvas.mid_click_px() == C_look.DRAG_STARTS["short"] == 3
    sp = SimulatedPage("", page=_page(), sim=FakeSimulation(), run=False)
    sp.resize(500, 400)
    sp.show()
    _pump()
    try:
        cv = sp.canvas
        got = []
        cv.inputEvent.connect(got.append)

        def mouse(kind, pos):
            t = {
                "press": QtCore.QEvent.MouseButtonPress,
                "move": QtCore.QEvent.MouseMove,
                "release": QtCore.QEvent.MouseButtonRelease,
            }[kind]
            p = QtCore.QPointF(*pos)
            return QtGui.QMouseEvent(
                t,
                p,
                QtCore.QPointF(cv.viewport().mapToGlobal(p.toPoint())),
                M.MiddleButton,
                M.MiddleButton,
                M.NoModifier,
            )

        cv.mousePressEvent(mouse("press", (50, 50)))
        cv.mouseMoveEvent(mouse("move", (56, 50)))
        cv.mouseReleaseEvent(mouse("release", (56, 50)))
        assert not [e for e in got if e.kind in ("down", "up")]  # (6 px: a pan)
        C_look.set_pointer_options(drag="long")
        assert SimCanvas.mid_click_px() == 10
        cv.mousePressEvent(mouse("press", (50, 50)))
        cv.mouseMoveEvent(mouse("move", (56, 50)))
        cv.mouseReleaseEvent(mouse("release", (56, 50)))
        assert [(e.kind, e.button) for e in got] == [("down", "middle"), ("up", "middle")]
    finally:
        C_look.set_pointer_options()
        sp.stop()
        sp.deleteLater()


# ------------------------------------------------------------ 11: the Simulate tabs
def test_simulate_tabs_close_on_a_middle_click_and_have_a_menu(sim, monkeypatch):
    from ctgedit.ui.simulate.sources import FakeSimulation

    win, sp = sim
    sp2 = win.open_page("", page=_page(name="second"), sim=FakeSimulation(), run=False)
    _pump()
    assert win.tabs.count() == 2
    bar = win.tabs.tabBar()
    asked = []
    bar.tabMenuRequested.disconnect(win._tab_menu)  # (its menu would wait for a click)
    bar.tabMenuRequested.connect(lambda k, pos: asked.append(k))
    r = bar.tabRect(1)
    QtWidgets.QApplication.sendEvent(
        bar, QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, r.center(), bar.mapToGlobal(r.center()))
    )
    assert asked == [1]
    m = win.build_tab_menu(1)
    assert [x.text() for x in m.actions() if not x.isSeparator()] == [
        "Close",
        "Close Others",
        "Reload Page",
        "Edit Page in Editor",
    ]
    m.actions()[1].trigger()  # Close Others keeps the second tab
    _pump()
    assert win.tabs.count() == 1 and win.current() is sp2
    m.deleteLater()
    sp = win.open_page("", page=_page(name="third"), sim=FakeSimulation(), run=False)
    _pump()
    assert win.tabs.count() == 2
    r = bar.tabRect(1)
    # a middle click closes the tab under it (on the release, under the same tab)
    g = bar.mapToGlobal(r.center())
    QtWidgets.QApplication.sendEvent(
        bar,
        QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress,
            QtCore.QPointF(r.center()),
            QtCore.QPointF(g),
            M.MiddleButton,
            M.MiddleButton,
            M.NoModifier,
        ),
    )
    QtWidgets.QApplication.sendEvent(
        bar,
        QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonRelease,
            QtCore.QPointF(r.center()),
            QtCore.QPointF(g),
            M.MiddleButton,
            M.NoButton,
            M.NoModifier,
        ),
    )
    _pump()
    assert win.tabs.count() == 1 and win.current() is sp2
    assert sp not in win.tab_pages()


# ------------------------------------------------------------ 13: the Tags panel's double-click
def test_the_tags_panel_says_what_a_double_click_did(win, monkeypatch):
    d = win.tags_dock
    # (a row of the model stands in for a listed tag: the fake install lists few)
    name = "FxTag_Level"
    monkeypatch.setattr(d._model, "name", lambda row: name if row == 0 else "")
    idx = d._model.createIndex(0, 0)
    assert idx.isValid()
    QtWidgets.QApplication.clipboard().setText("")
    d._copy(idx)
    assert QtWidgets.QApplication.clipboard().text() == name
    assert win.statusBar().currentMessage() == "Copied %s to the clipboard." % name
    assert d._table.contextMenuPolicy() == M.CustomContextMenu


# ------------------------------------------------------------ 15: Find, Replace, Pack with no page
def test_find_replace_and_pack_libraries_work_with_no_page_open(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.dialog.pack_dialog import PackLibrariesDialog
    from ctgedit.ui.prefs.settings import app_settings

    w = MainWindow()
    try:
        w._update_actions()
        assert w.doc is None
        for n in ("act_find", "act_replace", "act_pack_libs"):
            assert getattr(w, n).isEnabled(), n
            assert n in MainWindow.ALWAYS_ENABLED
        app_settings().setValue("updatepages/root", "FxMain")
        w._find_dialog(False)
        dlg = w._find_dlg
        assert dlg is not None
        assert dlg._rb_project.isChecked() and not dlg._rb_page.isEnabled() and not dlg._rb_all.isEnabled()
        assert dlg._cb_project.currentText() == "FxMain"
        dlg.close()
        pack = PackLibrariesDialog(w, "")
        assert pack.project.count() > 0 and pack.project.currentText() == "FxMain"
        assert pack.project_dir() and os.path.isdir(pack.project_dir())
        assert not pack.include_chain()
        pack.deleteLater()
    finally:
        app_settings().remove("updatepages/root")
        w.close()
        w.deleteLater()


# ------------------------------------------------------------ 16: the corner double-click
def test_the_navigator_and_ruler_corner_double_click_follow_the_setting(win, monkeypatch):
    v = win.view
    calls = []
    monkeypatch.setattr(v, "fit_page", lambda: calls.append("fit_page"))
    monkeypatch.setattr(v, "fit_all", lambda: calls.append("fit_all"))

    def dbl(widget):
        c = widget.rect().center()
        widget.mouseDoubleClickEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonDblClick,
                QtCore.QPointF(c),
                QtCore.QPointF(widget.mapToGlobal(c)),
                M.LeftButton,
                M.LeftButton,
                M.NoModifier,
            )
        )

    nav, corner = win.navigator.map, win.rulers.corner
    dbl(nav)
    dbl(corner)
    assert calls == ["fit_page", "fit_page"]
    assert corner.tip_text() == "Double-click to fit the page"
    _setting("opt/corner_double_click", "fit_all")
    dbl(nav)
    dbl(corner)
    assert calls == ["fit_page", "fit_page", "fit_all", "fit_all"]
    assert corner.tip_text() == "Double-click to fit all objects"
    _setting("opt/corner_double_click", "nothing")
    dbl(nav)
    dbl(corner)
    assert len(calls) == 4
    _setting("opt/corner_double_click", "fit_page")


def test_a_middle_double_click_on_the_simulated_page_fits_where_nothing_takes_input(sim):
    from ctgedit.ui.prefs.settings import app_settings

    win, sp = sim
    cv = sp.canvas
    got = []
    cv.inputEvent.connect(got.append)
    cv.wheelEvent(_wheel_event(cv.viewport(), 0, 360, M.ControlModifier))
    assert not cv._auto_fit and cv.zoom() != pytest.approx(cv.fit_zoom())

    def dbl(pos):
        p = QtCore.QPointF(*pos)
        cv.mouseDoubleClickEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonDblClick,
                p,
                QtCore.QPointF(cv.viewport().mapToGlobal(p.toPoint())),
                M.MiddleButton,
                M.MiddleButton,
                M.NoModifier,
            )
        )

    dbl((5, 5))  # (no object takes input here)
    _pump()
    assert cv.zoom() == pytest.approx(cv.fit_zoom()) and not got

    def view_pos(x, y):
        """Page point (x, y) in the middle of the view, as a viewport position."""
        cv.centerOn(QtCore.QPointF(x, y))
        _pump(1)
        p = cv.mapFromScene(QtCore.QPointF(x, y))
        return p.x(), p.y()

    cv.wheelEvent(_wheel_event(cv.viewport(), 0, 120, M.ControlModifier))
    assert not cv._auto_fit
    rect_at = view_pos(140, 130)
    assert cv.ans_at(QtCore.QPoint(*rect_at)) and not cv.input_at(QtCore.QPoint(*rect_at))
    dbl(rect_at)  # (a drawn rectangle takes no input)
    _pump()
    assert cv.zoom() == pytest.approx(cv.fit_zoom()) and not got
    cv.wheelEvent(_wheel_event(cv.viewport(), 0, 120, M.ControlModifier))
    z = cv.zoom()
    button_at = view_pos(340, 260)
    assert cv.input_at(QtCore.QPoint(*button_at))
    dbl(button_at)  # (a button takes it: the page's command)
    assert cv.zoom() == pytest.approx(z) and [e.kind for e in got] == ["double"]
    got.clear()
    app_settings().setValue("opt/middle_double_click", "nothing")
    try:
        cv.wheelEvent(_wheel_event(cv.viewport(), 0, 360, M.ControlModifier))
        z = cv.zoom()
        dbl((5, 5))
        assert cv.zoom() == pytest.approx(z)
        assert [e.kind for e in got] == ["double"]  # (the page's MBUTTON_DBL command)
    finally:
        app_settings().remove("opt/middle_double_click")
