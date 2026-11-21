"""The Inputs dock's Alarms and Equipment tabs."""

from __future__ import annotations
from ..style.theme import sp as _sp

from typing import Any, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import tables
from ..style import theme

#: the states the Alarms tab sets (ctsim/setAlarm's words) and how it names them
ALARM_STATES = (
    ("on", "On"),
    ("on_ack", "Acknowledged"),
    ("off_unack", "Off unacknowledged"),
    ("off", "Off"),
    ("disabled", "Disabled"),
)
#: a listed state (ctsim/alarms) -> (its name, the setAlarm word, the badge colour)
STATE_SHOWN = {
    "unack_on": ("On", "on", "#e0402a"),
    "ack_on": ("Acknowledged", "on_ack", "#e0a030"),
    "unack_off": ("Off unacknowledged", "off_unack", "#d6c341"),
    "off": ("Off", "off", "#6aa36f"),
    "disabled": ("Disabled", "disabled", "#969ca5"),
}
_ROW_ROLE = QtCore.Qt.UserRole + 1
EQUIPMENT_FIELDS = ("MODE", "STATE", "DRMODE")


def _call(fn, *args, regex: bool = False):
    """A source's rows with the search box's '.*' (``regex``); a source that takes no regex
    gets the text alone."""
    if regex:
        try:
            return fn(*args, regex=True)
        except TypeError:
            pass
    return fn(*args)


def _badge(colour: str) -> QtGui.QIcon:
    pm = QtGui.QPixmap(12, 12)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(colour))
    p.setPen(QtGui.QPen(QtGui.QColor("#202020"), 1))
    p.drawEllipse(1, 1, 10, 10)
    p.end()
    return QtGui.QIcon(pm)


_BADGES: Dict[str, QtGui.QIcon] = {}


def badge(state: str) -> QtGui.QIcon:
    shown = STATE_SHOWN.get(state)
    if shown is None:
        return QtGui.QIcon()
    if state not in _BADGES:
        _BADGES[state] = _badge(shown[2])
    return _BADGES[state]


def page_equipment_keys(page_equipment) -> set:
    """The page's equipment and every parent of one, without case (for :func:`_in_page`)."""
    out = set()
    for e in page_equipment:
        parts = str(e).lower().split(".")
        for k in range(1, len(parts) + 1):
            out.add(".".join(parts[:k]))
    return out


def _in_page(name: str, page_equipment) -> bool:
    """``name`` is one of the page's equipment, or a parent of one (``page_equipment``: the
    names, or the set :func:`page_equipment_keys` made of them)."""
    keys = page_equipment if isinstance(page_equipment, set) else page_equipment_keys(page_equipment)
    return name.lower() in keys


class _ListPanel(QtWidgets.QWidget):
    """Search + page-only box + a list + a count line; the subclass fills the rows."""

    COLUMNS: tuple = ()
    KEY = ""
    PLACEHOLDER = ""
    PAGE_ONLY = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._shown_key = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self.search = theme.SearchField(
            self.PLACEHOLDER, tip="A part of the name or equipment; * and ? match the whole name"
        )
        lay.addWidget(self.search)
        self.page_only = QtWidgets.QCheckBox(self.PAGE_ONLY)
        self.page_only.setChecked(True)
        lay.addWidget(self.page_only)
        self.model = QtGui.QStandardItemModel(0, len(self.COLUMNS), self)
        self.model.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.tree = QtWidgets.QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName(self.KEY)
        # (the columns fit the panel)
        tables.make_resizable(self.tree, "SimulateWindow/" + self.KEY.lower(), stretch=0, fit_view=True)
        lay.addWidget(self.tree, 1)
        self.search.attach(self.tree, self)
        self.count = theme.note_label("")
        lay.addWidget(self.count)
        self.search.searchChanged.connect(lambda _t: self.refresh(force=True))
        self.page_only.toggled.connect(lambda _on: self.refresh(force=True))

    def set_source(self, src) -> None:
        if src is self._src:
            return
        self._src = src
        self._shown_key = None
        self.model.removeRows(0, self.model.rowCount())
        self.refresh(force=True)

    def _note(self) -> str:
        fn = getattr(self._src, "alarms_note", None)
        if self._src is None:
            return ""
        if fn is None:
            return "Alarms and equipment come from the Cicode simulator; the demo values have none."
        try:
            return fn()
        except Exception:  # noqa: BLE001 -- a source problem must not break the view
            return ""

    def refresh(self, force: bool = False) -> None:
        if not force and not self.isVisible():
            return
        self._refresh()

    def _refresh(self) -> None:  # pragma: no cover - the subclasses
        raise NotImplementedError

    def selected_row(self) -> Optional[Dict[str, Any]]:
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        it = self.model.item(idx.row(), 0)
        return it.data(_ROW_ROLE) if it is not None else None

    def _select(self, key: str, col: int = 0) -> None:
        for r in range(self.model.rowCount()):
            it = self.model.item(r, 0)
            if it is not None and it.text() == key:
                self.tree.setCurrentIndex(self.model.index(r, col))
                return


