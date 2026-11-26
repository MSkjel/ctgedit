"""The Find and Replace window."""

from __future__ import annotations
from ..style.theme import sp as _sp

import contextlib
import json
import os

from PySide6 import QtCore, QtGui, QtWidgets

from ...features import find_replace as FR
from ..jobs.run_stats import RunStats
from ..prefs.settings import app_settings

#: the result list's column that shows the field after the replacement
BECOMES = 4

_SCOPE_LABELS = [
    (FR.SCOPE_TEXT, "Text", "Captions of texts and buttons, and display value texts"),
    (
        FR.SCOPE_EXPR,
        "Expressions and commands",
        "Animation expressions, touch and keyboard commands, tag associations and other object properties",
    ),
    (FR.SCOPE_PARAM, "Genie parameters", "The parameter values of placed genies"),
    (
        FR.SCOPE_NAME,
        "Names, descriptions and metadata",
        "Object names, descriptions, tool tips and metadata values",
    ),
    (
        FR.SCOPE_PAGE,
        "Page properties",
        "The page's title, description, events, keyboard commands and associations",
    ),
]
_MATCH_LABELS = [
    (FR.MATCH_PART, "Anywhere in a field"),
    (FR.MATCH_WORD, "Whole tag names and words only"),
    (FR.MATCH_WHOLE, "Match whole field only"),
]
_MATCH_TIPS = {
    FR.MATCH_WORD: "LOOP_1 does not match LOOP_10 or LOOP_1_SP",
    FR.MATCH_WHOLE: "The field must hold exactly this text",
}


def _group(title: str):
    """(a titled box, its column layout): one group of the Options."""
    box = QtWidgets.QGroupBox(title)
    lay = QtWidgets.QVBoxLayout(box)
    lay.setSpacing(_sp(1))
    return box, lay


def _radio_row(buttons) -> QtWidgets.QWidget:
    """A row of ``buttons`` side by side, one choice among them: a button group of their own
    (the row's ``group``), as the window's rows of radio buttons share a parent."""
    row = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(_sp(4))
    group = QtWidgets.QButtonGroup(row)
    for b in buttons:
        group.addButton(b)
        lay.addWidget(b)
    lay.addStretch(1)
    row.group = group
    return row


def page_name(doc) -> str:
    """The name a document's rows show in the Page column."""
    name = getattr(doc, "display_name", "") or ""
    if name:
        return name
    page = getattr(doc, "page", None)
    return getattr(page, "name", "") or os.path.splitext(os.path.basename(getattr(doc, "path", "") or ""))[0]


def object_label(doc, i: int) -> str:
    """'<class> AN<n> "<name>"', the way Goto Object labels the object."""
    from ...features.object_labels import object_label as _label

    return _label(doc, i)


class ClosedHit:
    """A match in a page that is not open (find_worker's row). Go To, Properties and
    Replace open its page first, and the row becomes that page's own hit."""

    po = None
    doc = None
    genie = None
    genie_po = None

    def __init__(self, row: dict, rank: int):
        self.path = row["page"]
        self.page = row["name"]
        self.project = row.get("project", "")
        self.index = int(row["index"])
        self.object = row.get("object") or ""
        self.scope, self.kind, self.key = row["scope"], row["kind"], row["key"]
        self.value, self.label = row["value"], row["label"]
        self.reason = row.get("reason") or ""
        self.params = tuple(row.get("params") or ())
        self.sub = int(row.get("sub") or 0)
        self.rank = rank

    @property
    def replaceable(self) -> bool:
        return not self.reason

    @property
    def via_genie(self) -> bool:
        return self.kind == FR.K_PARAM or bool(self.params)

    def same_field(self, h) -> bool:
        return (h.index, h.kind, h.key.upper(), h.sub) == (self.index, self.kind, self.key.upper(), self.sub)


# (the project choice is pick_fields.ProjectScopeField's, shared with Replace Genie)
from ..pick_fields import project_choices, project_dirs  # noqa: E402,F401


@contextlib.contextmanager
def _busy():
    """A wait cursor while a search / replace runs (re-composing many genies
    on a big page takes seconds)."""
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        yield
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()


def page_for_key(key: str) -> str:
    """The object sheet page that edits property ``key`` ('' = its first)."""
    up = (key or "").upper()
    if up.startswith("METADATA_"):
        return "metadata"
    if up.startswith("AI_"):
        return "alarm_indicator"
    if up.startswith("VERTEX_"):
        return "vertices"
    if up.startswith("ACTIVEX_"):
        return "tag_association"
    if up.startswith("COL_"):
        return "fill_color"
    if up.startswith("FIL"):
        return "fill_level"
    if up.startswith(("SET_ARR", "SYMBOL")):
        return "set_object"
    if up.startswith(("TXT_", "STATE_", "ARRAY_")) or up == "TXTMODE":
        return "display_value"
    if up in ("__AN_NAME__", "NAME", "DESC", "COMMENT", "AREA", "PRIV", "DEVICE"):
        return "access"
    if up in ("DISABLED", "DISMODE", "GREY_STYLE"):
        return "disable"
    if up == "VISIBLE":
        return "visibility"
    for prefix, page in (
        ("MVXSL", "slider_h"),
        ("MVYSL", "slider_v"),
        ("ROTSL", "slider_rot"),
        ("MVX", "move_h"),
        ("MVY", "move_v"),
        ("ROT", "move_rot"),
        ("SZX", "scale_h"),
        ("SZY", "scale_v"),
    ):
        if up.startswith(prefix):
            return page
    return ""


