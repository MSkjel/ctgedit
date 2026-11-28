"""The aids to see what a page does without running it: View > Preview States (the page
drawn for values set by hand), View > Click Map (what takes a click where) and a tag's uses
marked on the page (the Tags panel)."""

from __future__ import annotations

from ..main_window_base import QtCore, QtWidgets


class PageDebugViews:
    """Preview States, Click Map and the tag marks of the window's page."""

    # -- construction ----------------------------------------------------------------
    def _build_preview_dock(self) -> None:
        """The Preview States panel (hidden; View > Preview States shows it)."""
        from ..panels.preview_states import PreviewStatesPanel

        self.preview_states = PreviewStatesPanel(self)
        d = QtWidgets.QDockWidget("Preview States", self)
        d.setObjectName("dock_preview_states")
        d.setWidget(self.preview_states)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, d)
        self.tabifyDockWidget(self.dock_page_checks, d)
        d.hide()
        self.dock_preview_states = d
        self.preview_states.valuesChanged.connect(self._preview_apply)
        self.preview_states.objectsChosen.connect(self._select_from_panel)
        d.toggleViewAction().toggled.connect(self._preview_shown)
        self._wire_tag_uses()

    # -- Preview States --------------------------------------------------------------
    def _previewing(self) -> bool:
        dock = getattr(self, "dock_preview_states", None)
        return dock is not None and not dock.isHidden() and self.doc is not None

    def toggle_preview_states(self, on: bool) -> None:
        """View > Preview States: the panel shown (the page drawn in the states set) or not."""
        dock = self.dock_preview_states
        if on:
            dock.show()
            dock.raise_()
        else:
            dock.hide()

    def _preview_shown(self, on: bool) -> None:
        a = getattr(self, "act_preview_states", None)
        if a is not None and a.isChecked() != on:
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)
        if on:
            self._preview_refresh()
        else:
            self._preview_off()

    def _preview_model(self):
        """The Preview States model of the page shown (made again after an edit)."""
        doc = self.doc
        key = (
            id(doc),
            getattr(doc, "derive_serial", 0),
            getattr(doc, "touch_serial", 0),
            getattr(doc, "edit_serial", 0),
        )
        memo = self.__dict__.get("_preview_memo")
        if memo is not None and memo[0] == key and memo[1].doc is doc:
            return memo[1]
        from ...features.state_preview import PreviewModel

        try:
            model = PreviewModel(doc)
        except Exception:  # noqa: BLE001
            from ...infra.logsetup import note_exception

            note_exception("page_debug._preview_model")
            return None
        self._preview_memo = (key, model)
        return model

    def _preview_refresh(self) -> None:
        """_refresh: the panel and the page's states follow the page shown."""
        if not self._previewing():
            if self.__dict__.get("_preview_on_scene"):
                self._preview_off()
            return
        model = self._preview_model()
        panel = self.preview_states
        if model is not panel.model:
            values = self.doc.ui.preview_values if getattr(self.doc, "ui", None) is not None else {}
            panel.set_model(model, values)
        panel.set_selection(self._sel)
        self._preview_apply()

    def _preview_apply(self) -> None:
        """Draw the page in the states the panel's values make."""
        if not self._previewing():
            return
        model = self.preview_states.model
        states = model.states(self.preview_states.values) if model is not None else {}
        self.scene.apply_states(states, replace=True)
        self._preview_on_scene = True
        self.view.viewport().update()
        if getattr(self, "_click_map_on", False):
            self._click_map_refresh()

    def _preview_off(self) -> None:
        """The page drawn as it is stored again."""
        if self.__dict__.get("_preview_on_scene"):
            self._preview_on_scene = False
            self.scene.clear_states()
            self.view.viewport().update()
            if getattr(self, "_click_map_on", False):
                self._click_map_refresh()

    def preview_shown_states(self) -> dict:
        """{AN: ObjectState} the preview draws now ({} while it is off)."""
        if not self.__dict__.get("_preview_on_scene"):
            return {}
        return self.scene.sim_states()

    # -- Click Map -------------------------------------------------------------------
    def toggle_click_map(self, on: bool) -> None:
        """View > Click Map: what takes a click or a key shaded on the page."""
        self._click_map_on = bool(on)
        self._click_map_refresh(tell=True)

    def _click_map_targets(self):
        """The page's click targets as the page shows now (Preview States hides some)."""
        from ...features.click_map import click_targets

        shown = None
        settled: frozenset = frozenset()
        states = self.preview_shown_states()
        if self.__dict__.get("_preview_on_scene"):
            # (a condition set in the panel is known: TRUE hides, FALSE shows)
            from ...features.state_preview import ON

            pre = ON + ":"
            settled = frozenset(c[len(pre) :] for c in self.preview_states.values if c.startswith(pre))
        if states:
            page = self.doc.page
            hidden = {an for an, st in states.items() if not st.shown}

            def shown(i: int) -> bool:
                j, guard = i, 0
                while j is not None and guard < 64:
                    if page.objects[j].an in hidden:
                        return False
                    j = page.objects[j].parent
                    guard += 1
                return True

        try:
            return click_targets(self.doc, shown, settled)
        except Exception:  # noqa: BLE001
            from ...infra.logsetup import note_exception

            note_exception("page_debug._click_map_targets")
            return []

    def _click_map_refresh(self, tell: bool = False) -> None:
        from ..editor import click_map as CMV

        if not getattr(self, "_click_map_on", False) or self.doc is None:
            CMV.set_targets(self.view, None)
            return
        targets = self._click_map_targets()
        CMV.set_targets(self.view, targets)
        if tell:
            from ...features.click_map import summary

            self.statusBar().showMessage("Click Map: " + summary(targets), 8000)

    # -- the refresh -----------------------------------------------------------------
    def _debug_views_refresh(self) -> None:
        """_refresh: Preview States, the Click Map and a tag's marks follow the page shown."""
        self._preview_refresh()
        if getattr(self, "_click_map_on", False):
            self._click_map_refresh()
        if getattr(self, "tags_dock", None) is not None:
            self._tag_marks_refresh()

    # -- a tag's uses (the Tags panel's On This Page) --------------------------------
    def _wire_tag_uses(self) -> None:
        t = self.tags_dock
        t.currentTagChanged.connect(lambda _name: self._tag_marks_refresh())
        t.marksToggled.connect(lambda _on: self._tag_marks_refresh())
        t.usesChosen.connect(self._select_from_panel)
        self.dock_tags.visibilityChanged.connect(lambda _on: self._tag_marks_refresh())

    def _tag_uses_index(self):
        """The tag uses of the page shown (made again after an edit)."""
        doc = self.doc
        key = (
            id(doc),
            getattr(doc, "derive_serial", 0),
            getattr(doc, "touch_serial", 0),
            getattr(doc, "edit_serial", 0),
        )
        memo = self.__dict__.get("_tag_uses_memo")
        if memo is not None and memo[0] == key and memo[1].doc is doc:
            return memo[1]
        from ...features.tag_uses import PageTagUses

        try:
            got = PageTagUses(doc)
        except Exception:  # noqa: BLE001
            from ...infra.logsetup import note_exception

            note_exception("page_debug._tag_uses_index")
            return None
        self._tag_uses_memo = (key, got)
        return got

    def _tag_marks_refresh(self) -> None:
        from ..editor import tag_marks as TM

        dock = getattr(self, "tags_dock", None)
        if dock is None:
            return
        tag = dock.current_tag()
        if self.doc is None or not tag or not self.dock_tags.isVisible():
            TM.set_marks(self.view, "", None)
            dock.show_uses(tag if self.doc is not None else "", [])
            return
        index = self._tag_uses_index()
        uses = index.uses(tag) if index is not None else []
        from ...features.tag_uses import count_text

        dock.show_uses(tag, uses, count_text(uses))
        if not dock.marks_wanted():
            TM.set_marks(self.view, tag, None)
            return
        by: dict = {}
        for u in uses:
            by.setdefault(u.index, []).append(u)
        marks = []
        for i, us in by.items():
            try:
                box = self.doc._obj_bbox(i)
            except Exception:  # noqa: BLE001  # swallow-ok: an object without a box is not marked
                continue
            marks.append((box, us))
        TM.set_marks(self.view, tag, marks)
