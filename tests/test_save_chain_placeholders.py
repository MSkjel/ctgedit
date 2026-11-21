"""Placeholders and self-links in the save chain."""

import os
import shutil
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fxinstall  # noqa: E402

INNER = "normal@XGA"


# ------------------------------------------------------------------------------ helpers
def _oracle(step, unit, *parts):
    return fxinstall.oracle(step, unit, "/".join(parts))


def _put(src, dst):
    from ctgedit.editing import libraries_changed

    shutil.copyfile(src, dst)
    libraries_changed()


def _held_template(fx):
    """fx_style.ctt as CtDraw saved normal@XGA while fxgenie.ctm did not open (its FxTplMenu
    one placeholder, the table the bare entry: chain6/template_placeholder)."""
    tlib = fx.file("FxStyle", "fx_style.ctt")
    _put(_oracle("chain6", "template_placeholder", "template_missing_fresh", "library.ctt"), tlib)
    return tlib


def _read(path):
    from ctgedit.format.page import parse_ctg_bytes

    with open(path, "rb") as fh:
        return parse_ctg_bytes(fh.read()).page


def _captured(step, unit, *stage):
    from ctgedit.format.page import parse_ctg_bytes

    return parse_ctg_bytes(fxinstall.oracle_bytes(step, unit, "/".join(stage + ("page.ctg",)))).page


def _table(pb):
    from ctgedit.format import links

    if not pb.link_count:
        return []
    return [(g == bytes(16), n) for g, n in links.parse_link_table(pb.link_table_raw)[1]]


def _names(pb):
    return [n for _z, n in _table(pb)]


def _key(po, an=True):
    from ctgedit.format import genie_place as GP

    r = GP.decode(po)
    return (
        r.cf,
        r.tc,
        r.dw,
        r.handle,
        r.an if an else None,
        r.name,
        r.link,
        r.slots,
        r.anchor,
        r.rect1,
        r.rect2,
        [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])],
    )


def _keys(objs, an=True):
    return [_key(po, an) for po in objs]


def _element(path, name):
    from ctgedit.format.element import open_element

    return open_element(path, name)


def _markers(objs):
    from ctgedit.format import genie_place as GP

    return [k for k, po in enumerate(objs) if GP.placeholder_genie(po) is not None]


def _named(objs, name):
    from ctgedit.format import genie_place as GP

    hits = [GP.decode(po) for po in objs if GP.decode(po).name == name]
    assert len(hits) == 1, name
    return hits[0]


# ----------------------------------------------- a placeholder of a genie inside that genie
def test_a_placeholder_of_a_genie_cannot_be_pasted_into_that_genie(fx_install):
    """FxReturned's placeholder of fxback.fx_value pasted into fx_value's own window: refused
    as a linked copy is."""
    from ctgedit.editing import EditableDocument, ElementDocument

    src = EditableDocument.open(fx_install.page("FxReturned"))
    i = [i for i, lib, el, _x in src.placeholder_genies() if el == "fx_value"][0]
    payload = src.copy_payload([i])
    back = fx_install.file("FxMain", "fxback.ctm")
    with open(back, "rb") as fh:
        before = fh.read()
    g = ElementDocument.open_element(back, "fx_value")
    n0 = len(g.parsed.page.objects)
    with pytest.raises(ValueError, match="cannot be placed in itself"):
        g.paste_payload(payload, offset=0)
    assert len(g.parsed.page.objects) == n0
    with pytest.raises(ValueError, match="holds a copy of fxback.fx_value"):
        src.copy_to_library([i], back, "fx_value")
    with open(back, "rb") as fh:
        assert fh.read() == before


