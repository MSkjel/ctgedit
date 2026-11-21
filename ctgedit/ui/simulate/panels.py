"""The Simulate Inputs and Outputs panels."""

from __future__ import annotations
from ..style.theme import sp as _sp

import bisect
import re
import collections
import math
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import tables
from ..style import theme
from .sources import (
    CATEGORIES,
    CATEGORY_LABELS,
    QUALITIES,
    SEVERITIES,
    LogEntry,
    TagInfo,
    category_of,
    severity_of,
    tag_matches,
)

#: rows the Inputs panel lists at most (type to narrow): the default of Settings > Simulate >
#: Inputs > Tags listed at most (options.tag_rows)
MAX_TAG_ROWS = 2000
#: log lines the Outputs panel keeps: the default of Log lines kept (options.log_lines, any
#: value of its span)
MAX_LOG_ROWS = 5000

_NAME_ROLE = QtCore.Qt.UserRole + 1
_NO_EQUIPMENT = "(no equipment)"
#: the group of the tags the page reads that the project does not define
UNDEFINED_GROUP = "Not defined in the project"
PAGE_VARS_GROUP = "Page variables"
PARAMS_GROUP = "Parameters"
#: the group of the tags the page does not name that its Cicode used while it ran
CICODE_GROUP = "Read by the page's Cicode"
#: whether a name row shows the lock icon
_LOCK_ROLE = QtCore.Qt.UserRole + 7
#: the panel's key of a page variable (a tag of the same name is another row)
_PV = "\x01pv:"


_PA = "\x01pa:"


def _key(t) -> str:
    if getattr(t, "param", False):
        return _PA + t.name
    return _PV + t.name if getattr(t, "page_var", False) else t.name


#: the setting of the 'Simulate undefined tags' check box (off by default)
SIMULATE_UNDEFINED_SETTING = "sim/simulate_undefined"


def simulate_undefined_setting() -> bool:
    try:
        from ..prefs.settings import app_settings

        return bool(app_settings().value(SIMULATE_UNDEFINED_SETTING, False, type=bool))
    except Exception:  # noqa: BLE001 -- a setting is a convenience only
        return False


def _set_simulate_undefined_setting(on: bool) -> None:
    try:
        from ..prefs.settings import app_settings

        app_settings().setValue(SIMULATE_UNDEFINED_SETTING, bool(on))
    except Exception:  # noqa: BLE001
        pass


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            # a REAL tag can hold INF or NaN in the simulator (Pow(10, 400), a scenario):
            # shown as Cicode converts it, never an exception in the window's tick
            return "NaN" if value != value else ("INF" if value > 0 else "-INF")
        return ("%.3f" % value).rstrip("0").rstrip(".") if value != int(value) else "%d" % value
    return str(value)


#: the tag properties a simulation can override (ctsim/setTagProperty), as the Properties
#: tab shows them: (FIELD, label as Plant SCADA's variable tag form names it, TagInfo field)
TAG_PROPERTIES = (
    ("ENG_ZERO", "Eng Zero Scale", "eng_zero"),
    ("ENG_FULL", "Eng Full Scale", "eng_full"),
    ("RAW_ZERO", "Raw Zero Scale", "raw_zero"),
    ("RAW_FULL", "Raw Full Scale", "raw_full"),
    ("ENG_UNITS", "Eng Units", "units"),
    ("FORMAT", "Format", "format"),
    ("COMMENT", "Comment", "comment"),
    ("DEADBAND", "Deadband", "deadband"),
    ("UNIT", "I/O Device", "io_device"),
    ("ADDR", "Address", "address"),
) + tuple(("CUSTOM%d" % k, "Custom %d" % k, "custom%d" % k) for k in range(1, 9))


def _prop_text(t: TagInfo, attr: str) -> str:
    """A tag property's text as the Properties tab shows it ('' = blank)."""
    if attr.startswith("custom") and attr[6:].isdigit():
        k = int(attr[6:]) - 1
        return t.custom[k] if 0 <= k < len(t.custom) else ""
    v = getattr(t, attr, "")
    if v is None:
        return ""
    if isinstance(v, float):
        return "%.15g" % v
    return str(v)


