"""Settings reach the Simulate window and the keymap."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


def _page():
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind, Page

    p = Page(name="sim_reach")
    p.appearance.width, p.appearance.height = 400, 300
    p.appearance.background_colour = 0xFFFFFF
    o = GraphicObject(
        kind=ObjectKind.RECTANGLE, an=11, geometry=Geometry(x=100, y=100, extent_x=80, extent_y=60)
    )
    o.style.filled, o.style.fill_colour, o.style.line_colour = True, 0x00FF00, 0x00FF00
    o.raw["_type_code"] = 0x0F
    p.objects = [o]
    return p


@pytest.fixture()
def sim():
    """A Simulate window with one tab on a demo simulation: (window, its page)."""
    _app()
    from ctgedit.ui.simulate.sources import FakeSimulation
    from ctgedit.ui.simulate.window import SimulateWindow

    win = SimulateWindow()
    sp = win.open_page("", page=_page(), sim=FakeSimulation(), run=False)
    win.resize(700, 500)
    win.show()
    QtWidgets.QApplication.processEvents()
    yield win, sp
    win.close()
    win.deleteLater()
    QtWidgets.QApplication.processEvents()


def _set(key, value):
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(key, value)


def _status_ms(win):
    """The time the message shown in the window's status bar stays (its timer)."""
    timers = [t for t in win.statusBar().findChildren(QtCore.QTimer) if t.isActive()]
    assert timers, "no message with a time"
    return timers[0].interval()


# ------------------------------------------------------------------ Messages stay
def test_the_simulate_windows_messages_follow_messages_stay(sim):
    from ctgedit.ui.notify import message_ms, reading_ms

    win, _sp = sim
    win.tabs.clear()
    text = "There is no page to copy."
    for choice in ("long", "short"):
        _set("ui/message_time", choice)
        assert not win.copy_image()
        assert win.statusBar().currentMessage() == text
        assert _status_ms(win) == message_ms(text, "info", 4000)
    _set("ui/message_time", "long")
    assert message_ms(text, "info", 4000) > 4000
    _set("ui/message_time", "short")
    assert message_ms(text, "info", 4000) < 4000
    # a line of a simulation's dialog in the status bar too (a warning at least 10 s)
    _set("ui/message_time", "normal")
    said = "The question about user32.dll was not answered in time: denied."
    win.dialogs._say(said, level="warning")
    assert win.statusBar().currentMessage() == said
    assert _status_ms(win) == reading_ms(said, "warning") == 10000


def test_at_normal_no_simulate_message_stays_shorter_than_before(sim):
    """Each message keeps its fixed time as a floor, times the setting: 5 to 8 s for a short
    text or one with a path, not reading_ms's 4 s."""
    from ctgedit.ui.notify import reading_ms

    win, _sp = sim
    _set("ui/message_time", "normal")
    text = "The Cicode simulator does not run this page."
    assert win._sim_tools() is None  # (a demo simulation)
    assert win.statusBar().currentMessage() == text
    assert reading_ms(text) == 4740 and _status_ms(win) == 5000  # (it stayed 5 s)
    win.dialogs._say("Allowed for this run.")
    assert _status_ms(win) == 8000  # (a dialog's line stayed 8 s)
    _set("ui/message_time", "long")
    win.dialogs._say("Allowed for this run.")
    assert _status_ms(win) == 12800
    _set("ui/message_time", "short")
    assert win._sim_tools() is None
    assert _status_ms(win) == 3500
    _set("ui/message_time", "normal")


def test_the_ui_text_check_reads_the_simulate_windows_messages(tmp_path):
    """The messages that go through SimulateWindow.status_message are checked as
    showMessage's are ('(s)', the final full stop)."""
    import sys

    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    sys.path.insert(0, tools)
    import check_ui_text as C

    py = tmp_path / "sample.py"
    py.write_text(
        'self.status_message("Loaded 3 file(s).", at_least_ms=5000)\n'
        'self.status_message(text="No stop here")\n'
        'self.status_message("Loaded %s." % path, at_least_ms=5000)\n',
        encoding="utf-8",
    )
    got = sorted((ln, what.split(":")[0]) for _r, ln, what in C.check_python(str(py)))
    assert got == [(1, C.RULE_PLURAL), (2, C.RULE_STOP)], got
    sim = os.path.join(os.path.dirname(tools), "ctgedit", "ui", "simulate", "window.py")
    assert not [p for p in C.check_python(sim) if "message" in p[2]]


