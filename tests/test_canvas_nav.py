"""Canvas navigation."""

import math
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402
from qtkit import app as _app

# the pages of the fake install: SMALL is FxMain's FxShapes (1024 x 768,
#: 22 top-level shapes, texts, a group and bitmaps, none from a template), BIG is FxMain's
#: FxBig (1920 x 1080, 124 top-level objects in rows: at zoom 4 the navigator shows)
SMALL = "FxShapes"
BIG = "FxBig"

LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier
CTRL = QtCore.Qt.ControlModifier


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _me(view, kind, vp, buttons=QtCore.Qt.NoButton, mods=NOMOD, button=LEFT):
    vp = QtCore.QPoint(*vp) if isinstance(vp, tuple) else vp
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), button, buttons, mods)


def _at(view, sp):
    return view.mapFromScene(QtCore.QPointF(*sp))


def _press(view, vp, mods=NOMOD, button=LEFT):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, button, mods, button))


def _move(view, vp, mods=NOMOD, buttons=LEFT):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, vp, buttons, mods))


def _release(view, vp, mods=NOMOD, button=LEFT):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, QtCore.Qt.NoButton, mods, button))


def _wheel(view, angle=(0, 0), pixel=(0, 0), mods=NOMOD, phase=QtCore.Qt.NoScrollPhase, at=None):
    vp = view.viewport()
    pos = QtCore.QPointF(at if at is not None else QtCore.QPoint(vp.width() // 2, vp.height() // 2))
    e = QtGui.QWheelEvent(
        pos,
        QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
        QtCore.QPoint(*pixel),
        QtCore.QPoint(*angle),
        QtCore.Qt.NoButton,
        mods,
        phase,
        False,
    )
    view.wheelEvent(e)


def _open(tmp_path, src, w=1300, h=900):
    from ctgedit.ui.main_window import MainWindow

    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    win = MainWindow()
    win.open_document(str(dst))
    win.resize(w, h)
    win.show()
    _pump()
    win.view.fit()
    _pump()
    return win


def _close(win):
    from ctgedit.ui.main_window import MainWindow

    for e in getattr(win, "_docs", []):
        e["doc"].dirty = False
    if win in MainWindow.instances:
        MainWindow.instances.remove(win)
    win.hide()


@pytest.fixture()
def win(tmp_path, fx_page):
    """The main window on a copy of the small page (FxShapes)."""
    _app()
    w = _open(tmp_path, fx_page(SMALL))
    yield w
    _close(w)


@pytest.fixture()
def mixing(tmp_path, fx_page):
    """The main window on a copy of the big page (FxBig, 1920 x 1080)."""
    _app()
    w = _open(tmp_path, fx_page(BIG))
    yield w
    _close(w)


def _scroll(view):
    return view.horizontalScrollBar().value(), view.verticalScrollBar().value()


# ------------------------------------------------------------------ the wheel
def test_a_sideways_turn_scrolls_and_does_not_zoom(win):
    v = win.view
    v.set_zoom(1.0)
    z, (h, _y) = v.zoom(), _scroll(v)
    _wheel(v, angle=(-120, 0))
    assert v.zoom() == z
    assert v.horizontalScrollBar().value() != h


def test_an_event_with_no_delta_changes_nothing(win):
    v = win.view
    z, s = v.zoom(), _scroll(v)
    _wheel(v, phase=QtCore.Qt.ScrollEnd)
    _wheel(v)
    assert v.zoom() == z and _scroll(v) == s


def test_touchpad_events_pan_instead_of_zooming(win):
    v = win.view
    v.TOUCHPAD_PANS = True  # (Settings > Canvas's touchpad choice: off by default)
    v.set_zoom(1.0)
    z, (h, y) = v.zoom(), _scroll(v)
    for _ in range(10):
        _wheel(v, angle=(0, 8), pixel=(0, 8), phase=QtCore.Qt.ScrollUpdate)
    assert v.zoom() == z
    assert v.verticalScrollBar().value() == y - 80
    for _ in range(5):
        _wheel(v, angle=(8, 0), pixel=(6, 0), phase=QtCore.Qt.ScrollUpdate)
    assert v.horizontalScrollBar().value() == h - 30


def test_ctrl_wheel_zooms_by_the_size_of_the_turn(win):
    v = win.view
    v.set_zoom(0.5)
    _wheel(v, angle=(0, 8), mods=CTRL)
    step = 1 + v.WHEEL_ZOOM_STEP / 100.0
    assert v.zoom() == pytest.approx(0.5 * step ** (8 / 120.0), rel=1e-6)
    # a pinch on a touchpad arrives as Ctrl + the wheel: it zooms too
    z = v.zoom()
    _wheel(v, angle=(0, -16), pixel=(0, -4), mods=CTRL, phase=QtCore.Qt.ScrollUpdate)
    assert v.zoom() == pytest.approx(z * step ** (-16 / 120.0), rel=1e-6)


def test_a_wheel_notch_zooms_by_the_step_and_alt_zooms_finely(win):
    v = win.view
    v.set_zoom(2.0)
    _wheel(v, angle=(0, 120))
    assert v.zoom() == pytest.approx(2.0 * 1.15, rel=1e-6)
    v.set_zoom(2.0)
    # (Qt reports Alt + the wheel as a sideways turn)
    _wheel(v, angle=(120, 0), mods=QtCore.Qt.AltModifier)
    assert v.zoom() == pytest.approx(2.0 * 1.15**0.25, rel=1e-6)


def test_shift_wheel_scrolls_sideways(win):
    v = win.view
    v.set_zoom(1.0)
    z, (h, y) = v.zoom(), _scroll(v)
    _wheel(v, angle=(0, -120), mods=QtCore.Qt.ShiftModifier)
    assert v.zoom() == z and v.horizontalScrollBar().value() > h
    assert v.verticalScrollBar().value() == y


def test_the_wheel_zoom_stops_at_100_for_one_notch(win):
    v = win.view
    v.set_zoom(0.9)
    _wheel(v, angle=(0, 120))
    assert v.zoom() == pytest.approx(1.0)
    _wheel(v, angle=(0, 120))
    assert v.zoom() == pytest.approx(1.0)  # the notch it holds
    _wheel(v, angle=(0, 120))
    assert v.zoom() == pytest.approx(1.15)
    v.WHEEL_STOP_AT_100 = False
    v.set_zoom(0.9)
    _wheel(v, angle=(0, 120))
    assert v.zoom() == pytest.approx(0.9 * 1.15)


def test_wheel_scrolls_setting_still_scrolls(win):
    v = win.view
    v.WHEEL_SCROLLS = True
    try:
        v.set_zoom(1.0)
        z, (_h, y) = v.zoom(), _scroll(v)
        _wheel(v, angle=(0, -120))
        assert v.zoom() == z and v.verticalScrollBar().value() > y
        _wheel(v, angle=(0, 120), mods=CTRL)
        assert v.zoom() > z
    finally:
        v.WHEEL_SCROLLS = False


# ------------------------------------------------------------------ the zoom levels
def test_the_zoom_ladder():
    from ctgedit.ui.editor.viewnav import ZOOM_LEVELS, next_zoom_level

    assert next_zoom_level(0.55, 1) == pytest.approx(2 / 3)
    assert next_zoom_level(2 / 3, 1) == 1.0
    assert next_zoom_level(1.0, -1) == pytest.approx(2 / 3)
    assert next_zoom_level(64.0, 1) == 64.0 and next_zoom_level(0.02, -1) == 0.02
    assert ZOOM_LEVELS[0] == 0.02 and ZOOM_LEVELS[-1] == 64.0


def test_zoom_in_from_55_goes_to_66_then_100(win):
    v = win.view
    v.set_zoom(0.55)
    win.act_zoom_in.trigger()
    assert v.zoom() == pytest.approx(2 / 3)
    win.act_zoom_in.trigger()
    assert v.zoom() == pytest.approx(1.0)
    win.act_zoom_out.trigger()
    assert v.zoom() == pytest.approx(2 / 3)
    v.ZOOM_STEPS = "factor"
    try:
        v.set_zoom(1.0)
        win.act_zoom_in.trigger()
        assert v.zoom() == pytest.approx(1.25)
    finally:
        v.ZOOM_STEPS = "levels"


# ------------------------------------------------------------------ the glide
def test_with_no_animation_the_zoom_lands_at_once(win):
    assert os.environ.get("CTG_NO_ANIMATION")
    v = win.view
    v.set_zoom(3.0)
    v.fit_page()
    assert not v.view_animating()
    z_fit = v.zoom()
    v.set_zoom(3.0)
    assert v.zoom() == pytest.approx(3.0)
    v.fit()
    assert v.zoom() == pytest.approx(z_fit)


def test_a_zoom_change_glides_and_ends_exactly_on_the_target(win, monkeypatch):
    monkeypatch.delenv("CTG_NO_ANIMATION", raising=False)
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.setattr(VN, "windows_animations_on", lambda: True)
    # a long glide: under a loaded suite the first frame can come after a 160 ms glide has
    # ended, and the glide then shows only its end
    monkeypatch.setattr(VN, "GLIDE_MS", 1500)
    v = win.view
    v.ANIMATE_ZOOM = True
    v.set_zoom(1.0)  # (the start, gliding)
    v.finish_view_animation()
    start = v.zoom()
    v.set_zoom(4.0)
    assert v.view_animating()
    assert v.zoom() == pytest.approx(start)  # put back: the glide starts there
    zooms = []
    v.zoomChanged.connect(zooms.append)
    # (wait for its end: under a loaded suite its frames come late)
    for _ in range(400):
        QtTest.QTest.qWait(20)
        if not v.view_animating():
            break
    assert not v.view_animating()
    assert v.zoom() == pytest.approx(4.0)
    assert len(zooms) >= 2 and all(start - 1e-6 <= z <= 4.0 + 1e-6 for z in zooms)
    # a new command finishes a running glide at once
    v.set_zoom(1.0)
    assert v.view_animating()
    v.set_zoom(2.0)
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(2.0)
    # no glide when the setting is off
    v.ANIMATE_ZOOM = False
    v.set_zoom(3.0)
    assert not v.view_animating() and v.zoom() == pytest.approx(3.0)


def test_windows_animations_reads_as_on_or_off():
    from ctgedit.ui.editor.viewnav import windows_animations_on

    assert windows_animations_on() in (True, False)


# ------------------------------------------------------------------ Previous / Next View
def test_previous_and_next_view(win):
    v = win.view
    doc = win.doc
    win.act_zoom_fit.trigger()
    fitted = (v.zoom(), v.view_centre())
    i = next(
        k for k, o in enumerate(doc.page.objects) if o.parent is None and not o.raw.get("_from_template")
    )
    win._set_selection([i])
    win.act_zoom_sel.trigger()
    assert v.zoom() != pytest.approx(fitted[0])
    win._update_actions()
    assert win.act_prev_view.isEnabled() and not win.act_next_view.isEnabled()
    assert win.act_prev_view.shortcut().toString() == "Alt+Left"
    assert win.act_prev_view.shortcutContext() == QtCore.Qt.WidgetWithChildrenShortcut
    win.act_prev_view.trigger()
    assert v.zoom() == pytest.approx(fitted[0])
    c = v.view_centre()
    assert abs(c.x() - fitted[1].x()) < 2 and abs(c.y() - fitted[1].y()) < 2
    assert win.act_next_view.isEnabled()
    win.act_next_view.trigger()
    assert v.zoom() != pytest.approx(fitted[0])
    # the lists are the document's
    assert doc.ui.view_history and not doc.ui.view_future
    # the mouse's back button
    _press(v, (10, 10), button=QtCore.Qt.BackButton)
    assert v.zoom() == pytest.approx(fitted[0])


def test_a_wheel_burst_is_one_history_step(win):
    v = win.view
    past = win.doc.ui.view_history
    past.clear()
    for _ in range(5):
        _wheel(v, angle=(0, 120))
    assert len(past) == 1
    v._burst_at -= 1.0  # (600 ms of quiet later)
    _wheel(v, angle=(0, 120))
    assert len(past) == 2


def test_the_history_keeps_at_most_50():
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    v.setScene(QtWidgets.QGraphicsScene(-1000, -1000, 2000, 2000))
    v.resize(300, 200)
    for k in range(70):
        v.set_zoom(0.5 + k * 0.01)
    assert len(v._history()[0]) == 50


# ------------------------------------------------------------------ saved views
def test_a_saved_view_comes_back_after_reopening_the_page(tmp_path, fx_page):
    _app()
    from ctgedit.ui.editor import saved_views as SV

    w = _open(tmp_path, fx_page(SMALL))
    try:
        v = w.view
        v.set_zoom(2.0)
        v.centerOn(200, 150)
        SV.store_views(w.doc, [SV.current_view(v, "Tank area")])
        path = w.doc.path
    finally:
        _close(w)
    from ctgedit.ui.main_window import MainWindow

    w2 = MainWindow()
    try:
        w2.open_document(path)
        w2.resize(1300, 900)
        w2.show()
        _pump()
        w2._update_actions()
        names = [a.text() for a in w2.saved_views_menu.actions()]
        assert "Tank area" in names
        a = next(a for a in w2.saved_views_menu.actions() if a.text() == "Tank area")
        a.trigger()
        assert w2.view.zoom() == pytest.approx(2.0)
        c = w2.view.view_centre()
        assert abs(c.x() - 200) < 2 and abs(c.y() - 150) < 2
        assert w2.view.can_go_back()  # (Previous View comes back)
        # the command palette finds it: View > Saved Views > Tank area
        from ctgedit.ui.dialog.palette import harvest_actions

        crumbs = [(c, t) for _a, c, t in harvest_actions(w2.menuBar())]
        assert ("View > Saved Views", "Tank area") in crumbs
    finally:
        _close(w2)


def test_manage_saved_views(win):
    from ctgedit.ui.editor import saved_views as SV

    win._manage_saved_views()
    dlg = win._dialogs.get("saved_views")
    assert dlg is not None and dlg.isVisible()
    try:
        win.view.set_zoom(1.5)
        dlg._add()
        win.view.set_zoom(3.0)
        dlg._add()
        assert [v["name"] for v in SV.load_views(win.doc)] == ["View 1", "View 2"]
        dlg.list.setCurrentRow(1)
        dlg._move(-1)
        assert [v["name"] for v in SV.load_views(win.doc)] == ["View 2", "View 1"]
        dlg.list.item(0).setText("Header")
        assert [v["name"] for v in SV.load_views(win.doc)] == ["Header", "View 1"]
        dlg.list.setCurrentRow(1)
        dlg._go()
        assert win.view.zoom() == pytest.approx(1.5)
        dlg.list.setCurrentRow(0)
        dlg._remove()
        assert [v["name"] for v in SV.load_views(win.doc)] == ["View 1"]
        assert [a.text() for a in win.saved_views_menu.actions() if a.text()][1] == "View 1"
    finally:
        dlg.close()


def test_save_current_view_asks_a_name(win):
    from ctgedit.ui.editor import saved_views as SV

    win._save_view_dialog()
    pop = win._save_view_popover
    assert pop.field.text() == "View 1"
    pop.field.setText("Legend")
    pop._save()
    assert [v["name"] for v in SV.load_views(win.doc)] == ["Legend"]


# ------------------------------------------------------------------ the zoom box
def test_a_zoom_box_fills_the_view(win):
    v = win.view
    v.set_zoom(1.0)
    win._set_tool("zoom")
    assert win.tool_actions["zoom"].isChecked()
    vp = v.viewport()
    a = QtCore.QPoint(vp.width() // 2 - 50, vp.height() // 2 - 50)
    b = a + QtCore.QPoint(100, 100)
    box = v.mapToScene(QtCore.QRect(a, b)).boundingRect()
    _press(v, a)
    _move(v, a + QtCore.QPoint(50, 50))
    assert v.zoom_box_rect() is not None
    _move(v, b)
    _release(v, b)
    expect = min((vp.width() - 4) / box.width(), (vp.height() - 4) / box.height())
    assert v.zoom() == pytest.approx(expect, rel=0.02)
    c = v.view_centre()
    assert abs(c.x() - box.center().x()) < 2 and abs(c.y() - box.center().y()) < 2
    # a click zooms in 2 times about the point, Alt + click out
    z = v.zoom()
    _press(v, (200, 150))
    _release(v, (200, 150))
    assert v.zoom() == pytest.approx(min(v.ZOOM_MAX, z * 2))
    z = v.zoom()
    _press(v, (200, 150), mods=QtCore.Qt.AltModifier)
    _release(v, (200, 150), mods=QtCore.Qt.AltModifier)
    assert v.zoom() == pytest.approx(z / 2)
    # Esc goes back to Select
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, NOMOD))
    assert v.tool() == "select"


def test_ctrl_shift_drag_on_the_empty_page_is_a_zoom_box(win):
    v = win.view
    v.set_zoom(1.0)
    v._set_units([])
    mods = CTRL | QtCore.Qt.ShiftModifier
    # an empty spot: below the page, in the surround
    page = v.scene().page_rect()
    v.centerOn(page.center().x(), page.bottom() + 200)
    a = v.mapFromScene(QtCore.QPointF(page.center().x(), page.bottom() + 150))
    assert not [h for h in v._hits_under(a) if v._resolve_unit(h)]
    _press(v, a, mods)
    assert v.zoom_box_rect() is not None and v._marquee is None
    _move(v, a + QtCore.QPoint(80, 60), mods)
    _release(v, a + QtCore.QPoint(80, 60), mods)
    assert v.zoom() > 1.5


def test_holding_z_arms_the_zoom_box_for_a_moment(win):
    v = win.view
    win._set_tool("rectangle")
    ev = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_Z, NOMOD, "z")
    v.event(ev)
    assert ev.isAccepted()
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "zoom" and win.tool_actions["zoom"].isChecked()
    v._zhold["t"] -= 1.0  # (held a second)
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "rectangle"
    # a short tap arms it
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "zoom"
    win._set_tool("select")


