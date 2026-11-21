"""Library elements as documents, and placing them."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import fxinstall

# the fake install's libraries (tools/make_fixtures.py; the catalogue).
#: The genie library is FxLib's fxgenie.ctm: fx_valve declares only TagPrefix (placed it is
#: 3 records: the genie and its 2 members), fx_value's text is '@(%Description%)', fx_pump's
#: rectangle owns a touch command FxToggle(%Tag%). FxMain's FxGenies places fx_value once
#: (FxValue); FxShapes holds top-level texts (FxText, FxMultiLine, FxNumber, FxCp1252).
GENIE_LIB = "fxgenie"
COMMITTED = os.path.join(fxinstall.FX_INSTALL, "User")

#: every library unit of the oracle's 'elements' step: (unit, library extension)
ORACLE_LIBRARIES = (
    "FxInclude/cicode.ctl",
    "FxInclude/fxicons16.ctl",
    "FxInclude/fxinc.ctl",
    "FxInclude/fxprio.ctl",
    "FxInclude/fxprio.ctm",
    "FxInclude/icons.ctl",
    "FxInclude/internal.ctm",
    "FxInclude/lights.ctl",
    "FxInclude/lights_square_small.ctl",
    "FxInclude/thumbs.ctl",
    "FxStyle/fx_alt.ctt",
    "FxStyle/fx_hd.ctt",
    "FxStyle/fx_sa.ctt",
    "FxStyle/fx_style.ctt",
    "FxLib/fxgenie.ctm",
    "FxLib/fxmany.ctl",
    "FxLib/fxmore.ctl",
    "FxLib/fxnest.ctm",
    "FxLib/fxparts.ctm",
    "FxLib/fxsuper.ctm",
    "FxLib/fxsym.ctl",
    "FxMain/fxback.ctm",
    "FxOther/fxsym.ctl",
    "FxSa/fxws.ctm",
)


@pytest.fixture()
def libdir(fx_install):
    """FxLib of the fake install's copy (fxgenie.ctm, fxsym.ctl ...): edits land in the
    copy, the committed fixture stays as it is."""
    return fx_install.project("FxLib")


@pytest.fixture()
def genies_page(fx_install):
    """FxMain's FxGenies in the copy: it includes FxLib, so fxgenie and fxsym resolve."""
    return fx_install.page("FxGenies")


def _check_library_round_trip(path):
    """The patching writer reproduces the file byte for byte (it keeps orphan gaps /
    stale slack a re-tiling writer would zero), and the editing session (root unwrapped,
    trailer as attributes) folds back to the stored content unchanged. Returns the number
    of elements checked."""
    from ctgedit.format.element import LibraryFile, open_element, serialize_element, element_content
    from ctgedit.format.element_session import open_element_session, session_to_content

    name = os.path.basename(path)
    raw = open(path, "rb").read()
    assert LibraryFile(raw).to_bytes() == raw
    n = 0
    for el in LibraryFile(raw).element_names():
        pe = open_element(path, el)
        assert serialize_element(pe) == raw, (name, el)
        sess, parsed = open_element_session(path, el)
        assert session_to_content(sess, parsed) == element_content(pe), (name, el)
        n += 1
    return n


def test_fixture_library_elements_round_trip_byte_exact():
    """Every element of every library in the project folders of the fake install (the
    committed fixture, read only): 129 elements in 24 libraries as tools/make_fixtures.py
    writes them (the composition caches below FxMain are not walked)."""
    n = libs = 0
    for proj in sorted(os.listdir(COMMITTED)):
        folder = os.path.join(COMMITTED, proj)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith((".ctl", ".ctm", ".ctt")):
                continue
            n += _check_library_round_trip(os.path.join(folder, name))
            libs += 1
    assert libs >= 24 and n >= 129  # (more when the fixtures grow)


@pytest.mark.golden
@pytest.mark.parametrize("unit", ORACLE_LIBRARIES)
def test_ctdraw_library_elements_round_trip_byte_exact(unit):
    """The same over each library as CtDraw left it after opening and saving every element
    (the oracle's elements step): its slack and gaps, not ours."""
    path = fxinstall.oracle("elements", unit, "library" + os.path.splitext(unit)[1])
    assert _check_library_round_trip(path) >= 1


