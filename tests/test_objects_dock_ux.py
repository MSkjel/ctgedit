"""Objects panel rows, renaming, eye and padlock cells."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

#: the objects of FxGenies: the pump the rows are checked on,
#: the genie whose Label is an '@(...)' text, the third genie on the same tag, an object
#: to hide, one to keep shown, one more top-level object
PUMP, LABELLED, LAMP = "FxPump3", "FxPump2", "FxLamp"
HIDDEN, SHOWN, OTHER = "FxPlain", "FxKeys", "FxValue"


def _prepared(fx_install, page="FxGenies", project="FxMain"):
    """``page`` of the fx_install copy with its top-level names taken off (saved in place):
    (path, {fixture name: record index})."""
    from ctgedit.editing import EditableDocument

    path = fx_install.page(page, project)
    doc = EditableDocument.open(path)
    idx = {}
    for r in doc.page.roots():
        name = doc.object_name(r)
        if name:
            idx[name] = r
            assert doc.set_object_name(r, ""), name
    if page == "FxGenies":
        assert doc.set_genie_bindings(idx[LABELLED], {"Tag": "FX_PUMP3", "Label": "@(Fx Breaker)"})
    doc.save()
    return path, idx


def _pump(n=8):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    for k in (
        "auto_placeholder_missing",
        "auto_update_genies",
        "auto_update_symbols",
        "auto_update_template",
    ):
        app_settings().setValue("opt/" + k, False)
    w = MainWindow()
    w.resize(1280, 800)
    yield w
    for x in list(MainWindow._instances):
        for e in getattr(x, "_docs", []):
            e["doc"].dirty = False
        if x in MainWindow._instances:
            MainWindow._instances.remove(x)
        x.hide()


def _open(win, fx_install, page="FxGenies", project="FxMain"):
    """``page`` (prepared: :func:`_prepared`) in the window: (doc, {fixture name: index})."""
    path, idx = _prepared(fx_install, page, project)
    win.open_document(path)
    win.show()
    _pump()
    return win.doc, idx


def _template_doc(win, fx_install):
    """FxTplLinked in the window: (doc, the template's index, FxTplPump's index)."""
    doc, idx = _open(win, fx_install, "FxTplLinked")
    tpl = doc.page.roots()[0]
    assert doc.page.objects[tpl].kind.value == "genie" and ".<ctt" in str(
        doc.page.objects[tpl].genie_template
    )
    return doc, tpl, idx["FxTplPump"]


def _rect(dock, idx):
    tree = dock._tree
    item = dock._items[idx]
    tree.scrollToItem(item)
    _pump(2)
    # (the Object cell, as the delegate lays it out: visualItemRect spans every column)
    return tree.visualRect(tree.indexFromItem(item, 0))


def _zone(dock, idx, name):
    look = dock._looks[idx]
    return dock._delegate.zones(_rect(dock, idx), idx, look.flags)[name]


def _mouse(widget, kind, pos, button=QtCore.Qt.LeftButton, buttons=None, mods=QtCore.Qt.NoModifier):
    t = {
        "press": QtCore.QEvent.MouseButtonPress,
        "move": QtCore.QEvent.MouseMove,
        "release": QtCore.QEvent.MouseButtonRelease,
    }[kind]
    if buttons is None:
        buttons = QtCore.Qt.NoButton if kind == "release" else QtCore.Qt.LeftButton
    p = QtCore.QPointF(pos)
    ev = QtGui.QMouseEvent(
        t,
        p,
        QtCore.QPointF(widget.mapToGlobal(pos)),
        QtCore.Qt.NoButton if kind == "move" else button,
        buttons,
        mods,
    )
    QtWidgets.QApplication.sendEvent(widget, ev)


# --------------------------------------------------------------------------- row labels
def test_row_label_leads_with_the_element_and_the_tag(fx_install):
    """names.row_label is Qt-free and shared: a genie by its element and main binding, a text
    by its caption, a template by its element; an equipment path by its last part."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import names

    path, idx = _prepared(fx_install)
    doc = EditableDocument.open(path)
    pg = doc.page
    i = idx[PUMP]
    assert names.row_label(pg, i, pg.objects[i]) == ("fx_pump  FX_PUMP3", "Genie (3)")
    k, v = names.main_binding(pg.objects[idx[LABELLED]])
    assert (k, v) == ("Tag", "FX_PUMP3")  # not the '@(Fx Breaker)' label
    assert names.binding_text("Equipment", "FxPlant.Area.Line.FxSpray02") == "FxSpray02"
    path, _idx = _prepared(fx_install, "FxTplLinked")
    tdoc = EditableDocument.open(path)
    tpl = tdoc.page.roots()[0]
    assert names.row_label(tdoc.page, tpl, tdoc.page.objects[tpl])[1].startswith("Template")
    kw, tags, lib = names.row_search_text(pg, i, pg.objects[i])
    assert "Tag=FX_PUMP3" in kw and "FX_PUMP3" in tags and lib.startswith("fxgenie.fx_pump")


