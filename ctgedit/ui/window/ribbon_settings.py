"""Settings > Ribbon applied to the window."""

from __future__ import annotations

from ..messages import warn

from ..main_window_base import (
    ElementDocument,
    contextlib,
    doc_key,
)

from ...infra.logsetup import note_exception as _note_exc


from ..main_window_base import (
    QtCore,
    QtGui,
    app_settings,
    os,
)


class RibbonSettings:
    """The ribbon groups and the own group of Settings > Ribbon."""

    # ------------------------------------------------------------- Settings > Ribbon
    CUSTOM_GROUP = "My Commands"

    def apply_ribbon_layout(self) -> None:
        """Settings > Ribbon: hide the groups the user turned off (ui/ribbon_hidden_groups,
        'Tab/Group' names) and build the user's own group on the Design tab from the
        command names in ui/ribbon_custom (the menu commands by their label)."""
        rb = getattr(self, "ribbon", None)
        if rb is None:
            return
        st = app_settings()
        hidden = {str(x) for x in (st.value("ui/ribbon_hidden_groups", [], type=list) or [])}
        for title, caption in rb.group_names():
            if caption == self.CUSTOM_GROUP:
                continue
            rb.set_group_hidden(title, caption, "%s/%s" % (title, caption) in hidden)
        wanted = [str(x) for x in (st.value("ui/ribbon_custom", [], type=list) or [])]
        old = getattr(self, "_custom_group", None)
        if old is not None:
            rb.remove_group(old)
            self._custom_group = None
        if not wanted:
            return
        from ..prefs import keymap

        page = rb.page_of("Design")
        if page is None:
            return
        g = rb.add_group(page, self.CUSTOM_GROUP, priority=0)
        n = 0
        for label in wanted:
            a = keymap.find_action(self, label)
            if a is None:
                continue
            g.add_small(a)
            n += 1
        if n == 0:
            rb.remove_group(g)
            return
        self._custom_group = g
        rb._reflow()

    @staticmethod
    def ribbon_command_labels(window) -> list:
        """The command names Settings > Ribbon offers for the user's group: every menu
        command, sorted, without duplicates."""
        from ..dialog.palette import harvest_actions

        seen = set()
        out = []
        for _a, _crumb, label in harvest_actions(window.menuBar()):
            if label and label not in seen:
                seen.add(label)
                out.append(label)
        return sorted(out, key=str.lower)

    def palette_context_actions(self) -> list:
        """The command palette's extra rows: the selection's context-menu commands no menu
        has, as (action, breadcrumb, label). The actions live on the window and are made
        anew for each palette (they act on the selection of that moment)."""
        out = []
        if self.doc is None or not self._sel:
            return out
        roots = {self.doc.root_of(i) for i in self._sel}
        if len(roots) != 1:
            return out
        i = self._sel[0]
        holder = self.__dict__.setdefault("_palette_ctx", [])
        for a in holder:
            a.deleteLater()
        holder.clear()

        doc, n = self.doc, len(self.doc.page.objects)

        def run(fn):
            # (made for this page as it was: another page shown, or an edit since that
            # changed its records, and the row's object is not that one any more)
            if self.doc is not doc or len(self.doc.page.objects) != n:
                self.statusBar().showMessage("The page changed since the search opened. Search again.", 5000)
                return
            fn()

        def make(label, fn):
            a = QtGui.QAction(label, self)
            a.triggered.connect(lambda _=False: run(fn))
            holder.append(a)
            out.append((a, "Selection", label))

        defn = self._definition_of(i)
        if defn is not None:
            make(
                "Edit Definition (%s.%s)" % (os.path.splitext(os.path.basename(defn[0]))[0], defn[1]),
                lambda d=defn: self._open_element(*d),
            )
        comp = self._composite_root(i)
        if comp is not None:
            make("Edit Composite Genie…", lambda r=comp: self._edit_genie(r, self.doc.page.objects[r]))
            make("Update Composite Genie", lambda r=comp: self._update_composite(r))
        return out

    def _context_menu(self, global_pos: QtCore.QPoint, indices: list) -> None:
        """The canvas's right-click menu: its sections (window/context_menu.py)."""
        from . import context_menu

        context_menu.show_canvas_menu(self, global_pos, indices)

    def _simulate_page(self) -> None:
        """Tools > Simulate Page: the page as it is now (unsaved edits too) in the Simulate
        window, read-only and live."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        from ..simulate.window import open_simulation

        path = self.doc.path or getattr(self.doc.page, "source_path", "") or ""
        # (said at once: a large page takes a second to draw before the window shows)
        note = "Opening the page in the Simulate window…"
        self.statusBar().showMessage(note)
        self.statusBar().repaint()
        from ..busy import wait_cursor

        try:
            with wait_cursor():
                # the records as they are now, derived again in the runtime's label mode (the
                # editor's literal labels '@(...)' / '="..."' are Graphics Builder text)
                open_simulation(
                    path,
                    page=self.doc.page,
                    parsed=getattr(self.doc, "parsed", None) if path else None,
                    doc=self.doc if path else None,
                    host=self,
                )
        except Exception as exc:  # noqa: BLE001 -- say why, keep the editor going
            warn(self, "Simulate Page", "The page could not be simulated. %s" % exc)
        finally:
            if self.statusBar().currentMessage() == note:
                self.statusBar().clearMessage()

    def _composite_root(self, index: int):
        """The placed composite genie ``index`` is (or lies in, or whose unit
        holds it as its root), or None."""
        if self.doc is None or not 0 <= index < len(self.doc.page.objects):
            return None
        pg = self.doc.page
        for k in [self.doc.root_of(index), index] + list(pg.ancestors(index)):
            if 0 <= k < len(pg.objects) and pg.objects[k].kind.value == "composite_genie":
                return k
        return None

    def _definition_of(self, index: int):
        """(library path, element) of the library genie / symbol a placed
        object (or its unit root) instantiates -- for the page's template
        objects, the template (.ctt) -- or None. Never a composite genie's
        composition cache (only Plant SCADA writes it)."""
        if self.doc is None:
            return None
        from ...format.symbols import reference_from_object

        objs = self.doc.parsed.page.objects
        root = self.doc.root_of(index)
        for i in (root, index):
            if not (0 <= i < len(objs)):
                continue
            try:
                ref = reference_from_object(objs[i])
            except Exception:  # noqa: BLE001
                _note_exc("shell._definition_of")
                ref = None
            if (
                ref is None
                or ref.ext not in ("ctl", "ctm", "ctt")
                or "_compositioncaches" in (ref.library or "").lower()
            ):
                continue
            try:
                path = self.doc.library_path(ref.library, ref.element, ref.ext)
            except Exception:  # noqa: BLE001
                _note_exc("shell._definition_of")
                path = None
            if path:
                return path, ref.element
        if self._is_template_obj(root) and not isinstance(self.doc, ElementDocument):
            with contextlib.suppress(Exception):
                return self.doc.template_element()
        return None

    def _owner_definition(self, index: int):
        """(library path, element) whose edit changes a read-only object."""
        if self.doc is None:
            return None
        from ...format.symbols import reference_from_object
        from ..objprops.registry import placed_genies_of

        objs = self.doc.parsed.page.objects
        for g in placed_genies_of(self.doc, index):
            try:
                ref = reference_from_object(objs[g])
                path = (
                    self.doc.library_path(ref.library, ref.element, ref.ext)
                    if ref is not None and ref.ext in ("ctl", "ctm")
                    else None
                )
            except Exception:  # noqa: BLE001
                _note_exc("shell._owner_definition")
                path = None
            if path and "_compositioncaches" in (ref.library or "").lower():
                # a composite genie: its composition cache is Plant SCADA's
                return None
            if path:
                return path, ref.element
        return self._definition_of(index)

    def _owner_definition_label(self, index: int) -> str:
        """'library.element' of :meth:`_owner_definition`, or ''."""
        d = self._owner_definition(index)
        return "%s.%s" % (os.path.splitext(os.path.basename(d[0]))[0], d[1]) if d else ""

    def _edit_owner_definition(self, index: int) -> None:
        d = self._owner_definition(index)
        if d is None:
            self.statusBar().showMessage(
                "The definition of this object is not in this project or the projects "
                "it includes, so it cannot be opened here.",
                6000,
            )
            return
        self._open_element(*d)

    def _read_only_reason(self, index: int) -> str:
        """Why object ``index``'s settings are shown read-only ('' = editable):
        a placed genie's member, or a template record while templates are
        locked (the Settings option, Break Lock Mode)."""
        if self.doc is None:
            return ""
        from ..objprops.registry import read_only_reason

        return read_only_reason(
            self.doc, index, template_locked=self._template_locked() and not self._break_lock
        )

    def page_properties(self) -> None:
        """CtDraw's Page Properties dialog (General/Appearance/Keyboard
        Commands/Events/Environment/Associations)."""
        self._show_target_tab()  # (from a tab's menu: that page)
        if self.doc is None:
            return
        from ...io.project import Project
        from ..dialog.page_props import PagePropertiesDialog

        # one window per document (tabs)
        if self._raise_modeless(self._modeless_for("pageprops")):
            return
        # a library element has no pages.DBF row (its properties / size /
        # colour live in the element itself)
        project = (
            None
            if isinstance(self.doc, ElementDocument)
            else Project(os.path.dirname(os.path.abspath(self.doc.path)))
        )
        dlg = PagePropertiesDialog(self.doc, project, self)
        # every Apply that changed the page refreshes the window at once (the
        # unsaved mark, Undo: its ``applied`` signal, _show_modeless); the
        # pages.DBF / keyboard edits are staged in the document and written
        # by the next Save
        self._show_modeless(dlg, ("pageprops", doc_key(self.doc)), scope="page", title="Page Properties")
