"""Settings > Keyboard."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import keymap
from .settings import app_settings
from ..messages import warn

#: the table's columns
COLUMNS = ("Command", "Menu", "Keys", "Where", "Source")
KEYS_ROLE = QtCore.Qt.UserRole + 1  # a row's keys (portable text list)
ID_ROLE = QtCore.Qt.UserRole + 2  # a row's command id


def _main_window(widget):
    """The editor window ``widget`` belongs to (else the first one open)."""
    try:
        from ..main_window import MainWindow
    except ImportError:  # swallow-ok: no editor in this process
        return None
    w = widget
    while w is not None:
        if isinstance(w, MainWindow):
            return w
        w = w.parent() if isinstance(w, QtCore.QObject) else None
    wins = MainWindow.editor_windows()
    return wins[0] if wins else None


def key_parts(key: str) -> List[str]:
    """'Ctrl+Shift+Z' -> ['Ctrl', 'Shift', 'Z'] (the caps of one key, as the menus name
    them; 'Ctrl++' keeps its plus)."""
    text = keymap.native(key) or key
    parts, cur = [], ""
    for k, ch in enumerate(text):
        if ch == "+" and cur:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


# ----------------------------------------------------------------------------- key caps
class KeyCapDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the keys of a row (KEYS_ROLE) as rounded key caps; several keys side by side."""

    PAD, GAP, SEP = 5, 3, 10

    def _caps(self, option, keys):
        fm = option.fontMetrics
        out = []
        for key in keys:
            out.append([(p, fm.horizontalAdvance(p) + 2 * self.PAD) for p in key_parts(key)])
        return out

    @staticmethod
    def cap_colours(selected: bool):
        """(edge, face, text) of a cap. The caps keep their own face on a selected row too,
        so their text stays the text colour (the selection's white text on a light cap could
        not be read); the edge takes the selection's text colour to stand out."""
        from ..style.theme import COLORS

        edge = QtGui.QColor(
            COLORS.get("sel_text" if selected else "border", "#ffffff" if selected else "#3a3f46")
        )
        face = QtGui.QColor(COLORS.get("raised", "#343941"))
        text = QtGui.QColor(COLORS.get("text", "#d6d9de"))
        return edge, face, text

    def paint(self, painter, option, index):  # noqa: D401 -- Qt naming
        keys = index.data(KEYS_ROLE)
        if not keys:
            super().paint(painter, option, index)
            return
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = option.rect
        fm = option.fontMetrics
        h = min(r.height() - 4, fm.height() + 4)
        y = r.top() + (r.height() - h) / 2.0
        x = r.left() + 4
        edge, face, text = self.cap_colours(bool(option.state & QtWidgets.QStyle.State_Selected))
        more = fm.horizontalAdvance("…") + 4
        for n, caps in enumerate(self._caps(option, keys)):
            whole = sum(w + self.GAP for _p, w in caps) - self.GAP
            # (a key is drawn whole or not at all; the tool tip names every key)
            last = n == len(keys) - 1
            if x + whole > r.right() - (0 if last else more):
                painter.setPen(text)
                painter.drawText(
                    QtCore.QRectF(x, y, max(0.0, r.right() - x), h),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                    "…",
                )
                break
            for part, w in caps:
                rect = QtCore.QRectF(x, y, w, h)
                painter.setPen(QtGui.QPen(edge, 1))
                painter.setBrush(face)
                painter.drawRoundedRect(rect, 3, 3)
                painter.setPen(text)
                painter.drawText(rect, QtCore.Qt.AlignCenter, part)
                x += w + self.GAP
            x += self.SEP
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802 -- Qt naming
        base = super().sizeHint(option, index)
        keys = index.data(KEYS_ROLE)
        if not keys:
            return base
        w = 8 + sum(sum(cw + self.GAP for _p, cw in caps) + self.SEP for caps in self._caps(option, keys))
        return QtCore.QSize(max(base.width(), int(w)), max(base.height(), option.fontMetrics.height() + 8))