def test_a_placed_group_root_symbol_is_a_symbol(fx_install):
    """A placed group-root symbol shows as a symbol."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind
    from ctgedit.ui import names
    from ctgedit.ui.panels.objects_dock import _kind_key
    from ctgedit.ui.simulate.inspector import kind_text, owner_genie

    doc = EditableDocument.open(fx_install.page("FxSymbols"))
    pg = doc.page
    (box,) = doc.find_objects_by_name("FxLinkedBox")
    (sset,) = doc.find_objects_by_name("FxOnOffSet")
    for i in [box] + pg.objects[sset].members:
        o = pg.objects[i]
        assert o.kind is ObjectKind.GENIE and not o.is_genie
        assert names.kind_name(o) == "Symbol" and kind_text(o) == "Symbol"
        assert names.row_label(pg, i, o)[1].startswith("Symbol")
        assert names.is_placed_symbol(o) and _kind_key(o) == "symbol"
        assert owner_genie(pg, pg.objects[o.members[0]]) == ""  # its member: no genie
    gdoc = EditableDocument.open(fx_install.page("FxGenies"))
    (pump,) = gdoc.find_objects_by_name(PUMP)
    p = gdoc.page.objects[pump]
    assert names.kind_name(p) == "Genie" and kind_text(p) == "Genie"
    assert not names.is_placed_symbol(p) and _kind_key(p) == "genie"
    assert owner_genie(gdoc.page, gdoc.page.objects[p.members[0]]).startswith("fxgenie.fx_pump")


def test_the_filter_and_the_search_take_a_placed_symbol_as_a_symbol(win, fx_install):
    """FxSymbols' placed group-root symbols (FxLinkedBox and the rest, the reader's genie
    kind) sit in the filter's Symbols group with the symbol icon, and 'kind:symbol' finds
    them while 'kind:genie' does not: as their rows name them (names.is_placed_symbol)."""
    from ctgedit.ui.names import is_placed_symbol

    doc, idx = _open(win, fx_install, "FxSymbols")
    dock = win.objects_dock
    placed = {i for i, o in enumerate(doc.page.objects) if is_placed_symbol(o)}
    assert idx["FxLinkedBox"] in placed and len(placed) >= 10
    assert all(dock._looks[i].kind_key == "symbol" and dock._looks[i].icon == "symbol" for i in placed)
    assert dock.filter_counts()["kind:symbol"] >= len(placed)
    for text, want in (("kind:symbol", True), ("kind:genie", False)):
        dock._search.setText(text)
        dock._search.flush()
        listed = set(dock._listed)  # (the topmost matches: a set lists its states)
        found = {i for i in placed if listed & ({i} | set(doc.page.ancestors(i)))}
        assert found == (placed if want else set()), text
    dock._search.setText("")
    dock._search.flush()


def test_a_composite_genie_reads_by_its_template_and_its_equipment(fx_install):
    """FxComp1 (FxValveC, Equipment FxPlant.Valve1, two members). Its content genie binds
    the equipment as Tag, so the row shows the whole path (a binding named Equipment shows
    its last part: test_an_equipment_binding_reads_by_its_last_part)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import names

    path, idx = _prepared(fx_install, "FxComposites")
    doc = EditableDocument.open(path)
    i = idx["FxComp1"]
    primary, secondary = names.row_label(doc.page, i, doc.page.objects[i])
    assert primary == "FxValveC  FxPlant.Valve1" and secondary == "Composite genie (2)"


