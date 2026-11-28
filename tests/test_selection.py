"""Selecting on a page covered by a template."""

import os

from PySide6 import QtCore, QtGui, QtWidgets


def _win(page):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(page)
    w.resize(1400, 1000)
    w.show()
    QtWidgets.QApplication.processEvents()
    return w


def _drop(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow.instances:
        MainWindow.instances.remove(w)


def _me(view, kind, vp, buttons=QtCore.Qt.NoButton):
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(
        kind, QtCore.QPointF(vp), QtCore.QPointF(g), QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier
    )


def test_marquee_selects_over_template_anchor(fx_page):
    w = _win(fx_page("FxLarge", "FxPerf"))
    try:
        view = w.view
        p0 = view.mapFromScene(QtCore.QPointF(5, 5))
        view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, p0, QtCore.Qt.LeftButton))
        assert view._marquee is not None  # background press -> marquee

        p1 = view.mapFromScene(QtCore.QPointF(1390, 845))
        view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, p1, QtCore.Qt.LeftButton))
        view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, p1))
        QtWidgets.QApplication.processEvents()

        roots = view.selection_roots()
        assert len(roots) > 2  # many real objects, not 0
        # align/distribute enablement follows the UNIT count
        w._on_canvas_selection(roots)
        assert w.act_dist_h.isEnabled()
        assert w.act_dist_v.isEnabled()
    finally:
        _drop(w)


def test_item_scene_shape_tracks_line_not_bbox():
    """The marquee/highlight selection shape follows a node item's true outline,
    so a diagonal line's empty bbox corner is NOT part of it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.editor.canvas import CanvasView

    view = CanvasView()
    line = QtWidgets.QGraphicsLineItem(0, 0, 100, 100)  # diagonal
    shape = view._item_scene_shape(line)

    def box(x, y, w, h):
        p = QtGui.QPainterPath()
        p.addRect(QtCore.QRectF(x, y, w, h))
        return p

    assert line.sceneBoundingRect().intersects(QtCore.QRectF(80, 0, 18, 18))
    assert not box(80, 0, 18, 18).intersects(shape)  # corner: misses line
    assert box(41, 41, 18, 18).intersects(shape)  # straddles the line


def test_marquee_touch_skips_line_bbox_corner():
    """End-to-end: a crossing marquee over a diagonal line's empty bbox corner
    selects nothing; one crossing the line selects it."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.editor.canvas import CanvasView

    view = CanvasView()
    view._marquee_mode = "touch"
    view._resolve_unit = lambda i: [i]
    scene = QtWidgets.QGraphicsScene()
    scene.setSceneRect(-20, -20, 260, 260)
    line = scene.addLine(0, 0, 200, 200)
    scene._items = {0: line}
    view.setScene(scene)
    view.resize(400, 400)
    view.show()
    app.processEvents()

    def drag(x0, y0, x1, y1):
        p0 = view.mapFromScene(QtCore.QPointF(x0, y0))
        p1 = view.mapFromScene(QtCore.QPointF(x1, y1))
        view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, p0, QtCore.Qt.LeftButton))
        view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, p1, QtCore.Qt.LeftButton))
        view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, p1))
        app.processEvents()

    drag(135, 80, 180, 125)  # top-right corner, off the diagonal
    assert view.selection_roots() == []
    drag(135, 80, 80, 135)  # box straddling the diagonal
    assert view.selection_roots() == [0]


def test_click_on_genie_still_selects(fx_page):
    w = _win(fx_page("FxLarge", "FxPerf"))
    try:
        view, pg = w.view, w.doc.page
        g = next(
            i
            for i, o in enumerate(pg.objects)
            if o.members and view.scene()._items.get(i) is not None and view._resolve_unit(i)
        )
        o = pg.objects[g]
        c = view.mapFromScene(
            QtCore.QPointF(o.geometry.x + o.geometry.extent_x / 2, o.geometry.y + o.geometry.extent_y / 2)
        )
        view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, c, QtCore.Qt.LeftButton))
        view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, c))
        QtWidgets.QApplication.processEvents()
        assert view.selection_roots()
        assert view._marquee is None
    finally:
        _drop(w)
