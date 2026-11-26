"""Find and Replace > Options > Filters."""

from __future__ import annotations

import re
from typing import Dict, List

from PySide6 import QtCore, QtWidgets

from ...features import find_replace as FR
from ..style.theme import sp as _sp

#: (FindOptions field, label, tool tip) of each filter kind
FILTER_KINDS = [
    ("pages", "Pages", "The page's name, such as Mixing_L3"),
    (
        "fields",
        "Fields",
        "The field's key or its name in the Field column: CMD, Command, TXT_ON, or a genie "
        "parameter's name such as Tag",
    ),
    (
        "genies",
        "Genies and symbols",
        "The library and element of the genie or symbol an object belongs to, such as pumps.pump_1",
    ),
    ("objects", "Objects", "The object's name or AN (AN12), or the name of a group or genie it is in"),
]
_LABEL = {k: label for k, label, _t in FILTER_KINDS}
#: the RegexToggle key of the rows' text boxes
_REGEX_KEY = "Find filter"


class FilterRow(QtWidgets.QWidget):
    """One filter: [what] [match / do not match] [text .*] [remove]."""

    changed = QtCore.Signal()
    removeRequested = QtCore.Signal(object)

    def __init__(self, kind: str = "pages", exclude: bool = False, text: str = "", regex: bool = False):
        super().__init__()
        from ..style.theme import RegexToggle, icon

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(2))
        self.kind_box = QtWidgets.QComboBox()
        for k, label, tip in FILTER_KINDS:
            self.kind_box.addItem(label, k)
            self.kind_box.setItemData(self.kind_box.count() - 1, tip, QtCore.Qt.ToolTipRole)
        self.kind_box.setAccessibleName("Filter on")
        self.mode_box = QtWidgets.QComboBox()
        self.mode_box.addItem("match", False)
        self.mode_box.addItem("do not match", True)
        self.mode_box.setAccessibleName("Keep or leave out")
        self.edit = QtWidgets.QLineEdit(text)
        self.edit.setAccessibleName("Filter text")
        self.edit.setPlaceholderText("Part of the name (not case-sensitive)")
        self.edit.setToolTip(
            "Found anywhere in the name. With .* on, a regular expression: ^ and $ anchor it"
        )
        self.regex = RegexToggle(self.edit, _REGEX_KEY)
        self.regex.set_on(regex)
        self.remove = QtWidgets.QToolButton()
        self.remove.setIcon(icon("close"))
        self.remove.setAutoRaise(True)
        self.remove.setToolTip("Remove this filter")
        self.remove.setAccessibleName("Remove filter")
        lay.addWidget(self.kind_box)
        lay.addWidget(self.mode_box)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.remove)
        self.kind_box.setCurrentIndex(max(0, self.kind_box.findData(kind)))
        self.mode_box.setCurrentIndex(1 if exclude else 0)
        self._tip()
        self.kind_box.currentIndexChanged.connect(lambda _i: self._tip())
        self.mode_box.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.edit.textChanged.connect(lambda _t: self.changed.emit())
        self.regex.toggled.connect(lambda _on: self.changed.emit())
        self.remove.clicked.connect(lambda: self.removeRequested.emit(self))

    def _tip(self) -> None:
        """The kind box says what its choice looks at."""
        self.kind_box.setToolTip(dict((k, t) for k, _l, t in FILTER_KINDS).get(self.kind(), ""))
        self.changed.emit()

    def kind(self) -> str:
        return self.kind_box.currentData()

    def exclude(self) -> bool:
        return bool(self.mode_box.currentData())

    def pattern(self) -> str:
        """The row as a regular expression ('' = no condition)."""
        text = self.edit.text().strip()
        if not text:
            return ""
        return text if self.regex.on else re.escape(text)

    def problem(self) -> str:
        if not (self.regex.on and self.edit.text().strip()):
            return ""
        try:
            re.compile(self.edit.text().strip())
        except re.error as e:
            return str(e)
        return ""

    def summary(self) -> str:
        text = self.edit.text().strip()
        if not text:
            return ""
        return "%s %s %s" % (
            _LABEL.get(self.kind(), self.kind()).lower(),
            "not matching" if self.exclude() else "matching",
            text,
        )

    def state(self) -> dict:
        return {
            "kind": self.kind(),
            "exclude": self.exclude(),
            "text": self.edit.text(),
            "regex": self.regex.on,
        }


class FilterList(QtWidgets.QWidget):
    """The filter rows and Add Filter. A name passes when it matches one 'match' row of its
    kind (if any) and no 'do not match' row."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(_sp(1))
        self.rows: List[FilterRow] = []
        foot = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add Filter")
        self.add_button.setAutoDefault(False)
        self.add_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.add_button.setToolTip(
            "Search only some pages, fields, genies or objects, or leave some out, by their names"
        )
        self.add_button.clicked.connect(lambda: self.add().edit.setFocus())
        self.note = QtWidgets.QLabel("None: every page, field and object is searched.")
        from ..style.theme import set_note_role

        set_note_role(self.note)
        foot.addWidget(self.add_button)
        foot.addWidget(self.note, 1)
        foot.addStretch(0)
        self._lay.addLayout(foot)

    def add(
        self, kind: str = "pages", text: str = "", exclude: bool = False, regex: bool = False
    ) -> FilterRow:
        row = FilterRow(kind, exclude, text, regex)
        row.changed.connect(self.changed)
        row.removeRequested.connect(self.remove)
        self._lay.insertWidget(len(self.rows), row)
        self.rows.append(row)
        self._sync()
        self.changed.emit()
        return row

    def remove(self, row: FilterRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
            row.setParent(None)
            row.deleteLater()
            self._sync()
            self.changed.emit()

    def clear(self) -> None:
        for row in list(self.rows):
            self.remove(row)

    def _sync(self) -> None:
        self.note.setVisible(not self.rows)

    def values(self) -> Dict[str, FR.NameFilter]:
        """{FindOptions field: NameFilter} of the rows."""
        inc: Dict[str, List[str]] = {}
        exc: Dict[str, List[str]] = {}
        for row in self.rows:
            pat = row.pattern()
            if pat:
                (exc if row.exclude() else inc).setdefault(row.kind(), []).append("(?:%s)" % pat)
        return {
            k: FR.NameFilter("|".join(inc.get(k, ())), "|".join(exc.get(k, ()))) for k, _l, _t in FILTER_KINDS
        }

    def problem(self) -> str:
        """'<kind> filter is not a regular expression (why)' for the first bad row, else ''."""
        for row in self.rows:
            err = row.problem()
            if err:
                return "The %s filter is not a regular expression (%s)" % (
                    _LABEL.get(row.kind(), row.kind()).lower(),
                    err,
                )
        return ""

    def summary(self) -> List[str]:
        return [s for s in (row.summary() for row in self.rows) if s]

    def state(self) -> list:
        return [row.state() for row in self.rows]

    def set_state(self, rows) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            for r in rows or ():
                if isinstance(r, dict) and r.get("kind") in _LABEL:
                    self.add(
                        r["kind"], str(r.get("text") or ""), bool(r.get("exclude")), bool(r.get("regex"))
                    )
        finally:
            self.blockSignals(False)
        self.changed.emit()