class InputsPanel(QtWidgets.QWidget):
    """The Inputs panel: the page's tags by equipment, with an editor for the selected one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Simulation inputs")
        self._src = None
        self._serial = None
        self._rows: Dict[str, tuple] = {}  # name -> (value item, quality item)
        self._tags: Dict[str, TagInfo] = {}
        self._names: tuple = ()
        # setting the editor from the source: its controls' signals write nothing. Each setter
        # puts back what it found (they nest: _show_selected calls _show_drive)
        self._busy = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self.search = theme.SearchField("Search tags", tip="Tag, equipment or comment")
        lay.addWidget(self.search)
        self.page_only = QtWidgets.QCheckBox("Only tags this page reads")
        self.page_only.setChecked(True)
        lay.addWidget(self.page_only)
        self.simulate_undefined = QtWidgets.QCheckBox("Simulate undefined tags")
        self.simulate_undefined.setToolTip(
            "Let you set tags the project does not define. They exist only in the "
            "simulation. Off: they read as not found, as in the runtime."
        )
        self.simulate_undefined.setChecked(simulate_undefined_setting())
        # set in Simulate Settings; the box stays as the switch the panel reads
        self.simulate_undefined.setVisible(False)
        lay.addWidget(self.simulate_undefined)
        self.model = QtGui.QStandardItemModel(0, 4, self)
        self.model.setHorizontalHeaderLabels(["Tag", "Value", "Quality", "Association"])
        self.tree = QtWidgets.QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.setAccessibleName("Tags")
        self.tree.setColumnHidden(3, True)
        # the columns always fit the panel (docked it is narrow): the tag takes what is left,
        # Quality is as wide as its longest word. The widths the
        # user sets are remembered and give way when the panel is narrower (fit_view).
        tables.make_resizable(
            self.tree,
            "SimulateWindow/inputs",
            stretch=0,
            caps={1: 0.22, 3: 0.3},
            fixed={2: "Uncertain"},
            fit_view=True,
        )
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # (narrow, as docked: Quality gives its room to the tag names; the editor below
        # shows the selected tag's quality anyway)
        self.tree.viewport().installEventFilter(self)
        # the list above, the selected tag's editor below: a splitter, so the docked window's
        # little room goes where it is needed
        top = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(_sp(1))
        # (at least its header and five tags: on a short screen the panel scrolls instead of
        # leaving a list of one row)
        self.tree.setMinimumHeight(self.tree.fontMetrics().lineSpacing() * 7 + 12)
        tl.addWidget(self.tree, 1)
        self.search.attach(self.tree, self)
        self.count = theme.note_label("")
        tl.addWidget(self.count)
        self.split = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(top)
        lay.addWidget(self.split, 1)
        self._build_editor(self.split)
        # the list takes the room, the editor its own height
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.search.searchChanged.connect(lambda _t: self.rebuild())
        self.page_only.toggled.connect(lambda _on: self.rebuild())
        self.simulate_undefined.toggled.connect(self._simulate_undefined_toggled)
        self.tree.selectionModel().currentChanged.connect(lambda *_a: self._show_selected())
        self.tree.selectionModel().selectionChanged.connect(lambda *_a: self._show_selected())
        self.tree.doubleClicked.connect(self._toggle_digital)

    def showEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().showEvent(ev)
        if not getattr(self, "_split_set", False):
            # (the first split: the editor at the height it needs, the list the rest; the
            # stretch factors act on a resize only)
            self._split_set = True
            total = sum(self.split.sizes())
            need = self.editor_tabs.minimumSizeHint().height()
            if total > need:
                self.split.setSizes([total - need, need])

    # -- the selected tag's control ------------------------------------------------
    #: below this viewport width the Quality column hides
    QUALITY_MIN_WIDTH = 400

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if obj is self.tree.viewport() and ev.type() == QtCore.QEvent.Resize:
            narrow = ev.size().width() < self.QUALITY_MIN_WIDTH
            if self.tree.isColumnHidden(2) != narrow:
                self.tree.setColumnHidden(2, narrow)
        return super().eventFilter(obj, ev)

    def _build_editor(self, lay) -> None:
        """The selected tag's control in two lines (the docked Simulate window has little
        room): its name, quality and Lock, then its value control. What the tag is (type,
        range, equipment, comment) is the name's tool tip; a line under it only says why the
        tag cannot be set (not defined, a parameter, a page variable)."""
        self.editor_tabs = QtWidgets.QTabWidget()
        self.editor_tabs.setDocumentMode(True)
        self.editor_tabs.setAccessibleName("Selected tag")
        box = QtWidgets.QFrame()
        box.setAccessibleName("Value")
        self.editor_box = box
        g = QtWidgets.QGridLayout(box)
        g.setContentsMargins(0, _sp(0), 0, 0)
        g.setHorizontalSpacing(_sp(1))
        g.setVerticalSpacing(_sp(0))
        self.tag_name = QtWidgets.QLabel("")
        f = self.tag_name.font()
        f.setBold(True)
        self.tag_name.setFont(f)
        self.tag_name.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        # (a long name is cut to the panel's width; the whole name is in the tool tip)
        self.tag_name.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        g.addWidget(self.tag_name, 0, 0)
        self.quality = QtWidgets.QComboBox()
        self.quality.addItems(list(QUALITIES))
        self.quality.setAccessibleName("Quality")
        self.quality.setToolTip("The tag's quality")
        g.addWidget(self.quality, 0, 1)
        self.lock = QtWidgets.QCheckBox("Lock")
        self.lock.setToolTip(
            "Keep this value: Cicode cannot change it (a simulator feature). You can still set it here."
        )
        g.addWidget(self.lock, 0, 2)
        self.stack = QtWidgets.QStackedWidget()
        # number: slider over the range + a spin box
        num = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(num)
        h.setContentsMargins(0, 0, 0, 0)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName("Value")
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setAccessibleName("Value")
        self.spin.setKeyboardTracking(False)
        h.addWidget(self.slider, 1)
        h.addWidget(self.spin)
        self.stack.addWidget(num)
        # digital
        self.toggle = QtWidgets.QCheckBox("On")
        self.stack.addWidget(self.toggle)
        # string
        self.text = QtWidgets.QLineEdit()
        self.text.setAccessibleName("Value")
        self.text.setPlaceholderText("Type a value and press Enter")
        self.stack.addWidget(self.text)
        self.stack.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        g.addWidget(self.stack, 1, 0, 1, 3)
        # why the tag cannot be set, or what setting it does (shown only then)
        self.tag_info = theme.note_label("")
        self.tag_info.setWordWrap(True)
        self.tag_info.setVisible(False)
        g.addWidget(self.tag_info, 2, 0, 1, 3)
        g.addWidget(self._build_drive(), 3, 0, 1, 3)
        g.setColumnStretch(0, 1)
        g.setRowStretch(4, 1)
        self.editor_tabs.addTab(box, "Value")
        self.editor_tabs.addTab(self._build_properties(), "Properties")
        lay.addWidget(self.editor_tabs)
        box.setEnabled(False)
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)
        self.toggle.toggled.connect(lambda on: self._write(1 if on else 0))
        self.text.returnPressed.connect(lambda: self._write(self.text.text()))
        self.quality.currentTextChanged.connect(lambda _q: self._write(self._current_value()))
        self.lock.toggled.connect(self._lock_toggled)

    # -- Drive: a generator on the tag (sim_tools, the engine's scenario generators) --------
    DRIVE_KINDS = (
        ("Off", ""),
        ("Ramp", "ramp"),
        ("Sine", "sine"),
        ("Random", "random"),
        ("Square", "square"),
    )

    def _build_drive(self) -> QtWidgets.QWidget:
        """The Drive row: Off, or a signal the simulator writes to the tag on its clock: a
        ramp from Min to Max in Period seconds, a sine between them with that period, random
        values every Period seconds, a square wave (Duty = the part at Max)."""
        w = QtWidgets.QWidget()
        h = QtWidgets.QGridLayout(w)
        h.setContentsMargins(0, _sp(0), 0, 0)
        h.setHorizontalSpacing(_sp(1))
        h.setVerticalSpacing(_sp(0))
        lab = QtWidgets.QLabel("Drive:")
        h.addWidget(lab, 0, 0)
        self.drive = QtWidgets.QComboBox()
        self.drive.setAccessibleName("Drive")
        self.drive.setToolTip(
            "Let the simulator write a signal to the tag on its clock (it "
            "follows Run, Pause and Step). Off: the value stays as set"
        )
        for label, kind in self.DRIVE_KINDS:
            self.drive.addItem(label, kind)
        lab.setBuddy(self.drive)
        h.addWidget(self.drive, 0, 1, 1, 3)

        def spin(name, tip, lo=-1e12, hi=1e12, dec=3):
            b = QtWidgets.QDoubleSpinBox()
            b.setRange(lo, hi)
            b.setDecimals(dec)
            b.setAccessibleName(name)
            b.setToolTip(tip)
            b.setKeyboardTracking(False)
            b.setMinimumWidth(60)
            return b

        self.drive_min = spin("Min", "The lowest value")
        self.drive_max = spin("Max", "The highest value")
        self.drive_period = spin(
            "Period",
            "Seconds: the ramp's length, the sine's or square wave's period, the time between random values",
            0.05,
            86400,
            2,
        )
        self.drive_duty = spin("Duty", "The part of each period at Max (0 to 1)", 0.0, 1.0, 2)
        self._drive_params = []
        for k, (b, text) in enumerate(
            (
                (self.drive_min, "Min"),
                (self.drive_max, "Max"),
                (self.drive_period, "Period"),
                (self.drive_duty, "Duty"),
            )
        ):
            lb = QtWidgets.QLabel(text)
            lb.setBuddy(b)
            h.addWidget(lb, 1 + k // 2, (k % 2) * 2)
            h.addWidget(b, 1 + k // 2, (k % 2) * 2 + 1)
            self._drive_params += [lb, b]
            b.valueChanged.connect(lambda _v: self._drive_changed())
        h.setColumnStretch(1, 1)
        h.setColumnStretch(3, 1)
        self.drive.currentIndexChanged.connect(lambda _k: self._drive_changed(kind_changed=True))
        self.drive_box = w
        w.setVisible(False)
        return w

    def _tools(self):
        from .sim_tools import tools_of

        return tools_of(self._src) if self._src is not None else None

    def _show_drive(self, t: Optional[TagInfo]) -> None:
        tools = self._tools()
        can = (
            t is not None
            and tools is not None
            and tools.available()
            and not t.param
            and not t.page_var
            and not t.is_string()
            and (t.defined or t.simulated)
        )
        self.drive_box.setVisible(bool(can))
        if not can:
            return
        g = tools.generator_of(t.name)
        was_busy, self._busy = self._busy, True
        try:
            k = self.drive.findData(g["kind"] if g else "")
            self.drive.setCurrentIndex(max(0, k))
            lo, hi = (0.0, 1.0) if t.is_digital() else self._range(t)
            spec = g or {"min": lo, "max": hi, "period": 10.0 if not t.is_digital() else 2.0, "duty": 0.5}
            for b, key in (
                (self.drive_min, "min"),
                (self.drive_max, "max"),
                (self.drive_period, "period"),
                (self.drive_duty, "duty"),
            ):
                if not b.hasFocus():
                    b.setValue(float(spec.get(key, b.value())))
            self.drive_duty.setEnabled(self.drive.currentData() == "square")
            for x in self._drive_params:
                x.setVisible(bool(self.drive.currentData()))
        finally:
            self._busy = was_busy

    def _drive_changed(self, kind_changed: bool = False) -> None:
        if self._busy:
            return
        t = self.selected_tag()
        tools = self._tools()
        if t is None or tools is None:
            return
        kind = self.drive.currentData() or ""
        self.drive_duty.setEnabled(kind == "square")
        for x in self._drive_params:
            x.setVisible(bool(kind))
        if kind_changed and kind == "square" and t.is_digital():
            was_busy, self._busy = self._busy, True
            try:
                self.drive_min.setValue(0.0)
                self.drive_max.setValue(1.0)
            finally:
                self._busy = was_busy
        spec = (
            None
            if not kind
            else {
                "kind": kind,
                "min": self.drive_min.value(),
                "max": self.drive_max.value(),
                "period": self.drive_period.value(),
                "duty": self.drive_duty.value(),
            }
        )
        if spec is None and tools.generator_of(t.name) is None:
            return
        try:
            tools.set_generator(t.name, spec)
        except ValueError as e:
            self._set_info(str(e), shown=True)

    def _build_properties(self) -> QtWidgets.QWidget:
        """The Properties tab: each overridable definition field (TAG_PROPERTIES) as a text
        box, the overridden ones bold with the project's value in the tool tip and their own
        Reset; Reset All; a line that says the project is not changed."""
        outer = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(outer)
        ol.setContentsMargins(0, _sp(0), 0, 0)
        ol.setSpacing(_sp(0))
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(inner)
        form.setContentsMargins(0, 0, _sp(1), 0)
        form.setHorizontalSpacing(_sp(1))
        form.setVerticalSpacing(_sp(0))
        # (narrow, the docked panel: a label goes above its box, which gets the width)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.prop_rows: Dict[str, tuple] = {}  # FIELD -> (label, edit, reset)
        for r, (field_, label, _attr) in enumerate(TAG_PROPERTIES):
            lab = QtWidgets.QLabel(label)
            edit = QtWidgets.QLineEdit()
            edit.setAccessibleName(label)
            reset = QtWidgets.QToolButton()
            reset.setText("Reset")
            reset.setToolTip("Back to the project's %s" % label)
            reset.setAutoRaise(True)
            lab.setBuddy(edit)
            cell = QtWidgets.QWidget()
            ch = QtWidgets.QHBoxLayout(cell)
            ch.setContentsMargins(0, 0, 0, 0)
            ch.setSpacing(_sp(0))
            ch.addWidget(edit, 1)
            ch.addWidget(reset)
            form.addRow(lab, cell)
            edit.editingFinished.connect(lambda f=field_: self._prop_edited(f))
            reset.clicked.connect(lambda _c=False, f=field_: self._prop_reset(f))
            self.prop_rows[field_] = (lab, edit, reset)
        area.setWidget(inner)
        ol.addWidget(area, 1)
        bottom = QtWidgets.QHBoxLayout()
        self.prop_note = theme.note_label("")
        self.prop_note.setWordWrap(True)
        bottom.addWidget(self.prop_note, 1)
        self.prop_reset_all = QtWidgets.QPushButton("Reset All")
        self.prop_reset_all.setToolTip("Every property of this tag back to the project's")
        self.prop_reset_all.clicked.connect(lambda: self._prop_reset(None))
        bottom.addWidget(self.prop_reset_all)
        ol.addLayout(bottom)
        self.props_box = outer
        return outer

    def _props_settable(self, t: Optional[TagInfo]) -> str:
        """'' when tag ``t``'s properties can be overridden, else why not."""
        if t is None:
            return "Select a tag."
        if t.param or t.page_var:
            return "Parameters and page variables have no tag properties."
        if not t.defined and not t.simulated:
            return "The project does not define this tag."
        fn = getattr(self._src, "can_set_tag_properties", None)
        if fn is None:
            return "Only the Cicode simulator overrides tag properties."
        if not fn():
            return "The Cicode simulator is not connected."
        return ""

    def _show_properties(self, t: Optional[TagInfo]) -> None:
        why = self._props_settable(t)
        ok = not why
        over = dict(t.overrides) if t is not None else {}
        conf = dict(t.configured) if t is not None else {}
        for field_, label, attr in TAG_PROPERTIES:
            lab, edit, reset = self.prop_rows[field_]
            text = _prop_text(t, attr) if t is not None else ""
            if not edit.hasFocus() and edit.text() != text:
                edit.setText(text)
            edit.setEnabled(ok)
            on = field_ in over
            for w in (lab, edit):
                f = w.font()
                if f.bold() != on:
                    f.setBold(on)
                    w.setFont(f)
            reset.setEnabled(ok and on)
            tip = (
                ("Overridden in this simulation. The project's value: %s" % (conf.get(field_) or "(blank)"))
                if on
                else "%s as the project has it" % label
            )
            edit.setToolTip(tip)
        self.prop_reset_all.setEnabled(ok and bool(over))
        if why:
            self.prop_note.setText(why)
        elif over:
            self.prop_note.setText(
                "%d overridden (bold). Changes apply to this simulation "
                "only; the project is not changed." % len(over)
            )
        else:
            self.prop_note.setText(
                "Type a value and press Enter. Changes apply to this "
                "simulation only; the project is not changed."
            )

    def _prop_edited(self, field_: str) -> None:
        t = self.selected_tag()
        if t is None or self._props_settable(t):
            return
        attr = next(a for f, _l, a in TAG_PROPERTIES if f == field_)
        text = self.prop_rows[field_][1].text().strip()
        if text == _prop_text(t, attr):
            return  # (nothing changed: leaving the box)
        fn = getattr(self._src, "set_tag_property", None)
        if fn is not None:
            fn(t.name, field_, text)

    def _prop_reset(self, field_: Optional[str]) -> None:
        t = self.selected_tag()
        fn = getattr(self._src, "set_tag_property", None)
        if t is not None and fn is not None and not self._props_settable(t):
            fn(t.name, field_, None)

    def _range(self, t: TagInfo):
        lo = t.eng_zero if t.eng_zero is not None else 0.0
        hi = t.eng_full if t.eng_full is not None else 100.0
        if hi == lo:
            hi = lo + 1.0
        return float(lo), float(hi)

    def selected_tag(self) -> Optional[TagInfo]:
        idx = self.tree.currentIndex()
        # the current row without a selection (Qt gives one on focus) is no chosen tag
        if not idx.isValid() or not self.tree.selectionModel().isRowSelected(idx.row(), idx.parent()):
            return None
        name = self.model.itemFromIndex(idx.siblingAtColumn(0)).data(_NAME_ROLE)
        return self._tags.get(name) if name else None

    def select_tag(self, name: str) -> bool:
        """Select a row by its key (a tag's name; a page variable's ``_key``)."""
        row = self._rows.get(name)
        if row is None:
            # (a name in another spelling: Cicode names do not care about case)
            low = (name or "").lower()
            row = next((r for k, r in self._rows.items() if k.lower() == low), None)
        if row is None:
            return False
        rows = self._rows
        idx = self.model.indexFromItem(row[0]).siblingAtColumn(0)
        self.tree.setCurrentIndex(idx)
        if self._rows is not rows:
            # (the selection listed the tags again: the index is of rows that are gone)
            return False
        self.tree.scrollTo(idx)
        return True

    def _show_selected(self) -> None:
        t = self.selected_tag()
        self.editor_box.setEnabled(t is not None)
        was_busy, self._busy = self._busy, True
        try:
            can_lock = (
                t is not None
                and getattr(self._src, "set_locked", None) is not None
                and (t.defined or t.simulated or t.page_var)
                and not t.param
            )
            self.lock.setVisible(getattr(self._src, "set_locked", None) is not None)
            self.lock.setEnabled(bool(can_lock))
            self.lock.setChecked(bool(t is not None and t.locked))
            self._show_properties(t)
            self._show_drive(t)
            if t is None:
                self.tag_name.setText("")
                self._set_info("")
                return
            self.tag_name.setText(t.name)
            if t.param:
                self._set_info(
                    (
                        "Parameter from %s. %s.%s Set a value to change what ParameterGet returns from "
                        "now on. Empty restores the project's value. The project is not changed."
                    )
                    % (t.source or "?", t.comment, " Read by %s." % ", ".join(t.users) if t.users else ""),
                    shown=True,
                )
                self.stack.setEnabled(True)
                self.quality.setVisible(False)
                self.stack.setCurrentIndex(2)
                if not self.text.hasFocus():
                    self.text.setText("" if t.value is None else str(t.value))
                return
            if t.page_var:
                word = "Str" if t.is_string() else "Int"
                self._set_info(
                    "%s page variable of this window (PageSet%s / PageGet%s). Setting it does "
                    "what the page's code would." % ("Text" if t.is_string() else "Whole number", word, word)
                )
                self.stack.setEnabled(True)
                self.quality.setVisible(False)  # a page variable has none
                if t.is_string():
                    self.stack.setCurrentIndex(2)
                    if not self.text.hasFocus():
                        self.text.setText("" if t.value is None else str(t.value))
                else:
                    self.stack.setCurrentIndex(0)
                    self.spin.setDecimals(0)
                    self.spin.setRange(-2147483648, 2147483647)
                    self._set_number(t.value)
                return
            self.spin.setDecimals(3)
            self.quality.setVisible(True)
            bits = [t.type.title()]
            if not t.is_digital() and not t.is_string():
                lo, hi = self._range(t)
                bits.append("%s to %s%s" % (_fmt(lo), _fmt(hi), " " + t.units if t.units else ""))
            if t.equipment:
                bits.append("equipment %s" % t.equipment)
            if t.via:
                bits.append("from the association '%s'" % t.via)
            if t.comment:
                bits.append(t.comment)
            if t.cicode_used and t.used_by:
                bits.append("first used by %s" % t.used_by)
            text = ", ".join(bits)
            if not t.defined:
                if t.simulated:
                    text += ". Simulated: the project does not define it."
                elif self.simulate_undefined.isChecked():
                    text += ". Not defined in the project. Set a value to simulate it."
                else:
                    text += ". Not defined in the project. Turn on Simulate undefined tags to set it."
            # an undefined tag takes a value only while the simulator simulates it
            settable = t.defined or t.simulated or self.simulate_undefined.isChecked()
            self._set_info(text, shown=not t.defined)
            self.stack.setEnabled(settable)
            self.quality.setEnabled(settable)
            if t.is_digital():
                self.stack.setCurrentWidget(self.toggle)
                self.toggle.setChecked(bool(t.value))
            elif t.is_string():
                self.stack.setCurrentIndex(2)
                if not self.text.hasFocus():
                    self.text.setText("" if t.value is None else str(t.value))
            else:
                self.stack.setCurrentIndex(0)
                lo, hi = self._range(t)
                self.spin.setRange(min(lo, hi) - abs(hi - lo) * 10, max(lo, hi) + abs(hi - lo) * 10)
                self._set_number(t.value)
            self.quality.setCurrentText(t.quality if t.quality in QUALITIES else QUALITIES[0])
        finally:
            self._busy = was_busy

    def _set_info(self, text: str, shown: bool = False) -> None:
        """What the selected tag is: the name's tool tip; also the line under the control
        when ``shown`` (it says why the tag cannot be set, or what setting it does)."""
        self.tag_info.setText(text)
        self.tag_info.setVisible(bool(shown and text))
        name = self.tag_name.text()
        self.tag_name.setToolTip("%s\n%s" % (name, text) if text else name)

    def _set_number(self, value) -> None:
        t = self.selected_tag()
        if t is None:
            return
        try:
            v = float(value)
        except TypeError, ValueError:
            v = 0.0
        if not math.isfinite(v):
            return  # INF / NaN: the slider and the box keep their place
        lo, hi = self._range(t)
        if not self.slider.isSliderDown():
            self.slider.setValue(int(round(1000 * (v - lo) / (hi - lo))))
        if not self.spin.hasFocus():
            self.spin.setValue(v)

    def _slider_moved(self, pos: int) -> None:
        if self._busy:
            return
        t = self.selected_tag()
        if t is None:
            return
        lo, hi = self._range(t)
        v = round(lo + (hi - lo) * pos / 1000.0, 6)
        was_busy, self._busy = self._busy, True
        try:
            self.spin.setValue(v)
        finally:
            self._busy = was_busy
        self._write(v)

    def _spin_changed(self, v: float) -> None:
        if self._busy:
            return
        self._write(v)

    def _current_value(self):
        t = self.selected_tag()
        if t is None:
            return None
        if t.is_digital():
            return 1 if self.toggle.isChecked() else 0
        if t.is_string():
            return self.text.text()
        return self.spin.value()

    def _write(self, value) -> None:
        if self._busy:
            return
        t = self.selected_tag()
        if t is None or self._src is None:
            return
        if t.param:
            fn = getattr(self._src, "set_param", None)
            if fn is not None:
                fn(t.name, value)
        elif t.page_var:
            fn = getattr(self._src, "set_page_var", None)
            if fn is not None:
                fn(t.name, value, "string" if t.is_string() else "int")
        else:
            self._src.set_tag(t.name, value, self.quality.currentText())
        self.refresh(force=True)

    def _lock_toggled(self, on: bool) -> None:
        if self._busy:
            return
        t = self.selected_tag()
        fn = getattr(self._src, "set_locked", None)
        if t is None or fn is None:
            return
        if t.page_var:
            fn(
                t.name,
                on,
                page_var=True,
                type_="string" if t.is_string() else "int",
                value=self._current_value(),
            )
        else:
            fn(t.name, on)
        self.refresh(force=True)

    def _toggle_digital(self, idx) -> None:
        t = self.selected_tag()
        if t is not None and not t.defined and not t.simulated and not self.simulate_undefined.isChecked():
            return
        if t is not None and t.is_digital() and not t.page_var and self._src is not None:
            self._src.set_tag(t.name, 0 if t.value else 1, t.quality)
            self.refresh(force=True)

    # -- the list ---------------------------------------------------------------------
    def set_source(self, src) -> None:
        if src is self._src:
            return
        self._src = src
        self._serial = None
        fn = getattr(src, "set_simulate_undefined", None)
        if fn is not None:
            fn(self.simulate_undefined.isChecked())
        self.rebuild()

    def _simulate_undefined_toggled(self, on: bool) -> None:
        _set_simulate_undefined_setting(on)
        fn = getattr(self._src, "set_simulate_undefined", None)
        if fn is not None:
            fn(on, user=True)
        self._show_selected()

    def rebuild(self) -> None:
        """List the tags again (a new source, search or filter)."""
        keep = self.selected_tag()
        self.model.removeRows(0, self.model.rowCount())
        self._rows = {}
        self._tags = {}
        src = self._src
        if src is None:
            self.count.setText("")
            self._names = ()
            self._show_selected()
            return
        tags, server_total = self._query(src)
        self._names = tuple(_key(t) for t in tags)
        m = self.search.matcher()
        if not m.empty:
            tags = [t for t in tags if tag_matches(t, m)]
        total = len(tags)
        from .options import tag_rows

        cap = tag_rows()  # (Settings > Simulate > Tags listed at most)
        if server_total is not None and len(tags) >= cap:
            total = max(total, server_total)
        # the tags the project does not define go last, in a group of their own, then the
        # window's page variables (never cut by the row limit)
        pvars = [t for t in tags if t.page_var or t.param]
        tags = [t for t in tags if not (t.page_var or t.param)]
        total = len(tags) if server_total is None or len(tags) < cap else max(len(tags), server_total)
        shown = sorted(tags[:cap], key=lambda t: not t.defined) + pvars
        groups: Dict[str, QtGui.QStandardItem] = {}
        root = self.model.invisibleRootItem()
        any_via = False

        def group(title: str, tip: str = "") -> QtGui.QStandardItem:
            g = groups.get(title)
            if g is None:
                g = QtGui.QStandardItem(title)
                f = g.font()
                f.setBold(True)
                g.setFont(f)
                if tip:
                    g.setToolTip(tip)
                groups[title] = g
                root.appendRow([g, QtGui.QStandardItem(""), QtGui.QStandardItem(""), QtGui.QStandardItem("")])
            return g

        for t in shown:
            self._tags[_key(t)] = t
            parent = root
            if t.param:
                parent = group(
                    PARAMS_GROUP,
                    "The parameters ([Section]Name, ParameterGet) the "
                    "page's Cicode read, where each value came from and "
                    "who read it. Set one to change what the next read "
                    "gets (the project is not changed).",
                )
            elif t.page_var:
                parent = group(
                    PAGE_VARS_GROUP,
                    "Values the page's Cicode keeps for this "
                    "window with PageSetInt / PageSetStr. A name "
                    "shows once the page's code used it.",
                )
            elif t.cicode_used:
                parent = group(
                    CICODE_GROUP,
                    "Tags the page does not name itself that its "
                    "Cicode used while it ran (names built at run "
                    "time too). A tag shows once its code has run.",
                )
            elif not t.defined:
                parent = group(
                    UNDEFINED_GROUP,
                    "Tags the page reads that the project does not "
                    "define. Their type is guessed from the page.",
                )
            elif t.equipment:
                parent = group(t.equipment)
            label = t.item if (t.defined and t.equipment and t.item) else t.name
            name = QtGui.QStandardItem(label)
            name.setData(_key(t), _NAME_ROLE)
            name.setData(t.locked, _LOCK_ROLE)
            tip = (
                t.name
                if not t.page_var
                else "%s\nPage variable (%s)" % (t.name, "text" if t.is_string() else "whole number")
            )
            if t.param:
                tip = "%s\nFrom: %s\n%s" % (t.name, t.source or "?", t.comment)
                if t.users:
                    tip += "\nRead by: %s" % ", ".join(t.users)
            if t.via:
                tip += "\nFrom the association '%s' (%s)" % (t.via, t.ref or "?%s?" % t.via)
            if t.cicode_used:
                tip += ("\nFirst used by %s" % t.used_by) if t.used_by else "\nUsed by the page's Cicode"
            if not t.defined:
                tip += "\nSimulated" if t.simulated else "\nNot defined in the project"
            if t.locked:
                tip += "\nLocked: Cicode cannot change it"
                name.setIcon(theme.icon("lock"))
            name.setToolTip(tip)
            if not t.defined:
                name.setForeground(QtGui.QColor(theme.COLORS["text_dim"]))
            val = QtGui.QStandardItem(_fmt(t.value))
            q = QtGui.QStandardItem(t.source if t.param else "" if t.page_var else t.quality)
            if t.param and t.source:
                q.setToolTip("From %s" % t.source)
            via = QtGui.QStandardItem(t.via)
            if t.via:
                any_via = True
                via.setToolTip(t.ref or "")
            parent.appendRow([name, val, q, via])
            self._rows[_key(t)] = (val, q, name)
        self.tree.setColumnHidden(3, not any_via)
        self.tree.expandAll()
        # the page variables count too
        nv = len(pvars)
        extra = ", %d page variable%s" % (nv, "" if nv == 1 else "s") if nv else ""
        if total > len(shown) - len(pvars):
            self.count.setText(
                "Showing %d of %d tags%s. Type to narrow the list." % (len(shown) - len(pvars), total, extra)
            )
        else:
            self.count.setText(("%d tags" % total if total != 1 else "1 tag") + extra)
        self._serial = src.serial()
        if keep is not None:
            self.select_tag(_key(keep))
        self._show_selected()

    def _query(self, src):
        """(tags, total) from the source: a source that searches itself (the Cicode
        simulator: ``search_tags(text, page_only)``, never waiting) gets the search text and
        tells the total; else every tag (filtered here)."""
        finder = getattr(src, "search_tags", None)
        if finder is not None:
            if self.search.regex:
                try:
                    return finder(self.search.text().strip(), self.page_only.isChecked(), regex=True)
                except TypeError:
                    pass  # (a source without regex: matched below)
            return finder(self.search.text().strip(), self.page_only.isChecked())
        return src.tags(page_only=self.page_only.isChecked()), None

    def refresh(self, force: bool = False) -> None:
        """Show the source's current values (called every tick; cheap when nothing changed)."""
        src = self._src
        if src is None:
            return
        s = src.serial()
        if s == self._serial and not force:
            return
        self._serial = s
        tags, _total = self._query(src)
        keys = tuple(_key(t) for t in tags)
        if keys != self._names:
            self.rebuild()
            return
        for k, t in zip(keys, tags):
            row = self._rows.get(k)
            if row is None:
                continue
            self._tags[k] = t
            v = _fmt(t.value)
            if row[0].text() != v:
                row[0].setText(v)
            q = t.source if t.param else "" if t.page_var else t.quality
            if row[1].text() != q:
                row[1].setText(q)
            if len(row) > 2 and bool(row[2].data(_LOCK_ROLE)) != t.locked:
                row[2].setData(t.locked, _LOCK_ROLE)
                row[2].setIcon(theme.icon("lock") if t.locked else QtGui.QIcon())
        sel = self.selected_tag()
        if sel is not None:
            self._show_properties(sel)
        if sel is not None and not self.slider.isSliderDown():
            was_busy, self._busy = self._busy, True
            try:
                if sel.is_digital():
                    self.toggle.setChecked(bool(sel.value))
                elif sel.is_string():
                    if not self.text.hasFocus():
                        self.text.setText("" if sel.value is None else str(sel.value))
                else:
                    self._set_number(sel.value)
            finally:
                self._busy = was_busy


