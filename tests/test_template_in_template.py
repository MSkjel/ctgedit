"""A template holding another template's copy."""

import os
import shutil

import pytest

from ctgedit.format import genie_place as GP
from ctgedit.format import geometry as geom
from ctgedit.format import links, writer

TPL = ("fx_style", "normal@xga")
RECT = (300, 300, 400, 360)


def _outer(fx, name="made@XGA"):
    """Copy to Library of FxTplLinked's template copy into FxMain mytpl.ctt ``name``: a
    template holding normal@XGA's copy (its root dwFlags 0xd, as CtDraw's PageNewTemplate
    writes it). Returns the library path."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP

    d = EditableDocument.open(fx.page("FxTplLinked"))
    a, b, _ref = TP.template_roots(d.parsed.page.objects)[0]
    lib = os.path.join(fx.project("FxMain"), "mytpl.ctt")
    assert d.copy_to_library(list(range(a, b)), lib, name)
    libraries_changed()
    return lib


def _inner(fx):
    return fx.file("FxStyle", "fx_style.ctt")


def _inner_gains(fx, kind="rectangle", rect=RECT, text=None):
    """normal@XGA gains an object (CtDraw's DrawRectangle of the oracle's Q2) and is saved."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_inner(fx), "normal@XGA")
    d.create_object(kind, rect, text=text)
    d.save()
    libraries_changed()
    return d


def _tree(lib, name):
    from ctgedit.format import element as E

    return list(E.open_element(lib, name).tree)


def _entry(lib, name, key=TPL):
    want = "%s.%s.<" % key
    got = [n for _g, n in GP.element_table_entries(lib, name) if n.lower().startswith(want)]
    assert len(got) == 1, got
    return got[0]


def _labels(lib, name):
    from ctgedit.format.symbols import open_library

    pb = open_library(lib).element(name)
    t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
    return [n for _g, n in (t[1] if t else []) if n.startswith("\\")]


def _current(fx):
    return GP.current_link_entry(_inner(fx), "fx_style", "normal@XGA")[1]


def _holds(recs, mark: bytes) -> int:
    return sum(mark in writer.object_payload(po) for po in recs)


def _copy_span(recs):
    """(start, end) of the one template copy in ``recs``: its root links a '.<ctt' element and
    has no genie bit (found without the code under test)."""
    out = [
        (k, GP.subtree_end(recs, k))
        for k, po in enumerate(recs)
        if k
        and po.class_flag
        and po.subtype_size & 0x1
        and not po.subtype_size & 0x200
        and (GP.decode(po).link or "").lower().endswith(".<ctt")
    ]
    assert len(out) == 1, out
    return out[0]


def _rect_records(recs):
    return [
        k
        for k, po in enumerate(recs)
        if po.type_code != 0x05
        and geom.parse_geom_header(po.body_raw) is not None
        and tuple(geom.norm_rect(geom.parse_geom_header(po.body_raw).rect1)) == RECT
    ]


