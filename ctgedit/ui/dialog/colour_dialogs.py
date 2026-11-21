"""The colour dialogs under Tools > Colours."""

from __future__ import annotations
from ..style.theme import sp as _sp

import math
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import colourpalette as CP
from ...format import colourtools as T
from ..colour_picker import (
    ColourButton,
    EyeDropper,
    PaletteGrid,
    as_pair,
    paint_colour,
    parse_colour_text,
    recent_colours,
    swatch_pixmap,
)
from ..prefs.settings import app_settings

Pair = Tuple[int, int]


# --------------------------------------------------------------------------- small widgets
class ColourSample(QtWidgets.QWidget):
    """CtDraw's ColorSample: the colour pair the dialog makes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pair: Pair = (0, 0)
        self.setFixedSize(62, 32)

    def set_pair(self, pair: Pair) -> None:
        self._pair = pair
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        paint_colour(p, self.rect(), self._pair[0], self._pair[1], border=QtGui.QColor("#555555"))
        p.end()


class ColourWheel(QtWidgets.QWidget):
    """The colour wheel: hue round the circle (0 = red on the right, 180 =
    cyan on the left, anticlockwise), saturation growing to the edge,
    drawn at mid lightness. ``picked(hue10, sat)`` on a click / drag."""

    picked = QtCore.Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(190, 190)
        self._hs = (0, 0)
        self._img: Optional[QtGui.QImage] = None

    def set_hs(self, h10: int, sat: int) -> None:
        self._hs = (h10, sat)
        self.update()

    def _radius(self) -> float:
        return min(self.width(), self.height()) / 2.0 - 4

    def _image(self) -> QtGui.QImage:
        if self._img is None:
            img = QtGui.QImage(self.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            c = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
            r = self._radius()
            con = QtGui.QConicalGradient(c, 0)
            for k in range(0, 361, 30):
                rr, gg, bb = T.hls_to_rgb((k % 360) * 10, 128, 255)
                con.setColorAt(k / 360.0, QtGui.QColor(rr, gg, bb))
            p.setBrush(QtGui.QBrush(con))
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(c, r, r)
            rad = QtGui.QRadialGradient(c, r)
            rad.setColorAt(0.0, QtGui.QColor(128, 128, 128, 255))
            rad.setColorAt(1.0, QtGui.QColor(128, 128, 128, 0))
            p.setBrush(QtGui.QBrush(rad))
            p.drawEllipse(c, r, r)
            p.end()
            self._img = img
        return self._img

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.drawImage(0, 0, self._image())
        h10, sat = self._hs
        r = self._radius() * sat / 255.0
        a = math.radians(h10 / 10.0)
        x = self.width() / 2.0 + r * math.cos(a)
        y = self.height() / 2.0 - r * math.sin(a)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        for colour, width in (("#000000", 3), ("#ffffff", 1)):
            p.setPen(QtGui.QPen(QtGui.QColor(colour), width))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(QtCore.QPointF(x, y), 4, 4)
        p.end()

    def hs_at(self, pos: QtCore.QPointF) -> Tuple[int, int]:
        dx = pos.x() - self.width() / 2.0
        dy = self.height() / 2.0 - pos.y()
        deg = math.degrees(math.atan2(dy, dx)) % 360.0
        sat = min(255, int(round(math.hypot(dx, dy) / self._radius() * 255)))
        return int(round(deg)) % 360 * 10, sat

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == QtCore.Qt.LeftButton:
            self.picked.emit(*self.hs_at(ev.position()))

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if ev.buttons() & QtCore.Qt.LeftButton:
            self.picked.emit(*self.hs_at(ev.position()))


class BrightnessBar(QtWidgets.QWidget):
    """The brightness bar: the current hue and saturation from black
    (bottom) to white (top). ``picked(lum)`` on a click / drag."""

    picked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 190)
        self._hls = (0, 128, 0)

    def set_hls(self, h10: int, lum: int, sat: int) -> None:
        self._hls = (h10, lum, sat)
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        h10, lum, sat = self._hls
        bar = self.rect().adjusted(4, 4, -8, -4)
        n = max(1, bar.height())
        for k in range(n):
            lv = 255 - (k * 255) // max(1, n - 1)
            r, g, b = T.hls_to_rgb(h10, lv, sat)
            p.fillRect(QtCore.QRect(bar.left(), bar.top() + k, bar.width(), 1), QtGui.QColor(r, g, b))
        p.setPen(QtGui.QPen(QtGui.QColor("#555555"), 1))
        p.drawRect(bar.adjusted(0, 0, -1, -1))
        y = bar.top() + (255 - lum) * (n - 1) // 255
        p.setPen(QtCore.Qt.NoPen)
        from ..style.theme import color

        p.setBrush(color("text"))
        tri = QtGui.QPolygon(
            [
                QtCore.QPoint(bar.right() + 2, y),
                QtCore.QPoint(bar.right() + 7, y - 4),
                QtCore.QPoint(bar.right() + 7, y + 4),
            ]
        )
        p.drawPolygon(tri)
        p.end()

    def lum_at(self, y: float) -> int:
        bar = self.rect().adjusted(4, 4, -8, -4)
        n = max(1, bar.height() - 1)
        return max(0, min(255, int(round(255 - (y - bar.top()) * 255.0 / n))))

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == QtCore.Qt.LeftButton:
            self.picked.emit(self.lum_at(ev.position().y()))

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if ev.buttons() & QtCore.Qt.LeftButton:
            self.picked.emit(self.lum_at(ev.position().y()))


class _Strip(QtWidgets.QWidget):
    """A bar of colour segments (the Swap range ramp / the Adjust hue bar);
    greyed out (COLOR_GRAYTEXT) while disabled, as CtDraw draws it."""

    def __init__(self, parent=None, height: int = 16):
        super().__init__(parent)
        self._colours: list = []
        self.setMinimumWidth(180)
        self.setFixedHeight(height)

    def set_colours(self, colours) -> None:
        self._colours = list(colours)
        self.update()

    def colours(self) -> list:
        return list(self._colours)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        r = self.rect()
        if not self.isEnabled() or not self._colours:
            p.fillRect(r, self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text))
            p.end()
            return
        n = len(self._colours)
        for k, c in enumerate(self._colours):
            x0 = r.left() + (k * r.width()) // n
            x1 = r.left() + ((k + 1) * r.width()) // n
            p.fillRect(
                QtCore.QRect(x0, r.top(), max(1, x1 - x0), r.height()),
                QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF),
            )
        p.end()


# --------------------------------------------------------------------------- Edit Favorite Colors
class FavoriteColorsDialog(QtWidgets.QDialog):
    """Tools > Edit Favorite Colors... (help 1172092) on
    a COPY of the project palette (format.colourpalette.Palette): the caller
    saves :meth:`palette_` when :meth:`palette_changed`, and uses
    :meth:`result_pair` -- the colour the dialog shows at OK."""

    #: a palette grid and a colour wheel of fixed sizes (UI guide section 9)
    RESIZE = "none"

    def __init__(self, palette: CP.Palette, parent=None, current: Optional[Pair] = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Favourite Colours")
        self._pal = palette.copy()
        self._orig = list(palette.entries)
        self._pair = list(current) if current is not None else [0x000000, 0x000000]
        self._half = 0  # 0 = On State, 1 = Off State
        self._hls = list(T.colour_hls(self._pair[0]))
        self._syncing = False

        outer = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.grid = PaletteGrid(self._pal, self)
        self.grid.activated.connect(self._load_entry)
        left.addWidget(self.grid)
        btns = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("&Add")
        self.btn_replace = QtWidgets.QPushButton("&Replace")
        self.btn_clear = QtWidgets.QPushButton("C&lear")
        self.btn_add.setToolTip("Add the colour to the first empty favourite")
        self.btn_replace.setToolTip("Replace the selected favourite with the colour")
        self.btn_clear.setToolTip("Clear the selected favourite")
        for b in (self.btn_add, self.btn_replace, self.btn_clear):
            btns.addWidget(b)
        left.addLayout(btns)
        nrow = QtWidgets.QHBoxLayout()
        nrow.addWidget(QtWidgets.QLabel("Name:"))
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(127)
        self.name.setToolTip("The name shown in the colour picker")
        nrow.addWidget(self.name, 1)
        left.addLayout(nrow)
        outer.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        top = QtWidgets.QHBoxLayout()
        self.wheel = ColourWheel(self)
        self.bar = BrightnessBar(self)
        top.addWidget(self.wheel)
        top.addWidget(self.bar)
        right.addLayout(top)
        srow = QtWidgets.QHBoxLayout()
        col = QtWidgets.QVBoxLayout()
        col.addWidget(QtWidgets.QLabel("Colour:"))
        self.sample = ColourSample(self)
        col.addWidget(self.sample)
        srow.addLayout(col)
        hexcol = QtWidgets.QVBoxLayout()
        hex_label = QtWidgets.QLabel("Hex:")
        hexcol.addWidget(hex_label)
        hrow = QtWidgets.QHBoxLayout()
        hrow.setSpacing(_sp(1))
        #: the colour as '#RRGGBB' (also takes 'RRGGBB', 'rgb(r,g,b)' and a palette name)
        self.hex = QtWidgets.QLineEdit()
        self.hex.setMaxLength(32)
        self.hex.setMinimumWidth(90)
        self.hex.setPlaceholderText("#RRGGBB")
        self.hex.setToolTip("Type or paste a colour: #3A7BD5, 3A7BD5 or rgb(58, 123, 213). Enter takes it")
        hex_label.setBuddy(self.hex)
        hrow.addWidget(self.hex)
        from ..style.theme import icon

        self.dropper = QtWidgets.QToolButton()
        self.dropper.setIcon(icon("eyedropper"))
        self.dropper.setAccessibleName("Pick from Page")
        self.dropper.setToolTip("Pick from Page: click a colour on the page or the screen. Esc cancels")
        hrow.addWidget(self.dropper)
        hexcol.addLayout(hrow)
        srow.addLayout(hexcol)
        self.transparent = QtWidgets.QPushButton("&Transparent")
        self.transparent.setCheckable(True)
        self.transparent.setToolTip("No colour: what is behind shows through")
        srow.addWidget(self.transparent, 1, QtCore.Qt.AlignBottom)
        right.addLayout(srow)
        # the colours picked last (the colour picker's recent colours)
        self.recent_buttons = []
        recents = [p for p in recent_colours() if not (T.is_clear(p[0]) and T.is_clear(p[1]))]
        if recents:
            rrow = QtWidgets.QHBoxLayout()
            rrow.setSpacing(_sp(0))
            rrow.addWidget(QtWidgets.QLabel("Recent:"))
            for pair in recents[:8]:
                b = QtWidgets.QToolButton()
                b.setAutoRaise(True)
                b.setIcon(QtGui.QIcon(swatch_pixmap(pair[0], pair[1], 14, 14)))
                b.setIconSize(QtCore.QSize(14, 14))
                from ..colour_picker import describe

                b.setToolTip(describe(*pair))
                b.clicked.connect(lambda _=False, pr=pair: self._set_pair(pr))
                rrow.addWidget(b)
                self.recent_buttons.append(b)
            rrow.addStretch(1)
            right.addLayout(rrow)
        frow = QtWidgets.QHBoxLayout()
        self.flashing = QtWidgets.QCheckBox("&Flashing")
        self.flashing.setToolTip("Alternates between the On State and Off State colours at runtime")
        self.on_state = QtWidgets.QRadioButton("On State")
        self.off_state = QtWidgets.QRadioButton("Off State")
        self.on_state.setChecked(True)
        frow.addWidget(self.flashing)
        frow.addWidget(self.on_state)
        frow.addWidget(self.off_state)
        frow.addStretch(1)
        right.addLayout(frow)
        fields = QtWidgets.QGridLayout()
        self.spins = {}
        for row, (label, key, hi) in enumerate((("Hue:", "h", 359), ("Lum:", "l", 255), ("Sat:", "s", 255))):
            fields.addWidget(QtWidgets.QLabel(label), row, 0)
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, hi)
            fields.addWidget(sp, row, 1)
            self.spins[key] = sp
        for row, (label, key) in enumerate((("Red:", "r"), ("Green:", "g"), ("Blue:", "b"))):
            fields.addWidget(QtWidgets.QLabel(label), row, 2)
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, 255)
            fields.addWidget(sp, row, 3)
            self.spins[key] = sp
        right.addLayout(fields)
        outer.addLayout(right)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(outer)
        from ..style import theme

        self.note = theme.note_label("", parent=self)
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.btn_add.clicked.connect(self.add)
        self.btn_replace.clicked.connect(self.replace)
        self.btn_clear.clicked.connect(self.clear)
        self.transparent.toggled.connect(self._on_transparent)
        self.flashing.toggled.connect(self._on_flashing)
        self.on_state.toggled.connect(lambda on: self._set_half(0 if on else 1))
        for key in ("h", "l", "s"):
            self.spins[key].valueChanged.connect(self._on_hls_field)
        for key in ("r", "g", "b"):
            self.spins[key].valueChanged.connect(self._on_rgb_field)
        self.wheel.picked.connect(self._on_wheel)
        self.bar.picked.connect(self._on_bar)
        self.hex.returnPressed.connect(self._on_hex)
        self.hex.editingFinished.connect(self._on_hex)
        self.dropper.clicked.connect(self.start_dropper)
        self._dropper = None

        if palette.source != "project":
            self.note.setText(
                "This project has no palette file yet. Saving the favourites creates its citect.pal."
                if palette.target_path()
                else "This page is not in a project. The favourites are kept in the ctgedit settings."
            )
        else:
            self.note.setText("Favourites are saved in %s." % palette.path)
        i = self._pal.index_of(*self._pair) if current is not None else -1
        self.grid.set_selected(i)
        if i >= 0:
            self.name.setText(self._pal[i].name)
        self._set_pair(self._pair)
        self._update_buttons()

    # -- state ------------------------------------------------------------------------
    def palette_(self) -> CP.Palette:
        return self._pal

    def palette_changed(self) -> bool:
        return list(self._pal.entries) != self._orig

    def result_pair(self) -> Pair:
        return int(self._pair[0]) & 0xFFFFFFFF, int(self._pair[1]) & 0xFFFFFFFF

    def selected_index(self) -> int:
        return self.grid.selected()

    def _set_pair(self, pair, flashing: Optional[bool] = None) -> None:
        """Show a colour pair in every control (no feedback loop);
        ``flashing`` = keep the Flashing box so (default: the halves differ)."""
        on, off = CP.verify(*pair)
        self._pair = [on, off]
        clear = T.is_clear(on) and T.is_clear(off)
        if not clear:
            self._last_colour = (on, off)
        self._syncing = True
        try:
            self.transparent.setChecked(clear)
            self.flashing.setChecked((on != off if flashing is None else flashing) and not clear)
            self.flashing.setEnabled(not clear)
            flashing = self.flashing.isChecked()
            self.on_state.setEnabled(flashing)
            self.off_state.setEnabled(flashing)
            if not flashing:
                self._half = 0
                self.on_state.setChecked(True)
            self._load_half()
        finally:
            self._syncing = False
        self.sample.set_pair((on, off))

    def _load_half(self) -> None:
        c = self._pair[self._half]
        clear = T.is_clear(c)
        for sp in self.spins.values():
            sp.setEnabled(not clear)
        self.wheel.setEnabled(not clear)
        self.bar.setEnabled(not clear)
        self.hex.setEnabled(not clear)
        if clear:
            self.hex.setText("")
            return
        self._hls = list(T.colour_hls(c))
        self._put_fields()

    def _put_fields(self) -> None:
        was = self._syncing
        self._syncing = True
        try:
            h10, lum, sat = self._hls
            c = self._pair[self._half]
            self.spins["h"].setValue(int(round(h10 / 10.0)) % 360)
            self.spins["l"].setValue(lum)
            self.spins["s"].setValue(sat)
            self.spins["r"].setValue((c >> 16) & 0xFF)
            self.spins["g"].setValue((c >> 8) & 0xFF)
            self.spins["b"].setValue(c & 0xFF)
            self.wheel.set_hs(h10, sat)
            self.bar.set_hls(h10, lum, sat)
            if not self.hex.hasFocus() or not self.hex.isModified():
                self.hex.setText("#%06X" % (c & 0xFFFFFF))
                self.hex.setModified(False)
        finally:
            self._syncing = was

    def _put_colour(self, c: int) -> None:
        self._pair[self._half] = c & 0x00FFFFFF
        if not self.flashing.isChecked():
            self._pair[1 - self._half] = self._pair[self._half]
        self.sample.set_pair(tuple(self._pair))
        self._put_fields()

    def _set_half(self, half: int) -> None:
        if self._syncing:
            return
        self._half = half
        self._syncing = True
        try:
            self._load_half()
        finally:
            self._syncing = False

    # -- field edits ------------------------------------------------------------------------
    def set_hls(self, h10: int, lum: int, sat: int) -> None:
        """Hue (tenths) / Lum / Sat for the half being edited (the RGB follows
        through CtDraw's HLS to RGB arithmetic)."""
        self._hls = [h10 % 3600, max(0, min(255, lum)), max(0, min(255, sat))]
        self._put_colour(T.with_hls(0, *self._hls))

    def set_rgb(self, r: int, g: int, b: int) -> None:
        c = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
        self._hls = list(T.colour_hls(c))
        self._put_colour(c)

    def _on_hls_field(self, _v=None) -> None:
        if self._syncing:
            return
        self.set_hls(self.spins["h"].value() * 10, self.spins["l"].value(), self.spins["s"].value())

    def _on_rgb_field(self, _v=None) -> None:
        if self._syncing:
            return
        self.set_rgb(self.spins["r"].value(), self.spins["g"].value(), self.spins["b"].value())

    def _on_hex(self) -> bool:
        """The colour typed in Hex (Enter or leaving the field)."""
        from ..style.theme import mark_invalid

        if self._syncing or not self.hex.isModified():
            return False
        text = self.hex.text().strip()
        pair = parse_colour_text(text, self._pal)
        if pair is None or T.is_clear(pair[0]):
            mark_invalid(self.hex, "Type a colour as #RRGGBB, RRGGBB or rgb(r, g, b).")
            return False
        mark_invalid(self.hex, "")
        self.hex.setModified(False)
        if pair[0] != pair[1]:
            self._set_pair(pair)  # (a flashing favourite named)
            return True
        c = pair[0] & 0xFFFFFF
        self.set_rgb((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
        return True

    def start_dropper(self) -> None:
        """Take the colour of the next click: the page as it is drawn, else the screen."""
        self._dropper = EyeDropper(self)
        self._dropper.sampled.connect(self._sampled)
        self._dropper.start()

    def _sampled(self, colour: int) -> None:
        c = int(colour) & 0xFFFFFF
        if self.transparent.isChecked():
            self.transparent.setChecked(False)
        self.set_rgb((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

    def _on_wheel(self, h10: int, sat: int) -> None:
        self.set_hls(h10, self._hls[1], sat)

    def _on_bar(self, lum: int) -> None:
        self.set_hls(self._hls[0], lum, self._hls[2])

    def _on_transparent(self, on: bool) -> None:
        if self._syncing:
            return
        if on:
            self._set_pair((T.TRANSPARENT, T.TRANSPARENT))
        else:
            self._set_pair(getattr(self, "_last_colour", (0x000000, 0x000000)))

    def _on_flashing(self, on: bool) -> None:
        if self._syncing:
            return
        if not on:
            self._pair[1] = self._pair[0]
        self._set_pair(tuple(self._pair), flashing=on)
        if on:
            self.off_state.setChecked(True)  # the second colour is what to pick next

    def set_flashing(self, on: bool) -> None:
        self.flashing.setChecked(on)

    # -- the grid ---------------------------------------------------------------------------
    def _load_entry(self, index: int) -> None:
        e = self._pal[index]
        if e.used:
            self._set_pair(e.pair)
            self.name.setText(e.name)
        else:
            self.name.clear()
        self._update_buttons()

    def _update_buttons(self) -> None:
        i = self.grid.selected()
        favourite = 0 <= i < CP.SIZE and not self._pal.is_standard(i)
        self.btn_replace.setEnabled(favourite)
        self.btn_clear.setEnabled(favourite and self._pal[i].used)

    def _refused(self, exc: Exception) -> None:
        QtWidgets.QMessageBox.warning(self, "Edit Favourite Colours", str(exc))

    def add(self) -> int:
        """Add the colour shown as a new favorite (-1 when refused)."""
        try:
            i = self._pal.add(self._pair[0], self._pair[1], self.name.text())
        except CP.PaletteError as exc:
            self._refused(exc)
            return -1
        self.grid.set_selected(i)
        self.name.setText(self._pal[i].name)
        self.grid.update()
        self._update_buttons()
        return i

    def replace(self) -> bool:
        i = self.grid.selected()
        try:
            self._pal.replace(i, self._pair[0], self._pair[1], self.name.text())
        except CP.PaletteError as exc:
            self._refused(exc)
            return False
        self.name.setText(self._pal[i].name)
        self.grid.update()
        self._update_buttons()
        return True

    def clear(self) -> bool:
        i = self.grid.selected()
        try:
            self._pal.clear(i)
        except CP.PaletteError as exc:
            self._refused(exc)
            return False
        self.name.clear()
        self.grid.update()
        self._update_buttons()
        return True


# --------------------------------------------------------------------------- Swap Colors
def _load_pair(st, key: str, default: Pair) -> Pair:
    v = st.value(key, None)
    try:
        a, _, b = str(v).partition(",")
        return int(a, 16) & 0xFFFFFFFF, int(b or a, 16) & 0xFFFFFFFF
    except TypeError, ValueError:
        return default


# --------------------------------------------------------------------------- Apply to
class _ApplyTo(QtCore.QObject):
    """The 'Apply to' choice of Swap / Adjust Colours: the selection (n
    objects) or the whole page. With nothing selected only the whole page;
    when the selection comes back, so does 'Selection' (unless the user
    chose the whole page)."""

    def __init__(self, dialog: QtWidgets.QDialog, count_of=None):
        super().__init__(dialog)
        self._count_of = count_of
        self._n = 0
        self._auto = True  # the whole page was not chosen by the user
        self._syncing = False
        self.box = QtWidgets.QGroupBox("Apply to", dialog)
        row = QtWidgets.QHBoxLayout(self.box)
        self.selection = QtWidgets.QRadioButton("Selection", self.box)
        self.page = QtWidgets.QRadioButton("Whole page", self.box)
        self.page.setToolTip(
            "Every object on the page. Locked objects, template objects "
            "and the objects of genies and symbols keep their colours."
        )
        row.addWidget(self.selection)
        row.addWidget(self.page)
        row.addStretch(1)
        self.selection.setChecked(True)
        self.page.toggled.connect(self._user_choice)
        self._show()

    def _user_choice(self, on: bool) -> None:
        if not self._syncing:
            self._auto = not on

    def set_selection(self, doc, indices) -> None:
        indices = list(indices or [])
        if doc is None:
            self._n = 0
        elif self._count_of is not None:
            self._n = int(self._count_of(doc, indices))
        else:
            self._n = len(indices)
        self._show()

    def _show(self) -> None:
        self._syncing = True
        try:
            self.selection.setText("Selection (%d object%s)" % (self._n, "" if self._n == 1 else "s"))
            self.selection.setEnabled(self._n > 0)
            if self._n == 0:
                self.page.setChecked(True)
            elif self._auto:
                self.selection.setChecked(True)
        finally:
            self._syncing = False

    def scope(self) -> str:
        """'selection' or 'page'."""
        return "selection" if self.selection.isChecked() and self._n > 0 else "page"

    def count(self) -> int:
        return self._n


class _UseSwatch(QtWidgets.QToolButton):
    """A colour in use (Swap Colours): a click sets From, Shift+click sets To."""

    def __init__(self, pair: Pair, count: int, parent=None):
        super().__init__(parent)
        self.pair_ = (int(pair[0]) & 0xFFFFFFFF, int(pair[1]) & 0xFFFFFFFF)
        self.count = int(count)
        self.setObjectName("useSwatch")
        self.setAutoRaise(True)
        self.setIcon(QtGui.QIcon(swatch_pixmap(self.pair_[0], self.pair_[1], 16, 16)))
        self.setIconSize(QtCore.QSize(16, 16))
        self.setFixedSize(20, 20)
        self.setToolTip(
            "%s: %d object%s. Click: From. Shift+click: To"
            % (_rgb_text(self.pair_), self.count, "" if self.count == 1 else "s")
        )
        self.setAccessibleName(_rgb_text(self.pair_))


def _rgb_text(pair: Pair) -> str:
    """'RGB 80,80,80' (a flashing pair: both halves; Transparent by name)."""

    def one(c):
        if T.is_clear(c):
            return "Transparent"
        return "RGB %d,%d,%d" % ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

    a, b = pair
    return one(a) if a == b else "%s flashing with %s" % (one(a), one(b))


_DIALOG_QSS = """
QToolButton#useSwatch { border: 1px solid transparent; border-radius: 2px; padding: 0px; }
QToolButton#useSwatch:hover { border-color: #4da3ff; }
QToolButton#useSwatch[kbdFocus="true"] { border: 2px solid #4da3ff; }
"""


class SwapColorsDialog(QtWidgets.QDialog):
    """Tools > Swap Colors."""

    #: Apply / OK: swap the colours of what 'Apply to' names
    applyRequested = QtCore.Signal()
    #: the most colours the 'Colours in use' row shows
    IN_USE_MAX = 24

    def __init__(self, parent=None, frm=None, count_of=None, colours_of=None):
        super().__init__(parent)
        self.setWindowTitle("Swap Colours")
        self.setStyleSheet(_DIALOG_QSS)
        st = app_settings()
        start_from = as_pair(frm) or _load_pair(st, "colour/swap/from", (0xFF0000, 0xFF0000))
        start_to = _load_pair(st, "colour/swap/to", (0x00FF00, 0x00FF00))
        self._colours_of = colours_of
        self._doc = None
        self._indices: list = []
        from ..style.theme import icon

        lay = QtWidgets.QVBoxLayout(self)
        g1 = QtWidgets.QGroupBox("From")
        v1 = QtWidgets.QVBoxLayout(g1)
        l1 = QtWidgets.QHBoxLayout()
        self._frm = ColourButton(start_from, self, title="Swap from")
        self._frm_drop = self._dropper_button("Pick the From colour from the page")
        self._frm_drop.clicked.connect(lambda: self._pick_from_page(self.set_from))
        self._frm_bar = _Strip(self)
        l1.addWidget(self._frm)
        l1.addWidget(self._frm_drop)
        l1.addWidget(self._frm_bar, 1)
        l1.addStretch(0)
        v1.addLayout(l1)
        self._in_use_label = QtWidgets.QLabel("Colours in use:")
        v1.addWidget(self._in_use_label)
        self._in_use = QtWidgets.QWidget()
        from ..flow_layout import FlowLayout

        self._in_use_lay = FlowLayout(self._in_use, spacing=1)
        v1.addWidget(self._in_use)
        self.in_use_buttons: list = []
        swap_row = QtWidgets.QHBoxLayout()
        self._swap = QtWidgets.QToolButton()
        self._swap.setIcon(icon("swap_vertical"))
        self._swap.setToolTip("Swap From and To")
        self._swap.setAccessibleName("Swap From and To")
        self._swap.clicked.connect(self.swap_from_to)
        swap_row.addSpacing(12)
        swap_row.addWidget(self._swap)
        swap_row.addStretch(1)
        g2 = QtWidgets.QGroupBox("To")
        l2 = QtWidgets.QHBoxLayout(g2)
        self._to = ColourButton(start_to, self, title="Swap to")
        self._to_drop = self._dropper_button("Pick the To colour from the page")
        self._to_drop.clicked.connect(lambda: self._pick_from_page(self.set_to))
        self._to_bar = _Strip(self)
        l2.addWidget(self._to)
        l2.addWidget(self._to_drop)
        l2.addWidget(self._to_bar, 1)
        l2.addStretch(0)
        lay.addWidget(g1)
        lay.addLayout(swap_row)
        lay.addWidget(g2)
        row = QtWidgets.QHBoxLayout()
        self._chk_any = QtWidgets.QCheckBox("From &any colour")
        self._chk_any.setToolTip("Change every colour of the objects to the To colour")
        self._chk_range = QtWidgets.QCheckBox("Swap &range")
        self._chk_range.setToolTip("Change every shade of the From hue to the same shade of the To hue")
        row.addWidget(self._chk_any)
        row.addWidget(self._chk_range)
        row.addStretch(1)
        lay.addLayout(row)
        self._chk_any.setChecked(st.value("colour/swap/any", False, type=bool))
        self._chk_range.setChecked(st.value("colour/swap/range", False, type=bool))
        self.apply_to = _ApplyTo(self, count_of)
        self.apply_to.page.toggled.connect(lambda _on: self._fill_in_use())
        lay.addWidget(self.apply_to.box)
        lay.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(buttons)
        self._frm.changed.connect(self._update)
        self._to.changed.connect(self._update)
        self._chk_any.toggled.connect(lambda _b: self._update())
        self._chk_range.toggled.connect(lambda _b: self._update())
        self._dropper = None
        self._update()
        self._fill_in_use()

    def _dropper_button(self, tip: str) -> QtWidgets.QToolButton:
        from ..style.theme import icon

        b = QtWidgets.QToolButton()
        b.setIcon(icon("eyedropper"))
        b.setToolTip(tip + ". Esc cancels")
        b.setAccessibleName(tip)
        return b

    def _pick_from_page(self, setter) -> None:
        """An eye dropper: the next click's colour (the page as drawn, else the screen)."""
        self._dropper = EyeDropper(self)
        self._dropper.sampled.connect(lambda c, f=setter: f((int(c) & 0xFFFFFF, int(c) & 0xFFFFFF)))
        self._dropper.start()

    def set_from(self, pair) -> None:
        self._frm.set_pair(pair)
        self._update()

    def set_to(self, pair) -> None:
        self._to.set_pair(pair)
        self._update()

    def swap_from_to(self) -> None:
        """From takes To's colour and To takes From's."""
        a, b = self._frm.pair(), self._to.pair()
        self._frm.set_pair(b)
        self._to.set_pair(a)
        self._update()

    def _update(self) -> None:
        """flashing From / To or From any color disable Swap
        range; the ramps show only while it applies; From any greys From."""
        frm, to = self._frm.pair(), self._to.pair()
        flashing = frm[0] != frm[1] or to[0] != to[1]
        any_ = self._chk_any.isChecked()
        self._frm.setEnabled(not any_)
        self._frm_drop.setEnabled(not any_)
        self._chk_range.setEnabled(not any_ and not flashing)
        ramps = self._chk_range.isChecked() and not any_ and not flashing
        shown = self._frm_bar.isVisibleTo(self)
        for bar in (self._frm_bar, self._to_bar):
            bar.setEnabled(ramps)
            # (hidden while Swap range is off: a flat bar read as an empty field)
            bar.setVisible(ramps)
        self._frm_bar.set_colours(T.lightness_ramp(0xFFFFFF if any_ else frm[0], 64))
        self._to_bar.set_colours(T.lightness_ramp(to[0], 64))
        if shown != ramps and self.isVisible():
            QtCore.QTimer.singleShot(0, self, self.adjustSize)

    # -- the colours in use ---------------------------------------------------------------
    def colours_in_use(self) -> list:
        """[(pair, objects)] of the scope, most used first (at most IN_USE_MAX)."""
        if self._colours_of is None or self._doc is None:
            return []
        try:
            got = list(self._colours_of(self._doc, list(self._indices), self.scope()) or ())
        except Exception:  # noqa: BLE001  # swallow-ok: no list of colours, the row stays empty
            _note_exc("colour_dialogs.colours_in_use")
            got = []
        return got[: self.IN_USE_MAX]

    def _fill_in_use(self) -> None:
        while self._in_use_lay.count():
            item = self._in_use_lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.in_use_buttons = []
        got = self.colours_in_use()
        for pair, count in got:
            b = _UseSwatch(pair, count, self._in_use)
            b.clicked.connect(lambda _=False, sw=b: self._in_use_clicked(sw))
            self._in_use_lay.addWidget(b)
            self.in_use_buttons.append(b)
        show = self._colours_of is not None
        self._in_use_label.setVisible(show)
        self._in_use.setVisible(show)
        self._in_use_label.setText("Colours in use:" if got or not show else "Colours in use: none")

    def _in_use_clicked(self, sw: "_UseSwatch") -> None:
        shift = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ShiftModifier)
        self.pick_in_use(sw.pair_, to=shift)

    def pick_in_use(self, pair: Pair, to: bool = False) -> None:
        """A colour in use clicked: it becomes From (``to``: To)."""
        if to:
            self.set_to(pair)
        else:
            if self._chk_any.isChecked():
                self._chk_any.setChecked(False)
            self.set_from(pair)

    def set_selection(self, doc, indices) -> None:
        """The selection changed: what 'Selection' changes."""
        self._doc = doc
        self._indices = list(indices or [])
        self.apply_to.set_selection(doc, indices)
        self._fill_in_use()

    def scope(self) -> str:
        """'selection' or 'page'."""
        return self.apply_to.scope()

    def _remember(self) -> None:
        st = app_settings()
        st.setValue("colour/swap/from", "%08x,%08x" % self._frm.pair())
        st.setValue("colour/swap/to", "%08x,%08x" % self._to.pair())
        st.setValue("colour/swap/any", self._chk_any.isChecked())
        st.setValue("colour/swap/range", self._chk_range.isChecked())

    def _apply(self) -> None:
        self._remember()
        self.applyRequested.emit()
        self._fill_in_use()

    def _ok(self) -> None:
        self._apply()
        self.accept()

    def values(self):
        """(From pair or None for From any color, To pair, swap range)."""
        any_ = self._chk_any.isChecked()
        rng = self._chk_range.isChecked() and self._chk_range.isEnabled()
        return (None if any_ else self._frm.pair(), self._to.pair(), rng)


# --------------------------------------------------------------------------- Adjust Colors
class HueRangeBar(QtWidgets.QWidget):
    """A hue strip with draggable handles."""

    changed = QtCore.Signal(int, int)
    PAD = 7  # room for a handle at either end (px)

    def __init__(self, two: bool = True, parent=None):
        super().__init__(parent)
        self._two = bool(two)
        self._start = 0
        self._end = 0
        self._focus = "start"
        self._drag = None  # ('start' | 'end' | 'band', start, end, x)
        from ..style.theme import px

        self.setMinimumWidth(px(240))
        self.setFixedHeight(px(30))
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setAccessibleName("From hue range" if self._two else "To hue range")
        self._sync_tip()

    # -- values -----------------------------------------------------------------------
    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def set_range(self, start: int, end: int) -> None:
        """Show the range ``start`` .. ``end`` (degrees; end < start wraps past 360)."""
        self._start, self._end = int(start) % 360, int(end) % 360
        self._sync_tip()
        self.update()

    def colours(self) -> list:
        """The strip's 360 colours (colourtools.hue_bar)."""
        return T.hue_bar(self._start, self._end)

    def focused_handle(self) -> str:
        return self._focus

    def set_focused_handle(self, name: str) -> None:
        if name in ("start", "end") and (self._two or name == "start"):
            self._focus = name
            self._sync_tip()
            self.update()

    def _sync_tip(self) -> None:
        if self._two:
            self.setToolTip(
                "From %d° to %d°. Drag a handle or the band; Left and Right "
                "move the chosen handle (Shift: 10°), Tab chooses the other" % (self._start, self._end)
            )
        else:
            self.setToolTip(
                "Starts at %d° (to %d°). Drag the handle; Left and Right move "
                "it (Shift: 10°)" % (self._start, self._end)
            )
        self.setAccessibleDescription(self.toolTip())

    # -- geometry ---------------------------------------------------------------------
    def _strip(self) -> QtCore.QRectF:
        h = max(10.0, self.height() * 0.47)
        return QtCore.QRectF(self.PAD, 2, max(1, self.width() - 2 * self.PAD), h)

    def x_of(self, deg: float) -> float:
        r = self._strip()
        return r.left() + (float(deg) % 360.0) / 359.0 * r.width()

    def deg_at(self, x: float) -> int:
        r = self._strip()
        return int(round(max(0.0, min(1.0, (x - r.left()) / r.width())) * 359))

    def _in_band(self, deg: int) -> bool:
        if self._start <= self._end:
            return self._start <= deg <= self._end
        return deg >= self._start or deg <= self._end

    # -- painting -----------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt API
        from ..style.theme import color, has_keyboard_focus

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self._strip()
        cols = self.colours()
        n = len(cols)
        for k, c in enumerate(cols):
            x0 = r.left() + k * r.width() / n
            x1 = r.left() + (k + 1) * r.width() / n
            p.fillRect(
                QtCore.QRectF(x0, r.top(), x1 - x0 + 0.6, r.height()),
                QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF),
            )
        p.setPen(QtGui.QPen(color("control_edge"), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(r)
        # the band: a frame over the range (two pieces when it wraps)
        p.setPen(QtGui.QPen(color("text"), 1.5))
        a, b = self.x_of(self._start), self.x_of(self._end)
        if self._start <= self._end:
            p.drawRect(QtCore.QRectF(a, r.top() - 1, max(1.0, b - a), r.height() + 2))
        else:
            p.drawRect(QtCore.QRectF(a, r.top() - 1, r.right() - a, r.height() + 2))
            p.drawRect(QtCore.QRectF(r.left(), r.top() - 1, b - r.left(), r.height() + 2))
        handles = ("start", "end") if self._two else ("start",)
        for name in handles:
            x = self.x_of(self._start if name == "start" else self._end)
            hs = max(6.0, (self.height() - r.bottom() - 3) * 0.75)
            tri = QtGui.QPolygonF(
                [
                    QtCore.QPointF(x, r.bottom() + 2),
                    QtCore.QPointF(x - hs * 0.7, r.bottom() + 2 + hs),
                    QtCore.QPointF(x + hs * 0.7, r.bottom() + 2 + hs),
                ]
            )
            focused = name == self._focus and self.hasFocus()
            p.setBrush(color("accent") if focused else color("text"))
            p.setPen(QtGui.QPen(color("bg"), 1))
            p.drawPolygon(tri)
        if has_keyboard_focus(self):
            p.setPen(QtGui.QPen(color("accent"), 1, QtCore.Qt.DotLine))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()

    # -- the mouse ----------------------------------------------------------------------
    def _handle_at(self, x: float):
        best, dist = None, 7.0
        for name in ("start", "end") if self._two else ("start",):
            d = abs(x - self.x_of(self._start if name == "start" else self._end))
            if d <= dist:
                best, dist = name, d
        return best

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(e)
        x = e.position().x()
        name = self._handle_at(x)
        deg = self.deg_at(x)
        if name is None and self._two and self._in_band(deg):
            name = "band"
        if name is None:
            # a click outside: the nearest handle goes there
            name = "start"
            if self._two and abs(deg - self._end) < abs(deg - self._start):
                name = "end"
            self._move(name, deg)
        if name in ("start", "end"):
            self.set_focused_handle(name)
        self._drag = (name, self._start, self._end, x)
        e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if self._drag is None or not e.buttons() & QtCore.Qt.LeftButton:
            return super().mouseMoveEvent(e)
        name, s0, e0, x0 = self._drag
        if name == "band":
            r = self._strip()
            delta = int(round((e.position().x() - x0) / r.width() * 359))
            self._set(s0 + delta, e0 + delta)
        else:
            self._move(name, self.deg_at(e.position().x()))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self._drag = None
        super().mouseReleaseEvent(e)

    def _move(self, name: str, deg: int) -> None:
        if name == "start":
            if self._two:
                self._set(deg, self._end)
            else:
                length = (self._end - self._start) % 360
                self._set(deg, deg + length)
        else:
            self._set(self._start, deg)

    def _set(self, start: int, end: int) -> None:
        start, end = int(start) % 360, int(end) % 360
        if (start, end) == (self._start, self._end):
            return
        self._start, self._end = start, end
        self._sync_tip()
        self.update()
        self.changed.emit(start, end)

    # -- the keys -----------------------------------------------------------------------
    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        k = e.key()
        if k in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            step = 10 if e.modifiers() & QtCore.Qt.ShiftModifier else 1
            step = -step if k == QtCore.Qt.Key_Left else step
            cur = self._start if self._focus == "start" else self._end
            self._move(self._focus, cur + step)
            e.accept()
            return
        super().keyPressEvent(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:  # noqa: N802 -- Qt API
        # Tab goes to the end handle before it leaves the bar, Shift+Tab back
        if self._two and nxt and self._focus == "start":
            self.set_focused_handle("end")
            return True
        if self._two and not nxt and self._focus == "end":
            self.set_focused_handle("start")
            return True
        return super().focusNextPrevChild(nxt)

    def focusInEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.reason() == QtCore.Qt.TabFocusReason:
            self._focus = "start"
        elif e.reason() == QtCore.Qt.BacktabFocusReason and self._two:
            self._focus = "end"
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self.update()
        super().focusOutEvent(e)


class _BeforeAfter(QtWidgets.QWidget):
    """Up to 16 colours of the scope, each as it is (top) and as the adjustment makes it
    (bottom)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pairs: list = []
        self.setFixedHeight(34)
        self.setMinimumWidth(240)

    def set_colours(self, before: list, after: list) -> None:
        self._pairs = list(zip(before, after))[:16]
        self.setToolTip(
            "\n".join("#%06X to #%06X" % (a & 0xFFFFFF, b & 0xFFFFFF) for a, b in self._pairs[:8])
        )
        self.update()

    def pairs(self) -> list:
        return list(self._pairs)

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt API
        from ..style.theme import color

        p = QtGui.QPainter(self)
        if not self._pairs:
            p.setPen(QtGui.QPen(color("text_dim")))
            p.drawText(self.rect(), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "No colours in this scope")
            p.end()
            return
        w = min(28.0, (self.width() - 2) / max(1, len(self._pairs)))
        for k, (a, b) in enumerate(self._pairs):
            x = 1 + k * w
            for y, c in ((1, a), (17, b)):
                rr = QtCore.QRectF(x, y, w - 2, 15)
                p.fillRect(rr, QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))
                p.setPen(QtGui.QPen(QtGui.QColor("#555555"), 1))
                p.drawRect(rr)
        p.end()


class AdjustColorsDialog(QtWidgets.QDialog):
    """Tools > Adjust Colors."""

    #: Apply / OK: adjust the colours of what 'Apply to' names
    applyRequested = QtCore.Signal()

    def __init__(self, parent=None, count_of=None, colours_of=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust Colours")
        st = app_settings()
        self._syncing = False
        self._colours_of = colours_of
        self._doc = None
        self._indices: list = []
        self._start = st.value("colour/adjust/hue_from", 0, type=int) % 360
        self._range = st.value("colour/adjust/hue_range", 0, type=int) % 360
        self._to = st.value("colour/adjust/hue_to", 0, type=int) % 360
        lay = QtWidgets.QVBoxLayout(self)

        hue = QtWidgets.QGroupBox("Hue")
        hl = QtWidgets.QGridLayout(hue)

        def spin(enabled: bool = True) -> QtWidgets.QSpinBox:
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, 359)
            sp.setSuffix(" °")
            sp.setWrapping(True)
            sp.setEnabled(enabled)
            return sp

        hl.addWidget(QtWidgets.QLabel("From hue range:"), 0, 0, 1, 4)
        self._from_bar = HueRangeBar(True, self)
        hl.addWidget(self._from_bar, 1, 0, 1, 4)
        self._sp_from = spin()
        self._sp_from.setToolTip("Where the From range starts")
        self._sp_end = spin()
        self._sp_end.setToolTip("Where the From range ends")
        hl.addWidget(self._sp_from, 2, 0)
        hl.addWidget(QtWidgets.QLabel("to"), 2, 1)
        hl.addWidget(self._sp_end, 2, 2)
        hl.addWidget(QtWidgets.QLabel("To hue range:"), 3, 0, 1, 4)
        self._to_bar = HueRangeBar(False, self)
        hl.addWidget(self._to_bar, 4, 0, 1, 4)
        self._sp_to = spin()
        self._sp_to.setToolTip("Where the To range starts")
        self._to_end = spin(enabled=False)
        self._to_end.setToolTip("The end of the To range: as long as the From range")
        hl.addWidget(self._sp_to, 5, 0)
        hl.addWidget(QtWidgets.QLabel("to"), 5, 1)
        hl.addWidget(self._to_end, 5, 2)
        hl.setColumnStretch(3, 1)
        lay.addWidget(hue)

        def percent_row(title, key_val, key_sel):
            box = QtWidgets.QGroupBox(title)
            row = QtWidgets.QHBoxLayout(box)
            sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sl.setRange(-100, 100)
            sp = QtWidgets.QSpinBox()
            sp.setRange(-100, 100)
            sp.setSuffix(" %")
            chk = QtWidgets.QCheckBox("Selected hues only")
            chk.setToolTip("Change only the colours whose hue is in the From range")
            row.addWidget(sl, 1)
            row.addWidget(sp)
            row.addWidget(chk)
            sl.valueChanged.connect(sp.setValue)
            sp.valueChanged.connect(sl.setValue)
            sp.setValue(max(-100, min(100, st.value(key_val, 0, type=int))))
            chk.setChecked(st.value(key_sel, False, type=bool))
            lay.addWidget(box)
            return sp, chk

        self._sp_light, self._chk_light = percent_row(
            "Lightness", "colour/adjust/lightness", "colour/adjust/light_selected"
        )
        self._sp_sat, self._chk_sat = percent_row(
            "Saturation", "colour/adjust/saturation", "colour/adjust/sat_selected"
        )
        self._preview_box = QtWidgets.QGroupBox("Before / after")
        pl = QtWidgets.QVBoxLayout(self._preview_box)
        self.preview = _BeforeAfter(self)
        pl.addWidget(self.preview)
        lay.addWidget(self._preview_box)
        self._preview_box.setVisible(colours_of is not None)
        self.apply_to = _ApplyTo(self, count_of)
        self.apply_to.page.toggled.connect(lambda _on: self._sync_preview())
        lay.addWidget(self.apply_to.box)
        lay.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(buttons)

        self._sp_from.valueChanged.connect(self.set_from_start)
        self._sp_end.valueChanged.connect(self.set_from_end)
        self._sp_to.valueChanged.connect(self.set_to_start)
        self._from_bar.changed.connect(self._from_bar_moved)
        self._to_bar.changed.connect(lambda s, _e: self.set_to_start(s))
        for sp in (self._sp_light, self._sp_sat):
            sp.valueChanged.connect(lambda _v: self._sync_preview())
        for chk in (self._chk_light, self._chk_sat):
            chk.toggled.connect(lambda _on: self._sync_preview())
        self._show()

    # -- the hue ranges (CtDraw's rules) ----------------------------------------------------
    def set_from_start(self, deg: int) -> None:
        """the From start moves the range (its length kept)."""
        if self._syncing:
            return
        self._start = int(deg) % 360
        self._show()

    def set_from_end(self, deg: int) -> None:
        """the From end sets the range length."""
        if self._syncing:
            return
        self._range = (int(deg) - self._start + 360) % 360
        self._show()

    def set_to_start(self, deg: int) -> None:
        """the To range starts here, as long as the From range."""
        if self._syncing:
            return
        self._to = int(deg) % 360
        self._show()

    def _from_bar_moved(self, start: int, end: int) -> None:
        """The From bar: a handle sets its end of the range, the band moves both."""
        if self._syncing:
            return
        self._start = int(start) % 360
        self._range = (int(end) - self._start + 360) % 360
        self._show()

    def from_end(self) -> int:
        return (self._start + self._range) % 360

    def to_end(self) -> int:
        return (self._to + self._range) % 360

    def _show(self) -> None:
        self._syncing = True
        try:
            for w, v in (
                (self._sp_from, self._start),
                (self._sp_end, self.from_end()),
                (self._sp_to, self._to),
                (self._to_end, self.to_end()),
            ):
                w.setValue(v)
            self._from_bar.set_range(self._start, self.from_end())
            self._to_bar.set_range(self._to, self.to_end())
        finally:
            self._syncing = False
        self._sync_preview()

    def set_lightness(self, pct: int, selected: bool = False) -> None:
        self._sp_light.setValue(pct)
        self._chk_light.setChecked(selected)

    def set_saturation(self, pct: int, selected: bool = False) -> None:
        self._sp_sat.setValue(pct)
        self._chk_sat.setChecked(selected)

    def adjustment(self) -> T.Adjustment:
        return T.Adjustment.from_dialog(
            self._start,
            self.from_end(),
            self._to,
            self._sp_light.value(),
            self._sp_sat.value(),
            self._chk_light.isChecked(),
            self._chk_sat.isChecked(),
        )

    def values(self):
        """(hue shift, lightness %, saturation %) -- the older call form."""
        return ((self._to - self._start) % 360, self._sp_light.value(), self._sp_sat.value())

    # -- before / after ---------------------------------------------------------------------
    def _sync_preview(self) -> None:
        if not hasattr(self, "preview") or self._colours_of is None or self._doc is None:
            return
        try:
            got = list(self._colours_of(self._doc, list(self._indices), self.scope()) or ())
        except Exception:  # noqa: BLE001  # swallow-ok: no preview of the colours
            _note_exc("colour_dialogs.adjust_preview")
            got = []
        before = [p[0] & 0xFFFFFF for p, _n in got if not T.is_clear(p[0])][:16]
        adj = self.adjustment()
        after = [T.adjust_colour(c, adj) & 0xFFFFFF for c in before]
        self.preview.set_colours(before, after)

    def set_selection(self, doc, indices) -> None:
        """The selection changed: what 'Selection' changes."""
        self._doc = doc
        self._indices = list(indices or [])
        self.apply_to.set_selection(doc, indices)
        self._sync_preview()

    def scope(self) -> str:
        """'selection' or 'page'."""
        return self.apply_to.scope()

    def _remember(self) -> None:
        st = app_settings()
        st.setValue("colour/adjust/hue_from", self._start)
        st.setValue("colour/adjust/hue_range", self._range)
        st.setValue("colour/adjust/hue_to", self._to)
        st.setValue("colour/adjust/lightness", self._sp_light.value())
        st.setValue("colour/adjust/saturation", self._sp_sat.value())
        st.setValue("colour/adjust/light_selected", self._chk_light.isChecked())
        st.setValue("colour/adjust/sat_selected", self._chk_sat.isChecked())

    def _apply(self) -> None:
        self._remember()
        self.applyRequested.emit()
        self._sync_preview()

    def _ok(self) -> None:
        self._apply()
        self.accept()

    @staticmethod
    def transform(colour: int, hue_deg: int, light_pct: int, sat_pct: int) -> int:
        """One 0x00RRGGBB colour through CtDraw's Adjust Colors with every hue
        selected: turned by ``hue_deg``, lightness / saturation changed by
        the percentages (colourtools.adjust_colour)."""
        adj = T.Adjustment(0, 3590, int(hue_deg) * 10, int(sat_pct), int(light_pct))
        return T.adjust_colour(colour & 0xFFFFFF, adj) & 0xFFFFFF


def _note_exc(where: str) -> None:
    from ...infra.logsetup import note_exception

    note_exception(where)
