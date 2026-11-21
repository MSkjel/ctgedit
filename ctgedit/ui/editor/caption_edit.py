"""A multi-line caption field."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .inplace_text import enter_action, enter_stores, plain_enter
from ..objprops.scroll import LinesMixin


class CaptionEdit(LinesMixin, QtWidgets.QPlainTextEdit):
    """A caption field: Enter adds a line, Ctrl+Enter or leaving finishes (configurable)."""

    editingFinished = QtCore.Signal()

    def __init__(self, text: str = "", parent=None, lines: int = 3, min_lines=None):
        QtWidgets.QPlainTextEdit.__init__(self, parent)
        self._tip = None
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.setText(text)
        self.set_tip(self.keys_hint)
        self._init_lines(lines if min_lines is None else min_lines)

    @staticmethod
    def keys_hint() -> str:
        """The field's tool tip: how a new line is typed and how the text applies."""
        if enter_stores():
            return "Shift+Enter starts a new line; Enter or leaving the field applies the text."
        return "Enter starts a new line; Ctrl+Enter or leaving the field applies the text."

    # -- the tool tip ----------------------------------------------------------
    def set_tip(self, tip) -> None:
        """The tool tip: a function giving it (made again by :meth:`refresh_tip`: it names
        the keys of Settings > Editing > Text > "In a text, Enter") or a fixed text."""
        self._tip = tip if callable(tip) else None
        QtWidgets.QPlainTextEdit.setToolTip(self, tip() if callable(tip) else str(tip or ""))

    def setToolTip(self, tip: str) -> None:  # noqa: N802 -- Qt naming
        """A fixed tool tip (not made again from the setting)."""
        self.set_tip(str(tip or ""))

    def refresh_tip(self) -> None:
        """The tool tip made again from its function (the setting may have changed)."""
        if self._tip is not None:
            tip = self._tip()
            if tip != self.toolTip():
                QtWidgets.QPlainTextEdit.setToolTip(self, tip)

    def enterEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        self.refresh_tip()
        super().enterEvent(e)

    def focusInEvent(self, e: QtGui.QFocusEvent) -> None:  # noqa: N802 -- Qt naming
        self.refresh_tip()
        super().focusInEvent(e)

    # -- the QLineEdit calls ---------------------------------------------------
    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(str(text or ""))
        self.moveCursor(QtGui.QTextCursor.End)  # like QLineEdit.setText

    def revert(self) -> bool:
        """Esc: the text the field was loaded with (its 'orig' property) back. False when
        there is none or nothing was typed."""
        orig = self.property("orig")
        if orig is None or self.text() == str(orig):
            return False
        self.setText(str(orig))
        return True

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if (
            e.key() == QtCore.Qt.Key_Escape
            and not e.modifiers() & ~QtCore.Qt.KeypadModifier
            and self.revert()
        ):
            e.accept()  # (a second Esc goes on: the dialog / the panel)
            return
        act = enter_action(e)
        if act == "store":
            e.accept()
            self.editingFinished.emit()
            return
        if act == "newline":
            super().keyPressEvent(plain_enter(e))  # (Shift+Enter: a new line)
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e: QtGui.QFocusEvent) -> None:
        super().focusOutEvent(e)
        # a menu or another program taking the focus is not "done"
        if e.reason() not in (QtCore.Qt.PopupFocusReason, QtCore.Qt.ActiveWindowFocusReason):
            self.editingFinished.emit()
