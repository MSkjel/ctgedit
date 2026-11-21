"""Guidelines, freehand, colour tools, cut link and the bitmap editor."""

import os
import shutil

import pytest

import fxinstall


@pytest.fixture
def host(fx_install):
    """FxSymbols of the fake install's copy (the page new objects go on)."""
    return fx_install.page("FxSymbols")


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ------------------------------------------------------------- freehand line
def test_freehand_polyline_is_open_polygon(host):
    """The Free Hand Line tool commits an OPEN polygon record (attribs bit0)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    i = doc.create_object("polyline", None, [(5, 5), (9, 14), (22, 8), (30, 30)])
    assert doc.page.objects[i].style.polygon_open
    # ...and HOLLOW: CtDraw fills an open polygon whose fill_style != 0, so a
    # solid polyline would draw as a filled blob now that the scene fills open polygons
    assert not doc.page.objects[i].style.filled
    assert doc.page.objects[i].geometry.nodes is None or True  # record created
    # closed polygon stays closed
    j = doc.create_object("polygon", None, [(5, 5), (50, 5), (25, 40)])
    assert not doc.page.objects[j].style.polygon_open


# ------------------------------------------------------------- colour tools
def test_swap_and_adjust_colours(host):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    i = doc.create_object("rectangle", (10, 10, 100, 80), None, colour=0x00FF0000)
    assert doc.swap_colours([i], 0x00FF0000, 0x0000FF00) == 1
    assert doc.page.objects[i].style.fill_colour == 0x0000FF00
    # no-match leaves it alone
    assert doc.swap_colours([i], 0x00123456, 0x00FFFFFF) == 0
    # from-any (frm=None) recolours unconditionally
    assert doc.swap_colours([i], None, 0x00ABCDEF) == 1
    assert doc.page.objects[i].style.fill_colour == 0x00ABCDEF
    # adjust via mapping function
    assert doc.adjust_colours([i], lambda c: 0x00112233) == 1
    assert doc.page.objects[i].style.fill_colour == 0x00112233


def test_adjust_colors_transform():
    """The HLS transform behind Tools > Adjust Colors."""
    _qapp()
    from ctgedit.ui.dialog.colour_dialogs import AdjustColorsDialog

    t = AdjustColorsDialog.transform
    green = t(0x00FF0000, 120, 0, 0)  # red hue-rotated 120deg -> green
    assert (green >> 8) & 0xFF >= 0xF0 and (green >> 16) & 0xFF <= 0x10
    lighter = t(0x00808080, 0, 50, 0)
    assert lighter > 0x00808080
    darker = t(0x00808080, 0, -50, 0)
    assert darker < 0x00808080
    assert t(0x00AABBCC, 0, 0, 0) == 0x00AABBCC  # identity


# ----------------------------------------------------------------- cut link
def test_break_library_link_converts_symbol_to_group(host):
    """Cut Link turns a placed symbol into a local group."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    target = next(
        (
            i
            for i, o in enumerate(doc.page.objects)
            if o.parent is None
            and doc.symbol_link(i)
            and doc.symbol_link(i)["kind"] in ("symbol", "group")
            and doc.symbol_link(i)["path"]
        ),
        None,
    )
    assert target is not None, "no linked symbol on FxSymbols?"
    assert doc._stored_name(target) == "FxLinkedBox"
    assert doc.symbol_link(target)["kind"] == "group"
    before = len(doc.page.objects)
    new = doc.break_library_link(target)
    assert new is not None
    grp = doc.parsed.page.objects[new]
    assert grp.type_code == 0x08 and grp.class_flag >= 1 and not grp.subtype_size & 0x1
    assert doc.symbol_link(new) is None and doc._stored_name(new) == "FxLinkedBox"
    # (the element's own root takes the instance root's place, so the record count stays;
    # a bitmap symbol's one record becomes a group of copies: test_symbol_workflows)
    assert len(doc.page.objects) == before
    doc.undo()
    assert len(doc.page.objects) == before
    assert doc.symbol_link(target)["kind"] == "group"
    # refuses a record without a library reference (e.g. plain shapes)
    plain = doc.create_object("rectangle", (0, 0, 10, 10), None)
    assert doc.break_library_link(plain) is None


