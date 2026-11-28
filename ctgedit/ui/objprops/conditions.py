"""The tables of a multi-state property (Fill colour, Display value) and its Conditions group:
conditions A to E, condition A being the property's expression."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

#: the names of the conditions, in order
LETTERS = "ABCDE"


def table_item(text: str, editable: bool = False) -> QtWidgets.QTableWidgetItem:
    """A cell; ``editable``: typed in place."""
    it = QtWidgets.QTableWidgetItem(text)
    flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
    if editable:
        flags |= QtCore.Qt.ItemIsEditable
    it.setFlags(flags)
    return it


def resizable(t: QtWidgets.QTableWidget, name: str) -> None:
    """Resizable, remembered columns (ui/tables.py)."""
    from ..tables import make_resizable

    make_resizable(t, "ObjectPropertiesDialog/" + name)


def property_table(headers, editable: bool = False, icon_size=None) -> QtWidgets.QTableWidget:
    """A table of the sheet: one row selected at a time; ``editable``: its cells are typed in
    place; ``icon_size``: the size of the colour swatches it shows."""
    t = QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    if editable:
        t.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        t.setColumnWidth(0, 110)
    else:
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    if icon_size is not None:
        t.setIconSize(QtCore.QSize(*icon_size))
    t.verticalHeader().setDefaultSectionSize(t.fontMetrics().height() + 8)
    return t


class ConditionsEditing:
    """The Conditions group of a property editor. The editor gives :meth:`_conditions` (the
    list edited in place), ``_MAX_CONDITIONS``, ``_expr_field(limit)``, ``_show_text``,
    ``_fill_states``, ``_syncing`` and ``_read_only``; it is told through
    :meth:`_condition_a_changed`, :meth:`_condition_a_removed` and :meth:`_conditions_touched`."""

    _MAX_CONDITIONS = len(LETTERS)

    def _conditions(self) -> list:
        raise NotImplementedError

    def _condition_a_changed(self, text: str) -> None:
        """Condition A (the property's expression) was typed in the group."""

    def _condition_a_removed(self, text: str) -> None:
        """Condition A was removed: ``text`` is the new condition A."""

    def _conditions_touched(self) -> None:
        """A condition was typed, added or removed."""

    def _conditions_group(self, name: str, limit: int) -> QtWidgets.QGroupBox:
        """The group: the table of conditions, Add and Remove, and the selected condition's
        field. ``name``: the table's remembered columns; ``limit``: a condition's length."""
        box = QtWidgets.QGroupBox("Conditions")
        grid = QtWidgets.QGridLayout(box)
        self._conds = property_table(["", "Condition (TRUE / FALSE)"])
        self._conds.setColumnWidth(0, 28)
        resizable(self._conds, name)
        # (room for the five conditions CtDraw allows)
        self._conds.setFixedHeight(
            self._conds.horizontalHeader().sizeHint().height()
            + self._MAX_CONDITIONS * self._conds.verticalHeader().defaultSectionSize()
            + 4
        )
        self._conds.currentCellChanged.connect(lambda *_a: self._show_condition())
        grid.addWidget(self._conds, 0, 0, 3, 1)
        self._btn_add = QtWidgets.QPushButton("Add")
        self._btn_add.setToolTip("Add a condition (at most %d)" % self._MAX_CONDITIONS)
        self._btn_add.clicked.connect(self._add_condition)
        self._btn_del = QtWidgets.QPushButton("Remove")
        self._btn_del.setToolTip("Remove the selected condition")
        self._btn_del.clicked.connect(self._delete_condition)
        grid.addWidget(self._btn_add, 0, 1)
        grid.addWidget(self._btn_del, 1, 1)
        self._cond_label = QtWidgets.QLabel("Condition A:")
        self._cond_edit = self._expr_field(limit)
        self._cond_edit.setToolTip("The selected condition: a TRUE / FALSE expression")
        self._cond_edit.valueChanged.connect(self._on_condition_edit)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._cond_label)
        row.addWidget(self._cond_edit, 1)
        grid.addLayout(row, 3, 0, 1, 2)
        return box

    def _fill_conditions(self, select: int = 0) -> None:
        conds = self._conditions()
        self._syncing = True
        try:
            self._conds.setRowCount(len(conds))
            for r, c in enumerate(conds):
                self._conds.setItem(r, 0, table_item(LETTERS[r]))
                self._conds.setItem(r, 1, table_item(c))
        finally:
            self._syncing = False
        self._conds.setCurrentCell(max(0, min(select, len(conds) - 1)), 1)
        self._show_condition()
        ro = self._read_only
        self._btn_add.setEnabled(not ro and len(conds) < self._MAX_CONDITIONS)
        self._btn_del.setEnabled(not ro and len(conds) > 1)

    def _show_condition(self) -> None:
        conds = self._conditions()
        r = self._conds.currentRow()
        if not (0 <= r < len(conds)):
            r = 0
        self._cond_label.setText("Condition %s:" % LETTERS[r])
        was = self._syncing
        self._syncing = True
        try:
            self._show_text(self._cond_edit, conds[r] if conds else "")
        finally:
            self._syncing = was

    def _on_condition_edit(self, text: str) -> None:
        if self._syncing:
            return
        conds = self._conditions()
        r = max(0, self._conds.currentRow())
        if r >= len(conds):
            return
        self._conditions_touched()
        conds[r] = text
        if r == 0:
            self._condition_a_changed(text)
            return
        self._syncing = True
        try:
            it = self._conds.item(r, 1)
            if it is not None:
                it.setText(text)
        finally:
            self._syncing = False

    def _add_condition(self) -> None:
        conds = self._conditions()
        if len(conds) >= self._MAX_CONDITIONS or self._read_only:
            return
        self._conditions_touched()
        conds.append("")
        self._fill_conditions(len(conds) - 1)
        self._fill_states()
        self._cond_edit.setFocus()

    def _delete_condition(self) -> None:
        conds = self._conditions()
        if len(conds) <= 1 or self._read_only:
            return
        r = self._conds.currentRow()
        if not (0 <= r < len(conds)):
            r = len(conds) - 1
        del conds[r]
        self._conditions_touched()
        if r == 0:
            self._condition_a_removed(conds[0])
        self._fill_conditions(min(r, len(conds) - 1))
        self._fill_states()
