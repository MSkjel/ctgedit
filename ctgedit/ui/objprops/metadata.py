"""The Metadata page."""

from __future__ import annotations

from typing import List, Tuple

from PySide6 import QtWidgets

from .fields import _Sub
from ..style.theme import set_note_role

METADATA = _Sub("General", [], family=("METADATA_*",))
SUBS = [METADATA]

HELP_TOPIC = "properties#metadata"
_HINT = (
    "A name that matches an association of a super genie this object opens passes "
    "its value to the super genie."
)


class MetadataEditor:
    """The Name | Value list; Apply = EditableDocument.set_metadata."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._initial: List[Tuple[str, str]] = []
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except Exception:  # noqa: BLE001
            pass
        self.reload()

    def _build(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QVBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Value"])
        from ..tables import make_resizable

        make_resizable(self.table, "ObjectPropertiesDialog/metadata")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.table.itemChanged.connect(lambda _it: self._changed())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        left.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("A&dd")
        self.btn_add.setToolTip("Add a metadata row")
        self.btn_add.clicked.connect(self.add_row)
        self.btn_edit = QtWidgets.QPushButton("&Edit")
        self.btn_edit.setToolTip("Edit the selected cell (or double-click it)")
        self.btn_edit.clicked.connect(self.edit_cell)
        self.btn_delete = QtWidgets.QPushButton("&Remove")
        self.btn_delete.setToolTip("Remove the selected rows")
        self.btn_delete.clicked.connect(self.delete_rows)
        for b in (self.btn_add, self.btn_edit, self.btn_delete):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch(1)
        left.addLayout(row)
        self.note = QtWidgets.QLabel(_HINT)
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        left.addWidget(self.note)
        h.addLayout(left, 1)
        return w

    # -- rows --------------------------------------------------------------------------
    def _set_rows(self, rows) -> None:
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for name, value in rows:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
                self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(value))
        finally:
            self.table.blockSignals(False)
        self._update_buttons()

    def rows(self) -> List[Tuple[str, str]]:
        """The rows as the Apply stores them (a row left entirely empty is
        not stored)."""
        out = []
        for r in range(self.table.rowCount()):
            cells = [self.table.item(r, c) for c in (0, 1)]
            name, value = [(it.text() if it is not None else "") for it in cells]
            name = name.strip()
            if name or value:
                out.append((name, value))
        return out

    def add_row(self) -> None:
        """Add: a new row whose name is being typed (Esc leaves it empty; an
        empty row is not stored)."""
        if self._ro:
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c in (0, 1):
            self.table.setItem(r, c, QtWidgets.QTableWidgetItem(""))
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))
        self._update_buttons()

    def delete_rows(self) -> None:
        if self._ro:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        for r in rows:
            self.table.removeRow(r)
        self._changed()

    def edit_cell(self) -> None:
        if self._ro:
            return
        it = self.table.currentItem()
        if it is None and self.table.rowCount():
            it = self.table.item(0, 0)
        if it is not None:
            self.table.editItem(it)

    def _changed(self) -> None:
        self._update_buttons()
        s = self.sheet
        if not s._syncing:
            s._refresh_marks(s._pending_props())

    def _update_buttons(self) -> None:
        has = self.table.rowCount() > 0
        self.btn_add.setEnabled(not self._ro)
        self.btn_delete.setEnabled(not self._ro and has)
        self.btn_edit.setEnabled(not self._ro and has)

    # -- the sheet's editor contract ---------------------------------------------------
    def pending_props(self, props: dict) -> None:
        """``props`` as the rows would leave them (the check mark follows
        what is typed)."""
        rows = self.rows()
        if rows == self._initial:
            return
        for k in [k for k in props if k.upper().startswith("METADATA_")]:
            del props[k]
        for n, (name, value) in enumerate(rows):
            props["METADATA_%d" % n] = "%s=%s" % (name, value)

    def problem(self):
        rows = self.rows()
        if rows == self._initial:
            return None
        from ...editing import metadata_problem

        p = metadata_problem(rows)
        if p is None:
            return None
        row, msg = p
        # the row as the table shows it (empty rows are skipped in ``rows``)
        k = -1
        for r in range(self.table.rowCount()):
            items = [self.table.item(r, c) for c in (0, 1)]
            if any(it is not None and it.text() for it in items):
                k += 1
                if k == row:
                    self.table.setCurrentCell(r, 0)
                    break
        return msg, self.table

    def apply(self) -> bool:
        rows = self.rows()
        if rows == self._initial:
            return False
        s = self.sheet
        try:
            done = s._doc.set_metadata(s._index, rows)
        except ValueError as exc:
            s._fail(str(exc), self.table)
            return False
        if not done and rows != s._doc.metadata(s._index):
            s._fail(
                "The metadata could not be stored: a placed genie keeps no metadata of "
                "its own. Add it in the genie definition.",
                self.table,
            )
            return False
        return bool(done)

    def reload(self) -> None:
        s = self.sheet
        try:
            self._initial = list(s._doc.metadata(s._index))
        except Exception:  # noqa: BLE001
            self._initial = []
        self._set_rows(self._initial)
        why = s.key_blocked("METADATA_0")
        if why and not self._ro:
            self.set_read_only(True)
        if why:
            self.table.setToolTip(why)
            self.note.setText(why)
        else:
            self.note.setText(_HINT + self._association_hint())

    def _association_hint(self) -> str:
        """The associations of the super genies this object's genie opens
        (their names are what a metadata name must match, help 1172182)."""
        s = self.sheet
        try:
            found = s._doc.super_genie_associations(s._index)
        except Exception:  # noqa: BLE001 -- a hint only
            return ""
        parts = []
        for page, names in found:
            parts.append("%s (%s)" % (page, ", ".join(names) if names else "no associations"))
        if not parts:
            return ""
        return " Super genies this genie opens, with their associations: %s." % "; ".join(parts)

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
            if ro
            else QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self._update_buttons()


def build_metadata(sheet, page):
    ed = MetadataEditor(sheet)
    sheet.add_editor(ed)
    sheet.metadata_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"metadata": build_metadata}
