"""Group properties and member access."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

CTRL = QtCore.Qt.ControlModifier
NOMOD = QtCore.Qt.NoModifier
#: the page of the member tests and the page on a template
MEMBERS = "FxMembers"
TEMPLATE_PAGE = "FxTplLinked"
#: FxPair's AN and FxInputOwner's (the AN its touch and keyboard records share)
PAIR_AN = 3
INPUT_AN = 13


def _window(path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(path)
    w.resize(1400, 1000)
    w.show()
    QtWidgets.QApplication.processEvents()
    return w


def _close(w):
    from ctgedit.ui.main_window import MainWindow

    for d in list(w._dialogs.values()):
        d.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.doc.dirty = False
    w.close()


@pytest.fixture()
def win(fx_page, monkeypatch):
    """A window showing FxMembers (in the fake install: its libraries resolve through
    FxMain's includes)."""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")  # every re-derive == a full one
    w = _window(fx_page(MEMBERS))
    yield w
    _close(w)


@pytest.fixture()
def tplwin(fx_page, monkeypatch):
    """A window showing FxTplLinked (a page on fx_style normal@XGA)."""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    w = _window(fx_page(TEMPLATE_PAGE))
    yield w
    _close(w)


def _named(doc, name):
    (i,) = doc.find_objects_by_name(name)
    return i


def _owner(doc):
    """FxInputOwner and its touch record."""
    i = _named(doc, "FxInputOwner")
    touch = next(k for k in doc.subtree(i) if doc.page.objects[k].raw.get("_type_code") == 0x1010)
    return i, touch


def _dblclick(view, sp, mods=NOMOD):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    view.mouseDoubleClickEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QPointF(vp),
            QtCore.QPointF(g),
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            mods,
        )
    )
    QtWidgets.QApplication.processEvents()


def _spot(w, pred):
    """(scene point, the object the canvas hits there) of the first drawn
    object ``pred(index)`` accepts that is the TOPMOST hit at its centre."""
    view, sc = w.view, w.view.scene()
    for i in sorted(sc._items):
        if not pred(i):
            continue
        r = sc._items[i].sceneBoundingRect()
        if r.width() < 4 or r.height() < 4:
            continue
        c = r.center()
        vp = view.mapFromScene(c)
        if not view.viewport().rect().contains(vp):
            continue
        hits = view._hits_under(vp)
        if hits and hits[0] == i:
            return (c.x(), c.y()), i
    return None, None


def _genie_member(w, i):
    from ctgedit.ui.objprops.registry import placed_genie_of

    pg = w.doc.page
    g = placed_genie_of(w.doc, i)
    return (
        g is not None
        and not pg.objects[i].members
        and not pg.objects[w.doc.root_of(i)].raw.get("_from_template")
        and (pg.objects[g].raw.get("_dwflags") or 0) & 0x1
    )


# ------------------------------------------------------------------ double-click
def test_double_click_group_opens_its_sheet(win):
    """Group 0 (FxPair, a plain group, AN 3): a double-click opens the GROUP sheet
    -- the group pages only, no Appearance > General (line / fill)."""
    pg = win.doc.page
    assert _named(win.doc, "FxPair") == 0
    assert pg.objects[0].kind.value == "group" and len(pg.objects[0].members) == 2
    sp, hit = _spot(win, lambda i: win.doc.root_of(i) == 0)
    assert hit is not None
    _dblclick(win.view, sp)
    dlg = win._dialogs.get(("objprops", 0))
    assert dlg is not None, "the group's sheet did not open"
    assert dlg.windowTitle() == "Group Properties (AN %d)" % PAIR_AN
    assert not dlg.is_read_only()
    ids = dlg.page_ids()
    assert "general" not in ids and "effects" not in ids
    assert {"visibility", "move_h", "fill_color", "access"} <= set(ids)
    assert win.view.selection_roots() == [0]
    # the Properties panel shows no Appearance box for a group (no own look)
    boxes = [b.title() for b in win.props.findChildren(QtWidgets.QGroupBox)]
    assert "Appearance" not in boxes and "Geometry" in boxes


