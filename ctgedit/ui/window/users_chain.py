"""The chain after a library save: its helper run, its own writes, the open tabs it updates."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..main_window_base import editor_windows, main_window_class


from ...infra.paths import path_key

from ..prefs.settings import option


from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    app_settings,
    contextlib,
    editing_mod,
    json,
    os,
    ui_state,
)
from ..main_window_base import (
    PendingPage,
)
import weakref
from typing import List
from ..messages import inform

from .open_tabs import element_tab, page_mtimes, page_tab, run_state
from .run_text import page_label, write_text


class UsersChain:
    """Updating what uses a saved genie, symbol or template, in a helper process, and the open tabs that follow it."""

    # -- the chain helper's own writes -------------------------------------------------------
    @staticmethod
    def _chain_versions(res) -> dict:
        """What a chain's helper read and wrote."""

        def key(p):
            return path_key(str(p))

        elems, pages = {}, {}
        for row in (res or {}).get("element_versions") or ():
            try:
                p, el, read, wrote = row[:4]
                src = row[4] if len(row) > 4 else None
                elems[(key(p), str(el).lower())] = (
                    str(read),
                    str(wrote),
                    frozenset(tuple(x) for x in src) if src is not None else None,
                )
            except TypeError, ValueError:  # swallow-ok: a row of another form: not ours
                continue
        for row in (res or {}).get("page_versions") or ():
            try:
                p, read, wrote = row[:3]
                pages[key(p)] = (tuple(read), tuple(wrote))
            except TypeError, ValueError:  # swallow-ok: a row of another form: not ours
                continue
        listed = {key(p) for p in (res or {}).get("pages") or () if p}
        return {"elements": elems, "pages": pages, "listed": listed}

    @classmethod
    def _chain_versions_so_far(cls) -> dict:
        """:meth:`_chain_versions` of the chain running in its helper now, from its answer
        so far (written after each element and before the pages) and the records of the
        pages done; empty when none runs."""
        from ...workers import genie_worker as GW

        return cls._chain_versions(GW.chain_answer_so_far(run_state().chain_progress))

    def _chain_may_write(self, d) -> bool:
        """The chain after a library save running in its helper now may write ``d``'s file:
        a library element's tab (its plan saves elements), a page of its pages phase (its
        answer so far names it); never a new element, a read-only page or a tab not opened
        yet."""
        if isinstance(d, PendingPage):
            return False
        if isinstance(d, ElementDocument):
            return not d.is_new
        if getattr(d, "read_only", False):
            return False
        so_far = self._chain_versions_so_far()
        where = path_key(d.path or "")
        return where in so_far["listed"] or where in so_far["pages"]

    @staticmethod
    def _chain_wrote(d, versions) -> bool:
        """The chain's helper stored ``d``'s file (an element tab: its element's block; a
        page: the page file) from the version the tab holds as its baseline, and the file is
        still as it wrote it (``versions``: :meth:`_chain_versions`): its own write, not
        another program's."""
        if not versions:
            return False
        try:
            where = path_key(d.path or "")
            if isinstance(d, ElementDocument):
                v = versions["elements"].get((where, (d.element_name or "").lower()))
                if not v or v[0] == v[1] or d.baseline_block_sha1() != v[0]:
                    return False
                return d.stored_block_sha1() == v[1]
            v = versions["pages"].get(where)
            if not v or v[0] == v[1]:
                return False
            return (
                tuple(getattr(d, "_disk_stamp", None) or ()) == v[0]
                and editing_mod.EditableDocument.disk_stamp(d.path) == v[1]
            )
        except Exception:  # noqa: BLE001 -- not known: the tab is left (its notice says it)
            from ...infra import logsetup

            logsetup.note_exception("main_window.chain_wrote", page=getattr(d, "path", ""))
            return False

    def _take_chain_write(self, d, versions=None, look_again: bool = True, opened=None) -> bool:
        """Take what the chain's helper stored as the tab's baseline."""
        if versions is None:
            versions = self._chain_versions_so_far()
        if not self._chain_wrote(d, versions):
            return False
        try:
            if isinstance(d, ElementDocument):
                d.adopt_disk_version(opened)
            else:
                d.adopt_disk_version()
        except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
            from ...infra import logsetup

            logsetup.note_exception("main_window.take_chain_write", page=d.path)
            return False
        self._chain_write_taken(d, look_again)
        return True

    def _chain_write_taken(self, d, look_again: bool = True) -> None:
        """``d`` took a file the chain's helper wrote as its baseline: its disk notice goes,
        its 'composed with these files' memo empties (looked at again on its own records),
        and with ``look_again`` it is brought up to date now (gated by the user as every
        automatic update: files._bring_up_to_date)."""
        from ..editor import notices

        with contextlib.suppress(Exception):
            notices.remove(d, "disk")
            ui_state(d).disk_asked = None
        with contextlib.suppress(Exception):
            ui_state(d).genie_sources_done.clear()
        with contextlib.suppress(RuntimeError):
            self._update_title()
        if look_again:
            try:
                self._bring_up_to_date(d, first=d is self.doc)
            except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
                from ...infra import logsetup

                logsetup.note_exception("main_window.chain_write_taken", page=d.path)

    @staticmethod
    def _stored_behind(d, opened, res=None) -> bool:
        """Does the stored block quote an old stamp for a nested genie?"""
        if opened is None:
            return False
        try:
            from ...format import writer
            from ...io import updatepages
            from ...io.genieusage import TEMPLATE_COPY
            from ...format.symbols import split_lib_element

            resolver = d._resolver()
            page = opened[1].page
            raw = getattr(page, "link_table_raw", None)
            if resolver is None or not raw or not getattr(page, "link_count", 0):
                return False
            mine, stored = d.parsed.page.objects, page.objects
            if len(mine) == len(stored) and all(
                writer.object_payload(a) == writer.object_payload(b) for a, b in zip(mine, stored)
            ):
                return False
            stale: set = set()
            for k, v in updatepages.changed_genies(raw, resolver).items():
                stale |= {k} if v is None else set(v)
            label = ("%s.%s" % (d.library_name, d.element_name)).lower()
            for lb, names in (res or {}).get("not_composed") or ():
                if str(lb).lower() != label:
                    continue
                for nm in names or ():
                    nm = str(nm)
                    if nm.startswith(TEMPLATE_COPY):
                        nm = nm[len(TEMPLATE_COPY) :]
                    stale.discard(split_lib_element(nm.lower()))
            return bool(stale)
        except Exception:  # noqa: BLE001 -- not known: the store is read again
            from ...infra import logsetup

            logsetup.note_exception("main_window.stored_behind", page=d.path)
            return False

    @staticmethod
    def _element_tab_states() -> str:
        """The elements open in the editor's tabs, for the chain's worker (its editor file:
        genie_worker._editor_state): 'clean' or 'dirty', the library, the element."""
        rows = []
        for w in editor_windows():
            for e in getattr(w, "_docs", ()):
                d = e["doc"]
                # (an earlier version shown read-only is not the element's tab)
                if element_tab(d):
                    rows.append(
                        "%s\t%s\t%s"
                        % ("clean" if main_window_class()._tab_clean(d) else "dirty", d.path, d.element_name)
                    )
        return "\n".join(rows)

    def _start_users_run(self, lib_path: str, element: str, name: str) -> bool:
        """The chain of _update_closed_pages for ``lib_path``/``element`` (``name`` =
        'lib.element'), started now. Outside a running event loop (scripts, tests) it runs
        at once in this process. False when it could not start (said)."""
        from ..jobs.update_run import UpdatePagesRun

        try:
            from ...backend.graphics_builder import open_pages

            in_builder = [list(x) for x in open_pages()]
        except Exception:  # noqa: BLE001
            _note_exc("users_chain._start_users_run")
            in_builder = []
        run = _ChainRun(self, lib_path, element, name, in_builder)
        if not run.start_helper():
            return False
        UpdatePagesRun.users_busy = name
        # (its answer so far: a tab whose file it stored from the version the tab holds
        # takes it as its own at once, _take_chain_write)
        run_state().chain_progress = run.files["progress_file"]
        # (the open tabs' genie updates that have not landed: they land when they are done,
        # the run's end brings the tabs up to date again; with Settings 'Open tabs wait for
        # the update of what uses a saved genie' they wait for the run's end, composed once
        # (files._hold_tab_updates_for_run))
        if self._tabs_wait_for_runs():
            with contextlib.suppress(Exception):
                self._hold_tab_updates_for_run()
        run.show_note()
        if run.inline:
            run.run_inline()
            return True
        run.timer.timeout.connect(lambda: run.poll())
        run.timer.start()
        return True

    #: the answer keys of a chain that make its note a warning: what it could not do, and
    #: the closed pages it left out of date or saved without their database rows
    _CHAIN_WARN = (
        "failed",
        "elements_failed",
        "stopped",
        "problems",
        "closed_meanwhile",
        "not_composed",
        "db_in_use",
        "open_in_builder",
        "changed_meanwhile",
        "unchanged",
        "rows_pending",
    )
    #: the closed pages a chain left out of date (its answer's keys; Details says why)
    _CHAIN_LEFT = ("db_in_use", "open_in_builder", "changed_meanwhile", "unchanged")

    @staticmethod
    def _chain_level(res: dict) -> str:
        """The level of a chain's note (``res`` = its answer): 'error', 'warning' (what it
        could not do, the pages it left out of date, an element it left that is not in a
        library supplied with Plant SCADA) or 'success'."""
        from ...io.genieusage import SHIPPED_WHY

        if res.get("error"):
            return "error"
        if any(res.get(k) for k in UsersChain._CHAIN_WARN):
            return "warning"
        if any(not str(x).endswith(": " + SHIPPED_WHY) for x in res.get("elements_skipped") or ()):
            return "warning"
        return "success"

    def _users_run_finished(self, res: dict, name: str, kind: str = "genie") -> str:
        """The end of a chain: reload saved tabs, follow libraries and open tabs. Its note's
        text."""
        from .libwatch import chain_saves_settled

        saved = list(res.get("elements_saved") or []) + list(res.get("elements_in_editor") or [])
        # (counted by kind: a template or a symbol is not called a genie)
        kinds_of = res.get("element_kinds") or {}
        # the libraries its helper wrote are the editor's own (the watch held them while it
        # ran, and the end below brings the open tabs up to date once); a file another
        # program changed meanwhile is looked at now
        with contextlib.suppress(Exception):
            chain_saves_settled(res.get("libraries") or (), first=self)
        # (a tab opened while it ran, before its helper knew of it, whose element or page it
        # saved: the tab takes what it saved)
        self._settle_unlisted_tabs(res)
        if res.get("error"):
            return self._chain_failed_text(res, name, saved, kinds_of)
        upd, failed = res.get("updated") or [], res.get("failed") or []
        self._log_chain_answer(res, name, upd, failed)
        self._follow_chain_end(
            res, saved, libraries=bool(saved or upd or failed or res.get("elements_failed"))
        )
        return self._chain_result_text(res, name, kind, saved, kinds_of, upd, failed)

    def _chain_failed_text(self, res: dict, name: str, saved, kinds_of) -> str:
        """The end of a chain that failed or stopped with an error: logged, the open tabs
        follow what it saved before; its note's text."""
        from ...infra import logsetup
        from ..names import plural

        logsetup.get("genies").warning(
            "the update of what uses %s failed: %s\n%s", name, res["error"], res.get("trace", "")
        )
        # (the helper may have saved library genies, and closed pages, before it failed
        # or stopped: the libraries are read anew and the open tabs follow anyway)
        editing_mod.libraries_changed()
        if saved:
            self._after_chain_saves(res)
        else:
            # (a tab it found current, its automatic changes in the store, reads it
            # again)
            self._reload_saved_tabs(res.get("tabs_saved") or [], res)
            if option("auto_update_genies"):
                for w in editor_windows():
                    with contextlib.suppress(Exception), self._updates_for("follow"):
                        w._queue_open_genie_updates()
        # (the open pages took the saved template at its save, _follow_saved_template:
        # not again here, an update the user undid stays undone)
        self._follow_used_by_windows()
        # (the pages it saved before it stopped are named too: their files changed)
        upd = list(res.get("updated") or [])
        parts = self._saved_kinds_parts(saved, kinds_of) + ([plural(len(upd), "closed page")] if upd else [])
        if parts:
            return "Saved %s using %s, then the update failed." % (self._and_list(parts), name)
        return "Could not update what uses %s." % name

    @staticmethod
    def _log_chain_answer(res: dict, name: str, upd, failed) -> None:
        from ...infra import logsetup

        logsetup.get("genies").info(
            "users of %s: genies saved %s (in their tabs %s), left %s, failed %s; closed "
            "pages updated %s; failed: %s; left: %s / %s; links removed: %s; unchanged: %s; "
            "backup %s",
            name,
            res.get("elements_saved"),
            res.get("elements_in_editor"),
            res.get("elements_skipped"),
            res.get("elements_failed"),
            upd,
            failed,
            res.get("skipped_open"),
            res.get("open_in_builder"),
            res.get("links_dropped"),
            res.get("details") or res.get("unchanged"),
            res.get("backup"),
        )

    def _follow_chain_end(self, res: dict, saved, libraries: bool) -> None:
        """After a chain that ended: the libraries read anew (``libraries``: it wrote or
        tried to), the open tabs and the Used By windows follow, the database writes it
        rolled back are told."""
        if libraries:
            editing_mod.libraries_changed()
        if saved:
            self._after_chain_saves(res)
        else:
            self._reload_saved_tabs(res.get("tabs_saved") or [], res)
        # (the open pages linked to the saved template, or to one built on it, took it at
        # its save (_follow_saved_template), and those of the templates the chain saved again
        # take theirs above (_after_chain_saves): the user's template is not applied again
        # here, so an update undone in a tab stays undone)
        self._follow_used_by_windows()
        if res.get("recovered"):
            # (database writes a dead ctgedit process left half done, rolled back)
            with contextlib.suppress(RuntimeError):
                self.report_journal_recovery(list(res["recovered"]))

    def _chain_result_text(self, res: dict, name: str, kind: str, saved, kinds_of, upd, failed) -> str:
        """The note's text of a chain that ended: what it updated (``upd`` = the closed
        pages), what it left (``failed`` = the closed pages it could not update)."""
        from ..names import plural

        # (the closed pages it left out of date, with why in Details)
        out_left = [x for k in self._CHAIN_LEFT for x in res.get(k) or ()]
        left_any = bool(
            out_left
            or res.get("rows_pending")
            or res.get("stopped")
            or res.get("elements_skipped")
            or failed
            or res.get("elements_failed")
            or res.get("not_composed")
            or res.get("closed_meanwhile")
            or res.get("unregistered")
            or res.get("problems")
        )
        if not saved and not upd and not left_any:
            # (it found nothing out of date)
            return "Nothing that uses %s needed an update.%s" % (name, self._chain_tabs_text(res, kind))
        parts = self._saved_kinds_parts(saved, kinds_of) + [plural(len(upd), "closed page")]
        text = "%s %s that %s %s." % (
            "Stopped after updating" if res.get("stopped") else "Updated",
            self._and_list(parts),
            "uses" if not saved and len(upd) == 1 else "use",
            name,
        )
        # ('lib.element: why' each)
        left = [str(x).split(": ", 1)[0] for x in res.get("elements_skipped") or []]
        bad = len(failed) + len(res.get("elements_failed") or [])
        if left:
            text += " %s left (Details says why)." % self._and_list(self._saved_kinds_parts(left, kinds_of))
        if bad:
            text += " %d could not be updated." % bad
        if out_left:
            n = len(out_left)
            text += " %s %s left out of date (Details says why)." % (
                plural(n, "closed page"),
                "was" if n == 1 else "were",
            )
        n = len(res.get("rows_pending") or [])
        if n:
            text += " %s saved, but %s project database rows are not written yet (Details says which)." % (
                plural(n, "page was", "pages were"),
                "its" if n == 1 else "their",
            )
        # (a save that kept a copy it could not compose: its old stamp stays, so Used By
        # still calls the element out of date; only this says why. A template's copy is no
        # nested genie)
        n = sum(len(keys or ()) for _lb, keys in res.get("not_composed") or ())
        if n:
            text += " %s could not be composed again and %s as stored (Details says which)." % (
                plural(n, "nested copy", "nested copies"),
                "stays" if n == 1 else "stay",
            )
        n = len(res.get("closed_meanwhile") or [])
        if n:
            text += " %s closed during the update %s not updated." % (
                plural(n, "page"),
                "was" if n == 1 else "were",
            )
        n = len(res.get("unregistered") or [])
        if n:
            text += " %s not registered in %s project %s left alone." % (
                plural(n, "page"),
                "its" if n == 1 else "their",
                "was" if n == 1 else "were",
            )
        n = len(res.get("problems") or [])
        if n:
            text += " %s could not be read, so what %s holds was not checked." % (
                plural(n, "file"),
                "it" if n == 1 else "each",
            )
        return text + self._chain_tabs_text(res, kind)

    def _chain_tabs_text(self, res: dict, kind: str = "genie") -> str:
        """The chain's sentence about the pages it left to their tabs."""
        now = {path_key(p) for p in self._open_page_paths()}
        tabs = {
            path_key(p)
            for p in list(res.get("open_at_start") or []) + list(res.get("skipped_open_paths") or [])
            if p
        }
        return self._open_tabs_sentence(len(tabs & now), kind)

    @staticmethod
    def _open_tabs_sentence(n, kind: str = "genie") -> str:
        """' n pages open in tabs take the change there.'"""
        from ..names import plural

        key = "opt/auto_update_template" if kind == "template" else "opt/auto_update_genies"
        if n is None:
            if app_settings().value(key, True, type=bool):
                return " Pages and %s open in tabs that use it take the change there." % (
                    "templates" if kind == "template" else "genies"
                )
            if kind == "template":
                return (
                    " Pages open in tabs that use it are not updated there (Apply a "
                    "changed template again is off)."
                )
            return (
                " Pages open in tabs that use it take it there only where they place "
                "it directly (Update changed genies is off)."
            )
        if not n:
            return ""
        where = "%s open in %s" % (plural(n, "page"), "a tab" if n == 1 else "tabs")
        if app_settings().value(key, True, type=bool):
            return " %s %s the change there." % (where, "takes" if n == 1 else "take")
        if kind == "template":
            return " %s %s not updated there (Apply a changed template again is off)." % (
                where,
                "is" if n == 1 else "are",
            )
        return " %s %s it there only where %s it directly (Update changed genies is off)." % (
            where,
            "takes" if n == 1 else "take",
            "it places" if n == 1 else "they place",
        )

    def _follow_saved_template(self, kind: str, name: str) -> int:
        """A template was saved: update the open pages that use it."""
        if kind != "template":
            return 0
        n = 0
        for w in editor_windows():
            with contextlib.suppress(RuntimeError):
                n += w._chain_templates_in_tabs([name], fresh=True)
        return n

    @staticmethod
    def _template_built_on(d, ref, saved) -> bool:
        """The template of page ``d`` (``ref``) holds a copy of one of the templates ``saved``
        ('lib.template' labels, lower case) that its load composes again (it changed since the
        outer template was saved: template_place.load_template, ``entry_overrides``), so the
        page is out of date through it."""
        from ...format import template_place as TP

        tpl = TP.resolve_template(d._resolver(), ref)
        for base in (getattr(tpl, "entry_overrides", None) or {}) if tpl is not None else ():
            lib, elem, _x = TP.parse_ref_text(base)
            if ("%s.%s" % (lib, elem)).lower() in saved:
                return True
        return False

    @staticmethod
    def _fold_into_step(d, first, second) -> None:
        """Fold an automatic step into the template step it finished."""
        undo = getattr(d, "_undo", None) or []
        if first is None or len(undo) < 2 or undo[-1] is not second or undo[-2] is not first:
            return
        if not d.fold_last_step():
            return
        items = ui_state(d).auto_changes or []
        d.ui.auto_changes = [(what, c) for what, c in items if c is not second]

    @staticmethod
    def _template_span_key(d) -> bytes:
        """A digest of the records of page ``d``'s template placements (what a template
        step changes): the record of the template step a template save made in its tab."""
        import hashlib
        from ...format import template_place as TP
        from ...format import writer

        objs = d.parsed.page.objects
        h = hashlib.sha1()
        for a, b, _ref in TP.template_roots(objs):
            h.update(b"%d:%d;" % (a, b))
            for po in objs[a:b]:
                h.update(writer.object_payload(po))
        return h.digest()

    def _closed_meanwhile(self, seen: dict, after_saves=(), names_from=()) -> List[str]:
        """Pages that were open when the run started, closed since, and unchanged."""
        now = {path_key(p) for p in self._open_page_paths()}
        follow = {path_key(p) for p in after_saves or () if p}
        out = set()
        for p, t in (seen or {}).items():
            if not p or path_key(p) in now:
                continue
            if t is not None and page_mtimes([p]).get(p) != t and path_key(p) not in follow:
                continue  # (saved before it was closed, and current)
            out.add(os.path.abspath(p))
        # (two of one name in two projects told apart by their project, as the run's other
        # lines name them)
        label = page_label(list(out) + [p for p in names_from or () if p])
        return sorted({label(p) for p in out}, key=str.lower)

    def _after_chain_saves(self, res: dict) -> None:
        """The chain's worker saved library genies: update tabs."""
        self._reload_saved_tabs(res.get("tabs_saved") or [], res)
        # (the other tabs of every window re-render when shown, the Library panels re-list)
        libs = [p for p in res.get("libraries") or () if p]
        if libs:
            with contextlib.suppress(Exception):
                self._after_library_write(*libs)
        self._library_cache_notice(res.get("save_warning") or "")
        # (first, as a page's open meets its template first: the handles it frees are free
        # for the genies composed after it)
        saved = list(res.get("elements_saved") or []) + list(res.get("elements_in_editor") or [])
        for w in editor_windows():
            with contextlib.suppress(RuntimeError):
                w._chain_templates_in_tabs(saved)
        # (then the placed symbols, as a page's open meets them before its genies; every
        # window's tabs: Settings > Pages and libraries, Update changed symbols)
        with contextlib.suppress(Exception):
            self._follow_symbols_in_tabs()
        if option("auto_update_genies"):
            for w in editor_windows():
                # (a tab that took the save: its update finishes that step, files.
                # _fold_into_save_step)
                with contextlib.suppress(Exception), self._updates_for("follow"):
                    w._queue_open_genie_updates()

    #: the mark of a template step a template save made wait for the user
    #: (DocumentUiState.template_steps: :meth:`_chain_templates_in_tabs`)
    _TEMPLATE_STEP_WAITS = "waiting"

    def _chain_templates_in_tabs(self, saved, docs=None, gate: bool = True, fresh: bool = False) -> int:
        """Update open pages whose template or super genie the chain saved."""
        from ...format import template_place as TP
        from ...infra import logsetup
        from ..names import plural, template_name
        from ..notify import notify

        saved = {str(x).lower() for x in saved or ()}
        if not saved:
            return 0
        tpl_on = option("auto_update_template")
        changed, from_super = 0, 0
        for e in list(getattr(self, "_docs", ())):
            d = e["doc"]
            if docs is not None and not any(d is x for x in docs):
                continue
            if not page_tab(d):
                continue
            n = 0
            try:
                roots = TP.template_roots(d.parsed.page.objects)
                tpl = ("%s.%s" % (roots[0][2].library, roots[0][2].element)).lower() if roots else ""
                link = d.supergenie_link()
                sg = ("%s.%s" % tuple(link)).lower() if link else ""
                marks = ui_state(d).template_steps
                hit = bool(
                    tpl_on
                    and tpl
                    and (tpl in saved or (fresh and self._template_built_on(d, roots[0][2], saved)))
                )
                waits = self._TEMPLATE_STEP_WAITS
                mark = marks.get(tpl) if hit and not fresh else None
                # (the step the save made: still on the page (True), undone (False))
                kept = None
                if mark is not None and mark != waits:
                    kept = self._template_span_key(d) == mark[0]
                    hit = hit and kept
                mine = {x for x in (tpl if hit else "", sg if sg in saved else "") if x}
                if gate and mine and not self._may_change_now(d):
                    # (the step would land in the middle of a drag: it waits, and the page's
                    # symbols and genies wait after it)
                    if fresh and hit:
                        marks[tpl] = waits
                    self._wait_until_idle(d, templates=mine)
                    continue
                if kept is not None:
                    del marks[tpl]  # (looked at once: the save's chain)
                if hit:
                    state, link = d.template_status()
                    if state == "stale":
                        before = self._template_span_key(d)
                        k = (
                            self._auto_step(
                                d, "template %s re-applied" % template_name(link), d.update_template
                            )
                            or 0
                        )
                        after = self._template_span_key(d) if k > 0 else before
                        top = d._undo[-1] if k > 0 and d._undo else None
                        if fresh or mark == waits:
                            marks[tpl] = (after, top)
                        if not (kept and after == before):
                            # (a step that changed only the link table after the save's step:
                            # the page showed the change already, said then)
                            self._template_message(d, link, k > 0)
                        elif top is not None:
                            self._fold_into_step(d, mark[1], top)
                        n += k > 0
                    elif mark == waits:
                        marks.pop(tpl, None)
                if sg in saved and d.supergenie_status()[0] == "stale":
                    k = (
                        self._auto_step(d, "page made again from its super genie", d.update_supergenie_page)
                        or 0
                    )
                    from_super += k > 0
                    n += k > 0
            except Exception:  # noqa: BLE001 -- the page is updated at its next open
                logsetup.note_exception("main_window.chain_templates_in_tabs", page=d.path)
                continue
            if n:
                changed += 1
                with contextlib.suppress(RuntimeError):
                    self._mark_modeless_reason([d], "replace")
                    if d is self.doc:
                        self._refresh()
        if from_super:
            notify(
                self,
                "Updated %s from %s super genie. Save %s to keep the change."
                % (
                    plural(from_super, "open page"),
                    "its" if from_super == 1 else "their",
                    "it" if from_super == 1 else "them",
                ),
                "success",
            )
        if changed:
            self._update_title()
        return changed

    def _reload_saved_tabs(self, refs, res=None) -> None:
        """Reload element tabs the chain saved."""
        self._settle_saved_tabs(refs, reload=True, res=res)

    def _settle_unlisted_tabs(self, res: dict) -> int:
        """Settle tabs opened while the chain ran."""
        labels = {
            str(x).lower()
            for x in list(res.get("elements_saved") or []) + list(res.get("elements_in_editor") or [])
        }
        libs = {path_key(str(p)) for p in res.get("libraries") or () if p}
        pages = {path_key(str(p)) for p in res.get("pages") or () if p}
        versions = self._chain_versions(res)
        views = self._reload_read_only_views(pages)
        listed = set()  # (the tabs it knew: _reload_saved_tabs)
        for r in res.get("tabs_saved") or ():
            with contextlib.suppress(TypeError, ValueError):
                p, el = r[:2]
                listed.add((path_key(str(p)), str(el).lower()))
        refs, reload_pages, own_pages = [], [], []
        for w in editor_windows():
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                d = e["doc"]
                if isinstance(d, PendingPage) or getattr(d, "read_only", False):
                    continue
                try:
                    where = path_key(d.path or "")
                    if isinstance(d, ElementDocument):
                        if (
                            d.is_new
                            or where not in libs
                            or ("%s.%s" % (d.library_name, d.element_name)).lower() not in labels
                            or (where, (d.element_name or "").lower()) in listed
                        ):
                            continue
                        if d.changed_on_disk() and (self._tab_clean(d) or self._chain_wrote(d, versions)):
                            refs.append([d.path, d.element_name])
                    elif where in pages and d.changed_on_disk():
                        # (a window of the tab holding typed values keeps the tab, as a change
                        # of the user's does: a reload would close it with what was typed)
                        if (
                            not getattr(d, "has_unstepped_changes", False)
                            and (not d.dirty or self._only_automatic_since_saved(d))
                            and not w._pending_windows(d)
                        ):
                            reload_pages.append((w, d))
                        elif self._chain_wrote(d, versions):
                            own_pages.append((w, d))
                except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
                    from ...infra import logsetup

                    logsetup.note_exception("main_window.settle_unlisted_tabs", page=getattr(d, "path", ""))
        if not refs and not reload_pages and not own_pages:
            return views
        editing_mod.libraries_changed()  # (what the worker wrote is read anew)
        self._settle_saved_tabs(refs, reload=True, res=res)
        for w, d in reload_pages:
            try:
                if not any(x["doc"] is d for x in w._docs):
                    continue
                if not w._may_change_now(d):
                    # (a change the user makes meanwhile keeps the tab: what the worker
                    # wrote from the version it read is its own then, files._reload_that_waited)
                    w._chain_writes_waiting()[id(d)] = (weakref.ref(d), versions)
                    w._wait_until_idle(d, reload="page")
                    continue
                w.revert_document(d, ask=False)
            except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
                from ...infra import logsetup

                logsetup.note_exception(
                    "main_window.settle_unlisted_tabs/reload", page=getattr(d, "path", "")
                )
        for w, d in own_pages:
            with contextlib.suppress(RuntimeError):
                w._take_chain_write(d, versions)
        return len(refs) + len(reload_pages) + len(own_pages) + views

    def _chain_writes_waiting(self) -> dict:
        """Tabs whose reload of the chain's writes waits for the user."""
        waits = self.__dict__.setdefault("_chain_waits", {})
        with contextlib.suppress(RuntimeError):
            open_now = {id(e["doc"]): e["doc"] for e in getattr(self, "_docs", ())}
            for k in [k for k, (ref, _v) in waits.items() if ref() is None or open_now.get(k) is not ref()]:
                waits.pop(k, None)
        return waits

    def _reload_read_only_views(self, paths) -> int:
        """Reload read-only views of pages a run updated."""
        from ..dialog.history_versions import is_version

        want = {path_key(str(p)) for p in paths or () if p}
        if not want:
            return 0
        n = 0
        for w in editor_windows():
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                d = e["doc"]
                if (
                    isinstance(d, (ElementDocument, PendingPage))
                    or is_version(d)
                    or not getattr(d, "read_only", False)
                ):
                    continue
                try:
                    if path_key(d.path or "") not in want or d.dirty or not d.changed_on_disk():
                        continue
                    n += bool(w.revert_document(d, ask=False))
                except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
                    from ...infra import logsetup

                    logsetup.note_exception("main_window.reload_read_only_views", page=getattr(d, "path", ""))
        return n

    def _settle_saved_tabs(self, refs, reload: bool, gate: bool = True, res=None) -> None:
        """Element tabs saved from their clean state take the stored element."""
        want = set()
        for r in refs or ():
            with contextlib.suppress(TypeError, ValueError):
                p, el = r[:2]
                want.add((path_key(str(p)), str(el).lower()))
        if not want:
            return
        from ..editor import notices
        from .files import NESTED_DONE

        versions = self._chain_versions(res) if res is not None else None
        look = []
        for w in editor_windows():
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                # (never an earlier version shown read-only)
                key = (path_key(d.path or ""), (d.element_name or "").lower()) if element_tab(d) else None
                if key is None or getattr(d, "read_only", False) or key not in want:
                    continue
                try:
                    clean = self._tab_clean(d)
                    if not reload:
                        if not clean or d.dirty:
                            continue  # (its save did not take)
                    elif not clean:
                        # (a change of the user's: kept. The block the helper stored from
                        # the block the tab holds is the tab's own; one the tab took already
                        # (its reload waited, or the run's answer so far named it) is looked
                        # at again on the tab's records, its memo saying what is done)
                        if not w._take_chain_write(d, versions or {}) and not d.changed_on_disk():
                            w._bring_up_to_date(d, first=d is w.doc)
                        continue
                    elif gate and not w._may_change_now(d):
                        w._take_chain_write(d, versions or {}, look_again=False)
                        w._wait_until_idle(d, reload="element")
                        continue
                    jobs = getattr(w, "_genie_jobs", None)
                    opened = d.stored_session() if reload else None
                    if reload and d.dirty and self._stored_behind(d, opened, res):
                        # (a nested genie changed after the helper composed it, and the
                        # tab's own updates compose it as it is now: the tab keeps them, the
                        # stored block is its baseline, and what is still out of date is
                        # composed on its records; its update still running does that)
                        d.adopt_disk_version(opened)
                        busy = jobs is not None and jobs.busy(d)
                        w._chain_write_taken(d, look_again=not busy)
                        if busy:
                            ui_state(d).genie_sources_done[NESTED_DONE] = d.nested_genie_sources()
                        continue
                    if jobs is not None:
                        jobs.cancel_automatic(d)
                    if reload:
                        d.reload_stored(opened)
                    d.ui.auto_changes = []
                    with contextlib.suppress(Exception):
                        notices.remove(d, "disk")
                        ui_state(d).disk_asked = None
                    with contextlib.suppress(RuntimeError):
                        w._genies_updated(d)  # (its amber marks go)
                        if d is w.doc:
                            w._auto_bar.show_for(d)
                    # (its nested genies were composed by that save, from the files the
                    # helper reported; a genie changed since, its import table says, is
                    # composed now: also one saved again while a run in this process saved
                    # the tab)
                    done = ui_state(d).genie_sources_done
                    sources = d.nested_genie_sources()
                    v = (versions or {}).get("elements", {}).get(key)
                    composed = v[2] if v else None
                    if composed == sources or not d.nested_genie_changes():
                        done[NESTED_DONE] = sources
                    else:
                        done.pop(NESTED_DONE, None)
                        look.append((w, d))
                except Exception:  # noqa: BLE001 -- the tab keeps what it showed
                    from ...infra import logsetup

                    logsetup.note_exception("main_window.reload_saved_tabs", page=d.path)
                    continue
                with contextlib.suppress(RuntimeError):
                    w._mark_modeless_reason([d], "replace")
                    if d is w.doc:
                        w._refresh()
                    w._update_title()
        if look and option("auto_update_genies"):
            for w, d in look:
                with contextlib.suppress(Exception):
                    w._update_stale_genies(d, first=d is w.doc)

    @staticmethod
    def _users_run_details(res: dict) -> str:
        """The Details box of a chain of _update_closed_pages. A library element that is
        not a genie is named with its kind (the answer's 'element_kinds')."""
        kinds = res.get("element_kinds") or {}

        def named(key, x):
            label, sep, why = str(x).partition(": ")  # ('lib.element: why')
            kind = kinds.get(label) if key.startswith("elements_") else None
            if not kind or kind == "genie":
                return str(x)
            return "%s (%s)%s%s" % (label, kind, sep, why)

        lines = []
        for key, title in (
            ("elements_saved", "Library elements saved again"),
            ("elements_in_editor", "Library elements saved again from their tabs"),
            ("elements_current", "Library elements that already matched"),
            ("elements_skipped", "Library elements left"),
            ("elements_failed", "Library elements that could not be saved"),
            ("super_pages", "Super genie pages created"),
            ("updated", "Closed pages updated"),
            ("links_dropped", "Saved without the links to library elements no longer on the page"),
            ("unchanged", "Out of date, but nothing on them could be updated (the log says why)"),
            ("failed", "Could not be updated"),
            ("skipped_open", "Left (opened in an editor meanwhile)"),
            ("open_in_builder", "Left (open in the Graphics Builder)"),
            ("changed_meanwhile", "Left (changed meanwhile)"),
            ("db_in_use", "Left (the project database is in use)"),
            ("rows_pending", "Saved, their project database rows not written yet"),
            (
                "closed_meanwhile",
                "Not updated (open in an editor during the "
                "update and closed before it ended; run "
                "Update Users again)",
            ),
            ("unregistered", "Left (not registered in their project, as Update Pages leaves them)"),
            ("problems", "Not checked (could not be read)"),
        ):
            if res.get(key):
                lines.append("%s:\n  %s" % (title, "\n  ".join(named(key, x) for x in res[key])))
            if key == "elements_failed" and res.get("not_composed"):
                # (saved, with copies it could not compose: they keep their old stamp)
                lines.append(
                    "Nested copies that could not be composed again and stay as "
                    "stored:\n  %s"
                    % "\n  ".join(main_window_class()._not_composed_names(res["not_composed"]))
                )
        if res.get("templates"):
            lines.append("Page templates applied again: %d." % int(res["templates"]))
        if res.get("supergenies"):
            lines.append("Pages made again from their super genie: %d." % int(res["supergenies"]))
        if res.get("placeholders"):
            lines.append("Placeholder genies placed again from their library: %d." % int(res["placeholders"]))
        if res.get("stopped"):
            lines.append("Stopped before the end.")
        if res.get("backup"):
            lines.append("Backup: %s (Tools > Restore Backup puts the files back)." % res["backup"])
        return "\n\n".join(lines) or "Nothing to update."


