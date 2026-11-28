"""Small widgets of the Properties panel's forms: swatches, mixed-value fields, the
Add animation combo, the area field."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from ..style.theme import sp as _sp
import contextlib
import functools
import weakref
from PySide6 import QtCore, QtWidgets
from ..number_field import NumberField, evaluate, is_relative, round_half_away
from ...infra import caches as _caches
from .properties_parts import ICON_CACHE

if TYPE_CHECKING:
    from .properties import PropertyPanel


def fill_level_companions(props: dict) -> list:
    """The keys the object sheet's Fill > Level page stores with a NEW level expression
    (LevelEditor.apply): FILMINPC 0 and FILMAXPC 100 when absent, and the
    background FILCOL when absent; in its order."""
    from ...format import fillcolour as FC

    have = {k.upper() for k in props}
    out = []
    for k, v in (("FILMINPC", "0"), ("FILMAXPC", "100")):
        if k not in have:
            out.append((k, v))
    if "FILCOL" not in have:
        out.append(("FILCOL", FC.fmt_pair(FC.level_background(props))))
    return out


@functools.lru_cache(maxsize=1)
def sheet_labels() -> dict:
    """KEY -> the label the object sheet gives its field ('VISIBLE' -> 'Hidden when'), from
    every objprops module's fields (All properties names its rows by them)."""
    out = {}
    try:
        from ..objprops import modules as M

        for m in M.all_modules():
            fields = list(getattr(m, "FIELDS", ()) or ())
            for sub in getattr(m, "SUBS", ()) or ():
                fields.extend(sub.fields)
            for f in fields:
                label = str(getattr(f, "label", "") or "").strip().rstrip(":").strip()
                key = str(getattr(f, "key", "") or "")
                if label and key and "*" not in key:
                    out.setdefault(key.upper(), label)
    except Exception:  # noqa: BLE001  # swallow-ok: the raw keys then
        return {}
    return out


try:
    from ..colour_picker import ColourButton as panel_ColourButton
except Exception:  # noqa: BLE001  # swallow-ok: the panel still builds without swatches
    panel_ColourButton = QtWidgets.QToolButton


class PanelSwatch(panel_ColourButton):
    """A ColourButton whose click the panel handles (each row offers its own
    picker options: flashing, Transparent)."""

    def choose(self) -> None:  # noqa: D102 -- the panel connects clicked itself
        return


class ScanTimeSpin(NumberField):
    """The page's scan time: -1 shows 'Default' (no SCANTIME); stepping up
    from it starts at the project's default scan time, not at 49 ms."""

    def __init__(self, default: str = "250", parent=None):
        super().__init__(parent, lo=-1, hi=60000)
        try:
            self._default = int(default)
        except TypeError, ValueError:
            self._default = 250

    def stepBy(self, steps: int) -> None:  # noqa: N802 -- Qt naming
        if self.value() == self.minimum() and steps > 0:
            self.setValue(max(0, min(self.maximum(), self._default)))
            return
        super().stepBy(steps)


