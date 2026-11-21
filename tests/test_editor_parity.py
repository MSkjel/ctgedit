"""Editor parity pins on the fake install."""

import os

import pytest

import fxinstall
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _read(fx_page, page, project="FxMain"):
    """The page read as the editor shows it (literal labels) and its document (for names)."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader

    path = fx_page(page, project)
    reader.set_label_mode("literal")
    try:
        pg = reader.read_page(path)
    finally:
        reader.set_label_mode("native")
    return pg, EditableDocument.open(path)


def _idx(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def _need(doc, page, *names):
    """Skip 'needs fixture' while the fake install has none of these objects."""
    missing = [n for n in names if not doc.find_objects_by_name(n)]
    if missing:
        pytest.skip("needs fixture: %s on %s (shared request)" % (", ".join(missing), page))


def _need_page(fx_page, page, project="FxMain"):
    try:
        return fx_page(page, project)
    except KeyError:
        pytest.skip("needs fixture: the page %s\\%s (shared request)" % (project, page))


def _render(page, box):
    from PySide6 import QtCore, QtGui
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(page)
    l, t, r, b = box
    img = QtGui.QImage(r - l, b - t, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor("#ffffff"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    sc.render(p, QtCore.QRectF(0, 0, r - l, b - t), QtCore.QRectF(l, t, r - l, b - t))
    p.end()
    return img


def _baked(unit):
    """CtDraw's baked static background of an oracle page (the .ctF 'Dynamic CTF Bitmap',
    record 0) as rows of 0xRRGGBB ints: bg[y][x]."""
    import struct
    import zlib
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

    class _Bg:
        def px(self, x, y):
            o = 40 + (y * w + x) * 4
            bl, g, r = raw[o], raw[o + 1], raw[o + 2]
            return (r << 16) | (g << 8) | bl

    return _Bg()


def _canvas(unit):
    """CtDraw's editor canvas of an oracle page (the unit's canvas.png: the canvas's client
    area at 100 %, the page's top left at its top left), read like :func:`_baked`."""
    from PySide6 import QtGui

    _app()
    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", unit, "canvas.png"))

    class _Canvas:
        def px(self, x, y):
            return img.pixel(x, y) & 0xFFFFFF

    return _Canvas()


# --------------------------------------------------------------------------- buttons
def test_xp_button_bitmap_matches_ctdraw_pixels():
    """CtDraw's XP button face, as measured on the live editor."""
    _app()
    from ctgedit.render.buttons import xp_button_image

    img = xp_button_image(114, 23)  # a 113x22 rect1 (inclusive + 1)
    px = lambda x, y: img.pixel(x, y) & 0xFFFFFF  # noqa: E731
    assert (img.pixel(0, 0) >> 24) == 0  # transparent corner
    assert px(30, 1) == 0x003C74 and px(30, 21) == 0x003C74  # top / bottom border
    assert px(1, 11) == 0x003C74 and px(112, 11) == 0x003C74  # left / right border
    assert px(30, 2) == 0xFFFFFF  # face top
    assert [px(30, y) for y in (18, 19, 20)] == [0xECEBE6, 0xE2DFD6, 0xD6D0C5]
    assert px(30, 0) == 0xE0DBC9  # halo


def test_xp_style_buttons_take_their_font(fx_page):
    """VIS_MODE=XP_STYLE -> the XP face; the caption font = the dwFlags 0x40 font block.
    FxColours' FxXpButton: an XP style button the generator gave Segoe UI 12."""
    pg, doc = _read(fx_page, "FxColours")
    o = pg.objects[_idx(doc, "FxXpButton")]
    assert o.raw.get("_button_mode") == 4
    assert o.style.font == "Segoe UI" and o.style.font_size == 12


# --------------------------------------------------------------------------- genie members
def test_art_stripped_status_box_takes_its_twin_line(fx_page):
    """A genie's status box (an art-stripped member): white state-0 fill + the library
    twin's 1px black frame."""
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxStatus1.FxStatusBox")
    st = pg.objects[_idx(doc, "FxStatus1.FxStatusBox")].style
    assert st.filled and st.fill_colour == 0xFFFFFF and st.line_colour == 0x000000


