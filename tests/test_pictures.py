"""Picture import, export, resample and the Bitmap Editor tools."""

import os
import shutil
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import bitmaps, geometry as G  # noqa: E402
from qtkit import app as _app

#: the fixture page the tests copy (FxMain of the fake install)
SEED = "FxTplBlank"
windows = pytest.mark.skipif(os.name != "nt", reason="GDI converts enhanced metafiles")

RED = (200, 30, 30, 255)


@pytest.fixture()
def seed(fx_install, monkeypatch):
    """The page the tests copy; the copies (in tmp_path) find their libraries in the fake
    install's FxMain (CTG_LIB_PATH)."""
    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxMain"))
    return fx_install.page(SEED)


@pytest.fixture()
def win(tmp_path, seed):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)


def _open(win, tmp_path, name="FxPictures.ctg"):
    dst = tmp_path / name
    shutil.copyfile(fxinstall_page(win), dst)
    win.open_document(str(dst))
    win.act_snap.setChecked(False)
    win.doc.derive_check = True
    return str(dst)


def fxinstall_page(_win):
    """The seed page of the test's fake install (the ``seed`` fixture set CTG_LIB_PATH to
    its project)."""
    return os.path.join(os.environ["CTG_LIB_PATH"], SEED + ".ctg")


def _placeable_wmf(w_units=2880, h_units=1440, inch=1440, with_ext=False):
    """A small placeable WMF: header + a Rectangle (+ optional window ext) + EOF (as
    tests/test_bitmaps.py makes one)."""
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


def _png(path, w=8, h=4, colour=(10, 200, 10, 255), clear_row=False):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(*colour))
    if clear_row:
        for x in range(w):
            img.setPixelColor(x, 0, QtGui.QColor(0, 0, 0, 0))
    assert img.save(str(path))
    return str(path)


def _svg(path):
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
        '<rect x="0" y="0" width="20" height="20" fill="red"/>'
        '<rect x="20" y="0" width="20" height="20" fill="blue"/></svg>'
    )
    return str(path)


def _emf(path):
    """An enhanced metafile drawn with GDI: a red rectangle filling a 200 x 100 frame of
    the screen's own pixels."""
    import ctypes
    from ctypes import wintypes

    gdi = ctypes.windll.gdi32
    user = ctypes.windll.user32
    vp = ctypes.c_void_p
    gdi.CreateEnhMetaFileW.argtypes = [vp, wintypes.LPCWSTR, ctypes.POINTER(wintypes.RECT), wintypes.LPCWSTR]
    gdi.CreateEnhMetaFileW.restype = vp
    gdi.CloseEnhMetaFile.argtypes = [vp]
    gdi.CloseEnhMetaFile.restype = vp
    gdi.GetDeviceCaps.argtypes = [vp, ctypes.c_int]
    gdi.Rectangle.argtypes = [vp] + [ctypes.c_int] * 4
    gdi.CreateSolidBrush.restype = vp
    gdi.SelectObject.argtypes = [vp, vp]
    gdi.SelectObject.restype = vp
    gdi.GetEnhMetaFileBits.argtypes = [vp, ctypes.c_uint, ctypes.c_void_p]
    gdi.DeleteEnhMetaFile.argtypes = [vp]
    user.GetDC.restype = vp
    user.ReleaseDC.argtypes = [vp, vp]
    screen = user.GetDC(None)
    hs, hp = gdi.GetDeviceCaps(screen, 4), gdi.GetDeviceCaps(screen, 8)
    vs, vpx = gdi.GetDeviceCaps(screen, 6), gdi.GetDeviceCaps(screen, 10)
    user.ReleaseDC(None, screen)
    r = wintypes.RECT(0, 0, round(200 * hs * 100 / hp), round(100 * vs * 100 / vpx))
    dc = gdi.CreateEnhMetaFileW(None, None, ctypes.byref(r), None)
    gdi.SelectObject(dc, gdi.CreateSolidBrush(0x0000FF))
    gdi.Rectangle(dc, 0, 0, 200, 100)
    h = gdi.CloseEnhMetaFile(dc)
    n = gdi.GetEnhMetaFileBits(h, 0, None)
    buf = ctypes.create_string_buffer(n)
    gdi.GetEnhMetaFileBits(h, n, buf)
    gdi.DeleteEnhMetaFile(h)
    path.write_bytes(buf.raw)
    return str(path)


