"""A page composition recomposes nested copies older than their element."""

import pytest

import fxinstall
from ctgedit.format import genie_place as GP
from ctgedit.format import geometry as G
from ctgedit.format import placement as P
from ctgedit.format.symbols import SymbolResolver, reference_from_object

#: n0 .. n4 as stored: records per element (n0 is two records, each level adds two)
STORED = {"n0": 2, "n1": 4, "n2": 6, "n4": 10}


def test_merge_bag_keeps_the_copys_sheet_state_and_user_values():
    """A nested copy's '!*' property-sheet keys and its '<User>' Access values survive the
    merge, a page composition's and an element load's alike (LIVE: CtDraw's save of a user
    genie keeps them); other keys the current bag lacks are dropped, kept keys
    take the current value in place, new keys are appended in the current order."""
    stale = GP.Bag(
        [
            ("!*PropSheet.Focus", "#1403.#1407"),
            ("!*#1410", "#1411"),
            ("OLD", "x"),
            ("VISIBLE", "a"),
            ("COMMENT", "abc<User>"),
        ],
        7,
    )
    cur = GP.Bag([("VISIBLE", "b"), ("!*PropSheet.Focus", "#1403.#1407"), ("NEW", "y")])
    want = [
        ("!*PropSheet.Focus", "#1403.#1407"),
        ("!*#1410", "#1411"),
        ("VISIBLE", "b"),
        ("COMMENT", "abc<User>"),
        ("NEW", "y"),
    ]
    for keep in (False, True):
        got = GP.merge_bag(stale, cur, keep_sheet=keep)
        assert got.pairs == want
        assert got.ov == 7


def test_merge_bag_keeps_a_templated_value_in_place():
    """``templated``: a stale value whose key the merged template bag holds stays where it
    is (an inner binding level's bound value the current element leaves unbound)."""
    stale = GP.Bag([("Tag", "A_x"), ("Text 9", "t"), ("Num 9", "9")])
    cur = GP.Bag([("Tag", "_x")])
    assert GP.merge_bag(stale, cur).pairs == [("Tag", "_x")]
    got = GP.merge_bag(stale, cur, templated=frozenset({"tag", "text 9", "num 9"}))
    assert got.pairs == [("Tag", "_x"), ("Text 9", "t"), ("Num 9", "9")]


def test_memory_values_of_a_nested_member():
    """The value bag a stale copy's member merges against is the current record's as CtDraw
    holds it: a member of a genie nested in the element has its templates substituted with
    that genie's bound values, and a key that comes out empty is not there (LIVE: a middle
    element passing TAG through unbound; TXT_A merged away and re-derived at the end)."""
    E = lambda *p: GP.Bag(list(p))
    # e0: values (the file's save form: effective templates), templates; e1: the nested
    # genie's level (decls, values, templates); e2: the element's level
    none = GP.NO_DEFAULT
    unbound = [
        [E(), E(("TXTMODE", "0"), ("TXT_A", "%TAG%")), E(("TXTMODE", "0"), ("TXT_A", "%TAG%"))],
        [E(("TAG", none)), E(), E(("TAG", "%TAG%"))],
        [E(("TAG", none)), E(), E()],
    ]
    assert GP.memory_values(unbound).pairs == [("TXTMODE", "0")]
    bound = [
        [E(), E(("TXTMODE", "0"), ("TXT_A", "%TAG%_s")), E(("TXTMODE", "0"), ("TXT_A", "%TAG%"))],
        [E(("TAG", none)), E(("TAG", "_s")), E(("TAG", "%TAG%_s"))],
        [E(("TAG", none)), E(), E()],
    ]
    assert [k for k, _v in GP.memory_values(bound).pairs] == ["TXTMODE", "TXT_A"]
    direct = [[E(), E(("X", "1")), E(("X", "%P=1%"))], [E(("P", "1")), E(), E()]]
    assert GP.memory_values(direct) is direct[0][1]


def test_values_within_templates():
    """An element load leaves a nested copy record's value bag with only the keys its
    template bag holds (every nested copy record of the corpus libraries does)."""
    ent = [GP.Bag([]), GP.Bag([("!*PropSheet.Focus", "#1421.#1404"), ("A", "1")], 10), GP.Bag([("a", "%P%")])]
    GP._values_within_templates(ent)
    assert ent[1].pairs == [("A", "1")] and ent[1].ov == 10


def _nested_spans(recs):
    """(k, end, ref) of the outermost nested genie copies of an element's records."""
    out = []
    k = 1
    while k < len(recs):
        po = recs[k]
        if po.class_flag and (po.subtype_size & 0x201) == 0x201:
            end = GP.subtree_end(recs, k)
            out.append((k, end, reference_from_object(po)))
            k = end
            continue
        k += 1
    return out