def test_hidden_when_true_page_objects_are_drawn(fx_page):
    """VISIBLE '1' (hidden-when TRUE) instrument tags are drawn by the editor: the scene keeps
    them (only composition-cache children fold constant gates)."""
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxStatus1.FxHiddenTag")
    from ctgedit.render.scene import PageScene

    i = _idx(doc, "FxStatus1.FxHiddenTag")
    assert doc.object_properties(i).get("VISIBLE") == "1"
    assert (pg.objects[i].style.text or "").startswith("-FX.T501/")
    sc = PageScene()
    sc.set_page(pg)
    assert i in sc._items


def test_genie_text_justify_follows_its_anchor(fx_page):
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxStatus1.FxHiddenTag")
    from ctgedit.model.document import TextJustify

    o = pg.objects[_idx(doc, "FxStatus1.FxHiddenTag")]
    assert o.style.justify is TextJustify.LEFT  # anchored at l


def test_state_text_shows_its_first_set_state(fx_page):
    """An art-stripped TXTMODE 1 text draws its first set STATE_n ('S'), not a mask."""
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxStatus1.FxStateText")
    assert pg.objects[_idx(doc, "FxStatus1.FxStateText")].style.text == "S"


# --------------------------------------------------------------------------- symbols
def test_page_symbol_set_draws_one_state(fx_page):
    """A symbol set on a page draws one state."""
    from ctgedit.render.scene import PageScene

    pg, doc = _read(fx_page, "FxSymbols")
    s = pg.objects[_idx(doc, "FxOnOffSet")]
    shown, other = s.members
    assert pg.objects[shown].raw.get("_ref", "").startswith("fxsym.fx_off")
    assert pg.objects[other].raw.get("_ref", "").startswith("fxsym.fx_on")
    (art,) = pg.objects[shown].members
    (hidden,) = pg.objects[other].members
    assert not pg.objects[shown].raw.get("_state_hidden")
    assert not pg.objects[art].raw.get("_state_hidden")
    assert pg.objects[other].raw.get("_state_hidden")
    assert pg.objects[hidden].raw.get("_state_hidden")
    sc = PageScene()
    sc.set_page(pg)
    assert art in sc._items and hidden not in sc._items


def test_anisotropic_symbol_keeps_its_local_scale(fx_page):
    """A stretched symbol keeps its local scale."""
    from ctgedit.render.scene import PageScene

    pg, doc = _read(fx_page, "FxMotion")
    sym = pg.objects[_idx(doc, "FxSymStretched")]
    assert sym.raw.get("_slot_scale") == pytest.approx((1.0, 1.5), abs=1e-3)
    (art,) = sym.members
    sc = PageScene()
    sc.set_page(pg)
    r = sc._items[art].sceneBoundingRect()
    assert (r.left(), r.top(), r.width(), r.height()) == (240, 200, 41, 46)
    assert (r.height() / 31) / (r.width() / 41) == pytest.approx(1.5, rel=0.03)


def test_cicode_object_shows_the_fx_badge(fx_page, monkeypatch):
    """A Cicode object (tc 0x1009) without a symbol shows the class icon at its top-left.
    The icon comes from CtRes32.DLL, which the fake install has not: a stand-in image
    takes its place (the scene's path is what is tested)."""
    pg, doc = _read(fx_page, "FxCicode")
    from PySide6 import QtGui
    from ctgedit.render import ctres
    from ctgedit.render.scene import PageScene

    icon = QtGui.QImage(22, 22, QtGui.QImage.Format_ARGB32)
    icon.fill(QtGui.QColor("#ff8000"))
    monkeypatch.setattr(ctres, "class_icon", lambda name: icon if name == "ADVSHOW" else None)
    i = _idx(doc, "FxCicodeIcon")
    assert pg.objects[i].raw.get("_class_icon") == "ADVSHOW"
    sc = PageScene()
    sc.set_page(pg)
    assert i in sc._items
    r = sc._items[i].sceneBoundingRect()
    g = pg.objects[i].geometry
    assert (round(r.left()), round(r.top())) == (round(g.x), round(g.y))


def test_text_block_colour_is_not_blanked(fx_page):
    """FxTextKinds' FxGreyText keeps its 0xCCCCCC text colour."""
    pg, doc = _read(fx_page, "FxTextKinds")
    assert pg.objects[_idx(doc, "FxGreyText")].style.text_colour == 0xCCCCCC