def where_text(e) -> tuple:
    """(short, full) 'where' of a log line: 'PageDisplay, citect.ci:2594' for the
    column, 'PageDisplay (<full path of citect.ci>:2594)' for the tool tip and the
    details box."""
    line = ":%d" % e.line if e.line else ""
    func = e.function or ""
    an = getattr(e, "an", -1) or -1
    if not func and an > 0:
        # page code (an object's expression or command): the object
        obj = "AN %d" % an + (" %s" % e.source if getattr(e, "source", "") else "")
        return obj, obj + (" on %s" % e.page if getattr(e, "page", "") else "")
    if not e.file:
        return func, func
    base = e.file.replace("/", "\\").rsplit("\\", 1)[-1]
    short = "%s, %s%s" % (func, base, line) if func else "%s%s" % (base, line)
    full = "%s (%s%s)" % (func, e.file, line) if func else "%s%s" % (e.file, line)
    return short, full


def call_text(e) -> str:
    """The Call column: the failing built-in with its values, else the tag it read."""
    if getattr(e, "call", ""):
        return e.call
    if getattr(e, "tag", ""):
        return "tag %s" % e.tag
    return ""


def details_text(e, expressions=None) -> str:
    """Everything a log line says, for the details box: the text, the failing call, the page
    object and its expression (``expressions(an, source)`` = ['KEY = value', ...]), the Cicode
    traceback with each function's arguments."""
    lines = [e.text or ""]
    if getattr(e, "call", ""):
        lines.append("Call: %s" % e.call)
    if getattr(e, "tag", ""):
        lines.append("Tag: %s" % e.tag)
    an = getattr(e, "an", -1) or -1
    if an > 0 or getattr(e, "page", ""):
        what = "Object: AN %d" % an if an > 0 else "Page:"
        if getattr(e, "page", ""):
            what += " on %s" % e.page
        if getattr(e, "window", -1) is not None and getattr(e, "window", -1) >= 0:
            what += ", window %d" % e.window
        if getattr(e, "source", ""):
            what += ", %s" % e.source
        lines.append(what)
        if expressions is not None and an > 0:
            try:
                found = expressions(an, getattr(e, "source", "")) or []
            except Exception:  # noqa: BLE001 -- the details still show the rest
                found = []
            for x in found:
                lines.append("  %s" % x)
    stack = list(getattr(e, "stack", ()) or ())
    if stack:
        lines += ["", "Traceback (most recent call first):"]
        for fr in stack:
            func, file, line = fr[0], fr[1], fr[2]
            args = fr[3] if len(fr) > 3 else ()
            name = func or "(page code)"
            if args:
                name += "(%s)" % ", ".join("%s=%s" % a for a in args)
            where = file.replace("/", "\\").rsplit("\\", 1)[-1] if file else ""
            if where and line:
                where += " line %d" % line
            elif line:
                where = "line %d" % line
            lines.append("  %s%s" % (name, "    %s" % where if where else ""))
            if file and "\\" in file.replace("/", "\\"):
                lines.append("      %s" % file)
    elif e.function or e.file:
        _short, full = where_text(e)
        lines.append("Where: %s" % full)
    return "\n".join(lines)


