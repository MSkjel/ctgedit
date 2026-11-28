"""Library and symbol workflows."""

import os
import shutil

import pytest

import fxinstall
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the page, its turned linked symbol and that symbol's library and element
PAGE = "FxSymbols"
SYMBOL = "FxBitmapSymbol"
LIB = "fxsym"
POINTER = "fx_bitmap"
#: another element of a different size to re-point to (Include's thumbs, 22 x 11)
OTHER = ("thumbs", "pointer_s_red")
#: a grouped symbol of the library (a group root)
GROUPED = "fx_group"


@pytest.fixture()
def chain(fx_install, monkeypatch):
    """FxMain of the fx_install copy as the scratch project's include path."""
    path = fx_install.project("FxMain")
    monkeypatch.setenv("CTG_LIB_PATH", path)
    return path


@pytest.fixture()
def proj(tmp_path, fx_install, chain):
    """A scratch folder with FxSymbols.ctg and its fxsym library."""
    d = tmp_path / "FxSym"
    d.mkdir()
    shutil.copyfile(fx_install.page(PAGE), d / (PAGE + ".ctg"))
    shutil.copyfile(fx_install.file("FxLib", LIB + ".ctl"), d / (LIB + ".ctl"))
    return d


def _page(proj):
    return str(proj / (PAGE + ".ctg"))


def _lib(proj):
    return str(proj / (LIB + ".ctl"))


def _named(doc, name):
    (i,) = doc.find_objects_by_name(name)
    return i


def _rect(po):
    from ctgedit.format import geometry as G

    return tuple(G.norm_rect(G.parse_geom_header(po.body_raw).rect1))


def _double_pointer(lib):
    """Scale the library symbol fx_bitmap to twice its size, save."""
    from ctgedit.editing import ElementDocument

    d = ElementDocument.open_element(lib, POINTER)
    l, t, r, b = _rect(d.objects[0])
    d.set_object_rect(0, (l, t, l + 2 * (r - l), t + 2 * (b - t)))
    d.save()
    return d


# ------------------------------------------------------------------ format rules
def test_linear_ops_decompose_quarter_turns_mirrors_and_scale():
    from ctgedit.format import placement as P

    assert P.linear_ops((1, 0, 0, 1, 5, 5)) == ([], (1, 1))
    assert P.linear_ops((2.0, 0, 0, 0.5, 0, 0)) == ([], (2.0, 0.5))
    assert P.linear_ops((-1, 0, 0, 1, 0, 0)) == (["mh"], (1, 1))
    assert P.linear_ops((1, 0, 0, -3, 0, 0)) == (["mv"], (1, 3))
    # (x, y) -> (-2y, x): a clockwise quarter turn, then x scaled by 2
    assert P.linear_ops((0, 1, -2, 0, 0, 0)) == (["cw"], (2, 1))
    assert P.linear_ops((0, -1, 1, 0, 0, 0)) == (["acw"], (1, 1))
    assert P.linear_ops((0, 1, 1, 0, 0, 0)) == (["cw", "mh"], (1, 1))
    assert P.linear_ops((0.7071, 0.7071, -0.7071, 0.7071, 0, 0)) is None  # 45 degrees


def test_new_library_names_follow_ctdraws_rules():
    """CtDraw's New Library dialog: 64 characters, letters /
    digits / '_' / '!', not a DOS device name."""
    from ctgedit.format.element import check_library_name

    assert check_library_name("my_symbols") == ""
    assert check_library_name("Fx21!icons") == ""
    assert check_library_name("sæt") == ""  # a code-page letter
    assert "64" in check_library_name("x" * 65)
    assert check_library_name("")
    assert "'-'" in check_library_name("my-lib")
    assert check_library_name("a.b")
    assert check_library_name("sub\\lib")
    assert "reserved" in check_library_name("con")
    assert "reserved" in check_library_name("LPT1")


