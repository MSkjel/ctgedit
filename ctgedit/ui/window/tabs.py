"""Document tabs, after-open work, files changed on disk and crash recovery."""

from __future__ import annotations

from ..main_window_base import (  # noqa: F401
    EditableDocument,
    ElementDocument,
    Page,
    PageScene,
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (  # noqa: F401
    _PaintWatch,
    _PendingPage,
    _app_filter,
    _run_all_post_open,
)


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
        if isinstance(doc, _PendingPage) and el:
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
        return app_settings().value("opt/tabs_wait_for_runs", False, type=bool)

    @classmethod
    def _runs_hold_tabs(cls) -> bool:
        """Do the open tabs wait for a run that saves library genies?"""
        return cls._libraries_being_saved() and cls._tabs_wait_for_runs()

    def _activate_doc(self, entry: dict) -> None:
        self._flush_text_edit()  # it belongs to the page we leave
        # selected nodes belong to the page we leave (the same index on this
        # page is another object)
        self.view.clear_node_selection()
        if isinstance(entry["doc"], _PendingPage):
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
        gen = editing_mod._LIBRARY_GENERATION[0]
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
            app_settings().value("opt/auto_update_genies", True, type=bool)
            or app_settings().value("opt/auto_update_symbols", True, type=bool)
        ):
            if self._runs_hold_tabs():
                # (while a run saves library genies one after another the tab waits: the
                # run's end brings every tab up to date, _after_chain_saves /
                # _follow_run_saves, and so does the end of the wait, _after_library_run)
                self._after_library_run(self.doc)
            else:
                if app_settings().value("opt/auto_update_genies", True, type=bool):
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
        pend = _PendingPage(path)
        pend.element = element or ""
        entry = {"doc": pend}
        if pinned:
            entry["pinned"] = True
        if view is not None:
            entry["session_view"] = view
        self._docs.append(entry)
        self._watch(path)  # as an opened tab (a recovery copy may take the tab over)
        _mw().MainWindow._session_seen.add(os.path.normcase(os.path.abspath(path)))
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
        _mw().MainWindow._session_seen.add(os.path.normcase(os.path.abspath(doc.path)))
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
            if not isinstance(doc, _PendingPage):
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

    # -------------------------------------------------- the tab menu and its commands
    def _target_entry(self):
        """The tab a tab command works on: the one its menu was opened on, else the current
        one (None: no tabs)."""
        t = self.__dict__.get("_tab_target")
        if t is not None and any(t is e for e in self._docs):
            return t
        k = self.doc_tabs.currentIndex()
        return self._docs[k] if 0 <= k < len(self._docs) else None

    def _tab_menu(self, k: int, global_pos: QtCore.QPoint) -> None:
        """Right-click on a tab: its commands (the File and Window menus' actions)."""
        if not 0 <= k < len(self._docs):
            return
        menu = self.build_tab_menu(k)
        try:
            self._exec_tab_menu(menu, global_pos)
        finally:
            self._tab_target = None
            self._update_actions()
            menu.deleteLater()

    @staticmethod
    def _exec_tab_menu(menu, pos) -> None:
        """Show the tab menu (a seam: tests trigger its actions instead)."""
        menu.exec(pos)

    def build_tab_menu(self, k: int) -> QtWidgets.QMenu:
        """The menu of tab ``k`` (its commands then act on that tab)."""
        self._tab_target = self._docs[k]
        self._update_actions()
        menu = QtWidgets.QMenu(self)
        for a in (
            self.act_close_doc,
            self.act_close_others,
            self.act_close_right,
            self.act_close_saved,
            self.act_close_all,
        ):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_pin_tab)
        menu.addAction(self.act_reopen_closed)
        menu.addSeparator()
        for a in (
            self.act_copy_page_path,
            self.act_copy_page_name,
            self.act_reveal_page,
            self.act_show_in_browser,
        ):
            menu.addAction(a)
        menu.addSeparator()
        # (these two show the tab first: they act on the page shown)
        menu.addAction(self.act_page_props)
        menu.addAction(self.act_revert)
        menu.addSeparator()
        menu.addAction(self.act_move_to_window)
        sub = menu.addMenu("Move to Window")
        self._fill_move_to_menu(sub)
        return menu

    def _show_target_tab(self) -> None:
        """A command of the tab menu that acts on the page shown: its tab is shown first."""
        t = self.__dict__.get("_tab_target")
        if t is not None and any(t is e for e in self._docs) and t["doc"] is not self.doc:
            self.show_tab(self._entry_index(t))

    def _closable(self, entries) -> list:
        """``entries`` without the pinned tabs."""
        return [e for e in entries if not e.get("pinned")]

    def close_other_tabs(self) -> bool:
        """Close Others: every unpinned tab but the target one (one save question)."""
        t = self._target_entry()
        if t is None:
            return False
        ok = self._close_entries(self._closable(e for e in self._docs if e is not t), show=t)
        if ok and any(t is e for e in self._docs) and self.doc is not t["doc"]:
            self.show_tab(self._entry_index(t))
        return ok

    def close_tabs_to_the_right(self) -> bool:
        t = self._target_entry()
        if t is None:
            return False
        k = self._entry_index(t)
        return self._close_entries(self._closable(self._docs[k + 1 :]))

    def close_saved_tabs(self) -> bool:
        """Close Saved: the unpinned tabs without unsaved changes."""
        return self._close_entries(
            [
                e
                for e in self._closable(self._docs)
                if not e["doc"].dirty and not self._pending_windows(e["doc"])
            ]
        )

    def close_all_tabs(self) -> bool:
        """Close All Pages (Ctrl+Shift+W): every unpinned tab (one save question)."""
        return self._close_entries(self._closable(self._docs))

    def _close_entries(self, entries, show=None) -> bool:
        """Close the tabs of ``entries``."""
        entries = [e for e in entries if any(e is d for d in self._docs)]
        if not entries:
            return True
        self._flush_text_edit()
        unsaved = [(e, self._pending_windows(e["doc"])) for e in entries]
        unsaved = [(e, p) for e, p in unsaved if e["doc"].dirty or p]
        if len(unsaved) >= 2:
            if not self._save_changes_list(unsaved, "closing them"):
                return False
        elif unsaved:
            # one unsaved page: its own question first (Cancel closes nothing)
            if not self._ask_to_close(self._entry_index(unsaved[0][0])):
                return False
        left = [e for e in entries if any(e is d for d in self._docs)]
        shown = next((e for e in left if e["doc"] is self.doc), None)
        for e in sorted((e for e in left if e is not shown), key=self._entry_index, reverse=True):
            if not any(e is d for d in self._docs):
                continue
            if not self._close_doc_tab(self._entry_index(e), ask=False):
                return False
        if shown is not None and any(shown is d for d in self._docs):
            if not self._close_doc_tab(self._entry_index(shown), ask=False, show=show):
                return False
        return True

    def _save_changes_list(self, unsaved, close_word: str = "closing") -> bool:
        """The Save Changes list for ``unsaved`` = [(entry, pending windows)] (two or more):
        the ticked pages are saved in tab order (stopping at the first that fails, with its
        message), the others are discarded (their recovery copies go). True: go on and
        close them all."""
        from ..dialog.save_changes import SaveChangesDialog

        rows = []
        for e, pending in sorted(unsaved, key=lambda u: self._entry_index(u[0])):
            rows.append(
                (
                    e,
                    self._tab_title(e["doc"]),
                    self._doc_project(e["doc"]),
                    self._change_note(e["doc"], pending),
                    os.path.dirname(os.path.abspath(e["doc"].path)),
                    not (not pending and self._only_auto_changes(e["doc"])),
                )
            )
        dlg = SaveChangesDialog(rows, self)
        r = self._exec_save_changes(dlg)
        if r == SaveChangesDialog.CANCEL:
            return False
        keep = dlg.selected() if r == SaveChangesDialog.SAVE else []
        if r == SaveChangesDialog.SAVE and not keep:
            return False  # (Save with nothing ticked never discards: only Don't Save does)
        failed = None
        for e, pending in sorted(unsaved, key=lambda u: self._entry_index(u[0])):
            if not any(e is x for x in keep):
                continue
            k = self._entry_index(e)
            if k != self.doc_tabs.currentIndex():
                self.doc_tabs.setCurrentIndex(k)
            try:
                ok = self._apply_pending_windows(pending) and (not e["doc"].dirty or self.save())
            except Exception as ex:  # noqa: BLE001  # swallow-ok: _save_failed says it
                ok = False
                with contextlib.suppress(Exception):
                    self._save_failed(e["doc"].path, ex, offer_save_as=False)
            if not ok:
                failed = e
                break
        if failed is not None:
            self.statusBar().showMessage(
                "Not closed: %s was not saved." % self._tab_title(failed["doc"]), 12000
            )
            return False
        from ..recovery import AutosaveController

        for e, _p in unsaved:
            if not any(e is x for x in keep):
                AutosaveController.forget(e["doc"])
        return True

    @staticmethod
    def _exec_save_changes(dlg) -> int:
        """Show the Save Changes list (a seam: tests answer it)."""
        return dlg.exec_choice()

    def _change_note(self, doc, pending) -> str:
        """What a page's unsaved changes are, for the Save Changes list."""
        undo = getattr(doc, "_undo", None) or []
        if not pending and self._only_auto_changes(doc):
            text = "updated automatically"
        elif len(undo) > 1:
            text = "%d steps" % len(undo)
        else:
            text = "edited" if doc.dirty else ""
        if pending:
            names = ", ".join(n for _d, n in pending)
            text = ("%s + %s not applied" % (text, names)) if text else "%s not applied" % names
        return text

    # -- copy, show, pin --------------------------------------------------------------------
    def copy_tab_path(self) -> None:
        e = self._target_entry()
        if e is None:
            return
        p = os.path.abspath(e["doc"].path)
        QtWidgets.QApplication.clipboard().setText(p)
        from ..notify import notify

        notify(self, "Copied the path of %s." % self._tab_title(e["doc"]), detail=p)

    def copy_tab_name(self) -> None:
        e = self._target_entry()
        if e is None:
            return
        d = e["doc"]
        if isinstance(d, ElementDocument):
            name = d.display_name
        elif getattr(d, "element", ""):
            name = "%s.%s" % (os.path.splitext(os.path.basename(d.path))[0], d.element)
        else:
            name = os.path.splitext(os.path.basename(d.path))[0]
        QtWidgets.QApplication.clipboard().setText(name)
        self.statusBar().showMessage("Copied %s." % name, 4000)

    def reveal_tab(self) -> None:
        """Show in Explorer: the page's folder with the file selected."""
        e = self._target_entry()
        if e is None:
            return
        p = os.path.abspath(e["doc"].path)
        self._reveal_in_explorer(p)

    @staticmethod
    def _reveal_in_explorer(path: str) -> None:
        """Open Explorer on ``path`` with it selected (a seam: tests replace it)."""
        import subprocess

        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", path])  # noqa: S603,S607
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))

    def show_tab_in_browser(self) -> None:
        """Show in Page Browser: File > Open's browser with this page (element) selected."""
        e = self._target_entry()
        if e is None:
            return
        from ..browser import PageBrowserDialog

        d = e["doc"]
        element = d.element_name if isinstance(d, ElementDocument) else (getattr(d, "element", "") or None)
        r = PageBrowserDialog.get_open_target(
            self, d.path, before_delete=self._before_delete_or_rename, start_element=element
        )
        if r is None:
            return
        if r.kind == "page":
            self.open_page(r.path)
        elif r.element:
            self._open_element(r.path, r.element)

    def toggle_pin_tab(self) -> None:
        """Pin Tab / Unpin Tab: a pinned tab sits at the left, short, without a close
        button; Close All, Close Others and Close Saved leave it."""
        e = self._target_entry()
        if e is None:
            return
        e["pinned"] = not e.get("pinned")
        self._enforce_pin_order()
        self._refresh_tabs()
        self._update_actions()
        self.statusBar().showMessage(
            "%s %s." % (self._tab_title(e["doc"]), "pinned" if e["pinned"] else "unpinned"), 4000
        )

    def _unpin_tab(self, k: int) -> None:
        """A click on a pinned tab's pin."""
        if 0 <= k < len(self._docs) and self._docs[k].get("pinned"):
            self._tab_target = self._docs[k]
            try:
                self.toggle_pin_tab()
            finally:
                self._tab_target = None

    def _enforce_pin_order(self) -> None:
        """The pinned tabs first (a tab dragged across the pinned edge goes back)."""
        want = [e for e in self._docs if e.get("pinned")] + [e for e in self._docs if not e.get("pinned")]
        if all(a is b for a, b in zip(want, self._docs)):
            return
        for t, e in enumerate(want):
            k = self._entry_index(e)
            if k != t:
                self._switching = True
                try:
                    self.doc_tabs.moveTab(k, t)  # (tabMoved keeps _docs in step)
                finally:
                    self._switching = False
        self._refresh_tabs()

    # -- switching -------------------------------------------------------------------------
    def _text_has_focus(self) -> bool:
        """A text field or the in-place text editor has the keyboard (the tab keys leave it
        alone)."""
        if getattr(self, "view", None) is not None and self.view.text_editing():
            return True
        fw = QtWidgets.QApplication.focusWidget()
        sw = self.__dict__.get("_switcher")
        if fw is None or (sw is not None and (fw is sw or sw.isAncestorOf(fw))):
            return False
        return isinstance(
            fw,
            (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox),
        )

    def _install_tab_keys(self) -> None:
        """Next / Previous Page learn which of their keys was pressed (Ctrl+Tab goes in the
        order the pages were used, Ctrl+PgDown along the tabs)."""
        rec = self.__dict__.get("_tab_key_recorder")
        if rec is None:
            rec = self._tab_key_recorder = _ShortcutKeyRecorder(self)
        for a in (self.act_next_page, self.act_prev_page):
            a.installEventFilter(rec)

    def switch_page(self, step: int, via_tab=None) -> None:
        """Window > Next / Previous Page."""
        sw = self.__dict__.get("_switcher")
        if sw is not None and sw.isVisible():
            sw.step(1 if step > 0 else -1)
            return
        act = self.act_next_page if step > 0 else self.act_prev_page
        if via_tab is None:
            via_tab = act.property("ctg_last_key") == "tab"
        act.setProperty("ctg_last_key", None)
        if self._text_has_focus() or len(self._docs) < 2:
            return
        from ..panels.doc_tabs import ctrl_tab_order

        if via_tab and ctrl_tab_order() == "mru":
            order = self._mru_order()
            held = bool(QtGui.QGuiApplication.queryKeyboardModifiers() & QtCore.Qt.ControlModifier)
            if held and self.isVisible():
                self._show_switcher(order, 1 if step > 0 else len(order) - 1)
                return
            target = order[1 if step > 0 else -1]
        else:
            k = (self.doc_tabs.currentIndex() + (1 if step > 0 else -1)) % len(self._docs)
            target = self._docs[k]
        self.show_tab(self._entry_index(target))

    def _mru_order(self) -> list:
        """The tabs, the one shown first, then in the order they were last shown (the
        never-shown ones last, in tab order)."""
        mru = [e for e in self.__dict__.get("_mru_entries", ()) if any(e is d for d in self._docs)]
        rest = [e for e in self._docs if not any(e is m for m in mru)]
        cur = self._target_entry()
        out = mru + rest
        if cur is not None and out and out[0] is not cur:
            out = [cur] + [e for e in out if e is not cur]
        return out

    def _show_switcher(self, order: list, start: int) -> None:
        sw = self.__dict__.get("_switcher")
        if sw is None:
            from ..panels.doc_tabs import TabSwitcher

            sw = self._switcher = TabSwitcher(self)
            sw.chosen.connect(
                lambda e: self.show_tab(self._entry_index(e)) if any(e is d for d in self._docs) else None
            )
            sw.cancelled.connect(lambda: self.view.setFocus(QtCore.Qt.OtherFocusReason))
        rows = [
            (
                e,
                self._tab_title(e["doc"]),
                self._doc_project(e["doc"]),
                self._doc_kind(e["doc"]),
                bool(e["doc"].dirty),
            )
            for e in order
        ]
        sw.open(rows, start)

    def show_tab(self, k: int) -> None:
        """Show tab ``k``; the canvas takes the keyboard."""
        if not 0 <= k < len(self._docs):
            return
        if k != self.doc_tabs.currentIndex():
            self.doc_tabs.setCurrentIndex(k)
        elif self.doc is not self._docs[k]["doc"]:
            self._activate_doc(self._docs[k])
        self.view.setFocus(QtCore.Qt.OtherFocusReason)

    def go_to_tab(self, k: int) -> None:
        """Window > Go to Page > Page n (Alt+Shift+1 to 9)."""
        if not self._text_has_focus():
            self.show_tab(k)

    def tab_rows(self) -> list:
        """(name, project, kind, unsaved, current) per tab (the list at the end of the tab
        bar)."""
        return [
            (
                self._tab_title(e["doc"]),
                self._doc_project(e["doc"]),
                self._doc_kind(e["doc"]),
                bool(e["doc"].dirty),
                e["doc"] is self.doc,
            )
            for e in self._docs
        ]

    def _fill_goto_tab_menu(self) -> None:
        for n, a in enumerate(self.goto_tab_actions):
            if n < len(self._docs):
                a.setText("&%d %s" % (n + 1, self._tab_title(self._docs[n]["doc"])))
            else:
                a.setText("Page %d" % (n + 1))

    # -- reopen a closed tab ----------------------------------------------------------------
    #: the closed tabs File > Reopen Closed Page keeps (per window)
    CLOSED_MAX = 20

    def _push_closed(self, entry: dict, k: int) -> None:
        d = entry["doc"]
        path = getattr(d, "path", "")
        if not path or getattr(d, "is_new", False) or not os.path.exists(path):
            return  # (never saved: nothing to reopen)
        element = d.element_name if isinstance(d, ElementDocument) else getattr(d, "element", "")
        view = self._entry_view(entry)
        stack = self.__dict__.setdefault("_closed_stack", [])
        stack.append(
            {
                "path": os.path.abspath(path),
                "element": element or "",
                "view": view,
                "index": k,
                "pinned": bool(entry.get("pinned")),
            }
        )
        del stack[: -self.CLOSED_MAX]

    def _entry_view(self, entry: dict):
        """(zoom, (x, y) of the view centre) the tab is shown at, or None (never shown)."""
        if entry["doc"] is self.doc and not isinstance(entry["doc"], _PendingPage):
            self._stash_view(entry)
        return entry.get("view")

    def reopen_closed_tab(self) -> bool:
        """File > Reopen Closed Page (Ctrl+Shift+T): the page closed last opens again, at
        the zoom and place it had, where its tab was."""
        if self._text_has_focus():
            return False
        stack = self.__dict__.get("_closed_stack") or []
        while stack:
            it = stack.pop()
            if not os.path.exists(it["path"]):
                continue
            before = len(self._docs)
            if it["element"]:
                self._open_element(it["path"], it["element"])
            elif not self.open_page(it["path"]):
                continue
            k = self.doc_tabs.currentIndex()
            e = self._docs[k] if self.doc is not None and 0 <= k < len(self._docs) else None
            if e is None or e["doc"] is not self.doc:
                return False
            if len(self._docs) > before:
                # a new tab: back at its old place, pinned again, at its old view
                if it.get("pinned"):
                    e["pinned"] = True
                want = max(0, min(it.get("index", len(self._docs) - 1), len(self._docs) - 1))
                k = self._entry_index(e)
                if k != want:
                    self._switching = True
                    try:
                        self.doc_tabs.moveTab(k, want)
                    finally:
                        self._switching = False
                self._enforce_pin_order()
                self._refresh_tabs()
                if it.get("view"):
                    doc = e["doc"]
                    z, c = it["view"]
                    # (after the element's own framing, which runs on the next turn)
                    QtCore.QTimer.singleShot(0, self, lambda d=doc, zz=z, cc=c: self._show_view_at(d, zz, cc))
                    e["view_restored"] = True
            self._update_actions()
            return True
        self._update_actions()
        self.statusBar().showMessage("No closed page to reopen.", 4000)
        return False

    # -- the first view of a page: where it was left, or fitted at most at the cap ----------
    def _open_zoom_mode(self) -> str:
        mode = self.__dict__.get("_open_zoom")
        if mode is None:
            from ..prefs.settings import OPEN_ZOOM, OPEN_ZOOM_DEFAULT, choice

            mode = choice("opt/open_zoom", OPEN_ZOOM, OPEN_ZOOM_DEFAULT)
        return mode

    def _first_view(self, entry: dict) -> None:
        """A tab shown for the first time: at the zoom and place the page was left at
        (Settings > Canvas 'Where I left it'; the page's size must be the same), at 100 %,
        or fitted -- at most at the largest opening zoom."""
        doc = entry["doc"]
        frame = entry.pop("frame", False)
        mode = self._open_zoom_mode()
        moved = entry.pop("moved_view", None)
        if moved is not None:
            entry["view_restored"] = True
            self._show_view_at(doc, *moved)
            return
        if mode == "last":
            saved = self._saved_view(doc)
            if saved is not None:
                entry["view_restored"] = True
                self._show_view_at(doc, *saved)
                return
            sv = entry.pop("session_view", None)
            if sv is not None:
                entry["view_restored"] = True
                self.view.restore_view(*sv)
                return
        if mode == "100":
            self.view.open_actual_size()
        else:
            self._fit_capped(isinstance(doc, ElementDocument))
        if frame and isinstance(doc, ElementDocument):
            # a restored library element: framed on its objects once the canvas has its
            # size, as files._open_element frames one opened from the library
            QtCore.QTimer.singleShot(
                0,
                self,
                lambda d=doc: self._when_laid_out(lambda: self._frame_content() if self.doc is d else None),
            )

    def _when_laid_out(self, fn, tries: int = 40) -> None:
        """Run ``fn`` once the canvas is shown and has its size (a page opened before the
        window shows waits for it)."""
        vp = self.view.viewport()
        if self.view.isVisible() and vp.width() > 10 and vp.height() > 10:
            fn()
            return
        if tries > 0:
            QtCore.QTimer.singleShot(
                0 if tries > 20 else 25, self, lambda: self._when_laid_out(fn, tries - 1)
            )

    def _show_view_at(self, doc, zoom: float, centre) -> None:
        def go():
            if self.doc is not doc:
                return
            self.view.set_zoom(float(zoom))
            self.view.centerOn(QtCore.QPointF(float(centre[0]), float(centre[1])))

        self._when_laid_out(go)

    def _fit_zoom(self) -> float:
        """The zoom Fit Page gives in the canvas now (0: no page)."""
        sc = self.view.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is None or page.isNull():
            return 0.0
        from ..editor import viewnav

        return viewnav.fit_zoom(page, self.view.maximumViewportSize())

    def _fit_capped(self, element: bool = False) -> None:
        """Fit Page for a page shown the first time, at most at Settings > Canvas 'Largest
        zoom when a page opens'. While the window settles (the fit follows its size) the cap
        is kept."""
        from ..panels.doc_tabs import open_zoom_cap

        self.view.fit()
        cap = open_zoom_cap(element)
        if not cap:
            return
        self._cap_arm = (self.doc, cap)
        if not self.__dict__.get("_cap_hooked"):
            self._cap_hooked = True
            self.view.zoomChanged.connect(lambda _z: self._apply_cap())
        self._when_laid_out(self._apply_cap)

    def _apply_cap(self) -> None:
        arm = self.__dict__.get("_cap_arm")
        if not arm or arm[0] is not self.doc:
            return
        cap = arm[1]
        z = self.view.zoom()
        fit = self._fit_zoom()
        if fit <= 0:
            return
        if abs(z - fit) > 0.01 * max(fit, 1e-6):
            if abs(z - cap) > 1e-6 and self.view.isVisible():
                self._cap_arm = None  # the user zoomed: theirs from now on
            return
        if z <= cap * 1.0001:
            return  # (fits under the cap: auto-fit goes on)
        self._cap_arm = None
        sc = self.view.scene()
        page = sc.page_rect()
        self.view.set_zoom(cap)
        self.view.centerOn(page.center())

    # -- 'where I left it': view/last/<path> --------------------------------------------------
    #: the pages whose last view is kept (the oldest goes): the default of Settings > Canvas >
    #: Opening a page > Remember the view of at most (ui/prefs/timing_options.LAST_VIEWS_KEPT)
    VIEW_LAST_MAX = 200

    @staticmethod
    def _view_key(doc) -> str:
        key = os.path.normcase(os.path.abspath(doc.path))
        el = doc.element_name if isinstance(doc, ElementDocument) else getattr(doc, "element", "")
        if el:
            key += "|" + el.lower()
        return "view/last/" + key.replace("\\", "/")

    @staticmethod
    def _page_size(doc):
        try:
            ap = doc.page.appearance
            pb = doc.parsed.page
            return (
                int(ap.width or getattr(pb, "page_width", 0) or 0),
                int(ap.height or getattr(pb, "page_height", 0) or 0),
            )
        except AttributeError:
            return (0, 0)

    def _remember_view(self, entry: dict) -> None:
        """Keep where tab ``entry`` was left (its zoom, the page point at the view's centre and
        the page's size), for the next time the page opens."""
        doc = entry["doc"]
        if isinstance(doc, _PendingPage) or not getattr(doc, "path", ""):
            return
        view = self._entry_view(entry)
        if view is None:
            return
        import json
        import time

        z, (x, y) = view
        w, h = self._page_size(doc)
        st = app_settings()
        st.setValue(
            self._view_key(doc),
            json.dumps(
                {
                    "z": round(float(z), 6),
                    "x": round(float(x), 2),
                    "y": round(float(y), 2),
                    "w": w,
                    "h": h,
                    "t": round(time.time(), 3),
                }
            ),
        )
        self._prune_views(st)

    def _prune_views(self, st) -> None:
        import json
        from ..prefs import timing_options

        keep = timing_options.LAST_VIEWS_KEPT
        st.beginGroup("view/last")
        try:
            keys = st.allKeys()
            if len(keys) <= keep:
                return
            stamped = []
            for k in keys:
                try:
                    stamped.append((float(json.loads(str(st.value(k))).get("t", 0)), k))
                except ValueError, TypeError, AttributeError:  # swallow-ok: garbled = oldest
                    stamped.append((0.0, k))
            stamped.sort()
            for _t, k in stamped[: len(keys) - keep]:
                st.remove(k)
        finally:
            st.endGroup()

    def _saved_view(self, doc):
        """(zoom, (x, y)) the page was left at, when its size is the same now; else None."""
        import json

        raw = app_settings().value(self._view_key(doc), "")
        if not raw:
            return None
        try:
            v = json.loads(str(raw))
            z, x, y = float(v["z"]), float(v["x"]), float(v["y"])
            size = (int(v.get("w", 0)), int(v.get("h", 0)))
        except ValueError, TypeError, KeyError:  # swallow-ok: a garbled entry fits instead
            return None
        if size != self._page_size(doc) or z <= 0:
            return None  # (another page now: it is fitted)
        return z, (x, y)

    def _remember_all_views(self) -> None:
        """The window closes: every opened tab's view is kept."""
        for e in list(self._docs):
            with contextlib.suppress(Exception):
                self._remember_view(e)

    # -- move a tab to another window ---------------------------------------------------------
    def _fill_move_to_menu(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        others = [w for w in _mw().MainWindow._instances if w is not self and w.isVisible()]
        for w in others:
            a = menu.addAction(w.windowTitle() or "ctgedit")
            a.triggered.connect(lambda _=False, win=w: self.move_tab_to_window(win))
        if not others:
            a = menu.addAction("No other window")
            a.setStatusTip("Open another editor window (Window > New Window) to move the page to it.")
            a.setEnabled(False)

    def move_tab_to_new_window(self):
        """Window > Move to New Window: the page (with its undo history and its windows)
        goes to a new editor window, maximised on the next screen when there is one."""
        e = self._target_entry()
        if e is None:
            return None
        win = _mw().MainWindow()
        self._place_new_window(win)
        self._move_entry_to(e, win)
        return win

    def _place_new_window(self, win, at: QtCore.QPoint | None = None) -> None:
        screens = QtGui.QGuiApplication.screens()
        if at is not None:
            scr = QtGui.QGuiApplication.screenAt(at) or self.screen()
            area = scr.availableGeometry()
            g = self.normalGeometry() if self.isMaximized() else self.geometry()
            w, h = min(g.width(), area.width()), min(g.height(), area.height())
            x = max(area.left(), min(at.x() - w // 2, area.right() - w))
            y = max(area.top(), min(at.y() - 20, area.bottom() - h))
            win.setGeometry(x, y, w, h)
            win.show()
            return
        cur = self.screen()
        nxt = cur
        if len(screens) > 1 and cur in screens:
            nxt = screens[(screens.index(cur) + 1) % len(screens)]
        win.setGeometry(nxt.availableGeometry())
        win.showMaximized()

    def move_tab_to_window(self, win) -> None:
        e = self._target_entry()
        if e is not None and win is not self:
            self._move_entry_to(e, win)
            win.bring_to_front()

    def _tab_dragged_out(self, k: int, global_pos: QtCore.QPoint) -> None:
        """A tab dropped where no editor window took it: a new window there shows it."""
        if not 0 <= k < len(self._docs):
            return
        target = None
        for w in _mw().MainWindow._instances:
            if w is not self and w.isVisible() and w.frameGeometry().contains(global_pos):
                target = w
                break
        e = self._docs[k]
        if target is None:
            target = _mw().MainWindow()
            self._place_new_window(target, global_pos)
        self._move_entry_to(e, target)
        target.bring_to_front()

    def _tab_dropped(self, source_bar, src_index: int, at: int) -> None:
        """Another window's tab was dropped on this window's tab bar: it moves here."""
        src = source_bar.window()
        if not hasattr(src, "_docs") or not 0 <= src_index < len(src._docs):
            return
        if src is self:
            # its own tab, dragged out of the bar and back: it goes where it was dropped
            to = max(0, min(at, len(self._docs) - 1))
            if to != src_index:
                self.doc_tabs.moveTab(src_index, to)
                self._enforce_pin_order()
            return
        src._move_entry_to(src._docs[src_index], self, at)
        self.bring_to_front()

    def _move_entry_to(self, entry: dict, win, index: int | None = None) -> None:
        """Move tab ``entry`` to editor window ``win`` (at tab ``index``): the document object
        goes as it is (no reopen, its undo history), with its object and page windows."""
        if not any(entry is e for e in self._docs) or win is self:
            return
        doc = entry["doc"]
        if doc is self.doc:
            self._flush_text_edit()
            self._stash_current()
        k = self._entry_index(entry)
        # ('stale': drawn again when shown, from the files as they are)
        moved = {
            key: entry[key]
            for key in (
                "doc",
                "sel",
                "sel_note",
                "hidden",
                "locked",
                "pinned",
                "genie_gen",
                "session_view",
                "post_open",
                "choice",
                "frame",
                "stale",
            )
            if key in entry
        }
        view = self._entry_view(entry)
        if view is not None:
            moved["moved_view"] = view
        dialogs = [] if isinstance(doc, _PendingPage) else self._page_windows(doc)
        for dlg in dialogs:
            # (out of this window's list first: showing another tab here would close them)
            self._release_modeless(dlg)
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None and not isinstance(doc, _PendingPage):
            if jobs.busy(doc):
                # (its update goes with the job: the new window looks again, as after the
                # task's Cancel)
                with contextlib.suppress(Exception):
                    ui_state(doc).genie_sources_done.clear()
            jobs.cancel(doc)
        self._switching = True
        try:
            del self._docs[k]
            self.doc_tabs.removeTab(k)
        finally:
            self._switching = False
        self._forget_entry(entry)
        self._drop_tab_scene(entry)
        self._unwatch(doc.path)
        # its open checks that have not run yet (the page moved within a moment of its open)
        # go with it: the new window runs them
        pend = [p for p in getattr(self, "_post_open_pending", []) if p[0] is doc]
        self._post_open_pending = [p for p in getattr(self, "_post_open_pending", []) if p[0] is not doc]
        if pend:
            moved["open_checks"] = [(path, choice) for _d, path, choice in pend]
        # (and an automatic update that waits for the user to finish: files._wait_until_idle)
        idle = self._take_idle_wait(doc)
        if idle is not None:
            moved["idle_wait"] = idle
        q = self.__dict__.get("_ctf_queue")
        if q:
            q[:] = [d for d in q if d is not doc]
        self._after_tab_removed(k)
        win._adopt_entry(moved, index, dialogs, self)
        self.statusBar().showMessage("Moved %s to another window." % self._tab_title(doc), 6000)

    def _adopt_entry(self, entry: dict, index, dialogs, old) -> None:
        """Take tab ``entry`` (its document as it is) and its ``dialogs`` from window
        ``old``."""
        doc = entry["doc"]
        checks = entry.pop("open_checks", None)
        idle = entry.pop("idle_wait", None)
        self._stash_current()
        if index is None or not 0 <= index <= len(self._docs):
            index = len(self._docs)
        self._docs.insert(index, entry)
        self._watch(doc.path)
        _mw().MainWindow._session_seen.add(os.path.normcase(os.path.abspath(doc.path)))
        self._start_page_off = False
        self._switching = True
        try:
            self.doc_tabs.insertTab(index, self._tab_title(doc))
        finally:
            self._switching = False
        self._enforce_pin_order()
        self._refresh_tabs()
        self.doc_tabs.setVisible(True)
        for dlg in dialogs:
            with contextlib.suppress(RuntimeError):
                self._adopt_modeless(dlg, old)
        if idle is not None:
            # (its show below makes the update that waited: _activate_doc; a wait for its
            # steps to redo keeps its minute)
            self._wait_until_idle(doc, redraw=idle[0], templates=idle[1], since=idle[2], reload=idle[3])
        k = self._entry_index(entry)
        if k != self.doc_tabs.currentIndex():
            self.doc_tabs.setCurrentIndex(k)
        if self.doc is not doc:
            self._activate_doc(entry)
        if checks and not isinstance(doc, _PendingPage):
            # the open checks the window it came from had not run (the template, the
            # symbols, then the genies; the .ctF check after them), after this window
            # painted it
            for path, choice in checks:
                self._queue_post_open(doc, path, choice)
        elif not isinstance(doc, _PendingPage):
            if not isinstance(doc, ElementDocument):
                self._queue_ctf_check(doc)
            if ui_state(doc).steps_after_template:
                # (its open's template step went with the old window's job: asked here again,
                # the symbol, placeholder genie and genie steps after it when it lands)
                with contextlib.suppress(Exception):
                    if not self._open_template_step(doc):
                        self._open_steps_after_template(doc)
            elif app_settings().value("opt/auto_update_genies", True, type=bool):
                # (an element tab too: the genies nested in it, _update_stale_nested; a job
                # the old window dropped is submitted here again)
                with contextlib.suppress(Exception):
                    self._update_stale_genies(doc, first=True)

    def open_documents(self) -> list:
        """The documents of the open tabs; a restored tab not shown yet is
        opened here (a search over every open page reads them all)."""
        for e in list(self._docs):
            if isinstance(e["doc"], _PendingPage):
                self._open_pending_now(e)
        return [e["doc"] for e in self._docs]

    def _open_pending_now(self, entry: dict) -> bool:
        """Open a restored tab's page in the background (its tab stays where
        it is): its damage notice and open checks at once, as restore_session
        did for every tab. False when it did not open (reported, tab gone)."""
        if not self._materialise(entry):
            return False
        doc = entry["doc"]
        if isinstance(doc, ElementDocument):
            return True  # (a library element has no open checks)
        with contextlib.suppress(Exception):
            if doc.damage:
                self._damage_notice(doc)
        with contextlib.suppress(Exception):
            self._post_open_checks(doc, doc.path, entry.pop("choice", "edit"))
        self._log_page_open(doc)
        self._queue_ctf_check(doc)
        self._queue_display_check(doc)
        return True

    def _open_pending_placing(self, element: str) -> None:
        """A library element was saved: the restored tabs not opened yet whose
        page may place it (its name is in the file) are opened now, so the
        save updates them in their tabs like every other open page -- as
        when restore_session had opened every tab."""
        from ...format.textcodec import CODEC

        needle = (element or "").lower().encode(CODEC, "replace")
        for e in list(self._docs):
            if not isinstance(e["doc"], _PendingPage) or getattr(e["doc"], "element", ""):
                continue
            try:
                with open(e["doc"].path, "rb") as fh:
                    data = fh.read()
            except OSError:
                data = None
            # (bytes.lower() folds ASCII only: a non-ASCII name opens the page)
            if data is None or not needle.isascii() or needle in data.lower():
                self._open_pending_now(e)

    def open_document(self, path: str, ask_supergenie: bool = True) -> None:
        # already open -> just switch to its tab; open in ANOTHER editor window -> that window
        # and tab come to the front (two editable copies of one page would each overwrite
        # the other's save)
        want = os.path.normcase(os.path.abspath(path))
        wins = [self] + [w for w in list(_mw().MainWindow._instances) if w is not self]
        for win in wins:
            for e in win._docs:
                d = e["doc"]
                if isinstance(d, ElementDocument) or getattr(d, "element", ""):
                    continue  # (a library element's tab, opened or not)
                if getattr(ui_state(d), "version_view", None) is not None:
                    continue  # (an earlier version: not the page)
                if os.path.normcase(os.path.abspath(d.path)) == want:
                    if win is not self:
                        win.showNormal() if win.isMinimized() else None
                        win.raise_()
                        win.activateWindow()
                        win.statusBar().showMessage(
                            "%s is open in this window." % os.path.basename(path), 6000
                        )
                    win.set_document(d)
                    return
        doc = self._open_page_file(path, checks=True)
        # a page CREATED FROM A SUPER GENIE: CtDraw refuses it (or asks, with
        # [CtDraw.RSC] AllowEditSuperGeniePage = 1) -- Update Pages would
        # overwrite edits made here
        choice = self._supergenie_guard(doc, ask_supergenie)
        if choice == "cancel":
            return
        if choice == "definition":
            target = doc.supergenie_element()
            if target is not None:
                self._open_element(*target)
            return
        # (the objects hidden on it last time: its tab's activation, _saved_hidden)
        if choice == "view":
            doc.read_only = True
            doc.ui.supergenie_view = True  # Revert / reload keep it read-only
        self.set_document(doc)
        self._locked.clear()
        self._push_mru(path)
        self._opened_message(self.doc, path, choice)
        self._opened(self.doc, path, choice)

    def _opened_message(self, doc, path: str, choice: str) -> None:
        """'Opened Mixer (Example).' (the path in its tool tip); a page that opened read-only
        says so in its notice bar too (ui/editor/notices.py)."""
        from ..notify import notify

        name = os.path.splitext(os.path.basename(path))[0]
        project = os.path.basename(os.path.dirname(os.path.abspath(path)))
        where = "%s (%s)" % (name, project) if project else name
        if choice == "view":
            notify(
                self,
                "Opened %s read-only. It is made from its super genie: edit the super genie instead." % where,
                detail=path,
                key="open:%s" % path,
            )
        elif doc.read_only:
            notify(
                self,
                "Opened %s. The file is read-only: Save asks where to save a copy." % where,
                "warning",
                detail=path,
                key="open:%s" % path,
            )
        else:
            notify(self, "Opened %s." % where, detail=path, key="open:%s" % path)
        self._auto_bar.show_for(self.doc)

    # -- after a page opened --------------------------------------------------
    #: run a page open's checks after the canvas painted the page? None = when
    #: an event loop runs (a script or a test without one runs them at once)
    DEFER_POST_OPEN = None
    #: ms to wait for the page's first paint before running them anyway
    POST_OPEN_FALLBACK_MS = 400

    def _defer_post_open(self) -> bool:
        d = self.DEFER_POST_OPEN
        if d is None:
            d = QtCore.QThread.currentThread().loopLevel() > 0
        return bool(d)

    def _opened(self, doc, path: str, choice: str) -> None:
        """A page was opened and shown: run its checks."""
        if doc.damage:
            self._damage_notice(doc)
        self._queue_post_open(doc, path, choice)

    def _queue_post_open(self, doc, path: str, choice: str) -> None:
        """Run ``doc``'s open checks (:meth:`_post_open_checks`) once the canvas has painted
        it, before any user input reaches the window (:meth:`_opened`; a page moved here
        from another window before they ran, _adopt_entry)."""
        pending = self.__dict__.setdefault("_post_open_pending", [])
        pending.append((doc, path, choice))
        if not self._defer_post_open():
            self._run_post_open()
            return
        _PaintWatch(self.view.viewport(), self._run_post_open)
        QtCore.QTimer.singleShot(self.POST_OPEN_FALLBACK_MS, self, self._run_post_open)
        f = _app_filter()
        if f is not None:
            f.before_input = _run_all_post_open

    def _run_post_open(self) -> None:
        """Run the pending open checks (once each; a closed page's are gone)."""
        pending = self.__dict__.get("_post_open_pending") or []
        self._post_open_pending = []
        for doc, path, choice in pending:
            if not any(e["doc"] is doc for e in self._docs):
                continue
            # the checks' auto steps each asked for a window refresh (the scene, the Objects
            # tree, the Properties panel): one at the end instead
            self._refresh_hold = []
            try:
                self._post_open_checks(doc, path, choice)
            except Exception:  # noqa: BLE001 -- hints / optional updates only
                from ...infra import logsetup

                logsetup.get("open").warning("open checks of %s failed", path, exc_info=True)
            finally:
                held = self.__dict__.pop("_refresh_hold", None)
            if held and doc is self.doc:
                units = []
                for h in held:
                    units.extend(h[2])
                self._refresh(
                    keep_sel=all(h[0] for h in held), rebuild_tree=any(h[1] for h in held), units=units
                )
                self._update_actions()
            self._log_page_open(doc)
            self._queue_ctf_check(doc)
            self._queue_display_check(doc)

    def _post_open_checks(self, doc, path: str, choice: str) -> None:
        """What a page's open does after it showed, as CtDraw's load (each change an undoable
        step, listed in the notice bar); each result is a message of its own (the bell lists
        them all: ui/notify.py)."""
        from ..names import plural
        from ..notify import notify

        cur = doc is self.doc
        name = os.path.splitext(os.path.basename(path))[0]
        if not doc.damage and self._open_in_builder(path):
            self._builder_notice(doc, path)
            notify(
                self,
                "%s is also open in the Graphics Builder. Close it there before you save here." % name,
                "warning",
                detail=path,
                key="open:builder:%s" % path,
            )
        if choice == "edit" and not doc.read_only and doc.supergenie_link():
            # CtDraw's load of a super genie page gives it the super genie's
            # environment and associations
            synced = False
            with contextlib.suppress(Exception):
                synced = self._auto_step(doc, "super genie lists synced", doc.sync_supergenie_lists)
            if synced:
                if cur:
                    self._refresh()
                    self._update_actions()
                notify(
                    self,
                    "The associations and environment variables of %s were taken "
                    "from its super genie (Undo keeps the old ones)." % name,
                    detail=path,
                    key="open:sglists:%s" % path,
                )
        # a template or genie whose library does not open: CtDraw's load makes it a
        # placeholder (it becomes the object again when the library opens); first, as
        # CtDraw's load meets the page's records in order
        if not doc.read_only and app_settings().value("opt/auto_placeholder_missing", True, type=bool):
            n = 0
            with contextlib.suppress(Exception):
                missing = doc.missing_library_objects()
                if missing:
                    n = self._auto_step(
                        doc, "placeholders for missing libraries", doc.placeholders_for_missing_libraries
                    )
            if n:
                if cur:
                    self._refresh()
                    self._update_actions()
                notify(
                    self,
                    "In %s, %s whose library is missing became %s, as in the "
                    "Graphics Builder (Undo keeps them)."
                    % (name, plural(n, "object"), "a placeholder" if n == 1 else "placeholders"),
                    "warning",
                    detail=path,
                    key="open:placeholders:%s" % path,
                )
            # a placeholder a library genie holds, its library still missing: its box as
            # CtDraw's load derives it (an automatic step, no message)
            with contextlib.suppress(Exception):
                if doc.placeholder_boxes_due():
                    self._auto_step(doc, "placeholder boxes redrawn", doc.rederive_placeholder_boxes)
                    if cur:
                        self._refresh()
        # a page whose template changed since it was saved: CtDraw re-applies
        # the current template when it opens the page (live-verified); do the
        # same, as an undoable step. FIRST, as CtDraw's load meets it first (the
        # page's first record): the handles it frees are free for the symbols and
        # genies after it
        if self._open_template_step(doc):
            # (in the genie worker: the symbol, placeholder genie and genie steps follow when
            # it lands, _genie_job_finished: run at once, they would take the handles first
            # and the template's answer would land after them)
            return
        self._open_steps_after_template(doc)

    def _open_template_step(self, doc) -> bool:
        """The open's template step."""
        from ..names import template_name

        cur = doc is self.doc
        ui_state(doc).steps_after_template = False
        try:
            state, link = doc.template_status()
        except Exception:  # noqa: BLE001 -- a hint only
            state, link = "", ""
        if state != "stale":
            return False
        n = 0
        if app_settings().value("opt/auto_update_template", True, type=bool):
            worker = False
            if not doc.read_only and not self._template_takes_page_properties():
                with contextlib.suppress(Exception):
                    worker = self._genie_job_runner().submit_template(doc, link)
            if worker:
                ui_state(doc).steps_after_template = True
                return True
            with contextlib.suppress(Exception):
                n = self._auto_step(doc, "template %s re-applied" % template_name(link), doc.update_template)
            if n and n > 0:
                if cur:
                    self._refresh()
                    self._update_actions()
                self._template_message(doc, link, True)
        if not n:
            self._template_message(doc, link, False)
        return False

    def _template_job_dropped(self, doc) -> None:
        """The open's template job will not land (its task's Cancel): the steps that waited
        for it run now (:meth:`_open_steps_after_template`); the template stays as it is."""
        if ui_state(doc).steps_after_template and any(e["doc"] is doc for e in self._docs):
            self._open_steps_after_template(doc)

    def _open_steps_after_template(self, doc) -> None:
        """The open's steps after its template, in CtDraw's load order."""
        from ..names import plural
        from ..notify import notify

        ui_state(doc).steps_after_template = False
        cur = doc is self.doc
        path = getattr(doc, "path", "") or ""
        name = os.path.splitext(os.path.basename(path))[0]
        # placed library SYMBOLS whose element changed since the page was saved:
        # CtDraw re-derives every linked symbol from its element when it loads
        # a page (a symbol keeps only its Local) -- the same here, undoable.
        # (doc.symbol_status's rule, the stale elements read ONCE)
        if not doc.read_only and app_settings().value("opt/auto_update_symbols", True, type=bool):
            names = []
            with contextlib.suppress(Exception):
                placed = doc.symbol_placements()
                stale = doc.stale_symbol_elements() if placed else set()
                if stale:
                    names = sorted(
                        {
                            "%s.%s" % key
                            for _i, key, _k in placed
                            if (key[0].lower(), key[1].lower()) in stale
                        },
                        key=str.lower,
                    )
                    self._auto_step(
                        doc,
                        "symbols updated from their library (%s)" % ", ".join(names[:5]),
                        doc.update_symbol_placements,
                        stale,
                    )
                    # (the versions it took: undone, a later automatic update leaves them,
                    # files._update_placed_symbols)
                    self._remember_symbol_versions(doc, stale)
            if names:
                if cur:
                    self._refresh()
                    self._update_actions()
                notify(
                    self,
                    "In %s, %s changed in %s library since the page was saved and "
                    "%s been updated (Undo keeps the old version)."
                    % (
                        name,
                        ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
                        "its" if len(names) == 1 else "their",
                        "has" if len(names) == 1 else "have",
                    ),
                    detail=path,
                    key="open:symbols:%s" % path,
                )
        # PLACEHOLDER genies (a genie whose library was missing when the page was
        # saved, stored as a marker with its parameters): CtDraw's page load places
        # the genie again once its library is there -- the same here, undoable
        if not doc.read_only:
            placed = 0
            with contextlib.suppress(Exception):
                if doc.placeholder_genies():
                    placed = (
                        self._auto_step(
                            doc, "genies placed from their library", doc.instantiate_placeholder_genies
                        )
                        or 0
                    )
            if placed:
                if cur:
                    self._refresh()
                    self._update_actions()
                notify(
                    self,
                    "In %s, %s whose library was missing when the page was saved "
                    "%s been placed (Undo keeps the old version)."
                    % (name, plural(placed, "genie"), "has" if placed == 1 else "have"),
                    detail=path,
                    key="open:placeholder_genies:%s" % path,
                )
        # placed GENIES whose element, or a genie nested in it, changed since the
        # page was saved: CtDraw's page load composes every instance from the
        # element and its nested genies as they are now -- the same here, undoable
        # a remembered genie update the open applied before the page's derive: said now
        self._genie_answer_said(doc)
        if not doc.read_only and app_settings().value("opt/auto_update_genies", True, type=bool):
            # (the update lands by itself when it is done: _genie_job_finished)
            self._update_stale_genies(doc, first=cur)

    def _template_message(self, doc, link: str, applied: bool) -> None:
        """The open's message about a template that changed since the page was saved (applied
        again, or not: Tools > Update Template on This Page does it). The template is named
        as its library spells it, ``link`` being its link-table reference."""
        from ..names import template_name
        from ..notify import notify

        path = getattr(doc, "path", "")
        name = os.path.splitext(os.path.basename(path))[0]
        link = template_name(link)
        if applied:
            notify(
                self,
                "The template %s of %s changed since the page was saved and was "
                "applied again (Undo keeps the old version)." % (link, name),
                detail=path,
                key="open:template:%s" % path,
            )
            return
        acts = []
        a = getattr(self, "act_update_page_template", None)
        if a is not None:
            acts.append(("Update Template", a.trigger, lambda d=doc: self.doc is d and a.isEnabled()))
        notify(
            self,
            "The template %s of %s changed since the page was saved. Tools > Update "
            "Template on This Page applies it again." % (link, name),
            "warning",
            actions=acts,
            detail=path,
            key="open:template:%s" % path,
        )

    def _builder_notice(self, doc, path: str) -> None:
        """The notice of a page that is open in the Graphics Builder too (Check Again)."""
        from ..editor import notices

        def again():
            win = notices.window_of(doc, self)
            from ..notify import notify

            name = os.path.splitext(os.path.basename(path))[0]
            if win._open_in_builder(path):
                notify(
                    win,
                    "%s is still open in the Graphics Builder." % name,
                    "warning",
                    key="open:builder:%s" % path,
                )
                return False
            notify(
                win,
                "%s is no longer open in the Graphics Builder." % name,
                "success",
                key="open:builder:%s" % path,
            )
            return None

        notices.add(
            doc,
            "builder",
            "warning",
            "This page is also open in the Graphics Builder. Close it there before you save here.",
            [("Check Again", again)],
        )

    @staticmethod
    def _template_takes_page_properties() -> bool:
        """[Animator] TemplateUpdate of the active install: the template step also sets the
        page's size, background and properties (done inline then)."""
        try:
            from ...io.citect_config import template_update_enabled

            return bool(template_update_enabled())
        except Exception:  # noqa: BLE001  # swallow-ok: not known, as the step itself takes it
            return False

    # -- the fast-display trust check (editing.CtfPrecheck) ---------------------
    #: ms after a page's open checks before its check process may start, and
    #: between tries while another page's check runs (one at a time)
    CTF_CHECK_DELAY_MS = 1500

    def _queue_ctf_check(self, doc) -> None:
        """Start ``doc``'s fast-display check process when the editor is idle
        (after its open), one page at a time."""
        try:
            if not doc.ctf_precheck_pending():
                return
        except Exception:  # noqa: BLE001 -- not an EditableDocument
            return
        q = self.__dict__.setdefault("_ctf_queue", [])
        if not any(d is doc for d in q):
            q.append(doc)
        t = self.__dict__.get("_ctf_timer")
        if t is None:
            t = self._ctf_timer = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._ctf_tick)
        if not t.isActive():
            t.start(self.CTF_CHECK_DELAY_MS)

    def _ctf_tick(self) -> None:
        q = self.__dict__.get("_ctf_queue") or []
        q[:] = [d for d in q if any(e["doc"] is d for e in self._docs) and d.ctf_precheck_pending()]
        if q and q[0].start_ctf_precheck():
            self._ctf_task_started(q.pop(0))  # started (False = another one still runs)
        if q:
            self._ctf_timer.start(self.CTF_CHECK_DELAY_MS)

    #: ms between looks at a running fast display check (its task in the status bar)
    CTF_TASK_POLL_MS = 700

    def _ctf_task_started(self, doc) -> None:
        """The fast display check of ``doc`` runs in its process: a background task until it
        ends (ui/jobs/tasks.py)."""
        box = getattr(doc, "_ctf_pre", None)
        chk = box.get("thread") if isinstance(box, dict) else None
        if chk is None or not chk.is_alive():
            return
        from ..jobs import tasks

        name = os.path.splitext(os.path.basename(doc.path))[0]
        # (quiet: a check that runs by itself after an open, for the save that waits for it;
        # its end posts nothing and flashes nothing)
        token = tasks.begin("ctf", "Checking the fast display file of %s…" % name, window=self, quiet=True)
        timer = QtCore.QTimer(self)
        timer.setInterval(self.CTF_TASK_POLL_MS)

        def poll():
            if chk.is_alive() and any(e["doc"] is doc for e in self._docs):
                return
            timer.stop()
            timer.deleteLater()
            tasks.end(token, tell=False)

        timer.timeout.connect(poll)
        timer.start()

    def _supergenie_guard(self, doc, ask: bool = True) -> str:
        """'edit' | 'view' | 'definition' | 'cancel' for opening ``doc``: an
        ordinary page opens ('edit'); a page created from a super genie asks
        (SuperGeniePageDialog) -- without asking (a restored session) it opens
        read-only unless [CtDraw.RSC] AllowEditSuperGeniePage is set."""
        try:
            link = doc.supergenie_link()
        except Exception:  # noqa: BLE001
            link = None
        if link is None:
            return "edit"
        try:
            from ...io.citect_config import allow_edit_super_genie_page

            allow = allow_edit_super_genie_page()
        except Exception:  # noqa: BLE001
            allow = False
        if not ask:
            return "edit" if allow else "view"
        from ..dialog.supergenie_dialogs import SuperGeniePageDialog
        from ...format import supergenie as SG

        dlg = SuperGeniePageDialog(
            os.path.splitext(os.path.basename(doc.path))[0],
            "%s.%s" % (link[0], SG.page_name(link[1])),
            allow,
            self,
            can_open_definition=doc.supergenie_element() is not None,
        )
        return self._exec_supergenie_dialog(dlg)

    @staticmethod
    def _exec_supergenie_dialog(dlg) -> str:
        """Show the open-a-super-genie-page question (a seam: tests answer it)."""
        dlg.exec()
        return dlg.choice()

    def _damage_notice(self, doc) -> None:
        """A page that was damaged when it was opened: its notice bar says what is wrong
        (non-blocking: a restored session must not stop on it), with Save As and Details."""
        name = os.path.basename(doc.path)
        from ..notify import notify

        notify(
            self,
            "%s is damaged: %s. ctgedit opened what it could read." % (name, "; ".join(doc.damage)),
            "error",
            detail=doc.path,
            actions=[("Details", lambda d=doc: self._damage_details(d))],
            key="open:damaged:%s" % doc.path,
        )
        if doc is self.doc:
            self._auto_bar.show_for(doc)
        from ...infra import logsetup

        logsetup.get("open").warning("%s is damaged: %s", doc.path, "; ".join(doc.damage))

    def _damage_details(self, doc) -> None:
        """The damaged page's notice > Details: what is wrong and what saving it does."""
        from ..messages import error_box

        name = os.path.basename(doc.path)
        error_box(
            self,
            "Damaged Page",
            "%s is damaged: %s." % (name, "; ".join(doc.damage)),
            next_steps=(
                "ctgedit opened what it could read. Saving it in place replaces "
                "the damaged file (ctgedit asks first).",
                "Use Save As to keep the original.",
            ),
            actions=(("Show in Explorer", lambda: self._reveal_in_explorer(doc.path)),),
        )

    @staticmethod
    def _open_in_builder(path: str) -> bool:
        """Is page ``path`` open in a running Graphics Builder?"""
        try:
            from ...backend.graphics_builder import page_open_in_builder

            return page_open_in_builder(path)
        except Exception:  # noqa: BLE001 -- a hint only
            return False

    def open_page(self, path: str) -> bool:
        """``open_document`` for the user's own commands (Open, Recent Pages,
        the start page): a page that cannot be opened is reported in plain
        words instead of escaping as a traceback. The Recent Pages entry is
        kept. False when it did not open."""
        try:
            self.open_document(path)
        except Exception as e:  # noqa: BLE001
            self.report_open_failures([(path, e)])
            return False
        return True

    @staticmethod
    def _open_error_text(path: str, exc: BaseException) -> str:
        name = os.path.basename(path)
        if isinstance(exc, FileNotFoundError) or not os.path.exists(path):
            return "%s was not found. It may have been moved, renamed or deleted." % path
        if isinstance(exc, PermissionError):
            who = ""
            with contextlib.suppress(Exception):
                from ...backend.filelock import describe

                who = describe(path)
            if who:
                return "%s could not be read: it is locked by %s." % (name, who)
            return "%s could not be read: Windows denied access to it." % name
        if isinstance(exc, ValueError) and name.lower() in str(exc).lower():
            return str(exc)  # the editor's own message names the file
        if isinstance(exc, OSError):
            return "%s could not be opened: %s." % (name, exc.strerror or exc)
        # a parse error from a truncated / foreign file ('attributes block too
        # small', struct.error ...): say what it means, keep the detail
        return "%s could not be opened: the file is damaged or is not a graphics page (%s)." % (name, exc)

    def report_open_failures(self, failures) -> None:
        """One box listing the pages that did not open ([(path, error)]), with Copy Details,
        Open Log and Show in Explorer (ui/messages.py)."""
        failures = list(failures)
        if not failures:
            return
        lines = [self._open_error_text(p, e) for p, e in failures]
        if len(lines) == 1:
            text = lines[0]
        else:
            text = "%d pages could not be opened:\n\n%s" % (len(lines), "\n".join("• " + ln for ln in lines))
        path, exc = failures[0]
        from ..messages import error_box

        error_box(
            self,
            "Page Not Opened" if len(lines) == 1 else "Pages Not Opened",
            text,
            exc=exc if isinstance(exc, BaseException) else None,
            next_steps=(self._open_next_step(path, exc),),
            actions=(("Show in Explorer", lambda p=path: self._reveal_in_explorer(p)),),
        )

    @staticmethod
    def _open_next_step(path: str, exc) -> str:
        """What to do about a page that did not open (one line)."""
        if isinstance(exc, FileNotFoundError) or not os.path.exists(path):
            return "Open it from its new place with File > Open."
        if isinstance(exc, PermissionError):
            return "Close the program that has it open, then open it again."
        return "Open a backup copy of the page, or ask for the page again."

    def open_files(self, paths) -> list:
        """Open pages handed in from outside -- the command line, or a
        double-clicked .ctg forwarded by a second launch: each in its own tab
        (an open one is just shown). One message lists the files that could
        not be opened; returns those as (path, reason) pairs."""
        failed = []
        for p in paths:
            if not os.path.isfile(p):
                failed.append((p, "The file does not exist (it may have been moved or deleted)."))
                continue
            if not p.lower().endswith(".ctg"):
                failed.append(
                    (
                        p,
                        "ctgedit opens graphics pages (.ctg) this way. To edit a "
                        "symbol, genie or template, use File > Open and pick it "
                        "in its library.",
                    )
                )
                continue
            try:
                self.open_document(p)
            except Exception as e:  # noqa: BLE001 -- reported below
                failed.append((p, self._error_text(e)))
        self._update_start_page()
        if failed:
            from ..messages import error_box

            first = failed[0][0]
            # (a file of another kind: its reason says what to do instead)
            steps = (
                ()
                if os.path.isfile(first) and not first.lower().endswith(".ctg")
                else (self._open_next_step(first, None),)
            )
            error_box(
                self,
                "Could Not Open Page" if len(failed) == 1 else "Could Not Open Pages",
                "\n\n".join("%s\n%s" % f for f in failed),
                next_steps=steps,
                actions=(("Show in Explorer", lambda p=first: self._reveal_in_explorer(p)),),
            )
        return failed

    # ------------------------------------------------ files changed on disk
    def _watch(self, path: str) -> None:
        with contextlib.suppress(Exception):
            if path and os.path.exists(path) and path not in self._watcher.files():
                self._watcher.addPath(path)

    def _unwatch(self, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if any(os.path.normcase(os.path.abspath(e["doc"].path)) == key for e in self._docs):
            return  # another tab shows it
        with contextlib.suppress(Exception):
            self._watcher.removePath(path)

    def _on_file_changed(self, path: str) -> None:
        self._disk_pending.add(path)
        self._disk_timer.start()

    def _check_disk_changes(self, paths=None) -> None:
        """Notice open documents whose file another program changed."""
        from ..prefs.settings import disk_reload
        from ..editor.notices import disk_change_prompt
        from ..notify import notify
        from ..jobs.update_run import UpdatePagesRun

        if paths is None:
            paths, self._disk_pending = self._disk_pending, set()
        mode = disk_reload()
        if mode == "never":
            return
        dialog = disk_change_prompt() == "dialog"
        # a dialog is open: no question now, and no reload either (the page would change
        # under the dialog and the code waiting for it); a notice can be added at once
        busy = QtWidgets.QApplication.activeModalWidget() is not None or getattr(self, "_disk_asking", False)
        if dialog and busy:
            # not now: a dialog is open
            self._disk_pending |= set(paths)
            self._disk_timer.start(1500)
            return
        inactive = self.isVisible() and not self.isActiveWindow()
        later = set()
        keys = {os.path.normcase(os.path.abspath(p)) for p in paths}
        for p in paths:
            self._watch(p)  # replaced files drop out of the watch
        for e in list(self._docs):
            doc = e["doc"]
            if os.path.normcase(os.path.abspath(doc.path)) not in keys or isinstance(
                doc, _PendingPage
            ):  # read when first shown
                continue
            if doc is self.__dict__.get("_saving_doc"):
                # (its save is under way, ui/dialog/element_save: what another process wrote meanwhile
                # is replaced by this save, so it is looked at after it)
                later.add(doc.path)
                continue
            name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
            if not os.path.exists(doc.path):
                text = "%s was deleted or moved by another program. Save writes it again." % name
                notify(self, text, "warning", detail=doc.path, key="disk:%s" % doc.path)
                from ..editor import notices

                notices.add(doc, "disk", "warning", text, [("Save", lambda d=doc: self._save_notice_doc(d))])
                continue
            if self._reload_waits(doc):
                # (its reload of what the chain after a genie save wrote waits for the user:
                # the tab takes it then)
                later.add(doc.path)
                continue
            if UpdatePagesRun.users_busy and self._chain_may_write(doc):
                # (the chain after a genie save may be saving this element, or this page:
                # what it stored from the version the tab holds is the tab's own, taken at
                # once with the user's changes kept (library._take_chain_write; composed
                # again on the tab's records at its end, which reads a clean tab again);
                # else its end reads it again here, or keeps the user's change, and it is
                # looked at again after it)
                if (
                    doc.changed_on_disk()
                    and (
                        isinstance(doc, ElementDocument)
                        or (doc.dirty and not self._only_automatic_since_saved(doc))
                    )
                    and self._take_chain_write(doc, look_again=False)
                ):
                    continue
                later.add(doc.path)
                continue
            if (
                getattr(doc, "read_only", False)
                and not isinstance(doc, ElementDocument)
                and (
                    self._libraries_being_saved()
                    or UpdatePagesRun.active is not None
                    or UpdatePagesRun.tabs_following
                )
            ):
                # (a page shown read-only is a closed page for the runs, which update its
                # file: their end reads it again (library._reload_read_only_views); looked
                # at again after it)
                later.add(doc.path)
                continue
            try:
                changed = doc.changed_on_disk()
            except Exception:  # noqa: BLE001
                changed = False
            stamp = EditableDocument._stamp(doc.path)
            if not changed or ui_state(doc).disk_asked == stamp:
                continue
            if mode == "always" and not doc.dirty:
                if busy or (doc is self.doc and self.view.busy()) or self._pending_windows(doc):
                    # (reloaded once the dialog is closed, or the user has finished on the
                    # page: an in-place edit or arrow-key moves not committed yet would go
                    # with the version thrown away; values typed in a window of the page and
                    # not applied too)
                    later.add(doc.path)
                    continue
                if self.revert_document(doc, ask=False):
                    notify(
                        self,
                        "%s was changed by another program and was reloaded." % name,
                        detail=doc.path,
                        key="disk:%s" % doc.path,
                    )
                continue
            if not dialog:
                doc.ui.disk_asked = stamp
                self._disk_notice(doc)
                continue
            if doc is self.doc and self.view.busy():
                # (asked once the user has finished on the page: an in-place edit or
                # arrow-key moves not committed yet are changes the box did not know of, and
                # its Reload would throw them away)
                later.add(doc.path)
                continue
            if inactive:
                # (the user is in another program: asked when this window is active again)
                later.add(doc.path)
                continue
            doc.ui.disk_asked = stamp
            self._disk_asking = True
            try:
                self._ask_reload(doc)
            finally:
                self._disk_asking = False
        if later:
            self._disk_pending |= later
            self._disk_timer.start(1500)

    def _disk_notice(self, doc) -> None:
        """The page's notice that another program changed its file: Reload / Ignore, or with
        unsaved changes here Reload and Discard Mine / Keep Mine (saving it then asks before
        it overwrites the other program's changes)."""
        from ..editor import notices
        from ..notify import notify

        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)

        def reload(discard: bool = False):
            # (decided at the click: a page edited since the notice came asks before its
            # changes go, revert's own question; only Reload and Discard Mine does not)
            win = notices.window_of(doc, self)
            return None if win.revert_document(doc, ask=not discard) else False

        if doc.dirty:
            text = "%s was changed by another program, and you have unsaved changes to it here." % name
            acts = [("Reload and Discard Mine", lambda: reload(True)), ("Keep Mine", None)]
        else:
            text = "%s was changed by another program." % name
            acts = [("Reload", reload), ("Ignore", None)]
        notices.add(doc, "disk", "warning", text, acts)
        notify(self, text, "warning", detail=doc.path, key="disk:%s" % doc.path)

    def _save_notice_doc(self, doc):
        """A notice's Save: save ``doc`` (shown first). False keeps the notice."""
        win = self
        k = next((i for i, e in enumerate(win._docs) if e["doc"] is doc), None)
        if k is None:
            return False
        if doc is not win.doc:
            win.show_tab(k)
        return None if win.save() else False

    def _ask_reload(self, doc) -> None:
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        if doc.dirty:
            text = "%s was changed by another program, and you have unsaved changes to it here." % name
            detail = (
                "Reload discards your changes and shows the version on disk. Keep "
                "Mine leaves your version open; saving it then asks before it "
                "overwrites the other program's changes."
            )
            ok_label = "Reload (Discard Mine)"
        else:
            text = "%s was changed by another program." % name
            detail = "Reload it to see that version?"
            ok_label = "Reload"
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Question, "Changed on Disk", text, QtWidgets.QMessageBox.NoButton, self
        )
        box.setInformativeText(detail)
        ok = box.addButton(ok_label, QtWidgets.QMessageBox.AcceptRole)
        keep = box.addButton("Keep Mine" if doc.dirty else "Not Now", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(keep if doc.dirty else ok)
        again = None
        if not doc.dirty:
            # (remembered as Settings > Pages and libraries: reload it / do nothing; a page
            # with unsaved changes is always asked about)
            again = QtWidgets.QCheckBox("Don't ask again", box)  # (owned by the box)
            box.setCheckBox(again)
        self._exec_disk_prompt(box)
        clicked = box.clickedButton()
        if again is not None and again.isChecked() and clicked in (ok, keep):
            from ..prefs.settings import set_disk_reload

            set_disk_reload("always" if clicked is ok else "never")
        if clicked is ok:
            self.revert_document(doc, ask=False)

    @staticmethod
    def _exec_disk_prompt(box) -> None:
        """Show the Changed on Disk question (a seam: tests answer it)."""
        box.exec()

    def revert_document(self, doc=None, ask: bool = True) -> bool:
        """File > Revert to Saved."""
        if doc is None:
            self._show_target_tab()
        doc = doc or self.doc
        if doc is None:
            return False
        k = next((i for i, e in enumerate(self._docs) if e["doc"] is doc), None)
        if k is None:
            return False
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        is_new = isinstance(doc, ElementDocument) and doc.is_new
        if is_new or not os.path.exists(doc.path):
            QtWidgets.QMessageBox.information(
                self,
                "Revert to Saved",
                "%s has never been saved, so there is no saved version to go back to." % name,
            )
            return False
        if ask and doc is self.doc:
            # (an in-place edit or arrow-key moves not committed yet are changes the question
            # asks about: committed later, they would go into the version thrown away)
            self._flush_text_edit()
        if (
            ask
            and doc.dirty
            and not self._confirm(
                "Revert to Saved",
                "Discard your unsaved changes to %s?" % name,
                "Discard and Reload",
                "The page is reloaded from the file on disk. This cannot be undone.",
            )
        ):
            return False
        try:
            if isinstance(doc, ElementDocument):
                new = ElementDocument.open_element(doc.path, doc.element_name)
            else:
                # (the open checks follow: a remembered genie update is applied first)
                new = self._open_page_file(doc.path, checks=True)
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "Revert to Saved",
                self._open_error_text(doc.path, e),
                exc=e,
                next_steps=("Your version is still open here; the file on disk was not read.",),
                actions=(("Show in Explorer", lambda p=doc.path: self._reveal_in_explorer(p)),),
            )
            return False
        if ui_state(doc).supergenie_view and new.supergenie_link() is not None:
            # a page created from a super genie that was opened read-only
            # (SuperGeniePageDialog) stays read-only when it is reloaded
            new.read_only = True
            new.ui.supergenie_view = True
        from ..recovery import AutosaveController

        AutosaveController.forget(doc)
        if doc is self.doc:
            self._stash_current()  # keep the zoom / scroll
        # its windows show the version thrown away
        self._close_page_windows(doc)
        entry = self._docs[k]
        entry["doc"] = new
        # (the underlay stays on the page: ui/editor/underlay.py)
        self._underlay_doc_replaced(doc, new)
        entry["sel"] = []
        # hidden / locked roots are record indices of the OLD version (the
        # file on disk may hold a different page now)
        entry.pop("hidden", None)
        entry.pop("locked", None)
        page = not isinstance(new, ElementDocument)
        if page:
            # (the open checks below compose its genies, as after an open)
            entry["genie_gen"] = editing_mod._LIBRARY_GENERATION[0]
        else:
            # (its first show re-derives its symbols and composes its nested genies, as an
            # element's open does: now when it is shown, else when its tab is shown)
            entry.pop("genie_gen", None)
        if doc is self.doc:
            self._activate_doc(entry)
        self._update_title()
        self._update_actions()
        from ..notify import notify

        # (over the page too when the user asked for it: the page they look at changed)
        notify(
            self,
            "Reloaded %s from disk." % name,
            "success",
            detail=doc.path,
            toast=ask and new is self.doc,
            key="revert:%s" % doc.path,
        )
        from ...infra import logsetup

        logsetup.get("open").info("reverted %s to the version on disk (%s)", name, doc.path)
        if page:
            # the checks a page's open runs (its damage notice, then the template, the
            # symbols, the genies ... that changed since it was saved, each an undoable
            # automatic step), once the canvas has painted it
            self._opened(new, new.path, "view" if ui_state(new).supergenie_view else "edit")
        elif getattr(new, "damage", None):
            self._damage_notice(new)
        return True

    # ------------------------------------------------ crash recovery
    def offer_recovery(self) -> int:
        """Start-up: the recovery copies the last run left behind (it crashed
        or was killed) -- Restore / Discard / Decide Later. Returns the number
        of documents restored."""
        from ...infra import autosave, logsetup
        from ..recovery import RecoveryDialog

        try:
            snaps = autosave.orphans()
        except Exception:  # noqa: BLE001
            return 0
        if not snaps:
            return 0
        dlg = RecoveryDialog(snaps, self)
        dlg.exec()
        if dlg.choice == RecoveryDialog.DISCARD:
            for s in dlg.selected():
                autosave.remove(s.key)
            logsetup.get("autosave").info("discarded %d recovery copies", len(dlg.selected()))
            return 0
        if dlg.choice != RecoveryDialog.RESTORE:
            return 0
        return self.restore_recovered(dlg.selected())

    def restore_recovered(self, snaps) -> int:
        from ...infra import autosave, logsetup

        n, failed = 0, []
        for s in snaps:
            try:
                doc = autosave.restore_document(s)
            except Exception as e:  # noqa: BLE001 -- reported below, the copy is kept
                failed.append((s.path, e))
                continue
            # the session may have reopened the saved version already: the
            # recovered one takes over that (unchanged) tab
            tabs = self._tabs_of(
                "element" if isinstance(doc, ElementDocument) else "page", doc.path, s.element
            )
            clean = [k for k in tabs if not self._docs[k]["doc"].dirty]
            if clean:
                entry = self._docs[clean[0]]
                entry["doc"], entry["sel"] = doc, []
                if self.doc_tabs.currentIndex() != clean[0]:
                    self.doc_tabs.setCurrentIndex(clean[0])
                else:
                    self._activate_doc(entry)
            else:
                # (the new tab's hidden objects: its activation; the tab shown keeps its own)
                self.set_document(doc)
            self._locked.clear()
            autosave.remove(s.key)
            n += 1
            logsetup.get("autosave").info("restored the recovery copy of %s (%s)", s.title, s.path)
        self._update_title()
        self._update_actions()
        if n:
            from ..names import plural
            from ..notify import notify

            notify(
                self,
                "Restored %s with %s unsaved changes. Save %s to keep the "
                "changes." % (plural(n, "page"), "its" if n == 1 else "their", "it" if n == 1 else "them"),
                "success",
                key="recovery",
            )
        if failed:
            from ..messages import error_box

            error_box(
                self,
                "Recover Unsaved Changes",
                "These recovery copies could not be restored:\n\n%s"
                % "\n".join("• %s: %s" % (os.path.basename(p), e) for p, e in failed),
                exc=failed[0][1],
                next_steps=("They are kept and offered again the next time ctgedit starts.",),
            )
        return n

    def report_journal_recovery(self, notes) -> None:
        """Start-up: database writes of the last run that did not finish were
        rolled back (format/journal) -- say so (non-blocking)."""
        if not notes:
            return
        lines, texts, pages = [], [], []
        for n in notes:
            pages += [p for p in self._recovery_pages(n) if p not in pages]
            if n.get("text"):
                # (a note of its own: the pages saved without their database rows)
                texts.append(str(n["text"]))
                continue
            files = ", ".join(os.path.basename(p) for p in n.get("restored", ()))
            lines.append("• %s%s" % (n.get("label", "an update"), " (%s restored)" % files if files else ""))
            skipped = n.get("skipped") or ()
            if skipped:
                lines.append(
                    "   left alone, changed by another program since: %s"
                    % ", ".join(os.path.basename(p) for p in skipped)
                )
            failed = n.get("failed") or ()
            if failed:
                lines.append(
                    "   not undone yet, cannot be written now: %s"
                    % ", ".join(os.path.basename(p) for p in failed)
                )
        parts = []
        if lines:
            parts.append(
                "A ctgedit process stopped while it was updating project databases. Those "
                "unfinished updates were undone, so the databases are as they were "
                "before:\n\n%s\n\nThe pages those updates were for are saved, but not "
                "registered in their project yet: save them again, or run Tools > Update "
                "Pages." % "\n".join(lines)
            )
        parts += texts
        # (the box offers the pages it names and the log; shown without blocking:
        # a start-up note)
        from ..messages import ErrorBox

        actions = []
        if pages:
            actions.append(("Open These Pages", lambda ps=tuple(pages): self.open_files(ps)))
        box = ErrorBox(
            self,
            "Project Databases Restored" if lines else "Pages Not Registered Yet",
            "\n\n".join(parts),
            actions=tuple(actions),
            icon=QtWidgets.QMessageBox.Information,
        )
        box.show_modeless()
        self._journal_box = box

    @staticmethod
    def _recovery_pages(note) -> list:
        """The page files a recovery note is about: the ones it names (``pages``), else the
        page of a 'register page <name>' / 'restore the rows of page <name>' journal in the
        folder of the database files it put back, when that page file exists."""
        pages = [str(p) for p in (note.get("pages") or ()) if p]
        if pages:
            return pages
        label = str(note.get("label") or "")
        name = ""
        for head in ("register page ", "restore the rows of page "):
            if label.startswith(head):
                name = label[len(head) :].strip()
        files = list(note.get("restored") or ()) + list(note.get("skipped") or ())
        if not name or not files:
            return []
        path = os.path.join(os.path.dirname(str(files[0])), name + ".ctg")
        return [path] if os.path.isfile(path) else []


class _ShortcutKeyRecorder(QtCore.QObject):
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


def _mw():
    """ctgedit.ui.main_window (MainWindow is defined there, after this module is imported)."""
    from .. import main_window

    return main_window
