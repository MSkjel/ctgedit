"""Movement, Scaling and Slider pages."""

from __future__ import annotations
from ...format.cnum import c_atoi
from ..style.theme import sp as _sp  # (the spacing scale)

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..number_field import NumberField
from .fields import NUMBER_RANGES, L_EXPR, L_SLIDER, Field, i, OptionalSpin, Sub
from ...infra.logsetup import note_exception as _note_exc

#: CtDraw's 'At minimum / maximum' spin range (the table the
#: Properties panel shares: fields.NUMBER_RANGES)
_LO, _HI = NUMBER_RANGES["offset"][:2]


def _px(label, key, default):
    lo, hi, unit = NUMBER_RANGES["offset"]
    return i(label, key, default, unit, lo=lo, hi=hi)


def _pc(label, key, default):
    lo, hi, unit = NUMBER_RANGES["size"]
    return i(label, key, default, unit, lo=lo, hi=hi)


def _deg(label, key, default):
    lo, hi, unit = NUMBER_RANGES["angle"]
    return i(label, key, default, unit, lo=lo, hi=hi)


_MOVE_NOTE = "Offsets are pixels from where the object is drawn. A negative offset moves the other way."
_ROT_NOTE = (
    "Angles are degrees from the object as drawn: at minimum it turns "
    "anticlockwise, at maximum clockwise. A negative angle turns the other way."
)
_SCALE_NOTE = "Sizes are percentages of the size drawn; more than 100 % is allowed."

MOVE_H = Sub(
    "Horizontal",
    [
        Field("Movement expression:", "MVX", limit=L_EXPR, range=True),
        _px("Offset at minimum (left):", "MVXMINPT", 0),
        _px("Offset at maximum (right):", "MVXMAXPT", 500),
    ],
    family=("MVX", "MVXMINPT", "MVXMAXPT", "MVXMODE"),
    primary="MVX",
    note=_MOVE_NOTE,
)
MOVE_V = Sub(
    "Vertical",
    [
        Field("Movement expression:", "MVY", limit=L_EXPR, range=True),
        _px("Offset at maximum (up):", "MVYMAXPT", 500),
        _px("Offset at minimum (down):", "MVYMINPT", 0),
    ],
    family=("MVY", "MVYMINPT", "MVYMAXPT", "MVYMODE"),
    primary="MVY",
    note=_MOVE_NOTE,
)
MOVE_ROT = Sub(
    "Rotational",
    [
        Field("Angle expression:", "ROT", limit=L_EXPR, range=True),
        _deg("Angle at minimum (anticlockwise):", "ROTMINPC", 0),
        _deg("Angle at maximum (clockwise):", "ROTMAXPC", 360),
    ],
    family=("ROT", "ROTMINPC", "ROTMAXPC", "ROTMODE", "ROTXORG", "ROTYORG"),
    primary="ROT",
    note=_ROT_NOTE,
)
SCALE_H = Sub(
    "Horizontal",
    [
        Field("Scaling expression:", "SZX", limit=L_EXPR, range=True),
        _pc("Width at minimum:", "SZXMINPC", 0),
        _pc("Width at maximum:", "SZXMAXPC", 100),
    ],
    family=("SZX", "SZXMINPC", "SZXMAXPC", "SZXMODE", "SZXORG"),
    primary="SZX",
    note=_SCALE_NOTE,
)
SCALE_V = Sub(
    "Vertical",
    [
        Field("Scaling expression:", "SZY", limit=L_EXPR, range=True),
        _pc("Height at minimum:", "SZYMINPC", 0),
        _pc("Height at maximum:", "SZYMAXPC", 100),
    ],
    family=("SZY", "SZYMINPC", "SZYMAXPC", "SZYMODE", "SZYORG"),
    primary="SZY",
    note=_SCALE_NOTE,
)
_TAG_WIZARD = ("tag",)
SLIDER_H = Sub(
    "Horizontal",
    [
        Field("Tag:", "MVXSL", limit=L_SLIDER, wizard=_TAG_WIZARD),
        Field("Continuous update of tag", "MVXSLMODE", "slmode", tag="MVXSL"),
        _px("Offset at minimum (left):", "MVXMINPT", 0),
        _px("Offset at maximum (right):", "MVXMAXPT", 500),
    ],
    family=("MVXSL", "MVXSLMODE", "MVXMINPT", "MVXMAXPT"),
    primary="MVXSL",
    note="The offsets are the horizontal movement's: they limit the slide.",
)
SLIDER_V = Sub(
    "Vertical",
    [
        Field("Tag:", "MVYSL", limit=L_SLIDER, wizard=_TAG_WIZARD),
        Field("Continuous update of tag", "MVYSLMODE", "slmode", tag="MVYSL"),
        _px("Offset at maximum (up):", "MVYMAXPT", 500),
        _px("Offset at minimum (down):", "MVYMINPT", 0),
    ],
    family=("MVYSL", "MVYSLMODE", "MVYMINPT", "MVYMAXPT"),
    primary="MVYSL",
    note="The offsets are the vertical movement's: they limit the slide.",
)
SLIDER_ROT = Sub(
    "Rotational",
    [
        Field("Tag:", "ROTSL", limit=L_SLIDER, wizard=_TAG_WIZARD),
        Field("Continuous update of tag", "ROTSLMODE", "slmode", tag="ROTSL"),
        _deg("Angle at minimum (anticlockwise):", "ROTMINPC", 0),
        _deg("Angle at maximum (clockwise):", "ROTMAXPC", 360),
    ],
    family=("ROTSL", "ROTSLMODE", "ROTMINPC", "ROTMAXPC", "ROTMODE", "ROTXORG", "ROTYORG"),
    primary="ROTSL",
    note="The angles and the centre axis are the rotation's: they limit the turn.",
)

