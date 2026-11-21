"""Picture options for import, paste, drop and Replace Picture."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import bitmap_import as B
from ..prefs.settings import app_settings, choice

#: Settings > Canvas: when the picture options open
WHEN = (
    ("all", "At every import, paste and drop"),
    ("import", "At File > Import only"),
    ("never", "Never (place them at their own size)"),
)
WHEN_KEY = "opt/picture_options"
#: Settings > Canvas: what Ctrl+V pastes when another program copied a drawing and a picture
PASTE_KINDS = (("bitmap", "A bitmap"), ("drawing", "A drawing, when there is one (stays sharp)"))
PASTE_KIND_KEY = "opt/paste_drawing"
#: Settings > Canvas: a picture stored with more bytes than this is flagged (KB)
LARGE_KEY = "opt/large_picture_kb"
LARGE_DEFAULT = 1024
LARGE_RANGE = (50, 65536)
#: the dialog's last choices
SIZE_MODE_KEY = "import/size_mode"
STORE_KEY = "import/store_at_size"
TOLERANCE_KEY = "import/tolerance"
THRESHOLD_KEY = "import/threshold"


def when() -> str:
    return choice(WHEN_KEY, WHEN, "all")


def asks(trigger: str) -> bool:
    """The options open for ``trigger`` ('import' = File > Import; 'paste', 'drop')."""
    w = when()
    return w == "all" or (w == "import" and trigger == "import")


def paste_kind() -> str:
    return choice(PASTE_KIND_KEY, PASTE_KINDS, "bitmap")


def large_bytes() -> int:
    lo, hi = LARGE_RANGE
    try:
        v = int(str(app_settings().value(LARGE_KEY, LARGE_DEFAULT)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        v = LARGE_DEFAULT
    return max(lo, min(hi, v)) * 1024


def size_text(n: int) -> str:
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / (1024 * 1024))
    return "%d KB" % max(1, round(n / 1024))


def _setting_int(key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(str(app_settings().value(key, default)).strip())
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        v = default
    return max(lo, min(hi, v))


class _Preview(QtWidgets.QWidget):
    """The picture as it will look, over a chequered background; a click picks a colour
    while picking is on."""

    picked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img: Optional[QtGui.QImage] = None
        self._src: Optional[QtGui.QImage] = None
        self.picking = False
        self.setMinimumSize(260, 200)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_images(self, shown: Optional[QtGui.QImage], source: Optional[QtGui.QImage]):
        """``shown`` = the result, ``source`` = the picture before its transparency (the
        colour a click picks)."""
        self._img, self._src = shown, source
        self.update()

    def _target(self) -> QtCore.QRect:
        if self._img is None:
            return QtCore.QRect()
        r = self.rect().adjusted(8, 8, -8, -8)
        w, h = self._img.width(), self._img.height()
        k = min(r.width() / max(1, w), r.height() / max(1, h), 8.0)
        tw, th = max(1, int(w * k)), max(1, int(h * k))
        return QtCore.QRect(r.center().x() - tw // 2, r.center().y() - th // 2, tw, th)

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt override
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        t = self._target()
        if t.isEmpty():
            return
        tile = QtGui.QPixmap(16, 16)
        tile.fill(QtGui.QColor(255, 255, 255))
        tp = QtGui.QPainter(tile)
        tp.fillRect(0, 0, 8, 8, QtGui.QColor(204, 204, 204))
        tp.fillRect(8, 8, 8, 8, QtGui.QColor(204, 204, 204))
        tp.end()
        p.fillRect(t, QtGui.QBrush(tile))
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, t.width() < self._img.width())
        p.drawImage(t, self._img)
        p.setPen(QtGui.QPen(self.palette().mid().color()))
        p.drawRect(t.adjusted(0, 0, -1, -1))

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 -- Qt override
        self.setCursor(
            QtCore.Qt.CrossCursor
            if self.picking and self._target().contains(e.position().toPoint())
            else QtCore.Qt.ArrowCursor
        )

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt override
        t = self._target()
        pos = e.position().toPoint()
        if not self.picking or self._src is None or not t.contains(pos):
            return
        x = (pos.x() - t.left()) * self._src.width() // max(1, t.width())
        y = (pos.y() - t.top()) * self._src.height() // max(1, t.height())
        c = self._src.pixelColor(min(self._src.width() - 1, x), min(self._src.height() - 1, y))
        self.picked.emit((c.red() << 16) | (c.green() << 8) | c.blue())


class PictureImportDialog(QtWidgets.QDialog):
    """Picture import options."""

    RESIZE = "both"

    def exec(self) -> int:  # noqa: D102 -- QDialog.exec
        import os

        if os.environ.get("CTG_NO_MODAL_QUESTIONS") == "1":
            # (the tests: a picture window they did not answer fails at once, never waits)
            raise RuntimeError("an unanswered picture options window")
        return super().exec()

    def __init__(
        self,
        sources: Sequence[B.Source],
        parent=None,
        *,
        fit: Optional[Tuple[int, int]] = None,
        target: Optional[Tuple[int, int]] = None,
        action: str = "Place",
        drawing: Optional[bool] = None,
        each_size: bool = False,
    ):
        super().__init__(parent)
        self.sources: List[B.Source] = list(sources)
        self._fit = fit
        self._target = target
        self.setWindowTitle("Replace Picture" if target else "Import Picture")
        st = app_settings()
        src = self.sources[0]
        lay = QtWidgets.QVBoxLayout(self)
        intro = (
            "Choose the size and transparency of %s." % src.name
            if len(self.sources) == 1
            else "These choices apply to all %d pictures." % len(self.sources)
        )
        lay.addWidget(QtWidgets.QLabel(intro))
        body = QtWidgets.QHBoxLayout()
        lay.addLayout(body, 1)

        left = QtWidgets.QVBoxLayout()
        self.which = QtWidgets.QComboBox()
        for s in self.sources:
            self.which.addItem(s.name)
        self.which.setToolTip("The picture the preview shows")
        self.which.setVisible(len(self.sources) > 1)
        left.addWidget(self.which)
        self.preview = _Preview()
        left.addWidget(self.preview, 1)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        left.addWidget(self.info)
        body.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        # -- size
        box = QtWidgets.QGroupBox("Size on the page")
        g = QtWidgets.QGridLayout(box)
        self.size_mode = QtWidgets.QButtonGroup(self)
        self.r_target = QtWidgets.QRadioButton("The object's size")
        self.r_own = QtWidgets.QRadioButton("Its own size")
        self.r_fit = QtWidgets.QRadioButton("Fit the visible page")
        self.r_custom = QtWidgets.QRadioButton("This size:")
        row = 0
        for key, rb in (
            ("target", self.r_target),
            ("own", self.r_own),
            ("fit", self.r_fit),
            ("custom", self.r_custom),
        ):
            self.size_mode.addButton(rb)
            rb.setProperty("mode", key)
            if key == "target" and not target:
                rb.setVisible(False)
                continue
            if key == "fit" and not fit:
                rb.setVisible(False)
                continue
            g.addWidget(rb, row, 0, 1, 3)
            row += 1
        self.w = QtWidgets.QSpinBox()
        self.h = QtWidgets.QSpinBox()
        for sb, tip in ((self.w, "The width on the page"), (self.h, "The height on the page")):
            sb.setRange(1, 32000)
            sb.setSuffix(" px")
            sb.setToolTip(tip)
        g.addWidget(self.w, row, 1)
        g.addWidget(QtWidgets.QLabel("x"), row, 2)
        g.addWidget(self.h, row, 3)
        row += 1
        self.keep = QtWidgets.QCheckBox("Keep its proportions")
        self.keep.setChecked(True)
        g.addWidget(self.keep, row, 1, 1, 3)
        right.addWidget(box)
        if each_size:
            # (Replace Everywhere: every copy keeps its own size; the preview shows one)
            box.setVisible(False)
            right.addWidget(QtWidgets.QLabel("Each copy keeps its size on the page."))
        # -- kind (a metafile)
        self.kind_box = QtWidgets.QGroupBox("Place it as")
        kl = QtWidgets.QVBoxLayout(self.kind_box)
        self.r_drawing = QtWidgets.QRadioButton("A drawing that stays sharp when resized")
        self.r_bitmap = QtWidgets.QRadioButton("A bitmap")
        self.r_drawing.setChecked(drawing is not False)
        self.r_bitmap.setChecked(drawing is False)
        kl.addWidget(self.r_drawing)
        kl.addWidget(self.r_bitmap)
        self.kind_box.setVisible(drawing is None and any(s.kind == "metafile" for s in self.sources))
        right.addWidget(self.kind_box)
        # -- pixels
        self.px_box = QtWidgets.QGroupBox("Pixels")
        pl = QtWidgets.QVBoxLayout(self.px_box)
        self.store = QtWidgets.QCheckBox("Store the picture at its size on the page")
        self.store.setToolTip(
            (
                "A large picture takes less space in the page and looks sharper than the runtime's "
                "stretching. Off: every pixel is kept and stretched."
            )
        )
        self.store.setChecked(st.value(STORE_KEY, True, type=bool))
        pl.addWidget(self.store)
        self.stored = QtWidgets.QLabel()
        pl.addWidget(self.stored)
        from ..style import theme

        self.large = theme.note_label("", "warn", self)
        self.large.setVisible(False)
        pl.addWidget(self.large)
        right.addWidget(self.px_box)
        # -- transparency
        tbox = QtWidgets.QGroupBox("Transparency")
        tg = QtWidgets.QGridLayout(tbox)
        tg.addWidget(QtWidgets.QLabel("Transparent:"), 0, 0)
        self.mode = QtWidgets.QComboBox()
        for key, label in B.TRANSPARENCY:
            self.mode.addItem(label, key)
        self.mode.setToolTip(
            "None: every pixel shows. The picture's own: its transparent "
            "pixels stay transparent. A colour: that colour becomes "
            "transparent."
        )
        tg.addWidget(self.mode, 0, 1, 1, 2)
        self.threshold_label = QtWidgets.QLabel("Clear below:")
        tg.addWidget(self.threshold_label, 1, 0)
        self.threshold = QtWidgets.QSpinBox()
        self.threshold.setRange(1, 255)
        self.threshold.setValue(_setting_int(THRESHOLD_KEY, 128, 1, 255))
        self.threshold.setToolTip(
            "A pixel less opaque than this becomes transparent (a page bitmap has no half-transparent pixels)"
        )
        tg.addWidget(self.threshold, 1, 1)
        self.colour_label = QtWidgets.QLabel("Colour:")
        tg.addWidget(self.colour_label, 2, 0)
        from ..objprops.colour_field import ColourPairField

        self.colour = ColourPairField("Transparent colour", flash=False)
        tg.addWidget(self.colour, 2, 1)
        self.pick = QtWidgets.QToolButton()
        self.pick.setText("Pick")
        self.pick.setCheckable(True)
        self.pick.setToolTip(
            "Click the preview to take the colour of a pixel, for example a screenshot's background"
        )
        tg.addWidget(self.pick, 2, 2)
        self.tol_label = QtWidgets.QLabel("Tolerance:")
        tg.addWidget(self.tol_label, 3, 0)
        self.tolerance = QtWidgets.QSpinBox()
        self.tolerance.setRange(0, 100)
        self.tolerance.setSuffix(" %")
        self.tolerance.setValue(_setting_int(TOLERANCE_KEY, 0, 0, 100))
        self.tolerance.setToolTip("Also colours this close to it (for photos and smoothed edges)")
        tg.addWidget(self.tolerance, 3, 1)
        right.addWidget(tbox)
        # -- favourite
        self.favourite = QtWidgets.QCheckBox("Keep in File > Import Recent as a favourite")
        self.favourite.setVisible(len(self.sources) == 1 and bool(src.path))
        self.favourite.setChecked(bool(src.path) and B.is_favourite(src.path))
        right.addWidget(self.favourite)
        right.addStretch(1)
        body.addLayout(right)

        self.again = QtWidgets.QCheckBox("Show these options at every import")
        self.again.setToolTip("Settings > Canvas also chooses whether they show for a paste and a drop")
        self.again.setChecked(when() != "never")
        lay.addWidget(self.again)
        self.problem = theme.note_label("", "danger", self)
        self.problem.setVisible(False)
        lay.addWidget(self.problem)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText(action)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        # the starting choices
        key = B.transparent_colour()
        self.colour.set_pair(0xFFFFFF if key is None else key)
        if src.has_alpha:
            self.mode.setCurrentIndex(self.mode.findData("alpha"))
        elif key is not None:
            self.mode.setCurrentIndex(self.mode.findData("colour"))
        else:
            self.mode.setCurrentIndex(self.mode.findData("none"))
        mode = "target" if target else str(st.value(SIZE_MODE_KEY, "own", type=str) or "own")
        if (
            mode == "target"
            and not target
            or mode == "fit"
            and not fit
            or mode not in ("target", "own", "fit", "custom")
        ):
            mode = "own"
        self._busy = True
        self._button(mode).setChecked(True)
        w0, h0 = self._mode_size(src, mode)
        self.w.setValue(w0)
        self.h.setValue(h0)
        self._busy = False

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._update_preview)
        self.size_mode.buttonClicked.connect(lambda _b: self._mode_changed())
        self.w.valueChanged.connect(lambda _v: self._edited(self.w))
        self.h.valueChanged.connect(lambda _v: self._edited(self.h))
        for sig in (
            self.store.toggled,
            self.r_drawing.toggled,
            self.mode.currentIndexChanged,
            self.threshold.valueChanged,
            self.tolerance.valueChanged,
            self.colour.changed,
            self.which.currentIndexChanged,
        ):
            sig.connect(lambda *_a: self._changed())
        self.pick.toggled.connect(self._picking)
        self.preview.picked.connect(self._picked)
        self._sync_enabled()
        self._update_preview()
        self.resize(820, 520)

    # -- the size --------------------------------------------------------------------
    def _button(self, mode: str) -> QtWidgets.QRadioButton:
        return {"target": self.r_target, "own": self.r_own, "fit": self.r_fit, "custom": self.r_custom}[mode]

    def size_mode_name(self) -> str:
        b = self.size_mode.checkedButton()
        return str(b.property("mode")) if b is not None else "own"

    def _mode_size(self, src: B.Source, mode: str) -> Tuple[int, int]:
        if mode == "target" and self._target:
            return self._target
        if mode == "fit" and self._fit:
            fw, fh = self._fit
            k = min(fw * 0.9 / src.width, fh * 0.9 / src.height)
            return max(1, round(src.width * k)), max(1, round(src.height * k))
        if mode == "custom":
            return self.w.value(), self.h.value()
        return src.width, src.height

    def _mode_changed(self) -> None:
        mode = self.size_mode_name()
        if mode != "custom":
            w, h = self._mode_size(self._current(), mode)
            self._busy = True
            self.w.setValue(w)
            self.h.setValue(h)
            self._busy = False
        self._changed()

    def _edited(self, which: QtWidgets.QSpinBox) -> None:
        if self._busy:
            return
        self._busy = True
        self.r_custom.setChecked(True)
        if self.keep.isChecked():
            src = self._current()
            if which is self.w:
                self.h.setValue(max(1, round(self.w.value() * src.height / src.width)))
            else:
                self.w.setValue(max(1, round(self.h.value() * src.width / src.height)))
        self._busy = False
        self._changed()

    def _current(self) -> B.Source:
        return self.sources[max(0, self.which.currentIndex())]

    def options_for(self, src: B.Source) -> B.ImportOptions:
        """The ImportOptions for ``src`` (each picture keeps its proportions with Its own
        size and Fit the visible page; This size with Keep its proportions keeps the
        width and follows each picture's shape)."""
        mode = self.size_mode_name()
        if mode == "custom":
            w = self.w.value()
            # (the width typed is every picture's; with Keep its proportions each height
            # follows the picture's own shape, the first one too, whichever is previewed)
            h = self.h.value() if not self.keep.isChecked() else max(1, round(w * src.height / src.width))
        else:
            w, h = self._mode_size(src, mode)
        return B.ImportOptions(
            w,
            h,
            store_at_size=self.store.isChecked(),
            as_drawing=self.r_drawing.isChecked(),
            transparency=str(self.mode.currentData()),
            threshold=self.threshold.value(),
            key=self.colour.pair()[0] & 0xFFFFFF,
            tolerance=self.tolerance.value(),
        )

    # -- the preview -------------------------------------------------------------------
    def _changed(self) -> None:
        self._sync_enabled()
        self._timer.start()

    def _sync_enabled(self) -> None:
        mode = self.mode.currentData()
        for wdg in (self.threshold_label, self.threshold):
            wdg.setEnabled(mode in ("alpha", "colour"))
        for wdg in (self.colour_label, self.colour, self.pick, self.tol_label, self.tolerance):
            wdg.setEnabled(mode == "colour")
        if mode != "colour" and self.pick.isChecked():
            self.pick.setChecked(False)
        drawing = self.r_drawing.isChecked() and self._current().kind == "metafile"
        self.px_box.setEnabled(not drawing and self._current().kind != "svg")
        self.mode.setEnabled(not drawing)

    def _picking(self, on: bool) -> None:
        self.preview.picking = on
        if on:
            self.mode.setCurrentIndex(self.mode.findData("colour"))

    def _picked(self, rgb: int) -> None:
        self.colour.set_pair(rgb)
        self.pick.setChecked(False)
        self._changed()

    def _update_preview(self) -> None:
        src = self._current()
        opts = self.options_for(src)
        self.problem.setVisible(False)
        ok = True
        try:
            pic = B.build(src, opts)
        except B.PictureError as e:
            self.problem.setText(str(e))
            self.problem.setVisible(True)
            pic = None
            ok = False
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(ok)
        source_img = None
        if pic is None:
            self.preview.set_images(None, None)
        elif pic.kind == "metafile":
            img = src.image(*pic.size)
            self.preview.set_images(img, img)
        else:
            shown = QtGui.QImage(
                bytes(pic.data), pic.width, pic.height, pic.width * 4, QtGui.QImage.Format_RGBA8888
            ).copy()
            source_img = src.image(pic.width, pic.height)
            self.preview.set_images(shown, source_img)
        own = "%s: %d x %d %s" % (
            src.name,
            src.width,
            src.height,
            "px drawing (stays sharp)" if src.vector else "pixels",
        )
        if src.kind == "metafile" and src.emf:
            own += ", an enhanced metafile"
        self.info.setText(own)
        if pic is None:
            self.stored.setText("")
            self.large.setVisible(False)
            return
        n = B.stored_bytes(pic)
        if pic.kind == "metafile":
            self.stored.setText("A drawing of %s." % size_text(n))
        else:
            self.stored.setText(
                "Stored as %d x %d pixels, about %s in the page." % (pic.width, pic.height, size_text(n))
            )
        big = n > large_bytes()
        self.large.setText(
            "A large picture: every page that shows it loads it. Store it "
            "at its size on the page, or make it smaller."
            if big
            else ""
        )
        self.large.setVisible(big)

    # -- the end -----------------------------------------------------------------------
    def accept(self) -> None:
        st = app_settings()
        mode = self.size_mode_name()
        if mode != "target":
            st.setValue(SIZE_MODE_KEY, mode)
        st.setValue(STORE_KEY, self.store.isChecked())
        st.setValue(TOLERANCE_KEY, self.tolerance.value())
        st.setValue(THRESHOLD_KEY, self.threshold.value())
        if self.favourite.isVisible():
            B.set_favourite(self.sources[0].path, self.favourite.isChecked())
        if self.again.isChecked() != (when() != "never"):
            st.setValue(WHEN_KEY, "import" if self.again.isChecked() else "never")
        super().accept()

    def pictures(self) -> List[B.Picture]:
        """Every source as the Picture to place. Raises PictureError."""
        return [B.build(s, self.options_for(s)) for s in self.sources]


def choose(parent, sources: Sequence[B.Source], **kw) -> Optional[List[B.Picture]]:
    """Ask for the options of ``sources``; their Pictures, or None (Cancel). Raises
    PictureError."""
    dlg = PictureImportDialog(sources, parent, **kw)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    return dlg.pictures()
