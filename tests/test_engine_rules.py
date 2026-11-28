"""Rules that keep our output unchanged by a CtDraw load and save."""

import glob
import os
import sys

import pytest

import fxinstall
from ctgedit.format import (
    colourstate as CS,
    factory,
    geometry as G,
    placement as PL,
    textmetrics as TM,
    writer,
)
from ctgedit.format.page import parse_ctg, parse_ctg_bytes

win = pytest.mark.skipif(sys.platform != "win32", reason="GDI text metrics")

#: the FxMain pages a golden rule reads as CtDraw saved them (each a 'pages' unit)
ORACLE_PAGES = ("FxShapes", "FxColours", "FxAnim", "FxGenies", "FxTextKinds", "FxSymbols", "FxMembers")


def _fx_pages(fx_project):
    """The records of every page of FxMain in the fx_install copy: [(name, objects)]."""
    out = []
    for path in sorted(glob.glob(os.path.join(fx_project("FxMain"), "*.ctg"))):
        out.append((os.path.basename(path), parse_ctg(path).page.objects))
    assert len(out) > 40
    return out


def _oracle_pages():
    """The records of :data:`ORACLE_PAGES` as CtDraw saved them (skips while the capture
    is missing)."""
    return [
        (name, parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/" + name, "page.ctg")).page.objects)
        for name in ORACLE_PAGES
    ]


def _colour_state_identity(pages):
    n = 0
    for name, objs in pages:
        for po in objs:
            before = po.body_raw
            CS.rebuild(po)
            assert po.body_raw == before, (name, po.index)
            n += 1
    assert n


# ------------------------------------------------------------------ colour
def test_colour_state_rebuild_is_identity_on_the_fixtures(fx_project):
    _colour_state_identity(_fx_pages(fx_project))


@pytest.mark.golden
def test_colour_state_rebuild_is_identity_on_ctdraws_saves():
    _colour_state_identity(_oracle_pages())


def test_pipe_colour_goes_to_hilight_and_halves_lolight():
    po = factory.create_object("pipe", nodes=[(0, 0), (50, 0), (50, 40)])
    assert factory.set_object_colour(po, 0x3366CC)
    cb = G.find_colour_block(po)
    assert cb.slots[G.SLOT_HILIGHT] == (0x3366CC, 0x3366CC)
    assert cb.slots[G.SLOT_LOLIGHT] == (0x193366, 0x193366)
    assert cb.slots[G.SLOT_LINE] == cb.slots[G.SLOT_FILL] == (0x3366CC, 0x3366CC)


def test_text_colour_is_fill_and_line_and_keeps_fill_style():
    po = factory.create_object("text", rect=(10, 10, 90, 30), text="T")
    fs = G.find_colour_block(po).flags[G.FLAG_FILL_STYLE]
    assert factory.set_object_colour(po, 0xCC3300, line=True)
    cb = G.find_colour_block(po)
    assert cb.slots[G.SLOT_FILL] == cb.slots[G.SLOT_LINE] == (0xCC3300, 0xCC3300)
    assert cb.flags[G.FLAG_FILL_STYLE] == fs


def test_state0_follows_fill_slot_on_rect_seed():
    po = factory.create_object("rectangle", rect=(0, 0, 40, 30), colour=0x00FF00)
    L = CS.layout(po)
    s0 = tuple(__import__("struct").unpack_from("<II", po.body_raw, L["state0_at"]))
    assert s0 == (0x00FF00, 0x00FF00)
    # a new plain rectangle stores no colour animation: the seed's lone
    # COL_MAXON is dropped, so the colour-state array is state-0 only (FLAG
    # 0x01, 12 bytes) like every bagless rectangle CtDraw wrote (corpus)
    assert "COL_MAXON" not in po.raw_properties()
    assert L["flag"] == 0x01


def test_no_style_record_has_no_static_colour_channel(fx_page):
    """FxGenies' genies hold rectangles with no style block (fxgenie's elements)."""
    objs = parse_ctg(fx_page("FxGenies")).page.objects
    po = next(p for p in objs if p.type_code == 0x0F and not (p.subtype_size & 2))
    before = po.body_raw
    assert factory.set_object_colour(po, 0x123456) is False
    assert po.body_raw == before


def test_ellipse_seed_drops_stale_dynamic_bit():
    po = factory.create_object("ellipse", rect=(10, 60, 70, 110), colour=0xFFA000)
    assert not (po.subtype_size & 0x10)
    gh = G.parse_geom_header(po.body_raw)
    assert gh.anchor == (10 + (60 - 1) // 2, 60)  # (l + (w-1)//2, t)


# ------------------------------------------------------------------ text
def _static_text_extents(pages, project_dir):
    """Every static text measures to its stored box, with FxMain's tag FORMATs installed
    (a numeric display bound to a bare name measures the name's FORMAT, as the editor and
    CtDraw do); the numeric displays of FxTextKinds that need them are measured too."""
    from ctgedit.io.reader import tag_format_lookup

    fm = tag_format_lookup(os.path.join(project_dir, "FxTextKinds.ctg")) or {}
    before = TM.installed_tag_format()
    TM.use_tag_formats(lambda name: fm.get(name.strip().lower()))
    try:
        hit, tot, bad, done = 0, 0, [], set()
        for name, objs in pages:
            for po in objs:
                if po.type_code != 5 or not (po.subtype_size & 2) or po.an_str != "-1":
                    continue
                e = TM.text_extent(po)
                if e is None:
                    continue
                tot += 1
                done.add(po.raw_properties().get("__AN_NAME__"))
                if tuple(e) == tuple(G.parse_geom_header(po.body_raw).rect1):
                    hit += 1
                else:
                    bad.append((name, po.index))
    finally:
        TM.use_tag_formats(before)
    assert tot and hit == tot, bad[:10]
    if any(name.startswith("FxTextKinds") for name, _objs in pages):
        assert BARE_NAME_NUMBERS <= done, BARE_NAME_NUMBERS - done


#: FxTextKinds' numeric displays on a bare name with a ':format' tail ('FX_LEVEL2:#!',
#: 'FX_NOT_A_TAG:##.#', ...): CtDraw measures the name's FORMAT or '####.#', not the tail
BARE_NAME_NUMBERS = {"FxNumBang", "FxNumUndefined", "FxNumField", "FxNumOverride", "FxNumEquip"}


@win
def test_static_text_extent_matches_the_fixtures(fx_project):
    """Every measured static text of FxMain keeps the box the text measures to (the
    fixtures were measured at 96 dpi, as the suite reads them)."""
    _static_text_extents(_fx_pages(fx_project), fx_project("FxMain"))


@pytest.mark.golden
@win
def test_static_text_extent_matches_ctdraws_saves(fx_project):
    """CtDraw's own boxes for the static texts (its PageSave measures them again)."""
    _static_text_extents(_oracle_pages(), fx_project("FxMain"))


def _text_strings_identity(pages):
    n = 0
    for name, objs in pages:
        for po in objs:
            if po.type_code == 5 and (po.subtype_size & 2):
                before = po.body_raw
                writer.rebuild_text_strings(po)
                assert po.body_raw == before, (name, po.index)
                n += 1
    assert n


def test_text_string_section_rebuild_is_identity(fx_project):
    _text_strings_identity(_fx_pages(fx_project))


@pytest.mark.golden
def test_text_string_section_rebuild_is_identity_on_ctdraws_saves():
    _text_strings_identity(_oracle_pages())


def test_measured_string_modes():
    po = factory.create_object("number", rect=(0, 0, 60, 20), text="(Tag):##.#")
    assert TM.measured_string(po, "####.#") == "##.#"
    writer.set_object_property(po, "TXTMODE", "4")
    assert TM.measured_string(po, "x") == "=(Tag):##.#"


def test_a_format_tail_after_a_bare_name_is_not_measured():
    """CtDraw (the FxTextKinds capture) measures 'name:fmt' on a bare name as the name's
    FORMAT, or '####.#' when the whole name is no tag; only '(expr):fmt' measures its tail."""
    formats = {"fx_level1": "###.#", "fx_level2": "#.##"}
    look = lambda name: formats.get(name.strip().lower())  # noqa: E731
    po = factory.create_object("number", rect=(0, 0, 60, 20), text="(Tag):##.#")
    for expr, want in (
        ("FX_LEVEL2:#!", "#.##"),
        ("FX_NOT_A_TAG:##.#", "####.#"),
        ("FX_LEVEL1.Field:##", "####.#"),
        ("Plant.Tank1.Level:###", "####.#"),
        ("(FX_LEVEL1):#.#", "#.#"),
    ):
        writer.set_object_property(po, "TXT_STR", expr)
        assert TM.measured_string(po, "x", tag_format=look) == want, expr
    writer.set_object_property(po, "TXT_STR", "FX_LEVEL2:#!")
    assert TM.measured_string(po, "x", tag_format=None) is None  # no tag table: unknown


@win
def test_caption_edit_refits_box_and_bag_size(fx_page):
    """FxTxtOffLabel of FxTextKinds: a caption with a text expression (TXT_OFF
    '@(Fx Start)')."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxTextKinds"))
    i = doc.find_objects_by_name("FxTxtOffLabel")[0]
    p = doc.parsed.page.objects[i]
    assert p.type_code == 5 and (p.subtype_size & 2) and p.text_exprs
    doc.set_text(i, "A much longer caption than before")
    po = doc.parsed.page.objects[i]
    assert tuple(G.parse_geom_header(po.body_raw).rect1) == tuple(TM.text_extent(po))
    # the TXT_OFF bag entry keeps its engine overshoot (size = pairs + 10)
    for bag in po.bags:
        if any(k == "TXT_OFF" for k, _ in bag.pairs):
            assert bag.trailer_len == 10


def test_number_uses_txt_str_and_mask_caption():
    po = factory.create_object("number", rect=(0, 0, 60, 20), text="PUMP_A_SPEED")
    p = po.raw_properties()
    assert p["TXTMODE"] == "3" and p["TXT_STR"] == "PUMP_A_SPEED"
    assert "TXT_A" not in p and "FORMAT" not in p
    assert G.parse_text_tail(po)["text"] == "####.#"


def test_button_caption_lpstr_follows_text_bag():
    po = factory.create_object("button", rect=(0, 0, 100, 35), text="Press me")
    L = CS.layout(po)
    o = L["sec80_at"]
    n = int.from_bytes(po.body_raw[o : o + 2], "little")
    assert po.body_raw[o + 2 : o + 2 + n] == b"Press me"
    assert po.raw_properties()["WIDTH"] == "100"
    G.resize_object(po, (0, 0, 120, 40))
    assert po.raw_properties()["WIDTH"] == "120" and po.raw_properties()["HEIGHT"] == "40"


# ------------------------------------------------------------------ forest transforms
def _genie_with_members(doc):
    for i, o in enumerate(doc.page.objects):
        if o.parent is None and o.members and doc.parsed.page.objects[i].subtype_size & 0x200:
            return i
    return None


def test_genie_move_keeps_member_locals_and_rederives_totals(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxGenies"))
    root = _genie_with_members(doc)
    assert root is not None
    sub = doc.subtree(root)
    objs = doc.parsed.page.objects
    locals_before = {j: PL.read_slots(objs[j].body_raw)[0][0][1] for j in sub[1:]}
    t3_before = {j: PL.slot3(objs[j]) for j in sub}
    doc.move_objects([root], 9, 4)
    for j in sub[1:]:
        assert PL.read_slots(objs[j].body_raw)[0][0][1] == locals_before[j]
    for j in sub:
        m, T = PL.slot3(objs[j])
        if t3_before[j][0]:
            assert abs(T[4] - t3_before[j][1][4] - 9) < 1e-6
            assert abs(T[5] - t3_before[j][1][5] - 4) < 1e-6


def test_stripped_ellipse_anchor_is_never_moved(fx_page):
    """FxMembers holds ellipses with no style block inside groups and genies."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxMembers"))
    objs = doc.parsed.page.objects
    k = next(i for i, p in enumerate(objs) if p.type_code == 0x11 and not (p.subtype_size & 2))
    a0 = G.parse_geom_header(objs[k].body_raw).anchor
    doc.move_objects([doc.root_of(k)], 9, 4)
    assert G.parse_geom_header(objs[k].body_raw).anchor == a0


