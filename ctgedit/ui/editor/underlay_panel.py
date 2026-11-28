"""The Underlay window and the earlier-version picker."""

from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import underlay as U


class UnderlayDialog(QtWidgets.QDialog):
    """The underlay of the page shown: its source (Change, Remove), Show, Above the objects,
    Opacity, Tint, X / Y, Scale (Fit Page, Actual Size), Snap to its objects, Smooth
    scaling, Draw its template and Move with the Mouse."""

    RESIZE = "width"

    def __init__(self, window):
        super().__init__(window)
        from ..style.theme import sp

        self._win = window
        self._doc = None
        self._loading = False
        self.setWindowTitle("Underlay")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(sp(2))
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self._build_source_rows(form, window)
        self._build_look_rows(form)
        self._build_place_rows(form)
        lay.addLayout(form)
        self._form = form
        self._build_options(lay)
        lay.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, self)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._controls = (
            self.cb_show,
            self.cb_above,
            self.sl_opacity,
            self.sp_opacity,
            self.cb_tint,
            self.sp_x,
            self.sp_y,
            self.sp_scale,
            self.btn_fit,
            self.btn_actual,
            self.cb_snap,
            self.cb_template,
            self.cb_smooth,
            self.btn_move,
            self.btn_remove,
        )

    def _build_source_rows(self, form: QtWidgets.QFormLayout, window) -> None:
        """The Source row (Change, Remove) and the note under it (Try Again)."""
        from ..names import ElidedLabel
        from ..style.theme import set_note_role, sp

        # -- the source
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(sp(2))
        self.source = ElidedLabel("", self)
        self.source.setAccessibleName("Source")
        self.source.setMinimumWidth(140)
        row.addWidget(self.source, 1)
        self.btn_change = QtWidgets.QPushButton("Change", self)
        self.btn_change.setToolTip("Underlay another page, a picture or an earlier version of this page")
        self.btn_change.setAutoDefault(False)
        self._change_menu = QtWidgets.QMenu(self.btn_change)
        for a in (window.act_underlay_page, window.act_underlay_image, window.act_underlay_version):
            self._change_menu.addAction(a)
        self.btn_change.setMenu(self._change_menu)
        row.addWidget(self.btn_change)
        self.btn_remove = QtWidgets.QPushButton("Remove", self)
        self.btn_remove.setToolTip("Take the underlay off this page")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.clicked.connect(lambda: self._win.act_underlay_remove.trigger())
        row.addWidget(self.btn_remove)
        form.addRow("Source:", row)
        note_row = QtWidgets.QHBoxLayout()
        note_row.setSpacing(sp(2))
        self.note = QtWidgets.QLabel("", self)
        self.note.setWordWrap(True)
        set_note_role(self.note, "text_dim")
        note_row.addWidget(self.note, 1)
        # (shown while it could not be drawn: a share that was away, a page saved again)
        self.btn_retry = QtWidgets.QPushButton("Try Again", self)
        self.btn_retry.setToolTip("Draw the underlay again from its file")
        self.btn_retry.setAutoDefault(False)
        self.btn_retry.clicked.connect(lambda: self._win.reload_underlay())
        self.btn_retry.setVisible(False)
        note_row.addWidget(self.btn_retry, 0, QtCore.Qt.AlignTop)
        form.addRow("", note_row)

    def _build_look_rows(self, form: QtWidgets.QFormLayout) -> None:
        """How it shows: Show, Above the objects, Opacity and Tint."""
        from ..style.theme import sp

        # -- how it shows
        shows = QtWidgets.QHBoxLayout()
        shows.setSpacing(sp(4))
        self.cb_show = QtWidgets.QCheckBox("Show", self)
        self.cb_show.setToolTip("Show the underlay on the canvas (also View > Underlay > Show Underlay)")
        self.cb_show.toggled.connect(lambda on: self._apply(shown=on))
        shows.addWidget(self.cb_show)
        self.cb_above = QtWidgets.QCheckBox("Above the objects", self)
        self.cb_above.setToolTip(
            "Draw it over the page's objects instead of under them. The selection and the guides stay on top"
        )
        self.cb_above.toggled.connect(lambda on: self._apply(above=on))
        shows.addWidget(self.cb_above)
        shows.addStretch(1)
        form.addRow("", shows)
        op = QtWidgets.QHBoxLayout()
        op.setSpacing(sp(2))
        lo, hi = U.OPACITY_RANGE
        self.sl_opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self.sl_opacity.setRange(lo, hi)
        self.sl_opacity.setAccessibleName("Opacity")
        self.sl_opacity.setToolTip("How strongly the underlay shows")
        self.sl_opacity.valueChanged.connect(self._opacity_slid)
        op.addWidget(self.sl_opacity, 1)
        self.sp_opacity = QtWidgets.QSpinBox(self)
        self.sp_opacity.setRange(lo, hi)
        self.sp_opacity.setSuffix(" %")
        self.sp_opacity.setKeyboardTracking(False)
        self.sp_opacity.setAccessibleName("Opacity")
        self.sp_opacity.setToolTip("How strongly the underlay shows, %d to %d %%" % (lo, hi))
        self.sp_opacity.valueChanged.connect(self._opacity_typed)
        op.addWidget(self.sp_opacity)
        form.addRow("Opacity:", op)
        self.cb_tint = QtWidgets.QComboBox(self)
        for value, label in U.TINTS:
            self.cb_tint.addItem(label, value)
        self.cb_tint.setToolTip("Grey or Blue make it read as a reference, not as objects")
        self.cb_tint.currentIndexChanged.connect(lambda _i: self._apply(tint=self.cb_tint.currentData()))
        form.addRow("Tint:", self.cb_tint)

    def _build_place_rows(self, form: QtWidgets.QFormLayout) -> None:
        """Where it lies: X and Y, and Scale with Fit Page and Actual Size."""
        from ..style.theme import sp

        # -- where it lies
        def coord(name: str, tip: str) -> QtWidgets.QDoubleSpinBox:
            s = QtWidgets.QDoubleSpinBox(self)
            s.setRange(-U.COORD_RANGE, U.COORD_RANGE)
            s.setDecimals(1)
            s.setSuffix(" px")
            s.setKeyboardTracking(False)
            s.setAccessibleName(name)
            s.setToolTip(tip)
            return s

        self.sp_x = coord("X", "Where its left edge lies on the page, in page pixels")
        self.sp_x.valueChanged.connect(lambda v: self._apply(x=float(v)))
        self.sp_y = coord("Y", "Where its top edge lies on the page, in page pixels")
        self.sp_y.valueChanged.connect(lambda v: self._apply(y=float(v)))
        xy = QtWidgets.QHBoxLayout()
        xy.setSpacing(sp(2))
        xy.addWidget(self.sp_x)
        ylab = QtWidgets.QLabel("Y:", self)
        ylab.setBuddy(self.sp_y)
        xy.addSpacing(sp(2))
        xy.addWidget(ylab)
        xy.addWidget(self.sp_y)
        xy.addStretch(1)
        form.addRow("X:", xy)
        sc = QtWidgets.QHBoxLayout()
        sc.setSpacing(sp(2))
        self.sp_scale = QtWidgets.QDoubleSpinBox(self)
        self.sp_scale.setRange(U.SCALE_RANGE[0] * 100, U.SCALE_RANGE[1] * 100)
        self.sp_scale.setDecimals(1)
        self.sp_scale.setSuffix(" %")
        self.sp_scale.setKeyboardTracking(False)
        self.sp_scale.setAccessibleName("Scale")
        self.sp_scale.setToolTip(
            "Its size: at 100 % one pixel of the page or picture is one pixel of this page"
        )
        self.sp_scale.valueChanged.connect(lambda v: self._apply(scale=float(v) / 100.0))
        sc.addWidget(self.sp_scale)
        self.btn_fit = QtWidgets.QPushButton("Fit Page", self)
        self.btn_fit.setAutoDefault(False)
        self.btn_fit.setToolTip("Make it as large as fits this page, centred on it")
        self.btn_fit.clicked.connect(lambda: self._win.fit_underlay(True))
        sc.addWidget(self.btn_fit)
        self.btn_actual = QtWidgets.QPushButton("Actual Size", self)
        self.btn_actual.setAutoDefault(False)
        self.btn_actual.setToolTip("100 % at the page's top left corner (0, 0)")
        self.btn_actual.clicked.connect(lambda: self._win.fit_underlay(False))
        sc.addWidget(self.btn_actual)
        sc.addStretch(1)
        form.addRow("Scale:", sc)

    def _build_options(self, lay: QtWidgets.QVBoxLayout) -> None:
        """The options: Snap to its objects, Draw its template, Smooth scaling, and Move
        with the Mouse with its note."""
        from ..style.theme import set_note_role, sp

        # -- the options
        self.cb_snap = QtWidgets.QCheckBox("Snap to its objects", self)
        self.cb_snap.setToolTip(
            "Objects you drag, draw or resize snap to the edges and centres of the underlaid page's objects"
        )
        self.cb_snap.toggled.connect(lambda on: self._apply(snap=on))
        lay.addWidget(self.cb_snap)
        self.cb_template = QtWidgets.QCheckBox("Draw its template", self)
        self.cb_template.setToolTip("Draw the underlaid page's template too (its title bar, menus and frame)")
        self.cb_template.toggled.connect(self._template_toggled)
        lay.addWidget(self.cb_template)
        self.cb_smooth = QtWidgets.QCheckBox("Smooth scaling", self)
        self.cb_smooth.setToolTip("Smooth its pixels when it is drawn larger or smaller. Off: sharp pixels")
        self.cb_smooth.toggled.connect(lambda on: self._apply(smooth=on))
        lay.addWidget(self.cb_smooth)
        mv = QtWidgets.QHBoxLayout()
        mv.setSpacing(sp(2))
        self.btn_move = QtWidgets.QPushButton("Move with the Mouse", self)
        self.btn_move.setObjectName("UnderlayMove")
        self.btn_move.setCheckable(True)
        self.btn_move.setAutoDefault(False)
        from ..style.theme import DARK

        # (pressed while it is on; the design colours: the stylesheet hook maps them to the
        # scheme shown)
        self.btn_move.setStyleSheet(
            "QPushButton#UnderlayMove:checked { background: %(selection)s; color: %(sel_text)s;"
            " border: 1px solid %(accent)s; }"
            'QPushButton#UnderlayMove[kbdFocus="true"] { border: 2px solid %(accent)s; }'
            % {"selection": DARK["selection"], "sel_text": DARK["text"], "accent": DARK["accent"]}
        )
        self.btn_move.setToolTip(
            "While it is on, a drag on the page moves the underlay, not "
            "the objects (also View > Underlay > Move Underlay with the "
            "Mouse)"
        )
        self.btn_move.clicked.connect(self._move_clicked)
        mv.addWidget(self.btn_move)
        mv.addStretch(1)
        lay.addLayout(mv)
        self.move_note = QtWidgets.QLabel(
            "Drag on the page to move it. On the page, the arrow keys move it 1 px (Shift "
            "10 px), Enter ends it where it is and Esc puts it back. Closing this window also "
            "ends it where it is.",
            self,
        )
        self.move_note.setWordWrap(True)
        set_note_role(self.move_note, "text_dim")
        lay.addWidget(self.move_note)

    # -- following the page --------------------------------------------------------------
    def set_document(self, doc) -> None:
        """Show the underlay of ``doc`` (the page tab shown)."""
        self._doc = doc
        name = ""
        if doc is not None:
            fn = getattr(self._win, "_doc_name", None)
            name = fn(doc) if callable(fn) else os.path.basename(getattr(doc, "path", ""))
            name = os.path.splitext(name)[0] if name.lower().endswith(".ctg") else name
        title = "Underlay - %s" % name if name else "Underlay"
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        self._load()

    def _load(self) -> None:
        from ..style.theme import set_note_role

        ul = U.peek(self._doc)
        self._loading = True
        try:
            has = ul is not None
            for w in self._controls:
                w.setEnabled(has)
            # (no underlay: the source and the way to choose one; how it shows and where it
            # lies come with a source)
            self.btn_change.setText("Change" if has else "Choose Underlay…")
            self.btn_remove.setVisible(has)
            for k in range(2, self._form.rowCount()):
                self._form.setRowVisible(k, has)
            for w in (self.cb_snap, self.cb_template, self.cb_smooth, self.btn_move):
                w.setVisible(has)
            self.btn_retry.setVisible(has and ul.status == "error")
            if not has:
                self.source.setText("No underlay")
                self.source.setToolTip("")
                self.note.setText(
                    "This page has no underlay. Choose a page, a picture or an "
                    "earlier version to draw under it."
                )
                set_note_role(self.note, "text_dim")
                self._show_new_values()
                self.btn_move.setChecked(False)
                self.move_note.setVisible(False)
                return
            self.source.setText(ul.name())
            self.source.setToolTip(ul.describe())
            if ul.status == "error":
                self.note.setText("It could not be drawn: %s." % ul.error.rstrip("."))
                set_note_role(self.note, "danger")
            elif ul.status == "loading":
                self.note.setText("%s. Drawing it…" % ul.kind_text())
                set_note_role(self.note, "text_dim")
            else:
                bw, bh = ul.base
                self.note.setText("%s, %d × %d px." % (ul.kind_text(), round(bw), round(bh)))
                set_note_role(self.note, "text_dim")
            self.cb_show.setChecked(ul.shown)
            self.cb_above.setChecked(ul.above)
            pct = int(round(ul.opacity * 100))
            self.sl_opacity.setValue(pct)
            self.sp_opacity.setValue(pct)
            k = self.cb_tint.findData(ul.tint)
            self.cb_tint.setCurrentIndex(max(0, k))
            for spin, v in ((self.sp_x, ul.x), (self.sp_y, ul.y), (self.sp_scale, ul.scale * 100.0)):
                if abs(spin.value() - v) > 1e-6:
                    spin.setValue(v)
            self.cb_snap.setChecked(ul.snap)
            self.cb_snap.setEnabled(ul.is_page())
            self.cb_snap.setToolTip(
                "Objects you drag, draw or resize snap to the edges and centres of the "
                "underlaid page's objects"
                if ul.is_page()
                else "Only a page underlay has objects to snap to"
            )
            self.cb_template.setChecked(ul.template)
            self.cb_template.setEnabled(ul.is_page())
            self.cb_smooth.setChecked(ul.smooth)
            can_move = ul.shown and ul.ready()
            self.btn_move.setEnabled(can_move)
            self.btn_move.setChecked(ul.moving)
            self.move_note.setVisible(ul.moving)
            for w in (self.sp_x, self.sp_y, self.sp_scale, self.btn_fit, self.btn_actual):
                w.setEnabled(ul.ready())
        finally:
            self._loading = False

    def _show_new_values(self) -> None:
        """The (greyed) controls show what a new underlay starts with, not the last page's
        values (they read as this page's)."""
        from ..prefs.settings import option

        self.cb_show.setChecked(True)
        self.cb_above.setChecked(option(U.ABOVE_OPTION))
        pct = U.default_opacity()
        self.sl_opacity.setValue(pct)
        self.sp_opacity.setValue(pct)
        self.cb_tint.setCurrentIndex(max(0, self.cb_tint.findData(U.default_tint())))
        self.sp_x.setValue(0.0)
        self.sp_y.setValue(0.0)
        self.sp_scale.setValue(100.0)
        self.cb_snap.setChecked(option(U.SNAP_OPTION))
        self.cb_template.setChecked(False)
        self.cb_smooth.setChecked(True)

    def underlay(self) -> Optional[U.Underlay]:
        return U.peek(self._doc)

    # -- changes -------------------------------------------------------------------------
    def _apply(self, **values) -> None:
        if self._loading:
            return
        ul = U.peek(self._doc)
        if ul is None or self._doc is not getattr(self._win, "doc", None):
            return
        changed = False
        for k, v in values.items():
            if getattr(ul, k) != v:
                setattr(ul, k, v)
                changed = True
        if not changed:
            return
        if "shown" in values and not ul.shown:
            self._win.stop_underlay_moving(ul)
        ul.touch()
        self._win.underlay_changed()

    def _opacity_slid(self, v: int) -> None:
        if not self._loading:
            self._loading = True
            try:
                self.sp_opacity.setValue(v)
            finally:
                self._loading = False
        self._apply(opacity=v / 100.0)

    def _opacity_typed(self, v: int) -> None:
        if not self._loading:
            self._loading = True
            try:
                self.sl_opacity.setValue(v)
            finally:
                self._loading = False
        self._apply(opacity=v / 100.0)

    def _template_toggled(self, on: bool) -> None:
        if self._loading:
            return
        ul = U.peek(self._doc)
        if ul is None or ul.template == bool(on):
            return
        ul.template = bool(on)
        self._win.reload_underlay()

    def _move_clicked(self, _checked: bool = False) -> None:
        # (the command does it: View > Underlay > Move Underlay with the Mouse)
        if self._loading:
            return
        a = self._win.act_underlay_move
        if a.isEnabled():
            a.trigger()
        self._load()

    def hideEvent(self, e: QtGui.QHideEvent) -> None:  # noqa: N802 -- Qt naming
        # (closed with Close, Esc or the title bar: Move with the Mouse ends with the
        # underlay where it is now)
        ul = U.peek(self._doc)
        if ul is not None and ul.moving:
            try:
                if self._doc is getattr(self._win, "doc", None):
                    self._win.finish_underlay_move()
            except RuntimeError:  # swallow-ok: the editor window is being destroyed
                pass
        super().hideEvent(e)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        # (Enter applies a typed number; it never closes the window)
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            w = self.focusWidget()
            if isinstance(w, QtWidgets.QAbstractSpinBox):
                w.interpretText()
            elif w is not None and isinstance(w.parentWidget(), QtWidgets.QAbstractSpinBox):
                w.parentWidget().interpretText()
            e.accept()
            return
        super().keyPressEvent(e)


