"""Selecting on the canvas and the edit intents."""

from __future__ import annotations

from ..names import plural

from ..main_window_base import (
    QtWidgets,
    os,
    TransformError,
)


class CanvasSelection:
    """What a click, a drag or a key selects on the canvas, and the edit intents."""

    # ------------------------------------------------------------- selecting
    def _unit_label(self, root: int) -> str:
        """'Genie AN 80': a unit's kind and AN, as the status bar names it."""
        if self.doc is None or not 0 <= root < len(self.doc.page.objects):
            return "Object"
        from ..names import kind_name

        o = self.doc.page.objects[root]
        return "%s AN %d" % (kind_name(o), o.an) if o.an > 0 else kind_name(o)

    def _unit_row_text(self, root: int) -> str:
        """'Genie AN 80 · energydemo.valve_left': the label and what the unit shows (its
        library element, its text or its name), as the Objects panel's row reads."""
        o = self.doc.page.objects[root]
        what = self._element_key(o, keep_case=True) or " ".join((o.style.text or "").split())[:24] or o.cls
        return "%s · %s" % (self._unit_label(root), what) if what else self._unit_label(root)

    def _select_pool(self) -> list:
        """The page's top-level units a click could select now: not locked, not the page
        template, not hidden in the editor, as the ribbon's Filter allows."""
        if self.doc is None:
            return []
        page = self.doc.page
        ok = self.view.kind_filter()
        hidden = self._hidden_roots
        out = []
        for i in page.roots():
            o = page.objects[i]
            if (
                o.raw.get("_non_drawable")
                or o.raw.get("_from_template")
                or self._unit_locked(i)
                or i in hidden
            ):
                continue
            if str(o.raw.get("_ref") or "").lower().endswith("<ctt"):
                continue  # (the template's own root)
            if ok is not None and not ok(i):
                continue
            out.append(i)
        return out

    def _filter_note(self) -> str:
        """' (Filter)' while the ribbon's Filter limits what can be selected."""
        sf = getattr(self, "sel_filter", None)
        return " (Filter)" if sf is not None and sf.currentIndex() > 0 else ""

    def select_all(self) -> None:
        """Edit > Select All: every top-level unit the Filter lets through (a group / genie
        is one unit), not the locked ones."""
        if self.doc is None:
            return
        self.commit_nudge()
        roots = self._select_pool()
        self._set_selection(roots)
        n = len(roots)
        self.statusBar().showMessage("Selected %s%s." % (plural(n, "object"), self._filter_note()), 4000)

    def deselect_all(self) -> None:
        """Edit > Select > Deselect All."""
        if self.doc is not None:
            self.view.clear_node_selection()
            self._set_selection([])

    def invert_selection(self) -> None:
        """Edit > Select > Invert Selection: every unit Select All would take but the
        selected ones."""
        if self.doc is None:
            return
        n = len(self.doc.page.objects)
        have = {self.doc.root_of(i) for i in self._sel if 0 <= i < n}
        roots = [r for r in self._select_pool() if r not in have]
        self._set_selection(roots)
        self.statusBar().showMessage(
            "Selected %s%s." % (plural(len(roots), "object"), self._filter_note()),
            4000,
        )

    #: Select Same: (mode, menu text, what the status bar says the key object lacks)
    SAME_MODES = (
        ("element", "Library Element", "no library element"),
        ("kind", "Kind", ""),
        ("fill", "Fill Colour", "no fill"),
        ("line_colour", "Line Colour", "no line colour"),
        ("line_width", "Line Width", "no line"),
        ("font", "Font and Size", "no font"),
        ("size", "Size", "no size"),
        ("anim", "Animation Expression", "no animation expression"),
    )
    _LINE_KINDS = frozenset(("line", "pipe", "rectangle", "ellipse", "polygon"))
    _TEXT_KINDS = frozenset(("text", "number", "button"))

    @staticmethod
    def _element_key(o, keep_case: bool = False) -> str:
        """The library element a genie / symbol unit places ('energydemo.valve_left'), ''
        for any other object."""
        for ref in (o.genie_template, o.raw.get("_ref"), o.raw.get("_resolved_ref")):
            s = str(ref or "").strip()
            if not s:
                continue
            low = s.lower()
            for ext in (".<ctm", ".<ctl", ".<ctt", "<ctm", "<ctl", "<ctt"):
                if low.endswith(ext):
                    s = s[: -len(ext)].rstrip(".")
                    break
            s = os.path.basename(s.replace("\\", "/")) if "/" in s or "\\" in s else s
            return s if keep_case else s.lower()
        return ""

    def _unit_expressions(self, root: int) -> frozenset:
        """Every animation expression of the unit's records (case and spaces ignored)."""
        out = set()
        objs = self.doc.page.objects
        for i in self.doc.subtree(root):
            if not 0 <= i < len(objs):
                continue
            a = objs[i].animations
            vals = []
            if a.visibility:
                vals.append(a.visibility.expression)
            if a.movement:
                m = a.movement
                vals += [
                    m.rotation_expr,
                    m.scale_h_expr,
                    m.scale_v_expr,
                    m.move_h_expr,
                    m.move_v_expr,
                    m.slider_expr,
                ]
            if a.fill_colour:
                vals += [t.expression for t in a.fill_colour.thresholds]
            if a.fill_level:
                vals.append(a.fill_level.expression)
            if a.symbol_set:
                vals += list(a.symbol_set.expressions)
            if a.display_value:
                vals.append(a.display_value.expression)
            if a.access_disable:
                vals.append(a.access_disable.expression)
            vals.append(a.cicode_expression)
            for v in vals:
                v = " ".join(str(v or "").split()).lower()
                if v:
                    out.add(v)
        return frozenset(out)

    def _same_key(self, root: int, mode: str):
        """What Select Same compares for ``mode`` on unit ``root`` (None: it has none)."""
        o = self.doc.page.objects[root]
        kind = o.kind.value
        st = o.style
        if mode == "element":
            return self._element_key(o) or None
        if mode == "kind":
            from ..names import kind_code

            code = kind_code(o)  # (a placed group-root symbol: 'symbol')
            for k, group in enumerate(self._SEL_FILTERS):
                if group and code in group:
                    return "group%d" % k
            return code
        if mode == "fill":
            return st.fill_colour if st.filled and st.fill_colour is not None else None
        if mode == "line_colour":
            return st.line_colour if kind not in self._TEXT_KINDS else None
        if mode == "line_width":
            return st.line_width if kind in self._LINE_KINDS else None
        if mode == "font":
            return ((st.font or "").lower(), st.font_size) if kind in self._TEXT_KINDS and st.font else None
        if mode == "size":
            g = o.geometry
            return (g.extent_x, g.extent_y) if g.extent_x > 0 or g.extent_y > 0 else None
        if mode == "anim":
            return self._unit_expressions(root) or None
        return None

    def select_same(self, mode: str) -> None:
        """Edit > Select > Select Same: every unit like the key object (the one added last
        by a click) in ``mode`` (SAME_MODES), as Select All would take them."""
        if self.doc is None:
            return
        self.commit_nudge()
        key = self.view.key_root() if self._sel else None
        n = len(self.doc.page.objects)
        if key is None and self._sel and 0 <= self._sel[-1] < n:
            key = self.doc.root_of(self._sel[-1])
        if key is None or not 0 <= key < n:
            self.statusBar().showMessage(
                "Select an object first: Select Same selects the objects like it.", 5000
            )
            return
        want = self._same_key(key, mode)
        lacks = dict((m, why) for m, _t, why in self.SAME_MODES).get(mode, "")
        if want is None:
            self.statusBar().showMessage("%s has %s." % (self._unit_label(key), lacks), 5000)
            return
        roots = []
        for r in self._select_pool():
            k = self._same_key(r, mode)
            if k is None:
                continue
            if (mode == "anim" and k & want) or (mode != "anim" and k == want):
                roots.append(r)
        if key not in roots and key in self._select_pool():
            roots.append(key)
        self._set_selection(sorted(roots), reveal=False)
        self.statusBar().showMessage(
            "Selected %d object%s like %s%s."
            % (len(roots), "" if len(roots) == 1 else "s", self._unit_label(key), self._filter_note()),
            6000,
        )

    def instances_of(self, root: int) -> list:
        """The selectable units that place the same library element as ``root``."""
        want = self._element_key(self.doc.page.objects[root]) if self.doc is not None else ""
        if not want:
            return []
        return [r for r in self._select_pool() if self._element_key(self.doc.page.objects[r]) == want]

    def select_instances(self, root: int) -> None:
        """Right-click > Select All Instances of <element>."""
        roots = self.instances_of(root)
        if roots:
            self._set_selection(roots, reveal=False)
            self.statusBar().showMessage(
                "Selected %d object%s placing %s%s."
                % (
                    len(roots),
                    "" if len(roots) == 1 else "s",
                    self._element_key(self.doc.page.objects[root], keep_case=True),
                    self._filter_note(),
                ),
                6000,
            )

    def select_using_tag(self, tag: str | None = None) -> None:
        """Edit > Select > Select Objects Using Tag…: every unit with an expression, a
        command or a genie parameter naming the tag (a whole tag name, not a part of a
        longer one)."""
        if self.doc is None:
            return
        if tag is None:
            from ..dialog.select_dialogs import SelectByTagDialog

            dlg = SelectByTagDialog(self, self._tag_completion())
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            tag = dlg.tag()
        tag = (tag or "").strip()
        if not tag:
            return
        from ..remember import store

        store("select_by_tag", tag)
        from ...features import find_replace as FR

        opts = FR.FindOptions(
            tag,
            scopes=frozenset((FR.SCOPE_EXPR, FR.SCOPE_PARAM)),
            match=FR.MATCH_WORD,
            inside_genies=True,
            inside_templates=False,
            template_editable=False,
            break_lock=self._break_lock,
        )
        n = len(self.doc.page.objects)
        found = {self.doc.root_of(h.index) for h in FR.find_hits(self.doc, opts) if 0 <= h.index < n}
        roots = [r for r in self._select_pool() if r in found]
        self._set_selection(roots, reveal=True)
        if roots:
            self.statusBar().showMessage(
                "Selected %d object%s that use %s%s."
                % (len(roots), "" if len(roots) == 1 else "s", tag, self._filter_note()),
                6000,
            )
        else:
            self.statusBar().showMessage("No object you can select uses %s." % tag, 6000)

    #: Edit > Select > Select Previous goes back this many selections
    SEL_HISTORY_MAX = 10

    def _note_selection_history(self, indices) -> None:
        """Remember the selection before this one (Select Previous), by record: an edit
        that renumbers the records keeps it."""
        doc = self.doc
        if doc is None or self.__dict__.get("_sel_restoring"):
            return
        ui = getattr(doc, "ui", None)
        if ui is None:
            return
        objs = doc.parsed.page.objects if doc.parsed is not None else []
        recs = tuple(objs[i] for i in indices if 0 <= i < len(objs))
        last = getattr(ui, "sel_now", ())
        if [id(r) for r in recs] == [id(r) for r in last]:
            return
        hist = getattr(ui, "sel_history", None)
        if hist is None:
            hist = ui.sel_history = []
        if last:
            hist.append(last)
            del hist[: -self.SEL_HISTORY_MAX]
        ui.sel_now = recs

    def _on_canvas_selection_noted(self, roots: list) -> None:
        """A click on the canvas changed the selection (Select Previous remembers it)."""
        self._note_selection_history(roots)

    def select_previous(self) -> None:
        """Edit > Select > Select Previous: the selection before the last change (ten are
        kept per page)."""
        doc = self.doc
        ui = getattr(doc, "ui", None) if doc is not None else None
        hist = getattr(ui, "sel_history", None) if ui is not None else None
        objs = doc.parsed.page.objects if doc is not None and doc.parsed is not None else []
        pos = {id(po): k for k, po in enumerate(objs)}
        while hist:
            recs = hist.pop()
            idx = [pos[id(r)] for r in recs if id(r) in pos]
            if not idx:
                continue  # (those objects are gone)
            self._sel_restoring = True
            try:
                self._set_selection(idx, reveal=True)
            finally:
                self._sel_restoring = False
            ui.sel_now = tuple(objs[i] for i in idx)
            self.statusBar().showMessage(
                "Selected the %s selected before." % (plural(len(idx), "object")), 4000
            )
            return
        self.statusBar().showMessage("No earlier selection on this page.", 4000)

    # ------------------------------------------------------------- edit intents
    def _on_objects_moved(self, indices: list, dx: int, dy: int) -> None:
        self.commit_nudge()
        if self.doc is None:
            return
        dragged = list(indices)  # the canvas moved these items: rebuild them
        indices = self._filter_template(indices, "move")
        if not indices:
            self._refresh(units=dragged)  # snap items back to the model
            return
        try:
            self.doc.move_objects(indices, dx, dy)
        except TransformError as e:
            self.statusBar().showMessage(str(e), 8000)
            self._refresh(units=dragged)  # snap items back to the model
            return
        # move = geometry-only; tree unchanged
        self._refresh(rebuild_tree=False, units=dragged)

    def _on_object_resized(self, index: int, rect: tuple) -> None:
        self.commit_nudge()
        if self.doc is None:
            return
        if not self._filter_template([index], "resize"):
            self._refresh(units=[index])
            return
        self._resize_unit(index, rect, getattr(self.view, "last_resize_origin", None))
        # resize = geometry-only; tree unchanged; the dragged items always re-sync
        self._refresh(rebuild_tree=False, units=[index])
