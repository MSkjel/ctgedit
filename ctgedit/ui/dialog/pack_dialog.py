"""Tools > Pack Libraries and Pack Database: confirm, and pick a project."""

from __future__ import annotations

from typing import List

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
    INCLUDES_WHAT = "the libraries"

    def __init__(self, parent=None, current_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        from ..pick_fields import ProjectScopeField

        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(self.INTRO)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.scope = ProjectScopeField(
            current_path, what=self.WHAT, includes_what=self.INCLUDES_WHAT, remember="pack"
        )
        form.addRow("Project:", self.scope)
        lay.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        ok = bb.button(QtWidgets.QDialogButtonBox.Ok)
        ok.setText("Pack")
        from ..style.theme import enable

        enable(ok, self.scope.has_projects(), "No project of this computer was found.")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setDefault(True)  # (it rewrites every library)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def project_dirs(self) -> List[str]:
        return self.scope.project_dirs()


class PackDatabaseDialog(PackLibrariesDialog):
    """Tools > Pack Database."""

    TITLE = "Pack Database"
    INTRO = (
        "Remove the deleted rows from the project's database tables and build their indexes "
        "again, as Studio's Pack does. Close Plant SCADA Studio and the Graphics Builder first."
    )
    WHAT = "The project whose database is packed"
    INCLUDES_WHAT = "the database tables"
