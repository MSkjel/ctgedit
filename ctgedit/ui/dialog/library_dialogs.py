"""Library dialogs: Copy To / Save As, Hot Spot Location and Used By."""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..browser.thumbs import PREVIEW_SIZE, ROW_SIZE
from ..prefs.settings import app_settings
from ..style.theme import COLORS

_PREVIEW = PREVIEW_SIZE
_ICON = ROW_SIZE
#: elements listed with a thumbnail (a bigger library lists names only)
_THUMB_LIMIT = 400


def _pixmap_from_summary(s, size: QtCore.QSize):
    rgba = s.thumbnail_rgba() if s is not None else None
    if not rgba:
        return None
    w, h, data = rgba
    img = QtGui.QImage(data, w, h, 4 * w, QtGui.QImage.Format_RGBA8888).copy()
    pm = QtGui.QPixmap.fromImage(img)
    if pm.width() > size.width() or pm.height() > size.height():
        pm = pm.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    return pm


def shipped_library(path: str) -> Optional[str]:
    """The Plant SCADA project a library file belongs to when it is one of
    the supplied ones (Include, System, SA_Include, ...), else None."""
    try:
        from ...format.template_place import shipped_project

        return shipped_project(path)
    except Exception:  # noqa: BLE001
        return None


class CopyToLibraryDialog(QtWidgets.QDialog):
    """Pick a project, library and element name."""

    _TYPES = {
        "ctl": "Symbol libraries (*.ctl)",
        "ctm": "Genie libraries (*.ctm)",
        "ctt": "Template libraries (*.ctt)",
    }

    def __init__(
        self,
        start_dir: str,
        parent=None,
        exts=("ctl", "ctm"),
        library: str = "",
        element: str = "",
        title: str = "Copy to Library",
        name_advice=None,
        on_edit: Optional[Callable] = None,
        on_delete: Optional[Callable] = None,
        catalog=None,
        projects=(),
    ):
        super().__init__(parent)
        self._more_projects = [p for p in (projects or ()) if p]
        self.setWindowTitle(title)
        # ``name_advice(name) -> str``: a naming hint shown under the name
        # (a super genie: start it with '!')
        self._name_advice = name_advice
        self._start_dir = start_dir
        self._exts = tuple(exts)
        self._browse_filter = ";;".join(self._TYPES[e] for e in self._exts)
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._catalog = catalog
        self.edit_requested = None  # (lib_path, element) when Edit closed it
        # (UI guide section 9: a QVBoxLayout with the buttons last, under
        # the two columns)
        page = QtWidgets.QVBoxLayout(self)
        outer = QtWidgets.QHBoxLayout()
        page.addLayout(outer, 1)
        left = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()
        self._cmb_proj = QtWidgets.QComboBox()
        self._cmb_proj.setMinimumWidth(320)
        self._cmb_proj.setToolTip("The project whose library gets the element")
        form.addRow("Project:", self._cmb_proj)
        row = QtWidgets.QHBoxLayout()
        self._cmb_lib = QtWidgets.QComboBox()
        self._cmb_lib.setEditable(True)
        self._cmb_lib.setMinimumWidth(240)
        self._cmb_lib.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self._cmb_lib.lineEdit().setPlaceholderText("Library name (a new name creates it)")
        self._cmb_lib.setAccessibleName("Library")
        row.addWidget(self._cmb_lib, 1)
        self._btn_newlib = QtWidgets.QPushButton("New Library…")
        self._btn_newlib.setToolTip("Create a library in the selected project")
        self._btn_newlib.clicked.connect(self._new_library)
        row.addWidget(self._btn_newlib)
        btn = QtWidgets.QPushButton("Browse…")
        btn.setToolTip("Choose a library file in another folder")
        btn.clicked.connect(self._browse)
        row.addWidget(btn)
        form.addRow("Library:", row)
        left.addLayout(form)
        left.addWidget(QtWidgets.QLabel("In this library:"))
        self._list = QtWidgets.QListWidget()
        self._list.setIconSize(_ICON)
        self._list.setMinimumHeight(170)
        self._list.setToolTip("Click an element to replace it, double-click to edit it")
        self._list.setAccessibleName("Elements in this library")
        self._list.currentItemChanged.connect(self._picked)
        self._list.itemDoubleClicked.connect(lambda _it: self._edit())
        left.addWidget(self._list, 1)
        btns = QtWidgets.QHBoxLayout()
        self._btn_edit = QtWidgets.QPushButton("Edit")
        self._btn_edit.setToolTip("Open the selected element instead")
        self._btn_edit.clicked.connect(self._edit)
        self._btn_new = QtWidgets.QPushButton("New")
        self._btn_new.setToolTip("Type a name for a new element")
        self._btn_new.clicked.connect(self._new_name)
        self._btn_del = QtWidgets.QPushButton("Delete…")
        self._btn_del.setToolTip("Delete the selected element from its library")
        self._btn_del.clicked.connect(self._delete)
        for b in (self._btn_edit, self._btn_new, self._btn_del):
            btns.addWidget(b)
        btns.addStretch(1)
        left.addLayout(btns)
        name_form = QtWidgets.QFormLayout()
        self._ed_elem = QtWidgets.QLineEdit(element)
        self._ed_elem.setMaxLength(31)  # CtDraw's element names
        self._ed_elem.setPlaceholderText("Element name (at most 31 characters)")
        name_form.addRow("Name:", self._ed_elem)
        left.addLayout(name_form)
        self._hint = QtWidgets.QLabel("")
        self._hint.setStyleSheet("color:%s;" % COLORS["text_dim"])
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(QtCore.Qt.RichText)
        left.addWidget(self._hint)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons
        outer.addLayout(left, 1)
        right = QtWidgets.QVBoxLayout()
        self._preview = QtWidgets.QLabel()
        self._preview.setFixedSize(_PREVIEW + QtCore.QSize(8, 8))
        self._preview.setAlignment(QtCore.Qt.AlignCenter)
        self._preview.setFrameShape(QtWidgets.QFrame.StyledPanel)
        right.addWidget(self._preview)
        self._chk_preview = QtWidgets.QCheckBox("Show preview")
        self._chk_preview.setChecked(app_settings().value("library/copy_preview", True, type=bool))
        self._chk_preview.toggled.connect(self._preview_toggled)
        right.addWidget(self._chk_preview)
        right.addStretch(1)
        outer.addLayout(right)
        page.addWidget(buttons)
        self._fill_projects()
        self._cmb_proj.currentIndexChanged.connect(self._project_changed)
        self._cmb_lib.editTextChanged.connect(self._library_changed)
        self._ed_elem.textChanged.connect(self._update_hint)
        if library:
            self._set_library(library)
        else:
            self._cmb_lib.setCurrentIndex(-1)
            self._cmb_lib.setEditText("")
        self._preview_toggled(self._chk_preview.isChecked())
        self._library_changed()

    # -- projects / libraries -----------------------------------------------------------
    def _catalog_or_none(self):
        if self._catalog is not None:
            return self._catalog
        try:
            from ...io.catalog import catalog_for

            self._catalog = catalog_for()
        except Exception:  # noqa: BLE001
            self._catalog = None
        return self._catalog

    def _library_count(self, d: str) -> int:
        try:
            return sum(1 for n in os.listdir(d) if os.path.splitext(n)[1].lower().lstrip(".") in self._exts)
        except OSError:
            return 0

    def _project_label(self, name: str, d: str) -> str:
        """``'Example (3 libraries)'``; a supplied project says so."""
        n = self._library_count(d)
        parts = ["%d librar%s" % (n, "y" if n == 1 else "ies")]
        if shipped_library(os.path.join(d, "x.ctl")):
            parts.append("supplied with Plant SCADA")
        return "%s (%s)" % (name, ", ".join(parts))

    def _fill_projects(self) -> None:
        """The start project and its include chain first (the Graphics
        Builder's search order), a separator, then the other projects of the
        active install (natural order) and the other open pages' projects; a
        folder outside every project is listed first on its own."""
        first, rest = [], []
        cat = self._catalog_or_none()
        if cat is not None:
            try:
                info = cat.project_for_dir(self._start_dir) or cat.folder(self._start_dir)
                first = [(p.name, p.dir) for p in cat.closure(info)]
            except Exception:  # noqa: BLE001
                first = []
            try:
                from ...io.catalog import natural_key

                rest = [(p.name, p.dir) for p in sorted(cat.projects(), key=lambda p: natural_key(p.name))]
            except Exception:  # noqa: BLE001
                rest = []
            for start in self._more_projects:
                try:
                    info = cat.project_for_dir(start) or cat.folder(start)
                    rest.extend((p.name, p.dir) for p in cat.closure(info))
                except Exception:  # noqa: BLE001
                    continue
        if not first or os.path.normcase(os.path.abspath(first[0][1])) != os.path.normcase(
            os.path.abspath(self._start_dir)
        ):
            first.insert(
                0, (os.path.basename(os.path.normpath(self._start_dir)) or self._start_dir, self._start_dir)
            )
        seen = set()
        for group in (first, rest):
            added = False
            for name, d in group:
                key = os.path.normcase(os.path.abspath(d))
                if key in seen:
                    continue
                if not added and group is rest and self._cmb_proj.count():
                    self._cmb_proj.insertSeparator(self._cmb_proj.count())
                added = True
                seen.add(key)
                self._cmb_proj.addItem(self._project_label(name, d), d)
                self._cmb_proj.setItemData(self._cmb_proj.count() - 1, d, QtCore.Qt.ToolTipRole)
        self._cmb_proj.setCurrentIndex(0)
        self._fill_libraries()

    def project_dir(self) -> str:
        return str(self._cmb_proj.currentData() or self._start_dir)

    def _fill_libraries(self) -> None:
        d = self.project_dir()
        keep = self._cmb_lib.currentText()
        self._cmb_lib.blockSignals(True)
        self._cmb_lib.clear()
        try:
            for n in sorted(os.listdir(d), key=str.lower):
                if os.path.splitext(n)[1].lower().lstrip(".") in self._exts:
                    self._cmb_lib.addItem(n, os.path.join(d, n))
        except OSError:
            pass
        self._cmb_lib.setCurrentIndex(-1)
        self._cmb_lib.setEditText(keep if not os.path.dirname(keep) else "")
        self._cmb_lib.blockSignals(False)

    def _project_changed(self, _i=None) -> None:
        self._fill_libraries()
        self._library_changed()

    def _set_library(self, path: str) -> None:
        # the library's own project first (Save As of an element of an
        # included project starts there)
        d = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        for k in range(self._cmb_proj.count()):
            data = self._cmb_proj.itemData(k)
            if data and os.path.normcase(os.path.abspath(str(data))) == d:
                if k != self._cmb_proj.currentIndex():
                    self._cmb_proj.setCurrentIndex(k)
                break
        for k in range(self._cmb_lib.count()):
            if os.path.normcase(str(self._cmb_lib.itemData(k))) == os.path.normcase(path):
                self._cmb_lib.setCurrentIndex(k)
                return
        self._cmb_lib.setEditText(path)

    def _browse(self) -> None:
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select Library",
            self.project_dir(),
            self._browse_filter,
            options=QtWidgets.QFileDialog.DontConfirmOverwrite,
        )
        if path:
            self._set_library(path)

    def new_library_problem(self, text: str) -> str:
        """Why a New Library name cannot be used ('' = it can)."""
        from ...format.element import check_library_name

        text = (text or "").strip()
        if not text:
            return "Enter a name."
        err = check_library_name(text)
        if not err and os.path.exists(os.path.join(self.project_dir(), "%s.%s" % (text, self._exts[0]))):
            err = "%s.%s already exists in this project." % (text, self._exts[0])
        return err

    def _new_library(self) -> None:
        """CtDraw's New Library dialog (NEWLIBRARY): a name, checked by its
        rules as it is typed (OK waits for a usable one); the library is
        created when the element is saved."""
        project = self._cmb_proj.currentText().split(" (")[0]
        prompt = "Name of the new library in project %s:" % project
        dlg = QtWidgets.QInputDialog(self)
        dlg.setWindowTitle("New Library")
        dlg.setLabelText(prompt)
        dlg.setTextValue("")

        def check(text: str) -> None:
            err = self.new_library_problem(text) if text.strip() else ""
            dlg.setLabelText(prompt + ("\n" + err if err else ""))
            box = dlg.findChild(QtWidgets.QDialogButtonBox)
            ok = box.button(QtWidgets.QDialogButtonBox.Ok) if box is not None else None
            if ok is not None:
                ok.setEnabled(bool(text.strip()) and not err)

        dlg.textValueChanged.connect(check)
        QtCore.QTimer.singleShot(0, self, lambda: check(dlg.textValue()))
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        text = dlg.textValue().strip()
        if self.new_library_problem(text):
            return
        self._cmb_lib.setEditText(text)

    def _library_path(self) -> str:
        text = self._cmb_lib.currentText().strip()
        if not text:
            return ""
        k = self._cmb_lib.findText(text, QtCore.Qt.MatchFixedString)
        if k >= 0 and self._cmb_lib.itemData(k):
            return str(self._cmb_lib.itemData(k))
        if not os.path.dirname(text):
            text = os.path.join(self.project_dir(), text)
        if not os.path.splitext(text)[1].lower().lstrip(".") in self._exts:
            text += "." + self._exts[0]
        return text

    # -- the element list --------------------------------------------------------------
    def _library_changed(self, _t=None) -> None:
        self._fill_elements()
        self._update_hint()

    def _fill_elements(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        lib = self._library_path()
        if lib and os.path.exists(lib):
            try:
                from ...format.pagesummary import read_element_summaries

                sums = read_element_summaries(lib)
            except Exception:  # noqa: BLE001
                sums = []
            for k, s in enumerate(sums):
                it = QtWidgets.QListWidgetItem(s.name)
                it.setData(QtCore.Qt.UserRole, s.name)
                if k < _THUMB_LIMIT:
                    pm = _pixmap_from_summary(s, _ICON)
                    if pm is not None:
                        it.setIcon(QtGui.QIcon(pm))
                    it.setData(QtCore.Qt.UserRole + 1, s)
                self._list.addItem(it)
        if not self._list.count():
            # (an empty library says so, rather than an empty list beside an empty preview)
            note = QtWidgets.QListWidgetItem(
                "This library has no elements yet." if lib and os.path.exists(lib) else "A new library."
            )
            note.setFlags(QtCore.Qt.NoItemFlags)
            note.setForeground(QtGui.QBrush(QtGui.QColor(COLORS["text_dim"])))
            self._list.addItem(note)
        self._list.blockSignals(False)
        self._sync_buttons()
        self._show_preview(None)

    def element_names(self) -> list:
        names = (self._list.item(k).data(QtCore.Qt.UserRole) for k in range(self._list.count()))
        return [n for n in names if n is not None]

    def select_element(self, name: str) -> bool:
        for k in range(self._list.count()):
            if self._list.item(k).data(QtCore.Qt.UserRole) is None:
                continue
            if str(self._list.item(k).data(QtCore.Qt.UserRole)).lower() == name.lower():
                self._list.setCurrentRow(k)
                return True
        return False

    def _current(self) -> Optional[str]:
        it = self._list.currentItem()
        if it is None or it.data(QtCore.Qt.UserRole) is None:
            return None  # (none, or the empty library's note)
        return str(it.data(QtCore.Qt.UserRole))

    def _picked(self, cur, _prev=None) -> None:
        if cur is None or cur.data(QtCore.Qt.UserRole) is None:
            self._show_preview(None)
        else:
            self._ed_elem.setText(str(cur.data(QtCore.Qt.UserRole)))
            self._show_preview(cur.data(QtCore.Qt.UserRole + 1))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has = self._current() is not None
        self._btn_edit.setEnabled(has and self._on_edit is not None)
        self._btn_del.setEnabled(has and self._on_delete is not None)

    def _show_preview(self, summary) -> None:
        """An element's picture on the light well of the library cards (it is drawn for a
        light page); nothing to show: the panel's own surface with a note, never a bright
        empty rectangle."""
        from ..style.theme import fixed

        pm = _pixmap_from_summary(summary, _PREVIEW) if summary is not None else None
        if pm is not None:
            self._preview.setStyleSheet(
                "background:%s; color:%s;" % (fixed("well").name(), fixed("well_text").name())
            )
            self._preview.setPixmap(pm)
            return
        self._preview.setStyleSheet(
            "background:%s; color:%s; border:1px dashed %s;"
            % (COLORS["panel"], COLORS["text_dim"], COLORS["border"])
        )
        self._preview.setPixmap(QtGui.QPixmap())
        self._preview.setText("No preview" if summary is not None else "No element selected")
        self._preview.setWordWrap(True)

    def _preview_toggled(self, on: bool) -> None:
        self._preview.setVisible(bool(on))
        app_settings().setValue("library/copy_preview", bool(on))

    def _edit(self) -> None:
        name = self._current()
        if name is None or self._on_edit is None:
            return
        self.edit_requested = (self._library_path(), name)
        self.reject()
        self._on_edit(*self.edit_requested)

    def _new_name(self) -> None:
        self._list.blockSignals(True)
        self._list.setCurrentRow(-1)
        self._list.clearSelection()
        self._list.blockSignals(False)
        self._sync_buttons()
        self._show_preview(None)
        self._ed_elem.clear()
        self._ed_elem.setFocus()

    def _delete(self) -> None:
        name = self._current()
        if name is None or self._on_delete is None:
            return
        if self._on_delete(self._library_path(), name):
            self._fill_elements()
            self._update_hint()

    # -- checks --------------------------------------------------------------------------
    def problem(self) -> str:
        """Why OK cannot close ('' = it can): no library / name, a name the
        library cannot store, a new library name CtDraw would refuse."""
        from ...format.element import check_element_name, check_library_name

        lib = self._library_path()
        name = self._ed_elem.text().strip()
        if not lib:
            return "Choose or type a library."
        if not os.path.exists(lib):
            err = check_library_name(os.path.splitext(os.path.basename(lib))[0])
            if err:
                return err
        return check_element_name(name)

    def _update_hint(self, _t=None) -> None:
        """The line above the buttons: what cannot be saved (OK disabled),
        what the save will do, warnings."""
        import html

        lib = self._library_path()
        name = self._ed_elem.text().strip()
        lines = []  # (text, colour role)
        why = self.problem()
        if why:
            # an empty field is a prompt, a wrong value (a bad library name
            # while the element name is still empty too) an error
            from ...format.element import check_element_name

            empty = not lib or (not name and why == check_element_name(""))
            lines.append((why if why.endswith(".") else why + ".", "text_dim" if empty else "danger"))
        if lib and not os.path.exists(lib) and not why:
            lines.append(("A new library %s will be created." % os.path.basename(lib), "text_dim"))
        replacing = False
        if lib and name and not why and os.path.exists(lib):
            if any(n.lower() == name.lower() for n in self.element_names()):
                replacing = True
                lines.append(
                    ("%s already exists in %s and will be replaced." % (name, os.path.basename(lib)), "warn")
                )
        proj = shipped_library(lib) if lib else None
        if proj:
            lines.append(
                (
                    "%s is supplied with Plant SCADA (project %s). A change there "
                    "reaches every project that includes it and is lost on a "
                    "reinstall." % (os.path.basename(lib), proj),
                    "warn",
                )
            )
        advice = self._name_advice(name) if self._name_advice is not None else ""
        if advice:
            lines.append((advice, "text_dim"))
        self._hint.setText(
            "<br>".join(
                "<span style='color:%s'>%s</span>"
                % (COLORS.get(role, COLORS["text_dim"]), html.escape(text, quote=False))
                for text, role in lines
            )
        )
        self._hint.setVisible(bool(lines))
        ok = self._buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(not why)
            # (a click on a listed element fills its name: the button then says it replaces,
            # not a plain OK after a browsing click)
            if ok.property("ctg_ok_text") is None:
                ok.setProperty("ctg_ok_text", ok.text())
            ok.setText("Replace" if replacing else str(ok.property("ctg_ok_text")))

    def accept(self) -> None:
        if self.problem():
            self._update_hint()
            return
        super().accept()

    def values(self):
        return self._library_path(), self._ed_elem.text().strip()


class HotSpotDialog(QtWidgets.QDialog):
    """Hot Spot Location (as in CtDraw): the X / Y position of the
    element's hot spot (the point a paste of the element lands on) in the
    element window's coordinates."""

    #: two spin boxes and three buttons: a fixed size (UI guide section 9)
    RESIZE = "none"

    def __init__(self, x: int, y: int, parent=None, extent=None):
        super().__init__(parent)
        self.setWindowTitle("Hot Spot Location")
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "The point of the symbol that goes where you click when you place it. "
            "Placed copies keep their hot spot where it is."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self._x = QtWidgets.QSpinBox()
        self._y = QtWidgets.QSpinBox()
        for sp, v in ((self._x, x), (self._y, y)):
            sp.setRange(-32768, 32767)
            sp.setValue(int(v))
            sp.setSuffix(" px")
        form.addRow("X position:", self._x)
        form.addRow("Y position:", self._y)
        lay.addLayout(form)
        if extent is not None:
            l, t, r, b = extent
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Move to:"))
            for label, pt in (
                ("Top Left", (l, t)),
                ("Centre", ((l + r) // 2, (t + b) // 2)),
                ("Bottom Left", (l, b)),
            ):
                btn = QtWidgets.QPushButton(label)
                btn.setToolTip(
                    "Put the hot spot on the %s of the element's objects (%d, %d)"
                    % (label.lower(), pt[0], pt[1])
                )
                btn.clicked.connect(lambda _c=False, p=pt: self._put(p))
                row.addWidget(btn)
            row.addStretch(1)
            lay.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _put(self, pt) -> None:
        self._x.setValue(int(pt[0]))
        self._y.setValue(int(pt[1]))

    def values(self):
        return self._x.value(), self._y.value()


class UsedByDialog(QtWidgets.QDialog):
    """The pages that place a library element."""

    def __init__(
        self,
        lib_path: str,
        element: str,
        usage,
        parent=None,
        on_open: Optional[Callable] = None,
        note: str = "",
        stay_open: bool = False,
    ):
        super().__init__(parent)
        self._stay_open = bool(stay_open)
        stem = os.path.splitext(os.path.basename(lib_path))[0]
        self.setWindowTitle("Used By - %s.%s" % (stem, element))
        self.setMinimumSize(460, 320)
        self._on_open = on_open
        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel(
            ("%s.%s is placed on %d page%s:" % (stem, element, len(usage), "" if len(usage) == 1 else "s"))
            if usage
            else "No page of its project, or of the projects that include it, places %s.%s." % (stem, element)
        )
        head.setWordWrap(True)
        lay.addWidget(head)
        self._list = QtWidgets.QTreeWidget()
        self._list.setHeaderLabels(["Page", "Project", "Title"])
        self._list.setRootIsDecorated(False)
        for e in usage:
            it = QtWidgets.QTreeWidgetItem([e.name, e.project, e.title or ""])
            it.setData(0, QtCore.Qt.UserRole, e.path)
            self._list.addTopLevelItem(it)
        from ..tables import make_resizable

        make_resizable(self._list, "UsedByDialog/pages")
        self._list.itemDoubleClicked.connect(lambda _it, _c: self._open())
        lay.addWidget(self._list, 1)
        if note:
            n = QtWidgets.QLabel(note)
            n.setWordWrap(True)
            n.setStyleSheet("color:%s;" % COLORS["text_dim"])
            lay.addWidget(n)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self._btn_open = buttons.addButton("Open Page", QtWidgets.QDialogButtonBox.ActionRole)
        self._btn_open.setEnabled(bool(usage) and on_open is not None)
        self._btn_open.clicked.connect(self._open)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def pages(self) -> list:
        return [self._list.topLevelItem(k).text(0) for k in range(self._list.topLevelItemCount())]

    def _open(self) -> None:
        it = self._list.currentItem() or (
            self._list.topLevelItem(0) if self._list.topLevelItemCount() else None
        )
        if it is None or self._on_open is None:
            return
        if not self._stay_open:
            self.accept()
        self._on_open(it.data(0, QtCore.Qt.UserRole))


class ElementUsersDialog(QtWidgets.QDialog):
    """Used By: every page and element that uses an element, directly or through genies."""

    RESIZE = "both"

    def __init__(
        self,
        lib_path: str,
        element: str,
        usage,
        parent=None,
        on_open: Optional[Callable] = None,
        on_update: Optional[Callable] = None,
        on_refresh: Optional[Callable] = None,
        note: str = "",
    ):
        super().__init__(parent)
        stem = os.path.splitext(os.path.basename(lib_path))[0]
        self.setWindowTitle("Used By - %s.%s" % (stem, element))
        self.setMinimumSize(560, 360)
        self.lib_path, self.element = lib_path, element
        self._label = "%s.%s" % (stem, element)
        self._on_open, self._on_update, self._on_refresh = on_open, on_update, on_refresh
        self.usage = None
        #: a library or page was written since its users were read (the editor marks it:
        #: LibraryCommands._follow_used_by_windows); set_usage clears it
        self.stale = False
        lay = QtWidgets.QVBoxLayout(self)
        self._head = QtWidgets.QLabel()
        self._head.setWordWrap(True)
        lay.addWidget(self._head)
        self._only_stale = QtWidgets.QCheckBox("Show only what needs an update")
        self._only_stale.toggled.connect(lambda _on: self._fill())
        lay.addWidget(self._only_stale)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Type", "Project", "Status", "Through"])
        self._tree.setUniformRowHeights(True)
        from ..tables import make_resizable

        make_resizable(self._tree, "ElementUsersDialog/users")
        self._tree.itemDoubleClicked.connect(lambda _it, _c: self._open())
        self._tree.currentItemChanged.connect(lambda *_a: self._sync())
        lay.addWidget(self._tree, 1)
        self._note = QtWidgets.QLabel(note)
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color:%s;" % COLORS["text_dim"])
        self._note.setVisible(bool(note))
        lay.addWidget(self._note)
        # the library files that could not be read (whole): set_usage
        self._problems = QtWidgets.QLabel()
        self._problems.setWordWrap(True)
        self._problems.setVisible(False)
        lay.addWidget(self._problems)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self._btn_open = buttons.addButton("Open", QtWidgets.QDialogButtonBox.ActionRole)
        self._btn_open.clicked.connect(self._open)
        self._btn_update = buttons.addButton("Update Out of Date…", QtWidgets.QDialogButtonBox.ActionRole)
        self._btn_update.setToolTip(
            "Save the genies that use it again (the ones it is nested in first), then update "
            "the pages, so every user shows its current version"
        )
        self._btn_update.clicked.connect(self._update)
        self._btn_refresh = buttons.addButton("Refresh", QtWidgets.QDialogButtonBox.ActionRole)
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_refresh.setVisible(on_refresh is not None)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.set_usage(usage)

    # -- contents -------------------------------------------------------------------------
    def set_usage(self, usage) -> None:
        self.usage = usage
        self.stale = False
        if usage is None or not usage.users:
            self._head.setText(
                "No page or genie of its project, or of the projects that include it, uses %s." % self._label
            )
        else:
            c = usage.counts()

            def n(k, one, many):
                v = c.get(k, 0)
                return "%d %s" % (v, one if v == 1 else many)

            direct = c["direct_pages"] + c["direct_elements"]
            indirect = len(usage.users) - direct
            parts = ["%d directly" % direct]
            if indirect:
                parts.append("%d through other genies" % indirect)
            text = "%s is used by %s and %s (%s)." % (
                self._label,
                n("pages", "page", "pages"),
                n("elements", "library element", "library elements"),
                ", ".join(parts),
            )
            if c["out_of_date"]:
                text += " %d of them need an update." % c["out_of_date"]
            else:
                text += " All of them are up to date."
            self._head.setText(text)
        self._problems.setText(self.problems_text(usage))
        self._problems.setVisible(bool(self._problems.text()))
        self._fill()

    @staticmethod
    def problems_text(usage) -> str:
        """The line under the list naming the library files that could not be read (whole),
        '<project>\\<file>' (the log says why); '' when there is none."""
        bad = list(getattr(usage, "unreadable", None) or ()) if usage is not None else []
        if not bad:
            return ""
        names = []
        for f, el in bad:
            p = os.path.normpath(os.path.abspath(f))
            n = os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p))
            n += " (%s)" % el if el else ""
            if n not in names:
                names.append(n)
        more = " and %d more" % (len(names) - 10) if len(names) > 10 else ""
        return (
            "Some library files could not be read in full: %s%s. Their genies are listed as far as "
            "they could be read, and Update skips them."
        ) % (", ".join(names[:10]), more)

    def _status_text(self, u) -> str:
        from ...io import genieusage as GU

        if u.status == GU.OUT_OF_DATE:
            return "Out of date"
        if u.status == GU.WAITING:
            below = None
            for lk in u.links:
                b = self.usage.of(lk.target) if self.usage is not None else None
                if b is not None and b.needs_update:
                    below = b
                    break
            return ("Out of date once %s is updated" % below.ref.label) if below else "Out of date"
        return "Up to date"

    def _fill(self) -> None:
        cur = self._current_user()
        cur_key = cur.ref.key if cur is not None else None
        self._tree.clear()
        usage = self.usage
        if usage is None:
            self._sync()
            return
        only = self._only_stale.isChecked()
        dim = QtGui.QBrush(QtGui.QColor(COLORS["text_dim"]))
        warn = QtGui.QBrush(QtGui.QColor(COLORS.get("warn", "#d9a441")))
        for title, users in (
            ("Uses it directly", usage.direct()),
            ("Uses it through other genies", usage.indirect()),
        ):
            rows = [u for u in users if u.needs_update or not only]
            if not users:
                continue
            g = QtWidgets.QTreeWidgetItem(["%s (%d)" % (title, len(rows))])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            g.setFlags(g.flags() & ~QtCore.Qt.ItemIsSelectable)
            self._tree.addTopLevelItem(g)
            g.setFirstColumnSpanned(True)
            for u in sorted(rows, key=lambda u: (u.ref.kind != "element", u.ref.label.lower())):
                what = u.ref.what
                if u.ref.kind == "page" and u.registered is False:
                    what += " (not registered)"
                it = QtWidgets.QTreeWidgetItem(
                    [
                        u.ref.label,
                        what,
                        u.ref.project,
                        self._status_text(u),
                        " > ".join(r.label for r in reversed(u.via)),
                    ]
                )
                it.setData(0, QtCore.Qt.UserRole, u)
                if u.needs_update:
                    it.setForeground(3, warn)
                if u.shipped or getattr(u, "damaged", ""):
                    it.setToolTip(
                        0,
                        "Its library file could not be read whole: Update leaves it alone"
                        if getattr(u, "damaged", "")
                        else "In a library supplied with Plant SCADA: Update leaves it alone",
                    )
                    for col in range(5):
                        it.setForeground(col, dim)
                g.addChild(it)
                if cur_key is not None and u.ref.key == cur_key:
                    self._tree.setCurrentItem(it)
            g.setExpanded(True)
        self._sync()

    def users(self) -> list:
        """(name, status, through) of the rows shown (tests)."""
        out = []
        for k in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(k)
            for j in range(g.childCount()):
                it = g.child(j)
                out.append((it.text(0), it.text(3), it.text(4)))
        return out

    def _current_user(self):
        it = self._tree.currentItem() if hasattr(self, "_tree") else None
        return it.data(0, QtCore.Qt.UserRole) if it is not None else None

    @staticmethod
    def updatable(u) -> bool:
        """``u`` (a user) needs an update that Update Out of Date can make: not a page left
        because it is not registered in its project, as Update Pages leaves it, nor an
        element of a library supplied with Plant SCADA or of a file not read whole."""
        if not u.needs_update:
            return False
        if u.ref.kind == "page":
            return u.registered is not False
        return not (u.shipped or getattr(u, "damaged", ""))

    def _sync(self) -> None:
        self._btn_open.setEnabled(self._on_open is not None and self._current_user() is not None)
        need = self.usage is not None and any(self.updatable(u) for u in self.usage.users)
        self._btn_update.setEnabled(self._on_update is not None and need)

    # -- actions ----------------------------------------------------------------------------
    def _open(self) -> None:
        u = self._current_user()
        if u is not None and self._on_open is not None:
            self._on_open(u.ref)

    def _update(self) -> None:
        """Update Out of Date: the users are read again first (``on_refresh``), so the
        update acts on what is out of date now, not on what was when the list was read.
        A read that is cancelled or fails updates nothing."""
        if self._on_update is None:
            return
        if self._on_refresh is not None:
            usage = self._on_refresh()
            if usage is None:
                return
            self.set_usage(usage)
        if self.usage is not None:
            self._on_update(self.usage)

    def refresh(self) -> None:
        """Read the users again (``on_refresh``: the editor window shows its progress
        window with Cancel; None = cancelled or not readable, the list stays)."""
        if self._on_refresh is None:
            return
        usage = self._on_refresh()
        if usage is not None:
            self.set_usage(usage)
