"""Edit > Library > Genie Form: design a genie's form and save it to the .frm file."""

from __future__ import annotations

from ...infra.paths import path_key

import copy
import os
from collections import OrderedDict
from typing import Callable, List, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import genieform as GF
from ...format.genieform import DraftItem, FormField
from ..messages import warn

#: a field's least width in columns ('{', its number, '}')
MIN_FIELD = 3
#: the grid shown (columns, lines): it grows with the form
GRID_COLUMNS, GRID_LINES = 100, 16
#: a zero-width space (lets a long path break at its folders)
ZWSP = "\u200b"


def _width(it: DraftItem) -> int:
    """The columns an item takes on its line (a rule takes none)."""
    if it.kind == "text":
        return len(it.text)
    if it.kind == "field":
        return it.width
    return 0


def overlaps(items: Sequence[DraftItem]) -> Optional[int]:
    """The first line where two prompts or fields overlap, or None."""
    by_line = {}
    for it in items:
        if it.kind != "line":
            by_line.setdefault(it.line, []).append(it)
    for line in sorted(by_line):
        row = sorted(by_line[line], key=lambda x: x.col)
        for a, b in zip(row, row[1:]):
            if a.col + _width(a) > b.col:
                return line
    return None


class _Design(QtWidgets.QGraphicsView):
    """The form's grid: prompts, fields and rules as boxes on it."""

    changed = QtCore.Signal()  # an item was moved or sized (committed)
    selection = QtCore.Signal()

    def __init__(self, editor: "GenieFormEditor"):
        super().__init__()
        self.editor = editor
        fm = QtGui.QFontMetrics(self.font())
        self.cw = max(7, fm.averageCharWidth())
        self.lh = max(22, fm.height() + 10)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.scene().selectionChanged.connect(self.selection)
        self.setRenderHint(QtGui.QPainter.Antialiasing, False)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.setMinimumHeight(self.lh * 8)
        self._boxes: List["_Box"] = []

    # -- the grid ---------------------------------------------------------------------
    def drawBackground(self, painter: QtGui.QPainter, rect) -> None:  # noqa: N802
        pal = self.palette()
        painter.fillRect(rect, pal.color(QtGui.QPalette.Base))
        dim = pal.color(QtGui.QPalette.Mid)
        dim.setAlpha(70)
        painter.setPen(QtGui.QPen(dim, 0))
        r = self.sceneRect()
        lines, cols = int(r.height() / self.lh), int(r.width() / self.cw)
        for k in range(lines + 1):
            y = k * self.lh
            painter.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(r.width(), y))
        strong = QtGui.QColor(dim)
        strong.setAlpha(140)
        for c in range(0, cols + 1, 10):
            painter.setPen(QtGui.QPen(strong if c else dim, 0))
            x = c * self.cw
            painter.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, r.height()))

    def rebuild(self, items: Sequence[DraftItem], keep: Sequence[DraftItem] = ()) -> None:
        """The boxes of ``items`` (``keep`` = the items to select again)."""
        sc = self.scene()
        sc.blockSignals(True)
        for b in self._boxes:
            sc.removeItem(b)
        self._boxes = []
        cols = max(
            [GRID_COLUMNS]
            + [it.col + _width(it) + 10 for it in items]
            + [it.width + 10 for it in items if it.kind == "line"]
        )
        lines = max([GRID_LINES] + [it.line + 4 for it in items])
        sc.setSceneRect(0, 0, cols * self.cw, lines * self.lh)
        for it in items:
            b = _Box(self, it)
            sc.addItem(b)
            self._boxes.append(b)
            if any(it is k for k in keep):
                b.setSelected(True)
        sc.blockSignals(False)
        self.selection.emit()

    def selected_items(self) -> List[DraftItem]:
        return [b.item for b in self._boxes if b.isSelected()]

    def select(self, items: Sequence[DraftItem]) -> None:
        sc = self.scene()
        sc.blockSignals(True)
        for b in self._boxes:
            b.setSelected(any(b.item is it for it in items))
        sc.blockSignals(False)
        self.selection.emit()
        for b in self._boxes:
            if b.isSelected():
                self.ensureVisible(b)
                break

    def refresh(self) -> None:
        for b in self._boxes:
            b.place()

    def keyPressEvent(self, ev: QtGui.QKeyEvent) -> None:  # noqa: N802
        """Arrow keys move the selection one cell; Shift moves a large step."""
        steps = {
            QtCore.Qt.Key_Left: (-1, 0),
            QtCore.Qt.Key_Right: (1, 0),
            QtCore.Qt.Key_Up: (0, -1),
            QtCore.Qt.Key_Down: (0, 1),
        }
        if ev.key() in steps and self.selected_items():
            dc, dl = steps[ev.key()]
            if ev.modifiers() & QtCore.Qt.ShiftModifier:
                from ..prefs import settings as S

                n = S.whole("opt/big_nudge", S.BIG_NUDGE_RANGE)
                dc, dl = dc * n, dl * n
            self.editor.move_items(self.selected_items(), dc, dl)
            return
        if ev.key() == QtCore.Qt.Key_Delete:
            self.editor.delete_selected()
            return
        if ev.key() == QtCore.Qt.Key_Escape and self.selected_items():
            self.select(())
            return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        super().mouseDoubleClickEvent(ev)
        self.editor.focus_properties()


