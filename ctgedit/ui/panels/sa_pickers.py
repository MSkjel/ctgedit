"""Pickers for equipment, menu items and SA genie parameters."""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..dialog.genie_dialogs import _BrowseField

HELP_EQUIPMENT = "situational-awareness#choose-equipment"
HELP_MENU = "situational-awareness#choose-a-menu-item"
#: the most matches a search in the equipment tree lists
SEARCH_LIMIT = 500

_FULL_ROLE = QtCore.Qt.UserRole + 1
_ROW_ROLE = QtCore.Qt.UserRole + 2

#: parameter-name endings that are NOT equipment names (equip9Colour,
#: equip8Text, EquipItem, eqPartItemName, EquipTooltip9, EquipHeader9 ...)
_NOT_EQUIPMENT = (
    "colour",
    "color",
    "text",
    "tooltip",
    "header",
    "item",
    "itemname",
    "assocs",
    "type",
    "desc",
    "description",
    "label",
)


def is_equipment_param(name: str) -> bool:
    """A genie parameter whose value is an equipment name: 'Equipment',
    'EquipmentFrom', 'Equip', 'equip1', 'eqFeedCurve', 'AreaEquipment' --
    not 'EquipRefAssocs' (categories), 'EquipItem' / 'eqItem' (an item),
    'equip9Colour' / 'equip8Text' (display settings) or 'Equip.item'."""
    low = (name or "").strip().lower().replace(" ", "")
    if not low or "." in low:
        return False
    if not (low.startswith("eq") or "equipment" in low):
        return False
    stem = low.rstrip("0123456789")  # EquipTooltip9, EquipHeader8
    return not any(stem.endswith(s) for s in _NOT_EQUIPMENT)


def _dim() -> QtGui.QColor:
    try:
        from ..style.theme import COLORS

        return QtGui.QColor(COLORS.get("text_dim", "#969ca5"))
    except Exception:  # noqa: BLE001
        return QtGui.QColor(139, 145, 154)


def scope_equipment(project_dir: str) -> tuple:
    """The equipment rows of the page folder's tag scope (io.project.equipment)."""
    if not project_dir:
        return ()
    try:
        from ...io.project import equipment

        return equipment(project_dir)
    except Exception:  # noqa: BLE001 -- the list is an aid only
        return ()


