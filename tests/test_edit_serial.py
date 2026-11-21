"""Every edit bumps edit_serial, which the Simulate window watches."""

import pytest


@pytest.fixture
def doc(fx_page):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page("FxShapes"))


def _rect(doc):
    hits = doc.find_objects_by_name("FxRect")
    assert len(hits) == 1
    i = hits[0]
    assert doc.page.objects[i].kind.name == "RECTANGLE"
    assert i in doc.page.roots()
    return i


def test_a_move_bumps_the_edit_serial(doc):
    i = _rect(doc)
    before = (doc.edit_serial, doc.derive_serial)
    doc.move_objects([i], 40, 0)
    assert doc.edit_serial > before[0]
    n = doc.edit_serial
    doc.undo()
    assert doc.edit_serial > n


def test_a_colour_edit_bumps_the_edit_serial(doc):
    i = _rect(doc)
    n = doc.edit_serial
    doc.set_colour(i, 0x00FF00)
    assert doc.edit_serial > n
