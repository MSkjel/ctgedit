"""Recomposing nested genie copies after a library save."""

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402


@pytest.fixture
def fx(fx_install, monkeypatch):
    """The fake install, each save with a stamp of its own (two saves in one second read
    as one version)."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _mine(fx):
    return os.path.join(fx.project("FxLib"), "mylib.ctm")


def _tree(lib, name: str):
    return E.open_element(lib, name).tree


def _levels(po):
    r = GP.decode(po)
    return [[list(b.pairs) for b in lv] for lv in (r.entries or [])]


def _txt(po):
    r = GP.decode(po)
    return dict(r.entries[0][1].pairs).get("TXT_OFF") if r.entries else None


def _tmpl(po):
    r = GP.decode(po)
    return dict(r.entries[0][2].pairs).get("TXT_OFF") if r.entries else None


def _span(recs, element: str):
    """(start, end) of the outermost copy of ``element`` in ``recs``."""
    for k, po in enumerate(recs):
        if k and po.class_flag and (po.subtype_size & 0x201) == 0x201:
            ref = GP.reference_from_object(po)
            if ref.element.lower() == element.lower():
                return k, GP._span_end(recs, k)
    raise AssertionError("no copy of %s" % element)


def _edit(lib, name: str, edit) -> None:
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib, name)
    edit(d)
    d.save()
    libraries_changed()


def _update(lib, host: str, report=None) -> None:
    """``host`` composes its changed nested genies and is saved (the chain's step)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib, host)
    assert d.nested_genie_changes(), host
    d.update_nested_genies()
    d.save(report=report) if report is not None else d.save()
    libraries_changed()


def _own_text(d) -> int:
    par = GP.P.forest_parents(d.parsed.page.objects)
    return next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05 and par[k] is None)


def _placed(fx, lib_name: str, element: str, params=None):
    """The records of a fresh placement of ``element`` on a page (in memory)."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element(lib_name, element, "ctm", (400, 300), params=params)
    return pd.parsed.page.objects[n:]


def _geom(po):
    r = GP.decode(po)
    return (r.anchor, r.rect1, r.rect2, [(m, tuple(round(x, 6) for x in s)) for m, s in r.slots])


# -------------------------------------------------------------- a genie without parameters
def _parameterless_chain(fx):
    """pg (a rectangle, no parameters) placed into hh (a text '%Tag%') and into hp (none),
    and h3 placing hp: the copy root of pg in hh carries the host's level only, empty
    empty (CtDraw's paste, the fixture oracle's chain4/param_gained)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    mine = _mine(fx)
    d = ElementDocument.new_element(mine, "pg", "genie")
    d.create_object("rectangle", rect=(0, 0, 40, 20))
    d.save()
    libraries_changed()
    for name, own in (("hh", "text"), ("hp", "rect")):
        d = ElementDocument.new_element(mine, name, "genie")
        if own == "text":
            d.create_object("text", (0, 0, 80, 16), text="%Tag%")
        else:
            d.create_object("rectangle", rect=(0, 0, 8, 8))
        d.place_library_element("mylib", "pg", "ctm", (10, 40))
        d.save()
        libraries_changed()
    d = ElementDocument.new_element(mine, "h3", "genie")
    d.create_object("text", (0, 0, 80, 16), text="%Top%")
    d.place_library_element("mylib", "hp", "ctm", (200, 40))
    d.save()
    libraries_changed()
    k, _end = _span(_tree(mine, "hh"), "pg")
    assert len(GP.decode(_tree(mine, "hh")[k]).entries) == 2  # (nothing bound)
    return mine


def _rects(recs, tc=0x0F):
    return [GP.decode(p).rect1 for p in recs if p.type_code == tc]


def test_a_parameterless_genie_moved_reaches_its_hosts(fx):
    """pg's rectangle moved 5 px: hh's and hp's copies and h3's copy of hp's copy move with
    it, and the hosts read current after their update."""
    mine = _parameterless_chain(fx)
    _edit(mine, "pg", lambda d: d.move_objects([0], 5, 5))
    for host in ("hh", "hp"):
        _update(mine, host)
        assert (15, 45, 55, 65) in _rects(_tree(mine, host)), host
    _update(mine, "h3")
    assert (215, 85, 255, 105) in _rects(_tree(mine, "h3"))
    from ctgedit.editing import ElementDocument

    for host in ("hh", "hp", "h3"):
        d = ElementDocument.open_element(mine, host)
        assert not d.nested_genie_changes(), host
        assert d.nested_update_ops(every=True) == ([], None), host


def test_a_parameterless_copy_gains_a_record(fx):
    """A rectangle added to pg: the hosts' copies of it are composed again (no stale_nested)."""
    mine = _parameterless_chain(fx)
    _edit(mine, "pg", lambda d: d.create_object("rectangle", rect=(50, 0, 70, 20)))
    rep = {}
    _update(mine, "hh", report=rep)
    assert not rep.get("stale_nested")
    assert (60, 40, 80, 60) in _rects(_tree(mine, "hh"))


