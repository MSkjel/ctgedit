"""The Search field: tips, keys, rows and its width."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=4):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui import help as UH
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    UH._last_main[:] = []
    yield w
    from ctgedit.ui.dialog.help_window import HelpWindow

    hw = HelpWindow._inst
    if hw is not None:
        hw.hide()
    w.palette_popup.hide()
    for m in w.menuBar().findChildren(QtWidgets.QMenu):
        m.hide()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


@pytest.fixture()
def shapes(win, fx_page):
    """The window with FxMain's FxShapes open (the fake install's copy: no object of it has
    AN 99999)."""
    win.open_document(fx_page("FxShapes"))
    _pump()
    return win


def _key(widget, key, mods=QtCore.Qt.NoModifier, text=""):
    QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text))


def test_the_magnifier_of_the_search_field_has_the_rich_tip(win):
    from ctgedit.ui.style import tips

    hs = win.header_search
    buttons = [b for b in hs.field.findChildren(QtWidgets.QToolButton) if b.defaultAction() is not None]
    assert buttons
    for b in buttons:
        assert b.property(tips.RICH_TIP_PROP)
        tip = tips.rich_tip(b.defaultAction(), b)
        assert "Search" in tip and "Find a command" in tip
        assert tips.plain_tip(b.defaultAction(), b).startswith("Search (")


def test_enter_in_the_manuals_search_box_never_runs_a_command(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("index", main=win)
    hw.search_for("smart guides")
    rows = hw.result_rows()
    assert rows[0].kind == "command"
    # the highlighted row is the first section, not the command above it
    assert hw.results.current_row_obj().kind == "help"
    was = win.act_smart_guides.isChecked()
    hw._result_enter()
    _pump()
    assert win.act_smart_guides.isChecked() is was
    # on a command row Enter in the box only shows the command
    hw.results.setCurrentRow(0)
    hw._result_enter()
    _pump()
    assert win.act_smart_guides.isChecked() is was
    for m in win.menuBar().findChildren(QtWidgets.QMenu):
        m.hide()


def test_a_double_click_on_a_command_row_never_runs_it(win):
    from ctgedit.ui import help as UH

    hw = UH.show_help("index", main=win)
    hw.search_for("smart guides")
    idx = hw.results.rows_model.index(0, 0)
    ran = []
    win.act_smart_guides.triggered.connect(lambda *_: ran.append(1))
    was = win.act_smart_guides.isChecked()
    try:
        hw._result_activated(idx)  # (clicked)
        hw._result_activated(idx)  # (the double-click's second press)
        _pump()
        # (a look-up: the command is shown in the menu, Shift+click runs it)
        assert ran == []
        for m in win.menuBar().findChildren(QtWidgets.QMenu):
            m.hide()
    finally:
        if win.act_smart_guides.isChecked() is not was:
            win.act_smart_guides.trigger()


def test_bold_words_never_land_in_the_key_cap_markup():
    _app()
    from ctgedit.ui import search_rows as SR
    from ctgedit.ui.dialog.help_window import HelpResultDelegate

    dl = HelpResultDelegate()
    dl.words = ["style", "color", "span"]
    r = SR.Row("help", "Page > Section", detail="Pick a style or a color with Ctrl+B <b>", meta=("Ctrl+B",))
    out = dl.snippet_html(r)
    assert "<span <b>" not in out and "<b>style</b>='" not in out
    assert out.count("<b>style</b>") == 1 and out.count("<b>color</b>") == 1
    assert "&nbsp;Ctrl+B&nbsp;</span>" in out
    assert "&lt;b&gt;" in out  # (the text's own '<b>' is text)


def test_the_snippet_starts_under_the_title_at_any_text_size():
    _app()
    from ctgedit.ui.dialog.help_window import HelpResultDelegate
    from ctgedit.ui.style.theme import px, sp

    assert HelpResultDelegate.snippet_x(10) == 10 + sp(2) + px(16) + sp(2)


def test_a_greyed_tile_keeps_the_palette_open_and_says_why(win):
    p = win.palette_popup
    p.open_at(QtGui.QCursor.pos(), toolbox=True)
    _pump()
    a = p.toolbox.action_at(1)
    assert a is not None
    a.setEnabled(False)
    fired = []
    a.triggered.connect(lambda *_: fired.append(1))
    try:
        _key(p._edit, QtCore.Qt.Key_1, text="1")
        _pump()
        assert p.isVisible() and not fired
        assert p.footer_text().startswith(a.text().replace("&", "").rstrip("…").rstrip("."))
    finally:
        a.setEnabled(True)
    assert not p.toolbox.pick(99)


def test_the_header_chip_follows_the_palettes_key(win):
    from ctgedit.ui.prefs import keymap

    hs = win.header_search
    keys = list(win.act_palette.shortcuts())
    try:
        win.act_palette.setShortcuts([])
        hs.refresh_keys()
        assert keymap.keys_of(win.act_palette) == []
        assert not hs._chip.isVisibleTo(hs.field)
        assert "Ctrl+K" not in hs.field.toolTip() and "Ctrl+K" not in hs.button.toolTip()
        win.act_palette.setShortcuts([QtGui.QKeySequence("Ctrl+Shift+P")])
        hs.refresh_keys()
        assert hs._chip.isVisibleTo(hs.field)
        assert "Ctrl+Shift+P" in hs._chip.text() and "Ctrl+Shift+P" in hs.field.toolTip()
    finally:
        win.act_palette.setShortcuts(keys)
        hs.refresh_keys()


def test_the_header_field_takes_the_width_of_the_text_size_again(win):
    from ctgedit.ui.style.theme import px

    win.resize(1280, 900)
    _pump(10)
    hs = win.header_search
    assert hs.fit() == "narrow"
    hs.field.setFixedWidth(10)
    assert hs.fit() == "narrow"
    assert hs.field.width() == px(hs.NARROW)


def test_ctrl_c_copies_the_highlighted_pages_name(shapes):
    from ctgedit.ui.dialog import quick_open as QO

    p = shapes.palette_popup
    p.open_at()
    p._edit.setText("p:fxshapes")
    _pump()
    r = p.current_row()
    assert r is not None and r.kind in QO.ENTER_HINT
    QtWidgets.QApplication.clipboard().setText("")
    _key(p._edit, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
    _pump()
    got = QtWidgets.QApplication.clipboard().text()
    assert got and got.lower().startswith("fxshapes")
    assert "clipboard" in p.footer_text()
    # a command row: Ctrl+C is the field's own
    assert not p._copy_current() or p.current_row().kind in QO.ENTER_HINT


def test_the_colon_note_says_no_object_has_that_an(win):
    from ctgedit.ui.dialog import quick_open as QO

    assert QO.goto_note(win, "") == "Type an AN after the colon"
    assert QO.goto_note(win, "x") == "Type an AN after the colon"
    assert QO.goto_note(win, "5").startswith("Open a page first")


def test_quick_open_colon_with_an_unknown_an(shapes):
    from ctgedit.ui.dialog import quick_open as QO

    assert QO.goto_note(shapes, "99999") == "No object has AN 99999"


def test_the_footer_drops_its_last_parts_to_fit():
    _app()
    from ctgedit.ui.dialog import palette

    fm = QtGui.QFontMetrics(QtWidgets.QApplication.font())
    text = "Enter runs · Shift+Enter runs and stays open · Alt+Enter assigns keys"
    assert palette.fit_footer(text, fm, 4000) == text
    first = fm.horizontalAdvance("Enter runs · Shift+Enter runs and stays open")
    assert palette.fit_footer(text, fm, first + 2) == "Enter runs · Shift+Enter runs and stays open"
    short = palette.fit_footer(text, fm, fm.horizontalAdvance("Enter") + 4)
    assert short.endswith("…") and fm.horizontalAdvance(short) <= fm.horizontalAdvance("Enter") + 4


def test_the_palettes_footer_fits_its_width(win):
    p = win.palette_popup
    p.open_at()
    _pump()
    lab = p._footer
    assert (
        lab.fontMetrics().horizontalAdvance(lab.text()) <= max(lab.contentsRect().width(), 1)
        or not lab.text()
    )
