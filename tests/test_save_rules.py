"""What CtDraw's save adds: name keys and button box sizes."""

import os
import shutil

import pytest

import fxinstall
from ctgedit.format import genie_place as GP
from ctgedit.format import writer
from ctgedit.format.page import parse_ctg


def _keys(po, b):
    return [k for k, _v in GP.decode(po).entries[0][b].pairs]


def _without_name_keys(po, *, lead=(), tail=(), template=None):
    """A copy of record ``po`` with no '__AN_NAME__' key in its bags; ``lead`` pairs go in
    front of its value bag and ``tail`` pairs after it, ``template`` (pairs) replaces its
    template bag."""
    r = GP.decode(po)
    e0 = r.entries[0]
    e0[1] = GP.Bag(list(lead) + [p for p in e0[1].pairs if p[0] != "__AN_NAME__"] + list(tail), e0[1].ov)
    if len(e0) > 2:
        pairs = list(template) if template is not None else e0[2].pairs
        e0[2] = GP.Bag([p for p in pairs if p[0] != "__AN_NAME__"], e0[2].ov)
    return GP.to_po(GP.encode(r))


def _named(objs):
    return [k for k, po in enumerate(objs) if po.raw_payload is None and GP.decode(po).name]


def _index_of(objs, name):
    found = [k for k in _named(objs) if GP.decode(objs[k]).name == name]
    assert len(found) == 1, (name, found)
    return found[0]


def test_a_named_record_gets_its_name_key_where_ctdraw_puts_it(fx_install):
    """A named record gets its name key where CtDraw puts it."""
    stored = parse_ctg(fx_install.page("FxAnim")).page.objects
    named = _named(stored)
    vertex = _index_of(stored, "FxVertex")
    assert stored[vertex].type_code == 0x10 and len(named) == 28
    src = list(stored)
    for k in named:
        if k == vertex:
            src[k] = _without_name_keys(
                stored[k],
                lead=[("DESC", "Fx vertex")],
                tail=[("VERTEX_VERTFROMX2", "0")],
                template=[("FX_TPL", "1")],
            )
        else:
            src[k] = _without_name_keys(stored[k])
    assert all(GP.decode(src[k]).entries[0][1].get("__AN_NAME__") is None for k in named)
    new = GP.with_name_keys(src)
    assert [k for k, (a, b) in enumerate(zip(src, new)) if a is not b] == named
    assert _keys(new[vertex], 1) == ["DESC", "VERTEX_VERTEXP1", "__AN_NAME__", "VERTEX_VERTFROMX2"]
    assert _keys(new[vertex], 2) == ["FX_TPL", "__AN_NAME__"]
    for k in named:
        r = GP.decode(new[k])
        assert r.entries[0][1].get("__AN_NAME__") == r.name
        if k != vertex:  # (no trailing vertex keys: the key goes last)
            assert _keys(new[k], 1) == _keys(src[k], 1) + ["__AN_NAME__"], r.name
    # a page that has them is left as it is (every fixture page; every CtDraw-saved page)
    assert GP.with_name_keys(new) is new
    assert GP.with_name_keys(stored) is stored


@pytest.mark.golden
def test_ctdraws_name_key_order_of_an_arrow_and_a_member_button(fx_install):
    """CtDraw's name key order for an arrow and a member button."""
    from ctgedit.format.page import parse_ctg_bytes

    src = parse_ctg(fx_install.page("FxNoAnNames")).page.objects
    named = _named(src)
    assert len(named) == 5
    assert all(GP.decode(src[k]).entries[0][1].get("__AN_NAME__") is None for k in named)
    ours = GP.with_name_keys(src)
    arrow, button = _index_of(ours, "FxArrow"), _index_of(ours, "FxTipButton")
    assert _keys(ours[arrow], 1)[0] == "DESC"
    assert _keys(ours[arrow], 1)[-2:] == ["VERTEX_VERTEXP2", "__AN_NAME__"]
    assert _keys(ours[button], 1)[-2:] == ["__AN_NAME__", "COMMENT"]
    theirs = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxNoAnNames", "page.ctg")).page.objects
    for k in named:
        name = GP.decode(ours[k]).name
        t = _index_of(theirs, name)
        bags = len(GP.decode(ours[k]).entries[0])
        assert bags == len(GP.decode(theirs[t]).entries[0]), name
        for b in range(1, bags):
            assert _keys(ours[k], b) == _keys(theirs[t], b), (name, b)