def test_an_equipment_binding_reads_by_its_last_part(fx_install):
    """A composite genie whose content binds Equipment = 'FxPlant.Area.FxSpray02': its row
    reads '<template>  FxSpray02' (with the cache) and ('FxSpray02', 'Composite genie (n)')
    without it, the Tag column 'FxSpray02'. FxComposites' FxComp9 (FxStatusC: its content
    fxparts.fx_alarm binds Equipment from the template's Equipment)."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.ui import names

    path, idx = _prepared(fx_install, "FxComposites")
    doc = EditableDocument.open(path)
    i = idx["FxComp9"]
    primary, secondary = names.row_label(doc.page, i, doc.page.objects[i])
    assert primary == "FxStatusC  FxSpray02" and secondary.startswith("Composite genie (")
    cache = os.path.join(
        fx_install.project("FxMain"), "_CompositionCaches", "{B0C1D2E3-F405-4A16-8B27-C3D4E5F60718}"
    )
    assert os.path.isdir(cache)
    shutil.rmtree(cache)
    libraries_changed()
    doc = EditableDocument.open(path)
    primary, secondary = names.row_label(doc.page, i, doc.page.objects[i])
    assert primary == "FxSpray02" and secondary.startswith("Composite genie (")
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    assert ObjectsDock._column_value(doc.page, i, doc.page.objects[i], "tag") == "FxSpray02"


def test_the_pump_reads_pump_b_d100_at_268_px(win, fx_install):
    doc, idx = _open(win, fx_install)
    win.resizeDocks([win.dock_objects], [268], QtCore.Qt.Horizontal)
    _pump()
    dock = win.objects_dock
    i = idx[PUMP]
    an = doc.page.objects[i].an
    look = dock._looks[i]
    assert (look.primary, look.secondary, look.an) == ("fx_pump  FX_PUMP3", "Genie (3)", "AN %d" % an)
    assert "🔒" not in "".join(lk.text for lk in dock._looks.values())
    text = _zone(dock, i, "text")
    fm = QtGui.QFontMetrics(dock._tree.font())
    # (the kind's icon, then the whole primary label fits)
    assert fm.horizontalAdvance(look.primary) + 20 <= text.width()
    # the row's whole text for a screen reader and the keywords role for the search
    item = dock._items[i]
    assert item.text(0) == "fx_pump  FX_PUMP3  Genie (3)  AN %d" % an
    from ctgedit.ui.panels.objects_dock import KEYWORDS_ROLE

    assert "Tag=FX_PUMP3" in item.data(0, KEYWORDS_ROLE)


def test_classic_labels_and_no_an_and_columns(win, fx_install):
    from ctgedit.ui.panels.objects_dock import _row_text
    from ctgedit.ui.prefs.settings import app_settings

    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    i = idx[PUMP]
    app_settings().setValue("objects/row_label", "classic")
    win._objects_panel_settings()
    assert dock._items[i].text(0) == _row_text(i, doc.page.objects[i], doc.page)
    assert dock._looks[i].secondary == "" and not dock.show_an_column()
    app_settings().setValue("objects/row_label", "smart")
    app_settings().setValue("objects/show_an", False)
    win._objects_panel_settings()
    assert dock._looks[i].an == "" and "an" not in dock._delegate.zones(
        QtCore.QRect(0, 0, 200, 20), i, dock._looks[i].flags
    )
    app_settings().setValue("objects/show_an", True)
    dock.set_columns(["object", "tag", "an"])
    assert app_settings().value("objects/columns", type=list) == ["object", "an", "tag"]
    assert not dock._tree.isHeaderHidden() and dock._tree.columnCount() == 3
    assert dock._items[i].text(1) == str(doc.page.objects[i].an)
    assert dock._items[i].text(2) == "FX_PUMP3"
    assert dock._tree.headerItem().text(1) == "AN"
    dock.set_columns(["object"])
    assert dock._tree.isHeaderHidden() and dock._tree.columnCount() == 1


# --------------------------------------------------------------------------- rename
def test_f2_renames_in_one_undo_step_and_a_bad_name_keeps_the_editor(win, fx_install):
    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    i = idx[PUMP]
    win._on_canvas_selection([i])
    tree.setFocus()
    _pump()
    steps = len(doc._undo)
    QTest.keyClick(tree, QtCore.Qt.Key_F2)
    _pump()
    assert tree.state() == QtWidgets.QAbstractItemView.EditingState
    ed = tree.findChild(QtWidgets.QLineEdit)
    assert ed is not None and ed.placeholderText() == "Object name"
    # a name the Graphics Builder refuses: framed, said, the editor stays
    ed.setText("Tank 1")
    ed.textEdited.emit("Tank 1")
    from ctgedit.ui.style.theme import is_invalid

    assert is_invalid(ed) and "not a valid object name" in ed.toolTip()
    QTest.keyClick(ed, QtCore.Qt.Key_Return)
    _pump()
    assert tree.state() == QtWidgets.QAbstractItemView.EditingState
    assert doc.object_name(i) == "" and "valid" in win.statusBar().currentMessage()
    ed.setText("Tank1")
    ed.textEdited.emit("Tank1")
    assert not is_invalid(ed)
    QTest.keyClick(ed, QtCore.Qt.Key_Return)
    _pump()
    assert doc.object_name(i) == "Tank1" and len(doc._undo) == steps + 1
    # the row leads with the name now, the element and tag after it
    look = dock._looks[i]
    assert look.primary == "Tank1" and "fx_pump  FX_PUMP3" in look.secondary
    win.undo()
    assert doc.object_name(i) == ""
    # a name another top-level object has is refused
    j = idx[OTHER]
    doc.set_object_name(j, "Pump")
    assert "already used" in win._tree_name_problem(i, "pump")


def test_genie_members_and_the_template_are_not_renamed(win, fx_install):
    from ctgedit.ui.panels.objects_dock import PART_RENAME_TIP, TEMPLATE_TIP

    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    g = idx[PUMP]
    member = doc.page.objects[g].members[0]
    assert not dock.start_rename(member)
    assert win.statusBar().currentMessage() == PART_RENAME_TIP
    assert not dock._items[member].flags() & QtCore.Qt.ItemIsEditable
    assert dock._items[g].flags() & QtCore.Qt.ItemIsEditable
    # the menu's Rename is off there, with the reason
    win._on_dock_selection([member])
    dock.set_selection([member])
    menu = dock.build_menu()
    ren = next(a for a in menu.actions() if a.text() == "Rename…")
    assert not ren.isEnabled() and ren.toolTip() == PART_RENAME_TIP
    # the template (FxTplLinked's normal@XGA) is not renamed, a genie beside it is
    _doc, tpl, pump = _template_doc(win, fx_install)
    assert dock.rename_refusal(tpl) == TEMPLATE_TIP
    assert not dock.rename_refusal(pump)


def test_a_slow_second_click_on_the_name_renames(win, fx_install):
    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    i = idx[PUMP]
    win._on_canvas_selection([i])
    pos = _zone(dock, i, "text").center()
    QTest.mouseClick(tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pos)
    assert tree._rename_timer.isActive()
    # (the rename follows after objects/rename_click_ms, 600 ms; a busy run is later)
    for _ in range(60):
        QTest.qWait(50)
        if tree.state() == QtWidgets.QAbstractItemView.EditingState:
            break
    assert tree.state() == QtWidgets.QAbstractItemView.EditingState
    QTest.keyClick(tree.findChild(QtWidgets.QLineEdit), QtCore.Qt.Key_Escape)


def _valves(doc):
    """FxGenies' eight fx_valve genies (FxValve1 .. 8, two rows of four)."""
    pg = doc.page
    return [i for i in pg.roots() if ".fx_valve." in (pg.objects[i].genie_template or "")]


