"""What a plain save writes when stored records aren't in CtDraw's form."""

import os
import struct

import pytest


def _gdi():
    from ctgedit.format import textmetrics as TM

    g = TM.Gdi.get()
    if g is None:
        pytest.skip("needs Windows GDI (text extents)")
    return g


# ------------------------------------------------------------------ legacy Numbers / Strings
#: (changes to the Number's values, odd stored Bounds) -> CtDraw's Bounds less its point
#: (LIVE, oracle chain7/legacy_measure; FxMain's FX_LEVEL1 has the FORMAT '###.#', FxSP and
#: DefaultFont are Courier New 14 px, FxBig Arial 18 px, FxSmall Arial 10 px)
NUMBERS = [
    ({}, False, (0, -13, 40, 4)),
    ({}, True, (0, -13, 40, 4)),
    ({"FONT": None}, False, (0, -13, 40, 4)),
    ({"FONT": "DefaultFont"}, False, (0, -13, 40, 4)),
    ({"FONT": "FxBig"}, False, (0, -17, 45, 4)),
    ({"FONT": "FxSmall"}, False, (0, -10, 27, 3)),
    ({"FONT": "FxNoSuchFont"}, False, (0, -11, 40, 2)),
    ({"EXPR": "FX_LEVEL1:#"}, False, (0, -13, 8, 4)),
    ({"EXPR": "FX_LEVEL1:##########.##"}, False, (0, -13, 104, 4)),
    ({"EXPR": "Time()"}, False, (0, -13, 48, 4)),
    ({"EXPR": "FX_LEVEL1 + FX_LEVEL1 * 100 + FX_LEVEL1 * 10000"}, False, (0, -13, 48, 4)),
    ({"EXPR": "FX_LEVEL1:##########.##", "FONT": "FxBig"}, False, (0, -17, 125, 4)),
    ({"EXPR": "FX_LEVEL1:###", "FONT": None}, True, (0, -13, 24, 4)),
]
STRINGS = [
    ({}, False, (0, -12, 64, 5)),
    ({}, True, (0, -12, 64, 5)),
    ({"FONT": None}, False, (0, -12, 64, 5)),
    ({"FONT": None, "OFF_FONT": "FxBig", "ON_FONT": "FxBig"}, False, (0, -16, 80, 5)),
    ({"FONT": None, "OFF_FONT": "FxSmall", "ON_FONT": "FxSmall"}, False, (0, -9, 45, 4)),
    ({"FONT": None, "OFF_FONT": "FxSmall", "ON_FONT": "FxBig"}, False, (0, -9, 45, 4)),
    ({"FONT": None, "OFF_FONT": "FxSP", "ON_FONT": "FxSP"}, False, (0, -12, 64, 5)),
    ({"FONT": None, "OFF_TEXT": '"Hi"', "ON_TEXT": '"Hi"'}, False, (0, -12, 16, 5)),
    ({"FONT": None, "OFF_TEXT": '"A much longer string"', "ON_TEXT": '"x"'}, False, (0, -12, 160, 5)),
    ({"FONT": None, "OFF_TEXT": "", "ON_TEXT": ""}, False, (0, -12, 64, 5)),
    ({"FONT": None, "OFF_TEXT": "FX_TEXT", "ON_TEXT": '"On"'}, False, (0, -12, 64, 5)),
    ({"FONT": None, "OFF_FONT": "FxNoSuchFont", "ON_FONT": "FxNoSuchFont"}, False, (0, -10, 64, 3)),
    ({"FONT": "FxBig", "OFF_FONT": None, "ON_FONT": None}, False, (0, -12, 64, 5)),
]
#: the odd stored Bounds of the oracle's input, about the point
ODD = (0, -40, 200, 40)
#: the names a font table of the project does not define: the stock fixed font, whose metrics
#: are the system DPI's (the oracle's are 96 dpi)
STOCK = "FxNoSuchFont"


def _with(bag, changes):
    out = bag.copy()
    for key, val in changes.items():
        k = next((i for i, (a, _b) in enumerate(out.pairs) if a.lower() == key.lower()), None)
        if val is None:
            if k is not None:
                del out.pairs[k]
        elif k is not None:
            out.pairs[k] = (out.pairs[k][0], val)
        else:
            out.pairs.append((key, val))
    return out