# ---------------------------------------------- symbols follow their library
def test_update_pages_re_derives_a_doubled_symbol(proj, chain):
    """The acceptance: a symbol doubled in its library -> Update Pages -> the
    page's copy doubles (its Local -- where the hot spot sits, its turn --
    stays), the link table quotes the new stamp and the page is saved."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import links, placement as P
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages as U

    page, lib = _page(proj), _lib(proj)
    before = EditableDocument.open(page)
    k = _named(before, SYMBOL)
    r0 = _rect(before.objects[k])
    local0 = P.read_slots(before.objects[k].body_raw)[0][0][1]
    assert before.symbol_status() == ("current", [])
    _double_pointer(lib)
    cur = U.Current(SymbolResolver.for_page(page, [chain]))
    assert U.stale_symbols(page, cur) == {(LIB, POINTER.lower())}
    rep = U.update_pages(str(proj))
    assert rep.updated == [PAGE] and rep.symbols == 1
    after = EditableDocument.open(page)
    po = after.objects[k]
    r1 = _rect(po)
    # turned 90 degrees: the doubled element shows twice as wide and twice as tall
    assert (r1[2] - r1[0], r1[3] - r1[1]) == (2 * (r0[2] - r0[0]), 2 * (r0[3] - r0[1]))
    assert P.read_slots(po.body_raw)[0][0][1] == local0  # Local = stored truth
    assert after.symbol_status() == ("current", [])
    names = [n for _g, n in links.parse_link_table(after.parsed.page.link_table_raw)[1]]
    assert U._stale_ctl(names, U.Current(SymbolResolver.for_page(page, [chain]))) == set()
    # a second run finds nothing to do
    assert U.update_pages(str(proj)).updated == []


def test_open_page_symbol_update_is_one_undo_step(proj):
    from ctgedit.editing import EditableDocument

    page, lib = _page(proj), _lib(proj)
    _double_pointer(lib)
    doc = EditableDocument.open(page)
    doc.derive_check = True
    k = _named(doc, SYMBOL)
    r0 = _rect(doc.objects[k])
    lt0 = doc.parsed.page.link_table_raw
    state, names = doc.symbol_status()
    assert state == "stale" and names == ["%s.%s" % (LIB, POINTER)]
    assert doc.update_symbol_placements(doc.stale_symbol_elements()) == 1
    assert doc.symbol_stamps_updated and doc.dirty
    assert _rect(doc.objects[k]) != r0
    assert doc.symbol_status()[0] == "current"
    doc.undo()
    assert _rect(doc.objects[k]) == r0 and doc.parsed.page.link_table_raw == lt0
    doc.redo()
    assert _rect(doc.objects[k]) != r0
    # a call that finds nothing to do reports no stamp change either
    assert doc.update_symbol_placements({("no_such", "symbol")}) == 0
    assert doc.symbol_stamps_updated is False


def _re_derives_to_the_same_bytes(path):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    doc = EditableDocument.open(path)
    before = [writer.object_payload(o) for o in doc.objects]
    assert doc.update_symbol_placements() == 0
    assert [writer.object_payload(o) for o in doc.objects] == before
    return doc


def test_current_linked_symbols_re_derive_to_the_same_bytes(proj):
    """Current linked symbols re-derive to the same bytes."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    doc = EditableDocument.open(_page(proj))
    groups = {k for _i, k, kind in doc.symbol_placements() if kind != "leaf"}
    leafs = {k for _i, k, kind in doc.symbol_placements() if kind == "leaf"} - groups
    assert leafs == {("fxsym", "fx_bitmap"), ("fxsym", "fx_flash")}, leafs
    assert {
        ("fxsym", "fx_box"),
        ("fxsym", "fx_round"),
        ("fxsym", "fx_wmf"),
        ("fxsym", "fx_on"),
        ("fxsym", "fx_off"),
        ("fxsym", "fx_group"),
    } <= groups, groups
    before = [writer.object_payload(o) for o in doc.objects]
    assert doc.update_symbol_placements(leafs) == 0
    assert [writer.object_payload(o) for o in doc.objects] == before


def test_current_symbols_re_derive_to_the_same_bytes(proj):
    """A page whose symbols are current: re-deriving every placed symbol changes nothing
    (ctgedit's own placements of them, as the fixtures hold). A named group-root symbol keeps
    its root's name (FxLinkedBox, FxGroupSymbol, FxIncludeBanner: CtDraw's load + save keeps
    them, the oracle)."""
    _re_derives_to_the_same_bytes(_page(proj))


@pytest.mark.golden
@pytest.mark.parametrize(
    "step, unit, page",
    [
        ("pages", "FxMain/FxSymbols", "FxSymbols"),
        ("pages", "FxMain/FxSymbols2", "FxSymbols2"),
        ("turns", "T25_group_symbol_turn", "FxSymbols"),
    ],
)
def test_ctdraw_saved_symbols_re_derive_to_the_same_bytes(fx_page, step, unit, page):
    """Symbols CtDraw saved re-derive to the same bytes."""
    data = fxinstall.oracle_bytes(step, unit, "page.ctg")
    path = fx_page(page)
    with open(path, "wb") as fh:
        fh.write(data)
    doc = _re_derives_to_the_same_bytes(path)
    groups = [i for i, _k, kind in doc.symbol_placements() if kind == "group"]
    assert groups
    if page == "FxSymbols2":
        assert any("VISIBLE" in doc.object_properties(i) for i in groups)


