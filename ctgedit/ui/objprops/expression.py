"""The expression field and its Wizard."""

from __future__ import annotations

import bisect
import os
import re
from typing import List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ... import cicode_names as _cicode_names
from ..panels import panel_options as _panels
from . import validators as V
from .range_field import RangeField, default_range, join_range, split_range

# -- context -------------------------------------------------------------------
#: (project dir, extra names) -> [tag index, names]
_NAMES_CACHE: dict = {}
#: scope key -> the TagCompletionModel every field of the scope shares
_COMPLETION_MODELS: dict = {}
_WARMED: set = set()


def _warm_tags(project_dir: str) -> None:
    """Read the scope's tag index in a background thread (once per folder), so
    the first completion or Insert Tag finds it read."""
    key = os.path.normcase(os.path.abspath(project_dir))
    if key in _WARMED:
        return
    _WARMED.add(key)

    def work():
        try:
            from ...io.project import tag_index

            tag_index(project_dir)
        except Exception:  # noqa: BLE001 -- a warm-up only
            pass

    import threading
    from ...infra import gcguard

    gcguard.before_worker()  # only the GUI thread may collect garbage
    threading.Thread(target=work, name="tag-index", daemon=True).start()


class TagCompletionModel(QtCore.QAbstractTableModel):
    """The completion list of a scope: (name, project) rows of the names that
    match the text being typed (:meth:`update`), at most :attr:`LIMIT`; the
    names that start with the text first. Column 0 completes; column 1 (the
    project, or what a function is) is shown dimmed. An expression or command field
    adds the Cicode functions (ExpressionContext.function_rows)."""

    LIMIT = 200
    #: extra names above this many are matched by their start only (a 'contains'
    #: scan of a list the size of a scope would run on every key press)
    EXTRA_SCAN = 20000

    def __init__(self, project_dir: Optional[str], parent=None):
        super().__init__(parent)
        self.project_dir = project_dir
        self._index = None
        self._rows: List[tuple] = []
        self._prefix = None
        self._extra_prep = None
        #: the lower-case names of the function rows shown
        self._function_names: set = set()
        #: the lower-case names of the equipment rows shown (a level, not an item)
        self._level_names: set = set()
        self._fn_prep = None

    def tag_index(self):
        if self._index is None or not self._current():
            from ...io.project import TagIndex, tag_index

            try:
                self._index = tag_index(self.project_dir) if self.project_dir else TagIndex(())
            except Exception:  # noqa: BLE001
                self._index = TagIndex(())
        return self._index

    def _current(self) -> bool:
        if not self.project_dir:
            return True
        from ...io.project import cached_tag_index

        return cached_tag_index(self.project_dir) is self._index

    def _prepared(self, extras: Sequence[str]):
        """(sorted lower-case names, names) of ``extras``, kept while the same
        list is passed (a field passes its context's list on every key press)."""
        hit = self._extra_prep
        if hit is not None and hit[0] is extras:
            return hit[1], hit[2]
        pairs = sorted(((n.lower(), n) for n in extras if n), key=lambda p: p[0])
        lows = [p[0] for p in pairs]
        names = [p[1] for p in pairs]
        self._extra_prep = (extras, lows, names)
        return lows, names

    def update(self, prefix: str, extras: Sequence[str] = (), functions: Sequence[tuple] = ()) -> int:
        """Show the names matching ``prefix`` (and the ``extras`` that contain
        it, and the ``functions`` rows: (lower-case name, name, what it is), sorted);
        returns how many."""
        low = (prefix or "").strip().lower()
        idx = self.tag_index()
        rows: List[tuple] = []
        seen = set()
        self._function_names = set()
        self._level_names = set()
        below = self._equipment_rows(idx, low) if "." in low else None
        if below:
            rows = below[: self.LIMIT]
        elif low:
            for c in idx.search(low, self.LIMIT):
                name = idx.name_of(c)
                seen.add(name.lower())
                rows.append((name, idx.project_of(c)))
            head: List[str] = []
            tail: List[str] = []
            if extras:
                lows, names = self._prepared(extras)
                i = bisect.bisect_left(lows, low)
                while i < len(lows) and lows[i].startswith(low) and len(head) < self.LIMIT:
                    if lows[i] not in seen:
                        seen.add(lows[i])
                        head.append(names[i])
                    i += 1
                if len(lows) <= self.EXTRA_SCAN:
                    for lo, n in zip(lows, names):
                        if low in lo and lo not in seen:
                            seen.add(lo)
                            tail.append(n)
            rows = [(n, "") for n in head] + rows + [(n, "") for n in tail]
            if functions:
                rows = self._with_functions(low, rows, functions, seen)
            rows = rows[: self.LIMIT]
        self.beginResetModel()
        self._rows = rows
        self._prefix = low
        self.endResetModel()
        return len(rows)

    def _with_functions(
        self, low: str, rows: List[tuple], functions: Sequence[tuple], seen: set
    ) -> List[tuple]:
        """``rows`` with the functions matching ``low``: those that start with it among the
        names that do (in name order), those that contain it after the names that do."""
        prep = self._fn_prep
        if prep is None or prep[0] is not functions:
            prep = self._fn_prep = (functions, [r[0] for r in functions])
        lows = prep[1]
        starts, contains = [], []
        i = bisect.bisect_left(lows, low)
        while i < len(lows) and lows[i].startswith(low):
            if lows[i] not in seen:
                starts.append(functions[i])
            i += 1
        for r in functions:
            if low in r[0] and not r[0].startswith(low) and r[0] not in seen:
                contains.append(r)
        self._function_names = {r[0] for r in starts + contains}
        named = [r for r in rows if r[0].lower().startswith(low)]
        others = [r for r in rows if not r[0].lower().startswith(low)]
        head = sorted(named + [(r[1], r[2]) for r in starts], key=lambda r: r[0].lower())
        return head + others + [(r[1], r[2]) for r in contains]

    def is_function_row(self, name: str) -> bool:
        """``name`` is a function row of the list shown."""
        return (name or "").lower() in self._function_names

    def is_level_row(self, name: str) -> bool:
        """``name`` is an equipment row of the list shown (it has items below it)."""
        return (name or "").lower() in self._level_names

    def _equipment_rows(self, idx, low: str) -> List[tuple]:
        """The rows of the level below the equipment before the last '.' of ``low`` that
        start with what follows it ('pump1.sp' -> Pump1.Speed); [] when that is no
        equipment."""
        head = low[: low.rfind(".") + 1]
        tail = low[len(head) :]
        try:
            kids = idx.equipment_children(head)
        except Exception:  # noqa: BLE001  # swallow-ok: an index without equipment
            return []
        rows = []
        for name, what in kids:
            if name.lower()[len(head) :].startswith(tail):
                if what == "equipment":
                    self._level_names.add(name.lower())
                rows.append((name, "Equipment" if what == "equipment" else "Equipment item"))
        return rows

    def names(self) -> List[str]:
        return [r[0] for r in self._rows]

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 2

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole):
            return self._rows[index.row()][index.column()]
        if role == QtCore.Qt.ForegroundRole and index.column() == 1:
            try:
                from ..style.theme import COLORS

                return QtGui.QColor(COLORS.get("text_dim", "#969ca5"))
            except Exception:  # noqa: BLE001
                return QtGui.QColor("#969ca5")
        return None


