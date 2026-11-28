"""File > Properties: the page properties dialog."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra.logsetup import note_exception as _note_exc
from ...format.cnum import is_digits
from ..style import theme as _theme
from ..objprops.input import CommandEdit, KeySequenceDialog, command_argument_warnings, key_sequence_warnings
from ..objprops.scroll import FitTabWidget, scroll_page, unwrap_page

from ...format import codes as _codes
from ..pick_fields import NONE_TEXT, PageField, TemplatePicker, library_stem
from ..messages import warn

#: CtDraw's text the General / Keyboard pages show for no area
ALL_AREAS_TEXT = "<All areas>"

# IGNOREVQ (pages.DBF) -> "Ignore Quality" combo entries: CtDraw's own labels
# with the [Page]IgnoreValueQuality value each
# stands for (AVEVA help 1172104; CtDraw accepts -1..2). -1 / blank = default.
_IGNORE_QUALITY = [
    ("Default Indication", "-1"),
    ("#COM Indication", "0"),
    ("No Indication", "1"),
    ("Background Indication", "2"),
]

# The Events tab in CtDraw's order (CtDraw's event list, keyed !ENTRY / !EXIT /
# !ALWAYS / !READY / !ACTIVATE / !DEACTIVATE) and the pages.DBF column each key
# is saved to (!ALWAYS -> BGND_FUNC, !READY ->
# READY_FUNC, !ACTIVATE -> ACT_FUNC, !DEACTIVATE -> DEACT_FUNC).
_EVENT_FIELDS = [
    ("On page entry:", "ENTRY_FUNC"),
    ("On page exit:", "EXIT_FUNC"),
    ("While page shown:", "BGND_FUNC"),
    ("On page shown:", "READY_FUNC"),
    ("On activate:", "ACT_FUNC"),
    ("On deactivate:", "DEACT_FUNC"),
]
# CtDraw limits an event command to 254 characters (the
# pages.DBF columns are C(254))
_EVENT_MAX = 254
_EVENT_WHEN = {
    "ENTRY_FUNC": "runs while the page opens, before it is shown",
    "EXIT_FUNC": "runs when the operator leaves the page",
    "BGND_FUNC": "runs again and again while the page is shown, at the page scan time",
    "READY_FUNC": "runs once the page is shown and its objects (ActiveX controls too) are ready",
    "ACT_FUNC": "runs when the page's window becomes the active window",
    "DEACT_FUNC": "runs when the page's window stops being the active window",
}

# The Associations tab columns -- the editable ones;
# CtDraw's list view adds 'In Use' ('Yes' / 'No'): whether the name is
# one of the page's ?associations?
_ASSOC_COLUMNS = ["Name", "Default", "Value on Error", "Description"]
_ASSOC_IN_USE = len(_ASSOC_COLUMNS)  # the read-only 'In Use' column
_ASSOC_TIPS = [
    "The association name used as ?name? in this page's expressions: letters, "
    "digits, _ and \\ only, at most 253 characters.",
    "Used when the association is not made at runtime: a literal in single "
    "quotes ('1.23') or a tag or equipment.item name.",
    "Shown when there is no default and the association is not made, or does "
    "not resolve to a valid tag. Plain text, no quotes.",
    "A note for whoever maintains the page.",
]
# CtDraw reads each Default / Value on Error / Description field into a
# 256-byte buffer, so a
# longer field is cut short the next time the page opens in CtDraw
_ASSOC_FIELD_MAX = 255

# CtDraw's Edit Property box reads the name and
# the value into 128-byte buffers
_ENV_MAX = 127

# CtDraw clamps the view area so that width x height stays at most 2**24
# (as the Appearance page is typed in)
_VIEW_AREA_MAX = 0x1000000

_AREAS = tuple(str(n) for n in range(1, 256))
_PRIVILEGES = tuple(str(n) for n in range(1, 9))

# calls the help says an event must not make (help 1172106)
_ENTRY_FORBIDDEN = re.compile(r"\b(Login|UserLogin)\s*\(", re.IGNORECASE)
_EXIT_FORBIDDEN = re.compile(r"\b(PageGoto|PageNext|PagePrev|PageDisplay|PageLast)\s*\(", re.IGNORECASE)
_ACTIVEX_USE = re.compile(
    r"\b(ObjectByName|CreateControlObject|ObjectAssociate\w*|"
    r"_Object\w+|ActiveX\w*)\b",
    re.IGNORECASE,
)


def event_warnings(column: str, command: str) -> List[str]:
    """Advice for an event command (never blocking), from the help's rules
    (1172106): Login() / UserLogin() must not run On page entry, the page
    navigation functions not On page exit, and commands that use ActiveX
    controls belong in On page shown."""
    out = []
    cmd = command or ""
    if column == "ENTRY_FUNC":
        m = _ENTRY_FORBIDDEN.search(cmd)
        if m:
            out.append(
                "Do not call %s() in On page entry: the page is not shown yet. "
                "Log in from a button or a keyboard command." % m.group(1)
            )
        if _ACTIVEX_USE.search(cmd):
            out.append(
                "This command seems to use ActiveX controls. Put it in On page "
                "shown: On page entry runs before the controls are ready."
            )
    if column == "EXIT_FUNC":
        m = _EXIT_FORBIDDEN.search(cmd)
        if m:
            out.append(
                "Do not call %s() in On page exit: the page is already being "
                "replaced by another one." % m.group(1)
            )
    return out


def _combo(items=(), editable: bool = True, limit: int = 0) -> QtWidgets.QComboBox:
    cb = QtWidgets.QComboBox()
    cb.setEditable(editable)
    cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
    for it in items:
        cb.addItem(it)
    if editable and limit:
        cb.lineEdit().setMaxLength(limit)
    return cb


def _combo_value(cb: QtWidgets.QComboBox) -> str:
    """A combo's value: the typed text, '' for the '<None>' entry."""
    t = cb.currentText().strip()
    return "" if t in (NONE_TEXT, ALL_AREAS_TEXT) else t


def _counter(edit: QtWidgets.QLineEdit, limit: int) -> QtWidgets.QLabel:
    """'n / limit' beside a line edit (in the danger colour past the limit, following the
    theme)."""
    lab = QtWidgets.QLabel()
    lab.setObjectName("dim")

    def upd(_t=""):
        n = len(edit.text())
        lab.setText("%d / %d" % (n, limit))
        _theme.set_note_role(lab, "danger" if n > limit else "text_dim")

    edit.textChanged.connect(upd)
    upd()
    return lab


class _ContentTypeCombo(QtWidgets.QComboBox):
    """The Content type field whose list is read when it first opens or the field takes the
    focus, a key or the wheel (:meth:`fill`): the Properties panel builds its page form after
    every edit with nothing selected, and the list is the project's contype.dbf."""

    def __init__(self, project_dir):
        super().__init__()
        self._project_dir = project_dir

    def fill(self) -> None:
        """List the content types now (once; the text typed or set stays)."""
        if self._project_dir is False:
            return
        project_dir, self._project_dir = self._project_dir, False
        text = self.lineEdit().text() if self.lineEdit() is not None else None
        blocked = self.blockSignals(True)
        try:
            _fill_content_types(self, project_dir)
            if text is not None and self.lineEdit().text() != text:
                self.setEditText(text)
        finally:
            self.blockSignals(blocked)

    def showPopup(self) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().showPopup()

    def focusInEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()  # (the completer completes from the listed types)
        super().focusInEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().keyPressEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        self.fill()
        super().wheelEvent(ev)


def _fill_content_types(cb: QtWidgets.QComboBox, project_dir) -> None:
    """'' (no content type) and the content types of the tag scope, comments as tips."""
    from ...io import project as P

    types = ()
    if project_dir:
        try:
            types = P.content_types(project_dir)
        except Exception:  # noqa: BLE001  # swallow-ok: the list is an aid
            types = ()
    first = cb.count()
    for it in [""] + [r["NAME"] for r in types]:
        cb.addItem(it)
    for i, r in enumerate(types):
        cb.setItemData(first + i + 1, r.get("COMMENT", ""), QtCore.Qt.ToolTipRole)


def content_type_combo(
    project_dir, value: str = "", limit: int = 0, lazy: bool = False
) -> QtWidgets.QComboBox:
    """The Content type field (Page Properties > General and the Properties panel): an
    editable list of the content types of the page's tag scope (contype.dbf), their
    comments as tool tips; '' first (no content type). ``lazy``: the list is read when the
    field is first used (:class:`_ContentTypeCombo`)."""
    if lazy:
        cb = _ContentTypeCombo(project_dir)
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        if limit:
            cb.lineEdit().setMaxLength(limit)
    else:
        cb = _combo((), limit=limit)
        _fill_content_types(cb, project_dir)
    if limit and len(value or "") > limit:
        cb.lineEdit().setMaxLength(len(value))
    cb.setEditText(value or "")
    cb.setToolTip("The page's content type. Situational Awareness panes show pages by content type.")
    # a list of names: it grows with the window like the page fields
    pol = cb.sizePolicy()
    pol.setHorizontalPolicy(QtWidgets.QSizePolicy.Expanding)
    cb.setSizePolicy(pol)
    return cb


def _buddy(text: str, widget: QtWidgets.QWidget) -> QtWidgets.QLabel:
    """A field label whose '&' letter jumps to ``widget`` (Alt+letter)."""
    lab = QtWidgets.QLabel(text)
    lab.setBuddy(widget)
    return lab


def environment_name_problem(name: str) -> str:
    """Why ``name`` cannot be a page variable ('' = it can)."""
    if not name:
        return "Type the variable's name."
    if name.startswith("!"):
        return "A name cannot start with '!': those are the Graphics Builder's own."
    if "\t" in name:
        return "A name cannot contain a tab."
    if len(name) > _ENV_MAX:
        return "A name can be at most %d characters long." % _ENV_MAX
    return ""


