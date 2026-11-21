"""Appearance > Vertices, for polygons and polylines."""

from __future__ import annotations

from typing import List, Tuple

from PySide6 import QtCore, QtWidgets

from ...format import geometry as G
from ...format import vertexanim as VA
from .fields import _Field, _Sub
from ..style.theme import set_note_role

#: the vertex keys (the sheet's key contract, objprops.sheet_keys); the page
#: is a custom editor, the family is what Clear Property removes
VERTICES = _Sub("Vertices", [], family=tuple("%s%s*" % (VA.PREFIX, n) for n in VA.NAMES))
SUBS = [VERTICES]
FIELDS = [_Field("", "%s%s*" % (VA.PREFIX, n)) for n in VA.NAMES]

HELP_TOPIC = "properties#vertices"

_NOTE = (
    "A vertex with an expression moves in a straight line from its From offset (at "
    "the minimum) to its To offset (at the maximum)."
)
_NO_NODES = "This polygon has no vertex list of its own. Its shape comes from its genie or library element."


class _CoordDelegate(QtWidgets.QStyledItemDelegate):
    """X / Y cells edit as whole page pixels (-32768 .. 32767)."""

    def createEditor(self, parent, option, index):  # noqa: N802 -- Qt naming
        sp = QtWidgets.QSpinBox(parent)
        sp.setRange(G.COORD_MIN, G.COORD_MAX)
        sp.setFrame(False)
        return sp

    def setEditorData(self, editor, index):  # noqa: N802 -- Qt naming
        try:
            editor.setValue(int(index.data() or 0))
        except TypeError, ValueError:
            editor.setValue(0)

    def setModelData(self, editor, model, index):  # noqa: N802 -- Qt naming
        editor.interpretText()
        model.setData(index, str(editor.value()))


def _offset_spin(tip: str) -> QtWidgets.QSpinBox:
    sp = QtWidgets.QSpinBox()
    sp.setRange(VA.OFFSET_MIN, VA.OFFSET_MAX)
    sp.setSuffix(" px")
    sp.setToolTip(tip)
    sp.setKeyboardTracking(False)
    return sp


