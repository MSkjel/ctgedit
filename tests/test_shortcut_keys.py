"""Keyboard shortcuts."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the page: FxMain's FxShapes in the fake install (one of each primitive, all single
#: records at the top level; FxLine is a line the Del tests delete)
PAGE = "FxShapes"

Qt = QtCore.Qt


def _window():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.activateWindow()
    QtTest.QTest.qWaitForWindowActive(w, 2000)
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for dlg in list(getattr(w, "_dialogs", {}).values()):
        dlg.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


@pytest.fixture()
def bare():
    w = _window()
    yield w
    _close(w)


@pytest.fixture()
def win(fx_page):
    dst = fx_page(PAGE)
    w = _window()
    w.open_document(str(dst))
    w.view.setFocus()
    QtWidgets.QApplication.processEvents()
    yield w
    _close(w)


def _rects(win, *rects):
    idx = [win.doc.create_object("rectangle", rect=r, colour=0x336699) for r in rects]
    win._refresh()
    win._set_selection(list(idx))
    return idx


def _box(doc, i):
    return G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)


def _snap(doc):
    return [bytes(po.body_raw) for po in doc.parsed.page.objects]


def _key(w, key, mods=Qt.NoModifier):
    w.view.setFocus()
    QtTest.QTest.keyClick(w.view, key, mods)
    QtWidgets.QApplication.processEvents()


def _undo_label(doc):
    return doc._undo[-1].label if doc.can_undo() else ""


# ------------------------------------------------------------------ every layout
def test_ctrl_plus_and_ctrl_keypad_plus_zoom_in(win):
    for mods in (Qt.ControlModifier, Qt.ControlModifier | Qt.KeypadModifier):
        z = win.view.transform().m11()
        _key(win, Qt.Key_Plus, mods)
        assert win.view.transform().m11() > z, mods
    z = win.view.transform().m11()
    _key(win, Qt.Key_Minus, Qt.ControlModifier | Qt.KeypadModifier)
    assert win.view.transform().m11() < z
    # the menu shows the first key; the plain keypad minus still deletes on the canvas
    assert win.act_zoom_in.shortcuts()[0].toString() == "Ctrl+="
    assert win.act_redo.shortcuts()[0].toString() == "Ctrl+Y"


def test_ctrl_shift_z_redoes(win):
    i = _rects(win, (10, 10, 60, 40))[0]
    assert win.doc.can_undo()
    win.undo()
    assert win.doc.can_redo()
    _key(win, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert not win.doc.can_redo() and i < len(win.doc.page.objects)


def test_alt_page_up_brings_forward_one_step(win):
    a, b, c = _rects(win, (10, 10, 60, 40), (20, 20, 70, 50), (30, 30, 80, 60))
    win._set_selection([a])
    depth = len(win.doc._undo)
    before = _snap(win.doc)
    _key(win, Qt.Key_PageUp, Qt.AltModifier)
    assert len(win.doc._undo) == depth + 1
    after = _snap(win.doc)
    assert after != before
    # one step: the object is now above the next one only
    assert after[a] == before[b] and after[b] == before[a] and after[c] == before[c]


# ------------------------------------------------------------------ align keys
ALIGN_KEYS = {
    "left": (Qt.Key_Left, None),
    "right": (Qt.Key_Right, None),
    "top": (Qt.Key_Up, None),
    "bottom": (Qt.Key_Down, None),
    "hcentre": (Qt.Key_C, None),
    "vcentre": (Qt.Key_M, None),
}


def test_align_and_distribute_keys_do_what_the_menu_does(win):
    rects = ((10, 10, 60, 40), (90, 50, 130, 80), (200, 120, 230, 170))
    for k, (mode, (key, _x)) in enumerate(ALIGN_KEYS.items()):
        idx = _rects(win, *rects)
        _key(win, key, Qt.AltModifier | Qt.ShiftModifier)
        by_key = [_box(win.doc, i) for i in idx]
        label = _undo_label(win.doc)
        win.undo()
        win._set_selection(idx)
        a = next(x for x in win.align_actions if x.objectName() == "align." + mode)
        a.trigger()
        assert [_box(win.doc, i) for i in idx] == by_key, mode
        assert _undo_label(win.doc) == label
        win.undo()
    for key, act in ((Qt.Key_H, win.act_dist_h), (Qt.Key_V, win.act_dist_v)):
        idx = _rects(win, *rects)
        _key(win, key, Qt.AltModifier | Qt.ShiftModifier)
        by_key = [_box(win.doc, i) for i in idx]
        win.undo()
        win._set_selection(idx)
        act.trigger()
        assert [_box(win.doc, i) for i in idx] == by_key
        win.undo()


# ------------------------------------------------------------------ F4 repeat
def test_f4_repeats_the_last_command(win, tmp_path):
    assert not win.act_repeat.isEnabled() and win.act_repeat.text() == "Repeat"
    a = _rects(win, (10, 10, 60, 40), (90, 50, 130, 80))
    win.align_actions[0].trigger()  # Align Left
    assert win.act_repeat.text() == "Repeat Align Left"
    b = _rects(win, (300, 10, 340, 40), (380, 50, 420, 80))
    depth = len(win.doc._undo)
    _key(win, Qt.Key_F4)
    assert len(win.doc._undo) == depth + 1  # its own undo step
    assert _box(win.doc, b[0])[0] == _box(win.doc, b[1])[0]
    win.save()
    c = _rects(win, (500, 10, 540, 40), (580, 50, 620, 80))
    assert win.act_repeat.isEnabled()
    _key(win, Qt.Key_F4)
    assert _box(win.doc, c[0])[0] == _box(win.doc, c[1])[0]
    win._set_selection([])
    win._update_actions()
    assert not win.act_repeat.isEnabled()  # nothing to align
    # a dialog or a tool is never repeated
    win._set_selection(a)
    win.tool_actions["line"].trigger()
    assert win.act_repeat.text() == "Repeat Align Left"


# ------------------------------------------------------------------ panel keys
def test_alt_digits_reach_the_panels_and_ctrl_f6_cycles(bare):
    w = bare
    from ctgedit.ui.panels import panel_nav

    w.view.setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(w.view, Qt.Key_4, Qt.AltModifier)
    QtWidgets.QApplication.processEvents()
    fw = QtWidgets.QApplication.focusWidget()
    assert fw is not None and (fw is w.props or w.props.isAncestorOf(fw))
    QtTest.QTest.keyClick(fw, Qt.Key_0, Qt.AltModifier)
    QtWidgets.QApplication.processEvents()
    assert QtWidgets.QApplication.focusWidget() is w.view
    w.dock_history.hide()
    w.act_go_history.trigger()
    assert not w.dock_history.isHidden()
    assert QtWidgets.QApplication.focusWidget() is w.history_dock.list
    w.act_go_library.trigger()
    assert QtWidgets.QApplication.focusWidget() is w.library_dock.search_field()
    # Ctrl+F6: the page, Properties, the left panel, the page tabs; and around again
    w.doc_tabs.setVisible(True)
    w.view.setFocus()
    seen = [panel_nav.cycle(w, 1) for _k in range(4)]
    assert seen == ["props", "library", "tabs", "page"], seen
    assert panel_nav.cycle(w, -1) == "tabs"
    assert w.act_next_panel.shortcut().toString() == "Ctrl+F6"
    assert [a.shortcut().toString() for a in w.panel_go_actions] == ["Alt+%d" % k for k in range(6)]


# ------------------------------------------------------------------ shortcuts window
def test_the_keyboard_shortcuts_window(bare, tmp_path):
    w = bare
    assert w.act_shortcuts_window.shortcut().toString() == "Ctrl+/"
    w.act_shortcuts_window.trigger()
    sw = w._dialogs.get("shortcuts")
    assert sw is not None and sw.isVisible() and not sw.isModal()
    from ctgedit.ui.help import dialog_topic

    assert dialog_topic(sw) == "the-window#keyboard-shortcuts"
    sw.bar.search.setText("dup")
    sw.bar.search.flush()
    shown = sw.shown_ids()
    assert "act_dup" in shown
    r = next(
        r for r in range(sw.table.rowCount()) if sw.table.item(r, 0).data(QtCore.Qt.UserRole + 2) == "act_dup"
    )
    assert sw.table.item(r, 2).data(QtCore.Qt.UserRole + 1) == ["Ctrl+D"]
    sw.bar.search.setText("")
    sw.bar.record.setChecked(True)
    QtTest.QTest.keyClick(sw.bar.search, Qt.Key_D, Qt.ControlModifier)
    assert sw.shown_ids() == ["act_dup"]
    assert sw.keyboard.lit() == {"D"}
    sw.bar.record.setChecked(False)
    sw.bar.set_key("")
    # the menus at the left
    sw.categories.setCurrentRow(5)  # Arrange
    assert all(i.startswith(("act_", "align.")) for i in sw.shown_ids())
    assert "act_group" in sw.shown_ids() and "act_dup" not in sw.shown_ids()
    # a key of the picture lists its commands
    sw.categories.setCurrentRow(0)
    sw._key_clicked("F4")
    assert set(sw.shown_ids()) == {"act_repeat", "act_close_doc"}  # F4, Ctrl+F4
    sw._key_clicked("F4")
    # the one-page list
    pdf = str(tmp_path / "keys.pdf")
    assert sw.export_pdf(pdf)
    with open(pdf, "rb") as f:
        assert f.read(5) == b"%PDF-"
    assert "Duplicate" in sw.cheat_sheet_html()
    # asking again brings the same window to the front
    w.act_shortcuts_window.trigger()
    assert w._dialogs.get("shortcuts") is sw


def test_the_shortcuts_list_never_runs_on_a_double_click_nor_edits_a_hidden_row(bare, monkeypatch):
    """A double-click changes the keys (as Settings > Keyboard), Run Command runs; a row the
    search hides is not the one Change acts on, and an empty search says so."""
    w = bare
    w.act_shortcuts_window.trigger()
    sw = w._dialogs.get("shortcuts")
    ran, changed = [], []
    monkeypatch.setattr(sw, "run_current", lambda: ran.append(sw.current_id()))
    monkeypatch.setattr(sw, "change_current", lambda: changed.append(sw.current_id()))
    sw.bar.search.setText("dup")
    sw.bar.search.flush()
    assert sw.select("act_dup")
    item = sw.table.item(sw.table.currentRow(), 0)
    sw.table.itemDoubleClicked.emit(item)
    assert changed == ["act_dup"] and ran == []
    assert sw.btn_change.isEnabled() and sw.btn_run.isEnabled()
    # the search hides it: nothing selected, Change off, the empty note shown
    sw.bar.search.setText("zznomatch99")
    sw.bar.search.flush()
    assert sw.current_id() == "" and not sw.btn_change.isEnabled() and not sw.btn_run.isEnabled()
    assert sw.table_stack.currentWidget() is sw.empty
    # another search that keeps other rows: the hidden selection is still gone
    sw.bar.search.setText("dup")
    sw.bar.search.flush()
    assert sw.select("act_dup")
    sw.bar.search.setText("group")
    sw.bar.search.flush()
    assert sw.shown_ids() and sw.current_id() == "" and not sw.btn_change.isEnabled()
    assert sw.table_stack.currentWidget() is sw.table
    sw.bar.search.setText("")
    sw.bar.search.flush()


def test_the_first_run_hint_names_the_palette_and_the_shortcuts(bare):
    from ctgedit.ui.prefs.settings import app_settings

    assert app_settings().value("ui/keys_hint_shown", False, type=bool)
    w2 = _window()
    try:
        assert "Ctrl+K" not in w2.statusBar().currentMessage()  # once only
    finally:
        _close(w2)
    app_settings().remove("ui/keys_hint_shown")
    w3 = _window()
    try:
        msg = w3.statusBar().currentMessage()
        assert "Ctrl+K" in msg and "Ctrl+/" in msg
    finally:
        _close(w3)


# ------------------------------------------------------------------ keys and the focus
def test_delete_with_the_focus_in_the_objects_panel_deletes_the_selection(win):
    """Edit > Delete keeps its window key, so Del
    in the Objects panel deletes the selected object (help: objects-panel)."""
    n0 = len(win.doc.parsed.page.objects)
    (line,) = win.doc.find_objects_by_name("FxLine")
    win._set_selection([line])
    QtWidgets.QApplication.processEvents()
    trees = [t for t in win.objects_dock.findChildren(QtWidgets.QTreeWidget) if t.isVisible()]
    assert trees
    trees[0].setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(trees[0], Qt.Key_Delete)
    QtWidgets.QApplication.processEvents()
    assert len(win.doc.parsed.page.objects) == n0 - 1


def test_the_find_window_takes_no_key_from_the_window(win):
    """The Find window's F3 / Shift+F3 work in it only; a keymap applied
    while it is open leaves the window's commands their keys."""
    from ctgedit.ui.prefs import keymap

    win._find_dialog(False)
    QtWidgets.QApplication.processEvents()
    keymap.apply_keymap(win)
    assert win.act_show_tools.shortcut().toString() == "F3"
    assert win.act_find.shortcut().toString() == "Ctrl+F"


