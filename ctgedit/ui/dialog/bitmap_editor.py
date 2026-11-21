"""Tools > Bitmap Editor: edit an embedded bitmap's pixels."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..objprops.colour_field import NO_COLOUR, ColourPairField

#: the in-editor undo history keeps at most this many bytes of pictures
UNDO_BYTES = 64 * 1024 * 1024
MAX_ZOOM = 32
VIEWS = (("both", "Both states"), ("on", "On state"), ("off", "Off state"))


def _argb(c: int) -> int:
    """A stored colour (0x00RRGGBB; high byte 0xFF = none) as an ARGB32 pixel
    (0 = transparent)."""
    c &= 0xFFFFFFFF
    return 0 if (c >> 24) == 0xFF else 0xFF000000 | (c & 0xFFFFFF)


def _stored(px: int) -> int:
    """An ARGB32 pixel as a stored colour (transparent -> the no-colour value)."""
    return NO_COLOUR if (px >> 24) & 0xFF < 128 else px & 0xFFFFFF


def _image(rgba: bytes, w: int, h: int) -> QtGui.QImage:
    """RGBA bytes -> an ARGB32 image whose pixels are opaque or 0 (the
    record's one-bit transparency)."""
    from ...format.bitmaps import normal_rgba

    data = normal_rgba(bytes(rgba[: w * h * 4]))
    img = QtGui.QImage(data, w, h, w * 4, QtGui.QImage.Format_RGBA8888)
    return img.convertToFormat(QtGui.QImage.Format_ARGB32)


def _rgba(img: QtGui.QImage) -> bytes:
    out = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
    w, h = out.width(), out.height()
    stride = out.bytesPerLine()
    data = bytes(memoryview(out.constBits()))
    if stride == w * 4:
        return data[: w * h * 4]
    return b"".join(data[y * stride : y * stride + w * 4] for y in range(h))


class _BitmapCanvas(QtWidgets.QWidget):
    """The pixel surface: both pictures, zoomed; paints only what is exposed
    (cached background / diagonal-split tiles), updates only changed pixels."""

    #: the eye dropper picked a pixel: (on, off) stored colours (0xFF000000 =
    #: transparent -- beyond a signed int, hence object)
    colourPicked = QtCore.Signal(object, object)
    #: pointer over pixel (x, y), or (-1, -1) when it left the picture
    hovered = QtCore.Signal(int, int)
    #: an edit is about to change the pictures (the dialog takes an undo step)
    aboutToChange = QtCore.Signal()
    changed = QtCore.Signal()
    #: the Select tool's area changed (Crop follows it)
    selectionChanged = QtCore.Signal()

    def __init__(self, primary: QtGui.QImage, flash: QtGui.QImage, parent=None):
        super().__init__(parent)
        self.primary = primary
        self.flash = flash
        self.zoom = 8
        self.tool = "pen"
        self.view = "both"
        self.pair = (0xFF000000, 0xFF000000)  # ARGB (on, off): black
        self._stroke: Optional[Tuple[int, int]] = None
        self._stroke_pair = self.pair
        self._tiles: dict = {}
        self._flashing = self.primary != self.flash
        #: the Select tool's area (picture pixels), None = nothing selected
        self.selection: Optional[QtCore.QRect] = None
        self._sel_from: Optional[Tuple[int, int]] = None
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self._sync_size()

    # -- geometry --------------------------------------------------------------
    def _sync_size(self) -> None:
        self.setFixedSize(self.primary.width() * self.zoom, self.primary.height() * self.zoom)
        self.update()

    def set_zoom(self, z: int) -> None:
        self.zoom = max(1, min(MAX_ZOOM, int(z)))
        self._sync_size()

    def set_view(self, view: str) -> None:
        self.view = view
        self.update()

    def set_pictures(self, primary: QtGui.QImage, flash: QtGui.QImage) -> None:
        self.primary, self.flash = primary, flash
        self._flashing = primary != flash
        self.set_selection(None)
        self._sync_size()

    def set_selection(self, rect: Optional[QtCore.QRect]) -> None:
        """The Select tool's area (clipped to the picture; None or empty = none)."""
        if rect is not None:
            rect = rect.normalized().intersected(
                QtCore.QRect(0, 0, self.primary.width(), self.primary.height())
            )
            if rect.isEmpty():
                rect = None
        if rect != self.selection:
            self.selection = rect
            self.update()
            self.selectionChanged.emit()

    def _px(self, pos) -> Tuple[int, int]:
        return int(pos.x() // self.zoom), int(pos.y() // self.zoom)

    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.primary.width() and 0 <= y < self.primary.height()

    # -- painting --------------------------------------------------------------
    def _tile(self, kind: str) -> QtGui.QBrush:
        """Cached brush tiles: the transparent background (CtDraw: a white
        pixel with a black dot; a light check when zoomed out) and the
        top-left-triangle mask of a flashing pixel."""
        z = self.zoom
        key = (kind, z)
        brush = self._tiles.get(key)
        if brush is not None:
            return brush
        if kind == "clear":
            if z >= 4:
                pm = QtGui.QPixmap(z, z)
                pm.fill(QtGui.QColor(255, 255, 255))
                p = QtGui.QPainter(pm)
                d = max(1, z // 5)
                p.fillRect((z - d) // 2, (z - d) // 2, d, d, QtGui.QColor(0, 0, 0))
                p.end()
            else:
                pm = QtGui.QPixmap(8, 8)
                pm.fill(QtGui.QColor(255, 255, 255))
                p = QtGui.QPainter(pm)
                p.fillRect(0, 0, 4, 4, QtGui.QColor(214, 214, 214))
                p.fillRect(4, 4, 4, 4, QtGui.QColor(214, 214, 214))
                p.end()
            brush = QtGui.QBrush(pm)
        else:  # the on half: top-left triangle
            img = QtGui.QImage(z, z, QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QtGui.QPainter(img)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0))
            p.drawPolygon(QtGui.QPolygon([QtCore.QPoint(0, 0), QtCore.QPoint(z, 0), QtCore.QPoint(0, z)]))
            p.end()
            brush = QtGui.QBrush(img)
        self._tiles[key] = brush
        return brush

    def paintEvent(self, e) -> None:  # noqa: N802 -- Qt override
        p = QtGui.QPainter(self)
        z = self.zoom
        w, h = self.primary.width(), self.primary.height()
        r = e.rect()
        x0, y0 = max(0, r.left() // z), max(0, r.top() // z)
        x1, y1 = min(w, r.right() // z + 1), min(h, r.bottom() // z + 1)
        if x0 >= x1 or y0 >= y1:
            p.end()
            return
        src = QtCore.QRect(x0, y0, x1 - x0, y1 - y0)
        dst = QtCore.QRect(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z)
        p.setBrushOrigin(0, 0)
        p.fillRect(dst, self._tile("clear"))
        if self.view == "on":
            p.drawImage(dst, self.primary, src)
        elif self.view == "off":
            p.drawImage(dst, self.flash, src)
        elif self._flashing and z >= 3:
            # a flashing pixel is split diagonally: on colour top-left
            p.drawImage(dst.topLeft(), self._half(self.flash, src, dst, False))
            p.drawImage(dst.topLeft(), self._half(self.primary, src, dst, True))
        else:
            p.drawImage(dst, self.primary, src)
        if z >= 6:
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 40), 0))
            lines = [QtCore.QLine(x * z, dst.top(), x * z, dst.bottom() + 1) for x in range(x0, x1 + 1)]
            lines += [QtCore.QLine(dst.left(), y * z, dst.right() + 1, y * z) for y in range(y0, y1 + 1)]
            p.drawLines(lines)
        self._paint_selection(p)
        p.end()

    def _paint_selection(self, p: QtGui.QPainter) -> None:
        if self.selection is None:
            return
        z = self.zoom
        s = self.selection
        r = QtCore.QRect(s.left() * z, s.top() * z, s.width() * z - 1, s.height() * z - 1)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 0))
        p.drawRect(r)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 0, QtCore.Qt.DashLine))
        p.drawRect(r)

    def _half(self, img: QtGui.QImage, src: QtCore.QRect, dst: QtCore.QRect, on: bool) -> QtGui.QImage:
        """The zoomed ``src`` part of ``img`` keeping only each pixel's
        top-left (``on``) or bottom-right triangle."""
        out = QtGui.QImage(dst.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        out.fill(0)
        q = QtGui.QPainter(out)
        q.drawImage(out.rect(), img, src)
        q.setCompositionMode(
            QtGui.QPainter.CompositionMode_DestinationIn
            if on
            else QtGui.QPainter.CompositionMode_DestinationOut
        )
        q.setBrushOrigin(0, 0)
        q.fillRect(out.rect(), self._tile("tri"))
        q.end()
        return out

    def _dirty(self, x: int, y: int) -> None:
        z = self.zoom
        self.update(QtCore.QRect(x * z, y * z, z, z))

    # -- editing ---------------------------------------------------------------
    def _targets(self) -> List[QtGui.QImage]:
        return {"on": [self.primary], "off": [self.flash]}.get(self.view, [self.primary, self.flash])

    def put(self, x: int, y: int, pair: Tuple[int, int]) -> bool:
        """Set pixel (x, y) to the ARGB ``pair`` in the pictures being edited;
        True when a pixel changed."""
        if not self._inside(x, y):
            return False
        hit = False
        if self.view in ("both", "on") and self.primary.pixel(x, y) != pair[0]:
            self.primary.setPixel(x, y, pair[0])
            hit = True
        if self.view in ("both", "off") and self.flash.pixel(x, y) != pair[1]:
            self.flash.setPixel(x, y, pair[1])
            hit = True
        if hit:
            self._dirty(x, y)
        return hit

    def _line(self, a: Tuple[int, int], b: Tuple[int, int], pair) -> bool:
        """Every pixel of the segment a-b (Bresenham), so a fast drag leaves
        no gaps."""
        (x0, y0), (x1, y1) = a, b
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        hit = False
        while True:
            hit |= self.put(x0, y0, pair)
            if (x0, y0) == (x1, y1):
                return hit
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def fill(self, x: int, y: int, pair: Tuple[int, int]) -> bool:
        """Flood-fill the 4-connected area of the colour under (x, y) (the
        colour PAIR when both states are edited) with ``pair``."""
        if not self._inside(x, y):
            return False
        import numpy as np

        w, h = self.primary.width(), self.primary.height()

        def arr(img):
            return np.frombuffer(bytes(memoryview(img.constBits())), dtype=np.uint32).reshape(
                h, img.bytesPerLine() // 4
            )[:, :w]

        planes = [arr(im) for im in self._targets()]
        key = np.zeros((h, w), dtype=np.uint64)
        for k, pl in enumerate(planes):
            key |= pl.astype(np.uint64) << np.uint64(32 * k)
        seed = key[y, x]
        new = (pair[0] if self.view != "off" else pair[1]) & 0xFFFFFFFF
        want = [pair[0] & 0xFFFFFFFF, pair[1] & 0xFFFFFFFF] if self.view == "both" else [new]
        if all(int(pl[y, x]) == v for pl, v in zip(planes, want)):
            return False
        same = key == seed
        region = np.zeros((h, w), dtype=bool)
        stack = [(x, y)]
        while stack:  # span fill over the mask
            cx, cy = stack.pop()
            if region[cy, cx] or not same[cy, cx]:
                continue
            ok = same[cy] & ~region[cy]
            left = ok[: cx + 1][::-1]
            lx = cx + 1 - (len(left) if left.all() else int(np.argmin(left)))
            right = ok[cx:]
            rx = cx - 1 + (len(right) if right.all() else int(np.argmin(right)))
            region[cy, lx : rx + 1] = True
            for ny in (cy - 1, cy + 1):
                if 0 <= ny < h:
                    seg = same[ny, lx : rx + 1] & ~region[ny, lx : rx + 1]
                    if seg.any():
                        edges = np.flatnonzero(np.diff(np.concatenate(([0], seg.astype(np.int8), [0]))) == 1)
                        stack.extend((lx + int(s), ny) for s in edges)
        for img, v in zip(self._targets(), want):
            a = arr(img).copy()
            a[region] = v
            full = np.zeros((h, img.bytesPerLine() // 4), dtype=np.uint32)
            full[:, :w] = a
            data = full.tobytes()
            fresh = QtGui.QImage(data, w, h, img.bytesPerLine(), QtGui.QImage.Format_ARGB32).copy()
            if img is self.primary:
                self.primary = fresh
            else:
                self.flash = fresh
        self._flashing = self.primary != self.flash
        self.update()
        return True

    def pick(self, x: int, y: int) -> None:
        if not self._inside(x, y):
            return
        on, off = self.primary.pixel(x, y), self.flash.pixel(x, y)
        if self.view == "on":
            off = on
        elif self.view == "off":
            on = off
        self.colourPicked.emit(_stored(on), _stored(off))

    def _finish_stroke(self) -> None:
        if self._stroke is not None:
            self._stroke = None
            self._flashing = self.primary != self.flash
            self.update()
            self.changed.emit()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        x, y = self._px(e.position())
        right = e.button() == QtCore.Qt.RightButton
        if e.button() not in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
            return
        if self.tool == "dropper" and not right:
            self.pick(x, y)
            return
        if self.tool == "select":
            x = max(0, min(self.primary.width() - 1, x))
            y = max(0, min(self.primary.height() - 1, y))
            self._sel_from = (x, y)
            self.set_selection(QtCore.QRect(x, y, 1, 1))
            return
        # the right button draws with the transparent background colour
        pair = (0, 0) if right or self.tool == "eraser" else self.pair
        if self.tool == "fill":
            self.aboutToChange.emit()
            self.fill(x, y, pair)
            self.changed.emit()  # the dialog drops the step if nothing changed
            return
        self.aboutToChange.emit()
        self._stroke = (x, y)
        self._stroke_pair = pair
        self.put(x, y, pair)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        x, y = self._px(e.position())
        self.hovered.emit(*((x, y) if self._inside(x, y) else (-1, -1)))
        if self._sel_from is not None and e.buttons() & QtCore.Qt.LeftButton:
            x0, y0 = self._sel_from
            x = max(0, min(self.primary.width() - 1, x))
            y = max(0, min(self.primary.height() - 1, y))
            self.set_selection(
                QtCore.QRect(QtCore.QPoint(min(x0, x), min(y0, y)), QtCore.QPoint(max(x0, x), max(y0, y)))
            )
            return
        if self._stroke is not None and e.buttons() & (QtCore.Qt.LeftButton | QtCore.Qt.RightButton):
            self._line(self._stroke, (x, y), self._stroke_pair)
            self._stroke = (x, y)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._sel_from = None
        self._finish_stroke()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.hovered.emit(-1, -1)
        super().leaveEvent(e)


class BitmapSizeDialog(QtWidgets.QDialog):
    """The Bitmap Editor's Resize: Grow adds transparent pixels to each edge,
    Shrink removes them (CtDraw's Bitmap Size dialog) -- the
    image itself is never scaled."""

    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bitmap Size")
        self._w, self._h = width, height
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Current size: %d x %d pixels" % (width, height)))
        mode = QtWidgets.QHBoxLayout()
        self.grow = QtWidgets.QRadioButton("Grow (add pixels)")
        self.shrink = QtWidgets.QRadioButton("Shrink (remove pixels)")
        self.grow.setChecked(True)
        mode.addWidget(self.grow)
        mode.addWidget(self.shrink)
        mode.addStretch(1)
        lay.addLayout(mode)
        form = QtWidgets.QFormLayout()
        self.sides = {}
        for key, label in (("left", "Left:"), ("top", "Top:"), ("right", "Right:"), ("bottom", "Bottom:")):
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, 4096)
            sp.setSuffix(" px")
            sp.valueChanged.connect(self._update)
            form.addRow(label, sp)
            self.sides[key] = sp
        lay.addLayout(form)
        self._new = QtWidgets.QLabel()
        lay.addWidget(self._new)
        self._bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._bb.accepted.connect(self.accept)
        self._bb.rejected.connect(self.reject)
        lay.addWidget(self._bb)
        self.grow.toggled.connect(self._update)
        self._update()

    def margins(self) -> Tuple[int, int, int, int]:
        s = self.sides
        return (s["left"].value(), s["top"].value(), s["right"].value(), s["bottom"].value())

    def new_size(self) -> Tuple[int, int]:
        l, t, r, b = self.margins()
        sign = 1 if self.grow.isChecked() else -1
        return self._w + sign * (l + r), self._h + sign * (t + b)

    def _update(self) -> None:
        from ...format.bitmaps import MAX_SIDE

        nw, nh = self.new_size()
        ok = 1 <= nw <= MAX_SIDE and 1 <= nh <= MAX_SIDE
        if ok:
            self._new.setText("New size: %d x %d pixels" % (nw, nh))
        elif nw < 1 or nh < 1:
            self._new.setText("That removes the whole picture: remove fewer pixels.")
        else:
            self._new.setText("A bitmap can be at most %d pixels wide and high." % MAX_SIDE)
        self._bb.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(ok and any(self.margins()))


class BitmapEditorDialog(QtWidgets.QDialog):
    """Tools > Bitmap Editor."""

    #: Save: put the picture into the page (the window answers saved() /
    #: show_problem())
    saveRequested = QtCore.Signal()

    def __init__(
        self,
        rgba: bytes,
        width: int,
        height: int,
        parent=None,
        flash_rgba: Optional[bytes] = None,
        note: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Bitmap Editor")
        self._gone = False
        #: the editor window writes the picture (saveRequested); else Save
        #: accepts the dialog and the caller reads the result
        self.window_saves = False
        primary = _image(rgba, width, height)
        flash = _image(flash_rgba, width, height) if flash_rgba is not None else primary.copy()
        self._orig = (primary.copy(), flash.copy())
        #: (primary, flash, crop shift) before each edit
        self._undo: List[tuple] = []
        self._redo: List[tuple] = []
        self._prev_tool = "pen"
        #: how far crops moved the picture's top-left (the page keeps the kept part where
        #: it was): (dx, dy) pixels, undone with the edits
        self._shift = (0, 0)
        lay = QtWidgets.QVBoxLayout(self)

        bar = QtWidgets.QHBoxLayout()
        self._tools = QtWidgets.QButtonGroup(self)
        self._tool_buttons = {}
        for key, label, tip in (
            ("pen", "Pen", "Draw pixels. The right mouse button draws transparent."),
            ("eraser", "Eraser", "Make pixels transparent"),
            ("fill", "Fill", "Fill an area of one colour"),
            ("dropper", "Eye Dropper", "Eye dropper: take the colour of a pixel (Shift+P)"),
            ("select", "Select", "Drag an area to crop the picture to"),
        ):
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(key == "pen")
            b.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            self._tools.addButton(b)
            self._tool_buttons[key] = b
            bar.addWidget(b)
        bar.addSpacing(8)
        self.colour = ColourPairField("Drawing colour", flash=True, transparent=True)
        self.colour.set_pair(0x000000, 0x000000)
        self.colour.setToolTip("The drawing colour. A flashing colour draws flashing pixels.")
        bar.addWidget(self.colour)
        bar.addStretch(1)
        lay.addLayout(bar)

        bar2 = QtWidgets.QHBoxLayout()
        from ..style.theme import icon

        self._b_undo = QtWidgets.QToolButton()
        self._b_undo.setIcon(icon("undo"))
        self._b_undo.setToolTip("Undo (Ctrl+Z)")
        self._b_redo = QtWidgets.QToolButton()
        self._b_redo.setIcon(icon("redo"))
        self._b_redo.setToolTip("Redo (Ctrl+Y)")
        b_in = QtWidgets.QToolButton()
        b_in.setIcon(icon("zoom_in"))
        b_out = QtWidgets.QToolButton()
        b_out.setIcon(icon("zoom_out"))
        self._b_in, self._b_out = b_in, b_out
        b_size = QtWidgets.QPushButton("Bitmap Size…")
        b_size.setToolTip("Add or remove pixels at the edges")
        for w in (self._b_undo, self._b_redo, b_out, b_in, b_size):
            bar2.addWidget(w)
        bar2.addSpacing(8)
        self._b_crop = QtWidgets.QToolButton()
        self._b_crop.setText("Crop")
        self._b_crop.setToolTip("Keep only the selected area (Select tool)")
        self._b_crop.setEnabled(False)
        b_trim = QtWidgets.QToolButton()
        b_trim.setText("Trim")
        b_trim.setToolTip("Remove the transparent rows and columns at the edges")
        turns = [(b_trim, "trim")]
        # (the four turns in one menu: the row keeps every command's name whole, also at a
        # large text size on a small screen)
        self._b_turn = QtWidgets.QToolButton()
        self._b_turn.setText("Rotate and Flip")
        self._b_turn.setToolTip("Turn or mirror the picture")
        self._b_turn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._b_turn.setProperty("ctgMenu", True)  # (room for its arrow)
        turn_menu = QtWidgets.QMenu(self._b_turn)
        self._turn_actions = {}
        for label, tip, op in (
            ("Rotate Left", "Turn the picture 90 degrees anticlockwise", "left"),
            ("Rotate Right", "Turn the picture 90 degrees clockwise", "right"),
            ("Flip Horizontal", "Mirror the picture left to right", "flip_h"),
            ("Flip Vertical", "Mirror the picture top to bottom", "flip_v"),
        ):
            a = turn_menu.addAction(label)
            a.setToolTip(tip)
            a.triggered.connect(lambda _=False, o=op: self.turn(o))
            self._turn_actions[op] = a
        self._b_turn.setMenu(turn_menu)
        for w in (self._b_crop, b_trim, self._b_turn):
            bar2.addWidget(w)
        bar2.addSpacing(12)
        bar2.addWidget(QtWidgets.QLabel("Show:"))
        self.view = QtWidgets.QComboBox()
        for key, label in VIEWS:
            self.view.addItem(label, key)
        # (as wide as its longest value, its arrow included)
        self.view.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.view.setToolTip("The state to show and edit")
        bar2.addWidget(self.view)
        bar2.addStretch(1)
        lay.addLayout(bar2)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setBackgroundRole(QtGui.QPalette.Dark)
        # a picture smaller than the area sits in its middle
        self._scroll.setAlignment(QtCore.Qt.AlignCenter)
        self._canvas = _BitmapCanvas(primary, flash)
        self._scroll.setWidget(self._canvas)
        self._scroll.setMinimumSize(420, 320)
        lay.addWidget(self._scroll, 1)
        self._note = QtWidgets.QLabel(note)
        self._note.setWordWrap(True)
        self._note.setVisible(bool(note))
        lay.addWidget(self._note)
        self._lbl = QtWidgets.QLabel()
        lay.addWidget(self._lbl)
        from ..style import theme

        self._problem = theme.note_label("", "danger", self)
        self._problem.setVisible(False)
        lay.addWidget(self._problem)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        # (it puts the picture on the page, one undo step; the page itself still needs saving,
        # so not 'Save')
        apply_btn = buttons.button(QtWidgets.QDialogButtonBox.Save)
        apply_btn.setText("Apply to Page")
        apply_btn.setToolTip(
            "Close the editor and put the changed picture on the page (one undo step). Save the "
            "page to keep it on disk"
        )
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setToolTip(
            "Close the editor and discard the changes"
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        # a zoom that shows the whole picture (at most 32x, at least 1x)
        fit = min(640 // max(1, width), 480 // max(1, height))
        self._canvas.set_zoom(max(1, min(MAX_ZOOM, fit, 16)))
        b_in.clicked.connect(lambda: self._zoom(2))
        b_out.clicked.connect(lambda: self._zoom(0.5))
        b_size.clicked.connect(self._bitmap_size)
        self._b_crop.clicked.connect(self.crop)
        for tb, op in turns:
            tb.clicked.connect(self.trim if op == "trim" else (lambda _=False, o=op: self.turn(o)))
        self._canvas.selectionChanged.connect(
            lambda: self._b_crop.setEnabled(self._canvas.selection is not None)
        )
        self._b_undo.clicked.connect(self.undo)
        self._b_redo.clicked.connect(self.redo)
        self.view.currentIndexChanged.connect(lambda _i: self._canvas.set_view(self.view.currentData()))
        self.colour.changed.connect(self._sync_colour)
        self._canvas.colourPicked.connect(self._picked)
        self._canvas.aboutToChange.connect(self._snapshot)
        self._canvas.changed.connect(self._committed)
        self._canvas.hovered.connect(self._hover)
        # the editor's zoom keys always; the bare + / = / - (the Graphics Builder's) while
        # Settings > Keyboard says so
        for seq, fn in (
            ("Ctrl+=", lambda: self._zoom(2)),
            ("Ctrl++", lambda: self._zoom(2)),
            ("Ctrl+-", lambda: self._zoom(0.5)),
            ("Shift+P", lambda: self._set_tool("dropper")),
            ("Ctrl+Z", self.undo),
            ("Ctrl+Y", self.redo),
            ("Ctrl+Shift+Z", self.redo),
        ):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, fn)
        self._bare_zoom_keys = [
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, fn)
            for seq, fn in (
                ("+", lambda: self._zoom(2)),
                ("=", lambda: self._zoom(2)),
                ("-", lambda: self._zoom(0.5)),
            )
        ]
        self.apply_key_settings()
        self._sync_colour()
        self._update_label()
        self.resize(760, 620)

    def apply_key_settings(self) -> None:
        """Settings > Keyboard > "Plus and minus zoom in the Bitmap Editor and the magnifier"."""
        from ..editor.view_consistency import bare_zoom_keys

        bare = bare_zoom_keys()
        for sc in self._bare_zoom_keys:
            sc.setEnabled(bare)
        self._b_in.setToolTip("Zoom in (Ctrl+=%s)" % (", +" if bare else ""))
        self._b_out.setToolTip("Zoom out (Ctrl+-%s)" % (", -" if bare else ""))

    # -- state -------------------------------------------------------------------
    @property
    def canvas(self) -> _BitmapCanvas:
        return self._canvas

    def modified(self) -> bool:
        c = self._canvas
        return c.primary != self._orig[0] or c.flash != self._orig[1]

    def has_pending_edits(self) -> bool:
        """Pixels were changed and not saved into the page."""
        return self.modified()

    # -- saving into the page ------------------------------------------------------
    def _save(self) -> None:
        self.show_problem("")
        if self.window_saves:
            self.saveRequested.emit()
        else:
            self.accept()

    def show_problem(self, text: str) -> None:
        """Why Save did not write the picture (the edits are kept)."""
        self._problem.setText(text)
        self._problem.setVisible(bool(text))

    def saved(self) -> None:
        """The window wrote the picture: close without asking."""
        c = self._canvas
        self._orig = (c.primary.copy(), c.flash.copy())
        self.accept()

    def target_gone(self) -> None:
        """Its bitmap is no longer on the page (the edits stay; Save says
        why it cannot write them)."""
        self._gone = True

    def retarget(self, _index: int) -> None:
        """Its bitmap moved in the page's object list."""
        self._gone = False

    def _set_tool(self, key: str) -> None:
        if key == "dropper" and self._canvas.tool != "dropper":
            self._prev_tool = self._canvas.tool
        self._canvas.tool = key
        self._tool_buttons[key].setChecked(True)
        self._canvas.setCursor(QtCore.Qt.CrossCursor if key != "dropper" else QtCore.Qt.PointingHandCursor)
        if key != "select":
            self._canvas.set_selection(None)

    def _sync_colour(self) -> None:
        on, off = self.colour.pair()
        self._canvas.pair = (_argb(on), _argb(off))

    def _picked(self, on: int, off: int) -> None:
        """Eye Dropper: the pixel's colour becomes the current colour and the
        previous drawing tool comes back (CtDraw: the colour is 'used when you
        click elsewhere on the image')."""
        if (on >> 24) == 0xFF and (off >> 24) == 0xFF:
            self.colour.pick_transparent()
        elif (on >> 24) == 0xFF or (off >> 24) == 0xFF:
            # one half transparent: a pair the colour field cannot show -- keep
            # it for drawing as picked
            self.colour.pick(on & 0xFFFFFF if (on >> 24) != 0xFF else off & 0xFFFFFF)
        else:
            self.colour.pick(on, off)
        self._canvas.pair = (_argb(on), _argb(off))
        self._set_tool(self._prev_tool if self._prev_tool != "dropper" else "pen")

    def _zoom(self, factor: float) -> None:
        c = self._canvas
        z = c.zoom * 2 if factor > 1 else c.zoom // 2
        c.set_zoom(z or 1)
        self._update_label()

    def _hover(self, x: int, y: int) -> None:
        self._update_label((x, y) if x >= 0 else None)

    def _update_label(self, at=None) -> None:
        c = self._canvas
        text = "%d x %d pixels   zoom %dx" % (c.primary.width(), c.primary.height(), c.zoom)
        if c.primary != c.flash:
            text += "   flashing"
        if at is not None:
            x, y = at
            on, off = c.primary.pixel(x, y), c.flash.pixel(x, y)

            def name(px):
                return "transparent" if (px >> 24) < 128 else "#%06X" % (px & 0xFFFFFF)

            text += "   (%d, %d) %s" % (x, y, name(on) if on == off else "%s / %s" % (name(on), name(off)))
        self._lbl.setText(text)
        self._b_undo.setEnabled(bool(self._undo))
        self._b_redo.setEnabled(bool(self._redo))

    # -- undo inside the editor ---------------------------------------------------
    def _snapshot(self) -> None:
        """The pictures before an edit (the redo history is dropped only once
        the edit changed a pixel: :meth:`_committed`)."""
        c = self._canvas
        self._undo.append((c.primary.copy(), c.flash.copy(), self._shift))
        per = max(1, c.primary.sizeInBytes() * 2)
        while len(self._undo) > 1 and len(self._undo) * per > UNDO_BYTES:
            self._undo.pop(0)
        self._update_label()

    def _committed(self) -> None:
        """A stroke / fill ended: a no-op one (a click with the colour the
        pixel already had) leaves no undo step and keeps the redo history."""
        c = self._canvas
        if self._undo and self._undo[-1][0] == c.primary and self._undo[-1][1] == c.flash:
            self._undo.pop()
        else:
            self._redo.clear()
        self._update_label()

    def _restore(self, src: list, dst: list) -> None:
        if not src:
            return
        c = self._canvas
        dst.append((c.primary.copy(), c.flash.copy(), self._shift))
        p, f, self._shift = src.pop()
        c.set_pictures(p, f)
        self._update_label()

    def undo(self) -> None:
        # a no-op stroke (nothing changed) leaves an equal snapshot: skip it
        c = self._canvas
        while self._undo and self._undo[-1][0] == c.primary and self._undo[-1][1] == c.flash:
            self._undo.pop()
        self._restore(self._undo, self._redo)

    def redo(self) -> None:
        self._restore(self._redo, self._undo)

    # -- resize ---------------------------------------------------------------------
    def _bitmap_size(self) -> None:
        c = self._canvas
        dlg = BitmapSizeDialog(c.primary.width(), c.primary.height(), self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self.resize_edges(*dlg.margins(), grow=dlg.grow.isChecked())

    def resize_edges(self, left: int, top: int, right: int, bottom: int, grow: bool = True) -> bool:
        """Grow (transparent pixels) or shrink (crop) each edge -- one undo step."""
        c = self._canvas
        w, h = c.primary.width(), c.primary.height()
        if grow:
            nw, nh = w + left + right, h + top + bottom
        else:
            nw, nh = w - left - right, h - top - bottom
        if nw < 1 or nh < 1 or (nw, nh) == (w, h) and not any((left, top, right, bottom)):
            return False
        self._snapshot()
        out = []
        for img in (c.primary, c.flash):
            if grow:
                new = QtGui.QImage(nw, nh, QtGui.QImage.Format_ARGB32)
                new.fill(0)
                p = QtGui.QPainter(new)
                p.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
                p.drawImage(left, top, img)
                p.end()
            else:
                new = img.copy(left, top, nw, nh)
            out.append(new)
        c.set_pictures(*out)
        self._redo.clear()
        self._update_label()
        return True

    # -- crop, trim, turn ---------------------------------------------------------------
    def _set_both(self, primary: QtGui.QImage, flash: QtGui.QImage, shift=(0, 0)) -> None:
        """One undo step: the new pictures (a crop moves the top-left by ``shift``)."""
        self._snapshot()
        self._shift = (self._shift[0] + shift[0], self._shift[1] + shift[1])
        self._canvas.set_pictures(primary, flash)
        self._redo.clear()
        self._update_label()

    def crop(self) -> bool:
        """Keep only the Select tool's area."""
        c = self._canvas
        s = c.selection
        if s is None:
            return False
        self._set_both(c.primary.copy(s), c.flash.copy(s), (s.left(), s.top()))
        return True

    def trim(self) -> bool:
        """Remove the transparent rows and columns at the edges (of both states)."""
        import numpy as np

        c = self._canvas
        w, h = c.primary.width(), c.primary.height()
        keep = np.zeros((h, w), dtype=bool)
        for img in (c.primary, c.flash):
            a = np.frombuffer(_rgba(img), dtype=np.uint8).reshape(h, w, 4)
            keep |= a[:, :, 3] >= 128
        if not keep.any():
            return False
        ys, xs = np.flatnonzero(keep.any(axis=1)), np.flatnonzero(keep.any(axis=0))
        r = QtCore.QRect(int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1))
        if (r.width(), r.height()) == (w, h):
            return False
        self._set_both(c.primary.copy(r), c.flash.copy(r), (r.left(), r.top()))
        return True

    def turn(self, op: str) -> None:
        """'left' / 'right' (90 degrees), 'flip_h' / 'flip_v'."""
        c = self._canvas
        out = []
        for img in (c.primary, c.flash):
            if op in ("left", "right"):
                t = QtGui.QTransform().rotate(-90 if op == "left" else 90)
                img = img.transformed(t, QtCore.Qt.FastTransformation)
            else:
                img = img.flipped(QtCore.Qt.Horizontal if op == "flip_h" else QtCore.Qt.Vertical)
            out.append(img.convertToFormat(QtGui.QImage.Format_ARGB32))
        self._set_both(*out)

    def origin_shift(self) -> Tuple[int, int]:
        """How far the crops moved the picture's top-left (pixels)."""
        return self._shift

    # -- results --------------------------------------------------------------------
    def reject(self) -> None:
        if self.modified() and self.isVisible() and not getattr(self, "_ctg_dropped", False):
            ans = QtWidgets.QMessageBox.question(
                self,
                "Bitmap Editor",
                "Discard your changes to this bitmap?",
                QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if ans != QtWidgets.QMessageBox.Discard:
                return
        super().reject()

    def result_rgba(self):
        """``(rgba, width, height)`` of the on-state (primary) picture."""
        img = self._canvas.primary
        return _rgba(img), img.width(), img.height()

    def result_flash(self) -> Optional[bytes]:
        """The off-state picture, or None when it equals the on state (a
        plain, not flashing, bitmap)."""
        c = self._canvas
        return None if c.primary == c.flash else _rgba(c.flash)
