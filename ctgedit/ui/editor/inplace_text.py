"""In-place caption editing on the canvas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..style.theme import color, fixed

if TYPE_CHECKING:
    from .canvas import CanvasView

_CTRL = QtCore.Qt.ControlModifier
_SHIFT = QtCore.Qt.ShiftModifier
_RETURN_KEYS = (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)

# Settings > Editing > Text > "In a text, Enter" (opt/text_enter): False = Enter starts a new
# line and Ctrl+Enter stores the text (the Graphics Builder's, help 1172129); True = Enter
# stores it and Shift+Enter starts a new line. The canvas's editor and the caption fields
# (ui/editor/caption_edit) read it; ui/editor/canvas_options.apply_canvas_settings sets it
_ENTER = {"stores": False}


def enter_stores() -> bool:
    """Enter stores the text (Settings > Editing > Text), else it starts a new line."""
    return _ENTER["stores"]


def set_enter_stores(on: bool) -> None:
    _ENTER["stores"] = bool(on)


def enter_hint() -> str:
    """How a new line is typed, for a tool tip: 'Enter starts a new line.' or 'Shift+Enter
    starts a new line.'"""
    return "Shift+Enter starts a new line." if _ENTER["stores"] else "Enter starts a new line."


def enter_action(e: QtGui.QKeyEvent) -> str:
    """What an Enter key press does in a text: 'store', 'newline' (typed as a plain Enter)
    or '' (not an Enter key; anything else the editor does as before)."""
    if e.key() not in _RETURN_KEYS:
        return ""
    mods = e.modifiers()
    if _ENTER["stores"]:
        return "newline" if mods & _SHIFT and not mods & _CTRL else "store"
    return "store" if mods & _CTRL else ""


def plain_enter(e: QtGui.QKeyEvent) -> QtGui.QKeyEvent:
    """A copy of the Enter key press ``e`` without its modifiers (Shift+Enter typed as the
    plain Enter that starts a new line)."""
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, e.key(), QtCore.Qt.NoModifier, "\r")


class InplaceText(QtWidgets.QGraphicsTextItem):
    """The in-place caption editor."""

    MIN_WIDTH = 24.0

    def __init__(self, view: "CanvasView", text: str, font: QtGui.QFont):
        super().__init__()
        self._view = view
        self.setFont(font)
        self.setDefaultTextColor(fixed("edit_text"))
        self.document().setDocumentMargin(1)
        self.setPlainText(text)
        self.setTextInteractionFlags(QtCore.Qt.TextEditorInteraction)
        self.setZValue(1e7)

    def boundingRect(self) -> QtCore.QRectF:
        r = super().boundingRect()
        if r.width() < self.MIN_WIDTH:
            r.setWidth(self.MIN_WIDTH)
        return r

    def paint(self, painter, option, widget=None) -> None:
        painter.save()
        pen = QtGui.QPen(color("accent"))
        pen.setCosmetic(True)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(fixed("edit_box"))
        painter.drawRect(self.boundingRect())
        painter.restore()
        super().paint(painter, option, widget)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() == QtCore.Qt.Key_Escape:
            e.accept()
            QtCore.QTimer.singleShot(0, self, lambda: self._view._finish_from(self, False))
            return
        act = enter_action(e)
        if act == "store" or e.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            # Ctrl+Enter (or Enter, by Settings) = done; Tab leaves the text (a caption holds
            # no tab)
            e.accept()
            QtCore.QTimer.singleShot(0, self, lambda: self._view._finish_from(self, True))
            return
        if act == "newline":
            super().keyPressEvent(plain_enter(e))  # (Shift+Enter: a new line)
            e.accept()
            return
        super().keyPressEvent(e)

    def sceneEvent(self, e: QtCore.QEvent) -> bool:
        # QGraphicsTextItem types Tab / Backtab from sceneEvent itself (they
        # never reach keyPressEvent): route them to the commit above
        if e.type() == QtCore.QEvent.KeyPress and e.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().sceneEvent(e)

    def focusOutEvent(self, e: QtGui.QFocusEvent) -> None:
        super().focusOutEvent(e)
        # a menu, a popup or switching to another program is not "done":
        # the editor stays open for when the focus comes back
        if e.reason() in (
            QtCore.Qt.ActiveWindowFocusReason,
            QtCore.Qt.PopupFocusReason,
            QtCore.Qt.MenuBarFocusReason,
        ):
            return
        QtCore.QTimer.singleShot(0, self, lambda: self._view._focus_left(self))
