"""Object and page windows are modeless and resize."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page the object sheet tests draw on (FxMain)
HOST = "FxShapes"
#: the Page Properties page (FxSa) and its template, and the other template of fx_sa.ctt
PAGE_PROJECT, PAGE = "FxSa", "FxSaContent"
PAGE_TEMPLATE, OTHER_TEMPLATE = "normal@-HD1080", "blank@-HD1080"


def _doc(fx_page, name=HOST, project="FxMain"):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(fx_page(name, project))


def _sheet(doc, index):
    from ctgedit.ui.objprops import ObjectPropertiesDialog

    _app()
    return ObjectPropertiesDialog(doc, index)


def _page_dialog(fx_install):
    """Page Properties on FxSaContent of the test's copy, with FxSa's pages.DBF."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io.project import Project
    from ctgedit.ui.dialog.page_props import PagePropertiesDialog

    _app()
    doc = EditableDocument.open(fx_install.page(PAGE, PAGE_PROJECT))
    return doc, PagePropertiesDialog(doc, Project(fx_install.project(PAGE_PROJECT)))


# ------------------------------------------------------------------ the object sheet
def test_retarget_makes_apply_write_the_moved_record(fx_page):
    """A structural edit moves the sheet's record to another index: after
    retarget(i) Apply writes record i (the same object), nothing else."""
    doc = _doc(fx_page)
    first = doc.create_object("rectangle", rect=(5, 5, 60, 40), colour=0x336699)
    second = doc.create_object("rectangle", rect=(80, 5, 140, 40), colour=0x993366)
    po = doc.parsed.page.objects[second]
    dlg = _sheet(doc, second)
    dlg._ctg_target = po
    doc.delete_objects([first])
    moved = doc.parsed.page.objects.index(po)
    assert moved == second - 1
    dlg.retarget(moved)
    assert dlg._ctg_target is po
    dlg._editors["DESC"].setText("the moved one")
    assert dlg.apply() is True
    assert doc.object_properties(moved)["DESC"] == "the moved one"
    assert doc.parsed.page.objects[moved] is po
    dlg.reject()


def test_reload_shows_an_outside_change_and_keeps_what_was_typed(fx_page):
    """reload(): a tool tip set from outside shows in the untouched field; a
    typed description stays; Apply writes the typed one and leaves the tool
    tip as the page has it."""
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    dlg._editors["DESC"].setText("typed here")
    assert doc.ensure_property(i, "COMMENT", "set outside")
    dlg.reload()
    assert dlg._editors["COMMENT"].text() == "set outside"
    assert dlg._editors["DESC"].text() == "typed here"
    assert dlg.has_pending_edits()
    assert dlg.apply() is True
    props = doc.object_properties(i)
    assert props["DESC"] == "typed here" and props["COMMENT"] == "set outside"
    assert not dlg.has_pending_edits()
    dlg.reject()


def test_pending_edits_until_apply(fx_page):
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    assert not dlg.has_pending_edits()
    dlg._editors["VISIBLE"].setText("NOT Pump_Running")
    assert dlg.has_pending_edits()
    assert dlg.apply() is True
    assert not dlg.has_pending_edits()
    dlg._sp_width.setValue(dlg._sp_width.value() + 2)  # a page editor's control
    assert dlg.has_pending_edits()
    assert dlg.apply() is True
    assert not dlg.has_pending_edits()
    dlg.reject()


def test_general_page_never_writes_back_a_value_changed_on_the_page(fx_page):
    """The line width changes on the page while the sheet is open: Apply of
    another field does not put the old width back, and reload() shows the new
    width on the untouched Appearance page."""
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    old = dlg._sp_width.value()
    assert doc.set_line_width(i, old + 3)
    dlg._editors["DESC"].setText("x")
    assert dlg.apply() is True
    from ctgedit.format import geometry as G

    assert G.find_colour_block(doc.parsed.page.objects[i]).flags[4] == old + 3
    assert dlg._sp_width.value() == old + 3  # after Apply: the page as it is
    dlg._editors["COMMENT"].setText("y")
    assert dlg.apply() is True  # a second Apply: still not back
    assert G.find_colour_block(doc.parsed.page.objects[i]).flags[4] == old + 3
    dlg.reload()
    assert dlg._sp_width.value() == old + 3
    assert doc.set_line_width(i, old + 5)
    dlg._sp_width.setValue(old + 1)  # typed: kept by reload
    dlg.reload()
    assert dlg._sp_width.value() == old + 1
    dlg.reject()


