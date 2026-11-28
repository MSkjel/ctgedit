"""Number fields."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from qtkit import app as _app


@pytest.mark.parametrize(
    "text, current, negative_ok, want",
    [
        ("344+10", None, True, 354),
        ("+20", 5, True, 25),
        ("*2", 5, True, 10),
        ("/2", 5, True, 2.5),
        ("(1+2)*3", None, True, 9),
        ("1366/2", None, True, 683),
        ("-5", 10, True, -5),  # a field that takes negatives: minus five
        ("-5", 10, False, 5),  # one that does not: 5 off the value shown
        (" 7 ", None, True, 7),
        ("2.5*2", None, True, 5),
    ],
)
def test_evaluate_sums(text, current, negative_ok, want):
    from ctgedit.ui.number_field import evaluate

    assert evaluate(text, current, negative_ok) == want


@pytest.mark.parametrize(
    "text",
    ["", "1+", "abc", "2**3", "7//2", "1/0", "(1+2", "__import__", "1e5", "+3", "(" * 200 + "1" + ")" * 200],
)
def test_evaluate_refuses_what_is_not_a_sum(text):
    from ctgedit.ui.number_field import evaluate

    assert evaluate(text) is None


def test_the_parser_never_uses_eval():
    import ast
    import inspect
    from ctgedit.ui import number_field

    tree = ast.parse(inspect.getsource(number_field))
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"eval", "exec", "compile"}


def test_round_half_away():
    from ctgedit.ui.number_field import round_half_away

    assert [round_half_away(v) for v in (2.5, -2.5, 2.4, 0.5, -0.4)] == [3, -3, 2, 1, 0]


def _settle():
    """Run what earlier tests left pending (a long one made a qWait end before the step
    timer's event was dispatched)."""
    app = _app()
    for _ in range(3):
        app.sendPostedEvents()
        app.processEvents()
    QTest.qWait(1)


def _wait_for(cond, ms=2000):
    import time

    end = time.perf_counter() + ms / 1000.0
    while not cond() and time.perf_counter() < end:
        QTest.qWait(20)
    return cond()


def _field(**kw):
    _settle()
    from ctgedit.ui.number_field import NumberField

    f = NumberField(**kw)
    got = []
    f.valueCommitted.connect(got.append)
    f.show()
    f.setFocus()
    return f, got


def _type(f, text, enter=True):
    f.lineEdit().selectAll()
    QTest.keyClicks(f, text)
    if enter:
        QTest.keyClick(f, QtCore.Qt.Key_Return)


def test_typing_a_sum_applies_its_value_once():
    f, got = _field(lo=-32000, hi=32000, suffix=" px", value=344)
    _type(f, "344+10")
    assert f.value() == 354 and got == [354]  # not 3441
    _type(f, "+20")
    _type(f, "*2")
    _type(f, "/2")
    assert got == [354, 374, 748, 374]
    _type(f, "(1+2)*3")
    assert f.value() == 9


def test_bad_input_keeps_the_value_and_frames_the_field():
    from ctgedit.ui.style import theme

    f, got = _field(lo=0, hi=1000, value=50)
    _type(f, "3+")
    assert f.value() == 50 and got == []  # the '3' it starts with is not applied
    assert theme.is_invalid(f) and "100+20" in f.toolTip()
    _type(f, "abc")  # letters are not taken at all
    assert "abc" not in f.text()
    _type(f, "60")
    assert f.value() == 60 and not theme.is_invalid(f)


def test_the_result_is_rounded_and_clamped():
    f, _got = _field(lo=0, hi=100, value=10)
    _type(f, "5/2")
    assert f.value() == 3
    _type(f, "*100")
    assert f.value() == 100
    _type(f, "-500")  # (relative: 100 - 500, kept at 0)
    assert f.value() == 0


def test_shift_steps_ten_and_ctrl_the_grid():
    f, _got = _field(lo=-1000, hi=1000, value=0, grid_step=lambda: 8)
    QTest.keyClick(f, QtCore.Qt.Key_Up)
    assert f.value() == 1
    QTest.keyClick(f, QtCore.Qt.Key_Up, QtCore.Qt.ShiftModifier)
    assert f.value() == 11
    QTest.keyClick(f, QtCore.Qt.Key_Down, QtCore.Qt.ControlModifier)
    assert f.value() == 3


def test_arrow_steps_apply_after_a_pause_as_one_commit():
    f, got = _field(lo=0, hi=1000, value=100, live_steps=True)
    for _ in range(5):
        QTest.keyClick(f, QtCore.Qt.Key_Up)
    assert f.value() == 105 and got == [] and f.pending_steps()
    assert _wait_for(lambda: got) and got == [105]
    f.editingFinished.emit()  # leaving the field: nothing new
    assert got == [105]


def test_arrow_steps_wait_for_enter_when_not_live():
    f, got = _field(lo=0, hi=1000, value=100)
    QTest.keyClick(f, QtCore.Qt.Key_Up)
    QTest.qWait(450)
    assert got == []
    QTest.keyClick(f, QtCore.Qt.Key_Return)
    assert got == [101]


def test_revert_puts_the_loaded_value_back():
    f, got = _field(lo=0, hi=1000, value=346)
    _type(f, "500", enter=False)
    assert f.is_modified()
    assert f.revert() and f.value() == 346 and f.text() == "346" and not f.is_modified()
    assert got == []


def test_the_wheel_steps_only_a_focused_field():
    from PySide6 import QtGui

    f, _got = _field(lo=0, hi=1000, value=10)
    other = QtWidgets.QLineEdit()
    other.show()
    other.setFocus()
    _app().processEvents()

    def wheel():
        ev = QtGui.QWheelEvent(
            QtCore.QPointF(5, 5),
            QtCore.QPointF(5, 5),
            QtCore.QPoint(0, 0),
            QtCore.QPoint(0, 120),
            QtCore.Qt.NoButton,
            QtCore.Qt.NoModifier,
            QtCore.Qt.NoScrollPhase,
            False,
        )
        QtWidgets.QApplication.sendEvent(f, ev)

    if not f.hasFocus():
        wheel()
        assert f.value() == 10  # (the panel scrolls instead)


def test_scrub_label_drags_the_value_and_commits_once():
    from ctgedit.ui.number_field import ScrubLabel

    f, got = _field(lo=-1000, hi=1000, value=10)
    lab = ScrubLabel("X", f)
    lab.drag_by(20)  # 1 per 2 px
    assert f.value() == 20 and got == []
    lab.drag_by(4, QtCore.Qt.ShiftModifier)  # Shift: 10 per 2 px
    assert f.value() == 40
    lab.drag_by(8, QtCore.Qt.AltModifier)  # Alt: 1 per 8 px
    assert f.value() == 41
    lab.finish(True)
    assert got == [41]


def test_scrub_escape_puts_the_value_back():
    from ctgedit.ui.number_field import ScrubLabel

    f, got = _field(lo=0, hi=1000, value=10)
    lab = ScrubLabel("W", f)
    lab.drag_by(30)
    assert f.value() == 25
    lab.finish(False)
    assert f.value() == 10 and got == []


def test_scrub_label_can_be_off():
    from ctgedit.ui.number_field import ScrubLabel

    f, _got = _field(lo=0, hi=1000, value=10)
    lab = ScrubLabel("W", f, enabled=False)
    assert not lab.scrub_enabled()
    f.setReadOnly(True)
    lab.set_scrub_enabled(True)
    assert not lab.scrub_enabled()  # (a read-only field is not dragged)


def test_optional_spin_is_a_number_field():
    _app()
    from ctgedit.ui.number_field import NumberField
    from ctgedit.ui.objprops.fields import OptionalSpin

    sp = OptionalSpin(0, 100, 50, " %")
    assert isinstance(sp, NumberField)
    assert sp.key_value() is None and sp.text() == "(not set)"
    sp.stepBy(1)
    assert sp.key_value() == "50"
    sp.show()
    sp.setFocus()
    _type(sp, "+10")
    assert sp.key_value() == "60"
