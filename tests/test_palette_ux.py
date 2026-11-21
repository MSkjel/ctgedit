"""The command palette."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the tag FxFill and four other objects of FxAnim name in their expressions
TAG = "FX_LEVEL1"


def _pump(n=4):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    yield w
    w.palette_popup.hide()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


@pytest.fixture()
def mixer(win, fx_page, tmp_path):
    dst = tmp_path / "Mixer.ctg"
    shutil.copyfile(fx_page("FxAnim"), dst)
    win.open_document(str(dst))
    _pump()
    return win


def _an_of(win, name):
    """(index, AN) of the object named ``name`` on the window's page."""
    (i,) = win.doc.find_objects_by_name(name)
    return i, win.doc.page.objects[i].an


def _rows(p, text):
    p._edit.setText(text)
    return p._list.rows()


def _texts(p, text):
    p._edit.setText(text)
    return [p._list.item(i).text() for i in range(p._list.count())]


def _commands(rows):
    return [r for r in rows if r.kind == "command"]


def _key(widget, key, mods=QtCore.Qt.NoModifier, text=""):
    QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text))


# --------------------------------------------------------------------------- the scorer
def test_the_word_rule_scorer():
    from ctgedit.ui import search_rows as SR

    assert SR.rank("dh", "Distribute Horizontally") >= 0
    assert SR.match("dh", "Distribute Horizontally")[1] == [0, 11]
    for q, text in (
        ("level", "Tile Vertically"),
        ("ali", "Pack Libraries"),
        ("rot", "Properties"),
        ("rot", "Strikeout"),
    ):
        assert SR.rank(q, text, substring_min=4) == -1, (q, text)
    # (a part of a word only from four letters on: 'ali' in Italian is noise)
    assert SR.rank("ali", "Italian(Italy)", substring_min=4) == -1
    assert SR.rank("ital", "Italian(Italy)", substring_min=4) == 0
    # (nor a joining word: 'ali' is not 'and Line'; typed whole, it is a word)
    assert SR.rank("ali", "Swap Fill and Line Colours", substring_min=4) == -1
    assert SR.rank("sfl", "Swap Fill and Line Colours") >= 0
    assert SR.rank("and", "Swap Fill and Line Colours") == 12
    # Quick Open's names: letters inside a word, each next word entered at its start
    assert SR.rank("mxl3", "Mixing_L3", within_word=True) >= 0
    assert SR.rank("mxl3", "Mixing_L3") == -1
    assert SR.match("mxl3", "Mixing_L3", within_word=True)[1] == [0, 2, 7, 8]
    # the order: start, word, each word, part, letters
    a = SR.rank("flip", "Flip Horizontal")
    b = SR.rank("hor", "Flip Horizontal")
    c = SR.rank("fl ho", "Flip Horizontal")
    d = SR.rank("fh", "Flip Horizontal")
    assert a < b < c < d
    assert SR.word_starts("Mixing_L3") == [0, 7, 8]
    assert SR.key_parts("Ctrl+Shift+A") == ["Ctrl", "Shift", "A"]
    assert SR.key_parts("Ctrl++") == ["Ctrl", "+"]


def test_ali_lists_the_align_commands_not_italian_or_pack_libraries(win):
    p = win.palette_popup
    p.open_at()
    cmds = _commands(_rows(p, "ali"))
    assert cmds and all(r.text.startswith("Align") for r in cmds), [r.text for r in cmds]
    assert not any("Pack Libraries" in r.text or "Italian" in r.text for r in cmds)


def test_dh_finds_distribute_horizontally(win):
    p = win.palette_popup
    p.open_at()
    cmds = _commands(_rows(p, "dh"))
    assert cmds[0].text == "Distribute Horizontally"
    assert cmds[0].hits == (0, 11)


def test_level_does_not_list_tile_vertically_but_finds_by_keywords(win):
    p = win.palette_popup
    p.open_at()
    names = [r.text for r in _commands(_rows(p, "level"))]
    assert "Tile Vertically" not in names
    assert any(n.startswith("Object Properties") for n in names)
    r = next(r for r in _commands(p._list.rows()) if r.text.startswith("Object Properties"))
    assert r.alias.startswith("Also:")