def test_the_name_key_goes_after_the_vertices_with_an_expression():
    """The name key goes after the vertex keys when there's an expression."""

    def off(n, x="0"):
        return [("VERTEX_VERT%s%d" % (f, n), x) for f in ("FROMX", "FROMY", "TOX", "TOY")]

    at = GP._name_key_at
    assert at([("DESC", "d")], False) == 1
    assert at([("DESC", "d")] + off(0) + off(1), False) == 1
    assert at([("DESC", "d")] + off(0) + [("VERTEX_VERTEXP0", "")], False) == 1
    assert at([("DESC", "d")] + off(0, "5") + [("VERTEX_VERTEXP0", "FX_LEVEL1")], False) == 6
    assert at([("DESC", "d")] + off(0) + [("VERTEX_VERTEXP0", "FX_LEVEL1")] + off(1), False) == 6
    member = [("TEXT", "Tip")] + off(0) + [("VERTEX_VERTEXP0", "FX_LEVEL1"), ("COMMENT", "c")]
    assert at(member, True) == 6
    assert at(member, False) == 7
    assert at([("TEXT", "Tip"), ("COMMENT", "c")] + off(1), True) == 1
    assert at([("TEXT", "Tip"), ("COMMENT", "c")] + off(1), False) == 2


@pytest.mark.golden
def test_a_value_bag_key_leaves_the_template_bag_as_it_is():
    """A value bag key leaves the template bag alone."""
    from ctgedit.format.page import parse_ctg_bytes

    for name in ("page.ctg", "reopened.ctg"):
        objs = parse_ctg_bytes(fxinstall.oracle_bytes("newpage", "cut_link", name)).page.objects
        menu = _index_of(objs, "FxTplMenu")
        e0 = GP.decode(objs[menu]).entries
        assert len(e0) == 2 and e0[0][1].get("__AN_NAME__") == "FxTplMenu", name
        assert e0[0][2].pairs == [], name
        assert GP.with_name_keys(objs) is objs, name
    src = list(objs)
    src[menu] = _without_name_keys(objs[menu])
    new = GP.with_name_keys(src)
    assert _keys(new[menu], 1) == ["__AN_NAME__"] and _keys(new[menu], 2) == ["__AN_NAME__"]


def test_a_save_writes_the_name_keys_and_the_document_keeps_its_records(fx_install):
    """A copy of FxShapes whose named records lack the key (written by the test): the save
    writes the keys, the open document's records stay as they were."""
    from ctgedit.editing import EditableDocument

    p = parse_ctg(fx_install.page("FxShapes"))
    named = _named(p.page.objects)
    assert len(named) == 26
    for k in named:
        p.page.objects[k] = _without_name_keys(p.page.objects[k])
    dst = os.path.join(fx_install.project("FxMain"), "FxNoNameKeys.ctg")
    with open(dst, "wb") as fh:
        fh.write(writer.serialize_ctg(p.container, p.page, p.attributes))
    doc = EditableDocument.open(dst)
    before = [writer.object_payload(po) for po in doc.parsed.page.objects]
    assert GP.decode(doc.parsed.page.objects[named[0]]).entries[0][1].get("__AN_NAME__") is None
    doc.save()
    assert [writer.object_payload(po) for po in doc.parsed.page.objects] == before
    saved = parse_ctg(dst).page.objects
    for k in named:
        r = GP.decode(saved[k])
        assert r.entries[0][1].get("__AN_NAME__") == r.name


def test_a_member_buttons_size_is_its_box():
    """WIDTH / HEIGHT follow the box in both bags (LIVE: a 139-pixel copy of a 43-pixel base
    button keeps 139)."""
    r = GP.Rec.__new__(GP.Rec)
    r.tc = GP.BUTTON_TC
    r.rect1 = (949, 463, 1088, 506)
    r.entries = [
        [
            GP.Bag([]),
            GP.Bag([("WIDTH", "43"), ("HEIGHT", "43"), ("NAME", "Target")], 7),
            GP.Bag([("WIDTH", "43"), ("HEIGHT", "43")]),
        ]
    ]
    assert GP._button_box_size(r)
    assert r.entries[0][1].pairs == [("WIDTH", "139"), ("HEIGHT", "43"), ("NAME", "Target")]
    assert r.entries[0][1].ov == 7
    assert r.entries[0][2].pairs == [("WIDTH", "139"), ("HEIGHT", "43")]
    assert not GP._button_box_size(r)


