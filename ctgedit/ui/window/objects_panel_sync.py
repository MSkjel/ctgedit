"""The Objects panel kept in step with the page and the selection."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    QtCore,
    QtGui,
    QtWidgets,
    os,
)


class ObjectsPanelSync:
    """The Objects panel: its tree, its selection and its edits, in step with the canvas."""

    # -------------------------------------------------------- the Objects panel
    def _wire_objects_dock(self) -> None:
        """The Objects panel's requests and its right-click menu (after the commands are
        made: MainWindow.__init__)."""
        d = self.objects_dock
        d.activated.connect(self._on_object_activated)
        d.revealRequested.connect(self._reveal_index)  # (a double-click: Scrolls to it)
        d.renameRequested.connect(self._on_rename_requested)
        d.lockToggled.connect(self._on_lock_toggled)
        d.soloRequested.connect(self._solo_row)
        d.hideSelectionRequested.connect(self.hide_or_show_selection)
        d.statusMessage.connect(self._tree_status)
        d.selectable = self._tree_selectable
        d.selectable_key = self._units_state_key
        d.name_problem = self._tree_name_problem
        d.rename_blocked = self._tree_rename_blocked
        d.genie_values = self._tree_genie_values
        d.set_actions(self._tree_menu_actions())

    def _tree_genie_values(self, index: int) -> list:
        """The values genie ``index`` of the page shown gets (EditableDocument.genie_values)."""
        fn = getattr(self.doc, "genie_values", None)
        return fn(index) if fn is not None else []

    def _select_from_panel(self, indices) -> None:
        """Select ``indices`` from the Properties panel (a use in a genie's Inputs list) and
        bring them into view."""
        from ..dialog.quick_open import select_objects

        select_objects(self, indices)

    def _window_action(self, text: str):
        """The window's command labelled ``text`` (a command another part of the window
        adds: Hide or Show Selection, Select Parent), or None."""
        want = text.replace("&", "").replace("…", "").strip().lower()
        for a in self.findChildren(QtGui.QAction):
            if (
                a.text().replace("&", "").replace("…", "").strip().lower() == want
                and a.objectName() != "tree.local"
            ):
                return a
        return None

    def _tree_menu_actions(self) -> dict:
        """The commands of the Objects panel's right-click menu (ObjectsDock.MENU)."""
        acts = {
            "props": self.act_obj_props,
            "genie": self.act_genie_params,
            "definition": self._tree_definition,
            "rename": self.act_rename,
            "cut": self.act_cut,
            "copy": self.act_copy,
            "dup": self.act_dup,
            "del": self.act_del,
            "front": self.act_front,
            "fwd": self.act_fwd,
            "bwd": self.act_bwd,
            "back": self.act_back,
            "group": self.act_group,
            "ungroup": self.act_ungroup,
            "lock": self.act_lock,
            "unlock": self.act_unlock,
            "isolate": self.act_isolate,
            "show_all": self.act_show_all_roots,
            "zoom_sel": self.act_zoom_sel,
        }
        # (the commands other parts of the window add, when they are there)
        for name, texts in (
            ("lock_toggle", ("Lock or Unlock Selection",)),
            ("hide_toggle", ("Hide or Show Selection",)),
            ("select_parent", ("Select Parent",)),
            ("select_same", ("Select Same Element", "Select Same Library Element")),
        ):
            for text in texts:
                a = self._window_action(text)
                if a is not None:
                    acts[name] = a
                    break
        return acts

    def _tree_definition(self, index: int, menu):
        """'Edit Definition (lib.element)' for the row's genie or symbol, as the canvas
        menu has it; None when it has none."""
        if self.doc is None:
            return None
        try:
            defn = self._definition_of(index)
        except Exception:  # noqa: BLE001 -- swallow-ok: no definition, no entry
            defn = None
        if defn is None:
            return None
        a = QtGui.QAction(
            "Edit Definition (%s.%s)" % (os.path.splitext(os.path.basename(defn[0]))[0], defn[1]), menu
        )
        a.setObjectName("tree.local")
        a.triggered.connect(lambda _=False, d=defn: self._open_element(*d))
        return a

    def _tree_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)

    def _tree_selectable(self, root: int) -> bool:
        if self.doc is None or not 0 <= root < len(self.doc.page.objects):
            return False
        return not self._unit_locked(self.doc.root_of(root))

    def _tree_name_problem(self, index: int, name: str) -> str:
        if self.doc is None:
            return "No page is open."
        from ..dialog.rename_dialog import codepage_problem

        return codepage_problem(name) or self.doc.object_name_problem(index, name)

    def _tree_rename_blocked(self, index: int) -> str:
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return "Nothing to rename."
        if getattr(self.doc, "read_only", False):
            return "This page is read-only."
        if self._unit_locked(self.doc.root_of(index)):
            return "Locked: click its padlock in the Objects panel to unlock it first."
        return ""

    def _rename_refusal_of(self, index: int) -> str:
        """Why object ``index`` cannot be renamed ('' = it can): a template object, a part
        of a placed genie or symbol (its name is the definition's), a locked unit."""
        from ..panels.objects_dock import ObjectsDock, PART_RENAME_TIP, TEMPLATE_TIP

        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return "Nothing to rename."
        page = self.doc.page
        if page.objects[index].raw.get("_from_template"):
            return TEMPLATE_TIP
        if page.objects[index].parent is not None and ObjectsDock.in_part(page, index):
            return PART_RENAME_TIP
        return self._tree_rename_blocked(index)

    def _on_rename_requested(self, index: int, name: str) -> None:
        """A name typed in the Objects panel (F2, a slow click, Rename…): one undo step."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        why = self._tree_rename_blocked(index)
        if why:
            self.statusBar().showMessage(why, 6000)
            return
        try:
            ok = self.doc.set_object_name(index, name)
        except ValueError as e:
            self.statusBar().showMessage(str(e), 8000)
            return
        if not ok:
            self.statusBar().showMessage("The name could not be stored on this object.", 6000)
            return
        self._refresh()
        self.statusBar().showMessage(("Renamed to '%s'." % name) if name else "The name was removed.", 4000)

    def _on_lock_toggled(self, root: int, locked: bool) -> None:
        """A padlock of the Objects panel: Edit > Lock / Unlock of that unit (one undo
        step, saved in the page as Edit > Lock is)."""
        if self.doc is None or not 0 <= root < len(self.doc.page.objects):
            return
        if getattr(self.doc, "read_only", False):
            self.statusBar().showMessage("This page is read-only.", 5000)
            return
        if not locked:
            self._locked.discard(root)
        n = self.doc.set_locked([root], locked)
        if locked and not self._break_lock:
            # (a locked unit cannot stay selected)
            sub = set(self.doc.page.subtree(root))
            self._sel = [i for i in self._sel if i not in sub]
        self._refresh()
        if n:
            self.statusBar().showMessage(
                "Locked: it cannot be selected or moved. Click the padlock to unlock it."
                if locked
                else "Unlocked.",
                5000,
            )

    def rename_selection(self) -> None:
        """Edit > Rename…: one object is renamed in the Objects panel, several in Rename
        Objects."""
        if self.doc is None or not self._sel:
            return
        if len(self._sel) > 1:
            self.rename_objects_dialog()
            return
        idx = self._sel[0]
        self.dock_objects.show()
        self.dock_objects.raise_()
        self.objects_dock.start_rename(idx)  # (the editor has the focus)

    def rename_objects_dialog(self) -> None:
        """Edit > Rename Objects…: the selected objects by a pattern, or a find and replace
        in their names (one undo step)."""
        if self.doc is None or not self._sel:
            return
        from ..dialog.rename_dialog import RenameObjectsDialog

        dlg = RenameObjectsDialog(self, self.doc, list(self._sel))
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.renamed:
            self._refresh()
            self.statusBar().showMessage("%s renamed." % (plural(dlg.renamed, "object")), 5000)

    def _objects_panel_settings(self) -> None:
        """Settings > Appearance > Objects panel changed (the Settings window calls it)."""
        self.objects_dock.apply_settings()

    def _reveal_index(self, index: int) -> None:
        """Objects panel double-click with Settings > Appearance > Objects panel >
        Double-click a row > Scrolls to it: the object into view, the zoom kept."""
        if self.doc is None or index is None:
            return
        box = self.doc.selection_extent([index], bounds=True)
        if box is None:
            return
        l, t, r, b = box
        self.view.reveal_rect(QtCore.QRectF(l, t, max(1, r - l), max(1, b - t)))

    def _frame_index(self, index: int) -> None:
        """Browser double-click: centre + zoom the unit."""
        if self.doc is None or index is None:
            return
        box = self.doc.selection_extent([index], bounds=True)
        if box is None:
            return
        l, t, r, b = box
        self.view.frame_rect(QtCore.QRectF(l, t, max(1, r - l), max(1, b - t)))

    def _on_tag_dropped(self, index: int, tag: str) -> None:
        """Drag a tag onto a genie: when exactly one binding
        is tag-typed the tag binds directly (one undo step); otherwise the
        parameter dialog opens to choose."""
        if self.doc is None:
            return
        # the OUTERMOST genie: CtDraw re-composes every placed genie from its
        # element on load, so a nested genie's own bindings are derived from
        # the outer one's -- binding there would be lost
        pg = self.doc.page
        chain = ([index] if pg.objects[index].is_genie else []) + [
            a for a in pg.ancestors(index) if pg.objects[a].is_genie
        ]
        groot = chain[-1] if chain else None
        if groot is None:
            self.statusBar().showMessage("Drop a tag on a genie to bind it.", 4000)
            return
        if self.doc.is_composite_genie(groot):
            from ...editing import COMPOSITE_LOCKED

            self.statusBar().showMessage(COMPOSITE_LOCKED, 8000)
            return
        bindings = self.doc.genie_all_params(groot)
        # a parameter holding a tag of the page's tag scope (or empty, or an
        # Equip.Item style reference) takes the tag
        tag_keys = [
            k
            for k, v in bindings.items()
            if (v.strip() and self.doc.tag_entry(v) is not None)
            or (("." in v or not v.strip()) and not v.startswith("%"))
        ]
        if "Equipment" in bindings and "Equipment" not in tag_keys:
            tag_keys.append("Equipment")
        if len(tag_keys) == 1:
            value = tag
            if tag_keys[0].lower() in ("equipment", "equipmentref", "equip"):
                # an Equipment input takes the equip.dbf NAME (the tag's
                # EQUIP field), not a tag name -- a tag there fails the
                # compile with E2123 "Undefined equipment name"
                entry = self.doc.tag_entry(tag)
                equip = entry.equip if entry is not None else ""
                if not equip:
                    self.statusBar().showMessage(
                        "Tag %s has no equipment to bind to %s" % (tag, tag_keys[0]), 6000
                    )
                    return
                value = equip
            if self.doc.set_genie_binding(groot, tag_keys[0], value):
                self._refresh()
                self.statusBar().showMessage("Bound %s = %s" % (tag_keys[0], value), 5000)
                return
        # ambiguous: open the parameter editor to place the tag
        QtWidgets.QApplication.clipboard().setText(tag)
        self._on_dock_selection([groot])
        self.statusBar().showMessage("Tag %s copied. Paste it into a parameter in the dialog." % tag, 5000)
        self._edit_genie(groot, self.doc.page.objects[groot])