def test_a_middle_double_click_fits_all_objects(win):
    v = win.view
    v.set_zoom(5.0)
    e = _me(
        v,
        QtCore.QEvent.MouseButtonDblClick,
        (100, 100),
        QtCore.Qt.MiddleButton,
        NOMOD,
        QtCore.Qt.MiddleButton,
    )
    v.mouseDoubleClickEvent(e)
    zoom_all = v.zoom()
    v.set_zoom(5.0)
    win.act_zoom_all.trigger()
    assert v.zoom() == pytest.approx(zoom_all)
    v.MIDDLE_DOUBLE_CLICK = "nothing"
    v.set_zoom(5.0)
    v.mouseDoubleClickEvent(e)
    assert v.zoom() == pytest.approx(5.0)
    v.MIDDLE_DOUBLE_CLICK = "fit_all"


# ------------------------------------------------------------------ Hand and Space
def test_the_hand_tool_pans(win):
    v = win.view
    v.set_zoom(2.0)
    win._set_tool("pan")
    assert v.viewport().cursor().shape() == QtCore.Qt.OpenHandCursor
    s = _scroll(v)
    _press(v, (300, 300))
    _move(v, (250, 260))
    _release(v, (250, 260))
    assert _scroll(v) == (s[0] + 50, s[1] + 40)
    win._set_tool("select")


