"""Settings > Export and Import."""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from .settings import (
    EXPORT_FORMAT,
    EXPORT_GROUP,
    GROUP_NAMES,
    LOCAL_PREFIXES,
    SettingsFileError,
    app_settings,
    key_group,
)

#: the groups an import takes unless the user ticks them
IMPORT_OFF = ("Recent pages and folders", "Window layout and workspaces")
MERGE, REPLACE = "merge", "replace"


def _portable(key: str) -> bool:
    return not key.startswith(EXPORT_GROUP + "/") and not key.startswith(LOCAL_PREFIXES)


def _ordered(groups: Iterable[str]) -> List[str]:
    gs = set(groups)
    return [g for g in GROUP_NAMES if g in gs]


def export_groups(
    path: str, groups: Optional[Iterable[str]] = None, overrides: Optional[dict] = None
) -> Tuple[int, List[str]]:
    """Write the settings of ``groups`` (None: all) to the INI file ``path``; ``overrides``
    = values written instead of the stored ones. (settings written, the groups they are
    in)."""
    from ... import __version__

    want = set(GROUP_NAMES if groups is None else groups)
    st = app_settings()
    values = {k: st.value(k) for k in st.allKeys() if _portable(k)}
    for k, v in (overrides or {}).items():
        if _portable(k):
            values[k] = v
    values = {k: v for k, v in values.items() if key_group(k) in want}
    # (written beside it first and put in its place once whole: a settings file there from
    # before stays as it was when the export fails)
    tmp = path + ".~export"
    if os.path.exists(tmp):
        os.remove(tmp)
    out = QtCore.QSettings(tmp, QtCore.QSettings.Format.IniFormat)
    for k, v in values.items():
        out.setValue(k, v)
    out.setValue(EXPORT_GROUP + "/format", EXPORT_FORMAT)
    out.setValue(EXPORT_GROUP + "/version", __version__)
    out.setValue(EXPORT_GROUP + "/groups", _ordered(want))
    out.sync()
    ok = out.status() == QtCore.QSettings.Status.NoError and os.path.isfile(tmp)
    del out  # (the file closed before it moves)
    try:
        if ok:
            os.replace(tmp, path)
    except OSError as e:
        raise SettingsFileError("Could not replace %s: %s" % (path, e)) from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not ok:
        raise SettingsFileError(
            "Could not write %s. Check that the folder exists and that you may write to it." % path
        )
    return len(values), _ordered({key_group(k) for k in values})


def file_groups(path: str) -> List[str]:
    """The groups a settings file has settings of (after checking it is one)."""
    src = _open(path)
    return _ordered({key_group(k) for k in src.allKeys() if _portable(k)})


def _open(path: str) -> QtCore.QSettings:
    if not os.path.isfile(path):
        raise SettingsFileError("%s does not exist." % path)
    src = QtCore.QSettings(path, QtCore.QSettings.Format.IniFormat)
    if src.status() != QtCore.QSettings.Status.NoError:
        raise SettingsFileError("%s could not be read as a settings file." % path)
    try:
        fmt = int(src.value(EXPORT_GROUP + "/format"))
    except TypeError, ValueError:
        fmt = None
    if fmt is None:
        raise SettingsFileError(
            "%s is not a settings file exported from ctgedit (Settings > Export)." % os.path.basename(path)
        )
    if fmt > EXPORT_FORMAT:
        raise SettingsFileError(
            "%s was exported by a newer ctgedit; update ctgedit to import it." % os.path.basename(path)
        )
    return src


def import_groups(
    path: str, groups: Optional[Iterable[str]] = None, mode: str = MERGE
) -> Tuple[int, List[str]]:
    """Take the settings of ``groups`` (None: all) from ``path`` (see the module docstring).
    (settings set, their groups). Nothing changes when the file is no settings export."""
    src = _open(path)
    want = set(GROUP_NAMES if groups is None else groups)
    values = {k: src.value(k) for k in src.allKeys() if _portable(k) and key_group(k) in want}
    st = app_settings()
    if mode == REPLACE:
        for k in st.allKeys():
            if _portable(k) and key_group(k) in want and k not in values:
                st.remove(k)
    for k, v in values.items():
        st.setValue(k, v)
    st.sync()
    return len(values), _ordered({key_group(k) for k in values})


def summary(n: int, groups: List[str], verb: str = "Imported") -> str:
    """'Imported 42 settings: Options, Styles.'"""
    if not n:
        return "%s no settings." % verb
    return "%s %d setting%s: %s." % (verb, n, "" if n == 1 else "s", ", ".join(groups))


class SettingsTransferDialog(QtWidgets.QDialog):
    """Export… / Import…: the groups to take and, for an import, Merge or Replace."""

    RESIZE = "none"

    def __init__(self, parent, mode: str = "export", available: Optional[List[str]] = None):
        super().__init__(parent)
        self.mode = mode
        self.setWindowTitle("Export Settings" if mode == "export" else "Import Settings")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Settings to export:" if mode == "export" else "Settings to import:"))
        self.boxes = {}
        for g in GROUP_NAMES:
            cb = QtWidgets.QCheckBox(g)
            on = mode == "export" or g not in IMPORT_OFF
            if available is not None and g not in available:
                cb.setEnabled(False)
                cb.setToolTip("The file has none")
                on = False
            cb.setChecked(on)
            cb.toggled.connect(lambda _on: self._ok_state())
            self.boxes[g] = cb
            lay.addWidget(cb)
        self.merge = self.replace = None
        if mode == "import":
            box = QtWidgets.QGroupBox("How")
            bl = QtWidgets.QVBoxLayout(box)
            self.merge = QtWidgets.QRadioButton("Merge: set only the settings in the file")
            self.replace = QtWidgets.QRadioButton(
                "Replace: also reset the settings of these groups the file does not have"
            )
            self.merge.setChecked(True)
            bl.addWidget(self.merge)
            bl.addWidget(self.replace)
            lay.addWidget(box)
        self.bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.bb.button(QtWidgets.QDialogButtonBox.Ok).setText("Export" if mode == "export" else "Import")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._ok_state()

    def _ok_state(self) -> None:
        self.bb.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(bool(self.groups()))

    def groups(self) -> List[str]:
        return [g for g, cb in self.boxes.items() if cb.isChecked() and cb.isEnabled()]

    def import_mode(self) -> str:
        return REPLACE if self.replace is not None and self.replace.isChecked() else MERGE
