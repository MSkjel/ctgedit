"""Selection in the window: copy drags, touch boxes, menus and Tab."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

CTRL = QtCore.Qt.ControlModifier
SHIFT = QtCore.Qt.ShiftModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton
#: the synthetic objects lie this far below the page, where nothing else is
OY = 2000


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _open(src):
    """A main window on ``src`` (a page of the test's fx_install copy)."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(src)
    w.resize(1400, 1000)
    w.show()
    _pump()
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    return w


def _close(w):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    C_look.set_modifier_scheme("builder", "ctrl")
    app_settings().remove("canvas/ctrl_shift_drag")
    app_settings().remove("canvas/drag_copy_key")


@pytest.fixture()
def about(fx_page):
    """The near-empty page: FxMain's FxThumb."""
    w = _open(fx_page("FxThumb"))
    yield w
    _close(w)


@pytest.fixture()
def mixer(fx_page):
    """A page with a placed valve genie: FxMain's FxParts (FxValveLeft1)."""
    w = _open(fx_page("FxParts"))
    yield w
    _close(w)


def _me(view, kind, vp, buttons=QtCore.Qt.NoButton, mods=NOMOD):
    vp = QtCore.QPoint(vp)
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _press(view, vp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, LEFT, mods))


def _move(view, vp, mods=NOMOD):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, vp, LEFT, mods))


def _release(view, vp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, QtCore.Qt.NoButton, mods))


def _key(view, key, mods, press=True):
    kind = QtCore.QEvent.KeyPress if press else QtCore.QEvent.KeyRelease
    ev = QtGui.QKeyEvent(kind, key, mods)
    (view.keyPressEvent if press else view.keyReleaseEvent)(ev)


def _rect(win, l, t, r, b, colour=0x3366CC):
    i = win.doc.create_object("rectangle", (l, t + OY, r, b + OY), None, colour=colour)
    win._refresh()
    win.view.set_zoom(1.0)
    win.view.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    return i


def _at(view, x, y):
    return view.mapFromScene(QtCore.QPointF(x, y + OY))


def _ghosts(view):
    """The pixmap items in the scene: only a copy drag's picture is one on these pages."""
    return [it for it in view.scene().items() if isinstance(it, QtWidgets.QGraphicsPixmapItem)]


