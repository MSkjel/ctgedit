"""The colour system."""

import os
import shutil
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from ctgedit.format import bitmaps, colourpalette as CP, colourstate, colourtools as T  # noqa: E402
from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the host page (FxMain)
HOST = "FxThumb"


@pytest.fixture(autouse=True)
def _fresh_picker_state():
    from ctgedit.ui import colour_picker

    colour_picker._palettes.clear()
    colour_picker.set_context_dir(None)
    yield
    colour_picker._palettes.clear()
    colour_picker.set_context_dir(None)


def _palette_entries():
    """A 256-entry palette in CtDraw's layout: 176 standard entries (Black at 0, White
    at 15, Lt Red at 81, Transparent at 160, the others distinct greenish blues of our
    own named 'Fx Std <n>'), then the favourites Fx Silver, Fx Flash Red (flashing:
    red / black) and Fx Teal, the rest unused."""
    out = []
    for i in range(CP.STANDARD):
        c = 0x002000 + 0x010101 * i + 0x40  # never 0, 0xFFFFFF or 0xFF0000
        out.append(CP.PaletteColour(c, c, "Fx Std %d" % i))
    out[0] = CP.PaletteColour(0x000000, 0x000000, "Black")
    out[15] = CP.PaletteColour(0xFFFFFF, 0xFFFFFF, "White")
    out[81] = CP.PaletteColour(0xFF0000, 0xFF0000, "Lt Red")
    out[160] = CP.PaletteColour(T.TRANSPARENT, T.TRANSPARENT, "Transparent")
    out += [
        CP.PaletteColour(0xC0C0C0, 0xC0C0C0, "Fx Silver"),
        CP.PaletteColour(0xFF0000, 0x000000, "Fx Flash Red"),
        CP.PaletteColour(0x008080, 0x008080, "Fx Teal"),
    ]
    out += [CP.UNUSED_COLOUR] * (CP.SIZE - len(out))
    return out


def _palette_bytes():
    return CP.format_entries(_palette_entries())


def _project(fx_install, tmp_path, page=HOST, palette=True):
    """A scratch project: a copy of a FxMain page, an (empty) pages.DBF and the test's
    palette."""
    proj = tmp_path / "proj"
    proj.mkdir()
    src = fx_install.page(page)
    dst = proj / os.path.basename(src)
    shutil.copyfile(src, dst)
    (proj / "pages.DBF").write_bytes(b"")
    if palette:
        (proj / "citect.pal").write_bytes(_palette_bytes())
    return str(proj), str(dst)


def _doc(path):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(path)
    doc.derive_check = True
    return doc


def _slots(doc, i):
    L = colourstate.layout(doc.parsed.page.objects[i])
    b = doc.parsed.page.objects[i].body_raw
    return [struct.unpack_from("<II", b, L["style_at"] + 6 + 8 * k) for k in range(6)], L


# ================================================================== colour arithmetic
def test_hls_is_ctdraw_arithmetic():
    """RGB to HLS keeps hue in TENTHS of a degree and rounds half away from
    zero; HLS to RGB is Foley / van Dam's -- so pure red at lightness 128
    comes back (255, 1, 1), as the Graphics Builder computes it."""
    assert T.rgb_to_hls(255, 0, 0) == (0, 128, 255)
    assert T.rgb_to_hls(0, 255, 0) == (1200, 128, 255)
    assert T.rgb_to_hls(0, 0, 128) == (2400, 64, 255)
    assert T.rgb_to_hls(128, 128, 128) == (0, 128, 0)  # grey: hue 0, saturation 0
    assert T.hls_to_rgb(0, 128, 255) == (255, 1, 1)
    assert T.hls_to_rgb(2400, 64, 255) == (0, 0, 128)
    assert T.hls_to_rgb(1234, 77, 0) == (77, 77, 77)
    for rgb in ((12, 200, 99), (255, 255, 255), (0, 0, 0), (1, 2, 3), (250, 10, 128)):
        back = T.hls_to_rgb(*T.rgb_to_hls(*rgb))
        assert max(abs(a - b) for a, b in zip(rgb, back)) <= 2


def test_tcolor_functions():
    red = 0x00FF0000
    assert T.add_hue_delta(red, 1200) == (0x0001FF01, True)
    assert T.add_hue_delta(red, 0) == (red, False)
    assert T.add_hue_delta(T.TRANSPARENT, 1200) == (T.TRANSPARENT, False)
    # a hue range wraps past 0
    assert T.hue_in_range(3500, 3000, 100) and T.hue_in_range(50, 3000, 100)
    assert not T.hue_in_range(1200, 3000, 100)
    # 'in range' needs a hue change (CtDraw: a zero delta answers False)
    assert T.add_hue_delta_in_range(red, 0, 0, 3590) == (red, False)
    # lightness / saturation are PERCENT of the colour's own value
    grey = 0x00808080
    assert T.colour_hls(T.adjust_lightness(grey, 50)[0])[1] == 192
    assert T.colour_hls(T.adjust_lightness(grey, -50)[0])[1] == 64
    assert T.adjust_saturation(grey, 100) == (grey, True)  # a grey stays grey
    assert T.adjust_lightness(grey, 101) == (grey, False)  # outside -100..100
    assert T.is_grey(grey) and not T.is_grey(red)
    assert T.grey_in_range(red, 0, 0)[0] == 0x00808080


