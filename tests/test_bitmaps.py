"""The Bitmap Editor and picture import."""

import glob
import os
import shutil
import struct
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import bitmaps, colourstate, factory, geometry as G, placement  # noqa: E402

import fxinstall  # noqa: E402
from qtkit import app as _app
from timing import check_speed

#: the page the document and window tests edit (a copy in fx_install)
HOST = "FxShapes"

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
CLEAR = (0, 0, 0, 0)


def _px(colour, n):
    return bytes(colour) * n


def _doc(fx_install):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page(HOST))
    doc.derive_check = True
    return doc


def _flashing_record(path):
    """The first flashing bitmap record of the library ``path``."""
    from ctgedit.format.symbols import open_library

    lib = open_library(path)
    for name in lib.element_names():
        for po in lib.element_objects(name) or []:
            sec = bitmaps.dib_section(po)
            if sec is not None and sec.flash is not None:
                return po
    return None


def _bitmap_groups(pages=(), libraries=()):
    """The records of each page path and of each element of each library path."""
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.format.symbols import open_library

    groups = []
    for path in pages:
        pg = parse_ctg_bytes(open(path, "rb").read())
        groups.append(pg.page.objects if pg.page else [])
    for path in libraries:
        lib = open_library(path)
        groups += [lib.element_objects(e) or [] for e in lib.element_names()]
    return groups


def _put_back_every_picture(groups):
    """Put each bitmap's own pictures back; every record must keep its bytes. Returns
    (bitmaps, flashing ones)."""
    n = flashing = 0
    for objs in groups:
        for po in objs:
            if bitmaps.dib_section(po) is None:
                continue
            p, f = bitmaps.read_pictures(po)
            before = po.body_raw
            assert bitmaps.set_pictures(po, p.rgba, p.width, p.height, f.rgba if f is not None else None)
            assert po.body_raw == before
            n += 1
            flashing += f is not None
    return n, flashing


def _edit_one_state_keeps_the_rest(po):
    assert po is not None
    sec = bitmaps.dib_section(po)
    p, f = bitmaps.read_pictures(po)
    before = po.body_raw
    new = bytearray(p.rgba)
    new[0:4] = bytes(GREEN)
    assert bitmaps.set_pictures(po, bytes(new), p.width, p.height, f.rgba)
    sec2 = bitmaps.dib_section(po)
    assert sec2.flags == 0x101 and sec2.flash == sec.flash  # flash bytes kept
    assert sec2.primary != sec.primary
    assert po.body_raw[: sec.start] == before[: sec.start]  # bags, name, rect
    p2, f2 = bitmaps.read_pictures(po)
    assert p2.rgba[:4] == bytes(GREEN) and f2.rgba == f.rgba


def _metadata_bitmaps_have_their_class_section_past_the_bag(objs) -> int:
    hits = 0
    for po in objs:
        sec = bitmaps.dib_section(po)
        if sec is None or not po.subtype_size & 0x100:
            continue
        assert placement.class_section_offset(po) == sec.rect_at
        hits += 1
    return hits


# ------------------------------------------------------------------ format level
def test_encode_dib_is_the_factory_encoding():
    """The fast encoder writes exactly the bytes of a per-pixel loop
    (the factory bitmap is an engine fixed point)."""
    rgba = bytes(range(256)) * 4  # 256 pixels, every alpha value
    ref = bytearray(struct.pack("<IiiHHIIiiII", 40, 16, -16, 1, 32, 0, 0, 0, 0, 0, 0))
    for i in range(256):
        r, g, b, a = rgba[i * 4 : i * 4 + 4]
        ref += bytes((b, g, r, 0x00 if a >= 128 else 0xFF))
    assert bitmaps.encode_dib(rgba, 16, 16) == bytes(ref)
    assert factory._encode_dib(rgba, 16, 16) == bytes(ref)


def test_unchanged_pictures_leave_every_fixture_bitmap_byte_identical(fx_install):
    """Putting a bitmap's own pictures back changes nothing -- the deflate stream, the
    flash DIB and the extent of a stretched bitmap stay. Over every page and library of the
    fake install: 19 bitmaps, 4 of them flashing (fxsym.fx_flash, fxmore.fx_flash2, the
    flash@-XGA template's and FxShapes' FxFlashBitmap)."""
    pages = sorted(glob.glob(os.path.join(fx_install.user, "*", "*.ctg")))
    libs = sorted(glob.glob(os.path.join(fx_install.user, "*", "*.ct[lmt]")))
    n, flashing = _put_back_every_picture(_bitmap_groups(pages, libs))
    assert n >= 19 and flashing >= 4


