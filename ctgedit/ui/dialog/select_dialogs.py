"""Edit > Select > Select Objects Using Tag."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class SelectByTagDialog(QtWidgets.QDialog):
    """A tag field with the page's tag completion; Select selects every object that names
    the tag (MainWindow.select_using_tag)."""

    #: (the tag field grows with the window)
    RESIZE = "width"

    def __init__(self, parent=None, names=None, tag: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Select Objects Using Tag")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(
            QtWidgets.QLabel(
                "Selects every object whose expressions, commands or genie parameters name the tag."
            )
        )
        form = QtWidgets.QFormLayout()
        self.field = self._tag_field(names)
        self.field.setText(tag)
        self.field.selectAll()
        self.field.setMinimumWidth(260)
        self.field.setAccessibleName("Tag")
        form.addRow("Tag:", self.field)
        lay.addLayout(form)
        B = QtWidgets.QDialogButtonBox
        self.buttons = B(B.Cancel)
        self._ok = self.buttons.addButton("Select", B.AcceptRole)
        self._ok.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.field.textChanged.connect(self._sync)
        self._sync()
        self.field.setFocus(QtCore.Qt.OtherFocusReason)

    @staticmethod
    def _tag_field(names) -> QtWidgets.QLineEdit:
        """The scope's tag completion (the Wizard's Insert Tag too) when there is one."""
        try:
            from ..objprops.expression import ExpressionContext, ExpressionField, TagCompletionModel
        except ImportError:  # swallow-ok: a plain field then
            return QtWidgets.QLineEdit()
        if isinstance(names, TagCompletionModel):
            w = ExpressionField(ExpressionContext(names.project_dir), wizard=("tag",), field_kind="tag")
            w.set_names(names)
            return w
        return QtWidgets.QLineEdit()

    def _sync(self) -> None:
        self._ok.setEnabled(bool(self.tag()))

    def tag(self) -> str:
        return self.field.text().strip()