# ------------------------------------------------------------------ format and import
def test_digest_matches_one_picture_whoever_compressed_it(tmp_path, seed):
    from ctgedit.editing import EditableDocument
    from ctgedit.format.ctf import compress

    dst = tmp_path / "p.ctg"
    shutil.copyfile(seed, dst)
    doc = EditableDocument.open(str(dst))
    rgba = bytes(RED) * 24
    i = doc.add_bitmap_object((0, 0, 6, 4), rgba, 6, 4)
    j = doc.add_bitmap_object((10, 0, 16, 4), rgba, 6, 4)
    objs = doc.parsed.page.objects
    sec = bitmaps.dib_section(objs[j])
    # the same pixels in another deflate stream (as the engine writes them)
    raw = __import__("zlib").decompress(sec.primary)
    other = __import__("zlib").compress(raw, 9)
    assert other != sec.primary
    body = objs[j].body_raw
    objs[j].body_raw = body[: sec.start + 8] + struct.pack("<I", len(other)) + other
    assert bitmaps.picture_digest(objs[i]) == bitmaps.picture_digest(objs[j])
    assert bitmaps.stored_size(objs[i]) == len(bitmaps.dib_section(objs[i]).primary)
    assert compress  # (the writer's compressor exists)
    k = doc.add_bitmap_object((20, 0, 26, 4), bytes((1, 2, 3, 255)) * 24, 6, 4)
    assert bitmaps.picture_digest(objs[k]) != bitmaps.picture_digest(objs[i])
    assert doc.picture_copies(i) == [j]


def test_metafile_swaps_in_place_and_exports_as_a_wmf_file():
    from ctgedit.format import factory

    wmf, w, h = bitmaps.wmf_from_file(_placeable_wmf())
    po = factory.make_metafile_record(5, (10, 10, 10 + w, 10 + h), wmf)
    wmf2, _w2, _h2 = bitmaps.wmf_from_file(_placeable_wmf(w_units=1440, h_units=1440))
    assert bitmaps.set_metafile(po, wmf2, rect=(10, 10, 60, 60))
    assert bitmaps.metafile_bits(po) == wmf2
    assert tuple(G.norm_rect(G.parse_geom_header(po.body_raw).rect1)) == (10, 10, 60, 60)
    assert bitmaps.stored_size(po) == len(wmf2)
    again, aw, ah = bitmaps.wmf_from_file(bitmaps.placeable_wmf(wmf2, 50, 50))
    assert again[18:] == wmf2[18:] and (aw, ah) == (50, 50)


@windows
def test_an_emf_becomes_a_metafile_that_draws_its_picture(tmp_path):
    _app()
    from ctgedit.infra import emf
    from ctgedit.ui.dialog import bitmap_import as B

    path = _emf(tmp_path / "d.emf")
    data = open(path, "rb").read()
    assert emf.is_emf(data) and not emf.is_emf(data[:40])
    src = B.open_source(path)
    assert src.kind == "metafile" and src.emf and src.vector
    pic = B.build(src, B.plain_options(src))
    assert pic.kind == "metafile"
    img = src.image(100, 50)
    # the whole frame is red (the drawing is mapped onto it, not cut off)
    for x, y in ((2, 2), (97, 47), (50, 25)):
        c = img.pixelColor(x, y)
        assert (c.red(), c.green(), c.blue()) == (255, 0, 0), (x, y)
    flat = B.build(src, B.ImportOptions(40, 20, as_drawing=False))
    assert (flat.kind, flat.width, flat.height) == ("bitmap", 40, 20)
    assert "*.emf" in B.import_filter() and B.is_importable(path)


