"""Render details: blank parameter captions, text frames, boxes and baselines, hidden genie members,
missing library elements, the reader parts' import order."""

import os

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _idx(doc, name):
    hits = doc.find_objects_by_name(name)
    assert len(hits) == 1, (name, hits)
    return hits[0]


def test_a_blank_genie_parameter_caption_draws_nothing(qapp, fx_page):
    """A member whose caption is a '%Param%' token with the instance's value blank draws
    nothing (CtDraw), not the raw token. FxParts' FxOrder1 binds MixedCase and Title only
    (its %lower% and %UPPER% members are blank); FxManyParams binds Tag, P01, P05 and P41
    (P02 .. P04 in its second member are blank)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    path = fx_page("FxParts")
    page = reader.read_page(path)
    doc = EditableDocument.open(path)
    sc = PageScene()
    sc.set_page(page)
    order = _idx(doc, "FxOrder1")
    many = _idx(doc, "FxManyParams")
    shown = {
        i: sc._text_string(page.objects[i])
        for i in page.subtree(order) + page.subtree(many)
        if page.objects[i].kind.name == "TEXT"
    }
    assert not [t for t in shown.values() if "%lower" in t or "%UPPER" in t or "%P0" in t or "%Title" in t]
    # the two unbound members of FxOrder1 draw nothing; the bound ones their values
    order_texts = [shown[i] for i in page.subtree(order) if i in shown]
    assert order_texts.count("") == 2 and "mixed" in order_texts and "Order" in order_texts
    assert "first    fifth" in shown.values()


def _with_slot2(po, m4, scale):
    """A copy of text record ``po`` whose slot 2 (its Total) is the linear map ``m4``, its
    rect1 the old box scaled by ``scale`` (a text placed at that transform)."""
    import copy
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as PL

    po = copy.copy(po)
    slots, _end = PL.read_slots(po.body_raw)
    mode, m6 = slots[2][0], slots[2][1]
    mode = mode if PL._n_doubles(mode) == 6 else 0x0F
    new = [(m, x) for m, x, _o, _n in slots[:2]] + [(mode, tuple(m4) + tuple(m6[4:]))]
    PL.write_slots(po, new)
    gh = G.parse_geom_header(po.body_raw)
    l, t, r, b = gh.rect1
    rect = (l, t, l + round((r - l) * scale[0]), t + round((b - t) * scale[1]))
    PL.write_header_geom(po, gh.anchor, rect, gh.rect2)
    return po


def test_text_frames_follow_ctdraws_aspect_tolerance(fx_page):
    """Text frames follow CtDraw's aspect tolerance."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import page as P
    from ctgedit.io.readerparts import graft as G

    def framed(path):
        with open(path, "rb") as fh:
            objs = P.parse_ctg_bytes(fh.read()).page.objects
        return [i for i, po in enumerate(objs) if po.type_code == 5 and G.text_needs_frame(po)]

    assert len(framed(fx_page("FxSaMaster", "FxSa"))) == 7
    assert framed(fx_page("FxGenies")) == []
    path = fx_page("FxTextKinds")
    doc = EditableDocument.open(path)
    po = doc.parsed.page.objects[_idx(doc, "FxDigit22")]
    assert not G.text_needs_frame(po)
    assert G.text_needs_frame(_with_slot2(po, (1.14, 0, 0, 1.21), (1.14, 1.21)))
    assert not G.text_needs_frame(_with_slot2(po, (2.4606, 0, 0, 2.4618), (2.4606, 2.4618)))
    assert not G.text_needs_frame(_with_slot2(po, (1.5, 0, 0, 1.5), (1.5, 1.5)))
    assert G.text_needs_frame(_with_slot2(po, (-1, 0, 0, 1), (1, 1)))  # mirrored


def test_an_ordinary_genies_always_hidden_member_still_draws(qapp, fx_page):
    """Only a composite genie's cache folds a constant hidden-when away; an ordinary genie's
    blue base (VISIBLE '1') draws, as in CtDraw. FxParts' FxHiddenBase1
    (fxparts.fx_hidden_base: a 60 x 30 rectangle 0x2050E0 with VISIBLE '1')."""
    from ctgedit.editing import EditableDocument

    path = fx_page("FxParts")
    doc = EditableDocument.open(path)
    from PySide6 import QtCore, QtGui
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    page = reader.read_page(path)
    g = page.objects[_idx(doc, "FxHiddenBase1")].geometry
    sc = PageScene()
    sc.set_page(page)
    l, t = int(g.x), int(g.y)
    w, h = int(g.extent_x), int(g.extent_y)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor("white"))
    pa = QtGui.QPainter(img)
    sc.render(pa, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(l, t, w, h))
    pa.end()
    blue = sum(
        1
        for x in range(w)
        for y in range(h)
        if (lambda c: c.blue() > 180 and c.red() < 120)(img.pixelColor(x, y))
    )
    assert blue > 40


def test_a_reader_part_imports_first():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "from ctgedit.io.readerparts import graft"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert r.returncode == 0, r.stderr


