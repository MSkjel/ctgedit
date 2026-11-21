"""The look of new objects."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app


@pytest.fixture
def shapes(fx_page):
    """FxShapes in the test's fx_install copy."""
    return fx_page("FxShapes")


def _doc(tmp_path, src, name="a"):
    """A copy of ``src`` in its own folder ``name`` of tmp_path, opened (two documents of
    one test never share a file)."""
    _app()
    from ctgedit.editing import EditableDocument

    d = tmp_path / name
    d.mkdir(exist_ok=True)
    shutil.copy(src, d)
    return EditableDocument.open(str(d / os.path.basename(src)))


class _Page:
    """A page of a given colour (draw_styles reads appearance.background_colour)."""

    class appearance:  # noqa: N801
        background_colour = None

    def __init__(self, colour):
        self.appearance = type("A", (), {"background_colour": colour})()


LIGHT, DARK = _Page(0xF1F1F1), _Page(0x202428)


def _clean():
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    for k in (
        DS.STYLE_KEY,
        DS.LAST_KEY,
        DS.MODE_KEY,
        DS.FILL_KEY,
        DS.LINE_KEY,
        DS.TEXT_KEY,
        DS.COLOUR_MODE_KEY,
        DS.NO_FILL_KEY,
    ):
        st.remove(k)


@pytest.fixture(autouse=True)
def _fresh():
    _app()
    _clean()
    yield
    _clean()


# ------------------------------------------------------------------ the colours
def test_new_objects_contrast_with_the_page():
    from ctgedit.ui.editor import draw_styles as DS

    assert DS.page_is_light(LIGHT) and not DS.page_is_light(DARK)
    assert DS.page_is_light(_Page(None)) and DS.page_is_light(_Page(0xFF000000))
    t = DS.style_for("text", "text", LIGHT)
    assert t["text_colour"] == 0x202020 and "fill_colour" not in t
    r = DS.style_for("rectangle", "rectangle", LIGHT)
    assert (r["fill_colour"], r["line_colour"]) == (0xC0C0C0, 0x202020)
    assert DS.style_for("line", "line", LIGHT) == {"line_colour": 0x202020}
    d = DS.style_for("rectangle", "rectangle", DARK)
    assert (d["fill_colour"], d["line_colour"]) == (0x404040, 0xE0E0E0)
    assert DS.style_for("text", "text", DARK)["text_colour"] == 0xE0E0E0
    # the one colour a tool passes to create_object (what the canvas still sends)
    assert DS.creation_colour("text", LIGHT) == 0x202020
    assert DS.creation_colour("rectangle", LIGHT) == 0xC0C0C0
    assert DS.creation_colour("rectangle_outline", LIGHT) == 0x202020
    assert DS.creation_colour("pipe", DARK) == 0xE0E0E0
    assert DS.creation_colour("button", LIGHT) is None


def test_a_picked_colour_is_kept_and_the_others_keep_what_they_showed():
    from ctgedit.ui.editor import draw_styles as DS

    assert DS.colour_mode() == "auto"
    DS.set_colour("fill", 0x3A7BD5, LIGHT)
    assert DS.colour_mode() == "fixed"
    c = DS.colours(DARK)  # (fixed: the page no longer decides)
    assert c == {"fill": 0x3A7BD5, "line": 0x202020, "text": 0x202020}
    DS.swap_fill_and_line(DARK)
    assert DS.colours(DARK)["fill"] == 0x202020 and DS.colours(DARK)["line"] == 0x3A7BD5
    DS.set_colour_mode("auto")
    assert DS.colours(DARK) == {"fill": 0x404040, "line": 0xE0E0E0, "text": 0xE0E0E0}


def test_no_fill_draws_shapes_hollow():
    from ctgedit.ui.editor import draw_styles as DS

    DS.set_no_fill(True)
    r = DS.style_for("rectangle", "rectangle", LIGHT)
    assert r["filled"] is False and "fill_colour" not in r
    assert DS.creation_colour("ellipse", LIGHT) == DS.colours(LIGHT)["line"]
    DS.set_colour("fill", 0x00FF00, LIGHT)  # (a fill colour fills again)
    assert DS.style_for("rectangle", "rectangle", LIGHT)["fill_colour"] == 0x00FF00


def test_the_outline_tools_stay_hollow():
    from ctgedit.ui.editor import draw_styles as DS

    DS.set_kind_style("rectangle", {"filled": True, "line_width": 2})
    o = DS.style_for("rectangle", "rectangle_outline", LIGHT)
    assert o["filled"] is False and "fill_colour" not in o and o["line_width"] == 2
    assert DS.style_for("rectangle", "rectangle", LIGHT)["filled"] is True


# ------------------------------------------------------------------ the styles
def test_a_tool_keeps_its_own_style():
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.prefs.settings import app_settings

    DS.set_tool_field("rectangle", "line_width", 2)
    DS.set_tool_field("rectangle", "corner_radius", 4)
    DS.set_tool_field("rectangle", "font", "Arial")  # (not a rectangle's field)
    assert DS.tool_style("rectangle") == {"line_width": 2, "corner_radius": 4}
    import json

    assert json.loads(app_settings().value(DS.STYLE_KEY)) == {
        "rectangle": {"corner_radius": 4, "line_width": 2}
    }
    s = DS.style_for("rectangle", "rectangle", LIGHT)
    assert s["line_width"] == 2 and s["corner_radius"] == 4
    assert DS.tool_style("ellipse") == {}
    shown = DS.shown_values("rectangle", "rectangle", LIGHT)
    assert shown["line_style"] == 0 and shown["filled"] is True  # (the factory look)
    DS.set_tool_field("rectangle", "line_width", None)
    assert DS.tool_style("rectangle") == {"corner_radius": 4}
    DS.reset_tool("rectangle")
    assert DS.tool_style("rectangle") == {} and DS._json(DS.STYLE_KEY) == {}