def test_swap_range_adjustment_is_ctdraws():
    """From's hue alone, turned to To's hue; grey <-> colour
    by a saturation delta; no Swap range for flashing or transparent From."""
    a = T.swap_adjustment(0xFF0000, 0x00FF00)
    assert (a.hue_from, a.hue_to, a.hue_delta, a.swap_saturation) == (0, 0, 1200, 0)
    a = T.swap_adjustment(0x808080, 0x0000FF)
    assert (a.hue_delta, a.swap_saturation) == (2400, 255)
    a = T.swap_adjustment(0x0000FF, 0x808080)
    assert (a.hue_from, a.swap_saturation) == (2400, -255)
    assert T.swap_adjustment((0xFF0000, 0x000000), 0x00FF00) is None
    assert T.swap_adjustment(0xFF0000, (0x00FF00, 0)) is None
    assert T.swap_adjustment(T.TRANSPARENT, 0x00FF00) is None
    # every shade of red turns green, keeping its lightness
    adj = T.swap_adjustment(0xFF0000, 0x00FF00)
    dark, light = T.adjust_pair((0x800000, 0xFF8080), adj)
    assert T.colour_hls(dark)[0] == 1200 and T.colour_hls(light)[0] == 1200
    assert T.colour_hls(dark)[1] == T.colour_hls(0x800000)[1]
    assert T.adjust_pair((0x0000FF, 0x0000FF), adj) == (0x0000FF, 0x0000FF)  # other hue
    # grey -> blue: every grey takes blue's hue and saturation
    g = T.adjust_pair((0x404040, 0xC0C0C0), T.swap_adjustment(0x808080, 0x0000FF))
    assert g == (0x000080, 0x8181FF)


def test_adjust_object_and_bitmap_orders():
    """Objects: the global lightness first, 'Selected Hues Only' only with a
    hue change; bitmaps: lightness / saturation passes in the
    range whatever the hue delta."""
    adj = T.Adjustment.from_dialog(0, 30, 0, 50, 0, light_selected=True)
    assert adj.hue_delta == 0 and adj.lightness_selected
    red = 0x00800000
    assert T.adjust_pair((red, red), adj) == (red, red)  # object: no hue change -> nothing
    lit = T.adjust_pixel_fn(adj)(red)
    assert T.colour_hls(lit)[1] > T.colour_hls(red)[1]  # bitmap: lighter
    assert T.adjust_pixel_fn(adj)(0x000080) == 0x000080  # out of the hue range
    assert T.adjust_pixel_fn(adj)(bitmaps.CLEAR_PIXEL) == bitmaps.CLEAR_PIXEL
    full = T.Adjustment.from_dialog(0, 359, 120, 0, 0)
    assert T.adjust_colour(0xFF0000, full) == 0x01FF01


def test_dialog_bars():
    bar = T.hue_bar(0, 60)
    assert T.colour_hls(bar[30])[2] == 0xEB and T.colour_hls(bar[200])[2] == 0x40
    wrap = T.hue_bar(300, 30)  # wraps: [30, 300] grey
    assert T.colour_hls(wrap[10])[2] == 0xEB and T.colour_hls(wrap[100])[2] == 0x40
    ramp = T.lightness_ramp(0xFF0000, 256)
    assert ramp[0] == 0xFFFFFF and ramp[-1] == 0x000000


# ================================================================== the palette
def test_palette_is_citect_pal(fx_install, tmp_path):
    """The picker's grid IS the project's citect.pal: 256 entries, the first
    11 rows standard (Transparent at 160), favourites after; CtDraw's save
    format byte for byte."""
    proj, _page = _project(fx_install, tmp_path)
    pal = CP.Palette.for_project(proj)
    assert pal.source == "project" and len(pal) == 256
    assert pal[0] == (0, 0, "Black") and pal.transparent_index() == 160
    assert pal[160].transparent and pal.is_standard(175) and not pal.is_standard(176)
    assert any(e.flashing for _i, e in pal.favourites())  # Fx Flash Red
    assert CP.format_entries(pal.entries) == _palette_bytes()
    # the lines CtDraw saves: the header, then on R G B A, off R G B A as %03d and the
    # quoted name, CRLF ('Transparent' is 255 x 8)
    lines = _palette_bytes().split(b"\r\n")
    assert lines[0] == b'"Citect.Pal Version 2"' and lines[-1] == b"" and len(lines) == 258
    assert lines[1] == b'000 000 000 000 000 000 000 000 "Black"'
    assert lines[161] == b'255 255 255 255 255 255 255 255 "Transparent"'
    assert lines[178] == b'255 000 000 000 000 000 000 000 "Fx Flash Red"'


