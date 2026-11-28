"""The Preview States panel (View > Preview States): the page's conditions and expressions,
each given a value by hand, and the page drawn in that state (features/state_preview.py)."""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...features import state_preview as SP
from ..style.theme import sp as _sp

HELP_TOPIC = "troubleshooting#preview-states"

_ID = QtCore.Qt.UserRole
_KIND = QtCore.Qt.UserRole + 1
_INDICES = QtCore.Qt.UserRole + 2
#: the most rows the list builds (a filter finds the others)
MAX_ROWS = 2000


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def value_text(kind: str, value) -> str:
    """What the Value column shows for ``value`` (None: not set)."""
    if kind == SP.ON:
        return "True" if value else "False"
    if value is None or value == "":
        return "As drawn"
    if kind == SP.PERCENT:
        return "%g %%" % float(value)
    return str(value)


class _PercentEditor(QtWidgets.QWidget):
    """A slider, 0 to 100 %, and its value: the canvas follows as it moves (the arrow keys
    step by 1, Page Up and Page Down by 10)."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(_sp(1), 0, _sp(1), 0)
        lay.setSpacing(_sp(1))
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setPageStep(10)
        self.label = QtWidgets.QLabel("0 %")
        self.label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.label.setFixedWidth(self.label.fontMetrics().horizontalAdvance("100 %") + 4)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._moved)
        self.setFocusProxy(self.slider)

    def _moved(self, v: int) -> None:
        self.label.setText("%d %%" % v)
        self.changed.emit()

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, v) -> None:
        v = int(round(float(v or 0)))
        self.slider.setValue(v)
        self.label.setText("%d %%" % v)


class _ValueDelegate(QtWidgets.QStyledItemDelegate):
    """The Value column's editors: a slider for a percent, a number for an index, a line for
    a text (a TRUE / FALSE condition is a check box)."""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    def createEditor(self, parent, option, index):  # noqa: N802 -- Qt naming
        kind = index.data(_KIND)
        if index.column() != 0 or kind in (None, SP.ON):
            return None
        if kind == SP.PERCENT:
            ed = _PercentEditor(parent)
            ed.changed.connect(lambda e=ed: self.commitData.emit(e))
            return ed
        ed = QtWidgets.QLineEdit(parent)
        if kind == SP.INDEX:
            ed.setValidator(QtGui.QIntValidator(0, 255, ed))
        ed.setFrame(False)
        ed.setPlaceholderText("As drawn")
        ed.textEdited.connect(lambda _t, e=ed: self.commitData.emit(e))
        return ed

    def setEditorData(self, editor, index):  # noqa: N802 -- Qt naming
        v = self._panel.value_of(index.data(_ID))
        if isinstance(editor, _PercentEditor):
            editor.set_value(50 if v is None else v)
        elif isinstance(editor, QtWidgets.QLineEdit):
            editor.setText("" if v is None else str(v))

    def setModelData(self, editor, model, index):  # noqa: N802 -- Qt naming
        cid = index.data(_ID)
        if isinstance(editor, _PercentEditor):
            v = editor.value()
        elif index.data(_KIND) == SP.INDEX:
            v = int(editor.text()) if editor.text().strip() else None
        else:
            v = editor.text() or None
        self._panel.set_value(cid, v)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802 -- Qt naming
        editor.setGeometry(option.rect)


class PreviewStatesPanel(QtWidgets.QWidget):
    """One row a condition (its value, its text, how many objects use it) with a row under
    it for each field it is set in."""

    #: the values changed: the window draws the page again
    valuesChanged = QtCore.Signal()
    #: select these objects (a row double-clicked)
    objectsChosen = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: Optional[SP.PreviewModel] = None
        self.values: Dict[str, object] = {}
        self._selection: List[int] = []
        self._items: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        from ..style.theme import set_note_role

        set_note_role(self.note, "text_dim")
        lay.addWidget(self.note)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(_sp(1))
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Show the conditions whose text or objects contain this")
        self.filter.textChanged.connect(lambda _t: self._apply_filter())
        top.addWidget(self.filter, 1)
        self.selected_only = QtWidgets.QCheckBox("Selected objects")
        self.selected_only.setToolTip("Show only the conditions of the selected objects and their members")
        self.selected_only.toggled.connect(lambda _on: self._apply_filter())
        top.addWidget(self.selected_only)
        self.reset_btn = QtWidgets.QPushButton("Reset")
        self.reset_btn.setToolTip("Draw every object as the page stores it again")
        self.reset_btn.clicked.connect(self.reset)
        top.addWidget(self.reset_btn)
        lay.addLayout(top)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("previewStates")
        self.tree.setAccessibleName("Preview states")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Value", "Condition", "Objects"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(QtCore.Qt.ElideRight)
        self.tree.setEditTriggers(
            QtWidgets.QAbstractItemView.CurrentChanged
            | QtWidgets.QAbstractItemView.SelectedClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        self.tree.setItemDelegateForColumn(0, _ValueDelegate(self))
        from ..tables import make_resizable

        # (the Value column wide enough for its slider; Condition takes the rest)
        make_resizable(self.tree, "PreviewStatesPanel/conditions", stretch=1, fit_on_first_fill=False)
        self.tree.setColumnWidth(0, 190)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemActivated.connect(self._activated)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self._building = False
        self.set_model(None)
        try:
            from ..help import set_help

            set_help(self, HELP_TOPIC)
        except Exception:  # noqa: BLE001  # swallow-ok: the help link is an aid
            pass

    # -- the window ----------------------------------------------------------------------
    def set_model(self, model: Optional[SP.PreviewModel], values: Optional[dict] = None) -> None:
        """Show ``model``'s conditions with ``values`` (the document's: kept while it is open;
        the panel changes the dict in place)."""
        self.model = model
        self.values = values if values is not None else {}
        if model is not None:
            for cid in [c for c in self.values if c not in model.conditions]:
                del self.values[cid]  # (an edit removed the condition)
        self._rebuild()

    def set_selection(self, indices) -> None:
        self._selection = list(indices or ())
        if self.selected_only.isChecked():
            self._apply_filter()

    def value_of(self, cid: str):
        return self.values.get(cid)

    def set_value(self, cid: str, value) -> None:
        """Give condition ``cid`` ``value`` (None: as drawn) and draw the page again."""
        if cid is None:
            return
        old = self.values.get(cid)
        if value is None:
            self.values.pop(cid, None)
        else:
            self.values[cid] = value
        if old == value:
            return
        it = self._items.get(cid)
        if it is not None:
            self._show_value(it)
        self._update_note()
        self.valuesChanged.emit()

    def reset(self) -> None:
        if not self.values:
            return
        self.values.clear()
        self._building = True
        try:
            for it in self._items.values():
                self._show_value(it)
        finally:
            self._building = False
        self._update_note()
        self.valuesChanged.emit()

    # -- building ------------------------------------------------------------------------
    def _rebuild(self) -> None:
        tree = self.tree
        scroll = tree.verticalScrollBar().value()
        open_ids = {cid for cid, it in self._items.items() if it.isExpanded()}
        self._building = True
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            self._items = {}
            conds = self.model.ordered() if self.model is not None else []
            dim = self.palette().brush(QtGui.QPalette.PlaceholderText)
            for c in conds[:MAX_ROWS]:
                top = QtWidgets.QTreeWidgetItem(["", _one_line(c.text), str(len(c.objects))])
                top.setData(0, _ID, c.id)
                top.setData(0, _KIND, c.kind)
                top.setData(0, _INDICES, c.objects)
                flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                if c.kind == SP.ON:
                    flags |= QtCore.Qt.ItemIsUserCheckable
                else:
                    flags |= QtCore.Qt.ItemIsEditable
                top.setFlags(flags)
                top.setToolTip(
                    0, SP.KIND_LABELS[c.kind] + (". Click to change it" if c.kind != SP.ON else "")
                )
                top.setToolTip(
                    1, "%s\n\n%s. Double-click to select its objects." % (c.text, SP.KIND_LABELS[c.kind])
                )
                top.setToolTip(2, "The objects it is set on")
                tree.addTopLevelItem(top)
                self._items[c.id] = top
                self._show_value(top)
                for u in c.uses:
                    ch = QtWidgets.QTreeWidgetItem(["", u.label])
                    ch.setData(0, _INDICES, [u.index])
                    ch.setForeground(1, dim)
                    ch.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    ch.setToolTip(1, "%s\n\nDouble-click to select it." % u.label)
                    top.addChild(ch)
                    ch.setFirstColumnSpanned(False)
                if c.id in open_ids:
                    top.setExpanded(True)
        finally:
            tree.setUpdatesEnabled(True)
            self._building = False
        self._apply_filter()
        tree.verticalScrollBar().setValue(scroll)
        self._update_note()

    def _show_value(self, it: QtWidgets.QTreeWidgetItem) -> None:
        cid, kind = it.data(0, _ID), it.data(0, _KIND)
        v = self.values.get(cid)
        was, self._building = self._building, True
        try:
            if kind == SP.ON:
                it.setCheckState(0, QtCore.Qt.Checked if v else QtCore.Qt.Unchecked)
            it.setText(0, value_text(kind, v))
            f = it.font(0)
            f.setBold(v is not None)
            it.setFont(0, f)
        finally:
            self._building = was

    def _update_note(self) -> None:
        if self.model is None:
            self.note.setText("Open a page to preview its states.")
            return
        n = len(self.model.conditions)
        if not n:
            self.note.setText("This page has no conditions or expressions to preview.")
            return
        set_n = len(self.values)
        more = " The first %d are listed: filter to find the others." % MAX_ROWS if n > MAX_ROWS else ""
        if set_n:
            self.note.setText(
                "%d of %d conditions set. The page shows them; nothing is run.%s" % (set_n, n, more)
            )
        else:
            self.note.setText(
                "Set a condition to see the page in that state. Nothing is run: you choose the values.%s"
                % more
            )
        self.reset_btn.setEnabled(bool(set_n))

    def _apply_filter(self) -> None:
        text = self.filter.text().strip().lower()
        sel = None
        if self.selected_only.isChecked() and self.model is not None:
            page = self.model.doc.page
            sel = set()
            for i in self._selection:
                if 0 <= i < len(page.objects):
                    sel.update(page.subtree(i))
        for k in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(k)
            hit = True
            if sel is not None:
                hit = any(i in sel for i in top.data(0, _INDICES) or ())
            if hit and text:
                hit = text in top.text(1).lower() or any(
                    text in top.child(j).text(1).lower() for j in range(top.childCount())
                )
            top.setHidden(not hit)

    # -- input ---------------------------------------------------------------------------
    def _item_changed(self, it: QtWidgets.QTreeWidgetItem, col: int) -> None:
        if self._building or col != 0 or it.data(0, _KIND) != SP.ON:
            return
        self.set_value(it.data(0, _ID), it.checkState(0) == QtCore.Qt.Checked)

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        top = it if it.parent() is None else it.parent()
        cid = top.data(0, _ID)
        m = QtWidgets.QMenu(self)
        a = m.addAction("Draw as Stored")
        a.setToolTip("Leave this condition unset: its objects drawn as the page stores them")
        a.setEnabled(cid in self.values)
        a.triggered.connect(lambda: self.set_value(cid, None))
        m.addAction("Select Objects").triggered.connect(
            lambda: self.objectsChosen.emit(sorted(set(it.data(0, _INDICES) or ())))
        )
        m.exec(self.tree.viewport().mapToGlobal(pos))
        m.deleteLater()

    def _activated(self, it: QtWidgets.QTreeWidgetItem, col: int) -> None:
        if it is None or (col == 0 and it.parent() is None):
            return
        got = it.data(0, _INDICES)
        if got:
            self.objectsChosen.emit(sorted(set(got)))