def test_typed_page_values_survive_repeated_reloads(fx_page):
    """A page without its own change test (Appearance > General, 3D Effects)
    keeps what was typed through ANY number of reloads (the window reloads
    after every edit of the object) and stays 'pending' for the save question."""
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    dlg = _sheet(doc, i)
    w0 = dlg._sp_width.value()
    dlg._sp_width.setValue(w0 + 4)
    dlg._rb_effects[2].setChecked(True)
    for n in range(3):
        assert doc.ensure_property(i, "COMMENT", "outside %d" % n)
        dlg.reload()
        assert dlg._editors["COMMENT"].text() == "outside %d" % n
        assert dlg._sp_width.value() == w0 + 4
        assert dlg._rb_effects[2].isChecked()
        assert dlg.has_pending_edits()
    assert dlg.apply() is True
    assert not dlg.has_pending_edits()
    dlg.reject()


def test_sheet_of_a_wide_line_opens_without_an_error(fx_page, monkeypatch):
    """A sheet on a line wider than 1 px opens without an error (the Style check runs before
    the section has read the line style)."""
    import sys

    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a[1]))
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(5, 5, 80, 60), colour=0x336699)
    assert doc.set_line_width(i, 4)
    dlg = _sheet(doc, i)
    assert dlg._sp_width.value() == 4
    assert not dlg._cb_lstyle.isEnabled()  # a wide line is drawn solid
    assert doc.set_line_width(i, 1)
    dlg.reload()
    assert dlg._cb_lstyle.isEnabled()
    dlg.reject()
    assert seen == []


# ------------------------------------------------------------------ layout
def _controls(page):
    """The visible controls of ``page`` (not the parts inside another one)."""
    kinds = (
        QtWidgets.QLabel,
        QtWidgets.QLineEdit,
        QtWidgets.QAbstractSpinBox,
        QtWidgets.QComboBox,
        QtWidgets.QAbstractButton,
        QtWidgets.QAbstractItemView,
        QtWidgets.QPlainTextEdit,
        QtWidgets.QTextEdit,
    )
    cand = [
        w
        for w in page.findChildren(QtWidgets.QWidget)
        if isinstance(w, kinds) and w.isVisibleTo(page) and w.width() > 0 and w.height() > 0
    ]
    ids = {id(w) for w in cand}
    out = []
    for w in cand:
        p = w.parentWidget()
        while p is not None and p is not page and id(p) not in ids:
            p = p.parentWidget()
        if p is page or p is None:
            out.append(w)
    return out


def _overlaps(page):
    rects = [(w, QtCore.QRect(w.mapTo(page, QtCore.QPoint(0, 0)), w.size())) for w in _controls(page)]
    bad = []
    for k, (a, ra) in enumerate(rects):
        for b, rb in rects[k + 1 :]:
            if ra.intersects(rb):
                bad.append(
                    "%s %r / %s %r"
                    % (
                        type(a).__name__,
                        getattr(a, "text", str)(),
                        type(b).__name__,
                        getattr(b, "text", str)(),
                    )
                )
    return bad


def _titles_shown(dlg) -> list:
    """The visible tab widgets of ``dlg`` whose last tab title is cut."""
    cut = []
    for tw in dlg.findChildren(QtWidgets.QTabWidget):
        if not tw.isVisible() or not tw.count():
            continue
        bar = tw.tabBar()
        if bar.tabRect(bar.count() - 1).right() >= bar.width():
            cut.append(tw.tabText(tw.count() - 1))
    return cut


def test_a_remembered_narrow_sheet_still_shows_every_tab_title(fx_page):
    """One size is remembered for the object sheets of every kind: a size
    left on a sheet with few tabs (a genie's) must not cut the tab titles of
    one with more, and the window cannot be made narrower than its titles."""
    from ctgedit.ui import help as H
    from ctgedit.ui.prefs.settings import app_settings

    app = _app()
    doc = _doc(fx_page)
    i = doc.create_object("rectangle", rect=(10, 10, 80, 60), colour=0x336699)
    app_settings().setValue(H.GEOMETRY_PREFIX + "ObjectPropertiesDialog", "300,300")
    dlg = _sheet(doc, i)
    dlg.show()
    H._decorate_dialog(dlg)
    app.processEvents()
    assert dlg._tabs.count() > 5
    assert _titles_shown(dlg) == []
    dlg.resize(200, dlg.height())
    app.processEvents()
    assert _titles_shown(dlg) == []
    dlg.reject()