def test_the_shortcuts_window_follows_keys_changed_elsewhere(bare):
    """A preset chosen in Settings (or an import) shows in an open
    Keyboard Shortcuts window at once, and in a hidden one when it is shown again."""
    from ctgedit.ui.prefs import keymap

    w = bare
    w.act_shortcuts_window.trigger()
    sw = w._dialogs.get("shortcuts")

    def keys_of(cid):
        r = next(
            r for r in range(sw.table.rowCount()) if sw.table.item(r, 0).data(QtCore.Qt.UserRole + 2) == cid
        )
        return sw.table.item(r, 2).data(QtCore.Qt.UserRole + 1)

    try:
        assert keys_of("tool.symbol") == ["A"]
        keymap.set_preset("builder")
        keymap.apply_everywhere()
        QtWidgets.QApplication.processEvents()
        assert keys_of("tool.symbol") == ["S"]
        sw.hide()
        keymap.set_preset("ctgedit")
        keymap.apply_everywhere()
        QtWidgets.QApplication.processEvents()
        sw.show()
        QtWidgets.QApplication.processEvents()
        assert keys_of("tool.symbol") == ["A"]
    finally:
        keymap.set_preset("ctgedit")


# ------------------------------------------------------------------ key fields and keymaps
def test_f4_in_a_drop_down_opens_its_list_and_repeats_nothing(win):
    """F4 with the keyboard in a drop-down of the Properties panel opens
    the list (as in Windows); Edit > Repeat does not run on the selection."""
    _rects(win, (10, 10, 60, 40), (90, 50, 130, 80))
    win.align_actions[0].trigger()  # Align Left
    _rects(win, (300, 10, 340, 40), (380, 50, 420, 80))
    QtWidgets.QApplication.processEvents()
    assert win.act_repeat.isEnabled()
    combos = [c for c in win.props.findChildren(QtWidgets.QComboBox) if c.isVisible() and c.isEnabled()]
    assert combos
    combo = combos[0]
    combo.setFocus()
    QtWidgets.QApplication.processEvents()
    depth = len(win.doc._undo)
    QtTest.QTest.keyClick(combo, Qt.Key_F4)
    QtWidgets.QApplication.processEvents()
    assert len(win.doc._undo) == depth  # nothing repeated
    assert combo.view().isVisible()  # the list is open
    combo.hidePopup()
    # Alt+Down too; F4 on the page still repeats
    QtTest.QTest.keyClick(combo, Qt.Key_Down, Qt.AltModifier)
    QtWidgets.QApplication.processEvents()
    assert combo.view().isVisible() and len(win.doc._undo) == depth
    combo.hidePopup()
    win.activateWindow()
    QtTest.QTest.qWaitForWindowActive(win, 2000)
    _key(win, Qt.Key_F4)
    assert len(win.doc._undo) == depth + 1


