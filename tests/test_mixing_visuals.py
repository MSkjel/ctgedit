"""Colour rules from the Mixing page."""

import os

import pytest

import fxinstall
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


@pytest.fixture
def colours(fx_page):
    """FxColours as the editor reads it: (doc, find), find(name) = the object."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxColours"))

    def find(name):
        found = doc.find_objects_by_name(name)
        assert len(found) == 1, name
        return doc.page.objects[found[0]]

    return doc, find


def test_flat_pair_on_animated_object_is_unfilled(colours, tmp_path):
    """The flat COL_MAXON pair of an animated shape never paints: FxEllipseMaxon (MVX)
    rests at its block grey, not the pair's ffcc00. A hollow shape with a flat pair +
    SZX / DISABLED stays hollow: the test adds that one to a copy
    of FxColours (a rectangle_outline with the pair 969696, FxFlatPair's keys)."""
    doc, find = colours
    obj = find("FxEllipseMaxon")
    assert obj.kind.value == "ellipse"
    assert obj.style.filled and obj.style.fill_colour == 0x00808080, hex(obj.style.fill_colour or 0)
    # FxFlatPair: its block is the same grey as its pair: it rests at the block
    obj = find("FxFlatPair")
    assert obj.style.filled and obj.style.fill_colour == 0x00969696
    # a hollow shape with the same keys is not filled by its pair
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    i = doc.create_object("rectangle_outline", (480, 200, 540, 240), None)
    pair = "0x00969696,0x00969696"
    assert doc.set_fill_keys(i, [("COL_MAXON", pair)]) is not False
    for k, v in (("SZX", "FX_LEVEL1"), ("SZXMINPC", "0"), ("SZXMAXPC", "100"), ("DISABLED", "FX_MODE = 0")):
        assert doc.ensure_property(i, k, v)
    assert doc.set_object_name(i, "FxHollowPair")
    path = str(tmp_path / "FxColoursHollow.ctg")
    doc.save(path)
    page = read_page(path)
    (k,) = EditableDocument.open(path).find_objects_by_name("FxHollowPair")
    assert (page.objects[k].raw.get("_props") or {}).get("COL_MAXON") == pair
    assert not page.objects[k].style.filled


def test_literal_col_a_does_not_paint_col_maxon(colours):
    """A literal COL_A doesn't paint COL_MAXON."""
    _doc, find = colours
    obj = find("FxPennant")
    st = obj.style
    assert st.filled and st.fill_colour == 0x00FFFFFF, hex(st.fill_colour or 0)
    assert st.line_colour == 0x00969696, hex(st.line_colour or 0)
    assert len(obj.geometry.nodes) == 3, "pennant vertices not grafted"
    st = find("FxPennantShadow").style
    assert st.filled and st.fill_colour == 0x00C0C0C0, hex(st.fill_colour or 0)


def test_state_array_shapes_rest_at_their_style_block(colours):
    """State array shapes rest at their style block."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer as W
    from ctgedit.io.reader import read_page

    doc, find = colours
    for name in ("FxCm1", "FxCm2"):
        st = find(name).style
        assert st.filled and st.fill_colour == 0x00FF0000, (name, hex(st.fill_colour or 0))
    (i,) = doc.find_objects_by_name("FxCm2")
    assert doc.parsed.page.objects[i].subtype_size & 0x2  # it has a style block
    assert doc.set_colour(i, 0x808080)
    assert W.set_object_property(doc.parsed.page.objects[i], "COL_ARR_0", "0x00ff0000,0x00ff0000")
    with open(doc.path, "wb") as fh:
        fh.write(doc.serialize())
    again = EditableDocument.open(doc.path)
    assert again.object_properties(i)["COL_ARR_0"] == "0x00ff0000,0x00ff0000"  # state 0 red
    st = read_page(doc.path).objects[i].style
    assert st.filled and st.fill_colour == 0x00808080, hex(st.fill_colour or 0)


def test_target_buttons_and_ai_indicators_render(colours, fx_page, tmp_path):
    """Target buttons and alarm indicators render."""
    _app()
    from PySide6 import QtCore
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    doc, _find = colours
    page = read_page(doc.path)
    scene = PageScene()
    scene.set_page(page, grid_size=0)
    (i,) = doc.find_objects_by_name("FxTargetButton")
    item = scene._items.get(i)
    assert item is not None
    assert item.brush().style() == QtCore.Qt.NoBrush
    # the alarm indicators
    alarms = EditableDocument.open(fx_page("FxAlarms"))
    on = ["FxAi%02d" % k for k in range(1, 31, 3)] + ["FxAi30"]
    for k in range(1, 31):
        (g,) = alarms.find_objects_by_name("FxAi%02d" % k)
        assert alarms.alarm_indicator(g)["has"], k  # the fixture's: all on
        if "FxAi%02d" % k not in on:
            assert alarms.set_alarm_indicator(g, {"has": False}), k
    path = str(tmp_path / "FxAlarmsOn.ctg")
    alarms.save(path)
    page = read_page(path)
    scene = PageScene()
    scene.set_page(page, grid_size=0)
    want = {
        i
        for i, o in enumerate(page.objects)
        if o.raw.get("_type_code") == 0x08
        and str((o.raw.get("_props") or {}).get("AI_ON", "")).strip() == "1"
    }
    zero = {
        i
        for i, o in enumerate(page.objects)
        if o.raw.get("_type_code") == 0x08
        and str((o.raw.get("_props") or {}).get("AI_ON", "")).strip() == "0"
    }
    assert len(want) == 11 and len(zero) == 19
    assert want == set(scene._ai_items), (sorted(want), sorted(scene._ai_items))


def test_dynamics_badges_off_by_default(fx_page, monkeypatch):
    """No dynamics glyph items (a green '#', a red 'C' ...) unless opted in, on FxMain's
    FxAnim (every animation key)."""
    monkeypatch.delenv("CTG_DYN_BADGES", raising=False)
    _app()
    from ctgedit.io.reader import read_page
    from ctgedit.render.scene import PageScene

    page = read_page(fx_page("FxAnim"))
    scene = PageScene()
    scene.set_page(page, grid_size=0)
    assert scene._items  # (the page drew)
    glyphs = [
        it
        for it in scene.items()
        if hasattr(it, "text")
        and callable(it.text)
        and it.text() in ("V", "M", "C", "L", "S", "T", "K")
        and it.font().pointSize() == 6
    ]
    assert not glyphs


@pytest.mark.golden
def test_the_resting_colours_are_what_ctdraw_draws(fx_page):
    """CtDraw's own canvas of FxColours (the oracle's canvas.png, the page's top left at
    the canvas's top left, 100 %): the pixel inside each shape is the rest colour our
    model gives it. FxPennantShadow is drawn over FxPennant (2 px down and right), so the
    shadow is sampled at its centroid and the pennant on its uncovered left strip."""
    from PySide6 import QtGui
    from ctgedit.editing import EditableDocument

    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", "FxMain/FxColours", "canvas.png"))
    doc = EditableDocument.open(fx_page("FxColours"))

    def at(name, x, y):
        (i,) = doc.find_objects_by_name(name)
        want = (doc.page.objects[i].style.fill_colour or 0) & 0xFFFFFF
        c = img.pixelColor(x, y)
        assert (c.red(), c.green(), c.blue()) == ((want >> 16) & 0xFF, (want >> 8) & 0xFF, want & 0xFF), name

    at("FxPennantShadow", 42, 117)
    at("FxPennant", 21, 115)
    at("FxEllipseMaxon", 590, 40)
    at("FxCm2", 210, 40)
