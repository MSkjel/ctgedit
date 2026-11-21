"""Dialogs shown by the simulated page's Cicode."""

from __future__ import annotations

import contextlib
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from .. import help as _help

#: Windows' MessageBox button ids
IDOK, IDCANCEL, IDABORT, IDRETRY, IDIGNORE, IDYES, IDNO = 1, 2, 3, 4, 5, 6, 7

_SB = QtWidgets.QMessageBox.StandardButton
#: MB_* button sets (mode & 0xF) -> the buttons shown
_BUTTONS = {
    0: (_SB.Ok,),
    1: (_SB.Ok, _SB.Cancel),
    2: (_SB.Abort, _SB.Retry, _SB.Ignore),
    3: (_SB.Yes, _SB.No, _SB.Cancel),
    4: (_SB.Yes, _SB.No),
    5: (_SB.Retry, _SB.Cancel),
}
_BUTTON_ID = {
    _SB.Ok: IDOK,
    _SB.Cancel: IDCANCEL,
    _SB.Abort: IDABORT,
    _SB.Retry: IDRETRY,
    _SB.Ignore: IDIGNORE,
    _SB.Yes: IDYES,
    _SB.No: IDNO,
}
_ACCEPT = (IDOK, IDYES, IDRETRY, IDIGNORE)
#: a button the engine names -> the standard button
_NAMED = {
    "ok": _SB.Ok,
    "cancel": _SB.Cancel,
    "yes": _SB.Yes,
    "no": _SB.No,
    "abort": _SB.Abort,
    "retry": _SB.Retry,
    "ignore": _SB.Ignore,
}
#: MB_ICON* (mode & 0xF0) -> the icon
_ICONS = {
    0x10: QtWidgets.QMessageBox.Critical,
    0x20: QtWidgets.QMessageBox.Question,
    0x30: QtWidgets.QMessageBox.Warning,
    0x40: QtWidgets.QMessageBox.Information,
}


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except TypeError, ValueError:
        return default


def menu_choice(item: Dict[str, Any], menu_no: Optional[int], position: int) -> int:
    """The number DspPopupMenu returns for ``item``: its own ``id`` when the engine sent one,
    else menu x 100 + position (help: 'a return value of 201 indicates that the first item in
    Menu 2 was selected')."""
    for k in ("id", "choice", "value"):
        if isinstance(item.get(k), int):
            return int(item[k])
    return (menu_no or 0) * 100 + position


def build_menu(menu: QtWidgets.QMenu, items, menu_no: Optional[int] = 0) -> None:
    """Fill ``menu`` with a ctsim/dialog popupMenu's items (submenus, disabled, checked,
    separators); every action's data is its choice."""
    pos = 0
    for it in items or ():
        if not isinstance(it, dict):
            continue
        if it.get("separator"):
            menu.addSeparator()
            continue
        pos += 1
        label = str(it.get("label") or "").replace("&", "&&")
        sub_items = it.get("items")
        if isinstance(sub_items, list):
            # a link to a submenu (an empty one, a link to its own menu: shown disabled)
            sub = menu.addMenu(label)
            sub.setFont(menu.font())
            sub.setEnabled(bool(sub_items) and it.get("enabled", True) is not False)
            build_menu(sub, sub_items, it.get("menu") if isinstance(it.get("menu"), int) else None)
            continue
        act = menu.addAction(label)
        act.setEnabled(it.get("enabled", True) is not False)
        if it.get("checked"):
            act.setCheckable(True)
            act.setChecked(True)
        act.setData(menu_choice(it, menu_no, pos))


#: form field kinds shown as buttons (engine Interaction.cs DialogField.KindOf)
_BUTTON_KINDS = ("button", "ok", "cancel")
#: form field kinds the user does not change (answered as None: the engine keeps their text)
_SHOWN_ONLY = ("prompt", "label", "static", "text", "group", "none", "disabled")


def _truthy(value: str) -> bool:
    return str(value).strip().lower() not in ("", "0", "false")