def test_show_on_page_marks_stay_as_long_as_messages_stay_says(sim):
    _win, sp = sim
    cv = sp.canvas
    _set("ui/message_time", "long")
    assert cv.show_object(11)
    assert cv._shown_timer.interval() == 6400  # 4 s x 1.6
    assert cv.show_objects([11]) == 1
    assert cv._shown_timer.interval() == 9600  # 6 s x 1.6
    _set("ui/message_time", "short")
    assert cv.show_object(11)
    assert cv._shown_timer.interval() == 2800


# ------------------------------------------------------------------ Selection colour, outlines
def _mark_row(cv):
    """The colours across the left edge of the mark round object 11 at 100 %."""
    cv.set_zoom(1.0)
    cv.centerOn(140, 130)
    QtWidgets.QApplication.processEvents()
    assert cv.show_object(11)
    QtWidgets.QApplication.processEvents()
    img = cv.viewport().grab().toImage()
    at = cv.mapFromScene(QtCore.QPointF(100, 130))
    return [img.pixelColor(at.x() + dx, at.y()) for dx in range(-9, 1)]


def test_the_show_on_page_mark_takes_selection_colour_and_selection_outlines(sim):
    from ctgedit.ui.style import theme
    from ctgedit.ui.editor.canvas_look import set_canvas_lines

    _win, sp = sim
    _set("view/selection_colour", "#ff00ff")
    theme.reload_canvas_colours()
    try:
        widths = {}
        for outline in ("thin", "thick"):
            set_canvas_lines(outline=outline)
            row = _mark_row(sp.canvas)
            magenta = [c for c in row if (c.red(), c.green(), c.blue()) == (255, 0, 255)]
            assert magenta, [c.getRgb() for c in row]
            widths[outline] = len(magenta)
        assert widths["thin"] == 3 and widths["thick"] == 5
    finally:
        set_canvas_lines()
        _set("view/selection_colour", "accent")
        theme.reload_canvas_colours()


# ------------------------------------------------------------------ Scroll bars, glide
def test_the_simulate_canvas_has_the_canvas_scroll_bars(sim):
    win, sp = sim
    cv = sp.canvas
    # the default: thin bars over the page, the view's own ones off
    assert cv.overlay_bars.mode == "auto"
    assert cv.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    assert cv.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    _set("view/canvas_scrollbars", "always")
    win.apply_settings()
    assert cv.overlay_bars.mode == "always"
    assert cv.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAsNeeded
    _set("view/canvas_scrollbars", "never")
    win.apply_settings()
    assert cv.overlay_bars.mode == "never"
    assert cv.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    # a window drawn inside another or as a popup has none, whatever the setting
    from ctgedit.ui.simulate.canvas import SimCanvas

    _set("view/canvas_scrollbars", "always")
    inner = SimCanvas(sp.scene, sp)
    try:
        inner.set_fixed_view(QtCore.QRectF(0, 0, 100, 80), 1.0, 1.0)
        inner.apply_canvas_options()
        assert inner.overlay_bars.mode == "never"
        assert inner.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    finally:
        inner.setParent(None)
        inner.deleteLater()


def test_show_on_page_glides_when_it_zooms_out_to_its_marks(sim, monkeypatch):
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.delenv("CTG_NO_ANIMATION", raising=False)
    monkeypatch.setattr(VN, "windows_animations_on", lambda: True)
    _win, sp = sim
    cv = sp.canvas
    cv.set_zoom(12.0)  # (the object is far bigger than the view)
    assert cv.show_objects([11]) == 1
    assert cv.view_animating()
    assert cv.zoom() == pytest.approx(12.0)  # (the glide starts where the view was)
    for _ in range(150):
        QtTest.QTest.qWait(20)
        if not cv.view_animating():
            break
    assert not cv.view_animating()
    target = cv.zoom()
    assert target < 12.0
    # Animate zoom changes off: it goes there at once
    _set("opt/animate_zoom", False)
    cv.set_zoom(12.0)
    cv.show_objects([11])
    assert not cv.view_animating()
    assert cv.zoom() == pytest.approx(target)


# ------------------------------------------------------------------ Overlay opacity
def _paused_sim():
    """A demo simulation the debugger holds in page code."""
    from ctgedit.ui.simulate.sources import FakeSimulation

    class Paused(FakeSimulation):
        def debug_paused(self):
            return {"paused": True, "function": "fillLevel", "file": "Genie.ci", "line": 18}

    return Paused()


