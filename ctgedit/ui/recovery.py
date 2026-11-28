"""Crash recovery and backups."""

from __future__ import annotations

from ..infra.logsetup import note_exception as _note_exc

from ..infra.paths import path_key

import os
import time
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

from .prefs.settings import app_settings
from ..editing import ui_state

TICK_MS = 5000  # how often the controller looks (at most: a shorter pause halves it)
IDLE_SECONDS = 10.0  # a pause this long after an edit writes the copy (the default of
#                         Settings > Editing > Safety > Also after a pause in editing of:
#                         opt/autosave_idle_s, 0 = only by the minutes; ui/prefs/timing_options)
DEFAULT_MINUTES = 2  # ... and at the latest this often while editing


def _now() -> float:
    return time.monotonic()


def autosave_minutes() -> int:
    from .prefs.settings import AUTOSAVE_MINUTES_RANGE

    try:
        v = int(app_settings().value("opt/autosave_minutes", DEFAULT_MINUTES, type=int))
    except TypeError, ValueError:
        return DEFAULT_MINUTES
    return max(AUTOSAVE_MINUTES_RANGE[0], min(AUTOSAVE_MINUTES_RANGE[1], v))


def autosave_idle_seconds() -> float:
    """The pause in editing after which a recovery copy is written (s; 0 = none: only the
    minutes write one)."""
    from .prefs import timing_options

    return float(timing_options.AUTOSAVE_IDLE_S)


def tick_ms() -> int:
    """How often the controller looks: every TICK_MS, or half the idle pause when that is
    shorter (a copy comes within the pause the user chose), at least once a second."""
    idle = autosave_idle_seconds()
    if idle <= 0:
        return TICK_MS
    return max(1000, min(TICK_MS, int(idle * 500)))


def _signature(doc) -> tuple:
    """Changes whenever the document's content does (every edit, undo and
    redo moves its undo stacks)."""
    return (
        id(doc._undo[-1]) if doc._undo else 0,
        len(doc._undo),
        len(doc._redo),
        bool(doc.dirty),
        path_key(doc.path),
    )