class SimFormDialog(QtWidgets.QDialog):
    """A Cicode form (FormNew / FormInput / FormEdit ... then FormRead): a label and a field
    per row and the form's button fields (OK / Cancel when it has none). ``pressed(index)`` =
    a button field that is neither OK nor Cancel was pressed (the form stays open)."""

    RESIZE = "width"
    pressed = QtCore.Signal(int)

    def __init__(self, body: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(str(body.get("title") or "Form"))
        self.setProperty("_ctgNoGeometry", True)
        _help.set_help(self, "simulate#dialogs-the-page-shows")
        lay = QtWidgets.QVBoxLayout(self)
        if body.get("text"):
            lay.addWidget(QtWidgets.QLabel(str(body["text"])))
        form = QtWidgets.QFormLayout()
        lay.addLayout(form)
        # [kind, widget or None, the value the engine sent last], one per field
        self._fields: List[List[Any]] = []
        buttons: List[Tuple[str, str, int]] = []
        for k, f in enumerate(body.get("fields") or ()):
            if not isinstance(f, dict):
                continue
            kind = str(f.get("kind") or "edit").lower()
            label = str(f.get("label") or "")
            value = "" if f.get("value") is None else str(f.get("value"))
            if kind in _BUTTON_KINDS:
                buttons.append((kind, label or value, _int(f.get("index"), k)))
                self._fields.append([kind, None, value])
                continue
            if kind == "none":
                self._fields.append([kind, None, value])
                continue
            if kind in ("prompt", "label", "static", "text"):
                w = QtWidgets.QLabel(value or label)
                form.addRow(w)
            elif kind == "group":
                w = QtWidgets.QLabel(label or value)
                font = w.font()
                font.setBold(True)
                w.setFont(font)
                form.addRow(w)
            elif kind in ("check", "checkbox", "radio"):
                if kind == "radio":
                    w = QtWidgets.QRadioButton(label)
                else:
                    w = QtWidgets.QCheckBox(label)
                w.setChecked(_truthy(value))
                form.addRow("", w)
            elif kind in ("combo", "list", "combobox"):
                w = QtWidgets.QComboBox()
                w.setEditable(kind != "list")
                w.addItems([str(x) for x in f.get("items") or ()])
                w.setCurrentText(value)
                form.addRow(label, w)
            elif kind in ("textbox", "multiline"):
                w = QtWidgets.QPlainTextEdit(value)
                rows = max(2, min(10, _int(f.get("height"), 3)))
                w.setMinimumHeight(w.fontMetrics().lineSpacing() * rows + 12)
                form.addRow(label, w)
            else:
                w = QtWidgets.QLineEdit(value)
                if kind == "password":
                    w.setEchoMode(QtWidgets.QLineEdit.Password)
                if kind == "disabled":
                    w.setEnabled(False)
                width = _int(f.get("width"))
                if width > 0:
                    w.setMinimumWidth(min(600, w.fontMetrics().averageCharWidth() * width + 12))
                if _int(f.get("maxLength")) > 0:
                    w.setMaxLength(_int(f.get("maxLength")))
                form.addRow(label, w)
            self._fields.append([kind, w, value])
        self.choice: Optional[int] = None
        bb = QtWidgets.QDialogButtonBox()
        names = [str(b) for b in body.get("buttons") or () if b is not None]
        if buttons:
            # (the engine's forms: the button fields; 'buttons' only repeats their labels)
            for kind, label, index in buttons:
                if kind == "ok":
                    bb.addButton(label or "OK", QtWidgets.QDialogButtonBox.AcceptRole)
                elif kind == "cancel":
                    bb.addButton(label or "Cancel", QtWidgets.QDialogButtonBox.RejectRole)
                else:
                    b = bb.addButton(label or "Button", QtWidgets.QDialogButtonBox.ActionRole)
                    b.clicked.connect(lambda _c=False, i=index: self.pressed.emit(i))
            bb.accepted.connect(self.accept)
            bb.rejected.connect(self.reject)
        elif names:
            for k, name in enumerate(names):
                b = bb.addButton(name, QtWidgets.QDialogButtonBox.ActionRole)
                b.clicked.connect(lambda _c=False, k=k, name=name: self._press(k + 1, name))
        else:
            bb.setStandardButtons(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            bb.accepted.connect(self.accept)
            bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _press(self, number: int, name: str) -> None:
        self.choice = number
        if name.strip().lower() in ("cancel", "&cancel"):
            self.reject()
        else:
            self.accept()

    def update_fields(self, body: Dict[str, Any]) -> None:
        """The engine sent the form again (its code changed a field while it is shown): the
        fields whose value changed take it; what the user typed in the others stays."""
        if body.get("title"):
            self.setWindowTitle(str(body["title"]))
        fields = [f for f in body.get("fields") or () if isinstance(f, dict)]
        for slot, f in zip(self._fields, fields):
            _kind, w, old = slot
            value = "" if f.get("value") is None else str(f.get("value"))
            if value == old:
                continue
            slot[2] = value
            if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                w.setChecked(_truthy(value))
            elif isinstance(w, QtWidgets.QComboBox):
                w.setCurrentText(value)
            elif isinstance(w, QtWidgets.QLineEdit):
                w.setText(value)
            elif isinstance(w, QtWidgets.QLabel):
                w.setText(value or str(f.get("label") or ""))
            elif isinstance(w, QtWidgets.QPlainTextEdit):
                w.setPlainText(value)

    def values(self) -> List[Optional[str]]:
        out: List[Optional[str]] = []
        for kind, w, _value in self._fields:
            if w is None or kind in _SHOWN_ONLY:
                out.append(None)
            elif isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                out.append("1" if w.isChecked() else "0")
            elif isinstance(w, QtWidgets.QComboBox):
                out.append(w.currentText())
            elif isinstance(w, QtWidgets.QLineEdit):
                out.append(w.text())
            elif isinstance(w, QtWidgets.QPlainTextEdit):
                out.append(w.toPlainText())
            else:
                out.append(None)
        return out


class _Open:
    """One dialog on screen: its widget, its services, its engine window."""

    def __init__(self, svc, dialog_id, window: Optional[int], widget):
        self.svc = svc
        self.id = dialog_id
        self.window = window
        self.widget = widget
        self.done = False


class DialogHost(QtCore.QObject):
    """Shows the simulation's dialogs over the Simulate window (see the module docstring).
    ``find_page(svc, window)`` gives the tab (SimulatedPage) showing that engine window, or
    None."""

    def __init__(self, window: QtWidgets.QWidget, find_page):
        super().__init__(window)
        self._window = window
        self._find_page = find_page
        self._open: Dict[Tuple[int, Any], _Open] = {}
        #: called with (access, target) when a permission question is answered Always Allow
        #: (the window adds the DLL or database to the allowed list of the settings)
        self.on_always = None

    # -- events ----------------------------------------------------------------------
    def handle(self, svc, kind: str, body: Dict[str, Any]) -> None:
        if kind == "dialogClosed":
            self.closed(svc, body.get("id"))
        elif kind == "dialog":
            self.show(svc, body)

    def open_count(self) -> int:
        return len(self._open)

    def widgets(self) -> List[QtWidgets.QWidget]:
        return [o.widget for o in self._open.values()]

    def show(self, svc, body: Dict[str, Any]) -> None:
        did = body.get("id")
        if did is None:
            return
        key = (id(svc), did)
        if key in self._open:
            # the same dialog again: a form whose code changed a field while it is shown
            w = self._open[key].widget
            if isinstance(w, SimFormDialog):
                try:
                    w.update_fields(body)
                except RuntimeError:
                    pass
            return
        window = body.get("window")
        window = _int(window, -1) if window is not None else None
        kind = str(body.get("kind") or "").strip()
        page = self._find_page(svc, window)
        try:
            if kind == "popupMenu":
                w = self._menu(svc, did, body, page)
            elif kind == "message":
                w = self._message(svc, did, body)
            elif kind == "input":
                w = self._input(svc, did, body)
            elif kind == "form":
                w = self._form(svc, did, body)
            elif kind == "permission":
                w = self._permission(svc, did, body)
            else:
                svc.answer(did, ok=False, choice=0)
                return
        except Exception:  # noqa: BLE001 -- a dialog that cannot be shown is cancelled
            from ...infra import logsetup

            logsetup.note_exception("sim.dialogs.show")
            svc.answer(did, ok=False, choice=0)
            return
        self._open[key] = _Open(svc, did, window, w)

    def closed(self, svc, dialog_id) -> None:
        """dialogClosed: the engine stopped waiting; the dialog goes without an answer."""
        o = self._open.pop((id(svc), dialog_id), None)
        if o is not None:
            o.done = True
            target = getattr(o.widget, "target", None)
            if target and getattr(o.widget, "access", ""):
                # a permission question nobody answered in time: said, not just gone
                self._say(
                    "The question about %s was not answered in time: denied." % target, svc, level="warning"
                )
            self._close_widget(o.widget)

    def _say(self, text: str, svc=None, level: str = "") -> None:
        """A line in Outputs (the simulation the dialog belongs to) and the status bar (for
        its reading time and at least 8 s, times Settings > Appearance > Messages stay)."""
        from . import sources as S
        from ..notify import message_ms

        win = self._window
        with contextlib.suppress(Exception):
            win.statusBar().showMessage(text, message_ms(text, level or "info", 8000, scaled=False))
        add = None
        for p in win.pages() if hasattr(win, "pages") else ():
            sim = getattr(p, "sim", None)
            if svc is None or getattr(sim, "_session", None) is svc or getattr(sim, "viewer", None) is svc:
                add = getattr(sim, "_add_log", None)
                if add is not None:
                    break
        if add is None and hasattr(win, "current") and win.current() is not None:
            add = getattr(win.current().sim, "_add_log", None)
        if add is not None:
            with contextlib.suppress(Exception):
                add(
                    S.LogEntry(
                        getattr(win.current().sim, "_now", lambda: 0.0)(),
                        "log",
                        text,
                        level=level,
                        category="simulator",
                    )
                )

    def cancel_window(self, svc, window: Optional[int]) -> None:
        """The tab of ``window`` closed: its dialogs are cancelled (a tab that had no engine
        window yet has none: the dialogs of the simulation's other windows stay)."""
        if window is None:
            return
        for key, o in list(self._open.items()):
            if o.svc is svc and o.window == window:
                self._finish(key, ok=False, choice=0)

    def forget_closed(self) -> None:
        """Dialogs of a simulation whose connection ended go (nobody waits for them)."""
        for key, o in list(self._open.items()):
            if getattr(o.svc, "closed", False):
                self._open.pop(key, None)
                o.done = True
                self._close_widget(o.widget)

    def cancel_all(self) -> None:
        for key in list(self._open):
            self._finish(key, ok=False, choice=0)

    # -- answers ---------------------------------------------------------------------
    def _finish(self, key, **answer) -> None:
        o = self._open.pop(key, None)
        if o is None or o.done:
            return
        o.done = True
        o.svc.answer(o.id, **answer)
        self._close_widget(o.widget)

    @staticmethod
    def _close_widget(w) -> None:
        try:
            if isinstance(w, QtWidgets.QMenu):
                w.hide()
            else:
                w.done(0) if isinstance(w, QtWidgets.QDialog) and w.isVisible() else w.close()
            w.deleteLater()
        except RuntimeError:
            pass

    # -- the four kinds --------------------------------------------------------------
    def _point(self, body: Dict[str, Any], page) -> QtCore.QPoint:
        x, y = body.get("x"), body.get("y")
        canvas = getattr(page, "canvas", None)
        if canvas is not None and isinstance(x, (int, float)) and isinstance(y, (int, float)):
            vp = canvas.mapFromScene(QtCore.QPointF(float(x), float(y)))
            return canvas.viewport().mapToGlobal(vp)
        from PySide6 import QtGui

        return QtGui.QCursor.pos()

    def _menu(self, svc, did, body, page):
        parent = getattr(page, "canvas", None) or self._window
        menu = QtWidgets.QMenu(parent)
        menu.setAccessibleName("Popup menu of the page")
        top = body.get("menu")
        if isinstance(body.get("font"), dict):
            from ...render.sim_drawn import font_of

            menu.setFont(font_of(body["font"]))
        build_menu(menu, body.get("items") or (), top if isinstance(top, int) else 0)
        key = (id(svc), did)

        def picked(act):
            data = act.data()
            if isinstance(data, int):
                self._finish(key, choice=int(data))

        def hidden():
            # (triggered comes after the menu hides: answer 'cancelled' only when nothing was
            # picked by then)
            QtCore.QTimer.singleShot(0, self, lambda: self._finish(key, choice=0))

        for m in [menu] + menu.findChildren(QtWidgets.QMenu):
            m.triggered.connect(picked)  # (a submenu's pick answers once: popped)
        menu.aboutToHide.connect(hidden)
        menu.popup(self._point(body, page))
        return menu

    def _message(self, svc, did, body):
        mode = _int(body.get("mode"))
        box = QtWidgets.QMessageBox(self._window)
        _help.set_help(box, "simulate#dialogs-the-page-shows")
        box.setWindowTitle(str(body.get("title") or "Message"))
        box.setText(str(body.get("text") or ""))
        box.setIcon(_ICONS.get(mode & 0xF0, QtWidgets.QMessageBox.NoIcon))
        names = [str(b) for b in body.get("buttons") or () if b is not None]
        key = (id(svc), did)
        std = [_NAMED.get(n.replace("&", "").strip().lower()) for n in names]
        if names and all(b is not None for b in std):
            # (the engine names Message's own buttons: OK, or OK and Cancel)
            for b in std:
                box.addButton(b)
        elif names:
            for k, name in enumerate(names):
                box.addButton(name, QtWidgets.QMessageBox.ActionRole).setProperty("_ctgChoice", k + 1)
        else:
            for b in _BUTTONS.get(mode & 0xF, _BUTTONS[0]):
                box.addButton(b)
        box.setWindowModality(QtCore.Qt.WindowModal)

        def finished(_r):
            btn = box.clickedButton()
            if btn is not None and btn.property("_ctgChoice") is not None:
                n = int(btn.property("_ctgChoice"))
                self._finish(key, ok=n == 1, choice=n)
                return
            std = box.standardButton(btn) if btn is not None else _SB.Cancel
            bid = _BUTTON_ID.get(std, IDCANCEL)
            if btn is None and len(box.buttons()) == 1:
                bid = IDOK  # an OK-only box closed by its title bar
            self._finish(key, ok=bid in _ACCEPT, choice=bid)

        box.finished.connect(finished)
        box.open()
        return box

    def _permission(self, svc, did, body):
        """The simulator's own question (DLLs / SQL databases set to Ask): Allow Once, Allow
        for This Simulation, Always Allow or Deny; closing it denies."""
        from .outside_access import PermissionDialog

        dlg = PermissionDialog(body, self._window)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        key = (id(svc), did)

        def finished(_r):
            choice = dlg.choice
            if choice == "always" and self.on_always is not None:
                try:
                    self.on_always(dlg.access, dlg.target)
                except Exception:  # noqa: BLE001 -- the answer goes all the same
                    from ...infra import logsetup

                    logsetup.note_exception("sim.dialogs.on_always")
            self._finish(key, ok=choice != "deny", permission=choice)

        dlg.finished.connect(finished)
        dlg.open()
        return dlg

    def _input(self, svc, did, body):
        dlg = QtWidgets.QInputDialog(self._window)
        _help.set_help(dlg, "simulate#dialogs-the-page-shows")
        dlg.setWindowTitle(str(body.get("title") or "Input"))
        fields = body.get("fields") or ()
        first = fields[0] if fields and isinstance(fields[0], dict) else {}
        # (the engine: the prompt as 'text' and as the one field's label, the default its value)
        dlg.setLabelText(str(body.get("text") or first.get("label") or ""))
        default = body.get("value")
        if default is None:
            default = first.get("value")
        dlg.setTextValue("" if default is None else str(default))
        if _int(body.get("mode")) & 1:
            dlg.setTextEchoMode(QtWidgets.QLineEdit.Password)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        key = (id(svc), did)

        def finished(r):
            ok = r == QtWidgets.QDialog.Accepted
            self._finish(key, ok=ok, text=dlg.textValue() if ok else "")

        dlg.finished.connect(finished)
        dlg.open()
        return dlg

    def _form(self, svc, did, body):
        dlg = SimFormDialog(body, self._window)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        key = (id(svc), did)

        def finished(r):
            if r == QtWidgets.QDialog.Accepted:
                self._finish(key, ok=True, values=dlg.values(), choice=dlg.choice)
            else:
                self._finish(key, ok=False, choice=dlg.choice)

        def pressed(index):
            # a button field: its function runs and the form stays open (the engine sends
            # dialogClosed when the form closes)
            o = self._open.get(key)
            if o is not None and not o.done:
                o.svc.answer(o.id, press=int(index))

        dlg.finished.connect(finished)
        dlg.pressed.connect(pressed)
        dlg.open()
        return dlg
