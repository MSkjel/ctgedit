"""Colour tools, the bitmap editor, and replacing, exporting or resampling a picture."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    QtCore,
    ElementDocument,
    QtGui,
    QtWidgets,
    os,
)
from ..messages import warn


class PictureTools:
    """The page's colours and pictures."""

    # ------------------------------------------------------- colour tools
    def _colour_tool_targets(self, scope: str = "selection"):
        """The objects Swap / Adjust Colours change."""
        if self.doc is None:
            return None
        if scope != "page" and self._sel:
            idx = self._filter_template(self._sel, "recolour")
            return idx or None
        roots = [i for i, o in enumerate(self.doc.page.objects) if o.parent is None]
        return self._filter_template(roots, "recolour", quiet=True) or None

    def _colour_count(self, doc, indices) -> int:
        """How many objects 'Selection' recolours (the selection windows)."""
        if doc is None or doc is not self.doc or not indices:
            return 0
        n = len(doc.page.objects)
        sel = self._filter_template([i for i in indices if 0 <= i < n], "recolour", quiet=True)
        return len({doc.root_of(i) for i in sel})

    def _colours_in_use(self, doc, indices, scope: str = "selection") -> list:
        """The colours Swap / Adjust Colours would change in their scope, most used first:
        [((on, off), objects)] (the fill and line pairs of each object, a pipe's colour;
        what colourstate.colour_channels gives, the objects colour_targets reaches)."""
        if doc is None or doc is not self.doc:
            return []
        from ...format import colourstate, colourtools as T

        n = len(doc.page.objects)
        if scope == "page" or not indices:
            targets = None
        else:
            targets = (
                self._filter_template([i for i in indices if 0 <= i < n], "recolour", quiet=True) or None
            )
            if targets is None:
                return []
        objs = doc.parsed.page.objects
        counts: dict = {}
        for i in doc._colour_targets(targets):
            if not 0 <= i < len(objs):
                continue
            ch = colourstate.colour_channels(objs[i])
            if ch is None:
                continue
            seen = set()
            for pair in ch.values():
                # (the 'no colour' values are the one Transparent, as Swap Colours finds
                # them: colourtools.same_colour)
                p = tuple(T.TRANSPARENT if T.is_clear(c) else int(c) & 0xFFFFFFFF for c in pair[:2])
                if p in seen:
                    continue
                seen.add(p)
                counts[p] = counts.get(p, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def _swap_colors(self) -> None:
        """Tools > Colours > Swap Colours: a modeless window; Apply / OK swap
        the colours of the selection or the whole page, one undo step."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("swapcol")):
            self._sync_selection_windows()
            return
        from ..dialog.colour_dialogs import SwapColorsDialog

        targets = self._colour_tool_targets() if self._sel else None
        frm = self.doc.selection_colour(targets) if targets else None
        dlg = SwapColorsDialog(self, frm=frm, count_of=self._colour_count, colours_of=self._colours_in_use)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_swap(d))
        self._show_modeless(dlg, "swapcol", scope="selection")
        self._sync_selection_windows()

    def _apply_swap(self, dlg) -> None:
        """Apply / OK of Swap Colours: the targets as they are now."""
        targets = self._colour_tool_targets(dlg.scope())
        if targets is None:
            self.statusBar().showMessage("Nothing to change. Select objects or choose the whole page.", 5000)
            return
        frm, to, swap_range = dlg.values()
        n = self.doc.swap_colours(targets, frm, to, swap_range=swap_range)
        self._refresh()
        self.statusBar().showMessage(
            "Swapped the colours of %s." % plural(n, "object")
            if n
            else "No object has the From colour. Nothing was changed.",
            5000,
        )

    def _adjust_colors(self) -> None:
        """Tools > Colours > Adjust Colours: a modeless window; Apply / OK
        adjust the colours of the selection or the whole page, one undo
        step."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("adjustcol")):
            self._sync_selection_windows()
            return
        from ..dialog.colour_dialogs import AdjustColorsDialog

        dlg = AdjustColorsDialog(self, count_of=self._colour_count, colours_of=self._colours_in_use)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_adjust_colours(d))
        self._show_modeless(dlg, "adjustcol", scope="selection")
        self._sync_selection_windows()

    def _apply_adjust_colours(self, dlg) -> None:
        """Apply / OK of Adjust Colours: the targets as they are now."""
        adj = dlg.adjustment()
        if adj.is_identity():
            self.statusBar().showMessage(
                "No hue, lightness or saturation change was set. Nothing was changed.", 5000
            )
            return
        targets = self._colour_tool_targets(dlg.scope())
        if targets is None:
            self.statusBar().showMessage("Nothing to change. Select objects or choose the whole page.", 5000)
            return
        n = self.doc.adjust_colours(targets, adj)
        self._refresh()
        self.statusBar().showMessage(
            "Adjusted the colours of %s." % plural(n, "object")
            if n
            else "No colour is in the selected hue range. Nothing was changed.",
            5000,
        )

    def _favorite_colors(self) -> None:
        """Tools > Edit Favorite Colors: the favourites of this project's
        palette (its citect.pal, shared with the Graphics Builder)."""
        from .. import colour_picker

        folder = (
            os.path.dirname(os.path.abspath(self.doc.path))
            if self.doc is not None
            else colour_picker.context_dir(self)
        )
        colour_picker.edit_favourites(self, None, folder)

    # ------------------------------------------------------ bitmap editor
    def _bitmap_target(self, quiet: bool = False):
        """The embedded bitmap Tools > Bitmap Editor edits: the selected one,
        or the only one inside a selected small plain group (a logo symbol
        holds one bitmap). ``(index, None)`` or ``(None, reason)``;
        ``quiet`` = a state query (no status message)."""
        from ...format import bitmaps

        if self.doc is None or len(self._sel) != 1:
            return None, "Select one bitmap first"
        i = self._sel[0]
        objs = self.doc.parsed.page.objects
        if not 0 <= i < len(objs):
            return None, "Select one bitmap first"
        if bitmaps.dib_section(objs[i]) is None:
            sub = self.doc.subtree(i) if objs[i].type_code == 0x08 else []
            inner = [j for j in sub if len(sub) <= 64 and bitmaps.dib_section(objs[j]) is not None]
            if objs[i].type_code == 0x08 and len(inner) == 1:
                i = inner[0]
            elif objs[i].type_code == bitmaps.METAFILE_TC:
                return None, (
                    "The selection is a metafile (a vector drawing); use "
                    "Tools > Convert to Bitmap first to edit its pixels"
                )
            elif objs[i].type_code == bitmaps.BITMAP_TC and objs[i].subtype_size & 0x1:
                return None, (
                    "The selection is a library symbol; its picture belongs "
                    "to the library (right-click > Edit Definition, or Cut "
                    "Link first)"
                )
            else:
                return None, ("The selection is not a bitmap (use Tools > Convert to Bitmap first)")
        if not self._filter_template([i], "edit", quiet=quiet):
            return None, None  # locked / a genie's member: already said
        return i, None

    def _bitmap_editor(self) -> None:
        """Tools > Bitmap Editor (F9): edit the selected bitmap's pixels (both
        flash states) and put them back into the same record (a modeless
        window; one per bitmap)."""
        i, why = self._bitmap_target()
        if i is None:
            if why:
                self.statusBar().showMessage(why, 6000)
            return
        if self._raise_modeless(self._modeless_for("bitmap", i)):
            return
        from ...format import bitmaps

        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage(
                "The bitmap's picture could not be read (its stored data is damaged); "
                "import the picture again to replace it.",
                8000,
            )
            return
        po = self.doc.parsed.page.objects[i]
        from ...format import geometry as geom_mod

        l, t, r, b = geom_mod.parse_geom_header(po.body_raw).rect1
        w, h = max(1, r - l), max(1, b - t)
        rgba, frgba = primary.rgba, flash.rgba if flash is not None else None
        note = ""
        if (w, h) != (primary.width, primary.height):
            # CtDraw edits a stretched bitmap at the size it has on the page
            rgba = bitmaps.resample(rgba, primary.width, primary.height, w, h)
            if frgba is not None:
                frgba = bitmaps.resample(frgba, primary.width, primary.height, w, h)
            note = (
                "The picture is %d x %d pixels, stretched to %d x %d on the page. "
                "It is edited at its size on the page; saving stores it at that "
                "size." % (primary.width, primary.height, w, h)
            )
        else:
            w, h = primary.width, primary.height
        from ..dialog.bitmap_editor import BitmapEditorDialog

        dlg = BitmapEditorDialog(rgba, w, h, self, flash_rgba=frgba, note=note)
        # modeless: Save writes into the record the editor opened, if it is
        # still on the page with the picture it had (else the edits stay)
        dlg.window_saves = True
        dlg._ctg_dib = self._dib_signature(po)
        # (the size it had on the page when the editor opened: a resize on the page after
        # it is kept by a Save of painted pixels, _save_bitmap)
        dlg._ctg_page_size = (r - l, b - t)
        dlg._ctg_no_reload = True
        # (through the editor window it belongs to NOW: Move to New Window takes it along
        # with its page, modeless._adopt_modeless)
        dlg._ctg_apply = lambda d=dlg: getattr(d, "_ctg_host", self)._save_bitmap(d)
        dlg.saveRequested.connect(lambda d=dlg: getattr(d, "_ctg_host", self)._save_bitmap(d))
        self._show_modeless(dlg, ("bitmap", i), scope="object", title="Bitmap Editor")

    @staticmethod
    def _dib_signature(po):
        """What the Bitmap Editor compares at Save: the record's DIB section
        (flags, size and both stored pictures)."""
        from ...format import bitmaps

        sec = bitmaps.dib_section(po)
        return None if sec is None else (sec.flags, sec.uncompressed, sec.primary, sec.flash)

    def _save_bitmap(self, dlg) -> bool:
        """Save of the Bitmap Editor: put its picture into its bitmap (one
        undo step). Refused in the editor, with the edits kept, when the
        bitmap is gone or its picture changed on the page since it opened.
        True when the editor closed."""
        if not dlg.modified():
            dlg.saved()
            return True
        i = self._modeless_target_index(dlg, getattr(dlg, "_ctg_index", -1))
        if i is None:
            dlg.show_problem(
                "The bitmap is no longer on the page, so the picture cannot "
                "be saved. Undo on the page brings the bitmap back."
            )
            return False
        from ...format import geometry as geom_mod

        po = self.doc.parsed.page.objects[i]
        if self._dib_signature(po) != getattr(dlg, "_ctg_dib", None):
            dlg.show_problem(
                "The bitmap's picture was changed on the page after the editor "
                "opened, so it cannot be saved over it."
            )
            return False
        l, t, r, b = geom_mod.parse_geom_header(po.body_raw).rect1
        # (a crop keeps the part it kept where it was on the page)
        dx, dy = dlg.origin_shift() if hasattr(dlg, "origin_shift") else (0, 0)
        rgba2, w2, h2 = dlg.result_rgba()
        flash = dlg.result_flash()
        # the size on the page: the editor's, unless the bitmap was resized on the page
        # after the editor opened. Painted pixels only: that newer size stays. A crop or a
        # resize in the editor too: asked which size to use (Cancel keeps the edits)
        opened = getattr(dlg, "_ctg_page_size", None)
        now = (r - l, b - t)
        out_w, out_h = w2, h2
        if opened is not None and now != opened:
            pixels_only = (w2, h2) == opened and (dx, dy) == (0, 0)
            if pixels_only:
                out_w, out_h = now
            else:
                box = QtWidgets.QMessageBox(dlg)
                box.setWindowTitle("Bitmap Editor")
                box.setText(
                    "The bitmap was resized on the page to %d x %d after the editor opened, "
                    "and the edited picture is %d x %d. Which size should it have on the page?"
                    % (now[0], now[1], w2, h2)
                )
                keep = box.addButton("Keep %d x %d" % now, QtWidgets.QMessageBox.AcceptRole)
                use = box.addButton("Use %d x %d" % (w2, h2), QtWidgets.QMessageBox.AcceptRole)
                box.addButton(QtWidgets.QMessageBox.Cancel)
                box.setDefaultButton(keep)
                box.exec()
                if box.clickedButton() is keep:
                    out_w, out_h = now
                elif box.clickedButton() is not use:
                    return False
        l, t = l + dx, t + dy
        if not self.doc.replace_bitmap(i, (l, t, l + out_w, t + out_h), rgba2, w2, h2, flash_rgba=flash):
            dlg.show_problem("The bitmap could not be changed.")
            return False
        self._refresh()
        size = "%d x %d" % (w2, h2)
        if (out_w, out_h) != (w2, h2):
            size += ", shown at %d x %d on the page" % (out_w, out_h)
        self.statusBar().showMessage(
            "Bitmap changed (%s%s)." % (size, ", flashing" if flash is not None else ""), 4000
        )
        dlg._ctg_page_size = (out_w, out_h)
        dlg.saved()
        return True

    # ------------------------------------------- replace / export / resample a picture
    def _picture_target(self, quiet: bool = False):
        """The one selected picture with its own pixels or drawing: ``(index, 'bitmap' |
        'metafile')``, or ``(None, reason)``. A small plain group holding one bitmap
        counts as that bitmap (as the Bitmap Editor)."""
        from ...format import bitmaps

        if self.doc is None or len(self._sel) != 1:
            return None, "Select one picture first"
        i = self._sel[0]
        objs = self.doc.parsed.page.objects
        if 0 <= i < len(objs) and bitmaps.metafile_bits(objs[i]) is not None:
            if not self._filter_template([i], "edit", quiet=quiet):
                return None, None
            return i, "metafile"
        j, why = self._bitmap_target(quiet=quiet)
        if j is None:
            if why and why.startswith("The selection is a metafile"):
                why = "The metafile's drawing could not be read"
            elif why and why.startswith("Select one bitmap"):
                why = "Select one picture first"
            elif why and why.startswith("The selection is not a bitmap"):
                why = "The selection is not a picture"
            return None, why
        return j, "bitmap"

    def _picture_rect(self, i: int):
        from ...format import geometry as geom_mod

        l, t, r, b = geom_mod.parse_geom_header(self.doc.parsed.page.objects[i].body_raw).rect1
        return l, t, max(l + 1, r), max(t + 1, b)

    def _replace_picture(self) -> None:
        """Edit > Replace Picture…: another picture file in the selected picture's place;
        it keeps its place, size (unless another is chosen), animations, commands, name
        and AN. The page's other copies of the same picture can be replaced with it."""
        i, kind = self._picture_target()
        if i is None:
            if kind:
                self.statusBar().showMessage(kind, 6000)
            return
        from ..dialog.bitmap_import import PictureError, add_recent, build, import_filter, open_source
        from ..dialog.picture_import import PictureImportDialog

        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Replace Picture", self._dialog_dir("import"), import_filter()
        )
        if not path:
            return
        self._remember_dir("import", path)
        try:
            src = open_source(path)
        except PictureError as e:
            warn(self, "Replace Picture", str(e))
            return
        if kind == "metafile" and src.kind != "metafile":
            warn(
                self,
                "Replace Picture",
                "The selection is a drawing (a metafile): it takes another drawing (.wmf or "
                ".emf). Delete it and import %s instead." % src.name,
            )
            return
        l, t, r, b = self._picture_rect(i)
        dlg = PictureImportDialog(
            [src], self, target=(r - l, b - t), action="Replace", drawing=kind == "metafile"
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        copies = self.doc.picture_copies(i) if kind == "bitmap" else []
        targets = [i]
        if copies:
            from ..messages import choose

            every = "Replace All %d" % (len(copies) + 1)
            got = choose(
                self,
                "Replace Picture",
                "The page shows this picture %d more time%s. Replace %s too?"
                % (len(copies), "" if len(copies) == 1 else "s", "it" if len(copies) == 1 else "them"),
                [every, "Only the Selected One"],
                every,
            )
            if got is None:
                return
            if got == every:
                targets += copies
        opts = dlg.options_for(src)
        done = 0
        try:
            with self.doc.batch("replace picture"):
                for n, j in enumerate(targets):
                    jl, jt, jr, jb = self._picture_rect(j)
                    o = (
                        opts
                        if n == 0
                        else opts._replace(
                            width=(jr - jl) * opts.width // max(1, r - l),
                            height=(jb - jt) * opts.height // max(1, b - t),
                        )
                    )
                    pic = build(src, o)
                    pw, ph = pic.size
                    rect = (jl, jt, jl + pw, jt + ph)
                    if kind == "metafile":
                        done += bool(self.doc.replace_metafile(j, rect, pic.data))
                    else:
                        done += bool(
                            self.doc.replace_bitmap(j, rect, pic.data, pic.width, pic.height, flash_rgba=None)
                        )
        except PictureError as e:
            warn(self, "Replace Picture", str(e))
            return
        add_recent([path])
        self._refresh()
        self.statusBar().showMessage(
            "Replaced the picture with %s%s" % (src.name, "" if done <= 1 else " (%d copies)" % done), 5000
        )

    def _export_picture(self) -> None:
        """File > Export Picture…: save the selected picture as a file (a bitmap as PNG,
        BMP or JPEG, both states of a flashing one; a drawing as .wmf or as PNG)."""
        i, kind = self._picture_target(quiet=True)
        if i is None:
            if kind:
                self.statusBar().showMessage(kind, 6000)
            return
        from ...format import bitmaps

        name = self.doc.object_name(i) or "picture"
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "picture"
        start = os.path.join(self._dialog_dir("export"), name)
        po = self.doc.parsed.page.objects[i]
        l, t, r, b = self._picture_rect(i)
        if kind == "metafile":
            flt = "Windows Metafile (*.wmf);;PNG picture (*.png)"
            path, chosen = QtWidgets.QFileDialog.getSaveFileName(self, "Export Picture", start + ".wmf", flt)
            if not path:
                return
            self._remember_dir("export", path)
            wmf = bitmaps.metafile_bits(po)
            try:
                if path.lower().endswith(".png") or chosen.startswith("PNG"):
                    from ...format.symbols import render_metafile

                    eb = render_metafile(wmf, 0, r - l, b - t, max_dim=max(r - l, b - t))
                    if eb is None or not self._image_of(eb.rgba, eb.width, eb.height).save(path):
                        raise OSError("the picture could not be drawn or written")
                else:
                    with open(path, "wb") as fh:
                        fh.write(bitmaps.placeable_wmf(wmf, r - l, b - t))
            except (OSError, bitmaps.MetafileError) as e:
                warn(self, "Export Picture", "%s could not be saved: %s" % (path, e))
                return
            self.statusBar().showMessage("Saved %s" % path, 5000)
            return
        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage("The bitmap's picture could not be read.", 6000)
            return
        flt = "PNG picture (*.png);;Bitmap (*.bmp);;JPEG picture (*.jpg)"
        path, _chosen = QtWidgets.QFileDialog.getSaveFileName(self, "Export Picture", start + ".png", flt)
        if not path:
            return
        self._remember_dir("export", path)
        written = []
        stem, ext = os.path.splitext(path)
        # both names before either file is written: the file chooser asked about the one it
        # was given only, so a flashing picture's companion that exists is asked about here
        # (Cancel leaves both files as they are)
        outs = [
            (eb, out) for eb, out in ((primary, path), (flash, "%s (flashing)%s" % (stem, ext or ".png")))
        ]
        outs = [(eb, out) for eb, out in outs if eb is not None]
        companion = [out for eb, out in outs[1:] if os.path.exists(out)]
        if companion:
            from ..messages import confirm

            if not confirm(
                self,
                "Export Picture",
                "The flashing picture goes to %s, which already exists. Replace it?"
                % os.path.basename(companion[0]),
                "Replace",
            ):
                self.statusBar().showMessage("Export Picture cancelled: nothing was written.", 6000)
                return
        for eb, out in outs:
            img = self._image_of(eb.rgba, eb.width, eb.height)
            if ext.lower() in (".jpg", ".jpeg", ".bmp"):
                # no transparency there: the clear pixels over white
                flat = QtGui.QImage(img.size(), QtGui.QImage.Format_RGB32)
                flat.fill(QtCore.Qt.white)
                p = QtGui.QPainter(flat)
                p.drawImage(0, 0, img)
                p.end()
                img = flat
            if not img.save(out):
                warn(self, "Export Picture", "%s could not be saved." % out)
                return
            written.append(out)
        self.statusBar().showMessage("Saved %s" % " and ".join(os.path.basename(p) for p in written), 6000)

    def _find_duplicate_pictures(self) -> None:
        """Tools > Pictures > Find Duplicate Pictures…: the pictures the pages of this
        page's project store more than once (ui/dialog/duplicate_pictures)."""
        if self.doc is None or isinstance(self.doc, ElementDocument) or not self.doc.path:
            self.statusBar().showMessage("Open a page of the project first.", 5000)
            return
        if getattr(self.doc, "is_new", False):
            self.statusBar().showMessage("Save the page into its project first.", 5000)
            return
        from ..dialog.duplicate_pictures import DuplicatePicturesDialog

        DuplicatePicturesDialog(self, os.path.dirname(os.path.abspath(self.doc.path))).exec()

    @staticmethod
    def _image_of(rgba: bytes, w: int, h: int) -> QtGui.QImage:
        return QtGui.QImage(bytes(rgba), w, h, w * 4, QtGui.QImage.Format_RGBA8888).copy()

    def _resample_picture(self) -> None:
        """Tools > Resample Picture to Its Size: a stretched bitmap's pixels made the
        size it has on the page, scaled smoothly (the runtime stretches a bitmap
        coarsely). One undo step."""
        i, kind = self._picture_target()
        if i is None or kind != "bitmap":
            self.statusBar().showMessage(
                "A drawing (metafile) is always drawn at its size."
                if kind == "metafile"
                else kind or "Select one bitmap first.",
                6000,
            )
            return
        from ..dialog.bitmap_import import smooth_resample
        from ...format import bitmaps

        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage("The bitmap's picture could not be read.", 6000)
            return
        l, t, r, b = self._picture_rect(i)
        w, h = r - l, b - t
        if (w, h) == (primary.width, primary.height):
            self.statusBar().showMessage(
                "The bitmap is already stored at its size on the page (%d x %d)." % (w, h), 5000
            )
            return
        if w > bitmaps.MAX_SIDE or h > bitmaps.MAX_SIDE:
            self.statusBar().showMessage("The bitmap is too large on the page to store at that size.", 6000)
            return
        rgba = smooth_resample(primary.rgba, primary.width, primary.height, w, h)
        frgba = None if flash is None else smooth_resample(flash.rgba, flash.width, flash.height, w, h)
        if self.doc.replace_bitmap(i, (l, t, r, b), rgba, w, h, flash_rgba=frgba):
            self._refresh()
            self.statusBar().showMessage(
                "Resampled the bitmap from %d x %d to %d x %d" % (primary.width, primary.height, w, h), 5000
            )
