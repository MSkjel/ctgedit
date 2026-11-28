"""Appearance > Display Value for text objects."""

from __future__ import annotations

from .conditions import ConditionsEditing, property_table, resizable, table_item

import functools
from typing import List

from PySide6 import QtWidgets

from ...format import dspval as D
from ...format.symbolset import combination_label, combination_text
from .fields import Field
from ..style.theme import set_note_role
from ...infra.logsetup import note_exception as _note_exc

#: the keys the page writes / removes (the sheet's key contract, objprops.sheet_keys)
FIELDS = [Field("", k) for k in D.KEYS]
#: (re-exported by objprops / dialogs)
DISPLAY_FIELDS = FIELDS
SUBS: list = []

HELP_TOPIC = "properties#display-value"

_TYPE_HELP = {
    D.ON_OFF: "Shows the ON text while the condition is TRUE, else the OFF text (the object's text).",
    D.MULTI_STATE: "Each combination of the conditions shows its own text. Condition A is "
    "the lowest bit. Type ^n for a line break.",
    D.ARRAY: "The expression picks a row, 0 to 255. Row 0 is the object's text. Type ^n for a line break.",
    D.NUMERIC: "Shows the value in the chosen format. # is one digit, #! adds the engineering units.",
    D.STRING: "Shows the value of the expression as a string.",
}
_EXPR_LABEL = {
    D.ON_OFF: "ON text when:",
    D.ARRAY: "Array expression:",
    D.NUMERIC: "Numeric expression:",
    D.STRING: "String expression:",
}


@functools.lru_cache(maxsize=1)
def format_choices() -> tuple:
    """CtDraw's Format list: the TYPE 'FORMAT' rows of the install's
    Bin\\SCADAHELP.dbf (the 'ScadaHelp' /
    'FORMAT' rows CtDraw lists); the frozen R2 copy when no install is found. '#!' (the tag's
    engineering units, help 1172133) is added when the list lacks it."""
    out: List[str] = []
    try:
        import os
        from ...format.projects import read_dbf_rows
        from ...io.cicode_catalog import bin_dirs

        for d in bin_dirs():
            p = os.path.join(d, "SCADAHELP.dbf")
            if os.path.exists(p):
                for r in read_dbf_rows(p):
                    if (r.get("TYPE") or "").strip().upper() == "FORMAT":
                        v = (r.get("DATA") or "").strip()
                        if v and v not in out:
                            out.append(v)
                if out:
                    break
    except Exception:  # noqa: BLE001 -- the list is an aid
        _note_exc("objprops.display_value.format_choices")
        out = []
    if not out:
        out = list(D.SCADAHELP_FORMATS)
    if "#!" not in out:
        out.append("#!")
    return tuple(out)


