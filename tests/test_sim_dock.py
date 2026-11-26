"""The Simulate window docked in the editor."""

import gc
import os

import pytest
from qtkit import app as _app


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _rect_page():
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name="sim_dock")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0xFF0000, 0xFF0000
    o.raw["_type_code"] = 0x0F
    p.objects = [o]
    return p


class _Sim:
    """A FakeSimulation that says when it was stopped."""

    def __new__(cls):
        from ctgedit.ui.simulate.sources import FakeSimulation

        class Stopping(FakeSimulation):
            stopped = 0

            def stop(self):
                type(self).stopped += 1
                super().stop()

        Stopping.stopped = 0
        return Stopping(clock=_Clock())


@pytest.fixture()
def editor():
    app = _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate import window as SW

    wins = []
    # (these tests start from a stored 'docked' choice: the first open floats, see
    # test_the_first_open_is_a_floating_window_of_a_good_size)
    from ctgedit.ui.prefs.settings import app_settings as _st

    _st().setValue("simulate/dock_floating", False)

    def make():
        w = MainWindow()
        w.resize(1200, 800)
        w.show()
        wins.append(w)
        return w

    yield make
    sw = SW.simulate_window(create=False)
    for w in wins:
        w.doc = None
        if sw is not None:
            sw.release_host(w)
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()
    if sw is not None:
        sw.close()
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("simulate/dock_floating", False)
    QtWidgets.QApplication.processEvents()
    gc.collect()
    del app


def _in(widget, ancestor) -> bool:
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = w.parentWidget()
    return False


def test_it_opens_docked_and_floats_like_a_panel(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import window as SW

    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    sw = SW.simulate_window(create=False)
    QtWidgets.QApplication.processEvents()
    # in the editor window's dock, its tabs, clock bar and panels with it
    assert sw.is_docked() and sw.host_window() is w and not sw.isWindow()
    assert _in(sw, w) and w._sim_dock.widget() is sw and w._sim_dock.isVisible()
    assert sw.tabs.count() == 1 and sw.dock_inputs.parentWidget() is sw
    assert w._sim_dock.windowTitle().startswith("Simulate")
    # no Dock / Pop Out button: the dock floats like the editor's other panels
    assert not hasattr(sw.clock, "act_dock")
    assert w._sim_dock.features() & QtWidgets.QDockWidget.DockWidgetFloatable
    w._sim_dock.setFloating(True)
    QtWidgets.QApplication.processEvents()
    assert w._sim_dock.isFloating() and w._sim_dock.widget() is sw and sw.tabs.count() == 1
    assert str(app_settings().value("simulate/dock_floating")).lower() in ("1", "true")
    w._sim_dock.setFloating(False)
    QtWidgets.QApplication.processEvents()
    assert not w._sim_dock.isFloating() and _in(sw, w)
    assert str(app_settings().value("simulate/dock_floating")).lower() in ("0", "false")
    assert SW.simulate_window(create=False) is sw and len(SW._WINDOWS) == 1


def test_floating_is_remembered(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    sw = SW.simulate_window(create=False)
    w._sim_dock.setFloating(True)
    w._sim_dock.setGeometry(150, 120, 640, 420)
    QtWidgets.QApplication.processEvents()
    sw._save_layout()
    w._sim_dock.close()
    QtWidgets.QApplication.processEvents()
    v = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=v)
    QtWidgets.QApplication.processEvents()
    assert sw.host_window() is v and v._sim_dock.isFloating()
    g = v._sim_dock.geometry()
    assert abs(g.width() - 640) <= 40 and g.height() >= 420, g  # (no lower than its content)


def test_simulate_page_opens_where_it_was_last_in_the_window_that_asks(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    a, b = editor(), editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=a)
    sw = SW.simulate_window(create=False)
    assert sw.host_window() is a
    # another editor window opens a simulation: the Simulate window docks there
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=b)
    QtWidgets.QApplication.processEvents()
    assert sw.host_window() is b and _in(sw, b) and not _in(sw, a)
    assert not a._sim_dock.isVisible() and b._sim_dock.isVisible() and sw.tabs.count() == 2


