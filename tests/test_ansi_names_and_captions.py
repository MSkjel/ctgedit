"""ANSI object names and empty captions."""

from qtkit import app as _app


def test_object_name_with_ansi_letters_keeps_its_geometry(fx_page, monkeypatch):
    """A geometry-header object name with a code-page letter ('Rør': CtDraw's name rule
    forbids only punctuation) is read as a name, so the rect survives a rename and a reload.
    The host page is the fake install's FxShapes (FxMain); the rectangle is the test's own."""
    monkeypatch.setenv("CTG_NO_CTF_PRECHECK", "1")
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.format import geometry as G

    doc = EditableDocument.open(fx_page("FxShapes"))
    i = doc.create_object("rectangle", rect=(100, 100, 160, 140), colour=0x00FF00)
    i = i if isinstance(i, int) else len(doc.page.objects) - 1
    doc.set_object_name(i, "R\u00f8r")
    gh = G.parse_geom_header(parse_ctg_bytes(doc.serialize()).page.objects[i].body_raw)
    assert tuple(gh.rect1) == (100, 100, 160, 140) and gh.name == "R\u00f8r"
    g = doc.page.objects[i].geometry
    assert (g.x, g.y, g.extent_x, g.extent_y) == (100, 100, 60, 40)


def test_txtmode0_empty_caption_measures_txt_on():
    """mode 0: an EMPTY caption measures the TXT_ON text; a caption measures
    itself."""
    from ctgedit.format import textmetrics

    class _Po:
        def __init__(self, props):
            self._p = props

        def raw_properties(self):
            return self._p

    po = _Po({"TXTMODE": "0", "TXT_ON": "FX.11-PV07", "TXT_OFF": "off"})
    assert textmetrics.measured_string(po, "") == "FX.11-PV07"
    assert textmetrics.measured_string(po, "Pump") == "Pump"
    assert textmetrics.measured_string(_Po({"TXTMODE": "0"}), "") == " "


def test_null_pen_gradient_rect_fills_its_inclusive_extent():
    """A gradient face with no pen covers (l, t, r + 1, b + 1) like CtDraw's fill; a solid
    null-pen fill keeps (l, t, r, b)."""
    _app()
    from PySide6 import QtCore
    from ctgedit.model.document import GraphicObject, Geometry, ObjectKind, Style
    from ctgedit.render.scene import PageScene

    sc = PageScene()

    def rect_item(grad):
        st = Style(filled=True, fill_colour=0x999999, line_style=5)
        if grad:
            st.gradient_mode, st.gradient_colour = 3, 0xBFBFBF
        o = GraphicObject(kind=ObjectKind.RECTANGLE, geometry=Geometry(10, 20, 30, 12), style=st)
        o.raw["_dwflags"] = 0x18
        return sc._make_item(o)

    g = rect_item(True)
    assert g.pen().style() == QtCore.Qt.NoPen and g.brush().gradient() is not None
    assert (g.rect().width(), g.rect().height()) == (31, 13)
    s = rect_item(False)
    assert (s.rect().width(), s.rect().height()) == (30, 12)
