"""CtDraw's page load rules that our open reproduces."""

import os
import shutil
import struct

import pytest

from ctgedit.format import genie_place as GP
from ctgedit.format import geometry as G
from ctgedit.format.page import PageObject

#: the genie the placeholder tests make missing (FxValue on FxGenies)
GENIE = ("fxgenie", "fx_value")
GENIE_NAME = "fxgenie.fx_value"


def _rec(h, an="-1", tc=0x01, cf=0):
    """A bare record: child count, type code, AN string and handle (what the rules read)."""
    return PageObject(
        index=0,
        record_offset=0,
        payload_size=0,
        next_link=0,
        class_flag=cf,
        type_code=tc,
        subtype_size=0,
        an_str=an,
        body_raw=struct.pack("<HH", 0, h) + b"\0" * 8,
    )


def test_link_instance_pairs_by_an_string_and_class():
    """A stored record links with the element record its AN string names when the type
    matches, the first one in stored order; the roots always link."""
    el = [_rec(0, cf=3), _rec(5, tc=0x05), _rec(6), _rec(7, tc=0x05)]
    inst = [_rec(40, cf=3), _rec(41, "7", 0x05), _rec(42, "6", 0x05), _rec(43, "5", 0x05)]
    # element 5 (text) -> stored 43; 6 (rectangle) names a text: no link; 7 -> stored 41
    assert GP.link_instance(inst, el) == [0, 3, None, 1]
    # two stored records name one element record: the first in stored order links
    inst2 = [_rec(40, cf=3), _rec(41, "5", 0x05), _rec(42, "5", 0x05), _rec(43, "-1")]
    assert GP.link_instance(inst2, el) == [0, 1, None, None]


def test_page_instance_handles_frees_every_unlinked_record_first():
    """Linked records keep their handles; every unlinked stored record is deleted first,
    page-wide; then a new record takes its element handle when free (page order), then the
    rest the lowest free (page order); an input shares its owner's handle."""
    el = [_rec(0, cf=2), _rec(2), _rec(9, cf=1), _rec(9, tc=0x1010)]
    # instance A links its root (handle 10) and element record 2 (stored 11); its stored
    # records 12 and 3 link to nothing, so both handles are free
    a = [_rec(10, cf=3), _rec(11, "2"), _rec(12, "77"), _rec(3, "78")]
    # instance B links its root (20) only
    b = [_rec(20, cf=1), _rec(21, "55")]
    outside = {0, 1, 4}
    got = GP.page_instance_handles([(a, el), (b, el)], outside)
    # A: its new record takes its element handle 9, its input shares it
    assert got[0] == [10, 11, 9, 9]
    # B: element handle 2 is free (A kept 11 for it); 9 is A's: the lowest free, 3 (the
    # handle A's unlinked stored record gave up); the input shares it
    assert got[1] == [20, 2, 3, 3]
    # one instance alone: the same rules
    assert GP.ctdraw_instance_handles(a, el, outside) == [10, 11, 9, 9]


def test_page_instance_handles_placeholder_case_claims_no_handle():
    """A placeholder case (claim False) links nothing and asks for no element handle: it
    takes the lowest free in its turn, after the element handles of the others."""
    ph = [_rec(30)]
    gen = [_rec(31, cf=1)]
    gen_el = [_rec(3, cf=1), _rec(2)]
    got = GP.page_instance_handles([(ph, [_rec(8)]), (gen, gen_el)], {0, 1}, claim=[False, True])
    assert got == [[3], [31, 2]]


def test_untrunc_finds_the_native_coordinate_nearest_the_guess():
    # int(0.5 * n + 10) == 15 for n in 10, 11: the nearer to the guess wins
    assert GP._untrunc(15, 0.5, 10, 10.9) == 11
    assert GP._untrunc(15, 0.5, 10, 10.1) == 10
    # (nothing truncates to it: the guess rounded)
    assert GP._untrunc(15, 1.0, 0.5, 40.4) == 40


def test_trunc_box_truncates_the_mapped_corners():
    M = (0.5, 0, 0, -1, 10.75, 100.25)  # half scale in x, a mirror in y
    assert GP._trunc_box(M, (3, 4, 9, 8)) == (12, 92, 15, 96)


