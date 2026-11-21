"""Symbol Set Properties."""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import symbolset as S
from ..style import theme

_THUMB = QtCore.QSize(44, 34)
_TYPE_HELP = {
    S.ON_OFF: "The ON symbol shows while the expression is TRUE, the OFF symbol while it is FALSE.",
    S.MULTI_STATE: "Each combination of the conditions shows its own symbol (A is the "
    "lowest bit). A blank condition counts as FALSE. At most 5 "
    "conditions.",
    S.ARRAY: "The expression returns 0 to 255; symbol n shows for value n.",
    S.ANIMATED: "While the expression is TRUE the symbols show in turn; the Off "
    "symbol shows while it is FALSE.",
}
_EXPR_LABEL = {S.ON_OFF: "ON symbol when:", S.ARRAY: "Array expression:", S.ANIMATED: "Animate when:"}


def _default_picker(parent, doc) -> Optional[Tuple[str, str]]:
    from ..browser import PageBrowserDialog

    pick = PageBrowserDialog.get_library_element(
        parent, os.path.dirname(os.path.abspath(doc.path)), "symbol", title="Select Symbol"
    )
    if pick is None:
        return None
    return os.path.splitext(os.path.basename(pick[0]))[0], pick[1]


class _FitTable(QtWidgets.QTableWidget):
    """A table whose size hint shows its columns' texts and up to
    ``max_rows`` rows (the window opens big enough for them; a larger
    window gives the table the room)."""

    def __init__(self, rows: int, cols: int, max_rows: int = 9, parent=None):
        super().__init__(rows, cols, parent)
        self._max_rows = max_rows

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        s = super().sizeHint()
        fm = self.fontMetrics()
        icon = self.iconSize().width()
        w = self.frameWidth() * 2 + self.verticalScrollBar().sizeHint().width()
        if self.verticalHeader().isVisible():
            w += self.verticalHeader().sizeHint().width()
        last = self.columnCount() - 1
        stretch = self.horizontalHeader().stretchLastSection()
        for c in range(self.columnCount()):
            texts = [self.item(r, c) for r in range(self.rowCount())]
            need = max(
                [
                    fm.horizontalAdvance(it.text()) + (icon + 8 if not it.icon().isNull() else 0) + 20
                    for it in texts
                    if it is not None
                ]
                or [0]
            )
            head = self.horizontalHeaderItem(c)
            if head is not None:
                need = max(need, fm.horizontalAdvance(head.text()) + 24)
            # (a stretched last column is as wide as the table: not a need)
            w += need if (c == last and stretch) else max(self.columnWidth(c), need)
        rows = min(self.rowCount(), self._max_rows)
        h = (
            self.frameWidth() * 2
            + self.horizontalHeader().sizeHint().height()
            + sum(self.rowHeight(r) for r in range(rows))
        )
        return QtCore.QSize(max(s.width(), w), max(s.height(), h))


class _ExpressionDelegate(QtWidgets.QStyledItemDelegate):
    """A condition cell edited in an ExpressionField (Wizard, completion)."""

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self._context = context

    def createEditor(self, parent, option, index):  # noqa: N802 -- Qt naming
        from ..objprops.expression import ExpressionField

        ed = ExpressionField(self._context, parent)
        ed.set_names(self._context.completion_model())
        ed.setMaxLength(S.EXPRESSION_MAX)
        return ed

    def setEditorData(self, editor, index):  # noqa: N802 -- Qt naming
        editor.set_value(index.data() or "")

    def setModelData(self, editor, model, index):  # noqa: N802 -- Qt naming
        model.setData(index, editor.value())