def _one_line(text: str) -> str:
    return " ".join((text or "").replace("\r", "").split("\n"))


class DetailsBox(QtWidgets.QPlainTextEdit):
    """Details of the selected row, under its list."""

    def __init__(self, name: str, key: str = ""):
        super().__init__()
        self.setReadOnly(True)
        self.setAccessibleName(name)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.setPlaceholderText("Select a line to see all of it.")
        fm = self.fontMetrics()
        # (at least one line: its usual height is the sizeHint's six; a larger minimum is
        # taken from the window's own minimum height)
        self.setMinimumHeight(fm.lineSpacing() + 12)
        # (shown only with something in it: an empty box would take its lines from the
        # window's height for nothing)
        self.setVisible(False)
        self.textChanged.connect(self._sync_shown)
        self._key = key
        self._height = fm.lineSpacing() * 6 + 12
        if key:
            try:
                from ..prefs.settings import app_settings

                h = int(app_settings().value(key, 0) or 0)
            except Exception:  # noqa: BLE001  # swallow-ok: a remembered size is a convenience
                h = 0
            if h >= self.minimumHeight():
                self._height = h

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(super().sizeHint().width(), self._height)

    def _sync_shown(self) -> None:
        """Shown while it has text; on showing, its height again (a splitter shows a widget
        it had hidden at its minimum: one line, not the six or the height the user gave)."""
        want = bool(self.toPlainText())
        if want == (not self.isHidden()):
            return
        self.setVisible(want)
        split = self.parentWidget()
        if want and isinstance(split, QtWidgets.QSplitter):
            self._fit(split)

    def _fit(self, split: QtWidgets.QSplitter) -> None:
        """Its height in ``split`` (as much of it as the other panes leave)."""
        sizes = split.sizes()
        k = split.indexOf(self)
        total = sum(sizes)
        others = [i for i in range(len(sizes)) if i != k and sizes[i] > 0]
        if not (0 <= k < len(sizes)) or total <= 0 or not others:
            return
        room = total - sum(split.widget(i).minimumSizeHint().height() for i in others)
        mine = max(self.minimumHeight(), min(self._height, room))
        rest = max(0, total - mine)
        old = sum(sizes[i] for i in others) or 1
        new = [0] * len(sizes)
        for i in others:
            new[i] = rest * sizes[i] // old
        new[k] = total - sum(new)
        split.setSizes(new)

    def under(self, split: QtWidgets.QSplitter) -> None:
        """Go under the list of ``split``: the list takes the window's changes of height, this
        box keeps the height the user gave it."""
        split.addWidget(self)
        k = split.indexOf(self)
        for i in range(split.count()):
            split.setStretchFactor(i, 0 if i == k else 1)
        split.splitterMoved.connect(lambda *_a: self._moved(split))
        split.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        # a taller window: back to its height when it had less (a short window had cut it)
        if (
            event.type() == QtCore.QEvent.Resize
            and isinstance(obj, QtWidgets.QSplitter)
            and not self.isHidden()
        ):
            k = obj.indexOf(self)
            sizes = obj.sizes()
            if 0 <= k < len(sizes) and sizes[k] < self._height:
                QtCore.QTimer.singleShot(0, self, lambda: self._fit(obj))
        return False

    def _moved(self, split: QtWidgets.QSplitter) -> None:
        sizes = split.sizes()
        k = split.indexOf(self)
        if not (0 <= k < len(sizes)) or sizes[k] < self.minimumHeight():
            return
        self._height = sizes[k]
        self.updateGeometry()
        if self._key:
            try:
                from ..prefs.settings import app_settings

                app_settings().setValue(self._key, int(sizes[k]))
            except Exception:  # noqa: BLE001  # swallow-ok: a remembered size is a convenience
                pass

    def show_text(self, text: str, same_row: bool = False) -> None:
        """Show ``text``. ``same_row``: the row is the one shown already, so text that did
        not change is left alone and changed text keeps the scroll place and the selection."""
        if same_row and text == self.toPlainText():
            return
        if not same_row:
            self.setPlainText(text)
            return
        bar_v, bar_h = self.verticalScrollBar(), self.horizontalScrollBar()
        v, h = bar_v.value(), bar_h.value()
        cur = self.textCursor()
        anchor, pos = cur.anchor(), cur.position()
        self.setPlainText(text)
        if anchor != pos and max(anchor, pos) <= len(text):
            c = self.textCursor()
            c.setPosition(anchor)
            c.setPosition(pos, QtGui.QTextCursor.KeepAnchor)
            self.setTextCursor(c)
        bar_v.setValue(min(v, bar_v.maximum()))
        bar_h.setValue(min(h, bar_h.maximum()))