def test_the_notes_over_the_simulated_page_follow_overlay_opacity(sim):
    win, sp = sim
    _set("ui/overlay_opacity", 50)
    sp.show_reloading(True)
    sp.show_missing_panel(True, "No simulator", lambda: None, lambda: None)
    real, sp.sim = sp.sim, _paused_sim()
    sp._show_debug_pause()
    sp.sim = real
    notes = (sp._reload_overlay, sp._missing, sp._pause_overlay)
    for w in notes:
        # (the alpha of the navigation bar's surface at 50 %)
        assert ", %d);" % round(50 * 2.55) in w.styleSheet(), w.objectName()
    _set("ui/overlay_opacity", 80)
    win.apply_settings()
    for w in notes:
        assert ", %d);" % round(80 * 2.55) in w.styleSheet(), w.objectName()
    sp.show_reloading(False)


# ------------------------------------------------------------------ Delete's minus keys
@pytest.fixture()
def editor():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.deleteLater()
    QtWidgets.QApplication.processEvents()


def _canvas_keys(w):
    return {
        sc.key().toString(): sc
        for sc in w.view.findChildren(QtGui.QShortcut)
        if sc.isEnabled() and not sc.key().isEmpty()
    }


def test_the_minus_keys_are_keys_of_delete_in_the_keymap(editor):
    from ctgedit.ui.prefs import keymap

    w = editor
    # (not by default: the minus keys zoom out; Settings > Keyboard gives them)
    eff, _n = keymap.resolve(w)
    assert eff["act_del"][0] == ["Del"] and "-" not in _canvas_keys(w)
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(w)
    eff, _n = keymap.resolve(w)
    assert eff["act_del"][0] == ["Del", "-", "Num+-"]  # (Settings > Keyboard lists them)
    # Del works in the whole window (the Objects panel too), the minus keys on the canvas only
    assert [s.toString() for s in w.act_del.shortcuts()] == ["Del"]
    assert w.act_del.shortcutContext() == QtCore.Qt.WindowShortcut
    keys = _canvas_keys(w)
    for k in ("-", "Num+-"):
        assert keys[k].context() == QtCore.Qt.WidgetWithChildrenShortcut
    assert "-" not in keymap.fixed_keys(w)
    ran = []
    w.act_del.triggered.connect(lambda *_a: ran.append(1))
    w.act_del.setEnabled(True)
    keys["-"].activated.emit()
    assert ran == [1]
    assert keymap.shown_keys(w.act_del) == ["Del", "-", "Num+-"]
    # each can be taken away, and given to another command
    keymap.store_user_keys({"act_del": ["Del", "Num+-"]})
    keymap.apply_keymap(w)
    assert "-" not in _canvas_keys(w) and "Num+-" in _canvas_keys(w)
    assert keymap.resolve(w)[0]["act_del"][0] == ["Del", "Num+-"]
    assert keymap.conflicts(w, "act_group", ["-"]) == []
    keymap.store_user_keys({"act_del": ["Del", "Num+-"], "act_group": ["-"]})
    keymap.apply_keymap(w)
    assert [s.toString() for s in w.act_group.shortcuts()] == ["-"]
    keymap.store_user_keys({})
    keymap.apply_keymap(w)
    assert "-" not in _canvas_keys(w) and keymap.resolve(w)[0]["act_del"][0] == ["Del"]


def _shown_editor(w):
    w.resize(900, 600)
    w.show()
    w.activateWindow()
    QtTest.QTest.qWaitForWindowActive(w, 2000)
    w.view.setFocus()
    QtWidgets.QApplication.processEvents()


def _presses(w, key, mods=QtCore.Qt.NoModifier):
    """How often Edit > Delete runs for ``key`` pressed on the canvas."""
    ran = []

    def hit(*_a):
        ran.append(1)

    w.act_del.setEnabled(True)
    w.act_del.triggered.connect(hit)
    try:
        QtTest.QTest.keyClick(w.view, key, mods)
        QtWidgets.QApplication.processEvents()
    finally:
        w.act_del.triggered.disconnect(hit)
    return len(ran)


