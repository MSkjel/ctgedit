"""A colour field holding an on / off pair."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

NO_COLOUR = 0xFF000000
#: what the Transparent choice writes (citect.pal's 'Transparent' entry)
TRANSPARENT = 0xFFFFFFFF


def _qcolor(c: Optional[int]) -> Optional[QtGui.QColor]:
    if c is None or (c >> 24) & 0xFF == 0xFF:
        return None
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


try:
    from ..colour_picker import ColourButton as _ColourButton
except Exception:  # noqa: BLE001 -- only when the picker module cannot load
    _ColourButton = QtWidgets.QToolButton


class _Swatch(_ColourButton):
    """A ColourButton showing one half of the field's pair; a click is the
    field's (it knows the flashing / Transparent rules)."""

    def __init__(self, field, title: str):
        super().__init__(None, None, title=title)
        self._field = field
        self._colour: Optional[int] = None
        self._off: Optional[int] = None

    def choose(self) -> None:  # noqa: D102 -- ColourButton's click
        self._field._choose(self is self._field.swatch_off)

    def set_colour(self, c: Optional[int], off: Optional[int] = None) -> None:
        self._colour = c
        self._off = off
        on = NO_COLOUR if c is None else c
        self.set_pair((on, on if off is None else off))
        if c is None or _qcolor(c) is None:
            self.setToolTip("Transparent")


class ColourPairField(QtWidgets.QWidget):
    """An (on, off) colour pair. ``flash`` = offer the Flashing option;
    ``transparent`` = offer a Transparent choice (both halves the no-colour
    value, e.g. a fill level's background)."""

    changed = QtCore.Signal()

    def __init__(self, title: str = "Colour", flash: bool = True, parent=None, *, transparent: bool = False):
        super().__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self._title = title
        self._on: int = NO_COLOUR
        self._off: int = NO_COLOUR
        self._syncing = False
        self._last = 0xFFFFFF  # the colour Transparent was ticked over
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(_sp(1))
        self.swatch = _Swatch(self, title)
        self.swatch_off = None
        h.addWidget(self.swatch)
        self.transparent = QtWidgets.QCheckBox("Transparent")
        self.transparent.setToolTip("No colour: what is behind shows through")
        self.transparent.toggled.connect(self._on_transparent)
        h.addWidget(self.transparent)
        self.transparent.setVisible(transparent)
        self._transparent_offered = transparent
        self.flashing = QtWidgets.QCheckBox("Flashing")
        self.flashing.setToolTip("Alternate between two colours at runtime")
        self.flashing.toggled.connect(self._on_flash)
        h.addWidget(self.flashing)
        self.flashing.setVisible(flash)
        self._flash_offered = flash
        self.swatch_off = _Swatch(self, "%s (flashing)" % title)
        h.addWidget(self.swatch_off)
        self.swatch_off.setVisible(False)
        h.addStretch(1)

    # -- values ------------------------------------------------------------------
    def set_pair(self, on: Optional[int], off: Optional[int] = None) -> None:
        """Show a stored pair (no signal)."""
        on = NO_COLOUR if on is None else on
        off = on if off is None else off
        self._on, self._off = on & 0xFFFFFFFF, off & 0xFFFFFFFF
        self._syncing = True
        try:
            self.flashing.setChecked(self._on != self._off)
            self.transparent.setChecked(self.is_transparent())
        finally:
            self._syncing = False
        self._show()

    def pair(self) -> Tuple[int, int]:
        return self._on, self._off

    def is_flashing(self) -> bool:
        return self._on != self._off

    def is_transparent(self) -> bool:
        """Both halves are the no-colour sentinel (high byte 0xFF)."""
        return (self._on >> 24) & 0xFF == 0xFF and (self._off >> 24) & 0xFF == 0xFF

    def pick(self, on: int, off: Optional[int] = None) -> None:
        """Set the colour as the user does (both halves unless ``off``); a
        half with the high byte 0xFF stays 'no colour'."""
        on &= 0xFFFFFFFF if (on >> 24) & 0xFF == 0xFF else 0xFFFFFF
        off = on if off is None else off & (0xFFFFFFFF if (off >> 24) & 0xFF == 0xFF else 0xFFFFFF)
        self._on, self._off = on, off
        self._syncing = True
        try:
            self.flashing.setChecked(on != off)
            self.transparent.setChecked(self.is_transparent())
        finally:
            self._syncing = False
        self._show()
        self.changed.emit()

    def pick_transparent(self) -> None:
        """Set 'no colour' as the user does (the Transparent box / the
        picker's Transparent): the palette's Transparent, 0xffffffff."""
        if not self.is_transparent():
            self._last = self._on & 0xFFFFFF
        self._on = self._off = TRANSPARENT
        self._syncing = True
        try:
            self.flashing.setChecked(False)
            self.transparent.setChecked(True)
        finally:
            self._syncing = False
        self._show()
        self.changed.emit()

    def _on_transparent(self, on: bool) -> None:
        if self._syncing:
            return
        if on:
            self.pick_transparent()
        else:
            self.pick(self._last)

    def _show(self) -> None:
        clear = self.is_transparent()
        self.swatch.set_colour(self._on, None if self.flashing.isChecked() else self._on)
        self.swatch_off.set_colour(self._off)
        self.flashing.setVisible(self._flash_offered and not (clear and self._transparent_offered))
        self.swatch_off.setVisible(self.flashing.isChecked() and not clear)

    def _on_flash(self, on: bool) -> None:
        if self._syncing:
            return
        if not on and self._off != self._on:
            self._off = self._on  # one colour again
            self._show()
            self.changed.emit()
        self._show()

    def _choose(self, second: bool) -> None:
        """Open the colour picker for the colour (``second``: its flashing half)."""
        from ..colour_picker import choose_colour

        title = "%s (flashing)" % self._title if second else self._title
        cur = (
            (self._off, self._off)
            if second
            else ((self._on, self._off) if self.flashing.isChecked() else (self._on, self._on))
        )
        # (a flashing colour's halves are both colours or both Transparent,
        # as CtDraw keeps them)
        pair = choose_colour(
            self,
            cur,
            title=title,
            flash=self._flash_offered and not second,
            transparent=self._transparent_offered and not second,
            anchor=(self.swatch_off if second else self.swatch),
        )
        if pair is None:
            return
        on, off = pair
        if on >> 24 == 0xFF and off >> 24 == 0xFF and not second:
            self.pick_transparent()
        elif second:
            self.pick(self._on, on)
        elif on != off:
            self.pick(on, off)  # a flashing favourite: both halves
        elif self.flashing.isChecked():
            self.pick(on, self._off)
        else:
            self.pick(on)

    def set_read_only(self, ro: bool) -> None:
        for w in (self.swatch, self.swatch_off, self.flashing, self.transparent):
            w.setEnabled(not ro)
