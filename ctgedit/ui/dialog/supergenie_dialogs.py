"""Super genie dialogs."""

from __future__ import annotations

from ..dialog_buttons import dialog_buttons

import os
from typing import Callable, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra.logsetup import note_exception as _note_exc


def library_token(lib_path: str) -> str:
    """The library name a reference writes for ``lib_path``: its file stem,
    lower-cased when the file name is all capitals (the shipped CONTROLS.CTM /
    VALVES.CTM are referenced as 'controls' / 'valves')."""
    stem = os.path.splitext(os.path.basename(lib_path))[0]
    return stem.lower() if stem.isupper() else stem


def supergenie_name_advice(name: str) -> str:
    """The advice for a new super genie name ('' when there is none): start
    it with '!' (help 1172237): the page it creates keeps that name, and
    '!' pages are system pages."""
    name = (name or "").strip()
    if not name or name.startswith("!"):
        return ""
    return (
        "Super genie names usually start with '!' (for example '!%s'). The page it "
        "creates takes that name." % name.split("@", 1)[0]
    )


class AttachSuperGeniesDialog(QtWidgets.QDialog):
    """Edit > Attach Super Genies."""

    def __init__(
        self,
        attachments: Sequence[str],
        parent=None,
        *,
        genie: str = "",
        project_dir: Optional[str] = None,
        problem: Optional[Callable[[str], Optional[str]]] = None,
        pick: Optional[Callable[[], Optional[str]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Attach Super Genies" + (" - %s" % genie if genie else ""))
        self._problem = problem
        self._pick = pick
        self._project_dir = project_dir
        self._initial = [a for a in attachments]
        lay = QtWidgets.QVBoxLayout(self)
        from ..style.theme import set_note_role

        intro = QtWidgets.QLabel("Super genies this genie opens.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self._list = QtWidgets.QListWidget()
        self._list.setMinimumWidth(320)
        lay.addWidget(self._list, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("&Add…")
        self.btn_add.setToolTip("Choose a super genie")
        self.btn_remove = QtWidgets.QPushButton("&Remove")
        self.btn_up = QtWidgets.QPushButton("Move &Up")
        self.btn_down = QtWidgets.QPushButton("Move &Down")
        for b in (self.btn_add, self.btn_remove, self.btn_up, self.btn_down):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._msg = QtWidgets.QLabel("")
        self._msg.setWordWrap(True)
        set_note_role(self._msg, "danger")  # (follows a theme change)
        lay.addWidget(self._msg)
        buttons = dialog_buttons(self)
        lay.addWidget(buttons)
        self.btn_add.clicked.connect(self._add)
        self.btn_remove.clicked.connect(self._remove)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self._list.currentRowChanged.connect(lambda _r: self._sync())
        for name in attachments:
            self._append(name)
        self._sync()

    # -- rows --------------------------------------------------------------------------
    def _append(self, name: str) -> None:
        from ...format import supergenie as SG

        lib, _, elem = name.rpartition(".")  # (the last dot, as CtDraw splits a link)
        it = QtWidgets.QListWidgetItem(name)
        it.setData(QtCore.Qt.UserRole, name)
        why = None
        if self._problem is not None:
            try:
                why = self._problem(name)
            except Exception:  # noqa: BLE001 -- a hint only
                _note_exc("supergenie_dialogs._append", name=name)
                why = None
        page = SG.page_name(elem) or elem
        if why:
            from ..style.theme import COLORS

            it.setForeground(QtGui.QColor(COLORS["warn"]))
            it.setToolTip(why)
            it.setText("%s (not found)" % name)
        else:
            it.setToolTip("Creates page '%s' when the genie is placed" % page)
        self._list.addItem(it)

    def names(self) -> List[str]:
        return [self._list.item(i).data(QtCore.Qt.UserRole) for i in range(self._list.count())]

    def add_name(self, name: str) -> bool:
        """Add ``name`` ('library.name', '@SUPER' optional). False (with the
        reason shown) when it is already listed or cannot be attached."""
        name = (name or "").strip().split("@", 1)[0]
        if not name:
            return False
        if name.lower() in {n.lower() for n in self.names()}:
            self._msg.setText("'%s' is already attached." % name)
            return False
        if self._problem is not None:
            why = self._problem(name)
            if why:
                self._msg.setText(why)
                return False
        self._append(name)
        self._list.setCurrentRow(self._list.count() - 1)
        self._msg.setText("")
        self._sync()
        return True

    def _default_pick(self) -> Optional[str]:
        from ..browser import PageBrowserDialog

        got = PageBrowserDialog.get_library_element(
            self, self._project_dir, "supergenie", title="Select Super Genie"
        )
        if got is None:
            return None
        path, element = got
        return "%s.%s" % (library_token(path), element.split("@", 1)[0])

    def _add(self) -> None:
        pick = self._pick or self._default_pick
        name = pick()
        if name:
            self.add_name(name)

    def _remove(self) -> None:
        r = self._list.currentRow()
        if r >= 0:
            self._list.takeItem(r)
        self._msg.setText("")
        self._sync()

    def _move(self, step: int) -> None:
        r = self._list.currentRow()
        k = r + step
        if r < 0 or not 0 <= k < self._list.count():
            return
        it = self._list.takeItem(r)
        self._list.insertItem(k, it)
        self._list.setCurrentRow(k)
        self._sync()

    def _sync(self) -> None:
        r = self._list.currentRow()
        n = self._list.count()
        self.btn_remove.setEnabled(r >= 0)
        self.btn_up.setEnabled(r > 0)
        self.btn_down.setEnabled(0 <= r < n - 1)


# ------------------------------------------------------------ open guard
class SuperGeniePageDialog(QtWidgets.QMessageBox):
    """Asked before a page created from a super genie opens (see the module
    docstring). ``choice()`` after exec: 'edit' (open it for editing), 'view'
    (read-only), 'definition' (open the super genie) or 'cancel'."""

    def __init__(
        self, page: str, supergenie: str, allow_edit: bool, parent=None, can_open_definition: bool = True
    ):
        super().__init__(parent)
        self._choice = "cancel"
        self.setIcon(QtWidgets.QMessageBox.Question if allow_edit else QtWidgets.QMessageBox.Information)
        self.setWindowTitle("Page Created from a Super Genie")
        if allow_edit:
            text = (
                "'%s' was created from super genie '%s'. Update Pages creates it again "
                "and overwrites your changes. Open it for editing anyway?" % (page, supergenie)
            )
        else:
            text = (
                "'%s' was created from super genie '%s'. To change it, edit the super "
                "genie. You can also open the page read-only." % (page, supergenie)
            )
        self.setText(text)
        self.setInformativeText(
            "Set by [CtDraw.RSC] AllowEditSuperGeniePage in citect.ini (now %d)." % (1 if allow_edit else 0)
        )
        self._buttons = {}
        if can_open_definition:
            self._buttons["definition"] = self.addButton("Edit Super Genie", QtWidgets.QMessageBox.AcceptRole)
        if allow_edit:
            self._buttons["edit"] = self.addButton("Open Page", QtWidgets.QMessageBox.YesRole)
        self._buttons["view"] = self.addButton("Open Read-Only", QtWidgets.QMessageBox.NoRole)
        self._buttons["cancel"] = self.addButton(QtWidgets.QMessageBox.Cancel)
        self.setDefaultButton(self._buttons.get("definition") or self._buttons["view"])
        self.setEscapeButton(self._buttons["cancel"])
        self.buttonClicked.connect(self._clicked)

    def _clicked(self, button) -> None:
        for k, b in self._buttons.items():
            if b is button:
                self._choice = k
                return

    def choice(self) -> str:
        return self._choice

    def choose(self, key: str) -> None:
        """Tests: the button ``key`` as if clicked."""
        self._choice = key