def test_a_graphics_builder_word_shows_dim_beside_the_command(win):
    p = win.palette_popup
    p.open_at()
    r = _commands(_rows(p, "attributes"))[0]
    assert r.text.startswith("Object Properties")
    assert r.alias == "Graphics Builder: Attributes"


def test_a_row_has_its_icon_keys_path_and_state(win):
    p = win.palette_popup
    p.open_at()
    r = next(r for r in _commands(_rows(p, "smart guides")) if r.cid == "act_smart_guides")
    assert r.detail == "View" and r.icon is not None and not r.icon.isNull()
    assert r.check is win.act_smart_guides.isChecked()
    r = next(r for r in _commands(_rows(p, "snap to grid")) if r.cid == "act_snap")
    assert r.keys == ("F8",)
    # the plain line is the accessible text: name, keys, path
    assert p._list.item(0).text().startswith("Snap to Grid    F8")
    # one of a set (a drawing tool, a grid size): a box only on the chosen one
    tools = {r.cid: r for r in _commands(_rows(p, "rectangle"))}
    assert tools["tool.rectangle"].check is None


def test_rows_that_are_not_commands_are_named_after_their_menu(win):
    p = win.palette_popup
    p.open_at()
    names = [r.text for r in _commands(_rows(p, "grid"))]
    assert "Grid: 1 px" in names and "1 px" not in names
    # the sizes keep the menu's order (1, 2, 4 ... not 1, 10, 16)
    sizes = [n for n in names if n.startswith("Grid: ") and n.endswith(" px")]
    assert sizes == sorted(sizes, key=lambda n: int(n.split()[1]))
    # a row of a menu built as it opens (no id): its menu's name first
    from ctgedit.ui.dialog.palette import display_name

    a = QtGui.QAction("FxShapes  •\tFxMain", win)
    assert display_name(a, "Window", a.text()) == ("Window: FxShapes •", "FxShapes •", "FxMain")
    b = QtGui.QAction("&2  FxAnim  (FxMain)", win)
    assert (
        display_name(b, "File > Recent Pages", b.text().replace("&", ""))[0]
        == "Recent Pages: FxAnim (FxMain)"
    )


def test_the_empty_palette_starts_with_recently_used(win):
    from ctgedit.ui.prefs import keymap

    for cid in ("act_zoom_in", "act_grid", "act_options"):
        keymap.push_palette_mru(win, cid)
    p = win.palette_popup
    p.open_at()
    rows = p._list.rows()
    assert rows[0].kind == "header" and rows[0].text == "Recently used"
    assert [r.cid for r in rows[1:4]] == ["act_options", "act_grid", "act_zoom_in"]
    heads = [r.text for r in rows if r.kind == "header"]
    assert heads == ["Recently used", "Suggested", "All commands"]
    sug = rows[heads and [r.text for r in rows].index("Suggested") + 1 :]
    assert sug[0].cid == "tool.line"
    # each command once; the first row is the current one, its header shows
    cids = [r.cid for r in rows if r.kind == "command"]
    assert len(cids) == len(set(cids))
    assert p._list.currentRow() == 1
    assert p._list.verticalScrollBar().value() == 0
    # the prefixes are listed under the empty field
    assert p._hints.isVisibleTo(p) and "set:" in p._hints.text()
    _rows(p, "x")
    assert not p._hints.isVisibleTo(p)


def test_the_recent_pages_and_the_open_pages_are_rows_named_after_their_menu(mixer):
    win = mixer
    p = win.palette_popup
    p.open_at()
    names = [r.text for r in _commands(_rows(p, "mixer"))]
    assert any(n.startswith("Recent Pages: Mixer") for n in names), names
    assert any(n.startswith("Window: Mixer") for n in names), names
    # (a menu's placeholder row is not a command)
    assert not any("No recent pages" in n for n in _texts(p, "no recent"))


def test_the_selection_shows_its_contextual_commands(mixer):
    win = mixer
    t = next(i for i, o in enumerate(win.doc.page.objects) if o.parent is None and o.kind.value == "text")
    win._on_canvas_selection([t])
    _pump()
    p = win.palette_popup
    p.open_at()
    rows = p._list.rows()
    heads = [r.text for r in rows if r.kind == "header"]
    assert "For the selection" in heads
    k = [r.text for r in rows].index("For the selection")
    cids = []
    for r in rows[k + 1 :]:
        if r.kind == "header":
            break
        cids.append(r.cid)
    assert "textflag.bold" in cids


