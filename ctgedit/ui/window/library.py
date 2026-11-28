"""Library, colour, bitmap, composite and super genie commands."""

from __future__ import annotations


from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    contextlib,
    os,
    paste_linked,
    set_paste_linked,
    ui_state,
)
from ..messages import inform, warn


class LibraryCommands:
    """MainWindow's cut link, library symbols, colour tools, the bitmap editor, composite and super
    genies, Copy to Library, special objects, legacy tools, Pack Libraries and variable tags."""

    # ----------------------------------------------------------- cut link
    def _cut_link(self) -> None:
        """Edit > Cut Link: placed library symbol -> local group; on the
        page's template (its objects selected, or nothing selected on a
        linked page) it cuts the page's link to the template."""
        if self.doc is None:
            return
        if not self._sel or all(self._is_template_obj(i) for i in self._sel):
            if self._page_has_template():
                self._cut_template_link()
            return
        from ...editing import LibraryLinkError

        roots = []
        for i in self._sel:
            r = self.doc.root_of(i) if i < len(self.doc.page.objects) else None
            if r is not None and r not in roots:
                roots.append(r)
        if len(roots) <= 1:
            try:
                new = self.doc.break_library_link(roots[0] if roots else self._sel[0])
            except LibraryLinkError as e:
                inform(self, "Cut Link", str(e))
                return
            if new is None:
                self.statusBar().showMessage(
                    "Cut Link works on a placed library symbol (or, with nothing selected, "
                    "on the page's template).",
                    6000,
                )
                return
            self._sel = [new]
            self._refresh()
            self.statusBar().showMessage(
                "The symbol is now a group of its own objects: it no longer follows its "
                "library (Undo restores the link).",
                6000,
            )
            return
        self._cut_links(roots)

    def _cut_links(self, roots: list) -> None:
        """Cut Link on several selected units at once: the link of every placed library
        symbol among them is cut (the highest index first, so the others keep their
        indices), as ONE undo step, behind a progress window with Cancel (the ones done
        stay cut). What could not be cut is said afterwards, with the reason."""
        from ...editing import LibraryLinkError
        from ..jobs.job_progress import JobProgress
        from ..names import plural

        order = sorted(roots, reverse=True)
        objs = self.doc.parsed.page.objects
        new: list = []
        refused: list = []
        skipped = 0
        stopped = False
        prog = JobProgress(self, "Cut Link", "Cutting the links…", phase="unlink", total=len(order))
        try:
            with self.doc.batch("Cut Link"):
                for k, r in enumerate(order):
                    link = self.doc.symbol_link(r)
                    what = link["reference"] if link else "object %d" % (r + 1)
                    if not prog.tick(
                        k, len(order), "Cutting the link of %s… (%d of %d)" % (what, k + 1, len(order))
                    ):
                        stopped = True
                        break
                    before = len(objs)
                    try:
                        idx = self.doc.break_library_link(r)
                    except LibraryLinkError as e:
                        refused.append(str(e))
                        continue
                    if idx is None:
                        skipped += 1
                        continue
                    # (the groups made before sit above r: the copies move them)
                    delta = len(objs) - before
                    new = [x + delta for x in new] + [idx]
        finally:
            prog.close()
        self._sel = sorted(new)
        self._refresh()
        self._update_actions()
        n = len(new)
        if n:
            text = "%s %s now %s of %s own objects: %s no longer %s %s library (Undo restores the links)" % (
                plural(n, "symbol"),
                "is" if n == 1 else "are",
                "a group" if n == 1 else "groups",
                "its" if n == 1 else "their",
                "it" if n == 1 else "they",
                "follows" if n == 1 else "follow",
                "its" if n == 1 else "their",
            )
        else:
            text = "No link was cut"
        if stopped:
            text += ". Stopped before the end"
        if skipped:
            text += ". %s not a placed library symbol" % ("1 was" if skipped == 1 else "%d were" % skipped)
        self.statusBar().showMessage(text, 8000)
        if refused:
            inform(
                self,
                "Cut Link",
                "%s could not be cut:\n%s"
                % ("1 link" if len(refused) == 1 else "%d links" % len(refused), "\n".join(refused[:10])),
            )

    # ------------------------------------------------- library symbol helpers
    def _library_dialog_hooks(self) -> dict:
        """The Copy To / Save As dialog's Edit and Delete buttons (CtDraw's
        library dialog Edit / Delete): open the element for editing / delete
        it (the usage warning and the confirmation first)."""
        # the projects of the open pages too: Save As of an element of Include
        # can go into the local project
        projects = []
        for w in type(self).instances or [self]:
            with contextlib.suppress(RuntimeError):
                for e in w._docs:
                    d = e["doc"]
                    if not isinstance(d, ElementDocument) and d.path:
                        projects.append(os.path.dirname(os.path.abspath(d.path)))
        return dict(
            on_edit=lambda path, el: QtCore.QTimer.singleShot(0, self, lambda: self._open_element(path, el)),
            on_delete=self._delete_library_element,
            projects=projects,
        )

    def _sync_paste_linked(self) -> None:
        """The Linked setting changed in a picker: the menu tick and the
        Library panel toggle follow it."""
        on = paste_linked()
        self.act_paste_linked.setChecked(on)
        with contextlib.suppress(Exception):
            self.library_dock.sync_linked()

    def _toggle_paste_linked(self, on: bool) -> None:
        """Edit > Library > Paste Symbols Linked: whether a placed symbol stays
        linked to its library (CtDraw's Linked check box of the Paste Symbol
        dialog: PasteLibrary_Linked / PasteLibrary_Unlinked)."""
        set_paste_linked(bool(on))
        with contextlib.suppress(Exception):
            self.library_dock.sync_linked()
        self.statusBar().showMessage(
            "Symbols are placed %s"
            % (
                "linked to their library"
                if on
                else "as unlinked copies (they no longer follow their library)"
            ),
            5000,
        )

    def _compose_nested_again(self) -> None:
        """Edit > Library > Compose Nested Genies Again."""
        doc = self.doc
        if not isinstance(doc, ElementDocument) or doc.is_new or getattr(doc, "read_only", False):
            return
        from ..notify import notify
        from ...format import genie_place as GP

        if not GP.nested_keys(doc.parsed.page.objects):
            notify(
                self, "No genie is nested in %s, so there is nothing to compose." % doc.display_name, "info"
            )
            return
        report: dict = {}
        self._flush_text_edit()  # (an in-place edit belongs to the old records)
        top = (getattr(doc, "_undo", None) or [None])[-1]
        try:
            n = doc.update_nested_genies(label="compose nested genies again", every=True, report=report)
        except Exception as e:  # noqa: BLE001 -- a library that cannot be read: said
            from ..messages import error_box

            error_box(
                self,
                "Compose Nested Genies Again",
                "The genies nested in %s could not be composed again." % doc.display_name,
                exc=e,
            )
            return
        if (getattr(doc, "_undo", None) or [None])[-1] is not top:
            # the tab shows the new records as after any edit: the canvas, the Objects tree,
            # the object windows, the title's unsaved mark and Edit > Undo. An automatic
            # update of its nested genies that has not landed would only compose them again:
            # it goes, and the tab does not compose them again until a file they are made of
            # changes
            jobs = self.__dict__.get("_genie_jobs")
            if jobs is not None:
                with contextlib.suppress(Exception):
                    if jobs.cancel_automatic(doc, templates=False):
                        self._genies_updated(doc)
                        self._clear_auto_update_line(doc, templates=False)
            from .files import NESTED_DONE

            with contextlib.suppress(Exception):
                ui_state(doc).genie_sources_done[NESTED_DONE] = doc.nested_genie_sources()
            self._sync_modeless("replace")
            self._refresh()
            self._update_actions()
            self._update_title()
        failed = sorted("%s.%s" % key for key in report.get("stale_nested", ()))
        if failed:
            # a copy that could not be composed stays as stored: said, never 'matches'
            names = ", ".join(failed)
            notify(
                self,
                (
                    "Composed the genies nested in %s again. Save to keep it. %s could "
                    "not be composed and %s as stored."
                    if n
                    else "The genies nested in %s were not composed again. %s could not be "
                    "composed and %s as stored."
                )
                % (doc.display_name, names, "stays" if len(failed) == 1 else "stay"),
                "warning",
                detail=doc.path,
            )
        elif n:
            notify(
                self,
                "Composed the genies nested in %s again from their libraries. Save "
                "to keep it." % doc.display_name,
                "success",
            )
        else:
            notify(self, "Every genie nested in %s already matches its library." % doc.display_name, "info")

    def _hotspot_dialog(self) -> None:
        """Edit > Library > Hot Spot Location (as in CtDraw: X / Y):
        where the element's hot spot -- the point a paste lands on -- sits."""
        doc = self.doc
        if not isinstance(doc, ElementDocument) or not doc.can_set_hotspot:
            self.statusBar().showMessage(
                "The hot spot belongs to a symbol or genie: open one for editing first.", 5000
            )
            return
        from ..dialog.library_dialogs import HotSpotDialog

        cur = doc.hotspot
        box = (
            doc.selection_extent([i for i, o in enumerate(doc.page.objects) if o.parent is None])
            if doc.page is not None
            else None
        )
        if cur is None:
            cur = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2) if box else (0, 0)
        dlg = HotSpotDialog(cur[0], cur[1], self, extent=box)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self._on_hotspot_moved(*dlg.values())

    def _on_hotspot_moved(self, x: int, y: int) -> None:
        doc = self.doc
        if not isinstance(doc, ElementDocument):
            return
        if not doc.can_set_hotspot:
            self._refresh(rebuild_tree=False)  # the marker goes back
            self.statusBar().showMessage(
                "A template or super genie has no hot spot to move: its reference point "
                "is its page's top-left corner.",
                6000,
            )
            return
        try:
            moved = doc.set_hotspot(int(x), int(y))
        except ValueError as e:
            self._refresh(rebuild_tree=False)
            warn(self, "Hot Spot Location", str(e))
            return
        self._refresh(rebuild_tree=False)
        self._update_title()
        if moved:
            self.statusBar().showMessage(
                "Hot spot at %d, %d. Save the element, then run Update Pages to move the "
                "copies on pages." % (int(x), int(y)),
                7000,
            )

    # --------------------------------------------------------- variable tags
    def _variable_tags(self) -> None:
        """Tools > Tags Panel (Ctrl+T; the Graphics Builder's Variable Tags): show and
        raise the Tags panel (it shows its own loading and empty state)."""
        self.dock_tags.show()
        self.dock_tags.raise_()
        self.tags_dock.focus_search()