def test_element_open_edit_save_is_a_fixed_point(libdir):
    from ctgedit.editing import ElementDocument
    from ctgedit.format.symbols import open_library

    lib = os.path.join(libdir, "fxgenie.ctm")
    raw = open(lib, "rb").read()
    d = ElementDocument.open_element(lib, "fx_valve")
    assert d.kind == "genie" and d.display_name == "fxgenie.fx_valve"
    assert d.session.root is not None  # the root group is unwrapped
    assert d.page.name == "fx_valve"
    d.save()
    assert open(lib, "rb").read() == raw  # unedited save = no-op
    roots = [i for i, o in enumerate(d.page.objects) if o.parent is None]
    d.move_objects(d.subtree(roots[0]), 10, 5)
    assert d.dirty
    d.save()
    assert not d.dirty
    after = open(lib, "rb").read()
    L = open_library(lib)
    assert all(L.element(n) is not None for n in L.element_names())
    # our own output is engine-normal: a re-open + save changes nothing
    d2 = ElementDocument.open_element(lib, "fx_valve")
    d2.save()
    assert open(lib, "rb").read() == after
    # only THIS element's block changed; every sibling is byte-identical
    L0 = open_library(os.path.join(COMMITTED, "FxLib", "fxgenie.ctm"))
    for n in L.element_names():
        if n != "fx_valve":
            assert L.container.block(n).data == L0.container.block(n).data, n


def test_genie_element_edits_the_template_bag(libdir):
    """In a genie element a member's properties are its %TEMPLATE% bag; a new
    %param% appears in the paste parameters after the save."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format.genie_place import load_element, declared_params

    lib = os.path.join(libdir, "fxgenie.ctm")
    d = ElementDocument.open_element(lib, "fx_value")
    assert d.is_genie_element()
    i = next(k for k, o in enumerate(d.objects) if o.type_code == 0x05)
    assert d.object_properties(i)["TXT_OFF"] == "@(%Description%)"
    assert d.set_text(i, "%Label% units")
    assert d.object_properties(i)["TXT_OFF"] == "@(%Label% units)"  # stays wrapped
    d.save()
    params = declared_params(load_element(lib, "fx_value"))
    assert "Label" in params
    d.undo()
    assert d.object_properties(i)["TXT_OFF"] == "@(%Description%)"


def test_new_element_needs_an_object_then_saves_one_root(libdir):
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import open_element

    lib = os.path.join(libdir, "mine.ctl")
    d = ElementDocument.new_element(lib, "probe", "symbol")
    assert d.is_new and not os.path.exists(lib)
    with pytest.raises(ValueError):
        d.save()
    d.create_object("rectangle", (100, 100, 200, 150), colour=0x00FF00)
    d.create_object("text", (120, 130, 180, 146), text="Hello")
    d.save()
    assert not d.is_new
    pe = open_element(lib, "probe")
    assert pe.tree[0].type_code == 0x08 and pe.tree[0].class_flag == 2  # ONE root
    assert pe.root_end == 3 and len(pe.trailer.props) == 2  # + trailer
    assert pe.origin == (150, 125)  # hotspot = centre


def test_copy_to_library_writes_one_root_and_the_trailer(libdir, fx_page):
    """Copy to Library of several roots writes one group root and the trailer (CtDraw reads
    only the first of roots side by side). The three are FxShapes's FxText, FxMultiLine and
    FxNumber (top-level texts of one record each)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.element import open_element

    pd = EditableDocument.open(fx_page("FxShapes"))
    texts = [
        i for i, o in enumerate(pd.page.objects) if o.parent is None and pd.objects[i].type_code == 0x05
    ][:3]
    assert [pd._stored_name(i) for i in texts] == ["FxText", "FxMultiLine", "FxNumber"]
    lib = os.path.join(libdir, "mine.ctl")
    assert pd.copy_to_library(texts, lib, "three_texts")
    pe = open_element(lib, "three_texts")
    assert pe.tree[0].type_code == 0x08 and pe.tree[0].class_flag == 3
    assert pe.root_end == 4 and len(pe.page.objects) == 7


def _new_symbol(lib, name, build):
    from ctgedit.editing import ElementDocument

    d = ElementDocument.new_element(lib, name, "symbol")
    build(d)
    d.save()