def test_ctrl_double_click_opens_a_group_member(win):
    """Ctrl+double-click on a member of group 0 opens THAT member's sheet
    (editable: a plain group's members are the user's) and selects it."""
    sp, hit = _spot(win, lambda i: win.doc.root_of(i) == 0 and i != 0 and not win.doc.page.objects[i].members)
    assert hit is not None
    _dblclick(win.view, sp, CTRL)
    dlg = win._dialogs.get(("objprops", hit))
    assert dlg is not None and ("objprops", 0) not in win._dialogs
    assert not dlg.is_read_only()
    assert win._sel == [hit]
    assert not win.props.is_read_only()
    # the Objects tree highlights the member too
    rows = [it.data(0, QtCore.Qt.UserRole) for it in win.objects_dock._tree.selectedItems()]
    assert rows == [hit]


def test_ctrl_double_click_genie_member_is_read_only(win):
    """Ctrl+double-click on a genie member opens a read-only sheet."""
    sp, hit = _spot(win, lambda i: _genie_member(win, i))
    assert hit is not None
    _dblclick(win.view, sp, CTRL)
    QtWidgets.QApplication.processEvents()  # the ribbon's selection sync
    dlg = win._dialogs.get(("objprops", hit))
    assert dlg is not None
    assert dlg.is_read_only() and "part of the genie" in dlg.read_only_reason
    # selecting it edits nothing: no 'not edited' warning yet
    assert "not edited" not in win.statusBar().currentMessage()
    bb = dlg._buttons
    assert dlg.is_partly_editable()
    assert bb.button(QtWidgets.QDialogButtonBox.Ok) is not None
    assert bb.button(QtWidgets.QDialogButtonBox.Close) is None
    assert dlg._btn_definition.isVisibleTo(dlg)
    defn = win._owner_definition(hit)
    assert defn is not None and defn[0].lower().endswith(".ctm")
    # the panel: banner + Edit Definition + every editor read-only
    assert win._sel == [hit] and win.props.is_read_only()
    # (the form shown: the panel also keeps the page's own form, hidden)
    form = win.props.widget()
    btns = [b for b in form.findChildren(QtWidgets.QPushButton) if b.isEnabled()]
    # (the element is named in the tool tip, so a long name does not overflow the dock)
    assert any(
        b.text() == "Edit Definition" and ".ctm" not in b.text() and b.toolTip().startswith("Open ")
        for b in btns
    )
    for ed in form.findChildren(QtWidgets.QLineEdit):
        assert ed.isReadOnly()
    for sp_ in form.findChildren(QtWidgets.QSpinBox):
        assert sp_.isReadOnly()
    # Edit Definition opens the genie's element in its own tab
    opened = []
    win._open_element = lambda path, el, kind="": opened.append((path, el))
    win._edit_owner_definition(hit)
    assert opened == [defn]


def test_plain_double_click_on_genie_member_still_opens_the_genie(win):
    """Without Ctrl a click inside a genie acts on the genie (its parameter
    dialog), never on the member."""
    sp, hit = _spot(win, lambda i: _genie_member(win, i))
    top = win.doc.root_of(hit)
    _dblclick(win.view, sp)
    assert ("objprops", hit) not in win._dialogs
    assert ("genie", top) in win._dialogs or ("objprops", top) in win._dialogs


def test_ctrl_double_click_template_record_is_read_only(tplwin):
    """The page template's records are locked: a double-click skips them, a
    Ctrl+double-click opens their sheet read-only (help 1172115) -- never
    the template's page-wide container itself."""
    win = tplwin
    pg = win.doc.page
    tpl, ref = win.doc.template_reference()
    assert tpl is not None and ref.raw == "fx_style.normal@XGA.<ctt"
    assert pg.objects[tpl].raw.get("_from_template") and pg.objects[tpl].parent is None
    sp, hit = _spot(win, lambda i: i != tpl and win.doc.root_of(i) == tpl and not pg.objects[i].members)
    assert hit is not None
    _dblclick(win.view, sp)
    assert all((kind, hit) not in win._dialogs for kind in ("objprops", "genie", "symset", "bitmap"))
    _dblclick(win.view, sp, CTRL)
    dlg = win._dialogs.get(("objprops", hit))
    assert dlg is not None and dlg.is_read_only()
    assert "template" in dlg.read_only_reason or "genie" in dlg.read_only_reason
    assert not win._member_reachable(tpl)
    # with templates unlocked (Break Lock Mode) a template record is editable
    dlg.reject()
    win._break_lock = True
    try:
        assert win._read_only_reason(hit) == "" or "genie" in win._read_only_reason(hit)
    finally:
        win._break_lock = False


