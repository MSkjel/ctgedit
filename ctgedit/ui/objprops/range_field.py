"""'Specify range' for an expression field."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

#: a Cicode numeric literal with an optional sign (the tokenizer's 0x103
#: integer / 0x104 float after an optional '-' / '+')
NUMBER = re.compile(r"[+-]?\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_RANGE_TAIL = re.compile(r"\((?P<lo>[^,()]*),(?P<hi>[^,()]*)\)(?P<trail>\s*)")

#: CtDraw's buffers: the composed expression is at most 253 characters
#: (0xfe incl. the terminator), each number at most 11 (0xb)
MAX_NUMBER = 11


@dataclass(frozen=True)
class ExprRange:
    """An expression split from its ``^(min,max)`` suffix. ``lo`` / ``hi``
    are the numbers' text exactly as stored (spaces included); ``trail`` is
    whitespace after the closing ')'."""

    expr: str
    lo: str
    hi: str
    trail: str = ""


def is_number(text: str) -> bool:
    return NUMBER.fullmatch((text or "").strip()) is not None


def split_range(text: str) -> Optional[ExprRange]:
    """``(expr)^(min,max)`` -> ExprRange, None when ``text`` carries no range
    CtDraw's parser would accept (it is then a plain expression)."""
    if not text or text[0] != "(":
        return None
    p = text.rfind("^")
    if p <= 0 or text[p - 1] != ")" or text[p + 1 : p + 2] != "(":
        return None
    m = _RANGE_TAIL.fullmatch(text, p + 1)
    if m is None or not is_number(m.group("lo")) or not is_number(m.group("hi")):
        return None
    return ExprRange(text[1 : p - 1], m.group("lo"), m.group("hi"), m.group("trail"))


def join_range(expr: str, lo: str, hi: str, trail: str = "") -> str:
    """CtDraw's (%s)^(%s,%s)."""
    return "(%s)^(%s,%s)%s" % (expr, lo, hi, trail)


def split_format(text: str) -> Optional[Tuple[str, str]]:
    """``(expr):fmt`` -> (expr, fmt) the way a format field parses it
    (the LAST ':' with ')' before it); None otherwise."""
    if not text or text[0] != "(":
        return None
    p = text.rfind(":")
    if p <= 0 or text[p - 1] != ")":
        return None
    fmt = text[p + 1 :]
    if not fmt or "(" in fmt or ")" in fmt:
        return None
    return text[1 : p - 1], fmt


def join_format(expr: str, fmt: str, lo: Optional[str] = None, hi: Optional[str] = None) -> str:
    """(%s):%s or, with a range, (%s)^(%s,%s):%s."""
    if lo is not None and hi is not None:
        return "(%s)^(%s,%s):%s" % (expr, lo, hi, fmt)
    return "(%s):%s" % (expr, fmt)


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.\\]*")
_STRING = re.compile(r'"(?:\^.|[^"^])*"')


def last_tag(expr: str, is_tag: Callable[[str], bool]) -> Optional[str]:
    """The last identifier in ``expr`` (strings skipped, function names
    skipped) that ``is_tag`` accepts."""
    body = _STRING.sub(" ", expr or "")
    found = None
    for m in _IDENT.finditer(body):
        rest = body[m.end() :].lstrip()
        if rest.startswith("("):
            continue  # a function call
        if is_tag(m.group(0)):
            found = m.group(0)
    return found


def default_range(expr: str, tag_range: Callable[[str], Optional[Tuple[str, str]]]) -> Tuple[str, str, str]:
    """(minimum, maximum, why) the compiler uses when 'Specify range' is off.
    ``tag_range(name)`` gives a variable tag's (ENG_ZERO, ENG_FULL) or None
    when ``name`` is not a variable tag."""
    ranges = {}

    def known(name):
        r = tag_range(name)
        if r is not None:
            ranges[name] = r
        return r is not None

    tag = last_tag(expr, known)
    # the text the Graphics Builder fills in when 'Specify range' is ticked: a
    # tag's ENG_ZERO / ENG_FULL as stored ('0', '200.0'), else '0.0' /
    # '32000.0', without a tag '0.0' / '100.0'
    if tag is None:
        return "0.0", "100.0", "no variable tag in the expression"
    lo, hi = ranges[tag]
    if (lo or "").strip() and (hi or "").strip():
        return lo.strip(), hi.strip(), "the engineering range of %s" % tag
    return "0.0", "32000.0", "%s has no engineering range" % tag