def test_space_pans_with_the_keyboard_in_a_panel(win, monkeypatch):
    """Space with the keyboard in a panel that does not type pans the canvas under the
    pointer; the Objects panel's tree keeps Space (it hides the selection there) and a text
    field keeps it too."""
    from ctgedit.ui.editor.viewnav import SpacePanForwarder

    v = win.view
    v.set_zoom(2.0)
    monkeypatch.setattr(SpacePanForwarder, "_canvas_under_pointer", staticmethod(lambda: v))
    objects = win.objects_dock.findChild(QtWidgets.QTreeWidget)
    objects.setFocus()
    _pump()
    if QtWidgets.QApplication.focusWidget() is not objects:
        pytest.skip("the offscreen window does not take the focus")
    hidden = []
    win.objects_dock.hideSelectionRequested.connect(lambda: hidden.append(1))
    press = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space, NOMOD, " ")
    QtWidgets.QApplication.sendEvent(objects, press)
    assert not v._space_pan and hidden == [1]  # (the panel's own key, pointer or not)
    tree = win.library_dock._tree
    tree.setFocus()
    _pump()
    if QtWidgets.QApplication.focusWidget() is not tree:
        pytest.skip("the offscreen window does not take the focus")
    press = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space, NOMOD, " ")
    QtWidgets.QApplication.sendEvent(tree, press)
    assert v._space_pan
    s = _scroll(v)
    _press(v, (300, 300))
    _move(v, (260, 280))
    _release(v, (260, 280))
    assert _scroll(v) == (s[0] + 40, s[1] + 20)
    QtWidgets.QApplication.sendEvent(
        tree, QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Space, NOMOD, " ")
    )
    assert not v._space_pan
    # a text field keeps Space
    field = win.navbar.zoom_field
    field.setFocus()
    _pump()
    if QtWidgets.QApplication.focusWidget() is field:
        QtWidgets.QApplication.sendEvent(
            field, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Space, NOMOD, " ")
        )
        assert not v._space_pan


# ------------------------------------------------------------------ the edge scroll
def test_a_move_held_at_the_edge_scrolls_and_moves_further(win):
    v = win.view
    v.set_snap(False)
    v.set_zoom(1.0)
    vp = v.viewport()
    c = v.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))
    i = win.doc.create_object(
        "rectangle", (int(c.x()) - 20, int(c.y()) - 15, int(c.x()) + 20, int(c.y()) + 15), None
    )
    win._refresh()
    _pump()
    undo = len(win.doc._undo)
    x0 = win.doc.selection_extent([i])[0]
    start = v.mapFromScene(c)
    _press(v, start)
    edge = QtCore.QPoint(vp.width() - 5, start.y())
    for k in range(1, 6):
        _move(v, start + (edge - start) * (k / 5.0))
    h = v.horizontalScrollBar().value()
    QtTest.QTest.qWait(200)
    assert v.horizontalScrollBar().value() > h + 20  # it scrolled
    _release(v, edge)
    _pump()
    moved = win.doc.selection_extent([i])[0] - x0
    assert moved > (edge.x() - start.x()) + 20  # further than the pointer went
    assert len(win.doc._undo) == undo + 1  # one undo step