class MixedField(NumberField):
    """A number field for several objects; shows 'Mixed' when they differ."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._mixed = False
        self._was_mixed = False
        self._suffix = None  # the unit, kept aside while Mixed shows
        self._base = 0  # the value shown under Mixed (the first object's)
        # how the value was changed since Mixed showed: None, 'typed' (a number), 'sum' (a
        # sum with a leading operator, in _sum) or 'delta' (steps, the wheel, a label drag)
        self._how: Optional[str] = None
        self._sum = ""
        self._op = None  # what the last commit made of it (relative_op)
        self.lineEdit().setPlaceholderText("Mixed")
        self.lineEdit().textEdited.connect(self._typed)

    def set_mixed(self, on: bool, value: Optional[int] = None) -> None:
        """Show ``value`` (the first object's) or, ``on``, Mixed."""
        if value is not None:
            self._unit_back()
            self.load(int(value))
        self._mixed = self._was_mixed = bool(on)
        self._base = self.value()
        self._how, self._sum, self._op = None, "", None
        self._show_mixed()

    def is_mixed(self) -> bool:
        return self._mixed

    def relative_op(self):
        """For the commit just made: ``fn(own value) -> new value`` when the change applies
        to each object's own value (the objects did not share it, and a sum with a leading
        operator, a step or a drag changed it); None when the field's value is for every
        object."""
        return self._op

    def _make_op(self):
        lo = self.minimum() + (1 if self.specialValueText() else 0)
        hi = self.maximum()
        if self._how == "sum" and self._sum:
            text, neg = self._sum, self._negative_ok()

            def op(old):
                v = evaluate(text, old, neg)
                return old if v is None else max(lo, min(hi, round_half_away(v)))

            return op
        if self._how == "delta":
            d = self.value() - self._base
            return lambda old: max(lo, min(hi, int(old) + d))
        return None

    def _typed(self, _text: str) -> None:
        self._mixed = False
        if self._was_mixed:
            self._how, self._sum = "typed", ""

    def _note_sum(self, text: str) -> None:
        body = self._body(text)
        if self._was_mixed and self._how in (None, "typed", "sum") and is_relative(body, self._negative_ok()):
            self._how, self._sum = "sum", body

    def fixup(self, text: str) -> str:  # noqa: D102 -- Qt API
        self._note_sum(text)
        return super().fixup(text)

    def valueFromText(self, text: str) -> int:  # noqa: N802 -- Qt API
        self._note_sum(text)
        return super().valueFromText(text)

    def note_drag(self) -> None:  # noqa: D102 -- NumberField API
        self._mixed = False
        self._unit_back()
        if self._was_mixed and self._how in (None, "delta"):
            self._how = "delta"

    def _show_mixed(self) -> None:
        if not self._mixed:
            return
        with contextlib.suppress(RuntimeError):
            if self._suffix is None:
                # (a spin box keeps its suffix in the text: out of it while Mixed shows)
                self._suffix = self.suffix()
                QtWidgets.QSpinBox.setSuffix(self, "")
            self.lineEdit().clear()

    def _unit_back(self) -> None:
        if self._suffix is not None:
            unit, self._suffix = self._suffix, None
            QtWidgets.QSpinBox.setSuffix(self, unit)

    def is_modified(self) -> bool:  # noqa: D102 -- NumberField API
        if self._mixed:
            return False
        return super().is_modified()

    def revert(self) -> bool:  # noqa: D102 -- NumberField API
        if self._was_mixed and not self._mixed:
            self._stop_steps()
            self._mixed = True
            self._show_mixed()
            return True
        return super().revert()

    def stepBy(self, steps: int) -> None:  # noqa: N802 -- Qt API
        self._mixed = False
        self._unit_back()
        if self._was_mixed and self._how in (None, "delta"):
            self._how = "delta"
        super().stepBy(steps)

    def commit(self) -> None:  # noqa: D102 -- NumberField API
        if self._mixed:
            self._stop_steps()
            self._show_mixed()
            return
        self._op = None
        if self._was_mixed:
            # (the first object's value typed again is still a value for all of them; a
            # sum, a step or a drag changes each one's own)
            self._op = self._make_op()
            self._was_mixed = False
            self._orig = None
        self._unit_back()
        super().commit()

    def focusOutEvent(self, e) -> None:  # noqa: N802 -- Qt API
        super().focusOutEvent(e)
        if self._mixed:
            QtCore.QTimer.singleShot(0, self, self._show_mixed)

    def showEvent(self, e) -> None:  # noqa: N802 -- Qt API
        # (a spin box writes its value's text again when it is shown)
        super().showEvent(e)
        self._show_mixed()


class AddAnimationCombo(QtWidgets.QComboBox):
    """'Add animation...' drop-down."""

    TEXT = "Add animation…"

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setObjectName("addAnimation")
        self.setAccessibleName("Add animation")
        self.setPlaceholderText(self.TEXT)
        self._entries = entries

    def fill(self) -> None:
        """Add the items now (once)."""
        if self._entries is None:
            return
        entries, self._entries = self._entries, None
        blocked = self.blockSignals(True)
        try:
            self.addItem(self.TEXT)
            for label, key, default in entries():
                self.addItem(label, (key, default))
            self.setCurrentIndex(0)
        finally:
            self.blockSignals(blocked)

    def showPopup(self) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().showPopup()

    def focusInEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().focusInEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().keyPressEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().wheelEvent(ev)


class AreaField(QtWidgets.QComboBox):
    """The page's area: '<All areas>' and the areas 1 to 255 to choose from, typed or chosen.
    The 255 areas are listed when the list first opens or the field takes the focus (as
    PageField): the list of a field being shown lays out every item."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filled = False

    def fill(self) -> None:
        """List the areas now (once; the text typed or set stays)."""
        if self._filled:
            return
        self._filled = True
        text = self.lineEdit().text() if self.lineEdit() is not None else None
        blocked = self.blockSignals(True)
        try:
            self.addItems([str(n) for n in range(1, 256)])
            if text is not None and self.lineEdit().text() != text:
                self.setEditText(text)
        finally:
            self.blockSignals(blocked)

    def showPopup(self) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().showPopup()

    def focusInEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()  # (the completer completes from the listed areas)
        super().focusInEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        self.fill()
        super().wheelEvent(ev)


class ValueSwatch(QtWidgets.QWidget):
    """A swatch with its value beside it in dim text ('#3A7BD5', the palette's name,
    'Flashing', 'Transparent'): a click on the value opens the colour picker with the
    colour entry ready (the panel's handlers ask :meth:`PropertyPanel._take_entry_focus`)."""

    def __init__(self, swatch: PanelSwatch, panel: "PropertyPanel"):
        super().__init__()
        self.swatch = swatch
        self._panel = weakref.ref(panel)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(_sp(1))
        h.addWidget(swatch)
        self.value_text = QtWidgets.QToolButton()
        self.value_text.setObjectName("swatchValue")
        self.value_text.setAutoRaise(True)
        self.value_text.setCursor(QtCore.Qt.IBeamCursor)
        self.value_text.setToolTip("Type a colour: #RRGGBB, r,g,b or a palette name")
        self.value_text.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.value_text.clicked.connect(self._value_clicked)
        h.addWidget(self.value_text)
        h.addStretch(1)
        self.clicked = swatch.clicked

    def _value_clicked(self) -> None:
        panel = self._panel()
        if panel is not None:
            panel._entry_focus = True
        self.swatch.click()

    def pair(self):
        return self.swatch.pair()


class LabelledSwatch(QtWidgets.QWidget):
    """A swatch with a label beside it (the ON colour's two phases)."""

    def __init__(self, swatch: PanelSwatch, text: str):
        super().__init__()
        self.swatch = swatch
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(_sp(1))
        self.label = QtWidgets.QLabel(text)
        h.addWidget(self.label)
        h.addWidget(swatch)
        self.clicked = swatch.clicked

    def text(self) -> str:
        return self.label.text()


def styles_strip_tip() -> str:
    """The tool tip of the Appearance section's style tiles (the pointing sentence only
    while Settings > Editing > Styles > Preview a style on the page when you point at it is
    on: panel_options.STYLE_PREVIEW)."""
    from . import panel_options as _panels

    if _panels.STYLE_PREVIEW:
        return "Click a style to apply it. Point at one to see it on the page"
    return "Click a style to apply it"


# the process caches of this module (infra/caches.py: when each is emptied)
_caches.register("properties.sheet_labels", sheet_labels, on=())
_caches.register("properties.icons", ICON_CACHE, on=("tests",))