def _legacy_page(fx_install, name="FxLegacyMeasure"):
    """A page in FxMain of the copies of FxLegacy2's Number and String with the changes of
    NUMBERS and STRINGS (the oracle's legacy_measure input, built the same way); its path and
    the expected Bounds less the point, per record."""
    from ctgedit.format import genie_place as GP, legacy, template_place as TP
    from ctgedit.format.page import parse_ctg_bytes

    src = parse_ctg_bytes(open(fx_install.page("FxLegacy2"), "rb").read()).page.objects
    base = {tc: next(po for po in src if po.type_code == tc) for tc in (0x1004, 0x1005)}
    objs, want = [], []
    h = 0
    for col, (stem, tc, rows) in enumerate((("FxNum", 0x1004, NUMBERS), ("FxStr", 0x1005, STRINGS))):
        for k, (changes, odd, deltas) in enumerate(rows):
            r = GP.decode(base[tc])
            nm = "%s%02d" % (stem, k)
            vals = _with(r.entries[0][1], dict(changes, __AN_NAME__=nm))
            r.entries = [[r.entries[0][0], vals, r.entries[0][2]]] + list(r.entries[1:])
            r.name, r.cf, r.handle = nm, 0, h
            h += 1
            po = GP.to_po(GP.encode(r))
            legacy.place_point(po, 20 + 520 * col, 40 + 50 * k)
            if odd:
                legacy.set_point_extent(po, ODD)
            objs.append(po)
            want.append((nm, changes, deltas))
    path = os.path.join(fx_install.project("FxMain"), name + ".ctg")
    with open(path, "wb") as fh:
        fh.write(TP.new_page_bytes(1024, 768, objects=objs))
    return path, want


def _deltas(r):
    return tuple(r.rect2[i] - r.anchor[i % 2] for i in range(4))