def test_group_root_symbol_recomposes_from_its_element(proj):
    """A group-root symbol (fxsym.fx_group, an instance forest) follows a
    change of its element on Update Pages."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.io import updatepages as U

    page = _page(proj)
    doc = EditableDocument.open(page)
    i = doc.place_library_element(LIB, GROUPED, "ctl", (600, 500))
    assert doc.objects[i].type_code == 0x08 and doc.objects[i].subtype_size & 0x1
    n_members = len(doc.subtree(i))
    last = max(k for k in doc.subtree(i) if doc.page.objects[k].parent == i)
    r_last = _rect(doc.objects[last])
    # the placement's OWN animation (a group-root symbol has the group pages)
    doc.ensure_property(i, "VISIBLE", "FX_PUMP1")
    doc.save()
    d = ElementDocument.open_element(_lib(proj), GROUPED)
    roots = [k for k, o in enumerate(d.page.objects) if o.parent is None]
    d.move_objects(d.subtree(roots[-1]), 7, 3)
    d.save()
    rep = U.update_pages(str(proj))
    assert rep.updated == [PAGE]
    after = EditableDocument.open(page)
    assert len(after.subtree(i)) == n_members
    assert after.symbol_status()[0] == "current"
    moved = _rect(after.objects[last])  # the element's change landed
    assert (moved[0] - r_last[0], moved[1] - r_last[1]) == (7, 3)
    # the root keeps its own properties and stays animated
    assert after.object_properties(i).get("VISIBLE") == "FX_PUMP1"
    assert after.objects[i].subtype_size & 0x10
    # Cut Link of a group-root symbol: the element's own root takes its place
    after.derive_check = True
    box = _rect(after.objects[i])
    g = after.break_library_link(i)
    assert g == i and not after.objects[g].subtype_size & 0x1
    assert after.objects[g].type_code == 0x08 and _rect(after.objects[g]) == box
    assert after.symbol_placements() == [p for p in after.symbol_placements() if p[0] != g]


def test_relink_of_a_symbol_set_state_refits_the_set(proj):
    """Relinking a symbol set state refits the set."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(proj))
    doc.derive_check = True
    from ctgedit.format import symbolset as S

    objs, sem = doc.objects, doc.page.objects
    # (a state of a group-root symbol is its instance forest: the root is the state)
    k = next(
        k
        for k, o in enumerate(objs)
        if S.state_symbol(o) is not None
        and sem[k].parent is not None
        and objs[sem[k].parent].type_code == 0x0C
        and min(j for j in doc.subtree(sem[k].parent) if sem[j].parent == sem[k].parent) == k
    )
    s = sem[k].parent
    r_set = _rect(objs[s])
    assert doc.relink_symbol(k, LIB, "fx_pump_tall")
    assert doc.symbol_link(k)["element"] == "fx_pump_tall"
    assert _rect(doc.objects[s]) == _rect(doc.objects[k]) != r_set
    doc.undo()
    assert _rect(doc.objects[s]) == r_set
    # a state takes a symbol of the other form too (OTHER: a single picture): the set
    # rebuilds the state as one tc 7 record, as its Symbol Set sheet does; one undo step
    n = len(doc.objects)
    assert doc.relink_symbol(k, *OTHER)
    assert doc.objects[k].type_code == 0x07 and doc.symbol_link(k)["element"] == OTHER[1]
    assert len(doc.objects) == n - 1  # the forest's ellipse went with it
    assert _rect(doc.objects[s]) == _rect(doc.objects[k]) != r_set
    doc.undo()
    assert _rect(doc.objects[s]) == r_set and len(doc.objects) == n
    assert doc.symbol_link(k)["kind"] == "group"


