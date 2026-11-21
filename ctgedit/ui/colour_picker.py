"""The colour picker every colour field opens."""

from __future__ import annotations
from .style.theme import sp as _sp

import os
import re
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..format import colourpalette as CP
from ..format import colourtools as T
from .prefs.settings import app_settings

TRANSPARENT = T.TRANSPARENT
Pair = Tuple[int, int]
RECENT_KEY = "colour/recent"
FAVOURITES_KEY = "colour/favourites"
RECENT_MAX = 16


# --------------------------------------------------------------------------- values
def as_pair(value) -> Optional[Pair]:
    """An int / (on, off) / None as a pair (None stays None)."""
    if value is None:
        return None
    return T.as_pair(value)


def qcolour(c: int) -> Optional[QtGui.QColor]:
    """A stored colour as a QColor; None for the no-colour sentinel."""
    c = int(c) & 0xFFFFFFFF
    if T.is_clear(c):
        return None
    return QtGui.QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)


def describe(on: int, off: Optional[int] = None, name: str = "") -> str:
    """'Name  #RRGGBB' / 'Transparent' / '#RRGGBB flashing with #RRGGBB'."""
    off = on if off is None else off
    if T.is_clear(on) and T.is_clear(off):
        text = "Transparent"
    elif on == off:
        text = "#%06X" % (on & 0xFFFFFF)
    else:
        a = "Transparent" if T.is_clear(on) else "#%06X" % (on & 0xFFFFFF)
        b = "Transparent" if T.is_clear(off) else "#%06X" % (off & 0xFFFFFF)
        text = "%s flashing with %s" % (a, b)
    if name and name.lower() not in (text.lower(), "transparent"):
        return "%s  (%s)" % (name, text)
    return text


_HEX6 = re.compile(r"#?([0-9a-fA-F]{6})")
_HEX3 = re.compile(r"#([0-9a-fA-F]{3})")
_RGB = re.compile(r"(?:rgb\s*\()?\s*(\d{1,3})\s*[, ;]\s*(\d{1,3})\s*[, ;]\s*(\d{1,3})\s*\)?", re.I)


def parse_colour_text(text: str, palette: Optional[CP.Palette] = None) -> Optional[Pair]:
    """What a typed colour means as an (on, off) pair: '#3A7BD5', '3A7BD5', '#38D',
    '58,123,213', 'rgb(58, 123, 213)', 'Transparent' or the name of a colour of
    ``palette`` (a flashing favourite keeps both halves). None when it means none."""
    t = (text or "").strip()
    if not t:
        return None
    m = _HEX6.fullmatch(t)
    if m:
        c = int(m.group(1), 16)
        return c, c
    m = _HEX3.fullmatch(t)
    if m:
        c = int("".join(ch * 2 for ch in m.group(1)), 16)
        return c, c
    m = _RGB.fullmatch(t)
    if m:
        r, g, b = (int(x) for x in m.groups())
        if max(r, g, b) <= 255:
            c = (r << 16) | (g << 8) | b
            return c, c
        return None
    if t.lower() == "transparent":
        return TRANSPARENT, TRANSPARENT
    if palette is not None:
        low = t.lower()
        for e in palette.entries:
            if e.used and e.name and e.name.strip().lower() == low:
                return e.on, e.off
    return None


def colour_name(on: int, off: Optional[int] = None, folder: Optional[str] = None) -> str:
    """The name the project palette gives the colour pair ('' = none)."""
    off = on if off is None else off
    try:
        pal = _named(folder)
    except Exception:  # noqa: BLE001  # swallow-ok: no palette, no name
        return ""
    return pal.get((int(on) & 0xFFFFFFFF, int(off) & 0xFFFFFFFF), "")


def short_text(on: Optional[int], off: Optional[int] = None, folder: Optional[str] = None) -> str:
    """The value a swatch shows beside it: 'Transparent', 'Flashing', the palette's name of
    the colour or '#RRGGBB' ('' for no colour)."""
    if on is None:
        return ""
    on = int(on) & 0xFFFFFFFF
    off = on if off is None else int(off) & 0xFFFFFFFF
    if T.is_clear(on) and T.is_clear(off):
        return "Transparent"
    if on != off:
        return "Flashing"
    if (on >> 24) & 0xFF == 0xFF:
        return ""
    return colour_name(on, off, folder) or "#%06X" % (on & 0xFFFFFF)