def test_a_second_key_of_your_own_keeps_a_built_in_commands_scope(win):
    """Del and a key of your own on Edit > Delete: Del still deletes from the Objects panel
    (the command keeps its built-in scope while it keeps a built-in key)."""
    from ctgedit.ui.prefs import keymap

    before = win.act_del.shortcutContext()
    assert keymap.conflicts(win, "act_del", ["Del", "Q"]) == []
    assert keymap.scope_for(win.act_del, ["Del", "Q"]) == keymap.scope_for(win.act_del, ["Del"])
    try:
        keymap.store_user_keys({"act_del": ["Del", "Q"]})
        keymap.apply_keymap(win)
        assert win.act_del.shortcutContext() == before
        n0 = len(win.doc.parsed.page.objects)
        (line,) = win.doc.find_objects_by_name("FxLine")
        win._set_selection([line])
        QtWidgets.QApplication.processEvents()
        tree = next(t for t in win.objects_dock.findChildren(QtWidgets.QTreeWidget) if t.isVisible())
        tree.setFocus()
        QtWidgets.QApplication.processEvents()
        QtTest.QTest.keyClick(tree, Qt.Key_Delete)
        QtWidgets.QApplication.processEvents()
        assert len(win.doc.parsed.page.objects) == n0 - 1
    finally:
        keymap.store_user_keys({})
        keymap.apply_keymap(win)


