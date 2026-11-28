"""Canvas hit tests."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the U-bend: down the left, along the bottom, up the right; the top stays open, so its
#: implicitly closed hull is the square 600..800 x 400..560
BEND = [(600, 400), (600, 560), (800, 560), (800, 400)]
#: the filled tank inside the bend
TANK = (660, 440, 740, 500)
#: a point of the tank, and a point of the empty page inside the hull (30 px from the
#: nearest stroke)
ON_TANK = (700, 470)
EMPTY_IN_HULL = (700, 420)


def _pump(n=3):
    for _ in range(n):
        _app().processEvents()


@pytest.fixture()
def mixer_win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.open_document(fx_page("FxShapes"))
    _pump()
    w.view.reset_zoom()
    _pump()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    w.deleteLater()
    _pump()


def _hits(win, x, y):
    v = win.view
    v.centerOn(QtCore.QPointF(x, y))
    _pump()
    return v._hits_under(v.mapFromScene(QtCore.QPointF(x, y)))


def _bend_over_tank(win):
    """The tank (a filled rectangle) and, over it, the U-bend pipe: (tank, pipe)."""
    doc = win.doc
    tank = doc.create_object("rectangle", rect=TANK, colour=0x3366CC)
    doc.set_filled(tank, True)
    pipe = doc.create_object("pipe", nodes=BEND, colour=0x808080)
    win._refresh()
    _pump()
    return tank, pipe


def test_inside_the_bend_of_a_pipe_hits_the_tank_not_the_pipe(mixer_win):
    tank, pipe = _bend_over_tank(mixer_win)
    page = mixer_win.doc.page
    assert page.objects[pipe].kind.name == "PIPE"
    assert page.objects[tank].style.filled
    hits = _hits(mixer_win, *ON_TANK)
    assert tank in hits, hits
    assert pipe not in hits, hits
    assert not [i for i in _hits(mixer_win, *EMPTY_IN_HULL) if page.objects[i].kind.name == "PIPE"]


def test_the_pipe_itself_is_still_hit_on_its_stroke(mixer_win):
    _tank, pipe = _bend_over_tank(mixer_win)
    page = mixer_win.doc.page
    nodes = page.objects[pipe].geometry.nodes
    assert nodes, "the new pipe has no nodes"
    (x0, y0), (x1, y1) = nodes[0], nodes[1]
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    assert pipe in _hits(mixer_win, mx, my)


def test_paint_only_children_pick_nothing():
    _app()
    from ctgedit.render.scene import PaintOnlyPathItem, StrokePathItem

    path = QtGui.QPainterPath(QtCore.QPointF(0, 0))
    path.lineTo(0, 100)
    path.lineTo(100, 100)
    path.lineTo(100, 0)  # a U: its closed hull is the square
    tube = StrokePathItem(path)
    tube.setPen(QtGui.QPen(QtCore.Qt.transparent, 6))
    s = PaintOnlyPathItem(path, tube)
    s.setPen(QtGui.QPen(QtGui.QColor(0, 0, 255), 6))
    sc = QtWidgets.QGraphicsScene()
    sc.addItem(tube)
    inside = sc.items(QtCore.QPointF(50, 50))
    assert tube not in inside and s not in inside
    on = sc.items(QtCore.QPointF(0, 50))
    assert tube in on and s not in on
    # (it still paints: its bounds are the stroke's)
    assert s.boundingRect().contains(QtCore.QPointF(50, 99))


def test_effect_copies_hit_test_as_their_original():
    _app()
    from ctgedit.render.scene import HollowPolygonItem, PageScene, StrokePathItem

    path = QtGui.QPainterPath(QtCore.QPointF(0, 0))
    path.lineTo(0, 100)
    path.lineTo(100, 100)
    line = StrokePathItem(path)
    copy = PageScene._clone_shape(line, line)
    assert isinstance(copy, StrokePathItem)
    poly = HollowPolygonItem(
        QtGui.QPolygonF([QtCore.QPointF(0, 0), QtCore.QPointF(50, 0), QtCore.QPointF(50, 50)])
    )
    assert isinstance(PageScene._clone_shape(poly, poly), HollowPolygonItem)
    plain = QtWidgets.QGraphicsPathItem(path)
    assert type(PageScene._clone_shape(plain, plain)) is QtWidgets.QGraphicsPathItem


def test_a_hollow_arc_is_hit_on_its_stroke_not_inside_its_chord(mixer_win):
    from ctgedit.render.scene import StrokePathItem

    w = mixer_win
    doc = w.doc
    i = doc.create_object("ellipse", rect=(600, 560, 760, 720))
    # an arc over the top: from 9 o'clock clockwise to 3 o'clock (CtDraw's angles)
    assert doc.set_ellipse(i, style=2, start=270, end=90)
    doc.set_filled(i, False)
    w._refresh()
    _pump()
    item = w.scene._items[i]
    assert isinstance(item, StrokePathItem)
    assert item.brush().style() == QtCore.Qt.NoBrush
    assert i not in _hits(w, 680, 600)  # inside the chord: empty page
    assert i in _hits(w, 680, 560)  # on the arc at 12 o'clock
    # a FILLED arc still picks by its chord area
    doc.set_filled(i, True)
    w._refresh()
    _pump()
    assert i in _hits(w, 680, 600)
