"""Simulate settings."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the Simulate window's keys before they were settings, with the editor's zoom commands on
#: the editor's keys and the panels on Alt+1 / Alt+2 (the editor's View > Panels keys)
OLD_KEYS = {
    "sim.run": ["F5"],
    "sim.pause": ["Shift+F5"],
    "sim.step": ["F10"],
    "sim.open_page": ["Ctrl+O"],
    "sim.copy_image": ["Ctrl+Shift+C"],
    "sim.inspector": ["Ctrl+I"],
    "sim.zoom_fit": ["Ctrl+0"],
    "sim.zoom_100": ["Ctrl+1"],
    "sim.zoom_in": ["Ctrl+=", "Ctrl++"],
    "sim.zoom_out": ["Ctrl+-"],
    "sim.inputs": ["Alt+1"],
    "sim.outputs": ["Alt+2"],
}


def _page(name="audit_sim"):
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name=name)
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0x00FF00, 0x00FF00
    o.raw["_type_code"] = 0x0F
    p.objects = [o]
    return p


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _sim():
    """A demo simulation that keeps the clock requests it gets."""
    from ctgedit.ui.simulate.sources import FakeSimulation

    class Recording(FakeSimulation):
        def __init__(self):
            super().__init__(clock=_Clock())
            self.asked = []

        def clock(self, mode, value=None):
            self.asked.append((mode, value))
            super().clock(mode, value)

    return Recording()


class _Env:
    def __init__(self, editor, sim):
        self.editor = editor
        self.sim = sim


@pytest.fixture()
def env():
    """An editor window (the Settings window's owner) and the application's Simulate window
    (the one the Settings window applies its Simulate page to), shown at 1200 x 900."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate import window as SW
    from ctgedit.ui.simulate.canvas import SimCanvas

    saved = {k: getattr(SimCanvas, k) for k in ("RESERVED_KEYS", "open_zoom", "space_pans", "keys_to_page")}
    for w in list(SW._WINDOWS):
        w.hide()
        w.deleteLater()
    SW._WINDOWS.clear()
    editor = MainWindow()
    sw = SW.simulate_window(create=True)
    sw.resize(1200, 900)
    sw.show()
    QtWidgets.QApplication.processEvents()
    yield _Env(editor, sw)
    sw.hide()
    sw.shut_down()
    sw.deleteLater()
    SW._WINDOWS.clear()
    if editor in MainWindow.instances:
        MainWindow.instances.remove(editor)
    editor.deleteLater()
    for k, v in saved.items():
        setattr(SimCanvas, k, v)
    QtWidgets.QApplication.processEvents()


def _settings(editor, page):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    dlg = SettingsDialog(editor, page=page)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    return dlg


def _wait(cond, ms=3000):
    """Past the Settings window's live apply (until ``cond()`` holds, at most ``ms``)."""
    QtTest.QTest.qWait(150)
    waited = 150
    while not cond() and waited < ms:
        QtTest.QTest.qWait(50)
        waited += 50
    return cond()


def _choose(box, value):
    k = box.findData(value)
    assert k >= 0, value
    box.setCurrentIndex(k)


def _settle(ms=250):
    QtTest.QTest.qWait(ms)


def _keys(a):
    return [s.toString() for s in a.shortcuts()]


def _override(canvas, key, mods, text=""):
    ev = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, key, mods, text)
    ev.ignore()
    canvas.event(ev)
    return ev.isAccepted()


# ------------------------------------------------------------------ the defaults
def test_every_default_is_the_behaviour_from_before(env):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.canvas import SimCanvas

    sw = env.sim
    assert options.open_zoom() == "fit" and options.get("space_pans") is True
    assert options.start_speed() == 1.0 and options.step() == "scan"
    assert options.step_seconds() is None  # (the simulator's own step, as before)
    assert {cid: _keys(a) for cid, a in sw.sim_actions().items()} == OLD_KEYS
    assert {cid: ks for cid, (ks, _s) in keymap.sim_resolve()[0].items()} == OLD_KEYS
    assert SimCanvas.RESERVED_KEYS == frozenset(
        ("Ctrl+O", "Ctrl+I", "Ctrl+Shift+C", "Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl++", "Ctrl+-")
    )
    assert SimCanvas.open_zoom == "fit" and SimCanvas.space_pans is True
    assert sw.clock.act_step.toolTip() == "Run one page scan while the clock is stopped (F10)"
    assert sw.clock.act_run.toolTip() == "Run the simulated clock (F5)"
    assert sw.dock_inputs.toggleViewAction().toolTip() == "Show or hide the Inputs panel (Alt+1)"
    assert sw.clock.speed.currentData() == 1.0
    # the Settings window shows them
    dlg = _settings(env.editor, "Simulate")
    try:
        c = dlg._sim
        assert c.open_zoom.currentData() == "fit" and c.space_pans.isChecked()
        assert c.start_speed.currentData() == 1.0 and c.step.currentData() == "scan"
        keys = {r.key for r in dlg._tabs.rows()}
        assert {"sim/open_zoom", "sim/space_pans", "sim/start_speed", "sim/step"} <= keys
    finally:
        dlg.close_live()
        dlg.deleteLater()