def test_a_one_object_symbol_gets_the_group_root_ctdraw_saves(libdir):
    """LIVE (the oracle, CtDraw's open + save of fxsym.fx_box and the rest): CtDraw keeps a
    .ctl's single root bare only when it is a bitmap, a bar or a trend; any other
    one-object symbol is saved under a 0x08 group root: the member's rects, anchor (l, b - 1),
    the lowest free handle, slots (I, T(-e), I) over a member with identity slots."""
    import struct
    from ctgedit.format import placement as P, geometry as G
    from ctgedit.format.element import open_element, keeps_bare_root

    lib = os.path.join(libdir, "mine.ctl")
    _new_symbol(lib, "box", lambda d: d.create_object("rectangle", (0, 0, 40, 30)))
    root, member = open_element(lib, "box").tree
    assert (root.type_code, root.class_flag, member.type_code) == (0x08, 1, 0x0F)
    assert (G.get_handle(root), G.get_handle(member)) == (1, 0)
    gr, gm = G.parse_geom_header(root.body_raw), G.parse_geom_header(member.body_raw)
    assert (tuple(gr.rect1), tuple(gr.rect2)) == (tuple(gm.rect1), tuple(gm.rect2))
    assert struct.unpack_from("<2h", root.body_raw, gr.anchor_off) == (0, 29)
    assert [s[:2] for s in P.read_slots(root.body_raw)[0]] == [
        (0, P.IDENT),
        (1, (1.0, 0.0, 0.0, 1.0, -20.0, -15.0)),
        (0, P.IDENT),
    ]
    assert [s[:2] for s in P.read_slots(member.body_raw)[0]] == [(0, P.IDENT)] * 3
    # a symbol set as the one object: the set (with its own bag) is the member
    _new_symbol(
        lib,
        "set",
        lambda d: d.create_symbol_set(
            (0, 0), kind=0, exprs=["FX_PUMP1"], states=[("fxsym", "fx_off"), ("fxsym", "fx_on")]
        ),
    )
    tree = open_element(lib, "set").tree
    assert [o.type_code for o in tree[:2]] == [0x08, 0x0C] and tree[0].class_flag == 1
    assert "TXT_A" in tree[1].raw_properties() and not tree[0].raw_properties()
    # an unlinked bitmap stays the root
    _new_symbol(lib, "pic", lambda d: d.add_bitmap_object((0, 0, 4, 4), b"\x80" * 64, 4, 4))
    tree = open_element(lib, "pic").tree
    assert len(tree) == 1 and tree[0].type_code == 0x07 and keeps_bare_root(tree[0])
    # so does a placed bitmap symbol (tc 7 + LINK: shipped Include ICONS left7 / left72)
    _new_symbol(
        lib, "pic2", lambda d: d.create_object("symbol", (0, 0, 14, 23), symbol=("fxsym", "fx_bitmap", "ctl"))
    )
    tree = open_element(lib, "pic2").tree
    assert len(tree) == 1 and tree[0].type_code == 0x07 and tree[0].subtype_size & 0x1


@pytest.mark.golden
def test_new_one_object_symbols_are_ctdraws_saves(libdir):
    """LIVE (the oracle's open + save of FxLib fxsym.ctl): our new fx_box, fx_round, fx_on and
    fx_pump_tall, made as tools/make_fixtures.py makes them, are CtDraw's saves of them,
    record for record."""
    from ctgedit.format import writer
    from ctgedit.format.element import open_element

    want = fxinstall.oracle("elements", "FxLib/fxsym.ctl", "library.ctl")
    lib = os.path.join(libdir, "mine.ctl")
    shapes = {
        "fx_box": ("rectangle", (0, 0, 40, 30), 0x3080C0),
        "fx_round": ("ellipse", (0, 0, 30, 30), 0xC08030),
        "fx_on": ("ellipse", (0, 0, 16, 16), 0x00C000),
        "fx_pump_tall": ("rectangle", (0, 0, 30, 80), 0x5050A0),
    }
    for name, (kind, rect, colour) in shapes.items():
        _new_symbol(lib, name, lambda d, k=kind, r=rect, c=colour: d.create_object(k, r, colour=c))
        assert [writer.object_payload(o) for o in open_element(lib, name).tree] == [
            writer.object_payload(o) for o in open_element(want, name).tree
        ], name


