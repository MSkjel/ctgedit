"""The Alarm Indicator page."""

from __future__ import annotations


from PySide6 import QtCore, QtWidgets

from ...format import alarmind as AI
from ..panels.sa_pickers import EquipmentField, EquipmentPickerDialog  # noqa: F401 (re-exported)
from .colour_field import ColourPairField
from .fields import Field, Sub
from ..style.theme import set_note_role
from ...format import codes as _codes
from ...infra.logsetup import note_exception as _note_exc

ALARM = Sub("General", [], family=AI.KEYS)
SUBS = [ALARM]
FIELDS = [Field("", k) for k in AI.KEYS]

HELP_TOPIC = "properties#alarm-indicator"
#: the Position radios' place in CtDraw's 3 x 3 layout: (row, column)
_GRID = {0: (0, 0), 1: (1, 0), 2: (2, 0), 3: (0, 1), 4: (2, 1), 5: (0, 2), 6: (1, 2), 7: (2, 2)}
#: V3/V4 object classes: an alarm indicator ignores them (help 1172183) --
#: the library classes 0x1000.. except the Button (0x1001), a current object
_LEGACY_FROM = 0x1000
_CURRENT_TCS = frozenset({0x1001})
_TREND_TC = 0x1003


class AlarmIndicatorEditor:
    """The Alarm Indicator page; Apply = EditableDocument.set_alarm_indicator."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self._ro = False
        self._syncing = False
        self._initial = dict(AI.page_values({}))
        self.widget = self._build()
        try:
            from ..help import set_help

            set_help(self.widget, HELP_TOPIC)
        except RuntimeError:
            pass
        self.reload()

    # -- layout ------------------------------------------------------------------------
    def _build(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.has = QtWidgets.QCheckBox("Has alarm indicator")
        self.has.setToolTip(
            "Show a border and a flag around the group while its equipment has an active alarm"
        )
        lay.addWidget(self.has)
        src = QtWidgets.QGroupBox("Highest priority alarm from")
        g = QtWidgets.QGridLayout(src)
        pdir = getattr(self.sheet.expression_context, "project_dir", None) or ""
        self.equipment = EquipmentField("", pdir, tip="Select the equipment from the list")
        self.expr = self.equipment.lineEdit()
        self.expr.setMaxLength(AI.EXPR_LIMIT)
        self.expr.setPlaceholderText("Equipment name (cluster.equipment for another cluster)")
        self.expr.setToolTip("The equipment whose highest priority alarm the indicator shows")
        self.browse = self.equipment.button
        self.scope = QtWidgets.QComboBox()
        for label in AI.EQHIER:
            self.scope.addItem(label)
        self.scope.setToolTip("Count the alarms of the equipment, of the equipment below it, or both")
        self.include = QtWidgets.QCheckBox("Include equipment references in count")
        lab = QtWidgets.QLabel("Equipment:")
        lab.setBuddy(self.expr)
        g.addWidget(lab, 0, 0)
        g.addWidget(self.equipment, 0, 1, 1, 3)
        g.addWidget(self.scope, 1, 1)
        g.addWidget(self.include, 1, 2, 1, 2)
        g.setColumnStretch(2, 1)
        lay.addWidget(src)
        row = QtWidgets.QHBoxLayout()
        border = QtWidgets.QGroupBox("Border appearance")
        f = QtWidgets.QFormLayout(border)
        self.inside = QtWidgets.QCheckBox()
        self.inside.setToolTip("Draw the border inside the group's extent")
        self.width = QtWidgets.QSpinBox()
        self.width.setRange(*AI.WIDTH_RANGE)
        self.width.setSuffix(" px")
        self.padding = QtWidgets.QSpinBox()
        self.padding.setRange(*AI.PADDING_RANGE)
        self.padding.setSuffix(" px")
        self.padding.setToolTip("The space between the group and the border")
        self.colour = ColourPairField("Frame colour", flash=False)
        f.addRow("Inside:", self.inside)
        f.addRow("Width:", self.width)
        f.addRow("Padding:", self.padding)
        f.addRow("Frame colour:", self.colour)
        row.addWidget(border, 1)
        flag = QtWidgets.QGroupBox("Flag appearance")
        fl = QtWidgets.QVBoxLayout(flag)
        self.flag_on = QtWidgets.QCheckBox("Show flag")
        self.flag_on.setToolTip("Attach the alarm priority's flag to the outside of the border")
        fl.addWidget(self.flag_on)
        pos = QtWidgets.QGridLayout()
        self._pos_group = QtWidgets.QButtonGroup(w)
        self.positions = []
        for loc, label in enumerate(AI.FLAG_LOCATIONS):
            b = QtWidgets.QRadioButton()
            b.setToolTip(label)
            b.setAccessibleName(label)
            self._pos_group.addButton(b, loc)
            r, c = _GRID[loc]
            pos.addWidget(b, r, c, QtCore.Qt.AlignCenter)
            self.positions.append(b)
        frame = QtWidgets.QLabel()
        frame.setFrameShape(QtWidgets.QFrame.Box)
        frame.setMinimumSize(28, 16)
        pos.addWidget(frame, 1, 1)
        self.pos_label = QtWidgets.QLabel()
        fl.addWidget(QtWidgets.QLabel("Position:"))
        fl.addLayout(pos)
        fl.addWidget(self.pos_label)
        fl.addStretch(1)
        row.addWidget(flag, 1)
        lay.addLayout(row)
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        set_note_role(self.hint, "warn")
        self.hint.hide()
        lay.addWidget(self.hint)
        lay.addStretch(1)
        for sig in (self.has.toggled, self.include.toggled, self.inside.toggled, self.flag_on.toggled):
            sig.connect(lambda *_a: self._changed())
        self.expr.textChanged.connect(lambda *_a: self._changed())
        self.scope.currentIndexChanged.connect(lambda *_a: self._changed())
        self.width.valueChanged.connect(lambda *_a: self._changed())
        self.padding.valueChanged.connect(lambda *_a: self._changed())
        self.colour.changed.connect(self._changed)
        self._pos_group.idClicked.connect(lambda _k: self._changed())
        return w

    def _browse(self) -> None:
        self.equipment.browse()
        self.expr.setFocus()

    # -- values ------------------------------------------------------------------------
    def values(self) -> dict:
        """What the page shows now (format.alarmind.page_values keys)."""
        loc = self._pos_group.checkedId()
        return {
            "has": self.has.isChecked(),
            "expr": self.expr.text(),
            "eqhier": self.scope.currentIndex(),
            "include": self.include.isChecked(),
            "inside": self.inside.isChecked(),
            "width": self.width.value(),
            "padding": self.padding.value(),
            "colour": self.colour.pair()[0] & 0xFFFFFFFF,
            "flag_on": self.flag_on.isChecked(),
            "flag_loc": loc if loc >= 0 else self._initial.get("flag_loc", 0),
        }

    def _show(self, v: dict) -> None:
        self._syncing = True
        try:
            self.has.setChecked(bool(v["has"]))
            self.expr.setText(v["expr"])
            self.scope.setCurrentIndex(int(v["eqhier"]))
            self.include.setChecked(bool(v["include"]))
            self.inside.setChecked(bool(v["inside"]))
            self.width.setValue(max(AI.WIDTH_RANGE[0], min(AI.WIDTH_RANGE[1], int(v["width"]))))
            self.padding.setValue(max(AI.PADDING_RANGE[0], min(AI.PADDING_RANGE[1], int(v["padding"]))))
            self.colour.set_pair(int(v["colour"]), int(v["colour"]))
            self.flag_on.setChecked(bool(v["flag_on"]))
            b = self._pos_group.button(int(v["flag_loc"]))
            if b is not None:
                b.setChecked(True)
        finally:
            self._syncing = False
        self._enable()

    def _changed(self) -> None:
        if self._syncing:
            return
        self._enable()
        s = self.sheet
        if not s._syncing:
            s._refresh_marks(s._pending_props())

    def _enable(self) -> None:
        """CtDraw's page: everything but the box greyed while it is clear;
        Padding greyed when Is Inside; the positions greyed without a flag."""
        on = self.has.isChecked() and not self._ro
        self.has.setEnabled(not self._ro)
        for w in (self.expr, self.browse, self.scope, self.include, self.inside, self.width, self.flag_on):
            w.setEnabled(on)
        self.colour.set_read_only(not on)
        self.padding.setEnabled(on and not self.inside.isChecked())
        for b in self.positions:
            b.setEnabled(on and self.flag_on.isChecked())
        loc = self._pos_group.checkedId()
        self.pos_label.setText(AI.FLAG_LOCATIONS[loc] if loc >= 0 else "")
        self._update_hint()

    def _update_hint(self) -> None:
        """Help 1172183: an indicator ignores V3/V4 objects and is not
        recommended on a group of trends."""
        s = self.sheet
        msg = ""
        if self.has.isChecked():
            try:
                objs = s._doc.parsed.page.objects
                kids = [
                    j
                    for j in s._doc.subtree(s._index)
                    if j != s._index
                    and objs[j].type_code not in (_codes.TC.TOUCH, _codes.TC.KEYBOARD, 0xFFFF)
                ]
                tcs = [objs[j].type_code for j in kids]
            except Exception:  # noqa: BLE001  # swallow-ok: no such object, no hint
                tcs = []
            if (
                tcs
                and all(tc >= _LEGACY_FROM and tc not in _CURRENT_TCS for tc in tcs)
                and not all(tc == _TREND_TC for tc in tcs)
            ):
                msg = (
                    "This group holds only version 3.xx/4.xx objects: an alarm "
                    "indicator is not shown around those at runtime."
                )
            elif tcs and any(tc == _TREND_TC for tc in tcs):
                msg = "An alarm indicator is not recommended on a group of trends."
        self.hint.setText(msg)
        self.hint.setVisible(bool(msg))

    def _genie_instance(self) -> bool:
        """The object is a placed genie or inside one (genie syntax is not
        allowed in its properties)."""
        s = self.sheet
        from ...editing import ElementDocument

        if isinstance(s._doc, ElementDocument):
            return False
        try:
            objs = s._doc.parsed.page.objects
            chain = [s._index] + list(s._doc.page.ancestors(s._index))
            return any(objs[a].subtype_size & 0x200 for a in chain)
        except Exception:  # noqa: BLE001  # swallow-ok: no such object, not a genie
            return False

    # -- the sheet's editor contract ---------------------------------------------------
    def pending_props(self, props: dict) -> None:
        v = self.values()
        if v == self._initial:
            return
        for k, val in AI.apply_edits(v):
            if val is None:
                props.pop(k, None)
            else:
                props[k] = val

    def problem(self):
        v = self.values()
        if v == self._initial:
            return None
        if "%" in v["expr"] and self._genie_instance():
            return (
                "The Equipment cannot contain '%' here: genie syntax is not supported in "
                "the properties of a placed genie. Enter the equipment name, or set the "
                "indicator in the genie definition.",
                self.expr,
            )
        from ...editing import UnencodableTextError, guard_text

        try:
            guard_text(v["expr"])
        except UnencodableTextError as exc:
            return "%s. Replace those characters and apply again." % exc, self.expr
        return None

    def apply(self) -> bool:
        v = self.values()
        if v == self._initial:
            return False
        s = self.sheet
        try:
            return bool(s._doc.set_alarm_indicator(s._index, v))
        except Exception as exc:  # noqa: BLE001 -- reported in the sheet
            _note_exc("objprops.alarm_indicator.apply")
            s._fail("The alarm indicator could not be written: %s" % exc, self.widget)
            return False

    def reload(self) -> None:
        s = self.sheet
        try:
            stored = dict(s._doc.alarm_indicator(s._index))
        except Exception:  # noqa: BLE001
            _note_exc("objprops.alarm_indicator.reload")
            stored = dict(AI.page_values({}))
        self._initial = stored
        self._show(stored)
        # the baseline is what the page SHOWS (a stored width beyond 1..20 is
        # shown clamped: not a change until the user edits the page)
        self._initial = self.values()
        why = s.key_blocked("AI_ON")
        if why and not self._ro:
            self.set_read_only(True)

    def set_read_only(self, ro: bool) -> None:
        self._ro = bool(ro)
        self._enable()


def build_alarm_indicator(sheet, page):
    ed = AlarmIndicatorEditor(sheet)
    sheet.add_editor(ed)
    sheet.alarm_editor = ed
    return ed.widget


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"alarm_indicator": build_alarm_indicator}
