"""The Properties panel's appearance rows."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the host page: FxSa's FxSaBlank in the fake install (a blank page on the fx_sa template
#: with one text); the tests make their own objects on it
HOST = ("FxSaBlank", "FxSa")
#: a top-level text whose caption is a label: FxMain's FxTextKinds holds FxTxtOffLabel,
#: TXT_OFF '@(Fx Start)'
LABEL_PAGE = ("FxTextKinds", "FxMain")
LABEL_TEXT = "FxTxtOffLabel"


@pytest.fixture
def page(fx_page):
    """``page(where=HOST)``: a new document of a page of the fake install's copy (each call
    opens its own document)."""

    def open_page(where=HOST):
        _app()
        from ctgedit.editing import EditableDocument

        return EditableDocument.open(fx_page(*where))

    return open_page


def _label_text(doc):
    (i,) = doc.find_objects_by_name(LABEL_TEXT)
    o = doc.page.objects[i]
    assert o.parent is None and "Fx Start" in str(o.style.text or "")
    return i


def _panel(width=300):
    _app()
    from ctgedit.ui.panels.properties import PropertyPanel

    p = PropertyPanel()
    p.resize(width, 900)
    p.show()
    _app().processEvents()
    return p


def _show(panel, doc, sel):
    panel.set_target(doc, sel)
    for _ in range(3):
        _app().processEvents()
    return panel.widget()


def _field(panel, key, cls=QtWidgets.QWidget):
    return next(w for w in panel.widget().findChildren(cls) if w.property("propKey") == key)


@pytest.fixture(autouse=True)
def _fresh_styles():
    _app()
    from ctgedit.ui.editor import draw_styles as DS
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    keys = (
        DS.STYLE_KEY,
        DS.LAST_KEY,
        DS.MODE_KEY,
        DS.FILL_KEY,
        DS.LINE_KEY,
        DS.TEXT_KEY,
        DS.COLOUR_MODE_KEY,
        DS.NO_FILL_KEY,
        "props/multi_apply",
        "props/search_keep",
    )
    for k in keys:
        st.remove(k)
    yield
    for k in keys:
        st.remove(k)


# ------------------------------------------------------------------ alignment, effects
def test_centring_a_text_from_the_panel_is_what_the_sheet_stores(page):
    a = page(LABEL_PAGE)
    b = page(LABEL_PAGE)
    # '@(Fx Start)' (its caption 'Fx Start' without the window's label mode), left first
    i = _label_text(a)
    assert _label_text(b) == i
    for d in (a, b):
        d.set_font(i, justify="left")  # (the fixture's text is left already)
        assert d.page.objects[i].style.justify.value == "left"
    panel = _panel()
    _show(panel, a, [i])
    centre = _field(panel, "justify_centre", QtWidgets.QToolButton)
    assert not centre.isChecked() and _field(panel, "justify_left").isChecked()
    n = len(a._undo)
    centre.click()
    assert len(a._undo) == n + 1
    # the object sheet's Alignment: _FontSection.apply_font -> set_font(justify=)
    assert b.set_font(i, justify="centre")
    assert a.parsed.page.objects[i].body_raw == b.parsed.page.objects[i].body_raw
    assert (a.page.objects[i].style.style_flags[3] & 0x0F) == 2  # CENTRE
    assert a.page.objects[i].style.justify.value == "centre"
    _show(panel, a, [i])
    assert _field(panel, "justify_centre").isChecked()
    assert not _field(panel, "justify_left").isChecked()


def test_a_texts_effect_and_bold_with_icons(page):
    doc = page(LABEL_PAGE)
    i = _label_text(doc)
    panel = _panel()
    _show(panel, doc, [i])
    bold = _field(panel, "bold", QtWidgets.QToolButton)
    assert not bold.icon().isNull() and bold.accessibleName() == "Bold"
    eff = _field(panel, "text_effect", QtWidgets.QComboBox)
    n = len(doc._undo)
    k = eff.findData(1)  # Raised
    eff.setCurrentIndex(k)
    eff.activated.emit(k)
    assert len(doc._undo) == n + 1
    assert doc.page.objects[i].style.style_flags[0] & 0x0F == 1


def test_corner_radius_and_the_3d_effect_of_a_rectangle(page):
    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None)
    panel = _panel()
    _show(panel, doc, [r])
    rad = _field(panel, "corner_radius")
    n = len(doc._undo)
    rad.setValue(6)
    rad.editingFinished.emit()
    assert doc.page.objects[r].style.corner_radius == 6 and len(doc._undo) == n + 1
    _show(panel, doc, [r])
    eff = _field(panel, "effect", QtWidgets.QComboBox)
    assert eff.currentText() == "None"
    eff.setCurrentIndex(eff.findData(1))
    eff.activated.emit(eff.currentIndex())
    st = doc.page.objects[r].style
    assert st.style_flags[0] & 0x0F == 1 and len(doc._undo) == n + 2
    _show(panel, doc, [r])
    depth = _field(panel, "effect_depth")
    assert depth.text() == "Automatic"  # (CtDraw's 'auto' box: -1)
    depth.setValue(4)
    depth.editingFinished.emit()
    assert doc.page.objects[r].style.style_flags[5] == 4 and len(doc._undo) == n + 3


# ------------------------------------------------------------------ several objects
@pytest.fixture
def win(fx_page):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page(*HOST))
    w.resize(1400, 900)
    w.show()
    _app().processEvents()
    yield w
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


def _select(w, ix):
    w._on_canvas_selection(list(ix))
    w.view.set_selection(list(ix))
    for _ in range(3):
        _app().processEvents()


def test_several_objects_the_arrange_row_comes_first(win):
    doc = win.doc
    a = doc.create_object("rectangle", (300, 100, 360, 140), None)
    b = doc.create_object("rectangle", (420, 180, 500, 230), None)
    win._refresh()
    _select(win, [a, b])
    root = win.props.widget()
    row = root.findChild(QtWidgets.QWidget, "arrangeRow")
    assert row is not None
    lay = root.layout()
    order = [lay.itemAt(k).widget() for k in range(lay.count()) if lay.itemAt(k).widget()]
    assert order.index(row) < order.index(win.props.section("Selection"))
    hosted = [btn.defaultAction() for btn in row.findChildren(QtWidgets.QToolButton)]
    assert hosted[:6] == list(win.align_actions)
    assert {win.act_dist_h, win.act_dist_v, win.act_space_equally, win.act_same_size} <= set(hosted)
    texts = [x.text() for x in root.findChildren(QtWidgets.QLabel)]
    assert not any("the Arrange menu" in t for t in texts)
    # the button runs the menu's own command: one undo step
    n = len(doc._undo)
    next(
        btn for btn in row.findChildren(QtWidgets.QToolButton) if btn.defaultAction() is win.align_actions[0]
    ).click()
    assert len(doc._undo) == n + 1
    assert doc.page.objects[a].geometry.x == doc.page.objects[b].geometry.x


def test_each_object_x_lines_them_up_in_one_step(win):
    doc = win.doc
    a = doc.create_object("rectangle", (300, 100, 360, 140), None)
    b = doc.create_object("rectangle", (420, 180, 500, 230), None)
    win._refresh()
    _select(win, [a, b])
    p = win.props
    each = _field(p, "multi_apply_each", QtWidgets.QToolButton)
    each.click()
    for _ in range(3):
        _app().processEvents()
    from ctgedit.ui.prefs.settings import app_settings

    assert app_settings().value("props/multi_apply") == "each"
    x = p._multi_fields["x"]
    assert x.is_mixed() and x.lineEdit().text() == "" and x.lineEdit().placeholderText() == "Mixed"
    n = len(doc._undo)
    x.lineEdit().setText("100")
    x.lineEdit().textEdited.emit("100")
    x.interpretText()
    x.editingFinished.emit()
    assert len(doc._undo) == n + 1
    assert doc.page.objects[a].geometry.x == 100 and doc.page.objects[b].geometry.x == 100
    # W: each one takes the width
    _select(win, [a, b])
    w = win.props._multi_fields["w"]
    w.lineEdit().setText("40")
    w.lineEdit().textEdited.emit("40")
    w.interpretText()
    w.editingFinished.emit()
    assert doc.page.objects[a].geometry.extent_x == 40
    assert doc.page.objects[b].geometry.extent_x == 40


def _type_into(field, text):
    field.lineEdit().setText(text)
    field.lineEdit().textEdited.emit(text)
    field.interpretText()
    field.editingFinished.emit()


def test_a_sum_a_step_or_a_drag_on_mixed_values_changes_each_objects_own(win):
    doc = win.doc
    a = doc.create_object("rectangle", (300, 100, 360, 140), None)
    b = doc.create_object("rectangle", (420, 180, 500, 230), None)
    win._refresh()
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("props/multi_apply", "each")

    def xs():
        return doc.page.objects[a].geometry.x, doc.page.objects[b].geometry.x

    def field(key):
        _select(win, [a, b])
        for _ in range(3):
            _app().processEvents()
        f = win.props._multi_fields[key]
        assert f.is_mixed()
        return f

    # '+10' moves each by 10 (not both to the first one's x + 10), in one undo step
    n = len(doc._undo)
    _type_into(field("x"), "+10")
    assert xs() == (310, 430) and len(doc._undo) == n + 1
    # an arrow step: each by one step
    f = field("x")
    f.stepBy(1)
    f.commit()
    assert xs() == (311, 431)
    # a label drag: each by the drag's amount
    f = field("x")
    f.note_drag()
    f.setValue(f.value() + 5)
    f.commit()
    assert xs() == (316, 436)
    # a typed number still puts every one there
    _type_into(field("x"), "100")
    assert xs() == (100, 100)
    # the line width: '+1' on two different widths
    wa = max(1, doc.page.objects[a].style.line_width or 1)
    doc.set_line_width(b, wa + 2)
    win._refresh()
    _select(win, [a, b])
    for _ in range(3):
        _app().processEvents()
    lw = _field(win.props, "line_width")
    assert lw.is_mixed()
    _type_into(lw, "+1")
    assert doc.page.objects[a].style.line_width == wa + 1
    assert doc.page.objects[b].style.line_width == wa + 3


def test_mixed_values_fill_and_line_apart(page, monkeypatch):
    doc = page()
    from ctgedit.format import geometry as G

    a = doc.create_object("rectangle", (0, 0, 50, 50), None, colour=0x112233)
    b = doc.create_object("rectangle", (60, 0, 110, 50), None, colour=0x445566)
    doc.set_line_width(b, 3)
    doc.set_filled(b, False)
    panel = _panel()
    _show(panel, doc, [a, b])
    lw = _field(panel, "line_width")
    assert lw.lineEdit().text() == "" and lw.lineEdit().placeholderText() == "Mixed"
    sec = panel.section("Appearance")
    labels = {}
    form = sec.form()
    for r in range(form.rowCount()):
        lab = form.itemAt(r, QtWidgets.QFormLayout.LabelRole)
        fld = form.itemAt(r, QtWidgets.QFormLayout.FieldRole)
        if lab is not None and lab.widget() is not None and fld is not None:
            labels[lab.widget().text()] = fld.widget()
    assert "Fill colour" in labels and "Line colour" in labels
    fill = labels["Fill colour"]
    assert fill.swatch.toolTip().startswith("Mixed") and fill.value_text.text() == "Mixed"
    filled = labels["Filled"]
    assert filled.checkState() == QtCore.Qt.PartiallyChecked
    from ctgedit.ui import colour_picker as C

    monkeypatch.setattr(C, "choose_colour", lambda *a_, **k: (0x00AA00, 0x00AA00))
    n = len(doc._undo)
    fill.swatch.click()
    assert len(doc._undo) == n + 1
    from ctgedit.format import colourstate

    for i in (a, b):
        ch = colourstate.colour_channels(doc.parsed.page.objects[i])
        assert ch["fill"] == (0x00AA00, 0x00AA00)
    assert colourstate.colour_channels(doc.parsed.page.objects[a])["line"] != (0x00AA00,) * 2
    _show(panel, doc, [a, b])
    lw = _field(panel, "line_width")
    lw.lineEdit().setText("2")
    lw.lineEdit().textEdited.emit("2")
    lw.interpretText()
    lw.editingFinished.emit()
    assert [doc.page.objects[i].style.line_width for i in (a, b)] == [2, 2]
    assert len(doc._undo) == n + 2
    assert G.SLOT_FILL is not None


def test_several_texts_foreground_and_alignment(page, monkeypatch):
    doc = page()
    t1 = doc.create_object("text", (10, 10, 80, 30), None, text="One")
    t2 = doc.create_object("text", (10, 40, 80, 60), None, text="Two")
    panel = _panel()
    _show(panel, doc, [t1, t2])
    from ctgedit.ui import colour_picker as C

    monkeypatch.setattr(C, "choose_colour", lambda *a_, **k: (0x0000FF, 0x0000FF))
    sec = panel.section("Text")
    fg = next(w for w in sec.findChildren(QtWidgets.QWidget) if hasattr(w, "value_text"))
    n = len(doc._undo)
    fg.swatch.click()
    assert [doc.page.objects[i].style.text_colour for i in (t1, t2)] == [0xFF, 0xFF]
    _show(panel, doc, [t1, t2])
    _field(panel, "justify_right", QtWidgets.QToolButton).click()
    assert len(doc._undo) == n + 2
    assert all(doc.page.objects[i].style.justify.value == "right" for i in (t1, t2))


# ------------------------------------------------------------------ the search
def test_searching_tool_tip_shows_the_row_and_the_sheet_page(page):
    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None)
    panel = _panel()
    _show(panel, doc, [r])
    panel.search.setText("tool tip")
    panel.search.flush()
    _app().processEvents()
    tip = _field(panel, "COMMENT", QtWidgets.QLineEdit)
    assert tip.isVisibleTo(panel) and tip.property("searchHit")
    assert not panel.section("Geometry").isVisibleTo(panel)
    links = [
        b
        for b in panel.widget().findChildren(QtWidgets.QToolButton)
        if b.objectName() == "linkButton" and b.isVisibleTo(panel)
    ]
    assert any(b.text() == "Access > General" and b.property("pageId") == "access" for b in links)
    got = []
    panel.editPage.connect(lambda i, pid: got.append((i, pid)))
    next(b for b in links if b.text() == "Access > General").click()
    assert got == [(r, "access")]
    # the text stays across a selection change (props/search_keep), the rebuilt form
    # is searched again; an empty search shows every row
    b2 = doc.create_object("rectangle", (200, 20, 260, 80), None)
    _show(panel, doc, [b2])
    assert panel.search.text() == "tool tip"
    assert not panel.section("Geometry").isVisibleTo(panel)
    panel.search.setText("")
    panel.search.flush()
    _app().processEvents()
    assert panel.section("Geometry").isVisibleTo(panel)
    assert not _field(panel, "COMMENT").property("searchHit")


def test_search_matches_values_and_keys_and_can_be_cleared_by_selection(page):
    from ctgedit.ui.prefs.settings import app_settings

    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None)
    doc.set_line_width(r, 7)
    panel = _panel()
    _show(panel, doc, [r])
    panel.search.setText("7 px")
    panel.search.flush()
    _app().processEvents()
    assert _field(panel, "line_width").isVisibleTo(panel)
    assert not _field(panel, "line_style").isVisibleTo(panel)
    panel.search.setText("corner_radius")  # (the raw key)
    panel.search.flush()
    _app().processEvents()
    assert _field(panel, "corner_radius").isVisibleTo(panel)
    app_settings().setValue("props/search_keep", False)
    b2 = doc.create_object("rectangle", (200, 20, 260, 80), None)
    _show(panel, doc, [b2])
    assert panel.search.text() == ""


def test_ctrl_f_in_the_panel_goes_to_the_search(page):
    from PySide6 import QtGui
    from PySide6.QtTest import QTest

    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None)
    panel = _panel()
    panel.activateWindow()
    _show(panel, doc, [r])
    name = _field(panel, "__AN_NAME__")
    name.setFocus()
    _app().processEvents()
    QTest.keySequence(name, QtGui.QKeySequence(QtGui.QKeySequence.Find))
    _app().processEvents()
    assert panel.search.hasFocus() or QtWidgets.QApplication.focusWidget() is panel.search


# ------------------------------------------------------------------ New <Kind>
def test_an_armed_rectangle_shows_new_rectangle_and_keeps_its_style(page):
    from ctgedit.ui.editor import draw_styles as DS

    doc = page()
    panel = _panel()
    _show(panel, doc, [])
    panel.set_tool("rectangle")
    _app().processEvents()
    sec = panel.section("New Rectangle")
    assert sec is not None
    n = len(doc._undo)
    lw = _field(panel, "line_width")
    lw.setValue(2)
    lw.editingFinished.emit()
    _app().processEvents()
    _app().processEvents()
    rad = _field(panel, "corner_radius")
    rad.setValue(4)
    rad.editingFinished.emit()
    _app().processEvents()
    s = DS.style_for("rectangle", "rectangle", doc.page)
    assert s["line_width"] == 2 and s["corner_radius"] == 4
    assert len(doc._undo) == n  # (no page edit)
    assert _field(panel, "line_width").value() == 2
    # the colours are the swatches' until the tool has its own
    fill = _field(panel, "fill_colour")
    # (#C0C0C0: the fake install's Bin/citect.pal names that favourite Fx Silver)
    assert fill.value_text.text() == "Fx Silver"
    # Last used
    combo = _field(panel, "new_object_style", QtWidgets.QComboBox)
    combo.setCurrentIndex(combo.findData("last"))
    combo.activated.emit(combo.currentIndex())
    _app().processEvents()
    assert DS.mode() == "last"
    # Select: the page form again
    panel.set_tool("select")
    _app().processEvents()
    assert panel.section("New Rectangle") is None and panel.section("Page") is not None


def test_last_used_records_the_object_changed_in_the_panel(page):
    from ctgedit.ui.editor import draw_styles as DS

    DS.set_mode("last")
    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None)
    panel = _panel()
    _show(panel, doc, [r])
    lw = _field(panel, "line_width")
    lw.setValue(5)
    lw.editingFinished.emit()
    assert DS.last_style("rectangle")["line_width"] == 5
    assert DS.style_for("rectangle", "rectangle", doc.page)["line_width"] == 5


def test_new_text_and_button(page):
    from ctgedit.ui.editor import draw_styles as DS

    doc = page()
    panel = _panel()
    _show(panel, doc, [])
    panel.set_tool("text")
    _app().processEvents()
    assert panel.section("New Text") is not None
    _field(panel, "justify_centre", QtWidgets.QToolButton).click()
    _app().processEvents()
    assert DS.style_for("text", "text", doc.page)["justify"] == "centre"
    assert _field(panel, "text_colour").value_text.text() == "#202020"
    panel.set_tool("button")
    _app().processEvents()
    xp = _field(panel, "xp_buttons", QtWidgets.QCheckBox)
    from ctgedit.ui.prefs.settings import option

    assert xp.isChecked() == option("xp_buttons")


# ------------------------------------------------------------------ value text
def test_a_swatch_shows_its_value_and_the_value_opens_the_entry(page, monkeypatch):
    doc = page()
    r = doc.create_object("rectangle", (20, 20, 120, 80), None, colour=0x3A7BD5)
    panel = _panel()
    _show(panel, doc, [r])
    sec = panel.section("Appearance")
    sw = next(w for w in sec.findChildren(QtWidgets.QWidget) if hasattr(w, "value_text"))
    assert sw.value_text.text() == "#3A7BD5"
    from ctgedit.ui import colour_picker as C

    calls = []
    monkeypatch.setattr(C, "choose_colour", lambda *a_, **k: (calls.append(k), None)[1])
    sw.value_text.click()
    assert calls and calls[-1].get("focus_entry") is True
    sw.swatch.click()
    assert calls[-1].get("focus_entry") is False
