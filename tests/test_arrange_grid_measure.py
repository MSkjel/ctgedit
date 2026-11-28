"""Space Equally, Arrange in Grid and the Measure tool."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app


@pytest.fixture()
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxShapes"))
    yield w
    for dlg in list(getattr(w, "_dialogs", {}).values()):
        dlg.reject()
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def _box(doc, i):
    return G.norm_rect(G.parse_geom_header(doc.parsed.page.objects[i].body_raw).rect1)


def _rects(win, *rects):
    idx = [win.doc.create_object("rectangle", rect=r, colour=0x336699) for r in rects]
    win._refresh()
    win._set_selection(list(idx))
    return idx


def _apply(dlg):
    dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).click()


# ------------------------------------------------------------------ the document
def test_space_equally_shares_the_room_between_the_outer_objects(win):
    doc = win.doc
    a, b, c = _rects(win, (100, 50, 140, 80), (160, 50, 240, 80), (400, 50, 420, 80))
    doc.space_equally([a, b, c], "h")
    ba, bb, bc = _box(doc, a), _box(doc, b), _box(doc, c)
    assert ba[0] == 100 and bc[0] == 400  # the outer two stay
    gap1, gap2 = bb[0] - ba[2], bc[0] - bb[2]
    assert abs(gap1 - gap2) <= 1 and gap1 > 0
    assert doc._undo[-1].label == "space equally"
    doc.undo()
    assert _box(doc, b)[0] == 160


def test_space_equally_with_a_gap_keeps_the_first_object(win):
    doc = win.doc
    a, b, c = _rects(win, (100, 50, 140, 80), (160, 50, 240, 80), (400, 50, 420, 80))
    doc.space_equally([a, b, c], "h", gap=15)
    ba, bb, bc = _box(doc, a), _box(doc, b), _box(doc, c)
    assert ba[0] == 100 and bb[0] == ba[2] + 15 and bc[0] == bb[2] + 15
    # vertical: the order is by top edge
    doc.space_equally([a, b, c], "v", gap=5)
    tops = sorted(_box(doc, i)[1] for i in (a, b, c))
    assert tops[1] == tops[0] + 30 + 5 and tops[2] == tops[1] + 30 + 5


def test_arrange_in_grid_lays_the_objects_out_in_columns(win):
    doc = win.doc
    # four objects of different sizes, in reading order a b c d
    a, b, c, d = _rects(
        win, (100, 100, 140, 120), (300, 105, 320, 140), (120, 300, 200, 320), (500, 310, 520, 330)
    )
    doc.arrange_in_grid([a, b, c, d], columns=2, hgap=10, vgap=8, by_rows=True)
    ba, bb, bc, bd = (_box(doc, i) for i in (a, b, c, d))
    assert (ba[0], ba[1]) == (100, 100)
    # column 0 is as wide as c (80), row 0 as high as b (35)
    assert bb[0] == 100 + 80 + 10 and bb[1] == 100
    assert bc[0] == 100 and bc[1] == 100 + 35 + 8
    assert bd[0] == 190 and bd[1] == 143
    assert doc._undo[-1].label == "arrange in grid"
    doc.undo()
    assert _box(doc, d) == (500, 310, 520, 330)
    # by columns: b goes under a (row 0 is as high as a and c: 20), c to the right of a
    # (column 0 is as wide as a and b: 40)
    doc.arrange_in_grid([a, b, c, d], columns=2, hgap=0, vgap=0, by_rows=False)
    assert _box(doc, b)[1] == 100 + 20 and _box(doc, b)[0] == 100
    assert _box(doc, c)[0] == 100 + 40 and _box(doc, c)[1] == 100


# ------------------------------------------------------------------ the windows
def test_space_equally_window_follows_the_selection_and_applies(win):
    doc = win.doc
    a, b, c = _rects(win, (100, 50, 140, 80), (160, 50, 240, 80), (400, 50, 420, 80))
    win.act_space_equally.trigger()
    dlg = win._dialogs.get("space_equally")
    assert dlg is not None and not dlg.isModal() and dlg.isVisible()
    assert dlg.units() == 3 and dlg.problem() == ""
    win._set_selection([a, b])
    assert "three or more" in dlg.problem()
    assert not dlg._buttons.button(QtWidgets.QDialogButtonBox.Apply).isEnabled()
    dlg.fixed.setChecked(True)
    dlg.gap.setValue(20)
    assert dlg.problem() == ""
    _apply(dlg)
    assert _box(doc, b)[0] == _box(doc, a)[2] + 20
    assert _box(doc, c)[0] == 400
    assert dlg.isVisible()
    dlg.reject()


def test_arrange_grid_window_remembers_its_values(win):
    doc = win.doc
    a, b, c, d = _rects(
        win, (100, 100, 140, 120), (300, 105, 320, 140), (120, 300, 200, 320), (500, 310, 520, 330)
    )
    win.act_arrange_grid.trigger()
    dlg = win._dialogs.get("arrange_grid")
    assert dlg is not None and not dlg.isModal() and dlg.isVisible()
    dlg.set_values(columns=4, hgap=3, vgap=0)
    dlg._ok()
    assert not dlg.isVisible()
    assert [_box(doc, i)[1] for i in (a, b, c, d)] == [100] * 4
    assert _box(doc, b)[0] == 100 + 40 + 3
    from ctgedit.ui.prefs.settings import app_settings

    assert int(app_settings().value("arrange/grid_columns")) == 4
    win.act_arrange_grid.trigger()
    dlg2 = win._dialogs.get("arrange_grid")
    assert dlg2.values()["columns"] == 4 and dlg2.values()["hgap"] == 3
    dlg2.reject()


def test_arrange_commands_need_two_objects(win):
    (a,) = _rects(win, (100, 100, 140, 120))
    win._set_selection([a])
    assert not win.act_space_equally.isEnabled() and not win.act_arrange_grid.isEnabled()
    (b,) = _rects(win, (200, 100, 240, 120))
    win._set_selection([a, b])
    assert win.act_space_equally.isEnabled() and win.act_arrange_grid.isEnabled()


# ------------------------------------------------------------------ the Measure tool
def test_measure_tool_shows_a_distance_and_draws_nothing(win):
    view = win.view
    doc = win.doc
    n = len(doc.page.objects)
    win.show()
    QtWidgets.QApplication.processEvents()
    view.set_zoom(1.0) if hasattr(view, "set_zoom") else None
    win.tool_actions["measure"].trigger()
    assert view.tool() == "measure"
    vp = view.viewport()
    # on the grid (Snap to Grid may be on: an 8 px grid keeps these points)
    p0 = view.mapFromScene(QtCore.QPointF(96, 96))
    p1 = view.mapFromScene(QtCore.QPointF(128, 136))
    QTest.mousePress(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p0)
    QTest.mouseMove(vp, p1)
    QTest.mouseRelease(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p1)
    m = view.measurement()
    assert m is not None
    x0, y0, x1, y1 = m
    assert abs((x1 - x0) - 32) <= 1 and abs((y1 - y0) - 40) <= 1
    assert "px" in view._pill and "°" in view._pill and "51 px" in view._pill
    # the measurement stays until the tool changes; nothing was drawn
    QtWidgets.QApplication.processEvents()
    assert view.measurement() is not None
    assert len(doc.page.objects) == n and not doc.can_undo()
    win.tool_actions["select"].trigger()
    assert view.measurement() is None and view._pill is None
