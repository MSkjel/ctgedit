"""Saving and loading the window preferences."""

from __future__ import annotations

from ...infra.paths import path_key

from ..main_window_base import (
    ElementDocument,
    QtWidgets,
    PendingPage,
    contextlib,
    default_open_dir,
)

from ...infra.logsetup import note_exception as _note_exc

from ..main_window_base import editor_windows, main_window_class

import json


from ..main_window_base import (
    QtCore,
    QtGui,
    app_settings,
    os,
)


class WindowPrefs:
    """The window's preferences: its layout, its session and the options it reads."""

    # ------------------------------------------------------------ preferences
    @staticmethod
    def _settings() -> QtCore.QSettings:
        return app_settings()

    def _dialog_dir(self, role: str = "open") -> str:
        """Last directory used for a file dialog of ``role``, falling back to
        the current document's folder, then the Example project."""
        from ..remember import last_folder

        here = os.path.dirname(os.path.abspath(self.doc.path)) if self.doc is not None else ""
        return last_folder(role, here or default_open_dir())

    def _remember_dir(self, role: str, path: str) -> None:
        from ..remember import keep_folder

        keep_folder(role, path)

    def save_prefs(self) -> None:
        """Persist the session to QSettings (window geometry + dock layout,
        open file, view toggles)."""
        st = self._settings()
        st.setValue("win/geometry", self.saveGeometry())
        st.setValue("win/state", self.saveState())
        if self.doc is not None:
            st.setValue("session/last_file", os.path.abspath(self.doc.path))
        st.setValue("session/open_files", self.merged_session_files(st, self))
        tabs, current = self.session_tabs(st, self)
        st.setValue("session/tabs", json.dumps(tabs))
        st.setValue("session/current", current)
        # (the pinned pages' paths, also on their own: the rows above carry them too)
        st.setValue("session/pinned", [r["path"] for r in tabs if r.get("pinned")])
        st.setValue("view/grid", self._show_grid)
        st.setValue("view/grid_size", self._grid_size)
        st.setValue("view/grid_y", 0 if self._grid_y is None else self._grid_y)
        st.setValue("view/grid_origin_x", self._grid_origin[0])
        st.setValue("view/grid_origin_y", self._grid_origin[1])
        st.setValue("view/snap", self.act_snap.isChecked())
        st.setValue("view/snap_guides", self.act_snap_guides.isChecked())
        st.setValue("view/guides_visible", self.act_show_guides.isChecked())
        st.setValue("view/crosshair", self.act_crosshair.isChecked())
        st.setValue("view/selection_hud", self.act_selection_hud.isChecked())
        st.setValue("view/status_bar", self.act_status_bar.isChecked())
        st.setValue("view/toolbox", self.act_show_tools.isChecked())
        if getattr(self, "_toolbox", None) is not None:
            st.setValue("view/toolbox_geometry", self._toolbox.saveGeometry())
        st.setValue("view/smart_guides", self.act_smart_guides.isChecked())
        st.setValue("view/smart_guide_tol", self.view.SMART_GUIDE_TOL)
        st.setValue("view/smart_guide_outline", bool(self.view.SMART_GUIDE_OUTLINE))
        st.setValue("view/nudge_step", self._nudge_step)
        st.setValue("view/slow_keys", self.act_slow_keys.isChecked())
        st.setValue("view/slow_mouse", self.act_slow_mouse.isChecked())
        st.setValue("view/thumb", self._show_thumb)
        st.setValue("sel/filter", self.sel_filter.currentIndex())
        st.setValue("sel/marquee", self.sel_marquee.currentIndex())

    @classmethod
    def merged_session_files(cls, st, window=None) -> list:
        """The session's tab list merged with another ctgedit process's."""
        mine: list = []
        windows = [w for w in cls.instances if w is window or w.isVisible()]
        if window is not None and window not in windows:
            windows.insert(0, window)
        for w in windows:
            for e in getattr(w, "_docs", []):
                p = os.path.abspath(e["doc"].path)
                if p not in mine:
                    mine.append(p)
        mine_keys = {os.path.normcase(p) for p in mine}
        keep = []
        for f in st.value("session/open_files", [], type=list) or []:
            f = str(f)
            k = path_key(f) if f else ""
            if f and k not in cls._session_seen and k not in mine_keys and f not in keep:
                keep.append(f)
        return keep + mine

    @classmethod
    def session_tabs(cls, st, window=None):
        """The tabs of the session as JSON rows ({path, element, transform: 6 numbers,
        scroll: [x, y], pinned}) and the index of the current one (-1: none): this process's
        windows' tabs, after the rows another ctgedit process stored for pages this one
        never had open (as :meth:`merged_session_files`)."""
        mine, current = [], -1
        windows = [w for w in cls.instances if w is window or w.isVisible()]
        if window is not None and window not in windows:
            windows.insert(0, window)
        keys = set()
        for w in windows:
            for e in getattr(w, "_docs", []):
                d = e["doc"]
                element = (
                    d.element_name if isinstance(d, ElementDocument) else getattr(d, "element", "") or ""
                )
                path = os.path.abspath(d.path)
                key = (os.path.normcase(path), element.lower())
                if key in keys:
                    continue
                keys.add(key)
                row = {"path": path, "element": element, "pinned": bool(e.get("pinned"))}
                if d is w.doc:
                    w._stash_view(e)
                tr, sc = e.get("transform"), e.get("scroll")
                if tr is not None and sc is not None:
                    row["transform"] = [tr.m11(), tr.m12(), tr.m21(), tr.m22(), tr.dx(), tr.dy()]
                    row["scroll"] = [int(sc[0]), int(sc[1])]
                if w is window and d is w.doc:
                    current = len(mine)
                mine.append(row)
        keep = []
        # (another process's rows: of the pages its session/open_files still lists)
        listed = {path_key(str(f)) for f in st.value("session/open_files", [], type=list) or [] if f}
        for row in session_rows(st):
            k = path_key(row["path"])
            if (
                k not in cls._session_seen
                and k in listed
                and (k, (row.get("element") or "").lower()) not in keys
            ):
                keep.append(row)
        return keep + mine, (current + len(keep) if current >= 0 else -1)

    def apply_canvas_options(self) -> None:
        """Apply the canvas settings this window keeps."""
        from ..prefs.settings import OPEN_ZOOM, OPEN_ZOOM_DEFAULT, choice, paste_offset

        st = self._settings()
        v = self.view
        tol = type(v).SMART_GUIDE_TOL
        try:
            v.SMART_GUIDE_TOL = float(st.value("view/smart_guide_tol", tol, type=float))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
            v.SMART_GUIDE_TOL = tol
        v.SMART_GUIDE_OUTLINE = bool(st.value("view/smart_guide_outline", True, type=bool))
        try:
            self._nudge_step = int(st.value("view/nudge_step", 1, type=int))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
            self._nudge_step = 1
        # (a step changed in Settings while Cursor Keys Slow Speed is on would seem
        # ignored: say so; the first read is the window's start, not a change)
        seen = getattr(self, "_nudge_step_seen", None)
        if (
            seen is not None
            and seen != self._nudge_step
            and getattr(self, "act_slow_keys", None) is not None
            and self.act_slow_keys.isChecked()
        ):
            from ..notify import notify

            notify(
                self,
                "The arrow key step is %d px, but Cursor Keys Slow Speed (Tools > "
                "Advanced) is on, so the arrow keys move 1 px until it is unticked." % self._nudge_step,
                "info",
            )
        self._nudge_step_seen = self._nudge_step
        self._paste_offset = paste_offset()
        self._open_zoom = choice("opt/open_zoom", OPEN_ZOOM, OPEN_ZOOM_DEFAULT)

    #: the View menu's toggles that Settings > Canvas > "Grid and guidelines" shows too: the
    #: stored key, the action, the canvas's setter (None: the window's own _show_grid)
    VIEW_TOGGLES = (
        ("view/grid", "act_grid", None),
        ("view/snap", "act_snap", "set_snap"),
        ("view/snap_guides", "act_snap_guides", "set_snap_guides"),
        ("view/guides_visible", "act_show_guides", "set_guides_visible"),
        ("view/smart_guides", "act_smart_guides", "set_smart_guides"),
    )

    def apply_view_toggles(self) -> None:
        """Apply the View menu's toggles from settings."""
        st = self._settings()
        for key, act, setter in self.VIEW_TOGGLES:
            on = bool(st.value(key, True, type=bool))
            getattr(self, act).setChecked(on)
            if setter is None:
                self._show_grid = on
            else:
                getattr(self.view, setter)(on)

    def restore_session(self, reopen: bool = True, defer: bool = False) -> bool:
        """Restore geometry, dock layout, view settings and optionally the last file."""
        st = self._settings()
        geo = st.value("win/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = st.value("win/state")
        if state is not None:
            # (a stored layout names the Simulate dock too: it stays as the simulation
            # docked here has it, whatever the stored state says)
            dock = getattr(self, "_sim_dock", None)
            shown = dock is not None and not dock.isHidden()
            self.restoreState(state)
            if dock is not None:
                with contextlib.suppress(Exception):
                    dock.setFloating(False)
                    dock.setVisible(shown)
        # (the grid, snap, guideline and smart guide toggles: every window reads them when it
        # is made too)
        self.apply_view_toggles()
        gy = int(st.value("view/grid_y", 0, type=int) or 0)
        self._apply_grid(
            st.value("view/grid_size", 8, type=int),
            gy or None,
            st.value("view/grid_origin_x", 0, type=int),
            st.value("view/grid_origin_y", 0, type=int),
        )
        self.act_crosshair.setChecked(st.value("view/crosshair", False, type=bool))
        self.act_selection_hud.setChecked(st.value("view/selection_hud", True, type=bool))
        self._toggle_crosshair()
        self.act_status_bar.setChecked(st.value("view/status_bar", True, type=bool))
        self._toggle_status_bar()
        if st.value("view/toolbox", False, type=bool):
            self.act_show_tools.setChecked(True)
            # shown once the editor window is (restore runs before its show)
            QtCore.QTimer.singleShot(0, self, self._toggle_toolbox)
        # Settings > Canvas and Editing (every window reads them when it is made too; the
        # wheel's and the other navigation choices are the canvas's own: apply_nav_options)
        self.apply_canvas_options()
        from ..prefs.settings import apply_drawing_options, apply_undo_budget

        apply_drawing_options()
        apply_undo_budget()
        self.act_slow_keys.setChecked(st.value("view/slow_keys", False, type=bool))
        self.act_slow_mouse.setChecked(st.value("view/slow_mouse", False, type=bool))
        self.view.set_slow_mouse(self.act_slow_mouse.isChecked())
        self._show_thumb = st.value("view/thumb", False, type=bool)
        self.act_thumb.setChecked(self._show_thumb)
        self._update_colour_button()
        self.sel_filter.setCurrentIndex(st.value("sel/filter", 0, type=int))
        self.sel_marquee.setCurrentIndex(st.value("sel/marquee", 0, type=int))
        if reopen:
            # an element tab stores its LIBRARY (.ctl/.ctm/.ctt) path: element
            # tabs are not reopened by the session, so they are skipped quietly
            # (open_document would refuse the library as "not a page")
            def _page(f):
                return bool(f) and os.path.splitext(f)[1].lower() not in (".ctl", ".ctm", ".ctt")

            last = st.value("session/last_file", "", type=str)
            if not _page(last):
                last = ""
            stored = [str(f) for f in st.value("session/open_files", [], type=list)]
            # this process takes the stored list over: an entry that does not
            # reopen (a deleted page, a library tab) is not kept for ever as
            # "another editor's" (merged_session_files)
            main_window_class()._session_seen.update(path_key(f) for f in stored + [last] if f)
            rows = session_rows(st)
            if rows:
                # the tabs as they were: their order, pins, library elements and views
                return self._restore_tab_rows(rows, st, stored, defer)
            files = [f for f in stored if _page(f)]
            return self._restore_tabs(files, last, defer)
        return False

    def _restore_tab_rows(self, rows: list, st, stored: list, defer: bool = False) -> bool:
        """The session's tabs from its rows (session/tabs, :meth:`session_tabs`): pinned ones
        first, each opened when first shown, the current one (session/current) shown. A page
        listed in session/open_files but not in the rows (another editor window's) comes
        after them."""
        failures = []
        try:
            current = int(st.value("session/current", -1))
        except TypeError, ValueError:  # swallow-ok: a garbled index shows the last tab
            current = -1
        cur_row = rows[current] if 0 <= current < len(rows) else None
        if stored:
            # session/open_files says which files were open last (it is kept current while
            # ctgedit runs, so a crash keeps it); the rows add their order, pins and views
            listed = {path_key(f) for f in stored if f}
            rows = [r for r in rows if path_key(r["path"]) in listed]
        known = {(path_key(r["path"]), (r.get("element") or "").lower()) for r in rows}
        for f in stored:
            if f and os.path.splitext(f)[1].lower() == ".ctg" and (path_key(f), "") not in known:
                rows.append({"path": f, "element": "", "pinned": False})
        open_keys = {
            (
                path_key(e["doc"].path),
                (getattr(e["doc"], "element_name", "") or "").lower(),
            )
            for e in self._docs
        }
        pinned = {path_key(str(p)) for p in st.value("session/pinned", [], type=list) or [] if p}
        for r in rows:
            if not r.get("element") and path_key(r["path"]) in pinned:
                r["pinned"] = True
        ordered = [r for r in rows if r.get("pinned")] + [r for r in rows if not r.get("pinned")]
        cur_entry = None
        for r in ordered:
            path, element = r["path"], r.get("element") or ""
            key = (path_key(path), element.lower())
            if key in open_keys:
                continue
            open_keys.add(key)
            if not os.path.exists(path):
                failures.append((path, FileNotFoundError(2, "No such file", path)))
                continue
            view = None
            tr, sc = r.get("transform"), r.get("scroll")
            if isinstance(tr, list) and len(tr) == 6 and isinstance(sc, list) and len(sc) == 2:
                with contextlib.suppress(TypeError, ValueError):
                    view = (
                        QtGui.QTransform(
                            float(tr[0]), float(tr[1]), float(tr[2]), float(tr[3]), float(tr[4]), float(tr[5])
                        ),
                        (int(sc[0]), int(sc[1])),
                    )
            entry = self._add_pending_tab(path, element, bool(r.get("pinned")), view)
            if not element:
                self._push_mru(path)
            if r is cur_row:
                cur_entry = entry
        if not self._docs:
            if failures:
                QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
            return False
        if cur_entry is None:
            cur_entry = self._docs[-1]
        self._switching = True
        try:
            self.doc_tabs.setCurrentIndex(self._entry_index(cur_entry))
        finally:
            self._switching = False
        if defer:

            def later(fl=failures):
                self._open_restored(fl)
                if fl:
                    self.report_open_failures(fl)

            QtCore.QTimer.singleShot(0, self, later)
            return True
        opened = self._open_restored(failures)
        if failures:
            QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
        return opened and self.doc is not None

    def _restore_tabs(self, files: list, last: str, defer: bool = False) -> bool:
        """Restore the session's tabs. Only the active page is read now."""
        failures = []  # a page that was open and does not reopen is REPORTED
        last_key = os.path.normcase(last or "")
        open_keys = {path_key(e["doc"].path) for e in self._docs}
        opened = False
        for f in files:
            if not f or os.path.normcase(f) == last_key:
                continue
            k = path_key(f)
            if k in open_keys:
                continue
            open_keys.add(k)
            if not os.path.exists(f):
                failures.append((f, FileNotFoundError(2, "No such file", f)))
                continue
            self._add_pending_tab(f)
            self._push_mru(f)
            opened = True
        was_open = any(os.path.normcase(f) == last_key for f in files)
        if last and (os.path.exists(last) or was_open):
            if defer and os.path.exists(last) and path_key(last) not in open_keys:
                entry = self._add_pending_tab(last)  # the active tab last
                self._push_mru(last)
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(self._entry_index(entry))
                finally:
                    self._switching = False
                opened = True
            else:
                try:
                    self.open_document(last, ask_supergenie=False)  # the active tab last
                    opened = True
                except Exception as e:  # noqa: BLE001
                    _note_exc("shell._restore_tabs")
                    failures.append((last, e))
        if self.doc is None and self._docs:
            # the active page did not come back: the LAST tab is shown, as when
            # every tab was opened in turn (addTab had made the first current)
            self._switching = True
            try:
                self.doc_tabs.setCurrentIndex(len(self._docs) - 1)
            finally:
                self._switching = False
        if defer:
            # the window shows first; the active page opens on the next turn
            def later(fl=failures):
                self._open_restored(fl)
                if fl:
                    self.report_open_failures(fl)

            QtCore.QTimer.singleShot(0, self, later)
            return opened
        if self.doc is None and self._docs:
            opened = self._open_restored(failures)
        if failures:
            # after the window is shown (the app shows it right after this)
            QtCore.QTimer.singleShot(0, self, lambda f=failures: self.report_open_failures(f))
        return opened and self.doc is not None

    def _open_restored(self, failures: list) -> bool:
        """Show the current tab of a restored session, opening it (a page
        that no longer opens is dropped: the last remaining tab is tried
        next). False when none opened."""
        while self._docs:
            k = self.doc_tabs.currentIndex()
            if not 0 <= k < len(self._docs):
                k = len(self._docs) - 1
            entry = self._docs[k]
            if not isinstance(entry["doc"], PendingPage):
                if entry["doc"] is not self.doc:
                    self._activate_doc(entry)
                return True
            if self._materialise(entry, failures):
                entry["post_open"] = True
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(k)
                finally:
                    self._switching = False
                self._activate_doc(entry)
                return True
            if self._docs:  # dropped: the last tab next
                self._switching = True
                try:
                    self.doc_tabs.setCurrentIndex(len(self._docs) - 1)
                finally:
                    self._switching = False
        return False

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        # every open page with unsaved edits is asked about (its tab shown);
        # a page whose save fails keeps the window open -- but the other pages
        # are still asked, so one read-only / locked page never costs their edits
        self._flush_text_edit()
        # two or more unsaved pages: one Save Changes list (save_changes.SaveChangesDialog)
        dirty = [(en, self._pending_windows(en["doc"])) for en in self._docs]
        dirty = [(en, p) for en, p in dirty if en["doc"].dirty or p]
        if len(dirty) >= 2:
            if not self._save_changes_list(dirty, "closing"):
                e.ignore()
                return
            self._finish_close(e)
            return
        unsaved = []
        for k in range(len(self._docs) - 1, -1, -1):
            doc = self._docs[k]["doc"]
            # typed values of its windows not applied yet count as its edits
            pending = self._pending_windows(doc)
            if not doc.dirty and not pending:
                continue
            self.doc_tabs.setCurrentIndex(k)
            r = self._ask_save_on_close(doc, pending, "closing")
            if r == QtWidgets.QMessageBox.Save:
                try:
                    ok = self._apply_pending_windows(pending) and (not doc.dirty or self.save())
                except Exception as ex:  # noqa: BLE001 -- never close over a failed save
                    _note_exc("shell.closeEvent")
                    ok = False
                    with contextlib.suppress(Exception):
                        self._save_failed(doc.path, ex, offer_save_as=False)
                if not ok:
                    unsaved.append(doc)
            elif r == QtWidgets.QMessageBox.Cancel:
                e.ignore()
                return
            else:  # Discard: its recovery copy goes too
                from ..recovery import AutosaveController

                AutosaveController.forget(doc)
        if unsaved:
            e.ignore()  # a refused / failed save keeps the window
            for k, entry in enumerate(self._docs):
                if entry["doc"] is unsaved[-1]:
                    self.doc_tabs.setCurrentIndex(k)
                    break
            self.statusBar().showMessage(
                "The window stays open. Not saved: %s"
                % ", ".join(os.path.basename(d.path) for d in reversed(unsaved)),
                12000,
            )
            return
        self._finish_close(e)

    def _finish_close(self, e: QtGui.QCloseEvent) -> None:
        """The window closes (every unsaved page was saved or discarded): the views of its
        tabs are kept for the next open, the session is written, its windows go."""
        with contextlib.suppress(Exception):
            self._remember_all_views()
        # only the last window writes the session (a closing child must not
        # clobber the layout still open elsewhere)
        if len([w for w in editor_windows() if w.isVisible()]) <= 1:
            with contextlib.suppress(Exception):
                self.save_prefs()
        if self in editor_windows():
            main_window_class().instances.remove(self)
        # its windows go with it (another editor window may keep the program
        # running: Grid Setup / Goto Object / a sheet must not float on,
        # acting on a closed window)
        with contextlib.suppress(Exception):
            self._close_modeless()
        # its library watch stops (window/libwatch.py)
        with contextlib.suppress(Exception):
            self._close_library_watch()
        # a simulation docked in this window stops, as closing the Simulate window does
        with contextlib.suppress(Exception):
            from ..simulate.window import simulate_window

            sw = simulate_window(create=False)
            if sw is not None:
                sw.release_host(self)
        for name in ("_find_dlg", "_unresolved_dlg"):
            d = getattr(self, name, None)
            if d is not None:
                with contextlib.suppress(RuntimeError):
                    d.close()
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None:
            jobs.shutdown()  # (genie updates of pages that close now)
        from ..dialog import element_save

        element_save.stop(self)  # (its helper for the saves of large elements)
        # (nothing lands on its pages any more, and they are let go)
        try:
            self._release_closed_window()
        except Exception:  # noqa: BLE001 -- the window closes anyway
            from ...infra import logsetup

            logsetup.note_exception("ui.window.shell.release_closed_window")
        e.accept()
        self._free_closed_window()

    #: the window's timers that act on its documents (stopped when it closes)
    _DOC_TIMERS = (
        "_idle_timer",
        "_after_run_timer",
        "_lib_timer",
        "_disk_timer",
        "_ctf_timer",
        "_nudge_timer_obj",
        "_hud_timer",
        "_window_sync_timer",
        "_chain_named_timer",
    )

    def _release_closed_window(self) -> None:
        """Clean up after a window closes."""
        self._window_closed = True
        for name in self._DOC_TIMERS:
            t = self.__dict__.get(name)
            if t is not None:
                with contextlib.suppress(RuntimeError):
                    t.stop()
        self._idle_docs, self._after_run_docs = [], []
        # (and those waiting for the running chain's answer to name them, or whose reload
        # of what its helper wrote waits for the user: files._when_chain_names,
        # library._chain_writes_waiting)
        self._chain_named_docs, self._chain_waits = [], {}
        self._post_open_pending = []
        self._lib_pending = set()
        self._disk_pending = set()
        q = self.__dict__.get("_ctf_queue")
        if q:
            q.clear()
        from ..recovery import AutosaveController

        for entry in list(self._docs):
            doc = entry["doc"]
            if not isinstance(doc, PendingPage):
                with contextlib.suppress(Exception):
                    doc.stop_ctf_precheck()
                with contextlib.suppress(Exception):
                    self._underlay_doc_closed(doc)
                with contextlib.suppress(Exception):
                    AutosaveController.forget(doc)
            with contextlib.suppress(Exception):
                self._drop_tab_scene(entry)
        self._switching = True
        try:
            self._docs[:] = []
            with contextlib.suppress(RuntimeError):
                while self.doc_tabs.count():
                    self.doc_tabs.removeTab(self.doc_tabs.count() - 1)
        finally:
            self._switching = False
        self.__dict__.pop("_mru_entries", None)
        self.__dict__.pop("_scene_order", None)
        self.doc = None
        self._sel = []
        with contextlib.suppress(Exception):
            self.scene.clear_page()
        # (the panels let the page go too)
        with contextlib.suppress(Exception):
            self.objects_dock.set_page(None, [])
        with contextlib.suppress(Exception):
            self.props.set_target(None, [])
        with contextlib.suppress(Exception):
            self.tags_dock.set_project_dir(None)
        with contextlib.suppress(Exception):
            self._auto_bar.show_for(None)
        self._hist_sel = None
        self._cap_arm = None

    def _free_closed_window(self) -> None:
        """A closed window is deleted (its widgets and scenes go) once no task of its own
        runs: the chain after a library save or an Update Pages run started here goes on on
        a timer of the window, and says its result in another window. Outside a running
        event loop (scripts, tests) the window is left as it is: nothing would delete it."""
        if not self.__dict__.get("_window_closed") or any(w is self for w in editor_windows()):
            return
        if QtCore.QThread.currentThread().loopLevel() == 0:
            return
        from ..jobs import tasks

        if tasks.running(self):
            from ..prefs.job_options import poll_ms

            QtCore.QTimer.singleShot(poll_ms(), self, self._free_closed_window)
            return
        self.deleteLater()


def session_rows(st) -> list:
    """session/tabs as a list of rows with a 'path' (a garbled value: none)."""
    raw = st.value("session/tabs", "")
    if not raw:
        return []
    try:
        rows = json.loads(str(raw))
    except ValueError:  # swallow-ok: a garbled value reads as no rows
        return []
    return (
        [r for r in rows if isinstance(r, dict) and isinstance(r.get("path"), str) and r["path"]]
        if isinstance(rows, list)
        else []
    )