def test_a_bare_one_object_symbol_is_wrapped_and_placed_as_a_forest(libdir, genies_page, monkeypatch):
    """A one-object symbol stored bare: CtDraw's load holds it under the
    group root (LIVE, the oracle's LibraryObjectPlaceEx of the bare fx_box: the instance
    forest 0x08 dwFlags 0x9 > 0x0F), so our placement is that forest, and its next edit
    saves the group root."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import element as E

    lib = os.path.join(libdir, "fxsym.ctl")
    with monkeypatch.context() as m:
        m.setattr(E, "keeps_bare_root", lambda po: True)
        _new_symbol(lib, "fx_bare", lambda d: d.create_object("ellipse", (0, 0, 20, 20)))
    libraries_changed()
    assert [o.type_code for o in E.open_element(lib, "fx_bare").tree] == [0x11]
    pd = EditableDocument.open(genies_page)
    n0 = len(pd.objects)
    i = pd.place_library_element("fxsym", "fx_bare", "ctl", (300, 200))
    placed = pd.objects[n0:]
    assert i == n0 and [o.type_code for o in placed] == [0x08, 0x11]
    assert placed[0].subtype_size & 0x1 and placed[0].class_flag == 1
    d = ElementDocument.open_element(lib, "fx_bare")
    d.move_objects([0], 2, 0)
    d.save()
    tree = E.open_element(lib, "fx_bare").tree
    assert [o.type_code for o in tree] == [0x08, 0x11] and tree[0].class_flag == 1


def test_a_symbol_drawn_to_a_box_is_the_instance_forest(genies_page, monkeypatch):
    """The Symbol tool drawn to a box (create_object 'symbol'): a group-root symbol is the
    instance forest a paste gives (6.12), sized to the box with its members, as one undo
    step; a bitmap symbol stays one linked tc 7 record."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G, writer

    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    pd = EditableDocument.open(genies_page)
    objs = pd.parsed.page.objects
    n0 = len(objs)
    pd.place_library_element("fxsym", "fx_group", "ctl", (20, 20))
    pasted = [writer.object_payload(o) for o in objs[n0:]]
    pd.undo()
    i = pd.create_object("symbol", (20, 20, 80, 60), symbol=("fxsym", "fx_group", "ctl"))
    assert i == n0 and objs[i].type_code == 0x08 and objs[i].subtype_size & 0x1
    assert [writer.object_payload(o) for o in objs[n0:]] == pasted
    pd.undo()
    assert len(objs) == n0
    i = pd.create_object("symbol", (100, 100, 164, 164), symbol=("fxsym", "fx_box", "ctl"))
    assert [o.type_code for o in objs[i:]] == [0x08, 0x0F]
    assert [tuple(G.parse_geom_header(o.body_raw).rect1) for o in objs[i:]] == [(100, 100, 164, 164)] * 2
    pd.undo()
    assert len(objs) == n0
    i = pd.create_object("symbol", (10, 10, 30, 40), symbol=("fxsym", "fx_bitmap", "ctl"))
    assert len(objs) == n0 + 1 and objs[i].type_code == 0x07 and objs[i].subtype_size & 0x1


