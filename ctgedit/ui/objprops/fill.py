"""Fill > Color and Fill > Level."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import fillcolour as F
from ...format.symbolset import combination_label, combination_text
from .colour_field import ColourPairField
from .fields import NUMBER_RANGES, _L_EXPR, _Field, _i, _Sub
from .range_field import join_range, split_range
from .scroll import CurrentStack, note
from ..style.theme import color, set_note_role

#: the Fill > Color keys (the sheet's key contract, objprops.sheet_keys);
#: the page is a custom editor, not declarative fields
COLOR = _Sub("Colour", [], family=F.KEYS)
LEVEL = _Sub(
    "Level",
    [
        _Field("Level expression:", "FIL", limit=_L_EXPR, range=True),
        _i(
            "At minimum:",
            "FILMINPC",
            0,
            NUMBER_RANGES["fill"][2],
            lo=NUMBER_RANGES["fill"][0],
            hi=NUMBER_RANGES["fill"][1],
        ),
        _i(
            "At maximum:",
            "FILMAXPC",
            100,
            NUMBER_RANGES["fill"][2],
            lo=NUMBER_RANGES["fill"][0],
            hi=NUMBER_RANGES["fill"][1],
        ),
    ],
    family=F.LEVEL_KEYS,
)

FILL_SUBS = [COLOR, LEVEL]
SUBS = FILL_SUBS
FIELDS = [_Field("", k) for k in F.KEYS + F.LEVEL_KEYS]

HELP_COLOUR = "properties#fill-colour"
HELP_LEVEL = "properties#fill-level"

_TYPE_HELP = {
    F.ON_OFF: "Fills with the ON colour while the condition is TRUE, else with the OFF "
    "colour (the object's own fill colour).",
    F.MULTI_STATE: "Each combination of the conditions fills with its own colour. Condition "
    "A is the lowest bit.",
    F.ARRAY: "The expression picks a colour, 0 to 255. Colour 0 is the object's own fill colour.",
    F.THRESHOLD: "The fill changes colour when the expression passes a threshold (a "
    "percentage of its range).",
    F.GRADIENT: "The fill fades from the At minimum colour (the object's own fill colour) "
    "to the At maximum colour.",
}
_NO_STATIC = "This object has no colour of its own (it comes from its genie or library element)."
_COMPARISONS = ("<", "<=", ">", ">=")


# --------------------------------------------------------------------------- helpers
def _qcolor(c: int) -> Optional[QtGui.QColor]:
    if (c >> 24) & 0xFF == 0xFF:
        return None
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def pair_icon(pair) -> QtGui.QIcon:
    """A colour cell's swatch: one colour, both phases of a flashing colour
    side by side, or a crossed box for 'no colour'."""
    pm = QtGui.QPixmap(30, 14)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    try:
        r = QtCore.QRect(1, 1, 27, 11)
        on, off = _qcolor(pair[0]), _qcolor(pair[1])
        if on is None and off is None:
            p.setPen(QtGui.QPen(color("text_dim"), 1))
            p.drawRect(r)
            p.drawLine(r.bottomLeft(), r.topRight())
        else:
            if pair[0] == pair[1]:
                p.fillRect(r, on)
            else:
                half = QtCore.QRect(r.left(), r.top(), r.width() // 2, r.height() + 1)
                if on is not None:
                    p.fillRect(half, on)
                if off is not None:
                    p.fillRect(
                        QtCore.QRect(half.right() + 1, r.top(), r.width() - half.width() + 1, r.height() + 1),
                        off,
                    )
            p.setPen(QtGui.QPen(QtGui.QColor("#555555"), 1))
            p.drawRect(r)
    finally:
        p.end()
    return QtGui.QIcon(pm)


def pair_text(pair) -> str:
    def one(c):
        return "none" if (c >> 24) & 0xFF == 0xFF else "#%06X" % (c & 0xFFFFFF)

    if pair[0] == pair[1]:
        return one(pair[0])
    return "%s / %s (flashing)" % (one(pair[0]), one(pair[1]))


def _item(text: str, editable: bool = False) -> QtWidgets.QTableWidgetItem:
    it = QtWidgets.QTableWidgetItem(text)
    flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
    if editable:
        flags |= QtCore.Qt.ItemIsEditable
    it.setFlags(flags)
    return it


def _colour_item(pair) -> QtWidgets.QTableWidgetItem:
    it = _item(pair_text(pair))
    it.setIcon(pair_icon(pair))
    return it


def _resizable(t, name: str) -> None:
    """Resizable, remembered columns (ui/tables.py)."""
    from ..tables import make_resizable

    make_resizable(t, "ObjectPropertiesDialog/" + name)


def _table(headers) -> QtWidgets.QTableWidget:
    t = QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.setIconSize(QtCore.QSize(30, 14))
    t.verticalHeader().setDefaultSectionSize(t.fontMetrics().height() + 8)
    return t


def _inner(text: str) -> str:
    """An expression without its ``^(min,max)`` range suffix."""
    r = split_range(text or "")
    return r.expr if r is not None else (text or "")


# --------------------------------------------------------------------------- Fill > Color
class FillColorEditor:
    """Fill > Color: builds the page and writes it (before the sheet's key
    edits, in the same undo step)."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._syncing = False
        self._read_only = False
        self._ctx = sheet.expression_context
        doc, i = sheet._doc, sheet._index
        self._po = doc.parsed.page.objects[i]
        self._initial = doc.fill_colour(i)
        self._fc = self._initial.copy()
        self.widget = self._build()
        self._link_appearance()
        self._load(self._initial)
        try:
            from ..help import set_help

            set_help(self.widget, HELP_COLOUR)
        except Exception:  # noqa: BLE001
            pass

    # -- widgets ----------------------------------------------------------------------
    def _expr_field(self, limit: int, ranged: bool = False):
        from .expression import ExpressionField
        from .range_field import RangeField

        f = ExpressionField(self._ctx)
        f.set_names(self._ctx.completion_model())
        from ..expression_check import checker_for

        f.set_checker(checker_for(getattr(self._ctx, "project_dir", None)))
        f.setMaxLength(limit)
        f.setProperty("_ctg_limit", limit)
        if ranged:
            f.attach_range(RangeField())
        return f

    @staticmethod
    def _show_text(field, text: str) -> None:
        """Show a stored value; one longer than the field's limit is never
        truncated (it is only reported when the page is applied)."""
        limit = field.property("_ctg_limit") or 32767
        field.setMaxLength(max(int(limit), len(_inner(text or ""))))
        field.setText(text)

    def _static_field(self, title: str) -> ColourPairField:
        f = ColourPairField(title)
        f.changed.connect(lambda: self._set_static(f.pair(), f))
        return f

    def _build(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)
        box = QtWidgets.QGroupBox("Type")
        bl = QtWidgets.QVBoxLayout(box)
        self._types = QtWidgets.QButtonGroup(page)
        self._radios = {}
        for t in F.TYPES:
            b = QtWidgets.QRadioButton(F.TYPE_LABELS[t])
            b.setToolTip(_TYPE_HELP[t])
            self._types.addButton(b, t)
            self._radios[t] = b
            bl.addWidget(b)
        bl.addStretch(1)
        self._btn_clear = QtWidgets.QPushButton("Clear Property")
        self._btn_clear.setToolTip("Remove the fill colour animation")
        self._btn_clear.clicked.connect(self._clear)
        bl.addWidget(self._btn_clear)
        row.addWidget(box)
        # (as big as the type shown: the widest one would make the page scroll sideways)
        self._stack = CurrentStack()
        row.addWidget(self._stack, 1)
        lim = F.TEXT_LIMITS

        # On / Off
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self._on_expr = self._expr_field(lim["on_when"])
        self._on_expr.setToolTip("The condition (TRUE / FALSE) that turns the fill to the ON colour")
        self._on_expr.valueChanged.connect(lambda _t: self._on_expression(self._on_expr))
        f.addRow("ON colour when:", self._on_expr)
        self._off_col = self._static_field("OFF colour")
        self._off_col.setToolTip("The fill while the condition is FALSE (the object's own fill colour)")
        f.addRow("OFF colour:", self._off_col)
        self._on_col = ColourPairField("ON colour")
        self._on_col.setToolTip("The fill while the condition is TRUE")
        self._on_col.changed.connect(lambda: self._set_on(self._on_col.pair(), self._on_col))
        f.addRow("ON colour:", self._on_col)
        self._stack.addWidget(self._panel(w, F.ON_OFF))

        # Multi-state
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        cbox = QtWidgets.QGroupBox("Conditions")
        cl = QtWidgets.QGridLayout(cbox)
        self._conds = _table(["", "Condition (TRUE / FALSE)"])
        self._conds.setColumnWidth(0, 28)
        _resizable(self._conds, "fill_conditions")
        self._conds.setFixedHeight(
            self._conds.horizontalHeader().sizeHint().height()
            + F.MAX_CONDITIONS * self._conds.verticalHeader().defaultSectionSize()
            + 4
        )
        self._conds.currentCellChanged.connect(lambda *_a: self._show_condition())
        cl.addWidget(self._conds, 0, 0, 3, 1)
        self._btn_add = QtWidgets.QPushButton("Add")
        self._btn_add.setToolTip("Add a condition (at most 5)")
        self._btn_add.clicked.connect(self._add_condition)
        self._btn_del = QtWidgets.QPushButton("Remove")
        self._btn_del.setToolTip("Remove the selected condition")
        self._btn_del.clicked.connect(self._delete_condition)
        cl.addWidget(self._btn_add, 0, 1)
        cl.addWidget(self._btn_del, 1, 1)
        self._cond_label = QtWidgets.QLabel("Condition A:")
        self._cond_edit = self._expr_field(lim["condition"])
        self._cond_edit.setToolTip("The selected condition: a TRUE / FALSE expression")
        self._cond_edit.valueChanged.connect(self._on_condition_edit)
        ce = QtWidgets.QHBoxLayout()
        ce.addWidget(self._cond_label)
        ce.addWidget(self._cond_edit, 1)
        cl.addLayout(ce, 3, 0, 1, 2)
        v.addWidget(cbox)
        sbox = QtWidgets.QGroupBox("State colours")
        sl = QtWidgets.QVBoxLayout(sbox)
        self._states = _table(["Combination", "Colour"])
        self._states.setColumnWidth(0, 130)
        _resizable(self._states, "fill_states")
        self._states.currentCellChanged.connect(lambda *_a: self._show_state())
        self._states.cellDoubleClicked.connect(lambda *_a: self._state_col._choose(False))
        sl.addWidget(self._states, 1)
        se = QtWidgets.QHBoxLayout()
        self._state_label = QtWidgets.QLabel("Colour:")
        se.addWidget(self._state_label)
        self._state_col = ColourPairField("State colour")
        self._state_col.changed.connect(self._on_state_colour)
        se.addWidget(self._state_col, 1)
        sl.addLayout(se)
        v.addWidget(sbox, 1)
        self._stack.addWidget(self._panel(w, F.MULTI_STATE))

        # Array
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        f = QtWidgets.QFormLayout()
        self._arr_expr = self._expr_field(lim["expression"])
        self._arr_expr.setToolTip("An expression that returns a whole number 0 to 255")
        self._arr_expr.valueChanged.connect(lambda _t: self._on_expression(self._arr_expr))
        f.addRow("Array expression:", self._arr_expr)
        v.addLayout(f)
        abox = QtWidgets.QGroupBox("Array colours")
        al = QtWidgets.QVBoxLayout(abox)
        self._array = _table(["Value", "Colour"])
        self._array.setColumnWidth(0, 60)
        _resizable(self._array, "fill_array")
        self._array.currentCellChanged.connect(lambda *_a: self._show_array())
        self._array.cellDoubleClicked.connect(lambda *_a: self._array_col._choose(False))
        al.addWidget(self._array, 1)
        ae = QtWidgets.QHBoxLayout()
        self._array_label = QtWidgets.QLabel("Colour of 0:")
        ae.addWidget(self._array_label)
        self._array_col = ColourPairField("Array colour")
        self._array_col.changed.connect(self._on_array_colour)
        ae.addWidget(self._array_col, 1)
        al.addLayout(ae)
        v.addWidget(abox, 1)
        self._stack.addWidget(self._panel(w, F.ARRAY))

        # Threshold
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        f = QtWidgets.QFormLayout()
        self._thr_expr = self._expr_field(lim["expression"], ranged=True)
        self._thr_expr.setToolTip("The value that is compared with the thresholds")
        self._thr_expr.valueChanged.connect(lambda _t: self._on_expression(self._thr_expr))
        f.addRow("Colour expression:", self._thr_expr)
        f.addRow("", self._thr_expr.range_field)
        v.addLayout(f)
        tbox = QtWidgets.QGroupBox("Thresholds (% of the range)")
        tl = QtWidgets.QGridLayout(tbox)
        self._thr = _table(["Comparison", "Threshold", "Colour"])
        self._thr.setColumnWidth(0, 90)
        self._thr.setColumnWidth(1, 80)
        _resizable(self._thr, "fill_thresholds")
        self._thr.currentCellChanged.connect(lambda *_a: self._show_threshold())
        self._thr.cellDoubleClicked.connect(lambda _r, c: self._thr_col._choose(False) if c == 2 else None)
        tl.addWidget(self._thr, 0, 0, 3, 1)
        self._btn_tadd = QtWidgets.QPushButton("Add")
        self._btn_tadd.setToolTip("Add a threshold after the selected one (at most 100)")
        self._btn_tadd.clicked.connect(self._add_threshold)
        self._btn_tdel = QtWidgets.QPushButton("Remove")
        self._btn_tdel.setToolTip("Remove the selected threshold")
        self._btn_tdel.clicked.connect(self._delete_threshold)
        tl.addWidget(self._btn_tadd, 0, 1)
        tl.addWidget(self._btn_tdel, 1, 1)
        te = QtWidgets.QHBoxLayout()
        self._thr_cmp = QtWidgets.QComboBox()
        self._thr_cmp.addItems(list(_COMPARISONS))
        self._thr_cmp.setToolTip("When the expression is below (<, <=) or above (>, >=) the threshold")
        self._thr_cmp.currentIndexChanged.connect(self._on_threshold_edit)
        self._thr_val = QtWidgets.QSpinBox()
        self._thr_val.setRange(0, 100)
        self._thr_val.setSuffix(" %")
        self._thr_val.setToolTip("The threshold, as a percentage of the expression's range")
        self._thr_val.valueChanged.connect(self._on_threshold_edit)
        self._thr_col = ColourPairField("Threshold colour")
        self._thr_col.changed.connect(self._on_threshold_edit)
        te.addWidget(self._thr_cmp)
        te.addWidget(self._thr_val)
        te.addWidget(self._thr_col, 1)
        tl.addLayout(te, 3, 0, 1, 2)
        v.addWidget(tbox, 1)
        self._stack.addWidget(self._panel(w, F.THRESHOLD))

        # Gradient
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self._grad_expr = self._expr_field(lim["expression"], ranged=True)
        self._grad_expr.setToolTip("The value that moves the fill between the two colours")
        self._grad_expr.valueChanged.connect(lambda _t: self._on_expression(self._grad_expr))
        f.addRow("Colour expression:", self._grad_expr)
        f.addRow("", self._grad_expr.range_field)
        self._min_col = self._static_field("At minimum colour")
        self._min_col.setToolTip("The fill at the expression's minimum: the object's own fill colour")
        f.addRow("At minimum:", self._min_col)
        self._max_col = ColourPairField("At maximum colour")
        self._max_col.setToolTip("The fill at the expression's maximum")
        self._max_col.changed.connect(lambda: self._set_on(self._max_col.pair(), self._max_col))
        f.addRow("At maximum:", self._max_col)
        self._stack.addWidget(self._panel(w, F.GRADIENT))

        self._types.idToggled.connect(self._on_type)
        return page

    def _panel(self, body: QtWidgets.QWidget, kind: int) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        lists = kind in (F.MULTI_STATE, F.ARRAY, F.THRESHOLD)
        v.addWidget(body, 1 if lists else 0)
        hint = note(QtWidgets.QLabel(_TYPE_HELP[kind]))
        set_note_role(hint, "text_dim")
        v.addWidget(hint)
        if not lists:
            v.addStretch(1)
        return w

    def _expr_fields(self):
        return (self._on_expr, self._arr_expr, self._thr_expr, self._grad_expr)

    # -- the Appearance page shares the static colour --------------------------------------
    def _appearance_field(self) -> Optional[ColourPairField]:
        """The Appearance > General colour field holding the static colour:
        a pipe's highlight, a text's colour, a hollow shape's line colour,
        else the fill colour (CtDraw's FillColor_StaticColor)."""
        s = self.sheet
        tc = self._po.type_code
        if tc == 0x0B:
            return getattr(s, "_col_hilight", None)
        if tc == 0x05:
            return getattr(s, "_col_fore", None)
        filled = getattr(s, "_chk_filled", None)
        if filled is not None and not filled.isChecked():
            return getattr(s, "_col_line", None)
        return getattr(s, "_col_fill", None) or getattr(s, "_col_line", None)

    def _link_appearance(self) -> None:
        s = self.sheet
        for name in ("_col_fill", "_col_line", "_col_hilight", "_col_fore"):
            w = getattr(s, name, None)
            if isinstance(w, ColourPairField):
                w.changed.connect(self._on_appearance_colour)
        chk = getattr(s, "_chk_filled", None)
        if chk is not None:
            chk.toggled.connect(lambda _on: self._on_appearance_colour())

    def _on_appearance_colour(self) -> None:
        if self._syncing or not self._fc.static_editable:
            return
        f = self._appearance_field()
        if f is not None:
            self._set_static(f.pair(), f)

    # -- loading ------------------------------------------------------------------------
    def _load(self, fc: F.FillColour) -> None:
        self._fc = fc.copy()
        self._syncing = True
        try:
            self._radios[fc.mode].setChecked(True)
            self._stack.setCurrentIndex(fc.mode)
            self._show_expression(fc.expression, None)
            static = fc.static if fc.static is not None else (F.NO_COLOUR, F.NO_COLOUR)
            for fld in (self._off_col, self._min_col):
                fld.set_pair(*static)
                fld.set_read_only(self._read_only or not fc.static_editable)
                if not fc.static_editable:
                    fld.setToolTip(_NO_STATIC)
            self._on_col.set_pair(*fc.on)
            self._max_col.set_pair(*fc.on)
        finally:
            self._syncing = False
        self._fill_conditions()
        self._fill_states()
        self._fill_array()
        self._fill_thresholds()

    def _show_expression(self, text: str, src) -> None:
        """Show the shared expression (CtDraw's FillColor_Expression) in
        every panel: the ranged fields keep a range they have, the others --
        and Multi-state condition A -- show the expression alone. ``src`` =
        the field it was typed in (None: the stored expression at load)."""
        was = self._syncing
        self._syncing = True
        try:
            inner = _inner(text)
            ranged_src = src is None or src in (self._thr_expr, self._grad_expr)
            for fld in self._expr_fields():
                if fld is src:
                    continue
                if fld in (self._thr_expr, self._grad_expr):
                    if ranged_src:
                        self._show_text(fld, text)
                    elif fld.range_field.specified() and inner.strip():
                        lo, hi, trail = fld.range_field.numbers()
                        self._show_text(fld, join_range(inner, lo, hi, trail))
                    else:
                        self._show_text(fld, inner)
                else:
                    self._show_text(fld, inner)
            if src is not None and self._fc.conditions:
                self._fc.conditions[0] = inner
            if self._conds.currentRow() <= 0 and src is not self._cond_edit and self._fc.conditions:
                self._show_text(self._cond_edit, self._fc.conditions[0])
            it = self._conds.item(0, 1)
            if it is not None and self._fc.conditions:
                it.setText(self._fc.conditions[0])
        finally:
            self._syncing = was

    def _current_expression(self) -> str:
        """The current type's expression as its field stores it."""
        m = self._fc.mode
        if m == F.MULTI_STATE:
            return self._fc.conditions[0] if self._fc.conditions else ""
        return {
            F.ON_OFF: self._on_expr,
            F.ARRAY: self._arr_expr,
            F.THRESHOLD: self._thr_expr,
            F.GRADIENT: self._grad_expr,
        }[m].text()

    def _fill_conditions(self, select: int = 0) -> None:
        conds = self._fc.conditions
        self._syncing = True
        try:
            self._conds.setRowCount(len(conds))
            for r, c in enumerate(conds):
                self._conds.setItem(r, 0, _item("ABCDE"[r]))
                self._conds.setItem(r, 1, _item(c))
        finally:
            self._syncing = False
        self._conds.setCurrentCell(max(0, min(select, len(conds) - 1)), 1)
        self._show_condition()
        ro = self._read_only
        self._btn_add.setEnabled(not ro and len(conds) < F.MAX_CONDITIONS)
        self._btn_del.setEnabled(not ro and len(conds) > 1)

    def _fill_states(self) -> None:
        n = len(self._fc.conditions)
        self._fc.states = F.resized_states(self._fc.states, n, self._initial.states)
        if self._fc.static is not None:
            self._fc.states[0] = self._fc.static
        row = max(0, self._states.currentRow())
        self._syncing = True
        try:
            self._states.setRowCount(len(self._fc.states))
            for r, pair in enumerate(self._fc.states):
                lab = _item(combination_label(r, n))
                lab.setToolTip(
                    combination_text(r, n) + ("  (the object's own fill colour)" if r == 0 else "")
                )
                self._states.setItem(r, 0, lab)
                self._states.setItem(r, 1, _colour_item(pair))
        finally:
            self._syncing = False
        self._states.setCurrentCell(min(row, len(self._fc.states) - 1), 1)
        self._show_state()

    def _fill_array(self) -> None:
        if self._fc.static is not None:
            self._fc.array[0] = self._fc.static
        row = max(0, self._array.currentRow())
        self._syncing = True
        try:
            self._array.setRowCount(F.ARRAY_SIZE)
            for r in range(F.ARRAY_SIZE):
                lab = _item(str(r))
                if r == 0:
                    lab.setToolTip("Value 0: the object's own fill colour")
                self._array.setItem(r, 0, lab)
                self._array.setItem(r, 1, _colour_item(self._fc.array[r]))
        finally:
            self._syncing = False
        self._array.setCurrentCell(row, 1)
        self._show_array()

    def _fill_thresholds(self, select: Optional[int] = None) -> None:
        rows = self._fc.thresholds
        cur = self._thr.currentRow() if select is None else select
        self._syncing = True
        try:
            self._thr.setRowCount(len(rows))
            for r, t in enumerate(rows):
                self._thr.setItem(r, 0, _item(t.comparison))
                self._thr.setItem(r, 1, _item("%d %%" % t.value))
                self._thr.setItem(r, 2, _colour_item(t.pair))
        finally:
            self._syncing = False
        if rows:
            self._thr.setCurrentCell(max(0, min(cur, len(rows) - 1)), 2)
        self._show_threshold()
        ro = self._read_only
        self._btn_tadd.setEnabled(not ro and len(rows) < F.MAX_THRESHOLDS)
        self._btn_tdel.setEnabled(not ro and bool(rows))

    # -- shared values --------------------------------------------------------------------
    def _on_expression(self, src) -> None:
        if self._syncing:
            return
        self._fc.cleared = False
        self._show_expression(src.text(), src)
        self._fc.expression = self._current_expression()

    def _set_static(self, pair, src) -> None:
        """The object's static colour (CtDraw's FillColor_StaticColor): the
        OFF colour, state 0, array colour 0, the at-minimum colour and the
        Appearance page's field."""
        if self._syncing or not self._fc.static_editable:
            return
        pair = tuple(pair)
        self._fc.static = pair
        if self._fc.states:
            self._fc.states[0] = pair
        self._fc.array[0] = pair
        self._syncing = True
        try:
            for fld in (self._off_col, self._min_col):
                if fld is not src and fld.pair() != pair:
                    fld.set_pair(*pair)
            app = self._appearance_field()
            if app is not None and app is not src and app.pair() != pair:
                app.set_pair(*pair)
            if self._states.rowCount():
                self._states.setItem(0, 1, _colour_item(pair))
            self._array.setItem(0, 1, _colour_item(pair))
            if src is not self._state_col and self._states.currentRow() == 0:
                self._state_col.set_pair(*pair)
            if src is not self._array_col and self._array.currentRow() == 0:
                self._array_col.set_pair(*pair)
        finally:
            self._syncing = False

    def _set_on(self, pair, src) -> None:
        """COL_MAXON: the On / Off ON colour = the Gradient at-maximum."""
        if self._syncing:
            return
        self._fc.on = tuple(pair)
        self._fc.cleared = False
        self._syncing = True
        try:
            for fld in (self._on_col, self._max_col):
                if fld is not src:
                    fld.set_pair(*pair)
        finally:
            self._syncing = False

    # -- edits ------------------------------------------------------------------------------
    def _on_type(self, kind: int, checked: bool) -> None:
        if not checked:
            return
        self._stack.setCurrentIndex(kind)
        if self._syncing:
            return
        self._fc.mode = kind
        self._fc.cleared = False
        # the expression carries over (FillColor_Expression): each panel's
        # field already shows it in the panel's own form
        self._fc.expression = self._current_expression()

    def _show_condition(self) -> None:
        r = self._conds.currentRow()
        if not (0 <= r < len(self._fc.conditions)):
            r = 0
        self._cond_label.setText("Condition %s:" % "ABCDE"[r])
        was = self._syncing
        self._syncing = True
        try:
            self._show_text(self._cond_edit, self._fc.conditions[r])
        finally:
            self._syncing = was

    def _on_condition_edit(self, text: str) -> None:
        if self._syncing:
            return
        r = max(0, self._conds.currentRow())
        if r >= len(self._fc.conditions):
            return
        self._fc.cleared = False
        self._fc.conditions[r] = text
        if r == 0:
            self._show_expression(text, self._cond_edit)
            self._fc.expression = self._current_expression()
            return
        self._syncing = True
        try:
            it = self._conds.item(r, 1)
            if it is not None:
                it.setText(text)
        finally:
            self._syncing = False

    def _add_condition(self) -> None:
        if len(self._fc.conditions) >= F.MAX_CONDITIONS or self._read_only:
            return
        self._fc.conditions.append("")
        self._fill_conditions(len(self._fc.conditions) - 1)
        self._fill_states()
        self._cond_edit.setFocus()

    def _delete_condition(self) -> None:
        conds = self._fc.conditions
        if len(conds) <= 1 or self._read_only:
            return
        r = self._conds.currentRow()
        if not (0 <= r < len(conds)):
            r = len(conds) - 1
        del conds[r]
        self._fc.cleared = False
        if r == 0:
            self._show_expression(conds[0], self._cond_edit)
            self._fc.expression = self._current_expression()
        self._fill_conditions(min(r, len(conds) - 1))
        self._fill_states()

    def _show_state(self) -> None:
        r = self._states.currentRow()
        if not (0 <= r < len(self._fc.states)):
            return
        n = len(self._fc.conditions)
        self._state_label.setText(combination_label(r, n) + ":")
        self._state_label.setToolTip(combination_text(r, n))
        was = self._syncing
        self._syncing = True
        try:
            self._state_col.set_pair(*self._fc.states[r])
            ro = self._read_only or (r == 0 and not self._fc.static_editable)
            self._state_col.set_read_only(ro)
            self._state_col.setToolTip(_NO_STATIC if r == 0 and not self._fc.static_editable else "")
        finally:
            self._syncing = was

    def _on_state_colour(self) -> None:
        if self._syncing:
            return
        r = self._states.currentRow()
        if not (0 <= r < len(self._fc.states)):
            return
        pair = self._state_col.pair()
        if r == 0:
            self._set_static(pair, self._state_col)
            return
        self._fc.states[r] = tuple(pair)
        self._fc.cleared = False
        self._states.setItem(r, 1, _colour_item(pair))

    def _show_array(self) -> None:
        r = self._array.currentRow()
        if not (0 <= r < F.ARRAY_SIZE):
            return
        self._array_label.setText("Colour of %d:" % r)
        was = self._syncing
        self._syncing = True
        try:
            self._array_col.set_pair(*self._fc.array[r])
            ro = self._read_only or (r == 0 and not self._fc.static_editable)
            self._array_col.set_read_only(ro)
            self._array_col.setToolTip(_NO_STATIC if r == 0 and not self._fc.static_editable else "")
        finally:
            self._syncing = was

    def _on_array_colour(self) -> None:
        if self._syncing:
            return
        r = self._array.currentRow()
        if not (0 <= r < F.ARRAY_SIZE):
            return
        pair = self._array_col.pair()
        if r == 0:
            self._set_static(pair, self._array_col)
            return
        self._fc.array[r] = tuple(pair)
        self._fc.cleared = False
        self._array.setItem(r, 1, _colour_item(pair))

    def _show_threshold(self) -> None:
        r = self._thr.currentRow()
        rows = self._fc.thresholds
        has = 0 <= r < len(rows)
        for w in (self._thr_cmp, self._thr_val):
            w.setEnabled(has and not self._read_only)
        self._thr_col.set_read_only(not has or self._read_only)
        if not has:
            return
        t = rows[r]
        was = self._syncing
        self._syncing = True
        try:
            self._thr_cmp.setCurrentIndex(_COMPARISONS.index(t.comparison))
            self._thr_val.setRange(min(0, t.value), max(100, t.value))
            self._thr_val.setValue(t.value)
            self._thr_col.set_pair(t.on, t.off)
        finally:
            self._syncing = was

    def _on_threshold_edit(self, *_a) -> None:
        if self._syncing:
            return
        r = self._thr.currentRow()
        if not (0 <= r < len(self._fc.thresholds)):
            return
        cmp_ = _COMPARISONS[max(0, self._thr_cmp.currentIndex())]
        on, off = self._thr_col.pair()
        self._fc.thresholds[r] = F.Threshold(
            cmp_.startswith(">"), cmp_.endswith("="), self._thr_val.value(), on, off
        )
        self._fc.cleared = False
        self._fill_thresholds(r)

    def _add_threshold(self) -> None:
        rows = self._fc.thresholds
        if len(rows) >= F.MAX_THRESHOLDS or self._read_only:
            return
        r = self._thr.currentRow()
        base = rows[r] if 0 <= r < len(rows) else F.Threshold(False, False, 50, 0xFF0000, 0xFF0000)
        at = r + 1 if 0 <= r < len(rows) else len(rows)
        rows.insert(at, base)
        self._fc.cleared = False
        self._fill_thresholds(at)

    def _delete_threshold(self) -> None:
        rows = self._fc.thresholds
        r = self._thr.currentRow()
        if not (0 <= r < len(rows)) or self._read_only:
            return
        del rows[r]
        self._fc.cleared = False
        self._fill_thresholds(min(r, len(rows) - 1))

    def _clear(self) -> None:
        """Clear Property: the object's colour animation removed (the page
        back to an unconfigured On / Off; its own fill colour stays)."""
        if self._read_only:
            return
        self._load(F.cleared(self._fc))

    # -- the sheet's editor contract ----------------------------------------------------------
    def current(self) -> F.FillColour:
        """What the page shows now."""
        return self._fc.copy()

    def _widget_for(self, name: str):
        if name == "expression":
            return {
                F.ON_OFF: self._on_expr,
                F.ARRAY: self._arr_expr,
                F.THRESHOLD: self._thr_expr,
                F.GRADIENT: self._grad_expr,
            }.get(self._fc.mode, self._on_expr)
        if name.startswith("condition:"):
            self._conds.setCurrentCell(int(name.split(":")[1]), 1)
            return self._cond_edit
        if name.startswith("threshold:"):
            r = int(name.split(":")[1])
            if r < self._thr.rowCount():
                self._thr.setCurrentCell(r, 2)
            return self._thr
        return self.widget

    def problem(self):
        """(message, widget) when the page cannot be stored."""
        fc = self.current()
        if not F.has_changes(self._initial, fc):
            return None
        probs = F.problems(fc)
        if probs:
            name, msg = probs[0]
            return msg, self._widget_for(name)
        if fc.mode in F.RANGED:
            fld = self._thr_expr if fc.mode == F.THRESHOLD else self._grad_expr
            msg = fld.range_problem()
            if msg:
                return "Colour expression: %s" % msg, fld.range_field.minimum
        from ...editing import UnencodableTextError, guard_text

        try:
            guard_text(*F.written_texts(fc))
        except UnencodableTextError as exc:
            return "%s. Replace those characters and apply again." % exc, self.widget
        return None

    def apply(self) -> bool:
        s = self.sheet
        try:
            done = s._doc.apply_fill_colour(s._index, self._initial, self.current())
        except Exception as exc:  # noqa: BLE001 -- reported in the sheet
            s._fail("The fill colour could not be written: %s" % exc, self.widget)
            return False
        if done is False:
            s._fail("The fill colour could not be stored: this object has no property list.", self.widget)
            return False
        return bool(done)

    def reload(self) -> None:
        s = self.sheet
        self._initial = s._doc.fill_colour(s._index)
        self._load(self._initial)

    def has_pending_edits(self) -> bool:
        """What the page shows differs from what it loaded."""
        return bool(F.has_changes(self._initial, self.current()))

    def set_read_only(self, ro: bool) -> None:
        self._read_only = bool(ro)
        for b in self._radios.values():
            b.setEnabled(not ro)
        for f in self._expr_fields() + (self._cond_edit,):
            f.setReadOnly(ro)
        for fld in (self._on_col, self._max_col):
            fld.set_read_only(ro)
        for fld in (self._off_col, self._min_col):
            fld.set_read_only(ro or not self._fc.static_editable)
        self._btn_clear.setEnabled(not ro)
        self._fill_conditions(self._conds.currentRow())
        self._fill_thresholds()
        self._show_state()
        self._show_array()


