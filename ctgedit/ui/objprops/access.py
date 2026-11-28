"""Access > General, Disable and Identification."""

from __future__ import annotations

import os

from PySide6 import QtGui, QtWidgets

from ..style.theme import set_note_role
from .fields import (
    GREY_STYLES,
    L_AX_CLASS,
    L_AX_NAME,
    L_COND,
    L_DESC,
    L_NAME,
    L_SHORT,
    L_TOOLTIP,
    Field,
    Sub,
)
from ...infra.logsetup import note_exception as _note_exc

GENERAL = Sub(
    "General",
    [
        Field("Name:", "__AN_NAME__", "name", limit=L_NAME),
        Field("Description:", "DESC", "text", limit=L_DESC),
        Field("Tool tip:", "COMMENT", "tooltip", limit=L_TOOLTIP),
        Field("Object area:", "AREA", "text", limit=L_SHORT),
        Field("Privilege level:", "PRIV", "text", limit=L_SHORT),
        Field("Log device:", "DEVICE", "text", limit=L_SHORT),
    ],
    family=("__AN_NAME__", "DESC", "COMMENT", "AREA", "PRIV", "DEVICE"),
)
DISABLE = Sub(
    "Disable",
    [
        Field("Disable when:", "DISABLED", limit=L_COND),
        Field("Disable on insufficient area or privilege", "DISMODE", "check", on="TRUE", loose=True),
        Field("Disable style:", "GREY_STYLE", "radio", choices=GREY_STYLES),
    ],
    family=("DISABLED", "DISMODE", "GREY_STYLE"),
)
IDENTIFICATION = Sub(
    "Identification",
    [
        Field("Object name:", "ACTIVEXEXT_*NAME", "text", limit=L_AX_NAME),
        Field("Event class:", "ACTIVEXEXT_*EVENTCLASS", "text", limit=L_AX_CLASS),
    ],
    family=("ACTIVEXEXT_*NAME", "ACTIVEXEXT_*EVENTCLASS"),
)
PERSIST = Field(
    "Persist ActiveX data between page transitions", "ACTIVEXEXT_*PERSIST", "check", on="TRUE", off="FALSE"
)

ACCESS_SUBS = [GENERAL, DISABLE]
SUBS = [GENERAL, DISABLE, IDENTIFICATION]
FIELDS = [PERSIST]

HELP_GENERAL = "properties#access"
ALL_AREAS = "<All areas>"  # CtDraw's text
NO_PRIVILEGE = "<None>"  # CtDraw's text


def _project_dir(doc) -> str:
    path = getattr(doc, "path", None)
    return os.path.dirname(os.path.abspath(path)) if path else ""


