"""Link table stamps of copies made outside a save."""

import os
import shutil

from ctgedit.format import geometry as geom
from ctgedit.format import links, writer

VALUE = ("fxgenie", "fx_value")
BOX = ("fxsym", "fx_box")
ON = ("fxsym", "fx_on")


def _entries(raw, count=1):
    t = links.parse_link_table(raw) if (raw and count) else None
    return [(g, n) for g, n in (t[1] if t else []) if not n.startswith("\\")]


def _entry(raw, key, count=1):
    want = "%s.%s.<" % key
    got = [e for e in _entries(raw, count) if e[1].lower().startswith(want)]
    assert len(got) == 1, got
    return got[0]


def _element_entry(lib_path, element, key):
    from ctgedit.format import genie_place as GP

    got = [e for e in GP.element_table_entries(lib_path, element) if e[1].lower().startswith("%s.%s.<" % key)]
    assert len(got) == 1, got
    return got[0]


def _current(lib_path, key):
    from ctgedit.format import genie_place as GP

    return GP.current_link_entry(lib_path, *key)


def _rects(objs):
    return [tuple(geom.norm_rect(geom.parse_geom_header(o.body_raw).rect1)) for o in objs]


def _grow(lib_path, element, dw, dh):
    """Make the last record of a symbol ``dw`` x ``dh`` larger and save the symbol."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib_path, element)
    k = len(d.parsed.page.objects) - 1
    r = geom.norm_rect(geom.parse_geom_header(d.parsed.page.objects[k].body_raw).rect1)
    d.set_object_rect(k, (r[0], r[1], r[2] + dw, r[3] + dh))
    d.save()
    libraries_changed()


def _value_text(lib_path, text):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib_path, "fx_value")
    t = next(i for i, o in enumerate(d.page.objects) if o.parent is None and d.objects[i].type_code == 0x05)
    d.set_text(t, "%Description% " + text)
    d.save()
    libraries_changed()


def _add_object(lib_path, element, text):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib_path, element)
    d.create_object("text", (10, 60, 120, 76), text=text)
    d.save()
    libraries_changed()


def _marks(objs, mark: bytes) -> int:
    return sum(mark in writer.object_payload(o) for o in objs)


# ------------------------------------------------------------------- unlinked copies
def test_an_unlinked_paste_of_a_stale_symbol_quotes_the_versions_it_holds(fx_install):
    """fxmore.fx_symsym nests fx_box; fx_box grows, fx_symsym is not saved again. Its
    unlinked paste holds the old 40 x 30 box: the page quotes fx_symsym's own entry for
    fx_box, so it is out of date for it and an update derives the box again."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages

    sym = fx_install.file("FxLib", "fxsym.ctl")
    more = fx_install.file("FxLib", "fxmore.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    n0 = len(pd.parsed.page.objects)
    pd.place_library_element("fxmore", "fx_symsym", "ctl", (300, 300), linked=False)
    pb = pd.parsed.page
    assert _entry(pb.link_table_raw, BOX, pb.link_count) == _current(sym, BOX)  # current
    pd.undo()
    _grow(sym, "fx_box", 40, 30)
    pd.place_library_element("fxmore", "fx_symsym", "ctl", (300, 300), linked=False)
    copy_ = pd.parsed.page.objects[n0:]
    assert _rects(copy_) == [(300, 300, 340, 330)] * 3
    pb = pd.parsed.page
    assert (
        _entry(pb.link_table_raw, BOX, pb.link_count)
        == _element_entry(more, "fx_symsym", BOX)
        != _current(sym, BOX)
    )
    assert BOX in pd.stale_symbol_elements()
    pd.save()
    assert updatepages.page_needs_update(pd.path)
    pd.update_symbol_placements({BOX})
    assert _rects(pd.parsed.page.objects[n0:]) == [(300, 300, 380, 360)] * 3
    assert BOX not in pd.stale_symbol_elements()


def test_cut_link_of_a_placed_stale_symbol_quotes_the_versions_it_holds(fx_install):
    """Edit > Cut Link on a placed fx_symsym after fx_box grew: the copy is fx_symsym's
    stored one, so the page is out of date for fx_box."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pd = EditableDocument.open(fx_install.page("FxShapes"))
    root = len(pd.parsed.page.objects)
    pd.place_library_element("fxmore", "fx_symsym", "ctl", (300, 300))
    pd.save()
    libraries_changed()
    _grow(fx_install.file("FxLib", "fxsym.ctl"), "fx_box", 40, 30)
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    assert BOX not in pd.stale_symbol_elements()
    pd.break_library_link(root)
    assert _rects([pd.parsed.page.objects[k] for k in pd.subtree(root)]) == [(300, 300, 340, 330)] * 3
    assert BOX in pd.stale_symbol_elements()
    pd.update_symbol_placements({BOX})
    assert _rects([pd.parsed.page.objects[k] for k in pd.subtree(root)]) == [(300, 300, 380, 360)] * 3


def test_an_unlinked_paste_takes_the_current_stamp_of_a_genie_its_load_composed(fx_install):
    """A symbol that nests fx_value (Copy to Library of an instance into a .ctl): after a
    text change of fx_value its unlinked paste shows the change (the load merges a copy
    that lines up), and quotes fx_value's current stamp; after an object is added to
    fx_value the copy no longer lines up, keeps its stored records and the symbol's entry."""
    from ctgedit.editing import EditableDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    more = fx_install.file("FxLib", "fxmore.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    n0 = len(pd.parsed.page.objects)
    pd.place_library_element(*VALUE, "ctm", (100, 500), {"Description": "S"})
    assert pd.copy_to_library([n0], more, "symgen")
    libraries_changed()
    _value_text(lib, "EDITED")
    p2 = EditableDocument.open(fx_install.page("FxArrange"))
    k = len(p2.parsed.page.objects)
    p2.place_library_element("fxmore", "symgen", "ctl", (300, 300), linked=False)
    assert _marks(p2.parsed.page.objects[k:], b"EDITED") == 1
    pb = p2.parsed.page
    assert _entry(pb.link_table_raw, VALUE, pb.link_count) == _current(lib, VALUE)
    assert p2.genie_changes() == {}
    _add_object(lib, "fx_value", "NEWOBJ")
    p3 = EditableDocument.open(fx_install.page("FxArrange"))
    k = len(p3.parsed.page.objects)
    p3.place_library_element("fxmore", "symgen", "ctl", (300, 300), linked=False)
    assert _marks(p3.parsed.page.objects[k:], b"NEWOBJ") == 0
    pb = p3.parsed.page
    assert _entry(pb.link_table_raw, VALUE, pb.link_count) == _element_entry(more, "symgen", VALUE)
    assert p3.genie_changes() == {VALUE: None}


def test_an_unlinked_new_page_quotes_the_templates_versions(fx_install):
    """File > New page, unlinked, from fx_style normal@XGA after fx_value changed, and
    from fx_alt sets@-XGA after fx_on grew: the copies are the template's stored ones (the
    old text, the 16 x 16 fx_on), so the new page is out of date for them."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages

    _value_text(fx_install.file("FxLib", "fxgenie.ctm"), "EDITED")
    _grow(fx_install.file("FxLib", "fxsym.ctl"), "fx_on", 20, 20)
    main = fx_install.project("FxMain")
    pd = EditableDocument.new_from_template(
        "fx_style.normal@XGA.<ctt", os.path.join(main, "NewNormal.ctg"), linked=False
    )
    assert pd.genie_changes() == {VALUE: None}
    assert all(
        _marks([pd.objects[j] for j in pd.subtree(i)], b"EDITED") == 0
        for i, key in pd.genie_instances()
        if key == VALUE
    )
    assert updatepages.page_needs_update(pd.path)
    pd = EditableDocument.new_from_template(
        "fx_alt.sets@-XGA.<ctt", os.path.join(main, "NewSets.ctg"), linked=False
    )
    assert ON in pd.stale_symbol_elements()
    on = [i for i, key, _kind in pd.symbol_placements() if key == ON]
    assert [_rects([pd.parsed.page.objects[i]]) for i in on] == [[(904, 20, 920, 36)]]
    pd.update_symbol_placements({ON})
    assert ON not in pd.stale_symbol_elements()


def test_cut_link_to_the_template_quotes_the_templates_versions(fx_install):
    """Cut Link to Template on FxTplSets after fx_on grew, on a page that also places fx_on
    at its current version: the cut copy is the template's 16 x 16 one, so the page keeps
    the template's (older) entry for fx_on and stays out of date."""
    from ctgedit.editing import EditableDocument

    sym = fx_install.file("FxLib", "fxsym.ctl")
    _grow(sym, "fx_on", 20, 20)
    pd = EditableDocument.open(fx_install.page("FxTplSets"))
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 300))
    pb = pd.parsed.page
    assert _entry(pb.link_table_raw, ON, pb.link_count) == _current(sym, ON)
    assert ON not in pd.stale_symbol_elements()
    pd.cut_template_link()
    pb = pd.parsed.page
    got = _entry(pb.link_table_raw, ON, pb.link_count)
    want = _element_entry(fx_install.file("FxStyle", "fx_alt.ctt"), "sets@-XGA", ON)
    assert got == want != _current(sym, ON)
    assert ON in pd.stale_symbol_elements()
    pd.update_symbol_placements({ON})
    assert ON not in pd.stale_symbol_elements()


