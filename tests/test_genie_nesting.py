"""Genies nested five deep, pinned to what CtDraw writes."""

import os
import shutil

import fxinstall
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "nested_chain")
LIBNAME = "_nest3"
CHAIN = [
    # (element, its own text, the genie it nests, the bindings typed in the genie dialog)
    ("n0", "%Label%", None, {}),
    ("n1", "%Name%", "n0", {"Label": "%Name% L1"}),
    ("n2", "N2", "n1", {"Name": "%Name%"}),
    ("n3", "%Extra%", "n2", {"Name": "%Name% 3"}),
    ("n4", "N4", "n3", {"Name": "%Name%", "Extra": "E%Extra%"}),
]
PAGE_VALUES = {"Name": "Pump", "Extra": "X"}


def _records(lib_path, name):
    from ctgedit.format.element import open_element

    return [po for po in open_element(lib_path, name).page.objects if po.type_code < 0x2000]


def _bags(po):
    from ctgedit.format import genie_place as GP

    try:
        r = GP.decode(po)
    except Exception:  # noqa: BLE001 -- an element trailer record (no geometry)
        return None
    return [[dict(b.pairs) for b in lvl] for lvl in r.entries] if r.entries else None


def _same_but_root_pair(ours: bytes, eng: bytes) -> bool:
    """Equal, or different only in single size-word bytes CtDraw writes one higher (the
    root bag pair 12 for 11)."""
    if ours == eng:
        return True
    from tools.engine_roundtrip import field_diff

    d = field_diff(ours, eng)
    if set(d) != {"body_after_geom"}:
        return False
    ops = d["body_after_geom"].get("ops") or []
    return bool(ops) and all(
        op.get("op") == "replace"
        and len(op.get("ours", "")) == 2
        and len(op.get("eng", "")) == 2
        and int(op["eng"], 16) == int(op["ours"], 16) + 1
        for op in ops
    )


def _build(tmp_path, monkeypatch, libname: str = LIBNAME) -> str:
    """The chain authored in ctgedit in a library of its own (its folder is the library
    path, so each element finds the one it nests)."""
    from ctgedit.editing import ElementDocument

    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / (libname + ".ctm"))
    for name, own_text, inner, binds in CHAIN:
        d = ElementDocument.new_element(lib, name, "genie", canvas=(300, 200))
        t = d.create_object("text", rect=(10, 10, 120, 26), text=own_text)
        if libname == "_nest8":
            # the fixture's own texts have handle 20: the same input for the comparison
            from ctgedit.format import geometry as G

            G.set_handle(d.parsed.page.objects[t], 20)
            d._touch_model([t])
        if inner:
            assert d.place_library_element(libname, inner, "ctm", (10, 40), params=binds) is not None
        d.save()
    return lib


@pytest.fixture
def chain(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch)


def _unnumbered(po):
    """The record's payload with its handle and AN string blanked."""
    import copy
    from ctgedit.format import geometry as G, writer

    if po.raw_payload is not None or len(po.body_raw) < 4:
        return writer.object_payload(po)
    q = copy.copy(po)
    G.set_handle(q, 0)
    q.an_str = "-1"
    return writer.object_payload(q)


def test_authored_chain_is_ctdraws_form_record_for_record(chain):
    """Every record of every element = CtDraw's own save of it (the fixture), bytes (a root
    bag size word aside). The handles and AN strings aside too: the fixture is CtDraw's save of
    a chain numbered max + 1, which CtDraw's load keeps; ctgedit numbers new objects as
    CtDraw does (the next test)."""
    for name, _t, _i, _b in CHAIN:
        ours = [_unnumbered(po) for po in _records(chain, name)]
        eng = [_unnumbered(po) for po in _records(os.path.join(DATA, LIBNAME + ".ctm"), name)]
        assert len(ours) == len(eng), name
        for k, (a, b) in enumerate(zip(ours, eng)):
            assert _same_but_root_pair(a, b), "%s record %d differs from CtDraw's" % (name, k)