def test_palette_favourite_rules(fx_install, tmp_path):
    proj, _page = _project(fx_install, tmp_path)
    pal = CP.Palette.for_project(proj)
    first_free = pal.find_unused()
    assert first_free >= CP.STANDARD
    i = pal.add(0x123456, 0x654321, "Pump running")
    assert i == first_free and pal[i] == (0x123456, 0x654321, "Pump running")
    j = pal.add(0x00AA00)
    assert pal[j].name.startswith("New Color (")  # a unique name
    with pytest.raises(CP.PaletteError, match="already exists"):
        pal.add(0x111111, None, "pump RUNNING")
    with pytest.raises(CP.PaletteError, match="reserved"):
        pal.add(0x111111, None, "Unused")
    with pytest.raises(CP.PaletteError, match="double quote"):
        pal.add(0x111111, None, 'a "b"')
    with pytest.raises(CP.PaletteError, match="standard colours"):
        pal.replace(3, 0x111111)
    pal.replace(i, T.TRANSPARENT, 0x00FF00, "")  # Verify: no mixed pair
    assert pal[i].pair == (T.TRANSPARENT, T.TRANSPARENT) and pal[i].name == "Pump running"
    pal.clear(i)
    assert not pal[i].used and pal[i].pair == (0x00FFFFFF, 0x00FFFFFF)
    full = pal.copy()
    while full.find_unused() != -1:
        full.add(0x010101 * (full.find_unused() & 0x7F))
    with pytest.raises(CP.PaletteError, match="no unused colour favourites"):
        full.add(0x222222)


def test_palette_save_like_ctdraw(fx_install, tmp_path):
    """The project's citect.pal is rewritten (the old one kept as
    citect_pal.bak); a project without its own palette
    gets one; a page outside any project has no palette file to write."""
    proj, _page = _project(fx_install, tmp_path)
    pal = CP.Palette.for_project(proj)
    i = pal.add(0x00C0FFEE, None, "Coffee")
    gen = CP.GENERATION
    path = pal.save()
    assert os.path.basename(path) == "citect.pal" and CP.GENERATION == gen + 1
    assert open(os.path.join(proj, "citect_pal.bak"), "rb").read() == _palette_bytes()
    again = CP.Palette.for_project(proj)
    assert again[i] == (0x00C0FFEE, 0x00C0FFEE, "Coffee")
    # the colour-array defaults follow the new palette (V1 colour order)
    table = CP.table_for_project(proj)
    assert (0x00C0FFEE, 0x00C0FFEE) in table
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "pages.dbf").write_bytes(b"")
    p2 = CP.Palette.for_project(str(bare))
    # (no palette of its own: the fake install's Bin\citect.pal)
    assert p2.source == "install" and p2.target_path().endswith("citect.pal")
    loose = tmp_path / "loose"
    loose.mkdir()
    p3 = CP.Palette.for_project(str(loose))
    assert p3.target_path() is None
    with pytest.raises(CP.PaletteError, match="not in a Plant SCADA project"):
        p3.save()


# ================================================================== Swap / Adjust on a page
def _rect(doc, colour=0xFF0000):
    return doc.create_object("rectangle", (10, 10, 80, 60), None, colour=colour)


def test_swap_colours_matches_pairs_and_keeps_animation(fx_install, tmp_path):
    """Swap Colors: each channel equal to From (both halves) takes To; COL_MAXON
    is not touched; one undo step."""
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    i = _rect(doc)
    doc.ensure_property(i, "COL_MAXON", "0x00ff0000,0x00ff0000")
    n = len(doc._undo)
    assert doc.swap_colours([i], 0xFF0000, 0x00FF00) == 1
    assert len(doc._undo) == n + 1
    s, _L = _slots(doc, i)
    assert s[G.SLOT_FILL] == (0x00FF00, 0x00FF00)
    assert doc.object_properties(i)["COL_MAXON"] == "0x00ff0000,0x00ff0000"
    assert doc.swap_colours([i], (0x00FF00, 0x000000), 0x0000FF) == 0  # the pair must match
    assert doc.swap_colours([], None, 0x0000FF) == 0  # empty selection: nothing
    doc.undo()
    assert _slots(doc, i)[0][G.SLOT_FILL] == (0xFF0000, 0xFF0000)


def test_flashing_pair_round_trips_through_colourstate(fx_install, tmp_path):
    """A flashing colour (Swap To a pair) is stored as the style-block pair,
    the colour-state array's state 0 follows, dwFlags 0x10 is set
    -- and it all survives a save and a reopen."""
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    i = _rect(doc)
    assert not doc.parsed.page.objects[i].subtype_size & 0x10
    assert doc.swap_colours([i], 0xFF0000, (0x00FF00, 0x0000FF)) == 1
    po = doc.parsed.page.objects[i]
    assert po.subtype_size & 0x10
    doc.save()
    again = _doc(page)
    j = next(k for k, o in enumerate(again.parsed.page.objects) if bytes(o.body_raw) == bytes(po.body_raw))
    s, L = _slots(again, j)
    assert s[G.SLOT_FILL] == (0x00FF00, 0x0000FF)
    assert struct.unpack_from("<II", again.parsed.page.objects[j].body_raw, L["state0_at"]) == (
        0x00FF00,
        0x0000FF,
    )
    assert again.parsed.page.objects[j].subtype_size & 0x10
    assert not colourstate.rebuild(again.parsed.page.objects[j])  # a fixed point


