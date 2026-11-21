"""Arrange > Align and Arrange > Adjust Position and Size."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

#: the largest coordinate CtDraw's Adjust Position and Size accepts
#: (-32000 <= X, Y <= 32000, X + Width <= 32000)
COORD_LIMIT = 32000


class AlignDialog(QtWidgets.QDialog):
    """Arrange > Align."""

    changed = QtCore.Signal(str, str)
    #: Apply / OK: align the selection now
    applyRequested = QtCore.Signal()
    #: the Align to box changed: 'selection', 'last' or 'page' (Settings > Editing keeps it)
    referenceChosen = QtCore.Signal(str)
    #: Settings > Editing 'Align to' as the tips name it
    REFERENCES = {"selection": "the selection", "last": "the object selected last", "page": "the page"}

    VERTICAL = (
        ("top", "Top", "Every object's top edge on the top of the selection"),
        ("vcentre", "Centre", "Every object's centre on the vertical centre of the selection"),
        ("bottom", "Bottom", "Every object's bottom edge on the bottom of the selection"),
        (
            "even",
            "Even (centres)",
            "Equal vertical steps between the objects' centres; the top and bottom objects stay. "
            "Arrange > Space Equally makes the gaps between edges equal",
        ),
        ("", "None", "Leave the vertical positions as they are"),
    )
    HORIZONTAL = (
        ("left", "Left", "Every object's left edge on the left of the selection"),
        ("hcentre", "Centre", "Every object's centre on the horizontal centre of the selection"),
        ("right", "Right", "Every object's right edge on the right of the selection"),
        (
            "even",
            "Even (centres)",
            "Equal horizontal steps between the objects' centres; the leftmost and rightmost objects "
            "stay. Arrange > Space Equally makes the gaps between edges equal",
        ),
        ("", "None", "Leave the horizontal positions as they are"),
    )

    def __init__(self, parent=None, units: int = 2, units_of=None):
        super().__init__(parent)
        self.setWindowTitle("Align")
        self._units = int(units)
        self._units_of = units_of
        self._reference = "selection"
        lay = QtWidgets.QVBoxLayout(self)
        # what Left, Centre and the others align to, in the dialog
        ref = QtWidgets.QHBoxLayout()
        ref_label = QtWidgets.QLabel("&Align to:", self)
        self.align_to = QtWidgets.QComboBox(self)
        for key, text in (
            ("selection", "The selection"),
            ("last", "The object selected last"),
            ("page", "The page"),
        ):
            self.align_to.addItem(text, key)
        self.align_to.setToolTip("What the edges and centres line up with (Settings > Editing keeps it)")
        ref_label.setBuddy(self.align_to)
        ref.addWidget(ref_label)
        ref.addWidget(self.align_to, 1)
        lay.addLayout(ref)
        self.align_to.currentIndexChanged.connect(self._reference_chosen)
        row = QtWidgets.QHBoxLayout()
        self._groups: dict[str, QtWidgets.QButtonGroup] = {}
        self._evens: list = []
        for key, title, options in (("v", "Vertical", self.VERTICAL), ("h", "Horizontal", self.HORIZONTAL)):
            box = QtWidgets.QGroupBox(title, self)
            bl = QtWidgets.QVBoxLayout(box)
            group = QtWidgets.QButtonGroup(self)
            for mode, label, tip in options:
                rb = QtWidgets.QRadioButton(label, box)
                rb.setProperty("mode", mode)
                rb.setToolTip(tip)
                rb.setProperty("tip", tip)
                rb.setProperty("base_tip", tip)
                if mode == "even":
                    self._evens.append(rb)
                rb.setChecked(mode == "")
                group.addButton(rb)
                bl.addWidget(rb)
            group.buttonToggled.connect(self._toggled)
            self._groups[key] = group
            row.addWidget(box)
        lay.addLayout(row)
        from ..style import theme

        self._note = theme.note_label("", "warn", self)
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        lay.addStretch(1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply,
            self,
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(self._buttons)
        self._update_state()

    # -- the selection ---------------------------------------------------------------
    def set_selection(self, doc, indices) -> None:
        """The selection changed (or another page is shown)."""
        n = (
            self._units_of(doc, indices)
            if self._units_of is not None
            else (len({doc.root_of(i) for i in indices}) if doc is not None else 0)
        )
        self.set_units(n)

    def set_units(self, n: int) -> None:
        """How many objects the next Apply aligns."""
        self._units = int(n)
        self._update_state()

    def units(self) -> int:
        return self._units

    def set_reference(self, reference: str) -> None:
        """Settings > Editing 'Align to': 'selection' (the selection box, the Graphics
        Builder's), 'last' (the object selected last) or 'page'. The tips name it."""
        if reference not in self.REFERENCES:
            reference = "selection"
        self._reference = reference
        k = self.align_to.findData(reference)
        if k >= 0 and k != self.align_to.currentIndex():
            self.align_to.blockSignals(True)
            self.align_to.setCurrentIndex(k)
            self.align_to.blockSignals(False)
        what = self.REFERENCES[reference]
        for group in self._groups.values():
            for b in group.buttons():
                base = str(b.property("base_tip") or "")
                if (b.property("mode") or "") not in ("", "even"):
                    b.setProperty("tip", base.replace("the selection", what))
                    b.setToolTip(b.property("tip"))
        self._update_state()

    def reference(self) -> str:
        return self._reference

    def _reference_chosen(self, _i=None) -> None:
        key = self.align_to.currentData()
        if key and key != self._reference:
            self.set_reference(key)
            self.referenceChosen.emit(key)

    def problem(self) -> str:
        """Why Apply cannot be used now ('' = it can)."""
        if self._reference == "page" and self._units < 1:
            return "Select the objects to align."
        if self._reference != "page" and self._units < 2:
            return "Select two or more objects to align."
        return ""

    def _update_state(self) -> None:
        three = self._units >= 3
        for rb in self._evens:
            if not three and rb.isChecked():
                # (the None of its group takes over)
                for b in rb.group().buttons():
                    if (b.property("mode") or "") == "":
                        b.setChecked(True)
            rb.setEnabled(three)
            rb.setToolTip(
                rb.property("tip")
                if three
                else "Even needs three or more objects: the outer two stay where they are"
            )
        why = self.problem()
        chosen = bool(self.vertical() or self.horizontal())
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).setEnabled(not why and chosen)
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(not why or not chosen)
        self._note.setText(why)
        self._note.setVisible(bool(why))

    def _apply(self) -> None:
        if self.problem() or not (self.vertical() or self.horizontal()):
            return
        self.applyRequested.emit()

    def _ok(self) -> None:
        if not self.problem() and (self.vertical() or self.horizontal()):
            self.applyRequested.emit()
        self.accept()

    def _mode(self, key: str) -> str:
        b = self._groups[key].checkedButton()
        return str(b.property("mode") or "") if b is not None else ""

    def vertical(self) -> str:
        return self._mode("v")

    def horizontal(self) -> str:
        return self._mode("h")

    def set_choice(self, vertical: str | None = None, horizontal: str | None = None) -> None:
        """Check the given modes (None = leave that group); each change signals
        ``changed`` like a click."""
        for key, mode in (("v", vertical), ("h", horizontal)):
            if mode is None:
                continue
            for b in self._groups[key].buttons():
                if (b.property("mode") or "") == mode and b.isEnabled():
                    b.setChecked(True)

    def _toggled(self, button, checked: bool) -> None:
        if checked:
            self._update_state()
            self.changed.emit(self.vertical(), self.horizontal())