def test_no_edge_scroll_without_a_drag_or_with_the_setting_off(win):
    v = win.view
    v.set_zoom(1.0)
    vp = v.viewport()
    _move(v, (vp.width() - 5, vp.height() // 2), buttons=QtCore.Qt.NoButton)
    assert not v._edge_timer.isActive()
    v.DRAG_AUTOSCROLL = False
    try:
        _press(v, (vp.width() // 2, vp.height() // 2))
        _move(v, (vp.width() - 5, vp.height() // 2))
        assert not v._edge_timer.isActive()
        _release(v, (vp.width() - 5, vp.height() // 2))
    finally:
        v.DRAG_AUTOSCROLL = True


# ------------------------------------------------------------------ the rulers
def test_a_drag_from_the_top_ruler_adds_a_horizontal_guideline(win):
    v = win.view
    v.set_zoom(1.0)
    v.set_guidelines([], [])
    r = win.rulers
    assert r.is_shown() and v.viewportMargins().top() == r.size()
    top = r.top
    p0 = QtCore.QPoint(top.width() // 2, top.height() // 2)
    QtTest.QTest.mousePress(top, LEFT, NOMOD, p0)
    target = v.mapFromScene(QtCore.QPointF(300, 203))
    glob = v.viewport().mapToGlobal(target)
    to_ruler = top.mapFromGlobal(glob)
    for k in (0.3, 0.6, 1.0):
        QtTest.QTest.mouseMove(top, p0 + (to_ruler - p0) * k)
    assert v._guide_drag is not None and v._guide_drag["moved"]
    assert "y = 200" in (v._pill or "")
    msgs = []
    v.guidelinesChanged.connect(msgs.append)
    QtTest.QTest.mouseRelease(top, LEFT, NOMOD, to_ruler)
    assert v.guidelines() == ([], [200])  # snapped to the 8 px grid
    assert msgs == ["Guideline added at y = 200"]
    # let go over the ruler: none added
    QtTest.QTest.mousePress(r.left, LEFT, NOMOD, QtCore.QPoint(8, 100))
    QtTest.QTest.mouseMove(r.left, QtCore.QPoint(9, 160))
    QtTest.QTest.mouseRelease(r.left, LEFT, NOMOD, QtCore.QPoint(9, 160))
    assert v.guidelines() == ([], [200])
    # it is stored for the page (editor state)
    assert win._load_guidelines(win.doc) == ([], [200])


def test_ctrl_r_hides_the_rulers(win):
    v = win.view
    assert win.act_rulers.shortcut().toString() == "Ctrl+R"
    win.act_rulers.trigger()
    assert not win.act_rulers.isChecked()
    m = v.viewportMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0)
    assert not win.rulers.top.isVisible()
    from ctgedit.ui.prefs.settings import app_settings

    assert app_settings().value("view/rulers", True, type=bool) is False
    win.act_rulers.trigger()
    assert v.viewportMargins().left() == win.rulers.size()


def test_ruler_ticks_follow_the_zoom():
    from ctgedit.ui.editor.rulers import tick_steps

    assert tick_steps(1.0) == (50, 10)
    assert tick_steps(4.0) == (20, 2)
    assert tick_steps(0.1) == (500, 100)
    major, _minor = tick_steps(0.02)
    assert major * 0.02 >= 50


# ------------------------------------------------------------------ the navigator
def test_the_navigator_shows_zoomed_in_and_its_frame_pans(mixing):
    v = mixing.view
    nav = mixing.navigator
    v.fit()
    _pump()
    nav.refresh()
    assert not nav.isVisible()  # the whole page shows
    v.set_zoom(4.0)
    _pump()
    assert nav.isVisible() and nav.objectName() == "Navigator"
    QtTest.QTest.qWait(50)
    _pump()
    m = nav.map
    frame = m.visible_frame()
    assert not frame.isNull()
    s = _scroll(v)
    start = frame.center().toPoint()
    QtTest.QTest.mousePress(m, LEFT, NOMOD, start)
    QtTest.QTest.mouseMove(m, start + QtCore.QPoint(30, 12))
    QtTest.QTest.mouseRelease(m, LEFT, NOMOD, start + QtCore.QPoint(30, 12))
    s2 = _scroll(v)
    assert s2[0] > s[0] and s2[1] > s[1]
    # the map was rendered (the page shows)
    assert m._src._pixmap is not None
    # the collapse button remembers
    nav.toggle_collapsed()
    from ctgedit.ui.prefs.settings import app_settings

    assert app_settings().value("view/navigator_collapsed", False, type=bool) is True
    assert nav.isVisible() and not m.isVisible()
    nav.toggle_collapsed()
    # View > Navigator off
    mixing.act_navigator.trigger()
    assert not nav.isVisible()
    mixing.act_navigator.trigger()


# ------------------------------------------------------------------ the cross hairs
def test_the_cross_hairs_cross_at_the_snapped_point_with_its_position(win):
    v = win.view
    v.set_zoom(2.0)
    v.centerOn(QtCore.QPointF(200, 150))  # (FxShapes is wider than the view at 200 %)
    _pump()
    v.set_snap(True)
    win.act_crosshair.setChecked(True)
    win._toggle_crosshair()
    try:
        _move(v, _at(v, (203, 101)), buttons=QtCore.Qt.NoButton)
        p = v.cross_hair_point()
        assert (p.x(), p.y()) == (200.0, 104.0)
        img = v.viewport().grab().toImage()
        line = _at(v, (200, 104))
        from ctgedit.ui.style.theme import color

        acc = color("accent")
        c = QtGui.QColor(img.pixel(line.x(), 40))
        assert abs(c.blue() - acc.blue()) < 50 and abs(c.red() - acc.red()) < 50
        v.CROSSHAIR_STYLE = "short"
        v.viewport().update()
        img = v.viewport().grab().toImage()
        far = QtGui.QColor(img.pixel(line.x(), max(2, line.y() - 120)))
        assert not (abs(far.blue() - acc.blue()) < 30 and abs(far.red() - acc.red()) < 30)
    finally:
        v.CROSSHAIR_STYLE = "full"
        win.act_crosshair.setChecked(False)
        win._toggle_crosshair()


# ------------------------------------------------------------------ the 'Page' chip
def test_the_page_chip_shows_far_from_the_page_and_fits(win):
    v = win.view
    v.set_zoom(10.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().maximum())
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum())
    _pump()
    chip = v.page_chip_rect()
    if chip is None:
        pytest.skip("the scroll range does not reach past the page here")
    v.viewport().grab()
    assert v._page_chip is not None
    # it points the way to the page: up and to the left of the centre
    vc = QtCore.QPointF(v.viewport().rect().center())
    assert chip.center().x() < vc.x() and chip.center().y() < vc.y()
    p = chip.center().toPoint()
    _press(v, p)
    _release(v, p)
    _pump()
    assert v.page_chip_rect() is None
    page = v.scene().page_rect()
    assert v.mapToScene(v.viewport().rect()).boundingRect().contains(page)


# ------------------------------------------------------------------ the scroll bars
def test_thin_overlay_scroll_bars(win):
    v = win.view
    bars = v.overlay_bars
    assert bars.mode == "auto"
    assert v.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
    m = v.viewportMargins()
    fw = v.frameWidth()
    assert v.viewport().width() == v.width() - 2 * fw - m.left()
    v.set_zoom(3.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().value() + 30)
    _pump()
    assert bars.h.isVisible() and bars.h.height() <= 12
    bars.set_mode("always")
    assert v.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAsNeeded
    assert not bars.h.isVisible()
    bars.set_mode("never")
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().value() + 30)
    assert not bars.h.isVisible()
    bars.set_mode("auto")


# ------------------------------------------------------------------ touch
def test_touch_on_takes_touch_and_a_pinch_zooms_about_its_centre(win):
    v = win.view
    v.apply_touch("on")
    try:
        assert v.touch_enabled()
        assert v.viewport().testAttribute(QtCore.Qt.WA_AcceptTouchEvents)
        v.set_zoom(1.0)
        at = QtCore.QPoint(200, 150)
        keep = v.mapToScene(at)
        v.pinch(1.5, at)
        assert v.zoom() == pytest.approx(1.5)
        after = v.mapToScene(at)
        assert abs(after.x() - keep.x()) < 1 and abs(after.y() - keep.y()) < 1
        for _ in range(40):
            v.pinch(2.0, at)
        assert v.zoom() == pytest.approx(v.ZOOM_MAX)
        # a long press opens the context menu
        got = []
        v.contextRequested.disconnect(win._context_menu)  # (its menu would block)
        v.contextRequested.connect(lambda g, roots: got.append(g))
        v.long_press(QtCore.QPoint(30, 30), QtCore.QPoint(130, 130))
        assert got == [QtCore.QPoint(130, 130)]
        # a pen's barrel button is the right button
        dev = QtGui.QPointingDevice.primaryPointingDevice()
        te = QtGui.QTabletEvent(
            QtCore.QEvent.TabletPress,
            dev,
            QtCore.QPointF(40, 40),
            QtCore.QPointF(140, 140),
            0.5,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            NOMOD,
            QtCore.Qt.RightButton,
            QtCore.Qt.RightButton,
        )
        assert v.viewportEvent(te)
        assert got[-1] == QtCore.QPoint(140, 140)
    finally:
        v.apply_touch("off")
    assert not v.viewport().testAttribute(QtCore.Qt.WA_AcceptTouchEvents)


def test_auto_touch_follows_the_devices(monkeypatch):
    _app()
    import ctgedit.ui.editor.viewnav as VN
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    monkeypatch.setattr(VN, "touch_screen_present", lambda: True)
    v.apply_touch("auto")
    assert v.touch_enabled()
    monkeypatch.setattr(VN, "touch_screen_present", lambda: False)
    v.apply_touch("auto")
    assert not v.touch_enabled()
    assert math.isfinite(v.zoom())


def test_a_second_zoom_in_during_a_glide_steps_on(win, monkeypatch):
    monkeypatch.delenv("CTG_NO_ANIMATION", raising=False)
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.setattr(VN, "windows_animations_on", lambda: True)
    v = win.view
    v.ANIMATE_ZOOM = True
    v.set_zoom(1.0)
    v.finish_view_animation()
    v.zoom_step(1)
    assert v.view_animating()
    QtTest.QTest.qWait(40)  # (mid glide: between 100 % and 150 %)
    v.zoom_step(1)
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(2.0)
    v.zoom_step(-1)
    v.zoom_step(-1)
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(1.0)


def test_touchpad_scrolls_on_windows_pan_without_pixel_deltas(win, monkeypatch):
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.setattr(VN, "PAD_BY_DELTA", True)
    v = win.view
    v.TOUCHPAD_PANS = True  # (Settings > Canvas's touchpad choice: off by default)
    v.set_zoom(1.0)
    z, (_h, y) = v.zoom(), _scroll(v)
    # (a precision touchpad on Windows: small angle deltas, no pixel delta, no phase)
    for _ in range(6):
        _wheel(v, angle=(0, -24))
    assert v.zoom() == z and v.verticalScrollBar().value() > y
    # a whole notch inside the touchpad's burst pans too
    y = v.verticalScrollBar().value()
    _wheel(v, angle=(0, -120))
    assert v.zoom() == z and v.verticalScrollBar().value() > y
    # a pinch (Ctrl + the wheel) zooms
    _wheel(v, angle=(0, 24), mods=CTRL)
    assert v.zoom() > z
    # a mouse's notch after the touchpad stopped zooms
    v._pad_at -= 1.0
    z = v.zoom()
    _wheel(v, angle=(0, 120))
    assert v.zoom() > z
    # with the setting off (the default) a touchpad zooms
    v.TOUCHPAD_PANS = False
    z = v.zoom()
    _wheel(v, angle=(0, 24))
    assert v.zoom() > z


class _Hold:
    def state(self):
        return QtCore.Qt.GestureFinished

    def position(self):
        return QtCore.QPointF(130, 130)


class _GestureEvent:
    def __init__(self):
        self.hold = _Hold()
        self.ignored = []

    def gesture(self, kind):
        return self.hold if kind == QtCore.Qt.TapAndHoldGesture else None

    def ignore(self, g):
        self.ignored.append(g)

    def accept(self):
        pass


def test_a_mouse_button_held_still_opens_no_menu(win, monkeypatch):
    v = win.view
    v.apply_touch("on")
    try:
        calls = []
        monkeypatch.setattr(v, "long_press", lambda vp, g: calls.append(g))
        _press(v, (50, 50))
        assert not v._press_touch
        ev = _GestureEvent()
        v._gesture_event(ev)
        assert calls == [] and ev.ignored == [ev.hold]
        _release(v, (50, 50))
        # a finger (the mouse event Qt makes from a touch screen names the device)
        T = QtGui.QInputDevice.DeviceType
        dev = QtGui.QPointingDevice(
            "touch",
            99,
            T.TouchScreen,
            QtGui.QPointingDevice.PointerType.Finger,
            QtGui.QInputDevice.Capability.Position,
            10,
            0,
        )
        g = v.viewport().mapToGlobal(QtCore.QPoint(50, 50))
        v.mousePressEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress,
                QtCore.QPointF(50, 50),
                QtCore.QPointF(g),
                LEFT,
                LEFT,
                NOMOD,
                dev,
            )
        )
        assert v._press_touch
        v._gesture_event(_GestureEvent())
        assert calls == [QtCore.QPoint(130, 130)]
        _release(v, (50, 50))
    finally:
        v.apply_touch("off")


def test_a_middle_click_that_moves_nothing_is_no_history_step(win):
    v = win.view
    past = win.doc.ui.view_history
    past.clear()
    win._update_actions()
    _press(v, (200, 200), button=QtCore.Qt.MiddleButton)
    _release(v, (200, 200), button=QtCore.Qt.MiddleButton)
    assert not v.can_go_back()
    # a turn at the largest zoom moves nothing either
    v.set_zoom(v.ZOOM_MAX)
    past.clear()
    v._burst_at -= 1.0
    _wheel(v, angle=(0, 120))
    assert not past
    # a pan that moves is one step
    v.set_zoom(2.0)
    past.clear()
    v._burst_at -= 1.0
    _press(v, (200, 200), button=QtCore.Qt.MiddleButton)
    _move(v, (180, 190), buttons=QtCore.Qt.MiddleButton)
    _move(v, (160, 170), buttons=QtCore.Qt.MiddleButton)
    _release(v, (160, 170), button=QtCore.Qt.MiddleButton)
    assert len(past) == 1
    assert win.act_prev_view.isEnabled()
    s = _scroll(v)
    assert v.go_back() and _scroll(v) != s


def test_esc_during_a_zoom_box_with_z_held_gives_the_tool_back(win):
    v = win.view
    win._set_tool("rectangle")
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "zoom"
    _press(v, (100, 100))
    _move(v, (160, 150))
    v._zhold["t"] -= 1.0
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v._zhold_return == "rectangle"
    assert v.cancel_drag()
    assert v.tool() == "rectangle" and v._zhold_return is None
    _release(v, (160, 150))
    # let go outside the canvas (the move comes with no button): back too
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    _press(v, (100, 100))
    _move(v, (160, 150))
    v._zhold["t"] -= 1.0
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    _move(v, (170, 160), buttons=QtCore.Qt.NoButton)
    assert v.tool() == "rectangle" and v._zhold_return is None
    win._set_tool("select")


def test_holding_z_keeps_the_symbol_waiting_to_be_placed(win):
    v = win.view
    win._arm_symbol_placement("lib", "elem", "ctm")
    assert v.tool() == "symbol" and win._pending_symbol is not None
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "zoom" and win.tool_actions["zoom"].isChecked()
    assert win._pending_symbol == ("lib", "elem", "ctm")
    v._zhold["t"] -= 1.0
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "symbol" and win._pending_symbol == ("lib", "elem", "ctm")
    # a tap makes Zoom Box the tool: the placement goes
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Z, NOMOD, "z"))
    v.keyReleaseEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, QtCore.Qt.Key_Z, NOMOD, "z"))
    assert v.tool() == "zoom" and win._pending_symbol is None
    win._set_tool("select")