def test_swap_any_range_transparent_and_pipes(fx_install, tmp_path):
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    i = _rect(doc, 0x800000)
    k = _rect(doc, 0x0000FF)
    # Swap range: every shade of red's hue -> green's hue (lightness kept)
    assert doc.swap_colours([i, k], 0xFF0000, 0x00FF00, swap_range=True) == 1
    fill = _slots(doc, i)[0][G.SLOT_FILL][0]
    assert T.colour_hls(fill)[0] == 1200 and T.colour_hls(fill)[1] == T.colour_hls(0x800000)[1]
    assert _slots(doc, k)[0][G.SLOT_FILL] == (0x0000FF, 0x0000FF)
    # From any color
    assert doc.swap_colours([k], None, 0xABCDEF) == 1
    s, _L = _slots(doc, k)
    assert s[G.SLOT_FILL] == s[G.SLOT_LINE] == (0xABCDEF, 0xABCDEF)
    # Transparent: the palette's value, and it finds a hollow shape's 0xff000000 too
    assert doc.set_colour_slot(k, G.SLOT_FILL, T.TRANSPARENT)
    assert doc.page.objects[k].style.filled is False or doc.page.objects[k].style.fill_colour is None
    assert doc.set_colour_slot(k, G.SLOT_FILL, 0xFF000000)
    assert doc.swap_colours([k], T.TRANSPARENT, 0x112233) == 1
    assert _slots(doc, k)[0][G.SLOT_FILL] == (0x112233, 0x112233)
    # a pipe: its highlight is its colour (the load copies it to fill / line)
    p = doc.create_object("pipe", None, [(100, 100), (200, 100)], colour=0x00FF00)
    assert doc.swap_colours([p], 0x00FF00, (0xFF0000, 0x0000FF)) == 1
    s, _L = _slots(doc, p)
    assert s[G.SLOT_HILIGHT] == s[G.SLOT_FILL] == s[G.SLOT_LINE] == (0xFF0000, 0x0000FF)
    assert s[G.SLOT_LOLIGHT] == (0x7F0000, 0x00007F)


def test_adjust_colours_uses_ctdraws_arithmetic(fx_install, tmp_path):
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    i = _rect(doc, 0xFF0000)
    t = doc.create_object("text", (100, 100, 160, 120), None, colour=0x0000FF, text="Tag")
    adj = T.Adjustment.from_dialog(0, 359, 120, 0, 0)
    assert doc.adjust_colours([i, t], adj) == 2
    assert _slots(doc, i)[0][G.SLOT_FILL] == (0x01FF01, 0x01FF01)
    s, _L = _slots(doc, t)
    assert s[G.SLOT_FILL] == s[G.SLOT_LINE]  # a text's line = its fill
    assert T.colour_hls(s[G.SLOT_FILL][0])[0] == 0  # blue + 120 degrees = red
    assert doc.adjust_colours([i], T.Adjustment()) == 0  # nothing to do
    # a mapping function works too
    assert doc.adjust_colours([i], lambda c: 0x112233) == 1
    assert _slots(doc, i)[0][G.SLOT_FILL] == (0x112233, 0x112233)


RED, GREEN, BLUE, CLEAR = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (0, 0, 0, 0)


def test_bitmap_swap_like_ctdraw(fx_install, tmp_path):
    """the pixels of each flash state swap their own half; a
    flashing To gives a plain bitmap its flash picture; From any color fills
    the whole picture."""
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    pic = bytes(RED) * 3 + bytes(CLEAR)
    i = doc.add_bitmap_object((30, 40, 32, 42), pic, 2, 2)
    assert doc.swap_colours([i], 0xFF0000, (0x00FF00, 0x0000FF)) == 1
    p, f = doc.bitmap_pictures(i)
    assert p.rgba == bytes(GREEN) * 3 + bytes(CLEAR)
    assert f is not None and f.rgba == bytes(BLUE) * 3 + bytes(CLEAR)
    assert doc.parsed.page.objects[i].subtype_size & 0x10  # flashing = dynamic
    assert doc.swap_colours([i], None, 0x123456) == 1
    p, f = doc.bitmap_pictures(i)
    assert p.rgba == bytes((0x12, 0x34, 0x56, 255)) * 4 and f is None
    # Transparent From finds the masked pixels
    j = doc.add_bitmap_object((50, 40, 52, 42), pic, 2, 2)
    assert doc.swap_colours([j], T.TRANSPARENT, 0x00FF00) == 1
    assert doc.bitmap_pictures(j)[0].rgba == bytes(RED) * 3 + bytes(GREEN)


