"""The Properties panel."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app

#: the page most tests run on (name, project) and the registered page on a template
HOST = ("FxMany", "FxMain")
FORM_PAGE = ("FxTplLinked", "FxMain")


#: the project tables a test copies with its page
TABLES = ("pages.dbf", "contype.dbf", "include.dbf")


def _copy(tmp_path, src, tables=TABLES):
    """A copy of the page with some of its project's tables."""
    shutil.copy(src, tmp_path)
    folder = os.path.dirname(src)
    for f in os.listdir(folder):
        if f.lower() in tables:
            shutil.copy(os.path.join(folder, f), tmp_path)
    return str(tmp_path / os.path.basename(src))


def _doc(tmp_path, src, tables=TABLES):
    _app()
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(_copy(tmp_path, src, tables))


def _panel(width=300):
    _app()
    from ctgedit.ui.panels.properties import PropertyPanel

    p = PropertyPanel()
    p.resize(width, 900)
    p.show()
    p.activateWindow()
    _app().processEvents()
    return p


def _show(panel, doc, sel):
    panel.set_target(doc, sel)
    for _ in range(3):
        _app().processEvents()
    return panel.widget()


def _named(doc, name):
    [i] = doc.find_objects_by_name(name)
    return i


def _text(doc):
    """A top-level text of FxMany (FxManyText01)."""
    i = _named(doc, "FxManyText01")
    assert doc.page.objects[i].kind.value == "text" and doc.page.objects[i].parent is None
    return i


def _field(panel, key, cls=QtWidgets.QWidget):
    return next(w for w in panel.widget().findChildren(cls) if w.property("propKey") == key)


# ------------------------------------------------------------------ 1. fits its dock
def _member(doc):
    """A member of a placed genie (shown read-only)."""
    from ctgedit.ui.objprops.registry import read_only_reason

    return next(
        i for i, o in enumerate(doc.page.objects) if o.parent is not None and read_only_reason(doc, i)
    )


@pytest.mark.parametrize("width", [200, 220, 260, 360])
def test_the_panel_fits_its_dock(tmp_path, width, fx_page):
    from ctgedit.ui.panels.properties_parts import panel_is_field

    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel(width)
    composite = next(i for i in doc.page.roots() if doc.page.objects[i].is_genie)
    shapes = [
        i
        for i in doc.page.roots()
        if doc.page.objects[i].kind.value in ("rectangle", "ellipse", "line", "polygon", "pipe")
    ][:4]
    cases = {
        "page": [],
        "text": [_text(doc)],
        "composite": [composite],
        "shapes": shapes,
        "mixed": doc.page.roots()[:40],
        "member": [_member(doc)],
    }
    for name, sel in cases.items():
        root = _show(panel, doc, sel)
        vw = panel.viewport().width()
        assert root.minimumSizeHint().width() <= vw, (name, width)
        for w in root.findChildren(QtWidgets.QWidget):
            if panel_is_field(w) and w.isVisibleTo(root):
                right = w.mapTo(root, QtCore.QPoint(w.width(), 0)).x()
                assert right <= vw - 4, (name, width, type(w).__name__, w.property("propKey"))
    # under 300 px the labels go above the fields (Automatic), from 300 px beside them
    assert panel.label_mode() == ("above" if width < 300 else "beside")


def test_label_layout_setting(tmp_path, fx_page):
    from ctgedit.ui.panels.properties_parts import PanelSection

    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel(360)
    _show(panel, doc, [_text(doc)])
    panel.set_label_layout("above")
    forms = [s.form() for s in panel.widget().findChildren(PanelSection)]
    assert all(f.rowWrapPolicy() == QtWidgets.QFormLayout.WrapAllRows for f in forms)
    panel.set_label_layout("beside")
    assert all(f.rowWrapPolicy() == QtWidgets.QFormLayout.WrapLongRows for f in forms)
    panel.set_label_layout("auto")


def test_the_dock_opens_300_wide():
    from ctgedit.ui.panels.properties import PropertyPanel

    _app()
    assert PropertyPanel().sizeHint().width() == 300