def _natural_key(text: str) -> tuple:
    """'Pump10' after 'Pump9', case-insensitive."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text.lower()))


def _sorted_tree(nodes: list) -> list:
    """``nodes`` and every level below them sorted by name (natural order): the
    equipment of several projects comes in their table order otherwise."""
    nodes.sort(key=lambda n: _natural_key(n.name))
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if n.children:
            n.children.sort(key=lambda c: _natural_key(c.name))
            stack.extend(n.children)
    return nodes


def _scope_name(project_dir: str) -> str:
    return os.path.basename(os.path.normpath(project_dir)) if project_dir else "this project"


# ----------------------------------------------------------------- equipment
class EquipmentTreeModel(QtCore.QAbstractItemModel):
    """The equipment hierarchy (io.project.EquipmentNode) as a tree. A level's
    rows are made when it is expanded (fetchMore); with :meth:`set_visible` only
    the given nodes (search matches and their parents) are shown."""

    HEADERS = ("Equipment", "Type", "Comment", "Page", "Project")

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self._roots = list(roots)
        self._visible: Optional[set] = None
        self._kids: Dict[int, list] = {}
        self._fetched: set = set()
        self._pos: Dict[int, int] = {}

    # -- structure --------------------------------------------------------------------
    def _children(self, node) -> list:
        key = id(node) if node is not None else 0
        kids = self._kids.get(key)
        if kids is None:
            src = self._roots if node is None else node.children
            vis = self._visible
            kids = [c for c in src if vis is None or id(c) in vis]
            self._kids[key] = kids
            for k, c in enumerate(kids):
                self._pos[id(c)] = k
        return kids

    def node(self, index) -> Optional[object]:
        return index.internalPointer() if index.isValid() else None

    def set_roots(self, roots) -> None:
        self.beginResetModel()
        self._roots = list(roots)
        self._visible = None
        self._kids.clear()
        self._pos.clear()
        self._fetched.clear()
        self.endResetModel()

    def set_visible(self, visible: Optional[set]) -> None:
        """Show only the nodes whose id is in ``visible`` (None = all)."""
        self.beginResetModel()
        self._visible = visible
        self._kids.clear()
        self._pos.clear()
        self._fetched.clear()
        self.endResetModel()

    def index(self, row, column, parent=QtCore.QModelIndex()):
        node = self.node(parent)
        kids = self._children(node)
        if 0 <= row < len(kids) and 0 <= column < len(self.HEADERS):
            return self.createIndex(row, column, kids[row])
        return QtCore.QModelIndex()

    def parent(self, index=QtCore.QModelIndex()):  # noqa: D102 -- Qt
        node = self.node(index)
        par = getattr(node, "parent", None) if node is not None else None
        if par is None:
            return QtCore.QModelIndex()
        grand = getattr(par, "parent", None)
        self._children(grand)
        row = self._pos.get(id(par))
        if row is None:
            return QtCore.QModelIndex()
        return self.createIndex(row, 0, par)

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        if parent.column() > 0:
            return 0
        node = self.node(parent)
        if node is not None and id(node) not in self._fetched and self._visible is None:
            return 0
        return len(self._children(node))

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def hasChildren(self, parent=QtCore.QModelIndex()) -> bool:  # noqa: N802
        node = self.node(parent)
        if node is None:
            return bool(self._roots)
        return bool(self._children(node))

    def canFetchMore(self, parent) -> bool:  # noqa: N802
        node = self.node(parent)
        return (
            node is not None
            and self._visible is None
            and id(node) not in self._fetched
            and bool(node.children)
        )

    def fetchMore(self, parent) -> None:  # noqa: N802
        node = self.node(parent)
        if node is None or id(node) in self._fetched:
            return
        kids = self._children(node)
        self.beginInsertRows(parent, 0, len(kids) - 1)
        self._fetched.add(id(node))
        self.endInsertRows()

    def fetch_path(self, node) -> QtCore.QModelIndex:
        """The index of ``node``, its parents fetched."""
        chain = []
        n = node
        while n is not None:
            chain.append(n)
            n = getattr(n, "parent", None)
        idx = QtCore.QModelIndex()
        for n in reversed(chain):
            if idx.isValid() and self.canFetchMore(idx):
                self.fetchMore(idx)
            kids = self._children(self.node(idx))
            try:
                row = kids.index(n)
            except ValueError:
                return QtCore.QModelIndex()
            idx = self.index(row, 0, idx)
        return idx

    # -- cells ---------------------------------------------------------------------------
    def data(self, index, role=QtCore.Qt.DisplayRole):
        node = self.node(index)
        if node is None:
            return None
        r = node.row or {}
        col = index.column()
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            # every row shows its WHOLE dotted name, not only its last part: the tree
            # groups the equipment, the name tells what the parameter will get
            return (
                node.full,
                r.get("TYPE", ""),
                r.get("COMMENT", ""),
                r.get("PAGE", ""),
                r.get("PROJECT", ""),
            )[col]
        if role == _FULL_ROLE:
            return node.full
        if role == _ROW_ROLE:
            return node.row
        if role == QtCore.Qt.ForegroundRole and (node.row is None or col == 4):
            return _dim()
        return None

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):  # noqa: N802
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.HEADERS[section]
        return None


class EquipmentBrowserDialog(QtWidgets.QDialog):
    """Select Equipment: the hierarchy of the page's tag scope
    (io.project.equipment + equipment_hierarchy). A level no equipment row
    defines is shown dimmed and can be chosen too."""

    TITLE = "Select Equipment"

    def __init__(self, project_dir: str, current: str = "", parent=None, rows=None):
        super().__init__(parent)
        from .tags_dock import ALL_PROJECTS
        from ..style.theme import SearchField
        from ...io.project import equipment_hierarchy

        self.setWindowTitle(self.TITLE)
        self.resize(900, 560)
        self._project_dir = project_dir
        self._result: Optional[str] = None
        self._hierarchy = equipment_hierarchy
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search = SearchField(
            "Search equipment", tip="Searches the equipment names, types, comments and pages"
        )
        top.addWidget(self.search, 1)
        self.project = QtWidgets.QComboBox()
        self.project.setToolTip("Show the equipment of one project")
        top.addWidget(self.project)
        lay.addLayout(top)
        self.rows = list(rows) if rows is not None else list(scope_equipment(project_dir))
        self.model = EquipmentTreeModel(self._roots_for(None), self)
        self.view = QtWidgets.QTreeView()
        self.view.setModel(self.model)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        from ..tables import make_resizable

        # the Equipment column (the one chosen from) keeps its whole dotted names, the
        # deeper levels' too as they are opened (grow): the others fit their text up to a
        # share of the view each, and it takes the rest
        make_resizable(
            self.view,
            "%s/equipment" % type(self).__name__,
            stretch=0,
            caps={0: 0.6, 1: 0.12, 2: 0.25, 3: 0.18, 4: 0.12},
            grow=True,
        )
        lay.addWidget(self.view, 1)
        self.search.attach(self.view, self)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail.setMinimumHeight(34)
        lay.addWidget(self.detail)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.select_btn = self.buttons.addButton("Select", QtWidgets.QDialogButtonBox.AcceptRole)
        self.select_btn.setDefault(True)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        counts: Dict[str, int] = {}
        for r in self.rows:
            p = r.get("PROJECT", "") or ""
            counts[p] = counts.get(p, 0) + 1
        self.project.addItem(ALL_PROJECTS, None)
        for p, n in counts.items():
            if p:
                self.project.addItem("%s (%d)" % (p, n), p)
        self.project.setVisible(self.project.count() > 2)
        self._flat: Optional[list] = None
        self.search.searchChanged.connect(lambda _t: self._apply_filter())
        self.project.currentIndexChanged.connect(lambda _i: self._project_changed())
        self.view.doubleClicked.connect(lambda _i: self._accept())
        self.view.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))
        if not self.rows:
            self.detail.setText(
                "No equipment in %s or the projects it compiles with." % _scope_name(project_dir)
            )
        self.select(current)
        self._update_ok()
        self.search.setFocus()

    # -- the tree -----------------------------------------------------------------------
    def _roots_for(self, project: Optional[str]):
        rows = self.rows if not project else [r for r in self.rows if (r.get("PROJECT") or "") == project]
        self._roots = _sorted_tree(self._hierarchy(rows))
        return self._roots

    def _project_changed(self) -> None:
        self.model.set_roots(self._roots_for(self.project.currentData()))
        self._flat = None
        self._apply_filter()

    def _flat_list(self) -> list:
        """(lower-case haystack, node) of every node, in tree order (built once)."""
        if self._flat is None:
            out = []
            stack = list(reversed(self._roots))
            while stack:
                n = stack.pop()
                r = n.row or {}
                out.append(
                    (
                        " ".join(
                            (
                                n.full,
                                r.get("TYPE", "") or "",
                                r.get("COMMENT", "") or "",
                                r.get("PAGE", "") or "",
                            )
                        ).lower(),
                        n,
                    )
                )
                stack.extend(reversed(n.children))
            self._flat = out
        return self._flat

    def select(self, full: str) -> bool:
        """Show and select equipment ``full`` (case-insensitive); False when it
        is not in the list."""
        want = (full or "").strip().lower()
        if not want:
            return False
        for _hay, n in self._flat_list():
            if n.full.lower() == want:
                if self.model._visible is not None and id(n) not in self.model._visible:
                    return False
                idx = self.model.fetch_path(n)
                if not idx.isValid():
                    return False
                self.view.scrollTo(idx)
                self.view.setCurrentIndex(idx)
                return True
        return False

    def _apply_filter(self) -> None:
        m = self.search.matcher()
        if m.empty:
            self.model.set_visible(None)
            self.detail.setText("" if self.rows else self.detail.text())
            self._update_ok()
            return
        hits = []
        for hay, n in self._flat_list():
            if m(hay):
                hits.append(n)
                if len(hits) >= SEARCH_LIMIT:
                    break
        visible = set()
        for n in hits:
            p = n
            while p is not None and id(p) not in visible:
                visible.add(id(p))
                p = getattr(p, "parent", None)
        self.model.set_visible(visible)
        self.view.expandAll()
        own = next((n for n in hits if n.row is not None), hits[0] if hits else None)
        if own is not None:
            idx = self.model.fetch_path(own)
            self.view.setCurrentIndex(idx)
            self.view.scrollTo(idx)
        if len(hits) >= SEARCH_LIMIT:
            self.detail.setText("Showing the first %d matches." % SEARCH_LIMIT)
        elif not hits:
            self.detail.setText(
                "Not a regular expression: %s" % m.error
                if m.error
                else "Nothing matches '%s'." % self.search.text().strip()
            )
        self._update_ok()

    def select_text(self, text: str) -> bool:
        """Search for ``text`` and select the first match (tests / scripting)."""
        self.search.setText(text)
        self._apply_filter()
        return self.view.currentIndex().isValid()

    def _show(self, idx) -> None:
        self._update_ok()
        if not idx.isValid():
            return
        full = idx.data(_FULL_ROLE) or ""
        row = self.model.index(idx.row(), 0, idx.parent()).data(_ROW_ROLE)
        if row is None:
            self.detail.setText("%s: a level of the hierarchy, not equipment." % full)
            return
        bits = ["Equipment %s" % full]
        for key, label in (
            ("TYPE", "type"),
            ("CLUSTER", "cluster"),
            ("PAGE", "page"),
            ("CONTENT", "content"),
            ("PROJECT", "project"),
        ):
            if row.get(key):
                bits.append("%s %s" % (label, row[key]))
        text = ", ".join(bits) + "."
        if row.get("COMMENT"):
            text += " " + row["COMMENT"]
        self.detail.setText(text)

    def _update_ok(self) -> None:
        self.select_btn.setEnabled(self.view.currentIndex().isValid())

    def chosen(self) -> Optional[str]:
        idx = self.view.currentIndex()
        return idx.data(_FULL_ROLE) if idx.isValid() else None

    def _accept(self) -> None:
        full = self.chosen()
        if full:
            self._result = full
            self.accept()

    @classmethod
    def pick(cls, parent, project_dir: str, current: str = "") -> Optional[str]:
        dlg = cls(project_dir, current, parent)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg._result


class EquipmentPickerDialog(EquipmentBrowserDialog):
    """Select Equipment for an alarm indicator (the same tree): built from the
    sheet's expression context."""

    def __init__(self, parent=None, context=None, current: str = "", rows=None):
        pdir = getattr(context, "project_dir", None) or ""
        super().__init__(pdir, current, parent, rows=rows)

    @classmethod
    def pick(cls, parent, context=None, current: str = "") -> Optional[str]:  # noqa: D102
        dlg = cls(parent, context, current)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg._result