def test_rename_objects_names_eight_valves_in_reading_order(win, fx_install):
    """'Valve_{n:02}' on FxGenies' 8 fx_valve genies: Valve_01 .. Valve_08 from the top
    left, one undo step, the same bytes as renaming them one by one."""
    from ctgedit.ui.dialog.rename_dialog import RenameObjectsDialog, reading_order

    doc, idx = _open(win, fx_install)
    valves = _valves(doc)
    assert len(valves) == 8
    win._on_canvas_selection(valves)
    dlg = RenameObjectsDialog(win, doc, list(win._sel), refusal=win._rename_refusal_of)
    dlg._mode.setCurrentIndex(0)
    dlg._pattern.setText("Valve_{n:02}")
    dlg._order.setCurrentIndex(0)
    dlg.flush()  # (the preview follows typing after a pause)
    assert dlg._ok.isEnabled()
    order = reading_order(doc.page, valves)
    ys = [doc.page.objects[i].geometry.y for i in order]
    assert ys == sorted(ys, key=lambda y: round(y / 50))  # rows from the top
    assert order == [idx["FxValve%d" % k] for k in range(1, 9)]  # (left to right in each)
    steps = len(doc._undo)
    dlg._apply()
    assert dlg.renamed == 8 and len(doc._undo) == steps + 1
    assert [doc.object_name(i) for i in order] == ["Valve_%02d" % k for k in range(1, 9)]
    got = doc.serialize()
    # the same names one by one, on a fresh copy: the same bytes
    from ctgedit.editing import EditableDocument

    fresh = EditableDocument.open(doc.path)
    for k, i in enumerate(order, 1):
        fresh.set_object_name(i, "Valve_%02d" % k)
    assert fresh.serialize() == got
    win.undo()
    assert all(doc.object_name(i) == "" for i in valves)
    dlg.close()


def test_rename_objects_swaps_names_and_finds_and_replaces(win, fx_install):
    from ctgedit.ui.dialog.rename_dialog import (
        RenameObjectsDialog,
        apply_renames,
        plan_problems,
        replaced_name,
        pattern_name,
    )

    doc, idx = _open(win, fx_install)
    a, b = _valves(doc)[:2]
    doc.set_object_name(a, "Inlet")
    doc.set_object_name(b, "Outlet")
    assert apply_renames(doc, [(a, "Outlet"), (b, "Inlet")]) == 2
    assert (doc.object_name(a), doc.object_name(b)) == ("Outlet", "Inlet")
    # two objects given one name, a bad name, a name an object not renamed has
    c = _valves(doc)[2]
    probs = plan_problems(doc, [(a, "Same"), (c, "same")])
    assert a in probs and c in probs
    assert "valid" in plan_problems(doc, [(c, "1st")])[c]
    assert "already used" in plan_problems(doc, [(c, "Outlet")])[c]
    with pytest.raises(ValueError):
        apply_renames(doc, [(c, "Outlet")])
    assert replaced_name("Valve_01", "valve_", "V_", False, False) == "V_01"
    assert replaced_name("Valve_01", r"_(\d+)", r"\1x", True, True) == "Valve01x"
    assert pattern_name(doc.page, c, "{element}_{n}", 3) == "fx_valve_3"
    with pytest.raises(ValueError):
        pattern_name(doc.page, c, "{nope}", 1)
    # the dialog's find and replace, with a conflict in red
    win._on_canvas_selection([a, b])
    dlg = RenameObjectsDialog(win, doc, [a, b])
    dlg._mode.setCurrentIndex(1)
    dlg._find.setText("Out")
    dlg._repl.setText("In")
    dlg.flush()
    assert not dlg._ok.isEnabled() and "two objects" in dlg._status.text()
    assert dlg._table.item(0, 2).toolTip()
    dlg._find.setText("let")
    dlg._repl.setText("Pipe")
    dlg.flush()
    assert dlg._ok.isEnabled()
    assert [dlg._table.item(r, 2).text() for r in range(2)] == ["OutPipe", "InPipe"]
    dlg.close()