def test_long_labels_are_short_with_the_long_text_in_the_tip(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    for k, v in (
        ("MVX", "T1"),
        ("MVXMINPT", "0"),
        ("MVXMAXPT", "500"),
        ("ROT", "T2"),
        ("ROTMINPC", "0"),
        ("ROTMAXPC", "360"),
        ("SZX", "T3"),
        ("SZXMINPC", "0"),
        ("SZXMAXPC", "100"),
    ):
        doc.ensure_property(r, k, v)
    panel = _panel(360)
    _show(panel, doc, [r])
    sec = panel.section("Movement / Scaling")
    labels = [lab.text() for lab in sec.findChildren(QtWidgets.QLabel)]
    for short in ("Min offset", "Max offset", "Min angle", "Max angle", "Min size", "Max size"):
        assert short in labels
    assert "Offset at minimum (left)" in _field(panel, "MVXMINPT").toolTip()


# ------------------------------------------------------------------ 2. the font
def test_typing_a_font_name_applies_it_once(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    t = _text(doc)
    panel = _panel()
    _show(panel, doc, [t])
    fc = _field(panel, "font", QtWidgets.QFontComboBox)
    n = len(doc._undo)
    fc.setFocus()
    fc.lineEdit().selectAll()
    QTest.keyClicks(fc.lineEdit(), "Verdana")
    assert len(doc._undo) == n  # nothing while typing
    QTest.keyClick(fc.lineEdit(), QtCore.Qt.Key_Return)
    _app().processEvents()
    assert doc.page.objects[t].style.font == "Verdana"
    assert len(doc._undo) == n + 1


def test_a_font_that_does_not_exist_is_not_written(tmp_path, fx_page):
    from ctgedit.ui.style import theme

    doc = _doc(tmp_path, fx_page(*HOST))
    t = _text(doc)
    panel = _panel()
    msgs = []
    panel.statusMessage.connect(msgs.append)
    _show(panel, doc, [t])
    face = doc.page.objects[t].style.font
    fc = _field(panel, "font", QtWidgets.QFontComboBox)
    n = len(doc._undo)
    panel._on_font_typed(t, fc, "Nofontcalledthis")
    assert doc.page.objects[t].style.font == face and len(doc._undo) == n
    assert theme.is_invalid(fc) and fc.currentText() == face
    assert msgs and "No font called 'Nofontcalledthis'." in msgs[-1]
    panel._on_font_typed(t, fc, "verd")  # the first family starting with it
    assert doc.page.objects[t].style.font == "Verdana" and not theme.is_invalid(fc)


def test_several_texts_take_one_font_in_one_step(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    texts = [i for i, o in enumerate(doc.page.objects) if o.kind.value == "text" and o.parent is None][:3]
    panel = _panel()
    _show(panel, doc, texts)
    fc = (
        _field(panel, "font", QtWidgets.QFontComboBox)
        if any(w.property("propKey") == "font" for w in panel.widget().findChildren(QtWidgets.QWidget))
        else panel.widget().findChildren(QtWidgets.QFontComboBox)[0]
    )
    n = len(doc._undo)
    panel._on_multi_font(texts, fc, "Verdana")
    assert len(doc._undo) == n + 1
    assert all(doc.page.objects[i].style.font == "Verdana" for i in texts)
    panel._on_multi_font(texts, fc, "Verdana")
    assert len(doc._undo) == n + 1  # nothing new


# ------------------------------------------------------------------ 3. Add animation
def _add_combo(panel):
    """The 'Add animation…' field, its entries listed (they come with its first use)."""
    combo = next(
        c for c in panel.widget().findChildren(QtWidgets.QComboBox) if c.objectName() == "addAnimation"
    )
    combo.fill()
    assert combo.itemText(0) == "Add animation…"
    return combo


def _add(panel, index, label):
    combo = _add_combo(panel)
    pos = combo.findText(label)
    assert pos > 0, (label, [combo.itemText(k) for k in range(combo.count())])
    panel._on_add_animation(index, combo, pos)
    for _ in range(4):
        _app().processEvents()
        QTest.qWait(10)


def test_add_animation_shows_and_focuses_its_section(tmp_path, monkeypatch, fx_page):
    from ctgedit.ui.panels import properties_parts as PP_parts
    from ctgedit.ui.objprops.input import KeySequenceDialog

    monkeypatch.setattr(KeySequenceDialog, "get_sequence", staticmethod(lambda *a, **k: "F7"))
    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel(320)
    for label, key, _default in PP_parts.ADDABLE:
        if key == PP_parts.DISPLAY_VALUE:
            continue  # (a text's: below)
        r = doc.create_object("rectangle", (40, 40, 140, 90), None)
        _show(panel, doc, [r])
        _add(panel, r, label)
        title, _fkey = PP_parts.ADDED_SECTION[key]
        sec = panel.section(title)
        assert sec is not None, label
        fw = panel.focusWidget()
        assert fw is not None and sec.isAncestorOf(fw), (label, fw)
    # a text: Display value shows its numeric expression, focused
    t = doc.create_object("text", (10, 10, 120, 30), None, text="Hi")
    _show(panel, doc, [t])
    _add(panel, t, "Display value")
    sec = panel.section("Display value")
    assert sec is not None and sec.isAncestorOf(panel.focusWidget())
    assert doc.object_properties(t).get("TXTMODE") == "3"


def test_an_empty_movement_shows_its_rows(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    panel = _panel()
    _show(panel, doc, [r])
    _add(panel, r, "Movement (horizontal)")
    assert doc.object_properties(r)["MVX"] == ""
    assert _field(panel, "MVX").hasFocus()
    assert _field(panel, "MVXMINPT") is not None  # its offsets show with it


def test_fill_level_writes_what_the_sheet_writes(tmp_path, fx_page):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    doc = _doc(tmp_path, fx_page(*HOST))
    a = doc.create_object("rectangle", (40, 40, 140, 90), None, colour=0x336699)
    b = doc.create_object("rectangle", (40, 40, 140, 90), None, colour=0x336699)
    from ctgedit.format import writer

    def differ():
        pa = writer.object_payload(doc.parsed.page.objects[a])
        pb = writer.object_payload(doc.parsed.page.objects[b])
        return len(pa) - len(pb), [k for k in range(min(len(pa), len(pb))) if pa[k] != pb[k]]

    before = differ()  # (their ANs: the handle bytes)
    panel = _panel()
    _show(panel, doc, [a])
    n = len(doc._undo)
    _add(panel, a, "Fill level")
    assert len(doc._undo) == n + 1  # one undo step
    ed = _field(panel, "FIL")
    assert ed.hasFocus()
    ed.setText("Tank_Level")
    ed.editingFinished.emit()
    dlg = ObjectPropertiesDialog(doc, b)
    dlg.show_page("fill_level")
    f, w = dlg.field_entries("FIL")[0]
    w.setText("Tank_Level")
    assert dlg.apply()
    pa, pb = doc.object_properties(a), doc.object_properties(b)
    assert list(pa.items()) == list(pb.items())
    assert differ() == before  # the same bytes but the AN


# ------------------------------------------------------------------ 4. Esc / Ctrl+Z
def test_esc_puts_the_field_back(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (346, 40, 446, 90), None)
    panel = _panel()
    _show(panel, doc, [r])
    x = _field(panel, "x")
    x.setFocus()
    x.lineEdit().selectAll()
    QTest.keyClicks(x, "500")
    QTest.keyClick(x, QtCore.Qt.Key_Escape)
    assert x.value() == 346 and x.text() == "346 px"
    assert doc.page.objects[r].geometry.x == 346
    cap_t = doc.create_object("text", (10, 10, 120, 30), None, text="Skim Milk")
    _show(panel, doc, [cap_t])
    cap = _field(panel, "caption")
    cap.setFocus()
    QTest.keyClicks(cap, "XYZ")
    QTest.keyClick(cap, QtCore.Qt.Key_Escape)
    assert cap.text() == "Skim Milk"


def test_ctrl_z_in_a_field_after_enter_undoes(tmp_path, fx_page):
    from ctgedit.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        win.resize(1400, 900)
        win.show()
        win.activateWindow()
        win.open_document(_copy(tmp_path, fx_page(*HOST)))
        _app().processEvents()
        doc = win.doc
        r = doc.create_object("rectangle", (346, 40, 446, 90), None)
        win._after_edit()
        win._on_canvas_selection([r])
        _app().processEvents()
        win.props.set_target(doc, [r])
        _app().processEvents()
        x = _field(win.props, "x")
        x.setFocus()
        x.lineEdit().selectAll()
        QTest.keyClicks(x, "400")
        QTest.keyClick(x, QtCore.Qt.Key_Return)
        _app().processEvents()
        assert doc.page.objects[r].geometry.x == 400
        QTest.qWait(20)
        fw = QtWidgets.QApplication.focusWidget()
        target = fw if fw is not None and win.props.isAncestorOf(fw) else _field(win.props, "x")
        QTest.keyClick(target, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
        _app().processEvents()
        assert doc.page.objects[r].geometry.x == 346
    finally:
        win.doc.dirty = False
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


# ------------------------------------------------------------------ 5. number fields
def test_x_takes_a_sum_and_held_arrows_are_one_step(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (344, 40, 444, 90), None)
    panel = _panel()
    _show(panel, doc, [r])
    x = _field(panel, "x")
    x.setFocus()
    x.lineEdit().selectAll()
    QTest.keyClicks(x, "344+10")
    QTest.keyClick(x, QtCore.Qt.Key_Return)
    _app().processEvents()
    assert doc.page.objects[r].geometry.x == 354  # not 3441
    _show(panel, doc, [r])
    x = _field(panel, "x")
    x.setFocus()
    n = len(doc._undo)
    for _ in range(5):
        QTest.keyClick(x, QtCore.Qt.Key_Up)
    import time

    end = time.perf_counter() + 2
    while doc.page.objects[r].geometry.x != 359 and time.perf_counter() < end:
        QTest.qWait(20)
    assert doc.page.objects[r].geometry.x == 359 and len(doc._undo) == n + 1


def test_every_panel_spin_is_a_number_field(tmp_path, fx_page):
    from ctgedit.ui.number_field import NumberField

    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    for sel in (
        [],
        [_text(doc)],
        doc.page.roots()[:20],
        [doc.create_object("rectangle", (4, 4, 40, 40), None)],
    ):
        root = _show(panel, doc, sel)
        spins = root.findChildren(QtWidgets.QAbstractSpinBox)
        assert spins and all(isinstance(s, NumberField) for s in spins)


# ------------------------------------------------------------------ 6. animation numbers
def test_animation_numbers_are_validated(tmp_path, fx_page):
    from ctgedit.ui.style import theme
    from ctgedit.ui.number_field import NumberField
    from ctgedit.ui.objprops import motion as MO
    from ctgedit.ui.objprops.fields import number_range

    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    for k, v in (("MVX", "T1"), ("MVXMINPT", "0"), ("MVXMAXPT", "abc")):
        doc.ensure_property(r, k, v)
    panel = _panel()
    _show(panel, doc, [r])
    mn = _field(panel, "MVXMINPT")
    assert isinstance(mn, NumberField) and mn.suffix() == " px"
    mn.setFocus()
    mn.lineEdit().selectAll()
    QTest.keyClicks(mn, "abc")  # not taken
    QTest.keyClick(mn, QtCore.Qt.Key_Return)
    assert doc.object_properties(r)["MVXMINPT"] == "0"
    # a stored non-number: text in a framed field until a number replaces it
    mx = _field(panel, "MVXMAXPT")
    assert isinstance(mx, QtWidgets.QLineEdit) and theme.is_invalid(mx)
    mx.setText("xyz")
    mx.editingFinished.emit()
    assert doc.object_properties(r)["MVXMAXPT"] == "abc"
    mx.setText("250*2")
    mx.editingFinished.emit()
    assert doc.object_properties(r)["MVXMAXPT"] == "500"
    # the sheet and the panel share one range table
    for f in MO.MOVE_H.fields + MO.MOVE_ROT.fields + MO.SCALE_H.fields:
        if f.kind == "int":
            assert number_range(f.key)[:2] == (f.lo, f.hi) and number_range(f.key)[2] == f.suffix


# ------------------------------------------------------------------ 7. compact geometry
def test_reference_point_and_aspect_lock(tmp_path, fx_page):
    from ctgedit.ui.panels import properties_parts as PP_parts
    from ctgedit.ui.prefs.settings import app_settings

    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (0, 0, 100, 50), None)
    app_settings().setValue(PP_parts.REFERENCE_KEY, "centre")
    app_settings().setValue(PP_parts.LOCK_ASPECT_KEY, True)
    try:
        panel = _panel()
        _show(panel, doc, [r])
        assert (_field(panel, "x").value(), _field(panel, "y").value()) == (50, 25)
        assert panel._geom["chain"].isChecked()
        w = _field(panel, "w")
        n = len(doc._undo)
        w.setValue(200)
        w.editingFinished.emit()
        g = doc.page.objects[r].geometry
        assert (g.x, g.y, g.extent_x, g.extent_y) == (-50, -25, 200, 100)
        assert len(doc._undo) == n + 1
    finally:
        app_settings().setValue(PP_parts.REFERENCE_KEY, "top_left")
        app_settings().setValue(PP_parts.LOCK_ASPECT_KEY, False)


def test_a_line_shows_its_length_and_angle(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    ln = doc.create_object("line", None, [(300, 300), (400, 250)])
    panel = _panel()
    root = _show(panel, doc, [ln])
    sec = panel.section("Geometry")
    form = sec.form()
    rows = {
        form.itemAt(r, QtWidgets.QFormLayout.LabelRole).widget().text(): form.itemAt(
            r, QtWidgets.QFormLayout.FieldRole
        ).widget()
        for r in range(form.rowCount())
        if form.itemAt(r, QtWidgets.QFormLayout.LabelRole) is not None
    }
    assert rows["Length"].text() == "112 px" and rows["Angle"].text() == "26.6°"
    assert root is panel.widget()


def test_several_objects_scale_from_w_and_h(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    a = doc.create_object("rectangle", (0, 0, 100, 50), None)
    b = doc.create_object("rectangle", (100, 50, 200, 100), None)
    panel = _panel()
    _show(panel, doc, [a, b])
    _roots, _x, _y, (w, h) = panel._multi_geom
    assert (w.value(), h.value()) == (200, 100)
    n = len(doc._undo)
    w.setValue(400)
    w.editingFinished.emit()
    assert doc.selection_extent([a, b]) == (0, 0, 400, 100) and len(doc._undo) == n + 1


# ------------------------------------------------------------------ 8. identity rows
def test_name_an_and_tool_tip_rows(tmp_path, fx_page):
    from ctgedit.ui.style import theme

    doc = _doc(tmp_path, fx_page(*HOST))
    a = doc.create_object("rectangle", (0, 0, 100, 50), None)
    b = doc.create_object("rectangle", (200, 0, 300, 50), None)
    panel = _panel()
    _show(panel, doc, [a])
    name = _field(panel, "__AN_NAME__")
    n = len(doc._undo)
    name.setText("Tank1")
    name.editingFinished.emit()
    assert doc.object_name(a) == "Tank1" and len(doc._undo) == n + 1
    _show(panel, doc, [b])
    name = _field(panel, "__AN_NAME__")
    name.setFocus()
    QTest.keyClicks(name, "Tank1")  # (typed: checked as you type)
    assert theme.is_invalid(name) and name.problem_note.isVisibleTo(panel.widget())
    assert "already used" in name.problem_note.text()
    n = len(doc._undo)
    name.editingFinished.emit()
    assert doc.object_name(b) == "" and len(doc._undo) == n  # nothing committed
    tip = _field(panel, "COMMENT")
    tip.setText("Hello")
    tip.editingFinished.emit()
    assert doc.object_properties(b)["COMMENT"] == "Hello"


def test_identity_rows_are_hidden_for_a_genie_member(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    _show(panel, doc, [_member(doc)])
    assert panel.is_read_only()
    assert not any(
        w.property("propKey") == "__AN_NAME__" for w in panel.widget().findChildren(QtWidgets.QWidget)
    )


def test_the_an_is_editable_with_modify_an(tmp_path, fx_page):
    from ctgedit.ui.number_field import NumberField
    from ctgedit.ui.prefs.settings import set_option

    doc = _doc(tmp_path, fx_page(*HOST))
    a = doc.create_object("rectangle", (0, 0, 100, 50), None)
    panel = _panel()
    _show(panel, doc, [a])
    assert not any(
        isinstance(w, NumberField) and w.property("propKey") == "an"
        for w in panel.widget().findChildren(QtWidgets.QWidget)
    )
    set_option("modify_an", True)
    try:
        _show(panel, doc, [a])
        panel.refresh()
        an = _field(panel, "an")
        an.setValue(30000)
        an.editingFinished.emit()
        assert doc.page.objects[a].an == 30000
    finally:
        set_option("modify_an", False)


# ------------------------------------------------------------------ 9. sections
def test_remove_and_the_section_menu(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (0, 0, 100, 50), None)
    doc.ensure_property(r, "VISIBLE", "Hide_It")
    for k, v in (("MVX", "T1"), ("MVXMINPT", "0"), ("MVXMAXPT", "500")):
        doc.ensure_property(r, k, v)
    panel = _panel()
    opened = []
    panel.editPage.connect(lambda i, pid: opened.append((i, pid)))
    _show(panel, doc, [r])
    sec = panel.section("Visibility")
    menu = sec.header.menu_button.menu()
    menu.aboutToShow.emit()
    texts = [a.text() for a in menu.actions()]
    assert {"Open in Object Properties", "Remove", "Fold All Others"} <= set(texts)
    next(a for a in menu.actions() if a.text() == "Open in Object Properties").trigger()
    assert opened == [(r, "visibility")]
    n = len(doc._undo)
    next(a for a in menu.actions() if a.text() == "Remove").trigger()
    assert "VISIBLE" not in doc.object_properties(r) and len(doc._undo) == n + 1
    panel.refresh()
    sec = panel.section("Movement / Scaling")
    link = next(b for b in sec.findChildren(QtWidgets.QToolButton) if b.objectName() == "linkButton")
    link.click()
    assert opened[-1] == (r, "move_h")
    panel._remove_family(r, "motion")
    assert not {"MVX", "MVXMINPT", "MVXMAXPT"} & set(doc.object_properties(r))


def test_sections_fold_and_remember_it(tmp_path, fx_page):
    from ctgedit.ui.panels import properties_parts as PP_parts

    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (0, 0, 100, 50), None)
    panel = _panel()
    _show(panel, doc, [r])
    sec = panel.section("Appearance")
    sec.header.chevron.click()
    assert sec.folded() and "Appearance" in PP_parts.collapsed_sections()
    _show(panel, doc, [doc.create_object("ellipse", (0, 0, 40, 40), None)])
    assert panel.section("Appearance").folded()
    panel.section("Appearance").toggle()
    assert "Appearance" not in PP_parts.collapsed_sections()
    assert panel.section("All properties") is None or panel.section("All properties").folded()


# ------------------------------------------------------------------ 10. All properties
def test_all_properties_hides_the_graphics_builder_notes(tmp_path, fx_page):
    """A button with a Graphics Builder note ('!*PropSheet.Focus', the key CtDraw writes
    when its sheet closes) and VIS_MODE: the test adds both to a new button of FxMany (the
    fake install has no page with such notes)."""
    from ctgedit.ui.panels import properties_widgets as PP_widgets
    from ctgedit.ui.panels import properties_parts as PP_parts

    doc = _doc(tmp_path, fx_page(*HOST))
    i = doc.create_object("button", (40, 300, 140, 330), None, text="Fx note")
    assert doc.ensure_property(i, "!*PropSheet.Focus", "Appearance")
    if "VIS_MODE" not in doc.object_properties(i):
        assert doc.ensure_property(i, "VIS_MODE", "XP_STYLE")
    keys = doc.object_properties(i)
    assert "!*PropSheet.Focus" in keys and "VIS_MODE" in keys
    panel = _panel()
    _show(panel, doc, [i])
    sec = panel.section("All properties")
    form = sec.form()
    # folded by default: its rows come with the first unfold (a selection builds none)
    assert sec.folded() and not sec.is_filled() and form.rowCount() == 1
    sec.set_folded(False, remember=False)
    assert sec.is_filled() and form.rowCount() > 2
    labels = [
        form.itemAt(r, QtWidgets.QFormLayout.LabelRole).widget()
        for r in range(form.rowCount())
        if form.itemAt(r, QtWidgets.QFormLayout.LabelRole) is not None
    ]
    assert not any(lab.toolTip().startswith("!*") for lab in labels)
    vis = next(lab for lab in labels if lab.toolTip() == "VIS_MODE")
    assert vis.text().startswith("Mode")
    assert "VISIBLE" not in PP_widgets.sheet_labels() or PP_widgets.sheet_labels()["VISIBLE"] == "Hidden when"
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue(PP_parts.PRIVATE_KEYS_KEY, True)
    try:
        panel.refresh()
        sec = panel.section("All properties")
        sec.set_folded(False, remember=False)
        form = sec.form()
        tips = [
            form.itemAt(r, QtWidgets.QFormLayout.LabelRole).widget().toolTip()
            for r in range(form.rowCount())
            if form.itemAt(r, QtWidgets.QFormLayout.LabelRole) is not None
        ]
        assert any(t.startswith("!*") for t in tips)
    finally:
        app_settings().setValue(PP_parts.PRIVATE_KEYS_KEY, False)


# ------------------------------------------------------------------ 11. other details
def test_expression_fields_have_the_cicode_check(tmp_path, fx_page):
    from ctgedit.ui.objprops.expression import ExpressionField

    doc = _doc(tmp_path, fx_page(*HOST), tables=TABLES + ("variable.dbf",))
    r = doc.create_object("rectangle", (0, 0, 100, 50), None)
    doc.ensure_property(r, "VISIBLE", "")
    panel = _panel()
    _show(panel, doc, [r])
    ed = _field(panel, "VISIBLE", ExpressionField)
    assert ed.checker() is not None
    ed.setFocus()
    QTest.keyClicks(ed, "No_Such_Tag_Here")
    QTest.keyClick(ed, QtCore.Qt.Key_Return)
    _app().processEvents()
    assert any("not a tag in the project" in p.message for p in ed.problems())
    assert ed._advice_action is not None and ed._advice_action.isVisible()


def test_a_read_only_member_says_why_once(tmp_path, fx_page):
    """The banner says why; the Input section does not say it again."""
    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    from ctgedit.ui.objprops.registry import read_only_reason

    members = [i for i, o in enumerate(doc.page.objects) if o.parent is not None and read_only_reason(doc, i)]
    m = next((i for i in members if doc.input_commands(i)["touch"]), members[0])
    root = _show(panel, doc, [m])
    info = doc.input_commands(m)
    texts = [lab.text() for lab in root.findChildren(QtWidgets.QLabel)]
    assert any(panel._object_read_only(m) in t for t in texts)  # the banner
    if info["reason"]:
        assert info["reason"] not in texts  # said once
    if info["touch"]:
        assert panel.section("Input") is not None


def test_the_page_form(fx_page):
    """FxTplLinked (FxMain): a registered page on a linked template, opened where it is in
    the test's copy of the fake install (its project resolves the template)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.pick_fields import PageField

    _app()
    doc = EditableDocument.open(fx_page(*FORM_PAGE))
    panel = _panel()
    _show(panel, doc, [])
    w = _field(panel, "page_w")
    n = len(doc._undo)
    w.setValue(1400)
    w.editingFinished.emit()
    assert doc.page.appearance.width == 1400 and len(doc._undo) == n + 1
    _show(panel, doc, [])
    assert isinstance(_field(panel, "PARENT"), PageField)
    ct = _field(panel, "CON_TYPE", QtWidgets.QComboBox)
    ct.setEditText("L2")
    ct.lineEdit().editingFinished.emit()
    assert doc.page_record()["CON_TYPE"] == "L2"
    assert any(
        isinstance(lab, QtWidgets.QLabel) and lab.text().startswith("The template is")
        for lab in panel.widget().findChildren(QtWidgets.QLabel)
    )


def test_the_page_properties_button_is_the_menus_action(tmp_path, fx_page):
    from ctgedit.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        win.open_document(_copy(tmp_path, fx_page(*HOST)))
        win.props.set_target(win.doc, [])
        b = next(
            b
            for b in win.props.widget().findChildren(QtWidgets.QPushButton)
            if b.objectName() == "pagePropertiesButton"
        )
        hits = []
        win.act_page_props.triggered.disconnect()
        win.act_page_props.triggered.connect(lambda *_: hits.append(1))
        b.click()
        assert hits == [1] and b.text() == win.act_page_props.text().replace("&", "")
    finally:
        win.doc.dirty = False
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


def test_an_outside_the_range_or_taken_is_refused_not_clamped(tmp_path, fx_page):
    from ctgedit.ui.style import theme
    from ctgedit.ui.prefs.settings import set_option

    doc = _doc(tmp_path, fx_page(*HOST))
    a = doc.create_object("rectangle", (0, 0, 100, 50), None)
    b = doc.create_object("rectangle", (200, 0, 300, 50), None)
    an_a, an_b = doc.page.objects[a].an, doc.page.objects[b].an
    set_option("modify_an", True)
    try:
        panel = _panel()
        _show(panel, doc, [a])
        an = _field(panel, "an")
        n = len(doc._undo)
        an.setFocus()
        an.lineEdit().selectAll()
        QTest.keyClicks(an, "40000")
        assert "40000 is outside" in an.problem_note.text()
        QTest.keyClick(an, QtCore.Qt.Key_Return)
        _app().processEvents()
        assert doc.page.objects[a].an == an_a and len(doc._undo) == n  # not 31999
        assert an.value() == an_a and theme.is_invalid(an)
        assert "40000 is outside" in an.problem_note.text()
        # an AN another object has: refused, the field shows the stored AN again
        _show(panel, doc, [a])
        an = _field(panel, "an")
        an.setValue(an_b)
        an.editingFinished.emit()
        assert doc.page.objects[a].an == an_a and len(doc._undo) == n
        assert an.value() == an_a and an.orig() == an_a and an.problem_note.text()
        assert not an.revert()  # (nothing to put back)
    finally:
        set_option("modify_an", False)


def test_a_number_field_without_clamp_keeps_its_value():
    from ctgedit.ui.style import theme
    from ctgedit.ui.number_field import NumberField

    _app()
    f = NumberField(lo=1, hi=100, value=5, clamp=False)
    got = []
    f.valueCommitted.connect(got.append)
    f.lineEdit().setText("50*3")
    f.interpretText()
    f.editingFinished.emit()
    assert f.value() == 5 and got == [] and theme.is_invalid(f)
    assert "from 1 to 100" in f.toolTip()
    g = NumberField(lo=1, hi=100, value=5)  # (the default brings it in range)
    g.lineEdit().setText("50*3")
    g.interpretText()
    assert g.value() == 100


def test_an_untouched_font_that_is_not_installed_gives_no_error(tmp_path, fx_page):
    from ctgedit.ui.style import theme

    doc = _doc(tmp_path, fx_page(*HOST))
    t = _text(doc)
    doc.set_font(t, face="NoSuchFontXyz")
    panel = _panel()
    msgs = []
    panel.statusMessage.connect(msgs.append)
    _show(panel, doc, [t])
    fc = _field(panel, "font", QtWidgets.QFontComboBox)
    assert fc.currentText() == "NoSuchFontXyz"
    n = len(doc._undo)
    fc.setFocus()
    QTest.keyClick(fc.lineEdit(), QtCore.Qt.Key_Return)
    _app().processEvents()
    assert msgs == [] and not theme.is_invalid(fc) and len(doc._undo) == n
    # a typed name that is no font: said once, not once for each signal of the Enter
    fc.lineEdit().selectAll()
    QTest.keyClicks(fc.lineEdit(), "Nofontcalledthis")
    QTest.keyClick(fc.lineEdit(), QtCore.Qt.Key_Return)
    _app().processEvents()
    assert msgs == ["No font called 'Nofontcalledthis'."] and theme.is_invalid(fc)
    assert doc.page.objects[t].style.font == "NoSuchFontXyz" and len(doc._undo) == n


def test_no_folded_section_is_kept_across_a_restart():
    from ctgedit.ui.panels import properties_parts as PP_parts
    from ctgedit.ui.prefs import settings as S

    _app()
    st = S.app_settings()
    before = st.value(PP_parts.COLLAPSED_KEY)
    try:
        st.setValue(PP_parts.COLLAPSED_KEY, [])
        st.sync()
        # the next run reads the file again: QSettings wrote the empty list as @Invalid()
        ini = os.environ.get("CTG_SETTINGS_INI") or ""
        fresh = (
            QtCore.QSettings(ini, QtCore.QSettings.Format.IniFormat)
            if ini
            else QtCore.QSettings(S.ORG, S.APP)
        )
        held = S._LOCAL.settings
        S._LOCAL.settings = (ini, fresh)
        try:
            assert PP_parts.collapsed_sections() == []
        finally:
            S._LOCAL.settings = held
    finally:
        if before is None:
            st.remove(PP_parts.COLLAPSED_KEY)
        else:
            st.setValue(PP_parts.COLLAPSED_KEY, before)
    st.remove(PP_parts.COLLAPSED_KEY)
    assert PP_parts.collapsed_sections() == PP_parts.DEFAULT_COLLAPSED  # (never set: the default)


def test_a_stored_number_out_of_range_says_so(tmp_path, fx_page):
    from ctgedit.ui.style import theme

    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    for k, v in (("MVX", "T1"), ("MVXMINPT", "40000"), ("MVXMAXPT", "abc")):
        doc.ensure_property(r, k, v)
    panel = _panel()
    _show(panel, doc, [r])
    mn, mx = _field(panel, "MVXMINPT"), _field(panel, "MVXMAXPT")
    assert theme.is_invalid(mn) and theme.is_invalid(mx)
    assert "The stored value 40000 is outside -32767 to 32767" in mn.toolTip()
    assert "is not a number" in mx.toolTip()


def test_arrow_steps_waiting_for_the_pause_apply_before_a_rebuild(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (351, 40, 451, 90), None)
    other = doc.create_object("rectangle", (600, 40, 700, 90), None)
    panel = _panel()
    _show(panel, doc, [r])
    x = _field(panel, "x")
    x.setFocus()
    _app().processEvents()
    n = len(doc._undo)
    for _ in range(3):
        QTest.keyClick(x, QtCore.Qt.Key_Up)
    assert x.pending_steps() and doc.page.objects[r].geometry.x == 351
    panel.set_target(doc, [other])  # (the focus stays where it was)
    assert doc.page.objects[r].geometry.x == 354 and len(doc._undo) == n + 1


def test_a_selection_builds_no_hidden_rows_and_no_menus(tmp_path, fx_page):
    from ctgedit.ui.panels import properties_parts as PP_parts
    from ctgedit.ui.prefs.settings import app_settings

    doc = _doc(tmp_path, fx_page(*HOST))
    t = _text(doc)
    panel = _panel()
    _show(panel, doc, [t])
    secs = panel.widget().findChildren(PP_parts.PanelSection)
    assert secs
    # a header's menu comes with its first use (QToolButton's own menu() is still None)
    assert all(QtWidgets.QToolButton.menu(s.header.menu_button) is None for s in secs)
    assert secs[0].header.menu_button.menu() is not None
    # All properties, folded by default, has only its header until it is unfolded; its rows
    # then get what the other fields got (the panel's keys, the fit-the-dock rule)
    sec = panel.section("All properties")
    assert sec.form().rowCount() == 1
    sec.set_folded(False, remember=False)
    search = _field(panel, "@search")
    assert search.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Ignored
    # the reference point is one widget: a click on a square picks it, the arrows move it
    grid = panel.widget().findChild(PP_parts.RefGrid)
    got = []
    grid.changed.connect(got.append)
    try:
        QTest.mouseClick(grid, QtCore.Qt.LeftButton, pos=grid._square(2, 2).center())
        assert got == ["bottom_right"] and grid.current() == "bottom_right"
        grid.setFocus()
        QTest.keyClick(grid, QtCore.Qt.Key_Left)
        assert got[-1] == "bottom"
        assert grid.accessibleName() == "Reference point: bottom centre"
    finally:
        app_settings().setValue(PP_parts.REFERENCE_KEY, "top_left")


# ------------------------------------------------------------------
def _geom(doc, i):
    g = doc.page.objects[i].geometry
    return (g.x, g.y, g.extent_x, g.extent_y)


def _two_docs(tmp_path, src):
    pa, pb = tmp_path / "a", tmp_path / "b"
    pa.mkdir()
    pb.mkdir()
    return _doc(pa, src), _doc(pb, src)


def test_arrow_steps_waiting_for_the_pause_stay_on_their_page(tmp_path, fx_page):
    """A page switch inside the pause: the steps go to the page they were made on."""
    da, db = _two_docs(tmp_path, fx_page(*HOST))
    ra = da.create_object("rectangle", (351, 40, 451, 90), None)
    panel = _panel()
    for retarget in (lambda: panel.set_target(db, [0]), panel.show_pending):
        _show(panel, da, [ra])
        x = _field(panel, "x")
        x.setFocus()
        _app().processEvents()
        x0 = da.page.objects[ra].geometry.x
        na, nb, gb, dirty = len(da._undo), len(db._undo), _geom(db, 0), db.dirty
        for _ in range(2):
            QTest.keyClick(x, QtCore.Qt.Key_Up)
        assert x.pending_steps()
        retarget()
        assert da.page.objects[ra].geometry.x == x0 + 2 and len(da._undo) == na + 1
        assert _geom(db, 0) == gb and len(db._undo) == nb and db.dirty == dirty


def test_a_tab_switch_inside_the_pause_leaves_the_other_page_alone(tmp_path, fx_page):
    from ctgedit.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        win.resize(1400, 900)
        win.show()
        win.activateWindow()
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        win.open_document(_copy(tmp_path / "a", fx_page(*HOST)))
        win.open_document(_copy(tmp_path / "b", fx_page(*HOST)))
        _app().processEvents()
        da, db = win._docs[0]["doc"], win._docs[1]["doc"]
        win.doc_tabs.setCurrentIndex(0)
        _app().processEvents()
        assert win.doc is da
        win._on_canvas_selection([0])
        _app().processEvents()
        x = _field(win.props, "x")
        x.setFocus()
        _app().processEvents()
        ga, gb = _geom(da, 0), _geom(db, 0)
        na, nb, dirty = len(da._undo), len(db._undo), db.dirty
        for _ in range(2):
            QTest.keyClick(x, QtCore.Qt.Key_Up)
        assert x.pending_steps()
        win.doc_tabs.setCurrentIndex(1)
        _app().processEvents()
        assert win.doc is db
        QTest.qWait(400)  # (past the pause)
        assert _geom(db, 0) == gb and len(db._undo) == nb and db.dirty == dirty
        assert _geom(da, 0)[0] == ga[0] + 2 and len(da._undo) == na + 1
    finally:
        for e in win._docs:
            e["doc"].dirty = False
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


def test_selection_steps_inside_the_pause_apply_to_the_selection_they_were_made_on(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    sel = [doc.create_object("rectangle", (100 + 60 * k, 40, 150 + 60 * k, 90), None) for k in range(3)]
    other = doc.create_object("rectangle", (834, 40, 870, 76), None)  # 36 px wide
    panel = _panel()
    _show(panel, doc, sel)
    x = panel._multi_geom[1]
    x.setFocus()
    _app().processEvents()
    before = [_geom(doc, i) for i in sel]
    for _ in range(3):
        QTest.keyClick(x, QtCore.Qt.Key_Up)
    panel.set_target(doc, [other])
    assert [_geom(doc, i)[0] for i in sel] == [g[0] + 3 for g in before]
    assert _geom(doc, other) == (834, 40, 36, 36)
    # W: the selection the field shows is scaled, not the object selected now
    _show(panel, doc, sel)
    box = doc.selection_extent(sel)
    w = panel._multi_geom[3][0]
    w.setFocus()
    _app().processEvents()
    for _ in range(3):
        QTest.keyClick(w, QtCore.Qt.Key_Up)
    panel.set_target(doc, [other])
    after = doc.selection_extent(sel)
    assert after[2] - after[0] == box[2] - box[0] + 3
    assert _geom(doc, other) == (834, 40, 36, 36)


def _all_properties(panel):
    sec = panel.section("All properties")
    sec.set_folded(False, remember=False)
    _app().processEvents()
    return sec, _field(panel, "@search")


def test_the_all_properties_search_filters_its_rows(tmp_path, fx_page):
    from PySide6 import QtGui

    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (0, 0, 100, 50), None)
    doc.ensure_property(r, "VISIBLE", "Hide_It")
    doc.ensure_property(r, "COMMENT", "A tip")
    panel = _panel()
    _show(panel, doc, [r])
    sec, search = _all_properties(panel)
    form = sec.form()

    def shown():
        out = []
        for row in range(form.rowCount()):
            lab = form.itemAt(row, QtWidgets.QFormLayout.LabelRole)
            if lab is not None and lab.widget() is not None and form.isRowVisible(row):
                out.append(lab.widget().toolTip())
        return out

    assert {"VISIBLE", "COMMENT"} <= set(shown())
    search.setFocus()
    QTest.keyClicks(search, "hide_it")
    search.flush()
    assert shown() == ["VISIBLE"]
    # a fold and an unfold keep the search
    sec.set_folded(True, remember=False)
    sec.set_folded(False, remember=False)
    assert shown() == ["VISIBLE"]
    # Esc clears the search first (the focus stays in it), as every search box does
    search.setFocus()  # (the fold took it)
    _app().processEvents()
    assert search.hasFocus()
    QTest.keyClick(search, QtCore.Qt.Key_Escape)
    assert search.text() == "" and search.hasFocus()
    search.flush()
    assert {"VISIBLE", "COMMENT"} <= set(shown())

    # Ctrl+Z is the box's own while it holds text; empty, it is Edit > Undo
    def override_taken():
        ev = QtGui.QKeyEvent(QtCore.QEvent.ShortcutOverride, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
        ev.ignore()
        QtWidgets.QApplication.sendEvent(search, ev)
        return ev.isAccepted()

    assert not override_taken()
    QTest.keyClicks(search, "ab")
    assert override_taken()


def test_a_read_only_object_can_be_searched(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    _show(panel, doc, [_member(doc)])
    assert panel.is_read_only()
    sec, search = _all_properties(panel)
    assert not search.isReadOnly() and search.isEnabled()
    others = [
        w
        for w in sec.findChildren(QtWidgets.QLineEdit)
        if w is not search
        and w.parentWidget() is not search
        and not isinstance(w.parentWidget(), QtWidgets.QAbstractSpinBox)
    ]
    assert others and all(w.isReadOnly() for w in others)


def test_remove_on_an_empty_touch_section(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    panel = _panel()
    _show(panel, doc, [r])
    _add(panel, r, "Touch command")
    assert panel.section("Input") is not None
    msgs = []
    panel.statusMessage.connect(msgs.append)
    n = len(doc._undo)
    panel._remove_family(r, "input")
    for _ in range(3):
        _app().processEvents()
    assert panel.section("Input") is None and not msgs and len(doc._undo) == n


def test_a_font_name_no_font_has_is_not_added_to_the_list(tmp_path, fx_page):
    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    t = _text(doc)
    texts = [i for i in doc.page.roots() if doc.page.objects[i].kind.value in ("text", "number")][:2]
    for sel in ([t], texts):
        _show(panel, doc, sel)
        fc = next(c for c in panel.widget().findChildren(QtWidgets.QFontComboBox))
        n = fc.count()
        fc.setFocus()
        fc.lineEdit().selectAll()
        QTest.keyClicks(fc.lineEdit(), "zzqq")
        QTest.keyClick(fc.lineEdit(), QtCore.Qt.Key_Return)
        _app().processEvents()
        assert fc.count() == n and fc.findText("zzqq") < 0, sel


def test_add_animation_flashes_the_header_in_the_window(tmp_path, fx_page):
    from ctgedit.ui.main_window import MainWindow

    _app()
    win = MainWindow()
    try:
        win.resize(1400, 900)
        win.show()
        win.activateWindow()
        win.open_document(_copy(tmp_path, fx_page(*HOST)))
        doc = win.doc
        r = doc.create_object("rectangle", (346, 40, 446, 90), None)
        win._after_edit()
        win._on_canvas_selection([r])
        _app().processEvents()
        builds = []
        orig = win.props._rebuild

        def counted():
            builds.append(1)
            orig()

        win.props._rebuild = counted
        _add(win.props, r, "Visibility")
        sec = win.props.section("Visibility")
        assert sec is not None and "background" in sec.header.chevron.styleSheet()
        assert sec.isAncestorOf(win.props.focusWidget())
        assert len(builds) == 1  # (the window's refresh, no second one)
    finally:
        win.doc.dirty = False
        if win in MainWindow._instances:
            MainWindow._instances.remove(win)
        win.close()


def test_the_lists_of_a_form_are_filled_when_first_used(fx_page):
    """The page form's content types and an object's Add animation entries come with the
    field's first use (each form is built again at every selection). FxTplLinked is
    opened where it is in the test's copy of the fake install: FxMain's content types come
    from System's table (FxMaster, FxFace)."""
    from ctgedit.editing import EditableDocument

    _app()
    doc = EditableDocument.open(fx_page(*FORM_PAGE))
    panel = _panel()
    _show(panel, doc, [])
    ct = _field(panel, "CON_TYPE", QtWidgets.QComboBox)
    shown = ct.currentText()
    assert ct.count() == 0
    ct.setFocus()
    _app().processEvents()
    from ctgedit.ui.dialog.page_props import content_type_combo

    eager = content_type_combo(os.path.dirname(os.path.abspath(doc.path)), "", 0)
    assert ct.itemText(0) == "" and ct.currentText() == shown
    assert eager.count() > 1
    assert [ct.itemText(k) for k in range(ct.count())] == [eager.itemText(k) for k in range(eager.count())]
    r = doc.create_object("rectangle", (40, 40, 140, 90), None)
    _show(panel, doc, [r])
    combo = next(
        c for c in panel.widget().findChildren(QtWidgets.QComboBox) if c.objectName() == "addAnimation"
    )
    assert combo.count() == 0 and combo.placeholderText() == "Add animation…"
    combo.setFocus()
    _app().processEvents()
    assert combo.itemText(0) == "Add animation…" and combo.findText("Visibility") > 0


def test_a_group_shows_no_appearance_and_no_stray_section(tmp_path, fx_page):
    from ctgedit.ui.panels import properties_parts as PP_parts

    doc = _doc(tmp_path, fx_page(*HOST))
    panel = _panel()
    g = next(
        i
        for i in doc.page.roots()
        if doc.page.objects[i].raw.get("_type_code") == 0x08 and not panel._object_read_only(i)
    )
    root = _show(panel, doc, [g])
    secs = root.findChildren(PP_parts.PanelSection)
    assert secs and "Appearance" not in [s.title() for s in secs]
    # every section is in the form's layout (one made in the form but left out would show
    # over its top left corner)
    assert all(s.isVisibleTo(root) and s.y() > 0 for s in secs)
