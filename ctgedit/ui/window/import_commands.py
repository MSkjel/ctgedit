"""Importing pictures and pages; the bitmap and clipboard commands."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..names import plural

from ..main_window_base import (
    CLIPBOARD_MIME,
    PASTE_POINT_UNREAD,
    QtCore,
    QtGui,
    QtWidgets,
    os,
)
from ..messages import warn


class ImportCommands:
    """File > Import, pictures dropped or pasted, and the other import commands."""

    # ------------------------------------------------ import / bitmap / misc
    def _qimage_to_rgba(self, img: QtGui.QImage):
        img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
        ptr = img.constBits()
        return bytes(memoryview(ptr))[: img.width() * img.height() * 4], img.width(), img.height()

    def _import_image(self) -> None:
        """File > Import…: pictures -> embedded bitmaps (every format this Qt
        reads) or MetaFile objects (.wmf), placed in the middle of the page."""
        if self.doc is None:
            return
        from ..dialog.bitmap_import import import_filter

        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Import", self._dialog_dir("import"), import_filter()
        )
        if not paths:
            return
        self._remember_dir("import", paths[0])
        self._import_files(paths)

    def _on_files_dropped(self, paths: list, pos: QtCore.QPointF) -> None:
        """Picture files dragged from Explorer onto the page (CtDraw's drag
        import): each placed with its top-left at the drop point."""
        if self.doc is None:
            self.statusBar().showMessage("Open a page first, then drop pictures on it.", 5000)
            return
        self._import_files(paths, (int(pos.x()), int(pos.y())), trigger="drop")

    def _import_recent(self, path: str) -> None:
        """File > Import Recent: import that picture again."""
        if self.doc is None:
            return
        if not os.path.isfile(path):
            self.statusBar().showMessage("%s is no longer there." % path, 6000)
            return
        self._import_files([path])

    def _fill_import_recent(self) -> None:
        """File > Import Recent: the favourite pictures, then the ones imported last."""
        from ..dialog.bitmap_import import clear_recent, favourite_pictures, recent_pictures

        m = self._import_recent_menu
        m.clear()
        favs = favourite_pictures()
        recent = [
            p for p in recent_pictures() if not any(os.path.normcase(p) == os.path.normcase(f) for f in favs)
        ]
        if favs:
            m.addSection("Favourites")
            for p in favs:
                a = m.addAction(os.path.basename(p), lambda path=p: self._import_recent(path))
                a.setStatusTip(p)
        if recent:
            m.addSection("Recent")
            for p in recent:
                a = m.addAction(os.path.basename(p), lambda path=p: self._import_recent(path))
                a.setStatusTip(p)
        if not favs and not recent:
            a = m.addAction("No pictures imported yet")
            a.setStatusTip("The pictures imported last are listed here.")
            a.setEnabled(False)
        m.addSeparator()
        a = m.addAction("Clear Recent Pictures", clear_recent)
        a.setStatusTip("Forget the pictures imported last (the favourites stay).")
        a.setEnabled(bool(recent))
        for a in m.actions():
            if a.text() not in ("Clear Recent Pictures", "No pictures imported yet") and not a.isSeparator():
                a.setEnabled(self.doc is not None)

    def _visible_size(self):
        """The visible part of the page (Fit the visible page)."""
        pw = self.doc.page.appearance.width or 1024
        ph = self.doc.page.appearance.height or 768
        try:
            r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            return max(16, min(pw, int(r.width()))), max(16, min(ph, int(r.height())))
        except Exception:  # noqa: BLE001 -- swallow-ok: no view yet, the page size
            return pw, ph

    def _pictures_from(self, sources, trigger: str, failed: list):
        """The Pictures for ``sources``: through the picture options when they open for
        ``trigger``, else CtDraw's import (their own size, the Transparent Paste colour).
        None = the options were cancelled."""
        from ..dialog.bitmap_import import PictureError, build, plain_options, transparent_colour
        from ..dialog.picture_import import asks, choose

        if not sources:
            return []
        if asks(trigger):
            try:
                return choose(
                    self, sources, fit=self._visible_size(), action="Paste" if trigger == "paste" else "Place"
                )
            except PictureError as e:
                failed.append(str(e))
                return []
        key = transparent_colour()
        out = []
        for s in sources:
            try:
                out.append(build(s, plain_options(s, key)))
            except PictureError as e:
                failed.append(str(e))
        return out

    def _place_point(self, at, w: int, h: int):
        """Top-left for a new picture: ``at`` (grid-snapped when snapping is on),
        else the middle of the visible part of the page."""
        if at is not None:
            x, y = int(at[0]), int(at[1])
            # (an underlaid page's object corner near it catches it, else the grid)
            snap = getattr(self, "act_snap", None)
            return self.view.placement_point(x, y, grid=bool(snap is not None and snap.isChecked()))
        pw = self.doc.page.appearance.width or 1024
        ph = self.doc.page.appearance.height or 768
        try:
            c = self.view.mapToScene(self.view.viewport().rect().center())
            cx, cy = int(c.x()), int(c.y())
        except Exception:  # noqa: BLE001
            _note_exc("commands._place_point")
            cx, cy = pw // 2, ph // 2
        if not (0 <= cx <= pw and 0 <= cy <= ph):
            cx, cy = pw // 2, ph // 2
        return max(0, cx - w // 2), max(0, cy - h // 2)

    def _place_picture(self, pic, at=None, flash=None) -> int:
        """Add one Picture (bitmap_import) to the page; returns its index."""
        w, h = pic.size
        x, y = self._place_point(at, w, h)
        rect = (x, y, x + w, y + h)
        if pic.kind == "metafile":
            return self.doc.add_metafile_object(rect, pic.data)
        return self.doc.add_bitmap_object(rect, pic.data, pic.width, pic.height, flash_rgba=flash)

    def _import_files(self, paths, at=None, trigger: str = "import") -> list:
        """Import picture files (all of them one undo step), through the picture
        options when Settings > Canvas opens them for ``trigger`` ('import',
        'drop'); a file that cannot be read is reported by name with the reason.
        Returns the new indices."""
        from ..dialog.bitmap_import import PictureError, add_recent, open_source

        sources, failed = [], []
        for path in paths:
            try:
                sources.append(open_source(path))
            except PictureError as e:
                failed.append(str(e))
        pics = self._pictures_from(sources, trigger, failed)
        if pics is None:
            return []
        new = []
        # several files dropped at once: each the paste offset further (Settings > Canvas)
        step = int(getattr(self, "_paste_offset", 10))
        with self.doc.batch("import"):  # several files: one undo step
            for n, pic in enumerate(pics):
                where = None if at is None else (at[0] + step * n, at[1] + step * n)
                new.append(self._place_picture(pic, where))
        add_recent([p.path for p in pics if p.path])
        if new:
            self._sel = new
            self._refresh()
            self.statusBar().showMessage("Imported %s." % plural(len(new), "picture"), 4000)
        if failed:
            from ..messages import list_box

            list_box(
                self,
                "Import",
                "%s could not be imported." % (plural(len(failed), "file")),
                failed,
            )
        return new

    def _import_flashing(self) -> None:
        """File > Import As Flashing…: a primary picture, then the flashing
        one (stretched to the primary's size) -> one two-DIB flashing bitmap
        (the runtime alternates the DIBs). A .wmf is drawn to a bitmap here."""
        if self.doc is None:
            return
        from ..dialog.bitmap_import import PictureError, import_filter, read_picture, transparent_colour

        flt = import_filter()
        p1, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Primary", self._dialog_dir("import"), flt
        )
        if not p1:
            return
        self._remember_dir("import", p1)
        p2, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Flashing (shown alternately with the primary)", self._dialog_dir("import"), flt
        )
        if not p2:
            return
        key = transparent_colour()
        try:
            pic1 = read_picture(p1, key, raster_only=True)
            pic2 = read_picture(p2, key, raster_only=True)
        except PictureError as e:
            warn(self, "Import As Flashing", str(e))
            return
        from ...format.bitmaps import resample

        # CtDraw stretches the flash picture to the primary's size
        rgba2 = resample(pic2.data, pic2.width, pic2.height, pic1.width, pic1.height)
        idx = self._place_picture(pic1, flash=rgba2)
        self._sel = [idx]
        self._refresh()
        self.statusBar().showMessage("Imported a flashing bitmap (the page shows its primary picture).", 6000)

    def _pick_bitmap_element(self, title: str):
        """A library BITMAP symbol from the page's include chain (the Library
        browser, thumbnails): its EmbeddedBitmap and 'lib.element', or None."""
        from ..browser import PageBrowserDialog
        from ...format.symbols import open_library
        from ...format import bitmaps

        pick = PageBrowserDialog.get_library_element(
            self, os.path.dirname(os.path.abspath(self.doc.path)), "symbol", title=title
        )
        if pick is None:
            return None
        path, element = pick
        name = "%s.%s" % (os.path.splitext(os.path.basename(path))[0], element)
        try:
            objs = open_library(path).element_objects(element) or []
        except Exception:  # noqa: BLE001
            _note_exc("commands._pick_bitmap_element")
            objs = []
        for po in objs:
            primary, _flash = bitmaps.read_pictures(po)
            if primary is not None:
                return primary, name
        warn(
            self,
            "Paste Symbol As Flashing",
            "%s is not a bitmap symbol (it is drawn with shapes). Paste Symbol As "
            "Flashing needs two bitmap symbols, as in the Graphics Builder." % name,
        )
        return False

    def _paste_symbol_flashing(self) -> None:
        """Edit > Paste Symbol As Flashing…: the primary BITMAP symbol, then
        the flashing one (stretched to the primary's size) -> one embedded
        two-DIB flashing bitmap (vector symbols are refused, as in CtDraw)."""
        if self.doc is None:
            return
        first = self._pick_bitmap_element("Select Primary Symbol")
        while first is False:
            first = self._pick_bitmap_element("Select Primary Symbol")
        if first is None:
            return
        second = self._pick_bitmap_element("Select Flashing Symbol (shown alternately with %s)" % first[1])
        while second is False:
            second = self._pick_bitmap_element("Select Flashing Symbol")
        if second is None:
            return
        from ...format.bitmaps import resample
        from ..dialog.bitmap_import import Picture

        eb1, eb2 = first[0], second[0]
        rgba2 = resample(eb2.rgba, eb2.width, eb2.height, eb1.width, eb1.height)
        idx = self._place_picture(Picture("bitmap", eb1.rgba, eb1.width, eb1.height), flash=rgba2)
        self._sel = [idx]
        self._refresh()
        self.statusBar().showMessage("Pasted %s flashing with %s" % (first[1], second[1]), 5000)

    def _clipboard_has_objects(self, mime) -> bool:
        return mime is not None and (
            mime.hasFormat(CLIPBOARD_MIME) or (mime.hasText() and "ctgedit_objects" in mime.text())
        )

    def _paste_command(self) -> None:
        """Edit > Paste (Ctrl+V): the objects ctgedit copied; else a picture
        another program copied (CtDraw: 'Edit | Paste' of a bitmap) or copied
        picture files."""
        if self.doc is None:
            return
        # (Settings > Canvas 'Paste puts the objects: At the pointer': read once, before a
        # picture options window can open over the canvas)
        at = self.paste_point()
        mime = QtWidgets.QApplication.clipboard().mimeData()
        if self._clipboard_has_objects(mime):
            self.paste_clipboard(at=at)
            return
        from ..dialog.bitmap_import import dropped_files
        from ...infra import emf

        if mime is not None and mime.hasImage() or emf.clipboard_has_emf():
            self._paste_image(mime, at=at)
            return
        files = dropped_files(mime)
        if files:
            # (copied picture files land there too)
            self._import_files(files, at=at)
            return
        self.paste_clipboard(at=at)

    def _paste_image(self, mime, drawing=None, options: bool = False, at=PASTE_POINT_UNREAD):
        """Paste an image from another program as an embedded bitmap."""
        from ..dialog.bitmap_import import PictureError, clipboard_source
        from ..dialog.picture_import import choose, paste_kind

        if at is PASTE_POINT_UNREAD:
            # (now: the picture options window can be under the pointer once it closes)
            at = self.paste_point()
        if drawing is None:
            drawing = paste_kind() == "drawing"
        try:
            src = clipboard_source(mime, drawing)
            if src is None and not drawing:
                src = clipboard_source(mime, True)  # only a drawing was copied
        except PictureError as e:
            warn(self, "Paste", str(e))
            return None
        if src is None:
            self.statusBar().showMessage("The clipboard picture could not be read.", 4000)
            return None
        failed: list = []
        if options:
            try:
                pics = choose(self, [src], fit=self._visible_size(), action="Paste")
            except PictureError as e:
                failed.append(str(e))
                pics = []
        else:
            pics = self._pictures_from([src], "paste", failed)
        if failed:
            from ..messages import list_box

            list_box(self, "Paste", "The picture could not be pasted.", failed)
        if not pics:
            return None
        pic = pics[0]
        idx = self._place_picture(pic, at=at)
        self._sel = [idx]
        self._refresh()
        w, h = pic.size
        self.statusBar().showMessage(
            "Pasted a %d x %d %s" % (w, h, "drawing" if pic.kind == "metafile" else "bitmap"), 4000
        )
        return idx

    def _paste_special(self) -> None:
        """Edit > Paste Special…: choose between object and image formats."""
        if self.doc is None:
            return
        # (Settings > Canvas 'Paste puts the objects: At the pointer': where the pointer is
        # now, not where it is when the window closes)
        at = self.paste_point()
        cb = QtWidgets.QApplication.clipboard()
        mime = cb.mimeData()
        formats = []
        if self._clipboard_has_objects(mime):
            formats.append("AVEVA Plant SCADA Graphics Object(s)")
        from ...infra import emf

        if mime is not None and mime.hasImage():
            formats.append("Device Independent Bitmap")
        if emf.clipboard_has_emf():
            formats.append(self.PASTE_DRAWING)
        if (mime is not None and mime.hasImage()) or emf.clipboard_has_emf():
            formats.append(self.PASTE_WITH_OPTIONS)
        if not formats:
            self.statusBar().showMessage("Nothing on the clipboard can be pasted.", 4000)
            return
        from ..dialog.dialogs import PasteSpecialDialog

        dlg = PasteSpecialDialog(formats, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        chosen = formats[dlg.choice()]
        if chosen.startswith("AVEVA"):
            self.paste_clipboard(at=at)
            return
        if chosen == self.PASTE_WITH_OPTIONS:
            self._paste_image(mime, options=True, at=at)
            return
        self._paste_image(mime, drawing=chosen == self.PASTE_DRAWING, at=at)

    #: Paste Special's other picture formats: another program's drawing (an
    #: enhanced metafile, placed as a metafile object), and the picture through the
    #: picture options (size, transparency: a screenshot's background made clear)
    PASTE_DRAWING = "Enhanced Metafile (a drawing that stays sharp)"
    PASTE_WITH_OPTIONS = "Picture, choosing its size and transparency…"

    def _transparent_paste_dialog(self) -> None:
        from ..dialog.bitmap_import import TransparentPasteDialog

        TransparentPasteDialog(self).exec()

    def _convert_to_bitmap(self) -> None:
        """Tools > Convert to Bitmap: rasterise the selection in place."""
        if self.doc is None or not self._sel:
            return
        sel = self._filter_template(self._sel, "convert")
        if not sel:
            return
        # the units' Bounds (pen width / 3D edges included), not a subtree
        # union that drags in behaviour-record icon boxes
        l, t, r, b = self.doc.selection_extent(sel, bounds=True)
        w, h = max(1, r - l), max(1, b - t)
        img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
        img.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # only the selection: no page background, grid or other objects
        with self.scene.export_only(self.doc.expand_to_subtrees(sel)):
            self.scene.render(painter, QtCore.QRectF(0, 0, w, h), QtCore.QRectF(l, t, w, h))
        painter.end()
        rgba, w, h = self._qimage_to_rgba(img)
        new = self.doc.convert_to_bitmap(sel, (l, t, r, b), rgba, w, h)
        self._sel = [new]
        self._refresh()

    def _options_dialog(self) -> None:
        from ..prefs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        try:
            dlg.exec()  # applies + persists on OK
        finally:
            # (a closed Settings window would stay a hidden child of the editor: every
            # theme change after it would restyle its many widgets too)
            dlg.deleteLater()

    def _print_setup(self) -> None:
        from PySide6 import QtPrintSupport

        if getattr(self, "_printer", None) is None:
            self._printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        QtPrintSupport.QPageSetupDialog(self._printer, self).exec()

    def _launch_external(self, exe: str) -> None:
        import glob as _glob
        from ...infra import install

        for base in install.tool_dirs():
            b = _glob.escape(base)
            for hit in _glob.glob(os.path.join(b, exe)) + _glob.glob(os.path.join(b, "*", exe)):
                QtCore.QProcess.startDetached(hit)
                return
        self.statusBar().showMessage("%s not found in the Plant SCADA install." % exe, 5000)

    def _about(self) -> None:
        """Help > About: version, commit and date of this build (+ Python / Qt
        and the log folder), with Copy Details for a bug report."""
        from ..dialog.about import AboutDialog

        AboutDialog(self).exec()

    def _check_updates(self) -> None:
        """Help > Check for Updates: a newer ctgedit on GitHub, downloaded and installed
        (the installed ctgedit; run from source, its release page)."""
        from ..dialog.updates import run_update_dialog

        run_update_dialog(self)

    def _toggle_crosshair(self) -> None:
        """View > Cross Hair Cursor (help 1172089): lines through the pointer
        across the whole drawing area (the canvas draws them)."""
        self.view.set_crosshair(self.act_crosshair.isChecked())
