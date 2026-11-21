"""Mixer page visuals: captions, icon buttons, text children, colours."""

import os
import shutil

import pytest


@pytest.fixture
def kinds(fx_page):
    """(document, page) of FxTextKinds as the reader derives it."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    path = fx_page("FxTextKinds")
    return EditableDocument.open(path), read_page(path)


def _one(doc, name):
    found = doc.find_objects_by_name(name)
    assert len(found) == 1, (name, found)
    return found[0]


def test_multi_label_captions_resolve(fx_page, tmp_path):
    """'@(A) @(B) @(C)' resolves EVERY wrapper, not the 'abc' string-display placeholder.
    The test writes the two shapes into a copy of FxTextKinds:
    FxTxtOffLabel's display OFF text (TXTMODE 0) and FxLabelOne's plain caption."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    path = str(tmp_path / "FxTextKinds.ctg")
    shutil.copy2(fx_page("FxTextKinds"), path)
    doc = EditableDocument.open(path)
    off, plain = _one(doc, "FxTxtOffLabel"), _one(doc, "FxLabelOne")
    assert doc.set_text_display(
        off, [("TXTMODE", "0"), ("TXT_A", "FX_PUMP1"), ("TXT_OFF", "@(Fx Tank) @(Fx Part) @(Fx Total)")]
    )
    assert doc.set_text(plain, "@(Fx Line) @(Fx Part) @(Fx Total)")
    doc.save()
    page = read_page(path)
    assert page.objects[off].style.text == "Fx Tank Fx Part Fx Total"
    assert page.objects[plain].style.text == "Fx Line Fx Part Fx Total"
    assert page.objects[off].style.text != "abc" and page.objects[plain].style.text != "abc"