_EQUIP_MODELS: dict = {}
#: id(the scope's equipment rows) -> (rows, their names): what a warm-up read
_EQUIP_NAMES: dict = {}
#: folder -> the thread reading its scope's equipment (warm_equipment)
_EQUIP_WARMING: dict = {}


def _equipment_names(rows) -> list:
    hit = _EQUIP_NAMES.get(id(rows))
    if hit is not None and hit[0] is rows:
        return hit[1]
    return [r.get("NAME", "") for r in rows if r.get("NAME")]


def warm_equipment(project_dir: str) -> None:
    """Read the folder's equipment on a worker thread, once per folder."""
    if not project_dir:
        return
    key = os.path.normcase(os.path.abspath(project_dir))
    if key in _EQUIP_WARMING:
        return

    def work():
        try:
            rows = scope_equipment(project_dir)
            if rows:
                names = [r.get("NAME", "") for r in rows if r.get("NAME")]
                if len(_EQUIP_NAMES) > 8:
                    _EQUIP_NAMES.clear()
                _EQUIP_NAMES[id(rows)] = (rows, names)
        except Exception:  # noqa: BLE001 -- a warm-up only: the field reads them itself
            from ...infra.logsetup import note_exception

            note_exception("sa_pickers.warm_equipment")

    import threading
    from ...infra import gcguard

    gcguard.before_worker()  # only the GUI thread may collect garbage
    t = threading.Thread(target=work, name="equipment", daemon=True)
    _EQUIP_WARMING[key] = t
    t.start()


