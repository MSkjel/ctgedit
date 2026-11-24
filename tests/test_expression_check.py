"""The Cicode check of an expression field."""

import os
import sys

from PySide6 import QtCore, QtWidgets
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class _FakeChecker:
    """A checker that answers at once (the real one answers on the GUI thread later)."""

    def __init__(self):
        self.asked = []

    def check(self, text, kind, callback):
        self.asked.append((text, kind))
        if "+ )" in text:
            callback(
                {
                    "ok": False,
                    "errors": [
                        {"code": 2031, "name": "E2031", "message": "Expression expected", "column": 7}
                    ],
                    "warnings": [],
                }
            )
        elif "NoSuch" in text:
            callback(
                {
                    "ok": True,
                    "errors": [],
                    "warnings": [{"message": "the project does not define the tag NoSuchTag"}],
                }
            )
        else:
            callback({"ok": True, "errors": [], "warnings": []})


def _mark(f):
    """The mark at the start of the field: None (hidden) or its level."""
    from ctgedit.ui.notify import level_icon

    a = f._advice_action
    if a is None or not a.isVisible():
        return None
    key = a.icon().cacheKey()
    return next(lv for lv in ("success", "warning", "error") if level_icon(lv).cacheKey() == key)


def test_the_mark_shows_the_compilers_verdict():
    _app()
    from ctgedit.ui.objprops.expression import ExpressionField

    f = ExpressionField(None)
    assert f.checker() is None
    chk = _FakeChecker()
    f.set_checker(chk)
    assert not [a for a in f.actions() if a.toolTip().startswith("Check")]  # (no button)
    f.setText("IntToStr(1 + )")
    assert _mark(f) is None  # (no verdict before a check)
    assert f.check_now()
    assert chk.asked == [("IntToStr(1 + )", "expression")]
    probs = f.problems()
    assert any(p.level == "ERROR" and "E2031 Expression expected (column 7)" in p.message for p in probs)
    assert _mark(f) == "error" and "E2031" in f._advice_action.toolTip()
    # the verdict is the text's: changed, it no longer shows until the next check
    f.setText("IntToStr(1 + 2)")
    assert f.check_result() is None and _mark(f) is None
    assert f.check_now()
    assert f.check_result() == {"ok": True, "errors": [], "warnings": []}
    assert not any(p.level == "ERROR" for p in f.problems())
    assert _mark(f) == "success" and f._advice_action.toolTip() == "Compiles."
    assert f.advice_text() == ""
    # a tag the project does not define: a warning, the field still compiles
    f.setText("NoSuchTag + 1")
    f.check_now()
    assert [p.level for p in f.problems() if "NoSuchTag" in p.message] == ["WARNING"]
    assert _mark(f) == "warning"
    QtWidgets.QLineEdit.setText(f, "IntToStr(1 + 3)")  # (typed: the verdict is gone)
    assert _mark(f) is None
    f.deleteLater()


def test_a_pause_in_typing_checks():
    _app()
    from ctgedit.ui.objprops.expression import ExpressionField

    f = ExpressionField(None)
    chk = _FakeChecker()
    f.set_checker(chk)
    f.setText("Tag1 + 1")
    assert chk.asked == [] and not f._check_timer.isActive()  # (a value set is not typed)
    try:
        # (typing, not set_value)
        QtWidgets.QLineEdit.setText(f, "Tag1 + 2")
        assert f._check_timer.isActive()
        f._check_timer.setInterval(1)
        f._check_timer.start()
        deadline = QtCore.QDeadlineTimer(2000)
        while not chk.asked and not deadline.hasExpired():
            QtWidgets.QApplication.processEvents()
        assert chk.asked == [("Tag1 + 2", "expression")]
        assert _mark(f) == "success"
        f.setReadOnly(True)
        QtWidgets.QLineEdit.setText(f, "Tag1 + 3")
        assert not f._check_timer.isActive()  # (read-only: nothing is typed)
    finally:
        f.deleteLater()


