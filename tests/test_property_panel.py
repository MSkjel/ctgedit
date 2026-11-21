"""The Properties panel matches the ribbon."""

import os

import pytest

#: the page record tests' page (pages.DBF TITLE, no SCANTIME, no PREV / NEXT)
RECORD_PAGE = "FxAssoc"
RECORD_TITLE = "Fx genie host"


@pytest.fixture
def shapes(fx_page):
    """FxMain's FxShapes in the fx_install copy."""
    return fx_page("FxShapes")


def _panel_doc(path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.properties import PropertyPanel

    doc = EditableDocument.open(path)
    return PropertyPanel(), doc


def test_panel_edits_shape_stroke(shapes):
    panel, doc = _panel_doc(shapes)
    p = doc.create_object("pipe", None, [(50, 600), (150, 600)])
    panel.set_target(doc, [p])
    panel._on_line_width(p, 11)
    assert doc.page.objects[p].style.line_width == 11
    panel._on_line_style(p, 2)
    assert doc.page.objects[p].style.line_style == 2


def test_panel_edits_fill(shapes):
    panel, doc = _panel_doc(shapes)
    r = doc.create_object("rectangle", (10, 10, 100, 80), None, colour=0x00808080)
    panel.set_target(doc, [r])
    assert doc.page.objects[r].style.filled
    panel._on_filled(r, False)
    assert not doc.page.objects[r].style.filled


def test_panel_edits_font(shapes):
    from PySide6 import QtGui

    panel, doc = _panel_doc(shapes)
    t = doc.create_object("text", (10, 10, 120, 30), None, text="Hi")
    panel.set_target(doc, [t])
    panel._on_font_size(t, 22)
    assert doc.page.objects[t].style.font_size == 22
    panel._on_font_face(t, QtGui.QFont("Verdana"))
    assert doc.page.objects[t].style.font == "Verdana"
    panel._on_font_flag(t, "bold", True)
    assert doc.page.objects[t].style.bold
    panel._on_font_flag(t, "italic", True)
    assert doc.page.objects[t].style.italic


def test_panel_builds_every_kind_without_error(shapes, fx_page):
    """Constructing the panel for each object kind must not raise: FxShapes' primitives,
    groups, symbols and buttons, FxGenies' genies, FxLegacy's trend, V3 / V4 objects and
    ActiveX controls, FxSymbols' symbol sets."""
    from ctgedit.editing import EditableDocument

    panel, doc = _panel_doc(shapes)
    seen = set()
    for path in (shapes, fx_page("FxGenies"), fx_page("FxLegacy"), fx_page("FxSymbols")):
        d = doc if path == shapes else EditableDocument.open(path)
        for i, o in enumerate(d.page.objects):
            if o.kind.value in seen:
                continue
            seen.add(o.kind.value)
            panel.set_target(d, [i])
        panel.set_target(d, [])  # page mode
        panel.set_target(d, [1, 2])  # multi
    assert len(seen) >= 8 and "genie" in seen, sorted(seen)


def _form_rows(box):
    from PySide6 import QtWidgets

    form = box.layout()
    rows = {}
    for r in range(form.rowCount()):
        lab = form.itemAt(r, QtWidgets.QFormLayout.LabelRole)
        fld = form.itemAt(r, QtWidgets.QFormLayout.FieldRole)
        if lab is not None and fld is not None:
            rows[lab.widget().text()] = fld.widget()
    return rows


def test_page_rows_edit_the_page_record(shapes, fx_page, fx_project):
    """Page mode: Title / Description / Area / Scan time / Previous / Next
    page are the page's pages.DBF fields (staged, written on save), not
    attributes list keys; the page colour is a ColourButton."""
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.colour_picker import ColourButton
    from ctgedit.ui.pick_fields import PageField

    panel, _doc = _panel_doc(shapes)
    doc = EditableDocument.open(fx_page(RECORD_PAGE))
    panel.set_target(doc, [])
    root = panel.widget()
    box = next(b for b in root.findChildren(QtWidgets.QGroupBox) if b.title() == "Page details")
    rows = _form_rows(box)
    assert list(rows) == [
        "Title",
        "Description",
        "Area",
        "Scan time",
        "Previous page",
        "Next page",
        "Parent page",
        "Content type",
    ]
    assert rows["Title"].text() == RECORD_TITLE
    assert isinstance(rows["Previous page"], PageField)
    assert rows["Scan time"].suffix() == " ms" and rows["Scan time"].text() == "Default"
    assert not any(b.title() == "All page properties" for b in root.findChildren(QtWidgets.QGroupBox))
    assert any(isinstance(w, ColourButton) for w in root.findChildren(QtWidgets.QToolButton))
    n = len(doc._undo)
    rows["Title"].setText("Mixing edited")
    rows["Title"].editingFinished.emit()
    assert doc.page_record()["TITLE"] == "Mixing edited" and len(doc._undo) == n + 1
    assert Project(fx_project("FxMain")).page_record(RECORD_PAGE)["TITLE"] == RECORD_TITLE
    # a value the field cannot store is refused with a message, nothing staged
    msgs = []
    panel.statusMessage.connect(msgs.append)
    panel._on_page_record("TITLE", "x" * 200)
    assert msgs and doc.page_record()["TITLE"] == "Mixing edited"


def test_expression_and_command_rows_are_expression_fields(shapes):
    """Every expression row (Hidden when, movement, fill conditions, raw
    expression keys) and the touch command rows are ExpressionFields sharing
    one ExpressionContext."""
    from PySide6 import QtWidgets
    from ctgedit.ui.objprops.expression import ExpressionField

    panel, doc = _panel_doc(shapes)
    r = doc.create_object("rectangle", (10, 10, 100, 80), None, colour=0x00808080)
    for k, v in (
        ("VISIBLE", "Tag1 > 5"),
        ("MVX", "Tag2"),
        ("COL_A", "Run"),
        ("COL_MAXON", "0x00ff0000,0x00ff0000"),
    ):
        assert doc.ensure_property(r, k, v)
    assert doc.set_input_commands(r, touch={"CMD": "Beep()"}, label="touch command")
    panel.set_target(doc, [r])
    fields = panel.widget().findChildren(ExpressionField)
    values = {f.value() for f in fields}
    assert {"Tag1 > 5", "Tag2", "Run", "Beep()"} <= values
    assert len({id(f._context) for f in fields}) == 1
    cmd = next(f for f in fields if f.value() == "Beep()")
    assert cmd._kind == "command"
    assert not any(w.completer() is not None for w in panel.widget().findChildren(QtWidgets.QLineEdit))


def test_scan_time_steps_up_from_default_to_the_projects_scan_time(shapes, fx_page, fx_project):
    """The page's Scan time row: 'Default' is no SCANTIME; the up arrow
    starts at the project's default scan time (not at 49 ms)."""
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import default_scan_time

    panel, _doc = _panel_doc(shapes)
    doc = EditableDocument.open(fx_page(RECORD_PAGE))
    panel.set_target(doc, [])
    box = next(b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Page details")
    spin = _form_rows(box)["Scan time"]
    assert spin.value() == -1 and spin.text() == "Default"
    spin.stepBy(1)
    assert spin.value() == int(default_scan_time())
    spin.stepBy(1)
    assert spin.value() == int(default_scan_time()) + spin.singleStep()
    spin.editingFinished.emit()
    assert doc.page_record()["SCANTIME"] == str(spin.value())


def test_display_value_rows_are_named_for_the_type(shapes):
    """The Display value box names TXT_A as Object Properties does for the
    display type (On / Off 'ON text when', Array 'Array expression',
    Multi-state 'Condition A') and shows the type, not the raw TXTMODE."""
    from PySide6 import QtWidgets

    panel, doc = _panel_doc(shapes)
    for mode, want in (("0", "ON text when"), ("1", "Condition A"), ("2", "Array expression")):
        t = doc.create_object("text", (10, 10, 120, 30), None, text="Hi")
        assert doc.ensure_property(t, "TXTMODE", mode)
        assert doc.ensure_property(t, "TXT_A", "Tag1")
        panel.set_target(doc, [t])
        box = next(
            b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Display value"
        )
        rows = _form_rows(box)
        assert want in rows and "Mode" not in rows, (mode, list(rows))
        assert rows["Type"].text() == {"0": "On / Off", "1": "Multi-state", "2": "Array"}[mode]


def test_page_form_is_kept_per_document_and_never_moved(shapes):
    """A deselect shows the document's kept page form in its place; another document shows its
    own kept form; a released document's form goes, and all of them when no page is shown."""
    import gc
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument

    panel, doc = _panel_doc(shapes)
    other = EditableDocument.open(shapes)
    obj = next(r for r in doc.page.roots() if not doc.page.objects[r].raw.get("_non_drawable"))
    panel.set_target(doc, [])
    form_a = panel.widget()
    assert any(b.title() == "Page details" for b in form_a.findChildren(QtWidgets.QGroupBox))
    assert form_a.parentWidget() is panel._stack
    panel.set_target(doc, [obj])
    assert panel.widget() is not form_a
    assert not any(b.title() == "Page details" for b in panel.widget().findChildren(QtWidgets.QGroupBox))
    panel.set_target(doc, [])
    assert panel.widget() is form_a and form_a.parentWidget() is panel._stack

    # the panel is as tall as the form shown, not as its tallest kept form
    # (in height, at a dock's width: the form follows the dock's width, its own width hint
    # is ignored)
    def tall(w):
        h = w.heightForWidth(300)
        return h if h >= 0 else w.sizeHint().height()

    assert tall(panel._stack) == tall(form_a)
    panel.set_target(other, [])
    form_b = panel.widget()
    assert form_b is not form_a and panel.page_form_count() == 2
    panel.set_target(doc, [])
    assert panel.widget() is form_a
    panel.set_target(other, [])
    assert panel.widget() is form_b
    panel.set_target(doc, [obj])
    del other, form_b
    gc.collect()
    panel.set_target(doc, [])
    assert panel.page_form_count() == 1 and panel.widget() is form_a
    panel.set_target(None, [])
    assert panel.page_form_count() == 0


def test_a_page_record_edit_builds_the_page_form_again(shapes, fx_page, fx_project):
    """The kept form follows what it shows: a staged page field change builds it again."""
    from PySide6 import QtWidgets
    from ctgedit.editing import EditableDocument

    panel, _doc = _panel_doc(shapes)
    doc = EditableDocument.open(fx_page(RECORD_PAGE))
    panel.set_target(doc, [])
    form = panel.widget()
    box = next(b for b in form.findChildren(QtWidgets.QGroupBox) if b.title() == "Page details")
    rows = _form_rows(box)
    assert not rows["Previous page"].is_filled()  # (listed when it opens)
    rows["Title"].setText("Edited title")
    rows["Title"].editingFinished.emit()
    panel.set_target(doc, [])
    assert panel.widget() is not form and panel.page_form_count() == 1
    box = next(b for b in panel.widget().findChildren(QtWidgets.QGroupBox) if b.title() == "Page details")
    assert _form_rows(box)["Title"].text() == "Edited title"


def test_a_move_sets_the_position_fields_in_place(shapes):
    """After moves only (a nudge, its undo / redo) the X / Y fields are set in place;
    any other edit builds the form again. A selection of several objects keeps the
    position a typed X moves from."""
    panel, doc = _panel_doc(shapes)
    r = doc.create_object("rectangle", (10, 10, 100, 80), None)
    panel.set_target(doc, [r])
    form = panel.widget()

    def xy():
        return panel._geom["x"].value(), panel._geom["y"].value()

    def model(i):
        g = doc.page.objects[i].geometry
        return g.x, g.y

    doc.move_objects([r], 7, 3)
    panel.set_target(doc, [r])
    assert panel.widget() is form and xy() == model(r)
    doc.undo()
    panel.set_target(doc, [r])
    assert panel.widget() is form and xy() == model(r) == (10, 10)
    doc.redo()
    panel.set_target(doc, [r])
    assert panel.widget() is form and xy() == model(r) == (17, 13)
    assert doc.set_line_width(r, 3)
    panel.set_target(doc, [r])
    assert panel.widget() is not form and xy() == model(r)
    # several objects
    b = doc.create_object("rectangle", (200, 50, 260, 90), None)
    panel.set_target(doc, [r, b])
    form = panel.widget()
    _roots, sx, sy, _size = panel._multi_geom
    assert (sx.value(), sy.value()) == (17, 13)
    doc.move_objects([r, b], 5, 0)
    panel.set_target(doc, [r, b])
    assert panel.widget() is form and (sx.value(), sx.property("base")) == (22, 22)
    sx.setValue(32)
    sx.editingFinished.emit()  # moves both by 10, from where they are now
    assert model(r) == (32, 13) and model(b)[0] == 215


def test_dropped_forms_survive_the_panel_and_a_flush_of_deferred_deletes(shapes):
    """Forms dropped (deleteLater) while still under the panel, then the panel deleted by
    PySide: the next flush of the deferred deletes does not crash, as they leave the panel
    before their deleteLater."""
    import gc
    from PySide6 import QtCore, QtWidgets
    from ctgedit.editing import EditableDocument

    def run():
        panel, doc = _panel_doc(shapes)
        other = EditableDocument.open(shapes)
        obj = next(r for r in doc.page.roots() if not doc.page.objects[r].raw.get("_non_drawable"))
        for d, sel in ((doc, []), (doc, [obj]), (other, []), (doc, [obj, obj + 1])):
            panel.set_target(d, sel)
        panel.set_target(None, [])  # (every form dropped)
        assert panel.page_form_count() == 0

    run()
    gc.collect()
    app = QtWidgets.QApplication.instance()
    app.sendPostedEvents(None, int(QtCore.QEvent.DeferredDelete.value))
    gc.collect()


def test_a_narrow_field_shows_its_value_from_the_start_and_whole_in_its_tip():
    """At a large text size each field shows its value from the start, and one too narrow for it
    has the whole value as its tip."""
    from PySide6 import QtWidgets

    from ctgedit.ui.panels.properties_parts import show_value_starts

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    form = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(form)
    narrow = QtWidgets.QLineEdit()
    narrow.setFixedWidth(40)
    narrow.setToolTip("The page's title")
    narrow.setText("FxShapes and a long title")
    wide = QtWidgets.QLineEdit("Short")
    wide.setFixedWidth(300)
    lay.addWidget(narrow)
    lay.addWidget(wide)
    try:
        form.show()
        show_value_starts(form)
        assert narrow.cursorPosition() == 0
        assert narrow.toolTip() == "FxShapes and a long title\nThe page's title"
        assert wide.toolTip() == ""
    finally:
        form.deleteLater()


def test_an_object_form_shown_again_is_the_kept_one_while_the_page_is_unchanged(shapes):
    """A selection that comes back to an object shows the form built for it; an edit, a field
    holding an edit not made, refresh() and a change of the window's activation (a dialog,
    another program) each drop the kept forms, and at most KEPT_FORMS are kept."""
    from PySide6 import QtCore, QtWidgets

    panel, doc = _panel_doc(shapes)
    a = doc.create_object("rectangle", (10, 10, 100, 80), None)
    b = doc.create_object("rectangle", (200, 50, 260, 90), None)
    panel.set_target(doc, [a])
    form_a, geom_a = panel.widget(), dict(panel._geom)
    panel.set_target(doc, [b])
    form_b = panel.widget()
    assert form_b is not form_a and panel.kept_form_count() == 1
    panel.set_target(doc, [a])
    assert panel.widget() is form_a and panel._geom == geom_a and panel.kept_form_count() == 1
    assert (panel._geom["x"].value(), panel._geom["y"].value()) == (10, 10)
    # the shown form's state is its own: a move sets its fields in place
    doc.move_objects([a], 4, 0)
    panel.set_target(doc, [a])
    assert panel.widget() is form_a and panel._geom["x"].value() == 14
    # the move changed the page: B's kept form is not shown again
    panel.set_target(doc, [b])
    assert panel.widget() is not form_b and panel.kept_form_count() == 1
    form_b = panel.widget()
    # a field holding an edit not made (typed, not applied): its form is not kept
    panel.set_target(doc, [a])
    form_a = panel.widget()
    panel._geom["x"].lineEdit().setText("123")
    assert panel._keys.modified(panel._geom["x"])
    panel.set_target(doc, [b])
    assert panel.widget() is form_b
    panel.set_target(doc, [a])
    assert panel.widget() is not form_a and panel._geom["x"].value() == 14
    # refresh() and an activation change drop them
    panel.set_target(doc, [b])
    assert panel.kept_form_count() >= 1
    panel.refresh()
    assert panel.kept_form_count() == 0
    panel.set_target(doc, [a])
    panel.set_target(doc, [b])
    assert panel.kept_form_count() == 1
    QtWidgets.QApplication.sendEvent(panel, QtCore.QEvent(QtCore.QEvent.ActivationChange))
    assert panel.kept_form_count() == 0
    # at most KEPT_FORMS
    more = [doc.create_object("rectangle", (10 + 30 * k, 200, 30 + 30 * k, 230), None) for k in range(6)]
    for r in more:
        panel.set_target(doc, [r])
    assert panel.kept_form_count() == panel.KEPT_FORMS
    # no document: none kept
    panel.set_target(None, [])
    assert panel.kept_form_count() == 0
