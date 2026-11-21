"""The ribbon header's Search field."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app


def _pump(n=6):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    _pump()
    yield w
    w.palette_popup.hide()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


def test_the_search_field_sits_in_the_ribbon_header_before_the_utility_buttons(win):
    hs = win.header_search
    host = hs.parentWidget()
    assert host is not None and host.objectName() == "RibbonHeader"
    assert hs.field.placeholderText() == "Search"
    assert hs._chip.text() == "Ctrl+K"
    # before Compile Project in the header's utility row
    order = []
    lay = host.layout()
    for k in range(lay.count()):
        sub = lay.itemAt(k).layout()
        if sub is not None:
            order = [sub.itemAt(j).widget() for j in range(sub.count())]
    assert order[0] is hs
    assert order[1].defaultAction() is win.act_compile
    # it stays in Focus Mode
    win.act_focus_mode.trigger()
    _pump()
    try:
        assert hs.isVisible() and hs.field.isVisible()
    finally:
        win.act_focus_mode.trigger()


def test_typing_rect_shows_rectangle_under_the_field(win):
    hs = win.header_search
    p = win.palette_popup
    hs.field.setFocus(QtCore.Qt.OtherFocusReason)
    QtWidgets.QApplication.sendEvent(
        hs.field, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_R, QtCore.Qt.NoModifier, "r")
    )
    _pump()
    assert p.isVisible() and p._edit.text() == "r"
    p._edit.setText("rect")
    first = next(r for r in p._list.rows() if r.selectable)
    assert first.text == "Rectangle"
    # under the field, its right end under the field's
    f = hs.field
    top = f.mapToGlobal(QtCore.QPoint(0, f.height())).y()
    assert p.y() >= top
    right = f.mapToGlobal(QtCore.QPoint(f.width(), 0)).x()
    assert abs(p.geometry().right() - right) <= 6 or p.x() <= f.mapToGlobal(QtCore.QPoint(0, 0)).x()
    assert hs.field.text() == ""
    p.hide()
    _pump()
    # the keyboard goes back to the page, not into the field (it would open again)
    assert not hs.field.hasFocus()


def test_a_click_on_the_field_opens_the_palette(win):
    hs = win.header_search
    p = win.palette_popup
    ev = QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.MouseFocusReason)
    hs.eventFilter(hs.field, ev)
    _pump()
    assert p.isVisible() and p._edit.text() == ""
    p.hide()
    # focus coming back when the palette closes does not open it again
    ev = QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.PopupFocusReason)
    hs.eventFilter(hs.field, ev)
    _pump()
    assert not p.isVisible()


@pytest.mark.parametrize("width,state", [(1600, "wide"), (1280, "narrow"), (1050, "button")])
def test_the_field_narrows_with_the_window(win, width, state):
    from ctgedit.ui.style.theme import px

    win.resize(width, 900)
    _pump(10)
    hs = win.header_search
    got = hs.fit()
    assert got == state
    if state == "wide":
        assert hs.field.width() == px(hs.WIDE)
        assert hs.companions_named()
    elif state == "narrow":
        assert hs.field.width() == px(hs.NARROW)
    else:
        assert hs.button.isVisibleTo(hs) and not hs.field.isVisibleTo(hs)
        hs.button.click()
        _pump()
        assert win.palette_popup.isVisible()


def test_the_utility_buttons_lose_their_names_before_the_field_narrows(win):
    hs = win.header_search
    host = hs.parentWidget()
    win.resize(1600, 900)
    _pump(10)
    assert hs.fit() == "wide" and hs.companions_named()
    # less room in the header (more contextual tabs): the names go first, the field stays
    other = hs._other_width()
    text_w = hs._companion_width(QtCore.Qt.ToolButtonTextBesideIcon)
    icon_w = hs._companion_width(QtCore.Qt.ToolButtonIconOnly)
    from ctgedit.ui.style.theme import px

    room = other + px(hs.WIDE) + (text_w + icon_w) // 2
    host.resize(room, host.height())
    assert hs.fit() == "wide" and not hs.companions_named()
    host.resize(other + px(hs.NARROW) + icon_w, host.height())
    assert hs.fit() == "narrow"
    win.resize(1601, 900)
    _pump(10)


def test_the_new_settings_are_declared():
    from ctgedit.ui.prefs import settings as ST

    assert ST.SETTINGS_KEYS["ui/palette_pins"] is None
    assert ST.SETTINGS_KEYS["help/zoom"] == 0
    assert "help/splitter" in ST.SETTINGS_KEYS
    assert ST.key_group("ui/palette_pins") == "Ribbon and Quick Access"
    assert ST.key_group("help/splitter") == "Window layout and workspaces"
    assert ST.key_group("help/zoom") == "Options"


def test_f1_on_the_search_opens_its_help(win):
    from ctgedit.ui import help as UH

    assert UH.topic_for(win.header_search.field) == "the-window#search"
    assert UH.topic_for(win.palette_popup._edit) == "the-window#search"
    from ctgedit import help as H

    assert "search" in H.anchors("the-window") and "quick-open" in H.anchors("the-window")
    assert "prefixes" in H.anchors("the-window") and "the-tool-box" in H.anchors("the-window")
    assert "using-the-manual" in H.anchors("index")
