"""Defaults for new objects from the drawing tools."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app


@pytest.fixture()
def win(fx_page):
    """A window on FxShapes of the fake install (its copy; code page 1252, which cannot
    store U+2603)."""
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_page("FxShapes"))
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def _answer(monkeypatch, expr=None, fmt=""):
    """Make the Number prompt answer ``expr`` (+ format), or cancel (None)."""
    from ctgedit.ui.dialog.dialogs import NumberExpressionDialog

    def fake_exec(dlg):
        if expr is None:
            return QtWidgets.QDialog.Rejected
        dlg._expr.setText(expr)
        dlg._fmt.setText(fmt)
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(NumberExpressionDialog, "exec", fake_exec)


def test_number_prompt_dialog():
    _app()
    from ctgedit.ui.dialog.dialogs import NumberExpressionDialog
    from ctgedit.ui.help import DIALOG_TOPICS

    dlg = NumberExpressionDialog(["LOOP_1_PV", "LOOP_1_SP"])
    ok = dlg._buttons.button(QtWidgets.QDialogButtonBox.Ok)
    assert not ok.isEnabled()  # no expression: OK disabled
    dlg._expr.setText("  ")
    assert not ok.isEnabled()
    dlg._expr.setText("LOOP_1_PV")
    assert ok.isEnabled()
    assert dlg.txt_str() == "LOOP_1_PV"
    dlg._fmt.setText(":###.#")
    assert dlg.txt_str() == "LOOP_1_PV:###.#"
    # an expression field: the Wizard, completion of the name under the cursor
    from ctgedit.ui.objprops.expression import ExpressionField

    assert isinstance(dlg._expr, ExpressionField)
    assert dlg._expr._wizard is not None
    assert dlg._expr.name_completer() is not None
    assert "LOOP_1_SP" in dlg._expr.completion_names("LOOP_1")
    assert dlg.windowTitle() == "New Number"
    assert dlg._expr.placeholderText() == "For example LOOP_1_PV"
    assert NumberExpressionDialog.compose(" Tag1 ", " #.## EU ") == "Tag1:#.## EU"
    assert "NumberExpressionDialog" in DIALOG_TOPICS
    # a character the page code page cannot store is named, OK stays off
    dlg._expr.setText("LOOP_1_PV☃")
    assert not ok.isEnabled()
    assert not dlg._error.isHidden() and "☃" in dlg._error.text()
    dlg._expr.setText("LOOP_1_PV")
    assert ok.isEnabled() and dlg._error.isHidden()


def test_create_refuses_text_the_page_cannot_store(win):
    # the factory encodes with 'replace': the snowman would be saved as '?'
    from ctgedit.editing import UnencodableTextError

    n = len(win.doc.parsed.page.objects)
    for kind in ("number", "text", "button"):
        with pytest.raises(UnencodableTextError):
            win.doc.create_object(kind, (40, 40, 110, 60), text="T☃")
    assert len(win.doc.parsed.page.objects) == n and not win.doc.can_undo()


def test_number_tool_writes_the_expression(win, monkeypatch):
    from ctgedit.io import pgregister as PR

    _answer(monkeypatch, "LOOP_1_PV", "###.#")
    n = len(win.doc.parsed.page.objects)
    win._on_draw_committed("number", {"rect": (40, 40, 110, 60)})
    assert len(win.doc.parsed.page.objects) == n + 1
    (i,) = win._sel
    po = win.doc.parsed.page.objects[i]
    p = po.raw_properties()
    assert p["TXTMODE"] == "3" and p["TXT_STR"] == "LOOP_1_PV:###.#"
    rows = [
        r
        for r in PR.page_rows(win.doc.parsed, "FxShapes")["pgdynobj"]
        if r.get("TXT_STR") == "LOOP_1_PV:###.#"
    ]
    assert len(rows) == 1 and rows[0]["TYPE"] == "0x0005"
    assert win.tool_actions["select"].isChecked()  # back to Select


def test_number_tool_cancel_places_nothing(win, monkeypatch):
    _answer(monkeypatch, None)
    n = len(win.doc.parsed.page.objects)
    win._on_draw_committed("number", {"rect": (40, 40, 110, 60)})
    assert len(win.doc.parsed.page.objects) == n
    assert not win.doc.can_undo()
    assert "not placed" in win.statusBar().currentMessage()
    assert win.tool_actions["select"].isChecked()


def test_text_and_button_tools_store_ctdraw_defaults(win):
    from ctgedit.io import pgregister as PR

    win._on_draw_committed("text", {"rect": (40, 40, 110, 60)})
    (i,) = win._sel
    po = win.doc.parsed.page.objects[i]
    assert po.raw_properties()["TXT_OFF"] == "Text"
    assert "Text" not in PR.lang_strings([po])
    win._on_draw_committed("button", {"rect": (40, 80, 130, 110)})
    (j,) = win._sel
    p = win.doc.parsed.page.objects[j].raw_properties()
    assert "CMD" not in p and "ACTION" not in p and p["TEXT"] == "Button"


@pytest.mark.parametrize("tool", ["rectangle", "rectangle_outline", "ellipse", "ellipse_outline"])
def test_shape_tools_draw_flat_square_full_shapes(win, tool):
    # the seeds carried a 3D edge, a corner radius 3 and (ellipse outline) a pie
    win._on_draw_committed(tool, {"rect": (40, 40, 120, 90)})
    (i,) = win._sel
    st = win.doc.page.objects[i].style
    assert st.three_d == 0 and st.corner_radius == 0 and st.ellipse_style == 0
    assert st.filled is not tool.endswith("_outline")
