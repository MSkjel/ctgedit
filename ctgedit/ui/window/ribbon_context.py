"""The contextual ribbon tabs and the canvas HUD."""

from __future__ import annotations

from ..main_window_base import (
    QtCore,
    QtGui,
    QtWidgets,
)


class RibbonContext:
    """The ribbon tabs that follow the selection, and the canvas HUD."""

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