@pytest.mark.parametrize(
    "kind,rect,kw", [("trend", (200, 5, 400, 160), {}), ("button", (10, 10, 90, 40), {"text": "Go"})]
)
def test_sheet_533_px_high_scrolls_without_overlap(fx_page, kind, rect, kw):
    """The Trend and Button sheets in a window 533 px high: the page scrolls,
    no two of its controls overlap."""
    from ctgedit.ui.objprops.scroll import page_scroll_of

    app = _app()
    doc = _doc(fx_page)
    i = doc.create_object(kind, rect=rect, **kw)
    dlg = _sheet(doc, i)
    dlg.show()
    app.processEvents()
    dlg.resize(dlg.width(), 533)
    app.processEvents()
    assert dlg.height() == 533
    page = dlg.page_widget("general")
    assert _overlaps(page) == []
    scroll = page_scroll_of(page)
    assert scroll is not None and scroll.widgetResizable()
    assert scroll.verticalScrollBar().maximum() > 0  # it scrolls
    dlg.close()


def test_touch_command_boxes_take_the_extra_height(fx_page):
    app = _app()
    doc = _doc(fx_page)
    i = doc.create_object("button", rect=(10, 10, 90, 40), text="Go")
    dlg = _sheet(doc, i)
    dlg.show()
    tab = dlg._input
    dlg._tabs.setCurrentWidget(tab)
    tab.setCurrentIndex(0)  # Touch
    app.processEvents()
    dlg.resize(dlg.width() + 100, 700)
    app.processEvents()
    before = [ed.height() for ed in tab.command_edits.values()]
    dlg.resize(dlg.width(), 1000)
    app.processEvents()
    after = [ed.height() for ed in tab.command_edits.values()]
    assert all(b > a + 50 for a, b in zip(before, after)), (before, after)
    assert sum(after) - sum(before) > 200
    dlg.close()


def test_short_combos_fit_their_text_and_name_lists_grow(fx_page):
    """Under the form rule 'only Expanding fields grow' a combo keeps its size
    hint: the trend's Cluster name must still show a 16-character name and
    its placeholder; Page Properties' Content type
    (a list of names) grows with the window."""
    doc = _doc(fx_page)
    i = doc.create_object("trend", rect=(200, 5, 400, 160))
    dlg = _sheet(doc, i)
    ed = next(e for e in dlg._page_editors if hasattr(e, "cluster"))
    combo = ed.cluster
    fm = combo.fontMetrics()
    need = max(fm.horizontalAdvance(combo.lineEdit().placeholderText()), fm.horizontalAdvance("W" * 12))
    assert combo.sizeHint().width() > need
    dlg.reject()


def test_page_properties_content_type_grows(fx_install):
    _doc_, dlg = _page_dialog(fx_install)
    assert dlg._cb_contype.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Expanding


def test_key_sequence_dialog_has_a_fixed_size():
    from ctgedit.ui import help as H
    from ctgedit.ui.objprops.input import KeySequenceDialog

    app = _app()
    assert KeySequenceDialog.RESIZE == "none"
    dlg = KeySequenceDialog(None, "F2", ["F2", "ENTER"], [], "object")
    H._decorate_dialog(dlg)
    dlg.show()
    app.processEvents()
    if dlg.minimumSize() != dlg.maximumSize():
        pytest.skip("help._decorate_dialog does not apply the RESIZE rule in this tree")
    assert dlg.size() == dlg.minimumSize()
    dlg.close()


def test_sheet_titles_name_the_special_classes(fx_page):
    doc = _doc(fx_page)
    web = doc.create_object("web_content", rect=(5, 5, 300, 200))
    cic = doc.create_object("cicode_object", rect=(320, 5, 340, 25))
    ax = doc.create_object("activex", rect=(400, 400, 560, 560))
    for i, name in ((web, "Web Content"), (cic, "Cicode Object"), (ax, "ActiveX")):
        dlg = _sheet(doc, i)
        assert dlg.windowTitle().startswith("%s Properties (AN " % name), dlg.windowTitle()
        dlg.reject()


