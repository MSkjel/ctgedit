"""The Tags panel: tags in the page's scope."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import json
import os
import threading
from collections import OrderedDict
from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

TAG_MIME = "application/x-ctgedit-tag"
TAG_INFO_MIME = "application/x-ctgedit-tag-info"
#: the panel's columns
DOCK_COLUMNS = ("name", "type", "equip", "project", "comment")
COLUMN_TITLES = {
    "name": "Tag",
    "kind": "Kind",
    "type": "Type",
    "equip": "Equipment",
    "project": "Project",
    "comment": "Comment",
}
KIND_LABELS = {"variable": "Variable tag", "local": "Local variable", "equipment": "Equipment item"}
_COL_INDEX = {"name": 0, "kind": 1, "type": 2, "equip": 3, "project": 4, "comment": 5}
#: (the scope is the page's: its project, the projects it includes and those including it)
ALL_PROJECTS = "All projects of the page"


def _dim() -> QtGui.QColor:
    try:
        from ..style.theme import COLORS

        return QtGui.QColor(COLORS.get("text_dim", "#969ca5"))
    except Exception:  # noqa: BLE001
        return QtGui.QColor("#969ca5")


def restore_root_choices() -> None:
    """Every compiled project chosen in a Tags panel's From: box (settings
    tags/scope/<project>): a page's scope, and its display formats, use it from
    the page's first read on."""
    try:
        from ...io import tagscope as TS
        from ..prefs.settings import app_settings

        st = app_settings()
        st.beginGroup("tags/scope")
        try:
            for project in st.childKeys():
                root = st.value(project, "", type=str)
                if root and not TS.chosen_root(project):
                    TS.choose_root(project, root)
        finally:
            st.endGroup()
    except Exception:  # noqa: BLE001 -- a remembered choice is a convenience
        pass


def _tag_key(path: str) -> tuple:
    """What a panel's list is for: the page folder and the compiled project chosen."""
    from ...io import tagscope as TS

    d = os.path.normcase(os.path.abspath(path)) if path else ""
    return (d, (TS.chosen_root(path) or "").lower() if path else "")