@pytest.mark.golden
def test_unchanged_pictures_leave_every_ctdraw_bitmap_byte_identical():
    """The same over the bitmaps CtDraw wrote: its PageOpen + PageSave of the bitmap pages
    and its save of the bitmap libraries (its own deflate streams and flash DIBs)."""
    pages = [
        fxinstall.oracle("pages", "FxMain/" + p, "page.ctg")
        for p in ("FxShapes", "FxSymbols", "FxSymbols2", "FxTplFlash")
    ]
    libs = [
        fxinstall.oracle("elements", "FxLib/fxsym.ctl", "library.ctl"),
        fxinstall.oracle("elements", "FxLib/fxmore.ctl", "library.ctl"),
        fxinstall.oracle("elements", "FxStyle/fx_alt.ctt", "library.ctt"),
    ]
    n, flashing = _put_back_every_picture(_bitmap_groups(pages, libs))
    assert n >= 11 and flashing >= 4  # (11 and 4 in the fixture files)


def test_editing_one_state_of_a_flashing_bitmap_keeps_the_rest(fx_install):
    """fxsym.fx_flash (a flashing bitmap: DIB flags 0x101, a second DIB)."""
    _edit_one_state_keeps_the_rest(_flashing_record(fx_install.file("FxLib", "fxsym.ctl")))


@pytest.mark.golden
def test_editing_one_state_of_a_ctdraw_flashing_bitmap_keeps_the_rest():
    """The same on fx_flash as CtDraw saved fxsym.ctl."""
    _edit_one_state_keeps_the_rest(
        _flashing_record(fxinstall.oracle("elements", "FxLib/fxsym.ctl", "library.ctl"))
    )


def test_flash_equal_to_primary_is_dropped_and_size_moves_the_extent():
    red = _px(RED, 16)
    po = factory.make_bitmap_record(3, (40, 50, 44, 54), red, 4, 4, flash_rgba=_px(BLUE, 16))
    assert bitmaps.dib_section(po).flags == 0x101
    assert bitmaps.set_pictures(po, red, 4, 4, red)
    assert bitmaps.dib_section(po).flags == 0x001
    big = _px(GREEN, 6 * 3)
    assert bitmaps.set_pictures(po, big, 6, 3)
    gh = G.parse_geom_header(po.body_raw)
    assert tuple(gh.rect1) == (40, 50, 46, 53) == tuple(gh.rect2)  # top-left kept
    assert tuple(gh.anchor) == (40, 52)
    assert tuple(placement.native_rect(po)) == (40, 50, 46, 53)
    # exactly what the factory makes for that picture
    assert po.body_raw == factory.make_bitmap_record(3, (40, 50, 46, 53), big, 6, 3).body_raw


def test_a_bitmap_without_a_class_rect_has_none_quietly():
    """Most unlinked bitmaps of the corpus end where the class rect would start: native_rect
    is None for them without a swallowed error; a damaged record is still noted."""
    from ctgedit.infra import logsetup
    import dataclasses

    po = factory.make_bitmap_record(3, (40, 50, 44, 54), _px(RED, 16), 4, 4)
    cut = (
        dataclasses.replace(po, body_raw=po.body_raw[: placement.class_section_offset(po)])
        if dataclasses.is_dataclass(po)
        else None
    )
    if cut is None:
        po.body_raw = po.body_raw[: placement.class_section_offset(po)]
        cut = po
    before = sum(
        n for (where, _t), n in logsetup.swallowed_totals().items() if where == "placement.native_rect"
    )
    assert placement.has_native_rect(cut) and placement.native_rect(cut) is None
    after = sum(
        n for (where, _t), n in logsetup.swallowed_totals().items() if where == "placement.native_rect"
    )
    assert after == before


def test_class_section_follows_the_metadata_bag(fx_install):
    """dwFlags 0x100: the loader reads the metadata bag after
    the style block and BEFORE the class section -- the native rect / DIB
    section of a bitmap with metadata (FxSymbols2's FxBmp3) sits past it."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_install.page("FxSymbols2"))
    i = doc.find_objects_by_name("FxBmp3")[0]
    po = doc.parsed.page.objects[i]
    assert po.subtype_size & 0x100 and bitmaps.dib_section(po) is not None
    assert _metadata_bitmaps_have_their_class_section_past_the_bag([po]) == 1


@pytest.mark.golden
def test_class_section_follows_the_metadata_bag_as_ctdraw_writes_it():
    """The same on CtDraw's save of FxSymbols2 (its record shape)."""
    from ctgedit.format.page import parse_ctg_bytes

    pg = parse_ctg_bytes(fxinstall.oracle_bytes("pages", "FxMain/FxSymbols2", "page.ctg"))
    assert _metadata_bitmaps_have_their_class_section_past_the_bag(pg.page.objects)