def test_ctrl_shift_drag_over_an_object_is_a_zoom_box_and_a_click_toggles(win):
    v = win.view
    v.set_zoom(1.0)
    win._set_tool("select")
    v._set_units([])
    doc = win.doc
    a = unit = c = None
    for k, o in enumerate(doc.page.objects):
        if o.parent is not None or o.raw.get("_from_template"):
            continue
        u = v._resolve_unit(k)
        ext = doc.selection_extent([k])
        if not u or not ext:
            continue
        p = QtCore.QPointF((ext[0] + ext[2]) / 2.0, (ext[1] + ext[3]) / 2.0)
        v.centerOn(p)
        q = v.mapFromScene(p)
        if v._topmost_unit(q) == u:
            a, unit, c = q, u, p
            break
    assert a is not None, "no object of FxShapes is on top at its centre"
    mods = CTRL | QtCore.Qt.ShiftModifier
    _press(v, a, mods)
    assert v.zoom_box_rect() is not None and not v._units
    _move(v, a + QtCore.QPoint(60, 40), mods)
    _release(v, a + QtCore.QPoint(60, 40), mods)
    assert v.zoom() > 1.5 and not v._units
    # a click (no drag) still adds the object to the selection
    v.set_zoom(1.0)
    v.centerOn(c)
    _pump()
    a = v.mapFromScene(c)
    _press(v, a, mods)
    _release(v, a, mods)
    assert v.zoom() == pytest.approx(1.0)
    assert min(unit) in v.selection_roots()