def test_nested_build_of_a_copy_without_a_binding_level(fx):
    from ctgedit.format.symbols import SymbolResolver

    mine = _parameterless_chain(fx)
    recs = list(_tree(mine, "hh"))
    k, end = _span(recs, "pg")
    for j in range(k, end):
        # (a paste with e0 only, no binding level)
        r = GP.decode(recs[j])
        r.entries = r.entries[:1]
        recs[j] = GP.to_po(GP.encode(r))
    built = GP._nested_build(recs, k, end, GP.load_element(mine, "pg"), SymbolResolver.for_page(mine))
    assert [len(r.entries or ()) for r in built] == [1, 1]
    assert built[1].rect1 == GP.decode(recs[k + 1]).rect1


def test_a_copy_whose_geometry_cannot_be_derived_keeps_its_old_stamp(fx, monkeypatch):
    """When the reference build of an aligned copy fails, its geometry stays as stored: the
    save names the genie (stale_nested) and keeps its old stamp, so the host stays out of
    date for it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    _edit(lib, "n0", lambda d: d.move_objects([_own_text(d)], 5, 0))

    def fail(*a, **k):
        raise RuntimeError("no reference build")

    monkeypatch.setattr(GP, "_nested_build", fail)
    GP.clear_memo()
    d = ElementDocument.open_element(lib, "n1")
    rep = {}
    d.update_nested_genies(report=rep)
    d.save(report=rep)
    libraries_changed()
    assert ("fxnest", "n0") in rep["stale_nested"]
    assert ElementDocument.open_element(lib, "n1").nested_genie_changes() == {("fxnest", "n0")}


# -------------------------------------------- a member that starts using a parameter
def _n2_text_uses_name(d):
    d.set_text(_own_text(d), "N2 %Name%")


def test_a_member_that_starts_using_a_parameter_takes_the_hosts_binding(fx):
    """n2's text 'N2' edited to 'N2 %Name%': n3's and n4's copies take the host's binding, and
    a new placement of n3 or n4 shows the bound text."""
    lib = _lib(fx)
    _edit(lib, "n2", _n2_text_uses_name)
    _update(lib, "n3")
    n3 = _tree(lib, "n3")
    k, end = _span(n3, "n2")
    t = next(p for p in n3[k:end] if _tmpl(p) == "N2 %Name%")
    assert _txt(t) == "N2 %Name% 3"
    lv = _levels(t)
    assert lv[1] == [[("Name", "\n")], [("Name", " 3")], [("Name", "%Name% 3")]]
    assert lv[2][0] == [("Name", "\n")]
    _update(lib, "n4")
    n4 = _tree(lib, "n4")
    assert "N2 %Name% 3" in [_txt(p) for p in n4]
    placed = [_txt(p) for p in _placed(fx, "fxnest", "n3", {"Name": "P", "Extra": "E"})]
    assert "N2 P 3" in placed, placed
    placed = [_txt(p) for p in _placed(fx, "fxnest", "n4", {"Name": "Pump", "Extra": "X"})]
    assert "N2 Pump 3" in placed, placed


def test_a_page_composes_a_member_that_starts_using_a_parameter(fx):
    """The page path alone (n3 not saved again): a page placing n3 and brought up to date by
    its genie update shows the bound text."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pd = EditableDocument.open(fx.page("FxShapes"))
    n = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n3", "ctm", (400, 300), params={"Name": "P"})
    pd.save()
    libraries_changed()
    _edit(_lib(fx), "n2", _n2_text_uses_name)
    pd = EditableDocument.open(fx.page("FxShapes"))
    ch = pd.genie_changes()
    assert ch and pd.apply_genie_changes(ch)
    got = [_txt(p) for p in pd.parsed.page.objects[n:]]
    assert "N2 P 3" in got, got


