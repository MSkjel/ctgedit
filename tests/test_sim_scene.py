"""The simulator's object states drawn over the static page."""

import gc
import os
import time

import pytest
from qtkit import app as _app, canvas_picture

W, H = 400, 300
BG = 0xFFFFFF


def _obj(kind, an, x, y, w, h, tc=None, **style):
    from ctgedit.model.document import Geometry, GraphicObject, ObjectKind

    o = GraphicObject(kind=kind, an=an, geometry=Geometry(x=x, y=y, extent_x=w, extent_y=h))
    for k, v in style.items():
        setattr(o.style, k, v)
    o.raw["_type_code"] = (
        tc
        if tc is not None
        else {
            ObjectKind.RECTANGLE: 0x0F,
            ObjectKind.TEXT: 0x05,
            ObjectKind.GROUP: 0x08,
            ObjectKind.ELLIPSE: 0x11,
            ObjectKind.POLYGON: 0x10,
            ObjectKind.LINE: 0x01,
            ObjectKind.PIPE: 0x0B,
            ObjectKind.BUTTON: 0x1001,
        }.get(kind, 0)
    )
    return o


def _page(objects, bg=BG):
    from ctgedit.model.document import Page

    p = Page(name="sim_test")
    p.appearance.width, p.appearance.height = W, H
    p.appearance.background_colour = bg
    p.objects = objects
    return p


class _Rig:
    """A scene + a 1:1 view of exactly the page."""

    def __init__(self, page):
        from PySide6 import QtCore, QtWidgets
        from ctgedit.render.scene import PageScene

        self.app = _app()
        self.page = page
        self.scene = PageScene()
        self.scene.set_page(page)
        v = QtWidgets.QGraphicsView(self.scene)
        v.setFrameShape(QtWidgets.QFrame.NoFrame)
        v.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        v.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        v.setSceneRect(0, 0, W, H)
        v.resize(W, H)
        v.show()
        self.view = v
        self.app.processEvents()

    def grab(self):
        self.app.processEvents()
        return canvas_picture(self.view)

    def px(self, x, y, img=None):
        from PySide6 import QtCore

        img = img if img is not None else self.grab()
        p = self.view.mapFromScene(QtCore.QPointF(x, y))
        c = img.pixelColor(p.x(), p.y())
        return c.red(), c.green(), c.blue()

    def apply(self, states, **kw):
        return self.scene.apply_states(states, **kw)

    def close(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()


def _near(c, rgb, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c, rgb))


@pytest.fixture
def rig():
    from ctgedit.model.document import ObjectKind as K, TextJustify

    objs = [
        _obj(K.RECTANGLE, 1, 20, 20, 60, 40, filled=True, fill_colour=0xFF0000, line_colour=0xFF0000),
        _obj(K.GROUP, 2, 120, 20, 120, 60),
        _obj(K.RECTANGLE, 3, 120, 20, 50, 60, filled=True, fill_colour=0x0000FF, line_colour=0x0000FF),
        _obj(K.ELLIPSE, 4, 190, 20, 50, 60, filled=True, fill_colour=0x00FF00, line_colour=0x00FF00),
        _obj(
            K.TEXT,
            5,
            20,
            120,
            120,
            20,
            text="####.#",
            font="Arial",
            font_size=12,
            text_colour=0x000000,
            justify=TextJustify.RIGHT,
        ),
        _obj(K.POLYGON, 6, 200, 150, 80, 60, filled=True, fill_colour=0xFFFF00, line_colour=0xFFFF00),
        _obj(K.RECTANGLE, 7, 20, 200, 60, 40, filled=False, line_colour=0x000000, line_width=3),
        _obj(K.LINE, 8, 300, 40, 80, 0, line_colour=0x000000, line_width=5),
    ]
    objs[5].geometry.nodes = [(200, 210), (240, 150), (280, 210)]
    objs[7].geometry.nodes = [(300, 40), (380, 40)]
    objs[1].members = [2, 3]
    objs[2].parent = objs[3].parent = 1
    r = _Rig(_page(objs))
    yield r
    r.close()
    gc.collect()


# -- every field ------------------------------------------------------------------------------
def test_shown_false_hides_and_restores(rig):
    from ctgedit.sim.state import ObjectState

    assert _near(rig.px(50, 40), (255, 0, 0), 5)
    rig.apply({1: ObjectState(1, shown=False)})
    assert _near(rig.px(50, 40), (255, 255, 255), 5)
    rig.apply({1: None})
    assert _near(rig.px(50, 40), (255, 0, 0), 5)


def test_move_rotate_scale(rig):
    from ctgedit.sim.state import ObjectState

    rig.apply({1: ObjectState(1, move=(50, 60))})
    img = rig.grab()
    assert _near(rig.px(30, 30, img), (255, 255, 255), 5)  # the old place is empty
    assert _near(rig.px(110, 110, img), (255, 0, 0), 5)  # 20..80 + 50, 20..60 + 60
    # 90 degrees clockwise about its centre (50, 40): 60 x 40 -> 40 x 60
    rig.apply({1: ObjectState(1, rotate=90.0)})
    img = rig.grab()
    assert _near(rig.px(50, 15, img), (255, 0, 0), 5)  # above the old top edge
    assert _near(rig.px(25, 40, img), (255, 255, 255), 5)  # left part gone
    # double width about the left edge
    rig.apply({1: ObjectState(1, scale=(2.0, 1.0), scale_origin=(20, 40))})
    img = rig.grab()
    assert _near(rig.px(110, 40, img), (255, 0, 0), 5)  # 20..80 -> 20..140
    assert _near(rig.px(15, 40, img), (255, 255, 255), 5)
    # default origin = the centre (50, 40): half size keeps the centre
    rig.apply({1: ObjectState(1, scale=(0.5, 0.5))})
    img = rig.grab()
    assert _near(rig.px(50, 40, img), (255, 0, 0), 5)
    assert _near(rig.px(25, 25, img), (255, 255, 255), 5)


def test_rotation_turns_clockwise_about_the_given_origin(rig):
    from ctgedit.sim.state import ObjectState

    # about its top-left corner (20, 20): 90 degrees clockwise swings it to x 20 - 40 .. 20
    rig.apply({1: ObjectState(1, rotate=90.0, rotate_origin=(20, 20))})
    img = rig.grab()
    assert _near(rig.px(5, 50, img), (255, 0, 0), 5)
    assert _near(rig.px(50, 40, img), (255, 255, 255), 5)