def test_an_index_made_with_the_case_flag_orders_keys_with_case(tmp_path):
    """pglang under [LANGUAGE] CaseSensitive: the index has the case flag
    (0x800), whose keys compare byte for byte, not without case."""
    from ctgedit.format.dbf import Ndx, create_ndx

    keys = ["b", "B", "a", "A", "c"]
    got = {}
    for case in (False, True):
        p = str(tmp_path / ("x%d.NDX" % case))
        create_ndx(p, "NATIVE", 8)
        with Ndx(p, case=case) as nx:
            nx.insert_many((k, i + 1) for i, k in enumerate(keys))
            got[case] = [(k.rstrip(b" ").decode(), r) for k, r in nx.entries()]
            assert nx.find("a") == ([3] if case else [3, 4])
    # without case: a / A, b / B tie and keep record order; with case: capitals first
    assert got[False] == [("a", 3), ("A", 4), ("b", 1), ("B", 2), ("c", 5)]
    assert got[True] == [("A", 4), ("B", 2), ("a", 3), ("b", 1), ("c", 5)]


CHAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nested_chain", "_nest3.ctm")


def test_an_element_save_keeps_an_unchanged_nested_copy_as_stored(tmp_path, monkeypatch):
    """CtDraw's element load re-composes only the nested genies whose import table stamps
    changed; a copy of an unchanged genie keeps its bags as stored ('!*' sheet keys
    included) when the element is edited and saved (LIVE)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E
    from ctgedit.format.element import open_element

    lib = str(tmp_path / "_nest3.ctm")
    shutil.copyfile(CHAIN, lib)
    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    pe = open_element(lib, "n1")
    recs = list(pe.page.objects)
    k = next(
        j for j, po in enumerate(recs) if j and po.type_code == 0x05 and len(GP.decode(po).entries or ()) >= 3
    )  # the nested copy's text
    r = GP.decode(recs[k])
    r.entries[0][1] = GP.Bag(list(r.entries[0][1].pairs) + [("!*#1403", "#1404")], r.entries[0][1].ov)
    recs[k] = GP.to_po(GP.encode(r))
    pe.page.objects[: len(recs)] = recs
    open(lib, "wb").write(E.serialize_element(pe, E.element_content(pe)))
    doc = ElementDocument.open_element(lib, "n1")
    own = next(
        j
        for j, po in enumerate(doc.parsed.page.objects)
        if po.type_code == 0x05
        and not po.subtype_size & 0x200
        and GP.decode(po).entries
        and "%Name%" in str(GP.decode(po).entries[0][2].pairs)
    )
    doc.move_objects([own], 4, 0)  # (the element's own text)
    doc.save()
    after = GP.decode(open_element(lib, "n1").page.objects[k])
    assert after.entries[0][1].get("!*#1403") == "#1404"


def test_a_merged_copy_member_keeps_its_own_local(tmp_path, monkeypatch):
    """A nested genie that changed is merged into its copy; a member that has a Local of
    its own keeps it, and the copy's root bounds the moved member (LIVE: a changed alarm
    genie's state set kept T(2, 0))."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import element as E
    from ctgedit.format import placement as P
    from ctgedit.format.element import open_element

    lib = str(tmp_path / "_nest3.ctm")
    shutil.copyfile(CHAIN, lib)
    monkeypatch.setenv("CTG_LIB_PATH", str(tmp_path))
    # n0 changes (its stamp with it): its text moves 3 pixels down
    d0 = ElementDocument.open_element(lib, "n0")
    t0 = next(j for j, po in enumerate(d0.parsed.page.objects) if po.type_code == 0x05)
    d0.move_objects([t0], 0, 3)
    d0.save()
    # n1's copy of n0: its text gets a Local of its own, T(2, 0)
    pe = open_element(lib, "n1")
    recs = list(pe.page.objects)
    k = next(
        j for j, po in enumerate(recs) if j and po.type_code == 0x05 and len(GP.decode(po).entries or ()) >= 3
    )
    root = max(j for j in range(k) if recs[j].class_flag and recs[j].subtype_size & 0x200)
    r = GP.decode(recs[k])
    m3, t3 = r.slots[2]
    r.slots = [
        r.slots[0],
        (1, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0)),
        (1, P.affine_mul(t3 if m3 else P.IDENT, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0))),
    ]
    l, t, rr, b = r.rect1
    r.rect1, r.rect2 = (l + 2, t, rr + 2, b), (r.rect2[0] + 2, r.rect2[1], r.rect2[2] + 2, r.rect2[3])
    r.anchor = (r.anchor[0] + 2, r.anchor[1])
    recs[k] = GP.to_po(GP.encode(r))
    pe.page.objects[: len(recs)] = recs
    open(lib, "wb").write(E.serialize_element(pe, E.element_content(pe)))
    doc = ElementDocument.open_element(lib, "n1")
    assert ("_nest3", "n0") in (doc._changed_nested(doc._resolver()) or ())
    own = next(
        j
        for j, po in enumerate(doc.parsed.page.objects)
        if po.type_code == 0x05
        and GP.decode(po).entries
        and "%Name%" in str(GP.decode(po).entries[0][2].pairs)
    )
    doc.move_objects([own], 4, 0)
    doc.save()
    out = open_element(lib, "n1").page.objects
    a = GP.decode(out[k])
    assert a.slots[1] == (1, (1.0, 0.0, 0.0, 1.0, 2.0, 0.0))
    assert GP.decode(out[root]).rect1[2] >= a.rect1[2]


