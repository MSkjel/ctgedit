"""Trend > Appearance > General."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from .colour_field import ColourPairField, qcolor
from .expression import ExpressionField, wizard_button
from ..style.theme import color, set_note_role
from ...infra.logsetup import note_exception as _note_exc

HELP_TOPIC = "properties#trend"
PEN_COUNT = 8
#: the SPC suffixes a pen may add to its trend tag (help 1172142)
SPC_SUFFIXES = (
    ".XUCL",
    ".XLCL",
    ".XCL",
    ".X",
    ".RUCL",
    ".RLCL",
    ".RCL",
    ".R",
    ".SUCL",
    ".SLCL",
    ".SCL",
    ".S",
)


def _trend_tag_advice(text, field_kind, context):
    """A pen that names no trend tag of the include chain (SPC suffixes and
    genie substitutions allowed) -- non-blocking, like every sheet advice."""
    from . import validators as V

    if field_kind != "trend" or context is None or "%" in text or "?" in text:
        return []
    try:
        tags = {t.name.lower() for t in context.trend_tags()}
    except Exception:  # noqa: BLE001
        _note_exc("objprops.trend._trend_tag_advice")
        return []
    base = text.strip()
    for suf in SPC_SUFFIXES:
        if base.upper().endswith(suf):
            base = base[: -len(suf)]
            break
    if not tags or not base or base.lower() in tags:
        return []
    return [V.Problem(V.WARNING, "'%s' is not a known trend tag." % base)]


def _register_advice():
    from . import validators as V

    V.register(_trend_tag_advice)


_register_advice()


def _swatch_icon(pair) -> QtGui.QIcon:
    pm = QtGui.QPixmap(28, 14)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    try:
        on, off = pair if pair is not None else (0xFF000000, 0xFF000000)
        for k, c in enumerate((on, off)):
            q = qcolor(c)
            r = QtCore.QRect(1 + 13 * k, 1, 12, 12)
            if q is None:
                p.setPen(QtGui.QPen(color("text_dim"), 1))
                p.drawRect(r.adjusted(0, 0, -1, -1))
                p.drawLine(r.bottomLeft(), r.topRight())
            else:
                p.fillRect(r, q)
                # a frame, so a white / pale pen shows on the white list
                p.setPen(QtGui.QPen(color("text_dim"), 1))
                p.drawRect(r.adjusted(0, 0, -1, -1))
            if on == off:
                break
    finally:
        p.end()
    return QtGui.QIcon(pm)


class TrendEditor:
    """The Trend page's widgets; Apply = EditableDocument.set_trend_properties."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._syncing = False
        self._initial: dict = {}
        self._pens: List[Tuple[str, Optional[tuple]]] = []
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except RuntimeError:
            pass
        self.reload()

    # -- widgets ----------------------------------------------------------------------
    def _build(self) -> QtWidgets.QWidget:
        s = self.sheet
        ctx = s.expression_context
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        form = QtWidgets.QFormLayout()
        self.cluster = QtWidgets.QComboBox()
        self.cluster.setEditable(True)
        self.cluster.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cluster.lineEdit().setMaxLength(16)
        self.cluster.lineEdit().setPlaceholderText("Connected cluster")
        # wide enough for a 16-character name and the placeholder (a short
        # field: it keeps this size in a larger window)
        self.cluster.setMinimumContentsLength(18)
        self.cluster.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cluster.setToolTip(
            "The cluster that runs the pens' trends. Leave it blank "
            "when the client connects to one cluster only"
        )
        self.cluster.addItem("")
        try:
            from ...io.project import clusters

            for row in clusters(ctx.project_dir) if ctx.project_dir else ():
                self.cluster.addItem(row.get("NAME", ""))
        except Exception:  # noqa: BLE001 -- the list is an aid only
            _note_exc("objprops.trend._build/clusters")
        self.cluster.currentTextChanged.connect(lambda _t: self._changed())
        form.addRow("Cluster name:", self.cluster)
        lay.addLayout(form)

        box = QtWidgets.QGroupBox("Pens")
        bl = QtWidgets.QVBoxLayout(box)
        self.table = QtWidgets.QTableWidget(PEN_COUNT, 3)
        self.table.setHorizontalHeaderLabels(["Pen", "Trend tag", "Colour"])
        self.table.verticalHeader().setVisible(False)
        from ..tables import make_resizable

        make_resizable(self.table, "ObjectPropertiesDialog/trend_pens", stretch=1)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # all 8 pens in view (the theme's header padding would make rows ~30 px)
        vh = self.table.verticalHeader()
        vh.setMinimumSectionSize(18)
        vh.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        vh.setDefaultSectionSize(22)
        self.table.setMinimumHeight(PEN_COUNT * 22 + 30)
        for r in range(PEN_COUNT):
            it = QtWidgets.QTableWidgetItem(str(r + 1))
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(""))
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(""))
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._select(r))
        self.table.cellDoubleClicked.connect(lambda r, _c: self.edit_row(r))
        bl.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        self.lbl_pen = QtWidgets.QLabel("Pen 1:")
        row.addWidget(self.lbl_pen)
        self.pen_tag = ExpressionField(ctx, wizard=("trend",), field_kind="trend")
        self.pen_tag.setMaxLength(79)
        self.pen_tag.setPlaceholderText("Trend tag (SPC: tag.XUCL, tag.X)")
        try:
            from ...io.project import trend_tags

            names = [t.name for t in trend_tags(ctx.project_dir)] if ctx.project_dir else []
            self.pen_tag.set_names(names)
        except Exception:  # noqa: BLE001
            _note_exc("objprops.trend._build/trend_tags")
        self.pen_tag.textChanged.connect(self._tag_typed)
        row.addWidget(self.pen_tag, 1)
        self.btn_edit = QtWidgets.QPushButton("&Edit")
        self.btn_edit.setToolTip("Edit the selected pen's trend tag. You can also double-click it")
        self.btn_edit.clicked.connect(lambda: self.edit_row(self.table.currentRow()))
        row.addWidget(self.btn_edit)
        bl.addLayout(row)
        crow = QtWidgets.QHBoxLayout()
        crow.addWidget(QtWidgets.QLabel("Pen colour:"))
        self.pen_colour = ColourPairField("Pen colour", transparent=True)
        self.pen_colour.changed.connect(self._colour_picked)
        crow.addWidget(self.pen_colour, 1)
        bl.addLayout(crow)
        lay.addWidget(box, 1)

        low = QtWidgets.QHBoxLayout()
        sbox = QtWidgets.QGroupBox("Samples")
        sf = QtWidgets.QFormLayout(sbox)
        from ...format import legacy

        self.samples = QtWidgets.QSpinBox()
        self.samples.setRange(1, legacy.MAX_SAMPLES)
        self.samples.setToolTip("How many samples the trend shows without scrolling")
        self.samples.valueChanged.connect(lambda _v: self._changed())
        sf.addRow("Number of samples:", self.samples)
        self.spacing = QtWidgets.QSpinBox()
        self.spacing.setRange(1, legacy.MAX_SPACING)
        self.spacing.setToolTip("The width of each sample in pixels")
        self.spacing.valueChanged.connect(lambda _v: self._changed())
        sf.addRow("Pixels per sample:", self.spacing)
        self.lbl_width = QtWidgets.QLabel()
        set_note_role(self.lbl_width, "text_dim")
        sf.addRow("", self.lbl_width)
        low.addWidget(sbox)
        cbox = QtWidgets.QGroupBox("Command")
        cf = QtWidgets.QVBoxLayout(cbox)
        crow2 = QtWidgets.QHBoxLayout()
        self.command = QtWidgets.QLineEdit()
        self.command.setMaxLength(64)
        self.command.setToolTip("A Cicode command stored with the trend (at most 64 characters)")
        self.command.textChanged.connect(lambda _t: self._changed())
        crow2.addWidget(self.command, 1)
        crow2.addWidget(wizard_button(self.command, ctx))
        cf.addLayout(crow2)
        self.periodic = QtWidgets.QCheckBox("Display all trend types as periodic")
        self.periodic.setToolTip("Show event and periodic pens the same way, so both can be on one graph")
        self.periodic.toggled.connect(lambda _v: self._changed())
        cf.addWidget(self.periodic)
        low.addWidget(cbox, 1)
        lay.addLayout(low)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        self.note.hide()
        lay.addWidget(self.note)
        return w

    # -- rows --------------------------------------------------------------------------
    def _show_row(self, r: int) -> None:
        tag, colour = self._pens[r]
        self.table.item(r, 1).setText(tag)
        it = self.table.item(r, 2)
        it.setIcon(_swatch_icon(colour))
        it.setText(
            ""
            if colour is None
            else ("#%06X" % (colour[1] & 0xFFFFFF) if not (colour[1] >> 24) & 0xFF else "None")
        )

    def _select(self, r: int) -> None:
        if not 0 <= r < PEN_COUNT or not self._pens:
            return
        self._syncing = True
        try:
            self.lbl_pen.setText("Pen %d:" % (r + 1))
            self.pen_tag.setText(self._pens[r][0])
            colour = self._pens[r][1]
            self.pen_colour.set_pair(*(colour or (0xFF000000, 0xFF000000)))
        finally:
            self._syncing = False

    def edit_row(self, r: int) -> None:
        """Edit / double-click: the selected pen's trend tag takes the focus."""
        if self._ro or not 0 <= r < PEN_COUNT:
            return
        self.table.setCurrentCell(r, 1)
        self.pen_tag.setFocus()
        self.pen_tag.selectAll()

    def set_pen(self, r: int, tag: Optional[str] = None, colour=None) -> None:
        """Set a pen row as the user does (tests / scripting)."""
        old_tag, old_colour = self._pens[r]
        self._pens[r] = (old_tag if tag is None else tag, old_colour if colour is None else tuple(colour))
        self._show_row(r)
        if self.table.currentRow() == r:
            self._select(r)
        self._changed()

    def _tag_typed(self, text: str) -> None:
        if self._syncing:
            return
        r = self.table.currentRow()
        if 0 <= r < PEN_COUNT and self._pens[r][0] != text:
            self._pens[r] = (text, self._pens[r][1])
            self._show_row(r)
            self._changed()

    def _colour_picked(self) -> None:
        if self._syncing:
            return
        r = self.table.currentRow()
        if 0 <= r < PEN_COUNT:
            self._pens[r] = (self._pens[r][0], self.pen_colour.pair())
            self._show_row(r)
            self._changed()

    def _changed(self) -> None:
        sp, n = self.spacing.value(), self.samples.value()
        self.lbl_width.setText("Width: %d pixels" % (sp * (n - 1) + 1))

    # -- values --------------------------------------------------------------------------
    def values(self) -> dict:
        return {
            "pens": list(self._pens),
            "cluster": self.cluster.currentText().strip(),
            "periodic": self.periodic.isChecked(),
            "command": self.command.text(),
            "samples": self.samples.value(),
            "spacing": self.spacing.value(),
        }

    def _dirty(self) -> bool:
        return self.values() != self._initial

    # -- the sheet's editor contract --------------------------------------------------------
    def problem(self):
        if not self._dirty():
            return None
        for r, (tag, _c) in enumerate(self._pens):
            if len(tag) > 79:
                self.table.setCurrentCell(r, 1)
                return (
                    "Pen %d's trend tag is %d characters long. The limit is 79." % (r + 1, len(tag)),
                    self.pen_tag,
                )
        return None

    def apply(self) -> bool:
        if self._ro or not self._dirty():
            return False
        v = self.values()
        cur = self._initial
        s = self.sheet
        kw = {"pens": v["pens"], "periodic": v["periodic"]}
        if v["cluster"].lower() != (cur.get("cluster") or "").lower():
            kw["cluster"] = v["cluster"]
        if v["command"] != cur.get("command"):
            kw["command"] = v["command"]
        if (v["samples"], v["spacing"]) != (cur.get("samples"), cur.get("spacing")):
            kw["samples"], kw["spacing"] = v["samples"], v["spacing"]
        try:
            return bool(s._doc.set_trend_properties(s._index, **kw))
        except ValueError as exc:
            s._fail(str(exc), self.table)
            return False

    def reload(self) -> None:
        s = self.sheet
        try:
            info = s._doc.trend_properties(s._index)
        except Exception:  # noqa: BLE001
            _note_exc("objprops.trend.reload")
            info = {
                "pens": [("", None)] * PEN_COUNT,
                "cluster": "",
                "periodic": False,
                "command": "",
                "samples": 1,
                "spacing": 1,
                "free": False,
            }
        self._pens = [(t, tuple(c) if c is not None else None) for t, c in info["pens"]]
        self._syncing = True
        try:
            for r in range(PEN_COUNT):
                self._show_row(r)
            self.cluster.setCurrentText(info["cluster"])
            self.periodic.setChecked(bool(info["periodic"]))
            self.command.setText(info["command"])
            self.samples.setValue(int(info["samples"]))
            self.spacing.setValue(int(info["spacing"]))
        finally:
            self._syncing = False
        free = bool(info.get("free"))
        for wdg in (self.samples, self.spacing, self.pen_colour):
            wdg.setEnabled(free and not self._ro)
        if not free:
            self.note.setText(
                "This trend comes from a library. Its pen colours and size are set in the library element."
            )
            self.note.show()
        if self.table.currentRow() < 0:
            self.table.setCurrentCell(0, 1)
        self._select(max(0, self.table.currentRow()))
        self._changed()
        self._initial = self.values()

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        for wdg in (
            self.cluster,
            self.pen_tag,
            self.btn_edit,
            self.samples,
            self.spacing,
            self.command,
            self.periodic,
        ):
            wdg.setEnabled(not ro)
        self.pen_colour.set_read_only(ro)


def build_general(sheet, page):
    ed = TrendEditor(sheet)
    sheet.add_editor(ed)
    sheet.trend_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"general": build_general}
SUBS: list = []