def _details_box(name: str, key: str = "") -> DetailsBox:
    return DetailsBox(name, key)


#: the LogEntry of a log row (a group row: its latest occurrence)
_ENTRY_ROLE = QtCore.Qt.UserRole + 3
#: the _Group of a group row
_GROUP_ROLE = QtCore.Qt.UserRole + 4
#: a column's sort value
_SORT_ROLE = QtCore.Qt.UserRole + 5

#: the log's columns
COL_TIME, COL_COUNT, COL_KIND, COL_MSG, COL_WHERE, COL_CALL = range(6)
_HEADERS = ["Time", "Count", "Type", "Message", "Where", "Call"]

_SEVERITY_LABELS = {
    "error": ("Errors", "error", "errors"),
    "warning": ("Warnings", "warning", "warnings"),
    "info": ("Info", "info line", "info lines"),
}
#: the occurrences a group keeps (its count goes on)
GROUP_KEEP = 200


def group_key(e) -> tuple:
    """What makes two log lines 'the same thing from the same place': the kind, the
    message, its code, where it happened (the page object and what ran, or the Cicode
    function, file and line) and the built-in that failed. The values a call was made with
    (and the tag it read) may differ between occurrences: they are kept per occurrence."""
    call = getattr(e, "call", "") or ""
    builtin = call.split("(", 1)[0].strip()
    return (
        e.kind,
        getattr(e, "level", ""),
        getattr(e, "code", 0),
        e.text or "",
        e.function or "",
        e.file or "",
        e.line or 0,
        getattr(e, "page", "") or "",
        getattr(e, "an", -1),
        getattr(e, "source", "") or "",
        builtin,
    )


def _severity_icon(sev: str) -> QtGui.QIcon:
    """The app's level mark (notify.level_icon), as every other list of messages."""
    from ..notify import level_icon

    return level_icon(sev if sev in ("error", "warning") else "info")


class _Group:
    """One kind of log line from one place: how often, when, the latest occurrences."""

    __slots__ = ("key", "count", "first", "last", "entries", "calls", "item", "category", "severity")

    def __init__(self, key, e):
        self.key = key
        self.count = 0
        self.first = e.time
        self.last = e.time
        self.entries = collections.deque(maxlen=GROUP_KEEP)
        self.calls: "collections.Counter[str]" = collections.Counter()
        self.item = None
        self.category = category_of(e)
        self.severity = severity_of(e)

    def add(self, e) -> None:
        self.count += 1
        self.last = e.time
        self.entries.append(e)
        c = call_text(e)
        if c and (c in self.calls or len(self.calls) < 50):
            self.calls[c] += 1

    @property
    def latest(self):
        return self.entries[-1] if self.entries else None


class _Row(QtWidgets.QTreeWidgetItem):
    """A log row that sorts by its columns' sort values (times and counts as numbers)."""

    #: while a batch of lines takes its places (OutputsPanel.add_entries): each top-level
    #: row's place in the order worked out (_TimeOrder), by id; None the rest of the time
    rank = None

    def __lt__(self, other):  # noqa: D105
        rank = _Row.rank
        if rank is not None:
            a, b = rank.get(id(self)), rank.get(id(other))
            if a is not None and b is not None:
                return a < b
        col = self.treeWidget().sortColumn() if self.treeWidget() is not None else 0
        a = self.data(col, _SORT_ROLE)
        b = other.data(col, _SORT_ROLE)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text(col).lower() < other.text(col).lower()


class _TimeOrder:
    """The log's rows in time order, worked out while sorting is off."""

    def __init__(self, rows: list):
        self.ids = [id(r) for r in rows]
        self.keys = [r.data(COL_TIME, _SORT_ROLE) for r in rows]
        # (the rows themselves: their ids stay theirs while the batch runs)
        self.rows = rows

    @classmethod
    def of(cls, tree: QtWidgets.QTreeWidget) -> Optional["_TimeOrder"]:
        """The order of ``tree``'s rows; None when a row has no time or the rows are not in
        time order (the tree's own sort is left to place them then)."""
        order = cls([tree.topLevelItem(k) for k in range(tree.topLevelItemCount())])
        keys = order.keys
        if any(type(k) is not float for k in keys) or any(
            keys[k + 1] < keys[k] for k in range(len(keys) - 1)
        ):
            return None
        return order

    def add(self, row, time) -> None:
        """A new group's row (its Time set next, as _fill_row sets it)."""
        key = float(time)
        self._put(bisect.bisect_left(self.keys, key), row, key)

    def move(self, row, time) -> None:
        """A group's row whose Time becomes ``time``."""
        r = self.ids.index(id(row))
        del self.rows[r], self.ids[r], self.keys[r]
        keys = self.keys
        key = float(time)
        at = bisect.bisect_left(keys, key)
        if at < r and not key < keys[r - 1] and not keys[r - 1] < key:
            at = r
        self._put(at, row, key)

    def _put(self, at: int, row, key: float) -> None:
        self.rows.insert(at, row)
        self.ids.insert(at, id(row))
        self.keys.insert(at, key)

    def ranks(self) -> dict:
        return {i: k for k, i in enumerate(self.ids)}


_ICONS: Dict[str, QtGui.QIcon] = {}


def _icon(sev: str) -> QtGui.QIcon:
    ic = _ICONS.get(sev)
    if ic is None:
        ic = _ICONS[sev] = _severity_icon(sev)
    return ic


