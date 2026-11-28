"""The format painter and the named styles."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..names import plural

from ..main_window_base import (
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    os,
)
from ..main_window_base import (
    TOOLS,
)
from ..messages import warn


class FormatPainter:
    """Copying an appearance from one object to others, and the named styles."""

    # ------------------------------------------------ page find / print / view
    # -- the format painter and named styles ---------------------------------------------
    def copy_appearance(self) -> None:
        """Edit > Copy Appearance: the selected object's look (line, fill, 3D, font, colours)
        kept for Paste Appearance; the first selected object when several are."""
        if self.doc is None or not self._sel:
            return
        i = self._sel[0]
        self._appearance = self.doc.appearance_of(i)
        from ..names import kind_name

        self.statusBar().showMessage(
            "Copied the appearance of the %s (Paste Appearance puts it "
            "on the selection)." % kind_name(self.doc.page.objects[i]).lower(),
            5000,
        )
        self._update_actions()

    def paste_appearance(self) -> None:
        """Edit > Paste Appearance: the copied look onto every selected object, one undo step."""
        app = getattr(self, "_appearance", None)
        if self.doc is None or not self._sel or not app:
            return
        n = self.doc.apply_appearance(list(self._sel), app)
        self._refresh()
        self.statusBar().showMessage(
            "Pasted the appearance onto %s." % (plural(n, "object"))
            if n
            else "Nothing on the selection takes that appearance.",
            5000,
        )

    # (the named styles, Edit > Styles: ui/panels/styles_gallery.StyleCommands)

    #: the PNG scales Export Page offers (percent)
    EXPORT_SCALES = (100, 150, 200, 300)

    def _export_page(self) -> None:
        """File > Export Page: a picture of the page as it is (no editor marks, the static
        page) as a PNG, SVG or PDF file: for a document, a review, a web page. PNG at the
        remembered scale (Settings are asked once, kept in export/png_scale)."""
        if self.doc is None:
            return
        w = self.doc.page.appearance.width or 1024
        h = self.doc.page.appearance.height or 768
        name = os.path.splitext(os.path.basename(self.doc.path or ""))[0] or "page"
        filters = "PNG picture (*.png);;SVG drawing (*.svg);;PDF document (*.pdf)"
        path, chosen = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Page", os.path.join(self._dialog_dir("export"), name + ".png"), filters
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".svg", ".pdf"):
            ext = ".svg" if "SVG" in chosen else ".pdf" if "PDF" in chosen else ".png"
            from ..messages import with_extension

            path = with_extension(self, "Export Page", path, ext)
            if not path:
                return
        scale = 1.0
        if ext == ".png":
            st = self._settings()
            last = int(st.value("export/png_scale", 100) or 100)
            items = ["%d %%" % s for s in self.EXPORT_SCALES]
            cur = items.index("%d %%" % last) if last in self.EXPORT_SCALES else 0
            from ..messages import ask_item

            pick = ask_item(self, "Export Page", "Scale (%d x %d page pixels):" % (w, h), items, cur)
            if pick is None:
                return
            scale = int(pick.split()[0]) / 100.0
            st.setValue("export/png_scale", int(pick.split()[0]))
        # (written beside it first and put in its place once whole: a file there from
        # before stays as it was when the export fails, and never passes for this one)
        tmp = os.path.splitext(path)[0] + ".~export" + ext
        try:
            try:
                self._render_page_to(tmp, ext, w, h, scale)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except Exception as exc:  # noqa: BLE001 -- said to the user
            from ...infra import logsetup

            logsetup.note_exception("export_page")
            warn(self, "Export Page", "The page could not be exported. %s" % exc)
            return
        self._remember_dir("export", path)
        self.statusBar().showMessage(
            "Exported %s (%s, %d x %d)." % (path, ext[1:].upper(), int(w * scale), int(h * scale)), 8000
        )

    def _render_page_to(self, path: str, ext: str, w: int, h: int, scale: float) -> None:
        """The static page rendered into a PNG (at ``scale``), an SVG or a PDF file."""
        src = QtCore.QRectF(0, 0, w, h)
        with self.scene.export_only(None):  # the page, no editor grid or marks
            if ext == ".png":
                img = QtGui.QImage(int(w * scale), int(h * scale), QtGui.QImage.Format_ARGB32_Premultiplied)
                img.fill(QtCore.Qt.transparent)
                painter = QtGui.QPainter(img)
                try:
                    painter.setRenderHints(
                        QtGui.QPainter.Antialiasing
                        | QtGui.QPainter.TextAntialiasing
                        | QtGui.QPainter.SmoothPixmapTransform
                    )
                    self.scene.render(painter, QtCore.QRectF(0, 0, w * scale, h * scale), src)
                finally:
                    painter.end()
                if not img.save(path, "PNG"):
                    raise OSError("the file could not be written: %s" % path)
            elif ext == ".svg":
                from PySide6 import QtSvg

                gen = QtSvg.QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(QtCore.QSize(w, h))
                gen.setViewBox(QtCore.QRect(0, 0, w, h))
                gen.setTitle(os.path.splitext(os.path.basename(path))[0])
                gen.setDescription("Exported by ctgedit")
                painter = QtGui.QPainter(gen)
                if not painter.isActive():
                    raise OSError("the file could not be written: %s" % path)
                try:
                    self.scene.render(painter, src, src)
                finally:
                    ended = painter.end()
                self._written(path, ended)
            else:
                writer = QtGui.QPdfWriter(path)
                writer.setResolution(96)
                writer.setPageSize(
                    QtGui.QPageSize(
                        QtCore.QSizeF(w * 25.4 / 96.0, h * 25.4 / 96.0), QtGui.QPageSize.Millimeter
                    )
                )
                writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
                painter = QtGui.QPainter(writer)
                if not painter.isActive():
                    raise OSError("the file could not be written: %s" % path)
                try:
                    target = QtCore.QRectF(painter.viewport())
                    self.scene.render(painter, target, src)
                finally:
                    ended = painter.end()
                del writer  # (the PDF is finished and closed here)
                self._written(path, ended)

    @staticmethod
    def _written(path: str, ended: bool) -> None:
        """An export is reported done only when its file is there: a painter that could
        not open or finish the file (a folder gone, no right to write) raises."""
        if not ended or not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise OSError("the file could not be written: %s" % path)

    def print_orientation(self):
        """The orientation File > Print starts in (Settings > Pages and libraries 'Print pages
        in'): Landscape (the default), Portrait, the page's shape (portrait when it is taller
        than wide) or the last print's."""
        from ..prefs.settings import PRINT_ORIENTATIONS, one_of

        mode = one_of("opt/print_orientation", PRINT_ORIENTATIONS)
        if mode == "last":
            mode = one_of("opt/print_last_orientation", ("landscape", "portrait"))
        elif mode == "page":
            ap = self.doc.page.appearance if self.doc is not None else None
            w, h = (ap.width or 0, ap.height or 0) if ap is not None else (0, 0)
            mode = "portrait" if h > w > 0 else "landscape"
        return QtGui.QPageLayout.Portrait if mode == "portrait" else QtGui.QPageLayout.Landscape

    def _print_page(self) -> None:
        if self.doc is None:
            return
        from PySide6 import QtPrintSupport

        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        printer.setPageOrientation(self.print_orientation())
        dlg = QtPrintSupport.QPrintDialog(printer, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        # (Settings 'Print pages in: The last print's choice' starts the next one so)
        landscape = printer.pageLayout().orientation() == QtGui.QPageLayout.Landscape
        app_settings().setValue("opt/print_last_orientation", "landscape" if landscape else "portrait")
        painter = QtGui.QPainter(printer)
        try:
            w = self.doc.page.appearance.width or 1024
            h = self.doc.page.appearance.height or 768
            target = painter.viewport()
            scale = min(target.width() / w, target.height() / h)
            with self.scene.export_only(None):  # page, no editor grid
                self.scene.render(
                    painter, QtCore.QRectF(0, 0, w * scale, h * scale), QtCore.QRectF(0, 0, w, h)
                )
        finally:
            painter.end()
        self.statusBar().showMessage("Page sent to printer.", 4000)

    def _grid_setup_dialog(self) -> None:
        """View > Grid Setup: a modeless window; Apply / OK change the grid
        at once (editor-only, no undo step)."""
        if self._raise_modeless(self._dialogs.get("grid")):
            self._sync_grid_dialog()
            return
        from ..dialog.dialogs import GridSetupDialog

        dlg = GridSetupDialog(
            self._grid_size,
            self._show_grid,
            self.act_snap.isChecked(),
            self,
            pitch_y=self._grid_pitch_y(),
            origin=self._grid_origin,
        )
        dlg.applyRequested.connect(lambda d=dlg: self._apply_grid_setup(d))
        self._show_modeless(dlg, "grid", scope="window")

    def _apply_grid_setup(self, dlg) -> None:
        """Apply / OK of Grid Setup."""
        _pitch, display, snap = dlg.values()
        self._apply_grid(*dlg.grid())
        if hasattr(dlg, "look"):
            major, style = dlg.look()
            st = app_settings()
            st.setValue("view/grid_major", int(major))
            st.setValue("view/grid_style", style)
            self.apply_canvas_look()
        self._show_grid = display
        self.act_grid.setChecked(display)
        self.act_snap.setChecked(snap)
        self.view.set_snap(snap)
        dlg.mark_applied()
        self._refresh()

    def _sync_grid_dialog(self) -> None:
        """The open Grid Setup follows View > Show Grid and Snap to Grid (F8), the grid's
        look (Settings > Canvas) while its own look is not changed, and the grid itself
        while nothing is typed."""
        dlg = self._dialogs.get("grid")
        if dlg is None:
            return
        from ..prefs.settings import canvas_look

        try:
            if dlg.has_pending_edits():
                dlg.set_display(self._show_grid)
                dlg.set_snap(self.act_snap.isChecked())
                if hasattr(dlg, "set_look"):
                    dlg.set_look(canvas_look())
            else:
                dlg.load(
                    self._grid_size,
                    self._show_grid,
                    self.act_snap.isChecked(),
                    pitch_y=self._grid_pitch_y(),
                    origin=self._grid_origin,
                    look=canvas_look(),
                )
        except RuntimeError:
            pass

    def _grid_pitch_y(self) -> int:
        return self._grid_size if self._grid_y is None else self._grid_y

    def _grid_spec(self) -> tuple:
        """(pitch x, pitch y, origin x, origin y) -- what the scene draws."""
        return (self._grid_size, self._grid_pitch_y()) + tuple(self._grid_origin)

    def _apply_grid(self, pitch_x: int, pitch_y: int | None = None, ox: int = 0, oy: int = 0) -> None:
        """The grid the canvas snaps to and the scene draws (editor-only)."""
        self._grid_size = max(1, int(pitch_x))
        self._grid_y = None if pitch_y is None or int(pitch_y) == self._grid_size else max(1, int(pitch_y))
        self._grid_origin = (int(ox), int(oy))
        self.view.set_grid(self._grid_size, self._grid_y, self._grid_origin)
        self._sync_grid_menu()

    def _toggle_zoom_window(self) -> None:
        from ..dialog.dialogs import ZoomWindow

        if getattr(self, "_zoom_win", None) is None:
            self._zoom_win = ZoomWindow(self.view, self)
            self._zoom_win.installEventFilter(self)
        if self.act_zoom_window.isChecked():
            self._zoom_win.show()
        else:
            self._zoom_win.hide()

    def _toggle_toolbox(self) -> None:
        """View > Show Tools (F3): CtDraw's floating drawing toolbox."""
        if getattr(self, "_toolbox", None) is None:
            from ..editor.toolbox import ToolboxWindow

            # (in groups: drawing tools, text and data, place, inserted controls)
            groups = (
                (
                    "Draw",
                    (
                        "select",
                        "line",
                        "freehand",
                        "pipe",
                        "rectangle",
                        "rectangle_outline",
                        "ellipse",
                        "ellipse_outline",
                        "polygon",
                        "measure",
                    ),
                ),
                ("Text and data", ("text", "number", "button")),
                ("Place", ("symbol", "symbol_set")),
            )
            placed = {t for _h, ids in groups for t in ids}
            items = []
            for heading, ids in groups:
                items.append(heading)
                items += [self.tool_actions[t] for t in ids if t in self.tool_actions]
                if heading == "Draw":  # (a tool no group names: with the drawing tools)
                    items += [self.tool_actions[t] for t, _l, _i, _k in TOOLS if t not in placed]
            items.append("Controls")
            items += list(self.modern_v34_actions) + list(self.insert_actions)
            self._toolbox = ToolboxWindow(items, self)
            from .. import help as _help

            _help.set_help(self._toolbox, "the-window#the-drawing-toolbox")
            geo = app_settings().value("view/toolbox_geometry")
            if geo is not None:
                self._toolbox.restoreGeometry(geo)
            else:
                # beside the canvas's top-left corner
                self._toolbox.move(self.view.mapToGlobal(QtCore.QPoint(12, 12)))
            self._toolbox.installEventFilter(self)
        if self.act_show_tools.isChecked():
            self._toolbox.show()
            self._toolbox.raise_()
        else:
            self._toolbox.hide()

    def _toggle_status_bar(self) -> None:
        """View > Show Status Bar (help 1172084)."""
        self.statusBar().setVisible(self.act_status_bar.isChecked())

    def _refresh_view(self) -> None:
        """View > Refresh: re-read libraries and redraw."""
        from ...editing import libraries_changed

        self._drop_art_caches()
        libraries_changed()
        wins = [self] + [w for w in (type(self).instances or ()) if w is not self]
        for w in wins:
            try:
                w._refresh_own_pages()
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
        self._look_at_open_genies_again()

    def _refresh_own_pages(self) -> None:
        """This window's part of View > Refresh (the caches are emptied already): the shown
        page is drawn again in full, the other tabs when shown next, the Library panel
        re-lists."""
        for e in self._docs:
            # (a full derive: a library change alone derives only the units it reached)
            if hasattr(e["doc"], "_derivation"):
                e["doc"]._derivation = None
            if e["doc"] is not self.doc:
                e["stale"] = True
        if self.doc is not None:
            try:
                self.doc._rerender_page()
            except Exception as ex:  # noqa: BLE001 -- keep showing what we have
                _note_exc("commands._refresh_own_pages")
                self.statusBar().showMessage(
                    "The page could not be redrawn from its libraries: %s" % ex, 8000
                )
                return
            self._refresh()
            self._update_diag_chips()
            from ...infra import logsetup

            logsetup.get("open").info("View > Refresh: %s redrawn from its files", self.doc.path)
        with contextlib.suppress(Exception):
            self.library_dock.refresh()
        self.view.viewport().update()
        self.statusBar().showMessage("Redrawn. The page's libraries were read again.", 4000)