#: (palette file stamp) -> {(on, off): name}: the names of a project palette's colours
_NAMES: dict = {}


def _named(folder: Optional[str]) -> dict:
    key = (
        os.path.normcase(os.path.abspath(folder)) if folder else None,
        CP.palette_stamp(folder),
        CP.GENERATION,
    )
    names = _NAMES.get(key)
    if names is None:
        pal = palette_for(folder)
        names = {}
        for e in pal.entries:
            if e.used and e.name:
                names.setdefault((int(e.on) & 0xFFFFFFFF, int(e.off) & 0xFFFFFFFF), e.name)
        if len(_NAMES) > 16:
            _NAMES.clear()
        _NAMES[key] = names
    return names


# --------------------------------------------------------------------------- painting
_CHECK_A = QtGui.QColor("#ffffff")
_CHECK_B = QtGui.QColor("#c8c8c8")


def _checker(p: QtGui.QPainter, r: QtCore.QRect, cell: int = 4) -> None:
    p.fillRect(r, _CHECK_A)
    for y in range(r.top(), r.bottom() + 1, cell):
        for x in range(r.left(), r.right() + 1, cell):
            if ((x - r.left()) // cell + (y - r.top()) // cell) % 2:
                p.fillRect(QtCore.QRect(x, y, cell, cell).intersected(r), _CHECK_B)


def _half(p: QtGui.QPainter, r: QtCore.QRect, c: int, checker: bool, cross: bool) -> None:
    """Fill ``r`` (already clipped to the half) with one colour."""
    q = qcolour(c)
    if q is not None:
        p.fillRect(r, q)
    elif checker:
        _checker(p, r)
    else:
        p.fillRect(r, QtGui.QColor("#ffffff"))


def paint_colour(
    p: QtGui.QPainter,
    rect: QtCore.QRect,
    on: int,
    off: Optional[int] = None,
    *,
    checker: bool = True,
    cross: bool = False,
    border: Optional[QtGui.QColor] = None,
) -> None:
    """A colour pair in ``rect``: a steady colour fills it, a flashing pair is
    split diagonally (on top-left, off bottom-right), Transparent is a
    checkerboard (``checker``) or CtDraw's white cell with a black cross
    (``cross``)."""
    off = on if off is None else off
    p.save()
    if on == off or (T.is_clear(on) and T.is_clear(off)):
        _half(p, rect, on, checker, cross)
    else:
        path_on = QtGui.QPainterPath()
        path_on.moveTo(rect.topLeft())
        path_on.lineTo(QtCore.QPointF(rect.right() + 1, rect.top()))
        path_on.lineTo(QtCore.QPointF(rect.left(), rect.bottom() + 1))
        path_on.closeSubpath()
        p.setClipRect(rect)
        _half(p, rect, off, checker, cross)
        p.setClipPath(path_on, QtCore.Qt.IntersectClip)
        _half(p, rect, on, checker, cross)
        p.setClipping(False)
    if cross and T.is_clear(on) and T.is_clear(off):
        p.setPen(QtGui.QPen(QtGui.QColor("#000000"), 1))
        p.drawLine(rect.topLeft(), rect.bottomRight())
        p.drawLine(rect.bottomLeft(), rect.topRight())
    if border is not None:
        p.setPen(QtGui.QPen(border, 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))
    p.restore()


def swatch_pixmap(on: Optional[int], off: Optional[int] = None, w: int = 36, h: int = 16) -> QtGui.QPixmap:
    """A swatch picture of a colour (None = 'not set': an empty frame)."""
    pm = QtGui.QPixmap(w, h)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    r = QtCore.QRect(0, 0, w, h)
    if on is None:
        from .style.theme import color

        p.setPen(QtGui.QPen(color("text_dim"), 1, QtCore.Qt.DashLine))
        p.drawRect(r.adjusted(0, 0, -1, -1))
    else:
        paint_colour(p, r, on, off, border=QtGui.QColor("#555555"))
    p.end()
    return pm


# --------------------------------------------------------------------------- the project palette
_last_dir: Optional[str] = None


def set_context_dir(folder: Optional[str]) -> None:
    """The project folder of the page being edited (the window sets it when a
    page becomes active): the palette pickers show when no widget ancestor
    names a document."""
    global _last_dir
    _last_dir = folder or None


def context_dir(widget: Optional[QtWidgets.QWidget] = None) -> Optional[str]:
    """The project folder whose palette a picker opened from ``widget`` shows:
    the document of the nearest dialog / window above it, else the active
    page's."""
    w = widget
    while w is not None:
        for attr in ("_doc", "doc"):
            doc = getattr(w, attr, None)
            path = getattr(doc, "path", None) if doc is not None else None
            if isinstance(path, str) and path:
                return os.path.dirname(os.path.abspath(path))
        w = w.parentWidget() if isinstance(w, QtWidgets.QWidget) else None
    return _last_dir


_palettes: dict = {}


def _settings_favourites(pal: CP.Palette) -> None:
    """A palette that is not a project's own: the favourites kept in the
    settings replace its favourite rows (ctgedit's fallback outside a
    project)."""
    st = app_settings()
    rows = st.value(FAVOURITES_KEY, None)
    if rows:
        for k, line in enumerate(list(rows)[: CP.SIZE - CP.STANDARD]):
            try:
                on, off, name = str(line).split(",", 2)
                pal.entries[CP.STANDARD + k] = CP.PaletteColour(int(on, 16), int(off, 16), name)
            except ValueError:
                continue


def palette_for(folder: Optional[str]) -> CP.Palette:
    """The palette of the project ``folder`` (a COPY: edit it freely),
    cached per palette file version."""
    key = (
        os.path.normcase(os.path.abspath(folder)) if folder else None,
        CP.palette_stamp(folder),
        CP.GENERATION,
    )
    pal = _palettes.get(key)
    if pal is None:
        pal = CP.Palette.for_project(folder)
        if pal.source != "project":
            _settings_favourites(pal)
        if len(_palettes) > 16:
            _palettes.clear()
        _palettes[key] = pal
    return pal.copy()


def save_palette(pal: CP.Palette) -> str:
    """Keep ``pal``'s favourites: the project's citect.pal (CtDraw's own
    store, shared with the Graphics Builder), or -- a page outside any
    project -- the settings. Returns a sentence saying where; raises
    PaletteError when the file cannot be written."""
    _palettes.clear()
    _NAMES.clear()
    if pal.target_path() is None:
        rows = ["%08x,%08x,%s" % (e.on, e.off, e.name) for _i, e in pal.favourites()]
        app_settings().setValue(FAVOURITES_KEY, rows)
        return "This page is not in a project. The favourite colours are kept in the ctgedit settings."
    path = pal.save()
    return "Favourite colours saved in %s." % path


# --------------------------------------------------------------------------- recents
def recent_colours() -> List[Pair]:
    st = app_settings()
    out: List[Pair] = []
    for entry in st.value(RECENT_KEY, [], type=list) or []:
        try:
            a, _, b = str(entry).partition(",")
            out.append((int(a, 16) & 0xFFFFFFFF, int(b or a, 16) & 0xFFFFFFFF))
        except ValueError:
            continue
    return out[:RECENT_MAX]


def remember_colour(on: int, off: Optional[int] = None) -> None:
    """Put a picked colour first in the recent colours."""
    pair = T.as_pair((on, on if off is None else off))
    rows = [p for p in recent_colours() if p != pair]
    rows.insert(0, pair)
    app_settings().setValue(RECENT_KEY, ["%08x,%08x" % p for p in rows[:RECENT_MAX]])


# --------------------------------------------------------------------------- the grid
class PaletteGrid(QtWidgets.QWidget):
    """The palette's 256 entries, 16 x 16, the standard rows and the
    favourite rows split by a gap. ``activated(index)`` on a click / Enter,
    ``hovered(index)`` (-1 = none) as the pointer moves."""

    activated = QtCore.Signal(int)
    hovered = QtCore.Signal(int)
    #: a cell and the gap between the standard and the favourite rows (px at 100 %: times
    #: Settings > Appearance > Text size); the gap between cells
    CELL = 14
    SPLIT = 5
    GAP = 1

    def __init__(
        self,
        palette: CP.Palette,
        parent=None,
        *,
        flash: bool = True,
        transparent: bool = True,
        show_unused: bool = True,
    ):
        super().__init__(parent)
        self._pal = palette
        self._flash = flash
        self._transparent = transparent
        self._show_unused = show_unused
        self._selected = -1
        self._hover = -1
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._fit_cells()

    def _fit_cells(self) -> None:
        """The cells at the text size, the grid's size from them."""
        from .style.theme import px

        self.cell = px(self.CELL)
        self.split = px(self.SPLIT)
        step = self.cell + self.GAP
        self.setFixedSize(CP.COLUMNS * step + 1, CP.SIZE // CP.COLUMNS * step + self.split + 1)

    def _theme_changed(self) -> None:
        """apply_theme (theme._retheme_widgets): the cells at the new text size."""
        self._fit_cells()
        self.update()

    def palette_(self) -> CP.Palette:
        return self._pal

    def selected(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    def cell_rect(self, index: int) -> QtCore.QRect:
        step = self.cell + self.GAP
        row, col = divmod(index, CP.COLUMNS)
        y = row * step + (self.split if row >= CP.STANDARD_ROWS else 0)
        return QtCore.QRect(col * step + 1, y + 1, self.cell, self.cell)

    def index_at(self, pos: QtCore.QPoint) -> int:
        for i in range(CP.SIZE):
            if self.cell_rect(i).adjusted(0, 0, self.GAP, self.GAP).contains(pos):
                return i
        return -1

    def allowed(self, index: int) -> str:
        """'' when the entry can be picked here, else why not."""
        if not 0 <= index < len(self._pal):
            return "no colour"
        e = self._pal[index]
        if not e.used:
            return "an unused favourite slot"
        if e.transparent and not self._transparent:
            return "Transparent is not available here"
        if e.flashing and not self._flash and not e.transparent:
            return CP.MSG_NO_FLASHING
        return ""

    def paintEvent(self, ev) -> None:  # noqa: N802 -- Qt override
        from .style.theme import color, readable_on

        p = QtGui.QPainter(self)
        bg = color("bg")
        p.fillRect(self.rect(), bg)
        for i in range(min(CP.SIZE, len(self._pal))):
            r = self.cell_rect(i)
            if not r.intersects(ev.rect()):
                continue
            e = self._pal[i]
            if not e.used:
                p.setPen(QtGui.QPen(color("border"), 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRect(r.adjusted(0, 0, -1, -1))
                continue
            if self.allowed(i):
                p.setOpacity(0.3)
            paint_colour(p, r, e.on, e.off, checker=False, cross=True, border=QtGui.QColor("#555555"))
            p.setOpacity(1.0)
        # (the cell under the pointer in the colour of text on the selection: white in the
        # dark theme, dark in the light one; the window text where that does not read on the
        # background (high contrast). The selected cell in the accent)
        hover = QtGui.QColor(readable_on(bg, (color("on_selection"), color("head")), 3.0))
        for idx, colour, width in ((self._hover, hover, 1), (self._selected, color("accent"), 2)):
            if 0 <= idx < CP.SIZE:
                r = self.cell_rect(idx).adjusted(-1, -1, 0, 0)
                p.setPen(QtGui.QPen(colour, width))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRect(r)
        p.end()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        i = self.index_at(ev.position().toPoint())
        if i != self._hover:
            self._hover = i
            self.update()
            self.hovered.emit(i)
            if 0 <= i < len(self._pal):
                e = self._pal[i]
                tip = describe(e.on, e.off, e.name) if e.used else "Unused favourite slot"
                why = self.allowed(i)
                if why and e.used:
                    tip += "\n" + why
                self.setToolTip(tip)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        self.hovered.emit(-1)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != QtCore.Qt.LeftButton:
            return
        i = self.index_at(ev.position().toPoint())
        if i >= 0:
            self._selected = i
            self.update()
            self.activated.emit(i)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        i = self._selected if self._selected >= 0 else 0
        k = ev.key()
        moves = {
            QtCore.Qt.Key_Left: -1,
            QtCore.Qt.Key_Right: 1,
            QtCore.Qt.Key_Up: -CP.COLUMNS,
            QtCore.Qt.Key_Down: CP.COLUMNS,
        }
        if k in moves:
            self.set_selected(max(0, min(CP.SIZE - 1, i + moves[k])))
            self.hovered.emit(self._selected)
            return
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space):
            self.activated.emit(i)
            return
        super().keyPressEvent(ev)


# --------------------------------------------------------------------------- eye dropper
def sample_view(view: QtWidgets.QGraphicsView, viewport_pos: QtCore.QPoint) -> Optional[int]:
    """The colour a page view draws at a viewport point, from its scene."""
    scene = view.scene()
    if scene is None or not hasattr(scene, "sample_colour"):
        return None
    sp = view.mapToScene(viewport_pos)
    return scene.sample_colour(sp.x(), sp.y())


def sample_at(global_pos: QtCore.QPoint) -> Optional[int]:
    """The colour under a screen point: a page view's rendered scene when
    the point is on a page, else the screen's pixel."""
    w = QtWidgets.QApplication.widgetAt(global_pos)
    while w is not None:
        if isinstance(w, QtWidgets.QGraphicsView):
            vp = w.viewport()
            local = vp.mapFromGlobal(global_pos)
            if vp.rect().contains(local):
                c = sample_view(w, local)
                if c is not None:
                    return c
            break
        w = w.parentWidget()
    screen = QtGui.QGuiApplication.screenAt(global_pos)
    if screen is None:
        return None
    geo = screen.geometry()
    pm = screen.grabWindow(0, global_pos.x() - geo.x(), global_pos.y() - geo.y(), 1, 1)
    if pm.isNull():
        return None
    img = pm.toImage()
    if img.isNull() or img.width() < 1:
        return None
    return img.pixel(0, 0) & 0xFFFFFF


class EyeDropper(QtCore.QObject):
    """CtDraw's Color Selector: grab the mouse (through ``grabber``, a
    visible widget of the window the picker belongs to -- a modal dialog
    too), and take the colour of the next click. Esc / a right click
    cancels."""

    sampled = QtCore.Signal(int)
    cancelled = QtCore.Signal()

    def __init__(self, grabber: QtWidgets.QWidget):
        super().__init__(grabber)
        self._w = grabber
        self.active = False

    def start(self) -> None:
        self.active = True
        self._w.installEventFilter(self)
        self._w.grabMouse(QtGui.QCursor(QtCore.Qt.CrossCursor))
        self._w.grabKeyboard()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self._w.removeEventFilter(self)
        self._w.releaseMouse()
        self._w.releaseKeyboard()

    def take(self, global_pos: QtCore.QPoint) -> None:
        """The click at ``global_pos`` (a test calls it directly)."""
        self.stop()
        c = sample_at(global_pos)
        if c is None:
            self.cancelled.emit()
        else:
            self.sampled.emit(c)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if not self.active:
            return False
        t = ev.type()
        if t == QtCore.QEvent.MouseButtonPress:
            if ev.button() == QtCore.Qt.LeftButton:
                self.take(ev.globalPosition().toPoint())
            else:
                self.stop()
                self.cancelled.emit()
            return True
        if t in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.Wheel):
            return True
        if t == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_Escape:
            self.stop()
            self.cancelled.emit()
            return True
        return False


# --------------------------------------------------------------------------- the picker
class ColourPicker(QtWidgets.QFrame):
    """The colour picker popup. ``picked(on, off)`` when a colour is chosen,
    ``finished()`` when it goes away (chosen or not); :attr:`result` = the
    pair or None."""

    # (object: a u32 colour does not fit Qt's signed int)
    picked = QtCore.Signal(object, object)
    finished = QtCore.Signal()
    #: the picker shown last (tests reach an open one through it)
    last_opened = None

    def __init__(
        self,
        current=None,
        parent=None,
        *,
        title: str = "Colour",
        flash: bool = True,
        transparent: bool = True,
        project_dir: Optional[str] = None,
    ):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setObjectName("ColourPicker")
        from .style.theme import type_qss

        self.setStyleSheet(
            "#ColourPicker { background: #23262b; border: 1px solid #3a3f46; "
            "border-radius: 6px; } QLabel { color: #d6d9de; } "
            "QLabel#dim { color: #969ca5; %s }"
            % type_qss("caption")
            + " QToolButton#pickerDropper { border: 1px solid #3a3f46; border-radius: 3px; "
            "padding: 2px; } QToolButton#pickerDropper:hover { border-color: #4da3ff; }"
            ' QToolButton#pickerDropper[kbdFocus="true"] { border: 2px solid #4da3ff; '
            "padding: 1px; }"
        )
        self._flash = flash
        self._transparent = transparent
        self._current = as_pair(current)
        self._anchor: Optional[QtWidgets.QWidget] = parent
        self._busy = False
        self._dropper: Optional[EyeDropper] = None
        self.result: Optional[Pair] = None
        folder = project_dir if project_dir is not None else context_dir(parent)
        self._dir = folder
        self._palette = palette_for(folder)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(2), _sp(2), _sp(2), _sp(2))
        lay.setSpacing(_sp(1))
        head = QtWidgets.QLabel(title)
        head.setObjectName("dim")
        lay.addWidget(head)
        #: the title (what the picker sets)
        self.head = head
        self.grid = PaletteGrid(self._palette, self, flash=flash, transparent=transparent)
        self.grid.activated.connect(self.pick_index)
        self.grid.hovered.connect(self._show_hover)
        lay.addWidget(self.grid)
        if self._current is not None:
            self.grid.set_selected(self._palette.index_of(*self._current))

        self._recents = [p for p in recent_colours() if self._allowed_pair(p)]
        if self._recents:
            lbl = QtWidgets.QLabel("Recent")
            lbl.setObjectName("dim")
            lay.addWidget(lbl)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(1)
            self.recent_buttons = []
            from .style.theme import px

            cell, sw = self.grid.cell, px(12)  # (the grid's cells: the text size)
            for pair in self._recents:
                b = QtWidgets.QToolButton(self)
                b.setAutoRaise(True)
                b.setFixedSize(cell, cell)
                b.setIcon(QtGui.QIcon(swatch_pixmap(pair[0], pair[1], sw, sw)))
                b.setIconSize(QtCore.QSize(sw, sw))
                b.setToolTip(describe(*pair))
                b.clicked.connect(lambda _=False, pr=pair: self.pick_pair(*pr))
                row.addWidget(b)
                self.recent_buttons.append(b)
            row.addStretch(1)
            lay.addLayout(row)

        shown = QtWidgets.QHBoxLayout()
        shown.setSpacing(_sp(1))
        self.value = QtWidgets.QLabel(self)
        self.value.setFixedSize(40, 20)
        shown.addWidget(self.value)
        self.value_text = QtWidgets.QLabel(self)
        self.value_text.setObjectName("dim")
        self.value_text.setMinimumWidth(80)
        shown.addWidget(self.value_text, 1)
        lay.addLayout(shown)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(_sp(1))
        #: a typed colour: '#RRGGBB', 'r,g,b' or the name of a palette colour; Enter picks it
        self.entry = QtWidgets.QLineEdit(self)
        self.entry.setPlaceholderText("#RRGGBB, r,g,b or a name")
        self.entry.setAccessibleName("Colour value")
        self.entry.setToolTip(
            "Type a colour and press Enter: #3A7BD5, 58,123,213 or the name of a colour of the palette"
        )
        self.entry.setClearButtonEnabled(True)
        self.entry.returnPressed.connect(self.pick_text)
        self.entry.textEdited.connect(self._entry_edited)
        bottom.addWidget(self.entry, 1)
        from .style.theme import icon

        self.dropper_button = QtWidgets.QToolButton(self)
        self.dropper_button.setObjectName("pickerDropper")
        self.dropper_button.setIcon(icon("eyedropper"))
        self.dropper_button.setAccessibleName("Pick from Page")
        self.dropper_button.setToolTip(
            "Pick from Page: click a colour on the page or the screen. Esc cancels."
        )
        self.dropper_button.clicked.connect(self.start_dropper)
        bottom.addWidget(self.dropper_button)
        self.edit_button = QtWidgets.QPushButton("Edit…", self)
        self.edit_button.setToolTip("Edit the favourite colours")
        self.edit_button.clicked.connect(self.edit_favourites)
        bottom.addWidget(self.edit_button)
        lay.addLayout(bottom)
        self._show_value(self._current)
        if (
            self._current is not None
            and self._current[0] == self._current[1]
            and not T.is_clear(self._current[0])
        ):
            self.entry.setText("#%06X" % (self._current[0] & 0xFFFFFF))
            self.entry.selectAll()

    # -- helpers --------------------------------------------------------------------
    def palette_(self) -> CP.Palette:
        return self._palette

    def _allowed_pair(self, pair: Pair) -> bool:
        clear = T.is_clear(pair[0]) and T.is_clear(pair[1])
        if clear:
            return self._transparent
        return self._flash or pair[0] == pair[1]

    def _show_value(self, pair: Optional[Pair], name: str = "") -> None:
        if pair is None:
            self.value.setPixmap(swatch_pixmap(None, None, 40, 20))
            self.value_text.setText("(not set)")
            return
        self.value.setPixmap(swatch_pixmap(pair[0], pair[1], 40, 20))
        self.value_text.setText(describe(pair[0], pair[1], name))

    def _show_hover(self, index: int) -> None:
        if 0 <= index < len(self._palette) and self._palette[index].used:
            e = self._palette[index]
            self._show_value(e.pair, e.name)
        else:
            self._show_value(self._current)

    # -- choosing ----------------------------------------------------------------------
    def pick_index(self, index: int) -> bool:
        """Choose palette entry ``index`` (a grid click)."""
        why = self.grid.allowed(index)
        if why:
            if 0 <= index < len(self._palette) and self._palette[index].used:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), why, self.grid)
            return False
        e = self._palette[index]
        return self.pick_pair(e.on, e.off)

    def pick_text(self, text: Optional[str] = None) -> bool:
        """Choose the colour typed in the entry (Enter): '#RRGGBB', 'r,g,b', a palette
        colour's name. A text that names no colour frames the entry and says why."""
        from .style.theme import mark_invalid

        text = self.entry.text() if text is None else text
        pair = parse_colour_text(text, self._palette)
        if pair is None:
            mark_invalid(
                self.entry, "Type a colour as #RRGGBB, as r,g,b (0 to 255) or the name of a palette colour."
            )
            return False
        if not self._allowed_pair(pair):
            clear = T.is_clear(pair[0]) and T.is_clear(pair[1])
            mark_invalid(self.entry, "Transparent is not available here." if clear else CP.MSG_NO_FLASHING)
            return False
        mark_invalid(self.entry, "")
        return self.pick_pair(*pair)

    def _entry_edited(self, text: str) -> None:
        """The entry previews the colour typed so far (the swatch and its value)."""
        from .style.theme import mark_invalid

        mark_invalid(self.entry, "")
        pair = parse_colour_text(text, self._palette)
        self._show_value(pair if pair is not None else self._current)

    def pick_pair(self, on: int, off: Optional[int] = None) -> bool:
        pair = T.as_pair((on, on if off is None else off))
        if not self._allowed_pair(pair):
            return False
        self.result = pair
        remember_colour(*pair)
        self.picked.emit(pair[0], pair[1])
        self._close()
        return True

    def _close(self) -> None:
        self._busy = False
        if self._dropper is not None:  # a child of the grabbing widget
            self._dropper.stop()
            self._dropper.deleteLater()
            self._dropper = None
        if self.isVisible():
            self.hide()
        else:
            self.finished.emit()

    def hideEvent(self, ev) -> None:  # noqa: N802
        super().hideEvent(ev)
        if not self._busy:
            self.finished.emit()

    # -- eye dropper -------------------------------------------------------------------
    def _grabber(self) -> QtWidgets.QWidget:
        a = self._anchor
        if isinstance(a, QtWidgets.QWidget) and a.isVisible():
            return a
        return QtWidgets.QApplication.activeWindow() or self

    def start_dropper(self) -> None:
        """Hide the picker and take the colour of the next click."""
        self._busy = True
        self.hide()
        self._dropper = EyeDropper(self._grabber())
        self._dropper.sampled.connect(lambda c: self.pick_pair(c, c))
        self._dropper.cancelled.connect(self._close)
        self._dropper.start()

    # -- favourites -------------------------------------------------------------------
    def edit_favourites(self) -> None:
        """Edit Favorite Colors; its OK selects the colour it shows."""
        self._busy = True
        self.hide()
        parent = self._grabber().window() if self._grabber() is not self else None
        pair = edit_favourites(
            parent, self._current, self._dir, flash=self._flash, transparent=self._transparent
        )
        if pair is not None and self._allowed_pair(pair):
            self.pick_pair(*pair)
        else:
            self._close()

    # -- showing ----------------------------------------------------------------------
    def open_under(self, widget: Optional[QtWidgets.QWidget], focus_entry: bool = False) -> None:
        """Show under ``widget`` (kept on its screen); ``focus_entry``: the keyboard in the
        colour entry (a click on a swatch's value text), else in the palette."""
        self._anchor = widget or self._anchor
        self.adjustSize()
        if widget is not None:
            pos = widget.mapToGlobal(QtCore.QPoint(0, widget.height() + 2))
        else:
            pos = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(pos.x(), area.right() - self.width()))
            y = pos.y()
            if y + self.height() > area.bottom() and widget is not None:
                y = widget.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.height() - 2
            pos = QtCore.QPoint(x, max(area.top(), y))
        self.move(pos)
        ColourPicker.last_opened = self
        self.show()
        if focus_entry:
            self.entry.setFocus(QtCore.Qt.PopupFocusReason)
            self.entry.selectAll()
        else:
            self.grid.setFocus()


