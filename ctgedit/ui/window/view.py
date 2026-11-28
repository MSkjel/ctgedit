"""Refresh, status, selection, focus mode and contextual ribbon tabs."""

from __future__ import annotations

from ...infra.paths import path_key

import contextlib
from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtGui,
    TITLE_APP,
    os,
    set_note_role,
)
from ..main_window_base import PaintWatch
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
        # Preview States, the Click Map and a tag's marks follow the page (page_debug)
        self._debug_views_refresh()
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
        PaintWatch(self.view.viewport(), self._panels_painted)
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
        if live and path_key(user_dir) != path_key(live):
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
            except Exception:  # noqa: BLE001  # swallow-ok: a hint only
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
        if getattr(self, "preview_states", None) is not None:
            self.preview_states.set_selection(self._sel)
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
    #: name in _update_actions and its _enable_* parts (CUSTOM_ENABLE); ALWAYS_ENABLED
    #: need nothing open.
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
            "act_preview_states",
            "act_click_map",
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
    #: set by their own rule in _update_actions and its _enable_* parts
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
        # Tools > Rename Pages, Check Project: whole projects, also with nothing open
        "act_rename_pages",
        "act_check_project",
        # Find / Replace (All pages of the project) and Pack Libraries: a whole
        # project, also with nothing open
        "act_find",
        "act_replace",
        "act_pack_libs",
        "act_pack_db",
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
        is_page = has_doc and not isinstance(self.doc, ElementDocument)
        docs = getattr(self, "_docs", [])
        self._enable_by_rule(has_doc, has_sel, n_units, is_page, docs)
        self._label_run(has_doc and not is_page)
        find = self.__dict__.get("_find_dlg")
        if find is not None:
            with contextlib.suppress(RuntimeError):
                find.page_changed()  # (This page / All open pages need a page)
        self._enable_drawing_actions(has_doc, has_sel, n_units > 1)
        self._enable_document_actions(has_doc, docs)
        self._enable_step_and_view_actions(has_doc, has_sel)
        self._enable_selection_actions(has_doc, has_sel, n_units, is_page)
        # the open Adjust Position and Size window follows the selection, and
        # so do the other selection windows (Align, Font, Swap / Adjust Colours)
        self._sync_adjust_dialog()
        self._sync_selection_windows()
        self._sync_goto_dialog()
        from ..panels import status_items

        status_items.refresh(self)  # (the status bar's fields)

    def _enable_by_rule(self, has_doc: bool, has_sel: bool, n_units: int, is_page: bool, docs) -> None:
        """The commands of ENABLE_WHEN, by their rule."""
        in_project = False
        if is_page:
            with contextlib.suppress(Exception):
                in_project = bool(self.doc.page_record_available())
        elif not has_doc:
            # (no page: Compile and Run act on the project of the most recent page)
            in_project = self._compile_target() is not None
        state = {
            "doc": has_doc,
            "sel": has_sel,
            "multi": n_units > 1,
            "three": n_units > 2,
            "page": is_page,
            "project": in_project,
            "tab": bool(docs),
        }
        for rule, names in self.ENABLE_WHEN.items():
            for nm in names:
                getattr(self, nm).setEnabled(state[rule])

    def _enable_drawing_actions(self, has_doc: bool, has_sel: bool, multi: bool) -> None:
        """The drawing tools, Insert and Align."""
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

    def _enable_document_actions(self, has_doc: bool, docs) -> None:
        """Revert, the document tabs' commands and New Page from the last template."""
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

    def _enable_step_and_view_actions(self, has_doc: bool, has_sel: bool) -> None:
        """Undo and Redo, Previous / Next View, Paste Appearance, the underlay's commands and
        the undo history."""
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

    def _enable_selection_actions(self, has_doc: bool, has_sel: bool, n_units: int, is_page: bool) -> None:
        """Everything else that needs a page / a selection / a kind (a command that cannot
        apply is greyed out instead of silently doing nothing)."""
        traits = self._classify_selection() if has_sel else set()
        one = has_sel and n_units == 1
        self._enable_paste_composite(has_doc)
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
        self._enable_library_actions(has_doc, has_sel, is_page, traits, one)
        self._enable_picture_and_text_actions(has_sel, traits, one)
        # Edit > Repeat: the last repeatable command, while it can run (ui/prefs/keymap)
        self.act_repeat.setEnabled(_keymap.repeat_ok(self))

    def _enable_paste_composite(self, has_doc: bool) -> None:
        """Paste Composite Genie, with its tips."""
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

    def _enable_library_actions(self, has_doc: bool, has_sel: bool, is_page: bool, traits, one: bool) -> None:
        """The template, genie and library element commands (``traits`` = the selection's,
        ``one`` = one unit selected)."""
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

    def _enable_picture_and_text_actions(self, has_sel: bool, traits, one: bool) -> None:
        """The Bitmap Editor, Tools > Pictures and the Text menu."""
        first = self._sel[0] if has_sel else None
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
        self.statusBar().showMessage("UI layout reset.", 4000)

    # -------------------------------------- what changed since the save
    def changes_since_save(self):
        """(changed or new record indices, how many fewer records than the saved page has),
        by the records' payloads against the saved file (ui/panels/changes_dock)."""
        return self.changed_records()

    def _mark_changes(self) -> None:
        """View > Mark Changes Since Save: new, changed and removed objects marked on the
        canvas, kept up to date with every edit while ticked (ui/panels/changes_dock)."""
        self._changes_mark()

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