def test_options_size_pixels_and_transparency(tmp_path):
    _app()
    from ctgedit.ui.dialog import bitmap_import as B

    src = B.open_source(_png(tmp_path / "a.png", clear_row=True))
    assert src.has_alpha and (src.width, src.height) == (8, 4)
    plain = B.build(src, B.plain_options(src))
    assert (plain.width, plain.height, plain.size) == (8, 4, (8, 4))
    assert plain.data[:4] == b"\0\0\0\0" and plain.data[32:36] == bytes((10, 200, 10, 255))
    big = B.build(src, B.ImportOptions(16, 8))
    assert (big.width, big.height, big.size) == (16, 8, (16, 8))
    kept = B.build(src, B.ImportOptions(16, 8, store_at_size=False))
    assert (kept.width, kept.height, kept.size) == (8, 4, (16, 8))
    none = B.build(src, B.ImportOptions(8, 4, transparency="none"))
    assert none.data[:4] == b"\xff\xff\xff\xff"  # the clear row over white
    key = B.build(src, B.ImportOptions(8, 4, transparency="colour", key=0x0BC70B, tolerance=1))
    assert key.data[32:36] == b"\0\0\0\0"  # within 1 % of the key
    exact = B.build(src, B.ImportOptions(8, 4, transparency="colour", key=0x0BC70B))
    assert exact.data[32:36] == bytes((10, 200, 10, 255))
    with pytest.raises(B.PictureError):
        B.build(src, B.ImportOptions(9000, 10))
    assert B.stored_bytes(big) > 0


def test_an_svg_is_drawn_at_the_size_it_is_placed(tmp_path):
    _app()
    from ctgedit.ui.dialog import bitmap_import as B

    src = B.open_source(_svg(tmp_path / "t.svg"))
    assert (src.kind, src.width, src.height) == ("svg", 40, 20)
    pic = B.build(src, B.ImportOptions(400, 200, store_at_size=False))
    assert (pic.width, pic.height) == (400, 200)  # always drawn at its size
    px = lambda x, y: pic.data[(y * 400 + x) * 4 : (y * 400 + x) * 4 + 4]  # noqa: E731
    assert px(100, 100) == bytes((255, 0, 0, 255)) and px(300, 100) == bytes((0, 0, 255, 255))
    assert px(199, 100)[3] == 255 and px(201, 100) == bytes((0, 0, 255, 255))


def test_recent_and_favourite_pictures(tmp_path):
    _app()
    from ctgedit.ui.dialog import bitmap_import as B

    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    B.add_recent([a])
    B.add_recent([b, a])
    assert B.recent_pictures() == [b, a]
    B.add_recent([str(tmp_path / ("p%d.png" % k)) for k in range(12)])
    assert len(B.recent_pictures()) == B.RECENT_MAX
    B.set_favourite(a, True)
    assert B.is_favourite(a) and B.favourite_pictures() == [a]
    B.set_favourite(a, False)
    assert not B.favourite_pictures()
    B.clear_recent()
    assert B.recent_pictures() == []


def test_smooth_resample_keeps_one_bit_transparency():
    _app()
    from ctgedit.ui.dialog.bitmap_import import smooth_resample

    rgba = (bytes(RED) * 2 + b"\0\0\0\0" * 2) * 4
    out = smooth_resample(rgba, 4, 4, 8, 8)
    assert len(out) == 8 * 8 * 4
    assert set(out[3::4]) <= {0, 255}
    assert out[:4] == bytes(RED) and out[7 * 4 : 8 * 4] == b"\0\0\0\0"


# ------------------------------------------------------------------ the options dialog
def test_the_options_dialog_sizes_each_picture(tmp_path):
    _app()
    from ctgedit.ui.dialog import bitmap_import as B
    from ctgedit.ui.dialog.picture_import import PictureImportDialog
    from ctgedit.ui.prefs.settings import app_settings

    srcs = [B.open_source(_png(tmp_path / "a.png", 8, 4)), B.open_source(_png(tmp_path / "b.png", 4, 4))]
    dlg = PictureImportDialog(srcs, None, fit=(800, 600))
    assert dlg.which.isVisibleTo(dlg) and dlg.r_fit.isVisibleTo(dlg)
    assert not dlg.r_target.isVisibleTo(dlg)
    assert [dlg.options_for(s)[:2] for s in srcs] == [(8, 4), (4, 4)]
    dlg.r_fit.click()
    assert [dlg.options_for(s)[:2] for s in srcs] == [(720, 360), (540, 540)]
    dlg.w.setValue(100)  # This size, keeping the shape
    assert dlg.size_mode_name() == "custom" and dlg.h.value() == 50
    assert [dlg.options_for(s)[:2] for s in srcs] == [(100, 50), (100, 100)]
    # sized with the second (portrait) one previewed: the first keeps its own shape
    srcs2 = [B.open_source(_png(tmp_path / "c.png", 80, 40)), B.open_source(_png(tmp_path / "d.png", 40, 80))]
    dlg2 = PictureImportDialog(srcs2, None, fit=(800, 600))
    dlg2.which.setCurrentIndex(1)
    dlg2.keep.setChecked(True)
    dlg2.w.setValue(100)
    assert dlg2.h.value() == 200
    assert [dlg2.options_for(s)[:2] for s in srcs2] == [(100, 50), (100, 200)]
    dlg2.which.setCurrentIndex(0)
    assert [dlg2.options_for(s)[:2] for s in srcs2] == [(100, 50), (100, 200)]
    dlg2.reject()
    dlg.pick.setChecked(True)
    assert dlg.mode.currentData() == "colour" and dlg.preview.picking
    dlg.preview.picked.emit(0x123456)
    assert dlg.colour.pair()[0] & 0xFFFFFF == 0x123456 and not dlg.pick.isChecked()
    dlg.again.setChecked(False)
    dlg.accept()
    st = app_settings()
    assert st.value("import/size_mode") == "custom"
    assert st.value("opt/picture_options") == "never"
    pics = dlg.pictures()
    assert [p.size for p in pics] == [(100, 50), (100, 100)]