class AdjustPositionDialog(QtWidgets.QDialog):
    """Arrange > Adjust Position and Size (modeless)."""

    applyRequested = QtCore.Signal()

    def __init__(self, rect=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust Position and Size")
        self._box = None
        self._loading = False
        self._dirty: set[str] = set()
        self._extra_note = ""
        lay = QtWidgets.QVBoxLayout(self)

        pos = QtWidgets.QGroupBox("Position", self)
        pf = QtWidgets.QFormLayout(pos)
        self._sp: dict[str, QtWidgets.QSpinBox] = {}
        for label, key in (("X:", "x"), ("Y:", "y")):
            sp = QtWidgets.QSpinBox(pos)
            sp.setRange(-COORD_LIMIT, COORD_LIMIT)
            sp.setSuffix(" px")
            sp.setToolTip("The left edge (X) or top edge (Y) of the selection")
            self._sp[key] = sp
            pf.addRow(label, sp)
        lay.addWidget(pos)

        size = QtWidgets.QGroupBox("Size", self)
        sg = QtWidgets.QGridLayout(size)
        self._by_pct = QtWidgets.QRadioButton("By percentage", size)
        self._by_abs = QtWidgets.QRadioButton("By absolute size", size)
        self._by_pct.setToolTip("Scale the selection about its top-left corner; 100 keeps the size")
        self._by_abs.setToolTip(
            "Give the selection a new width and height in pixels; it scales about its top-left corner"
        )
        pct = QtWidgets.QSpinBox(size)
        pct.setRange(1, 32767)
        pct.setSuffix(" %")
        self._sp["pct"] = pct
        sg.addWidget(self._by_pct, 0, 0)
        sg.addWidget(pct, 0, 1)
        sg.addWidget(self._by_abs, 1, 0, 1, 2)
        for row, (label, key) in enumerate((("Width:", "w"), ("Height:", "h")), start=2):
            sp = QtWidgets.QSpinBox(size)
            sp.setRange(0, COORD_LIMIT)
            sp.setSuffix(" px")
            self._sp[key] = sp
            lbl = QtWidgets.QLabel(label, size)
            lbl.setBuddy(sp)
            setattr(self, "_lbl_" + key, lbl)
            sg.addWidget(lbl, row, 0)
            sg.addWidget(sp, row, 1)
        self._by_pct.setChecked(True)  # CtDraw opens By percentage
        lay.addWidget(size)

        from ..style import theme

        self._note = theme.note_label("", "warn", self)
        lay.addWidget(self._note)

        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply,
            self,
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(self._buttons)

        for key, sp in self._sp.items():
            sp.valueChanged.connect(lambda _v, k=key: self._edited(k))
        self._by_pct.toggled.connect(lambda _c: self._update_state())
        self.set_box(rect)

    # -- the selection -------------------------------------------------------
    def set_box(self, rect, note: str = "") -> None:
        """Show the selection box (x, y, width, height) -- or None: nothing to
        adjust. The fields start from the box (percentage 100) and nothing
        counts as changed; the size mode stays as chosen. ``note`` = a line
        under the fields (e.g. locked objects left out)."""
        self._box = tuple(int(v) for v in rect) if rect is not None else None
        self._extra_note = note
        self._loading = True
        try:
            x, y, w, h = self._box if self._box is not None else (0, 0, 0, 0)
            # a box past CtDraw's limits is SHOWN as it is (problem() says
            # why OK is greyed) -- a spin box clamping it would move or
            # resize the selection on the next Apply
            for key, v in (("x", x), ("y", y)):
                self._sp[key].setRange(min(-COORD_LIMIT, v), max(COORD_LIMIT, v))
            for key, v in (("w", w), ("h", h)):
                self._sp[key].setRange(0, max(COORD_LIMIT, v))
            for key, v in (("x", x), ("y", y), ("pct", 100), ("w", w), ("h", h)):
                self._sp[key].setValue(v)
        finally:
            self._loading = False
        self._dirty.clear()
        self._update_state()

    def box(self):
        return self._box

    # -- the values ------------------------------------------------------------
    def by_percentage(self) -> bool:
        return self._by_pct.isChecked()

    def set_by_percentage(self, on: bool) -> None:
        (self._by_pct if on else self._by_abs).setChecked(True)

    def position(self) -> tuple:
        return (self._sp["x"].value(), self._sp["y"].value())

    def scale(self) -> tuple:
        """(sx, sy) as CtDraw's apply computes them: the
        percentage / 100 on both axes, or the new size over the old (+1e-10,
        so the truncated right / bottom edge lands on the typed size); 1 on
        an axis the selection has no size on or that did not change."""
        if self._box is None:
            return (1.0, 1.0)
        w, h = self._box[2], self._box[3]
        if self.by_percentage():
            s = self._sp["pct"].value() / 100.0
            return (s if w else 1.0, s if h else 1.0)
        nw, nh = self._sp["w"].value(), self._sp["h"].value()
        sx = (nw + 1e-10) / w if w and nw != w else 1.0
        sy = (nh + 1e-10) / h if h and nh != h else 1.0
        return (sx, sy)

    def rect(self) -> tuple:
        """(x, y, width, height) the selection will have."""
        x, y = self.position()
        if self._box is None:
            return (x, y, self._sp["w"].value(), self._sp["h"].value())
        sx, sy = self.scale()
        return (x, y, int(self._box[2] * sx), int(self._box[3] * sy))

    def problem(self) -> str:
        """Why OK / Apply cannot be used now ('' = they can)."""
        if self._box is None:
            return "Select the objects to move or resize."
        x, y = self.position()
        for name, v in (("X", x), ("Y", y)):
            if not -COORD_LIMIT <= v <= COORD_LIMIT:
                return "%s must be between %d and %d, the range of page coordinates." % (
                    name,
                    -COORD_LIMIT,
                    COORD_LIMIT,
                )
        w, h = self._box[2], self._box[3]
        if self.by_percentage():
            p = self._sp["pct"].value()
            if w * p / 100.0 + x > COORD_LIMIT or h * p / 100.0 + y > COORD_LIMIT:
                return (
                    "At %d%% the selection would reach past %d, the largest "
                    "position on a page. Use a smaller percentage or move it "
                    "first." % (p, COORD_LIMIT)
                )
            return ""
        for key, size, at, edge in (("w", w, x, "right"), ("h", h, y, "bottom")):
            if not size:
                continue
            v = self._sp[key].value()
            if not 1 <= v <= COORD_LIMIT:
                return "The %s must be between 1 and %d pixels." % (
                    "width" if key == "w" else "height",
                    COORD_LIMIT,
                )
            if at + v > COORD_LIMIT:
                return (
                    "The %s edge would be at %d, past %d, the largest position "
                    "on a page. Reduce %s or %s."
                    % (
                        edge,
                        at + v,
                        COORD_LIMIT,
                        "X" if key == "w" else "Y",
                        "Width" if key == "w" else "Height",
                    )
                )
        return ""

    def changed(self) -> bool:
        """A value was edited since the box was shown (CtDraw's Apply rule:
        the position, or the size field of the current mode)."""
        if self._dirty & {"x", "y"}:
            return True
        if self.by_percentage():
            return "pct" in self._dirty
        return bool(self._dirty & {"w", "h"})

    # -- state -----------------------------------------------------------------
    def _edited(self, key: str) -> None:
        if not self._loading:
            self._dirty.add(key)
        self._update_state()

    def _update_state(self) -> None:
        have = self._box is not None
        pct_mode = self.by_percentage()
        w, h = (self._box[2], self._box[3]) if have else (0, 0)
        for key in ("x", "y"):
            self._sp[key].setEnabled(have)
        self._by_pct.setEnabled(have)
        self._by_abs.setEnabled(have)
        self._sp["pct"].setEnabled(have and pct_mode)
        for key, size in (("w", w), ("h", h)):
            on = have and not pct_mode and size != 0
            self._sp[key].setEnabled(on)
            getattr(self, "_lbl_" + key).setEnabled(on)
            if have and size == 0:
                self._sp[key].setToolTip(
                    "The selection has no %s, so it cannot be "
                    "scaled that way" % ("width" if key == "w" else "height")
                )
            else:
                self._sp[key].setToolTip("")
        why = self.problem()
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(not why)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).setEnabled(not why and self.changed())
        text = why if have else "Select the objects to move or resize."
        if self._extra_note:
            text = (text + "\n" if text else "") + self._extra_note
        self._note.setText(text)
        self._note.setVisible(bool(text))

    def _apply(self) -> None:
        if self.problem():
            return
        self.applyRequested.emit()

    def _ok(self) -> None:
        if self.problem():
            return
        if self.changed():
            self.applyRequested.emit()
        self.accept()


