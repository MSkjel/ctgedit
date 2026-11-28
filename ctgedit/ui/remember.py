"""Choices a dialog keeps between openings (settings remember/<name>): restored when the dialog
opens, kept when they change; and the folder each kind of file dialog used last (dir/<name>)."""

from __future__ import annotations

import os
from typing import Sequence

from PySide6 import QtWidgets

#: the settings group of the kept choices
PREFIX = "remember/"


def keep(widget: QtWidgets.QWidget, name: str) -> None:
    """Keep ``widget``'s choice under ``name``: a combo box its current item (by its data, else
    its text; when listed), a check box its state, a line edit its text (when it opens empty),
    a spin box its value."""
    from .prefs.settings import app_settings

    key = PREFIX + name
    st = app_settings()
    v = st.value(key, None)
    if isinstance(widget, QtWidgets.QComboBox):
        if v is not None:
            k = widget.findData(v)
            k = k if k >= 0 else widget.findText(str(v))
            if k >= 0:
                widget.setCurrentIndex(k)

        def chosen(_i, w=widget):
            data = w.currentData()
            st.setValue(key, data if data is not None else w.currentText())

        widget.currentIndexChanged.connect(chosen)
    elif isinstance(widget, QtWidgets.QAbstractButton):
        if v is not None:
            widget.setChecked(str(v).lower() in ("1", "true"))
        widget.toggled.connect(lambda on: st.setValue(key, bool(on)))
    elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
        if v is not None:
            try:
                widget.setValue(type(widget.value())(v))
            except TypeError, ValueError:  # swallow-ok: a garbled value: the box keeps its own
                pass
        widget.valueChanged.connect(lambda x: st.setValue(key, x))
    elif isinstance(widget, QtWidgets.QLineEdit) or hasattr(widget, "textChanged"):
        # (a line edit, or a field that is one inside: the expression fields)
        if v and not widget.text():
            widget.setText(str(v))
        widget.textChanged.connect(lambda t: st.setValue(key, t))
    else:
        raise TypeError("cannot keep the choice of a %s" % type(widget).__name__)


def store(name: str, value) -> None:
    """Keep ``value`` under ``name`` (a choice made outside the dialog that shows it)."""
    from .prefs.settings import app_settings

    app_settings().setValue(PREFIX + name, value)


def keep_one_of(buttons: Sequence[QtWidgets.QAbstractButton], name: str) -> None:
    """Keep which of the radio ``buttons`` is chosen (by its text) under ``name``."""
    from .prefs.settings import app_settings

    key = PREFIX + name
    st = app_settings()
    v = str(st.value(key, "") or "")
    for b in buttons:
        if v and b.text() == v:
            b.setChecked(True)
        b.toggled.connect(lambda on, b=b: on and st.setValue(key, b.text()))


#: the settings group of the folders the file dialogs used last
FOLDER_PREFIX = "dir/"


def last_folder(name: str, fallback: str = "") -> str:
    """The folder a file dialog for ``name`` used last; ``fallback`` (else the home folder)
    before there is one, or when it is gone."""
    from .prefs.settings import app_settings

    d = str(app_settings().value(FOLDER_PREFIX + name, "") or "")
    return d if d and os.path.isdir(d) else (fallback or os.path.expanduser("~"))


def keep_folder(name: str, path: str) -> None:
    """The file dialog for ``name`` chose ``path``: its folder is the next one's start."""
    from .prefs.settings import app_settings

    if path:
        app_settings().setValue(FOLDER_PREFIX + name, os.path.dirname(os.path.abspath(path)))


def file_path(
    parent, title: str, name: str, file_filter: str, *, save: bool, file_name: str = "", fallback: str = ""
) -> str:
    """A file to save (``save``) or open, the dialog starting in :func:`last_folder` of
    ``name``; '' when cancelled."""
    folder = last_folder(name, fallback)
    start = os.path.join(folder, file_name) if file_name else folder
    pick = QtWidgets.QFileDialog.getSaveFileName if save else QtWidgets.QFileDialog.getOpenFileName
    path, _f = pick(parent, title, start, file_filter)
    keep_folder(name, path)
    return path or ""
