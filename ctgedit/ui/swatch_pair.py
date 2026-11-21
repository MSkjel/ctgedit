"""The ribbon's fill and line swatches."""

from __future__ import annotations
from .style.theme import sp as _sp

from typing import Callable, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .editor import draw_styles as DS
from .colour_picker import ColourButton, ColourPicker, remember_colour

#: what the popover's title says for each swatch
TITLES = {
    "fill": "Fill colour for new shapes",
    "line": "Line colour for new objects",
    "text": "Text colour for new text",
}
#: the parts of the swatch pair and their tool tips
TIPS = {
    "fill": "Fill colour of new shapes: %s. Click to change it",
    "line": "Line colour of new lines, pipes and shape edges: %s. Click to change it",
    "text": "Text colour of new text and numbers: %s. Click to change it",
    "swap": "Swap the fill and line colours (Shift+X)",
    "no_fill": "No fill: new shapes are drawn hollow. Click to switch",
}


def _hex(c: Optional[int]) -> str:
    return "none" if c is None else "#%06X" % (int(c) & 0xFFFFFF)


class SwatchPair(QtWidgets.QToolButton):
    """The fill swatch over the line swatch (module doc). ``state_fn()`` gives the
    colours to show: {'fill', 'line', 'text': 0x00RRGGBB, 'no_fill': bool, 'auto': bool};
    it is asked at each paint, so :meth:`update` is all a change needs."""

    partClicked = QtCore.Signal(str)

    def __init__(self, state_fn: Optional[Callable[[], Dict]] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("SwatchPair")
        self._state_fn = state_fn or (
            lambda: dict(DS.colours(None), no_fill=DS.no_fill(), auto=DS.colour_mode() == "auto")
        )
        self._regions: Dict[str, QtCore.QRectF] = {}
        self._hover = ""
        self._pressed = ""
        self.setText("Colours")
        self.setAccessibleName("Colours of new objects")
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    # -- state -----------------------------------------------------------------------
    def state(self) -> Dict:
        try:
            st = dict(self._state_fn() or {})
        except Exception:  # noqa: BLE001  # swallow-ok: no page yet, the light-page colours
            st = {}
        out = dict(zip(("fill", "line", "text"), DS.LIGHT_PAGE))
        out.update(no_fill=False, auto=True)
        out.update(st)
        return out

    def ribbon_size(self):
        """Its size in the ribbon's Create group (RibbonGroup.add_swatch): narrow, as tall
        as the big buttons."""
        from .ribbon import big_height
        from .style.theme import px

        return px(44), big_height()

    def sizeHint(self) -> QtCore.QSize:  # noqa: D102 -- Qt API
        return QtCore.QSize(44, 64)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: D102 -- Qt API
        return QtCore.QSize(32, 40)

    # -- geometry ------------------------------------------------------------------------
    def _layout(self) -> Dict[str, QtCore.QRectF]:
        """The pair on top (the fill square over the line frame, the swap arrow at the top
        right, No fill at the bottom left), the text colour row under it ('A' and a bar)."""
        r = QtCore.QRectF(self.rect())
        area = r.adjusted(3, 3, -3, -3)
        row_h = max(9.0, min(14.0, area.height() * 0.22))
        side = max(16.0, min(area.width(), area.height() - row_h - 3))
        x0 = area.left() + (area.width() - side) / 2.0
        y0 = area.top() + max(0.0, (area.height() - side - row_h - 3) / 2.0)
        big = side * 0.64
        return {
            "fill": QtCore.QRectF(x0, y0, big, big),
            "line": QtCore.QRectF(x0 + side - big, y0 + side - big, big, big),
            "swap": QtCore.QRectF(x0 + big + 1, y0, side - big - 1, side - big - 1),
            "no_fill": QtCore.QRectF(x0, y0 + big + 1, side - big - 1, side - big - 1),
            "text": QtCore.QRectF(x0, y0 + side + 3, side, row_h),
        }

    def part_at(self, pos) -> str:
        """The part under ``pos`` (widget pixels): 'swap', 'no_fill', 'fill', 'line',
        'text', or 'fill' anywhere else on the button."""
        p = QtCore.QPointF(pos)
        reg = self._layout()
        for name in ("swap", "no_fill", "fill", "line", "text"):
            if reg[name].adjusted(-1, -1, 1, 1).contains(p):
                return name
        return "fill"

    # -- painting -------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt API
        from .style.theme import color, has_keyboard_focus

        st = self.state()
        reg = self._layout()
        self._regions = reg
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.underMouse() or self._pressed:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(color("hover"))
            p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        edge = color("control_edge")
        fg = color("text")
        # the line swatch: a thick frame in the line colour (behind the fill)
        lr = reg["line"]
        thick = max(3.0, lr.width() * 0.24)
        line_c = QtGui.QColor((st["line"] >> 16) & 0xFF, (st["line"] >> 8) & 0xFF, st["line"] & 0xFF)
        path = QtGui.QPainterPath()
        path.addRect(lr)
        inner = lr.adjusted(thick, thick, -thick, -thick)
        path.addRect(inner)
        path.setFillRule(QtCore.Qt.OddEvenFill)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(line_c)
        p.drawPath(path)
        p.setPen(QtGui.QPen(edge, 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(lr)
        p.drawRect(inner)
        # the fill swatch (or the 'none' mark)
        fr = reg["fill"]
        if st.get("no_fill"):
            p.setBrush(color("bg"))
            p.setPen(QtGui.QPen(edge, 1))
            p.drawRect(fr)
            p.setPen(QtGui.QPen(color("danger"), 2))
            p.drawLine(fr.bottomLeft() + QtCore.QPointF(2, -2), fr.topRight() + QtCore.QPointF(-2, 2))
        else:
            fill_c = QtGui.QColor((st["fill"] >> 16) & 0xFF, (st["fill"] >> 8) & 0xFF, st["fill"] & 0xFF)
            p.setBrush(fill_c)
            p.setPen(QtGui.QPen(edge, 1))
            p.drawRect(fr)
        # the swap arrow: a quarter circle with a head at each end
        sr = reg["swap"].adjusted(1.5, 1.5, -1.5, -1.5)
        hover = self._hover
        p.setPen(QtGui.QPen(color("accent") if hover == "swap" else fg, 1.3))
        p.setBrush(QtCore.Qt.NoBrush)
        arc = QtCore.QRectF(sr.left() - sr.width(), sr.top(), sr.width() * 2, sr.height() * 2)
        p.drawArc(arc, 0, 90 * 16)
        h = max(2.0, sr.width() * 0.32)
        a1 = QtCore.QPointF(sr.left(), sr.top())
        p.drawLine(a1, a1 + QtCore.QPointF(h, -h * 0.6))
        p.drawLine(a1, a1 + QtCore.QPointF(h, h * 0.6))
        a2 = QtCore.QPointF(sr.right(), sr.bottom())
        p.drawLine(a2, a2 + QtCore.QPointF(-h * 0.6, -h))
        p.drawLine(a2, a2 + QtCore.QPointF(h * 0.6, -h))
        # the 'no fill' corner: a small white square with a red stroke
        nr = reg["no_fill"].adjusted(1.5, 1.5, -1.5, -1.5)
        p.setPen(QtGui.QPen(color("accent") if hover == "no_fill" else edge, 1))
        p.setBrush(color("bg"))
        p.drawRect(nr)
        p.setPen(QtGui.QPen(color("danger"), 1.4))
        p.drawLine(nr.bottomLeft(), nr.topRight())
        # the text colour: an 'A' and a bar in the colour
        tr = reg["text"]
        text_c = QtGui.QColor((st["text"] >> 16) & 0xFF, (st["text"] >> 8) & 0xFF, st["text"] & 0xFF)
        f = QtGui.QFont(self.font())
        f.setBold(True)
        f.setPixelSize(max(8, int(tr.height())))
        p.setFont(f)
        p.setPen(QtGui.QPen(color("accent") if hover == "text" else fg))
        aw = tr.height() * 0.9
        p.drawText(QtCore.QRectF(tr.left(), tr.top(), aw, tr.height()), QtCore.Qt.AlignCenter, "A")
        bar = QtCore.QRectF(
            tr.left() + aw + 2, tr.top() + tr.height() * 0.2, tr.width() - aw - 2, tr.height() * 0.6
        )
        p.setBrush(text_c)
        p.setPen(QtGui.QPen(edge, 1))
        p.drawRect(bar)
        # the part under the pointer is ringed
        if hover in ("fill", "line"):
            p.setPen(QtGui.QPen(color("accent"), 1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(reg[hover].adjusted(-1, -1, 1, 1))
        if has_keyboard_focus(self):
            p.setPen(QtGui.QPen(color("accent"), 2))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)
        p.end()

    # -- the mouse and keys -----------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        part = self.part_at(e.position())
        if part != self._hover:
            self._hover = part
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self._hover = ""
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.button() == QtCore.Qt.LeftButton:
            self._pressed = self.part_at(e.position())
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.button() == QtCore.Qt.LeftButton and self._pressed:
            part = self.part_at(e.position())
            pressed, self._pressed = self._pressed, ""
            self.update()
            if part == pressed and self.isEnabled():
                self.partClicked.emit(part)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.key() in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.partClicked.emit("fill")
            e.accept()
            return
        super().keyPressEvent(e)

    def event(self, e) -> bool:  # noqa: D102 -- Qt API
        if e.type() == QtCore.QEvent.ToolTip:
            part = self.part_at(e.pos())
            st = self.state()
            if part in ("fill", "line", "text"):
                value = None if part == "fill" and st.get("no_fill") else st[part]
                tip = TIPS[part] % _hex(value)
                if st.get("auto"):
                    tip += ". The colours contrast with the page until you pick one"
            else:
                tip = TIPS[part]
            QtWidgets.QToolTip.showText(e.globalPos(), tip, self)
            return True
        if e.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.FocusOut):
            self.update()
        return super().event(e)


class NewColourPopover(ColourPicker):
    """The colour picker for a colour of new objects: ``target`` 'fill' / 'line' / 'text'
    (a row of three buttons changes it; the title says which). ``chosen(target, colour)``
    after a pick; ``noFillToggled(bool)``, ``autoToggled(bool)`` from their check boxes."""

    chosen = QtCore.Signal(str, int)
    noFillToggled = QtCore.Signal(bool)
    autoToggled = QtCore.Signal(bool)

    def __init__(self, target: str, colours: Dict, parent=None, *, no_fill: bool = False, auto: bool = True):
        self._colours = dict(colours)
        self._target = target if target in TITLES else "fill"
        c = int(self._colours.get(self._target, 0)) & 0xFFFFFF
        super().__init__((c, c), parent, title=TITLES[self._target], flash=False, transparent=False)
        lay = self.layout()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(_sp(0))
        self.target_buttons: Dict[str, QtWidgets.QToolButton] = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for key, text in (("fill", "Fill"), ("line", "Line"), ("text", "Text")):
            b = QtWidgets.QToolButton(self)
            b.setText(text)
            b.setCheckable(True)
            b.setChecked(key == self._target)
            b.setToolTip(TITLES[key])
            b.setAutoRaise(True)
            b.setIcon(QtGui.QIcon(_chip(self._colours.get(key, 0))))
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.clicked.connect(lambda _=False, k=key: self.set_target(k))
            group.addButton(b)
            row.addWidget(b)
            self.target_buttons[key] = b
        row.addStretch(1)
        lay.insertLayout(1, row)
        extras = QtWidgets.QHBoxLayout()
        self.no_fill = QtWidgets.QCheckBox("No fill", self)
        self.no_fill.setToolTip("New shapes are drawn hollow")
        self.no_fill.setChecked(bool(no_fill))
        self.no_fill.toggled.connect(self._no_fill)
        extras.addWidget(self.no_fill)
        self.auto = QtWidgets.QCheckBox("Contrast with the page", self)
        self.auto.setToolTip(
            "Dark lines and text on a light page, light ones on a dark page. Picking a colour turns this off"
        )
        self.auto.setChecked(bool(auto))
        self.auto.toggled.connect(self._auto)
        extras.addWidget(self.auto)
        extras.addStretch(1)
        lay.addLayout(extras)
        self._sync_target()

    def target(self) -> str:
        return self._target

    def set_target(self, target: str) -> None:
        """Set the fill, the line or the text colour (the title and the choice follow)."""
        if target not in TITLES:
            return
        self._target = target
        self._sync_target()

    def _sync_target(self) -> None:
        c = int(self._colours.get(self._target, 0)) & 0xFFFFFF
        self._current = (c, c)
        self.head.setText(TITLES[self._target])
        self.grid.set_selected(self._palette.index_of(c, c))
        self._show_value(self._current)
        for key, b in self.target_buttons.items():
            b.setChecked(key == self._target)
        self.no_fill.setVisible(self._target == "fill")

    def pick_pair(self, on: int, off=None) -> bool:
        c = int(on) & 0xFFFFFF
        if not self._allowed_pair((c, c)):
            return False
        self.result = (c, c)
        remember_colour(c)
        self.chosen.emit(self._target, c)
        self._close()
        return True

    def _no_fill(self, on: bool) -> None:
        self.noFillToggled.emit(bool(on))
        self._close()

    def _auto(self, on: bool) -> None:
        self.autoToggled.emit(bool(on))
        self._close()


def _chip(colour: int, size: int = 12) -> QtGui.QPixmap:
    from .colour_picker import swatch_pixmap

    c = int(colour) & 0xFFFFFF
    return swatch_pixmap(c, c, size, size)


class NewColoursRow(QtWidgets.QWidget):
    """Settings > Editing > New objects: 'Colours of new objects:' (Contrast with the page /
    The colours I picked), the fill, line and text colours and No fill. ``changed`` after
    any of them; :meth:`choices` gives the settings."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(_sp(1))
        top = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        for value, text in DS.COLOUR_MODES:
            self.mode.addItem(text, value)
        self.mode.setToolTip(
            "Contrast with the page: dark lines and text on a light page, light ones on a dark page"
        )
        top.addWidget(self.mode)
        top.addStretch(1)
        outer.addLayout(top)
        lay = QtWidgets.QHBoxLayout()
        lay.setSpacing(_sp(1))
        outer.addLayout(lay)
        self.buttons: Dict[str, ColourButton] = {}
        for key, text in (("fill", "Fill"), ("line", "Line"), ("text", "Text")):
            lab = QtWidgets.QLabel(text)
            b = ColourButton(0, title=TITLES[key].capitalize(), flash=False, transparent=False, size=(36, 22))
            lab.setBuddy(b)
            lay.addWidget(lab)
            lay.addWidget(b)
            self.buttons[key] = b
            b.changed.connect(self._picked)
        self.no_fill = QtWidgets.QCheckBox("No fill")
        self.no_fill.setToolTip("New shapes are drawn hollow")
        lay.addWidget(self.no_fill)
        lay.addStretch(1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.no_fill.toggled.connect(lambda _on: self.changed.emit())
        self.load()

    def _picked(self) -> None:
        # (a colour picked here is a colour of the user's own, as on the ribbon)
        k = self.mode.findData("fixed")
        if self.mode.currentIndex() != k:
            self.mode.setCurrentIndex(k)
        self.changed.emit()

    def _mode_changed(self, _i) -> None:
        self.changed.emit()

    def load(self) -> None:
        """Show the stored colours (the light-page ones while they follow the page)."""
        from .prefs.settings import app_settings

        st = app_settings()
        blocked = self.blockSignals(True)
        try:
            self.mode.setCurrentIndex(max(0, self.mode.findData(DS.colour_mode())))
            light = dict(zip(("fill", "line", "text"), DS.LIGHT_PAGE))
            for key, b in self.buttons.items():
                v = DS.packed(st.value(DS.KEYS[key], None))
                c = light[key] if v is None else v
                b.set_pair((c, c))
            self.no_fill.setChecked(DS.no_fill())
        finally:
            self.blockSignals(blocked)

    def defaults(self) -> None:
        blocked = self.blockSignals(True)
        try:
            self.mode.setCurrentIndex(max(0, self.mode.findData("auto")))
            for key, c in zip(("fill", "line", "text"), DS.LIGHT_PAGE):
                self.buttons[key].set_pair((c, c))
            self.no_fill.setChecked(False)
        finally:
            self.blockSignals(blocked)

    def choices(self) -> Dict:
        out = {DS.COLOUR_MODE_KEY: self.mode.currentData(), DS.NO_FILL_KEY: self.no_fill.isChecked()}
        for key, b in self.buttons.items():
            pair = b.pair()
            if pair is not None:
                out[DS.KEYS[key]] = int(pair[0]) & 0xFFFFFF
        return out

    def state(self) -> tuple:
        """The three colours (the Settings window's Undo Changes and Reset to Default put
        them back; the mode and No fill are the row's own controls)."""
        return tuple(None if b.pair() is None else int(b.pair()[0]) & 0xFFFFFF for b in self.buttons.values())

    def set_state(self, state) -> None:
        blocked = self.blockSignals(True)
        try:
            for b, c in zip(self.buttons.values(), state or ()):
                if c is not None:
                    b.set_pair((int(c), int(c)))
        finally:
            self.blockSignals(blocked)
