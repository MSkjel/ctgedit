"""The browser's New mode (File > New)."""

from __future__ import annotations

from ...infra.paths import path_key
from ..style.theme import sp as _sp  # (the spacing scale)

import html
import os
from dataclasses import dataclass
from typing import List, Optional

from PySide6 import QtCore, QtWidgets

from ...io.catalog import ProjectInfo
from ..prefs.settings import app_settings
from ..style.theme import COLORS
from . import naming

#: what each tab creates
NEW_KIND_LABELS = {
    "page": "Page",
    "template": "Template",
    "symbol": "Symbol",
    "genie": "Genie",
    "supergenie": "Super Genie",
}
#: the default button of each kind
CREATE_LABELS = {
    "page": "Create Page",
    "template": "Create Template",
    "symbol": "Create Symbol",
    "genie": "Create Genie",
    "supergenie": "Create Super Genie",
}
#: the library kind an element kind is kept in, and its file extension
LIBRARY_KIND = {"template": "template", "symbol": "symbol", "genie": "genie", "supergenie": "genie"}
LIBRARY_EXT = {"template": "ctt", "symbol": "ctl", "genie": "ctm"}
#: the setting that keeps whether a new page is linked to its template
LINKED_KEY = "newpage/linked"


@dataclass(frozen=True)
class NewTarget:
    """What File > New creates: ``kind``; ``path`` = the page's .ctg, or the library file of an
    element; ``element`` = the element's name ('' for a page; a super genie's without
    '@SUPER'); a page's ``template_ref`` ('<style>.<element>.<ctt') and ``linked``."""

    kind: str
    path: str
    element: str = ""
    template_ref: str = ""
    linked: bool = True


def unique_element_name(base: str, existing, limit: int = 31) -> str:
    """``base`` with the first free ``_2``, ``_3`` ... suffix (case-insensitive), within
    ``limit`` characters (a library element name: 31)."""
    import re

    taken = {str(e).lower() for e in existing}
    base = (base or "").strip() or "New"
    if base.lower() not in taken:
        return base[:limit]
    stem, i = base, 2
    m = re.match(r"^(.*\D)_(\d{1,3})$", stem)
    if m:
        stem, i = m.group(1), int(m.group(2)) + 1
    while True:
        suffix = "_%d" % i
        cand = stem[: limit - len(suffix)] + suffix
        if cand.lower() not in taken:
            return cand
        i += 1


