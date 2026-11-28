"""A template holding another template's copy, with its own parameters."""

import os

import fxinstall
import pytest

from ctgedit.format import genie_place as GP
from ctgedit.format import links, writer

pytestmark = pytest.mark.golden

OUT, INNER = "fxouter@XGA", "normal@XGA"
RECT = (300, 300, 400, 360)
EMPTY = [[], [], []]
NO_DEFAULT = chr(10)  # a declaration's '\n': no default


def _cap(step, unit, *parts) -> bytes:
    return fxinstall.oracle_bytes(step, unit, "/".join(parts))


def _put(fx, project, name, data) -> str:
    from ctgedit.editing import libraries_changed

    path = os.path.join(fx.project(project), name)
    with open(path, "wb") as fh:
        fh.write(data)
    libraries_changed()
    return path


def _style(fx, data) -> str:
    return _put(fx, "FxStyle", "fx_style.ctt", data)


def _tree(lib, el, data=None):
    from ctgedit.format.element import LibraryFile, open_element

    if data is not None:
        return list(open_element("x.ctt", el, library=LibraryFile(data)).tree)
    return list(open_element(lib, el).tree)


def _table(lib, el, data=None):
    from ctgedit.format.element import LibraryFile, open_element

    pe = open_element("x.ctt", el, library=LibraryFile(data)) if data is not None else open_element(lib, el)
    pb = pe.page
    return [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []


def _payloads(recs):
    return [writer.object_payload(po) for po in recs]


def _levels(po):
    return [[list(b.pairs) for b in lv] for lv in (GP.decode(po).entries or [])]


def _key(po):
    r = GP.decode(po)
    return (
        r.cf,
        r.tc,
        r.dw,
        r.handle,
        r.an,
        r.name,
        r.link,
        r.slots,
        r.anchor,
        r.rect1,
        r.rect2,
        _levels(po),
    )


def _page(data):
    from ctgedit.format.page import parse_ctg_bytes

    return parse_ctg_bytes(data).page


def _page_table(pb):
    return [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []


def _save(lib, el, refresh=False) -> dict:
    from ctgedit.editing import ElementDocument, libraries_changed

    rep: dict = {}
    ElementDocument.open_element(lib, el).save(refresh=refresh, report=rep)
    libraries_changed()
    return rep


def _with_record(po, **kw):
    r = GP.decode(po)
    for k, v in kw.items():
        setattr(r, k, v)
    return GP.to_po(GP.encode(r))


def _store(lib, el, tree):
    from ctgedit.editing import libraries_changed
    from ctgedit.format import element as E

    pe = E.open_element(lib, el)
    E.store_element(lib, el, E.element_content(pe, objects=tree, trailer=pe.trailer), newer=True)
    libraries_changed()


# ------------------------------------------------------------ the element-form composition
@pytest.mark.parametrize("refresh", [False, True])
def test_an_element_form_copy_is_composed_as_ctdraw_does(fx_install, refresh):
    """An element-form copy is composed as CtDraw does."""
    lib = _style(fx_install, _cap("chain4", "outer_param", "inner_changed", "library.ctt"))
    rep = _save(lib, OUT, refresh=refresh)
    want = _cap("chain4", "outer_param", "same", OUT, "library.ctt")
    assert _payloads(_tree(lib, OUT)) == _payloads(_tree(None, OUT, want))
    assert _table(lib, OUT) == _table(None, OUT, want)
    assert not rep["stale_nested"]


def test_a_member_binding_the_inner_template_lacks_goes_from_an_element_form_copy(fx_install):
    """A touch command put on a member of the element-form copy (CtDraw's record of the
    oracle's chain5/extra_binding, with the outer's level): the composition after normal@XGA
    changed drops it and gives the member normal@XGA's form again, as CtDraw does in the
    page form (LIVE): the result is CtDraw's composition of the copy without the command."""
    lib = _style(fx_install, _cap("chain4", "outer_param", "inner_changed", "library.ctt"))
    bound = _tree(None, OUT, _cap("chain5", "extra_binding", "bound", "library.ctt"))
    rect = next(k for k, po in enumerate(bound) if po.type_code == 0x0F and po.class_flag)
    tree = _tree(lib, OUT)
    k = next(i for i, po in enumerate(tree) if po.an_str == bound[rect].an_str and po.type_code == 0x0F)
    touch = GP.decode(bound[rect + 1])
    touch.entries = touch.entries + [[GP.Bag([], 0), GP.Bag([], 0), GP.Bag([], 0)]]
    member = GP.decode(tree[k])
    tree[k] = _with_record(tree[k], cf=1, dw=member.dw | 0x10)
    tree.insert(k + 1, GP.to_po(GP.encode(touch)))
    _store(lib, OUT, tree)
    assert any(po.type_code == 0x1010 for po in _tree(lib, OUT))
    _save(lib, OUT)
    want = _cap("chain4", "outer_param", "same", OUT, "library.ctt")
    assert [_key(po) for po in _tree(lib, OUT)] == [_key(po) for po in _tree(None, OUT, want)]


def test_a_member_binding_the_inner_template_lacks_goes_in_the_page_form(fx_install):
    """chain5/extra_binding itself (the page form): the outer's save after normal@XGA changed
    drops the touch command, as CtDraw's load + save did (the oracle's Q13)."""
    lib = _style(fx_install, _cap("chain5", "extra_binding", "inner_changed", "library.ctt"))
    _save(lib, OUT)
    want = _cap("chain5", "extra_binding", "fresh", OUT, "library.ctt")
    assert [_key(po) for po in _tree(lib, OUT)] == [_key(po) for po in _tree(None, OUT, want)]
    assert _table(lib, OUT)[0] == _table(None, OUT, want)[0]


def test_the_composed_copy_keeps_its_template_bags_and_orders_its_values(fx_install):
    """The composed copy keeps its template bags and orders its values."""
    lib = _style(fx_install, _cap("chain4", "outer_param", "inner_changed", "library.ctt"))
    vals = [("!*PropSheet.Focus", "#1403.#1404"), ("COMMENT", "Frame"), ("!*#1421", "#1404")]
    inner = _tree(lib, INNER)
    k_in = next(k for k, po in enumerate(inner) if k and po.type_code == 0x0F)
    r = GP.decode(inner[k_in])
    r.entries[0][1] = GP.Bag(list(vals), r.entries[0][1].ov)
    inner[k_in] = GP.to_po(GP.encode(r))
    _store(lib, INNER, inner)  # (the stamp moves: the copy is out of date)
    tree = _tree(lib, OUT)
    k = next(i for i, po in enumerate(tree) if po.an_str == str(r.handle) and po.type_code == 0x0F)
    rc = GP.decode(tree[k])
    stored_b2 = [("COMMENT", "Frame"), ("!*PropSheet.Focus", "#1403.#1404"), ("!*#1421", "#1404")]
    rc.entries[0][1] = GP.Bag(list(vals), rc.entries[0][1].ov)
    rc.entries[0][2] = GP.Bag(list(stored_b2), rc.entries[0][2].ov)
    tree[k] = GP.to_po(GP.encode(rc))
    _store(lib, OUT, tree)
    _save(lib, OUT)
    got = GP.decode(next(po for po in _tree(lib, OUT) if po.an_str == str(r.handle) and po.type_code == 0x0F))
    assert list(got.entries[0][2].pairs) == stored_b2
    assert list(got.entries[0][1].pairs) == stored_b2
    assert _levels(GP.to_po(GP.encode(got)))[1] == EMPTY


def test_an_element_whose_root_does_not_decode_keeps_its_template_copies(fx_install):
    """Neither form is known when the element's root does not decode: its template copy is
    kept as stored and named as not composed (its entry keeps the old stamp)."""
    import copy
    from ctgedit.format import template_place as TP
    from ctgedit.format.symbols import SymbolResolver

    lib = _style(fx_install, _cap("chain4", "outer_param", "inner_changed", "library.ctt"))
    tree = _tree(lib, OUT)
    bad = copy.copy(tree[0])
    bad.body_raw = bad.body_raw[:20]
    with pytest.raises(Exception):
        GP.decode(bad)
    recs = [bad] + tree[1:]
    assert TP.nested_template_spans(recs)
    rep: dict = {}
    out = TP.compose_nested_templates(recs, SymbolResolver.for_page(lib), report=rep, markers=False)
    assert _payloads(out) == _payloads(recs)
    assert rep["stale"] == {("fx_style", INNER.lower())} and not rep["composed"]


def test_compose_all_converges_on_an_element_form_copy(fx_install):
    """Update Pages' chain with 'compose their nested genies again' after normal@XGA changed:
    the first run saves fxouter@XGA with its element-form copy composed and its entry
    current; the next plan does not name it again."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU

    lib = _style(fx_install, _cap("chain4", "outer_param", "plain", OUT, "library.ctt"))
    d = ElementDocument.open_element(lib, INNER)
    d.create_object("rectangle", RECT)
    d.save()
    libraries_changed()
    dirs = [fx_install.project("FxStyle")]
    plan = GU.chain_plan(dirs, compose_all=True)
    assert "fx_style.%s" % OUT in [u.ref.label for u in plan.elements]
    rep = GU.run_update(plan)
    libraries_changed()
    assert "fx_style.%s" % OUT in rep.elements_saved
    assert not any(OUT.lower() in str(x).lower() for x in rep.not_composed)
    again = GU.chain_plan(dirs, compose_all=True)
    assert "fx_style.%s" % OUT not in [u.ref.label for u in again.elements]
    cur = GP.current_link_entry(lib, "fx_style", INNER)[1]
    assert _table(lib, OUT)[0].lower() == cur.lower()
    tree = _tree(lib, OUT)
    rects = [po for po in tree if po.type_code == 0x0F and GP.decode(po).rect1 == RECT]
    assert len(rects) == 1 and _levels(rects[0]) == [EMPTY, EMPTY]


# ------------------------------------------------------------ the first own parameter
def test_the_first_own_parameter_puts_every_record_in_the_element_form(fx_install):
    """The first own parameter puts every record in the element form."""
    from ctgedit.editing import ElementDocument, libraries_changed

    made = _cap("chain4", "outer_param", "made", "library.ctt")
    lib = _style(fx_install, made)
    d = ElementDocument.open_element(lib, OUT)
    d.create_object("text", rect=(500, 500, 533, 516), text="%P%")
    d.save()
    libraries_changed()
    want = _tree(None, OUT, _cap("chain4", "outer_param", "param_added", "library.ctt"))
    got = _tree(lib, OUT)
    assert [_key(po) for po in got] == [_key(po) for po in want]
    assert _payloads(got)[:-1] == _payloads(want)[:-1]
    assert _table(lib, OUT) == _table(None, OUT, made)
    # (a load + save of it changes nothing)
    before = _payloads(got)
    _save(lib, OUT, refresh=True)
    assert _payloads(_tree(lib, OUT)) == before


def test_a_new_template_that_names_a_parameter_is_saved_in_the_genie_form(fx_install):
    """A NEW template element (Copy to Library, the first save) whose own text names a
    %parameter%: every record takes the genie form, the text declares it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import element as E
    from ctgedit.format.container import Block
    from ctgedit.format.page import parse_page

    doc = EditableDocument.open(fx_install.page("FxShapes"))
    n = len(doc.parsed.page.objects)
    doc.create_object("rectangle", (10, 10, 60, 40))
    doc.create_object("text", (100, 100, 140, 116), text="%P%")
    recs = list(doc.parsed.page.objects[n:])
    content = E.new_element_content(recs, ext="ctt", page_size=(1024, 768))
    pb = parse_page(Block(1, "new", 0, len(content) + E.PAD, content + bytes(E.PAD)), 0x0C)
    tree = pb.objects[: E.root_tree_end(pb.objects, 0)]
    assert [len(GP.decode(po).entries or ()) for po in tree] == [2, 2, 2]
    text = next(po for po in tree if po.type_code == 0x05)
    assert _levels(text) == [[[], [], [("TXT_OFF", "%P%")]], [[("P", NO_DEFAULT)], [], []]]


# ------------------------------------------------------------ New Page
@pytest.mark.parametrize("stage", ["same", "fresh"])
def test_a_new_page_from_it_keeps_the_unbound_token_as_typed(fx_install, stage):
    """A new page keeps the unbound token as typed."""
    from ctgedit.editing import EditableDocument

    src = ("same", OUT, "library.ctt") if stage == "same" else ("inner_changed", "library.ctt")
    _style(fx_install, _cap("chain4", "outer_param", *src))
    path = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    doc = EditableDocument.new_from_template("fx_style.%s.<ctt" % OUT, path, linked=True)
    want = _page(_cap("chain4", "outer_param", stage, "new_page", "page.ctg"))
    assert [_key(po) for po in doc.parsed.page.objects] == [_key(po) for po in want.objects]
    assert [n.split("#")[0] for n in _page_table(doc.parsed.page)] == [
        n.split("#")[0] for n in _page_table(want)
    ]


def test_a_new_page_keeps_only_a_texts_caption_as_typed(fx_install):
    """Only a text's caption that substitutes to nothing stays as typed on a New Page: its
    other values, and other objects' (the shipped doubletrend@XGA's trend pens, LIVE),
    substitute as before. fxouter@XGA's '%P%' text given the comment '%P% note': the New Page
    holds TXT_OFF '%P%' and COMMENT 'note'."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = _style(fx_install, _cap("chain4", "outer_param", "param_added", "library.ctt"))
    d = ElementDocument.open_element(lib, OUT)
    k = next(
        i
        for i, po in enumerate(d.parsed.page.objects)
        if po.type_code == 0x05 and GP.decode(po).entries[0][2].get("TXT_OFF") == "%P%"
    )
    assert d.set_member_property(k, "COMMENT", "%P% note")
    d.save()
    libraries_changed()
    path = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    doc = EditableDocument.new_from_template("fx_style.%s.<ctt" % OUT, path, linked=True)
    text = next(
        po
        for po in doc.parsed.page.objects
        if po.type_code == 0x05 and GP.decode(po).entries[0][2].get("TXT_OFF") == "%P%"
    )
    vals = dict(GP.decode(text).entries[0][1].pairs)
    assert vals.get("TXT_OFF") == "%P%" and vals.get("COMMENT") == "note"
    assert GP.decode(text).rect1 == (500, 500, 533, 516)  # (measured as written)


# ------------------------------------------------------------ a page linked to the outer
def _outer_page(fx, step="chain4", unit="cut_link_outer", lib_stage="inner_changed", page_stage="page_made"):
    lib = _style(fx, _cap(step, unit, lib_stage, "library.ctt"))
    page = _put(fx, "FxMain", "FxOracleOuterLinked.ctg", _cap(step, unit, page_stage, "page.ctg"))
    return lib, page


def test_a_page_linked_to_an_outer_template_shows_the_inner_change_when_opened(fx_install):
    """The outer not saved since normal@XGA gained its rectangle: the page's template reads
    out of date, and its open + save writes CtDraw's plain load + save byte for byte (the
    rectangle in the page's copy of normal@XGA, the outer's entry as it was)."""
    from ctgedit.editing import EditableDocument

    _lib, page = _outer_page(fx_install)
    doc = EditableDocument.open(page)
    assert doc.template_status()[0] == "stale"
    doc.apply_open_updates()
    doc.save()
    want = _page(_cap("chain4", "cut_link_outer", "fresh", "plain", "page.ctg"))
    got = _page(open(page, "rb").read())
    assert _payloads(got.objects) == _payloads(want.objects)
    assert _page_table(got) == _page_table(want)


def test_update_pages_updates_it_once_and_then_reads_it_current(fx_install):
    """Update Pages composes the page through the outer as CtDraw's load does (byte for
    byte); the page then shows the composition and reads current, so the next run leaves it."""
    from ctgedit.io import updatepages as UP

    _lib, page = _outer_page(fx_install)
    assert UP.page_needs_update(page)
    rep = UP.update_pages(pages=[page])
    assert [r["outcome"] for r in rep.records] == ["updated"]
    want = _page(_cap("chain4", "cut_link_outer", "fresh", "plain", "page.ctg"))
    assert _payloads(_page(open(page, "rb").read()).objects) == _payloads(want.objects)
    assert not UP.page_needs_update(page)
    rep = UP.update_pages(pages=[page])
    assert [r["outcome"] for r in rep.records] == ["current"]


def test_a_page_that_shows_the_inner_change_is_current(fx_install):
    """A page that shows the inner change is current."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as UP

    _lib, page = _outer_page(fx_install, page_stage="fresh/plain")
    doc = EditableDocument.open(page)
    assert doc.template_status()[0] == "current"
    before = _payloads(doc.parsed.page.objects)
    assert not [x for x in doc.apply_open_updates() if "template" in x]
    assert _payloads(doc.parsed.page.objects) == before
    assert doc.update_template() == 0
    assert not UP.page_needs_update(page)
    rep = UP.update_pages(pages=[page])
    assert [r["outcome"] for r in rep.records] == ["current"]


