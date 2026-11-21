"""The Input tab: touch and keyboard commands."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import os
import re
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

from ...format import inputs as IN
from .scroll import FitTabWidget, LinesMixin, scroll_page
from ..style.theme import set_note_role

#: key sequences with numeric arguments, offered at the top of the key menu
_ARG_PATTERNS = ["### ENTER", "##### ENTER", "########## ENTER"]


def key_sequence_choices(doc=None) -> List[str]:
    """Key names for the key sequence menu."""
    out: List[str] = []
    seen = set()

    def add(name):
        name = (name or "").strip()
        if name and name.upper() not in seen:
            seen.add(name.upper())
            out.append(name)

    for p in _ARG_PATTERNS:
        add(p)
    path = getattr(doc, "path", None)
    if path:
        try:
            from ...io.project import keyboard_keys

            for row in keyboard_keys(os.path.dirname(os.path.abspath(path))):
                add(row.get("NAME"))
        except Exception:  # noqa: BLE001 -- the menu is an aid, never required
            pass
    try:
        from ...io.reader import _catalog

        for k in _catalog().get("key_seq_value_enum", []):
            add(k)
    except Exception:  # noqa: BLE001 -- the menu is an aid, never required
        pass
    return out


def key_sequence_warnings(seq: str, others=(), where: str = "object") -> List[str]:
    """Advice for a key sequence (never blocking): hot keys ('*') belong to
    system keyboard commands, and a sequence that starts another one of the
    same object (``where``: 'object' / 'page') makes the longer one
    unreachable ('F3' vs 'F3 F4')."""
    seq = seq.strip()
    out = []
    if "*" in seq:
        out.append(
            "'*' (hot key) works only in system keyboard commands, not on "
            "a%s %s." % ("n" if where[:1] in "aeiou" else "", where)
        )
    toks = seq.upper().split()
    for o in others:
        ot = o.strip().upper().split()
        if not ot or not toks or ot == toks:
            if ot == toks and toks:
                out.append("Another key sequence of this %s is the same: '%s'." % (where, o.strip()))
            continue
        short, long_ = (toks, ot) if len(toks) < len(ot) else (ot, toks)
        if long_[: len(short)] == short:
            out.append(
                "'%s' starts '%s': the longer sequence can never run." % (" ".join(short), " ".join(long_))
            )
    return out


#: Plant SCADA passes at most eight keyboard arguments (Arg1 .. Arg8, help
#: 1094533 'Passing Multiple Arguments')
MAX_KEY_ARGUMENTS = 8
_ARG_RUN = re.compile(r"#+")
_ARG_USE = re.compile(r"\bArg(?:Value)?(\d+)\b", re.IGNORECASE)


def key_sequence_arguments(seq: str) -> int:
    """How many arguments a key sequence passes: one per run of '#'
    characters ('F3 ### ENTER' -> 1, 'F3 ###, ### ENTER' -> 2; each '#' is
    one character the operator may type, help 1094532)."""
    return len(_ARG_RUN.findall(seq or ""))


def command_argument_warnings(seq: str, command: str) -> List[str]:
    """Advice on a keyboard command's arguments (never blocking): the
    command reads ArgN / ArgValueN that the key sequence does not pass, the
    sequence passes arguments the command never reads, or more than eight."""
    n = key_sequence_arguments(seq)
    used = sorted({int(m) for m in _ARG_USE.findall(command or "")})
    out = []
    if n > MAX_KEY_ARGUMENTS:
        out.append(
            "The key sequence passes %d arguments; Plant SCADA passes at most %d "
            "(Arg1 to Arg%d)." % (n, MAX_KEY_ARGUMENTS, MAX_KEY_ARGUMENTS)
        )
    over = [u for u in used if u > n]
    if over:
        out.append(
            "The command uses Arg%d, but the key sequence passes %s. Add a '#' "
            "for each character the operator types (for example F3 ### ENTER)."
            % (over[0], "no argument" if n == 0 else "only %d argument%s" % (n, "" if n == 1 else "s"))
        )
    elif n and not used:
        out.append(
            "The key sequence takes %s ('#'), but the command does not use it."
            % ("an argument" if n == 1 else "%d arguments" % n)
        )
    return out


class CommandEdit(LinesMixin, QtWidgets.QPlainTextEdit):
    """A multi-line Cicode command field."""

    def __init__(self, value: str = "", limit: int = IN.MAX_COMMAND, parent=None, min_lines: int = 2):
        QtWidgets.QPlainTextEdit.__init__(self, parent)
        self._limit = limit
        self._orig = value or ""
        self.setPlainText(self._orig.replace("\r\n", "\n"))
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self._init_lines(min_lines)
        self.counter = QtWidgets.QLabel()
        self.counter.setObjectName("dim")
        self.textChanged.connect(self._update_counter)
        self._update_counter()
        # the Cicode check (ui/expression_check): Check compiles the command as the page
        # compiler would; the verdict shows under the field
        self._checker = None
        self.verdict = QtWidgets.QLabel()
        self.verdict.setWordWrap(True)
        self.verdict.hide()
        self._checked_text: Optional[str] = None
        self.textChanged.connect(self._text_edited)

    def set_checker(self, checker) -> None:
        self._checker = checker

    def checker(self):
        return self._checker

    def check_now(self) -> bool:
        """Compile the command now; the verdict under the field. False without a checker."""
        text = self.value().replace("\r\n", "\n").strip()
        if self._checker is None or not text:
            self.verdict.hide()
            return False
        self.verdict.setText("Checking…")
        set_note_role(self.verdict, "text_dim")
        self.verdict.show()
        self._checker.check(text, "command", lambda result, t=text: self._checked(t, result))
        return True

    def _checked(self, text: str, result: dict) -> None:
        if text != self.value().replace("\r\n", "\n").strip():
            return
        from ..expression_check import summary

        said = summary(result)
        self._checked_text = text
        if result.get("error"):
            self.verdict.setText(said)
            set_note_role(self.verdict, "warn")
        elif result.get("errors"):
            self.verdict.setText(said)
            set_note_role(self.verdict, "danger")
        elif result.get("warnings"):
            self.verdict.setText(said)
            set_note_role(self.verdict, "warn")
        else:
            self.verdict.setText("Compiles.")
            set_note_role(self.verdict, "text_dim")
        self.verdict.show()

    def _text_edited(self) -> None:
        if self._checked_text is not None and self.verdict.isVisible():
            self.verdict.hide()  # (the verdict was the old text's)

    def value(self) -> str:
        text = self.toPlainText()
        if text == self._orig.replace("\r\n", "\n"):
            return self._orig
        return text.replace("\r\n", "\n").replace("\n", "\r\n")

    def set_value(self, value: str) -> None:
        self._orig = value or ""
        self.setPlainText(self._orig.replace("\r\n", "\n"))

    def changed(self) -> bool:
        return self.value() != self._orig

    def too_long(self) -> bool:
        return self.changed() and len(self.value()) > self._limit

    def _update_counter(self) -> None:
        n = len(self.value())
        self.counter.setText("%d / %d" % (n, self._limit))
        set_note_role(self.counter, "danger" if n > self._limit else "")


class KeySequenceDialog(QtWidgets.QDialog):
    """Add / Edit a key sequence: type it, or pick keys from the menu (each
    pick appends the key name, so 'F2' then 'ENTER' gives 'F2 ENTER')."""

    # one short field, a fixed size
    RESIZE = "none"

    def __init__(self, parent=None, current: str = "", choices=(), others=(), where: str = "object"):
        super().__init__(parent)
        self.setWindowTitle("Key Sequence")
        self._others = list(others)
        self._where = where
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(
            QtWidgets.QLabel(
                "Keys separated by spaces, '#' for each digit of an argument (for example ### ENTER)."
            )
        )
        row = QtWidgets.QHBoxLayout()
        self.combo = QtWidgets.QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.combo.addItems(list(choices))
        self.combo.setEditText(current)
        self.combo.lineEdit().setMaxLength(IN.MAX_KEY_SEQ)
        self.combo.setMinimumWidth(260)
        row.addWidget(self.combo, 1)
        keys = QtWidgets.QToolButton()
        keys.setText("Keys")
        keys.setToolTip("Append a key name to the sequence")
        keys.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(keys)
        for name in choices:
            menu.addAction(name, lambda n=name: self._append(n))
        keys.setMenu(menu)
        row.addWidget(keys)
        lay.addLayout(row)
        self.warning = QtWidgets.QLabel()
        self.warning.setWordWrap(True)
        set_note_role(self.warning, "warn")
        lay.addWidget(self.warning)
        self.combo.editTextChanged.connect(self._check)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.accepted.connect(self._ok)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._check()

    def _append(self, name: str) -> None:
        cur = self.combo.currentText().rstrip()
        self.combo.setEditText((cur + " " + name).strip() if cur else name)

    def sequence(self) -> str:
        return " ".join(self.combo.currentText().split())

    def _check(self, *_a) -> None:
        self.warning.setText("\n".join(key_sequence_warnings(self.sequence(), self._others, self._where)))

    def _ok(self) -> None:
        seq = self.sequence()
        # (the dialog's own warning line, not a message box: the UI guide's inline rule)
        if not seq:
            self.warning.setText("Type a key sequence or pick one from the Keys menu.")
            self.combo.setFocus()
            return
        if len(seq) > IN.MAX_KEY_SEQ:
            self.warning.setText("A key sequence can be at most %d characters long." % IN.MAX_KEY_SEQ)
            self.combo.setFocus()
            return
        self.accept()

    @classmethod
    def get_sequence(
        cls, parent, doc=None, current: str = "", others=(), where: str = "object"
    ) -> Optional[str]:
        dlg = cls(parent, current, key_sequence_choices(doc), others, where)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg.sequence()


class InputCommandsTab(FitTabWidget):
    """Input > Touch and Input > Keyboard Commands for one object."""

    # the Touch page's Action list (Up / Down / Repeat / Double click); a button has
    # no double-click command (no pgbutton column)
    _ACTIONS = (
        ("Up", "CMD", "MSG_LOG"),
        ("Down", "DOWN_CMD", "MSG_LOGDN"),
        ("Repeat", "REPEAT_CMD", "MSG_LOGRPT"),
        ("Double-click", "DBLCLK_CMD", "MSG_LOGDBL"),
    )
    _WHEN = {
        "Up": "clicks and releases the left mouse button",
        "Down": "presses the left mouse button",
        "Repeat": "holds the left mouse button down",
        "Double-click": "double-clicks the object",
    }

    def __init__(self, doc, index: int, parent=None, context=None):
        super().__init__(parent)
        self._doc = doc
        self._index = index
        #: the sheet's ExpressionContext: command fields get the Wizard menu
        self._context = context
        self._load()
        self._actions = [a for a in self._ACTIONS if not (self._button and a[1] == "DBLCLK_CMD")]
        self._build_touch()
        self._build_keyboard()

    # -- state ----------------------------------------------------------------
    def _load(self) -> None:
        info = self._doc.input_commands(self._index)
        self._info = info
        self._editable = info["editable"]
        self._button = info["button"]
        self._touch0 = dict(info["touch"] or {})
        kids = self._doc.keyboard_children(self._index)
        self._keys: List[dict] = [dict(v, _child=c) for c, v in zip(kids, info["keys"])]
        self._keys0 = [dict(e) for e in self._keys]

    def _banner(self, lay: QtWidgets.QVBoxLayout) -> None:
        if not self._editable and self._info["reason"]:
            lab = QtWidgets.QLabel(self._info["reason"] + " The commands are shown read-only.")
            lab.setWordWrap(True)
            lab.setObjectName("dim")
            lay.addWidget(lab)

    # -- touch ----------------------------------------------------------------
    def _build_touch(self) -> None:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self._banner(lay)
        # one row per action; the command boxes share the extra height of a
        # larger window
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid, 1)
        t = self._touch0
        self.action_checks = {}
        self.command_edits = {}
        self.log_edits = {}
        none_set = not any(t.get(c) or t.get(g) for _n, c, g in self._actions)
        for row, (name, ckey, lkey) in enumerate(self._actions):
            chk = QtWidgets.QCheckBox(name)
            chk.setChecked(bool(t.get(ckey) or t.get(lkey)) or (none_set and name == "Up"))
            ed = CommandEdit(t.get(ckey, ""))
            ed.setToolTip(
                "%s command: runs when the operator %s (at most %d characters)."
                % (name, self._WHEN[name], IN.MAX_COMMAND)
            )
            cell = QtWidgets.QWidget()
            cl = QtWidgets.QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(_sp(0))
            cl.addWidget(ed)
            cl.addLayout(self._counter_row(ed))
            cl.addWidget(ed.verdict)
            logrow = QtWidgets.QHBoxLayout()
            log_label = QtWidgets.QLabel("Log message:")
            log = QtWidgets.QLineEdit(t.get(lkey, ""))
            log.setMaxLength(max(IN.MAX_LOG, len(t.get(lkey, ""))))
            log.setPlaceholderText("at most %d characters" % IN.MAX_LOG)
            log.setToolTip("Written to the log device when the %s action runs" % name)
            logrow.addWidget(log_label)
            logrow.addWidget(log, 1)
            cl.addLayout(logrow)
            if self._button and lkey != "MSG_LOG":
                log.setVisible(False)  # a button has one log message (pgbutton)
                log_label.setVisible(False)
            grid.addWidget(chk, row, 0, QtCore.Qt.AlignTop)
            grid.addWidget(cell, row, 1)
            grid.setRowStretch(row, 1)
            chk.toggled.connect(lambda on, e=ed, g=log: (e.setEnabled(on), g.setEnabled(on)))
            ed.setEnabled(chk.isChecked())
            log.setEnabled(chk.isChecked())
            self.action_checks[ckey] = chk
            self.command_edits[ckey] = ed
            self.log_edits[lkey] = log
        row = len(self._actions)
        self.rate = QtWidgets.QSpinBox()
        self.rate.setRange(1, 99999)
        self.rate.setSuffix(" ms")
        self._rate0 = t.get("RPT_RATE", "")
        try:
            self.rate.setValue(int(self._rate0 or IN.DEFAULT_RPT_RATE))
        except ValueError:
            self.rate.setValue(int(IN.DEFAULT_RPT_RATE))
        self.rate.setToolTip("Delay before the first repeat and between repeats")
        rate_label = QtWidgets.QLabel("Repeat rate:")
        rate_label.setBuddy(self.rate)
        grid.addWidget(rate_label, row, 0)
        rrow = QtWidgets.QHBoxLayout()
        rrow.addWidget(self.rate)
        rrow.addStretch(1)
        grid.addLayout(rrow, row, 1)
        clear = QtWidgets.QPushButton("Clear Property")
        clear.setToolTip("Remove every touch command of this object")
        clear.clicked.connect(self._clear_touch)
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(clear)
        crow.addStretch(1)
        grid.addLayout(crow, row + 1, 1)
        if not self._editable:
            for wdg in list(self.command_edits.values()) + list(self.log_edits.values()):
                wdg.setReadOnly(True)
            for wdg in list(self.action_checks.values()) + [self.rate, clear]:
                wdg.setEnabled(False)
        self.addTab(scroll_page(w), "Touch")

    def _counter_row(self, ed: "CommandEdit") -> QtWidgets.QHBoxLayout:
        """A command field's Wizard button (Insert Tag / Insert Function at the
        cursor) and its character counter."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if self._context is not None:
            from .expression import wizard_button

            b = wizard_button(ed, self._context)
            b.setAutoRaise(True)
            ed.wizard = b
            row.addWidget(b)
            # Check: the command compiled by the project's Cicode (the simulator), nothing run
            from ..expression_check import checker_for

            checker = checker_for(getattr(self._context, "project_dir", None))
            if checker is not None:
                ed.set_checker(checker)
                cb = QtWidgets.QToolButton()
                cb.setText("Check")
                cb.setAutoRaise(True)
                cb.setToolTip(
                    "Compile this command with the project's Cicode (the simulator); "
                    "nothing runs. The verdict shows under the field"
                )
                cb.clicked.connect(ed.check_now)
                ed.check_button = cb
                row.addWidget(cb)
        row.addStretch(1)
        row.addWidget(ed.counter, 0, QtCore.Qt.AlignRight)
        return row

    def show_pages(self, touch: bool = True, keyboard: bool = True) -> None:
        """Show only the pages the object's class has (CtDraw's page mask)."""
        self.setTabVisible(0, bool(touch))
        self.setTabVisible(1, bool(keyboard))
        if not touch and keyboard:
            self.setCurrentIndex(1)

    def _clear_touch(self) -> None:
        for chk in self.action_checks.values():
            chk.setChecked(False)
        for ed in self.command_edits.values():
            ed.setPlainText("")
        for log in self.log_edits.values():
            log.setText("")

    def touch_values(self) -> dict:
        """What the Touch tab would write: unchecked actions write nothing."""
        out = {}
        for _name, ckey, lkey in self._actions:
            on = self.action_checks[ckey].isChecked()
            out[ckey] = self.command_edits[ckey].value() if on else ""
            out[lkey] = self.log_edits[lkey].text() if on else ""
        rate = str(self.rate.value())
        if rate != (self._rate0 or IN.DEFAULT_RPT_RATE):
            out["RPT_RATE"] = rate  # unchanged: the record keeps its own
        return out

    def _touch_changed(self) -> bool:
        vals = self.touch_values()
        t0 = self._touch0
        if "RPT_RATE" in vals:
            return True
        # only the actions this tab shows (a button has no double click)
        return any(v != t0.get(k, "") for k, v in vals.items())

    # -- keyboard -------------------------------------------------------------
    def _build_keyboard(self) -> None:
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        self._banner(outer)
        body = QtWidgets.QHBoxLayout()
        outer.addLayout(body, 1)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Key sequences:"))
        self.key_list = QtWidgets.QListWidget()
        self.key_list.setMinimumWidth(170)
        left.addWidget(self.key_list, 1)
        brow = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add…")
        self.btn_edit = QtWidgets.QPushButton("Edit…")
        self.btn_del = QtWidgets.QPushButton("Remove")
        for b in (self.btn_add, self.btn_edit, self.btn_del):
            b.setAutoDefault(False)
            brow.addWidget(b)
        left.addLayout(brow)
        body.addLayout(left)

        right = QtWidgets.QFormLayout()
        self.key_cmd = CommandEdit("")
        self.key_cmd.setToolTip(
            "Runs when the operator types the key sequence over the "
            "object (at most %d characters)." % IN.MAX_COMMAND
        )
        cmd_cell = QtWidgets.QWidget()
        cc = QtWidgets.QVBoxLayout(cmd_cell)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.addWidget(self.key_cmd)
        cc.addLayout(self._counter_row(self.key_cmd))
        cc.addWidget(self.key_cmd.verdict)
        right.addRow("Command:", cmd_cell)
        self.same_area = QtWidgets.QCheckBox("Same area as object/group")
        self.key_area = QtWidgets.QLineEdit()
        self.key_area.setPlaceholderText("Command area")
        right.addRow("Security:", self.same_area)
        right.addRow("Area:", self.key_area)
        self.same_priv = QtWidgets.QCheckBox("Same privilege as object/group")
        self.key_priv = QtWidgets.QLineEdit()
        self.key_priv.setPlaceholderText("Privilege level")
        right.addRow("", self.same_priv)
        right.addRow("Privilege:", self.key_priv)
        self.key_log = QtWidgets.QLineEdit()
        self.key_log.setMaxLength(IN.MAX_LOG)
        self.key_log.setPlaceholderText("Log message (at most %d characters)" % IN.MAX_LOG)
        right.addRow("Log message:", self.key_log)
        # the command box takes the extra height (no stretch below the form)
        right_col = QtWidgets.QVBoxLayout()
        right_col.addLayout(right, 1)
        body.addLayout(right_col, 1)

        self.same_area.toggled.connect(lambda on: self.key_area.setEnabled(not on))
        self.same_priv.toggled.connect(lambda on: self.key_priv.setEnabled(not on))
        self.key_list.currentRowChanged.connect(self._on_key_row)
        self.btn_add.clicked.connect(self._add_key)
        self.btn_edit.clicked.connect(self._edit_key)
        self.btn_del.clicked.connect(self._delete_key)
        self._cur = -1
        self._fill_key_list()
        if not self._editable:
            for wdg in (self.btn_add, self.btn_edit, self.btn_del, self.same_area, self.same_priv):
                wdg.setEnabled(False)
            for wdg in (self.key_cmd, self.key_area, self.key_priv, self.key_log):
                wdg.setReadOnly(True)
        self.addTab(scroll_page(w), "Keyboard Commands")

    def _fill_key_list(self, select: int = 0) -> None:
        self._cur = -1
        self.key_list.blockSignals(True)
        self.key_list.clear()
        for e in self._keys:
            self.key_list.addItem(e.get("KEY_SEQ") or "(no key sequence)")
        self.key_list.blockSignals(False)
        if self._keys:
            self.key_list.setCurrentRow(max(0, min(select, len(self._keys) - 1)))
        else:
            self._on_key_row(-1)

    def _store_current(self) -> None:
        if 0 <= self._cur < len(self._keys):
            e = self._keys[self._cur]
            e["CMD"] = self.key_cmd.value()
            e["AREA"] = "" if self.same_area.isChecked() else self.key_area.text().strip()
            e["PRIV"] = "" if self.same_priv.isChecked() else self.key_priv.text().strip()
            e["MSG_LOG"] = self.key_log.text()

    def _on_key_row(self, row: int) -> None:
        self._store_current()
        self._cur = row
        have = 0 <= row < len(self._keys)
        e = self._keys[row] if have else {}
        self.key_cmd.set_value(e.get("CMD", ""))
        self.key_area.setText(e.get("AREA", ""))
        self.key_priv.setText(e.get("PRIV", ""))
        self.same_area.setChecked(not e.get("AREA"))
        self.same_priv.setChecked(not e.get("PRIV"))
        self.key_area.setEnabled(have and not self.same_area.isChecked())
        self.key_priv.setEnabled(have and not self.same_priv.isChecked())
        self.key_log.setText(e.get("MSG_LOG", ""))
        for wdg in (self.key_cmd, self.key_log):
            wdg.setEnabled(have)  # read-only mode: still selectable
        for wdg in (self.same_area, self.same_priv):
            wdg.setEnabled(have and self._editable)
        self.btn_edit.setEnabled(have and self._editable)
        self.btn_del.setEnabled(have and self._editable)

    def _others(self, skip: int = -1) -> List[str]:
        return [e.get("KEY_SEQ", "") for i, e in enumerate(self._keys) if i != skip]

    def _add_key(self) -> None:
        seq = KeySequenceDialog.get_sequence(self, self._doc, "", self._others())
        if seq:
            self.add_key_sequence(seq)

    def add_key_sequence(self, seq: str, command: str = "") -> None:
        """Append a key sequence (the Add button's effect) and select it."""
        self._store_current()
        self._keys.append({"_child": None, "KEY_SEQ": seq, "CMD": command})
        self._fill_key_list(len(self._keys) - 1)

    def _edit_key(self) -> None:
        row = self.key_list.currentRow()
        if not 0 <= row < len(self._keys):
            return
        seq = KeySequenceDialog.get_sequence(
            self, self._doc, self._keys[row].get("KEY_SEQ", ""), self._others(row)
        )
        if seq:
            self._store_current()
            self._keys[row]["KEY_SEQ"] = seq
            self._fill_key_list(row)

    def _delete_key(self) -> None:
        row = self.key_list.currentRow()
        if 0 <= row < len(self._keys):
            self._cur = -1  # do not store into the removed entry
            del self._keys[row]
            self._fill_key_list(row)

    def key_entries(self) -> List[dict]:
        self._store_current()
        return [dict(e) for e in self._keys]

    def _keys_changed(self) -> bool:
        cur = self.key_entries()
        if len(cur) != len(self._keys0):
            return True
        for a, b in zip(cur, self._keys0):
            if a.get("_child") != b.get("_child"):
                return True
            if any((a.get(k) or "") != (b.get(k) or "") for k in IN.KEYBOARD_KEYS):
                return True
        return False

    # -- validate / apply -----------------------------------------------------
    def validate(self) -> Optional[str]:
        """A message when a changed value breaks a Graphics Builder limit."""
        if not self._editable:
            return None
        for name, ckey, _lkey in self._actions:
            ed = self.command_edits[ckey]
            if self.action_checks[ckey].isChecked() and ed.too_long():
                return "The %s command is %d characters long; Plant SCADA allows at most %d." % (
                    name,
                    len(ed.value()),
                    IN.MAX_COMMAND,
                )
        for e in self.key_entries():
            before = next(
                (
                    b
                    for b in self._keys0
                    if b.get("_child") is not None and b.get("_child") == e.get("_child")
                ),
                {},
            )
            cmd = e.get("CMD") or ""
            if cmd != (before.get("CMD") or "") and len(cmd) > IN.MAX_COMMAND:
                return (
                    "The command of key sequence '%s' is %d characters long; Plant "
                    "SCADA allows at most %d." % (e.get("KEY_SEQ", ""), len(cmd), IN.MAX_COMMAND)
                )
            if cmd and not e.get("KEY_SEQ"):
                return "A keyboard command needs a key sequence (click Edit)."
        return None

    def has_changes(self) -> bool:
        return self._editable and (self._touch_changed() or self._keys_changed())

    def apply(self) -> bool:
        """Write the tab (ONE undo step). Raises ValueError with a message the
        caller shows when a value cannot be stored."""
        if not self.has_changes():
            return False
        err = self.validate()
        if err:
            raise ValueError(err)
        touch = self.touch_values() if self._touch_changed() else None
        keys = self.key_entries() if self._keys_changed() else None
        changed = self._doc.set_input_commands(self._index, touch=touch, keys=keys)
        self.reload()
        return changed

    def reload(self) -> None:
        """Re-read the object's commands (after an apply / undo)."""
        row = self.key_list.currentRow()
        self._load()
        t = self._touch0
        none_set = not any(t.get(c) or t.get(g) for _n, c, g in self._actions)
        for name, ckey, lkey in self._actions:
            self.action_checks[ckey].setChecked(
                bool(t.get(ckey) or t.get(lkey)) or (none_set and name == "Up")
            )
            self.command_edits[ckey].set_value(t.get(ckey, ""))
            self.log_edits[lkey].setText(t.get(lkey, ""))
        self._rate0 = t.get("RPT_RATE", "")
        try:
            self.rate.setValue(int(self._rate0 or IN.DEFAULT_RPT_RATE))
        except ValueError:
            self.rate.setValue(int(IN.DEFAULT_RPT_RATE))
        self._fill_key_list(max(0, row))