# ----------------------------------------------------------------- the model
class TagTableModel(QtCore.QAbstractTableModel):
    """Rows of a TagIndex (io.project): the codes of an :meth:`TagIndex.order`,
    filtered by text, project and kind, sorted by any column. Only the codes
    are held; the cells are read from the index when shown."""

    def __init__(self, columns: Sequence[str] = DOCK_COLUMNS, titles: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.columns = tuple(columns)
        self._titles = dict(COLUMN_TITLES, **(titles or {}))
        self.index_ = None
        self._base = ()
        self._rows = ()
        self._text = ""
        self._regex = False
        self._project = None
        self._kinds = None
        self._sort = (0, QtCore.Qt.AscendingOrder)
        #: the sort the rows shown have (a sort on a worker lands later)
        self._shown_sort = self._sort
        #: a view that sorts a large source on a worker sets it: called with the new sort
        #: set; the rows follow through :meth:`take_rows`
        self.sort_worker = None
        #: ... for a source of at least this many rows
        self.sort_worker_rows = 100_000

    # -- contents ---------------------------------------------------------------------
    def set_source(self, index, order) -> None:
        """Show ``order`` (codes of ``index``), keeping the filter and the sort."""
        self.beginResetModel()
        self.index_ = index
        self._base = order if order is not None else ()
        self._rows = self._compute()
        self.endResetModel()

    def clear(self) -> None:
        self.set_source(None, ())

    def set_filter(
        self,
        text: str = "",
        project: Optional[str] = None,
        kinds: Optional[Sequence[str]] = None,
        regex: bool = False,
    ) -> None:
        """Rows whose name, type, equipment, project or comment contains ``text``
        (case-insensitive; ``regex``: a regular expression found in them, the search box's
        '.*'), of ``project`` (None = all) and ``kinds`` (None = all)."""
        text = (text or "").strip()
        kinds = tuple(kinds) if kinds else None
        regex = bool(regex)
        if (text, project, kinds, regex) == (self._text, self._project, self._kinds, self._regex):
            return
        self.beginResetModel()
        self._text, self._project, self._kinds, self._regex = text, project, kinds, regex
        self._rows = self._compute()
        self.endResetModel()

    def _compute(self):
        rows, last = self._rows_for(self._text, self._project, self._kinds, self._regex)
        if last is not None:
            self._last_filter = last
        self._shown_sort = self._sort
        return rows

    @staticmethod
    def _params(text, project, kinds, regex) -> tuple:
        return ((text or "").strip(), project, tuple(kinds) if kinds else None, bool(regex))

    def params(self) -> tuple:
        """(text, project, kinds, regex) of the rows shown."""
        return (self._text, self._project, self._kinds, self._regex)

    def shows(self, params: tuple) -> bool:
        """The rows shown are those of ``params`` in the sort asked for."""
        return self._params(*params) == self.params() and self._shown_sort == self._sort

    def rows_for(
        self,
        text: str = "",
        project: Optional[str] = None,
        kinds=None,
        regex: bool = False,
        sort: Optional[tuple] = None,
    ):
        """``(base, rows, last)`` the filter and the sort (default: the one asked for) give
        over the source, the model left as it is: the Tags panel runs it on a worker thread
        for a large scope (the index is read only; its derived values are built under their
        lock) and hands the result to :meth:`take_rows` on the GUI thread."""
        base = self._base
        rows, last = self._rows_for(*self._params(text, project, kinds, regex), sort=sort)
        return base, rows, last

    def take_rows(self, params: tuple, result) -> bool:
        """Show what :meth:`rows_for` computed for ``params``; False when the source or
        the sort changed meanwhile (nothing changes then)."""
        base, rows, last, sort = result
        if base is not self._base or sort != self._sort:
            return False
        self.beginResetModel()
        self._text, self._project, self._kinds, self._regex = self._params(*params)
        self._rows = rows
        self._shown_sort = sort
        if last is not None:
            self._last_filter = last
        self.endResetModel()
        return True

    def _rows_for(self, text, project, kinds, regex, sort=None):
        idx = self.index_
        if idx is None:
            return (), None
        base = self._base
        rows = base
        last_out = None
        if text or project or kinds:
            cols = [c for c in ("name", "type", "equip", "project", "comment")]
            # typing on (the text grows): the matches are among the last matches -- filter
            # those, not every tag
            last = getattr(self, "_last_filter", None)
            low = text.lower()
            src = rows
            # (plain text only: a longer regular expression need not match fewer rows)
            if (
                not regex
                and last is not None
                and last[0] is base
                and last[2:5] == (project, kinds, False)
                and last[1]
                and last[1] in low
            ):
                src = last[5]
            rows = idx.filter(src, text, project, cols, kinds=kinds, regex=regex)
            last_out = (base, low, project, kinds, regex, rows)
        col, order = self._sort if sort is None else sort
        name = self.columns[col] if 0 <= col < len(self.columns) else "name"
        if name == "name":
            if order == QtCore.Qt.DescendingOrder:
                rows = rows[::-1]
        else:
            rows = sorted(rows, key=idx.sort_key(name), reverse=order == QtCore.Qt.DescendingOrder)
        return rows, last_out

    def sort(self, column: int, order=QtCore.Qt.AscendingOrder) -> None:  # noqa: D102
        if (column, order) == self._sort:
            return
        if self.sort_worker is not None and len(self._base) >= self.sort_worker_rows:
            self._sort = (column, order)
            self.sort_worker()
            return
        self.layoutAboutToBeChanged.emit()
        self._sort = (column, order)
        self._rows = self._compute()
        self.layoutChanged.emit()

    def total(self) -> int:
        """How many rows the source has (before the filter)."""
        return len(self._base)

    #: the sources whose project counts are kept (a tab switch between pages of a few
    #: projects counts each once)
    COUNTS_KEPT = 4

    def project_counts(self) -> "OrderedDict[str, int]":
        """project -> rows of the source it defines, in scope order (counted once per
        source)."""
        if self.index_ is None:
            return OrderedDict()
        memo = self.__dict__.setdefault("_counts_memo", [])
        for k, (idx, base, counts) in enumerate(memo):
            if idx is self.index_ and base is self._base:
                memo.append(memo.pop(k))
                return counts
        counts = self.index_.counts(self._base)
        memo.append((self.index_, self._base, counts))
        del memo[: -self.COUNTS_KEPT]
        return counts

    def code(self, row: int):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def entry(self, row: int):
        c = self.code(row)
        return self.index_.entry(c) if c is not None else None

    def name(self, row: int) -> Optional[str]:
        c = self.code(row)
        return self.index_.name_of(c) if c is not None else None

    def row_of(self, name: str) -> int:
        """The row showing ``name`` (case-insensitive), -1 when not shown."""
        low = (name or "").strip().lower()
        for k in range(len(self._rows)):
            if self.index_.name_of(self._rows[k]).lower() == low:
                return k
        return -1

    # -- Qt -----------------------------------------------------------------------------
    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return len(self.columns)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        col = self.columns[index.column()]
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            cells = self.index_.cells(self._rows[index.row()])
            v = cells[_COL_INDEX[col]]
            if col == "kind":
                v = KIND_LABELS.get(v, v)
            if role == QtCore.Qt.ToolTipRole and col == "project":
                sc = getattr(self.index_, "scope", None)
                p = sc.by_name(v) if sc is not None else None
                if p is not None:
                    from ...io.tagscope import relation_label

                    return "%s (%s)" % (v, relation_label(p.relation))
            return v
        if role == QtCore.Qt.ForegroundRole and col == "project":
            return _dim()
        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):  # noqa: N802
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self._titles.get(self.columns[section], self.columns[section])
        return None

    def flags(self, index):
        f = super().flags(index)
        return f | QtCore.Qt.ItemIsDragEnabled if index.isValid() else f

    def mimeTypes(self):  # noqa: N802
        return [TAG_MIME, TAG_INFO_MIME, "text/plain"]

    def mimeData(self, indexes):  # noqa: N802
        md = QtCore.QMimeData()
        rows = sorted({i.row() for i in indexes})
        if rows:
            e = self.entry(rows[0])
            if e is not None:
                md.setData(TAG_MIME, e.name.encode("utf-8"))
                md.setData(
                    TAG_INFO_MIME,
                    json.dumps(
                        {
                            "name": e.name,
                            "kind": e.kind,
                            "equip": e.equip,
                            "item": e.item,
                            "project": e.project,
                        }
                    ).encode("utf-8"),
                )
                md.setText(e.name)
        return md


