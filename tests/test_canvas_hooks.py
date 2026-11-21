"""Canvas extension hooks."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page the window tests open (a copy in fx_install)
HOST = "FxMaster"

LEFT = QtCore.Qt.LeftButton
NOMOD = QtCore.Qt.NoModifier


def _pump(n=4):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _me(view, kind, sp, buttons=QtCore.Qt.NoButton, mods=NOMOD, button=LEFT):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), button, buttons, mods)


def _press(view, sp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, sp, LEFT, mods))


def _move(view, sp, mods=NOMOD, buttons=LEFT):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, sp, buttons, mods))


def _release(view, sp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, sp, QtCore.Qt.NoButton, mods))


def _key(view, key, mods=NOMOD):
    view.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods))


@pytest.fixture()
def win(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page(HOST))
    w.resize(1300, 900)
    w.show()
    w.view.set_smart_guides(False)
    w.view.reset_zoom()
    w.view.centerOn(300, 220)
    _pump()
    yield w
    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


@pytest.fixture()
def bare():
    """A canvas on an empty scene, shown."""
    _app()
    from ctgedit.ui.editor.canvas import CanvasView

    v = CanvasView()
    sc = QtWidgets.QGraphicsScene(-500, -500, 1000, 1000)
    v.setScene(sc)
    v.resize(400, 300)
    v.show()
    _pump()
    yield v
    v.hide()
    v.deleteLater()


# ------------------------------------------------------------------ the hooks
def test_an_overlay_registered_from_outside_paints_in_scene_coordinates(bare):
    from ctgedit.ui.editor import canvas_hooks as H

    seen = []

    def paint(view, painter, rect):
        seen.append((view, painter.transform().m11(), QtCore.QRectF(rect)))
        painter.setBrush(QtGui.QColor(0, 200, 0))  # (restored after the call)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRect(QtCore.QRectF(-20, -20, 40, 40))

    # (an editor window built earlier in the same process registers overlays of its own:
    # the test's own comes and goes)
    before = [o.name for o in H.overlays("foreground")]
    H.register_overlay("test.green", paint)
    try:
        img = bare.viewport().grab().toImage()
        assert seen and seen[-1][0] is bare
        c = QtGui.QColor(img.pixel(bare.mapFromScene(QtCore.QPointF(0, 0))))
        assert c.green() > 150 and c.red() < 80
        assert sorted([o.name for o in H.overlays("foreground")]) == sorted(before + ["test.green"])
    finally:
        H.unregister_overlay("test.green")
    assert [o.name for o in H.overlays("foreground")] == before


def test_a_background_overlay_draws_under_the_items_and_repaints_on_request(bare):
    from ctgedit.ui.editor import canvas_hooks as H

    item = bare.scene().addRect(
        QtCore.QRectF(-10, -10, 20, 20), QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(0, 0, 255))
    )
    calls = []

    def paint(view, painter, rect):
        calls.append(1)
        painter.fillRect(QtCore.QRectF(-40, -40, 80, 80), QtGui.QColor(255, 0, 0))

    H.register_overlay("test.under", paint, layer="background")
    try:
        img = bare.viewport().grab().toImage()
        mid = QtGui.QColor(img.pixel(bare.mapFromScene(QtCore.QPointF(0, 0))))
        edge = QtGui.QColor(img.pixel(bare.mapFromScene(QtCore.QPointF(30, 30))))
        assert mid.blue() > 200 and mid.red() < 60  # the item is over the overlay
        assert edge.red() > 200 and edge.blue() < 60
        n = len(calls)
        bare.request_overlay_update(background=True)
        bare.viewport().grab()
        assert len(calls) > n  # the cached background redrawn
    finally:
        H.unregister_overlay("test.under")
        bare.scene().removeItem(item)
    with pytest.raises(ValueError):
        H.register_overlay("test.bad", paint, layer="middle")


def test_a_failing_overlay_does_not_stop_the_paint(bare):
    from ctgedit.ui.editor import canvas_hooks as H

    ok = []
    H.register_overlay("test.a_fails", lambda v, p, r: 1 / 0, order=0)
    H.register_overlay("test.b_paints", lambda v, p, r: ok.append(1), order=1)
    try:
        bare.viewport().grab()
        assert ok
    finally:
        H.unregister_overlay("test.a_fails")
        H.unregister_overlay("test.b_paints")


def test_a_viewport_filter_goes_on_canvases_made_before_and_after(bare):
    from ctgedit.ui.editor import canvas_hooks as H
    from ctgedit.ui.editor.canvas import CanvasView

    class Watch(QtCore.QObject):
        def __init__(self, view):
            super().__init__(view)
            self.view = view
            self.events = 0

        def eventFilter(self, obj, ev):
            if ev.type() == QtCore.QEvent.Paint:
                self.events += 1
            return False

    made = []
    H.register_viewport_filter("test.watch", lambda v: made.append(Watch(v)) or made[-1])
    other = CanvasView()
    try:
        other.setScene(QtWidgets.QGraphicsScene())
        other.resize(200, 150)
        other.show()
        _pump()
        assert {m.view for m in made} >= {bare, other}
        bare.viewport().repaint()
        assert next(m for m in made if m.view is bare).events > 0
    finally:
        H.unregister_viewport_filter("test.watch")
        other.hide()
        other.deleteLater()
    n = next(m for m in made if m.view is bare).events
    bare.viewport().repaint()
    assert next(m for m in made if m.view is bare).events == n  # removed


def _drag_event(view, kind, sp, mime):
    vp = view.mapFromScene(QtCore.QPointF(*sp))
    if kind == QtCore.QEvent.Drop:
        return QtGui.QDropEvent(QtCore.QPointF(vp), QtCore.Qt.CopyAction, mime, LEFT, NOMOD)
    cls = QtGui.QDragEnterEvent if kind == QtCore.QEvent.DragEnter else QtGui.QDragMoveEvent
    return cls(vp, QtCore.Qt.CopyAction, mime, LEFT, NOMOD)


def test_a_drop_handler_is_asked_before_the_built_in_ones(bare):
    from ctgedit.ui.editor import canvas_hooks as H

    mime = QtCore.QMimeData()
    mime.setData("application/x-test-thing", b"pump")
    dropped = []

    def accept(view, event, sp, unit):
        return "Drop the thing here" if event.mimeData().hasFormat("application/x-test-thing") else None

    def drop(view, event, sp, unit):
        dropped.append(
            (bytes(event.mimeData().data("application/x-test-thing")), round(sp.x()), round(sp.y()), unit)
        )
        return True

    # (an editor window built earlier in the same process registers its own handlers)
    before = [d.name for d in H.drop_handlers()]
    H.register_drop_handler("test.thing", accept, drop, priority=5)
    try:
        e = _drag_event(bare, QtCore.QEvent.DragEnter, (10, 20), mime)
        bare.dragEnterEvent(e)
        assert e.isAccepted()
        e = _drag_event(bare, QtCore.QEvent.DragMove, (12, 22), mime)
        bare.dragMoveEvent(e)
        assert bare._drop_hint is not None and bare._drop_hint[1] == "Drop the thing here"
        e = _drag_event(bare, QtCore.QEvent.Drop, (12, 22), mime)
        bare.dropEvent(e)
        assert dropped == [(b"pump", 12, 22, [])]
        assert bare._drop_hint is None
        other = QtCore.QMimeData()
        other.setText("x")
        assert (
            H.drop_hint(bare, _drag_event(bare, QtCore.QEvent.DragMove, (0, 0), other), QtCore.QPointF(), [])
            is None
        )
    finally:
        H.unregister_drop_handler("test.thing")
    assert [d.name for d in H.drop_handlers()] == before


# ------------------------------------------------------------------ the extractions
def test_the_moved_names_are_where_they_were():
    from ctgedit.ui.editor import canvas
    from ctgedit.ui.editor.inplace_text import InplaceText
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.window.canvas_edits import CanvasEdits
    from ctgedit.ui.window.text_edit import TextEditMixin

    assert canvas.InplaceText is InplaceText
    assert issubclass(MainWindow, TextEditMixin)
    for name in ("edit_text_in_place", "_inplace_refusal", "_ask_number_expression"):
        assert name in vars(TextEditMixin) and name not in vars(CanvasEdits)
        assert getattr(MainWindow, name) is vars(TextEditMixin)[name]


def test_edit_text_in_place_still_opens_and_stores(win):
    from ctgedit.model.document import ObjectKind

    doc = win.doc
    i = doc.find_objects_by_name("FxMasterText")[0]
    assert doc.page.objects[i].kind is ObjectKind.TEXT and win._inplace_refusal(i) is None
    win._set_selection([i])
    assert win.edit_text_in_place()
    item = win.view.text_edit_item()
    assert item is not None and type(item).__name__ == "InplaceText"
    item.setPlainText("Hello there")
    win.view.finish_text_edit(True)
    _pump()
    assert "Hello there" in doc.page.objects[i].style.text  # (a label shows as @(text))
    assert win.act_undo.isEnabled()


# ------------------------------------------------------------------ draw-02
def test_a_polygon_being_drawn_with_a_rectangle_selected_is_not_filled(win):
    """The resize handles' solid brush does not fill the polygon preview: three points with a
    rectangle selected, the pixel inside the triangle keeps (almost) the page colour."""
    view = win.view
    view.set_snap(False)
    i = win.doc.create_object("rectangle", (40, 40, 90, 80), None)
    win._refresh()
    win._set_selection([i])
    _pump()
    page = QtGui.QColor(view.viewport().grab().toImage().pixel(view.mapFromScene(QtCore.QPointF(300, 260))))
    win._set_tool("polygon")
    for sp in ((240, 200), (380, 200)):
        _press(view, sp)
        _release(view, sp)
    _move(view, (310, 330), buttons=QtCore.Qt.NoButton)
    img = view.viewport().grab().toImage()
    inside = QtGui.QColor(img.pixel(view.mapFromScene(QtCore.QPointF(310, 240))))
    for a, b in ((inside.red(), page.red()), (inside.green(), page.green()), (inside.blue(), page.blue())):
        assert abs(a - b) <= 40, (inside.name(), page.name())
    # the closing edge back to the first point is drawn (dashed accent)
    assert view._nodes and view._draw_cur is not None
    win._set_tool("select")


def test_a_pipe_preview_is_never_filled(win):
    view = win.view
    view.set_snap(False)
    page = QtGui.QColor(view.viewport().grab().toImage().pixel(view.mapFromScene(QtCore.QPointF(310, 240))))
    win._set_tool("pipe")
    for sp in ((240, 200), (380, 200)):
        _press(view, sp)
        _release(view, sp)
    _move(view, (310, 330), buttons=QtCore.Qt.NoButton)
    inside = QtGui.QColor(view.viewport().grab().toImage().pixel(view.mapFromScene(QtCore.QPointF(310, 240))))
    assert inside.rgb() == page.rgb()
    win._set_tool("select")


# ------------------------------------------------------------------ keyboard-05
def test_esc_during_a_move_puts_the_objects_back_and_commits_nothing(win):
    view = win.view
    i = win.doc.create_object("rectangle", (100, 100, 150, 130), None)
    win._refresh()
    undo_before = len(win.doc._undo)
    rect_before = tuple(win.doc.selection_extent([i]))
    _press(view, (125, 115))
    for k in (1, 2, 3):
        _move(view, (125 + 20 * k, 115 + 10 * k))
    assert view._moving
    item = win.scene._items[i]
    moved_pos = item.pos()
    msgs = []
    view.statusMessage.connect(msgs.append)
    _key(view, QtCore.Qt.Key_Escape)
    assert not view._moving and view._pill is None and not view._sg_active
    assert item.pos() != moved_pos  # the preview went back
    assert view.selection_roots() == [i]  # the selection stays
    assert msgs == ["Move cancelled."]
    _release(view, (185, 145))
    _pump()
    assert len(win.doc._undo) == undo_before
    assert tuple(win.doc.selection_extent([i])) == rect_before


def test_esc_during_a_resize_commits_nothing(win):
    view = win.view
    view.set_snap(False)
    i = win.doc.create_object("rectangle", (100, 100, 150, 130), None)
    win._refresh()
    win._set_selection([i])
    _pump()
    undo_before = len(win.doc._undo)
    rect_before = tuple(win.doc.selection_extent([i]))
    _press(view, (150, 130))  # the bottom-right handle
    assert view._resizing is not None
    _move(view, (190, 170))
    msgs = []
    view.statusMessage.connect(msgs.append)
    _key(view, QtCore.Qt.Key_Escape)
    assert view._resizing is None and msgs == ["Resize cancelled."]
    _release(view, (190, 170))
    _pump()
    assert len(win.doc._undo) == undo_before
    assert tuple(win.doc.selection_extent([i])) == rect_before


def test_esc_during_a_selection_box_keeps_the_selection_before_it(win):
    view = win.view
    i = win.doc.create_object("rectangle", (100, 100, 150, 130), None)
    win._refresh()
    win._set_selection([i])
    _pump()
    before = view.selection_roots()
    _press(view, (600, 500))  # empty page: a selection box
    _move(view, (650, 540))
    assert view._marquee is not None
    _key(view, QtCore.Qt.Key_Escape)
    assert view._marquee is None
    assert view.selection_roots() == before
    _release(view, (650, 540))
    assert view.selection_roots() == before


# ------------------------------------------------------------------ the focus frame
def test_the_keyboard_focus_frame_shows_after_keys_not_after_clicks(win):
    view = win.view
    view.setFocus(QtCore.Qt.TabFocusReason)
    _pump()
    win.activateWindow()
    view.focusInEvent(QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.TabFocusReason))
    if not view.hasFocus():
        pytest.skip("the offscreen window does not take the focus")
    assert view.keyboard_focus_shown()
    img = view.viewport().grab().toImage()
    edge = QtGui.QColor(img.pixel(1, view.viewport().height() // 2))
    from ctgedit.ui.style.theme import color

    acc = color("accent")
    assert abs(edge.blue() - acc.blue()) < 40 and abs(edge.red() - acc.red()) < 40
    _press(view, (600, 500))
    _release(view, (600, 500))
    assert not view.keyboard_focus_shown()
    _key(view, QtCore.Qt.Key_Right)
    assert view.keyboard_focus_shown()