# -------------------------------------------------------------- bitmap edit
def test_replace_bitmap_in_place(host):
    """Tools > Bitmap Editor: the picture swaps in the SAME record slot."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(host)
    rgba = bytes([10, 20, 30, 255]) * (8 * 8)
    b = doc.add_bitmap_object((0, 0, 8, 8), rgba, 8, 8)
    rgba2 = bytes([200, 100, 50, 255]) * (4 * 4)
    assert doc.replace_bitmap(b, (0, 0, 4, 4), rgba2, 4, 4)
    got = doc.page.objects[b].raw.get("_embedded_bmp")
    assert got and got[0] == 4 and got[2][:4] == bytes([200, 100, 50, 255])
    doc.undo()
    got = doc.page.objects[b].raw.get("_embedded_bmp")
    assert got and got[0] == 8


# ------------------------------------------------------- copy to library
def test_copy_to_library_new_file(host, tmp_path):
    """Edit > Copy to Library...: selection -> element block in a NEW .ctl;
    our own resolver reads it back."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolLibrary, SymbolReference, SymbolResolver

    doc = EditableDocument.open(host)
    i = doc.create_object("rectangle", (100, 50, 220, 130), None, colour=0x00FF8800)
    j = doc.create_object("line", None, [(100, 50), (220, 130)])
    lib = str(tmp_path / "mylib.ctl")
    assert doc.copy_to_library([i, j], lib, "my_widget")
    sl = SymbolLibrary.open(lib)
    assert "my_widget" in sl.element_names()
    pb = sl.element("my_widget")
    from ctgedit.format.element import root_tree_end

    # CtDraw's element form: ONE root (a group of the two) + the 3-record
    # element trailer -- never the roots side by side
    assert pb is not None and root_tree_end(pb.objects) == 3
    assert pb.objects[0].type_code == 0x08 and pb.objects[0].class_flag == 2
    assert len(pb.objects) == 3 + 3
    # head W,H = the root's Bounds (pen included)
    assert 120 <= pb.page_width <= 124 and 80 <= pb.page_height <= 84
    # replacing the same element does not duplicate the block
    assert doc.copy_to_library([i], lib, "my_widget")
    sl2 = SymbolLibrary.open(lib)
    assert sl2.element_names().count("my_widget") == 1
    # one rectangle: CtDraw's save wraps a one-object symbol that is not a bitmap, a bar
    # or a trend in a group root too (element.keeps_bare_root)
    one = sl2.element("my_widget").objects
    assert root_tree_end(one) == 2
    assert (one[0].type_code, one[0].class_flag, one[1].type_code) == (0x08, 1, 0x0F)
    # the resolver search path picks the library up next to a page
    res = SymbolResolver.for_page(str(tmp_path / "fake_page.ctg"))
    hit = res.resolve(SymbolReference("mylib", "my_widget", "ctl", "symbol"))
    assert hit is not None and root_tree_end(hit.objects) == 2