class InputEditor:
    """The sheet's editor contract for the Input tab: it writes the touch /
    keyboard CHILD records (a structural edit) AFTER the key edits, which
    address the records by index."""

    phase = "after"

    def __init__(self, sheet, tab: InputCommandsTab):
        self.sheet = sheet
        self.tab = tab

    def ok_problem(self):
        """(title, message, widget) that keeps OK from closing the sheet."""
        err = self.tab.validate()
        return ("Input Commands", err, self.tab) if err else None

    def problem(self):
        return None

    def apply(self) -> bool:
        try:
            return bool(self.tab.apply())
        except ValueError as e:
            # OK keeps the sheet open (the commands were not stored)
            self.sheet._tabs.setCurrentWidget(self.tab)
            self.sheet._fail(str(e))
            return False

    def reload(self) -> None:
        pass

    # -- modeless -------------------------------------------
    def has_pending_edits(self) -> bool:
        tab = self.tab
        return bool(tab._editable and (tab._touch_changed() or tab._keys_changed()))

    def refresh(self) -> None:
        """The page changed underneath: show the object's commands as they
        are now (only called while nothing typed here is pending)."""
        self.tab.reload()

    def retarget(self, index: int) -> None:
        self.tab._index = index

    def set_read_only(self, ro: bool) -> None:
        if not ro:
            return
        tab = self.tab
        for w in (
            list(tab.command_edits.values())
            + list(tab.log_edits.values())
            + [tab.key_cmd, tab.key_area, tab.key_priv, tab.key_log]
        ):
            w.setReadOnly(True)
            wiz = getattr(w, "wizard", None)
            if wiz is not None:
                wiz.setVisible(False)
        for w in list(tab.action_checks.values()) + [
            tab.rate,
            tab.btn_add,
            tab.btn_edit,
            tab.btn_del,
            tab.same_area,
            tab.same_priv,
        ]:
            w.setEnabled(False)
        for b in tab.findChildren(QtWidgets.QPushButton):
            if b.text() == "Clear Property":
                b.setEnabled(False)
        tab._editable = False


def build_input_tab(sheet, pages):
    """Input > Touch + Input > Keyboard Commands as ONE tab widget (its inner
    tabs are the two pages)."""
    ids = {p.id for p in pages}
    tab = InputCommandsTab(
        sheet._doc, sheet._index, sheet, context=getattr(sheet, "expression_context", None)
    )
    tab.show_pages("touch" in ids, "keyboard" in ids)
    sheet._input = tab
    sheet.add_editor(InputEditor(sheet, tab))
    return tab


TAB_BUILDERS = {"Input": build_input_tab}
BUILDERS: dict = {}
SUBS: list = []