def build_fill_color(sheet, page):
    ed = FillColorEditor(sheet)
    sheet.add_editor(ed)
    sheet.fill_color_editor = ed
    return ed.widget


# --------------------------------------------------------------------------- Fill > Level
class LevelEditor:
    """Fill > Level's direction and background colour."""

    phase = "after"

    def __init__(self, sheet, buttons, background: ColourPairField):
        self.sheet = sheet
        self._buttons = buttons
        self._bg = background
        self._syncing = False
        self._cleared = False
        self.reload()

    def direction(self) -> int:
        for d, b in self._buttons.items():
            if b.isChecked():
                return d
        return F.UP

    def _set_direction(self, d: int) -> None:
        self._buttons.get(d, self._buttons[F.UP]).setChecked(True)

    def clear(self) -> None:
        """Clear Property: the direction and colour back to CtDraw's
        defaults (the sheet removes the keys)."""
        self._cleared = True
        self._syncing = True
        try:
            self._set_direction(F.UP)
            self._bg.set_pair(*F.TRANSPARENT)
        finally:
            self._syncing = False

    def reload(self) -> None:
        s = self.sheet
        props = s._doc.object_properties(s._index)
        get = F._getter(props)
        self._fil0 = get("FIL")
        self._dir0 = F.direction(props)
        self._bg0 = F.level_background(props)
        self._syncing = True
        try:
            self._set_direction(self._dir0)
            self._bg.set_pair(*self._bg0)
        finally:
            self._syncing = False
        self._cleared = False
        # the percentages this editor stored with a new level expression: the
        # sheet's fields show them (and compare against them) from now on
        for key in ("FILMINPC", "FILMAXPC"):
            if key not in s._initial:
                continue  # (the sheet loads its fields itself)
            s._syncing = True
            try:
                for f, w in s.field_entries(key):
                    s._set_widget(f, w, get(key))
            finally:
                s._syncing = False
            s._initial[key] = s._state(key)

    def refresh(self) -> None:
        """The page changed underneath and nothing here was typed: show the
        direction and background as they are now (the sheet shows its own
        fields, a typed percentage included, itself)."""
        s = self.sheet
        props = s._doc.object_properties(s._index)
        get = F._getter(props)
        self._fil0 = get("FIL")
        self._dir0 = F.direction(props)
        self._bg0 = F.level_background(props)
        self._syncing = True
        try:
            self._set_direction(self._dir0)
            self._bg.set_pair(*self._bg0)
        finally:
            self._syncing = False
        self._cleared = False

    def problem(self):
        return None

    def apply(self) -> bool:
        s = self.sheet
        doc, i = s._doc, s._index
        props = doc.object_properties(i)
        get = F._getter(props)
        fil = get("FIL")
        if not fil:
            return False  # no level: nothing of it is stored
        new_level = fil != self._fil0 or self._cleared
        want: List[Tuple[str, Optional[str]]] = []
        if new_level:
            for k, v in (("FILMINPC", "0"), ("FILMAXPC", "100")):
                if get(k) is None:
                    want.append((k, v))
        d = self.direction()
        if d != self._dir0 or (new_level and F.direction(props) != d):
            want.append(("FILMODE", str(d) if d else None))
        bg = self._bg.pair()
        if bg != self._bg0 or (new_level and get("FILCOL") is None):
            want.append(("FILCOL", F.fmt_pair(bg)))
        edits = [(k, v) for k, v in want if get(k) != v]
        if not edits:
            return False
        try:
            ok = doc.set_fill_keys(i, edits, label="fill level")
        except Exception as exc:  # noqa: BLE001 -- reported in the sheet
            s._fail("The fill level could not be written: %s" % exc)
            return False
        if not ok:
            s._fail("The fill direction and background could not be stored on this object.")
        return bool(ok)

    def set_read_only(self, ro: bool) -> None:
        for b in self._buttons.values():
            b.setEnabled(not ro)
        self._bg.set_read_only(ro)