def test_a_greyed_command_says_what_it_needs_and_the_palette_stays(win):
    p = win.palette_popup
    p.open_at()
    rows = _rows(p, "align left")
    r = _commands(rows)[0]
    assert r.cid == "align.left" and not r.enabled
    assert r.reason == "Select two or more objects first."
    fired = []
    win.align_actions[0].triggered.connect(lambda *_: fired.append(1))
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert p.isVisible() and not fired
    assert p._footer.text() == "Align Left: Select two or more objects first."
    # the footer says what Enter does again on the next row
    _rows(p, "align")
    p._list.move(1)
    assert p._list.current_row_obj().kind == "command"
    assert "Enter runs" in p._footer.text()


def test_greyed_rows_come_after_the_ones_that_run_among_equal_matches():
    _app()
    from ctgedit.ui.dialog import palette

    w = QtWidgets.QMainWindow()
    m = w.menuBar().addMenu("Tools")
    off = QtGui.QAction("Alpha One", w)
    off.setEnabled(False)
    on = QtGui.QAction("Alpha Two", w)
    m.addAction(off)
    m.addAction(on)
    p = palette.CommandPalette(w)
    p._actions = palette.harvest_actions(w.menuBar())
    names = [r.text for r in _rows(p, "alpha")]
    assert names == ["Alpha Two", "Alpha One"]
    w.deleteLater()


def test_the_keys_move_in_the_list(win):
    p = win.palette_popup
    p.open_at()
    rows = p._list.rows()
    assert rows[p._list.currentRow()].selectable
    _key(p._edit, QtCore.Qt.Key_End)
    last = p._list.currentRow()
    assert last == len(rows) - 1
    _key(p._edit, QtCore.Qt.Key_Home)
    first = p._list.currentRow()
    assert first == p._list.first_selectable()
    _key(p._edit, QtCore.Qt.Key_PageDown)
    assert p._list.currentRow() > first + 3
    _key(p._edit, QtCore.Qt.Key_End, QtCore.Qt.ControlModifier)
    assert p._list.currentRow() == last
    _key(p._edit, QtCore.Qt.Key_Home, QtCore.Qt.ControlModifier)
    assert p._list.currentRow() == first
    # Down and Up go over a header
    k = next(i for i, r in enumerate(rows) if r.kind == "header" and i > first)
    p._list.setCurrentRow(k - 1)
    _key(p._edit, QtCore.Qt.Key_Down)
    assert p._list.currentRow() == k + 1
    _key(p._edit, QtCore.Qt.Key_Up)
    assert p._list.currentRow() == k - 1


def test_shift_enter_runs_and_stays_open(win):
    p = win.palette_popup
    p.open_at()
    was = win.act_smart_guides.isChecked()
    _rows(p, "smart guides")
    k = next(i for i, r in enumerate(p._list.rows()) if r.cid == "act_smart_guides")
    p._list.setCurrentRow(k)
    _key(p._edit, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
    _pump()
    try:
        assert p.isVisible()
        assert win.act_smart_guides.isChecked() is not was
        r = p._list.current_row_obj()
        assert r.cid == "act_smart_guides" and r.check is (not was)
    finally:
        if win.act_smart_guides.isChecked() is not was:
            win.act_smart_guides.trigger()


def test_alt_enter_and_ctrl_enter_open_change_shortcut(win, monkeypatch):
    from ctgedit.ui.prefs import keymap_page

    opened = []
    monkeypatch.setattr(
        keymap_page, "open_change_shortcut", lambda parent, cid, window=None, **k: opened.append(cid)
    )
    p = win.palette_popup
    for mods in (QtCore.Qt.AltModifier, QtCore.Qt.ControlModifier):
        p.open_at()
        _rows(p, "zoom in")
        _key(p._edit, QtCore.Qt.Key_Return, mods)
        _pump()
        assert not p.isVisible()
    assert opened == ["act_zoom_in", "act_zoom_in"]


def test_the_width_follows_the_window(win):
    p = win.palette_popup
    win.resize(1600, 1000)
    p.open_at()
    assert p.width() == 640
    p.hide()
    win.resize(1000, 800)
    p.open_at()
    assert p.width() == 460


def test_the_frame_is_drawn_in_the_theme_colours(win):
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.style import theme

    assert theme.COLORS["popup"] in palette.CommandPalette._qss()
    assert theme.COLORS["border"] in palette.CommandPalette._qss()


# --------------------------------------------------------------------------- the prefixes
def test_gt_lists_commands_only(win):
    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, ">grid") if r.selectable]
    assert rows and all(r.kind == "command" for r in rows)
    # without the prefix the settings and the manual come after the commands
    kinds = [r.kind for r in _rows(p, "grid")]
    assert "setting" in kinds and "help" in kinds
    assert kinds.index("setting") > max(i for i, k in enumerate(kinds) if k == "command")
    heads = [r.text for r in p._list.rows() if r.kind == "header"]
    assert heads[:2] == ["Settings", "Help"]
    assert kinds.count("setting") <= 3 and kinds.count("help") <= 3