@pytest.mark.parametrize("page", ["FxGenies", "FxTplLinked"])
def test_whole_page_swap_leaves_genies_and_template_alone(fx_page, page):
    """indices None = the whole page; members of placed genies / symbols /
    the template keep their colours (CtDraw: an owned record refuses).
    FxGenies: placed genies (FxPump1 ...); FxTplLinked: the fx_style normal@XGA
    template, the genie FxTplPump and the free rectangle FxOwnRect."""
    doc = _doc(fx_page(page))
    objs = doc.parsed.page.objects
    owned = [i for i in range(len(objs)) if doc.colour_owned(i)]
    if page == "FxTplLinked":
        tpl, _ref = doc.template_reference()
        assert set(doc.subtree(tpl)[1:]) <= set(owned)  # the template's members
        pump = doc.find_objects_by_name("FxTplPump")[0]
        assert set(doc.subtree(pump)[1:]) <= set(owned)  # the genie's members
        assert not doc.colour_owned(doc.find_objects_by_name("FxOwnRect")[0])
    assert owned and all(objs[i].subtype_size & 0x201 or doc.colour_owned(i) for i in owned)
    before = {i: bytes(objs[i].body_raw) for i in owned}
    n = doc.swap_colours(None, None, 0x00FF00)
    assert n > 0
    assert all(bytes(doc.parsed.page.objects[i].body_raw) == before[i] for i in owned)


def test_selection_colour_is_the_swap_from(fx_install, tmp_path):
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    i = _rect(doc, 0x336699)
    assert doc.selection_colour([i]) == (0x336699, 0x336699)
    ln = doc.create_object("line", None, [(5, 5), (50, 50)], colour=0x445566)
    assert doc.selection_colour([ln]) == (0x445566, 0x445566)


def test_transparent_line_draws_nothing(fx_install, tmp_path):
    """A free line whose own line colour is Transparent draws no stroke."""
    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    ln = doc.create_object("line", None, [(5, 5), (50, 50)], colour=0x445566)
    assert doc.set_colour_slot(ln, G.SLOT_LINE, T.TRANSPARENT)
    st = doc.page.objects[ln].style
    assert st.line_colour is None and st.line_style == 5


# ================================================================== the picker UI
def test_picker_shows_the_project_palette_and_refuses_what_the_context_cannot_take(fx_install, tmp_path):
    _app()
    from ctgedit.ui.colour_picker import ColourPicker

    proj, _page = _project(fx_install, tmp_path)
    got = []
    pk = ColourPicker((0, 0), None, flash=False, transparent=False, project_dir=proj)
    pk.picked.connect(lambda a, b: got.append((a, b)))
    assert pk.palette_().source == "project" and pk.grid.selected() == 0
    assert pk.grid.allowed(160)  # Transparent refused
    flash = next(i for i, e in pk.palette_().favourites() if e.used and e.flashing)
    assert pk.grid.allowed(flash) == CP.MSG_NO_FLASHING
    assert not pk.pick_index(160) and not pk.pick_index(flash) and got == []
    assert pk.pick_index(15)  # White
    assert got == [(0xFFFFFF, 0xFFFFFF)] and pk.result == (0xFFFFFF, 0xFFFFFF)
    pk2 = ColourPicker(None, None, project_dir=proj)
    assert pk2.pick_index(160) and pk2.result == (T.TRANSPARENT, T.TRANSPARENT)
    assert pk2.recent_buttons  # the recents row


def test_recent_colours_are_remembered():
    _app()
    from ctgedit.ui import colour_picker as C

    C.remember_colour(0x123456)
    C.remember_colour(0x00FF00, 0x0000FF)
    C.remember_colour(0x123456)
    assert C.recent_colours()[:2] == [(0x123456, 0x123456), (0x00FF00, 0x0000FF)]


def test_transparent_swatch_is_a_checkerboard_and_flashing_splits():
    _app()
    from ctgedit.ui.colour_picker import swatch_pixmap

    img = swatch_pixmap(T.TRANSPARENT, None, 16, 16).toImage()
    px = {img.pixel(x, y) & 0xFFFFFF for x in range(1, 15) for y in range(1, 15)}
    assert {0xFFFFFF, 0xC8C8C8} <= px
    img = swatch_pixmap(0xFF0000, 0x0000FF, 16, 16).toImage()
    assert img.pixel(3, 3) & 0xFFFFFF == 0xFF0000 and img.pixel(12, 12) & 0xFFFFFF == 0x0000FF