def test_a_legacy_number_and_string_take_the_bounds_ctdraws_load_derives(fx_install):
    """Legacy Number and String records take the bounds CtDraw's load derives."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    g = _gdi()
    path, want = _legacy_page(fx_install)
    before = [GP.decode(po) for po in parse_ctg_bytes(open(path, "rb").read()).page.objects]
    EditableDocument.open(path).save()
    after = [GP.decode(po) for po in parse_ctg_bytes(open(path, "rb").read()).page.objects]
    assert len(after) == len(want)
    for (nm, changes, deltas), a, b in zip(want, before, after):
        assert (b.anchor, b.rect1) == (a.anchor, a.rect1), nm
        assert [p for p in b.entries[0][1].pairs if p[0] != "__AN_NAME__"] == [
            p for p in a.entries[0][1].pairs if p[0] != "__AN_NAME__"
        ], nm
        if STOCK in changes.values() and g.measured_dpi != 96:
            continue  # (the stock font follows the system DPI)
        assert _deltas(b) == deltas, (nm, changes, b.rect2)
    # the page as saved is a fixed point (CtDraw's second load + save: the same)
    data = open(path, "rb").read()
    EditableDocument.open(path).save()
    again = [GP.decode(po) for po in parse_ctg_bytes(open(path, "rb").read()).page.objects]
    assert [r.rect2 for r in again] == [r.rect2 for r in after]
    assert len(data) == len(open(path, "rb").read())


def test_the_fixture_include_defines_defaultfont_as_the_install_does(fx_install):
    """A Number or String that names no font is measured in DefaultFont, which the install's
    own Include defines as Courier New 14 (the oracle's CtDraw read it there): the fixture's
    Include defines it the same way, so the fixture pages save as CtDraw saved them."""
    from ctgedit.format import legacy

    fonts = legacy.named_fonts(fx_install.page("FxLegacy2"))
    assert fonts["defaultfont"] == ("Courier New", 14)
    assert fonts["fxsp"] == ("Courier New", 14) and fonts["fxbig"] == ("Arial", 18)


def _write_dbf(path, fields, rows):
    """A minimal dBASE III table (character fields only)."""
    fdescs = b""
    rec_size = 1
    for name, length in fields:
        fdescs += name.ljust(11, "\x00").encode("latin1")[:11]
        fdescs += b"C" + b"\x00" * 4 + bytes([length]) + b"\x00" * 15
        rec_size += length
    head = struct.pack("<BBBBIHH20x", 3, 26, 6, 10, len(rows), 32 + len(fdescs) + 1, rec_size)
    body = b"".join(
        b" " + b"".join(v.encode("latin1").ljust(n)[:n] for (_f, n), v in zip(fields, row)) for row in rows
    )
    with open(path, "wb") as fh:
        fh.write(head + fdescs + b"\x0d" + body + b"\x1a")


def test_a_named_font_in_points_and_with_style_letters(fx_install):
    """a fonts.dbf SIZE that is not positive is points, lfHeight = LOGPIXELSY x
    SIZE / 72 truncated toward zero (-11 points at 96 dpi is 14 px, not 15), a positive one
    pixels; the letters after the face's comma make it bold (B), italic, strikeout or
    underlined. The project's table comes before its includes' (Include's FxSP still found)."""
    from ctgedit.format import legacy, records as R

    g = _gdi()
    proj = os.path.join(fx_install.user, "FxFonts")
    os.makedirs(proj)
    _write_dbf(
        os.path.join(proj, "fonts.DBF"),
        [("NAME", 16), ("TYPE", 32), ("SIZE", 8)],
        [("FxPt", "Arial", "-11"), ("FxBold", "Arial,B", "14"), ("FxSP", "Arial", "9")],
    )
    fonts = legacy.named_fonts(os.path.join(proj, "page.ctg"))
    assert fonts["fxpt"] == ("Arial", -11) and fonts["fxbold"] == ("Arial,B", 14)
    assert fonts["fxsp"] == ("Arial", 9) and fonts["defaultfont"] == ("Courier New", 14)
    px = (g.logpixelsy * 11) // 72
    fonts = dict(fonts, fxpx=("Arial", px), fxpx1=("Arial", px + 1), fxreg=("Arial", 14))

    def box(font, mask="X:##########.##"):
        po = R.to_po(
            R.point_record(0x1004, at=(20, 40), deltas=(0, 0, 80, 16), props=(("EXPR", mask), ("FONT", font)))
        )
        return legacy.value_extent(po, fonts, None)

    assert box("FxPt") == box("FxPx") != box("FxPx1")
    # (a mask with units: Arial's bold letters are wider, its digits and '#' are not)
    assert box("FxBold", "X:#.# mmmmmmmm")[2] > box("FxReg", "X:#.# mmmmmmmm")[2]


def test_the_mask_and_the_text_rules():
    """, pure: the mask from the first '#' after the ':' (a '#'
    first is the whole expression), else the named tag's FORMAT, else nothing; a String's
    text is OFF_TEXT (ON_TEXT when OFF_TEXT is empty) with that state's font, a literal's
    characters up to its closing quote."""
    from ctgedit.format import legacy as L

    fmt = {"fx_level1": "###.#"}.get
    look = lambda n: fmt(n.lower())  # noqa: E731
    assert L.display_mask("FX_LEVEL1:##.# EU", look) == "##.# EU"
    assert L.display_mask("#.##", look) == "#.##"
    assert L.display_mask("FX_LEVEL1", look) == "###.#"
    assert L.display_mask("FX_LEVEL1:abc", look) == "###.#"
    assert L.display_mask("Time()", look) == "" and L.display_mask("", look) == ""
    assert L.string_display({"OFF_TEXT": '"Hi" + x', "OFF_FONT": "A", "ON_FONT": "B"}) == ("Hi", "A")
    assert L.string_display({"ON_TEXT": '"On"', "OFF_FONT": "A", "ON_FONT": "B"}) == ("On", "B")
    assert L.string_display({"OFF_TEXT": "FX_LEVEL1", "OFF_FONT": "A"}, look) == ("###.#", "A")
    assert L.string_display({"FONT": "F"}) == ("", "")
    assert L._string_lines("a^nbc^N") == ["a", "bc"]
    assert L._string_lines("^nx") == ["", "x"] and L._string_lines("a^b") == ["a^b"]


def test_a_page_without_font_tables_keeps_its_legacy_bounds():
    """A page outside any project (no fonts.dbf in its folder or its includes): its Numbers
    and Strings keep their stored Bounds rather than taking the stock font's."""
    from ctgedit.format import genie_place as GP, legacy, records as R

    po = R.to_po(
        R.point_record(0x1004, at=(20, 40), deltas=(0, 0, 80, 16), props=(("EXPR", "X:##"), ("FONT", "FxSP")))
    )
    out, moved = legacy.save_extents([po], None)
    assert moved == [] and out[0] is po
    assert GP.save_form([po], fonts={})[0].body_raw == po.body_raw


def test_a_trend_in_the_old_library_form_is_saved_in_the_form_ctdraw_writes(fx_install):
    """LIVE (the shipped doubletrend's two trends, oracle Q23): CtDraw's load + save
    writes a Trend stored in the old library form (anchor (l, b - 1), rect2 = rect1) with
    anchor (l, b) and rect2 one pixel wider and taller, rect1 kept. FxLegacy's trend, put in
    the old form."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP, legacy, writer
    from ctgedit.format.page import parse_ctg_bytes

    path = fx_install.page("FxLegacy")
    pc = parse_ctg_bytes(open(path, "rb").read())
    k = next(i for i, po in enumerate(pc.page.objects) if po.type_code == legacy.TREND_TC)
    r1 = tuple(GP.decode(pc.page.objects[k]).rect1)
    legacy.place_trend(pc.page.objects[k], r1, "legacy")
    old = GP.decode(pc.page.objects[k])
    assert (old.anchor, old.rect2) == ((r1[0], r1[3] - 1), r1)
    with open(path, "wb") as fh:
        fh.write(writer.serialize_ctg(pc.container, pc.page, pc.attributes))
    EditableDocument.open(path).save()
    r = GP.decode(parse_ctg_bytes(open(path, "rb").read()).page.objects[k])
    assert r.rect1 == old.rect1
    assert r.anchor == (r1[0], r1[3]) and r.rect2 == (r1[0], r1[1], r1[2] + 1, r1[3] + 1)


def _number(d, at, expr="FX_LEVEL1:##.#", font="FxSP"):
    from ctgedit.format import records as R

    return d.add_object(
        R.to_po(
            R.point_record(
                0x1004,
                handle=d.next_handle(),
                at=at,
                deltas=(0, 0, 80, 16),
                props=(("EXPR", expr), ("FONT", font)),
            )
        )
    )


def _tree(lib, name):
    from ctgedit.format import genie_place as GP
    from ctgedit.format.element import open_element

    return [GP.decode(po) for po in open_element(lib, name).tree]


def test_an_element_save_derives_the_legacy_bounds_inside_a_nested_copy_too(fx_install):
    """The element normal form (genie_norm.normalise_records) derives the Bounds of every
    Number and String, a direct member and one of a nested genie's copy alike (LIVE, the
    shipped doubletrend: CtDraw's load + save derived the Numbers and Strings of its copy of
    top.NORMAL@XGA too). '##.#' in FxSP: 32 wide, 13 up, 4 down."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E, geometry as G

    _gdi()
    lib = os.path.join(fx_install.project("FxLib"), "fxleg.ctm")
    d = ElementDocument.new_element(lib, "fx_inner", "genie", canvas=(300, 200))
    d.create_object("rectangle", (0, 0, 120, 60))
    _number(d, (10, 40))
    d.save()
    libraries_changed()
    inner = [r for r in _tree(lib, "fx_inner") if r.tc == 0x1004]
    assert len(inner) == 1 and _deltas(inner[0]) == (0, -13, 32, 4)
    d = ElementDocument.new_element(lib, "fx_outer", "genie", canvas=(400, 300))
    d.create_object("rectangle", (0, 0, 200, 100))
    assert d.place_library_element("fxleg", "fx_inner", "ctm", (20, 20)) is not None
    d.save()
    libraries_changed()
    # the copy's Number with odd stored Bounds (as a library from another machine holds)
    pe = E.open_element(lib, "fx_outer")
    tree = list(pe.tree)
    k = next(i for i, po in enumerate(tree) if po.type_code == 0x1004)
    gh = G.parse_geom_header(tree[k].body_raw)
    body = bytearray(tree[k].body_raw)
    ax, ay = gh.anchor
    struct.pack_into("<4h", body, gh.rect2_off, ax, ay - 40, ax + 200, ay + 40)
    tree[k].body_raw = bytes(body)
    E.store_element(lib, "fx_outer", E.element_content(pe, objects=tree))
    libraries_changed()
    assert _deltas(_tree(lib, "fx_outer")[k]) == (0, -40, 200, 40)
    ElementDocument.open_element(lib, "fx_outer").save(refresh=True)
    got = _tree(lib, "fx_outer")[k]
    assert got.tc == 0x1004 and got.anchor == (ax, ay) and _deltas(got) == (0, -13, 32, 4)


def test_compose_again_does_not_count_a_legacy_bounds_change():
    """copy_compare: a Number's or String's Bounds are its measured text (derived again on
    every load, on the installed fonts), so a copy that differs only there holds the same
    content; its anchor and rect1 still count."""
    from ctgedit.format import copy_compare as CC, legacy, records as R

    a = R.to_po(
        R.point_record(0x1004, at=(20, 40), deltas=(0, -13, 40, 4), props=(("EXPR", "X"), ("FONT", "FxSP")))
    )
    b = R.to_po(
        R.point_record(0x1004, at=(20, 40), deltas=(0, -15, 50, 3), props=(("EXPR", "X"), ("FONT", "FxSP")))
    )
    c = R.to_po(
        R.point_record(0x1004, at=(25, 40), deltas=(0, -13, 40, 4), props=(("EXPR", "X"), ("FONT", "FxSP")))
    )
    assert a.body_raw != b.body_raw and CC.same_content(a, b)
    assert not CC.same_content(a, c)
    legacy.set_point_extent(c, (0, 0, 1, 1))
    assert not CC.same_content(a, c)


# ------------------------------------------------------------------ array and state texts
def _text_genie(fx_install, name, caption, edits):
    """A genie in FxLib fxarr.ctm: a label '%Label%' (a parameter: its records take the
    genie form) and the text ``caption`` with the Display Value ``edits``."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = os.path.join(fx_install.project("FxLib"), "fxarr.ctm")
    d = ElementDocument.new_element(lib, name, "genie", canvas=(300, 200))
    d.create_object("rectangle", (0, 0, 20, 20))
    d.create_object("text", (0, 40, 66, 56), text="%Label%")
    t = d.create_object("text", (0, 100, 66, 116), text=caption)
    assert d.set_text_display(t, edits)
    d.save()
    libraries_changed()
    return lib


def _placed_text(fx_install, lib_name, name, params, page="FxArrPage"):
    """The placed text member of genie ``name`` on a blank FxMain page after its save, and the
    library twin's font."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP, template_place as TP
    from ctgedit.format.page import parse_ctg_bytes

    path = os.path.join(fx_install.project("FxMain"), page + ".ctg")
    with open(path, "wb") as fh:
        fh.write(TP.new_page_bytes(1024, 768))
    doc = EditableDocument.open(path)
    assert doc.place_library_element(lib_name, name, "ctm", (100, 100), params=params) is not None
    doc.save()
    objs = parse_ctg_bytes(open(path, "rb").read()).page.objects
    k = [i for i, po in enumerate(objs) if po.type_code == 0x05][-1]
    return GP.decode(objs[k])


def _width(text, lib, name):
    from ctgedit.format import colourstate as CS, geometry as G, textmetrics as TM
    from ctgedit.format.element import open_element

    tw = [po for po in open_element(lib, name).tree if po.type_code == 0x05][-1]
    tail = G.parse_text_tail(tw)
    L = CS.layout(tw)
    box = TM.extent(
        text,
        tail["face"],
        tail["size"],
        tw.body_raw[L["style_at"] + 3],
        tail["orient"],
        tail["charset"],
        (0, 0),
    )
    return box[2] - box[0]


def test_an_array_text_whose_row_0_is_unbound_measures_its_first_row(fx_install):
    """An array text whose row 0 is unbound measures its first row."""
    _gdi()
    edits = [
        ("TXTMODE", "2"),
        ("TXT_A", "FX_IDX"),
        ("ARRAY_0", "%Array0%"),
        ("ARRAY_1", "%Array1%"),
        ("ARRAY_2", "%Array2%"),
    ]
    lib = _text_genie(fx_install, "fx_array", "%Array0%", edits)
    r = _placed_text(fx_install, "fxarr", "fx_array", {"Array1": "A1"})
    vals = dict(r.entries[0][1].pairs)
    assert vals.get("ARRAY_1") == "A1" and not vals.get("TXT_OFF") and not vals.get("ARRAY_0")
    w = r.rect1[2] - r.rect1[0]
    assert w == _width("A1", lib, "fx_array") and w != _width(" ", lib, "fx_array")


def test_a_caption_template_of_one_blank_keeps_the_blanks_width(fx_install):
    """A state text whose caption template is one blank (STATE_1 'M'): the substitution ' '
    is trimmed out of the value bag but stays the caption, which CtDraw measures (R2 pages
    of a project saved by CtDraw: 4 wide, not 'M')."""
    _gdi()
    lib = _text_genie(
        fx_install,
        "fx_state",
        " ",
        [("TXTMODE", "1"), ("TXT_A", "FX_PUMP1"), ("STATE_1", "M"), ("STATE_2", "L")],
    )
    r = _placed_text(fx_install, "fxarr", "fx_state", {}, page="FxStatePage")
    assert not dict(r.entries[0][1].pairs).get("TXT_OFF")
    assert r.rect1[2] - r.rect1[0] == _width(" ", lib, "fx_state") != _width("M", lib, "fx_state")


# ------------------------------------------------------------------ a symbol of one marker
def _marker_symbol(lib, name, slots, cls, anchor, rect, monkeypatch):
    """A .ctl element of ONE DummyMarker (dwFlags 0xA), bare, as a library holds it."""
    from ctgedit.format import element as E, genie_place as GP, records as R

    r = GP.decode(R.to_po(R.dummy(handle=0, hotspot=(0, 0))))
    r.dw = 0xA
    r.slots = list(slots)
    r.cls = struct.pack("<4h2h", *cls)
    r.anchor, r.rect1, r.rect2 = anchor, rect, rect
    po = GP.to_po(GP.encode(r))
    with monkeypatch.context() as m:
        m.setattr(E, "keeps_bare_root", lambda p: True)
        content = E.new_element_content([po], origin=(20, 15), ext="ctl", normal_form=False)
    E.store_element(lib, name, content)


def test_a_symbol_of_one_scaled_marker_is_saved_with_its_transform_baked(fx_install, monkeypatch):
    """A symbol of one scaled marker is saved with its transform baked."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import placement as P

    lib = os.path.join(fx_install.project("FxLib"), "fxmark.ctl")
    M = (1.2, 0.0, 0.0, 1.2, 631.25, 361.0)
    _marker_symbol(
        lib,
        "fx_scaled",
        [(3, M), (3, (1.2, 0.0, 0.0, 1.2, -4.75, -31.0)), (3, M)],
        (0, 0, 0, 0, 0, 0),
        (631, 361),
        (631, 361, 631, 361),
        monkeypatch,
    )
    _marker_symbol(
        lib,
        "fx_plain",
        [(0, P.IDENT)] * 3,
        (700, 100, 803, 119, 700, 118),
        (700, 118),
        (700, 100, 803, 119),
        monkeypatch,
    )
    libraries_changed()
    assert len(_tree(lib, "fx_scaled")) == 1
    for name in ("fx_scaled", "fx_plain"):
        ElementDocument.open_element(lib, name).save(refresh=True)
        libraries_changed()
    root, mk = _tree(lib, "fx_scaled")
    assert (root.tc, root.cf, root.handle) == (0x08, 1, 1)
    assert root.anchor == (631, 360) and root.rect1 == root.rect2 == (631, 361, 631, 361)
    assert [s[0] for s in root.slots] == [0, 1, 0] and root.slots[1][1][4:] == (-20.0, -15.0)
    assert mk.tc == 0xFFFF and mk.anchor == (0, 0)
    assert mk.rect1 == mk.rect2 == (631, 361, 631, 361)
    assert all(s == (0, P.IDENT) for s in mk.slots)
    assert struct.unpack("<4h2h", mk.cls[-12:]) == (631, 361, 631, 361, 0, 0)
    root, mk = _tree(lib, "fx_plain")
    assert root.anchor == (700, 118) and root.rect1 == (700, 100, 803, 119)
    assert mk.anchor == (700, 118) and mk.rect1 == (700, 100, 803, 119)
    assert struct.unpack("<4h2h", mk.cls[-12:]) == (700, 100, 803, 119, 700, 118)
    # a fixed point (CtDraw's fresh load + save: the same)
    before = _tree(lib, "fx_scaled")
    ElementDocument.open_element(lib, "fx_scaled").save(refresh=True)
    libraries_changed()
    assert [(r.anchor, r.rect1, r.rect2, r.slots, r.cls) for r in _tree(lib, "fx_scaled")] == [
        (r.anchor, r.rect1, r.rect2, r.slots, r.cls) for r in before
    ]


@pytest.mark.parametrize("form", ["ctgedit", "user"])
def test_a_scaled_marker_moved_in_its_window_is_baked_where_the_move_put_it(fx_install, monkeypatch, form):
    """A scaled marker moved in its window is baked where the move put it."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E, placement as P

    lib = os.path.join(fx_install.project("FxLib"), "fxmark.ctl")
    M = (1.2, 0.0, 0.0, 1.2, 631.25, 361.0)
    user = [(3, M), (3, (1.2, 0.0, 0.0, 1.2, -4.75, -31.0)), (3, M)]
    _marker_symbol(lib, "fx_scaled", user, (0, 0, 0, 0, 0, 0), (631, 361), (631, 361, 631, 361), monkeypatch)
    if form == "user":
        pe = E.open_element(lib, "fx_scaled")
        tree = list(pe.tree)
        P.write_slots(tree[0], user)
        E.store_element(lib, "fx_scaled", E.element_content(pe, objects=tree))
    libraries_changed()
    assert [s[0] for s in _tree(lib, "fx_scaled")[0].slots] == ([3, 3, 3] if form == "user" else [3, 3, 0])
    doc = ElementDocument.open_element(lib, "fx_scaled")
    doc.move_objects([0], 5, 0)
    doc.save()
    libraries_changed()
    root, mk = _tree(lib, "fx_scaled")
    assert root.tc == 0x08 and root.rect1 == (636, 361, 636, 361) and root.anchor == (636, 360)
    assert mk.rect1 == mk.rect2 == (636, 361, 636, 361) and mk.anchor == (0, 0)
    assert struct.unpack("<4h2h", mk.cls[-12:]) == (636, 361, 636, 361, 0, 0)
    assert all(s == (0, P.IDENT) for s in mk.slots)


def test_a_named_font_takes_the_systems_character_set():
    """A named font takes the system's character set."""
    from ctgedit.format import geometry as G, textmetrics as TM

    g = _gdi()
    lf = TM._logfont(g, "Wingdings", 14)
    if TM._NAMED.get(("face", b"Wingdings")) is None:
        pytest.skip("Wingdings is not installed")
    assert (lf.lfCharSet & 0xFF, lf.lfOutPrecision) == (G.DEFAULT_CHARSET & 0xFF, 0)
    assert lf.lfFaceName == b"Wingdings" and lf.lfHeight == -14
    # (the face's own LOGFONT says SYMBOL_CHARSET: CtDraw does not keep it)
    assert TM._NAMED[("face", b"Wingdings")][23] == 2


# ------------------------------------------------------------------ own value bags (pinned)
def test_an_elements_own_values_are_its_templates_substituted_without_a_trim(fx_install):
    """An element's own values are its templates substituted, untrimmed."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E, genie_place as GP

    lib = os.path.join(fx_install.project("FxLib"), "fxvals.ctm")
    d = ElementDocument.new_element(lib, "fx_vals", "genie", canvas=(300, 200))
    a = d.create_object("text", (10, 10, 120, 26), text="@(%Description%)")
    b = d.create_object("text", (10, 40, 120, 56), text="####")
    assert d.set_text_display(b, [("TXTMODE", "4"), ("TXT_STR", "%TimeRemaining% * (12 / %MaxTime%)")])
    c = d.create_object("text", (10, 70, 120, 86), text="%Tekst%")
    d.save()
    libraries_changed()
    # the stored value bags put back to the raw templates (the shipped form)
    pe = E.open_element(lib, "fx_vals")
    tree = list(pe.tree)
    for k, po in enumerate(tree):
        r = GP.decode(po)
        if r.tc != 0x05 or not r.entries or len(r.entries[0]) < 3:
            continue
        r.entries[0][1] = GP.Bag(list(r.entries[0][2].pairs), r.entries[0][1].ov)
        tree[k] = GP.to_po(GP.encode(r))
    E.store_element(lib, "fx_vals", E.element_content(pe, objects=tree))
    libraries_changed()
    doc = ElementDocument.open_element(lib, "fx_vals")
    doc.move_objects([a], 1, 0)  # an edit: the save normalises the element
    doc.save()
    libraries_changed()
    vals = [dict(r.entries[0][1].pairs) for r in _tree(lib, "fx_vals") if r.tc == 0x05]
    assert vals[0].get("TXT_OFF") == "@()"
    assert vals[1].get("TXT_STR") == " * (12 / )"
    assert "TXT_OFF" not in vals[2]
    del b, c


# ------------------------------------------------------------------ CtDraw's own captures
@pytest.mark.golden
def test_the_legacy_page_saves_as_ctdraws_load_and_save_wrote_it(fx_install):
    """oracle chain7/legacy_measure: ctgedit's open + save of the input page gives CtDraw's
    Bounds on every record (its anchors, rect1 and values as CtDraw's too)."""
    import fxinstall
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    g = _gdi()
    if g.measured_dpi != 96:
        pytest.skip("the capture's stock font is the 96 dpi one")
    want = [
        GP.decode(po)
        for po in parse_ctg_bytes(
            fxinstall.oracle_bytes("chain7", "legacy_measure", "loaded/page.ctg")
        ).page.objects
    ]
    path = os.path.join(fx_install.project("FxMain"), "FxOracleLegacy.ctg")
    with open(path, "wb") as fh:
        fh.write(fxinstall.oracle_bytes("chain7", "legacy_measure", "input/page.ctg"))
    EditableDocument.open(path).save()
    got = [GP.decode(po) for po in parse_ctg_bytes(open(path, "rb").read()).page.objects]
    assert [(r.name, r.anchor, r.rect1, r.rect2) for r in got] == [
        (r.name, r.anchor, r.rect1, r.rect2) for r in want
    ]


@pytest.mark.golden
def test_the_array_page_saves_as_ctdraw_measured_it(fx_install):
    """oracle chain7/array_token: with CtDraw's fx_plain (the array text) and CtDraw's page
    placing it with Array1 = 'A1', ctgedit's open + save keeps CtDraw's box (100, 200, 117,
    216), 'A1'."""
    import fxinstall
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.format.page import parse_ctg_bytes

    _gdi()
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    with open(lib, "wb") as fh:
        fh.write(fxinstall.oracle_bytes("chain7", "array_token", "genie/library.ctm"))
    libraries_changed()
    data = fxinstall.oracle_bytes("chain7", "array_token", "placed/page.ctg")
    path = os.path.join(fx_install.project("FxMain"), "FxOraclePlace.ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    want = [GP.decode(po) for po in parse_ctg_bytes(data).page.objects]
    EditableDocument.open(path).save()
    got = [GP.decode(po) for po in parse_ctg_bytes(open(path, "rb").read()).page.objects]
    assert [(r.anchor, r.rect1, r.rect2) for r in got] == [(r.anchor, r.rect1, r.rect2) for r in want]
    assert got[3].rect1 == (100, 200, 117, 216)


@pytest.mark.golden
@pytest.mark.parametrize("name", ["fx_marker", "fx_marker_scaled"])
def test_the_marker_symbols_save_as_ctdraw_saved_them(fx_install, name):
    """oracle chain7/marker_symbol: ctgedit's normalising save (refresh) of each input symbol
    writes CtDraw's two records (the root group and the marker, the scaled one baked)."""
    import fxinstall
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import writer
    from ctgedit.format.element import open_element

    lib = fx_install.file("FxLib", "fxsym.ctl")
    with open(lib, "wb") as fh:
        fh.write(fxinstall.oracle_bytes("chain7", "marker_symbol", "input/library.ctl"))
    libraries_changed()
    want = open_element(fxinstall.oracle("chain7", "marker_symbol", "saved/%s/library.ctl" % name), name).tree
    ElementDocument.open_element(lib, name).save(refresh=True)
    libraries_changed()
    got = open_element(lib, name).tree
    assert [writer.object_payload(po) for po in got] == [writer.object_payload(po) for po in want]
