"""Tools > Pack Libraries and Pack Database: confirm, and pick a project."""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtWidgets


class PackLibrariesDialog(QtWidgets.QDialog):
    """See the module notes."""

    RESIZE = "width"
    TITLE = "Pack Libraries"
    INTRO = (
        "Compact the symbol, genie and template libraries of the project (deleted elements are "
        "removed). Composite genie instances no page uses are removed from the composition caches "
        "too (you are asked first)."
    )
    WHAT = "The project whose libraries are packed"

    def __init__(self, parent=None, current_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        from .find_dialog import project_choices
        from ..prefs.settings import app_settings

        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(self.INTRO)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.project = QtWidgets.QComboBox()
        self.project.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.project.setToolTip(self.WHAT)
        projects, here = project_choices(current_dir)
        for name, folder in projects:
            self.project.addItem(name, folder)
        if current_dir and not here:
            # (a page outside the listed projects: its own folder)
            self.project.insertItem(0, os.path.basename(current_dir.rstrip("\\/")), current_dir)
            here = self.project.itemText(0)
        want = here or str(app_settings().value("updatepages/root", "") or "")
        k = self.project.findText(want)
        if k >= 0:
            self.project.setCurrentIndex(k)
        form.addRow("Project:", self.project)
        lay.addLayout(form)
        self.includes = QtWidgets.QCheckBox("Include the included projects")
        lay.addWidget(self.includes)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        ok = bb.button(QtWidgets.QDialogButtonBox.Ok)
        ok.setText("Pack")
        ok.setEnabled(self.project.count() > 0)
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setDefault(True)  # (it rewrites every library)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def project_dir(self) -> Optional[str]:
        d = self.project.currentData()
        return str(d) if d else None

    def include_chain(self) -> bool:
        return self.includes.isChecked()


class PackDatabaseDialog(PackLibrariesDialog):
    """Tools > Pack Database."""

    TITLE = "Pack Database"
    INTRO = (
        "Remove the deleted rows from the project's database tables and build their indexes "
        "again, as Studio's Pack does. Close Plant SCADA Studio and the Graphics Builder first."
    )
    WHAT = "The project whose database is packed"
