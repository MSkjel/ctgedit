"""Animation point markers in the editor."""

import pytest

import fxinstall
from qtkit import app as _app

#: where _points_page parks its pool (below the 1024 x 768 page) and the grouped point
POOL = [(862 + 40 * k, 820) for k in range(5)]
GROUPED = (100, 800)
#: the white rectangle grouped with GROUPED (on the page)
WHITE_BOX = (300, 300, 360, 340)
#: the pick box CtDraw stores as rect2 of an AnPoint: (x - 4, y - 9) .. (x + 21, y + 6)
DELTAS = (-4, -9, 21, 6)


def _points_page(fx_install) -> str:
    """FxShapes of the fx_install copy with the animation points of this module (the
    module docstring), saved in place; returns its path."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.format import records as R

    path = fx_install.page("FxShapes")
    doc = EditableDocument.open(path)
    doc.set_page_background(0xE5E5E5)
    for at in POOL:
        doc.add_object(R.to_po(R.point_record(0x1000, handle=doc.next_handle(), at=at, deltas=DELTAS)))
    box = doc.create_object("rectangle", WHITE_BOX, colour=0xFFFFFF)
    pt = doc.add_object(R.to_po(R.point_record(0x1000, handle=doc.next_handle(), at=GROUPED, deltas=DELTAS)))
    assert doc.group_objects([box, pt]) is not None
    doc.save()
    return path


def _scene(path):
    _app()
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    page = read_page(path)
    sc = PageScene()
    sc.set_page(page)
    return page, sc


def _render(sc, rect, chrome=True):
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(int(rect.width()), int(rect.height()), QtGui.QImage.Format_ARGB32)
    img.fill(0)
    p = QtGui.QPainter(img)
    try:
        if chrome:
            with sc.editor_chrome():
                sc.render(p, QtCore.QRectF(img.rect()), rect)
        else:
            sc.render(p, QtCore.QRectF(img.rect()), rect)
    finally:
        p.end()
    return img


def test_every_animation_point_gets_a_marker_with_its_an_and_pick_box(fx_install):
    from ctgedit.render.scene import _AnPointMarker

    page, sc = _scene(_points_page(fx_install))
    pts = [i for i, o in enumerate(page.objects) if o.raw.get("_type_code") == 0x1000]
    assert len(pts) == len(POOL) + 1
    marks = {i: it for i, it in sc._items.items() if isinstance(it, _AnPointMarker)}
    assert set(marks) == set(pts)
    for i in pts:
        o, m = page.objects[i], marks[i]
        assert m._an == o.an
        # the bounds' pick box (x - 4, y - 9) .. (x + 21, y + 6) IS the stored rect2
        l, t, r, b = o.raw["_rect2"]
        br = m.boundingRect()
        assert (br.left(), br.top(), br.right(), br.bottom()) == (l, t, r, b)
    first = next(i for i in pts if page.objects[i].raw["_anchor"] == POOL[0])
    assert page.objects[first].an and marks[first]._an == page.objects[first].an
    assert (marks[first]._x, marks[first]._y) == (862, 820)
    assert sum(1 for i in pts if page.objects[i].parent is not None) == 1


def test_marker_cross_is_white_on_the_grey_surround_and_never_exported(fx_install):
    from PySide6 import QtCore

    page, sc = _scene(_points_page(fx_install))
    sc.set_surround("page")
    rect = QtCore.QRectF(850, 805, 40, 30)  # the point at (862, 820), off the page
    img = _render(sc, rect)
    white = 0xFFFFFFFF
    for dx, dy in ((0, 0), (-4, 0), (4, 0), (0, -4), (0, 4)):
        assert img.pixel(12 + dx, 15 + dy) == white  # the cross (CtDraw: white on e5e5e5)
    assert img.pixel(12 + 5, 15) != white  # it ends at x + 4
    with sc.export_only(None):
        out = _render(sc, rect, chrome=False)
    assert all(out.pixel(12 + dx, 15) != white for dx in range(-4, 5))
    # the eye dropper never sees a marker either
    assert sc.sample_colour(862, 820) is None  # off the page


def test_marker_ink_follows_the_pixel_under_it():
    from ctgedit.render.scene import _AnPointMarker

    assert _AnPointMarker._ink(0xFFFFFF).rgb() & 0xFFFFFF == 0x000000
    assert _AnPointMarker._ink(0xECECEC).rgb() & 0xFFFFFF == 0x000000
    assert _AnPointMarker._ink(0xEBFFFF).rgb() & 0xFFFFFF == 0xFFFFFF
    assert _AnPointMarker._ink(0xE5E5E5).rgb() & 0xFFFFFF == 0xFFFFFF


def test_an_on_page_marker_samples_the_drawn_content(fx_install, monkeypatch):
    """A point ON the page reads the drawn pixel under it (CtDraw's GetPixel), not the page
    colour: a point moved onto a white rectangle of the grey page turns black."""
    monkeypatch.setenv("CTG_NO_CTF_PRECHECK", "1")
    dst = _points_page(fx_install)
    from ctgedit.editing import EditableDocument
    from ctgedit.render.scene import PageScene, _AnPointMarker

    doc = EditableDocument.open(dst)
    sc = PageScene()
    sc.set_page(doc.page)
    # a spot where the drawn page is white (the white box; the page itself is 0xE5E5E5)
    tx, ty = next(
        (x, y) for y in range(100, 740, 7) for x in range(20, 1350, 7) if sc.sample_colour(x, y) == 0xFFFFFF
    )
    pt = next(
        i
        for i, o in enumerate(doc.page.objects)
        if o.raw.get("_type_code") == 0x1000 and doc.page.objects[i].parent is not None
    )
    m = sc._items[pt]
    assert m.under is None  # off the page: surround at paint
    m._x, m._y = tx, ty
    sc._resolve_marker_colours([m])
    assert m.under == 0xFFFFFF and m._ink(m.under).rgb() & 0xFFFFFF == 0
    assert isinstance(m, _AnPointMarker)


@pytest.mark.golden
def test_ctdraws_pick_box_and_cross_of_a_stored_point(fx_page):
    """CtDraw's pick box and cross for a stored point."""
    from PySide6 import QtGui
    from ctgedit.format import geometry as G
    from ctgedit.format.page import parse_ctg, parse_ctg_bytes

    mine = [po for po in parse_ctg(fx_page("FxAnPoints")).page.objects if po.type_code == 0x1000]
    assert len(mine) == 7
    data = fxinstall.oracle_bytes("pages", "FxMain/FxAnPoints", "page.ctg")
    theirs = [po for po in parse_ctg_bytes(data).page.objects if po.type_code == 0x1000]
    assert len(theirs) == len(mine)
    for po in theirs:
        gh = G.parse_geom_header(po.body_raw)
        x, y = gh.anchor
        assert tuple(gh.rect2) == tuple(v + d for v, d in zip((x, y, x, y), DELTAS)), (x, y)
    _app()
    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", "FxMain/FxAnPoints", "canvas.png"))
    for (x, y), ink in (((700, 600), 0xFFFFFF), ((330, 320), 0x000000)):
        for dx, dy in ((0, 0), (-4, 0), (4, 0), (0, -4), (0, 4)):
            assert img.pixel(x + dx, y + dy) & 0xFFFFFF == ink, (x, y, dx, dy)
        assert img.pixel(x + 5, y) & 0xFFFFFF != ink, (x, y)
