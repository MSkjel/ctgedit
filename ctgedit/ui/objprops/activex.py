"""ActiveX control page and Appearance > Tag Association."""

from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6 import QtCore, QtWidgets

from .expression import ExpressionField
from ..style.theme import set_note_role
from ..messages import inform, warn
from ...infra.logsetup import note_exception as _note_exc

HELP_CONTROL = "properties#activex-control"
HELP_TAGS = "properties#tag-association"
CHANGE_NOTIFICATION = "<Property change notification>"


def _clsid(doc, index: int) -> str:
    try:
        from ...io.pgregister import ole_clsid

        return ole_clsid(doc.parsed.page.objects[index].body_raw)
    except Exception:  # noqa: BLE001
        _note_exc("objprops.activex._clsid")
        return ""


# -- the control page ------------------------------------------------------------------
def build_general(sheet, page):
    from ...io import activex as AX

    doc, i = sheet._doc, sheet._index
    clsid = _clsid(doc, i)
    info = AX.control_info(clsid) if clsid else None
    w = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(w)

    def value(text):
        lab = QtWidgets.QLabel(text or "")
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lab.setWordWrap(True)
        return lab

    form.addRow("Control:", value(info.label if info else "(not registered on this computer)"))
    form.addRow("ProgID:", value(info.progid if info else ""))
    form.addRow("CLSID:", value(clsid))
    form.addRow("Server:", value(info.server if info else ""))
    from ...backend import controlhost

    edit = QtWidgets.QPushButton("Control Properties\u2026")
    edit.setToolTip("Show the control with its own properties and property pages")
    can = info is not None and controlhost.available() and not getattr(sheet, "read_only_reason", "")
    edit.setEnabled(bool(can))
    edit.clicked.connect(lambda _=False: edit_control(sheet, edit))
    form.addRow("", edit)
    note = QtWidgets.QLabel(
        "The control runs in a separate program while you change it. OK there stores its new "
        "settings in the page (one undo step)."
    )
    note.setWordWrap(True)
    set_note_role(note, "text_dim")
    form.addRow(note)
    if info is None and clsid:
        warn = QtWidgets.QLabel("This control is not registered on this computer.")
        warn.setWordWrap(True)
        set_note_role(warn, "warn")
        form.addRow(warn)
    try:
        from ..help import set_help

        set_help(w, HELP_CONTROL)
    except RuntimeError:
        pass
    sheet.activex_info = info
    return w


def edit_control(sheet, button=None) -> bool:
    """Run the control host's property window for the sheet's control and store what it
    returns. The editor keeps painting while the window is up; True when the page changed."""
    import threading

    from ...backend import controlhost

    doc, i = sheet._doc, sheet._index
    found = doc.control_state(i)
    if found is None:
        warn(sheet, "Control Properties", "This control has no saved settings to edit.")
        return False
    clsid, state, (w, h) = found
    owner = int(sheet.window().winId())
    result: dict = {}

    def run():
        try:
            result["state"] = controlhost.edit(clsid, state, w, h, owner)
        except Exception as e:  # noqa: BLE001 -- told to the user below
            _note_exc("objprops.activex.edit_control")
            result["error"] = e

    if button is not None:
        button.setEnabled(False)
    loop = QtCore.QEventLoop()
    worker = threading.Thread(target=run, name="control-properties", daemon=True)
    worker.start()
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: None if worker.is_alive() else loop.quit())
    timer.start(100)
    loop.exec()
    timer.stop()
    if button is not None:
        button.setEnabled(True)
    if "error" in result:
        warn(sheet, "Control Properties", "The control could not be shown: %s" % result["error"])
        return False
    new = result.get("state")
    if new is None:
        return False
    try:
        changed = doc.set_control_state(i, new)
    except ValueError as e:
        warn(sheet, "Control Properties", str(e))
        return False
    if changed:
        sheet.changed = True
        applied = getattr(sheet, "applied", None)
        if applied is not None:
            applied.emit()
    return changed