def test_a_page_form_host_takes_the_binding_of_a_member_that_starts_using_it(fx):
    """A genie without parameters holding n2 placed with Name = Bound (the page form): its
    copy shows 'N2 Bound', as a placement of n2 does."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.new_element(lib, "nplain", "genie", canvas=(300, 200))
    d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={"Name": "Bound"})
    d.save()
    libraries_changed()
    _edit(lib, "n2", _n2_text_uses_name)
    _update(lib, "nplain")
    assert "N2 Bound" in [_txt(p) for p in _tree(lib, "nplain")]
    assert "N2 Bound" in [_txt(p) for p in _placed(fx, "fxnest", "nplain")]


def test_bind_declared_appends_the_roots_binding():
    from ctgedit.format.genie_place import Bag

    ents = [
        [Bag([]), Bag([]), Bag([])],
        [Bag([("Name", "\n")]), Bag([]), Bag([])],
        [Bag([]), Bag([]), Bag([])],
    ]
    root = [Bag([("Name", "\n")]), Bag([("Name", " 3")]), Bag([("Name", "%Name% 3")])]
    GP._bind_declared(ents, 1, root, page_form=False)
    assert ents[1][1].pairs == [("Name", " 3")] and ents[1][2].pairs == [("Name", "%Name% 3")]
    assert ents[2][0].pairs == [("Name", "\n")]
    page = [[Bag([]), Bag([]), Bag([])], [Bag([("Name", "\n")]), Bag([("Old", "1")]), Bag([])]]
    GP._bind_declared(page, 1, [Bag([]), Bag([("Name", "Bound")]), Bag([])], page_form=True)
    assert page[1][1].pairs == [("Old", "1"), ("Name", "Bound")] and not page[1][2].pairs


# ---------------------------------------------------- the same count in another order
def test_a_copy_restructured_with_as_many_records_gets_its_forest(fx):
    """n1's text brought in front of its n0 copy: n2's copy of n1 goes from [8, 5, 8, 5] to
    [8, 8, 5, 5] and takes the new forest, so n3's copy of n1 and an unbound page-form host
    follow it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.new_element(lib, "nplain", "genie", canvas=(300, 200))
    d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={})
    d.save()
    libraries_changed()
    _edit(lib, "n1", lambda d: d.reorder([_own_text(d)], "front"))
    for host in ("n2", "n3", "nplain"):
        # (a plain save composes the changed nested genies, as the chain's save does: one
        # normalisation)
        _edit(lib, host, lambda d: None)
    n3 = _tree(lib, "n3")
    par = GP.P.forest_parents(n3)
    k, end = _span(n3, "n2")
    for j in range(k, end):
        if n3[j].class_flag and n3[j].type_code == 0x08:
            kids = [GP.decode(n3[c]).rect1 for c in range(k, end) if par[c] == j]
            r1 = GP.decode(n3[j]).rect1
            assert r1 == (
                min(x[0] for x in kids),
                min(x[1] for x in kids),
                max(x[2] for x in kids),
                max(x[3] for x in kids),
            ), j
    vals = {_tmpl(p): _txt(p) for p in _tree(lib, "nplain") if p.type_code == 0x05}
    assert vals["%Label%"] == "L1", vals


# ------------------------------------------------------- a record the nested genie gained
def _gain_text(d):
    d.create_object("text", (80, 12, 140, 28), text="%Label% X")


def test_a_gained_member_carries_its_kept_siblings_levels_in_every_host(fx):
    """A gained member carries its kept siblings' levels in every host."""
    lib = _lib(fx)
    before = {n: list(GP.declared_params(GP.load_element(lib, n))) for n in ("n1", "n2", "n3", "n4")}
    _edit(lib, "n0", _gain_text)
    for k in (1, 2, 3, 4):
        el = "n%d" % k
        _update(lib, el)
        recs = _tree(lib, el)
        kept = [p for p in recs if _tmpl(p) == "%Label%"]
        gained = [p for p in recs if _tmpl(p) == "%Label% X"]
        assert len(kept) == len(gained) == 1, el
        kl, gl = _levels(kept[0]), _levels(gained[0])
        assert len(gl) == len(kl) == k + 2, el
        assert gl[1:] == kl[1:], el
        assert _txt(gained[0]) == _txt(kept[0]) + " X", el
    after = {n: list(GP.declared_params(GP.load_element(lib, n))) for n in before}
    assert after == before
    assert before["n1"] == ["Name"]


def test_a_gained_member_stays_a_clone_on_a_page(fx):
    """A gained member stays a clone on a page."""
    _edit(_lib(fx), "n0", _gain_text)
    recs = _placed(fx, "fxnest", "n1", {"Name": "Q"})
    kept = next(p for p in recs if _txt(p) == "Q L1")
    gained = next(p for p in recs if p.type_code == 0x05 and _txt(p) in ("X", " X"))
    assert len(_levels(gained)) == len(_levels(kept)) - 1
    assert not [v for lv in _levels(gained)[1:] for v in lv[1]]


# --------------------------------------------------- Q4: a text mirrored and mirrored back
def _mirror_n0_text(d):
    d.mirror_objects([_own_text(d)])


