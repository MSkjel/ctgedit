"""Help > About."""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets


def about_rows() -> List[Tuple[str, str]]:
    """(label, value) rows shown in the dialog and copied by Copy Details."""
    import PySide6
    from ... import buildinfo, crashlog

    info = buildinfo.build_info()
    rows = [
        ("Version", info["version"]),
        ("Commit", info["commit"] or "unknown (not built from a git checkout)"),
    ]
    if info.get("date"):
        rows.append(("Commit date", info["date"]))
    if info.get("built"):
        rows.append(("Built", info["built"]))
    rows += [
        ("Python", sys.version.split()[0]),
        ("Qt", "%s (PySide6 %s)" % (QtCore.qVersion(), PySide6.__version__)),
        (
            "Program folder",
            os.path.dirname(
                os.path.abspath(sys.executable if getattr(sys, "frozen", False) else buildinfo.PACKAGE_DIR)
            ),
        ),
        ("Logs", crashlog.log_dir()),
    ]
    return rows


def details_text() -> str:
    return "ctgedit\n" + "\n".join("%s: %s" % r for r in about_rows())


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About ctgedit")
        from ..style.theme import app_icon_image

        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QHBoxLayout()
        logo = QtWidgets.QLabel()
        logo.setPixmap(QtGui.QPixmap.fromImage(app_icon_image(64)))
        head.addWidget(logo, 0, QtCore.Qt.AlignTop)
        from ..style.theme import type_px

        # (the name in the type scale's title size at the text size now: it stays the
        # heading at any text size)
        title = QtWidgets.QLabel(
            "<b style='font-size:%dpx'>ctgedit</b><br>"
            "An editor for AVEVA Plant SCADA (Citect) graphics pages, symbols, "
            "genies and templates." % type_px("title")
        )
        title.setWordWrap(True)
        head.addWidget(title, 1)
        lay.addLayout(head)
        form = QtWidgets.QFormLayout()
        self._rows = about_rows()
        for label, value in self._rows:
            if os.sep in value:
                # (a folder: shown as far as it fits, selectable, the whole of it in the tool tip)
                v = QtWidgets.QLineEdit(value)
                v.setReadOnly(True)
                v.setFrame(False)
                v.setCursorPosition(0)
                v.setToolTip(value)
            else:
                v = QtWidgets.QLabel(value)
                v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            v.setObjectName("about_" + label.lower().replace(" ", "_"))
            form.addRow(label + ":", v)
        lay.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy = buttons.addButton("Copy Details", QtWidgets.QDialogButtonBox.ActionRole)
        copy.setToolTip("Copy the version, commit and system details")
        copy.clicked.connect(self.copy_details)
        logs = buttons.addButton("Open Log Folder", QtWidgets.QDialogButtonBox.ActionRole)
        logs.setToolTip("Open the folder with the crash reports and the log")
        logs.clicked.connect(self.open_logs)
        buttons.rejected.connect(self.reject)
        for b in (copy, logs):
            b.setAutoDefault(False)
        close = buttons.button(QtWidgets.QDialogButtonBox.Close)
        close.setDefault(True)
        close.setFocus()
        lay.addWidget(buttons)
        self.setMinimumWidth(460)

    def copy_details(self) -> None:
        QtWidgets.QApplication.clipboard().setText(details_text())

    def open_logs(self) -> None:
        from ... import crashlog

        from ..messages import open_folder

        open_folder(crashlog.log_dir())


def _user_folder_line() -> str:
    from ...io.citect_config import live_user_dir

    return "Active Plant SCADA user folder: %s" % (live_user_dir() or "not found")


def register_diagnostics() -> None:
    """Give infra/logsetup's diagnostics report the build / system details and the active
    user folder (it imports nothing above infra itself)."""
    from ...infra import logsetup

    logsetup.add_details_source(details_text)
    logsetup.add_details_source(_user_folder_line)
