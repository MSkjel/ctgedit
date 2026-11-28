"""Save chain links, against CtDraw captures."""

from ctgedit.ui.main_window import MainWindow
import os
import shutil
import sys


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fxinstall  # noqa: E402

STATE_LABELS = [
    "\\\\Running",
    "\\\\Stopped",
    "\\\\Fault",
    "\\\\Manual",
    "\\\\Auto",
    "\\\\Hand",
    "\\\\Off Mode",
    "\\\\Fx Off",
    "\\\\Fx On",
]


# ------------------------------------------------------------------------------ helpers
def _oracle(step, unit, *parts):
    return fxinstall.oracle(step, unit, "/".join(parts))


def _put(src, dst):
    from ctgedit.editing import libraries_changed

    shutil.copyfile(src, dst)
    libraries_changed()


def _write_page(fx, name, step, unit, stage, project="FxMain"):
    from ctgedit.editing import libraries_changed

    path = os.path.join(fx.project(project), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(fxinstall.oracle_bytes(step, unit, "%s/page.ctg" % stage))
    libraries_changed()
    return path


def _page(step, unit, stage):
    from ctgedit.format.page import parse_ctg_bytes

    return parse_ctg_bytes(fxinstall.oracle_bytes(step, unit, "%s/page.ctg" % stage)).page


def _read(path):
    from ctgedit.format.page import parse_ctg_bytes

    with open(path, "rb") as fh:
        return parse_ctg_bytes(fh.read()).page


def _table(pb):
    from ctgedit.format import links

    if not pb.link_count:
        return []
    return [(g == bytes(16), n) for g, n in links.parse_link_table(pb.link_table_raw)[1]]


def _names(pb):
    return [n for _z, n in _table(pb)]


def _key(po, handles=True):
    from ctgedit.format import genie_place as GP

    r = GP.decode(po)
    return (
        r.cf,
        r.tc,
        r.dw,
        r.handle if handles else None,
        r.an,
        r.name,
        r.link,
        r.slots,
        r.anchor,
        r.rect1,
        r.rect2,
        [[list(b.pairs) for b in lvl] for lvl in (r.entries or [])],
    )


def _keys(objs, handles=True):
    return [_key(po, handles) for po in objs]


def _no_size_words(po):
    """The record's bytes with its bags' size words set to 0: the overshoot is the
    Graphics Builder's session state, which its loader ignores."""
    from ctgedit.format import genie_place as GP

    r = GP.decode(po)
    for lvl in r.entries or []:
        for b in lvl:
            b.ov = 0
    return GP.encode(r)


def _element(path, name):
    from ctgedit.format.element import open_element

    return open_element(path, name)


# ------------------------------------------------------- an element holding a placeholder
def _row_library(fx, which="element_missing", nested=True):
    """fxparts.ctm as CtDraw saved fx_row with fxnest.ctm missing (its copy of n1 one
    placeholder, the table its bare entry), and fxnest.ctm back (``nested``) or gone."""
    from ctgedit.editing import libraries_changed

    lib = fx.file("FxLib", "fxparts.ctm")
    shutil.copyfile(_oracle("chain5", "element_placeholder", which, "library.ctm"), lib)
    if not nested:
        os.remove(fx.file("FxLib", "fxnest.ctm"))
    libraries_changed()
    return lib


def test_the_elements_save_places_the_genie_again_as_ctdraws_load_and_save(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _row_library(fx_install)
    doc = ElementDocument.open_element(lib, "fx_row")
    assert [(a, b) for _i, a, b, _x in doc.placeholder_genies()] == [("fxnest", "n1")]
    assert ("fxnest", "n1") in doc.nested_genie_changes()
    doc.save(refresh=True, expect_stored=True)
    libraries_changed()
    got = _element(lib, "fx_row")
    want = _element(
        _oracle("chain5", "element_placeholder", "element_back", "fx_row", "library.ctm"), "fx_row"
    )
    assert _keys(got.tree) == _keys(want.tree)  # (the handles CtDraw gave too)
    assert _table(got.page) == [(False, "fxnest.n1.<ctm#ed02da80")] == _table(want.page)


def test_the_elements_save_places_it_also_without_the_bare_entry(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _row_library(fx_install, "nobare_input")
    assert _table(_element(lib, "fx_row").page) == []
    ElementDocument.open_element(lib, "fx_row").save(refresh=True, expect_stored=True)
    libraries_changed()
    want = _element(
        _oracle("chain5", "element_placeholder", "element_nobare", "fx_row", "library.ctm"), "fx_row"
    )
    assert _keys(_element(lib, "fx_row").tree) == _keys(want.tree)


def test_the_element_tabs_automatic_update_places_it(fx_install):
    """The tab's update (the worker's ElementDocument.nested_update_ops): the copy's records
    come in as an automatic step, the save not needed."""
    from ctgedit.editing import ElementDocument

    lib = _row_library(fx_install, "nobare_input")
    doc = ElementDocument.open_element(lib, "fx_row")
    ops, _link = doc.nested_update_ops()
    assert ops
    assert doc.update_nested_genies() >= 1
    assert doc.placeholder_genies() == []
    assert any(po.subtype_size & 0x209 == 0x209 for po in doc.parsed.page.objects)


def test_compose_nested_genies_again_places_it(fx_install):
    """Edit > Library > Compose Nested Genies Again (nested_update_ops every=True) on an
    element whose only nested genie is the placeholder's."""
    from ctgedit.editing import ElementDocument

    lib = _row_library(fx_install, "nobare_input")
    doc = ElementDocument.open_element(lib, "fx_row")
    rep = {}
    ops, _link = doc.nested_update_ops(every=True, report=rep)
    assert ops and ("fxnest", "n1") in [(a.lower(), b.lower()) for a, b in rep["changed_copies"] if a]


def test_used_by_calls_the_element_out_of_date_until_its_save(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU

    lib = _row_library(fx_install)
    nest = fx_install.file("FxLib", "fxnest.ctm")

    def status():
        return {u.ref.label: u.status for u in GU.element_users(nest, "n1").users}

    assert status()["fxparts.fx_row"] == GU.OUT_OF_DATE
    plan = GU.chain_plan([fx_install.project("FxLib")])
    assert "fxparts.fx_row" in [u.ref.label for u in plan.elements]
    ElementDocument.open_element(lib, "fx_row").save(refresh=True, expect_stored=True)
    libraries_changed()
    assert status()["fxparts.fx_row"] == GU.UP_TO_DATE


def test_while_the_library_is_missing_the_save_keeps_the_marker_and_its_bare_entry(fx_install):
    """The save keeps the bare entry, the only trace of the genie."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _row_library(fx_install, nested=False)
    doc = ElementDocument.open_element(lib, "fx_row")
    assert doc.placeholder_genies() == []
    doc.create_object("rectangle", (0, 70, 10, 80))
    doc.save()
    libraries_changed()
    got = _element(lib, "fx_row")
    assert any(po.type_code == 0xFFFF for po in got.tree)
    assert _table(got.page) == [(True, "fxnest.n1.<ctm")]


def test_with_the_library_back_a_pages_open_gives_the_nested_genie_as_an_unlinked_copy(fx_install):
    """CtDraw's load + save of a page placing fx_row (its marker inside the instance): the
    marker becomes n1's copy inside the instance, dwFlags 0x208, its texts as CtDraw's
    element load holds them, and the bare entry goes."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U

    _row_library(fx_install)
    path = _write_page(fx_install, "FxOraclePlace", "chain5", "element_placeholder", "page_placed")
    doc = EditableDocument.open(path)
    assert doc.placeholder_genies() == []  # (never a linked instance in the host)
    assert doc.genie_changes() == {("fxparts", "fx_row"): None}
    doc.apply_open_updates()
    doc.save()
    want = _page("chain5", "element_placeholder", "page_back")
    got = _read(path)
    assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want) == [(False, "fxparts.fx_row.<ctm#ee7fd083")]
    assert len(EditableDocument.open(path).genie_instances()) == 1
    # Update Pages on a closed page: the same
    path2 = _write_page(fx_install, "FxOraclePlace2", "chain5", "element_placeholder", "page_placed")
    assert U.page_needs_update(path2)
    (rec,) = U.update_pages(pages=[path2]).records
    assert rec["outcome"] == "updated"
    got = _read(path2)
    assert _keys(got.objects) == _keys(want.objects) and _table(got) == _table(want)


def test_the_page_follows_also_when_the_element_lost_its_bare_entry(fx_install):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U

    _row_library(fx_install, "nobare_input")
    want = _page("chain5", "element_placeholder", "page_back")
    path = _write_page(fx_install, "FxOraclePlace", "chain5", "element_placeholder", "page_placed")
    (rec,) = U.update_pages(pages=[path]).records
    assert rec["outcome"] == "updated"
    assert _keys(_read(path).objects) == _keys(want.objects)
    assert not U.page_needs_update(path)
    assert EditableDocument.open(path).genie_changes() == {}


def _blank_page(fx, name):
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP

    path = os.path.join(fx.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(TP.new_page_bytes(1024, 768))
    libraries_changed()
    return EditableDocument.open(path)


def test_a_placement_of_the_element_gives_the_nested_genie(fx_install):
    _row_library(fx_install)
    doc = _blank_page(fx_install, "FxOracleDraw")
    doc.place_library_element("fxparts", "fx_row", "ctm", (100, 100), {"RowNum": "7"})
    want = _page("chain5", "element_placeholder", "page_placed_back")
    assert _keys(doc.parsed.page.objects) == _keys(want.objects)
    assert _table(doc.parsed.page) == _table(want)


def test_a_placement_while_the_library_is_missing_carries_the_bare_entry(fx_install):
    """CtDraw's placement: the marker keeps its box (the element's, moved), dwFlags 0x8
    (its '_Library' keys are no animation), and the page lists the bare entry."""
    _row_library(fx_install, nested=False)
    doc = _blank_page(fx_install, "FxOracleDraw")
    doc.place_library_element("fxparts", "fx_row", "ctm", (100, 100), {"RowNum": "7"})
    want = _page("chain5", "element_placeholder", "page_placed")
    assert _keys(doc.parsed.page.objects) == _keys(want.objects)
    assert (
        _table(doc.parsed.page)
        == _table(want)
        == [(False, "fxparts.fx_row.<ctm#ee7fd083"), (True, "fxnest.n1.<ctm")]
    )


def test_the_pages_open_while_the_library_is_missing_derives_the_markers_box(fx_install):
    from ctgedit.editing import EditableDocument

    _row_library(fx_install, nested=False)
    path = _write_page(fx_install, "FxOraclePlace", "chain5", "element_placeholder", "page_placed")
    doc = EditableDocument.open(path)
    assert doc.placeholder_boxes_due() == [2]
    assert doc.apply_open_updates() == ["1 placeholder boxes"]
    doc.save()
    want = _page("chain5", "element_placeholder", "page_missing")
    got = _read(path)
    assert _keys(got.objects) == _keys(want.objects) and _table(got) == _table(want)
    doc.undo()
    assert doc.placeholder_boxes_due() == [2]


def test_the_open_in_a_tab_derives_the_markers_box_as_an_automatic_step(fx_install):
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    _row_library(fx_install, nested=False)
    path = _write_page(fx_install, "FxOraclePlace", "chain5", "element_placeholder", "page_placed")
    w = MainWindow()
    try:
        w.open_files([path])
        app.processEvents()
        assert w.doc.placeholder_boxes_due() == []
        assert "placeholder boxes" in [u.label for u in w.doc._undo]
    finally:
        w.doc.dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()
        app.processEvents()


def test_a_template_holding_a_placeholder_is_brought_back_and_its_pages_follow(fx_install):
    """A template holding a placeholder is restored and its pages follow."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.format import template_place as TP
    from ctgedit.io import genieusage as GU, updatepages as U

    src = EditableDocument.open(fx_install.page("FxReturned"))
    payload = src.copy_payload([src.placeholder_genies()[0][0]])
    back = fx_install.file("FxMain", "fxback.ctm")
    os.replace(back, back + ".away")
    libraries_changed()
    tlib = os.path.join(fx_install.project("FxMain"), "mytpl.ctt")
    t = ElementDocument.new_element(tlib, "ph@XGA", "template", page_size=(1024, 768))
    t.create_object("rectangle_outline", (0, 0, 1023, 767))
    t.paste_payload(payload, offset=0)
    t.save()
    os.replace(back + ".away", back)
    libraries_changed()
    assert _table(_element(tlib, "ph@XGA").page) == [(True, "fxback.fx_value.<ctm")]
    pp = os.path.join(fx_install.project("FxMain"), "FromPh.ctg")
    p = EditableDocument.new_from_template("mytpl.ph@XGA.<ctt", pp, linked=True)
    objs = p.parsed.page.objects
    a, b, _r = TP.template_roots(objs)[0]
    assert not any(GP.placeholder_genie(po) for po in objs[a:b])
    assert U.template_placeholder_keys(objs) == set()
    assert "\\\\Fx Brann 1" in _names(p.parsed.page)
    assert not U.page_needs_update(pp)
    plan = GU.chain_plan([fx_install.project("FxMain")])
    assert "mytpl.ph@XGA" in [u.ref.label for u in plan.elements]
    ElementDocument.open_element(tlib, "ph@XGA").save(refresh=True, expect_stored=True)
    libraries_changed()
    assert ElementDocument.open_element(tlib, "ph@XGA").placeholder_genies() == []
    assert U.page_needs_update(pp)
    (rec,) = U.update_pages(pages=[pp]).records
    assert rec["outcome"] == "updated"
    d = EditableDocument.open(pp)
    a, b, _r = TP.template_roots(d.parsed.page.objects)[0]
    assert not any(po.type_code == 0xFFFF for po in d.parsed.page.objects[a:b])
    assert "\\\\Fx Brann 1" in _names(d.parsed.page)
    assert not U.page_needs_update(pp)


# ------------------------------------------------- placeholders on pages: Q3, FxReturned
def test_a_turned_and_scaled_placeholder_is_placed_again_as_ctdraw_does(fx_install):
    """chain4/placeholder_turned: the library back, CtDraw's load + save gave the turned
    and scaled genie back byte for byte (its bags' session size words aside)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U

    want = _page("chain4", "placeholder_turned", "back")
    path = _write_page(fx_install, "FxOraclePlace", "chain4", "placeholder_turned", "missing")
    doc = EditableDocument.open(path)
    assert [(a, b) for _i, a, b, _x in doc.placeholder_genies()] == [("fxgenie", "fx_pump")]
    assert doc.apply_open_updates() == ["1 placeholder genies"]
    doc.save()
    got = _read(path)
    assert [_no_size_words(p) for p in got.objects] == [_no_size_words(p) for p in want.objects]
    assert _table(got) == _table(want) == [(False, "fxgenie.fx_pump.<ctm#ed02da80")]
    path2 = _write_page(fx_install, "FxOraclePlace2", "chain4", "placeholder_turned", "missing")
    (rec,) = U.update_pages(pages=[path2]).records
    assert rec["outcome"] == "updated" and rec["placeholders"] == 1
    got = _read(path2)
    assert [_no_size_words(p) for p in got.objects] == [_no_size_words(p) for p in want.objects]


def test_the_returned_placeholders_are_placed_record_for_record(fx_install):
    """FxReturned (the oracle's load + save): CtDraw's handles, a parameterless genie's root
    keeping the marker's template bag, and the table in the records' order."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxReturned"))
    assert doc.instantiate_placeholder_genies() == 4
    doc.save()
    got = _read(doc.path)
    from ctgedit.format.page import parse_ctg_bytes

    want = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxReturned", "page.ctg")).page
    assert [_no_size_words(p) for p in got.objects] == [_no_size_words(p) for p in want.objects]
    assert _table(got) == _table(want)


# --------------------------------------------------------------- the order of the updates
def test_update_pages_gives_the_templates_new_members_their_handles_first(fx_install):
    """chain5/order: normal@XGA (its nested fx_value copy) and the page's own fx_value gained
    two rectangles each: CtDraw's load + save gives the template's the lowest free handles
    first. Update Pages and the open-tabs step give the same as the open."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    _put(_oracle("chain5", "order", "genie_changed", "library.ctm"), fx_install.file("FxLib", "fxgenie.ctm"))
    _put(
        _oracle("chain5", "order", "template_saved", "library.ctt"),
        fx_install.file("FxStyle", "fx_style.ctt"),
    )
    want = _keys(_page("chain5", "order", "same").objects)
    path = _write_page(fx_install, "FxOracleOrder", "chain5", "order", "page_made")
    (rec,) = U.update_pages(pages=[path]).records
    assert rec["outcome"] == "updated" and rec["templates"] == 1 and rec["instances"] == 1
    assert _keys(_read(path).objects) == want
    path2 = _write_page(fx_install, "FxOracleOrder2", "chain5", "order", "page_made")
    d = EditableDocument.open(path2)
    res = OpenTabsUpdate()
    failed, _found = MainWindow._update_open_document(
        d, {"genies": True, "templates": True, "symbols": True}, res
    )
    assert failed == []
    assert _keys(d.parsed.page.objects) == want


# ------------------------------------------------------------------------------- labels
def test_an_element_lists_the_labels_of_states_arrays_and_on_texts_in_key_order(fx_install):
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    _put(_oracle("chain5", "label_states", "saved", "library.ctm"), lib)
    ElementDocument.open_element(lib, "fx_plain").save(refresh=True, expect_stored=True)
    libraries_changed()
    assert _names(_element(lib, "fx_plain").page) == STATE_LABELS
    doc = _blank_page(fx_install, "FxOraclePlace")
    doc.place_library_element("fxgenie", "fx_plain", "ctm", (100, 100), {})
    names = _names(doc.parsed.page)
    assert names[0].startswith("fxgenie.fx_plain.<ctm#") and names[1:] == STATE_LABELS


def test_a_pages_update_lists_the_label_of_an_edited_text(fx_install):
    """chain5/label_edit: fx_value's text became '@(%Description% EDITED)'; CtDraw's load +
    save of the page lists '\\\\Fx Start EDITED' and drops '\\\\Fx Start'."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U

    _put(
        _oracle("chain5", "label_edit", "genie_edited", "library.ctm"),
        fx_install.file("FxLib", "fxgenie.ctm"),
    )
    want = _page("chain5", "label_edit", "same")
    path = _write_page(fx_install, "FxOraclePlace", "chain5", "label_edit", "placed")
    doc = EditableDocument.open(path)
    doc.apply_open_updates()
    doc.save()
    got = _read(path)
    assert _keys(got.objects) == _keys(want.objects)
    assert _table(got) == _table(want)
    assert _names(got)[1:] == ["\\\\Fx Start EDITED"]
    path2 = _write_page(fx_install, "FxOraclePlace2", "chain5", "label_edit", "placed")
    (rec,) = U.update_pages(pages=[path2]).records
    assert rec["outcome"] == "updated"
    assert _table(_read(path2)) == _table(want)


def test_a_template_update_lists_the_labels_of_its_new_texts(fx_install):
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.io import updatepages as U

    tlib = fx_install.file("FxStyle", "fx_style.ctt")
    t = ElementDocument.open_element(tlib, "normal@XGA")
    t.create_object("text", (20, 700, 200, 716), text="@(Fx Tpl New)")
    t.save()
    libraries_changed()
    tp = fx_install.page("FxTplLinked")
    (rec,) = U.update_pages(pages=[tp]).records
    assert rec["outcome"] == "updated" and rec["templates"] == 1
    labels = [n for n in _names(_read(tp)) if n.startswith("\\")]
    new = EditableDocument.new_from_template(
        "fx_style.normal@XGA.<ctt", os.path.join(fx_install.project("FxMain"), "FxNewT.ctg"), linked=True
    )
    want = [n for n in _names(new.parsed.page) if n.startswith("\\")]
    assert "\\\\Fx Tpl New" in labels and labels[: len(want)] == want


def test_a_paste_from_a_genie_window_lists_only_the_substituted_labels(fx_install):
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib = os.path.join(fx_install.project("FxLib"), "mylib.ctm")
    h = ElementDocument.new_element(lib, "hh", "genie")
    h.create_object("rectangle_outline", (0, 0, 300, 100))
    h.place_library_element("fxgenie", "fx_value", "ctm", (10, 10), params={"Description": "Fx Menu"})
    h.save()
    libraries_changed()
    h = ElementDocument.open_element(lib, "hh")
    assert set(n for n in _names(h.parsed.page) if n.startswith("\\")) == {"\\\\%Description%", "\\\\Fx Menu"}
    objs = h.parsed.page.objects
    k = next(i for i, o in enumerate(objs) if i and o.class_flag and o.subtype_size & 0x201 == 0x201)
    payload = h.copy_payload([k])
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    before = [n for n in _names(pd.parsed.page) if n.startswith("\\")]
    pd.paste_payload(payload, offset=0)
    added = [n for n in _names(pd.parsed.page) if n.startswith("\\") and n not in before]
    assert added == ["\\\\Fx Menu"]
    pd.save()
    pd = EditableDocument.open(pd.path)
    assert [n for n in _names(pd.parsed.page) if n.startswith("\\") and n not in before] == ["\\\\Fx Menu"]


def test_the_saves_prune_keeps_a_value_label_and_drops_a_template_only_one(fx_install):
    """A page whose table lists a placed genie's template label too (an older paste's): the
    save keeps the substituted one only, as a page lists its labels (LIVE B3)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP, links

    pd = EditableDocument.open(fx_install.page("FxShapes"))
    pd.place_library_element("fxgenie", "fx_value", "ctm", (400, 400), params={"Description": "Fx Start"})
    pb = pd.parsed.page
    area, ents = links.parse_link_table(pb.link_table_raw)
    pb.link_table_raw, pb.link_count = links.serialize_link_table(
        area, list(ents) + [(bytes(16), "\\\\%Description%")]
    )
    assert (
        GP.used_link_entries(pb.objects, links.parse_link_table(pb.link_table_raw)[1])[-1][1]
        == "\\\\%Description%"
    )
    pd.save()
    names = _names(_read(pd.path))
    assert "\\\\Fx Start" in names and "\\\\%Description%" not in names


def test_the_saved_table_takes_the_records_first_use_order(fx_install):
    """A label an update added at the end goes after its genie's entry, as CtDraw's save
    rebuilds the table; an entry no record gives keeps its place."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP

    doc = EditableDocument.open(fx_install.page("FxReturned"))
    doc.instantiate_placeholder_genies()
    objs = doc.parsed.page.objects
    ents = [
        (b"\x01" * 16, "fxback.fx_value.<ctm#ed02da80"),
        (b"\x02" * 16, "fxback.fx_plain.<ctm#ed02da80"),
        (bytes(16), "lib.attached@SUPER.<ctm#00000001"),
        (bytes(16), "\\\\Fx Brann 1"),
        (bytes(16), "\\\\Fx Brann 2"),
    ]
    got = [n for _g, n in GP.record_order_entries(objs, ents)]
    assert got == [
        "fxback.fx_value.<ctm#ed02da80",
        "\\\\Fx Brann 1",
        "lib.attached@SUPER.<ctm#00000001",
        "fxback.fx_plain.<ctm#ed02da80",
        "\\\\Fx Brann 2",
    ]
    assert GP.record_order_entries(objs, [ents[i] for i in (0, 3, 1, 4)]) == [ents[i] for i in (0, 3, 1, 4)]


# ------------------------------------------------------------ a shipped host's own table
def _shipped_host_with_old_nested_stamp(fx):
    """Include's fxprio.fx_p1 (a library supplied with Plant SCADA) whose own table quotes an
    older stamp of a symbol it nests (as xp_controls.Button quotes xp_buttons), placed on
    FxShapes."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import element as E, genie_norm as GN, genie_place as GP, writer

    lib = fx.file("Include", "fxprio.ctm")
    L = GP.open_library(lib)
    pb = L.element("fx_p1")
    blk = L._find_block("fx_p1")
    pb.link_table_raw, pb.link_count = GN.link_table_bytes(
        "    ", [(b"\x07" * 16, "fxinc.fx_icon.<ctl#00000001")]
    )
    E.store_element(
        lib, "fx_p1", writer.serialize_page_block(pb, blk.data)[: -writer.PAGE_TRAILING_PAD], newer=True
    )
    libraries_changed()
    pd = EditableDocument.open(fx.page("FxShapes"))
    pd.place_library_element("fxprio", "fx_p1", "ctm", (400, 400))
    pd.save()
    libraries_changed()
    return pd.path


def test_a_shipped_genies_older_nested_stamp_keeps_no_page_out_of_date(fx_install):
    from ctgedit.io import genieusage as GU, updatepages as U

    page = _shipped_host_with_old_nested_stamp(fx_install)
    assert not U.page_needs_update(page)
    (rec,) = U.update_pages(pages=[page]).records
    assert rec["outcome"] == "current"
    users = {
        u.ref.label: u for u in GU.element_users(fx_install.file("Include", "fxinc.ctl"), "fx_icon").users
    }
    assert users["fxprio.fx_p1"].shipped and users["fxprio.fx_p1"].status == GU.OUT_OF_DATE
    assert users["FxShapes"].status == GU.UP_TO_DATE


# ----------------------------------------------- element memos, labels and dummy markers
def test_the_element_memo_follows_a_placeholders_library_coming_back(fx_install):
    """A compose load of fx_row while fxnest.ctm is missing keeps the marker; once the file
    is back, the next load places n1, without libraries_changed."""
    import time
    from ctgedit.format import genie_place as GP

    lib = _row_library(fx_install)
    nest = fx_install.file("FxLib", "fxnest.ctm")
    folder = os.path.dirname(nest)
    aside = os.path.join(fx_install.root, "fxnest.ctm.aside")
    os.replace(nest, aside)
    old = time.time() - 600
    os.utime(folder, (old, old))  # (a listing the folder cache keeps)
    from ctgedit.editing import libraries_changed

    libraries_changed()

    def markers():
        el = GP.load_element(lib, "fx_row", compose=True)
        return sum(1 for po in el.records if GP.placeholder_genie(po) is not None)

    assert markers() == 1
    os.replace(aside, nest)
    os.utime(folder, (old + 60, old + 60))
    assert markers() == 0


def _labels(pb):
    return [n for n in _names(pb) if n.startswith("\\")]


def test_one_label_per_native_whatever_its_case(fx_install):
    """CtDraw lists one label per native, spelled as the first text in record order spells
    it: the page's save, a placement and an element's save alike."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    doc = _blank_page(fx_install, "FxCase")
    doc.create_object("text", (20, 20, 200, 36), text="@(Fx Case)")
    doc.create_object("text", (20, 60, 200, 76), text="@(fx case)")
    doc.save()
    assert _labels(_read(doc.path)) == ["\\\\Fx Case"]
    doc.place_library_element("fxgenie", "fx_value", "ctm", (400, 400), params={"Description": "FX CASE"})
    assert _labels(doc.parsed.page) == ["\\\\Fx Case"]
    doc.save()
    assert _labels(_read(doc.path)) == ["\\\\Fx Case"]
    objs = doc.parsed.page.objects
    ents = [(bytes(16), "\\\\FX CASE")]
    assert GP.add_record_labels(ents, objs) == ents
    lib = os.path.join(fx_install.project("FxLib"), "mylib.ctm")
    h = ElementDocument.new_element(lib, "cc", "genie")
    h.create_object("text", (0, 0, 100, 16), text="@(Fx Case)")
    h.create_object("text", (0, 30, 100, 46), text="@(FX case)")
    h.save()
    libraries_changed()
    assert _labels(_element(lib, "cc").page) == ["\\\\Fx Case"]


def test_the_save_lists_the_labels_its_records_give(fx_install):
    """CtDraw's save rebuilds the table from the records (LIVE: its load + save of FxAnim,
    whose texts' labels the fixture's table never listed, lists them). A new text's label,
    and a symbol update's new text, are listed at the save."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.io import updatepages as U

    d = EditableDocument.open(fx_install.page("FxAnim"))
    d.save()
    from ctgedit.format.page import parse_ctg_bytes

    want = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxAnim", "page.ctg")).page
    got = _read(d.path)
    assert got.link_table_raw == want.link_table_raw and got.link_count == want.link_count
    assert _labels(got) == ["\\\\Fx Start", "\\\\Fx Stop", "\\\\Fx Mode"]
    d = EditableDocument.open(fx_install.page("FxShapes"))
    d.create_object("text", (20, 600, 200, 616), text="@(Fx New)")
    d.save()
    assert "\\\\Fx New" in _labels(_read(d.path))
    # a group symbol whose element gained a text: Update Pages re-composes it and lists it
    d.place_library_element("fxsym", "fx_group", "ctl", (100, 500))
    d.save()
    libraries_changed()
    lib = fx_install.file("FxLib", "fxsym.ctl")
    s = ElementDocument.open_element(lib, "fx_group")
    s.create_object("text", (0, 0, 80, 16), text="@(Fx Sym New)")
    s.save()
    libraries_changed()
    (rec,) = U.update_pages(pages=[d.path]).records
    assert rec["outcome"] == "updated" and rec["symbols"] == 1
    names = _names(_read(d.path))
    k = next(i for i, n in enumerate(names) if n.startswith("fxsym.fx_group.<ctl#"))
    assert names[k + 1] == "\\\\Fx Sym New"


def test_a_dummy_marker_is_dynamic_only_through_its_container(fx_install):
    """A DummyMarker is no point class: CtDraw sets dwFlags 0x10 on a symbol set's state
    (through its set) and on a member of an animated group, not on a free marker or a
    placeholder genie's (CtDraw's own pages here)."""
    import copy
    from ctgedit.format import colourstate as CS, genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    pages = [
        parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/" + nm, "page.ctg")).page
        for nm in ("FxMissing", "FxPlaceholder", "FxParts", "FxTplMarker")
    ]
    pages.append(_page("chain5", "element_placeholder", "page_placed"))
    seen = 0
    for pb in pages:
        objs = pb.objects
        for i, po in enumerate(objs):
            if po.type_code != 0xFFFF:
                continue
            seen += 1
            again = [copy.copy(x) for x in objs]
            assert CS.derive_dynamic_flags(again, i) == [], (i, hex(po.subtype_size))
    placed = pages[-1].objects
    k = next(i for i, po in enumerate(placed) if GP.placeholder_genie(po) is not None)
    assert placed[k].subtype_size == 0x8 and not CS.is_dynamic(placed[k])
    assert seen >= 10