#: the scope's names sorted without case: (tag index, lower-case names, names)
_SORTED: list = []


def _sorted_names(idx):
    """(lower-case names, names) of every name of ``idx``, sorted without case (kept for
    the index held)."""
    if not _SORTED or _SORTED[0][0] is not idx:
        pairs = sorted((n.lower(), n) for n in idx.sorted_names(idx.KINDS))
        _SORTED[:] = [(idx, [p[0] for p in pairs], [p[1] for p in pairs])]
    return _SORTED[0][1], _SORTED[0][2]


class NameCompletionModel(TagCompletionModel):
    """The values of a genie parameter that makes names (``patterns``: (before, after)
    around the value, '%Tag%_PV' -> ('', '_PV')): the values that start with the text
    typed and make one of the scope's names, those that make the most first. Column 1
    says how many of the names the value makes are in the scope."""

    #: the most names one key press reads
    SCAN = 50000
    #: the most values counted
    SCORED = 300

    def __init__(self, project_dir: Optional[str], patterns, parent=None):
        super().__init__(project_dir, parent)
        self.patterns = [tuple(p) for p in patterns if p[0] or p[1]]

    def update(self, prefix: str, extras: Sequence[str] = (), functions: Sequence[tuple] = ()) -> int:
        low = (prefix or "").strip().lower()
        rows: List[tuple] = []
        if low and self.patterns:
            idx = self.tag_index()
            lows, names = _sorted_names(idx)
            # the pattern with the most letters of its own finds the fewest values
            before, after = max(self.patterns, key=lambda p: len(p[0]) + len(p[1]))
            head, tail = (before + low).lower(), after.lower()
            found: dict = {}
            i = bisect.bisect_left(lows, head)
            end = min(len(lows), i + self.SCAN)
            while i < end and lows[i].startswith(head) and len(found) < self.SCORED:
                n = lows[i]
                if n.endswith(tail) and len(n) - len(tail) >= len(head):
                    v = names[i][len(before) : len(n) - len(after)]
                    found.setdefault(v.lower(), v)
                i += 1
            total = len(self.patterns)
            scored = []
            for v in found.values():
                k = sum(1 for b, a in self.patterns if idx.is_name(b + v + a))
                scored.append((-k, v.lower(), v, k))
            scored.sort()
            rows = [(v, "%d of %d tags" % (k, total)) for _nk, _lo, v, k in scored[: self.LIMIT]]
        self.beginResetModel()
        self._rows = rows
        self._prefix = low
        self.endResetModel()
        return len(rows)


def completion_model_for(project_dir: Optional[str]) -> TagCompletionModel:
    """The shared completion model of the page folder's scope."""
    key = None
    if project_dir:
        try:
            from ...io.tagscope import scope

            key = scope(project_dir).key
        except Exception:  # noqa: BLE001
            key = (os.path.normcase(os.path.abspath(project_dir)),)
    m = _COMPLETION_MODELS.get(key)
    if m is None:
        m = TagCompletionModel(project_dir)
        _COMPLETION_MODELS[key] = m
    return m


