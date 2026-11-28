"""Colour entry and the colour windows."""

import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app


# ------------------------------------------------------------------ typed colours
def test_a_typed_colour_means_what_it_says(fx_install):
    """(The palette is the fake install's Bin/citect.pal: its classic colours have names.)"""
    _app()
    from ctgedit.format import colourpalette as CP
    from ctgedit.ui.colour_picker import TRANSPARENT, parse_colour_text

    pal = CP.Palette.for_project(None)
    assert parse_colour_text("#3A7BD5") == (0x3A7BD5, 0x3A7BD5)
    assert parse_colour_text("3a7bd5") == (0x3A7BD5, 0x3A7BD5)
    assert parse_colour_text("#38D") == (0x3388DD, 0x3388DD)
    assert parse_colour_text("58,123,213") == (0x3A7BD5, 0x3A7BD5)
    assert parse_colour_text("rgb(58, 123, 213)") == (0x3A7BD5, 0x3A7BD5)
    assert parse_colour_text("transparent") == (TRANSPARENT, TRANSPARENT)
    assert parse_colour_text("300,0,0") is None and parse_colour_text("nope") is None
    named = next(e for e in pal.entries if e.used and e.name and not e.transparent)
    assert parse_colour_text(named.name.upper(), pal) == (named.on, named.off)


def test_the_value_beside_a_swatch():
    _app()
    from ctgedit.ui.colour_picker import TRANSPARENT, short_text

    assert short_text(TRANSPARENT) == "Transparent"
    assert short_text(0x112233, 0x445566) == "Flashing"
    assert short_text(None) == ""
    assert short_text(0x123457) in ("#123457",)


def test_enter_in_the_pickers_entry_picks_the_colour():
    _app()
    from ctgedit.ui import colour_picker as C

    p = C.ColourPicker((0, 0), None, flash=False, transparent=False)
    got = []
    p.picked.connect(lambda a, b: got.append((a, b)))
    p.open_under(None, focus_entry=True)
    _app().processEvents()
    assert p.entry.hasFocus() or QtWidgets.QApplication.focusWidget() is p.entry
    p.entry.setText("#3A7BD5")
    p.entry.textEdited.emit("#3A7BD5")
    assert p.value_text.text() == "#3A7BD5"  # (the preview follows)
    assert p.pick_text()
    assert got == [(0x3A7BD5, 0x3A7BD5)] and p.result == (0x3A7BD5, 0x3A7BD5)
    q = C.ColourPicker((0, 0), None, flash=False, transparent=False)
    assert not q.pick_text("Transparent") and q.entry.property("ctgInvalid")
    assert not q.pick_text("not a colour")
    q.hide()


