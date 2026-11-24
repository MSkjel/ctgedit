"""Edit in a Larger Box: an expression or command field's text in a multi-line box with the
same Cicode help (cicode_assist: colours, underlines, hints, completion). Its line breaks
become spaces in the field."""

from __future__ import annotations

from typing import List

from PySide6 import QtCore, QtGui, QtWidgets

from ... import cicode_lex as L
from ..style.theme import px


def one_line(text: str) -> str:
    """``text`` with each line break a space (the field holds one line)."""
    return " ".join(part.strip() for part in text.replace("\r\n", "\n").split("\n") if part.strip())


def comment_problem(text: str) -> str:
    """Why ``text`` cannot become one line: a '!' or '//' comment ends at its line, so the
    lines after it would join the comment ('' it can)."""
    tokens = L.tokenize(text)
    for k, t in enumerate(tokens):
        if t.kind != L.COMMENT or text.startswith("/*", t.start):
            continue
        if any(u.kind != L.COMMENT or not text.startswith("/*", u.start) for u in tokens[k + 1 :]):
            return (
                "A comment that starts with ! or // runs to the end of the line, and the "
                "lines after it would join it. Use /* */ for this comment."
            )
    return ""


class CicodeBoxDialog(QtWidgets.QDialog):
    """The larger box: ``kind`` 'expr' or 'command'."""

    def __init__(self, text: str, context, kind: str = "expr", read_only: bool = False, parent=None):
        super().__init__(parent)
        from .cicode_assist import CicodeAssist

        self.setWindowTitle("Edit Command" if kind == "command" else "Edit Expression")
        self.setSizeGripEnabled(True)
        lay = QtWidgets.QVBoxLayout(self)
        self.edit = QtWidgets.QPlainTextEdit()
        self.edit.setPlainText(text)
        self.edit.setReadOnly(read_only)
        self.edit.setTabChangesFocus(True)
        self.edit.setAccessibleName(self.windowTitle())
        lay.addWidget(self.edit, 1)
        self.assist = CicodeAssist(
            self.edit, context, "command" if kind == "command" else "expr", complete=True
        )
        self.note = QtWidgets.QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.edit.textChanged.connect(self._update_note)
        self._update_note()
        self.resize(px(640), px(280))
        cur = self.edit.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        self.edit.setTextCursor(cur)
        self.edit.setFocus(QtCore.Qt.OtherFocusReason)

    def problems(self) -> List[str]:
        why = comment_problem(self.edit.toPlainText())
        return [why] if why else []

    def _update_note(self) -> None:
        probs = self.problems()
        if probs:
            self.note.setText(probs[0])
        elif self.edit.isReadOnly():
            self.note.setText("The field is read-only.")
        else:
            self.note.setText("Each line break becomes a space in the field.")
        ok = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        ok.setEnabled(not probs)

    def text(self) -> str:
        """The text for the field: one line."""
        return one_line(self.edit.toPlainText())

    def accept(self) -> None:
        if self.problems():
            return
        super().accept()