def _nav_genie(page):
    """The records of ``page`` and the index of FxValue's root (a genie instance: children,
    dwFlags 0x201, a '.<ctm' link entry)."""
    from ctgedit.format.page import parse_ctg

    objs = parse_ctg(page).page.objects
    i = next(
        k
        for k, po in enumerate(objs)
        if po.class_flag
        and (po.subtype_size & 0x201) == 0x201
        and str(GP.decode(po).link or "").lower() == GENIE_NAME + ".<ctm"
    )
    return objs, i


@pytest.fixture
def nav(fx_page):
    """FxMain's FxGenies page of the fake install."""
    return fx_page("FxGenies")


def test_placeholder_record_keeps_the_genies_name_local_and_values(nav):
    objs, i = _nav_genie(nav)
    root = objs[i]
    r0 = GP.decode(root)
    ph = GP.placeholder_record(root, 77, GENIE_NAME)
    r = GP.decode(ph)
    l, t, rr, b = r0.rect1
    assert ph.type_code == 0xFFFF and ph.class_flag == 0 and ph.an_str == "-1"
    assert G.get_handle(ph) == 77
    assert ph.subtype_size == (root.subtype_size & ~0x201) | 0x2
    assert r.anchor == (l, b - 1) and r.rect1 == r.rect2 == (l, t, rr, b)
    assert r.cls == struct.pack("<6h", l, t, rr, b, l, b - 1)
    vals = r.entries[0][1].pairs
    assert vals[0] == ("_Library", GENIE_NAME)
    assert [k for k, _v in vals[1:7]] == ["_Transform_%s" % c for c in "ABCDHK"]
    assert r.entries[0][1].ov == 10
    # a genie: its template bag holds the same, and its binding level is kept, size words 0
    assert r.entries[0][2].pairs == vals
    assert [bg.pairs for bg in r.entries[1]] == [bg.pairs for bg in r0.entries[-1]]
    assert all(bg.ov == 0 for bg in r.entries[1])


def test_a_genie_whose_library_is_missing_becomes_a_placeholder(nav, monkeypatch):
    """The genie's subtree becomes one placeholder with the lowest free handle; its link
    entry keeps the bare name with a zero GUID; once the library opens again, the
    placeholder is placed again in the genie's box. One undo step restores the page."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import links

    # (the page in its project of the fake install copy: its include chain finds fxgenie)
    doc = EditableDocument.open(nav)
    objs = list(doc.parsed.page.objects)
    i = next(k for k, key in doc.genie_instances() if tuple(x.lower() for x in key) == GENIE)
    end = GP.subtree_end(objs, i)
    real = doc.library_path
    monkeypatch.setattr(
        doc,
        "library_path",
        lambda lib, el, ext: None if (lib.lower(), el.lower()) == GENIE else real(lib, el, ext),
    )
    assert [k for k, _l in doc.missing_library_objects()] == [i]
    assert doc.placeholders_for_missing_libraries() == 1
    now = doc.parsed.page.objects
    assert len(now) == len(objs) - (end - i) + 1
    ph = now[i]
    assert ph.type_code == 0xFFFF
    used = {G.get_handle(p) for k, p in enumerate(objs) if not i <= k < end}
    h = 0
    while h in used:
        h += 1
    assert G.get_handle(ph) == h
    t = links.parse_link_table(doc.parsed.page.link_table_raw)
    ent = [(g, n) for g, n in t[1] if links.strip_stamp(n).lower() == GENIE_NAME + ".<ctm"]
    assert ent and all(g == bytes(16) and n == links.strip_stamp(n) for g, n in ent)
    # the library opens again: the placeholder is placed again
    monkeypatch.setattr(doc, "library_path", real)
    assert [k for k, *_r in doc.placeholder_genies()] == [i]
    # one undo step gives the page as read
    doc.undo()
    assert [bytes(p.body_raw) for p in doc.parsed.page.objects] == [bytes(p.body_raw) for p in objs]


def test_a_page_outside_a_project_keeps_its_objects(nav, tmp_path, monkeypatch):
    """A page copied out of its project (no pages.DBF beside it, no CTG_LIB_PATH project):
    its libraries are not known, so nothing becomes a placeholder."""
    from ctgedit.editing import EditableDocument

    dst = tmp_path / os.path.basename(nav)
    shutil.copyfile(nav, dst)
    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    doc = EditableDocument.open(str(dst))
    monkeypatch.setattr(doc, "library_path", lambda lib, el, ext: None)
    assert doc.missing_library_objects() == []
    assert doc.placeholders_for_missing_libraries() == 0


def test_a_placeholder_whose_library_is_missing_draws_the_x_box(nav, monkeypatch):
    """CtDraw's DummyMarker class (type 0xFFFF) is its X box object: a stored placeholder
    whose library does not resolve draws a white box with both diagonals over its rect1
    (the reader marks it as a missing element); one that resolves is left to the open
    updates, which place the genie again."""
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    objs, i = _nav_genie(nav)
    end = GP.subtree_end(objs, i)
    doc = EditableDocument.open(nav)
    pb = doc.parsed.page
    gone = GP.placeholder_record(objs[i], 900, "_no_such_library.fx_value.<ctm")
    here = GP.placeholder_record(objs[i], 901, GENIE_NAME + ".<ctm")
    pb.objects[i:end] = [gone, here]
    pb.object_count = len(pb.objects)
    again = EditableDocument.from_bytes(doc.serialize(), doc.path)
    o, o2 = again.page.objects[i], again.page.objects[i + 1]
    assert o.raw.get("_missing_element") == "_no_such_library.fx_value"
    assert not o2.raw.get("_missing_element")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    sc = PageScene()
    sc.set_page(again.page)
    assert sc._items.get(i) is not None
    assert "_no_such_library.fx_value was not found." == sc._items[i].toolTip()


def test_a_new_object_takes_the_lowest_free_handle():
    """CtDraw's new handles: LIVE, two rectangles drawn on a copy of Example
    !About (handles 0, 1, 2, 4, 6, 13) took 3 and 5."""
    used = {0, 1, 2, 4, 6, 13}
    assert GP.alloc_handles(used, 2) == [3, 5]
    assert {3, 5} <= used


def test_a_pasted_genie_takes_its_element_handles_where_free(fx_install):
    """A pasted genie takes its element handles where they're free."""
    el = GP.load_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump", compose=True)
    assert [G.get_handle(p) for p in el.records] == [3, 0, 0, 1, 2]
    used = {0, 1, 2, 4, 6, 13}
    assert GP.paste_handles(el.records, used) == [3, 5, 5, 7, 8]
    assert used == {0, 1, 2, 3, 4, 5, 6, 7, 8, 13}