def test_used_by_and_update_pages_agree_on_a_page_linked_to_an_outer_template(fx_install):
    """Used By and Update Pages agree on a page linked to an outer template."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import template_place as TP
    from ctgedit.io import genieusage as GU, updatepages as UP

    d = EditableDocument.open(fx_install.page("FxTplLinked"))
    a, b, _ref = TP.template_roots(d.parsed.page.objects)[0]
    lib = os.path.join(fx_install.project("FxMain"), "mytpl.ctt")
    assert d.copy_to_library(list(range(a, b)), lib, "made@XGA")
    libraries_changed()
    page = os.path.join(fx_install.project("FxMain"), "FromMade.ctg")
    EditableDocument.new_from_template("mytpl.made@XGA.<ctt", page, linked=True)
    libraries_changed()
    inner = fx_install.file("FxStyle", "fx_style.ctt")
    n = ElementDocument.open_element(inner, INNER)
    n.create_object("rectangle", RECT)
    n.save()
    libraries_changed()
    status = {u.ref.label: u.status for u in GU.element_users(inner, INNER).users}
    assert status["mytpl.made@XGA"] == "out of date" and status["FromMade"] == "waiting"
    assert UP.page_needs_update(page)
    assert EditableDocument.open(page).template_status()[0] == "stale"
    rep = UP.update_pages(pages=[page])
    assert [r["outcome"] for r in rep.records] == ["updated"]
    assert not UP.page_needs_update(page)
    assert EditableDocument.open(page).template_status()[0] == "current"
    status = {u.ref.label: u.status for u in GU.element_users(inner, INNER).users}
    assert status["FromMade"] == "waiting"


def test_update_pages_reaches_the_page_linked_to_the_outer_template(fx_install):
    """Update Pages reaches the page linked to the outer template."""
    from ctgedit.editing import libraries_changed
    from ctgedit.io import genieusage as GU, updatepages as UP

    lib, page = _outer_page(fx_install, "chain5", "template_warning", "menu_save")
    w = _page(_cap("chain5", "template_warning", "update_pages", "FxOracleOuterLinked", "page.ctg"))
    rep = UP.update_pages(pages=[page])
    assert [r["outcome"] for r in rep.records] == ["updated"]
    assert _payloads(_page(open(page, "rb").read()).objects) == _payloads(w.objects)
    plan = GU.chain_plan([fx_install.project("FxMain"), fx_install.project("FxStyle")])
    assert [u.ref.label for u in plan.elements] == ["fx_style.%s" % OUT]
    GU.run_update(plan)
    libraries_changed()
    want = _cap("chain5", "template_warning", "update_pages", "library.ctt")
    assert [_key(po) for po in _tree(lib, OUT)] == [_key(po) for po in _tree(None, OUT, want)]
    UP.update_pages(pages=[page])
    got = _page(open(page, "rb").read())
    assert _payloads(got.objects) == _payloads(w.objects)
    stamp = links.entry_stamp(GP.current_link_entry(lib, "fx_style", OUT)[1])
    assert links.entry_stamp(_page_table(got)[0]) == stamp
    assert not UP.page_needs_update(page)


@pytest.mark.parametrize("stage", ["same/cut", "fresh/cut"])
def test_cut_link_leaves_the_inner_copy_as_the_pages_template(fx_install, stage):
    """Cut Link leaves the inner copy as the page's template."""
    from ctgedit.editing import EditableDocument

    _lib, page = _outer_page(fx_install)
    doc = EditableDocument.open(page)
    doc.cut_template_link()
    doc.save()
    want = _page(_cap("chain4", "cut_link_outer", *stage.split("/"), "page.ctg"))
    got = _page(open(page, "rb").read())
    assert _payloads(got.objects) == _payloads(want.objects)
    assert _page_table(got) == _page_table(want)


