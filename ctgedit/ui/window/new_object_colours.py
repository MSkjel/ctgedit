"""The colours new objects are drawn in."""

from __future__ import annotations

from ..messages import inform, warn

from ..main_window_base import (
    PageScene,
    QtWidgets,
    contextlib,
    option,
    paste_linked,
)

from ...infra.logsetup import note_exception as _note_exc


from ..main_window_base import (
    QtCore,
    QtGui,
    os,
)


class NewObjectColours:
    """The fill and line colours new objects take, and their commands."""

    # ----------------------------------------------------------- the colours of new objects
    @property
    def _draw_colour(self) -> int:
        """The one colour the armed tool gives a new object (draw_styles.creation_colour:
        the text colour for Text and Number, the line colour for lines and outlines, the
        fill colour for shapes), on the page shown. Read only: the colours are the ribbon
        swatches' (draw_styles.set_colour)."""
        from ..editor import draw_styles as DS

        view = getattr(self, "view", None)
        tool = "rectangle"
        try:
            tool = view.tool() if view is not None else tool
        except RuntimeError:  # swallow-ok: the canvas is going (the window closes)
            pass
        if DS.kind_of_tool(tool) is None:
            tool = "rectangle"
        c = DS.creation_colour(tool, self._colour_page())
        return DS.colours(self._colour_page())["fill"] if c is None else c

    def _create_styled(self, tool: str, rect=None, nodes=None, *, colour=None, **kw) -> int:
        """``doc.create_object`` with the look of new objects (ui/editor/draw_styles): the tool's
        colour, then its style (style_for, through doc.apply_appearance: the record of
        drawing the object and pasting that appearance), as ONE undo step. ``colour`` None
        keeps the factory colour (a button, a symbol)."""
        from ..editor import draw_styles as DS

        doc = self.doc
        style_tool = "freehand" if tool == "polyline" else tool
        kind = DS.kind_of_tool(style_tool)
        if kind is None:
            return doc.create_object(tool, rect, nodes, colour=colour, **kw)
        page = doc.page
        if colour is not None:
            colour = DS.creation_colour(style_tool, page)
        style = DS.style_for(kind, style_tool, page)
        with doc.batch("add object"):
            idx = doc.create_object(tool, rect, nodes, colour=colour, **kw)
            if style:
                doc.apply_appearance([idx], style, label="add object")
        return idx

    def _install_new_colours(self) -> None:
        """The colours of new objects follow the page shown (its colour, a tab switch, an
        undo): the swatch pair is painted again (MainWindow construction)."""
        self.props.changed.connect(self._update_colour_button)
        self.doc_tabs.currentChanged.connect(self._update_colour_button)
        for a in (self.act_undo, self.act_redo):
            a.triggered.connect(self._update_colour_button)

    def _colour_page(self):
        """The page the colours of new objects contrast with (None: no page open)."""
        doc = getattr(self, "doc", None)
        return getattr(doc, "page", None) if doc is not None else None

    def new_object_colours(self) -> dict:
        """What the ribbon's swatch pair shows: the fill, line and text colours on the page
        shown, No fill and whether they follow the page."""
        from ..editor import draw_styles as DS

        out = dict(DS.colours(self._colour_page()))
        out["no_fill"] = DS.no_fill()
        out["auto"] = DS.colour_mode() == "auto"
        return out

    def _update_colour_button(self, *_a) -> None:
        """The swatch pair and the colour commands show the colours as they are now."""
        from ..editor import draw_styles as DS

        pair = getattr(self, "ribbon_colours", None)
        if pair is not None:
            with contextlib.suppress(RuntimeError):
                pair.update()
        for name, on in (
            ("act_new_no_fill", DS.no_fill()),
            ("act_new_colours_auto", DS.colour_mode() == "auto"),
        ):
            a = getattr(self, name, None)
            if a is not None and a.isChecked() != on:
                blocked = a.blockSignals(True)
                a.setChecked(on)
                a.blockSignals(blocked)
        panel = getattr(self, "props", None)
        if (
            panel is not None
            and getattr(panel, "_tool", "select") not in ("", "select")
            and hasattr(panel, "refresh_tool_style")
        ):
            panel.refresh_tool_style()

    def _colour_popover(self, target: str = "fill") -> None:
        """The colour picker of a colour of new objects (the swatch pair, Tools > Colours >
        Colours of New Objects): ``target`` 'fill' / 'line' / 'text'."""
        from ..editor import draw_styles as DS
        from ..swatch_pair import NewColourPopover

        st = self.new_object_colours()
        pop = NewColourPopover(target, st, self, no_fill=st["no_fill"], auto=st["auto"])
        page = self._colour_page()

        def chosen(which, colour):
            DS.set_colour(which, colour, page)
            self._update_colour_button()
            self.statusBar().showMessage(
                {
                    "fill": "New shapes are filled with #%06X.",
                    "line": "New lines and shape edges are #%06X.",
                    "text": "New text is #%06X.",
                }[which]
                % (colour & 0xFFFFFF),
                4000,
            )

        def no_fill(on):
            self._set_new_no_fill(on)

        def auto(on):
            self._set_new_colours_auto(on)

        pop.chosen.connect(chosen)
        pop.noFillToggled.connect(no_fill)
        pop.autoToggled.connect(auto)
        # a new picker per click (the palette may have changed): let each go
        # once it is closed -- picked, eye-dropped or dismissed
        pop.finished.connect(pop.deleteLater)
        anchor = getattr(self, "ribbon_colours", None)
        if anchor is not None and not anchor.isVisible():
            anchor = None
        pop.open_under(anchor)

    def _on_swatch_part(self, part: str) -> None:
        """A click on a part of the ribbon's swatch pair."""
        if part == "swap":
            self._swap_new_colours()
        elif part == "no_fill":
            from ..editor import draw_styles as DS

            self._set_new_no_fill(not DS.no_fill())
        else:
            self._colour_popover(part)

    def _swap_new_colours(self) -> None:
        """Tools > Colours > Colours of New Objects > Swap Fill and Line (Shift+X)."""
        from ..editor import draw_styles as DS

        DS.swap_fill_and_line(self._colour_page())
        self._update_colour_button()
        c = DS.colours(self._colour_page())
        self.statusBar().showMessage("New shapes: fill #%06X, line #%06X." % (c["fill"], c["line"]), 4000)

    def _set_new_no_fill(self, on: bool) -> None:
        from ..editor import draw_styles as DS

        DS.set_no_fill(bool(on))
        self._update_colour_button()
        self.statusBar().showMessage(
            "New shapes are drawn hollow." if on else "New shapes are filled again.", 4000
        )

    def _set_new_colours_auto(self, on: bool) -> None:
        from ..editor import draw_styles as DS

        DS.set_colour_mode("auto" if on else "fixed", self._colour_page())
        self._update_colour_button()
        self.statusBar().showMessage(
            "The colours of new objects contrast with the page."
            if on
            else "New objects take the colours you picked.",
            4000,
        )

    def _arm_symbol_placement(self, library: str, element: str, ext: str) -> None:
        if self.doc is None:
            self.statusBar().showMessage("Open a page first to place %s.%s" % (library, element), 5000)
            return
        self._pending_symbol = (library, element, ext)
        self._pending_composite = None
        self._set_tool("symbol")
        what = "genie" if ext == "ctm" else "symbol"
        self.statusBar().showMessage(
            "Click on the page to place the %s %s.%s%s (Esc cancels)."
            % (what, library, element, "" if ext == "ctm" else ", or drag a box to size it"),
            8000,
        )

    def _paste_library_dialog(self, kind: str) -> None:
        """Edit > Paste Symbol / Genie / Super Genie."""
        if self.doc is None:
            self.statusBar().showMessage("Open a page first.", 4000)
            return
        from ..browser import PageBrowserDialog

        titles = {"symbol": "Paste Symbol", "genie": "Paste Genie", "supergenie": "Paste Super Genie"}
        # a symbol pick shows the Linked check box (genies are always linked)
        extra = {"linked_option": True} if kind == "symbol" else {}
        pick = PageBrowserDialog.get_library_element(
            self,
            os.path.dirname(os.path.abspath(self.doc.path)),
            "symbol" if kind == "symbol" else "genie",
            title=titles.get(kind),
            super_genies=None if kind == "symbol" else kind == "supergenie",
            **extra,
        )
        self._sync_paste_linked()
        if pick is None:
            return
        library = os.path.splitext(os.path.basename(pick[0]))[0]
        self._arm_symbol_placement(library, pick[1], "ctl" if kind == "symbol" else "ctm")

    def _page_thumbnail_for(self, doc):
        """The TH thumbnail of another page document (a super genie page
        created by a placement), rendered in a scratch scene."""
        from ...format import thumbnail as _thumb

        pb = doc.parsed.page
        pw = (doc.page.appearance.width if doc.page is not None else 0) or pb.page_width
        ph = (doc.page.appearance.height if doc.page is not None else 0) or pb.page_height
        tw, th = _thumb.thumb_size(pw, ph)
        if not tw or doc.page is None:
            return None
        scene = PageScene()
        try:
            scene.set_page(doc.page)
            img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
            img.fill(QtCore.Qt.white)
            painter = QtGui.QPainter(img)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            try:
                with scene.export_only(None):
                    scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, pw, ph))
            finally:
                painter.end()
            bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
            return _thumb.encode(bits, tw, th)
        finally:
            scene.clear()
            scene.deleteLater()

    def _report_super_pages(self, reports) -> None:
        """After a genie placement / genie save: the super genie pages it
        created (CtDraw creates them then too) -- or why one could not be."""
        made = [r for r in reports if r.get("status") == "created"]
        # a page written with a save warning (its database rows refused ...)
        bad = [
            r
            for r in reports
            if r.get("status") in ("missing", "failed") or (r.get("status") == "created" and r.get("detail"))
        ]
        for r in made:
            with contextlib.suppress(Exception):
                self._saved_page_elsewhere(r["path"])
        if made:
            projects = sorted({os.path.basename(os.path.dirname(r["path"])) for r in made})
            self.statusBar().showMessage(
                "Created the super genie page%s %s in project %s (compile to use %s)."
                % (
                    "s" if len(made) > 1 else "",
                    ", ".join(r["name"] for r in made),
                    ", ".join(projects),
                    "them" if len(made) > 1 else "it",
                ),
                10000,
            )
        if bad:
            # non-blocking: the placement itself succeeded
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Warning,
                "Super Genie Page",
                "The genie opens super genies whose page could not be created, or not "
                "completely:\n\n%s\n\nThe genie was placed; its popup opens at runtime only "
                "once the page exists and is registered in the project."
                % "\n".join("• %s (%s): %s" % (r["name"], r["element"], r["detail"]) for r in bad),
                QtWidgets.QMessageBox.Ok,
                self,
            )
            box.setModal(False)
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            box.show()
            self._super_page_box = box

    def _saved_page_elsewhere(self, path: str) -> None:
        """A page file written outside the open tabs: the browsers / catalog
        re-list its project."""
        from ...io.catalog import catalog_for

        with contextlib.suppress(Exception):
            cat = catalog_for()
            info = cat.project_for_dir(os.path.dirname(os.path.abspath(path)))
            if info is not None:
                cat.touch(info)

    def _on_library_drop(self, library: str, element: str, ext: str, pos: QtCore.QPointF) -> None:
        """A Library-panel card dropped on the page: paste it there."""
        self._place_library(library, element, ext, (int(pos.x()), int(pos.y())))

    def _place_library(self, library: str, element: str, ext: str, pos):
        """Paste a library genie or symbol at ``pos``."""
        idx = None
        try:
            if self.doc is None:
                self.statusBar().showMessage("Open a page first.", 4000)
                return None
            x, y = int(pos[0]), int(pos[1])
            if getattr(self, "_place_anchor", "top_left") == "centre":
                size = self.doc.library_element_size(library, element, ext)
                if size is not None:
                    x, y = x - size[0] // 2, y - size[1] // 2
            # (an underlaid page's object corner near it catches it, else the grid: the
            # same element placed where the reference has it)
            snap = getattr(self, "act_snap", None)
            x, y = self.view.placement_point(x, y, grid=bool(snap is not None and snap.isChecked()))
            params = None
            if ext == "ctm":
                # (a genie placed into itself, or into a genie it holds a copy of: said
                # before its parameters are asked for)
                why = self.doc.placement_refusal(library, element, ext)
                if why:
                    inform(self, "Place", why)
                    return None
            no_forms = option("disable_genie_forms")
            if ext == "ctm" and not no_forms:
                try:
                    decl = self.doc.genie_parameters(library, element, ext)
                except Exception:  # noqa: BLE001
                    _note_exc("shell._place_library")
                    decl = {}
                # the genie's FORM (<library>.frm) when it has one -- CtDraw
                # shows the same form on Paste as on Edit
                form = self.doc.genie_form_for(library, element)
                if decl or form is not None:
                    from ..dialog.genie_dialogs import GeniePasteDialog

                    problems = [] if form is not None else self.doc.genie_form_problems(library, element)
                    dlg = GeniePasteDialog(
                        "%s.%s" % (library, element),
                        decl,
                        self._tag_completion(),
                        self,
                        form=form,
                        list_values=self.doc.form_list_values,
                        an=self.doc.next_placement_an(),
                        problems=problems,
                        pickers=self._genie_pickers(),
                        uses=self.doc.genie_uses_for(library, element, decl),
                    )
                    if dlg.exec() != QtWidgets.QDialog.Accepted:
                        return None
                    params = dlg.values()
            # a symbol is pasted linked or as an unlinked copy (the Linked
            # setting: Library panel / Edit > Library > Paste Symbols Linked)
            linked = ext != "ctl" or paste_linked()
            try:
                idx = self.doc.place_library_element(
                    library,
                    element,
                    ext,
                    (x, y),
                    params,
                    linked=linked,
                    page_thumbnail=self._page_thumbnail_for,
                    dialog=params is not None,
                )
            except Exception as e:  # noqa: BLE001
                warn(self, "Place", "%s.%s: %s" % (library, element, e))
                return None
            self._sel = [idx] if idx is not None else []
            self._refresh()
            with contextlib.suppress(Exception):
                self.library_dock.note_placed(library, element, ext)
            self.statusBar().showMessage(
                "Placed %s.%s%s" % (library, element, "" if linked else " (an unlinked copy)"), 4000
            )
            self._report_super_pages(getattr(self.doc, "super_pages", None) or [])
            if idx is not None and ext == "ctm" and no_forms:
                self._genie_object_forms(idx)
            elif idx is not None and ext == "ctl" and option("props_on_new"):
                QtCore.QTimer.singleShot(0, self, lambda i=idx: self._props_after_new(i))
        finally:
            if not (idx is not None and getattr(self, "_keep_tool", False)):
                self._set_tool("select")
        return idx
