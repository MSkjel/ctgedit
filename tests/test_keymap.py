"""The keyboard map."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.deleteLater()


def _keys(a):
    return [s.toString() for s in a.shortcuts()]


def _all_keys(w):
    """{key: {owners}} of every action of the window and its canvas (one scope: the canvas
    keys work while the window's do)."""
    owners = {}
    for a in w.findChildren(QtGui.QAction):
        if a.shortcutContext() not in (QtCore.Qt.WindowShortcut, QtCore.Qt.WidgetWithChildrenShortcut):
            continue
        if not (a in w.actions() or a in w.view.actions()):
            continue
        for s in a.shortcuts():
            owners.setdefault(s.toString(), set()).add(a)
    for sc in w.findChildren(QtGui.QShortcut):
        k = sc.key().toString()
        if k:
            owners.setdefault(k, set()).add(sc)
    return owners


def test_the_presets_and_no_key_twice(win):
    from ctgedit.ui.prefs import keymap

    assert set(keymap.presets()) >= {"ctgedit", "builder", "design"}
    for preset in ("ctgedit", "builder", "design"):
        keymap.set_preset(preset)
        keymap.apply_keymap(win)
        both = {
            k: sorted(getattr(o, "text", lambda: "shortcut")() for o in v)
            for k, v in _all_keys(win).items()
            if len(v) > 1
        }
        assert not both, (preset, both)
    keymap.set_preset("ctgedit")


def test_switching_to_the_graphics_builder_keymap_needs_no_restart(win):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.settings import app_settings

    assert _keys(win.tool_actions["symbol"]) == ["A"]
    assert "S" in _keys(win.act_palette_here) and "Shift+S" in _keys(win.act_palette_here)
    keymap.set_preset("builder")
    keymap.apply_everywhere()
    assert _keys(win.tool_actions["symbol"]) == ["S"]
    assert _keys(win.act_palette_here) == ["Shift+S"]
    # the palette key of the canvas works on the canvas only
    assert win.act_palette_here.shortcutContext() == QtCore.Qt.WidgetWithChildrenShortcut
    assert win.act_palette_here in win.view.actions()
    keymap.set_preset("design")
    keymap.apply_everywhere()
    assert _keys(win.act_group) == ["Ctrl+G"] and _keys(win.act_goto) == ["Ctrl+Alt+G"]
    assert _keys(win.act_ungroup)[0] == "Ctrl+Shift+G"
    assert app_settings().value("opt/shift_arrow") == "nudge10"
    keymap.set_preset("ctgedit")
    keymap.apply_everywhere()
    assert _keys(win.act_goto) == ["Ctrl+G"] and _keys(win.act_group) == ["Ctrl+Shift+G"]


def test_export_reset_all_import_gives_the_same_keys(win, tmp_path):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.keymap_page import KeymapPage

    user = keymap.take_keys(win, "act_group", ["Ctrl+Alt+Shift+G"], {})
    user["act_genie_params"] = ["Ctrl+Alt+P"]
    keymap.store_user_keys(user)
    keymap.apply_keymap(win)
    before = {c.id: _keys(c.action) for c in keymap.commands(win)}
    assert _keys(win.act_genie_params) == ["Ctrl+Alt+P"]
    page = KeymapPage(win, immediate=True)
    path = str(tmp_path / "keys.json")
    assert page.export_to(path)
    page.reset_all()
    assert keymap.user_keys() == {} and _keys(win.act_genie_params) == []
    assert page.import_from(path)
    assert {c.id: _keys(c.action) for c in keymap.commands(win)} == before
    # a file that is no keyboard export changes nothing
    bad = tmp_path / "bad.json"
    bad.write_text('{"x": 1}')
    with pytest.raises(keymap.KeysFileError):
        keymap.read_keys_file(str(bad))


def test_a_key_another_command_has_is_named_and_can_be_taken(win):
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.keymap_page import open_change_shortcut
    from ctgedit.ui.style.theme import DARK

    got = []
    dlg = open_change_shortcut(win, "act_group", window=win, on_done=got.append)
    dlg.key1.setKeySequence(QtGui.QKeySequence("Ctrl+D"))
    assert dlg.conflict.text() == "Ctrl+D is already Duplicate."
    assert DARK["danger"] in dlg.conflict.styleSheet()
    assert not dlg._ok_button.isEnabled() and dlg.use_anyway.isVisibleTo(dlg)
    dlg.use_anyway.click()
    assert got and got[0]["act_group"] == ["Ctrl+D"] and got[0]["act_dup"] == []
    keymap.store_user_keys(got[0])
    keymap.apply_keymap(win)
    assert _keys(win.act_group) == ["Ctrl+D"] and _keys(win.act_dup) == []
    # a key of the canvas no command can take is refused (no Use Anyway)
    dlg = open_change_shortcut(win, "act_group", window=win, on_done=got.append)
    dlg.key1.setKeySequence(QtGui.QKeySequence("Left"))
    assert "is a key of the canvas" in dlg.conflict.text()
    assert not dlg.use_anyway.isVisibleTo(dlg) and not dlg._ok_button.isEnabled()
    dlg.reject()
    # Remove and Reset
    dlg = open_change_shortcut(win, "act_group", window=win, on_done=got.append)
    dlg.reset.click()
    assert "act_group" not in got[-1]


def test_a_user_key_that_takes_the_palette_key_says_so(win):
    from ctgedit.ui.prefs import keymap

    keymap.store_user_keys({"act_genie_params": ["S"]})
    notes = keymap.apply_keymap(win)
    assert ("S", "act_genie_params", "act_palette_here") in notes
    assert "S" not in _keys(win.act_palette_here)
    assert "instead of the command palette" in win.statusBar().currentMessage()
    # (a bare key: the command works on the canvas, not from the lists)
    assert win.act_genie_params in win.view.actions()
    keymap.store_user_keys({})
    keymap.apply_keymap(win)
    assert "S" in _keys(win.act_palette_here)
    assert win.act_genie_params not in win.view.actions()


def test_a_panels_own_key_stays_free_for_a_tool_letter(win):
    """Only overlapping scopes conflict: a key of a panel's list never fires with the
    canvas, so a tool letter keeps it; a window command cannot take it."""
    from ctgedit.ui.prefs import keymap

    tree = win.objects_dock.findChildren(QtWidgets.QTreeWidget)[0]
    sc = QtGui.QShortcut(QtGui.QKeySequence("F"), tree)
    sc.setContext(QtCore.Qt.WidgetShortcut)
    try:
        keymap.apply_keymap(win)
        assert _keys(win.tool_actions["freehand"]) == ["F"]
        assert keymap.conflicts(win, "tool.freehand", ["F"]) == []
        assert keymap.scope_for(win.act_group, ["F", "Ctrl+Alt+F"]) == keymap.WINDOW
        assert keymap.conflicts(win, "act_group", ["F", "Ctrl+Alt+F"]) == [("F", keymap.FIXED_PANEL)]
        # the canvas' own keys are fixed for every command
        assert keymap.conflicts(win, "tool.freehand", ["Left"]) == [("Left", "")]
    finally:
        sc.setParent(None)
        sc.deleteLater()


def test_the_main_window_applies_its_keymap(win):
    from ctgedit.ui.prefs import keymap

    keymap.store_user_keys({"act_obj_props": ["Ctrl+Alt+Return"]})
    win.apply_keymap()
    assert _keys(win.act_obj_props) == ["Ctrl+Alt+Return"]


def test_the_keyboard_page_searches_and_records_keys(win):
    from ctgedit.ui.prefs.keymap_page import KeymapPage

    page = KeymapPage(win)
    page.bar.search.setText("dup")
    page.bar.search.flush()
    shown = page.shown_ids()
    assert "act_dup" in shown and "act_undo" not in shown
    page.bar.search.setText("")
    page.bar.record.setChecked(True)
    QtTest.QTest.keyClick(page.bar.search, QtCore.Qt.Key_D, QtCore.Qt.ControlModifier)
    assert page.shown_ids() == ["act_dup"]
    QtTest.QTest.keyClick(page.bar.search, QtCore.Qt.Key_Escape)
    assert not page.bar.record.isChecked()
    # the columns, the canvas keys listed as fixed
    assert [page.table.horizontalHeaderItem(k).text() for k in range(5)] == [
        "Command",
        "Menu",
        "Keys",
        "Where",
        "Source",
    ]
    fixed = [r for r in range(page.table.rowCount()) if page.table.item(r, 4).text() == "Fixed"]
    assert fixed
    r = next(
        r
        for r in range(page.table.rowCount())
        if page.table.item(r, 0).data(QtCore.Qt.UserRole + 2) == "tool.rectangle"
    )
    assert page.table.item(r, 3).text() == "Canvas"


def test_a_dialogs_own_keys_take_no_key_from_the_window(win):
    """The QShortcuts of a dialog (Settings' Ctrl+F, the Find window's F3,
    the Bitmap Editor's Ctrl+Z) work in that dialog only; they never strip a key from the
    window's commands, open or closed."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    settings = SettingsDialog(win)
    settings.show()
    other = QtWidgets.QDialog(win)
    for key, ctx in (
        ("F3", QtCore.Qt.WindowShortcut),
        ("Ctrl+Z", QtCore.Qt.WindowShortcut),
        ("Ctrl+Y", QtCore.Qt.WidgetWithChildrenShortcut),
    ):
        sc = QtGui.QShortcut(QtGui.QKeySequence(key), other)
        sc.setContext(ctx)
    other.show()
    QtWidgets.QApplication.processEvents()
    try:
        assert "Ctrl+F" not in keymap.fixed_keys(win)
        keymap.apply_keymap(win)
        assert _keys(win.act_find) == ["Ctrl+F"]
        assert _keys(win.act_show_tools) == ["F3"]
        assert _keys(win.act_undo) == ["Ctrl+Z"]
        assert _keys(win.act_redo) == ["Ctrl+Y", "Ctrl+Shift+Z"]
        assert keymap.conflicts(win, "act_find", ["Ctrl+F"]) == []
        settings.close_live()
        other.hide()
        keymap.apply_keymap(win)
        assert _keys(win.act_find) == ["Ctrl+F"] and _keys(win.act_undo) == ["Ctrl+Z"]
    finally:
        other.deleteLater()
        settings.deleteLater()


def test_a_built_in_bare_key_keeps_its_scope(win):
    """Edit > Delete's Del stays a key of the whole window (it deletes
    from the Objects panel too); a tool letter and a bare key the user gives works on the
    canvas only."""
    from ctgedit.ui.prefs import keymap

    keymap.apply_keymap(win)
    assert _keys(win.act_del) == ["Del"]
    assert keymap.scope_for(win.act_del, ["Del"]) == keymap.WINDOW
    assert win.act_del.shortcutContext() == QtCore.Qt.WindowShortcut
    assert win.act_del in win.actions() and win.act_del not in win.view.actions()
    assert keymap.scope_for(win.tool_actions["rectangle"], ["R"]) == keymap.CANVAS
    assert keymap.scope_for(win.act_del, ["D"]) == keymap.CANVAS
    keymap.store_user_keys({"act_del": ["Ctrl+Alt+Del"]})
    keymap.apply_keymap(win)
    assert win.act_del in win.actions()
    keymap.store_user_keys({})
    keymap.apply_keymap(win)
    assert _keys(win.act_del) == ["Del"]
    assert win.act_del.shortcutContext() == QtCore.Qt.WindowShortcut


def test_two_change_shortcut_windows_keep_both_changes(win):
    """Change Shortcut waits for its answer (window modal), and what it
    changed is put onto the keys as they are when it closes."""
    from ctgedit.ui.prefs.keymap_page import KeymapPage

    page = KeymapPage(win)
    first = page.change("act_group")
    assert first.windowModality() == QtCore.Qt.WindowModal
    second = page.change("act_dup")
    second.key1.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+D"))
    second._ok()
    assert page.state()[1]["act_dup"] == ["Ctrl+Alt+D"]
    first.key1.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+G"))
    first._ok()
    user = page.state()[1]
    assert user["act_group"] == ["Ctrl+Alt+G"] and user["act_dup"] == ["Ctrl+Alt+D"]
    page.deleteLater()


def test_key_caps_of_a_selected_row_keep_the_text_colour():
    """The caps of a selected row are drawn with the text colour on their
    own face (white on a light cap could not be read)."""
    _app()
    from ctgedit.ui.prefs.keymap_page import KeyCapDelegate
    from ctgedit.ui.style.theme import COLORS

    _edge, face, text = KeyCapDelegate.cap_colours(True)
    assert text == QtGui.QColor(COLORS["text"])
    assert face == KeyCapDelegate.cap_colours(False)[1]


def test_change_shortcut_shows_every_key_and_keeps_the_ones_not_changed(win):
    """Zoom In has four keys; Change Shortcut shows all of them, OK
    without a change stores no key of your own, and changing one key keeps the others."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.keymap_page import ChangeShortcutDialog, open_change_shortcut

    four = keymap.keys_of(win.act_zoom_in)
    assert len(four) == 4
    dlg = ChangeShortcutDialog(win, win, "act_zoom_in")
    assert [keymap.norm(f.keySequence()) for f in dlg.fields] == four
    dlg._ok()
    assert dlg.result_user == {}
    # through the stored path: nothing changes
    dlg = open_change_shortcut(win, "act_zoom_in", window=win)
    dlg._ok()
    QtWidgets.QApplication.processEvents()
    assert keymap.user_keys() == {} and keymap.keys_of(win.act_zoom_in) == four
    # one key changed: the other three stay
    dlg = ChangeShortcutDialog(win, win, "act_zoom_in")
    dlg.key1.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+="))
    dlg._ok()
    assert dlg.result_user == {"act_zoom_in": ["Ctrl+Alt+="] + four[1:]}
    # Add Key: one more field once every field has a key
    dlg = ChangeShortcutDialog(win, win, "act_group")
    assert len(dlg.fields) == 2 and not dlg.add_key.isEnabled()
    dlg.key1.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+F10"))
    dlg.key2.setKeySequence(QtGui.QKeySequence("Ctrl+Alt+F11"))
    assert dlg.add_key.isEnabled()
    dlg.add_key.click()
    assert len(dlg.fields) == 3
    dlg.fields[2].setKeySequence(QtGui.QKeySequence("Ctrl+Alt+F12"))
    dlg._ok()
    assert dlg.result_user["act_group"] == ["Ctrl+Alt+F10", "Ctrl+Alt+F11", "Ctrl+Alt+F12"]