def test_the_keys_the_canvas_handles_itself_are_refused(bare):
    """Esc, Space, Enter, Backspace, Insert and '+' are the canvas's own keys (cancel, pan,
    finish, take a point back, add a node): Change Shortcut refuses them as it refuses the
    arrows."""
    from ctgedit.ui.prefs import keymap

    for k in ("Esc", "Space", "Return", "Enter", "Backspace", "Ins", "+", "Num++", "Left"):
        assert keymap.conflicts(bare, "act_snap", [k]) == [(k, "")], k
    # no built-in or preset key is one of them (no command loses a key to the canvas)
    own = set(keymap.CANVAS_OWN_KEYS)
    for preset in ("ctgedit", "builder", "design"):
        lay = keymap.layered(bare, preset, {})
        assert not [c for c, (ks, _s) in lay.items() if set(keymap._clean(ks)) & own], preset


def test_choosing_a_keymap_keeps_your_shift_arrow_choice(bare):
    """Only Design tools sets Shift + arrow; leaving it puts back the default, and a keymap
    that sets nothing keeps your own choice."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    assert keymap.preset_switch_settings("ctgedit", "builder") == {}
    assert keymap.preset_switch_settings("ctgedit", "design") == {"opt/shift_arrow": "nudge10"}
    assert keymap.preset_switch_settings("design", "builder") == {"opt/shift_arrow": "resize"}
    try:
        keymap.set_preset("ctgedit")
        st.setValue("opt/shift_arrow", "nudge10")
        keymap.set_preset("builder")
        assert st.value("opt/shift_arrow") == "nudge10"
        keymap.set_preset("ctgedit")
        assert st.value("opt/shift_arrow") == "nudge10"
        keymap.set_preset("design")
        keymap.set_preset("ctgedit")
        assert st.value("opt/shift_arrow") == "resize"
    finally:
        keymap.set_preset("ctgedit")
        st.setValue("opt/shift_arrow", "resize")


def test_settings_in_its_window_follows_the_keymap_it_leaves(bare, monkeypatch):
    """Settings > Keyboard: choosing a keymap changes Shift + arrow only when the keymap
    sets it or the one before did."""
    from ctgedit.ui.prefs import keymap
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    keymap.set_preset("ctgedit")
    dlg = SettingsDialog(bare)
    try:
        dlg._shift_arrow.setCurrentIndex(dlg._shift_arrow.findData("nudge10"))
        dlg._keymap_preset_changed("builder")
        assert dlg._shift_arrow.currentData() == "nudge10"
        dlg._keymap_preset_changed("design")
        dlg._shift_arrow.setCurrentIndex(dlg._shift_arrow.findData("nudge10"))
        dlg._keymap_preset_changed("ctgedit")
        assert dlg._shift_arrow.currentData() == "resize"
    finally:
        dlg.reject()
        dlg.deleteLater()
        keymap.set_preset("ctgedit")


def test_a_closed_settings_window_does_not_stay_behind(bare, monkeypatch):
    """A Settings open leaves no hidden SettingsDialog child of the editor (each made
    later theme changes slower)."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    monkeypatch.setattr(SettingsDialog, "exec", lambda self: 0)
    for _ in range(2):
        bare._options_dialog()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    QtWidgets.QApplication.processEvents()
    assert not bare.findChildren(SettingsDialog)