class ExpressionContext:
    """Tags, functions and trend tags for one sheet's expression fields."""

    def __init__(self, project_dir: Optional[str] = None, extra_tags: Sequence[str] = (), *, live=None):
        self.project_dir = project_dir
        self._extra = [t for t in (extra_tags or ()) if t]
        #: () -> the simulation's tools (ui/simulate/sim_tools.SimTools) whose values a hover
        #: shows; None: the Simulate window's page of this project, when one runs
        self._live = live
        # the function lists are read in the background (a field's hints and lookups
        # never wait for them: FunctionCatalog.get_now, is_function)
        try:
            from ...io.cicode_catalog import preload

            preload(project_dir)
        except Exception:  # noqa: BLE001  # swallow-ok: a warm-up only
            pass
        self._index = None
        self._functions = None
        self._trends = None
        self._names = None
        self._model = None
        #: (when read, [(lower-case name, name, what it is)]) of the functions
        self._function_rows = None

    @classmethod
    def for_document(cls, doc, extra_tags: Sequence[str] = ()) -> "ExpressionContext":
        path = getattr(doc, "path", None)
        pdir = os.path.dirname(os.path.abspath(path)) if path else None
        if pdir is not None:
            # warm the function and tag lists while the user reads the sheet
            try:
                from ...io.cicode_catalog import preload

                preload(pdir)
            except Exception:  # noqa: BLE001
                pass
            _warm_tags(pdir)
            # (and the equipment names an Alarm Indicator page or a genie form completes)
            from ..panels.sa_pickers import warm_equipment

            warm_equipment(pdir)
        return cls(pdir, extra_tags)

    @property
    def extra_tags(self) -> List[str]:
        return list(self._extra)

    def tag_index(self):
        """The scope's tag index, read again when the tag tables changed since (the same
        test as TagCompletionModel._current, so the completion and the advice agree)."""
        if self._index is None or not self._index_current():
            from ...io.project import TagIndex, tag_index

            try:
                self._index = tag_index(self.project_dir) if self.project_dir else TagIndex(())
            except Exception:  # noqa: BLE001 -- the list is an aid, never required
                self._index = TagIndex(())
            self._names = None  # (the names follow the new index)
        return self._index

    def names_ready(self) -> bool:
        """The scope's tag index is read (a lookup now does not wait for the tables); when
        it is not, it is read in the background."""
        if not self.project_dir:
            return True
        if self._index is not None and self._index_current():
            return True
        from ...io.project import cached_tag_index

        try:
            if cached_tag_index(self.project_dir) is not None:
                return True
        except Exception:  # noqa: BLE001  # swallow-ok: not known: read in the background
            pass
        _warm_tags(self.project_dir)
        return False

    def _index_current(self) -> bool:
        """The index held is the scope's current one (its tables' stamps as read)."""
        if not self.project_dir:
            return True
        from ...io.project import cached_tag_index

        try:
            return cached_tag_index(self.project_dir) is self._index
        except Exception:  # noqa: BLE001 -- swallow-ok: not known: keep what is held
            return True

    def _cached_names(self):
        idx = self.tag_index()
        key = (self.project_dir, tuple(self._extra))
        hit = _NAMES_CACHE.get(key)
        if hit is None or hit[0] is not idx:
            seen, out = set(), []
            for n in list(idx.sorted_names(idx.KINDS)) + self._extra:
                if n.lower() not in seen:
                    seen.add(n.lower())
                    out.append(n)
            if len(_NAMES_CACHE) > 16:
                _NAMES_CACHE.clear()
            hit = _NAMES_CACHE[key] = [idx, out]
        return hit

    def tag_names(self) -> List[str]:
        """Every name the scope offers (tags, locals, equipment items), sorted."""
        self.tag_index()  # (a changed table drops the names held)
        if self._names is None:
            self._names = self._cached_names()[1]
        return self._names

    def completion_model(self) -> TagCompletionModel:
        """The completion model shared by every field of the scope (a
        TagCompletionModel: it holds only the names that match what is typed)."""
        if self._model is None:
            self._model = completion_model_for(self.project_dir)
        return self._model

    def functions(self):
        if self._functions is None:
            from ...io.cicode_catalog import FunctionCatalog

            self._functions = FunctionCatalog(self.project_dir)
        return self._functions

    def function(self, name: str):
        """The Cicode function ``name`` (io.cicode_catalog.CicodeFunction), None when it
        is not known; never waits for the built-in list."""
        try:
            return self.functions().get_now(name)
        except Exception:  # noqa: BLE001  # swallow-ok: no function list: not known
            return None

    #: how long the function rows of the completion list are kept (s)
    FUNCTION_ROWS_S = 5.0

    def function_rows(self) -> list:
        """(lower-case name, name, what it is) of every function, sorted: the completion
        list's function rows (read again after FUNCTION_ROWS_S, a .ci file may change)."""
        import time

        from ...io.cicode_catalog import builtins_loading

        now = time.monotonic()
        hit = self._function_rows
        if hit is not None and now - hit[0] < self.FUNCTION_ROWS_S:
            return hit[1]
        cat = self.functions()
        try:
            if cat._builtins is None and builtins_loading():
                fns = list(cat.project())  # (the built-ins come with the next read)
                now -= self.FUNCTION_ROWS_S
            else:
                fns = cat.all()
        except Exception:  # noqa: BLE001  # swallow-ok: no function list: tags only
            fns = []
        rows = sorted(
            (
                (
                    f.name.lower(),
                    f.name,
                    "Built-in function" if f.source == "built-in" else "%s function" % f.source,
                )
                for f in fns
            ),
            key=lambda r: r[0],
        )
        self._function_rows = (now, rows)
        return rows

    def trend_tags(self):
        if self._trends is None:
            from ...io.project import trend_tags

            try:
                self._trends = trend_tags(self.project_dir) if self.project_dir else ()
            except Exception:  # noqa: BLE001
                self._trends = ()
        return self._trends

    # -- advice hooks (validators.advice) -----------------------------------------
    def is_function(self, name: str) -> Optional[bool]:
        try:
            return self.functions().is_function(name)
        except Exception:  # noqa: BLE001
            return None

    def is_name(self, name: str) -> Optional[bool]:
        idx = self.tag_index()
        known = idx.is_name(name)
        if known is None and not self._extra:
            return None
        if known:
            return True
        low = name.lower()
        if any(t.lower() == low or low.startswith(t.lower() + ".") for t in self._extra):
            return True
        return self.is_function(name) is True or False

    # -- the simulation ----------------------------------------------------------------
    def live_tools(self):
        """The tools of the simulation that runs a page of this scope (None: none runs)."""
        if self._live is not None:
            return self._live()
        if not self.project_dir:
            return None
        from ..simulate.window import simulate_window

        sw = simulate_window(create=False)
        return sw.live_tools(self.project_dir) if sw is not None else None

    def live_value(self, name: str) -> Optional[str]:
        """``name``'s value in the simulation ('' while it is read; each call asks again),
        None when no simulation runs the project."""
        try:
            t = self.live_tools()
        except Exception:  # noqa: BLE001  # swallow-ok: no simulation: no value
            return None
        if t is None or not t.available():
            return None
        from ..simulate.sim_tools import SimTools
        from ..simulate.watch_panel import watch_call

        call = watch_call(name)
        o = t.outcome(call)
        t.call(call)  # (the next look shows the value it reads now)
        return SimTools.outcome_text(o) if o is not None else ""

    def tag_range(self, name: str):
        """(ENG_ZERO, ENG_FULL) of a tag for Specify range."""
        idx = self.tag_index()
        e = idx.get(name)
        if e is not None and e.kind == "local":
            return "", ""
        return idx.eng_range(name)


# -- the Wizard menu -----------------------------------------------------------
#: extra Wizard entries: (label, callback(field, context) -> text to insert)
_EXTRA_ENTRIES: List[tuple] = []


def _wizard_icon() -> QtGui.QIcon:
    try:
        from ..style.theme import COLORS

        fg = QtGui.QColor(COLORS.get("text", "#d4d4d4"))
    except Exception:  # noqa: BLE001
        fg = QtGui.QColor("#d4d4d4")
    pm = QtGui.QPixmap(16, 16)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(fg, 1.6)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QtCore.QPointF(3, 13), QtCore.QPointF(10, 6))  # the wand
    for dx, dy in ((0, -3), (0, 3), (-3, 0), (3, 0)):  # the sparkle
        p.drawLine(QtCore.QPointF(12, 4), QtCore.QPointF(12 + dx * 0.7, 4 + dy * 0.7))
    p.end()
    return QtGui.QIcon(pm)