class AlarmsPanel(_ListPanel):
    COLUMNS = ("Alarm", "State", "Priority", "Equipment", "Name")
    KEY = "Alarms"
    PLACEHOLDER = "Search alarms"
    PAGE_ONLY = "Only alarms of this page"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Simulated alarms")
        row = QtWidgets.QHBoxLayout()
        state_label = QtWidgets.QLabel("State:")
        row.addWidget(state_label)
        self.state = QtWidgets.QComboBox()
        self.state.setAccessibleName("State")
        state_label.setBuddy(self.state)
        for word, label in ALARM_STATES:
            self.state.addItem(
                badge({"on": "unack_on", "on_ack": "ack_on", "off_unack": "unack_off"}.get(word, word)),
                label,
                word,
            )
        self.state.setToolTip(
            "Set the selected alarm's state on the simulated alarm server "
            "(it holds until one of the alarm's inputs changes)"
        )
        self.state.setEnabled(False)
        row.addWidget(self.state, 1)
        self.layout().addLayout(row)
        self._busy = False
        self.state.activated.connect(self._state_chosen)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.selectionModel().currentChanged.connect(lambda *_a: self._show_selected())

    def _rows(self):
        fn = getattr(self._src, "alarm_rows", None)
        if fn is None:
            return None, 0
        try:
            return _call(fn, self.search.text().strip(), self.page_only.isChecked(), regex=self.search.regex)
        except Exception:  # noqa: BLE001
            return None, 0

    def _refresh(self) -> None:
        rows, total = self._rows()
        note = self._note()
        if rows is None:
            self.model.removeRows(0, self.model.rowCount())
            self._shown_key = None
            self.count.setText(note or ("Waiting for the simulator." if self._src is not None else ""))
            self._show_selected()
            return
        key = tuple((a["tag"], a["state"], str(a.get("priority")), a["equipment"], a["name"]) for a in rows)
        if key == self._shown_key:
            return
        keep = self.selected_row()
        same_rows = self._shown_key is not None and [k[0] for k in key] == [k[0] for k in self._shown_key]
        self._shown_key = key
        if same_rows:
            for r, a in enumerate(rows):
                self._fill(r, a)
        else:
            self.model.removeRows(0, self.model.rowCount())
            for a in rows:
                self.model.appendRow([QtGui.QStandardItem() for _c in self.COLUMNS])
                self._fill(self.model.rowCount() - 1, a)
            if keep is not None:
                self._select(keep["tag"])
        if total > len(rows):
            self.count.setText("Showing %d of %d alarms. Type to narrow the list." % (len(rows), total))
        else:
            self.count.setText("%d alarms" % len(rows) if len(rows) != 1 else "1 alarm")
        self._show_selected()

    def _fill(self, r: int, a: Dict[str, Any]) -> None:
        shown = STATE_SHOWN.get(a["state"], (a["state"], "", ""))
        cells = (
            a["tag"],
            shown[0],
            "" if a.get("priority") is None else str(a["priority"]),
            a["equipment"],
            a["name"] or a["desc"],
        )
        for c, text in enumerate(cells):
            it = self.model.item(r, c)
            if it is None:
                continue
            it.setEditable(False)
            if it.text() != text:
                it.setText(text)
        tag = self.model.item(r, 0)
        tag.setData(a, _ROW_ROLE)
        tag.setToolTip(
            "%s\n%s%s"
            % (
                a["tag"],
                a.get("desc") or a.get("name") or "",
                "\nOn since %s" % a["onTime"] if a.get("onTime") else "",
            )
        )
        self.model.item(r, 1).setIcon(badge(a["state"]))

    def _show_selected(self) -> None:
        a = self.selected_row()
        self.state.setEnabled(a is not None)
        if a is None:
            return
        word = STATE_SHOWN.get(a["state"], ("", "", ""))[1]
        k = self.state.findData(word)
        if k >= 0 and k != self.state.currentIndex():
            self.state.setCurrentIndex(k)

    def _state_chosen(self, k: int) -> None:
        a = self.selected_row()
        if a is not None:
            self.set_state(a["tag"], self.state.itemData(k))

    def set_state(self, tag: str, word: str) -> None:
        fn = getattr(self._src, "set_alarm", None)
        if fn is not None and word:
            fn(tag, word)
            self.refresh(force=True)

    def _menu(self, pos) -> None:
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        self.tree.setCurrentIndex(idx)
        a = self.selected_row()
        if a is None:
            return
        m = QtWidgets.QMenu(self)
        for word, label in ALARM_STATES:
            act = m.addAction(label)
            act.triggered.connect(lambda _c=False, w=word: self.set_state(a["tag"], w))
        m.exec(self.tree.viewport().mapToGlobal(pos))