def test_a_worker_save_writes_a_fresh_thumbnail(fx_install, monkeypatch):
    """A page saved by a worker process (Update Pages, Replace) gets a thumbnail of its
    content as it is now, as CtDraw's save does (render.headless_thumb). A copy of FxShapes
    (stored without a thumbnail, as the generator saves)."""
    from ctgedit.doc import history
    from ctgedit.editing import EditableDocument
    from ctgedit.format import thumbnail as T
    from ctgedit.render import headless_thumb

    monkeypatch.setattr(history, "SAVE_THUMBNAIL", headless_thumb.page_thumbnail)
    dst = os.path.join(fx_install.project("FxMain"), "FxThumbSave.ctg")
    shutil.copyfile(fx_install.page("FxShapes"), dst)
    doc = EditableDocument.open(dst)
    before = doc.parsed.page.thumbnail
    doc.create_object("rectangle", rect=(10, 10, 400, 300))
    doc.save()
    after = parse_ctg(dst).page.thumbnail
    assert after and after != before
    from ctgedit.format.pagesummary import read_page_summary

    w, h = T.thumb_size(doc.parsed.page.page_width, doc.parsed.page.page_height)
    assert read_page_summary(dst).thumbnail_size == (w, h)


def _wmf(*records):
    """A memory Windows Metafile of ``records`` ((function, params) in their stored order)."""
    import struct as S

    body = b"".join(
        S.pack("<IH", 3 + len(p), fn) + S.pack("<%dh" % len(p), *p) for fn, p in records
    ) + S.pack("<IH", 3, 0)
    words = 9 + len(body) // 2
    return S.pack("<HHHIHIH", 1, 9, 0x300, words, 0, 8, 0) + body


@pytest.mark.skipif(os.name != "nt", reason="GDI")
def test_a_metafile_bakes_into_the_fast_display_as_ctdraw_draws_it():
    """CtDraw's draw: MM_ANISOTROPIC, the viewport at the record's extent less one
    pixel each way, the metafile played (its window set by its own records). LIVE:
    LIVE: baked backgrounds equal CtDraw's .ctF pixel for pixel."""
    from ctgedit.format import ctf as CTF

    api = CTF._GdiApi.get()
    wmf = _wmf((0x020C, (10, 10)), (0x041B, (10, 10, 0, 0)))  # window 10 x 10, a rectangle
    dib = CTF._Dib(api, 100, 100, 0x0000FF)
    try:
        # (the metafile of the record's in-memory appearance: ctf._metafile_bits)
        assert CTF._draw_metafile(api, dib.dc, wmf, (20, 30, 61, 71))
        px = dib.pixels()
    finally:
        dib.close()

    def at(x, y):
        return px[4 * (y * 100 + x) : 4 * (y * 100 + x) + 3]

    assert at(20, 50) == b"\0\0\0"  # the left edge, black
    assert at(40, 50) == b"\xff\xff\xff"  # the fill, white
    assert at(10, 50) == b"\xff\0\0"  # the page colour (B, G, R)
    assert at(61, 50) == b"\xff\0\0"  # past the extent less one


def _copy(fx_install, page, name):
    dst = os.path.join(fx_install.project("FxMain"), name + ".ctg")
    shutil.copyfile(fx_install.page(page), dst)
    return dst


@pytest.mark.parametrize("turn,want", [(True, 4), (False, 3)])
def test_a_mirror_turns_a_gradient_only_when_asked(fx_install, monkeypatch, turn, want):
    """The Graphics Builder's vertical mirror keeps a rectangle's gradient mode 3 (LIVE);
    ctgedit turns it to 4 unless Settings > Editing says not to. FxShapes'
    FxGradient has gradient mode 3."""
    from ctgedit.doc.geometry import GeometryEdits
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    monkeypatch.setattr(GeometryEdits, "TURN_GRADIENTS", turn)
    doc = EditableDocument.open(_copy(fx_install, "FxShapes", "FxMirror"))
    found = doc.find_objects_by_name("FxGradient")
    assert len(found) == 1
    k = found[0]
    assert G.parse_shape_tail(doc.parsed.page.objects[k])["grad_mode"] == 3
    doc.mirror_objects([k], horizontal=False)
    assert G.parse_shape_tail(doc.parsed.page.objects[k])["grad_mode"] == want