def test_replace_and_replace_everywhere_fix_the_dialog(tmp_path):
    _app()
    from ctgedit.ui.dialog import bitmap_import as B
    from ctgedit.ui.dialog.picture_import import PictureImportDialog

    src = B.open_source(_png(tmp_path / "a.png", 8, 4))
    dlg = PictureImportDialog([src], None, target=(30, 20), action="Replace", drawing=False)
    assert dlg.size_mode_name() == "target" and dlg.options_for(src)[:2] == (30, 20)
    assert not dlg.kind_box.isVisibleTo(dlg) and dlg.r_bitmap.isChecked()
    assert dlg.buttons.button(QtWidgets.QDialogButtonBox.Ok).text() == "Replace"
    each = PictureImportDialog([src], None, target=(30, 20), drawing=False, each_size=True)
    assert not each.r_own.isVisibleTo(each)


# ------------------------------------------------------------------ the window
def test_import_through_the_options_places_the_chosen_size(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    from ctgedit.ui.dialog import picture_import as PI

    png = _png(tmp_path / "a.png", 8, 4)
    monkeypatch.setattr(
        PI.PictureImportDialog,
        "exec",
        lambda self: (self.r_custom.click(), self.w.setValue(32), QtWidgets.QDialog.Accepted)[-1],
    )
    new = win._import_files([png])
    objs = win.doc.parsed.page.objects
    assert len(new) == 1
    l, t, r, b = G.norm_rect(G.parse_geom_header(objs[new[0]].body_raw).rect1)
    assert (r - l, b - t) == (32, 16)
    p, _f = bitmaps.read_pictures(objs[new[0]])
    assert (p.width, p.height) == (32, 16)
    from ctgedit.ui.dialog.bitmap_import import recent_pictures

    assert recent_pictures()[0] == os.path.normpath(png)
    # cancelled: nothing placed
    monkeypatch.setattr(PI.PictureImportDialog, "exec", lambda self: QtWidgets.QDialog.Rejected)
    n = len(objs)
    assert win._import_files([png]) == [] and len(win.doc.parsed.page.objects) == n
    # a drop asks too (the same review step however a picture arrives): cancelled, nothing
    assert win._import_files([png], (5, 5), trigger="drop") == []
    assert len(win.doc.parsed.page.objects) == n
    # Settings > Canvas 'At File > Import only': a drop places it as it is
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/picture_options", "import")
    win._import_files([png], (5, 5), trigger="drop")
    assert len(win.doc.parsed.page.objects) == n + 1


def test_replace_picture_keeps_the_object_and_its_copies(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    from ctgedit.ui.dialog import picture_import as PI

    doc = win.doc
    rgba = bytes(RED) * 24
    i = doc.add_bitmap_object((50, 50, 62, 58), rgba, 6, 4)  # stretched x2
    doc.ensure_property(i, "COMMENT", "logo tip")
    doc.set_object_name(i, "Logo")
    j = doc.add_bitmap_object((100, 50, 106, 54), rgba, 6, 4)
    an = doc.parsed.page.objects[i].an_str
    new_png = _png(tmp_path / "new.png", 3, 2, colour=(0, 0, 255, 255))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (new_png, "")))
    monkeypatch.setattr(PI.PictureImportDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "choose", lambda *a, **k: a[3][0])  # (Replace All)
    win._sel = [i]
    steps = len(doc._undo)
    win._replace_picture()
    objs = doc.parsed.page.objects
    for k, size in ((i, (12, 8)), (j, (6, 4))):
        l, t, r, b = G.norm_rect(G.parse_geom_header(objs[k].body_raw).rect1)
        assert (r - l, b - t) == size
        p, _f = bitmaps.read_pictures(objs[k])
        assert (p.width, p.height) == size and p.rgba[:4] == bytes((0, 0, 255, 255))
    assert doc.object_properties(i).get("COMMENT") == "logo tip"
    assert doc.object_name(i) == "Logo" and objs[i].an_str == an
    assert len(doc._undo) == steps + 1
    doc.undo()
    p, _f = bitmaps.read_pictures(doc.parsed.page.objects[j])
    assert p.rgba[:4] == bytes(RED)