# -- Tag Association ----------------------------------------------------------------
class TagAssociationEditor:
    """The Tag Association page; Apply = EditableDocument.set_activex_associations."""

    phase = "before"

    def __init__(self, sheet):
        from ...io import activex as AX

        self.sheet = sheet
        self._ro = False
        self._syncing = False
        clsid = _clsid(sheet._doc, sheet._index)
        self.members = AX.control_members(clsid) if clsid else None
        self._by_name: Dict[str, object] = {}
        if self.members is not None:
            self._by_name = {m.name.lower(): m for m in self.members.properties}
        #: property -> [tag, event]
        self._rows: Dict[str, List[str]] = {}
        self._order: List[str] = []
        self._initial: List[Tuple[str, str, str]] = []
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TAGS)
        except RuntimeError:
            pass
        self.reload()

    def _build(self) -> QtWidgets.QWidget:
        s = self.sheet
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("&Properties"))
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumWidth(170)
        self.list.currentRowChanged.connect(self._select)
        self.list.itemChanged.connect(self._item_checked)
        left.addWidget(self.list, 1)
        self.add_row = QtWidgets.QHBoxLayout()
        self.add_name = QtWidgets.QLineEdit()
        self.add_name.setPlaceholderText("Property name")
        self.add_btn = QtWidgets.QPushButton("Add")
        self.add_btn.setToolTip("Add a property by name")
        self.add_btn.clicked.connect(self._add_typed)
        self.add_row.addWidget(self.add_name, 1)
        self.add_row.addWidget(self.add_btn)
        left.addLayout(self.add_row)
        h.addLayout(left)
        right = QtWidgets.QVBoxLayout()
        self.assoc_box = QtWidgets.QGroupBox("Associate property with tag")
        al = QtWidgets.QVBoxLayout(self.assoc_box)
        self.tag = ExpressionField(s.expression_context, wizard=("tag",), field_kind="tag")
        self.tag.setMaxLength(255)
        self.tag.set_names(s.expression_context.completion_model())
        self.tag.setToolTip("A variable tag, not an expression")
        self.tag.textChanged.connect(self._tag_typed)
        al.addWidget(self.tag)
        facts = QtWidgets.QHBoxLayout()
        self.bindable = QtWidgets.QCheckBox("Bindable")
        self.bindable.setEnabled(False)
        self.bindable.setToolTip("The control reports changes of this property")
        facts.addWidget(self.bindable)
        facts.addWidget(QtWidgets.QLabel("Property status:"))
        self.status = QtWidgets.QLabel("")
        facts.addWidget(self.status)
        facts.addStretch(1)
        al.addLayout(facts)
        self.type_warn = QtWidgets.QLabel()
        self.type_warn.setWordWrap(True)
        set_note_role(self.type_warn, "warn")
        self.type_warn.hide()
        al.addWidget(self.type_warn)
        right.addWidget(self.assoc_box)
        ev = QtWidgets.QGroupBox("Update association on")
        el = QtWidgets.QFormLayout(ev)
        self.event = QtWidgets.QComboBox()
        self.event.setEditable(True)
        self.event.lineEdit().setMaxLength(128)
        self.event.currentTextChanged.connect(self._event_changed)
        el.addRow("&Event:", self.event)
        right.addWidget(ev)
        right.addWidget(QtWidgets.QLabel("Property &documentation:"))
        from .scroll import LinesEdit

        # three lines to start with; a taller window shows more of it
        self.doc = LinesEdit(3)
        self.doc.setReadOnly(True)
        right.addWidget(self.doc, 1)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.btn_types = QtWidgets.QPushButton("&List Property Types")
        self.btn_types.setToolTip("Show the tag types this property can be associated with")
        self.btn_types.clicked.connect(self.list_types)
        btns.addWidget(self.btn_types)
        self.btn_clear = QtWidgets.QPushButton("&Clear Property")
        self.btn_clear.setToolTip("Remove every tag association of the control")
        self.btn_clear.clicked.connect(self.clear)
        btns.addWidget(self.btn_clear)
        right.addLayout(btns)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        right.addWidget(self.note)
        h.addLayout(right, 1)
        return w

    # -- rows ------------------------------------------------------------------------
    def _member(self, name: str):
        return self._by_name.get((name or "").lower())

    def _fill_list(self) -> None:
        self._syncing = True
        try:
            self.list.clear()
            for name in self._order:
                it = QtWidgets.QListWidgetItem(name)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(
                    QtCore.Qt.Checked if self._rows.get(name, ["", ""])[0] else QtCore.Qt.Unchecked
                )
                m = self._member(name)
                if self.members is not None and m is None:
                    f = it.font()
                    f.setItalic(True)
                    it.setFont(f)
                    it.setToolTip("Stored on the object, but not a property of the control's type library")
                self.list.addItem(it)
        finally:
            self._syncing = False

    def current(self) -> str:
        it = self.list.currentItem()
        return it.text() if it is not None else ""

    def _select(self, _row: int) -> None:
        name = self.current()
        m = self._member(name)
        self._syncing = True
        try:
            tag, event = self._rows.get(name, ["", ""])
            self.assoc_box.setTitle(
                "Associate property '%s' with tag" % name if name else "Associate property with tag"
            )
            self.tag.setText(tag)
            self.bindable.setChecked(bool(m is not None and m.bindable))
            self.status.setText(m.status if m is not None else "")
            self.event.clear()
            if m is None or m.bindable:
                self.event.addItem(CHANGE_NOTIFICATION)
            for e in self.members.events if self.members is not None else ():
                self.event.addItem(e)
            self.event.setCurrentText(event or (CHANGE_NOTIFICATION if m is None or m.bindable else ""))
            if m is not None:
                self.doc.setPlainText(m.doc or "No documentation exists for this property.")
            else:
                self.doc.setPlainText("")
            from ...io.activex import compatible_types

            ok = bool(name) and (m is None or compatible_types(m.vt) is not None)
            for wdg in (self.tag, self.event):
                wdg.setEnabled(ok and not self._ro)
            self.btn_types.setEnabled(m is not None)
        finally:
            self._syncing = False
        self._check_type()

    def _item_checked(self, item) -> None:
        if self._syncing:
            return
        name = item.text()
        if item.checkState() != QtCore.Qt.Checked:
            # unticking clears the association (help 1172151)
            self._rows[name] = ["", ""]
            if name == self.current():
                self._select(self.list.currentRow())
        elif not self._rows.get(name, ["", ""])[0]:
            self.list.setCurrentItem(item)
            self.tag.setFocus()
        self._changed()

    def _tag_typed(self, text: str) -> None:
        if self._syncing:
            return
        name = self.current()
        if not name:
            return
        row = self._rows.setdefault(name, ["", ""])
        row[0] = text.strip()
        it = self.list.currentItem()
        self._syncing = True
        try:
            it.setCheckState(QtCore.Qt.Checked if row[0] else QtCore.Qt.Unchecked)
        finally:
            self._syncing = False
        self._check_type()
        self._changed()

    def _event_changed(self, text: str) -> None:
        if self._syncing:
            return
        name = self.current()
        if not name:
            return
        row = self._rows.setdefault(name, ["", ""])
        # '<Property change notification>' is stored as no event
        row[1] = "" if text.strip().lower() == CHANGE_NOTIFICATION.lower() else text.strip()
        self._changed()

    def _check_type(self) -> None:
        name = self.current()
        m = self._member(name)
        tag = self._rows.get(name, ["", ""])[0]
        msg = None
        if m is not None and tag:
            e = self.sheet.expression_context.tag_index().get(tag)
            if e is not None:
                from ...io.activex import tag_type_problem

                msg = tag_type_problem(m, tag, e.type)
        self.type_warn.setText(msg or "")
        self.type_warn.setVisible(bool(msg))

    def _add_typed(self) -> None:
        name = self.add_name.text().strip()
        if not name or self._ro:
            return
        if name not in self._order:
            self._order.append(name)
            self._rows.setdefault(name, ["", ""])
            self._fill_list()
        self.add_name.clear()
        for r in range(self.list.count()):
            if self.list.item(r).text() == name:
                self.list.setCurrentRow(r)
        self.tag.setFocus()

    def list_types(self) -> None:
        from ...io.activex import compatible_types

        m = self._member(self.current())
        if m is None:
            return
        types = compatible_types(m.vt)
        if types is None:
            text = "No Plant SCADA types are compatible with this property."
        else:
            text = "Plant SCADA types compatible with this property:\n\n%s" % types
        inform(self.sheet, "Compatible Types", text)

    def set_association(self, prop: str, tag: str, event: str = "") -> None:
        """Associate ``prop`` as the user does (tests / scripting)."""
        if prop not in self._order:
            self._order.append(prop)
        self._rows[prop] = [tag, event]
        self._fill_list()
        self._changed()

    def clear(self) -> None:
        if self._ro:
            return
        for row in self._rows.values():
            row[0], row[1] = "", ""
        self._fill_list()
        self._select(self.list.currentRow())
        self._changed()

    def rows(self) -> List[Tuple[str, str, str]]:
        return [
            (n, self._rows[n][0], self._rows[n][1]) for n in self._order if self._rows.get(n, ["", ""])[0]
        ]

    def _changed(self) -> None:
        s = self.sheet
        if not s._syncing:
            s._refresh_marks(s._pending_props())

    # -- the sheet's editor contract ------------------------------------------------------
    def pending_props(self, props: dict) -> None:
        rows = self.rows()
        if rows == self._initial:
            return
        for k in [k for k in props if k.startswith("ACTIVEX_")]:
            del props[k]
        for p, t, e in rows:
            props["ACTIVEX_" + p] = "%s|%s" % (t, e)

    def problem(self):
        if self.rows() == self._initial:
            return None
        for p, t, e in self.rows():
            if len("%s|%s" % (t, e)) > 160:
                return (
                    "The association of '%s' is too long (the tag and its event take at "
                    "most 160 characters)." % p,
                    self.tag,
                )
        return None

    def apply(self) -> bool:
        if self._ro:
            return False
        rows = self.rows()
        if rows == self._initial:
            return False
        s = self.sheet
        try:
            return bool(s._doc.set_activex_associations(s._index, rows))
        except ValueError as exc:
            s._fail(str(exc), self.tag)
            return False

    def reload(self) -> None:
        s = self.sheet
        try:
            stored = s._doc.activex_associations(s._index)
        except Exception:  # noqa: BLE001
            _note_exc("objprops.activex.reload")
            stored = []
        self._rows = {}
        self._order = []
        if self.members is not None:
            for m in self.members.properties:
                self._order.append(m.name)
                self._rows[m.name] = ["", ""]
        for p, t, e in stored:
            m = self._member(p)
            name = m.name if m is not None else p
            if name not in self._order:
                self._order.append(name)
            self._rows[name] = [t, e]
        self._initial = self.rows()
        self._fill_list()
        typed = self.members is None
        self.add_name.setVisible(typed)
        self.add_btn.setVisible(typed)
        self.note.setText(
            ""
            if not typed
            else "The control's type library is not available on this computer. "
            "The stored associations are shown; add a property by name."
        )
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._select(-1)

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        for wdg in (self.tag, self.event, self.btn_clear, self.add_name, self.add_btn):
            wdg.setEnabled(not ro)
        self.list.setEnabled(True)


def build_tag_association(sheet, page):
    ed = TagAssociationEditor(sheet)
    sheet.add_editor(ed)
    sheet.tag_association_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"general": build_general, "tag_association": build_tag_association}
SUBS: list = []
