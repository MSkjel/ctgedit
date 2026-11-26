"""Declarative sheet fields, tabs and their widgets."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import re

from PySide6 import QtCore, QtWidgets

from ..number_field import NumberField

# field length limits (CtDraw's 'Expression is too long' sizes / DBF widths)
_L_EXPR = 253  # movement / scaling / rotation / level / commands
_L_COND = 254  # hidden when, disable when, ON colour when
_L_STATE = 128  # multi-state conditions
_L_SLIDER = 79  # slider tag
_L_TOOLTIP = 48  # COMMENT (pgdynobj 48)
_L_LOG = 32  # MSG_LOG (pgdynobj 32)
_L_NAME = 32  # __AN_NAME__ (CtDraw copies 0x21 bytes)
_L_DESC = 128  # DESC (pgdynobj 128)
_L_SHORT = 16  # AREA / PRIV / DEVICE (pgdynobj 16)
_L_AX_NAME = 64  # ACTIVEXEXT_*NAME (CtDraw's field takes 64)
_L_AX_CLASS = 16  # ACTIVEXEXT_*EVENTCLASS (CtDraw's field takes 16)

#: the number fields the object sheet and the Properties panel share, one table: kind ->
#: (lowest, highest, unit). CtDraw's own limits: the 'At minimum / maximum' spins of the
#: movement, scaling and slider pages (-32767 to 32767), the angles
#: (-360 to 360), the fill level percentages (0 to 100) and the touch
#: repeat rate (1 to 99999 ms)
NUMBER_RANGES = {
    "offset": (-32767, 32767, " px"),
    "size": (-32767, 32767, " %"),
    "angle": (-360, 360, "°"),
    "fill": (0, 100, " %"),
    "repeat": (1, 99999, " ms"),
}
#: bag key -> its NUMBER_RANGES kind
NUMBER_KEYS = {
    "MVXMINPT": "offset",
    "MVXMAXPT": "offset",
    "MVYMINPT": "offset",
    "MVYMAXPT": "offset",
    "SZXMINPC": "size",
    "SZXMAXPC": "size",
    "SZYMINPC": "size",
    "SZYMAXPC": "size",
    "ROTMINPC": "angle",
    "ROTMAXPC": "angle",
    "FILMINPC": "fill",
    "FILMAXPC": "fill",
    "RPT_RATE": "repeat",
}


def number_range(key: str):
    """(lowest, highest, unit) of the number key ``key`` (NUMBER_KEYS), else None."""
    kind = NUMBER_KEYS.get(str(key or "").upper())
    return NUMBER_RANGES[kind] if kind else None


#: CtDraw's disable styles: the Disable page's four radios 4011..4014 and
#: the value table (load / apply):
#: Embossed / Grayed / Hidden / Unchanged (ctgedit says 'Greyed')
_GREY_STYLES = (
    ("Embossed", "GRAY_SUNK"),
    ("Greyed", "GRAY_PART"),
    ("Hidden", "GRAY_HIDE"),
    ("Unchanged", "GRAY_UNCHANGED"),
)
#: a slider's *SLMODE: bit 0x1 = the slider is on,
#: 0x1000 = continuous update ('4097' continuous, '1' on release -- the
#: automation strings)
_SL_ON, _SL_CONTINUOUS = 0x1, 0x1000

_LINE_STYLES = ["Solid", "Dash", "Dot", "Dash Dot", "Dash Dot Dot"]

#: the Wizard menu entries an expression field offers by default
WIZARD_DEFAULT = ("tag", "function")


class _Field:
    """One sheet field bound to a bag key."""

    __slots__ = (
        "label",
        "key",
        "kind",
        "limit",
        "lo",
        "hi",
        "default",
        "suffix",
        "choices",
        "on",
        "off",
        "tag",
        "range",
        "wizard",
        "loose",
    )

    def __init__(
        self,
        label,
        key,
        kind="expr",
        *,
        limit=None,
        lo=-32768,
        hi=32767,
        default=0,
        suffix="",
        choices=(),
        on="TRUE",
        off=None,
        tag=None,
        range=False,
        wizard=WIZARD_DEFAULT,
        loose=False,
    ):
        self.label, self.key, self.kind, self.limit = label, key, kind, limit
        self.lo, self.hi, self.default, self.suffix = lo, hi, default, suffix
        self.choices, self.on, self.off, self.tag = choices, on, off, tag
        self.range, self.wizard = range, tuple(wizard)
        # a check that reads ticked for ANY non-empty stored value (CtDraw's Disable box,
        # any text ticks it; its apply writes 'TRUE')
        self.loose = loose


class _Sub:
    """A sub-tab: its fields, the WHOLE key family Clear Property removes
    (keys without a field too; 'NAME_*' = NAME_<n>), and the key that switches
    the property on (a key two properties share -- a slider's offsets are the
    movement's -- is kept while the other property is on)."""

    __slots__ = ("title", "fields", "family", "primary", "note")

    def __init__(self, title, fields, family=(), primary=None, note=""):
        self.title, self.fields, self.family = title, fields, tuple(family)
        self.primary, self.note = primary, note


def _i(label, key, default, suffix="", lo=-32768, hi=32767):
    return _Field(label, key, "int", default=default, suffix=suffix, lo=lo, hi=hi)


class _OptionalSpin(NumberField):
    """An integer field whose key may also be absent: the value below the
    range reads '(not set)'; stepping up from it starts at ``default``. It takes
    a sum ('100+20', '*2': ui/number_field)."""

    NOT_SET = "(not set)"

    def __init__(self, lo: int, hi: int, default: int, suffix: str = "", parent=None):
        super().__init__(parent, lo=lo - 1, hi=hi, suffix=suffix)
        self._unset = lo - 1
        self.locked = False
        self._default = max(lo, min(hi, default))
        self.setSpecialValueText(self.NOT_SET)
        self.setValue(self._unset)

    def stepBy(self, steps: int) -> None:
        if self.value() == self._unset and steps > 0:
            self.setValue(self._default)
            return
        super().stepBy(steps)

    def key_value(self):
        return None if self.value() == self._unset else str(self.value())

    def set_key_value(self, value) -> None:
        """Show ``value``. A stored value the box cannot show as it is (not a
        plain integer, or out of range -- e.g. after Convert to engine keys)
        reads '(not set)' and the box is LOCKED, so the value is never
        rewritten from here (Clear Property still removes it)."""
        text = None if value is None else str(value)
        ok = text is None or (
            re.fullmatch(r"-?\d+", text) is not None and self._unset < int(text) <= self.maximum()
        )
        self.locked = not ok
        self.setEnabled(ok)
        self.setToolTip(
            ""
            if ok
            else "The stored value '%s' is not a number this field can show. It is kept unchanged." % text
        )
        self.load(int(text) if ok and text is not None else self._unset)


class _RadioChoice(QtWidgets.QWidget):
    """A choice shown as radio buttons."""

    currentIndexChanged = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(_sp(0))
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(lambda k: self.setCurrentIndex(k + 1))
        self._items: list = []  # (label, value)
        self._index = 0

    def addItem(self, label, value=None) -> None:  # noqa: N802 -- QComboBox naming
        if not self._items and value is None:
            return  # the '(not set)' entry is index 0 itself
        b = QtWidgets.QRadioButton(str(label))
        self._group.addButton(b, len(self._items))
        self._lay.addWidget(b)
        self._items.append((str(label), value))
        self._show()

    def buttons(self) -> list:
        return list(self._group.buttons())

    def count(self) -> int:
        return len(self._items) + 1

    def itemData(self, n: int):  # noqa: N802
        return self._items[n - 1][1] if 1 <= n <= len(self._items) else None

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def currentData(self):  # noqa: N802
        return self.itemData(self._index)

    def currentText(self) -> str:  # noqa: N802
        if 1 <= self._index <= len(self._items):
            return self._items[self._index - 1][0]
        return self._items[0][0] if self._items else ""

    def setCurrentIndex(self, n: int) -> None:  # noqa: N802
        n = n if 0 <= n <= len(self._items) else 0
        changed = n != self._index
        self._index = n
        self._show()
        if changed:
            self.currentIndexChanged.emit(n)

    def _show(self) -> None:
        k = max(0, self._index - 1)
        b = self._group.button(k)
        if b is not None:
            b.setChecked(True)
