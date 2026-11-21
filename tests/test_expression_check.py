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


def test_the_check_action_shows_the_compilers_verdict_as_advice():
    _app()
    from ctgedit.ui.objprops.expression import ExpressionField

    f = ExpressionField(None)
    assert f.checker() is None and f._check_action is None
    chk = _FakeChecker()
    f.set_checker(chk)
    assert f._check_action is not None and f._check_action.isVisible()
    f.setText("IntToStr(1 + )")
    assert f.check_now()
    assert chk.asked == [("IntToStr(1 + )", "expression")]
    probs = f.problems()
    assert any(p.level == "ERROR" and "E2031 Expression expected (column 7)" in p.message for p in probs)
    assert f._advice_action is not None and f._advice_action.isVisible()
    assert "E2031" in f._advice_action.toolTip()
    # the verdict is the text's: typed on, it no longer shows until the next check
    f.setText("IntToStr(1 + 2)")
    assert f.check_result() is None
    assert f.check_now()
    assert f.check_result() == {"ok": True, "errors": [], "warnings": []}
    assert not any(p.level == "ERROR" for p in f.problems())
    assert f._check_action.toolTip() == "No problem found."
    # a tag the project does not define: a warning, the field still compiles
    f.setText("NoSuchTag + 1")
    f.check_now()
    assert [p.level for p in f.problems() if "NoSuchTag" in p.message] == ["WARNING"]
    # read-only: no Check action
    f.setReadOnly(True)
    assert not f._check_action.isVisible()
    f.deleteLater()


def test_a_pause_in_typing_checks_with_the_setting_on(monkeypatch):
    _app()
    from ctgedit.ui.prefs import settings as S
    from ctgedit.ui.objprops.expression import ExpressionField

    f = ExpressionField(None)
    chk = _FakeChecker()
    f.set_checker(chk)
    f.setText("Tag1 + 1")
    assert chk.asked == [] and not f._check_timer.isActive()  # off by default
    S.app_settings().setValue("opt/live_expression_check", True)
    try:
        f._check_timer.setInterval(1)
        # (typing, not set_value: a value the sheet sets is not checked by itself)
        QtWidgets.QLineEdit.setText(f, "Tag1 + 2")
        assert f._check_timer.isActive()
        deadline = QtCore.QDeadlineTimer(2000)
        while not chk.asked and not deadline.hasExpired():
            QtWidgets.QApplication.processEvents()
        assert chk.asked == [("Tag1 + 2", "expression")]
    finally:
        S.app_settings().remove("opt/live_expression_check")
        f.deleteLater()


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


def test_the_setting_is_declared():
    from ctgedit.ui.prefs.settings import OPTION_DEFAULTS

    assert OPTION_DEFAULTS["live_expression_check"] is False