# ------------------------------------------------------------------ the element's save
def test_the_save_composes_the_copy_of_a_template_that_changed(fx_install):
    """normal@XGA gains a rectangle; the outer template's plain save appends it as the
    copy's last member, its an the rectangle's handle in normal@XGA, the copy root's child
    count one more, and quotes normal@XGA's new stamp (LIVE Q2: CtDraw's load + save of
    fxouter@XGA)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _outer(fx_install)
    before = _tree(lib, "made@XGA")
    a, b = _copy_span(before)
    inner = _inner_gains(fx_install)
    twin = inner.parsed.page.objects[-1]
    x = ElementDocument.open_element(lib, "made@XGA")
    assert TPL in x.nested_genie_changes()
    x.save()
    libraries_changed()
    after = _tree(lib, "made@XGA")
    a2, b2 = _copy_span(after)
    assert (a2, b2) == (a, b + 1)
    assert after[a2].class_flag == before[a].class_flag + 1
    assert _rect_records(after) == [b2 - 1]
    assert after[b2 - 1].an_str == str(geom.get_handle(twin))
    assert after[a2].subtype_size == before[a].subtype_size  # (its lock as stored)
    assert links.entry_stamp(_entry(lib, "made@XGA")) == links.entry_stamp(_current(fx_install))
    assert ElementDocument.open_element(lib, "made@XGA").nested_genie_changes() == frozenset()


def test_update_users_brings_the_outer_template_and_its_pages_up_to_date(fx_install):
    """Used By > Update Users after normal@XGA changed: the outer template is saved with its
    copy composed again, then a page linked to it is updated and holds the change. The
    page waits for the outer template before the run."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import genieusage as GU, updatepages as U

    lib = _outer(fx_install)
    page = os.path.join(fx_install.project("FxMain"), "FromX.ctg")
    EditableDocument.new_from_template("mytpl.made@XGA.<ctt", page, linked=True)
    libraries_changed()
    _inner_gains(fx_install, "text", (20, 20, 200, 40), text="TPLNEW")
    usage = GU.element_users(_inner(fx_install), "normal@XGA")
    status = {u.ref.label: u.status for u in usage.users}
    assert status["mytpl.made@XGA"] == "out of date" and status["FromX"] == "waiting"
    rep = GU.run_update(GU.update_plan(usage))
    libraries_changed()
    assert rep.elements_saved == ["mytpl.made@XGA"] and "FromX" in rep.pages.updated
    assert _holds(_tree(lib, "made@XGA"), b"TPLNEW") == 1
    assert _holds(EditableDocument.open(page).parsed.page.objects, b"TPLNEW") == 1
    assert not U.page_needs_update(page)


def test_a_copy_that_cannot_be_composed_keeps_its_old_stamp(fx_install, monkeypatch):
    """A failed composition keeps the copy as stored and its entry's old stamp, and the save
    reports it: Used By still calls the outer template out of date."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import template_place as TP
    from ctgedit.io import genieusage as GU

    lib = _outer(fx_install)
    old = _entry(lib, "made@XGA")
    _inner_gains(fx_install)

    def fail(*_a, **_k):
        raise RuntimeError("no composition")

    # (the composition of the copy fails)
    monkeypatch.setattr(TP, "compose_prefix", fail)
    rep: dict = {}
    x = ElementDocument.open_element(lib, "made@XGA")
    x.save(refresh=True, report=rep)
    libraries_changed()
    assert TPL in rep["stale_nested"]
    assert _entry(lib, "made@XGA") == old
    assert _rect_records(_tree(lib, "made@XGA")) == []
    usage = GU.element_users(_inner(fx_install), "normal@XGA")
    assert {u.ref.label: u.status for u in usage.users}["mytpl.made@XGA"] == "out of date"


def _with_level(po, pairs=()):
    """``po`` with one more level: its declarations ``pairs`` (name, default), no values."""
    r = GP.decode(po)
    r.entries = [list(e) for e in r.entries] + [[GP.Bag(list(pairs), 0), GP.Bag([], 0), GP.Bag([], 0)]]
    return GP.to_po(GP.encode(r))


def _restore(lib, name, tree):
    from ctgedit.editing import libraries_changed
    from ctgedit.format import element as E

    pe = E.open_element(lib, name)
    E.store_element(lib, name, E.element_content(pe, objects=tree, trailer=pe.trailer), newer=True)
    libraries_changed()


@pytest.mark.parametrize("inner_gains_a_parameter", [False, True])
def test_a_copy_in_the_element_form_is_composed_in_that_form(fx_install, inner_gains_a_parameter):
    """A copy in the element form is composed in that form."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _outer(fx_install)
    tree = _tree(lib, "made@XGA")
    a, b = _copy_span(tree)
    tree[0] = _with_level(tree[0], [("Title", "\n")])
    tree[a:b] = [_with_level(po) for po in tree[a:b]]
    _restore(lib, "made@XGA", tree)
    _inner_gains(fx_install)
    if inner_gains_a_parameter:
        inner = _tree(_inner(fx_install), "normal@XGA")
        inner[0] = _with_level(inner[0], [("Area", "\n")])
        _restore(_inner(fx_install), "normal@XGA", inner)
    rep: dict = {}
    ElementDocument.open_element(lib, "made@XGA").save(refresh=True, report=rep)
    libraries_changed()
    assert TPL not in rep["stale_nested"]
    assert links.entry_stamp(_entry(lib, "made@XGA")) == links.entry_stamp(_current(fx_install))
    after = _tree(lib, "made@XGA")
    a2, b2 = _copy_span(after)
    assert len(after) == len(tree) + 1 and _rect_records(after) == [b2 - 1]
    inner = _tree(_inner(fx_install), "normal@XGA")
    assert [len(GP.decode(po).entries or ()) for po in after[a2:b2]] == [
        len(GP.decode(po).entries or ()) + 1 for po in inner
    ]