# ------------------------------------------------------------------ Open a page at
def test_a_page_opens_fitted_or_at_100_percent_as_the_runtime(env):
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.canvas import SimCanvas

    sw = env.sim
    sp = sw.open_page("", page=_page(), sim=_sim(), run=False)
    _settle()
    cv = sp.canvas
    # fitted by the editor's rule (the page plus Settings > Canvas's margin in the viewport,
    # at most the zoom a page opens at in the editor)
    from ctgedit.ui.editor import viewnav

    fit = min(viewnav.fit_zoom(QtCore.QRectF(0, 0, 400, 300), cv.maximumViewportSize()), 2.0)
    assert abs(cv.zoom() - fit) < 1e-3 and abs(fit - 1.0) > 0.2  # fitted (as before)
    # Settings > Simulate > Open a page at: 100 %, as the runtime
    dlg = _settings(env.editor, "Simulate")
    try:
        _choose(dlg._sim.open_zoom, "actual")
        assert _wait(lambda: SimCanvas.open_zoom == "actual")
    finally:
        dlg.close_live()
        dlg.deleteLater()
    assert options.open_zoom() == "actual"
    assert abs(cv.zoom() - fit) < 1e-3  # (an open tab keeps its view)
    sp2 = sw.open_page("", page=_page("audit_sim_2"), sim=_sim(), run=False)
    _settle()
    cv2 = sp2.canvas
    assert cv2.zoom() == 1.0
    shown = cv2.mapFromScene(QtCore.QRectF(0, 0, 400, 300)).boundingRect()
    assert cv2.viewport().rect().contains(shown)  # (it fits: all of it in view)
    assert shown.width() in (400, 401) and shown.left() > 100  # (in the middle part)
    sw.resize(1000, 760)
    _settle()
    assert cv2.zoom() == 1.0  # (not fitted again on a resize)
    # a page larger than the view shows its top left corner at 100 %
    big = _page("audit_sim_big")
    big.appearance.width, big.appearance.height = 3000, 2000
    sp3 = sw.open_page("", page=big, sim=_sim(), run=False)
    _settle()
    cv3 = sp3.canvas
    assert cv3.zoom() == 1.0
    top_left = cv3.mapToScene(QtCore.QPoint(0, 0))
    assert -10 <= top_left.x() <= 0 and -10 <= top_left.y() <= 0


# ------------------------------------------------------------------ Space + drag pans the page
def test_space_pans_or_goes_to_the_page_as_in_the_runtime(env):
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.canvas import SimCanvas

    sw = env.sim
    sp = sw.open_page("", page=_page(), sim=_sim(), run=False)
    _settle()
    cv = sp.canvas
    got = []
    cv.inputEvent.connect(got.append)
    Qt = QtCore.Qt
    # on (the default): Space held pans; neither it nor its repeats reach the page
    QtTest.QTest.keyPress(cv, Qt.Key_Space)
    assert cv._space
    cv.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True))
    QtTest.QTest.keyRelease(cv, Qt.Key_Space)
    assert not cv._space and not [e for e in got if e.kind == "key"]
    # Settings > Simulate > "Space + drag pans the page" off
    dlg = _settings(env.editor, "Simulate")
    try:
        dlg._sim.space_pans.setChecked(False)
        assert _wait(lambda: SimCanvas.space_pans is False)
    finally:
        dlg.close_live()
        dlg.deleteLater()
    assert options.get("space_pans") is False
    QtTest.QTest.keyPress(cv, Qt.Key_Space, Qt.NoModifier)
    assert not cv._space
    keys = [(e.key, e.modifiers) for e in got if e.kind == "key"]
    assert keys == [("Space", 0)]  # (a key of the page's commands)
    QtTest.QTest.keyRelease(cv, Qt.Key_Space)
    # a left drag with Space held is the page's drag, not a pan
    got.clear()
    QtTest.QTest.mousePress(cv.viewport(), Qt.LeftButton, Qt.NoModifier, QtCore.QPoint(300, 250))
    assert cv._pan is None and [e.kind for e in got if e.kind != "move"] == ["down"]
    QtTest.QTest.mouseRelease(cv.viewport(), Qt.LeftButton, Qt.NoModifier, QtCore.QPoint(300, 250))
    # the middle button still pans
    QtTest.QTest.mousePress(cv.viewport(), Qt.MiddleButton, Qt.NoModifier, QtCore.QPoint(300, 250))
    assert cv._pan is not None
    QtTest.QTest.mouseRelease(cv.viewport(), Qt.MiddleButton, Qt.NoModifier, QtCore.QPoint(300, 250))


