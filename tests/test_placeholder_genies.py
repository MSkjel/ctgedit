"""Placeholder genies whose library was missing at save."""

import os

import pytest

#: the genies the placeholders stand for
KEYS = {("fxback", "fx_value"), ("fxback", "fx_plain")}


@pytest.fixture
def page(fx_install):
    """FxReturned of the fake install's copy (fxback.ctm sits beside it in FxMain)."""
    return fx_install.page("FxReturned")


def test_placeholders_are_placed_in_their_box(page):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import reader

    reader.set_label_mode("literal")  # (the editor's look)
    doc = EditableDocument.open(page)
    found = doc.placeholder_genies()
    assert len(found) == 4
    assert {(lib, el) for _i, lib, el, *_ in found} == KEYS
    boxes = [doc.page.objects[i].geometry for i, *_ in found]
    boxes = [(g.x, g.y, g.extent_x, g.extent_y) for g in boxes]
    n0 = len(doc.parsed.page.objects)
    assert doc.instantiate_placeholder_genies() == 4
    assert doc.placeholder_genies() == []
    placed = [i for i, key in doc.genie_instances() if key in KEYS]
    got = sorted((g.x, g.y, g.extent_x, g.extent_y) for g in (doc.page.objects[i].geometry for i in placed))
    assert got == sorted(boxes)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(doc.page)
    drawn = {it.text() for it in sc.items() if hasattr(it, "text") and callable(it.text)}
    for n in (1, 2):
        assert any("Fx Brann %d" % n in t for t in drawn)
    again = EditableDocument.from_bytes(doc.serialize(), doc.path)
    assert len(again.parsed.page.objects) == len(doc.parsed.page.objects)
    assert again.placeholder_genies() == []
    doc.undo()
    assert len(doc.parsed.page.objects) == n0


def test_opening_the_page_places_them_as_an_automatic_step(page):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        w.open_files([page])
        app.processEvents()
        assert w.doc.placeholder_genies() == []
        assert w.doc.dirty
        # (then the genie update of the open brings the page's other copies current too)
        assert "place genies" in [u.label for u in w.doc._undo]
    finally:
        w.doc.dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()
        app.processEvents()


# ------------------------------------------------------------------ CtDraw's placeholders
def _scene_texts(page):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.render.scene import PageScene

    sc = PageScene()
    sc.set_page(page)
    return {it.text() for it in sc.items() if hasattr(it, "text") and callable(it.text)}


@pytest.mark.golden
def test_ctdraw_written_placeholders_are_placed(fx_install):
    """Placeholders CtDraw wrote are placed."""
    import shutil
    import fxinstall
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import reader

    data = fxinstall.oracle_bytes("pages", "FxMain/FxMissing", "page.ctg")
    main = fx_install.project("FxMain")
    path = os.path.join(main, "FxMissingCtDraw.ctg")
    with open(path, "wb") as fh:
        fh.write(data)
    shutil.copyfile(fx_install.file("FxLib", "fxgenie.ctm"), os.path.join(main, "fxgone.ctm"))
    libraries_changed()
    reader.set_label_mode("literal")
    doc = EditableDocument.open(path)
    found = doc.placeholder_genies()
    want = {("fxgone", "fx_value"), ("fxgone", "fx_plain")}
    assert {(lib, el) for _i, lib, el, *_ in found} == want  # (fx_thin resolves nowhere)
    boxes = sorted(
        (g.x, g.y, g.extent_x, g.extent_y)
        for g in (doc.page.objects[i].geometry for i, lib, el, *_ in found if (lib, el) in want)
    )
    assert doc.instantiate_placeholder_genies() == 2
    assert doc.placeholder_genies() == []
    left = [
        po.raw_properties().get("_Library")
        for po in doc.parsed.page.objects
        if po.type_code == 0xFFFF and not po.class_flag and po.raw_properties().get("_Library")
    ]
    assert len(left) == 1 and "fx_thin" in str(left[0]).lower(), left  # still a placeholder
    placed = [i for i, key in doc.genie_instances() if key in want]
    got = sorted((g.x, g.y, g.extent_x, g.extent_y) for g in (doc.page.objects[i].geometry for i in placed))
    assert got == boxes
    assert any("Fx Stop" in t for t in _scene_texts(doc.page))


@pytest.mark.golden
def test_returned_placement_matches_ctdraws(page):
    """CtDraw's PageOpen + PageSave of FxReturned (the oracle) re-places its four
    placeholders (fxback.ctm is there): its genie instances of KEYS have the same boxes as
    our instantiate_placeholder_genies() gives."""
    import fxinstall
    from ctgedit.editing import EditableDocument

    data = fxinstall.oracle_bytes("pages", "FxMain/FxReturned", "page.ctg")
    theirs = EditableDocument.from_bytes(data, page)
    assert theirs.placeholder_genies() == []

    def boxes(doc):
        return sorted(
            (g.x, g.y, g.extent_x, g.extent_y)
            for g in (doc.page.objects[i].geometry for i, key in doc.genie_instances() if key in KEYS)
        )

    ours = EditableDocument.open(page)
    assert ours.instantiate_placeholder_genies() == 4
    assert len(boxes(theirs)) == 4
    assert boxes(theirs) == boxes(ours)
