"""New objects carry only what CtDraw's new objects carry."""

import pytest

import fxinstall

KINDS = ("rectangle", "rectangle_outline", "ellipse", "ellipse_outline")


def _new_shapes(fx_page):
    """The four plain shapes made new on FxShapes: [(kind, index)] and the document."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    made = []
    for n, kind in enumerate(KINDS):
        i = doc.create_object(kind, rect=(20, 2000 + 70 * n, 100, 2060 + 70 * n), colour=0x336699)
        made.append((kind, i))
    return doc, made


def test_new_plain_shapes_have_an_empty_bag(fx_page):
    """A plain new rectangle / ellipse stores no colour animation (no lone COL_MAXON) and no
    property-sheet state (no '!*Appearance'): CtDraw's own static shapes carry neither."""
    doc, made = _new_shapes(fx_page)
    for kind, i in made:
        po = doc.parsed.page.objects[i]
        assert doc.object_properties(i) == {}, kind
        assert po.subtype_size == 0xA, kind
        # the static colour is still the object's own (style block), not an animation
        assert doc.page.objects[i].style.fill_colour in (0x336699, None), kind


@pytest.mark.golden
def test_new_plain_shapes_bag_region_is_ctdraws(fx_page, tmp_path):
    """A bagless new shape's bag region (anim_off .. cs_off) is the one CtDraw writes
    for a rectangle it draws itself with no properties (oracle draw/rectangle)."""
    from ctgedit.format import placement
    from ctgedit.format.page import parse_ctg

    data = fxinstall.oracle_bytes("draw", "rectangle", "page.ctg")
    ref = tmp_path / "ctdraw_rect.ctg"
    ref.write_bytes(data)
    corpus = next(
        po
        for po in parse_ctg(str(ref)).page.objects
        if po.type_code == 0x0F and po.subtype_size == 0xA and not po.raw_properties()
    )
    cw = placement.walk(corpus)
    want = corpus.body_raw[cw["anim_off"] : cw["cs_off"]]
    doc, made = _new_shapes(fx_page)
    for kind, i in made:
        po = doc.parsed.page.objects[i]
        w = placement.walk(po)
        assert po.body_raw[w["anim_off"] : w["cs_off"]] == want, kind