def test_enter_and_esc_in_a_key_field_are_ok_and_cancel(win):
    """A key field records any key, but plain Enter is OK and plain Esc is
    Cancel there; a key with Shift, Ctrl or Alt is still recorded."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.keymap_page import open_change_shortcut

    win.show()
    for key, mods, accepted in (
        (QtCore.Qt.Key_Return, QtCore.Qt.NoModifier, True),
        (QtCore.Qt.Key_Enter, QtCore.Qt.KeypadModifier, True),
        (QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier, False),
    ):
        got, finished = [], []
        dlg = open_change_shortcut(win, "act_grid", window=win, on_done=got.append)
        dlg.finished.connect(finished.append)
        QtTest.QTest.qWaitForWindowActive(dlg, 1000)
        QtTest.QTest.keyClick(dlg.key1, QtCore.Qt.Key_G, QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
        assert keymap.norm(dlg.key1.keySequence()) == "Ctrl+Alt+G"
        QtTest.QTest.keyClick(dlg.key1, key, mods)
        if accepted:
            assert finished == [QtWidgets.QDialog.Accepted]
            assert got == [{"act_grid": ["Ctrl+Alt+G"]}]
        else:
            assert finished == [QtWidgets.QDialog.Rejected] and got == []
        QtWidgets.QApplication.processEvents()
    dlg = open_change_shortcut(win, "act_grid", window=win, on_done=lambda _u: None)
    QtTest.QTest.qWaitForWindowActive(dlg, 1000)
    QtTest.QTest.keyClick(dlg.key1, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
    assert keymap.norm(dlg.key1.keySequence()) == "Shift+Return" and dlg.isVisible()
    dlg.reject()
    win.hide()


def test_the_keyboard_page_never_changes_a_command_the_search_hides(win):
    """Settings > Keyboard: a selected row the search hides is deselected, and Change and
    Reset are off until a shown row is picked."""
    from ctgedit.ui.prefs.keymap_page import KeymapPage

    page = KeymapPage(win)
    page.bar.search.setText("duplicate")
    page.bar.search.flush()
    ids = page.shown_ids()
    assert ids and page.select(ids[0]) and page.btn_change.isEnabled()
    page.bar.search.setText("zznomatch99")
    page.bar.search.flush()
    assert page.current_id() == "" and not page.btn_change.isEnabled() and not page.btn_reset.isEnabled()
    assert not page.select(ids[0])