def equipment_completion_model(project_dir: str) -> Optional[QtCore.QStringListModel]:
    """The equipment names of the scope as one shared model (per scope)."""
    t = _EQUIP_WARMING.get(os.path.normcase(os.path.abspath(project_dir))) if project_dir else None
    if t is not None and t.is_alive():
        t.join()  # (the warm-up has read part of it: wait, rather than read again)
    rows = scope_equipment(project_dir)
    if not rows:
        return None
    key = id(rows)
    hit = _EQUIP_MODELS.get(key)
    if hit is None or hit[0] is not rows:
        names = _equipment_names(rows)
        if len(_EQUIP_MODELS) > 8:
            _EQUIP_MODELS.clear()
        hit = _EQUIP_MODELS[key] = (rows, QtCore.QStringListModel(names))
    return hit[1]


class EquipmentField(_BrowseField):
    """An equipment name: a text box that completes from the scope's
    equipment and a 'Browse…' button that opens Select Equipment."""

    textChanged = QtCore.Signal(str)

    def __init__(
        self,
        value: str = "",
        project_dir: str = "",
        parent=None,
        browse: Optional[Callable[[str], Optional[str]]] = None,
        tip: str = "",
        rows=None,
    ):
        self._project_dir = project_dir or ""
        self._rows = rows
        super().__init__(
            value, browse or self._browse_tree, tip or "Select the equipment from the list", parent
        )
        self.edit.setPlaceholderText("")
        self.edit.textChanged.connect(self.textChanged.emit)
        self.edit.setAccessibleName("Equipment")
        model = equipment_completion_model(self._project_dir) if self._project_dir else None
        if model is not None:
            c = QtWidgets.QCompleter(model, self.edit)
            c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            c.setFilterMode(QtCore.Qt.MatchContains)
            c.setMaxVisibleItems(14)
            self.edit.setCompleter(c)

    def _browse_tree(self, current: str) -> Optional[str]:
        dlg = EquipmentBrowserDialog(self._project_dir, current, self.window(), rows=self._rows)
        return dlg._result if dlg.exec() == QtWidgets.QDialog.Accepted else None

    def setCompleter(self, c) -> None:  # noqa: N802
        """(keeps the equipment completion)"""

    def lineEdit(self) -> QtWidgets.QLineEdit:  # noqa: N802
        return self.edit

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.edit.setPlaceholderText(text)

    def setReadOnly(self, ro: bool) -> None:  # noqa: N802
        self.edit.setReadOnly(ro)
        self.button.setEnabled(not ro)

    def selectAll(self) -> None:  # noqa: N802
        self.edit.selectAll()