# --------------------------------------------------------------- protection
def test_genie_member_alone_cannot_be_moved_or_deleted(win):
    """A placed genie's member selected on its own (Objects tree / Goto /
    Ctrl+double-click) is not moved, nudged or deleted -- CtDraw would
    re-compose it from the element -- while the whole genie still moves."""
    doc = win.doc
    pg = doc.page
    m = next(
        i
        for i in range(len(pg.objects))
        if _genie_member(win, i) and pg.objects[i].raw.get("_type_code") in (0x05, 0x0F, 0x01, 0x11, 0x10)
    )
    top = doc.root_of(m)
    g0 = (pg.objects[m].geometry.x, pg.objects[m].geometry.y)
    n0 = len(pg.objects)
    win._on_dock_selection([m])
    win._on_objects_moved(doc.subtree(m), 16, 0)
    assert (pg.objects[m].geometry.x, pg.objects[m].geometry.y) == g0
    assert "part of a genie" in win.statusBar().currentMessage()
    win._delete_indices([m])
    assert len(doc.page.objects) == n0
    # two members selected together: the panel changes neither
    other = next(i for i in doc.subtree(top) if i != m and _genie_member(win, i))
    win._on_dock_selection([m, other])
    notes = [lab.text() for lab in win.props.findChildren(QtWidgets.QLabel)]
    assert any("part of a genie and are not changed" in t for t in notes)
    win.props._on_multi_move("x", 10)
    assert (pg.objects[m].geometry.x, pg.objects[m].geometry.y) == g0
    # the whole genie moves (its members with it)
    win._on_objects_moved(doc.subtree(top), 16, 0)
    assert doc.page.objects[m].geometry.x == g0[0] + 16


def test_nested_genie_alone_is_protected_with_its_members(win):
    """A nested genie selected alone is protected with its members."""
    from ctgedit.ui.objprops.registry import genie_member_edits, placed_genies_of

    doc = win.doc
    objs = doc.parsed.page.objects
    n = next(
        i
        for i, po in enumerate(objs)
        if po.subtype_size & 0x200 and placed_genies_of(doc, i) and len(doc.subtree(i)) > 2
    )
    outer = placed_genies_of(doc, n)[-1]
    sub = doc.subtree(n)
    assert genie_member_edits(doc, sub) == set(sub)
    assert not genie_member_edits(doc, doc.subtree(outer))

    def where():
        pg = doc.page
        return {j: (pg.objects[j].geometry.x, pg.objects[j].geometry.y) for j in sub}

    before = where()
    win._break_lock = True  # (Break Lock Mode: only the genie rule protects them)
    try:
        win._on_dock_selection([n])
        win._on_objects_moved(sub, 16, 0)
        assert where() == before
        assert "part of a genie" in win.statusBar().currentMessage()
        win.nudge(1, 0)
        assert where() == before
        win._on_objects_moved(doc.subtree(outer), 16, 0)
        assert doc.page.objects[n].geometry.x == before[n][0] + 16
    finally:
        win._break_lock = False


def test_multi_selection_of_members_edits_the_members(win):
    """Two members of group 0 picked in the Objects tree: the panel's
    Animation fields set THEIR settings, not their group's."""
    doc = win.doc
    a, b = [m for m in doc.page.objects[0].members][:2]
    win._on_dock_selection([a, b])
    # the Tool tip row of the Animation box (both have it: no "(n of m)"; the tool tip
    # names no key)
    eds = []
    for form in win.props.findChildren(QtWidgets.QFormLayout):
        for r in range(form.rowCount()):
            lab = form.itemAt(r, QtWidgets.QFormLayout.LabelRole)
            fld = form.itemAt(r, QtWidgets.QFormLayout.FieldRole)
            if lab and lab.widget() and lab.widget().text() == "Tool tip" and fld:
                eds.append(fld.widget())
    assert len(eds) == 1
    assert eds[0].toolTip().startswith("Sets this on the 2 selected objects")
    eds[0].setText("Pump inlet")
    eds[0].editingFinished.emit()
    assert doc.object_properties(a).get("COMMENT") == "Pump inlet"
    assert doc.object_properties(b).get("COMMENT") == "Pump inlet"
    assert "COMMENT" not in doc.object_properties(0)