# ----------------------------------------------------------------------------- search bar
class KeySearchBar(QtWidgets.QWidget):
    """'Search commands or keys' and Record Keys. ``changed`` when the filter changes;
    :meth:`matches` says whether a row (texts, keys) is shown."""

    changed = QtCore.Signal()

    def __init__(self, parent=None, regex_key: str = "Search commands or keys"):
        super().__init__(parent)
        from ..style.theme import SearchField

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = SearchField("Search commands or keys", self, regex_key=regex_key, enter_filters=True)
        self.search.setToolTip("Part of a command's name, its menu or a key, for example ctrl+d")
        self.search.searchChanged.connect(lambda _t: self._typed())
        lay.addWidget(self.search, 1)
        self.record = QtWidgets.QPushButton("Record Keys")
        self.record.setCheckable(True)
        self.record.setAutoDefault(False)
        self.record.setToolTip("Press a key to list the commands that use it. Esc or a click here stops")
        self.record.toggled.connect(self._record_toggled)
        lay.addWidget(self.record)
        self.key = ""  # the recorded key (portable text)
        self.search.installEventFilter(self)

    def _typed(self) -> None:
        if not self.record.isChecked():
            self.key = ""
        self.changed.emit()

    def _record_toggled(self, on: bool) -> None:
        from ..help import RECORDS_KEYS_PROP

        self.search.setProperty(RECORDS_KEYS_PROP, bool(on))  # (F1 is recorded too)
        if on:
            self.search.setFocus(QtCore.Qt.OtherFocusReason)
            self.search.setPlaceholderText("Press a key")
        else:
            self.search.setPlaceholderText("Search commands or keys")

    def set_key(self, key: str) -> None:
        """Show only the commands that use ``key`` (what Record Keys does)."""
        self.key = keymap.norm(key)
        self.search.blockSignals(True)
        self.search.setText(keymap.native(self.key))
        self.search.blockSignals(False)
        self.search.cancel()
        self.changed.emit()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if (
            obj is self.search
            and self.record.isChecked()
            and e.type() in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress)
        ):
            k = e.key()
            if k in (
                QtCore.Qt.Key_Control,
                QtCore.Qt.Key_Shift,
                QtCore.Qt.Key_Alt,
                QtCore.Qt.Key_Meta,
                QtCore.Qt.Key_AltGr,
                QtCore.Qt.Key_unknown,
            ):
                e.accept()
                return True
            if e.type() == QtCore.QEvent.ShortcutOverride:
                e.accept()  # the key is ours, not a shortcut's
                return True
            if k == QtCore.Qt.Key_Escape and not e.modifiers():
                self.record.setChecked(False)
                return True
            self.set_key(QtGui.QKeySequence(e.keyCombination()).toString(QtGui.QKeySequence.PortableText))
            return True
        return False

    def matches(self, texts, keys) -> bool:
        if self.key:
            return self.key in keys
        m = self.search.matcher()
        if m.empty:
            return True
        natives = [keymap.native(k) for k in keys]
        return bool(m(*(list(texts) + list(keys) + natives)))


# ----------------------------------------------------------------------------- change dialog
#: the names of Change Shortcut's key fields, in order (a command shows every key it has)
KEY_FIELD_NAMES = ("Key:", "Second key:", "Third key:", "Fourth key:", "Fifth key:", "Sixth key:")