# -------------------------------------------------------------------- menus
class MenuItemPickerDialog(QtWidgets.QDialog):
    """Select Menu Item: an item of the menu configuration (pagemenu.dbf of the
    page's tag scope), its path from Level1, as a navigation link's Menu Item
    takes it."""

    TITLE = "Select Menu Item"
    HEADERS = ("Menu item", "Target page", "Command", "Project")

    def __init__(self, project_dir: str, current: str = "", parent=None, menu: str = "", items=None):
        super().__init__(parent)
        from ...io import project as PR
        from ..style.theme import SearchField

        self.setWindowTitle(self.TITLE)
        self.resize(700, 500)
        self._project_dir = project_dir
        self._result: Optional[str] = None
        self._given = items
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("Menu:")
        top.addWidget(lab)
        self.menu = QtWidgets.QComboBox()
        lab.setBuddy(self.menu)
        pages = (
            PR.menu_pages(project_dir) if items is None else sorted({it.page for it in items}, key=str.lower)
        )
        for pg in pages:
            self.menu.addItem(pg)
        want = menu or PR.NAVIGATION_MENU
        k = next((i for i, pg in enumerate(pages) if pg.lower() == want.lower()), -1)
        if k >= 0:
            self.menu.setCurrentIndex(k)
        top.addWidget(self.menu, 1)
        self.search = SearchField("Search menu items")
        top.addWidget(self.search, 2)
        lay.addLayout(top)
        self.model = QtGui.QStandardItemModel(0, len(self.HEADERS), self)
        self.model.setHorizontalHeaderLabels(list(self.HEADERS))
        self.proxy = QtCore.QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterRole(QtCore.Qt.UserRole + 3)
        self.view = QtWidgets.QTreeView()
        self.view.setModel(self.proxy)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        from ..tables import make_resizable

        # (the Menu item column takes what the others leave, never less than its text)
        make_resizable(
            self.view, "MenuItemPickerDialog/items", stretch=0, caps={0: 0.6, 1: 0.25, 2: 0.25, 3: 0.12}
        )
        lay.addWidget(self.view, 1)
        self.search.attach(self.view, self)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail.setMinimumHeight(34)
        lay.addWidget(self.detail)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.select_btn = self.buttons.addButton("Select", QtWidgets.QDialogButtonBox.AcceptRole)
        self.select_btn.setDefault(True)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._nodes: Dict[str, QtGui.QStandardItem] = {}
        self.menu.currentIndexChanged.connect(lambda _i: self._fill())
        self.search.searchChanged.connect(self._filter)
        self.view.doubleClicked.connect(lambda _i: self._accept())
        self.view.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))
        self._fill()
        self.select(current)
        self.search.setFocus()

    def _items_of(self, page: str):
        from ...io import project as PR

        if self._given is not None:
            return [it for it in self._given if it.page.lower() == page.lower()]
        return PR.menu_items(self._project_dir, page)

    def _fill(self) -> None:
        self.model.removeRows(0, self.model.rowCount())
        self._nodes = {}
        page = self.menu.currentText()
        items = self._items_of(page) if page else []
        root = self.model.invisibleRootItem()
        for it in items:
            parent = root
            for n in range(1, len(it.levels) + 1):
                path = ".".join(it.levels[:n])
                node = self._nodes.get(path.lower())
                if node is None:
                    cells = [QtGui.QStandardItem(it.levels[n - 1])] + [
                        QtGui.QStandardItem("") for _ in self.HEADERS[1:]
                    ]
                    for c in cells:
                        c.setEditable(False)
                        c.setData(path, _FULL_ROLE)
                        c.setData(path.lower(), QtCore.Qt.UserRole + 3)
                    cells[0].setToolTip(path)
                    parent.appendRow(cells)
                    node = cells[0]
                    self._nodes[path.lower()] = node
                if n == len(it.levels):
                    row = node.row()
                    par = node.parent() or root
                    for col, text in ((1, it.target_page), (2, it.command), (3, it.project)):
                        cell = par.child(row, col)
                        if cell is not None and text:
                            cell.setText(text)
                            cell.setData(
                                " ".join((path, it.target_page, it.command)).lower(), QtCore.Qt.UserRole + 3
                            )
                    node.setData(it, _ROW_ROLE)
                    node.setData(
                        " ".join((path, it.target_page, it.command, it.comment)).lower(),
                        QtCore.Qt.UserRole + 3,
                    )
                parent = node
        self.view.expandToDepth(0)
        if not items:
            self.detail.setText(
                "The menu configuration has no '%s' menu items (Plant SCADA "
                "Studio > Visualization > Menu Configuration)." % (page or "")
            )
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        m = self.search.matcher(text)
        if m.regex and not m.empty:
            # (the box's rule: a regular expression, without case; one that does not compile
            # matches nothing)
            self.proxy.setFilterRegularExpression(
                QtCore.QRegularExpression(
                    m.text if m.ok else "(?!)", QtCore.QRegularExpression.CaseInsensitiveOption
                )
            )
        else:
            self.proxy.setFilterFixedString(text.strip())
        if text.strip():
            self.view.expandAll()
        self.select_btn.setEnabled(self.view.currentIndex().isValid())

    def select(self, path: str) -> bool:
        node = self._nodes.get((path or "").strip().lower())
        if node is None:
            return False
        idx = self.proxy.mapFromSource(node.index())
        if not idx.isValid():
            return False
        self.view.scrollTo(idx)
        self.view.setCurrentIndex(idx)
        return True

    def _show(self, idx) -> None:
        self.select_btn.setEnabled(idx.isValid())
        if not idx.isValid():
            return
        path = idx.data(_FULL_ROLE) or ""
        it = self.proxy.index(idx.row(), 0, idx.parent()).data(_ROW_ROLE)
        if it is None:
            self.detail.setText("%s: a menu level, not a menu item." % path)
            return
        bits = ["%s in menu %s" % (path, it.page)]
        if it.target_page:
            bits.append("target page %s" % it.target_page)
        if it.command:
            bits.append("command %s" % it.command)
        text = ", ".join(bits) + "."
        if it.comment:
            text += " " + it.comment
        self.detail.setText(text)

    def chosen(self) -> Optional[str]:
        idx = self.view.currentIndex()
        return idx.data(_FULL_ROLE) if idx.isValid() else None

    def _accept(self) -> None:
        path = self.chosen()
        if path:
            self._result = path
            self.accept()

    @classmethod
    def pick(cls, parent, project_dir: str, current: str = "", menu: str = "") -> Optional[str]:
        dlg = cls(project_dir, current, parent, menu=menu)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg._result