def test_panel_edit_genie_button_opens_that_genie(win):
    """'Edit genie parameters…' in the Properties panel of a genie placed
    INSIDE a plain group (FxInnerGenie in the group FxGenieInGroup) opens that
    genie's parameters -- not the group's sheet, which a double-click on the
    canvas opens."""
    w = win
    doc = w.doc
    genie, group = _named(doc, "FxInnerGenie"), _named(doc, "FxGenieInGroup")
    assert doc.page.objects[genie].is_genie and doc.root_of(genie) == group
    assert not doc.page.objects[group].is_genie
    opened = []
    w._edit_genie = lambda root, obj: opened.append(root)
    w._on_dock_selection([genie])
    btn = next(b for b in w.props.findChildren(QtWidgets.QPushButton) if b.text() == "Genie Parameters…")
    btn.click()
    assert opened == [genie]
    assert ("objprops", group) not in w._dialogs


def test_text_menu_skips_a_genies_texts(win):
    """Text > Bold on a selected genie does not restyle the genie's own
    texts (they come from its element); the status bar says why."""
    from ctgedit.ui.objprops.registry import placed_genie_of

    doc = win.doc
    pg = doc.page
    t = next(
        i
        for i in range(len(pg.objects))
        if placed_genie_of(doc, i) is not None and pg.objects[i].raw.get("_type_code") == 0x05
    )
    genie = placed_genie_of(doc, t)
    before = pg.objects[t].style.bold
    win._on_dock_selection([genie])
    win._toggle_font_flag("bold")
    assert doc.page.objects[t].style.bold == before
    assert "part of a genie" in win.statusBar().currentMessage()


def test_new_group_sheet_edits_the_group(tmp_path, monkeypatch, fx_page):
    """A group made with Arrange > Group (no property list yet): the double-
    click sheet stores its visibility / movement on the GROUP record (one
    undo step) and the page keeps them after a save. (On FxMain's FxThumb: one
    rectangle.)"""
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_page("FxThumb"))
        doc = w.doc
        a = doc.create_object("rectangle", rect=(20, 20, 90, 60), colour=0x336699)
        b = doc.create_object("ellipse", rect=(100, 20, 160, 60), colour=0x336699)
        g = doc.group_objects([a, b])
        w._refresh()
        w._on_object_activated(g)
        dlg = w._dialogs.get(("objprops", g))
        assert dlg is not None and dlg.windowTitle().startswith("Group Properties")
        assert "general" not in dlg.page_ids()
        depth = len(doc._undo)
        dlg._editors["VISIBLE"].setText("Tag1 = 1")
        dlg._editors["MVX"].setText("Tag2")
        assert dlg.apply() and not dlg.failures
        assert len(doc._undo) == depth + 1
        props = doc.object_properties(g)
        assert props["VISIBLE"] == "Tag1 = 1" and props["MVX"] == "Tag2"
        dlg.reject()
        out = str(tmp_path / "grouped.ctg")
        doc.save(out)
        again = EditableDocument.open(out).object_properties(g)
        assert again["VISIBLE"] == "Tag1 = 1" and again["MVX"] == "Tag2"
    finally:
        for d in list(w._dialogs.values()):
            d.reject()
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.doc.dirty = False
        w.close()