def test_closing_the_dock_stops_the_simulation_and_keeps_the_choice(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    sim = _Sim()
    SW.open_simulation("", page=_rect_page(), sim=sim, host=w)
    sw = SW.simulate_window(create=False)
    assert sw.is_docked()
    w._sim_dock.close()
    QtWidgets.QApplication.processEvents()
    assert type(sim).stopped >= 1 and sw.tabs.count() == 0 and not sw._timer.isActive()
    assert not w._sim_dock.isVisible()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    assert sw.is_docked() and w._sim_dock.isVisible() and sw.tabs.count() == 1


def test_closing_the_editor_window_stops_its_docked_simulation(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    sim = _Sim()
    SW.open_simulation("", page=_rect_page(), sim=sim, host=w)
    sw = SW.simulate_window(create=False)
    assert sw.host_window() is w
    w.close()
    QtWidgets.QApplication.processEvents()
    assert type(sim).stopped >= 1 and sw.tabs.count() == 0
    # the one Simulate window outlives the editor window (out of its dock, hidden)
    assert SW._alive(sw) and not sw.is_docked() and sw.isWindow() and not sw.isVisible()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()
    assert SW.simulate_window(create=False) is sw
    v = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=v)
    assert sw.host_window() is v  # it docks again, in the new editor window


def test_shortcuts_stay_with_their_canvas(editor):
    from PySide6 import QtCore, QtGui, QtTest, QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    hits = []
    act = QtGui.QAction("probe", w)
    act.setShortcut(QtGui.QKeySequence("Ctrl+Shift+F12"))
    act.triggered.connect(lambda: hits.append("editor"))
    w.addAction(act)
    deletes = []
    w._delete_command = lambda: deletes.append(1)  # the page canvas's '-' key
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    sw = SW.simulate_window(create=False)
    sim_canvas = sw.current().canvas
    keys = []
    sim_canvas.inputEvent.connect(lambda e: keys.append((e.kind, e.key, e.modifiers)))
    w.activateWindow()
    QtTest.QTest.qWaitForWindowActive(w)
    # the editor's canvas: its window shortcut fires
    w.view.setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(w.view, QtCore.Qt.Key_F12, QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
    assert hits == ["editor"]
    # the docked simulated page: the key goes to the simulation, not to the editor
    sim_canvas.setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(sim_canvas, QtCore.Qt.Key_F12, QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
    QtTest.QTest.keyClick(sim_canvas, QtCore.Qt.Key_Minus)
    assert hits == ["editor"] and deletes == []
    assert [k[0] for k in keys] == ["key", "key"] and keys[0][2] == 3
    # floating it is a window of its own: the editor's shortcuts are not in it either
    w._sim_dock.setFloating(True)
    w._sim_dock.activateWindow()
    QtTest.QTest.qWaitForWindowActive(w._sim_dock)
    sim_canvas.setFocus()
    QtTest.QTest.keyClick(sim_canvas, QtCore.Qt.Key_F12, QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
    assert hits == ["editor"]
    # (the application keeps the last key event's modifiers: a later test's selectRow then
    # extended a selection as with Shift held)
    for key in (QtCore.Qt.Key_Shift, QtCore.Qt.Key_Control):
        QtTest.QTest.keyRelease(sim_canvas, key, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.processEvents()
    assert QtGui.QGuiApplication.keyboardModifiers() == QtCore.Qt.NoModifier


def test_closed_while_docked_shows_again(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    sw = SW.simulate_window(create=False)
    sw.close()  # (the window itself, not its dock)
    QtWidgets.QApplication.processEvents()
    assert sw.tabs.count() == 0 and not w._sim_dock.isVisible()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    QtWidgets.QApplication.processEvents()
    assert sw.isVisible() and w._sim_dock.isVisible(), "an empty dock"


def test_a_restored_editor_layout_keeps_the_docked_simulation(editor):
    from PySide6 import QtWidgets
    from ctgedit.ui.simulate import window as SW

    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    QtWidgets.QApplication.processEvents()
    w._sim_dock.hide()
    w._settings().setValue("win/state", w.saveState())  # (a layout stored without it)
    w._sim_dock.show()
    w.restore_session(reopen=False)
    QtWidgets.QApplication.processEvents()
    assert w._sim_dock.isVisible() and not w._sim_dock.isFloating()


HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")
def test_popups_follow_dock_and_pop_out(tmp_path, monkeypatch, editor, fx_page):
    """The popup page is FxSim's !FxSimPop (a small popup of the fake install), copied as
    !Pop.ctg beside the page the fake server runs."""
    import shutil
    import sys

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import fake_ctsim as F
    from ctgedit.sim import client as C
    from ctgedit.ui.simulate import window as SW
    from test_sim_engine_source import _rect_page as rect_page, _sim, wait_for

    monkeypatch.setenv(C.SERVERS_ENV, str(tmp_path / "servers"))
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    main = proj / "FxSim1.ctg"
    main.write_bytes(b"")
    shutil.copyfile(fx_page("!FxSimPop", "FxSim"), proj / "!Pop.ctg")
    app = _app()
    hub = C.Hub(linger=0)
    srv = F.FakeServer(str(proj), str(tmp_path / "servers"), pages=True).start()
    try:
        ed = editor()
        sim = _sim(hub)
        sp = SW.open_simulation(str(main), page=rect_page(), sim=sim, host=ed)
        win = SW.simulate_window(create=False)
        pump = app.processEvents
        assert wait_for(lambda: (win.tick(), sim.page_active())[1], pump=pump)
        srv.open_window(
            3,
            "!Pop",
            str(proj / "!Pop.ctg"),
            parent=sim.window,
            mode=1 + 8 + 32 + 256 + 512,
            x=40,
            y=40,
            w=60,
            h=100,
            title="Pump A",
        )
        assert wait_for(lambda: (win.tick(), len(win.popups()) == 1)[1], timeout=15, pump=pump)
        pop = win.popups()[0]
        for _ in range(4):  # float, dock ... ends docked
            win._dock.setFloating(not win._dock.isFloating())
            pump()
            win._layout_views()
            pump()
            at = sp.canvas.viewport().mapToGlobal(sp.canvas.mapFromScene(40, 40))
            got = pop.frame.geometry().topLeft()
            assert pop.frame.isVisible() and pop.frame.isWindow()
            assert abs(got.x() - at.x()) <= 2 and abs(got.y() - at.y()) <= 2, (got, at)
        assert win.is_docked() and win.host_window() is ed and not win._dock.isFloating()
        # closing the editor window stops the docked simulation, the popup's window included
        ed.doc = None
        ed.close()
        pump()
        assert win.tabs.count() == 0 and not win.popups()
        closed = lambda: [
            a.get("window")
            for _c, cmd, a in list(srv.requests)  # noqa: E731
            if cmd == "ctsim/closePage"
        ]
        assert wait_for(lambda: 3 in closed(), timeout=15, pump=pump)
    finally:
        srv.stop()
        hub.shutdown()


def test_own_window_layout_is_not_applied_before_docking(editor):
    """A Simulate window saved maximized as a window of its own: Qt aborts when that geometry is
    restored into the window before it is ever shown and the window then goes into the
    editor's dock. The own-window layout is restored only when the window shows as a window of
    its own."""
    from PySide6 import QtCore, QtWidgets
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import window as SW

    own = QtWidgets.QMainWindow()
    own.showMaximized()
    QtWidgets.QApplication.processEvents()
    app_settings().setValue("simulate/geometry", own.saveGeometry())
    own.close()
    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    QtWidgets.QApplication.processEvents()
    sw = SW.simulate_window(create=False)
    assert sw.is_docked() and not sw._own_layout_restored
    assert not (sw.windowState() & QtCore.Qt.WindowMaximized)
    # a window of its own: then the saved layout applies
    sw.pop_out()
    QtWidgets.QApplication.processEvents()
    assert not sw.is_docked() and sw._own_layout_restored


def test_the_first_open_is_a_floating_window_of_a_good_size(editor):
    """With no stored layout the Simulate window opens floating, sized to the editor's
    screen; Outputs starts as a strip."""
    from PySide6 import QtWidgets
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import window as SW

    app_settings().remove("simulate/dock_floating")
    app_settings().remove("simulate/docked_state")
    w = editor()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    sw = SW.simulate_window(create=False)
    QtWidgets.QApplication.processEvents()
    assert w._sim_dock.isFloating() and w._sim_dock.widget() is sw
    scr = w.screen().availableGeometry()
    g = w._sim_dock.geometry()
    assert g.width() >= scr.width() * 0.6 and g.height() >= scr.height() * 0.6, (g, scr)
    assert sw.dock_outputs.height() <= sw.dock_outputs.minimumSizeHint().height() + 30
    w._sim_dock.setFloating(False)  # (a floating window left open takes focus)
    QtWidgets.QApplication.processEvents()


def test_docking_never_grows_the_editor_window(editor):
    """The Simulate window's minimum size, docked even for a moment, does not grow the editor
    past the screen."""
    from PySide6 import QtWidgets
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.simulate import window as SW

    app_settings().remove("simulate/dock_floating")
    app_settings().remove("simulate/docked_state")
    w = editor()
    w.resize(900, 420)  # smaller than the Simulate window wants
    QtWidgets.QApplication.processEvents()
    before = w.size()
    SW.open_simulation("", page=_rect_page(), sim=_Sim(), host=w)
    QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()
    assert w.height() <= before.height() and w.width() <= before.width(), (before, w.size())
    w._sim_dock.setFloating(False)
    QtWidgets.QApplication.processEvents()
