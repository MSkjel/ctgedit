"""Render parity."""

import sys

import pytest
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

import fxinstall
from qtkit import app as _app
from ctgedit.format.cnum import c_atoi


def _read(fx_page, page, project="FxMain", mode="native"):
    """(the page as the reader derives it in label ``mode``, its document, its path)."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader

    path = fx_page(page, project)
    old = reader.get_label_mode()
    reader.set_label_mode(mode)
    try:
        pg = reader.read_page(path)
    finally:
        reader.set_label_mode(old)
    return pg, EditableDocument.open(path), path


def _idx(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _scene(pg):
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(pg, thumbnail_opacity=0.0)
    return sc


def _needs(what):
    pytest.skip("needs fixture: %s (shared request)" % what)


def _baked(unit):
    """CtDraw's baked static background of an oracle page (the .ctF 'Dynamic CTF Bitmap',
    record 0) as an RGB array."""
    import struct
    import zlib
    import numpy as np
    from ctgedit.format.container import Container
    from ctgedit.format.page import parse_page

    data = fxinstall.oracle_bytes("pages", unit, "page.ctF")
    c = Container.parse(data)
    b = parse_page(c.block("page"), c.word2).objects[0].body_raw
    raw = None
    for sig in (b"\x78\x5e", b"\x78\x9c", b"\x78\x01", b"\x78\xda"):
        k = b.find(sig)
        if k >= 0:
            try:
                raw = zlib.decompress(b[k:])
                break
            except zlib.error:
                continue
    assert raw is not None, "no baked background in CtDraw's .ctF of %s" % unit
    w, h = struct.unpack_from("<ii", raw, 4)
    h = abs(h)
    return np.frombuffer(raw[40 : 40 + w * h * 4], np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0]]


def _canvas(unit):
    """CtDraw's editor canvas of an oracle page (the unit's canvas.png: the canvas's client
    area at 100 %, the page's top left at its top left) as an RGB array."""
    import numpy as np

    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", unit, "canvas.png"))
    img = img.convertToFormat(QtGui.QImage.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(
        img.height(), img.bytesPerLine() // 4, 4
    )
    return np.array(a[:, : img.width(), [2, 1, 0]])


# ------------------------------------------------------------------ buttons and art
def test_button_caption_paints(fx_page):
    """A captioned button renders a SimpleTextItem child (the GC-safe item) in a dark, readable
    colour. FxButtons' FxBtn01 .. FxBtn08 (captions Btn1 .. Btn8)."""
    from ctgedit.model.document import ObjectKind

    pg, doc, _path = _read(fx_page, "FxButtons")
    sc = _scene(pg)
    seen = 0
    for k in range(1, 9):
        i = _idx(doc, "FxBtn%02d" % k)
        o = pg.objects[i]
        assert o.kind is ObjectKind.BUTTON
        assert str((o.raw.get("_props") or {}).get("VIS_MODE", "")).upper() != "TARGET"
        caps = [c for c in sc._items[i].childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
        assert caps and caps[0].text() == "Btn%d" % k
        assert caps[0].brush().color().lightness() < 160, "caption must be dark on the face"
        seen += 1
    assert seen == 8


def test_splash_art_not_suppressed_by_literal_visible_gate(fx_page):
    """VISIBLE is HIDDEN-WHEN: a constant VISIBLE='0' ("never hidden") symbol is shown,
    like CtDraw draws it. FxSymbols2's FxHiddenEmbedded: an embedded symbol (a group of the
    element's art) with VISIBLE '0'."""
    pg, doc, _path = _read(fx_page, "FxSymbols2")
    i = _idx(doc, "FxHiddenEmbedded")
    o = pg.objects[i]
    assert o.animations.visibility is not None
    assert o.animations.visibility.expression.strip() == "0"
    assert o.raw.get("_rest_visible") is True
    art = [j for j in pg.subtree(i) if j != i]
    assert art
    sc = _scene(pg)
    drawn = [j for j in art if j in sc._items]
    assert drawn and all(sc._items[j].isVisible() for j in drawn)  # drawn, not skipped


def test_dynamic_string_display_echoes_expression_in_literal_mode(fx_page):
    """In literal/design-time mode a runtime string display shows '=<cicode>'
    (CtDraw's editor echo), not the generic 'abc' stub. FxTextKinds' FxPageGetStr
    (TXTMODE 4, PageGetStr("FxKey"))."""
    pg, doc, _path = _read(fx_page, "FxTextKinds", mode="literal")
    assert pg.objects[_idx(doc, "FxPageGetStr")].style.text == '=PageGetStr("FxKey")'
    assert not any(o.style.text == "abc" for o in pg.objects)


def test_native_mode_keeps_abc_stub(fx_page):
    """The headless/native default is unchanged: a runtime string display still
    shows the generic 'abc' stub (no design-time echo)."""
    pg, doc, _path = _read(fx_page, "FxTextKinds", mode="native")
    assert not any((o.style.text or "").startswith("=PageGetStr") for o in pg.objects)
    assert pg.objects[_idx(doc, "FxStrLeftover")].style.text == "abc"


def test_processanalyst_control_gets_light_fill_scheduler_does_not(fx_page):
    """A ProcessAnalyst ActiveX control (near-white surface) gets a light fill; another
    control is an opaque window-white box: CtDraw runs the control, which paints over what
    lies under it (live editor). FxActiveX's FxAnalyst (the Process Analyst
    CLSID) and its unnamed control (a CLSID of our own)."""
    pg, doc, _path = _read(fx_page, "FxActiveX")
    sc = _scene(pg)
    pac = _idx(doc, "FxAnalyst")
    assert pg.objects[pac].raw.get("_type_code") in (0x0D, 0x13)
    assert sc._items[pac].brush().color().lightness() >= 230  # light fill
    unnamed = [
        i
        for i, o in enumerate(pg.objects)
        if o.raw.get("_type_code") in (0x0D, 0x13) and not doc.object_name(i)
    ]
    assert len(unnamed) == 1
    b = sc._items[unnamed[0]].brush()
    assert b.style() == QtCore.Qt.SolidPattern and b.color().lightness() == 255


def test_3d_framed_rect_gets_bevel_edges(fx_page):
    """A 3D framed rect gets bevel edges."""
    from ctgedit.format import geometry as G
    from ctgedit.model.document import ObjectKind

    pg, doc, _path = _read(fx_page, "FxColours")
    sc = _scene(pg)
    seen = set()
    for name in ("FxHollowRaised", "FxHollowLowered", "FxEmbossed1", "FxEmbossed3", "FxHollowShadow"):
        i = _idx(doc, name)
        o = pg.objects[i]
        fl = o.style.style_flags
        assert o.kind is ObjectKind.RECTANGLE and fl and not (fl[2] & 0x0F)
        eff = fl[0] & 0x0F
        assert eff in (1, 2, 3, 4) and i in sc._items
        g = o.geometry
        d = G.effect_depth(
            o.raw["_type_code"],
            (int(round(g.x)), int(round(g.y)), int(round(g.x + g.extent_x)), int(round(g.y + g.extent_y))),
            fl,
        )
        behind = QtWidgets.QGraphicsItem.ItemStacksBehindParent
        copies = sorted(
            (c.pos().x(), c.pos().y(), bool(c.flags() & behind))
            for c in sc._items[i].childItems()
            if (c.pos().x(), c.pos().y()) != (0.0, 0.0)
        )
        if eff == 3:
            assert copies == [(d, d, True)], name
        elif eff == 4 and fl[4] == 1:
            assert copies == [(-d, -d, False)], name
        else:
            assert copies == [(-d, -d, True), (d, d, True)], name
        seen.add((eff, fl[4] == 1 if eff == 4 else None))
    assert {(1, None), (2, None), (3, None), (4, True)} <= seen


# ------------------------------------------------------------------ metafiles
def test_genie_metafile_renders(fx_page):
    """A genie whose element places a Windows MetaFile (tc 0x09): the genie expands that
    element and the WMF is rendered (Windows GDI, CtDraw's window -> extent play) to a
    picture of its box. FxParts' FxPicture1 (fxparts.fx_picture: a 48 x 24 metafile)."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    pg, doc, _path = _read(fx_page, "FxParts", mode="literal")
    g = _idx(doc, "FxPicture1")
    pics = [j for j in pg.subtree(g) if pg.objects[j].raw.get("_type_code") == 0x09]
    assert len(pics) == 1
    o = pg.objects[pics[0]]
    assert o.raw.get("_embedded_bmp") is not None, "the metafile did not render to a picture"
    assert o.raw["_embedded_bmp"][:2] == (o.geometry.extent_x, o.geometry.extent_y) == (48, 24)


@pytest.mark.golden
def test_genie_metafile_matches_ctdraws_canvas(fx_page):
    """The ORACLE: CtDraw's own editor canvas of FxParts (canvas.png): our picture of
    FxPicture1's metafile lands on CtDraw's pixels at shift 0. (Not its .ctF bake: FxPicture1
    is a named genie, so CtDraw keeps it a dynamic .ctF record and its bake shows the page
    colour there, which the test checks too.)"""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    import numpy as np
    from ctgedit.render.scene import PageScene

    bg = _canvas("FxMain/FxParts")
    pg, doc, _path = _read(fx_page, "FxParts")
    g = _idx(doc, "FxPicture1")
    pic = next(j for j in pg.subtree(g) if pg.objects[j].raw.get("_type_code") == 0x09)
    o = pg.objects[pic].geometry
    l, t = int(o.x), int(o.y)
    w, h = int(o.extent_x), int(o.extent_y)
    sc = PageScene()
    sc.set_page(pg, thumbnail_opacity=0.0)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    sc.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(l, t, w, h))
    p.end()
    a = np.array(np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())).reshape(
        h, img.bytesPerLine() // 4, 4
    )[:, :w, [2, 1, 0]]
    theirs = bg[t : t + h, l : l + w].astype(int)
    ours = a.astype(int)
    assert theirs.shape == ours.shape
    close = (np.abs(theirs - ours).max(axis=2) <= 8).sum() / float(w * h)
    assert close > 0.95, close
    baked = _baked("FxMain/FxParts")[t : t + h, l : l + w].reshape(-1, 3)
    assert len({tuple(v) for v in baked}) == 1  # the bake: page colour only


def test_genie_metafile_keeps_its_window_margins():
    _needs(
        "a genie element whose placed WMF's window is larger than what it draws (walls "
        "inside a 32346 x 28296 window, as the floor plans: x 4411 .. 28842, y 8420 .. "
        "24013): CtDraw draws the picture inset by those margins"
    )


def test_building_map_twin_found_by_link_number():
    _needs(
        "a genie instance on a page with Touch records its element lacks (index "
        "alignment fails) and a metafile member: CtDraw finds each member's twin by its "
        "AN string and the metafile member takes its twin's WMF"
    )


# ------------------------------------------------------------------ colours and fills
def test_link_driven_alarm_box_rests_white():
    _needs(
        "a genie member rectangle with an orange 0xFF3300 COL_MAXON alarm colour and an "
        "!*# anchor link: CtDraw's editor leaves it unfilled (white)"
    )


def test_transparent_hover_polygons_draw_no_pen():
    _needs(
        "art-stripped genie member polygons (tc 0x10) whose state 0 is the no-colour "
        "sentinel 0xFFFFFFFF and whose twin's fill and line are Transparent: no brush and "
        "a PS_NULL pen (line style 5); two or more"
    )


def test_transparent_state0_hover_rect_and_building_map():
    _needs(
        "a navigation genie of grey faces (state 0 0x00BFBFBF) under hover rects whose "
        "state 0 is 0xFFFFFFFF (four or more of each), and an art-stripped metafile "
        "member: the faces fill grey, the hovers stay see-through"
    )


def test_neutral_grey_panel_face_wins_over_flat_col_maxon(fx_page):
    """A neutral grey panel face wins over a flat COL_MAXON."""
    pg, doc, _path = _read(fx_page, "FxColours", mode="literal")
    for name, grey in (("FxCm0", 0x808080), ("FxEllipseMaxon", 0x808080), ("FxFlatPair", 0x969696)):
        st = pg.objects[_idx(doc, name)].style
        assert st.filled and (st.fill_colour or 0) & 0xFFFFFF == grey, (name, st.fill_colour)


def test_navbar_rests_at_colour_state_zero():
    _needs(
        "a colour-animated rectangle with a trailing 0x23 colour-state array, dwFlags bit "
        "0x10 CLEAR, a green state 0 (0x009530) and a blue COL_MAXON: it rests green"
    )


def test_runtime_animated_rows_not_filled_by_state_zero():
    _needs(
        "rectangles with a trailing 0x23 colour-state array and dwFlags bit 0x10 SET, "
        "state 0 0xFFAA55 or 0x00FF00: the editor leaves them unfilled"
    )


def test_stripped_boxes_with_a_no_colour_state_stay_unfilled():
    _needs(
        "a genie whose art-stripped 'Ref' boxes (more than 20) carry the no-colour "
        "sentinel as colour state 0 in their owner's style slot: they stay unfilled"
    )


def test_genie_value_box_grafts_template_fills():
    _needs(
        "an inline genie whose art-stripped members lost their style block and whose "
        "element gives a blue 0x0000FF border bar, a white field with rounded corners "
        "(radius 3 to 4), bold Arial 11 menu labels, un-grafted button captions and a "
        "'####.#' value mask (the template fill, font and radius grafts)"
    )


def test_control_button_faces_filled_and_drawn():
    _needs(
        "control button faces: fill-less rectangles grouped with a Touch command (some "
        "styled members with metadata, dwFlags 0x11A, grey 0xD8D8D8) and TARGET button "
        "faces: they fill a system button colour and draw"
    )


def test_redundant_setpoint_hotspot_outline_suppressed():
    _needs(
        "a genie with an unfilled, style-block-less, runtime-animated (dwFlags 0x10) "
        "clickable rectangle on top of a filled value field, and a standalone animated "
        "rectangle with no cover"
    )


def test_target_button_genie_caption_survives_and_face_fills():
    _needs(
        "a menu button genie: an art-stripped face rectangle (white state 0), a "
        "VIS_MODE=TARGET hotspot (the widest member) and stacked '@(Hide)' caption twins, "
        "beside long grey template frame strips"
    )


# ------------------------------------------------------------------ art-stripped genies
def test_art_stripped_genie_faces_do_not_fill_black(fx_page):
    """An art-stripped genie-member RECTANGLE has no style block; its zeroed transform-echo
    tail must not read as a (0,0) black pair and paint OPAQUE BLACK. No art-stripped member
    rectangle of FxGenies, FxParts or FxMany fills pure black (their elements' faces are
    grey or coloured)."""
    for page in ("FxGenies", "FxParts", "FxMany"):
        pg, _doc, _path = _read(fx_page, page, mode="literal")
        members = [
            o
            for o in pg.objects
            if o.kind.value == "rectangle" and o.parent is not None and not (o.raw.get("_dwflags", 0) & 2)
        ]
        assert members, page
        faces = [o for o in members if o.style.filled and (o.style.fill_colour or 0) & 0xFFFFFF == 0]
        assert not faces, "%s: %d art-stripped genie faces fill black" % (page, len(faces))


def test_art_stripped_genie_face_does_not_black_out_header():
    _needs(
        "a genie whose art-stripped panel face rectangle sits under its header text "
        "(the face grey in the element): the header pixel is not blacked out"
    )


def test_equipment_genie_glyph_art_resolves_from_template_ref():
    _needs(
        "an equipment genie whose element keeps its glyph art behind a nested group REF "
        "(element record 3 -> an instrument symbol -> red sensor symbols) that the page "
        "copy lost: four or more grafted sensor bitmaps, red glyph pixels"
    )


def test_art_stripped_genie_frames_not_drawn_as_black_boxes():
    _needs(
        "art-stripped genie member rectangles whose element twin has no visible line (no "
        "style block, or the 0xFF no-colour line slot), one an outer 84 x 77 frame: "
        "_no_outline, not drawn"
    )


def test_inline_genie_orientations_all_render():
    _needs(
        "an inline genie with four orientation groups gated by "
        "'%Direction% <> N' (the vertical one statically FALSE): every "
        "orientation draws"
    )


def test_instrument_arms_are_rotated():
    _needs(
        "a genie whose instrument arms are rotated in the geometry header's TOTAL slot "
        "(slot 2): N=2 +90 (0, -1, 1, 0), N=4 -90 (0, 1, -1, 0), N=1 not rotated"
    )


def test_coil_circle_hollow_not_black_filled():
    _needs(
        "an art-stripped ellipse member with a (0,0) trailing pair whose element twin is "
        "hollow (style block, fill style 0): it stays unfilled"
    )


def test_value_field_boxes_white_filled_frames_stay_transparent():
    _needs(
        "genies with art-stripped value field rectangles (120 x 30 or smaller) tightly "
        "behind '####.#' masks (20 or more) inside a wider 149 x 37 outer frame: the "
        "fields fill white with a black border, the frame stays unfilled"
    )


def test_template_chrome_logo_grafts_dib_no_occlusion():
    _needs(
        "a page holding extra art-stripped instances of its .ctt chrome template whose "
        "logo is an embedded bitmap under a white header background: each duplicate logo "
        "leaf grafts the template twin's bitmap, the logo region is not blank"
    )


def test_art_stripped_genie_polygons_take_twin_outline():
    _needs(
        "a genie (two instances) whose element polygons Base / Discharge / Body have a "
        "white fill and a 1 px 0x969696 line, and an element polygon 'Background' with "
        "attribs bit 0 (open, filled) beside a closed 'Arrow'"
    )


def test_art_stripped_ellipse_takes_its_owners_pie():
    _needs(
        "genies (three or more) whose art-stripped ellipse member's owner is a half-dial "
        "pie (-90 .. 90), and a (0, 0) pie ellipse drawn as a path"
    )


def test_art_stripped_touch_faces_are_drawn_from_their_data(fx_page):
    """No editor rounding or grey 'XP edge' on art-stripped rects that own a touch command
    (flat in CtDraw): FxParts' FxInputs1 (fx_inputs: a rect owning touch
    and keyboard commands) and FxGenies' pumps (a rect owning a touch command): no pen of
    theirs is the edge's 0x808080."""

    def walk(it):
        yield it
        for c in it.childItems():
            yield from walk(c)

    for page in ("FxGenies", "FxParts"):
        pg, _doc, _path = _read(fx_page, page)
        owners = [
            i
            for i, o in enumerate(pg.objects)
            if o.kind.value == "rectangle" and o.members and o.parent is not None
        ]
        assert owners, page
        sc = _scene(pg)
        pens = [
            it.pen().color().name()
            for i in owners
            if i in sc._items
            for it in walk(sc._items[i])
            if isinstance(it, (QtWidgets.QGraphicsRectItem, QtWidgets.QGraphicsPathItem))
        ]
        assert pens and "#808080" not in pens, page


def test_an_unbound_genie_token_caption_draws_nothing(fx_page):
    """A genie text whose '%Param%' nothing binds draws blank (CtDraw's composition blanks
    it): FxParts' FxOrder1 binds MixedCase and Title, not lower / UPPER."""
    pg, doc, _path = _read(fx_page, "FxParts", mode="literal")
    g = _idx(doc, "FxOrder1")
    texts = [pg.objects[j] for j in pg.subtree(g) if pg.objects[j].kind.value == "text"]
    blank = [o for o in texts if not (o.style.text or "").strip()]
    assert len(blank) == 2
    assert not any("%lower" in (o.style.text or "") or "%UPPER" in (o.style.text or "") for o in texts)


def test_text_in_a_scaled_genie_takes_the_genie_scale():
    _needs(
        "a genie instance placed at 2.46 x 2.46 whose element text is 20 pt (stored box "
        "74 x 89, captions such as '21'): the text draws 49 pt"
    )


# ------------------------------------------------------------------ symbols, shapes
def test_rotated_symbol_art_renders_in_placement_box(fx_page):
    """A placed symbol with a 90/270deg slot-1 rotation renders INTO its placement box, not
    double-rotated off it. FxSymbols' FxBitmapSymbol: fxsym.fx_bitmap (14 x 23, upright)
    turned a quarter, so its box is 23 wide and 14 high."""
    pg, doc, _path = _read(fx_page, "FxSymbols", mode="literal")
    i = _idx(doc, "FxBitmapSymbol")
    sym = pg.objects[i]
    assert (sym.raw.get("_local_xform") or (1, 0, 0, 1))[1] != 0, "expected a 90/270 rotation"
    sc = _scene(pg)
    gb = sc._items[i].sceneBoundingRect()
    assert gb.width() > gb.height(), "turned symbol drawn upright (%dx%d)" % (gb.width(), gb.height())
    assert abs(gb.width() - sym.geometry.extent_x) <= 2 and abs(gb.height() - sym.geometry.extent_y) <= 2, (
        gb,
        sym.geometry,
    )
    assert abs(gb.x() - sym.geometry.x) <= 2 and abs(gb.y() - sym.geometry.y) <= 2


def test_distribution_pipe_lines_use_line_slot_colour(fx_page):
    """Pipe lines use the line slot colour."""
    pg, doc, _path = _read(fx_page, "FxFreehand", mode="literal")
    for name, colour in (("FxRedLine", 0xFF0000), ("FxBlueLine", 0x0000FF)):
        o = pg.objects[_idx(doc, name)]
        assert o.kind.value in ("line", "pipe", "polyline", "polygon"), name
        assert (o.style.line_colour or 0) & 0xFFFFFF == colour, name


def test_button_face_rect_with_touch_child_still_draws(fx_page):
    """A drawable-shape RECT that owns a Touch behaviour child (a clickable button FACE)
    keeps its drawable kind and PAINTS its face. FxArrange's FxTouchFill, FxLegacy's
    FxTouchRect, FxInputs' FxKeyRect."""
    from ctgedit.model.document import ObjectKind

    for page, name in (("FxArrange", "FxTouchFill"), ("FxLegacy", "FxTouchRect"), ("FxInputs", "FxKeyRect")):
        pg, doc, _path = _read(fx_page, page)
        i = _idx(doc, name)
        o = pg.objects[i]
        assert o.kind is ObjectKind.RECTANGLE and o.members and o.style.filled, name
        sc = _scene(pg)
        assert i in sc._items and hasattr(sc._items[i], "brush")
        assert sc._items[i].brush().style() != QtCore.Qt.NoBrush, name


# ------------------------------------------------------------------ texts
def test_empty_value_text_renders_blank_not_hash():
    _needs(
        "a genie's empty TEXT value display (no format, no TXT mask): it draws nothing, "
        "not a '##' placeholder"
    )


def test_reader_set_hash_mask_preserved(fx_page):
    """A reader-set '##' value mask (FxTextKinds' FxColStaText: COL_STA_0 red, caption '##')
    is not affected by the empty-text blank rule."""
    pg, doc, _path = _read(fx_page, "FxTextKinds", mode="literal")
    sc = _scene(pg)
    i = _idx(doc, "FxColStaText")
    hashes = [
        it for it in sc.items() if isinstance(it, QtWidgets.QGraphicsSimpleTextItem) and it.text() == "##"
    ]
    assert hashes, "reader-set '##' masks lost"
    assert pg.objects[i].style.text == "##"


def test_empty_string_display_blank_in_literal(fx_page):
    """An EMPTY string display (TXTMODE 4 with an empty TXT_STR) does not show the generic
    'abc' stub in literal/editor mode. FxTextKinds' FxEmptyString (its caption 'text')."""
    pg, doc, _path = _read(fx_page, "FxTextKinds", mode="literal")
    assert pg.objects[_idx(doc, "FxEmptyString")].style.text != "abc"
    assert not any((o.style.text or "") == "abc" for o in pg.objects), (
        "empty string displays still show the 'abc' stub in literal/editor mode"
    )


def test_txtmode4_string_display_echoes_expr_not_numeric_mask(fx_page):
    """A TXTMODE=4 STRING/cicode display echoes its TXT_STR expression ('=<expr>' in literal
    mode), NOT a numeric '####.#' mask -- even when TXT_OFF carries a leftover mask
    (FxTextKinds' FxStrLeftover: TXT_STR FX_NAME, TXT_OFF '####'). A genuinely numeric
    TXTMODE=3 display (FxNumParen) keeps its mask."""
    pg, doc, _path = _read(fx_page, "FxTextKinds", mode="literal")
    m4 = [
        o
        for o in pg.objects
        if str((o.raw.get("_props") or {}).get("TXTMODE", "")) == "4"
        and (o.raw.get("_props") or {}).get("TXT_STR")
    ]
    assert len(m4) >= 4, "no TXTMODE=4 string displays found"
    assert all("#" not in (o.style.text or "") for o in m4), (
        "TXTMODE=4 string display still shows a numeric mask"
    )
    assert all((o.style.text or "").startswith("=") for o in m4)
    assert pg.objects[_idx(doc, "FxStrLeftover")].style.text == "=FX_NAME"
    assert pg.objects[_idx(doc, "FxNumParen")].style.text.startswith("#")


def test_legacy_v3_v4_objects_draw_like_ctdraw(fx_page):
    """Legacy V3/V4 objects draw like CtDraw."""
    pg, doc, _path = _read(fx_page, "FxLegacy2")
    sym = pg.objects[_idx(doc, "FxV3Symbol")]
    assert sym.raw.get("_type_code") == 0x1007
    assert sym.raw.get("_ref") == "fxsym.fx_off" and sym.children
    aset = pg.objects[_idx(doc, "FxV4AnimSymbol")]
    assert aset.raw.get("_type_code") == 0x100D
    assert aset.raw.get("_ref") == "fxsym.fx_off" and aset.children
    ok = pg.objects[_idx(doc, "FxV3String")]
    assert ok.raw.get("_type_code") == 0x1005 and ok.style.text == "@(Fx OK)"
    assert ok.members and pg.objects[ok.members[0]].raw.get("_type_code") == 0x100B
    assert ok.kind.value == "number" and not ok.raw.get("_non_drawable")
    assert ok.style.font == "Courier New" and ok.raw.get("_font_px") == 14
    assert ok.style.text_colour == 0xFF0000 and not ok.style.filled
    num = pg.objects[_idx(doc, "FxV3Number")]
    assert num.raw.get("_type_code") == 0x1004
    assert num.style.text.startswith("#") and not num.style.filled
    assert num.style.font == "Courier New" and num.raw.get("_font_px") == 14


def test_a_transparent_text_colour_draws_nothing(fx_page):
    """A text whose colour slot is the no-colour sentinel (Transparent) draws nothing:
    FxTextKinds' FxTransparentText (a '####.#' value whose text colour slot holds 0xFFFFFFFF)."""
    pg, doc, _path = _read(fx_page, "FxTextKinds")
    i = _idx(doc, "FxTransparentText")
    assert pg.objects[i].raw.get("_text_transparent")
    sc = _scene(pg)
    it = sc._items[i]
    texts = [c for c in [it] + it.childItems() if hasattr(c, "text")]
    assert texts and all(c.brush().style() == QtCore.Qt.NoBrush for c in texts)


def test_a_stretched_text_gets_the_editors_dotted_frame(fx_page):
    """CtDraw frames a text it cannot draw as placed (non-uniform scale or mirrored)
    with a dotted rectangle, on screen only: FxSa's FxSaMaster (the pane captions of
    its stretched panes)."""
    from ctgedit.render.scene import _TextFrame

    pg, _doc, _path = _read(fx_page, "FxSaMaster", "FxSa")
    framed = [i for i, o in enumerate(pg.objects) if o.raw.get("_text_frame")]
    assert len(framed) == 7
    sc = _scene(pg)
    fr = next(c for c in sc._items[framed[0]].childItems() if isinstance(c, _TextFrame))
    r = fr.sceneBoundingRect()
    g = pg.objects[framed[0]].geometry
    assert abs(r.left() + 1 - g.x) < 1.5 and abs(r.top() + 1 - g.y) < 1.5

    def ink():
        """The frame item's own dots painted on white."""
        br = fr.boundingRect()
        img = QtGui.QImage(int(br.width()) + 2, int(br.height()) + 2, QtGui.QImage.Format_RGB32)
        img.fill(0xFFFFFF)
        p = QtGui.QPainter(img)
        p.translate(-br.left() + 1, -br.top() + 1)
        fr.paint(p, QtWidgets.QStyleOptionGraphicsItem())
        p.end()
        return sum(
            1
            for x in range(img.width())
            for y in range(img.height())
            if QtGui.QColor(img.pixel(x, y)).lightness() < 100
        )

    assert ink() > 10
    with sc.export_only(None):
        assert ink() < 5  # the frame's dots are on screen only


# ------------------------------------------------------------------ alarm indicators
def _ai_groups(path):
    """{index: own value bag} of the tc 0x08 records whose own AI_ON is non-zero -- the
    records CtDraw draws an alarm indicator for."""
    from ctgedit.format import writer as W
    from ctgedit.format.page import parse_ctg

    out = {}
    for i, po in enumerate(parse_ctg(path).page.objects):
        bag = {k.upper(): v for k, v in (W.value_bag(po) or [])}
        if po.type_code == 0x08 and c_atoi(bag.get("AI_ON")):
            out[i] = bag
    return out


#: FxAlarms' status lights (AI_INSIDE 1, AI_WIDTH 15, AI_FLAG_ON 0) and two flagged frames
LIGHTS, FRAMES = ("FxAi29", "FxAi30"), ("FxAi02", "FxAi04")


@pytest.fixture
def alarms(fx_page):
    """FxAlarms, whose 30 groups' indicators are all on: (path, {name: index})."""
    from ctgedit.editing import EditableDocument

    path = fx_page("FxAlarms")
    doc = EditableDocument.open(path)
    idx = {"FxAi%02d" % k: _idx(doc, "FxAi%02d" % k) for k in range(1, 31)}
    return path, idx


def test_alarm_indicator_rule(alarms):
    """CtDraw draws alarm indicators only on groups with AI_ON."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import alarmind as AI
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg
    from ctgedit.io.reader import read_page

    path, idx = alarms
    pg = read_page(path)
    ai = {i: o.raw["_alarm_indicator"] for i, o in enumerate(pg.objects) if o.raw.get("_alarm_indicator")}
    groups = _ai_groups(path)
    assert set(groups) == set(idx.values())
    assert set(ai) == set(groups)
    pos = parse_ctg(path).page.objects
    doc = EditableDocument.open(path)
    sizes = AI.flag_sizes(path, doc._resolver())

    def rect2(i):
        return tuple(G.parse_geom_header(pos[i].body_raw).rect2)

    for name, i in idx.items():
        s = AI.settings(AI.props_of(pos[i]))
        union = doc._union_rects(doc._direct_children(i))[1]
        assert rect2(i) == tuple(AI.grown_bounds(union, s, sizes)), name
    for name in LIGHTS:
        a = ai[idx[name]]
        o = a["outer"]
        assert a["strip"] == 16, (name, a["strip"])
        assert (o[2] - o[0], o[3] - o[1]) == (61, 60), (name, o)
        assert a["fill"] == (255, 0, 0) and a["pen"] == (0xBF, 0xBF, 0xBF) and a["flag"] is None
        assert tuple(o) == rect2(idx[name]), name  # no flag: Bounds == frame
    for name in FRAMES:
        a = ai[idx[name]]
        assert a["flag"] is not None, name
        f = a["flag"]
        if isinstance(f, tuple):  # ('default', x, y): 20 x 20
            box = (f[1], f[2], f[1] + 20, f[2] + 20)
        else:
            g = f.geometry
            box = (g.x, g.y, g.x + g.extent_x, g.y + g.extent_y)
        drawn = AI.union([tuple(a["outer"]), box])
        assert AI.union([drawn, rect2(idx[name])]) == rect2(idx[name]), (name, drawn)


@pytest.mark.golden
def test_alarm_indicator_bounds_as_ctdraw_saves_them(fx_page):
    """FxAlarms' 30 indicator groups store AI_ON 1 (widths 1 to 3 and 15, paddings 0 to
    3, inside and not, with and without the flag): CtDraw's save of the page (the oracle)
    stores each framed group's Bounds (rect2), frame U flag, and the generator's (our
    editor's) are the same."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import alarmind as AI
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg_bytes

    doc = EditableDocument.open(fx_page("FxAlarms"))
    theirs = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxAlarms", "page.ctg")).page.objects
    named = {}
    for po in theirs:
        if po.raw_payload is None:
            n = po.raw_properties().get("__AN_NAME__")
            if n:
                named[n] = po
    for k in range(1, 31):
        name = "FxAi%02d" % k
        i = _idx(doc, name)
        ours = doc.parsed.page.objects[i]
        assert AI.settings(AI.props_of(ours))["on"], name
        assert AI.settings(AI.props_of(named[name]))["on"], name
        assert G.parse_geom_header(ours.body_raw).rect2 == G.parse_geom_header(named[name].body_raw).rect2, (
            name
        )


def test_alarm_indicator_never_on_text(alarms):
    """AI_ON on a TEXT record draws nothing (CtDraw draws it for a group only): a text of
    FxAlarms given AI_ON 1 in memory."""
    from ctgedit.editing import EditableDocument
    from ctgedit.model.document import ObjectKind

    path, idx = alarms
    doc = EditableDocument.open(path)
    i = next(i for i, o in enumerate(doc.page.objects) if o.kind is ObjectKind.TEXT and not o.members)
    assert doc.ensure_property(i, "AI_ON", "1")
    assert doc.ensure_property(i, "AI_WIDTH", "3")
    assert (doc.page.objects[i].raw.get("_props") or {}).get("AI_ON") == "1"  # re-derived
    got = {k for k, o in enumerate(doc.page.objects) if o.raw.get("_alarm_indicator")}
    assert i not in got and got == set(_ai_groups(path))


def test_alarm_indicator_follows_move(alarms):
    """move_objects translates the model in place (no re-parse): the indicator must follow
    the group, not stay at its parse-time position. The group is put in an outer group first
    (an owner moves it, as a status-light genie does)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene

    path, idx = alarms
    doc = EditableDocument.open(path)
    g = idx[LIGHTS[0]]
    other = idx[LIGHTS[1]]
    outer = doc.group_objects([g, other])
    assert outer is not None
    g = next(i for i in doc.page.subtree(outer) if doc.object_name(i) == LIGHTS[0])
    root = doc.page.root_of(g)
    assert root != g  # the owner holds the AI group
    sc = PageScene()
    sc.set_page(doc.page)
    r0 = sc._ai_items[g].sceneBoundingRect()
    doc.move_objects([root], 100, 50)
    sc.set_page(doc.page)
    r1 = sc._ai_items[g].sceneBoundingRect()
    assert (r1.x() - r0.x(), r1.y() - r0.y()) == (100, 50)
    doc.undo()
    sc.set_page(doc.page)
    assert sc._ai_items[g].sceneBoundingRect() == r0


def test_hover_never_deletes_scene_items(alarms):
    """The canvas hit-test never walks up with QGraphicsItem.parentItem() (PySide6 hands a
    top-level item reached that way to Python and deletes it). Hover over every drawn root
    and every alarm indicator, collect garbage: nothing may disappear."""
    import gc

    _app()
    from ctgedit.ui.main_window import MainWindow

    path, idx = alarms
    groups = set(_ai_groups(path))
    light = idx[LIGHTS[0]]
    w = MainWindow()
    try:
        w.open_document(path)
        w.resize(1600, 900)
        w.show()
        QtWidgets.QApplication.processEvents()
        sc = w.view.scene()
        before = len(sc.items())
        assert set(sc._ai_items) == groups
        targets = [it.sceneBoundingRect().center() for it in sc._ai_items.values()]
        targets += [it.sceneBoundingRect().center() for it in sc._items.values()]
        vpw = w.view.viewport()
        for c in targets:
            vp = w.view.mapFromScene(c)
            ev = QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                QtCore.QPointF(vp),
                QtCore.QPointF(vpw.mapToGlobal(vp)),
                QtCore.Qt.NoButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(vpw, ev)
        del targets
        gc.collect()
        QtWidgets.QApplication.processEvents()
        assert len(sc.items()) == before
        assert set(sc._ai_items) == groups
        # the hit-test still resolves the status light (its indicator) to its root
        o = w.doc.page.objects[light].raw["_alarm_indicator"]["outer"]
        vp = w.view.mapFromScene(QtCore.QPointF(o[0] + 4, o[1] + 4))
        assert w.doc.page.root_of(w.view._hit_index(vp)[0]) == w.doc.page.root_of(light)
    finally:
        w.doc = None
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()


# ------------------------------------------------------------------ free-hand shapes (tc 0x02)
#: FxMain's FxFreehand: FxFreehand1, an orange (0xFF952B) raised, filled, open free-hand
#: shape of 320 points (the generator writes its record: a style block, then a counted point
#: array that ends the body)
FREEHAND, FREEHAND_POINTS = "FxFreehand1", 320


def test_freehand_shape_draws_filled_and_open(fx_page):
    """CtDraw's free-hand ('Draw', tc 0x02) shape: a style block, then a counted
    point array that ends the body. A hollow free-hand stroke has no closing segment."""
    from ctgedit.model.document import ObjectKind

    pg, doc, _path = _read(fx_page, "FxFreehand")
    i = _idx(doc, FREEHAND)
    o = pg.objects[i]
    assert doc.parsed.page.objects[i].type_code == 0x02
    assert o.kind is ObjectKind.POLYGON and len(o.geometry.nodes) == FREEHAND_POINTS
    assert o.style.filled and o.style.fill_colour == 0xFF952B
    assert o.style.polygon_open and o.style.three_d == 1
    assert not o.raw.get("_non_drawable")
    sc = _scene(pg)
    assert sc._items[i] is not None


def test_freehand_move_carries_its_points(fx_page):
    """Move / resize edit the point array too (live: both are CtDraw fixed points)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_page("FxFreehand"))
    i = _idx(doc, FREEHAND)
    before = G.get_object_nodes(doc.parsed.page.objects[i])
    assert len(before) == FREEHAND_POINTS
    doc.move_objects([i], 30, 20)
    po = doc.parsed.page.objects[i]
    after = G.get_object_nodes(po)
    assert after == [(x + 30, y + 20) for x, y in before]
    l, t, r, b = G.parse_geom_header(po.body_raw).rect1
    xs = [x for x, _ in after]
    ys = [y for _, y in after]
    assert (min(xs), min(ys), max(xs), max(ys)) == (l, t, r, b)