def choose_colour(
    parent: Optional[QtWidgets.QWidget],
    current=None,
    *,
    title: str = "Colour",
    flash: bool = True,
    transparent: bool = True,
    anchor: Optional[QtWidgets.QWidget] = None,
    project_dir: Optional[str] = None,
    focus_entry: bool = False,
) -> Optional[Pair]:
    """Run the colour picker under ``anchor`` (else at the pointer) and
    return the chosen (on, off) pair, or None when it was closed without a
    choice. ``focus_entry``: the keyboard starts in the colour entry."""
    host = anchor or parent
    picker = ColourPicker(
        current, host, title=title, flash=flash, transparent=transparent, project_dir=project_dir
    )
    loop = QtCore.QEventLoop()
    picker.finished.connect(loop.quit)
    picker.open_under(anchor, focus_entry=focus_entry)
    if picker.isVisible() or picker._busy:
        loop.exec()
    result = picker.result
    picker.deleteLater()
    return result


def edit_favourites(
    parent: Optional[QtWidgets.QWidget],
    current=None,
    project_dir: Optional[str] = None,
    *,
    flash: bool = True,
    transparent: bool = True,
) -> Optional[Pair]:
    """Tools > Edit Favorite Colors (and the picker's Edit...): edit the
    project palette's favourites; OK saves them (the project's citect.pal)
    and returns the colour the dialog shows. None = cancelled."""
    from .dialog.colour_dialogs import FavoriteColorsDialog

    folder = project_dir if project_dir is not None else context_dir(parent)
    pal = palette_for(folder)
    dlg = FavoriteColorsDialog(pal, parent, current=as_pair(current))
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    if dlg.palette_changed():
        try:
            note = save_palette(dlg.palette_())
        except CP.PaletteError as exc:
            QtWidgets.QMessageBox.warning(parent, "Edit Favourite Colours", str(exc))
            return None
        if parent is not None and hasattr(parent, "statusBar"):
            try:
                parent.statusBar().showMessage(note, 6000)
            except Exception:  # noqa: BLE001
                pass
    return dlg.result_pair()


