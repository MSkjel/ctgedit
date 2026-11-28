"""Selection commands."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the valve genie FxMany places eight times
VALVE = "fxgenie.fx_valve"
OY = 2000


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _open(dst):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(str(dst))
    w.resize(1400, 1000)
    w.show()
    _pump()
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


@pytest.fixture()
def mixer(fx_page):
    w = _open(fx_page("FxMany"))
    yield w
    _close(w)


@pytest.fixture()
def about(fx_page):
    w = _open(fx_page("FxMaster"))
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    yield w
    _close(w)


def _rect(win, l, t, r, b):
    i = win.doc.create_object("rectangle", (l, t + OY, r, b + OY), None, colour=0x3366CC)
    win._refresh()
    win.view.set_zoom(1.0)
    win.view.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    return i


def _valves(win):
    return [
        r for r in win.doc.page.roots() if str(win.doc.page.objects[r].genie_template).startswith(VALVE + ".")
    ]


def _shortcut(win, key):
    seq = QtGui.QKeySequence(key)
    for sc in win.view.findChildren(QtGui.QShortcut):
        if sc.key() == seq:
            return sc
    raise AssertionError("no canvas shortcut %s" % key)


def _xy(win, i):
    g = win.doc.page.objects[i].geometry
    return g.x, g.y


# ================================================================== Edit > Select
def test_select_all_honours_the_filter(mixer):
    w = mixer
    w.select_all()
    every = len(w._sel)
    assert every > 50
    w.sel_filter.setCurrentIndex(6)  # Genies and groups
    w.select_all()
    kinds = {w.doc.page.objects[r].kind.value for r in w._sel}
    assert kinds <= {"genie", "composite_genie", "group"} and len(w._sel) < every
    assert "(Filter)" in w.statusBar().currentMessage()
    assert sorted(w.view.selection_roots()) == sorted(w._sel)
    w.sel_filter.setCurrentIndex(0)


def test_the_filter_and_select_same_take_a_placed_symbol_as_a_symbol(fx_page):
    """FxSymbols' placed group-root symbols (FxLinkedBox and the rest: the reader's genie
    kind, 'Symbol' in their rows, CtDraw's name) pass the Symbols filter, not the Genies and
    groups one, and Select Same > Kind groups them with the symbols."""
    from ctgedit.ui.names import is_placed_symbol

    w = _open(fx_page("FxSymbols"))
    try:
        page = w.doc.page
        placed = {r for r in page.roots() if is_placed_symbol(page.objects[r])}
        assert len(placed) >= 5
        w.sel_filter.setCurrentIndex(5)  # Symbols
        w.select_all()
        assert placed <= set(w._sel)
        w.sel_filter.setCurrentIndex(6)  # Genies and groups
        w.select_all()
        assert not placed & set(w._sel)
        w.sel_filter.setCurrentIndex(0)
        sets = [r for r in page.roots() if page.objects[r].kind.value == "symbol_set"]
        assert sets and {w._same_key(r, "kind") for r in sorted(placed) + sets} == {"group5"}
    finally:
        _close(w)


def test_invert_selection_and_deselect_all(mixer):
    w = mixer
    w.select_all()
    every = set(w._sel)
    one = sorted(every)[3]
    w._set_selection([one])
    w.invert_selection()
    assert set(w._sel) == every - {one}
    w.deselect_all()
    assert w._sel == [] and w.view.selection_roots() == []
    assert w.act_invert_sel.shortcut().toString() == "Ctrl+Shift+I"


def test_select_same_library_element_selects_every_valve(mixer):
    w = mixer
    valves = _valves(w)
    assert len(valves) == 8
    w.view.select_units([w.view._resolve_unit(valves[2])], reveal=False)
    w.act_select_same_element.trigger()
    assert sorted(w._sel) == valves
    msg = w.statusBar().currentMessage()
    assert msg.startswith("Selected 8 objects like Genie AN ")
    assert w.act_select_same_element.shortcut().toString() == "Ctrl+Alt+A"


def test_select_same_kind_fill_size_font_line_and_expression(mixer):
    w = mixer
    page = w.doc.page

    def check(mode, root, ok):
        w._set_selection([root])
        w.view.select_units([w.view._resolve_unit(root)], reveal=False)
        w.select_same(mode)
        assert root in w._sel
        for r in w._sel:
            assert ok(page.objects[r]), (mode, r)
        return list(w._sel)

    rects = [
        r
        for r in page.roots()
        if page.objects[r].kind.value == "rectangle" and page.objects[r].style.filled and w._unit_for(r)
    ]
    r0 = rects[0]
    o0 = page.objects[r0]
    check("kind", r0, lambda o: o.kind.value in ("rectangle", "ellipse", "polygon"))
    got = check("fill", r0, lambda o: o.style.filled and o.style.fill_colour == o0.style.fill_colour)
    assert len(got) >= 1
    check(
        "size",
        r0,
        lambda o: (o.geometry.extent_x, o.geometry.extent_y) == (o0.geometry.extent_x, o0.geometry.extent_y),
    )
    check("line_width", r0, lambda o: o.style.line_width == o0.style.line_width)
    texts = [
        r
        for r in page.roots()
        if page.objects[r].kind.value == "text" and page.objects[r].style.font and w._unit_for(r)
    ]
    t0 = page.objects[texts[0]]
    check(
        "font",
        texts[0],
        lambda o: (
            ((o.style.font or "").lower(), o.style.font_size) == (t0.style.font.lower(), t0.style.font_size)
        ),
    )
    # an expression shared: the valves are bound to tags
    valves = _valves(w)
    exprs = w._unit_expressions(valves[0])
    assert exprs  # (fx_valve animates on %TagPrefix%)
    got = check("anim", valves[0], lambda o: True)
    assert all(w._unit_expressions(r) & exprs for r in got)
    # a key object without the property says so
    w._set_selection([valves[0]])
    w.view.select_units([w.view._resolve_unit(valves[0])], reveal=False)
    w.select_same("fill")
    assert "has no fill" in w.statusBar().currentMessage()


def test_right_click_a_valve_select_all_instances(mixer):
    from ctgedit.ui.window import context_menu as CM

    w = mixer
    valves = _valves(w)
    assert len(valves) == 8
    w._set_selection([valves[0]])
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [valves[0]])
    act = next(a for a in menu.actions() if a.text().startswith("Select All Instances of "))
    assert act.text() == "Select All Instances of %s (8 on the page)" % VALVE
    act.trigger()
    assert sorted(w._sel) == valves


def test_select_previous_walks_back(mixer):
    w = mixer
    valves = _valves(w)
    w._set_selection([valves[0]])
    w._set_selection([valves[1], valves[2]])
    w._set_selection([valves[3]])
    w.select_previous()
    assert sorted(w._sel) == [valves[1], valves[2]]
    w.select_previous()
    assert w._sel == [valves[0]]
    # a canvas click is remembered too
    w.view.select_units([w.view._resolve_unit(valves[4])], reveal=False)
    w.select_previous()
    assert w._sel == [valves[0]]
    assert w.act_select_previous.shortcut().toString() == "Ctrl+Alt+Shift+A"


def test_select_objects_using_a_tag_takes_whole_names(mixer):
    w = mixer
    from ctgedit.features.find_replace import FindOptions, MATCH_WORD, SCOPE_EXPR, SCOPE_PARAM, find_hits

    # a tag one of the valves names
    valves = _valves(w)
    params = [v for v in w.doc.genie_bindings(valves[0]).values() if v and v.strip()]
    assert params == ["FX_V1"]  # (FxManyValve1's TagPrefix)
    tag = str(params[0]).split(".")[0].strip()
    w.select_using_tag(tag)
    got = set(w._sel)
    assert valves[0] in got
    opts = FindOptions(tag, scopes=frozenset((SCOPE_EXPR, SCOPE_PARAM)), match=MATCH_WORD)
    roots = {w.doc.root_of(h.index) for h in find_hits(w.doc, opts) if h.index >= 0}
    assert got <= roots
    w.select_using_tag(tag + "_NOT_A_TAG_XYZ")
    assert w._sel == [] and "No object" in w.statusBar().currentMessage()


def test_the_tag_dialog():
    from ctgedit.ui.dialog.select_dialogs import SelectByTagDialog

    _app()
    d = SelectByTagDialog(None, None, "Tank1")
    assert d.tag() == "Tank1" and d._ok.isEnabled() and d._ok.text() == "Select"
    d.field.setText("  ")
    assert not d._ok.isEnabled()
    from ctgedit.ui.help import DIALOG_TOPICS

    assert "SelectByTagDialog" in DIALOG_TOPICS
    d.close()


def test_the_select_list_of_what_is_under_the_pointer(mixer):
    from ctgedit.ui.window import context_menu as CM

    w, v = mixer, mixer.view
    vp = v.viewport().rect()
    at = None
    for y in range(40, vp.height() - 40, 12):
        for x in range(40, vp.width() - 40, 12):
            if len(v.units_under(QtCore.QPoint(x, y))) >= 3:
                at = QtCore.QPoint(x, y)
                break
        if at is not None:
            break
    assert at is not None, "FxStackA .. C are not in view"
    units = v.units_under(at)
    menu = CM.build_canvas_menu(w, v.viewport().mapToGlobal(at), [])
    assert menu.actions()[0].text() == "Select"
    sub = next(m for m in menu.findChildren(QtWidgets.QMenu) if m.title() == "Select")
    rows = sub.actions()
    assert len(rows) == len(units)
    assert rows[0].text().startswith(w._unit_label(min(units[0])))
    rows[1].hovered.emit()
    assert v._highlight == list(units[1])
    rows[1].trigger()
    assert v.selection_roots() == [min(units[1])] and w._sel == [min(units[1])]
    sub.aboutToHide.emit()
    assert v._highlight == []


# ================================================================== lock and hide
def test_locking_a_genie_chosen_in_the_tree_updates_the_tree(mixer):
    w = mixer
    g = _valves(w)[0]
    w.objects_dock.set_selection([g])
    w._on_dock_selection([g])
    assert w.objects_dock._selected_indices() == [g]
    n = len(w.doc._undo)
    w.act_lock_toggle.trigger()
    assert w._ct_locked(g) and len(w.doc._undo) == n + 1
    assert w.objects_dock._selected_indices() == []  # no row stays selected
    assert w._sel == [] and w.view.selection_roots() == []
    assert w.props._indices == []  # the panel let it go too
    from ctgedit.ui.panels.objects_dock import F_LOCKED

    look = w.objects_dock._looks.get(g)
    assert look is not None and look.flags & F_LOCKED  # the row's padlock shows it
    assert w.act_lock_toggle.shortcut().toString() == "Ctrl+L"
    # Unlock All Objects: every locked one, one step
    other = _valves(w)[1]
    w._set_selection([other])
    w._lock_selection()
    n = len(w.doc._undo)
    w.act_unlock_all.trigger()
    assert not w._ct_locked(g) and not w._ct_locked(other) and len(w.doc._undo) == n + 1
    assert w.act_unlock_all.shortcut().toString() == "Ctrl+Alt+L"


def test_the_lock_toggle_unlocks_a_locked_selection(mixer):
    w = mixer
    g = _valves(w)[0]
    w._set_selection([g])
    w._lock_toggle()
    assert w._ct_locked(g)
    w.act_break_lock.setChecked(True)
    w._break_locks()
    w._set_selection([g])
    w._lock_toggle()
    assert not w._ct_locked(g)
    w.act_break_lock.setChecked(False)
    w._break_locks()


def test_hide_or_show_selection(mixer):
    w = mixer
    vs = _valves(w)[:2]
    w._set_selection(vs)
    w.act_hide_toggle.trigger()
    assert set(vs) <= w._hidden_roots and w._sel == []
    assert w.view.scene()._hidden_roots >= set(vs)
    w._set_selection(vs)
    w._toggle_hidden_selection()
    assert not (set(vs) & w._hidden_roots)
    assert w.act_hide_toggle.shortcut().toString() == "Ctrl+Shift+H"


def test_set_selection_is_the_one_path(mixer):
    w = mixer
    vs = _valves(w)
    w._set_selection(vs)
    assert sorted(w.objects_dock._selected_indices()) == sorted(vs)
    assert sorted(w.view.selection_roots()) == sorted(vs)
    assert w.lbl_sel.text().startswith("%d objects · " % len(vs))
    assert sorted(w.props._indices) == sorted(vs)  # the Properties panel follows


# ================================================================== the arrow keys
def test_ctrl_arrow_modes_grid_steps_and_the_readout(about):
    from ctgedit.ui.prefs.settings import app_settings, set_option

    w, v = about, about.view
    i = _rect(w, 301, 300, 341, 330)
    w._set_selection([i])
    w._nudge_step = 1
    w._apply_grid(10, 10)
    # the default: Ctrl + an arrow moves one step
    _shortcut(w, "Ctrl+Right").activated.emit()
    w.commit_nudge()
    assert _xy(w, i) == (302, OY + 300)
    # ten steps
    app_settings().setValue("opt/ctrl_arrow", "x10")
    _shortcut(w, "Ctrl+Right").activated.emit()
    assert v.key_pill() == "312, %d  (+10, 0)" % (OY + 300)
    w.commit_nudge()
    assert _xy(w, i) == (312, OY + 300)
    # one grid pitch
    app_settings().setValue("opt/ctrl_arrow", "grid")
    _shortcut(w, "Ctrl+Right").activated.emit()
    _shortcut(w, "Ctrl+Right").activated.emit()
    assert v.key_pill().endswith("(+20, 0)")  # the burst's total
    w.commit_nudge()
    assert _xy(w, i) == (332, OY + 300)
    app_settings().setValue("opt/ctrl_arrow", "move")
    # with Snap to Grid and 'arrow_grid': the next grid line
    set_option("arrow_grid", True)
    w.act_snap.setChecked(True)
    _shortcut(w, "Right").activated.emit()
    w.commit_nudge()
    assert _xy(w, i) == (340, OY + 300)
    _shortcut(w, "Right").activated.emit()
    w.commit_nudge()
    assert _xy(w, i) == (350, OY + 300)
    _shortcut(w, "Left").activated.emit()
    w.commit_nudge()
    assert _xy(w, i) == (340, OY + 300)
    set_option("arrow_grid", False)
    # the readout goes a moment after the last key
    v.hide_key_pill()
    assert v.key_pill() is None


def test_a_shift_arrow_resize_shows_the_size_and_the_change(about):
    w, v = about, about.view
    i = _rect(w, 300, 300, 340, 330)
    w._set_selection([i])
    w._nudge_step = 5
    _shortcut(w, "Shift+Right").activated.emit()
    _shortcut(w, "Shift+Right").activated.emit()
    assert v.key_pill() == "50 × 30  (+10, 0)"


def test_the_arrows_scroll_the_page_with_nothing_selected(about):
    from ctgedit.ui.prefs.settings import set_option

    w, v = about, about.view
    w._set_selection([])
    v.set_zoom(4.0)
    v.centerOn(QtCore.QPointF(500, 380))
    _pump(1)
    hs, vs = v.horizontalScrollBar(), v.verticalScrollBar()
    h0, v0 = hs.value(), vs.value()
    _shortcut(w, "Right").activated.emit()
    assert hs.value() == h0 + v.viewport().width() // 8
    _shortcut(w, "Down").activated.emit()
    assert vs.value() == v0 + v.viewport().height() // 8
    _shortcut(w, "Ctrl+Left").activated.emit()  # (Ctrl too)
    assert hs.value() == h0
    h1 = hs.value()
    _shortcut(w, "Shift+Right").activated.emit()  # a whole view
    assert hs.value() > h1 + v.viewport().width() // 2
    # Home: the page's left edge 20 px from the canvas's
    _shortcut(w, "Home").activated.emit()
    pr = v.scene().page_rect()
    assert abs(v.mapFromScene(pr.topLeft()).x() - 20) <= 1
    _shortcut(w, "End").activated.emit()
    assert abs(v.mapFromScene(pr.topRight()).x() - (v.viewport().width() - 20)) <= 1
    # off: nothing, as in the Graphics Builder (Page Down still scrolls)
    set_option("arrows_pan", False)
    h2 = hs.value()
    _shortcut(w, "Right").activated.emit()
    assert hs.value() == h2
    v2 = vs.value()
    _shortcut(w, "PgDown").activated.emit()
    assert vs.value() > v2
    set_option("arrows_pan", True)


def test_the_new_commands_are_in_the_menus_with_icons_and_descriptions(about):
    from ctgedit.ui.main_window_base import DESCRIPTIONS

    w = about
    names = (
        "act_deselect",
        "act_invert_sel",
        "act_select_same_element",
        "act_select_same_kind",
        "act_select_same_fill",
        "act_select_same_line_colour",
        "act_select_same_line_width",
        "act_select_same_font",
        "act_select_same_size",
        "act_select_same_anim",
        "act_select_tag",
        "act_select_previous",
        "act_lock_toggle",
        "act_unlock_all",
        "act_hide_toggle",
    )
    from ctgedit.ui.dialog.palette import harvest_actions

    in_menus = {id(a) for a, _crumb, _t in harvest_actions(w.menuBar())}
    for n in names:
        a = getattr(w, n)
        assert a.objectName() == n and not a.icon().isNull(), n
        assert DESCRIPTIONS.get(n), n
        assert id(a) in in_menus, n