def test_the_shortcuts_window_shows_every_column_without_scrolling(bare):
    bare.act_shortcuts_window.trigger()
    sw = bare._dialogs.get("shortcuts")
    sw.resize(620, 460)
    QtWidgets.QApplication.processEvents()
    t = sw.table
    assert t.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    total = sum(t.columnWidth(c) for c in range(t.columnCount()))
    assert total <= t.viewport().width() + 1
    assert all(t.columnWidth(c) > 20 for c in range(t.columnCount()))


@pytest.mark.parametrize("fmt", ["1.0", [1], {"v": 1}])
def test_a_keys_file_with_an_odd_format_is_refused_with_a_message(tmp_path, fmt):
    import json
    from ctgedit.ui.prefs import keymap

    p = tmp_path / "keys.json"
    p.write_text(
        json.dumps({"format": fmt, "program": "ctgedit", "keymap": "ctgedit", "keys": {}}), encoding="utf-8"
    )
    with pytest.raises(keymap.KeysFileError):
        keymap.read_keys_file(str(p))


def test_f1_is_recorded_in_a_key_field_and_by_record_keys(bare, monkeypatch):
    """F1 is a key like any other in Change Shortcut and Record Keys: it is recorded, not
    answered with the manual."""
    from ctgedit.ui import help as H

    shown = []
    monkeypatch.setattr(H, "show_help", lambda *a, **k: shown.append(a))
    assert H.records_keys(QtWidgets.QKeySequenceEdit())
    assert not H.records_keys(QtWidgets.QLineEdit())
    bare.act_shortcuts_window.trigger()
    sw = bare._dialogs.get("shortcuts")
    sw.activateWindow()
    sw.bar.record.setChecked(True)
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(sw.bar.search, Qt.Key_F1)
    assert not shown
    assert sw.bar.key == "F1"
    assert "act_help" in sw.shown_ids()
    sw.bar.record.setChecked(False)
    sw.bar.set_key("")
    ed = QtWidgets.QKeySequenceEdit(sw)
    ed.show()
    ed.setFocus()
    QtWidgets.QApplication.processEvents()
    QtTest.QTest.keyClick(ed, Qt.Key_F1)
    assert not shown
    assert ed.keySequence().toString() == "F1"
    ed.deleteLater()