def with_figures(text: str, figures: str) -> str:
    """The chain's status bar note with its figures: 'Updating ...: 3 of 40 (Page). 12 s so
    far, about 2 min left.' (``text`` alone without figures)."""
    if not figures:
        return text
    return "%s. %s%s." % (text, figures[:1].upper(), figures[1:])


class _ChainRun:
    """One run of the chain after a library save (UsersChain._start_users_run): its helper's
    files, its note on the status bar with Stop, its background task, the poll of its
    progress and its end. Its Qt connections are lambdas: a signal holds a bound method of
    a plain object weakly, and the connections are what keep the run alive."""

    def __init__(self, win, lib_path: str, element: str, name: str, in_builder: list):
        from ..jobs.helper_job import RecordTail
        from ..jobs.run_stats import RunStats

        self.win = win  # (the window that started it)
        self.lib_path, self.element, self.name = lib_path, element, name
        self.in_builder = in_builder
        self.inline = QtCore.QThread.currentThread().loopLevel() == 0
        self.proc = None
        self.files: dict = {}
        self.request: dict = {}
        self.req = self.ans = ""
        self.result = None  # (its answer, once it has ended)
        self.took = ""
        # (each page open during the run, with its file's time when first seen open: else
        # one closed before the run ended unsaved is updated by nobody)
        self.seen: dict = {}
        # (the progress figures: the genies phase, then the pages with their records)
        self.stats, self.tail = RunStats.from_settings(), RecordTail()

    def start_helper(self) -> bool:
        """Write the helper's request and its lists, and start it (inline: it runs later, in
        this process). False when that failed (said)."""
        import tempfile
        from ...workers import genie_worker as GW
        from ...infra import workers
        from ...infra.paths import data_dir
        from ...io import updatepages

        win = self.win
        folder = data_dir("genieupdate")
        try:
            os.makedirs(folder, exist_ok=True)
            fd, req = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".json", dir=folder)
            os.close(fd)
            self.req = req
            base = req[:-5]
            self.files = files = {
                "progress_file": base + ".progress",
                "skip_file": base + ".skip",
                "cancel_file": base + ".cancel",
                "editor_file": base + ".editors",
            }
            write_text(files["skip_file"], "\n".join(win._open_page_paths()))
            write_text(files["editor_file"], win._element_tab_states())
            self.seen = page_mtimes(win._open_page_paths())
            self.request = {
                "format": GW.FORMAT,
                "library": self.lib_path,
                "element": self.element,
                "skip": win._open_page_paths(),
                "builder_open": self.in_builder,
                "backup": updatepages.backup_dir(),
                "elements": True,
                **files,
            }
            with open(req, "w", encoding="utf-8") as fh:
                json.dump(self.request, fh)
            self.ans = base + ".answer.json"
            if not self.inline:
                self.proc = workers.track("pages", workers.start(GW.users_command(req, self.ans)))
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("main_window._update_closed_pages")
            from ..notify import notify, open_log_action

            notify(
                win,
                "Could not start the update of what uses %s." % self.name,
                "error",
                actions=(open_log_action(),),
            )
            return False
        return True

    def show_note(self) -> None:
        """The run's note on the status bar (its text, Stop), its poll timer and its task."""
        from ..jobs import tasks
        from ..prefs.job_options import poll_ms

        win = self.win
        self.job = job = QtWidgets.QWidget()
        self.lay = QtWidgets.QHBoxLayout(job)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel("Finding what uses %s…" % self.name)
        self.lay.addWidget(self.label)
        self.btn = QtWidgets.QToolButton()
        self.btn.setText("Stop")
        self.btn.setToolTip(
            "Stop after the genie or page being updated now. The pages done so far stay updated"
        )
        self.lay.addWidget(self.btn)
        win.statusBar().addPermanentWidget(job)
        win._users_job = job  # (tests read its label)
        self.timer = QtCore.QTimer(win)
        self.timer.setInterval(poll_ms())
        # (a background task too: the tasks button lists it with Cancel, and its end stays
        # in the window's messages with Details: a user who was away finds it there)
        self.task = tasks.begin(
            "users", "Finding what uses %s…" % self.name, cancel=lambda: self.cancel(), window=win
        )
        self.btn.clicked.connect(lambda: self.cancel())

    def remove_note(self) -> None:
        # (the status bar it sits in now: the run moves it when its window closes)
        with contextlib.suppress(RuntimeError):
            bar = self.job.parentWidget()
            while bar is not None and not isinstance(bar, QtWidgets.QStatusBar):
                bar = bar.parentWidget()
            if bar is not None:
                bar.removeWidget(self.job)
        with contextlib.suppress(RuntimeError):
            self.job.deleteLater()

    def cancel(self) -> None:
        """Stop (the helper stops after the genie or page it updates now); after the end,
        Close: the note goes."""
        from ..jobs import tasks

        if self.result is None:
            with contextlib.suppress(OSError):
                open(self.files["cancel_file"], "w").close()
            self.btn.setEnabled(False)
            self.label.setText("Stopping after the genie or page being updated…")
            tasks.progress(self.task, 0, 0, "Stopping the update of what uses %s…" % self.name)
            return
        self.remove_note()

    def owner(self):
        """The window that started the run, or another editor window when it was closed
        meanwhile: the result is told where it can be seen."""
        wins = editor_windows()
        return self.win if self.win in wins or not wins else wins[0]

    def details(self) -> None:
        inform(
            self.owner(),
            "Users Updated",
            "\n\n".join([self.win._users_run_details(self.result or {})] + [x for x in (self.took,) if x]),
        )

    def lists(self) -> None:
        """The helper's lists anew: pages opened since (the worker leaves them), element tabs
        edited since (left). Each list is replaced whole: the worker never reads one half
        written."""
        opened = self.win._open_page_paths()
        for p, t in page_mtimes(p for p in opened if p not in self.seen).items():
            self.seen[p] = t
        with contextlib.suppress(OSError):
            write_text(self.files["skip_file"], "\n".join(opened))
        with contextlib.suppress(OSError, RuntimeError):
            write_text(self.files["editor_file"], self.win._element_tab_states())

    def poll(self) -> None:
        """The timer's look at the helper: its progress while it runs, else its end."""
        self.lists()
        if self.proc is not None and self.proc.poll() is None:
            with contextlib.suppress(OSError, ValueError):
                self.show_progress()
            return
        self.timer.stop()
        self.timer.deleteLater()
        self.finish()

    def show_progress(self) -> None:
        """The helper's progress file on the note and on the task."""
        from ...workers import genie_worker as GW
        from ..jobs import tasks
        from ..progress_text import hold_width

        stats, label = self.stats, self.label
        with open(self.files["progress_file"], encoding="utf-8") as fh:
            pr = json.load(fh)
        phase = "genies" if pr.get("phase") == "genies" else "pages"
        stats.begin_phase(phase, int(pr["n"]))
        if phase == "pages":
            stats.add_records(self.tail.take([self.files["progress_file"] + GW.RECORDS_SUFFIX]))
        stats.update(int(pr["i"]))
        if self.btn.isEnabled():
            what = (
                "Saving the library genies that use %s"
                if phase == "genies"
                else "Updating the closed pages that use %s"
            )
            text = "%s: %d of %d (%s)" % (what % self.name, pr["i"] + 1, pr["n"], pr["name"])
            tasks.progress(
                self.task, int(pr["i"]) + 1, int(pr["n"]), "%s: %s…" % (what % self.name, stats.step_text())
            )
            label.setText(with_figures(text, stats.time_text()))
            label.setToolTip("\n".join(stats.lines()))
            # (the figures change their length at every look: the note keeps room for the
            # widest, so the fields beside it stay put)
            hold_width(
                label,
                [with_figures(text, t) for t in stats.widest_times()] if stats.show else (),
            )

    def run_inline(self) -> None:
        """Outside a running event loop: the helper's work in this process, then the end."""
        from ...format import projectlock
        from ...workers import genie_worker as GW

        files = self.files
        try:
            res = GW.update_closed_pages(
                self.lib_path,
                self.element,
                self.request["skip"],
                self.in_builder,
                self.request["backup"],
                files["progress_file"],
                files["skip_file"],
                files["cancel_file"],
                files["editor_file"],
                elements=True,
            )
        except Exception as e:  # noqa: BLE001 -- said as the worker's error is
            from ...infra import logsetup

            logsetup.note_exception("main_window._update_closed_pages/inline")
            res = {"format": GW.FORMAT, "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            projectlock.set_cancel(None)
        with open(self.ans, "w", encoding="utf-8") as fh:
            json.dump(res, fh)
        self.timer.deleteLater()
        self.finish()

    def finish(self) -> None:
        # (CtDraw's cache notice of the genies it saved: once, when it has all ended)
        with self.win._cache_notice_held():
            self.finish_run()

    def finish_run(self) -> None:
        """The run's end: its answer read, its files removed, the window told."""
        from ..jobs.update_run import UpdatePagesRun

        self.took = self.stats.total_text()
        res = self.read_answer()
        self.remove_files()
        self.result = res
        UpdatePagesRun.users_busy = ""
        run_state().chain_progress = ""
        failed = bool(res.get("error"))
        if not res.get("error"):
            # (the pages it left because they were open, closed since and not saved)
            left = list(res.get("open_at_start") or []) + list(res.get("skipped_open_paths") or [])
            seen = {path_key(k): t for k, t in self.seen.items()}
            res["closed_meanwhile"] = self.win._closed_meanwhile(
                {p: seen.get(path_key(p)) for p in left},
                res.get("after_saves") or (),
                names_from=list(res.get("pages") or ()) + left,
            )
        win = self.owner()
        if win is not self.win:
            # (its note goes with the result to a window that is open)
            with contextlib.suppress(RuntimeError):
                win.statusBar().addPermanentWidget(self.job)
                win._users_job = self.job
        try:
            text = win._users_run_finished(
                res,
                self.name,
                {".ctt": "template", ".ctl": "symbol"}.get(
                    os.path.splitext(self.lib_path)[1].lower(), "genie"
                ),
            )
        except Exception:  # noqa: BLE001 -- the run is over either way
            from ...infra import logsetup

            logsetup.note_exception("main_window._update_closed_pages/finish")
            text = "Updated what uses %s. Its result could not be read." % self.name
            failed = True
        self.show_end(win, res, text, failed)

    def read_answer(self) -> dict:
        """The helper's answer; with an error, what it saved before it stopped."""
        from ...workers import genie_worker as GW

        try:
            with open(self.ans, encoding="utf-8") as fh:
                res = json.load(fh)
        except OSError, ValueError:
            res = {"error": "the update gave no answer"}
        if res.get("error"):
            # (no answer, or only an error: what the helper saved before it stopped, from
            # its partial answer (written after each element) and the records of its pages
            # (a line each as it is done); read before they are removed)
            try:
                res = GW.chain_partial_answer(res, self.files["progress_file"], self.request.get("backup"))
            except Exception:  # noqa: BLE001 -- the error is said either way
                from ...infra import logsetup

                logsetup.note_exception("main_window._update_closed_pages/partial")
        return res

    def remove_files(self) -> None:
        from ...workers import genie_worker as GW

        files = self.files
        leftovers = [
            self.req,
            self.ans,
            files["progress_file"] + GW.GENIES_SUFFIX,
            files["progress_file"] + GW.RECORDS_SUFFIX,
        ] + [p + x for p in files.values() for x in ("", ".tmp")]
        for x in leftovers:
            with contextlib.suppress(OSError):
                os.remove(x)

    def show_end(self, win, res: dict, text: str, failed: bool) -> None:
        """The end on the note (Details, the log, Close), on the task and in ``win``'s
        messages; the run that waited for this one goes next."""
        from ..jobs import tasks
        from ..jobs.update_run import UpdatePagesRun

        label, lay, btn = self.label, self.lay, self.btn
        backup = str(res.get("backup") or "")
        backup_text = ("Backup: %s (Tools > Restore Backup puts the files back)." % backup) if backup else ""
        label.setMinimumWidth(0)  # (the run's room: the result fits itself)
        label.setText(text)
        label.setToolTip("\n".join(x for x in (self.took, backup_text) if x))
        has_details = bool(
            not res.get("error")
            or res.get("backup")
            or res.get("elements_saved")
            or res.get("elements_in_editor")
            or res.get("updated")
        )
        if has_details:
            more = QtWidgets.QToolButton()
            more.setText("Details")
            more.clicked.connect(lambda: self.details())
            lay.insertWidget(1, more)
        if failed:
            # (the note names no log it cannot open: the button opens it)
            from ..messages import open_log_button

            log_btn = open_log_button(self.job)
            log_btn.setVisible(True)
            lay.insertWidget(1, log_btn)
        # (the end stays in the window's messages, Details and the backup with it)
        from ..notify import Action, notify

        tasks.end(self.task, ok=not res.get("error"), tell=False)
        level = self.win._chain_level(res)
        from ..notify import open_log_action

        acts = [Action("Details", lambda: self.details())] if has_details else []
        if failed:
            acts.append(open_log_action())  # (the log it points at)
        notify(win, text, level, actions=acts, key="users:%s" % self.name, detail=backup_text)
        btn.setText("")
        from ..style.theme import icon as _icon

        btn.setIcon(_icon("close"))
        btn.setToolTip("Close this note")
        btn.setEnabled(True)
        # (the note stays Settings > Long jobs 'Keep the note of a finished genie update
        # for', times Appearance > Messages stay; 0: until its Close)
        from ..prefs.job_options import users_note_s
        from ..notify import scaled_ms

        stay = users_note_s()
        if stay > 0:
            QtCore.QTimer.singleShot(scaled_ms(stay * 1000), self.job, lambda: self.remove_note())
        # (a save that waited for this run goes next)
        if UpdatePagesRun.users_queue:
            QtCore.QTimer.singleShot(0, win, win._next_users_run)