def test_the_keypad_minus_removed_from_delete_stops_deleting(editor):
    """Removing only Num - from Delete stops the keypad minus deleting (Qt runs the '-'
    shortcut for a keypad minus that has none of its own)."""
    from ctgedit.ui.prefs import keymap

    w = editor
    _shown_editor(w)
    pad = QtCore.Qt.KeypadModifier
    # by default neither minus deletes (the keypad minus zooms out)
    assert _presses(w, QtCore.Qt.Key_Minus) == 0
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 0
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(w)
    assert _presses(w, QtCore.Qt.Key_Minus) == 1
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 1
    keymap.store_user_keys({"act_del": ["Del", "-"]})
    keymap.apply_keymap(w)
    assert _presses(w, QtCore.Qt.Key_Minus) == 1
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 0
    # the keypad minus stays free: another command can have it, and then it runs that one
    assert "Num+-" not in keymap.fixed_keys(w)
    assert keymap.conflicts(w, "act_group", ["Num+-"]) == []
    keymap.store_user_keys({"act_del": ["Del", "-"], "act_group": ["Num+-"]})
    keymap.apply_keymap(w)
    grouped = []
    w.act_group.setEnabled(True)
    w.act_group.triggered.connect(lambda *_a: grouped.append(1))
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 0 and grouped == [1]
    # '-' taken away: the keypad minus still deletes, '-' does not
    keymap.store_user_keys({"act_del": ["Del", "Num+-"]})
    keymap.apply_keymap(w)
    assert _presses(w, QtCore.Qt.Key_Minus) == 0
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 1
    keymap.store_user_keys({})
    keymap.apply_keymap(w)
    assert _presses(w, QtCore.Qt.Key_Minus, pad) == 0


def test_a_delete_key_list_is_stored_as_it_is(editor):
    """Delete's keys are stored as given (the minus keys are no defaults, so leaving them out
    needs no mark), in the settings and in an exported file."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.settings import app_settings

    w = editor
    st = app_settings()
    keymap.store_user_keys({"act_del": ["Del", "Ctrl+D"]})
    assert sorted(st.value("keys/act_del")) == sorted(["Del", "Ctrl+D"])
    assert keymap.user_keys()["act_del"] == ["Del", "Ctrl+D"]
    keymap.apply_keymap(w)
    assert "-" not in _canvas_keys(w)
    keymap.store_user_keys({"act_del": []})
    assert keymap.user_keys()["act_del"] == []
    keymap.store_user_keys({"act_del": ["Del", "-"]})
    assert keymap.user_keys()["act_del"] == ["Del", "-"]
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "keys.json")
        keymap.export_keys(path, "ctgedit", {"act_del": ["Del"]})
        assert keymap.read_keys_file(path)[1]["act_del"] == ["Del"]
    keymap.store_user_keys({})


def test_settings_keyboard_says_where_each_key_of_delete_works(editor):
    """The canvas keys' 'Edit > Delete's keys' row is not listed as Fixed (keys no command can
    take), and Delete's Where does not say Window for its canvas-only minus keys."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.dialog.help_window import CANVAS_KEYS, shortcut_reference_html
    from ctgedit.ui.prefs.keymap_page import ID_ROLE, KeymapPage
    from ctgedit.ui.main_window_base import DESCRIPTIONS

    w = editor
    keymap.store_user_keys({"act_del": ["Del", "-", "Num+-"]})
    keymap.apply_keymap(w)
    assert not any("Delete" in k or "Delete" in what for k, what in CANVAS_KEYS)
    assert "nodes" in DESCRIPTIONS["act_del"]  # (the node rule is the command's)
    page = KeymapPage(w)
    try:
        rows = {page.table.item(r, 0).data(ID_ROLE): r for r in range(page.table.rowCount())}
        fixed = [
            page.table.item(r, 0).text() for r in rows.values() if page.table.item(r, 4).text() == "Fixed"
        ]
        assert fixed and not any("Delete" in t or "delete" in t for t in fixed), fixed
        r = rows["act_del"]
        note = "Del works in the whole window, - and Num+- on the canvas only."
        assert page.table.item(r, 3).text() == "Window"
        assert page.table.item(r, 3).toolTip() == note
        assert page.table.item(r, 2).toolTip() == "Del, -, Num+-\n" + note
        # only the minus keys left: they work on the canvas only, and the row says so
        page.set_state(keymap.DEFAULT_PRESET, {"act_del": ["-", "Num+-"]}, emit=False)
        r = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).data(ID_ROLE) == "act_del")
        assert page.table.item(r, 3).text() == "Canvas"
        assert page.table.item(r, 3).toolTip() == ""
        # a command whose keys all work in one place has no note
        r = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).data(ID_ROLE) == "act_dup")
        assert page.table.item(r, 2).toolTip() == "Ctrl+D"
    finally:
        page.setParent(None)
        page.deleteLater()
    # Help > Keyboard Shortcuts lists Delete's three keys under Edit, no row of its own
    text = shortcut_reference_html(w.menuBar())
    assert "Del, -, Num+-" in text and "Edit &gt; Delete" not in text
