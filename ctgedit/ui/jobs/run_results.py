"""The result window of a run that wrote files."""

from __future__ import annotations

import os
from typing import Iterable, Optional

from PySide6 import QtCore, QtWidgets


class RunResultsDialog(QtWidgets.QDialog):
    """See the module notes. ``lines`` = the summary (the first is its headline);
    ``attention`` = the lines that need the user (what was left undone: shown under the
    headline); ``backup`` = the run's backup folder ('' = none); ``compile_offer`` = pages
    changed, so a compile is offered. Every line is in the details, shown on demand."""

    RESIZE = "both"
    #: the result of exec() when Compile Project was clicked
    COMPILE = 2
    #: how much of a line needing attention is shown before the details (a list of names)
    SHORT = 180
    #: the lines needing attention shown before 'and N more in the details'
    MOST = 4
    DETAILS_KEY = "edit/run_details"

    def __init__(
        self,
        title: str,
        lines: Iterable[str],
        parent=None,
        backup: str = "",
        compile_offer: bool = False,
        attention: Iterable[str] = (),
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        lines = [str(x) for x in lines]
        self._text = "\n".join(lines)
        self.backup = backup or ""
        first = lines[0] if lines else ""
        attention = [str(x) for x in attention if str(x) and str(x) != first]
        lay = QtWidgets.QVBoxLayout(self)
        # the headline with what it means at a glance, then only what needs the user
        head = QtWidgets.QHBoxLayout()
        icon = QtWidgets.QLabel(self)
        from ..notify import level_icon

        icon.setPixmap(level_icon("warning" if attention else "success", 32).pixmap(32, 32))
        icon.setAlignment(QtCore.Qt.AlignTop)
        head.addWidget(icon)
        self.headline = QtWidgets.QLabel(first or "Done.", self)
        self.headline.setWordWrap(True)
        self.headline.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        from ..style.theme import font

        self.headline.setFont(font("body_strong"))
        head.addWidget(self.headline, 1)
        lay.addLayout(head)
        self.attention: list = []
        shown = attention[: self.MOST]
        for x in shown:
            lab = QtWidgets.QLabel("\u2022 " + self._short(x), self)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            lab.setToolTip(x if len(x) > self.SHORT else "")
            lay.addWidget(lab)
            self.attention.append(lab)
        more = len(attention) - len(shown)
        rest = len(lines) - 1 - len(shown)
        if more > 0:
            note = "%d more that need%s you in the details." % (more, "s" if more == 1 else "")
        elif not attention and rest > 0:
            note = "Nothing needs your attention."
        else:
            note = ""
        self.note = QtWidgets.QLabel(note, self)
        from ..style.theme import set_note_role

        set_note_role(self.note, "text_dim")
        self.note.setVisible(bool(note))
        lay.addWidget(self.note)
        # every line, on demand
        self._box = QtWidgets.QPlainTextEdit(self)
        self._box.setReadOnly(True)
        self._box.setPlainText(self._text)
        self._box.setAccessibleName("Result")
        self._box.setMinimumHeight(160)
        lay.addWidget(self._box, 1)
        self._spacer = QtWidgets.QWidget(self)
        self._spacer.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        lay.addWidget(self._spacer, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        self.details = QtWidgets.QPushButton("Show Details", self)
        self.details.setCheckable(True)
        self.details.setAutoDefault(False)
        self.details.setToolTip("Every line of the result")
        self.details.toggled.connect(self._show_details)
        bb.addButton(self.details, QtWidgets.QDialogButtonBox.ResetRole)
        # (after it is in the row: adding a button shows it)
        self.details.setVisible(len(lines) > 1)
        self._copy = bb.addButton("Copy", QtWidgets.QDialogButtonBox.ActionRole)
        self._copy.setToolTip("Copy the result to the clipboard")
        self._copy.clicked.connect(self.copy)
        self._log = bb.addButton("Open Log", QtWidgets.QDialogButtonBox.ActionRole)
        self._log.setToolTip("Open the activity log (it says what was done to each file)")
        self._log.clicked.connect(self.open_log)
        self._folder: Optional[QtWidgets.QPushButton] = None
        if self.backup:
            self._folder = bb.addButton("Open Backup Folder", QtWidgets.QDialogButtonBox.ActionRole)
            self._folder.setToolTip("Show the backup's files (Tools > Restore Backup puts them back)")
            self._folder.clicked.connect(self.open_backup_folder)
        self._compile: Optional[QtWidgets.QPushButton] = None
        if compile_offer:
            self._compile = bb.addButton("Compile Project", QtWidgets.QDialogButtonBox.ActionRole)
            self._compile.setToolTip(
                "Close this window and compile the project, so the runtime shows the changes"
            )
            self._compile.clicked.connect(lambda: self.done(self.COMPILE))
        bb.rejected.connect(self.reject)
        bb.button(QtWidgets.QDialogButtonBox.Close).setDefault(True)
        lay.addWidget(bb)
        # (the details as the user left them last time; one line only: nothing more to show)
        on = False
        if len(lines) > 1:
            try:
                from ..prefs.settings import app_settings

                on = app_settings().value(self.DETAILS_KEY, False, type=bool)
            except Exception:  # noqa: BLE001  # swallow-ok: a remembered state is a convenience
                on = False
        self.details.blockSignals(True)
        self.details.setChecked(on)
        self.details.blockSignals(False)
        self._show_details(on, store=False)

    def _short(self, line: str) -> str:
        """``line`` cut at a word before :attr:`SHORT` characters (a long list of names)."""
        if len(line) <= self.SHORT:
            return line
        cut = line[: self.SHORT].rsplit(" ", 1)[0].rstrip(",;:")
        return cut + " \u2026"

    def _show_details(self, on: bool, store: bool = True) -> None:
        self._box.setVisible(on)
        self._spacer.setVisible(not on)
        self.details.setText("Hide Details" if on else "Show Details")
        if store:
            try:
                from ..prefs.settings import app_settings

                app_settings().setValue(self.DETAILS_KEY, bool(on))
            except Exception:  # noqa: BLE001  # swallow-ok: a remembered state is a convenience
                pass
        self.adjustSize()
        self.resize(max(self.width(), 520), max(self.height(), 380) if on else self.height())

    def details_shown(self) -> bool:
        return not self._box.isHidden()

    def text(self) -> str:
        """The result as shown (tests)."""
        return self._text

    def copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self._text)

    @staticmethod
    def open_log() -> None:
        from ..notify import open_log

        open_log()

    def open_backup_folder(self) -> None:
        """Show the backup folder in Explorer (a seam: tests replace it)."""
        if self.backup and os.path.isdir(self.backup):
            from PySide6 import QtGui

            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.backup))

    def buttons(self) -> list:
        """The action buttons' labels, in order (tests)."""
        return [b.text() for b in (self._copy, self._log, self._folder, self._compile) if b is not None]