def test_at_an_selects_and_frames_that_object(mixer):
    win = mixer
    an, num = _an_of(win, "FxFill")
    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, "@%d" % num) if r.selectable]
    assert rows[0].kind == "object" and rows[0].data.data == an
    assert "AN %d" % num in rows[0].detail
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert not p.isVisible()
    assert win._sel == [an]


def test_colon_and_an_go_to_that_object(mixer):
    win = mixer
    _i, num = _an_of(win, "FxFill")
    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, ":%d" % num) if r.selectable]
    assert len(rows) == 1 and rows[0].kind == "goto"
    assert rows[0].text.startswith("Go to AN %d" % num)
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert win.doc.page.objects[win._sel[0]].an == num
    assert _texts(p, ":99999") == ["No object has AN 99999"]


def test_at_finds_objects_by_a_tag_and_ctrl_enter_selects_every_one(mixer):
    win = mixer
    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, "@" + TAG) if r.selectable]
    assert len(rows) >= 2
    assert rows[0].data.data == _an_of(win, "FxFill")[0]
    listed = {r.data.data for r in rows}
    _key(p._edit, QtCore.Qt.Key_Return, QtCore.Qt.ControlModifier)
    _pump()
    roots = {win.doc.root_of(i) for i in listed}
    assert roots <= {win.doc.root_of(i) for i in win._sel}


def test_set_lists_the_options_with_their_state(win, monkeypatch):
    from ctgedit.ui.dialog import palette

    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, "set:grid") if r.selectable]
    names = [r.text for r in rows]
    assert "Show the grid" in names and "Snap to the grid" in names
    r = rows[names.index("Show the grid")]
    assert r.kind == "setting" and r.data.key == "view/grid"
    assert r.chip in ("On", "Off")
    assert p._list.item(p._list.rows().index(r)).text().startswith("Setting: Show the grid, %s" % r.chip)
    opened = []
    monkeypatch.setattr(
        palette,
        "show_setting",
        lambda w, page, label="", key="", exec_=True: opened.append((page, label, key)),
    )
    p._list.setCurrentRow(p._list.rows().index(r))
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert opened == [("Canvas", "Show the grid", "view/grid")]


def test_show_setting_opens_settings_on_the_row(win):
    from ctgedit.ui.dialog import palette

    dlg = palette.show_setting(win, "Canvas", "Show the grid", "view/grid", exec_=False)
    try:
        _pump()
        assert dlg._tabs.tabText(dlg._tabs.currentIndex()) == "Canvas"
        assert dlg._tabs.search.text() == "Show the grid"
        assert dlg.focus_target is not None and dlg.focus_target.key == "view/grid"
        assert not dlg.focus_target.isHidden()
    finally:
        dlg.reject()
        dlg.deleteLater()


def test_question_mark_smart_opens_the_smart_guides_help(win, monkeypatch):
    from ctgedit.ui import help as UH

    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, "?smart") if r.selectable]
    assert rows[0].kind == "help" and rows[0].text == "Drawing objects > Smart guides"
    shown = []
    monkeypatch.setattr(UH, "show_help", lambda topic, parent=None, main=None: shown.append(topic))
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert shown == ["drawing#smart-guides"]