def test_fill_colour_per_class(rig):
    from ctgedit.sim.state import ObjectState

    rig.apply(
        {
            1: ObjectState(1, fill_colour=0x00FF00),  # filled: the fill
            7: ObjectState(7, fill_colour=0xFF00FF),  # hollow: the line
            8: ObjectState(8, fill_colour=0x0000FF),  # line: the pen
            5: ObjectState(5, fill_colour=0xFF0000, text="88888888"),
        }
    )
    img = rig.grab()
    assert _near(rig.px(50, 40, img), (0, 255, 0), 5)
    assert _near(rig.px(20, 220, img), (255, 0, 255), 60)  # the hollow rect's edge
    assert _near(rig.px(50, 220, img), (255, 255, 255), 5)  # its inside stays empty
    assert _near(rig.px(340, 40, img), (0, 0, 255), 30)
    item = rig.scene._items[4]
    assert item.brush().color().red() == 255 and item.brush().color().green() == 0
    # transparent = no fill
    rig.apply({1: ObjectState(1, fill_colour=0xFF000000)})
    assert _near(rig.px(50, 40), (255, 255, 255), 5)
    rig.scene.clear_states()
    img = rig.grab()
    assert _near(rig.px(50, 40, img), (255, 0, 0), 5)
    assert _near(rig.px(340, 40, img), (0, 0, 0), 30)


def test_fill_level_directions_and_background(rig):
    from ctgedit.sim.state import FILL_DOWN, FILL_LEFT, FILL_RIGHT, FILL_UP, ObjectState

    # rect 1 = x 20..80, y 20..60
    cases = [
        (FILL_UP, (50, 52), (50, 28)),
        (FILL_DOWN, (50, 28), (50, 52)),
        (FILL_LEFT, (72, 40), (28, 40)),
        (FILL_RIGHT, (28, 40), (72, 40)),
    ]
    for d, filled, empty in cases:
        rig.apply({1: ObjectState(1, fill_level=0.5, fill_level_direction=d, fill_level_colour=0x0000FF)})
        img = rig.grab()
        assert _near(rig.px(*filled, img), (255, 0, 0), 5), d
        assert _near(rig.px(*empty, img), (0, 0, 255), 5), d
    # a transparent background leaves the unfilled part empty; an animated colour fills
    rig.apply({1: ObjectState(1, fill_level=0.25, fill_level_colour=0xFF000000, fill_colour=0x00FF00)})
    img = rig.grab()
    assert _near(rig.px(50, 57, img), (0, 255, 0), 5)
    assert _near(rig.px(50, 30, img), (255, 255, 255), 5)
    rig.apply({1: None})
    assert _near(rig.px(50, 30), (255, 0, 0), 5)
    assert not rig.scene._items[0].childItems()  # the overlays are gone


def test_disabled_styles(rig):
    from ctgedit.sim.state import (
        DISABLE_GRAY_ALL,
        DISABLE_GRAY_PART,
        DISABLE_GRAY_SUNK,
        DISABLE_HIDE,
        DISABLE_UNCHANGED,
        ObjectState,
    )

    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_PART)})
    r, g, b = rig.px(50, 40)
    assert r == g == b and r < 130  # grey levels: red -> dark grey
    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_SUNK)})
    r2, g2, b2 = rig.px(50, 40)
    assert r2 == g2 == b2 and r2 > r  # embossed: a lighter grey
    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_ALL)})
    assert rig.px(50, 40) == (r2, g2, b2)  # GRAY_ALL draws embossed too
    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_HIDE)})
    assert _near(rig.px(50, 40), (255, 255, 255), 5)
    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_UNCHANGED)})
    assert _near(rig.px(50, 40), (255, 0, 0), 5)
    rig.apply({1: None})
    assert rig.scene._items[0].graphicsEffect() is None


def test_embossed_has_a_white_copy_down_right():
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import DISABLE_GRAY_SUNK, ObjectState

    r = _Rig(
        _page(
            [_obj(K.RECTANGLE, 1, 20, 20, 60, 40, filled=True, fill_colour=0xFF0000, line_colour=0xFF0000)],
            bg=0x202020,
        )
    )
    try:
        r.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_SUNK)})
        img = r.grab()
        right, bottom = (
            r.scene._items[0].sceneBoundingRect().right(),
            r.scene._items[0].sceneBoundingRect().bottom(),
        )
        assert r.px(int(right), 40, img) == (255, 255, 255)
        assert r.px(50, int(bottom), img) == (255, 255, 255)
    finally:
        r.close()


def test_text_value_error_and_justify(rig):
    from ctgedit.sim.state import ObjectState

    item = rig.scene._items[4]
    right0 = item.sceneBoundingRect().right()
    rig.apply({5: ObjectState(5, text="1.5")})
    assert item.text() == "1.5"
    assert abs(item.sceneBoundingRect().right() - right0) <= 1  # right justified
    rig.apply({5: ObjectState(5, text="1.5", error="#COM")})
    assert item.text() == "#COM"
    rig.apply({5: None})
    assert item.text() == "####.#"
    assert abs(item.sceneBoundingRect().right() - right0) <= 1


def test_vertex_offsets_move_a_polygons_vertices(rig):
    from ctgedit.sim.state import ObjectState

    item = rig.scene._items[5]
    base = [(item.polygon()[k].x(), item.polygon()[k].y()) for k in range(3)]
    assert _near(rig.px(240, 140), (255, 255, 255), 5)
    rig.apply({6: ObjectState(6, vertex_offsets=[(0, 0), (0, -30), (0, 0)])})
    p = item.polygon()
    assert (p[1].x(), p[1].y()) == (base[1][0], base[1][1] - 30)
    assert _near(rig.px(240, 140), (255, 255, 0), 5)
    rig.apply({6: None})
    assert [(item.polygon()[k].x(), item.polygon()[k].y()) for k in range(3)] == base


def test_tooltip(rig):
    from ctgedit.sim.state import ObjectState

    item = rig.scene._items[0]
    base = item.toolTip()
    rig.apply({1: ObjectState(1, tooltip="Pump running")})
    assert item.toolTip() == "Pump running"
    rig.apply({2: ObjectState(2, tooltip="Group tip")})
    assert rig.scene._items[2].toolTip() == "Group tip"  # members inherit
    rig.apply({1: None, 2: None})
    assert item.toolTip() == base