def _insert_into(widget, text: str, where=None) -> None:
    """Insert ``text`` at ``where`` (the (cursor, selection start, length)
    remembered when the menu opened) or at the current cursor."""
    if not widget.isEnabled() or widget.isReadOnly():
        return
    if isinstance(widget, QtWidgets.QPlainTextEdit):
        cur = widget.textCursor()
        if where is not None:
            pos, anchor = where
            cur.setPosition(anchor)
            cur.setPosition(pos, QtGui.QTextCursor.KeepAnchor)
        cur.insertText(text)
        widget.setTextCursor(cur)
    else:
        if where is not None:
            pos, start, length = where
            if start >= 0 and length:
                widget.setSelection(start, length)
            else:
                widget.setCursorPosition(pos)
        widget.insert(text)
    widget.setFocus()


def _where(widget):
    if isinstance(widget, QtWidgets.QPlainTextEdit):
        c = widget.textCursor()
        return c.position(), c.anchor()
    return widget.cursorPosition(), widget.selectionStart(), len(widget.selectedText())


def build_wizard_menu(
    widget, context: Optional[ExpressionContext], entries: Sequence[str] = ("tag", "function"), parent=None
) -> QtWidgets.QMenu:
    """The Wizard menu for ``widget`` (QLineEdit / QPlainTextEdit)."""
    menu = QtWidgets.QMenu(parent or widget)
    state = {}

    def remember():
        state["where"] = _where(widget)

    menu.aboutToShow.connect(remember)

    def run(pick):
        text = pick()
        if text:
            _insert_into(widget, text, state.get("where"))

    ctx = context if context is not None else ExpressionContext()
    if "tag" in entries:
        a = menu.addAction("Insert Tag…")
        a.setToolTip("Insert a variable tag, local variable or Equipment.Item reference at the cursor")
        a.triggered.connect(lambda: run(lambda: TagPickerDialog.pick(widget, ctx)))
    if "function" in entries:
        a = menu.addAction("Insert Function…")
        a.setToolTip("Insert a Cicode function call at the cursor")
        a.triggered.connect(lambda: run(lambda: FunctionPickerDialog.pick(widget, ctx)))
    if "trend" in entries:
        a = menu.addAction("Insert Trend Tag…")
        a.setToolTip("Insert a trend tag at the cursor")
        a.triggered.connect(lambda: run(lambda: TrendTagPickerDialog.pick(widget, ctx)))
    if _EXTRA_ENTRIES:
        menu.addSeparator()
        for label, cb in _EXTRA_ENTRIES:
            a = menu.addAction(label)
            a.triggered.connect(lambda _=False, c=cb: run(lambda: c(widget, ctx)))
    return menu


def wizard_button(
    widget, context: Optional[ExpressionContext], entries: Sequence[str] = ("tag", "function")
) -> QtWidgets.QToolButton:
    """A 'Wizard' tool button with the Insert Tag / Insert Function menu for
    a command field (which also gets its Cicode colours, hints and completion:
    cicode_assist)."""
    if context is not None and "function" in entries:
        from .cicode_assist import attach

        attach(widget, context, "command")
    b = QtWidgets.QToolButton()
    b.setText("Wizard")
    b.setIcon(_wizard_icon())
    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    b.setToolTip("Insert a tag or a Cicode function at the cursor")
    b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    b.setMenu(build_wizard_menu(widget, context, entries, b))
    return b


# -- the expression field -------------------------------------------------------
#: the key that opens the larger box (cicode_box)
BOX_KEY = "Shift+F2"
_TOKEN = re.compile(r"[%s.]*$" % _cicode_names.PART_CHARS)  # the name before the cursor


