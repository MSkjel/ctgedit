"""The Inspector's Performance tab."""

from __future__ import annotations

from ..names import plural
from ..style.theme import sp as _sp

import os
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra.textmatch import TextMatch
from .. import tables
from ..style import theme

KINDS = {"cicode": "Cicode", "builtin": "Built-in", "origin": "", "other": ""}
SORT_ROLE = QtCore.Qt.UserRole + 7
SHARE_ROLE = QtCore.Qt.UserRole + 8  # 0..1: the bar drawn behind a number
NODE_ROLE = QtCore.Qt.UserRole + 9


def _f(v, d: int = 2) -> str:
    try:
        return ("%%.%df" % d) % float(v)
    except TypeError, ValueError:
        return ""


def _num(v) -> float:
    try:
        return float(v)
    except TypeError, ValueError:
        return 0.0


# -- the call tree's data ------------------------------------------------------------------------
def prepare(tree: list) -> list:
    """The engine's tree with each node's parent and depth (``_up``, ``_depth``)."""

    def walk(n, up, depth):
        n["_up"] = up
        n["_depth"] = depth
        for c in n.get("children") or ():
            walk(c, n, depth + 1)

    for r in tree or ():
        walk(r, None, 0)
    return list(tree or ())


def path_of(n: dict) -> List[str]:
    out = []
    while n is not None:
        out.append(str(n.get("name") or ""))
        n = n.get("_up")
    return list(reversed(out))


def _occurrences(tree: list, name: str) -> List[dict]:
    """The nodes of function ``name`` that are not inside another node of it (its outermost
    calls: a recursion is counted once)."""
    out: List[dict] = []
    low = name.lower()

    def walk(n, inside):
        here = str(n.get("name") or "").lower() == low and n.get("kind") != "origin"
        if here and not inside:
            out.append(n)
        for c in n.get("children") or ():
            walk(c, inside or here)

    for r in tree:
        walk(r, False)
    return out


def _merge_into(dst: dict, src: dict) -> None:
    for k in ("ms", "selfMs", "calls"):
        dst[k] = _num(dst.get(k)) + _num(src.get(k))
    kids = dst.setdefault("children", [])
    by = {str(c.get("name")).lower(): c for c in kids}
    for c in src.get("children") or ():
        key = str(c.get("name")).lower()
        m = by.get(key)
        if m is None:
            m = {
                "name": c.get("name"),
                "kind": c.get("kind"),
                "ms": 0.0,
                "selfMs": 0.0,
                "calls": 0,
                "children": [],
            }
            kids.append(m)
            by[key] = m
        _merge_into(m, c)


def _sort_tree(n: dict) -> None:
    kids = n.get("children") or []
    kids.sort(key=lambda c: -_num(c.get("ms")))
    for c in kids:
        _sort_tree(c)


def merged_calls(tree: list, name: str) -> Optional[dict]:
    """Function ``name`` with everything it calls, all its calls together (one tree)."""
    occ = _occurrences(tree, name)
    if not occ:
        return None
    root = {
        "name": occ[0].get("name"),
        "kind": occ[0].get("kind"),
        "ms": 0.0,
        "selfMs": 0.0,
        "calls": 0,
        "children": [],
    }
    for n in occ:
        _merge_into(root, n)
    _sort_tree(root)
    prepare([root])
    return root


def callers_of(tree: list, name: str) -> Optional[dict]:
    """Who calls function ``name``: a tree from it up to who runs the code, each caller with
    the time of ``name`` called through it."""
    occ = _occurrences(tree, name)
    if not occ:
        return None
    root = {
        "name": occ[0].get("name"),
        "kind": occ[0].get("kind"),
        "ms": 0.0,
        "selfMs": 0.0,
        "calls": 0,
        "children": [],
    }
    for n in occ:
        ms, calls = _num(n.get("ms")), _num(n.get("calls"))
        root["ms"] += ms
        root["calls"] += calls
        at = root
        up = n.get("_up")
        while up is not None:
            key = str(up.get("name")).lower()
            nxt = next((c for c in at["children"] if str(c.get("name")).lower() == key), None)
            if nxt is None:
                nxt = {
                    "name": up.get("name"),
                    "kind": up.get("kind"),
                    "ms": 0.0,
                    "selfMs": 0.0,
                    "calls": 0,
                    "children": [],
                }
                at["children"].append(nxt)
            nxt["ms"] += ms
            nxt["calls"] += calls
            at = nxt
            up = up.get("_up")
    _sort_tree(root)
    prepare([root])
    return root


def tree_total(tree: list) -> float:
    return sum(_num(r.get("ms")) for r in tree or ())