def test_button_face_and_pipe_colour():
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import ObjectState

    pipe = _obj(
        K.PIPE,
        2,
        40,
        150,
        200,
        0,
        line_width=9,
        hilight_colour=0x808080,
        lolight_colour=0x404040,
        line_colour=0x808080,
    )
    pipe.geometry.nodes = [(40, 150), (240, 150)]
    btn = _obj(K.BUTTON, 1, 40, 40, 100, 40)
    btn.raw["_button_mode"] = 0
    r = _Rig(_page([btn, pipe]))
    try:
        r.apply({1: ObjectState(1, fill_colour=0x00FF00), 2: ObjectState(2, fill_colour=0xFF0000)})
        img = r.grab()
        assert _near(r.px(90, 60, img), (0, 255, 0), 5)
        cr, cg, cb = r.px(140, 150, img)
        assert cr > 120 and cg < 40 and cb < 40  # a red tube
        r.scene.clear_states()
        img = r.grab()
        assert not _near(r.px(90, 60, img), (0, 255, 0), 60)
        cr, cg, cb = r.px(140, 150, img)
        assert abs(cr - cg) < 10 and abs(cg - cb) < 10  # grey again
    finally:
        r.close()


# -- subtree semantics ----------------------------------------------------------------------
def test_group_state_applies_to_its_members(rig):
    from ctgedit.sim.state import DISABLE_GRAY_PART, ObjectState

    rig.apply({2: ObjectState(2, shown=False)})
    img = rig.grab()
    assert _near(rig.px(145, 50, img), (255, 255, 255), 5)
    assert _near(rig.px(215, 50, img), (255, 255, 255), 5)
    rig.apply({2: ObjectState(2, move=(0, 100))})
    img = rig.grab()
    assert _near(rig.px(145, 150, img), (0, 0, 255), 5)
    assert _near(rig.px(215, 150, img), (0, 255, 0), 5)
    # the member's own move runs inside the group's: both offsets add up
    rig.apply({3: ObjectState(3, move=(0, 20))})
    assert _near(rig.px(145, 175), (0, 0, 255), 5)
    # the group's colour reaches members without one of their own; the member's own wins
    rig.apply({2: ObjectState(2, fill_colour=0xFF00FF), 3: ObjectState(3, fill_colour=0x00FFFF)})
    img = rig.grab()
    assert _near(rig.px(145, 50, img), (0, 255, 255), 5)
    assert _near(rig.px(215, 50, img), (255, 0, 255), 5)
    # a disabled group greys every member
    rig.apply({2: ObjectState(2, disabled=True, disable_style=DISABLE_GRAY_PART), 3: None})
    for x in (145, 215):
        cr, cg, cb = rig.px(x, 50)
        assert cr == cg == cb
    rig.scene.clear_states()
    img = rig.grab()
    assert _near(rig.px(145, 50, img), (0, 0, 255), 5)
    assert _near(rig.px(215, 50, img), (0, 255, 0), 5)


def test_group_fill_level_reveals_its_members(rig):
    from ctgedit.sim.state import ObjectState

    # group = y 20..80: half from the bottom shows y 50..80
    rig.apply({2: ObjectState(2, fill_level=0.5)})
    img = rig.grab()
    assert _near(rig.px(145, 30, img), (255, 255, 255), 5)
    assert _near(rig.px(145, 70, img), (0, 0, 255), 5)
    # an opaque background covers the hidden part
    rig.apply({2: ObjectState(2, fill_level=0.5, fill_level_colour=0x808080)})
    img = rig.grab()
    assert _near(rig.px(145, 30, img), (128, 128, 128), 5)
    assert _near(rig.px(145, 70, img), (0, 0, 255), 5)
    rig.apply({2: None})
    img = rig.grab()
    assert _near(rig.px(145, 30, img), (0, 0, 255), 5)


def test_restore_to_static_is_pixel_identical(rig):
    from ctgedit.sim.state import DISABLE_GRAY_SUNK, ObjectState

    static = rig.grab()
    rig.apply(
        {
            1: ObjectState(1, move=(5, 5), rotate=10.0, fill_colour=0x00FF00, tooltip="x", fill_level=0.3),
            2: ObjectState(2, scale=(1.2, 0.8), disabled=True, disable_style=DISABLE_GRAY_SUNK),
            5: ObjectState(5, text="12345", fill_colour=0x00FF00),
            6: ObjectState(6, vertex_offsets=[(3, 3), (0, -10), (0, 0)]),
            7: ObjectState(7, fill_colour=0x00FF00),
            8: ObjectState(8, move=(0, 9)),
        }
    )
    assert rig.grab() != static
    rig.scene.clear_states()
    assert rig.grab() == static
    assert not rig.scene.sim_layer()._applied
    # also field by field back to None
    rig.apply({1: ObjectState(1, move=(5, 5), fill_colour=0x00FF00)})
    rig.apply({1: ObjectState(1, move=(5, 5))})
    rig.apply({1: ObjectState(1)})
    assert rig.grab() == static


def test_unchanged_states_touch_nothing(rig):
    from ctgedit.sim.state import ObjectState

    st = {1: ObjectState(1, move=(5, 5)), 5: ObjectState(5, text="1")}
    assert rig.apply(st)["touched"] == 2
    assert rig.apply(st)["touched"] == 0
    # the animator may reuse and change its own objects: the layer kept a copy
    st[1].move = (6, 6)
    assert rig.apply(st)["touched"] == 1
    assert rig.apply({99: ObjectState(99, shown=False)})["unmapped"] == 1


def test_replace_resets_the_others(rig):
    from ctgedit.sim.state import ObjectState

    rig.apply({1: ObjectState(1, shown=False), 7: ObjectState(7, shown=False)})
    rig.apply({7: ObjectState(7, shown=False)}, replace=True)
    assert set(rig.scene.sim_states()) == {7}
    assert _near(rig.px(50, 40), (255, 0, 0), 5)


def test_exports_stay_static(rig):
    from PySide6 import QtCore, QtGui
    from ctgedit.sim.state import ObjectState

    def export():
        img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
        img.fill(0)
        p = QtGui.QPainter(img)
        with rig.scene.export_only(None):
            rig.scene.render(p, QtCore.QRectF(0, 0, W, H), QtCore.QRectF(0, 0, W, H))
        p.end()
        return img

    static = export()
    rig.apply(
        {
            1: ObjectState(1, move=(40, 40), fill_colour=0x00FF00),
            2: ObjectState(2, shown=False),
            5: ObjectState(5, text="99"),
        }
    )
    live = rig.grab()
    assert export() == static
    assert rig.grab() == live  # the states are back
    assert rig.scene._items[4].text() == "99"