MOVEMENT_SUBS = [MOVE_H, MOVE_V, MOVE_ROT]
SCALING_SUBS = [SCALE_H, SCALE_V]
SLIDER_SUBS = [SLIDER_H, SLIDER_V, SLIDER_ROT]
SUBS = MOVEMENT_SUBS + SCALING_SUBS + SLIDER_SUBS

#: a property's primary key -> the keys CtDraw's page always writes with it
#: (their fields' defaults are CtDraw's)
COMPANIONS: Dict[str, Tuple[str, ...]] = {
    "MVX": ("MVXMINPT", "MVXMAXPT"),
    "MVY": ("MVYMINPT", "MVYMAXPT"),
    "ROT": ("ROTMINPC", "ROTMAXPC"),
    "SZX": ("SZXMINPC", "SZXMAXPC"),
    "SZY": ("SZYMINPC", "SZYMAXPC"),
    "MVXSL": ("MVXMINPT", "MVXMAXPT"),
    "MVYSL": ("MVYMINPT", "MVYMAXPT"),
    "ROTSL": ("ROTMINPC", "ROTMAXPC"),
}
#: CtDraw's defaults of the companion keys (the 'At minimum / maximum' spins)
COMPANION_DEFAULTS: Dict[str, str] = {
    "MVXMINPT": "0",
    "MVXMAXPT": "500",
    "MVYMINPT": "0",
    "MVYMAXPT": "500",
    "ROTMINPC": "0",
    "ROTMAXPC": "360",
    "SZXMINPC": "0",
    "SZXMAXPC": "100",
    "SZYMINPC": "0",
    "SZYMAXPC": "100",
}