# --------------------------------------------------------------------------- pixels
#: FxColours' FxWidePipe: (300, 370) -> (400, 370) -> (400, 420), width 3, colour 0x00FFFF;
#: the column x = 350 crosses its horizontal leg at rows 369 .. 371
PIPE_X, PIPE_ROWS = 350, range(364, 378)


def test_pipe_shading_matches_ctdraw(fx_page):
    """A width-3 duct: rows lolight + (hilight - lolight) * (0, 70, 100) % (the width
    table's (3,0,0) (2,0,70) (1,0,100) entries; a yellow duct was #D8D800 / #FFFF00 /
    #7F7F00 in the live editor). FxWidePipe is cyan: #00D8D8 / #00FFFF / #007F7F."""
    pg, _doc = _read(fx_page, "FxColours")
    img = _render(pg, (PIPE_X - 10, PIPE_ROWS[0], PIPE_X + 10, PIPE_ROWS[-1] + 1))
    col = [img.pixel(10, y - PIPE_ROWS[0]) & 0xFFFFFF for y in PIPE_ROWS]
    page_colour = col[0]  # (the rows above and below the pipe)
    assert col[-1] == page_colour
    assert [c for c in col if c != page_colour] == [0x00D8D8, 0x00FFFF, 0x007F7F]


@pytest.mark.golden
def test_pipe_shading_matches_ctdraws_canvas(fx_page):
    """The same column in CtDraw's editor canvas of FxColours (canvas.png). Not its .ctF
    bake: FxWidePipe is named, so CtDraw keeps it a dynamic .ctF record and bakes only the
    page colour there (checked too)."""
    canvas, bake = _canvas("FxMain/FxColours"), _baked("FxMain/FxColours")
    pg, _doc = _read(fx_page, "FxColours")
    img = _render(pg, (PIPE_X - 10, PIPE_ROWS[0], PIPE_X + 10, PIPE_ROWS[-1] + 1))
    ours = [img.pixel(10, y - PIPE_ROWS[0]) & 0xFFFFFF for y in PIPE_ROWS]
    assert [canvas.px(PIPE_X, y) for y in PIPE_ROWS] == ours
    assert len({bake.px(PIPE_X, y) for y in PIPE_ROWS}) == 1


#: FxShapes' FxRectOutline: a hollow rectangle (140, 40, 240, 100) with a 1 px black pen
FRAME_X, FRAME_ROWS = 190, range(34, 47)


def test_value_field_border_is_one_crisp_pixel(fx_page):
    """GDI draws a 1px frame ON pixel l; ours must not smear it over two grey pixels."""
    pg, doc = _read(fx_page, "FxShapes")
    o = pg.objects[_idx(doc, "FxRectOutline")]
    assert not o.style.filled and o.style.line_colour == 0 and o.style.line_width == 1
    img = _render(pg, (FRAME_X - 10, FRAME_ROWS[0], FRAME_X + 10, FRAME_ROWS[-1] + 1))
    col = [img.pixel(10, y - FRAME_ROWS[0]) & 0xFFFFFF for y in FRAME_ROWS]
    assert col[40 - FRAME_ROWS[0]] == 0x000000  # the pure black border row is row l
    assert [c for c in col if c != col[0]] == [0x000000]  # one pixel, no grey smear


@pytest.mark.golden
def test_value_field_border_matches_ctdraws_canvas(fx_page):
    """The frame's column in CtDraw's editor canvas of FxShapes (canvas.png): the same
    single black row. Not its .ctF bake: FxRectOutline is named, so CtDraw keeps it a
    dynamic .ctF record and bakes only the page colour there (checked too)."""
    canvas, bake = _canvas("FxMain/FxShapes"), _baked("FxMain/FxShapes")
    pg, _doc = _read(fx_page, "FxShapes")
    img = _render(pg, (FRAME_X - 10, FRAME_ROWS[0], FRAME_X + 10, FRAME_ROWS[-1] + 1))
    ours = [img.pixel(10, y - FRAME_ROWS[0]) & 0xFFFFFF for y in FRAME_ROWS]
    assert [canvas.px(FRAME_X, y) for y in FRAME_ROWS] == ours
    assert len({bake.px(FRAME_X, y) for y in FRAME_ROWS}) == 1


