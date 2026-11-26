"""Arrange, tools, text, find, print, view, import and guideline commands."""

from __future__ import annotations

from ..main_window_base import (  # noqa: F401
    CLIPBOARD_MIME,
    PASTE_POINT_UNREAD,
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    TransformError,
    app_settings,
    contextlib,
    hashlib,
    json,
    option,
    os,
)
from ..main_window_base import (  # noqa: F401
    _TOOLS,
)


class Commands:
    """MainWindow's Arrange / Tools, lock mode, the Text menu, find / print / view, import /
    bitmaps, save all / run / defaults and guidelines."""

    # ------------------------------------------------ Arrange / Tools actions
    def _rotate(self, clockwise: bool) -> None:
        if self.doc is None or not self._sel:
            return
        sel = self._filter_template(self._sel, "rotate")
        if sel:
            try:
                self.doc.rotate_objects(sel, clockwise=clockwise)
            except TransformError as e:
                QtWidgets.QMessageBox.warning(self, "Rotate", str(e))
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
                QtWidgets.QMessageBox.warning(self, "Flip", str(e))
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
        self.statusBar().showMessage("Aligned %d objects" % len({self.doc.root_of(i) for i in sel}), 4000)

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
                "and the objects of a genie are not moved)",
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
            "Spaced %d objects equally" % len({self.doc.root_of(i) for i in sel}), 4000
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
                "objects and the objects of a genie are not moved)",
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
            "Arranged %d objects in a grid" % len({self.doc.root_of(i) for i in sel}), 4000
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
            self.statusBar().showMessage("The selection changed: check the values and apply again", 5000)
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
                open_page=self._find_open_page,
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

    def _find_open_page(self, path: str):
        """A Find row of a closed page: open it (or show it) in a tab; its document, or
        None when it could not be opened."""
        try:
            self.open_document(path)
        except Exception as e:  # noqa: BLE001 -- swallow-ok: said in the status bar
            self.statusBar().showMessage("%s could not be opened: %s" % (os.path.basename(path), e), 8000)
            return None
        doc = self.doc
        here = os.path.normcase(os.path.abspath(getattr(doc, "path", "") or "")) if doc is not None else None
        if here is None or here != os.path.normcase(os.path.abspath(path)):
            return None
        return doc

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
            from ...features.find_replace import _resolve, Hit

            g = _resolve(doc, Hit(doc, hit.genie_po, hit.genie, "", "", "", "", ""))
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
        self.statusBar().showMessage("Unlocked %d object%s." % (n, "" if n == 1 else "s"), 5000)

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
            "Unlocked %d object%s." % (n, "" if n == 1 else "s") if n else "No object on the page is locked.",
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
            "Break Lock Mode on: locked objects can be edited"
            if self._break_lock
            else "Break Lock Mode off: locked objects cannot be edited",
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
            QtWidgets.QMessageBox.warning(self, "Rotate", str(e))
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

    # ------------------------------------------------------------ Text menu
    def _text_targets(self, indices=None, quiet: bool = False) -> list:
        """The selected TEXT records and buttons (font edits apply to these;
        a button's font is its caption font block) -- not a placed genie's
        texts: CtDraw re-composes those from the genie's element on load.
        ``indices`` = another selection; ``quiet`` = no status message."""
        if self.doc is None:
            return []
        from ..objprops.registry import placed_genie_of

        n = len(self.doc.page.objects)
        out, in_genie = [], 0
        for i in self._sel if indices is None else indices:
            if not 0 <= i < n:
                continue
            for j in self.doc.subtree(i):
                if self.doc.page.objects[j].raw.get("_type_code") in (0x05, 0x1001):
                    if placed_genie_of(self.doc, j) is not None:
                        in_genie += 1
                        continue
                    out.append(j)
        if in_genie and not out and not quiet:
            self.statusBar().showMessage(
                "The selected text is part of a genie, whose objects come from its "
                "definition. Right-click the genie > Edit Definition to change it.",
                8000,
            )
        return sorted(set(out))

    def _font_dialog(self) -> None:
        """Text > Font: a modeless window on the selected texts (it follows
        the selection); Apply / OK change them, one undo step."""
        if self._raise_modeless(self._dialogs.get("font")):
            self._sync_selection_windows()
            return
        targets = self._text_targets()
        if not targets:
            return
        from ..dialog.dialogs import FontDialog

        dlg = FontDialog(
            self.doc.page.objects[targets[0]].style,
            self,
            targets=lambda doc, sel: self._text_targets(sel, quiet=True) if doc is self.doc else [],
        )
        dlg.applyRequested.connect(lambda d=dlg: self._apply_font(d))
        self._show_modeless(dlg, "font", scope="selection")

    def _apply_font(self, dlg) -> None:
        """Apply / OK of the Font window: the texts selected now."""
        targets = self._text_targets()
        if not targets:
            return
        vals = dlg.values()
        changed = False
        with self.doc.batch("font"):
            for i in targets:
                changed |= bool(self.doc.set_font(i, **vals))
        dlg.mark_applied()
        if changed:
            self._refresh()

    def _toggle_font_flag(self, key: str) -> None:
        targets = self._text_targets()
        if not targets:
            return
        cur = bool(getattr(self.doc.page.objects[targets[0]].style, key))
        changed = False
        with self.doc.batch(key):
            for i in targets:
                changed |= bool(self.doc.set_font(i, **{key: not cur}))
        if changed:
            self._refresh()

    def _set_justify(self, mode: str) -> None:
        changed = False
        with self.doc.batch("justify"):
            for i in self._text_targets():
                changed |= bool(self.doc.set_font(i, justify=mode))
        if changed:
            self._refresh()

    def _set_text_effect(self, effect: int) -> None:
        changed = False
        with self.doc.batch("text effect"):
            for i in self._text_targets():
                changed |= bool(self.doc.set_text_effect(i, effect))
        if changed:
            self._refresh()

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
            "Pasted the appearance onto %d object%s." % (n, "" if n == 1 else "s")
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
        st = self._settings()
        folder = (
            str(st.value("dir/export", "") or "") or os.path.dirname(os.path.abspath(self.doc.path))
            if self.doc.path
            else ""
        )
        filters = "PNG picture (*.png);;SVG drawing (*.svg);;PDF document (*.pdf)"
        path, chosen = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Page", os.path.join(folder, name + ".png"), filters
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
            last = int(st.value("export/png_scale", 100) or 100)
            items = ["%d %%" % s for s in self.EXPORT_SCALES]
            cur = items.index("%d %%" % last) if last in self.EXPORT_SCALES else 0
            pick, ok = QtWidgets.QInputDialog.getItem(
                self, "Export Page", "Scale (%d x %d page pixels):" % (w, h), items, cur, False
            )
            if not ok:
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
            QtWidgets.QMessageBox.warning(self, "Export Page", "The page could not be exported. %s" % exc)
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
        self.statusBar().showMessage("Page sent to printer", 4000)

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
                    items += [self.tool_actions[t] for t, _l, _i, _k in _TOOLS if t not in placed]
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
        wins = [self] + [w for w in (type(self)._instances or ()) if w is not self]
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
            self.statusBar().showMessage("Open a page first, then drop pictures on it", 5000)
            return
        self._import_files(paths, (int(pos.x()), int(pos.y())), trigger="drop")

    def _import_recent(self, path: str) -> None:
        """File > Import Recent: import that picture again."""
        if self.doc is None:
            return
        if not os.path.isfile(path):
            self.statusBar().showMessage("%s is no longer there" % path, 6000)
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
            self.statusBar().showMessage("Imported %d picture(s)" % len(new), 4000)
        if failed:
            from ..messages import list_box

            list_box(
                self,
                "Import",
                "%d file%s could not be imported." % (len(failed), "" if len(failed) == 1 else "s"),
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
            QtWidgets.QMessageBox.warning(self, "Import As Flashing", str(e))
            return
        from ...format.bitmaps import resample

        # CtDraw stretches the flash picture to the primary's size
        rgba2 = resample(pic2.data, pic2.width, pic2.height, pic1.width, pic1.height)
        idx = self._place_picture(pic1, flash=rgba2)
        self._sel = [idx]
        self._refresh()
        self.statusBar().showMessage("Imported a flashing bitmap (the page shows its primary picture)", 6000)

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
            objs = []
        for po in objs:
            primary, _flash = bitmaps.read_pictures(po)
            if primary is not None:
                return primary, name
        QtWidgets.QMessageBox.warning(
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
            QtWidgets.QMessageBox.warning(self, "Paste", str(e))
            return None
        if src is None:
            self.statusBar().showMessage("The clipboard picture could not be read", 4000)
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
        self.statusBar().showMessage("%s not found in the Plant SCADA install" % exe, 5000)

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

    # ------------------------------------------- save all / run / defaults
    def save_all(self) -> None:
        """File > Save All."""
        from ..jobs.job_progress import JobProgress

        n = n_failed = 0
        failed, warned = [], []
        todo = []
        for win in list(_mw().MainWindow._instances):
            if win._flush_text_edit():
                win._refresh()
            todo += [(win, e["doc"]) for e in list(win._docs) if e["doc"].dirty]
        prog = (
            JobProgress(
                self,
                "Save All",
                "Saving the pages…",
                phase="save",
                total=len(todo),
                cancel="Stop After This Page",
            )
            if len(todo) > 1
            else None
        )
        left = 0
        try:
            for k, (win, doc) in enumerate(todo):
                name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
                if prog is not None and not prog.tick(
                    k, len(todo), "Saving %s… (%d of %d)" % (name, k + 1, len(todo))
                ):
                    left = len(todo) - k
                    break
                errs: list = []
                try:
                    ok = win._save_document(doc, errs)
                except RuntimeError:  # swallow-ok: its window closed meanwhile: not saved
                    ok = False
                if ok:
                    n += 1
                    warned.extend(errs)
                else:
                    n_failed += 1
                    failed.extend(errs or ["%s was not saved." % name])
        finally:
            if prog is not None:
                prog.close()
        for win in list(_mw().MainWindow._instances):
            with contextlib.suppress(RuntimeError):
                win._update_title()
        from ..names import plural

        self.statusBar().showMessage(
            "Saved %s%s%s."
            % (
                plural(n, "page"),
                (", %d not saved" % n_failed) if n_failed else "",
                (", %d left when Save All was stopped" % left) if left else "",
            ),
            8000,
        )
        if failed or warned:
            from ..messages import list_box

            text = "Saved %s." % plural(n, "page")
            if failed:
                text += (
                    " %s not saved: %s changes are still open here. To keep them, show %s tab "
                    "and use Save As to save under another name or in another folder."
                    % (
                        "1 page was" if n_failed == 1 else "%d pages were" % n_failed,
                        "its" if n_failed == 1 else "their",
                        "its" if n_failed == 1 else "each page's",
                    )
                )
            if warned:
                text += " %s saved with a problem." % (
                    "1 was" if len(warned) == 1 else "%d were" % len(warned)
                )
            list_box(
                self,
                "Save All",
                text,
                ["Not saved: " + t for t in failed] + ["Saved, with a problem: " + t for t in warned],
            )

    def _project_needs_compile(self) -> bool:
        """Whether Run has something to compile: the page has unsaved changes,
        or its project -- or the root project Run compiles -- is UNCOMPILED
        in its PROJECT.CIT (every save that registers a page marks it so;
        CtDraw's Run tests the same status)."""
        if self.doc is None:
            return False
        if self.doc.dirty:
            return True
        from ...io import projectcit

        pdir = os.path.dirname(os.path.abspath(self.doc.path))
        dirs = [pdir]
        try:
            from ...backend.compiler import root_project_for
            from ...format.projects import master_projects
            from ...io.citect_config import live_user_dir

            root = root_project_for(pdir)
            user = live_user_dir()
            master = master_projects(user) if user else {}
            path = master.get(root.lower()) or master.get(root)
            if path:
                dirs.append(path)
        except Exception:  # noqa: BLE001 -- the page's own project decides then
            pass
        for d in dirs:
            try:
                cit = projectcit.find(d)
                state = projectcit.read_streams(cit).get("CompState", b"") if cit else b""
            except Exception:  # noqa: BLE001
                state = b""
            if bytes(state).strip(b"\0 ").upper().startswith(b"UNCOMPILED"):
                return True
        return False

    def _ask_compile_before_run(self):
        """Ask whether to compile before running."""
        if not option("compile_enquiry") or not self._project_needs_compile():
            return True
        pdir = os.path.dirname(os.path.abspath(self.doc.path))
        try:
            from ...backend.compiler import root_project_for

            project = root_project_for(pdir)
        except Exception:  # noqa: BLE001
            project = os.path.basename(pdir)
        answer = self._compile_enquiry(project, bool(self.doc.dirty))
        return {"compile": True, "run": False}.get(answer)

    def _compile_enquiry(self, project: str, dirty: bool) -> str:
        """The Compile enquiry message: 'compile', 'run' (without compiling)
        or 'cancel'."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Run")
        box.setText("Compile project %s before running it?" % project)
        info = (
            "The project has changes that are not compiled. Without compiling, the "
            "runtime runs the project as it was last compiled."
        )
        if dirty:
            info += (
                " This page's unsaved changes are saved only when you compile; "
                "they stay in the editor otherwise."
            )
        box.setInformativeText(info)
        comp = box.addButton("Compile and Run", QtWidgets.QMessageBox.YesRole)
        run = box.addButton("Run Without Compiling", QtWidgets.QMessageBox.NoRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(comp)
        box.exec()
        clicked = box.clickedButton()
        return "compile" if clicked is comp else ("run" if clicked is run else "cancel")

    def _run_project(self) -> None:
        """File > Run: save, compile and start the runtime."""
        # (no page open: the project of the most recent page, as File > Compile)
        target = None if isinstance(self.doc, ElementDocument) else self._compile_target()
        if target is None:
            self._launch_external("RuntimeManager.exe")
            return
        if self._outside_install(target[0], "run"):
            return
        compile_now = self._ask_compile_before_run()
        if compile_now is None:
            return
        pdir = target[0]
        if compile_now:
            result = self.compile_project(show="errors")
            if result is None or result.errors or result.outcome != "completed":
                return
            project = getattr(result, "project", "") or os.path.basename(pdir)
        else:
            from ...backend.compiler import root_project_for

            try:
                project = root_project_for(pdir)
            except Exception:  # noqa: BLE001
                project = os.path.basename(pdir)
        from ...backend import ctapi, runtime

        page = os.path.splitext(os.path.basename(target[1]))[0] if target[1] else project
        done = "Compiled" if compile_now else "Not compiled"
        stop_note = (
            "Plant SCADA is shut down first: everything that runtime does on this "
            "computer stops (displays, alarms, trends, logging, device "
            "communication) until it has started again."
        )
        running = runtime.is_running()
        if running and not target[1] and runtime.runs_project(pdir):
            self.statusBar().showMessage(
                "%s. The runtime is running %s and shows the changes when it next displays "
                "a page." % (done, project),
                8000,
            )
            return
        # the runtime already running THIS project re-reads a re-displayed page
        # (drawing + the recompiled .rdb; live-verified) -- no restart needed
        if running and runtime.runs_project(pdir):
            try:
                if ctapi.redisplay(page):
                    self.statusBar().showMessage("%s. The runtime shows the updated %s." % (done, page), 8000)
                    return
                self.statusBar().showMessage(
                    "%s. The runtime shows the changes when it next displays %s." % (done, page), 8000
                )
                return
            except Exception as e:  # noqa: BLE001 -- no CtAPI: a restart shows it
                why = str(e) or type(e).__name__
            if not self._confirm(
                "Restart the Runtime",
                "The runtime is running %s, but ctgedit could not ask it to show "
                "the updated %s (%s)." % (project, page, why),
                "Restart Runtime",
                "Restart the runtime to see the change? " + stop_note,
            ):
                self.statusBar().showMessage(
                    "%s. The runtime shows the changes when it next displays %s." % (done, page), 10000
                )
                return
        elif running:
            other = runtime.running_project()
            if other:
                text = "The Plant SCADA runtime on this computer is running project %s." % other
            else:
                text = (
                    "A Plant SCADA runtime is running on this computer, and ctgedit "
                    "cannot tell which project it runs."
                )
            if not self._confirm(
                "Stop the Running Runtime",
                text,
                "Stop and Run %s" % project,
                "Stop it and run %s instead? %s" % (project, stop_note),
            ):
                self.statusBar().showMessage(
                    "%s %s. The running runtime was left as it is." % (done, project), 10000
                )
                return
        # (on a worker thread: stopping a runtime takes up to a minute; the window says
        # the time so far and its Cancel leaves the wait: the runtime goes on being stopped
        # and started on the thread, and the status bar says how it ended)
        from ..prefs.job_options import poll_ms
        from ..jobs.job_progress import JobProgress

        text = (
            "Stopping the running runtime (this can take a minute)…" if running else "Starting the runtime…"
        )
        prog = JobProgress(self, "Run", text, cancel="Stop Waiting")
        out: dict = {}

        def work():
            from ...infra import gcguard

            gcguard.before_worker()
            try:
                out["notes"] = runtime.start(pdir)
            except Exception as e:  # noqa: BLE001 -- said below
                out["error"] = e

        import threading

        th = threading.Thread(target=work, name="ctgedit-run", daemon=True)
        th.start()
        waiting = True
        try:
            while th.is_alive() and waiting:
                waiting = prog.wait(poll_ms())
        finally:
            prog.close()

        def finish():
            if "error" in out:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Run",
                    "The runtime could not be started: %s\n\nStart it from "
                    "the Runtime Manager or Plant SCADA Studio instead." % out["error"],
                )
                return
            self.statusBar().showMessage("Runtime starting: " + "; ".join(out.get("notes") or []) + ".", 8000)

        if not th.is_alive():
            finish()
            return
        # (the wait was left: the thread goes on, and says how it ended when it has)
        self.statusBar().showMessage(
            "The runtime is still being %s in the background. The status bar says when it "
            "has." % ("stopped and started" if running else "started"),
            8000,
        )
        timer = QtCore.QTimer(self)
        timer.setInterval(poll_ms())

        def look():
            if th.is_alive():
                return
            timer.stop()
            timer.deleteLater()
            with contextlib.suppress(RuntimeError):  # (the window closed meanwhile)
                finish()

        timer.timeout.connect(look)
        timer.start()

    # ------------------------------------------------------- guidelines
    def _guidelines_dialog(self) -> None:
        """View > Guidelines Setup: a modeless window on the guidelines of
        the page shown (it follows the page tabs and the guidelines dragged
        on the canvas while nothing is typed); Apply / OK set them."""
        if self._raise_modeless(self._dialogs.get("guides")):
            self._sync_guidelines_dialog()
            return
        from ..dialog.dialogs import GuidelinesDialog

        vert, horiz = self.view.guidelines()
        dlg = GuidelinesDialog(
            vert,
            horiz,
            self.view.guides_visible(),
            self.view.snap_guides(),
            self,
            page=self._doc_name(self.doc) if self.doc is not None else "",
        )
        dlg._ctg_page_doc = self.doc
        dlg._ctg_reads_page = True
        dlg._ctg_follow = lambda _doc, d=dlg: self._sync_guidelines_dialog()
        dlg.applyRequested.connect(lambda d=dlg: self._apply_guidelines(d))
        self._show_modeless(dlg, "guides", scope="window")

    def _apply_guidelines(self, dlg) -> None:
        """Apply / OK of Guidelines Setup: the page it shows (a page not
        shown now keeps them for its tab)."""
        vert, horiz, display, snap = dlg.values()
        target = getattr(dlg, "_ctg_page_doc", None)
        if target is None or target is self.doc:
            self.view.set_guidelines(vert, horiz)
            self._save_guidelines()
        else:
            self._store_guidelines(target, vert, horiz)
        self.view.set_guides_visible(display)
        self.act_show_guides.setChecked(display)
        self.view.set_snap_guides(snap)
        self.act_snap_guides.setChecked(snap)
        dlg.mark_applied()

    def _sync_guidelines_dialog(self) -> None:
        """The open Guidelines Setup shows the page shown now, while nothing
        is typed in it."""
        dlg = self._dialogs.get("guides")
        if dlg is None or self.doc is None:
            return
        try:
            if dlg.has_pending_edits():
                return
            vert, horiz = self.view.guidelines()
            dlg.load(
                vert,
                horiz,
                self.view.guides_visible(),
                self.view.snap_guides(),
                page=self._doc_name(self.doc),
            )
            dlg._ctg_page_doc = self.doc
        except RuntimeError:
            pass

    def _toggle_snap_guides(self) -> None:
        self.view.set_snap_guides(self.act_snap_guides.isChecked())
        self._sync_guidelines_dialog()

    def _toggle_show_guides(self) -> None:
        self.view.set_guides_visible(self.act_show_guides.isChecked())
        self._sync_guidelines_dialog()

    # Guideline positions are EDITOR state, never written to the .ctg: kept per
    # page in the settings (CtDraw keeps one set for the session in citect.ini
    # [CTDRAW] GuideHorz / GuideVert -- ctgedit shows several pages at once, and
    # each page keeps its own)
    @staticmethod
    def _guidelines_page(doc) -> str | None:
        """What the guidelines are stored for: the page file (a library
        element: its library file + element name), or None (nothing to key)."""
        path = getattr(doc, "path", None)
        if not path:
            return None
        name = os.path.normcase(os.path.abspath(path))
        if isinstance(doc, ElementDocument):
            name += "|" + str(doc.element_name).lower()
        return name

    @classmethod
    def _guidelines_key(cls, doc) -> str | None:
        # a hash, not the path itself: a settings key must not hold '\' / ':'
        name = cls._guidelines_page(doc)
        if name is None:
            return None
        return "guides/%s" % hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]

    def _load_guidelines(self, doc) -> tuple[list, list]:
        key = self._guidelines_key(doc) if doc is not None else None
        if key is None:
            return [], []
        raw = app_settings().value(key, "", type=str)
        try:
            d = json.loads(raw) if raw else {}
            return ([int(v) for v in d.get("v", [])], [int(h) for h in d.get("h", [])])
        except ValueError, TypeError, AttributeError:
            return [], []

    def _save_guidelines(self) -> None:
        """Store the canvas's guidelines for the current page."""
        if self.doc is not None:
            self._store_guidelines(self.doc, *self.view.guidelines())

    def _store_guidelines(self, doc, vert, horiz) -> None:
        """Store ``vert`` / ``horiz`` as the guidelines of ``doc``."""
        key = self._guidelines_key(doc) if doc is not None else None
        if key is None:
            return
        st = app_settings()
        vert, horiz = list(vert), list(horiz)
        if vert or horiz:
            st.setValue(key, json.dumps({"page": self._guidelines_page(doc), "v": vert, "h": horiz}))
        else:
            st.remove(key)

    def _on_guidelines_changed(self, message: str) -> None:
        """A guideline was dragged, added or deleted on the canvas."""
        self._save_guidelines()
        self._sync_guidelines_dialog()
        if message:
            self.statusBar().showMessage(message, 4000)


def _mw():
    """ctgedit.ui.main_window (MainWindow is defined there, after this module is imported)."""
    from .. import main_window

    return main_window