# ------------------------------------------------------------------ Clock speed at start
def test_a_new_simulation_starts_at_the_clock_speed_at_start(env):
    from ctgedit.ui.simulate import options

    sw = env.sim
    # (a tab of its own simulation: the Cicode simulator's source, turned off in the tests)
    sp = sw.open_page("", page=_page(), run=False)
    assert sp.sim.fake.clock_state().speed == 1.0  # real time, as before
    dlg = _settings(env.editor, "Simulate")
    try:
        _choose(dlg._sim.start_speed, 5.0)
        assert _wait(lambda: options.start_speed() == 5.0)
    finally:
        dlg.close_live()
        dlg.deleteLater()
    assert sp.sim.fake.clock_state().speed == 1.0  # (a running one keeps its speed)
    sp2 = sw.open_page("", page=_page("audit_sim_2"), run=False)
    assert sp2.sim.fake.clock_state().speed == 5.0
    # the speed the simulator is asked for when it connects (its clock is the tab's own)
    assert sp2.sim._want_speed == 5.0 and sp.sim._want_speed == 1.0
    # a simulation given to the tab (a window of a running one) keeps the speed it has
    given = _sim()
    sw.open_page("", page=_page("audit_sim_3"), sim=given, run=False)
    assert ("speed", 5.0) not in given.asked
    # with no page simulated the Speed box shows what the next simulation starts at
    for p in sw.tab_pages():
        sw.close_tab(sw.tabs.indexOf(p))
    QtWidgets.QApplication.processEvents()
    assert sw.current() is None
    sw.apply_settings()
    assert sw.clock.speed.currentData() == 5.0


# ------------------------------------------------------------------ Step runs
def test_step_runs_one_scan_ten_scans_or_one_second(env):
    from ctgedit.ui.simulate import options

    sw = env.sim
    sim = _sim()
    sw.open_page("", page=_page(), sim=sim, run=False)
    _settle(100)
    step = sw.clock.act_step
    assert step.isEnabled()
    scan = sim.clock_state().scan
    step.trigger()
    assert sim.asked[-1] == ("step", None) and sim.clock_state().scan == scan + 1
    for choice, seconds, scans, tip in (
        ("ten", 2.5, 10, "Run ten page scans while the clock is stopped (F10)"),
        ("second", 1.0, 4, "Run one second of simulated time while the clock is stopped (F10)"),
    ):
        dlg = _settings(env.editor, "Simulate")
        try:
            _choose(dlg._sim.step, choice)
            assert _wait(lambda: options.step() == choice and step.toolTip() == tip)
        finally:
            dlg.close_live()
            dlg.deleteLater()
        _settle(100)
        scan = sim.clock_state().scan
        assert step.isEnabled()
        step.trigger()
        assert sim.asked[-1] == ("step", seconds)  # (sent to the simulator as seconds)
        assert sim.clock_state().scan == scan + scans


# ------------------------------------------------------------------ Settings > Keyboard
def _rows(page):
    from ctgedit.ui.prefs.keymap_page import ID_ROLE

    return {page.table.item(r, 0).data(ID_ROLE): r for r in range(page.table.rowCount())}


def _change(page, cid, key):
    ch = page.change(cid)
    ch.key1.setKeySequence(QtGui.QKeySequence(key))
    QtWidgets.QApplication.processEvents()
    return ch


