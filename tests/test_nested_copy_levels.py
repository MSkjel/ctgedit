"""Levels and AN strings of a recomposed nested genie copy."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402


@pytest.fixture
def fx(fx_install, monkeypatch):
    """The fake install, each save with a stamp of its own (two saves in one second read
    as one version)."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _tree(fx, name: str):
    return E.open_element(_lib(fx), name).tree


def _levels(po):
    r = GP.decode(po)
    return [[list(b.pairs) for b in lv] for lv in (r.entries or [])]


def _txt(po):
    r = GP.decode(po)
    return dict(r.entries[0][1].pairs).get("TXT_OFF") if r.entries else None


def _span(recs, element: str):
    """(start, end) of the outermost copy of ``element`` in ``recs``."""
    for k, po in enumerate(recs):
        if k and po.class_flag and (po.subtype_size & 0x201) == 0x201:
            ref = GP.reference_from_object(po)
            if ref.element.lower() == element.lower():
                return k, GP._span_end(recs, k)
    raise AssertionError("no copy of %s" % element)


def _update(fx, host: str) -> None:
    """``host`` composes its changed nested genies and is saved (the chain's step)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), host)
    assert d.nested_genie_changes(), host
    d.update_nested_genies()
    d.save()
    libraries_changed()


def _placed_texts(fx, element: str, params: dict):
    """The TXT_OFF values of a fresh placement of ``element`` on a page."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", element, "ctm", (400, 300), params=params)
    return [_txt(p) for p in pd.parsed.page.objects[n:] if p.type_code == 0x05]


def _redraw_n0(fx) -> None:
    """n0 drawn again: a rectangle and a new text '%Label% Z' grouped, the old text
    deleted (only the copy's root matches by AN and class)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    t = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.create_object("rectangle", rect=(10, 10, 120, 30))
    d.create_object("text", (14, 12, 110, 28), text="%Label% Z")
    d.delete_objects([t])
    d.group_objects(list(range(len(d.parsed.page.objects))))
    d.save()
    libraries_changed()


def _copy_levels_follow_the_root(recs, k: int, end: int, sub) -> None:
    """Every record of the copy recs[k:end] has its element record's levels plus the one
    the copy root has beyond the element root (the element form of a genie element)."""
    extra = len(GP.decode(recs[k]).entries) - len(GP.decode(sub.records[0]).entries)
    assert extra == 1
    for j in range(end - k):
        a, c = GP.decode(recs[k + j]), GP.decode(sub.records[j])
        if a.entries is not None and c.entries is not None:
            assert len(a.entries) == len(c.entries) + extra, (j, _levels(recs[k + j]))


# ------------------------------------------------------------ a genie redrawn in full
@pytest.mark.parametrize("own_text", [True, False])
def test_a_redrawn_genie_keeps_the_hosts_parameters(fx, own_text):
    """A copy where only the root matched keeps the element form: the host n1 declares only Name,
    and a placement of n1 binds n0's text instead of showing '%Name% L1 Z'."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    if not own_text:
        d = ElementDocument.open_element(lib, "n1")
        t = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05 and o.subtype_size & 2)
        d.delete_objects([t])
        d.save()
        libraries_changed()
    assert dict(GP.declared_params(GP.load_element(lib, "n1"))) == {"Name": ""}
    _redraw_n0(fx)
    _update(fx, "n1")
    assert dict(GP.declared_params(GP.load_element(lib, "n1"))) == {"Name": ""}
    recs = _tree(fx, "n1")
    k, end = _span(recs, "n0")
    _copy_levels_follow_the_root(recs, k, end, GP.load_element(lib, "n0"))
    text = next(recs[j] for j in range(k, end) if recs[j].type_code == 0x05)
    assert _levels(text)[1:] == [
        [[("Label", "\n")], [("Label", " L1")], [("Label", "%Name% L1")]],
        [[("Name", "\n")], [], []],
    ]
    # as a fresh placement of the redrawn n0 binds it
    assert "Q L1 Z" in _placed_texts(fx, "n1", {"Name": "Q"})
    assert "Q L1 Z" in _placed_texts(fx, "n0", {"Label": "Q L1"})


