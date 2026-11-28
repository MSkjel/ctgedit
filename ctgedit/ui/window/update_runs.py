"""Update Pages runs, the open tabs a run updates, and Restore Backup."""

from __future__ import annotations

from ...infra.paths import folder_key, path_key

from .open_tabs import OpenTabsUpdate, TabsHold, all_tabs, history_depth, page_tab

from ...infra.logsetup import note_exception as _note_exc

from .run_text import follow_words, full_stop, unique_names

from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from ..main_window_base import editor_windows, main_window_class


from ..prefs.settings import option

from ..names import name_list


from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    app_settings,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)
from ..messages import inform, warn


class UpdateRuns:
    """Tools > Update Pages, the open documents its runs follow, and Tools > Restore Backup."""

    # -------------------------------------------------- open documents and Update Pages runs
    @staticmethod
    def _open_tab_marks() -> dict:
        """{id(page document): (the document, how many automatic changes it had)} of every
        page open in a tab: taken when Update Pages or Update Users starts, so its step for
        the open pages can tell an automatic update that landed during the run
        (_update_open_documents)."""
        out = {}
        for w, e in all_tabs():
            d = e["doc"]
            if page_tab(d):
                out[id(d)] = (d, ui_state(d).auto_count)
        return out

    def _open_documents_in(self, dirs, only=None) -> list:
        """The pages open in tabs that Update Pages' open-tabs step handles."""
        want = {path_key(d) for d in dirs}
        only_n = None if only is None else {path_key(p) for p in only}
        out = []
        for w, e in all_tabs():
            d = e["doc"]
            if not page_tab(d) or folder_key(d.path) not in want:
                continue
            if only_n is not None and path_key(d.path) not in only_n:
                continue
            out.append((w, e, d))
        return out

    def _update_open_documents_shown(
        self, dirs, opts, title: str, stats=None, only=None, marks=None
    ) -> "OpenTabsUpdate":
        """:meth:`_update_open_documents` behind a window-modal progress window (``title``,
        the 'tabs' phase of ``stats``: the run's figures, ui/jobs/job_progress) with Cancel
        between the pages; no window when no page of ``dirs`` is open in a tab."""
        from ..jobs.job_progress import JobProgress

        docs = self._open_documents_in(dirs, only)
        if not docs:
            return OpenTabsUpdate()
        n = len(docs)
        prog = JobProgress(
            self,
            title,
            "Updating the pages open in tabs…",
            phase="tabs",
            stats=stats,
            total=n,
            cancel="Stop After This Page",
        )

        def tick(i, total, name):
            return prog.tick(i, total, "Updating %s in its tab… (%d of %d)" % (name, int(i) + 1, int(total)))

        try:
            return self._update_open_documents(dirs, opts, only=only, marks=marks, progress=tick)
        finally:
            prog.close()

    def _update_open_documents(self, dirs, opts, only=None, marks=None, progress=None) -> "OpenTabsUpdate":
        """Update Pages for the pages open in tabs."""
        from ...infra import logsetup
        from ...io import updatepages

        res = OpenTabsUpdate()
        genies, templates = bool(opts.get("genies")), bool(opts.get("templates"))
        shown_changed: list = []
        untouched: list = []
        log = logsetup.get("update-pages")
        docs = self._open_documents_in(dirs, only)
        for k, (w, e, d) in enumerate(docs):
            if (
                progress is not None
                and progress(k, len(docs), os.path.splitext(os.path.basename(d.path))[0]) is False
            ):
                res.left = [x[2].path for x in docs[k:]]
                break
            jobs = getattr(w, "_genie_jobs", None)
            # (the open's template step still in the worker, and the open's steps waiting for
            # it: done here first, in their place in the order; a kept template job would land
            # after this step's symbols and genies)
            owed = self._open_steps_owed(d, opts, jobs)
            dropped = (
                jobs.cancel_automatic(d, genies=genies, templates=templates or "templates" in owed)
                if jobs is not None
                else 0
            )
            if dropped:
                with contextlib.suppress(RuntimeError):
                    if genies:
                        w._genies_updated(d)  # (its amber marks go)
                    # (and the dropped job's 'you can go on working' line)
                    w._clear_auto_update_line(d, genies=genies, templates=templates or "templates" in owed)
            # (a tab with nothing of the user's on it: saved once this step has updated it)
            if self._nothing_of_users(w, d):
                untouched.append((w, e, d))
            # (the steps the undo cap dropped count: a step that drops an old one is
            # still a step)
            steps = history_depth(d)
            failed, found = self._update_open_document(d, opts, res, owed=owed)
            for what, command in found.owed:
                # (the open's steps it did: automatic changes, as the open says them)
                with contextlib.suppress(RuntimeError):
                    w._note_auto_change(d, what, command)
            if failed:
                res.failed.append(d.path)
                log.warning(
                    "%s: open in a tab, could not update its %s there (the swallowed errors above say why)",
                    d.path,
                    ", ".join(failed),
                )
                if dropped:
                    # (the automatic update was dropped: the next open or tab switch
                    # tries again)
                    ui_state(d).genie_sources_done.clear()
            if history_depth(d) != steps:
                if not failed:
                    # (changed by the open's own steps alone: the automatic update)
                    (res.updated if history_depth(d) - steps > len(found.owed) else res.auto).append(d.path)
                # its windows re-read the page, as after Replace (the
                # page shown now, and the others when they are shown)
                if d is w.doc:
                    shown_changed.append(w)
                with contextlib.suppress(RuntimeError):
                    w._mark_modeless_reason([d], "replace")
            elif not failed:
                # (an automatic change since the run started, also one that became part of
                # the step before it, files._note_auto_change: a page opened during the
                # run counts all of its own)
                st = ui_state(d)
                mark = marks.get(id(d)) if marks is not None else (d, st.auto_count)
                landed = (
                    st.auto_count > mark[1] if mark is not None and mark[0] is d else bool(st.auto_changes)
                )
                if landed:
                    res.auto.append(d.path)
                elif found.due:
                    # out of date, and nothing on it changed: said why, as the helper
                    # says it of a closed page ('unchanged')
                    res.unchanged.append(d.path)
                    log.info(
                        "%s: open in a tab, out of date, but nothing on it changed: %s",
                        d.path,
                        updatepages.unchanged_reason(
                            set(found.genies), found.genies, found.template, found.supergenie, found.symbols
                        ),
                    )
                else:
                    res.nothing.append(d.path)
            if not failed and genies:
                # (its tab need not look again for changed genies when it is shown:
                # a run without genies leaves that look to the tab)
                e["genie_gen"] = editing_mod.LIBRARY_GENERATION[0]
        for w in shown_changed:
            with contextlib.suppress(RuntimeError):
                w._sync_modeless("replace")
        self._save_untouched(untouched, res, log)
        if res.paths():
            log.info(
                "open pages: updated in their tabs %s; already updated there by the "
                "automatic update %s; out of date, nothing changed %s; nothing to "
                "update %s; could not be updated %s",
                res.updated or "none",
                res.auto or "none",
                res.unchanged or "none",
                res.nothing or "none",
                res.failed or "none",
            )
        return res

    @staticmethod
    def _nothing_of_users(w, d) -> bool:
        """Open page ``d`` (in window ``w``) has no change of the user's: unchanged since its
        open or save, or changed by the automatic update alone; no window with changes not
        applied yet, no text being typed on it; not read-only."""
        if getattr(d, "read_only", False) or isinstance(d, ElementDocument):
            return False
        try:
            if d is w.doc and w.view.text_editing():
                return False
            if w._pending_windows(d):
                return False
            return not d.dirty or w._only_auto_changes(d)
        except RuntimeError, AttributeError:  # swallow-ok: a window going: not saved here
            return False

    @staticmethod
    def _save_untouched(untouched, res: "OpenTabsUpdate", log) -> None:
        """Save the open pages this step updated that had nothing of the user's on them
        (Update Pages then leaves no page it changed unsaved for no reason); one that does not
        save stays as it is, unsaved, listed as updated."""
        for w, _e, d in untouched:
            for bucket in (res.updated, res.auto):
                if d.path in bucket and d.dirty:
                    errs: list = []
                    try:
                        ok = w._save_document(d, errs)
                    except RuntimeError:  # swallow-ok: its window closed meanwhile: not saved
                        ok = False
                    if ok and not d.dirty:
                        bucket.remove(d.path)
                        res.saved.append(d.path)
                        with contextlib.suppress(RuntimeError):
                            w._update_title()  # (its tab's unsaved mark goes)
                    else:
                        log.warning("%s: updated in its tab but not saved there: %s", d.path, "; ".join(errs))
                    break

    @staticmethod
    def _update_open_document(d, opts, res: "OpenTabsUpdate", owed=frozenset()) -> tuple:
        """Update one open page in the helper's order."""
        from ...infra import logsetup

        genies = bool(opts.get("genies"))
        # (Update Pages' 'every page': everything composed again, as the helper does)
        force = bool(opts.get("force"))
        found = OpenPageFound()

        def composites():
            if not d.composite_roots():
                return 0
            return d.update_composites(force=True) if force else d.update_composites()

        def placeholders():
            # (a placeholder genie whose library opened again since the tab opened: placed
            # again, as the helper does on a closed page)
            return d.instantiate_placeholder_genies() if d.placeholder_genies() else 0

        def placed_genies():
            if force:
                ch = {(a.lower(), b.lower()): None for _i, (a, b) in d.genie_instances()}
            else:
                ch = d.genie_changes()
            if not ch:
                return 0
            found.genies = dict(ch)
            n = (d.apply_genie_changes(ch, force=True) if force else d.apply_genie_changes(ch)) or 0
            # (as the automatic update marks them: not composed again until a file changes)
            done = ui_state(d).genie_sources_done
            for k, v in d.genie_sources(set(ch)).items():
                done[k] = v
            return n

        def template():
            if force and opts.get("templates"):
                props = opts.get("page_properties", False)
                return 1 if d.update_template(force=True, page_properties=props) else 0
            if d.template_status()[0] != "stale":
                return 0
            found.template = True
            # (the open's own step keeps the page's properties, as its worker does)
            props = opts.get("page_properties", False) if opts.get("templates") else False
            return 1 if d.update_template(page_properties=props) else 0

        def supergenie():
            if force:
                return 1 if d.update_supergenie_page(force=True) else 0
            if d.supergenie_status()[0] != "stale":
                return 0
            found.supergenie = True
            return 1 if d.update_supergenie_page() else 0

        def symbols():
            if force and opts.get("symbols", genies):
                return d.update_symbol_placements(None) or 0
            stale = d.stale_symbol_elements()
            if not stale:
                return 0
            found.symbols = set(stale)
            n = d.update_symbol_placements(stale) or 0
            # (the versions it took: undone in the tab, an automatic update leaves them)

            main_window_class()._remember_symbol_versions(d, stale)
            return n

        steps = []
        if genies:
            steps.append(("composite genies", "composites", composites))
        # (the template before the genies, as the open and CtDraw's load: the handles its
        # new members take first, LIVE oracle chain5/order)
        if opts.get("templates") or "templates" in owed:
            steps.append(("template", "templates", template))
        if genies:
            steps.append(("super genie", "supergenies", supergenie))
        if opts.get("symbols", genies) or "symbols" in owed:
            steps.append(("symbols", "symbols", symbols))
        if genies or "placeholders" in owed:
            steps.append(("placeholder genies", "placeholders", placeholders))
        if genies or "genies" in owed:
            steps.append(("genies", "genies", placed_genies))
        ran = {
            "templates": bool(opts.get("templates")),
            "symbols": bool(opts.get("symbols", genies)),
            "placeholders": genies,
            "genies": genies,
        }
        failed = []
        for what, count, step in steps:
            mine = ran.get(count, True)
            depth = history_depth(d)
            try:
                n = step() or 0
            except Exception:  # noqa: BLE001 -- noted; the page is reported, the rest run
                logsetup.note_exception("ui.window.library.update_open_documents/" + count, page=d.path)
                failed.append(what)
                continue
            if mine:
                setattr(res, count, getattr(res, count) + int(n))
            elif history_depth(d) != depth:
                found.owed.append(
                    (open_step_text(d, count, found), d._undo[-1] if getattr(d, "_undo", None) else None)
                )
        return failed, found

    @staticmethod
    def _open_steps_owed(d, opts, jobs) -> frozenset:
        """The open's automatic steps this run's step does in their place."""
        pending = jobs is not None and jobs.template_pending(d)
        st = ui_state(d)
        if not pending and not st.steps_after_template:
            return frozenset()
        genies = bool(opts.get("genies"))
        owed = set()
        if pending and not opts.get("templates"):
            owed.add("templates")
        if st.steps_after_template:
            st.steps_after_template = False
            if not opts.get("symbols", genies) and app_settings().value(
                "opt/auto_update_symbols", True, type=bool
            ):
                owed.add("symbols")
            if not genies:
                owed.add("placeholders")
                if option("auto_update_genies"):
                    owed.add("genies")
        return frozenset(owed)

    def apply_lock_waits(self) -> None:
        """Settings > Pages and libraries > Busy projects: the new waits take effect
        (settings.apply_lock_waits)."""
        from ..prefs.settings import apply_lock_waits

        apply_lock_waits()

    def apply_job_options(self) -> None:
        """Settings > Pages and libraries > Long jobs (and Find's pages per helper): the
        new timings take effect for the next job (ui/prefs/job_options.load)."""
        from ..prefs.job_options import load

        load()

    def _update_pages(self, *, chain=None) -> None:
        """Tools > Update Pages."""
        hold = TabsHold("Update Pages")
        with self._cache_notice_held():
            try:
                self._update_pages_run(hold, chain=chain)
            finally:
                hold.release(self)  # (also after an error or a stop)

    def _update_pages_refused(self) -> bool:
        """Another run goes on (Update Pages or its chain, the chain after a library save,
        Update Users, or one of them still bringing its open tabs up to date): said; True.
        One run at a time: they write the same libraries and pages."""
        from ..jobs.update_run import UpdatePagesRun

        if UpdatePagesRun.active is not None or UpdatePagesRun.chain_busy:
            inform(self, "Update Pages", "Update Pages is already running.")
            return True
        busy = UpdatePagesRun.busy_text("Run Update Pages")
        if busy:
            inform(self, "Update Pages", busy)
            return True
        return False

    def _update_pages_run(self, hold=None, chain=None) -> None:
        """The run of :meth:`_update_pages`; ``hold`` = its hold on the other runs until its
        open tabs have followed (:class:`TabsHold`; one of its own when None), ``chain`` =
        the chain box's state for this run (None: as last time)."""
        from ..jobs.run_stats import RunStats

        hold = hold if hold is not None else TabsHold("Update Pages")
        if self._update_pages_refused():
            return
        from ...io import updatepages

        asked = self._ask_update_pages(chain)
        if asked is None:
            return
        dlg, opts, dirs = asked
        # (the whole run's time, for the result box: the chain and the pages)
        clock = RunStats.from_settings()
        free = self._update_pages_free_projects(dirs)
        if free is None:
            return
        busy, run_dirs = free
        backup = updatepages.backup_dir() if opts["backup"] else None
        # (the open pages' automatic changes so far: one that lands during the run is told)
        marks = self._open_tab_marks()
        # a page open in the Graphics Builder is skipped: the Builder keeps its
        # own copy and its next save would overwrite the update
        try:
            from ...backend.graphics_builder import open_pages

            in_builder = [list(x) for x in open_pages()]
        except Exception:  # noqa: BLE001
            _note_exc("update_runs._update_pages_run")
            in_builder = []
        # (the window and its questions let events through: a run started meanwhile in
        # another window goes first)
        if self._update_pages_refused():
            return
        # one run at a time until the open tabs have followed this one too: the helpers'
        # end clears ``active`` before the open pages' step, whose progress window lets
        # events through (a chain that waited would start inside it).
        # Simulate's library changes wait for it, the other runs and the queue too
        hold.take()
        chain = None
        if opts.get("chain"):
            chain = self._update_pages_chain_first(dlg, opts, busy, backup, clock, hold)
            if chain is None:
                return
        # in helper processes (pages are parsed there, never on a thread here): the window
        # keeps painting meanwhile
        rep = self._update_pages_in_worker(
            {
                "dirs": list(run_dirs),
                "templates": opts["templates"],
                "genies": opts["genies"],
                "backup": backup,
                "builder_open": in_builder,
                "page_properties": opts.get("page_properties", False),
                "symbols": opts.get("symbols", opts["genies"]),
                "force": bool(opts.get("force")),
            },
            parts=dlg.processes(),
        )
        if rep is None:
            self._update_pages_not_run(chain, backup, hold)
            return
        tabs = self._update_pages_tabs(rep, dirs, opts, clock, marks, chain, hold)
        self._note_pages_closed_meanwhile(rep, tabs)
        if getattr(rep, "composites", 0) or tabs.composites:
            from ...editing import libraries_changed

            libraries_changed()
        # every window shows its page as it is now (else a page updated in a tab of another
        # window keeps the old scene, its rows no longer the document's), as Update Users does
        for w in editor_windows():
            with contextlib.suppress(RuntimeError):
                w._refresh()
        self._tell_update_pages(rep, backup, tabs, chain, clock)

    def _ask_update_pages(self, chain):
        """The Update Pages window, started from the open page's project: (the window, its
        options, the projects ticked), else None (cancelled, or no project)."""
        from ..dialog import dialogs

        # an open page's project; editing a template / genie, the project of its library
        # (ElementDocument.path = the library file) is only a hint: the pages that use it
        # are in the projects that include it
        start_dir = library_dir = None
        if self.doc is not None and not isinstance(self.doc, PendingPage) and getattr(self.doc, "path", None):
            d = os.path.dirname(os.path.abspath(self.doc.path))
            if isinstance(self.doc, ElementDocument):
                library_dir = d
            else:
                start_dir = d
        dlg = dialogs.UpdatePagesDialog(
            start_dir, self, library_dir=library_dir, **({} if chain is None else {"chain": chain})
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
        opts = dlg.options()
        dirs = list(dlg.projects())
        if not dirs:
            return None
        return dlg, opts, dirs

    def _update_pages_free_projects(self, dirs):
        """(the projects of ``dirs`` whose project database another program holds open, the
        others to run), else None: all of them are, or the user would not run the others
        (said, asked)."""
        from ...io import pgregister

        # a project database another program holds open: its pages cannot be saved now
        busy = []
        for d in dirs:
            try:
                pgregister.probe_writable(d)
            except pgregister.ProjectDatabaseInUse:
                busy.append(d)
        if not busy:
            return busy, dirs
        names = ", ".join(os.path.basename(os.path.normpath(d)) for d in busy)
        if len(busy) == len(dirs):
            warn(
                self,
                "Update Pages",
                "The project database of %s is in use (is Plant SCADA Studio open?). "
                "Close Plant SCADA Studio, then run Update Pages again." % names,
            )
            return None
        from ..messages import confirm

        if not confirm(
            self,
            "Update Pages",
            "The project database of %s is in use (is Plant SCADA Studio open?). Their "
            "pages cannot be updated now. Update the other projects?" % names,
            "Update the Others",
            safe_default=False,
        ):
            return None
        return busy, [d for d in dirs if d not in busy]

    def _update_pages_chain_first(self, dlg, opts, busy, backup, clock, hold):
        """'Save the library genies in the chain first': the genies and templates of the
        projects' libraries that place a changed genie are saved again here first, as Update
        Users saves them; the helpers then read the new stamps from disk. The libraries are
        those of the ticked projects and of the tree's projects with no pages (library
        projects: nothing to tick), never of a busy one. Meanwhile a second Update Pages and
        Pack Libraries wait (the progress window processes events). The chain's result, else
        None: the run ends here (the chain could not be read, or it was stopped: said)."""
        from ..jobs.update_run import UpdatePagesRun

        chain_dirs = [d for d in dlg.chain_projects() if d not in busy]
        UpdatePagesRun.chain_busy = True
        try:
            chain = self._update_pages_chain(
                chain_dirs, backup, compose_all=bool(opts.get("chain_compose_all"))
            )
        finally:
            UpdatePagesRun.chain_busy = False
        if chain is None:
            return None  # (the chain could not be read: said)
        if chain.stopped:
            # (what it saved before the stop: the open tabs that nest it follow, and so
            # do the Used By windows)
            self._follow_run_saves(chain.elements_saved + chain.elements_in_editor, hold)
            inform(
                self,
                "Update Pages",
                "\n".join(
                    self._chain_summary(chain, backup)
                    + [x for x in (clock.total_text(),) if x]
                    + ["Stopped before the pages were updated."]
                ),
            )
            self._follow_used_by_windows()
            return None
        return chain

    def _update_pages_not_run(self, chain, backup, hold) -> None:
        """The pages' helpers could not start (said): what the chain saved is followed and
        told still."""
        if chain is not None:
            self._follow_run_saves(chain.elements_saved + chain.elements_in_editor, hold)
        hold.release(self)
        lines = self._chain_summary(chain, backup) if chain is not None else []
        if lines:
            # (the run could not start, said; what the chain saved is told still)
            inform(self, "Update Pages", "\n".join(lines + ["The pages were not updated."]))
        if chain is not None:
            self._follow_used_by_windows()

    def _update_pages_tabs(self, rep, dirs, opts, clock, marks, chain, hold) -> "OpenTabsUpdate":
        """After the pages' helpers: the pages open in tabs updated, and the tabs that nest
        what the chain saved; ``hold`` released then. The open tabs' update."""
        # (a page shown read-only was a closed page for the run: it reads its file again)
        self._reload_read_only_views(r.get("path") for r in rep.records if r.get("outcome") == "updated")
        # (the pages open in tabs: behind its own progress window with Cancel, the run's
        # clock going on)
        tabs = self._update_open_documents_shown(dirs, opts, "Update Pages", stats=clock, marks=marks)
        if chain is not None:
            # (the element tabs that nest the genies its chain saved, and the open pages
            # this step left, compose them, as after the chain after a genie save: a page a
            # Cancel in that step left too, and the result says so)
            if tabs.left and (chain.elements_saved or chain.elements_in_editor):
                tabs.left_follow = follow_words(
                    chain.elements_saved + chain.elements_in_editor, getattr(chain, "element_kinds", None)
                )
            self._follow_run_saves(chain.elements_saved + chain.elements_in_editor, hold)
            if tabs.left_follow:
                self._left_follow(
                    tabs,
                    chain.elements_saved + chain.elements_in_editor,
                    getattr(chain, "element_kinds", None),
                )
        # (the open tabs have followed: a chain that waited goes next)
        hold.release(self)
        return tabs

    def _note_pages_closed_meanwhile(self, rep, tabs) -> None:
        """``rep.closed_meanwhile``: the pages left because they were open in an editor (when
        the run started, or opened during it) and closed before it ended: nothing updated
        them."""
        from ...io import updatepages

        now = {path_key(p) for p in self._open_page_paths()}
        left = list(getattr(rep, "open_at_start", ()) or ()) + [
            r.get("path") for r in rep.records if r.get("outcome") == "skipped_open" and r.get("path")
        ]
        # (a page saved before it was closed, current now, is not named: the same rule as
        # the chain after a library save, _closed_meanwhile. Named as the summary's other
        # lines name a page, with its project in a run over several)
        label = run_page_label(len(unique_names(list(rep.projects) + tabs.projects())) > 1)
        rep.closed_meanwhile = sorted(
            {label(p) for p in left if path_key(p) not in now and updatepages.page_needs_update(p)},
            key=str.lower,
        )

    def _tell_update_pages(self, rep, backup, tabs, chain, clock) -> None:
        """The result of an Update Pages run: logged, shown, then the Used By windows, the
        database writes rolled back and the compile it offered."""
        from ...infra import backups, logsetup

        lines = self._update_pages_summary(rep, backup, tabs, chain=chain)
        if clock.total_text():
            lines.append(clock.total_text())
        log = logsetup.get("update-pages")
        log.info(
            "%d checked in %d project(s), %d out of date, updated: %s%s",
            rep.pages,
            len(rep.projects),
            rep.stale,
            ", ".join(rep.updated) or "none",
            "; failed: %s" % "; ".join(rep.failed) if rep.failed else "",
        )
        for r in rep.records:
            # (the box names these pages; the log says what was done and why, and which
            # language label entries a save dropped)
            if r.get("outcome") == "unchanged" or r.get("links_dropped") or r.get("labels_dropped"):
                log.info("%s: %s: %s", r.get("path"), r.get("outcome"), r.get("detail") or "")
        with contextlib.suppress(Exception):
            backups.prune()
        chain_saved = chain is not None and bool(chain.elements_saved or chain.elements_in_editor)
        compile_now = self._show_run_results(
            "Update Pages",
            lines,
            backup=backup if backup and (rep.updated or chain_saved) else "",
            compile_offer=bool(rep.updated),
        )
        # (the Used By windows read their users again)
        self._follow_used_by_windows()
        if rep.recovered:
            # (database writes a dead helper left half done, rolled back: said after the
            # result has been read, never over it)
            self.report_journal_recovery(list(rep.recovered))
        if compile_now:
            # (after this run's hold: CtDraw's cache notice first, then the compile window)
            QtCore.QTimer.singleShot(0, self, self.compile_project_command)

    def _show_run_results(self, title: str, lines, backup: str = "", compile_offer: bool = False) -> bool:
        """Show a run's result window."""
        from ..notify import notify
        from ..jobs.run_results import RunResultsDialog

        lines = [str(x) for x in lines]
        if lines:
            notify(
                self,
                lines[0],
                self._run_level(lines),
                key="run:%s" % title,
                detail="Backup: %s" % backup if backup else "",
            )
        dlg = RunResultsDialog(
            title,
            lines,
            self,
            backup=backup,
            compile_offer=compile_offer,
            attention=[x for x in lines[1:] if self._run_level([x]) == "warning"],
        )
        try:
            return dlg.exec() == RunResultsDialog.COMPILE
        finally:
            dlg.deleteLater()

    #: the starts of the result lines that make a run's note a warning: what it could not
    #: do, and every page or element it left out of date
    _WARN_LINES = (
        "Failed",
        "Could not",
        "A helper process failed",
        "Not restored",
        "Stopped",
        "Nested copies that could not be composed again",
        "Not updated",
        "Skipped because",
        "Changed by another save",
        "Out of date, but nothing",
        "Saved, but",
        "Not checked",
        "Not saved again",
        "Not compared",
        "Warning",
        "Open in a tab, out of date, but nothing",
        "Open in a tab, could not",
        "Open in a tab, not updated",
        "Open in a tab, left when",
    )
    #: ... and the parts of one (composite genies left: their template is not found)
    _WARN_PARTS = (" left as it was: ", " left as they were: ")

    @staticmethod
    def _run_level(lines) -> str:
        """'warning' if a result line reports something left undone, else 'info'."""
        for x in lines:
            t = str(x).lstrip()
            if t.startswith(UpdateRuns._WARN_LINES) or any(part in t for part in UpdateRuns._WARN_PARTS):
                return "warning"
        return "success"

    @staticmethod
    def _chain_summary(chain, backup=None) -> list:
        """The lines for the library genies Update Pages saved first."""
        from ...io.genieusage import SHIPPED_WHY

        kinds = getattr(chain, "element_kinds", None) or {}
        lines = []
        saved = list(chain.elements_saved) + list(chain.elements_in_editor)
        if saved:
            lines.append(
                "%s saved again first: %s."
                % (
                    main_window_class()._and_list(main_window_class()._saved_kinds_parts(saved, kinds)),
                    name_list(saved),
                )
            )
        if chain.elements_current:
            n = len(chain.elements_current)
            lines.append(
                "%s already matched what %s: %s."
                % (
                    main_window_class()._and_list(
                        main_window_class()._saved_kinds_parts(chain.elements_current, kinds)
                    ),
                    "it places" if n == 1 else "they place",
                    name_list(chain.elements_current),
                )
            )
        tail = ": " + SHIPPED_WHY
        shipped = [x[: -len(tail)] for x in chain.skipped if x.endswith(tail)]
        others = [x for x in chain.skipped if not x.endswith(tail)]
        lines += ["Not saved again: %s." % x for x in others[:10]]
        if len(others) > 10:
            more = len(others) - 10
            lines.append(
                "%d more %s not saved again. The log lists them." % (more, "was" if more == 1 else "were")
            )
        unreadable = list(getattr(chain, "unreadable", None) or ())
        if unreadable:
            lines.append(
                "Not checked, could not be read (the log says why): %s."
                % name_list(short_path(f) + (" (%s)" % el if el else "") for f, el in unreadable)
            )
        # (compose-all: a copy that could not be composed keeps a current stamp, so only
        # this line tells it; a genie whose comparison failed was not checked)
        left = list(getattr(chain, "not_composed", None) or ())
        if left:
            lines.append(main_window_class()._not_composed_text(left))
        not_compared = list(getattr(chain, "not_compared", None) or ())
        if not_compared:
            lines.append(
                "Not compared with their libraries, because of an error (the log "
                "says why): %s." % name_list(x.split(": ", 1)[0] for x in not_compared)
            )
        if shipped:
            lines.append(
                "%s in libraries supplied with Plant SCADA %s left alone: %s."
                % (
                    main_window_class()._and_list(
                        main_window_class()._saved_kinds_parts(
                            shipped, kinds, main_window_class()._KIND_WORDS_BARE
                        )
                    ),
                    "was" if len(shipped) == 1 else "were",
                    name_list(shipped),
                )
            )
        lines += ["Could not save %s" % x for x in chain.failed[:10]]
        if chain.super_pages:
            lines.append("Super genie pages created: %s." % name_list(chain.super_pages))
        if backup and saved:
            lines.append(
                "Backups of the changed libraries: %s (Tools > Restore Backup puts them back)" % backup
            )
        paths = [f for f, _el in unreadable]
        return [full_stop(shorten_paths(x, paths)) for x in lines]

    @staticmethod
    def _update_pages_summary(rep, backup, tabs=None, chain=None) -> list:
        """The lines of Update Pages' result."""
        from ..names import plural

        names = name_list
        if not isinstance(tabs, OpenTabsUpdate):
            tabs = OpenTabsUpdate()  # (none open)

        def its(n):
            return "its" if n == 1 else "their"

        lines = UpdateRuns._chain_summary(chain) if chain is not None else []
        chain_saved = chain is not None and bool(chain.elements_saved or chain.elements_in_editor)
        # (one project spelled two ways, its tab opened from a typed path: one project)
        projects = unique_names(list(rep.projects) + tabs.projects())
        label = run_page_label(len(projects) > 1)
        in_tabs = len(tabs.updated) + len(tabs.auto)
        count = "%s checked in %s, %d out of date, %d updated" % (
            plural(rep.pages + len(tabs.paths()), "page"),
            plural(len(projects), "project"),
            rep.stale + tabs.stale,
            len(rep.updated) + in_tabs + len(tabs.saved),
        )
        if in_tabs:
            count += " (%d in %s, not saved yet)" % (in_tabs, "its tab" if in_tabs == 1 else "their tabs")
        lines.append(count)
        if rep.stopped:
            lines.append("Stopped before the end.")
        if getattr(rep, "closed_meanwhile", None):
            lines.append(
                "Not updated, because they were open in an editor during the run "
                "and closed before it ended. Run Update Pages again: %s" % names(rep.closed_meanwhile)
            )
        if getattr(rep, "symbols", 0):
            lines.append(
                "%s updated from %s library." % (plural(rep.symbols, "placed symbol"), its(rep.symbols))
            )
        if rep.templates:
            lines.append("%s applied again." % plural(rep.templates, "page template"))
        if getattr(rep, "supergenies", 0):
            lines.append(
                "%s made again from %s super genie."
                % (plural(rep.supergenies, "super genie page"), its(rep.supergenies))
            )
        if rep.instances:
            lines.append("%s updated." % plural(rep.instances, "placed genie"))
        if getattr(rep, "placeholders", 0):
            # (a genie whose library was missing when the page was saved, its library found
            # again: placed again, as the Graphics Builder's load does)
            lines.append(
                "%s placed again from %s library."
                % (plural(rep.placeholders, "placeholder genie"), its(rep.placeholders))
            )
        if getattr(rep, "composites", 0):
            lines.append(
                "%s composed again with the newest template." % plural(rep.composites, "composite genie")
            )
        if getattr(rep, "composites_skipped", 0):
            one = rep.composites_skipped == 1
            lines.append(
                "%s left as %s: %s template is not in the project or the projects "
                "it includes."
                % (
                    plural(rep.composites_skipped, "composite genie"),
                    "it was" if one else "they were",
                    its(rep.composites_skipped),
                )
            )
        if getattr(rep, "links_dropped", None):
            # (a language label entry dropped is no link)
            lines.append(
                "Links to library elements no longer on the page removed: %s" % names(rep.links_dropped)
            )
        if getattr(rep, "unchanged", None):
            lines.append(
                "Out of date, but nothing on them could be updated (the log says "
                "why): %s" % names(rep.unchanged)
            )
        lines += tabs.lines(label)
        if rep.open_in_builder:
            lines.append(
                "Skipped because they are open in the Graphics Builder (close them "
                "there and run Update Pages again): %s" % names(rep.open_in_builder)
            )
        if rep.db_in_use:
            lines.append(
                "Not updated because the project database is in use (is Plant SCADA "
                "Studio open?): %s" % names(rep.db_in_use)
            )
        if rep.changed_meanwhile:
            lines.append(
                "Changed by another save while being updated. Run Update Pages "
                "again: %s" % names(rep.changed_meanwhile)
            )
        if rep.rows_pending:
            lines.append(
                "Saved, but not registered in their project yet (the project "
                "database could not be written). The next Update Pages run "
                "registers them: %s" % names(rep.rows_pending)
            )
        if rep.rows_written:
            lines.append(
                "Registered in their project now, after an earlier save could not: "
                "%s" % names(rep.rows_written)
            )
        if rep.not_checked:
            lines.append(
                "Not checked, because the run was stopped or a helper process "
                "ended: %s." % plural(rep.not_checked, "page")
            )
        if rep.unregistered:
            n = len(rep.unregistered)
            lines.append(
                "%s not registered in %s project %s left alone."
                % (plural(n, "page file"), its(n), "was" if n == 1 else "were")
            )
        if rep.updated:
            lines.append("Compile the project to see the changes in the runtime.")
        if backup and rep.updated and chain_saved:
            lines.append(
                "Backups of the changed libraries, pages and their project database "
                "rows: %s (Tools > Restore Backup puts them back)" % backup
            )
        elif backup and rep.updated:
            lines.append(
                "Backups of the changed pages and their project database rows: %s "
                "(Tools > Restore Backup puts them back)" % backup
            )
        elif backup and chain_saved:
            lines.append(
                "Backups of the changed libraries: %s (Tools > Restore Backup puts them back)" % backup
            )
        paths = [r.get("path") for r in (getattr(rep, "records", None) or ())] + tabs.paths()
        lines = [full_stop(shorten_paths(x, paths)) for x in lines]

        def block(title, items, most):
            return "\n%s:\n%s" % (title, "\n".join(full_stop(shorten_paths(x, paths)) for x in items[:most]))

        if rep.warnings:
            lines.append(block("Warnings", rep.warnings, 20))
        if rep.failed:
            lines.append(block("Failed", rep.failed, 20))
        if rep.errors:
            lines.append(block("A helper process failed", rep.errors, 5))
        return lines

    def _update_pages_chain(self, dirs, backup, compose_all: bool = False):
        """Update Pages: save the library genies in the chain first."""
        from ...infra import logsetup
        from ...io import genieusage as GU
        from ..jobs.run_stats import RunStats

        stats = RunStats.from_settings()
        from ..jobs.job_progress import JobProgress

        prog = JobProgress(
            self,
            "Update Pages",
            "Finding the library genies to save…",
            stats=stats,
            cancel="Stop After This Page",
        )

        def ticker(text, phase):
            def tick(i, n, label):
                prog.phase = phase
                return prog.tick(int(i), int(n), text % (label, int(i) + 1, int(n)))

            return tick

        plan = rep = None
        try:
            # (per library file: the window names the file and counts them all)
            plan = GU.chain_plan(
                dirs,
                progress=ticker("Reading %s… (%d of %d)", "libraries"),
                compose_all=compose_all,
                compose_progress=ticker("Comparing %s with its libraries… (%d of %d)", "compare"),
            )
            if plan.stopped:
                rep = GU.UpdateUsersReport(backup=backup, stopped=True)
            else:
                from ...render import headless_thumb

                rep = GU.run_update(
                    GU.UpdatePlan(plan.elements, [], plan.skipped),
                    backup=backup,
                    open_element=self._open_element_doc,
                    progress=ticker("Saving %s… (%d of %d)", "genies"),
                    page_thumbnail=self._page_thumbnail_for,
                    backup_kind="update-pages",
                    tab_clean=self._tab_clean,
                    element_thumbnail=headless_thumb.element_thumbnail,
                )
                self._settle_saved_tabs(rep.tabs_saved, reload=False)
                # (a Cancel clicked during the last save, or the last library read of an
                # empty plan, is still in the queue: nothing in a save processes events)
                if not prog.pulse():
                    rep.stopped = True  # (during the last save: still before the pages)
            # (a library that could not be read: its genies were neither checked nor saved;
            # the box names it, the log line below says why)
            rep.unreadable = list(plan.unreadable)
            # (compose-all: the genies it could not compare, and the copies it could not
            # compose in the genies it left; the saves' own come after them)
            rep.not_compared = list(plan.not_compared)
            # (each copy once: an element the plan left that a save names too)
            joined = []
            for label, keys in list(plan.not_composed) + list(rep.not_composed):
                row = (label, list(keys))
                if row not in joined:
                    joined.append(row)
            rep.not_composed = joined
        except Exception:  # noqa: BLE001 -- told; nothing of the pages has run
            logsetup.note_exception("ui.window.library.update_pages_chain")
        finally:
            prog.close()
        if rep is None:
            from ..messages import error_box

            error_box(
                self,
                "Update Pages",
                "The library genies in the chain could not be read, so the pages were not updated.",
                next_steps=(
                    "Untick 'Save the library genies and templates in the chain "
                    "first' to update the pages without it.",
                ),
            )
            return None
        saved = rep.elements_saved + rep.elements_in_editor
        if saved:
            from ...editing import libraries_changed

            libraries_changed()  # (run_update did too; the next derives read anew)
            done, folders = set(saved), []
            for u in plan.elements:
                d = os.path.dirname(os.path.abspath(u.ref.path))
                if u.ref.label in done and d not in folders:
                    folders.append(d)
                    self._after_library_write(u.ref.path)
            self._library_cache_notice(rep.save_warning)
            if rep.elements_in_editor:
                for w in editor_windows():
                    with contextlib.suppress(RuntimeError):
                        w._refresh()
        compared = set(rep.not_compared)
        logsetup.get("update-pages").info(
            "library genies saved first: %s%s%s%s%s%s%s",
            ", ".join(saved) or "none",
            "; skipped: %s" % "; ".join(rep.skipped) if rep.skipped else "",
            "; not read: %s" % "; ".join(x for x in plan.problems if x not in compared)
            if any(x not in compared for x in plan.problems)
            else "",
            "; not compared: %s" % "; ".join(rep.not_compared) if rep.not_compared else "",
            "; not composed: %s" % "; ".join("%s (%s)" % (lb, ", ".join(ks)) for lb, ks in rep.not_composed)
            if rep.not_composed
            else "",
            "; failed: %s" % "; ".join(rep.failed) if rep.failed else "",
            "; stopped" if rep.stopped else "",
        )
        return rep

    def _update_pages_in_worker(self, job: dict, parts: Optional[int] = None):
        """Run Update Pages in helper processes."""
        import types
        from ...workers import genie_worker as GW
        from ..jobs.update_run import UpdatePagesRun

        run = UpdatePagesRun(
            self, list(job.get("dirs") or ()), job, skip_paths=self._open_page_paths, parts=parts
        )
        # (a stop keeps the pages already updated: the button says so; Cancel, as in the
        # window before the run, read as 'nothing changes')
        from ..jobs.job_progress import JobProgress

        prog = JobProgress(
            self,
            "Update Pages",
            "Finding the pages to check…",
            stats=run.stats,
            cancel="Stop After This Page",
        )
        loop = QtCore.QEventLoop(self)
        state = {"res": None, "stopping": False}
        from ..jobs import taskbar, tasks

        def cancel():
            if run.done:
                return
            state["stopping"] = True
            run.stop()
            prog.steady.set(["Stopping after the pages being updated…"])
            tasks.progress(task, 0, 0, "Stopping Update Pages…")

        def on_progress(i, n, name, phase):
            if state["stopping"]:
                prog.show_lines(i, n, ["Stopping after the pages being updated…"])
                return  # (no estimate while it stops: cancel() said so)
            # (the step named: its estimate is of that step; the composite pass after the
            # check has its own)
            tasks.progress(task, int(i), int(n), "Update Pages: %s…" % run.stats.step_text())
            head = (
                "Checking %s… (%d of %d)" if phase == "check" else "Updating composite genies: %s… (%d of %d)"
            ) % (name, int(i) + 1, int(n))
            prog.show_lines(i, n, [head] + run.stats.lines())

        def on_finished(res):
            state["res"] = res
            loop.quit()

        run.progress.connect(on_progress)
        run.finished.connect(on_finished)
        prog.prog.canceled.connect(cancel)
        # (a background task in the status bar, the progress on the taskbar button)
        task = tasks.begin("update-pages", "Updating pages…", cancel=cancel, window=self)
        bar = taskbar.ProgressReporter(prog.prog, self)
        if not run.start():
            bar.finish()
            prog.close()
            tasks.end(task, ok=False, tell=False)
            if UpdatePagesRun.active is not None:
                warn(self, "Update Pages", "Update Pages is already running.")
            else:
                from ..messages import error_box

                error_box(self, "Update Pages", "Could not start Update Pages.")
            return None
        if state["res"] is None:
            loop.exec()
        bar.finish()
        prog.close()
        res = state["res"] or {}
        tasks.end(task, ok=not res.get("errors"), tell=False)  # (its result box says it)
        if res.get("errors"):
            from ...infra import logsetup

            logsetup.get("update-pages").warning(
                "Update Pages: a helper failed: %s", "; ".join(res["errors"])
            )
        rep = types.SimpleNamespace(**{k: res.get(k) for k in GW.REPORT_FIELDS})
        for k in (
            "updated",
            "failed",
            "unregistered",
            "open_in_builder",
            "skipped_open",
            "changed_meanwhile",
            "db_in_use",
            "rows_pending",
            "rows_written",
            "warnings",
            "links_dropped",
            "unchanged",
            "records",
            "recovered",
        ):
            setattr(rep, k, list(getattr(rep, k) or []))
        for k in (
            "pages",
            "stale",
            "instances",
            "placeholders",
            "templates",
            "supergenies",
            "symbols",
            "composites",
            "composites_skipped",
            "not_checked",
        ):
            setattr(rep, k, int(getattr(rep, k) or 0))
        rep.projects = list(res.get("projects") or [])
        rep.errors = list(res.get("errors") or [])
        rep.stopped = bool(res.get("stopped"))
        rep.open_at_start = list(res.get("open_at_start") or [])
        return rep

    # ------------------------------------------------------------------------ restore backup

    def _restore_backup_dialog(self) -> None:
        """Tools > Restore Backup: put back the pages (and their project
        database rows) or the libraries a run of Update Pages / Pack
        Libraries rewrote. Pages open here are reloaded afterwards; pages open
        in the Graphics Builder are skipped."""
        from ...infra import backups, logsetup
        from ..recovery import RestoreBackupDialog

        items = backups.list_backups()
        if not items:
            from ..messages import info_box, open_folder_action

            root = backups.backups_root()
            info_box(
                self,
                "Restore Backup",
                "There are no backups. Update Pages, Pack Libraries, Replace All in "
                "closed pages, Delete Page and Duplicate Pictures make one each time "
                "they rewrite files.",
                next_steps=("The backups go to %s." % root,),
                actions=(open_folder_action("Open Backup Folder", root),),
            )
            return
        dlg = RestoreBackupDialog(items, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted or dlg.chosen is None:
            return
        b = dlg.chosen
        if b.kind == "pack":
            if self._refuse_while_builder_runs(
                "Restore Backup",
                "it would write its own copies of the library elements back",
                "restore again",
            ):
                return
            from ...format.projects import restore_pack_backup

            res = restore_pack_backup(b.path)
            from ...editing import libraries_changed

            libraries_changed()
            if res.packed:
                self._after_library_write(*res.packed)
            done, failed = (
                [os.path.basename(p) for p in res.packed],
                ["%s: %s" % (os.path.basename(p), why) for p, why in res.failures],
            )
            logsetup.files_written("restored libraries from %s" % b.path, res.packed)
        else:
            from ...io import updatepages

            if any(e.get("original") for e in b.entries):
                # an Update Users backup also holds library files
                if self._refuse_while_builder_runs(
                    "Restore Backup",
                    "it would write its own copies of the library elements back",
                    "restore again",
                ):
                    return
            names = [e.get("page", "") for e in b.entries if e.get("page")]
            # tabs showing those pages: their Save would write over the restore
            keys = {
                os.path.normcase(os.path.join(e.get("project", ""), e.get("page", "") + ".ctg"))
                for e in b.entries
            }
            open_tabs = [
                (w, x)
                for w in editor_windows()
                for x in w._docs
                if not isinstance(x["doc"], ElementDocument) and path_key(x["doc"].path) in keys
            ]
            if any(x["doc"].dirty for _w, x in open_tabs) and not self._confirm(
                "Restore Backup",
                "Some of these pages are open here with unsaved changes.",
                "Discard and Restore",
                "Restoring reloads them from the backup; the unsaved changes are lost.",
            ):
                return
            try:
                from ...backend.graphics_builder import open_pages

                in_builder = open_pages()
            except Exception:  # noqa: BLE001
                _note_exc("update_runs._restore_backup_dialog")
                in_builder = []
            rep = updatepages.restore_backup(b.path, names, builder_open=in_builder)
            if rep.libraries:
                from ...editing import libraries_changed

                libraries_changed()
                self._after_library_write(*rep.libraries)
                logsetup.files_written("restored libraries from %s" % b.path, rep.libraries)
            restored = {n.lower() for n in rep.restored}
            for w, x in open_tabs:
                # a page that was not restored keeps its tab (and its edits)
                if os.path.splitext(os.path.basename(x["doc"].path))[0].lower() not in restored:
                    continue
                x["doc"].dirty = False
                with contextlib.suppress(Exception):
                    w.revert_document(x["doc"], ask=False)
            done, failed = rep.restored, rep.failed
            logsetup.get("restore").info(
                "restored pages from %s: %s%s",
                b.path,
                ", ".join(done) or "none",
                "; failed: %s" % "; ".join(failed) if failed else "",
            )
        lines, offer = self._restore_result_lines(b, done, failed)
        if self._show_run_results("Restore Backup", lines, backup=b.path, compile_offer=offer):
            QtCore.QTimer.singleShot(0, self, self.compile_project_command)

    @staticmethod
    def _restore_result_lines(b, done, failed) -> tuple:
        """(the lines, a compile is offered) of Restore Backup's result (the results window,
        _show_run_results): the items put back, "Compile the project" when pages came back,
        the items not restored."""
        from ..names import plural
        from ..recovery import when_text

        lines = [
            "%s restored from the backup of %s: %s."
            % (plural(len(done), "item"), when_text(b.created), name_list(done) or "none")
        ]
        if b.kind != "pack" and done:
            lines.append("Compile the project to see them in the runtime.")
        if failed:
            lines.append("")
            lines.append("Not restored:")
            lines += [full_stop(f) for f in failed[:20]]
            if len(failed) > 20:
                lines.append("%d more were not restored. The log lists them." % (len(failed) - 20))
        return lines, bool(b.kind != "pack" and done)


def run_page_label(multi: bool):
    """``label(path)``: a page's name in Update Pages' summary, with its project when the
    run went over several projects (``multi``), as the helpers name the pages
    (genie_worker's merged answer)."""

    def label(path) -> str:
        name = os.path.splitext(os.path.basename(path))[0]
        if multi:
            return "%s (%s)" % (name, os.path.basename(os.path.dirname(os.path.abspath(path))))
        return name

    return label


def open_step_text(d, count: str, found) -> str:
    """What an open's automatic step that Update Pages did in its place changed on the page
    ``d`` (main_window_class()._update_open_document's ``owed``), said as the open says it in
    the bar over the canvas."""
    if count == "templates":
        from ..names import template_name

        return "template %s re-applied" % template_name(d.template_status()[1] or "")
    if count == "symbols":
        names = sorted(
            {
                "%s.%s" % key
                for _i, key, _k in d.symbol_placements()
                if (key[0].lower(), key[1].lower()) in found.symbols
            },
            key=str.lower,
        )
        return "symbols updated from their library (%s)" % ", ".join(names[:5])
    if count == "placeholders":
        return "genies placed from their library"
    names = set()
    for k, v in (found.genies or {}).items():
        names |= {"%s.%s" % c for c in v} if v else {"%s.%s" % k}
    names = sorted(names, key=str.lower)
    return "%s updated from %s library" % (
        ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
        "its" if len(names) == 1 else "their",
    )


@dataclass
class OpenPageFound:
    """What the checks of the step for one open page found out of date
    (main_window_class()._update_open_document): the genie_changes, whether the template and
    the super genie were stale, the stale symbol elements; the open's own steps it did
    that changed the page, [(what, undo step)]."""

    genies: Dict[tuple, object] = field(default_factory=dict)
    template: bool = False
    supergenie: bool = False
    symbols: Set[tuple] = field(default_factory=set)
    owed: list = field(default_factory=list)

    @property
    def due(self) -> bool:
        return bool(self.genies or self.template or self.supergenie or self.symbols)


def short_path(path: str) -> str:
    """'<project>\\<file>' of a project file: what a result box names (the log keeps the
    full path)."""
    p = os.path.normpath(os.path.abspath(path))
    return os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p))


def shorten_paths(text: str, paths) -> str:
    """``text`` with the folders of ``paths`` (project files) written as their project's
    name: 'C:\\...\\User\\Proj\\a.ctg' reads 'Proj\\a.ctg' (an error message in a result
    box)."""
    import re

    text = str(text)
    dirs = sorted(
        {os.path.dirname(os.path.normpath(os.path.abspath(p))) for p in paths if p}, key=len, reverse=True
    )
    for d in dirs:
        if not os.path.basename(d):
            continue
        parent = os.path.dirname(d)
        if parent and parent != d:
            text = re.sub(re.escape(parent + os.sep), "", text, flags=re.IGNORECASE)
    return text