def test_freehand_without_a_style_block_has_no_points(fx_page):
    """A tc 0x02 record with dwFlags bit 0x2 clear (an art-stripped genie member)
    carries no points and stays non-drawable."""
    import copy
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg

    pc = parse_ctg(fx_page("FxFreehand"))
    po = copy.copy(next(p for p in pc.page.objects if p.type_code == 0x02))
    assert G.find_node_array(po) is not None
    po.subtype_size &= ~0x2
    assert G.find_node_array(po) is None


# ------------------------------------------------------------------ no page needed
def test_low_contrast_gradients_stay_gradients():
    """CtDraw draws every gradient, also a low-contrast panel whose stops average to the
    page colour."""
    from types import SimpleNamespace
    from PySide6 import QtGui as _G
    from ctgedit.render.scene import PageScene

    _app()
    sc = PageScene()
    sc._page_bg = _G.QColor(229, 229, 229)
    sc._page_w, sc._page_h = 1000, 700

    def mk(fill, grad, w=300, h=120):
        return SimpleNamespace(
            style=SimpleNamespace(fill_colour=fill, filled=True, gradient_mode=1, gradient_colour=grad),
            geometry=SimpleNamespace(x=0, y=0, extent_x=w, extent_y=h),
        )

    washout = sc._brush(mk(0xD8D8D8, 0xF2F2F2))  # avg lum ~229 == bg
    assert washout.style() == QtCore.Qt.LinearGradientPattern
    real = sc._brush(mk(0x224466, 0x00CCEE))  # high contrast
    assert real.style() == QtCore.Qt.LinearGradientPattern