def test_the_cross_hair_readout_is_repainted_where_it_flips(win, monkeypatch):
    v = win.view
    v.set_zoom(1.0)
    vp = v.viewport()
    regions = []
    monkeypatch.setattr(vp, "update", lambda *a: regions.append(a[0] if a else None))
    v.CROSSHAIR_READOUT = True
    v._crosshair = True
    try:
        near = QtCore.QPoint(vp.width() - 30, vp.height() - 30)
        sp = v.mapToScene(near)
        v._cross_hair_to(sp)
        box = v._crosshair_readout_rect(sp)
        assert box.right() < near.x() and box.bottom() < near.y()  # flipped
        reg = regions[-1]
        assert isinstance(reg, QtGui.QRegion)
        assert reg.contains(box.toAlignedRect())
    finally:
        v._crosshair = False
        v._cross_pos = None


def test_the_rulers_wait_for_a_page(tmp_path, fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.resize(1000, 700)
        w.show()
        _pump()
        assert w.doc is None and w.act_rulers.isChecked()
        assert not w.rulers.is_shown() and not w.rulers.top.isVisible()
        assert w.view.viewportMargins().top() == 0
        dst = tmp_path / (SMALL + ".ctg")
        shutil.copy2(fx_page(SMALL), dst)
        w.open_document(str(dst))
        _pump()
        assert w.rulers.is_shown() and w.rulers.top.isVisible()
    finally:
        _close(w)


def test_the_navbar_shows_in_full_with_the_pointer_on_it(win):
    nav = win.navbar
    vp = win.view.viewport()
    _pump()
    g = nav.geometry()
    QtTest.QTest.mouseMove(vp, QtCore.QPoint(g.center().x(), g.top() - 20))
    _pump()
    assert nav.faded()
    QtTest.QTest.mouseMove(nav, nav.rect().center())
    _pump()
    if not nav.underMouse():
        pytest.skip("the offscreen platform sends no enter events here")
    assert not nav.faded()


def test_the_page_chip_keeps_clear_of_the_navigator(win):
    v = win.view
    v.set_zoom(10.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().minimum())
    v.verticalScrollBar().setValue(v.verticalScrollBar().minimum())
    v.overlay_bars.flash()
    _pump()
    win.navigator.refresh()
    chip = v.page_chip_rect()
    if chip is None:
        pytest.skip("the scroll range does not reach past the page here")
    # the page lies down and to the right
    vc = QtCore.QPointF(v.viewport().rect().center())
    assert chip.center().x() > vc.x() and chip.center().y() > vc.y()
    for w in (win.navigator, win.navbar):
        if w.isVisible():
            assert not chip.intersects(QtCore.QRectF(w.geometry())), w.objectName()
    assert QtCore.QRectF(v.viewport().rect()).contains(chip)


def test_the_navigator_shows_the_new_page_after_a_tab_switch(mixing, tmp_path, fx_page):
    v = mixing.view
    src = mixing.nav_source
    nav = mixing.navigator
    v.set_zoom(4.0)
    _pump()
    nav.refresh()
    assert nav.isVisible()
    src.rebuild()
    first, first_scene = src._pixmap, v.scene()
    assert first is not None
    dst = tmp_path / (SMALL + ".ctg")
    shutil.copy2(fx_page(SMALL), dst)
    mixing.open_document(str(dst))
    _pump()
    assert v.scene() is not first_scene
    assert src._scene is v.scene() and src._pixmap is not first  # the old picture went
    v.set_zoom(4.0)
    _pump()
    QtTest.QTest.qWait(450)
    _pump()
    assert not src._dirty and src._pixmap is not None and src._pixmap is not first
    assert src._work == v.scene().work_rect()


def test_on_a_small_canvas_the_navigator_shows_as_its_button(tmp_path, fx_page):
    _app()
    w = _open(tmp_path, fx_page(SMALL), 900, 600)
    try:
        v, nav = w.view, w.navigator
        v.set_zoom(4.0)
        _pump()
        nav.refresh()
        if not nav.squeezed():
            pytest.skip("the canvas is not small here")
        assert nav.isVisible() and not nav.map.isVisible() and not nav.collapsed()
        nav.toggle_collapsed()  # the button opens it, until the canvas grows
        assert nav.map.isVisible()
        from ctgedit.ui.prefs.settings import app_settings

        assert app_settings().value("view/navigator_collapsed", False, type=bool) is False
        nav.toggle_collapsed()
        assert not nav.map.isVisible() and not nav.collapsed()
    finally:
        _close(w)


def _glide_on(v, monkeypatch):
    monkeypatch.delenv("CTG_NO_ANIMATION", raising=False)
    import ctgedit.ui.editor.viewnav as VN

    monkeypatch.setattr(VN, "windows_animations_on", lambda: True)
    v.ANIMATE_ZOOM = True
    assert v.can_animate()


def _key(widget, kind, auto=False):
    ev = QtGui.QKeyEvent(kind, QtCore.Qt.Key_Space, NOMOD, " ", auto)
    return QtWidgets.QApplication.sendEvent(widget, ev)


def test_space_held_with_key_repeat_ends_the_pan_on_the_real_release(win, monkeypatch):
    from ctgedit.ui.editor.viewnav import SpacePanForwarder

    v = win.view
    monkeypatch.setattr(SpacePanForwarder, "_canvas_under_pointer", staticmethod(lambda: v))
    tree = win.library_dock._tree  # (the Objects tree keeps Space as its own)
    tree.setFocus()
    _pump()
    if QtWidgets.QApplication.focusWidget() is not tree:
        pytest.skip("the offscreen window does not take the focus")
    _key(tree, QtCore.QEvent.KeyPress)
    assert v._space_pan
    for _ in range(3):  # held past the key-repeat delay
        assert _key(tree, QtCore.QEvent.KeyRelease, auto=True)  # (kept from the tree)
        assert _key(tree, QtCore.QEvent.KeyPress, auto=True)
        assert v._space_pan
    _key(tree, QtCore.QEvent.KeyRelease)
    assert not v._space_pan


def test_the_zoom_field_shows_the_typed_zoom_while_the_view_glides(win, monkeypatch):
    v = win.view
    _glide_on(v, monkeypatch)
    v.fit()
    f = win.navbar.zoom_field
    f.setText("150")
    assert f.apply()
    assert v.view_animating()  # (the glide is at its start)
    assert f.text() == "150 %"
    assert v.target_zoom() == pytest.approx(1.5)
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(1.5) and f.text() == "150 %"
    f.setText("99999")  # out of range: the field says where it goes
    assert f.apply()
    assert f.text() == "6400 %"
    v.finish_view_animation()


def test_the_selection_box_is_measured_once_and_follows_a_move(win, monkeypatch):
    v = win.view
    v.set_snap(False)
    v.set_zoom(1.0)
    win.select_all()
    _pump()
    calls = {"n": 0}
    orig = type(v)._outline_parts

    def counting(self, unit):
        calls["n"] += 1
        return orig(self, unit)

    monkeypatch.setattr(type(v), "_outline_parts", counting)
    v._drop_unit_rects()  # (a paint measured it already)
    box = v.selection_box()
    assert box is not None
    n = calls["n"]
    assert n >= 1
    for _ in range(5):
        assert v.selection_box() == box
    assert calls["n"] == n  # (remembered)
    # a move drag: the box follows the offset without measuring again
    unit = v._units[0]
    at = v.mapFromScene(v._unit_rect(unit).center())
    _press(v, at)
    _move(v, at + QtCore.QPoint(20, 0))
    _move(v, at + QtCore.QPoint(40, 0))
    if not v._moving:
        _release(v, at + QtCore.QPoint(40, 0))
        pytest.skip("the press did not start a move here")
    n = calls["n"]
    moved = v.selection_box()
    assert v._move_applied.x() > 0
    assert moved.left() == pytest.approx(box.left() + v._move_applied.x())
    assert calls["n"] == n
    v.cancel_drag()
    _release(v, at + QtCore.QPoint(40, 0))
    assert v.selection_box() == box
    # another selection: measured again
    v._set_units([unit])
    n = calls["n"]
    assert v.selection_box() == v._outline_parts(unit)[0] or v.selection_box() is not None
    assert calls["n"] > n


def test_the_rulers_repaint_only_what_changed(win, monkeypatch):
    v = win.view
    v.set_zoom(1.0)
    win.select_all()
    _pump()
    r = win.rulers
    assert r.is_shown()
    calls = []
    for ruler in (r.top, r.left):

        def counting(*args, _name=ruler.objectName()):
            calls.append((_name, args))

        monkeypatch.setattr(ruler, "update", counting)
    r.follow()  # (the state as it is now)
    calls.clear()
    v.viewport().repaint()  # nothing the rulers show changed
    r.follow()
    assert calls == []
    # the pointer moves: only the strips under its marks
    vp = v.viewport()
    monkeypatch.setattr(type(vp), "underMouse", lambda self: True)
    _move(v, (200, 150), buttons=QtCore.Qt.NoButton)
    r.follow()
    assert calls and all(args and isinstance(args[0], QtGui.QRegion) for _n, args in calls)
    top = [args[0] for n, args in calls if n == "RulerTop"]
    assert top and top[0].boundingRect().width() < 40
    calls.clear()
    # another selection: all of each ruler
    v._set_units([])
    r.follow()
    assert ("RulerTop", ()) in calls and ("RulerLeft", ()) in calls
    calls.clear()
    # the zoom: all of it
    v.set_zoom(2.0)
    assert ("RulerTop", ()) in calls


def test_a_ruler_paints_a_strip_as_its_whole_paint_does(win):
    """A partial repaint (the pointer's strip) draws only the ticks and labels that reach
    it, and the same pixels as a whole repaint."""
    v = win.view
    v.set_zoom(1.0)
    _pump()
    top = win.rulers.top
    full = top.grab().toImage()
    img = QtGui.QImage(full.size(), full.format())
    img.setDevicePixelRatio(full.devicePixelRatio())
    img.fill(QtGui.QColor(255, 0, 255))
    strip = QtCore.QRect(97, 0, 9, top.height())
    top.render(img, strip.topLeft(), QtGui.QRegion(strip))
    for x in range(strip.left(), strip.right() + 1):
        for y in range(top.height()):
            assert img.pixel(x, y) == full.pixel(x, y), (x, y)


def test_go_to_object_comes_back_to_the_view_before(win):
    v = win.view
    v.set_zoom(1.0)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().minimum())
    v.verticalScrollBar().setValue(v.verticalScrollBar().minimum())
    v._drop_burst()
    before = _scroll(v)
    best = None
    for i, o in enumerate(win.doc.page.objects):
        g = o.geometry
        if o.parent is None and g is not None and g.extent_x > 5 and g.extent_y > 5:
            if best is None or (g.x + g.y) > best[1]:
                best = (i, g.x + g.y)
    assert best is not None

    def near(s):  # (a view comes back by its centre)
        return abs(s[0] - before[0]) <= 1 and abs(s[1] - before[1]) <= 1

    win._goto_frame(best[0])
    v.finish_view_animation()
    assert not near(_scroll(v))
    win._prev_view()
    v.finish_view_animation()
    assert near(_scroll(v)) and v.zoom() == pytest.approx(1.0)
    before = _scroll(v)
    # the page row: Fit Page, a step back too
    win._goto_frame(-1)
    v.finish_view_animation()
    assert v.zoom() != pytest.approx(1.0)
    win._prev_view()
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(1.0) and near(_scroll(v))
    # a row highlighted moves the view: Previous View comes back from it too
    v.horizontalScrollBar().setValue(before[0])
    v.verticalScrollBar().setValue(before[1])
    v._drop_burst()
    win._goto_highlight(best[0])
    if near(_scroll(v)):
        pytest.skip("the object is in view already")
    win._prev_view()
    v.finish_view_animation()
    assert near(_scroll(v))