def test_a_mirrored_text_and_its_mirror_back_reach_the_hosts(fx):
    """A mirrored text and its mirror back reach the hosts."""
    lib = _lib(fx)
    fix0, fix1 = [_geom(p) for p in _tree(lib, "n0")], [_geom(p) for p in _tree(lib, "n1")]
    _edit(lib, "n0", _mirror_n0_text)
    t0 = GP.decode(next(p for p in _tree(lib, "n0") if p.type_code == 0x05))
    assert (t0.anchor, t0.rect1) == ((69, 23), (10, 10, 69, 26))
    assert [(m, s[0], s[4]) for m, s in t0.slots] == [(3, -1.0, 79.0)] * 3
    _update(lib, "n1")
    n1 = _tree(lib, "n1")
    k, end = _span(n1, "n0")
    t1 = GP.decode(n1[k + 1])
    assert (t1.anchor, t1.rect1) == ((69, 53), (-11, 40, 69, 56))
    assert GP.decode(n1[k]).rect1 == (-11, 40, 69, 56)
    assert GP.decode(n1[0]).rect1 == (-11, 10, 70, 56)
    _edit(lib, "n0", _mirror_n0_text)
    assert [_geom(p) for p in _tree(lib, "n0")] == fix0
    _update(lib, "n1")
    assert [_geom(p) for p in _tree(lib, "n1")] == fix1


def test_a_mirrored_text_is_placed_with_its_box_left_of_its_anchor(fx):
    """A mirrored text is placed with its box left of its anchor."""
    _edit(_lib(fx), "n0", _mirror_n0_text)
    root_x = GP.engine_root_extent(GP.load_element(_lib(fx), "n0"))[:2]
    assert root_x == (10, 10)
    recs = _placed(fx, "fxnest", "n0", {"Label": "MIRRORED"})
    root, text = GP.decode(recs[0]), GP.decode(recs[1])
    assert text.anchor == (459, 313)
    assert text.rect1[2] == 459 and text.rect1[0] < 400
    assert root.rect1 == text.rect1


def test_local_of_definition_takes_a_texts_mirror():
    from types import SimpleNamespace as NS

    ident = (0, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    mirror = (3, (-1.0, 0.0, 0.0, 1.0, 79.0, 0.0))
    move = (1, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0))
    text = NS(subtype_size=0x1A, type_code=0x05)
    assert GP._local_of_definition(NS(dw=0x18, tc=0x05, slots=[ident, mirror, mirror]), text)
    # (any other Local stays the copy's own: the LIVE state set's T(2, 0))
    assert not GP._local_of_definition(NS(dw=0x18, tc=0x05, slots=[ident, move, move]), text)
    assert not GP._local_of_definition(
        NS(dw=0x18, tc=0x0C, slots=[ident, move, move]), NS(subtype_size=0x1A, type_code=0x0C)
    )


# --------------------------------------------------------- copies kept in the page form
def test_a_genie_element_with_page_form_copies_is_placed_in_the_element_form(fx):
    """A genie element ('%Outer%' of its own) whose copy of n0 is stored in the PAGE form
    (bound '%Outer% Z' as a value, no templates, no element level: pasted from a page) is
    placed in the element form, with the bound value any save of the element gives."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format.symbols import SymbolResolver

    mine = _mine(fx)
    d = ElementDocument.new_element(mine, "pfh", "genie")
    d.create_object("text", (0, 0, 80, 16), text="%Outer%")
    d.place_library_element("fxnest", "n0", "ctm", (10, 40), params={"Label": "%Outer% Z"})
    d.save()
    libraries_changed()
    pe = E.open_element(mine, "pfh")
    tree = list(pe.tree)
    k, end = _span(tree, "n0")
    page_form = [
        GP.to_po(GP.encode(r))
        for r in GP._nested_build(
            tree, k, end, GP.load_element(_lib(fx), "n0"), SymbolResolver.for_page(mine)
        )
    ]
    assert len(GP.decode(page_form[0]).entries) == 2  # (no element level)
    assert not GP.decode(page_form[0]).entries[1][2].pairs  # (no templates)
    tree[k:end] = page_form
    E.save_element(pe, E.element_content(pe, objects=tree))
    libraries_changed()
    GP.clear_memo()
    assert ElementDocument.open_element(mine, "pfh").nested_genie_changes() == frozenset()
    got = [_txt(p) for p in _placed(fx, "mylib", "pfh", {"Outer": "Q"})]
    assert "Q Z" in got, got
    assert not [v for v in got if v and "%" in v], got


# ------------------------------------------- the left trim in a genie without parameters
def test_a_parameterless_host_is_placed_with_its_values_left_trimmed(fx):
    """A genie without parameters holding n2 placed with Name unbound: its element stores
    n0's text as 'L1' (the paste's left trim), and a placement shows 'L1', not ' L1'."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _lib(fx)
    d = ElementDocument.new_element(lib, "nplain", "genie", canvas=(300, 200))
    d.place_library_element("fxnest", "n2", "ctm", (100, 100), params={})
    d.save()
    libraries_changed()
    assert "L1" in [_txt(p) for p in _tree(lib, "nplain")]
    got = [_txt(p) for p in _placed(fx, "fxnest", "nplain")]
    assert "L1" in got and " L1" not in got, got