def test_export_picture_writes_both_states_and_a_wmf(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    doc = win.doc
    i = doc.add_bitmap_object((0, 0, 6, 4), bytes(RED) * 24, 6, 4, flash_rgba=bytes((0, 0, 255, 255)) * 24)
    out = str(tmp_path / "out.png")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "PNG picture (*.png)"))
    )
    win._sel = [i]
    win._export_picture()
    a, b = QtGui.QImage(out), QtGui.QImage(str(tmp_path / "out (flashing).png"))
    assert (a.width(), a.height()) == (6, 4) and a.pixelColor(0, 0).red() == 200
    assert b.pixelColor(0, 0).blue() == 255
    wmf, w, h = bitmaps.wmf_from_file(_placeable_wmf())
    m = doc.add_metafile_object((0, 0, w, h), wmf)
    out2 = str(tmp_path / "out.wmf")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (out2, "Windows Metafile (*.wmf)")),
    )
    win._sel = [m]
    win._export_picture()
    again, aw, ah = bitmaps.wmf_from_file(open(out2, "rb").read())
    assert (aw, ah) == (w, h)


def test_resample_picture_stores_a_stretched_bitmap_at_its_size(win, tmp_path):
    _open(win, tmp_path)
    doc = win.doc
    i = doc.add_bitmap_object((0, 0, 24, 16), bytes(RED) * 24, 6, 4)
    win._sel = [i]
    win._update_actions()
    assert win.act_resample_picture.isEnabled()
    win._resample_picture()
    p, _f = bitmaps.read_pictures(doc.parsed.page.objects[i])
    assert (p.width, p.height) == (24, 16)
    l, t, r, b = G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)
    assert (l, t, r, b) == (0, 0, 24, 16)


def test_objects_panel_shows_picture_sizes_and_flags_large_ones(win, tmp_path):
    _open(win, tmp_path)
    from ctgedit.ui.panels import objects_dock as OD
    from ctgedit.ui.prefs.settings import app_settings

    doc = win.doc
    import random

    rnd = random.Random(1)
    noisy = bytes(rnd.randrange(256) if k % 4 != 3 else 255 for k in range(400 * 300 * 4))
    i = doc.add_bitmap_object((0, 0, 400, 300), noisy, 400, 300)
    obj = doc.page.objects[i]
    assert OD.picture_text(obj).startswith("400 x 300, ")
    app_settings().setValue("opt/large_picture_kb", 50)
    assert OD.picture_is_large(obj)
    app_settings().setValue("opt/large_picture_kb", 65536)
    assert not OD.picture_is_large(obj)


def test_paste_special_offers_the_picture_options(win, tmp_path, monkeypatch):
    _open(win, tmp_path)
    img = QtGui.QImage(4, 2, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(9, 9, 9))
    QtWidgets.QApplication.clipboard().setImage(img)
    from ctgedit.ui.dialog.dialogs import PasteSpecialDialog
    from ctgedit.ui.dialog import picture_import as PI

    seen = {}

    def exec_(self):
        seen["formats"] = (
            [self._list.item(k).text() for k in range(self._list.count())] if hasattr(self, "_list") else None
        )
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(PasteSpecialDialog, "exec", exec_)
    monkeypatch.setattr(PasteSpecialDialog, "choice", lambda self: 1)
    monkeypatch.setattr(
        PI.PictureImportDialog,
        "exec",
        lambda self: (self.r_custom.click(), self.w.setValue(8), QtWidgets.QDialog.Accepted)[-1],
    )
    win._paste_special()
    objs = win.doc.parsed.page.objects
    l, t, r, b = G.norm_rect(G.parse_geom_header(objs[-1].body_raw).rect1)
    assert (r - l, b - t) == (8, 4)


