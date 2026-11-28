"""Error boxes: what went wrong, what to do, and details for a bug report."""

from __future__ import annotations

import contextlib
import traceback
import types
from typing import Iterable, Optional

from PySide6 import QtCore, QtWidgets

#: the log lines the details show
LOG_LINES = 12


def details_text(exc: Optional[BaseException] = None, lines: int = LOG_LINES) -> str:
    """The error's type and message, its last frames and the end of the activity log."""
    from ..infra import logsetup

    out = []
    if exc is not None:
        out.append("%s: %s" % (type(exc).__name__, exc))
        tb = traceback.extract_tb(exc.__traceback__)[-3:] if exc.__traceback__ else []
        for fr in tb:
            out.append(
                "  at %s line %d, in %s"
                % (fr.filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], fr.lineno or 0, fr.name)
            )
    tail = logsetup.tail(logsetup.log_path(), lines)
    if tail:
        out.append("")
        out.append("The end of the activity log (%s):" % logsetup.log_path())
        out.extend(tail)
    return "\n".join(out)


def copy_details(text: str, documents: Iterable = ()) -> str:
    """The box's details and the diagnostics of the open pages, on the clipboard."""
    from ..infra import logsetup

    full = text + "\n\n" + logsetup.diagnostics_text(list(documents))
    QtWidgets.QApplication.clipboard().setText(full)
    return full


def _open_documents() -> list:
    try:
        from .main_window import MainWindow

        return [e["doc"] for w in MainWindow.editor_windows() for e in getattr(w, "_docs", [])]
    except Exception:  # noqa: BLE001  # swallow-ok: no editor windows (a tool)
        return []


class ErrorBox(QtWidgets.QMessageBox):
    """The box :func:`error_box` shows (made here so tests can answer it: :meth:`run`)."""

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        exc=None,
        next_steps=(),
        actions=(),
        icon=QtWidgets.QMessageBox.Warning,
    ):
        super().__init__(icon, title, text, QtWidgets.QMessageBox.NoButton, parent)
        steps = [s.strip() for s in next_steps or () if s and s.strip()]
        if steps:
            self.setInformativeText("\n".join(steps))
        self.details = details_text(exc)
        if self.details:
            self.setDetailedText(self.details)
        self.chosen: Optional[str] = None
        self._actions = []
        for a in actions or ():
            label, fn = a[0], (a[1] if len(a) > 1 else None)
            b = self.addButton(label, QtWidgets.QMessageBox.ActionRole)
            self._actions.append((b, label, fn))
        self.copy_btn = self.addButton("Copy Details", QtWidgets.QMessageBox.HelpRole)
        self.copy_btn.setToolTip("Copy the details and the open pages' diagnostics for a problem report")
        self.log_btn = self.addButton("Open Log", QtWidgets.QMessageBox.HelpRole)
        self.log_btn.setToolTip("Open ctgedit's activity log")
        self.ok_btn = self.addButton(QtWidgets.QMessageBox.Ok)
        self.setDefaultButton(self.ok_btn)
        self.setEscapeButton(self.ok_btn)
        for b in (self.copy_btn, self.log_btn):
            # Copy Details and Open Log keep the box open: a QMessageBox closes on any of
            # its buttons, so these are taken from it (it never sees their click; its
            # title bar close, Esc and OK close it)
            b.clicked.disconnect()
            b.clicked.connect(lambda _=False, b=b: self._help_clicked(b))

    def _help_clicked(self, b) -> None:
        if b is self.copy_btn:
            copy_details("%s\n%s\n\n%s" % (self.windowTitle(), self.text(), self.details), _open_documents())
            self.copy_btn.setText("Copied")
        else:
            from .notify import open_log

            open_log()

    def run(self) -> Optional[str]:
        self.exec()
        return self._chosen_action()

    def _chosen_action(self) -> Optional[str]:
        clicked = self.clickedButton()
        for b, label, fn in self._actions:
            if clicked is b:
                self.chosen = label
                if fn is not None:
                    fn()
                return label
        return None

    def show_modeless(self) -> None:
        """The box shown without blocking (a start-up note): its actions run when
        their button is clicked, and it is deleted when closed."""
        self.setModal(False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.buttonClicked.connect(lambda _b: self._chosen_action())
        self.show()


def error_box(
    parent,
    title: str,
    text: str,
    exc: Optional[BaseException] = None,
    next_steps=(),
    actions=(),
    icon=QtWidgets.QMessageBox.Warning,
) -> Optional[str]:
    """Show an error (see the module notes); the label of the action chosen, else None."""
    if exc is not None:
        from ..infra import logsetup

        logsetup.note_exception("messages.error_box", exc)
    box = ErrorBox(parent, title, text, exc, next_steps, actions, icon)
    return _exec(box)


def info_box(parent, title: str, text: str, next_steps=(), actions=()) -> Optional[str]:
    """A result box (Information) with the actions a result calls for ('Compile
    Project', 'Open Backup Folder'), Copy Details and Open Log: a result that names a folder
    or the log offers them. The label of the action chosen, else None."""
    return error_box(
        parent, title, text, next_steps=next_steps, actions=actions, icon=QtWidgets.QMessageBox.Information
    )


def list_box(parent, title: str, text: str, items, warning: bool = True) -> None:
    """A result with a list behind it: ``text`` says what happened in a sentence or two, the
    first item is shown when it is the only one, and the whole list is behind Show Details
    (never a wall of reasons by default). A seam: tests replace it."""
    items = [str(x) for x in items if str(x)]
    if _unanswered():
        # (the tests: a box they did not replace fails at once, never waits)
        raise RuntimeError("an unexpected message: %s: %s %s" % (title, text, items))
    body = text
    if len(items) == 1:
        body = "%s\n\n%s" % (text, items[0])
    box = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Information,
        title,
        body,
        QtWidgets.QMessageBox.Ok,
        parent,
    )
    if len(items) > 1:
        box.setDetailedText("\n".join(items))
    box.exec()