def test_resample_is_nearest_neighbour():
    two = _px(RED, 1) + _px(BLUE, 1)  # 2 x 1
    out = bitmaps.resample(two, 2, 1, 4, 2)
    px = [out[i * 4 : i * 4 + 4] for i in range(8)]
    assert px == [bytes(RED)] * 2 + [bytes(BLUE)] * 2 + [bytes(RED)] * 2 + [bytes(BLUE)] * 2


def _placeable_wmf(w_units=2880, h_units=1440, inch=1440, with_ext=False):
    """A small placeable WMF: header + a Rectangle (+ optional window ext) + EOF."""
    recs = b""
    if with_ext:
        recs += struct.pack("<IH2h", 5, 0x020C, h_units, w_units)
    recs += struct.pack("<IH4h", 7, 0x041B, h_units, w_units, 0, 0)  # Rectangle(b,r,t,l)
    recs += struct.pack("<IH", 3, 0)
    size = (18 + len(recs)) // 2
    hdr = struct.pack("<HHHIHIH", 2, 9, 0x300, size, 0, 7, 0)
    ph = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, w_units, h_units, inch, 0)
    chk = 0
    for k in range(0, 20, 2):
        chk ^= struct.unpack_from("<H", ph, k)[0]
    return ph + struct.pack("<H", chk) + hdr + recs


def test_wmf_from_file_places_a_placeable_metafile():
    wmf, w, h = bitmaps.wmf_from_file(_placeable_wmf())
    assert (w, h) == (192, 96)  # 2 x 1 inch at 96 dpi
    assert struct.unpack_from("<HH", wmf, 0) == (1, 9)  # a memory metafile
    words = struct.unpack_from("<I", wmf, 6)[0]
    assert words * 2 == len(wmf)
    fns = [fn for _o, _s, fn in bitmaps._records(wmf, 9)]
    assert fns[:2] == [0x020B, 0x020C] and fns[-1] == 0  # window org / ext added
    # a metafile that states its window extent is left as it is
    wmf2, w2, h2 = bitmaps.wmf_from_file(_placeable_wmf(with_ext=True))
    assert [fn for _o, _s, fn in bitmaps._records(wmf2, 9)][0] == 0x020C
    with pytest.raises(bitmaps.MetafileError):
        bitmaps.wmf_from_file(b"GIF89a" + bytes(40))


def test_metafile_record_round_trips():
    from ctgedit.format.page import _parse_object
    from ctgedit.format import writer

    wmf, w, h = bitmaps.wmf_from_file(_placeable_wmf())
    po = factory.make_metafile_record(7, (10, 20, 10 + w, 20 + h), wmf)
    assert po.type_code == 0x09 and po.subtype_size == 0x0A
    blob = writer.object_payload(po)
    back = _parse_object(0, 0, len(blob), 0, blob)
    assert bitmaps.metafile_bits(back) == wmf
    assert tuple(placement.native_rect(back)) == (10, 20, 10 + w, 20 + h)
    gh = G.parse_geom_header(back.body_raw)
    assert tuple(gh.anchor) == (10, 20 + h - 1)


# -------------------------------------------------------------- document level
def test_editing_a_flashing_bitmap_keeps_flash_bags_and_identity(fx_install):
    """The acceptance: flag 0x101 and the second DIB stay, the VISIBLE bag
    survives, and nothing but the primary DIB stream changes."""
    doc = _doc(fx_install)
    i = doc.add_bitmap_object((30, 40, 34, 44), _px(RED, 16), 4, 4, flash_rgba=_px(BLUE, 16))
    assert doc.ensure_property(i, "VISIBLE", "Pump_Running")
    po = doc.parsed.page.objects[i]
    handle, before = G.get_handle(po), po.body_raw
    sec = bitmaps.dib_section(po)
    assert po.subtype_size & 0x10  # flashing = dynamic
    new = _px(GREEN, 16)
    assert doc.replace_bitmap(i, (30, 40, 34, 44), new, 4, 4)  # flash kept by default
    po = doc.parsed.page.objects[i]
    sec2 = bitmaps.dib_section(po)
    assert sec2.flags == 0x101 and sec2.flash == sec.flash
    assert po.body_raw[: sec.start] == before[: sec.start]
    assert doc.object_properties(i).get("VISIBLE") == "Pump_Running"
    assert G.get_handle(po) == handle and po.subtype_size & 0x10
    p, f = doc.bitmap_pictures(i)
    assert p.rgba == new and f.rgba == _px(BLUE, 16)
    # the saved page reads back the same
    from ctgedit.format.page import parse_ctg_bytes

    again = parse_ctg_bytes(doc.serialize()).page.objects[i]
    assert again.body_raw == po.body_raw
    doc.undo()
    assert doc.parsed.page.objects[i].body_raw == before
    doc.redo()
    assert bitmaps.dib_section(doc.parsed.page.objects[i]).flash == sec.flash