def test_ctdraws_own_authoring_has_the_same_bags(tmp_path, monkeypatch):
    """The chain CtDraw authored itself (LibraryObjectPlaceEx + LibraryObjectPutProperty, the
    fixture `_nest8`): every record's level bags equal ctgedit's, and so do the handles and AN
    strings (a paste takes each record's element handle where free, then the lowest free;
    a new object the lowest free: genie_place.paste_handles / alloc_handles)."""
    from ctgedit.format import geometry as G

    lib = _build(tmp_path, monkeypatch, "_nest8")
    for name, _t, _i, _b in CHAIN:
        ours = _records(lib, name)
        eng = _records(os.path.join(DATA, "_nest8.ctm"), name)
        assert len(ours) == len(eng), name
        assert [(G.get_handle(p), p.an_str) for p in ours] == [(G.get_handle(p), p.an_str) for p in eng], name
        for k, (a, b) in enumerate(zip(ours, eng)):
            assert _bags(a) == _bags(b), "%s record %d" % (name, k)
            assert (a.type_code, a.class_flag, a.subtype_size) == (
                b.type_code,
                b.class_flag,
                b.subtype_size,
            ), "%s record %d" % (name, k)


def test_bindings_are_templates_with_an_element_level(chain):
    """n1 places n0 with Label = '%Name% L1': the nested root's level holds the binding as
    a template, its value is the substitution with the unbound element (' L1'), and an
    element level follows; the member text reads '%Name% L1' with the element's Name
    declared on its element level."""
    recs = _records(chain, "n1")
    root = next(po for po in recs if po.class_flag and (po.subtype_size & 0x200))
    assert _bags(root) == [[{}, {}, {}], [{}, {"Label": " L1"}, {"Label": "%Name% L1"}], [{}, {}, {}]]
    text = recs[recs.index(root) + 1]
    assert _bags(text) == [
        [{}, {"TXT_OFF": "%Name% L1"}, {"TXT_OFF": "%Label%"}],
        [{"Label": "\n"}, {"Label": " L1"}, {"Label": "%Name% L1"}],
        [{"Name": "\n"}, {}, {}],
    ]


def test_no_double_substitution_three_levels_down(chain):
    """n3 binds Name = '%Name% 3' to n2, which binds Name = '%Name%' to n1, which binds
    Label = '%Name% L1' to n0: n0's text in n3 reads '%Name% 3 L1'."""
    recs = _records(chain, "n3")
    deepest = [po for po in recs if po.type_code == 0x05 and _bags(po) and len(_bags(po)) == 5]
    assert len(deepest) == 1
    lv = _bags(deepest[0])
    assert lv[0][1] == {"TXT_OFF": "%Name% 3 L1"}
    assert lv[1][1] == {"Label": "%Name% 3 L1"} and lv[2][1] == {"Name": "%Name% 3"}
    assert lv[3][1] == {"Name": " 3"} and lv[4] == [{"Name": "\n"}, {}, {}]


def test_the_outer_genie_declares_only_its_own_parameters(chain):
    """n4's dialog asks for Name and Extra, not for n0's Label or n1's bindings."""
    from ctgedit.format import genie_place as GP

    el = GP.load_element(chain, "n4")
    assert set(GP.declared_params(el)) == {"Name", "Extra"}
    el3 = GP.load_element(chain, "n3")
    assert set(GP.declared_params(el3)) == {"Name", "Extra"}
    el1 = GP.load_element(chain, "n1")
    assert set(GP.declared_params(el1)) == {"Name"}