def tag_info(mime: QtCore.QMimeData) -> Optional[dict]:
    """The name / kind / equip / item / project a tag drag carries, or None."""
    if mime is None or not mime.hasFormat(TAG_INFO_MIME):
        return None
    try:
        return json.loads(bytes(mime.data(TAG_INFO_MIME)).decode("utf-8"))
    except ValueError:
        return None


# ----------------------------------------------------------------- loading
class _Loader(QtCore.QObject):
    """Reads a scope's tag index in a worker thread and reports to the GUI thread. Only the
    newest read goes on: an older one, or one the user cancelled (:meth:`stop`), ends at its
    next project (io/project.TagIndexStopped), so the threads never pile up."""

    progress = QtCore.Signal(int, int, int, str)  # generation, n, m, project
    partial = QtCore.Signal(int, object)  # generation, (index, order)
    done = QtCore.Signal(int, object)  # generation, (index, order) | exception

    #: the generation whose read may go on (None: none)
    current: Optional[int] = None

    def stop(self) -> None:
        """The read that runs ends at its next project."""
        self.current = None

    def start(self, gen: int, pdir: str, kinds=("variable",)) -> threading.Thread:
        self.current = gen

        def work():
            from ...io import project as PR
            from ...io import tagscope as TS

            try:
                sc = TS.scope(pdir)
                far = sc.far()
                if far:
                    near = PR.tag_index(pdir, near_only=True)
                    self._emit(self.partial, gen, (near, near.order(kinds)))
                n0 = len(sc.near())
                total = len(sc.projects)

                def report(n, _m, name):
                    if self.current != gen:
                        return False  # (stopped, or a newer read took over)
                    if n >= n0 or not far:
                        self._emit(self.progress, gen, n + 1, total, name)
                    return True

                idx = PR.tag_index(pdir, progress=report)
                self._emit(self.done, gen, (idx, idx.order(kinds)))
                idx.prepare_search()  # (the first key's text indexes, after the list shows)
            except Exception as exc:  # noqa: BLE001 -- shown in the panel
                self._emit(self.done, gen, exc)

        from ...infra import gcguard

        gcguard.before_worker()  # only the GUI thread may collect garbage
        t = threading.Thread(target=work, name="tags-panel", daemon=True)
        t.start()
        return t

    @staticmethod
    def _emit(sig, *args) -> None:
        try:
            sig.emit(*args)
        except RuntimeError:  # the panel is gone
            pass


