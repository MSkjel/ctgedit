"""Appearance pages per object class."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the host page's size and background
HOST_SIZE = (1366, 746)
HOST_BG = 0xD8D8D8


@pytest.fixture()
def host(fx_install, tmp_path, monkeypatch):
    """A host page with one rectangle far right."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    folder = tmp_path / "FxHost"
    folder.mkdir()
    path = str(folder / "FxAppearance.ctg")
    with open(path, "wb") as fh:
        fh.write(TP.new_page_bytes(HOST_SIZE[0], HOST_SIZE[1], background=(HOST_BG, HOST_BG)))
    monkeypatch.delenv("CTG_DERIVE_CHECK", raising=False)
    doc = EditableDocument.open(path)
    doc.create_object("rectangle", rect=(1200, 600, 1210, 610), colour=0x808080)
    doc.save()
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")
    return path


@pytest.fixture(autouse=True)
def _derive_check(monkeypatch):
    # every incremental re-derive is compared with a full one
    monkeypatch.setenv("CTG_DERIVE_CHECK", "1")


def _doc(host):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(host)


def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index)


def _block(doc, i):
    from ctgedit.format import geometry as G

    return G.find_colour_block(doc.parsed.page.objects[i])


def _reopen(doc, tmp_path):
    from ctgedit.editing import EditableDocument

    out = str(tmp_path / "saved.ctg")
    doc.save(out)
    return EditableDocument.open(out)


def _make(doc):
    return {
        "line": doc.create_object("line", nodes=[(20, 20), (200, 60)]),
        "rect": doc.create_object("rectangle", rect=(20, 100, 120, 160), colour=0x336699),
        "ellipse": doc.create_object("ellipse", rect=(200, 100, 300, 160), colour=0x00AA00),
        "polygon": doc.create_object("polygon", nodes=[(20, 220), (70, 180), (120, 220)], colour=0x880000),
        "pipe": doc.create_object("pipe", nodes=[(20, 300), (200, 300)]),
        "text": doc.create_object("text", rect=(20, 400, 100, 430), text="Hello"),
        "button": doc.create_object("button", rect=(200, 400, 300, 440), text="Go"),
    }


# --------------------------------------------------------------- per-class pages
def test_general_page_shows_only_the_class_fields(host, tmp_path):
    doc = _doc(host)
    objs = _make(doc)
    fields = (
        "_sp_width",
        "_cb_lstyle",
        "_col_line",
        "_chk_filled",
        "_col_fill",
        "_cb_grad",
        "_col_grad",
        "_chk_border",
        "_chk_extra",
        "_sp_radius",
        "_cb_estyle",
        "_sp_astart",
        "_chk_open",
        "_col_hilight",
        "_col_lolight",
        "_cb_font",
        "_sp_fsize",
        "_col_fore",
        "_cb_script",
        "_rb_btext",
        "_chk_custom",
    )
    want = {
        "line": {"_sp_width", "_cb_lstyle", "_col_line"},
        "rect": {
            "_sp_width",
            "_cb_lstyle",
            "_col_line",
            "_chk_filled",
            "_col_fill",
            "_cb_grad",
            "_col_grad",
            "_chk_border",
            "_chk_extra",
            "_sp_radius",
        },
        "ellipse": {
            "_sp_width",
            "_cb_lstyle",
            "_col_line",
            "_chk_filled",
            "_col_fill",
            "_cb_grad",
            "_col_grad",
            "_cb_estyle",
            "_sp_astart",
        },
        "polygon": {
            "_sp_width",
            "_cb_lstyle",
            "_col_line",
            "_chk_filled",
            "_col_fill",
            "_cb_grad",
            "_col_grad",
            "_chk_open",
        },
        "pipe": {"_sp_width", "_col_hilight", "_col_lolight"},
        "text": {"_cb_font", "_sp_fsize", "_col_fore", "_cb_script"},
        "button": {"_cb_font", "_sp_fsize", "_col_fore", "_cb_script", "_rb_btext", "_chk_custom"},
    }
    for kind, i in objs.items():
        dlg = _sheet(doc, i)
        have = {f for f in fields if getattr(dlg, f) is not None}
        assert have == want[kind], kind
        # the 3D Effects page: every class but the pipe (CtDraw's page masks)
        assert ("effects" in dlg.page_ids()) == (kind != "pipe"), kind
        dlg.close()