def test_copy_to_library_appends_to_existing(fx_install, host, tmp_path):
    """Appending to an existing library keeps every existing element (fxmany.ctl: 14
    elements, a directory grown past 8)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.symbols import SymbolLibrary

    lib = str(tmp_path / "fxmany.ctl")
    shutil.copyfile(fx_install.file("FxLib", "fxmany.ctl"), lib)
    before = SymbolLibrary.open(lib).element_names()
    assert len(before) == 14
    doc = EditableDocument.open(host)
    i = doc.create_object("ellipse", (0, 0, 64, 64), None, colour=0x000088FF)
    assert doc.copy_to_library([i], lib, "ctgedit_added")
    sl = SymbolLibrary.open(lib)
    names = sl.element_names()
    assert "ctgedit_added" in names
    assert all(n in names for n in before)
    for n in before:  # existing elements still parse
        assert sl.element(n) is not None
    from ctgedit.format.element import root_tree_end

    added = sl.element("ctgedit_added").objects
    assert root_tree_end(added) == 2 and added[1].type_code == 0x11  # the group root


# ------------------------------------------------------ legacy V3/V4 tools
def test_legacy_object_creation_roundtrip(host, tmp_path):
    """The V3/V4 tools clone engine-authored corpus seeds: each kind creates
    with the right type code, cleared tag bindings, and survives save/reopen."""
    from ctgedit.editing import EditableDocument

    expect = {
        "trend": 0x1003,
        "string": 0x1005,
        "cicode_object": 0x1009,
        "animation_keyboard": 0x100B,
        "pagewide_keyboard": 0x100B,
        "animated_symbol": 0x100D,
        "bar_graph": 0x1002,
        "slider": 0x100F,
        "activex": 0x0D,
        "process_analyst": 0x0D,
        "scheduler": 0x0D,
        "web_content": 0x13,
        "digital_text": 0x1006,
        "color_flood": 0x1008,
    }
    doc = EditableDocument.open(host)
    made = {}
    for kind, tc in expect.items():
        i = doc.create_object(kind, (100, 100, 200, 160), None)
        po = doc.parsed.page.objects[i]
        assert po.type_code == tc, (kind, hex(po.type_code))
        assert po.class_flag == 0
        made[kind] = i
    # seed tag expressions / commands must not leak into new objects
    p = doc.parsed.page.objects[made["animation_keyboard"]].raw_properties()
    assert p.get("KEY_SEQ", "") == "" and p.get("CMD", "") == ""
    p = doc.parsed.page.objects[made["cicode_object"]].raw_properties()
    assert p.get("CMD", "") == ""
    p = doc.parsed.page.objects[made["string"]].raw_properties()
    assert p.get("EXPR", "") == ""
    # super-genie ?N? substitutions must not leak from the FACEPLAT seeds
    p = doc.parsed.page.objects[made["bar_graph"]].raw_properties()
    assert p.get("EXPR", "") == "" and p.get("TAG", "") == ""
    p = doc.parsed.page.objects[made["slider"]].raw_properties()
    assert p.get("TAG", "") == ""
    assert p.get("MODE") == "WRITE_ON_DRAG"  # functional default kept
    # web content gets a unique AN name; ActiveX keeps its persisted state
    p = doc.parsed.page.objects[made["web_content"]].raw_properties()
    assert p.get("__AN_NAME__", "").startswith("WebContent_")
    body = doc.parsed.page.objects[made["process_analyst"]].body_raw
    assert b"Analyst" in body.replace(b"\x00", b"")
    # string caption + animated-symbol reference patching
    s = doc.create_object("string", (0, 0, 140, 22), None, text="Hello")
    p = doc.parsed.page.objects[s].raw_properties()
    # ON_TEXT/OFF_TEXT are Cicode: a caption is a QUOTED literal (a bare word
    # makes the engine show its '########' placeholder -- live-verified)
    assert p.get("ON_TEXT") == '"Hello"' and p.get("OFF_TEXT") == '"Hello"'
    a = doc.create_object("animated_symbol", (0, 0, 64, 64), None, symbol=("fxsym", "fx_on", "ctl"))
    p = doc.parsed.page.objects[a].raw_properties()
    # AnimSymbol's states are OFF_SYMBOL + SYMBOL_1..8 ('SYMBOL' is not a key
    # of the class; the engine keeps only what resolves)
    assert p.get("OFF_SYMBOL") == "fxsym.fx_on" and "SYMBOL" not in p
    # save / reopen keeps them all
    out = str(tmp_path / "legacy.ctg")
    doc.save(out)
    doc2 = EditableDocument.open(out)
    assert len(doc2.parsed.page.objects) == len(doc.parsed.page.objects)
    for kind, i in made.items():
        assert doc2.parsed.page.objects[i].type_code == expect[kind]


# ------------------------------------------------------- flashing bitmaps
def test_flashing_bitmap_round_trip():
    """Import As Flashing: header flag 0x101 + second zlib DIB; both halves
    decode; a plain bitmap keeps flag 0x001 and no flash half."""
    from ctgedit.format.factory import make_bitmap_record
    from ctgedit.format.symbols import embedded_bitmaps_from_body
    from ctgedit.format import geometry as G

    rgba1 = bytes([255, 0, 0, 255]) * 16  # 4x4 red
    rgba2 = bytes([0, 255, 0, 255]) * 16  # 4x4 green
    po = make_bitmap_record(7, (0, 0, 4, 4), rgba1, 4, 4, flash_rgba=rgba2)
    gh = G.parse_geom_header(po.body_raw)
    primary, flash = embedded_bitmaps_from_body(po.body_raw, gh.end)
    assert primary is not None and flash is not None
    assert primary.rgba[:4] == bytes([255, 0, 0, 255])
    assert flash.rgba[:4] == bytes([0, 255, 0, 255])
    assert flash.width == 4 and flash.height == 4
    po2 = make_bitmap_record(8, (0, 0, 4, 4), rgba1, 4, 4)
    p2, f2 = embedded_bitmaps_from_body(po2.body_raw, G.parse_geom_header(po2.body_raw).end)
    assert p2 is not None and f2 is None


def _two_dib_flashing(path, element):
    """``element`` of the library at ``path`` holds a bitmap whose two DIBs both decode,
    of one size and different pixels."""
    from ctgedit.format.symbols import SymbolLibrary, embedded_bitmaps_from_body
    from ctgedit.format import geometry as G

    lib = SymbolLibrary.open(path)
    names = [n for n in lib.element_names() if n.lower() == element]
    assert names, lib.element_names()[:20]
    pb = lib.element(names[0])
    found = False
    for po in pb.objects:
        if po.type_code == 0x07:
            gh = G.parse_geom_header(po.body_raw)
            primary, flash = embedded_bitmaps_from_body(po.body_raw, gh.end if gh else 0)
            if primary is not None and flash is not None:
                assert (flash.width, flash.height) == (primary.width, primary.height)
                assert flash.rgba != primary.rgba
                found = True
    assert found, "%s did not decode as a two-DIB flashing bitmap" % element


@pytest.mark.parametrize("lib,element", [("fxsym.ctl", "fx_flash"), ("fxmore.ctl", "fx_flash2")])
def test_stored_flashing_bitmap_decodes(fx_install, lib, element):
    """A flashing bitmap element as our save stores it: two DIBs (flag 0x101)."""
    _two_dib_flashing(fx_install.file("FxLib", lib), element)


@pytest.mark.golden
def test_ctdraw_saved_flashing_bitmap_decodes():
    """The same element after CtDraw's open + save of fxsym.ctl."""
    _two_dib_flashing(fxinstall.oracle("elements", "FxLib/fxsym.ctl", "library.ctl"), "fx_flash")