class _SelectionDialog(QtWidgets.QDialog):
    """A modeless Arrange window that follows the selection (UI guide section 10):
    ``set_selection(doc, indices)`` counts the units that can move, Apply and OK signal
    ``applyRequested`` (one undo step each), a note says why Apply is off."""

    applyRequested = QtCore.Signal()
    MIN_UNITS = 2

    def __init__(self, parent=None, units: int = 2, units_of=None):
        super().__init__(parent)
        self._units = int(units)
        self._units_of = units_of
        self._lay = QtWidgets.QVBoxLayout(self)
        self._form = QtWidgets.QFormLayout()
        self._lay.addLayout(self._form)
        from ..style import theme

        self._note = theme.note_label("", "warn", self)
        self._note.setWordWrap(True)
        self._lay.addWidget(self._note)
        self._lay.addStretch(1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply,
            self,
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        self._lay.addWidget(self._buttons)

    def set_selection(self, doc, indices) -> None:
        n = (
            self._units_of(doc, indices)
            if self._units_of is not None
            else (len({doc.root_of(i) for i in indices}) if doc is not None else 0)
        )
        self.set_units(n)

    def set_units(self, n: int) -> None:
        self._units = int(n)
        self._update_state()

    def units(self) -> int:
        return self._units

    def problem(self) -> str:
        if self._units < self.MIN_UNITS:
            return "Select %s or more objects." % ("two" if self.MIN_UNITS == 2 else "three")
        return ""

    def _update_state(self) -> None:
        why = self.problem()
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).setEnabled(not why)
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(not why)
        self._note.setText(why)
        self._note.setVisible(bool(why))

    def _apply(self) -> None:
        if not self.problem():
            self.applyRequested.emit()

    def _ok(self) -> None:
        if self.problem():
            return
        self.applyRequested.emit()
        self.accept()