def test_the_simulate_windows_keys_are_rows_of_settings_keyboard(env):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.keymap_page import KEYS_ROLE
    from ctgedit.ui.prefs.settings import app_settings

    editor, sw = env.editor, env.sim
    sim = _sim()
    sp = sw.open_page("", page=_page(), sim=sim, run=False)
    _settle(100)
    editor_keys = {c.id: _keys(c.action) for c in keymap.commands(editor)}
    dlg = _settings(editor, "Keyboard")
    page = dlg._keys
    try:
        rows = _rows(page)
        for cid, keys in OLD_KEYS.items():
            r = rows[cid]
            assert page.table.item(r, 1).text() == "Simulate window"
            assert page.table.item(r, 2).data(KEYS_ROLE) == keys
            assert page.table.item(r, 3).text() == "Simulate"
            assert page.table.item(r, 4).text() == "Default"
        assert page.table.item(rows["sim.step"], 0).text() == "Step"
        # Step on F11: the editor has F11 too, but the two windows' keys never meet
        ch = _change(page, "sim.step", "F11")
        assert ch.conflict.isHidden()
        ch._ok()
        assert _wait(lambda: _keys(sw.clock.act_step) == ["F11"])
        assert app_settings().value("keys/sim.step") in (["F11"], "F11")
        assert page.table.item(_rows(page)["sim.step"], 4).text() == "User"
        assert sw.clock.act_step.toolTip() == ("Run one page scan while the clock is stopped (F11)")
        # a key another of its commands has is named, and Use Anyway takes it
        ch = _change(page, "sim.step", "F5")
        assert ch.conflict.text() == "F5 is already Run."
        ch.use_anyway.click()
        assert _wait(lambda: _keys(sw.clock.act_step) == ["F5"] and _keys(sw.clock.act_run) == [])
        assert sw.clock.act_run.toolTip() == "Run the simulated clock"
        # Space is the simulated page's
        ch = _change(page, "sim.step", "Space")
        assert ch.conflict.text() == "Space is a key of the simulated page. Choose another key."
        assert not ch.use_anyway.isVisible() and not ch._ok_button.isEnabled()
        ch.reject()
        # Step back on F10 for the key test below
        ch = _change(page, "sim.step", "F10")
        ch._ok()
        assert _wait(lambda: _keys(sw.clock.act_step) == ["F10"])
    finally:
        dlg.close_live()
        dlg.deleteLater()
    # the editor's own commands kept their keys (File > Run is still F5)
    assert {c.id: _keys(c.action) for c in keymap.commands(editor)} == editor_keys
    assert _keys(editor.act_run) == ["F5"]
    # the key runs the command in the Simulate window (focus outside the page)
    sw.activateWindow()
    assert QtTest.QTest.qWaitForWindowActive(sw, 2000)
    sw.input_tabs.setFocus()
    QtWidgets.QApplication.processEvents()
    scan = sim.clock_state().scan
    QtTest.QTest.keyClick(sw.input_tabs, QtCore.Qt.Key_F10)
    QtWidgets.QApplication.processEvents()
    assert sim.clock_state().scan == scan + 1
    assert sp is sw.current()


def test_open_page_without_a_key_lets_the_page_have_ctrl_o(env):
    """The keys of Open Page, Copy Image and the Inspector stay the window's while the page
    has the keyboard: they follow Settings > Keyboard."""
    from ctgedit.ui.simulate.canvas import SimCanvas

    editor, sw = env.editor, env.sim
    sp = sw.open_page("", page=_page(), sim=_sim(), run=False)
    _settle(100)
    cv = sp.canvas
    Ctrl = QtCore.Qt.ControlModifier
    assert not _override(cv, QtCore.Qt.Key_O, Ctrl, "\x0f")  # Open Page's (as before)
    dlg = _settings(editor, "Keyboard")
    try:
        page = dlg._keys
        ch = page.change("sim.open_page")
        ch.remove.click()
        assert _wait(lambda: _keys(sw.clock.act_open_page) == [])
        ch = _change(page, "sim.inspector", "Ctrl+Alt+I")
        ch._ok()
        assert _wait(lambda: "Ctrl+Alt+I" in SimCanvas.RESERVED_KEYS)
    finally:
        dlg.close_live()
        dlg.deleteLater()
    assert SimCanvas.RESERVED_KEYS == frozenset(
        ("Ctrl+Alt+I", "Ctrl+Shift+C", "Ctrl+0", "Ctrl+1", "Ctrl+=", "Ctrl++", "Ctrl+-")
    )
    assert sw.clock.act_open_page.toolTip().endswith("clock)")  # (no key named)
    assert _override(cv, QtCore.Qt.Key_O, Ctrl, "\x0f")  # the page's now
    assert _override(cv, QtCore.Qt.Key_I, Ctrl, "\x09")
    assert not _override(cv, QtCore.Qt.Key_I, Ctrl | QtCore.Qt.AltModifier)
    got = []
    cv.inputEvent.connect(got.append)
    QtTest.QTest.keyClick(cv, QtCore.Qt.Key_O, Ctrl)
    assert [(e.kind, e.key, e.modifiers) for e in got if e.kind == "key"] == [("key", "O", 2)]


