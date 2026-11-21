"""The project and page browser dialog."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import contextlib
import json
import os
import threading
import weakref
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...io.catalog import Entry, ProjectCatalog, ProjectInfo, catalog_for, is_super_genie
from ..panels import panel_options as _panels
from ..prefs.settings import app_settings
from ..style.theme import COLORS, SearchField, icon, type_qss
from . import naming
from .delegates import CARD_MAX, CARD_MIN, CardDelegate, card_size, cell_size
from .model import _COL_SORT, COL_NAME, COL_TEMPLATE, CatalogModel
from .preview import PreviewPane
from .new_mode import NEW_KIND_LABELS, NewMode
from .project_tree import FLAT, TREE, ProjectTree, Scope
from .thumbs import CARD_SIZE, ROW_SIZE, ThumbnailService

MODE_OPEN = "open"
MODE_SAVE_AS = "save_as"
MODE_PICK_TEMPLATE = "pick_template"
MODE_PICK_ELEMENT = "pick_element"
#: File > New: what to create, its project, library and name (browser/new_mode.py)
MODE_NEW = "new"

KIND_LABELS = {
    "page": "Pages",
    "template": "Templates",
    "symbol": "Symbols",
    "genie": "Genies",
    "supergenie": "Super Genies",
}
#: (one, many) nouns of a kind in the texts
KIND_NOUNS = {
    "page": ("page", "pages"),
    "template": ("template", "templates"),
    "symbol": ("symbol", "symbols"),
    "genie": ("genie", "genies"),
    "supergenie": ("super genie", "super genies"),
}
# CtDraw's Open dialog has a Super Genie tab: the '@SUPER' elements are
# listed there and left out of the Genies tab
ALL_KINDS = ("page", "template", "symbol", "genie", "supergenie")
_SORTS = [
    ("name", "Name"),
    ("modified", "Modified (newest)"),
    ("title", "Title / library"),
    ("project", "Project"),
    ("template", "Template"),
]
_POPUPS = [("any", "All pages"), ("hide", "Hide system pages (!)"), ("only", "Only system pages (!)")]
#: the kind of element a library file holds (Open from an element's window)
_LIB_KINDS = {".ctt": "template", ".ctl": "symbol", ".ctm": "genie"}
_S = "pagebrowser/"
#: the settings group of each mode
_PURPOSE = {
    MODE_OPEN: "open",
    MODE_SAVE_AS: "save",
    MODE_PICK_TEMPLATE: "pick",
    MODE_PICK_ELEMENT: "pick",
    MODE_NEW: "new",
}
#: shared settings (every mode and the Library panel)
PROJECT_VIEW_KEY = _S + "project_view"
HIDE_UNREGISTERED_KEY = _S + "hide_unregistered"
POPUPS_KEY = _S + "popups"
_SEARCH_TIP = (
    "Search names, titles, comments, areas, projects and libraries. "
    "Every word must match.\n"
    "tpl:<name>   pages on a template\n"
    "res:<name>   pages of a resolution\n"
    "proj:<name>  pages of a project\n"
    "!   system pages"
)


def project_view() -> str:
    """The shared Tree / Flat choice of the project lists (default Tree)."""
    try:
        v = str(app_settings().value(PROJECT_VIEW_KEY, TREE) or TREE)
    except Exception:  # noqa: BLE001 -- a garbled setting must not break a dialog
        v = TREE
    return v if v in (TREE, FLAT) else TREE


class _ViewNotifier(QtCore.QObject):
    """``changed(view)`` when the shared Tree / Flat choice changes (the
    Library panel follows a change made in the page browser)."""

    changed = QtCore.Signal(str)


_NOTIFIER: List[_ViewNotifier] = []


def view_notifier() -> _ViewNotifier:
    """The application's Tree / Flat change notifier."""
    if _NOTIFIER:
        try:
            _NOTIFIER[0].objectName()  # still alive?
            return _NOTIFIER[0]
        except RuntimeError:
            _NOTIFIER.clear()
    n = _ViewNotifier(QtWidgets.QApplication.instance())
    _NOTIFIER.append(n)
    return n


def set_project_view(view: str) -> None:
    view = view if view in (TREE, FLAT) else TREE
    changed = view != project_view()
    st = app_settings()
    st.setValue(PROJECT_VIEW_KEY, view)
    st.sync()
    if changed:
        view_notifier().changed.emit(view)


@dataclass(frozen=True)
class BrowserResult:
    """What the user picked: ``kind`` page/template/symbol/genie, ``path`` the
    .ctg or library file, ``element`` the element name (elements), ``action``
    open (File > Open: pages AND library elements, which open for editing) /
    use_template / place (the pickers) / save."""

    kind: str
    path: str
    element: str = ""
    action: str = "open"
    entry: Optional[Entry] = None


def reveal_in_explorer(path: str) -> None:
    """Open the file's folder with the file selected (Windows), else the folder."""
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))
    except OSError:
        pass


def _list_icon(size: int = 16) -> QtGui.QIcon:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(size / 20.0, size / 20.0)
    pen = QtGui.QPen(QtGui.QColor(COLORS["text"]), 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    for y in (5.0, 10.0, 15.0):
        p.drawPoint(QtCore.QPointF(3.5, y))
        p.drawLine(QtCore.QPointF(7.0, y), QtCore.QPointF(17.0, y))
    p.end()
    return QtGui.QIcon(pm)


def _tree_icon(size: int = 16) -> QtGui.QIcon:
    """Rows under rows (the Tree view of the project lists)."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(size / 20.0, size / 20.0)
    pen = QtGui.QPen(QtGui.QColor(COLORS["text"]), 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QtCore.QPointF(3.0, 4.0), QtCore.QPointF(12.0, 4.0))
    p.drawLine(QtCore.QPointF(5.0, 4.0), QtCore.QPointF(5.0, 15.0))
    p.drawLine(QtCore.QPointF(5.0, 10.0), QtCore.QPointF(8.0, 10.0))
    p.drawLine(QtCore.QPointF(5.0, 15.0), QtCore.QPointF(8.0, 15.0))
    p.drawLine(QtCore.QPointF(10.0, 10.0), QtCore.QPointF(17.0, 10.0))
    p.drawLine(QtCore.QPointF(10.0, 15.0), QtCore.QPointF(17.0, 15.0))
    p.end()
    return QtGui.QIcon(pm)


class _ElidedLabel(QtWidgets.QLabel):
    """A one-line label that elides its text instead of widening its parent
    (the full text is its tool tip)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(80)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def text(self) -> str:
        return self._full

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 (Qt API)
        fm = self.fontMetrics()
        return QtCore.QSize(fm.horizontalAdvance(self._full) + 4, fm.height())

    def _elide(self) -> None:
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, QtCore.Qt.ElideMiddle, max(10, self.width())))

    def resizeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        self._elide()


def _default_title(mode: str, kinds: Sequence[str], element_filter_super: Optional[bool]) -> str:
    if mode == MODE_OPEN:
        return "Open Page"
    if mode == MODE_SAVE_AS:
        return "Save Page As"
    kind = kinds[0] if kinds else "symbol"
    if mode == MODE_PICK_TEMPLATE or kind == "template":
        return "Select Template"
    if kind == "supergenie" or (kind == "genie" and element_filter_super):
        return "Select Super Genie"
    if kind == "genie":
        return "Select Genie"
    return "Select Symbol"