def test_a_tab_switch_during_a_glide_keeps_the_view_it_was_going_to(win, tmp_path, monkeypatch, fx_page):
    dst = tmp_path / (BIG + ".ctg")
    shutil.copy2(fx_page(BIG), dst)
    win.open_document(str(dst))
    _pump()
    v = win.view
    _glide_on(v, monkeypatch)
    tabs = win.doc_tabs
    assert tabs.count() == 2
    tabs.setCurrentIndex(0)
    _pump()
    v.fit()
    assert v.zoom() != pytest.approx(1.0)
    win.act_zoom_100.trigger()
    assert v.view_animating()
    tabs.setCurrentIndex(1)
    _pump()
    assert not v.view_animating()
    tabs.setCurrentIndex(0)
    _pump()
    v.finish_view_animation()
    assert v.zoom() == pytest.approx(1.0)


def test_the_map_is_not_made_again_for_the_text_editor_alone(win):
    from ctgedit.model.document import ObjectKind

    v = win.view
    src = win.nav_source
    idx = next(
        (
            i
            for i, o in enumerate(win.doc.page.objects)
            if o.kind is ObjectKind.TEXT and win._inplace_refusal(i) is None
        ),
        None,
    )
    assert idx is not None, "FxShapes has texts (FxText ...) to edit"
    assert win.edit_text_in_place(idx)
    try:
        item = v.text_edit_item()
        _pump()
        src._timer.stop()
        src._dirty = False
        inside = item.sceneBoundingRect().adjusted(1, 1, -1, -1)
        src.schedule([inside])  # (the caret blinks)
        assert not src._timer.isActive() and not src._dirty
        far = item.sceneBoundingRect().translated(5000, 5000)
        src.schedule([inside, far])  # something else changed too
        assert src._timer.isActive() and src._dirty
    finally:
        v.finish_text_edit(False)
    src._timer.stop()
    src._dirty = False
    src.schedule([inside])  # no editor: any change counts
    assert src._timer.isActive()