def test_choose_colour_returns_the_pick(fx_install, tmp_path):
    _app()
    from ctgedit.ui import colour_picker as C

    proj, _page = _project(fx_install, tmp_path)
    host = QtWidgets.QWidget()
    host.show()

    def pick():
        C.ColourPicker.last_opened.pick_index(81)  # Lt Red

    QtCore.QTimer.singleShot(0, pick)
    assert C.choose_colour(host, 0x000000, anchor=host, project_dir=proj) == (0xFF0000, 0xFF0000)

    def close():
        C.ColourPicker.last_opened.hide()

    QtCore.QTimer.singleShot(0, close)
    assert C.choose_colour(host, 0x000000, anchor=host, project_dir=proj) is None
    host.close()


def test_eye_dropper_samples_the_rendered_page(fx_install, tmp_path):
    """The Color Selector takes the colour DRAWN at a page point (the scene
    render: no grid, no selection chrome)."""
    _app()
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.main_window import MainWindow

    proj, page = _project(fx_install, tmp_path)
    w = MainWindow()
    try:
        w.resize(1200, 800)
        w.show()
        w.open_page(page)
        i = w.doc.create_object("rectangle", (150, 40, 200, 90), None, colour=0x2468AC)
        w._refresh()
        _app().processEvents()
        assert w.scene.sample_colour(175, 65) == 0x2468AC
        bg = w.doc.page.appearance.background_colour & 0xFFFFFF
        assert w.scene.sample_colour(0, 60) == bg  # the page edge
        assert w.scene.sample_colour(-10, 5) is None
        vp = w.view.mapFromScene(QtCore.QPointF(175.5, 65.5))
        assert C.sample_view(w.view, vp) == 0x2468AC
        got = []
        d = C.EyeDropper(w.view.viewport())
        d.sampled.connect(got.append)
        d.start()
        assert d.active
        d.take(w.view.viewport().mapToGlobal(vp))
        assert not d.active and got in ([0x2468AC], [])  # offscreen widgetAt may miss
        assert i >= 0
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_the_same_picker_opens_from_ribbon_panel_and_sheet(fx_install, tmp_path, monkeypatch):
    _app()
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.popover import ColourPopover
    from ctgedit.ui.panels.properties import PropertyPanel
    from ctgedit.ui.objprops.colour_field import ColourPairField

    proj, page = _project(fx_install, tmp_path)
    calls = []

    def fake(parent, current=None, **kw):
        calls.append((type(parent).__name__, kw.get("flash", True), kw.get("transparent", True)))
        return (0x00FF00, 0x0000FF) if kw.get("flash", True) else (0x00FF00, 0x00FF00)

    monkeypatch.setattr(C, "choose_colour", fake)
    # the ribbon's drawing colour: the picker itself
    assert issubclass(ColourPopover, C.ColourPicker)
    w = MainWindow()
    try:
        w.open_page(page)
        pop = ColourPopover(w._draw_colour, w)
        assert pop.palette_().source == "project"  # the page's project palette
        # the Properties panel
        doc = w.doc
        i = _rect(doc, 0xFF0000)
        panel = PropertyPanel()
        panel.set_target(doc, [i])
        panel._on_object_colour(i, QtWidgets.QPushButton())
        assert calls[-1][1] is True
        assert _slots(doc, i)[0][G.SLOT_FILL] == (0x00FF00, 0x0000FF)
        panel._on_line_colour(i, QtWidgets.QPushButton())
        assert _slots(doc, i)[0][G.SLOT_LINE] == (0x00FF00, 0x0000FF)
        # the object sheet's colour fields
        f = ColourPairField("Fill colour", transparent=True)
        f.swatch.click()
        assert calls[-1][0] == "ColourPairField" and f.pair() == (0x00FF00, 0x0000FF)
        assert f.is_flashing()
        f.pick_transparent()
        assert f.pair() == (T.TRANSPARENT, T.TRANSPARENT)
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


# ================================================================== the dialogs
def test_edit_favorite_colors_dialog(fx_install, tmp_path, monkeypatch):
    _app()
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.dialog.colour_dialogs import FavoriteColorsDialog

    proj, _page = _project(fx_install, tmp_path)
    pal = C.palette_for(proj)
    dlg = FavoriteColorsDialog(pal, None, current=(0xFF0000, 0xFF0000))
    assert dlg.selected_index() == 81  # Lt Red, a standard colour
    assert not dlg.btn_replace.isEnabled() and not dlg.btn_clear.isEnabled()
    # HLS fields drive RGB through CtDraw's HLS to RGB (hue in degrees)
    dlg.spins["h"].setValue(120)
    assert dlg.result_pair() == (0x01FF01, 0x01FF01)
    dlg.spins["r"].setValue(0x10)
    dlg.spins["g"].setValue(0x20)
    dlg.spins["b"].setValue(0x30)
    assert dlg.result_pair() == (0x102030, 0x102030)
    assert dlg.spins["h"].value() == 210
    # a flashing favourite: tick Flashing, the Off State takes the next colour
    dlg.set_flashing(True)
    assert dlg.off_state.isChecked()
    dlg.set_rgb(0, 0, 0)
    assert dlg.result_pair() == (0x102030, 0x000000)
    dlg.name.setText("Blink")
    k = dlg.add()
    assert k >= CP.STANDARD and dlg.palette_()[k] == (0x102030, 0x000000, "Blink")
    assert dlg.btn_replace.isEnabled() and dlg.btn_clear.isEnabled()
    # Transparent: both halves, no flashing
    dlg.transparent.setChecked(True)
    assert dlg.result_pair() == (T.TRANSPARENT, T.TRANSPARENT) and not dlg.flashing.isEnabled()
    dlg.transparent.setChecked(False)
    assert not T.is_clear(dlg.result_pair()[0])
    assert dlg.palette_changed()
    # the picker's Edit... saves the project palette and returns the colour shown
    monkeypatch.setattr(
        FavoriteColorsDialog,
        "exec",
        lambda self: (self.set_rgb(0x0A, 0x0B, 0x0C), self.add(), QtWidgets.QDialog.Accepted)[-1],
    )
    assert C.edit_favourites(None, None, proj) == (0x0A0B0C, 0x0A0B0C)
    saved = CP.Palette.for_project(proj)
    assert saved.index_of(0x0A0B0C) >= CP.STANDARD
    assert os.path.exists(os.path.join(proj, "citect_pal.bak"))


