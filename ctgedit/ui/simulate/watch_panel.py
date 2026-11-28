"""The Watch tab: live tag and function values. Its two boxes are Cicode fields
(objprops/expression.ExpressionField): names and functions completed, colours, hints, and
a name's value on hover."""

from __future__ import annotations
from ..style.theme import sp as _sp

import os
import re
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import tables
from ..style import theme
from . import options
from .sim_tools import SimTools, tools_of

#: the results the Call box keeps
CALL_LINES = 200
_TAG = re.compile(r"^[A-Za-z_][\w.]*(\[\d+\])?$")


def watch_call(text: str) -> str:
    """The Cicode call a watch evaluates: a tag name read with TagRead, else the text (one
    function call)."""
    t = (text or "").strip()
    if _TAG.match(t):
        return 'TagRead("%s")' % t
    return t


class WatchPanel(QtWidgets.QWidget):
    """The Watch tab (see the module)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Watch")
        self._src = None
        self._project_dir: Optional[str] = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        row = QtWidgets.QHBoxLayout()
        from ..objprops.expression import ExpressionField

        self.context = self._make_context(None)
        self.add_edit = ExpressionField(self.context, field_kind="expr")
        self.add_edit.set_names(self.context.completion_model())
        self.add_edit.setPlaceholderText("A tag, or one function call")
        self.add_edit.setAccessibleName("Watch")
        self.add_edit.setToolTip(
            "A tag name, or one Cicode function call with literal "
            'arguments (TagGetProperty("Level", "EngUnitsHigh")). '
            "Press Enter to add it"
        )
        self.add_edit.returnPressed.connect(self._add_typed)
        row.addWidget(self.add_edit, 1)
        add = QtWidgets.QPushButton("Add")
        add.clicked.connect(self._add_typed)
        row.addWidget(add)
        lay.addLayout(row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Watch", "Value"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setAccessibleName("Watches")
        tables.make_resizable(
            self.tree, "SimulateWindow/watch", stretch=0, caps={0: 0.6, 1: 0.4}, fit_view=True
        )
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.installEventFilter(self)
        lay.addWidget(self.tree, 1)
        self.note = theme.note_label("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        lay.addWidget(QtWidgets.QLabel("Call a function in the page's window:"))
        self.call_edit = ExpressionField(self.context, field_kind="command")
        self.call_edit.set_names(self.context.completion_model())
        self.call_edit.setAccessibleName("Call")
        self.call_edit.setPlaceholderText("Function(arguments), then Enter")
        self.call_edit.setToolTip(
            "Runs one Cicode function call with literal arguments in the "
            "simulated page's window, once; its result shows below"
        )
        self.call_edit.returnPressed.connect(self._run_call)
        lay.addWidget(self.call_edit)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(CALL_LINES)
        self.output.setAccessibleName("Call results")
        f = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.output.setFont(f)
        self.output.setMaximumHeight(110)
        lay.addWidget(self.output)
        self._calls: List[str] = []  # the calls sent from the Call box
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(options.watch_refresh_ms())
        self._timer.timeout.connect(self.refresh)
        for text in self._remembered():
            self._add(text, remember=False)
        self._show_note()

    def apply_settings(self) -> None:
        """Settings > Simulate changed: the refresh takes its new time."""
        ms = options.watch_refresh_ms()
        if ms != self._timer.interval():
            self._timer.setInterval(ms)

    # -- the list ---------------------------------------------------------------------------
    @staticmethod
    def _remembered() -> List[str]:
        raw = str(options.get("watch_list") or "")
        return [ln for ln in raw.split("\n") if ln.strip()]

    def _remember(self) -> None:
        try:
            options.put("watch_list", "\n".join(self.watches()))
        except Exception:  # noqa: BLE001 -- a setting is a convenience
            from ...infra import logsetup

            logsetup.note_exception("sim.watch_panel._remember")

    def watches(self) -> List[str]:
        return [self.tree.topLevelItem(k).text(0) for k in range(self.tree.topLevelItemCount())]

    def _add_typed(self) -> None:
        text = self.add_edit.text().strip()
        if text:
            self._add(text)
            self.add_edit.clear()
            self.refresh()

    def _add(self, text: str, remember: bool = True) -> None:
        if text in self.watches():
            return
        it = QtWidgets.QTreeWidgetItem([text, ""])
        it.setToolTip(0, watch_call(text))
        self.tree.addTopLevelItem(it)
        if remember:
            self._remember()
        self._show_note()

    def add_watch(self, text: str) -> None:
        """Add a watch (the Inspector's tags, a tag of the Tags panel)."""
        self._add(text)
        self.refresh()

    def _remove_selected(self) -> None:
        for it in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        self._remember()
        self._show_note()

    def _menu(self, pos) -> None:
        m = QtWidgets.QMenu(self)
        if self.tree.selectedItems():
            m.addAction("Remove", self._remove_selected)
        if self.tree.topLevelItemCount():
            m.addAction("Remove All", lambda: (self.tree.clear(), self._remember(), self._show_note()))
            m.addAction("Copy", self._copy)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _copy(self) -> None:
        from ..tables import selected_rows_text

        QtGui.QGuiApplication.clipboard().setText(selected_rows_text(self.tree, all_if_none=True))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.tree and ev.type() == QtCore.QEvent.KeyPress:
            if ev.key() == QtCore.Qt.Key_Delete:
                self._remove_selected()
                return True
            if ev.matches(QtGui.QKeySequence.Copy):
                self._copy()
                return True
        return super().eventFilter(obj, ev)

    # -- the source -------------------------------------------------------------------------
    def _make_context(self, project_dir: Optional[str]):
        from ..objprops.expression import ExpressionContext

        return ExpressionContext(project_dir, live=self._tools)

    def set_source(self, src, page_path: Optional[str] = None) -> None:
        """Watch simulation ``src``, which runs the page ``page_path`` (its project's names
        are the ones the boxes offer)."""
        self._src = src
        pdir = os.path.dirname(os.path.abspath(page_path)) if page_path else None
        if pdir != self._project_dir:
            self._project_dir = pdir
            self.context = self._make_context(pdir)
            for f in (self.add_edit, self.call_edit):
                f.set_context(self.context)
        self._show_note()
        self.refresh()

    def _tools(self) -> Optional[SimTools]:
        return tools_of(self._src) if self._src is not None else None

    def _show_note(self) -> None:
        t = self._tools()
        if t is None or not t.available():
            self.note.setText("Watches need the Cicode simulator to run the page.")
        elif not self.tree.topLevelItemCount():
            ms = options.watch_refresh_ms()
            every = "second" if ms == 1000 else "%g s" % (ms / 1000.0)
            self.note.setText("Add a tag or a function call above: its value updates every %s." % every)
        else:
            self.note.setText("")

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._timer.start()
        self.refresh()

    def hideEvent(self, ev) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(ev)

    def refresh(self) -> None:
        """Show the last answers and ask again (one call per watch at a time)."""
        t = self._tools()
        for k in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(k)
            call = watch_call(it.text(0))
            text = "" if t is None else SimTools.outcome_text(t.outcome(call))
            if it.text(1) != text:
                it.setText(1, text)
                it.setToolTip(1, text)
            if t is not None and t.available() and self.isVisible():
                t.call(call)
        # the Call box's answers
        if t is not None:
            for call in list(self._calls):
                o = t.outcome(call)
                if o is not None and call not in t._calls_busy:
                    self._calls.remove(call)
                    self.output.appendPlainText("%s = %s" % (call, SimTools.outcome_text(o)))
        self._show_note()

    def _run_call(self) -> None:
        text = self.call_edit.text().strip()
        t = self._tools()
        if not text:
            return
        if t is None or not t.available():
            self.output.appendPlainText("%s: the Cicode simulator does not run this page." % text)
            return
        with t._lock:
            t.calls.pop(text, None)
        if t.call(text):
            self._calls.append(text)
            self.call_edit.selectAll()
        QtCore.QTimer.singleShot(300, self, self.refresh)