# ----------------------------------------------------------------- the panel
#: Settings > Appearance > Tags panel: the first widths' numbers and their spans (declared
#: in ui/prefs/settings.SETTINGS_KEYS and WHOLE_SPANS). The defaults: Type at most 80 px, Tag
#: 42 % and Equipment 29 % of the rest (Project the remainder), Tag at least 90 px, Equipment
#: and Project at least 60 px.
COLUMN_SPANS = {
    "tags/type_max_width": (40, 400),
    "tags/name_share": (10, 80),
    "tags/equipment_share": (10, 80),
    "tags/name_min_width": (24, 400),
    "tags/min_column_width": (24, 400),
}


def _apply_column_settings(window) -> None:
    dock = getattr(window, "tags_dock", None)
    if dock is not None:
        dock.apply_column_settings()


def _register_hooks() -> None:
    from ..prefs import settings_pages as SP

    for key in COLUMN_SPANS:
        if _apply_column_settings not in SP.APPLY_HOOKS.get(key, []):
            SP.register_apply(key, _apply_column_settings)


def _end_token(token) -> None:
    """End the tags load task ``token`` (None: none) without a word (a panel destroyed)."""
    if token is not None:
        from ..jobs import tasks

        tasks.end(token, tell=False)


class TagsDock(QtWidgets.QWidget):
    """The Tags panel; double-click copies the tag name."""

    #: a scope with more tags than this is searched on a worker thread
    ASYNC_ROWS = 100_000
    #: (params, result) of a search the worker finished
    _filtered = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        from ..style.theme import SearchField

        self._filter = SearchField(
            "Search tags", tip="Searches the tag names, types, equipment, projects and comments"
        )
        lay.addWidget(self._filter)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._project_label = QtWidgets.QLabel("Project:")
        row.addWidget(self._project_label)
        self._project = QtWidgets.QComboBox()
        self._project.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._project.setMinimumContentsLength(10)
        self._project.setToolTip("Show the tags of one project")
        self._project.addItem(ALL_PROJECTS, None)
        self._project_label.setBuddy(self._project)
        row.addWidget(self._project, 1)
        self._root_label = QtWidgets.QLabel("From:")
        row.addWidget(self._root_label)
        self._root = QtWidgets.QComboBox()
        self._root.setToolTip("The compiled project whose tags are listed")
        self._root_label.setBuddy(self._root)
        row.addWidget(self._root, 1)
        self._root_label.hide()
        self._root.hide()
        lay.addLayout(row)
        self._model = TagTableModel(DOCK_COLUMNS, parent=self)
        # a column sort of a large scope runs on the search worker (_apply_filter)
        self._model.sort_worker = self._apply_filter
        self._model.sort_worker_rows = self.ASYNC_ROWS
        self._table = QtWidgets.QTableView(self)
        self._table.setAccessibleName("Tags")
        self._table.setModel(self._model)
        hdr = self._table.horizontalHeader()
        hdr.setSortIndicator(0, QtCore.Qt.AscendingOrder)
        from ..tables import make_resizable

        # (the first widths are the panel's own, not fitted to the text: _fit_columns shares
        # the panel's width so that Tag, Type, Equipment and Project are all in view. The widths
        # the user sets are remembered; Comment follows them, the scroll bar shows it.)
        make_resizable(self._table, "TagsDock/tags", stretch=None, fit_on_first_fill=False, caps={0: 0.5})
        _register_hooks()
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self._table.setWordWrap(False)
        self._table.doubleClicked.connect(self._copy)
        self._table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self._table, 1)
        self._count = QtWidgets.QLabel("")
        self._count.setStyleSheet("color:%s;" % _dim().name())
        lay.addWidget(self._count)
        self._filter.attach(self._table, self)
        self._filter_busy = False
        self._filter_want = None
        self._filtered.connect(self._on_filtered)
        self._filter.searchChanged.connect(lambda _t: self._apply_filter())
        self._project.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self._root.activated.connect(self._root_chosen)
        self._dir = None  # the page folder the panel is for
        self._loaded_key = None  # the (folder, root) its list shows
        self._index = None  # the TagIndex shown
        self._loading = False
        self._loading_key = None
        self._gen = 0
        self._task = None  # the load's background task (ui/jobs/tasks)
        # (the window closed with a read going on: the loader's done signal never reaches
        # the deleted panel, so the task ends with the panel; the dict outlives the Qt object)
        self.destroyed.connect(lambda *_a, d=self.__dict__: _end_token(d.pop("_task", None)))
        self._loader = _Loader(self)
        self._loader.progress.connect(self._on_progress)
        self._loader.partial.connect(self._on_partial)
        self._loader.done.connect(self._on_done)
        self._load_timer = QtCore.QTimer(self)  # a load deferred past the page's paint
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(0)
        self._load_timer.timeout.connect(self._load_if_shown)
        self._show_count()

    # -- what is listed ---------------------------------------------------------------------
    def set_project_dir(self, path: str) -> None:
        """The open page's folder: its tags are listed when the panel is shown
        (read in a worker); nothing is read while it is hidden, nor again for
        the scope it already shows. None (no page open): the list empties, and a read
        going on ends, its task with it (the page it was for has gone)."""
        if not path:
            self._dir = None
            self._loaded_key = None
            self._gen += 1  # (a late answer of the read is ignored)
            if self._loading:
                self._loader.stop()
                self._loading = False
                self._end_task()
            self._index = None
            self._model.clear()
            self._update_roots()
            self._show_count()
            return
        self._dir = path
        self._restore_root()
        if self._loaded_key is not None and self._loaded_key == _tag_key(path) and self._current():
            return
        if self.isVisible():
            self._load_timer.start()  # after the page has shown

    def _restore_root(self) -> None:
        """The compiled project saved for this page's project (tags/scope/<project>)."""
        if not self._dir:
            return
        from ...io import tagscope as TS

        name = TS.project_name(self._dir)
        try:
            from ..prefs.settings import app_settings

            saved = app_settings().value("tags/scope/%s" % name.lower(), "", type=str)
        except Exception:  # noqa: BLE001
            saved = ""
        if saved and not TS.chosen_root(self._dir):
            TS.choose_root(self._dir, saved)

    def _current(self) -> bool:
        """The list shown is the scope's current index."""
        if self._index is None or self._dir is None:
            return False
        from ...io import project as PR

        return PR.cached_tag_index(self._dir) is self._index

    def ensure_loaded(self) -> None:
        """List the current scope's tags now, in this thread (no-op when they are)."""
        if self._dir is None:
            return
        key = _tag_key(self._dir)
        if self._loaded_key == key and self._current() and not self._loading:
            return
        from ...io import project as PR

        self._gen += 1
        self._loading = False
        try:
            idx = PR.tag_index(self._dir)
            self._show_index(key, idx, idx.order(("variable",)), final=True)
        except Exception:  # noqa: BLE001 -- the panel is an aid
            self._show_index(key, None, (), final=True)

    def _load_if_shown(self) -> None:
        if not self.isVisible() or self._dir is None:
            return
        key = _tag_key(self._dir)
        if self._loaded_key == key and self._current() and not self._loading:
            return
        from ...io import project as PR

        cached = PR.cached_tag_index(self._dir)
        if cached is not None:
            self._gen += 1
            self._loading = False
            self._show_index(key, cached, cached.order(("variable",)), final=True)
            return
        self._gen += 1
        self._loading = True
        self._loading_key = key
        self._model.clear()
        self._index = None
        self._update_roots()
        self._count.setText("Loading tags")
        # (a background task too: the status bar's tasks button shows the projects read,
        # the time so far and Cancel; it runs by itself, so it says nothing when it ends)
        from ..jobs import tasks
        from ...io import tagscope as TS

        self._end_task(tasks)
        self._task = tasks.begin(
            "tags",
            "Loading the tags of %s…" % TS.project_name(self._dir),
            cancel=self.stop_loading,
            window=self,
            quiet=True,
        )
        self._loader.start(self._gen, self._dir)

    def stop_loading(self) -> None:
        """Cancel of the tags load (the tasks button): the read ends at its next project;
        the panel says so and reads again when it is shown next."""
        if not self._loading:
            return
        self._loader.stop()
        self._count.setText("Stopping")

    def _end_task(self, tasks=None) -> None:
        if tasks is None:
            from ..jobs import tasks
        token = getattr(self, "_task", None)
        self._task = None
        if token is not None:
            tasks.end(token, tell=False)

    def _on_progress(self, gen: int, n: int, m: int, _project: str) -> None:
        if gen == self._gen and self._loading:
            self._count.setText("Loading %d of %d projects" % (n, m))
            from ..jobs import tasks

            tasks.progress(getattr(self, "_task", None), n, m)

    def _on_partial(self, gen: int, result) -> None:
        if gen != self._gen or not self._loading or isinstance(result, Exception):
            return
        idx, order = result
        self._show_index(self._loading_key, idx, order, final=False)

    def _on_done(self, gen: int, result) -> None:
        if gen != self._gen:
            return
        self._loading = False
        self._end_task()
        from ...io.project import TagIndexStopped

        if isinstance(result, TagIndexStopped):
            # (cancelled: the list stays as it is, read again when the panel is shown next)
            self._loaded_key = None
            self._count.setText("Loading stopped. Show the panel again to load the tags")
            return
        if isinstance(result, Exception):
            self._show_index(self._loading_key, None, (), final=True)
            return
        idx, order = result
        self._show_index(self._loading_key, idx, order, final=True)

    def _show_index(self, key, idx, order, final: bool) -> None:
        self._load_timer.stop()
        keep = self._project.currentData()
        self._index = idx if final else None
        self._loaded_key = key if final else None
        self._model.set_source(idx, order)
        self._fill_projects(keep)
        self._update_roots()
        self._apply_filter()
        from .. import tables

        if final and not tables.has_user_widths(self._table):
            self._fit_columns((key, id(idx)))

    def _fit_columns(self, key=None) -> None:
        """Initial column widths for the Tags panel."""
        from ..prefs.settings import whole

        hdr = self._table.horizontalHeader()
        col = {c: DOCK_COLUMNS.index(c) for c in DOCK_COLUMNS}
        kept = self.__dict__.setdefault("_type_widths", OrderedDict())
        type_max = whole("tags/type_max_width", COLUMN_SPANS["tags/type_max_width"])
        type_w = kept.get((key, type_max)) if key is not None else None
        if type_w is None:
            # (the first 200 rows say what a Type column needs)
            hdr.setResizeContentsPrecision(200)
            self._table.resizeColumnToContents(col["type"])
            type_w = min(hdr.sectionSize(col["type"]), type_max)
            if key is not None:
                kept[(key, type_max)] = type_w
                while len(kept) > 8:
                    kept.popitem(last=False)
        hdr.resizeSection(col["type"], type_w)
        name_min = whole("tags/name_min_width", COLUMN_SPANS["tags/name_min_width"])
        least = whole("tags/min_column_width", COLUMN_SPANS["tags/min_column_width"])
        name_share = whole("tags/name_share", COLUMN_SPANS["tags/name_share"]) / 100.0
        equip_share = whole("tags/equipment_share", COLUMN_SPANS["tags/equipment_share"]) / 100.0
        rest = max(0, self._table.viewport().width() - type_w)
        name_w = max(name_min, int(rest * name_share))
        equip_w = max(least, int(rest * equip_share))
        hdr.resizeSection(col["name"], name_w)
        hdr.resizeSection(col["equip"], equip_w)
        hdr.resizeSection(col["project"], max(least, rest - name_w - equip_w))

    def apply_column_settings(self) -> None:
        """Settings > Appearance > Tags panel changed: the first widths again (a width the
        user set stays)."""
        from .. import tables

        if not tables.has_user_widths(self._table):
            self._fit_columns()

    def _fill_projects(self, keep) -> None:
        counts = self._model.project_counts()
        self._project.blockSignals(True)
        try:
            self._project.clear()
            self._project.addItem(ALL_PROJECTS, None)
            for name, n in counts.items():
                self._project.addItem("%s (%d)" % (name, n), name)
            k = self._project.findData(keep) if keep else 0
            self._project.setCurrentIndex(k if k >= 0 else 0)
        finally:
            self._project.blockSignals(False)
        many = len(counts) > 1
        self._project.setEnabled(many)

    def _update_roots(self) -> None:
        """The 'From:' drop-down: shown when several compiled projects include
        the page's project."""
        from ...io import tagscope as TS

        roots = TS.roots(self._dir) if self._dir else []
        show = len(roots) > 1
        self._root_label.setVisible(show)
        self._root.setVisible(show)
        if not show:
            return
        cur = TS.scope(self._dir).root
        self._root.blockSignals(True)
        try:
            self._root.clear()
            for r in roots:
                self._root.addItem(r, r)
            k = self._root.findData(cur)
            self._root.setCurrentIndex(k if k >= 0 else 0)
        finally:
            self._root.blockSignals(False)

    def _root_chosen(self, _i: int) -> None:
        root = self._root.currentData()
        if not root or not self._dir:
            return
        from ...io import tagscope as TS

        if (TS.chosen_root(self._dir) or "").lower() == str(root).lower():
            return
        TS.choose_root(self._dir, root)
        try:
            from ..prefs.settings import app_settings

            app_settings().setValue("tags/scope/%s" % TS.project_name(self._dir).lower(), root)
        except Exception:  # noqa: BLE001
            pass
        self._loaded_key = None
        # (hidden: nothing is read now; the list follows when the panel is shown)
        if self.isVisible():
            self._load_if_shown()
        # the Simulate tabs of this project run it as the same compiled project
        try:
            from ..simulate.ctsim_source import root_choice_changed

            root_choice_changed(self._dir)
        except Exception:  # noqa: BLE001 -- the tag list changed; the simulator follows later
            from ...infra import logsetup

            logsetup.note_exception("ui.tags_dock.root_chosen_sim")

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        if (
            self._dir is not None
            and (self._loaded_key != _tag_key(self._dir) or not self._current())
            and not self._loading
        ):
            self._load_timer.start()

    # -- filter / status ----------------------------------------------------------------------
    def _apply_filter(self) -> None:
        want = (self._filter.text(), self._project.currentData(), None, self._filter.regex)
        if self._model.total() < self.ASYNC_ROWS:
            self._model.set_filter(want[0], want[1], regex=want[3])
            self._show_count()
            return
        # a large scope: one worker, the newest search next (the box stays live meanwhile)
        self._filter_want = want
        if not self._filter_busy:
            self._start_filter()

    def _start_filter(self) -> None:
        want, self._filter_want = self._filter_want, None
        if want is None:
            return
        m = self._model
        if m.shows(want):
            self._show_count()
            return
        self._filter_busy = True
        sort = m._sort

        def work():
            try:
                base, rows, last = m.rows_for(*want, sort=sort)
                res = (base, rows, last, sort)
            except Exception as exc:  # noqa: BLE001  # swallow-ok: noted on the GUI thread
                res = exc
            try:
                self._filtered.emit((want, res))
            except RuntimeError:  # swallow-ok: the panel is gone
                pass

        from ...infra import gcguard

        gcguard.before_worker()  # only the GUI thread may collect garbage
        threading.Thread(target=work, name="tags-search", daemon=True).start()

    def _on_filtered(self, payload) -> None:
        self._filter_busy = False
        want, res = payload
        if isinstance(res, Exception):
            from ...infra import logsetup

            logsetup.note_exception("ui.tags_dock.search", error=repr(res))
        elif not self._model.take_rows(want, res) and self._filter_want is None:
            self._filter_want = want  # the list changed meanwhile: once more
        if self._filter_want is not None:
            self._start_filter()
        else:
            self._show_count()

    def wait_filter(self, timeout_ms: int = 10000) -> bool:
        """Wait for a worker search to land (tests and tools); True when none is left."""
        t = QtCore.QElapsedTimer()
        t.start()
        app = QtWidgets.QApplication.instance()
        while (self._filter_busy or self._filter_want is not None) and t.elapsed() < timeout_ms:
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        return not self._filter_busy and self._filter_want is None

    def _show_count(self) -> None:
        if self._loading and self._index is None and not self._model.total():
            return
        n, total = self._model.rowCount(), self._model.total()
        if not total:
            text = "" if self._dir is None else "No tags for this page"
        elif not n:
            text = "No tags"
        else:
            text = "%d of %d tags" % (n, total)
        if self._loading and total:
            text += ". Loading the other projects"
        self._count.setText(text)

    def _copy(self, index) -> None:
        """A double-click (and the menu's Copy Name): the tag name to the clipboard, said in
        the status bar."""
        name = self._model.name(index.row()) if index is not None and index.isValid() else ""
        if name:
            QtWidgets.QApplication.clipboard().setText(name)
            from ..notify import notify

            notify(self, "Copied %s to the clipboard." % name)

    def _context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            idx = self._table.currentIndex()
        if not idx.isValid():
            return
        m = QtWidgets.QMenu(self)
        m.addAction("Copy Name").triggered.connect(lambda: self._copy(idx))
        m.exec(self._table.viewport().mapToGlobal(pos))
        m.deleteLater()

    # -- for the window ------------------------------------------------------------------------
    def tag_names(self) -> list:
        """Every tag the panel lists (read now when it is not yet)."""
        self.ensure_loaded()
        idx = self._index
        if idx is None:
            return []
        return [idx.name_of(c) for c in self._model._base]

    def focus_search(self) -> None:
        self._filter.setFocus()
        self._filter.selectAll()
