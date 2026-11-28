"""The Arrange and Tools commands, lock mode, selecting and transforming."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    ElementDocument,
    QtCore,
    TransformError,
    app_settings,
    contextlib,
    option,
)
from ..messages import warn


class ArrangeCommands:
    """Align, space, arrange, rotate and flip; lock mode; select and transform commands."""

    # ------------------------------------------------ Arrange / Tools actions
    def _rotate(self, clockwise: bool) -> None:
        if self.doc is None or not self._sel:
            return
        sel = self._filter_template(self._sel, "rotate")
        if sel:
            try:
                self.doc.rotate_objects(sel, clockwise=clockwise)
            except TransformError as e:
                warn(self, "Rotate", str(e))
                return
            self._refresh()

    def _flip(self, horizontal: bool) -> None:
        if self.doc is None or not self._sel:
            return
        sel = self._filter_template(self._sel, "flip")
        if sel:
            try:
                self.doc.mirror_objects(sel, horizontal=horizontal)
            except TransformError as e:
                warn(self, "Flip", str(e))
                return
            self._refresh()

    def set_surround_mode(self, mode: str, store: bool = True) -> None:
        """View > Outside the Page (also Settings > Editing): what the canvas
        paints around the page -- 'desk' (the page as a card), 'shaded', 'page' (the
        Graphics Builder) or 'workspace'. Editor-only; saved in the settings
        (view/surround). Every tab's scene follows."""
        from ..prefs.settings import surround_mode

        if mode not in self.surround_actions:
            mode = surround_mode()
        self.scene.set_surround(mode)
        for e in getattr(self, "_docs", ()):
            sc = e.get("scene")
            if sc is not None and sc is not self.scene:
                sc.set_surround(mode)
        self.surround_actions[mode].setChecked(True)
        if store:
            app_settings().setValue("view/surround", mode)

    def _zoom_selection(self) -> None:
        if self.doc is None or not self._sel:
            return
        box = self.doc.selection_extent(self._sel, bounds=True)
        if box is not None:
            l, t, r, b = box
            pad = max(8, (r - l + b - t) // 20)
            self.view.frame_rect(QtCore.QRectF(l - pad, t - pad, r - l + 2 * pad, b - t + 2 * pad))

    def _align_dialog(self) -> None:
        """Arrange > Align."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("align")):
            self._sync_selection_windows()
            return
        from ..dialog.arrange_dialogs import AlignDialog

        dlg = AlignDialog(self, units=self._align_units(self.doc, self._sel), units_of=self._align_units)
        dlg.set_reference(getattr(self, "_align_to", "selection"))
        dlg.referenceChosen.connect(self._align_to_chosen)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_align(d))
        self._show_modeless(dlg, "align", scope="selection")
        self._sync_selection_windows()

    def _align_to_chosen(self, key: str) -> None:
        """The Align window's Align to box: the same setting as Settings > Editing."""
        from ..prefs.settings import app_settings

        app_settings().setValue("opt/align_to", key)
        self._align_to = key
        self._sync_selection_windows()

    def _align_units(self, doc, indices) -> int:
        """How many units Align would move: the selected roots that may be
        moved now (locked objects and a genie's members on their own left
        out)."""
        if doc is None or doc is not self.doc or not indices:
            return 0
        n = len(doc.page.objects)
        sel = self._filter_template([i for i in indices if 0 <= i < n], "align", quiet=True)
        return len({doc.root_of(i) for i in sel})

    def _apply_align(self, dlg) -> None:
        """Apply / OK of the Align window: the current selection, one undo
        step."""
        if self.doc is None:
            return
        sel = self._filter_template(self._sel, "align")
        ref = self.align_reference(sel)
        if ref is False:
            # (Settings > Editing 'Align to: The page' aligns one object too)
            if getattr(self, "_align_to", "") == "page":
                msg = "Select the objects to align. Locked objects and the objects of a genie are not moved."
            else:
                msg = (
                    "Select two or more objects to align. Locked objects and the objects "
                    "of a genie are not moved."
                )
            self.statusBar().showMessage(msg, 5000)
            return
        vertical, horizontal = dlg.vertical(), dlg.horizontal()
        if not vertical and not horizontal:
            return
        self._apply_alignment(self.doc, sel, vertical, horizontal, reference=ref)
        self._refresh(rebuild_tree=False)
        self.statusBar().showMessage("Aligned %d objects." % len({self.doc.root_of(i) for i in sel}), 4000)

    @staticmethod
    def _apply_alignment(doc, sel, vertical: str, horizontal: str, reference=None) -> None:
        """One undo step: the vertical then the horizontal choice of the Align
        dialog ('even' = distribute by midpoints; the others line up with
        ``reference``, None = the selection box: Settings 'Align to')."""
        if not vertical and not horizontal:
            return
        with doc.batch("align"):
            for mode, axis in ((vertical, "v"), (horizontal, "h")):
                if mode == "even":
                    doc.distribute(sel, axis)
                elif mode:
                    doc.align(sel, mode, reference=reference)

    def _space_equally_dialog(self) -> None:
        """Arrange > Space Equally…: equal gaps between the objects' edges (modeless,
        follows the selection; Apply / OK = one undo step)."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("space_equally")):
            self._sync_selection_windows()
            return
        from ..dialog.arrange_dialogs import SpaceEquallyDialog

        dlg = SpaceEquallyDialog(
            self, units=self._align_units(self.doc, self._sel), units_of=self._align_units
        )
        st = app_settings()
        dlg.direction.setCurrentIndex(1 if st.value("arrange/space_axis", "h") == "v" else 0)
        dlg.gap.setValue(int(st.value("arrange/space_gap", 10)))
        if st.value("arrange/space_fixed", False, type=bool):
            dlg.fixed.setChecked(True)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_space_equally(d))
        self._show_modeless(dlg, "space_equally", scope="selection")
        self._sync_selection_windows()

    def _apply_space_equally(self, dlg) -> None:
        if self.doc is None:
            return
        sel = self._filter_template(self._sel, "align")
        if len({self.doc.root_of(i) for i in sel}) < 2:
            self.statusBar().showMessage(
                "Select two or more objects to space (locked objects "
                "and the objects of a genie are not moved).",
                5000,
            )
            return
        st = app_settings()
        st.setValue("arrange/space_axis", dlg.axis())
        st.setValue("arrange/space_gap", int(dlg.gap.value()))
        st.setValue("arrange/space_fixed", dlg.fixed.isChecked())
        self.doc.space_equally(sel, dlg.axis(), dlg.gap_value())
        self._refresh(rebuild_tree=False)
        self.statusBar().showMessage(
            "Spaced %d objects equally." % len({self.doc.root_of(i) for i in sel}), 4000
        )

    def _arrange_grid_dialog(self) -> None:
        """Arrange > Arrange in Grid…: the selection laid out in columns and rows
        (modeless, follows the selection; Apply / OK = one undo step)."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("arrange_grid")):
            self._sync_selection_windows()
            return
        from ..dialog.arrange_dialogs import ArrangeGridDialog

        dlg = ArrangeGridDialog(
            self, units=self._align_units(self.doc, self._sel), units_of=self._align_units
        )
        st = app_settings()
        dlg.set_values(
            columns=int(st.value("arrange/grid_columns", 2)),
            hgap=int(st.value("arrange/grid_hgap", 10)),
            vgap=int(st.value("arrange/grid_vgap", 10)),
            by_rows=str(st.value("arrange/grid_by_rows", True)).lower() != "false",
        )
        dlg.applyRequested.connect(lambda d=dlg: self._apply_arrange_grid(d))
        self._show_modeless(dlg, "arrange_grid", scope="selection")
        self._sync_selection_windows()

    def _apply_arrange_grid(self, dlg) -> None:
        if self.doc is None:
            return
        sel = self._filter_template(self._sel, "align")
        if len({self.doc.root_of(i) for i in sel}) < 2:
            self.statusBar().showMessage(
                "Select two or more objects to arrange (locked "
                "objects and the objects of a genie are not moved).",
                5000,
            )
            return
        v = dlg.values()
        st = app_settings()
        st.setValue("arrange/grid_columns", v["columns"])
        st.setValue("arrange/grid_hgap", v["hgap"])
        st.setValue("arrange/grid_vgap", v["vgap"])
        st.setValue("arrange/grid_by_rows", bool(v["by_rows"]))
        self.doc.arrange_in_grid(sel, v["columns"], v["hgap"], v["vgap"], v["by_rows"])
        self._refresh(rebuild_tree=False)
        self.statusBar().showMessage(
            "Arranged %d objects in a grid." % len({self.doc.root_of(i) for i in sel}), 4000
        )

    def _adjust_dialog(self) -> None:
        """Arrange > Adjust Position and Size."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("adjust")):
            self._sync_adjust_dialog(force=True)
            return
        from ..dialog.arrange_dialogs import AdjustPositionDialog

        dlg = AdjustPositionDialog(None, self)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_adjust(d))
        self._show_modeless(dlg, "adjust", scope="selection")
        self._sync_adjust_dialog(force=True)

    def _adjust_targets(self):
        """(unit roots, extent (l, t, r, b), left out) for Adjust Position and
        Size: the selected objects that may be moved / resized now."""
        if self.doc is None or self.doc.parsed is None or not self._sel:
            return [], None, 0
        n = len(self.doc.parsed.page.objects)
        sel = [i for i in self._sel if 0 <= i < n]
        ok = self._filter_template(sel, "resize", quiet=True)
        roots = self.doc.adjust_roots(ok)
        left_out = len(self.doc.adjust_roots(sel)) - len(roots)
        return roots, self.doc.adjust_extent(roots), left_out

    def _sync_adjust_dialog(self, force: bool = False) -> None:
        """Show the current selection's box in the open Adjust Position and
        Size window -- only when the box changed (``force``: always), so an
        unrelated refresh does not reset what is being typed."""
        dlg = getattr(self, "_dialogs", {}).get("adjust")
        if dlg is None:
            return
        roots, box, left_out = self._adjust_targets()
        key = (id(self.doc), tuple(roots), box, left_out)
        if not force and key == getattr(dlg, "_ctg_key", None):
            return
        dlg._ctg_key = key
        note = ""
        if left_out:
            note = (
                "%d selected object(s) left out: locked, or part of a genie "
                "(Edit > Break Lock Mode releases locked objects)." % left_out
            )
        rect = None if box is None else (box[0], box[1], box[2] - box[0], box[3] - box[1])
        dlg.set_box(rect, note)

    def _apply_adjust(self, dlg) -> None:
        """OK / Apply of Adjust Position and Size."""
        roots, box, _left_out = self._adjust_targets()
        if not roots or box is None:
            return
        if dlg.box() != (box[0], box[1], box[2] - box[0], box[3] - box[1]):
            # the values were typed for another box (the selection changed)
            self._sync_adjust_dialog(force=True)
            self.statusBar().showMessage("The selection changed: check the values and apply again.", 5000)
            return
        x, y = dlg.position()
        sx, sy = dlg.scale()
        if self.doc.adjust_position_and_size(roots, x, y, sx, sy):
            self._refresh(rebuild_tree=False)
        self._sync_adjust_dialog(force=True)

    def _goto_dialog(self) -> None:
        """Tools > Goto Object."""
        if self.doc is None:
            return
        existing = self._dialogs.get("goto")
        if self._raise_modeless(existing):
            existing.set_document(self.doc)
            return
        from ..dialog.dialogs import GotoObjectDialog

        dlg = GotoObjectDialog(
            self.doc, self, modeless=True, current=self._sel[0] if len(self._sel) == 1 else None
        )
        # the picked row is selected on the page as it changes (CtDraw)
        dlg.highlighted.connect(self._goto_highlight)
        dlg.goToRequested.connect(self._goto_frame)
        dlg.propertiesRequested.connect(self._goto_properties)
        dlg._ctg_reads_page = True
        self._show_modeless(dlg, "goto", scope="window")

    def _goto_index(self, index):
        """The object a Goto Object row names now."""
        dlg = self._dialogs.get("goto")
        if dlg is None or self.doc is None or not isinstance(index, int) or index < 0:
            return index
        same = getattr(dlg, "_doc", None) is self.doc
        if same and getattr(dlg, "_serial", None) == getattr(self.doc, "derive_serial", None):
            return index
        rec = getattr(dlg, "_records", {}).get(index) if same else None
        objs = self.doc.parsed.page.objects
        found = next((k for k, po in enumerate(objs) if po is rec), None) if rec is not None else None
        if found is None and rec is not None:
            # (a record replaced by another, a genie composed again from its library: the
            # one that took its place, as the object windows follow it)
            from ...format.geometry import successor_index

            found = successor_index(objs, rec, index)
        # (not inside the tree's own signal: the rebuild clears its rows)
        QtCore.QTimer.singleShot(0, self, lambda d=dlg: self._goto_rebuild(d))
        return found

    def _sync_goto_dialog(self) -> None:
        """An open Goto Object window shows the object selected on the page (a click on
        another object moves its current row there)."""
        dlg = getattr(self, "_dialogs", {}).get("goto")
        if dlg is None or self.doc is None or len(self._sel) != 1:
            return
        with contextlib.suppress(RuntimeError):
            if getattr(dlg, "_doc", None) is not self.doc or getattr(dlg, "_serial", None) != getattr(
                self.doc, "derive_serial", None
            ):
                dlg.set_document(self.doc)
            dlg.follow_selection(self._sel[0])

    def _goto_rebuild(self, dlg) -> None:
        if self.doc is None or self._dialogs.get("goto") is not dlg:
            return
        with contextlib.suppress(RuntimeError):
            dlg.set_document(self.doc)

    def _goto_highlight(self, index: int) -> None:
        """A row picked in Goto Object: its object selected on the page."""
        i = self._goto_index(index)
        if i is not None:
            self._goto_preview(i)

    def _goto_properties(self, index: int) -> None:
        """Properties… in Goto Object."""
        if self.doc is None:
            return
        index = self._goto_index(index)
        if index is None:
            self.statusBar().showMessage("That object is no longer on the page.", 5000)
            return
        if index == -1:
            self.page_properties()
        elif 0 <= index < len(self.doc.page.objects):
            self._goto_preview(index)
            self._open_sheet_of(index)

    def _goto_frame(self, index: int) -> None:
        """Go To in Goto Object: the object selected and framed (a small one
        at a readable zoom); the page row shows the whole page."""
        if self.doc is None:
            return
        index = self._goto_index(index)
        if index is None:
            self.statusBar().showMessage("That object is no longer on the page.", 5000)
            return
        if index == -1:
            self.view.fit_page()  # (it glides; Previous View comes back)
            return
        if not 0 <= index < len(self.doc.page.objects):
            return
        # (selected without centring: frame_rect moves the view, and Previous View comes
        # back to the view before Go To, not to the object centred at the old zoom)
        self._goto_preview(index, centre=False)
        sel = self._sel or [index]
        box = self.doc.selection_extent(sel, bounds=True)
        if box is None:
            return
        l, t, r, b = box
        cx, cy = (l + r) / 2.0, (t + b) / 2.0
        w, h = max(r - l, 160), max(b - t, 120)
        pad = max(8, (w + h) // 20)
        self.view.frame_rect(QtCore.QRectF(cx - w / 2.0 - pad, cy - h / 2.0 - pad, w + 2 * pad, h + 2 * pad))

    def _goto_preview(self, index: int, centre: bool = True) -> None:
        """Select object ``index`` exactly (a touch / keyboard record: its
        owner, which draws) and, with ``centre``, bring it into view (a move that
        Previous View comes back from, as a pan: rows picked in a row are one step)."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        from ...format import inputs

        obj = self.doc.page.objects[index]
        if obj.raw.get("_type_code") in inputs.INPUT_TCS and obj.parent is not None:
            index = obj.parent
        self._on_dock_selection([index], reveal=False)  # (moved into view below)
        self.objects_dock.set_selection(self._sel)  # the tree follows too
        if centre:
            g = self.doc.page.objects[index].geometry
            self.view.pan_to(QtCore.QPointF(g.x + g.extent_x / 2, g.y + g.extent_y / 2))

    def _find_dialog(self, replace_mode: bool):
        """Edit > Find / Replace: one modeless window per editor window. Returns it."""
        dlg = getattr(self, "_find_dlg", None)
        if dlg is None:
            from ..dialog.find_dialog import FindReplaceDialog

            dlg = FindReplaceDialog(
                self.doc,
                self,
                replace_mode=replace_mode,
                documents=self.open_documents,
                current=lambda: self.doc,
                template_editable=lambda: self._break_lock or not self._template_locked(),
                break_lock=lambda: self._break_lock,
                open_page=self.open_shown,
            )
            dlg.setModal(False)
            dlg.found.connect(self._find_goto)
            dlg.replaced.connect(self._find_replaced)
            dlg.propertiesRequested.connect(self._find_properties)

            def _gone(_result, d=dlg):
                if getattr(self, "_find_dlg", None) is d:
                    self._find_dlg = None
                d.deleteLater()

            dlg.finished.connect(_gone)
            self._find_dlg = dlg
        else:
            dlg.set_replace_mode(replace_mode)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _find_goto(self, doc, index: int) -> None:
        """Show a Find hit: its page (switching tabs), its object selected
        exactly (a genie member too, as Goto Object does)."""
        if doc is not self.doc:
            # (documents compare by identity: a dataclass == compares fields;
            # the tabs as they are -- a hit's page is open, the restored tabs
            # not shown yet need not be read for this)
            if not any(e["doc"] is doc for e in self._docs):
                self.statusBar().showMessage("That page is closed. Open it again to see the match.", 6000)
                return
            self.set_document(doc)
        if not 0 <= index < len(self.doc.page.objects):
            self._set_selection([])
            self.statusBar().showMessage(
                "The match is in the page's properties (File > Page Properties).", 6000
            )
            return
        self._goto_preview(index)

    def _find_replaced(self, docs) -> None:
        """Replacements changed ``docs``: redraw the page shown; the windows
        of their objects and pages re-read them (those of pages not shown
        when their tab is shown again)."""
        self._mark_modeless_reason(docs, "replace")
        self._sync_modeless("replace")
        self._refresh()
        self._update_actions()

    def _find_properties(self, doc, index: int, hit) -> None:
        """'Properties…' on a Find row: the properties holding its field -- a
        genie's parameters for a genie parameter or a genie member's field
        its parameter sets, the Page Properties for a page field, else the
        object's sheet on the page that edits the field."""
        self._find_goto(doc, index)
        if self.doc is not doc:
            return
        if index < 0:
            self.page_properties()
            return
        if getattr(hit, "via_genie", False) and hit.genie is not None:
            from ...features.find_replace import Hit, resolve_hit

            g = resolve_hit(doc, Hit(doc, hit.genie_po, hit.genie, "", "", "", "", ""))
            if g is not None:
                self._edit_genie_at(g)
                return
        from ..dialog.find_dialog import page_for_key

        page = page_for_key(getattr(hit, "key", ""))
        if page and not self._read_only_reason(index):
            self.edit_object_page(index, page)
        else:
            self._open_sheet_of(index)

    # ------------------------------------------------------------- lock mode
    def _lock_selection(self) -> None:
        """Edit > Lock: CtDraw persists the lock in the file (dwFlags 0x4), so
        it survives save/reopen and is honoured by CtDraw itself."""
        if self.doc is None or not self._sel:
            return
        n = self.doc.set_locked(self._sel, True)
        self._after_lock_change(clear=True)
        self.statusBar().showMessage(
            "Locked %d object%s. To unlock, point at it and press Ctrl+L, or use the padlock "
            "in the Objects panel." % (n, "" if n == 1 else "s"),
            6000,
        )

    def _unlock_selection(self) -> None:
        """Edit > Unlock: clears the persisted lock (reachable in Break Lock
        Mode, which is how a locked object can be selected at all)."""
        if self.doc is None or not self._sel:
            return
        self._locked.difference_update(self.doc.root_of(i) for i in self._sel)
        n = self.doc.set_locked(self._sel, False)
        self._after_lock_change(clear=False)
        self.statusBar().showMessage("Unlocked %s." % (plural(n, "object")), 5000)

    def _after_lock_change(self, clear: bool) -> None:
        """A lock changed: the rows show it at once (the Objects tree's lock mark), and a
        locked selection is let go the way the canvas lets go of one (every panel)."""
        if clear:
            self._sel = []
            self._set_selection([])
        self._refresh()
        self._update_title()

    def _lock_toggle(self) -> None:
        """Edit > Lock or Unlock Selection (Ctrl+L): lock the selection, or unlock it when
        every selected object is locked (in Break Lock Mode); with nothing selected, unlock
        the locked object under the pointer. One undo step."""
        if self.doc is None:
            return
        if not self._sel:
            root = self.view.locked_under_pointer()
            if root is not None:
                self._unlock_root(root)
            else:
                self.statusBar().showMessage(
                    "Select the objects to lock, or point at a locked object to unlock it.", 5000
                )
            return
        n = len(self.doc.page.objects)
        roots = sorted({self.doc.root_of(i) for i in self._sel if 0 <= i < n})
        if roots and all(r in self._locked or self._ct_locked(r) for r in roots):
            self._unlock_selection()
        else:
            self._lock_selection()

    def _unlock_root(self, root: int) -> None:
        """Unlock one locked unit (its right-click 'Unlock' entry, Ctrl+L over it)."""
        if self.doc is None or not 0 <= root < len(self.doc.page.objects):
            return
        self._locked.discard(root)
        if self.doc.set_locked([root], False):
            label = self._unit_label(root)
            self._refresh()
            self._update_title()
            self.statusBar().showMessage("Unlocked %s." % label, 5000)

    def _unlock_all(self) -> None:
        """Edit > Unlock All Objects (Ctrl+Alt+L): every locked object of the page (not the
        template's), one undo step."""
        if self.doc is None:
            return
        page = self.doc.page
        roots = [
            r for r in page.roots() if self._ct_locked(r) and not page.objects[r].raw.get("_from_template")
        ]
        self._locked.difference_update(roots)
        n = self.doc.set_locked(roots, False) if roots else 0
        if n:
            self._refresh()
            self._update_title()
        self.statusBar().showMessage(
            "Unlocked %s." % (plural(n, "object")) if n else "No object on the page is locked.",
            5000,
        )

    def _locked_root_of(self, index: int):
        """The root of the unit ``index`` belongs to when that unit is locked by Edit >
        Lock or the Graphics Builder's lock (not the page template), else None."""
        if self.doc is None or self._break_lock or not 0 <= index < len(self.doc.page.objects):
            return None
        root = self.doc.root_of(index)
        if self._is_template_obj(root):
            return None
        return root if (root in self._locked or self._ct_locked(root)) else None

    def _lock_as_background(self) -> None:
        """Right-click > Lock as Background: the big rectangle or picture behind the others
        is locked, so a drag on it boxes the objects on it (one undo step)."""
        root = self._background_candidate()
        if root is None:
            return
        self.doc.set_locked([root], True)
        self._after_lock_change(clear=True)
        self.statusBar().showMessage("Locked. Use the padlock in the Objects panel to unlock it.", 6000)

    def _background_candidate(self):
        """The one selected unit that may be locked as the background: a filled rectangle
        or a picture larger than a quarter of the page; else None."""
        if self.doc is None or len(self._sel) != 1 or isinstance(self.doc, ElementDocument):
            return None
        n = len(self.doc.page.objects)
        root = self.doc.root_of(self._sel[0]) if 0 <= self._sel[0] < n else None
        if root is None:
            return None
        o = self.doc.page.objects[root]
        kind = o.kind.value
        picture = bool(o.raw.get("_pic_bytes") or o.raw.get("_embedded_bmp"))
        if not ((kind == "rectangle" and o.style.filled) or picture):
            return None
        ap = self.doc.page.appearance
        area = float(ap.width or 1024) * float(ap.height or 768)
        g = o.geometry
        return root if g.extent_x * g.extent_y > 0.25 * area else None

    def _toggle_hidden_selection(self) -> None:
        """Edit > Hide or Show Selection (Ctrl+Shift+H): hide the selected objects in the
        editor (like the Objects panel's eye: never saved), or show them again when every
        one of them is hidden."""
        if self.doc is None or not self._sel:
            return
        n = len(self.doc.page.objects)
        roots = sorted({self.doc.root_of(i) for i in self._sel if 0 <= i < n})
        self.set_roots_hidden(roots, not all(r in self._hidden_roots for r in roots))

    def set_roots_hidden(self, roots, hidden: bool) -> None:
        """Hide or show units in the editor only (the eye; remembered for the page)."""
        if self.doc is None:
            return
        roots = [r for r in roots if 0 <= r < len(self.doc.page.objects)]
        if not roots:
            return
        if hidden:
            self._hidden_roots.update(roots)
        else:
            self._hidden_roots.difference_update(roots)
        self.scene.set_hidden(self._hidden_roots)
        sel = [] if hidden else list(self._sel)
        if not self.objects_dock.update_units(self.doc.page, roots, sel, hidden=self._hidden_roots):
            self.objects_dock.set_page(self.doc.page, sel, hidden=self._hidden_roots)
        self._update_hidden_chip()
        self._save_hidden()
        if hidden:
            self._set_selection([])
        k = len(roots)
        self.statusBar().showMessage(
            (
                "%d object%s hidden in the editor. View > Show All Hidden Objects shows them again."
                if hidden
                else "%d object%s shown."
            )
            % (k, "" if k == 1 else "s"),
            6000,
        )

    def _break_locks(self) -> None:
        # toggle Break Lock Mode: while ON, every lock (user / CtDraw bit 0x4 /
        # template) is ignored, so locked objects can be selected, moved, edited.
        self._break_lock = self.act_break_lock.isChecked()
        self.statusBar().showMessage(
            "Break Lock Mode on: locked objects can be edited."
            if self._break_lock
            else "Break Lock Mode off: locked objects cannot be edited.",
            5000,
        )
        self._update_actions()

    def _set_selection(self, indices: list, reveal: bool = False) -> None:
        """THE way the window selects (every caller but the canvas's own clicks): the
        canvas, the Objects panel, the Properties panel, the selection label, the commands
        and the contextual ribbon follow; ``reveal`` scrolls the selection into view
        (Settings 'Scroll to objects selected in a panel')."""
        self._sel = list(indices)
        self.view.set_selection(self._sel)
        self.objects_dock.set_selection(self._sel)
        self.props.set_target(self.doc, self._sel)
        self._update_sel_label()
        self._update_actions()
        self._queue_context_update("window")
        self._note_selection_history(self._sel)
        if reveal and self._sel:
            self._reveal_selection()

    # ------------------------------------------------- selecting and transforming
    def apply_select_options(self) -> None:
        """Settings > Canvas / Editing / Appearance: the canvas's selecting, moving and
        handle choices (read again after every change in Settings)."""

        from ..editor import canvas_look as _canvas_look

        st = app_settings()

        def flag(key, default):
            try:
                return bool(st.value(key, default, type=bool))
            except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
                return default

        _canvas_look.set_handle_size(st.value("view/handle_size", "medium"))
        _canvas_look.set_canvas_lines(
            st.value("view/guide_width", "thin"),
            st.value("view/guide_reach", "near"),
            st.value("view/outline_width", "thin"),
        )
        _canvas_look.set_modifier_scheme(
            str(st.value("canvas/modifier_scheme", "builder")), str(st.value("canvas/drag_copy_key", "ctrl"))
        )
        v = self.view
        v.SELECTION_BOX = flag("view/selection_box", True)
        v.BOX_PREVIEW = flag("canvas/box_preview", True)
        v.BOX_FROM_BACKGROUND = flag("canvas/box_from_background", False)
        v.CLICK_SELECTED_ISOLATES = flag("canvas/click_selected_isolates", False)
        shift_drag = str(st.value("canvas/ctrl_shift_drag", "zoom"))
        v.CTRL_SHIFT_DRAG = "touch" if shift_drag == "touch" else "zoom"
        v.TAB_SELECTS = option("tab_selects_objects")
        v.TAB_ORDER = "drawing" if str(st.value("sel/tab_order", "reading")) == "drawing" else "reading"
        v.set_multi_box_source(self._multi_box_of)
        v.set_unit_labeler(self._unit_label)
        v.set_locked_probe(self._locked_root_of)
        # (the tool tips name the keys of the scheme chosen)
        sg = getattr(self, "act_smart_guides", None)
        if sg is not None:
            sg.setToolTip(
                "While you drag, snap the object's edges and centre to other objects "
                "and the page (hold %s to drag freely)"
                % ("Alt+Shift" if _canvas_look.drag_copy_key() == "alt" else "Alt")
            )
        v.viewport().update()

    def _multi_box_of(self, roots):
        """The box a multi-selection's handles scale: Adjust Position and Size's box of the
        units it would change (locked objects and lone genie members left out)."""
        if self.doc is None or self.doc.parsed is None or not roots:
            return None
        n = len(self.doc.parsed.page.objects)
        ok = self._filter_template([i for i in roots if 0 <= i < n], "resize", quiet=True)
        return self.doc.adjust_extent(self.doc.adjust_roots(ok)) if ok else None

    def _on_selection_scaled(self, x: int, y: int, sx: float, sy: float) -> None:
        """A multi-selection's box dragged by a handle: Adjust Position and Size with that
        position and scale, one undo step (the same records its OK writes)."""
        self.commit_nudge()
        roots, box, _left_out = self._adjust_targets()
        if not roots or box is None:
            self._refresh(rebuild_tree=False)
            return
        if self.doc.adjust_position_and_size(roots, x, y, sx, sy):
            self._refresh(rebuild_tree=False)
            self.statusBar().showMessage(
                "Scaled %d objects to %d %% by %d %%." % (len(roots), round(sx * 100), round(sy * 100)), 4000
            )
        self._sync_adjust_dialog(force=True)

    def _rotate_steps(self, steps: int) -> None:
        """The rotation grip let go: 1 = a quarter turn clockwise, 2 = a half turn, 3 = a
        quarter turn anticlockwise; one undo step."""
        steps %= 4
        if self.doc is None or not self._sel or not steps:
            return
        self.commit_nudge()
        sel = self._filter_template(self._sel, "rotate")
        if not sel:
            return
        before = len(self.doc._undo)
        try:
            with self.doc.batch("rotate"):
                if steps == 3:
                    self.doc.rotate_objects(sel, clockwise=False)
                else:
                    for _k in range(steps):
                        self.doc.rotate_objects(sel, clockwise=True)
        except TransformError as e:
            if len(self.doc._undo) > before:
                self.doc.undo()  # (half of a half turn: the page as it was)
            self._refresh()
            warn(self, "Rotate", str(e))
            return
        self._refresh()
        self.statusBar().showMessage(
            {
                1: "Turned a quarter turn clockwise.",
                2: "Turned a half turn.",
                3: "Turned a quarter turn anticlockwise.",
            }[steps],
            4000,
        )