def _fill_row(
    it: QtWidgets.QTreeWidgetItem, e, time: float, count: Optional[int], occurrence: bool = False
) -> None:
    """A log row; ``occurrence`` = one time of a group (under its row): only what can differ
    from the group's row shows (the time, the call with its values), the rest is the
    group's."""
    short, where = where_text(e)
    call = call_text(e)
    sev = severity_of(e)
    cat = category_of(e)
    it.setText(COL_TIME, "%.2f s" % time)
    it.setData(COL_TIME, _SORT_ROLE, float(time))
    it.setText(COL_COUNT, "" if count is None else ("%d" % count if count > 1 else ""))
    it.setData(COL_COUNT, _SORT_ROLE, int(count or 1))
    it.setTextAlignment(COL_COUNT, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    it.setText(COL_KIND, CATEGORY_LABELS.get(cat, cat))
    it.setData(COL_KIND, _SORT_ROLE, (SEVERITIES.index(sev), CATEGORY_LABELS.get(cat, cat)))
    it.setToolTip(COL_KIND, "%s, %s" % (CATEGORY_LABELS.get(cat, cat), sev))
    # (the Type column says Startup already)
    it.setText(COL_MSG, _one_line(e.text[9:] if e.text.startswith("Startup: ") else e.text))
    it.setText(COL_WHERE, short)
    it.setText(COL_CALL, call)
    it.setData(0, QtCore.Qt.UserRole, e.kind)
    it.setData(0, _ENTRY_ROLE, e)
    it.setData(COL_MSG, QtCore.Qt.UserRole, e.text)
    it.setData(COL_WHERE, QtCore.Qt.UserRole, where)
    it.setToolTip(COL_MSG, tables.tool_tip_text(e.text))
    if where:
        it.setToolTip(COL_WHERE, where)
    if call:
        it.setToolTip(COL_CALL, call)
    if occurrence:
        for col in (COL_KIND, COL_MSG, COL_WHERE):
            it.setText(col, "")
    else:
        it.setIcon(COL_KIND, _icon(sev))
    colour = {"error": "danger", "warning": "warn"}.get(sev)
    if colour:
        it.setForeground(COL_MSG, QtGui.QBrush(theme.color(colour)))


def _parse_filter(text: str):
    """VS Code's filter: comma-separated terms, '!term' leaves those lines out; a line shows
    when it holds one of the other terms (or there are none) and none of the '!' ones."""
    keep, drop = [], []
    for part in (text or "").split(","):
        t = part.strip().lower()
        if not t:
            continue
        if t.startswith("!"):
            if t[1:].strip():
                drop.append(t[1:].strip())
        else:
            keep.append(t)
    return keep, drop


class OutputsPanel(QtWidgets.QWidget):
    """The Outputs panel: tag writes, log lines, errors and unsimulated calls."""

    #: Show on Page: the LogEntry whose object to show
    showObject = QtCore.Signal(object)
    #: Inspect Object: the LogEntry whose object the Inspector shows
    inspectObject = QtCore.Signal(object)
    #: Edit in Editor: the LogEntry whose object the editor opens (SimulateWindow.edit_log_object)
    editObject = QtCore.Signal(object)
    #: the user cleared the log (Clear): the page's error marks go with its lines
    cleared = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Simulation outputs")
        from . import options

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # (the Inputs panel's tabs: document mode, each tab's page with its own margins)
        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs)
        log = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(log)
        lv.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lv.setSpacing(_sp(1))
        # (wraps onto a second row when the panel is narrow, so it never sets the Simulate
        # window's least width)
        from ..flow_layout import FlowLayout

        bar = FlowLayout()
        # the severities: a toggle each, with its count
        shown = {s.strip() for s in str(options.get("log_severities")).split(",")}
        self.severity_buttons: Dict[str, QtWidgets.QToolButton] = {}
        for sev in SEVERITIES:
            b = QtWidgets.QToolButton()
            b.setCheckable(True)
            b.setChecked(sev in shown)
            b.setIcon(_icon(sev))
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setText("0")
            b.setToolTip("Show the %s (click to hide them)" % _SEVERITY_LABELS[sev][0].lower())
            b.setAccessibleName(_SEVERITY_LABELS[sev][0])
            b.toggled.connect(self._severity_toggled)
            bar.addWidget(b)
            self.severity_buttons[sev] = b
        self.types_button = QtWidgets.QPushButton("Types")
        self.types_button.setToolTip("Show or hide each type of line")
        self.types_menu = QtWidgets.QMenu(self.types_button)
        self.types_menu.aboutToShow.connect(self._fill_types_menu)
        self.types_button.setMenu(self.types_menu)
        bar.addWidget(self.types_button)
        # (the search field of every list: its syntax in the tip)
        self.search = theme.SearchField("Search the log")
        self.search.setToolTip(
            "Show the lines whose message, place or call holds one of the "
            "comma-separated words; a word starting with ! leaves its lines "
            "out (for example: Bad tag, !FIL1)"
        )
        self.search.setMinimumWidth(160)
        self.search.setFixedWidth(220)
        bar.addWidget(self.search)
        self.hidden_button = QtWidgets.QPushButton("Hidden")
        self.hidden_button.setToolTip("The messages you hid: show them again")
        self.hidden_menu = QtWidgets.QMenu(self.hidden_button)
        self.hidden_menu.aboutToShow.connect(self._fill_hidden_menu)
        self.hidden_button.setMenu(self.hidden_menu)
        bar.addWidget(self.hidden_button)
        self.group_box = QtWidgets.QCheckBox("Group repeats")
        self.group_box.setToolTip(
            "Show the same line from the same place once, with how "
            "many times it happened; open the row to see each time"
        )
        self.group_box.setChecked(bool(options.get("log_group")))
        bar.addWidget(self.group_box)
        self.show_button = QtWidgets.QPushButton("Show on Page")
        self.show_button.setToolTip("Show the object whose code wrote the selected line")
        self.show_button.setEnabled(False)
        self.show_button.clicked.connect(self._show_object)
        bar.addWidget(self.show_button)
        self.clear_button = QtWidgets.QPushButton("Clear Log")
        # (the log's own scope: the Not simulated list, another tab, stays)
        self.clear_button.setToolTip(
            "Clear the log and the error marks its lines put on the page. The Not simulated list stays"
        )
        bar.addWidget(self.clear_button)
        lv.addLayout(bar)
        self.summary = QtWidgets.QLabel("")
        theme.set_note_role(self.summary)
        self.summary.setTextFormat(QtCore.Qt.RichText)
        self.summary.linkActivated.connect(self._summary_link)
        lv.addWidget(self.summary)
        self.log = QtWidgets.QTreeWidget()
        self.log.setRootIsDecorated(True)
        self.log.setIndentation(12)
        self.log.setUniformRowHeights(True)
        self.log.setHeaderLabels(_HEADERS)
        self.log.setAccessibleName("Simulation log")
        self.log.setSortingEnabled(True)
        self.log.sortByColumn(COL_TIME, QtCore.Qt.AscendingOrder)
        # Message and Where fit their text (capped); the view scrolls rather than cut it
        tables.make_resizable(
            self.log,
            "SimulateWindow/log_typed",
            stretch=COL_MSG,
            caps={COL_TIME: 96, COL_COUNT: 50, COL_KIND: 130, COL_MSG: 0.45, COL_WHERE: 0.2, COL_CALL: 0.3},
            grow=True,
        )
        #: (an, source) -> the object's expressions, for the details (set by the window)
        self.expressions = None
        tables.enable_row_copy(self.log)
        self._copy_actions = [a for a in self.log.actions()]
        # the rows' own menu (hide, show only, copy, show on page)
        self.log.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.log.customContextMenuRequested.connect(self._row_menu)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self.log)
        self.details = _details_box("The selected line", "simulate/log_details_height")
        self.details.under(split)
        #: the row the details show (a line that comes into the list leaves them as they are)
        self._details_of = None
        lv.addWidget(split, 1)
        self.tabs.addTab(log, "Log")
        ns = QtWidgets.QWidget()
        nv = QtWidgets.QVBoxLayout(ns)
        nv.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        self.not_sim = QtWidgets.QTreeWidget()
        self.not_sim.setRootIsDecorated(False)
        self.not_sim.setHeaderLabels(["Function or field", "Calls", "Why"])
        self.not_sim.setAccessibleName("Functions and fields not simulated")
        self.not_sim.setSortingEnabled(True)
        tables.make_resizable(self.not_sim, "SimulateWindow/not_simulated", stretch=2)
        tables.enable_row_copy(self.not_sim)
        # a field row (an expression the simulator could not compile): double-click shows
        # the object on the page
        self.not_sim.itemDoubleClicked.connect(self._not_sim_double_clicked)
        nsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        nsplit.setChildrenCollapsible(False)
        nsplit.addWidget(self.not_sim)
        self.not_sim_details = _details_box("The selected function", "simulate/not_simulated_details_height")
        self.not_sim_details.under(nsplit)
        nv.addWidget(nsplit, 1)
        self.tabs.addTab(ns, "Not Simulated")
        self.log.currentItemChanged.connect(lambda *_a: self._show_details())
        self.log.itemDoubleClicked.connect(self._double_clicked)
        self.log.itemExpanded.connect(self._fill_occurrences)
        self.not_sim.currentItemChanged.connect(lambda *_a: self._show_not_sim_details())
        self._calls: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        # every line (the flat view, and a rebuild), and the groups in the order they began
        from .options import log_lines

        # (Log lines kept, any value of its span; apply_settings resizes it)
        self._all: "collections.deque" = collections.deque(maxlen=log_lines())
        self._groups: "collections.OrderedDict[tuple, _Group]" = collections.OrderedDict()
        # what the filters leave out: types turned off, messages hidden (remembered)
        self._types_off = {t.strip() for t in str(options.get("log_types_off")).split(",") if t.strip()}
        self._hidden: List[dict] = self._load_hidden()
        self.search.searchChanged.connect(lambda _t: self._apply_filter())
        self.group_box.toggled.connect(self._group_toggled)
        self.clear_button.clicked.connect(self.clear_by_user)
        self._show_summary()

    def _theme_changed(self) -> None:
        """Another theme (theme._retheme_widgets calls it): the error and warning lines in its
        colours (a row's colour is set when the row is filled)."""

        def walk(it):
            e = it.data(0, _ENTRY_ROLE)
            colour = {"error": "danger", "warning": "warn"}.get(severity_of(e)) if e is not None else None
            if colour:
                it.setForeground(COL_MSG, QtGui.QBrush(theme.color(colour)))
            for k in range(it.childCount()):
                walk(it.child(k))

        for k in range(self.log.topLevelItemCount()):
            walk(self.log.topLevelItem(k))

    # -- state -------------------------------------------------------------------------------
    @property
    def grouped(self) -> bool:
        return self.group_box.isChecked()

    def clear_by_user(self) -> None:
        """Clear Log: the log's lines, and (``cleared``) the error marks they put on the page,
        so the page shows what the log does. The Not simulated list stays (it is another
        tab). (``clear`` empties everything: a tab change fills it with that tab's lines
        again.)"""
        self.log.clear()
        self._all.clear()
        self._groups.clear()
        self._details_of = None
        self.details.clear()
        self._show_summary()
        self.cleared.emit()

    def clear(self) -> None:
        self.log.clear()
        self.not_sim.clear()
        self._calls = {}
        self._all.clear()
        self._groups.clear()
        self._details_of = None
        self.details.clear()
        self.not_sim_details.clear()
        self._show_summary()

    def groups(self) -> List[_Group]:
        return list(self._groups.values())

    def entries(self) -> List[QtWidgets.QTreeWidgetItem]:
        """The top-level rows (groups, or every line with Group repeats off)."""
        return [self.log.topLevelItem(k) for k in range(self.log.topLevelItemCount())]

    def visible_entries(self) -> List[QtWidgets.QTreeWidgetItem]:
        return [r for r in self.entries() if not r.isHidden()]

    def selected_entry(self) -> Optional[LogEntry]:
        it = self.log.currentItem()
        return it.data(0, _ENTRY_ROLE) if it is not None else None

    def _group_toggled(self, on: bool) -> None:
        from . import options

        options.put("log_group", bool(on))
        self._rebuild()

    # -- the filters ---------------------------------------------------------------------------
    def shown_severities(self) -> set:
        return {s for s, b in self.severity_buttons.items() if b.isChecked()}

    def set_severity_shown(self, sev: str, on: bool) -> None:
        self.severity_buttons[sev].setChecked(bool(on))

    def _severity_toggled(self, _on: bool) -> None:
        from . import options

        options.put(
            "log_severities",
            ",".join(s for s in SEVERITIES if self.severity_buttons[s].isChecked()) or "none",
        )
        self._apply_filter()

    def set_type_shown(self, cat: str, on: bool) -> None:
        if on:
            self._types_off.discard(cat)
        else:
            self._types_off.add(cat)
        from . import options

        options.put("log_types_off", ",".join(sorted(self._types_off)))
        self._apply_filter()

    def show_only_type(self, cat: str) -> None:
        self._types_off = {k for k, _l, _w in CATEGORIES if k != cat}
        from . import options

        options.put("log_types_off", ",".join(sorted(self._types_off)))
        self._apply_filter()

    def _fill_types_menu(self) -> None:
        m = self.types_menu
        m.clear()
        counts = collections.Counter()
        for g in self._groups.values():
            counts[g.category] += g.count
        for cat, label, what in CATEGORIES:
            a = m.addAction("%s (%d)" % (label, counts.get(cat, 0)))
            a.setCheckable(True)
            a.setChecked(cat not in self._types_off)
            a.setToolTip(what)
            a.toggled.connect(lambda on, c=cat: self.set_type_shown(c, on))
        m.addSeparator()
        a = m.addAction("Show All Types")
        a.setEnabled(bool(self._types_off))
        a.triggered.connect(self._all_types)

    def _all_types(self) -> None:
        self._types_off = set()
        from . import options

        options.put("log_types_off", "")
        self._apply_filter()

    # -- hidden messages -----------------------------------------------------------------------
    def _load_hidden(self) -> List[dict]:
        import json
        from . import options

        try:
            rules = json.loads(str(options.get("log_hidden")) or "[]")
        except ValueError:
            rules = []
        return [r for r in rules if isinstance(r, dict) and r.get("scope") in ("place", "text")]

    def _store_hidden(self) -> None:
        import json
        from . import options

        options.put("log_hidden", json.dumps(self._hidden))

    def hidden_rules(self) -> List[dict]:
        return [dict(r) for r in self._hidden]

    def _hidden_by(self, e) -> Optional[dict]:
        if not self._hidden:
            return None
        key = None
        for r in self._hidden:
            if r["scope"] == "place":
                if key is None:
                    key = list(group_key(e))
                if r.get("key") == key:
                    return r
            elif r.get("text") == (e.text or "") and r.get("category") == category_of(e):
                return r
        return None

    def hide_message(self, e, everywhere: bool = False) -> None:
        """Hide a line (from its menu): this message from this place, or ``everywhere``."""
        text = _one_line(e.text)
        label = text if len(text) <= 70 else text[:69] + "…"
        if everywhere:
            rule = {
                "scope": "text",
                "category": category_of(e),
                "text": e.text or "",
                "label": "%s (everywhere)" % label,
            }
        else:
            short, _where = where_text(e)
            rule = {
                "scope": "place",
                "key": list(group_key(e)),
                "label": "%s%s" % (label, " (%s)" % short if short else ""),
            }
        if rule not in self._hidden:
            self._hidden.append(rule)
            self._store_hidden()
        self._apply_filter()

    def unhide(self, rule: Optional[dict] = None) -> None:
        """Show a hidden message again (None: every one)."""
        if rule is None:
            self._hidden = []
        else:
            self._hidden = [r for r in self._hidden if r != rule]
        self._store_hidden()
        self._apply_filter()

    def _fill_hidden_menu(self) -> None:
        m = self.hidden_menu
        m.clear()
        lines = collections.Counter()
        for g in self._groups.values():
            e = g.latest
            r = self._hidden_by(e) if e is not None else None
            if r is not None:
                lines[id(r)] += g.count
        for r in self._hidden:
            a = m.addAction("Show again: %s (%d)" % (r.get("label", ""), lines.get(id(r), 0)))
            a.triggered.connect(lambda _c=False, r=r: self.unhide(r))
        m.addSeparator()
        m.addAction("Show All Hidden Messages").triggered.connect(lambda: self.unhide(None))

    # -- the rows' menu ------------------------------------------------------------------------
    def _row_menu(self, pos) -> None:
        it = self.log.itemAt(pos)
        e = it.data(0, _ENTRY_ROLE) if it is not None else None
        m = QtWidgets.QMenu(self)
        if e is not None:
            self.log.setCurrentItem(it)
            cat = category_of(e)
            label = CATEGORY_LABELS.get(cat, cat)
            m.addAction("Hide This Message").triggered.connect(lambda: self.hide_message(e))
            m.addAction("Hide This Message Everywhere").triggered.connect(
                lambda: self.hide_message(e, everywhere=True)
            )
            m.addAction('Hide All "%s"' % label).triggered.connect(lambda: self.set_type_shown(cat, False))
            m.addAction('Show Only "%s"' % label).triggered.connect(lambda: self.show_only_type(cat))
            m.addSeparator()
            if (getattr(e, "an", -1) or -1) > 0:
                m.addAction("Show on Page").triggered.connect(self._show_object)
                m.addAction("Inspect Object").triggered.connect(lambda: self.inspectObject.emit(e))
                m.addAction("Edit in Editor").triggered.connect(lambda: self.editObject.emit(e))
        for a in self._copy_actions:
            m.addAction(a)
        m.exec(self.log.viewport().mapToGlobal(pos))

    # -- adding --------------------------------------------------------------------------------
    #: a batch with new group rows places its rows itself (_TimeOrder); off, the tree's own
    #: sort places them (the same rows in the same order: tests/test_sim_log_groups.py)
    PLACE_ROWS = True

    def add_entries(self, entries: List[LogEntry]) -> None:
        if not entries:
            return
        sb = self.log.verticalScrollBar()
        at_end = sb.value() >= sb.maximum()
        grouped = self.grouped
        cur = self.log.currentItem()
        self.log.setUpdatesEnabled(False)
        # (the rows sort as they come: off while adding, on again after; rows sorted by time
        # ascending come in order already, so the sort is left alone: turning it on again
        # re-sorts every row)
        hdr = self.log.header()
        in_order = (
            hdr.sortIndicatorSection() == COL_TIME and hdr.sortIndicatorOrder() == QtCore.Qt.AscendingOrder
        )
        # a new group row: the places the tree's sort gives the rows are worked out here, with
        # its sorting off, and taken in one sort at the end (_TimeOrder)
        order = None
        if (
            grouped
            and in_order
            and self.PLACE_ROWS
            and any(group_key(e) not in self._groups for e in entries)
        ):
            order = _TimeOrder.of(self.log)
        if not in_order or order is not None:
            self.log.setSortingEnabled(False)
        done = False
        try:
            for e in entries:
                self._all.append(e)
                g = self._add_to_group(e)
                if grouped:
                    self._show_group(g, e, order)
                else:
                    self._add_flat(e)
                self._count_not_simulated(e)
            if not grouped:
                extra = self.log.topLevelItemCount() - self._all.maxlen
                for _k in range(max(0, extra)):
                    self.log.takeTopLevelItem(0)
            self._trim_groups()
            done = True
        finally:
            if order is not None:
                _Row.rank = order.ranks() if done else None
                try:
                    self.log.setSortingEnabled(True)
                finally:
                    _Row.rank = None
            elif not in_order:
                self.log.setSortingEnabled(True)
            self.log.setUpdatesEnabled(True)
        if cur is not None and self.log.currentItem() is cur:
            self._show_details()  # (its count and times moved on)
        self._show_summary()
        if at_end:
            self.log.scrollToBottom()

    def _add_to_group(self, e) -> _Group:
        k = group_key(e)
        g = self._groups.get(k)
        if g is None:
            g = self._groups[k] = _Group(k, e)
        g.add(e)
        return g

    def _show_group(self, g: _Group, e, order: Optional[_TimeOrder] = None) -> None:
        """``order``: the batch's row order (add_entries), the tree's sorting off meanwhile."""
        it = g.item
        if it is None:
            it = g.item = _Row()
            it.setData(0, _GROUP_ROLE, g)
            self.log.addTopLevelItem(it)
            it.setHidden(not self._passes(e))
            if order is not None:
                order.add(it, g.last)
        elif order is not None:
            order.move(it, g.last)
        _fill_row(it, e, g.last, g.count)
        it.setChildIndicatorPolicy(
            QtWidgets.QTreeWidgetItem.ShowIndicator
            if g.count > 1
            else QtWidgets.QTreeWidgetItem.DontShowIndicator
        )
        if g.count > 1:
            it.setToolTip(COL_COUNT, "%d times, first at %.2f s, last at %.2f s" % (g.count, g.first, g.last))
            it.setToolTip(COL_TIME, "Last at %.2f s (first at %.2f s)" % (g.last, g.first))
        if it.isExpanded():
            # an open group shows its new occurrence too (the oldest kept goes)
            ch = _Row()
            _fill_row(ch, e, e.time, None, occurrence=True)
            it.addChild(ch)
            if order is not None and it.childCount() > GROUP_KEEP:
                self._occurrences_in_order(it)
            while it.childCount() > GROUP_KEEP:
                it.takeChild(0)

    @staticmethod
    def _occurrences_in_order(it) -> None:
        """A group's occurrences sorted by time before the oldest goes, as the sort an insert
        leaves pending sorts them first when the tree sorts as it goes (a batch with its
        sorting off: add_entries)."""
        keys = [it.child(k).data(COL_TIME, _SORT_ROLE) for k in range(it.childCount())]
        if any(b < a for a, b in zip(keys, keys[1:])):
            it.sortChildren(COL_TIME, QtCore.Qt.AscendingOrder)

    def _add_flat(self, e) -> None:
        it = _Row()
        _fill_row(it, e, e.time, None)
        it.setChildIndicatorPolicy(QtWidgets.QTreeWidgetItem.DontShowIndicator)
        self.log.addTopLevelItem(it)
        # (once it is in the tree: an item's hidden state is the tree's, Qt ignores it before)
        it.setHidden(not self._passes(e))

    _FIELD_RE = re.compile(r"^Not simulated: AN (\d+) (\S+(?: \S+)?): (.*)$", re.S)

    def _count_not_simulated(self, e) -> None:
        if e.kind != "not_simulated" and e.text.startswith("Not simulated: AN "):
            # an object field the simulator does not run (its expression does not compile)
            m = self._FIELD_RE.match(e.text)
            if m is None:
                return
            key = "AN %s %s" % (m.group(1), m.group(2))
            row = self._calls.get(key)
            if row is None:
                row = QtWidgets.QTreeWidgetItem([key, "", m.group(3).strip()])
                row.setData(0, QtCore.Qt.UserRole, int(m.group(1)))
                row.setToolTip(2, m.group(3).strip())
                self.not_sim.addTopLevelItem(row)
                self._calls[key] = row
            return
        if e.kind != "not_simulated" or not e.function:
            return
        row = self._calls.get(e.function)
        if row is None:
            row = QtWidgets.QTreeWidgetItem([e.function, ""])
            self.not_sim.addTopLevelItem(row)
            self._calls[e.function] = row
        # LogEntry.count = the calls so far (notSimulated {function, count})
        n = max(int(e.count or 1), int(row.data(1, QtCore.Qt.UserRole) or 0))
        row.setData(1, QtCore.Qt.UserRole, n)
        row.setText(1, str(n))

    def _not_sim_double_clicked(self, item, _col) -> None:
        an = item.data(0, QtCore.Qt.UserRole)
        if isinstance(an, int) and an > 0:
            self.showObject.emit(LogEntry(0.0, "log", item.text(0), an=an))

    def _trim_groups(self) -> None:
        """At most as many groups as log lines: the ones seen longest ago go."""
        extra = len(self._groups) - self._all.maxlen
        if extra <= 0:
            return
        for g in sorted(self._groups.values(), key=lambda g: g.last)[:extra]:
            self._groups.pop(g.key, None)
            if g.item is not None:
                k = self.log.indexOfTopLevelItem(g.item)
                if k >= 0:
                    self.log.takeTopLevelItem(k)
                g.item = None

    def _fill_occurrences(self, it) -> None:
        """A group opened: its occurrences (the latest GROUP_KEEP), oldest first."""
        g = it.data(0, _GROUP_ROLE)
        if g is None or it.childCount():
            return
        self.log.setUpdatesEnabled(False)
        try:
            for e in g.entries:
                ch = _Row()
                _fill_row(ch, e, e.time, None, occurrence=True)
                it.addChild(ch)
        finally:
            self.log.setUpdatesEnabled(True)

    def apply_settings(self) -> None:
        """Settings > Simulate > Log lines kept changed: the lines kept take the new limit
        (the oldest go when it is lower), and the view shows what is kept."""
        from .options import log_lines

        n = log_lines()
        if n == self._all.maxlen:
            return
        self._all = collections.deque(self._all, maxlen=n)
        self._trim_groups()
        self._rebuild()

    def _rebuild(self) -> None:
        """The view again from every line kept (Group repeats switched)."""
        self.log.clear()
        for g in self._groups.values():
            g.item = None
        self._details_of = None
        self.details.clear()
        self.log.setUpdatesEnabled(False)
        self.log.setSortingEnabled(False)
        try:
            if self.grouped:
                for g in self._groups.values():
                    e = g.latest
                    if e is not None:
                        self._show_group(g, e)
            else:
                for e in self._all:
                    self._add_flat(e)
        finally:
            self.log.setSortingEnabled(True)
            self.log.setUpdatesEnabled(True)
        self._show_summary()
        self.log.scrollToBottom()

    # -- which rows show -------------------------------------------------------------------
    def _why_hidden(self, e) -> str:
        """'' when the line shows, else why not: 'severity', 'type', 'hidden', 'filter'."""
        if severity_of(e) not in self.shown_severities():
            return "severity"
        if category_of(e) in self._types_off:
            return "type"
        if self._hidden_by(e) is not None:
            return "hidden"
        if self.search.regex:
            # the '.*': the text is one regular expression; '!' first leaves those lines out
            text = self.search.text().strip()
            drop = text.startswith("!")
            m = self.search.matcher(text[1:] if drop else text)
            if not m.empty:
                short, where = where_text(e)
                hit = m(
                    e.text or "",
                    where or "",
                    call_text(e),
                    getattr(e, "page", "") or "",
                    CATEGORY_LABELS.get(category_of(e), ""),
                )
                if hit == drop or not m.ok:
                    return "filter"
            return ""
        keep, drop = _parse_filter(self.search.text())
        if keep or drop:
            short, where = where_text(e)
            hay = " ".join(
                (
                    e.text or "",
                    where or "",
                    call_text(e),
                    getattr(e, "page", "") or "",
                    CATEGORY_LABELS.get(category_of(e), ""),
                )
            ).lower()
            if any(t in hay for t in drop):
                return "filter"
            if keep and not any(t in hay for t in keep):
                return "filter"
        return ""

    def _passes(self, e) -> bool:
        return not self._why_hidden(e)

    def _apply_filter(self) -> None:
        for k in range(self.log.topLevelItemCount()):
            it = self.log.topLevelItem(k)
            e = it.data(0, _ENTRY_ROLE)
            it.setHidden(e is not None and not self._passes(e))
        self._show_summary()

    def show_all(self) -> None:
        """Show All (the line below the bar): every severity and type, no filter. The hidden
        messages stay hidden (Hidden shows them again)."""
        for b in self.severity_buttons.values():
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        from . import options

        options.put("log_severities", ",".join(SEVERITIES))
        self._types_off = set()
        options.put("log_types_off", "")
        self.search.setText("")
        self.search.flush()
        self._apply_filter()

    def _summary_link(self, href: str) -> None:
        if href == "all":
            self.show_all()
        elif href == "hidden":
            self.unhide(None)

    def _show_summary(self) -> None:
        """The severity counts on their buttons, and the line below the bar: how many lines
        show and how many the filters hide, and why."""
        sev_lines = collections.Counter()
        total = 0
        why = collections.Counter()
        rows_shown = rows_total = 0
        for g in self._groups.values():
            sev_lines[g.severity] += g.count
            total += g.count
            e = g.latest
            if e is None:
                continue
            w = self._why_hidden(e)
            if w:
                why[w] += g.count
        for sev, b in self.severity_buttons.items():
            b.setText(str(sev_lines.get(sev, 0)))
        if self.grouped:
            rows_total = len(self._groups)
            rows_shown = sum(1 for g in self._groups.values() if g.item is not None and not g.item.isHidden())
        else:
            rows_total = self.log.topLevelItemCount()
            rows_shown = sum(1 for r in self.entries() if not r.isHidden())
        n_hidden = len(self._hidden)
        self.hidden_button.setVisible(n_hidden > 0)
        self.hidden_button.setText("Hidden: %d" % n_hidden)
        off = len(self._types_off)
        self.types_button.setText("Types" if not off else "Types (%d off)" % off)
        if not total:
            self.summary.setText("Nothing logged yet.")
            return
        hidden_lines = sum(why.values())
        if not hidden_lines:
            what = "%d line%s" % (total, "" if total == 1 else "s")
            if self.grouped and rows_total < total:
                what += " in %d row%s" % (rows_total, "" if rows_total == 1 else "s")
            self.summary.setText("Showing everything: %s." % what)
            return
        reasons = []
        off_sev = [_SEVERITY_LABELS[s][0].lower() for s in SEVERITIES if s not in self.shown_severities()]
        if why.get("severity"):
            reasons.append("%s off" % " and ".join(off_sev))
        if why.get("type"):
            reasons.append("%d type%s off" % (off, "" if off == 1 else "s"))
        if why.get("hidden"):
            reasons.append("%d hidden message%s" % (n_hidden, "" if n_hidden == 1 else "s"))
        if why.get("filter"):
            reasons.append("the search")
        text = 'Showing %d of %d row%s · %d line%s hidden (%s) · <a href="all">Show All</a>' % (
            rows_shown,
            rows_total,
            "" if rows_total == 1 else "s",
            hidden_lines,
            "" if hidden_lines == 1 else "s",
            ", ".join(reasons),
        )
        if why.get("hidden"):
            text += ' · <a href="hidden">Show Hidden Messages</a>'
        self.summary.setText(text)

    # -- details ---------------------------------------------------------------------------
    def _double_clicked(self, it, _col) -> None:
        if (
            it is not None
            and it.data(0, _GROUP_ROLE) is not None
            and it.childCount() == 0
            and getattr(it.data(0, _GROUP_ROLE), "count", 1) > 1
        ):
            it.setExpanded(True)
            return
        self._show_object()

    def _show_object(self) -> None:
        e = self.selected_entry()
        if e is not None and (getattr(e, "an", -1) or -1) > 0:
            self.showObject.emit(e)

    def _show_details(self) -> None:
        it = self.log.currentItem()
        e = it.data(0, _ENTRY_ROLE) if it is not None else None
        self.show_button.setEnabled(e is not None and (getattr(e, "an", -1) or -1) > 0)
        if it is None or e is None:
            self._details_of = None
            self.details.clear()
            return
        # (the same row again: a line came into the list, or its group's count moved on)
        same = self._details_of is it
        self._details_of = it
        g = it.data(0, _GROUP_ROLE)
        cat = category_of(e)
        head = "%.2f s  %s, %s" % (e.time, severity_of(e).title(), CATEGORY_LABELS.get(cat, cat))
        lines = []
        if g is not None and g.count > 1:
            lines.append("Happened %d times, first at %.2f s, last at %.2f s." % (g.count, g.first, g.last))
            if len(g.calls) > 1:
                lines.append("Calls:")
                for c, n in g.calls.most_common(10):
                    lines.append("  %dx  %s" % (n, c))
                if len(g.calls) > 10:
                    lines.append("  and %d more" % (len(g.calls) - 10))
            lines += ["", "The latest time:"]
        lines.append(head)
        lines.append(details_text(e, self.expressions))
        if getattr(e, "raw", ""):
            lines += ["", "The simulator's text:", e.raw]
        self.details.show_text("\n".join(lines), same_row=same)

    def _show_not_sim_details(self) -> None:
        it = self.not_sim.currentItem()
        if it is None:
            self.not_sim_details.clear()
            return
        n = it.text(1)
        self.not_sim_details.setPlainText(
            "%s\nCalled %s time%s. The simulator does not run this function."
            % (it.text(0), n, "" if n == "1" else "s")
        )


from ...infra import caches as _caches  # noqa: E402

_caches.register("simulate_panels.icons", _ICONS, on=("tests",))