class _Box(QtWidgets.QGraphicsRectItem):
    """One prompt, field or rule on the grid; dragged to move, its right edge to size."""

    EDGE = 5

    def __init__(self, design: _Design, item: DraftItem):
        super().__init__()
        self.design = design
        self.item = item
        self._resizing = False
        self._press = None
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.place()

    def place(self) -> None:
        it, d = self.item, self.design
        if it.kind == "line":
            self.setPos(0, (it.line - 1) * d.lh)
            self.setRect(0, d.lh * 0.5 - 3, max(1, it.width) * d.cw, 6)
        else:
            self.setPos((it.col - 1) * d.cw, (it.line - 1) * d.lh)
            w = max(1, _width(it)) * d.cw
            self.setRect(0, 3, w, d.lh - 6)
        self.setToolTip(self._tip())
        self.update()

    def _tip(self) -> str:
        it = self.item
        if it.kind == "field" and it.field is not None:
            s = "Field: %s (%d characters)" % (it.field.name or "no parameter", it.field.size)
            if it.field.list_form:
                s += "\nList: %s, %s" % (it.field.list_form, it.field.list_field)
            return s
        if it.kind == "line":
            return "Rule, %d long" % it.width
        return "Prompt"

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        pal = self.design.palette()
        r = self.rect()
        sel = self.isSelected()
        hi = pal.color(QtGui.QPalette.Highlight)
        it = self.item
        if it.kind == "line":
            painter.setPen(QtGui.QPen(hi if sel else pal.color(QtGui.QPalette.Text), 2))
            y = r.center().y()
            painter.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
            return
        if it.kind == "field":
            painter.setBrush(pal.color(QtGui.QPalette.AlternateBase))
            painter.setPen(QtGui.QPen(hi if sel else pal.color(QtGui.QPalette.Mid), 1.5))
            painter.drawRect(r)
            name = it.field.name if it.field is not None else ""
            text = name or "(no parameter)"
            painter.setPen(pal.color(QtGui.QPalette.Text if name else QtGui.QPalette.Mid))
        else:
            if sel:
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(hi, 1, QtCore.Qt.DashLine))
                painter.drawRect(r)
            painter.setPen(pal.color(QtGui.QPalette.Text))
            text = it.text
        painter.drawText(
            r.adjusted(3, 0, -2, 0),
            QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft | QtCore.Qt.TextSingleLine,
            text,
        )

    # -- moving and sizing ------------------------------------------------------------
    def itemChange(self, change, value):  # noqa: N802
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and not self._resizing:
            d = self.design
            x = 0.0 if self.item.kind == "line" else max(0.0, round(value.x() / d.cw) * d.cw)
            y = max(0.0, round(value.y() / d.lh) * d.lh)
            return QtCore.QPointF(x, y)
        return super().itemChange(change, value)

    def _on_edge(self, pos: QtCore.QPointF) -> bool:
        return self.item.kind in ("field", "line") and abs(pos.x() - self.rect().right()) <= self.EDGE

    def hoverMoveEvent(self, ev) -> None:  # noqa: N802
        self.setCursor(QtCore.Qt.SizeHorCursor if self._on_edge(ev.pos()) else QtCore.Qt.SizeAllCursor)
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self._press = self.pos()
        self._resizing = ev.button() == QtCore.Qt.LeftButton and self._on_edge(ev.pos())
        if self._resizing:
            self.setSelected(True)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._resizing:
            d = self.design
            cols = max(MIN_FIELD if self.item.kind == "field" else 1, int(round(ev.pos().x() / d.cw)))
            r = self.rect()
            self.setRect(r.left(), r.top(), cols * d.cw, r.height())
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        # (the commit builds the boxes again: never inside this box's own handler)
        editor = self.design.editor
        if self._resizing:
            self._resizing = False
            cols = max(1, int(round(self.rect().width() / self.design.cw)))
            item = self.item
            QtCore.QTimer.singleShot(0, editor, lambda: editor.resize_item(item, cols))
            return
        super().mouseReleaseEvent(ev)
        if self._press is not None and self.pos() != self._press:
            QtCore.QTimer.singleShot(0, editor, editor.commit_drag)
        self._press = None