def test_a_click_by_the_edge_after_a_scroll_reaches_the_canvas(win):
    v = win.view
    bars = v.overlay_bars
    v.set_zoom(3.0)
    vs = v.verticalScrollBar()
    vs.setValue(vs.value() + 30)
    _pump()
    bar = bars.v
    assert bar.isVisible()
    vp = v.viewport()
    y = vp.height() // 3
    nine = QtCore.QPoint(vp.width() - 9, y)
    assert bar.geometry().contains(nine)  # (the bar's widget is that wide)
    assert vp.childAt(nine) is not bar  # but the canvas takes the click
    assert bar.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    # a press on its track while the pointer is not on it pages nothing
    value = vs.value()
    QtTest.QTest.mouseClick(bar, LEFT, NOMOD, bar.mapFrom(vp, nine))
    assert vs.value() == value
    # the pointer on the thin bar: the bar takes the mouse and widens
    on = QtCore.QPoint(vp.width() - 3, y)
    QtWidgets.QApplication.sendEvent(vp, _me(v, QtCore.QEvent.MouseMove, on))
    assert bar.hot() and not bar.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    assert vp.childAt(on) is bar
    # back on the canvas: it passes the mouse through again
    QtWidgets.QApplication.sendEvent(vp, _me(v, QtCore.QEvent.MouseMove, QtCore.QPoint(vp.width() - 40, y)))
    assert not bar.hot() and bar.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    # a drag on the canvas never makes it hot
    QtWidgets.QApplication.sendEvent(vp, _me(v, QtCore.QEvent.MouseMove, on, buttons=LEFT))
    assert not bar.hot()


def test_a_guideline_dragged_out_of_a_ruler_scrolls_at_the_edge(win):
    v = win.view
    v.set_zoom(4.0)
    v.set_guidelines([], [])
    page = v.scene().page_rect()
    seen = v.mapToScene(v.viewport().rect()).boundingRect()
    v.centerOn(page.center().x(), page.top() + seen.height() / 2)  # (the page's top part)
    _pump()
    top = win.rulers.top
    vp = v.viewport()
    vs = v.verticalScrollBar()

    def move(pt):
        ev = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove,
            QtCore.QPointF(pt),
            QtCore.QPointF(top.mapToGlobal(pt)),
            QtCore.Qt.NoButton,
            LEFT,
            NOMOD,
        )
        QtWidgets.QApplication.sendEvent(top, ev)

    def to_top(vpt):
        return top.mapFromGlobal(vp.mapToGlobal(vpt))

    p0 = QtCore.QPoint(top.width() // 2, top.height() // 2)
    QtTest.QTest.mousePress(top, LEFT, NOMOD, p0)
    # in the edge band just below the ruler: no scroll yet (the drag starts there)
    move(to_top(QtCore.QPoint(p0.x(), 6)))
    start = vs.value()
    QtTest.QTest.qWait(120)
    assert vs.value() == start
    # through the canvas to its bottom edge: it scrolls, the guideline follows
    move(to_top(QtCore.QPoint(p0.x(), vp.height() // 2)))
    edge = to_top(QtCore.QPoint(p0.x(), vp.height() - 4))
    move(edge)
    QtTest.QTest.qWait(250)
    assert vs.value() > start + 10
    assert v.mapToScene(QtCore.QPoint(0, vp.height() - 4)).y() < page.bottom() - 16
    QtTest.QTest.mouseRelease(top, LEFT, NOMOD, edge)
    assert not v._edge_timer.isActive()
    _vert, horiz = v.guidelines()
    assert len(horiz) == 1
    bottom = v.mapToScene(QtCore.QPoint(0, vp.height() - 4)).y()
    assert abs(horiz[0] - bottom) <= 8 + 2  # (snapped to the 8 px grid)


def test_a_view_past_the_limit_is_not_said_to_be_saved(win, monkeypatch):
    """At the page's most (40), a new name is refused and said so, a known name still
    replaces its view, and Manage Saved Views' Add is off."""
    from ctgedit.ui.editor import saved_views as SV
    from ctgedit.ui.prefs import timing_options

    n = timing_options.SAVED_VIEWS_MAX
    SV.store_views(win.doc, [SV.current_view(win.view, "Position %d" % k) for k in range(1, n + 1)])
    told = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a[2])))
    win.statusBar().clearMessage()
    win._save_view_dialog()
    pop = win._save_view_popover
    pop.field.setText("Position %d" % (n + 1))
    pop._save()
    assert told and "most it keeps" in told[0]
    assert len(SV.load_views(win.doc)) == n and "Saved the view" not in win.statusBar().currentMessage()
    # a known name replaces it
    win.view.set_zoom(2.0)
    win._save_view_dialog()
    pop = win._save_view_popover
    pop.field.setText("Position 3")
    pop._save()
    vs = SV.load_views(win.doc)
    assert len(vs) == n and next(v for v in vs if v["name"] == "Position 3")["zoom"] == pytest.approx(2.0)
    win._manage_saved_views()
    dlg = win._dialogs.get("saved_views")
    try:
        assert not dlg.b_add.isEnabled() and "most it keeps" in dlg.b_add.toolTip()
        dlg.list.setCurrentRow(0)
        dlg._remove()
        assert dlg.b_add.isEnabled()
    finally:
        dlg.close()