def test_typing_a_hex_colour_in_the_panels_picker_sets_the_selection(fx_page):
    """The acceptance: '#3A7BD5' + Enter in the picker sets that colour on the selection."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.format import colourstate
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = EditableDocument.open(fx_page("FxShapes"))
    r = doc.create_object("rectangle", (20, 20, 120, 80), None, colour=0x808080)
    panel = PropertyPanel()
    panel.show()
    panel.set_target(doc, [r])
    _app().processEvents()
    sw = next(
        w for w in panel.section("Appearance").findChildren(QtWidgets.QWidget) if hasattr(w, "value_text")
    )

    def type_it():
        p = C.ColourPicker.last_opened
        p.entry.setText("#3A7BD5")
        QTest.keyClick(p.entry, QtCore.Qt.Key_Return)

    QtCore.QTimer.singleShot(50, type_it)
    n = len(doc._undo)
    sw.value_text.click()
    assert len(doc._undo) == n + 1
    ch = colourstate.colour_channels(doc.parsed.page.objects[r])
    assert ch["fill"] == (0x3A7BD5, 0x3A7BD5)


# ------------------------------------------------------------------ Edit Favourite Colours
def test_favourite_colours_hex_eyedropper_and_recent():
    _app()
    from ctgedit.format import colourpalette as CP
    from ctgedit.ui import colour_picker as C
    from ctgedit.ui.dialog.colour_dialogs import FavoriteColorsDialog

    C.remember_colour(0x3A7BD5)
    C.remember_colour(0x22AA44)
    pal = CP.Palette.for_project(None)
    dlg = FavoriteColorsDialog(pal, None, current=(0xFF0000, 0xFF0000))
    assert dlg.hex.text() == "#FF0000"
    dlg.spins["g"].setValue(0x80)
    assert dlg.hex.text() == "#FF8000"
    dlg.hex.setText("rgb(58, 123, 213)")
    dlg.hex.setModified(True)
    assert dlg._on_hex()
    assert dlg.result_pair() == (0x3A7BD5, 0x3A7BD5) and dlg.spins["b"].value() == 213
    dlg.hex.setText("12ab34")
    dlg.hex.setModified(True)
    dlg._on_hex()
    assert dlg.result_pair() == (0x12AB34, 0x12AB34)
    dlg.hex.setText("no colour")
    dlg.hex.setModified(True)
    assert not dlg._on_hex() and dlg.hex.property("ctgInvalid")
    # the eye dropper's colour
    dlg._sampled(0x445566)
    assert dlg.result_pair() == (0x445566, 0x445566)
    # the recent colours
    assert len(dlg.recent_buttons) >= 2 and len(dlg.recent_buttons) <= 8
    dlg.recent_buttons[0].click()
    assert dlg.result_pair() == (0x22AA44, 0x22AA44)
    # the buttons say what they act on
    assert dlg.btn_add.toolTip() == "Add the colour to the first empty favourite"
    assert dlg.btn_replace.toolTip() == "Replace the selected favourite with the colour"
    assert dlg.btn_clear.toolTip() == "Clear the selected favourite"
    dlg.transparent.setChecked(True)
    assert not dlg.hex.isEnabled()


# ------------------------------------------------------------------ Swap Colours
def test_the_ramps_show_only_with_swap_range():
    _app()
    from ctgedit.ui.dialog.colour_dialogs import SwapColorsDialog

    dlg = SwapColorsDialog(None, frm=(0x336699, 0x336699))
    dlg._chk_range.setChecked(False)
    assert dlg._frm_bar.isHidden() and dlg._to_bar.isHidden()
    dlg._chk_range.setChecked(True)
    assert not dlg._frm_bar.isHidden() and not dlg._to_bar.isHidden()
    dlg.set_to((0x00FF00, 0x000000))  # (flashing: no range)
    assert dlg._frm_bar.isHidden()


def test_swap_from_and_to_and_the_colours_in_use():
    _app()
    from ctgedit.ui.dialog.colour_dialogs import SwapColorsDialog

    rows = [((0x505050, 0x505050), 36), ((0xFF0000, 0xFF0000), 2)]
    seen = []

    def colours_of(doc, indices, scope):
        seen.append((indices, scope))
        return rows

    dlg = SwapColorsDialog(None, frm=(0x336699, 0x336699), colours_of=colours_of)
    dlg.set_from((0x111111, 0x111111))
    dlg.set_to((0x222222, 0x222222))
    dlg.swap_from_to()
    assert dlg._frm.pair() == (0x222222, 0x222222) and dlg._to.pair() == (0x111111, 0x111111)
    dlg.set_selection(object(), [])
    assert [b.pair_ for b in dlg.in_use_buttons] == [r[0] for r in rows]
    assert dlg.in_use_buttons[0].toolTip().startswith("RGB 80,80,80: 36 objects")
    assert seen[-1][1] == "page"  # (nothing selected: the page)
    dlg.in_use_buttons[0].click()
    assert dlg._frm.pair() == (0x505050, 0x505050)
    dlg.pick_in_use((0xFF0000, 0xFF0000), to=True)
    assert dlg._to.pair() == (0xFF0000, 0xFF0000)
    dlg._chk_any.setChecked(True)
    dlg.in_use_buttons[1].click()  # (From any: a colour turns it off)
    assert not dlg._chk_any.isChecked() and dlg._frm.pair() == (0xFF0000, 0xFF0000)
    # the eye droppers
    assert not dlg._frm_drop.icon().isNull() and not dlg._swap.icon().isNull()


def test_on_a_page_the_colours_in_use_list_the_pages_grey(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_document(fx_page("FxColours"))
        w._on_canvas_selection([])
        w._swap_colors()
        dlg = w._dialogs.get("swapcol")
        dlg.apply_to.page.setChecked(True)
        _app().processEvents()
        pairs = [b.pair_ for b in dlg.in_use_buttons]
        assert pairs and len(pairs) <= dlg.IN_USE_MAX
        greys = [
            p
            for p in pairs
            if p[0] == p[1]
            and (p[0] >> 16) & 0xFF == (p[0] >> 8) & 0xFF == p[0] & 0xFF
            and 0 < (p[0] & 0xFF) < 0xFF
        ]
        assert greys, pairs
        counts = [b.count for b in dlg.in_use_buttons]
        assert counts == sorted(counts, reverse=True)
        b = next(x for x in dlg.in_use_buttons if x.pair_ == greys[0])
        b.click()
        assert dlg._frm.pair() == greys[0]
        dlg.reject()
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.hide()


# ------------------------------------------------------------------ Adjust Colours
def test_adjust_colours_in_degrees_with_range_bars():
    _app()
    from ctgedit.ui.dialog.colour_dialogs import AdjustColorsDialog

    dlg = AdjustColorsDialog(None)
    assert dlg._sp_from.suffix() == " °" and dlg._to_end.suffix() == " °"
    assert not dlg._to_end.isEnabled()
    dlg.set_from_start(10)
    dlg.set_from_end(50)
    bar = dlg._from_bar
    assert (bar.start(), bar.end()) == (10, 50)
    bar.resize(380, bar.height())
    # dragging the band moves both ends
    x0 = bar.x_of(30)
    QTest.mousePress(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(x0), 8))
    QTest.mouseMove(bar, QtCore.QPoint(int(bar.x_of(70)), 8))
    QTest.mouseRelease(bar, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(bar.x_of(70)), 8))
    assert dlg.from_end() - dlg._start == 40  # (the length kept)
    assert 45 <= dlg._start <= 55
    # keys: the focused handle, 1 degree, Shift 10
    bar.setFocus()
    bar.set_focused_handle("end")
    end = dlg.from_end()
    QTest.keyClick(bar, QtCore.Qt.Key_Right)
    assert dlg.from_end() == (end + 1) % 360
    QTest.keyClick(bar, QtCore.Qt.Key_Left, QtCore.Qt.ShiftModifier)
    assert dlg.from_end() == (end - 9) % 360
    # the To bar: one handle, its band as long as the From range
    dlg.set_to_start(200)
    assert (dlg._to_bar.start(), dlg._to_bar.end()) == (200, dlg.to_end())
    to = dlg._to_bar
    to.setFocus()
    QTest.keyClick(to, QtCore.Qt.Key_Right, QtCore.Qt.ShiftModifier)
    assert dlg._to == 210
    # the adjustment struct is the dialog's as before
    a = dlg.adjustment()
    assert a.hue_from == dlg._start * 10 and a.hue_to == dlg.from_end() * 10


def test_tab_goes_from_the_start_handle_to_the_end_handle():
    _app()
    from ctgedit.ui.dialog.colour_dialogs import HueRangeBar

    bar = HueRangeBar(True)
    bar.set_range(10, 60)
    assert bar.focused_handle() == "start"
    assert bar.focusNextPrevChild(True) and bar.focused_handle() == "end"
    assert bar.focusNextPrevChild(False) and bar.focused_handle() == "start"
    one = HueRangeBar(False)
    one.set_range(100, 130)
    assert one.colours()[115] != one.colours()[300]


def test_before_and_after():
    _app()
    from ctgedit.format import colourtools as T
    from ctgedit.ui.dialog.colour_dialogs import AdjustColorsDialog

    rows = [((0xFF0000, 0xFF0000), 3), ((0x00FF00, 0x00FF00), 1)]
    dlg = AdjustColorsDialog(None, colours_of=lambda d, i, s: rows)
    dlg.set_selection(object(), [])
    dlg.set_lightness(30)
    pairs = dlg.preview.pairs()
    assert [a for a, _b in pairs] == [0xFF0000, 0x00FF00]
    adj = dlg.adjustment()
    assert [b for _a, b in pairs] == [T.adjust_colour(c, adj) & 0xFFFFFF for c in (0xFF0000, 0x00FF00)]
    assert pairs[0][1] != 0xFF0000