def ask_text(parent, title: str, label: str, text: str = "") -> Optional[str]:
    """A line of text asked for, ``text`` given to edit; None when cancelled."""
    value, ok = _input("getText", parent, title, label, QtWidgets.QLineEdit.Normal, text)
    return value if ok else None


def ask_item(
    parent, title: str, label: str, items, current: int = 0, editable: bool = False
) -> Optional[str]:
    """One of ``items`` chosen in a list (``current`` first; ``editable``: or a text typed);
    None when cancelled."""
    value, ok = _input("getItem", parent, title, label, list(items), current, editable)
    return value if ok else None


def _input(name: str, parent, title: str, label: str, *args):
    import os

    fn = getattr(QtWidgets.QInputDialog, name)
    if isinstance(fn, types.BuiltinFunctionType) and os.environ.get("CTG_NO_MODAL_QUESTIONS") == "1":
        # (the tests: a question they did not answer fails at once, never waits)
        raise RuntimeError("an unanswered question: %s: %s" % (title, label))
    return fn(parent, title, label, *args)


def warn(parent, title: str, text: str) -> None:
    """A notice that something was refused or could not be done, with OK."""
    _notice("warning", parent, title, text)


def inform(parent, title: str, text: str) -> None:
    """A notice of what happened, with OK."""
    _notice("information", parent, title, text)


def _notice(kind: str, parent, title: str, text: str) -> None:

    fn = _stand_in(kind)
    if fn is not None:
        fn(parent, title, text)
        return
    if _unanswered():
        # (the tests: a notice they did not expect fails at once, never waits)
        raise RuntimeError("an unexpected message: %s: %s" % (title, text))
    getattr(QtWidgets.QMessageBox, kind)(parent, title, text)


def choose(
    parent,
    title: str,
    text: str,
    actions,
    default: Optional[str] = None,
    detail: str = "",
    warning: bool = False,
) -> Optional[str]:
    """A question answered by buttons named for what they do (``actions``: 'Replace All 3',
    'Only the Selected One'), never Yes / No, and Cancel. ``default``: the action Enter
    picks (None: Cancel); ``detail``: a second paragraph; ``warning``: its icon is the
    warning's. The action clicked, None for Cancel. A seam: tests replace it."""
    if _unanswered():
        # (the tests: a question they did not answer fails at once, never waits)
        raise RuntimeError("an unanswered question: %s: %s" % (title, text))
    icon = QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Question
    box = QtWidgets.QMessageBox(icon, title, text, QtWidgets.QMessageBox.NoButton, parent)
    if detail:
        box.setInformativeText(detail)
    buttons = {box.addButton(a, QtWidgets.QMessageBox.AcceptRole): a for a in actions}
    cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(next((b for b, a in buttons.items() if a == default), cancel))
    box.setEscapeButton(cancel)
    box.exec()
    return buttons.get(box.clickedButton())


def ask_with_again(
    parent,
    title: str,
    text: str,
    actions,
    dismiss: str,
    *,
    detail: str = "",
    default: Optional[str] = None,
    icon: str = "question",
    again: bool = True,
    properties=None,
    exec_=None,
):
    """A question answered by the buttons named for what they do (``actions``) or ``dismiss``
    ('Later', 'Not Now'), with a 'Don't ask again' box (``again``). (the action clicked, or None
    for ``dismiss`` or Esc; whether 'Don't ask again' was ticked). ``default``: the action
    Enter picks (None: ``dismiss``); ``icon``: 'question', 'information' or 'warning';
    ``properties``: dynamic properties of the box; ``exec_``: shows the box (a seam: tests
    answer it)."""
    M = QtWidgets.QMessageBox
    icons = {"question": M.Question, "information": M.Information, "warning": M.Warning}
    box = M(icons.get(icon, M.Question), title, text, M.NoButton, parent)
    for k, v in (properties or {}).items():
        box.setProperty(k, v)
    if detail:
        box.setInformativeText(detail)
    buttons = {box.addButton(a, M.AcceptRole if i == 0 else M.ActionRole): a for i, a in enumerate(actions)}
    later = box.addButton(dismiss, M.RejectRole)
    box.setEscapeButton(later)
    box.setDefaultButton(next((b for b, a in buttons.items() if a == default), later))
    tick = None
    if again:
        tick = QtWidgets.QCheckBox("Don't ask again", box)  # (owned by the box)
        box.setCheckBox(tick)
    (exec_ or _exec_box)(box)
    return buttons.get(box.clickedButton()), bool(tick is not None and tick.isChecked())


