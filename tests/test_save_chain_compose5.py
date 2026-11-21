"""Composition along the save chain."""

import itertools
import os

import fxinstall
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ctgedit.format import element as E  # noqa: E402
from ctgedit.format import genie_place as GP  # noqa: E402
from ctgedit.format import links  # noqa: E402

pytestmark = pytest.mark.golden

OUT = "fxouter@XGA"
OUT_REF = "fx_style.fxouter@XGA.<ctt"


@pytest.fixture
def fx(fx_install, monkeypatch):
    """The fake install, each save with a stamp of its own."""
    c = itertools.count(E.ctdraw_timestamp())
    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: next(c))
    return fx_install


def _cap(step, unit, *parts) -> bytes:
    return fxinstall.oracle_bytes(step, unit, "/".join(parts))


def _put(fx, project, name, data) -> str:
    from ctgedit.editing import libraries_changed

    path = os.path.join(fx.project(project), name)
    with open(path, "wb") as fh:
        fh.write(data)
    libraries_changed()
    return path


def _levels(r):
    return [[list(b.pairs) for b in lv] for lv in (r.entries or [])]


def _key(po):
    r = GP.decode(po)
    slots = [(m, tuple(round(v, 6) for v in x)) for m, x in r.slots]
    return (r.cf, r.tc, r.dw, r.handle, r.an, r.name, r.link, slots, r.anchor, r.rect1, r.rect2, _levels(r))


def _keys(recs):
    return [_key(po) for po in recs]


def _tree(lib, el, data=None):
    if data is not None:
        ext = ".ctt" if "@" in el else ".ctm"
        return list(E.open_element("x" + ext, el, library=E.LibraryFile(data)).tree)
    return list(E.open_element(lib, el).tree)