def _wmf(records, window=(0, 0, 1000, 1000)):
    """A memory metafile: MM_ANISOTROPIC + SetWindowOrg / SetWindowExt (x, y, w, h), then
    ``records`` (function, params as i16), black brush + pen selected first."""
    import struct

    ox, oy, ew, eh = window
    recs = [(0x0103, (8,)), (0x020B, (oy, ox)), (0x020C, (eh, ew))]
    body = b""
    for fn, prm in recs:
        body += struct.pack("<IH%dh" % len(prm), 3 + len(prm), fn, *prm)
    body += struct.pack("<IHHIH", 7, 0x02FC, 0, 0x00000000, 0)  # CreateBrushIndirect solid black
    body += struct.pack("<IHH", 4, 0x012D, 0)  # SelectObject(0)
    for fn, prm in records:
        body += struct.pack("<IH%dh" % len(prm), 3 + len(prm), fn, *prm)
    body += struct.pack("<IH", 3, 0)  # EOF
    words = (18 + len(body)) // 2
    return struct.pack("<HHHIHIH", 1, 9, 0x300, words, 1, 7, 0) + body


def _ink_box(eb):
    import numpy as np

    a = np.frombuffer(eb.rgba, dtype=np.uint8).reshape(eb.height, eb.width, 4)
    ys, xs = np.nonzero(a[:, :, 3])
    return xs.min() / eb.width, ys.min() / eb.height, (xs.max() + 1) / eb.width, (ys.max() + 1) / eb.height