# ------------------------------------------------------------------ the bitmap editor
def test_bitmap_editor_select_crop_trim_turn_and_undo():
    _app()
    from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog

    clear = b"\0\0\0\0"
    # 6 x 4: a clear border round a 4 x 2 red block
    rows = [clear * 6] + [clear + bytes(RED) * 4 + clear] * 2 + [clear * 6]
    dlg = BitmapEditorDialog(b"".join(rows), 6, 4)
    c = dlg.canvas
    assert dlg.trim() and (c.primary.width(), c.primary.height()) == (4, 2)
    assert dlg.origin_shift() == (1, 1)
    dlg.turn("right")
    assert (c.primary.width(), c.primary.height()) == (2, 4)
    dlg.turn("flip_h")
    dlg._set_tool("select")
    c.set_selection(QtCore.QRect(0, 0, 2, 2))
    assert dlg._b_crop.isEnabled()
    assert dlg.crop() and (c.primary.width(), c.primary.height()) == (2, 2)
    assert dlg.origin_shift() == (1, 1)
    dlg.undo()
    assert (c.primary.width(), c.primary.height()) == (2, 4)
    dlg.undo()
    dlg.undo()
    dlg.undo()
    assert (c.primary.width(), c.primary.height()) == (6, 4) and dlg.origin_shift() == (0, 0)
    dlg.redo()
    assert dlg.origin_shift() == (1, 1)


def test_rotation_turns_pixels_a_quarter():
    _app()
    from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog

    blue = bytes((0, 0, 255, 255))
    dlg = BitmapEditorDialog(bytes(RED) + blue, 2, 1)
    dlg.turn("right")
    c = dlg.canvas
    top, bottom = c.primary.pixelColor(0, 0), c.primary.pixelColor(0, 1)
    assert (top.red(), bottom.blue()) == (200, 255)
    dlg.turn("left")
    dlg.turn("left")
    top = c.primary.pixelColor(0, 0)
    assert top.blue() == 255


# ------------------------------------------------------------------ duplicate pictures
def _project_with_copies(tmp_path):
    """tmp_path/Pics with two copies of the seed page (a, b), each with the same picture
    twice (once stretched)."""
    from ctgedit.editing import EditableDocument

    rgba = bytes(RED) * 24
    d = tmp_path / "Pics"
    d.mkdir()
    for name in ("a", "b"):
        p = d / (name + ".ctg")
        shutil.copyfile(os.path.join(os.environ["CTG_LIB_PATH"], SEED + ".ctg"), p)
        doc = EditableDocument.open(str(p))
        doc.add_bitmap_object((10, 10, 16, 14), rgba, 6, 4)
        j = doc.add_bitmap_object((40, 10, 52, 18), rgba, 6, 4)  # stretched x2
        doc.ensure_property(j, "COMMENT", "logo tip")
        doc.set_input_commands(j, touch={"CMD": "Beep(0)"})
        doc.set_object_name(j, "Logo")
        doc.save()
    return str(d)


