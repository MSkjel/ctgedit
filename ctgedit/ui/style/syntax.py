"""The colours of Cicode in the expression and command fields (cicode_lex's token kinds).
Each colour reads at 4.5:1 or more on every surface a field sits on
(tests/test_cicode_assist.py). The high-contrast scheme keeps the window text colour and
tells the kinds apart by weight and slant."""

from __future__ import annotations

from typing import Dict, Optional

from PySide6 import QtGui

from ... import cicode_lex as L
from . import theme

#: colour role -> colour, per scheme
DARK = {
    "keyword": "#c792ea",
    "type": "#82aaff",
    "string": "#e2a27c",
    "number": "#b5d99c",
    "function": "#e6d68a",
    "param": "#4ec9b0",
    "comment": "#8fb87a",
    "label": "#6cc4ff",
    "name": "#9cdcfe",
    "format": "#d7ba7d",
}
LIGHT = {
    "keyword": "#8f1fb0",
    "type": "#1a4fc4",
    "string": "#a3271b",
    "number": "#0f6b48",
    "function": "#6b4f10",
    "param": "#156b82",
    "comment": "#2f6e1f",
    "label": "#0b5fa5",
    "name": "#1c3f7a",
    "format": "#7a4d00",
}
#: token kind -> colour role (the kinds left out keep the field's text colour)
ROLE = {
    L.KEYWORD: "keyword",
    L.OPERATOR_WORD: "keyword",
    L.RANGE: "keyword",
    L.TYPE: "type",
    L.STRING: "string",
    L.NUMBER: "number",
    L.FUNCTION: "function",
    L.PARAM: "param",
    L.ASSOCIATION: "param",
    L.COMMENT: "comment",
    L.LABEL: "label",
    L.NAME: "name",
    L.FIELD: "name",
    L.FORMAT: "format",
}
#: colour role -> (bold, italic): in every scheme (high contrast tells the kinds apart so)
STYLE = {
    "keyword": (True, False),
    "type": (True, False),
    "param": (True, True),
    "comment": (False, True),
    "string": (False, True),
}


def colours() -> Dict[str, str]:
    """The active scheme's colours (high contrast: the window text for every role)."""
    scheme = theme.current_scheme()
    if scheme == "contrast":
        text = theme.COLORS.get("text", "#000000")
        return {k: text for k in DARK}
    return dict(LIGHT if scheme == "light" else DARK)


def role_format(role: str, table: Optional[Dict[str, str]] = None) -> QtGui.QTextCharFormat:
    """The format of a colour role."""
    table = colours() if table is None else table
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QColor(table[role]))
    bold, italic = STYLE.get(role, (False, False))
    if bold:
        f.setFontWeight(QtGui.QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


def problem_format(level: str, base: Optional[QtGui.QTextCharFormat] = None) -> QtGui.QTextCharFormat:
    """A wavy underline in the danger (an error) or warn colour, over ``base``."""
    f = QtGui.QTextCharFormat(base) if base is not None else QtGui.QTextCharFormat()
    f.setUnderlineStyle(QtGui.QTextCharFormat.WaveUnderline)
    f.setUnderlineColor(QtGui.QColor(theme.COLORS.get("danger" if level == "error" else "warn", "#d9a441")))
    return f