def _table(lib, el, data=None):
    if data is not None:
        ext = ".ctt" if "@" in el else ".ctm"
        pb = E.open_element("x" + ext, el, library=E.LibraryFile(data)).page
    else:
        pb = E.open_element(lib, el).page
    return (
        [n.split("#")[0] for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []
    )


def _page(data):
    from ctgedit.format.page import parse_ctg_bytes

    return parse_ctg_bytes(data).page


def _page_file(path):
    with open(path, "rb") as fh:
        return _page(fh.read())


def _page_table(pb):
    return (
        [n.split("#")[0] for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []
    )


def _declared(recs):
    """The names the element's records declare on their outermost level."""
    out = []
    for po in recs:
        r = GP.decode(po)
        if r.entries and len(r.entries) >= 2:
            for k, _v in r.entries[-1][0].pairs:
                if k not in out:
                    out.append(k)
    return out


def _open(lib, el):
    from ctgedit.editing import ElementDocument

    return ElementDocument.open_element(lib, el)


def _save(doc, **kw):
    from ctgedit.editing import libraries_changed

    rep: dict = {}
    doc.save(report=rep, **kw)
    libraries_changed()
    return rep


def _blank_page(fx, name):
    from ctgedit.format import template_place as TP

    path = os.path.join(fx.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(TP.new_page_bytes(1024, 768, background=(0xD8D8D8, 0xD8D8D8)))
    return path


def _text_of(recs, template):
    hits = [
        po
        for po in recs
        if po.type_code == 0x05
        and GP.decode(po).entries
        and GP.decode(po).entries[0][2].get("TXT_OFF") == template
    ]
    assert len(hits) == 1, template
    return GP.decode(hits[0])


# ---------------------------------------------------------------- Q18: New Page
@pytest.mark.parametrize(
    "stage, lib", [("first", ("params_added", "library.ctt")), ("resaved", ("resaved", OUT, "library.ctt"))]
)
def test_a_new_page_binds_nothing_and_keeps_the_texts_as_typed(fx, stage, lib):
    """A new page binds nothing and keeps the texts as typed."""
    from ctgedit.editing import EditableDocument

    _put(fx, "FxStyle", "fx_style.ctt", _cap("chain6", "outer_mixed", *lib))
    path = os.path.join(fx.project("FxMain"), "FromMixed.ctg")
    EditableDocument.new_from_template(OUT_REF, path, linked=True)
    got = _page_file(path)
    want = _page(_cap("chain6", "outer_mixed", stage, "new_page", "page.ctg"))
    assert _keys(got.objects) == _keys(want.objects)
    assert _page_table(got) == _page_table(want)
    root = GP.decode(got.objects[0])
    assert _levels(root)[1] == [[("Q", "5")], [], []]
    for typed, width in (("%P%", 33), ("%Q=5%", 51)):
        t = _text_of(got.objects, typed)
        assert t.entries[0][1].get("TXT_OFF") == typed
        assert t.rect1[2] - t.rect1[0] == width


def test_the_new_page_is_current_and_its_save_a_fixed_point(fx):
    """The New Page reads current when it is opened again (nothing composes it), and its open
    + save keeps every record: the typed texts are measured as written."""
    from ctgedit.editing import EditableDocument

    _put(fx, "FxStyle", "fx_style.ctt", _cap("chain6", "outer_mixed", "params_added", "library.ctt"))
    path = os.path.join(fx.project("FxMain"), "FromMixed.ctg")
    EditableDocument.new_from_template(OUT_REF, path, linked=True)
    before = _keys(_page_file(path).objects)
    doc = EditableDocument.open(path)
    assert doc.template_status()[0] == "current"
    assert not doc.apply_open_updates()
    doc.save()
    assert _keys(_page_file(path).objects) == before


def test_a_template_update_blanks_the_typed_token(fx):
    """Q25 b: a page made by New Page from fxouter@XGA (P without a default) after
    normal@XGA changed: CtDraw's load + save blanks the '%P%' text (no TXT_OFF, 4 wide), and
    so do ctgedit's open + save and its template update, record for record."""
    from ctgedit.editing import EditableDocument

    _put(fx, "FxStyle", "fx_style.ctt", _cap("chain7", "outer_param_page", "inner_changed", "library.ctt"))
    want = _page(_cap("chain7", "outer_param_page", "fresh", "page.ctg"))
    for how in ("open", "update"):
        path = _put(
            fx, "FxMain", "FromOuter.ctg", _cap("chain7", "outer_param_page", "page_made", "page.ctg")
        )
        doc = EditableDocument.open(path)
        if how == "open":
            assert doc.apply_open_updates()
        else:
            assert doc.update_template()
        doc.save()
        got = _page_file(path)
        assert _keys(got.objects) == _keys(want.objects), how
        t = _text_of(got.objects, "%P%")
        assert t.entries[0][1].get("TXT_OFF") is None and t.rect1 == (500, 500, 504, 516)


# ---------------------------------------------------------------- Q19: a first parameter
def _gain_p(fx):
    """CtDraw's fx_plain holding its pasted unbound page-form copy of n0, then '%P%' drawn
    in it and saved (ctgedit); the library path."""
    lib = _put(fx, "FxLib", "fxgenie.ctm", _cap("chain6", "paramless_copy", "pasted", "library.ctm"))
    d = _open(lib, "fx_plain")
    d.create_object("text", rect=(0, 25, 10, 41), text="%P%")
    _save(d)
    return lib


def test_a_first_parameter_gives_an_unbound_copy_the_element_level(fx):
    """fx_plain gains '%P%' while it holds an unbound page-form copy of n0: every record of
    the copy takes an empty third level (CtDraw's save, 6 of 6 records), and fx_plain
    declares P alone."""
    lib = _gain_p(fx)
    want = _tree(None, "fx_plain", _cap("chain6", "paramless_copy", "gained", "library.ctm"))
    got = _tree(lib, "fx_plain")
    assert _keys(got) == _keys(want)
    assert _declared(got) == ["P"]
    assert list(GP.declared_params_of(lib, "fx_plain")) == ["P"]


def test_the_genie_nesting_it_declares_nothing(fx):
    """fx_none (no parameters) nesting fx_plain, saved after ctgedit's own fx_plain gained
    '%P%': CtDraw's fx_none record for record (8 of 8), declaring nothing; a placement of it
    leaves every text unbound. Before, the copy's text took Label on fx_none's level (7 of
    8) and fx_none listed it."""
    from ctgedit.editing import EditableDocument

    _gain_p(fx)
    parts = _put(fx, "FxLib", "fxparts.ctm", _cap("chain6", "paramless_copy", "host_made", "library.ctm"))
    _save(_open(parts, "fx_none"))
    want = _tree(None, "fx_none", _cap("chain6", "paramless_copy", "same", "fx_none", "library.ctm"))
    got = _tree(parts, "fx_none")
    assert _keys(got) == _keys(want)
    assert list(GP.declared_params_of(parts, "fx_none")) == []
    path = _blank_page(fx, "FromNone")
    doc = EditableDocument.open(path)
    # (CtDraw's capture was placed with P and Label: it keeps them in the root's own value
    # bag, the root not dynamic; in the editor a genie form can ask for such values)
    doc.place_library_element("fxparts", "fx_none", "ctm", (200, 200), params={"P": "PV", "Label": "LV"})
    doc.save()
    page = _page(_cap("chain6", "paramless_copy", "same", "page", "page.ctg"))
    placed = _page_file(path).objects
    assert _keys(placed) == _keys(page.objects)
    assert [GP.decode(po).dw for po in placed] == [GP.decode(po).dw for po in page.objects]
    for po in placed:
        r = GP.decode(po)
        if r.tc == 0x05:
            assert not r.entries[0][1].get("TXT_OFF")


def test_values_for_a_genie_without_parameters_go_to_its_root(fx):
    """fx_none (no parameters) placed with P and Label: CtDraw's placement stores them in
    the root's own value bag (e0.b1) only, in the order given, and the root stays static
    (dw 0x209); every member is CtDraw's (chain6/paramless_copy same/page). A placement
    without values writes none."""
    from ctgedit.editing import EditableDocument

    _put(fx, "FxLib", "fxgenie.ctm", _cap("chain6", "paramless_copy", "gained", "library.ctm"))
    _put(fx, "FxLib", "fxparts.ctm", _cap("chain6", "paramless_copy", "same", "fx_none", "library.ctm"))
    want = _page(_cap("chain6", "paramless_copy", "same", "page", "page.ctg")).objects
    path = _blank_page(fx, "ValuesNone")
    doc = EditableDocument.open(path)
    doc.place_library_element("fxparts", "fx_none", "ctm", (200, 200), params={"P": "PV", "Label": "LV"})
    doc.save()
    got = _page_file(path).objects
    assert _keys(got) == _keys(want)
    root = GP.decode(got[0])
    assert len(root.entries) == 1 and root.entries[0][1].pairs == [("P", "PV"), ("Label", "LV")]
    assert root.dw == 0x209
    plain = _blank_page(fx, "NoValues")
    doc = EditableDocument.open(plain)
    doc.place_library_element("fxparts", "fx_none", "ctm", (200, 200))
    doc.save()
    assert not GP.decode(_page_file(plain).objects[0]).entries[0][1].pairs


def test_an_unbound_page_copy_pasted_into_a_genie_takes_the_element_level(fx):
    """A page copy of n0 placed with nothing bound, pasted into n1 (a genie element): every
    record of the copy keeps its levels and takes n1's level, empty, so n1 still declares
    Name only (Label stays the copy's)."""
    from ctgedit.editing import EditableDocument

    lib = fx.file("FxLib", "fxnest.ctm")
    path = _blank_page(fx, "Unbound")
    page = EditableDocument.open(path)
    page.place_library_element("fxnest", "n0", "ctm", (100, 100))
    page.save()
    page = EditableDocument.open(path)
    roots = [i for i, o in enumerate(page.page.objects) if o.parent is None]
    payload = page.copy_payload([roots[0]])
    before = [len(GP.decode(po).entries or ()) for po in page.parsed.page.objects]
    d = _open(lib, "n1")
    n = len(d.parsed.page.objects)
    d.paste_payload(payload, offset=0)
    _save(d)
    got = _tree(lib, "n1")
    assert list(GP.declared_params_of(lib, "n1")) == ["Name"]
    pasted = got[-(len(got) - 1 - n) :] if len(got) - 1 > n else []
    assert [len(GP.decode(po).entries or ()) for po in pasted] == [b + 1 for b in before]


# ---------------------------------------------------------------- Q20: a stale host on a page
def _stale_page(fx):
    _put(fx, "FxLib", "fxnest.ctm", _cap("chain6", "stale_page", "n1_saved", "n1", "library.ctm"))
    return _put(fx, "FxMain", "StaleHost.ctg", _cap("chain6", "stale_page", "placed", "page.ctg"))


def _gained_text(recs):
    return _text_of(recs, "%Label% X")


def test_a_page_placing_a_stale_host_binds_the_gained_text(fx):
    """The page placing n2 (Name 'P2') while n2 is out of date for the text n0 gained (n1
    saved, n2 not): its open + save gives CtDraw's page load + save record for record, the
    gained text bound 'P2 L1 X' (49 wide, three levels: Label 'P2 L1', Name 'P2'),
    the genie and its copies refit to it."""
    from ctgedit.editing import EditableDocument

    path = _stale_page(fx)
    doc = EditableDocument.open(path)
    assert doc.apply_open_updates()
    doc.save()
    got = _page_file(path)
    want = _page(_cap("chain6", "stale_page", "fresh", "page.ctg"))
    assert _keys(got.objects) == _keys(want.objects)
    t = _gained_text(got.objects)
    assert t.entries[0][1].get("TXT_OFF") == "P2 L1 X" and t.rect1 == (200, 295, 249, 311)
    assert _levels(t)[1:] == [
        [[("Label", "\n")], [("Label", "P2 L1")], [("Label", "%Name% L1")]],
        [[("Name", "\n")], [("Name", "P2")], []],
    ]


def test_update_pages_and_a_new_placement_bind_it_too(fx):
    """Update Pages of that page, and a new placement of the stale n2 with Name 'P2' on a
    blank page, give CtDraw's records (its load + save, and its own new placement)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as UP

    path = _stale_page(fx)
    UP.update_pages(pages=[path])
    assert _keys(_page_file(path).objects) == _keys(
        _page(_cap("chain6", "stale_page", "fresh", "page.ctg")).objects
    )
    blank = _blank_page(fx, "PlaceStale")
    doc = EditableDocument.open(blank)
    doc.place_library_element("fxnest", "n2", "ctm", (200, 200), params={"Name": "P2"})
    doc.save()
    assert _keys(_page_file(blank).objects) == _keys(
        _page(_cap("chain6", "stale_page", "fresh", "placed", "page.ctg")).objects
    )


# ---------------------------------------------------------------- Q25 a: a paste
def _paste_plain_into_n0(fx):
    from ctgedit.editing import EditableDocument

    path = _put(fx, "FxMain", "PlacedPlain.ctg", _cap("chain7", "paste_paramless", "placed", "page.ctg"))
    page = EditableDocument.open(path)
    roots = [i for i, o in enumerate(page.page.objects) if o.parent is None]
    payload = page.copy_payload([roots[0]])
    lib = fx.file("FxLib", "fxnest.ctm")
    d = _open(lib, "n0")
    d.paste_payload(payload, offset=0)
    _save(d)
    return lib


def test_a_pasted_page_copy_of_a_parameterless_genie_takes_the_hosts_level(fx):
    """A clipboard paste of a page copy of fx_plain (handles 2 / 0 / 1) into n0 (its text
    holds 0): CtDraw's n0 record for record: each record of the copy two levels, the
    second empty, the free handles kept first (2 and 1) and the taken one the next free (3)."""
    lib = _paste_plain_into_n0(fx)
    want = _tree(None, "n0", _cap("chain7", "paste_paramless", "pasted", "library.ctm"))
    got = _tree(lib, "n0")
    assert _keys(got) == _keys(want)
    assert _table(lib, "n0") == _table(None, "n0", _cap("chain7", "paste_paramless", "pasted", "library.ctm"))


def test_the_genie_nesting_the_paste_takes_it_as_ctdraw_does(fx):
    """n1's save after ctgedit's paste into n0: CtDraw's n1 over its own paste, record for
    record (the copy of fx_plain inside n0's copy three levels on each record)."""
    lib = _paste_plain_into_n0(fx)
    _save(_open(lib, "n1"))
    want = _tree(None, "n1", _cap("chain7", "paste_paramless", "same", "n1", "library.ctm"))
    assert _keys(_tree(lib, "n1")) == _keys(want)


# ---------------------------------------------------------------- Q25 c: into a template
def test_a_genie_placed_into_a_template_with_a_parameter_takes_the_element_form(fx):
    """n0 (Label 'TL') and fx_plain placed into fxouter@XGA, which declares P: CtDraw's
    template record for record: n0's root levels [e0, Label 'TL' in values and
    templates, empty], fx_plain's records two levels each; the template declares P only."""
    lib = _put(fx, "FxStyle", "fx_style.ctt", _cap("chain7", "genie_in_outer", "param_added", "library.ctt"))
    d = _open(lib, OUT)
    d.place_library_element("fxnest", "n0", "ctm", (600, 600), params={"Label": "TL"})
    d.place_library_element("fxgenie", "fx_plain", "ctm", (600, 650))
    _save(d)
    want = _tree(None, OUT, _cap("chain7", "genie_in_outer", "placed", "library.ctt"))
    got = _tree(lib, OUT)
    assert _keys(got) == _keys(want)
    assert _declared(got) == ["P"]
    assert _table(lib, OUT) == _table(None, OUT, _cap("chain7", "genie_in_outer", "placed", "library.ctt"))


def test_a_plain_template_keeps_the_page_form(fx):
    """A template without parameters of its own places a genie in the page form
    (R2: the plain templates' nested genies at their genie's level count)."""
    lib = fx.file("FxStyle", "fx_style.ctt")
    d = _open(lib, "normal@XGA")
    d.place_library_element("fxnest", "n0", "ctm", (600, 600), params={"Label": "TL"})
    _save(d)
    got = _tree(lib, "normal@XGA")
    roots = [
        po
        for po in got[1:]
        if po.class_flag and po.subtype_size & 0x200 and GP.decode(po).link == "fxnest.n0.<ctm"
    ]
    assert len(roots) == 1 and len(GP.decode(roots[0]).entries) == 2


def test_the_genie_dialog_in_a_template_with_a_parameter_keeps_the_element_form(fx):
    """The genie dialog's OK on n0's copy in CtDraw's fxouter@XGA (it declares P; the copy in
    the element form, as CtDraw placed it): the copy is composed again in that form, its
    text shows the new value, and the template still declares P only."""
    lib = _put(fx, "FxStyle", "fx_style.ctt", _cap("chain7", "genie_in_outer", "placed", "library.ctt"))
    d = _open(lib, OUT)
    objs = d.parsed.page.objects
    i = [k for k, po in enumerate(objs) if po.class_flag and GP.decode(po).link == "fxnest.n0.<ctm"][0]
    assert d.set_genie_bindings(i, {"Label": "T2"}, dialog=True)
    _save(d)
    got = _tree(lib, OUT)
    assert _declared(got) == ["P"]
    k = [j for j, po in enumerate(got) if po.class_flag and GP.decode(po).link == "fxnest.n0.<ctm"][0]
    root = GP.decode(got[k])
    assert len(root.entries) == 3 and root.entries[1][2].get("Label") == "T2"
    text = GP.decode(got[k + 1])
    assert len(text.entries) == 3 and text.entries[0][1].get("TXT_OFF") == "T2"


def test_a_placeholder_in_a_template_with_a_parameter_is_placed_in_the_element_form(fx):
    """A placeholder in a template with a parameter is placed in the element form."""
    from ctgedit.editing import EditableDocument, ElementDocument

    src = EditableDocument.open(fx.page("FxReturned"))
    payload = src.copy_payload([src.placeholder_genies()[0][0]])
    tlib = os.path.join(fx.project("FxMain"), "mytpl.ctt")
    t = ElementDocument.new_element(tlib, "ph@XGA", "template", page_size=(1024, 768))
    t.create_object("rectangle_outline", (0, 0, 1023, 767))
    t.create_object("text", rect=(500, 500, 510, 516), text="%P%")
    t.paste_payload(payload, offset=0)
    _save(t)
    # (the template's own save places the placeholder, its library opening, as CtDraw's
    # template save does: chain6/template_placeholder, the placeholders package)
    assert not _open(tlib, "ph@XGA").placeholder_genies()
    got = _tree(tlib, "ph@XGA")
    assert _declared(got) == ["P"]
    k = [j for j, po in enumerate(got) if po.class_flag and GP.decode(po).link == "fxback.fx_value.<ctm"][0]
    span = range(k, k + 1 + got[k].class_flag)
    assert [len(GP.decode(got[j]).entries) for j in span] == [3] * len(span)


# ---------------------------------------------------------------- Q25 d: leading blanks
def test_a_nested_copys_value_is_trimmed_in_the_element_form(fx):
    """n0's text ' %Label%' (CtDraw's n0): n1's save stores its copy's value '%Name% L1',
    blank trimmed, in the box measured with it (10, 40, 94, 56), CtDraw's record for record;
    n0's own text keeps ' ' (a direct member)."""
    lib = _put(fx, "FxLib", "fxnest.ctm", _cap("chain7", "label_blank", "n0_blank", "library.ctm"))
    _save(_open(lib, "n1"))
    want = _tree(None, "n1", _cap("chain7", "label_blank", "n1_after", "n1", "library.ctm"))
    got = _tree(lib, "n1")
    assert _keys(got) == _keys(want)
    t = [
        GP.decode(po)
        for po in got
        if po.type_code == 0x05 and GP.decode(po).entries[0][2].get("TXT_OFF") == " %Label%"
    ][0]
    assert t.entries[0][1].get("TXT_OFF") == "%Name% L1" and t.rect1 == (10, 40, 94, 56)
    own = _tree(lib, "n0")
    assert [GP.decode(po).entries[0][1].get("TXT_OFF") for po in own if po.type_code == 0x05] == [" "]


def test_a_blank_led_binding_is_trimmed_in_the_copys_text(fx):
    """n0 placed into n1 with Label ' %Name% L1': the binding level keeps its blanks
    ('  L1', ' %Name% L1'), the text stores '%Name% L1' in a box measured with both blanks
    (10, 70, 98, 86): CtDraw's n1 record for record."""
    lib = _put(fx, "FxLib", "fxnest.ctm", _cap("chain7", "label_blank", "n1_after", "n1", "library.ctm"))
    d = _open(lib, "n1")
    d.place_library_element("fxnest", "n0", "ctm", (10, 70), params={"Label": " %Name% L1"})
    _save(d)
    want = _tree(None, "n1", _cap("chain7", "label_blank", "n1_bound", "library.ctm"))
    assert _keys(_tree(lib, "n1")) == _keys(want)