def test_find_duplicates_then_make_a_library_symbol(tmp_path, monkeypatch, seed):
    from ctgedit.workers import picture_worker as PW
    from ctgedit.editing import EditableDocument

    d = _project_with_copies(tmp_path)
    rep = PW.scan(d)
    assert rep["pages"] == 2 and len(rep["groups"]) == 1
    g = rep["groups"][0]
    assert (len(g["uses"]), g["pages"], g["width"], g["height"]) == (4, 2, 6, 4)
    assert g["wasted"] == 3 * g["bytes"] and g["thumb"]["w"] == 6
    first = g["uses"][0]
    ans = PW.apply(
        {
            "mode": "symbol",
            "digest": g["digest"],
            "pages": [os.path.join(d, "a.ctg"), os.path.join(d, "b.ctg")],
            "project": d,
            "library": "logos",
            "element": "red",
            "make": True,
            "first": {"page": first["page"], "index": first["index"]},
            "backup": str(tmp_path / "backup"),
        }
    )
    assert ans["updated"] == ["a", "b"] and ans["changed"] == 4 and not ans["failed"]
    assert os.path.exists(os.path.join(d, "logos.ctl"))
    doc = EditableDocument.open(os.path.join(d, "a.ctg"))
    doc.derive_check = True
    objs = doc.parsed.page.objects
    linked = [i for i in range(len(objs)) if (doc.symbol_link(i) or {}).get("element") == "red"]
    assert len(linked) == 2
    sizes = sorted(G.norm_rect(G.parse_geom_header(objs[i].body_raw).rect1) for i in linked)
    assert sizes == [(10, 10, 16, 14), (40, 10, 52, 18)]
    big = [i for i in linked if doc.object_name(i) == "Logo"][0]
    assert doc.object_properties(big).get("COMMENT") == "logo tip"
    assert (doc.input_commands(big).get("touch") or {}).get("CMD") == "Beep(0)"
    assert g["digest"] not in PW.page_digests(os.path.join(d, "a.ctg"))
    assert PW.scan(d)["groups"] == []
    # the backup holds the pages as they were
    assert os.path.exists(os.path.join(str(tmp_path / "backup"), os.path.basename(d), "a.ctg"))


def test_link_picture_to_symbol_is_one_undo_step(tmp_path, monkeypatch, seed):
    from ctgedit.workers import picture_worker as PW
    from ctgedit.editing import EditableDocument

    d = _project_with_copies(tmp_path)
    g = PW.scan(d)["groups"][0]
    PW.make_symbol(d, "logos", "red", g["uses"][0]["page"], g["uses"][0]["index"], g["digest"])
    doc = EditableDocument.open(os.path.join(d, "b.ctg"))
    doc.derive_check = True
    before = [bytes(po.body_raw) for po in doc.parsed.page.objects]
    i = [
        k
        for k, po in enumerate(doc.parsed.page.objects)
        if bitmaps.picture_digest(po) == g["digest"] and doc.object_name(k) == "Logo"
    ][0]
    steps = len(doc._undo)
    doc.link_picture_to_symbol(i, "logos", "red")
    assert len(doc._undo) == steps + 1
    doc.undo()
    assert [bytes(po.body_raw) for po in doc.parsed.page.objects] == before


def test_replace_everywhere_puts_the_new_pixels_at_each_size(tmp_path, monkeypatch, seed):
    from ctgedit.workers import picture_worker as PW

    d = _project_with_copies(tmp_path)
    g = PW.scan(d)["groups"][0]
    pixels = {}
    for w, h in ((6, 4), (12, 8)):
        f = tmp_path / ("p%dx%d.rgba" % (w, h))
        f.write_bytes(bytes((0, 0, 255, 255)) * (w * h))
        pixels["%dx%d" % (w, h)] = {"file": str(f), "w": w, "h": h}
    ans = PW.apply(
        {
            "mode": "replace",
            "digest": g["digest"],
            "pages": [os.path.join(d, "a.ctg")],
            "pixels": pixels,
            "skip": [os.path.join(d, "b.ctg")],
        }
    )
    assert ans["updated"] == ["a"] and ans["changed"] == 2
    from ctgedit.format.page import parse_ctg

    objs = parse_ctg(os.path.join(d, "a.ctg")).page.objects
    blue = [
        po
        for po in objs
        if po.type_code == 7
        and bitmaps.dib_section(po) is not None
        and bitmaps.read_pictures(po)[0].rgba[:4] == bytes((0, 0, 255, 255))
    ]
    assert sorted((p.width, p.height) for p in (bitmaps.read_pictures(po)[0] for po in blue)) == [
        (6, 4),
        (12, 8),
    ]
    # b.ctg (open in an editor) is left
    assert g["digest"] in PW.page_digests(os.path.join(d, "b.ctg"))


def _wait(cond, seconds=120):
    import time

    end = time.monotonic() + seconds
    while not cond():
        QtWidgets.QApplication.processEvents()
        assert time.monotonic() < end, "timed out"
        time.sleep(0.05)


