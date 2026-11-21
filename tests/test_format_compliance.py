"""Format invariants CtDraw relies on."""

import glob
import os

import pytest

import fxinstall
from ctgedit.format import geometry as G
from ctgedit.format import textcodec
from ctgedit.format.page import parse_ctg


def _idx(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _oracle_page(fx_install, page, project="FxMain"):
    """CtDraw's PageOpen + PageSave of a fixture page (the oracle), written over the page
    of the fx_install copy (its project's tables and libraries then apply). Its path."""
    data = fxinstall.oracle_bytes("pages", "%s/%s" % (project, page), "page.ctg")
    path = fx_install.page(page, project)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _handle_violations(objects):
    """(duplicate non-input handles, input records not sharing their owner's)."""
    seen, dups, bad_inputs = {}, [], []
    stack = []
    for i, po in enumerate(objects):
        while stack and stack[-1][1] <= 0:
            stack.pop()
        owner = None
        if stack:
            owner = stack[-1][0]
            stack[-1][1] -= 1
        h = G.get_handle(po)
        if po.type_code in G.INPUT_TYPE_CODES and owner is not None:
            if h != owner:
                bad_inputs.append(i)
        else:
            if h in seen:
                dups.append(i)
            seen[h] = i
        if po.class_flag > 0:
            stack.append([h, po.class_flag])
    return dups, bad_inputs


# ---------------------------------------------------------------- codec
def test_codec_is_a_lossless_windows_1252_bijection():
    b = bytes(range(256))
    s = b.decode(textcodec.CODEC)
    assert len(set(s)) == 256
    assert s.encode(textcodec.CODEC) == b
    # Windows maps the 5 bytes Python's cp1252 leaves undefined to C1 points
    for x in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
        assert bytes([x]).decode(textcodec.CODEC) == chr(x)
    assert "\u20ac\u2013\u2022\u2026\u201c".encode(textcodec.CODEC) == b"\x80\x96\x95\x85\x93"
    assert textcodec.unencodable("\u03a9 ohm \u2013") == ["\u03a9"]


def test_bullets_decode_as_bullets_and_survive_an_unrelated_edit(fx_page):
    """FxTextKinds' FxBullets: a text whose caption, TXT_ON and TXT_OFF hold 0x95 bullets.
       The 0x95 bytes are counted in the records only: a save gives the page a fresh identity
    GUID (CtDraw does too), in the head and the trailer, whose random bytes can hold a 0x95."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    def record_bytes(path):
        return b"".join(p.body_raw for p in parse_ctg(path).page.objects)

    dst = fx_page("FxTextKinds")
    n_bullets = record_bytes(dst).count(b"\x95")
    assert n_bullets >= 4
    doc = EditableDocument.open(dst)
    i = _idx(doc, "FxBullets")
    txt = doc.page.objects[i]
    assert "\u2022" in txt.style.text
    assert "\x95" not in txt.style.text
    before = doc.parsed.page.objects[i].raw_properties()
    assert all("\u2022" in before[k] for k in ("TXT_ON", "TXT_OFF")), before
    # an unrelated bag edit re-serialises every pair of the bag holding the
    # bullets
    po = next(p for p in doc.parsed.page.objects if b"\x95" in p.body_raw)
    writer.ensure_object_property(po, "ZZ_TEST", "1")
    doc.save(dst)
    assert record_bytes(dst).count(b"\x95") == n_bullets
    after = parse_ctg(dst).page.objects[i].raw_properties()
    assert all(after[k] == before[k] for k in ("TXT_ON", "TXT_OFF"))
    assert EditableDocument.open(dst).page.objects[i].style.text == txt.style.text


# ---------------------------------------------------------------- plain caption
def test_plain_text_caption_edit_writes_tail_and_txt_off(fx_page):
    """FxShapes' FxText: a plain (no expression, not a display) text caption."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    dst = fx_page("FxShapes")
    doc = EditableDocument.open(dst)
    objs = doc.parsed.page.objects
    i = _idx(doc, "FxText")
    o = objs[i]
    assert o.type_code == 0x05 and not o.text_exprs and G.parse_text_tail(o) is not None
    assert (o.raw_properties().get("TXTMODE") or "") not in ("3", "4")
    before = [o.body_raw for o in objs]
    new = "Pris 5 \u20ac \u2013 ok"
    assert doc.set_text(i, new)
    doc.save(dst)
    pc = parse_ctg(dst)
    po = pc.page.objects[i]
    assert G.parse_text_tail(po)["text"] == new
    assert po.raw_properties()["TXT_OFF"] == new  # the load-time mirror
    assert [k for k, o in enumerate(pc.page.objects) if o.body_raw != before[k]] == [
        i
    ]  # nothing else touched
    assert read_page(dst).objects[i].style.text == new


# ---------------------------------------------------------------- handles / AN
def _check_handles_and_live_an(path):
    pc = parse_ctg(path)
    assert _handle_violations(pc.page.objects) == ([], [])
    from ctgedit.io.reader import read_page

    page = read_page(path)
    for po, o in zip(pc.page.objects, page.objects):
        assert o.an == G.live_an(po)
        assert o.raw["_link_an"] == po.an_id
    # the pages hold input records (touch / keyboard) that share their owner's handle
    assert any(po.type_code in G.INPUT_TYPE_CODES for po in pc.page.objects)


@pytest.mark.parametrize("page", ["FxGenies", "FxInputs", "FxParts"])
def test_page_handle_invariant_and_live_an(fx_page, page):
    _check_handles_and_live_an(fx_page(page))


@pytest.mark.golden
@pytest.mark.parametrize("page", ["FxGenies", "FxInputs", "FxParts"])
def test_ctdraw_saved_page_handle_invariant_and_live_an(fx_install, page):
    """The same invariants on CtDraw's save of the page."""
    _check_handles_and_live_an(_oracle_page(fx_install, page))


def test_duplicate_and_paste_renumber_to_unique_handles(fx_page):
    """FxArrange's biggest top-level unit: FxBigGroup (a 94-record group between two
    leaves), then FxKeyText (a text with a keyboard child) for the input records."""
    from ctgedit.editing import EditableDocument

    path = fx_page("FxArrange")
    doc = EditableDocument.open(path)
    objs = doc.parsed.page.objects
    big = max(
        (i for i in range(len(objs)) if doc.page.objects[i].parent is None), key=lambda i: len(doc.subtree(i))
    )
    assert big == _idx(doc, "FxBigGroup")
    n = len(doc.subtree(big))
    assert n > 90
    doc.duplicate_objects([big])
    assert _handle_violations(doc.parsed.page.objects) == ([], [])
    # copy the ROOT only: the payload must still carry the whole subtree
    payload = doc.copy_payload([big])
    before = len(doc.parsed.page.objects)
    new = doc.paste_payload(payload)
    assert len(doc.parsed.page.objects) - before == n == len(new)
    assert _handle_violations(doc.parsed.page.objects) == ([], [])
    # the originals keep their handles (CtDraw keeps the FIRST in z-order)
    fresh = parse_ctg(path).page.objects
    for k in range(len(fresh)):
        assert G.get_handle(doc.parsed.page.objects[k]) == G.get_handle(fresh[k])
    # a text with its keyboard record: the input record keeps its owner's new handle
    kt = _idx(doc, "FxKeyText")
    objs = doc.parsed.page.objects
    assert any(objs[j].type_code in G.INPUT_TYPE_CODES for j in doc.subtree(kt))
    doc.duplicate_objects([kt])
    assert _handle_violations(doc.parsed.page.objects) == ([], [])
    new = doc.paste_payload(doc.copy_payload([kt]))
    assert len(new) == len(doc.subtree(kt))
    assert _handle_violations(doc.parsed.page.objects) == ([], [])


def test_create_and_group_records_are_unlinked_and_unique(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    a = doc.create_object("rectangle", (10, 10, 50, 50))
    b = doc.create_object("line", None, [(0, 0), (40, 40)])
    for i in (a, b):
        assert doc.parsed.page.objects[i].an_str == "-1"  # no library link
    g = doc.group_objects([a, b])
    assert g is not None
    assert doc.parsed.page.objects[g].an_str == "-1"
    assert _handle_violations(doc.parsed.page.objects) == ([], [])


def test_replace_bitmap_keeps_the_objects_identity(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    i = doc.add_bitmap_object((5, 5, 9, 9), bytes([255, 0, 0, 255]) * 16, 4, 4)
    h = G.get_handle(doc.parsed.page.objects[i])
    assert doc.replace_bitmap(i, (5, 5, 9, 9), bytes([0, 255, 0, 255]) * 16, 4, 4)
    assert G.get_handle(doc.parsed.page.objects[i]) == h


# ---------------------------------------------------------------- libraries
def _blocks_round_trip(libs):
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page
    from ctgedit.format import writer

    blocks = 0
    for path in libs:
        with open(path, "rb") as fh:
            c = Container.parse(fh.read())
        for blk in c.blocks:
            if not blk.size:
                continue
            pb = parse_page(blk, c.word2)
            region = blk.data[pb.data_start : pb.data_start + pb.objdata_size]
            assert writer.serialize_objects(pb.objects) == region, (path, blk.name)
            blocks += 1
    return blocks


def test_library_element_blocks_round_trip_byte_exact(fx_install):
    """Every library element block ends with element-definition trailer
    records whose prefix does not frame (a ~130-byte definition record + two
    2-byte records); they must be re-emitted verbatim, not re-prefixed. On every
    library of the fake install (symbols, genies, super genies, templates)."""
    libs = sorted(glob.glob(os.path.join(fx_install.user, "*", "*.ct[lmt]")))
    assert len(libs) > 12
    assert _blocks_round_trip(libs) > 20


#: the libraries CtDraw saved again element by element (the oracle's 'elements' step)
LIBRARY_UNITS = [
    ("FxLib/fxsym.ctl", "library.ctl"),
    ("FxLib/fxmore.ctl", "library.ctl"),
    ("FxLib/fxmany.ctl", "library.ctl"),
    ("FxLib/fxgenie.ctm", "library.ctm"),
    ("FxLib/fxparts.ctm", "library.ctm"),
    ("FxLib/fxnest.ctm", "library.ctm"),
    ("FxLib/fxsuper.ctm", "library.ctm"),
    ("FxStyle/fx_style.ctt", "library.ctt"),
    ("FxStyle/fx_alt.ctt", "library.ctt"),
]


@pytest.mark.golden
@pytest.mark.parametrize("unit,name", LIBRARY_UNITS)
def test_ctdraw_saved_library_blocks_round_trip_byte_exact(unit, name):
    """The trailers as CtDraw's own save writes them: every block of the library, after
    CtDraw saved each of its elements, is re-emitted verbatim."""
    path = fxinstall.oracle("elements", unit, name)
    assert _blocks_round_trip([path]) > 0


def test_unencodable_user_text_is_refused_not_saved_as_question_marks(fx_page):
    from ctgedit.editing import EditableDocument, UnencodableTextError

    doc = EditableDocument.open(fx_page("FxTextKinds"))
    i = next(k for k, o in enumerate(doc.parsed.page.objects) if o.type_code == 0x05)
    before = doc.parsed.page.objects[i].body_raw
    with pytest.raises(UnencodableTextError) as ei:
        doc.set_text(i, "R = 5 \u03a9")
    assert "U+03A9" in str(ei.value)
    assert doc.parsed.page.objects[i].body_raw == before
    assert not doc.dirty


# ---------------------------------------------------------------- rect2 = Bounds
def _bounds_checked(path):
    pc = parse_ctg(path)
    n = 0
    for po in pc.page.objects:
        fl = G.style_flags(po)
        if fl is None:
            continue
        gh = G.parse_geom_header(po.body_raw)
        p = G.predict_bounds(po.type_code, gh.rect1, fl)
        if p is None:
            continue
        assert tuple(gh.rect2) == tuple(p), (po.index, hex(po.type_code))
        n += 1
    return n


def test_bounds_rule_reproduces_rect2(fx_page):
    """FxStyles: 48 styled shapes (four kinds, widths 1 to 4, three effects)."""
    assert _bounds_checked(fx_page("FxStyles")) > 40


@pytest.mark.golden
@pytest.mark.parametrize("page,least", [("FxStyles", 40), ("FxShapes", 15), ("FxColours", 20)])
def test_bounds_rule_reproduces_ctdraws_rect2(fx_install, page, least):
    """The rule against the rect2 (Bounds) CtDraw's own save writes."""
    assert _bounds_checked(_oracle_page(fx_install, page)) > least


def test_line_width_edit_rederives_bounds(fx_page):
    """FxShapes' FxRect: a top-level styled rectangle (tc 0x0F)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    objs = doc.parsed.page.objects
    i = _idx(doc, "FxRect")
    assert objs[i].type_code == 0x0F and G.style_flags(objs[i]) is not None
    assert doc.page.objects[i].parent is None
    assert doc.set_line_width(i, 5)
    po = doc.parsed.page.objects[i]
    gh = G.parse_geom_header(po.body_raw)
    assert tuple(gh.rect2) == G.predict_bounds(0x0F, gh.rect1, G.style_flags(po))


def test_group_record_and_member_move_keep_container_unions(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    a = doc.create_object("rectangle", (10, 10, 50, 50))
    b = doc.create_object("rectangle", (80, 20, 120, 70))
    g = doc.group_objects([a, b])
    assert doc._container_fits(g)
    member = doc._direct_children(g)[1]
    doc.move_objects([member], 30, 40)  # member-only edit
    assert doc._container_fits(g)
    doc.undo()
    assert doc._container_fits(g)


def test_new_group_is_a_free_ctdraw_group_not_locked(fx_page):
    """Arrange > Group must write the shape CtDraw writes for a FREE group
    (dwFlags 0xA, 109-byte body, anchor = left,bottom-1), without NOSELECT (0x4)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    a = doc.create_object("rectangle", (10, 10, 50, 50))
    b = doc.create_object("rectangle", (80, 20, 120, 70))
    g = doc.group_objects([a, b])
    po = doc.parsed.page.objects[g]
    assert po.subtype_size == 0x0A and not (po.subtype_size & 0x4)
    assert len(po.body_raw) == 109
    gh = G.parse_geom_header(po.body_raw)
    l, t, r, bb = G.norm_rect(gh.rect1)
    assert gh.anchor == (l, bb - 1)


def test_edit_lock_is_persisted_and_undoable(fx_page):
    from ctgedit.editing import EditableDocument

    dst = fx_page("FxShapes")
    doc = EditableDocument.open(dst)
    r = doc.page.roots()[-1]
    assert doc.set_locked([r], True) == 1
    doc.save(dst)
    assert parse_ctg(dst).page.objects[r].subtype_size & 0x4
    doc.undo()
    assert not doc.parsed.page.objects[r].subtype_size & 0x4


# ---------------------------------------------------------------- V3/V4 classes
def test_legacy_kinds_are_created_with_their_class_position():
    """V3/V4 classes keep their position in their class section
    (only when dwFlags bit 0x2 is set); the seeds must be UNLINKED records and
    the section must carry the requested position (engine: fixed points)."""
    import struct
    from ctgedit.format import factory, legacy

    for kind in (
        "string",
        "cicode_object",
        "animation_keyboard",
        "digital_text",
        "color_flood",
        "animated_symbol",
    ):
        po = factory.create_object(kind, (40, 60, 90, 80), text="Hi", symbol=("icons", "help1", "ctl"))
        assert po.subtype_size & 0x2 and po.an_str == "-1", kind
        ct = legacy.class_tail(po)
        assert ct and ct[0] == "point", kind
        gh = G.parse_geom_header(po.body_raw)
        assert tuple(gh.rect2[:2]) == (40, 60), kind  # drawn box at rect TL
        assert struct.unpack_from("<2h", po.body_raw, ct[1]) == tuple(gh.anchor)
    tr = factory.create_object("trend", (120, 150, 300, 225))
    assert legacy.class_tail(tr)[0] == "trend"


def test_moving_a_legacy_record_moves_its_class_section():
    """A moved Cicode object / trend shifts its class section too, or the engine snaps it
    back to its old spot."""
    from ctgedit.format import factory, legacy

    for kind, rect in (("cicode_object", (40, 60, 90, 80)), ("trend", (120, 150, 300, 225))):
        po = factory.create_object(kind, rect)
        G.translate_object(po, 7, 30)
        assert legacy.class_tail(po) is not None, kind  # still consistent
    tr = factory.create_object("trend", (120, 150, 300, 225))
    G.resize_object(tr, (10, 20, 150, 110))
    assert legacy.class_tail(tr) is not None


# ---------------------------------------------------------------- genie params
def test_param_edit_keeps_members_percent_an_substitution(fx_page):
    """A member's %AN% was substituted with the root's bag AN; an unrelated
    parameter edit must not write the raw '%AN%' marker back. FxParts' FxAnList1
    (fxparts.fx_anlist: ArrayAN, a command, MVY and SZY on %AN%)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    doc = EditableDocument.open(fx_page("FxParts"))
    objs = doc.parsed.page.objects

    def substituted(o):
        return [v for b in o.bags for k, v in b.pairs if k == "ArrayAN" and v.strip() and "%" not in v]

    root = _idx(doc, "FxAnList1")
    member = next(i for i in doc.subtree(root) if substituted(objs[i]))
    before = substituted(objs[member])
    assert doc.page.ancestors(member)[0] == root
    b = writer.genie_bindings(objs[root])
    key = next(k for k in b if k != "AN")
    doc.set_genie_binding(root, key, (b[key] or "") + "1")
    assert substituted(objs[member]) == before
    assert not any(v == "%AN%" for bb in objs[member].bags[:3] for k, v in bb.pairs if k == "ArrayAN")


def test_param_edit_whose_new_value_contains_the_old_one_terminates(fx_page):
    """FxGenies' FxPump1 (Tag FX_PUMP1): the new value holds the old one."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer

    doc = EditableDocument.open(fx_page("FxGenies"))
    objs = doc.parsed.page.objects
    r = _idx(doc, "FxPump1")
    b = writer.genie_bindings(objs[r])
    key = "Tag"
    assert len(b[key]) >= 3
    new = b[key] + "_x"
    doc.set_genie_binding(r, key, new)
    assert writer.genie_bindings(objs[r])[key] == new


def _numbers(doc, exprs):
    """Numeric displays (TXTMODE 3) of ``exprs`` [(name, expression, caption)] added to
    ``doc`` in Lucida Console, with no format of their own; their indexes."""
    out = []
    for k, (name, expr, caption) in enumerate(exprs):
        i = doc.create_object("number", (20 + 160 * k, 400, 140 + 160 * k, 416), text="##.#")
        assert doc.set_object_name(i, name)
        doc.set_text_display(i, [("TXTMODE", "3"), ("TXT_STR", expr)], caption=caption)
        doc.set_font(i, face="Lucida Console")
        out.append(_idx(doc, name))
    return out


#: undefined expressions with no format: a tag the project lacks, a tag extension's
#: '.Field' and '.Override' (empty captions), an equipment item reference captioned 'Idle'
UNDEFINED = [
    ("FxTestUndefined", "FX_NOT_A_TAG", None),
    ("FxTestField", "FX_NOT_A_TAG.Field", ""),
    ("FxTestOverride", "FX_NOT_A_TAG.Override", ""),
    ("FxTestEquip", "FxNowhere.Tank9.Recipe", "Idle"),
]


def test_unknown_tag_numeric_measures_the_default_mask(fx_page):
    """A numeric display of an unknown tag measures the default mask."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textmetrics as TM
    from ctgedit.io import reader

    reader.set_label_mode("literal")  # the app's (CtDraw's editor) label mode
    path = fx_page("FxTextKinds")
    doc = EditableDocument.open(path)
    idx = _numbers(doc, UNDEFINED)
    doc.save(path)
    doc = EditableDocument.open(path)
    doc._use_tag_formats()
    for i, (_n, _e, caption) in zip(idx, UNDEFINED):
        po = doc.parsed.page.objects[i]
        if caption is not None:
            assert G.parse_text_tail(po)["text"] == caption
        assert TM.measured_string(po, G.parse_text_tail(po)["text"]) == "####.#"
        assert TM.text_extent(po) == G.parse_geom_header(po.body_raw).rect1
    # what the editor draws for the page in its project (its tag database)
    page = reader.read_page(path)
    assert [page.objects[i].style.text for i in idx] == ["####.#"] * len(idx)


@pytest.mark.golden
def test_unknown_equipment_ref_with_a_caption_measures_the_default_mask(fx_page):
    """CtDraw's stored box of such a display is the '####.#' one (two Lucida Console
    characters wider than 'Idle'), not the caption's: a fact of CtDraw's save, which needs
    these displays in a fixture page the oracle captures."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxTextKinds"))
    assert doc.find_objects_by_name("FxNumIdleRef")  # (the fixture has the displays)
    import fxinstall as FX

    data = FX.oracle_bytes("pages", "FxMain/FxTextKinds", "page.ctg")
    path = fx_page("FxTextKinds")
    with open(path, "wb") as fh:
        fh.write(data)
    from ctgedit.format import textmetrics as TM

    doc = EditableDocument.open(path)
    doc._use_tag_formats()
    captions = {"FxNumIdleRef": "Idle", "FxNumExtField": "", "FxNumExtOverride": ""}
    for name, want in captions.items():
        po = doc.parsed.page.objects[_idx(doc, name)]
        caption = G.parse_text_tail(po)["text"]
        assert caption == want, (name, caption)  # CtDraw keeps the caption
        assert TM.measured_string(po, caption) == "####.#"
        assert TM.text_extent(po) == G.parse_geom_header(po.body_raw).rect1