def test_flashing_bitmap_follows_the_flash_phase():
    from PySide6 import QtCore, QtGui
    from ctgedit.model.document import ObjectKind as K

    bmp = _obj(K.SYMBOL, 3, 40, 40, 40, 40, tc=0x07)
    bmp.raw["_embedded_bmp"] = (4, 4, bytes([255, 0, 0, 255]) * 16, True)
    bmp.raw["_embedded_bmp_flash"] = (4, 4, bytes([0, 0, 255, 255]) * 16, True)
    r = _Rig(_page([bmp]))
    try:
        assert _near(r.px(60, 60), (255, 0, 0), 5)
        assert r.scene.set_flash_phase(False) == 1
        assert _near(r.px(60, 60), (0, 0, 255), 5)
        # an export draws the static picture
        img = QtGui.QImage(W, H, QtGui.QImage.Format_ARGB32)
        p = QtGui.QPainter(img)
        with r.scene.export_only(None):
            r.scene.render(p, QtCore.QRectF(0, 0, W, H), QtCore.QRectF(0, 0, W, H))
        p.end()
        c = img.pixelColor(60, 60)
        assert (c.red(), c.blue()) == (255, 0)
        assert _near(r.px(60, 60), (0, 0, 255), 5)  # off phase again after it
        r.scene.set_flash_phase(True)
        assert _near(r.px(60, 60), (255, 0, 0), 5)
    finally:
        r.close()


def test_runtime_look_hides_editor_marks():
    from ctgedit.model.document import ObjectKind as K

    pt = _obj(K.UNKNOWN, 9, 100, 100, 1, 1, tc=0x1000)
    pt.raw["_non_drawable"] = True
    pt.raw["_anchor"] = (100, 100)
    r = _Rig(_page([pt]))
    try:
        marker = r.scene._items[0]
        assert marker.isVisible()
        r.scene.set_runtime_look(True)
        assert not marker.isVisible()
        r.scene.set_runtime_look(False)
        assert marker.isVisible()
    finally:
        r.close()


def test_runtime_look_hides_a_cicode_objects_symbol():
    """A Cicode object (tc 0x1009) shows its SYMBOL in the editor only: CtDraw's .ctF keeps it
    as a bare point and the runtime draws nothing there (the warning triangle over row 1 of
    the Tab_Style alarm tables)."""
    from ctgedit.model.document import ObjectKind as K

    obj = _obj(
        K.RECTANGLE, 9, 100, 100, 16, 16, tc=0x1009, filled=True, fill_colour=0xFF0000, line_colour=0xFF0000
    )
    r = _Rig(_page([obj]))
    try:
        assert _near(r.px(108, 108), (255, 0, 0), 5)
        r.scene.set_runtime_look(True)
        assert _near(r.px(108, 108), (255, 255, 255), 5)
        r.scene.set_runtime_look(False)
        assert _near(r.px(108, 108), (255, 0, 0), 5)
    finally:
        r.close()


def test_symbol_cicode_objects_hidden_at_run_time(fx_page):
    """FxCicode's Cicode objects with a symbol (FxCicodePage: fxicons16.page,
    FxCicodePriority: fxprio.fx_p1; an alarm table's Cicode objects are of this kind) draw
    in the editor, not in the runtime look."""
    _app()
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate.window import runtime_page

    page = runtime_page(fx_page("FxCicode"))
    sc = PageScene()
    sc.set_page(page)
    idx = [
        i
        for i, o in enumerate(page.objects)
        if o.raw.get("_type_code") == 0x1009
        and (o.raw.get("_props") or {}).get("SYMBOL") in ("fxicons16.page", "fxprio.fx_p1")
        and i in sc._items
    ]
    assert len(idx) == 2
    assert all(sc._items[i].isVisible() for i in idx)
    sc.set_runtime_look(True)
    assert not any(sc._items[i].isVisible() for i in idx)
    sc.set_runtime_look(False)
    assert all(sc._items[i].isVisible() for i in idx)


# -- real pages -----------------------------------------------------------------------------
def test_symbol_set_state_index_and_element_swap(fx_page):
    """FxSymbols' FxOnOffSet: a symbol set of two linked states (fxsym.fx_off, fx_on)."""
    from ctgedit.io.reader import read_page
    from ctgedit.sim.state import ObjectState

    page = read_page(fx_page("FxSymbols"))
    r = _Rig(page)
    try:
        sc = r.scene
        # a symbol set of two states: the first drawn, the second not built at all
        (k,) = [
            i
            for i, o in enumerate(page.objects)
            if (o.raw.get("_props") or {}).get("__AN_NAME__") == "FxOnOffSet"
        ]
        o = page.objects[k]
        assert o.raw.get("_type_code") == 0x0C and len(o.members) == 2
        assert o.members[0] in sc._items and o.members[1] not in sc._items
        s0, s1 = page.objects[k].members
        an = page.objects[k].an
        layer = sc.sim_layer()
        r.apply({an: ObjectState(an, symbol_state=1)})
        swap = layer._swap_active[k]
        assert swap.isVisible() and not sc._items[s0].isVisible()
        box = sc._items[s0].sceneBoundingRect()
        assert swap.sceneBoundingRect().intersects(box)
        r.apply({an: ObjectState(an, symbol_state=0)})
        assert layer._swap_active[k] is not swap and layer._swap_active[k].isVisible()
        assert not swap.isVisible()
        r.apply({an: ObjectState(an, symbol_state=5)})  # no such state: nothing drawn
        assert k not in layer._swap_active and not sc._items[s0].isVisible()
        # a 'lib.element' through the page's include chain (Include's lights.ctl)
        r.apply({an: ObjectState(an, symbol="lights.light_1_red")})
        art = layer._swap_active[k]
        assert art.isVisible() and art.childItems()
        r.apply({an: ObjectState(an, symbol="nolibrary.nothing")})
        assert "nolibrary.nothing" in layer.missing_symbols
        r.apply({an: None})
        assert sc._items[s0].isVisible() and not art.isVisible()
    finally:
        r.close()
        gc.collect()


def _composite(page, name):
    """The root of the composite genie ``name`` (its page record's name)."""
    roots = [
        i
        for i, o in enumerate(page.objects)
        if o.kind.value == "composite_genie"
        and o.children
        and (o.raw.get("_props") or {}).get("__AN_NAME__") == name
    ]
    assert len(roots) == 1, (name, roots)
    return roots[0]


