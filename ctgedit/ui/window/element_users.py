"""Used By and Update Users: what uses a library element."""

from __future__ import annotations

from ...infra.paths import folder_key, path_key

from ..messages import ask_with_again, inform

from .open_tabs import all_tabs

from ...infra.logsetup import note_exception as _note_exc


from ..prefs.settings import option

from ..names import name_list

from ..main_window_base import editor_windows

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    contextlib,
    os,
)
from .open_tabs import (
    OpenTabsUpdate,
    TabsHold,
    element_tab,
    page_mtimes,
    page_tab,
    refresh_stale_used_by,
    run_state,
    used_by_windows,
)
from .run_text import follow_words, page_label


class ElementUsers:
    """The pages and genies that use a library element: the Used By window, Update Users and its questions."""

    def _used_by_target(self):
        """(library path or None, library, element) whose usage Used By shows:
        the element being edited, else the one selected placed symbol /
        genie. Cheap (no library read): None when there is none."""
        doc = self.doc
        if doc is None:
            return None
        if isinstance(doc, ElementDocument):
            return None if doc.is_new else (doc.path, doc.library_name, doc.element_name)
        if len(self._sel) != 1:
            return None
        i = self.doc.root_of(self._sel[0]) if self._sel[0] < len(doc.page.objects) else None
        if i is None or not doc.parsed.page.objects[i].subtype_size & 0x1:
            return None
        from ...format.symbols import reference_from_object

        try:
            ref = reference_from_object(doc.parsed.page.objects[i])
        except Exception:  # noqa: BLE001
            _note_exc("element_users._used_by_target")
            ref = None
        if ref is None or ref.ext not in ("ctl", "ctm", "ctt"):
            return None
        return (None, ref.library, ref.element, ref.ext)

    def _used_by_current(self) -> None:
        t = self._used_by_path()
        if t is not None:
            self._show_used_by(*t)

    def _used_by_path(self):
        """(library path, element) of Used By's target, else None (said on the status bar)."""
        t = self._used_by_target()
        if t is None:
            return None
        if t[0] is not None:
            return t[0], t[2]
        path = self.doc.library_path(t[1], t[2], t[3])
        if path is None:
            self.statusBar().showMessage("%s.%s is not on this project's include path." % (t[1], t[2]), 5000)
            return None
        return path, t[2]

    def _update_users_current(self) -> None:
        """Edit > Library > Update Users: of the element being edited, or of the selected
        placed genie / symbol."""
        t = self._used_by_path()
        if t is None:
            return
        doc = self.doc
        if isinstance(doc, ElementDocument) and doc.dirty:
            if not self._confirm(
                "Update Users",
                "%s has unsaved changes." % doc.display_name,
                "Update Anyway",
                "Its users are updated to the version saved "
                "in its library, not to the changes in this tab. Save it first "
                "to update them to those.",
            ):
                return
        self._update_element_users(self._element_users(*t))

    def _show_used_by(self, lib_path: str, element: str) -> None:
        """Library panel / Edit > Library > Used By: every page and library element that uses
        the element, directly or through other genies, and which are out of date
        (io/genieusage); Open, Update Out of Date..., Refresh."""
        key = ("usedby", path_key(lib_path), element.lower())
        open_one = self._dialogs.get(key)
        if open_one is not None:
            # (one window per element: shown again, its users read again first, so a save,
            # a run or another program since is in it)
            with contextlib.suppress(RuntimeError):  # (a window being destroyed)
                open_one.refresh()
            if self._raise_modeless(open_one):
                return
        from .. import lifecycle
        from ..dialog.library_dialogs import ElementUsersDialog

        usage = self._element_users(lib_path, element)
        note = ""
        with contextlib.suppress(Exception):
            from ...format.element import element_kind

            if element_kind(lib_path, element) == "supergenie":
                note = lifecycle.SUPER_NOTE
        dlg = ElementUsersDialog(
            lib_path,
            element,
            usage,
            self,
            note=note,
            on_open=self._open_user,
            on_update=lambda u: self._update_element_users(u),
            on_refresh=lambda: self._element_users(lib_path, element),
        )
        self._show_modeless(dlg, key, scope="window")

    def _follow_used_by_windows(self) -> None:
        """Every open Used By window reads its element's users again."""
        dlgs = used_by_windows()
        if not dlgs:
            return
        for d in dlgs:
            d.stale = True
        if run_state().used_by_due:
            return
        run_state().used_by_due = True
        QtCore.QTimer.singleShot(0, refresh_stale_used_by)

    def _element_users(self, lib_path: str, element: str, title: str = "Used By"):
        """The usage of a library element, or None."""
        return self._element_users_read(lib_path, element, title)[0]

    def _element_users_read(self, lib_path: str, element: str, title: str = "Used By"):
        """(usage, why it's None: '', 'stopped' or 'failed')."""
        from ...io import genieusage as GU
        from ..prefs.job_options import users_read_window_ms
        from ..jobs.job_progress import JobProgress

        stem = os.path.splitext(os.path.basename(lib_path))[0]
        name = "%s.%s" % (stem, element)
        prog = JobProgress(
            self,
            title,
            "Finding what uses %s…" % name,
            phase="index",
            show_after_ms=users_read_window_ms(),
            status_bar=self.statusBar(),
            lets_tabs_update=not self._tabs_wait_for_runs(),
        )

        def tick(i, n, label):
            return prog.tick(i, n, "Reading %s… (%d of %d)" % (label, int(i) + 1, int(n)))

        run_state().users_reads += 1
        try:
            usage = GU.element_users(lib_path, element, progress=tick)
        except Exception:  # noqa: BLE001 -- a hint, never a blocker
            from ...infra import logsetup

            logsetup.note_exception("ui.main_window.element_users")
            return None, "failed"
        finally:
            run_state().users_reads = max(0, run_state().users_reads - 1)
            prog.close()
        if prog.cancelled or getattr(usage, "stopped", False):
            self.statusBar().showMessage(
                "Stopped before every page and library that could use %s was read." % name, 8000
            )
            return None, "stopped"
        return usage, ""

    def _open_user(self, ref) -> None:
        """Open a user of a library element: a page in a tab, an element for editing."""
        if ref.kind == "page":
            self.open_page(ref.path)
        else:
            self._open_element(ref.path, ref.element)

    def _open_element_doc(self, ref):
        """The open tab's document of library element ``ref``, else None: its own tab, never
        an earlier version of it shown read-only (:func:`element_tab`)."""
        want = path_key(ref.path)
        for w, e in all_tabs():
            d = e["doc"]
            if (
                element_tab(d)
                and path_key(d.path or "") == want
                and (d.element_name or "").lower() == ref.element.lower()
            ):
                return d
        return None

    def _update_element_users(self, usage, confirm: bool = True):
        """Update everything that uses an element, in order."""
        hold = TabsHold("Update Users")
        with self._cache_notice_held():
            try:
                return self._update_element_users_run(usage, confirm, hold=hold)
            finally:
                hold.release(self)  # (also after an error)

    def _update_element_users_run(self, usage, confirm: bool = True, hold=None):
        """The run of :meth:`_update_element_users`. ``hold`` = its hold on the other runs
        until its open tabs have followed (:class:`TabsHold`; one of its own when None)."""
        from ...io import updatepages
        from ..jobs.run_stats import RunStats

        hold = hold if hold is not None else TabsHold("Update Users")
        if usage is None:
            return None
        plan = self._users_update_plan(usage)
        if plan is None:
            return None
        if confirm and not self._confirm_users_update(usage, plan):
            return None
        # (the pages open in a tab the run may change: a read-only one is a closed page here,
        # as for the chain after a library save)
        open_paths = self._open_page_paths()
        stats = RunStats.from_settings()
        backup = updatepages.backup_dir()
        marks = self._open_tab_marks()
        open_at_start = page_mtimes(
            p for p in open_paths if path_key(p) in {path_key(u.ref.path) for u in plan.pages}
        )
        rep = self._run_users_update(usage, plan, hold, open_paths, stats, backup)
        saved, tabs, closed_meanwhile = self._users_update_tabs(rep, plan, stats, marks, open_at_start, hold)
        self._tell_users_update(usage, plan, rep, tabs, saved, closed_meanwhile, stats, backup)
        # the Used By windows show the new state (once the result box is closed)
        self._follow_used_by_windows()
        return rep

    def _users_update_plan(self, usage):
        """The plan of Update Users for ``usage``, else None: another run goes on, or nothing
        can be updated (said)."""
        from ...io import genieusage as GU
        from ..jobs.update_run import UpdatePagesRun

        busy = UpdatePagesRun.busy_text("Update the users")
        if busy:
            # (the chain after a genie save, Update Pages and its chain save the same genies
            # and pages: one run at a time)
            inform(self, "Update Users", busy)
            return None
        plan = GU.update_plan(usage)
        # (a page not registered in its project is left, as Update Pages leaves it: a plan of
        # such pages alone updates nothing)
        unreg = [u for u in plan.pages if getattr(u, "registered", None) is False]
        if not plan.elements and len(unreg) == len(plan.pages):
            text = "Everything that uses %s is up to date%s." % (
                usage.target.label,
                "" if not plan.skipped else ", except what is in libraries supplied with Plant SCADA",
            )
            if unreg or any(why != GU.SHIPPED_WHY for _u, why in plan.skipped):
                # (a library file that could not be read whole: its genies are left too)
                text = "Nothing that uses %s can be updated here. Left alone: %s." % (
                    usage.target.label,
                    name_list(
                        ["%s (%s)" % (u.ref.label, why) for u, why in plan.skipped]
                        + ["%s (not registered in its project)" % u.ref.label for u in unreg]
                    ),
                )
            inform(self, "Update Users", text)
            return None
        return plan

    def _confirm_users_update(self, usage, plan) -> bool:
        """Ask before Update Users runs ``plan``: True to go on (and no other run started
        while the question was open, else said)."""
        from ..jobs.update_run import UpdatePagesRun

        def names(users, k=8):
            return name_list([u.ref.label for u in users], k)

        lines = []
        if plan.elements:
            n = len(plan.elements)
            lines.append(
                "First %d library element%s %s saved again, each after the "
                "genies nested in it: %s."
                % (n, "" if n == 1 else "s", "is" if n == 1 else "are", names(plan.elements))
            )
        if plan.pages:
            n = len(plan.pages)
            lines.append(
                "Then %d page%s %s updated: %s."
                % (n, "" if n == 1 else "s", "is" if n == 1 else "are", names(plan.pages))
            )
        if plan.skipped:
            lines.append(
                "Left alone: %s." % "; ".join("%s (%s)" % (u.ref.label, why) for u, why in plan.skipped[:5])
            )
        lines.append("The files are backed up first (Tools > Restore Backup puts them back).")
        if not self._confirm(
            "Update Users",
            "Update what uses %s?" % usage.target.label,
            "Update",
            "\n\n".join(lines),
            icon=QtWidgets.QMessageBox.Question,
        ):
            return False
        # (the question let events through: a run started meanwhile in another window
        # goes first)
        busy = UpdatePagesRun.busy_text("Update the users")
        if busy:
            inform(self, "Update Users", busy)
            return False
        return True

    def _run_users_update(self, usage, plan, hold, open_paths, stats, backup):
        """Save the elements and update the closed pages of ``plan`` behind a progress window,
        ``hold`` taken (``open_paths`` = the pages open in a tab: left to their tabs). The
        run's report."""
        from ...io import genieusage as GU
        from ...render import headless_thumb
        from ..jobs.job_progress import JobProgress
        from ..jobs.update_run import UpdatePagesRun
        from ..prefs.job_options import users_read_window_ms

        try:
            from ...backend.graphics_builder import open_pages

            in_builder = open_pages()
        except Exception:  # noqa: BLE001
            _note_exc("element_users._update_element_users_run")
            in_builder = []
        # (a stop keeps the pages done so far: the button says so, not Cancel)
        prog = JobProgress(
            self,
            "Update Users",
            "Updating…",
            stats=stats,
            cancel="Stop After This Page",
            show_after_ms=users_read_window_ms(),
            status_bar=self.statusBar(),
        )

        def tick(i, n, label):
            if label == "pages":
                # (the genies are all done: the pages step starts, its count with it)
                prog.phase = "pages"
                return prog.tick(0, len(plan.pages), "Updating the pages…")
            prog.phase = "genies"
            return prog.tick(i, n, "Saving %s…" % label)

        def page_tick(i, n, name):
            # (the pages step, page by page)
            prog.phase = "pages"
            return prog.tick(int(i), int(n), "Updating %s… (%d of %d)" % (name, int(i) + 1, int(n)))

        def opened_now(path):
            # (page_tick lets events through: a page opened in another window during the
            # pages step is left, and its tab is updated after the run like the others)
            n = os.path.normcase
            return n(os.path.abspath(path)) in {n(os.path.abspath(p)) for p in self._open_page_paths()}

        # (one run at a time: a genie saved in another window meanwhile waits for this one,
        # and Update Pages and Pack Libraries do not start; tick() lets events through)
        UpdatePagesRun.users_busy = usage.target.label
        # (and until the open tabs have followed it: Simulate, the other runs and a chain
        # that waits in the queue wait for that too, _follow_run_saves)
        hold.take()
        try:
            # (each element and page saved gets a fresh thumbnail, as the chain's helper
            # writes it: one result for both)
            return GU.run_update(
                plan,
                backup=backup,
                open_element=self._open_element_doc,
                skip_pages=open_paths,
                builder_open=in_builder,
                progress=tick,
                page_thumbnail=self._page_thumbnail_for,
                tab_clean=self._tab_clean,
                page_progress=page_tick,
                on_record=lambda r: stats.add_records([r]),
                skip_now=opened_now,
                element_thumbnail=headless_thumb.element_thumbnail,
                save_thumbnail=headless_thumb.page_thumbnail,
            )
        except Exception:
            # (what another program changed while it ran is looked at still; its own writes
            # are this process's: the watch knows them)
            UpdatePagesRun.users_busy = ""
            hold.release(self)
            from .libwatch import chain_saves_settled

            with contextlib.suppress(Exception):
                chain_saves_settled((), first=self)
            raise
        finally:
            UpdatePagesRun.users_busy = ""
            prog.close()

    def _users_update_tabs(self, rep, plan, stats, marks, open_at_start, hold):
        """After an Update Users run: the libraries it wrote, the open tabs that follow it
        (``hold`` released then) and every window's page drawn again. (The elements saved,
        the open tabs' update, the pages closed during the run that nothing updated.)"""
        from ...io import genieusage as GU

        # (a chain that waited in the queue starts when the open tabs have followed:
        # _follow_run_saves releases the hold; started now, it would save libraries and
        # pages while the tabs below are composed)
        self._settle_saved_tabs(rep.tabs_saved, reload=False)
        if rep.pages is not None:
            # (a page shown read-only was a closed page for the run: it reads its file again)
            self._reload_read_only_views(
                r.get("path") for r in rep.pages.records if r.get("outcome") == "updated"
            )
        saved = rep.elements_saved + rep.elements_in_editor
        # (the files that changed while it ran waited for its end: libwatch)
        from .libwatch import chain_saves_settled

        with contextlib.suppress(Exception):
            chain_saves_settled(rep.libraries_written, first=self)
        if saved:
            # (every library it wrote: each folder's catalog, every window's tabs)
            libs = list(rep.libraries_written) or [plan.elements[0].ref.path]
            self._after_library_write(*libs)
            self._library_cache_notice(rep.save_warning)
        dirs = []
        for u in plan.pages:
            d = os.path.dirname(os.path.abspath(u.ref.path))
            if d not in dirs:
                dirs.append(d)
        # (the template's page properties as [Animator] TemplateUpdate says, as the closed
        # pages of the run and the chain after a genie save take them: page_properties None)
        opts = {"genies": True, "templates": True, "symbols": True, "page_properties": None}
        # (the pages open in tabs: behind its own progress window with Cancel, the run's
        # figures going on)
        tabs = (
            self._update_open_documents_shown(
                dirs, opts, "Update Users", stats=stats, only=[u.ref.path for u in plan.pages], marks=marks
            )
            if dirs
            else OpenTabsUpdate()
        )
        # (the element tabs that nest what it saved, and the open pages it did not update in
        # their tab, compose it, as after the chain after a genie save: the pages a Cancel in
        # the open tabs' step left too, and the result says so)
        if tabs.left and saved:
            tabs.left_follow = follow_words(saved, rep.element_kinds)
        self._follow_run_saves(saved, hold)
        if tabs.left_follow:
            self._left_follow(tabs, saved, rep.element_kinds)
        if rep.pages is not None:
            open_at_start.update(
                page_mtimes(
                    r.get("path")
                    for r in rep.pages.records
                    if r.get("outcome") == "skipped_open" and r.get("path")
                )
            )
        closed_meanwhile = self._closed_meanwhile(
            open_at_start,
            GU.pages_using_saved(plan.pages, saved),
            names_from=[u.ref.path for u in plan.pages],
        )
        for w in editor_windows():
            with contextlib.suppress(RuntimeError):
                w._refresh()
        return saved, tabs, closed_meanwhile

    def _tell_users_update(self, usage, plan, rep, tabs, saved, closed_meanwhile, stats, backup) -> None:
        """The result of an Update Users run: logged, then shown."""
        from ...infra import backups, logsetup

        pr = rep.pages
        lines = self._users_update_lines(plan, rep, tabs, saved, closed_meanwhile)
        if (pr is not None and pr.updated) or saved:
            lines.append("Compile the project to see the changes in the runtime.")
            lines.append("Backup: %s (Tools > Restore Backup)" % backup)
        if stats.total_text():
            lines.append(stats.total_text())
        logsetup.get("update-users").info(
            "users of %s: saved %s; pages updated %s%s",
            usage.target.label,
            ", ".join(saved) or "none",
            ", ".join(pr.updated) if pr is not None and pr.updated else "none",
            "; failed: %s" % "; ".join(rep.failed) if rep.failed else "",
        )
        with contextlib.suppress(Exception):
            backups.prune()
        changed = bool((pr is not None and pr.updated) or saved)
        self._report_users_update(
            "\n".join(lines) or "Done.", backup=backup if changed else "", compile_offer=changed
        )

    def _users_update_lines(self, plan, rep, tabs, saved, closed_meanwhile) -> list:
        """The lines of an Update Users result: the elements, the pages, what it left."""
        from ...io import updatepages

        pr = rep.pages
        lines = []
        if saved:
            # (counted by kind, as the chain after a library save counts them)
            lines.append(
                "%s saved again: %s."
                % (self._and_list(self._saved_kinds_parts(saved, rep.element_kinds)), name_list(saved))
            )
        if rep.elements_current:
            n = len(rep.elements_current)
            lines.append(
                "%s already matched what %s: %s."
                % (
                    self._and_list(self._saved_kinds_parts(rep.elements_current, rep.element_kinds)),
                    "it places" if n == 1 else "they place",
                    name_list(rep.elements_current),
                )
            )
        if rep.not_composed:
            # (a save that wrote but kept a copy it could not compose: its old stamp stays,
            # so Used By still calls it out of date; only this line says why)
            lines.append(self._not_composed_text(rep.not_composed))
        if pr is not None or tabs.paths() or tabs.left:
            pr = pr if pr is not None else updatepages.UpdateReport()
            lines += self._users_pages_lines(plan, pr, tabs, closed_meanwhile)
        if rep.super_pages:
            lines.append("Super genie pages created: %s." % ", ".join(rep.super_pages))
        lines += ["Not updated: %s." % x for x in rep.skipped[:10]]
        lines += ["Failed: %s" % x for x in rep.failed[:10]]
        if rep.stopped:
            lines.append("Stopped before the end.")
        return lines

    @staticmethod
    def _users_pages_lines(plan, pr, tabs, closed_meanwhile) -> list:
        """The lines of an Update Users result on its pages: ``pr`` = the closed pages' report,
        ``tabs`` = the open tabs' update."""
        from ..names import plural

        lines = []
        n_tab = len(tabs.updated) + len(tabs.auto)
        lines.append(
            "%s checked, %d out of date, %d updated%s."
            % (
                plural(pr.pages + len(tabs.paths()), "page"),
                pr.stale + tabs.stale,
                len(pr.updated) + n_tab + len(tabs.saved),
                " (%d in %s, not saved yet)" % (n_tab, "its tab" if n_tab == 1 else "their tabs")
                if n_tab
                else "",
            )
        )
        if pr.links_dropped:
            # (a language label entry dropped is no link)
            lines.append(
                "Links to library elements no longer on the page removed: %s." % name_list(pr.links_dropped)
            )
        if pr.unchanged:
            lines.append(
                "Out of date, but nothing on them could be updated (the log says "
                "why): %s." % name_list(pr.unchanged)
            )
        # (two pages of one name in two projects told apart, as the closed ones are)
        lines += tabs.lines(page_label([u.ref.path for u in plan.pages] + tabs.paths() + tabs.left))
        if pr.open_in_builder:
            lines.append(
                "Skipped because they are open in the Graphics Builder: %s"
                % ", ".join(pr.open_in_builder[:10])
            )
        if pr.unregistered:
            n = len(pr.unregistered)
            lines.append(
                "%s not registered in %s project %s left alone."
                % (plural(n, "page file"), "its" if n == 1 else "their", "was" if n == 1 else "were")
            )
        if closed_meanwhile:
            lines.append(
                "Not updated, because they were open in an editor during the run "
                "and closed before it ended. Run Update Users again: %s" % name_list(closed_meanwhile)
            )
        lines += ["Failed: %s" % f for f in pr.failed[:10]]
        # (batch saves: a page can be left, or saved without its database rows)
        if pr.db_in_use:
            lines.append(
                "Not updated because the project database is in use (is Plant "
                "SCADA Studio open?): %s" % ", ".join(pr.db_in_use[:10])
            )
        if pr.changed_meanwhile:
            lines.append(
                "Changed by another save while being updated. Run Update Users "
                "again: %s" % ", ".join(pr.changed_meanwhile[:10])
            )
        if pr.rows_pending:
            lines.append(
                "Saved, but the project database rows are not written yet. The "
                "next Update Pages run writes them: %s" % ", ".join(pr.rows_pending[:10])
            )
        if pr.rows_written:
            lines.append(
                "Project database rows written for pages saved earlier: %s" % ", ".join(pr.rows_written[:10])
            )
        if pr.not_checked:
            lines.append("Not checked, because the run was stopped: %s." % plural(pr.not_checked, "page"))
        lines += ["Warning: %s" % w for w in pr.warnings[:10]]
        return lines

    def _follow_run_saves(self, saved, hold=None) -> None:
        """Update open tabs after a run saved library elements."""
        try:
            if saved:
                for w in editor_windows():
                    with contextlib.suppress(Exception):
                        w._chain_templates_in_tabs(saved)
                with contextlib.suppress(Exception):
                    self._follow_symbols_in_tabs()
            if saved and option("auto_update_genies"):
                # (a tab that took the save: its update finishes that step, files.
                # _fold_into_save_step)
                with contextlib.suppress(Exception), self._updates_for("follow"):
                    self._queue_open_genie_updates()
        finally:
            if hold is not None:
                hold.release(self)

    def _report_users_update(self, text: str, backup: str = "", compile_offer: bool = False) -> None:
        """The Update Users report (a seam: tests read it): the results window
        (_show_run_results), a compile after it when asked."""
        if self._show_run_results(
            "Update Users", text.split("\n"), backup=backup, compile_offer=compile_offer
        ):
            QtCore.QTimer.singleShot(0, self, self.compile_project_command)

    @staticmethod
    def _users_to_update(usage, open_paths) -> tuple:
        """(elements, closed pages) a save's chain updates."""
        from ...io import genieusage as GU

        if usage is None:
            return [], []
        plan = GU.update_plan(usage)
        # (a page not registered in its project is left, as Update Pages leaves it: not
        # counted, _users_left_text names it)
        closed = [
            u
            for u in usage.users
            if u.ref.kind == "page"
            and u.needs_update
            and u.registered is not False
            and path_key(u.ref.path) not in open_paths
        ]
        return list(plan.elements), closed

    def _users_left_text(self, usage, name: str, open_paths=None) -> str:
        """What the chain leaves, for the save prompts."""
        from ..names import plural

        if usage is None:
            return ""
        if open_paths is None:
            open_paths = {path_key(p) for p in self._open_page_paths()}
        out = []
        unreg = [
            u.ref.label
            for u in usage.users
            if u.ref.kind == "page"
            and u.needs_update
            and u.registered is False
            and path_key(u.ref.path) not in open_paths
        ]
        if unreg:
            out.append(
                "%s not registered in %s project %s left alone: %s."
                % (
                    plural(len(unreg), "page"),
                    "its" if len(unreg) == 1 else "their",
                    "is" if len(unreg) == 1 else "are",
                    name_list(unreg, 5),
                )
            )
        if usage.problems:
            n = len(usage.problems)
            out.append(
                "%s could not be read, so what uses %s through %s was not checked. "
                "Show Users names %s."
                % (plural(n, "file"), name, "it" if n == 1 else "them", "it" if n == 1 else "them")
            )
        return " ".join(out)

    def _left_follow(self, tabs, saved, kinds_of) -> None:
        """What a run's follow did after a Cancel."""
        from ...format import template_place as TP

        kinds = dict(kinds_of or {})
        own_kinds = ("template", "super genie")
        for p in list(tabs.left):
            key = path_key(p)
            found = None
            for w in editor_windows():
                with contextlib.suppress(RuntimeError):
                    found = found or next(
                        (
                            (w, e["doc"])
                            for e in w._docs
                            if page_tab(e["doc"]) and path_key(e["doc"].path) == key
                        ),
                        None,
                    )
            if found is None:
                continue
            w, d = found
            try:
                roots = TP.template_roots(d.parsed.page.objects)
                tpl = ("%s.%s" % (roots[0][2].library, roots[0][2].element)).lower() if roots else ""
                link = d.supergenie_link()
                sg = ("%s.%s" % tuple(link)).lower() if link else ""
                waits = any(item[0] is d and tpl in item[3] for item in w.__dict__.get("_idle_docs") or ())
                kept = bool(tpl) and d.template_status()[0] == "stale" and not waits
            except Exception:  # noqa: BLE001 -- the run's words stand for it
                from ...infra import logsetup

                logsetup.note_exception("main_window.left_follow", page=p)
                continue
            mine = [
                s
                for s in saved or ()
                if str(kinds.get(str(s), "genie")) not in own_kinds
                or str(s).lower() == sg
                or (str(s).lower() == tpl and not kept)
            ]
            tabs.left_words[p] = follow_words(mine, kinds)
            if kept:
                tabs.left_template.append(p)

    def _users_nothing_to_update_note(
        self, usage, name: str, said: bool = False, kind: str = "genie"
    ) -> None:
        """Note that nothing uses the saved element."""
        now = {path_key(p) for p in self._open_page_paths()}
        text = self._users_left_text(usage, name, now)
        from ..notify import notify

        tabs = ""
        if said and usage is not None:
            n = sum(
                1
                for u in usage.users
                if u.ref.kind == "page" and u.needs_update and path_key(u.ref.path) in now
            )
            tabs = self._open_tabs_sentence(n, kind)
        if not text:
            if said:
                notify(
                    self,
                    "%s saved.%s Nothing %sthat uses it needs an update."
                    % (name, tabs, "else " if tabs else ""),
                    "info",
                    key="users:%s" % name,
                )
            return
        notify(
            self,
            "%s saved.%s %s" % (name, tabs, text),
            "warning" if usage.problems else "info",
            detail="\n".join(usage.problems[:10]),
            key="users:left:%s" % name,
        )

    #: what the chain after a save of each kind updates, for the note of a stopped search
    #: (:meth:`_users_not_read_note`)
    _LEFT_WORDS = {
        "genie": "the library genies and closed pages that use it",
        "symbol": "the library genies and closed pages that use it",
        "template": "the templates built on it and the closed pages that use it",
    }

    def _users_not_read_note(
        self, lib_path: str, element: str, name: str, kind: str = "genie", failed: bool = False
    ) -> None:
        """Note that reading the users was cancelled."""
        from ..notify import Action, notify

        left = self._LEFT_WORDS.get(kind, self._LEFT_WORDS["genie"])
        tabs = self._open_tabs_sentence(None, kind) if self._other_tabs_open(lib_path, element) else ""
        text = "%s saved. Finding what uses it %s, so %s were not updated.%s" % (
            name,
            "failed" if failed else "was stopped",
            left,
            tabs,
        )

        def update():
            with contextlib.suppress(RuntimeError):
                self._update_element_users(self._element_users(lib_path, element, title="Update Users"))

        def tell():
            with contextlib.suppress(RuntimeError):
                notify(
                    self,
                    text,
                    "warning",
                    actions=(Action("Update Users", update),),
                    detail=lib_path,
                    key="users:%s" % name,
                )

        QtCore.QTimer.singleShot(0, tell)

    @staticmethod
    def _other_tabs_open(lib_path: str, element: str) -> bool:
        """A page or a library element other than ``lib_path``/``element`` (the saved one) is
        open in a tab of an editor window: one that may take the save there
        (:func:`page_tab`, :func:`element_tab`)."""
        own = (path_key(lib_path), (element or "").lower())
        for w in editor_windows():
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                if page_tab(d):
                    return True
                if element_tab(d) and (path_key(d.path or ""), (d.element_name or "").lower()) != own:
                    return True
        return False

    @staticmethod
    def _users_count_text(genies, pages: int, name: str) -> str:
        """'2 library genies, 1 template and 1 closed page use fxnest.n0 and still show its
        old version.' (the save prompts' first line). ``genies``: the library elements (their
        users, genieusage.User: counted by kind, a template or a symbol is not called a genie)
        or a count."""
        from ..names import plural

        if isinstance(genies, int):
            kinds = {"genie": genies} if genies else {}
        else:
            kinds = {}
            for u in genies:
                ref = getattr(u, "ref", u)
                ext = str(getattr(ref, "ext", "") or "").lower()
                el = str(getattr(ref, "element", "") or "")
                k = (
                    "template"
                    if ext == "ctt"
                    else "symbol"
                    if ext == "ctl"
                    else "super genie"
                    if "@super" in el.lower()
                    else "genie"
                )
                kinds[k] = kinds.get(k, 0) + 1
        parts = ElementUsers._kind_parts(kinds)
        if pages:
            parts.append(plural(pages, "closed page"))
        total = sum(kinds.values()) + (pages or 0)
        one = total == 1
        if len(parts) > 2:
            parts = [", ".join(parts[:-1]), parts[-1]]
        return "%s %s %s and still %s its old version." % (
            " and ".join(parts),
            "uses" if one else "use",
            name,
            "shows" if one else "show",
        )

    #: the kinds of library element the save prompts and the chain's results count, with
    #: their words (genieusage.Ref.what)
    _KIND_WORDS = (
        ("genie", "library genie"),
        ("super genie", "super genie"),
        ("template", "template"),
        ("symbol", "library symbol"),
    )
    #: the same kinds where the line names their libraries already ('... in libraries
    #: supplied with Plant SCADA')
    _KIND_WORDS_BARE = (
        ("genie", "genie"),
        ("super genie", "super genie"),
        ("template", "template"),
        ("symbol", "symbol"),
    )

    @staticmethod
    def _kind_parts(kinds: dict, words=None) -> list:
        """['2 library genies', '1 template'] of ``kinds`` ({kind: count}; ``words`` =
        :data:`_KIND_WORDS` or :data:`_KIND_WORDS_BARE`; an unknown kind counts as a
        genie)."""
        from ..names import plural

        words = words or ElementUsers._KIND_WORDS
        known = {k for k, _w in words}
        n = dict(kinds or {})
        for k in [k for k in n if k not in known]:
            n["genie"] = n.get("genie", 0) + n.pop(k)
        return [plural(n[k], word) for k, word in words if n.get(k)]

    @staticmethod
    def _saved_kinds_parts(labels, kinds_of, words=None) -> list:
        """['1 library genie', '2 library symbols'] of the library elements ``labels``
        ('lib.element'; ``kinds_of`` = {label: kind}, the chain's answer 'element_kinds' or
        genieusage.UpdateUsersReport.element_kinds: a label it does not name counts as a
        genie)."""
        kinds: dict = {}
        for s in labels or ():
            k = str((kinds_of or {}).get(str(s), "genie"))
            kinds[k] = kinds.get(k, 0) + 1
        return ElementUsers._kind_parts(kinds, words)

    @staticmethod
    def _not_composed_names(left) -> list:
        """['mylib.hh (its copy of fxnest.n0)', 'mytpl.made@XGA (its copy of the template
        fx_style.normal@XGA)', ...] of ``left`` ([(label, ['lib.genie', ...])]: the copies a
        save, or compose-all's comparison, could not compose again;
        genieusage.nested_copy_labels names them)."""
        out = []
        for row in left or ():
            with contextlib.suppress(TypeError, ValueError):
                label, keys = row
                out.append("%s (its copy of %s)" % (label, ", ".join(str(k) for k in keys)))
        return out

    @staticmethod
    def _not_composed_text(left) -> str:
        """The result line that names the copies of ``left`` (:meth:`_not_composed_names`):
        Update Pages' chain box, Update Users' result. A copy of a template is no nested
        genie: 'nested copies'."""
        return "Nested copies that could not be composed again and stay as stored: %s." % (
            name_list(ElementUsers._not_composed_names(left))
        )

    @staticmethod
    def _and_list(parts) -> str:
        """'a', 'a and b', 'a, b and c'."""
        parts = [p for p in parts if p]
        if len(parts) > 2:
            parts = [", ".join(parts[:-1]), parts[-1]]
        return " and ".join(parts)

    @staticmethod
    def _saved_kind(doc) -> str:
        """'template', 'symbol' or 'genie': the kind of the saved library element ``doc``
        whose users the save prompts and the chain update (a super genie is a genie)."""
        if (getattr(doc, "path", "") or "").lower().endswith(".ctt"):
            return "template"
        return "symbol" if getattr(doc, "kind", "") == "symbol" else "genie"

    #: the save question's text by kind: (its title, what the run does when library elements
    #: are saved first, when only pages are updated)
    _SAVED_TEXT = {
        "genie": (
            "Genie Saved",
            (
                "Update them in the background? The library genies are saved again first, nested ones "
                "first, then the pages. Only the parts that use the genie change, and each file is "
                "backed up first. Open pages are updated in their tabs."
            ),
            "Update them in the background? Only the parts that use the genie change; "
            "each page is backed up first. Open pages are updated in their tabs.",
        ),
        "symbol": (
            "Symbol Saved",
            "Update them in the background? The genies that nest it are saved again "
            "first, then the pages are updated. Each file is backed up first. Open "
            "pages are updated in their tabs.",
            "Update them in the background? The pages are updated. Each page is backed "
            "up first. Open pages are updated in their tabs.",
        ),
        # (the open pages took it at its save, with Settings 'Apply a changed template
        # again': _TEMPLATE_TABS_TEXT says so then)
        "template": (
            "Template Saved",
            "Update them in the background? The templates built on it are saved "
            "again first, then the pages are updated. Only the parts that come from "
            "the template change; each file is backed up first.",
            "Update them in the background? Only the parts that come from the "
            "template change; each page is backed up first.",
        ),
    }
    #: the template question's sentence about the open pages (they take it at its save)
    _TEMPLATE_TABS_TEXT = "Open pages are updated in their tabs."

    def _users_saved_prompt(self, doc) -> None:
        """Ask about updating what uses a saved genie, symbol or template."""
        from ..prefs import settings as S
        from ..dialog.library_dialogs import shipped_library

        kind = self._saved_kind(doc)
        get, put = {
            "genie": (S.genie_save_pages, S.set_genie_save_pages),
            "symbol": (S.symbol_save_pages, S.set_symbol_save_pages),
            "template": (S.template_save_pages, S.set_template_save_pages),
        }[kind]
        mode = get()
        if mode == "never" or shipped_library(doc.path):
            return  # (a shipped one: every project includes it)
        if kind == "template" and mode == "ask" and not option("save_template_warning"):
            return  # (the Graphics Builder's warning is off: no question)
        title, with_elements, pages_only = self._SAVED_TEXT[kind]
        # (asked, or run, only when the chain has something to update: open pages take it in
        # their tabs, a page not registered in its project is left)
        usage, why = self._element_users_read(doc.path, doc.element_name, title=title)
        if usage is None:
            if mode == "always" and why == "failed":
                self._update_closed_pages(doc)  # (its helper finds the users itself)
            elif why in ("stopped", "failed"):
                # (a Cancel in the reading: nothing runs, and a note that stays says so)
                # ('ask' with a reading that failed: said too)
                self._users_not_read_note(
                    doc.path, doc.element_name, doc.display_name, kind=kind, failed=why == "failed"
                )
            return
        open_paths = {path_key(p) for p in self._open_page_paths()}
        genies, closed = self._users_to_update(usage, open_paths)
        if not genies and not closed:
            self._users_nothing_to_update_note(usage, doc.display_name, said=mode == "always", kind=kind)
            return
        if mode == "always":
            self._update_closed_pages(doc)
            return
        # (what the chain updates, by kind: not the pages open in tabs, which take it there,
        # nor the pages it leaves, named below. A template: the Graphics Builder's own offer
        # too, its chain box ticked so the templates built on it are saved first. The open
        # tabs take the save while the question is up: what is open comes first)
        actions = (
            ["Update in Background"] + (["Update Pages…"] if kind == "template" else []) + ["Show Users…"]
        )
        choice, again = ask_with_again(
            self,
            title,
            self._users_count_text(genies, len(closed), doc.display_name),
            actions,
            "Later",
            detail=" ".join(
                x
                for x in (
                    with_elements if genies else pages_only,
                    self._TEMPLATE_TABS_TEXT if kind == "template" and option("auto_update_template") else "",
                    self._users_left_text(usage, doc.display_name, open_paths),
                )
                if x
            ),
            icon="information",
            properties={"ctg_lets_open_tabs_update": not self._tabs_wait_for_runs()},
            exec_=self._exec_prompt,
        )
        if again and choice in ("Update in Background", None):
            # (the choice is remembered; Show Users and Update Pages are no answer to the
            # question: it stays asked. Settings > Pages and libraries changes it)
            put("always" if choice else "never")
        if choice == "Update in Background":
            self._update_closed_pages(doc)
        elif choice == "Show Users…":
            self._show_used_by(doc.path, doc.element_name)
        elif choice == "Update Pages…":
            self._update_pages(chain=True)

    def _open_page_paths(self) -> list:
        """Page files open in tabs a run may update."""
        return [e["doc"].path for w in editor_windows() for e in w._docs if page_tab(e["doc"])]

    def _update_closed_pages(self, doc) -> None:
        """Update what uses the saved genie or symbol, in a worker process."""
        from ..jobs.update_run import UpdatePagesRun

        UpdatePagesRun.users_queue.append((self, (doc.path, doc.element_name, doc.display_name)))
        self._next_users_run()

    def _next_users_run(self) -> None:
        """Start the next waiting chain of _update_closed_pages when nothing else runs
        (another chain, Update Pages and its chain, a run in this process whose open tabs
        have not followed it yet: UpdatePagesRun.tabs_following); else look again in a
        second."""
        from ..jobs.update_run import UpdatePagesRun as R

        if R.users_busy or R.active is not None or R.chain_busy or R.tabs_following:
            if R.users_queue and not R.users_retry:
                R.users_retry = True

                def again():
                    R.users_retry = False
                    with contextlib.suppress(RuntimeError):
                        self._next_users_run()

                QtCore.QTimer.singleShot(1000, again)
                if R.active is not None or R.chain_busy:
                    self.statusBar().showMessage(
                        "Update Pages is running. What uses %s is updated when it has "
                        "finished." % R.users_queue[0][1][2],
                        8000,
                    )
                elif not R.users_busy:
                    self.statusBar().showMessage(
                        R.following_tabs_message(
                            "What uses %s is updated after that." % R.users_queue[0][1][2]
                        ),
                        8000,
                    )
            return
        while R.users_queue:
            win, target = R.users_queue.pop(0)
            live = editor_windows()
            if win not in live:
                # (its window was closed, and is deleted once its own tasks have ended: the
                # chain starts in a window still open, which tells its result)
                win = self if self in live or not live else live[0]
            with contextlib.suppress(RuntimeError):
                if win._start_users_run(*target):
                    return

    @staticmethod
    def _tab_clean(d) -> bool:
        """May the chain's worker save this element tab from its library?"""
        if not d.dirty:
            return True
        if getattr(d, "has_unstepped_changes", False) or getattr(d, "restored_version", None) is not None:
            return False
        return ElementUsers._only_automatic_since_saved(d)

    @staticmethod
    def _only_automatic_since_saved(d) -> bool:
        """Are all of ``d``'s steps since its last save automatic?"""
        depth = getattr(d, "saved_depth", None)
        if depth is None:
            return False
        undo = list(getattr(d, "_undo", None) or ())
        redo = list(getattr(d, "_redo", None) or ())
        now = int(getattr(d, "history_depth", len(undo)))
        if depth <= now:
            steps = undo[len(undo) - (now - depth) :] if now > depth else []
        elif depth - now <= len(redo):
            steps = redo[len(redo) - (depth - now) :]
        else:
            return False
        return all(getattr(c, "automatic", False) for c in steps)


#: the scopes whose usage is being read on a worker thread (:func:`warm_element_users`)
_USERS_WARMING: dict = {}


def warm_element_users(lib_path: str) -> None:
    """Read what uses the elements of ``lib_path`` on a worker thread."""
    if not lib_path:
        return
    key = folder_key(lib_path)
    t = _USERS_WARMING.get(key)
    if t is not None and t.is_alive():
        return

    def work():
        try:
            from ...io import genieusage as GU

            GU.build_index(GU.scope_projects(lib_path))
        except Exception:  # noqa: BLE001 -- a warm-up only: the search reads them itself
            from ...infra.logsetup import note_exception

            note_exception("ui.window.library.warm_element_users")

    import threading
    from ...infra import gcguard

    gcguard.before_worker()  # only the GUI thread may collect garbage
    t = threading.Thread(target=work, name="element-users", daemon=True)
    _USERS_WARMING[key] = t
    t.start()