def test_the_dialog_links_closed_and_open_pages(win, tmp_path, monkeypatch):
    from ctgedit.ui.dialog import duplicate_pictures as DP

    d = _project_with_copies(tmp_path)
    win.open_document(os.path.join(d, "b.ctg"))  # b is open in the editor
    dlg = DP.DuplicatePicturesDialog(win, d)
    _wait(lambda: not dlg.busy() and dlg._groups)
    assert dlg.groups.topLevelItemCount() == 1 and dlg.uses.topLevelItemCount() == 4
    monkeypatch.setattr(DP._SymbolNameDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    from ctgedit.ui import messages

    monkeypatch.setattr(messages, "confirm", lambda *a, **k: True)
    dlg._make_symbol()
    _wait(lambda: not dlg.busy() and "Linked" in dlg.status.text())
    assert "b open in the editor" in dlg.status.text()
    from ctgedit.workers import picture_worker as PW

    digest = None
    for po in win.doc.parsed.page.objects:
        digest = digest or bitmaps.picture_digest(po)
    # a.ctg (closed) was rewritten; b (open) changed in its tab, not on disk
    assert os.path.exists(os.path.join(d, "pictures.ctl"))
    assert not any(
        bitmaps.dib_section(po) is not None
        and bitmaps.picture_digest(po) in PW.page_digests(os.path.join(d, "b.ctg"))
        for po in win.doc.parsed.page.objects
    )
    assert PW.page_digests(os.path.join(d, "a.ctg")).isdisjoint(PW.page_digests(os.path.join(d, "b.ctg")))
    linked = [
        i
        for i in range(len(win.doc.parsed.page.objects))
        if (win.doc.symbol_link(i) or {}).get("element") == "picture1"
    ]
    assert len(linked) == 2
    _wait(lambda: not dlg.busy())  # (the scan after it)
    dlg.reject()


def test_export_picture_asks_before_replacing_the_flashing_companion(win, tmp_path, monkeypatch):
    """Only 'x (flashing).png' exists: the export asks, and Cancel writes neither file."""
    _open(win, tmp_path)
    doc = win.doc
    i = doc.add_bitmap_object((0, 0, 6, 4), bytes(RED) * 24, 6, 4, flash_rgba=bytes((0, 0, 255, 255)) * 24)
    out = str(tmp_path / "indicator.png")
    companion = tmp_path / "indicator (flashing).png"
    green = QtGui.QImage(9, 8, QtGui.QImage.Format_ARGB32)
    green.fill(QtGui.QColor(0, 255, 0))
    assert green.save(str(companion))
    before = companion.read_bytes()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "PNG picture (*.png)"))
    )
    from ctgedit.ui import messages

    asked = []

    def answer(_parent, _title, text, actions, *_a):
        asked.append(text)
        return None  # (Cancel)

    monkeypatch.setattr(messages, "choose", answer)
    win._sel = [i]
    win._export_picture()
    assert asked and "indicator (flashing).png" in asked[0]
    assert not os.path.exists(out) and companion.read_bytes() == before
    monkeypatch.setattr(messages, "choose", lambda *a, **k: a[3][0])
    win._export_picture()
    assert os.path.exists(out) and QtGui.QImage(str(companion)).width() == 6


def test_show_of_a_duplicate_finds_the_copy_after_an_unsaved_delete(win, tmp_path):
    """The scan read the saved page: after an object above the copy is deleted, Show finds
    the same picture at the same place, not the record now at the scan's index."""
    from ctgedit.format import bitmaps
    from ctgedit.format import geometry as G
    from ctgedit.ui.dialog.duplicate_pictures import locate_use

    _open(win, tmp_path)
    doc = win.doc
    blue = bytes((0, 0, 255, 255)) * 24
    first = doc.add_bitmap_object((10, 10, 16, 14), bytes(RED) * 24, 6, 4)
    red = doc.add_bitmap_object((100, 50, 106, 54), bytes(RED) * 24, 6, 4)
    doc.add_bitmap_object((200, 80, 206, 84), blue, 6, 4)
    objs = doc.parsed.page.objects
    digest = bitmaps.picture_digest(objs[red])
    use = {"index": red, "rect": list(G.parse_geom_header(objs[red].body_raw).rect1), "name": "x"}
    assert locate_use(objs, digest, use) == red
    doc.delete_objects([first])
    objs = doc.parsed.page.objects
    k = locate_use(objs, digest, use)
    assert k is not None and k != red
    assert list(G.parse_geom_header(objs[k].body_raw).rect1)[:2] == [100, 50]
    # gone from the page: None (Show says so)
    doc.delete_objects([k])
    assert locate_use(doc.parsed.page.objects, digest, use) is None