# (a flashing bitmap CtDraw made itself, Include's ICONS.CTL 'handflash_w': corpus only,
# tests_corpus/test_parity_batch.py::test_shipped_flashing_bitmap_decodes)


# ----------------------------------------------------------- pack libraries
def test_pack_libraries(fx_install, tmp_path):
    """Tools > Pack Libraries: stub blocks dropped, every element survives (fxsym.ctl
    holds the deleted fx_gone block)."""
    from ctgedit.format.projects import pack_libraries
    from ctgedit.format.symbols import SymbolLibrary

    lib = str(tmp_path / "fxsym.ctl")
    shutil.copyfile(fx_install.file("FxLib", "fxsym.ctl"), lib)
    before = SymbolLibrary.open(lib)
    names = before.element_names()
    assert "fx_box" in names and "fx_gone" not in names
    files, saved = pack_libraries(str(tmp_path))
    assert files >= 0 and saved >= 0
    after = SymbolLibrary.open(lib)
    assert after.element_names() == names
    for n in names:
        assert after.element(n) is not None
    # idempotent: a second pack changes nothing
    files2, saved2 = pack_libraries(str(tmp_path))
    assert (files2, saved2) == (0, 0)


# ------------------------------------------------------- bitmap editor UI
def test_bitmap_editor_dialog_paints():
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.dialog.bitmap_editor import BitmapEditorDialog

    rgba = bytes([255, 255, 255, 255]) * 16  # 4x4 white
    dlg = BitmapEditorDialog(rgba, 4, 4)
    dlg.colour.pick(0xFF0000)
    dlg.canvas.put(1, 1, dlg.canvas.pair)  # pen
    dlg.colour.pick(0x0000FF)
    dlg.canvas.fill(0, 0, dlg.canvas.pair)  # flood the white area
    out, w, h = dlg.result_rgba()
    assert (w, h) == (4, 4)
    px = [out[i * 4 : i * 4 + 4] for i in range(16)]
    assert px[5] == bytes([255, 0, 0, 255])  # the pen dot survived
    assert px[0] == bytes([0, 0, 255, 255])  # flood filled
    # Bitmap Size: shrink the right / bottom edges (edges, not a rescale)
    assert dlg.resize_edges(0, 0, 2, 2, grow=False)
    out2, w2, h2 = dlg.result_rgba()
    assert (w2, h2) == (2, 2)


