"""The editor look of the canvas: grid, pixel grid and page frame."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ctgedit.model.document import (  # noqa: E402
    Geometry,
    GraphicObject,
    ObjectKind as K,
    Page,
    PageAppearance,
    Style,
)
from qtkit import app as _app, canvas_picture


def _pump(n=3):
    for _ in range(n):
        _app().processEvents()


def _page(w=400, h=300, bg=0xFFFFFF):
    obj = GraphicObject(
        kind=K.RECTANGLE,
        geometry=Geometry(x=50, y=50, extent_x=60, extent_y=40),
        style=Style(filled=True, fill_colour=0x0000FF, line_colour=0x0000FF),
        raw={"_type_code": 0x0F, "_dwflags": 0x02},
    )
    return Page(
        name="synthetic", appearance=PageAppearance(width=w, height=h, background_colour=bg), objects=[obj]
    )


def _scene(page=None, grid=8, **look):
    from ctgedit.render.scene import PageScene

    _app()
    sc = PageScene()
    sc.set_page(page or _page(), grid_size=grid)
    sc._MARGIN_COLOUR = QtGui.QColor("#2a2d31")
    if look:
        sc.set_look(**look)
    return sc


def _bg(sc, zoom, origin=(-20.0, -20.0), size=(480, 360)):
    """The editor background as the canvas paints it: (image, scene -> pixel)."""
    w, h = size
    ox, oy = origin
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 0, 255))
    p = QtGui.QPainter(img)
    p.scale(zoom, zoom)
    p.translate(-ox, -oy)
    with sc.editor_chrome():
        sc.drawBackground(p, QtCore.QRectF(ox, oy, w / zoom, h / zoom))
    p.end()

    def at(x, y):
        return QtGui.QColor(img.pixel(int(round((x - ox) * zoom)), int(round((y - oy) * zoom))))

    return img, at


def _render(sc, w=400, h=300):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.white)
    p = QtGui.QPainter(img)
    sc.render(p, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(0, 0, w, h))
    p.end()
    return img


# --------------------------------------------------------------------- the signature
def test_background_signature_covers_every_look_key():
    from ctgedit.render import scene as S

    sc = _scene()
    seen = {sc.background_signature()}
    for key, value in (
        ("grid_major", 4),
        ("grid_style", "dots"),
        ("grid_outside", True),
        ("pixel_grid", False),
        ("page_edge", "line"),
        ("page_shadow", False),
    ):
        assert S.EDITOR_LOOK[key] != value
        sc.set_look(**{key: value})
        sig = sc.background_signature()
        assert sig not in seen, key
        seen.add(sig)
    sc.set_surround("shaded")
    assert sc.background_signature() not in seen
    # garbage is ignored, not stored
    before = sc.background_signature()
    sc.set_look(grid_style="hatched", page_edge="wavy", grid_major="x", nothing=1)
    assert sc.background_signature() == before
    sc.set_look(grid_major=1000)
    assert sc.look("grid_major") == S.GRID_MAJOR_MAX


def test_desk_is_the_default_surround():
    from ctgedit.render.scene import SURROUND_MODES
    from ctgedit.ui.prefs.settings import SURROUND_LABELS, VIEW_DEFAULTS

    sc = _scene()
    assert sc.surround() == "desk" == SURROUND_MODES[0] == VIEW_DEFAULTS["view/surround"]
    assert [m for m, _l in SURROUND_LABELS] == list(SURROUND_MODES)
    assert sc.surround_colour() == QtGui.QColor("#2a2d31")
    sc.set_surround("nonsense")
    assert sc.surround() == "desk"


# --------------------------------------------------------------------- prints / exports
def test_render_and_exports_never_draw_the_editor_look():
    looks = [
        dict(),
        dict(
            grid_major=2,
            grid_style="dots",
            grid_outside=True,
            pixel_grid=True,
            page_edge="line",
            page_shadow=True,
        ),
        dict(grid_major=0, pixel_grid=False, page_edge="none", page_shadow=False),
    ]
    plain, page_only, sel = [], [], []
    for look in looks:
        for mode in ("desk", "shaded", "page", "workspace"):
            sc = _scene(**look)
            sc.set_surround(mode)
            plain.append(_render(sc))
            with sc.export_only(None):
                page_only.append(_render(sc))
            with sc.export_only([0]):
                sel.append(_render(sc))
    for imgs in (plain, page_only, sel):
        assert all(i == imgs[0] for i in imgs[1:])
    # a render at 1000 % (a magnified print) draws no pixel grid either
    sc = _scene(pixel_grid=True)
    img = QtGui.QImage(200, 200, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.white)
    p = QtGui.QPainter(img)
    with sc.export_only(None):
        sc.render(p, QtCore.QRectF(0, 0, 200, 200), QtCore.QRectF(200, 200, 20, 20))
    p.end()
    assert len({img.pixel(x, 100) for x in range(200)}) == 1


def test_thumbnail_and_preview_are_unchanged():
    from ctgedit.ui.browser.preview import render_page_image

    a = render_page_image(_page(), 200, 150)
    from ctgedit.render import scene as S

    saved = dict(S.EDITOR_LOOK)
    try:
        S.EDITOR_LOOK.update(grid_style="dots", pixel_grid=True, page_edge="line")
        b = render_page_image(_page(), 200, 150)
    finally:
        S.EDITOR_LOOK.clear()
        S.EDITOR_LOOK.update(saved)
    assert a == b


# --------------------------------------------------------------------- the desk and shadow
def test_desk_shadow_lies_down_and_right_of_the_page():
    sc = _scene(grid=0)
    _img, at = _bg(sc, 1.0)
    desk = QtGui.QColor("#2a2d31")
    assert at(200, 150) == QtGui.QColor(255, 255, 255)  # the page
    assert at(-10, -10) == desk  # far off the page
    below = at(200, 301)
    right = at(401, 150)
    for c in (below, right):
        assert c.lightness() < desk.lightness(), c.name()
    # nothing above or left of the page
    assert at(200, -3) == desk and at(-3, 150) == desk
    sc.set_look(page_shadow=False)
    _img, at = _bg(sc, 1.0)
    assert at(200, 302) == desk and at(402, 150) == desk


def test_shadow_in_the_other_surrounds_follows_its_option():
    sc = _scene(grid=0)
    sc.set_surround("workspace")
    _img, at = _bg(sc, 1.0)
    assert at(200, 302).lightness() < QtGui.QColor("#2a2d31").lightness()
    sc.set_look(page_shadow=False)
    _img, at = _bg(sc, 1.0)
    assert at(200, 302) == QtGui.QColor("#2a2d31")


# --------------------------------------------------------------------- the page edge
def test_page_edge_dotted_line_none():
    sc = _scene(grid=0, page_shadow=False)
    sc.set_surround("workspace")
    _img, at = _bg(sc, 1.0)
    run = [at(x, -1) for x in range(10, 22)]
    assert all(run[k] != run[k + 1] for k in range(len(run) - 1))  # CtDraw's dots
    sc.set_look(page_edge="line")
    _img, at = _bg(sc, 1.0)
    run = {at(x, -1).rgba() for x in range(10, 22)}
    assert len(run) == 1 and QtGui.QColor.fromRgba(run.pop()) != QtGui.QColor("#2a2d31")
    sc.set_look(page_edge="none")
    _img, at = _bg(sc, 1.0)
    assert {at(x, -1).rgba() for x in range(10, 22)} == {QtGui.QColor("#2a2d31").rgba()}


# --------------------------------------------------------------------- the grid
def _alpha_on_white(c: QtGui.QColor) -> int:
    return 255 - c.red()


def test_major_lines_stay_when_the_minor_grid_is_too_fine():
    sc = _scene(grid=8, page_shadow=False)
    # 40 %: minor lines 3.2 px apart (not drawn); majors 25.6 px apart
    img, at = _bg(sc, 0.4, origin=(0.0, 0.0), size=(160, 120))
    majors = [x for x in range(160) if QtGui.QColor(img.pixel(x, 60)).red() < 250]
    assert majors, "no major lines at 40 %"
    gaps = {b - a for a, b in zip(majors, majors[1:])}
    assert gaps <= {25, 26}, gaps
    assert sc.grid_visible_at(0.4)
    sc.set_look(grid_major=0)
    img, at = _bg(sc, 0.4, origin=(0.0, 0.0), size=(160, 120))
    assert not [x for x in range(160) if QtGui.QColor(img.pixel(x, 60)).red() < 250]
    assert not sc.grid_visible_at(0.4)
    assert sc.grid_visible_at(1.0)


def test_minor_lines_fade_in_and_majors_are_stronger():
    from ctgedit.render.scene import PageScene

    assert PageScene._grid_fade(4.0) == 0.0 and PageScene._grid_fade(12.0) == 1.0
    assert 0.0 < PageScene._grid_fade(8.0) < 1.0
    sc = _scene(grid=8, page_shadow=False)
    weak, strong = {}, {}
    for z in (0.7, 1.0, 2.0):
        _img, at = _bg(sc, z, origin=(0.0, 0.0), size=(400, 300))
        weak[z] = _alpha_on_white(at(8, 21))  # a minor vertical line
        strong[z] = _alpha_on_white(at(64, 21))  # a major one
    assert 0 < weak[0.7] < weak[1.0] < weak[2.0]  # 5.6, 8, 16 px apart
    assert all(strong[z] > weak[z] for z in strong)
    assert strong[0.7] == strong[2.0]  # majors do not fade


def test_dots_style_marks_only_the_crossings():
    sc = _scene(grid=8, page_shadow=False, grid_style="dots", grid_major=0)
    img, at = _bg(sc, 2.0, origin=(0.0, 0.0), size=(200, 200))
    white = QtGui.QColor(255, 255, 255)
    assert at(16, 16) != white  # a crossing
    assert at(16, 20) == white  # on a grid line, between crossings
    assert at(20, 16) == white


def test_major_line_every_1_makes_every_line_major():
    sc = _scene(grid=8, page_shadow=False, grid_major=1)
    _img, at = _bg(sc, 0.7, origin=(0.0, 0.0), size=(400, 300))
    # 5.6 px apart: every line drawn in the major colour, as strong as a major one is
    assert _alpha_on_white(at(8, 21)) == _alpha_on_white(at(64, 21)) > 0
    ref = _scene(grid=8, page_shadow=False)
    _img, rat = _bg(ref, 0.7, origin=(0.0, 0.0), size=(400, 300))
    assert _alpha_on_white(at(8, 21)) == _alpha_on_white(rat(64, 21))
    # too fine (3.2 px at 40 %): nothing drawn, and grid_visible_at says so
    img, _at = _bg(sc, 0.4, origin=(0.0, 0.0), size=(160, 120))
    assert not [x for x in range(160) if QtGui.QColor(img.pixel(x, 60)).red() < 250]
    assert not sc.grid_visible_at(0.4) and sc.grid_visible_at(0.7)


def test_grid_alphas_on_light_and_dark_pages():
    from ctgedit.render.scene import PageScene

    for bg in (QtGui.QColor(255, 255, 255), QtGui.QColor(20, 20, 20)):
        major, minor = PageScene._grid_colours(bg)
        assert (major.alpha(), minor.alpha()) == (46, 20)


def test_dots_with_majors_and_one_device_pixel_at_any_ratio():
    sc = _scene(grid=8, page_shadow=False, page_edge="none", grid_style="dots", grid_major=2)
    _img, at = _bg(sc, 2.0, origin=(0.0, 0.0), size=(200, 200))
    white = QtGui.QColor(255, 255, 255)
    assert _alpha_on_white(at(32, 32)) > _alpha_on_white(at(24, 32)) > 0  # major, minor
    assert at(32, 36) == white
    # a 2x device: each dot is one device pixel, where a cosmetic point would land
    img = QtGui.QImage(200, 200, QtGui.QImage.Format_ARGB32)
    img.setDevicePixelRatio(2.0)
    img.fill(QtGui.QColor(255, 0, 255))
    p = QtGui.QPainter(img)
    with sc.editor_chrome():
        sc.drawBackground(p, QtCore.QRectF(0, 0, 100, 100))
    p.end()
    assert QtGui.QColor(img.pixel(32, 32)) != white
    assert QtGui.QColor(img.pixel(33, 32)) == white
    assert QtGui.QColor(img.pixel(32, 33)) == white


def _device_bg(sc, zoom, dpr, origin=(0.0, 0.0), size=(300, 200)):
    """The editor background painted on a device of ``dpr`` device pixels per logical
    one (150 % Windows scaling = 1.5), ``size`` logical pixels."""
    w, h = size
    img = QtGui.QImage(int(w * dpr), int(h * dpr), QtGui.QImage.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QtGui.QColor(255, 255, 255))
    p = QtGui.QPainter(img)
    p.scale(zoom, zoom)
    p.translate(-origin[0], -origin[1])
    with sc.editor_chrome():
        sc.drawBackground(p, QtCore.QRectF(origin[0], origin[1], w / zoom, h / zoom))
    p.end()
    return img


def _line_widths(img):
    """The widths (device pixels) of the vertical and of the horizontal lines: the runs of
    marked pixels along the rows and columns that are not on a line of the other way."""
    white = QtGui.QColor(255, 255, 255).rgb()
    W, H = img.width(), img.height()

    def runs(cells):
        out, cur = set(), 0
        for c in cells:
            if c != white:
                cur += 1
            elif cur:
                out.add(cur)
                cur = 0
        return out

    rows = [[img.pixel(x, y) for x in range(W)] for y in range(H)]
    cols = [[rows[y][x] for y in range(H)] for x in range(W)]
    across = [r for r in rows if sum(c != white for c in r) < W // 2]
    down = [c for c in cols if sum(v != white for v in c) < H // 2]
    assert across and down
    v = set().union(*(runs(r) for r in across))
    h = set().union(*(runs(c) for c in down))
    return v, h


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_grid_lines_and_pixel_grid_one_device_pixel_at_any_ratio(dpr):
    """A grid line and a pixel grid line are one DEVICE pixel wide at 125 / 150 / 200 %
    Windows scaling too, not 1 logical pixel."""
    sc = _scene(grid=10, page_shadow=False, page_edge="none")
    for zoom in (1.0, 2.0):
        v, h = _line_widths(_device_bg(sc, zoom, dpr))
        assert v == {1} and h == {1}, (dpr, zoom, v, h)
    px = _scene(grid=0, page_shadow=False, page_edge="none")
    img = _device_bg(px, 10.0, dpr, origin=(100.0, 100.0), size=(200, 150))
    v, h = _line_widths(img)
    assert v == {1} and h == {1}, (dpr, v, h)
    # a line at every page pixel: one per 10 logical pixels
    row = [img.pixel(x, int(5 * dpr)) for x in range(img.width())]
    marks = sum(1 for a, b in zip(row, row[1:]) if a == QtGui.QColor(255, 255, 255).rgb() != b)
    assert marks in (19, 20), marks


def test_grid_on_the_page_only_unless_asked():
    sc = _scene(grid=8, page_shadow=False)
    sc.set_surround("workspace")
    desk = QtGui.QColor("#2a2d31")
    img, at = _bg(sc, 2.0, origin=(-40.0, -40.0), size=(200, 200))
    row = {QtGui.QColor(img.pixel(x, 20)).rgba() for x in range(0, 60)}  # y = -30
    assert row == {desk.rgba()}
    sc.set_look(grid_outside=True)
    img, at = _bg(sc, 2.0, origin=(-40.0, -40.0), size=(200, 200))
    row = {QtGui.QColor(img.pixel(x, 20)).rgba() for x in range(0, 60)}
    assert len(row) > 1
    # the desk never has the grid around the page
    sc.set_surround("desk")
    img, at = _bg(sc, 2.0, origin=(-40.0, -40.0), size=(200, 200))
    row = {QtGui.QColor(img.pixel(x, 20)).rgba() for x in range(0, 60)}
    assert row == {desk.rgba()}


def test_snapping_keeps_the_minor_pitch():
    from ctgedit.ui.editor.canvas import CanvasView

    _app()
    v = CanvasView()
    v.set_grid(8)
    sc = _scene(grid=8, grid_major=8)
    v.setScene(sc)
    assert v.grid()[:2] == (8, 8)
    v.deleteLater()


# --------------------------------------------------------------------- the pixel grid
def test_pixel_grid_from_800_percent_on_the_page_only():
    sc = _scene(grid=0, page_shadow=False, page_edge="none")
    img, at = _bg(sc, 10.0, origin=(100.0, 100.0), size=(200, 200))
    line = QtGui.QColor(img.pixel(50, 5))  # x = 105: a pixel boundary
    inside = QtGui.QColor(img.pixel(55, 5))
    assert line != inside and inside == QtGui.QColor(255, 255, 255)
    img4, _at = _bg(sc, 4.0, origin=(100.0, 100.0), size=(200, 200))
    assert len({img4.pixel(x, 5) for x in range(200)}) == 1
    sc.set_look(pixel_grid=False)
    img, at = _bg(sc, 10.0, origin=(100.0, 100.0), size=(200, 200))
    assert len({img.pixel(x, 5) for x in range(200)}) == 1
    # off the page: none
    sc.set_look(pixel_grid=True)
    img, at = _bg(sc, 10.0, origin=(-15.0, 10.0), size=(100, 100))
    assert len({img.pixel(x, 50) for x in range(0, 140 // 10)}) == 1


def test_pixel_grid_is_lighter_on_dark_pages():
    sc = _scene(_page(bg=0x000000), grid=0, page_shadow=False)
    img, _at = _bg(sc, 10.0, origin=(100.0, 100.0), size=(200, 200))
    assert QtGui.QColor(img.pixel(50, 5)).red() > 0  # a white line on black
    assert QtGui.QColor(img.pixel(55, 5)).red() == 0


# --------------------------------------------------------------------- cached == direct
def test_cached_background_equals_direct_paint_with_the_new_look():
    from ctgedit.ui.editor.canvas import CanvasView

    _app()
    sc = _scene(grid=8)
    views = []
    for cache in (True, False):
        v = CanvasView()
        if not cache:
            v.setCacheMode(QtWidgets.QGraphicsView.CacheNone)
        v.setScene(sc)
        v.resize(700, 500)
        v.show()
        views.append(v)
    _pump()
    cached, direct = views

    def same(tag):
        direct.resize(cached.size())
        _pump()
        direct.setSceneRect(cached.sceneRect())
        direct.setTransform(cached.transform())
        direct.horizontalScrollBar().setValue(cached.horizontalScrollBar().value())
        direct.verticalScrollBar().setValue(cached.verticalScrollBar().value())
        canvas_picture(cached)
        a, b = canvas_picture(cached), canvas_picture(direct)
        bad = [
            (x, y)
            for y in range(0, a.height(), 3)
            for x in range(0, a.width(), 3)
            if a.pixel(x, y) != b.pixel(x, y)
        ]
        assert not bad, (tag, bad[:5])

    try:
        for look in (dict(), dict(grid_style="dots"), dict(grid_outside=True, page_edge="line")):
            sc.set_look(**look)
            for z in (0.4, 1.0, 10.0):
                cached.set_zoom(z)
                _pump()
                same((look, z))
                sb = cached.verticalScrollBar()
                sb.setValue(sb.value() + 37)
                same((look, z, "scrolled"))
        sc.set_surround("workspace")
        same("workspace")
    finally:
        for v in views:
            v.hide()
            v.setScene(None)
            v.deleteLater()
        _pump(1)