def test_a_page_form_copy_whose_inner_template_lost_its_parameter_is_composed(fx_install):
    """A page-form copy whose inner template lost its parameter is composed."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _outer(fx_install)
    tree = _tree(lib, "made@XGA")
    a, b = _copy_span(tree)
    tree[a] = _with_level(tree[a], [("EquipSymLib", "Equipment")])
    _restore(lib, "made@XGA", tree)
    _inner_gains(fx_install)
    rep: dict = {}
    ElementDocument.open_element(lib, "made@XGA").save(refresh=True, report=rep)
    libraries_changed()
    assert TPL not in rep["stale_nested"]
    after = _tree(lib, "made@XGA")
    a2, b2 = _copy_span(after)
    assert len(_rect_records(after[a2:b2])) == 1
    assert links.entry_stamp(_entry(lib, "made@XGA")) == links.entry_stamp(_current(fx_install))
    inner = _tree(_inner(fx_install), "normal@XGA")
    assert [len(GP.decode(po).entries or ()) for po in after[a2 + 1 : b2]] == [
        len(GP.decode(po).entries or ()) for po in inner[1:]
    ]


def test_a_plain_save_keeps_the_levels_and_declarations(fx_install):
    """A plain save keeps the levels and declarations."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import genie_norm as GN

    lib = _outer(fx_install)
    before = _tree(lib, "made@XGA")
    assert not GN.is_genie_element(before)
    ElementDocument.open_element(lib, "made@XGA").save(refresh=True)
    libraries_changed()
    after = _tree(lib, "made@XGA")
    assert len(after) == len(before)
    a, b = _copy_span(after)
    for k, (x, y) in enumerate(zip(before, after)):
        rx, ry = GP.decode(x), GP.decode(y)
        assert [[list(bg.pairs) for bg in e] for e in rx.entries or ()] == [
            [list(bg.pairs) for bg in e] for e in ry.entries or ()
        ], k
        if a < k < b and x.type_code != 0xFFFF:
            assert writer.object_payload(x) == writer.object_payload(y), k
    marks = [k for k in range(a, b) if after[k].type_code == 0xFFFF]
    assert marks and all(GP.decode(after[k]).rect1 == (0, 0, 0, 0) for k in marks)
    assert "\\\\%Description%" not in _labels(lib, "made@XGA")
    assert "\\\\Fx Menu" in _labels(lib, "made@XGA")


def test_a_copy_root_with_a_parameter_level_makes_no_genie_element(fx_install):
    """A copy root with a parameter level makes no genie element."""
    from ctgedit.format import genie_norm as GN

    lib = _outer(fx_install)
    tree = _tree(lib, "made@XGA")
    a, b = _copy_span(tree)
    r = GP.decode(tree[a])
    pair = [("EquipSymLib", "Equipment")]
    r.entries = [list(r.entries[0]), [GP.Bag(list(pair), 0), GP.Bag(list(pair), 11), GP.Bag([], 0)]]
    tree[a] = GP.to_po(GP.encode(r))
    assert not GN.is_genie_element(tree)
    out = GN.normalise_records(tree, ext="ctt", page_like=True)
    assert len(GP.decode(out[0]).entries) == 1
    assert list(GP.decode(out[a]).entries[1][0].pairs) == pair
    for k in range(a, b):
        assert writer.object_payload(out[k]) == writer.object_payload(tree[k]), k


