"""How the open tabs follow library changes: genies and symbols updated, automatic changes."""

from __future__ import annotations

from ...infra.paths import path_key

from ..prefs.settings import option

from typing import Optional

from ..main_window_base import (
    ElementDocument,
    QtWidgets,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)

from .files import NESTED_DONE


class OpenGenieUpdates:
    """The open pages and elements updated after a library save, and the automatic changes that wait for the user."""

    def _propagate_element(self, doc) -> None:
        """A genie was saved: update the open pages and elements that use it."""
        if getattr(doc, "kind", "") == "symbol":
            self._propagate_symbol(doc)
            # the copies of the symbol nested in placed genies and in open element tabs, in
            # every window, as after a genie save
            if option("auto_update_genies"):
                with self._updates_for("save"):
                    self._queue_open_genie_updates()
            return
        auto = option("auto_update_genies")
        # (every editor window's pages, as a symbol save does)
        for w in type(self).instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
                w._update_by_previous_version(doc, auto)
        # every open page and element tab whose genies are now out of date gets the changed
        # genies' copies composed again (CtDraw's page load), after this save returns: the
        # page shown first, then the other open pages, in every window
        if auto:
            # (the element tabs nearest the saved genie first: _update_stale_nested)
            with self._updates_for("save", [(doc.path, doc.element_name)]):
                self._queue_open_genie_updates()
        self._update_title()

    def _update_by_previous_version(self, doc, auto: bool) -> int:
        """Update this window's page tabs that place the saved genie."""
        from .open_tabs import page_tab

        data = doc.previous_bytes() if callable(getattr(doc, "previous_bytes", None)) else None
        if data is None:
            return 0
        held = auto and self._runs_hold_tabs()
        if auto and not held:
            return 0  # (the update of every out of date genie takes them now)
        key = (doc.library_name, doc.element_name)
        low = (key[0].lower(), key[1].lower())
        n = 0
        for e in list(getattr(self, "_docs", ())):
            d = e["doc"]
            # (a read-only tab, an earlier version or a page opened to view, is left, as
            # its open composes nothing)
            if d is doc or not page_tab(d):
                continue
            if ui_state(d).steps_after_template:
                # (its open's template step is in the worker: its genies are composed
                # after it, tabs._open_steps_after_template, as CtDraw's load orders them)
                continue
            try:
                path = d.library_path(doc.library_name, doc.element_name, "ctm")
                if not path or path_key(path) != path_key(doc.path):
                    continue
                roots = [i for i, (a, b) in d.genie_instances() if (a.lower(), b.lower()) == low]
            except Exception:  # noqa: BLE001 -- never let a page block the save
                from ...infra import logsetup

                logsetup.note_exception(
                    "ui.window.files.update_by_previous_version", page=getattr(d, "path", "")
                )
                continue
            if not roots:
                continue
            # (the instances are marked on the canvas until the update lands, as
            # _update_stale_genies marks the genies it composes again)
            d.ui.genies_updating = (len(d.page.objects), roots)
            if d is self.doc:
                self.view.set_pending(self._genies_updating(d))
            self._genie_job_runner().submit(
                d, {}, [doc.display_name], first=d is self.doc, why="save", previous={key: (doc.path, data)}
            )
            n += 1
        return n

    def _note_left_by_previous(self, doc, job) -> None:
        """A genie save's update of ``doc`` by the genie's previous version
        (:meth:`_update_by_previous_version`) landed and left instances it did not
        reproduce: with Settings' Update changed genies off nothing updates them later, so
        the window says so (with it on, the update of every out of date genie takes them)."""
        left = int(getattr(job, "left", 0) or 0)
        if not left or not getattr(job, "previous", None) or option("auto_update_genies"):
            return
        from ..names import plural
        from ..notify import notify

        what = " and ".join(getattr(job, "names", None) or ()) or "the genie"
        page = os.path.splitext(os.path.basename(getattr(doc, "path", "") or ""))[0]
        notify(
            self,
            "%s on %s %s left out of date. Update Pages updates %s."
            % (
                plural(left, "placed " + what, "placed " + what),
                page,
                "is" if left == 1 else "are",
                "it" if left == 1 else "them",
            ),
            "info",
            detail=getattr(doc, "path", ""),
        )

    #: what the open tabs' genie updates queued now are for (:meth:`_updates_for`): their
    #: GenieJob.why and the library elements just saved [(library file, element)]
    _queue_for: tuple = ("", ())

    @staticmethod
    @contextlib.contextmanager
    def _updates_for(why: str, saved=()):
        """The open tabs' genie updates queued in the block are for ``why`` (GenieJob.why:
        'save' after a library save, 'follow' at the end of a run that saved library genies
        again); ``saved`` [(library file, element)]: the element tabs nearest them go first
        (_update_stale_nested). Every window's (:meth:`_queue_open_genie_updates`)."""
        was = OpenGenieUpdates._queue_for
        OpenGenieUpdates._queue_for = (why, tuple(saved or ()))
        try:
            yield
        finally:
            OpenGenieUpdates._queue_for = was

    def _queue_open_genie_updates(self) -> None:
        """Queue genie updates for every open page, the shown one first."""
        gen = editing_mod.LIBRARY_GENERATION[0]
        wins = [self] + [w for w in (type(self).instances or ()) if w is not self]
        for w in wins:
            try:
                docs, shown = list(w._docs), w.doc
                hold = getattr(w._genie_job_runner(), "hold", None)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            order = [e for e in docs if e["doc"] is shown] + [e for e in docs if e["doc"] is not shown]
            with contextlib.ExitStack() as held:
                if callable(hold):
                    held.enter_context(hold())
                for e in order:
                    if isinstance(e["doc"], PendingPage):
                        continue
                    # (an element tab too: the genies nested in it, _update_stale_nested)
                    e["genie_gen"] = gen
                    with contextlib.suppress(Exception):
                        w._update_stale_genies(e["doc"], first=e["doc"] is shown)

    @staticmethod
    def _follow_tabs_changed(changed) -> None:
        """Redraw tabs that took a library save."""
        wins: list = []
        for w, _d in changed or ():
            if not any(w is x for x in wins):
                wins.append(w)
        for w in wins:
            with contextlib.suppress(RuntimeError):  # (a window being destroyed)
                if any(x is w and d is w.doc for x, d in changed):
                    w._sync_modeless("replace")
                    w._refresh()
                    w._update_actions()
                w._update_title()

    def _look_at_open_genies_again(self) -> None:
        """Check open pages and element tabs for genie updates again."""
        genies = option("auto_update_genies")
        if genies:
            for w in [self] + [w for w in (type(self).instances or ()) if w is not self]:
                try:
                    entries = list(w._docs)
                except RuntimeError:  # swallow-ok: a window being destroyed
                    continue
                for e in entries:
                    done = getattr(getattr(e["doc"], "ui", None), "genie_sources_done", None)
                    if done:
                        done.clear()  # (composed again from the files as they are)
        with contextlib.suppress(Exception):
            self._follow_symbols_in_tabs()
        if genies:
            with contextlib.suppress(Exception):
                self._queue_open_genie_updates()

    def _genie_job_runner(self):
        """This window's genie update jobs (ui/jobs/genie_jobs), made on first use."""
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is None:
            from ..jobs.genie_jobs import GenieJobs

            jobs = self._genie_jobs = GenieJobs(self)
            jobs.started.connect(self._genie_job_started)
            jobs.finished.connect(self._genie_job_finished)
            jobs.gate = self._genie_may_land
        return jobs

    def _genie_may_land(self, doc, waited: float = 0.0) -> bool:
        """Should a finished genie update wait because the user is busy?"""
        if doc is not None and doc is self.__dict__.get("_saving_doc"):
            return False  # (its save is under way: ui/dialog/element_save)
        modal = QtWidgets.QApplication.activeModalWidget()
        prop = getattr(modal, "property", None)
        if modal is not None and not (callable(prop) and prop("ctg_lets_open_tabs_update")):
            # (a dialog the user works in; not the Genie Saved question or the search for
            # what uses a saved element, under which the open tabs take the save at once:
            # what is open comes first)
            return False
        if waited < redo_wait_s() and self._user_can_redo(doc):
            return False
        if doc is self.doc:
            view = getattr(self, "view", None)
            if view is not None and view.busy():
                return False
        return True

    @staticmethod
    def _user_can_redo(doc) -> bool:
        """``doc`` has a step of the user's to redo: an automatic update undone (the bar's
        Undo) is not one (a new version of the library replaces it anyway, and the update
        the user undid is not made again until then)."""
        if getattr(doc, "can_redo", None) is None or not doc.can_redo():
            return False
        redo = getattr(doc, "_redo", None)
        if redo is None:
            return True
        return any(not getattr(c, "automatic", False) for c in redo)

    def _note_auto_change(self, doc, what: str, command, changed: bool = True) -> None:
        """List an automatic change in the bar over the canvas."""
        if command is not None:
            with contextlib.suppress(AttributeError):
                command.automatic = True
        if not changed:
            return
        items = ui_state(doc).auto_changes
        if items is None:
            items = doc.ui.auto_changes = []
        doc.ui.auto_count = int(getattr(doc.ui, "auto_count", 0) or 0) + 1
        same = next((k for k, (w, _c) in enumerate(items) if w == what), None)
        if same is not None and command is None:
            pass  # (nothing new to undo: listed already)
        elif same is not None and items[same][1] is None:
            items[same] = (what, command)
        else:
            items.append((what, command))
        doc.ui.auto_bar_dismissed = False
        if doc is self.doc:
            self._auto_bar.show_for(doc)

    def _undo_auto_changes(self) -> None:
        """The bar's Undo: the automatic steps on top of the page's undo history go back;
        one the user made after them stays (it says so)."""
        doc = self.doc
        auto = {id(c) for _w, c in (ui_state(doc).auto_changes or []) if c is not None}
        n = 0
        while getattr(doc, "_undo", None) and id(doc._undo[-1]) in auto:
            self.undo()
            n += 1
        if n:
            doc.ui.auto_changes = [(w, c) for w, c in doc.ui.auto_changes if c is not None and c in doc._undo]
            if not doc.ui.auto_changes:
                doc.ui.auto_changes = []
            self._auto_bar.show_for(doc)
        elif auto:
            self.statusBar().showMessage(
                "You made changes after the automatic updates: undo them first (Edit > Undo).", 8000
            )

    @staticmethod
    def _only_auto_changes(doc) -> bool:
        """The page's unsaved changes are all automatic ones (no step of the user's)."""
        undo = getattr(doc, "_undo", None) or []
        auto = {id(c) for _w, c in (ui_state(doc).auto_changes or []) if c is not None}
        return bool(undo) and all(id(c) in auto for c in undo)

    def _ask_save_on_close(self, doc, pending, close_word: str = "closing it"):
        """Ask whether to save on close."""
        name = self._doc_name(doc)
        if not pending and self._only_auto_changes(doc):
            from ..editor.notices import auto_change_names

            whats = "; ".join(auto_change_names(doc.ui.auto_changes))
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Question,
                "Unsaved Changes",
                "%s was updated automatically: %s. Save these updates before %s?" % (name, whats, close_word),
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                self,
            )
            box.setDefaultButton(QtWidgets.QMessageBox.Discard)
            return box.exec()
        return QtWidgets.QMessageBox.question(
            self,
            "Unsaved Changes",
            "Save changes to %s before %s?%s" % (name, close_word, self._pending_text(pending)),
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
        )

    def _update_stale_genies(self, doc, first: bool = False) -> list:
        """Bring the genies of ``doc`` up to date."""
        if ui_state(doc).steps_after_template:
            return []
        if self._runs_hold_tabs():
            self._after_library_run(doc)
            return []
        if self._waits_until_idle(doc):
            return []
        if isinstance(doc, ElementDocument):
            return self._update_stale_nested(doc, first)
        if getattr(doc, "read_only", False):
            return []
        changes, sources = {}, {}
        with contextlib.suppress(Exception):
            changes = doc.genie_changes()
            if changes:
                sources = doc.genie_sources(set(changes))
        # a genie whose nested genie changed stays 'out of date' until the genie that
        # nests it is saved again: once updated, it is not composed again until a file
        # it is made of changes
        done = ui_state(doc).genie_sources_done
        if done is None:
            done = doc.ui.genie_sources_done = {}
        changes = {k: v for k, v in changes.items() if sources.get(k) is None or done.get(k) != sources[k]}
        if not changes:
            return []
        names = set()
        for k, v in changes.items():
            names |= {"%s.%s" % c for c in v} if v else {"%s.%s" % k}
        names = sorted(names, key=str.lower)
        for k in changes:
            if sources.get(k) is not None:
                done[k] = sources[k]
        from ...infra import answer_memo, noop_memo

        memo_key = doc.genie_noop_key(changes)
        if noop_memo.known(memo_key):
            return []  # (composed before with these files: no change)
        why = self._queue_for[0]
        # (the end of a run on a page that took the save already: it shows the change, so no
        # marks and no line while it is composed again, and its step becomes part of the
        # save's, or is named as it: _genie_job_finished)
        quiet = why == "follow" and self._took_the_save(doc)
        # composed before with these files: the worker's answer then, at once
        ans = answer_memo.get(memo_key)
        if ans is not None:
            from ..jobs import genie_jobs

            n = None
            top = genie_jobs.undo_top(doc)
            with contextlib.suppress(Exception):
                n = genie_jobs.apply_answer(doc, ans)
            if n is not None and n >= 0:
                self._genie_job_finished(
                    doc,
                    names,
                    n,
                    stepped=genie_jobs.undo_top(doc) is not top,
                    why=why,
                    below=top,
                    quiet=quiet,
                )
                return names
        # the genies being composed again are marked on the canvas until the update lands
        if not quiet:
            with contextlib.suppress(Exception):
                doc.ui.genies_updating = (
                    len(doc.page.objects),
                    [i for i, (a, b) in doc.genie_instances() if (a.lower(), b.lower()) in changes],
                )
                if doc is self.doc:
                    self.view.set_pending(self._genies_updating(doc))
        self._genie_job_runner().submit(doc, changes, names, first=first, why=why, quiet=quiet)
        return names

    def _took_the_save(self, doc) -> bool:
        """Did ``doc``'s tab take the library save whose run just ended?"""
        jobs = self.__dict__.get("_genie_jobs")
        pending = getattr(jobs, "pending", None)
        if callable(pending) and any(getattr(j, "why", "") == "save" for j in pending(doc)):
            return True
        return self._save_step_entry(doc) is not None

    @staticmethod
    def _save_step_entry(doc) -> Optional[str]:
        """What the automatic changes bar says for the newest update of a library save that
        ``doc``'s undo history still holds (its step marked genie_save_step:
        :meth:`_genie_job_finished`); None when it holds none (saved since, or undone)."""
        held = {id(c) for c in getattr(doc, "_undo", None) or () if getattr(c, "genie_save_step", False)}
        for what, c in reversed(ui_state(doc).auto_changes or ()):
            if c is not None and id(c) in held:
                return what
        return None

    def _update_stale_nested(self, doc, first: bool = False) -> list:
        """Recompose an element tab's nested genies that changed since it was saved."""
        from ..dialog.history_versions import is_version

        if doc.is_new or getattr(doc, "read_only", False) or is_version(doc):
            return []
        keys, sources = frozenset(), frozenset()
        try:
            if doc.changed_on_disk() and not self._take_chain_write(doc, look_again=False):
                if self._libraries_being_saved():
                    self._after_library_run(doc)
                    # (the chain's helper wrote it and does not name it yet: looked at
                    # again as soon as its answer so far does, not at its end)
                    self._when_chain_names(doc)
                return []
            # (first the library symbols placed in it, as a page's open does them before
            # its genies: the worker then composes from the records with them)
            self._update_placed_symbols(doc)
            if self._waits_until_idle(doc):
                return []  # (both when the user has finished)
            keys = doc.nested_genie_changes()
            if keys:
                sources = doc.nested_genie_sources()
        except Exception:  # noqa: BLE001 -- the tab stays as it was read
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.update_stale_nested", page=doc.path)
            return []
        if not keys:
            return []
        done = ui_state(doc).genie_sources_done
        if done is None:
            done = doc.ui.genie_sources_done = {}
        if done.get(NESTED_DONE) == sources:
            return []
        done[NESTED_DONE] = sources
        names, label = self._nested_copy_names(doc, keys)
        why, saved = self._queue_for
        quiet = why == "follow" and self._took_the_save(doc)
        # the genies whose copies change are marked on the canvas until the update lands
        if not quiet:
            with contextlib.suppress(Exception):
                doc.ui.genies_updating = (len(doc.page.objects), [i for i, _k in doc.genie_instances()])
                if doc is self.doc:
                    self.view.set_pending(self._genies_updating(doc))
        self._genie_job_runner().submit(
            doc,
            {k: None for k in keys},
            names,
            first=first,
            why=why,
            near=self._nesting_depth(doc, saved),
            quiet=quiet,
            **({"label": label} if label else {}),
        )
        return names

    @staticmethod
    def _nested_copy_names(doc, keys) -> tuple:
        """(names, undo step label) for recomposed nested copies."""
        from ...io.genieusage import TEMPLATE_COPY, nested_copy_labels

        try:
            spelled = nested_copy_labels(doc, keys)
        except Exception:  # noqa: BLE001 -- named by their keys then
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nested_copy_names", page=getattr(doc, "path", ""))
            spelled = sorted(("%s.%s" % k for k in keys), key=str.lower)
        lead = TEMPLATE_COPY[len("the ") :] if TEMPLATE_COPY.startswith("the ") else TEMPLATE_COPY
        names = [lead + x[len(TEMPLATE_COPY) :] if x.startswith(TEMPLATE_COPY) else x for x in spelled]
        templates = [x for x in spelled if x.startswith(TEMPLATE_COPY)]
        if templates and len(templates) == len(spelled):
            label = "%s updated" % ", ".join(names)
        elif templates:
            label = "update genies and template from library"
        else:
            label = ""  # (the job's own: 'update genies from library')
        return names, label

    @staticmethod
    def _nesting_depth(doc, saved) -> Optional[int]:
        """How deep the element tab nests the first of ``saved``."""
        if not saved:
            return None
        from ...format import genie_place as GP

        best = None
        try:
            res = doc._resolver()
            for path, element in saved:
                chain = GP.records_nesting_chain(doc.parsed.page.objects, path, element, res, shortest=True)
                if chain is not None and (best is None or len(chain) < best):
                    best = len(chain)
        except Exception:  # noqa: BLE001 -- its order only: after the tabs that nest it
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nesting_depth", page=getattr(doc, "path", ""))
            return None
        return best

    @staticmethod
    def _tab_takes_symbols(doc) -> bool:
        """An open page or element tab whose placed library symbols may be re-derived in it:
        not a restored tab not opened yet, a new element, a read-only page or library, an
        earlier version shown read-only, or one changed on disk since the tab read it (the
        reload question comes first)."""
        from ..dialog.history_versions import is_version

        if isinstance(doc, PendingPage) or getattr(doc, "read_only", False) or is_version(doc):
            return False
        if isinstance(doc, ElementDocument) and doc.is_new:
            return False
        return not doc.changed_on_disk()

    def _placed_symbol_step(self, doc, stale, remember: bool = True) -> int:
        """Re-derive stale placed library symbols in ``doc``."""
        top = (getattr(doc, "_undo", None) or [None])[-1]
        names = sorted(
            {
                "%s.%s" % key
                for _i, key, _k in doc.symbol_placements()
                if (key[0].lower(), key[1].lower()) in stale
            },
            key=str.lower,
        )
        n = doc.update_symbol_placements(stale) or 0
        now = (getattr(doc, "_undo", None) or [None])[-1]
        self._note_auto_change(
            doc,
            "symbols updated from their library (%s)" % ", ".join(names[:5]),
            now if now is not top else None,
            changed=now is not top,
        )
        if remember:
            self._remember_symbol_versions(doc, stale)
        return n

    @staticmethod
    def _remember_symbol_versions(doc, stale) -> None:
        """An update in its tab took the library symbols ``stale`` placed in ``doc`` to their
        current versions (an automatic one, the open's, a symbol save's, a run's): undone,
        it is not made again by an automatic update until a symbol is saved again
        (DocumentUiState.symbols_done; a failed or cancelled genie update leaves it)."""
        keys = {(a.lower(), b.lower()) for a, b in stale}
        st = ui_state(doc)
        if not keys or st is not getattr(doc, "ui", None):
            return
        st.symbols_done = frozenset(
            {x for x in st.symbols_done if x[0] not in keys} | OpenGenieUpdates._symbol_versions(doc, keys)
        )

    @staticmethod
    def _symbol_versions(doc, keys) -> frozenset:
        """((library, element), its current link-table entry), lower case, of the library
        symbols ``keys`` placed in ``doc`` (genie_place.current_link_entry: the element's
        stamp, new at each save of it); a symbol whose element does not read is left out."""
        from ...format import genie_place as GP

        out = set()
        for lib, elem in sorted(keys):
            try:
                path = doc.library_path(lib, elem, "ctl")
                if path:
                    out.add(((lib, elem), GP.current_link_entry(path, lib, elem)[1].lower()))
            except Exception:  # noqa: BLE001  # swallow-ok: no version to remember for it
                continue
        return frozenset(out)

    def _update_placed_symbols(self, doc, gate: bool = True) -> list:
        """Update an open tab's placed symbols whose element changed."""
        if not option("auto_update_symbols"):
            return []
        if ui_state(doc).steps_after_template:
            return []
        if self._runs_hold_tabs():
            self._after_library_run(doc)
            return []
        element = isinstance(doc, ElementDocument)
        try:
            if not self._tab_takes_symbols(doc):
                return []
            placed = doc.symbol_placements()
            stale = doc.stale_symbol_elements() if placed else set()
            done = ui_state(doc).symbols_done
            if stale and done:
                # (a version it took before and the user undid since stays undone)
                stale -= {k for k, _v in self._symbol_versions(doc, stale) & done}
            if not stale:
                return []
            if gate and self._waits_first(doc):
                return []  # (the template step or reload that waits comes first)
            if self._redo_holds(doc) if gate == "redo" else bool(gate) and not self._may_change_now(doc):
                # (a step in the middle of a drag would move the records under the view's
                # selection, and its release would move other objects)
                self._wait_until_idle(doc)
                return []
            names = sorted(
                {"%s.%s" % key for _i, key, _k in placed if (key[0].lower(), key[1].lower()) in stale},
                key=str.lower,
            )
            top = (getattr(doc, "_undo", None) or [None])[-1]
            self._placed_symbol_step(doc, stale)
        except Exception:  # noqa: BLE001 -- the tab stays as it was read (its open does it)
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.update_placed_symbols", page=doc.path)
            return []
        if (getattr(doc, "_undo", None) or [None])[-1] is top:
            return []
        self._mark_modeless_reason([doc], "replace")
        if doc is self.doc:
            # (drawn now when its tab's scene is on the canvas; during the tab's activation
            # (its first show) the activation draws it right after)
            ent = next((e for e in self._docs if e["doc"] is doc), None)
            if ent is not None and ent.get("scene") is self.scene:
                self._sync_modeless("replace")
                self._refresh()
                self._update_actions()
            from ..notify import notify

            what = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
            if element:
                notify(
                    self,
                    "Updated %s in %s. Save it to keep the change." % (what, doc.display_name),
                    "success",
                    detail=doc.path,
                    key="symbols:%s:%s" % (doc.path, doc.element_name),
                )
            else:
                notify(
                    self,
                    "Updated %s on %s. Save the page to keep the change."
                    % (what, os.path.splitext(os.path.basename(doc.path))[0]),
                    "success",
                    detail=doc.path,
                    key="symbols:%s" % doc.path,
                )
        self._update_title()
        return names

    def _follow_symbols_in_tabs(self) -> int:
        """Update placed symbols in every editor window's tabs."""
        if not option("auto_update_symbols"):
            return 0
        n = 0
        for w in [self] + [w for w in (type(self).instances or ()) if w is not self]:
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                if isinstance(e["doc"], PendingPage):
                    continue
                with contextlib.suppress(RuntimeError):
                    n += bool(w._update_placed_symbols(e["doc"]))
        return n

    def _bring_up_to_date(self, doc, first: bool = False, gate: bool = True) -> list:
        """Update ``doc``'s symbols and genies that changed since its tab took them."""
        took = list(self._update_placed_symbols(doc, gate=gate))
        if option("auto_update_genies"):
            self._update_stale_genies(doc, first=first)
        return took


def redo_wait_s() -> int:
    """How long an automatic update of an open tab waits for the user's steps to redo, which
    it would clear (s, counted from its first wait; 0: it does not wait): Settings > Pages and
    libraries > When a page opens, read at each look."""
    from ..prefs.settings import WHOLE_SPANS, whole

    return whole("opt/auto_update_redo_wait_s", WHOLE_SPANS["opt/auto_update_redo_wait_s"])