class ExpressionField(QtWidgets.QLineEdit):
    """An expression field with Wizard, completion, advice and Specify range; an
    expression or command field also colours its Cicode, underlines what is wrong, shows
    the arguments of the function being typed and says what a name is on hover
    (cicode_assist), and completes functions as well as names."""

    #: the stored value changed (the expression or its range)
    valueChanged = QtCore.Signal(str)
    #: the field's menu holds the assist's actions itself (cicode_assist)
    assist_menu = True

    @staticmethod
    def min_completion() -> int:
        """The letters of a name typed before the list of names opens: Settings > Editing >
        Expressions > Suggest names after (2; 0 = it never opens)."""
        return _panels.TAG_SUGGEST_AFTER

    def __init__(
        self,
        context: Optional[ExpressionContext] = None,
        parent=None,
        *,
        wizard: Sequence[str] = ("tag", "function"),
        field_kind: str = "expr",
    ):
        super().__init__(parent)
        self._context = context
        self._kind = field_kind
        self._range: Optional[RangeField] = None
        self._quiet = False
        self._completer = None
        self._dynamic = None
        self._wizard = None
        self._advice_action = None
        self._advice = ""  # (the advice the mark shows: advice_text)
        #: more advice on the value: value -> [Problem] (set_advice)
        self._more_advice = None
        # the Cicode check (ui/expression_check): at every pause in typing; the mark at the
        # start of the field shows what it found (_update_advice)
        self._checker = None
        self._check_result: Optional[dict] = None
        self._checked_text: Optional[str] = None
        self._check_timer = QtCore.QTimer(self)
        self._check_timer.setSingleShot(True)
        # (the pause: Settings > Editing > Expressions > Check after a pause of, read when
        # the timer starts: ui/prefs/timing_options)
        self._check_timer.timeout.connect(self.check_now)
        self._wizard_entries = tuple(wizard or ())
        if wizard:
            self._wizard = self.addAction(_wizard_icon(), QtWidgets.QLineEdit.TrailingPosition)
            self._wizard.setToolTip("Wizard: insert a tag or a Cicode function at the cursor")
            self._menu = build_wizard_menu(self, context, wizard, self)
            self._wizard.triggered.connect(self._show_menu)
        self.textChanged.connect(self._inner_changed)
        self.editingFinished.connect(self._finished)
        self._assist = None
        if field_kind in ("expr", "command"):
            from .cicode_assist import CicodeAssist

            self._assist = CicodeAssist(self, context, field_kind)

    @property
    def assist(self):
        """The field's Cicode assist (cicode_assist.CicodeAssist; None: a name field)."""
        return self._assist

    # -- the Cicode check ---------------------------------------------------------------
    def set_checker(self, checker) -> None:
        """Give the field a checker (expression_check.ExpressionChecker, or anything with
        ``check(text, kind, callback)``): what is typed is compiled at every pause."""
        self._checker = checker

    def checker(self):
        return self._checker

    def check_now(self) -> bool:
        """Compile the text now. False when there is no checker or nothing to check."""
        self._check_timer.stop()
        text = self.value().strip()
        if self._checker is None or not text:
            self._check_result = None
            self._checked_text = None
            self._update_advice()
            return False
        kind = "command" if self._kind == "command" else "expression"
        self._checker.check(text, kind, lambda result, t=text: self._checked(t, result))
        return True

    def check_result(self) -> Optional[dict]:
        """The last check's result for the text as it is now (None: not checked)."""
        return self._check_result if self._checked_text == self.value().strip() else None

    def _checked(self, text: str, result: dict) -> None:
        if text != self.value().strip():
            return  # (typed on meanwhile: the next check says)
        self._check_result = result
        self._checked_text = text
        if self._assist is not None:
            self._assist.set_check_result(self.inner_text(), result)
        self._update_advice()

    # -- the stored value -------------------------------------------------------------
    def inner_text(self) -> str:
        """What the line edit shows (the expression without its range)."""
        return QtWidgets.QLineEdit.text(self)

    def value(self) -> str:
        inner = self.inner_text()
        if self._range is not None and self._range.specified() and inner.strip():
            lo, hi, trail = self._range.numbers()
            return join_range(inner, lo, hi, trail)
        return inner

    def set_value(self, text) -> None:
        text = "" if text is None else str(text)
        self._quiet = True
        try:
            if self._range is not None:
                r = split_range(text)
                QtWidgets.QLineEdit.setText(self, r.expr if r is not None else text)
                self._range.set_range(r)
            else:
                QtWidgets.QLineEdit.setText(self, text)
        finally:
            self._quiet = False
        self._update_hint()
        if self._advice_action is not None:
            self._advice_action.setVisible(False)  # advice follows the user's edits
        self.valueChanged.emit(self.value())

    def text(self) -> str:  # noqa: D102 -- the stored value (see the class doc)
        return self.value()

    def setText(self, text) -> None:  # noqa: N802,D102 -- Qt naming
        self.set_value(text)

    # -- range --------------------------------------------------------------------
    def attach_range(self, rf: RangeField) -> None:
        """Compose the stored value with ``rf`` (Specify range)."""
        stored = self.value()
        self._range = rf
        rf.changed.connect(self._range_changed)
        rf.check.toggled.connect(self._prefill_range)
        self.set_value(stored)

    @property
    def range_field(self) -> Optional[RangeField]:
        return self._range

    def range_problem(self) -> Optional[str]:
        if self._range is None or not self.inner_text().strip():
            return None
        return self._range.problem()

    def _prefill_range(self, on: bool) -> None:
        rf = self._range
        if on and rf is not None and not rf.minimum.text() and not rf.maximum.text():
            rf.minimum.setText(rf.minimum.placeholderText())
            rf.maximum.setText(rf.maximum.placeholderText())

    def _range_changed(self) -> None:
        if not self._quiet:
            self.valueChanged.emit(self.value())

    def _update_hint(self) -> None:
        if self._range is None:
            return
        ctx = self._context
        lookup = ctx.tag_range if ctx is not None else (lambda _n: None)
        try:
            lo, hi, why = default_range(self.inner_text(), lookup)
        except Exception:  # noqa: BLE001
            lo, hi, why = "0.0", "100.0", ""
        self._range.set_default_hint(lo, hi, why)

    # -- read-only -------------------------------------------------------------------
    def setReadOnly(self, ro: bool) -> None:  # noqa: N802 -- Qt naming
        super().setReadOnly(ro)
        self._sync_wizard()
        if self._range is not None:
            self._range.setReadOnly(ro)

    # -- the wizard icon and menu -------------------------------------------------------
    def _wizard_room(self) -> bool:
        """True when the field is wide enough for the icon and some text. A genie
        form field can be a few characters wide; there the icon would cover the box."""
        return self.width() >= 28 + 8 * self.fontMetrics().averageCharWidth()

    def _sync_wizard(self) -> None:
        if self._wizard is not None:
            self._wizard.setVisible(not self.isReadOnly() and self._wizard_room())

    def resizeEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self._sync_wizard()

    def contextMenuEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        menu = self.context_menu(ev.pos())
        menu.exec(ev.globalPos())
        menu.deleteLater()

    def context_menu(self, at: QtCore.QPoint) -> QtWidgets.QMenu:
        """The field's menu for a click at ``at``: the assist's actions for the name there,
        the edit actions, Insert and the larger box."""
        menu = self.createStandardContextMenu()
        if self._assist is not None:
            self._assist.add_to_menu(menu, self.cursorPositionAt(at))
        if self._wizard is not None and not self.isReadOnly():
            menu.addSeparator()
            self._menu.setTitle("Insert")
            menu.addMenu(self._menu)
        if self._assist is not None:
            menu.addSeparator()
            a = menu.addAction("Edit in a Larger Box…", self.edit_in_box)
            a.setShortcut(QtGui.QKeySequence(BOX_KEY))
        return menu

    def edit_in_box(self) -> bool:
        """Edit the text in a larger box (cicode_box) with the same help; True when it was
        changed."""
        from .cicode_box import CicodeBoxDialog

        dlg = CicodeBoxDialog(
            self.inner_text(), self._context, self._kind, read_only=self.isReadOnly(), parent=self
        )
        if not dlg.exec() or self.isReadOnly():
            return False
        new = dlg.text()
        if new == self.inner_text():
            return False
        self.setFocus()
        self.selectAll()
        self.insert(new)  # (one undo step)
        self.editingFinished.emit()
        return True

    def set_context(self, context: Optional[ExpressionContext]) -> None:
        """Look names up in ``context`` (another project's simulation, say)."""
        self._context = context
        if self._assist is not None:
            self._assist.context = context
            self._assist._names = []
            self._assist._meant = {}
            self._assist.look_up_names()
        if self._wizard is not None:
            self._menu = build_wizard_menu(self, context, self._wizard_entries, self)
        if context is not None and isinstance(self._dynamic, TagCompletionModel):
            self.set_names(context.completion_model())

    # -- advice ------------------------------------------------------------------------
    def set_advice(self, fn) -> None:
        """More advice on the value: ``fn(value) -> [Problem]`` (a genie parameter's
        composed names)."""
        self._more_advice = fn
        self._update_advice()

    def problems(self) -> List[V.Problem]:
        probs = V.advice(self.value(), self._kind, self._context)
        if self._more_advice is not None:
            try:
                probs = probs + list(self._more_advice(self.value()) or ())
            except Exception:  # noqa: BLE001  # swallow-ok: advice must never break a form
                pass
        # what the Cicode check found for this text (the compiler's word comes first)
        r = self.check_result()
        if r is not None:
            from ..expression_check import summary

            compiled = []
            if r.get("error"):
                compiled.append(V.Problem("WARNING", "Not checked: %s" % r["error"]))
            for e in r.get("errors") or ():
                compiled.append(V.Problem("ERROR", summary({"errors": [e]})))
            for w in r.get("warnings") or ():
                compiled.append(V.Problem("WARNING", str(w.get("message") or "")))
            probs = compiled + [p for p in probs if p.message not in {c.message for c in compiled}]
        return probs

    def _update_advice(self) -> None:
        """The mark at the start of the field: the problems (a cross for an error, else a
        warning sign), or a tick when a check found that the text compiles."""
        from ..notify import level_icon

        probs = self.problems() if self.inner_text().strip() else []
        r = self.check_result()
        compiles = not probs and r is not None and not r.get("error") and not r.get("errors")
        self._advice = V.summary(probs)
        if self._advice_action is None and (probs or compiles):
            self._advice_action = self.addAction(level_icon("warning"), QtWidgets.QLineEdit.LeadingPosition)
        if self._advice_action is None:
            return
        a = self._advice_action
        a.setVisible(bool(probs) or compiles)
        if probs:
            a.setToolTip(self._advice)
            a.setIcon(level_icon("error" if any(p.level == "ERROR" for p in probs) else "warning"))
        elif compiles:
            a.setToolTip("Compiles.")
            a.setIcon(level_icon("success"))

    def advice_text(self) -> str:
        """The advice currently shown ('' = none)."""
        return self._advice

    # -- completion of the name under the cursor -----------------------------------------
    def set_names(self, names) -> None:
        """Offer ``names`` (a list, a shared QAbstractItemModel, or the scope's
        :class:`TagCompletionModel`) while typing, replacing only the name
        being typed."""
        self._dynamic = None
        if isinstance(names, TagCompletionModel):
            from .cicode_assist import name_completer

            c = name_completer(self, names)
            self._dynamic = names
        elif isinstance(names, QtCore.QAbstractItemModel):
            if not names.rowCount():
                self._completer = None
                return
            c = QtWidgets.QCompleter(names, self)
            c.setFilterMode(QtCore.Qt.MatchContains)
        else:
            names = list(names or ())
            if not names:
                self._completer = None
                return
            c = QtWidgets.QCompleter(names, self)
            c.setFilterMode(QtCore.Qt.MatchContains)
        c.setWidget(self)
        c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        if self._dynamic is None:
            c.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        c.setMaxVisibleItems(12)
        c.activated[str].connect(self._complete_with)
        self._completer = c

    def name_completer(self) -> Optional[QtWidgets.QCompleter]:
        return self._completer

    def _extras(self) -> Sequence[str]:
        # the context's own list (not a copy): the completion model keeps its
        # sorted form while the same list comes back
        ctx = self._context
        return (getattr(ctx, "_extra", None) or ()) if ctx is not None else ()

    def completion_names(self, prefix: str) -> List[str]:
        """What the completer offers for ``prefix`` (tests / diagnostics)."""
        c = self._completer
        if c is None:
            return []
        if self._dynamic is not None:
            self._dynamic.update(prefix, self._extras(), functions=self._functions())
            c.setCompletionPrefix(prefix)
            return self._dynamic.names()
        c.setCompletionPrefix(prefix)
        m = c.completionModel()
        return [m.index(i, 0).data() for i in range(m.rowCount())]

    def _functions(self) -> Sequence[tuple]:
        """The function rows the list offers (an expression or command field's)."""
        ctx = self._context
        if self._assist is None or ctx is None or not hasattr(ctx, "function_rows"):
            return ()
        if isinstance(self._dynamic, NameCompletionModel):
            return ()
        return ctx.function_rows()

    def _no_names_at(self, pos: int) -> bool:
        """A name typed from character ``pos`` is none of the project's (completes_names_at)."""
        if self._assist is None:
            return False
        from .cicode_assist import completes_names_at

        return not completes_names_at(self.inner_text(), self._assist.tokens(), pos)

    def _token(self):
        text = self.inner_text()
        cur = self.cursorPosition()
        m = _TOKEN.search(text[:cur])
        return (m.start() if m else cur), cur, (m.group(0) if m else "")

    def _complete_with(self, name: str) -> None:
        start, cur, _tok = self._token()
        self.setSelection(start, cur - start)
        dyn = self._dynamic
        add = ""
        if dyn is not None and dyn.is_function_row(name) and self.inner_text()[cur : cur + 1] != "(":
            add = "("
        elif dyn is not None and dyn.is_level_row(name):
            add = "."  # (and the level below it is offered)
        self.insert(name + add)
        if add == ".":
            QtCore.QTimer.singleShot(0, self, self._update_completion)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        if event.key() == QtCore.Qt.Key_F2 and event.modifiers() == QtCore.Qt.ShiftModifier and self._assist:
            self.edit_in_box()
            return
        c = self._completer
        if (
            c is not None
            and c.popup().isVisible()
            and event.key()
            in (
                QtCore.Qt.Key_Enter,
                QtCore.Qt.Key_Return,
                QtCore.Qt.Key_Escape,
                QtCore.Qt.Key_Tab,
                QtCore.Qt.Key_Backtab,
            )
        ):
            event.ignore()  # the popup takes them
            return
        super().keyPressEvent(event)
        if not event.text() and event.key() not in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete):
            return
        self._update_completion()

    def _update_completion(self) -> None:
        """Offer the names that start like the one before the cursor."""
        c = self._completer
        if c is None or self.isReadOnly():
            return
        start, _cur, tok = self._token()
        need = self.min_completion()
        if need <= 0 or len(tok) < need or tok[0].isdigit() or self._no_names_at(start):
            c.popup().hide()  # (a number: '> 100' is no tag name)
            return
        if self._dynamic is not None:
            n = self._dynamic.update(tok, self._extras(), functions=self._functions())
            c.setCompletionPrefix(tok)
            if not n:
                c.popup().hide()
                return
            from .cicode_assist import show_completion

            show_completion(c, self.cursorRect())
            return
        c.setCompletionPrefix(tok)
        if c.completionCount() == 0:
            c.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(
            max(260, c.popup().sizeHintForColumn(0) + c.popup().verticalScrollBar().sizeHint().width())
        )
        c.complete(rect)

    # -- internals ---------------------------------------------------------------------
    def _show_menu(self) -> None:
        self._menu.popup(self.mapToGlobal(QtCore.QPoint(self.width() - 20, self.height())))

    def _inner_changed(self, _t: str) -> None:
        if self._check_result is not None and self._checked_text != self.value().strip():
            self._update_advice()  # (the old verdict is not this text's)
        if self._quiet:
            return
        self._update_hint()
        self.valueChanged.emit(self.value())
        if self._checker is not None and not self.isReadOnly():
            from ..prefs import timing_options

            self._check_timer.start(timing_options.EXPRESSION_CHECK_MS)  # (a pause: the check)

    def _finished(self) -> None:
        # a range typed the old way ('(Tag)^(0,100)') moves into the range fields
        if self._range is not None and not self._range.specified():
            r = split_range(self.inner_text())
            if r is not None:
                self.set_value(self.inner_text())
        self._update_advice()