class RangeField(QtWidgets.QWidget):
    """[x] Specify range   Minimum [ ]   Maximum [ ]  -- attached to an
    :class:`~ctgedit.ui.objprops.expression.ExpressionField`, which composes
    the stored text from it. The numbers are disabled while the box is clear
    and then show the default range (greyed) the compiler applies."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.check = QtWidgets.QCheckBox("Specify range")
        self.check.setToolTip("Set the expression's minimum and maximum")
        lay.addWidget(self.check)
        self.minimum = QtWidgets.QLineEdit()
        self.maximum = QtWidgets.QLineEdit()
        rx = QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression(r"\s*[+-]?\s*[0-9]*\.?[0-9]*([eE][+-]?[0-9]*)?\s*"), self
        )
        for label, ed in (("Minimum:", self.minimum), ("Maximum:", self.maximum)):
            ed.setValidator(rx)
            ed.setMaxLength(MAX_NUMBER)
            ed.setMaximumWidth(90)
            lay.addWidget(QtWidgets.QLabel(label))
            lay.addWidget(ed)
            ed.textChanged.connect(self._emit)
        lay.addStretch(1)
        self.check.toggled.connect(self._sync)
        self.check.toggled.connect(self._emit)
        self._raw = ("", "")
        self._trail = ""
        self._syncing = False
        self._read_only = False
        self._sync()

    # -- state ------------------------------------------------------------------
    def specified(self) -> bool:
        return self.check.isChecked()

    def set_range(self, r: Optional[ExprRange]) -> None:
        """Show a parsed range (None = not specified); the stored number text
        is remembered so an untouched value re-joins identically."""
        self._syncing = True
        try:
            if r is None:
                self._raw, self._trail = ("", ""), ""
                self.check.setChecked(False)
                self.minimum.setText("")
                self.maximum.setText("")
            else:
                self._raw, self._trail = (r.lo, r.hi), r.trail
                self.check.setChecked(True)
                self.minimum.setText(r.lo.strip())
                self.maximum.setText(r.hi.strip())
        finally:
            self._syncing = False
        self._sync()

    def numbers(self) -> Tuple[str, str, str]:
        """(min, max, trail) as they are written: the stored text when the
        field still shows it, else the typed number."""
        out = []
        for ed, raw in zip((self.minimum, self.maximum), self._raw):
            t = ed.text().strip()
            out.append(raw if raw.strip() == t and raw else t)
        return out[0], out[1], self._trail

    def problem(self) -> Optional[str]:
        """What stops the range from being stored (None = fine)."""
        if not self.specified():
            return None
        lo, hi = self.minimum.text().strip(), self.maximum.text().strip()
        if not lo or not hi:
            return "Enter both a Minimum and a Maximum, or clear Specify range."
        for name, t in (("Minimum", lo), ("Maximum", hi)):
            if not is_number(t):
                return "%s must be a number (for example 0, -50 or 12.5)." % name
        return None

    def set_default_hint(self, lo: str, hi: str, why: str = "") -> None:
        self.minimum.setPlaceholderText(lo)
        self.maximum.setPlaceholderText(hi)
        tip = "Default range %s to %s%s" % (lo, hi, (" (%s)" % why) if why else "")
        for ed in (self.minimum, self.maximum):
            ed.setToolTip(tip + ". Tick Specify range to set your own.")

    def setReadOnly(self, ro: bool) -> None:  # noqa: N802 -- Qt naming
        self._read_only = bool(ro)
        self.check.setEnabled(not ro)
        self._sync()

    # -- internals ----------------------------------------------------------------
    def _sync(self, *_a) -> None:
        on = self.check.isChecked() and not self._read_only
        for ed in (self.minimum, self.maximum):
            ed.setEnabled(on)

    def _emit(self, *_a) -> None:
        if not self._syncing:
            self.changed.emit()