class VerticesEditor:
    """The Vertices page: its widgets, the per-vertex settings while the
    sheet is open, the node list it writes back (in the sheet's undo step)."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._syncing = False
        self._read_only = False
        self._row = -1
        self._nodes0: List[Tuple[int, int]] = []
        self._nodes: List[Tuple[int, int]] = []
        self._anim0: List[VA.VertexAnim] = []
        self._anim: List[VA.VertexAnim] = []
        self._range_bad: dict = {}
        self.widget = self._build()
        self._load()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except Exception:  # noqa: BLE001
            pass

    # -- widgets ----------------------------------------------------------------------
    def _build(self) -> QtWidgets.QWidget:
        from .expression import ExpressionField
        from .range_field import RangeField

        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        self._no_nodes = QtWidgets.QLabel(_NO_NODES)
        self._no_nodes.setWordWrap(True)
        v.addWidget(self._no_nodes)
        row = QtWidgets.QHBoxLayout()
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Vertex", "X", "Y", "Expression"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.table.setItemDelegateForColumn(1, _CoordDelegate(self.table))
        self.table.setItemDelegateForColumn(2, _CoordDelegate(self.table))
        self.table.setColumnWidth(0, 56)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 70)
        from ..tables import make_resizable

        make_resizable(self.table, "ObjectPropertiesDialog/vertices")
        self.table.setToolTip("The vertices in drawing order. Double-click X or Y to move a vertex.")
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._select(r))
        self.table.itemChanged.connect(self._on_item_changed)
        row.addWidget(self.table, 1)
        v.addLayout(row, 1)

        box = QtWidgets.QGroupBox("Animation of the selected vertex")
        self._anim_box = box
        f = QtWidgets.QFormLayout(box)
        self._expr_label = QtWidgets.QLabel("Expression for vertex 1:")
        self.expr = ExpressionField(self.sheet.expression_context)
        self.expr.set_names(self.sheet.expression_context.completion_model())
        from ..expression_check import checker_for_document

        self.expr.set_checker(checker_for_document(getattr(self.sheet, "_doc", None)))
        self.expr.setMaxLength(VA.EXPR_LIMIT)
        self.expr.setToolTip("The tag or expression that moves this vertex. Empty: it does not move.")
        self.expr.attach_range(RangeField())
        f.addRow(self._expr_label, self.expr)
        f.addRow("", self.expr.range_field)
        self.sp = {}
        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("X"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("Y"), 0, 2)
        tips = {
            VA.FROM_X: "Pixels right of the node at the minimum (negative = left)",
            VA.FROM_Y: "Pixels below the node at the minimum (negative = up)",
            VA.TO_X: "Pixels right of the node at the maximum (negative = left)",
            VA.TO_Y: "Pixels below the node at the maximum (negative = up)",
        }
        for r, (label, names) in enumerate(
            (
                ("From offset (at minimum):", (VA.FROM_X, VA.FROM_Y)),
                ("To offset (at maximum):", (VA.TO_X, VA.TO_Y)),
            ),
            start=1,
        ):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            for c, name in enumerate(names, start=1):
                sp = _offset_spin(tips[name])
                sp.valueChanged.connect(self._changed)
                self.sp[name] = sp
                grid.addWidget(sp, r, c)
        grid.setColumnStretch(3, 1)
        f.addRow(grid)
        note = QtWidgets.QLabel(_NOTE)
        note.setWordWrap(True)
        set_note_role(note, "text_dim")
        f.addRow(note)
        self.expr.valueChanged.connect(self._changed)
        v.addWidget(box)
        self._btn_clear = QtWidgets.QPushButton("Clear Property")
        self._btn_clear.setToolTip("Remove the animation of every vertex")
        self._btn_clear.clicked.connect(lambda: self.sheet._clear(VERTICES))
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self._btn_clear)
        h.addStretch(1)
        v.addLayout(h)
        return page

    # -- loading ------------------------------------------------------------------------
    def _load(self) -> None:
        s = self.sheet
        info = s._doc.reshape_info(s._index)
        self._nodes0 = list(info.nodes) if info is not None else []
        self._nodes = list(self._nodes0)
        self._anim0 = VA.read(s._props, len(self._nodes0))
        self._anim = [a.copy() for a in self._anim0]
        self._range_bad = {}
        has = bool(self._nodes0)
        self._no_nodes.setVisible(not has)
        self.table.setVisible(has)
        self._anim_box.setVisible(has)
        self._fill_table()
        self._row = -1
        if has:
            self.table.setCurrentCell(0, 1)
            self._select(0)

    def _fill_table(self) -> None:
        self._syncing = True
        try:
            self.table.setRowCount(len(self._nodes))
            for r, (x, y) in enumerate(self._nodes):
                num = QtWidgets.QTableWidgetItem(str(r + 1))
                num.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                self.table.setItem(r, 0, num)
                for c, val in ((1, x), (2, y)):
                    it = QtWidgets.QTableWidgetItem(str(val))
                    flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
                    if not self._read_only:
                        flags |= QtCore.Qt.ItemIsEditable
                    it.setFlags(flags)
                    self.table.setItem(r, c, it)
                self._show_expr_cell(r)
        finally:
            self._syncing = False

    def _show_expr_cell(self, r: int) -> None:
        a = self._anim[r] if r < len(self._anim) else VA.VertexAnim()
        it = QtWidgets.QTableWidgetItem(a.expr or "")
        it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        it.setToolTip(a.expr or "No animation")
        was = self._syncing
        self._syncing = True
        try:
            self.table.setItem(r, 3, it)
        finally:
            self._syncing = was

    # -- the selected vertex ----------------------------------------------------------------
    def _select(self, r: int) -> None:
        if self._syncing or r == self._row or not 0 <= r < len(self._anim):
            return
        self._store()
        self._row = r
        a = self._anim[r]
        self._syncing = True
        try:
            self._expr_label.setText("Expression for vertex %d:" % (r + 1))
            self.expr.set_value(a.expr or "")
            for name, sp in self.sp.items():
                val = VA.offset_value(a.offsets.get(name))
                if val is None:
                    # a stored value the spin cannot show: kept as it is
                    sp.setValue(0)
                    sp.setEnabled(False)
                    sp.setToolTip(
                        "The stored value '%s' is not a whole number this field "
                        "can show. It is kept unchanged (Clear Property removes "
                        "it)." % a.offsets.get(name)
                    )
                    sp.setProperty("_ctg_locked", True)
                else:
                    sp.setValue(val)
                    sp.setEnabled(not self._read_only)
                    sp.setProperty("_ctg_locked", False)
        finally:
            self._syncing = False

    def _store(self) -> None:
        """The fields of the shown vertex into its settings."""
        r = self._row
        if not 0 <= r < len(self._anim):
            return
        a = self._anim[r]
        text = self.expr.value()
        a.expr = text if text.strip() else None
        msg = self.expr.range_problem()
        if msg:
            self._range_bad[r] = msg
        else:
            self._range_bad.pop(r, None)
        for name, sp in self.sp.items():
            if sp.property("_ctg_locked"):
                continue
            stored = a.offsets.get(name)
            if sp.value() == VA.offset_value(stored):
                continue  # unchanged: the stored spelling stays
            a.offsets[name] = str(sp.value())
        self._show_expr_cell(r)

    def _changed(self, *_a) -> None:
        if self._syncing or self._read_only:
            return
        self._store()
        # the Vertices check mark follows the pending keys
        self.sheet._refresh_marks(self.sheet._pending_props())

    def _on_item_changed(self, it: QtWidgets.QTableWidgetItem) -> None:
        if self._syncing or it.column() not in (1, 2):
            return
        r = it.row()
        if not 0 <= r < len(self._nodes):
            return
        try:
            v = int(str(it.text()).strip())
        except ValueError:
            v = None
        if v is None or not G.COORD_MIN <= v <= G.COORD_MAX:
            self._syncing = True
            try:
                it.setText(str(self._nodes[r][it.column() - 1]))
            finally:
                self._syncing = False
            return
        x, y = self._nodes[r]
        self._nodes[r] = (v, y) if it.column() == 1 else (x, v)

    def select_vertex(self, n: int) -> None:
        """Show vertex ``n`` (from 0) -- tests / scripting."""
        self.table.setCurrentCell(n, 1)
        self._select(n)

    def set_vertex(self, n: int, x: int, y: int) -> None:
        """Type a vertex's position into the list (tests / scripting)."""
        self.table.item(n, 1).setText(str(int(x)))
        self.table.item(n, 2).setText(str(int(y)))

    # -- the sheet's editor contract ----------------------------------------------------------
    def key_edits(self):
        if not self._anim:
            return []
        return VA.edits(self.sheet._props, self._anim0, self._anim)

    def on_clear(self, sub) -> None:
        if sub is not VERTICES:
            return
        self._anim = [VA.VertexAnim() for _ in self._anim]
        self._range_bad = {}
        for r in range(len(self._anim)):
            self._show_expr_cell(r)
        r, self._row = self._row, -1
        self._select(max(0, r))

    def _current_nodes(self) -> List[Tuple[int, int]]:
        """The object's nodes as the document holds them NOW (the sheet is
        modeless: the canvas can reshape the object while it is open)."""
        info = self.sheet._doc.reshape_info(self.sheet._index)
        return list(info.nodes) if info is not None else []

    def problem(self):
        self._store()
        if self._nodes0 and (self._nodes != self._nodes0 or self.key_edits()):
            now = self._current_nodes()
            if len(now) != len(self._nodes0):
                # nodes added / deleted on the canvas since this page loaded:
                # its node list and vertex numbers no longer match the object
                return (
                    "The polygon's vertices were changed on the canvas after this "
                    "dialog opened: it now has %d vertices, this page shows %d. "
                    "Close the dialog and open it again to edit the vertices."
                    % (len(now), len(self._nodes0)),
                    self.table,
                )
        for r, a in enumerate(self._anim):
            if a.expr is not None and len(a.expr) > VA.EXPR_LIMIT:
                self.select_vertex(r)
                return (
                    "Vertex %d: the expression is %d characters with its range; the "
                    "maximum is %d. Shorten it and apply again." % (r + 1, len(a.expr), VA.EXPR_LIMIT),
                    self.expr,
                )
        for r, msg in sorted(self._range_bad.items()):
            if r < len(self._anim) and self._anim[r].animated:
                self.select_vertex(r)
                return "Vertex %d: %s" % (r + 1, msg), self.expr.range_field.minimum
        return None

    def apply(self) -> bool:
        s = self.sheet
        if not self._nodes or self._nodes == self._nodes0:
            return False
        nodes = list(self._nodes)
        now = self._current_nodes()
        if now != self._nodes0 and len(now) == len(nodes):
            # the canvas moved nodes since the page loaded: only the vertices
            # typed here change, the others keep where the canvas put them
            nodes = [mine if mine != was else cur for mine, was, cur in zip(self._nodes, self._nodes0, now)]
        try:
            ok = s._doc.reshape_nodes(s._index, nodes, "vertices")
        except Exception as exc:  # noqa: BLE001 -- reported in the sheet
            s._fail("The vertices could not be moved: %s" % exc, self.table)
            return False
        if not ok:
            s._fail(
                "The vertices could not be moved: this polygon has no vertex list ctgedit can change.",
                self.table,
            )
        return bool(ok)

    def has_pending_edits(self) -> bool:
        """A vertex moved or its animation changed on the page, not applied yet."""
        self._store()
        return bool(self._nodes != self._nodes0 or self.key_edits())

    def reload(self) -> None:
        r = self._row
        self._load()
        if 0 < r < len(self._anim):
            self.select_vertex(r)

    def set_read_only(self, ro: bool) -> None:
        self._read_only = bool(ro)
        self.expr.setReadOnly(ro)
        for sp in self.sp.values():
            sp.setEnabled(not ro and not sp.property("_ctg_locked"))
        self._btn_clear.setEnabled(not ro)
        self._fill_table()


def build_vertices(sheet, page):
    ed = VerticesEditor(sheet)
    sheet.add_editor(ed)
    sheet.vertices_editor = ed
    return ed.widget


BUILDERS = {"vertices": build_vertices}