class EquipmentPanel(_ListPanel):
    COLUMNS = ("Equipment", "Type", "MODE", "STATE", "DRMODE", "Scheduled")
    KEY = "Equipment"
    PLACEHOLDER = "Search equipment"
    PAGE_ONLY = "Only equipment of this page"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Simulated equipment")
        self.tree.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        self._busy = False
        self.model.itemChanged.connect(self._edited)
        tip = theme.note_label(
            "Double-click MODE, STATE or DRMODE to set it, as the scheduler or EquipSetProperty would."
        )
        tip.setWordWrap(True)
        self.layout().addWidget(tip)

    def _refresh(self) -> None:
        fn = getattr(self._src, "equipment_rows", None)
        rows, total = (None, 0)
        if fn is not None:
            try:
                rows, total = _call(fn, self.search.text().strip(), regex=self.search.regex)
            except Exception:  # noqa: BLE001
                rows, total = None, 0
        if rows is None:
            self.model.removeRows(0, self.model.rowCount())
            self._shown_key = None
            note = self._note()
            self.count.setText(note or ("Waiting for the simulator." if self._src is not None else ""))
            return
        if self.page_only.isChecked():
            pe = []
            pf = getattr(self._src, "page_equipment", None)
            if pf is not None:
                try:
                    pe = pf()
                except Exception:  # noqa: BLE001
                    pe = []
            keys = page_equipment_keys(pe)
            rows = [e for e in rows if _in_page(e["name"], keys)]
        key = tuple(
            (e["name"], e["type"]) + tuple(e[f] for f in EQUIPMENT_FIELDS) + (e["scheduled"],) for e in rows
        )
        if key == self._shown_key:
            return
        if self.tree.state() == QtWidgets.QAbstractItemView.EditingState:
            return  # the user types a value: not now
        same_rows = self._shown_key is not None and [k[0] for k in key] == [k[0] for k in self._shown_key]
        self._shown_key = key
        self._busy = True
        try:
            if not same_rows:
                keep = self.selected_row()
                self.model.removeRows(0, self.model.rowCount())
                for _e in rows:
                    self.model.appendRow([QtGui.QStandardItem() for _c in self.COLUMNS])
            for r, e in enumerate(rows):
                cells = (
                    (e["name"], e["type"])
                    + tuple(e[f] for f in EQUIPMENT_FIELDS)
                    + ("Yes" if e["scheduled"] else "No",)
                )
                for c, text in enumerate(cells):
                    it = self.model.item(r, c)
                    it.setEditable(2 <= c <= 4)
                    if it.text() != text:
                        it.setText(text)
                self.model.item(r, 0).setData(e, _ROW_ROLE)
            if not same_rows and keep is not None:
                self._select(keep["name"])
        finally:
            self._busy = False
        n = len(rows)
        if total > n and not self.page_only.isChecked():
            self.count.setText("Showing %d of %d equipment. Type to narrow the list." % (n, total))
        else:
            self.count.setText("%d equipment" % n)

    def _edited(self, item: QtGui.QStandardItem) -> None:
        if self._busy or not 2 <= item.column() <= 4:
            return
        row = self.model.item(item.row(), 0)
        e = row.data(_ROW_ROLE) if row is not None else None
        if not e:
            return
        field = EQUIPMENT_FIELDS[item.column() - 2]
        value = item.text().strip()
        if value == e.get(field):
            return
        fn = getattr(self._src, "set_equipment", None)
        if fn is not None:
            fn(e["name"], field, value)
            e[field] = value


from ...infra import caches as _caches  # noqa: E402

_caches.register("alarm_panels.badges", _BADGES, on=("tests",))