class SymbolSetDialog(QtWidgets.QDialog):
    """Symbol Set Properties."""

    applied = QtCore.Signal()
    # the symbol list takes the extra room
    RESIZE = "both"

    def __init__(
        self,
        doc,
        index: int,
        parent=None,
        picker: Optional[Callable[[QtWidgets.QWidget], Optional[Tuple[str, str]]]] = None,
    ):
        super().__init__(parent)
        self._doc = doc
        self._index = index
        self._picker = picker
        self._thumbs: dict = {}
        self.changed = False
        self._legacy = doc.is_animated_symbol(index)
        # the expression fields complete the tags of the page's include chain
        from ..objprops.expression import ExpressionContext

        try:
            self._context = ExpressionContext.for_document(doc)
        except Exception:  # noqa: BLE001 -- completion is an aid
            self._context = ExpressionContext(None, [])
        self._load()
        self._set_title()
        self._build()
        self._rebuild()
        if self._list.rowCount():
            self._list.setCurrentCell(0, 1)  # Set / Clear act on a row at once

    def _set_title(self) -> None:
        an = self._an
        kind = "Animated Symbol" if self._legacy else "Symbol Set"
        self.setWindowTitle(
            "%s Properties (AN %d)" % (kind, an) if an is not None and an >= 0 else "%s Properties" % kind
        )

    # -- model ---------------------------------------------------------------
    def _load(self) -> None:
        doc, i = self._doc, self._index
        if self._legacy:
            info = doc.animated_symbol_info(i)
            self._kind = S.ANIMATED
            self._exprs = [info["expression"]]
            self._mode = info["mode"]
            self._states: List = list(info["states"])
            self._editable, self._reason = True, ""
            from ...format import geometry as G

            self._an = G.live_an(doc.parsed.page.objects[i])
        else:
            info = doc.symbol_set_info(i)
            self._kind = info["type"] if info["type"] in S.TYPE_LABELS else S.ON_OFF
            self._exprs = list(info["expressions"]) or [""]
            self._states = doc.symbol_set_specs(i)
            # a state kept by record index: an own picture, or an art-stripped
            # copy whose symbol comes from the library definition
            self._record_kinds = {st["record"]: st["kind"] for st in info["states"]}
            self._editable, self._reason = info["editable"], info["reason"]
            self._an = info["an"]
        self._orig = (self._kind, list(self._exprs), list(self._states), getattr(self, "_mode", ""))

    def _capacity(self) -> int:
        if self._legacy:
            return len(S.ANIMSYM_STATES)
        return S.capacity(self._kind, len(self._exprs))

    def _slot_count(self) -> int:
        """Rows the state list shows: every slot the type addresses (On / Off
        2, Multi-state 2**n, V3/V4 9); for Array / Animated the states in use
        (at least one row, so an empty set can be given its first symbol)."""
        if self._legacy or self._kind in (S.ON_OFF, S.MULTI_STATE):
            return self._capacity()
        return min(self._capacity(), max(1, len(self._states)))

    def _names(self, count: int) -> List[str]:
        if self._legacy:
            return ["Off symbol"] + ["Symbol %d" % k for k in range(1, 9)]
        return S.state_names(self._kind, count, len(self._exprs))

    # -- UI ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QtWidgets.QVBoxLayout(self)
        self._banner = theme.note_label(self._reason, "warn")
        self._banner.setVisible(bool(self._reason))
        lay.addWidget(self._banner)

        body = QtWidgets.QHBoxLayout()
        lay.addLayout(body, 1)
        self._type_box = QtWidgets.QGroupBox("Type")
        tl = QtWidgets.QVBoxLayout(self._type_box)
        self._type_buttons = {}
        self._type_group = QtWidgets.QButtonGroup(self)
        for kind in (S.ON_OFF, S.MULTI_STATE, S.ARRAY, S.ANIMATED):
            b = QtWidgets.QRadioButton(S.TYPE_LABELS[kind])
            b.setToolTip(_TYPE_HELP[kind])
            self._type_group.addButton(b, kind)
            self._type_buttons[kind] = b
            tl.addWidget(b)
        tl.addStretch(1)
        self._type_buttons[self._kind].setChecked(True)
        self._type_group.idToggled.connect(self._on_type)
        self._type_box.setVisible(not self._legacy)
        body.addWidget(self._type_box)

        right = QtWidgets.QVBoxLayout()
        body.addLayout(right, 1)
        # the single-expression field (On / Off, Array, Animated, V3/V4)
        self._expr_row = QtWidgets.QWidget()
        er = QtWidgets.QFormLayout(self._expr_row)
        er.setContentsMargins(0, 0, 0, 0)
        from ..objprops.expression import ExpressionField

        self._ed_expr = ExpressionField(self._context)
        self._ed_expr.set_names(self._context.completion_model())
        self._ed_expr.setMaxLength(S.EXPRESSION_MAX)
        self._ed_expr.set_value(self._exprs[0] if self._exprs else "")
        self._ed_expr.textEdited.connect(self._on_expr_edited)
        self._lbl_expr = QtWidgets.QLabel()
        er.addRow(self._lbl_expr, self._ed_expr)
        self._ed_mode = None
        if self._legacy:
            self._ed_mode = QtWidgets.QComboBox()
            self._ed_mode.setEditable(True)
            self._ed_mode.addItems(["SOFT", "ANM_ARRAY"])
            self._ed_mode.setEditText(self._mode)
            self._ed_mode.setToolTip(
                "SOFT: animate while the expression is TRUE. "
                "ANM_ARRAY: show the symbol the expression selects."
            )
            er.addRow("Mode:", self._ed_mode)
        right.addWidget(self._expr_row)

        # the Multi-state condition list
        self._cond_box = QtWidgets.QGroupBox("Conditions")
        cl = QtWidgets.QHBoxLayout(self._cond_box)
        self._conds = QtWidgets.QTableWidget(0, 2)
        self._conds.setHorizontalHeaderLabels(["", "Condition (TRUE / FALSE expression)"])
        self._conds.verticalHeader().setVisible(False)
        self._conds.setColumnWidth(0, 28)
        from ..tables import make_resizable

        make_resizable(self._conds, "SymbolSetDialog/conditions")
        self._conds.setMaximumHeight(150)
        self._conds.setItemDelegateForColumn(1, _ExpressionDelegate(self._context, self._conds))
        self._conds.itemChanged.connect(self._on_cond_changed)
        cl.addWidget(self._conds, 1)
        cb = QtWidgets.QVBoxLayout()
        self._btn_cond_add = QtWidgets.QPushButton("Add")
        self._btn_cond_add.setToolTip("Add a condition (at most 5)")
        self._btn_cond_add.clicked.connect(self._add_condition)
        self._btn_cond_edit = QtWidgets.QPushButton("Edit")
        self._btn_cond_edit.setToolTip("Edit the selected condition")
        self._btn_cond_edit.clicked.connect(self._edit_condition)
        self._btn_cond_del = QtWidgets.QPushButton("Remove")
        self._btn_cond_del.setToolTip("Remove the selected condition (one must remain)")
        self._btn_cond_del.clicked.connect(self._delete_condition)
        for b in (self._btn_cond_add, self._btn_cond_edit, self._btn_cond_del):
            cb.addWidget(b)
        cb.addStretch(1)
        cl.addLayout(cb)
        right.addWidget(self._cond_box)

        # the state symbols
        self._states_box = QtWidgets.QGroupBox("Symbols")
        sl = QtWidgets.QHBoxLayout(self._states_box)
        self._list = _FitTable(0, 2)
        self._list.setHorizontalHeaderLabels(["State", "Symbol"])
        self._list.verticalHeader().setVisible(False)
        self._list.setIconSize(_THUMB)
        self._list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._list.verticalHeader().setDefaultSectionSize(_THUMB.height() + 6)
        self._list.setColumnWidth(0, 150)
        make_resizable(self._list, "SymbolSetDialog/states")
        self._list.cellDoubleClicked.connect(lambda r, _c: self._set_state(r))
        self._list.currentCellChanged.connect(lambda *_a: self._sync_buttons())
        sl.addWidget(self._list, 1)
        sb = QtWidgets.QVBoxLayout()
        self._btn_add = QtWidgets.QPushButton("Add…")
        self._btn_add.setToolTip("Add a state at the end with the symbol you choose")
        self._btn_add.clicked.connect(self._add_state)
        self._btn_set = QtWidgets.QPushButton("Edit…")
        self._btn_set.setToolTip("Choose the symbol of the selected state")
        self._btn_set.clicked.connect(lambda: self._set_state(self._list.currentRow()))
        self._btn_remove = QtWidgets.QPushButton("Remove")
        self._btn_remove.setToolTip("Remove the selected state; the later ones move up")
        self._btn_remove.clicked.connect(self._remove_state)
        self._btn_clear = QtWidgets.QPushButton("Clear")
        self._btn_clear.setToolTip("Show no symbol in the selected state")
        self._btn_clear.clicked.connect(lambda: self._clear_state(self._list.currentRow()))
        for b in (self._btn_add, self._btn_set, self._btn_remove, self._btn_clear):
            sb.addWidget(b)
        sb.addStretch(1)
        sl.addLayout(sb)
        right.addWidget(self._states_box, 1)

        self._hint = theme.note_label("")
        right.addWidget(self._hint)
        self._status = theme.note_label("", "warn")
        right.addWidget(self._status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        self._btn_clear_prop = buttons.addButton("Clear Property", QtWidgets.QDialogButtonBox.ResetRole)
        self._btn_clear_prop.setToolTip("Blank the expression(s) and clear every symbol")
        self._btn_clear_prop.clicked.connect(self._clear_property)
        self._btn_other = buttons.addButton("Other Properties…", QtWidgets.QDialogButtonBox.ActionRole)
        self._btn_other.setToolTip(
            "Open Object Properties of this object (movement, scaling, fill, input, access)"
        )
        self._btn_other.clicked.connect(self._other_properties)
        # a V3/V4 Animated Symbol has no property sheet in the Graphics
        # Builder (page mask 0, objprops.registry): nothing else to open. (Taken
        # out of the box: the box re-shows its buttons whenever it lays them out.)
        try:
            from ..objprops.registry import SheetContext

            has_sheet = SheetContext.for_object(self._doc, self._index).has_sheet()
        except Exception:  # noqa: BLE001 -- keep the button when unsure
            has_sheet = True
        if not has_sheet:
            buttons.removeButton(self._btn_other)
            self._btn_other.setParent(self)
            self._btn_other.hide()
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply)
        self._buttons = buttons
        lay.addWidget(buttons)
        self._btn_definition = None
        if not self._editable:
            for w in (self._type_box, self._expr_row, self._cond_box, self._states_box):
                w.setEnabled(False)
            self._read_only_buttons(buttons)

    def _read_only_buttons(self, buttons: QtWidgets.QDialogButtonBox) -> None:
        """A set taken from a template / genie / library definition: Close,
        plus Edit Template / Edit Definition when the window can open it."""
        for b in (self._btn_clear_prop, self._btn_other):
            buttons.removeButton(b)
            b.setParent(self)
            b.hide()
        buttons.setStandardButtons(QtWidgets.QDialogButtonBox.Close)
        close = buttons.button(QtWidgets.QDialogButtonBox.Close)
        close.setDefault(True)
        close.setAutoDefault(True)
        win = self.parent()
        finder = getattr(win, "_owner_definition", None)
        opener = getattr(win, "_open_element", None)
        target = None
        if finder is not None and opener is not None:
            try:
                target = finder(self._index)
            except Exception:  # noqa: BLE001 -- no button then
                target = None
        if target is None:
            return
        sem = self._doc.page.objects if self._doc.page is not None else []
        from_template = self._index < len(sem) and sem[self._index].raw.get("_from_template")
        b = buttons.addButton(
            "Edit Template" if from_template else "Edit Definition", QtWidgets.QDialogButtonBox.ActionRole
        )
        b.setToolTip(
            "Open %s for editing" % os.path.splitext(os.path.basename(target[0]))[0]
            if target[0]
            else "Open the definition for editing"
        )

        def edit(_checked=False, t=target):
            self.reject()
            opener(*t)

        b.clicked.connect(edit)
        self._btn_definition = b

    # -- rebuild -------------------------------------------------------------
    def _rebuild(self) -> None:
        multi = self._kind == S.MULTI_STATE and not self._legacy
        self._expr_row.setVisible(not multi)
        self._cond_box.setVisible(multi)
        self._lbl_expr.setText(
            "Animate when:" if self._legacy else _EXPR_LABEL.get(self._kind, "Expression:")
        )
        if not multi and self._ed_expr.text() != (self._exprs[0] if self._exprs else ""):
            self._ed_expr.setText(self._exprs[0] if self._exprs else "")
        if multi:
            self._fill_conditions()
        count = self._slot_count()
        names = self._names(count)
        self._list.setRowCount(count)
        for r in range(count):
            spec = self._states[r] if r < len(self._states) else None
            name = QtWidgets.QTableWidgetItem(names[r] if r < len(names) else "State %d" % r)
            if multi:
                name.setToolTip(S.combination_text(r, len(self._exprs)))
            self._list.setItem(r, 0, name)
            self._list.setItem(r, 1, self._symbol_item(spec))
        vertical = self._legacy or self._kind in (S.ARRAY, S.ANIMATED)
        self._btn_add.setVisible(vertical and not self._legacy)
        self._btn_remove.setVisible(vertical and not self._legacy)
        self._hint.setText("" if self._legacy else _TYPE_HELP.get(self._kind, ""))
        self._update_status()
        self._sync_buttons()
        self._list.updateGeometry()  # its size hint follows the symbols

    def _fill_conditions(self) -> None:
        self._conds.blockSignals(True)
        self._conds.setRowCount(len(self._exprs))
        for r, e in enumerate(self._exprs):
            letter = QtWidgets.QTableWidgetItem("ABCDE"[r])
            letter.setFlags(QtCore.Qt.ItemIsEnabled)
            self._conds.setItem(r, 0, letter)
            self._conds.setItem(r, 1, QtWidgets.QTableWidgetItem(e))
        self._conds.blockSignals(False)
        self._btn_cond_add.setEnabled(len(self._exprs) < S.MAX_CONDITIONS)
        self._btn_cond_del.setEnabled(len(self._exprs) > 1)

    def _symbol_item(self, spec) -> QtWidgets.QTableWidgetItem:
        if spec is None:
            it = QtWidgets.QTableWidgetItem("")
            it.setToolTip("No symbol")
            return it
        if isinstance(spec, int):
            if getattr(self, "_record_kinds", {}).get(spec) == "stripped":
                it = QtWidgets.QTableWidgetItem("(from the library definition)")
                it.setToolTip(
                    "This state's symbol comes from the library definition "
                    "of the genie or symbol that contains this set."
                )
                return it
            it = QtWidgets.QTableWidgetItem("(embedded picture)")
            it.setToolTip(
                "This state shows a picture stored in the page itself. Set a library symbol to replace it."
            )
            return it
        it = QtWidgets.QTableWidgetItem("%s.%s" % (spec[0], spec[1]))
        icon = self._thumbnail(spec)
        if icon is not None:
            it.setIcon(icon)
        return it

    def _thumbnail(self, spec) -> Optional[QtGui.QIcon]:
        key = (spec[0].lower(), spec[1].lower())
        if key in self._thumbs:
            return self._thumbs[key]
        icon = None
        try:
            path = self._doc.library_path(spec[0], spec[1], "ctl")
            if path:
                from ...format.pagesummary import read_element_summary

                es = read_element_summary(path, spec[1])
                dec = es.thumbnail_rgba() if es is not None else None
                if dec:
                    w, h, rgba = dec
                    img = QtGui.QImage(rgba, w, h, w * 4, QtGui.QImage.Format_RGBA8888).copy()
                    from ..panels.library_panel import fit_pixmap, key_background

                    pm = fit_pixmap(
                        key_background(QtGui.QPixmap.fromImage(img)), _THUMB.width(), _THUMB.height()
                    )
                    icon = QtGui.QIcon(pm)
        except Exception:  # noqa: BLE001 -- a missing thumbnail is not an error
            icon = None
        self._thumbs[key] = icon
        return icon

    def _update_status(self) -> None:
        cap = self._capacity()
        lost = sum(1 for s in self._states[cap:] if s is not None)
        n = len(self._exprs)
        if self._kind == S.MULTI_STATE and not self._legacy:
            while n > 1 and not self._exprs[n - 1]:
                n -= 1  # OK drops blank conditions at the end
        if n < len(self._exprs):
            cap = S.capacity(S.MULTI_STATE, n)
            lost = sum(1 for s in self._states[cap:] if s is not None)
            msg = (
                "Condition %s is blank. Blank conditions at the end are dropped when "
                "you click OK%s. Enter an expression or delete the condition."
                % ("ABCDE"[n], ", together with %d symbol(s) that need them TRUE" % lost if lost else "")
            )
        elif lost:
            msg = (
                "%s addresses %d state(s): %d symbol(s) after state %d will be "
                "removed when you click OK." % (S.TYPE_LABELS[self._kind], cap, lost, cap - 1)
            )
        elif not any(s is not None for s in self._states):
            msg = "No symbols set yet: the set shows nothing at runtime."
        else:
            msg = ""
        theme.set_note_role(self._status, "warn")
        self._status.setText(msg)

    def _sync_buttons(self) -> None:
        r = self._list.currentRow()
        has = 0 <= r < self._list.rowCount()
        self._btn_set.setEnabled(has and self._editable)
        spec = self._states[r] if has and r < len(self._states) else None
        self._btn_clear.setEnabled(has and self._editable and spec is not None)
        self._btn_add.setEnabled(self._editable and len(self._states) < self._capacity())
        self._btn_remove.setEnabled(has and self._editable and r < len(self._states))

    # -- edits (working copy) --------------------------------------------------
    def _on_type(self, kind: int, checked: bool) -> None:
        if not checked or kind == self._kind:
            return
        # the first expression carries over (condition A <-> the single one)
        self._exprs = self._exprs[:1] or [""]
        self._kind = kind
        self._rebuild()

    def _on_expr_edited(self, text: str) -> None:
        self._exprs = [text]

    def _on_cond_changed(self, item) -> None:
        if item.column() == 1 and 0 <= item.row() < len(self._exprs):
            self._exprs[item.row()] = item.text()[: S.EXPRESSION_MAX]
            self._update_status()

    def _add_condition(self) -> None:
        if len(self._exprs) >= S.MAX_CONDITIONS:
            return
        self._exprs.append("")
        self._rebuild()
        self._conds.setCurrentCell(len(self._exprs) - 1, 1)
        self._conds.editItem(self._conds.item(len(self._exprs) - 1, 1))

    def _edit_condition(self) -> None:
        r = max(0, self._conds.currentRow())
        it = self._conds.item(r, 1)
        if it is not None:
            self._conds.setCurrentCell(r, 1)
            self._conds.editItem(it)

    def _delete_condition(self) -> None:
        if len(self._exprs) <= 1:
            return
        r = self._conds.currentRow()
        if not (0 <= r < len(self._exprs)):
            r = len(self._exprs) - 1
        del self._exprs[r]
        self._rebuild()

    def _pick(self) -> Optional[Tuple[str, str]]:
        if self._picker is not None:
            return self._picker(self)
        return _default_picker(self, self._doc)

    def _set_state(self, row: int) -> None:
        if not self._editable or not (0 <= row < self._list.rowCount()):
            return
        pick = self._pick()
        if pick is None:
            return
        while len(self._states) <= row:
            self._states.append(None)
        self._states[row] = (pick[0], pick[1])
        self._rebuild()
        self._list.setCurrentCell(row, 1)

    def _clear_state(self, row: int) -> None:
        if not (0 <= row < len(self._states)):
            return
        self._states[row] = None
        # CtDraw keeps no cleared slot at the end of a set (the V3/V4 Animated
        # Symbol has fixed OFF_SYMBOL / SYMBOL_n keys instead)
        while not self._legacy and self._states and self._states[-1] is None:
            self._states.pop()
        self._rebuild()

    def _add_state(self) -> None:
        """Append a state (an Array symbol / Animated frame) with the picked
        symbol."""
        if not self._editable or len(self._states) >= self._capacity():
            return
        pick = self._pick()
        if pick is None:
            return
        self._states.append((pick[0], pick[1]))
        self._rebuild()
        self._list.setCurrentCell(len(self._states) - 1, 1)

    def _remove_state(self) -> None:
        r = self._list.currentRow()
        if 0 <= r < len(self._states):
            del self._states[r]
            while self._states and self._states[-1] is None:
                self._states.pop()
            self._rebuild()

    def _clear_property(self) -> None:
        self._exprs = [""] * (len(self._exprs) if self._kind == S.MULTI_STATE else 1)
        self._states = []
        self._ed_expr.setText("")
        self._rebuild()

    def _other_properties(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "object_properties"):
            parent.object_properties(self._index, generic=True)
            return
        from ..objprops import ObjectPropertiesDialog

        ObjectPropertiesDialog(self._doc, self._index, self).show()

    # -- values / apply --------------------------------------------------------
    def values(self) -> dict:
        """What OK writes: {'type', 'expressions', 'states'[, 'mode']}."""
        exprs = (
            list(self._exprs) if self._kind == S.MULTI_STATE and not self._legacy else [self._ed_expr.text()]
        )
        out = {"type": self._kind, "expressions": exprs, "states": list(self._states)}
        if self._legacy:
            out["mode"] = self._ed_mode.currentText() if self._ed_mode is not None else ""
        return out

    # -- modeless -------------------------------------
    def _parts(self) -> tuple:
        """(type, expressions, states, mode) as the dialog shows them now."""
        v = self.values()
        return (v["type"], v["expressions"], v["states"], v.get("mode", "") if self._legacy else "")

    def _loaded_parts(self) -> tuple:
        o = self._orig
        return (o[0], o[1], o[2], o[3] if self._legacy else "")

    def has_pending_edits(self) -> bool:
        """Something changed in the dialog is not applied yet."""
        return bool(self._editable) and self._parts() != self._loaded_parts()

    def retarget(self, index: int) -> None:
        """The set's record now sits at ``index`` (a structural edit moved it)."""
        self._index = index
        if hasattr(self, "_ctg_target"):
            objs = self._doc.parsed.page.objects
            if 0 <= index < len(objs):
                self._ctg_target = objs[index]

    def reload(self) -> None:
        """The page changed underneath: the type, expressions, symbols and
        mode the user has not changed show the set as it is now."""
        now, was = self._parts(), self._loaded_parts()
        typed = [a != b for a, b in zip(now, was)]
        keep = (
            self._kind,
            list(self._exprs) if self._kind == S.MULTI_STATE and not self._legacy else [self._ed_expr.text()],
            list(self._states),
            now[3],
        )
        try:
            self._load()
        except Exception:  # noqa: BLE001 -- not a set any more: keep what is shown
            return
        if typed[0]:
            self._kind = keep[0]
        if typed[1]:
            self._exprs = list(keep[1])
        if typed[2]:
            self._states = list(keep[2])
        self._type_buttons[self._kind].setChecked(True)
        self._ed_expr.set_value(self._exprs[0] if self._exprs else "")
        if self._ed_mode is not None:
            self._ed_mode.setEditText(keep[3] if typed[3] else getattr(self, "_mode", ""))
        editable = self._editable
        for w in (self._type_box, self._expr_row, self._cond_box, self._states_box):
            w.setEnabled(editable)
        self._banner.setText(self._reason)
        self._banner.setVisible(bool(self._reason))
        self._set_title()
        self._rebuild()

    def _ok(self) -> None:
        if self.apply() is not False:
            self.accept()

    def apply(self):
        """Write the sheet (one undo step). False when it could not be written
        (the reason shows in the dialog)."""
        if not self._editable:
            return None
        v = self.values()
        orig = self._orig
        if (v["type"], v["expressions"], v["states"], v.get("mode", "")) == (
            orig[0],
            orig[1],
            orig[2],
            orig[3] if self._legacy else "",
        ):
            return None
        try:
            if self._legacy:
                self._doc.apply_animated_symbol(self._index, v["expressions"][0], v["mode"], v["states"])
            else:
                self._doc.apply_symbol_set(self._index, v["type"], v["expressions"], v["states"])
        except ValueError as e:
            theme.set_note_role(self._status, "danger")
            self._status.setText("Not applied: %s" % e)
            return False
        self.changed = True
        # the set's records were rebuilt: follow the new record (the window's
        # stale-dialog check compares it)
        objs = self._doc.parsed.page.objects
        if self._index < len(objs):
            self._ctg_target = objs[self._index]
        self._load()
        self._rebuild()
        self.applied.emit()
        return True