# -- pickers ------------------------------------------------------------------------
class _RowsModel(QtCore.QAbstractTableModel):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self._headers = list(headers)
        self._all: list = []
        self._rows: list = []

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._all = list(rows)
        self._rows = list(self._all)
        self.endResetModel()

    def filter(self, text, keep=None) -> None:
        """The rows whose cells match ``text`` (a part, without case; a TextMatch: the
        search box's rule, a regular expression with its '.*') and ``keep``."""
        from ...infra.textmatch import TextMatch

        m = text if isinstance(text, TextMatch) else TextMatch(text)
        self.beginResetModel()
        self._rows = [r for r in self._all if (keep is None or keep(r)) and m(*r[0])]
        self.endResetModel()

    def row(self, i: int):
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def rowCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QtCore.QModelIndex()) -> int:  # noqa: N802
        return len(self._headers)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if role not in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole) or not index.isValid():
            return None
        cols = self._rows[index.row()][0]
        return str(cols[index.column()]) if index.column() < len(cols) else ""

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):  # noqa: N802
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self._headers[section]
        return None


class ListPickerDialog(QtWidgets.QDialog):
    """A searchable list; Insert returns the chosen row's text."""

    HEADERS: tuple = ("Name",)
    TITLE = "Insert"
    FILTERS: tuple = ()  # (label, predicate(row payload)) choices
    SEARCH_TIP = ""

    def __init__(self, parent=None, context: Optional[ExpressionContext] = None):
        super().__init__(parent)
        from ..style.theme import SearchField

        self._context = context if context is not None else ExpressionContext()
        self.setWindowTitle(self.TITLE)
        self.resize(640, 460)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.search = SearchField("Search", tip=self.SEARCH_TIP)
        top.addWidget(self.search, 1)
        self.kind = QtWidgets.QComboBox()
        for label, _pred in self.FILTERS:
            self.kind.addItem(label)
        self.kind.setVisible(bool(self.FILTERS))
        top.addWidget(self.kind)
        self._top = top
        lay.addLayout(top)
        self.model = self.make_model()
        self.view = self.make_view()
        self.view.setModel(self.model)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        lay.addWidget(self.view, 1)
        self.search.attach(self.view, self)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail.setMinimumHeight(36)
        lay.addWidget(self.detail)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.insert_btn = self.buttons.addButton("Insert", QtWidgets.QDialogButtonBox.AcceptRole)
        self.insert_btn.setDefault(True)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.search.searchChanged.connect(lambda _t: self._apply_filter())
        self.kind.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self.view.doubleClicked.connect(lambda _i: self._accept())
        self.search.returnPressed.connect(self._accept)
        self._result = None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.load()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.view.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))
        self._apply_filter()
        # (the least each column gets; the helper fits them when the list is first shown)
        self.fit_columns()
        from ..tables import make_resizable

        make_resizable(self.view, "%s/list" % type(self).__name__)
        self.search.setFocus()

    # -- to override ------------------------------------------------------------
    def make_model(self):
        return _RowsModel(self.HEADERS, self)

    def make_view(self) -> QtWidgets.QAbstractItemView:
        v = QtWidgets.QTreeView()
        v.setRootIsDecorated(False)
        v.setUniformRowHeights(True)
        v.setAlternatingRowColors(True)
        return v

    def load(self) -> None:
        self.model.set_rows(self.rows())

    def fit_columns(self) -> None:
        for c in range(len(self.HEADERS)):
            self.view.resizeColumnToContents(c)

    def rows(self) -> list:
        """[(columns tuple, payload), ...]"""
        return []

    def filter_rows(self, text: str) -> None:
        keep = None
        if self.FILTERS:
            pred = self.FILTERS[self.kind.currentIndex()][1]
            keep = (lambda r: pred(r[1])) if pred is not None else None
        self.model.filter(self.search.matcher(text), keep)

    def payload(self, row: int):
        r = self.model.row(row)
        return r[1] if r is not None else None

    def insert_text(self, payload) -> str:
        return str(payload)

    def describe(self, payload) -> str:
        return ""

    # -- internals ----------------------------------------------------------------
    def _apply_filter(self) -> None:
        self.filter_rows(self.search.text())
        n = self.model.rowCount()
        self.insert_btn.setEnabled(n > 0)
        if n:
            self.view.setCurrentIndex(self.model.index(0, 0))
        else:
            self.detail.setText(
                "Nothing matches '%s'." % self.search.text().strip()
                if self.search.text().strip()
                else "The list is empty."
            )

    def _show(self, index) -> None:
        p = self.payload(index.row()) if index.isValid() else None
        if p is not None:
            self.detail.setText(self.describe(p))

    def select_text(self, text: str) -> bool:
        """Search for ``text`` and select the first match (tests / scripting)."""
        self.search.setText(text)
        self._apply_filter()
        return self.model.rowCount() > 0

    def chosen(self) -> Optional[str]:
        p = self.payload(self.view.currentIndex().row())
        return self.insert_text(p) if p is not None else None

    def _accept(self) -> None:
        text = self.chosen()
        if text:
            self._result = text
            self.accept()

    @classmethod
    def pick(cls, parent, context: Optional[ExpressionContext] = None) -> Optional[str]:
        dlg = cls(parent, context)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        return dlg._result