def _exec_box(box) -> None:
    if _unanswered():
        raise RuntimeError("an unanswered question: %s: %s" % (box.windowTitle(), box.text()))
    box.exec()


def confirm(
    parent,
    title: str,
    text: str,
    action: str,
    safe_default: bool = True,
    detail: str = "",
    warning: bool = False,
) -> bool:
    """A question answered by a button named for what it does (``action``: 'Delete') or
    Cancel; ``safe_default``: Enter is Cancel (an action that discards or removes). True
    when ``action`` was clicked (:func:`choose`)."""
    return choose(parent, title, text, [action], None if safe_default else action, detail, warning) == action


def with_extension(parent, title: str, path: str, ext: str) -> Optional[str]:
    """``path`` from a save dialog with ``ext`` ('.png') added when it has none. The dialog
    asked about replacing the name it was given only, so a file that already has the name
    with ``ext`` is asked about here. None when the user does not want it replaced."""
    import os

    if path.lower().endswith(ext.lower()):
        return path
    full = path + ext
    if os.path.exists(full) and not confirm(
        parent, title, "%s already exists. Replace it?" % os.path.basename(full), "Replace"
    ):
        return None
    return full


def open_folder(path: str) -> None:
    """Open ``path`` (a folder, made when it is missing) in Explorer. A seam: tests replace
    it."""
    import os

    with contextlib.suppress(OSError):
        os.makedirs(path, exist_ok=True)
    from PySide6 import QtGui

    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


def reveal(path: str) -> None:
    """Show the file ``path`` selected in Explorer; its folder when the file is not there."""
    import os
    import subprocess

    if os.path.exists(path) and os.name == "nt":
        with contextlib.suppress(OSError):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])  # noqa: S603,S607
            return
    open_folder(os.path.dirname(os.path.abspath(path)))


def open_folder_action(label: str, path: str) -> tuple:
    """An ``actions`` entry of a box: ``label`` opens ``path`` in Explorer."""
    return (label, lambda: open_folder(path))


def open_log_button(parent=None) -> QtWidgets.QToolButton:
    """A dialog's 'Open Log' button, for a status line that reports a failure
    ('The closed pages could not be searched.' with the button beside it, never '(see the
    log)'). Hidden until the dialog shows it."""
    b = QtWidgets.QToolButton(parent)
    b.setText("Open Log")
    b.setToolTip("Open ctgedit's activity log, which says what went wrong")
    b.setAutoRaise(True)
    b.setVisible(False)

    def run():
        from .notify import open_log

        open_log()

    b.clicked.connect(run)
    return b


def _unanswered() -> bool:
    """A box would wait for an answer no one gives: the tests (CTG_NO_MODAL_QUESTIONS=1) when
    no stand-in for QMessageBox.exec answers the boxes."""
    import os

    return os.environ.get("CTG_NO_MODAL_QUESTIONS") == "1" and not isinstance(
        QtWidgets.QMessageBox.exec, types.FunctionType
    )


def _stand_in(name: str):
    """A test's stand-in for QMessageBox.<name>, else None. Qt's own static boxes are built
    in functions; a stand-in is Python's (a lambda, a mock). Told by its kind, not by the
    object seen at import: this module may first be imported while a stand-in is set."""
    fn = getattr(QtWidgets.QMessageBox, name, None)
    return None if fn is None or isinstance(fn, types.BuiltinFunctionType) else fn


def _exec(box: ErrorBox) -> Optional[str]:
    """Show the box (a seam: tests answer it). A test that stands in for QMessageBox.warning
    (critical, information) gets the box's parent, title and text (with its next steps)
    instead."""
    name = {QtWidgets.QMessageBox.Critical: "critical", QtWidgets.QMessageBox.Information: "information"}.get(
        box.icon(), "warning"
    )
    fn = _stand_in(name)
    if fn is not None:
        text = box.text() + ("\n\n" + box.informativeText() if box.informativeText() else "")
        fn(box.parentWidget(), box.windowTitle(), text)
        box.deleteLater()
        return None
    return box.run()
