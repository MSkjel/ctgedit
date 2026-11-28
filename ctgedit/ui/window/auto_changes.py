"""Automatic changes that wait for the user."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from .open_tabs import SharedState

from ...infra.paths import path_key

from ..prefs.settings import option

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

from .files import NESTED_DONE
from .open_genie_updates import (
    redo_wait_s,
)


class AutoChanges:
    """The changes a library save made to an open tab by itself, kept apart from the user's own steps."""

    # -- automatic changes that wait for the user ------------------------------------------
    def _may_change_now(self, doc, redo: bool = True) -> bool:
        """Can an automatic change of ``doc`` be made now?"""
        import time

        if not redo:
            return self._genie_may_land(doc, float("inf"))
        since = next((item[1] for item in self.__dict__.get("_idle_docs") or () if item[0] is doc), None)
        return self._genie_may_land(doc, time.monotonic() - since if since is not None else 0.0)

    def _redo_holds(self, doc) -> bool:
        """Is ``doc`` holding an update to keep its redo steps?"""
        import time

        if not self._user_can_redo(doc):
            return False
        since = next((item[1] for item in self.__dict__.get("_idle_docs") or () if item[0] is doc), None)
        return (time.monotonic() - since if since is not None else 0.0) < redo_wait_s()

    def _wait_until_idle(self, doc, redraw: bool = False, templates=(), since=None, reload: str = "") -> None:
        """Wait until the user is done, then update ``doc``."""
        import time
        from ..prefs.job_options import poll_ms

        labels = {str(x).lower() for x in templates or () if x}
        waits = self.__dict__.setdefault("_idle_docs", [])
        for item in waits:
            if item[0] is doc:
                item[2] = item[2] or redraw
                item[3] |= labels
                item[4] = item[4] or reload
                break
        else:
            waits.append(
                [doc, time.monotonic() if since is None else since, bool(redraw), labels, reload or ""]
            )
        timer = self.__dict__.get("_idle_timer")
        if timer is None:
            timer = self._idle_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._idle_tick)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _waits_until_idle(self, doc) -> bool:
        return any(item[0] is doc for item in self.__dict__.get("_idle_docs") or ())

    def _reload_waits(self, doc) -> bool:
        """``doc`` waits for the user with a reload of what the chain's helper saved
        (:meth:`_wait_until_idle` ``reload``): its file changed by ctgedit's own update, which
        the tab takes when the user has finished."""
        return any(item[0] is doc and item[4] for item in self.__dict__.get("_idle_docs") or ())

    def _waits_first(self, doc) -> bool:
        """``doc`` waits with a step that comes before its symbols and genies (the chain's
        template step, a reload of what the chain's helper saved): its symbol step waits
        for it too (:meth:`_idle_tick` makes them in that order)."""
        return any(item[0] is doc and (item[3] or item[4]) for item in self.__dict__.get("_idle_docs") or ())

    def _take_idle_wait(self, doc, show: bool = False):
        """Stop ``doc`` waiting for the user."""
        waits = self.__dict__.get("_idle_docs") or []
        item = next((x for x in waits if x[0] is doc), None)
        if item is None:
            return None
        if show and (item[4] or self._redo_holds(doc)):
            redraw, item[2] = bool(item[2]), False
            return redraw, frozenset(), item[1], ""
        waits.remove(item)
        return bool(item[2]), frozenset(item[3]), item[1], item[4]

    def _templates_that_waited(self, doc, labels, hold: bool = False) -> int:
        """The chain's template step of ``doc`` that waited for the user (``labels``:
        :meth:`_wait_until_idle`), made now (library._chain_templates_in_tabs, no second
        wait); ``hold``: the window does not draw it (a tab being shown: its show draws it).
        The pages changed."""
        if not labels:
            return 0
        own = hold and self.__dict__.get("_refresh_hold") is None
        if own:
            self._refresh_hold = []
        try:
            return self._chain_templates_in_tabs(labels, docs=(doc,), gate=False)
        except Exception:  # noqa: BLE001 -- the page is updated at its next open
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.templates_that_waited", page=getattr(doc, "path", ""))
            return 0
        finally:
            if own:
                self.__dict__.pop("_refresh_hold", None)

    def _idle_tick(self) -> list:
        """Timer for _wait_until_idle."""
        import time

        waits = list(self.__dict__.get("_idle_docs") or ())
        self._idle_docs = []
        timer = self.__dict__.get("_idle_timer")
        if self.__dict__.get("_window_closed"):
            # (the window was closed: nothing lands on its pages)
            if timer is not None:
                timer.stop()
            return []
        now = time.monotonic()
        ready = []
        for item in waits:
            doc, since, redraw, labels, reload = item
            e = next((x for x in self._docs if x["doc"] is doc), None)
            if e is None:
                continue  # (closed, or moved: _move_entry_to)
            if not self._may_change_now(doc, redo=False):
                self._idle_docs.append(item)
                continue
            if redraw and not reload:
                # (with a reload, after it: the reload draws it, a tab it keeps is drawn
                # then)
                item[2] = False
                self._redraw_waited(doc, e)
            if not self._genie_may_land(doc, now - since):
                self._idle_docs.append(item)  # (steps to redo: for up to a minute)
                continue
            if labels and self._runs_hold_tabs():
                # (the template step waits for a run that saves library elements, as the
                # symbol and genie steps do: it reads the template's library)
                self._idle_docs.append(item)
                continue
            ready.append(item)
        for item in ready:
            doc, _since, redraw, labels, reload = item
            if reload:
                if self._reload_that_waited(doc, reload):
                    continue  # (reloaded: its open checks follow)
                if redraw and (reload == "page" or self._stays_changed_on_disk(doc)):
                    # (the library watch's redraw that waited with the reload: the tab kept
                    # for a change of the user's is drawn from the files as they are; an
                    # element tab that read the store again was drawn by it)
                    item[2] = False
                    e = next((x for x in self._docs if x["doc"] is doc), None)
                    if e is not None:
                        self._redraw_waited(doc, e)
            # (the template first, as the chain's end and a page's open do it)
            self._templates_that_waited(doc, labels)
            try:
                # (the wait is over: no second look at it)
                self._bring_up_to_date(doc, first=doc is self.doc, gate=False)
            except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.idle_tick", page=getattr(doc, "path", ""))
        if timer is not None and not self._idle_docs:
            timer.stop()
        return [item[0] for item in ready]

    def _redraw_waited(self, doc, e) -> None:
        """The library watch's redraw that waited for the user (:meth:`_wait_until_idle`
        ``redraw``): the page shown is drawn again from the library files as they are now,
        a hidden tab when it is shown (its 'stale' mark)."""
        try:
            if doc is self.doc:
                from .libwatch import redraw_from_libraries

                redraw_from_libraries(doc)
                self._refresh()
                self._update_diag_chips()
                self.view.viewport().update()
            else:
                e["stale"] = True  # (drawn again when its tab is shown)
        except Exception:  # noqa: BLE001 -- keep showing what we have
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.idle_tick/redraw", page=getattr(doc, "path", ""))

    @staticmethod
    def _stays_changed_on_disk(doc) -> bool:
        """``doc``'s file (an element tab: its library's block) differs from what the tab
        read: it did not take what was saved meanwhile."""
        try:
            return bool(doc.changed_on_disk())
        except Exception:  # noqa: BLE001 -- not known: taken as changed
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.stays_changed_on_disk", page=getattr(doc, "path", ""))
            return True

    def _reload_that_waited(self, doc, kind: str) -> bool:
        """Run a reload that waited for the user."""
        from ...infra import logsetup

        waiting = self._chain_writes_waiting().pop(id(doc), None)
        versions = waiting[1] if waiting is not None and waiting[0]() is doc else {}
        try:
            if kind == "element":
                self._settle_saved_tabs([[doc.path, doc.element_name]], reload=True, gate=False)
                return False
            if not doc.changed_on_disk():
                return False
            # (a window of the tab holding typed values keeps the tab, as a change of the
            # user's does: the reload would close it with what was typed)
            if (
                not getattr(doc, "has_unstepped_changes", False)
                and (not doc.dirty or self._only_automatic_since_saved(doc))
                and not self._pending_windows(doc)
            ):
                return bool(self.revert_document(doc, ask=False))
            self._take_chain_write(doc, versions)
        except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
            logsetup.note_exception("ui.window.files.reload_that_waited", page=getattr(doc, "path", ""))
        return False

    def _hold_tab_updates_for_run(self) -> int:
        """Hold the open tabs' automatic updates while a chain runs."""
        n = 0
        for w in [self] + [w for w in (type(self).instances or ()) if w is not self]:
            try:
                jobs, entries = w.__dict__.get("_genie_jobs"), list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            if jobs is None:
                continue
            for e in entries:
                d = e["doc"]
                if isinstance(d, PendingPage):
                    continue
                try:
                    if not jobs.cancel_automatic(d, templates=False):
                        continue
                    # (composed again at the run's end, from the files as they are then)
                    ui_state(d).genie_sources_done.clear()
                    w._genies_updated(d)
                    w._clear_auto_update_line(d, templates=False)
                    w._after_library_run(d)
                    n += 1
                except Exception:  # noqa: BLE001 -- the tab lands its update as before
                    from ...infra import logsetup

                    logsetup.note_exception(
                        "ui.window.files.hold_tab_updates_for_run", page=getattr(d, "path", "")
                    )
        return n

    def _after_library_run(self, doc) -> None:
        """Update ``doc`` after a run that saves library genies."""
        from ..prefs.job_options import poll_ms

        waiting = self.__dict__.setdefault("_after_run_docs", [])
        if not any(d is doc for d in waiting):
            waiting.append(doc)
        timer = self.__dict__.get("_after_run_timer")
        if timer is None:
            timer = self._after_run_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._library_run_over)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _when_chain_names(self, doc) -> None:
        """Wait for the running chain's answer to name ``doc``'s element."""
        from ..jobs.update_run import UpdatePagesRun

        if not UpdatePagesRun.chain_progress:
            return
        from ..prefs.job_options import poll_ms

        waiting = self.__dict__.setdefault("_chain_named_docs", [])
        if not any(d is doc for d in waiting):
            waiting.append(doc)
        timer = self.__dict__.get("_chain_named_timer")
        if timer is None:
            timer = self._chain_named_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._chain_named_tick)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _chain_named_tick(self) -> list:
        """Timer for _when_chain_names."""
        from ..jobs.update_run import UpdatePagesRun

        waiting = list(self.__dict__.get("_chain_named_docs") or ())
        self._chain_named_docs = []
        timer = self.__dict__.get("_chain_named_timer")
        if (
            self.__dict__.get("_window_closed")
            or not UpdatePagesRun.users_busy
            or not UpdatePagesRun.chain_progress
        ):
            if timer is not None:
                timer.stop()
            return []
        versions = self._chain_versions_so_far()
        done = []
        for doc in waiting:
            if not any(e["doc"] is doc for e in self._docs):
                continue  # (closed, or moved to another window)
            try:
                if doc.changed_on_disk() and not self._chain_wrote(doc, versions):
                    self._chain_named_docs.append(doc)
                    continue
                self._update_stale_genies(doc, first=doc is self.doc)
            except Exception:  # noqa: BLE001 -- the chain's end looks at it
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.chain_named_tick", page=getattr(doc, "path", ""))
                continue
            done.append(doc)
        if timer is not None and not self._chain_named_docs:
            timer.stop()
        return done

    def _nested_job_refused(self, doc) -> None:
        """The worker refused a nested genie job because the library changed."""
        with contextlib.suppress(Exception):
            ui_state(doc).genie_sources_done.pop(NESTED_DONE, None)
        if getattr(doc, "read_only", False) or not app_settings().value(
            "opt/auto_update_genies", True, type=bool
        ):
            return
        try:
            self._update_stale_genies(doc, first=doc is self.doc)
        except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nested_job_refused", page=getattr(doc, "path", ""))

    def _library_run_over(self) -> list:
        """The timer of :meth:`_after_library_run`: when the run has ended (and a run in this
        process has brought the open tabs up to date: tabs_following), the tabs that waited
        for it are brought up to date, those still open in this window. The documents."""
        from ..jobs.update_run import UpdatePagesRun

        if self._libraries_being_saved() or UpdatePagesRun.tabs_following:
            return []
        timer = self.__dict__.get("_after_run_timer")
        if timer is not None:
            timer.stop()
        waiting, self._after_run_docs = list(self.__dict__.get("_after_run_docs") or ()), []
        if not any(w is self for w in type(self).instances or ()):
            return []  # (the window was closed)
        gen = editing_mod.LIBRARY_GENERATION[0]
        genies = option("auto_update_genies")
        done = []
        for doc in waiting:
            e = next((e for e in self._docs if e["doc"] is doc), None)
            if e is None:
                continue  # (closed, or moved to another window)
            if genies:
                e["genie_gen"] = gen
            try:
                self._bring_up_to_date(doc, first=doc is self.doc)
            except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.library_run_over", page=getattr(doc, "path", ""))
                continue
            done.append(doc)
        return done

    def _genie_answer_before_derive(self, doc) -> bool:
        """Apply a remembered genie answer before the page's derive."""
        if (
            isinstance(doc, ElementDocument)
            or doc.read_only
            or not option("auto_update_genies")
            or self._runs_hold_tabs()
        ):
            return False
        from ..jobs import genie_jobs

        got = genie_jobs.preapply_answer(doc)
        if got is None:
            return False
        names, step, changes, sources, n = got
        done = doc.ui.genie_sources_done
        for k in changes:
            if sources.get(k) is not None:
                done[k] = sources[k]
        # (no range replaced: only the link table took the current stamps, said quietly)
        doc.ui.genie_preapplied = (names, step, not n)
        return True

    def _genie_answer_said(self, doc) -> None:
        """The open checks' line for a genie update :meth:`_genie_answer_before_derive`
        applied: as _genie_job_finished says a landed one (the page shows it already)."""
        pre = ui_state(doc).genie_preapplied
        if not pre:
            return
        doc.ui.genie_preapplied = None
        names, step = pre[0], pre[1]
        quiet = len(pre) > 2 and pre[2]
        self._update_title()
        if doc is self.doc:
            self._update_actions()
        if not quiet:
            self._genies_updated_message(doc, names)
        self._note_auto_change(
            doc,
            "%s updated from %s library"
            % (
                ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
                "its" if len(names) == 1 else "their",
            ),
            step,
        )

    def _genies_updated_message(self, doc, names) -> None:
        from ..notify import notify

        path = getattr(doc, "path", "")
        what = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        if isinstance(doc, ElementDocument):
            notify(
                self,
                "Updated %s in %s. Save it to keep the change." % (what, doc.display_name),
                "success",
                detail=path,
                key="genies:%s:%s" % (path, doc.element_name),
            )
            return
        page = os.path.splitext(os.path.basename(path))[0]
        notify(self, "Updated %s on %s." % (what, page), "success", detail=path, key="genies:%s" % path)

    @staticmethod
    def _genies_updating(doc) -> list:
        """The root indices of the genies a background update of ``doc`` is composing
        again (none once the page's objects changed in number: the indices moved)."""
        st = getattr(ui_state(doc), "genies_updating", None)
        if not st or getattr(doc, "page", None) is None or st[0] != len(doc.page.objects):
            return []
        return list(st[1])

    def _genies_updated(self, doc) -> None:
        with contextlib.suppress(Exception):
            doc.ui.genies_updating = None
        if doc is self.doc:
            self.view.set_pending([])

    def _clear_auto_update_line(self, doc, genies: bool = True, templates: bool = True) -> None:
        """Clear the status line of an automatic update that won't land."""
        if doc is not self.doc:
            return
        msg = self.statusBar().currentMessage()
        if "you can go on working" not in msg:
            return
        if (genies and msg.startswith("Updating ")) or (
            templates and msg.startswith("Applying the template ")
        ):
            self.statusBar().clearMessage()

    def _genie_job_started(self, doc, names) -> None:
        if doc is not self.doc:
            return
        job = getattr(getattr(self, "_genie_jobs", None), "last_started", None)
        if job is not None and getattr(job, "quiet", False):
            # (the end of a run on a page that shows the save already: it only finishes the
            # save's step, nothing on the page is marked as changing)
            return
        if job is not None and getattr(job, "template", False):
            # (named as its library spells it)
            from ..names import template_name

            self.statusBar().showMessage(
                "Applying the template %s again (you can go on working)…"
                % ", ".join(template_name(x) for x in names[:1])
            )
            return
        if doc is self.doc:
            self.statusBar().showMessage(
                "Updating %s %s. The genies marked in amber change when it is "
                "done (you can go on working)…"
                % (
                    ", ".join(names[:3]),
                    "in this element" if isinstance(doc, ElementDocument) else "on this page",
                )
            )

    def _genie_job_finished(
        self, doc, names, n, job=None, stepped: bool = False, why: str = "", below=None, quiet: bool = False
    ) -> None:
        """A genie update of ``doc`` landed."""
        if not any(e["doc"] is doc for e in self._docs):
            return  # (the page was closed meanwhile)
        stepped = bool(stepped or getattr(job, "stepped", False))
        why = why or getattr(job, "why", "")
        quiet = bool(quiet or getattr(job, "quiet", False))
        below = below if below is not None else getattr(job, "below", None)
        self._note_left_by_previous(doc, job)
        if getattr(job, "unfinished", False):
            # a genie left as stored (its library unreadable then): the next open or tab
            # switch tries again, not only the next change of a file
            ui_state(doc).genie_sources_done.clear()
        if job is None or getattr(job, "bindings", None) is None:
            self._genies_updated(doc)
        if job is not None and getattr(job, "template", False):
            self._template_job_landed(doc, names, n)
            return
        if job is not None and getattr(job, "bindings", None) is not None:
            self._genie_form_job_landed(doc, names, n)
            return
        if n is None or n < 0:
            self._genie_job_failed(doc, names)
            return
        if stepped and self._fold_into_save_step(doc, why, below):
            # (said with the save's step: no line, no second entry in the bar, one undo step)
            self._clear_auto_update_line(doc)
            if n:
                self._mark_modeless_reason([doc], "replace")
                if doc is self.doc:
                    self._refresh()
            if doc is self.doc:
                self._update_actions()
            self._update_title()
            return
        if stepped and why in ("save", "follow"):
            top = (getattr(doc, "_undo", None) or [None])[-1]
            with contextlib.suppress(AttributeError):
                top.genie_save_step = True
        if not n:
            self._genie_job_unchanged(doc, names, job, stepped, why, quiet)
            return
        self._mark_modeless_reason([doc], "replace")
        if doc is self.doc:
            self._refresh()
            self._update_actions()
            self._genies_updated_message(doc, names)
        self._update_title()
        top = (getattr(doc, "_undo", None) or [None])[-1]
        self._note_auto_change(doc, self._genies_from_library(names), top)

    @staticmethod
    def _genies_from_library(names) -> str:
        """The automatic change's name of a genie update: 'a, b updated from their library'."""
        return "%s updated from %s library" % (
            ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
            "its" if len(names) == 1 else "their",
        )

    def _template_job_landed(self, doc, names, n) -> None:
        """The open's template step, computed in the worker, landed: said as the inline step
        says it, then the genie step that follows it (tabs._post_open_checks). A remembered
        genie answer lands at once: the window shows both steps with ONE refresh."""
        own_hold = self.__dict__.get("_refresh_hold") is None
        if own_hold:
            self._refresh_hold = []
        try:
            link = names[0] if names else ""
            if n and n > 0:
                self._mark_modeless_reason([doc], "replace")
                if doc is self.doc:
                    self._refresh()
                    self._update_actions()
                self._update_title()
                self._template_message(doc, link, True)
                top = (getattr(doc, "_undo", None) or [None])[-1]
                from ..names import template_name

                self._note_auto_change(doc, "template %s re-applied" % template_name(link), top)
            elif n is None or n < 0:
                self._template_message(doc, link, False)
            if ui_state(doc).steps_after_template:
                # (the open's symbol, placeholder genie and genie steps, which waited for
                # it: CtDraw's load order, tabs._open_steps_after_template)
                self._open_steps_after_template(doc)
            elif not doc.read_only and option("auto_update_genies"):
                self._update_stale_genies(doc, first=doc is self.doc)
        finally:
            held = self.__dict__.pop("_refresh_hold", None) if own_hold else None
        if held and doc is self.doc:
            units = []
            for h in held:
                units.extend(h[2])
            self._refresh(keep_sel=all(h[0] for h in held), rebuild_tree=any(h[1] for h in held), units=units)
            self._update_actions()

    def _genie_form_job_landed(self, doc, names, n) -> None:
        """A genie dialog's OK that ran in the worker landed."""
        from ..notify import notify, open_log_action

        if n and n > 0:
            self._mark_modeless_reason([doc], "replace")
            if doc is self.doc:
                self._refresh()
                self._update_actions()
            self._update_title()
            notify(self, "Updated the parameters of %s." % names[0], "success")
        elif n is None or n < 0:
            notify(
                self,
                "Could not apply the parameters of %s. The genie keeps its old values." % names[0],
                "error",
                actions=[open_log_action()],
            )

    def _genie_job_failed(self, doc, names) -> None:
        """A genie update of ``doc`` failed: the next open / tab switch tries again."""
        from ..notify import notify, open_log_action

        ui_state(doc).genie_sources_done.clear()
        if doc is self.doc:
            text = (
                "Could not update %s in this element. Opening it again tries again."
                if isinstance(doc, ElementDocument)
                else "Could not update %s on this page. Opening the page again tries again."
            )
            notify(
                self,
                text % ", ".join(names[:3]),
                "error",
                actions=[open_log_action()],
                key="genies:%s" % getattr(doc, "path", ""),
            )

    def _genie_job_unchanged(self, doc, names, job, stepped: bool, why: str, quiet: bool) -> None:
        """A genie update of ``doc`` that changed no genie landed: the 'Updating ... (you can
        go on working)' line goes."""
        self._clear_auto_update_line(doc)
        if getattr(job, "changed_on_disk", False):
            self._nested_job_refused(doc)
            return
        if stepped:
            # (no genie changed, but the page's link table took the genies' current
            # stamps: one undo step, the page unsaved; it is an automatic change, never
            # one of the user's, for the bar's Undo, the close question and the chain)
            self._update_title()
            if doc is self.doc:
                self._update_actions()
            top = (getattr(doc, "_undo", None) or [None])[-1]
            # (the end of the run on a tab that took the save, a step of the user's in
            # between: only the stamps the run wrote, named as the save's update, not as
            # a genie the user never saved)
            what = self._save_step_entry(doc) if quiet and why == "follow" else None
            self._note_auto_change(doc, what or self._genies_from_library(names), top)

    @staticmethod
    def _fold_into_save_step(doc, why: str, below) -> bool:
        """Fold an update into the save step that caused it."""
        if why != "follow" or below is None or not getattr(below, "genie_save_step", False):
            return False
        undo = getattr(doc, "_undo", None) or []
        if len(undo) < 2 or undo[-2] is not below:
            return False
        if not any(c is below for _w, c in ui_state(doc).auto_changes or ()):
            return False  # (saved since, or undone and made again)
        top = undo[-1]
        with contextlib.suppress(AttributeError):
            top.automatic = True
        if not doc.fold_last_step():
            return False
        doc.ui.auto_count = int(getattr(doc.ui, "auto_count", 0) or 0) + 1
        return True

    def _propagate_symbol(self, doc) -> None:
        """A symbol was saved: update open pages that place it."""
        from .open_tabs import page_tab

        low = {(doc.library_name.lower(), doc.element_name.lower())}
        updated = pages = elems = 0
        changed = []
        for w in type(self).instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                if d is doc or isinstance(d, PendingPage):
                    continue
                elem = isinstance(d, ElementDocument)
                if not elem and not page_tab(d):
                    # (a read-only page tab is left, as its open re-derives nothing:
                    # _tab_takes_symbols)
                    continue
                try:
                    if elem and not w._tab_takes_symbols(d):
                        continue
                    if ui_state(d).steps_after_template:
                        # (its open's template step is in the worker: its symbols follow it,
                        # as CtDraw's load meets the template first)
                        continue
                    path = d.library_path(doc.library_name, doc.element_name, "ctl")
                    if not path or path_key(path) != path_key(doc.path):
                        continue
                    # (an element tab: the symbols it places directly; the copies inside the
                    # genies nested in it are composed by _queue_open_genie_updates. A page
                    # remembers the version below, when it took it)
                    n = w._placed_symbol_step(d, low, remember=elem)
                except Exception:  # noqa: BLE001 -- never let a page block the save
                    _note_exc("open_genie_updates._propagate_symbol")
                    continue
                took = bool(n or getattr(d, "symbol_stamps_updated", False))
                if took and not elem:
                    # (the version it took: an update undone on the page is not made again
                    # by a later automatic one, _update_placed_symbols)
                    w._remember_symbol_versions(d, low)
                if took:
                    changed.append((w, d))
                if n:
                    with contextlib.suppress(RuntimeError):
                        w._mark_modeless_reason([d], "replace")
                updated += n
                if elem:
                    elems += took
                else:
                    pages += took
        # (a page or element shown in another window is drawn again now, with its title)
        self._follow_tabs_changed(changed)
        self._update_title()
        if pages or elems:
            from ..names import plural
            from ..notify import notify

            where = " and ".join(
                x
                for x in (
                    plural(pages, "open page") if pages else "",
                    plural(elems, "open library element") if elems else "",
                )
                if x
            )
            notify(
                self,
                "Updated %d placed %s on %s. Save %s to keep the change%s."
                % (
                    updated,
                    doc.display_name,
                    where,
                    "it" if pages + elems == 1 else "them",
                    "; Tools > Update Pages updates the others" if pages else "",
                ),
                "success",
            )

    def _library_cache_notice(self, warn: str) -> None:
        """CtDraw's in-memory element cache caveat (Studio always runs a
        Graphics Builder): once per session (this editor process, every window) as a
        NON-blocking notice, after that on the status bar only. While a run of several
        steps goes on in any window (_cache_notice_held) it waits for that run's end."""
        if not warn:
            return
        if SharedState.cache_notice_holds:
            # (a run of several steps is going on: said once when it has ended)
            if not SharedState.cache_notice_waiting:
                SharedState.cache_notice_waiting = warn
            return
        from ..builder_close import auto_close

        warn = auto_close(self, warn)  # (the option: the builder is closed instead)
        if not warn:
            return
        self.statusBar().showMessage(
            "Saved. Close the Graphics Builder to see this change there (Tools > Close Graphics Builder).",
            8000,
        )
        if SharedState.cache_notice_shown:
            return
        SharedState.cache_notice_shown = True
        self._show_cache_notice_box(warn)

    def _show_cache_notice_box(self, warn: str) -> None:
        """The non-blocking box of _library_cache_notice (a seam: tests count it)."""
        from ..builder_close import add_close_button

        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Information, "Graphics Builder", warn, QtWidgets.QMessageBox.Ok, self
        )
        add_close_button(box, self)
        box.setModal(False)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        box.show()

    def _close_graphics_builder(self) -> None:
        """Tools > Close Graphics Builder (ui/builder_close)."""
        from ..builder_close import close_builder

        close_builder(self)

    @contextlib.contextmanager
    def _cache_notice_held(self):
        """Hold CtDraw's cache notice until a multi-step run is done."""
        SharedState.cache_notice_holds += 1
        try:
            yield
        finally:
            SharedState.cache_notice_holds -= 1
            if not SharedState.cache_notice_holds:
                warn = SharedState.cache_notice_waiting
                SharedState.cache_notice_waiting = ""
                if warn:
                    with contextlib.suppress(RuntimeError):  # (the window has gone)
                        self._library_cache_notice(warn)

    def _after_library_write(self, lib_path: str, *more: str) -> None:
        """Libraries changed (``lib_path`` and ``more``): the pages open in the other tabs
        of every editor window re-render on their next activation (their genies / symbols
        show the new art), the Library panels re-list (new elements, new thumbnails), the
        catalog of each library's folder is looked at again."""
        paths = [p for p in (lib_path,) + more if p]
        for w in type(self).instances or [self]:
            try:
                shown, entries = w.doc, list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                if e["doc"] is not shown:
                    e["stale"] = True
            with contextlib.suppress(Exception):
                proj = (
                    os.path.dirname(os.path.abspath(shown.path))
                    if shown is not None and getattr(shown, "path", "")
                    else os.path.dirname(os.path.abspath(paths[0]))
                )
                w.library_dock.set_project_dir(proj)
                w.library_dock.refresh()
        dirs = []
        for p in paths:
            d = os.path.dirname(os.path.abspath(p))
            if os.path.normcase(d) not in {os.path.normcase(x) for x in dirs}:
                dirs.append(d)
        with contextlib.suppress(Exception):
            from ...io.catalog import catalog_for

            cat = catalog_for()
            for d in dirs:
                cat.touch(d)
        # (the open Used By windows read their users again)
        with contextlib.suppress(RuntimeError):
            self._follow_used_by_windows()

    def _confirm_replace_new(self, errors=None) -> str:
        """The first save of a new page whose file exists now: 'replace' | 'saved' (saved
        under another name) | 'cancel' (the default)."""
        name = os.path.basename(self.doc.path)
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Page Already Exists")
        box.setText("%s already exists. It was saved after this new page was started here." % name)
        box.setInformativeText(
            "Replacing it loses the page that is on disk now. Save this page under another name instead?"
        )
        copy_btn = box.addButton("Save As…", QtWidgets.QMessageBox.AcceptRole)
        over = box.addButton("Replace", QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is over:
            return "replace"
        if clicked is copy_btn:
            return "saved" if self.save_as() else "cancel"
        if errors is not None:
            errors.append(
                "%s was not saved: a page of that name was saved after it was "
                "started, and you chose not to replace it." % name
            )
        return "cancel"

    def _save_to(self, path=None, errors=None) -> bool:
        """Save, and report anything the save couldn't do."""
        if isinstance(self.doc, ElementDocument):
            return self._save_element_to(path, errors=errors)
        if path_key(self.doc.path) in SharedState.renaming:
            why = "%s is being renamed. Save it again in a moment." % os.path.basename(self.doc.path)
            if errors is not None:
                errors.append(why)
            self.statusBar().showMessage(why, 8000)
            return False
        in_place = path is None or path_key(path) == path_key(self.doc.path)
        if path is None and self._version_refused(self.doc, errors):
            return False
        # (a page file the chain after a genie save wrote from the version this tab holds is
        # the tab's own: no question, library._take_chain_write)
        if in_place and self.doc.changed_on_disk() and not self._take_chain_write(self.doc, look_again=False):
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Warning,
                "Page Changed on Disk",
                "%s was changed on disk since it was opened here.\n\nOverwrite it "
                "with this version?" % os.path.basename(self.doc.path),
                parent=self,
            )
            over = box.addButton("Overwrite", QtWidgets.QMessageBox.AcceptRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is not over:
                if errors is not None:
                    errors.append(
                        "%s was not saved: it changed on disk since it was "
                        "opened, and you chose not to overwrite it." % os.path.basename(self.doc.path)
                    )
                return False
        if in_place and getattr(self.doc, "_disk_stamp", 0) is None and os.path.exists(self.doc.path):
            # a new page (not on disk when it was made here) whose file was made since, by
            # another tab or program: its first save does not replace that page unasked
            r = self._confirm_replace_new(errors)
            if r != "replace":
                return r == "saved"  # saved under another name / cancelled
        if in_place and self.doc.damage:
            r = self._confirm_damaged_save(errors)
            if r != "overwrite":
                return r == "saved"  # saved as a copy / cancelled
        target = path or self.doc.path
        if not self._confirm_builder_save(target, errors):
            return False
        with contextlib.suppress(Exception):
            self._render_thumbnail()
        try:
            self.doc.save(path)
        except Exception as e:  # noqa: BLE001 -- a refused save must never escape
            _note_exc("open_genie_updates._save_to")
            return self._save_failed(path or self.doc.path, e, errors, offer_save_as=in_place)
        from ..recovery import AutosaveController

        AutosaveController.forget(self.doc)
        self._watch(self.doc.path)  # a replace drops the watch
        warn = getattr(self.doc, "save_warning", "")
        if warn:
            if errors is not None:
                errors.append("%s: %s" % (os.path.basename(self.doc.path), warn))
            else:
                warn(self, "Save", warn)
        self._refresh_runtime_page()
        with contextlib.suppress(Exception):  # page browser listings
            from ...io.catalog import catalog_for

            catalog_for().touch(os.path.dirname(os.path.abspath(self.doc.path)))
        with contextlib.suppress(RuntimeError):  # (the Used By windows: the page's status)
            self._follow_used_by_windows()
        with contextlib.suppress(Exception):  # a report only: never fails a save
            self._after_save_checks(errors)
        return True