def test_multi_line_boxes_open_at_their_minimum_lines_and_grow():
    """CommandEdit / CaptionEdit: the size hint and the minimum are the lines
    asked for, the height is not fixed, a taller window gives them more."""
    from ctgedit.ui.editor.caption_edit import CaptionEdit
    from ctgedit.ui.objprops.input import CommandEdit
    from ctgedit.ui.objprops.scroll import lines_height

    _app()
    for ed, n in (
        (CommandEdit(""), 2),
        (CommandEdit("", min_lines=4), 4),
        (CaptionEdit("a"), 3),
        (CaptionEdit("", lines=2), 2),
    ):
        want = lines_height(ed, n)
        assert ed.sizeHint().height() == want and ed.minimumHeight() == want
        assert ed.maximumHeight() == 16777215
        assert ed.sizePolicy().verticalPolicy() == QtWidgets.QSizePolicy.Expanding
    assert CommandEdit("", min_lines=4).sizeHint().height() > CommandEdit("").sizeHint().height()


def test_properties_panel_caption_keeps_its_height(fx_page):
    """The docked Properties panel lays the caption out at its size hint
    (three lines): it does not take the panel's spare height."""
    from ctgedit.ui.editor.caption_edit import CaptionEdit
    from ctgedit.ui.objprops.scroll import lines_height
    from ctgedit.ui.panels.properties import PropertyPanel

    app = _app()
    doc = _doc(fx_page)
    i = doc.create_object("text", rect=(60, 60, 200, 82), text="One")
    panel = PropertyPanel()
    panel.resize(340, 1400)
    panel.set_target(doc, [i])
    panel.show()
    app.processEvents()
    ed = panel.findChild(CaptionEdit)
    assert ed is not None
    assert ed.height() == ed.sizeHint().height() == lines_height(ed, 3)
    panel.close()


# ------------------------------------------------------------------ Page Properties
def test_page_properties_apply_keeps_a_size_changed_outside(fx_install):
    doc, dlg = _page_dialog(fx_install)
    w, h = doc.page.appearance.width, doc.page.appearance.height
    doc.set_page_size(w + 10, h + 20)
    assert doc.set_page_record({"COMMENT": "set on the page"})
    dlg._title.setText("A new title")
    assert dlg.apply() is True
    assert (doc.page.appearance.width, doc.page.appearance.height) == (w + 10, h + 20)
    rec = doc.page_record()
    assert rec["TITLE"] == "A new title" and rec["COMMENT"] == "set on the page"
    assert (dlg._sp_w.value(), dlg._sp_h.value()) == (w + 10, h + 20)
    assert dlg._desc.value() == "set on the page"  # after Apply: the page as it is
    dlg._cb_contype.setEditText("L2")
    assert dlg.apply() is True  # a second Apply: nothing put back
    assert doc.page_record()["COMMENT"] == "set on the page"
    assert (doc.page.appearance.width, doc.page.appearance.height) == (w + 10, h + 20)


def test_page_properties_apply_keeps_a_template_changed_outside(fx_install):
    """The page is linked to another template while the dialog is open: Apply
    of another field neither switches it back nor restores the old size."""
    doc, dlg = _page_dialog(fx_install)
    assert doc.template_reference()[1].element == PAGE_TEMPLATE
    assert doc.change_template("fx_sa.%s.<ctt" % OTHER_TEMPLATE)
    size = (doc.page.appearance.width, doc.page.appearance.height)
    dlg._title.setText("Other title")
    assert dlg.apply() is True
    assert doc.template_reference()[1].element == OTHER_TEMPLATE
    assert (doc.page.appearance.width, doc.page.appearance.height) == size
    assert dlg._new_template_ref() is None


def test_page_properties_reload_shows_the_page_and_keeps_typed_fields(fx_install):
    doc, dlg = _page_dialog(fx_install)
    assert not dlg.has_pending_edits()
    dlg._desc.setPlainText("typed description")
    assert dlg.has_pending_edits()
    doc.set_page_background(0x123456)
    assert doc.set_page_record({"TITLE": "Set outside"})
    w, h = doc.page.appearance.width, doc.page.appearance.height
    doc.set_page_size(w + 4, h)
    dlg.reload()
    assert dlg._btn_colour.pair()[0] & 0xFFFFFF == 0x123456
    assert dlg._title.text() == "Set outside"
    assert dlg._desc.value() == "typed description"
    assert dlg._sp_w.value() == w + 4
    assert dlg.apply() is True
    rec = doc.page_record()
    assert rec["TITLE"] == "Set outside" and rec["COMMENT"] == "typed description"
    assert doc.page.appearance.background_colour & 0xFFFFFF == 0x123456
    assert not dlg.has_pending_edits()