@pytest.mark.usefixtures("composite_cache")
def test_composite_genie_members_draw_their_states_in_the_genies_picture(fx_page):
    """A composite genie draws its cache element as one picture; its page members (the records
    the compiler animates) have no items. Their states go to the picture's items of the cache
    records they link to (by handle), inside the genie's frame. FxComposites' FxComp3 (of
    fx_drive_c): five of its members draw in the picture."""
    from ctgedit.io.reader import read_page
    from ctgedit.sim.state import ObjectState

    page = read_page(fx_page("FxComposites"))
    r = _Rig(page)
    try:
        sc, layer = r.scene, r.scene.sim_layer()
        root = _composite(page, "FxComp3")
        parts = layer._composite_parts(root)
        drawn = {j: p for j, p in parts.items() if p[1] is not None}
        assert len(drawn) == 5
        from PySide6 import QtWidgets

        j = next(
            k
            for k, p in drawn.items()
            if isinstance(p[1], QtWidgets.QAbstractGraphicsShapeItem)
            and page.objects[k].kind.value in ("rectangle", "polygon", "ellipse")
        )
        item = drawn[j][1]
        an, ran = page.objects[j].an, page.objects[root].an
        b0 = item.sceneBoundingRect()
        rb0 = sc._items[root].sceneBoundingRect()
        res = r.apply({an: ObjectState(an, move=(5, 0), fill_colour=0xFF00FF)})
        assert res["undrawn"] == 0 and res["touched"] == 1
        assert abs(item.sceneBoundingRect().left() - b0.left() - 5) < 0.01
        ch = layer._channel(drawn[j][0], item)
        if ch == "fill":
            assert item.brush().color().name() == "#ff00ff"
        elif ch == "pen":
            assert item.pen().color().name() == "#ff00ff"
        # the genie's own move carries the member's: 100 + 5
        r.apply({ran: ObjectState(ran, move=(100, 0))})
        assert abs(sc._items[root].sceneBoundingRect().left() - rb0.left() - 100) < 0.01
        assert abs(item.sceneBoundingRect().left() - b0.left() - 105) < 0.01
        # hiding the member hides its item only
        r.apply({an: ObjectState(an, shown=False)})
        assert not item.isVisible() and sc._items[root].isVisible()
        sc.clear_states()
        assert abs(item.sceneBoundingRect().left() - b0.left()) < 0.01 and item.isVisible()
    finally:
        r.close()
        gc.collect()


@pytest.mark.usefixtures("composite_cache")
def test_a_composite_genies_symbol_set_and_alarm_frames(fx_page):
    """A symbol set inside a composite genie shows the state asked for, inside the picture,
    and clear_states hides the swap again; the runtime look hides the alarm indicator frames
    (no alarm state yet). FxComposites' FxComp9 (FxStatusC: fxparts.fx_sets, two symbol
    sets on its Tag, and fxparts.fx_alarm, an alarm indicator group that is on)."""
    from ctgedit.io.reader import read_page
    from ctgedit.sim.state import ObjectState

    page = read_page(fx_page("FxComposites"))
    r = _Rig(page)
    try:
        sc, layer = r.scene, r.scene.sim_layer()
        root = next(
            i
            for i, o in enumerate(page.objects)
            if o.kind.value == "composite_genie"
            and o.children
            and any(page.objects[k].raw.get("_type_code") == 0x0C for k in page.subtree(i)[1:])
        )
        k = next(
            i
            for i in page.subtree(root)[1:]
            if page.objects[i].raw.get("_type_code") == 0x0C and page.objects[i].members
        )
        san = page.objects[k].an
        r.apply({san: ObjectState(san, symbol_state=1)})
        swap = layer._swap_active[k]
        assert swap.isVisible()
        assert sc._items[root].sceneBoundingRect().intersects(swap.sceneBoundingRect())
        sc.clear_states()
        assert not swap.isVisible()
        assert sc._ai_items and all(it.isVisible() for it in sc._ai_items.values())
        sc.set_runtime_look(True)
        assert not any(it.isVisible() for it in sc._ai_items.values())
        sc.set_runtime_look(False)
        assert all(it.isVisible() for it in sc._ai_items.values())
    finally:
        r.close()
        gc.collect()


@pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="heavy")
def test_scan_timing_on_a_big_page(fx_page):
    """500 changing states per scan on a 5000-object page (FxPerf's FxHuge: rectangles and
    texts; tools/sim_scene_bench.py prints the numbers)."""
    _app()
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sim_scene_bench", os.path.join(os.path.dirname(__file__), "..", "tools", "sim_scene_bench.py")
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    page = read_page(fx_page("FxHuge", "FxPerf"))
    assert len(page.objects) == 5000
    sc = PageScene()
    sc.set_page(page)
    picked = bench.pick(page, sc, 500)
    assert len(picked) == 500
    sc.apply_states(bench.states_for(picked, 0))
    times = []
    for scan in range(1, 8):
        st = bench.states_for(picked, scan)
        t = time.perf_counter()
        res = sc.apply_states(st)
        times.append((time.perf_counter() - t) * 1000)
        assert res["changed"] >= 490  # (a rounded value can repeat between scans)
    assert sum(times) / len(times) < 60, times
    t = time.perf_counter()
    sc.clear_states()
    assert (time.perf_counter() - t) * 1000 < 60
    del sc
    gc.collect()


# -- robustness ---------------------------------------------------------------------------------
def test_nan_and_infinite_numbers_are_not_animated(rig):
    """A NaN never equals itself, so a NaN state must not redraw the record every scan, and a
    NaN / huge transform must not reach Qt (it allocates an effect picture it cannot)."""
    from ctgedit.sim.state import DISABLE_GRAY_PART, ObjectState

    nan, inf = float("nan"), float("inf")
    static = rig.grab()
    st = {
        1: ObjectState(
            1,
            rotate=nan,
            move=(inf, 3.0),
            scale=(nan, 1.0),
            disabled=True,
            disable_style=DISABLE_GRAY_PART,
            scale_origin=(nan, 0.0),
        ),
        6: ObjectState(6, vertex_offsets=[(nan, 1.0), (0.0, inf)]),
    }
    assert rig.apply(st)["touched"] >= 2
    assert rig.apply(st)["touched"] == 0  # equal again: nothing redrawn
    look = rig.scene.sim_layer().look_of(0)
    assert look.matrix is None and look.gray == "part"  # the NaN parts are not animated
    rig.grab()
    rig.scene.clear_states()
    assert rig.grab() == static


def test_a_zero_size_factor_collapses_the_record(rig):
    """A size factor of 0 (the bottom of the default 0 .. 100 % range) draws nothing; the item
    never gets a singular transform."""
    from ctgedit.sim.state import DISABLE_GRAY_SUNK, ObjectState

    static = rig.grab()
    rig.apply(
        {
            2: ObjectState(2, scale=(0.0, 1.0), disabled=True, disable_style=DISABLE_GRAY_SUNK),
            1: ObjectState(1, scale=(1.0, 0.0)),
        }
    )
    img = rig.grab()
    for i in (0, 2, 3):
        it = rig.scene._items[i]
        assert not it.isVisible() and abs(it.transform().determinant()) > 1e-9
    assert _near(rig.px(50, 40, img), (255, 255, 255), 5)
    rig.apply({2: ObjectState(2, scale=(0.5, 1.0)), 1: None})
    assert rig.scene._items[2].isVisible()
    rig.scene.clear_states()
    assert rig.grab() == static