class ChangeShortcutDialog(QtWidgets.QDialog):
    """Change Shortcut: the keys of one command (see the module docstring). ``done_keys``
    carries the new user keys (command id -> keys) when it is accepted: what this window
    changed, put onto the keys ``current()`` gives then (a change made elsewhere while it was
    open stays)."""

    RESIZE = "width"
    done_keys = QtCore.Signal(dict)

    def __init__(
        self,
        parent,
        window,
        cid: str,
        preset: Optional[str] = None,
        user: Optional[Dict[str, List[str]]] = None,
        current: Optional[Callable[[], Dict[str, List[str]]]] = None,
    ):
        super().__init__(parent)
        from ..style.theme import set_note_role

        self._win = window
        self._current = current
        self._cid = cid
        self._preset = keymap.preset_name() if preset is None else preset
        self._user = dict(keymap.user_keys() if user is None else user)
        self.result_user: Optional[Dict[str, List[str]]] = None
        # (a command of the Simulate window has no action here: keymap.SIM_COMMANDS)
        self._sim = keymap.is_sim(cid)
        action = keymap.find_action(window, cid) if window is not None and not self._sim else None
        if action is None and window is not None and not self._sim:
            action = next((c.action for c in keymap.commands(window) if c.id == cid), None)
        self._action = action
        label = keymap.sim_label(cid) if self._sim else keymap.label_of(action) if action is not None else cid
        self.setWindowTitle("Change Shortcut - %s" % label)
        now = keymap.keys_now(window, cid, self._preset, self._user)
        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(label)
        set_note_role(head, "head", "font-weight: 600;")
        lay.addWidget(head)
        #: the keys shown when the window opened (OK without a change stores nothing)
        self._shown = list(now)
        #: keys beyond the last field (kept as they are)
        self._extra = list(now[len(KEY_FIELD_NAMES) :])
        #: one field per key (every key the command has, at least two)
        self.fields: List[QtWidgets.QKeySequenceEdit] = []
        self._form = QtWidgets.QFormLayout()
        shown = now[: len(KEY_FIELD_NAMES)]
        for k in list(shown) + [""] * max(0, 2 - len(shown)):
            self._add_field(k)
        self.key1, self.key2 = self.fields[0], self.fields[1]
        self.add_key = QtWidgets.QPushButton("Add Key")
        self.add_key.setAutoDefault(False)
        self.add_key.setToolTip("One more key for the same command")
        self.add_key.clicked.connect(self._add_empty_field)
        add_row = QtWidgets.QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.addWidget(self.add_key)
        add_row.addStretch(1)
        self._form.addRow("", add_row)
        lay.addLayout(self._form)
        self.conflict = QtWidgets.QLabel("")
        self.conflict.setWordWrap(True)
        set_note_role(self.conflict, "danger")
        self.conflict.hide()
        lay.addWidget(self.conflict)
        crow = QtWidgets.QHBoxLayout()
        crow.addStretch(1)
        self.use_anyway = QtWidgets.QPushButton("Use Anyway")
        self.use_anyway.setAutoDefault(False)
        self.use_anyway.setToolTip("Give the key to this command; the other command loses it")
        self.use_anyway.clicked.connect(self._use_anyway)
        # (the spec's Cancel: named for what it does, next to the window's own Cancel)
        self.cancel_conflict = QtWidgets.QPushButton("Choose Another")
        self.cancel_conflict.setToolTip("Clear the key and press another one")
        self.cancel_conflict.setAutoDefault(False)
        self.cancel_conflict.clicked.connect(self._drop_conflicting)
        crow.addWidget(self.use_anyway)
        crow.addWidget(self.cancel_conflict)
        self._conflict_row = QtWidgets.QWidget()
        self._conflict_row.setLayout(crow)
        crow.setContentsMargins(0, 0, 0, 0)
        self._conflict_row.hide()
        lay.addWidget(self._conflict_row)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.remove = bb.addButton("Remove", QtWidgets.QDialogButtonBox.ResetRole)
        self.remove.setToolTip("No key for this command")
        self.remove.clicked.connect(self._remove)
        self.reset = bb.addButton("Reset", QtWidgets.QDialogButtonBox.ResetRole)
        self.reset.setToolTip("The keymap's own key")
        self.reset.clicked.connect(self._reset)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        self._ok_button = bb.button(QtWidgets.QDialogButtonBox.Ok)
        lay.addWidget(bb)
        self._conflicts: list = []
        self._check()

    # -- the keys ---------------------------------------------------------------------
    def _add_field(self, key: str = "") -> QtWidgets.QKeySequenceEdit:
        ed = QtWidgets.QKeySequenceEdit()
        ed.setMaximumSequenceLength(1)
        ed.setClearButtonEnabled(True)
        ed.setKeySequence(QtGui.QKeySequence(key))
        ed.keySequenceChanged.connect(lambda _s: self._check())
        ed.installEventFilter(self)
        n = len(self.fields)
        ed.setToolTip(
            "Press the new key. Enter is OK and Esc is Cancel"
            if n == 0
            else "Another key for the same command (optional)"
        )
        self._form.insertRow(n, KEY_FIELD_NAMES[n], ed)
        self.fields.append(ed)
        return ed

    def _add_empty_field(self) -> None:
        if len(self.fields) >= len(KEY_FIELD_NAMES):
            return
        ed = self._add_field("")
        QtWidgets.QWidget.setTabOrder(self.fields[-2], ed)
        self._check()
        self.adjustSize()
        ed.setFocus(QtCore.Qt.OtherFocusReason)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        # (a key field records every key: plain Enter is OK and plain Esc is Cancel there, a
        # key with Ctrl, Alt or Shift is still recorded)
        if e.type() == QtCore.QEvent.KeyPress and any(obj is f for f in self.fields):
            mods = e.modifiers() & ~QtCore.Qt.KeypadModifier
            if mods == QtCore.Qt.NoModifier:
                if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    if self._ok_button.isEnabled():
                        self._ok()
                    return True
                if e.key() == QtCore.Qt.Key_Escape:
                    self.reject()
                    return True
        return super().eventFilter(obj, e)

    def keys(self) -> List[str]:
        out = []
        for ed in self.fields:
            k = keymap.norm(ed.keySequence())
            if k and k not in out:
                out.append(k)
        return out + [k for k in self._extra if k not in out]

    def _check(self) -> None:
        self.add_key.setEnabled(
            len(self.fields) < len(KEY_FIELD_NAMES) and all(keymap.norm(f.keySequence()) for f in self.fields)
        )
        if self._win is None and not self._sim:
            return
        self._conflicts = keymap.conflicts(self._win, self._cid, self.keys(), self._preset, self._user)
        by_id = {c.id: c for c in (keymap.sim_commands() if self._sim else keymap.commands(self._win))}
        texts, fixed = [], False
        for k, other in self._conflicts:
            if other == keymap.FIXED_PANEL:
                fixed = True
                texts.append("%s is a key of a panel's list. Choose another key." % keymap.native(k))
            elif not other:
                fixed = True
                texts.append(
                    (
                        "%s is a key of the simulated page. Choose another key."
                        if self._sim
                        else "%s is a key of the canvas. Choose another key."
                    )
                    % keymap.native(k)
                )
            else:
                name = by_id[other].label if other in by_id else other
                texts.append("%s is already %s." % (keymap.native(k), name))
        self.conflict.setText(" ".join(texts))
        self.conflict.setVisible(bool(texts))
        self._conflict_row.setVisible(bool(texts))
        self.use_anyway.setVisible(bool(texts) and not fixed)
        self._ok_button.setEnabled(not texts)

    def _drop_conflicting(self) -> None:
        bad = {k for k, _o in self._conflicts}
        for ed in self.fields:
            if keymap.norm(ed.keySequence()) in bad:
                ed.clear()
        self._check()

    def _finish(self, user: Dict[str, List[str]]) -> None:
        # only the commands this window changed, onto the keys as they are now
        changes = {k: user.get(k) for k in set(user) | set(self._user) if user.get(k) != self._user.get(k)}
        merged = dict(self._current() if self._current is not None else self._user)
        for k, v in changes.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = list(v)
        self.result_user = merged
        self.done_keys.emit(merged)
        self.accept()

    def _ok(self) -> None:
        if self._conflicts:
            return
        keys = self.keys()
        user = dict(self._user)
        if keys == self._shown:
            self._finish(user)  # nothing changed here: no key of your own
            return
        own = keymap.own_keys(self._win, self._cid, self._preset, user)
        if keys == own:
            user.pop(self._cid, None)  # the keymap's own keys: no user key needed
        else:
            user[self._cid] = keys
        self._finish(user)

    def _use_anyway(self) -> None:
        self._finish(keymap.take_keys(self._win, self._cid, self.keys(), self._user, self._preset))

    def _remove(self) -> None:
        user = dict(self._user)
        user[self._cid] = []
        self._finish(user)

    def _reset(self) -> None:
        self._finish(keymap.reset_command(self._win, self._cid, self._user))