def test_a_page_composes_a_redrawn_nested_genie_bound(fx):
    """The page path alone (the page's genie update, no element saved): FxShapes' placed n1
    shows 'Q L1 Z', not the raw template '%Name% L1 Z'."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n1", "ctm", (400, 300), params={"Name": "Q"})
    pd.place_library_element("fxnest", "n2", "ctm", (600, 300), params={"Name": "R"})
    pd.save()
    libraries_changed()
    _redraw_n0(fx)
    pd = EditableDocument.open(fx.page("FxShapes"))
    ch = pd.genie_changes()
    assert ch and pd.apply_genie_changes(ch)
    got = [_txt(p) for p in pd.parsed.page.objects[n:] if p.type_code == 0x05]
    assert "Q L1 Z" in got and "R L1 Z" in got, got
    assert not [v for v in got if v and "%" in v], got


@pytest.mark.parametrize("host, nested", [("n1", "n0"), ("n2", "n1"), ("n3", "n2"), ("n4", "n3")])
def test_a_full_build_of_a_current_genie_is_its_stored_copy(fx, host, nested):
    """A full build of a current genie is its stored copy."""
    from ctgedit.format.symbols import SymbolResolver

    lib = _lib(fx)
    recs = list(_tree(fx, host))
    k, end = _span(recs, nested)
    sub = GP.load_element(lib, nested)
    par = GP.P.forest_parents(sub.records)
    handles = {
        j: 60000 + j
        for j in range(1, len(sub.records))
        if not (sub.records[j].type_code in GP.INPUT_TCS and par[j] is not None)
    }
    m = [0] + [None] * (len(sub.records) - 1)
    out, _dms = GP._build_nested_copy(recs, k, end, sub, m, handles, SymbolResolver.for_page(lib), full=True)
    assert len(out) == end - k
    for j in range(1, end - k):
        a, b = GP.decode(out[j]), GP.decode(recs[k + j])
        assert (a.tc, a.dw, a.cf) == (b.tc, b.dw, b.cf), j
        assert [[(list(x.pairs), x.ov) for x in lv] for lv in a.entries] == [
            [(list(x.pairs), x.ov) for x in lv] for lv in b.entries
        ], (j, _levels(recs[k + j]))


# ------------------------------------------------------------ a member the genie gained
def test_a_member_gained_among_kept_ones_is_bound_in_an_element_file(fx):
    """A member gained among kept ones is bound in an element file."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.open_element(lib, "n0")
    d.create_object("text", (80, 12, 140, 28), text="%Label% X")
    d.save()
    libraries_changed()
    _update(fx, "n1")
    recs = _tree(fx, "n1")
    k, end = _span(recs, "n0")
    gained = next(recs[j] for j in range(k, end) if _txt(recs[j]) == "%Name% L1 X")
    assert _levels(gained)[1:] == [
        [[("Label", "\n")], [("Label", " L1")], [("Label", "%Name% L1")]],
        [[("Name", "\n")], [], []],
    ]
    assert list(GP.declared_params(GP.load_element(lib, "n1"))) == ["Name"]


def test_a_parameterless_genie_keeps_its_bindings_of_a_genie_it_gained(fx):
    """pp (no parameters of its own) places n0 with Label = 'DEF' as a second copy: in the host
    hh that level is pp's binding of n0, so hh and its pages show 'DEF' as a fresh pp does."""
    from ctgedit.editing import ElementDocument, libraries_changed

    mine = os.path.join(fx.project("FxLib"), "mylib.ctm")
    d = ElementDocument.new_element(mine, "pp", "genie")
    d.place_library_element("fxnest", "n0", "ctm", (10, 10), params={"Label": "ABC"})
    d.save()
    libraries_changed()
    assert not GP.declared_params(GP.load_element(mine, "pp"))
    d = ElementDocument.new_element(mine, "hh", "genie")
    d.create_object("text", (0, 0, 80, 16), text="%Tag%")
    d.place_library_element("mylib", "pp", "ctm", (10, 40))
    d.save()
    libraries_changed()
    d = ElementDocument.open_element(mine, "pp")
    d.place_library_element("fxnest", "n0", "ctm", (10, 40), params={"Label": "DEF"})
    d.save()
    libraries_changed()
    d = ElementDocument.open_element(mine, "hh")
    assert d.nested_genie_changes() == {("mylib", "pp")}
    d.update_nested_genies()
    d.save()
    libraries_changed()
    hh = E.open_element(mine, "hh").tree
    assert [_txt(p) for p in hh if p.type_code == 0x05] == [None, "ABC", "DEF"]
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element("mylib", "hh", "ctm", (400, 300), params={"Tag": "T"})
    assert [_txt(p) for p in pd.parsed.page.objects[n:] if p.type_code == 0x05] == ["T", "ABC", "DEF"]