def test_favourites_outside_a_project_go_to_the_settings(tmp_path):
    _app()
    from ctgedit.ui import colour_picker as C

    loose = tmp_path / "loose"
    loose.mkdir()
    pal = C.palette_for(str(loose))
    i = pal.add(0x0F0F0F, None, "Mine")
    note = C.save_palette(pal)
    assert "settings" in note
    assert C.palette_for(str(loose))[i] == (0x0F0F0F, 0x0F0F0F, "Mine")


def test_swap_dialog_follows_ctdraws_rules():
    """Swap range only for one colour each and not From any;
    the ramps show only while it applies; the values are remembered."""
    _app()
    from ctgedit.ui.dialog.colour_dialogs import SwapColorsDialog

    dlg = SwapColorsDialog(None, frm=(0x336699, 0x336699))
    assert dlg._frm.pair() == (0x336699, 0x336699)
    dlg.set_to((0x00FF00, 0x00FF00))
    dlg._chk_range.setChecked(True)
    assert dlg._chk_range.isEnabled() and dlg._frm_bar.isEnabled()
    assert dlg.values() == ((0x336699, 0x336699), (0x00FF00, 0x00FF00), True)
    dlg.set_to((0x00FF00, 0x000000))  # flashing To: no range
    assert not dlg._chk_range.isEnabled() and not dlg._frm_bar.isEnabled()
    assert dlg.values()[2] is False
    dlg.set_to((0x00FF00, 0x00FF00))
    dlg._chk_any.setChecked(True)
    assert not dlg._frm.isEnabled() and dlg.values()[0] is None
    dlg._ok()
    dlg2 = SwapColorsDialog(None)
    assert dlg2._to.pair() == (0x00FF00, 0x00FF00) and dlg2._chk_any.isChecked()


def test_adjust_dialog_hue_ranges():
    """From start moves the range, From
    end sets its length, the To end follows; OK builds the adjustment."""
    _app()
    from ctgedit.ui.dialog.colour_dialogs import AdjustColorsDialog

    dlg = AdjustColorsDialog(None)
    dlg.set_from_end(40)
    dlg.set_from_start(10)
    assert dlg.from_end() == 50  # the length (40) kept
    dlg.set_to_start(200)
    assert dlg.to_end() == 240 and dlg._to_end.value() == 240 and not dlg._to_end.isEnabled()
    dlg.set_from_end(0)  # wraps past 360
    assert dlg._range == 350
    dlg.set_lightness(25, True)
    dlg.set_saturation(-10)
    a = dlg.adjustment()
    assert (
        a.hue_from,
        a.hue_to,
        a.hue_delta,
        a.lightness,
        a.saturation,
        a.lightness_selected,
        a.saturation_selected,
    ) == (100, 0, 1900, 25, -10, True, False)
    grey = dlg._from_bar.colours()
    assert T.colour_hls(grey[5])[2] == 0x40 and T.colour_hls(grey[20])[2] == 0xEB
    assert AdjustColorsDialog.transform(0x00AABBCC, 0, 0, 0) == 0x00AABBCC