# ------------------------------------------------------------------ sheets
def test_genie_and_template_sheets_have_no_shape_fields(win, tplwin):
    """A placed genie's own sheet ('Genie Properties') has CtDraw's genie
    pages -- Access, Metadata, Alarm Indicator -- and no
    Appearance > General; the page template's sheet is 'Template
    Properties' (Linkage only)."""
    from ctgedit.ui.objprops import registry as R
    from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog

    doc = win.doc
    g = _named(doc, "FxMixed")  # a top-level linked genie
    assert (doc.parsed.page.objects[g].subtype_size & 0x201) == 0x201
    ctx = R.SheetContext.for_object(doc, g)
    assert [p.id for p in R.pages_for(ctx)] == ["access", "metadata", "alarm_indicator"]
    dlg = ObjectPropertiesDialog(doc, g)
    assert dlg.windowTitle().startswith("Genie Properties")
    assert "general" not in dlg.page_ids() and dlg._sp_width is None
    dlg.reject()
    tdoc = tplwin.doc
    tpl = tdoc.template_reference()[0]
    ctx = R.SheetContext.for_object(tdoc, tpl)
    assert R.sheet_title(ctx, tdoc.page.objects[tpl].kind.value) == "Template"


def test_nested_genie_without_its_own_link_has_the_genie_pages(win, tplwin):
    """A nested genie without its own link still has the genie pages."""
    from ctgedit.ui.objprops import registry as R

    doc = tplwin.doc
    n = _named(doc, "FxTplMenu")
    po = doc.parsed.page.objects[n]
    assert doc.page.objects[n].raw.get("_from_template")
    assert po.type_code == 0x08 and po.subtype_size == 0x218
    assert po.subtype_size & 0x201 == 0x200
    ctx = R.SheetContext.for_object(doc, n)
    assert [p.id for p in R.pages_for(ctx)] == ["access", "metadata", "alarm_indicator"]
    # a plain group keeps the group pages
    ctx0 = R.SheetContext.for_object(win.doc, 0)
    assert "visibility" in [p.id for p in R.pages_for(ctx0)]


# -------------------------------------------------------------------- Goto
def test_goto_object_tree_is_ctdraws_tree_and_finds_by_an(win, tplwin):
    """Goto Object's tree matches CtDraw's and finds by AN."""
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc = win.doc
    pg = doc.page
    dlg = GotoObjectDialog(doc, win)
    inputs = [i for i, o in enumerate(pg.objects) if o.raw.get("_type_code") in (0x1010, 0x100B)]
    assert inputs and dlg.object_count() == len(pg.objects) - len(inputs)
    assert dlg._root.text(0) == "Page FxMembers"
    assert dlg._items[0].text(0) == "FxPair  Group AN%d" % PAIR_AN
    # a nested genie without its own link (FxNestedHost's)
    host = _named(doc, "FxNestedHost")
    nested = next(k for k in doc.page.objects[host].members if doc.page.objects[k].is_genie)
    assert dlg._items[nested].text(0) == "Genie AN%d" % pg.objects[nested].an
    # topmost first: the root's first row is the last top-level record, group 0 last
    roots = [
        i
        for i, o in enumerate(pg.objects)
        if o.parent is None and o.raw.get("_type_code") not in (0x1010, 0x100B)
    ]
    assert dlg._root.child(0) is dlg._items[roots[-1]]
    assert dlg._root.child(dlg._root.childCount() - 1) is dlg._items[0]
    mixed = _named(doc, "FxMixed")
    kids = [
        dlg._items[mixed].child(k).data(0, QtCore.Qt.UserRole) for k in range(dlg._items[mixed].childCount())
    ]
    assert kids == sorted(kids, reverse=True)  # FxMixed: its nested group first
    assert pg.objects[kids[0]].kind.value == "group"
    owner, touch = _owner(doc)
    assert pg.objects[touch].raw.get("_type_code") == 0x1010 and touch not in dlg._items
    assert pg.objects[owner].an == INPUT_AN
    dlg._filter.setText(str(INPUT_AN))
    assert dlg.matches() == [owner] and dlg.current_index() == owner
    assert not dlg._btn_next.isEnabled()
    dlg._filter.setText("31999")
    assert dlg.matches() == [] and "Cannot find object AN 31999" in dlg._status.text()
    # OK after a failed search opens nothing (not the earlier match)
    assert dlg.current_index() is None
    dlg._ok()
    assert dlg.result() != QtWidgets.QDialog.Accepted and dlg.selected_index is None
    # a row picked in the tree shows its AN
    dlg._tree.setCurrentItem(dlg._items[0])
    assert dlg._filter.text() == str(PAIR_AN)
    dlg.reject()
    # the template's row names the template (FxTplLinked: fx_style normal@XGA, AN 11)
    tdoc = tplwin.doc
    tpl = tdoc.template_reference()[0]
    tdlg = GotoObjectDialog(tdoc, tplwin)
    assert tdlg._items[tpl].text(0) == "Template fx_style.normal@XGA AN11"
    menu = _named(tdoc, "FxTplMenu")
    assert tdlg._items[menu].text(0) == "FxTplMenu  Genie AN%d" % tdoc.page.objects[menu].an
    tdlg.reject()


