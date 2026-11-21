"""Lightweight popovers."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .colour_picker import ColourPicker


class Popover(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setObjectName("Popover")
        self.setStyleSheet("#Popover { background: #23262b; border: 1px solid #3a3f46; border-radius: 6px; }")

    def open_under(self, widget: QtWidgets.QWidget) -> None:
        self.adjustSize()
        pos = widget.mapToGlobal(QtCore.QPoint(0, widget.height() + 2))
        self.move(pos)
        self.show()


class ColourPopover(ColourPicker):
    """The drawing-colour picker; emits ``picked(0x00RRGGBB)``."""

    picked = QtCore.Signal(int)

    def __init__(self, current: int, parent=None):
        super().__init__(
            (current & 0xFFFFFF, current & 0xFFFFFF),
            parent,
            title="Drawing colour for new objects",
            flash=False,
            transparent=False,
        )

    def pick_pair(self, on: int, off=None) -> bool:
        off = on if off is None else off
        if not self._allowed_pair((on & 0xFFFFFFFF, off & 0xFFFFFFFF)):
            return False
        from .colour_picker import remember_colour

        self.result = (on & 0xFFFFFF, on & 0xFFFFFF)
        remember_colour(on & 0xFFFFFF)
        self.picked.emit(on & 0xFFFFFF)
        self._close()
        return True

    def _pick(self, colour: int) -> None:
        """Choose ``colour`` (a swatch click)."""
        self.pick_pair(colour & 0xFFFFFF)