def test_fill_levels_and_button_faces_add_no_children_per_scan():
    """A fill level is the shape's own brush, a button's coloured face ONE child made once."""
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import FILL_LEFT, ObjectState

    rect = _obj(K.RECTANGLE, 1, 20, 20, 60, 40, filled=True, fill_colour=0xFF0000, line_colour=0xFF0000)
    btn = _obj(K.BUTTON, 2, 120, 40, 100, 40)
    btn.raw["_button_mode"] = 0
    target = _obj(K.BUTTON, 3, 120, 120, 100, 40)
    target.raw["_button_mode"] = 3
    r = _Rig(_page([rect, btn, target]))
    try:
        sc = r.scene
        kids = [len(sc._items[k].childItems()) for k in range(3)]
        for n in range(6):
            r.apply(
                {
                    1: ObjectState(
                        1,
                        fill_level=0.25 + 0.1 * n,
                        fill_level_direction=FILL_LEFT,
                        fill_level_colour=0x0000FF,
                    ),
                    2: ObjectState(2, fill_colour=0x00FF00) if n % 2 else None,
                    3: ObjectState(3, fill_colour=0x00FF00),
                }
            )
            img = r.grab()
            assert len(sc._items[0].childItems()) == kids[0]
            assert len(sc._items[1].childItems()) <= kids[1] + 1
            assert len(sc._items[2].childItems()) == kids[2]  # a target's face is transparent
        # the last level: 0.75 from the right of 20 .. 80 (x 35 .. 80 red, 20 .. 35 blue)
        assert _near(r.px(70, 40, img), (255, 0, 0), 5)
        assert _near(r.px(25, 40, img), (0, 0, 255), 5)
        assert _near(r.px(170, 60, img), (0, 255, 0), 5)
        sc.clear_states()
        assert [len(sc._items[k].childItems()) for k in (0, 2)] == [kids[0], kids[2]]
        assert not any(
            c.isVisible()
            for c in sc._items[1].childItems()
            if isinstance(c, type(sc._items[1])) and c.brush().color().green() == 255
        )
    finally:
        r.close()


_STRESS = r"""
import os, sys, random
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
from PySide6 import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
from ctgedit.io.reader import read_page
from ctgedit.render.scene import PageScene
from ctgedit.sim.state import ObjectState
runtime = len(sys.argv) > 2 and sys.argv[2] == "runtime"
if runtime:
    # the Simulate window's page: composites drawn from their own members (the runtime's)
    from ctgedit.ui.simulate.window import runtime_page
    page = runtime_page(sys.argv[1])
else:
    page = read_page(sys.argv[1])
sc = PageScene(); sc.set_page(page)
if runtime:
    sc.set_runtime_look(True)
v = QtWidgets.QGraphicsView(sc); v.resize(900, 700); v.setSceneRect(sc.page_rect()); v.show()
app.processEvents(); static = v.viewport().grab().toImage()
objs, ans = page.objects, []
for r, o in enumerate(objs):
    if o.kind.value == "composite_genie" and (o.children or runtime):
        for j in page.subtree(r):
            if objs[j].an > 0 and objs[j].an not in ans:
                ans.append(objs[j].an)
rnd = random.Random(1)
def state(an):
    st, k = ObjectState(an), rnd.randrange(6)
    if k == 0: st.disabled, st.disable_style = True, rnd.choice((1, 2, 3))
    elif k == 1: st.scale = (rnd.choice((0.0, 0.5, 2.0)), rnd.choice((0.0, 1.0, 2.5)))
    elif k == 2: st.fill_level, st.fill_level_direction = rnd.random(), rnd.randrange(4)
    elif k == 3: st.fill_colour = rnd.choice((0xFF0000, 0xFF000000))
    elif k == 4: st.move, st.rotate = (rnd.uniform(-30, 30), 0.0), rnd.uniform(-90, 90)
    else: st.symbol_state = rnd.randrange(3)
    if rnd.random() < 0.25: st.indication = rnd.choice(("bad", "override", "uncertain"))
    return st
for rep in range(3):
    for scan in range(12):
        sc.apply_states({an: state(an) for an in rnd.sample(ans, min(len(ans), 40))})
        app.processEvents(); v.viewport().grab()
    sc.clear_states(); app.processEvents(); v.viewport().grab()
sys.exit(0 if v.viewport().grab().toImage() == static else 5)
"""


@pytest.mark.parametrize(
    "name,mode",
    [
        ("FxComposites", "editor"),
        ("FxComposite", "editor"),
        ("FxComposites", "runtime"),
        ("FxComposite", "runtime"),
    ],
)
def test_composite_pages_survive_random_states(fx_page, name, mode):
    """Composite pages survive random states."""
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root)
    r = subprocess.run(
        [sys.executable, "-c", _STRESS, fx_page(name), mode], env=env, capture_output=True, timeout=600
    )
    assert r.returncode == 0, (r.returncode, r.stderr.decode(errors="replace")[-2000:])


def test_a_wider_value_keeps_its_justify_edge():
    """The runtime lays a text out from its anchor by the justify: a value wider than the
    record's box grows to the left (right justified) or both ways (centred), not to the right."""
    from ctgedit.model.document import ObjectKind as K, TextJustify
    from ctgedit.sim.state import ObjectState

    objs = [
        _obj(
            K.TEXT,
            n + 1,
            100,
            40 + 40 * n,
            60,
            20,
            text="##.#",
            font="Arial",
            font_size=12,
            text_colour=0x000000,
            justify=j,
        )
        for n, j in enumerate((TextJustify.RIGHT, TextJustify.CENTRE, TextJustify.LEFT))
    ]
    r = _Rig(_page(objs))
    try:
        r.apply({n: ObjectState(n, text="123456789.123") for n in (1, 2, 3)})
        right, centre, left = (r.scene._items[k].sceneBoundingRect() for k in range(3))
        assert right.width() > 60
        assert abs(right.right() - 160) <= 1 and right.left() < 100
        assert abs(centre.center().x() - 130) <= 1
        assert abs(left.left() - 100) <= 1 and left.right() > 160
        r.scene.clear_states()
        assert abs(r.scene._items[0].sceneBoundingRect().right() - 160) <= 1
    finally:
        r.close()