class FindReplaceDialog(QtWidgets.QDialog):
    """Edit > Find / Replace."""

    #: a hit to show: (document, object index; -1 = the page itself)
    found = QtCore.Signal(object, int)
    #: replacements changed these documents (a list)
    replaced = QtCore.Signal(object)
    #: 'Properties…' on a row: (document, object index, the hit)
    propertiesRequested = QtCore.Signal(object, int, object)

    def __init__(
        self,
        doc,
        parent=None,
        replace_mode: bool = False,
        documents=None,
        current=None,
        template_editable=None,
        break_lock=None,
        open_page=None,
    ):
        super().__init__(parent)
        #: open a closed page in the editor: open_page(path) -> its document or None
        self._open_page = open_page
        #: the closed pages' search running now (ui/jobs/helper_job), its options key
        self._job = None
        self._closed_key = None
        self._closed: list = []
        self._closed_note = ""
        self._after_search = None
        self._doc = doc
        self._current = current or (lambda: self._doc)
        self._documents = documents or (lambda: [d for d in (self._current(),) if d])
        self._template_editable = template_editable or (lambda: False)
        self._break_lock = break_lock or (lambda: False)
        self._hits: list = []
        self._key = None
        self._pos = -1
        #: the object of the current hit (its index when found; -1 = none)
        self._cursor = -1
        #: (document, record, its index) of the last hit shown -- the index
        #: stands in when the record is gone (a genie re-composition makes
        #: new member records at the same places)
        self._last = None
        self._replace_mode = False
        self.setMinimumWidth(640)
        self.resize(720, 560)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self._ed_find = QtWidgets.QLineEdit()
        self._ed_find.setPlaceholderText("Text to find (not case-sensitive)")
        self._ed_find.setAccessibleName("Find what")
        # the '.*' toggle every search box has (theme.RegexToggle): a regular expression, and
        # the replacement may name its groups
        from ..style.theme import RegexToggle

        self._find_regex = RegexToggle(self._ed_find, "Find what")
        form.addRow("Find what:", self._ed_find)
        self._ed_repl = QtWidgets.QLineEdit()
        self._ed_repl.setPlaceholderText(self._repl_placeholder(self._find_regex.on))
        # (the group syntax stays readable once something is typed)
        self._ed_repl.setToolTip(
            "With .* on, \\1 or \\g<name> in the replacement inserts a group of the match"
        )
        self._find_regex.TIP_ON = (
            RegexToggle.TIP_ON + ". In Replace with, \\1 or \\g<name> inserts a group of the match"
        )
        self._find_regex._show()
        form.addRow("Replace with:", self._ed_repl)
        self._lbl_repl = form.labelForField(self._ed_repl)
        lay.addLayout(form)

        # Look in: one row of choices; the project's row under it shows with 'All pages of
        # a project'
        self._rb_page = QtWidgets.QRadioButton("This page")
        self._rb_all = QtWidgets.QRadioButton("All open pages")
        self._rb_project = QtWidgets.QRadioButton("All pages of a project")
        self._rb_project.setToolTip(
            (
                "Every page of the project, open or not. Replace All saves the closed pages, each "
                "backed up first in the backups folder (Tools > Restore Backup)"
            )
        )
        self._look_in = _radio_row((self._rb_page, self._rb_all, self._rb_project))
        self._rb_page.setChecked(True)
        form.addRow("Look in:", self._look_in)
        # the project and its includes (shared with Replace Genie)
        from ..pick_fields import ProjectScopeField

        self._scope = ProjectScopeField(getattr(doc, "path", "") or "", horizontal=True)
        self._cb_project, self._cb_includes = self._scope.combo, self._scope.includes
        self._project_here = self._scope.here
        # the helper processes that search the closed pages at once (the same setting as
        # Settings > Pages and libraries > Find in closed pages: opt/find_processes)
        from ..prefs.settings import FIND_PROCESSES_RANGE

        self._procs_label = QtWidgets.QLabel("Helper processes:")
        self._procs = QtWidgets.QSpinBox()
        self._procs.setRange(*FIND_PROCESSES_RANGE)
        self._procs.setSpecialValueText("Automatic")
        self._procs.setToolTip(
            "How many helper processes search the closed pages at once. "
            "Automatic: half the processors, at most 4. Each takes about "
            "300 MB while it runs."
        )
        self._procs_label.setBuddy(self._procs)
        try:
            # (as text: Qt reads a garbled number as 0, not as an error)
            self._procs.setValue(int(str(app_settings().value("opt/find_processes", 0)).strip()))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows Automatic
            self._procs.setValue(0)
        self._procs.valueChanged.connect(lambda v: app_settings().setValue("opt/find_processes", int(v)))
        self._procs_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self._procs_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._scope, 1)
        row.addSpacing(_sp(3))
        row.addWidget(self._procs_label)
        row.addWidget(self._procs)
        form.addRow("Project:", self._procs_row)
        self._lbl_project = form.labelForField(self._procs_row)
        self._rb_project.toggled.connect(self._sync_project_row)
        self._sync_project_row(self._rb_project.isChecked())
        self._build_options(lay)
        self._build_buttons(lay, doc, replace_mode)

    def _sync_project_row(self, on: bool) -> None:
        self._procs_row.setVisible(bool(on))
        self._lbl_project.setVisible(bool(on))

    def _build_options(self, lay):
        # Search in, Match, Find Next's direction, Include and Filters: under Options, closed
        # by default, its line naming what is not the default
        self._opt_toggle = QtWidgets.QToolButton()
        self._opt_toggle.setObjectName("FindOptionsToggle")
        self._opt_toggle.setCheckable(True)
        self._opt_toggle.setAutoRaise(True)
        self._opt_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._opt_toggle.setArrowType(QtCore.Qt.RightArrow)
        self._opt_box = QtWidgets.QWidget()
        self._opt_box.setObjectName("FindOptions")
        self._opt_toggle.toggled.connect(self._show_options)
        lay.addWidget(self._opt_toggle)
        lay.addWidget(self._opt_box)
        # the choices grouped by what they are about, one to a line: Search in and Find
        # Next on the left, Match and Include on the right, the Filters under both
        outer = QtWidgets.QVBoxLayout(self._opt_box)
        outer.setContentsMargins(_sp(3), _sp(2), _sp(3), _sp(3))
        cols = QtWidgets.QHBoxLayout()
        left, right = QtWidgets.QVBoxLayout(), QtWidgets.QVBoxLayout()
        self._cb_scope = {}
        box, bl = _group("Search in")
        for scope, label, tip in _SCOPE_LABELS:
            cb = QtWidgets.QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(True)
            self._cb_scope[scope] = cb
            bl.addWidget(cb)
        left.addWidget(box)
        box, bl = _group("Find Next goes")
        self._rb_fwd = QtWidgets.QRadioButton("Forward")
        self._rb_back = QtWidgets.QRadioButton("Backward")
        self._dir_group = _radio_row((self._rb_fwd, self._rb_back))
        self._rb_fwd.setChecked(True)
        bl.addWidget(self._dir_group)
        left.addWidget(box)
        left.addStretch(1)
        box, bl = _group("Match")
        self._rb_match = {}
        for mode, label in _MATCH_LABELS:
            rb = QtWidgets.QRadioButton(label)
            rb.setToolTip(_MATCH_TIPS.get(mode, ""))
            self._rb_match[mode] = rb
            bl.addWidget(rb)
        self._rb_match[FR.MATCH_PART].setChecked(True)
        right.addWidget(box)
        box, bl = _group("Include")
        self._cb_genies = QtWidgets.QCheckBox("Inside genies and symbols")
        self._cb_genies.setToolTip("The objects a placed genie or symbol consists of")
        self._cb_genies.setChecked(True)
        self._cb_template = QtWidgets.QCheckBox("Inside the page template")
        self._cb_template.setToolTip("The objects the page takes from its template")
        bl.addWidget(self._cb_genies)
        bl.addWidget(self._cb_template)
        right.addWidget(box)
        right.addStretch(1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        outer.addLayout(cols)
        from .find_filters import FilterList

        box, bl = _group("Filters")
        self._filter_list = FilterList()
        self._filter_list.changed.connect(self._sync_options_text)
        self._filter_list.changed.connect(self._filters_resized)
        bl.addWidget(self._filter_list)
        outer.addWidget(box)
        for b in (
            [self._cb_genies, self._cb_template, self._rb_back, self._rb_fwd]
            + list(self._cb_scope.values())
            + list(self._rb_match.values())
        ):
            b.toggled.connect(lambda *_a: self._sync_options_text())
        shown = app_settings().value("find/options_shown", False, type=bool)
        self._opt_toggle.setChecked(bool(shown))
        self._show_options(bool(shown))

        self._list = QtWidgets.QTreeWidget()
        self._list.setColumnCount(5)
        # Becomes: the field after the replacement, as Replace with is typed (Replace only)
        self._list.setHeaderLabels(["Page", "Object", "Field", "Text", "Becomes"])
        self._ed_repl.textChanged.connect(self._update_becomes)
        self._b_stop = QtWidgets.QToolButton()
        self._b_stop.setText("Stop")
        self._b_stop.setToolTip("Stop searching the closed pages")
        self._b_stop.setVisible(False)
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setAlternatingRowColors(True)
        # (eight rows at least: the options above never squeeze the results away)
        self._list.setMinimumHeight(self._list.fontMetrics().height() * 10)
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self._list, "FindReplaceDialog/results")
        enable_row_copy(self._list)
        self._list.itemDoubleClicked.connect(lambda *_a: self.go_to())
        self._list.currentItemChanged.connect(self._on_row)
        lay.addWidget(self._list, 1)
        self._status = QtWidgets.QLabel()

    def _filter_values(self) -> dict:
        """{FindOptions field: NameFilter} of the Filters rows."""
        return self._filter_list.values()

    def _filters_resized(self) -> None:
        if self.isVisible() and self._opt_toggle.isChecked():
            self._fit_height()

    def _build_buttons(self, lay, doc, replace_mode):
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(self._status, 1)
        # (a failure's status line has Open Log beside it, never 'see the log';
        # a Replace All that saved closed pages has Open Backup Folder)
        from ..messages import open_log_button

        self._b_log = open_log_button(self)
        srow.addWidget(self._b_log)
        self._b_backup = QtWidgets.QToolButton()
        self._b_backup.setText("Open Backup Folder")
        self._b_backup.setAutoRaise(True)
        self._b_backup.setVisible(False)
        self._backup_folder = ""
        self._b_backup.clicked.connect(self._open_backup_folder)
        srow.addWidget(self._b_backup)
        # one way back from a Replace All, open and closed pages alike (the open tabs' replace
        # step undone, the closed pages put back from this run's backup)
        self._b_undo_all = QtWidgets.QToolButton()
        self._b_undo_all.setText("Undo Replace All")
        self._b_undo_all.setAutoRaise(True)
        self._b_undo_all.setToolTip(
            "Undo the last Replace All: its step in each open page, and the closed pages it saved "
            "put back from their backup"
        )
        self._b_undo_all.setVisible(False)
        self._undo_set = None
        self._b_undo_all.clicked.connect(self.undo_replace_all)
        srow.addWidget(self._b_undo_all)
        srow.addWidget(self._b_stop)
        self._b_stop.clicked.connect(self._stop_closed)
        lay.addLayout(srow)

        btns = QtWidgets.QHBoxLayout()
        self._b_all = QtWidgets.QPushButton("Find All")
        self._b_all.setToolTip("List every field that contains the text")
        self._b_all.clicked.connect(self.find_all)
        self._b_next = QtWidgets.QPushButton("Find Next")
        self._b_next.setToolTip("Go to the next match in the chosen direction (F3)")
        self._b_next.setDefault(True)
        self._b_next.clicked.connect(self.find_next)
        self._b_rep = QtWidgets.QPushButton("Replace")
        self._b_rep.setToolTip("Replace the text in the selected row, then go to the next match")
        self._b_rep.clicked.connect(self.replace_one)
        self._b_repall = QtWidgets.QPushButton("Replace All")
        self._b_repall.setToolTip("Replace every match (one undo step per page)")
        self._b_repall.clicked.connect(self.replace_all)
        self._b_goto = QtWidgets.QPushButton("Goto")  # (the menu's Goto Object)
        self._b_goto.setToolTip("Select the object of the selected row on its page")
        self._b_goto.clicked.connect(self.go_to)
        self._b_props = QtWidgets.QPushButton("Properties…")
        self._b_props.setToolTip("Open the properties of the selected row")
        self._b_props.clicked.connect(self.open_properties)
        for b in (self._b_all, self._b_next, self._b_rep, self._b_repall, self._b_goto, self._b_props):
            btns.addWidget(b)
        btns.addStretch(1)
        # (a button box: the help system adds its Help button there)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        box.button(QtWidgets.QDialogButtonBox.Close).setAutoDefault(False)
        box.rejected.connect(self.reject)
        btns.addWidget(box)
        lay.addLayout(btns)
        sc = QtGui.QShortcut(QtGui.QKeySequence("F3"), self)
        sc.activated.connect(self.find_next)
        sc = QtGui.QShortcut(QtGui.QKeySequence("Shift+F3"), self)
        sc.activated.connect(lambda: self.find_next(backward=True))

        self._restore_options()
        if doc is None:
            # no page open: the closed pages of a project (the project Update Pages ran on
            # last, else the first)
            self._rb_project.setChecked(True)
            last = str(app_settings().value("updatepages/root", "") or "")
            if last and self._cb_project.findText(last) >= 0:
                self._cb_project.setCurrentText(last)
        self._sync_page_choices()
        for w in [
            self._rb_page,
            self._rb_all,
            self._rb_project,
            self._cb_includes,
            self._cb_genies,
            self._cb_template,
            *self._cb_scope.values(),
            *self._rb_match.values(),
        ]:
            w.toggled.connect(self._options_changed)
        self._filter_list.changed.connect(self._options_changed)
        self._cb_project.currentTextChanged.connect(self._project_changed)
        self._sync_project_widgets()
        self._ed_find.textChanged.connect(self._options_changed)
        self._find_regex.toggled.connect(self._regex_toggled)
        self._rb_back.toggled.connect(self._save_options)
        self.set_replace_mode(replace_mode)
        self._update_buttons()
        self._ed_find.setFocus()

    # -- options ------------------------------------------------------------------
    def set_replace_mode(self, on: bool) -> None:
        """Show (Replace, Ctrl+H) or hide (Find, Ctrl+F) the replace fields."""
        self._replace_mode = bool(on)
        self.setWindowTitle("Replace" if on else "Find")
        for w in (self._ed_repl, self._lbl_repl, self._b_rep, self._b_repall):
            if w is not None:
                w.setVisible(self._replace_mode)
        self._list.setColumnHidden(BECOMES, not self._replace_mode)
        self._ed_find.setFocus()
        self._ed_find.selectAll()

    def preset(
        self,
        text: str,
        *,
        regex: bool = False,
        match: str = FR.MATCH_PART,
        project: str = "",
        includes: bool = False,
    ) -> None:
        """Start a new search from another window (Rename Pages' Find References): ``text``,
        the '.*' toggle, the match, every field (the Search in boxes ticked, the Filters
        emptied) and, with ``project``, every page of that project (and of those it includes,
        with ``includes``)."""
        self._find_regex.set_on(regex)
        self._ed_find.setText(text)
        if match in self._rb_match:
            self._rb_match[match].setChecked(True)
        for cb in self._cb_scope.values():
            cb.setChecked(True)
        self._filter_list.clear()
        if project and self._scope.set_project(project):
            self._rb_project.setChecked(True)
            self._cb_includes.setChecked(bool(includes))

    def options(self) -> FR.FindOptions:
        match = next((m for m, rb in self._rb_match.items() if rb.isChecked()), FR.MATCH_PART)
        try:
            tpl_edit = bool(self._template_editable())
        except Exception:  # noqa: BLE001
            tpl_edit = False
        try:
            unlocked = bool(self._break_lock())
        except Exception:  # noqa: BLE001
            unlocked = False
        return FR.FindOptions(
            self._ed_find.text(),
            scopes=frozenset(s for s, cb in self._cb_scope.items() if cb.isChecked()),
            match=match,
            inside_genies=self._cb_genies.isChecked(),
            inside_templates=self._cb_template.isChecked(),
            template_editable=tpl_edit,
            break_lock=unlocked,
            regex=self._find_regex.on,
            **self._filter_values(),
        )

    def _targets(self) -> list:
        """The documents to search, the current page first. (Documents
        compare by identity: EditableDocument is a dataclass, whose == would
        compare every field.)"""
        cur = self._current()
        if not (self._rb_all.isChecked() or self._rb_project.isChecked()):
            return [cur] if cur is not None else []
        try:
            docs = list(self._documents() or [])
        except Exception:  # noqa: BLE001
            docs = []
        docs = [d for d in docs if d is not cur]
        out = ([cur] if cur is not None else []) + docs
        if self._rb_project.isChecked():
            dirs = {os.path.normcase(os.path.abspath(d)) for d in self.project_folders()}
            out = [
                d
                for d in out
                if getattr(d, "path", "")
                and os.path.normcase(os.path.dirname(os.path.abspath(d.path))) in dirs
            ]
        return out

    # -- the closed pages of a project ---------------------------------------------
    def project_folders(self) -> list:
        return self._scope.project_dirs()

    def _project_changed(self, _t) -> None:
        self._options_changed()

    def _sync_page_choices(self) -> None:
        """This page / All open pages need an open page."""
        try:
            has = self._current() is not None
        except Exception:  # noqa: BLE001 -- swallow-ok: no page then
            has = False
        self._rb_page.setEnabled(has)
        self._rb_all.setEnabled(has)
        if not has and not self._rb_project.isChecked():
            self._rb_project.setChecked(True)

    def page_changed(self) -> None:
        """The window shows another page (or none): the choices follow."""
        self._sync_page_choices()

    def _sync_project_widgets(self) -> None:
        on = self._rb_project.isChecked()
        self._cb_project.setEnabled(on)
        self._cb_includes.setEnabled(on)
        self._procs.setEnabled(on)
        self._procs_label.setEnabled(on)

    def _open_paths(self) -> list:
        try:
            docs = list(self._documents() or [])
        except Exception:  # noqa: BLE001 -- swallow-ok: no open pages known
            docs = []
        return [d.path for d in docs if getattr(d, "path", "")]

    def _closed_search_key(self):
        if not self._rb_project.isChecked():
            return None
        return (
            self.options(),
            tuple(self.project_folders()),
            tuple(sorted(os.path.normcase(p) for p in self._open_paths())),
        )

    def _start_closed(self) -> None:
        """Search the project's closed pages in the background (find_worker)."""
        from ...workers import find_worker as FW
        from ...infra.paths import data_dir
        from ..jobs.helper_job import HelperJob

        key = self._closed_search_key()
        if key is None or key == self._closed_key:
            return
        if self._job is not None:
            self._job.stop()
            self._job = None
        self._closed_key = key
        self._closed = []
        self._set_closed_note("")
        req = {
            "format": FW.FORMAT,
            "mode": "find",
            "projects": list(key[1]),
            "skip": self._open_paths(),
            "options": FW.options_to_json(key[0]),
        }
        # (Settings > Pages and libraries: several helper processes, each on every n-th
        # page, when there are enough pages for each to be worth its start)
        from ..prefs.settings import find_processes

        try:
            total = len(FW.pages_of(key[1]))
        except Exception:  # noqa: BLE001 -- swallow-ok: one helper, as before
            total = 0
        parts = max(1, min(find_processes(), total // max(1, self.PAGES_PER_HELPER)))
        if parts > 1:
            from ..jobs.helper_job import HelperJobs

            job = HelperJobs(
                self,
                FW.command,
                req,
                data_dir("find"),
                parts,
                total,
                FW.merge,
                skip_paths=self._open_paths,
                label="Find in closed pages",
            )
        else:
            job = HelperJob(
                self,
                FW.command,
                req,
                data_dir("find"),
                skip_paths=self._open_paths,
                label="Find in closed pages",
            )
        job.progress.connect(lambda i, n, name: self._closed_progress(job, i, n, name))
        job.finished.connect(lambda res: self._closed_done(job, res))
        if not job.start():
            self._set_closed_note("The closed pages could not be searched.", log=True)
            return
        self._job = job
        # (the figures of the search: the time so far, the estimate and the rate over every
        # helper, ui/jobs/run_stats)
        self._job_stats = RunStats.from_settings(workers=parts)
        self._job_stats.begin_phase("search", total)
        self._b_stop.setVisible(True)

    def searching(self) -> bool:
        """The closed pages are being searched now."""
        return self._job is not None

    def _stop_closed(self) -> None:
        if self._job is not None:
            self._job.stop()
            self._status.setText("Stopping…")

    def _closed_progress(self, job, i: int, n: int, name: str) -> None:
        if job is self._job and not job.stopping:
            self._status.setText(
                self._job_line("Searching the closed pages: %d of %d (%s)…" % (i + 1, n, name), i, n)
            )

    def _job_line(self, text: str, i: int, n: int) -> str:
        """``text`` with the job's figures after it ('1 min 5 s so far, about 2 min left,
        40 pages a minute'; none when Settings turns the figures off)."""
        stats = getattr(self, "_job_stats", None)
        if stats is None:
            return text
        stats.update(int(i), int(n))
        figures = stats.time_text()
        return "%s %s." % (text, figures[:1].upper() + figures[1:]) if figures else text

    def _closed_done(self, job, res: dict) -> None:
        if job is not self._job:
            return
        self._job = None
        self._b_stop.setVisible(False)
        if res.get("error"):
            self._set_closed_note("The closed pages could not be searched: %s" % res["error"], log=True)
            self._closed = []
        else:
            self._closed = [ClosedHit(r, k) for k, r in enumerate(res.get("rows") or ())]
            notes = []
            if res.get("stopped"):
                notes.append("Stopped before every closed page was searched.")
            if res.get("more"):
                notes.append("Only the first %d matches of the closed pages are listed." % len(self._closed))
            if res.get("failed"):
                notes.append(
                    "%d page%s could not be read."
                    % (len(res["failed"]), "" if len(res["failed"]) == 1 else "s")
                )
            self._set_closed_note(" ".join(notes), log=bool(res.get("failed")))
        open_hits = [h for h in self._hits if not isinstance(h, ClosedHit)]
        self._hits = open_hits + self._closed
        self._fill()
        then, self._after_search = self._after_search, None
        if then is not None:
            then()

    def _live_hit(self, h):
        """The open page's hit for the closed-page row ``h``: its page is opened in the
        editor and searched (None: it could not be opened, or the field no longer
        holds the text)."""
        if not isinstance(h, ClosedHit):
            return h
        name = os.path.splitext(os.path.basename(h.path))[0]
        # (why there is none, for the status line: the two cases have different remedies)
        self._live_why = "%s could not be opened. The window's status bar says why." % name
        if self._open_page is None:
            return None
        doc = self._open_page(h.path)
        if doc is None:
            return None
        self._live_why = (
            "%s is open now, but that field no longer holds the text. Its matches are listed again." % name
        )
        try:
            live = FR.find_hits(doc, self.options())
        except Exception:  # noqa: BLE001 -- swallow-ok: said in the status line
            live = []
        hit = next((x for x in live if h.same_field(x)), None)
        # the page is open now: its rows are the page's own
        self._hits = [x for x in self._hits if not (isinstance(x, ClosedHit) and x.path == h.path)]
        self._hits.extend(live)
        self._closed = [x for x in self._closed if x.path != h.path]
        self._closed_key = self._closed_search_key()
        self._key = self._search_key(self._targets())
        self._fill()
        if hit is not None:
            n = next(k for k, x in enumerate(self._hits) if x is hit)
            item = self._list.topLevelItem(n)
            self._list.blockSignals(True)
            self._list.setCurrentItem(item)
            self._list.blockSignals(False)
            self._pos = n
        return hit

    def _rank(self, doc, docs=None) -> int:
        docs = self._targets() if docs is None else docs
        return next((k for k, d in enumerate(docs) if d is doc), len(docs))

    def _search_key(self, docs):
        o = self.options()
        return (
            o,
            tuple(
                (
                    id(d),
                    getattr(d, "derive_serial", 0),
                    len(getattr(d, "_undo", ())),
                    len(getattr(d, "_redo", ())),
                )
                for d in docs
            ),
        )

    @staticmethod
    def _repl_placeholder(on: bool) -> str:
        return "Replacement text (\\1 or \\g<name>: a group of the match)" if on else "Replacement text"

    def _regex_toggled(self, on: bool) -> None:
        self._ed_repl.setPlaceholderText(self._repl_placeholder(on))
        self._options_changed()

    def _set_closed_note(self, text: str, log: bool = False) -> None:
        """The note after the status line about the closed pages; ``log``: it reports a
        failure, so the Open Log button shows beside it."""
        self._closed_note = text
        self._b_log.setVisible(bool(log))

    def _open_backup_folder(self) -> None:
        from ..messages import open_folder

        if self._backup_folder:
            open_folder(self._backup_folder)

    def _options_changed(self, *_a) -> None:
        self._key = None  # the list is out of date
        self._clear_stale()
        self._sync_project_widgets()
        self._save_options()
        self._update_buttons()
        bad = self._filter_problem()
        if bad:
            self._status.setText("%s. Correct it to search." % bad)

    def _filter_problem(self) -> str:
        """'<row> is not a regular expression (why)' for the first Filters box that does not
        compile ('' = none)."""
        return self._filter_list.problem()

    def _clear_stale(self) -> None:
        """A changed query or scope: the matches of the last search go (their count, Go To
        and Properties no longer stand for what the box says) until Find All, Find Next or
        Replace searches again. A search of the closed pages still running stops."""
        lst = getattr(self, "_list", None)
        if lst is None:
            return
        busy = self._job is not None
        if not (self._hits or lst.topLevelItemCount() or busy):
            return
        if busy:
            job, self._job = self._job, None
            job.stop()
            self._closed_key = None  # (its rows are not complete)
            self._closed = []
            self._b_stop.setVisible(False)
            self._after_search = None
        self._hits = []
        self._pos = -1
        lst.clear()
        self._set_closed_note("")
        self._status.setText("Search again to update the results.")

    _SETTINGS = "find/"
    #: the closed pages for each helper process at least (a helper's start costs about as
    #: much as searching this many pages): Settings > Pages and libraries > Long jobs
    #: (ui/prefs/job_options)
    PAGES_PER_HELPER = 8

    def _save_options(self, *_a) -> None:
        try:
            st = app_settings()
            st.setValue(
                self._SETTINGS + "scopes", ",".join(s for s, cb in self._cb_scope.items() if cb.isChecked())
            )
            st.setValue(self._SETTINGS + "match", self.options().match)
            st.setValue(self._SETTINGS + "genies", self._cb_genies.isChecked())
            st.setValue(self._SETTINGS + "template", self._cb_template.isChecked())
            st.setValue(self._SETTINGS + "all_pages", self._rb_all.isChecked())
            st.setValue(self._SETTINGS + "project_pages", self._rb_project.isChecked())
            st.setValue(self._SETTINGS + "includes", self._cb_includes.isChecked())
            st.setValue(self._SETTINGS + "backward", self._rb_back.isChecked())
            st.setValue(self._SETTINGS + "filters", json.dumps(self._filter_list.state()))
        except Exception:  # noqa: BLE001 -- a convenience only
            pass

    def _restore_options(self) -> None:
        try:
            st = app_settings()
            scopes = st.value(self._SETTINGS + "scopes", None)
            if scopes is not None:
                on = set(str(scopes).split(",")) if scopes else set()
                for s, cb in self._cb_scope.items():
                    cb.setChecked(s in on)
            m = st.value(self._SETTINGS + "match", FR.MATCH_PART)
            if m in self._rb_match:
                self._rb_match[m].setChecked(True)
            self._cb_genies.setChecked(st.value(self._SETTINGS + "genies", True, type=bool))
            self._cb_template.setChecked(st.value(self._SETTINGS + "template", False, type=bool))
            (
                self._rb_project
                if st.value(self._SETTINGS + "project_pages", False, type=bool)
                else self._rb_all
                if st.value(self._SETTINGS + "all_pages", False, type=bool)
                else self._rb_page
            ).setChecked(True)
            self._cb_includes.setChecked(st.value(self._SETTINGS + "includes", False, type=bool))
            (
                self._rb_back if st.value(self._SETTINGS + "backward", False, type=bool) else self._rb_fwd
            ).setChecked(True)
            try:
                rows = json.loads(str(st.value(self._SETTINGS + "filters", "") or "[]"))
            except ValueError:  # swallow-ok: a garbled setting starts with no filters
                rows = []
            self._filter_list.set_state(rows if isinstance(rows, list) else [])
            # what was searched for last (selected: typing replaces it)
            self._ed_find.setText(str(st.value(self._SETTINGS + "text", "") or ""))
            self._ed_repl.setText(str(st.value(self._SETTINGS + "replace", "") or ""))
            self._ed_find.selectAll()
        except Exception:  # noqa: BLE001
            pass

    def _save_texts(self) -> None:
        try:
            st = app_settings()
            st.setValue(self._SETTINGS + "text", self._ed_find.text())
            st.setValue(self._SETTINGS + "replace", self._ed_repl.text())
        except Exception:  # noqa: BLE001 -- a convenience only
            pass

    def _update_buttons(self) -> None:
        has_text = bool(self._ed_find.text())
        has_scope = any(cb.isChecked() for cb in self._cb_scope.values())
        ok = has_text and has_scope and not self._filter_problem()
        for b in (self._b_all, self._b_next, self._b_rep, self._b_repall):
            b.setEnabled(ok)
        hit = self.current_hit()
        self._b_goto.setEnabled(hit is not None)
        self._b_props.setEnabled(hit is not None)

    # -- searching ----------------------------------------------------------------
    def _run(self) -> bool:
        """Search again when the list is out of date. True when it did."""
        bad = self._filter_problem()
        if bad:
            self._status.setText("%s. Correct it to search." % bad)
            return False
        docs = self._targets()
        key = self._search_key(docs)
        if key == self._key:
            return False
        opts = key[0]
        hits = []
        with _busy():
            for d in docs:
                try:
                    hits.extend(FR.find_hits(d, opts))
                except Exception as exc:  # noqa: BLE001 -- one page must not stop the others
                    self._status.setText("%s could not be searched: %s" % (page_name(d), exc))
        self._hits = hits
        self._key = key
        if self._rb_project.isChecked():
            if self._closed_search_key() == self._closed_key and self._job is None:
                self._hits = hits + self._closed  # (the closed pages' rows still hold)
            else:
                self._start_closed()
        self._fill()
        return True

    def _fill(self) -> None:
        self._list.blockSignals(True)
        try:
            self._list.clear()
            grey = self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text)
            labels = {}
            # (the rows go in at once: one by one, each would make the column fit read its
            # setting and queue a fit)
            items = []
            for n, h in enumerate(self._hits):
                if isinstance(h, ClosedHit):
                    obj = h.object
                elif h.po is None:
                    obj = "Page"
                else:
                    k = (id(h.doc), h.index)
                    if k not in labels:
                        labels[k] = object_label(h.doc, h.index)
                    obj = labels[k]
                fld = h.label
                if h.params and h.kind != FR.K_PARAM:
                    fld += " (genie parameter %s)" % ", ".join(h.params)
                text = " ".join(str(h.value).split())
                if len(text) > 160:
                    text = text[:157] + "…"
                pname = ("%s (closed)" % h.page) if isinstance(h, ClosedHit) else page_name(h.doc)
                item = QtWidgets.QTreeWidgetItem([pname, obj, fld, text, ""])
                if isinstance(h, ClosedHit):
                    item.setToolTip(0, h.path)
                item.setData(0, QtCore.Qt.UserRole, n)
                tip = str(h.value)
                if h.reason:
                    for c in range(4):
                        item.setForeground(c, grey)
                    tip = "Cannot be replaced: " + h.reason
                elif h.params and h.kind != FR.K_PARAM:
                    tip = "Replacing it changes genie parameter %s." % ", ".join(h.params)
                for c in range(1, 4) if isinstance(h, ClosedHit) else range(4):
                    item.setToolTip(c, tip)
                items.append(item)
            self._list.addTopLevelItems(items)
            self._update_becomes()
            from ..tables import refit

            refit(self._list)
        finally:
            self._list.blockSignals(False)
        self._pos = -1
        n = len(self._hits)
        ro = sum(1 for h in self._hits if h.reason)
        if self._job is not None:
            msg = "%d match%s on the open pages; searching the closed pages…" % (n, "" if n == 1 else "es")
            self._status.setText(msg)
        elif not self._ed_find.text():
            self._status.setText("")
        elif not n:
            self._status.setText(
                ("'%s' was not found. %s" % (self._ed_find.text(), self._closed_note)).strip()
            )
        else:
            pages = len({h.path if isinstance(h, ClosedHit) else id(h.doc) for h in self._hits})
            msg = "%d match%s" % (n, "" if n == 1 else "es")
            if pages > 1:
                msg += " on %d pages" % pages
            closed = len({h.path for h in self._hits if isinstance(h, ClosedHit)})
            if closed:
                msg += " (%d closed)" % closed
            if ro:
                msg += "; %d cannot be replaced (grey rows)" % ro
            self._status.setText((msg + ". " + self._closed_note).strip())
        self._update_buttons()

    def becomes(self, h, opts=None) -> str:
        """What hit ``h``'s field turns into with the Replace with text ('' for a row that
        cannot be replaced, or when nothing is typed). ``opts`` = :meth:`options` when the
        caller has them (a row each)."""
        if h.reason or not self._replace_mode:
            return ""
        repl = self._ed_repl.text()
        if not repl and not self._ed_find.text():
            return ""
        try:
            out = FR.replaced(str(h.value), opts if opts is not None else self.options(), repl)
        except Exception:  # noqa: BLE001  # swallow-ok: a preview only; Replace reports its own error
            return ""
        return out if out is not None else ""

    def _update_becomes(self, *_a) -> None:
        """The Becomes column follows the Replace with text (no new search)."""
        if self._list.isColumnHidden(BECOMES):
            return
        opts = None
        for k in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(k)
            n = item.data(0, QtCore.Qt.UserRole)
            if n is None or not 0 <= int(n) < len(self._hits):
                continue
            if opts is None:
                opts = self.options()  # (read once: the widgets do not change here)
            full = self.becomes(self._hits[int(n)], opts)
            text = " ".join(full.split())
            if len(text) > 160:
                text = text[:157] + "…"
            item.setText(BECOMES, text)
            item.setToolTip(BECOMES, full)

    def find_all(self) -> list:
        """List every match (the Find All button); returns the hits."""
        self._key = None
        self._run()
        return list(self._hits)

    def hits(self) -> list:
        """The listed hits (tests / scripting)."""
        return list(self._hits)

    def current_hit(self):
        item = self._list.currentItem()
        if item is None:
            return None
        n = item.data(0, QtCore.Qt.UserRole)
        return self._hits[n] if isinstance(n, int) and 0 <= n < len(self._hits) else None

    def _order(self, h, docs=None) -> tuple:
        if isinstance(h, ClosedHit):
            docs = self._targets() if docs is None else docs
            return len(docs) + h.rank, h.index
        idx = FR._resolve(h.doc, h) if h.po is not None else -1
        return self._rank(h.doc, docs), -1 if idx is None else idx

    def _ref_order(self, docs) -> tuple:
        """Where the object of the last hit shown sits (page rank, index; its
        stored index when the record is gone)."""
        doc, po, idx = self._last
        if po is None:
            return self._rank(doc, docs), -1
        i = FR._resolve(doc, FR.Hit(doc, po, idx, "", "", "", "", ""))
        return self._rank(doc, docs), idx if i is None else i

    def find_next(self, backward: bool | None = None) -> None:
        """Go to the next match (Find Next): from the current row, or -- when
        the page changed since the list was made -- from the last object
        shown, in the chosen direction; wraps at the end."""
        if not self._ed_find.text():
            self._status.setText("Type the text to find.")
            return
        if backward is None:
            backward = self._rb_back.isChecked()
        fresh = self._run()
        if not self._hits:
            self._cursor = -1
            if self._job is not None:
                # (the closed pages' rows come later: go to the first then)
                self._after_search = lambda b=backward: self.find_next(b)
            return
        n = len(self._hits)
        if fresh or self._pos < 0:
            pos = self._start_pos(backward)
        else:
            pos = self._pos + (-1 if backward else 1)
        wrapped = pos < 0 or pos >= n
        self._go(pos % n)
        if wrapped and n > 1:
            self._status.setText(
                "Search wrapped to the %s. %s" % ("end" if backward else "start", self._row_status())
            )

    def _start_pos(self, backward: bool) -> int:
        """Where Find Next starts in a new list: after (before) the object
        of the last hit shown."""
        if self._last is None:
            return len(self._hits) - 1 if backward else 0
        docs = self._targets()
        ref_order = self._ref_order(docs)
        orders = [self._order(h, docs) for h in self._hits]
        if backward:
            cands = [k for k, o in enumerate(orders) if o < ref_order]
            return cands[-1] if cands else -1
        cands = [k for k, o in enumerate(orders) if o > ref_order]
        return cands[0] if cands else len(self._hits)

    def _start_at_or_after(self, skip=None, genie=None) -> int:
        """The first row at or after the last hit shown."""
        if self._last is None:
            return 0
        docs = self._targets()
        ref_order = self._ref_order(docs)
        for k, h in enumerate(self._hits):
            o = self._order(h, docs)
            if o < ref_order:
                continue
            if o == ref_order and skip is not None and (h.kind, h.key.upper()) == skip:
                continue
            if genie is not None and h.doc is self._last[0] and h.via_genie:
                if h.kind == FR.K_PARAM:
                    names = {h.key.lower()}
                else:
                    names = {n.lower() for n in h.params}
                g = FR._resolve(h.doc, FR.Hit(h.doc, h.genie_po, h.genie, "", "", "", "", ""))
                if g == genie[0] and names <= genie[1]:
                    continue
            return k
        return len(self._hits)

    def _row_status(self) -> str:
        h = self.current_hit()
        if h is None:
            return ""
        s = "Match %d of %d." % (self._pos + 1, len(self._hits))
        if h.reason:
            s += " This one cannot be replaced: " + h.reason
        return s

    def _go(self, pos: int) -> None:
        item = self._list.topLevelItem(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)  # -> _on_row
        self._list.scrollToItem(item)
        self.go_to()
        self._status.setText(self._row_status())

    def _on_row(self, item, _prev) -> None:
        if item is not None:
            n = item.data(0, QtCore.Qt.UserRole)
            if isinstance(n, int):
                self._pos = self._list.indexOfTopLevelItem(item)
        self._update_buttons()

    def go_to(self) -> None:
        """Show the selected row's object on its page (Go To)."""
        h = self.current_hit()
        if h is None:
            return
        if isinstance(h, ClosedHit):
            h = self._live_hit(h)
            if h is None:
                self._status.setText(getattr(self, "_live_why", ""))
                return
        if h.po is None:
            self._last = (h.doc, None, -1)
            self._cursor = -1
            self.found.emit(h.doc, -1)
            return
        i = FR._resolve(h.doc, h)
        if i is None:
            self._status.setText("That object is no longer on the page. Click Find All to search again.")
            return
        self._last = (h.doc, h.po, i)
        self._cursor = i
        self.found.emit(h.doc, i)

    def open_properties(self) -> None:
        h = self.current_hit()
        if h is None:
            return
        if isinstance(h, ClosedHit):
            h = self._live_hit(h)
            if h is None:
                self._status.setText(getattr(self, "_live_why", ""))
                return
        i = FR._resolve(h.doc, h) if h.po is not None else -1
        if h.po is not None and i is None:
            self._status.setText("That object is no longer on the page. Click Find All to search again.")
            return
        self.propertiesRequested.emit(h.doc, -1 if i is None else i, h)

    # -- replacing ----------------------------------------------------------------
    def _report(self, rep: FR.ReplaceReport, what: str) -> str:
        parts = []
        if rep.replaced:
            pages = len(rep.docs)
            parts.append(
                "%s: %d field%s changed%s."
                % (
                    what,
                    rep.replaced,
                    "" if rep.replaced == 1 else "s",
                    " on %d pages (undo each page separately)" % pages if pages > 1 else "",
                )
            )
            if rep.parameters:
                # (a genie member's match changes its genie's parameter: every
                # object of the genie that uses it follows)
                n = rep.parameters
                if n == rep.replaced:
                    parts.append("It is a genie parameter." if n == 1 else "All are genie parameters.")
                else:
                    parts.append("%d of them %s genie parameters." % (n, "is" if n == 1 else "are"))
        else:
            parts.append("%s: nothing was changed." % what)
        if rep.read_only:
            parts.append(
                "%d field%s cannot be replaced (grey rows)."
                % (rep.read_only, "" if rep.read_only == 1 else "s")
            )
        if rep.gone:
            parts.append("%d field%s no longer held the text." % (rep.gone, "" if rep.gone == 1 else "s"))
        parts.extend(rep.problems[:3])
        if len(rep.problems) > 3:
            parts.append("… and %d more problem(s)." % (len(rep.problems) - 3))
        parts.extend(rep.notes[:3])
        return " ".join(parts)

    def replace_one(self) -> None:
        """Replace the text in the selected row's field (Replace), then go to
        the next match. With no row selected it finds the next one first."""
        if not self._ed_find.text():
            self._status.setText("Type the text to find.")
            return
        self._run()
        h = self.current_hit()
        if h is None:
            self.find_next()
            return
        if isinstance(h, ClosedHit):
            if h.reason:
                self._status.setText("This field cannot be replaced: " + h.reason)
                return
            h = self._live_hit(h)
            if h is None:
                self._status.setText(getattr(self, "_live_why", ""))
                return
        if h.reason:
            self._status.setText("This field cannot be replaced: " + h.reason)
            if len(self._hits) > 1:
                self._go((self._pos + 1) % len(self._hits))
            return
        with _busy():
            rep = FR.replace_hits(h.doc, [h], self.options(), self._ed_repl.text(), label="replace")
        if rep.docs:
            self.replaced.emit(list(rep.docs))
        msg = self._report(rep, "Replace")
        i = FR._resolve(h.doc, h)
        self._last = (h.doc, h.po, h.index if i is None else i)
        genie = None
        if h.via_genie and h.genie is not None:
            # (a re-composition makes new records at the same places)
            g = FR._resolve(h.doc, FR.Hit(h.doc, h.genie_po, h.genie, "", "", "", "", ""))
            names = [h.key] if h.kind == FR.K_PARAM else list(h.params)
            genie = (h.genie if g is None else g, {n.lower() for n in names})
        self._key = None
        self._run()
        if self._hits:
            pos = self._start_at_or_after(skip=(h.kind, h.key.upper()), genie=genie)
            self._go(pos % len(self._hits))
        self._status.setText(msg + (" " + self._row_status() if self._hits else ""))

    def replace_all(self) -> None:
        """Replace the text in every field it is found in (Replace All): one
        undo step per page."""
        if not self._ed_find.text():
            self._status.setText("Type the text to find.")
            return
        docs = self._targets()
        opts = self.options()
        if self._rb_project.isChecked() and not self._replace_closed_first(opts):
            return
        with _busy():
            rep = FR.replace_all(docs, opts, self._ed_repl.text(), label="replace all")
        if rep.docs:
            self.replaced.emit(list(rep.docs))
        self._remember_replace(rep.docs, "", [])
        self._key = None
        self._closed_key = None
        self._run()
        self._status.setText(self._report(rep, "Replace All"))
        self._last_report = rep
        if rep.docs:
            # (told over the page too, with Undo of the page shown)
            from ..notify import editor_window, notify, undo_action

            win = editor_window(self)
            if win is not None:
                acts = [undo_action(win)] if getattr(win, "doc", None) in list(rep.docs) else []
                notify(
                    win,
                    self._report(rep, "Replace All"),
                    "success",
                    actions=acts,
                    toast=True,
                    key="edit:replace",
                )

    def _replace_closed_first(self, opts) -> bool:
        """Replace All with 'All pages of the project': the closed pages first, in the
        background (asked first; each changed page backed up and saved); the open pages
        follow when it is done. False = not now (cancelled, or it runs)."""
        if self._job is not None:
            self._status.setText("Wait until the closed pages have been searched.")
            return False
        folders = self.project_folders()
        closed = sorted({h.path for h in self._closed})
        ans = QtWidgets.QMessageBox.question(
            self,
            "Replace All",
            "Replace in every page of %s%s? The closed pages%s are changed and saved; each "
            "is backed up first. The open pages change in their tabs. Undo Replace All, here "
            "afterwards, puts both back."
            % (
                self._cb_project.currentText(),
                " and the projects it includes" if self._cb_includes.isChecked() else "",
                " (%d with matches)" % len(closed) if closed else "",
            ),
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Ok,
        )
        if ans != QtWidgets.QMessageBox.Ok:
            return False
        from ...workers import find_worker as FW
        from ...infra import backups
        from ...infra.paths import data_dir
        from ..jobs.helper_job import HelperJob
        from ..jobs.helper_job import builder_open_pages

        in_builder = builder_open_pages()
        self._backup_folder = backups.new_folder("replace")
        self._b_backup.setVisible(False)
        self._b_log.setVisible(False)
        req = {
            "format": FW.FORMAT,
            "mode": "replace",
            "projects": folders,
            "skip": self._open_paths(),
            "options": FW.options_to_json(opts),
            "replacement": self._ed_repl.text(),
            "builder_open": in_builder,
            "backup": self._backup_folder,
        }
        job = HelperJob(
            self,
            FW.command,
            req,
            data_dir("find"),
            skip_paths=self._open_paths,
            label="Replace All in closed pages",
        )
        job.progress.connect(
            lambda i, n, name: self._status.setText(
                self._job_line("Replacing in the closed pages: %d of %d (%s)…" % (i + 1, n, name), i, n)
            )
        )
        job.finished.connect(lambda res: self._replaced_closed(job, res, opts))
        if not job.start():
            self._status.setText(
                "The closed pages could not be changed: the helper process did not start. Open Log says why."
            )
            self._b_log.setVisible(True)
            return False
        self._job = job
        self._job_stats = RunStats.from_settings()
        self._job_stats.begin_phase("replace", len(closed))
        self._b_stop.setVisible(True)
        return False

    def _replaced_closed(self, job, res: dict, opts) -> None:
        if job is not self._job:
            return
        self._job = None
        self._b_stop.setVisible(False)
        if res.get("error"):
            self._status.setText("Replace All in the closed pages failed: %s" % res["error"])
            self._b_log.setVisible(True)
            return
        with _busy():
            rep = FR.replace_all(self._targets(), opts, self._ed_repl.text(), label="replace all")
        if rep.docs:
            self.replaced.emit(list(rep.docs))
        upd = list(res.get("updated") or [])
        self._remember_replace(rep.docs, self._backup_folder if upd else "", upd)
        closed = FR.ReplaceReport(
            replaced=int(res.get("replaced") or 0),
            parameters=int(res.get("parameters") or 0),
            read_only=int(res.get("read_only") or 0),
            problems=list(res.get("problems") or ()),
        )
        rep.add(closed)
        notes = []
        if upd:
            # (the backup is named and opened from here; Tools > Restore Backup
            # puts the pages back)
            notes.append(
                "Saved %d closed page%s, backed up first in %s (Undo Replace All puts them "
                "back; later, Tools > Restore Backup)."
                % (len(upd), "" if len(upd) == 1 else "s", self._backup_folder)
            )
            self._b_backup.setVisible(bool(self._backup_folder) and os.path.isdir(self._backup_folder))
        if res.get("failed"):
            notes.append("Could not change: %s." % "; ".join(res["failed"][:3]))
            self._b_log.setVisible(True)
        if res.get("open_in_builder"):
            notes.append("Left (open in the Graphics Builder): %s." % ", ".join(res["open_in_builder"]))
        if res.get("stopped"):
            notes.append("Stopped before every closed page was done.")
        self._last_report = rep
        self._key = None
        self._closed_key = None
        self._run()
        self._status.setText(" ".join([self._report(rep, "Replace All")] + notes))

    # -- one way back from a Replace All ------------------------------------------------
    def _remember_replace(self, docs, backup: str, saved: list) -> None:
        """What Undo Replace All reverses: each open page's replace step (the command itself,
        so a later edit there is never undone instead) and the closed pages (``saved``: page
        names) saved after their backup in ``backup``."""
        steps = [(d, d._undo[-1]) for d in docs or () if getattr(d, "_undo", None)]
        self._undo_set = {"steps": steps, "backup": backup, "saved": list(saved)} if steps or saved else None
        self._b_undo_all.setVisible(self._undo_set is not None)

    def undo_replace_all(self) -> None:
        """Undo Replace All: the open pages' replace step and the closed pages from the
        backup. A tab with a later change keeps it (its replace step is no longer the last:
        it is left, and said)."""
        u = self._undo_set
        if not u:
            return
        undone, kept = [], []
        for doc, cmd in u["steps"]:
            if doc._undo and doc._undo[-1] is cmd:
                doc.undo()
                undone.append(doc)
            else:
                kept.append(os.path.splitext(os.path.basename(getattr(doc, "path", "") or "page"))[0])
        restored, failed = [], []
        if u["backup"] and u["saved"]:
            from ...io import updatepages
            from ..jobs.helper_job import builder_open_pages

            # (the saved pages by name, as the helper lists them; a page opened since is left:
            # its tab could hold other changes)
            open_now = {os.path.splitext(os.path.basename(p))[0].lower() for p in self._open_paths()}
            names = [n for n in u["saved"] if n.lower() not in open_now]
            kept += [n for n in u["saved"] if n.lower() in open_now]
            with _busy():
                rr = updatepages.restore_backup(
                    u["backup"], names, builder_open=builder_open_pages(), libraries=False
                )
            restored, failed = rr.restored, rr.failed
        if undone:
            self.replaced.emit(undone)
        self._undo_set = None
        self._b_undo_all.setVisible(False)
        self._key = None
        self._closed_key = None
        self._run()
        parts = [
            "Replace All undone in %d page%s."
            % (len(undone) + len(restored), "" if len(undone) + len(restored) == 1 else "s")
        ]
        if kept:
            parts.append("Not undone (changed since, or open now): %s." % ", ".join(kept[:5]))
        if failed:
            parts.append("Could not put back: %s." % "; ".join(failed[:3]))
            self._b_log.setVisible(True)
        self._status.setText(" ".join(parts))

    # -- the Options section -------------------------------------------------------------
    def _show_options(self, on: bool) -> None:
        self._opt_box.setVisible(bool(on))
        self._opt_toggle.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
        app_settings().setValue("find/options_shown", bool(on))
        self._sync_options_text()
        if on and self.isVisible():
            self._fit_height()

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        if self._opt_toggle.isChecked():
            self._fit_height()

    def _fit_height(self) -> None:
        """Options shown: the window grows (within its screen) so the results keep their
        rows, rather than the options squeezing them."""
        self.layout().activate()
        want = self.layout().minimumSize().height()
        screen = self.screen()
        if screen is not None:
            want = min(want, screen.availableGeometry().height() - _sp(10))
        if self.height() < want:
            self.resize(self.width(), want)

    def _sync_options_text(self) -> None:
        """'Options' and, closed, what differs from the defaults (so a match missing because
        of an option shows without opening them)."""
        if not hasattr(self, "_rb_match"):
            return
        off = [cb.text() for cb in self._cb_scope.values() if not cb.isChecked()]
        notes = []
        if off:
            notes.append("not in: %s" % ", ".join(t.replace("&", "") for t in off))
        if not self._cb_genies.isChecked():
            notes.append("not inside genies")
        if self._cb_template.isChecked():
            notes.append("inside the template")
        mode = next((m for m, rb in self._rb_match.items() if rb.isChecked()), FR.MATCH_PART)
        if mode != FR.MATCH_PART:
            notes.append(dict(_MATCH_LABELS).get(mode, "").replace("&", "").lower())
        if self._rb_back.isChecked():
            notes.append("backward")
        notes.extend(self._filter_list.summary())
        text = "Options"
        if notes and not self._opt_toggle.isChecked():
            text += ": " + "; ".join(notes)
        self._opt_toggle.setText(text)
        self._opt_toggle.setToolTip(
            "Include, Search in, Match, Direction and Filters. Shown here when they are not the defaults"
        )

    def done(self, r) -> None:
        self._save_texts()  # (the next Find / Replace starts from them)
        # (a closed-pages search stops with the window)
        if self._job is not None:
            self._job.stop()
            self._job = None
        super().done(r)