def test_line_and_fill_colours_are_separate_slots(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["rect"]
    dlg = _sheet(doc, i)
    n = len(doc._undo)
    dlg._col_line.pick(0xFF0000)
    dlg._col_fill.pick(0x00FF00, 0x0000FF)  # a flashing fill
    assert dlg.apply() is True
    assert len(doc._undo) == n + 1  # one undo step
    cb = _block(doc, i)
    assert cb.slots[G.SLOT_LINE] == (0xFF0000, 0xFF0000)
    assert cb.slots[G.SLOT_FILL] == (0x00FF00, 0x0000FF)
    # a flashing colour makes the object dynamic
    assert doc.parsed.page.objects[i].subtype_size & 0x10
    assert dlg.apply() is False  # nothing left to write
    re = _reopen(doc, tmp_path)
    cb = _block(re, i)
    assert cb.slots[G.SLOT_LINE] == (0xFF0000, 0xFF0000)
    assert cb.slots[G.SLOT_FILL] == (0x00FF00, 0x0000FF)
    doc.undo()
    cb = _block(doc, i)
    assert cb.slots[G.SLOT_LINE] != (0xFF0000, 0xFF0000)


def test_pipe_highlight_lowlight_write_independently(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["pipe"]
    dlg = _sheet(doc, i)
    assert dlg._cb_lstyle is None  # a pipe has no line style
    dlg._col_hilight.pick(0xFFFF00)
    dlg._col_lolight.pick(0x404000)
    assert dlg.apply() is True
    cb = _block(doc, i)
    assert cb.slots[G.SLOT_HILIGHT] == (0xFFFF00, 0xFFFF00)
    assert cb.slots[G.SLOT_LOLIGHT] == (0x404000, 0x404000)
    # the pipe's load copies hilight -> fill + line: kept in step
    assert cb.slots[G.SLOT_FILL] == cb.slots[G.SLOT_LINE] == (0xFFFF00, 0xFFFF00)
    st = doc.page.objects[i].style
    assert (st.hilight_colour, st.lolight_colour) == (0xFFFF00, 0x404000)


def test_gradient_colour_and_flashing_pair(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["ellipse"]
    dlg = _sheet(doc, i)
    assert not dlg._col_grad.swatch.isEnabled()  # no gradient: no colour
    dlg._cb_grad.setCurrentIndex(3)  # Top to Bottom
    assert dlg._col_grad.swatch.isEnabled()
    dlg._col_grad.pick(0xFFFFFF, 0x000000)
    assert dlg.apply() is True
    tail = G.parse_shape_tail(doc.parsed.page.objects[i])
    assert (tail["grad_mode"], tail["grad_on"], tail["grad_off"]) == (3, 0xFFFFFF, 0)
    assert doc.page.objects[i].style.gradient_mode == 3
    # a flashing gradient colour makes the object dynamic: the ellipse class
    # takes the gradient pair into account too
    assert doc.parsed.page.objects[i].subtype_size & 0x10
    assert doc.set_gradient(i, 3, 0xFFFFFF)  # one colour again
    assert not doc.parsed.page.objects[i].subtype_size & 0x10


def test_rectangle_border_extra_line_and_radius_constraints(host, tmp_path):
    doc = _doc(host)
    i = _make(doc)["rect"]
    dlg = _sheet(doc, i)
    # Extra line needs a Raised / Lowered effect (the 3D Effects page)
    assert dlg._chk_border.isEnabled() and not dlg._chk_extra.isEnabled()
    dlg._rb_effects[1].setChecked(True)  # Raised
    assert dlg._chk_extra.isEnabled()
    # a corner radius disables Border and Extra line (help 1172124)
    dlg._sp_radius.setValue(5)
    assert not dlg._chk_border.isEnabled() and not dlg._chk_extra.isEnabled()
    dlg._sp_radius.setValue(0)
    dlg._chk_border.setChecked(True)
    dlg._chk_extra.setChecked(True)
    assert dlg.apply() is True
    o = doc.page.objects[i]
    assert o.style.rectangle_style == 3 and o.style.three_d == 1


def test_ellipse_angles_greyed_for_a_full_ellipse(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["ellipse"]
    dlg = _sheet(doc, i)
    assert not dlg._sp_astart.isEnabled() and not dlg._sp_aend.isEnabled()
    dlg._cb_estyle.setCurrentIndex(1)  # Pie-slice
    assert dlg._sp_astart.isEnabled() and dlg._sp_aend.isEnabled()
    dlg._sp_astart.setValue(50)
    dlg._sp_aend.setValue(150)
    assert dlg.apply() is True
    tail = G.parse_shape_tail(doc.parsed.page.objects[i])
    assert (tail["start"], tail["end"]) == (50, 150)
    assert doc.page.objects[i].style.ellipse_style == 1


def test_wide_line_style_greyed(host, tmp_path):
    doc = _doc(host)
    i = _make(doc)["line"]
    dlg = _sheet(doc, i)
    assert dlg._cb_lstyle.isEnabled()
    dlg._sp_width.setValue(3)
    assert not dlg._cb_lstyle.isEnabled()  # GDI draws it solid
    assert "solid" in dlg._cb_lstyle.toolTip()


# ------------------------------------------------------------------- 3D effects
def test_effects_page_writes_effect_depth_and_colours(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["rect"]
    dlg = _sheet(doc, i)
    assert dlg._rb_effects[0].isChecked()  # a new rect: no effect
    assert not dlg._sp_depth.isEnabled()
    assert dlg._chk_auto_depth.isChecked()  # depth -1 = automatic
    dlg._rb_effects[3].setChecked(True)  # Shadowed
    assert not dlg._col_shadow.isHidden() and dlg._col_hilight3d.isHidden()
    dlg._rb_effects[1].setChecked(True)  # Raised
    assert not dlg._col_hilight3d.isHidden() and dlg._col_shadow.isHidden()
    dlg._sp_depth.setValue(2)  # typing a depth: not automatic
    assert not dlg._chk_auto_depth.isChecked()
    dlg._col_hilight3d.pick(0xFFFFCC)
    dlg._col_lolight3d.pick(0x333300)
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1
    cb = _block(doc, i)
    assert cb.flags[G.FLAG_EFFECTS] & 0x0F == 1 and cb.flags[G.FLAG_EFFECT_DEPTH] == 2
    assert cb.slots[G.SLOT_HILIGHT] == (0xFFFFCC, 0xFFFFCC)
    assert cb.slots[G.SLOT_LOLIGHT] == (0x333300, 0x333300)
    # the Bounds grow by the depth
    gh = G.parse_geom_header(doc.parsed.page.objects[i].body_raw)
    assert gh.rect2 == (18, 98, 123, 163)
    # back to automatic: -1 stored, the page shows the computed depth
    dlg._chk_auto_depth.setChecked(True)
    assert dlg._sp_depth.value() == 1
    assert dlg.apply() is True
    assert _block(doc, i).flags[G.FLAG_EFFECT_DEPTH] == 0xFF
    re = _reopen(doc, tmp_path)
    assert re.page.objects[i].style.three_d == 1


def test_effect_depth_and_bounds_rules():
    """The 3D depth: automatic depth = (w + h) / 500 + 1 (Shadowed / 200),
    clamped to the pen width on a HOLLOW shape; the Bounds use it."""
    from ctgedit.format import geometry as G

    solid_raised = bytes([1, 0, 1, 0, 1, 0xFF])
    hollow_raised = bytes([1, 0, 0, 0, 1, 0xFF])
    big = (0, 0, 400, 200)
    assert G.effect_depth(0x0F, big, solid_raised) == 2
    assert G.effect_depth(0x0F, big, hollow_raised) == 1  # the width clamp
    assert G.effect_depth(0x0F, big, bytes([3, 0, 1, 0, 1, 0xFF])) == 4
    assert G.effect_depth(0x0F, big, bytes([1, 0, 1, 0, 1, 3])) == 3  # stored
    assert G.predict_bounds(0x0F, big, solid_raised) == (-2, -2, 403, 203)
    assert G.predict_bounds(0x0F, big, hollow_raised) == (-1, -1, 402, 202)


# ------------------------------------------------------------------------ text
def test_text_font_foreground_and_script(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["text"]
    dlg = _sheet(doc, i)
    assert dlg._lbl_script_warn.isHidden()  # Arial has Latin
    dlg._cb_font.setEditText("Tahoma")
    dlg._sp_fsize.setValue(16)
    dlg._cb_fstyle.setCurrentIndex(3)  # Bold Italic
    assert [dlg._cb_justify.itemText(k) for k in range(3)] == ["Left", "Centre", "Right"]
    dlg._cb_justify.setCurrentIndex(1)  # Centre
    dlg._chk_under.setChecked(True)
    dlg._col_fore.pick(0x123456)
    dlg._cb_script.setCurrentIndex(dlg._cb_script.findData(204))
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1
    po = doc.parsed.page.objects[i]
    tail = G.parse_text_tail(po)
    assert (tail["face"], tail["size"], tail["charset"]) == ("Tahoma", 16, 204)
    # the saver stores a non-default charset in the high byte + bit 0x80
    raw = int.from_bytes(po.body_raw[tail["orient_off"] : tail["orient_off"] + 2], "little")
    assert raw == (204 << 8) | 0x80 | tail["orient"]
    cb = _block(doc, i)
    assert cb.slots[G.SLOT_FILL] == cb.slots[G.SLOT_LINE] == (0x123456, 0x123456)
    st = doc.page.objects[i].style
    assert st.bold and st.italic and st.underline and st.text_colour == 0x123456
    assert st.justify.value == "centre"
    assert dlg.apply() is False
    # Western again: the charset is not stored (the system default)
    assert doc.set_text_charset(i, 0)
    tail = G.parse_text_tail(doc.parsed.page.objects[i])
    raw = int.from_bytes(
        doc.parsed.page.objects[i].body_raw[tail["orient_off"] : tail["orient_off"] + 2], "little"
    )
    assert tail["charset"] == 0 and raw == tail["orient"]


def test_script_support_check():
    _app()
    from ctgedit.ui.objprops.appearance import script_supported

    assert script_supported("Arial", 0)
    assert not script_supported("Wingdings", 204)


# --------------------------------------------------------------------- buttons
def test_button_font_block_writer(host, tmp_path):
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["button"]
    assert doc.set_font(i, face="Tahoma", size=14, italic=True, bold=False, underline=True, justify="left")
    f = G.parse_button_font(doc.parsed.page.objects[i])
    assert (f["face"], f["size"], f["attribs"]) == ("Tahoma", 14, 0x20 | 0x40)
    assert doc.set_foreground(i, 0xCC0000)
    assert G.parse_button_font(doc.parsed.page.objects[i])["text"] == (0xCC0000, 0xCC0000)
    assert doc.set_text_charset(i, 204)
    f = G.parse_button_font(doc.parsed.page.objects[i])
    assert f["charset"] == 204 and f["flags"] & 0x80000000  # the saver's marker
    st = doc.page.objects[i].style
    assert (st.font, st.font_size, st.italic, st.underline, st.text_colour) == (
        "Tahoma",
        14,
        True,
        True,
        0xCC0000,
    )
    re = _reopen(doc, tmp_path)
    f = G.parse_button_font(re.parsed.page.objects[i])
    assert (f["face"], f["size"], f["charset"]) == ("Tahoma", 14, 204)


def test_auto_width_button_follows_caption_and_font(host, tmp_path):
    """A button without a WIDTH property sizes itself to its caption (and font).
    On a page the fitted width is stored at once (CtDraw's page load keeps no
    auto-width state -- live), so removing WIDTH re-fits the button
    to the caption and font it has then."""
    from ctgedit.format import geometry as G

    doc = _doc(host)
    i = _make(doc)["button"]
    assert doc.remove_property(i, "WIDTH")
    w0 = doc.page.objects[i].geometry.extent_x
    assert doc.set_text(i, "A much longer caption")
    assert doc.page.objects[i].geometry.extent_x == w0  # the stored width is kept
    assert doc.remove_property(i, "WIDTH")
    w1 = doc.page.objects[i].geometry.extent_x
    assert w1 > w0
    assert doc.set_font(i, size=20)
    assert doc.remove_property(i, "WIDTH")
    assert doc.page.objects[i].geometry.extent_x > w1
    assert doc.object_properties(i)["WIDTH"] == str(doc.page.objects[i].geometry.extent_x)
    po = doc.parsed.page.objects[i]
    gh = G.parse_geom_header(po.body_raw)
    assert po.body_raw[-8:] == bytes(__import__("struct").pack("<4h", *gh.rect1))
    # a button WITH a width keeps its box
    j = doc.create_object("button", rect=(20, 480, 120, 510), text="Go")
    assert doc.set_text(j, "A much longer caption")
    assert doc.page.objects[j].geometry.extent_x == 100


def test_button_page_types_custom_fill_and_symbol(host, tmp_path):
    doc = _doc(host)
    i = _make(doc)["button"]
    dlg = _sheet(doc, i)
    assert dlg._rb_btext.isChecked() and dlg._chk_xp.isChecked()
    assert not dlg._col_up.swatch.isEnabled()  # needs Custom Fill Color
    dlg._chk_custom.setChecked(True)
    assert dlg._col_up.swatch.isEnabled()
    dlg._col_up.pick(0xCC0000)
    dlg._col_down.pick(0x660000)
    dlg._ed_caption.setText("Start^nMotor")
    n = len(doc._undo)
    assert dlg.apply() is True and len(doc._undo) == n + 1
    face = doc.button_face(i)
    assert face["custom_fill"] and face["up"] == (0xCC0000, 0xCC0000)
    assert face["down"] == (0x660000, 0x660000) and face["caption"] == "Start^nMotor"
    assert doc.page.objects[i].raw.get("_button_face") == 0xCC0000
    assert dlg.apply() is False
    # Custom Fill off writes CtDraw's XP defaults
    dlg._chk_custom.setChecked(False)
    assert dlg.apply() is True
    face = doc.button_face(i)
    assert not face["custom_fill"] and face["up"] == (0xF4F4F0, 0xF4F4F0)
    assert face["down"] == (0xE2E2D9, 0xE2E2D9)
    # Symbol: TEXT = '#lib.element'
    dlg._rb_bsym.setChecked(True)
    assert dlg._ed_symbol.isVisibleTo(dlg) and not dlg._cb_font.isVisibleTo(dlg)
    from ctgedit.ui.pick_fields import LibraryElementField

    assert isinstance(dlg._ed_symbol, LibraryElementField)
    assert dlg._btn_symset.text() == "Browse…" and dlg._ed_symbol.title == "Select Symbol"
    dlg._ed_symbol.setText("")
    assert dlg.apply() is False and "Choose the symbol" in dlg._status.text()
    dlg._ed_symbol.setText("icons.note")
    assert dlg.apply() is True
    assert doc.object_properties(i)["TEXT"] == "#icons.note"
    # Target: VIS_MODE = the mode, no caption (CtDraw clears TEXT)
    dlg._rb_btarget.setChecked(True)
    assert dlg._cb_bmode.isVisibleTo(dlg) and not dlg._chk_xp.isEnabled()
    dlg._cb_bmode.setCurrentIndex(0)  # BORDER_3D
    assert dlg.apply() is True
    props = doc.object_properties(i)
    assert props["VIS_MODE"] == "BORDER_3D" and "TEXT" not in props
    assert doc.page.objects[i].raw["_button_mode"] == 1
    # Text again, NORMAL (XP off)
    dlg._rb_btext.setChecked(True)
    dlg._chk_xp.setChecked(False)
    dlg._ed_caption.setText("Go")
    assert dlg.apply() is True
    props = doc.object_properties(i)
    assert props["VIS_MODE"] == "NORMAL" and props["TEXT"] == "Go"
    re = _reopen(doc, tmp_path)
    assert re.object_properties(i)["VIS_MODE"] == "NORMAL"


# ----------------------------------------------------------------------- render
def _scene(doc):
    _app()
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(doc.page)
    return sc


def test_filled_raised_rect_bevel_ring(host, tmp_path):
    """A filled square Raised rect: the body, then a d-pixel bevel ring
    outside it -- lolight bottom / right, hilight top / left."""
    doc = _doc(host)
    i = doc.create_object("rectangle", rect=(20, 20, 120, 80), colour=0xC0C0C0)
    doc.set_effect(i, 1, 2)
    doc.set_colour_slot(i, 3, 0xFFFFFF)
    doc.set_colour_slot(i, 4, 0x404040)
    sc = _scene(doc)
    img = QtGui.QImage(160, 120, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 0, 255))
    p = QtGui.QPainter(img)
    sc.render(p, QtCore.QRectF(0, 0, 160, 120), QtCore.QRectF(0, 0, 160, 120))
    p.end()

    def px(x, y):
        return img.pixelColor(x, y).rgb() & 0xFFFFFF

    assert px(70, 50) == 0xC0C0C0  # the face
    assert px(70, 18) == 0xFFFFFF and px(18, 50) == 0xFFFFFF  # hilight ring
    assert px(70, 82) == 0x404040 and px(122, 50) == 0x404040  # lolight ring


def test_effect_copies_for_ellipse_polygon_line(host, tmp_path):
    from ctgedit.render.scene import PageScene  # noqa: F401

    doc = _doc(host)
    e = doc.create_object("ellipse", rect=(20, 20, 120, 80), colour=0x3399FF)
    doc.set_effect(e, 2, 2)  # Lowered
    pg = doc.create_object("polygon", nodes=[(150, 80), (200, 20), (250, 80)], colour=0xFFCC00)
    doc.set_effect(pg, 3, 4)  # Shadowed
    ln = doc.create_object("line", nodes=[(280, 20), (380, 80)])
    doc.set_line_width(ln, 3)
    doc.set_effect(ln, 4, 2)  # Embossed: the body in page bg
    sc = _scene(doc)
    behind = QtWidgets.QGraphicsItem.ItemStacksBehindParent

    def copies(i):
        return sorted(
            (c.pos().x(), c.pos().y(), c.pen().color().rgb() & 0xFFFFFF)
            for c in sc._items[i].childItems()
            if c.flags() & behind
        )

    st = doc.page.objects[e].style
    assert copies(e) == sorted([(2.0, 2.0, st.hilight_colour), (-2.0, -2.0, st.lolight_colour)])
    st = doc.page.objects[pg].style
    assert copies(pg) == [(4.0, 4.0, st.shadow_colour)]
    assert len(copies(ln)) == 2
    assert sc._items[ln].pen().color() == sc._page_bg


def test_gdi_line_styles():
    _app()
    from ctgedit.model.document import Style
    from ctgedit.render.scene import _gdi_pen_style

    for style, pattern in ((1, [18, 6]), (2, [3, 3]), (3, [9, 6, 3, 6]), (4, [9, 3, 3, 3, 3, 3])):
        pen = QtGui.QPen()
        pen.setWidth(1)
        _gdi_pen_style(pen, Style(line_style=style))
        assert pen.dashPattern() == pattern and pen.capStyle() == QtCore.Qt.FlatCap
        wide = QtGui.QPen()
        wide.setWidth(3)
        _gdi_pen_style(wide, Style(line_style=style))
        assert wide.style() == QtCore.Qt.SolidLine  # GDI: a wide dashed pen is solid
    # end-cap / join bits: an ExtCreatePen geometric pen keeps its dashes
    pen = QtGui.QPen()
    pen.setWidth(3)
    _gdi_pen_style(pen, Style(line_style=1, style_flags=bytes([0, 0x21, 0, 0, 3, 0xFF])))
    assert pen.dashPattern() == [3, 1] and pen.capStyle() == QtCore.Qt.FlatCap


def test_button_caret_n_caption_breaks_lines(host, tmp_path):
    doc = _doc(host)
    i = doc.create_object("button", rect=(20, 20, 140, 70), text="Start^nMotor")
    doc.set_font(i, justify="left")
    sc = _scene(doc)
    caps = [c for c in sc._items[i].childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
    assert [c.text() for c in caps] == ["Start", "Motor"]
    assert caps[1].pos().y() > caps[0].pos().y()
    assert caps[0].pos().x() == caps[1].pos().x()  # left-aligned in the block
    doc.set_font(i, justify="centre")
    sc = _scene(doc)
    caps = [c for c in sc._items[i].childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
    assert caps[0].pos().x() != caps[1].pos().x()  # centred lines of unequal width


def test_own_style_rect_with_touch_child_draws_its_own_line(host, tmp_path):
    """Live: a factory rectangle that owns a touch command shows no
    border in CtDraw (it draws its own line slot) -- the art-stripped
    genie-face heuristics (grey XP edge, rounded corners) are not applied."""
    doc = _doc(host)
    i = doc.create_object("rectangle", rect=(20, 20, 120, 60), colour=0x336699)
    doc.add_input(i, "touch", {"CMD": "Beep(1)"})
    sc = _scene(doc)
    item = sc._items[i]
    assert isinstance(item, QtWidgets.QGraphicsRectItem)  # square corners
    assert item.pen().color().rgb() & 0xFFFFFF == 0x336699


def test_ellipse_pie_angles_run_clockwise_from_12(host, tmp_path):
    """CtDraw's angles (as format/ctf.py bakes them): 0 = 12
    o'clock, increasing clockwise; the pie runs clockwise from start to end."""
    doc = _doc(host)
    i = doc.create_object("ellipse", rect=(0, 0, 100, 100), colour=0x000000)
    doc.set_ellipse(i, style=1, start=0, end=90)
    sc = _scene(doc)
    path = sc._items[i].path()
    assert path.contains(QtCore.QPointF(75, 25))  # top right quadrant
    assert not path.contains(QtCore.QPointF(25, 75))


# ------------------------------------------------------ the engine check, offline
def test_engine_roundtrip_appearance_cases_offline(host, tmp_path):
    """The 'appearance' round-trip cases apply offline."""
    import importlib.util
    from ctgedit.format import colourstate, geometry as G, writer
    from ctgedit.format.page import parse_ctg_bytes

    spec = importlib.util.spec_from_file_location(
        "engine_roundtrip", os.path.join(os.path.dirname(__file__), "..", "tools", "engine_roundtrip.py")
    )
    ER = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ER)
    page = str(tmp_path / "FxAppearance.ctg")
    shutil.copy2(host, page)
    fails = []
    mutate, labels = ER.appearance_mutator(page, log=fails.append)
    assert not fails and len(labels) == len(ER.APPEARANCE_CASES)
    from ctgedit.format.page import parse_ctg

    p = parse_ctg(page)
    handles = mutate(p)
    data = writer.serialize_ctg(p.container, p.page, p.attributes)
    back = {G.get_handle(po): po for po in parse_ctg_bytes(data).page.objects}
    ours = {G.get_handle(po): po for po in p.page.objects}
    for h in handles:
        po, re = ours[h], back[h]
        label = labels[h]
        assert writer.object_payload(re) == writer.object_payload(po), label
        before = writer.object_payload(re)
        colourstate.rebuild(re)
        colourstate.derive_dynamic_flag(re)
        assert writer.object_payload(re) == before, label
        fl = G.style_flags(re)
        gh = G.parse_geom_header(re.body_raw)
        pb = G.predict_bounds(re.type_code, gh.rect1, fl) if fl else None
        if pb is not None:
            assert tuple(gh.rect2) == tuple(pb), label


# ------------------------------------------------------------- properties panel
def test_panel_line_colour_row_for_closed_shapes(host, tmp_path):
    _app()
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = _doc(host)
    objs = _make(doc)
    labels = {}
    for kind in ("rect", "text"):
        panel = PropertyPanel()
        panel.set_target(doc, [objs[kind]])
        labels[kind] = {lab.text() for lab in panel.findChildren(QtWidgets.QLabel)}
    assert {"Fill colour", "Line colour"} <= labels["rect"]
    assert "Foreground" in labels["text"] and "Line colour" not in labels["text"]


def test_panel_fill_colour_writes_the_fill_slot_of_a_hollow_shape(host, tmp_path, monkeypatch):
    """The panel's 'Fill colour' row on a HOLLOW closed shape writes the FILL slot and shows
    it, and leaves the shape hollow."""
    _app()
    from ctgedit.format import geometry as G
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = _doc(host)
    i = doc.create_object("rectangle_outline", rect=(20, 20, 120, 80), colour=0x112233)
    line_before = _block(doc, i).slots[G.SLOT_LINE]
    panel = PropertyPanel()
    panel.set_target(doc, [i])
    # (the panel's swatches open the one colour picker, ui.colour_picker)
    from ctgedit.ui import colour_picker

    monkeypatch.setattr(colour_picker, "choose_colour", lambda *a, **k: (0x336699, 0x336699))
    btn = QtWidgets.QPushButton()
    panel._on_object_colour(i, btn)
    cb = _block(doc, i)
    assert cb.slots[G.SLOT_FILL] == (0x336699, 0x336699)
    assert cb.slots[G.SLOT_LINE] == line_before  # the outline is untouched
    assert not (cb.flags[G.FLAG_FILL_STYLE] & 0x0F)  # still hollow: 'Filled' decides
    assert panel._fill_slot_colour(i) == 0x336699


def test_removing_width_makes_a_button_auto_width(host, tmp_path):
    """Removing WIDTH (or setting 0) makes the button auto-width again."""
    doc = _doc(host)
    i = doc.create_object("button", rect=(20, 20, 220, 50), text="Go")
    assert doc.page.objects[i].geometry.extent_x == 200
    assert doc.remove_property(i, "WIDTH")
    auto = doc.page.objects[i].geometry.extent_x
    assert 21 <= auto < 200
    assert doc.object_properties(i).get("WIDTH") == str(auto)
    j = doc.create_object("button", rect=(20, 80, 220, 110), text="Go")
    assert doc.set_property(j, "WIDTH", "abc")
    assert doc.page.objects[j].geometry.extent_x == auto
    assert doc.object_properties(j).get("WIDTH") == str(auto)


# ------------------------------------------ live-editor render rules
def test_flashing_pair_paints_its_first_half(host, tmp_path):
    """CtDraw's editor paints the FIRST stored half of a flashing (on, off) pair
    (its pen / brush / caption picks take it while the flash phase is set):
    live, rect_fill_flashing (on 00FF00, off 0000FF) is green and
    line_colour_flashing (CC0000 / 0000CC) is red."""
    from ctgedit.format import geometry as G

    doc = _doc(host)
    r = doc.create_object("rectangle", rect=(20, 20, 100, 60), colour=0x336699)
    doc.set_colour_slot(r, G.SLOT_FILL, 0x00FF00, 0x0000FF)
    ln = doc.create_object("line", nodes=[(120, 20), (200, 60)])
    doc.set_colour_slot(ln, G.SLOT_LINE, 0xCC0000, 0x0000CC)
    assert doc.page.objects[r].style.fill_colour == 0x00FF00
    assert doc.page.objects[ln].style.line_colour == 0xCC0000


def test_filled_arc_paints_its_chord(host, tmp_path):
    """A FILLED arc (ellipse type Arc) paints the chord region (BeginPath / Arc / EndPath / FillPath, then the Arc stroke) -- live,
    ellipse_arc_270_150 is a filled chord in the editor."""
    doc = _doc(host)
    e = doc.create_object("ellipse", rect=(20, 20, 120, 80), colour=0x3399FF)
    doc.set_ellipse(e, style=2, start=270, end=150)
    sc = _scene(doc)
    item = sc._items[e]
    assert isinstance(item, QtWidgets.QGraphicsPathItem)
    assert item.brush().style() != QtCore.Qt.NoBrush


def _captions(sc, i):
    return [
        (c.text(), c.pos().x(), c.pos().y(), c.brush().color().rgb() & 0xFFFFFF)
        for c in sc._items[i].childItems()
        if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)
    ]


def test_raised_button_caption_draws_its_3d_copies(host, tmp_path):
    """a Raised button caption = the lolight copy at (+d, +d), the
    hilight copy at (-d, -d), then the caption -- live, button_raised shows a
    white copy up-left and a grey one down-right. No effect = one caption."""
    doc = _doc(host)
    plain = doc.create_object("button", rect=(20, 20, 110, 50), text="Go")
    raised = doc.create_object("button", rect=(20, 80, 110, 110), text="Go")
    doc.set_effect(raised, 1)
    sc = _scene(doc)
    assert len(_captions(sc, plain)) == 1
    caps = _captions(sc, raised)
    assert len(caps) == 3
    (_, x0, y0, _c0) = caps[-1]  # the caption itself, drawn last
    st = doc.page.objects[raised].style
    offs = {(x - x0, y - y0): col for _t, x, y, col in caps[:-1]}
    assert offs == {(1.0, 1.0): st.lolight_colour, (-1.0, -1.0): st.hilight_colour}


def test_raised_text_draws_hilight_and_lolight_copies(host, tmp_path):
    """The text draw's effects switch: Raised = lolight at +d, hilight at -d, then
    the text (live: Dynamic_1's raised '#######' shows its hilight copy up-left)."""
    doc = _doc(host)
    t = doc.create_object("text", rect=(20, 20, 120, 40), text="Raised")
    doc.set_effect(t, 1)
    sc = _scene(doc)
    group = sc._items[t]
    texts = [c for c in group.childItems() if isinstance(c, QtWidgets.QGraphicsSimpleTextItem)]
    assert len(texts) == 3