def test_placed_chain_resolves_through_every_level_on_a_page(fx_page, chain, tmp_path):
    """A placed chain resolves through every level on a page."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    page = str(tmp_path / "!NEST_T3.ctg")
    doc = EditableDocument.new_blank(fx_page("FxShapes"), page, register=False)
    root = doc.place_library_element(LIBNAME, "n4", "ctm", (100, 100), params=PAGE_VALUES)
    assert root is not None
    texts = [(o.style.text or "") for o in doc.page.objects[root:] if o.kind.value == "text"]
    assert texts == ["N4", "EX", "N2", "Pump 3", "Pump 3 L1"]
    assert dict(doc.genie_all_params(root)) == PAGE_VALUES
    doc.save()
    ours = parse_ctg(page).page.objects
    for fixture in ("!NEST_T3.ctg", "!NEST_T8.ctg"):  # CtDraw's save, CtDraw's own placement
        eng = parse_ctg(os.path.join(DATA, fixture)).page.objects
        assert len(ours) == len(eng), fixture
        for k, (a, b) in enumerate(zip(ours, eng)):
            assert _bags(a) == _bags(b), "page record %d vs %s" % (k, fixture)
    # read back: the same texts and rows
    doc2 = EditableDocument.open(page)
    assert [(o.style.text or "") for o in doc2.page.objects if o.kind.value == "text"] == texts
    assert dict(doc2.genie_all_params(0)) == PAGE_VALUES


def test_ctdraws_chain_reads_the_same_in_ctgedit():
    """CtDraw's chain files read the same in ctgedit."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP

    for libname in (LIBNAME, "_nest8"):
        lib = os.path.join(DATA, libname + ".ctm")
        ed = ElementDocument.open_element(lib, "n4")
        texts = [(o.style.text or "") for o in ed.page.objects if o.kind.value == "text"]
        assert texts == ["N4", "E%Extra%", "N2", "%Name% 3", "%Name% 3 L1"], libname
        assert set(GP.declared_params(GP.load_element(lib, "n4"))) == {"Name", "Extra"}, libname
        ed1 = ElementDocument.open_element(lib, "n1")
        assert [(o.style.text or "") for o in ed1.page.objects if o.kind.value == "text"] == [
            "%Name%",
            "%Name% L1",
        ], libname


def _string_display_library(tmp_path, monkeypatch) -> str:
    """fx_ext.ctm in tmp_path: fx_inner = a string display TXT_STR '%Tag%'; fx_outer = a
    label, its own string display TXT_STR '%Tag%' and fx_inner placed with Tag = '%Tag%.V'."""
    from ctgedit.editing import ElementDocument

    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / "fx_ext.ctm")
    d = ElementDocument.new_element(lib, "fx_inner", "genie", canvas=(300, 200))
    t = d.create_object("text", rect=(10, 10, 120, 26), text="####")
    assert d.set_text_display(t, [("TXTMODE", "4"), ("TXT_STR", "%Tag%")])
    d.save()
    d = ElementDocument.new_element(lib, "fx_outer", "genie", canvas=(300, 200))
    d.create_object("text", rect=(10, 10, 120, 26), text="Fx block")
    t = d.create_object("text", rect=(10, 40, 120, 56), text="####")
    assert d.set_text_display(t, [("TXTMODE", "4"), ("TXT_STR", "%Tag%")])
    assert (
        d.place_library_element("fx_ext", "fx_inner", "ctm", (10, 70), params={"Tag": "%Tag%.V"}) is not None
    )
    d.save()
    return lib