def test_resizing_a_bitmap_with_colour_state_sections_keeps_its_style_block(fx_install):
    """Resizing a bitmap with colour-state sections keeps its style block."""
    doc = _doc(fx_install)
    i = doc.add_bitmap_object((30, 40, 34, 44), _px(RED, 16), 4, 4)
    assert doc.ensure_property(i, "COL_MAXON", "0x0000ff00,0x0000ff00")
    po = doc.parsed.page.objects[i]
    L = colourstate.layout(po)
    assert L["flag"] & 0x2
    sec = bitmaps.dib_section(po)
    assert placement.class_section_offset(po) == sec.rect_at
    assert placement.walk(po)["after_cs"] + 54 != sec.rect_at  # the plain walk: short
    style = po.body_raw[L["style_at"] : L["style_at"] + 54]
    assert doc.replace_bitmap(i, None, _px(GREEN, 6 * 4), 6, 4)
    po = doc.parsed.page.objects[i]
    assert po.body_raw[L["style_at"] : L["style_at"] + 54] == style
    assert tuple(placement.native_rect(po)) == (30, 40, 36, 44)
    assert tuple(G.parse_geom_header(po.body_raw).rect1) == (30, 40, 36, 44)
    assert doc.bitmap_pictures(i)[0].rgba == _px(GREEN, 24)


def test_plain_flash_edit_makes_a_bitmap_flash_and_back(fx_install):
    doc = _doc(fx_install)
    i = doc.add_bitmap_object((30, 40, 34, 44), _px(RED, 16), 4, 4)
    assert not doc.parsed.page.objects[i].subtype_size & 0x10
    assert doc.replace_bitmap(i, (30, 40, 34, 44), _px(RED, 16), 4, 4, flash_rgba=_px(GREEN, 16))
    po = doc.parsed.page.objects[i]
    assert bitmaps.dib_section(po).flags == 0x101 and po.subtype_size & 0x10
    assert doc.replace_bitmap(i, (30, 40, 34, 44), _px(RED, 16), 4, 4, flash_rgba=None)
    po = doc.parsed.page.objects[i]
    assert bitmaps.dib_section(po).flags == 0x001 and not po.subtype_size & 0x10


def test_swap_and_adjust_colours_recolour_bitmap_pixels(fx_install):
    doc = _doc(fx_install)
    pic = _px(RED, 3) + _px(CLEAR, 1)
    i = doc.add_bitmap_object((30, 40, 32, 42), pic, 2, 2, flash_rgba=_px(RED, 4))
    assert doc.swap_colours([i], 0xFF0000, 0x00FF00) == 1
    p, f = doc.bitmap_pictures(i)
    assert p.rgba == _px(GREEN, 3) + _px(CLEAR, 1)  # transparent stays
    assert f.rgba == _px(GREEN, 4)  # both flash states
    assert doc.adjust_colours([i], lambda c: 0x0000FF if c == 0x00FF00 else c) == 1
    p, _f = doc.bitmap_pictures(i)
    assert p.rgba[:4] == bytes(BLUE)
    doc.undo()
    doc.undo()
    assert doc.bitmap_pictures(i)[0].rgba == pic


def test_metafile_object_renders(fx_install):
    doc = _doc(fx_install)
    wmf, w, h = bitmaps.wmf_from_file(_placeable_wmf())
    i = doc.add_metafile_object((100, 100, 100 + w, 100 + h), wmf)
    po = doc.parsed.page.objects[i]
    assert po.type_code == 0x09 and bitmaps.metafile_bits(po) == wmf
    if os.name == "nt":
        assert doc.page.objects[i].raw.get("_embedded_bmp") is not None


# ----------------------------------------------------------------- the editor
def _editor(rgba, w, h, flash=None):
    _app()
    from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog

    return BitmapEditorDialog(rgba, w, h, flash_rgba=flash)