class ColourButton(QtWidgets.QToolButton):
    """A swatch button that opens the colour picker; ``changed`` after a
    pick. Holds an (on, off) pair (``pair()``) or None."""

    changed = QtCore.Signal()

    def __init__(
        self,
        current=None,
        parent=None,
        *,
        title: str = "Colour",
        flash: bool = True,
        transparent: bool = True,
        size=(48, 22),
    ):
        super().__init__(parent)
        self._pair = as_pair(current)
        self._title = title
        self._flash = flash
        self._transparent = transparent
        self.setFixedSize(*size)
        self.setIconSize(QtCore.QSize(size[0] - 8, size[1] - 8))
        self.clicked.connect(self.choose)
        self._refresh()

    def pair(self) -> Optional[Pair]:
        return self._pair

    def colour(self) -> Optional[int]:
        return None if self._pair is None else self._pair[0]

    def set_pair(self, pair) -> None:
        self._pair = as_pair(pair)
        self._refresh()

    def set_colour(self, colour) -> None:
        self.set_pair(colour)

    def _refresh(self) -> None:
        w, h = self.iconSize().width(), self.iconSize().height()
        if self._pair is None:
            self.setIcon(QtGui.QIcon(swatch_pixmap(None, None, w, h)))
            self.setToolTip("%s: not set" % self._title)
        else:
            self.setIcon(QtGui.QIcon(swatch_pixmap(self._pair[0], self._pair[1], w, h)))
            self.setToolTip("%s: %s" % (self._title, describe(*self._pair)))

    def choose(self) -> None:
        pair = choose_colour(
            self, self._pair, title=self._title, flash=self._flash, transparent=self._transparent, anchor=self
        )
        if pair is not None and pair != self._pair:
            self.set_pair(pair)
            self.changed.emit()


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

# (keyed by the palette file's stamp: a changed citect.pal is a new key)
_caches.register("colour_picker.names", _NAMES, on=("refresh", "tests"))