def _turn_state(doc, names):
    """Each record of the named objects: (name, offset in its subtree, type, anchor, rect1,
    rect2)."""
    objs = doc.parsed.page.objects
    out = []
    for n in names:
        found = doc.find_objects_by_name(n)
        assert len(found) == 1, (n, found)
        for k in doc.subtree(found[0]):
            po = objs[k]
            if po.raw_payload is not None:
                continue
            r = GP.decode(po)
            out.append((n, k - found[0], po.type_code, r.anchor, r.rect1, r.rect2))
    return out


@pytest.mark.golden
def test_a_quarter_turn_about_a_half_pixel_centre_truncates_like_ctdraw(fx_install):
    """A quarter turn about a half-pixel centre truncates like CtDraw."""
    from ctgedit.editing import EditableDocument

    names = ["FxPointGenie", "FxLoneText"]
    want_src = fxinstall.oracle("turns", "T03_point_text_turn", "page.ctg")
    want_dst = os.path.join(fx_install.project("FxMain"), "FxOracleT03.ctg")
    shutil.copyfile(want_src, want_dst)
    want = _turn_state(EditableDocument.open(want_dst), names)
    doc = EditableDocument.open(_copy(fx_install, "FxArrange", "FxTurn"))
    roots = [doc.find_objects_by_name(n)[0] for n in names]
    doc.rotate_objects(roots, clockwise=True)
    assert _turn_state(doc, names) == want


def test_text_the_code_page_cannot_hold_is_refused_or_stored_as_question_marks(monkeypatch):
    """Refused by default (the characters named); with the setting, stored as '?' as the
    Graphics Builder and the Project Editor do."""
    from ctgedit.editing_base import UnencodableTextError, guard_text
    from ctgedit.format import textcodec as TC

    text = "Pump ☃ 1"  # a snowman no ANSI code page holds
    monkeypatch.delenv("CTG_TEXT_REPLACE", raising=False)
    with pytest.raises(UnencodableTextError):
        guard_text(text)
    with pytest.raises(UnencodableTextError):
        TC.encode_field(text, "a test field")
    monkeypatch.setenv("CTG_TEXT_REPLACE", "1")
    guard_text(text)
    assert TC.encode_field(text, "a test field") == b"Pump ? 1"


# --------------------------------------------------------------------------- 2026-10-09
def test_the_factory_takes_handle_zero():
    """Handle 0 is a handle: the element window's lowest free one is often 0 (its root
    group is not in it), so each new object of a template window takes its own handle."""
    from ctgedit.format import factory, geometry as G

    assert G.get_handle(factory.create_object("rectangle", (10, 10, 50, 40), handle=0)) == 0


def test_members_of_a_group_given_a_touch_command_turn_dynamic(fx_install):
    """A group that owns a touch record is dynamic, and its members inherit the bit (CtDraw
    asks the parent group): LIVE, CtDraw saved a template group's two
    rectangles 0x1a after PropertiesInputTouchPut on the group. Undo restores them. The
    host page is a copy of FxShapes."""
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(_copy(fx_install, "FxShapes", "FxGroupTouch"))
    a = d.create_object("rectangle", rect=(100, 100, 160, 140))
    b = d.create_object("rectangle", rect=(170, 100, 230, 140))
    g = d.group_objects([a, b])
    objs = d.parsed.page.objects
    members = [objs[g + 1], objs[g + 2]]
    assert not any(m.subtype_size & 0x10 for m in members)
    assert d.set_input_commands(g, touch={"CMD": "Beep()"})
    assert objs[g].subtype_size & 0x10 and all(m.subtype_size & 0x10 for m in members)
    d.undo()
    assert not any(m.subtype_size & 0x10 for m in members)


def test_the_default_charset_is_the_code_pages():
    """CtDraw gives a text tail without the charset flag the SYSTEM charset
    and writes the flag only for another one: ANSI_CHARSET 0 on cp1252, SHIFTJIS 128 on
    cp932 (the tail flag is not written for it there)."""
    from ctgedit.format import geometry as G
    from ctgedit.format import textcodec

    assert G.DEFAULT_CHARSET == G.CODEPAGE_CHARSETS.get(textcodec.CODEPAGE, 0)
    assert G.CODEPAGE_CHARSETS[1252] == 0 and G.CODEPAGE_CHARSETS[932] == 128
