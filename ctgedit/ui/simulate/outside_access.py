"""DLLs and SQL databases in the simulator."""

from __future__ import annotations
from ..style.theme import sp as _sp

from typing import Any, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as _help
from ..style import theme
from . import options

MODES = (("Off", "off"), ("Ask", "ask"), ("On", "on"))

#: the notice under each setting, by mode
NOTICES = {
    "dll": {
        "off": "Off: the simulator loads no DLL. DLLOpen fails as it does for a missing file. "
        "Ask and On let the page's Cicode run code from DLLs on this computer.",
        "ask": "Ask: the simulator asks before the page's Cicode loads a DLL that is not on "
        "the allowed list. A loaded DLL runs its own code on this computer, with your "
        "rights.",
        "on": "On: every DLL the page's Cicode asks for is loaded without a question. A DLL "
        "runs its own code on this computer, with your rights.",
    },
    "sql": {
        "off": "Off: the simulator reaches no database. SQLConnect fails as it does when the "
        "database is down. Ask and On let the page's Cicode read and change your real "
        "databases.",
        "ask": "Ask: the simulator asks before it connects to a database that is not on the "
        "allowed list, and before a statement that can change it (INSERT, UPDATE, "
        "DELETE and the like). These are your real databases.",
        "on": "On: the page's Cicode connects to your real databases and changes them without a question.",
    },
}
#: the notice's colour by mode: yellow, orange, red (theme roles)
NOTICE_ROLES = {"off": "warn", "ask": "caution", "on": "danger"}


class AllowedListDialog(QtWidgets.QDialog):
    """The allowed DLLs or databases: a list with Add and Remove. ``kind`` 'dll' (Add picks a
    file) or 'sql' (Add asks for the database as the question names it)."""

    def __init__(self, kind: str, items: List[str], parent=None):
        super().__init__(parent)
        Q = QtWidgets
        self.kind = kind
        dll = kind == "dll"
        self.setWindowTitle("Allowed DLLs" if dll else "Allowed Databases")
        _help.set_help(self, "simulate#dlls-and-databases")
        lay = Q.QVBoxLayout(self)
        intro = theme.note_label(
            "With DLLs set to Ask, the simulator loads these DLLs without a question."
            if dll
            else "With SQL databases set to Ask, the simulator connects to these databases and "
            "runs their statements without a question."
        )
        lay.addWidget(intro)
        self.list = Q.QListWidget()
        self.list.setAccessibleName(self.windowTitle())
        self.list.setSelectionMode(Q.QAbstractItemView.ExtendedSelection)
        for x in options.parse_list(items):
            self.list.addItem(x)
        lay.addWidget(self.list, 1)
        row = Q.QHBoxLayout()
        self.add_button = Q.QPushButton("Add…")
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self._add)
        self.remove_button = Q.QPushButton("Remove")
        self.remove_button.setAutoDefault(False)
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        lay.addLayout(row)
        bb = Q.QDialogButtonBox(Q.QDialogButtonBox.Ok | Q.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.list.itemSelectionChanged.connect(self._sync)
        self._sync()
        self.resize(560, 360)

    def _sync(self) -> None:
        self.remove_button.setEnabled(bool(self.list.selectedItems()))

    def items(self) -> List[str]:
        return options.parse_list([self.list.item(k).text() for k in range(self.list.count())])

    def add(self, text: str) -> bool:
        text = str(text).strip()
        if not text or text.lower() in {x.lower() for x in self.items()}:
            return False
        self.list.addItem(text)
        return True

    def _add(self) -> None:
        if self.kind == "dll":
            paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Choose DLLs to Allow", "", "DLLs (*.dll);;All files (*)"
            )
            import os

            for p in paths:
                self.add(os.path.normpath(p))
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Allow a Database",
            "The database as the question names it, for example DSN=Plant or\n"
            "Driver={SQL Server}; Server=srv1; Database=History:",
        )
        if ok:
            self.add(text)

    def _remove(self) -> None:
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))


