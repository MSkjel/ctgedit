"""The OK / Cancel (/ Apply) buttons of a dialog."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtWidgets


def dialog_buttons(
    dlg: QtWidgets.QDialog,
    *,
    ok: str = "",
    accept: Optional[Callable] = None,
    reject: Optional[Callable] = None,
    apply: Optional[Callable] = None,
) -> QtWidgets.QDialogButtonBox:
    """OK and Cancel, with Apply when ``apply`` is given: OK named after its action (``ok``,
    'Paste', 'Resize') when that is clearer; ``accept`` / ``reject`` / ``apply`` run on the
    buttons (by default the dialog's accept() and reject()). Add it to the dialog's layout."""
    B = QtWidgets.QDialogButtonBox
    box = B(B.Ok | B.Cancel | (B.Apply if apply is not None else B.NoButton), dlg)
    if ok:
        box.button(B.Ok).setText(ok)
    box.accepted.connect(accept or dlg.accept)
    box.rejected.connect(reject or dlg.reject)
    if apply is not None:
        box.button(B.Apply).clicked.connect(apply)
    return box