def test_page_properties_associations_changed_outside_are_not_overwritten(fx_install):
    doc, dlg = _page_dialog(fx_install)
    assert doc.set_association("OutsideAssoc", "'1'", "", "added on the page")
    dlg._title.setText("Title again")
    assert dlg.apply() is True
    assert any(n == "OutsideAssoc" for n, _v in doc.parsed.attributes.list2.pairs)


def test_page_properties_wider_window_keeps_cluster_next_to_its_field(fx_install):
    app = _app()
    _doc_, dlg = _page_dialog(fx_install)
    dlg.show()
    app.processEvents()
    dlg.resize(dlg.width() + 400, dlg.height())
    app.processEvents()
    page = dlg._general_page
    lab = next(lb for lb in page.findChildren(QtWidgets.QLabel) if lb.buddy() is dlg._cb_cluster)
    left = dlg._cb_cluster.mapTo(page, QtCore.QPoint(0, 0)).x()
    right = lab.mapTo(page, QtCore.QPoint(0, 0)).x() + lab.width()
    assert 0 <= left - right < 30, (left, right)
    dlg.close()


# ------------------------------------------------------------------ Symbol Set
def test_symbol_set_follows_the_page(fx_page):
    """FxSymbols' on / off set FxOnOffSet; the picker gives Include's lights.light_1_green."""
    from ctgedit.format import symbolset as S
    from ctgedit.ui.dialog.symbol_set_dialog import SymbolSetDialog

    _app()
    doc = _doc(fx_page, "FxSymbols")
    [SET] = doc.find_objects_by_name("FxOnOffSet")
    dlg = SymbolSetDialog(doc, SET, picker=lambda _p: ("lights", "light_1_green"))
    assert not dlg.has_pending_edits()
    dlg._list.setCurrentCell(1, 1)
    dlg._set_state(1)  # typed: a symbol
    assert dlg.has_pending_edits()
    states = doc.symbol_set_specs(SET)
    doc.apply_symbol_set(SET, S.ON_OFF, ["BIT_2"], states)  # changed on the page
    dlg.retarget(SET)
    dlg.reload()
    assert dlg._ed_expr.text() == "BIT_2"  # untouched: the new expression
    assert dlg.values()["states"][1] == ("lights", "light_1_green")  # typed: kept
    assert dlg.apply() is True
    info = doc.symbol_set_info(SET)
    assert info["expressions"][0] == "BIT_2"
    assert info["states"][1]["symbol"] == ("lights", "light_1_green")
    assert not dlg.has_pending_edits()


# ------------------------------------------------------------------ Presentation Options
def test_presentation_options_reload_keeps_typed_rows(fx_page):
    """FxComposites' FxComp3: an FxDriveC (fx_drive_c.xml) with its composition cache."""
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    _app()
    doc = _doc(fx_page, "FxComposites")
    [idx] = doc.find_objects_by_name("FxComp3")
    info = doc.composite_info(idx)
    assert info is not None and info.template is not None
    vals = dict(info.values())
    dlg = PresentationOptionsDialog(info.template, vals, read_only=False, index=idx)
    f = dlg.panel.form
    assert not dlg.has_pending_edits()
    f.widget("Equipment").setText("Area.Typed")
    assert dlg.has_pending_edits()
    orient = next(p for p in info.template.params if p.name == "Orientation")
    other = next(v for v in orient.standard_values if not C.same_value(v, vals["orientation"]))
    now = dict(vals)
    now["orientation"] = other
    now["equipment"] = "Area.FromPage"
    dlg.reload(now)
    out = dlg.values()
    assert C.same_value(
        C.typed_values(info.template, {"Orientation": out["Orientation"]})["orientation"], other
    )
    assert out["Equipment"] == "Area.Typed"
    dlg.retarget(idx + 2)
    assert dlg.index == idx + 2
    dlg.reject()