@pytest.mark.parametrize(
    "name, chain", [("n1", "fxnest.n1 > fxnest.n1"), ("n0", "fxnest.n0 > fxnest.n1 > fxnest.n0")]
)
def test_a_genie_holding_a_placeholder_of_itself_is_not_placed_and_not_saved(fx_install, name, chain):
    """CtDraw's input of chain7/self_placeholder (fx_row's placeholder of fxnest.n1 written
    into fxnest as n1, and as n0, which n1 nests): CtDraw's open faults. ctgedit opens it with
    the marker kept (not placed by the window, not by a page composition, whose cycles name
    the chain) and refuses the save."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP

    lib = fx_install.file("FxLib", "fxnest.ctm")
    _put(_oracle("chain7", "self_placeholder", "%s_holds" % name, "input", "library.ctm"), lib)
    with open(lib, "rb") as fh:
        before = fh.read()
    doc = ElementDocument.open_element(lib, name)
    assert doc.placeholder_genies() == []
    assert len(_markers(doc.parsed.page.objects)) == 1
    # (the tab's automatic update changes nothing for it)
    assert doc.nested_genie_changes() == frozenset()
    assert doc.nested_update_ops() == ([], None)
    for refresh in (False, True):
        with pytest.raises(ValueError, match="holds a copy of itself"):
            doc.save(refresh=refresh)
    with open(lib, "rb") as fh:
        assert fh.read() == before
    el = GP.load_element(lib, name, compose=True)
    assert len(_markers(el.records)) == 1
    assert chain in {" > ".join(c) for c in getattr(el, "cycles", ())}


# ----------------------------------------------------------- a template holding itself
def test_copy_to_library_of_a_template_copy_into_that_template_is_refused(fx_install):
    """FxTplLinked's copy of normal@XGA stored as normal@XGA: CtDraw refuses that save
    (chain7/self_template, 'Attempt to save page with a link to itself'); so does ctgedit,
    and the library stays as it was."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    tlib = fx_install.file("FxStyle", "fx_style.ctt")
    with open(tlib, "rb") as fh:
        before = fh.read()
    d = EditableDocument.open(fx_install.page("FxTplLinked"))
    a, b, _r = TP.template_roots(d.parsed.page.objects)[0]
    with pytest.raises(
        ValueError,
        match=r"^The selection holds a copy of fx_style.normal@XGA, "
        r"so it cannot be stored as fx_style.normal@XGA\.$",
    ):
        d.copy_to_library(list(range(a, b)), tlib, INNER)
    with open(tlib, "rb") as fh:
        assert fh.read() == before


def test_a_template_copy_holding_the_template_one_copy_down_is_refused(fx_install):
    """chain7/self_template's outer_self: a template made from fxouter@XGA (which holds
    normal@XGA's copy) saved as normal@XGA. A page linked to fxouter: Copy to Library of its
    template copy as normal@XGA names the chain."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    tlib = fx_install.file("FxStyle", "fx_style.ctt")
    _put(_oracle("chain7", "self_template", "made", "library.ctt"), tlib)
    pp = os.path.join(fx_install.project("FxMain"), "FromOuter.ctg")
    d = EditableDocument.new_from_template("fx_style.fxouter@XGA.<ctt", pp, linked=True)
    a, b, _r = TP.template_roots(d.parsed.page.objects)[0]
    with pytest.raises(ValueError, match="fx_style.fxouter@XGA > fx_style.normal@XGA"):
        d.copy_to_library(list(range(a, b)), tlib, INNER)


def test_a_template_copy_cannot_be_pasted_into_that_template(fx_install):
    """FxTplLinked's copy of normal@XGA pasted into normal@XGA's window: refused, as a genie
    copy pasted into its genie is."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import template_place as TP

    src = EditableDocument.open(fx_install.page("FxTplLinked"))
    a, b, _r = TP.template_roots(src.parsed.page.objects)[0]
    payload = src.copy_payload(list(range(a, b)))
    t = ElementDocument.open_element(fx_install.file("FxStyle", "fx_style.ctt"), INNER)
    n0 = len(t.parsed.page.objects)
    with pytest.raises(
        ValueError, match="A template cannot be placed in itself: fx_style.normal@XGA is the template you are"
    ):
        t.paste_payload(payload, offset=0)
    assert len(t.parsed.page.objects) == n0