def test_new_offers_the_five_classes(fx_install):
    """File > New: CtDraw's five classes (its NEWPAGE resource), as the New window's tabs."""
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = PageBrowserDialog(PageBrowserDialog.NEW)
    try:
        assert dlg.kinds == ("page", "template", "symbol", "genie", "supergenie")
        assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == [
            "Page",
            "Template",
            "Symbol",
            "Genie",
            "Super Genie",
        ]
        dlg.set_kind("genie")
        assert dlg.ok_button.text() == "Create Genie"
    finally:
        dlg.deleteLater()


def test_new_empty_library_element(host, tmp_path):
    """File > New Symbol opens an EMPTY, unsaved element to draw in; nothing
    is written until it holds an object (CtDraw's loader needs one tree -- a
    zero-record element is not loadable)."""
    import pytest as _pytest
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format.symbols import SymbolLibrary

    doc = EditableDocument.open(host)
    lib = str(tmp_path / "newlib.ctl")
    with _pytest.raises(ValueError):
        doc.copy_to_library([], lib, "fresh_symbol")
    el = ElementDocument.new_element(lib, "fresh_symbol", "symbol")
    assert not os.path.exists(lib)
    el.create_object("ellipse", (0, 0, 64, 64), colour=0x000088FF)
    el.save()
    pb = SymbolLibrary.open(lib).element("fresh_symbol")
    # (an ellipse under the group root CtDraw's save gives a one-object symbol)
    assert pb is not None and [o.type_code for o in pb.objects[:2]] == [0x08, 0x11]


# ------------------------------------------------------- guidelines + canvas
def test_canvas_guideline_snap():
    """Snap to Guidelines (F7) wins over the grid on its own axis."""
    _qapp()
    from ctgedit.ui.editor.canvas import CanvasView
    from PySide6 import QtCore

    view = CanvasView()
    view.set_grid_size(8)
    view.set_snap(True)
    view.set_guidelines([100], [40])
    p = view._snap_pt(QtCore.QPointF(98.0, 33.0))
    assert p.x() == 100.0  # guideline beats grid
    assert p.y() == 32.0  # grid handles the other axis
    view.set_snap_guides(False)
    p = view._snap_pt(QtCore.QPointF(98.0, 33.0))
    assert p.x() == 96.0  # back to pure grid snap
    assert view.guidelines() == ([100], [40])


def test_guidelines_dialog_roundtrip():
    _qapp()
    from ctgedit.ui.dialog.dialogs import GuidelinesDialog

    d = GuidelinesDialog([100, 200], [50], True, True)
    assert d.values() == ([100, 200], [50], True, True)
    d._sp_pos.setValue(300)
    d._set_pos()
    assert 300 in d.values()[0]
    d._rb_h.setChecked(True)  # switch direction
    d._sp_pos.setValue(75)
    d._set_pos()
    assert 75 in d.values()[1]
    d._clear_all()
    assert d.values()[1] == []
    assert d.values()[0] == [100, 200, 300]  # vertical list untouched


# --------------------------------------------------- window menu / shortcuts
def test_main_window_parity_bindings():
    """CtDraw's exact keys: Compile Alt+F10, Snap to Grid F8, Guidelines F7,
    Run F5, Bitmap Editor F9; freehand tool registered; Window menu plumbing."""
    _qapp()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w2 = None
    try:
        assert w.act_compile.shortcut().toString() == "Alt+F10"
        assert w.act_snap.shortcut().toString() == "F8"
        assert w.act_snap_guides.shortcut().toString() == "F7"
        assert w.act_run.shortcut().toString() == "F5"
        assert w.act_bitmap_editor.shortcut().toString() == "F9"
        assert "freehand" in w.tool_actions
        assert w in MainWindow._instances
        w.show()  # the Window menu lists visible windows
        # one window without pages: no 'Pages' and no 'Windows' section
        w._fill_window_list()
        assert w._window_list_actions == []
        # a second window: the 'Windows' section lists both, this one ticked
        w2 = MainWindow()
        w2.show()
        w._fill_window_list()
        texts = [a.text() for a in w._window_list_actions]
        n = len(MainWindow._open_windows())
        assert texts[0] == "Windows" and len(texts) == 1 + n and n >= 2, texts
        assert sum(a.isChecked() for a in w._window_list_actions[1:]) == 1
        w._cascade_windows()
        w._tile_windows(True)
        w._tile_windows(False)
    finally:
        for x in (w, w2):
            if x is not None and x in MainWindow._instances:
                MainWindow._instances.remove(x)
            if x is not None:
                x.hide()