# --------------------------------------------------------------------------- eye and padlock
def test_the_padlock_locks_in_one_undo_step_and_the_template_lock_is_fixed(win, fx_install):
    from ctgedit.ui.panels.objects_dock import TEMPLATE_TIP, F_LOCKED

    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    i = idx[PUMP]
    steps = len(doc._undo)
    QTest.mouseClick(
        tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _zone(dock, i, "lock").center()
    )
    _pump()
    assert (doc.page.objects[i].raw.get("_dwflags") or 0) & 0x4
    assert len(doc._undo) == steps + 1 and dock._looks[i].flags & F_LOCKED
    assert "locked" in dock._items[i].data(0, QtCore.Qt.AccessibleDescriptionRole)
    assert dock.tip_for(i, "lock").startswith("Locked")
    win.undo()
    assert not (doc.page.objects[i].raw.get("_dwflags") or 0) & 0x4
    # a member row has an eye and no padlock; the side is a setting
    m = doc.page.objects[i].members[0]
    z = dock._delegate.zones(QtCore.QRect(0, 0, 240, 20), m, dock._looks[m].flags)
    assert z["lock"] is None and z["eye"].left() > 200
    dock.toggles_side = "left"
    z = dock._delegate.zones(QtCore.QRect(0, 0, 240, 20), i, dock._looks[i].flags)
    assert z["eye"].left() < 5 and z["text"].left() > z["lock"].right()
    dock.toggles_side = "right"
    # the template's lock is fixed (FxTplLinked's normal@XGA)
    _tdoc, tpl, _pump_i = _template_doc(win, fx_install)
    QTest.mouseClick(
        tree.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _zone(dock, tpl, "lock").center()
    )
    assert win.statusBar().currentMessage() == TEMPLATE_TIP
    assert dock.tip_for(tpl, "lock") == TEMPLATE_TIP


def test_eye_alt_click_drag_space_and_member_eyes(win, fx_install):
    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    vp = tree.viewport()
    roots = [r for r in doc.page.roots() if r in dock._items][3:9]
    # a drag from one eye over the next four: five rows hidden
    first = _zone(dock, roots[0], "eye").center()
    _mouse(vp, "press", first)
    for r in roots[1:5]:
        _mouse(vp, "move", QtCore.QPoint(first.x(), _rect(dock, r).center().y()))
    _mouse(vp, "release", QtCore.QPoint(first.x(), _rect(dock, roots[4]).center().y()))
    assert set(roots[:5]) <= win._hidden_roots and len(win._hidden_roots) == 5
    assert win._hidden_chip.text().startswith("5 hidden")
    for r in roots[:5]:
        assert dock._items[r].data(0, QtCore.Qt.AccessibleDescriptionRole).startswith("Hidden")
    win._show_all_roots()
    # Alt+click: that one alone, again: all
    target = roots[2]
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, _zone(dock, target, "eye").center())
    others = {r for r in doc.page.roots() if r != target and not doc.page.objects[r].raw.get("_non_drawable")}
    assert win._hidden_roots == others
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, _zone(dock, target, "eye").center())
    assert win._hidden_roots == set()
    # Space in the panel: Hide or Show Selection
    win._on_canvas_selection([target])
    dock.set_selection([target])
    tree.setFocus()
    QTest.keyClick(tree, QtCore.Qt.Key_Space)
    assert win._hidden_roots == {target}
    QTest.keyClick(tree, QtCore.Qt.Key_Space)
    assert win._hidden_roots == set()
    # a genie member's eye hides that member only (editor-only)
    g = idx[LABELLED]
    m = doc.page.objects[g].members[0]
    dock._items[g].setExpanded(True)
    _pump()
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, _zone(dock, m, "eye").center())
    assert win._hidden_roots == {m}
    shown = [i for i in doc.page.subtree(g) if i not in doc.page.subtree(m) and i in win.scene._items]
    assert not win.scene._items[m].isVisible() if m in win.scene._items else True
    assert all(win.scene._items[i].isVisible() for i in shown)


# --------------------------------------------------------------------------- the menu
def test_the_menu_is_the_windows_commands_with_their_keys(win, fx_install):
    from ctgedit.ui.panels.objects_dock import ObjectsDock, PART_TIP
    from ctgedit.ui.prefs.settings import app_settings

    assert not hasattr(ObjectsDock, "isolateRequested")
    assert not hasattr(ObjectsDock, "showAllRequested")
    assert not hasattr(win, "_isolate")
    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    i = idx[PUMP]
    win._on_canvas_selection([i])
    dock.set_selection([i])
    menu = dock.build_menu()
    acts = [a for a in menu.actions() if not a.isSeparator()]
    assert win.act_dup in acts and win.act_dup.shortcut().toString() == "Ctrl+D"
    for a in (
        win.act_obj_props,
        win.act_genie_params,
        win.act_rename,
        win.act_cut,
        win.act_copy,
        win.act_del,
        win.act_front,
        win.act_back,
        win.act_group,
        win.act_lock_toggle,
        win.act_isolate,
        win.act_show_all_roots,
        win.act_zoom_sel,
    ):
        assert a in acts, a.text()
    assert any(a.text().startswith("Edit Definition (fxgenie.fx_pump") for a in acts)
    # Isolate from the panel toggles back, and is remembered
    win.act_isolate.trigger()
    assert win._hidden_roots and i not in win._hidden_roots
    assert app_settings().value(win._hidden_key(), [], type=list)
    win.act_isolate.trigger()
    assert win._hidden_roots == set()
    # a genie member: the order and Delete off, with the reason
    m = doc.page.objects[i].members[0]
    win._on_dock_selection([m])
    dock.set_selection([m])
    menu = dock.build_menu()
    off = {a.text().split("\t")[0]: a for a in menu.actions() if not a.isEnabled()}
    for name in ("Bring to Front", "Send to Back", "Delete"):
        assert off[name].toolTip() == PART_TIP
    # (a disabled copy still shows its command's key, as text: it never takes the key)
    keys = win.act_del.shortcut().toString(QtGui.QKeySequence.NativeText)
    assert off["Delete"].text() == "Delete\t" + keys and off["Delete"].shortcut().isEmpty()
    assert off["Delete"].objectName() == "tree.local"