def _store_and_apply(user: Dict[str, List[str]]) -> None:
    keymap.store_user_keys(user)
    keymap.apply_everywhere()


def open_change_shortcut(
    parent,
    command_id: str,
    window=None,
    preset: Optional[str] = None,
    user: Optional[Dict[str, List[str]]] = None,
    on_done: Optional[Callable[[dict], None]] = None,
    current: Optional[Callable[[], Dict[str, List[str]]]] = None,
):
    """Open Change Shortcut for ``command_id`` next to ``parent`` (the call does not block;
    the window it is opened from waits until it is closed, so only one is open there).
    ``on_done`` gets the new user keys (put onto ``current()``: by default the stored keys);
    without it they are stored and applied to every editor window."""
    window = window if window is not None else _main_window(parent)
    if current is None and on_done is None:
        current = keymap.user_keys
    dlg = ChangeShortcutDialog(parent, window, command_id, preset, user, current)
    dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    dlg.setWindowModality(QtCore.Qt.WindowModal)
    dlg.done_keys.connect(on_done if on_done is not None else _store_and_apply)
    pos = QtGui.QCursor.pos()
    dlg.adjustSize()
    screen = QtGui.QGuiApplication.screenAt(pos) or QtGui.QGuiApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        x = max(area.left(), min(pos.x() - dlg.width() // 2, area.right() - dlg.width()))
        y = max(area.top(), min(pos.y() + 12, area.bottom() - dlg.height()))
        dlg.move(x, y)
    dlg.show()
    dlg.key1.setFocus(QtCore.Qt.OtherFocusReason)
    return dlg


# ----------------------------------------------------------------------------- the page
class KeymapPage(QtWidgets.QWidget):
    """Settings > Keyboard (see the module docstring). ``immediate``: every change is stored
    and applied at once (the Keyboard Shortcuts window)."""

    changed = QtCore.Signal()
    presetChanged = QtCore.Signal(str)

    def __init__(self, window, parent=None, immediate: bool = False, table_key: str = "SettingsDialog/keys"):
        super().__init__(parent)
        from ..style.theme import note_label

        self._win = window
        self._immediate = immediate
        self._preset = keymap.preset_name()
        self._user = keymap.user_keys()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Keymap:"))
        self.preset = QtWidgets.QComboBox()
        for name, data in keymap.presets().items():
            self.preset.addItem(keymap.preset_title(name), name)
            self.preset.setItemData(
                self.preset.count() - 1, data.get("description", ""), QtCore.Qt.ToolTipRole
            )
        self.preset.setToolTip("A set of keys to start from. Your own keys stay on top")
        self.preset.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(20)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self._preset)))
        self.preset.currentIndexChanged.connect(self._preset_changed)
        top.addWidget(self.preset)
        self.preset_note = note_label("")
        top.addWidget(self.preset_note, 1)
        lay.addLayout(top)
        self.bar = KeySearchBar(self)
        self.bar.changed.connect(self._filter)
        lay.addWidget(self.bar)
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setAccessibleName("Keyboard shortcuts")
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(300)
        self.table.setItemDelegateForColumn(2, KeyCapDelegate(self.table))
        self.table.itemDoubleClicked.connect(lambda _i: self.change_current())
        self.table.installEventFilter(self)
        lay.addWidget(self.table, 1)
        self.bar.search.set_list_view(self.table)
        row = QtWidgets.QHBoxLayout()
        self.btn_change = QtWidgets.QPushButton("Change…")
        self.btn_reset = QtWidgets.QPushButton("Reset")
        self.btn_reset.setToolTip("The keymap's own keys for the selected command")
        self.btn_reset_all = QtWidgets.QPushButton("Reset All")
        self.btn_reset_all.setToolTip("Drop every key of your own")
        self.btn_export = QtWidgets.QPushButton("Export…")
        self.btn_export.setToolTip("Save the keymap and your own keys to a file")
        self.btn_import = QtWidgets.QPushButton("Import…")
        self.btn_import.setToolTip("Load keys saved with Export")
        for b, fn in (
            (self.btn_change, self.change_current),
            (self.btn_reset, self.reset_current),
            (self.btn_reset_all, self.reset_all),
            (self.btn_export, self._export),
            (self.btn_import, self._import),
        ):
            b.setAutoDefault(False)
            b.clicked.connect(fn)
        for b in (self.btn_change, self.btn_reset, self.btn_reset_all):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_import)
        lay.addLayout(row)
        self.refresh()
        from .. import tables

        # (the columns always fit the table's width, so a narrow Settings window shows every
        # column without a sideways scroll: Where and Source as wide as their words, the
        # menu and the keys a share, the command what is left)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setMinimumWidth(240)
        tables.make_resizable(
            self.table,
            table_key,
            stretch=0,
            fit_view=True,
            caps={1: 0.2, 2: 0.36},
            fixed={3: "Simulate", 4: "Default"},
            squeeze={1},
        )
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 10)
        self.table.selectionModel().currentRowChanged.connect(lambda *_a: self._buttons())
        self._buttons()

    # -- state ------------------------------------------------------------------------
    def state(self):
        return self._preset, dict(self._user)

    def set_state(self, preset: str, user: Dict[str, List[str]], emit: bool = True) -> None:
        self._preset = preset if preset in keymap.presets() else keymap.DEFAULT_PRESET
        self._user = dict(user)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self._preset)))
        self.preset.blockSignals(False)
        self.refresh()
        if emit:
            self._changed()

    def store(self) -> None:
        """The pending keymap into the settings (not applied: keymap.apply_everywhere)."""
        st = app_settings()
        st.setValue(keymap.KEYMAP_SETTING, self._preset)
        keymap.store_user_keys(self._user)

    def _changed(self) -> None:
        if self._immediate:
            self.store()
            keymap.apply_everywhere()
        self.changed.emit()

    def _preset_changed(self, _k: int) -> None:
        name = self.preset.currentData() or keymap.DEFAULT_PRESET
        if name == self._preset:
            return
        self._preset = name
        if self._immediate:
            keymap.set_preset(name)
        self.refresh()
        self.presetChanged.emit(name)
        self._changed()

    # -- the table --------------------------------------------------------------------
    def rows(self):
        """[(Command, keys, source)] of the pending keymap: the editor's commands, then the
        Simulate window's (keymap.SIM_COMMANDS)."""
        cmds = keymap.commands(self._win, fixed=True)
        eff, _n = keymap.resolve(self._win, self._preset, self._user)
        out = []
        for c in cmds:
            if c.fixed:
                out.append((c, [], "Fixed"))
                continue
            keys, source = eff.get(c.id, ([], "default"))
            out.append((c, keys, "User" if source == "user" else "Default"))
        sim, _n = keymap.sim_resolve(self._user)
        for c in keymap.sim_commands():
            keys, source = sim.get(c.id, ([], "default"))
            out.append((c, keys, "User" if source == "user" else "Default"))
        return out

    def refresh(self) -> None:
        cur = self.current_id()
        self.preset_note.setText(str(keymap.presets().get(self._preset, {}).get("description", "")))
        rows = self.rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, (c, keys, source) in enumerate(rows):
            crumb = c.crumb.replace(" > ", " › ")
            # (Edit > Delete: Del works in the window, its minus keys on the canvas only)
            note = keymap.scope_note(c.action, keys) if keys and not c.fixed else ""
            where = (
                keymap.SCOPE_NAMES.get(c.scope, c.scope)
                if c.fixed
                else keymap.where_name(c.action, keys, c.scope)
            )
            vals = (c.label, crumb, "" if keys or not c.fixed else c.what, where, source)
            for col, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                it.setData(ID_ROLE, c.id)
                if col == 2:
                    it.setData(KEYS_ROLE, keys)
                    it.setToolTip(keymap.keys_tip(None if c.fixed else c.action, keys, c.what))
                elif col == 3 and note:
                    it.setToolTip(note)
                if c.fixed:
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(r, col, it)
        self._filter()
        if cur:
            self.select(cur)

    def _filter(self) -> None:
        for r in range(self.table.rowCount()):
            texts = [self.table.item(r, c).text() for c in (0, 1, 2)]
            keys = self.table.item(r, 2).data(KEYS_ROLE) or []
            self.table.setRowHidden(r, not self.bar.matches(texts, keys))
        # (a selected row the search hid is not the one Change or Reset acts on)
        r = self.table.currentRow()
        if r >= 0 and self.table.isRowHidden(r):
            self.table.setCurrentCell(-1, -1)
            self.table.clearSelection()
        self._buttons()

    def shown_ids(self) -> List[str]:
        return [
            self.table.item(r, 0).data(ID_ROLE)
            for r in range(self.table.rowCount())
            if not self.table.isRowHidden(r)
        ]

    def current_id(self) -> str:
        r = self.table.currentRow()
        if r < 0 or self.table.item(r, 0) is None or self.table.isRowHidden(r):
            return ""
        return self.table.item(r, 0).data(ID_ROLE) or ""

    def select(self, cid: str) -> bool:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(ID_ROLE) == cid and not self.table.isRowHidden(r):
                self.table.setCurrentCell(r, 0)
                self.table.scrollToItem(self.table.item(r, 0))
                return True
        return False

    def _buttons(self) -> None:
        cid = self.current_id()
        editable = bool(cid) and not cid.startswith("canvas.")
        self.btn_change.setEnabled(editable)
        self.btn_reset.setEnabled(editable and cid in self._user)
        self.btn_reset_all.setEnabled(bool(self._user))

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if (
            obj is self.table
            and e.type() == QtCore.QEvent.KeyPress
            and e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
        ):
            self.change_current()
            return True
        return False

    # -- the commands -----------------------------------------------------------------
    def change_current(self):
        cid = self.current_id()
        if not cid or cid.startswith("canvas."):
            return None
        return self.change(cid)

    def change(self, cid: str):
        """Open Change Shortcut for ``cid`` (the result comes back to this page)."""
        self.select(cid)
        return open_change_shortcut(
            self,
            cid,
            window=self._win,
            preset=self._preset,
            user=self._user,
            on_done=self._done,
            current=lambda: dict(self._user),
        )

    def _done(self, user: dict) -> None:
        self._user = dict(user)
        self.refresh()
        self._buttons()
        self._changed()

    def reset_current(self) -> None:
        cid = self.current_id()
        if cid in self._user:
            self._done(keymap.reset_command(self._win, cid, self._user))

    def reset_all(self) -> None:
        if self._user:
            self._done({})

    def _export(self) -> None:
        from ..remember import file_path

        path = file_path(
            self,
            "Export Keyboard Shortcuts",
            "keys",
            "Keyboard files (*.json);;All files (*.*)",
            save=True,
            file_name="ctgedit-keys.json",
        )
        if path:
            self.export_to(path)

    def export_to(self, path: str) -> bool:
        try:
            keymap.export_keys(path, self._preset, self._user)
        except OSError as e:
            warn(self, "Export Keyboard Shortcuts", str(e))
            return False
        return True

    def _import(self) -> None:
        from ..remember import file_path

        path = file_path(
            self, "Import Keyboard Shortcuts", "keys", "Keyboard files (*.json);;All files (*.*)", save=False
        )
        if path:
            self.import_from(path)

    def import_from(self, path: str) -> bool:
        try:
            preset, user = keymap.read_keys_file(path)
        except keymap.KeysFileError as e:
            warn(self, "Import Keyboard Shortcuts", str(e))
            return False
        changed_preset = preset != self._preset
        self.set_state(preset, user)
        if changed_preset:
            self.presetChanged.emit(preset)
        return True