def test_default_pivot_is_the_bounds_centre_in_c_division():
    """No origin given: the bounds centre (rect2) with C division, like CtDraw's
    (ctf.rotation_origin): (-5 + 0) / 2 = -2."""
    from ctgedit.model.document import ObjectKind as K

    o = _obj(K.RECTANGLE, 1, -5, 10, 5, 10, filled=True, fill_colour=0xFF0000)
    line = _obj(K.LINE, 2, 100, 50, 80, 0, line_colour=0, line_width=5)
    line.raw["_rect2"] = (98, 48, 183, 53)  # a 5 px pen widens the bounds
    r = _Rig(_page([o, line]))
    try:
        layer = r.scene.sim_layer()
        assert layer._centre(0) == (-2, 15)
        assert layer._centre(1) == (140, 50)
    finally:
        r.close()


def test_runtime_look_draws_target_buttons_transparent_but_clickable():
    """A TARGET button is 'totally transparent' at runtime (help 1172135): its dashed editor
    frame goes, its pick area stays (the click reaches it)."""
    from PySide6 import QtCore
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.ui.simulate.canvas import SimCanvas

    btn = _obj(K.BUTTON, 7, 100, 100, 120, 50)
    btn.raw["_button_mode"] = 3
    r = _Rig(_page([btn]))
    try:
        frame = r.scene._items[0].childItems()
        assert frame and all(c.isVisible() for c in frame)
        assert not _near(r.px(100, 125), (255, 255, 255), 30)  # the dashed left edge
        r.scene.set_runtime_look(True)
        assert not any(c.isVisible() for c in frame)
        assert _near(r.px(100, 125), (255, 255, 255), 5)
        cv = SimCanvas(r.scene)
        cv.resize(W, H)
        cv.setSceneRect(0, 0, W, H)
        cv.show()
        r.app.processEvents()
        assert cv.ans_at(cv.mapFromScene(QtCore.QPointF(160, 125))) == (7,)
        r.scene.set_runtime_look(False)
        assert all(c.isVisible() for c in frame)
        cv.close()
    finally:
        r.close()


@pytest.mark.usefixtures("composite_cache")
def test_a_click_in_a_composite_genie_reaches_the_member_it_draws(fx_page):
    """A composite genie draws its members as one picture, but the runtime hit-tests the member
    records (their touch commands are theirs): the click's AN chain starts at the member.
    FxComposites: two drawn members with a touch command (FxComp3's and FxComp4's, of
    fx_drive_c)."""
    _app()
    from PySide6 import QtWidgets
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene
    from ctgedit.ui.simulate.canvas import SimCanvas

    page = read_page(fx_page("FxComposites"))
    sc = PageScene()
    sc.set_page(page)
    sc.set_runtime_look(True)
    cv = SimCanvas(sc)
    cv.resize(1200, 900)
    cv.show()
    QtWidgets.QApplication.processEvents()
    cv.set_zoom(1.0)
    layer, objs, checked = sc.sim_layer(), page.objects, 0
    try:
        for r, o in enumerate(objs):
            if o.kind.value != "composite_genie" or not o.children:
                continue
            for j, (_c, it, _f) in layer._composite_parts(r).items():
                touch = [m for m in objs[j].members if objs[m].raw.get("_type_code") == 0x1010]
                if it is None or not touch:
                    continue
                c = it.sceneBoundingRect().center()
                cv.centerOn(c)
                QtWidgets.QApplication.processEvents()
                ans = cv.ans_at(cv.mapFromScene(c))
                assert ans and ans[0] == objs[j].an and objs[r].an in ans, (j, ans)
                checked += 1
        assert checked == 2
    finally:
        cv.close()
        cv.deleteLater()
        QtWidgets.QApplication.processEvents()
        gc.collect()


def test_a_greyed_object_greys_its_new_colour_and_paints_from_a_cache(rig):
    """The grey picture is cached per effect and made again when the item changes under it
    (QGraphicsEffect.sourceChanged)."""
    from ctgedit.render.sim_states import _StateEffect
    from ctgedit.sim.state import DISABLE_GRAY_PART, ObjectState

    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_PART)})
    red = rig.px(50, 40)
    eff = rig.scene._items[0].graphicsEffect()
    assert isinstance(eff, _StateEffect) and eff._cache is not None
    cached = eff._cache
    rig.grab()
    assert eff._cache is cached  # an unchanged repaint reuses it
    rig.apply({1: ObjectState(1, disabled=True, disable_style=DISABLE_GRAY_PART, fill_colour=0x00FF00)})
    green = rig.px(50, 40)
    assert red[0] == red[1] == red[2] and green[0] == green[1] == green[2]
    assert green[0] > red[0] + 40  # grey of green is lighter


def test_runtime_trend_draws_the_engine_pens_not_the_design_time_traces():
    """The runtime look leaves out the editor's sine traces and focus rectangle; a trend state
    with its pens' scale draws each pen's samples (newest on the right edge, the value scaled
    from [lo, hi] to bottom .. top); the first engine shape (no scale) draws no pen."""
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import ObjectState

    tr = _obj(K.TREND, 9, 50, 50, 100, 60, tc=0x1003)
    tr.raw["_trend"] = {"spacing": 1, "pens": [(0x0000FF, 0x0000FF)] * 8}
    r = _Rig(_page([tr]))
    try:

        def blue_pixels(img):
            n = 0
            for x in range(50, 151, 2):
                for y in range(50, 111, 2):
                    c = r.px(x, y, img)
                    if c[2] > 200 and c[0] < 80:
                        n += 1
            return n

        assert blue_pixels(r.grab()) > 0  # the editor's design-time traces
        r.scene.set_runtime_look(True)
        assert blue_pixels(r.grab()) == 0  # the runtime draws no sine traces
        vals = [50.0] * 101
        r.apply(
            {
                9: ObjectState(
                    9,
                    trend={
                        "pens": [
                            {"pen": 1, "tag": "T", "scale": [0, 100], "colour": 0xFF0000, "values": vals}
                        ]
                    },
                )
            }
        )
        img = r.grab()
        assert _near(r.px(149, 80, img), (255, 0, 0), 60)  # the middle of 50 .. 110
        assert _near(r.px(149, 60, img), (255, 255, 255), 5)
        # the first engine shape ([t, v] samples, no scale): no pen
        r.apply({9: ObjectState(9, trend={"period": 0.25, "pens": [[[0.0, 50.0], [0.25, 50.0]]]})})
        img = r.grab()
        assert _near(r.px(149, 80, img), (255, 255, 255), 5)
        r.scene.clear_states()
        assert blue_pixels(r.grab()) == 0
        r.scene.set_runtime_look(False)
        assert blue_pixels(r.grab()) > 0
    finally:
        r.close()