_KINDS = {"variable": "Variable tag", "local": "Local variable", "equipment": "Equipment item"}


class TagPickerDialog(ListPickerDialog):
    """Insert Tag: the variable tags, local variables and Equipment.Item
    references of the page's tag scope (its project, the projects it includes
    and the projects compiled with it), sorted by name, with a project filter."""

    HEADERS = ("Name", "Kind", "Type", "Project", "Comment")
    TITLE = "Insert Tag"
    FILTERS = (
        ("All", None),
        ("Variable tags", ("variable",)),
        ("Local variables", ("local",)),
        ("Equipment items", ("equipment",)),
    )
    SEARCH_TIP = "Searches the names, types, projects and comments"

    def make_model(self):
        from ..pick_fields import WideCombo
        from ..panels.tags_dock import TagTableModel

        # (as wide as its longest 'Project (count)', sharing the row's extra width with the
        # search box; its list opens as wide as the names)
        self.project = WideCombo()
        self.project.setToolTip("Show the names of one project")
        self.project.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self._top.addWidget(self.project, 1)
        return TagTableModel(("name", "kind", "type", "project", "comment"), {"name": "Name"}, self)

    def make_view(self) -> QtWidgets.QAbstractItemView:
        # a table view: a tree view lays out every row of its model after each
        # reset (a large scope lists many names), a table view only the rows it shows
        v = QtWidgets.QTableView()
        v.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        v.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.setAlternatingRowColors(True)
        v.setShowGrid(False)
        v.setWordWrap(False)
        v.verticalHeader().setVisible(False)
        v.verticalHeader().setDefaultSectionSize(v.fontMetrics().height() + 6)
        hdr = v.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        return v

    def load(self) -> None:
        idx = self._context.tag_index()
        self.model.set_source(idx, idx.order(idx.KINDS))
        from ..panels.tags_dock import ALL_PROJECTS

        self.project.blockSignals(True)
        try:
            self.project.clear()
            self.project.addItem(ALL_PROJECTS, None)
            for name, n in self.model.project_counts().items():
                self.project.addItem("%s (%d)" % (name, n), name)
        finally:
            self.project.blockSignals(False)
        self.project.setVisible(self.project.count() > 2)

    def fit_columns(self) -> None:
        self.view.resizeColumnToContents(1)
        self.view.resizeColumnToContents(2)
        self.view.resizeColumnToContents(3)
        if self.view.columnWidth(0) < 240:
            self.view.setColumnWidth(0, 240)

    def filter_rows(self, text: str) -> None:
        kinds = self.FILTERS[self.kind.currentIndex()][1] if self.FILTERS else None
        self.model.set_filter(text, self.project.currentData(), kinds, regex=self.search.regex)

    def payload(self, row: int):
        return self.model.entry(row)

    def insert_text(self, e) -> str:
        return e.name

    def describe(self, e) -> str:
        bits = ["%s %s" % (_KINDS.get(e.kind, e.kind), e.name)]
        if e.type:
            bits.append("type %s" % e.type)
        if e.eng_zero or e.eng_full:
            bits.append(
                ("range %s to %s %s" % (e.eng_zero or "?", e.eng_full or "?", e.eng_units or "")).rstrip()
            )
        bits.append("project %s" % e.project)
        return ", ".join(bits) + (". " + e.comment if e.comment else ".")