class GenieFormEditor(QtWidgets.QDialog):
    """The form of genie ``library.element`` (its library at ``lib_path``), see the module.
    ``params`` = the genie's declared parameters (name -> default), ``list_values(field)`` =
    a field's drop-down for the preview, ``project_dir`` = the project the form is used in
    (to say which .frm the Graphics Builder takes it from)."""

    RESIZE = "both"
    saved = QtCore.Signal(str)  # the .frm path written

    def __init__(
        self,
        lib_path: str,
        library: str,
        element: str,
        params=None,
        *,
        project_dir: str = "",
        list_values: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.lib_path = lib_path
        self.library = library
        self.element = element
        self.project_dir = project_dir or os.path.dirname(os.path.abspath(lib_path))
        self.params = OrderedDict(params or {})
        self._list_values = list_values
        self.path = GF.form_file_for(lib_path)
        self.setWindowTitle("Genie Form: %s.%s" % (library, element))
        self.setModal(False)
        self._undo: List[tuple] = []
        self._redo: List[tuple] = []
        self._saved_state = None
        self._block = False
        self._build()
        self.load()
        self.resize(1000, 760)

    # -- the window -------------------------------------------------------------------
    def _build(self) -> None:
        Q = QtWidgets
        lay = Q.QVBoxLayout(self)
        head = Q.QFormLayout()
        self.title = Q.QLineEdit()
        self.title.setPlaceholderText("%s.%s" % (self.library, self.element))
        self.title.setToolTip("The dialog's caption. Empty: the genie's name")
        self.title.textEdited.connect(lambda _t: self._changed(push=False))
        self.title.editingFinished.connect(self._commit_edit)
        head.addRow("Title:", self.title)
        self.file_note = Q.QLabel()
        self.file_note.setWordWrap(True)
        self.file_note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        head.addRow("Saved in:", self.file_note)
        lay.addLayout(head)

        bar = Q.QToolBar()
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.act_prompt = bar.addAction("Add Prompt", self.add_prompt)
        self.act_field = bar.addAction("Add Field", lambda: self.add_field(""))
        self.act_rule = bar.addAction("Add Rule", self.add_rule)
        bar.addSeparator()
        self.act_delete = bar.addAction("Delete", self.delete_selected)
        self.act_insert = bar.addAction("Insert Line", lambda: self.shift_lines(1))
        self.act_insert.setToolTip("Move the selection's line and the lines under it down")
        self.act_remove = bar.addAction("Remove Line", lambda: self.shift_lines(-1))
        self.act_remove.setToolTip("Remove the empty line above the selection")
        bar.addSeparator()
        self.act_undo = bar.addAction("Undo", self.undo)
        self.act_undo.setShortcut(QtGui.QKeySequence.Undo)
        self.act_redo = bar.addAction("Redo", self.redo)
        self.act_redo.setShortcut(QtGui.QKeySequence.Redo)
        lay.addWidget(bar)

        split = Q.QSplitter(QtCore.Qt.Horizontal)
        self.design = _Design(self)
        self.design.selection.connect(self._show_selection)
        split.addWidget(self.design)
        side = Q.QWidget()
        sl = Q.QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        self.props = Q.QStackedWidget()
        self.props.addWidget(self._build_none())
        self.props.addWidget(self._build_prompt())
        self.props.addWidget(self._build_field())
        self.props.addWidget(self._build_rule())
        box = Q.QGroupBox("Selected")
        bl = Q.QVBoxLayout(box)
        bl.addWidget(self.props)
        sl.addWidget(box)
        pbox = Q.QGroupBox("The genie's parameters")
        pl = Q.QVBoxLayout(pbox)
        self.param_list = Q.QListWidget()
        self.param_list.setToolTip("Double-click one to add it to the form with its prompt")
        self.param_list.itemDoubleClicked.connect(lambda i: self.add_parameter(i.data(QtCore.Qt.UserRole)))
        pl.addWidget(self.param_list)
        self.btn_add_param = Q.QPushButton("Add to the Form")
        self.btn_add_param.setAutoDefault(False)
        self.btn_add_param.clicked.connect(self._add_chosen_parameter)
        pl.addWidget(self.btn_add_param)
        sl.addWidget(pbox, 1)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        vsplit = Q.QSplitter(QtCore.Qt.Vertical)
        vsplit.addWidget(split)
        prev = Q.QGroupBox("Preview")
        pvl = Q.QVBoxLayout(prev)
        self.preview_holder = Q.QWidget()
        self.preview_layout = Q.QVBoxLayout(self.preview_holder)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        pvl.addWidget(self.preview_holder)
        vsplit.addWidget(prev)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        vsplit.setSizes([440, 300])
        lay.addWidget(vsplit, 1)

        self.problem = Q.QLabel()
        self.problem.setWordWrap(True)
        lay.addWidget(self.problem)
        box = Q.QDialogButtonBox()
        self.btn_revert = box.addButton("Revert", Q.QDialogButtonBox.ResetRole)
        self.btn_revert.setAutoDefault(False)
        self.btn_revert.setToolTip("Go back to the form as it is saved")
        self.btn_revert.clicked.connect(self.revert)
        self.btn_save = box.addButton("Save", Q.QDialogButtonBox.ApplyRole)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.save)
        self.btn_close = box.addButton(Q.QDialogButtonBox.Close)
        self.btn_close.setAutoDefault(False)
        self.btn_close.clicked.connect(self.close)
        lay.addWidget(box)
        sc = QtGui.QShortcut(QtGui.QKeySequence.Save, self)
        sc.activated.connect(self.save)
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._update_preview)

    def _build_none(self) -> QtWidgets.QWidget:
        w = QtWidgets.QLabel(
            "Select a prompt, field or rule to set it.\n\nDrag an item to "
            "move it, its right edge to size a field or rule. Arrow keys "
            "move the selection."
        )
        w.setWordWrap(True)
        w.setAlignment(QtCore.Qt.AlignTop)
        return w

    def _build_prompt(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(w)
        self.prompt_text = QtWidgets.QLineEdit()
        self.prompt_text.setToolTip("Keep trailing blanks to line up the fields after it")
        self.prompt_text.textEdited.connect(self._prompt_edited)
        self.prompt_text.editingFinished.connect(self._commit_edit)
        fl.addRow("Text:", self.prompt_text)
        return w

    def _build_field(self) -> QtWidgets.QWidget:
        Q = QtWidgets
        w = Q.QWidget()
        fl = Q.QFormLayout(w)
        self.f_param = Q.QComboBox()
        self.f_param.setEditable(True)
        self.f_param.setInsertPolicy(Q.QComboBox.NoInsert)
        self.f_param.setToolTip("The genie parameter the field edits")
        self.f_param.addItems(list(self.params))
        self.f_param.currentTextChanged.connect(self._field_edited)
        self.f_param.lineEdit().editingFinished.connect(self._commit_edit)
        self.f_param.activated.connect(lambda _k: self._commit_edit())
        fl.addRow("Parameter:", self.f_param)
        self.f_size = Q.QSpinBox()
        self.f_size.setRange(1, 4096)
        self.f_size.setToolTip("The most characters the value may have")
        self.f_size.valueChanged.connect(self._field_edited)
        self.f_size.editingFinished.connect(self._commit_edit)
        fl.addRow("Length:", self.f_size)
        self.f_width = Q.QSpinBox()
        self.f_width.setRange(MIN_FIELD, GF.MAX_COLUMN)
        self.f_width.setSuffix(" columns")
        self.f_width.setToolTip("How wide the field is on the form")
        self.f_width.valueChanged.connect(self._field_edited)
        self.f_width.editingFinished.connect(self._commit_edit)
        fl.addRow("Width:", self.f_width)
        self.f_list_form = Q.QLineEdit()
        self.f_list_form.setPlaceholderText("None")
        self.f_list_form.setToolTip(
            "A drop-down from a project table, for example Variable Tags, or a help list such as SCADAHELP"
        )
        self.f_list_field = Q.QLineEdit()
        self.f_list_field.setToolTip("The table's field the drop-down lists, for example NAME")
        self.f_list_filter = Q.QLineEdit()
        self.f_list_filter.setToolTip("Only the rows that match, for example TYPE=DIGITAL")
        for e in (self.f_list_form, self.f_list_field, self.f_list_filter):
            e.textEdited.connect(self._field_edited)
            e.editingFinished.connect(self._commit_edit)
        fl.addRow("List form:", self.f_list_form)
        fl.addRow("List field:", self.f_list_field)
        fl.addRow("List filter:", self.f_list_filter)
        return w

    def _build_rule(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(w)
        self.r_width = QtWidgets.QSpinBox()
        self.r_width.setRange(1, GF.MAX_COLUMN)
        self.r_width.setSuffix(" columns")
        self.r_width.valueChanged.connect(self._rule_edited)
        self.r_width.editingFinished.connect(self._commit_edit)
        fl.addRow("Length:", self.r_width)
        return w

    # -- the state ----------------------------------------------------------------------
    def _state(self) -> tuple:
        return copy.deepcopy((self.title.text(), self.items, self.extra))

    def _set_state(self, st: tuple) -> None:
        title, items, extra = copy.deepcopy(st)
        self._block = True
        self.title.setText(title)
        self._block = False
        self.items, self.extra = items, extra
        self.design.rebuild(self.items)
        self._changed(push=False)

    def _push(self) -> None:
        self._undo.append(self._before)
        self._redo.clear()
        self._before = self._state()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._state())
        st = self._undo.pop()
        self._set_state(st)
        self._before = self._state()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._state())
        st = self._redo.pop()
        self._set_state(st)
        self._before = self._state()

    def dirty(self) -> bool:
        return self._state() != self._saved_state

    def load(self) -> None:
        """The form as the .frm file holds it; the default form (a prompt and a field per
        parameter) when the file has none, or one that does not read."""
        text = ""
        if os.path.exists(self.path):
            with open(self.path, "rb") as fh:
                from ...format import textcodec

                text = textcodec.dec(fh.read())
        forms = [f for f in GF.parse_forms(text, self.path) if f.name.lower() == self.element.lower()]
        form = GF.lookup_form(forms, self.element)
        self._load_note = ""
        if form is None:
            bad = [f for f in forms if not f.valid]
            if bad:
                self._load_note = (
                    "The form in the file does not read (%s): this is a new one in its place." % bad[0].error
                )
            else:
                self._load_note = "The genie has no form yet: this is a first one."
            form = GF.default_form(self.element, list(self.params))
        items, extra = GF.draft_from_form(form)
        self._block = True
        self.title.setText(form.title)
        self._block = False
        self.items, self.extra = items, extra
        self._undo.clear()
        self._redo.clear()
        self.design.rebuild(self.items)
        self._before = self._state()
        # a form made here is not saved yet: the window says so until it is
        self._saved_state = None if self._load_note else self._before
        self._show_file_note()
        self._changed(push=False)

    def revert(self) -> None:
        from ..messages import confirm

        if self.dirty() and not confirm(
            self, "Revert", "Discard the changes made to the form since it was saved?", "Discard Changes"
        ):
            return
        self.load()

    def _show_file_note(self) -> None:
        # (a path breaks at its folders: a zero-width space after each backslash)
        s = self.path.replace("\\", "\\" + ZWSP)
        self.file_note.setToolTip(self.path)
        try:
            used = GF.find_form(self.project_dir, self.library, self.element)
        except Exception:  # noqa: BLE001  # swallow-ok: the note only
            used = None
        if used is not None and used.source and path_key(used.source) != path_key(self.path):
            s += (
                "\nThe Graphics Builder uses the form in %s for this project: it is found "
                "first." % used.source
            )
        if self._load_note:
            s += "\n" + self._load_note
        self.file_note.setText(s)

    def form(self) -> GF.GenieForm:
        """The form as it would be saved."""
        return GF.form_from_draft(self.element, self.title.text().strip(), self.items, self.extra)

    def problem_text(self) -> str:
        line = overlaps(self.items)
        if line is not None:
            return "Line %d: two items overlap." % line
        return GF.check_form(self.form())

    def _changed(self, push: bool = True) -> None:
        if push:
            self._push()
        why = self.problem_text()
        from ..style import theme

        if why:
            self.problem.setText("The Graphics Builder would not use this form: %s." % why)
            theme.set_note_role(self.problem, "warn")
        else:
            self.problem.setText("The form is ready to save." if self.dirty() else "Saved.")
            theme.set_note_role(self.problem, "text_dim")
        self.btn_save.setEnabled(not why)
        self.act_undo.setEnabled(bool(self._undo))
        self.act_redo.setEnabled(bool(self._redo))
        self._fill_params()
        self._preview_timer.start()

    def _push_pending(self) -> None:
        """Typed edits not yet an undo step become one (before a structural change)."""
        if self._state() != self._before:
            self._push()

    # -- the preview -----------------------------------------------------------------------
    def _update_preview(self) -> None:
        from .genie_dialogs import GenieParamsForm

        while self.preview_layout.count():
            w = self.preview_layout.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        form = self.form() if not self.problem_text() else None
        if form is None:
            note = QtWidgets.QLabel("The genie dialog shows its default form while this form has a problem.")
            note.setWordWrap(True)
            self.preview_layout.addWidget(note)
        values = OrderedDict(self.params)
        for f in form.fields if form is not None else ():
            values.setdefault(f.name, "")
        pf = GenieParamsForm(values, form=form, list_values=self._list_values)
        self.preview = pf
        self.preview_layout.addWidget(pf, 1)

    # -- the side panel ---------------------------------------------------------------------
    def _fill_params(self) -> None:
        on = {it.field.name.lower() for it in self.items if it.kind == "field" and it.field is not None}
        cur = self.param_list.currentItem()
        cur = cur.data(QtCore.Qt.UserRole) if cur is not None else None
        self.param_list.clear()
        for name in self.params:
            item = QtWidgets.QListWidgetItem(name + ("   (on the form)" if name.lower() in on else ""))
            item.setData(QtCore.Qt.UserRole, name)
            if name.lower() in on:
                item.setForeground(self.palette().color(QtGui.QPalette.Mid))
            self.param_list.addItem(item)
            if name == cur:
                self.param_list.setCurrentItem(item)

    def _show_selection(self) -> None:
        sel = self.design.selected_items()
        one = sel[0] if len(sel) == 1 else None
        self._block = True
        if one is None:
            self.props.setCurrentIndex(0)
        elif one.kind == "text":
            self.props.setCurrentIndex(1)
            self.prompt_text.setText(one.text)
        elif one.kind == "field":
            self.props.setCurrentIndex(2)
            f = one.field
            self.f_param.setEditText(f.name if f is not None else "")
            self.f_size.setValue(f.size if f is not None else 32)
            self.f_width.setValue(one.width)
            self.f_list_form.setText(f.list_form if f is not None else "")
            self.f_list_field.setText(f.list_field if f is not None else "")
            self.f_list_filter.setText(f.list_filter if f is not None else "")
        else:
            self.props.setCurrentIndex(3)
            self.r_width.setValue(one.width)
        self._block = False
        has = bool(sel)
        for a in (self.act_delete, self.act_insert, self.act_remove):
            a.setEnabled(has)

    def focus_properties(self) -> None:
        k = self.props.currentIndex()
        target = {1: self.prompt_text, 2: self.f_param, 3: self.r_width}.get(k)
        if target is not None:
            target.setFocus()
            if isinstance(target, QtWidgets.QLineEdit):
                target.selectAll()

    def _one(self, kind: str) -> Optional[DraftItem]:
        sel = self.design.selected_items()
        return sel[0] if len(sel) == 1 and sel[0].kind == kind else None

    def _prompt_edited(self, text: str) -> None:
        it = self._one("text")
        if self._block or it is None:
            return
        it.text = text.replace("{", "(").replace("}", ")")
        self.design.refresh()
        self._changed(push=False)

    def _field_edited(self, *_a) -> None:
        it = self._one("field")
        if self._block or it is None or it.field is None:
            return
        f = it.field
        f.name = self.f_param.currentText().strip()
        f.size = self.f_size.value()
        f.list_form = self.f_list_form.text().strip()
        f.list_field = self.f_list_field.text().strip()
        f.list_filter = self.f_list_filter.text().strip()
        it.width = self.f_width.value()
        self.design.refresh()
        self._changed(push=False)  # (an undo step when the edit is done)

    def _rule_edited(self, value: int) -> None:
        it = self._one("line")
        if self._block or it is None:
            return
        it.width = value
        self.design.refresh()
        self._changed(push=False)

    def _commit_edit(self) -> None:
        if not self._block and self._state() != self._before:
            self._push()
            self._changed(push=False)

    # -- editing ----------------------------------------------------------------------------
    def _free_line(self) -> int:
        return max([0] + [it.line for it in self.items]) + 1

    def _add(self, items: Sequence[DraftItem]) -> None:
        self._push_pending()
        self.items.extend(items)
        self.design.rebuild(self.items, keep=items[-1:])
        self._changed()

    def add_prompt(self) -> None:
        self._add([DraftItem("text", self._free_line(), 1, 6, text="Prompt")])
        self.focus_properties()

    def add_field(self, name: str) -> None:
        self._add([DraftItem("field", self._free_line(), 1, 20, field=FormField(0, name, 32))])
        self.focus_properties()

    def add_rule(self) -> None:
        width = max([40] + [it.col + _width(it) for it in self.items])
        self._add([DraftItem("line", self._free_line(), 0, width)])

    def add_parameter(self, name: Optional[str]) -> None:
        """A parameter's prompt and field on a new line, the field lined up with the other
        fields."""
        if not name:
            return
        line = self._free_line()
        cols = [it.col for it in self.items if it.kind == "field"]
        col = max(cols) if cols else len(name) + 3
        col = max(col, len(name) + 3)
        self._add(
            [
                DraftItem("text", line, 1, len(name), text=name),
                DraftItem("field", line, col, 20, field=FormField(0, name, 32)),
            ]
        )

    def _add_chosen_parameter(self) -> None:
        item = self.param_list.currentItem()
        if item is not None:
            self.add_parameter(item.data(QtCore.Qt.UserRole))

    def delete_selected(self) -> None:
        sel = self.design.selected_items()
        if not sel:
            return
        self._push_pending()
        self.items = [it for it in self.items if not any(it is s for s in sel)]
        self.design.rebuild(self.items)
        self._changed()

    def move_items(self, items: Sequence[DraftItem], dc: int, dl: int) -> None:
        self._push_pending()
        old = [(it.line, it.col) for it in items]
        for it in items:
            it.line = max(1, it.line + dl)
            if it.kind != "line":
                it.col = max(1, it.col + dc)
        if overlaps(self.items) is not None:
            for it, (ln, c) in zip(items, old):
                it.line, it.col = ln, c
            self._status("That place is taken")
            return
        self.design.rebuild(self.items, keep=items)
        self._changed()

    def commit_drag(self) -> None:
        """The dragged boxes' places into the draft (refused where they would overlap)."""
        self._push_pending()
        moved = []
        for b in self.design._boxes:
            it = b.item
            line = int(round(b.pos().y() / self.design.lh)) + 1
            col = int(round(b.pos().x() / self.design.cw)) + 1
            if it.kind == "line":
                col = 0
            if (line, col) != (it.line, it.col):
                moved.append((it, it.line, it.col))
                it.line, it.col = line, col
        if not moved:
            return
        if overlaps(self.items) is not None:
            for it, ln, c in moved:
                it.line, it.col = ln, c
            self._status("That place is taken")
        sel = self.design.selected_items()
        self.design.rebuild(self.items, keep=sel)
        if overlaps(self.items) is None and moved and self._state() != self._before:
            self._changed()

    def resize_item(self, item: DraftItem, cols: int) -> None:
        self._push_pending()
        old = item.width
        item.width = max(MIN_FIELD if item.kind == "field" else 1, cols)
        if overlaps(self.items) is not None:
            item.width = old
            self._status("That would overlap the next item")
        self.design.rebuild(self.items, keep=[item])
        if item.width != old:
            self._changed()

    def shift_lines(self, step: int) -> None:
        """Insert a line at the selection's (its line and the lines under it go down), or
        remove the empty line above it (they go up)."""
        sel = self.design.selected_items()
        if not sel:
            return
        at = min(it.line for it in sel)
        if step < 0 and (at <= 1 or any(it.line == at - 1 for it in self.items)):
            self._status("The line above the selection is not empty")
            return
        self._push_pending()
        for it in self.items:
            if step > 0 and it.line >= at:
                it.line += 1
            elif step < 0 and it.line >= at:
                it.line -= 1
        self.design.rebuild(self.items, keep=sel)
        self._changed()

    def _status(self, text: str) -> None:
        self.problem.setText(text + ".")

    # -- saving -----------------------------------------------------------------------------
    def save(self) -> bool:
        """The form into the .frm file (its other blocks and comments as they are)."""
        why = self.problem_text()
        if why:
            warn(self, "Genie Form", "The form was not saved: %s." % why)
            return False
        from ...format import textcodec
        from ...format.fileio import write_atomic

        text = ""
        if os.path.exists(self.path):
            with open(self.path, "rb") as fh:
                text = textcodec.dec(fh.read())
        blocks = [s for s in GF.form_spans(text) if s[0].lower() == self.element.lower()]
        from ..messages import confirm

        if len(blocks) > 1 and not confirm(
            self,
            "Genie Form",
            "%s has %d forms for %s. The Graphics Builder uses the last one it can read. "
            "Replace them all with this one?" % (os.path.basename(self.path), len(blocks), self.element),
            "Replace All Forms",
        ):
            return False
        new = GF.replace_form(text, self.form())
        try:
            data = textcodec.encode_field(new, "the genie form")
            write_atomic(self.path, data)
        except Exception as e:  # noqa: BLE001 -- said to the user
            warn(self, "Genie Form", "%s could not be saved: %s" % (self.path, e))
            return False
        try:
            from ...infra import logsetup

            logsetup.files_written(
                "saved the genie form of %s.%s" % (self.library, self.element), [self.path]
            )
        except Exception:  # noqa: BLE001  # swallow-ok: the log line only
            pass
        self._saved_state = self._state()
        self._load_note = ""
        self._show_file_note()
        self._changed(push=False)
        self.saved.emit(self.path)
        return True

    def reject(self) -> None:
        """Esc (and Cancel): as closing the window, with its save question."""
        if self.__dict__.get("_closing"):
            super().reject()  # (QDialog's closeEvent, below)
            return
        self._closing = True
        try:
            self.close()
        finally:
            self._closing = False

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self.dirty() and not self.problem_text():
            r = QtWidgets.QMessageBox.question(
                self,
                "Genie Form",
                "Save the changes to the form of %s?" % self.element,
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
            )
            if r == QtWidgets.QMessageBox.Cancel or (r == QtWidgets.QMessageBox.Save and not self.save()):
                ev.ignore()
                return
        super().closeEvent(ev)