class NewMode:
    """The New mode's part of :class:`~.dialog.PageBrowserDialog` (``mode == MODE_NEW``)."""

    # -- building -------------------------------------------------------------------
    def _new_init(self) -> None:
        self.new_target: Optional[NewTarget] = None
        self.tpl_picker = None
        self._tpl_project: Optional[str] = None
        self._new_ok = False

    def _new_build_template_box(self) -> None:
        """The Page tab's centre: the target project's templates."""
        from ..style.theme import note_label

        self.tpl_box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(self.tpl_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(1))
        self.tpl_where = note_label("")
        lay.addWidget(self.tpl_where)
        self.tpl_host = QtWidgets.QVBoxLayout()
        self.tpl_host.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.tpl_host, 1)
        self.chk_new_linked = QtWidgets.QCheckBox("Linked")
        self.chk_new_linked.setToolTip(
            "Keep the page linked to the template. Template objects stay locked and follow "
            "template changes. The link cannot be added later."
        )
        self.chk_new_linked.setChecked(app_settings().value(LINKED_KEY, True, type=bool))
        # (it leaves this window for the shared template itself: named so, and kept apart
        # from the options of the new page)
        self.btn_edit_template = QtWidgets.QPushButton("Open Template to Edit…")
        self.btn_edit_template.setToolTip(
            "Close this window and open the selected template itself for editing. Every page "
            "made from it follows the changes"
        )
        self.btn_edit_template.setAutoDefault(False)
        self.btn_edit_template.clicked.connect(self._new_edit_template)
        self.stack.addWidget(self.tpl_box)

    def _new_build_bar(self, bar: QtWidgets.QFrame) -> None:
        grid = QtWidgets.QGridLayout(bar)
        grid.setContentsMargins(_sp(3), _sp(2), _sp(3), _sp(2))
        grid.setHorizontalSpacing(_sp(2))
        grid.setVerticalSpacing(_sp(0))
        self._action_layout = grid
        grid.addWidget(QtWidgets.QLabel("Project:"), 0, 0)
        self.cb_project = QtWidgets.QComboBox()
        self.cb_project.setMinimumWidth(160)
        self.cb_project.setAccessibleName("Project")
        self.cb_project.setToolTip("The project that gets the new page or element")
        self._fill_project_combo()
        grid.addWidget(self.cb_project, 0, 1)
        self.lbl_library = QtWidgets.QLabel("Library:")
        grid.addWidget(self.lbl_library, 0, 2)
        self.cb_library = QtWidgets.QComboBox()
        self.cb_library.setEditable(True)
        self.cb_library.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cb_library.setMinimumWidth(140)
        self.cb_library.setAccessibleName("Library")
        self.cb_library.setToolTip("A library of the project; type a new name to create one")
        self.cb_library.lineEdit().setPlaceholderText("Library")
        self.lbl_library.setBuddy(self.cb_library)
        grid.addWidget(self.cb_library, 0, 3)
        self.btn_newlib = QtWidgets.QPushButton("New Library")
        self.btn_newlib.setToolTip("Type the name of a new library in the project")
        self.btn_newlib.setAutoDefault(False)
        self.btn_newlib.clicked.connect(self._new_library)
        grid.addWidget(self.btn_newlib, 0, 4)
        grid.setColumnMinimumWidth(5, 4)
        self.lbl_name = QtWidgets.QLabel("Name:")
        grid.addWidget(self.lbl_name, 0, 6)
        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setMinimumWidth(160)
        self.ed_name.setMaxLength(120)
        self.ed_name.setAccessibleName("Name")
        self.lbl_name.setBuddy(self.ed_name)
        grid.addWidget(self.ed_name, 0, 7)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(QtCore.Qt.RichText)
        # (under the name it is about: the character count and the name's errors start at
        # the name field's left edge)
        grid.addWidget(self.msg, 1, 7, 1, 4)
        # the name field takes the extra width (UI guide section 9)
        grid.setColumnStretch(7, 1)
        grid.addWidget(self.ok_button, 0, 8)
        grid.addWidget(self.cancel_button, 0, 9)
        grid.addWidget(self.btn_help, 0, 10)
        self.ed_name.textEdited.connect(self._on_name_edited)
        self.ed_name.textChanged.connect(lambda _t: self._new_validate())
        self.cb_project.currentIndexChanged.connect(lambda _i: self._new_project_changed())
        self.cb_library.editTextChanged.connect(lambda _t: self._new_validate())
        self.cb_library.currentIndexChanged.connect(lambda _i: self._new_validate())

    # -- the kind --------------------------------------------------------------------------
    def _new_page_kind(self) -> bool:
        return self._kind == "page"

    def _new_sync_kind(self) -> None:
        """Show what the kind needs: the templates or the element cards, the Library field, the
        name and the button."""
        page = self._new_page_kind()
        for w in (self.lbl_library, self.cb_library, self.btn_newlib):
            w.setVisible(not page)
        self.lbl_name.setText("Page name:" if page else "Name:")
        self.ok_button.setText(CREATE_LABELS.get(self._kind, "Create"))
        for w in (
            self.search,
            self.scope_lbl,
            self.chk_includes,
            self.status,
            self.cb_sort,
            self.btn_grid,
            self.btn_list,
            self.slider,
        ):
            w.setVisible(not page)
        # (the page filters belong to listing pages, which New does not)
        self.cb_popups.setVisible(False)
        self.chip_unreg.setVisible(False)
        if page:
            self._new_show_templates()
        else:
            self.stack.setCurrentWidget(self.table if self.view_mode() == "list" else self.grid)
            self._new_fill_libraries()
        if self.tpl_picker is not None:
            # (a hidden picker leaves the stack's minimum width to the cards)
            self.tpl_picker.setVisible(page)
        self.preview.setVisible(self.preview_wanted())
        if not self._name_touched:
            self._new_default_name()
        self._new_validate()

    def _new_default_name(self) -> None:
        """The name a kind starts with: a free 'NewPage' for a page (Settings > Pages and
        libraries 'Name new pages:'), '!' for a super genie (help 1172237), nothing else."""
        info = self._target_project()
        if self._new_page_kind():
            existing = set(self.catalog.page_names(info)) if info is not None else set()
            # a new page is on disk only after its first Save: the names of the pages open
            # in the editor's tabs are taken too
            open_names = getattr(self.parent(), "open_page_names", None)
            if info is not None and callable(open_names):
                try:
                    existing |= set(open_names(info.dir))
                except RuntimeError:  # swallow-ok: the window is closing; the disk's names
                    pass
            from ..prefs.window_options import new_page_name

            text = naming.unique_page_name(new_page_name(), existing)
        else:
            text = "!" if self._kind == "supergenie" else ""
        self.ed_name.blockSignals(True)
        self.ed_name.setText(text)
        self.ed_name.blockSignals(False)

    # -- the project ------------------------------------------------------------------------
    def _new_project_changed(self) -> None:
        info = self._target_project()
        base = self.tree.current_base_scope()
        if info is not None and (base is None or base.anchor is None or base.anchor.key != info.key):
            # the tree follows the bar's project (and lists its elements)
            if not self.tree.select_project(info):
                self._reload()
        if self._new_page_kind():
            self._new_show_templates()
            if not self._name_touched:
                self._new_default_name()
        else:
            self._new_fill_libraries()
        self._new_validate()

    # -- the templates (a page) -------------------------------------------------------------
    def _new_template_memory(self) -> dict:
        raw = str(self._value(self._key("template"), "") or "")
        parts = raw.split("|")
        if len(parts) != 4:
            return {}
        return {"style": parts[0], "element": parts[1], "resolution": parts[2], "title_bar": parts[3] == "1"}

    def _new_show_templates(self) -> None:
        """The target project's templates (the picker is made again for another project,
        keeping the style, the resolution and the template chosen)."""
        from ..dialog.dialogs import page_defaults
        from ..pick_fields import TemplatePicker

        self.stack.setCurrentWidget(self.tpl_box)
        info = self._target_project()
        pdir = info.dir if info is not None else None
        if pdir is None:
            return
        key = path_key(pdir)
        if self.tpl_picker is not None and self._tpl_project == key:
            return
        want = {}
        if self.tpl_picker is not None:
            v = self.tpl_picker.value()
            want = {
                "style": self.tpl_picker.style_name(),
                "resolution": self.tpl_picker.resolution(),
                "title_bar": self.tpl_picker.title_bar(),
                "element": v[1] if v else "",
            }
            self.tpl_picker.setParent(None)
            self.tpl_picker.deleteLater()
        else:
            want = self._new_template_memory()
        defaults = page_defaults(pdir)
        style = want.get("style") or defaults.get("style", "")
        res = want.get("resolution", defaults.get("resolution", ""))
        tb = want.get("title_bar", bool(defaults.get("title_bar", False)))
        picker = TemplatePicker(
            pdir,
            self.tpl_box,
            background=defaults.get("background"),
            style=style,
            resolution=res,
            title_bar=tb,
        )
        element = want.get("element") or ""
        if element and picker.catalog.find(style, element) is not None:
            picker.set_current(style, element)
        picker.side.insertWidget(picker.side.count() - 1, self.chk_new_linked)
        picker.side.addWidget(self.btn_edit_template)  # (below the stretch: apart from the options)
        picker.changed.connect(self._new_validate)
        picker.activated.connect(self.accept_current)
        self.tpl_host.addWidget(picker, 1)
        self.tpl_picker = picker
        self._tpl_project = key
        self.tpl_where.setText("Templates of project %s and the projects it includes." % info.name)

    # -- the libraries (an element) ---------------------------------------------------------
    def _new_library_kind(self) -> str:
        return LIBRARY_KIND.get(self._kind, "genie")

    def _new_fill_libraries(self, prefer: str = "") -> None:
        """The target project's libraries of the kind: ``prefer`` (a path), else the one used
        last for this kind when the project has it, else a typed new name kept, else the
        first."""
        info = self._target_project()
        cb = self.cb_library
        typed = cb.currentText().strip()
        typed_new = bool(typed) and cb.findText(typed, QtCore.Qt.MatchFixedString) < 0
        cb.blockSignals(True)
        cb.clear()
        libs = (
            self.catalog.libraries(info, self._new_library_kind(), include_empty=True)
            if info is not None
            else []
        )
        for lib in libs:
            cb.addItem(lib.stem, lib.path)
            cb.setItemData(cb.count() - 1, lib.path, QtCore.Qt.ToolTipRole)
        pick = -1
        for want in (prefer, str(self._value(self._key("library_" + self._kind), "") or "")):
            if not want:
                continue
            for i in range(cb.count()):
                if os.path.normcase(str(cb.itemData(i))) == os.path.normcase(want):
                    pick = i
                    break
            if pick >= 0:
                break
        if pick >= 0:
            cb.setCurrentIndex(pick)
        elif typed_new and not prefer:
            cb.setCurrentIndex(-1)
            cb.setEditText(typed)
        elif cb.count():
            cb.setCurrentIndex(0)
        else:
            cb.setCurrentIndex(-1)
            cb.setEditText("")
        cb.blockSignals(False)

    def _new_library(self) -> None:
        """New Library: the field empties for the new library's name (checked as it is
        typed; the library is created when the element is saved)."""
        self.cb_library.setCurrentIndex(-1)
        self.cb_library.setEditText("")
        self.cb_library.lineEdit().setPlaceholderText("Name of the new library")
        self.cb_library.setFocus()
        self._new_validate()

    def _new_library_path(self) -> str:
        text = self.cb_library.currentText().strip()
        if not text:
            return ""
        i = self.cb_library.findText(text, QtCore.Qt.MatchFixedString)
        if i >= 0 and self.cb_library.itemData(i):
            return str(self.cb_library.itemData(i))
        info = self._target_project()
        path = text if os.path.dirname(text) or info is None else os.path.join(info.dir, text)
        ext = LIBRARY_EXT[self._new_library_kind()]
        if not path.lower().endswith("." + ext):
            path += "." + ext
        return path

    def _new_library_elements(self, lib: str) -> List[str]:
        """The element names of library file ``lib`` (directory read, cached)."""
        info = self._target_project()
        if not lib or info is None or not os.path.exists(lib):
            return []
        key = os.path.normcase(lib)
        for li in self.catalog.libraries(info, self._new_library_kind(), include_empty=True):
            if os.path.normcase(li.path) == key:
                return [de.name for de in li.elements]
        return []

    # -- a card picked ------------------------------------------------------------------------
    def _new_on_pick(self, e) -> None:
        """A card clicked: its project and library get the new element, named after it
        (a free variant)."""
        if e is None or not e.is_element:
            return
        if e.project_dir:
            info = self.catalog.project_for_dir(e.project_dir)
            self.cb_project.blockSignals(True)
            self._select_combo_dir(e.project_dir, add_info=info)
            self.cb_project.blockSignals(False)
        self._new_fill_libraries(prefer=e.path)
        names = self._new_library_elements(e.path)
        base = e.name[: -len("@SUPER")] if e.name.upper().endswith("@SUPER") else e.name
        if self._kind == "supergenie":
            names = [n[: -len("@SUPER")] if n.upper().endswith("@SUPER") else n for n in names]
        self._name_touched = False
        self.ed_name.setText(unique_element_name(base, names))
        self._new_validate()

    # -- checks ----------------------------------------------------------------------------
    def _new_lines(self) -> tuple:
        """``(ok, [(text, colour role)])``: whether Create can go on, and the line under the
        fields."""
        info = self._target_project()
        name = self.ed_name.text()
        if info is None:
            return False, [("Choose a project.", "danger")]
        if self._new_page_kind():
            lines = []
            ok = True
            if self.tpl_picker is None or self.tpl_picker.value() is None:
                lines.append(("Choose a template.", "text_dim"))
                ok = False
            self.btn_edit_template.setEnabled(
                self.tpl_picker is not None and self.tpl_picker.value() is not None
            )
            v = naming.validate_page_name(
                name,
                info.dir,
                existing=self.catalog.page_names(info),
                registered=self.catalog.registered_names(info),
                has_pages_dbf=info.has_pages_dbf,
                mode="new",
                elsewhere=self._pages_elsewhere(info),
            )
            role = {naming.OK: "text_dim", naming.WARN: "warn", naming.ERROR: "danger"}[v.level]
            lines.append((v.message, role))
            return ok and v.level != naming.ERROR, lines
        from ...format.element import check_element_name, check_library_name
        from ..dialog.library_dialogs import shipped_library

        lines = []
        ok = True
        lib = self._new_library_path()
        noun = NEW_KIND_LABELS[self._kind].lower()
        if not lib:
            lines.append(("Choose a library, or type the name of a new one.", "text_dim"))
            ok = False
        elif not os.path.exists(lib):
            err = check_library_name(os.path.splitext(os.path.basename(lib))[0])
            if err:
                lines.append((err, "danger"))
                ok = False
            else:
                lines.append(("A new library %s will be created." % os.path.basename(lib), "text_dim"))
        stored = name.strip()
        if self._kind == "supergenie" and stored and not stored.upper().endswith("@SUPER"):
            stored += "@SUPER"
        if not name.strip() or name.strip() == "!":
            lines.append(("Type the name of the new %s." % noun, "text_dim"))
            ok = False
        else:
            err = check_element_name(stored)
            if err:
                lines.append((err, "danger"))
                ok = False
            elif lib and any(n.lower() == stored.lower() for n in self._new_library_elements(lib)):
                lines.append(
                    (
                        "%s is already in %s: saving the new %s replaces it."
                        % (name.strip(), os.path.basename(lib), noun),
                        "warn",
                    )
                )
        if self._kind == "supergenie" and name.strip():
            from ..dialog.supergenie_dialogs import supergenie_name_advice

            advice = supergenie_name_advice(name.strip())
            if advice:
                lines.append((advice, "text_dim"))
        proj = shipped_library(lib) if lib else None
        if proj:
            lines.append(
                (
                    "%s is supplied with Plant SCADA (project %s): a change there "
                    "reaches every project that includes it and is lost on a "
                    "reinstall." % (os.path.basename(lib), proj),
                    "warn",
                )
            )
        return ok, lines

    def _new_validate(self) -> bool:
        if getattr(self, "msg", None) is None:
            return False
        ok, lines = self._new_lines()
        self.msg.setText(
            "<br>".join(
                "<span style='color:%s'>%s</span>"
                % (COLORS.get(role, COLORS["text_dim"]), html.escape(text, quote=False))
                for text, role in lines
            )
        )
        self._new_ok = ok
        self.ok_button.setEnabled(ok)
        return ok

    def _new_edit_template(self) -> None:
        """Edit Template: the dialog closes and the selected template opens for editing
        (``new_target.kind == 'edit_template'``)."""
        from .dialog import BrowserResult

        v = self.tpl_picker.value() if self.tpl_picker is not None else None
        if v is None:
            return
        self.new_target = NewTarget("edit_template", v[0], v[1])
        self.result_value = BrowserResult("template", v[0], v[1], "open")
        self.accept()

    # -- create ---------------------------------------------------------------------------
    def _new_accept(self) -> bool:
        from .dialog import BrowserResult

        if not self._new_validate():
            self.ed_name.setFocus()
            return False
        info = self._target_project()
        st = app_settings()
        if self._new_page_kind():
            name = naming.normalise_page_name(self.ed_name.text())
            path = os.path.join(info.dir, name + ".ctg")
            v = self.tpl_picker.value()
            target = NewTarget(
                "page", path, "", self.tpl_picker.template_ref(), self.chk_new_linked.isChecked()
            )
            st.setValue(
                self._key("template"),
                "|".join(
                    (
                        self.tpl_picker.style_name(),
                        v[1] if v else "",
                        self.tpl_picker.resolution(),
                        "1" if self.tpl_picker.title_bar() else "0",
                    )
                ),
            )
            st.setValue(LINKED_KEY, self.chk_new_linked.isChecked())
        else:
            lib = self._new_library_path()
            target = NewTarget(self._kind, lib, self.ed_name.text().strip())
            st.setValue(self._key("library_" + self._kind), lib)
        self.new_target = target
        self.result_value = BrowserResult(target.kind, target.path, target.element, "new")
        self.accept()
        return True


def target_info(dlg) -> Optional[ProjectInfo]:
    """The project the New mode creates in (tests)."""
    return dlg._target_project()
