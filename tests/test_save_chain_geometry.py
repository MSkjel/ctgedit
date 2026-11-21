"""Geometry of recomposed nested genie copies and genie placement."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import placement as P  # noqa: E402


@pytest.fixture
def stamps(monkeypatch):
    """Each save a stamp of its own (two saves within a second otherwise share one)."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _tree(fx, name: str):
    return E.open_element(_lib(fx), name).tree


def _compose(fx, name: str = "n1") -> None:
    """The host ``name`` takes its changed n0 copy (the chain's step for it) and is saved."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), name)
    assert d.nested_genie_changes()
    d.update_nested_genies()
    d.save()
    libraries_changed()


def _edit_n0(fx, edit) -> None:
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    edit(d)
    d.save()
    libraries_changed()


def _linked(d) -> int:
    """The index of the linked symbol placed in the open element ``d``."""
    return next(k for k, o in enumerate(d.parsed.page.objects) if k and o.subtype_size & 0x201 == 0x1)


def _shapes(records, tc: int):
    return [GP.decode(p) for p in records if p.type_code == tc]


def _off(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2], a[3] - b[3])


# ------------------------------------------------------------- Cut Link in the definition
def test_cut_link_and_a_move_in_one_save_reach_the_hosts(fx_install, stamps):
    """fx_box placed in n0 at (80, 10), then Cut Link and a 10 px move right before the
    save: n1's copy takes the new Local, not twice the offset plus the move."""
    _edit_n0(fx_install, lambda d: d.place_library_element("fxsym", "fx_box", "ctl", (80, 10)))
    _compose(fx_install)
    assert [tuple(r.rect1) for r in _shapes(_tree(fx_install, "n1"), 0x0F)] == [(80, 40, 120, 70)]

    def cut_and_move(d):
        g = d.break_library_link(_linked(d))
        d.move_objects([g], 10, 0)

    _edit_n0(fx_install, cut_and_move)
    assert [tuple(r.rect1) for r in _shapes(_tree(fx_install, "n0"), 0x0F)] == [(90, 10, 130, 40)]
    _compose(fx_install)
    recs = _tree(fx_install, "n1")
    assert [tuple(r.rect1) for r in _shapes(recs, 0x0F)] == [(90, 40, 130, 70)]
    k = next(j for j, p in enumerate(recs) if p.type_code == 0x0F)
    assert GP.decode(recs[k - 1]).slots[1][0] == 0  # (the group took the element's slots)


def test_cut_link_of_a_symbol_turned_on_its_own_box_drops_the_turn(fx_install, stamps):
    """fx_box at the element origin turned half round maps its box onto itself: the copy takes
    the cut-linked group's slots, so asymmetric art is not turned back in a host."""

    def place(d):
        i = d.place_library_element("fxsym", "fx_box", "ctl", (0, 0))
        d.rotate_objects([i])
        d.rotate_objects([i])

    _edit_n0(fx_install, place)
    _compose(fx_install)
    _edit_n0(fx_install, lambda d: d.break_library_link(_linked(d)))
    _compose(fx_install)
    recs = _tree(fx_install, "n1")
    k = next(j for j, p in enumerate(recs) if p.type_code == 0x0F)
    group, rect = GP.decode(recs[k - 1]), GP.decode(recs[k])
    assert group.slots[1][0] == 0
    m3, t3 = rect.slots[2]
    assert tuple(t3[:4]) == (1.0, 0.0, 0.0, 1.0)  # (no turn left in its Total)
    assert tuple(rect.rect1) == (0, 30, 40, 60)


def test_cut_link_of_a_turned_group_symbol_keeps_its_rectangle_in_place(fx_install, stamps):
    """fx_group (a rectangle and a text) turned half round at (150, 10) in n0, then Cut
    Link: n1's rectangle stays at (150, 40, 210, 80) and its text where n0 has it."""

    def place(d):
        i = d.place_library_element("fxsym", "fx_group", "ctl", (150, 10))
        d.rotate_objects([i])
        d.rotate_objects([i])

    _edit_n0(fx_install, place)
    _compose(fx_install)
    _edit_n0(fx_install, lambda d: d.break_library_link(_linked(d)))
    _compose(fx_install)
    n0, n1 = _tree(fx_install, "n0"), _tree(fx_install, "n1")
    (r0,), (r1,) = _shapes(n0, 0x0F), _shapes(n1, 0x0F)
    assert tuple(r1.rect1) == (150, 40, 210, 80)
    assert _off(r1.rect1, r0.rect1) == (0, 30, 0, 30)
    t0 = [r for r in _shapes(n0, 0x05) if r.rect1[0] >= 150]
    t1 = [r for r in _shapes(n1, 0x05) if r.rect1[0] >= 150]
    assert len(t0) == len(t1) == 1
    assert _off(t1[0].rect1, t0[0].rect1) == (0, 30, 0, 30)


