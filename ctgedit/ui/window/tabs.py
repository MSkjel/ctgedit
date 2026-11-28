"""Document tabs, after-open work, files changed on disk and crash recovery."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc


from ...infra.paths import path_key

from ..prefs.settings import option

from ..main_window_base import main_window_class

from ..main_window_base import (
    EditableDocument,
    ElementDocument,
    Page,
    PageScene,
    QtCore,
    QtGui,
    QtWidgets,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)


class PageNotShown(Exception):
    """:meth:`main_window_class().open_shown` could not show the page (the reason in its text)."""


class DocumentTabs:
    """MainWindow's document tabs, what happens after a page opens, the fast-display check, files
    changed on disk and crash recovery."""

    # ------------------------------------------------------------- document tabs
    _KIND_LABEL = {"symbol": "Symbol", "genie": "Genie", "supergenie": "Super Genie", "template": "Template"}

    def _doc_name(self, doc) -> str:
        """A document's name in its tab and the window title: the page name, or
        'Genie pumps.pump1 (Example)' for a library element."""
        if isinstance(doc, ElementDocument):
            project = os.path.basename(os.path.dirname(os.path.abspath(doc.path)))
            name = "%s %s (%s)" % (
                self._KIND_LABEL.get(doc.kind, doc.kind.title()),
                doc.display_name,
                project,
            )
        else:
            name = os.path.splitext(os.path.basename(doc.path))[0]
        if getattr(doc, "read_only", False):
            name += " [read-only]"
        return name

    def _tab_title(self, doc) -> str:
        """The name a tab shows: the page name, or the element's name for a library element
        (its kind is the tab's icon; the full name is in the tool tip). The unsaved dot and
        the read-only lock are the tab's own marks (doc_tabs.DocTabBar)."""
        v = getattr(ui_state(doc), "version_view", None)
        if isinstance(doc, ElementDocument):
            # (an earlier version names its save: it is not the element's own tab)
            return "%s (%s)" % (doc.element_name, v.when[:16]) if v is not None else doc.element_name
        el = getattr(doc, "element", "")
        if isinstance(doc, PendingPage) and el:
            return el
        name = os.path.splitext(os.path.basename(doc.path))[0]
        # an earlier version shown read-only (File > Page History: ui/dialog/history_versions)
        return "%s (%s)" % (name, v.when[:16]) if v is not None else name

    _EXT_KIND = {".ctt": "template", ".ctl": "symbol", ".ctm": "genie"}

    @classmethod
    def _doc_kind(cls, doc) -> str:
        """'page' | 'template' | 'symbol' | 'genie' | 'supergenie' (the tab's icon)."""
        if isinstance(doc, ElementDocument):
            return doc.kind if doc.kind in ("template", "symbol", "genie", "supergenie") else "page"
        if getattr(doc, "element", ""):
            return cls._EXT_KIND.get(os.path.splitext(doc.path)[1].lower(), "page")
        return "page"

    @staticmethod
    def _doc_project(doc) -> str:
        return os.path.basename(os.path.dirname(os.path.abspath(doc.path)))

    def _refresh_tabs(self) -> None:
        """Every tab's name, icon, marks and tool tip; the project after the name of the
        tabs whose name another open tab has too."""
        docs = getattr(self, "_docs", None)
        if docs is None:
            return
        # (every refresh asks -- each click on the canvas: a tab's name, kind, project and
        # tool tip are worked out once per document and path; a tab whose state is the same
        # costs a compare; the close buttons are set once at the end, not once a tab)
        texts = [self._tab_texts(e) for e in docs]
        seen = {}
        for t in texts:
            seen[t[0].lower()] = seen.get(t[0].lower(), 0) + 1
        bar = self.doc_tabs
        for k, e in enumerate(docs):
            d = e["doc"]
            if k >= bar.count():
                break
            title, kind, project, tip = texts[k]
            read_only = bool(getattr(d, "read_only", False))
            if d.dirty:
                tip += "\nUnsaved changes."
            if read_only:
                tip += "\nRead-only. Save asks where to save a copy."
            bar.set_tab_state(
                k,
                title,
                kind=kind,
                dirty=bool(d.dirty),
                pinned=bool(e.get("pinned")),
                read_only=read_only,
                project=project,
                show_project=seen[title.lower()] > 1,
                tip=tip,
                sync=False,
            )
        bar.sync_buttons()
        from ..panels import status_items

        status_items.refresh_doc_state(self)  # (saved, unsaved changes, read-only)

    def _tab_texts(self, entry: dict) -> tuple:
        """(name, kind, project, tool tip without the unsaved / read-only lines) of a tab,
        kept with its entry while its document and path stay."""
        d = entry["doc"]
        key = (id(d), d.path, getattr(d, "element_name", None) or getattr(d, "element", ""))
        c = entry.get("tab_texts")
        if c is None or c[0] != key:
            c = entry["tab_texts"] = (
                key,
                (self._tab_title(d), self._doc_kind(d), self._doc_project(d), self._doc_tooltip(d, False)),
            )
        return c[1]

    @classmethod
    def _doc_tooltip(cls, doc, marks: bool = True) -> str:
        """A tab's tool tip: its name, project and path (``marks``: and the unsaved and
        read-only lines)."""
        if isinstance(doc, ElementDocument):
            tip = "%s %s (%s)\n%s" % (
                cls._KIND_LABEL.get(doc.kind, doc.kind.title()),
                doc.display_name,
                cls._doc_project(doc),
                doc.path,
            )
        elif getattr(doc, "element", ""):
            tip = "%s.%s\n%s" % (os.path.splitext(os.path.basename(doc.path))[0], doc.element, doc.path)
        else:
            tip = "%s (%s)\n%s" % (
                os.path.splitext(os.path.basename(doc.path))[0],
                cls._doc_project(doc),
                doc.path,
            )
        if marks and doc.dirty:
            tip += "\nUnsaved changes."
        if marks and getattr(doc, "read_only", False):
            tip += "\nRead-only. Save asks where to save a copy."
        return tip

    def _stash_current(self) -> None:
        """Remember the active tab's editor state before switching away."""
        # a glide still running ends first: the tab keeps the view it was going to (and
        # no glide is left to finish onto the next page's scene)
        self.view.finish_view_animation()
        k = self.doc_tabs.currentIndex()
        if self.doc is None or not (0 <= k < len(self._docs)) or self._docs[k]["doc"] is not self.doc:
            return
        e = self._docs[k]
        e["sel"] = list(self._sel)
        e["sel_note"] = self._selection_note()  # (followed when shown again)
        e["hidden"] = set(self._hidden_roots)
        e["locked"] = set(self._locked)
        self._stash_view(e)

    def _stash_view(self, e: dict) -> None:
        """The view's zoom and position into entry ``e`` (its page is shown)."""
        e["transform"] = QtGui.QTransform(self.view.transform())
        e["scroll"] = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
        vp = self.view.viewport()
        c = self.view.mapToScene(QtCore.QPoint(vp.width() // 2, vp.height() // 2))
        e["view"] = (self.view.zoom(), (c.x(), c.y()))

    @staticmethod
    def _libraries_being_saved() -> bool:
        """A run saves library genies one after another now (the chain after a library save,
        Update Users, Update Pages' chain: UpdatePagesRun): a tab shown meanwhile does not
        compose its genies against libraries it has only part written."""
        from ..jobs.update_run import UpdatePagesRun

        return bool(UpdatePagesRun.users_busy or UpdatePagesRun.chain_busy)

    @staticmethod
    def _tabs_wait_for_runs() -> bool:
        """Settings > Pages and libraries > When a page opens: 'Open tabs wait for the update
        of what uses a saved genie' (opt/tabs_wait_for_runs, off by default: what is open
        comes first)."""
        return option("tabs_wait_for_runs")

    @classmethod
    def _runs_hold_tabs(cls) -> bool:
        """Do the open tabs wait for a run that saves library genies?"""
        return cls._libraries_being_saved() and cls._tabs_wait_for_runs()

    def _activate_doc(self, entry: dict) -> None:
        self._flush_text_edit()  # it belongs to the page we leave
        # selected nodes belong to the page we leave (the same index on this
        # page is another object)
        self.view.clear_node_selection()
        if isinstance(entry["doc"], PendingPage):
            # a restored session's tab shown for the first time: open it now
            if not self._materialise(entry):
                return
            entry["post_open"] = not isinstance(entry["doc"], ElementDocument)
        # Ctrl+Tab goes back through the tabs in the order they were shown
        mru = self.__dict__.setdefault("_mru_entries", [])
        mru[:] = [e for e in mru if e is not entry]
        mru.insert(0, entry)
        self._cap_arm = None  # (a capped first fit belongs to its page)
        left = self.doc
        if left is not None and left is not entry["doc"]:
            # the object and page windows of the page we leave wait for its
            # tab; the selection and window windows stay
            self._hide_page_windows(left)
            # its Objects tree rows wait for it too (shown again as they are)
            le = next((e for e in self._docs if e["doc"] is left), None)
            shown = getattr(self.objects_dock, "shown", None)
            if le is not None and shown and shown[0] is left:
                with contextlib.suppress(Exception):
                    le["rows"] = self.objects_dock.detach_rows()
        self.doc = entry["doc"]
        # (its genie update, queued behind other tabs', goes first: what the user looks at
        # comes first)
        jobs = self.__dict__.get("_genie_jobs")
        if callable(getattr(jobs, "promote", None)):
            with contextlib.suppress(Exception):
                jobs.promote(self.doc)
        self._prune_orphan_windows()
        # windows of this page follow what changed while it was not shown
        with contextlib.suppress(Exception):
            self._sync_modeless(self._pop_modeless_reason(self.doc))
        # (an automatic update that waited for the user to finish, files._wait_until_idle,
        # is made now: the user has chosen this tab. One that waits for the page's steps to
        # redo goes on waiting: landing would clear them)
        waited = self._take_idle_wait(self.doc, show=True)
        if entry.pop("stale", False) or (waited is not None and waited[0]):
            # a library element it uses was saved in another tab: drawn again from the
            # library files, its records as they are (no edit: the Simulate window does not
            # reload the page for it, libwatch.redraw_from_libraries)
            from .libwatch import redraw_from_libraries

            with contextlib.suppress(Exception):
                redraw_from_libraries(self.doc)
        if waited is not None and waited[1]:
            if self._runs_hold_tabs():
                # (a run saves library elements now: the template step waits for its end,
                # as the symbol and genie steps do)
                self._wait_until_idle(self.doc, templates=waited[1], since=waited[2])
            else:
                # (the chain's template step that waited, before its symbols and genies;
                # this show draws it)
                self._templates_that_waited(self.doc, waited[1], hold=True)
        # CtDraw re-reads a page's genies when its window is activated: a genie (or a
        # genie nested in one) saved since this tab was last shown is composed again
        gen = editing_mod.LIBRARY_GENERATION[0]
        first_show = "genie_gen" not in entry
        if "genie_gen" not in entry:
            entry["genie_gen"] = gen  # just opened: the open checks do it
            if isinstance(self.doc, ElementDocument):
                # (an element has no open checks: its first show re-derives the library
                # symbols placed in it (Update changed symbols) and composes the genies
                # nested in it that changed since it was saved (Update changed genies), as
                # CtDraw's element load; while a run saves library genies, at its end)
                self._bring_up_to_date(self.doc, first=True, gate=False)
        elif (entry["genie_gen"] != gen or waited is not None) and (
            option("auto_update_genies") or option("auto_update_symbols")
        ):
            if self._runs_hold_tabs():
                # (while a run saves library genies one after another the tab waits: the
                # run's end brings every tab up to date, _after_chain_saves /
                # _follow_run_saves, and so does the end of the wait, _after_library_run)
                self._after_library_run(self.doc)
            else:
                if option("auto_update_genies"):
                    entry["genie_gen"] = gen  # (genies off: looked at when turned on)
                # (its placed symbols, then its genies; the genie update lands by itself
                # when it is done: _genie_job_finished. Only the page's steps to redo hold
                # the symbol step here, as they hold a genie update's answer)
                self._bring_up_to_date(self.doc, first=True, gate="redo")
        self._auto_bar.show_for(self.doc)
        toast = self.__dict__.get("_ctg_toast")
        if toast is not None:
            toast.sync_actions()  # (the toast of the page left goes)
        # (its selection where its objects are now: an automatic update while it was not
        # shown, or at this show, may have moved them; view._followed_selection)
        self._sel = self._followed_selection(entry.get("sel", []), entry.get("sel_note"))
        # (a page shown in a new tab hides again what was hidden on it, by AN: view.py)
        self._hidden_roots = set(entry["hidden"]) if "hidden" in entry else self._saved_hidden(self.doc)
        self._locked = set(entry.get("locked", set()))
        self._tpl_cache = None
        # this page's guidelines (editor-only, stored per page in the settings)
        self.view.set_guidelines(*self._load_guidelines(self.doc))
        self._use_tab_scene(entry)
        rows = entry.pop("rows", None)
        if rows is not None and rows[5] and rows[5][0] is self.doc:
            with contextlib.suppress(Exception):
                self.objects_dock.attach_rows(rows, self._sel, self._hidden_roots)
        self._refresh()
        tr = entry.get("transform")
        if tr is None:
            # first shown: where it was left, at 100 %, or fitted (at most the largest
            # opening zoom of Settings > Canvas)
            self._first_view(entry)
        else:
            # the zoom and scroll position the tab was left at (the view's
            # slack is set up first: a position in the surround is kept)
            self.view.restore_view(tr, entry.get("scroll", (0, 0)))
        proj_dir = os.path.dirname(os.path.abspath(self.doc.path))
        self.library_dock.set_project_dir(proj_dir)
        self.tags_dock.set_project_dir(proj_dir)
        # the library folders of the pages open now are watched, and compared with what they
        # held (a library saved by another program: window/libwatch.py)
        with contextlib.suppress(Exception):
            self._sync_library_watch()
            self._library_watch_rescan()
        from ..colour_picker import set_context_dir

        set_context_dir(proj_dir)  # the colour pickers show this project's palette
        self._rebuild_labels_menu()  # this project's languages
        self._update_actions()
        # this page's windows come back; the ones that read the page shown
        # (Goto Object, Guidelines) read this one
        self._show_page_windows(self.doc)
        self._schedule_window_sync()
        self._sync_guidelines_dialog()
        with contextlib.suppress(Exception):
            self._show_sa_report()  # the Workspace Checks panel follows the tab
        with contextlib.suppress(Exception):
            self._show_page_report()  # the Page Checks panel too
        if (first_show or not self._diag_report_current(self.doc)) and self._defer_post_open():
            # the page's report is made again (it reads every record): its chips and the
            # switch's log line right after the page painted
            self._hide_diag_chips()
            QtCore.QTimer.singleShot(0, self, self._diag_after_paint(self.doc))
        else:
            self._log_switch(self.doc)
            self._update_diag_chips()
        if entry.pop("post_open", False):
            # a restored tab just opened: its message and open checks, as
            # open_document's
            choice = entry.pop("choice", "edit")
            self._opened_message(self.doc, self.doc.path, choice)
            self._opened(self.doc, self.doc.path, choice)

    #: the tabs that keep their scene (the most recently shown): switching back to one
    #: shows its items as they were; the others build theirs again when shown
    TAB_SCENES = 3

    def _use_tab_scene(self, entry: dict) -> None:
        """Show ``entry``'s own scene (made when it is first shown); keep the scenes of the
        TAB_SCENES tabs shown last."""
        sc = entry.get("scene")
        if sc is None:
            in_use = any(e.get("scene") is self.scene for e in self._docs if e is not entry)
            sc = entry["scene"] = self.scene if not in_use else PageScene(self)
            if sc is not self.scene and hasattr(self, "apply_canvas_look"):
                self.apply_canvas_look(sc)  # (Outside the Page, the grid's look)
        if sc is not self.scene:
            self.scene = sc
            self.view.setScene(sc)
        order = self.__dict__.setdefault("_scene_order", [])
        if entry in order:
            order.remove(entry)
        order.insert(0, entry)
        for old in order[self.TAB_SCENES :]:
            self._drop_tab_scene(old)
        del order[self.TAB_SCENES :]

    def _drop_tab_scene(self, entry: dict) -> None:
        entry.pop("rows", None)  # (its kept Objects tree rows too)
        sc = entry.pop("scene", None)
        order = self.__dict__.get("_scene_order", [])
        if entry in order:
            order.remove(entry)
        if sc is None or sc is self.scene:
            if sc is not None:
                entry["scene"] = sc  # (the scene shown stays with its tab)
            return
        with contextlib.suppress(RuntimeError):
            if hasattr(sc, "clear_page"):
                sc.clear_page()  # (nothing of the page until deleteLater runs)
            else:
                sc.clear()
            sc.deleteLater()

    def _materialise(self, entry: dict, failures: list | None = None) -> bool:
        """Open a restored placeholder tab for real."""
        pend = entry["doc"]
        element = getattr(pend, "element", "")
        try:
            if element:
                doc = ElementDocument.open_element(pend.path, element)
            else:
                doc = self._open_page_file(pend.path, checks=True)
        except Exception as e:  # noqa: BLE001 -- reported
            _note_exc("tabs._materialise")
            self._drop_pending_tab(entry, activate=failures is None)
            if failures is not None:
                failures.append((pend.path, e))
            else:
                self.report_open_failures([(pend.path, e)])
            return False
        choice = "edit" if element else self._supergenie_guard(doc, ask=False)
        if choice == "view":
            doc.read_only = True
            doc.ui.supergenie_view = True  # Revert / reload keep it read-only
        entry["doc"] = doc
        if element:
            self._refresh_tabs()  # (its kind is known now)
            # its first show frames its objects, as an element opened from the library does
            # (files._open_element), unless it comes back where it was left
            entry["frame"] = True
        entry["choice"] = choice
        entry.pop("stale", None)  # read just now
        self._watch(doc.path)
        return True

    def _drop_pending_tab(self, entry: dict, activate: bool = True) -> None:
        """A placeholder tab whose page did not open: remove it; show the page
        shown before (still ``self.doc``), else the last tab, else nothing
        (``activate`` False: the caller chooses)."""
        k = next((i for i, e in enumerate(self._docs) if e is entry), None)
        if k is None:
            return
        self._switching = True
        try:
            del self._docs[k]
            self.doc_tabs.removeTab(k)
        finally:
            self._switching = False
        self._forget_entry(entry)
        self._refresh_tabs()
        if not self._docs:
            self.doc_tabs.setVisible(False)
        if not activate:
            return
        back = next((e for e in self._docs if e["doc"] is self.doc), None) if self.doc is not None else None
        if back is None and self._docs:
            back = self._docs[-1]
        if back is not None:
            self._switching = True
            try:
                self.doc_tabs.setCurrentIndex(self._entry_index(back))
            finally:
                self._switching = False
            if back["doc"] is not self.doc:
                self._activate_doc(back)
        elif not self._docs:
            self.doc_tabs.setVisible(False)
            self._update_actions()

    def _entry_index(self, entry: dict) -> int:
        """The tab index of ``entry`` (by identity: list.index would compare
        the documents field by field)."""
        return next(i for i, e in enumerate(self._docs) if e is entry)

    def _add_pending_tab(self, path: str, element: str = "", pinned: bool = False, view=None) -> dict:
        """A tab for a session page (or library ``element``) that opens when first shown;
        ``view`` = (transform, scroll) it was left at."""
        pend = PendingPage(path)
        pend.element = element or ""
        entry = {"doc": pend}
        if pinned:
            entry["pinned"] = True
        if view is not None:
            entry["session_view"] = view
        self._docs.append(entry)
        self._watch(path)  # as an opened tab (a recovery copy may take the tab over)
        main_window_class()._session_seen.add(path_key(path))
        self._switching = True
        try:
            self.doc_tabs.addTab(self._tab_title(pend))
        finally:
            self._switching = False
        self._refresh_tabs()
        self.doc_tabs.setVisible(True)
        return entry

    def set_document(self, doc: EditableDocument) -> None:
        """Show ``doc`` -- in its tab when it is already open, else in a new
        one."""
        for k, e in enumerate(self._docs):
            if e["doc"] is doc:
                if k != self.doc_tabs.currentIndex():
                    self.doc_tabs.setCurrentIndex(k)
                else:
                    self._activate_doc(e)
                return
        self._stash_current()
        entry = {"doc": doc}
        # Settings > Appearance > Document tabs 'Open a new tab:': at the end, or next to the
        # current tab (after the pinned tabs: they stay first)
        at = len(self._docs)
        from ..prefs.window_options import new_tab_place

        if self._docs and new_tab_place() == "next":
            cur = self.doc_tabs.currentIndex()
            pinned = sum(1 for e in self._docs if e.get("pinned"))
            at = min(len(self._docs), max(cur + 1, pinned))
        self._docs.insert(at, entry)
        self._watch(doc.path)
        # (an element tab is listed as its library file: closing it must take
        # that entry out of the session too)
        main_window_class()._session_seen.add(path_key(doc.path))
        self._start_page_off = False  # (Settings: 'Open nothing' on start, until now)
        self._switching = True
        try:
            k = self.doc_tabs.insertTab(at, self._tab_title(doc))
        finally:
            self._switching = False
        self._refresh_tabs()
        self.doc_tabs.setVisible(True)
        self.doc_tabs.setCurrentIndex(k)
        if self.doc is not doc:  # currentChanged did not fire
            self._activate_doc(entry)

    def _on_doc_tab(self, k: int) -> None:
        if getattr(self, "_switching", False) or not (0 <= k < len(self._docs)):
            return
        if self._docs[k]["doc"] is self.doc:
            return
        # stash the tab we are leaving (it is the one whose doc is current), at the view
        # a glide still running was going to
        self.view.finish_view_animation()
        for e in self._docs:
            if e["doc"] is self.doc:
                e["sel"] = list(self._sel)
                e["sel_note"] = self._selection_note()
                e["hidden"] = set(self._hidden_roots)
                e["locked"] = set(self._locked)
                self._stash_view(e)
        self._activate_doc(self._docs[k])

    def _on_doc_tab_moved(self, frm: int, to: int) -> None:
        self._docs.insert(to, self._docs.pop(frm))
        if (
            not getattr(self, "_switching", False)
            and QtWidgets.QApplication.mouseButtons() == QtCore.Qt.NoButton
        ):
            # (moved by a command; a drag is checked when it ends: moveFinished)
            QtCore.QTimer.singleShot(0, self, self._enforce_pin_order)

    def _ask_to_close(self, k: int) -> bool:
        """Tab ``k`` is about to close: when it has unsaved edits (or typed values of its
        windows not applied yet) it is shown and Save / Don't Save / Cancel is asked; Save
        saves it. False: cancelled, or the save failed (it stays)."""
        if not (0 <= k < len(self._docs)):
            return False
        if self._docs[k]["doc"] is self.doc:
            self._flush_text_edit()
        doc = self._docs[k]["doc"]
        # typed values of its windows that are not applied yet are part of
        # the save question
        pending = self._pending_windows(doc)
        if not (doc.dirty or pending):
            return True
        self.doc_tabs.setCurrentIndex(k)
        r = self._ask_save_on_close(doc, pending, "closing it")
        if r == QtWidgets.QMessageBox.Cancel:
            return False
        if r == QtWidgets.QMessageBox.Save:
            if not self._apply_pending_windows(pending):
                self.statusBar().showMessage("Not closed: a window's values could not be applied.", 8000)
                return False
            if doc.dirty and not self.save():
                return False
        return True

    def _close_doc_tab(self, k: int, ask: bool = True, show=None) -> bool:
        """Close one page (asks to save its edits; ``ask`` False: the caller asked already,
        a Save Changes list). When it is the tab shown, tab entry ``show`` is shown next
        (None: its neighbour). False when cancelled."""
        if not (0 <= k < len(self._docs)):
            return False
        if self._docs[k]["doc"] is self.doc:
            self._flush_text_edit()
        if ask and not self._ask_to_close(k):
            return False
        doc = self._docs[k]["doc"]
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None:
            jobs.cancel(doc)
        closing = self._docs[k]
        # Reopen Closed Page brings it back where it was left; the next open of the page
        # too (Settings > Canvas: where I left it)
        self._push_closed(closing, k)
        self._remember_view(closing)
        self._switching = True
        try:
            del self._docs[k]
            self.doc_tabs.removeTab(k)
        finally:
            self._switching = False
        self._forget_entry(closing)
        self._drop_tab_scene(closing)
        # its object and page windows (and its Symbols Not Found list) go with it
        self._close_page_windows(doc)
        # its underlay's render stops, its pictures go (ui/editor/underlay.py)
        self._underlay_doc_closed(doc)
        self._log_close(doc)
        # saved or discarded: its recovery copy is not needed any more
        from ..recovery import AutosaveController

        AutosaveController.forget(doc)
        self._unwatch(doc.path)
        # its fast-display check process and deferred open checks go with it
        self._post_open_pending = [p for p in getattr(self, "_post_open_pending", []) if p[0] is not doc]
        with contextlib.suppress(Exception):
            if not isinstance(doc, PendingPage):
                doc.stop_ctf_precheck()
        self._after_tab_removed(k, show)
        return True

    def _forget_entry(self, entry: dict) -> None:
        """A tab went (closed or moved to another window): out of the Ctrl+Tab order."""
        mru = self.__dict__.get("_mru_entries")
        if mru:
            mru[:] = [e for e in mru if e is not entry]

    def _after_tab_removed(self, k: int, show=None) -> None:
        """Tab ``k`` went: when it was the tab shown, tab entry ``show`` (when given and still
        open) or else the tab now in its place (or the last) is shown, or the window is
        empty."""
        self._refresh_tabs()
        keep = (
            next((i for i, e in enumerate(self._docs) if e["doc"] is self.doc), None)
            if self.doc is not None
            else None
        )
        if keep is not None:
            # a tab in the background went: the page shown stays
            self._switching = True
            try:
                self.doc_tabs.setCurrentIndex(keep)
            finally:
                self._switching = False
            self._update_title()
            self._update_actions()
            return
        if self._docs:
            # the tab that was shown went: the one asked for, else its neighbour (Settings >
            # Appearance > Document tabs 'After closing a tab, show:', or the page used
            # before it: the first of the Ctrl+Tab order still open)
            n = next((i for i, e in enumerate(self._docs) if e is show), None) if show is not None else None
            if n is None:
                from ..prefs.window_options import after_close

                if after_close() == "last":
                    n = next(
                        (
                            i
                            for m in self.__dict__.get("_mru_entries", ())
                            for i, e in enumerate(self._docs)
                            if e is m
                        ),
                        None,
                    )
            if n is None:
                n = min(k, len(self._docs) - 1)
            # (shown once, here: not also by currentChanged)
            self._switching = True
            try:
                self.doc_tabs.setCurrentIndex(n)
            finally:
                self._switching = False
            # (a restored tab whose page no longer opens is dropped on the way:
            # _drop_pending_tab shows another one, or none is left)
            if n < len(self._docs):
                self._activate_doc(self._docs[n])
        if not self._docs:
            # (the compile results, Used By and Grid Setup stay)
            self._close_modeless(scopes=("object", "page", "selection"))
            self.doc = None
            self._sel = []
            self.doc_tabs.setVisible(False)
            self._page = None
            self.view.set_guidelines([], [])
            self.scene.set_page(Page(), grid_size=0)
            self.scene.set_element_mode(False)
            self.objects_dock.set_page(None, [])
            self.tags_dock.set_project_dir(None)  # (a tags read going on ends too)
            self.props.set_target(None, [])
            # (the selection label, the context tab and the zoom of the closed page go too)
            with contextlib.suppress(Exception):
                self._on_canvas_selection([])
            with contextlib.suppress(Exception):
                self.lbl_zoom.setText("")
            self._update_title()
            self._update_actions()
            self._update_diag_chips()

    def close_current_document(self) -> None:
        """File > Close Page (Ctrl+W): the tab the tab menu was opened on, else the current
        one; a pinned tab asks first."""
        e = self._target_entry()
        if e is not None:
            self._close_tab_asked(self._entry_index(e))

    def _close_tab_requested(self, k: int) -> None:
        """The tab's close button or a middle click."""
        self._close_tab_asked(k)

    def _close_tab_asked(self, k: int) -> bool:
        if not 0 <= k < len(self._docs):
            return False
        e = self._docs[k]
        pinned = bool(e.get("pinned"))
        if pinned:
            # (Settings > Appearance > Document tabs 'Ask before closing a pinned tab')
            from ..prefs.window_options import ask_unpin

            if ask_unpin() and not self._ask_unpin(self._tab_title(e["doc"])):
                return False
            e["pinned"] = False  # (Reopen Closed Page brings it back unpinned)
        ok = self._close_doc_tab(k)
        if not ok and pinned and any(e is d for d in self._docs):
            # the save question was cancelled: the tab stays, pinned as it was
            e["pinned"] = True
            self._enforce_pin_order()
            self._refresh_tabs()
        return ok

    def _ask_unpin(self, name: str) -> bool:
        """'Mixer is pinned. Unpin and close it?' (a seam: tests answer it)."""
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Question,
            "Pinned Tab",
            "%s is pinned. Unpin and close it?" % name,
            QtWidgets.QMessageBox.NoButton,
            self,
        )
        ok = box.addButton("Unpin and Close", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(ok)
        box.exec()
        return box.clickedButton() is ok


class ShortcutKeyRecorder(QtCore.QObject):
    """Notes on an action which of its keys triggered it (property 'ctg_last_key': 'tab' for
    a Tab / Backtab key, else 'other')."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.Shortcut:
            with contextlib.suppress(Exception):
                seq = ev.key()
                k = seq[0].key() if seq.count() else None
                obj.setProperty(
                    "ctg_last_key", "tab" if k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab) else "other"
                )
        return False