def test_cut_link_to_the_template_takes_the_current_stamp_of_a_genie_it_composed(fx_install):
    """FxTplLinked's template places fx_value (FxTplMenu); the cut composes it again (its
    values move). After a change of fx_value the composed copy is the current version, so
    the page quotes fx_value's current stamp and is not out of date for it."""
    from ctgedit.editing import EditableDocument

    lib = fx_install.file("FxLib", "fxgenie.ctm")
    _value_text(lib, "EDITED")
    pd = EditableDocument.open(fx_install.page("FxTplLinked"))
    pd.cut_template_link()
    pb = pd.parsed.page
    assert _entry(pb.link_table_raw, VALUE, pb.link_count) == _current(lib, VALUE)
    assert pd.genie_changes() == {}
    inst = [i for i, key in pd.genie_instances() if key == VALUE]
    assert inst and all(_marks([pd.objects[j] for j in pd.subtree(i)], b"EDITED") == 1 for i in inst)


# --------------------------------------------------------------------- new elements
def test_copy_to_library_derives_a_stale_symbol_again(fx_install):
    """A page tab places fx_on; fx_on grows and is saved; Copy to Library of the stale
    placement (into a symbol and into a genie): the new element holds the current 36 x 36
    fx_on and quotes its current stamp."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    sym = fx_install.file("FxLib", "fxsym.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 500))
    si = [i for i, key, _kind in pd.symbol_placements() if key == ON][-1]
    pd.save()
    libraries_changed()
    _grow(sym, "fx_on", 20, 20)
    assert ON in pd.stale_symbol_elements()
    for name, lib in (
        ("hosts", os.path.join(fx_install.project("FxLib"), "mysym.ctl")),
        ("hostg", os.path.join(fx_install.project("FxLib"), "mygen.ctm")),
    ):
        assert pd.copy_to_library([si], lib, name)
        libraries_changed()
        h = ElementDocument.open_element(lib, name)
        assert _rects(h.parsed.page.objects) == [(300, 500, 336, 536)] * 2, name
        assert _element_entry(lib, name, ON) == _current(sym, ON), name
        assert h.stale_symbol_elements() == set(), name


def test_copy_to_library_keeps_the_old_stamp_of_a_symbol_it_cannot_derive(fx_install, monkeypatch):
    """When the stale symbol cannot be derived again the new element keeps the page's
    older entry for it, so it reads as out of date and is updated later."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    sym = fx_install.file("FxLib", "fxsym.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 500))
    si = [i for i, key, _kind in pd.symbol_placements() if key == ON][-1]
    pd.save()
    libraries_changed()
    old = _entry(pd.parsed.page.link_table_raw, ON, pd.parsed.page.link_count)
    _grow(sym, "fx_on", 20, 20)
    monkeypatch.setattr(GP, "refresh_instance", lambda *a, **k: None)
    lib = os.path.join(fx_install.project("FxLib"), "mysym.ctl")
    assert pd.copy_to_library([si], lib, "hosts")
    libraries_changed()
    assert _element_entry(lib, "hosts", ON) == old
    assert ON in ElementDocument.open_element(lib, "hosts").stale_symbol_elements()


