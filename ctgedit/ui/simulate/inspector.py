"""The Simulate Inspector: what the running page is made of and uses."""

from __future__ import annotations
from ..style.theme import sp as _sp

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as _help
from ..style import theme
from ...infra.logsetup import note_exception as _note_exc
from . import options

#: a name an expression may use: a tag, a function, Equip.Item
_WORD = re.compile(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*")
#: an association reference: ?name? (also ?INT 1?)
_ASS = re.compile(r"\?([^?\n]{1,64})\?")

#: the default of Settings > Simulate > Inspector and Watch > Inspector refreshes every
#: (options.inspector_refresh_ms)
REFRESH_MS = 500


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return "%.6g" % v
    return str(v)


class RowsModel(QtCore.QAbstractTableModel):
    """A list of rows (tuples of text, the first column the key) with tool tips; updated in
    place when the keys stay (the selection and the scrolling stay), else reset."""

    def __init__(self, headers: Sequence[str], parent=None):
        super().__init__(parent)
        self.headers = list(headers)
        self.rows: List[tuple] = []
        self.data_of: List[Any] = []  # per row: what the row stands for (an AN, a tag ...)

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            v = self.rows[r][c] if c < len(self.rows[r]) else ""
            if role == QtCore.Qt.ToolTipRole:
                return v if len(v) > 40 else None
            return v
        if role == QtCore.Qt.UserRole:
            return self.data_of[r]
        if role == QtCore.Qt.UserRole + 1:
            return self._sort_key(r, c)
        return None

    def _sort_key(self, r: int, c: int):
        # (sorting: numbers as numbers)
        v = self.rows[r][c] if c < len(self.rows[r]) else ""
        try:
            return float(v)
        except TypeError, ValueError:
            return v.lower()

    def sort_keys(self, c: int) -> list:
        """Every row's sort value in column ``c`` (data's UserRole + 1), kept until the rows
        change: the filter's sort compares them straight (not through data())."""
        keys = self.__dict__.setdefault("_keys", {}).get(c)
        if keys is None or len(keys) != len(self.rows):
            keys = self._keys[c] = [self._sort_key(r, c) for r in range(len(self.rows))]
        return keys

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):  # noqa: N802
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return self.headers[section]
        return None

    def set_rows(self, rows: List[tuple], data_of: List[Any]) -> None:
        if len(rows) == len(self.rows) and all(a[0] == b[0] for a, b in zip(rows, self.rows)):
            changed = [k for k, (a, b) in enumerate(zip(rows, self.rows)) if a != b]
            self.rows, self.data_of = rows, data_of
            if changed:
                for c, keys in self.__dict__.get("_keys", {}).items():
                    for k in changed:
                        keys[k] = self._sort_key(k, c)
                self.dataChanged.emit(
                    self.index(min(changed), 0), self.index(max(changed), len(self.headers) - 1)
                )
            return
        self.beginResetModel()
        self.rows, self.data_of = rows, data_of
        self.__dict__.pop("_keys", None)
        self.endResetModel()