# ------------------------------------------------------------ AN strings of an aligned copy
def test_an_aligned_copy_names_its_element_records(fx):
    """n1's copy of n0 deleted and placed again: n2's copy takes the AN strings of n1's records,
    so a later second n0 in n1 does not take the first copy's records in n2."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.open_element(lib, "n1")
    g = next(k for k, o in enumerate(d.parsed.page.objects) if o.subtype_size & 0x200)
    d.place_library_element("fxnest", "n0", "ctm", (10, 70), params={"Label": "%Name% NEW"})
    d.delete_objects([g])
    d.save()
    libraries_changed()
    _update(fx, "n2")
    n1, n2 = _tree(fx, "n1"), _tree(fx, "n2")
    k, end = _span(n2, "n1")
    assert [p.an_str for p in n2[k + 1 : end]] == [str(G.get_handle(p)) for p in n1[1:]]
    d = ElementDocument.open_element(lib, "n1")
    d.place_library_element("fxnest", "n0", "ctm", (150, 10), params={"Label": "%Name% SECOND"})
    d.save()
    libraries_changed()
    _update(fx, "n2")
    n1, n2 = _tree(fx, "n1"), _tree(fx, "n2")
    k, end = _span(n2, "n1")
    assert [p.an_str for p in n2[k + 1 : end]] == [str(G.get_handle(p)) for p in n1[1:]]
    assert "Host NEW" in _placed_texts(fx, "n2", {"Name": "Host"})


def test_rekey_copy_renames_only_the_members_that_name_another_record(fx):
    from ctgedit.format import writer

    n1, n2 = list(_tree(fx, "n1")), list(_tree(fx, "n2"))
    k, end = _span(n2, "n1")
    assert GP._rekey_copy(n2, k, end, n1) == 0  # (the fixture names them all)
    stale = GP.decode(n2[k + 2])
    stale.an = "77"
    n2[k + 2] = GP.to_po(GP.encode(stale))
    root = GP.decode(n2[k])
    root.an = "99"
    n2[k] = GP.to_po(GP.encode(root))
    kept = writer.object_payload(n2[k + 1])
    assert GP._rekey_copy(n2, k, end, n1) == 1
    assert n2[k + 2].an_str == str(G.get_handle(n1[2]))
    assert n2[k].an_str == "99"  # (the root keeps its string)
    assert writer.object_payload(n2[k + 1]) == kept


# ------------------------------------------------------------ the genie bit
def test_a_group_in_place_of_a_nested_genie_does_not_line_up(fx):
    """n1's copy of n0 replaced by a plain group of the same types and child counts: n2's copy
    follows the group (no nested genie root left) and its text sits where a fresh placement puts it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.open_element(lib, "n0")
    d.create_object("rectangle", rect=(70, 10, 80, 20))
    d.save()
    libraries_changed()
    _update(fx, "n1")
    _update(fx, "n2")
    d = ElementDocument.open_element(lib, "n1")
    g = next(k for k, o in enumerate(d.parsed.page.objects) if o.subtype_size & 0x200)
    d.delete_objects([g])
    t = d.create_object("text", (10, 40, 90, 56), text="plain")
    r = d.create_object("rectangle", rect=(70, 40, 80, 50))
    d.group_objects([t, r])
    d.save()
    libraries_changed()
    n1 = _tree(fx, "n1")
    _update(fx, "n2")
    n2 = _tree(fx, "n2")
    k, end = _span(n2, "n1")
    assert [(p.type_code, p.subtype_size & 0x200) for p in n2[k + 1 : end]] == [
        (p.type_code, p.subtype_size & 0x200) for p in n1[1:]
    ]
    text = next(p for p in n2[k:end] if _txt(p) == "plain")
    twin = next(p for p in n1 if _txt(p) == "plain")
    # (a new member: its element record's levels and n2's own after them, as the copy binds
    # its kept members (CtDraw's element save, LIVE), not the old root's text with
    # n0's binding level)
    assert _levels(text)[:2] == _levels(twin) and _levels(text)[2:] == [[[], [], []]]
    # where a fresh placement of n1 in n2 puts it
    d = ElementDocument.open_element(lib, "n2")
    i = d.place_library_element("fxnest", "n1", "ctm", (10, 40), params={"Name": "%Name%"})
    fresh = next(p for p in d.parsed.page.objects[i:] if _txt(p) == "plain")
    assert GP.decode(text).rect1[:2] == GP.decode(fresh).rect1[:2]


def test_aligns_compares_the_genie_bit_below_the_root():
    from ctgedit.format.page import PageObject

    def po(flags, cf=0, tc=0x08):
        return PageObject(0, 0, 0, 0, cf, tc, flags, "-1")

    sub = [po(0x0A, 1), po(0x208, 1), po(0x18, 0, 0x05)]
    copy = [po(0x209, 1), po(0x208, 1), po(0x18, 0, 0x05)]
    assert GP._aligns(sub, copy, 0, 3)
    copy[1] = po(0x08, 1)
    assert not GP._aligns(sub, copy, 0, 3)
