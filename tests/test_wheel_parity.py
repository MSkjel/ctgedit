"""The mouse wheel behaves the same on the editor and Simulate canvases."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

M = QtCore.Qt


def _wheel(view, dx, dy, mods=M.NoModifier):
    pos = QtCore.QPointF(view.viewport().rect().center())
    ev = QtGui.QWheelEvent(
        pos,
        QtCore.QPointF(view.viewport().mapToGlobal(pos.toPoint())),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(dx, dy),
        M.NoButton,
        mods,
        M.NoScrollPhase,
        False,
    )
    view.wheelEvent(ev)


def _effect(view, zoom, fn):
    """(zoom ratio, sign of the horizontal scroll, sign of the vertical scroll) of ``fn``."""
    z0 = zoom()
    h0, v0 = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
    fn()
    QtWidgets.QApplication.processEvents()
    dh = view.horizontalScrollBar().value() - h0
    dv = view.verticalScrollBar().value() - v0
    sign = lambda d: (d > 0) - (d < 0)  # noqa: E731
    return round(zoom() / z0, 4), sign(dh), sign(dv)


def _scene(w=6000, h=6000):
    """A page scene (each canvas paints its own kind of scene) of an empty w x h page."""
    from ctgedit.model.document import Page
    from ctgedit.render.scene import PageScene

    p = Page(name="wheel")
    p.appearance.width, p.appearance.height = w, h
    sc = PageScene()
    sc.set_page(p)
    sc.setSceneRect(0, 0, w, h)
    _KEEP.append(sc)  # (a view does not keep its scene alive)
    return sc


_KEEP = []


def _views():
    from ctgedit.ui.editor.canvas import CanvasView
    from ctgedit.ui.simulate.canvas import SimCanvas

    ed = CanvasView()
    ed.setScene(_scene())
    ed.apply_nav_options()
    sim = SimCanvas(_scene())
    for v in (ed, sim):
        v.resize(500, 400)
        v.show()
    return ed, sim


@pytest.mark.parametrize("scrolls", [False, True])
def test_the_wheel_does_the_same_in_the_editor_and_the_simulate_window(scrolls):
    _app()
    from ctgedit.ui.prefs.settings import app_settings, option

    app_settings().setValue("opt/wheel_scrolls", scrolls)
    ed, sim = _views()
    try:
        ed.WHEEL_SCROLLS = option("wheel_scrolls")  # (as Settings applies it)
        cases = [
            (0, -120, M.NoModifier),
            (0, 120, M.NoModifier),
            (0, -120, M.ControlModifier),
            (0, 120, M.ControlModifier),
            (0, -120, M.AltModifier),
            (0, -120, M.ShiftModifier),
            (-120, 0, M.NoModifier),
        ]
        seen = []
        for dx, dy, mods in cases:
            for v in (ed, sim):
                v.set_zoom(0.5)
                v.horizontalScrollBar().setValue(1500)
                v.verticalScrollBar().setValue(1500)
                QtWidgets.QApplication.processEvents()
            a = _effect(ed, ed.zoom, lambda: _wheel(ed, dx, dy, mods))
            b = _effect(sim, sim.zoom, lambda: _wheel(sim, dx, dy, mods))
            assert a == b, ((dx, dy, int(mods.value)), a, b)
            seen.append(a)
        # (what the setting means, on both: the plain wheel zooms, or it scrolls)
        plain = seen[0]
        if scrolls:
            assert plain[0] == 1.0 and plain[2] != 0
        else:
            assert plain[0] != 1.0
        assert seen[2][0] != 1.0  # Ctrl + the wheel zooms
        assert 1.0 < 1 / seen[4][0] < 1 / seen[2][0]  # Alt: finer than Ctrl
    finally:
        for v in (ed, sim):
            v.close()


def test_the_simulate_window_takes_the_zoom_step_and_the_stop_at_100():
    _app()
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    st.setValue("opt/wheel_zoom_step", 40)
    ed, sim = _views()
    try:
        sim.set_zoom(0.5)
        _wheel(sim, 0, 120)
        assert abs(sim.zoom() - 0.5 * 1.4) < 1e-6  # (the step: 40 %)
        sim.set_zoom(0.9)
        _wheel(sim, 0, 120)
        assert sim.zoom() == pytest.approx(1.0)  # (it stops at 100 % ...)
        _wheel(sim, 0, 120)
        assert sim.zoom() == pytest.approx(1.0)  # (... for one notch)
        _wheel(sim, 0, 120)
        assert sim.zoom() == pytest.approx(1.4)
    finally:
        for v in (ed, sim):
            v.close()


def test_a_popup_scales_by_the_step_and_a_window_inside_hands_the_wheel_to_its_parent():
    _app()
    from ctgedit.ui.simulate.canvas import SimCanvas
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/wheel_scrolls", True)
    parent = SimCanvas(_scene())
    child = SimCanvas(_scene(200, 200))
    popup = SimCanvas(_scene(200, 200))
    for v in (parent, child, popup):
        v.resize(400, 300)
        v.show()
    try:
        child.forward = parent
        child.set_fixed_view(QtCore.QRectF(0, 0, 200, 200), 1.0, 1.0)
        popup.set_fixed_view(QtCore.QRectF(0, 0, 200, 200), 1.0, 1.0)
        parent.set_zoom(0.5)
        parent.verticalScrollBar().setValue(1000)
        # the plain wheel scrolls the parent (the setting), Ctrl + the wheel zooms it
        _wheel(child, 0, -120)
        assert parent.verticalScrollBar().value() > 1000 and parent.zoom() == 0.5
        _wheel(child, 0, 120, M.ControlModifier)
        assert parent.zoom() > 0.5
        # a popup: the plain wheel does nothing, Ctrl + the wheel asks its window to scale it
        steps = []
        popup.zoomRequested.connect(steps.append)
        _wheel(popup, 0, -120)
        _wheel(popup, 0, 120, M.ControlModifier)
        assert steps == [1.0]
    finally:
        for v in (parent, child, popup):
            v.close()