# --------------------------------------------------------------------------- templates
def test_duplicate_template_copies_draw_their_nav_arrows(fx_page):
    """A page placing its chrome template three times; the later (art-stripped) copies of
    the nav arrow leaf draw their owner's arrow too, instead of drawing an empty slot over
    the first copy's arrow. The owner is found by CtDraw's load linkage (the linked leaf's
    element root, a 16 x 16 picture)."""
    path = _need_page(fx_page, "FxTplTriple")
    pg, doc = _read(fx_page, "FxTplTriple")
    _need(doc, path, "FxChrome1.FxNavArrow", "FxChrome2.FxNavArrow", "FxChrome3.FxNavArrow")
    for k in (1, 2, 3):
        i = _idx(doc, "FxChrome%d.FxNavArrow" % k)
        o = pg.objects[i]
        assert o.children or o.raw.get("_embedded_bmp") is not None, i


def test_page_tab_takes_frame_and_gradient_from_its_element(fx_page):
    """A template-prefix genie whose page copy lost its ref: the header band is the
    element's T>B 0x999999 -> 0xBFBFBF gradient and the hotspot rect is the black 1px
    frame."""
    path = _need_page(fx_page, "FxTplTriple")
    pg, doc = _read(fx_page, "FxTplTriple")
    _need(doc, path, "FxPageTab.FxTabBand", "FxPageTab.FxTabHotspot")
    band = pg.objects[_idx(doc, "FxPageTab.FxTabBand")].style
    assert band.gradient_mode == 4 and band.gradient_colour == 0x999999
    frame = pg.objects[_idx(doc, "FxPageTab.FxTabHotspot")].style
    assert frame.line_colour == 0x000000 and not frame.filled


def test_linked_symbol_members_take_their_element_appearance(fx_page):
    """An info button = a symbol linked INSIDE the chrome template: its stripped face takes
    the element's black frame."""
    path = _need_page(fx_page, "FxTplTriple")
    pg, doc = _read(fx_page, "FxTplTriple")
    _need(doc, path, "FxChrome1.FxInfoFace")
    st = pg.objects[_idx(doc, "FxChrome1.FxInfoFace")].style
    assert st.filled and st.fill_colour == 0x0080FF and st.line_colour == 0x000000


def test_grafted_bitmap_turns_with_its_member(fx_page):
    """A twin's DIB is in the element frame: the 19x19 picture stretched into its 19x31
    element rect, then turned by the member's quarter-turn -> fills the 31x19 page box."""
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxTurnedPic1.FxPic")
    from ctgedit.render.scene import PageScene

    i = _idx(doc, "FxTurnedPic1.FxPic")
    o = pg.objects[i]
    assert o.raw.get("_bmp_grafted") and o.raw.get("_local_xform") == (0, 1, -1, 0)
    sc = PageScene()
    sc.set_page(pg)
    r = sc._items[i].sceneBoundingRect()
    assert (round(r.left()), round(r.top()), round(r.width()), round(r.height())) == (
        round(o.geometry.x),
        round(o.geometry.y),
        o.geometry.extent_x,
        o.geometry.extent_y,
    )
    assert (o.geometry.extent_x, o.geometry.extent_y) == (31, 19)


def test_element_bitmap_ignores_its_local_translation(fx_page):
    """A busy spinner element bitmap whose slot-1 H,K is (-587, 3): its art stays in its
    placement box (off the page edge), not 258px inside the page. A stripped caption shows
    the INSTANCE's value for the twin's %Desc% token."""
    path = _need_page(fx_page, "FxParity")
    pg, doc = _read(fx_page, "FxParity")
    _need(doc, path, "FxSpinner1.FxSpinPic")
    from ctgedit.render.scene import PageScene

    i = _idx(doc, "FxSpinner1.FxSpinPic")
    sc = PageScene()
    sc.set_page(pg)
    r = sc._items[i].sceneBoundingRect()
    assert r.left() >= pg.appearance.width + 5
    assert not any("%Desc%" in (o.style.text or "") for o in pg.objects)
