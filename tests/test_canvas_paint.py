"""Canvas painting stays cheap on big selections and hover."""

import pytest
from qtkit import app as _app

#: the page: FxMain's FxMany in the fake install (tools/make_fixtures.py), more than 50
#: selectable roots (valve and pump genies, groups, texts, plain shapes) spread over the
#: page; its genies resolve through FxMain's include chain (FxLib)
PAGE = "FxMany"


def _pump(n=4):
    from PySide6 import QtWidgets

    for _ in range(n):
        QtWidgets.QApplication.processEvents()


@pytest.fixture()
def win(fx_page):
    dst = fx_page(PAGE)
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1300, 850)
    w.show()
    w.open_document(str(dst))
    _pump()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def test_a_big_selection_is_measured_once_per_scene_build(win):
    view = win.view
    win.select_all()
    units = len(view._units)
    assert units > 32
    calls = []
    real = view._measure_unit
    view._measure_unit = lambda unit: (calls.append(tuple(unit)), real(unit))[1]
    try:
        view.viewport().repaint()
        first = len(calls)
        view.viewport().repaint()
        view.viewport().repaint()
        assert len(calls) == first  # the later paints measure nothing again
        # a new scene build measures again
        win.scene.build_serial += 1
        view.viewport().repaint()
        assert len(calls) == 2 * first
    finally:
        del view._measure_unit


def test_hover_repaints_where_the_outlines_are_and_shows_what_a_full_repaint_does(win):
    from PySide6 import QtCore, QtGui, QtWidgets

    view = win.view
    vp = view.viewport()
    view.set_zoom(2.0)
    _pump()
    rects = []
    real_update = vp.update

    def update(*a):
        rects.append(a[0] if a else None)
        return real_update(*a)

    vp.update = update
    scr = QtGui.QGuiApplication.primaryScreen()

    def shot():
        # (the canvas's area of the window)
        at = vp.mapTo(win, QtCore.QPoint(0, 0))
        return scr.grabWindow(win.winId(), at.x(), at.y(), vp.width(), vp.height()).toImage()

    def overlays():
        # the widgets laid over the canvas (scroll bars, navigation bar)
        kids = vp.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly)
        return [c.geometry() for c in kids if c.isVisible()]

    def move(p):
        ev = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(p),
            QtCore.QPointF(vp.mapToGlobal(p)),
            QtCore.Qt.NoButton,
            QtCore.Qt.NoButton,
            QtCore.Qt.NoModifier,
        )
        QtGui.QGuiApplication.sendEvent(vp, ev)

    try:
        pts = []
        for it in win.scene._items.values():
            c = view.mapFromScene(it.sceneBoundingRect().center())
            if vp.rect().contains(c):
                pts.append(c)
        assert len(pts) >= 3
        changes = 0
        for p in pts[:12]:
            before = list(view._hover_unit)
            rects.clear()
            move(p)
            if view._hover_unit == before:
                continue
            changes += 1
            # the canvas asks for the outlines' box, not the whole canvas
            boxes = [r for r in rects if isinstance(r, QtCore.QRect)]
            assert boxes and all(b.width() < vp.width() or b.height() < vp.height() for b in boxes)
            _pump()
            covered = overlays()
            a = shot()
            real_update()
            _pump()
            b = shot()
            covered += overlays()
            # (a picture drawn smoothly scaled may differ by a level at a repainted edge)
            assert a.size() == b.size()
            diff = 0
            for y in range(0, a.height(), 2):
                for x in range(0, a.width(), 2):
                    if any(r.contains(x, y) for r in covered):
                        continue
                    pa, pb = a.pixel(x, y), b.pixel(x, y)
                    if pa != pb and max(abs(((pa >> s) & 255) - ((pb >> s) & 255)) for s in (0, 8, 16)) > 2:
                        diff += 1
            assert diff == 0
        assert changes >= 2
    finally:
        del vp.update


def test_a_marquee_selects_what_testing_every_item_selects(win):
    """The rubber band tests only the items the scene's index says reach it: over random
    bands, enclosing and crossing, the units and their order are the ones testing every
    item gives."""
    import random
    from PySide6 import QtCore, QtGui

    view = win.view
    sc = win.scene
    rnd = random.Random(3)
    page = sc.page_rect()

    def band(rect, touch, every):
        view._set_units([], emit=False)
        view._marquee, view._marquee_touch = rect, touch
        if every:
            # (the index answers every item: the walk tests each one, as it did)
            all_items = list(sc._items.values())
            sc.items = lambda *a, **k: all_items
        try:
            p = QtCore.QPointF(10, 10)
            view.mouseReleaseEvent(
                QtGui.QMouseEvent(
                    QtCore.QEvent.MouseButtonRelease,
                    p,
                    p,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.NoButton,
                    QtCore.Qt.NoModifier,
                )
            )
            return [list(u) for u in view._units]
        finally:
            if every:
                del sc.items

    picked = 0
    for _ in range(24):
        x, y = rnd.uniform(0, page.width()), rnd.uniform(0, page.height())
        rect = QtCore.QRectF(x, y, rnd.uniform(20, page.width() / 2), rnd.uniform(20, page.height() / 2))
        touch = rnd.random() < 0.5
        got = band(rect, touch, False)
        assert got == band(rect, touch, True)
        picked += bool(got)
    assert picked >= 6