class AutosaveController(QtCore.QObject):
    """Writes recovery copies of unsaved documents (see the module doc)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(tick_ms())
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        self._session_key = None
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._timer.stop)

    def apply_settings(self) -> None:
        """Settings > Editing > Safety changed: the controller looks as often as the idle
        pause needs (ui/prefs/timing_options' hook)."""
        ms = tick_ms()
        if ms != self._timer.interval():
            self._timer.setInterval(ms)

    @staticmethod
    def documents() -> list:
        from .main_window import MainWindow

        out = []
        for w in list(MainWindow.instances):
            try:
                out += [e["doc"] for e in w._docs]
            except RuntimeError:  # a window being destroyed
                continue
        return out

    def tick(self, force: bool = False) -> List[str]:
        """Write the copies that are due; returns the documents written."""
        from ..infra import autosave, logsetup

        minutes = autosave_minutes()
        written = []
        now = _now()
        for doc in self.documents():
            key = ui_state(doc).autosave_key
            # (an earlier version shown read-only has its page's path: its copy would be
            # offered as the page's own recovery)
            if not doc.dirty or ui_state(doc).version_view is not None:
                if key:
                    autosave.remove(key)
                    doc.ui.autosave_key = None
                continue
            if minutes <= 0 and not force:
                continue
            sig = _signature(doc)
            if sig != ui_state(doc).as_sig:
                doc.ui.as_sig = sig
                doc.ui.as_changed_at = now
                if doc.ui.as_saved_at is None:
                    doc.ui.as_saved_at = now
            if sig == ui_state(doc).as_saved_sig:
                continue
            pause = autosave_idle_seconds()
            idle = pause > 0 and now - (doc.ui.as_changed_at or now) >= pause
            due = now - (doc.ui.as_saved_at or now) >= minutes * 60.0
            if not (force or idle or due):
                continue
            try:
                snap = autosave.save_document(doc)
            except Exception as e:  # noqa: BLE001 -- never disturb editing
                logsetup.get("autosave").warning("could not write the recovery copy of %s: %s", doc.path, e)
                continue
            if key and key != snap.key:
                autosave.remove(key)  # the document moved (Save As)
            doc.ui.autosave_key = snap.key
            doc.ui.as_saved_sig = sig
            doc.ui.as_saved_at = now
            # (the status bar's document state tells when: ui/panels/status_items.DocStateButton)
            doc.ui.recovery_at = time.time()
            written.append(doc.path)
        if written:
            logsetup.get("autosave").info(
                "recovery copies written: %s", ", ".join(os.path.basename(p) for p in written)
            )
        self._write_session()
        return written

    def _write_session(self) -> None:
        """The tab list, merged with other ctgedit processes' (a crash keeps
        it; see MainWindow.merged_session_files)."""
        from .main_window import MainWindow

        try:
            # no editor window showing = starting or closing: the last
            # window's own save_prefs wrote the session, keep it
            if not any(w.isVisible() for w in MainWindow.instances):
                return
            st = app_settings()
            files = MainWindow.merged_session_files(st)
            # (the tabs' order, pins and library elements too: window/shell.session_tabs)
            import json

            tabs, _cur = MainWindow.session_tabs(st)
            rows = json.dumps([[r.get(k) for k in ("path", "element", "pinned")] for r in tabs])
            if (files, rows) != self._session_key:
                st.setValue("session/open_files", files)
                st.setValue("session/tabs", json.dumps(tabs))
                st.setValue("session/pinned", [r["path"] for r in tabs if r.get("pinned")])
                self._session_key = (files, rows)
        except Exception:  # noqa: BLE001 -- best effort
            _note_exc("recovery._write_session")
            pass

    @staticmethod
    def forget(doc) -> None:
        """``doc`` was saved / closed / discarded / reverted."""
        from ..infra import autosave

        try:
            autosave.forget_document(doc)
        except Exception:  # noqa: BLE001
            _note_exc("recovery.forget")
            pass
        doc.ui.autosave_key = None
        doc.ui.as_saved_sig = None
        doc.ui.recovery_at = None


_controller: List[AutosaveController] = []


def has_controller() -> bool:
    """A controller was made (controller() makes one: a settings hook asks first)."""
    return bool(_controller)


def controller() -> AutosaveController:
    """The application's controller (created on first use)."""
    if not _controller:
        _controller.append(AutosaveController(QtWidgets.QApplication.instance()))
    return _controller[0]


def when_text(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


class RecoveryDialog(QtWidgets.QDialog):
    """'ctgedit did not close normally': the recovery copies it kept."""

    RESTORE, DISCARD, LATER = "restore", "discard", "later"

    def __init__(self, snaps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recover Unsaved Changes")
        self.snaps = list(snaps)
        self.choice = self.LATER
        lay = QtWidgets.QVBoxLayout(self)
        msg = QtWidgets.QLabel(
            "ctgedit did not close normally last time. These pages had unsaved changes. "
            "Restore opens them as unsaved changes; nothing is written until you save."
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)
        self.list = QtWidgets.QListWidget()
        for s in self.snaps:
            where = os.path.basename(os.path.dirname(s.path)) or s.path
            what = "%s %s" % (s.element_kind or "element", s.title) if s.kind == "element" else s.title
            text = "%s   (%s, copy of %s)" % (what, where, when_text(s.saved_at))
            if s.changed_on_disk():
                text += "  (the page on disk has changed since)"
            it = QtWidgets.QListWidgetItem(text)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            it.setToolTip(s.path)
            self.list.addItem(it)
        lay.addWidget(self.list)
        bb = QtWidgets.QDialogButtonBox()
        self.btn_restore = bb.addButton("Restore Selected", QtWidgets.QDialogButtonBox.AcceptRole)
        self.btn_discard = bb.addButton("Discard Selected", QtWidgets.QDialogButtonBox.DestructiveRole)
        self.btn_later = bb.addButton("Decide Later", QtWidgets.QDialogButtonBox.RejectRole)
        self.btn_restore.clicked.connect(lambda: self._done(self.RESTORE))
        self.btn_discard.clicked.connect(lambda: self._done(self.DISCARD))
        self.btn_later.clicked.connect(lambda: self._done(self.LATER))
        lay.addWidget(bb)
        self.setMinimumWidth(560)

    def _done(self, choice: str) -> None:
        if choice == self.DISCARD and self.selected():
            from .messages import confirm

            if not confirm(
                self,
                "Discard Recovered Changes",
                "Delete the %d selected recovery cop%s? Those changes are then lost."
                % (len(self.selected()), "y" if len(self.selected()) == 1 else "ies"),
                "Discard",
            ):
                return
        self.choice = choice
        (self.accept if choice != self.LATER else self.reject)()

    def selected(self) -> list:
        return [s for k, s in enumerate(self.snaps) if self.list.item(k).checkState() == QtCore.Qt.Checked]


class RestoreBackupDialog(QtWidgets.QDialog):
    """Tools > Restore Backup: pick a backup Update Pages / Pack Libraries
    took and restore what it holds."""

    def __init__(self, backups_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore Backup")
        self.backups = list(backups_list)
        lay = QtWidgets.QVBoxLayout(self)
        msg = QtWidgets.QLabel(
            "Put back the pages or libraries that Update Pages or Pack Libraries changed. "
            "Compile the project afterwards."
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)
        split = QtWidgets.QHBoxLayout()
        self.list = QtWidgets.QListWidget()
        for b in self.backups:
            it = QtWidgets.QListWidgetItem(b.title)
            it.setToolTip("%s\n%.1f MB" % (b.path, b.size / 1048576.0))
            self.list.addItem(it)
        self.contents = QtWidgets.QListWidget()
        split.addWidget(self.list, 1)
        split.addWidget(self.contents, 1)
        lay.addLayout(split)
        self.list.currentRowChanged.connect(self._show)
        bb = QtWidgets.QDialogButtonBox()
        self.btn_restore = bb.addButton("Restore", QtWidgets.QDialogButtonBox.AcceptRole)
        self.btn_folder = bb.addButton(
            "Open Folder", QtWidgets.QDialogButtonBox.ResetRole
        )  # (left of Restore and Close)
        bb.addButton(QtWidgets.QDialogButtonBox.Close)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_folder.clicked.connect(self._open_folder)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumSize(640, 360)
        if self.backups:
            self.list.setCurrentRow(0)
        self.btn_restore.setEnabled(bool(self.backups))

    def current(self):
        k = self.list.currentRow()
        return self.backups[k] if 0 <= k < len(self.backups) else None

    def _show(self, _k: int) -> None:
        self.contents.clear()
        b = self.current()
        if b is None:
            return
        for e in b.entries:
            if e.get("page"):
                proj = os.path.basename(e.get("project", ""))
                text = "%s  (%s)" % (e.get("page", "?"), proj) if proj else e.get("page", "?")
            else:
                text = os.path.basename(e.get("original", "")) or "?"
            self.contents.addItem(text)

    def _open_folder(self) -> None:
        from .messages import open_folder

        b = self.current()
        if b is not None:
            open_folder(b.path)

    def _restore(self) -> None:
        b = self.current()
        if b is None:
            return
        n = len(b.entries)
        from ..infra.backups import KINDS

        what = b.noun
        from .messages import confirm

        if confirm(
            self,
            "Restore Backup",
            "Put the %d %s of this backup back as %s before %s ran? Changes made "
            "to %s since are lost."
            % (
                n,
                what,
                "it was" if n == 1 else "they were",
                KINDS.get(b.kind, b.kind),
                "it" if n == 1 else "them",
            ),
            "Restore",
        ):
            self.chosen = b
            self.accept()

    chosen: Optional[object] = None