def test_goto_picked_row_rebases_the_match_steps(win):
    """After a search, a row picked in the tree shows ITS AN, and Previous /
    Next step through that AN's rows -- not the earlier search's matches the
    field no longer shows. 'AN 13' / 'an13' are read as the AN."""
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc = win.doc
    free = [_named(doc, "FxFree1"), _named(doc, "FxFree2")]
    for k in free:
        o = doc.page.objects[k]
        assert o.kind.value == "rectangle" and not o.raw.get("_from_template")
        assert doc.page.nearest_genie(k) is None
    for n, i in enumerate(free):
        assert doc.set_object_name(i, "Tank%d" % n)
    win._refresh()
    dlg = GotoObjectDialog(doc, win)
    dlg._filter.setText("tank")
    assert sorted(dlg.matches()) == sorted(free) and dlg._btn_next.isEnabled()
    dlg._tree.setCurrentItem(dlg._items[0])
    assert dlg._filter.text() == str(PAIR_AN) and dlg.matches() == [0]
    assert not dlg._btn_next.isEnabled()
    dlg.step(1)
    assert dlg.current_index() == 0
    owner, _touch = _owner(doc)
    for typed in ("AN %d", "an%d", "AN%d"):
        dlg._filter.setText(typed % INPUT_AN)
        assert dlg.matches() == [owner], typed
    dlg._filter.setText("AN 40000")
    assert dlg.matches() == [] and dlg._status.text() == "Cannot find object AN 40000"
    dlg.reject()


def test_goto_object_by_name_prefix(win):
    """A name (not a number) finds objects whose name STARTS with it, case-
    insensitively (CtDraw's _strnicmp over the name)."""
    from ctgedit.ui.dialog.dialogs import GotoObjectDialog

    doc = win.doc
    i = next(
        k
        for k, o in enumerate(doc.page.objects)
        if o.kind.value == "rectangle"
        and not o.raw.get("_from_template")
        and doc.page.nearest_genie(k) is None
    )
    assert doc.set_object_name(i, "TankFrame")
    win._refresh()
    dlg = GotoObjectDialog(doc, win)
    assert dlg._items[i].text(0).startswith("TankFrame  ")
    dlg._filter.setText("tankf")
    assert dlg.matches() == [i] and dlg.current_index() == i
    dlg.reject()


def test_goto_ok_opens_properties(win, monkeypatch):
    """Properties… (and a double-click) shows the properties (help 1172197):
    an object's sheet, the page row's Page Properties. Goto Object is
    modeless: it stays open, one window per editor window."""

    def run(picker):
        win._goto_dialog()
        dlg = win._dialogs.get("goto")
        assert dlg is not None and not dlg.isModal()
        picker(dlg)
        dlg._ok()
        assert dlg.isVisible()

    run(lambda d: d._filter.setText(str(PAIR_AN)))
    assert ("objprops", 0) in win._dialogs and win._sel == [0]
    assert [it.data(0, QtCore.Qt.UserRole) for it in win.objects_dock._tree.selectedItems()] == [0]
    win._dialogs[("objprops", 0)].reject()

    owner, _touch = _owner(win.doc)
    run(lambda d: d._filter.setText(str(INPUT_AN)))
    sheet = win._dialogs.get(("objprops", owner))
    assert sheet is not None and win._sel == [owner]
    sheet.reject()

    opened = []
    monkeypatch.setattr(type(win), "page_properties", lambda self: opened.append(True))
    run(lambda d: d._tree.setCurrentItem(d._root))
    assert opened == [True]