def test_swap_window_applies_to_the_selection_or_the_whole_page(fx_install, tmp_path, monkeypatch):
    """Swap Colours is modeless: 'Apply to' = the selection (n objects) or
    the whole page (nothing selected: only the whole page, no question box);
    the targets are read at Apply, the window stays open."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    _proj, page = _project(fx_install, tmp_path, palette=False)
    w = MainWindow()
    try:
        w.open_page(page)
        i = _rect(w.doc, 0xFF0000)
        w._refresh()
        w._set_selection([])
        asked = []
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "question",
            staticmethod(lambda *a, **k: (asked.append(a[2]), QtWidgets.QMessageBox.Cancel)[1]),
        )
        w._swap_colors()
        dlg = w._dialogs.get("swapcol")
        assert dlg is not None and not dlg.isModal() and not asked
        assert dlg.scope() == "page" and not dlg.apply_to.selection.isEnabled()
        assert _slots(w.doc, i)[0][G.SLOT_FILL] == (0xFF0000, 0xFF0000)
        dlg.set_from((0xFF0000, 0xFF0000))
        dlg.set_to((0x00FF00, 0x00FF00))
        dlg._chk_any.setChecked(False)
        dlg._chk_range.setChecked(False)
        n0 = len(w.doc._undo)
        dlg._apply()
        assert _slots(w.doc, i)[0][G.SLOT_FILL] == (0x00FF00, 0x00FF00)
        assert len(w.doc._undo) == n0 + 1 and dlg.isVisible()
        # a selection: 'Selection (1 object)' is chosen again
        w._set_selection([i])
        assert dlg.scope() == "selection" and "1 object" in dlg.apply_to.selection.text()
        dlg.reject()
        # with a selection the From colour is the selection's
        w._swap_colors()
        assert w._dialogs.get("swapcol")._frm.pair() == (0x00FF00, 0x00FF00)
        w._dialogs.get("swapcol").reject()
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_adjust_colours_window_reads_the_selection_at_apply(fx_install, tmp_path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    _proj, page = _project(fx_install, tmp_path, palette=False)
    w = MainWindow()
    try:
        w.open_page(page)
        i = _rect(w.doc, 0xFF0000)
        j = _rect(w.doc, 0xFF0000)
        w._refresh()
        w._set_selection([i])
        w._adjust_colors()
        dlg = w._dialogs.get("adjustcol")
        assert dlg is not None and not dlg.isModal() and dlg.scope() == "selection"
        dlg.set_from_start(0)
        dlg.set_from_end(0)
        dlg.set_to_start(120)
        w._set_selection([j])  # the selection moves on
        dlg._apply()
        assert _slots(w.doc, j)[0][G.SLOT_FILL] == (0x01FF01, 0x01FF01)
        assert _slots(w.doc, i)[0][G.SLOT_FILL] == (0xFF0000, 0xFF0000)
        assert dlg.isVisible()
        dlg.reject()
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)


def test_where_transparent_is_offered_follows_ctdraws_pages(fx_install, tmp_path, monkeypatch):
    """CtDraw's colour picker offers Transparent unless a page turns it off:
    a TEXT's foreground never, a shape's line
    and fill do -- in the Properties panel and in the object sheet alike (a
    Transparent line = no border)."""
    _app()
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.objprops import ObjectPropertiesDialog
    from ctgedit.ui.panels.properties import PropertyPanel

    _proj, page = _project(fx_install, tmp_path, palette=False)
    doc = _doc(page)
    r = _rect(doc, 0x336699)
    t = doc.create_object("text", (100, 100, 160, 120), None, colour=0x0000FF, text="Tag")
    seen = []

    def fake(parent, current=None, **kw):
        seen.append((kw.get("flash", True), kw.get("transparent", True)))
        return None

    monkeypatch.setattr(C, "choose_colour", fake)
    panel = PropertyPanel()
    panel.set_target(doc, [t])
    panel._on_object_colour(t, QtWidgets.QPushButton())
    assert seen[-1] == (True, False)  # a text flashes, never Transparent
    panel.set_target(doc, [r])
    panel._on_object_colour(r, QtWidgets.QPushButton())
    assert seen[-1] == (True, True)
    panel._on_line_colour(r, QtWidgets.QPushButton())
    assert seen[-1] == (True, True)
    # the object sheet: a Transparent line colour = no border
    dlg = ObjectPropertiesDialog(doc, r)
    assert dlg._col_line._transparent_offered and dlg._col_fill._transparent_offered
    dlg._col_line.pick_transparent()
    assert dlg.apply() is True
    assert _slots(doc, r)[0][G.SLOT_LINE] == (T.TRANSPARENT, T.TRANSPARENT)
    st = doc.page.objects[r].style
    assert st.line_colour is None and st.line_style == 5
    dlg.close()
    dlg = ObjectPropertiesDialog(doc, t)
    assert dlg._col_fore is not None and not dlg._col_fore._transparent_offered
    dlg.close()


def test_picker_releases_its_eye_dropper():
    """A finished eye dropper (sampled or cancelled) is stopped and let go --
    the ribbon makes a picker per click."""
    _app()
    from ctgedit.ui import colour_picker as C

    host = QtWidgets.QWidget()
    host.show()
    try:
        pk = C.ColourPicker((0, 0), host, project_dir=None)
        pk.start_dropper()
        d = pk._dropper
        assert d is not None and d.active
        d.cancelled.emit()  # Esc / a right click
        assert pk._dropper is None and not d.active
        pk.deleteLater()
    finally:
        # (let Qt delete the host and its children, the picker's dropper among them: one
        # the garbage collector deletes later can crash a following test's event loop)
        host.close()
        host.deleteLater()
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