def test_genie_resize_scales_subtree_and_refits_root(fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxGenies"))
    root = _genie_with_members(doc)
    assert root is not None
    objs = doc.parsed.page.objects
    l, t, r, b = G.norm_rect(G.parse_geom_header(objs[root].body_raw).rect1)
    doc.set_object_rect(root, (l, t, r + 10, b + 6))
    gh = G.parse_geom_header(objs[root].body_raw)
    kids = [j for j in doc.subtree(root) if doc.page.objects[j].parent == root]
    u = doc._union_rects(kids)[0]
    assert tuple(gh.rect1) == tuple(u)
    assert PL.read_slots(objs[root].body_raw)[0][0][0] & 0x02  # Local now scales
    doc.undo()
    assert G.norm_rect(G.parse_geom_header(objs[root].body_raw).rect1) == (l, t, r, b)


# ------------------------------------------------------------------ new-object defaults
_INPUT_KEYS = (
    "CMD",
    "DOWN_CMD",
    "REPEAT_CMD",
    "ACTION",
    "RPT_RATE",
    "MSG_LOG",
    "MSG_LOGDN",
    "MSG_LOGRPT",
    "PRIV",
)


def test_new_button_has_no_command():
    # the seed's CMD 'Toggle(Fill_C)' failed to compile without that tag
    from ctgedit.io import pgregister as PR

    po = factory.create_object("button", rect=(0, 0, 90, 30), text="Go", handle=7)
    p = po.raw_properties()
    assert not any(k in p for k in _INPUT_KEYS), p
    assert p["TEXT"] == "Go" and p["VIS_MODE"] == "XP_STYLE"
    assert p["WIDTH"] == "90" and p["HEIGHT"] == "30"
    (row,) = PR.page_rows([po], "P")["pgbutton"]
    assert "CMD" not in row and row["TEXT"] == "Go"
    # a command can still be added afterwards (the property bag is kept)
    assert writer.ensure_object_property(po, "CMD", "Beep()")
    assert po.raw_properties()["CMD"] == "Beep()"


@pytest.mark.parametrize("kind", ["rectangle", "rectangle_outline", "ellipse", "ellipse_outline"])
def test_new_rect_and_ellipse_have_no_3d_effect(kind):
    po = factory.create_object(kind, rect=(10, 10, 80, 40), colour=0x3366CC, handle=3)
    fl = G.find_colour_block_exact(po).flags
    assert fl[G.FLAG_EFFECTS] & 0x0F == 0  # CtDraw: effects 0
    assert fl[G.FLAG_EFFECT_DEPTH] == 0xFF  # ... depth -1 (auto)
    gh = G.parse_geom_header(po.body_raw)
    assert tuple(gh.rect2) == G.predict_bounds(po.type_code, gh.rect1, bytes(fl))
    assert tuple(gh.rect2) == (10, 10, 81, 41)  # no 3D inflation
    tail = G.parse_shape_tail(po)
    assert tail["grad_mode"] == 0
    if po.type_code == 0x0F:
        assert tail["radius"] == 0  # square corners
    else:
        assert fl[G.FLAG_ATTRIBS] & 0x0F == 0  # a full ellipse, not a pie
        assert (tail["start"], tail["end"]) == (0, 0)
    if kind.endswith("_outline"):
        assert fl[G.FLAG_FILL_STYLE] == 0


def test_new_outline_shapes_are_hollow_without_colour():
    for kind in ("rectangle_outline", "ellipse_outline"):
        po = factory.create_object(kind, rect=(0, 0, 40, 30))
        assert G.find_colour_block_exact(po).flags[G.FLAG_FILL_STYLE] == 0


@pytest.mark.parametrize("colour", [None, 0xFF8000])
def test_new_polyline_is_hollow_and_open(colour):
    po = factory.create_object("polyline", nodes=[(5, 5), (9, 14), (22, 8), (30, 30)], colour=colour)
    fl = G.find_colour_block_exact(po).flags
    assert fl[G.FLAG_FILL_STYLE] == 0  # never a filled blob
    assert fl[G.FLAG_ATTRIBS] & 0x01  # open
    closed = factory.create_object("polygon", nodes=[(5, 5), (50, 5), (25, 40)])
    assert not G.find_colour_block_exact(closed).flags[G.FLAG_ATTRIBS] & 0x01


def test_new_text_is_a_plain_caption():
    from ctgedit.io import pgregister as PR

    po = factory.create_object("text", rect=(10, 10, 90, 30), text="Hello")
    assert po.raw_properties()["TXT_OFF"] == "Hello"
    assert G.parse_text_tail(po)["text"] == "Hello"
    assert not po.text_exprs and b"@(" not in po.body_raw
    assert PR.lang_strings([po]) == []  # no pglang row
    # a typed @() label stays a translatable label
    lab = factory.create_object("text", rect=(10, 10, 90, 30), text="@(Hello)")
    assert lab.raw_properties()["TXT_OFF"] == "@(Hello)"
    assert G.parse_text_tail(lab)["text"] == "@(Hello)"
    assert PR.lang_strings([lab]) == ["Hello"]


def test_text_edit_keeps_the_caption_form():
    lab = factory.create_object("text", rect=(10, 10, 90, 30), text="@(Hello)")
    assert writer.set_object_text(lab, "Bye")
    assert lab.raw_properties()["TXT_OFF"] == "@(Bye)"  # existing label kept
    plain = factory.create_object("text", rect=(10, 10, 90, 30), text="Hello")
    assert writer.set_object_text(plain, "Bye")
    assert plain.raw_properties()["TXT_OFF"] == "Bye"
    assert writer.set_object_text(plain, "Hi", label=True)  # asked for a label
    assert plain.raw_properties()["TXT_OFF"] == "@(Hi)"
    assert G.parse_text_tail(plain)["text"] == "@(Hi)"


def test_number_expression_writes_a_dynamic_row():
    from ctgedit.io import pgregister as PR

    po = factory.create_object("number", rect=(0, 0, 60, 20), text="LOOP_1_PV:###.#", handle=11)
    assert po.subtype_size & 0x10
    (row,) = PR.page_rows([po], "P")["pgdynobj"]
    assert row["TYPE"] == "0x0005" and row["AN"] == "12"
    assert row["TXTMODE"] == "3" and row["TXT_STR"] == "LOOP_1_PV:###.#"


def test_chain_total_uses_engine_order():
    # Genies group 758 (live): the child Total is composed ancestor by ancestor
    L = (1.3703703703703705, 0.0, 0.0, 1.065217391304348, 135.0, 285.9347826086957)
    s2 = (1.0, 0.0, 0.0, 1.0, -142.0, -53.0)
    t, _m = PL.apply_parent(s2, 1, [(L, 3, (100, 101))])
    assert repr(t[4]) == "-196.62962962962968"