def test_an_outer_template_cannot_be_saved_as_its_inner_template(fx_install):
    """chain7/self_template's outer_self in the template window: fxouter@XGA (it holds
    normal@XGA's copy) saved as normal@XGA. CtDraw refused it ('Attempt to save page with a
    link to itself'); so does ctgedit's Save As, naming the chain, and the library stays."""
    from ctgedit.editing import ElementDocument

    tlib = fx_install.file("FxStyle", "fx_style.ctt")
    _put(_oracle("chain7", "self_template", "made", "library.ctt"), tlib)
    with open(tlib, "rb") as fh:
        before = fh.read()
    doc = ElementDocument.open_element(tlib, "fxouter@XGA")
    with pytest.raises(
        ValueError,
        match="fx_style.normal@XGA holds a copy of itself "
        r"\(fx_style.normal@XGA > fx_style.normal@XGA\)",
    ):
        doc.save(name=INNER)
    with open(tlib, "rb") as fh:
        assert fh.read() == before


def _self_holding_template(fx):
    """normal@XGA replaced by its own copy (FxTplLinked's template copy): the template holds a
    copy of itself."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import element as E
    from ctgedit.format import genie_place as GP
    from ctgedit.format import template_place as TP
    from ctgedit.format import writer

    tlib = fx.file("FxStyle", "fx_style.ctt")
    d = EditableDocument.open(fx.page("FxTplLinked"))
    objs = d.parsed.page.objects
    a, b, _r = TP.template_roots(objs)[0]
    copies = [GP.to_po(writer.object_payload(po)) for po in objs[a:b]]
    content = E.new_element_content(
        copies,
        origin=(0, 0),
        page_size=(1024, 768),
        trailer=E.default_trailer(),
        resolver=d._resolver(),
        ext="ctt",
        carried=d._link_entries(),
    )
    E.store_element(tlib, INNER, content, newer=True)
    libraries_changed()
    return tlib


def test_a_template_stored_holding_itself_is_not_saved_and_does_not_grow(fx_install):
    """A template holding a copy of itself: its save is refused, it is not out of date for
    itself, and its load, a New Page from it and its window's update keep the stored records
    (a save must not nest the stored version one level deeper each time)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import template_place as TP

    tlib = _self_holding_template(fx_install)
    stored = len(_element(tlib, INNER).tree)
    doc = ElementDocument.open_element(tlib, INNER)
    assert ("fx_style", "normal@xga") not in doc.nested_genie_changes()
    assert doc.nested_update_ops() == ([], None)
    with pytest.raises(ValueError, match="holds a copy of itself"):
        doc.save(refresh=True)
    libraries_changed()
    assert len(_element(tlib, INNER).tree) == stored
    assert len(TP.load_template(tlib, INNER).records) == stored
    pp = os.path.join(fx_install.project("FxMain"), "FromSelf.ctg")
    p = EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", pp, linked=True)
    assert len(p.parsed.page.objects) == stored