def test_a_command_box_checks_at_a_pause():
    _app()
    from ctgedit.ui.objprops.input import CommandEdit

    ed = CommandEdit("")
    chk = _FakeChecker()
    ed.set_checker(chk)
    ed.setPlainText("IntToStr(1 + )")
    assert ed._check_timer.isActive()
    ed._check_timer.setInterval(1)
    ed._check_timer.start()
    deadline = QtCore.QDeadlineTimer(2000)
    while not chk.asked and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
    assert chk.asked == [("IntToStr(1 + )", "command")]
    assert "E2031" in ed.verdict.text() and not ed.verdict.isHidden()
    ed.setPlainText("IntToStr(1 + 2)")
    assert ed.verdict.isHidden()  # (the verdict was the old text's)
    ed.deleteLater()


def test_the_checker_is_one_per_project_and_refuses_an_older_simulator(tmp_path):
    _app()
    from ctgedit.ui import expression_check as EC

    a = EC.checker_for(str(tmp_path / "Proj"))
    assert a is EC.checker_for(str(tmp_path / "proj"))  # (the folder, without case)
    assert EC.checker_for("") is None and EC.checker_for_document(None) is None

    class Doc:
        path = str(tmp_path / "Proj" / "FxShapes.ctg")

    assert EC.checker_for_document(Doc()) is a

    class NewSession:
        alive = True
        asked = []

        def request(self, name, args, timeout=0):
            self.asked.append((name, args))
            return {
                "ok": False,
                "errors": [{"code": 2064, "name": "E2064", "message": "Function not found", "column": 1}],
                "warnings": [],
            }

        def close(self):
            self.alive = False

    new = NewSession()
    a.opener = lambda _p: new
    got = []
    a.check("Nope(1)", "command", got.append)
    deadline = QtCore.QDeadlineTimer(5000)
    while not got and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
    assert got[0]["ok"] is False and new.asked == [
        ("checkExpression", {"text": "Nope(1)", "kind": "command"})
    ]
    assert EC.summary(got[0]) == "E2064 Function not found (column 1)"
    a.close()
    deadline = QtCore.QDeadlineTimer(5000)
    while new.alive and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
    assert not new.alive


def test_genie_parameters_and_associations_are_compiled_as_names(tmp_path):
    """'%Tag%' and '?INT 2?' go to the compiler as names of their own length; what it says
    about the names they make is left out, the rest is kept."""
    _app()
    from ctgedit.ui import expression_check as EC

    class Session:
        alive = True
        asked = []

        def request(self, name, args, timeout=0):
            text = args["text"]
            self.asked.append(text)
            errors, warnings = [], []
            if "+ )" in text:
                errors.append(
                    {"code": 2041, "name": "E2041", "message": "Operand expected", "column": len(text)}
                )
            if '+ "x"' in text:
                errors.append(
                    {"code": 2024, "name": "E2024", "message": "Incompatible types", "column": len(text) + 1}
                )
            if "Gp__(" in text:
                errors.append({"code": 2031, "name": "E2031", "message": "FUNCTION expected", "column": 1})
            for name in ("Gp____PV", "Gp_____", "Nope_Tag"):
                if name in text:
                    warnings.append({"message": "the project does not define the tag %s" % name})
            return {"ok": not errors, "errors": errors, "warnings": warnings}

        def close(self):
            self.alive = False

    c = EC.ExpressionChecker(str(tmp_path / "Lib"))
    c.opener = lambda _p: Session()
    texts = ("%Tag%_PV > 3", "?INT 2? + Nope_Tag", "%Fn%(1)", "%Tag% + )", '%Tag% + "x"')
    got = {}
    for t in texts:
        c.check(t, "expression", lambda r, t=t: got.__setitem__(t, r))
    deadline = QtCore.QDeadlineTimer(5000)
    while len(got) < len(texts) and not deadline.hasExpired():
        QtWidgets.QApplication.processEvents()
    assert got["%Tag%_PV > 3"] == {"ok": True, "errors": [], "warnings": []}
    assert [w["message"] for w in got["?INT 2? + Nope_Tag"]["warnings"]] == [
        "the project does not define the tag Nope_Tag"
    ]
    assert got["%Fn%(1)"]["ok"] is True
    assert [e["name"] for e in got["%Tag% + )"]["errors"]] == ["E2041"]
    assert got['%Tag% + "x"']["ok"] is True  # (the parameter's type is the placed genie's)
    c.close()
