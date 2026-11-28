"""Moving a tab to another editor window."""

from __future__ import annotations

from .open_tabs import page_tab_showing

from ...infra.paths import path_key

from ..prefs.settings import option

from ..main_window_base import editor_windows, main_window_class

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    contextlib,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)
from .tabs import (
    PageNotShown,
)


class TabMoves:
    """Moving a tab to another editor window, or to a new one."""

    # -- move a tab to another window ---------------------------------------------------------
    def _fill_move_to_menu(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        others = [w for w in editor_windows() if w is not self and w.isVisible()]
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
        win = main_window_class()()
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
        for w in editor_windows():
            if w is not self and w.isVisible() and w.frameGeometry().contains(global_pos):
                target = w
                break
        e = self._docs[k]
        if target is None:
            target = main_window_class()()
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
        dialogs = [] if isinstance(doc, PendingPage) else self._page_windows(doc)
        for dlg in dialogs:
            # (out of this window's list first: showing another tab here would close them)
            self._release_modeless(dlg)
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None and not isinstance(doc, PendingPage):
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
        main_window_class()._session_seen.add(path_key(doc.path))
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
        if checks and not isinstance(doc, PendingPage):
            # the open checks the window it came from had not run (the template, the
            # symbols, then the genies; the .ctF check after them), after this window
            # painted it
            for path, choice in checks:
                self._queue_post_open(doc, path, choice)
        elif not isinstance(doc, PendingPage):
            if not isinstance(doc, ElementDocument):
                self._queue_ctf_check(doc)
            if ui_state(doc).steps_after_template:
                # (its open's template step went with the old window's job: asked here again,
                # the symbol, placeholder genie and genie steps after it when it lands)
                with contextlib.suppress(Exception):
                    if not self._open_template_step(doc):
                        self._open_steps_after_template(doc)
            elif option("auto_update_genies"):
                # (an element tab too: the genies nested in it, _update_stale_nested; a job
                # the old window dropped is submitted here again)
                with contextlib.suppress(Exception):
                    self._update_stale_genies(doc, first=True)

    def open_documents(self) -> list:
        """The documents of the open tabs; a restored tab not shown yet is
        opened here (a search over every open page reads them all)."""
        for e in list(self._docs):
            if isinstance(e["doc"], PendingPage):
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
            if not isinstance(e["doc"], PendingPage) or getattr(e["doc"], "element", ""):
                continue
            try:
                with open(e["doc"].path, "rb") as fh:
                    data = fh.read()
            except OSError:
                data = None
            # (bytes.lower() folds ASCII only: a non-ASCII name opens the page)
            if data is None or not needle.isascii() or needle in data.lower():
                self._open_pending_now(e)

    def open_shown(self, path: str):
        """Open the page ``path`` in a tab, or bring the tab that shows it to the front (in
        whichever editor window has it); its document. PageNotShown says why it is not
        shown."""
        try:
            self.open_document(path)
        except Exception as e:  # noqa: BLE001 -- the reason goes to the caller
            from ...infra import logsetup

            logsetup.note_exception("tabs.open_shown")
            raise PageNotShown(str(e) or type(e).__name__) from e
        shown = page_tab_showing(path, first=self)
        if shown is not None:
            return shown[1]["doc"]
        raise PageNotShown("it did not open")

    def open_document(self, path: str, ask_supergenie: bool = True) -> None:
        # already open -> just switch to its tab; open in ANOTHER editor window -> that window
        # and tab come to the front (two editable copies of one page would each overwrite
        # the other's save)
        shown = page_tab_showing(path, first=self)
        if shown is not None:
            win, e = shown
            if win is not self:
                win.showNormal() if win.isMinimized() else None
                win.raise_()
                win.activateWindow()
                win.statusBar().showMessage("%s is open in this window." % os.path.basename(path), 6000)
            win.set_document(e["doc"])
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