class VersionPickerDialog(QtWidgets.QDialog):
    """Select Version (View > Underlay > Underlay an Earlier Version…): the page's earlier
    versions, newest first, with their pictures; Underlay takes the one picked."""

    def __init__(self, versions, page: str, parent=None):
        super().__init__(parent)
        from ..browser.thumbs import ROW_SIZE, bmp_to_qimage, fit_image
        from ..dialog.history_versions import size_text
        from ..tables import make_resizable

        self.versions = list(versions)
        self.setWindowTitle("Select Version")
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("The versions of %s saved before, newest first." % page, self)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setAccessibleName("Earlier versions")
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Saved", "Objects", "Size"])
        self.tree.setIconSize(ROW_SIZE)
        make_resizable(self.tree, "VersionPickerDialog/versions", stretch=0)
        from ...format import pagesummary as PS

        for v in self.versions:
            it = QtWidgets.QTreeWidgetItem([v.when, str(v.objects) if v.objects else "", size_text(v.size)])
            try:
                img = bmp_to_qimage(PS.read_page_summary(v.file, template_scan=0).thumb_bmp)
            except Exception:  # noqa: BLE001  # swallow-ok: a version without a picture
                img = QtGui.QImage()
            if not img.isNull():
                it.setIcon(0, QtGui.QIcon(QtGui.QPixmap.fromImage(fit_image(img, ROW_SIZE))))
            it.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            it.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        self.tree.itemActivated.connect(lambda *_a: self.accept())
        lay.addWidget(self.tree, 1)
        bb = QtWidgets.QDialogButtonBox(self)
        self.ok = bb.addButton("Underlay", QtWidgets.QDialogButtonBox.AcceptRole)
        self.ok.setDefault(True)
        bb.addButton(QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(560, 360)
        if self.versions:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def current(self):
        it = self.tree.currentItem()
        k = self.tree.indexOfTopLevelItem(it) if it is not None else -1
        return self.versions[k] if 0 <= k < len(self.versions) else None