def _click(canvas, x, y, button=QtCore.Qt.LeftButton):
    from PySide6 import QtTest

    z = canvas.zoom
    pos = QtCore.QPoint(x * z + z // 2, y * z + z // 2)
    QtTest.QTest.mousePress(canvas, button, QtCore.Qt.NoModifier, pos)
    QtTest.QTest.mouseRelease(canvas, button, QtCore.Qt.NoModifier, pos)


def _pixels(rgba):
    return [bytes(rgba[i : i + 4]) for i in range(0, len(rgba), 4)]


def test_editor_pen_right_button_transparent_and_eye_dropper():
    dlg = _editor(_px((255, 255, 255, 255), 16), 4, 4)
    c = dlg.canvas
    dlg.colour.pick(0xFF0000)
    _click(c, 1, 1)  # pen
    _click(c, 2, 2, QtCore.Qt.RightButton)  # right = transparent
    out, w, h = dlg.result_rgba()
    px = _pixels(out)
    assert px[5] == bytes(RED) and px[10] == bytes(CLEAR) and px[0] == b"\xff\xff\xff\xff"
    # Shift+P = the Eye Dropper (Color Select); one pick, then the pen again
    shift_p = [s for s in dlg.findChildren(QtGui.QShortcut) if s.key() == QtGui.QKeySequence("Shift+P")]
    assert len(shift_p) == 1
    shift_p[0].activated.emit()
    assert c.tool == "dropper" and dlg._tool_buttons["dropper"].isChecked()
    _click(c, 1, 1)
    assert dlg.colour.pair()[0] & 0xFFFFFF == 0xFF0000 and c.tool == "pen"
    shift_p[0].activated.emit()
    _click(c, 2, 2)  # pick the transparent one
    assert dlg.colour.is_transparent() and c.pair == (0, 0)
    assert dlg.modified() and dlg.result_flash() is None


def test_editor_flashing_colour_and_single_state_views():
    dlg = _editor(_px(RED, 4), 2, 2)
    c = dlg.canvas
    dlg.colour.pick(0x00FF00, 0x0000FF)  # a flashing colour pair
    _click(c, 0, 0)
    assert _pixels(dlg.result_rgba()[0])[0] == bytes(GREEN)
    assert _pixels(dlg.result_flash())[0] == bytes(BLUE)
    dlg.view.setCurrentIndex(dlg.view.findData("off"))
    dlg.colour.pick(0xFFFFFF)
    _click(c, 1, 0)  # the off state only
    assert _pixels(dlg.result_rgba()[0])[1] == bytes(RED)
    assert _pixels(dlg.result_flash())[1] == b"\xff\xff\xff\xff"
    # the dropper in 'both' picks the pixel's pair
    dlg.view.setCurrentIndex(dlg.view.findData("both"))
    dlg._set_tool("dropper")
    _click(c, 0, 0)
    assert dlg.colour.pair() == (0x00FF00, 0x0000FF)


def test_editor_fill_undo_redo():
    pic = _px(RED, 1) + _px(BLUE, 2) + _px(RED, 1)  # 2 x 2: red on one diagonal
    dlg = _editor(pic, 2, 2)
    c = dlg.canvas
    dlg._set_tool("fill")
    dlg.colour.pick(0x00FF00)
    _click(c, 0, 0)  # 4-connected: not the diagonal
    assert _pixels(dlg.result_rgba()[0]) == [bytes(GREEN), bytes(BLUE), bytes(BLUE), bytes(RED)]
    _click(c, 1, 0)  # the other blue is diagonal
    assert _pixels(dlg.result_rgba()[0]) == [bytes(GREEN)] * 2 + [bytes(BLUE), bytes(RED)]
    _click(c, 1, 1, QtCore.Qt.RightButton)  # right button fills transparent
    assert _pixels(dlg.result_rgba()[0])[3] == bytes(CLEAR)
    dlg.undo()
    dlg.undo()
    assert _pixels(dlg.result_rgba()[0])[1] == bytes(BLUE)
    dlg.undo()
    assert dlg.result_rgba()[0] == pic and not dlg.modified()
    dlg.redo()
    assert _pixels(dlg.result_rgba()[0])[0] == bytes(GREEN)


def test_editor_no_op_click_keeps_the_redo_history():
    """A click that changes no pixel (the colour it already has) is no undo
    step and does not throw away what Undo took back."""
    dlg = _editor(_px(RED, 4), 2, 2)
    c = dlg.canvas
    dlg.colour.pick(0x00FF00)
    _click(c, 0, 0)
    assert len(dlg._undo) == 1
    dlg.undo()
    assert dlg.result_rgba()[0] == _px(RED, 4) and len(dlg._redo) == 1
    dlg.colour.pick(0xFF0000)
    _click(c, 1, 1)  # red on red: nothing changes
    assert not dlg._undo and len(dlg._redo) == 1
    dlg.redo()
    assert _pixels(dlg.result_rgba()[0])[0] == bytes(GREEN)
    dlg.undo()
    dlg.colour.pick(0x0000FF)
    _click(c, 1, 0)  # a real change drops the redo
    assert not dlg._redo and len(dlg._undo) == 1


def test_bitmap_size_grows_and_shrinks_edges_without_scaling():
    pic = _px(RED, 1) + _px(BLUE, 1) + _px(GREEN, 1) + _px(RED, 1)
    dlg = _editor(pic, 2, 2)
    assert dlg.resize_edges(1, 0, 2, 1, grow=True)
    out, w, h = dlg.result_rgba()
    assert (w, h) == (5, 3)
    px = _pixels(out)
    assert px[0] == bytes(CLEAR) and px[1] == bytes(RED) and px[2] == bytes(BLUE)
    assert px[6] == bytes(GREEN) and px[14] == bytes(CLEAR)
    assert dlg.resize_edges(1, 0, 2, 1, grow=False)  # back to the picture
    assert dlg.result_rgba()[0] == pic
    assert not dlg.resize_edges(3, 0, 0, 0, grow=False)  # nothing left: refused
    from ctgedit.ui.dialog.bitmap_editor import BitmapSizeDialog

    sd = BitmapSizeDialog(2, 2)
    sd.shrink.setChecked(True)
    sd.sides["left"].setValue(2)
    ok = sd._bb.button(QtWidgets.QDialogButtonBox.Ok)
    assert not ok.isEnabled()
    sd.grow.setChecked(True)
    assert ok.isEnabled() and sd.new_size() == (4, 2)


def test_editor_repaint_of_an_800x500_flashing_bitmap_is_fast():
    """The acceptance: the 800x500 editor repaints in under 50 ms."""
    import numpy as np

    a = np.zeros((500, 800, 4), dtype=np.uint8)
    a[..., 0] = np.arange(800) % 256
    a[..., 3] = 255
    a[::7, ::5] = 0  # some transparent pixels
    f = a.copy()
    f[..., 1] = 200  # every pixel flashes
    dlg = _editor(a.tobytes(), 800, 500, flash=f.tobytes())
    dlg.resize(1000, 800)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    c = dlg.canvas
    for zoom in (1, 8):
        c.set_zoom(zoom)
        QtWidgets.QApplication.processEvents()
        vis = c.visibleRegion().boundingRect()
        assert vis.width() >= min(800 * zoom, 600) and vis.height() >= min(500 * zoom, 400)
        c.repaint(vis)  # warm the tile caches
        t0 = time.perf_counter()
        for _k in range(5):
            c.repaint(vis)
        per = (time.perf_counter() - t0) / 5
        check_speed(per, 0.05, "a repaint at zoom %d" % zoom)
    # one pixel stroke repaints just that pixel
    t0 = time.perf_counter()
    c.put(10, 10, (0xFF000000, 0xFF000000))
    QtWidgets.QApplication.processEvents()
    check_speed(time.perf_counter() - t0, 0.05, "a one-pixel stroke")
    dlg.hide()


# ------------------------------------------------------------------ the window
@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w._fx_host = fx_install.page(HOST)
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def _open(win, tmp_path):
    """Open the host page of the fx_install copy (``tmp_path`` holds the pictures the test
    writes)."""
    win.open_document(win._fx_host)
    win.act_snap.setChecked(False)
    win.doc.derive_check = True
    # these tests check CtDraw's import: Settings > Canvas 'never' opens no picture options
    # (tests/test_pictures.py has the options)
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/picture_options", "never")


def _png(tmp_path, name="pic.png", w=6, h=4, colour=(10, 20, 30), border=None):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(*colour))
    if border is not None:
        for x in range(w):
            img.setPixelColor(x, 0, QtGui.QColor(*border))
    p = str(tmp_path / name)
    assert img.save(p)
    return p