class PageBrowserDialog(NewMode, QtWidgets.QDialog):
    """Project-aware page / template / library-element browser."""

    OPEN = MODE_OPEN
    NEW = MODE_NEW
    SAVE_AS = MODE_SAVE_AS
    PICK_TEMPLATE = MODE_PICK_TEMPLATE
    PICK_ELEMENT = MODE_PICK_ELEMENT

    def __init__(
        self,
        mode: str = MODE_OPEN,
        parent: Optional[QtWidgets.QWidget] = None,
        catalog: Optional[ProjectCatalog] = None,
        start_path: Optional[str] = None,
        project_dir: Optional[str] = None,
        kinds: Optional[Sequence[str]] = None,
        thumbs: Optional[ThumbnailService] = None,
        title: Optional[str] = None,
        element_filter=None,
        super_genies: Optional[bool] = None,
        start_element: Optional[str] = None,
        ok_text: Optional[str] = None,
        avoid_path: Optional[str] = None,
    ):
        super().__init__(parent)
        self.mode = mode
        #: Open mode as a picker (Underlay a Page: 'Underlay'): the default button's name;
        #: no Delete there
        self._ok_text = ok_text or ""
        #: a page the first selection is not (Underlay a Page: the page itself, which one
        #: click would underlay under itself); normalised, '' = none
        self._avoid_path = os.path.normcase(os.path.abspath(avoid_path)) if avoid_path else ""
        self._purpose = _PURPOSE.get(mode, "open")
        #: ``Entry -> bool``: which library elements are listed (Paste Genie
        #: leaves the super genies out, Paste Super Genie lists only them)
        self.element_filter = element_filter
        self.catalog = catalog or catalog_for()
        self.catalog.refresh()
        self.thumbs = thumbs or ThumbnailService.shared()
        self.start_path = os.path.abspath(start_path) if start_path else None
        self.project_dir = os.path.abspath(project_dir) if project_dir else None
        if kinds is None:
            kinds = {
                MODE_OPEN: ALL_KINDS,
                MODE_SAVE_AS: ("page",),
                MODE_PICK_TEMPLATE: ("template",),
                MODE_PICK_ELEMENT: ("symbol", "genie"),
                MODE_NEW: ALL_KINDS,
            }.get(mode, ALL_KINDS)
        self.kinds: Tuple[str, ...] = tuple(k for k in kinds if k in ALL_KINDS) or ("page",)
        self.result_value: Optional[BrowserResult] = None
        self.confirm_replace = self._ask_replace  # tests may replace this hook
        # Delete / Rename (Open mode): ``before_delete(kind, path, element)``
        # runs after the confirmation and may cancel (the main window closes
        # the open tabs there); the ask hooks replace the confirmations (tests)
        self.before_delete = None
        self.ask_delete_page = None
        self.ask_delete_element = None
        self.ask_element_name = None
        self.deleted: List[str] = []  # what this dialog deleted / renamed
        self._scope: Optional[Scope] = None
        self._kind = self.kinds[0]
        self._want_select: Optional[str] = self.start_path
        # (both made absolute above, then normcase: the same page however it was named)
        if self._avoid_path and self.start_path and os.path.normcase(self.start_path) == self._avoid_path:
            self._want_select = None  # (its project is still the one listed)
        # the element open in an element window: selected in its library
        self._want_element: Optional[str] = start_element or None
        self._verdict: Optional[naming.Verdict] = None
        self._name_touched = False
        self._last_key: Optional[str] = None
        self._start_scope_id: Optional[str] = None
        self._includes_user: Optional[bool] = None  # the check box as the user left it
        # the pickers list only what the page can place: its project and its
        # include chain (the Graphics Builder's search order)
        self._chain_anchor: Optional[ProjectInfo] = None
        self._chain_keys: set = set()
        if self.is_picker():
            anchor_dir = self.project_dir or (os.path.dirname(self.start_path) if self.start_path else None)
            if anchor_dir:
                info = self.catalog.project_for_dir(anchor_dir)
                if info is None and os.path.isdir(anchor_dir):
                    info = self.catalog.folder(anchor_dir)
                if info is not None:
                    self._chain_anchor = info
                    self._chain_keys = {p.key for p in self.catalog.closure(info)}
        self._new_init()
        self.setWindowTitle(
            title or ("New" if mode == MODE_NEW else _default_title(mode, self.kinds, super_genies))
        )
        self.setSizeGripEnabled(True)
        self.resize(1280, 780)
        # it keeps its own size and position per purpose (_restore_settings)
        self.setProperty("_ctgNoGeometry", True)
        self._card_w = CARD_SIZE.width()

        self._build()
        self._restore_settings()
        self._pick_start_scope()
        base = self.tree.current_base_scope()
        self._start_scope_id = base.id if base is not None else None
        QtCore.QTimer.singleShot(0, self, self._focus_initial)

    # ------------------------------------------------------------------ settings keys
    def _key(self, name: str) -> str:
        return "%s%s/%s" % (_S, self._purpose, name)

    def is_picker(self) -> bool:
        return self.mode in (MODE_PICK_TEMPLATE, MODE_PICK_ELEMENT)

    def _value(self, key: str, default=None, typ=None):
        try:
            st = app_settings()
            return st.value(key, default, type=typ) if typ is not None else st.value(key, default)
        except Exception:  # noqa: BLE001 -- a garbled setting must not break the dialog
            return default

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)

        # left: Tree | Flat + the project tree
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(_sp(1), _sp(2), 0, _sp(1))
        ll.setSpacing(_sp(1))
        vrow = QtWidgets.QHBoxLayout()
        vrow.setSpacing(_sp(0))
        self.btn_tree = QtWidgets.QToolButton()
        self.btn_tree.setIcon(_tree_icon(16))
        self.btn_tree.setText("Tree")
        self.btn_tree.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.btn_tree.setCheckable(True)
        self.btn_tree.setToolTip("Show projects under the projects that include them")
        self.btn_flat = QtWidgets.QToolButton()
        self.btn_flat.setIcon(_list_icon(16))
        self.btn_flat.setText("Flat")
        self.btn_flat.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.btn_flat.setCheckable(True)
        self.btn_flat.setToolTip("Show every project once, in name order")
        vrow.addWidget(self.btn_tree)
        vrow.addWidget(self.btn_flat)
        vrow.addStretch(1)
        ll.addLayout(vrow)

        recent = (
            self._recent_paths()
            if "page" in self.kinds and not self.is_picker() and self.mode != MODE_NEW
            else []
        )
        folders = []
        anchor = self.start_path or (os.path.join(self.project_dir, "_") if self.project_dir else None)
        if anchor and not self.is_picker():
            d = os.path.dirname(anchor)
            if self.catalog.project_for_dir(d) is None and os.path.isdir(d):
                folders.append(self.catalog.folder(d))
        view = project_view()
        inc_default = self.is_picker()
        with_inc = bool(self._value(self._key("with_includes"), inc_default, bool))
        self._includes_user = with_inc
        self.tree = ProjectTree(
            self.catalog,
            recent=recent,
            folders=folders,
            kind=self._kind,
            view=view,
            chain_of=self._chain_anchor,
            with_includes=with_inc,
        )
        ll.addWidget(self.tree, 1)
        self.splitter.addWidget(left)
        self._sync_view_buttons()

        # centre
        mid = QtWidgets.QWidget()
        mv = QtWidgets.QVBoxLayout(mid)
        mv.setContentsMargins(_sp(2), _sp(2), _sp(2), _sp(1))
        mv.setSpacing(_sp(1))
        top = QtWidgets.QHBoxLayout()
        self.tabs = QtWidgets.QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        # (on a narrow screen the kinds scroll rather than push the window off it)
        self.tabs.setUsesScrollButtons(True)
        for k in self.kinds:
            self.tabs.addTab(NEW_KIND_LABELS[k] if self.mode == MODE_NEW else KIND_LABELS[k])
        self.tabs.setVisible(len(self.kinds) > 1)
        top.addWidget(self.tabs)
        self.kind_label = QtWidgets.QLabel(KIND_LABELS[self._kind])
        self.kind_label.setStyleSheet(type_qss("subtitle"))
        self.kind_label.setVisible(len(self.kinds) == 1)
        top.addWidget(self.kind_label)
        top.addStretch(1)
        # the one search box of the UI guide (theme.SearchField: its pause before the
        # search, its clear button and its '.*' toggle); the browser's own key handling
        # (eventFilter) moves the cards and types into it
        self.search = SearchField("", delay_ms=120, tip=_SEARCH_TIP, regex_key="Browser search")
        self.search.setAccessibleName("Search")
        self.search.setMinimumWidth(200)
        self.search.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.search.setMaximumWidth(460)
        self.search_regex = self.search.regex_toggle
        top.addWidget(self.search)
        mv.addLayout(top)
        self._build_page_list(mv)
        return self._build_status_and_preview(mid, mv, outer)

    def _build_page_list(self, mv):
        chips = QtWidgets.QHBoxLayout()
        self.scope_lbl = _ElidedLabel()
        self.scope_lbl.setStyleSheet("color:%s;" % COLORS["text_dim"])
        chips.addWidget(self.scope_lbl, 1)
        chips.addSpacing(6)
        self.chk_includes = QtWidgets.QCheckBox("With included projects")
        self.chk_includes.setToolTip("Also list the projects this project includes")
        self.chk_includes.setChecked(self.tree.with_includes())
        chips.addWidget(self.chk_includes)
        chips.addSpacing(6)
        self.chip_unreg = QtWidgets.QToolButton()
        self.chip_unreg.setAutoRaise(True)
        self.chip_unreg.setStyleSheet("QToolButton { color:%s; }" % COLORS["warn"])
        self.chip_unreg.setToolTip("Pages with no pages.DBF row")
        self.chip_unreg.clicked.connect(self._toggle_unregistered)
        chips.addWidget(self.chip_unreg)
        self.cb_popups = QtWidgets.QComboBox()
        self.cb_popups.setAccessibleName("System pages")
        self.cb_popups.setToolTip("Which pages to list: system pages have names starting with '!'")
        for key, lab in _POPUPS:
            self.cb_popups.addItem(lab, key)
        chips.addWidget(self.cb_popups)
        self.cb_sort = QtWidgets.QComboBox()
        self.cb_sort.setAccessibleName("Sort")
        for key, lab in _SORTS:
            self.cb_sort.addItem("Sort: " + lab, key)
        for cb in (self.cb_popups, self.cb_sort):
            cb.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
        self.btn_grid = QtWidgets.QToolButton()
        self.btn_grid.setIcon(icon("grid", 16))
        self.btn_grid.setCheckable(True)
        self.btn_grid.setToolTip("Thumbnail grid (Ctrl+1)")
        self.btn_grid.setAccessibleName("Thumbnail grid")
        self.btn_list = QtWidgets.QToolButton()
        self.btn_list.setIcon(_list_icon(16))
        self.btn_list.setCheckable(True)
        self.btn_list.setToolTip("Details list (Ctrl+2)")
        self.btn_list.setAccessibleName("Details list")
        mv.addLayout(chips)

        self.model = CatalogModel(self.thumbs, self)
        self.stack = QtWidgets.QStackedWidget()
        self.grid = QtWidgets.QListView()
        self.grid.setViewMode(QtWidgets.QListView.IconMode)
        self.grid.setResizeMode(QtWidgets.QListView.Adjust)
        self.grid.setMovement(QtWidgets.QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(cell_size(self._card_w))
        self.grid.setSpacing(0)
        self.grid.setMouseTracking(True)
        self.grid.setWordWrap(False)
        self.grid.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.grid.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.grid.setItemDelegate(
            CardDelegate(self.grid, lambda: self.model.common_prefix, lambda: self._card_w)
        )
        self.grid.setModel(self.model)
        self.grid.setAccessibleName("Pages and library elements")
        # (an edge in the background colour: the keyboard focus ring takes its place;
        # written in the design colours, which the theme maps to the active scheme and
        # accent on every change)
        self.grid.setStyleSheet(
            "QListView { background:#1f2226; border:1px solid #1f2226; }"
            'QListView[kbdFocus="true"] { border:1px solid #4da3ff; }'
        )
        self.table = QtWidgets.QTreeView()
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setIconSize(ROW_SIZE)
        self.table.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setModel(self.model)
        self.table.setAccessibleName("Pages and library elements")
        # one selection model: both views show the same current item
        self.table.setSelectionModel(self.grid.selectionModel())
        hdr = self.table.header()
        hdr.setSectionsClickable(True)
        hdr.setSortIndicatorShown(True)
        hdr.sectionClicked.connect(self._on_header_clicked)
        hdr.setSortIndicator(COL_NAME, QtCore.Qt.AscendingOrder)
        from ..tables import make_resizable

        # The columns fit their text the first time the list is shown with rows, measured
        # from the model's text over every row (text_columns), never through the Name
        # cells' thumbnails; the Name column takes what the others leave; the columns widen
        # to the Template, Size and Objects text that comes later from the page summaries
        # read in the background (grow). The widths the user sets are remembered. A
        # double-click on a divider measures the model's text too; Qt's own measure (the
        # precision) stays on the rows on screen, whose thumbnails are asked for anyway.
        hdr.setResizeContentsPrecision(0)
        make_resizable(
            self.table,
            "PageBrowserDialog/list",
            stretch=COL_NAME,
            grow=True,
            text_columns=range(self.model.columnCount()),
        )

    def _build_status_and_preview(self, mid, mv, outer):
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        self._view_mode = "grid"
        if self.mode == MODE_NEW:
            self._new_build_template_box()
        mv.addWidget(self.stack, 1)
        foot = QtWidgets.QHBoxLayout()
        self.status = _ElidedLabel()
        self.status.setStyleSheet("color:%s;" % COLORS["text_dim"])
        foot.addWidget(self.status, 1)
        foot.addWidget(self.cb_sort)
        foot.addSpacing(4)
        foot.addWidget(self.btn_grid)
        foot.addWidget(self.btn_list)
        foot.addSpacing(4)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(CARD_MIN, CARD_MAX)
        self.slider.setSingleStep(8)
        self.slider.setPageStep(32)
        self.slider.setFixedWidth(96)
        self.slider.setValue(self._card_w)
        self.slider.setToolTip("Card size (Ctrl+wheel over the grid)")
        self.slider.setAccessibleName("Card size")
        foot.addWidget(self.slider)
        mv.addLayout(foot)
        self.splitter.addWidget(mid)

        # right: preview
        self.preview = PreviewPane(self.thumbs, self.catalog)
        # it hides below PREVIEW_MIN_WIDTH: its own minimum (264 px with a long reference)
        # must not keep the window wider than that, or it never gets there
        self.preview.setMinimumWidth(200)
        self.splitter.addWidget(self.preview)
        self.splitter.setSizes([300, 660, 340])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        outer.addWidget(self._build_action_bar())

        # wiring
        self.search.searchChanged.connect(lambda _t: self._apply_search())
        self.tree.scopeChanged.connect(self._set_scope)
        self.tree.includesChanged.connect(self._on_tree_includes)
        self.chk_includes.toggled.connect(self._on_includes_toggled)
        self.btn_tree.clicked.connect(lambda: self.set_project_view(TREE))
        self.btn_flat.clicked.connect(lambda: self.set_project_view(FLAT))
        self.tabs.currentChanged.connect(self._on_tab)
        self.btn_grid.clicked.connect(lambda: self.set_view("grid"))
        self.btn_list.clicked.connect(lambda: self.set_view("list"))
        self.cb_popups.currentIndexChanged.connect(self._on_facets)
        self.cb_sort.currentIndexChanged.connect(self._on_sort)
        self.slider.valueChanged.connect(self.set_card_width)
        self.grid.selectionModel().currentChanged.connect(self._on_current)
        self.grid.doubleClicked.connect(lambda _i: self.accept_current())
        self.table.doubleClicked.connect(lambda _i: self.accept_current())
        self.grid.clicked.connect(self._on_user_pick)
        self.table.clicked.connect(self._on_user_pick)
        self.model.countsChanged.connect(self._on_counts)
        self.model.modelReset.connect(self._after_reset)
        self.preview.openPathRequested.connect(self._open_other_path)
        self.preview.selectPageRequested.connect(self._select_page_named)
        for w in (self.grid, self.table, self.search, self.grid.viewport()):
            w.installEventFilter(self)
        for v in (self.grid, self.table):
            v.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            v.customContextMenuRequested.connect(lambda pos, v=v: self._context_menu(v, pos))

        def sc(seq, fn):
            s = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            s.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
            return s

        sc("Ctrl+F", self.focus_search)
        sc("Ctrl+L", self.focus_search)
        sc("Ctrl+1", lambda: self.set_view("grid"))
        sc("Ctrl+2", lambda: self.set_view("list"))
        sc("F5", self.rescan)
        sc("Ctrl+Shift+C", self.preview.copy_path)
        for i in range(len(self.kinds)):
            sc("Alt+%d" % (i + 1), lambda i=i: self.tabs.setCurrentIndex(i))

    def _build_action_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setObjectName("browserActions")
        bar.setStyleSheet(
            "QFrame#browserActions { background:%s; border-top:1px solid %s; }"
            % (COLORS["panel"], COLORS["border"])
        )
        self.chk_linked = None
        self.ok_button = QtWidgets.QPushButton()
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.btn_help = QtWidgets.QPushButton("Help")
        self.btn_help.setToolTip("Open the help page for this dialog (F1)")
        self.btn_help.setAutoDefault(False)
        self.btn_help.clicked.connect(self.show_help)
        self.cb_project = None
        self.ed_name = None
        self.msg = None
        self.btn_browse = None
        self.btn_delete = None
        if self.mode == MODE_NEW:
            self._new_build_bar(bar)
        elif self.mode == MODE_SAVE_AS:
            grid = QtWidgets.QGridLayout(bar)
            grid.setContentsMargins(_sp(3), _sp(2), _sp(3), _sp(2))
            grid.setHorizontalSpacing(_sp(2))
            grid.setVerticalSpacing(_sp(0))
            self._action_layout = grid
            self.btn_browse = QtWidgets.QPushButton("Browse…")
            self.btn_browse.setToolTip("Save to another folder")
            self.btn_browse.clicked.connect(self._browse_save)
            grid.addWidget(self.btn_browse, 0, 0)
            grid.setColumnMinimumWidth(1, 4)
            grid.addWidget(QtWidgets.QLabel("Project:"), 0, 2)
            self.cb_project = QtWidgets.QComboBox()
            self.cb_project.setMinimumWidth(240)
            self.cb_project.setAccessibleName("Project")
            self._fill_project_combo()
            grid.addWidget(self.cb_project, 0, 3)
            grid.setColumnMinimumWidth(4, 4)
            grid.addWidget(QtWidgets.QLabel("Page name:"), 0, 5)
            self.ed_name = QtWidgets.QLineEdit()
            self.ed_name.setMinimumWidth(340)
            self.ed_name.setMaxLength(120)
            self.ed_name.setAccessibleName("Page name")
            if self.start_path:
                self.ed_name.setText(os.path.splitext(os.path.basename(self.start_path))[0])
            grid.addWidget(self.ed_name, 0, 6)
            self.msg = QtWidgets.QLabel()
            self.msg.setWordWrap(True)
            grid.addWidget(self.msg, 1, 6, 1, 5)
            # the page name field takes the extra width (UI guide section 9)
            grid.setColumnStretch(6, 1)
            self.ok_button.setText("Save")
            grid.addWidget(self.ok_button, 0, 8)
            grid.addWidget(self.cancel_button, 0, 9)
            grid.addWidget(self.btn_help, 0, 10)
            self.ed_name.textEdited.connect(self._on_name_edited)
            self.ed_name.textChanged.connect(lambda _t: self._validate())
            self.cb_project.currentIndexChanged.connect(lambda _i: self._validate())
        else:
            bl = QtWidgets.QHBoxLayout(bar)
            bl.setContentsMargins(_sp(3), _sp(2), _sp(3), _sp(2))
            self._action_layout = bl
            if self.mode == MODE_OPEN:
                self.btn_browse = QtWidgets.QPushButton("Browse…")
                self.btn_browse.setToolTip("Open a page from another folder")
                self.btn_browse.clicked.connect(self._browse_files)
                # (Delete sits alone on the left, away from Browse and Open)
                # CtDraw's Open dialog Delete button: a page (its files and project
                # records) or a library element (marked deleted until Pack)
                self.btn_delete = QtWidgets.QPushButton("Delete…")
                self.btn_delete.setToolTip(
                    "Delete the selected page from its project, or the selected element from its library"
                )
                self.btn_delete.setAutoDefault(False)
                self.btn_delete.clicked.connect(self.delete_current)
                from ..help import set_help

                set_help(self.btn_delete, "opening-and-saving#delete-a-page-or-library-element")
                bl.addWidget(self.btn_delete)
                self.btn_delete.setVisible(not self._ok_text)
                bl.addStretch(1)
                bl.addWidget(self.btn_browse)
                bl.addSpacing(12)
                self.ok_button.setText(self._ok_text or "Open")
            else:
                bl.addStretch(1)
                self.ok_button.setText("Select")
            bl.addWidget(self.ok_button)
            bl.addWidget(self.cancel_button)
            bl.addWidget(self.btn_help)
        self.ok_button.setDefault(True)
        self.ok_button.setAutoDefault(True)
        self.cancel_button.setAutoDefault(False)
        if self.btn_browse is not None:
            self.btn_browse.setAutoDefault(False)
        self.ok_button.clicked.connect(self.accept_current)
        self.cancel_button.clicked.connect(self.reject)
        return bar

    def add_linked_option(self) -> QtWidgets.QCheckBox:
        """CtDraw's Select Library Item 'Linked' check box (Paste Symbol): the
        picked symbol is placed linked to its library, or as an unlinked
        copy. One setting with the Library panel's Linked toggle."""
        from ..panels.library_panel import paste_linked, set_paste_linked

        if self.chk_linked is None:
            self.chk_linked = QtWidgets.QCheckBox("Linked")
            self.chk_linked.setToolTip(
                "Place the symbol linked to its library. Untick to place a copy of its objects."
            )
            self.chk_linked.setChecked(paste_linked())
            self.chk_linked.toggled.connect(set_paste_linked)
            self._action_layout.insertWidget(0, self.chk_linked)
        return self.chk_linked

    def show_help(self) -> None:
        """The Help button: this window's help page."""
        from .. import help as H

        H.show_help(H.dialog_topic(self) or "opening-and-saving", main=H.owning_main(self))

    # ------------------------------------------------------------------ settings
    def _recent_paths(self) -> List[str]:
        try:
            mru = [str(p) for p in (app_settings().value("mru", [], type=list) or [])]
        except Exception:  # noqa: BLE001 -- a garbled setting must not break the dialog
            mru = []
        return [p for p in mru if p.lower().endswith(".ctg") and os.path.isfile(p)]

    def _restore_settings(self) -> None:
        geo = self._value(self._key("geometry"))
        if isinstance(geo, (QtCore.QByteArray, bytes)) and geo:
            self.restoreGeometry(QtCore.QByteArray(geo))
        spl = self._value(self._key("splitter"))
        self._splitter_restored = False
        if isinstance(spl, (QtCore.QByteArray, bytes)) and spl:
            self._splitter_restored = bool(self.splitter.restoreState(QtCore.QByteArray(spl)))
        self.set_card_width(self._value(self._key("card_size"), CARD_SIZE.width()))
        self.set_view(str(self._value(self._key("view"), "grid")))
        hide = bool(self._value(HIDE_UNREGISTERED_KEY, False, bool))
        popups = str(self._value(POPUPS_KEY, "any") or "any")
        i = self.cb_popups.findData(popups)
        self.cb_popups.blockSignals(True)
        self.cb_popups.setCurrentIndex(max(0, i))
        self.cb_popups.blockSignals(False)
        self.model.set_facets(hide_unregistered=hide, popups=self.cb_popups.currentData() or "any")
        sort = str(self._value(self._key("sort"), "name"))
        i = self.cb_sort.findData(sort)
        if i >= 0:
            self.cb_sort.blockSignals(True)
            self.cb_sort.setCurrentIndex(i)
            self.cb_sort.blockSignals(False)
            self.model.set_sort(sort)
            self._sync_sort_indicator()
        if len(self.kinds) > 1:
            kind = str(self._value(self._key("kind"), self.kinds[0]))
            # opened from a library element's window: that kind of element
            ext = os.path.splitext(self.start_path or "")[1].lower()
            if _LIB_KINDS.get(ext) in self.kinds:
                kind = _LIB_KINDS[ext]
            if kind in self.kinds:
                self._kind = kind
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(self.kinds.index(kind))
                self.tabs.blockSignals(False)
                self.tree.set_kind(kind)
        raw = self._value(self._key("expanded"), "")
        try:
            paths = json.loads(raw) if isinstance(raw, str) and raw else []
        except ValueError:
            paths = []
        if isinstance(paths, list):
            self.tree.restore_expanded([str(p) for p in paths])
        self._sync_kind_widgets()

    def _save_settings(self) -> None:
        st = app_settings()
        st.setValue(self._key("geometry"), self.saveGeometry())
        st.setValue(self._key("splitter"), self.splitter.saveState())
        st.setValue(self._key("view"), self.view_mode())
        st.setValue(self._key("card_size"), int(self._card_w))
        st.setValue(self._key("sort"), self.model.sort_key)
        if self._includes_user is not None:
            st.setValue(self._key("with_includes"), bool(self._includes_user))
        st.setValue(self._key("expanded"), json.dumps(self.tree.expanded_paths()))
        st.setValue(HIDE_UNREGISTERED_KEY, self.model.hide_unregistered)
        st.setValue(POPUPS_KEY, self.cb_popups.currentData() or "any")
        if len(self.kinds) > 1:
            st.setValue(self._key("kind"), self._kind)
        base = self.tree.current_base_scope()
        if self.mode in (MODE_OPEN, MODE_NEW) and base is not None and base.kind not in ("recent", "folder"):
            st.setValue(self._key("scope"), base.id)
        st.sync()

    # ------------------------------------------------------------------ projects view
    def project_view(self) -> str:
        return self.tree.view()

    def set_project_view(self, view: str) -> None:
        """Tree (the include structure) or Flat (every project once); shared
        with the other project lists."""
        view = view if view in (TREE, FLAT) else TREE
        base = self.tree.current_base_scope()
        self.tree.set_view(view)
        set_project_view(view)
        self._sync_view_buttons()
        if base is not None:
            self.tree.select_scope_id(base.id, emit=False)

    def _sync_view_buttons(self) -> None:
        v = self.tree.view()
        self.btn_tree.setChecked(v == TREE)
        self.btn_flat.setChecked(v == FLAT)

    # ------------------------------------------------------------------ scope / kind
    def _pick_start_scope(self) -> None:
        """Start where the user is: the pickers on the page's project (with
        its include chain unless the check box is off), Open / Save As on the
        current page's project, else (Open) the last scope, else the first
        root project."""
        if self.is_picker() and self._chain_anchor is not None:
            if self.tree.select_project(self._chain_anchor):
                self._promote_to_chain_if_empty()
                return
        if self.mode == MODE_NEW:
            # New starts where the last one was made
            last = str(self._value(self._key("scope"), "") or "")
            if last and self.tree.select_scope_id(last):
                return
        anchor_dir = os.path.dirname(self.start_path) if self.start_path else self.project_dir
        if anchor_dir:
            info = self.catalog.project_for_dir(anchor_dir)
            if info is None and os.path.isdir(anchor_dir):
                info = self.catalog.folder(anchor_dir)
            if info is not None and self.tree.select_project(info):
                self._promote_to_chain_if_empty()
                return
        if self.mode == MODE_OPEN:
            last = str(self._value(self._key("scope"), "") or "")
            if last and self.tree.select_scope_id(last):
                self._promote_to_chain_if_empty()
                return
        first = self.tree.first_root_scope_id()
        if first and self.tree.select_scope_id(first):
            self._promote_to_chain_if_empty()
            return
        self._set_scope(None)

    def _set_scope(self, scope: Optional[Scope]) -> None:
        self._scope = scope
        base = self.tree.current_base_scope()
        self.chk_includes.setEnabled(base is not None and base.kind == "project")
        if (
            self.mode in (MODE_SAVE_AS, MODE_NEW)
            and scope is not None
            and scope.anchor is not None
            and scope.kind in ("project", "folder", "chain")
        ):
            self._select_combo_dir(scope.anchor.dir, add_info=scope.anchor)
        self._reload()
        self._warm_libraries(scope)

    def _warm_libraries(self, scope: Optional[Scope]) -> None:
        """Read the scope's library directories on a thread (a directory read is a page
        summary read), so the first symbol / genie listing of a large chain finds them in
        the catalog."""
        if scope is None or scope.kind == "recent":
            return
        kinds = [k for k in self.kinds if k not in ("page", "supergenie")]
        if "supergenie" in self.kinds and "genie" not in kinds:
            kinds.append("genie")
        if not kinds:
            return
        warmed = self.__dict__.setdefault("_warmed_scopes", set())
        if scope.id in warmed:
            return
        warmed.add(scope.id)
        projects = list(scope.projects)
        if self.is_picker() and self._chain_anchor is not None:
            with contextlib.suppress(Exception):
                projects = list(self.catalog.closure(self._chain_anchor)) + projects
        cat = self.catalog

        def run() -> None:
            from ...infra import gcguard

            gcguard.before_worker()
            for p in projects:
                for k in kinds:
                    try:
                        cat.libraries(p, k)
                    except Exception:  # noqa: BLE001  # swallow-ok: the GUI reads it itself then
                        pass

        threading.Thread(target=run, name="browser-libraries", daemon=True).start()

    def set_scope(self, scope: Scope) -> None:
        """Show ``scope`` (selecting its tree row when there is one)."""
        if not self.tree.select_scope_id(scope.id):
            self._set_scope(scope)

    def current_scope(self) -> Optional[Scope]:
        return self._scope

    def with_includes(self) -> bool:
        return self.tree.with_includes()

    def set_with_includes(self, on: bool) -> None:
        """The 'With included projects' check box."""
        self.chk_includes.setChecked(bool(on))

    def _on_includes_toggled(self, on: bool) -> None:
        self._includes_user = bool(on)
        self.tree.set_with_includes(bool(on))

    def _on_tree_includes(self, on: bool) -> None:
        # a 'c:' id was selected (a saved scope): the check box follows
        self.chk_includes.blockSignals(True)
        self.chk_includes.setChecked(bool(on))
        self.chk_includes.blockSignals(False)
        self._includes_user = bool(on)

    def _on_tab(self, i: int) -> None:
        if 0 <= i < len(self.kinds):
            self.set_kind(self.kinds[i])

    def set_kind(self, kind: str) -> None:
        if kind not in self.kinds:
            return
        if self.tabs.currentIndex() != self.kinds.index(kind):
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self.kinds.index(kind))
            self.tabs.blockSignals(False)
        if kind == self._kind and self.model.kind == kind and self.model.rowCount():
            return
        self._kind = kind
        self.tree.set_kind(kind)
        self._sync_kind_widgets()
        self._reload()
        self._promote_to_chain_if_empty()

    def _promote_to_chain_if_empty(self) -> None:
        """Templates / symbols / genies of a page's project usually live in its
        INCLUDED projects: when the project itself has none of the current kind,
        show its includes too (only on a kind switch / at start, never over an
        explicit choice; the check box setting is left as the user left it)."""
        s = self._scope
        if (
            self._kind == "page"
            or s is None
            or s.kind != "project"
            or s.anchor is None
            or self.model.total
            or s.anchor.is_folder
            or self.mode == MODE_NEW
        ):
            return
        if any(self.catalog.count(p, self._kind) for p in self.catalog.closure(s.anchor)[1:]):
            keep = self._includes_user
            self.chk_includes.setChecked(True)
            self._includes_user = keep

    def current_kind(self) -> str:
        return self._kind

    def _kind_noun(self, n: int = 2) -> str:
        one, many = KIND_NOUNS.get(self._kind, (self._kind, self._kind))
        return one if n == 1 else many

    def _sync_kind_widgets(self) -> None:
        pages = self._kind == "page"
        self.cb_popups.setVisible(pages)
        self.kind_label.setText(KIND_LABELS[self._kind])
        # symbols / genies have no resolution (a template's is its name)
        self.table.setColumnHidden(COL_TEMPLATE, self._kind in ("symbol", "genie", "supergenie"))
        if self.mode == MODE_OPEN:
            # Open OPENS: a template / symbol / genie opens as an editable
            # element (placing belongs to the Library panel)
            self.ok_button.setText(self._ok_text or "Open")
        elif self.mode == MODE_NEW:
            self.kind_label.setText(NEW_KIND_LABELS[self._kind])
            self._new_sync_kind()

    def _entries_for(self, scope: Optional[Scope], kind: str) -> List[Entry]:
        if scope is None or (self.mode == MODE_NEW and kind == "page"):
            return []
        if scope.kind == "recent":
            if kind != "page":
                return []
            out = []
            for p in scope.paths:
                e = self.catalog.entry_for_path(p)
                if e is not None:
                    out.append(e)
            return out
        if self.is_picker() and self._chain_anchor is not None and kind != "page":
            # what the page can place: the copy its include chain resolves
            # first; another copy is hidden, not listed
            want = {p.key for p in scope.projects}
            chain = self.catalog.closure(self._chain_anchor)
            entries = [
                e
                for e in self.catalog.entries(chain, kind)
                if not e.shadowed_by and os.path.normcase(os.path.abspath(e.project_dir)) in want
            ]
        else:
            entries = self.catalog.entries(scope.projects, kind, shadow=scope.kind != "install")
        if kind == "genie" and "supergenie" in self.kinds:
            # the super genies have their own tab
            entries = [e for e in entries if not is_super_genie(e.name)]
        if self.element_filter is not None and kind != "page":
            entries = [e for e in entries if self.element_filter(e)]
        return entries

    #: below this width the preview pane hides (the three panes need 1105 px)
    PREVIEW_MIN_WIDTH = 1150

    def preview_wanted(self) -> bool:
        """The preview pane shows: Settings > Pages and libraries > Open and Save As >
        Preview pane (When the window is wide: from :attr:`PREVIEW_MIN_WIDTH`; Always;
        Never). File > New's page form has none (its template picker shows the page)."""
        if self.mode == MODE_NEW and self._kind == "page":
            return False
        mode = _panels.PREVIEW_PANE
        if mode == "auto":
            return self.width() >= self.PREVIEW_MIN_WIDTH
        return mode == "always"

    def resizeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        pv = getattr(self, "preview", None)
        if pv is not None:
            want = self.preview_wanted()
            if (not pv.isHidden()) != want:  # (before the first show too)
                pv.setVisible(want)

    def _reload(self) -> None:
        busy = QtWidgets.QApplication.overrideCursor() is None
        if busy:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            entries = self._entries_for(self._scope, self._kind)
            self.thumbs.cancel_pending()
            self.model.set_entries(
                entries, self._kind, keep_order=self._scope is not None and self._scope.kind == "recent"
            )
            self._maybe_prefetch()
        finally:
            if busy:
                QtWidgets.QApplication.restoreOverrideCursor()
        self._update_scope_label()
        self._update_placeholder()

    def rescan(self) -> None:
        """F5: re-read everything (MASTER.DBF / include.dbf, folder listings,
        pages.DBF) and show the current scope again."""
        cur = self.current_entry()
        base = self.tree.current_base_scope()
        sid = base.id if base is not None else None
        self.catalog.reload()
        if self._chain_anchor is not None:
            self._chain_keys = {p.key for p in self.catalog.closure(self._chain_anchor)}
        self.tree.rebuild()
        if cur is not None:
            self._want_select = cur.path if cur.kind == "page" else cur.key
        if sid and self.tree.select_scope_id(sid, emit=False):
            self._scope = self.tree.current_scope()
        self._reload()

    def _update_scope_label(self) -> None:
        s = self._scope
        if s is None:
            self.scope_lbl.setText("No project")
            return
        n = self.model.total
        where = s.label
        if s.kind in ("project", "chain") and s.anchor is not None and not s.is_live:
            where += " (other install)"
        self.scope_lbl.setText("%s · %d %s" % (where, n, self._kind_noun(n)))
        self.scope_lbl.setToolTip("\n".join(p.name for p in s.projects) if s.kind == "chain" else "")

    def _update_placeholder(self) -> None:
        n = self.model.total
        noun = self._kind_noun(n)
        text = ("Search %d %s" % (n, noun)) if n else "Search %s   (Ctrl+F)" % self._kind_noun()
        self.search.setPlaceholderText(text)
        # wide enough for the whole placeholder (the icon and the clear button too)
        want = self.search.fontMetrics().horizontalAdvance(text) + 64
        self.search.setMinimumWidth(max(200, min(self.search.maximumWidth(), want)))

    # ------------------------------------------------------------------ filter / sort / view
    def _apply_search(self) -> None:
        self.model.set_query(self.search.text(), regex=self.search_regex.on)
        self._maybe_prefetch()

    def _on_facets(self, *_a) -> None:
        self.model.set_facets(popups=self.cb_popups.currentData())

    def _toggle_unregistered(self) -> None:
        self.model.set_facets(hide_unregistered=not self.model.hide_unregistered)

    def set_hide_unregistered(self, hide: bool) -> None:
        self.model.set_facets(hide_unregistered=hide)

    def _on_sort(self, *_a) -> None:
        key = self.cb_sort.currentData() or "name"
        self.model.set_sort(key)
        self._sync_sort_indicator()
        self._maybe_prefetch()

    def _sync_sort_indicator(self) -> None:
        col = next((c for c, k in _COL_SORT.items() if k == self.model.sort_key), COL_NAME)
        desc = self.model.sort_desc != (self.model.sort_key == "modified")
        self.table.header().setSortIndicator(
            col, QtCore.Qt.DescendingOrder if desc else QtCore.Qt.AscendingOrder
        )

    def _on_header_clicked(self, col: int) -> None:
        key = _COL_SORT.get(col, "name")
        desc = self.model.sort_key == key and not self.model.sort_desc
        self.model.set_sort(key, desc)
        self._sync_sort_indicator()
        self._maybe_prefetch()
        i = self.cb_sort.findData(key)
        self.cb_sort.blockSignals(True)
        if i >= 0:
            self.cb_sort.setCurrentIndex(i)
        self.cb_sort.blockSignals(False)

    def _on_counts(self, shown: int, total: int, hidden_unreg: int) -> None:
        txt = "%d of %d shown" % (shown, total) if shown != total else "%d shown" % shown
        s = self._scope
        if total == 0 and self.is_picker() and (s is None or s.kind != "project"):
            txt = "No %s on this page's include path" % self._kind_noun()
        elif total == 0 and self.mode == MODE_NEW and s is not None:
            txt = "No %s in %s yet." % (self._kind_noun(), s.label)
        elif total == 0 and s is not None and s.kind == "project" and self._kind != "page":
            txt = (
                "No %s in %s. They usually come from its included projects: tick "
                "'With included projects'." % (self._kind_noun(), s.label)
            )
        elif shown == 0 and total and self.model.query:
            txt = "No %s match '%s'." % (self._kind_noun(), self.model.query)
        if self.model.fuzzy_used:
            txt += ". Close matches only (no exact match)."
        self.status.setText(txt)
        n_unreg = self.model.unregistered_total
        if self._kind == "page" and n_unreg:
            self.chip_unreg.setText(
                ("%d not registered hidden · Show" % hidden_unreg)
                if self.model.hide_unregistered
                else "%d not registered · Hide" % n_unreg
            )
            self.chip_unreg.show()
        else:
            self.chip_unreg.hide()

    def _maybe_prefetch(self) -> None:
        """Queue background metadata reads when something SHOWS them: the list
        view's template / size / object columns, a sort or a ``tpl:``/``res:``
        filter on them. The grid needs none (its cards load on paint)."""
        if self.view_mode() == "list" or self.model.needs_summaries():
            self.thumbs.prefetch(self.model.all_entries())

    def view_mode(self) -> str:
        return getattr(self, "_view_mode", "grid")

    def set_view(self, mode: str) -> None:
        """``'grid'`` (thumbnail cards) or ``'list'`` (details columns)."""
        lst = mode == "list"
        self._view_mode = "list" if lst else "grid"
        if not (self.mode == MODE_NEW and self._kind == "page"):
            self.stack.setCurrentWidget(self.table if lst else self.grid)
        self.btn_grid.setChecked(not lst)
        self.btn_list.setChecked(lst)
        self.slider.setEnabled(not lst)
        v = self.current_view()
        idx = v.currentIndex()
        if idx.isValid():
            v.scrollTo(idx)
        if lst:
            self._maybe_prefetch()

    def current_view(self) -> QtWidgets.QAbstractItemView:
        return self.table if self.view_mode() == "list" else self.grid

    def card_width(self) -> int:
        return self._card_w

    def set_card_width(self, px) -> None:
        """The grid's card width (the size slider / Ctrl+wheel)."""
        try:
            px = int(px)
        except TypeError, ValueError:
            px = CARD_SIZE.width()
        px = card_size(px).width()
        if px == self._card_w and self.slider.value() == px:
            return
        self._card_w = px
        self.model.card_width = px
        if self.slider.value() != px:
            self.slider.blockSignals(True)
            self.slider.setValue(px)
            self.slider.blockSignals(False)
        self.grid.setGridSize(cell_size(px))
        self.grid.doItemsLayout()
        self.grid.viewport().update()

    # ------------------------------------------------------------------ selection
    def _after_reset(self) -> None:
        # the model reset dropped the selection: restore it (or pick the first row)
        self._restore_selection()

    def _restore_selection(self) -> None:
        if self.model.rowCount() == 0:
            self.preview.show_entry(None)
            self._update_accept()
            return
        cur = self.grid.selectionModel().currentIndex()
        if cur.isValid() and not self._want_select:
            return
        row = self._start_element_row()
        if row >= 0:
            self._want_select = self._want_element = None
            self.select_row(row)
            return
        want = self._want_select or self._last_key
        if want:
            row = self.model.row_of(want)
            if row >= 0:
                self._want_select = None
            elif (
                want == self._want_select
                and self.model.hide_unregistered
                and any(
                    e.registered is False
                    and e.kind == "page"
                    and os.path.normcase(e.path) == os.path.normcase(want)
                    for e in self.model.all_entries()
                )
            ):
                # the page being edited is unregistered: show the unregistered
                # pages rather than silently selecting something else
                self.model.set_facets(hide_unregistered=False)
                return
            elif (
                want == self._want_select
                and self.model.popups != "any"
                and any(
                    e.is_popup == (self.model.popups == "hide")
                    and e.kind == "page"
                    and os.path.normcase(e.path) == os.path.normcase(want)
                    for e in self.model.all_entries()
                )
            ):
                # the page being edited is hidden by the system pages filter:
                # show every page for it
                self.cb_popups.setCurrentIndex(max(0, self.cb_popups.findData("any")))
                return
        if row < 0:
            row = 0
        self.select_row(self._not_avoided(row))

    def _not_avoided(self, row: int) -> int:
        """``row``, or its neighbour when it is the page the first selection avoids
        (``avoid_path``: once, the user may still pick that page)."""
        avoid, self._avoid_path = self._avoid_path, ""
        if not avoid:
            return row
        e = self.model.entry(row)
        if e is None or os.path.normcase(os.path.abspath(e.path)) != avoid:
            return row
        n = self.model.rowCount()
        if n <= 1:
            return row
        return row + 1 if row + 1 < n else row - 1

    def _start_element_row(self) -> int:
        """The row of ``start_element`` in the start library, -1 when not shown."""
        if not (self._want_element and self.start_path):
            return -1
        path = os.path.normcase(self.start_path)
        name = self._want_element.lower()
        for row in range(self.model.rowCount()):
            e = self.model.entry(row)
            if e is None or not e.is_element or e.name.lower() != name:
                continue
            if os.path.normcase(os.path.abspath(e.path)) == path:
                return row
        return -1

    def select_row(self, row: int) -> bool:
        if not (0 <= row < self.model.rowCount()):
            return False
        idx = self.model.index(row, 0)
        sm = self.grid.selectionModel()
        sm.setCurrentIndex(idx, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
        self.current_view().scrollTo(idx)
        return True

    def select_entry(self, entry_or_path) -> bool:
        """Select an Entry / page path / entry key if it is shown."""
        return self.select_row(self.model.row_of(entry_or_path))

    def select_name(self, name: str, library: str = "") -> bool:
        return self.select_row(self.model.find(name, library))

    def current_entry(self) -> Optional[Entry]:
        idx = self.grid.selectionModel().currentIndex()
        return self.model.entry(idx.row()) if idx.isValid() else None

    def _on_current(self, idx, _prev) -> None:
        e = self.model.entry(idx.row()) if idx.isValid() else None
        self._last_key = e.key if e is not None else None
        self.preview.show_entry(e)
        if self.mode == MODE_SAVE_AS and e is not None and not self._name_touched:
            self._fill_name_from(e)
        self._update_accept()

    def _fill_name_from(self, e: Entry) -> None:
        """Save As: an existing page suggests a FREE variant of its name
        (``Mixer`` -> ``Mixer_2``); typing the exact name offers Replace."""
        if self.ed_name is None or e.kind != "page":
            return
        # the card's project is where the page goes (the Active install row lists every
        # project)
        info = self._target_project()
        if e.project_dir and (info is None or os.path.normcase(os.path.abspath(e.project_dir)) != info.key):
            self._select_combo_dir(e.project_dir, add_info=self.catalog.project_for_dir(e.project_dir))
            info = self._target_project()
        existing = self.catalog.page_names(info) if info is not None else {}
        self.ed_name.blockSignals(True)
        self.ed_name.setText(naming.unique_page_name(e.name, existing))
        self.ed_name.blockSignals(False)
        self._validate()

    def _on_user_pick(self, idx) -> None:
        if self.mode == MODE_NEW and idx.isValid():
            self._new_on_pick(self.model.entry(idx.row()))
            return
        # a deliberate click re-suggests the name even after the user typed
        if self.mode == MODE_SAVE_AS and idx.isValid():
            e = self.model.entry(idx.row())
            if e is not None:
                self._name_touched = False
                self._fill_name_from(e)

    def _select_page_named(self, name: str) -> None:
        if self.select_name(name):
            return
        # the page may be filtered out (search / facets): clear and retry
        self.search.clear()
        self.search.flush()
        self.select_name(name)

    # ------------------------------------------------------------------ Save As
    def _combo_label(self, info: ProjectInfo) -> str:
        if info.is_folder:
            return "%s (folder)" % info.name
        if self.mode == MODE_NEW:
            # (one list for every kind: the counts are the tree's)
            return info.name if info.is_live else "%s (other install)" % info.name
        n = self.catalog.count(info, "page")
        extra = "" if info.is_live else ", other install"
        return "%s (%d page%s%s)" % (info.name, n, "" if n == 1 else "s", extra)

    def _fill_project_combo(self) -> None:
        """The target project and its includes first, a separator, then the
        other projects of the active install (natural order)."""
        from ...io.catalog import natural_key

        cb = self.cb_project
        cb.clear()
        seen = set()
        start_info = None
        if self.start_path:
            d = os.path.dirname(self.start_path)
            start_info = self.catalog.project_for_dir(d) or (
                self.catalog.folder(d) if os.path.isdir(d) else None
            )
        first: List[ProjectInfo] = []
        if start_info is not None:
            first.append(start_info)
            if not start_info.is_folder:
                first.extend(self.catalog.closure(start_info)[1:])
        for info in first:
            if info.key in seen or not info.exists:
                continue
            seen.add(info.key)
            self._add_combo_project(info)
        rest = [
            p
            for p in sorted(self.catalog.projects(), key=lambda p: natural_key(p.name))
            if p.key not in seen and p.exists
        ]
        if rest and cb.count():
            cb.insertSeparator(cb.count())
        for info in rest:
            seen.add(info.key)
            self._add_combo_project(info)

    def _add_combo_project(self, info: ProjectInfo) -> int:
        self.cb_project.addItem(icon("open" if info.is_folder else "page", 16), self._combo_label(info), info)
        i = self.cb_project.count() - 1
        self.cb_project.setItemData(i, info.dir, QtCore.Qt.ToolTipRole)
        return i

    def _select_combo_dir(self, directory: str, add_info: Optional[ProjectInfo] = None) -> None:
        if self.cb_project is None:
            return
        k = os.path.normcase(os.path.abspath(directory))
        for i in range(self.cb_project.count()):
            info = self.cb_project.itemData(i)
            if isinstance(info, ProjectInfo) and info.key == k:
                self.cb_project.setCurrentIndex(i)
                return
        if add_info is not None:
            self.cb_project.setCurrentIndex(self._add_combo_project(add_info))

    def _target_project(self) -> Optional[ProjectInfo]:
        if self.cb_project is None:
            return None
        info = self.cb_project.currentData()
        return info if isinstance(info, ProjectInfo) else None

    def target_dir(self) -> Optional[str]:
        info = self._target_project()
        return info.dir if info is not None else None

    def set_target_project(self, project) -> bool:
        """Save As: choose the target project (ProjectInfo / name / dir)."""
        info = (
            project
            if isinstance(project, ProjectInfo)
            else (
                self.catalog.project(project) or self.catalog.project_for_dir(project)
                if isinstance(project, str)
                else None
            )
        )
        if info is None:
            return False
        self._select_combo_dir(info.dir, add_info=info)
        return True

    def set_page_name(self, name: str) -> None:
        if self.ed_name is not None:
            self._name_touched = True
            self.ed_name.setText(name)

    def _on_name_edited(self, _t: str) -> None:
        self._name_touched = True

    def _template_warning(self, info: ProjectInfo) -> str:
        """Cross-project Save As: the page's template style must be reachable
        from the target project's chain, or the page shows a broken template."""
        if not self.start_path or info.is_folder:
            return ""
        src = self.catalog.project_for_path(self.start_path)
        if src is not None and src.key == info.key:
            return ""
        from ...format.pagesummary import read_page_summary

        t = read_page_summary(self.start_path, want_thumbnail=False).template
        if t is None:
            return ""
        stems = {
            lib.stem.lower()
            for p in self.catalog.closure(info)
            for lib in self.catalog.libraries(p, "template")
        }
        if t.library.lower() in stems:
            return ""
        return "Template library '%s' is not on the include chain of %s." % (t.library, info.name)

    def _pages_elsewhere(self, info: ProjectInfo) -> dict:
        """The page names of the projects compiled together with ``info``
        (cached per project while the dialog is open: validation runs per
        keystroke; and across dialogs while those projects' folders and pages.DBF
        tables are the same)."""
        cache = self.__dict__.setdefault("_elsewhere", {})
        if info.key in cache:
            return cache[info.key]
        cat = self.catalog

        def stamp(d: str) -> tuple:
            out = []
            for path in (d, os.path.join(d, "pages.dbf")):
                try:
                    st = os.stat(path)
                    out += [st.st_mtime_ns, st.st_size]
                except OSError:
                    out += [0, -1]
            return tuple(out)

        try:
            deps = cat.dependents(info)
            group = list(cat.closure(info)) + list(deps)
            for d in deps:
                if d.is_root:
                    group += cat.closure(d)
            stamps = tuple(sorted({(p.key,) + stamp(p.dir) for p in group}))
        except Exception:  # noqa: BLE001  # swallow-ok: no stamps, the names are read again
            stamps = None
        kept = _ELSEWHERE.setdefault(cat, {})
        hit = kept.get(info.key)
        if stamps is not None and hit is not None and hit[0] == stamps:
            cache[info.key] = hit[1]
            return hit[1]
        try:
            cache[info.key] = cat.pages_elsewhere(info)
        except Exception:  # noqa: BLE001 -- a hint only
            cache[info.key] = {}
        if stamps is not None:
            kept[info.key] = (stamps, cache[info.key])
        return cache[info.key]

    def _validate(self) -> Optional[naming.Verdict]:
        if self.mode != MODE_SAVE_AS or self.ed_name is None:
            return None
        info = self._target_project()
        if info is None:
            v = naming.Verdict(naming.ERROR, "Choose a project.")
        else:
            v = naming.validate_page_name(
                self.ed_name.text(),
                info.dir,
                existing=self.catalog.page_names(info),
                registered=self.catalog.registered_names(info),
                has_pages_dbf=info.has_pages_dbf,
                mode="save",
                elsewhere=self._pages_elsewhere(info),
            )
            if v.level != naming.ERROR:
                warn = self._template_warning(info)
                if warn:
                    v = naming.Verdict(
                        naming.WARN, (v.message + " " if v.level == naming.WARN else "") + warn, v.collision
                    )
        self._verdict = v
        col = {naming.OK: COLORS["text_dim"], naming.WARN: COLORS["warn"], naming.ERROR: COLORS["danger"]}[
            v.level
        ]
        self.msg.setText(v.message)
        self.msg.setStyleSheet("color:%s;" % col)
        self.ok_button.setText("Replace…" if v.collision else "Save")
        self._update_accept()
        return v

    def verdict(self) -> Optional[naming.Verdict]:
        return self._verdict if self._verdict is not None else self._validate()

    def _ask_replace(self, path: str) -> bool:
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Replace Page")
        box.setText(
            "'%s' already exists in %s. Replace it with this page?"
            % (os.path.splitext(os.path.basename(path))[0], os.path.dirname(path))
        )
        rep = box.addButton("Replace", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is rep

    # ------------------------------------------------------------------ accept
    def _on_chain(self, e: Entry) -> bool:
        """A picker's entry is one the page can place (on its chain, not hidden)."""
        if not self._chain_keys or e.kind == "page":
            return True
        key = os.path.normcase(os.path.abspath(e.project_dir)) if e.project_dir else ""
        return key in self._chain_keys and not e.shadowed_by

    def _update_accept(self) -> None:
        if self.mode == MODE_NEW:
            self.ok_button.setEnabled(self._new_ok)
            return
        if self.mode == MODE_SAVE_AS:
            v = self._verdict
            self.ok_button.setEnabled(v is not None and v.level != naming.ERROR)
            return
        e = self.current_entry()
        ok = e is not None
        if self.btn_delete is not None:
            self.btn_delete.setEnabled(ok)
        if ok and self.mode == MODE_PICK_TEMPLATE:
            ok = e.kind == "template" and self._on_chain(e)
        elif ok and self.mode == MODE_PICK_ELEMENT:
            ok = e.kind in self.kinds and e.is_element and self._on_chain(e)
        self.ok_button.setEnabled(ok)

    def accept_current(self) -> bool:
        """The Enter / double-click / OK path: build ``result_value`` + accept."""
        if self.mode == MODE_NEW:
            return self._new_accept()
        if self.mode == MODE_SAVE_AS:
            v = self._validate()
            info = self._target_project()
            if v is None or v.level == naming.ERROR or info is None:
                if self.ed_name is not None:
                    self.ed_name.setFocus()
                return False
            name = naming.normalise_page_name(self.ed_name.text())
            path = v.collision or os.path.join(info.dir, name + ".ctg")
            if v.collision and not self.confirm_replace(v.collision):
                return False
            self.result_value = BrowserResult("page", path, action="save")
            self.accept()
            return True
        e = self.current_entry()
        if e is None:
            return False
        if self.mode == MODE_PICK_TEMPLATE and (e.kind != "template" or not self._on_chain(e)):
            return False
        if self.mode == MODE_PICK_ELEMENT and not (
            e.is_element and e.kind in self.kinds and self._on_chain(e)
        ):
            return False
        action = (
            "open"
            if self.mode == MODE_OPEN
            else {
                "page": "open",
                "template": "use_template",
                "symbol": "place",
                "genie": "place",
                "supergenie": "place",
            }[e.kind]
        )
        self.result_value = BrowserResult(e.kind, e.path, e.name if e.is_element else "", action, e)
        self.accept()
        return True

    def _browse_files(self) -> None:
        start = (
            os.path.dirname(self.start_path)
            if self.start_path
            else (self._scope.projects[0].dir if self._scope and self._scope.projects else "")
        )
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Page", start, "Graphics pages (*.ctg);;All files (*.*)"
        )
        if path:
            self._open_other_path(path)

    def _browse_save(self) -> None:
        start = self.start_path or (self.target_dir() or "")
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Page As", start, "Graphics pages (*.ctg)"
        )
        if not path:
            return
        from ..messages import with_extension

        path = with_extension(self, "Save Page As", path, ".ctg")
        if not path:
            return
        self.result_value = BrowserResult("page", os.path.abspath(path), action="save")
        self.accept()

    def _open_other_path(self, path: str) -> None:
        if not path:
            return
        self.result_value = BrowserResult(
            "page", os.path.abspath(path), action="open", entry=self.catalog.entry_for_path(path)
        )
        self.accept()

    def done(self, r: int) -> None:  # noqa: D401 (Qt API)
        try:
            self._save_settings()
        except Exception:  # noqa: BLE001 -- settings must never block closing
            pass
        self.thumbs.cancel_pending()
        for sig, slot in (
            (self.thumbs.thumbnailReady, self.model._on_thumb),
            (self.thumbs.summaryReady, self.model._on_summary),
            (self.thumbs.thumbnailReady, self.preview._on_thumb),
        ):
            try:
                sig.disconnect(slot)
            except RuntimeError, TypeError:
                pass
        super().done(r)

    # ------------------------------------------------------------------ context menu
    def build_context_menu(self, e: Entry) -> QtWidgets.QMenu:
        """The grid / list menu of ``e``."""
        menu = QtWidgets.QMenu(self)
        if self.ok_button.isEnabled() and self.mode != MODE_SAVE_AS:
            a = menu.addAction(self.ok_button.text(), self.accept_current)
            f = a.font()
            f.setBold(True)
            a.setFont(f)
        if e.kind == "page":
            menu.addAction("Live Render", self.preview.live_render)
        if e.is_element:
            menu.addAction("Used By…", lambda: self.show_used_by(e))
        menu.addSeparator()
        clip = QtWidgets.QApplication.clipboard()
        menu.addAction("Copy Path", lambda: clip.setText(e.path))
        menu.addAction("Copy Name", lambda: clip.setText(e.name))
        if e.is_element:
            menu.addAction("Copy Reference (%s)" % e.reference, lambda: clip.setText(e.reference))
        menu.addAction("Show in Explorer", lambda: reveal_in_explorer(e.path))
        if self.mode == MODE_OPEN and not self._ok_text:
            menu.addSeparator()
            if e.is_element:
                menu.addAction("Rename…", self.rename_current)
            menu.addAction("Delete…", self.delete_current)
        return menu

    def _context_menu(self, view: QtWidgets.QAbstractItemView, pos: QtCore.QPoint) -> None:
        idx = view.indexAt(pos)
        if idx.isValid():
            self.select_row(idx.row())
        e = self.current_entry()
        if e is None:
            return
        self.build_context_menu(e).exec(view.viewport().mapToGlobal(pos))

    def show_used_by(self, e: Optional[Entry] = None):
        """The pages that place library element ``e`` (Open: its Open Page
        button opens one)."""
        from ..dialog.library_dialogs import UsedByDialog

        e = e or self.current_entry()
        if e is None or not e.is_element:
            return None
        try:
            usage = self.catalog.element_usage(e.path, e.name)
        except Exception:  # noqa: BLE001 -- best effort
            usage = []
        dlg = UsedByDialog(
            e.path, e.name, usage, self, on_open=self._open_other_path if self.mode == MODE_OPEN else None
        )
        self._used_by = dlg
        dlg.exec()
        return dlg

    # ------------------------------------------------------------------ delete / rename
    def delete_current(self) -> bool:
        """Delete the selected page (asks: page and files / project only) or
        library element (warns about the pages using it). True when done."""
        from .. import lifecycle

        e = self.current_entry()
        if e is None:
            return False
        if e.kind == "page":
            done = (
                lifecycle.delete_page(
                    self, e.path, before=self.before_delete, ask=self.ask_delete_page, catalog=self.catalog
                )
                is not None
            )
        else:
            done = lifecycle.delete_element(
                self,
                e.path,
                e.name,
                before=self.before_delete,
                ask=self.ask_delete_element,
                catalog=self.catalog,
            )
        if done:
            self.deleted.append(e.path if e.kind == "page" else "%s|%s" % (e.path, e.name))
            self._after_lifecycle(e)
        return done

    def rename_current(self) -> Optional[str]:
        """Rename the selected library element; the new name, or None."""
        from .. import lifecycle

        e = self.current_entry()
        if e is None or not e.is_element:
            return None
        new = lifecycle.rename_element(
            self, e.path, e.name, before=self.before_delete, ask=self.ask_element_name, catalog=self.catalog
        )
        if new:
            self.deleted.append("%s|%s" % (e.path, e.name))
            self._want_select = None
            self._after_lifecycle(e)
            self.select_name(new, e.library)
        return new

    def _after_lifecycle(self, e: Entry) -> None:
        self.catalog.touch(e.project_dir or os.path.dirname(os.path.abspath(e.path)))
        self._last_key = None
        self._reload()

    # ------------------------------------------------------------------ keyboard
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def fit_to_screen(self) -> None:
        """Keep the whole window on its screen: no larger than the screen's free area (the
        default 1280 x 780 is wider than a 1024 x 768 display, and a size restored from a
        larger monitor can be too), and moved inside it, so the action buttons are on it."""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry().size() - self.size()
        if frame.width() <= 0 or frame.height() <= 0:
            frame = QtCore.QSize(16, 39)  # (before the frame is known: a caption and borders)
        w = max(self.minimumWidth(), min(self.width(), avail.width() - frame.width()))
        h = max(self.minimumHeight(), min(self.height(), avail.height() - frame.height()))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        fw, fh = w + frame.width(), h + frame.height()
        pos = self.frameGeometry().topLeft()
        x = max(avail.left(), min(pos.x(), avail.right() + 1 - fw))
        y = max(avail.top(), min(pos.y(), avail.bottom() + 1 - fh))
        if (x, y) != (pos.x(), pos.y()):
            self.move(x, y)

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            self.fit_to_screen()
        if not getattr(self, "_splitter_restored", True):
            # first show without saved widths: the project list 300 px wide
            self._splitter_restored = True
            total = max(900, self.splitter.width())
            right = 340
            self.splitter.setSizes([300, max(300, total - 300 - right), right])

    def _focus_initial(self) -> None:
        if self.mode in (MODE_SAVE_AS, MODE_NEW) and self.ed_name is not None:
            self.ed_name.setFocus()
            self.ed_name.selectAll()
        else:
            self.current_view().setFocus()

    def _escape_step(self) -> bool:
        """Esc: clear the search, else go back to the starting scope; False
        when there is nothing left to undo (the dialog closes)."""
        if self.search.text():
            self.search.clear()
            self.search.flush()
            return True
        base = self.tree.current_base_scope()
        if (
            self._start_scope_id
            and (base is None or base.id != self._start_scope_id)
            and self.tree.select_scope_id(self._start_scope_id)
        ):
            return True
        return False

    def keyPressEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if ev.key() == QtCore.Qt.Key_Escape and not (ev.modifiers() & ~QtCore.Qt.KeypadModifier):
            if self._escape_step():
                ev.accept()
                return
        super().keyPressEvent(ev)

    def _append_to_search(self, text: str) -> None:
        self.search.setFocus()
        self.search.deselect()
        self.search.end(False)
        self.search.insert(text)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 (Qt API)
        t = ev.type()
        if t == QtCore.QEvent.Wheel and obj is self.grid.viewport():
            from .thumbs import card_wheel_delta

            d = card_wheel_delta(ev)
            if d is not None:
                if d:
                    self.set_card_width(self._card_w + d)
                ev.accept()
                return True
            return False
        if t != QtCore.QEvent.KeyPress or obj is self.grid.viewport():
            return super().eventFilter(obj, ev)
        key = ev.key()
        mods = ev.modifiers() & ~QtCore.Qt.KeypadModifier
        if obj is self.search:
            if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up, QtCore.Qt.Key_PageDown, QtCore.Qt.Key_PageUp):
                v = self.current_view()
                if not v.currentIndex().isValid() and self.model.rowCount():
                    self.select_row(0)
                else:
                    QtWidgets.QApplication.sendEvent(
                        v, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier)
                    )
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self.search.flush()
                if self.mode in (MODE_SAVE_AS, MODE_NEW) and self.ed_name is not None:
                    self.ed_name.setFocus()  # Enter in search never saves blindly
                    return True
                self.accept_current()
                return True
            if key == QtCore.Qt.Key_Escape and not mods:
                return self._escape_step()
            return super().eventFilter(obj, ev)
        # the grid / list views
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.accept_current()
            return True
        if key == QtCore.Qt.Key_Space and not mods:
            self.preview.live_render()
            return True
        if key == QtCore.Qt.Key_Delete and not mods and self.mode == MODE_OPEN and not self._ok_text:
            self.delete_current()
            return True
        if key == QtCore.Qt.Key_Escape and not mods:
            return self._escape_step()
        if key == QtCore.Qt.Key_Backspace and not mods:
            self.search.setFocus()
            self.search.deselect()
            self.search.end(False)
            self.search.backspace()
            return True
        text = ev.text()
        if (
            text
            and text.isprintable()
            and not text.isspace()
            and not (mods & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier))
        ):
            self._append_to_search(text)
            return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ public API
    @staticmethod
    def _run(dlg: "PageBrowserDialog") -> int:
        """Show ``dlg`` modally (a seam: tests drive the dialog instead)."""
        return dlg.exec()

    @classmethod
    def _finish(cls, dlg: "PageBrowserDialog") -> Optional[BrowserResult]:
        try:
            code = PageBrowserDialog._run(dlg)
            return dlg.result_value if code == QtWidgets.QDialog.Accepted else None
        finally:
            dlg.deleteLater()

    @staticmethod
    def get_open_target(
        parent: Optional[QtWidgets.QWidget] = None,
        start_path: Optional[str] = None,
        catalog: Optional[ProjectCatalog] = None,
        before_delete=None,
        start_element: Optional[str] = None,
    ) -> Optional[BrowserResult]:
        """Open anything: a page, template, symbol, genie or super genie."""
        dlg = PageBrowserDialog(
            MODE_OPEN, parent, catalog=catalog, start_path=start_path, start_element=start_element
        )
        dlg.before_delete = before_delete
        return PageBrowserDialog._finish(dlg)

    @staticmethod
    def get_open_path(
        parent: Optional[QtWidgets.QWidget] = None,
        start_path: Optional[str] = None,
        catalog: Optional[ProjectCatalog] = None,
        title: Optional[str] = None,
        ok_text: Optional[str] = None,
        avoid_path: Optional[str] = None,
    ) -> Optional[str]:
        """Pick a page ``.ctg`` to open (Pages only); ``None`` on cancel. ``title`` /
        ``ok_text``: the window's title and its default button (a picker: no Delete);
        ``avoid_path``: a page not selected first (it can still be picked)."""
        dlg = PageBrowserDialog(
            MODE_OPEN,
            parent,
            catalog=catalog,
            start_path=start_path,
            kinds=("page",),
            title=title,
            ok_text=ok_text,
            avoid_path=avoid_path,
        )
        r = PageBrowserDialog._finish(dlg)
        return r.path if r is not None and r.kind == "page" else None

    @staticmethod
    def get_template(
        parent: Optional[QtWidgets.QWidget] = None,
        project_dir: Optional[str] = None,
        catalog: Optional[ProjectCatalog] = None,
    ) -> Optional[Tuple[str, str]]:
        """Pick a page template: ``(ctt_path, element_name)`` or ``None``.
        Lists the templates ``project_dir``'s include chain can use."""
        dlg = PageBrowserDialog(
            MODE_PICK_TEMPLATE, parent, catalog=catalog, project_dir=project_dir, kinds=("template",)
        )
        r = PageBrowserDialog._finish(dlg)
        return (r.path, r.element) if r is not None else None

    @staticmethod
    def get_new(
        parent: Optional[QtWidgets.QWidget] = None,
        start_path: Optional[str] = None,
        kind: Optional[str] = None,
        catalog: Optional[ProjectCatalog] = None,
    ):
        """File > New: what to create, its project, library and name (browser/new_mode.py).
        ``kind`` = the tab to start on (default: the one used last). The
        :class:`~.new_mode.NewTarget`, or ``None``."""
        dlg = PageBrowserDialog(MODE_NEW, parent, catalog=catalog, start_path=start_path)
        if kind:
            dlg.set_kind(kind)
        r = PageBrowserDialog._finish_new(dlg)
        return r

    @classmethod
    def _finish_new(cls, dlg: "PageBrowserDialog"):
        try:
            code = PageBrowserDialog._run(dlg)
            return dlg.new_target if code == QtWidgets.QDialog.Accepted else None
        finally:
            dlg.deleteLater()

    @staticmethod
    def get_save_path(
        parent: Optional[QtWidgets.QWidget] = None,
        start_path: Optional[str] = None,
        catalog: Optional[ProjectCatalog] = None,
    ) -> Optional[str]:
        """Save As: pick the target project + type a new page name (checked
        as you type; replacing an existing page asks first). The ``.ctg``
        path, or ``None``."""
        dlg = PageBrowserDialog(MODE_SAVE_AS, parent, catalog=catalog, start_path=start_path, kinds=("page",))
        r = PageBrowserDialog._finish(dlg)
        return r.path if r is not None else None

    @staticmethod
    def get_library_element(
        parent: Optional[QtWidgets.QWidget] = None,
        project_dir: Optional[str] = None,
        kind: str = "symbol",
        catalog: Optional[ProjectCatalog] = None,
        title: Optional[str] = None,
        super_genies: Optional[bool] = None,
        linked_option: bool = False,
    ) -> Optional[Tuple[str, str]]:
        """Pick a symbol or genie from the project's include chain."""
        if kind not in ("symbol", "genie", "template", "supergenie"):
            raise ValueError("kind must be 'symbol', 'genie', 'supergenie' or 'template', not %r" % (kind,))
        flt = None
        if kind == "genie" and super_genies is not None:
            want = bool(super_genies)
            flt = lambda e: is_super_genie(e.name) == want  # noqa: E731
        dlg = PageBrowserDialog(
            MODE_PICK_ELEMENT,
            parent,
            catalog=catalog,
            project_dir=project_dir,
            kinds=(kind,),
            title=title,
            element_filter=flt,
            super_genies=super_genies if kind == "genie" else None,
        )
        if linked_option and kind == "symbol":
            dlg.add_linked_option()
        r = PageBrowserDialog._finish(dlg)
        return (r.path, r.element) if r is not None else None


#: catalog -> {project key: (the stamps of the projects compiled with it, their page names)}
#: (PageBrowserDialog._pages_elsewhere)
_ELSEWHERE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
from ...infra import caches as _caches  # noqa: E402

_caches.register("browser.pages_elsewhere", _ELSEWHERE, on=("refresh", "tests"))