@pytest.fixture()
def docked():
    """An editor window, shown, with the Simulate window docked in it (not floating). No key
    of these tests reaches File > Run or File > Open (never a compile, a run or a dialog)."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import window as SW
    from ctgedit.ui.simulate.canvas import SimCanvas

    saved = {k: getattr(SimCanvas, k) for k in ("RESERVED_KEYS", "open_zoom", "space_pans", "keys_to_page")}
    for w in list(SW._WINDOWS):
        w.hide()
        w.deleteLater()
    SW._WINDOWS.clear()
    app_settings().setValue("simulate/dock_floating", False)
    editor = MainWindow()
    editor.resize(1200, 800)
    editor.show()
    for a in (editor.act_run, editor.act_open):
        a.triggered.disconnect()
    SW.open_simulation("", page=_page(), sim=_sim(), host=editor)
    sw = SW.simulate_window(create=False)
    for a in (sw.clock.act_open_page, sw.clock.act_inspect, sw.clock.act_copy):
        a.triggered.disconnect()
    QtWidgets.QApplication.processEvents()
    yield _Env(editor, sw)
    for key in (QtCore.Qt.Key_Shift, QtCore.Qt.Key_Control):
        QtTest.QTest.keyRelease(editor.view, key, QtCore.Qt.NoModifier)
    editor.doc = None
    sw.release_host(editor)
    if editor in MainWindow.instances:
        MainWindow.instances.remove(editor)
    editor.close()
    sw.close()
    sw.deleteLater()
    SW._WINDOWS.clear()
    editor.deleteLater()
    for k, v in saved.items():
        setattr(SimCanvas, k, v)
    app_settings().setValue("simulate/dock_floating", False)
    QtWidgets.QApplication.processEvents()


def _press(widget, key, mods=QtCore.Qt.NoModifier):
    widget.setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(widget, key, mods)
    QtWidgets.QApplication.processEvents()  # (the command runs after the key event)


def test_docked_the_windows_keys_work_in_it_and_the_editors_in_the_editor(docked):
    """Docked, the Simulate window's keys work while the focus is in it and the editor's in the
    editor, also a key both have (Ctrl+1: Zoom 100% in both)."""
    from ctgedit.ui.prefs import keymap

    editor, sw = docked.editor, docked.sim
    assert sw.is_docked() and not editor._sim_dock.isFloating()
    Qt = QtCore.Qt
    hits = []
    for cid, a in sw.sim_actions().items():
        a.triggered.connect(lambda *_a, cid=cid: hits.append(cid))
    editor.act_zoom_100.triggered.connect(lambda *_a: hits.append("editor zoom 100"))
    probe = QtGui.QAction("probe", editor)
    probe.setShortcut(QtGui.QKeySequence("Ctrl+Shift+F12"))
    probe.triggered.connect(lambda: hits.append("editor probe"))
    editor.addAction(probe)
    editor.activateWindow()
    assert QtTest.QTest.qWaitForWindowActive(editor, 2000)
    assert {a.shortcutContext() for a in sw._docked_scope_actions()} == {Qt.WidgetWithChildrenShortcut}
    # the built-in keys: Ctrl+1 is Zoom 100% in the editor and in here (each window's own)
    _press(editor.view, Qt.Key_1, Qt.ControlModifier)
    assert hits == ["editor zoom 100"]
    hits.clear()
    _press(sw.inputs, Qt.Key_1, Qt.ControlModifier)
    assert hits == ["sim.zoom_100"]
    hits.clear()
    _press(sw.inputs, Qt.Key_1, Qt.AltModifier)
    assert hits == ["sim.inputs"]
    hits.clear()
    _press(sw.inputs, Qt.Key_I, Qt.ControlModifier)
    _press(sw.inputs, Qt.Key_F5, Qt.ShiftModifier)
    assert hits == ["sim.inspector", "sim.pause"]
    hits.clear()
    # on the simulated page: the keys of Open Page, Copy Image and the Inspector stay the
    # window's (the page does not get them); Page Keys gives the page the others first
    cv = sw.current().canvas
    got = []
    cv.inputEvent.connect(lambda e: got.append(e.key) if e.kind == "key" else None)
    _press(cv, Qt.Key_I, Qt.ControlModifier)
    _press(cv, Qt.Key_2, Qt.ControlModifier)
    assert hits == ["sim.inspector"] and got == ["2"]
    hits.clear()
    got.clear()
    # a key of the user's own on the Inputs panel that an editor command has: no conflict
    # (the two never meet), each runs where its window has the focus
    assert keymap.conflicts(editor, "sim.inputs", ["Ctrl+Shift+F12"], None, {}) == []
    keymap.store_user_keys({"sim.inputs": ["Ctrl+Shift+F12"], "sim.outputs": ["Ctrl+Shift+F11"]})
    keymap.apply_everywhere()
    QtWidgets.QApplication.processEvents()
    assert _keys(sw.dock_inputs.toggleViewAction()) == ["Ctrl+Shift+F12"]
    _press(editor.view, Qt.Key_F12, Qt.ControlModifier | Qt.ShiftModifier)
    _press(editor.view, Qt.Key_F11, Qt.ControlModifier | Qt.ShiftModifier)
    assert hits == ["editor probe"]
    hits.clear()
    _press(sw.inputs, Qt.Key_F12, Qt.ControlModifier | Qt.ShiftModifier)
    _press(sw.inputs, Qt.Key_F11, Qt.ControlModifier | Qt.ShiftModifier)
    _press(sw.inputs, Qt.Key_1, Qt.AltModifier)  # (no command of it now)
    assert hits == ["sim.inputs", "sim.outputs"]
    hits.clear()
    # a window of its own: the keys work in all of it, as before
    sw.pop_out()
    QtWidgets.QApplication.processEvents()
    assert {a.shortcutContext() for a in sw._docked_scope_actions()} == {Qt.WindowShortcut}
    sw.activateWindow()
    assert QtTest.QTest.qWaitForWindowActive(sw, 2000)
    _press(sw.inputs, Qt.Key_F11, Qt.ControlModifier | Qt.ShiftModifier)
    assert hits == ["sim.outputs"]
    # and docked again: the window's own
    sw.dock_into(editor)
    QtWidgets.QApplication.processEvents()
    assert {a.shortcutContext() for a in sw._docked_scope_actions()} == {Qt.WidgetWithChildrenShortcut}


def test_settings_shows_the_choice_the_window_reads(env):
    """A stored value in another case or with spaces: the Settings window shows what the
    Simulate window does with it, and OK writes that back."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.settings_dialog import SimSettingsControls

    st = app_settings()
    for name, raw in (("open_zoom", " Actual"), ("step", "TEN"), ("start_speed", "5")):
        st.setValue(options.OPTIONS[name][0], raw)
    assert (options.open_zoom(), options.step(), options.start_speed()) == ("actual", "ten", 5.0)
    host = QtWidgets.QWidget()
    try:
        c = SimSettingsControls(host)
        assert c.open_zoom.currentData() == "actual" and c.step.currentData() == "ten"
        assert c.start_speed.currentData() == 5.0
        c.store()
        assert (options.get("open_zoom"), options.get("step")) == ("actual", "ten")
        assert options.start_speed() == 5.0
        # a garbled value: the default, as the window reads it
        for name, raw in (("open_zoom", "zoomed"), ("step", "two"), ("start_speed", "fast")):
            st.setValue(options.OPTIONS[name][0], raw)
        c.load()
        assert (c.open_zoom.currentData(), c.step.currentData(), c.start_speed.currentData()) == (
            "fit",
            "scan",
            1.0,
        )
        c.defaults()
        assert (c.open_zoom.currentData(), c.step.currentData(), c.start_speed.currentData()) == (
            "fit",
            "scan",
            1.0,
        )
    finally:
        host.deleteLater()