class AccessEditor:
    """Access > General: the six fields are sheet fields (their keys are
    written by the sheet); this page adds the AN, the 'Same area as page' /
    'No privilege restrictions' boxes, the area / privilege / device menus,
    the log-device advice and the object-name check."""

    phase = "before"
    #: a genie member's sheet keeps this page's own controls editable
    member_editable = True

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._saved = {"AREA": "", "PRIV": ""}
        self._devices = None
        self._page_area = None
        fields = {f.key: f for f in GENERAL.fields}
        mk = sheet.make_field
        self.name = mk(fields["__AN_NAME__"])
        self.desc = mk(fields["DESC"])
        self.tip = mk(fields["COMMENT"])
        self.area = mk(fields["AREA"])
        self.priv = mk(fields["PRIV"])
        self.device = mk(fields["DEVICE"])
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_GENERAL)
        except RuntimeError:
            pass

    # -- layout ------------------------------------------------------------------------
    def _menu_button(self, tip: str, fill) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton()
        b.setText("▾")
        b.setToolTip(tip)
        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        m = QtWidgets.QMenu(b)
        m.aboutToShow.connect(lambda m=m: fill(m))
        b.setMenu(m)
        return b

    def _build(self) -> QtWidgets.QWidget:
        s = self.sheet
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        # Identification
        ident = QtWidgets.QGroupBox("Identification")
        g = QtWidgets.QGridLayout(ident)
        self.an = QtWidgets.QLineEdit()
        self.an.setReadOnly(True)
        self.an.setToolTip("The object's animation number (AN)")
        self._an_initial = self._current_an()
        self.an.setText(self._an_initial)
        self.an.setMaximumWidth(90)
        # Settings > Modify AN field (CtDraw's ModifyAN): the field is editable
        # (CtDraw enables it; the apply renumbers)
        self._an_editable = False
        from ..prefs.settings import option

        if option("modify_an"):
            try:
                why = s._doc.an_change_problem(s._index)
            except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
                why = str(exc) or "The AN of this object cannot be changed."
            if why:
                self.an.setToolTip(why)
            else:
                self._an_editable = True
                self.an.setReadOnly(False)
                doc = s._doc
                self.an.setValidator(QtGui.QIntValidator(doc.AN_MIN, doc.AN_MAX, self.an))
                self.an.setToolTip(
                    "The object's animation number (AN), %d to %d. Cicode that uses the "
                    "old AN is not changed." % (doc.AN_MIN, doc.AN_MAX)
                )
        g.addWidget(QtWidgets.QLabel("Object AN:"), 0, 0)
        g.addWidget(self.an, 0, 1)
        g.addWidget(QtWidgets.QLabel("Name:"), 0, 2)
        g.addWidget(self.name, 0, 3)
        self.name.setToolTip(
            "Letters, numbers and '_', starting with a letter. Unique on the page, or in its group or genie."
        )
        g.addWidget(QtWidgets.QLabel("Description:"), 1, 0)
        g.addWidget(self.desc, 1, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Tool tip:"), 2, 0)
        g.addWidget(self.tip, 2, 1, 1, 3)
        self.tip.setToolTip("Shown when the operator points at the object (at most 48 characters)")
        g.setColumnStretch(3, 1)
        lay.addWidget(ident)
        # Security
        sec = QtWidgets.QGroupBox("Security")
        g = QtWidgets.QGridLayout(sec)
        self.same_area = QtWidgets.QCheckBox("Same area as page")
        self.same_area.setToolTip("The object uses the area of its page")
        self.no_priv = QtWidgets.QCheckBox("No privilege restrictions")
        self.no_priv.setToolTip("Any operator with access to the object's area can use it")
        self.area_btn = self._menu_button("Choose an area", self._fill_areas)
        self.priv_btn = self._menu_button("Choose a privilege level", self._fill_privileges)
        g.addWidget(self.same_area, 0, 0, 1, 3)
        g.addWidget(QtWidgets.QLabel("Object area:"), 1, 0)
        g.addWidget(self.area, 1, 1)
        g.addWidget(self.area_btn, 1, 2)
        g.addWidget(self.no_priv, 0, 3, 1, 3)
        g.addWidget(QtWidgets.QLabel("Privilege level:"), 1, 3)
        g.addWidget(self.priv, 1, 4)
        g.addWidget(self.priv_btn, 1, 5)
        lay.addWidget(sec)
        # Logging
        log = QtWidgets.QGroupBox("Logging")
        g = QtWidgets.QGridLayout(log)
        self.device_btn = self._menu_button("Choose a log device", self._fill_devices)
        g.addWidget(QtWidgets.QLabel("Log device:"), 0, 0)
        g.addWidget(self.device, 0, 1)
        g.addWidget(self.device_btn, 0, 2)
        self.device_hint = QtWidgets.QLabel()
        self.device_hint.setWordWrap(True)
        set_note_role(self.device_hint, "warn")
        self.device_hint.hide()
        g.addWidget(self.device_hint, 1, 0, 1, 3)
        g.setColumnStretch(1, 1)
        lay.addWidget(log)
        if s._partial:
            note = QtWidgets.QLabel("This object is part of a genie. Only these fields can be set here.")
            note.setWordWrap(True)
            set_note_role(note, "text_dim")
            lay.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        clear = QtWidgets.QPushButton("Clear Property")
        clear.setToolTip("Clear the fields of this page")
        clear.clicked.connect(lambda _=False: s._clear(GENERAL))
        row.addWidget(clear)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.same_area.toggled.connect(lambda on: self._on_box("AREA", on))
        self.no_priv.toggled.connect(lambda on: self._on_box("PRIV", on))
        self.area.textChanged.connect(lambda _t: self._on_field_text("AREA"))
        self.priv.textChanged.connect(lambda _t: self._on_field_text("PRIV"))
        self.device.textChanged.connect(lambda _t: self._update_device_hint())
        return w

    # -- the boxes ---------------------------------------------------------------------
    def _pair(self, key: str):
        return (
            (self.same_area, self.area, self.area_btn)
            if key == "AREA"
            else (self.no_priv, self.priv, self.priv_btn)
        )

    def _on_box(self, key: str, on: bool) -> None:
        box, field, _btn = self._pair(key)
        if on:
            if field.text():
                self._saved[key] = field.text()
            field.setText("")  # ticked = the key is not stored
        elif not field.text() and self._saved.get(key):
            field.setText(self._saved[key])
        self.enable_state()
        if not on and field.isEnabled():
            field.setFocus()

    def _on_field_text(self, key: str) -> None:
        # a LOAD sets the boxes from the stored keys; typing never ticks them
        if self.sheet._syncing:
            box, field, _btn = self._pair(key)
            box.blockSignals(True)
            box.setChecked(not field.text())
            box.blockSignals(False)

    def fields_loaded(self) -> None:
        """The sheet showed the stored keys: the boxes follow them."""
        for key in ("AREA", "PRIV"):
            box, field, _btn = self._pair(key)
            box.blockSignals(True)
            box.setChecked(not field.text())
            box.blockSignals(False)
        self.enable_state()
        self._update_device_hint()

    def on_clear(self, sub) -> None:
        """Clear Property blanked the fields: no area / privilege of its own."""
        if sub is GENERAL:
            self._saved = {"AREA": "", "PRIV": ""}
            self.fields_loaded()

    def page_area(self) -> str:
        """The area of the page the object is on (its pages.DBF row as the
        next save writes it: an area set in Page Properties counts at once)."""
        if self._page_area is None:
            try:
                doc = self.sheet._doc
                rec = doc.page_record() if hasattr(doc, "page_record") else None
                if rec is not None:
                    self._page_area = (rec.get("AREA") or "").strip()
                else:
                    from ...io.project import page_area as _pa

                    path = getattr(doc, "path", None)
                    self._page_area = _pa(path) if path else ""
            except Exception:  # noqa: BLE001
                _note_exc("objprops.access.page_area")
                self._page_area = ""
        return self._page_area

    def enable_state(self) -> None:
        """Enable the boxes, fields and menus the object's state allows."""
        s = self.sheet
        page_area = self.page_area()
        for key in ("AREA", "PRIV"):
            box, field, btn = self._pair(key)
            why = s.key_blocked(key)
            box.setEnabled(not why and not self._ro)
            if why:
                box.setToolTip(why)
            on = box.isChecked()
            editable = not why and not self._ro and not on
            field.setEnabled(not why and not on)
            btn.setEnabled(editable)
            if key == "AREA":
                field.setPlaceholderText((page_area or ALL_AREAS) if on else "")
            else:
                field.setPlaceholderText(NO_PRIVILEGE if on else "")
        why = s.key_blocked("DEVICE")
        self.device_btn.setEnabled(not why and not self._ro)

    # -- menus -------------------------------------------------------------------------
    def _pick(self, field, value: str) -> None:
        field.setText(value)
        field.setFocus()

    def _fill_areas(self, menu: QtWidgets.QMenu) -> None:
        from ...io.project import AREAS

        menu.clear()
        for lo in range(0, len(AREAS), 32):
            # parented: a submenu addMenu(title) returns is Python-owned in
            # PySide6 and gone with its wrapper
            sub = QtWidgets.QMenu("Areas %s to %s" % (AREAS[lo], AREAS[min(lo + 31, len(AREAS) - 1)]), menu)
            for a in AREAS[lo : lo + 32]:
                sub.addAction(a, lambda a=a: self._pick(self.area, a))
            menu.addMenu(sub)

    def _fill_privileges(self, menu: QtWidgets.QMenu) -> None:
        from ...io.project import PRIVILEGES

        menu.clear()
        for p in PRIVILEGES:
            menu.addAction(p, lambda p=p: self._pick(self.priv, p))

    def devices(self) -> tuple:
        if self._devices is None:
            try:
                from ...io.project import devices

                pdir = _project_dir(self.sheet._doc)
                self._devices = devices(pdir) if pdir else ()
            except Exception:  # noqa: BLE001 -- the menu is an aid only
                _note_exc("objprops.access.devices")
                self._devices = ()
        return self._devices

    def _fill_devices(self, menu: QtWidgets.QMenu) -> None:
        from ...io.project import device_logs_messages

        menu.clear()
        rows = self.devices()
        if not rows:
            a = menu.addAction("No devices are defined")
            a.setEnabled(False)
            return
        for r in rows:
            name = r.get("NAME", "")
            label = name if device_logs_messages(r) else "%s  (no {MsgLog} field)" % name
            act = menu.addAction(label, lambda n=name: self._pick(self.device, n))
            act.setToolTip(r.get("FORMAT", ""))

    def _update_device_hint(self) -> None:
        name = self.device.text().strip()
        msg = ""
        if name:
            from ...io.project import device_logs_messages

            rows = {r.get("NAME", "").lower(): r for r in self.devices()}
            r = rows.get(name.lower())
            if rows and r is None:
                msg = "No device named '%s' was found." % name
            elif r is not None and not device_logs_messages(r):
                msg = (
                    "The format of device '%s' has no {MsgLog} field, so command "
                    "messages are not written to it." % name
                )
        self.device_hint.setText(msg)
        self.device_hint.setVisible(bool(msg))

    # -- the AN (Settings > Modify AN field) --------------------------------------------
    def _current_an(self) -> str:
        try:
            an = self.sheet._doc.page.objects[self.sheet._index].an
        except Exception:  # noqa: BLE001  # swallow-ok: no such object, no AN
            an = -1
        return str(an) if an is not None and an >= 0 else ""

    def _an_changed(self) -> bool:
        return self._an_editable and not self._ro and self.an.text().strip() != self._an_initial

    # -- the sheet's editor contract ---------------------------------------------------
    def problem(self):
        """The object name CtDraw refuses (its character rule, or a name
        already used in the object's namespace), and a new AN another object
        has (or out of range)."""
        s = self.sheet
        if self._an_changed():
            try:
                msg = s._doc.an_change_problem(s._index, self.an.text())
            except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
                msg = str(exc)
            if msg:
                return msg, self.an
        if not s.field_entries("__AN_NAME__") or s._state("__AN_NAME__") == s._initial.get("__AN_NAME__"):
            return None
        try:
            msg = s._doc.object_name_problem(s._index, self.name.text())
        except Exception:  # noqa: BLE001 -- never block on a failed check
            _note_exc("objprops.access.object_name_problem")
            msg = ""
        return (msg, self.name) if msg else None

    def apply(self) -> bool:
        """A changed AN (the other fields are the sheet's)."""
        if not self._an_changed():
            return False
        return bool(self.sheet._doc.set_object_an(self.sheet._index, self.an.text()))

    def reload(self) -> None:
        self._an_initial = self._current_an()
        self.an.setText(self._an_initial)
        self.fields_loaded()

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.an.setReadOnly(self._ro or not self._an_editable)
        self.enable_state()


def build_general(sheet, page):
    ed = AccessEditor(sheet)
    sheet.add_editor(ed)
    sheet.access_editor = ed
    return ed.widget


# --------------------------------------------------------------------------- Identification
def _ax_defaults(doc, index):
    """CtDraw's displayed defaults of an ActiveX control's name and event
    class ('AN%s' and '%0.8s_AN%s')."""
    try:
        an = doc.page.objects[index].an
    except Exception:  # noqa: BLE001  # swallow-ok: no such object, no AN
        an = -1
    an = str(an) if an is not None and an >= 0 else "?"
    path = getattr(doc, "path", "") or ""
    page = os.path.splitext(os.path.basename(path))[0]
    return "AN%s" % an, "%s_AN%s" % (page[:8], an)


def build_identification(sheet, page):
    name_default, class_default = _ax_defaults(sheet._doc, sheet._index)

    def extra(form):
        box = sheet.make_field(PERSIST)
        form.addRow("", box)

    w = sheet.build_sub(IDENTIFICATION, extra=extra)
    for key, default, tip in (
        ("ACTIVEXEXT_*NAME", name_default, "The control's name in Cicode. Blank uses '%s'." % name_default),
        (
            "ACTIVEXEXT_*EVENTCLASS",
            class_default,
            "The prefix of the Cicode functions that handle the control's events. Blank "
            "uses '%s'." % class_default,
        ),
    ):
        for _f, fw in sheet.field_entries(key):
            fw.setPlaceholderText(default)
            fw.setToolTip(tip)
    return w


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {
    "access": build_general,
    "disable": lambda sheet, page: sheet.build_sub(DISABLE),
    "identification": build_identification,
}