class _Filter(QtCore.QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.needle = ""
        self.match = theme.TextMatch()
        self.only: Optional[Callable[[Any], bool]] = None
        self.setSortRole(QtCore.Qt.UserRole + 1)

    def set_needle(self, text, regex: bool = False) -> None:
        """Rows with a cell that matches ``text`` (the search box's rule: ``regex`` = its
        '.*', a regular expression)."""
        self.match = text if isinstance(text, theme.TextMatch) else theme.TextMatch(text, regex)
        self.needle = self.match.text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:  # noqa: N802
        m = self.sourceModel()
        if self.only is not None and not self.only(m.data_of[row]):
            return False
        if self.match.empty:
            return True
        return self.match(*m.rows[row])

    def lessThan(self, a, b) -> bool:  # noqa: N802
        m = self.sourceModel()
        keys = m.sort_keys(a.column()) if isinstance(m, RowsModel) else None
        if keys is not None:
            x, y = keys[a.row()], keys[b.row()]
        else:
            x, y = a.data(QtCore.Qt.UserRole + 1), b.data(QtCore.Qt.UserRole + 1)
        if type(x) is not type(y):
            x, y = str(x), str(y)
        return x < y


class _Table(QtWidgets.QWidget):
    """A sortable, filtered list with a count."""

    activated = QtCore.Signal(object)  # the row's data
    current = QtCore.Signal(object)

    def __init__(self, name: str, headers: Sequence[str], parent=None, stretch: Optional[int] = None):
        super().__init__(parent)
        self.model = RowsModel(headers, self)
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)
        self.view = QtWidgets.QTableView(self)
        self.view.setAccessibleName(name)
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(self.view.fontMetrics().height() + 6)
        # the columns of every table: drag or double-click a
        # divider, the main text column takes the width, the widths set are remembered
        from .. import tables

        tables.make_resizable(self.view, "PageInspector/" + name.lower(), stretch=stretch)
        # (the column names on the left, as in the Simulate window's lists and Insert Tag)
        self.view.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.view.doubleClicked.connect(lambda ix: self.activated.emit(self._data(ix)))
        self.view.selectionModel().currentRowChanged.connect(
            lambda ix, _old: self.current.emit(self._data(ix))
        )
        self.view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.installEventFilter(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.activate_text = ""
        #: more context menu commands: (text, function of the row's data)
        self.extra_actions: List[tuple] = []

    def _data(self, ix):
        if not ix.isValid():
            return None
        return self.proxy.data(ix.siblingAtColumn(0), QtCore.Qt.UserRole)

    def set_rows(self, rows, data_of) -> None:
        self.model.set_rows(rows, data_of)

    def selected(self) -> Optional[Any]:
        return self._data(self.view.currentIndex())

    def shown_count(self) -> int:
        return self.proxy.rowCount()

    def copy_rows(self, all_rows: bool = False) -> int:
        """The selected rows (all shown ones when none is selected) to the clipboard, tab
        separated with the column names first."""
        rows = sorted({ix.row() for ix in self.view.selectionModel().selectedRows()})
        if all_rows or not rows:
            rows = range(self.proxy.rowCount())
        out = ["\t".join(self.model.headers)]
        for r in rows:
            out.append(
                "\t".join(str(self.proxy.index(r, c).data() or "") for c in range(self.proxy.columnCount()))
            )
        QtGui.QGuiApplication.clipboard().setText("\n".join(out))
        return len(out) - 1

    def _menu(self, pos) -> None:
        menu = QtWidgets.QMenu(self)
        d = self.selected()
        if self.activate_text and d is not None:
            menu.addAction(self.activate_text, lambda: self.activated.emit(d))
        if d is not None:
            for text, fn in self.extra_actions:
                menu.addAction(text, lambda fn=fn: fn(d))
        menu.addAction("Copy", self.copy_rows)
        menu.addAction("Copy All Shown", lambda: self.copy_rows(True))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.view and ev.type() == QtCore.QEvent.KeyPress:
            if ev.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                d = self.selected()
                if d is not None:
                    self.activated.emit(d)
                return True
            if ev.matches(QtGui.QKeySequence.Copy):
                self.copy_rows()
                return True
        return super().eventFilter(obj, ev)


def _props(obj) -> Dict[str, str]:
    """The object's own properties that hold code or a value (KEY -> text)."""
    raw = getattr(obj, "raw", None) or {}
    props = raw.get("_props") or {}
    return {str(k): str(v) for k, v in props.items() if str(v or "").strip()}


def _state_text(st) -> str:
    """A short line of an object's live state ('hidden; fill 42%; text 42.5; #COM')."""
    if st is None:
        return ""
    out = []
    if not st.shown:
        out.append("hidden")
    if st.disabled:
        out.append("disabled")
    if st.error:
        out.append(st.error)
    if st.indication:
        out.append(st.indication)
    if st.text is not None:
        out.append("text %s" % st.text)
    if st.fill_level is not None:
        out.append("level %d%%" % round(st.fill_level * 100))
    if st.fill_colour is not None:
        out.append("colour #%06X" % (st.fill_colour & 0xFFFFFF))
    if st.symbol_state is not None:
        out.append("state %d" % st.symbol_state)
    elif st.symbol:
        out.append("symbol %s" % st.symbol)
    if st.move is not None:
        out.append("move %g,%g" % tuple(st.move))
    if st.rotate is not None:
        out.append("rotate %g" % st.rotate)
    if st.scale is not None:
        out.append("scale %g,%g" % tuple(st.scale))
    if st.alarm:
        out.append("alarm %s" % (st.alarm.get("state") or ""))
    return "; ".join(out)


_CALL = re.compile(r"[A-Za-z_]\w*\s*\(")
_STRING = re.compile(r'"[^"]*"')
_KEYWORDS = frozenset(
    (
        "IF",
        "THEN",
        "ELSE",
        "END",
        "AND",
        "OR",
        "NOT",
        "MOD",
        "BITAND",
        "BITOR",
        "BITXOR",
        "WHILE",
        "DO",
        "FOR",
        "TO",
        "SELECT",
        "CASE",
        "RETURN",
    )
)


def call_parts(text: str, most: int = 20) -> List[str]:
    """The function calls in Cicode text, innermost first ('TankLevel("A")' in
    '(TankLevel("A"))^(0,100)'; both 'F(G(1))' and 'G(1)'), the strings' insides skipped."""
    text = text or ""
    # (searched with the strings' insides blanked: a '(' in a string is no call)
    masked = _STRING.sub(lambda m: '"' + " " * (len(m.group()) - 2) + '"', text)
    out: List[str] = []
    i, n = 0, len(text)
    while i < n and len(out) < most:
        m = _CALL.search(masked, i)
        if m is None:
            break
        name = masked[m.start() : m.end() - 1].strip()
        depth, j = 1, m.end()
        while j < n and depth:
            c = masked[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        if depth == 0 and name.upper() not in _KEYWORDS:
            inner = call_parts(text[m.end() : j - 1], most)
            for x in inner + [text[m.start() : j]]:
                if x not in out:
                    out.append(x)
        i = m.end()
    return out[:most]


#: the engine's expression classes (ctsim/profile) as the animation they drive
_ANIMATION = (
    ("fill_colour_", "Fill colour"),
    ("fill_level", "Fill level"),
    ("display_", "Text"),
    ("legacy_string", "Text (legacy)"),
    ("legacy_number", "Number (legacy)"),
    ("legacy_animated_symbol", "Animated symbol (legacy)"),
    ("symbolset_", "Symbol"),
    ("visibility", "Visibility"),
    ("disable", "Disable"),
    ("movement", "Move"),
    ("scaling", "Size"),
    ("rotation", "Rotation"),
    ("slider", "Slider"),
    ("trend", "Trend"),
    ("vertex", "Vertex move"),
)
_VARIANT = {
    "onoff": "on/off",
    "multi": "multi-state",
    "array": "array",
    "threshold": "threshold",
    "gradient": "gradient",
    "animated": "animated",
}


def animation_text(cls: str) -> str:
    """The engine's expression class ('fill_colour_onoff') as the animation it drives
    ('Fill colour (on/off)'); an unknown class as its words."""
    c = (cls or "").strip().lower()
    for pre, text in _ANIMATION:
        if c == pre or (pre.endswith("_") and c.startswith(pre)):
            rest = c[len(pre) :] if pre.endswith("_") else ""
            if rest:
                return "%s (%s)" % (text, _VARIANT.get(rest, rest.replace("_", " ")))
            return text
    return c.replace("_", " ").capitalize()


def kind_text(o) -> str:
    """An object's type as a word ('Rectangle', 'Genie'), as the editor names it (a placed
    group-root symbol is a 'Symbol': names.kind_name)."""
    from ..names import kind_name

    return kind_name(o)


def owner_genie(page, o) -> str:
    """The genie the object is part of (itself or the nearest genie above it), or ''. A placed
    symbol or the page template links an element too (.ctl, .ctt): it is not a genie."""
    from ..names import library_link_label

    objs = getattr(page, "objects", None) or ()
    seen = 0
    while o is not None and seen < 64:
        g = getattr(o, "genie_template", "") or ""
        if g and library_link_label(g)[0] not in ("Symbol", "Template"):
            return g
        p = getattr(o, "parent", None)
        o = objs[p] if isinstance(p, int) and 0 <= p < len(objs) else None
        seen += 1
    return ""


class PageIndex:
    """What the page's objects name, made once per page: per object its properties, the words
    and ?associations? in them; per lower-case word the ANs that name it."""

    def __init__(self, page):
        self.page = page
        self.objects: List[Any] = [
            o for o in (getattr(page, "objects", None) or ()) if (getattr(o, "an", 0) or 0) > 0
        ]
        self.by_an: Dict[int, Any] = {o.an: o for o in self.objects}
        self.props: Dict[int, Dict[str, str]] = {}
        self.words: Dict[str, Set[int]] = {}
        self.assoc: Dict[str, Set[int]] = {}
        for o in self.objects:
            p = _props(o)
            self.props[o.an] = p
            for v in p.values():
                for m in _WORD.finditer(v):
                    self.words.setdefault(m.group(0).lower(), set()).add(o.an)
                for m in _ASS.finditer(v):
                    name = m.group(1).strip()
                    if name.upper().startswith(("INT ", "STRING ", "REAL ", "DIGITAL ")):
                        name = name.split(None, 1)[1]
                    self.assoc.setdefault(name.lower(), set()).add(o.an)

    def users_of(self, name: str) -> List[int]:
        return sorted(self.words.get((name or "").lower(), ()))


def _ans_text(ans: Sequence[int], most: int = 6) -> str:
    ans = list(ans)
    if not ans:
        return ""
    text = ", ".join(str(a) for a in ans[:most])
    return text + (" and %d more" % (len(ans) - most) if len(ans) > most else "")


class PageInspector(QtWidgets.QWidget):
    """The Inspector window of a Simulate window (see the module)."""

    def __init__(self, window, parent=None):
        super().__init__(parent, QtCore.Qt.Window)
        self.sim_window = window
        self.setObjectName("SimInspector")
        self.setWindowTitle("Inspector")
        self.setAccessibleName("Inspector")
        _help.set_help(self, "simulate#the-inspector")
        self.resize(980, 640)
        self._geometry_restored = False
        self._index: Optional[PageIndex] = None
        self._index_for = None
        #: the Performance tab's objects by AN, per page shown {id(page): (page, {an: obj})}
        self._prof_index: Dict[int, tuple] = {}
        self._shown_an: Optional[int] = None

        top = QtWidgets.QHBoxLayout()
        # the tab's page or a window shown in it (an SA page runs in its workspace's pane)
        page_label = QtWidgets.QLabel("Page:", self)
        top.addWidget(page_label)
        self.page_pick = QtWidgets.QComboBox(self)
        self.page_pick.setAccessibleName("Page")
        self.page_pick.setToolTip(
            "The tab's page, or a window shown in it (a pane, a popup): "
            "an SA page runs in a pane of its workspace"
        )
        self.page_pick.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.page_pick.activated.connect(self._page_picked)
        page_label.setBuddy(self.page_pick)
        # (as wide as its page's name, its arrow included)
        self.page_pick.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        top.addWidget(self.page_pick)
        self._family_key = None
        self._picked = None  # the SimulatedPage chosen, None = the default
        # the search field of every list: Ctrl+F, Esc clears it,
        # Up / Down move in the list shown; its placeholder names that list
        self.filter = theme.SearchField(
            "Search objects", self, tip="A name, a tag, an AN or a value, in any column"
        )
        self.filter.bind_find_shortcut(self)
        self.filter.searchChanged.connect(self._filter_changed)
        top.addWidget(self.filter, 1)
        self.errors_only = QtWidgets.QCheckBox("Only with errors", self)
        self.errors_only.setToolTip("Objects: only the ones with an error now or in the log")
        self.errors_only.toggled.connect(self._filter_changed)
        top.addWidget(self.errors_only)
        self.copy_btn = QtWidgets.QPushButton("Copy", self)
        self.copy_btn.setToolTip(
            "Copy the selected rows (all shown rows when none is selected), tab separated"
        )
        self.copy_btn.clicked.connect(self._copy)
        top.addWidget(self.copy_btn)
        self.count = theme.note_label("", parent=self)
        self.count.setWordWrap(False)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setDocumentMode(True)
        # (the list's row count at the right of its tabs, where there is room)
        self.count.setContentsMargins(0, 0, 4, 0)
        self.tabs.setCornerWidget(self.count, QtCore.Qt.TopRightCorner)

        # Objects: the list above, the selected object's details below
        self.objects = _Table("Objects", ("AN", "Name", "Kind", "Genie", "State now", "Error"), stretch=1)
        self.objects.activate_text = "Show on Page"
        self.objects.proxy.only = self._object_wanted
        self.details = QtWidgets.QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Object details")
        self.details.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        f = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.details.setFont(f)
        below = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(below)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(_sp(0))
        brow = QtWidgets.QHBoxLayout()
        self.eval_parts = QtWidgets.QPushButton("Evaluate Parts")
        self.eval_parts.setToolTip(
            "Run each function call in the object's expressions in its "
            "window and show what it returns now (a call that changes "
            "something changes it)"
        )
        self.eval_parts.clicked.connect(self._evaluate_parts)
        brow.addWidget(self.eval_parts)
        self.show_users = QtWidgets.QPushButton("Show on Page")
        self.show_users.setToolTip("Mark the object on the page")
        self.show_users.clicked.connect(lambda: self._show_object(self._shown_an))
        brow.addWidget(self.show_users)
        brow.addStretch(1)
        bl.addLayout(brow)
        bl.addWidget(self.details, 1)
        self._parts_an: Optional[int] = None
        self._parts: List[str] = []
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        split.addWidget(self.objects)
        split.addWidget(below)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.objects.current.connect(self._object_selected)
        self.objects.activated.connect(self._show_object)
        self.tabs.addTab(split, "Objects")

        self.tags = _Table(
            "Tags",
            ("Name", "Value", "Quality", "Type", "From", "Used by (AN)", "Equipment", "Comment"),
            stretch=0,
        )
        self.tags.activate_text = "Select in the Tags Panel"
        self.tags.activated.connect(self._select_tag)
        self.tags.extra_actions = [("Show Users on Page", self._show_users), ("Watch", self._watch_tag)]
        self.tabs.addTab(self.tags, "Tags")

        self.assocs = _Table(
            "Associations", ("Name", "Tag or value", "Window", "Page", "Used by (AN)", "Note"), stretch=0
        )
        self.assocs.activate_text = "Show the First User on Page"
        self.assocs.activated.connect(self._show_first)
        self.tabs.addTab(self.assocs, "Associations")

        self.vars = _Table("Variables", ("Name", "Value", "Kind", "Type or source", "Used by"), stretch=0)
        self.tabs.addTab(self.vars, "Variables")

        self.windows = _Table("Windows", ("Window", "Page", "Parent", "Associations", "Shown as"), stretch=1)
        self.tabs.addTab(self.windows, "Windows")
        self.tabs.addTab(self._build_performance(), "Performance")
        self.tabs.currentChanged.connect(
            lambda _k: (self._list_shown(), self._filter_changed(), self.refresh())
        )

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.tabs, 1)
        self.note = theme.note_label("", parent=self)
        self._note_is_state = False
        lay.addWidget(self.note)
        self._list_shown()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(options.inspector_refresh_ms())
        self._timer.timeout.connect(self.refresh)

    def apply_settings(self) -> None:
        """Settings > Simulate changed: the refresh takes its new time."""
        ms = options.inspector_refresh_ms()
        if ms != self._timer.interval():
            self._timer.setInterval(ms)

    #: the list each tab shows (its search placeholder)
    _LIST_NAMES = ("objects", "tags", "associations", "variables", "windows", "functions, objects and tasks")

    def _list_shown(self) -> None:
        """The search field follows the tab's list: its placeholder, Up / Down."""
        k = max(0, self.tabs.currentIndex())
        self.filter.setPlaceholderText("Search %s" % self._LIST_NAMES[k])
        t = self._current_table()
        # (the Performance tab: Up / Down move in its Functions list)
        self.filter.set_list_view(t.view if t is not None else self.perf.funcs)

    def _set_note(self, text: str, role: str = "text_dim", state: bool = False) -> None:
        """The line under the lists: what a command did (it stays until the next one), or the
        page's state (``state``: the debugger's pause, no page; gone when it ends)."""
        self._note_is_state = state
        theme.set_note_role(self.note, role)
        if self.note.text() != text:
            self.note.setText(text)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        # Esc closes the window (the search field clears itself first, then passes it on)
        if ev.key() == QtCore.Qt.Key_Escape and not ev.modifiers():
            self.close()
            return
        super().keyPressEvent(ev)

    # -- showing -------------------------------------------------------------------------
    def showEvent(self, ev) -> None:  # noqa: N802
        if not self._geometry_restored:
            self._geometry_restored = True
            _help.restore_window_geometry(self)
        super().showEvent(ev)
        self.refresh()
        self._timer.start()

    def hideEvent(self, ev) -> None:  # noqa: N802
        self._timer.stop()
        if not ev.spontaneous():
            # (as a modeless window of the editor: its size and place when it is hidden)
            _help.save_window_geometry(self)
        super().hideEvent(ev)

    def inspect(self, an: Optional[int] = None) -> None:
        """Show the window (the Objects list with object ``an`` selected, when given)."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()
        if an is not None:
            self.tabs.setCurrentIndex(0)
            self.filter.clear()
            self._select_an(an)

    def _family(self) -> list:
        w = self.sim_window
        if w is None:
            return []
        try:
            return [p for p in w.family(w.current()) if not getattr(p, "left", False)]
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            return []

    @staticmethod
    def _label(p) -> str:
        n = p.engine_window() if hasattr(p, "engine_window") else None
        how = {"embedded": "pane", "popup": "popup"}.get(getattr(p, "host", ""), "tab")
        return "%s (%s%s)" % (p.title(), how, ", window %d" % n if n is not None else "")

    def _sync_pages(self, fam: list) -> None:
        """The Page choice: the tab and its windows. A new set picks the window that shows
        the editor's document (the page simulated), else the tab."""
        key = tuple(id(p) for p in fam)
        labels = [self._label(p) for p in fam]
        if key == self._family_key:
            for k, text in enumerate(labels):
                if self.page_pick.itemText(k) != text:
                    self.page_pick.setItemText(k, text)
            return
        self._family_key = key
        if self._picked not in fam:
            self._picked = next(
                (
                    p
                    for p in fam
                    if getattr(p, "doc", None) is not None and getattr(p, "host", "tab") != "tab"
                ),
                None,
            ) or (fam[0] if fam else None)
        self.page_pick.blockSignals(True)
        try:
            self.page_pick.clear()
            for text in labels:
                self.page_pick.addItem(text)
            if self._picked in fam:
                self.page_pick.setCurrentIndex(fam.index(self._picked))
        finally:
            self.page_pick.blockSignals(False)

    def _page_picked(self, k: int) -> None:
        fam = self._family()
        if 0 <= k < len(fam):
            self._picked = fam[k]
            self._shown_an = None
            self.refresh()

    def _tab(self):
        """The page inspected: the Page choice of the current tab's family."""
        fam = self._family()
        self._sync_pages(fam)
        return self._picked if self._picked in fam else (fam[0] if fam else None)

    def _build_performance(self) -> QtWidgets.QWidget:
        """The Performance tab (ui/simulate/perf_view): the simulator's profiler and where
        the time goes (functions, the call tree, a flame graph, expressions, scans and tasks);
        the Inspector's search box filters it."""
        from .perf_view import PerformanceView

        self.perf = PerformanceView(self._profile, self._show_object, self._inspect_profiled)
        self._prof_asked = 0.0
        self._prof_shown = None
        return self.perf

    def _profile(self, action: str) -> None:
        from .sim_tools import tools_of

        sp = self._tab()
        t = tools_of(sp.sim) if sp is not None else None
        if t is None or not t.available():
            self.perf.set_status(False, "The Cicode simulator does not run this page.")
            return
        t.ask_profile(action)
        QtCore.QTimer.singleShot(300, self, self.refresh)

    def _fill_performance(self, sp) -> None:
        import time as _time
        from .sim_tools import tools_of

        t = tools_of(sp.sim)
        if t is None or not t.available():
            self.perf.set_status(False, "The Cicode simulator does not run this page.")
            self.perf.set_report(None, [])
            self._prof_shown = None
            return
        rep_ = t.profile
        running = bool(rep_ and rep_.get("running"))
        now = _time.monotonic()
        if running and now - self._prof_asked > 1.0:
            self._prof_asked = now
            t.ask_profile("get")
        if t.profile_error:
            text = t.profile_error
        elif rep_ is None:
            text = "Start times the page's scans, expressions, functions and tasks."
        else:
            text = "%s %.1f s." % ("Timing:" if running else "Timed", float(rep_.get("seconds") or 0.0))
        self.perf.set_status(running, text)
        if rep_ is self._prof_shown:
            return  # (the same report: the views keep their state)
        self._prof_shown = rep_
        rows = []
        pages = self._window_pages(sp)
        for x in (rep_ or {}).get("expressions") or ():
            an = int(x.get("an") or 0)
            obj, kind, genie = self._profiled_object(pages, x.get("window"), an)
            rows.append(
                (
                    an,
                    obj,
                    kind,
                    genie,
                    animation_text(str(x.get("class") or "")),
                    str(x.get("page") or ""),
                    x.get("count"),
                    x.get("ms"),
                    x.get("avgUs"),
                    x.get("maxUs"),
                )
            )
        self.perf.set_report(rep_, rows)

    def _window_pages(self, sp) -> Dict[int, Any]:
        """{engine window: its page} for the tab and the windows shown with it."""
        out: Dict[int, Any] = {}
        for v in [sp] + [p for p in self._family() if p is not sp]:
            try:
                w = v.engine_window()
            except Exception:  # noqa: BLE001
                _note_exc("sim.inspector")
                continue
            if w is not None and getattr(v, "page", None) is not None:
                out.setdefault(int(w), v.page)
        return out

    def _profiled_object(self, pages, window, an: int):
        """(name, type, genie) of the object ``an`` in the page of engine window ``window``;
        blanks when that page is not shown here."""
        try:
            page = pages.get(int(window))
        except TypeError, ValueError:
            page = None
        if page is None:
            return "", "", ""
        cache = self._prof_index
        if len(cache) > 16:
            cache.clear()
        idx = cache.get(id(page))
        if idx is None or idx[0] is not page:
            idx = (
                page,
                {o.an: o for o in (getattr(page, "objects", None) or ()) if (getattr(o, "an", 0) or 0) > 0},
            )
            cache[id(page)] = idx
        o = idx[1].get(an)
        if o is None:
            return "", "", ""
        return (getattr(o, "name", "") or "", kind_text(o), owner_genie(page, o))

    def _inspect_profiled(self, an) -> None:
        if an:
            self.inspect(int(an))

    # -- Evaluate Parts, users, watches -------------------------------------------------------
    def _evaluate_parts(self) -> None:
        from .sim_tools import tools_of

        sp = self._tab()
        an = self._shown_an
        idx = self._index
        t = tools_of(sp.sim) if sp is not None else None
        if an is None or idx is None or an not in idx.by_an:
            self._set_note("Select an object first.")
            return
        if t is None or not t.available():
            self._set_note("The Cicode simulator does not run this page.")
            return
        parts: List[str] = []
        for v in (idx.props.get(an) or {}).values():
            for x in call_parts(v):
                if x not in parts:
                    parts.append(x)
        if not parts:
            self._set_note("The object's expressions call no function.")
            return
        self._parts_an, self._parts = an, parts
        for x in parts:
            with t._lock:
                t.calls.pop(x, None)
            t.call(x)
        self._set_note(
            "Evaluating %d call%s in window %s."
            % (len(parts), "" if len(parts) == 1 else "s", sp.engine_window())
        )
        QtCore.QTimer.singleShot(400, self, lambda: self._object_selected(an, keep=True))

    def _parts_lines(self, sp, an) -> List[str]:
        from .sim_tools import SimTools, tools_of

        if an != self._parts_an or not self._parts:
            return []
        t = tools_of(sp.sim)
        lines = ["Parts now (Evaluate Parts):"]
        for x in self._parts:
            o = t.outcome(x) if t is not None else None
            lines.append("  %s = %s" % (x, SimTools.outcome_text(o) if o is not None else "..."))
        return lines

    def _show_users(self, name) -> None:
        sp = self._tab()
        idx = self._index
        if sp is None or idx is None:
            return
        ans = idx.users_of(str(name))
        if not ans:
            self._set_note("No object of the page names %s." % name)
            return
        n = sp.canvas.show_objects(ans)
        self._set_note(
            "%d of the %d objects that name %s are marked on the page." % (n, len(ans), name)
            if n != len(ans)
            else "The %d object%s that name %s %s marked "
            "on the page." % (n, "" if n == 1 else "s", name, "is" if n == 1 else "are")
        )

    def _watch_tag(self, name) -> None:
        w = self.sim_window
        try:
            w.watch.add_watch(str(name))
            w.input_tabs.setCurrentWidget(w.watch)
            self._set_note("%s is in the Watch tab." % name)
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector._watch_tag")

    def _filter_changed(self, *_a) -> None:
        match = self.filter.matcher()
        for t in (self.objects, self.tags, self.assocs, self.vars, self.windows):
            t.proxy.set_needle(match)
        self.perf.set_matcher(match)
        self.objects.proxy.invalidateFilter()
        self._show_count()

    def _current_table(self) -> Optional[_Table]:
        """The current tab's list (None on the Performance tab: it counts and copies itself)."""
        k = max(0, self.tabs.currentIndex())
        return [self.objects, self.tags, self.assocs, self.vars, self.windows][k] if k < 5 else None

    def _show_count(self) -> None:
        t = self._current_table()
        if t is None:
            self.count.setText(self.perf.count_text())
            return
        total, shown = t.model.rowCount(), t.shown_count()
        self.count.setText(
            "%d of %d rows" % (shown, total)
            if shown != total
            else "%d row%s" % (total, "" if total == 1 else "s")
        )

    def _copy(self) -> None:
        t = self._current_table()
        n = t.copy_rows() if t is not None else self.perf.copy_rows()
        self._set_note("Copied %d row%s." % (n, "" if n == 1 else "s"))

    # -- the data --------------------------------------------------------------------------
    def _page_index(self, sp) -> Optional[PageIndex]:
        page = getattr(sp, "page", None)
        if page is None:
            return None
        if self._index is None or self._index_for is not page:
            self._index = PageIndex(page)
            self._index_for = page
        return self._index

    def refresh(self) -> None:
        sp = self._tab()
        if sp is None:
            self.setWindowTitle("Inspector")
            self._set_note("No page is simulated.", state=True)
            return
        self.setWindowTitle("Inspector - %s" % sp.title())
        idx = self._page_index(sp)
        sim = sp.sim
        paused = getattr(sim, "debug_paused", None)
        p = paused() if callable(paused) else None
        if p is not None:
            from .ctsim_source import debug_pause_where

            self._set_note(
                "Paused by the debugger in %s: the values are the ones before "
                "the stop." % debug_pause_where(p),
                "warn",
                state=True,
            )
        elif self._note_is_state:
            self._set_note("")
        k = self.tabs.currentIndex()
        try:
            if k == 0:
                self._fill_objects(sp, idx)
            elif k == 1:
                self._fill_tags(sp, idx)
            elif k == 2:
                self._fill_assocs(sp, idx)
            elif k == 3:
                self._fill_vars(sp, idx)
            elif k == 4:
                self._fill_windows(sp)
            else:
                self._fill_performance(sp)
        except Exception:  # noqa: BLE001 -- an inspector must not break the simulation
            _note_exc("sim.inspector.refresh")
        self._show_count()

    def _errors(self, sp) -> Dict[int, tuple]:
        try:
            return sp.canvas.errors()
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            return {}

    def _states(self, sp) -> Dict[int, Any]:
        """The object states drawn now, by AN (the scene's: every object's, not only the
        last change)."""
        fn = getattr(getattr(sp, "scene", None), "sim_states", None)
        try:
            return fn() if fn is not None else {}
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            return {}

    def _object_wanted(self, an) -> bool:
        if not self.errors_only.isChecked():
            return True
        sp = self._tab()
        if sp is None:
            return True
        st = self._states(sp).get(an)
        return an in self._errors(sp) or bool(st is not None and (st.error or st.indication))

    def _fill_objects(self, sp, idx: Optional[PageIndex]) -> None:
        if idx is None:
            self.objects.set_rows([], [])
            return
        states, errors = self._states(sp), self._errors(sp)
        rows, data = [], []
        for o in idx.objects:
            st = states.get(o.an)
            err = errors.get(o.an)
            err_text = ""
            if err:
                err_text = "%s%s" % (err[1], " (%d times)" % err[0] if err[0] > 1 else "")
            elif st is not None and st.error:
                err_text = st.error
            kind = getattr(getattr(o, "kind", None), "name", "") or ""
            if kind == "UNKNOWN":
                kind = ""
            genie = getattr(o, "genie_template", "") or ""
            rows.append(
                ("%d" % o.an, getattr(o, "name", "") or "", kind.title(), genie, _state_text(st), err_text)
            )
            data.append(o.an)
        self.objects.set_rows(rows, data)
        if self._shown_an is not None:
            self._object_selected(self._shown_an, keep=True)

    def _object_selected(self, an, keep: bool = False) -> None:
        sp = self._tab()
        idx = self._index
        if sp is None or idx is None or an is None or an not in idx.by_an:
            if not keep:
                self.details.setPlainText("")
            return
        self._shown_an = an
        o = idx.by_an[an]
        lines = [
            "AN %d  %s  %s"
            % (an, getattr(o, "name", "") or "(no name)", getattr(getattr(o, "kind", None), "name", ""))
        ]
        if getattr(o, "genie_template", ""):
            lines.append("Genie: %s" % o.genie_template)
        subs = getattr(o, "substitutions", None) or getattr(o, "genie_params", None) or {}
        if subs:
            lines.append("Genie values:")
            for k, v in sorted(subs.items()):
                lines.append("  %s = %s" % (k, v))
        props = idx.props.get(an) or {}
        if props:
            lines.append("Properties:")
            for k, v in props.items():
                lines.append("  %s = %s" % (k, v))
        tags = self._tags_named(sp, props)
        if tags:
            lines.append("Tags it names (value, quality):")
            for t in tags:
                lines.append(
                    "  %s = %s (%s)%s"
                    % (t.name, _text(t.value), t.quality, "" if t.defined else ", not defined")
                )
        ass = sorted({m.group(1) for v in props.values() for m in _ASS.finditer(v)})
        if ass:
            lines.append("Associations it uses:")
            have = self._window_assocs(sp)
            for a in ass:
                lines.append("  ?%s? = %s" % (a, have.get(a.lower(), "(not associated)")))
        st = self._states(sp).get(an)
        if st is not None:
            lines.append("State now: %s" % (_state_text(st) or "as drawn"))
            if st.tooltip:
                lines.append("Tool tip: %s" % st.tooltip)
        err = self._errors(sp).get(an)
        if err:
            lines.append("Error: %s" % err[1])
        lines += self._parts_lines(sp, an)
        text = "\n".join(lines)
        if text != self.details.toPlainText():
            bar = self.details.verticalScrollBar().value()
            self.details.setPlainText(text)
            self.details.verticalScrollBar().setValue(bar)

    def _tags_named(self, sp, props: Dict[str, str]):
        words = {m.group(0).lower() for v in props.values() for m in _WORD.finditer(v)}
        if not words:
            return []
        try:
            tags = sp.sim.tags(page_only=True)
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            return []
        return [t for t in tags if t.name.lower() in words]

    def _engine_windows(self, sp) -> list:
        """The simulation's open windows (the last windowsChanged): the page's own source's,
        else its tab's (a pane's source does not get the window list)."""
        for p in (sp, (self._family() or [None])[0]):
            fn = getattr(getattr(p, "sim", None), "engine_windows", None)
            if fn is None:
                continue
            try:
                _serial, wins = fn()
            except Exception:  # noqa: BLE001
                _note_exc("sim.inspector")
                continue
            if wins:
                return wins
        return []

    def _window_assocs(self, sp) -> Dict[str, str]:
        """{lower name: tag} of the inspected page's window's associations."""
        wins = self._engine_windows(sp)
        mine = sp.engine_window() if hasattr(sp, "engine_window") else None
        for w in wins:
            if w.get("window") == mine:
                a = w.get("associations")
                return {str(k).lower(): str(v) for k, v in a.items()} if isinstance(a, dict) else {}
        return {}

    def _fill_tags(self, sp, idx: Optional[PageIndex]) -> None:
        try:
            tags = sp.sim.tags(page_only=True)
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            tags = []
        rows, data = [], []
        defined = {t.name.lower() for t in tags if t.defined}
        for t in tags:
            if t.page_var or t.param:
                continue
            base = t.name.rsplit(".", 1)[0].lower() if "." in t.name else ""
            if not t.defined and base in defined:
                frm = "an element of %s" % t.name.rsplit(".", 1)[0]
            elif t.via:
                frm = "association ?%s?" % t.via
            elif not t.defined:
                frm = "simulated" if t.simulated else "not defined"
            elif "Cicode" in (t.comment or "") and not t.page_reads:
                frm = "Cicode"
            elif t.comment == "Used by the page's Cicode":
                frm = "Cicode at runtime"
            else:
                frm = "the page"
            users = idx.users_of(t.name) if idx is not None else []
            if not users and t.ref and idx is not None:
                users = sorted(idx.assoc.get((t.via or "").lower(), ()))
            rows.append(
                (
                    t.name,
                    _text(t.value),
                    t.quality or "",
                    t.type or "",
                    frm,
                    _ans_text(users),
                    t.equipment or "",
                    t.comment or "",
                )
            )
            data.append(t.name)
        self.tags.set_rows(rows, data)

    def _fill_assocs(self, sp, idx: Optional[PageIndex]) -> None:
        wins = self._engine_windows(sp)
        mine = sp.engine_window() if hasattr(sp, "engine_window") else None
        rows, data = [], []
        seen = set()
        for w in wins:
            a = w.get("associations")
            if not isinstance(a, dict):
                continue
            for k, v in sorted(a.items()):
                users = (
                    sorted(idx.assoc.get(str(k).lower(), ()))
                    if idx is not None and w.get("window") == mine
                    else []
                )
                literal = str(v).startswith("'")
                rows.append(
                    (
                        str(k),
                        '"%s"' % str(v)[1:] if literal else str(v),
                        _text(w.get("window")),
                        str(w.get("page") or ""),
                        _ans_text(users),
                        "literal" if literal else "",
                    )
                )
                data.append(users[0] if users else None)
                if w.get("window") == mine:
                    seen.add(str(k).lower())
        if idx is not None:
            for name, ans in sorted(idx.assoc.items()):
                if name in seen:
                    continue
                rows.append(
                    (
                        name,
                        "",
                        _text(mine),
                        sp.title(),
                        _ans_text(sorted(ans)),
                        "not associated in this window",
                    )
                )
                data.append(min(ans))
        self.assocs.set_rows(rows, data)

    def _fill_vars(self, sp, idx: Optional[PageIndex]) -> None:
        rows, data = [], []
        for getter, kind in (("page_vars", "page variable"), ("params", "parameter")):
            fn = getattr(sp.sim, getter, None)
            try:
                items = fn() if fn is not None else []
            except Exception:  # noqa: BLE001
                _note_exc("sim.inspector")
                items = []
            for t in items:
                rows.append(
                    (
                        t.name,
                        _text(t.value),
                        kind,
                        t.source if kind == "parameter" else (t.type or ""),
                        ", ".join(str(u) for u in (t.users or ()))[:200],
                    )
                )
                data.append(t.name)
        self.vars.set_rows(rows, data)

    def _fill_windows(self, sp) -> None:
        wins = self._engine_windows(sp)
        shown = {}
        for p in self.sim_window.pages():
            w = p.engine_window() if hasattr(p, "engine_window") else None
            if w is not None:
                shown[w] = {"tab": "a tab", "embedded": "a pane", "popup": "a popup"}.get(
                    getattr(p, "host", "tab"), str(getattr(p, "host", ""))
                )
        rows, data = [], []
        for w in wins:
            n = w.get("window")
            a = w.get("associations")
            parent = w.get("parent", -1)
            rows.append(
                (
                    _text(n),
                    str(w.get("page") or ""),
                    "" if parent in (None, -1) else _text(parent),
                    "%d" % len(a) if isinstance(a, dict) else "",
                    shown.get(n, "not shown here"),
                )
            )
            data.append(n)
        self.windows.set_rows(rows, data)

    # -- actions ---------------------------------------------------------------------------
    def _select_an(self, an: int) -> None:
        t = self.objects
        for r in range(t.proxy.rowCount()):
            ix = t.proxy.index(r, 0)
            if t.proxy.data(ix, QtCore.Qt.UserRole) == an:
                t.view.setCurrentIndex(ix)
                t.view.scrollTo(ix, QtWidgets.QAbstractItemView.PositionAtCenter)
                self._object_selected(an)
                return

    def _show_object(self, an) -> None:
        sp = self._tab()
        if sp is None or an is None:
            return
        if sp.canvas.show_object(int(an)):
            self._set_note("AN %d is marked on the page." % int(an))
        else:
            self._set_note("AN %d is not drawn now (hidden, or outside the page)." % int(an))

    def _show_first(self, an) -> None:
        if an is not None:
            self._show_object(an)

    def _select_tag(self, name) -> None:
        w = self.sim_window
        try:
            if w.inputs.select_tag(str(name)):
                w.input_tabs.setCurrentWidget(w.inputs)
                self._set_note("%s is selected in the Tags panel." % name)
                return
        except Exception:  # noqa: BLE001
            _note_exc("sim.inspector")
            pass
        self._set_note("%s is not in the Tags panel's list." % name)