def test_cut_link_keeps_an_outer_root_with_objects_of_its_own(fx_install):
    """Cut Link keeps an outer root with objects of its own."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    _style(fx_install, _cap("chain4", "outer_param", "param_added", "library.ctt"))
    path = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    EditableDocument.new_from_template("fx_style.%s.<ctt" % OUT, path, linked=True)
    doc = EditableDocument.open(path)
    n = len(doc.parsed.page.objects)
    doc.cut_template_link()
    objs = doc.parsed.page.objects
    r0 = GP.decode(objs[0])
    assert len(objs) == n and r0.tc == 0x08 and r0.cf == 2 and not r0.dw & 0x1
    assert r0.an == "-1" and not r0.link
    assert TP.is_template_copy_root(objs[1])
    assert GP.decode(objs[1]).link.lower() == ("fx_style.%s.<ctt" % INNER).lower()
    text = objs[TP.subtree_end(objs, 1)]
    assert text.type_code == 0x05 and GP.decode(text).entries[0][1].get("TXT_OFF") == "%P%"


# ------------------------------------------------------------ CtDraw's other answers
def test_an_element_form_copys_free_marker_is_derived_again(fx_install):
    """chain5/marker_box: the element-form copy's free DummyMarker given normal@XGA's stored
    box; a refresh save derives it again to the empty rect at its origin and changes nothing
    else, as CtDraw's plain load + save."""
    lib = _style(fx_install, _cap("chain5", "marker_box", "patched", "library.ctt"))
    _save(lib, OUT, refresh=True)
    want = _cap("chain5", "marker_box", "plain", OUT, "library.ctt")
    assert [_key(po) for po in _tree(lib, OUT)] == [_key(po) for po in _tree(None, OUT, want)]