class SpaceEquallyDialog(_SelectionDialog):
    """Arrange > Space Equally…: equal gaps between the objects' EDGES (Align > Even spaces
    their centres). Direction Horizontal / Vertical; the gap either shares the room
    between the first and the last object (both stay; three or more objects) or is a
    number of pixels (the first object stays)."""

    def __init__(self, parent=None, units: int = 2, units_of=None):
        super().__init__(parent, units, units_of)
        self.setWindowTitle("Space Equally")
        from ..style import theme

        what = theme.note_label(
            "Equal gaps between the objects' edges. Align's Even (centres) spaces their centres instead.",
            "text_dim",
            self,
        )
        what.setWordWrap(True)
        self._form.addRow(what)
        self.direction = QtWidgets.QComboBox(self)
        self.direction.addItems(["Horizontal", "Vertical"])
        self._form.addRow("Direction:", self.direction)
        self.share = QtWidgets.QRadioButton("Share the room between the first and last object", self)
        self.share.setToolTip(
            "The outer two objects stay; the ones between are spread so that every gap is the same"
        )
        self.fixed = QtWidgets.QRadioButton("Gap of", self)
        self.gap = QtWidgets.QSpinBox(self)
        self.gap.setRange(0, COORD_LIMIT)
        self.gap.setSuffix(" px")
        self.gap.setToolTip("Every gap this wide; the first object stays")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.fixed)
        row.addWidget(self.gap)
        row.addStretch(1)
        self._form.addRow(self.share)
        self._form.addRow(row)
        self.share.setChecked(True)
        self.gap.setEnabled(False)
        self.fixed.toggled.connect(self.gap.setEnabled)
        self.share.toggled.connect(lambda _c: self._update_state())
        self._update_state()

    def axis(self) -> str:
        return "h" if self.direction.currentIndex() == 0 else "v"

    def gap_value(self):
        """None = share the room; else the gap in pixels."""
        return None if self.share.isChecked() else int(self.gap.value())

    def problem(self) -> str:
        if self._units < 2:
            return "Select two or more objects."
        if self.share.isChecked() and self._units < 3:
            return "Sharing the room needs three or more objects: choose a gap for two."
        return ""