@pytest.fixture()
def stale(fx_install):
    """fxnest.ctm of the fx_install copy after n0 gained a rectangle (saved alone): the
    library path. The stored copies of n0 in n1 .. n4 are now older than n0."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxnest.ctm")
    assert {n: len(GP.load_element(lib, n).records) for n in STORED} == STORED
    e = ElementDocument.open_element(lib, "n0")
    e.create_object("rectangle", (0, 60, 30, 80), colour=0x123456)
    e.save()
    libraries_changed()
    return lib


def test_element_save_view_keeps_the_stored_nested_copies(stale):
    """Without ``compose`` (the element window, genie_norm's save) nothing is restructured:
    the stale copies keep their records."""
    assert len(GP.load_element(stale, "n0").records) == STORED["n0"] + 1
    for n in ("n1", "n2", "n4"):
        assert len(GP.load_element(stale, n).records) == STORED[n], n


def test_compose_load_recomposes_the_stale_nested_copies(stale):
    """With ``compose`` each level's stale copy of n0 takes the rectangle n0 gained (one
    record more at every level), each nested span aligns with its current element, and
    the handles stay unique in the element."""
    for n in ("n1", "n2", "n4"):
        el = GP.load_element(stale, n, compose=True)
        assert el.compose
        assert len(el.records) == STORED[n] + 1, n
    sub = GP.load_element(stale, "n4", compose=True)
    res = SymbolResolver.for_page(stale)
    spans = _nested_spans(sub.records)
    assert spans
    for k, end, ref in spans:
        rs = res.resolve(ref)
        cur = GP.load_element(rs.library_path, ref.element, compose=True)
        assert GP._aligns(cur.records, sub.records, k, end), ref.element
    par = P.forest_parents(sub.records)
    hs = [
        G.get_handle(po)
        for j, po in enumerate(sub.records)
        if not (po.type_code in GP.INPUT_TCS and par[j] is not None)
    ]
    assert len(hs) == len(set(hs))


def test_compose_load_fills_the_stale_copies_symbol_sets():
    """The corpus case: the current nested elements fill symbol sets the stored copies hold
    empty, with [DummyMarker + state] (two states each, their class section counting them
    as the element's does)."""
    pytest.skip(
        "needs fixture: a genie library whose stored nested copies hold empty symbol "
        "sets that the current nested elements fill with [DummyMarker + state], and "
        "nested copy records with '!*' property-sheet keys"
    )


@pytest.mark.golden
def test_compose_load_is_ctdraws_structure(fx_install):
    """CtDraw's own placement of fxnest.n4 (the oracle's place:nested_genie, onto a blank
    page): the same (type code, child count) sequence as ctgedit's composition, and every
    record linked with its element record by its AN string."""
    from ctgedit.format.page import parse_ctg_bytes

    objs = parse_ctg_bytes(fxinstall.oracle_bytes("place", "nested_genie", "page.ctg")).page.objects
    root = next(i for i, po in enumerate(objs) if po.class_flag and (po.subtype_size & 0x201) == 0x201)
    inst = objs[root : GP.subtree_end(objs, root)]
    el = GP.load_element(fx_install.file("FxLib", "fxnest.ctm"), "n4", compose=True)
    assert [(po.type_code, po.class_flag) for po in inst] == [
        (po.type_code, po.class_flag) for po in el.records
    ]
    m = GP.match_instance(inst, el.records)
    assert m == list(range(len(inst)))
    assert all(inst[k].an_str == str(G.get_handle(el.records[k])) for k in range(1, len(inst)))


def test_compose_load_of_a_stale_copy_is_ctdraws_structure():
    """CtDraw's composition of a page placing a genie whose nested copies are stale (on the
    corpus: handles and AN strings unchanged, a copy's '!*' keys kept)."""
    pytest.skip(
        "needs fixture: a page CtDraw composed from a genie whose stored nested copies "
        "are older than their elements (an oracle unit: CtDraw's load + save of it)"
    )


def test_match_instance_keeps_the_handles_of_linked_records(stale, fx_page):
    """FxGenies' FxNested, composed before n0 changed, against the current composition of
    n4: every stored record is linked by its AN string, and only the one record the
    composition adds (n0's new rectangle) has no stored twin (it gets a new handle)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    page = fx_page("FxGenies")
    (k,) = EditableDocument.open(page).find_objects_by_name("FxNested")
    objs = parse_ctg(page).page.objects
    inst = objs[k : GP.subtree_end(objs, k)]
    assert len(inst) == STORED["n4"]
    el = GP.load_element(stale, "n4", compose=True)
    m = GP.match_instance(inst, el.records)
    assert sum(1 for s in m if s is None) == 1
    assert sorted(s for s in m if s is not None) == list(range(len(inst)))