# ------------------------------------------------------------- a template holding a placeholder
def test_a_linked_page_has_the_genie_placed_at_once(fx_install):
    """The library back, the template still holding the placeholder: FxTplLinked's open +
    save and Update Pages give CtDraw's load + save record for record (chain6 page_back: the
    genie placed in the template copy, the handles the template's own save later gives it,
    '\\Fx Menu' kept)."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import updatepages as U

    _held_template(fx_install)
    want = _captured("chain6", "template_placeholder", "page_back")
    path = fx_install.page("FxTplLinked")
    with open(path, "rb") as fh:
        page0 = fh.read()
    doc = EditableDocument.open(path)
    assert doc.template_status()[0] == "stale"
    doc.apply_open_updates()
    doc.save()
    got = _read(path)
    assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want)
    with open(path, "wb") as fh:
        fh.write(page0)
    libraries_changed()
    (rec,) = U.update_pages(pages=[path]).records
    assert rec["outcome"] == "updated"
    got = _read(path)
    assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want)
    assert not U.page_needs_update(path)


@pytest.mark.parametrize("linked", [True, False])
def test_a_new_page_from_the_template_has_the_genie_placed(fx_install, linked):
    """A new page from the template has the genie placed."""
    from ctgedit.editing import EditableDocument

    _held_template(fx_install)
    stage = "linked" if linked else "unlinked"
    want = _captured("chain7", "holder_pages", stage)
    pp = os.path.join(fx_install.project("FxMain"), "FromHolder.ctg")
    EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", pp, linked=linked)
    got = _read(pp)
    assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want)
    doc = EditableDocument.open(pp)
    doc.apply_open_updates()
    doc.save()
    got = _read(pp)
    if linked:
        loaded = _captured("chain7", "holder_pages", "cut_linked").objects[10]
        assert _key(got.objects[10])[8:11] == _key(loaded)[8:11] == ((0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
        assert _keys(got.objects[:10]) == _keys(want.objects[:10])
    else:
        assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want)
    assert EditableDocument.open(pp).apply_open_updates() == []


@pytest.mark.parametrize("stage", ["cut_fixture", "cut_linked"])
def test_cut_link_from_the_template_places_the_genie(fx_install, stage):
    """Cut Link from the template places the genie."""
    from ctgedit.editing import EditableDocument

    _held_template(fx_install)
    want = _captured("chain7", "holder_pages", stage)
    if stage == "cut_fixture":
        path = fx_install.page("FxTplLinked")
    else:
        path = os.path.join(fx_install.project("FxMain"), "FromHolder.ctg")
        EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", path, linked=True)
    doc = EditableDocument.open(path)
    doc.cut_template_link()
    doc.save()
    got = _read(path)
    assert _keys(got.objects, an=False) == _keys(want.objects, an=False)
    assert _table(got) == _table(want)


@pytest.mark.parametrize("refresh", [False, True])
def test_the_templates_own_save_keeps_the_name_and_flags(fx_install, refresh):
    """The template's own save with the library back (chain6 template_back): FxTplMenu placed
    again with its record name and __AN_NAME__ bags, dwFlags 0x219 and its rectangle 0x18,
    record for record."""
    from ctgedit.editing import ElementDocument

    tlib = _held_template(fx_install)
    want = _element(_oracle("chain6", "template_placeholder", "template_back", INNER, "library.ctt"), INNER)
    ElementDocument.open_element(tlib, INNER).save(refresh=refresh)
    got = _element(tlib, INNER)
    assert _keys(got.tree) == _keys(want.tree)
    assert _table(got.page) == _table(want.page)


def test_with_the_library_missing_the_pages_list_the_bare_entry(fx_install):
    """With the library missing, the pages list the bare entry."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import updatepages as U

    _held_template(fx_install)
    genie = fx_install.file("FxLib", "fxgenie.ctm")
    keep = genie + ".away"
    os.replace(genie, keep)
    libraries_changed()
    pu = os.path.join(fx_install.project("FxMain"), "HolderUnlinked.ctg")
    EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", pu, linked=False)
    pc = os.path.join(fx_install.project("FxMain"), "HolderCut.ctg")
    EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", pc, linked=True)
    d = EditableDocument.open(pc)
    d.cut_template_link()
    d.save()
    for p in (pu, pc):
        pb = _read(p)
        assert len(_markers(pb.objects)) == 1
        assert (True, "fxgenie.fx_value.<ctm") in _table(pb)
    os.replace(keep, genie)
    libraries_changed()
    for p in (pu, pc):
        assert U.page_needs_update(p)
    from ctgedit.io import genieusage as GU

    users = {x.ref.label: x.status for x in GU.element_users(genie, "fx_value").users}
    assert users.get("HolderUnlinked") == users.get("HolderCut") == "out of date"
    for rec in U.update_pages(pages=[pu, pc]).records:
        assert rec["outcome"] == "updated" and rec["placeholders"] == 1
    for p in (pu, pc):
        pb = _read(p)
        assert _markers(pb.objects) == []
        menu = _named(pb.objects, "FxTplMenu")
        assert menu.dw == 0x219 and menu.link == "fxgenie.fx_value.<ctm"
        assert menu.entries[0][1].pairs == [("__AN_NAME__", "FxTplMenu")]
        assert "fxgenie.fx_value.<ctm#ed02da80" in _names(pb)
        assert not U.page_needs_update(p)