class PageKeyboardTab(QtWidgets.QWidget):
    """Page Properties > Keyboard Commands: the page's key sequences (as in
    CtDraw). Each entry is a pgkey row with a blank AN; a
    V3/V4 page-wide keyboard RECORD (a root tc 0x100B, none in the shipped
    projects) is listed too and edited in place."""

    FIELDS = ("KEY_SEQ", "CMD", "AREA", "PRIV", "MSG_LOG")

    #: an entry was added, removed or edited (the dialog checks the input)
    edited = QtCore.Signal()

    def __init__(self, doc, context=None, page_area=None, parent=None, enabled=True):
        super().__init__(parent)
        self._doc = doc
        self._context = context
        self._page_area = page_area or (lambda: "")
        self._enabled = enabled
        self._cur = -1
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Key sequence"))
        self.key_list = QtWidgets.QListWidget()
        self.key_list.setMinimumWidth(170)
        left.addWidget(self.key_list, 1)
        brow = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add…")
        self.btn_edit = QtWidgets.QPushButton("Edit…")
        self.btn_del = QtWidgets.QPushButton("Remove")
        self.btn_add.setToolTip("Add a key sequence")
        self.btn_edit.setToolTip("Change the selected key sequence")
        self.btn_del.setToolTip("Remove the selected key sequence and its command")
        for b in (self.btn_add, self.btn_edit, self.btn_del):
            brow.addWidget(b)
        left.addLayout(brow)
        lay.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        # (the stored keyboard commands could not be read: they cannot be changed now)
        self.read_note = _theme.note_label("", "warn")
        self.read_note.setVisible(False)
        right.addWidget(self.read_note)
        self.cmd_group = QtWidgets.QGroupBox("Key sequence command")
        cg = QtWidgets.QVBoxLayout(self.cmd_group)
        self.cmd = CommandEdit("", limit=254, min_lines=3)
        self.cmd.setToolTip(
            "Runs when the operator types the key sequence while the page is shown. At most 254 characters."
        )
        cg.addWidget(self.cmd, 1)
        row = QtWidgets.QHBoxLayout()
        if context is not None:
            from ..objprops.expression import wizard_button

            self.cmd.wizard = wizard_button(self.cmd, context)
            self.cmd.wizard.setAutoRaise(True)
            row.addWidget(self.cmd.wizard)
        row.addStretch(1)
        row.addWidget(self.cmd.counter)
        cg.addLayout(row)
        self.advice = _theme.note_label("", "warn")
        cg.addWidget(self.advice)
        right.addWidget(self.cmd_group, 1)

        sec = QtWidgets.QGroupBox("Security")
        sg = QtWidgets.QGridLayout(sec)
        self.same_area = QtWidgets.QCheckBox("Same area as page")
        self.same_area.setToolTip("Only operators with access to the page's area can run the command")
        self.area = _combo(_AREAS, limit=16)
        self.area.setToolTip(
            "The area an operator needs access to (1 to 255, or an area name; at most 16 characters)"
        )
        self.area.setMinimumWidth(120)  # room for '<All areas>'
        self.no_priv = QtWidgets.QCheckBox("No privilege restrictions")
        self.priv = _combo(_PRIVILEGES, limit=16)
        self.priv.setMinimumWidth(90)
        self.priv.setToolTip("The privilege level an operator needs (1 to 8)")
        sg.addWidget(self.same_area, 0, 0, 1, 2)
        sg.addWidget(QtWidgets.QLabel("Command area:"), 1, 0)
        sg.addWidget(self.area, 1, 1)
        sg.addWidget(self.no_priv, 0, 2, 1, 2)
        sg.addWidget(QtWidgets.QLabel("Privilege level:"), 1, 2)
        sg.addWidget(self.priv, 1, 3)
        # the fields take the extra width, not their labels
        sg.setColumnStretch(1, 1)
        sg.setColumnStretch(3, 1)
        right.addWidget(sec)

        logg = QtWidgets.QGroupBox("Logging")
        lf = QtWidgets.QFormLayout(logg)
        self.log = QtWidgets.QLineEdit()
        self.log.setMaxLength(32)
        self.log.setToolTip(
            "Written to the page's log device (General tab) when the command runs. At most 32 characters."
        )
        lf.addRow("Log message:", self.log)
        right.addWidget(logg)
        crow = QtWidgets.QHBoxLayout()
        crow.addStretch(1)
        self.btn_clear = QtWidgets.QPushButton("Clear Property")
        self.btn_clear.setToolTip("Remove every keyboard command of the page")
        crow.addWidget(self.btn_clear)
        right.addLayout(crow)
        lay.addLayout(right, 1)

        self.same_area.toggled.connect(self._sync_security)
        self.no_priv.toggled.connect(self._sync_security)
        self.key_list.currentRowChanged.connect(self._on_row)
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del.clicked.connect(self._delete)
        self.btn_clear.clicked.connect(self.clear)
        self.cmd.textChanged.connect(self._check)
        self.key_list.itemDoubleClicked.connect(lambda _it: self._edit())
        for sig in (
            self.log.textChanged,
            self.area.editTextChanged,
            self.priv.editTextChanged,
            self.same_area.toggled,
            self.no_priv.toggled,
        ):
            sig.connect(lambda *_a: self.edited.emit())
        self.reload()

    # -- state ------------------------------------------------------------------
    def _record_entries(self) -> List[dict]:
        page = self._doc.page
        out = []
        for i in page.roots() if page is not None else ():
            o = page.objects[i]
            if o.raw.get("_type_code") != _codes.TC.KEYBOARD:
                continue
            props = o.raw.get("_props", {}) or {}
            e = {k: str(props.get(k, "") or "") for k in self.FIELDS}
            e["_record"] = i
            out.append(e)
        return out

    def reload(self) -> None:
        """(Re)read the page's keyboard commands from the document. When the stored ones
        cannot be read now (the project database is busy), the page's keyboard commands
        cannot be changed: a list made without them would replace them all on save."""
        rows, unread = self._doc.page_keyboard_read() if self._enabled else ([], "")
        if unread:
            self._enabled = False
            why = unread.strip()
            self.read_note.setText(
                "The page's keyboard commands cannot be read now. %s "
                "Close this window and try again later to change them."
                % (why if why.endswith(".") else why + ".")
            )
            self.read_note.setVisible(True)
        self._entries: List[dict] = [dict(r, _record=None) for r in rows] + self._record_entries()
        self._entries0 = [dict(e) for e in self._entries]
        self._fill(0)

    def _fill(self, select: int = 0) -> None:
        self._cur = -1
        self.key_list.blockSignals(True)
        self.key_list.clear()
        for e in self._entries:
            text = e.get("KEY_SEQ") or "(no key sequence)"
            it = QtWidgets.QListWidgetItem(text)
            if e.get("_record") is not None:
                it.setToolTip("A page-wide keyboard object on the page (V3/V4)")
            self.key_list.addItem(it)
        self.key_list.blockSignals(False)
        if self._entries:
            self.key_list.setCurrentRow(max(0, min(select, len(self._entries) - 1)))
        else:
            self._on_row(-1)
        self.btn_clear.setEnabled(self._enabled and bool(self._entries))
        self.edited.emit()

    def _store(self) -> None:
        if 0 <= self._cur < len(self._entries):
            e = self._entries[self._cur]
            e["CMD"] = self.cmd.value()
            e["AREA"] = "" if self.same_area.isChecked() else _combo_value(self.area)
            e["PRIV"] = "" if self.no_priv.isChecked() else _combo_value(self.priv)
            e["MSG_LOG"] = self.log.text()

    def _on_row(self, row: int) -> None:
        self._store()
        self._cur = row
        have = 0 <= row < len(self._entries)
        e = self._entries[row] if have else {}
        seq = e.get("KEY_SEQ", "")
        self.cmd_group.setTitle("'%s' command" % seq if seq else "Key sequence command")
        self.cmd.set_value(e.get("CMD", ""))
        self.log.setText(e.get("MSG_LOG", ""))
        for cb, chk, key in ((self.area, self.same_area, "AREA"), (self.priv, self.no_priv, "PRIV")):
            chk.blockSignals(True)
            chk.setChecked(not e.get(key))
            chk.blockSignals(False)
            if e.get(key):
                cb.setEditText(e[key])
        self._sync_security()
        on = self._editable(row)
        for w in (self.cmd, self.log, self.same_area, self.no_priv, self.btn_edit, self.btn_del):
            w.setEnabled(on)
        self.btn_add.setEnabled(self._enabled)
        wiz = getattr(self.cmd, "wizard", None)
        if wiz is not None:
            wiz.setEnabled(on)
        self._check()

    def _sync_security(self, *_a) -> None:
        """As in CtDraw: a ticked box shows the page's
        area ('<All areas>' when it has none) / '<None>' in the disabled menu."""
        have = self._editable(self._cur)
        if self.same_area.isChecked():
            self.area.setEditText(self._page_area() or ALL_AREAS_TEXT)
        elif self.area.currentText() in (ALL_AREAS_TEXT, "") or self.area.currentText() == self._page_area():
            e = self._entries[self._cur] if 0 <= self._cur < len(self._entries) else {}
            self.area.setEditText(e.get("AREA") or self.area.itemText(0))
        self.area.setEnabled(have and not self.same_area.isChecked())
        if self.no_priv.isChecked():
            self.priv.setEditText(NONE_TEXT)
        elif self.priv.currentText() in (NONE_TEXT, ""):
            e = self._entries[self._cur] if 0 <= self._cur < len(self._entries) else {}
            self.priv.setEditText(e.get("PRIV") or self.priv.itemText(0))
        self.priv.setEnabled(have and not self.no_priv.isChecked())

    def _editable(self, row: int) -> bool:
        """Entry ``row`` can be edited: a page keyboard command needs the
        project database; a page-wide keyboard RECORD is part of the page
        itself, editable anywhere."""
        if not 0 <= row < len(self._entries):
            return False
        return self._enabled or self._entries[row].get("_record") is not None

    def _others(self, skip: int = -1) -> List[str]:
        return [e.get("KEY_SEQ", "") for i, e in enumerate(self._entries) if i != skip]

    def _check(self, *_a) -> None:
        self.edited.emit()
        if not 0 <= self._cur < len(self._entries):
            self.advice.setText("")
            self.advice.setVisible(False)
            return
        seq = self._entries[self._cur].get("KEY_SEQ", "")
        msgs = key_sequence_warnings(seq, self._others(self._cur), "page") + command_argument_warnings(
            seq, self.cmd.value()
        )
        self.advice.setText("\n".join(msgs))
        self.advice.setVisible(bool(msgs))

    def _add(self) -> None:
        seq = KeySequenceDialog.get_sequence(self, self._doc, "", self._others(), "page")
        if seq:
            self.add_key_sequence(seq)

    def add_key_sequence(self, seq: str, command: str = "", **fields) -> None:
        """Append a key sequence (the Add button's effect) and select it."""
        self._store()
        e = {"KEY_SEQ": seq, "CMD": command, "AREA": "", "PRIV": "", "MSG_LOG": "", "_record": None}
        e.update({k.upper(): v for k, v in fields.items()})
        self._entries.append(e)
        self._fill(len(self._entries) - 1)

    def _edit(self) -> None:
        row = self.key_list.currentRow()
        if not self._editable(row):
            return
        seq = KeySequenceDialog.get_sequence(
            self, self._doc, self._entries[row].get("KEY_SEQ", ""), self._others(row), "page"
        )
        if seq:
            self._store()
            self._entries[row]["KEY_SEQ"] = seq
            self._fill(row)

    def _delete(self) -> None:
        row = self.key_list.currentRow()
        if self._editable(row):
            self._cur = -1  # do not store into the removed entry
            del self._entries[row]
            self._fill(row)

    def clear(self) -> None:
        """Clear Property: remove every keyboard command (CtDraw's case
        0x1324 deletes all the list's entries)."""
        self._cur = -1
        self._entries = []
        self._fill(0)

    # -- result -----------------------------------------------------------------
    def entries(self) -> List[dict]:
        self._store()
        return [dict(e) for e in self._entries]

    def page_rows(self) -> List[dict]:
        """The pgkey rows (blank AN) the tab stands for, in order."""
        return [
            {k: v for k, v in e.items() if k != "_record"} for e in self.entries() if e.get("_record") is None
        ]

    def changed(self) -> bool:
        cur = self.entries()
        if len(cur) != len(self._entries0):
            return True
        for a, b in zip(cur, self._entries0):
            if a.get("_record") != b.get("_record"):
                return True
            if any((a.get(k) or "") != (b.get(k) or "") for k in self.FIELDS):
                return True
        return False

    def rows_changed(self) -> bool:
        def norm(es):
            return [{k: (e.get(k) or "") for k in self.FIELDS} for e in es if e.get("_record") is None]

        return norm(self.entries()) != norm(self._entries0)

    def problem(self) -> str:
        """The first entry that cannot be stored ('' = none)."""
        for e in self.entries():
            err = self._doc.page_keyboard_problem(e)
            if err:
                return err
        return ""