def test_runtime_alarm_indicator_follows_the_alarm_state():
    """The editor always draws a group's alarm indicator; the runtime only while the group's
    equipment has an active alarm, in the state's colour."""
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import ObjectState

    g = _obj(K.GROUP, 2, 100, 100, 60, 40)
    m = _obj(K.RECTANGLE, 3, 100, 100, 60, 40, filled=True, fill_colour=0x808080, line_colour=0x808080)
    g.members, m.parent = [1], 0
    g.raw["_alarm_indicator"] = {
        "outer": (90, 90, 171, 151),
        "inner": (94, 94, 167, 147),
        "strip": 4,
        "fill": (255, 0, 0),
        "pen": (191, 191, 191),
        "flag": ("default", 60, 70),
        "last": 1,
        "origin": (100, 100),
        "width": 3,
        "flag_loc": 0,
        "flag_on": True,
    }
    r = _Rig(_page([g, m]))
    try:
        assert _near(r.px(91, 120), (255, 0, 0), 30)  # the editor's frame
        r.scene.set_runtime_look(True)
        assert _near(r.px(91, 120), (255, 255, 255), 5)  # no alarm: no frame
        r.apply(
            {2: ObjectState(2, alarm={"priority": 1, "state": "unack_on", "colour": 0xFFFF00, "flash": True})}
        )
        assert _near(r.px(91, 120), (255, 255, 0), 30)  # the alarm's colour
        assert _near(r.px(130, 120), (128, 128, 128), 10)  # the member is untouched
        r.apply({2: ObjectState(2, alarm={"priority": 0, "state": "none", "colour": None, "flash": False})})
        assert _near(r.px(91, 120), (255, 255, 255), 5)
        r.scene.set_runtime_look(False)
        assert _near(r.px(91, 120), (255, 0, 0), 30)  # the editor's again
    finally:
        r.close()


def test_a_genie_drawn_from_its_element_follows_its_members_states(fx_page):
    """A plain genie the reader draws from its library element (a clock set, status icons;
    here FxParts' FxSets1 of fx_sets, its symbol sets) takes its members' states on that
    picture's items, like a composite genie (the engine hides the clock
    set, the runtime shows nothing)."""
    from ctgedit.io.reader import read_page
    from ctgedit.sim.state import ObjectState

    page = read_page(fx_page("FxParts"))
    r = _Rig(page)
    try:
        sc, layer = r.scene, r.scene.sim_layer()
        roots = [
            i
            for i, o in enumerate(page.objects)
            if o.kind.value != "composite_genie" and o.children and o.raw.get("_resolved_ref")
        ]
        assert roots
        hit = None
        for root in roots:
            drawn = {j: p for j, p in layer._composite_parts(root).items() if p[1] is not None}
            if drawn:
                hit = (root, drawn)
                break
        assert hit is not None
        root, drawn = hit
        j, part = next(iter(drawn.items()))
        item = part[1]
        an = page.objects[j].an
        assert item.isVisible()
        r.apply({an: ObjectState(an, shown=False)})
        assert not item.isVisible() and sc._items[root].isVisible()
        r.scene.clear_states()
        assert item.isVisible()
    finally:
        r.close()


def test_an_active_alarm_indicator_frames_the_groups_current_content():
    """The runtime sizes an active indicator to the group's CURRENT content (a value grown
    wider widens the frame: Filling TNK20, Overview Milk Supply); back to
    the editor's frame when the alarm goes."""
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import ObjectState

    g = _obj(K.GROUP, 2, 100, 100, 60, 40)
    m = _obj(K.RECTANGLE, 3, 100, 100, 60, 40, filled=True, fill_colour=0x808080, line_colour=0x808080)
    g.members, m.parent = [1], 0
    g.raw["_alarm_indicator"] = {
        "outer": (90, 90, 171, 151),
        "inner": (94, 94, 167, 147),
        "strip": 4,
        "fill": (255, 0, 0),
        "pen": (191, 191, 191),
        "flag": None,
        "last": 1,
        "origin": (100, 100),
        "width": 3,
        "flag_loc": 0,
        "flag_on": False,
        "members": [(1, (100, 100, 161, 141))],
        "settings": {"width": 3, "padding": 5, "inside": False},
    }
    r = _Rig(_page([g, m]))
    try:
        r.scene.set_runtime_look(True)
        alarm = {"priority": 1, "state": "unack_on", "colour": 0xFFFF00, "flash": False}
        r.apply({2: ObjectState(2, alarm=alarm)})
        assert _near(r.px(91, 120), (255, 255, 0), 30)
        r.apply({3: ObjectState(3, move=(10, 0)), 2: ObjectState(2, alarm=alarm)})
        assert _near(r.px(91, 120), (255, 255, 255), 5)  # the frame moved with it
        assert _near(r.px(101, 120), (255, 255, 0), 30)
        r.apply({2: ObjectState(2, alarm={"priority": 0, "state": "none", "colour": None, "flash": False})})
        r.scene.set_runtime_look(False)
        assert _near(r.px(91, 120), (255, 0, 0), 30)  # the editor's frame again
    finally:
        r.close()


def test_the_runtime_look_has_no_page_border():
    """The runtime's window has no page border: the Simulate tab draws none."""
    from PySide6 import QtCore, QtGui
    from ctgedit.render.scene import PageScene

    _app()
    sc = PageScene()
    sc.set_page(_page([]))

    def edge():
        img = QtGui.QImage(20, 20, QtGui.QImage.Format_RGB32)
        img.fill(0x00FF00)
        p = QtGui.QPainter(img)
        sc.drawBackground(p, QtCore.QRectF(0, 0, 20, 20))
        p.end()
        return QtGui.QColor(img.pixel(0, 10))

    assert edge() != QtGui.QColor(BG)
    sc.set_runtime_look(True)
    assert edge() == QtGui.QColor(BG)


def test_a_state_copy_is_the_state_with_its_fields_made_safe():
    """_copy_state copies the fields through the instance dict: valid only while ObjectState
    has no __post_init__ and no __slots__."""
    import dataclasses

    from ctgedit.render.sim_states import _copy_state
    from ctgedit.sim.state import ObjectState

    assert not hasattr(ObjectState, "__post_init__") and not hasattr(ObjectState, "__slots__")
    st = ObjectState(an=7, move=(1, 2), rotate=725.0, text="1.5", vertex_offsets=[(1, 1)])
    c = _copy_state(st)
    assert c is not st and type(c) is ObjectState
    assert {f.name for f in dataclasses.fields(c)} == set(c.__dict__)
    assert c.rotate == 5.0 and c.text == "1.5" and c.vertex_offsets == [(1.0, 1.0)]
    assert c.vertex_offsets is not st.vertex_offsets
