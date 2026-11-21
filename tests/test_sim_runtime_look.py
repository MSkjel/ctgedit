"""GDI details of the runtime look."""

import gc

import pytest
from qtkit import app as _app

W, H = 300, 200


def _obj(kind, an, x, y, w, h, tc, dw=0, **style):
    from ctgedit.model.document import Geometry, GraphicObject

    o = GraphicObject(kind=kind, an=an, geometry=Geometry(x=x, y=y, extent_x=w, extent_y=h))
    for k, v in style.items():
        setattr(o.style, k, v)
    o.raw["_type_code"] = tc
    o.raw["_dwflags"] = dw
    o.raw["_handle"] = an - 1
    return o


def _scene(objs):
    from ctgedit.model.document import Page
    from ctgedit.render.scene import PageScene

    _app()
    p = Page(name="look_test")
    p.appearance.width, p.appearance.height = W, H
    p.appearance.background_colour = 0xFFFFFF
    p.objects = objs
    sc = PageScene()
    sc.set_page(p)
    return sc


def _px(sc, x, y):
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(W, H, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, False)
    sc.render(p, QtCore.QRectF(0, 0, W, H), QtCore.QRectF(0, 0, W, H))
    p.end()
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


@pytest.fixture
def objs():
    from ctgedit.model.document import ObjectKind as K

    yield [
        # FP_PMP AN 86 'Background': a COL_ARR face, no pen, dynamic (dwFlags 0x10)
        _obj(K.RECTANGLE, 1, 20, 20, 60, 40, 0x0F, dw=0x118, filled=True, fill_colour=0xDDDDDD, line_style=5),
        # the same, static: in the page's baked background (CtDraw's GDI: l..r-1)
        _obj(K.RECTANGLE, 2, 120, 20, 60, 40, 0x0F, dw=0x08, filled=True, fill_colour=0xDDDDDD, line_style=5),
    ]
    gc.collect()


def test_a_dynamic_fill_with_no_pen_covers_its_edge_at_run_time(objs):
    sc = _scene(objs)
    # the editor look: GDI's Rectangle(l, t, r + 1, b + 1) with a null pen fills l..r-1
    assert _px(sc, 79, 40) == (221, 221, 221) and _px(sc, 80, 40) == (255, 255, 255)
    assert _px(sc, 50, 60) == (255, 255, 255)
    sc.set_runtime_look(True)
    assert _px(sc, 80, 40) == (221, 221, 221)  # the right edge column
    assert _px(sc, 50, 60) == (221, 221, 221)  # the bottom edge row
    assert _px(sc, 81, 40) == (255, 255, 255)
    assert set(sc.sim_layer().runtime_fill_edges()) == {0}
    # the static one stays as the baked background has it
    assert _px(sc, 180, 40) == (255, 255, 255) and _px(sc, 179, 40) == (221, 221, 221)
    sc.set_runtime_look(False)
    assert _px(sc, 80, 40) == (255, 255, 255)
    assert sc.sim_layer().runtime_fill_edges() == {}


def test_a_scaled_rectangle_and_polygon_truncate_like_the_runtime():
    from ctgedit.model.document import ObjectKind as K
    from ctgedit.sim.state import ObjectState

    # ExampleSA Filling_L3 AN 622 / 624 at SZX 111.1 % about their left edges
    shadow = _obj(
        K.RECTANGLE, 1, 20, 91, 96, 28, 0x0F, dw=0x18, filled=True, fill_colour=0xC0C0C0, line_style=5
    )
    body = _obj(
        K.POLYGON, 2, 22, 93, 96, 28, 0x10, dw=0x18, filled=False, line_colour=0x969696, polygon_open=True
    )
    body.geometry.nodes = [(118, 93), (22, 93), (22, 121), (118, 121)]
    sc = _scene([shadow, body])
    sc.set_runtime_look(True)
    s = 1.0 / 0.9
    sc.apply_states(
        {
            1: ObjectState(1, scale=(s, 1.0), scale_origin=(20.0, 105.0)),
            2: ObjectState(2, scale=(s, 1.0), scale_origin=(22.0, 107.0)),
        },
        replace=True,
    )
    # live: the shadow's fill ends at x 126 (20 + 97 x 1.111 = 127.8, cut to 127, exclusive),
    # the outline's top row at 128 (22 + 96 x 1.111 = 128.67 cut to 128)
    assert _px(sc, 126, 92) == (192, 192, 192) and _px(sc, 127, 92) == (255, 255, 255)
    assert _px(sc, 128, 93) == (150, 150, 150) and _px(sc, 129, 93) == (255, 255, 255)
    sc.clear_states()
    # unscaled: the shadow to its edge (116), the outline on its last vertex (118)
    assert _px(sc, 116, 92) == (192, 192, 192) and _px(sc, 117, 92) == (255, 255, 255)
    assert _px(sc, 118, 93) == (150, 150, 150) and _px(sc, 119, 93) == (255, 255, 255)


def test_a_slanted_stroke_falls_on_the_pixels_gdi_lights():
    """FP_Wind / Filling_L3's off-page arrow (128,93) -> (142,107) -> (128,121): GDI lights x 139
    on row 110 of the down-left edge; Qt's aliased stroker on the pixel centres took 140."""
    from ctgedit.model.document import ObjectKind as K

    arrow = _obj(K.POLYGON, 1, 121, 93, 21, 28, 0x10, filled=True, fill_colour=0xDDDDDD, line_colour=0x969696)
    arrow.geometry.nodes = [(128, 93), (142, 107), (128, 121), (121, 121), (121, 93)]
    sc = _scene([arrow])
    sc.set_runtime_look(True)
    assert _px(sc, 139, 110) == (150, 150, 150) and _px(sc, 140, 110) == (255, 255, 255)
    assert _px(sc, 135, 100) == (150, 150, 150)  # the down-right edge as before
    assert _px(sc, 121, 100) == (150, 150, 150)  # the vertical edge as before
    sc.set_runtime_look(False)
    assert _px(sc, 121, 100) == (150, 150, 150)