# -- items that sort by number and draw a share bar -----------------------------------------------
class _Item(QtGui.QStandardItem):
    def __init__(self, text: str = "", sort=None, share: Optional[float] = None, tip: str = ""):
        super().__init__(text)
        self.setEditable(False)
        if sort is not None:
            self.setData(sort, SORT_ROLE)
            self.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if share is not None:
            self.setData(max(0.0, min(1.0, share)), SHARE_ROLE)
        if tip:
            self.setToolTip(tip)

    def __lt__(self, other) -> bool:
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return (self.text() or "").lower() < (other.text() or "").lower()


class _ShareDelegate(QtWidgets.QStyledItemDelegate):
    """A bar behind a cell whose item has a share (SHARE_ROLE)."""

    def paint(self, painter, option, index):
        share = index.data(SHARE_ROLE)
        if share:
            r = option.rect.adjusted(2, 3, -2, -3)
            w = int(r.width() * float(share))
            if w > 0:
                c = QtGui.QColor(theme.COLORS.get("accent", "#3d8fd1"))
                c.setAlpha(70)
                painter.fillRect(QtCore.QRect(r.left(), r.top(), w, r.height()), c)
        super().paint(painter, option, index)


def _view(name: str, tree: bool = False) -> QtWidgets.QTreeView:
    v = QtWidgets.QTreeView()
    v.setAccessibleName(name)
    v.setUniformRowHeights(True)
    v.setAlternatingRowColors(True)
    v.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    v.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    v.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    v.setSortingEnabled(not tree)
    v.setRootIsDecorated(tree)
    v.setItemDelegate(_ShareDelegate(v))
    v.header().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    v.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    v.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    return v


def _model(headers, parent) -> QtGui.QStandardItemModel:
    m = QtGui.QStandardItemModel(0, len(headers), parent)
    m.setHorizontalHeaderLabels(list(headers))
    m.setSortRole(QtCore.Qt.DisplayRole)
    return m