def test_metafile_window_maps_onto_the_extent():
    """CtDraw draws a MetaFile record with MM_ANISOTROPIC, the viewport
    = the object's extent and PlayMetaFile: the metafile's WHOLE window maps onto the
    rect, margins included. A rectangle in the middle half of the window stays there."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    from ctgedit.format.symbols import _render_wmf, wmf_window_extent

    wmf = _wmf([(0x041B, (750, 750, 250, 250))])  # Rectangle(250, 250, 750, 750)
    assert wmf_window_extent(wmf) == (1000, 1000)
    l, t, r, b = _ink_box(_render_wmf(wmf, 400, 200, 1600))
    assert abs(l - 0.25) < 0.02 and abs(r - 0.75) < 0.02, (l, r)
    assert abs(t - 0.25) < 0.02 and abs(b - 0.75) < 0.02, (t, b)
    # a window whose origin is not 0 moves the content with it
    wmf = _wmf([(0x041B, (1750, 1750, 1250, 1250))], window=(1000, 1000, 1000, 1000))
    l, t, r, b = _ink_box(_render_wmf(wmf, 300, 300, 1600))
    assert abs(l - 0.25) < 0.02 and abs(b - 0.75) < 0.02, (l, b)


def test_metafile_paints_white_fills_opaque():
    """CtDraw plays a metafile straight into the page DC: a WHITE fill it paints is opaque (the
    floor plans' window / door openings), only what it leaves alone shows the page."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    import struct
    import numpy as np
    from ctgedit.format.symbols import _render_wmf

    body = [(0x041B, (750, 750, 250, 250))]  # black Rectangle
    wmf = _wmf(body)
    # then a white brush + a white rectangle inside the black one
    extra = struct.pack("<IHHIH", 7, 0x02FC, 0, 0x00FFFFFF, 0) + struct.pack("<IHH", 4, 0x012D, 1)
    extra += struct.pack("<IH4h", 7, 0x041B, 600, 600, 400, 400)
    wmf = bytearray(wmf[:-6] + extra + wmf[-6:])
    struct.pack_into("<IH", wmf, 6, len(wmf) // 2, 2)  # mtSize, 2 objects
    wmf = bytes(wmf)
    eb = _render_wmf(wmf, 101, 101, 4096)
    a = np.frombuffer(eb.rgba, dtype=np.uint8).reshape(eb.height, eb.width, 4)
    assert tuple(a[50, 50]) == (255, 255, 255, 255)  # painted white: opaque
    assert a[27, 27, 3] == 255 and tuple(a[27, 27, :3]) == (0, 0, 0)  # painted black
    assert a[5, 5, 3] == 0  # untouched: transparent


def test_metafile_viewport_keeps_the_far_edge_when_capped():
    """A capped picture (max_dim) keeps the window's right / bottom edge inside it: the viewport
    extent is clamped to W - 1 (the unclamped (w - 1) * scale could round to W)."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    import numpy as np
    from ctgedit.format.symbols import _render_wmf

    # lines ON the window's far edges (a Rectangle never paints its right / bottom edge)
    wmf = _wmf([(0x0325, (2, 1000, 0, 1000, 1000)), (0x0325, (2, 0, 1000, 1000, 1000))])
    eb = _render_wmf(wmf, 3201, 3201, 1600)
    assert (eb.width, eb.height) == (1600, 1600)
    a = np.frombuffer(eb.rgba, dtype=np.uint8).reshape(eb.height, eb.width, 4)
    assert a[:, -1, 3].any() and a[-1, :, 3].any()


def test_metafile_record_found_at_its_class_section():
    """The reader takes the WMF where CtDraw's loader reads it (class section + 8,
    bitmaps.metafile_bits), not the first 01 00 09 00 in the body (a native rect can start (1, 9))."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    from ctgedit.format import factory
    from ctgedit.io.reader import object_from

    wmf = _wmf([(0x041B, (750, 750, 250, 250))])
    po = factory.make_metafile_record(1, (1, 9, 101, 109), wmf)
    assert po.body_raw.find(bytes.fromhex("01000900")) < po.body_raw.find(wmf)
    obj = object_from(po, None, 0)
    assert obj.raw.get("_embedded_bmp") is not None and obj.raw["_wmf"] == wmf


def test_metafile_without_window_extent_draws_nothing():
    """No SetWindowExt and no target size: nothing to map the window onto."""
    if not sys.platform.startswith("win"):
        pytest.skip("metafile rendering is Windows GDI only")
    import struct
    from ctgedit.format.symbols import _render_wmf, wmf_window_extent

    body = struct.pack("<IH4h", 7, 0x041B, 10, 10, 0, 0) + struct.pack("<IH", 3, 0)
    wmf = struct.pack("<HHHIHIH", 1, 9, 0x300, (18 + len(body)) // 2, 0, 7, 0) + body
    assert wmf_window_extent(wmf) is None
    assert _render_wmf(wmf, 0, 0, 4096) is None


def test_multi_line_text_lines_sit_by_their_justify(qapp=None):
    """CtDraw lays every line of a centred / right-justified text out by its justify
    QGraphicsSimpleTextItem would left-align them."""
    from PySide6 import QtGui, QtWidgets
    from ctgedit.render.scene import JustifiedTextItem

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    font = QtGui.QFont("Arial")
    font.setPixelSize(20)

    def ink(align):
        t = JustifiedTextItem("WWWWWWWW\nW", align=align)
        t.setFont(font)
        t.setBrush(QtGui.QColor("black"))
        br = t.boundingRect()
        img = QtGui.QImage(int(br.width()) + 2, int(br.height()) + 2, QtGui.QImage.Format_RGB32)
        img.fill(0xFFFFFF)
        p = QtGui.QPainter(img)
        t.paint(p, QtWidgets.QStyleOptionGraphicsItem())
        p.end()
        fm = QtGui.QFontMetricsF(font)
        y0, y1 = int(fm.height()) + 2, int(2 * fm.height()) - 2  # the second line
        xs = [
            x
            for x in range(img.width())
            for y in range(y0, y1)
            if QtGui.QColor(img.pixel(x, y)).lightness() < 128
        ]
        return min(xs), max(xs), img.width()

    lo, hi, w = ink(0)
    assert lo < w * 0.2
    lo, hi, w = ink(1)
    assert abs((lo + hi) / 2 - w / 2) < 4
    lo, hi, w = ink(2)
    assert hi > w * 0.85


def test_a_numeric_expression_takes_the_format_of_its_tag():
    r"""CtDraw measures a numeric expression with the format of the first variable tag in it
    (live: '####.##' for PLT_LINE2_PM800\MMXU1\TotW/1000, '###.#' for
    IFDEF(Tag1,,0)); a function name or a Cicode string is passed over."""
    from ctgedit.format import textmetrics as T

    fmts = {r"plt_line2_pm800\mmxu1\totw": "####.##", "fx_11_pv01_pvposition": "###.#", "ifdef": "#"}
    look = lambda n: fmts.get(n.lower())  # noqa: E731
    assert T._expr_tag_format(r"PLT_LINE2_PM800\MMXU1\TotW/1000", look) == "####.##"
    assert T._expr_tag_format("IFDEF(Fx_11_PV01_pvPosition,,0)", look) == "###.#"
    assert T._expr_tag_format('StrToReal("ifdef") + 1', look) is None


def test_text_without_smoothing_sets_the_views_hint():
    from PySide6 import QtGui
    from ctgedit.ui.editor.canvas import CanvasView

    _app()
    v = CanvasView()
    v.set_aliased_text(True)
    assert not v.renderHints() & QtGui.QPainter.TextAntialiasing
    v.set_aliased_text(False)
    assert v.renderHints() & QtGui.QPainter.TextAntialiasing


def test_text_without_smoothing_reaches_the_fonts():
    """On Windows the painter's TextAntialiasing hint alone leaves text smoothed; the scene's
    text fonts take QFont.NoAntialias, also on a scene set later."""
    from PySide6 import QtGui
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.editor.canvas import CanvasView

    _app()
    v = CanvasView()
    v.set_aliased_text(True)
    sc = PageScene()
    v.setScene(sc)
    assert sc.text_font(QtGui.QFont("Arial")).styleStrategy() & QtGui.QFont.NoAntialias
    v.set_aliased_text(False)
    assert not sc.text_font(QtGui.QFont("Arial")).styleStrategy() & QtGui.QFont.NoAntialias