def test_a_linked_page_holding_the_marker_at_the_current_stamp_is_out_of_date(fx_install):
    """A linked New Page made while the library was missing holds the marker in its template
    copy and quotes the template's current stamp: once the library is back the template's
    load places the genie, so the open and Update Pages both call the page out of date, and
    its update places the genie."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP
    from ctgedit.io import updatepages as U

    _held_template(fx_install)
    genie = fx_install.file("FxLib", "fxgenie.ctm")
    os.replace(genie, genie + ".away")
    libraries_changed()
    pp = os.path.join(fx_install.project("FxMain"), "HolderLinked.ctg")
    EditableDocument.new_from_template("fx_style.normal@XGA.<ctt", pp, linked=True)
    assert len(_markers(_read(pp).objects)) == 1
    assert not U.page_needs_update(pp)
    os.replace(genie + ".away", genie)
    libraries_changed()
    assert U.page_needs_update(pp)
    doc = EditableDocument.open(pp)
    assert doc.template_status()[0] == "stale"
    assert doc.apply_open_updates()
    doc.save()
    pb = _read(pp)
    a, b, _r = TP.template_roots(pb.objects)[0]
    assert _markers(pb.objects[a:b]) == []
    assert _named(pb.objects, "FxTplMenu").dw == 0x218
    assert "\\\\Fx Menu" in _names(pb)
    assert not U.page_needs_update(pp)
    assert EditableDocument.open(pp).template_status()[0] == "current"


def _order_page(fx):
    """CtDraw's linked New Page of chain5/order (new_page: made in a Graphics Builder where
    fxgenie.ctm did not open): its template copy holds FxTplMenu as a placeholder, while
    normal@XGA holds the genie and the page quotes its current stamp."""
    from ctgedit.editing import libraries_changed

    path = os.path.join(fx.project("FxMain"), "FxOracleOrder.ctg")
    shutil.copyfile(_oracle("chain5", "order", "new_page", "page.ctg"), path)
    libraries_changed()
    return path


@pytest.mark.parametrize("how", ["open", "update pages"])
def test_a_pages_own_template_copy_holding_a_placeholder_is_composed_again(fx_install, how):
    """A page's own template copy holding a placeholder is composed again."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP
    from ctgedit.io import updatepages as U

    path = _order_page(fx_install)
    made = _captured("chain5", "order", "page_made")
    a, b, _r = TP.template_roots(made.objects)[0]
    assert len(_markers(_read(path).objects)) == 1 and _markers(made.objects) == []
    assert U.page_needs_update(path)
    if how == "open":
        doc = EditableDocument.open(path)
        assert doc.template_status()[0] == "stale"
        assert doc.apply_open_updates()
        doc.save()
    else:
        (rec,) = U.update_pages(pages=[path]).records
        assert rec["outcome"] == "updated"
    libraries_changed()
    got = _read(path)
    a2, b2, _r = TP.template_roots(got.objects)[0]
    assert _keys(got.objects[a2:b2]) == _keys(made.objects[a:b])
    assert _names(got) == ["fx_style.normal@XGA.<ctt#ed02da80", "\\\\Fx Menu"]
    assert not U.page_needs_update(path)
    assert EditableDocument.open(path).template_status()[0] == "current"