def test_a_bitmap_symbol_grouped_in_its_window_gets_the_root_form(libdir):
    """A bitmap symbol's one root grouped with a new object in its window: the group is the
    new root, with the slots (I, T(-e), I), and the bitmap a member without its T(-e)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import placement as P
    from ctgedit.format.element import open_element

    lib = os.path.join(libdir, "fxsym.ctl")
    ex, ey = open_element(lib, "fx_bitmap").origin
    assert (ex, ey) != (0, 0)
    d = ElementDocument.open_element(lib, "fx_bitmap")
    d.create_object("rectangle", (0, 0, 10, 10))
    d.group_objects([0, 1])
    d.save()
    tree = open_element(lib, "fx_bitmap").tree
    assert [o.type_code for o in tree] == [0x08, 0x07, 0x0F]
    assert [s[:2] for s in P.read_slots(tree[0].body_raw)[0]] == [
        (0, P.IDENT),
        (1, (1.0, 0.0, 0.0, 1.0, float(-ex), float(-ey))),
        (0, P.IDENT),
    ]
    for po in tree[1:]:
        assert [s[:2] for s in P.read_slots(po.body_raw)[0]] == [(0, P.IDENT)] * 3


def test_place_genie_and_symbols(genies_page):
    """fxgenie.fx_valve placed is its root subtree: the genie and its two members."""
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(genies_page)
    n0 = len(pd.objects)
    lt0 = pd.parsed.page.link_table_raw
    i = pd.place_library_element(GENIE_LIB, "fx_valve", "ctm", (300, 200))
    g = pd.page.objects[i].geometry
    assert (g.x, g.y) == (300, 200)  # bbox top-left at the paste point
    assert len(pd.objects) - n0 == 3  # the element's root subtree
    i2 = pd.place_library_element(GENIE_LIB, "fx_value", "ctm", (400, 300), {"Description": "Fx level"})
    assert pd.genie_bindings(i2).get("Description") == "Fx level"
    i3 = pd.place_library_element("fxsym", "fx_box", "ctl", (500, 300))
    assert pd.objects[i3].subtype_size & 0x1  # a LINKED symbol
    i4 = pd.place_library_element("fxsym", "fx_box", "ctl", (560, 300), linked=False)
    assert pd.objects[i4].an_str == "-1"  # an embedded copy
    pd.save()
    pd2 = EditableDocument.open(pd.path)
    assert len(pd2.objects) == len(pd.objects)
    for _ in range(4):
        pd.undo()
    assert len(pd.objects) == n0 and pd.parsed.page.link_table_raw == lt0


def test_genie_parameters_are_the_paste_dialog_list(genies_page):
    from ctgedit.editing import EditableDocument

    pd = EditableDocument.open(genies_page)
    params = pd.genie_parameters(GENIE_LIB, "fx_valve")
    assert list(params) == ["TagPrefix"]


def test_empty_link_table_is_no_table():
    from ctgedit.format import links

    assert links.serialize_link_table("    ", []) == (b"", 0)


def test_main_window_opens_and_saves_an_element(libdir, genies_page):
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.editing import ElementDocument

    w = MainWindow()
    try:
        w.open_document(genies_page)
        lib = os.path.join(libdir, "fxgenie.ctm")
        w._open_element(lib, "fx_valve")
        assert isinstance(w.doc, ElementDocument)
        assert w.doc_tabs.count() == 2
        # (the tab shows the element's name, its kind icon and the full name in its tool
        # tip)
        assert w.doc_tabs.tabText(1) == "fx_valve"
        assert "fxgenie.fx_valve" in w.doc_tabs.tabToolTip(1)
        assert "fxgenie.fx_valve" in w.windowTitle()
        w._open_element(lib, "fx_valve")  # already open: switch
        assert w.doc_tabs.count() == 2
        roots = [i for i, o in enumerate(w.doc.page.objects) if o.parent is None]
        w.doc.move_objects(w.doc.subtree(roots[0]), 4, 0)
        w._exec_prompt = lambda box: None  # the Update Users offer
        assert w.save()
        assert not w.doc.dirty
        assert w._docs[0].get("stale")  # the page re-renders on activation
        w.doc_tabs.setCurrentIndex(0)
        assert not isinstance(w.doc, ElementDocument)
        assert not w._docs[0].get("stale")
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_an_embedded_symbol_has_identity_slots_and_its_native_rect(genies_page):
    """An embedded copy of a bitmap-root symbol (fxsym.fx_bitmap) keeps identity slots, and
    its rect1 is the native rect."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import placement as P, geometry as G

    pd = EditableDocument.open(genies_page)
    j = pd.place_library_element("fxsym", "fx_bitmap", "ctl", (560, 300), linked=False)
    po = pd.objects[j]
    assert [s[0] for s in P.read_slots(po.body_raw)[0]] == [0, 0, 0]  # identity slots
    assert tuple(P.native_rect(po)) == tuple(G.norm_rect(G.parse_geom_header(po.body_raw).rect1))


