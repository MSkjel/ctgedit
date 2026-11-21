"""Guideline and selection outline widths."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

OY = 2000  # objects drawn this far below the page: nothing else there


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def host(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxThumb"))
    w.resize(1400, 1000)
    w.show()
    _pump()
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    yield w
    from ctgedit.ui.editor import canvas_look as C_look

    from ctgedit.ui.editor import canvas_look as C_look

    C_look.set_canvas_lines()
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()
    w.deleteLater()


def _set(**values):
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k, v in values.items():
        st.setValue("view/" + k, v)


def test_the_settings_and_their_rows():
    from ctgedit.ui.prefs.settings import SETTINGS_KEYS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    assert SETTINGS_KEYS["view/guide_width"] == "thin"
    assert SETTINGS_KEYS["view/guide_reach"] == "near"
    assert SETTINGS_KEYS["view/outline_width"] == "thin"
    rows = {r.key: r for r in SettingsDialog.ROWS}
    assert (rows["view/guide_width"].page, rows["view/guide_width"].section) == (
        "Canvas",
        "Grid and guidelines",
    )
    assert (rows["view/guide_reach"].page, rows["view/guide_reach"].section) == (
        "Canvas",
        "Grid and guidelines",
    )
    assert (rows["view/outline_width"].page, rows["view/outline_width"].section) == ("Appearance", "Canvas")
    for key in ("view/guide_width", "view/guide_reach", "view/outline_width"):
        assert rows[key].apply == "window:apply_select_options" and rows[key].tip


def test_the_window_reads_them_and_a_wide_guideline_is_grabbed_anywhere_on_it():
    _app()
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert (C_look.guide_width(), C_look.guide_reach(), C_look.outline_width()) == (1.0, 3.0, 1.0)
        _set(guide_width="thick", guide_reach="far", outline_width="medium")
        w.apply_select_options()
        assert (C_look.guide_width(), C_look.guide_reach(), C_look.outline_width()) == (3.0, 10.0, 2.0)
        _set(guide_width="thick", guide_reach="near", outline_width="nonsense")
        w.apply_select_options()
        assert C_look.guide_reach() == 3.0 and C_look.outline_width() == 1.0
        C_look.set_canvas_lines("thick", "near")
        assert C_look.guide_reach() >= C_look.guide_width() / 2.0  # (all of a wide one grabs)
    finally:
        C_look.set_canvas_lines()
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.deleteLater()


def test_a_guideline_is_grabbed_within_the_reach(host):
    from ctgedit.ui.editor import canvas_look as C_look

    v = host.view
    v.set_guides_visible(True)
    v.set_guidelines([700], [])
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(700, OY + 320))
    _pump(2)
    at = v.mapFromScene(QtCore.QPointF(700, OY + 320))
    off = at + QtCore.QPoint(5, 0)
    assert v._guide_at(at) == ("v", 700)
    assert v._guide_at(off) is None  # 3 px: 5 px away misses
    C_look.set_canvas_lines(reach="medium")
    assert v._guide_at(off) == ("v", 700)  # 6 px
    v.set_zoom(4.0)
    v.centerOn(QtCore.QPointF(700, OY + 320))
    _pump(2)
    at = v.mapFromScene(QtCore.QPointF(700, OY + 320))
    assert v._guide_at(at + QtCore.QPoint(5, 0)) == ("v", 700)  # screen px at any zoom
    assert v._guide_at(at + QtCore.QPoint(8, 0)) is None


def _strip(img, dpr, xs, ys, keep):
    return sum(1 for x in xs for y in ys if keep(QtGui.QColor(img.pixel(int(x * dpr), int(y * dpr)))))


def test_the_outlines_and_the_guidelines_are_drawn_as_wide_as_the_settings(host):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.style import theme

    w, v = host, host.view
    i = w.doc.create_object("rectangle", (300, OY + 300, 500, OY + 400), None, colour=0x202020)
    w._refresh()
    v.select_units([[i]], reveal=False)
    v.set_guides_visible(True)
    v.set_guidelines([700], [])
    v.set_zoom(1.0)
    v.centerOn(QtCore.QPointF(500, OY + 350))
    _pump(3)
    acc = QtGui.QColor(theme.canvas_colour("selection"))

    def accent(c):
        return (
            abs(c.red() - acc.red()) < 50
            and abs(c.green() - acc.green()) < 50
            and abs(c.blue() - acc.blue()) < 50
        )

    def counts():
        v.viewport().update()
        _pump(2)
        img = v.viewport().grab().toImage()
        dpr = img.devicePixelRatio() or 1.0
        edge = v.mapFromScene(QtCore.QPointF(300, OY + 310))  # the left edge, no handle
        bottom = v.mapFromScene(QtCore.QPointF(300, OY + 340))
        outline = _strip(img, dpr, range(edge.x() - 6, edge.x() + 7), range(edge.y(), bottom.y()), accent)
        g = v.mapFromScene(QtCore.QPointF(700, OY + 310))
        back = QtGui.QColor(img.pixel(int((g.x() - 20) * dpr), int(g.y() * dpr)))
        guide = _strip(
            img,
            dpr,
            range(g.x() - 6, g.x() + 7),
            range(g.y(), g.y() + 30),
            lambda c: abs(c.lightness() - back.lightness()) > 12,
        )
        return outline, guide

    thin_outline, thin_guide = counts()
    assert thin_outline > 0 and thin_guide > 0
    C_look.set_canvas_lines("thick", "near", "thick")
    thick_outline, thick_guide = counts()
    assert thick_outline >= 2 * thin_outline, (thin_outline, thick_outline)
    assert thick_guide >= 2 * thin_guide, (thin_guide, thick_guide)
    # back to thin: as before
    C_look.set_canvas_lines()
    assert counts() == (thin_outline, thin_guide)