def test_the_find_prefixes_use_quick_opens_sources_and_enter(mixer, monkeypatch):
    win = mixer
    p = win.palette_popup
    p.open_at()
    rows = [r for r in _rows(p, "p:mix") if r.selectable]
    assert rows and all(r.kind == "page" for r in rows)
    opened = []
    monkeypatch.setattr(win, "open_document", lambda path, **k: opened.append(path))
    _key(p._edit, QtCore.Qt.Key_Return)
    _pump()
    assert opened and opened[0].lower().endswith("mixer.ctg")
    # (a loose folder has no libraries: 's:' says so)
    p.open_at()
    assert _texts(p, "s:") == ["No symbols in this project"]
    assert _texts(p, "s:zzqq") == ['No symbol matches "zzqq"']


# --------------------------------------------------------------------------- the tool box
def test_s_on_the_page_shows_the_tool_box_ctrl_k_does_not(win):
    from ctgedit.ui.dialog import palette

    p = win.palette_popup
    win.act_palette_here.trigger()
    _pump()
    assert p.isVisible() and p.toolbox.isVisibleTo(p)
    assert [t.defaultAction().objectName() for t in p.toolbox.tiles] == list(palette.DRAWING_TOOLS)
    assert p.toolbox.tiles[0].number == 1
    # (the drawing tools are not listed again under Suggested)
    assert "tool.line" not in [r.cid for r in p._list.rows()]
    p.hide()
    win.act_palette.trigger()
    _pump()
    assert p.isVisible() and not p.toolbox.isVisibleTo(p)


def test_pressing_3_arms_the_third_tool(mixer):
    win = mixer
    p = win.palette_popup
    p.open_at(QtGui.QCursor.pos(), toolbox=True)
    third = p.toolbox.tiles[2].defaultAction()
    assert third is win.tool_actions["rectangle"]
    _key(p._edit, QtCore.Qt.Key_3, text="3")
    _pump()
    assert not p.isVisible()
    assert win.tool_actions["rectangle"].isChecked()
    win.tool_actions["select"].trigger()


def test_pin_and_unpin_a_command(win):
    from ctgedit.ui.dialog import palette
    from ctgedit.ui.prefs.settings import app_settings

    p = win.palette_popup
    p.open_at(QtGui.QCursor.pos(), toolbox=True)
    _rows(p, "show grid")
    r = next(r for r in p._list.rows() if r.cid == "act_grid")
    m = p.row_menu(r)
    pin = next(a for a in m.actions() if a.text() == "Pin to the Tool Box")
    pin.trigger()
    assert palette.pins()[-1] == "act_grid"
    assert [str(x) for x in app_settings().value("ui/palette_pins")][-1] == "act_grid"
    assert p.toolbox.tiles[-1].defaultAction() is win.act_grid
    m = p.row_menu(r)
    unpin = next(a for a in m.actions() if a.text() == "Unpin from the Tool Box")
    unpin.trigger()
    assert "act_grid" not in palette.pins()
    assert len(p.toolbox.tiles) == len(palette.DRAWING_TOOLS)
    # a tile's own menu unpins too
    tm = p.toolbox.tile_menu(p.toolbox.tiles[0])
    tm.actions()[0].trigger()
    assert "tool.line" not in palette.pins()
    palette.set_pins(palette.DRAWING_TOOLS)


def test_a_selection_row_made_for_another_page_does_nothing(mixer, tmp_path):
    """The palette's Selection rows act on the page and records of the moment it opened: one
    run after the page changed says so and does nothing."""
    w = mixer
    doc = w.doc
    w._definition_of = lambda i: ("lib.ctm", "elem")  # (a row: Edit Definition)
    w._set_selection([doc.page.roots()[1]])
    acts = w.palette_context_actions()
    assert acts
    doc.duplicate_objects([doc.page.roots()[1]], offset=10)  # (the records changed)
    w._refresh()
    calls = []
    monkey = w._open_element
    w._open_element = lambda *a, **k: calls.append(a)
    try:
        for a, _crumb, _label in acts:
            a.trigger()
    finally:
        w._open_element = monkey
    assert calls == [] and "Search again" in w.statusBar().currentMessage()
    doc.dirty = False