@pytest.mark.golden
def test_linked_symbol_uses_the_element_roots_own_frame(fx_install):
    """A linked symbol uses the element root's own frame."""
    import shutil
    from ctgedit.editing import EditableDocument
    from ctgedit.format import placement as P, geometry as G, writer
    from ctgedit.format.page import parse_ctg

    want_page = fxinstall.oracle("place", "linked_symbol", "page.ctg")
    blank = fxinstall.oracle("place", "linked_symbol", "input.ctg")
    dst = os.path.join(fx_install.project("FxMain"), "FxOraclePlace.ctg")
    shutil.copyfile(blank, dst)
    before = len(parse_ctg(dst).page.objects)
    pd = EditableDocument.open(dst)
    i = pd.place_library_element("fxsym", "fx_box", "ctl", (150, 150))
    want = parse_ctg(want_page).page.objects
    got = pd.parsed.page.objects
    assert i == before and len(got) == len(want)
    po, wo = got[i], want[before]
    assert [s[:2] for s in P.read_slots(po.body_raw)[0]] == [s[:2] for s in P.read_slots(wo.body_raw)[0]]
    assert tuple(G.parse_geom_header(po.body_raw).rect1) == tuple(G.parse_geom_header(wo.body_raw).rect1)
    assert [writer.object_payload(o) for o in got[before:]] == [
        writer.object_payload(o) for o in want[before:]
    ]


def test_a_stale_linked_symbol_uses_the_element_roots_own_frame():
    """A stale linked symbol uses the element root's own frame."""
    pytest.skip(
        "needs fixture: a stale linked-symbol element with a bitmap root whose "
        "head origin and rect1 disagree with its root's slot 2 and native rect "
        "(and the oracle's LibraryObjectPlaceEx + PageSave of it)"
    )


def test_element_labels_list_template_and_substituted_natives():
    """LIVE: an element text '@(%Label% units)' (default '') lists both
    '\\%Label% units' and '\\ units' in the import table."""
    from ctgedit.format import genie_place as GP

    b1 = GP.Bag([("TXT_OFF", "@( units)")])
    b2 = GP.Bag([("TXT_OFF", "@(%Label% units)")])

    class _R:
        entries = [[GP.Bag([]), b1, b2], [GP.Bag([]), GP.Bag([]), GP.Bag([])]]

    orig = GP.decode
    try:
        GP.decode = lambda po: _R

        class _Po:
            type_code, subtype_size = 0x05, 0x8

        got = [n for _g, n in GP.label_entries([_Po()], template=True)]
    finally:
        GP.decode = orig
    assert got == ["\\\\%Label% units", "\\\\ units"]