def test_a_garbled_check_box_option_is_its_default(env):
    """'maybe' stored for Space + drag pans the page kept it on (its default), not off."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import options

    st = app_settings()
    key = options.OPTIONS["space_pans"][0]
    for raw, want in (
        ("maybe", True),
        ("off", False),
        ("No", False),
        ("0", False),
        ("false", False),
        (" yes ", True),
        ("1", True),
        ("true", True),
    ):
        st.setValue(key, raw)
        assert options.get("space_pans") is want, raw
    # (every check box option: its own default, on or off)
    st.setValue(options.OPTIONS["autorun"][0], "maybe")
    st.setValue(options.OPTIONS["tab_per_window"][0], "maybe")
    assert options.get("autorun") is True and options.get("tab_per_window") is False


def test_a_page_at_100_percent_keeps_the_runtimes_place_as_the_tab_changes_size(env):
    """Its top left corner stays 8 px in (the middle when it fits) as a panel goes or the
    window grows, until the user zooms, pans or scrolls."""
    from ctgedit.ui.simulate import options

    sw = env.sim
    options.put("open_zoom", "actual")
    sw.apply_settings()
    sw.resize(1000, 700)
    sw.dock_inputs.show()
    _settle(100)
    big = _page("audit_sim_big")
    big.appearance.width, big.appearance.height = 1600, 1000
    sp = sw.open_page("", page=big, sim=_sim(), run=False)
    _settle()
    cv = sp.canvas

    def corner():
        return cv.mapFromScene(QtCore.QPointF(0, 0))

    assert cv.zoom() == 1.0 and corner() == QtCore.QPoint(8, 8)
    sw.dock_inputs.hide()  # (Alt+1)
    _settle()
    assert corner() == QtCore.QPoint(8, 8)
    sw.resize(1500, 1000)
    _settle()
    assert cv.viewport().width() < 1616 and corner() == QtCore.QPoint(8, 8)
    # a page that fits stays in the middle (of the work area around it, as it opened)
    sp2 = sw.open_page("", page=_page("audit_sim_small"), sim=_sim(), run=False)
    _settle()
    cv2 = sp2.canvas

    def off_centre():
        return cv2.mapFromScene(QtCore.QPointF(200, 150)) - cv2.viewport().rect().center()

    before = off_centre()
    assert before.manhattanLength() < 60
    sw.dock_inputs.show()
    _settle()
    assert cv2.zoom() == 1.0 and (off_centre() - before).manhattanLength() <= 2
    # scrolled by the user (a scroll bar): the view keeps the place they chose
    sw.tabs.setCurrentWidget(sp)
    _settle()
    assert corner() == QtCore.QPoint(8, 8)
    cv.horizontalScrollBar().triggerAction(QtWidgets.QAbstractSlider.SliderSingleStepAdd)
    assert not cv._auto_actual and corner().x() < 8
    sw.dock_inputs.hide()
    _settle()
    assert corner() != QtCore.QPoint(8, 8)  # (not put back: the view's centre kept)
    # the thin bars over the page: a press on one is a scroll too
    cv.show_actual_size()
    assert cv._auto_actual
    bar = cv.overlay_bars.h
    for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
        QtWidgets.QApplication.sendEvent(
            bar,
            QtGui.QMouseEvent(
                kind,
                QtCore.QPointF(4, 2),
                QtCore.QPointF(bar.mapToGlobal(QtCore.QPoint(4, 2))),
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            ),
        )
    assert not cv._auto_actual


def test_open_a_page_at_has_the_words_of_the_editors_row(env):
    """Settings > Simulate > Open a page at and Settings > Canvas > Open a page at name the
    same choices alike (the search shows them side by side)."""
    dlg = _settings(env.editor, "Simulate")
    try:
        rows = {r.key: r for r in dlg._tabs.rows() if r.key in ("opt/open_zoom", "sim/open_zoom")}
        assert rows["sim/open_zoom"].label_text == rows["opt/open_zoom"].label_text == "Open a page at:"
        ed, sim = dlg._open_zoom, dlg._sim.open_zoom
        assert (
            sim.itemText(sim.findData("fit"))
            == ed.itemText(ed.findData("fit"))
            == "Fit the page in the window"
        )
        assert sim.itemText(sim.findData("actual")) == ed.itemText(ed.findData("100")) == "100 %"
    finally:
        dlg.close_live()
        dlg.deleteLater()