class AccessControls(QtCore.QObject):
    """One setting (``kind`` 'dll' or 'sql'): the Off / Ask / On box, its notice and the
    button that opens its allowed list."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        Q = QtWidgets
        self.kind = kind
        dll = kind == "dll"
        self.mode = Q.QComboBox()
        for label, value in MODES:
            self.mode.addItem(label, value)
        self.mode.setAccessibleName("DLLs" if dll else "SQL databases")
        self.mode.setToolTip(
            "Whether the page's Cicode may load DLLs (DLLOpen, DllClassCreate)"
            if dll
            else "Whether the page's Cicode may reach SQL databases (SQLConnect and the other SQL functions)"
        )
        self.notice = theme.note_label("", "warn")
        self.notice.setObjectName("OutsideAccessNotice")
        self.allowed: List[str] = []
        self.allowed_button = Q.QPushButton()
        self.allowed_button.setAutoDefault(False)
        self.allowed_button.setToolTip(
            "The DLLs that Ask loads without a question"
            if dll
            else "The databases that Ask connects to and changes without a question"
        )
        self.allowed_button.clicked.connect(self._edit)
        # DLLs: how long a call may take (the runtime waits as long as the library takes)
        self.timeout = Q.QSpinBox() if dll else None
        if self.timeout is not None:
            self.timeout.setRange(0, options.DLL_TIMEOUT_MAX)
            self.timeout.setSpecialValueText("No limit")
            self.timeout.setSuffix(" s")
            self.timeout.setAccessibleName("DLL call time limit")
            self.timeout.setToolTip(
                "How long a DLL call may take. No limit: as long as the DLL takes, as in the "
                "runtime (a message box a DLL shows waits for its click). With a limit, a call "
                "that takes longer fails and the DLLs are loaded again at the next call"
            )
        self.mode.currentIndexChanged.connect(self._sync)
        self._sync()

    def value(self) -> str:
        return str(self.mode.currentData() or "off")

    def set_value(self, mode: str) -> None:
        k = self.mode.findData(mode if mode in options.ACCESS_MODES else "off")
        self.mode.setCurrentIndex(k if k >= 0 else 0)
        self._sync()

    def set_allowed(self, items) -> None:
        self.allowed = options.parse_list(list(items))
        self._sync()

    def _sync(self, *_a) -> None:
        mode = self.value()
        self.notice.setText(NOTICES[self.kind][mode])
        theme.set_note_role(self.notice, NOTICE_ROLES[mode])
        n = len(self.allowed)
        what = "Allowed DLLs" if self.kind == "dll" else "Allowed Databases"
        self.allowed_button.setText("%s (%d)…" % (what, n) if n else what + "…")

    def _edit(self) -> None:
        dlg = AllowedListDialog(self.kind, self.allowed, self.allowed_button.window())
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.set_allowed(dlg.items())

    def lay_out(self, section, label: str) -> None:
        """The box, then the notice and the button under it."""
        section.row(label, self.mode, extra=" ".join(NOTICES[self.kind].values()))
        self.notice.setContentsMargins(0, 0, 0, _sp(0))
        section.sub(self.notice)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.allowed_button)
        if self.timeout is not None:
            row.addSpacing(12)
            row.addWidget(QtWidgets.QLabel("Call time limit:"))
            row.addWidget(self.timeout)
        row.addStretch(1)
        section.sub(row)


#: the question's texts by (access, action): title, what is asked, the warning
QUESTIONS = {
    ("dll", "load"): (
        "Load a DLL?",
        "The page's Cicode wants to load this DLL and call its functions:",
        "A DLL runs its own code on this computer, with your rights.",
    ),
    ("sql", "connect"): (
        "Connect to a Database?",
        "The page's Cicode wants to connect to this database:",
        "This is the real database, not a copy.",
    ),
    ("sql", "write"): (
        "Change a Database?",
        "The page's Cicode wants to run a statement that can change this database:",
        "The statement runs on the real database.",
    ),
}


class PermissionDialog(QtWidgets.QDialog):
    """The simulator's question in mode Ask (ctsim/dialog kind 'permission'). ``choice`` is
    'once', 'session', 'always' or 'deny' (closing the window denies)."""

    def __init__(self, body: Dict[str, Any], parent=None):
        super().__init__(parent)
        Q = QtWidgets
        self.access = str(body.get("access") or "")
        self.action = str(body.get("action") or "")
        self.target = str(body.get("target") or "")
        self.detail = str(body.get("detail") or "")
        self.choice = "deny"
        title, ask, warning = QUESTIONS.get(
            (self.access, self.action), (str(body.get("title") or "Allow?"), str(body.get("text") or ""), "")
        )
        self.setWindowTitle(title)
        _help.set_help(self, "simulate#dlls-and-databases")
        lay = Q.QVBoxLayout(self)
        self.ask_label = Q.QLabel(ask)
        self.ask_label.setWordWrap(True)
        lay.addWidget(self.ask_label)
        self.target_label = Q.QLabel(self.target)
        self.target_label.setWordWrap(True)
        self.target_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        f = self.target_label.font()
        f.setBold(True)
        self.target_label.setFont(f)
        lay.addWidget(self.target_label)
        self.detail_view: Optional[QtWidgets.QWidget] = None
        if self.action == "write":
            box = Q.QPlainTextEdit(self.detail)
            box.setReadOnly(True)
            box.setAccessibleName("The statement")
            box.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            box.setMaximumHeight(120)
            lay.addWidget(box)
            self.detail_view = box
        elif self.detail and self.access == "dll":
            lab = Q.QLabel("Function or class: %s" % self.detail)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            self.detail_view = lab
        if warning:
            self.warning_label = theme.note_label(warning, "caution")
            lay.addWidget(self.warning_label)
        bb = Q.QDialogButtonBox()
        dll = self.access == "dll"
        self.once_button = bb.addButton("Allow Once", Q.QDialogButtonBox.ActionRole)
        self.session_button = bb.addButton("Allow for This Simulation", Q.QDialogButtonBox.ActionRole)
        self.always_button = bb.addButton("Always Allow", Q.QDialogButtonBox.ActionRole)
        self.deny_button = bb.addButton("Deny", Q.QDialogButtonBox.RejectRole)
        self.once_button.setToolTip("This one time only")
        self.session_button.setToolTip(
            "This DLL loads without a question until the simulation ends"
            if dll
            else "This database is connected to and changed without a question until the simulation ends"
            if self.action == "write"
            else "This database is connected to without a question until the simulation ends"
        )
        self.always_button.setToolTip(
            "Adds this DLL to the allowed DLLs (Settings, Simulate)"
            if dll
            else "Adds this database to the allowed databases (Settings, Simulate): it is "
            "connected to and changed without a question"
        )
        for b, c in (
            (self.once_button, "once"),
            (self.session_button, "session"),
            (self.always_button, "always"),
        ):
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, c=c: self._choose(c))
        self.deny_button.setDefault(True)
        self.deny_button.clicked.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(460)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    def reject(self) -> None:
        self.choice = "deny"
        super().reject()