def test_relink_refuses_a_symbol_of_the_other_form(proj):
    """A single-picture symbol cannot be pointed at a grouped symbol (it
    would have to become an instance of that symbol's objects)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(_page(proj))
    k = _named(doc, SYMBOL)
    assert doc.library_path(LIB, GROUPED, "ctl")
    with pytest.raises(ValueError, match="grouped symbol"):
        doc.relink_symbol(k, LIB, GROUPED)
    assert doc.symbol_link(k)["element"] == POINTER


def test_window_updates_symbols_when_a_page_opens_and_after_a_symbol_save(proj):
    """Opening a page whose symbol changed re-derives it (undoable, setting
    opt/auto_update_symbols); saving a symbol updates the open pages that
    place it."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    page, lib = _page(proj), _lib(proj)
    _double_pointer(lib)
    w = MainWindow()
    try:
        w.open_document(page)
        k = _named(w.doc, SYMBOL)
        assert w.doc.symbol_status()[0] == "current" and w.doc.dirty
        r1 = _rect(w.doc.objects[k])
        w.doc.undo()
        assert w.doc.symbol_status()[0] == "stale"
        w.doc.redo()
        pd = w.doc
        pd.save()
        w._open_element(lib, POINTER)  # halve it again, save
        l, t, r, b = _rect(w.doc.objects[0])
        w.doc.set_object_rect(0, (l, t, l + (r - l) // 2, t + (b - t) // 2))
        w._exec_prompt = lambda box: None  # the Update Pages offer
        assert w.save()
        r2 = _rect(pd.objects[k])
        assert (r2[2] - r2[0]) * 2 == r1[2] - r1[0] and pd.dirty
    finally:
        for e in w._docs:
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


# ----------------------------------------------------------------- Cut Link
def test_cut_link_of_a_scaled_rotated_symbol_keeps_both(proj):
    """Cut Link of a turned AND scaled symbol with Hidden when: a group of
    the element's own objects, turned and scaled as the symbol showed, that
    keeps the symbol's AN, its properties (VISIBLE included) and its box."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import bitmaps, geometry as G, placement as P
    from ctgedit.format.element import open_element

    doc = EditableDocument.open(_page(proj))
    doc.derive_check = True
    i = _named(doc, SYMBOL)
    l, t, r, b = _rect(doc.objects[i])
    doc.set_object_rect(i, (l, t, l + 2 * (r - l), b))  # now scaled too
    doc.ensure_property(i, "VISIBLE", "FX_PUMP1")
    box = _rect(doc.objects[i])
    handle = G.get_handle(doc.objects[i])
    props = doc.object_properties(i)
    n0 = len(doc.objects)
    g = doc.break_library_link(i)
    assert g == i
    grp = doc.objects[g]
    assert grp.type_code == 0x08 and not grp.subtype_size & 0x1
    assert G.get_handle(grp) == handle  # the AN stays
    assert _rect(grp) == box
    got = doc.object_properties(g)
    assert got["VISIBLE"] == "FX_PUMP1"
    assert {k: got.get(k) for k in props} == props  # every property stays
    assert grp.subtype_size & 0x10  # still animated
    kids = [k for k in doc.subtree(g) if k != g]
    bmp = next(k for k in kids if bitmaps.dib_section(doc.objects[k]) is not None)
    assert _rect(doc.objects[bmp]) == box
    pic, _flash = bitmaps.read_pictures(doc.objects[bmp])
    # the pixels turned: the element's 14 x 23 picture is stored 23 x 14
    src, _f = bitmaps.read_pictures(open_element(_lib(proj), POINTER).tree[0])
    assert (src.width, src.height) == (14, 23)
    assert (pic.width, pic.height) == (23, 14)
    assert not P.is_linked_symbol(doc.objects[bmp])
    # the element's link-table entry went with the only placement
    from ctgedit.format import links

    names = [n for _g, n in links.parse_link_table(doc.parsed.page.link_table_raw)[1]]
    assert not any(n.lower().startswith("%s.%s." % (LIB, POINTER)) for n in names)
    doc.save()
    again = EditableDocument.open(doc.path)
    assert len(again.objects) == len(doc.objects)
    assert again.object_properties(g)["VISIBLE"] == "FX_PUMP1"
    doc.undo()
    assert len(doc.objects) == n0 and P.is_linked_symbol(doc.objects[i])


def test_cut_link_keeps_the_symbols_touch_command(proj):
    """The symbol's touch command moves under the new group, where a
    command record has identity slots (under a linked symbol it carries the
    symbol's frame); its point, its command and the AN stay (FxTouchSymbol)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G, placement as P, writer

    doc = EditableDocument.open(_page(proj))
    doc.derive_check = True
    objs = doc.objects
    i = _named(doc, "FxTouchSymbol")
    # fxsym.fx_box is a group-root symbol: its instance forest, the touch under its root
    assert doc.symbol_link(i)["kind"] == "group" and objs[i].class_flag == 2
    assert (doc.symbol_link(i) or {}).get("path")
    t = next(k for k in doc.subtree(i) if objs[k].type_code == 0x1010)
    anchor, bag = tuple(G.parse_geom_header(objs[t].body_raw).anchor), writer.value_bag(objs[t])
    handle = G.get_handle(objs[i])
    assert [s[:2] for s in P.read_slots(objs[t].body_raw)[0]] != [(0, P.IDENT)] * 3
    g = doc.break_library_link(i)
    touch = [
        k for k in doc.subtree(g) if doc.page.objects[k].parent == g and doc.objects[k].type_code == 0x1010
    ]
    assert len(touch) == 1
    po = doc.objects[touch[0]]
    assert [s[:2] for s in P.read_slots(po.body_raw)[0]] == [(0, P.IDENT)] * 3
    assert tuple(G.parse_geom_header(po.body_raw).anchor) == anchor
    assert writer.value_bag(po) == bag and G.get_handle(doc.objects[g]) == handle
    doc.undo()
    assert doc.symbol_link(i)["kind"] == "group"
    assert [s[:2] for s in P.read_slots(doc.objects[t].body_raw)[0]] != [(0, P.IDENT)] * 3


def test_cut_link_refuses_with_a_reason(fx_page):
    from ctgedit.editing import EditableDocument, LibraryLinkError

    genies = EditableDocument.open(fx_page("FxGenies"))
    objs = genies.parsed.page.objects
    genie = next(
        r for r in genies.page.roots() if objs[r].subtype_size & 0x201 == 0x201 and genies.symbol_link(r)
    )
    with pytest.raises(LibraryLinkError, match="genie"):
        genies.break_library_link(genie)
    from ctgedit.format import symbolset as S

    sym = EditableDocument.open(fx_page(PAGE))
    pobjs, sem = sym.parsed.page.objects, sym.page.objects
    state = next(
        k
        for k, o in enumerate(pobjs)
        if S.state_symbol(o) is not None
        and sem[k].parent is not None
        and pobjs[sem[k].parent].type_code == 0x0C
    )
    with pytest.raises(LibraryLinkError, match="symbol set"):
        sym.break_library_link(state)
    plain = genies.create_object("rectangle", (0, 0, 10, 10))
    assert genies.break_library_link(plain) is None


# ----------------------------------------------------- Linkage page / re-point
def test_relink_keeps_local_and_derives_the_new_symbol(proj):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import links, placement as P

    doc = EditableDocument.open(_page(proj))
    doc.derive_check = True
    i = _named(doc, SYMBOL)
    local0 = P.read_slots(doc.objects[i].body_raw)[0][0][1]
    props0 = doc.object_properties(i)
    info = P.element_info(doc._resolver(), OTHER[0], OTHER[1], "ctl")
    assert doc.relink_symbol(i, *OTHER)
    po = doc.objects[i]
    assert doc.symbol_link(i)["element"] == OTHER[1]
    assert P.read_slots(po.body_raw)[0][0][1] == local0
    nl, nt, nr, nb = info["N"]
    r = _rect(po)
    assert (nr - nl) != (nb - nt)  # (a box that shows a turn)
    assert (r[2] - r[0], r[3] - r[1]) == (nb - nt, nr - nl)  # turned 90 degrees
    assert doc.object_properties(i) == props0
    names = [n for _g, n in links.parse_link_table(doc.parsed.page.link_table_raw)[1]]
    assert any(OTHER[1] in n for n in names)
    assert not any(n.lower().startswith("%s.%s." % (LIB, POINTER)) for n in names)
    assert doc.relink_symbol(i, *OTHER) is False
    with pytest.raises(ValueError):
        doc.relink_symbol(i, OTHER[0], "no_such_symbol")
    doc.undo()
    assert doc.symbol_link(i)["element"] == POINTER


def test_linkage_page_shows_and_repoints(proj):
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.objprops.sheet import ObjectPropertiesDialog

    doc = EditableDocument.open(_page(proj))
    k = _named(doc, SYMBOL)
    dlg = ObjectPropertiesDialog(doc, k)
    try:
        assert dlg.page_ids()[0] == "linkage"
        ed = dlg.linkage_editor
        assert ed.name.text() == "%s.%s" % (LIB, POINTER)
        assert ("%s.ctl" % LIB) in ed.path.text().lower()
        assert ed.btn_set.isEnabled()
        ed._pending = (OTHER[0], OTHER[1], "ctl")  # what Set picks
        assert dlg.apply()
        assert doc.symbol_link(k)["element"] == OTHER[1]
        doc.undo()
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------------- hot spot
def test_hotspot_move_changes_only_the_origin(proj):
    """Hot Spot Location: the element's origin e moves (the head's i32
    0x0C / 0x10 and the root's T(-e)); its objects stay. A placed copy then
    shifts by the opposite of the move on Update Pages (its Local stays)."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import placement as P
    from ctgedit.format.element import open_element

    page, lib = _page(proj), _lib(proj)
    pd = EditableDocument.open(page)
    k = pd.place_library_element(LIB, POINTER, "ctl", (600, 400))
    pd.save()
    r_before = _rect(pd.objects[k])
    d = ElementDocument.open_element(lib, POINTER)
    assert d.can_set_hotspot
    e0 = d.hotspot
    geom0 = _rect(d.objects[0])
    assert d.set_hotspot(e0[0] + 5, e0[1] + 3)
    assert d.hotspot == (e0[0] + 5, e0[1] + 3) and d.dirty
    assert d.set_hotspot(e0[0] + 5, e0[1] + 3) is False
    d.undo()
    assert d.hotspot == e0
    d.redo()
    d.save()
    assert d.hotspot == (e0[0] + 5, e0[1] + 3)
    pe = open_element(lib, POINTER)
    assert pe.origin == (e0[0] + 5, e0[1] + 3)
    s2 = P.read_slots(pe.tree[0].body_raw)[0][1]
    assert (s2[1][4], s2[1][5]) == (-(e0[0] + 5.0), -(e0[1] + 3.0))
    assert _rect(pe.tree[0]) == geom0
    again = ElementDocument.open_element(lib, POINTER)
    assert again.hotspot == (e0[0] + 5, e0[1] + 3)
    # the placed copy: same Local, derived 5 / 3 px up-left
    after = EditableDocument.open(page)
    assert after.update_symbol_placements(after.stale_symbol_elements()) >= 1
    r_after = _rect(after.objects[k])
    assert (r_after[0] - r_before[0], r_after[1] - r_before[1]) == (-5, -3)


def test_hotspot_of_a_group_root_genie_and_refusals(tmp_path, fx_install):
    """fxparts.fx_grouproot: a genie whose root is a group."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import placement as P
    from ctgedit.format.element import open_element

    lib = fx_install.file("FxLib", "fxparts.ctm")
    d = ElementDocument.open_element(lib, "fx_grouproot")
    assert d.objects[0].type_code == 0x08
    x, y = d.hotspot
    d.set_hotspot(x - 10, y + 4)
    d.save()
    pe = open_element(lib, "fx_grouproot")
    assert pe.origin == (x - 10, y + 4)
    s2 = P.read_slots(pe.tree[0].body_raw)[0][1]
    assert (s2[1][4], s2[1][5]) == (-(x - 10.0), -(y + 4.0))
    # a new element takes the point set before its first save
    n = ElementDocument.new_element(str(tmp_path / "mine.ctl"), "probe", "symbol")
    n.create_object("rectangle", (100, 100, 140, 120))
    n.set_hotspot(100, 120)
    n.save()
    assert open_element(str(tmp_path / "mine.ctl"), "probe").origin == (100, 120)
    t = ElementDocument.new_element(str(tmp_path / "mine.ctt"), "normal", "template", page_size=(640, 480))
    assert not t.can_set_hotspot
    with pytest.raises(ValueError):
        t.set_hotspot(1, 1)


def test_hotspot_move_of_a_symbol_rooted_element_sets_its_origin(fx_install):
    """FxLib's fxmore.fx_symsym (a placed fxsym.fx_box, a group-root symbol: its instance
    forest under the element's group root, as CtDraw's save of fxmore.ctl has it): a hot
    spot move sets the element's origin."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import open_element

    lib = fx_install.file("FxLib", "fxmore.ctl")
    tree = open_element(lib, "fx_symsym").tree
    assert [(po.type_code, po.subtype_size & 0x1) for po in tree] == [(8, 0), (8, 1), (0xF, 0)]
    d = ElementDocument.open_element(lib, "fx_symsym")
    x, y = d.hotspot
    assert d.set_hotspot(x + 4, y + 2)
    d.save()
    assert open_element(lib, "fx_symsym").origin == (x + 4, y + 2)


def test_hotspot_of_a_symbol_whose_member_is_a_placed_symbol(fx_install):
    """FxLib's fxmore.fx_symsym: the element's group root holds a placed symbol of another
       element (fxsym.fx_box's instance forest). A hot spot move is the root's T(-e) (slot 2),
    as for any group root; the placed symbol's own slots derive from ITS
       element's origin and stay."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import placement as P
    from ctgedit.format.element import open_element

    lib = fx_install.file("FxLib", "fxmore.ctl")
    pe0 = open_element(lib, "fx_symsym")
    slots0 = [[s[:2] for s in P.read_slots(po.body_raw)[0]] for po in pe0.tree[1:]]
    d = ElementDocument.open_element(lib, "fx_symsym")
    x, y = d.hotspot
    assert d.set_hotspot(x + 4, y + 2)
    d.save()
    pe = open_element(lib, "fx_symsym")
    assert pe.origin == (x + 4, y + 2)
    s2 = P.read_slots(pe.tree[0].body_raw)[0][1]
    assert (s2[1][4], s2[1][5]) == (-(x + 4.0), -(y + 2.0))
    assert [[s[:2] for s in P.read_slots(po.body_raw)[0]] for po in pe.tree[1:]] == slots0


def test_hotspot_drag_on_the_canvas(proj):
    """The marker is dragged like CtDraw's (a press on it, a release moves
    it); the move is an undo step of the element document."""
    _app()
    from PySide6 import QtCore, QtGui, QtWidgets
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.resize(900, 700)
        w.show()
        w._open_element(_lib(proj), POINTER)
        QtWidgets.QApplication.processEvents()
        doc = w.doc
        x0, y0 = doc.hotspot
        vp = w.view.viewport()
        start = w.view.mapFromScene(QtCore.QPointF(x0, y0))
        z = w.view.transform().m11()
        end = start + QtCore.QPoint(int(round(20 * z)), int(round(10 * z)))
        assert w.view._hotspot_at(start)
        got = []
        w.view.hotspotMoved.connect(lambda a, b: got.append((a, b)))

        def send(kind, pos, buttons):
            ev = QtGui.QMouseEvent(
                kind,
                QtCore.QPointF(pos),
                QtCore.QPointF(vp.mapToGlobal(pos)),
                QtCore.Qt.LeftButton,
                buttons,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(vp, ev)

        send(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton)
        send(QtCore.QEvent.MouseMove, end, QtCore.Qt.LeftButton)
        send(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.NoButton)
        assert got and doc.hotspot == got[-1] and doc.hotspot != (x0, y0)
        doc.undo()
        assert doc.hotspot == (x0, y0)
    finally:
        for e in w._docs:
            e["doc"].dirty = False
        w.hide()
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


# -------------------------------------------------------- paste linked / not
def test_paste_unlinked_follows_the_linked_setting(proj):
    _app()
    from ctgedit.ui.panels.library_panel import paste_linked, set_paste_linked
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(_page(proj))
        set_paste_linked(False)
        w._sync_paste_linked()
        assert not w.act_paste_linked.isChecked() and not w.library_dock.linked()
        w._place_library(LIB, POINTER, "ctl", (600, 400))
        po = w.doc.objects[w._sel[0]]
        assert not po.subtype_size & 0x1 and po.an_str == "-1"  # an embedded copy
        w.library_dock.set_linked(True)
        assert paste_linked() and w.act_paste_linked.isChecked()
        w._place_library(LIB, POINTER, "ctl", (650, 400))
        assert w.doc.objects[w._sel[0]].subtype_size & 0x1
    finally:
        set_paste_linked(True)
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_library_panel_linked_toggle_used_by_and_flashing_badge():
    _app()
    from types import SimpleNamespace
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.panels.library_panel import LibraryPanel, paste_linked, set_paste_linked

    panel = LibraryPanel()
    try:
        seen, used = [], []
        panel.linkedToggled.connect(seen.append)
        panel.usedByRequested.connect(lambda p, e: used.append((p, e)))
        panel.set_linked(False)
        assert seen == [False] and paste_linked() is False
        set_paste_linked(True)
        panel.sync_linked()
        assert panel.linked() and seen == [False]  # a sync emits nothing
        e = Entry("symbol", "FxLib", "fx_flash", r"C:\x\fxsym.ctl", library="fxsym")
        panel._thumbs = SimpleNamespace(summary=lambda _e: SimpleNamespace(flashing=True))
        assert panel.is_flashing(e)
        assert "Flashing" in panel._preview_info(e)
        menu = panel.build_context_menu(e)
        act = next(a for a in menu.actions() if a.text().startswith("Used By"))
        act.trigger()
        assert used == [(e.path, e.name)]
    finally:
        set_paste_linked(True)
        panel.deleteLater()


def test_flashing_elements_are_marked(fx_install):
    """pagesummary marks an element whose bitmap stores a flash picture (the
    diamond badge of CtDraw's symbol picker): FxLib's fxsym.fx_flash, and not the
    plain fx_bitmap."""
    from ctgedit.format.pagesummary import read_element_summaries

    lib = fx_install.file("FxLib", "fxsym.ctl")
    by = {s.name: s.flashing for s in read_element_summaries(lib, want_thumbnail=False)}
    assert by["fx_flash"] is True
    assert by["fx_bitmap"] is False
    assert any(v is False for v in by.values())


# ---------------------------------------------------------- usage / dialogs
def test_usage_index_lists_the_pages_that_place_an_element(proj):
    from ctgedit.io.catalog import ProjectCatalog

    cat = ProjectCatalog(user_dir=str(proj))  # a loose folder
    lib = _lib(proj)
    names = [e.name for e in cat.element_usage(lib, POINTER)]
    assert names == [PAGE]
    assert cat.element_usage(lib, "no_such_element") == []
    idx = cat.usage_index(str(proj))
    assert "%s.%s.<ctl" % (LIB, POINTER.lower()) in idx


def test_copy_to_library_dialog_lists_projects_libraries_and_elements(proj, chain):
    _app()
    from ctgedit.ui.dialog.library_dialogs import CopyToLibraryDialog

    edits = []
    dlg = CopyToLibraryDialog(
        str(proj),
        None,
        exts=("ctl",),
        on_edit=lambda p, e: edits.append((p, e)),
        on_delete=lambda p, e: False,
        projects=[chain],
    )
    try:
        projects = [dlg._cmb_proj.itemText(k) for k in range(dlg._cmb_proj.count())]
        assert projects[0].startswith(os.path.basename(str(proj)) + " (")  # "<name> (n libraries)"
        assert any(p.startswith("FxMain") for p in projects)
        assert any("supplied with Plant SCADA" in p for p in projects)  # Include
        dlg._cmb_lib.setEditText(LIB + ".ctl")
        assert POINTER in dlg.element_names()
        assert dlg.select_element(POINTER) and dlg._ed_elem.text() == POINTER
        assert "will be replaced" in dlg._hint.text()
        # (the button says it replaces: a browsing click had changed what OK writes)
        from PySide6 import QtWidgets as _QW

        assert dlg._buttons.button(_QW.QDialogButtonBox.Ok).text() == "Replace"
        assert dlg._btn_edit.isEnabled() and dlg._btn_del.isEnabled()
        dlg._new_name()
        assert dlg._ed_elem.text() == "" and not dlg._btn_edit.isEnabled()
        dlg._ed_elem.setText("fresh")
        assert dlg.problem() == ""
        assert dlg.values() == (_lib(proj), "fresh")
        dlg._cmb_lib.setEditText("bad-name")
        assert "'-'" in dlg.problem() and "'-'" in dlg._hint.text()
        dlg._cmb_lib.setEditText("brand_new")
        assert dlg.problem() == "" and "will be created" in dlg._hint.text()
        dlg._chk_preview.setChecked(False)
        from ctgedit.ui.prefs.settings import app_settings

        assert app_settings().value("library/copy_preview", True, type=bool) is False
        dlg._cmb_lib.setEditText(LIB + ".ctl")
        dlg.select_element(POINTER)
        dlg._edit()
        assert edits == [(_lib(proj), POINTER)]
    finally:
        dlg.deleteLater()


def test_used_by_and_hotspot_dialogs(proj):
    _app()
    from ctgedit.io.catalog import ProjectCatalog
    from ctgedit.ui.dialog.library_dialogs import HotSpotDialog, UsedByDialog

    usage = ProjectCatalog(user_dir=str(proj)).element_usage(_lib(proj), POINTER)
    opened = []
    dlg = UsedByDialog(_lib(proj), POINTER, usage, None, on_open=opened.append)
    try:
        assert dlg.pages() == [PAGE]
        dlg._open()
        assert opened == [_page(proj)]
    finally:
        dlg.deleteLater()
    hs = HotSpotDialog(10, 20, None, extent=(0, 0, 40, 30))
    try:
        assert hs.values() == (10, 20)
        hs._put((0, 30))
        assert hs.values() == (0, 30)
    finally:
        hs.deleteLater()


def test_saving_into_a_supplied_symbol_library_warns(proj, fx_install, monkeypatch):
    """A symbol / genie library of a project supplied with Plant SCADA
    (Include, ...: the fake install's Include) warns before a save, like a template."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: None)
    w = MainWindow()
    try:

        class Doc:
            pass

        assert not w._confirm_shipped_template(Doc(), fx_install.file("Include", "thumbs.ctl"), True)
        assert w._confirm_shipped_template(Doc(), _lib(proj), True)
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