@pytest.mark.golden
def test_a_pasted_genie_takes_the_handles_ctdraw_gives(fx_install):
    """CtDraw's LibraryObjectPlaceEx of fxgenie.fx_pump onto FxOracleGaps (six rectangles
    with the handles 0, 1, 2, 4, 6, 13), then two rectangles drawn: the genie's records take
    what paste_handles gives, the rectangles the lowest free after them."""
    import fxinstall
    from ctgedit.format.page import parse_ctg

    before = parse_ctg(fxinstall.oracle("place", "genie_into_handle_gaps", "input.ctg")).page.objects
    after = parse_ctg(fxinstall.oracle("place", "genie_into_handle_gaps", "page.ctg")).page.objects
    used = {G.get_handle(p) for p in before}
    assert used == {0, 1, 2, 4, 6, 13}
    el = GP.load_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump", compose=True)
    new = after[len(before) :]
    assert len(new) == len(el.records) + 2
    assert [G.get_handle(p) for p in new[: len(el.records)]] == GP.paste_handles(el.records, used)
    assert [G.get_handle(p) for p in new[len(el.records) :]] == GP.alloc_handles(used, 2)


def test_the_library_search_takes_sibling_projects_only_when_asked(fx_page, monkeypatch):
    """The Graphics Builder looks in the page's project and the projects it includes; the
    other projects of the install are an option (Settings > Pages and libraries). FxMain
    includes FxLib and FxStyle, never FxSim."""
    from ctgedit.format.symbols import SymbolResolver

    page = fx_page("FxShapes")
    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    monkeypatch.delenv("CTG_LIB_SIBLINGS", raising=False)
    off = [os.path.normcase(p) for p in SymbolResolver.for_page(page).search_paths]
    monkeypatch.setenv("CTG_LIB_SIBLINGS", "1")
    on = [os.path.normcase(p) for p in SymbolResolver.for_page(page).search_paths]
    example = os.path.normcase(os.path.join(os.path.dirname(os.path.dirname(page)), "FxSim"))
    assert example not in off and example in on