class PageEventsTab(QtWidgets.QWidget):
    """Page Properties > Events: the six events (a check mark = has a
    command) and the selected event's command (as in CtDraw)."""

    #: a command changed (the dialog checks the input)
    edited = QtCore.Signal()

    def __init__(self, values: Dict[str, str], context=None, parent=None, enabled=True):
        super().__init__(parent)
        self._values = {col: values.get(col, "") or "" for _l, col in _EVENT_FIELDS}
        self._values0 = dict(self._values)
        self._enabled = enabled
        self._cur = -1
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Event"))
        self.events = QtWidgets.QListWidget()
        self.events.setMaximumWidth(190)
        for label, col in _EVENT_FIELDS:
            it = QtWidgets.QListWidgetItem(label.rstrip(":"))
            it.setData(QtCore.Qt.UserRole, col)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setToolTip("The command %s." % _EVENT_WHEN[col])
            self.events.addItem(it)
        left.addWidget(self.events, 1)
        lay.addLayout(left)
        right = QtWidgets.QVBoxLayout()
        self.cmd_group = QtWidgets.QGroupBox("Command")
        cg = QtWidgets.QVBoxLayout(self.cmd_group)
        self.cmd = CommandEdit("", limit=_EVENT_MAX, min_lines=3)
        cg.addWidget(self.cmd, 1)
        row = QtWidgets.QHBoxLayout()
        if context is not None:
            from ..objprops.expression import wizard_button

            self.cmd.wizard = wizard_button(self.cmd, context)
            self.cmd.wizard.setAutoRaise(True)
            row.addWidget(self.cmd.wizard)
        row.addStretch(1)
        row.addWidget(self.cmd.counter)
        cg.addLayout(row)
        self.advice = _theme.note_label("", "warn")
        cg.addWidget(self.advice)
        right.addWidget(self.cmd_group, 1)
        crow = QtWidgets.QHBoxLayout()
        crow.addStretch(1)
        self.btn_clear = QtWidgets.QPushButton("Clear Property")
        self.btn_clear.setToolTip("Remove the commands of every event")
        crow.addWidget(self.btn_clear)
        right.addLayout(crow)
        lay.addLayout(right, 1)

        self.events.currentRowChanged.connect(self._on_row)
        self.events.itemChanged.connect(self._on_check)
        self.cmd.textChanged.connect(self._on_text)
        self.btn_clear.clicked.connect(self.clear)
        self._sync_checks()
        self.events.setCurrentRow(0)
        if not enabled:
            for w in (self.cmd, self.btn_clear, self.events):
                w.setEnabled(False)

    def _col(self, row: int) -> Optional[str]:
        it = self.events.item(row)
        return it.data(QtCore.Qt.UserRole) if it is not None else None

    def _sync_checks(self) -> None:
        self.events.blockSignals(True)
        for r in range(self.events.count()):
            col = self._col(r)
            self.events.item(r).setCheckState(
                QtCore.Qt.Checked if self._values.get(col, "").strip() else QtCore.Qt.Unchecked
            )
        self.events.blockSignals(False)

    def _store(self) -> None:
        col = self._col(self._cur)
        if col is not None:
            self._values[col] = self.cmd.value()

    def _on_row(self, row: int) -> None:
        self._store()
        self._cur = row
        col = self._col(row)
        label = self.events.item(row).text() if col else ""
        self.cmd_group.setTitle("%s command" % label if label else "Command")
        self.cmd.blockSignals(True)
        self.cmd.set_value(self._values.get(col, "") if col else "")
        self.cmd.blockSignals(False)
        self.cmd._update_counter()
        self._advise()

    def _on_text(self) -> None:
        self._store()
        self._sync_checks()
        self._advise()

    def _on_check(self, item) -> None:
        """Unticking an event clears its command (CtDraw case 4000 / 6)."""
        col = item.data(QtCore.Qt.UserRole)
        if item.checkState() == QtCore.Qt.Unchecked and self._values.get(col):
            self._values[col] = ""
            if col == self._col(self._cur):
                self.cmd.blockSignals(True)
                self.cmd.set_value("")
                self.cmd.blockSignals(False)
                self.cmd._update_counter()
        self._sync_checks()
        self._advise()

    def _advise(self) -> None:
        col = self._col(self._cur)
        msgs = event_warnings(col, self._values.get(col, "")) if col else []
        self.advice.setText("\n".join(msgs))
        self.advice.setVisible(bool(msgs))
        self.edited.emit()

    def select(self, column: str) -> None:
        for r in range(self.events.count()):
            if self._col(r) == column:
                self.events.setCurrentRow(r)

    def set_command(self, column: str, command: str) -> None:
        """Type ``command`` for ``column`` (what the editor does)."""
        self.select(column)
        self.cmd.setPlainText(command)

    def clear(self) -> None:
        """Clear Property: every event loses its command (CtDraw
        clears all six)."""
        for k in self._values:
            self._values[k] = ""
        self.cmd.blockSignals(True)
        self.cmd.set_value("")
        self.cmd.blockSignals(False)
        self.cmd._update_counter()
        self._sync_checks()
        self._advise()

    def values(self) -> Dict[str, str]:
        self._store()
        return dict(self._values)

    def rebase(self, values: Dict[str, str]) -> None:
        """After Apply: the stored values are the new baseline."""
        self._values = {col: values.get(col, "") or "" for _l, col in _EVENT_FIELDS}
        self._values0 = dict(self._values)
        self._on_row(self._cur if self._cur >= 0 else 0)
        self._sync_checks()

    def reload_columns(self, values: Dict[str, str], columns) -> None:
        """The page changed underneath: ``columns`` (not typed here) show
        ``values`` and take them as their baseline."""
        self._store()
        for col in columns:
            v = values.get(col, "") or ""
            self._values[col] = v
            self._values0[col] = v
        row, self._cur = self._cur, -1  # redraw without storing the old text
        self._on_row(row if row >= 0 else 0)
        self._sync_checks()


class _AssociationNameDelegate(QtWidgets.QStyledItemDelegate):
    """The Associations tab's Name cell: an editable drop-down of the
    associations the page uses that are not listed yet (CtDraw's Name
    combo, help 1172108 -- 'Only those association names that are in-use
    ... are listed'); any other valid name can still be typed."""

    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self._dialog = dialog

    def createEditor(self, parent, option, index):
        cb = QtWidgets.QComboBox(parent)
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        cb.setMaxVisibleItems(15)
        cur = index.data() or ""
        try:
            names = self._dialog.unlisted_associations(keep=cur)
        except Exception:  # noqa: BLE001
            _note_exc("page_props.unlisted_associations")
            names = []
        cb.addItems(names)
        cb.lineEdit().setMaxLength(253)
        cb.setToolTip("Pick an association this page uses, or type a new name")
        return cb

    def setEditorData(self, editor, index):
        editor.setEditText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


def _shown_record(doc) -> dict:
    """The pages.DBF row the dialog shows: a page not registered yet shows an EMPTY title
    (as CtDraw's Page Properties does), though its registration writes the page name there
    unless a title is typed."""
    rec = dict(doc.page_record() or {})
    if not doc.page_record_registered() and "TITLE" not in doc.page_record_staged():
        rec["TITLE"] = ""
    return rec


