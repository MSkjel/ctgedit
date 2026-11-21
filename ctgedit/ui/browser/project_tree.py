"""The browser's project tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...io.catalog import (
    INCLUDE_CYCLE,
    INCLUDE_IMPLICIT,
    INCLUDE_MISSING,
    InstallInfo,
    ProjectCatalog,
    ProjectInfo,
    natural_key,
)
from ..style.theme import COLORS, icon

ScopeRole = QtCore.Qt.UserRole + 20
LabelRole = QtCore.Qt.UserRole + 21
StyleRole = QtCore.Qt.UserRole + 22
TipRole = QtCore.Qt.UserRole + 23
NoteRole = QtCore.Qt.UserRole + 24
CountRole = QtCore.Qt.UserRole + 25
ProjectRole = QtCore.Qt.UserRole + 26
StateRole = QtCore.Qt.UserRole + 27
PathRole = QtCore.Qt.UserRole + 28
PendingRole = QtCore.Qt.UserRole + 29

TREE, FLAT = "tree", "flat"
VIEWS = (TREE, FLAT)

_KIND_NOUN = {
    "page": ("page", "pages"),
    "template": ("template library", "template libraries"),
    "symbol": ("symbol library", "symbol libraries"),
    "genie": ("genie library", "genie libraries"),
    "supergenie": ("super genie library", "super genie libraries"),
}

#: tool tips of the rows that are not found
TIP_NOT_FOUND = "In include.dbf, project not found"
TIP_FOLDER_MISSING = "Registered, folder missing"


@dataclass(frozen=True, eq=False)
class Scope:
    """What the grid lists: one project, a project and its include chain, a
    whole install, recent pages or a folder."""

    id: str
    label: str
    kind: str  # project | chain | install | recent | folder
    projects: tuple = ()  # ProjectInfo, search order
    paths: tuple = ()  # recent page paths
    anchor: Optional[ProjectInfo] = None  # the project the scope is about
    is_live: bool = True
    install: Optional[InstallInfo] = None  # kind 'install'

    @property
    def spans_projects(self) -> bool:
        """The scope lists more than one project (cards name the project)."""
        return self.kind in ("chain", "install") and len(self.projects) > 1


def project_scope(info: ProjectInfo) -> Scope:
    kind = "folder" if info.is_folder else "project"
    prefix = "f:" if info.is_folder else "p:"
    return Scope(prefix + info.scope_id, info.name, kind, (info,), anchor=info, is_live=info.is_live)


def chain_scope(catalog: ProjectCatalog, info: ProjectInfo) -> Scope:
    """``info`` and its include chain (the Graphics Builder's search order)."""
    chain = tuple(catalog.closure(info))
    return Scope(
        "c:" + info.scope_id,
        "%s with included projects" % info.name,
        "chain",
        chain,
        anchor=info,
        is_live=info.is_live,
    )


def install_scope(catalog: ProjectCatalog, inst: InstallInfo) -> Scope:
    """Every project of ``inst``."""
    label = "All projects in %s" % inst.short_label
    if not inst.is_live:
        label += " (other install)"
    return Scope(
        "a:" + inst.key, label, "install", tuple(catalog.projects(inst)), is_live=inst.is_live, install=inst
    )


def recent_scope(paths: Sequence[str]) -> Scope:
    return Scope("recent", "Recent pages", "recent", (), tuple(paths))


def included_by_note(info: ProjectInfo, shown: int = 2) -> str:
    """``'included by A, B +3'`` (the first ``shown`` names), ``''`` if none."""
    if info.is_implicit:
        return "always included"
    names = list(info.included_by)
    if not names:
        return ""
    head = ", ".join(names[:shown])
    more = len(names) - shown
    return "included by %s%s" % (head, " +%d" % more if more > 0 else "")


class _RowDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a row as its name, a dim note and a right-aligned dim count."""

    def paint(self, p: QtGui.QPainter, opt, index) -> None:
        o = QtWidgets.QStyleOptionViewItem(opt)
        self.initStyleOption(o, index)
        widget = o.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        o.text = ""
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, o, p, widget)
        r = style.subElementRect(QtWidgets.QStyle.SE_ItemViewItemText, o, widget)
        r = r.adjusted(2, 0, -4, 0)
        label = index.data(LabelRole) or index.data(QtCore.Qt.DisplayRole) or ""
        note = index.data(NoteRole) or ""
        count = index.data(CountRole)
        st = index.data(StyleRole) or ""
        sel = bool(o.state & QtWidgets.QStyle.State_Selected)
        dim = QtGui.QColor(COLORS["text_dim"])
        if sel:
            fg = QtGui.QColor(COLORS.get("on_selection", "#ffffff"))
            dim = fg
        elif st == "warn":
            fg = QtGui.QColor(COLORS["warn"])
        elif st in ("dim", "section", "empty"):
            fg = QtGui.QColor(COLORS["text_dim"])
        else:
            fg = QtGui.QColor(COLORS["text"])
        p.save()
        p.setFont(o.font)
        fm = QtGui.QFontMetrics(o.font)
        right = r.right()
        if count is not None:
            ct = str(count)
            cw = fm.horizontalAdvance(ct)
            p.setPen(dim)
            p.drawText(
                QtCore.QRect(right - cw, r.top(), cw, r.height()),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                ct,
            )
            right -= cw + 10
        avail = max(10, right - r.left())
        name_w = min(fm.horizontalAdvance(label), avail)
        p.setPen(fg)
        p.drawText(
            QtCore.QRect(r.left(), r.top(), name_w, r.height()),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            fm.elidedText(label, QtCore.Qt.ElideMiddle, name_w),
        )
        if note and avail - name_w > 24:
            nx = r.left() + name_w + 6
            nw = right - nx
            p.setPen(dim)
            p.drawText(
                QtCore.QRect(nx, r.top(), nw, r.height()),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                fm.elidedText(note, QtCore.Qt.ElideRight, nw),
            )
        p.restore()


class ProjectTree(QtWidgets.QTreeWidget):
    """Scope picker (see module docstring)."""

    scopeChanged = QtCore.Signal(object)
    #: a ``c:`` id was selected: the project and its includes are shown
    includesChanged = QtCore.Signal(bool)

    def __init__(
        self,
        catalog: ProjectCatalog,
        parent=None,
        recent: Sequence[str] = (),
        folders: Sequence[ProjectInfo] = (),
        kind: str = "page",
        view: str = TREE,
        chain_of: Optional[ProjectInfo] = None,
        with_includes: bool = False,
    ):
        super().__init__(parent)
        self.catalog = catalog
        self._recent = [p for p in recent if p]
        self._folders: List[ProjectInfo] = list(folders)
        self._kind = kind
        self._view = view if view in VIEWS else TREE
        self._chain_of = chain_of
        self._with_includes = bool(with_includes)
        self._last_id: Optional[str] = None
        self._chains: Dict[str, Scope] = {}
        self._building = False
        self.setHeaderHidden(True)
        self.setMinimumWidth(200)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(True)
        self.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setAccessibleName("Projects")
        self.setItemDelegate(_RowDelegate(self))
        self._section_font = QtGui.QFont(self.font())
        self._section_font.setBold(True)
        self.itemExpanded.connect(self._fill_pending)
        self.rebuild()
        self.itemSelectionChanged.connect(self._on_selection)

    # -- options -----------------------------------------------------------------
    def view(self) -> str:
        return self._view

    def set_view(self, view: str) -> None:
        """``'tree'`` (the include structure) or ``'flat'`` (every project once)."""
        view = view if view in VIEWS else TREE
        if view == self._view:
            return
        self._view = view
        self.rebuild()

    def with_includes(self) -> bool:
        return self._with_includes

    def set_with_includes(self, on: bool, emit: bool = True) -> None:
        """A project row lists the project and its includes (``on``) or the
        project alone; re-emits the current scope when it changes."""
        on = bool(on)
        if on == self._with_includes:
            return
        self._with_includes = on
        it = self.currentItem()
        base = it.data(0, ScopeRole) if it is not None else None
        if emit and base is not None and base.kind == "project":
            self._on_selection()

    def chain_anchor(self) -> Optional[ProjectInfo]:
        return self._chain_of

    # -- rows ---------------------------------------------------------------------
    def _section(self, text: str, expanded: bool = True, tip: str = "") -> QtWidgets.QTreeWidgetItem:
        it = QtWidgets.QTreeWidgetItem(self, [text])
        it.setFlags(QtCore.Qt.ItemIsEnabled)
        it.setFont(0, self._section_font)
        it.setData(0, LabelRole, text)
        it.setData(0, StyleRole, "section")
        if tip:
            it.setToolTip(0, tip)
        it.setExpanded(expanded)
        return it

    def _row(
        self,
        parent,
        label: str,
        scope: Optional[Scope] = None,
        glyph: str = "page",
        tip: str = "",
        note: str = "",
        style: str = "",
        selectable: bool = True,
    ) -> QtWidgets.QTreeWidgetItem:
        it = QtWidgets.QTreeWidgetItem(parent, [label + ("  " + note if note else "")])
        it.setData(0, LabelRole, label)
        it.setData(0, NoteRole, note)
        it.setData(0, StyleRole, style)
        it.setData(0, TipRole, tip)
        if scope is not None:
            it.setData(0, ScopeRole, scope)
        it.setIcon(0, icon(glyph, 16))
        if tip:
            it.setToolTip(0, tip)
        if not selectable:
            it.setFlags(QtCore.Qt.ItemIsEnabled)
        return it

    def _project_tip(self, info: ProjectInfo, state: str = "ok") -> str:
        if state == INCLUDE_MISSING:
            return "%s\n%s" % (info.name, TIP_FOLDER_MISSING if info.registered else TIP_NOT_FOUND)
        lines = [info.name, info.dir]
        if info.is_implicit:
            lines.append("Always included")
        elif info.included_by:
            lines.append("Included by " + ", ".join(info.included_by))
        if info.includes:
            lines.append("Includes " + ", ".join(info.includes))
        if not info.registered and not info.is_folder:
            lines.append("Not registered in MASTER.DBF")
        if not info.is_live and not info.is_folder:
            lines.append("Other install. The Graphics Builder does not use these files.")
        return "\n".join(lines)

    def _project_row(
        self,
        parent,
        info: ProjectInfo,
        state: str = "ok",
        path: Tuple[ProjectInfo, ...] = (),
        note: Optional[str] = None,
        lazy: bool = True,
    ) -> QtWidgets.QTreeWidgetItem:
        missing = state == INCLUDE_MISSING or not info.exists
        if note is None:
            note = {
                INCLUDE_CYCLE: "(included above)",
                INCLUDE_MISSING: "(not found)",
                INCLUDE_IMPLICIT: "always included",
            }.get(state, "")
            if missing:
                note = "(not found)"
        style = ""
        if missing or state == INCLUDE_CYCLE:
            style = "dim"
        elif not info.registered or not info.is_live:
            style = "warn"
        it = self._row(
            parent,
            info.name,
            None if missing else project_scope(info),
            "open" if info.is_folder else "page",
            self._project_tip(info, INCLUDE_MISSING if missing else state),
            note,
            style,
            selectable=not missing,
        )
        it.setData(0, ProjectRole, info)
        it.setData(0, StateRole, INCLUDE_MISSING if missing else state)
        it.setData(0, PathRole, tuple(path))
        if (
            lazy
            and not missing
            and state != INCLUDE_CYCLE
            and self._view == TREE
            and not info.is_folder
            and (info.includes or not path)
        ):
            # children are made when the row is first expanded
            it.setData(0, PendingRole, True)
            QtWidgets.QTreeWidgetItem(it, [""])
        if not self._building:
            self._apply_count(it)
        return it

    def _fill_pending(self, item: QtWidgets.QTreeWidgetItem) -> None:
        if not item.data(0, PendingRole):
            return
        item.setData(0, PendingRole, False)
        while item.childCount():
            item.removeChild(item.child(0))
        info = item.data(0, ProjectRole)
        if info is None:
            return
        path = tuple(item.data(0, PathRole) or ()) + (info,)
        top = len(path) == 1
        kids = self.catalog.include_children(info, path[:-1], top=top)
        for sub, state in kids:
            self._project_row(item, sub, state, path)
        if not kids:
            item.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.DontShowIndicator)

    def _add_projects(self, parent, inst: InstallInfo) -> None:
        cat = self.catalog
        if self._view == FLAT:
            for info in sorted(cat.projects(inst, existing_only=False), key=lambda p: natural_key(p.name)):
                self._project_row(
                    parent, info, note=included_by_note(info) if info.exists else None, lazy=False
                )
        else:
            for info in cat.top_level(inst):
                self._project_row(parent, info)

    def _add_chain(self) -> None:
        anchor = self._chain_of
        if anchor is None:
            return
        if self._view == FLAT or anchor.is_folder:
            for info in self.catalog.closure(anchor):
                self._project_row(self, info, note=included_by_note(info), lazy=False)
        else:
            it = self._project_row(self, anchor)
            self._fill_pending(it)  # (signals are blocked while building)
            it.setExpanded(True)

    def rebuild(self) -> None:
        """(Re)build every row; keeps the selection and the expanded rows."""
        cur = self._last_id
        expanded = self.expanded_paths() if self.topLevelItemCount() else []
        at = self.currentItem()
        cur_path = self._name_path(at) if at is not None else ""
        self._chains.clear()
        self._building = True
        self.blockSignals(True)
        self.clear()
        cat = self.catalog
        if self._chain_of is not None:
            self._add_chain()
        else:
            if self._recent:
                q = self._section("Quick access")
                self._row(
                    q,
                    "Recent pages",
                    recent_scope(self._recent),
                    "open",
                    "Pages you opened recently (any project)",
                )
            live = cat.live_install
            if live is not None:
                row = self._row(
                    self,
                    "Active install: %s" % live.short_label,
                    install_scope(cat, live),
                    "grid",
                    "The install the running Graphics Builder and runtime use\n%s" % live.user_dir,
                )
                self._add_projects(row, live)
                row.setExpanded(True)
            others = [i for i in cat.installs if not i.is_live]
            if others:
                sec = self._section(
                    "Other installs", expanded=False, tip="Installs the running Graphics Builder does not use"
                )
                for inst in others:
                    node = self._row(
                        sec,
                        "%s (other install)" % inst.short_label,
                        install_scope(cat, inst),
                        "grid",
                        "%s\nThe running Graphics Builder does not read these files." % inst.user_dir,
                        style="warn",
                    )
                    self._add_projects(node, inst)
            if self._folders:
                sec = self._section("Folders")
                for f in self._folders:
                    fr = self._row(sec, f.name, project_scope(f), "open", f.dir)
                    fr.setData(0, ProjectRole, f)
        self._building = False
        self.set_kind(self._kind)
        self.blockSignals(False)
        if expanded:
            self.restore_expanded(expanded)
        if cur:
            # the same row again when it is still there (a project listed under
            # several includers), else the first row of the scope
            same = self._row_at_path(cur_path) if cur_path else None
            s = same.data(0, ScopeRole) if same is not None else None
            if s is not None and s.id == cur:
                self._select_item(same, emit=False)
            else:
                self.select_scope_id(cur, emit=False)

    # -- expanded rows (by name path) -------------------------------------------------
    @staticmethod
    def _name_path(item: QtWidgets.QTreeWidgetItem) -> str:
        parts = []
        while item is not None:
            parts.append(item.data(0, LabelRole) or item.text(0))
            item = item.parent()
        return "/".join(reversed(parts))

    def expanded_paths(self, limit: int = 200) -> List[str]:
        """The expanded rows as ``'Active install: 2023 R2/MyProject/MyInclude'``."""
        out = []
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value() is not None and len(out) < limit:
            item = it.value()
            if item.isExpanded() and item.childCount():
                out.append(self._name_path(item))
            it += 1
        return out

    def _row_at_path(self, name_path: str) -> Optional[QtWidgets.QTreeWidgetItem]:
        """The row of a :meth:`_name_path` (rows are made on the way)."""
        parts = name_path.split("/")
        level = [self.topLevelItem(k) for k in range(self.topLevelItemCount())]
        item = None
        for depth, part in enumerate(parts):
            item = next((it for it in level if (it.data(0, LabelRole) or it.text(0)) == part), None)
            if item is None:
                return None
            if depth + 1 < len(parts):
                self._fill_pending(item)
                level = [item.child(k) for k in range(item.childCount())]
        return item

    def restore_expanded(self, paths: Sequence[str]) -> None:
        """Expand the rows of ``paths`` (made on the way, when not made yet)."""
        want = set(str(p) for p in paths or ())
        if not want:
            return
        prefixes = set()
        for p in want:
            parts = p.split("/")
            for k in range(1, len(parts) + 1):
                prefixes.add("/".join(parts[:k]))
        todo = [self.topLevelItem(k) for k in range(self.topLevelItemCount())]
        while todo:
            item = todo.pop(0)
            name = self._name_path(item)
            if name not in prefixes:
                continue
            if name in want:
                item.setExpanded(True)  # itemExpanded fills a pending row
                self._fill_pending(item)
            todo.extend(item.child(k) for k in range(item.childCount()))

    # -- counts -----------------------------------------------------------------
    def _count(self, scope: Scope, kind: str) -> int:
        if scope.kind == "recent":
            return len(scope.paths) if kind == "page" else 0
        return sum(self.catalog.count(p, kind) for p in scope.projects)

    def _apply_count(self, item: QtWidgets.QTreeWidgetItem) -> None:
        scope = item.data(0, ScopeRole)
        if scope is None:
            return
        n = self._count(scope, self._kind)
        item.setData(0, CountRole, n)
        st = item.data(0, StyleRole) or ""
        if st in ("", "empty"):
            item.setData(0, StyleRole, "empty" if n == 0 else "")
        one, many = _KIND_NOUN.get(self._kind, (self._kind, self._kind))
        base_tip = item.data(0, TipRole) or ""
        what = "%d %s" % (n, one if n == 1 else many)
        item.setToolTip(0, ("%s\n\n%s" % (base_tip, what)) if base_tip else what)

    def set_kind(self, kind: str) -> None:
        """Count ``kind`` on every row; dim the rows that have none."""
        self._kind = kind
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value() is not None:
            self._apply_count(it.value())
            it += 1
        self.viewport().update()

    # -- selection ---------------------------------------------------------------
    def current_scope(self) -> Optional[Scope]:
        """The selected row's scope (a project row with its includes when
        ``with_includes`` is on)."""
        it = self.currentItem()
        base = it.data(0, ScopeRole) if it is not None else None
        if base is None:
            return None
        if self._with_includes and base.kind == "project" and base.anchor is not None:
            sc = self._chains.get(base.id)
            if sc is None:
                sc = self._chains[base.id] = chain_scope(self.catalog, base.anchor)
            return sc
        return base

    def current_base_scope(self) -> Optional[Scope]:
        """The selected row's own scope (``p:`` for a project row)."""
        it = self.currentItem()
        return it.data(0, ScopeRole) if it is not None else None

    def _on_selection(self) -> None:
        base = self.current_base_scope()
        if base is None:
            return
        self._last_id = base.id
        self.scopeChanged.emit(self.current_scope())

    def _find(self, scope_id: str) -> Optional[QtWidgets.QTreeWidgetItem]:
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value() is not None:
            item = it.value()
            s = item.data(0, ScopeRole)
            if s is not None and s.id == scope_id:
                return item
            it += 1
        return None

    def _project_for_id(self, sid: str) -> Optional[ProjectInfo]:
        """The project of a ``<install key>|<name>`` / ``folder|<dir>`` id."""
        if sid.startswith("folder|"):
            for f in self._folders:
                if f.scope_id == sid:
                    return f
            if self._chain_of is not None and self._chain_of.scope_id == sid:
                return self._chain_of
            return None
        key, _bar, name = sid.rpartition("|")
        for inst in self.catalog.installs:
            if inst.key == key:
                return self.catalog.project(name, inst)
        return None

    def _select_item(self, item: QtWidgets.QTreeWidgetItem, emit: bool) -> None:
        p = item.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        self.blockSignals(True)
        self.setCurrentItem(item)
        self.blockSignals(False)
        self.scrollToItem(item)
        base = item.data(0, ScopeRole)
        if base is not None:
            self._last_id = base.id
        if emit:
            self._on_selection()

    def select_scope_id(self, scope_id: str, emit: bool = True) -> bool:
        """Select (and reveal) the first row of ``scope_id`` in tree order. A
        ``c:`` id (a project and its includes) selects
        the project's row and turns ``with_includes`` on
        (``includesChanged``)."""
        if not scope_id:
            return False
        chain = scope_id[:2] == "c:"
        item = None
        if chain or scope_id[:2] in ("p:", "f:"):
            info = self._project_for_id(scope_id[2:])
            if info is not None:
                if chain and info.is_folder:
                    chain = False
                item = self._current_row_of(
                    ("f:" if info.is_folder else "p:") + info.scope_id
                ) or self._first_row_of(info)
        else:
            item = self._current_row_of(scope_id) or self._find(scope_id)
        if item is None:
            return False
        if chain and not self._with_includes:
            self._with_includes = True
            self.includesChanged.emit(True)
        self._select_item(item, emit)
        return True

    def select_project(self, info: ProjectInfo, chain: bool = False, emit: bool = True) -> bool:
        """Select the first row of ``info`` in tree order (``chain``: with its
        includes)."""
        if info.is_folder:
            return self.select_scope_id("f:" + info.scope_id, emit)
        return self.select_scope_id(("c:" if chain else "p:") + info.scope_id, emit)

    def _current_row_of(self, scope_id: str) -> Optional[QtWidgets.QTreeWidgetItem]:
        """The selected row when it already shows ``scope_id`` (a project
        listed under several includers keeps the row the user picked)."""
        it = self.currentItem()
        s = it.data(0, ScopeRole) if it is not None else None
        return it if s is not None and s.id == scope_id else None

    def _first_row_of(self, info: ProjectInfo) -> Optional[QtWidgets.QTreeWidgetItem]:
        """The first row of ``info`` in tree order (rows are made on the way)."""
        want = ("f:" if info.is_folder else "p:") + info.scope_id
        if self._view == TREE and not info.is_folder:
            item = self._make_path_to(info)
            if item is not None:
                return item
        return self._find(want)

    def _tree_tops(self) -> List[QtWidgets.QTreeWidgetItem]:
        """The rows the include trees start from (under the install rows)."""
        out = []
        roots = [self.topLevelItem(k) for k in range(self.topLevelItemCount())]
        for r in roots:
            if r.data(0, ProjectRole) is not None:
                out.append(r)
            for k in range(r.childCount()):
                c = r.child(k)
                if c.data(0, ProjectRole) is not None:
                    out.append(c)
                elif c.data(0, ScopeRole) is not None and c.data(0, ScopeRole).kind == "install":
                    out.extend(
                        c.child(j)
                        for j in range(c.childCount())
                        if c.child(j).data(0, ProjectRole) is not None
                    )
        return out

    def _make_path_to(self, info: ProjectInfo) -> Optional[QtWidgets.QTreeWidgetItem]:
        """Depth first in tree order: the first row of ``info`` (rows are made
        on the way)."""
        miss: set = set()
        target = info.key

        def under(item: QtWidgets.QTreeWidgetItem) -> Optional[QtWidgets.QTreeWidgetItem]:
            p = item.data(0, ProjectRole)
            state = item.data(0, StateRole)
            if p is None or state in (INCLUDE_MISSING, INCLUDE_CYCLE):
                return None
            if p.key == target:
                return item
            if p.key in miss:
                return None
            self._fill_pending(item)
            for k in range(item.childCount()):
                got = under(item.child(k))
                if got is not None:
                    return got
            miss.add(p.key)
            return None

        for top in self._tree_tops():
            got = under(top)
            if got is not None:
                return got
        return None

    def first_root_scope_id(self) -> Optional[str]:
        roots = self.catalog.roots()
        return ("p:" + roots[0].scope_id) if roots else None

    def install_scope_id(self) -> Optional[str]:
        live = self.catalog.live_install
        return ("a:" + live.key) if live is not None else None

    def all_scopes(self) -> Dict[str, Scope]:
        out: Dict[str, Scope] = {}
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value() is not None:
            s = it.value().data(0, ScopeRole)
            if s is not None:
                out.setdefault(s.id, s)
            it += 1
        return out

    def project_names(self) -> List[str]:
        """The project rows made so far, in order (tests)."""
        out = []
        it = QtWidgets.QTreeWidgetItemIterator(self)
        while it.value() is not None:
            p = it.value().data(0, ProjectRole)
            if p is not None:
                out.append(p.name)
            it += 1
        return out