def test_saved_genie_updates_its_instances_on_open_pages(libdir, fx_page):
    """A genie edited + saved: an open page that places it re-composes those
    instances (as CtDraw does on page load) -- ONLY where composing from the
    previous version reproduced the instance, so nothing but the edit lands.
    The test places fxgenie.fx_value six times on FxShapes (which places no genie)."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import writer

    pd = EditableDocument.open(fx_page("FxShapes"))
    assert not list(pd.genie_instances())
    for k in range(6):
        pd.place_library_element(
            GENIE_LIB, "fx_value", "ctm", (100 + 130 * k, 500), {"Description": "Fx %d" % k}
        )
    before = [writer.object_payload(o) for o in pd.objects]
    d = ElementDocument.open_element(os.path.join(libdir, "fxgenie.ctm"), "fx_value")
    # (the element's text moves: only the texts and the genie groups may change)
    text = next(
        i for i, o in enumerate(d.page.objects) if o.parent is None and d.objects[i].type_code == 0x05
    )
    d.move_objects(d.subtree(text), 3, 2)
    d.save()
    key = (GENIE_LIB, "fx_value")
    n = pd.update_genie_instances({key}, {key: d.previous_element})
    assert n == 6 and pd.genie_update_skipped == 0
    changed = [
        i for i, (a, b) in enumerate(zip(before, (writer.object_payload(o) for o in pd.objects))) if a != b
    ]
    assert changed and all(pd.objects[i].type_code in (0x05, 0x08) for i in changed)
    pd.undo()
    assert [writer.object_payload(o) for o in pd.objects] == before
    # an UNEDITED library leaves every instance exactly as stored
    assert pd.update_genie_instances({key}, {key: d._snapshot_element()}) == 0


def _dummy_markers(path):
    """Re-compose every genie instance of the page that holds a DummyMarker (tc 0xFFFF):
    each marker comes back as stored. Returns [(its rect1, its Total's origin)]."""
    from ctgedit.format import geometry as G, genie_place as GP
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import SymbolResolver

    objs = parse_ctg(path).page.objects
    res = SymbolResolver.for_page(path)
    found = []
    for k in GP.instance_roots(objs):
        inst = objs[k : GP.subtree_end(objs, k)]
        if not any(p.type_code == 0xFFFF for p in inst):
            continue
        out = GP.refresh_instance(inst, res)
        assert out is not None and len(out) == len(inst)
        for a, b in zip(inst, out):
            if a.type_code == 0xFFFF:
                ga, gb = G.parse_geom_header(a.body_raw), G.parse_geom_header(b.body_raw)
                assert (ga.anchor, ga.rect1, ga.rect2) == (gb.anchor, gb.rect1, gb.rect2)
                m3, T3 = GP.decode(a).slots[2]
                found.append((tuple(ga.rect1), (int(T3[4]), int(T3[5])) if m3 else (0, 0)))
    return found


def _free_markers_at_the_origin(path):
    """FxParts' two copies of fxparts.fx_dummy: their four markers (0 x 1 and 1 x 1 in
    the element, free members with no point in slot 2) are each the empty rect at its
    Total's origin, the instance's top left (820, 200) and (920, 200)."""
    found = _dummy_markers(path)
    assert len(found) == 4
    assert {o for _r, o in found} == {(820, 200), (920, 200)}
    for rect, (x, y) in found:
        assert rect == (x, y, x, y)


def test_recomposed_free_dummy_markers_are_the_empty_rect_at_the_origin(fx_page):
    """A free DummyMarker (tc 0xFFFF; a genie member, not a symbol-set state) with no point
    in slot 2 re-composes as the empty rect at its Total's origin, as CtDraw's load writes
    it (the golden test below). (A set-state marker keeps its twin's size, 1 x 1 or 0 x 1.)"""
    _free_markers_at_the_origin(fx_page("FxParts"))


@pytest.mark.golden
def test_ctdraws_load_puts_the_free_dummy_markers_at_the_origin(fx_install):
    """CtDraw's load puts free dummy markers at the origin."""
    import shutil

    src = fxinstall.oracle("pages", "FxMain/FxParts", "page.ctg")
    dst = os.path.join(fx_install.project("FxMain"), "FxParts.ctg")
    shutil.copyfile(src, dst)
    _free_markers_at_the_origin(dst)


def test_a_genies_commands_show_keep_and_declare_their_parameters(libdir):
    """A genie's touch / keyboard commands are genie members: the Input pages show their
    %parameter% templates, an edit goes into the template, and a new parameter is declared.
    fxgenie.fx_pump: its rectangle owns the touch FxToggle(%Tag%)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import codes, genie_place as GP

    lib = os.path.join(libdir, "fxgenie.ctm")
    doc = ElementDocument.open_element(str(lib), "fx_pump")
    owner = next(
        doc.page.objects[i].parent
        for i, po in enumerate(doc.parsed.page.objects)
        if po.type_code in codes.INPUT_TCS
    )
    assert doc.input_owner_status(owner)[0]
    touch = doc.input_commands(owner)["touch"]
    assert touch["CMD"] == "FxToggle(%Tag%)"
    assert doc.set_input_commands(owner, touch=dict(touch)) is False  # nothing changed
    steps = len(doc._undo)
    touch["CMD"] += ";Beep(%NewP=1%)"
    assert doc.set_input_commands(owner, touch=touch)
    assert doc.set_input_commands(owner, keys=[{"KEY_SEQ": "F2", "CMD": "Beep(%KeyP%)"}])
    assert len(doc._undo) == steps + 2
    doc.save()
    again = ElementDocument.open_element(str(lib), "fx_pump")
    cmds = again.input_commands(owner)
    assert cmds["touch"]["CMD"].endswith(";Beep(%NewP=1%)")
    assert [k["CMD"] for k in cmds["keys"]] == ["Beep(%KeyP%)"]
    declared = GP.declared_params_of(str(lib), "fx_pump")
    assert declared["NewP"] == "1" and "KeyP" in declared