class ArrangeGridDialog(_SelectionDialog):
    """Arrange > Arrange in Grid…: the selected objects in reading order laid out in a
    grid of N columns from the top-left of the selection, with a horizontal and a
    vertical gap; By rows fills a row before the next, By columns a column."""

    def __init__(self, parent=None, units: int = 2, units_of=None):
        super().__init__(parent, units, units_of)
        self.setWindowTitle("Arrange in Grid")
        self.columns = QtWidgets.QSpinBox(self)
        self.columns.setRange(1, 999)
        self.columns.setToolTip("How many objects side by side; the rows follow")
        self.hgap = QtWidgets.QSpinBox(self)
        self.hgap.setRange(0, COORD_LIMIT)
        self.hgap.setSuffix(" px")
        self.vgap = QtWidgets.QSpinBox(self)
        self.vgap.setRange(0, COORD_LIMIT)
        self.vgap.setSuffix(" px")
        self.order = QtWidgets.QComboBox(self)
        self.order.addItems(["By rows", "By columns"])
        self.order.setToolTip(
            "By rows fills a row from left to right before the next row; "
            "By columns fills a column from top to bottom first"
        )
        self._form.addRow("Columns:", self.columns)
        self._form.addRow("Horizontal gap:", self.hgap)
        self._form.addRow("Vertical gap:", self.vgap)
        self._form.addRow("Fill:", self.order)
        self._update_state()

    def values(self) -> dict:
        return {
            "columns": int(self.columns.value()),
            "hgap": int(self.hgap.value()),
            "vgap": int(self.vgap.value()),
            "by_rows": self.order.currentIndex() == 0,
        }

    def set_values(self, columns=None, hgap=None, vgap=None, by_rows=None) -> None:
        if columns is not None:
            self.columns.setValue(int(columns))
        if hgap is not None:
            self.hgap.setValue(int(hgap))
        if vgap is not None:
            self.vgap.setValue(int(vgap))
        if by_rows is not None:
            self.order.setCurrentIndex(0 if by_rows else 1)