def test_own_local_rule_by_the_records_kinds(fx_install):
    """_own_local's rule, by record kind."""
    rec = GP.Rec.__new__(GP.Rec)

    def stored(tc, dw, local=(1, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0))):
        r = GP.Rec.__new__(GP.Rec)
        r.tc, r.dw = tc, dw
        r.slots = [(0, P.IDENT), local, (1, (1.0, 0.0, 0.0, 1.0, 2.0, 30.0))]
        return r

    def element_record(tc, dw):
        from ctgedit.format.page import PageObject

        return PageObject(
            index=0,
            record_offset=0,
            payload_size=0,
            next_link=0,
            class_flag=1,
            type_code=tc,
            subtype_size=dw,
            an_str="1",
            body_offset=0,
            body_raw=b"",
        )

    rec.slots = [(0, P.IDENT), (0, P.IDENT), (1, (1.0, 0.0, 0.0, 1.0, 0.0, 30.0))]
    symset = 0x0C
    assert GP._own_local(stored(symset, 0x18), rec, element_record(symset, 0x18))
    assert GP._own_local(stored(0x05, 0x18), rec, element_record(0x05, 0x1A))
    mirror = (3, (-1.0, 0.0, 0.0, 1.0, 79.0, 0.0))
    assert not GP._own_local(stored(0x05, 0x18, mirror), rec, element_record(0x05, 0x1A))
    assert not GP._own_local(stored(0x08, 0x18), rec, element_record(0x08, 0x0A))
    assert not GP._own_local(stored(0x08, 0x18), rec, element_record(0x08, 0x09))
    assert not GP._own_local(stored(0x07, 0x18), rec, element_record(0x07, 0x19))
    assert GP._own_local(stored(0x08, 0x218), rec, element_record(0x08, 0x208))
    # (no stored Local, or one the current element has too: nothing to keep)
    assert not GP._own_local(stored(symset, 0x18, (0, P.IDENT)), rec, element_record(symset, 0x18))
    rec.slots[1] = (1, (1.0, 0.0, 0.0, 1.0, 5.0, 0.0))
    assert not GP._own_local(stored(symset, 0x18), rec, element_record(symset, 0x18))


# ------------------------------------------------------------ the boxes of turned texts
def _place_on_page(fx, element: str, at, params):
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", element, "ctm", at, params=params)
    return pd.parsed.page.objects[n:]


def test_a_mirrored_text_keeps_its_box_at_its_anchor(fx_install, stamps):
    """A mirrored text keeps its box at its anchor."""

    def mirror(d):
        i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
        d.mirror_objects([i])

    _edit_n0(fx_install, mirror)
    (t0,) = _shapes(_tree(fx_install, "n0"), 0x05)
    assert t0.slots[1][0] and t0.rect1[2] == t0.anchor[0]
    assert (t0.anchor, t0.rect1) == ((69, 23), (10, 10, 69, 26))
    placed = _place_on_page(fx_install, "n0", (400, 300), {"Label": "M"})
    root, text = GP.decode(placed[0]), GP.decode(placed[1])
    assert text.anchor == (459, 313) and text.rect1[2] == 459
    assert root.rect1 == text.rect1
    _compose(fx_install)
    t1 = [r for r in _shapes(_tree(fx_install, "n1"), 0x05) if r.slots[1][0]]
    assert len(t1) == 1
    assert t1[0].anchor == (t0.anchor[0], t0.anchor[1] + 30)
    assert t1[0].rect1[2] == t0.anchor[0] and t1[0].rect1[1] == t0.rect1[1] + 30


def test_a_text_of_a_turned_symbol_in_a_genie_stays_on_its_symbol(fx_install, stamps):
    """fx_group turned a quarter in n0: its text's box is turned once, in a placement and in
    every host, so the text keeps its place on the rectangle as n0 has it."""

    def place(d):
        i = d.place_library_element("fxsym", "fx_group", "ctl", (150, 10))
        d.rotate_objects([i])

    _edit_n0(fx_install, place)
    n0 = _tree(fx_install, "n0")
    (r0,) = _shapes(n0, 0x0F)
    (t0,) = [r for r in _shapes(n0, 0x05) if r.rect1[0] >= 150]
    want = _off(t0.rect1, r0.rect1)
    placed = _place_on_page(fx_install, "n0", (400, 300), {"Label": "X"})
    (rp,) = _shapes(placed, 0x0F)
    (tp,) = [r for r in _shapes(placed, 0x05) if r.rect1[0] >= rp.rect1[0]]
    assert _off(tp.rect1, rp.rect1) == want
    _compose(fx_install)
    n1 = _tree(fx_install, "n1")
    (r1,) = _shapes(n1, 0x0F)
    (t1,) = [r for r in _shapes(n1, 0x05) if r.rect1[0] >= r1.rect1[0]]
    assert _off(t1.rect1, r1.rect1) == want
    assert _off(r1.rect1, r0.rect1) == (0, 30, 0, 30)