# -- the flame graph -------------------------------------------------------------------------------
class FlameGraph(QtWidgets.QWidget):
    """The call tree as nested bars, each as wide as its time (see the module)."""

    #: right-click > Focus on this function: the function's name
    focusRequested = QtCore.Signal(str)

    ROW = 20
    CRUMB = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAccessibleName("Flame graph")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._roots: List[dict] = []
        self._zoom: Optional[dict] = None
        self._boxes: List[tuple] = []  # (QRectF, node)
        self._crumbs: List[tuple] = []  # (QRectF, node or None)
        self._hover: Optional[dict] = None
        self._match = TextMatch("")
        self._total = 0.0
        self.empty_text = "Start the profiler to see where the time goes."

    def set_tree(self, roots: List[dict], keep_zoom: bool = True) -> None:
        old = path_of(self._zoom) if (keep_zoom and self._zoom is not None) else None
        self._roots = list(roots or ())
        self._total = tree_total(self._roots)
        self._zoom = self._find(old) if old else None
        self._hover = None
        self._layout()
        self.update()

    def set_matcher(self, m: TextMatch) -> None:
        self._match = m
        self.update()

    def _find(self, path: List[str]) -> Optional[dict]:
        level = self._roots
        node = None
        for name in path:
            node = next((n for n in level if n.get("name") == name), None)
            if node is None:
                return None
            level = node.get("children") or []
        return node

    def zoom_to(self, node: Optional[dict]) -> None:
        self._zoom = node
        self._layout()
        self.update()

    def _depth_of(self, nodes) -> int:
        return max([1 + self._depth_of(n.get("children") or []) for n in nodes] or [0])

    def _layout(self) -> None:
        nodes = [self._zoom] if self._zoom is not None else self._roots
        rows = self._depth_of(nodes)
        h = self.CRUMB + max(1, rows) * self.ROW + 8
        self.setMinimumHeight(h)
        self._boxes = []
        w = max(1, self.width() - 2)
        total = sum(_num(n.get("ms")) for n in nodes) or 1.0

        def place(n, x, width, depth):
            if width < 0.5:
                return
            r = QtCore.QRectF(1 + x, self.CRUMB + depth * self.ROW, width, self.ROW - 1)
            self._boxes.append((r, n))
            cx = x
            ms = _num(n.get("ms")) or 1e-12
            for c in n.get("children") or ():
                cw = width * (_num(c.get("ms")) / ms)
                place(c, cx, cw, depth + 1)
                cx += cw

        x = 0.0
        for n in nodes:
            nw = w * (_num(n.get("ms")) / total)
            place(n, x, nw, 0)
            x += nw

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self._layout()

    @staticmethod
    def _colour(n: dict) -> QtGui.QColor:
        kind = n.get("kind")
        if kind == "origin":
            return QtGui.QColor(110, 118, 128)
        if kind == "other":
            return QtGui.QColor(85, 90, 98)
        h = sum(ord(ch) for ch in str(n.get("name") or "")) % 40
        if kind == "builtin":
            return QtGui.QColor.fromHsv(200 + h, 120, 200)
        return QtGui.QColor.fromHsv(15 + h, 170, 230)

    def paintEvent(self, ev):  # noqa: N802 -- Qt naming
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.COLORS.get("bg", "#1e2126")))
        fm = p.fontMetrics()
        text_c = QtGui.QColor(theme.COLORS.get("text", "#eeeeee"))
        if not self._roots:
            p.setPen(text_c)
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, self.empty_text)
            return
        # the path the view is zoomed into (click a step to go back)
        self._crumbs = []
        x = 6.0
        steps = [(None, "All")] + [(n, n.get("name")) for n in self._chain()]
        for k, (node, label) in enumerate(steps):
            t = str(label) + (" >" if k < len(steps) - 1 else "")
            wt = fm.horizontalAdvance(t) + 10
            r = QtCore.QRectF(x, 3, wt, self.CRUMB - 6)
            self._crumbs.append((r, node))
            p.setPen(text_c if k == len(steps) - 1 else QtGui.QColor(theme.COLORS.get("accent", "#3d8fd1")))
            p.drawText(r, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, t)
            x += wt
        searching = not self._match.empty
        hit_c = QtGui.QColor(theme.COLORS.get("warn", "#d9a441"))
        for r, n in self._boxes:
            c = self._colour(n)
            if searching:
                # a match in the highlight colour (it may be a thin bar), the rest dimmed
                c = hit_c if self._match(str(n.get("name") or "")) else c.darker(300)
            if n is self._hover:
                c = c.lighter(125)
            p.fillRect(r, c)
            p.setPen(QtGui.QColor(0, 0, 0, 90))
            p.drawRect(r)
            if r.width() > 28:
                label = fm.elidedText(str(n.get("name") or ""), QtCore.Qt.ElideRight, int(r.width() - 6))
                p.setPen(QtGui.QColor(20, 20, 20))
                p.drawText(r.adjusted(3, 0, -3, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)

    def match_summary(self) -> str:
        """'4 matching calls: 12.5 ms (3.1 % of all)' for the search ('' without one). A
        match inside another match is counted in the outer one."""
        if self._match.empty or not self._roots:
            return ""
        n_hit, ms = 0, 0.0

        def walk(n, inside):
            nonlocal n_hit, ms
            hit = n.get("kind") != "origin" and self._match(str(n.get("name") or ""))
            if hit:
                n_hit += 1
                if not inside:
                    ms += _num(n.get("ms"))
            for c in n.get("children") or ():
                walk(c, inside or hit)

        for r in [self._zoom] if self._zoom is not None else self._roots:
            walk(r, False)
        if not n_hit:
            return "No call matches the search."
        share = ms / self._total * 100 if self._total else 0.0
        return "%d matching call%s: %s ms (%.1f %% of all), in the highlight colour." % (
            n_hit,
            "" if n_hit == 1 else "s",
            _f(ms, 3),
            share,
        )

    def _chain(self) -> List[dict]:
        out = []
        n = self._zoom
        while n is not None:
            out.append(n)
            n = n.get("_up")
        return list(reversed(out))

    def _at(self, pos) -> Optional[dict]:
        for r, n in self._boxes:
            if r.contains(QtCore.QPointF(pos)):
                return n
        return None

    def mouseMoveEvent(self, ev):  # noqa: N802 -- Qt naming
        n = self._at(ev.position())
        if n is not self._hover:
            self._hover = n
            self.update()
        if n is not None:
            ms = _num(n.get("ms"))
            share = ms / self._total * 100 if self._total else 0.0
            kind = KINDS.get(n.get("kind"), "")
            tip = "<b>%s</b>%s<br>%s ms total (%.1f %% of all), %s ms own, %d calls<br>%s" % (
                n.get("name"),
                (" (%s)" % kind) if kind else "",
                _f(ms, 3),
                share,
                _f(n.get("selfMs"), 3),
                int(_num(n.get("calls"))),
                " > ".join(path_of(n)),
            )
            QtWidgets.QToolTip.showText(ev.globalPosition().toPoint(), tip, self)
        else:
            QtWidgets.QToolTip.hideText()

    def leaveEvent(self, ev):  # noqa: N802 -- Qt naming
        self._hover = None
        self.update()

    def mousePressEvent(self, ev):  # noqa: N802 -- Qt naming
        if ev.button() == QtCore.Qt.LeftButton:
            for r, node in self._crumbs:
                if r.contains(ev.position()):
                    self.zoom_to(node)
                    return
            n = self._at(ev.position())
            if n is not None and n.get("children"):
                self.zoom_to(n)
        elif ev.button() == QtCore.Qt.RightButton:
            n = self._at(ev.position())
            if n is not None and n.get("kind") not in ("origin", "other"):
                m = QtWidgets.QMenu(self)
                m.addAction("Focus on %s" % n.get("name")).triggered.connect(
                    lambda _c=False, name=str(n.get("name")): self.focusRequested.emit(name)
                )
                if n.get("children"):
                    m.addAction("Zoom Into It").triggered.connect(lambda _c=False, n=n: self.zoom_to(n))
                m.exec(ev.globalPosition().toPoint())

    def keyPressEvent(self, ev):  # noqa: N802 -- Qt naming
        if ev.key() in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Escape) and self._zoom is not None:
            self.zoom_to(self._zoom.get("_up"))
            return
        super().keyPressEvent(ev)