def test_copy_to_library_composes_a_structurally_stale_genie(fx_install):
    """A page tab places fxnest.n0; an object is added to n0; Copy to Library of the stale
    instance: the new genie holds the object (the copy is composed again structurally, as
    Compose Nested Genies Again does) under n0's current stamp."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    nest = fx_install.file("FxLib", "fxnest.ctm")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    gi = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n0", "ctm", (100, 500), {})
    pd.save()
    libraries_changed()
    _add_object(nest, "n0", "NEWOBJ")
    assert pd.genie_changes() == {("fxnest", "n0"): None}
    lib = os.path.join(fx_install.project("FxLib"), "mylib.ctm")
    assert pd.copy_to_library([gi], lib, "h")
    libraries_changed()
    h = ElementDocument.open_element(lib, "h")
    assert _marks(h.parsed.page.objects, b"NEWOBJ") == 1
    assert _element_entry(lib, "h", ("fxnest", "n0")) == _current(nest, ("fxnest", "n0"))
    assert h.nested_genie_changes() == frozenset()


def _thumb(lib_path, element):
    from ctgedit.format import genie_place as GP

    return bytes(GP.open_library(lib_path).element(element).thumbnail or b"")


def test_copy_to_library_draws_the_thumbnail_of_a_copy_it_brought_up_to_date(fx_install):
    """The page's picture of the selection is the element's thumbnail when the copy is
    current; when Copy to Library brings an out-of-date copy up to date (fx_on derived
    again at 36 x 36, n0 composed with its new object) the thumbnail is drawn from the
    element as stored (``thumbnail_for``), not left showing the old version."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import thumbnail as T

    sym = fx_install.file("FxLib", "fxsym.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    gi = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n0", "ctm", (100, 500), {})
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 500))
    si = [i for i, key, _kind in pd.symbol_placements() if key == ON][-1]
    pd.save()
    libraries_changed()
    page_pic = T.encode(bytes(4 * 4), 2, 2)
    drawn = []

    def draw(doc):
        objs = doc.parsed.page.objects
        drawn.append((_rects(objs), _marks(objs, b"NEWOBJ")))
        return T.encode(b"\xff" * 16, 2, 2)

    lib = os.path.join(fx_install.project("FxLib"), "mysym.ctl")
    assert pd.copy_to_library([si], lib, "now", thumbnail=page_pic, thumbnail_for=draw)
    libraries_changed()
    assert drawn == [] and _thumb(lib, "now") == page_pic  # (current: the page's)
    _grow(sym, "fx_on", 20, 20)
    assert pd.copy_to_library([si], lib, "grown", thumbnail=page_pic, thumbnail_for=draw)
    libraries_changed()
    assert drawn == [([(300, 500, 336, 536)] * 2, 0)]
    assert _thumb(lib, "grown") == T.encode(b"\xff" * 16, 2, 2)
    assert _element_entry(lib, "grown", ON) == _current(sym, ON)
    _add_object(fx_install.file("FxLib", "fxnest.ctm"), "n0", "NEWOBJ")
    glib = os.path.join(fx_install.project("FxLib"), "mylib.ctm")
    drawn.clear()
    assert pd.copy_to_library([gi], glib, "h", thumbnail=page_pic, thumbnail_for=draw)
    libraries_changed()
    assert len(drawn) == 1 and drawn[0][1] == 1  # (drawn with the object)
    assert _thumb(glib, "h") == T.encode(b"\xff" * 16, 2, 2)