# ----------------------------------------------------- Compose Again is a fixed point
def test_compose_again_twice_on_a_restructured_scaled_copy_finds_nothing(fx_install, stamps):
    """n1's copy of n0 (with fx_box in it) scaled to (10, 40, 70, 57), then n0 gains a
    text: a second Compose Again after the first finds nothing (the Totals are composed in
    the engine's order the aligned pass uses)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx_install)
    _edit_n0(fx_install, lambda d: d.place_library_element("fxsym", "fx_box", "ctl", (80, 10)))
    _compose(fx_install)
    n1 = ElementDocument.open_element(lib, "n1")
    root = next(k for k, o in enumerate(n1.parsed.page.objects) if k and o.subtype_size & 0x201 == 0x201)
    n1.set_object_rect(root, (10, 40, 70, 57))
    n1.save()
    libraries_changed()
    _edit_n0(fx_install, lambda d: d.create_object("text", (10, 60, 120, 76), text="NEWOBJ"))
    n1 = ElementDocument.open_element(lib, "n1")
    assert n1.update_nested_genies(every=True)
    n1.save()
    libraries_changed()
    n1 = ElementDocument.open_element(lib, "n1")
    assert n1.nested_update_ops(every=True) == ([], None)


# --------------------------------------- an unlinked copy's Totals under its identity root
def _totals_follow_slot2(records):
    """Each record's Total (slot 3) is its slot 2 composed with its ancestors' in the engine's
    order (placement.apply_parent over their links, nearest first)."""
    par = P.forest_parents(records)
    for j, po in enumerate(records):
        (_s1, (m2, s2, _o, _n), (m3, s3, _o3, _n3)), _e = P.read_slots(po.body_raw)
        chain, q = [], par[j]
        while q is not None:
            chain.append(P.link_of(records[q]))
            q = par[q]
        want, wm = P.apply_parent(s2, m2, chain or None)
        got = s3 if m3 else P.IDENT
        if any(abs(a - b) > 1e-9 for a, b in zip(got, want if wm else P.IDENT)):
            return j, got, want
    return None


def test_an_unlinked_symbol_set_copy_composes_its_totals_under_the_identity_root(fx_install):
    """fx_set (a symbol set, its element origin e (8, 8)) pasted unlinked at (100, 100): the
    copy's root drops its T(-e), and the members' Totals were composed before that, with it:
    the set's slot 2 T(100, 100) under Total T(92, 92)."""
    out = GP.place_symbol(
        fx_install.page("FxShapes"), fx_install.file("FxLib", "fxsym.ctl"), "fx_set", 100, 100, linked=False
    )
    recs = out["records"]
    assert [s[0] for s in P.read_slots(recs[0].body_raw)[0]] == [0, 0, 0]
    assert _totals_follow_slot2(recs) is None
    sets = [GP.decode(po) for po in recs if po.type_code == 0x0C]
    assert sets and all(tuple(r.slots[2][1][4:]) == (100.0, 100.0) for r in sets)


def test_cut_link_of_a_symbol_set_at_its_own_place_reaches_the_hosts(fx_install, stamps):
    """fx_set placed in n0 at (0, 0), its own place, then Cut Link: the members' Totals are
    derived again and n1 draws the set where n0 has it."""
    _edit_n0(fx_install, lambda d: d.place_library_element("fxsym", "fx_set", "ctl", (0, 0)))
    _compose(fx_install)
    _edit_n0(fx_install, lambda d: d.break_library_link(_linked(d)))
    n0 = list(_tree(fx_install, "n0"))
    g = P.forest_parents(n0)[next(j for j, po in enumerate(n0) if po.type_code == 0x0C)]
    end, pending = g, 1
    while pending:
        pending += n0[end].class_flag - 1
        end += 1
    assert _totals_follow_slot2(n0[g:end]) is None  # (the cut group as a forest of its own)
    _compose(fx_install)
    n1 = _tree(fx_install, "n1")
    a, b = _shapes(n0, 0x11), _shapes(n1, 0x11)
    assert a and len(a) == len(b)
    assert all(_off(y.rect1, x.rect1) == (0, 30, 0, 30) for x, y in zip(a, b))


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