def test_element_window_shows_a_string_displays_template(tmp_path, monkeypatch):
    """A genie (authored in tmp_path, :func:`_string_display_library`): its direct string
    display reads '=%Tag%' (the TXT_STR template, not the empty substitution) and the nested
    genie's reads '=%Tag%.V' (the binding chain)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.io.readerparts.common import derive_options

    lib = _string_display_library(tmp_path, monkeypatch)
    with derive_options(label_mode="literal"):
        ed = ElementDocument.open_element(lib, "fx_outer")
        texts = [(o.style.text or "") for o in ed.page.objects if o.kind.value == "text"]
    assert "=%Tag%" in texts and "=%Tag%.V" in texts, texts
    assert "=" not in [t for t in texts if t and t[0] != "="] and "" not in texts[:3]


def test_a_copy_of_ctdraws_chain_saved_by_ctgedit_stays_as_it_is(tmp_path, monkeypatch):
    """CtDraw's file opened and saved again by ctgedit (every element): ctgedit's own normal
    form keeps every record (the root bag size word aside)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import writer

    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    lib = str(tmp_path / (LIBNAME + ".ctm"))
    shutil.copyfile(os.path.join(DATA, LIBNAME + ".ctm"), lib)
    for name, _t, _i, _b in CHAIN:
        before = [writer.object_payload(po) for po in _records(lib, name)]
        ed = ElementDocument.open_element(lib, name)
        ed.save(refresh=True)
        after = [writer.object_payload(po) for po in _records(lib, name)]
        assert len(after) == len(before), name
        for k, (a, b) in enumerate(zip(after, before)):
            assert _same_but_root_pair(a, b), "%s record %d" % (name, k)


# ------------------------------------------------------------- the fixture chain (golden)
@pytest.mark.golden
def test_the_fixture_chain_is_ctdraws_form_record_for_record(fx_install):
    """FxLib/fxnest.ctm of the fake install (n0 .. n4, made by ctgedit like CHAIN): CtDraw's
    open + save of each element keeps every record (a root bag size word aside)."""
    eng_lib = fxinstall.oracle("elements", "FxLib/fxnest.ctm", "library.ctm")
    ours_lib = fx_install.file("FxLib", "fxnest.ctm")
    for name, _t, _i, _b in CHAIN:
        ours = [_unnumbered(po) for po in _records(ours_lib, name)]
        eng = [_unnumbered(po) for po in _records(eng_lib, name)]
        assert len(ours) == len(eng), name
        for k, (a, b) in enumerate(zip(ours, eng)):
            assert _same_but_root_pair(a, b), "%s record %d differs from CtDraw's" % (name, k)


@pytest.mark.golden
def test_the_fixture_placement_has_ctdraws_bags(fx_page):
    """FxNested on FxGenies (ctgedit's placement of fxnest.n4, Name Pump, Extra X): its
    records have the level bags of CtDraw's own placement of n4 with the same values and
    the same name (oracle place/nested_genie, which names it FxNested too) and of CtDraw's
    save of FxGenies (oracle pages)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    doc = EditableDocument.open(fx_page("FxGenies"))
    root = doc.find_objects_by_name("FxNested")[0]
    ours = [doc.parsed.page.objects[i] for i in doc.subtree(root)]
    texts = [
        (doc.page.objects[i].style.text or "")
        for i in doc.subtree(root)
        if doc.page.objects[i].kind.value == "text"
    ]
    assert texts == ["N4", "EX", "N2", "Pump 3", "Pump 3 L1"]

    levels = len(_bags(ours[0]))

    def genie_records(path, by_name):
        if by_name:  # FxGenies: many genies, find it
            od = EditableDocument.open(path)
            hits = od.find_objects_by_name("FxNested")
            assert len(hits) == 1, (path, hits)
            return [od.parsed.page.objects[i] for i in od.subtree(hits[0])]
        objs = parse_ctg(path).page.objects
        top = [
            j
            for j, po in enumerate(objs)
            if po.class_flag and (po.subtype_size & 0x200) and _bags(po) and len(_bags(po)) == levels
        ]
        assert len(top) == 1, (path, top)  # the place page holds the one genie
        return objs[top[0] : top[0] + len(ours)]

    for path, by_name in (
        (fxinstall.oracle("place", "nested_genie", "page.ctg"), False),
        (fxinstall.oracle("pages", "FxMain/FxGenies", "page.ctg"), True),
    ):
        eng = genie_records(path, by_name)
        assert len(eng) == len(ours), path
        for k, (a, b) in enumerate(zip(ours, eng)):
            assert _bags(a) == _bags(b), "record %d vs %s" % (k, path)