def test_import_filter_offers_every_qt_format_and_wmf():
    _app()
    from ctgedit.ui.dialog.bitmap_import import import_filter, importable_suffixes

    flt = import_filter()
    for s in ("png", "bmp", "jpg", "gif", "tif", "wmf"):
        assert "*." + s in flt
    assert "wmf" in importable_suffixes()
    for s in ("dib", "rle", "jif"):  # CtDraw's other bitmap / JPEG names
        assert "*." + s in flt


def test_a_dib_file_imports_as_a_bitmap(tmp_path):
    """*.DIB (a Windows bitmap under another name, CtDraw's Import list): Qt
    goes by the suffix, so it is read as BMP explicitly."""
    _app()
    from ctgedit.ui.dialog.bitmap_import import read_picture

    img = QtGui.QImage(3, 2, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(1, 2, 3))
    bmp = str(tmp_path / "a.bmp")
    assert img.save(bmp)
    dib = str(tmp_path / "a.dib")
    shutil.copyfile(bmp, dib)
    pic = read_picture(dib)
    assert (pic.kind, pic.width, pic.height) == ("bitmap", 3, 2)
    assert pic.data[:4] == bytes((1, 2, 3, 255))


def test_dropping_a_png_from_explorer_places_it_at_the_drop_point(win, tmp_path):
    _open(win, tmp_path)
    png = _png(tmp_path)
    n0 = len(win.doc.parsed.page.objects)
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(png)])
    view = win.view
    target = QtCore.QPointF(123, 77)
    vp = view.mapFromScene(target)
    enter = QtGui.QDragEnterEvent(vp, QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    view.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QtGui.QDropEvent(
        QtCore.QPointF(vp), QtCore.Qt.CopyAction, mime, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
    )
    view.dropEvent(drop)
    objs = win.doc.parsed.page.objects
    assert len(objs) == n0 + 1
    gh = G.parse_geom_header(objs[-1].body_raw)
    at = view.mapToScene(vp)
    assert tuple(gh.rect1[:2]) == (int(at.x()), int(at.y()))
    assert tuple(gh.rect1[2:]) == (gh.rect1[0] + 6, gh.rect1[1] + 4)
    assert win._sel == [len(objs) - 1]
    # several files: stepped down-right, ONE undo step; a text file is not offered
    other = _png(tmp_path, "b.png", colour=(90, 90, 90))
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    from ctgedit.ui.dialog.bitmap_import import dropped_files

    mime2 = QtCore.QMimeData()
    mime2.setUrls([QtCore.QUrl.fromLocalFile(p) for p in (png, other, str(txt))])
    assert dropped_files(mime2) == [png, other]
    n1 = len(objs)
    win._on_files_dropped(dropped_files(mime2), QtCore.QPointF(300, 200))
    objs = win.doc.parsed.page.objects
    assert len(objs) == n1 + 2
    tops = [tuple(G.parse_geom_header(o.body_raw).rect1[:2]) for o in objs[-2:]]
    assert tops == [(300, 200), (310, 210)]
    win.doc.undo()
    assert len(win.doc.parsed.page.objects) == n1


def test_ctrl_v_of_a_clipboard_picture_imports_it(win, tmp_path):
    _open(win, tmp_path)
    img = QtGui.QImage(5, 3, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(200, 10, 10))
    QtWidgets.QApplication.clipboard().setImage(img)
    n0 = len(win.doc.parsed.page.objects)
    win.act_paste.trigger()
    objs = win.doc.parsed.page.objects
    assert len(objs) == n0 + 1
    p, _f = win.doc.bitmap_pictures(len(objs) - 1)
    assert (p.width, p.height) == (5, 3) and p.rgba[:4] == bytes((200, 10, 10, 255))


def test_file_import_and_import_as_flashing(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    a = _png(tmp_path, "a.png", w=6, h=4, colour=(200, 0, 0))
    b = _png(tmp_path, "b.png", w=3, h=2, colour=(0, 0, 200))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileNames", staticmethod(lambda *a_, **k: ([a], "")))
    n0 = len(win.doc.parsed.page.objects)
    win._import_image()
    objs = win.doc.parsed.page.objects
    assert len(objs) == n0 + 1 and bitmaps.dib_section(objs[-1]).flash is None
    answers = iter([a, b])
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a_, **k: (next(answers), ""))
    )
    win._import_flashing()
    objs = win.doc.parsed.page.objects
    p, f = bitmaps.read_pictures(objs[-1])
    assert bitmaps.dib_section(objs[-1]).flags == 0x101
    assert (f.width, f.height) == (6, 4) and f.rgba[:4] == bytes((0, 0, 200, 255))
    # Paste Special > Device Independent Bitmap
    img = QtGui.QImage(2, 2, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(7, 8, 9))
    QtWidgets.QApplication.clipboard().setImage(img)
    from ctgedit.ui.dialog.dialogs import PasteSpecialDialog

    monkeypatch.setattr(PasteSpecialDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    monkeypatch.setattr(PasteSpecialDialog, "choice", lambda self: 0)
    win._paste_special()
    p, _f = win.doc.bitmap_pictures(len(win.doc.parsed.page.objects) - 1)
    assert (p.width, p.height) == (2, 2) and p.rgba[:4] == bytes((7, 8, 9, 255))


def test_transparent_paste_colour_clears_the_key_colour(win, tmp_path):
    from ctgedit.ui.dialog.bitmap_import import set_transparent_colour, transparent_colour

    _open(win, tmp_path)
    set_transparent_colour(0xFFFFFF)
    try:
        assert transparent_colour() == 0xFFFFFF
        png = _png(tmp_path, colour=(255, 255, 255), border=(1, 2, 3))
        new = win._import_files([png])
        p, _f = win.doc.bitmap_pictures(new[0])
        px = _pixels(p.rgba)
        assert px[0] == bytes((1, 2, 3, 255)) and px[6] == bytes(CLEAR)
    finally:
        set_transparent_colour(None)
    from ctgedit.ui.dialog.bitmap_import import TransparentPasteDialog

    dlg = TransparentPasteDialog(win)
    assert not dlg.enabled.isChecked() and dlg.value() is None


def test_import_of_a_wmf_file_places_a_metafile_object(win, tmp_path):
    _open(win, tmp_path)
    p = tmp_path / "shape.wmf"
    p.write_bytes(_placeable_wmf())
    new = win._import_files([str(p)])
    po = win.doc.parsed.page.objects[new[0]]
    assert po.type_code == 0x09
    gh = G.parse_geom_header(po.body_raw)
    assert (gh.rect1[2] - gh.rect1[0], gh.rect1[3] - gh.rect1[1]) == (192, 96)
    bad = tmp_path / "bad.wmf"
    bad.write_bytes(b"not a metafile at all" * 3)
    from ctgedit.ui import messages

    shown = []
    orig = messages.list_box
    messages.list_box = lambda parent, title, text, items, **k: shown.append((text, list(items)))
    try:
        assert win._import_files([str(bad)]) == []
    finally:
        messages.list_box = orig
    assert shown and "bad.wmf" in shown[0][1][0]


def test_flashing_commands_are_in_file_and_edit(win):
    menus = {m.title().replace("&", ""): m for m in win.menuBar().findChildren(QtWidgets.QMenu)}
    file_acts = menus["File"].actions()
    i = file_acts.index(win.act_import)
    assert file_acts[i + 1] is win.act_import_flash
    edit = menus["Edit"].actions()
    # CtDraw's Edit menu (docs/ctdraw_inventory.json): ... Paste Special, ...,
    # Paste Symbol (F6), Paste Symbol As Flashing, Paste Genie (F11)
    assert edit.index(win.act_paste_special) < edit.index(win.act_paste_flash)
    assert edit.index(win.act_paste_flash) == edit.index(win.act_paste_symbol) + 1
    assert win.act_import_flash not in menus["Advanced"].actions()
    assert win.act_paste_flash not in menus["Advanced"].actions()
    assert win.act_transparent_paste in menus["Colours"].actions()


def test_paste_symbol_as_flashing_uses_the_library_picker(win, tmp_path, monkeypatch, fx_install):
    from ctgedit.format.symbols import open_library

    _open(win, tmp_path)
    fxsym = fx_install.file("FxLib", "fxsym.ctl")
    lib = open_library(fxsym)
    names = [
        e
        for e in lib.element_names()
        if any(bitmaps.dib_section(po) is not None for po in lib.element_objects(e) or [])
    ][:2]
    assert len(names) == 2  # fx_bitmap and fx_flash
    picks = iter([(fxsym, names[0]), (fxsym, names[1])])
    titles = []

    def fake(parent, project_dir=None, kind="symbol", catalog=None, title=None):
        titles.append(title)
        return next(picks)

    from ctgedit.ui.browser import PageBrowserDialog

    monkeypatch.setattr(PageBrowserDialog, "get_library_element", staticmethod(fake))
    n0 = len(win.doc.parsed.page.objects)
    win._paste_symbol_flashing()
    objs = win.doc.parsed.page.objects
    assert len(objs) == n0 + 1
    assert titles[0] == "Select Primary Symbol" and "Flashing" in titles[1]
    sec = bitmaps.dib_section(objs[-1])
    p, f = bitmaps.read_pictures(objs[-1])
    assert sec.flags == 0x101 and (f.width, f.height) == (p.width, p.height)


def test_bitmap_editor_command_edits_the_record_in_place(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    i = win.doc.add_bitmap_object(
        (30, 40, 38, 44), _px(RED, 16), 4, 4, flash_rgba=_px(BLUE, 16)
    )  # stretched 2x
    win._sel = [i]
    win._refresh()
    before = win.doc.parsed.page.objects[i].body_raw
    win._bitmap_editor()  # modeless: one per bitmap
    dlg = win._dialogs.get(("bitmap", i))
    assert dlg is not None and not dlg.isModal()
    assert (dlg.canvas.primary.width(), dlg.canvas.primary.height()) == (8, 4)
    assert "stretched" in dlg._note.text()
    win._bitmap_editor()  # a second F9 raises the same window
    assert win._dialogs.get(("bitmap", i)) is dlg
    dlg.colour.pick(0x00FF00)
    dlg._snapshot()
    dlg.canvas.put(0, 0, dlg.canvas.pair)
    dlg._save()
    assert win._dialogs.get(("bitmap", i)) is None  # Save wrote it and closed
    po = win.doc.parsed.page.objects[i]
    assert po.body_raw != before
    p, f = win.doc.bitmap_pictures(i)
    assert (p.width, p.height) == (8, 4) and p.rgba[:4] == bytes(GREEN)
    assert f is not None and f.rgba[:4] == bytes(GREEN)  # 'both' states drawn
    gh = G.parse_geom_header(po.body_raw)
    assert tuple(gh.rect1) == (30, 40, 38, 44)
    # an unchanged Save leaves the record alone
    after = po.body_raw
    win._bitmap_editor()
    win._dialogs.get(("bitmap", i))._save()
    assert win.doc.parsed.page.objects[i].body_raw == after
    # a non-bitmap: a message, nothing opens
    win._sel = [win.doc.create_object("rectangle", (60, 60, 80, 80), None)]
    win._bitmap_editor()
    assert "bitmap" in win.statusBar().currentMessage().lower()