# --------------------------------------------------------------------------- search, filter
def test_searching_d100_lists_the_genies_bound_to_it(win, fx_install):
    """The three genies on FX_PUMP3 (FxPump3, FxPump2 rebound by the copy, FxLamp); the
    field words on their ANs and on fx_lamp, the element only FxLamp places."""
    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    an = {name: doc.page.objects[idx[name]].an for name in (PUMP, LABELLED, LAMP, HIDDEN)}
    dock._search.setText("FX_PUMP3")
    dock._search.flush()
    listed = {doc.page.objects[i].an for i in dock._listed}
    assert {an[PUMP], an[LABELLED], an[LAMP]} <= listed
    assert dock._count.text().endswith("of %d" % len(dock._items))
    assert dock._select_btn.text() == "Select %d" % len(dock.listed())
    # field words
    for text, want in (
        ("an:%d" % an[PUMP], {an[PUMP]}),
        ("kind:genie an:%d" % an[LAMP], {an[LAMP]}),
        ("lib:fx_lamp", {an[LAMP]}),
    ):
        dock._search.setText(text)
        dock._search.flush()
        assert {doc.page.objects[i].an for i in dock._listed} == want, text
    win._on_hide_toggled(idx[HIDDEN], True)
    dock._search.setText("is:hidden")
    dock._search.flush()
    assert [doc.page.objects[i].an for i in dock._listed] == [an[HIDDEN]]
    dock._search.setText("")
    dock._search.flush()
    assert not dock._count_row.isVisibleTo(dock)


def test_tag_search_on_mixer_finds_the_valves(win, fx_install):
    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    dock._search.setText("tag:FX_V")
    dock._search.flush()
    listed = set(dock._listed)
    assert set(_valves(doc)) <= listed  # (TagPrefix = FX_V1 .. FX_V8)


def test_the_filter_lists_kinds_and_states_and_is_remembered(win, fx_install):
    from ctgedit.ui.prefs.settings import app_settings

    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    counts = dock.filter_counts()
    texts = sum(
        1
        for o in doc.page.objects
        if o.kind.value == "text" and not o.raw.get("_non_drawable") and not o.raw.get("_pic_bytes")
    )
    assert counts["kind:text"] == texts
    dock.open_filter()
    pop = dock._popover
    assert pop.boxes["kind:genie"].text() == "Genies (%d)" % counts["kind:genie"]
    pop.boxes["kind:text"].setChecked(True)
    assert dock.filters() == ["kind:text"]
    assert app_settings().value("objects/filter", type=list) == ["kind:text"]
    assert "on" in dock._filter_action.toolTip()
    shown = [i for i, it in dock._items.items() if not it.isHidden() and dock._looks[i].kind_key == "text"]
    assert len(shown) == texts
    # the ancestors of a passing member stay
    for i in shown:
        p = doc.page.objects[i].parent
        if p is not None:
            assert not dock._items[p].isHidden()
    pop._clear()
    assert dock.filters() == [] and all(not it.isHidden() for it in dock._items.values())
    pop.close()


def test_clearing_a_search_closes_the_rows_it_opened(win, fx_install):
    """On FxComposites: the search 'Valve1' opens FxComp1's row (Equipment FxPlant.Valve1);
    FxComp7's row, opened by the user before, is closed while the search runs and open
    again after it."""
    doc, idx = _open(win, fx_install, "FxComposites")
    dock = win.objects_dock
    c = idx["FxComp1"]
    other = idx["FxComp7"]
    dock._items[other].setExpanded(True)  # opened by the user before
    dock._search.setText("Valve1")
    dock._search.flush()
    assert dock._items[c].isExpanded()
    assert not dock._items[other].isExpanded()
    dock._search.setText("")
    dock._search.flush()
    assert not dock._items[c].isExpanded() and dock._items[other].isExpanded()
    # the matched part is bold
    from ctgedit.infra.textmatch import FieldQuery

    runs = dock._delegate._runs("FxDriveC  FxSpray02", FieldQuery("spray"))
    assert ("Spray", True) in runs


def test_field_query():
    from ctgedit.infra.textmatch import FieldQuery

    q = FieldQuery("kind:genie tag:FxMix_ pump")
    assert q.terms == [("kind", "genie"), ("tag", "fxmix_")] and q.free.text == "pump"
    f = {"kind": "genie Genie genie", "tag": "FxMix_Valve", "an": ("581",), "is": ()}
    assert q(f, "Pump row")
    assert not q(f, "valve row")
    assert FieldQuery("an:581")(f) and not FieldQuery("an:58")(f)
    assert FieldQuery("is:hidden")({"is": ("hidden",)}) and not FieldQuery("is:hidden")({})
    assert FieldQuery('name:"Tank 1"').terms == [("name", "tank 1")]
    # no field words: the text as typed, spaces and all
    assert FieldQuery("Composite  genie").free.text == "Composite  genie"
    assert FieldQuery("http://x").terms == [] and FieldQuery("").empty