# ------------------------------------------------------------------ New Page, the cache
def test_a_new_page_from_the_outer_template_shows_the_inner_change(fx_install):
    """LIVE Q2: CtDraw's New Page from an outer template not saved again after its inner
    template changed held the inner template's new rectangle (its load composed the copy);
    the outer's entry on the page is its own stored stamp."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    lib = _outer(fx_install)
    _inner_gains(fx_install)
    page = os.path.join(fx_install.project("FxMain"), "FromX2.ctg")
    d = EditableDocument.new_from_template("mytpl.made@XGA.<ctt", page, linked=True)
    objs = d.parsed.page.objects
    ((a, b, _r),) = TP.template_roots(objs)
    assert len(_rect_records(objs[a:b])) == 1
    # an unlinked New Page quotes the version its copy holds: normal@XGA's current one
    data = TP.new_page(
        TP.load_template(lib, "made@XGA"), "mytpl.made@XGA.<ctt", linked=False, resolver=d._resolver()
    )
    from ctgedit.format.page import parse_ctg_bytes

    pb = parse_ctg_bytes(data).page
    t = links.parse_link_table(pb.link_table_raw)
    got = [n for _g, n in t[1] if n.lower().startswith("fx_style.normal@xga.<")]
    assert got and links.entry_stamp(got[0]) == links.entry_stamp(_current(fx_install))


def test_the_cached_outer_template_follows_its_inner_templates_library(fx_install, tmp_path):
    """The loaded outer template is read again when the inner template's library changes on
    disk (another tool's save), not only when the outer's own library does."""
    from ctgedit.editing import libraries_changed
    from ctgedit.format import template_place as TP

    lib = _outer(fx_install)
    keep = str(tmp_path / "fx_style.ctt")
    shutil.copyfile(_inner(fx_install), keep)
    _inner_gains(fx_install)
    changed = str(tmp_path / "changed.ctt")
    shutil.copyfile(_inner(fx_install), changed)
    shutil.copyfile(keep, _inner(fx_install))
    libraries_changed()
    assert _rect_records(TP.load_template(lib, "made@XGA").records) == []
    shutil.copyfile(changed, _inner(fx_install))
    st = os.stat(_inner(fx_install))
    os.utime(_inner(fx_install), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    # (no libraries_changed: another tool wrote the inner template's library)
    assert len(_rect_records(TP.load_template(lib, "made@XGA").records)) == 1


# ------------------------------------------------------------------ Copy to Library
def test_copy_to_library_of_a_page_whose_template_changed_composes_the_copy(fx_install):
    """The page still holds the old normal@XGA (its template update not made): the new
    template made from it holds the new one and quotes its stamp."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP

    _inner_gains(fx_install, "text", (20, 20, 200, 40), text="TPLNEW")
    d = EditableDocument.open(fx_install.page("FxTplLinked"))
    assert d.template_status()[0] == "stale"
    a, b, _ref = TP.template_roots(d.parsed.page.objects)[0]
    lib = os.path.join(fx_install.project("FxMain"), "mytpl.ctt")
    d.copy_to_library(list(range(a, b)), lib, "made@XGA")
    libraries_changed()
    assert _holds(_tree(lib, "made@XGA"), b"TPLNEW") == 1
    assert links.entry_stamp(_entry(lib, "made@XGA")) == links.entry_stamp(_current(fx_install))


def test_a_new_element_keeps_a_carried_template_entry():
    """element.carried_keys: a new element composes no template copy, so a carried .ctt
    entry keeps its stamp (as a symbol's does)."""
    from ctgedit.format import element as E

    keys = E.carried_keys(
        [
            (bytes(16), "fx_style.normal@XGA.<ctt#ed02da80"),
            (bytes(16), "fxsym.fx_box.<ctl#ed02da80"),
            (bytes(16), "fxgenie.fx_value.<ctm#ed02da80"),
        ]
    )
    assert keys == {TPL, ("fxsym", "fx_box")}


# ------------------------------------------------------------------ Compose Again
def test_compose_nested_genies_again_composes_a_changed_template_copy(fx_install):
    """Edit > Library > Compose Nested Genies Again on the outer template: a copy of a
    template that changed is composed; a
    current copy is kept (the composition does not give every stored copy back record for
    record)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _outer(fx_install)
    x = ElementDocument.open_element(lib, "made@XGA")
    assert x.update_nested_genies(every=True) == 0
    _inner_gains(fx_install)
    x = ElementDocument.open_element(lib, "made@XGA")
    assert x.update_nested_genies(every=True) >= 1
    assert len(_rect_records(x.parsed.page.objects)) == 1
    x.save()
    libraries_changed()
    assert len(_rect_records(_tree(lib, "made@XGA"))) == 1


# ------------------------------------------------------------------ labels
def test_a_template_lists_the_substituted_label_of_its_genie_copy(fx_install):
    """LIVE (oracle chain/outer_template, CtDraw's save of normal@XGA): the text of its
    FxTplMenu copy is '@(%Description%)' bound to 'Fx Menu'; the import table lists '\\\\Fx
    Menu' alone, as a page does (the copy keeps the page form)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    ElementDocument.open_element(_inner(fx_install), "normal@XGA").save(refresh=True)
    libraries_changed()
    assert _labels(_inner(fx_install), "normal@XGA") == ["\\\\Fx Menu"]


# ------------------------------------------------------------------------- paste
def test_a_paste_of_older_copies_from_an_element_window_lines_up(fx_install):
    """Two copies of n0 in n1's tab, read before n0 gained two members, pasted on a page: each
    is composed from the current n0 (four records, the new texts in both) with handles of its
    own, and a page update then changes nothing."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxnest.ctm")
    h = ElementDocument.open_element(lib, "n1")

    def copies():
        return [
            i
            for i, o in enumerate(h.parsed.page.objects)
            if i and o.class_flag and o.subtype_size & 0x201 == 0x201
        ]

    h.paste_payload(h.copy_payload(copies()), offset=30)
    assert len(copies()) == 2
    payload = h.copy_payload(copies())
    n0 = ElementDocument.open_element(lib, "n0")
    n0.create_object("text", (10, 60, 60, 76), text="G1")
    n0.create_object("text", (10, 80, 60, 96), text="G2")
    n0.save()
    libraries_changed()
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    others = {geom.get_handle(o) for o in pd.parsed.page.objects}
    new = pd.paste_payload(payload, offset=0)
    po = pd.parsed.page.objects
    roots = [j for j in new if po[j].class_flag and po[j].subtype_size & 0x201 == 0x201]
    assert len(new) == 8 and len(roots) == 2
    assert all(GP.subtree_end(po, j) - j == 4 for j in roots)
    assert _holds([po[j] for j in new], b"G1") == 2 and _holds([po[j] for j in new], b"G2") == 2
    handles = [geom.get_handle(po[j]) for j in new]
    assert len(set(handles)) == len(handles) and not set(handles) & others
    assert pd.genie_changes() == {}
    before = [writer.object_payload(o) for o in po]
    pd.update_genie_instances({("fxnest", "n0")})
    assert [writer.object_payload(o) for o in pd.parsed.page.objects] == before


def test_a_nested_genie_copy_pasted_from_an_element_window_is_composed(fx_install):
    """fxnest.n1 nests n0 bound with '%Name% L1': its copy pasted on a page is composed as a
    placement of n0 with that binding gives it (the text 'L1', 20 wide), not n1's form
    ('%Name% L1', 80 wide); a composition of the page changes nothing more."""
    from ctgedit.editing import EditableDocument, ElementDocument

    h = ElementDocument.open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    objs = h.parsed.page.objects
    k = next(i for i, o in enumerate(objs) if i and o.class_flag and o.subtype_size & 0x201 == 0x201)
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    new = pd.paste_payload(h.copy_payload([k]), offset=0)
    m0 = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n0", "ctm", (10, 40), params={"Label": " L1"})
    fresh = list(range(m0, len(pd.parsed.page.objects)))
    po = pd.parsed.page.objects
    for a, b in zip(new, fresh):
        ra, rb = GP.decode(po[a]), GP.decode(po[b])
        assert (ra.rect1, ra.rect2) == (rb.rect1, rb.rect2)
        assert [[list(bg.pairs) for bg in e] for e in ra.entries or ()] == [
            [list(bg.pairs) for bg in e] for e in rb.entries or ()
        ]
    assert not any(b"%Name%" in writer.object_payload(po[j]) for j in new)
    assert pd.genie_changes() == {}
    before = [writer.object_payload(o) for o in pd.parsed.page.objects]
    pd.update_genie_instances({("fxnest", "n0")})
    assert [writer.object_payload(o) for o in pd.parsed.page.objects] == before