class PagePropertiesDialog(QtWidgets.QDialog):
    """CtDraw's "<Page> Properties" dialog, tab for tab (see the module
    doc). ``project`` = the page's Project (None: no project database -- a
    library element, or a page outside a project)."""

    #: emitted after an Apply that changed the page (the window refreshes)
    applied = QtCore.Signal()
    # the lists and command boxes take the room
    RESIZE = "both"

    def __init__(self, doc, project, parent=None):
        super().__init__(parent)
        self._doc = doc
        self._project = project
        stem = getattr(doc, "_page_record_name", None)
        self._page_name = (
            (doc.page.name if project is None or stem is None else stem()) or doc.page.name or ""
        )
        # the project database fields are available (a page in a project folder)
        self._db = bool(project is not None and doc.page_record_available())
        self._widths = doc.page_record_widths() if self._db else {}
        self._rec = _shown_record(doc) if self._db else {}
        self._registered = self._db and doc.page_record_registered()
        self.setWindowTitle("%s Properties" % (self._page_name or "Page"))

        self._dbf_edits: dict = {}
        self._dirty_colour: int | None = None
        self.changed = False  # set once an apply() writes something
        self.apply_error = ""  # why the last apply() stopped ('' = it ran to the end)
        self._building = True
        self._check_timer = QtCore.QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(150)
        self._check_timer.timeout.connect(self._revalidate)
        try:
            from ..objprops.expression import ExpressionContext

            self._context = ExpressionContext.for_document(doc)
        except Exception:  # noqa: BLE001 -- the Wizard is an aid
            _note_exc("page_props.expression_context")
            self._context = None

        layout = QtWidgets.QVBoxLayout(self)
        if not self._db and not self._is_element():
            # said first, not only in a footer: nothing typed on the stored tabs is kept
            top = _theme.note_label(
                "This page is not in a project folder: its title, description, events and "
                "keyboard commands cannot be stored, and Plant SCADA does not compile it. "
                "Its size, colour and template can be changed."
            )
            _theme.set_note_role(top, "warn")
            layout.addWidget(top)
        self._tabs = FitTabWidget(self)
        layout.addWidget(self._tabs, 1)
        self._build_general()
        self._build_appearance()
        self._build_keyboard()
        self._build_events()
        self._build_environment()
        self._build_associations()
        self._take_baselines()
        # the focus on the first field (the window title), not on the tab bar
        QtCore.QTimer.singleShot(0, self, lambda: self._title.setFocus(QtCore.Qt.OtherFocusReason))

        # input problems show here (the tab they are on is named)
        self._status = _theme.note_label("", "danger")
        self._status.hide()
        layout.addWidget(self._status)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
            | QtWidgets.QDialogButtonBox.Help
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        self._btn_ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self._btn_apply = buttons.button(QtWidgets.QDialogButtonBox.Apply)
        self._btn_apply.clicked.connect(self.apply)
        buttons.button(QtWidgets.QDialogButtonBox.Help).setToolTip(
            "Open the help page of the current tab (F1)"
        )
        buttons.helpRequested.connect(self._show_help)
        layout.addWidget(buttons)
        self._building = False
        self._revalidate()

    # -- helpers -----------------------------------------------------------------
    def _dbf_value(self, col: str) -> str:
        return (self._rec or {}).get(col, "").strip()

    def _show_tab(self, page: QtWidgets.QWidget) -> None:
        """Bring the tab holding ``page`` (a tab's page, in its scroll area)
        to the front."""
        for k in range(self._tabs.count()):
            if unwrap_page(self._tabs.widget(k)) is page:
                self._tabs.setCurrentIndex(k)
                return

    def _project_dir(self) -> Optional[str]:
        if self._project is not None:
            return self._project.path
        return None

    def _chain(self, fn):
        pdir = self._project_dir()
        if not pdir:
            return ()
        try:
            return fn(pdir)
        except Exception:  # noqa: BLE001 -- the menus are aids
            _note_exc("page_props._chain", project=pdir)
            return ()

    def _width(self, col: str) -> int:
        from ...editing import PAGE_FIELD_LIMITS

        return int(self._widths.get(col) or PAGE_FIELD_LIMITS.get(col) or 0)

    def _is_element(self) -> bool:
        from ...editing import ElementDocument

        return isinstance(self._doc, ElementDocument)

    def _schedule_check(self, *_a) -> None:
        """Check the input once typing pauses (OK / Apply follow it)."""
        if not getattr(self, "_building", True):
            self._check_timer.start()

    # -- General ------------------------------------------------------------------
    def _page_field(self, col: str, system_pages=None) -> PageField:
        cb = PageField(
            self._project_dir(),
            value=self._dbf_value(col),
            exclude=self._page_name,
            system_pages=system_pages,
            limit=self._width(col),
        )
        return cb

    def _build_general(self) -> None:
        """The General tab: the page's fields and its Security, Page scan time, Logging and
        Cluster context groups."""
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        form = QtWidgets.QFormLayout()
        outer.addLayout(form)

        self._build_general_fields(form)

        grid = QtWidgets.QGridLayout()
        outer.addLayout(grid)
        self._build_security(grid)
        self._build_scan_time(grid)
        self._build_logging(grid)
        self._build_cluster(grid)
        self._note_general_store(outer)
        self._watch_general_edits()
        self._general_page = w
        self._tabs.addTab(scroll_page(w), "General")

    def _build_general_fields(self, form: QtWidgets.QFormLayout) -> None:
        """The page's fields: the window title, the description, the previous, next and
        parent pages and the content type."""
        self._title = QtWidgets.QLineEdit(self._dbf_value("TITLE"))
        # (no length cap: a longer title shows the counter in the danger colour, frames
        # the field and keeps OK / Apply off, as every input problem does)
        self._title.setToolTip(
            "The window title at runtime: plain text and super genie "
            "associations (?name?). At most %d characters." % self._width("TITLE")
        )
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(self._title, 1)
        trow.addWidget(_counter(self._title, self._width("TITLE")))
        form.addRow(_buddy("&Window title:", self._title), trow)
        self._dbf_edits["TITLE"] = self._title

        # two lines to start with; the description takes the extra height
        self._desc = CommandEdit(
            self._rec.get("COMMENT", "") if self._rec else "", limit=self._width("COMMENT"), min_lines=2
        )
        self._desc.setToolTip(
            "A note about the page. It is not shown at runtime. At most "
            "%d characters." % self._width("COMMENT")
        )
        drow = QtWidgets.QVBoxLayout()
        drow.addWidget(self._desc)
        drow.addWidget(self._desc.counter, 0, QtCore.Qt.AlignRight)
        form.addRow(_buddy("&Description:", self._desc), drow)
        self._dbf_edits["COMMENT"] = self._desc

        # Settings > List system pages (CtDraw's ListSystemPages) drives the
        # Previous / Next lists only; the Parent list has them
        for label, col, tip, system in (
            ("&Previous page:", "PREV_PAGE", "The page PagePrev() opens from this page", None),
            ("&Next page:", "NEXT_PAGE", "The page PageNext() opens from this page", None),
            ("Parent page:", "PARENT", "The parent page in the page hierarchy", True),
        ):
            cb = self._page_field(col, system)
            cb.setToolTip(tip)
            self._dbf_edits[col] = cb
            form.addRow(label, cb)
        self._cb_contype = content_type_combo(
            self._project_dir(), self._dbf_value("CON_TYPE"), self._width("CON_TYPE")
        )
        form.addRow("Content type:", self._cb_contype)
        self._dbf_edits["CON_TYPE"] = self._cb_contype

    def _build_security(self, grid: QtWidgets.QGridLayout) -> None:
        """The Security group: all areas or the page's area."""
        # Security
        sec = QtWidgets.QGroupBox("Security")
        sf = QtWidgets.QGridLayout(sec)
        self._chk_all_areas = QtWidgets.QCheckBox("All areas")
        self._chk_all_areas.setToolTip("The page belongs to every area")
        self._cb_area = _combo(_AREAS, limit=self._width("AREA"))
        self._cb_area.setMinimumWidth(130)  # room for '<All areas>'
        self._cb_area.setToolTip(
            "Only operators with access to this area can see the page "
            "(1 to 255, or an area name; at most 16 characters)"
        )
        sf.addWidget(self._chk_all_areas, 0, 0, 1, 2)
        sf.addWidget(_buddy("&Area:", self._cb_area), 1, 0)
        sf.addWidget(self._cb_area, 1, 1)
        area = self._dbf_value("AREA")
        self._chk_all_areas.setChecked(not area)
        self._cb_area.setEditText(area or ALL_AREAS_TEXT)
        self._cb_area.setEnabled(bool(area))
        self._chk_all_areas.toggled.connect(self._on_all_areas)
        self._dbf_edits["AREA"] = self._cb_area
        grid.addWidget(sec, 0, 0)

    def _build_scan_time(self, grid: QtWidgets.QGridLayout) -> None:
        """The Page scan time group: the project's default or the page's own."""
        from ...io import project as P

        # Page scan time
        sc = QtWidgets.QGroupBox("Page scan time")
        scf = QtWidgets.QHBoxLayout(sc)
        self._chk_scan_default = QtWidgets.QCheckBox("Default")
        self._chk_scan_default.setToolTip(
            "Use the project's default page scan time (now %s ms)" % P.default_scan_time()
        )
        self._sp_scan = QtWidgets.QSpinBox()
        self._sp_scan.setRange(0, 60000)
        self._sp_scan.setSingleStep(50)
        self._sp_scan.setSuffix(" ms")
        self._sp_scan.setToolTip(
            "How often the page is updated and its While page shown command runs (at most 60000 ms)"
        )
        scan = self._dbf_value("SCANTIME")
        self._scan0 = scan
        self._chk_scan_default.setChecked(not scan)
        try:
            self._sp_scan.setValue(int(scan or P.default_scan_time()))
        except ValueError:
            self._sp_scan.setValue(int(P.default_scan_time()))
        self._sp_scan.setEnabled(bool(scan))
        self._chk_scan_default.toggled.connect(self._on_scan_default)
        scf.addWidget(self._chk_scan_default)
        scf.addWidget(self._sp_scan)
        scf.addStretch(1)
        grid.addWidget(sc, 0, 1)

    def _build_logging(self, grid: QtWidgets.QGridLayout) -> None:
        """The Logging group: the log device of the page's keyboard commands."""
        from ...io import project as P

        # Logging
        lg = QtWidgets.QGroupBox("Logging")
        lgf = QtWidgets.QFormLayout(lg)
        devices = self._chain(P.devices)
        self._devices = {r["NAME"].lower(): r for r in devices}
        self._cb_logdevice = _combo(
            [NONE_TEXT] + [r["NAME"] for r in devices], limit=self._width("LOGDEVICE")
        )
        self._cb_logdevice.setEditText(self._dbf_value("LOGDEVICE") or NONE_TEXT)
        self._cb_logdevice.setToolTip("The device the log messages of the page's keyboard commands go to")
        lgf.addRow("&Log device:", self._cb_logdevice)
        self._logdevice_note = _theme.note_label("", "warn")
        lgf.addRow(self._logdevice_note)
        self._cb_logdevice.currentTextChanged.connect(self._check_logdevice)
        self._check_logdevice()
        self._dbf_edits["LOGDEVICE"] = self._cb_logdevice
        grid.addWidget(lg, 1, 0)

    def _build_cluster(self, grid: QtWidgets.QGridLayout) -> None:
        """The Cluster context group: inherit from the caller or the page's cluster."""
        from ...io import project as P

        # Cluster context
        cl = QtWidgets.QGroupBox("Cluster context")
        clf = QtWidgets.QGridLayout(cl)
        self._chk_inherit = QtWidgets.QCheckBox("Inherit from caller")
        self._chk_inherit.setToolTip("The page takes its cluster from the page or Cicode that opens it")
        self._cb_cluster = _combo(
            [""] + [r["NAME"] for r in self._chain(P.clusters)], limit=self._width("CLUSTER")
        )
        self._cb_cluster.setToolTip("The page's default cluster")
        inherit = self._dbf_value("CINHERIT").upper() == "TRUE"
        self._chk_inherit.setChecked(inherit)
        self._cb_cluster.setEditText("" if inherit else self._dbf_value("CLUSTER"))
        self._cb_cluster.setEnabled(not inherit)
        self._chk_inherit.toggled.connect(self._on_inherit)
        clf.addWidget(self._chk_inherit, 0, 0, 1, 2)
        clf.addWidget(_buddy("&Cluster:", self._cb_cluster), 1, 0)
        clf.addWidget(self._cb_cluster, 1, 1)
        clf.setColumnStretch(1, 1)  # the field takes the extra width
        self._dbf_edits["CLUSTER"] = self._cb_cluster
        grid.addWidget(cl, 1, 1)

    def _note_general_store(self, outer: QtWidgets.QVBoxLayout) -> None:
        """Where these properties are stored, and the fields off where they cannot be (no
        project, or a project table without the column)."""
        if not self._db:
            outer.addWidget(
                _theme.note_label(
                    "A library element has no page record in a project, so these properties "
                    "do not apply to it."
                    if self._is_element()
                    else "This page is not in a project folder, so these properties cannot be stored."
                )
            )
            for wdg in list(self._dbf_edits.values()) + [
                self._chk_all_areas,
                self._chk_scan_default,
                self._sp_scan,
                self._chk_inherit,
            ]:
                wdg.setEnabled(False)
        else:
            # an older project table without a column: that field cannot be
            # stored (every shipped pages.DBF has them all)
            from ...editing import PAGE_FIELD_LABELS

            for col, wdgs in (
                ("CON_TYPE", [self._cb_contype]),
                ("PARENT", [self._dbf_edits["PARENT"]]),
                ("CINHERIT", [self._chk_inherit]),
            ):
                if self._widths and col not in self._widths:
                    for wdg in wdgs:
                        wdg.setEnabled(False)
                        wdg.setToolTip(
                            "This project cannot store the %s." % PAGE_FIELD_LABELS.get(col, col.lower())
                        )
            outer.addWidget(
                _theme.note_label(
                    "These properties are stored in the project when you save the page."
                    if self._registered
                    else "This page is not in project %s yet. Saving it adds it with these "
                    "properties." % os.path.basename(self._project_dir() or "")
                )
            )

    def _watch_general_edits(self) -> None:
        """An edit of any field checks the page's values after a pause."""
        # (no stretch below: the description takes the extra height)
        for wdg in (
            self._title,
            self._desc,
            self._cb_contype,
            self._cb_area,
            self._cb_logdevice,
            self._cb_cluster,
            self._dbf_edits["PREV_PAGE"],
            self._dbf_edits["NEXT_PAGE"],
            self._dbf_edits["PARENT"],
        ):
            sig = getattr(wdg, "editTextChanged", None) or wdg.textChanged
            sig.connect(self._schedule_check)
        self._sp_scan.valueChanged.connect(self._schedule_check)
        for chk in (self._chk_all_areas, self._chk_scan_default, self._chk_inherit):
            chk.toggled.connect(self._schedule_check)

    def _on_all_areas(self, on: bool) -> None:
        """As in CtDraw: ticked shows '<All areas>' in the disabled menu;
        unticked gives it the stored area, else the menu's first entry."""
        if on:
            self._cb_area.setEditText(ALL_AREAS_TEXT)
        else:
            self._cb_area.setEditText(self._dbf_value("AREA") or self._cb_area.itemText(0))
        self._cb_area.setEnabled(not on and self._db)
        if hasattr(self, "_keys"):
            self._keys._sync_security()

    def _on_scan_default(self, on: bool) -> None:
        from ...io import project as P

        if on:
            self._sp_scan.setValue(int(P.default_scan_time()))
        elif self._scan0:
            self._sp_scan.setValue(int(self._scan0) if is_digits(self._scan0) else 0)
        self._sp_scan.setEnabled(not on and self._db)

    def _on_inherit(self, on: bool) -> None:
        if on:
            self._cb_cluster.setEditText("")
        else:
            self._cb_cluster.setEditText(self._dbf_value("CLUSTER"))
        self._cb_cluster.setEnabled(not on and self._db)

    def _check_logdevice(self, *_a) -> None:
        name = _combo_value(self._cb_logdevice)
        row = self._devices.get(name.lower()) if name else None
        msg = ""
        if name and row is None and self._devices:
            msg = "There is no device named %s in this project or its included projects." % name
        elif row is not None:
            from ...io.project import device_logs_messages

            if not device_logs_messages(row):
                msg = (
                    "The format of device %s has no {MsgLog} field, so the log messages "
                    "are not written." % row["NAME"]
                )
        self._logdevice_note.setText(msg)
        self._logdevice_note.setVisible(bool(msg))

    def _page_area(self) -> str:
        """The page's area as the General tab has it now ('' = all areas)."""
        if self._chk_all_areas.isChecked():
            return ""
        return _combo_value(self._cb_area)

    def _record_values(self) -> dict:
        """The pages.DBF values the General / Appearance / Events tabs show,
        the way CtDraw's apply derives them."""
        vals = {
            "TITLE": self._title.text(),
            "COMMENT": self._desc.value(),
            "PREV_PAGE": self._dbf_edits["PREV_PAGE"].value(),
            "NEXT_PAGE": self._dbf_edits["NEXT_PAGE"].value(),
            "PARENT": self._dbf_edits["PARENT"].value(),
            "CON_TYPE": _combo_value(self._cb_contype),
            "AREA": self._page_area(),
            "LOGDEVICE": _combo_value(self._cb_logdevice),
            "SCANTIME": "" if self._chk_scan_default.isChecked() else str(self._sp_scan.value()),
            "CLUSTER": "" if self._chk_inherit.isChecked() else _combo_value(self._cb_cluster),
            "CINHERIT": "TRUE" if self._chk_inherit.isChecked() else "",
        }
        vals.update(self._events.values())
        q = self._cb_quality.currentData()
        if q is not None:
            vals["IGNOREVQ"] = q
        return vals

    def _record_updates(self) -> dict:
        """The pages.DBF columns this Apply changes."""
        if not self._db:
            return {}
        multiline = {"COMMENT"} | {c for _l, c in _EVENT_FIELDS}
        out = {}
        for col, val in self._record_values().items():
            old = self._dbf_value(col)
            if col == "IGNOREVQ":
                if val == (old or "-1"):  # blank and -1 both mean the default
                    continue
            elif col == "CINHERIT":
                if (val == "TRUE") == (old.upper() == "TRUE"):
                    continue
            elif col == "SCANTIME":
                if val == old or (val and is_digits(old) and int(val) == int(old)):
                    continue
            if val.strip() == old:
                continue
            out[col] = val if col in multiline else val.strip()
        return out

    # -- Appearance ---------------------------------------------------------------
    def _page_colour(self):
        c = self._doc.page.appearance.background_colour
        return None if c is None else int(c)

    def _build_appearance(self) -> None:
        from ..colour_picker import ColourButton

        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)

        # ---- Template ----------------------------------------------------------
        tgrp = QtWidgets.QGroupBox("Template")
        tl = QtWidgets.QVBoxLayout(tgrp)
        self._tpl_index, ref = self._doc.template_reference()
        self._tpl_picker = None
        if self._tpl_index is not None:
            pdir = self._project_dir() or (
                os.path.dirname(os.path.abspath(self._doc.path)) if self._doc.path else ""
            )
            self._tpl_picker = TemplatePicker(pdir, tgrp, background=self._page_colour())
            if ref is not None:
                self._tpl_picker.set_current(ref.library, ref.element)
            tl.addWidget(self._tpl_picker, 1)
            self._res_note = _theme.note_label(
                "A User Defined template has no fixed size: the page is as large as its view area."
            )
            tl.addWidget(self._res_note)
            self._tpl_picker.changed.connect(self._sync_res_note)
            self._tpl_picker.changed.connect(self._schedule_check)
            self._sync_res_note()
        if self._tpl_picker is not None:
            # (the picker takes the room)
            outer.addWidget(tgrp, 1)
        else:
            # (no template: one line, not a tall empty box)
            tgrp.deleteLater()
            outer.addWidget(
                _theme.note_label(
                    "A library element has no template."
                    if self._is_element()
                    else "This page has no template."
                )
            )

        # ---- View area, page colour, Ignore Quality: together, at their own height -----
        vgrp = QtWidgets.QGroupBox("Page")
        vform = QtWidgets.QFormLayout(vgrp)
        from ..number_field import NumberField

        self._sp_w = NumberField(lo=16, hi=32767, suffix=" px")
        self._sp_h = NumberField(lo=16, hi=32767, suffix=" px")
        self._sp_w.setValue(int(self._doc.page.appearance.width or 0))
        self._sp_h.setValue(int(self._doc.page.appearance.height or 0))
        self._sp_w.setToolTip("The width of the page at runtime")
        self._sp_h.setToolTip("The height of the page at runtime")
        self._sp_w.valueChanged.connect(lambda v: self._clamp_view(self._sp_h, v))
        self._sp_h.valueChanged.connect(lambda v: self._clamp_view(self._sp_w, v))
        vform.addRow("&Width:", self._sp_w)
        vform.addRow("&Height:", self._sp_h)
        bform = vform
        bg = self._page_colour()
        self._btn_colour = ColourButton(
            None if bg is None else bg & 0xFFFFFF, title="Page Colour", flash=False, transparent=False
        )
        self._btn_colour.changed.connect(self._colour_changed)
        bform.addRow("Page colour:", self._btn_colour)
        self._cb_quality = QtWidgets.QComboBox()
        cur_q = self._dbf_value("IGNOREVQ")
        for label, value in _IGNORE_QUALITY:
            self._cb_quality.addItem(label, value)
        idx = self._cb_quality.findData(cur_q or "-1")
        if idx < 0:
            # a value CtDraw does not offer: shown as is, so an untouched
            # combo does not rewrite it
            self._cb_quality.addItem("Value %s" % cur_q, cur_q)
            idx = self._cb_quality.count() - 1
        self._cb_quality.setCurrentIndex(idx)
        self._cb_quality.setToolTip("How the page shows values of bad quality")
        if not self._db:
            self._cb_quality.setEnabled(False)
        bform.addRow("Ignore quality:", self._cb_quality)
        outer.addWidget(vgrp)
        if self._tpl_picker is None:
            outer.addStretch(1)
        self._appearance_page = w
        self._tabs.addTab(scroll_page(w), "Appearance")

    def _clamp_view(self, other: QtWidgets.QSpinBox, v: int) -> None:
        """CtDraw keeps width x height at most 2**24: typing one dimension
        shrinks the other."""
        if v and v * other.value() > _VIEW_AREA_MAX:
            other.blockSignals(True)
            other.setValue(max(other.minimum(), _VIEW_AREA_MAX // v))
            other.blockSignals(False)

    def _sync_res_note(self) -> None:
        p = self._tpl_picker
        if p is None:
            return
        from ...format.template_place import split_element

        res = split_element(p.element_name())[1] if p.element_name() else p.resolution()
        self._res_note.setVisible(str(res).lower() == "user")

    def _colour_changed(self) -> None:
        pair = self._btn_colour.pair()
        if pair is None:
            return
        self._dirty_colour = pair[0] & 0xFFFFFF
        if self._tpl_picker is not None:
            self._tpl_picker.set_background(self._dirty_colour)

    # -- Keyboard Commands ----------------------------------------------------------
    def _build_keyboard(self) -> None:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        keys_ok = self._db and self._doc.page_keyboard_available()
        self._keys = PageKeyboardTab(self._doc, self._context, self._page_area, enabled=keys_ok)
        self._keys.edited.connect(self._schedule_check)
        lay.addWidget(self._keys, 1)
        if not self._db:
            lay.addWidget(
                _theme.note_label(
                    "A library element's keyboard commands are not edited here."
                    if self._is_element()
                    else "Page keyboard commands are stored in the project, and this page is not "
                    "in a project folder."
                )
            )
        elif not keys_ok:
            lay.addWidget(
                _theme.note_label(
                    "Project %s cannot store page keyboard commands."
                    % os.path.basename(self._project_dir() or "")
                )
            )
        else:
            lay.addWidget(
                _theme.note_label(
                    "A page keyboard command runs when its key sequence is typed anywhere on the page."
                )
            )
        self._keys_page = w
        self._tabs.addTab(scroll_page(w), "Keyboard Commands")

    # -- Events ---------------------------------------------------------------------
    def _build_events(self) -> None:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        vals = {col: self._rec.get(col, "") if self._rec else "" for _l, col in _EVENT_FIELDS}
        self._events = PageEventsTab(vals, self._context, enabled=self._db)
        self._events.edited.connect(self._schedule_check)
        lay.addWidget(self._events, 1)
        self._events_page = w
        self._tabs.addTab(scroll_page(w), "Events")

    # -- Environment ----------------------------------------------------------------
    def _environment(self) -> dict:
        """Environment tab = attributes list 1 without the keys that start
        with '!' (CtDraw lists exactly those, its own '!*'
        state keys; PageCicodeOn IS listed) -- the associations are list 2."""
        return {k: v for k, v in self._doc.page_properties().items() if not k.startswith("!")}

    @staticmethod
    def _edit_table(columns, tips=()) -> QtWidgets.QTableWidget:
        t = QtWidgets.QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(list(columns))
        for c, tip in enumerate(tips):
            t.horizontalHeaderItem(c).setToolTip(tip)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        return t

    @staticmethod
    def _list_buttons(owner: str):
        """Add / Edit / Remove for a table edited in place."""
        add = QtWidgets.QPushButton("Add")
        edit = QtWidgets.QPushButton("Edit")
        rem = QtWidgets.QPushButton("Remove")
        add.setToolTip("Add a row and type its name")
        edit.setToolTip("Edit the selected cell")
        rem.setToolTip("Remove the selected %s" % owner)
        return add, edit, rem

    def _build_environment(self) -> None:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(
            QtWidgets.QLabel("Variables the page's Cicode reads with DspGetEnv(). Names are case-sensitive.")
        )
        self._env_table = self._edit_table(
            ["Name", "Value"], ["The variable's name, as DspGetEnv() reads it", "Its value"]
        )
        self._env_table.setColumnWidth(0, 220)
        from ..tables import make_resizable

        make_resizable(self._env_table, "PagePropertiesDialog/environment")
        lay.addWidget(self._env_table, 1)
        btns = QtWidgets.QHBoxLayout()
        self._btn_env_add, self._btn_env_edit, self._btn_env_del = self._list_buttons("variables")
        self._btn_env_add.clicked.connect(self._add_env)
        self._btn_env_edit.clicked.connect(self._edit_env)
        self._btn_env_del.clicked.connect(self._remove_env)
        for b in (self._btn_env_add, self._btn_env_edit, self._btn_env_del):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self._env_page = w
        self._fill_environment()
        self._env_table.itemChanged.connect(lambda _it: self._env_edited())
        self._env_table.itemSelectionChanged.connect(self._sync_env_buttons)
        self._sync_env_buttons()
        self._tabs.addTab(scroll_page(w), "Environment")

    def _fill_environment(self) -> None:
        """(Re)load the table from the document; each row remembers the
        stored name and value it came from."""
        self._env_original = self._environment()
        t = self._env_table
        t.blockSignals(True)
        t.setRowCount(0)
        for k, v in sorted(self._env_original.items()):
            self._insert_env_row(k, v, stored=True)
        t.blockSignals(False)

    def _insert_env_row(self, name: str = "", value: str = "", stored: bool = False) -> int:
        t = self._env_table
        r = t.rowCount()
        t.insertRow(r)
        a, b = QtWidgets.QTableWidgetItem(name), QtWidgets.QTableWidgetItem(value)
        a.setData(QtCore.Qt.UserRole, name if stored else None)
        b.setData(QtCore.Qt.UserRole, value if stored else None)
        t.setItem(r, 0, a)
        t.setItem(r, 1, b)
        return r

    def _env_rows(self) -> list:
        out = []
        for r in range(self._env_table.rowCount()):
            k_item = self._env_table.item(r, 0)
            v_item = self._env_table.item(r, 1)
            out.append((k_item.text().strip() if k_item else "", v_item.text() if v_item else ""))
        return out

    def set_environment_variable(self, name: str, value: str) -> None:
        """Add (or change) a variable in the table (Add of an existing name
        replaces its value, like CtDraw)."""
        t = self._env_table
        for r in range(t.rowCount()):
            it = t.item(r, 0)
            if it is not None and it.text() == name:
                t.item(r, 1).setText(value)
                t.setCurrentCell(r, 0)
                return
        t.blockSignals(True)
        r = self._insert_env_row(name, value)
        t.blockSignals(False)
        t.setCurrentCell(r, 0)
        self._env_edited()

    def _add_env(self) -> None:
        t = self._env_table
        t.blockSignals(True)
        r = self._insert_env_row()
        t.blockSignals(False)
        t.setCurrentCell(r, 0)
        t.editItem(t.item(r, 0))
        self._env_edited()

    def _edit_env(self) -> None:
        t = self._env_table
        r = t.currentRow()
        if r >= 0:
            t.editItem(t.item(r, max(0, t.currentColumn())))

    def _remove_env(self) -> None:
        t = self._env_table
        rows = sorted(
            {i.row() for i in t.selectedIndexes()} or ({t.currentRow()} if t.currentRow() >= 0 else set()),
            reverse=True,
        )
        for r in rows:
            t.removeRow(r)
        self._env_edited()

    def _env_edited(self) -> None:
        self._check_environment()
        self._sync_env_buttons()
        self._schedule_check()

    def _sync_env_buttons(self) -> None:
        has = self._env_table.currentRow() >= 0 and self._env_table.rowCount() > 0
        self._btn_env_edit.setEnabled(has)
        self._btn_env_del.setEnabled(has)

    def _check_environment(self) -> str:
        """Mark invalid / duplicate names (red, with the reason as a tool
        tip); the first problem ('' = none). An edited value is held to
        CtDraw's 127 characters."""
        t = self._env_table
        first = ""
        seen = set()
        t.blockSignals(True)
        for r in range(t.rowCount()):
            a, b = t.item(r, 0), t.item(r, 1)
            if a is None or b is None:
                continue
            name, value = a.text().strip(), b.text()
            err = ""
            if name or value:
                err = environment_name_problem(name)
                if not err and name in seen:
                    err = "There is already a variable named '%s'." % name
                seen.add(name)
            verr = ""
            if len(value) > _ENV_MAX and value != b.data(QtCore.Qt.UserRole):
                verr = "A value can be at most %d characters long." % _ENV_MAX
            for it, e in ((a, err), (b, verr)):
                it.setData(QtCore.Qt.ForegroundRole, QtGui.QColor(_theme.COLORS["danger"]) if e else None)
                it.setToolTip(e)
            if (err or verr) and not first:
                first = "Row %d: %s" % (r + 1, err or verr)
        t.blockSignals(False)
        return first

    # -- Associations ---------------------------------------------------------------
    def _build_associations(self) -> None:
        """Every association of the page (attributes list 2, stored order):
        Name / Default / Value on Error / Description, edited in place
        (double-click a cell, or Edit), Add and Remove; names are checked as
        you type."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(
            QtWidgets.QLabel(
                "Defaults for the super genie associations (?name?) "
                "of this page. Double-click a cell to edit it."
            )
        )
        # the names the page's objects and keyboard commands use (In Use)
        try:
            self._assoc_used = dict(self._doc.associations_in_use())
        except Exception:  # noqa: BLE001 -- a hint column only
            _note_exc("page_props._build_associations")
            self._assoc_used = {}
        t = self._assoc_table = self._edit_table(_ASSOC_COLUMNS + ["In Use"], _ASSOC_TIPS)
        t.horizontalHeaderItem(_ASSOC_IN_USE).setToolTip(
            "Yes when an object or keyboard command of this page uses the association"
        )
        for c, width in enumerate((160, 110, 110)):
            t.setColumnWidth(c, width)
        t.setColumnWidth(_ASSOC_IN_USE, 60)
        from ..tables import make_resizable

        make_resizable(t, "PagePropertiesDialog/associations", stretch=3)
        t.setItemDelegateForColumn(0, _AssociationNameDelegate(self, t))
        lay.addWidget(t, 1)
        btns = QtWidgets.QHBoxLayout()
        self._btn_assoc_add, self._btn_assoc_edit, self._btn_assoc_del = self._list_buttons("associations")
        self._btn_assoc_add.clicked.connect(self._add_association)
        self._btn_assoc_edit.clicked.connect(self._edit_association)
        self._btn_assoc_del.clicked.connect(self._delete_associations)
        for b in (self._btn_assoc_add, self._btn_assoc_edit, self._btn_assoc_del):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self._assoc_page = w
        self._fill_associations()
        t.itemChanged.connect(lambda _item: self._assoc_edited())
        t.itemSelectionChanged.connect(self._sync_assoc_buttons)
        self._sync_assoc_buttons()
        self._tabs.addTab(scroll_page(w), "Associations")

    def _assoc_edited(self) -> None:
        self._check_associations()
        self._schedule_check()

    def _fill_associations(self) -> None:
        """(Re)load the table from the document; each name cell remembers the
        stored value it came from, so an untouched row is written back
        byte-identical."""
        t = self._assoc_table
        t.blockSignals(True)
        t.setRowCount(0)
        at = self._doc.parsed.attributes
        for name, raw in list(at.list2.pairs) if at is not None else []:
            self._insert_association_row(name, raw)
        t.blockSignals(False)
        self._check_associations()

    def _insert_association_row(self, name: str = "", raw=None) -> int:
        from ...format.attributes import split_association

        t = self._assoc_table
        r = t.rowCount()
        t.insertRow(r)
        cells = (name,) + split_association(raw or "")
        for c, text in enumerate(cells):
            it = QtWidgets.QTableWidgetItem(text)
            if c == 0:
                it.setData(QtCore.Qt.UserRole, raw)  # None = a new row
                it.setData(QtCore.Qt.UserRole + 1, name)
            t.setItem(r, c, it)
        use = QtWidgets.QTableWidgetItem(self._in_use_text(name))
        use.setFlags(use.flags() & ~QtCore.Qt.ItemIsEditable)
        t.setItem(r, _ASSOC_IN_USE, use)
        return r

    def _in_use_text(self, name: str) -> str:
        name = (name or "").strip()
        if not name:
            return ""
        return "Yes" if name.lower() in getattr(self, "_assoc_used", {}) else "No"

    def unlisted_associations(self, keep: str = "") -> List[str]:
        """The associations the page uses that no row names yet (the Name
        drop-down; ``keep`` = the row being edited, which stays offered)."""
        listed = {self._assoc_cell(r, 0).strip().lower() for r in range(self._assoc_table.rowCount())}
        listed.discard((keep or "").strip().lower())
        return [n for k, n in getattr(self, "_assoc_used", {}).items() if k not in listed]

    def _add_association(self) -> None:
        t = self._assoc_table
        t.blockSignals(True)
        r = self._insert_association_row()
        t.blockSignals(False)
        t.setCurrentCell(r, 0)
        t.editItem(t.item(r, 0))
        self._assoc_edited()

    def _edit_association(self) -> None:
        t = self._assoc_table
        r = t.currentRow()
        if r < 0:
            return
        c = max(0, t.currentColumn())
        t.editItem(t.item(r, c))

    def _delete_associations(self) -> None:
        t = self._assoc_table
        rows = sorted(
            {i.row() for i in t.selectedIndexes()} or ({t.currentRow()} if t.currentRow() >= 0 else set()),
            reverse=True,
        )
        for r in rows:
            t.removeRow(r)
        self._assoc_edited()
        self._sync_assoc_buttons()

    def _sync_assoc_buttons(self) -> None:
        has = self._assoc_table.currentRow() >= 0 and self._assoc_table.rowCount() > 0
        self._btn_assoc_edit.setEnabled(has)
        self._btn_assoc_del.setEnabled(has)

    def _assoc_cell(self, r: int, c: int) -> str:
        it = self._assoc_table.item(r, c)
        return it.text() if it is not None else ""

    def _check_associations(self) -> str:
        """Mark invalid or duplicate association names. Returns the first problem."""
        from ...format.attributes import association_name_error, split_association

        t = self._assoc_table
        red = QtGui.QColor(_theme.COLORS["danger"])
        first = ""
        seen = {}
        t.blockSignals(True)
        for r in range(t.rowCount()):
            it = t.item(r, 0)
            if it is None:
                continue
            name = it.text().strip()
            blank = not name and not any(
                self._assoc_cell(r, c).strip() for c in range(1, len(_ASSOC_COLUMNS))
            )
            raw = it.data(QtCore.Qt.UserRole)
            stored = raw is not None and name == it.data(QtCore.Qt.UserRole + 1)
            err = None
            if not blank:
                err = None if stored else association_name_error(name)
                if err is None and name.lower() in seen:
                    err = (
                        "There is already an association named '%s'. Give "
                        "this one a different name." % seen[name.lower()]
                    )
                seen.setdefault(name.lower(), name)
            it.setData(QtCore.Qt.ForegroundRole, red if err else None)
            it.setToolTip(err or "")
            use = t.item(r, _ASSOC_IN_USE)
            if use is not None:
                use.setText(self._in_use_text(name))
            if err and not first:
                first = "Row %d: %s" % (r + 1, err)
            was = split_association(raw) if raw is not None else None
            for c in range(1, len(_ASSOC_COLUMNS)):
                cell = t.item(r, c)
                if cell is None:
                    continue
                text = cell.text()
                ferr = None
                if len(text) > _ASSOC_FIELD_MAX and (was is None or text != was[c - 1]):
                    ferr = "The %s can be at most %d characters long (this one has %d)." % (
                        _ASSOC_COLUMNS[c].lower(),
                        _ASSOC_FIELD_MAX,
                        len(text),
                    )
                cell.setData(QtCore.Qt.ForegroundRole, red if ferr else None)
                cell.setToolTip(ferr or "")
                if ferr and not first:
                    first = "Row %d: %s" % (r + 1, ferr)
        t.blockSignals(False)
        return first

    def _association_pairs(self) -> list:
        """The association table as ``(name, value)`` pairs."""
        from ...format.attributes import join_association, split_association

        err = self._check_associations()
        if err:
            raise ValueError("Associations tab, " + err[:1].lower() + err[1:])
        pairs = []
        t = self._assoc_table
        for r in range(t.rowCount()):
            cells = [self._assoc_cell(r, c) for c in range(len(_ASSOC_COLUMNS))]
            name = cells[0].strip()
            if not name:
                continue
            item = t.item(r, 0)
            raw = item.data(QtCore.Qt.UserRole)
            if raw is not None and cells[0] == item.data(QtCore.Qt.UserRole + 1):
                name = cells[0]
            fields = tuple(cells[1:])
            if raw is not None and split_association(raw) == fields:
                value = raw
            else:
                value = join_association(*(" ".join(f.splitlines()) for f in fields))
            pairs.append((name, value))
        return pairs

    def _show_help(self) -> None:
        """Help button: the manual on the current tab (the dialog's F1 topic,
        ui/help.DIALOG_TOPICS)."""
        from .. import help as _help

        _help.show_help(_help.dialog_topic(self) or "page-properties", main=_help.owning_main(self))

    # -- checking ------------------------------------------------------------------
    def _problem(self):
        """The first input problem: (message, tab, focus) or ('', None, None)."""
        from ...editing import guard_text, UnencodableTextError

        # the framed field follows the fields as they are now, whichever problem is shown
        # first (a field fixed while an Associations error shows loses its frame)
        self._bad_field = None
        field_problem = None
        for col, val in self._record_updates().items():
            err = self._doc.page_record_problem(col, val)
            if err:
                self._bad_field = (self._dbf_edits.get(col), err)
                if col.endswith("_FUNC"):
                    field_problem = (err, self._events_page, (lambda c=col: self._events.select(c)))
                else:
                    field_problem = (err, self._general_page, None)
                break
        err = self._check_associations()
        if err:
            return "Associations tab, " + err[:1].lower() + err[1:], self._assoc_page, None
        err = self._check_environment()
        if err:
            return "Environment tab, " + err[:1].lower() + err[1:], self._env_page, None
        if field_problem is not None:
            return field_problem
        if self._db and self._keys._enabled and self._keys.rows_changed():
            err = self._keys.problem()
            if err:
                return "Keyboard Commands tab: %s" % err, self._keys_page, None
        try:
            guard_text(*(t for kv in self._env_rows() for t in kv))
        except UnencodableTextError as e:
            return "Environment tab: %s" % e, self._env_page, None
        try:
            guard_text(
                *(
                    self._assoc_cell(r, c)
                    for r in range(self._assoc_table.rowCount())
                    for c in range(len(_ASSOC_COLUMNS))
                )
            )
        except UnencodableTextError as e:
            return "Associations tab: %s" % e, self._assoc_page, None
        return "", None, None

    def _show_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _mark_fields(self) -> None:
        """Frame the field of the input problem (theme.mark_invalid); the others clear."""
        bad = getattr(self, "_bad_field", None)
        for w in self._dbf_edits.values():
            if isinstance(w, QtWidgets.QWidget) and (bad is None or w is not bad[0]):
                _theme.mark_invalid(w, "")
        if bad is not None and isinstance(bad[0], QtWidgets.QWidget):
            _theme.mark_invalid(bad[0], bad[1])

    def _revalidate(self) -> str:
        """Show the first input problem and enable OK / Apply accordingly."""
        msg = self._problem()[0]
        self._mark_fields()
        self._show_status(msg)
        ok = not msg
        self._btn_ok.setEnabled(ok)
        self._btn_apply.setEnabled(ok)
        return msg

    # -- apply ---------------------------------------------------------------
    def _ok(self) -> None:
        self.apply()
        if not self.apply_error:
            self.accept()

    def _refuse(self, message: str, tab=None, focus=None) -> bool:
        """apply() stops at an input problem: show it in the status line and
        the tab concerned; nothing was changed."""
        self.apply_error = message
        if tab is not None:
            self._show_tab(tab)
        if focus is not None:
            focus()
        self._mark_fields()
        self._show_status(message)
        self._btn_ok.setEnabled(False)
        self._btn_apply.setEnabled(False)
        return False

    def _write_failed(self, message: str) -> bool:
        """apply() could not write: a message box (nothing was changed)."""
        self.apply_error = message
        warn(self, "Page Properties", message)
        return False

    def _new_template_ref(self) -> Optional[str]:
        """The template reference the Appearance tab names, when it differs
        from the one the dialog loaded (None = unchanged). Never compared with
        the page as it is now: a template changed on the page meanwhile is
        not switched back."""
        p = self._tpl_picker
        if p is None or self._tpl_index is None or self._tpl0 is None:
            return None
        v = p.value()
        if v is None:
            return None
        lib, element = library_stem(v[0]), v[1]
        if (lib.lower(), element.lower()) == self._tpl0:
            return None
        return "%s.%s.<ctt" % (lib, element)

    # -- what the dialog loaded (Apply compares with it) --
    def _page_size(self) -> tuple:
        app = self._doc.page.appearance
        return int(app.width or 0), int(app.height or 0)

    def _template_key(self):
        i, ref = self._doc.template_reference()
        return (ref.library.lower(), ref.element.lower()) if ref is not None else None

    def _stored_associations(self) -> list:
        at = self._doc.parsed.attributes
        return list(at.list2.pairs) if at is not None else []

    def _take_baselines(self) -> None:
        """Remember the view area, template and associations as loaded."""
        self._size0 = self._page_size()
        self._tpl0 = self._template_key()
        self._assoc0 = self._stored_associations()

    def _size_changed(self) -> bool:
        return (self._sp_w.value(), self._sp_h.value()) != self._size0

    def _env_changed(self) -> bool:
        rows = [(k, v) for k, v in self._env_rows() if k]
        return dict(rows) != self._env_original or len(rows) != len(self._env_original)

    def _associations_changed(self) -> bool:
        try:
            return self._association_pairs() != self._assoc0
        except ValueError:
            return True  # an invalid row: typed, not stored

    def _record_touched(self) -> set:
        """The pages.DBF columns shown with a value other than the one loaded
        (a cluster and its 'Inherit from caller' go together)."""
        cols = set(self._record_updates())
        if cols & {"CLUSTER", "CINHERIT"}:
            cols |= {"CLUSTER", "CINHERIT"}
        return cols

    def has_pending_edits(self) -> bool:
        """Something typed in the dialog is not applied yet."""
        return bool(
            self._record_touched()
            or self._keys.changed()
            or self._dirty_colour is not None
            or self._size_changed()
            or self._new_template_ref()
            or self._env_changed()
            or self._associations_changed()
        )

    def reload(self) -> None:
        """The page changed underneath (an edit, Undo, Redo, Replace): show it
        as it is now in every field the user has not changed; what was typed
        stays (and Apply still compares it with what the dialog loaded)."""
        doc = self._doc
        self._building = True
        try:
            # the page record (staged in the document) and the events
            if self._db:
                touched = self._record_touched()
                new = dict(doc.page_record() or {})
                rec = dict(new)
                for col in touched:  # typed: the loaded value stays the baseline
                    rec[col] = (self._rec or {}).get(col, "")
                self._rec = rec
                self._show_record(set(self._record_values()) - touched)
                self._events.reload_columns(new, [c for _l, c in _EVENT_FIELDS if c not in touched])
            # the page keyboard commands
            if not self._keys.changed():
                self._keys.reload()
            # view area, page colour, template
            if not self._size_changed():
                for sp, v in zip((self._sp_w, self._sp_h), self._page_size()):
                    sp.blockSignals(True)
                    sp.setValue(v)
                    sp.blockSignals(False)
                self._size0 = self._page_size()
            if self._dirty_colour is None:
                bg = self._page_colour()
                bg = None if bg is None else bg & 0xFFFFFF
                self._btn_colour.set_pair(bg)
                if self._tpl_picker is not None and self._tpl_picker._background != bg:
                    self._tpl_picker.set_background(bg)
            if self._tpl_picker is not None and not self._new_template_ref():
                i, ref = doc.template_reference()
                key = self._template_key()
                if ref is not None and key != self._tpl0:
                    self._tpl_index = i
                    self._tpl_picker.set_current(ref.library, ref.element)
                    self._sync_res_note()
                self._tpl0 = key
            # the lists
            if not self._env_changed():
                self._fill_environment()
                self._sync_env_buttons()
            if not self._associations_changed():
                try:
                    self._assoc_used = dict(doc.associations_in_use())
                except Exception:  # noqa: BLE001 -- a hint column only
                    _note_exc("page_props.reload/associations")
                    self._assoc_used = {}
                self._fill_associations()
                self._assoc0 = self._stored_associations()
                self._sync_assoc_buttons()
        finally:
            self._building = False
        self._revalidate()

    def _show_record(self, columns) -> None:
        """Show ``columns`` of ``self._rec`` in the General / Appearance fields."""
        from ...io import project as P

        cols = set(columns)
        if "TITLE" in cols:
            v = self._dbf_value("TITLE")
            self._title.setMaxLength(max(self._width("TITLE"), len(v)))
            self._title.setText(v)
        if "COMMENT" in cols:
            self._desc.set_value((self._rec or {}).get("COMMENT", ""))
        for col in ("PREV_PAGE", "NEXT_PAGE", "PARENT"):
            if col in cols:
                self._dbf_edits[col].set_value(self._dbf_value(col))
        if "CON_TYPE" in cols:
            self._cb_contype.setEditText(self._dbf_value("CON_TYPE"))
        if "AREA" in cols:
            area = self._dbf_value("AREA")
            self._chk_all_areas.blockSignals(True)
            self._chk_all_areas.setChecked(not area)
            self._chk_all_areas.blockSignals(False)
            self._cb_area.setEditText(area or ALL_AREAS_TEXT)
            self._cb_area.setEnabled(bool(area) and self._db)
            self._keys._sync_security()
        if "LOGDEVICE" in cols:
            self._cb_logdevice.setEditText(self._dbf_value("LOGDEVICE") or NONE_TEXT)
            self._check_logdevice()
        if "SCANTIME" in cols:
            scan = self._dbf_value("SCANTIME")
            self._scan0 = scan
            self._chk_scan_default.blockSignals(True)
            self._chk_scan_default.setChecked(not scan)
            self._chk_scan_default.blockSignals(False)
            try:
                self._sp_scan.setValue(int(scan or P.default_scan_time()))
            except ValueError:
                self._sp_scan.setValue(int(P.default_scan_time()))
            self._sp_scan.setEnabled(bool(scan) and self._db)
        if cols & {"CLUSTER", "CINHERIT"}:
            inherit = self._dbf_value("CINHERIT").upper() == "TRUE"
            self._chk_inherit.blockSignals(True)
            self._chk_inherit.setChecked(inherit)
            self._chk_inherit.blockSignals(False)
            self._cb_cluster.setEditText("" if inherit else self._dbf_value("CLUSTER"))
            self._cb_cluster.setEnabled(not inherit and self._db)
        if "IGNOREVQ" in cols:
            cur = self._dbf_value("IGNOREVQ")
            idx = self._cb_quality.findData(cur or "-1")
            if idx < 0:
                self._cb_quality.addItem("Value %s" % cur, cur)
                idx = self._cb_quality.count() - 1
            self._cb_quality.setCurrentIndex(idx)

    def apply(self) -> bool:
        """Write the dialog: everything it changes is ONE undo step."""
        doc = self._doc
        self.apply_error = ""
        self._check_timer.stop()

        # check everything that can refuse BEFORE the first write
        msg, tab, focus = self._problem()
        if msg:
            return self._refuse(msg, tab, focus)
        assoc_pairs = self._association_pairs()
        env_rows = self._env_rows()
        updates = self._record_updates()
        key_rows = None
        if self._db and self._keys._enabled and self._keys.rows_changed():
            key_rows = self._keys.page_rows()

        changed = False
        new_tpl = self._new_template_ref()
        # the view area as the dialog LOADED it: an untouched size is not
        # written (not the old size over one changed on the page meanwhile,
        # and not over the size a template switch below gives the page)
        size_before = self._size0
        write_assoc = self._associations_changed()
        with doc.batch("page properties"):
            # the page keyboard commands first: they are staged from the stored rows, read
            # again now, and when those cannot be read (another ctgedit process writing the
            # project for the whole wait) nothing else may have changed yet
            if key_rows is not None:
                try:
                    if doc.set_page_keyboard_commands(key_rows):
                        changed = True
                except ValueError as e:
                    return self._write_failed("%s\n\nNothing was changed." % e)
            if new_tpl:
                # CtDraw re-links the page: the old template's records go,
                # the new template's prefix is composed in their place and
                # the page takes the template's size / background / page
                # properties (format/template_place)
                try:
                    doc.change_template(new_tpl)
                except Exception as e:  # noqa: BLE001
                    _note_exc("page_props.apply/change_template", template=new_tpl)
                    return self._write_failed(
                        "The page could not be linked to template %s: %s\n\nNothing was "
                        "changed." % (new_tpl, e)
                    )
                changed = True

            # the project-database row + the page keyboard commands: STAGED,
            # written when the page is saved (like CtDraw)
            if updates and doc.set_page_record(updates):
                changed = True
            if self._apply_key_records():
                changed = True

            # view area: a size typed on the tab (after a template switch it
            # replaces the template's size)
            if (self._sp_w.value(), self._sp_h.value()) != size_before:
                doc.set_page_size(self._sp_w.value(), self._sp_h.value())
                changed = True

            # page colour
            if self._dirty_colour is not None:
                doc.set_page_background(self._dirty_colour)
                self._dirty_colour = None
                changed = True

            # environment (attributes property list 1)
            seen = set()
            for k, v in env_rows:
                if not k:
                    continue
                seen.add(k)
                if self._env_original.get(k) != v:
                    if doc.set_page_property(k, v):
                        changed = True
            for k in self._env_original:
                if k not in seen:
                    if doc.remove_page_property(k):
                        changed = True

            # associations (attributes property list 2): only a table the user
            # changed (never the loaded list over one changed on the page)
            if write_assoc and doc.set_associations(assoc_pairs):
                changed = True
        # the new baseline: every field shows the page as it is now (a value
        # changed on the page meanwhile included, so that the next Apply does
        # not put the old one back)
        self._take_baselines()
        self._fill_environment()
        if self._db:
            self._rec = _shown_record(doc)
            self._scan0 = self._dbf_value("SCANTIME")
            self._building = True
            try:
                self._show_record(set(self._record_values()))
            finally:
                self._building = False
            self._events.rebase(self._rec)
        self._keys.reload()
        self._fill_associations()
        # the view area and page colour as the page has them now (a template
        # switch gives the page the template's size and colour)
        for sp, v in ((self._sp_w, doc.page.appearance.width), (self._sp_h, doc.page.appearance.height)):
            sp.blockSignals(True)
            sp.setValue(int(v or 0))
            sp.blockSignals(False)
        bg = self._page_colour()
        bg = None if bg is None else bg & 0xFFFFFF
        self._btn_colour.set_pair(bg)
        if self._tpl_picker is not None and self._tpl_picker._background != bg:
            self._tpl_picker.set_background(bg)
        if self._tpl_picker is not None:
            # the page's template now (the one picked, or one linked on the page)
            i, ref = doc.template_reference()
            if ref is not None and (new_tpl or self._new_template_ref()):
                self._tpl_index = i
                self._tpl_picker.set_current(ref.library, ref.element)
                self._sync_res_note()
        self.changed = self.changed or changed
        self._revalidate()
        if changed:
            self.applied.emit()
        return changed

    def _apply_key_records(self) -> bool:
        """V3/V4 page-wide keyboard RECORDS listed on the Keyboard Commands
        tab: their edited fields go to the record, a deleted one is removed."""
        doc = self._doc
        before = {e["_record"]: e for e in self._keys._entries0 if e.get("_record") is not None}
        if not before:
            return False
        now = {e["_record"]: e for e in self._keys.entries() if e.get("_record") is not None}
        changed = False
        for idx, e in now.items():
            b = before.get(idx, {})
            for k in PageKeyboardTab.FIELDS:
                v = e.get(k) or ""
                if v == (b.get(k) or ""):
                    continue
                # an emptied field is no key at all (a new record carries
                # only the keys it uses: KEY_SEQ, CMD, AREA, PRIV, MSG_LOG)
                if doc.remove_property(idx, k) if not v else doc.ensure_property(idx, k, v):
                    changed = True
        gone = sorted((i for i in before if i not in now), reverse=True)
        if gone:
            doc.delete_objects(gone)
            changed = True
        return changed