# --------------------------------------------------------------------------- keys
def test_ctrl_a_in_a_filtered_panel_selects_only_what_it_lists(win, fx_install):
    """'fx_valve' in the search, Ctrl+A in the tree: 8 roots."""
    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    dock._search.setText("fx_valve")
    dock._search.flush()
    tree.setFocus()
    _pump()
    QTest.keyClick(tree, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
    _pump()
    assert sorted(win._sel) == sorted(_valves(doc))
    # Enter in the search and Select N do the same
    win._on_canvas_selection([])
    dock._search.setFocus()
    QTest.keyClick(dock._search, QtCore.Qt.Key_Return)
    assert sorted(win._sel) == sorted(_valves(doc))
    # without a search: the top-level objects that can be selected (as Edit > Select All)
    dock._search.setText("")
    dock._search.flush()
    tree.setFocus()
    QTest.keyClick(tree, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
    want = [r for r in doc.page.roots() if r in dock._items and not win._unit_locked(r)]
    assert sorted(win._sel) == sorted(want)


def test_enter_shift_enter_and_f_in_the_panel(win, fx_install, monkeypatch):
    doc, idx = _open(win, fx_install)
    dock, tree = win.objects_dock, win.objects_dock._tree
    g = idx[PUMP]
    seen = []
    monkeypatch.setattr(win, "_edit_genie", lambda i, o: seen.append(("genie", i)))
    monkeypatch.setattr(win, "_open_sheet_of", lambda i: seen.append(("sheet", i)))
    framed = []
    monkeypatch.setattr(win, "_frame_index", lambda i: framed.append(i))
    win._on_canvas_selection([g])
    dock.set_selection([g])
    tree.setFocus()
    QTest.keyClick(tree, QtCore.Qt.Key_Return)
    assert seen == [("genie", g)]
    m = doc.page.objects[g].members[0]
    dock.set_selection([m])
    win._on_dock_selection([m])
    QTest.keyClick(tree, QtCore.Qt.Key_Return)
    assert seen[-1] == ("sheet", m)
    QTest.keyClick(tree, QtCore.Qt.Key_Return, QtCore.Qt.ShiftModifier)
    assert win._sel == [g]
    QTest.keyClick(tree, QtCore.Qt.Key_F)
    assert framed and framed[-1] in (g, m)
    assert dock._search.text() == ""  # (F framed, it did not search)
    QTest.keyClick(tree, QtCore.Qt.Key_P)
    assert dock._search.text() == "p"  # other letters still search


# --------------------------------------------------------------------------- the theme
def _eye_pixels(img, rect):
    out = []
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            c = img.pixelColor(x, y)
            out.append(c)
    return out


def test_the_eyes_take_the_new_theme(win, fx_install):
    """Dark to light with a page open: a shown eye is drawn dark on the light panel
    and the hidden eye differs from it."""
    from ctgedit.ui.style import theme as T
    from ctgedit.ui.prefs.settings import app_settings

    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    app_settings().setValue("ui/tree_eye", "always")
    win._objects_panel_settings()
    hidden, shown = idx[HIDDEN], idx[SHOWN]
    win._on_hide_toggled(hidden, True)
    try:
        T.apply_theme(_app(), "light", 1.0)
        _pump()
        r_shown, r_hidden = _zone(dock, shown, "eye"), _zone(dock, hidden, "eye")
        img = dock._tree.viewport().grab().toImage()
        bg = QtGui.QColor(T.COLORS["bg"])

        def ink(rect):
            px = [c for c in _eye_pixels(img, rect) if abs(c.lightness() - bg.lightness()) > 30]
            return px

        a, b = ink(r_shown), ink(r_hidden)
        assert a and b
        assert min(c.lightness() for c in a) < 160  # a dark eye on the light panel
        assert len(a) != len(b) or [c.rgb() for c in a] != [c.rgb() for c in b]
    finally:
        T.apply_theme(_app(), "dark", 1.0)
        app_settings().setValue("ui/tree_eye", "hover")


# --------------------------------------------------------------------------- resumed checks
def _tip_at(widget, pos):
    """The tool tip a ToolTip event at ``pos`` of ``widget`` shows ('' for none)."""
    QtWidgets.QToolTip.hideText()
    _pump(2)
    ev = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, pos, widget.mapToGlobal(pos))
    QtWidgets.QApplication.sendEvent(widget, ev)
    _pump(2)
    return QtWidgets.QToolTip.text() if QtWidgets.QToolTip.isVisible() else ""


def test_the_eye_tip_shows_with_columns_and_after_them(win, fx_install):
    """The panel's own tips (the eye's, the padlock's and the row's) come first, not the column
    helper's cut cell text, also after the columns go."""
    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    i = idx[PUMP]
    vp = dock._tree.viewport()
    want = dock.tip_for(i, "eye")
    assert _tip_at(vp, _zone(dock, i, "eye").center()) == want
    dock.set_columns(["object", "name", "tag"])
    _pump()
    assert _tip_at(vp, _zone(dock, i, "eye").center()) == want
    assert _tip_at(vp, _zone(dock, i, "lock").center()) == dock.tip_for(i, "lock")
    dock.set_columns(["object"])
    _pump()
    assert _tip_at(vp, _zone(dock, i, "eye").center()) == want
    assert _tip_at(vp, _zone(dock, i, "text").center()).startswith("fx_pump  FX_PUMP3")


def test_columns_fit_the_panel_and_a_new_set_is_fitted_again(win, fx_install):
    from ctgedit.ui import tables
    from ctgedit.ui.prefs.settings import app_settings

    _open(win, fx_install)
    dock = win.objects_dock
    tree = dock._tree
    win.resizeDocks([win.dock_objects], [268], QtCore.Qt.Horizontal)
    dock.set_columns(["object", "name", "tag"])
    _pump(12)
    hdr = tree.header()

    def fits():
        return (
            sum(hdr.sectionSize(c) for c in range(hdr.count())) <= tree.viewport().width() + 1
            and tree.horizontalScrollBar().maximum() == 0
        )

    assert fits() and hdr.sectionSize(0) >= 120
    narrow = hdr.sectionSize(0)
    win.resizeDocks([win.dock_objects], [420], QtCore.Qt.Horizontal)
    _pump(12)
    assert fits() and hdr.sectionSize(0) > narrow  # the Object column takes the room
    # widths the user dragged stay (remembered), and give way when they do not fit the
    # panel; another set of columns is fitted afresh
    state = tables.state_of(tree)
    state.user = tables.parse_widths("300,90,100,v400")
    state.schedule()
    _pump(12)
    assert tables.has_user_widths(tree) and fits()
    assert hdr.sectionSize(1) == 90 and 100 <= hdr.sectionSize(2) <= 102  # (the last fills)
    app_settings().setValue(tables.settings_key("ObjectsDock/objects"), "300,90,100,v400")
    dock.set_columns(["object", "kind"])
    _pump(12)
    assert not tables.has_user_widths(tree) and fits()
    assert not app_settings().value(tables.settings_key("ObjectsDock/objects"))
    dock.set_columns(["object"])


def test_the_an_column_is_as_wide_as_the_largest_an(win, fx_install):
    doc, idx = _open(win, fx_install)
    dock = win.objects_dock
    top = max(o.an for o in doc.page.objects)
    digits = max(3, len(str(top)))
    assert dock.an_digits() == digits
    fm = QtGui.QFontMetrics(dock._tree.font())
    assert dock._delegate._an_width() == fm.horizontalAdvance("AN " + "0" * digits)
    i = idx[PUMP]
    assert _zone(dock, i, "an").width() == fm.horizontalAdvance("AN " + "0" * digits)


def test_a_composite_genie_without_its_cache_leads_with_its_binding(fx_install):
    """A composite genie without its cache leads with its binding."""
    _app()
    path, idx = _prepared(fx_install, "FxComposites")
    d = fx_install.new_project("FxNoCache")
    dst = os.path.join(d, "FxComposites.ctg")
    shutil.copyfile(path, dst)
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import names
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc = EditableDocument.open(dst)
    i = idx["FxComp1"]
    o = doc.page.objects[i]
    assert o.kind.value == "composite_genie"
    assert names.element_label(o, doc.page).startswith("instance ")  # (the Element column)
    assert names.row_label(doc.page, i, o) == ("FxPlant.Valve1", "Composite genie (2)")
    assert ObjectsDock._column_value(doc.page, i, o, "tag") == "FxPlant.Valve1"


def test_rename_objects_lists_each_object_as_its_row_reads(win, fx_install):
    from ctgedit.ui.dialog.rename_dialog import RenameObjectsDialog

    doc, idx = _open(win, fx_install)
    valves = _valves(doc)[:3]
    dlg = RenameObjectsDialog(win, doc, valves)
    for r in range(dlg._table.rowCount()):
        text = dlg._table.item(r, 0).text()
        assert text.startswith("fx_valve") and " AN " in text
    dlg.close()


def test_rename_objects_takes_enter_straight_after_typing(win, fx_install):
    """Enter before the preview's pause renames at once when the pattern is valid, and keeps
    the window open with the problem shown when it is not."""
    from PySide6.QtTest import QTest

    from ctgedit.ui.dialog.rename_dialog import RenameObjectsDialog

    doc, _idx = _open(win, fx_install)
    two = _valves(doc)[:2]
    win._on_canvas_selection(two)
    # a pattern that gives both the same name: not renamed, the window stays
    dlg = RenameObjectsDialog(win, doc, list(two))
    dlg._mode.setCurrentIndex(0)
    dlg.show()
    dlg._pattern.setFocus()
    dlg._pattern.clear()
    QTest.keyClicks(dlg._pattern, "Same")
    QTest.keyClick(dlg._pattern, QtCore.Qt.Key_Return)
    assert dlg.isVisible() and not dlg.renamed and dlg._status.isVisible()
    # a valid one: renamed on the first Enter
    dlg._pattern.clear()
    QTest.keyClicks(dlg._pattern, "Audit{n}")
    QTest.keyClick(dlg._pattern, QtCore.Qt.Key_Return)
    assert dlg.renamed == 2 and not dlg.isVisible()
    assert sorted(doc.object_name(i) for i in two) == ["Audit1", "Audit2"]
