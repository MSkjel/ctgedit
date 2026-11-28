"""A setting that is a list of commands, with Add, Remove, Move Up and Move Down."""

from __future__ import annotations
from ...format.cnum import is_digits
from ..style.theme import sp as _sp

from typing import List, Optional

from PySide6 import QtCore, QtWidgets


class CommandListField(QtWidgets.QWidget):
    """The commands of a list setting, in order (see the module)."""

    def __init__(self, window, parent=None, tip: str = ""):
        super().__init__(parent)
        self._w = window
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QtWidgets.QListWidget(self)
        self.list.setMinimumHeight(96)
        self.list.setMaximumHeight(150)
        self.list.setMinimumWidth(180)
        self.list.setAccessibleName("Commands")
        if tip:
            self.list.setToolTip(tip)
        lay.addWidget(self.list, 1)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(_sp(1))
        self.add_button = QtWidgets.QPushButton("Add…", self)
        self.add_button.setToolTip("Add a menu command")
        self.remove_button = QtWidgets.QPushButton("Remove", self)
        self.remove_button.setToolTip("Take the selected command out")
        self.up_button = QtWidgets.QPushButton("Move Up", self)
        self.down_button = QtWidgets.QPushButton("Move Down", self)
        for b in (self.add_button, self.remove_button, self.up_button, self.down_button):
            b.setAutoDefault(False)
            col.addWidget(b)
        col.addStretch(1)
        lay.addLayout(col)
        self.add_button.clicked.connect(lambda: self.add_command())
        self.remove_button.clicked.connect(self.remove_command)
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button.clicked.connect(lambda: self.move(1))

    # -- the value ----------------------------------------------------------------------
    def value(self) -> List[str]:
        """The command ids, in order."""
        out = []
        for k in range(self.list.count()):
            it = self.list.item(k)
            cid = it.data(QtCore.Qt.UserRole) or it.text()
            out.append(str(cid))
        return out

    def set_value(self, ids) -> None:
        """Show the commands ``ids`` (a command this window does not have shows its id)."""
        if isinstance(ids, str):
            ids = [ids] if ids else []
        want = [str(x) for x in (ids or ())]
        if want == self.value():
            return
        self.list.clear()
        names = self._names()
        for cid in want:
            self._append(cid, names.get(cid) or self._label(cid, names))

    def _names(self) -> dict:
        """{id: the name shown} of the window's commands: a command's name, with where it is
        when another command has the same name or the name alone does not say what it is (a
        grid size: '8 px (View > Grid)')."""
        from . import keymap

        if self._w is None:
            return {}
        cmds = [c for c in keymap.commands(self._w) if c.label and not c.id.startswith("menu:")]
        count: dict = {}
        for c in cmds:
            count[c.label] = count.get(c.label, 0) + 1
        out = {}
        for c in cmds:
            bare = count[c.label] > 1 or is_digits(c.label[:1])
            out[c.id] = "%s (%s)" % (c.label, c.crumb or "Canvas") if bare else c.label
        return out

    def _label(self, cid: str, names: Optional[dict] = None) -> str:
        """The name a row shows for ``cid`` (a command this window does not have: its id)."""
        from . import keymap

        if names is None:
            names = self._names()
        if cid in names:
            return names[cid]
        a = keymap.find_action(self._w, cid) if self._w is not None else None
        return keymap.label_of(a) if a is not None else cid

    def _append(self, cid: str, label: str) -> None:
        it = QtWidgets.QListWidgetItem(label)
        it.setData(QtCore.Qt.UserRole, cid)
        self.list.addItem(it)

    # -- editing --------------------------------------------------------------------------
    def choices(self) -> list:
        """(label, id) of the commands that can be added: the window's commands not in the
        list, by name (a name two commands share, or a name that alone does not say what it
        is, says where the command is: :meth:`_names`)."""
        have = set(self.value())
        out, seen = [], set()
        for cid, label in sorted(self._names().items(), key=lambda kv: (kv[1].lower(), kv[0])):
            if cid in have or label in seen:
                continue
            seen.add(label)
            out.append((label, cid))
        return out

    def _ask(self, labels: list) -> Optional[str]:
        """The command chosen in a list of ``labels`` (None: cancelled)."""
        from ..messages import ask_item

        return ask_item(self, "Add a Command", "Command:", labels) or None

    def add_command(self, cid: str = "") -> bool:
        """Add… (``cid`` given: that command, without asking). The new row is selected."""
        if not cid:
            pairs = self.choices()
            if not pairs:
                return False
            label = self._ask([p[0] for p in pairs])
            if label is None:
                return False
            cid = dict(pairs).get(label, "")
        if not cid or cid in self.value():
            return False
        self._append(cid, self._label(cid))
        self.list.setCurrentRow(self.list.count() - 1)
        return True

    def remove_command(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def move(self, step: int) -> None:
        row = self.list.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(new, item)
        self.list.setCurrentRow(new)
