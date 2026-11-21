"""Tools > Check Page and the Page Checks panel."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402


class _Tags:
    """A tag index that knows two names."""

    known = {"tank_1_level", "motor_a"}

    def empty(self):
        return False

    def is_name(self, name):
        low = name.lower().split(".", 1)[0]
        return low in self.known


class _Funcs:
    def is_function(self, name):
        return name.lower() in {"pumpon", "pagedisplay", "dsppopup"}


def test_names_in_leaves_out_strings_numbers_keywords_and_calls():
    from ctgedit.features.page_checks import names_in

    names, calls = names_in('Tank_1_Level > 10.5 AND PumpOn("Motor_B", Motor_A) OR NOT Flag.V')
    assert names == ["Tank_1_Level", "Motor_A", "Flag.V"] and calls == ["pumpon"]
    assert names_in("") == ([], [])
    assert names_in("TRUE AND 0x1F") == ([], [])


@pytest.fixture
def doc(fx_page, tmp_path):
    from ctgedit.editing import EditableDocument

    dst = tmp_path / "FxAnim.ctg"
    shutil.copyfile(fx_page("FxAnim"), dst)
    return EditableDocument.open(str(dst))


def test_expression_fields_lists_the_page_expressions(doc):
    from ctgedit.features.page_checks import expression_fields

    fields = expression_fields(doc)
    assert fields and all(f.kind in ("expression", "command") for f in fields)
    assert any(f.kind == "expression" for f in fields)
    assert all(f.text.strip() for f in fields)


def test_check_document_finds_the_planted_problems(doc):
    from ctgedit.features import page_checks as PC
    from ctgedit.model.document import ObjectKind

    # an unknown tag in an existing expression field
    f0 = next(f for f in PC.expression_fields(doc) if f.kind == "expression")
    assert doc.set_property(f0.index, f0.key, "NOPE_TAG_123 + Tank_1_Level")
    # a button without a command
    b = doc.create_object("button", rect=(50, 50, 120, 80), text="Go")
    assert doc.page.objects[b].kind is ObjectKind.BUTTON
    # an object off the page
    r = doc.create_object("rectangle", rect=(3000, 3000, 3040, 3040), colour=0x336699)
    rep = PC.check_document(doc, tags=_Tags(), functions=_Funcs(), measure=lambda *a: None)
    codes = {(f.code, f.index) for f in rep.findings}
    assert ("tag", f0.index) in codes
    assert ("no_command", b) in codes
    assert ("off_page", r) in codes
    tagf = next(f for f in rep.findings if f.code == "tag" and f.index == f0.index)
    assert "NOPE_TAG_123" in tagf.message and "Tank_1_Level" not in tagf.message
    assert tagf.level == "warning" and tagf.field == f0.key and tagf.label
    assert rep.expressions == len(PC.expression_fields(doc))
    assert "warning" in rep.summary()
    # the findings are ordered errors, warnings, notes
    levels = [f.level for f in rep.findings]
    order = {"error": 0, "warning": 1, "info": 2}
    assert levels == sorted(levels, key=order.get)


def test_check_document_measures_button_captions_and_duplicate_keys(doc):
    from ctgedit.features import page_checks as PC

    b = doc.create_object("button", rect=(50, 50, 90, 80), text="A long caption")
    doc.set_input_commands(
        b,
        touch={"CMD": 'PageDisplay("FxShapes")'},
        keys=[{"KEY_SEQ": "F2", "CMD": "x = 1"}, {"KEY_SEQ": "f2", "CMD": "x = 2"}],
    )

    def measure(lines, face, points, attribs):
        return ([120 * len(lines)], 14, 11)

    rep = PC.check_document(doc, tags=None, functions=None, measure=measure)
    codes = {(f.code, f.index) for f in rep.findings}
    assert ("caption_wide", b) in codes
    assert ("dup_key", b) in codes
    assert ("no_command", b) not in codes
    # a missing library element is an error
    doc.page.objects[b].raw["_missing_element"] = "nolib.nosym"
    rep = PC.check_document(doc, measure=lambda *a: None)
    assert any(
        f.code == "missing_element" and f.level == "error" and f.index == b and "nolib.nosym" in f.message
        for f in rep.findings
    )


def test_engine_findings_turn_compile_errors_into_rows():
    from ctgedit.features import page_checks as PC

    class _Doc:
        class page:
            objects = []

    f = PC.ExpressionField(3, "VISIBLE", "expression", "1 +", "Hidden when (VISIBLE)")
    out = PC.engine_findings(
        _Doc(),
        f,
        {
            "ok": False,
            "errors": [{"code": 1, "name": "E1", "message": "Syntax error", "column": 3}],
            "warnings": [],
        },
    )
    assert len(out) == 1 and out[0].level == "error" and out[0].index == 3
    assert "column 3" in out[0].message and "Syntax error" in out[0].message
    assert PC.engine_findings(_Doc(), f, {"error": "no engine"}) == []
    assert PC.engine_findings(_Doc(), f, {"ok": True, "errors": [], "warnings": []}) == []


@pytest.fixture
def win(fx_page, tmp_path):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    dst = tmp_path / "FxAnim.ctg"
    shutil.copyfile(fx_page("FxAnim"), dst)
    w = MainWindow()
    w.open_document(str(dst))
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def test_check_page_fills_the_panel_and_goes_to_the_object(win, monkeypatch):
    from ctgedit.ui import expression_check as EC

    monkeypatch.setattr(EC, "checker_for_document", lambda d: None)  # no engine here
    doc = win.doc
    b = doc.create_object("button", rect=(50, 50, 120, 80), text="Go")
    win._refresh()
    assert win.act_check_page.isEnabled()
    rep = win._check_page(show=True)
    assert rep is not None and rep is doc.ui.page_report
    assert not win.dock_page_checks.isHidden()
    panel = win.page_checks
    assert panel.report is rep and panel.tree.topLevelItemCount() == len(rep.findings)
    assert "not compiled" in panel.summary.text()
    rows = [panel.finding(k) for k in range(panel.tree.topLevelItemCount())]
    k = next(k for k, f in enumerate(rows) if f.code == "no_command" and f.index == b)
    panel.activate_row(k)
    assert win._sel == [b]
    # a page saved and reopened keeps nothing: the panel says so for an unchecked tab
    win._show_page_report()
    assert panel.report is rep
    doc.ui.page_report = None
    win._show_page_report()
    assert "not been checked" in panel.summary.text()


def test_a_finding_goes_to_its_object_after_an_edit_above_it(win, monkeypatch):
    """A row keeps its object's record: an unsaved delete above the object moves it in the
    list, and the row still selects it (not the one now at its old place); with the object
    gone the row says so."""
    from ctgedit.ui import expression_check as EC

    monkeypatch.setattr(EC, "checker_for_document", lambda d: None)
    doc = win.doc
    first = doc.create_object("button", rect=(10, 10, 60, 40), text="A")
    b = doc.create_object("button", rect=(200, 50, 270, 80), text="Go")
    win._refresh()
    rep = win._check_page(show=False)
    f = next(f for f in rep.findings if f.code == "no_command" and f.index == b)
    doc.delete_objects([first])
    win._refresh()
    win._goto_page_finding(f)
    assert len(win._sel) == 1 and win._sel[0] != b
    assert doc.parsed.page.objects[win._sel[0]] is f.record
    i = win._sel[0]
    doc.delete_objects([i])
    win._refresh()
    win.statusBar().clearMessage()
    win._goto_page_finding(f)
    assert "no longer on the page" in win.statusBar().currentMessage()


def test_the_finding_being_read_stays_selected_when_results_arrive():
    """The background checker refreshes the list as results come in: the selected finding
    stays selected and the list stays where it was scrolled."""
    from PySide6 import QtWidgets

    from ctgedit.features import page_checks as PC
    from ctgedit.ui.panels.page_checks import PageChecksPanel

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a = PC.Finding("warning", "first", index=1, field="TEXT", label="Text AN 1")
    b = PC.Finding("warning", "second", index=2, field="TEXT", label="Text AN 2")
    panel = PageChecksPanel()
    try:
        panel.show_report(PC.Report("P", [a, b]))
        assert panel._current() is a  # (nothing chosen yet: the first)
        panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
        assert panel._current() is b
        c = PC.Finding("error", "new", index=0, field="EXPR", label="Number AN 3")
        # (the engine's refresh: new Finding objects, one more ahead of it)
        panel.show_report(PC.Report("P", [c, PC.Finding(**vars(a)), PC.Finding(**vars(b))]))
        assert panel._current().message == "second"
    finally:
        panel.deleteLater()


def test_a_refresh_with_new_findings_keeps_the_rows_and_the_details_being_read():
    """Results arriving add rows: the rows already listed stay, and the selected finding keeps
    its details on screen."""
    from PySide6 import QtWidgets

    from ctgedit.features import page_checks as PC
    from ctgedit.ui.panels.page_checks import PageChecksPanel

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fs = [PC.Finding("warning", "m%d" % k, index=k, field="TEXT", label="T %d" % k) for k in range(5)]
    panel = PageChecksPanel()
    try:
        panel.show_report(PC.Report("P", fs))
        panel.tree.setCurrentItem(panel.tree.topLevelItem(2))
        row2 = panel.tree.topLevelItem(2)
        panel.detail.setText("details of m2")
        more = [PC.Finding(**vars(f)) for f in fs] + [
            PC.Finding("error", "new", index=9, field="EXPR", label="N")
        ]
        panel.show_report(PC.Report("P", more))
        assert panel.tree.topLevelItemCount() == 6
        assert panel.tree.topLevelItem(2) is row2 and panel._current() is more[2]
        assert panel.detail.text() == "details of m2"
        # a report that differs earlier: the rows from there are made again
        panel.show_report(PC.Report("P", more[3:]))
        assert [panel.finding(k).message for k in range(3)] == ["m3", "m4", "new"]
    finally:
        panel.deleteLater()