class DisplayValueEditor(ConditionsEditing):
    """Appearance > Display Value: builds the page and writes it (before the
    sheet's key edits, in the same undo step)."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._syncing = False
        self._read_only = False
        ctx = sheet.expression_context
        self._ctx = ctx
        self._initial = sheet._doc.text_display(sheet._index)
        self._td = self._initial.copy()
        self.widget = self._build()
        self._caption = getattr(sheet, "_ed_caption", None)
        sheet._caption_owner = self
        if self._caption is not None:
            self._caption.textChanged.connect(self._on_general_caption)
        self._load(self._initial)
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except RuntimeError:
            pass

    # -- widgets ----------------------------------------------------------------------
    def _expr_field(self, limit: int):
        from .expression import ExpressionField

        f = ExpressionField(self._ctx)
        f.set_names(self._ctx.completion_model())
        from ..expression_check import checker_for

        f.set_checker(checker_for(getattr(self._ctx, "project_dir", None)))
        f.setMaxLength(limit)
        f.setProperty("_ctg_limit", limit)
        return f

    @staticmethod
    def _show_text(field, text: str) -> None:
        """Show a stored value; a value longer than the field's limit is
        never truncated (it is only reported when the page is applied)."""
        limit = field.property("_ctg_limit") or 32767
        field.setMaxLength(max(int(limit), len(text or "")))
        field.setText(text)

    def _build(self) -> QtWidgets.QWidget:
        from ..editor.caption_edit import CaptionEdit
        from ..editor.inplace_text import enter_hint  # (Settings > Editing > Text)

        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)
        # the Type radios + Clear Property
        box = QtWidgets.QGroupBox("Type")
        bl = QtWidgets.QVBoxLayout(box)
        self._types = QtWidgets.QButtonGroup(page)
        self._radios = {}
        for t in D.TYPES:
            b = QtWidgets.QRadioButton(D.TYPE_LABELS[t])
            b.setToolTip(_TYPE_HELP[t])
            self._types.addButton(b, t)
            self._radios[t] = b
            bl.addWidget(b)
        bl.addStretch(1)
        self._btn_clear = QtWidgets.QPushButton("Clear Property")
        self._btn_clear.setToolTip(
            "Clear this type's settings (applied with Apply / OK); the object's text stays"
        )
        self._btn_clear.clicked.connect(self._clear)
        bl.addWidget(self._btn_clear)
        row.addWidget(box)

        self._stack = QtWidgets.QStackedWidget()
        row.addWidget(self._stack, 1)

        # On / Off
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self._on_expr = self._expr_field(D.EXPRESSION_MAX)
        self._on_expr.valueChanged.connect(lambda t: self._set_expression(t, self._on_expr))
        f.addRow(_EXPR_LABEL[D.ON_OFF], self._on_expr)
        self._off_text = CaptionEdit("", lines=2)
        # (the tips follow Settings > Editing > Text: CaptionEdit.set_tip)
        self._off_text.set_tip(
            lambda: "The text while the condition is FALSE (the object's text). " + enter_hint()
        )
        self._off_text.textChanged.connect(
            lambda: self._set_static(D.from_edit(self._off_text.text()), self._off_text)
        )
        f.addRow("OFF text:", self._off_text)
        self._on_text = CaptionEdit("", lines=2)
        self._on_text.set_tip(lambda: "The text while the condition is TRUE. " + enter_hint())
        self._on_text.textChanged.connect(self._on_on_text)
        f.addRow("ON text:", self._on_text)
        self._stack.addWidget(self._panel(w, D.ON_OFF))

        # Multi-state
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        cbox = self._conditions_group("text_conditions", D.CONDITION_MAX)
        v.addWidget(cbox)
        sbox = QtWidgets.QGroupBox("State text")
        sl = QtWidgets.QVBoxLayout(sbox)
        self._states = property_table(["Combination", "Text"], editable=True)
        resizable(self._states, "text_states")
        self._states.itemChanged.connect(self._on_state_item)
        sl.addWidget(self._states)
        v.addWidget(sbox, 1)
        self._stack.addWidget(self._panel(w, D.MULTI_STATE))

        # Array
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        f = QtWidgets.QFormLayout()
        self._arr_expr = self._expr_field(D.EXPRESSION_MAX)
        self._arr_expr.valueChanged.connect(lambda t: self._set_expression(t, self._arr_expr))
        f.addRow(_EXPR_LABEL[D.ARRAY], self._arr_expr)
        v.addLayout(f)
        abox = QtWidgets.QGroupBox("Array text")
        al = QtWidgets.QVBoxLayout(abox)
        self._array = property_table(["Value", "Text"], editable=True)
        resizable(self._array, "text_array")
        self._array.itemChanged.connect(self._on_array_item)
        al.addWidget(self._array)
        v.addWidget(abox, 1)
        self._stack.addWidget(self._panel(w, D.ARRAY))

        # Numeric
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self._num_expr = self._expr_field(D.EXPRESSION_MAX)
        self._num_expr.valueChanged.connect(lambda t: self._set_expression(t, self._num_expr))
        f.addRow(_EXPR_LABEL[D.NUMERIC], self._num_expr)
        self._format = QtWidgets.QComboBox()
        self._format.setEditable(True)
        self._format.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self._format.addItem(D.DEFAULT_FORMAT_LABEL)
        self._format.addItems(list(format_choices()))
        self._format.setToolTip(
            "# is one digit (####.# = four digits, one decimal); #! adds "
            "the engineering units; <default> uses the tag's format"
        )
        self._format.lineEdit().setMaxLength(D.FORMAT_MAX)
        self._format.editTextChanged.connect(self._on_format)
        f.addRow("Format:", self._format)
        self._stack.addWidget(self._panel(w, D.NUMERIC))

        # String
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self._str_expr = self._expr_field(D.EXPRESSION_MAX)
        self._str_expr.valueChanged.connect(lambda t: self._set_expression(t, self._str_expr))
        f.addRow(_EXPR_LABEL[D.STRING], self._str_expr)
        self._stack.addWidget(self._panel(w, D.STRING))

        self._types.idToggled.connect(self._on_type)
        return page

    def _panel(self, body: QtWidgets.QWidget, kind: int) -> QtWidgets.QWidget:
        """A type's panel: its fields and the one-line explanation."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        # the lists and the multi-line texts take the extra height
        grows = kind in (D.MULTI_STATE, D.ARRAY, D.ON_OFF)
        v.addWidget(body, 1 if grows else 0)
        hint = QtWidgets.QLabel(_TYPE_HELP[kind])
        hint.setWordWrap(True)
        set_note_role(hint, "text_dim")
        v.addWidget(hint)
        if not grows:
            v.addStretch(1)
        return w

    # -- loading ------------------------------------------------------------------------
    def _load(self, td: D.TextDisplay) -> None:
        """Show ``td`` in every panel."""
        self._td = td.copy()
        self._syncing = True
        try:
            self._radios[td.mode].setChecked(True)
            self._stack.setCurrentIndex(td.mode)
            for f in (self._on_expr, self._arr_expr, self._num_expr, self._str_expr):
                self._show_text(f, td.expression)
            self._off_text.setText(D.to_edit(td.static))
            self._on_text.setText(D.to_edit(td.on_text))
            self._set_format_text(td.fmt)
            if self._caption is not None:
                self._caption.setText(D.to_edit(td.static))
        finally:
            self._syncing = False
        self._fill_conditions()
        self._fill_states()
        self._fill_array()
        self._sync_caption_field()

    def _set_format_text(self, fmt: str) -> None:
        # a stored format longer than the field's limit is shown whole (a
        # truncated one would be written back as the new format)
        self._format.lineEdit().setMaxLength(max(D.FORMAT_MAX, len(fmt or "")))
        self._format.setEditText(fmt if fmt else D.DEFAULT_FORMAT_LABEL)

    def _fill_states(self) -> None:
        n = len(self._td.conditions)
        self._td.states = D.resized_states(self._td.states, n)
        self._td.states[0] = self._td.static
        self._syncing = True
        try:
            self._states.setRowCount(len(self._td.states))
            for r, s in enumerate(self._td.states):
                lab = table_item(combination_label(r, n), False)
                lab.setToolTip(combination_text(r, n) + ("  (the object's text)" if r == 0 else ""))
                self._states.setItem(r, 0, lab)
                self._states.setItem(r, 1, table_item(D.to_caret(s), not self._read_only))
        finally:
            self._syncing = False

    def _array_rows(self) -> int:
        """Rows the Array table shows: the filled ones plus the next one."""
        last = max((i for i, s in enumerate(self._td.array) if s), default=0)
        return min(D.ARRAY_SIZE, max(2, last + 2))

    def _fill_array(self) -> None:
        self._td.array[0] = self._td.static
        rows = self._array_rows()
        self._syncing = True
        try:
            self._array.setRowCount(rows)
            for r in range(rows):
                lab = table_item(str(r), False)
                if r == 0:
                    lab.setToolTip("Value 0: the object's text")
                self._array.setItem(r, 0, lab)
                self._array.setItem(r, 1, table_item(D.to_caret(self._td.array[r]), not self._read_only))
        finally:
            self._syncing = False

    # -- shared values --------------------------------------------------------------------
    def _set_expression(self, text: str, src) -> None:
        """The expression every panel shares (CtDraw's DspVal_Expression)."""
        if self._syncing:
            return
        self._td.expression = text
        self._td.conditions[0] = text
        self._syncing = True
        try:
            for f in (self._on_expr, self._arr_expr, self._num_expr, self._str_expr):
                if f is not src and f.text() != text:
                    self._show_text(f, text)
            if (
                src is not self._cond_edit
                and self._conds.currentRow() == 0
                and self._cond_edit.text() != text
            ):
                self._show_text(self._cond_edit, text)
            it = self._conds.item(0, 1)
            if it is not None:
                it.setText(text)
        finally:
            self._syncing = False

    # -- the Conditions group (objprops/conditions.ConditionsEditing) --------------------------
    _MAX_CONDITIONS = D.MAX_CONDITIONS

    def _conditions(self) -> list:
        return self._td.conditions

    def _condition_a_changed(self, text: str) -> None:
        self._set_expression(text, self._cond_edit)

    def _condition_a_removed(self, text: str) -> None:
        self._set_expression(text, None)

    def _set_static(self, text: str, src) -> None:
        """The object's text (CtDraw's DspVal_StaticText): OFF text, state 0,
        array row 0 and the General page's Text."""
        if self._syncing:
            return
        self._td.static = text
        self._td.states[0] = text
        self._td.array[0] = text
        self._syncing = True
        try:
            if src is not self._off_text and D.from_edit(self._off_text.text()) != text:
                self._off_text.setText(D.to_edit(text))
            if (
                self._caption is not None
                and src is not self._caption
                and D.from_edit(self._caption.text()) != text
            ):
                self._caption.setText(D.to_edit(text))
            for table in (self._states, self._array):
                it = table.item(0, 1)
                if it is not None and table is not src and D.from_caret(it.text()) != text:
                    it.setText(D.to_caret(text))
        finally:
            self._syncing = False

    # -- edits --------------------------------------------------------------------------
    def _on_type(self, kind: int, checked: bool) -> None:
        if not checked:
            return
        self._stack.setCurrentIndex(kind)
        if self._syncing:
            return
        self._td.mode = kind
        self._sync_caption_field()

    def _sync_caption_field(self) -> None:
        """A numeric / string display shows its value, not a text: the General
        page's Text field is off while one of those types is chosen."""
        if self._caption is None:
            return
        from ..editor.inplace_text import enter_hint  # (Settings > Editing > Text)

        text_type = self._td.mode in (D.ON_OFF, D.MULTI_STATE, D.ARRAY)
        self._caption.setEnabled(text_type)
        # (a text type's tip follows the setting: CaptionEdit.set_tip)
        self._caption.set_tip(
            enter_hint
            if text_type
            else "A %s display shows the value of its expression; set it on the Display Value "
            "page." % D.TYPE_LABELS[self._td.mode].lower()
        )

    def _on_general_caption(self) -> None:
        if not self._syncing:
            self._set_static(D.from_edit(self._caption.text()), self._caption)

    def _on_on_text(self) -> None:
        if not self._syncing:
            self._td.on_text = D.from_edit(self._on_text.text())

    def _on_format(self, text: str) -> None:
        if self._syncing:
            return
        t = text.strip()
        self._td.fmt = "" if t in ("", D.DEFAULT_FORMAT_LABEL) else t

    def _on_state_item(self, item) -> None:
        if self._syncing or item.column() != 1:
            return
        r = item.row()
        text = D.from_caret(item.text())
        if r == 0:
            self._set_static(text, self._states)
        elif r < len(self._td.states):
            self._td.states[r] = text

    def _on_array_item(self, item) -> None:
        if self._syncing or item.column() != 1:
            return
        r = item.row()
        text = D.from_caret(item.text())
        if r == 0:
            self._set_static(text, self._array)
        else:
            self._td.array[r] = text
        if r == self._array.rowCount() - 1 and text and r + 1 < D.ARRAY_SIZE:
            self._syncing = True
            try:
                self._array.insertRow(r + 1)
                self._array.setItem(r + 1, 0, table_item(str(r + 1), False))
                self._array.setItem(r + 1, 1, table_item("", not self._read_only))
            finally:
                self._syncing = False

    def _clear(self) -> None:
        """Clear Property: the current type's settings (CtDraw's per-type clear)."""
        if self._read_only:
            return
        self._load(D.cleared(self._td))

    # -- the sheet's editor contract ------------------------------------------------------------
    def current(self) -> D.TextDisplay:
        """What the page shows now."""
        return self._td.copy()

    def _widget_for(self, name: str):
        if name == "static":
            if self._td.mode == D.MULTI_STATE:
                return self._states
            if self._td.mode == D.ARRAY:
                return self._array
            return self._off_text
        if name == "expression":
            return {
                D.ON_OFF: self._on_expr,
                D.ARRAY: self._arr_expr,
                D.NUMERIC: self._num_expr,
                D.STRING: self._str_expr,
            }.get(self._td.mode, self._on_expr)
        if name == "on_text":
            return self._on_text
        if name == "format":
            return self._format
        if name.startswith("condition:"):
            self._conds.setCurrentCell(int(name.split(":")[1]), 1)
            return self._cond_edit
        if name.startswith("state:"):
            self._states.setCurrentCell(int(name.split(":")[1]), 1)
            return self._states
        if name.startswith("array:"):
            r = int(name.split(":")[1])
            if r < self._array.rowCount():
                self._array.setCurrentCell(r, 1)
            return self._array
        return self.widget

    def problem(self):
        """(message, widget) when the page cannot be stored."""
        td = self.current()
        # (state texts typed under a blank condition change nothing Apply
        # stores -- they would vanish silently with the reload after Apply)
        if not D.has_changes(self._initial, td) and not D.unstored_states(td):
            return None
        probs = D.problems(td, self._initial)
        if probs:
            name, msg = probs[0]
            return msg, self._widget_for(name)
        from ...editing import UnencodableTextError, guard_text

        try:
            guard_text(*D.written_texts(td))
        except UnencodableTextError as exc:
            return "%s. Replace those characters and apply again." % exc, self.widget
        return None

    def apply(self) -> bool:
        s = self.sheet
        after = self.current()
        try:
            done = s._doc.apply_text_display(s._index, self._initial, after)
        except Exception as exc:  # noqa: BLE001 -- reported in the sheet
            _note_exc("objprops.display_value.apply")
            s._fail("The display value could not be written: %s" % exc, self.widget)
            return False
        if done is False:
            s._fail("The display value could not be stored: this object has no property list.", self.widget)
            return False
        return bool(done)

    def reload(self) -> None:
        s = self.sheet
        self._initial = s._doc.text_display(s._index)
        self._load(self._initial)

    def has_pending_edits(self) -> bool:
        """What the page shows differs from what it loaded."""
        td = self.current()
        return bool(D.has_changes(self._initial, td) or D.unstored_states(td))

    def set_read_only(self, ro: bool) -> None:
        self._read_only = bool(ro)
        for b in self._radios.values():
            b.setEnabled(not ro)
        for f in (
            self._on_expr,
            self._arr_expr,
            self._num_expr,
            self._str_expr,
            self._cond_edit,
            self._off_text,
            self._on_text,
        ):
            f.setReadOnly(ro)
        self._format.setEnabled(not ro)
        self._btn_clear.setEnabled(not ro)
        trig = (
            QtWidgets.QAbstractItemView.NoEditTriggers
            if ro
            else (
                QtWidgets.QAbstractItemView.DoubleClicked
                | QtWidgets.QAbstractItemView.EditKeyPressed
                | QtWidgets.QAbstractItemView.AnyKeyPressed
            )
        )
        for t in (self._states, self._array):
            t.setEditTriggers(trig)
        self._btn_add.setEnabled(not ro and len(self._td.conditions) < D.MAX_CONDITIONS)
        self._btn_del.setEnabled(not ro and len(self._td.conditions) > 1)


def build_display_value(sheet, page):
    ed = DisplayValueEditor(sheet)
    sheet.add_editor(ed)
    sheet.display_value_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"display_value": build_display_value}


# the process caches of this module (infra/caches.py: when each is emptied)
from ...infra import caches as _caches  # noqa: E402

_caches.register("display_value.formats", format_choices, on=())