# -- the tab -----------------------------------------------------------------------------------------
class PerformanceView(QtWidgets.QWidget):
    """The Performance tab (see the module). The Inspector gives it the profile
    (:meth:`set_report`) and the search (:meth:`set_matcher`), and asks the profiler through
    ``profile(action)``."""

    VIEWS = ("Functions", "Call tree", "Flame graph", "Expressions", "Scans and tasks")

    def __init__(
        self,
        profile: Callable[[str], None],
        show_object: Callable[[int], None],
        inspect_object: Callable[[int], None],
        parent=None,
    ):
        super().__init__(parent)
        self._profile = profile
        self._show_object = show_object
        self._inspect_object = inspect_object
        self._report: Optional[dict] = None
        self._tree: List[dict] = []
        self._focus = ""
        self._match = TextMatch("")
        self._expr_rows: List[tuple] = []
        #: the call tree's nodes by the number its row holds (a node is a dict that points at
        #: its parent: Qt would follow it for ever converting it to a variant)
        self._nodes: Dict[int, dict] = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, _sp(1), 0, 0)
        lay.setSpacing(_sp(1))
        self._build_controls(lay)
        self._build_functions_view()
        self._build_call_tree_view()
        self._build_flame_view()
        self._build_expressions_view()
        self._build_scans_view()

    def _build_controls(self, lay: QtWidgets.QVBoxLayout) -> None:
        """Start, Stop and Reset with the note, the views' switch, the bar of the function
        in focus and the views' stack."""
        # Start / Stop / Reset, what was timed, the views
        bar = QtWidgets.QHBoxLayout()
        self.start = QtWidgets.QPushButton("Start")
        self.start.setToolTip(
            "Time the page's code from now: its scans, expressions, Cicode functions and tasks"
        )
        self.stop = QtWidgets.QPushButton("Stop")
        self.stop.setToolTip("Stop timing; what was timed stays")
        self.reset = QtWidgets.QPushButton("Reset")
        self.reset.setToolTip("Forget what was timed")
        for b, action in ((self.start, "start"), (self.stop, "stop"), (self.reset, "reset")):
            b.setAutoDefault(False)
            b.clicked.connect(lambda _c=False, a=action: self._profile(a))
            bar.addWidget(b)
        self.note = theme.note_label("Start times the page's scans, expressions, functions and tasks.")
        bar.addWidget(self.note, 1)
        lay.addLayout(bar)
        self.switch = QtWidgets.QTabBar()
        self.switch.setDocumentMode(True)
        self.switch.setExpanding(False)
        for v in self.VIEWS:
            self.switch.addTab(v)
        self.switch.setAccessibleName("Performance view")
        lay.addWidget(self.switch)
        # the function in focus
        self.focus_bar = QtWidgets.QFrame()
        fb = QtWidgets.QHBoxLayout(self.focus_bar)
        fb.setContentsMargins(_sp(1), _sp(0), _sp(1), _sp(0))
        self.focus_text = QtWidgets.QLabel("")
        self.focus_text.setWordWrap(True)
        self.focus_clear = QtWidgets.QPushButton("Show All")
        self.focus_clear.setAutoDefault(False)
        self.focus_clear.clicked.connect(lambda: self.set_focus(""))
        fb.addWidget(self.focus_text, 1)
        fb.addWidget(self.focus_clear)
        self.focus_bar.hide()
        lay.addWidget(self.focus_bar)
        self.stack = QtWidgets.QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.switch.currentChanged.connect(self.stack.setCurrentIndex)

    def _build_functions_view(self) -> None:
        """The Functions view: a row per function, the longest own time first."""
        # Functions
        self.f_headers = (
            "Function",
            "Kind",
            "Calls",
            "Total ms",
            "Own ms",
            "Own %",
            "Average us",
            "Longest us",
            "Called by",
            "File",
        )
        self.funcs = _view("Functions")
        self.f_model = _model(self.f_headers, self)
        self.funcs.setModel(self.f_model)
        self.funcs.sortByColumn(4, QtCore.Qt.DescendingOrder)
        self.f_model.setSortRole(SORT_ROLE)
        tables.make_resizable(
            self.funcs,
            "Performance/functions",
            stretch=0,
            caps={8: 0.3, 9: 0.2},
            squeeze={8, 9},
            fit_view=True,
        )
        self.funcs.doubleClicked.connect(lambda ix: self._focus_row(self.funcs, ix))
        self.funcs.customContextMenuRequested.connect(lambda pos: self._row_menu(self.funcs, pos))
        self.stack.addWidget(self.funcs)

    def _build_call_tree_view(self) -> None:
        """The Call tree view."""
        # Call tree
        self.t_headers = ("Function", "Total ms", "Total %", "Own ms", "Calls")
        self.tree = _view("Call tree", tree=True)
        self.t_model = _model(self.t_headers, self)
        self.tree.setModel(self.t_model)
        tables.make_resizable(self.tree, "Performance/tree", stretch=0, fit_view=True)
        self.tree.doubleClicked.connect(lambda ix: self._focus_row(self.tree, ix))
        self.tree.customContextMenuRequested.connect(lambda pos: self._row_menu(self.tree, pos))
        self.stack.addWidget(self.tree)

    def _build_flame_view(self) -> None:
        """The Flame graph view with its notes."""
        # Flame graph
        self.flame = FlameGraph()
        self.flame.focusRequested.connect(self.set_focus)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(self.flame)
        fw = QtWidgets.QWidget()
        fl = QtWidgets.QVBoxLayout(fw)
        fl.setContentsMargins(0, 0, 0, 0)
        self.flame_match = theme.note_label("", "warn")
        self.flame_match.hide()
        fl.addWidget(self.flame_match)
        fl.addWidget(scroll, 1)
        fl.addWidget(
            theme.note_label(
                "Each bar is a function, as wide as its time, under the function that called it. "
                "Click a bar to zoom into it, a step at the top (or Backspace) to go back; "
                "right-click to focus on its function."
            )
        )
        self.stack.addWidget(fw)

    def _build_expressions_view(self) -> None:
        """The Expressions view: a row per animation expression, the longest total time
        first."""
        # Expressions
        self.e_headers = (
            "AN",
            "Object",
            "Type",
            "Genie",
            "Animation",
            "Page",
            "Runs",
            "Total ms",
            "Average us",
            "Longest us",
        )
        self.exprs = _view("Expressions")
        self.e_model = _model(self.e_headers, self)
        self.exprs.setModel(self.e_model)
        self.e_model.setSortRole(SORT_ROLE)
        self.exprs.sortByColumn(7, QtCore.Qt.DescendingOrder)
        tables.make_resizable(
            self.exprs,
            "Performance/expressions",
            stretch=1,
            caps={3: 0.25, 4: 0.2},
            squeeze={2, 3, 4, 5},
            fit_view=True,
        )
        self.exprs.doubleClicked.connect(lambda ix: self._expr_an(ix, self._show_object))
        self.exprs.customContextMenuRequested.connect(self._expr_menu)
        self.stack.addWidget(self.exprs)

    def _build_scans_view(self) -> None:
        """The Scans and tasks view: the page scans over the tasks."""
        # Scans and tasks
        self.s_headers = ("Page", "Window", "Scans", "Total ms", "Average ms", "Longest ms")
        self.scans = _view("Scans")
        self.s_model = _model(self.s_headers, self)
        self.scans.setModel(self.s_model)
        self.s_model.setSortRole(SORT_ROLE)
        tables.make_resizable(self.scans, "Performance/scans", stretch=0, fit_view=True)
        self.k_headers = ("Task", "Slices", "Total ms", "Average us", "Longest us")
        self.tasks = _view("Tasks")
        self.k_model = _model(self.k_headers, self)
        self.tasks.setModel(self.k_model)
        self.k_model.setSortRole(SORT_ROLE)
        self.tasks.sortByColumn(2, QtCore.Qt.DescendingOrder)
        tables.make_resizable(self.tasks, "Performance/tasks", stretch=0, fit_view=True)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        for v in (self.scans, self.tasks):
            split.addWidget(v)
        split.setStretchFactor(1, 2)
        self.stack.addWidget(split)

    # -- input ----------------------------------------------------------------------------------
    def set_status(self, running: bool, text: str) -> None:
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        self.note.setText(text)

    def set_matcher(self, m: TextMatch) -> None:
        self._match = m
        self.flame.set_matcher(m)
        self._fill()

    def set_report(self, report: Optional[dict], expr_rows: List[tuple]) -> None:
        """The profiler's report and the expression rows (the Inspector names the objects:
        (AN, object, type, genie, animation, page, runs, total ms, average us, longest us))."""
        self._report = report
        self._expr_rows = list(expr_rows or ())
        self._tree = prepare((report or {}).get("tree") or [])
        self._fill()

    def set_focus(self, name: str) -> None:
        """Show only function ``name`` ('' = everything again)."""
        self._focus = name or ""
        if self._focus:
            self.focus_text.setText(
                "Focused on <b>%s</b>: the Call tree shows what it calls (all its calls "
                "together) and who calls it, the Flame graph and Functions what it calls." % self._focus
            )
        self.focus_bar.setVisible(bool(self._focus))
        self.flame.zoom_to(None)
        self._fill()

    # -- filling --------------------------------------------------------------------------------
    def _fill(self) -> None:
        rep = self._report or {}
        self._fill_functions(rep)
        self._fill_tree()
        self._fill_flame()
        self._fill_expressions()
        self._fill_scans(rep)

    def _keep(self, *texts) -> bool:
        return self._match.empty or self._match(*texts)

    @staticmethod
    def _refill(view, model, rows: List[List[QtGui.QStandardItem]], key_col: int = 0) -> None:
        """Replace the rows, keeping the selection and the sort."""
        sel = (
            {model.index(ix.row(), key_col).data() for ix in view.selectionModel().selectedRows()}
            if view.selectionModel()
            else set()
        )
        bar = view.verticalScrollBar().value()
        model.removeRows(0, model.rowCount())
        for r in rows:
            model.appendRow(r)
        hdr = view.header()
        if view.isSortingEnabled():
            model.sort(hdr.sortIndicatorSection(), hdr.sortIndicatorOrder())
        if sel:
            sm = view.selectionModel()
            for k in range(model.rowCount()):
                if model.index(k, key_col).data() in sel:
                    sm.select(
                        model.index(k, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows
                    )
        view.verticalScrollBar().setValue(bar)

    def _function_rows(self, rep: dict) -> list:
        funcs = list(rep.get("functions") or ())
        if self._focus and self._tree:
            merged = merged_calls(self._tree, self._focus)
            names = set()

            def walk(n):
                names.add(str(n.get("name")).lower())
                for c in n.get("children") or ():
                    walk(c)

            if merged is not None:
                walk(merged)
            funcs = [x for x in funcs if str(x.get("name")).lower() in names]
        return funcs

    def _fill_functions(self, rep: dict) -> None:
        funcs = self._function_rows(rep)
        own_total = sum(_num(x.get("selfMs")) for x in funcs) or 1.0
        rows = []
        for x in funcs:
            callers = ", ".join("%s (%s)" % (c.get("name"), c.get("calls")) for c in (x.get("callers") or ()))
            name = str(x.get("name") or "")
            kind = "Built-in" if x.get("builtin") else "Cicode"
            file = os.path.basename(str(x.get("file") or ""))
            if not self._keep(name, kind, callers, file):
                continue
            own = _num(x.get("selfMs"))
            rows.append(
                [
                    _Item(name, tip=str(x.get("file") or "")),
                    _Item(kind),
                    _Item(str(x.get("calls")), sort=_num(x.get("calls"))),
                    _Item(_f(x.get("ms"), 3), sort=_num(x.get("ms"))),
                    _Item(_f(own, 3), sort=own),
                    _Item("%.1f" % (own / own_total * 100), sort=own, share=own / own_total),
                    _Item(_f(x.get("avgUs"), 1), sort=_num(x.get("avgUs"))),
                    _Item(_f(x.get("maxUs"), 1), sort=_num(x.get("maxUs"))),
                    _Item(callers, tip=callers),
                    _Item(file, tip=str(x.get("file") or "")),
                ]
            )
        self._refill(self.funcs, self.f_model, rows)

    def _tree_roots(self) -> List[dict]:
        if not self._focus:
            return self._tree
        out = []
        calls = merged_calls(self._tree, self._focus)
        if calls is not None:
            calls = dict(calls, name="%s: what it calls" % calls.get("name"), _fn=calls.get("name"))
            out.append(calls)
        who = callers_of(self._tree, self._focus)
        if who is not None:
            who = dict(who, name="%s: who calls it" % who.get("name"), _fn=who.get("name"))
            out.append(who)
        return out

    def _fill_tree(self) -> None:
        roots = self._tree_roots()
        total = tree_total(roots if not self._focus else roots[:1]) or 1.0
        expanded = self._expanded_paths()
        self.t_model.removeRows(0, self.t_model.rowCount())
        self._nodes = {}
        searching = not self._match.empty

        def keep(n) -> bool:
            if not searching:
                return True
            if self._match(str(n.get("name") or "")):
                return True
            return any(keep(c) for c in n.get("children") or ())

        def add(parent, n, depth):
            if not keep(n):
                return
            ms = _num(n.get("ms"))
            kind = KINDS.get(n.get("kind"), "")
            name = str(n.get("name") or "")
            it = _Item(name, tip="%s%s" % (name, (" (%s)" % kind) if kind else ""))
            key = len(self._nodes) + 1
            self._nodes[key] = n
            it.setData(key, NODE_ROLE)
            if n.get("kind") == "builtin":
                it.setForeground(QtGui.QColor(theme.COLORS.get("text_dim", "#9aa0a6")))
            row = [
                it,
                _Item(_f(ms, 3), sort=ms),
                _Item("%.1f" % (ms / total * 100), sort=ms, share=ms / total),
                _Item(_f(n.get("selfMs"), 3), sort=_num(n.get("selfMs"))),
                _Item(str(int(_num(n.get("calls")))), sort=_num(n.get("calls"))),
            ]
            parent.appendRow(row)
            for c in n.get("children") or ():
                add(it, c, depth + 1)

        for r in roots:
            add(self.t_model.invisibleRootItem(), r, 0)
        # (a focused tree is small: all of it open)
        self._expand(expanded, searching or bool(self._focus))

    def _expanded_paths(self) -> set:
        out = set()

        def walk(parent_ix, path):
            for k in range(self.t_model.rowCount(parent_ix)):
                ix = self.t_model.index(k, 0, parent_ix)
                p = path + (ix.data(),)
                if self.tree.isExpanded(ix):
                    out.add(p)
                    walk(ix, p)

        walk(QtCore.QModelIndex(), ())
        return out

    def _expand(self, paths: set, all_open: bool) -> None:
        """The paths open before stay open; a first fill opens the costliest path (each
        level's child with at least half of its parent's time); a search opens them all."""
        first = not paths

        def walk(parent_ix, path, hot):
            for k in range(self.t_model.rowCount(parent_ix)):
                ix = self.t_model.index(k, 0, parent_ix)
                p = path + (ix.data(),)
                if all_open or p in paths or (first and hot and k == 0):
                    self.tree.setExpanded(ix, True)
                    n = self._nodes.get(ix.data(NODE_ROLE) or 0) or {}
                    kids = n.get("children") or []
                    big = bool(kids) and _num(kids[0].get("ms")) >= 0.5 * _num(n.get("ms"))
                    walk(ix, p, hot and big)

        walk(QtCore.QModelIndex(), (), True)

    def _fill_flame(self) -> None:
        if self._focus:
            m = merged_calls(self._tree, self._focus)
            self.flame.set_tree([m] if m is not None else [])
        else:
            self.flame.set_tree(self._tree)
        text = self.flame.match_summary()
        self.flame_match.setText(text)
        self.flame_match.setVisible(bool(text))
        self.flame.empty_text = "Start the profiler to see where the time goes."

    def _fill_expressions(self) -> None:
        rows = []
        for r in self._expr_rows:
            if not self._keep(*[str(x) for x in r]):
                continue
            an, obj, kind, genie, anim, page, runs, ms, avg, mx = r
            item = _Item(str(an), sort=_num(an))
            item.setData(int(_num(an)), NODE_ROLE)
            rows.append(
                [
                    item,
                    _Item(obj),
                    _Item(kind),
                    _Item(genie, tip=genie),
                    _Item(anim),
                    _Item(page),
                    _Item(str(runs), sort=_num(runs)),
                    _Item(_f(ms, 3), sort=_num(ms)),
                    _Item(_f(avg, 1), sort=_num(avg)),
                    _Item(_f(mx, 1), sort=_num(mx)),
                ]
            )
        self._refill(self.exprs, self.e_model, rows)

    def _fill_scans(self, rep: dict) -> None:
        rows = []
        for x in rep.get("scans") or ():
            page = str(x.get("page") or "")
            if not self._keep(page):
                continue
            rows.append(
                [
                    _Item(page),
                    _Item(str(x.get("window")), sort=_num(x.get("window"))),
                    _Item(str(x.get("count")), sort=_num(x.get("count"))),
                    _Item(_f(x.get("ms")), sort=_num(x.get("ms"))),
                    _Item(_f(_num(x.get("avgUs")) / 1000.0, 3), sort=_num(x.get("avgUs"))),
                    _Item(_f(_num(x.get("maxUs")) / 1000.0, 3), sort=_num(x.get("maxUs"))),
                ]
            )
        self._refill(self.scans, self.s_model, rows)
        rows = []
        for x in rep.get("tasks") or ():
            name = str(x.get("name") or "")
            if not self._keep(name):
                continue
            rows.append(
                [
                    _Item(name),
                    _Item(str(x.get("count")), sort=_num(x.get("count"))),
                    _Item(_f(x.get("ms"), 3), sort=_num(x.get("ms"))),
                    _Item(_f(x.get("avgUs"), 1), sort=_num(x.get("avgUs"))),
                    _Item(_f(x.get("maxUs"), 1), sort=_num(x.get("maxUs"))),
                ]
            )
        self._refill(self.tasks, self.k_model, rows)

    # -- menus ----------------------------------------------------------------------------------
    def _name_at(self, view, ix) -> str:
        if not ix.isValid():
            return ""
        n = view.model().index(ix.row(), 0, ix.parent())
        node = self._nodes.get(n.data(NODE_ROLE) or 0) if view is self.tree else None
        if isinstance(node, dict):
            if node.get("kind") in ("origin", "other"):
                return ""
            return str(node.get("_fn") or node.get("name") or "")
        return str(n.data() or "")

    def _focus_row(self, view, ix) -> None:
        name = self._name_at(view, ix)
        if name:
            self.set_focus(name)

    def _row_menu(self, view, pos) -> None:
        ix = view.indexAt(pos)
        name = self._name_at(view, ix)
        m = QtWidgets.QMenu(self)
        if name:
            m.addAction("Focus on %s" % name).triggered.connect(lambda _c=False: self.set_focus(name))
            m.addAction("Show in the Flame Graph").triggered.connect(
                lambda _c=False: (self.set_focus(name), self.switch.setCurrentIndex(2))
            )
        if self._focus:
            m.addAction("Show All").triggered.connect(lambda _c=False: self.set_focus(""))
        if view is self.tree:
            m.addSeparator()
            m.addAction("Expand All").triggered.connect(self.tree.expandAll)
            m.addAction("Collapse All").triggered.connect(self.tree.collapseAll)
        m.addSeparator()
        m.addAction("Copy").triggered.connect(lambda _c=False: self.copy_rows())
        m.exec(view.viewport().mapToGlobal(pos))

    def _expr_an(self, ix, fn) -> None:
        if ix.isValid():
            an = self.e_model.index(ix.row(), 0).data(NODE_ROLE)
            if an:
                fn(int(an))

    def _expr_menu(self, pos) -> None:
        ix = self.exprs.indexAt(pos)
        m = QtWidgets.QMenu(self)
        if ix.isValid():
            m.addAction("Show on Page").triggered.connect(
                lambda _c=False: self._expr_an(ix, self._show_object)
            )
            m.addAction("Inspect Object").triggered.connect(
                lambda _c=False: self._expr_an(ix, self._inspect_object)
            )
            m.addSeparator()
        m.addAction("Copy").triggered.connect(lambda _c=False: self.copy_rows())
        m.exec(self.exprs.viewport().mapToGlobal(pos))

    # -- count and copy (the Inspector's) --------------------------------------------------------
    def _current_view(self):
        return [self.funcs, self.tree, None, self.exprs, self.tasks][self.switch.currentIndex()]

    def count_text(self) -> str:
        v = self._current_view()
        if v is None:
            n = len(self.flame._boxes)
            return plural(n, "bar")
        return "%d" % v.model().rowCount()

    def copy_rows(self) -> int:
        """The selected rows (all shown ones when none is selected) of the view shown, tab
        separated with the column names first; the Flame graph copies the Functions list."""
        from ..tables import selected_rows_text

        text = selected_rows_text(self._current_view() or self.funcs, headers=True, all_if_none=True)
        QtGui.QGuiApplication.clipboard().setText(text)
        return text.count("\n")
