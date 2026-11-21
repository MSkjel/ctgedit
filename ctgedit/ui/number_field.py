"""Number fields that accept sums, and labels you drag to change a value."""

from __future__ import annotations

import ast
import math
import re
import weakref
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .panels import panel_options as _panels

#: the characters a number field takes while you type
_ALLOWED = re.compile(r"[0-9+\-*/(). ]*")
_PLAIN = re.compile(r"-?\d+")
#: the longest sum a field evaluates (a bound on the tree, so a pasted wall of brackets
#: cannot exhaust the parser)
MAX_TEXT = 120
#: the pause after the last arrow step before the steps apply (ms): the default of Settings >
#: Editing > Properties panel > Apply the steps after a pause of (ui/prefs/timing_options)
STEP_PAUSE_MS = 350

TIP = "Type a number or a sum, for example 100+20."
#: the fields whose arrow steps wait for the pause (a form about to be rebuilt commits them
#: first: :func:`pending_fields`)
_PENDING: "weakref.WeakSet" = weakref.WeakSet()
BAD_TIP = "Type a number or a sum, for example 100+20. The value was not changed."


def _walk(node) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _walk(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        a, b = _walk(node.left), _walk(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if b == 0:
            raise ZeroDivisionError
        return a / b
    raise ValueError("not a sum")


def is_relative(text: str, negative_ok: bool = True) -> bool:
    """``text`` starts with an operator that applies to the value shown: '+', '*', '/', and
    '-' in a field that takes no negative numbers (where one does, '-5' is minus five)."""
    t = (text or "").lstrip()
    return bool(t) and (t[0] in "+*/" or (t[0] == "-" and not negative_ok))


def evaluate(text: str, current: Optional[float] = None, negative_ok: bool = True) -> Optional[float]:
    """The value of a typed number or sum, or None when it is not one. A leading operator
    (:func:`is_relative`) applies to ``current``."""
    t = (text or "").strip()
    if not t or len(t) > MAX_TEXT or not _ALLOWED.fullmatch(t):
        return None
    if is_relative(t, negative_ok):
        if current is None:
            return None
        t = "(%s)%s" % (current, t)
    try:
        tree = ast.parse(t, mode="eval")
        v = float(_walk(tree.body))
    except SyntaxError, ValueError, TypeError, ZeroDivisionError, RecursionError, MemoryError, OverflowError:
        return None
    return v if math.isfinite(v) else None


def pending_fields() -> list:
    """The number fields whose arrow steps still wait for the pause."""
    out = []
    for f in list(_PENDING):
        try:
            if f.pending_steps():
                out.append(f)
            else:
                _PENDING.discard(f)
        except RuntimeError:  # (the field went with its form)
            _PENDING.discard(f)
    return out


def round_half_away(v: float) -> int:
    """``v`` to the nearest whole number, halves away from zero (2.5 -> 3, -2.5 -> -3)."""
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


_STEP_KEYS = frozenset((QtCore.Qt.Key_Up, QtCore.Qt.Key_Down, QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown))


class _ArrowKeeper(QtCore.QObject):
    """Keeps the arrow keys in a number field with the focus: its ShortcutOverride is
    accepted, so no menu shortcut (Ctrl+Up, an arrow nudge) takes them."""

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt API
        if (
            e.type() == QtCore.QEvent.ShortcutOverride
            and e.key() in _STEP_KEYS
            and not e.modifiers() & QtCore.Qt.AltModifier
        ):
            e.accept()
            return True
        return False


_KEEPER: list = []


def _arrow_keeper() -> _ArrowKeeper:
    """The one filter every number field shares (made with the first field, owned by the
    application so it lives as long as the fields)."""
    if not _KEEPER:
        _KEEPER.append(_ArrowKeeper(QtCore.QCoreApplication.instance()))
    return _KEEPER[0]


class NumberField(QtWidgets.QSpinBox):
    """A spin box that takes a number or a sum (module doc). ``orig`` = the value it was
    loaded with (:meth:`load`); Esc in the Properties panel puts it back."""

    #: the value was applied (Enter, leaving the field, the pause after arrow steps), once
    #: per change from the value loaded or last committed
    valueCommitted = QtCore.Signal(int)

    def __init__(
        self,
        parent=None,
        *,
        lo: int = -32000,
        hi: int = 32000,
        suffix: str = "",
        step: int = 1,
        value: Optional[int] = None,
        grid_step: Optional[Callable[[], int]] = None,
        live_steps: bool = False,
        clamp: bool = True,
    ):
        super().__init__(parent)
        #: a typed result outside the range is brought into it (False: it is bad input)
        self.clamp = bool(clamp)
        self._tip = ""
        self._orig: Optional[int] = None
        self.setRange(int(lo), int(hi))
        if suffix:
            self.setSuffix(suffix)
        self.setSingleStep(max(1, int(step)))
        # the value changes on Enter, on leaving the field and on a step, not at each key:
        # a sum typed after a number ('3+') must not apply the '3' it starts with, and a
        # leading operator works on the value shown before the typing
        self.setKeyboardTracking(False)
        #: ``fn() -> int``: the pitch Ctrl + an arrow steps by (None: Ctrl steps 1)
        self.grid_step = grid_step
        #: arrow steps apply after a pause (STEP_PAUSE_MS) as one commit
        self.live_steps = bool(live_steps)
        #: the pause timer, made at the first arrow step (a form builds many fields)
        self._step_timer: Optional[QtCore.QTimer] = None
        # the field keeps its arrow keys while it has the focus (Ctrl+Up is a shortcut too):
        # a shared filter, not an event() override (cheaper: a form's fields get many events
        # as it is built)
        self.installEventFilter(_arrow_keeper())
        self.editingFinished.connect(self._finished)
        self.lineEdit().textEdited.connect(self._edited)
        self.lineEdit().textChanged.connect(lambda _t: self._sync_buttons())
        self.setToolTip("")
        if value is not None:
            self.load(value)

    # -- the value loaded / committed ---------------------------------------------------
    def load(self, value: int) -> None:
        """Show ``value`` as the field's own (the baseline Esc goes back to), quietly."""
        blocked = self.blockSignals(True)
        try:
            self.setValue(int(value))
        finally:
            self.blockSignals(blocked)
        self._orig = self.value()
        self.setProperty("orig", self._orig)

    def orig(self) -> Optional[int]:
        return self._orig

    def is_modified(self) -> bool:
        """The field shows something other than the value loaded or last committed (typed
        text not applied yet included)."""
        if self._orig is None:
            return False
        return self.lineEdit().text() != self.shown_text(self._orig)

    def shown_text(self, value: int) -> str:
        """The text the field shows for ``value``."""
        if self.specialValueText() and value == self.minimum():
            return self.specialValueText()
        return self.prefix() + self.textFromValue(value) + self.suffix()

    def _stop_steps(self) -> None:
        if self._step_timer is not None:
            self._step_timer.stop()
        _PENDING.discard(self)

    def revert(self) -> bool:
        """Back to the value loaded or last committed. False when there was nothing to put
        back."""
        self._stop_steps()
        if self._orig is None or not self.is_modified():
            return False
        blocked = self.blockSignals(True)
        try:
            self.setValue(self._orig)
            self.lineEdit().setText(self.shown_text(self._orig))
        finally:
            self.blockSignals(blocked)
        self.lineEdit().setModified(False)
        self.clear_invalid()
        return True

    def commit(self) -> None:
        """Apply the value now when it changed: ``valueCommitted``."""
        self._stop_steps()
        v = self.value()
        if self._orig is not None and v == self._orig:
            return
        self._orig = v
        self.setProperty("orig", v)
        self.valueCommitted.emit(v)

    def _finished(self) -> None:
        self.commit()

    # -- tool tip -----------------------------------------------------------------------
    def setToolTip(self, text: str) -> None:  # noqa: N802 -- Qt naming
        """The caller's tip, with how to type a sum under it (not on a read-only field)."""
        self._tip = str(text or "")
        if self.isReadOnly():
            QtWidgets.QSpinBox.setToolTip(self, self._tip)
            return
        QtWidgets.QSpinBox.setToolTip(self, (self._tip + "\n" if self._tip else "") + TIP)

    def setReadOnly(self, ro: bool) -> None:  # noqa: N802 -- Qt naming
        super().setReadOnly(ro)
        self.setToolTip(self._tip)

    # -- typing ---------------------------------------------------------------------------
    def _negative_ok(self) -> bool:
        lo = self.minimum() + (1 if self.specialValueText() else 0)
        return lo < 0

    def _body(self, text: str) -> str:
        t = text
        p, s = self.prefix(), self.suffix()
        if p and t.startswith(p):
            t = t[len(p) :]
        st = s.strip()
        if st and t.rstrip().endswith(st):
            t = t.rstrip()[: -len(st)]
        return t.strip()

    def _evaluate(self, body: str) -> Optional[int]:
        v = evaluate(body, self.value(), self._negative_ok())
        if v is None:
            return None
        lo = self.minimum() + (1 if self.specialValueText() else 0)
        n = round_half_away(v)
        if not self.clamp and not lo <= n <= self.maximum():
            return None
        return max(lo, min(self.maximum(), n))

    def range_tip(self) -> str:
        """The message of a typed result outside the range (``clamp=False``)."""
        lo = self.minimum() + (1 if self.specialValueText() else 0)
        return "Type a number from %d to %d. The value was not changed." % (lo, self.maximum())

    def validate(self, text: str, pos: int):  # noqa: D102 -- Qt API
        V = QtGui.QValidator
        if self.specialValueText() and text == self.specialValueText():
            return V.Acceptable, text, pos
        body = self._body(text)
        if not body:
            return V.Intermediate, text, pos
        if not _ALLOWED.fullmatch(body):
            return V.Invalid, text, pos
        if _PLAIN.fullmatch(body) and not is_relative(body, self._negative_ok()):
            n = int(body)
            ok = self.minimum() <= n <= self.maximum()
            return (V.Acceptable if ok else V.Intermediate), text, pos
        return V.Intermediate, text, pos  # a sum: worked out on Enter (fixup)

    def fixup(self, text: str) -> str:  # noqa: D102 -- Qt API
        if self.specialValueText() and text == self.specialValueText():
            return text
        body = self._body(text)
        v = self._evaluate(body)
        if v is None:
            if body and not self.property("ctgInvalid"):
                # (a mark the owner set while the user typed says more: it stays)
                out = not self.clamp and evaluate(body, self.value(), self._negative_ok()) is not None
                self.mark_invalid(self.range_tip() if out else BAD_TIP)
            return text  # (the field keeps the old value)
        self.clear_invalid()
        return self.prefix() + self.textFromValue(v) + self.suffix()

    def valueFromText(self, text: str) -> int:  # noqa: N802 -- Qt API
        if self.specialValueText() and text == self.specialValueText():
            return self.minimum()
        body = self._body(text)
        if _PLAIN.fullmatch(body) and not is_relative(body, self._negative_ok()):
            return int(body)
        v = self._evaluate(body)
        return self.value() if v is None else v

    def _edited(self, _text: str) -> None:
        if self.property("ctgInvalid"):
            self.clear_invalid()

    def mark_invalid(self, message: str) -> None:
        from .style import theme

        theme.mark_invalid(self, message)

    def clear_invalid(self) -> None:
        from .style import theme

        theme.mark_invalid(self, "")

    # -- stepping ---------------------------------------------------------------------------
    def steps_for(self, steps: int, mods) -> int:
        """``steps`` with the keyboard modifiers ``mods``: Shift 10 times, Ctrl the grid pitch
        (``grid_step``; without one Ctrl steps 10 times, Qt's own rule)."""
        if mods & QtCore.Qt.ShiftModifier:
            return steps * 10
        if mods & QtCore.Qt.ControlModifier:
            if self.grid_step is None:
                return steps * 10
            try:
                pitch = max(1, int(self.grid_step()))
            except Exception:  # noqa: BLE001  # swallow-ok: no grid known, a plain step
                pitch = self.singleStep()
            return steps * max(1, pitch // max(1, self.singleStep()))
        return steps

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt API
        k = e.key()
        if (
            k in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down, QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown)
            and not self.isReadOnly()
        ):
            up = k in (QtCore.Qt.Key_Up, QtCore.Qt.Key_PageUp)
            if not self.stepEnabled() & (
                QtWidgets.QAbstractSpinBox.StepUpEnabled if up else QtWidgets.QAbstractSpinBox.StepDownEnabled
            ):
                e.accept()
                return
            steps = 10 if k in (QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown) else 1
            self.stepBy(self.steps_for(steps if up else -steps, e.modifiers()))
            e.accept()
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:  # noqa: N802 -- Qt API
        # only a field with the focus takes the wheel: over a panel the wheel scrolls it
        dy = e.angleDelta().y()
        if not self.hasFocus() or not dy or self.isReadOnly():
            e.ignore()
            return
        self.stepBy(self.steps_for(1 if dy > 0 else -1, e.modifiers()))
        e.accept()

    def stepBy(self, steps: int) -> None:  # noqa: N802 -- Qt API
        super().stepBy(steps)
        if self.live_steps and not self.isReadOnly():
            if self._step_timer is None:
                self._step_timer = QtCore.QTimer(self)
                self._step_timer.setSingleShot(True)
                self._step_timer.timeout.connect(self.commit)
            from .prefs import timing_options

            self._step_timer.start(timing_options.STEP_PAUSE_MS)
            _PENDING.add(self)

    # -- the arrows ---------------------------------------------------------------------------
    #: the arrow buttons go when the field is too narrow for its text and them (a narrow
    #: dock: the text stays readable; the keys, the wheel and a label drag still step)
    auto_buttons = True

    def _sync_buttons(self) -> None:
        if not self.auto_buttons:
            return
        fm = self.fontMetrics()
        need = fm.horizontalAdvance(self.lineEdit().text() or "0") + 14
        want = (
            QtWidgets.QAbstractSpinBox.NoButtons
            if self.width() - 18 < need
            else QtWidgets.QAbstractSpinBox.UpDownArrows
        )
        if self.buttonSymbols() != want:
            self.setButtonSymbols(want)

    def resizeEvent(self, e) -> None:  # noqa: N802 -- Qt API
        super().resizeEvent(e)
        self._sync_buttons()

    def pending_steps(self) -> bool:
        """Arrow steps wait for the pause (tests)."""
        return self._step_timer is not None and self._step_timer.isActive()

    def note_drag(self) -> None:
        """A label drag (:class:`ScrubLabel`) is about to change the value (a field of
        several objects takes it as a change of each one's own value)."""


class ScrubLabel(QtWidgets.QLabel):
    """A label you drag sideways to change its number field."""

    #: the screen pixels a drag needs before it changes anything
    THRESHOLD = 3

    def __init__(
        self,
        text: str,
        field: NumberField,
        parent=None,
        enabled: bool = True,
        preview: Optional[Callable[[Optional[int]], None]] = None,
    ):
        super().__init__(text, parent)
        self.field = field
        self.setBuddy(field)
        self.preview = preview
        self._enabled = False
        self._press: Optional[QtCore.QPointF] = None
        self._last_x = 0.0
        self._units = 0.0
        self._start = 0
        self._dragging = False
        self.set_scrub_enabled(enabled)

    def set_scrub_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self._enabled:
            self.setCursor(QtCore.Qt.SizeHorCursor)
            tip = self.field.toolTip().split("\n")[0] if self.field.toolTip() else ""
            self.setToolTip(
                ((tip + "\n") if tip and tip != TIP else "")
                + "Drag sideways to change the value. Shift: faster. "
                "Alt: finer."
            )
        else:
            self.unsetCursor()

    def scrub_enabled(self) -> bool:
        return self._enabled and self.field.isEnabled() and not self.field.isReadOnly()

    def dragging(self) -> bool:
        return self._dragging

    @staticmethod
    def rate(mods) -> float:
        """Field units per screen pixel for the keyboard modifiers ``mods``: the Label drag
        speed (0.5 at Normal), Shift 10 times it, Alt a quarter."""
        base = _panels.SCRUB_RATE
        if mods & QtCore.Qt.ShiftModifier:
            return base * 10.0
        if mods & QtCore.Qt.AltModifier:
            return base / 4.0
        return base

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:  # noqa: N802 -- Qt API
        if e.button() != QtCore.Qt.LeftButton or not self.scrub_enabled():
            super().mousePressEvent(e)
            return
        self._press = e.globalPosition()
        self._last_x = self._press.x()
        self._units = 0.0
        self._start = self.field.value()
        self._dragging = False
        e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:  # noqa: N802 -- Qt API
        if self._press is None:
            super().mouseMoveEvent(e)
            return
        x = e.globalPosition().x()
        if not self._dragging:
            if abs(x - self._press.x()) < self.THRESHOLD:
                return
            self._dragging = True
            self._last_x = self._press.x()
            self.grabKeyboard()
        self.drag_by(x - self._last_x, e.modifiers())
        self._last_x = x
        e.accept()

    def drag_by(self, dx_px: float, mods=QtCore.Qt.NoModifier) -> None:
        """Move the value as a drag of ``dx_px`` screen pixels does (tests)."""
        if not self._dragging:
            self._dragging = True
            self._start = self.field.value()
            self._units = 0.0
        self._units += dx_px * self.rate(mods) / max(1, self.field.singleStep())
        v = self._start + round_half_away(self._units) * self.field.singleStep()
        lo = self.field.minimum() + (1 if self.field.specialValueText() else 0)
        v = max(lo, min(self.field.maximum(), v))
        if v != self.field.value():
            self.field.note_drag()
            self.field.setValue(v)
            if self.preview is not None:
                self.preview(v)

    def finish(self, commit: bool = True) -> None:
        """End the drag: ``commit`` applies the value once, else it goes back."""
        was = self._dragging
        self._press = None
        self._dragging = False
        self.releaseKeyboard()
        if self.preview is not None and was:
            self.preview(None)
        if not was:
            return
        if commit:
            self.field.commit()
        else:
            blocked = self.field.blockSignals(True)
            try:
                self.field.setValue(self._start)
            finally:
                self.field.blockSignals(blocked)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:  # noqa: N802 -- Qt API
        if self._press is None:
            super().mouseReleaseEvent(e)
            return
        if not self._dragging:
            # a click: the field takes the focus, as a buddy label's click does
            self._press = None
            self.field.setFocus(QtCore.Qt.MouseFocusReason)
            self.field.selectAll()
            return
        self.finish(True)
        e.accept()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt API
        if self._dragging and e.key() == QtCore.Qt.Key_Escape:
            self.finish(False)
            e.accept()
            return
        super().keyPressEvent(e)
