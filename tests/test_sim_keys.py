"""Keys on a simulated page."""

from test_simulate_tab import _Clock, _app, _rect_page


def _page():
    _app()
    from ctgedit.ui.simulate import window as W
    from ctgedit.ui.simulate.sources import FakeSimulation

    return W.SimulatedPage("", page=_rect_page(), sim=FakeSimulation(clock=_Clock()))


def _override(canvas, key, mods, text=""):
    from PySide6 import QtCore, QtGui

    ev = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, key, mods, text)
    ev.ignore()
    canvas.event(ev)
    return ev.isAccepted()


def test_the_page_takes_ctrl_combinations_but_not_the_windows_shortcuts():
    from PySide6 import QtCore

    sp = _page()
    try:
        c = sp.canvas
        Ctrl, Shift, Alt = (QtCore.Qt.ControlModifier, QtCore.Qt.ShiftModifier, QtCore.Qt.AltModifier)
        assert _override(c, QtCore.Qt.Key_S, Ctrl, "\x13")  # a project's KEY_S_CTRL
        assert _override(c, QtCore.Qt.Key_Z, Ctrl, "\x1a")
        assert _override(c, QtCore.Qt.Key_F, Alt, "f")
        assert _override(c, QtCore.Qt.Key_F, Shift, "F")
        assert _override(c, QtCore.Qt.Key_F1, QtCore.Qt.NoModifier)
        # Open Page, Inspector, Copy Image stay the window's
        assert not _override(c, QtCore.Qt.Key_O, Ctrl, "\x0f")
        assert not _override(c, QtCore.Qt.Key_I, Ctrl, "\x09")
        assert not _override(c, QtCore.Qt.Key_C, Ctrl | Shift, "\x03")
    finally:
        sp.stop()
        sp.deleteLater()


def test_a_key_press_sends_its_name_text_and_modifiers():
    from PySide6 import QtCore, QtGui

    sp = _page()
    try:
        got = []
        sp.canvas.inputEvent.connect(got.append)
        sp.canvas.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier, "\x13")
        )
        sp.canvas.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F, QtCore.Qt.ShiftModifier, "F")
        )
        assert [(e.kind, e.key, e.text, e.modifiers) for e in got] == [
            ("key", "S", "\x13", 2),
            ("key", "F", "F", 1),
        ]
    finally:
        sp.stop()
        sp.deleteLater()


def _mouse(kind, button, pos):
    from PySide6 import QtCore, QtGui

    t = {
        "press": QtCore.QEvent.MouseButtonPress,
        "release": QtCore.QEvent.MouseButtonRelease,
        "move": QtCore.QEvent.MouseMove,
        "double": QtCore.QEvent.MouseButtonDblClick,
    }[kind]
    p = QtCore.QPointF(*pos)
    held = QtCore.Qt.NoButton if kind == "release" else button
    return QtGui.QMouseEvent(
        t, p, p, button if kind != "move" else QtCore.Qt.NoButton, held, QtCore.Qt.NoModifier
    )


def test_a_middle_click_is_a_click_and_a_middle_drag_pans():
    """Mouse buttons are keys: the page's MBUTTON_UP / _DN commands need the middle button,
    which pans the view; a middle press that does not move is a click (down, up)."""
    from PySide6 import QtCore

    sp = _page()
    try:
        got = []
        sp.canvas.inputEvent.connect(got.append)
        M = QtCore.Qt.MiddleButton
        sp.canvas.mousePressEvent(_mouse("press", M, (50, 50)))
        sp.canvas.mouseReleaseEvent(_mouse("release", M, (51, 50)))
        assert [(e.kind, e.button) for e in got] == [("down", "middle"), ("up", "middle")]
        got.clear()
        sp.canvas.mousePressEvent(_mouse("press", M, (50, 50)))
        sp.canvas.mouseMoveEvent(_mouse("move", M, (90, 70)))
        sp.canvas.mouseReleaseEvent(_mouse("release", M, (90, 70)))
        assert not [e for e in got if e.kind in ("down", "up")]  # (it panned)
    finally:
        sp.stop()
        sp.deleteLater()


def test_a_right_double_click_is_sent():
    from PySide6 import QtCore

    sp = _page()
    try:
        got = []
        sp.canvas.inputEvent.connect(got.append)
        sp.canvas.mouseDoubleClickEvent(_mouse("double", QtCore.Qt.RightButton, (50, 50)))
        assert [(e.kind, e.button) for e in got] == [("double", "right")]
    finally:
        sp.stop()
        sp.deleteLater()


def test_page_keys_off_leaves_the_keys_to_ctgedit():
    """Page Keys (the Simulate toolbar) off: no key goes to the simulation and the window's
    shortcuts get every key."""
    from PySide6 import QtCore, QtGui
    from ctgedit.ui.simulate.canvas import SimCanvas

    sp = _page()
    was = SimCanvas.keys_to_page
    try:
        got = []
        sp.canvas.inputEvent.connect(got.append)
        SimCanvas.keys_to_page = False
        assert not _override(sp.canvas, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier, "\x13")
        assert not _override(sp.canvas, QtCore.Qt.Key_F1, QtCore.Qt.NoModifier)
        sp.canvas.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F, QtCore.Qt.NoModifier, "f")
        )
        assert got == []
        SimCanvas.keys_to_page = True
        assert _override(sp.canvas, QtCore.Qt.Key_S, QtCore.Qt.ControlModifier, "\x13")
    finally:
        SimCanvas.keys_to_page = was
        sp.stop()
        sp.deleteLater()


def test_the_toolbar_toggle_sets_page_keys_and_remembers_it():
    from ctgedit.ui.simulate import options
    from ctgedit.ui.simulate.canvas import SimCanvas
    from ctgedit.ui.simulate.window import SimulateWindow

    _app()
    win = SimulateWindow()
    try:
        assert win.clock.act_page_keys.isChecked() == SimCanvas.keys_to_page
        win.clock.act_page_keys.setChecked(False)
        assert SimCanvas.keys_to_page is False and options.get("keys_to_page") is False
        win.clock.act_page_keys.setChecked(True)
        assert SimCanvas.keys_to_page is True and options.get("keys_to_page") is True
    finally:
        win.close()
        win.deleteLater()