def test_a_missing_library_element_is_ctdraws_x_box(qapp, fx_page):
    """A record whose library element is not found draws CtDraw's X box over rect1 and
    nothing of its own content. FxMissing
    places fxgone.fx_value and fxgone.fx_plain, a library the install has not; FxGenies,
    whose libraries are all there, has no such record."""
    from PySide6 import QtGui, QtCore
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene, _gdi_line

    page = reader.read_page(fx_page("FxMissing"))
    missing = [i for i, o in enumerate(page.objects) if o.raw.get("_missing_element")]
    assert [page.objects[i].raw["_missing_element"] for i in missing] == [
        "fxgone.fx_value",
        "fxgone.fx_plain",
    ]
    sc = PageScene()
    sc.set_page(page)
    for i in missing:
        inside = [j for j in page.subtree(i) if j != i]
        assert inside and all(page.objects[j].raw.get("_under_missing") for j in inside)
        assert all(j not in sc._items for j in inside)
        l, t, r, b = page.objects[i].raw["_rect1"]
        img = QtGui.QImage(r + 4, b + 4, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor("white"))
        pa = QtGui.QPainter(img)
        sc.render(pa, QtCore.QRectF(0, 0, r + 4, b + 4), QtCore.QRectF(0, 0, r + 4, b + 4))
        pa.end()
        dark = {
            (x, y)
            for x in range(l - 1, r + 2)
            for y in range(t - 1, b + 2)
            if img.pixelColor(x, y).lightness() < 100
        }
        frame = {(x, y) for x in range(l, r) for y in (t, b - 1)} | {
            (x, y) for y in range(t, b) for x in (l, r - 1)
        }
        want = frame | set(_gdi_line(l, t, r, b)) | set(_gdi_line(l, b - 1, r - 1, t))
        assert dark == want
    genies = reader.read_page(fx_page("FxGenies"))
    assert not [o for o in genies.objects if o.raw.get("_missing_element")]


def test_a_one_pixel_missing_element_paints_nothing(qapp, fx_page):
    """A 1 px wide or high rect1 of a missing element paints no X box: the record is not
    marked missing and its own members draw. FxMissing's FxGoneThin places fxgone.fx_thin,
    a genie of one 1 x 40 rectangle, whose library is gone; its rect1 is 1 px wide."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    doc = EditableDocument.open(fx_page("FxMissing"))
    page = reader.read_page(fx_page("FxMissing"))
    i = _idx(doc, "FxGoneThin")
    l, t, r, b = page.objects[i].raw["_rect1"]
    assert min(r - l, b - t) <= 1
    assert not page.objects[i].raw.get("_missing_element")
    inside = [j for j in page.subtree(i) if j != i]
    assert inside and not any(page.objects[j].raw.get("_under_missing") for j in inside)
    sc = PageScene()
    sc.set_page(page)
    assert any(j in sc._items for j in inside)  # its own content, no X box


@pytest.mark.golden
@pytest.mark.skipif(os.name != "nt", reason="GDI text metrics are Windows only")
def test_ctdraws_measured_text_box_is_gdis_line_height(qapp, fx_install):
    """CtDraw's PageOpen + PageSave of FxTextKinds (the oracle) re-measures FxCalibri
    (Calibri 12): the box height it stores is GDI's line height (textmetrics.font_metrics),
    and we draw the baseline tmAscent below that box's top."""
    import fxinstall
    from PySide6 import QtGui
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textmetrics
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    data = fxinstall.oracle_bytes("pages", "FxMain/FxTextKinds", "page.ctg")
    path = os.path.join(fx_install.project("FxMain"), "FxTextKindsCtDraw.ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    doc = EditableDocument.open(path)
    page = reader.read_page(path)
    sc = PageScene()
    sc.set_page(page)
    i = _idx(doc, "FxCalibri")
    obj, it = page.objects[i], sc._items[i]
    assert (obj.style.font, obj.style.font_size) == ("Calibri", 12)
    asc, h = textmetrics.font_metrics("Calibri", 12, 0)
    assert obj.geometry.extent_y == h
    base = it.pos().y() + QtGui.QFontMetricsF(it.font()).ascent()
    assert abs(base - (obj.geometry.y + asc)) < 0.01


@pytest.mark.skipif(os.name != "nt", reason="GDI text metrics are Windows only")
def test_a_texts_baseline_is_gdis_ascent_below_its_measured_box(qapp, fx_page):
    """A text's baseline is GDI's ascent below its box."""
    from PySide6 import QtGui
    from ctgedit.editing import EditableDocument
    from ctgedit.format import textmetrics
    from ctgedit.io import reader
    from ctgedit.render.scene import PageScene

    path = fx_page("FxTextKinds")
    doc = EditableDocument.open(path)
    page = reader.read_page(path)
    sc = PageScene()
    sc.set_page(page)
    i = _idx(doc, "FxCalibri")
    obj, it = page.objects[i], sc._items[i]
    assert (obj.style.font, obj.style.font_size, obj.style.text) == ("Calibri", 12, "Calibri twelve")
    asc, h = textmetrics.font_metrics("Calibri", 12, 0)
    assert obj.geometry.extent_y == h
    base = it.pos().y() + QtGui.QFontMetricsF(it.font()).ascent()
    assert abs(base - (obj.geometry.y + asc)) < 0.01
    # FxDigit22 (a 22 pt '8', its measured 16 x 33 box) given a 48 px high box it was not
    # measured for, in memory (as an art-stripped member's box)
    from ctgedit.format import geometry as G
    from ctgedit.format import placement as PL
    from ctgedit.format.page import parse_ctg

    i = _idx(doc, "FxDigit22")
    parsed = parse_ctg(path)
    po = parsed.page.objects[i]
    gh = G.parse_geom_header(po.body_raw)
    l, t, r, _b = gh.rect1
    PL.write_header_geom(po, gh.anchor, (l, t, r, t + 48), gh.rect2)
    page = reader.derive_page(parsed, path)[0]
    sc = PageScene()
    sc.set_page(page)
    obj, it = page.objects[i], sc._items[i]
    assert obj.style.text == "8" and obj.geometry.extent_y == 48
    assert (
        obj.geometry.extent_y
        != textmetrics.font_metrics(obj.style.font, obj.style.font_size, 0x10 if obj.style.bold else 0)[1]
    )
    assert (
        abs(it.pos().y() - (obj.geometry.y + (obj.geometry.extent_y - it.boundingRect().height()) / 2)) < 0.01
    )