def test_multi_line_text_reads_as_new_lines(fx_page):
    """Multi-line native strings store CR LF on disk; the reader normalises it to '\\n'
    for clean rendering (display only: the writer uses the raw bytes). FxShapes'
    FxMultiLine is 'Line one' CR LF 'Line two'."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.reader import read_page

    path = fx_page("FxShapes")
    i = _one(EditableDocument.open(path), "FxMultiLine")
    assert read_page(path).objects[i].style.text == "Line one\nLine two"


def test_icon_face_button_resolves(kinds):
    """TEXT='#fxsym.fx_bitmap' = an icon-face button (FxIconButton): the element's bitmap
    renders on the face."""
    doc, page = kinds
    icon = page.objects[_one(doc, "FxIconButton")].raw.get("_button_icon")
    assert icon is not None
    w, h, rgba, _a = icon
    assert w > 0 and h > 0 and len(rgba) == w * h * 4


def test_text_record_with_children_keeps_text_kind(kinds):
    """A tc=0x05 text that OWNS a child (its Touch input) draws its own art: the '##.#'
    caption of FxTouchNumber."""
    doc, page = kinds
    i = _one(doc, "FxTouchNumber")
    obj = page.objects[i]
    assert obj.raw.get("_type_code") == 0x05
    assert obj.kind.value == "text"
    assert obj.style.text == "##.#"
    assert obj.members  # still a forest container
    # and the scene actually paints it
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.render.scene import PageScene

    scene = PageScene()
    scene.set_page(page, grid_size=0)
    item = scene._items.get(i)
    assert item is not None
    # not the invisible container anchor: it has visible content
    assert item.childItems() or hasattr(item, "text")


def test_state0_text_colour(kinds):
    """A COL_STA-driven text rests in STATE 0 (FxColStaText '##': COL_STA_0 red, as an
    alarm count is in CtDraw's canvas)."""
    doc, page = kinds
    st = page.objects[_one(doc, "FxColStaText")].style
    assert st.text == "##"
    assert st.text_colour == 0x00FF0000


def test_topbar_login_print_not_overprinted(kinds):
    """Three stacked 'Login...' and three 'Print...' texts with runtime-exclusive states
    (FxLogin1 .. 3, FxPrint1 .. 3: VISIBLE FX_MODE = 0, 1, 2): only the topmost draws, the
    rest are dup-suppressed."""
    doc, page = kinds
    for stem in ("FxLogin", "FxPrint"):
        stack = [_one(doc, "%s%d" % (stem, k)) for k in (1, 2, 3)]
        text = page.objects[stack[0]].style.text
        assert text in ("Login...", "Print...")
        assert all(page.objects[i].style.text == text for i in stack)
        drawn = [i for i in stack if not page.objects[i].raw.get("_dup_suppressed")]
        assert drawn == [stack[-1]], "exactly one %s should draw" % text


def test_topbar_username_field_is_not_hidden_by_a_function_gate(kinds):
    """A field gated by a function (FxLoginGate: VISIBLE FxExpanded(), drawn over the
    'Login...' stack's row), which CtDraw's editor never evaluates: the live editor DRAWS
    the field, so it must not be rest-hidden."""
    doc, page = kinds
    obj = page.objects[_one(doc, "FxLoginGate")]
    assert obj.style.text, "the gated record should still parse"
    assert obj.raw.get("_rest_visible") is None
    assert not obj.raw.get("_dup_suppressed")


# --------------------------------------------------------------------------- CtDraw's canvas
def _ctdraw_canvas():
    """CtDraw's own canvas of FxTextKinds (the oracle's canvas.png: the page's top left at
    the canvas's top left, 100 %)."""
    import fxinstall

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtGui, QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    img = QtGui.QImage()
    assert img.loadFromData(fxinstall.oracle_bytes("pages", "FxMain/FxTextKinds", "canvas.png"))
    return img


def _pixels(img, obj):
    """The (r, g, b) of every canvas pixel inside the object's box."""
    g = obj.geometry
    out = []
    for y in range(int(g.y), int(g.y + g.extent_y)):
        for x in range(int(g.x), int(g.x + g.extent_x)):
            c = img.pixelColor(x, y)
            out.append((c.red(), c.green(), c.blue()))
    return out


def _near(a, b, tol=40):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def _rgb(colour):
    colour &= 0xFFFFFF
    return (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF


@pytest.mark.golden
def test_ctdraw_draws_the_text_shapes_the_reader_keeps(kinds):
    """The facts the tests above encode, against CtDraw's canvas of FxTextKinds: the
    '##.#' of FxTouchNumber (a text owning a Touch child) and the function-gated
    FxLoginGate are drawn in their text colour (black on the page's 0xD8D8D8), and the
    COL_STA text FxColStaText is drawn in its state-0 red."""
    doc, page = kinds
    img = _ctdraw_canvas()
    for name in ("FxTouchNumber", "FxLoginGate"):
        obj = page.objects[_one(doc, name)]
        ink = _rgb(obj.style.text_colour or 0)
        assert any(_near(p, ink) for p in _pixels(img, obj)), name
    red = page.objects[_one(doc, "FxColStaText")]
    assert red.style.text_colour == 0x00FF0000
    assert any(_near(p, (255, 0, 0)) for p in _pixels(img, red))


@pytest.mark.golden
def test_ctdraw_draws_the_icon_on_the_button_face(kinds):
    """FxIconButton (TEXT '#fxsym.fx_bitmap'): CtDraw draws the element's bitmap on the
    face, so some colour of the bitmap our reader decodes is inside the button's box."""
    doc, page = kinds
    img = _ctdraw_canvas()
    obj = page.objects[_one(doc, "FxIconButton")]
    w, h, rgba, _a = obj.raw["_button_icon"]
    colours = {tuple(rgba[k : k + 3]) for k in range(0, w * h * 4, 4) if rgba[k + 3] > 200}
    box = set(_pixels(img, obj))
    face = img.pixelColor(int(obj.geometry.x) + 3, int(obj.geometry.y) + 3)
    colours.discard((face.red(), face.green(), face.blue()))
    assert colours and colours & box
