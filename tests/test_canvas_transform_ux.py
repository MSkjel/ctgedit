"""Transforming on the canvas."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.format import geometry as G  # noqa: E402
from qtkit import app as _app

#: the small page and the page of the valve genie
SMALL_PAGE = "FxThumb"
GENIE_PAGE = "FxParts"
VALVE = "FxValveLeft1"

CTRL = QtCore.Qt.ControlModifier
SHIFT = QtCore.Qt.ShiftModifier
ALT = QtCore.Qt.AltModifier
NOMOD = QtCore.Qt.NoModifier
LEFT = QtCore.Qt.LeftButton
NOBUTTON = QtCore.Qt.NoButton
#: the synthetic objects lie this far below the page, where nothing else is
OY = 2000


def _pump(n=3):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _open(path):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(path)
    w.resize(1400, 1000)
    w.show()
    _pump()
    return w


def _close(w):
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.editor import canvas_look as C_look
    from ctgedit.ui.main_window import MainWindow

    for e in getattr(w, "_docs", []):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()
    C_look.set_modifier_scheme("builder", "ctrl")


@pytest.fixture()
def small(fx_page):
    w = _open(fx_page(SMALL_PAGE))
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    yield w
    _close(w)


@pytest.fixture()
def genie_page(fx_page):
    w = _open(fx_page(GENIE_PAGE))
    w.view.set_snap(False)
    w.view.set_smart_guides(False)
    yield w
    _close(w)


def _me(view, kind, vp, buttons=NOBUTTON, mods=NOMOD):
    vp = QtCore.QPoint(vp)
    g = view.viewport().mapToGlobal(vp)
    return QtGui.QMouseEvent(kind, QtCore.QPointF(vp), QtCore.QPointF(g), LEFT, buttons, mods)


def _press(view, vp, mods=NOMOD):
    view.mousePressEvent(_me(view, QtCore.QEvent.MouseButtonPress, vp, LEFT, mods))


def _move(view, vp, mods=NOMOD):
    view.mouseMoveEvent(_me(view, QtCore.QEvent.MouseMove, vp, LEFT, mods))


def _release(view, vp, mods=NOMOD):
    view.mouseReleaseEvent(_me(view, QtCore.QEvent.MouseButtonRelease, vp, NOBUTTON, mods))


def _drag(view, a, b, mods=NOMOD, steps=4, release_mods=None):
    a, b = QtCore.QPoint(a), QtCore.QPoint(b)
    _press(view, a, mods)
    for k in range(1, steps + 1):
        _move(view, a + (b - a) * (k / steps), mods)
    _release(view, b, mods if release_mods is None else release_mods)
    _pump(2)


def _sp(view, x, y):
    """The viewport point of page point (x, y)."""
    return view.mapFromScene(QtCore.QPointF(x, y))


def _rect(win, l, t, r, b, colour=0x3366CC):
    i = win.doc.create_object("rectangle", (l, t + OY, r, b + OY), None, colour=colour)
    win._refresh()
    win.view.set_zoom(1.0)
    win.view.centerOn(QtCore.QPointF(420, OY + 320))
    _pump(1)
    return i


def _box(po):
    return G.norm_rect(G.parse_geom_header(po.body_raw).rect1)


def _twin(win, tmp_path):
    """The window's page as it is now, in a document of its own: the same edits on both
    must give the same bytes."""
    from ctgedit.editing import EditableDocument

    dst = tmp_path / "twin.ctg"
    dst.write_bytes(win.doc.serialize())
    return EditableDocument.open(str(dst))


# ================================================================== the multi-selection box
def test_scaling_two_rectangles_by_the_corner_is_adjust_position_and_size(small, tmp_path):
    w, v = small, small.view
    a = _rect(w, 300, 300, 360, 340)
    b = _rect(w, 400, 320, 440, 380)
    twin = _twin(w, tmp_path)
    w._set_selection([a, b])
    v.select_units([[a], [b]], reveal=False)
    box = v._multi_box()
    assert (box.left(), box.top(), box.right(), box.bottom()) == (300, OY + 300, 440, OY + 380)
    hid, idx = v._handle_at(_sp(v, 440, OY + 380))
    assert hid == 4 and idx == -1  # the box's bottom-right handle
    got = []
    v.selectionScaled.connect(lambda *x: got.append(x))
    _drag(v, _sp(v, 440, OY + 380), _sp(v, 580, OY + 460))
    assert got == [(300, OY + 300, 2.0, 2.0)]
    objs = w.doc.parsed.page.objects
    assert _box(objs[a]) == (300, OY + 300, 420, OY + 380)
    assert _box(objs[b]) == (500, OY + 340, 580, OY + 460)
    # the same records Arrange > Adjust Position and Size writes for 200 %
    roots = twin.adjust_roots([a, b])
    assert twin.adjust_position_and_size(roots, 300, OY + 300, 2.0, 2.0)
    assert w.doc.serialize() == twin.serialize()
    # one undo step
    w.undo()
    assert _box(objs[a]) == (300, OY + 300, 360, OY + 340)


def test_the_box_scales_about_the_centre_with_shift_and_shows_the_percentages(small):
    w, v = small, small.view
    a = _rect(w, 300, 300, 340, 340)
    b = _rect(w, 380, 300, 420, 340)
    v.select_units([[a], [b]], reveal=False)
    _press(v, _sp(v, 420, OY + 320))  # the right-middle handle
    assert v._resizing == 3 and v._resize_multi
    _move(v, _sp(v, 460, OY + 320), SHIFT)
    assert v._pill == "200 × 40  (167 %, 100 %)"  # (the left edge went to 260)
    _release(v, _sp(v, 460, OY + 320), SHIFT)
    _pump(1)
    objs = w.doc.parsed.page.objects
    la, ta, ra, ba = _box(objs[a])
    lb, tb, rb, bb = _box(objs[b])
    assert abs(la - 260) <= 1 and abs(rb - 460) <= 1  # about 360, the box's middle
    assert (ta, ba, tb, bb) == (OY + 300, OY + 340, OY + 300, OY + 340)
    # off: no box, no handles for several
    v.SELECTION_BOX = False
    v.select_units([[a], [b]], reveal=False)
    assert v._multi_box() is None and v._handle_box() == (None, None)


def test_the_count_chip_and_the_box_paint(small):
    w, v = small, small.view
    a = _rect(w, 300, 300, 340, 340)
    b = _rect(w, 380, 300, 420, 340)
    v.select_units([[a], [b]], reveal=False)
    img = v.viewport().grab()  # (paints the box, handles, the chip)
    assert not img.isNull() and v._multi_box() is not None


# ================================================================== resizing keys
def test_shift_resizes_about_the_centre_ctrl_keeps_proportions(small):
    w, v = small, small.view
    i = _rect(w, 300, 300, 400, 350)
    v.select_units([[i]], reveal=False)
    objs = w.doc.parsed.page.objects
    _drag(v, _sp(v, 400, OY + 325), _sp(v, 420, OY + 325), SHIFT)
    assert _box(objs[i]) == (280, OY + 300, 420, OY + 350)  # 140 wide, centre kept
    v.select_units([[i]], reveal=False)
    # Ctrl + Shift on a corner: proportions about the centre
    _drag(v, _sp(v, 420, OY + 350), _sp(v, 455, OY + 350), CTRL | SHIFT)
    l, t, r, b = _box(objs[i])
    assert (l, r) == (245, 455) and abs((t + b) / 2 - (OY + 325)) <= 1
    assert abs((r - l) / (b - t) - 140 / 50) < 0.05
    assert v._pill is None


def test_modifier_roles_follow_the_scheme():
    from ctgedit.ui.editor import canvas as C
    from ctgedit.ui.editor import canvas_look as C_look

    R = C_look.modifier_roles
    try:
        C_look.set_modifier_scheme("builder", "ctrl")
        assert R(CTRL) == {"proportional", "constrain"}
        assert R(SHIFT) == {"centre"}
        assert R(ALT) == {"free"}
        assert R(CTRL, moving=True) == {"proportional", "copy"}
        assert R(SHIFT, moving=True) == {"centre", "constrain"}
        assert "free" in R(ALT, moving=True)
        C_look.set_modifier_scheme("builder", "alt")
        assert R(CTRL, moving=True) >= {"constrain"} and "copy" not in R(CTRL, moving=True)
        assert R(ALT, moving=True) == {"copy"}
        assert {"copy", "free"} <= R(ALT | SHIFT, moving=True)
        C_look.set_modifier_scheme("design", "ctrl")
        assert R(SHIFT) == {"proportional", "constrain"}
        assert R(ALT) == {"centre", "free"}
        assert R(CTRL, moving=True) == {"copy"}
        assert "constrain" in R(SHIFT, moving=True)
        # drawing: Shift makes the square, Alt the centre
        rect = C.CanvasView._box_rect(QtCore.QPointF(0, 0), QtCore.QPointF(40, 10), SHIFT)
        assert (rect.width(), rect.height()) == (40, 40)
        rect = C.CanvasView._box_rect(QtCore.QPointF(0, 0), QtCore.QPointF(40, 10), ALT)
        assert (rect.left(), rect.width()) == (-40, 80)
        C_look.set_modifier_scheme("nonsense", "nonsense")
        assert C_look.modifier_scheme() == "builder" and C_look.drag_copy_key() == "ctrl"
    finally:
        C_look.set_modifier_scheme("builder", "ctrl")


def test_the_design_keys_resize_proportionally_with_shift(small):
    from ctgedit.ui.editor import canvas_look as C_look

    w, v = small, small.view
    C_look.set_modifier_scheme("design", "ctrl")
    i = _rect(w, 300, 300, 400, 350)
    v.select_units([[i]], reveal=False)
    _drag(v, _sp(v, 400, OY + 350), _sp(v, 500, OY + 360), SHIFT)
    l, t, r, b = _box(w.doc.parsed.page.objects[i])
    assert (l, t) == (300, OY + 300) and (r - l, b - t) == (200, 100)


# ================================================================== a constrained move
def test_a_constrained_move_keeps_one_axis(small):
    from ctgedit.ui.editor import canvas_look as C_look

    w, v = small, small.view
    i = _rect(w, 300, 300, 340, 330)
    objs = w.doc.parsed.page.objects
    # Ctrl constrains with the Graphics Builder keys when Alt copies
    C_look.set_modifier_scheme("builder", "alt")
    v.select_units([[i]], reveal=False)
    a = _sp(v, 320, OY + 315)
    _press(v, a)
    for k in range(1, 5):
        _move(v, a + QtCore.QPoint(10 * k, int(1.5 * k)), CTRL)
    assert v._pill.endswith("(+40, 0)") and v._constrain[0] == "x"
    v.viewport().grab()  # (the dashed line paints)
    _release(v, a + QtCore.QPoint(40, 6), CTRL)
    _pump(1)
    assert _box(objs[i]) == (340, OY + 300, 380, OY + 330)
    # Shift with the default keys (Ctrl copies)
    C_look.set_modifier_scheme("builder", "ctrl")
    v.select_units([[i]], reveal=False)
    a = _sp(v, 360, OY + 315)
    _drag(v, a, a + QtCore.QPoint(5, 30), SHIFT)
    assert _box(objs[i]) == (340, OY + 330, 380, OY + 360)


# ================================================================== drag to copy
def test_ctrl_drag_copies_a_genie_like_duplicate_and_move(genie_page, tmp_path):
    w, v = genie_page, genie_page.view
    twin = _twin(w, tmp_path)
    genie = _valve(w)  # an fxparts.fx_valve_left
    o = w.doc.page.objects[genie]
    g = o.geometry
    c = QtCore.QPointF(g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0)
    v.set_zoom(1.0)
    v.centerOn(c)
    _pump(2)
    v.select_units([v._resolve_unit(genie)], reveal=False)
    w._set_selection([genie])
    n_roots = len(w.doc.page.roots())
    steps = len(w.doc._undo)
    a = v.mapFromScene(c)
    _press(v, a)
    for k in range(1, 5):
        _move(v, a + QtCore.QPoint(10 * k, 0), CTRL)
    assert v.viewport().cursor().shape() == QtCore.Qt.DragCopyCursor
    _release(v, a + QtCore.QPoint(40, 0), CTRL)
    _pump(2)
    assert len(w.doc.page.roots()) == n_roots + 1  # the original and a copy
    assert len(w.doc._undo) == steps + 1  # one undo step
    assert w.doc.page.objects[genie].geometry.x == g.x
    new = w._sel[0]
    sel = [genie]
    copy = twin.duplicate_objects(sel, offset=0)
    twin.move_objects(copy, 40, 0)
    assert copy == [new]
    assert w.doc.serialize() == twin.serialize()
    w.undo()
    assert len(w.doc.page.roots()) == n_roots


def test_a_ctrl_click_without_a_drag_still_toggles(small):
    w, v = small, small.view
    a = _rect(w, 300, 300, 340, 330)
    b = _rect(w, 400, 300, 440, 330)
    v.select_units([[a]], reveal=False)
    p = _sp(v, 420, OY + 315)
    _press(v, p, CTRL)
    _release(v, p, CTRL)
    assert v.selection_roots() == [a, b]
    n = len(w.doc.page.roots())
    _press(v, p, CTRL)
    _release(v, p, CTRL)
    assert v.selection_roots() == [a] and len(w.doc.page.roots()) == n


def _at_xy(win, i):
    g = win.doc.page.objects[i].geometry
    return g.x, g.y


def test_duplicate_repeats_the_last_offset(small):
    w, _v = small, small.view
    w._paste_offset = 10
    i = _rect(w, 300, 300, 340, 330)
    w._set_selection([i])
    w.duplicate_selected()
    first = w._sel[0]
    assert _at_xy(w, first) == (310, OY + 310)
    # the copy moved: (+110, -10) on, so 120 to the right of the original
    w._on_objects_moved(w.doc.expand_to_subtrees([first]), 110, -10)
    assert _at_xy(w, first) == (420, OY + 300)
    w._set_selection([first])
    w.duplicate_selected()
    third = w._sel[0]
    assert _at_xy(w, third) == (540, OY + 300)
    assert "(+120, 0)" in w.statusBar().currentMessage()
    # again, unmoved: the same pitch
    w.duplicate_selected()
    fourth = w._sel[0]
    assert _at_xy(w, fourth) == (660, OY + 300)
    # another edit between: the paste offset again
    w._set_selection([fourth])
    w.doc.set_line_width(fourth, 3)
    w._refresh()
    w.duplicate_selected()
    fifth = w._sel[0]
    assert _at_xy(w, fifth) == (670, OY + 310)
    # off by the setting
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("edit/smart_duplicate", False)
    w._on_objects_moved(w.doc.expand_to_subtrees([fifth]), 50, -10)
    w._set_selection([fifth])
    w.duplicate_selected()
    sixth = w._sel[0]
    assert _at_xy(w, sixth) == (730, OY + 310)
    app_settings().setValue("edit/smart_duplicate", True)


# ================================================================== the rotation grip
def _valve(win):
    """FxValveLeft1: a root fxparts.fx_valve_left genie with an extent of its own."""
    (r,) = win.doc.find_objects_by_name(VALVE)
    o = win.doc.page.objects[r]
    assert o.parent is None and "fx_valve_left" in str(o.genie_template)
    assert o.geometry.extent_x > 0 and o.geometry.extent_y > 0
    return r


def test_the_grip_turns_a_valve_like_rotate_clockwise(genie_page, tmp_path):
    w, v = genie_page, genie_page.view
    twin = _twin(w, tmp_path)
    r = _valve(w)
    g = w.doc.page.objects[r].geometry
    c = QtCore.QPointF(g.x + g.extent_x / 2.0, g.y + g.extent_y / 2.0)
    v.set_zoom(2.0)
    v.centerOn(c)
    _pump(2)
    w._set_selection([r])
    grip = v.mapFromScene(v._grip_point())
    assert v._grip_at(grip)
    centre = v.mapFromScene(c)
    steps = len(w.doc._undo)
    _press(v, grip)
    assert v.rotating()
    # a quarter turn clockwise: from above the centre to the right of it
    radius = centre.y() - grip.y()
    _move(v, centre + QtCore.QPoint(radius // 2, -radius))
    _move(v, centre + QtCore.QPoint(radius, -radius // 3))
    _move(v, centre + QtCore.QPoint(radius, 0))
    assert v._pill == "90 deg clockwise"
    v.viewport().grab()  # (the ghost paints)
    _release(v, centre + QtCore.QPoint(radius, 0))
    _pump(2)
    assert len(w.doc._undo) == steps + 1
    twin.rotate_objects([r], clockwise=True)
    assert w.doc.serialize() == twin.serialize()
    # a half turn is one step, Esc cancels
    w._set_selection([w.doc.root_of(r)])
    w._rotate_steps(2)
    assert len(w.doc._undo) == steps + 2
    twin.rotate_objects([r], clockwise=True)
    twin.rotate_objects([r], clockwise=True)
    assert w.doc.serialize() == twin.serialize()
    grip = v.mapFromScene(v._grip_point())
    _press(v, grip)
    _move(v, centre + QtCore.QPoint(-radius, 0))
    assert v._pill == "90 deg anticlockwise"
    v.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, NOMOD))
    _release(v, centre + QtCore.QPoint(-radius, 0))
    assert len(w.doc._undo) == steps + 2 and not v.rotating()


def test_just_outside_a_corner_the_pointer_turns_and_the_menu_has_rotate_and_flip(small):
    from ctgedit.ui.window import context_menu as CM

    w, v = small, small.view
    i = _rect(w, 300, 300, 400, 380)
    w._set_selection([i])
    corner = _sp(v, 400, OY + 380)
    assert v._handle_at(corner)[0] == 4  # the handle itself
    assert v._corner_zone(corner + QtCore.QPoint(12, 12))
    assert not v._corner_zone(corner + QtCore.QPoint(60, 60))
    assert not v._corner_zone(corner + QtCore.QPoint(4, 4))  # (the handle's own)
    # another object there: a press selects it, no turn
    other = _rect(w, 410, 390, 440, 420)
    w._set_selection([i])
    corner = _sp(v, 400, OY + 380)
    assert not v._corner_zone(corner + QtCore.QPoint(12, 12))
    w.doc.delete_objects([other])
    w._refresh()
    w._set_selection([i])
    v.mouseMoveEvent(_me(v, QtCore.QEvent.MouseMove, corner + QtCore.QPoint(12, 12)))
    assert v.viewport().cursor().shape() == QtCore.Qt.BitmapCursor
    menu = CM.build_canvas_menu(w, QtCore.QPoint(-50, -50), [i])
    assert "Rotate and Flip" in [a.text() for a in menu.actions()]
    sub = next(m for m in menu.findChildren(QtWidgets.QMenu) if m.title() == "Rotate and Flip")
    assert [a for a in sub.actions()] == [w.act_rot_cw, w.act_rot_ccw, w.act_flip_h, w.act_flip_v]
