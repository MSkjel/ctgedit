"""Appearance > General for a placed library object."""

from __future__ import annotations

import os

from PySide6 import QtCore, QtWidgets
from ...infra.logsetup import note_exception as _note_exc

HELP_TOPIC = "library-elements#symbol-linkage"
_THUMB = QtCore.QSize(64, 64)


class LinkageEditor:
    """The Linkage page; Apply = relink_symbol / change_template."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._link = None
        self._pending = None  # (library, element, ext) chosen with Browse
        self._told = None  # (library, element, ext) applied: told after the step
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except RuntimeError:
            pass
        self.reload()

    def _build(self) -> QtWidgets.QWidget:
        from ..style import theme
        from ..pick_fields import LibraryElementField

        s = self.sheet
        start = os.path.dirname(os.path.abspath(s._doc.path)) if s._doc.path else None
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        form = QtWidgets.QFormLayout()
        self.kind = QtWidgets.QLabel()
        form.addRow("Linked to:", self.kind)
        lay.addLayout(form)
        self.field = LibraryElementField(
            start, "symbol", w, title="Select Symbol", edit_text="Edit Symbol", picker=self._pick
        )
        self.field.btn_browse.setToolTip("Link this object to another library element")
        self.field.changed.connect(self._picked)
        self.field.editRequested.connect(self._edit)
        lay.addWidget(self.field)
        # the field's parts (callers and tests read them here)
        self.picture, self.name, self.path = self.field.picture, self.field.name, self.field.detail
        self.btn_set, self.btn_edit = self.field.btn_browse, self.field.btn_edit
        self.note = theme.note_label("")
        lay.addWidget(self.note)
        lay.addStretch(1)
        return w

    # -- display --------------------------------------------------------------------
    def _show(self, library: str, element: str, ext: str, path) -> None:
        self.field.set_value((library, element), path or None)
        if path:
            proj = os.path.basename(os.path.dirname(os.path.abspath(path)))
            self.field.set_detail("%s (project %s)" % (path, proj))
        else:
            self.field.set_detail("Not found on this project's include path (broken link)", "warn")

    def reload(self) -> None:
        s = self.sheet
        try:
            self._link = s._doc.symbol_link(s._index)
        except Exception:  # noqa: BLE001
            _note_exc("objprops.linkage.reload")
            self._link = None
        self._pending = None
        link = self._link
        if link is None:
            self.kind.setText("")
            self.field.set_value(None)
            self.field.set_detail("")
            self.btn_set.setEnabled(False)
            if self.btn_edit is not None:
                self.btn_edit.setEnabled(False)
            return
        self.kind.setText(
            {
                "symbol": "a library symbol",
                "group": "a library symbol (group)",
                "template": "the page template",
                "genie": "a genie",
            }.get(link["kind"], link["kind"])
        )
        template = link["kind"] == "template"
        self.field.kind = "template" if template else "symbol"
        self.field.title = "Select Template" if template else "Select Symbol"
        self._show(link["library"], link["element"], link["ext"], link["path"])
        if self.btn_edit is not None:
            self.btn_edit.setText("Edit Template" if template else "Edit Symbol")
            self.btn_edit.setEnabled(bool(link["path"]))
        self.btn_set.setEnabled(not self._ro and link["kind"] in ("symbol", "group", "template"))
        self.note.setText(
            "The symbol keeps its position, scale and rotation; its size follows the new symbol."
            if link["kind"] in ("symbol", "group")
            else "The page's template objects are replaced by the new template's; the page "
            "takes its size, page colour and page properties."
        )

    # -- actions ----------------------------------------------------------------------
    def _pick(self, parent):
        """Browse...: the template picker for a template, else the library
        element browser."""
        link = self._link
        if link is None:
            return None
        s = self.sheet
        start = os.path.dirname(os.path.abspath(s._doc.path)) if s._doc.path else ""
        if link["kind"] == "template":
            from ..dialog.dialogs import UseTemplateDialog, page_defaults

            cur = (link["path"] or link["library"] + ".ctt", link["element"])
            dlg = UseTemplateDialog(start, page_defaults(start), parent, mode="choose", current=cur)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return None
            return dlg.template_element()
        from ..browser import PageBrowserDialog

        return PageBrowserDialog.get_library_element(parent, start, "symbol", title="Select Symbol")

    def _picked(self) -> None:
        link = self._link
        v = self.field.value()
        if link is None or v is None:
            return
        ext = "ctt" if link["kind"] == "template" else "ctl"
        library, element = v
        self._pending = (library, element, ext)
        path = self.field.path()
        if path:
            proj = os.path.basename(os.path.dirname(os.path.abspath(path)))
            self.field.set_detail("%s (project %s)" % (path, proj))
        self.note.setText("Apply or OK links the object to %s.%s." % (library, element))
        s = self.sheet
        if hasattr(s, "_refresh_marks"):
            s._refresh_marks(s._pending_props())

    def _edit(self) -> None:
        link = self._link
        if link is None or not link["path"]:
            return
        win = self.sheet.parent()
        opener = getattr(win, "_open_element", None)
        if opener is not None:
            opener(link["path"], link["element"])

    # -- the sheet's editor contract ------------------------------------------------------
    def problem(self):
        return None

    def has_pending_edits(self) -> bool:
        """A new element was chosen and not applied yet."""
        return self._pending is not None

    def apply(self) -> bool:
        if self._ro or self._pending is None or self._link is None:
            return False
        s = self.sheet
        library, element, ext = self._pending
        try:
            if ext == "ctt":
                ok = bool(s._doc.change_template("%s.%s.<ctt" % (library, element)))
            else:
                ok = bool(s._doc.relink_symbol(s._index, library, element, ext))
        except (ValueError, KeyError) as exc:
            s._fail(
                "The object could not be linked to %s.%s: %s"
                % (library, element, exc.args[0] if exc.args else exc),
                self.btn_set,
            )
            return False
        self._pending = None
        self._told = (library, element, ext) if ok else None
        return ok

    def after_step(self) -> None:
        """The sheet's undo step is written: the relink is told over the page, with an Undo
        of that step."""
        told, self._told = self._told, None
        if told is None:
            return
        library, element, ext = told
        doc = self.sheet._doc
        from ..editor.notices import window_of
        from ..notify import notify, undo_action

        win = window_of(doc)
        if win is None:
            return
        shown = getattr(win, "doc", None) is doc
        text = (
            "The page now uses the template %s.%s." if ext == "ctt" else "Linked the object to %s.%s."
        ) % (library, element)
        notify(
            win,
            text,
            "success",
            actions=[undo_action(win, doc)] if shown else (),
            toast=shown,
            key="edit:relink",
        )

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self.field.set_read_only(ro)
        self.btn_set.setEnabled(
            not ro and self._link is not None and self._link["kind"] in ("symbol", "group", "template")
        )


def build_linkage(sheet, page):
    ed = LinkageEditor(sheet)
    sheet.add_editor(ed)
    sheet.linkage_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"linkage": build_linkage}
SUBS: list = []