# ------------------------------------------------------ parameter -> picker


class PickerSpec:
    """How one genie parameter is edited: 'combo', 'multi' or 'browse'."""

    def __init__(
        self,
        kind: str,
        items: Sequence[str] = (),
        browse: Optional[Callable] = None,
        tip: str = "",
        factory: Optional[Callable] = None,
    ):
        self.kind = kind
        self.items = list(items)
        self.browse = browse
        self.tip = tip
        self.factory = factory


class ParamPickers:
    """Which picker each parameter of a genie dialog gets."""

    def __init__(self, project_dir: str, panes=(), own: Optional[int] = None):
        self.project_dir = project_dir
        self.panes = [p for p in panes if own is None or p.root != own]
        self._menus: Optional[List[str]] = None

    def menus(self) -> List[str]:
        if self._menus is None:
            try:
                from ...io.project import menu_pages

                self._menus = list(menu_pages(self.project_dir))
            except Exception:  # noqa: BLE001
                self._menus = []
        return self._menus

    def menu_items(self) -> list:
        """The menu items of the scope (io.project.menu_items)."""
        try:
            from ...io.project import menu_items

            return list(menu_items(self.project_dir))
        except Exception:  # noqa: BLE001
            return []

    def reference_categories(self) -> List[str]:
        """The equipment reference categories of the scope."""
        try:
            from ...io.project import equipment_reference_categories

            return list(equipment_reference_categories(self.project_dir))
        except Exception:  # noqa: BLE001
            return []

    def equipment_rows(self) -> list:
        """The equipment of the scope, first definition of a name wins."""
        return list(scope_equipment(self.project_dir))

    def pane_names(self) -> List[str]:
        return [p.name for p in self.panes if p.name]

    def for_param(self, name: str, value: str = "") -> Optional[PickerSpec]:
        low = (name or "").strip().lower()
        if low == "menuitempath":
            return PickerSpec(
                "browse",
                browse=self._browse_menu_item,
                tip="Select the menu item from the menu configuration",
            )
        if low == "default tabs menu name":
            return PickerSpec("combo", self.menus(), tip="The menu whose items the tab bar shows")
        if low == "datasource":
            from ...io.project import EQUIPMENT_MODEL_MENU

            return PickerSpec(
                "combo",
                [EQUIPMENT_MODEL_MENU] + self.menus(),
                tip="__EquipmentModel is the equipment hierarchy; or a menu",
            )
        if low == "tabpanename" and self.pane_names():
            return PickerSpec("combo", self.pane_names(), tip="The pane on this page that shows the tab bar")
        if low == "excludedautofillpanes" and self.pane_names():
            return PickerSpec(
                "multi",
                self.pane_names(),
                tip="Panes that keep their content when this pane changes the "
                "context (several allowed, separated by commas)",
            )
        if low == "tabcontrolname":
            return PickerSpec(
                "browse",
                browse=self._browse_tab_control,
                tip="Select the tab bar on the header pane's default page",
            )
        if low == "equiprefassocs":
            cats = self.reference_categories()
            if cats:
                return PickerSpec(
                    "multi",
                    cats,
                    tip="Equipment reference categories whose references become the page's associations",
                )
            return None
        if is_equipment_param(name):
            pdir = self.project_dir
            return PickerSpec(
                "browse",
                browse=self._browse_equipment,
                tip="Select the equipment from the list",
                factory=lambda v, browse, tip: EquipmentField(v, pdir, browse=browse, tip=tip),
            )
        return None

    # -- browse callbacks: (parent widget, current text, the dialog's values)
    def _browse_menu_item(self, parent, current: str, _values=None) -> Optional[str]:
        dlg = MenuItemPickerDialog(self.project_dir, current, parent, items=self.menu_items())
        return dlg._result if dlg.exec() == QtWidgets.QDialog.Accepted else None

    def _browse_equipment(self, parent, current: str, _values=None) -> Optional[str]:
        dlg = EquipmentBrowserDialog(self.project_dir, current, parent)
        return dlg._result if dlg.exec() == QtWidgets.QDialog.Accepted else None

    def tab_control_names(self, header_pane: str) -> Tuple[str, List[str]]:
        """(the header pane's default page, the names of the objects on it)."""
        from ...features import sa_workspace as SA

        pane = next((p for p in self.panes if p.name.lower() == (header_pane or "").lower()), None)
        page = pane.default_page if pane is not None else ""
        if not page:
            return page, []
        path = SA.find_page_file(self.project_dir, page)
        names = SA.page_top_level_names(path) if path else None
        out, seen = [], set()
        for n in names or ():
            if n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
        return page, sorted(out, key=str.lower)

    def _browse_tab_control(self, parent, current: str, values=None) -> Optional[str]:
        header = ""
        for k, v in (values or {}).items():
            if k.strip().lower() == "tabpanename":
                header = v
        page, names = self.tab_control_names(header)
        if not header:
            QtWidgets.QMessageBox.information(
                parent,
                "Tab Control Name",
                "Enter the Tab Header Pane Name first. The tab control is on that pane's default page.",
            )
            return None
        if not names:
            QtWidgets.QMessageBox.information(
                parent,
                "Tab Control Name",
                "%s, the default page of pane '%s', has no named objects. Name the tab bar "
                "genie there (Access > General > Name)." % (page or "The default page", header),
            )
            return None
        item, ok = QtWidgets.QInputDialog.getItem(
            parent,
            "Tab Control Name",
            "Objects on %s (the default page of pane '%s'):" % (page, header),
            names,
            max(0, next((i for i, n in enumerate(names) if n.lower() == (current or "").lower()), 0)),
            True,
        )
        return item if ok and item else None


from ...infra import caches as _caches  # noqa: E402

_caches.register("sa_pickers.equipment_models", _EQUIP_MODELS, on=("refresh", "tests"))
_caches.register("sa_pickers.equipment_names", _EQUIP_NAMES, on=("refresh", "tests"))
# (the folders warmed: a refresh warms them again)
_caches.register("sa_pickers.equipment_warming", _EQUIP_WARMING, on=("refresh", "tests"))