def build_fill_level(sheet, page):
    w = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(w)
    fil, mn, mx = (sheet.make_field(f) for f in LEVEL.fields)
    fil.setToolTip("The value that sets how full the object is")
    form.addRow(LEVEL.fields[0].label, fil)
    if getattr(fil, "range_field", None) is not None:
        form.addRow("", fil.range_field)
    mn.setToolTip("How full (0 to 100 %) the object is at the expression's minimum")
    mx.setToolTip("How full (0 to 100 %) the object is at the expression's maximum")
    # an absent percentage is stored as CtDraw's default with a new level
    # expression (LevelEditor.apply)
    for spin, default in ((mn, 0), (mx, 100)):
        if isinstance(spin, QtWidgets.QSpinBox):
            spin.setSpecialValueText("default (%d %%)" % default)
    form.addRow(LEVEL.fields[1].label, mn)
    form.addRow(LEVEL.fields[2].label, mx)
    row = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    group = QtWidgets.QButtonGroup(row)
    arrows = {
        F.UP: QtCore.Qt.UpArrow,
        F.DOWN: QtCore.Qt.DownArrow,
        F.LEFT: QtCore.Qt.LeftArrow,
        F.RIGHT: QtCore.Qt.RightArrow,
    }
    tips = {
        F.UP: "Fill from the bottom up",
        F.DOWN: "Fill from the top down",
        F.LEFT: "Fill from right to left",
        F.RIGHT: "Fill from left to right",
    }
    buttons = {}
    for d, label in F.DIRECTIONS:
        b = QtWidgets.QToolButton()
        b.setArrowType(arrows[d])
        b.setText(label)
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        b.setCheckable(True)
        b.setToolTip(tips[d])
        group.addButton(b, d)
        buttons[d] = b
        h.addWidget(b)
    group.setExclusive(True)
    h.addStretch(1)
    form.addRow("Fill direction:", row)
    bg = ColourPairField("Background colour", transparent=True)
    bg.setToolTip("The colour of the unfilled part")
    form.addRow("Background colour:", bg)
    ed = LevelEditor(sheet, buttons, bg)
    clear = QtWidgets.QPushButton("Clear Property")
    clear.setToolTip("Remove the fill level settings")

    def on_clear():
        sheet._clear(LEVEL)
        ed.clear()

    clear.clicked.connect(on_clear)
    form.addRow("", clear)
    hint = QtWidgets.QLabel(
        "The object fills from the At minimum level to the At maximum "
        "level as the expression moves through its range."
    )
    note(hint)
    set_note_role(hint, "text_dim")
    form.addRow(hint)
    sheet.add_editor(ed)
    sheet.fill_level_editor = ed
    try:
        from ..help import set_help

        set_help(w, HELP_LEVEL)
    except Exception:  # noqa: BLE001
        pass
    return w


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"fill_color": build_fill_color, "fill_level": build_fill_level}
