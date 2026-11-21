"""Canvas navigation commands."""

from __future__ import annotations

from ..main_window_base import QtCore, QtWidgets, app_settings


class NavigationCommands:
    """See the module notes."""

    # ---- construction (called from main_window) -------------------------------------
    def _build_navigator_dock(self) -> QtWidgets.QDockWidget:
        """View > Panels > Navigator: the navigator's map as a panel (hidden at first)."""
        from ..editor.navigator import MapSource, NavigatorMap

        self.nav_source = MapSource(self.view)
        dock = QtWidgets.QDockWidget("Navigator", self)
        dock.setObjectName("dock_navigator")
        dock.setWidget(NavigatorMap(self.view, self.nav_source, dock))
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        dock.hide()
        self.dock_navigator = dock
        return dock

    def _install_navigation(self) -> None:
        """The rulers, the navigator panel and the canvas's navigation signals (after the
        actions, menus and the NavBar exist)."""
        from ..style.glyphs_nav import icon as nav_icon
        from ..editor.navigator import NavigatorPanel
        from ..editor.rulers import Rulers

        v = self.view
        for tool, glyph in (("pan", "hand"), ("zoom", "zoom_box")):
            a = self.tool_actions.get(tool)
            if a is not None:
                a.setIcon(nav_icon(glyph))
        for name, glyph in (
            ("act_prev_view", "prev_view"),
            ("act_next_view", "next_view"),
            ("act_rulers", "rulers"),
            ("act_navigator", "navigator"),
            ("act_crosshair", "crosshair"),
            ("act_show_guides", "guidelines"),
            ("act_zoom_window", "magnifier"),
            ("act_save_view", "saved_views"),
            ("act_zoom_all", "fit_all"),
            ("act_zoom_sel", "zoom_sel"),
        ):
            a = getattr(self, name, None)
            if a is not None and a.icon().isNull():
                a.setIcon(nav_icon(glyph))
        self.rulers = Rulers(v, self)
        self._sync_rulers()
        if getattr(self, "nav_source", None) is None:
            from ..editor.navigator import MapSource

            self.nav_source = MapSource(v)
        self.navigator = NavigatorPanel(v, self.nav_source, self)
        self.navigator.set_wanted(self.act_navigator.isChecked())
        v.viewHistoryChanged.connect(self._sync_view_history_actions)
        v.toolRequested.connect(self._set_tool)
        v.toolArmRequested.connect(self._arm_tool_for_a_moment)
        from ...editing import ui_state

        def history_state():
            doc = getattr(self, "doc", None)
            return ui_state(doc) if doc is not None else None

        v.set_history_source(history_state)
        v.set_fill_colour_source(lambda: self._draw_colour)
        # Previous / Next View act on the canvas (as the tool letters): canvas-scoped keys
        ctx = QtCore.Qt.WidgetWithChildrenShortcut
        for a in (self.act_prev_view, self.act_next_view):
            a.setShortcutContext(ctx)
            self.removeAction(a)
            v.addAction(a)
        # Space pans the canvas under the pointer wherever the keyboard is (not in a field)
        app = QtWidgets.QApplication.instance()
        if app is not None and getattr(app, "_ctg_space_pan", None) is None:
            from ..main_window_base import _app_filter
            from ..editor.viewnav import SpacePanForwarder

            app._ctg_space_pan = SpacePanForwarder(app)
            _app_filter().add(app._ctg_space_pan, SpacePanForwarder.TYPES)
        self._sync_view_history_actions()

    # ---- Previous View / Next View -----------------------------------------------------
    def _sync_view_history_actions(self) -> None:
        has_doc = getattr(self, "doc", None) is not None
        self.act_prev_view.setEnabled(bool(has_doc and self.view.can_go_back()))
        self.act_next_view.setEnabled(bool(has_doc and self.view.can_go_forward()))

    def _prev_view(self) -> None:
        if not self.view.go_back():
            self.statusBar().showMessage("No earlier view to go back to.", 3000)

    def _next_view(self) -> None:
        if not self.view.go_forward():
            self.statusBar().showMessage("No later view to go to.", 3000)

    # ---- the zoom commands -------------------------------------------------------------
    def _zoom_in_cmd(self) -> None:
        self.view.zoom_step(1)

    def _zoom_out_cmd(self) -> None:
        self.view.zoom_step(-1)

    def _arm_tool_for_a_moment(self, tool: str) -> None:
        """Z held: the canvas takes the zoom box for a moment. Unlike a tool change, what
        the tool before had waiting (a symbol or a genie to place, a control to insert)
        stays, so it is there again when Z is let go."""
        self.view.set_tool(tool)
        a = self.tool_actions.get(tool)
        if a is not None and not a.isChecked():
            a.setChecked(True)

    # ---- rulers and the navigator ------------------------------------------------------
    def _toggle_rulers(self) -> None:
        app_settings().setValue("view/rulers", self.act_rulers.isChecked())
        self._sync_rulers()

    def _sync_rulers(self) -> None:
        """The rulers show while View > Rulers is on and a page is open (not around the
        start page)."""
        r = getattr(self, "rulers", None)
        if r is None:
            return
        on = bool(self.act_rulers.isChecked() and getattr(self, "doc", None) is not None)
        if on != r.is_shown():
            r.set_visible(on)

    def _toggle_navigator(self) -> None:
        on = self.act_navigator.isChecked()
        app_settings().setValue("view/navigator", on)
        n = getattr(self, "navigator", None)
        if n is not None:
            n.set_wanted(on)

    def delete_guideline(self, axis: str, pos: int) -> None:
        """The right-click menu's Delete Guideline: the guideline ``axis`` ('v' | 'h') at
        ``pos`` goes (as a drop at the page edge)."""
        vert, horiz = self.view.guidelines()
        lines = vert if axis == "v" else horiz
        if int(pos) not in lines:
            return
        lines = [g for g in lines if g != int(pos)]
        self.view.set_guidelines(lines if axis == "v" else vert, horiz if axis == "v" else lines)
        self._on_guidelines_changed("Deleted the guideline at %d px." % int(pos))

    def guideline_setup_at(self, axis: str, pos: int) -> None:
        """The right-click menu's Guideline Position: Guidelines Setup with that guideline
        selected."""
        self._guidelines_dialog()
        dlg = self._dialogs.get("guides")
        if dlg is not None:
            dlg.select(axis, int(pos))

    def _clear_guidelines(self) -> None:
        """View > Guidelines > Clear All Guidelines (the page's; Undo is not needed: they
        are editor state, and Guidelines Setup can add them again)."""
        if self.doc is None:
            return
        vert, horiz = self.view.guidelines()
        n = len(vert) + len(horiz)
        if not n:
            self.statusBar().showMessage("The page has no guidelines.", 3000)
            return
        self.view.set_guidelines([], [])
        self._on_guidelines_changed("Cleared %d guideline%s." % (n, "" if n == 1 else "s"))

    # ---- saved views -------------------------------------------------------------------
    def _sync_saved_views_menu(self, force: bool = False) -> None:
        """View > Saved Views lists the page's views (rebuilt when the page shown or its
        views change), so the command palette finds each one."""
        menu = getattr(self, "saved_views_menu", None)
        if menu is None:
            return
        from ..editor import saved_views as SV

        doc = getattr(self, "doc", None)
        views = SV.load_views(doc) if doc is not None else []
        key = (
            SV.views_key(doc) if doc is not None else None,
            tuple((v["name"], v["zoom"], v["cx"], v["cy"]) for v in views),
        )
        if not force and key == getattr(self, "_saved_views_key", None):
            return
        self._saved_views_key = key
        menu.clear()
        menu.addAction(self.act_save_view)
        if views:
            menu.addSeparator()
            for k, v in enumerate(views):
                a = menu.addAction(
                    v["name"].replace("&", "&&"), lambda _=False, i=k: self._recall_saved_view(i)
                )
                a.setStatusTip("Show the saved view '%s'" % v["name"])
        menu.addSeparator()
        menu.addAction(self.act_manage_views)

    def _recall_saved_view(self, k: int) -> None:
        from ..editor import saved_views as SV

        views = SV.load_views(self.doc) if self.doc is not None else []
        if not 0 <= k < len(views):
            return
        v = views[k]
        self.view.go_to_view(v["zoom"], v["cx"], v["cy"])
        self.statusBar().showMessage("View '%s'." % v["name"], 3000)

    def _save_view_dialog(self) -> None:
        """View > Saved Views > Save Current View…: a small popover asks the name."""
        if self.doc is None:
            return
        from ..editor import saved_views as SV

        views = SV.load_views(self.doc)
        pop = SV.SaveViewPopover(SV.next_name(views), self)
        doc = self.doc

        def saved(name: str) -> None:
            if doc is not self.doc:
                return
            vs = SV.load_views(doc)
            if SV.is_full(vs, name):
                # (a new name past the limit would not be kept: not saved, and said so)
                QtWidgets.QMessageBox.information(self, "Save Current View", SV.full_text())
                return
            vs = [v for v in vs if v["name"] != name]
            vs.append(SV.current_view(self.view, name))
            SV.store_views(doc, vs)
            self._sync_saved_views_menu(force=True)
            dlg = self._dialogs.get("saved_views") if hasattr(self, "_dialogs") else None
            if dlg is not None:
                try:
                    dlg.load_for(doc)
                except RuntimeError:  # swallow-ok: the window was closed
                    pass
            self.statusBar().showMessage("Saved the view '%s'." % name, 4000)

        pop.saved.connect(saved)
        self._save_view_popover = pop  # (a reference keeps it alive while shown)
        vp = self.view.viewport()
        pop.open_at(vp.mapToGlobal(QtCore.QPoint(max(0, vp.width() // 2 - 150), 40)))

    def _manage_saved_views(self) -> None:
        """View > Saved Views > Manage Saved Views…: modeless, it shows the page tab shown."""
        if self._raise_modeless(self._dialogs.get("saved_views")):
            return
        from ..editor.saved_views import ManageSavedViewsDialog

        dlg = ManageSavedViewsDialog(self)
        dlg._ctg_reads_page = True
        dlg._ctg_follow = lambda doc, d=dlg: d.load_for(doc)
        self._show_modeless(dlg, "saved_views", scope="window")
