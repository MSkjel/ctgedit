"""Refresh, status, selection, focus mode and contextual ribbon tabs."""

from __future__ import annotations

import contextlib
from ..main_window_base import (  # noqa: F401
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    TITLE_APP,
    app_settings,
    os,
    set_note_role,
)
from ..main_window_base import _PaintWatch
from ..prefs import keymap as _keymap


class ViewState:
    """MainWindow's refresh, status, selection, focus mode, the Objects tree eyes, selection modes,
    contextual ribbon tabs and the selection toolbar."""

    # ------------------------------------------------------------- rendering
    def _view_changes(self, widget, units=()):
        """The root units ``widget`` (the scene / the Objects tree) must
        rebuild to show the current model: only those whose objects changed
        since it last showed this document (EditableDocument
        .model_changes_since) plus ``units``; None = rebuild it all."""
        shown = getattr(widget, "shown", None)
        if not shown or shown[0] is not self.doc:
            return None
        changes = self.doc.model_changes_since(shown[1], shown[2] if len(shown) > 2 else None)
        if changes is None:
            return None
        page = self.doc.page
        return changes | {page.root_of(i) for i in units if 0 <= i < len(page.objects)}

    #: the scene and the Objects tree keep what they built of a unit that only moved to
    #: another index (:meth:`_view_moves`); off, they build it again (the same items and rows:
    #: tests/test_incremental_render.py)
    KEEP_UNITS = True

    def _view_moves(self, widget, changes, units=()) -> dict:
        """{root now: root then} of the units of ``changes`` (:meth:`_view_changes`) whose
        items / rows ``widget`` keeps under their new index (EditableDocument.
        model_moves_since); not those of ``units``, which it builds again anyway."""
        shown = getattr(widget, "shown", None)
        if not self.KEEP_UNITS or changes is None or not shown or shown[0] is not self.doc:
            return {}
        moves = self.doc.model_moves_since(shown[1], changes, shown[2] if len(shown) > 2 else None)
        if moves and units:
            page = self.doc.page
            for i in units:
                if 0 <= i < len(page.objects):
                    moves.pop(page.root_of(i), None)
        return moves

    def _refresh(self, keep_sel: bool = True, rebuild_tree: bool = True, units=()) -> None:
        """Show the document's current model, rebuilding only what changed."""
        if self.doc is None:
            return
        hold = self.__dict__.get("_refresh_hold")
        if hold is not None:
            hold.append((keep_sel, rebuild_tree, tuple(units)))  # (one refresh at the end)
            return
        # the scene rebuild would delete an open in-place editor: store it first
        # (a text it PLACES is a new object: the tree must show it)
        if self._flush_text_edit():
            rebuild_tree = True
        # the modeless windows follow their object / page (ui/modeless.py)
        self._sync_modeless()
        # (the selection follows its objects through an automatic update: _followed_selection)
        sel = (
            [i for i in self._followed_selection(self._sel) if i < len(self.doc.page.objects)]
            if keep_sel
            else []
        )
        self._page = self.doc.page
        self.view.set_pending(self._genies_updating(self.doc))
        thumb = 0.5 if self._show_thumb else 0.0
        grid = self._grid_spec() if self._show_grid else 0
        if not self._shift_scene():
            changes = self._view_changes(self.scene, units)
            if changes is None or not self.scene.update_units(
                self.doc.page,
                changes,
                thumbnail_opacity=thumb,
                grid_size=grid,
                kept=self._view_moves(self.scene, changes, units),
            ):
                self.scene.set_page(self.doc.page, thumbnail_opacity=thumb, grid_size=grid)
        self.scene.shown = (
            self.doc,
            getattr(self.doc, "derive_serial", 0),
            getattr(self.doc, "touch_serial", 0),
        )
        # Mark Changes Since Save and the Changes panel follow the page (changes_dock)
        self._changes_refresh()
        is_elem = isinstance(self.doc, ElementDocument)
        self.scene.set_element_mode(is_elem, self.doc.hotspot if is_elem else None)
        # the hidden objects follow their objects through a delete, a paste, a reorder
        self._resolve_hidden()
        self.scene.set_hidden(self._hidden_roots)
        self._update_hidden_chip()
        # The Objects tree shows the object HIERARCHY (names/kinds), not geometry, so a
        # geometry-only edit (move/resize) leaves it unchanged -- rebuilding the whole
        # tree on every such edit is the bulk of the per-edit cost. Skip it then; the scene
        # rebuild above already re-syncs item positions to the model.
        # a large page just opened: its Objects tree and Properties form come right after
        # its first paint (the page shows first)
        wait = self._panels_wait()
        dshown = getattr(self.objects_dock, "shown", None)
        if (
            rebuild_tree
            and dshown
            and dshown[0] is self.doc
            and len(dshown) > 2
            and dshown[1] == getattr(self.doc, "derive_serial", 0)
            and self.doc.pending_moves(dshown[2])
        ):
            # only moves since the tree showed this document (a move's undo / redo):
            # the tree shows names and kinds, nothing to rebuild
            rebuild_tree = False
            self.objects_dock.shown = (self.doc, dshown[1], getattr(self.doc, "touch_serial", 0))
        if wait:
            pass  # (_panels_painted builds them)
        elif rebuild_tree:
            tree_changes = self._view_changes(self.objects_dock)
            if tree_changes is None or not self.objects_dock.update_units(
                self.doc.page,
                tree_changes,
                sel,
                hidden=self._hidden_roots,
                kept=self._view_moves(self.objects_dock, tree_changes),
            ):
                self.objects_dock.set_page(self.doc.page, sel, hidden=self._hidden_roots)
            self.objects_dock.shown = (
                self.doc,
                getattr(self.doc, "derive_serial", 0),
                getattr(self.doc, "touch_serial", 0),
            )
        else:
            self.objects_dock.set_selection(sel)
            self.objects_dock.update_positions()  # (a move: the Position column)
        self._sel = sel
        self.view.set_selection(sel)
        if wait:
            self.props.show_pending()
        else:
            self.props.set_target(self.doc, sel)
        self._schedule_hud()
        self._update_title()
        self._update_sel_label()
        self._update_actions()
        # Goto Object / Guidelines read the page shown (debounced)
        self._schedule_window_sync()

    #: a page of more records than this shows its Objects tree and Properties form right
    #: after its first paint; a smaller one shows them at once (no empty panels)
    PANELS_AFTER_PAINT = 2000

    def _panels_wait(self) -> bool:
        """Whether this refresh leaves the Objects tree and the Properties form for after the
        canvas's first paint of a large page just opened (when an event loop runs and the
        window shows: a script or a test without one builds them at once)."""
        doc = self.doc
        pending = self.__dict__.get("_panels_after_paint")
        if pending is not None:
            if pending is doc:
                return True
            self._panels_after_paint = None  # (another page shows now)
        dshown = getattr(self.objects_dock, "shown", None)
        if (dshown and dshown[0] is doc) or self.__dict__.get("_panels_painted_doc") is doc:
            return False
        if (
            len(doc.page.objects) <= self.PANELS_AFTER_PAINT
            or not self.isVisible()
            or not self._defer_post_open()
        ):
            return False
        self._panels_after_paint = doc
        _PaintWatch(self.view.viewport(), self._panels_painted)
        QtCore.QTimer.singleShot(self.POST_OPEN_FALLBACK_MS, self, self._panels_painted)
        return True

    def _panels_painted(self) -> None:
        """The page painted: its Objects tree and Properties form now."""
        doc = self.__dict__.pop("_panels_after_paint", None)
        if doc is None or doc is not self.doc:
            return
        self._panels_painted_doc = doc
        self._refresh()

    def _shift_scene(self) -> bool:
        """When everything since the scene last drew this document is pure moves (a move, its
        undo / redo, a nudge): move the items instead of building the units again. True
        when done (the scene shows the current model)."""
        shown = getattr(self.scene, "shown", None)
        if (
            not shown
            or shown[0] is not self.doc
            or len(shown) < 3
            or shown[1] != getattr(self.doc, "derive_serial", 0)
        ):
            return False
        if getattr(self.scene, "_page", None) is not self.doc.page:
            return False
        ap = self.doc.page.appearance
        # (a page size / colour change touches no record: the normal path rebuilds then)
        from ...render.scene import colorref_to_qcolor

        if (ap.width or 1024, ap.height or 768) != (
            self.scene._page_w,
            self.scene._page_h,
        ) or colorref_to_qcolor(ap.background_colour, QtGui.QColor("#d4d0c8")) != self.scene._page_bg:
            return False
        moves = self.doc.pending_moves(shown[2])
        if not moves:
            return False
        return self.scene.shift_records(moves)

    def _after_edit(self) -> None:
        self._refresh()

    def _update_breadcrumb(self) -> None:
        self._other_install = False
        lbl = getattr(self, "lbl_project", None)
        if lbl is None:
            return
        if self.doc is None:
            lbl.setText("")
            return
        proj_dir = os.path.dirname(os.path.abspath(self.doc.path))
        user_dir = os.path.dirname(proj_dir)
        install = os.path.basename(os.path.dirname(user_dir)) or user_dir
        text = "%s  ·  %s" % (install.replace("AVEVA Plant SCADA ", ""), os.path.basename(proj_dir))
        in_project = True
        if not isinstance(self.doc, ElementDocument):
            with contextlib.suppress(Exception):
                in_project = bool(self.doc.page_record_available())
        if not in_project:
            # (a page saved outside a project: its folder's parents are not an install)
            lbl.setText("⚠ Not in a project  ·  " + os.path.basename(proj_dir))
            set_note_role(lbl, "warn", "padding:0 6px;")
            lbl.setToolTip(
                "%s is not a project folder: the page is not registered, and Plant "
                "SCADA does not compile it. Save As into a project." % proj_dir
            )
            return
        live = None
        with contextlib.suppress(Exception):
            from ...io.citect_config import live_user_dir

            live = live_user_dir()
        if live and os.path.normcase(os.path.abspath(user_dir)) != os.path.normcase(os.path.abspath(live)):
            self._other_install = True
            lbl.setText("⚠ " + text + "  (not the active install)")
            set_note_role(lbl, "warn", "padding:0 6px;")
            lbl.setToolTip(
                "The Graphics Builder and the runtime use %s. They do not see edits to this copy." % live
            )
        else:
            lbl.setText(text)
            set_note_role(lbl, "text_dim", "padding:0 6px;")
            lbl.setToolTip(self.doc.path)

    def _update_title(self) -> None:
        self._update_breadcrumb()
        self._update_an_count()
        bar = getattr(self, "_auto_bar", None)
        if bar is not None and bar.isVisible():
            bar.show_for(self.doc)  # (gone once the page is saved)
        self._refresh_tabs()
        self.setWindowTitle(self.window_title())

    def window_title(self) -> str:
        """'<page>[ •] - <project> - ctgedit' ('Mixer • - Example - ctgedit' with unsaved
        changes), '<library>.<element> - <project> - ctgedit' for a library element; the
        project says '(other install)' when it is not in the install Plant SCADA uses."""
        doc = self.doc
        if doc is None:
            name = self._page.name if self._page is not None else ""
            return "%s - %s" % (name, TITLE_APP) if name else TITLE_APP
        if isinstance(doc, ElementDocument):
            name = doc.display_name
        else:
            name = os.path.splitext(os.path.basename(doc.path))[0]
        version = getattr(getattr(doc, "ui", None), "version_view", None)
        if version is not None:
            name += " (version of %s)" % version.when  # (File > Page History)
        if getattr(doc, "read_only", False):
            name += " [read-only]"
        if doc.dirty:
            name += " •"
        project = os.path.basename(os.path.dirname(os.path.abspath(doc.path)))
        if getattr(self, "_other_install", False):
            project += " (other install)"
        return "%s - %s - %s" % (name, project, TITLE_APP)

    #: help 1172254: 'keep the number of objects (and hence ANs) to less than 3000' (the
    #: default of Settings > Appearance > Status bar: ui/prefs/window_options.AN_WARN)
    AN_ADVICE_LIMIT = 3000

    def _update_an_count(self) -> None:
        """The status bar's animation-point count; amber with advice above about 3000 (the
        Graphics Builder help's rule of thumb; Settings > Appearance > Status bar 'Warn about
        pages with more animation points than:')."""
        lbl = getattr(self, "lbl_ans", None)
        if lbl is None:
            return
        doc = self.doc
        if doc is None or not hasattr(doc, "animation_point_count"):
            lbl.setText("")
            return
        key = (id(doc), getattr(doc, "derive_serial", 0))
        if getattr(self, "_an_count_key", None) != key:
            try:
                self._an_count = doc.animation_point_count()
            except Exception:  # noqa: BLE001 -- a hint only
                self._an_count = 0
            self._an_count_key = key
        n = self._an_count
        from ..prefs import window_options

        limit = window_options.AN_WARN
        own = (
            ""
            if limit == self.AN_ADVICE_LIMIT
            else " You chose to be warned above %d (Settings > Appearance)." % limit
        )
        if n > limit:
            lbl.setText("⚠ %d animation points" % n)
            set_note_role(lbl, "warn", "padding:0 6px;")
            lbl.setToolTip(
                "This page has %d animation points (ANs). The Graphics Builder "
                "help suggests keeping a page under %d objects.%s Split it into "
                "several pages or use fewer objects." % (n, self.AN_ADVICE_LIMIT, own)
            )
        else:
            lbl.setText("%d animation points" % n)
            set_note_role(lbl, "text_dim", "padding:0 6px;")
            lbl.setToolTip(
                "The page's animation points (ANs): every object has one. The "
                "Graphics Builder help suggests fewer than %d a page.%s" % (self.AN_ADVICE_LIMIT, own)
            )

    def _update_sel_label(self) -> None:
        self._update_an_count()
        if not self._sel:
            self.lbl_sel.setText("No selection")
        elif len(self._sel) == 1 and self.doc and self._sel[0] < len(self.doc.page.objects):
            o = self.doc.page.objects[self._sel[0]]
            g = o.geometry
            an = ("  AN %d" % o.an) if o.an > 0 else ""
            from ..names import kind_name

            self.lbl_sel.setText("%s%s  %d×%d @ %d,%d" % (kind_name(o), an, g.extent_x, g.extent_y, g.x, g.y))
        else:
            self.lbl_sel.setText("%d objects" % len(self._sel))

    def _update_start_page(self) -> None:
        sp = getattr(self, "start_page", None)
        if sp is None:
            return
        # (a restored session's tab whose page opens on the next turn: no start page)
        # (Settings 'On start: Open nothing': no start page until a page has been open)
        want = (
            self.doc is None
            and self._page is None
            and not self._docs
            and not getattr(self, "_start_page_off", False)
        )
        # isHidden, not isVisible: a page opened before the window is first
        # shown must still hide the start page
        if want and sp.isHidden():
            sp.refresh()
            sp.show()
            sp.raise_()
        elif not want and not sp.isHidden():
            sp.hide()
        nb = getattr(self, "navbar", None)
        if nb is not None:
            nb.setVisible(not want)
        if hasattr(self, "_sync_rulers"):
            self._sync_rulers()

    #: when each command with a plain rule is enabled. 'doc' = a page or element is open,
    #: 'sel' = something is selected, 'multi' = two or more units (a genie + its members =
    #: one), 'three' = three or more units, 'page' = a page (not a library element) is
    #: open. Commands with a rule of their own are set by
    #: name in _update_actions (CUSTOM_ENABLE); ALWAYS_ENABLED need nothing open.
    #: tests/test_action_table.py fails on an action in none of the three.
    ENABLE_WHEN = {
        "doc": (
            "act_save",
            "act_save_as",
            "act_close_doc",
            "act_paste",
            "act_export_page",
            "act_show_all_roots",
            "act_mark_changes",
            "act_page_props",
            "act_print",
            "act_import",
            "act_goto",
            "act_sel_all",
            "act_paste_special",
            "act_swap_colors",
            "act_adjust_colors",
            "act_import_flash",
            "act_paste_flash",
            "act_paste_symbol",
            "act_paste_genie",
            "act_paste_super",
            "act_save_view",
            "act_manage_views",
            "act_clear_guides",
            # (they act on a page only)
            "act_paste_linked",
            "act_break_lock",
            "act_refresh",
            # the undo history and the changes (history_dock, changes_dock)
            "act_page_history",
            "act_next_change",
            "act_prev_change",
            "act_select_changed",
            # Edit > Select
            "act_invert_sel",
            "act_select_tag",
            "act_select_previous",
            "act_unlock_all",
            "act_lock_toggle",
            # View > Underlay (ui/editor/underlay.py)
            "act_underlay_page",
            "act_underlay_image",
            "act_underlay_settings",
        ),
        # a page in a project folder (pages.DBF): Compile and Run act on its project
        "project": ("act_compile", "act_run"),
        "sel": (
            "act_cut",
            "act_copy",
            "act_dup",
            "act_del",
            "act_front",
            "act_back",
            "act_fwd",
            "act_bwd",
            "act_rot_cw",
            "act_rot_ccw",
            "act_flip_h",
            "act_flip_v",
            "act_adjust",
            "act_to_bitmap",
            "act_copy_to_lib",
            "act_lock",
            "act_unlock",
            "act_zoom_sel",
            "act_obj_props",
            "act_isolate",
            "act_copy_appearance",
            "act_save_style",
            "act_style_default",
            "act_rename",
            "act_rename_objects",
            # Edit > Select, Edit > Hide or Show Selection
            "act_deselect",
            "act_select_same_element",
            "act_select_same_kind",
            "act_select_same_fill",
            "act_select_same_line_colour",
            "act_select_same_line_width",
            "act_select_same_font",
            "act_select_same_size",
            "act_select_same_anim",
            "act_hide_toggle",
        ),
        "multi": ("act_same_size", "act_group", "act_space_equally", "act_arrange_grid"),
        "three": ("act_dist_h", "act_dist_v"),
        "page": (
            "act_check_page",
            "act_check_workspace",
            "act_check_display",
            "act_simulate",
            "act_dup_pictures",
        ),
        # the document tabs' commands (on the tab the tab menu was opened on, or the current)
        "tab": (
            "act_copy_page_path",
            "act_copy_page_name",
            "act_reveal_page",
            "act_show_in_browser",
            "act_pin_tab",
            "act_move_to_window",
        ),
    }
    #: set by their own rule in _update_actions
    CUSTOM_ENABLE = (
        "act_revert",
        "act_undo",
        "act_redo",
        "act_paste_composite",
        "act_paste_appearance",
        "act_ungroup",
        "act_cut_link",
        "act_cut_template_link",
        "act_edit_template",
        "act_update_page_template",
        "act_genie_params",
        "act_attach_super",
        "act_genie_form",
        "act_replace_genie",
        "act_hotspot",
        "act_used_by",
        "act_compose_nested",
        "act_update_users",
        "act_prev_view",
        "act_next_view",
        "act_bitmap_editor",
        "act_font_dialog",
        "act_edit_text",
        "act_replace_picture",
        "act_export_picture",
        "act_resample_picture",
        "act_close_others",
        "act_close_right",
        "act_close_saved",
        "act_close_all",
        "act_reopen_closed",
        "act_next_page",
        "act_prev_page",
        "act_new_from_last",
        "act_repeat",
        # View > Underlay: the page's earlier versions, the page's underlay
        "act_underlay_version",
        "act_underlay_show",
        "act_underlay_above",
        "act_underlay_move",
        "act_underlay_remove",
        # Align with one object too, to the page
        "act_align_dialog",
    )
    #: enabled whatever is open (window, view, settings, help, tools); act_rotate /
    #: act_mirror are aliases of act_rot_cw / act_flip_h
    ALWAYS_ENABLED = (
        "act_new",
        "act_open",
        "act_quick_open",
        "act_save_all",
        "act_options",
        "act_page_defaults",
        "act_manage_styles",
        "act_print_setup",
        "act_restore_backup",
        "act_open_logs",
        "act_copy_diag",
        "act_help",
        "act_reset_ui",
        "act_focus_mode",
        "act_status_bar",
        "act_show_tools",
        "act_selection_hud",
        "act_crosshair",
        "act_collapse_ribbon",
        "act_grid",
        "act_pixel_grid",
        "act_grid_setup",
        "act_guidelines",
        "act_show_guides",
        "act_snap",
        "act_snap_guides",
        "act_smart_guides",
        "act_slow_keys",
        "act_slow_mouse",
        "act_pane_labels",
        "act_aliased_text",
        "act_thumb",
        "act_cascade",
        "act_tile_h",
        "act_tile_v",
        "act_fav_colors",
        "act_transparent_paste",
        "act_var_tags",
        "act_zoom_100",
        "act_zoom_all",
        "act_zoom_fit",
        "act_zoom_in",
        "act_zoom_out",
        "act_zoom_window",
        "act_activex_tool",
        "act_messages",
        # Tools > Update Pages: any root project, also with nothing open
        "act_update_pages",
        # Find / Replace (All pages of the project) and Pack Libraries: a whole
        # project, also with nothing open
        "act_find",
        "act_replace",
        "act_pack_libs",
        # Tools > Close Graphics Builder: another program, also with nothing open
        "act_close_builder",
        "act_rotate",
        "act_mirror",
        "act_rulers",
        "act_navigator",
        # the File / Window / Help commands, the palette, the panel keys
        "act_exit",
        "act_new_window",
        "act_getting_started",
        "act_shortcuts_window",
        "act_differences",
        "act_troubleshooting",
        "act_cicode_ref",
        "act_check_updates",
        "act_about",
        "act_palette",
        "act_palette_here",
        "act_go_page",
        "act_go_objects",
        "act_go_library",
        "act_go_tags",
        "act_go_props",
        "act_go_history",
        "act_next_panel",
        "act_prev_panel",
        # the colours of new objects (Tools > Colours, the swatch pair)
        "act_new_fill_colour",
        "act_new_line_colour",
        "act_new_text_colour",
        "act_new_colours_swap",
        "act_new_no_fill",
        "act_new_colours_auto",
    )

    def _label_run(self, element: bool) -> None:
        """File > Run says what it does here: with a page it saves, compiles and runs the
        project in Plant SCADA; with a library element it only opens Runtime Manager."""
        act = getattr(self, "act_run", None)
        if act is None:
            return
        if element:
            text = "Open Runtime Manager"
            tip = "Open Plant SCADA's Runtime Manager. A library element is not run on its own"
        else:
            text = "Run in Plant SCADA"
            tip = (
                "Save the page, compile the project and start Plant SCADA's runtime with it (or "
                "show the page again in a runtime already running). Simulate Page needs none of that"
            )
        if act.text() != text:
            act.setText(text)
        # (the status bar's tip for Compile and Run with no page open starts from this one)
        act.setProperty("_ctg_own_tip", tip)
        act.setToolTip(tip)
        act.setStatusTip(tip)

    def _update_actions(self) -> None:
        self._update_start_page()
        has_doc = self.doc is not None
        has_sel = bool(self._sel) and has_doc
        # align/distribute act on UNITS (a genie+members = one), so count roots
        n_units = len({self.doc.root_of(i) for i in self._sel}) if has_doc else 0
        multi = n_units > 1
        is_page = has_doc and not isinstance(self.doc, ElementDocument)
        in_project = False
        if is_page:
            with contextlib.suppress(Exception):
                in_project = bool(self.doc.page_record_available())
        elif not has_doc:
            # (no page: Compile and Run act on the project of the most recent page)
            in_project = self._compile_target() is not None
        docs = getattr(self, "_docs", [])
        state = {
            "doc": has_doc,
            "sel": has_sel,
            "multi": multi,
            "three": n_units > 2,
            "page": is_page,
            "project": in_project,
            "tab": bool(docs),
        }
        for rule, names in self.ENABLE_WHEN.items():
            for nm in names:
                getattr(self, nm).setEnabled(state[rule])
        self._label_run(has_doc and not is_page)
        find = self.__dict__.get("_find_dlg")
        if find is not None:
            with contextlib.suppress(RuntimeError):
                find.page_changed()  # (This page / All open pages need a page)
        # the drawing tools and Insert need a page to draw on
        select = getattr(self, "tool_actions", {}).get("select")
        for a in (
            list(getattr(self, "tool_actions", {}).values())
            + list(getattr(self, "v34_actions", ()))
            + list(getattr(self, "insert_actions", ()))
        ):
            a.setEnabled(has_doc or a is select)
        # (Settings > Editing 'Align to: The page' aligns one object too)
        align = multi or (has_sel and getattr(self, "_align_to", "") == "page")
        for a in self.align_actions:
            a.setEnabled(align)
        self.act_align_dialog.setEnabled(align)
        self.act_revert.setEnabled(
            bool(has_doc and not getattr(self.doc, "is_new", False) and os.path.exists(self.doc.path))
        )
        # the document tabs' commands: Close Others / to the Right / Saved / All need an
        # unpinned tab to close, Next / Previous Page two tabs, Reopen a closed page; Pin
        # Tab says Unpin Tab on a pinned one
        t = self._target_entry() if docs else None
        k = next((i for i, e in enumerate(docs) if e is t), -1)
        free = [e for e in docs if not e.get("pinned")]
        self.act_close_all.setEnabled(bool(free))
        self.act_close_others.setEnabled(any(e is not t for e in free))
        self.act_close_right.setEnabled(k >= 0 and any(not e.get("pinned") for e in docs[k + 1 :]))
        self.act_close_saved.setEnabled(any(not e["doc"].dirty for e in free))
        self.act_next_page.setEnabled(len(docs) > 1)
        self.act_prev_page.setEnabled(len(docs) > 1)
        self.act_reopen_closed.setEnabled(bool(self.__dict__.get("_closed_stack")))
        last = self.last_template()
        self.act_new_from_last.setEnabled(last is not None)
        text = ("New Page from '%s.%s'" % last) if last else "New Page from the Last Template"
        if self.act_new_from_last.text() != text:
            self.act_new_from_last.setText(text)
        self.act_pin_tab.setText("Unpin Tab" if t is not None and t.get("pinned") else "Pin Tab")
        for n, a in enumerate(getattr(self, "goto_tab_actions", ())):
            a.setEnabled(n < len(docs))
        self.act_undo.setEnabled(bool(has_doc and self.doc.can_undo()))
        self.act_redo.setEnabled(bool(has_doc and self.doc.can_redo()))
        # Previous / Next View: the page shown has views to go back or forward to
        self.act_prev_view.setEnabled(bool(has_doc and self.view.can_go_back()))
        self.act_next_view.setEnabled(bool(has_doc and self.view.can_go_forward()))
        self._sync_saved_views_menu()
        self.act_paste_appearance.setEnabled(bool(has_sel and getattr(self, "_appearance", None)))
        # View > Underlay (ui/editor/underlay.py): the page's underlay comes back the first time it
        # shows; the version command needs earlier versions, the others an underlay (Move
        # with the Mouse one that shows: _underlay_update_actions)
        self._underlay_update_actions()
        ul = self.underlay() if has_doc else None
        self.act_underlay_version.setEnabled(bool(has_doc and self._underlay_versions()))
        self.act_underlay_show.setEnabled(ul is not None)
        self.act_underlay_above.setEnabled(ul is not None)
        self.act_underlay_remove.setEnabled(ul is not None)
        self.act_underlay_move.setEnabled(bool(ul is not None and ul.shown and ul.ready()))
        # name the step (Undo Move / Redo Colour) and note a new step's objects (history_dock)
        self._history_update_actions()
        hd = getattr(self, "history_dock", None)
        if hd is not None and not self.dock_history.isHidden():
            hd.show_document(self.doc if has_doc else None)
        # everything else that needs a page / a selection / a kind (a command
        # that cannot apply is greyed out instead of silently doing nothing)
        traits = self._classify_selection() if has_sel else set()
        one = has_sel and n_units == 1
        # a composite genie goes only into a page, template or super genie
        # (help 1172148_1); it DOES turn -- CtDraw offers Rotate / Mirror for it
        from ...editing import COMPOSITE_NOT_HERE

        for a in (self.act_paste_composite,):
            # remember the action's own tips -- again whenever they are not
            # ours, so a tip other code sets later (shortcut text) is kept
            tips = list(a.property("_ctg_tips") or ["", ""])
            for k, cur in enumerate((a.statusTip() or "", a.toolTip() or "")):
                if cur != COMPOSITE_NOT_HERE:
                    tips[k] = cur
            a.setProperty("_ctg_tips", tips)
        allowed = bool(has_doc and self.doc.composites_allowed)
        self.act_paste_composite.setEnabled(allowed)
        tips = self.act_paste_composite.property("_ctg_tips")
        refused = has_doc and not allowed
        self.act_paste_composite.setStatusTip(COMPOSITE_NOT_HERE if refused else tips[0])
        self.act_paste_composite.setToolTip(COMPOSITE_NOT_HERE if refused else tips[1])
        first = self._sel[0] if has_sel else None
        # Ungroup dissolves every selected group / genie at once (a shape
        # owning only touch / keyboard records is not a group)
        any_container = bool(
            has_sel
            and any(
                0 <= i < len(self.doc.page.objects) and self._drawable_members(self.doc.root_of(i))
                for i in self._sel
            )
        )
        self.act_ungroup.setEnabled(any_container)
        has_tpl = is_page and self._page_has_template()
        # (a selection of several units: every placed symbol's link is cut, as one step)
        self.act_cut_link.setEnabled(has_sel or has_tpl)
        self.act_cut_template_link.setEnabled(has_tpl)
        self.act_edit_template.setEnabled(has_tpl)
        self.act_update_page_template.setEnabled(has_tpl)
        self.act_genie_params.setEnabled("GENIE" in traits)
        # (on a page, and in a library genie, template or super genie: genieswap)
        self.act_replace_genie.setEnabled(has_doc and bool(getattr(self.doc, "_swap_window", lambda: "")()))
        # CtDraw: in the genie's own window; ctgedit also for a placed genie
        self.act_attach_super.setEnabled(
            bool(getattr(self.doc, "can_attach_super_genies", False))
            if isinstance(self.doc, ElementDocument)
            else ("GENIE" in traits and one)
        )
        # the genie of its element window, or the one selected placed genie
        self.act_genie_form.setEnabled(
            getattr(self.doc, "kind", "") == "genie"
            if isinstance(self.doc, ElementDocument)
            else ("GENIE" in traits and one)
        )
        # the hot spot of a symbol / genie element window; Used By of the
        # element being edited, or of the one selected placed symbol / genie
        self.act_hotspot.setEnabled(
            isinstance(self.doc, ElementDocument) and bool(getattr(self.doc, "can_set_hotspot", False))
        )
        self.act_used_by.setEnabled(self._used_by_target() is not None)
        # (an element of a library that is saved and writable: its nested genies composed
        # again on demand, whatever the import table's stamps say)
        self.act_compose_nested.setEnabled(
            isinstance(self.doc, ElementDocument)
            and not self.doc.is_new
            and not getattr(self.doc, "read_only", False)
        )
        self.act_update_users.setEnabled(self.act_used_by.isEnabled())
        self.act_bitmap_editor.setEnabled(
            bool(
                one
                and first is not None
                and first < len(self.doc.page.objects)
                and self._bitmap_target(quiet=True)[0] is not None
            )
        )
        pic = (
            self._picture_target(quiet=True)
            if one and first is not None and first < len(self.doc.page.objects)
            else (None, None)
        )
        self.act_replace_picture.setEnabled(pic[0] is not None)
        self.act_export_picture.setEnabled(pic[0] is not None)
        self.act_resample_picture.setEnabled(pic[1] == "bitmap")
        text_ok = "TEXT" in traits
        for a in [self.act_font_dialog] + self.text_flag_actions + self.justify_actions + self.effect_actions:
            a.setEnabled(text_ok)
        self.act_edit_text.setEnabled(bool(text_ok and one))
        # Edit > Repeat: the last repeatable command, while it can run (ui/prefs/keymap)
        self.act_repeat.setEnabled(_keymap.repeat_ok(self))
        # the open Adjust Position and Size window follows the selection, and
        # so do the other selection windows (Align, Font, Swap / Adjust Colours)
        self._sync_adjust_dialog()
        self._sync_selection_windows()
        self._sync_goto_dialog()
        from ..panels import status_items

        status_items.refresh(self)  # (the status bar's fields)

    # ------------------------------------------------------------- selection
    # The selection is ``_sel``: record indices of the page shown. Each time it is set, a note
    # of the records it holds is taken (_note_selection); a refresh finds those records again
    # (_followed_selection) when the records changed without the selection being set again:
    # an automatic update of the page (a library save's update of its tab, the library watch,
    # a genie update that landed, the chain's end) that inserted or removed records before
    # them, as the hidden set and the object windows follow theirs. A tab not shown keeps the
    # note of its selection (tabs._stash_current) and follows it when shown again.

    @property
    def _sel(self) -> list:
        return self.__dict__.get("_sel_indices", [])

    @_sel.setter
    def _sel(self, value) -> None:
        self.__dict__["_sel_indices"] = value
        self._note_selection(value)

    def _note_selection(self, indices) -> None:
        """Note the records ``indices`` hold on the page shown now (what
        :meth:`_followed_selection` follows): (the document, the indices, every record, each
        selected object's AN)."""
        doc = self.__dict__.get("doc")
        note = None
        if doc is not None and indices:
            try:
                recs = self._records_of(doc)
                objs = doc.page.objects
                n = min(len(recs), len(objs))
                note = (
                    doc,
                    tuple(indices),
                    tuple(recs),
                    tuple(objs[i].an if 0 <= i < n else 0 for i in indices),
                )
            except AttributeError, TypeError:  # swallow-ok: a tab not opened yet
                note = None
        self.__dict__["_sel_note"] = note

    def _selection_note(self):
        """The note of the selection (:meth:`_note_selection`), for a tab that is left."""
        return self.__dict__.get("_sel_note")

    def _followed_selection(self, sel, note=None) -> list:
        """Map a selection to where its objects are now."""
        doc = self.doc
        if note is None:
            note = self.__dict__.get("_sel_note")
        sel = list(sel or ())
        if doc is None or not sel or note is None or note[0] is not doc or tuple(sel) != note[1]:
            return sel
        old, ans = note[2], note[3]
        recs = self._records_of(doc)
        n = len(recs)
        if all(0 <= i < n and i < len(old) and recs[i] is old[i] for i in sel):
            return sel  # (nothing it holds moved)
        objs = doc.page.objects
        pos = {id(po): k for k, po in enumerate(recs)}
        by_an = olds = None
        out: list = []
        for i, an in zip(sel, ans):
            po = old[i] if 0 <= i < len(old) else None
            j = pos.get(id(po)) if po is not None else None
            if j is None and an > 0:
                if by_an is None:
                    by_an = {}
                    for k, o in enumerate(objs):
                        if o.an > 0 and not o.raw.get("_non_drawable"):
                            by_an.setdefault(o.an, k)
                j = by_an.get(an)
            if j is None and 0 <= i < n:
                if olds is None:
                    olds = {id(x) for x in old}
                if id(recs[i]) not in olds:
                    j = i
            if j is not None and j < len(objs) and j not in out:
                out.append(j)
        return out

    def _on_canvas_selection(self, indices: list) -> None:
        self._sel = list(indices)
        self.objects_dock.set_selection(self._sel)
        self.props.set_target(self.doc, self._sel)
        self._update_sel_label()
        self._update_actions()
        self._queue_context_update("canvas")

    # ------------------------------------------------ focus mode / reset UI
    def _toggle_focus_mode(self) -> None:
        """View > Focus Mode (default off): the menu bar drops
        to height 0 (not hide(), so shortcut routing survives); a tap of Alt
        reveals it. The QMenuBar and its actions stay alive (parity untouched)."""
        on = self.act_focus_mode.isChecked()
        if on:
            self.menuBar().setFixedHeight(0)
        else:
            self.menuBar().setMaximumHeight(16777215)
            self.menuBar().setMinimumHeight(0)

    def eventFilter(self, obj, ev):
        # a floating window closed from its title bar unticks its View item;
        # hidden by the system with a minimised editor it comes back with it
        closed = (
            ev.type() == QtCore.QEvent.Hide
            and not ev.spontaneous()
            and not self.isHidden()
            and not self.isMinimized()
        )
        if obj is getattr(self, "_zoom_win", None) and closed:
            self.act_zoom_window.setChecked(False)
        elif obj is getattr(self, "_toolbox", None) and closed:
            self.act_show_tools.setChecked(False)
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e) -> None:
        # Alt toggles the menu bar back while Focus Mode is on
        if e.key() == QtCore.Qt.Key_Alt and self.act_focus_mode.isChecked():
            mb = self.menuBar()
            if mb.height() == 0:
                mb.setMaximumHeight(16777215)
                mb.setMinimumHeight(0)
            else:
                mb.setFixedHeight(0)
        super().keyPressEvent(e)

    def _reset_ui_layout(self) -> None:
        """View > Reset UI Layout: back to the default surfaces."""
        self.act_focus_mode.setChecked(False)
        self._toggle_focus_mode()
        if getattr(self.ribbon, "_collapsed", False):
            self.ribbon.toggle_collapsed()
        for dock in (self.dock_objects, self.dock_library, self.dock_tags, self.dock_props):
            dock.show()
        self.dock_sa_checks.hide()  # opens by itself when a save finds something
        self.dock_objects.raise_()
        self._show_all_roots()
        self.act_status_bar.setChecked(True)
        self._toggle_status_bar()
        if self.act_show_tools.isChecked():
            self.act_show_tools.setChecked(False)
            self._toggle_toolbox()
        self.statusBar().showMessage("UI layout reset", 4000)

    # -------------------------------------- what changed since the save
    def changes_since_save(self):
        """(changed or new record indices, how many fewer records than the saved page has),
        by the records' payloads against the saved file (ui/panels/changes_dock)."""
        return self.changed_records()

    def _mark_changes(self) -> None:
        """View > Mark Changes Since Save: new, changed and removed objects marked on the
        canvas, kept up to date with every edit while ticked (ui/panels/changes_dock)."""
        self._changes_mark()

    # -------------------------------------- hidden in the editor / isolate / frame
    # The objects hidden in the editor (the Objects panel's eyes, Isolate Selection, Hide or
    # Show Selection) are ``_hidden_roots``: record indices of the page shown, any object (a
    # genie member hides itself only). They follow their objects: each hide takes a note of
    # the records it hides (_hidden_snap), and a refresh after a delete, a paste or a reorder
    # finds those records again (_resolve_hidden), else the object of the same AN. Saved per
    # page by AN in 'browser/hidden_an/<path>' (an index for an object without an AN, as
    # -1 - index); a page shown in a new tab hides them again (_saved_hidden). Editor-only:
    # nothing of it is ever saved into the page.

    def _hidden_key(self, doc=None):
        doc = doc if doc is not None else self.doc
        path = getattr(doc, "path", "") or ""
        if not path:
            return None
        key = "browser/hidden_an/%s" % os.path.abspath(path)
        element = getattr(doc, "element_name", "") if isinstance(doc, ElementDocument) else ""
        return key + ("|" + element if element else "")

    @staticmethod
    def _records_of(doc) -> list:
        """The document's records (the identities a hidden object is followed by)."""
        parsed = getattr(doc, "parsed", None)
        pg = getattr(parsed, "page", None)
        recs = getattr(pg, "objects", None)
        if recs is not None and len(recs) == len(doc.page.objects):
            return recs
        return doc.page.objects

    def _snap_hidden(self) -> None:
        """Note the records the hidden set holds now (what _resolve_hidden follows)."""
        doc = self.doc
        if doc is None or not self._hidden_roots:
            self._hidden_snap = None
            return
        recs = self._records_of(doc)
        objs = doc.page.objects
        n = min(len(recs), len(objs))
        self._hidden_roots = {i for i in self._hidden_roots if 0 <= i < n}
        self._hidden_snap = (
            doc,
            frozenset(self._hidden_roots),
            getattr(doc, "derive_serial", 0),
            len(recs),
            [(i, recs[i], objs[i].an) for i in sorted(self._hidden_roots)],
        )

    def _resolve_hidden(self) -> None:
        """The hidden set as record indices of the current model: each hidden record where it
        is now (a delete or an insert before it moved it), else the object of its AN; gone
        ones are dropped. A set given from elsewhere (a tab switch) is taken as it is."""
        doc = self.doc
        cur = set(self._hidden_roots)
        if doc is None or not cur:
            self._hidden_snap = None
            return
        snap = self.__dict__.get("_hidden_snap")
        recs = self._records_of(doc)
        if snap is None or snap[0] is not doc or snap[1] != frozenset(cur):
            self._snap_hidden()
            return
        if snap[2] == getattr(doc, "derive_serial", 0) and snap[3] == len(recs):
            return  # nothing derived since the note
        pos = {id(po): i for i, po in enumerate(recs)}
        objs = doc.page.objects
        by_an = None
        new = set()
        for _i, po, an in snap[4]:
            j = pos.get(id(po))
            if j is None and an > 0:
                if by_an is None:
                    by_an = {}
                    for k, o in enumerate(objs):
                        if o.an > 0 and not o.raw.get("_non_drawable"):
                            by_an.setdefault(o.an, k)
                j = by_an.get(an)
            if j is not None and j < len(objs):
                new.add(j)
        self._hidden_roots = new
        self._snap_hidden()
        if new != cur:
            self._save_hidden()

    def _save_hidden(self) -> None:
        """'browser/hidden_an/<path>': the hidden objects by AN (-1 - index without one)."""
        doc = self.doc
        key = self._hidden_key(doc) if doc is not None else None
        if key is None:
            return
        objs = doc.page.objects
        vals = sorted(
            {objs[i].an if objs[i].an > 0 else -1 - i for i in self._hidden_roots if 0 <= i < len(objs)}
        )
        try:
            st = app_settings()
            if vals:
                st.setValue(key, vals)
            else:
                st.remove(key)
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("view._save_hidden")

    def _saved_hidden(self, doc) -> set:
        """The objects of ``doc`` hidden when it was last shown (its saved ANs that are
        still on the page)."""
        key = self._hidden_key(doc)
        if key is None or doc.page is None:
            return set()
        try:
            vals = app_settings().value(key, [], type=list) or []
        except Exception:  # noqa: BLE001 -- swallow-ok: a garbled list hides nothing
            return set()
        objs = doc.page.objects
        by_an = {}
        for k, o in enumerate(objs):
            if o.an > 0 and not o.raw.get("_non_drawable"):
                by_an.setdefault(o.an, k)
        out = set()
        for v in vals:
            try:
                v = int(v)
            except TypeError, ValueError:
                continue
            j = by_an.get(v) if v > 0 else (-1 - v)
            if j is not None and 0 <= j < len(objs):
                out.add(j)
        return out

    def _set_hidden(self, indices, save: bool = True) -> None:
        """Hide exactly ``indices`` in the editor: the canvas, the Objects panel's eyes and
        the hidden chip follow; remembered for the page."""
        doc = self.doc
        n = len(doc.page.objects) if doc is not None else 0
        self._hidden_roots = {i for i in indices if 0 <= i < n}
        self._snap_hidden()
        self.scene.set_hidden(self._hidden_roots)
        self.objects_dock.set_hidden(self._hidden_roots)
        self._update_hidden_chip()
        if save:
            self._save_hidden()

    def isolate_selection(self) -> None:
        """View > Isolate Selection: every root but the selected units' hides (the Objects
        tree's eyes), so a unit is edited alone; the same command again, Show All or the
        hidden chip brings the rest back."""
        if self.doc is None or not self._sel:
            return
        page = self.doc.page
        keep = {self.doc.root_of(i) for i in self._sel if 0 <= i < len(page.objects)}
        hide = {r for r in page.roots() if r not in keep and not page.objects[r].raw.get("_non_drawable")}
        if not hide or hide == self._hidden_roots:
            self._show_all_roots()
            return
        self._set_hidden(hide)
        self.statusBar().showMessage(
            "%d object%s hidden: the selection is alone on the page (Isolate Selection again, "
            "or Show All, brings them back)." % (len(hide), "" if len(hide) == 1 else "s"),
            6000,
        )

    def _solo_row(self, index: int) -> None:
        """Alt+click on an eye: only that object shows (every other top-level object, and the
        objects beside it in the groups it is in, hide); again: everything shows."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        page = self.doc.page
        chain = [index] + page.ancestors(index)
        on_path = set(chain)
        hide = {r for r in page.roots() if r not in on_path and not page.objects[r].raw.get("_non_drawable")}
        for a in chain[1:]:
            hide.update(
                m
                for m in page.objects[a].members
                if m not in on_path and not page.objects[m].raw.get("_non_drawable")
            )
        if not hide or hide == self._hidden_roots:
            self._show_all_roots()
            return
        self._set_hidden(hide)
        from ..names import kind_name

        o = page.objects[index]
        self.statusBar().showMessage(
            "Only %s%s shows. Alt+click its eye again to show everything."
            % (kind_name(o), (" AN %d" % o.an) if o.an > 0 else ""),
            6000,
        )

    def _on_hide_toggled(self, index: int, hidden: bool) -> None:
        """An eye of the Objects panel (any row: a member hides itself only)."""
        s = set(self._hidden_roots)
        if hidden:
            s.add(index)
        else:
            s.discard(index)
            if self.doc is not None and 0 <= index < len(self.doc.page.objects):
                # (shown again: a hidden group around it shows it too)
                s.difference_update(self.doc.page.ancestors(index))
        self._set_hidden(s)

    def hide_or_show_selection(self) -> None:
        """Hide or Show Selection (Space in the Objects panel): the selected objects hide in
        the editor; when they are all hidden already, they show again. They stay selected
        (the same key brings them back)."""
        if self.doc is None or not self._sel:
            return
        page = self.doc.page
        sel = [i for i in self._sel if 0 <= i < len(page.objects)]
        covered = set()
        for h in self._hidden_roots:
            if 0 <= h < len(page.objects):
                covered.update(page.subtree(h))
        s = set(self._hidden_roots)
        if sel and all(i in covered for i in sel):
            for i in sel:
                s.discard(i)
                s.difference_update(page.ancestors(i))
            self._set_hidden(s)
            msg = "%d object%s shown again." % (len(sel), "" if len(sel) == 1 else "s")
        else:
            s.update(sel)
            self._set_hidden(s)
            msg = (
                "%d object%s hidden in the editor (they stay on the page). The same key "
                "shows them again." % (len(sel), "" if len(sel) == 1 else "s")
            )
        self.statusBar().showMessage(msg, 6000)

    def _show_all_roots(self) -> None:
        if self.doc is None:
            self._hidden_roots = set()
            self._hidden_snap = None
            self.scene.set_hidden(set())
            self.objects_dock.set_hidden(set())
            self._update_hidden_chip()
            return
        self._set_hidden(set())

    def _update_hidden_chip(self) -> None:
        chip = getattr(self, "_hidden_chip", None)
        if chip is None:
            return
        n = len(self._hidden_roots)
        chip.setVisible(n > 0)
        chip.setText("%d hidden  [show]" % n)

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
        d.set_actions(self._tree_menu_actions())

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
            self.statusBar().showMessage(
                "%d object%s renamed." % (dlg.renamed, "" if dlg.renamed == 1 else "s"), 5000
            )

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
            self.statusBar().showMessage("Drop a tag on a genie to bind it", 4000)
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

    # ----------------------------------------------------- selection modes
    _SEL_FILTERS = [
        None,
        {"line", "pipe"},
        {"rectangle", "ellipse", "polygon"},
        {"text", "number"},
        {"button"},
        {"symbol", "symbol_set"},
        {"genie", "composite_genie", "group"},
    ]

    def _apply_sel_filter(self, idx: int) -> None:
        kinds = self._SEL_FILTERS[idx] if 0 <= idx < len(self._SEL_FILTERS) else None
        if kinds is None:
            self.view.set_kind_filter(None)
            return
        from ..names import kind_code

        def ok(i: int) -> bool:
            if self.doc is None or i >= len(self.doc.page.objects):
                return True
            root = self.doc.root_of(i)
            # (a placed group-root symbol is a Symbol, as its row names it)
            return kind_code(self.doc.page.objects[root]) in kinds

        self.view.set_kind_filter(ok)
        self.statusBar().showMessage("Only %s can be selected." % self.sel_filter.currentText().lower(), 4000)

    # ---------------------------------------- contextual ribbon tabs + HUD
    def _queue_context_update(self, origin: str) -> None:
        """Coalesce rapid selection changes through one 0 ms timer."""
        self._ctx_origin = origin
        if getattr(self, "_ctx_pending", False):
            return
        self._ctx_pending = True
        QtCore.QTimer.singleShot(0, self, self._apply_selection_context)

    _SHAPE_KIND_NAMES = ("line", "pipe", "polygon", "rectangle", "ellipse")

    def _classify_selection(self) -> set:
        from ...model.document import ObjectKind

        traits = set()
        if self.doc is None:
            return traits
        for i in self._sel:
            if i >= len(self.doc.page.objects):
                continue
            kind = self.doc.page.objects[i].kind
            if kind in (ObjectKind.TEXT, ObjectKind.NUMBER, ObjectKind.BUTTON):
                traits.add("TEXT")
            if kind.value in self._SHAPE_KIND_NAMES:
                traits.add("SHAPE")
            if self.doc.page.nearest_genie(i) is not None:
                traits.add("GENIE")
        return traits

    def _sel_shapes(self, quiet: bool = False) -> list:
        return [
            i
            for i in self._filter_template(self._sel, "edit", quiet)
            if i < len(self.doc.page.objects)
            and self.doc.page.objects[i].kind.value in self._SHAPE_KIND_NAMES
        ]

    def _sel_texts(self, quiet: bool = False) -> list:
        # a button's caption font is its dwFlags-0x40 font block (set_font writes it)
        return [
            i
            for i in self._filter_template(self._sel, "edit", quiet)
            if i < len(self.doc.page.objects)
            and self.doc.page.objects[i].kind.value in ("text", "number", "button")
        ]

    def _apply_shape_width(self, v: int) -> None:
        if self._syncing or self.doc is None:
            return
        with self.doc.batch("line width"):
            ok = [self.doc.set_line_width(i, v) for i in self._sel_shapes()]
        if any(ok):
            self._refresh()

    def _apply_shape_style(self, idx: int) -> None:
        if self._syncing or self.doc is None:
            return
        with self.doc.batch("line style"):
            ok = [self.doc.set_line_style(i, idx) for i in self._sel_shapes()]
        if any(ok):
            self._refresh()

    def _apply_shape_filled(self, on: bool) -> None:
        if self._syncing or self.doc is None:
            return
        with self.doc.batch("fill"):
            ok = [self.doc.set_filled(i, on) for i in self._sel_shapes()]
        if any(ok):
            self._refresh()

    def _apply_font_face(self, font) -> None:
        if self._syncing or self.doc is None:
            return
        with self.doc.batch("font"):
            ok = [self.doc.set_font(i, face=font.family()) for i in self._sel_texts()]
        if any(ok):
            self._refresh()

    def _apply_font_size(self, pts: int) -> None:
        if self._syncing or self.doc is None:
            return
        with self.doc.batch("font size"):
            ok = [self.doc.set_font(i, size=pts) for i in self._sel_texts()]
        if any(ok):
            self._refresh()

    def _sync_context_widgets(self, traits: set) -> None:
        """Push the first selected shape/text object's values into the
        contextual widgets (signal-guarded)."""
        if self.doc is None:
            return
        self._syncing = True
        try:
            if "SHAPE" in traits:
                shapes = self._sel_shapes(quiet=True)  # a query, not an edit
                if shapes:
                    st = self.doc.page.objects[shapes[0]].style
                    self.shape_width.setValue(max(1, st.line_width or 1))
                    self.shape_style.setCurrentIndex(st.line_style if 0 <= st.line_style <= 4 else 0)
                    self.shape_filled.setChecked(bool(st.filled))
            if "TEXT" in traits:
                texts = self._sel_texts(quiet=True)
                if texts:
                    st = self.doc.page.objects[texts[0]].style
                    self.font_combo.setCurrentFont(QtGui.QFont(st.font or "Arial"))
                    # unset size shows the Citect default (10pt), not the
                    # spinbox minimum (4)
                    self.font_size.setValue(st.font_size or 10)
        finally:
            self._syncing = False

    def _apply_selection_context(self) -> None:
        self._ctx_pending = False
        ribbon = getattr(self, "ribbon", None)
        if ribbon is None:
            return
        traits = self._classify_selection()
        # auto-activate only for canvas-origin selections while DESIGN is the
        # active static tab; tree selections only tint
        on_design = ribbon._tabs.currentIndex() == 0
        # (Settings > Ribbon > Contextual tabs: off, a canvas selection only tints its tab)
        from ..prefs import window_options

        activate = (
            bool(traits) and self._ctx_origin == "canvas" and on_design and window_options.CONTEXT_TABS_OPEN
        )
        # (the tabs are titled 'Text' / 'Shape' / 'Genie':, no capitals)
        order = [t.title() for t in ("TEXT", "SHAPE", "GENIE") if t in traits]
        ribbon.set_context_tabs(order, activate=activate)
        self._sync_context_widgets(traits)
        self._schedule_hud()

    def _schedule_hud(self, *_args) -> None:
        """(Re)show the selection HUD once the view settles -- after a
        selection change, an edit commit (drag / nudge / resize hid it), a
        zoom or a scroll (it would otherwise float at a stale spot)."""
        hud = getattr(self, "hud", None)
        if hud is None:
            return
        if not (self._sel and self.doc is not None) or not self.act_selection_hud.isChecked():
            hud.hide()
            return
        t = getattr(self, "_hud_timer", None)
        if t is None:
            t = self._hud_timer = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._show_hud)
        if hud.isVisible() and _args:
            hud.hide()  # moving view: hide until it settles
        # (Settings > Ribbon > Selection toolbar > Show it again after the view settles for)
        from ..prefs import timing_options

        t.start(timing_options.HUD_DELAY_MS)

    def _show_hud(self) -> None:
        if (
            not self._sel
            or self.doc is None
            or getattr(self.view, "_resizing", None)
            or QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton
        ):
            return
        if not self.act_selection_hud.isChecked():
            return
        box = self.doc.selection_extent(self._sel)
        if box is None:
            return
        l, t, r, b = box
        roots = {self.doc.root_of(i) for i in self._sel}
        genie = len(roots) == 1 and self.doc.page.objects[next(iter(roots))].is_genie
        self.hud.set_primary(self.act_genie_params if genie else self.act_obj_props)
        skip = set()
        for root in roots:
            skip.update(self.doc.page.subtree(root))
        self.hud.show_for_rect(QtCore.QRectF(l, t, r - l, b - t), skip)

    def _toggle_selection_hud(self, on: bool) -> None:
        """View > Selection Toolbar."""
        if on:
            self._schedule_hud()
        elif getattr(self, "hud", None) is not None:
            self.hud.hide()

    def _on_object_activated(self, index: int) -> None:
        """Double-click acts on the top-level unit."""
        if self.doc is None or index >= len(self.doc.page.objects):
            return
        top = self.doc.root_of(index)
        obj = self.doc.page.objects[top]
        self._on_dock_selection([top])
        if obj.is_genie:
            self._edit_genie(top, obj)
        else:
            # a symbol set opens its Symbol Set sheet (object_properties picks
            # it unless read-only); an object's own Touch / Keyboard records
            # are its commands, not members
            self.object_properties(top)

    def _edit_genie_at(self, index: int) -> None:
        """The Properties panel's 'Edit genie parameters…': THAT genie's
        parameters -- not its top-level unit's, as a canvas double-click
        acts on (a genie inside a plain group would open the group's
        sheet)."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        obj = self.doc.page.objects[index]
        if obj.is_genie:
            self._edit_genie(index, obj)
        else:
            self._on_object_activated(index)

    def _member_reachable(self, index: int) -> bool:
        """May a Ctrl+double-click open this object's own sheet?"""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return False
        root = self.doc.root_of(index)
        if self._is_template_obj(root):
            return index != root
        return not self._unit_locked(root)

    def _on_member_activated(self, index: int) -> None:
        """Ctrl+double-click (CtDraw): the properties of the EXACT object --
        a member of a group, symbol or genie, or a template record -- instead
        of its top-level unit's. A genie member's (and a locked template
        record's) sheet is read-only with 'Edit Definition'."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return
        self._on_dock_selection([index])
        self.objects_dock.set_selection(self._sel)  # the tree shows the member
        self._open_sheet_of(index)

    def _open_sheet_of(self, index: int) -> None:
        """The sheet that shows object ``index`` (Ctrl+double-click, Goto
        Object's OK): a touch / keyboard record is its owner's command, shown
        on the owner's Input tab; a page-wide keyboard command is a Page
        Properties setting; anything else opens its own sheet."""
        from ...format import inputs

        obj = self.doc.page.objects[index]
        if obj.raw.get("_type_code") in inputs.INPUT_TCS:
            if obj.parent is not None:
                self.edit_input(obj.parent)
            else:
                self.page_properties()
            return
        self.object_properties(index)

    def _on_canvas_activated(self, index: int) -> None:
        """A double-click on the canvas: the in-place editor for a plain text or button
        caption (Settings > 'Double-click a text to edit it in place', on by default), else
        the object's properties (CtDraw)."""
        from ..prefs.settings import option

        if self.doc is not None and 0 <= index < len(self.doc.page.objects) and option("dblclick_edits_text"):
            top = self.doc.root_of(index)
            if self._inplace_refusal(top) is None:
                self._on_dock_selection([top])
                self.edit_text_in_place(top)
                return
        self._on_object_activated(index)