def companion_defaults(key: str, props: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    """(key, CtDraw's default) for the companions of ``key`` that ``props``
    does not have yet (all of them without ``props``)."""
    have = {k.upper() for k in (props or {})}
    return [(k, COMPANION_DEFAULTS[k]) for k in COMPANIONS.get(key.upper(), ()) if k not in have]


class Axis(NamedTuple):
    mode: str
    x: Optional[str]
    y: Optional[str]
    axes: str  # 'xy' (a point), 'x' (a vertical axis), 'y' (a horizontal axis)


#: the Center axis offset key sets
AXES: Dict[str, Axis] = {
    "rot": Axis("ROTMODE", "ROTXORG", "ROTYORG", "xy"),
    "sx": Axis("SZXMODE", "SZXORG", None, "x"),
    "sy": Axis("SZYMODE", None, "SZYORG", "y"),
}

_POINT_NAMES = {
    (-1, -1): "top left corner",
    (0, -1): "top centre",
    (1, -1): "top right corner",
    (-1, 0): "middle of the left edge",
    (0, 0): "centre",
    (1, 0): "middle of the right edge",
    (-1, 1): "bottom left corner",
    (0, 1): "bottom centre",
    (1, 1): "bottom right corner",
}
_X_NAMES = {-1: "left edge", 0: "centre", 1: "right edge"}
_Y_NAMES = {-1: "top edge", 0: "middle", 1: "bottom edge"}


def axis_state(props: Dict[str, str], axis: Axis) -> Tuple[Tuple[bool, int, int], str]:
    """((custom, x, y), locked) of a centre axis offset."""
    low = {k.upper(): v for k, v in props.items()}
    custom = c_atoi(low.get(axis.mode)) != 0
    out, locked = [], ""
    for k in (axis.x, axis.y):
        v = low.get(k) if k else None
        if not custom:
            n = c_atoi(v)
            out.append(n if n in (-1, 0, 1) else 0)
            continue
        text = (v or "").strip()
        if not text:
            out.append(0)
        elif re.fullmatch(r"[-+]?\d+", text) and _LO <= int(text) <= _HI:
            out.append(int(text))
        else:
            out.append(0)
            locked = v
    return (custom, out[0], out[1]), locked


def axis_edits(axis: Axis, state: Tuple[bool, int, int]) -> List[Tuple[str, Optional[str]]]:
    """The (key, value) writes of an axis choice (None = the
    key removed)."""
    custom, x, y = state
    out: List[Tuple[str, Optional[str]]] = []
    if custom:
        out.append((axis.mode, "1"))
        if axis.x:
            out.append((axis.x, str(x)))
        if axis.y:
            out.append((axis.y, str(y)))
    else:
        out.append((axis.mode, None))
        if axis.x:
            out.append((axis.x, str(x) if x else None))
        if axis.y:
            out.append((axis.y, str(y) if y else None))
    return out


def axis_choices(family: str) -> List[Tuple[str, Tuple[bool, int, int]]]:
    """The Express choices of an axis family: (label, state), in CtDraw's
    picture order (rows top to bottom, left to right)."""
    if family == "rot":
        pts = [(x, y) for y in (-1, 0, 1) for x in (-1, 0, 1)]
        names = [_POINT_NAMES[p] for p in pts]
    elif family == "sx":
        pts = [(x, 0) for x in (-1, 0, 1)]
        names = [_X_NAMES[p[0]] for p in pts]
    else:
        pts = [(0, y) for y in (-1, 0, 1)]
        names = [_Y_NAMES[p[1]] for p in pts]
    return [(n[:1].upper() + n[1:], (False, x, y)) for n, (x, y) in zip(names, pts)]


def offset_text(state: Tuple[bool, int, int], axis_kind: str = "xy") -> str:
    """A custom offset in words: '168 px right, 3 px up' ('the centre' at 0)."""
    _custom, x, y = state
    parts = []
    if "x" in axis_kind and x:
        parts.append("%d px %s" % (abs(x), "right" if x > 0 else "left"))
    if "y" in axis_kind and y:
        parts.append("%d px %s" % (abs(y), "down" if y > 0 else "up"))
    return ", ".join(parts) or "the centre"


def axis_summary(axis_kind: str, state: Tuple[bool, int, int], rotation: bool) -> str:
    """A plain sentence for an axis choice (the picker's caption)."""
    custom, x, y = state
    if rotation:
        if not custom:
            return "Turns about the %s." % _POINT_NAMES.get((x, y), "centre")
        parts = []
        if x:
            parts.append("%d px %s" % (abs(x), "right" if x > 0 else "left"))
        if y:
            parts.append("%d px %s" % (abs(y), "down" if y > 0 else "up"))
        return (
            "Turns about the centre."
            if not parts
            else "Turns about the point %s from the centre." % " and ".join(parts)
        )
    if axis_kind == "x":
        if not custom:
            return {
                -1: "The left edge stays in place; the width changes to the right.",
                0: "The centre stays in place; the width changes on both sides.",
                1: "The right edge stays in place; the width changes to the left.",
            }[x]
        if not x:
            return "The centre stays in place."
        return "The line %d px %s of the centre stays in place." % (abs(x), "right" if x > 0 else "left")
    if not custom:
        return {
            -1: "The top edge stays in place; the height changes downwards.",
            0: "The middle stays in place; the height changes up and down.",
            1: "The bottom edge stays in place; the height changes upwards.",
        }[y]
    if not y:
        return "The middle stays in place."
    return "The line %d px %s the middle stays in place." % (abs(y), "below" if y > 0 else "above")


# --------------------------------------------------------------------------- widgets
class PointGrid(QtWidgets.QWidget):
    """CtDraw's Express picture: the object's box with the points an axis can
    sit on -- 9 for a rotation ('xy'), the left / centre / right lines for a
    horizontal scaling ('x'), top / middle / bottom for a vertical one ('y').
    Click a point or use the arrow keys."""

    changed = QtCore.Signal()

    _MARGIN = 10

    def __init__(self, axes: str = "xy", parent=None):
        super().__init__(parent)
        self._axes = axes
        self._pt = (0, 0)
        self._hover: Optional[Tuple[int, int]] = None
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        w, h = {"xy": (104, 72), "x": (104, 44), "y": (60, 72)}[axes]
        self.setFixedSize(w, h)
        self.setAccessibleName("Centre axis point")

    # -- the points ---------------------------------------------------------------------
    def candidates(self) -> List[Tuple[int, int]]:
        xs = (-1, 0, 1) if "x" in self._axes else (0,)
        ys = (-1, 0, 1) if "y" in self._axes else (0,)
        return [(x, y) for y in ys for x in xs]

    def _box(self) -> QtCore.QRectF:
        m = self._MARGIN
        return QtCore.QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)

    def pos_of(self, pt: Tuple[int, int]) -> QtCore.QPointF:
        b = self._box()
        return QtCore.QPointF(b.center().x() + pt[0] * b.width() / 2, b.center().y() + pt[1] * b.height() / 2)

    def point(self) -> Tuple[int, int]:
        return self._pt

    def set_point(self, pt: Tuple[int, int], emit: bool = False) -> None:
        pt = (pt[0] if "x" in self._axes else 0, pt[1] if "y" in self._axes else 0)
        if pt not in self.candidates():
            pt = (0, 0)
        if pt == self._pt:
            return
        self._pt = pt
        self.update()
        if emit:
            self.changed.emit()

    def name_of(self, pt: Tuple[int, int]) -> str:
        if self._axes == "xy":
            return _POINT_NAMES[pt].capitalize()
        return (_X_NAMES[pt[0]] if self._axes == "x" else _Y_NAMES[pt[1]]).capitalize()

    def _hit(self, pos) -> Optional[Tuple[int, int]]:
        best, dist = None, 14.0**2
        for pt in self.candidates():
            p = self.pos_of(pt)
            if self._axes == "x":
                d = (p.x() - pos.x()) ** 2
            elif self._axes == "y":
                d = (p.y() - pos.y()) ** 2
            else:
                d = (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2
            if d < dist:
                best, dist = pt, d
        return best

    # -- events -------------------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        if not self.isEnabled():
            return
        pt = self._hit(ev.position())
        if pt is not None:
            self.set_point(pt, emit=True)
        self.setFocus()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        pt = self._hit(ev.position())
        if pt != self._hover:
            self._hover = pt
            self.setToolTip(self.name_of(pt) if pt is not None else "")
            self.update()

    def leaveEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        self._hover = None
        self.update()
        super().leaveEvent(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        step = {
            QtCore.Qt.Key_Left: (-1, 0),
            QtCore.Qt.Key_Right: (1, 0),
            QtCore.Qt.Key_Up: (0, -1),
            QtCore.Qt.Key_Down: (0, 1),
        }.get(ev.key())
        if step is None or not self.isEnabled():
            super().keyPressEvent(ev)
            return
        x = max(-1, min(1, self._pt[0] + step[0]))
        y = max(-1, min(1, self._pt[1] + step[1]))
        self.set_point((x, y), emit=True)

    def paintEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on = self.isEnabled()
        from ..style.theme import color

        dim = color("text_dim")
        accent = color("accent") if on else color("control_edge")
        panel = color("panel")
        b = self._box()
        pen = QtGui.QPen(dim, 1, QtCore.Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(b)
        sel = self.pos_of(self._pt)
        p.setPen(QtGui.QPen(accent, 1.4))
        if self._axes == "x":
            p.drawLine(QtCore.QPointF(sel.x(), b.top() - 4), QtCore.QPointF(sel.x(), b.bottom() + 4))
        elif self._axes == "y":
            p.drawLine(QtCore.QPointF(b.left() - 4, sel.y()), QtCore.QPointF(b.right() + 4, sel.y()))
        else:
            p.drawLine(QtCore.QPointF(sel.x() - 6, sel.y()), QtCore.QPointF(sel.x() + 6, sel.y()))
            p.drawLine(QtCore.QPointF(sel.x(), sel.y() - 6), QtCore.QPointF(sel.x(), sel.y() + 6))
        for pt in self.candidates():
            c = self.pos_of(pt)
            chosen = pt == self._pt
            p.setPen(QtGui.QPen(accent if chosen else dim, 1.2))
            p.setBrush(accent if chosen else panel)
            r = 4.5 if chosen else 3.5
            p.drawEllipse(c, r, r)
            if pt == self._hover and on and not chosen:
                p.setPen(QtGui.QPen(accent, 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(c, 6.0, 6.0)
        if self.hasFocus():
            p.setPen(QtGui.QPen(accent, 1, QtCore.Qt.DotLine))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(sel, 7.5, 7.5)
        p.end()


class AxisPicker(QtWidgets.QWidget):
    """Center axis offset: (o) Express -- a :class:`PointGrid` -- or
    (o) Custom -- pixels right of / below the object's centre -- with a line
    saying what the choice does."""

    changed = QtCore.Signal()

    def __init__(self, axis_kind: str, rotation: bool, parent=None):
        super().__init__(parent)
        self._kind = axis_kind
        self._rotation = rotation
        self._syncing = False
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(_sp(1))
        radios = QtWidgets.QHBoxLayout()
        self.rb_express = QtWidgets.QRadioButton("Express")
        self.rb_express.setToolTip("Pick the point on the object's outline")
        self.rb_custom = QtWidgets.QRadioButton("Custom")
        self.rb_custom.setToolTip("Type an offset in pixels from the object's centre")
        grp = QtWidgets.QButtonGroup(self)
        grp.addButton(self.rb_express)
        grp.addButton(self.rb_custom)
        self.rb_express.setChecked(True)
        radios.addWidget(self.rb_express)
        radios.addWidget(self.rb_custom)
        radios.addStretch(1)
        v.addLayout(radios)
        self.grid = PointGrid(axis_kind)
        v.addWidget(self.grid)
        self.custom = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(self.custom)
        h.setContentsMargins(0, 0, 0, 0)
        self.sp_x = self.sp_y = None
        if "x" in axis_kind:
            self.sp_x = self._spin("Pixels to the right of the centre (negative = left)")
            h.addWidget(QtWidgets.QLabel("Right:"))
            h.addWidget(self.sp_x)
        if "y" in axis_kind:
            self.sp_y = self._spin("Pixels below the centre (negative = up)")
            h.addWidget(QtWidgets.QLabel("Down:"))
            h.addWidget(self.sp_y)
        h.addStretch(1)
        v.addWidget(self.custom)
        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("dim")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        self.rb_custom.toggled.connect(self._on_mode)
        self.grid.changed.connect(self._emit)
        self._sync()

    def _spin(self, tip: str) -> QtWidgets.QSpinBox:
        sp = NumberField(lo=_LO, hi=_HI, suffix=" px")
        sp.setToolTip(tip)
        sp.setKeyboardTracking(False)
        sp.valueChanged.connect(self._emit)
        return sp

    # -- state ------------------------------------------------------------------------
    def state(self) -> Tuple[bool, int, int]:
        if self.rb_custom.isChecked():
            return (True, self.sp_x.value() if self.sp_x else 0, self.sp_y.value() if self.sp_y else 0)
        x, y = self.grid.point()
        return (False, x, y)

    def set_state(self, state: Tuple[bool, int, int]) -> None:
        custom, x, y = state
        self._syncing = True
        try:
            (self.rb_custom if custom else self.rb_express).setChecked(True)
            if custom:
                for sp, val in ((self.sp_x, x), (self.sp_y, y)):
                    if sp is not None:
                        sp.setValue(val)
                self.grid.set_point((0, 0))
            else:
                self.grid.set_point((x, y))
                for sp in (self.sp_x, self.sp_y):
                    if sp is not None:
                        sp.setValue(0)
        finally:
            self._syncing = False
        self._sync()

    def summary_text(self) -> str:
        return self.summary.text()

    def set_read_only(self, ro: bool, why: str = "") -> None:
        for w in (self.rb_express, self.rb_custom, self.grid, self.sp_x, self.sp_y):
            if w is not None:
                w.setEnabled(not ro)
        self.setToolTip(why)

    # -- internals ---------------------------------------------------------------------
    def _on_mode(self, _on: bool) -> None:
        self._sync()
        self._emit()

    def _sync(self) -> None:
        custom = self.rb_custom.isChecked()
        self.grid.setVisible(not custom)
        self.custom.setVisible(custom)
        self.summary.setText(axis_summary(self._kind, self.state(), self._rotation))

    def _emit(self, *_a) -> None:
        self.summary.setText(axis_summary(self._kind, self.state(), self._rotation))
        if not self._syncing:
            self.changed.emit()


# --------------------------------------------------------------------------- the editor
_DROPPED_TIP = (
    "A placed genie keeps only its name and alarm indicator. Set the axis in "
    "the genie definition (Edit Definition)."
)


class MotionEditor:
    """Centre axis offset pickers and the rest the declarative fields don't cover."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        #: axis family -> [(picker, sub)]
        self._axes: Dict[str, list] = {}
        self._initial: Dict[str, Tuple[bool, int, int]] = {}
        self._locked: Dict[str, str] = {}
        #: axis families the object cannot keep (a placed genie root drops
        #: them on load): shown, never written -- also after a reload
        self._dropped: set = set()
        #: companion keys the sheet filled with CtDraw's default (not stored yet)
        self._auto: set = set()
        self._read_only = False

    # -- building ----------------------------------------------------------------------
    def add_axis(self, family: str, picker: AxisPicker, sub: Sub) -> None:
        self._axes.setdefault(family, []).append((picker, sub))
        picker.changed.connect(lambda f=family, p=picker: self._axis_changed(f, p))
        drops = getattr(self.sheet._doc, "_genie_root_drops", None)
        try:
            if drops is not None and drops(self.sheet._index, AXES[family].mode):
                self._dropped.add(family)
        except Exception:  # noqa: BLE001 -- a hint only
            _note_exc("objprops.motion.add_axis")
        self._load_axis(family)

    def watch(self, sub: Sub) -> None:
        """Fill ``sub``'s companion fields with CtDraw's defaults when its
        primary field is typed."""
        w = self.sheet.field_widget(sub.primary)
        if w is None or sub.primary not in COMPANIONS:
            return
        sig = getattr(w, "valueChanged", None) or w.textChanged
        sig.connect(lambda *_a, k=sub.primary: self._primary_changed(k))

    # -- axis --------------------------------------------------------------------------
    def _load_axis(self, family: str) -> None:
        state, locked = axis_state(self.sheet._props, AXES[family])
        self._initial[family] = state
        if locked:
            self._locked[family] = locked
        else:
            self._locked.pop(family, None)
        for picker, _sub in self._axes.get(family, []):
            picker.set_state(state)
            if self._read_only:
                continue  # the sheet's own reason stays
            if family in self._dropped:
                picker.set_read_only(True, _DROPPED_TIP)
            elif locked:
                picker.set_read_only(
                    True,
                    "The stored offset '%s' is not a whole number "
                    "this field can show. It is kept unchanged "
                    "(Clear Property removes it)." % locked,
                )
            else:
                picker.set_read_only(False)

    def _axis_changed(self, family: str, src: AxisPicker) -> None:
        state = src.state()
        for picker, _sub in self._axes.get(family, []):
            if picker is not src and picker.state() != state:
                picker.set_state(state)

    def axis_picker(self, family: str, n: int = 0) -> Optional[AxisPicker]:
        pickers = self._axes.get(family, [])
        return pickers[n][0] if n < len(pickers) else None

    # -- companions ----------------------------------------------------------------
    def _on(self, key: str) -> bool:
        w = self.sheet.field_widget(key)
        return w is not None and bool(w.text().strip())

    def _primary_changed(self, key: str) -> None:
        s = self.sheet
        if s._syncing or self._read_only:
            return
        on = self._on(key)
        for ck in COMPANIONS.get(key, ()):
            entries = s.field_entries(ck)
            if not entries:
                continue
            f, w = entries[0]
            if not isinstance(w, OptionalSpin) or w.locked or not w.isEnabledTo(w.parentWidget()):
                continue
            default = COMPANION_DEFAULTS[ck]
            if on:
                if w.key_value() is None:
                    w.setValue(int(default))
                    self._auto.add(ck)
            elif (
                ck in self._auto
                and w.key_value() == default
                and not any(self._on(p) for p, cs in COMPANIONS.items() if p != key and ck in cs)
            ):
                w.setValue(w.minimum())  # back to '(not set)'
                self._auto.discard(ck)

    # -- the sheet's editor contract -------------------------------------------------
    def key_edits(self) -> List[Tuple[str, Optional[str]]]:
        out: List[Tuple[str, Optional[str]]] = []
        for family, pickers in self._axes.items():
            if family in self._locked or family in self._dropped or not pickers:
                continue
            state = pickers[0][0].state()
            if state != self._initial.get(family):
                out.extend(axis_edits(AXES[family], state))
        return out

    def on_clear(self, sub: Sub) -> None:
        for family, pickers in self._axes.items():
            if not any(ps is sub for _p, ps in pickers):
                continue
            if self.sheet._shared_in_use(AXES[family].mode, sub):
                continue  # the other property keeps its axis
            for picker, _ps in pickers:
                picker.set_state((False, 0, 0))
        self._auto.difference_update(k for k in sub.family)

    def problem(self):
        return None

    def apply(self) -> bool:
        return False  # the sheet writes key_edits()

    def reload(self) -> None:
        self._auto.clear()
        for family in self._axes:
            self._load_axis(family)

    def set_read_only(self, ro: bool) -> None:
        self._read_only = bool(ro)
        for family, pickers in self._axes.items():
            for picker, _sub in pickers:
                if ro:
                    picker.set_read_only(True, self.sheet.read_only_reason)
                elif family in self._dropped:
                    picker.set_read_only(True, _DROPPED_TIP)
                elif family not in self._locked:
                    picker.set_read_only(False)


def motion_editor(sheet) -> MotionEditor:
    ed = getattr(sheet, "motion_editor", None)
    if ed is None:
        ed = MotionEditor(sheet)
        sheet.add_editor(ed)
        sheet.motion_editor = ed
    return ed


_PAGE_AXIS = {"move_rot": "rot", "slider_rot": "rot", "scale_h": "sx", "scale_v": "sy"}


def _build(sheet, page, sub: Sub):
    ed = motion_editor(sheet)
    family = _PAGE_AXIS.get(page.id)

    def extra(form):
        axis = AXES[family]
        picker = AxisPicker(axis.axes, rotation=family == "rot")
        box = QtWidgets.QGroupBox("Centre axis offset")
        lay = QtWidgets.QVBoxLayout(box)
        lay.addWidget(picker)
        form.addRow(box)
        ed.add_axis(family, picker, sub)

    w = sheet.build_sub(sub, extra=extra if family else None)
    ed.watch(sub)
    return w


_BY_PAGE = {
    "move_h": MOVE_H,
    "move_v": MOVE_V,
    "move_rot": MOVE_ROT,
    "scale_h": SCALE_H,
    "scale_v": SCALE_V,
    "slider_h": SLIDER_H,
    "slider_v": SLIDER_V,
    "slider_rot": SLIDER_ROT,
}

#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {pid: (lambda sheet, page, _s=sub: _build(sheet, page, _s)) for pid, sub in _BY_PAGE.items()}