def test_a_pages_template_copy_keeps_its_placeholder_while_the_library_is_missing(fx_install):
    """The same page with fxgenie.ctm missing: the marker stands for a genie that does not
    open, so the page is current (the copy is not composed for it)."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import updatepages as U

    path = _order_page(fx_install)
    genie = fx_install.file("FxLib", "fxgenie.ctm")
    os.replace(genie, genie + ".away")
    libraries_changed()
    assert not U.page_needs_update(path)
    assert EditableDocument.open(path).template_status()[0] == "current"


def test_update_pages_says_why_a_template_placeholder_stays():
    """The 'unchanged' reason of a page whose template copy still holds a placeholder after
    the update (its genie could not be placed) is not 'save the template' (the template
    step places it, as CtDraw's load does)."""
    from ctgedit.io import updatepages as U

    key = ("fxgenie", "fx_value")
    why = U.unchanged_reason({key}, {}, False, False, set(), placeholders={key}, in_template={key})
    assert why == (
        "a placeholder of fxgenie.fx_value in the page's template could not be "
        "placed again (the log says why)"
    )


# ------------------------------------------------------------------------- the extra items
def test_a_new_elements_first_save_places_a_pasted_placeholder(fx_install):
    """A NEW genie with FxReturned's placeholder of fxback.fx_value pasted in: its first save
    places the genie (a linked copy, the table its current entry), as every later save does."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    src = EditableDocument.open(fx_install.page("FxReturned"))
    i = [i for i, lib, el, _x in src.placeholder_genies() if el == "fx_value"][0]
    payload = src.copy_payload([i])
    lib = os.path.join(fx_install.project("FxMain"), "mylib.ctm")
    h = ElementDocument.new_element(lib, "hp", "genie")
    h.create_object("rectangle_outline", (0, 0, 300, 100))
    h.paste_payload(payload, offset=0)
    assert len(h.placeholder_genies()) == 1
    h.save()
    libraries_changed()
    pe = _element(lib, "hp")
    assert _markers(pe.tree) == []
    assert [GP.decode(po).link for po in pe.tree if po.subtype_size & 0x201 == 0x201] == [
        "fxback.fx_value.<ctm"
    ]
    assert [n for _z, n in _table(pe.page) if not n.startswith("\\")] == ["fxback.fx_value.<ctm#ed02da80"]
    assert ElementDocument.open_element(lib, "hp").nested_genie_changes() == frozenset()


def test_an_element_table_lists_a_placeholder_and_a_copy_of_the_same_genie(fx_install):
    """genie_norm.rebuild_link_table: a placeholder of fxback.fx_value and a placed copy of
    it in one element list the bare entry and the copy's own entry, in record order, either
    way round."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_norm as GN
    from ctgedit.format import genie_place as GP

    doc = EditableDocument.open(fx_install.page("FxReturned"))
    mark = [doc.parsed.page.objects[i] for i, lib, el, _x in doc.placeholder_genies() if el == "fx_value"][0]
    doc.instantiate_placeholder_genies()
    objs = doc.parsed.page.objects
    k = [
        k
        for k, po in enumerate(objs)
        if po.subtype_size & 0x201 == 0x201 and GP.decode(po).link == "fxback.fx_value.<ctm"
    ][0]
    copy_ = list(objs[k : GP.subtree_end(objs, k)])
    old = [(b"\x01" * 16, "fxback.fx_value.<ctm#ed02da80"), (bytes(16), "fxback.fx_value.<ctm")]
    for recs, want in (([mark] + copy_, [old[1], old[0]]), (copy_ + [mark], old)):
        got = [e for e in GN.rebuild_link_table(recs, old, None) if not e[1].startswith("\\")]
        assert got == want


def test_update_pages_reads_a_template_entrys_guid_as_the_open_does(fx_install):
    """Update Pages reads a template entry's GUID the way the open does."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import links
    from ctgedit.io import updatepages as U

    path = fx_install.page("FxTplLinked")
    doc = EditableDocument.open(path)
    pb = doc.parsed.page
    area, ents = links.parse_link_table(pb.link_table_raw)
    ents = [(bytes(16), n) if ".<ctt" in n else (g, n) for g, n in ents]
    pb.link_table_raw, pb.link_count = links.serialize_link_table(area, ents)
    with open(path, "wb") as fh:
        fh.write(doc.serialize())
    libraries_changed()
    assert EditableDocument.open(path).template_status()[0] == "stale"
    assert U.page_needs_update(path)
    (rec,) = U.update_pages(pages=[path]).records
    assert rec["outcome"] == "updated"
    assert not U.page_needs_update(path)
    assert EditableDocument.open(path).template_status()[0] == "current"