def test_last_used_takes_the_look_of_the_last_object_changed():
    from ctgedit.ui.editor import draw_styles as DS

    DS.set_tool_field("text", "font_size", 20)
    DS.record_last(
        "text", {"font": "Calibri", "font_size": 11, "bold": True, "line_width": 7, "text_colour": 0x0000FF}
    )
    assert DS.style_for("text", "text", LIGHT)["font_size"] == 20  # (the tool's own)
    DS.set_mode("last")
    s = DS.style_for("text", "text", LIGHT)
    assert s["font"] == "Calibri" and s["bold"] is True and s["text_colour"] == 0x0000FF
    assert "line_width" not in s
    DS.set_mode("tool")


def test_kinds_and_tools():
    from ctgedit.ui.editor import draw_styles as DS

    assert DS.kind_of_tool("ellipse_outline") == "ellipse"
    assert DS.kind_of_tool("symbol") is None and DS.kind_of_tool("select") is None
    assert set(DS.tools_of_kind("rectangle")) == {"rectangle", "rectangle_outline"}
    assert DS.label("rectangle") == "Rectangle" and DS.label("text", True) == "Texts"


# ------------------------------------------------------------------ the records
def test_the_factory_look_is_what_the_tools_draw(tmp_path, shapes):
    from ctgedit.ui.editor import draw_styles as DS

    doc = _doc(tmp_path, shapes)
    for kind, tool in (
        ("line", "line"),
        ("pipe", "pipe"),
        ("rectangle", "rectangle"),
        ("ellipse", "ellipse"),
        ("polygon", "polygon"),
        ("text", "text"),
        ("button", "button"),
    ):
        nodes = [(10, 10), (60, 40), (90, 10)] if kind in ("pipe", "polygon") else None
        kw = {"text": "Abc"} if kind in ("text", "button") else {}
        i = doc.create_object(tool, (10, 10, 110, 60), nodes, **kw)
        got = doc.appearance_of(i)
        want = DS.FACTORY[kind]
        assert {k: got[k] for k in want} == want, kind


def test_a_styled_object_is_drawing_it_and_pasting_that_appearance(tmp_path, shapes):
    """The record a tool's style gives (style_for, applied at commit) is the record of the
    same object drawn plain and given that appearance with Paste Appearance."""
    from ctgedit.ui.editor import draw_styles as DS

    DS.set_kind_style("rectangle", {"line_width": 2, "corner_radius": 4, "line_style": 1})
    DS.set_colour("line", 0x112233, LIGHT)
    a = _doc(tmp_path, shapes, "a")
    b = _doc(tmp_path, shapes, "b")
    style = DS.style_for("rectangle", "rectangle", a.page)
    assert style["line_width"] == 2 and style["line_colour"] == 0x112233
    ia = a.create_object(
        "rectangle", (40, 40, 140, 100), None, colour=DS.creation_colour("rectangle", a.page)
    )
    a.apply_appearance([ia], style, label="add rectangle")
    ib = b.create_object(
        "rectangle", (40, 40, 140, 100), None, colour=DS.creation_colour("rectangle", b.page)
    )
    b.apply_appearance([ib], a.appearance_of(ia))  # (Paste Appearance)
    assert a.parsed.page.objects[ia].body_raw == b.parsed.page.objects[ib].body_raw
    assert a.appearance_of(ia)["corner_radius"] == 4


def test_a_pipes_own_lowlight(tmp_path, shapes):
    """apply_appearance's lolight_colour (the new-pipe style only): the pipe's lowlight
    slot, after its colour; appearance_of never gives it (Copy / Paste Appearance as they
    were) and a shape ignores it."""
    from ctgedit.format import colourstate, geometry as G

    a = _doc(tmp_path, shapes, "a")
    b = _doc(tmp_path, shapes, "b")
    nodes = [(10, 10), (80, 10), (80, 60)]
    ia = a.create_object("pipe", None, nodes)
    ib = b.create_object("pipe", None, nodes)
    assert "lolight_colour" not in a.appearance_of(ia)
    a.apply_appearance([ia], {"line_colour": 0x3366CC, "lolight_colour": 0x112244})
    b.set_colour(ib, 0x3366CC, line=True)
    b.set_colour_slot(ib, G.SLOT_LOLIGHT, 0x112244, label="lowlight colour")
    assert a.parsed.page.objects[ia].body_raw == b.parsed.page.objects[ib].body_raw
    po = a.parsed.page.objects[ia]
    L = colourstate.layout(po)
    import struct

    lo = struct.unpack_from("<II", po.body_raw, L["style_at"] + 6 + 8 * G.SLOT_LOLIGHT)
    assert lo == (0x112244, 0x112244)
    r = a.create_object("rectangle", (0, 0, 50, 50), None)
    before = bytes(a.parsed.page.objects[r].body_raw)
    a.apply_appearance([r], {"lolight_colour": 0x112244})
    assert bytes(a.parsed.page.objects[r].body_raw) == before


def test_the_kind_of_a_page_object(tmp_path, shapes):
    from ctgedit.ui.editor import draw_styles as DS

    doc = _doc(tmp_path, shapes)
    t = doc.create_object("text", (10, 10, 60, 30), None, text="Hi")
    n = doc.create_object("number", (10, 40, 60, 60), None, text="Tag1")
    r = doc.create_object("rectangle", (0, 0, 20, 20), None)
    assert DS.kind_of_object(doc.page.objects[t]) == "text"
    assert DS.kind_of_object(doc.page.objects[n]) == "number"
    assert DS.kind_of_object(doc.page.objects[r]) == "rectangle"