class FunctionPickerDialog(ListPickerDialog):
    """Insert Function."""

    HEADERS = ("Function", "Syntax", "Category", "Source")
    TITLE = "Insert Function"
    FILTERS = (
        ("All", None),
        ("Built-in", lambda f: f.source == "built-in"),
        ("This project and its includes", lambda f: f.source != "built-in"),
    )

    def __init__(self, parent=None, context: Optional[ExpressionContext] = None):
        super().__init__(parent, context)
        self.arguments = QtWidgets.QCheckBox("Insert arguments")
        self.arguments.setChecked(True)
        self.arguments.setToolTip("Insert the argument names too")
        lay = self.layout()
        lay.insertWidget(lay.indexOf(self.buttons), self.arguments)

    def rows(self) -> list:
        return [((f.name, f.signature, f.category, f.source), f) for f in self._context.functions().all()]

    def insert_text(self, f) -> str:
        box = getattr(self, "arguments", None)
        return f.insert_text(box is None or box.isChecked())

    def describe(self, f) -> str:
        where = (
            "Built-in Cicode function"
            if f.source == "built-in"
            else "Defined in %s (%s)" % (f.category, f.source)
        )
        return "%s: %s%s" % (
            f.signature or f.name + "()",
            where,
            (". " + f.description) if f.description else ".",
        )


class TrendTagPickerDialog(ListPickerDialog):
    """Insert Trend Tag (help 1172143): the trend tags of the include chain."""

    HEADERS = ("Trend tag", "Expression", "Project", "Comment")
    TITLE = "Insert Trend Tag"

    def rows(self) -> list:
        return [((t.name, t.expr, t.project, t.comment), t) for t in self._context.trend_tags()]

    def insert_text(self, t) -> str:
        return t.name

    def describe(self, t) -> str:
        return "Trend tag %s (%s) in %s%s" % (
            t.name,
            t.type or "trend",
            t.project,
            (". " + t.comment) if t.comment else ".",
        )


# the process caches of this module (infra/caches.py: when each is emptied)
from ...infra import caches as _caches  # noqa: E402

_caches.register("expression.names", _NAMES_CACHE, on=("refresh", "tests"))


# (the completion lists of a tag scope, and the scopes warmed up)
_caches.register("expression.completion_models", _COMPLETION_MODELS, on=("refresh", "tests"))
_caches.register("expression.warmed", _WARMED, on=("refresh", "tests"))
_caches.register("expression.sorted_names", _SORTED, on=("refresh", "tests"))