def test_an_element_save_draws_the_thumbnail_of_a_copy_it_brought_up_to_date(fx_install):
    """An element save draws the thumbnail of a copy it updated."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import thumbnail as T

    sym = fx_install.file("FxLib", "fxsym.ctl")
    more = fx_install.file("FxLib", "fxmore.ctl")
    pic, new_pic = T.encode(bytes(16), 2, 2), T.encode(b"\xff" * 16, 2, 2)
    drawn = []

    def draw(doc):
        drawn.append({r[2] - r[0] for r in _rects(doc.parsed.page.objects)})
        return new_pic

    d = ElementDocument.open_element(more, "fx_symsym")
    d.create_object("rectangle", (0, 0, 4, 4))
    d.save(thumbnail=pic, thumbnail_for=draw)
    libraries_changed()
    assert drawn == [] and _thumb(more, "fx_symsym") == pic
    _grow(sym, "fx_box", 40, 30)
    d = ElementDocument.open_element(more, "fx_symsym")
    d.create_object("rectangle", (0, 0, 6, 6))
    d.save(thumbnail=pic, thumbnail_for=draw)
    libraries_changed()
    assert len(drawn) == 1 and 80 in drawn[0] and 40 not in drawn[0]
    assert _thumb(more, "fx_symsym") == new_pic
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 500))
    si = [i for i, key, _kind in pd.symbol_placements() if key == ON][-1]
    pd.save()
    libraries_changed()
    payload = pd.copy_payload([si])
    _grow(sym, "fx_on", 20, 20)
    ns = ElementDocument.new_element(more, "brandsym", "symbol")
    ns.paste_payload(payload)
    drawn.clear()
    ns.save(thumbnail=pic, thumbnail_for=draw)
    libraries_changed()
    assert drawn == [{36}] and _thumb(more, "brandsym") == new_pic


def test_the_window_draws_copy_to_library_thumbnails_from_the_element(fx_install, monkeypatch):
    """Edit > Copy to Library and an element tab's save hand the element renderer to the
    document, so a copy they bring up to date gets a thumbnail of the new version."""
    from PySide6 import QtWidgets
    from ctgedit.render import headless_thumb
    from ctgedit.ui.dialog import library_dialogs
    from ctgedit.ui.main_window import MainWindow

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    lib = os.path.join(fx_install.project("FxLib"), "mysym.ctl")

    class _Pick:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def values(self):
            return lib, "picked"

    monkeypatch.setattr(library_dialogs, "CopyToLibraryDialog", _Pick)
    w = MainWindow()
    try:
        w.open_document(fx_install.page("FxShapes"))
        seen = {}

        def copy(indices, lib_path, element, thumbnail=b"", thumbnail_for=None):
            seen.update(element=element, thumbnail_for=thumbnail_for)
            return True

        monkeypatch.setattr(w.doc, "copy_to_library", copy)
        monkeypatch.setattr(w, "_confirm_shipped_template", lambda *a: True)
        monkeypatch.setattr(w, "_after_library_write", lambda *a: None)
        w._sel = [0]
        w._copy_to_library()
        assert seen == {"element": "picked", "thumbnail_for": headless_thumb.element_thumbnail}
        w._open_element(fx_install.file("FxLib", "fxmore.ctl"), "fx_symsym")
        saved = {}

        def save(path=None, name=None, **k):
            saved.update(k)
            return w.doc.path

        monkeypatch.setattr(w.doc, "save", save)
        assert w._save_element_to()
        assert saved["thumbnail_for"] is headless_thumb.element_thumbnail
        assert saved["thumbnail"]  # (the tab's own picture too)
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()
        w.deleteLater()


def test_a_composed_genie_does_not_lift_the_carried_entry_of_a_symbol(fx_install):
    """genie_place.page_link_entries: ``fresh`` names GENIES composed on the way; a linked
    symbol whose (library, element) is the same keeps the carried entry (a .ctl library of
    a genie library's name holds its own element of that name)."""
    from ctgedit.format import factory
    from ctgedit.format import genie_place as GP
    from ctgedit.format import placement
    from ctgedit.format.symbols import SymbolResolver

    sym = fx_install.file("FxLib", "fxsym.ctl")
    res = SymbolResolver.for_page(fx_install.page("FxShapes"))
    info = placement.element_info(res, "fxsym", "fx_on", "ctl")
    po = factory.create_object(
        "symbol", (0, 0, 16, 16), symbol=("fxsym", "fx_on", "ctl"), handle=7, link_info=info
    )
    old = (b"\x05" * 16, "fxsym.fx_on.<ctl#00000001")
    got = [
        e
        for e in GP.page_link_entries([po], res, carried=[old], fresh=frozenset({ON}))
        if not e[1].startswith("\\")
    ]
    assert got == [old]
    got = [e for e in GP.page_link_entries([po], res) if not e[1].startswith("\\")]
    assert got == [_current(sym, ON)]


def test_a_new_elements_first_save_composes_and_derives_a_pasted_stale_copy(fx_install):
    """File > New Genie / Symbol, a stale copy pasted, the first save: a structurally stale
    n0 instance is composed again, a stale fx_on placement derived again, and both are
    quoted at their current stamps."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    nest = fx_install.file("FxLib", "fxnest.ctm")
    sym = fx_install.file("FxLib", "fxsym.ctl")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    gi = len(pd.parsed.page.objects)
    pd.place_library_element("fxnest", "n0", "ctm", (100, 500), {})
    pd.place_library_element("fxsym", "fx_on", "ctl", (300, 500))
    si = [i for i, key, _kind in pd.symbol_placements() if key == ON][-1]
    pd.save()
    libraries_changed()
    genie = pd.copy_payload([gi])
    symbol = pd.copy_payload([si])
    _add_object(nest, "n0", "NEWOBJ")
    _grow(sym, "fx_on", 20, 20)
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    ne = ElementDocument.new_element(lib, "brandnew", "genie")
    ne.paste_payload(genie)
    ne.save()
    libraries_changed()
    h = ElementDocument.open_element(lib, "brandnew")
    assert _marks(h.parsed.page.objects, b"NEWOBJ") == 1
    assert _element_entry(lib, "brandnew", ("fxnest", "n0")) == _current(nest, ("fxnest", "n0"))
    more = fx_install.file("FxLib", "fxmore.ctl")
    ns = ElementDocument.new_element(more, "brandsym", "symbol")
    ns.paste_payload(symbol)
    assert ON in ns.stale_symbol_elements()
    ns.save()
    libraries_changed()
    h = ElementDocument.open_element(more, "brandsym")
    assert [r[2] - r[0] for r in _rects(h.parsed.page.objects)] == [36, 36]
    assert _element_entry(more, "brandsym", ON) == _current(sym, ON)
    assert h.stale_symbol_elements() == set()


def test_a_new_element_keeps_the_carried_stamp_of_a_genie_it_cannot_compose(fx_install, monkeypatch):
    """element.new_element_content: a nested genie copy whose composition cannot line it
    up (normalise_records reports it) keeps the entry the copy carries; a symbol always
    keeps its carried entry (a new element never derives one)."""
    from ctgedit.format import element as E
    from ctgedit.format import genie_norm as GN

    carried = [
        (b"\x01" * 16, "fxnest.n0.<ctm#00000010"),
        (b"\x02" * 16, "fxsym.fx_on.<ctl#00000020"),
        (b"\x00" * 16, "\\\\label"),
        (bytes(16), "fxgenie.x@SUPER#00000030"),
    ]
    assert E.carried_import_entries(carried) == carried[:2]
    assert E.carried_keys(carried) == {("fxsym", "fx_on")}
    assert E.carried_keys(carried, genies=True) == {("fxsym", "fx_on"), ("fxnest", "n0")}
    seen = {}
    real = GN.rebuild_link_table

    def spy(tree, old, resolver=None, instantiated=(), keep_stale=(), **kw):
        seen["old"], seen["keep"] = list(old), set(keep_stale)
        return real(tree, old, resolver, instantiated=instantiated, keep_stale=keep_stale, **kw)

    real_norm = GN.normalise_records

    def norm(*a, **k):
        out = real_norm(*a, **k)
        seen["nested_only"] = k.get("nested_only")
        if k.get("report") is not None:
            k["report"]["stale_nested"] = frozenset({("fxnest", "n0")})
        return out

    monkeypatch.setattr(GN, "rebuild_link_table", spy)
    monkeypatch.setattr(GN, "normalise_records", norm)
    from ctgedit.format import factory

    po = factory.create_object("rectangle", (0, 0, 20, 10), handle=1)
    E.new_element_content([po], ext="ctm", carried=carried)
    assert seen["nested_only"] == frozenset()  # every nested copy (none here)
    assert seen["old"] == carried[:2]
    assert seen["keep"] == {("fxsym", "fx_on"), ("fxnest", "n0")}


# ------------------------------------------------------------- paste between projects
def test_a_paste_from_another_projects_library_keeps_the_page_out_of_date(fx_install):
    """A paste from another project's library leaves the page out of date."""
    from ctgedit.editing import EditableDocument, libraries_changed

    other = fx_install.project("FxOther")
    shutil.copyfile(fx_install.file("FxLib", "fxgenie.ctm"), os.path.join(other, "fxgenie.ctm"))
    libraries_changed()
    pm = EditableDocument.open(fx_install.page("FxShapes"))
    pm.place_library_element(*VALUE, "ctm", (100, 500), {"Description": "M"})
    i = [k for k, key in pm.genie_instances() if key == VALUE][-1]
    same = pm.copy_payload([i])
    po = EditableDocument.open(os.path.join(other, "FxShapes.ctg"))
    po.place_library_element(*VALUE, "ctm", (100, 400), {"Description": "O"})
    po.paste_payload(same)
    assert po.genie_changes() == {}  # the same version: current
    _value_text(fx_install.file("FxLib", "fxgenie.ctm"), "EDITED")
    pm = EditableDocument.open(fx_install.page("FxShapes"))
    pm.place_library_element(*VALUE, "ctm", (100, 500), {"Description": "M"})
    i = [k for k, key in pm.genie_instances() if key == VALUE][-1]
    payload = pm.copy_payload([i])
    for place_first in (True, False):
        po = EditableDocument.open(os.path.join(other, "FxShapes.ctg"))
        if place_first:
            po.place_library_element(*VALUE, "ctm", (100, 400), {"Description": "O"})
            assert po.genie_changes() == {}
        po.paste_payload(payload)
        assert po.genie_changes() == {VALUE: None}, place_first
        po.update_genie_instances({VALUE})
        assert po.genie_changes() == {}
        assert all(
            _marks([po.objects[j] for j in po.subtree(k)], b"EDITED") == 0
            for k, key in po.genie_instances()
            if key == VALUE
        )


def test_carried_entry_wins_compares_versions_of_one_element():
    """genie_place.carried_entry_wins: with the current entry known, a listed current
    entry gives way to any other version, a newer-numbered one too; a listed entry that is
    not current keeps the older stamp; without it the older stamp stays."""
    from ctgedit.format import genie_place as GP

    cur = (b"\x01" * 16, "lib.elem.<ctm#00000010")
    older = (b"\x02" * 16, "lib.elem.<ctm#00000008")
    foreign = (b"\x03" * 16, "lib.elem.<ctm#00000020")
    assert GP.carried_entry_wins(cur, older, cur)
    assert GP.carried_entry_wins(cur, foreign, cur)
    assert not GP.carried_entry_wins(cur, foreign)  # (by number: newer)
    assert not GP.carried_entry_wins(cur, cur, cur)
    assert not GP.carried_entry_wins(older, cur, cur)  # out of date already
    assert not GP.carried_entry_wins(older, foreign, cur)
    assert GP.carried_entry_wins(foreign, older, cur)  # the older stays
    assert GP.same_version(cur, (cur[0], "LIB.Elem.<ctm#00000010"))
    assert not GP.same_version(cur, (b"\x09" * 16, cur[1]))


def test_a_genie_window_splits_an_attached_super_genie_at_the_last_dot(fx_install, monkeypatch):
    """The Metadata rows of a genie window look its attached super genies up with the
    library split at the LAST dot (a library file named 'plant.v2')."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import supergenie as SG

    d = ElementDocument.open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    monkeypatch.setattr(d, "attachments", lambda: ["plant.v2.pop"])
    seen = []
    monkeypatch.setattr(SG, "resolve", lambda res, lib, el: seen.append((lib, el)))
    assert d.super_genie_associations(0) == [("pop", [])]
    assert seen == [("plant.v2", "pop@SUPER")]