# ================================================================== the copy drag's picture
def test_a_copy_drag_leaves_a_picture_of_the_original_until_it_ends(about):
    w, v = about, about.view
    i = _rect(w, 300, 300, 360, 340, colour=0xCC3333)
    v.select_units([[i]], reveal=False)
    before = len(_ghosts(v))
    a = _at(v, 330, 320)
    _press(v, a)
    for k in range(1, 5):
        _move(v, a + QtCore.QPoint(20 * k, 0), CTRL)
    g = v.copy_ghost()
    assert g is not None and g.isVisible() and g.zValue() < -1e8
    from ctgedit.render.scene import PAGE_INDEX_ROLE

    assert g.data(PAGE_INDEX_ROLE) is None and g.acceptedMouseButtons() == QtCore.Qt.NoButton
    # it lies where the original was (the dragged items are 80 px on) and shows it
    r = g.sceneBoundingRect()
    assert abs(r.left() - 297) <= 2 and abs(r.top() - (OY + 297)) <= 2
    img = g.pixmap().toImage()
    mid = img.pixelColor(img.width() // 2, img.height() // 2)
    assert (mid.red(), mid.green(), mid.blue(), mid.alpha()) == (0xCC, 0x33, 0x33, 255)
    corner = img.pixelColor(0, 0)
    assert corner.alpha() == 0  # (no page background in it)
    # a click where the original was finds no object in it
    assert v._hits_under(_at(v, 330, 320)) == []
    # Ctrl let go: hidden, the move goes on; again: the same picture shows
    # (QKeyEvent.modifiers() flips a modifier key's own bit: the events are built with the
    # keys held before them, as Windows sends them)
    _key(v, QtCore.Qt.Key_Control, CTRL, press=False)
    assert not g.isVisible()
    _key(v, QtCore.Qt.Key_Control, NOMOD)
    assert v.copy_ghost() is g and g.isVisible()
    _release(v, a + QtCore.QPoint(80, 0), CTRL)
    _pump(2)
    assert v.copy_ghost() is None and len(_ghosts(v)) == before
    # the page: the original and its copy, 80 px on
    copy = w._sel[0]
    assert copy != i and w.doc.page.objects[copy].geometry.x == 380
    assert w.doc.page.objects[i].geometry.x == 300


def test_esc_and_a_plain_move_leave_no_picture(about):
    w, v = about, about.view
    i = _rect(w, 300, 300, 360, 340)
    v.select_units([[i]], reveal=False)
    before = len(_ghosts(v))
    a = _at(v, 330, 320)
    _press(v, a)
    _move(v, a + QtCore.QPoint(30, 0), CTRL)
    assert v.copy_ghost() is not None
    assert v.cancel_drag()
    _release(v, a + QtCore.QPoint(30, 0), CTRL)
    assert v.copy_ghost() is None and len(_ghosts(v)) == before
    assert w.doc.page.objects[i].geometry.x == 300
    # a plain move makes none
    _press(v, a)
    for k in range(1, 4):
        _move(v, a + QtCore.QPoint(10 * k, 0))
    assert v.copy_ghost() is None and len(_ghosts(v)) == before
    _release(v, a + QtCore.QPoint(30, 0))
    _pump(1)
    assert w.doc.page.objects[i].geometry.x == 330


def test_the_picture_leaves_the_saved_bytes_alone(mixer, tmp_path):
    """A copy drag writes what Duplicate (offset 0) + Move write, picture or not."""
    from ctgedit.editing import EditableDocument

    w, v = mixer, mixer.view
    found = w.doc.find_objects_by_name("FxValveLeft1")
    assert len(found) == 1
    genie = found[0]
    assert genie in w.doc.page.roots()
    assert "valve_left" in str(w.doc.page.objects[genie].genie_template)
    twin_path = tmp_path / "twin.ctg"
    twin_path.write_bytes(w.doc.serialize())
    twin = EditableDocument.open(str(twin_path))
    g = w.doc.page.objects[genie].geometry
    c = QtCore.QPointF(g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0)
    v.set_zoom(1.0)
    v.centerOn(c)
    _pump(2)
    w._set_selection([genie])
    a = v.mapFromScene(c)
    _press(v, a)
    for k in range(1, 5):
        _move(v, a + QtCore.QPoint(0, 15 * k), CTRL)
    assert v.copy_ghost() is not None
    _release(v, a + QtCore.QPoint(0, 60), CTRL)
    _pump(2)
    copy = twin.duplicate_objects([genie], offset=0)
    twin.move_objects(copy, 0, 60)
    assert w.doc.serialize() == twin.serialize()


# ================================================================== Ctrl+Shift + drag
def test_ctrl_shift_drag_is_a_touch_box_by_the_setting(about):
    from ctgedit.ui.prefs.settings import app_settings

    w, v = about, about.view
    a = _rect(w, 300, 300, 340, 330)
    b = _rect(w, 400, 300, 440, 330)
    both = CTRL | SHIFT
    # the default: a zoom box
    assert v.CTRL_SHIFT_DRAG == "zoom"
    _press(v, _at(v, 290, 290), both)
    assert v._zbox is not None
    v.cancel_drag()
    _release(v, _at(v, 290, 290), both)
    app_settings().setValue("canvas/ctrl_shift_drag", "touch")
    w.apply_select_options()
    assert v.CTRL_SHIFT_DRAG == "touch"
    v.set_marquee_mode("enclose")
    # from a press on the first rectangle, left to right: a Touch box all the same
    v.select_units([], reveal=False)
    start = _at(v, 320, 315)
    _press(v, start, both)
    assert v._zbox is None and v._marquee is None  # (a click yet: it may toggle)
    _move(v, start + QtCore.QPoint(40, 0), both)
    _move(v, _at(v, 410, 320), both)
    assert v._marquee is not None and v._marquee_touch
    _release(v, _at(v, 410, 320), both)
    _pump(1)
    assert v.selection_roots() == [a, b]  # both crossed, neither enclosed
    assert w.doc.page.objects[a].geometry.x == 300  # nothing moved
    # a Ctrl+Shift click on an object still adds it or takes it out
    _press(v, _at(v, 420, 315), both)
    _release(v, _at(v, 420, 315), both)
    assert v.selection_roots() == [a]
    # from the empty page too
    v.select_units([], reveal=False)
    _press(v, _at(v, 290, 360), both)
    _move(v, _at(v, 330, 320), both)
    _move(v, _at(v, 350, 310), both)
    _release(v, _at(v, 350, 310), both)
    assert v.selection_roots() == [a]


# ================================================================== the right-click menu
def test_the_right_click_menu_has_select_same(about):
    from ctgedit.ui.window import context_menu as CM

    w, _v = about, about.view
    i = _rect(w, 300, 300, 340, 330)
    w._set_selection([i])
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [i])
    sub = next((m for m in menu.findChildren(QtWidgets.QMenu) if m.title() == "Select Same"), None)
    assert sub is not None
    assert sub.actions() == w.select_same_actions  # the Edit menu's own commands
    # nothing selected: no Select Same
    w._set_selection([])
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [])
    assert "Select Same" not in [m.title() for m in menu.findChildren(QtWidgets.QMenu)]


# ================================================================== the keys' tool tips
def test_the_smart_guides_tool_tip_names_the_free_move_key(about):
    from ctgedit.ui.prefs.settings import app_settings

    w = about
    assert "hold Alt to drag freely" in w.act_smart_guides.toolTip()
    app_settings().setValue("canvas/drag_copy_key", "alt")
    w.apply_select_options()
    assert "hold Alt+Shift to drag freely" in w.act_smart_guides.toolTip()
    app_settings().setValue("canvas/drag_copy_key", "ctrl")
    w.apply_select_options()
    assert "hold Alt to drag freely" in w.act_smart_guides.toolTip()


# ================================================================== Tab with nothing to select
def test_tab_moves_on_when_there_is_nothing_to_select(about):
    w, v = about, about.view
    i = _rect(w, 300, 300, 340, 330)
    w.activateWindow()
    v.setFocus(QtCore.Qt.OtherFocusReason)
    _pump(2)
    if not v.hasFocus():
        pytest.skip("no keyboard focus on this platform")
    tab = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Tab, NOMOD)
    QtWidgets.QApplication.sendEvent(v, tab)
    assert v.hasFocus() and v.selection_roots()  # (an object: Tab selects it)
    v._kind_filter = lambda _i: False  # (the Filter lets nothing through)
    try:
        v.select_units([], reveal=False)
        tab = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Tab, NOMOD)
        QtWidgets.QApplication.sendEvent(v, tab)
        _pump(1)
        assert not v.hasFocus() and v.selection_roots() == []
    finally:
        v._kind_filter = None
    assert i is not None
