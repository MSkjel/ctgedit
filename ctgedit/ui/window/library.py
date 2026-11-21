"""Library, colour, bitmap, composite and super genie commands."""

from __future__ import annotations

from ..main_window_base import (  # noqa: F401
    ElementDocument,
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    editing_mod,
    json,
    os,
    paste_linked,
    set_paste_linked,
    ui_state,
)
from ..main_window_base import (  # noqa: F401
    _PendingPage,
)
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


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
                QtWidgets.QMessageBox.information(self, "Cut Link", str(e))
                return
            if new is None:
                self.statusBar().showMessage(
                    "Cut Link works on a placed library symbol (or, with nothing selected, "
                    "on the page's template)",
                    6000,
                )
                return
            self._sel = [new]
            self._refresh()
            self.statusBar().showMessage(
                "The symbol is now a group of its own objects: it no longer follows its "
                "library (Undo restores the link)",
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
            QtWidgets.QMessageBox.information(
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
        for w in type(self)._instances or [self]:
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
                "The hot spot belongs to a symbol or genie: open one for editing first", 5000
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
                "is its page's top-left corner",
                6000,
            )
            return
        try:
            moved = doc.set_hotspot(int(x), int(y))
        except ValueError as e:
            self._refresh(rebuild_tree=False)
            QtWidgets.QMessageBox.warning(self, "Hot Spot Location", str(e))
            return
        self._refresh(rebuild_tree=False)
        self._update_title()
        if moved:
            self.statusBar().showMessage(
                "Hot spot at %d, %d. Save the element, then run Update Pages to move the "
                "copies on pages." % (int(x), int(y)),
                7000,
            )

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
            self.statusBar().showMessage("%s.%s is not on this project's include path" % (t[1], t[2]), 5000)
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
        key = ("usedby", os.path.normcase(os.path.abspath(lib_path)), element.lower())
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
                note = lifecycle._SUPER_NOTE
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

    #: a look at the open Used By windows is due (one for many calls:
    #: :meth:`_follow_used_by_windows`)
    _USED_BY_DUE = [False]
    #: reads of what uses an element going on (:meth:`_element_users_read`): the look at the
    #: open Used By windows waits for them (else it would read its users again inside the
    #: save's own read, whose progress lets timers run)
    _USERS_READS = [0]

    def _follow_used_by_windows(self) -> None:
        """Every open Used By window reads its element's users again."""
        dlgs = _used_by_windows()
        if not dlgs:
            return
        for d in dlgs:
            d.stale = True
        if LibraryCommands._USED_BY_DUE[0]:
            return
        LibraryCommands._USED_BY_DUE[0] = True
        QtCore.QTimer.singleShot(0, _refresh_stale_used_by)

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

        LibraryCommands._USERS_READS[0] += 1
        try:
            usage = GU.element_users(lib_path, element, progress=tick)
        except Exception:  # noqa: BLE001 -- a hint, never a blocker
            from ...infra import logsetup

            logsetup.note_exception("ui.main_window.element_users")
            return None, "failed"
        finally:
            LibraryCommands._USERS_READS[0] = max(0, LibraryCommands._USERS_READS[0] - 1)
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
        an earlier version of it shown read-only (:func:`_element_tab`)."""
        want = os.path.normcase(os.path.abspath(ref.path))
        for w in _mw().MainWindow._instances:
            for e in w._docs:
                d = e["doc"]
                if (
                    _element_tab(d)
                    and os.path.normcase(os.path.abspath(d.path or "")) == want
                    and (d.element_name or "").lower() == ref.element.lower()
                ):
                    return d
        return None

    def _update_element_users(self, usage, confirm: bool = True):
        """Update everything that uses an element, in order."""
        hold = _TabsHold("Update Users")
        with self._cache_notice_held():
            try:
                return self._update_element_users_run(usage, confirm, hold=hold)
            finally:
                hold.release(self)  # (also after an error)

    def _update_element_users_run(self, usage, confirm: bool = True, hold=None):
        """The run of :meth:`_update_element_users`. ``hold`` = its hold on the other runs
        until its open tabs have followed (:class:`_TabsHold`; one of its own when None)."""
        from ...io import genieusage as GU
        from ...io import updatepages
        from ..progress_text import SteadyProgressLabel
        from ..jobs.run_stats import RunStats
        from ..jobs.update_run import UpdatePagesRun

        hold = hold if hold is not None else _TabsHold("Update Users")
        if usage is None:
            return None
        busy = UpdatePagesRun.busy_text("Update the users")
        if busy:
            # (the chain after a genie save, Update Pages and its chain save the same genies
            # and pages: one run at a time)
            QtWidgets.QMessageBox.information(self, "Update Users", busy)
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
                    _names(
                        ["%s (%s)" % (u.ref.label, why) for u, why in plan.skipped]
                        + ["%s (not registered in its project)" % u.ref.label for u in unreg]
                    ),
                )
            QtWidgets.QMessageBox.information(self, "Update Users", text)
            return None
        if confirm:

            def names(users, k=8):
                ns = [u.ref.label for u in users]
                more = " and %d more" % (len(ns) - k) if len(ns) > k else ""
                return ", ".join(ns[:k]) + more

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
                    "Left alone: %s."
                    % "; ".join("%s (%s)" % (u.ref.label, why) for u, why in plan.skipped[:5])
                )
            lines.append("The files are backed up first (Tools > Restore Backup puts them back).")
            if not self._confirm(
                "Update Users",
                "Update what uses %s?" % usage.target.label,
                "Update",
                "\n\n".join(lines),
                icon=QtWidgets.QMessageBox.Question,
            ):
                return None
            # (the question let events through: a run started meanwhile in another window
            # goes first)
            busy = UpdatePagesRun.busy_text("Update the users")
            if busy:
                QtWidgets.QMessageBox.information(self, "Update Users", busy)
                return None
        try:
            from ...backend.graphics_builder import open_pages

            in_builder = open_pages()
        except Exception:  # noqa: BLE001
            in_builder = []
        # (the pages open in a tab the run may change: a read-only one is a closed page here,
        # as for the chain after a library save)
        open_paths = self._open_page_paths()
        # (a stop keeps the pages done so far: the button says so, not Cancel)
        prog = QtWidgets.QProgressDialog("Updating…", "Stop After This Page", 0, 100, self)
        prog.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # (gone once closed)
        prog.setWindowTitle("Update Users")
        prog.setWindowModality(QtCore.Qt.WindowModal)
        prog.setMinimumDuration(0)
        stats = RunStats.from_settings()
        # (sized once for the widest figures: the window never jumps while the run goes on)
        steady = SteadyProgressLabel(prog, stats)
        steady.set(["Updating…"])

        def show(head):
            steady.set([head] + stats.lines())
            QtWidgets.QApplication.processEvents()
            return not prog.wasCanceled()

        def tick(i, n, label):
            prog.setMaximum(max(1, n))
            prog.setValue(i)
            if label == "pages":
                # (the genies are all done: the pages step starts, its count with it)
                stats.begin_phase("pages", len(plan.pages))
                stats.update(0)
                return show("Updating the pages…")
            stats.begin_phase("genies", len(plan.elements))
            stats.update(i)
            return show("Saving %s…" % label)

        def page_tick(i, n, name):
            # (the pages step, page by page)
            prog.setMaximum(max(1, int(n)))
            prog.setValue(min(int(i), max(1, int(n))))
            stats.begin_phase("pages", int(n))
            stats.update(int(i))
            return show("Updating %s… (%d of %d)" % (name, int(i) + 1, int(n)))

        def opened_now(path):
            # (page_tick lets events through: a page opened in another window during the
            # pages step is left, and its tab is updated after the run like the others)
            n = os.path.normcase
            return n(os.path.abspath(path)) in {n(os.path.abspath(p)) for p in self._open_page_paths()}

        backup = updatepages.backup_dir()
        marks = self._open_tab_marks()
        from ...render import headless_thumb

        open_at_start = _page_mtimes(
            p
            for p in open_paths
            if os.path.normcase(os.path.abspath(p))
            in {os.path.normcase(os.path.abspath(u.ref.path)) for u in plan.pages}
        )
        # (one run at a time: a genie saved in another window meanwhile waits for this one,
        # and Update Pages and Pack Libraries do not start; tick() lets events through)
        UpdatePagesRun.users_busy = usage.target.label
        # (and until the open tabs have followed it: Simulate, the other runs and a chain
        # that waits in the queue wait for that too, _follow_run_saves)
        hold.take()
        try:
            # (each element and page saved gets a fresh thumbnail, as the chain's helper
            # writes it: one result for both)
            rep = GU.run_update(
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
            tabs.left_follow = _follow_words(saved, rep.element_kinds)
        self._follow_run_saves(saved, hold)
        if tabs.left_follow:
            self._left_follow(tabs, saved, rep.element_kinds)
        if rep.pages is not None:
            open_at_start.update(
                _page_mtimes(
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
        for w in _mw().MainWindow._instances:
            with contextlib.suppress(RuntimeError):
                w._refresh()
        from ..names import plural

        pr = rep.pages
        lines = []
        if saved:
            # (counted by kind, as the chain after a library save counts them)
            lines.append(
                "%s saved again: %s."
                % (self._and_list(self._saved_kinds_parts(saved, rep.element_kinds)), _names(saved))
            )
        if rep.elements_current:
            n = len(rep.elements_current)
            lines.append(
                "%s already matched what %s: %s."
                % (
                    self._and_list(self._saved_kinds_parts(rep.elements_current, rep.element_kinds)),
                    "it places" if n == 1 else "they place",
                    _names(rep.elements_current),
                )
            )
        if rep.not_composed:
            # (a save that wrote but kept a copy it could not compose: its old stamp stays,
            # so Used By still calls it out of date; only this line says why)
            lines.append(self._not_composed_text(rep.not_composed))
        if pr is not None or tabs.paths() or tabs.left:
            pr = pr if pr is not None else updatepages.UpdateReport()
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
                    "Links to library elements no longer on the page removed: %s." % _names(pr.links_dropped)
                )
            if pr.unchanged:
                lines.append(
                    "Out of date, but nothing on them could be updated (the log says "
                    "why): %s." % _names(pr.unchanged)
                )
            # (two pages of one name in two projects told apart, as the closed ones are)
            lines += tabs.lines(_page_label([u.ref.path for u in plan.pages] + tabs.paths() + tabs.left))
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
                    "and closed before it ended. Run Update Users again: %s" % _names(closed_meanwhile)
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
                    "Project database rows written for pages saved earlier: %s"
                    % ", ".join(pr.rows_written[:10])
                )
            if pr.not_checked:
                lines.append("Not checked, because the run was stopped: %s." % plural(pr.not_checked, "page"))
            lines += ["Warning: %s" % w for w in pr.warnings[:10]]
        if rep.super_pages:
            lines.append("Super genie pages created: %s." % ", ".join(rep.super_pages))
        lines += ["Not updated: %s." % x for x in rep.skipped[:10]]
        lines += ["Failed: %s" % x for x in rep.failed[:10]]
        if rep.stopped:
            lines.append("Stopped before the end.")
        if (pr is not None and pr.updated) or saved:
            lines.append("Compile the project to see the changes in the runtime.")
            lines.append("Backup: %s (Tools > Restore Backup)" % backup)
        if stats.total_text():
            lines.append(stats.total_text())
        from ...infra import backups, logsetup

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
        # the Used By windows show the new state (once the result box is closed)
        self._follow_used_by_windows()
        return rep

    def _follow_run_saves(self, saved, hold=None) -> None:
        """Update open tabs after a run saved library elements."""
        try:
            if saved:
                for w in _mw().MainWindow._instances:
                    with contextlib.suppress(Exception):
                        w._chain_templates_in_tabs(saved)
                with contextlib.suppress(Exception):
                    self._follow_symbols_in_tabs()
            if saved and app_settings().value("opt/auto_update_genies", True, type=bool):
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
            and os.path.normcase(os.path.abspath(u.ref.path)) not in open_paths
        ]
        return list(plan.elements), closed

    def _users_left_text(self, usage, name: str, open_paths=None) -> str:
        """What the chain leaves, for the save prompts."""
        from ..names import plural

        if usage is None:
            return ""
        if open_paths is None:
            open_paths = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        out = []
        unreg = [
            u.ref.label
            for u in usage.users
            if u.ref.kind == "page"
            and u.needs_update
            and u.registered is False
            and os.path.normcase(os.path.abspath(u.ref.path)) not in open_paths
        ]
        if unreg:
            out.append(
                "%s not registered in %s project %s left alone: %s."
                % (
                    plural(len(unreg), "page"),
                    "its" if len(unreg) == 1 else "their",
                    "is" if len(unreg) == 1 else "are",
                    _names(unreg, 5),
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
            key = os.path.normcase(os.path.abspath(p))
            found = None
            for w in _mw().MainWindow._instances:
                with contextlib.suppress(RuntimeError):
                    found = found or next(
                        (
                            (w, e["doc"])
                            for e in w._docs
                            if _page_tab(e["doc"]) and os.path.normcase(os.path.abspath(e["doc"].path)) == key
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
            tabs.left_words[p] = _follow_words(mine, kinds)
            if kept:
                tabs.left_template.append(p)

    def _users_nothing_to_update_note(
        self, usage, name: str, said: bool = False, kind: str = "genie"
    ) -> None:
        """Note that nothing uses the saved element."""
        now = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        text = self._users_left_text(usage, name, now)
        from ..notify import notify

        tabs = ""
        if said and usage is not None:
            n = sum(
                1
                for u in usage.users
                if u.ref.kind == "page"
                and u.needs_update
                and os.path.normcase(os.path.abspath(u.ref.path)) in now
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
        (:func:`_page_tab`, :func:`_element_tab`)."""
        own = (os.path.normcase(os.path.abspath(lib_path)), (element or "").lower())
        for w in _mw().MainWindow._instances:
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                if _page_tab(d):
                    return True
                if (
                    _element_tab(d)
                    and (os.path.normcase(os.path.abspath(d.path or "")), (d.element_name or "").lower())
                    != own
                ):
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
        parts = LibraryCommands._kind_parts(kinds)
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

        words = words or LibraryCommands._KIND_WORDS
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
        return LibraryCommands._kind_parts(kinds, words)

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
            _names(LibraryCommands._not_composed_names(left))
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
        if (
            kind == "template"
            and mode == "ask"
            and not app_settings().value("opt/save_template_warning", True, type=bool)
        ):
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
        open_paths = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        genies, closed = self._users_to_update(usage, open_paths)
        if not genies and not closed:
            self._users_nothing_to_update_note(usage, doc.display_name, said=mode == "always", kind=kind)
            return
        if mode == "always":
            self._update_closed_pages(doc)
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle(title)
        # (the open tabs take the save while the question is up: what is open comes first)
        box.setProperty("ctg_lets_open_tabs_update", not self._tabs_wait_for_runs())
        # (what the chain updates, by kind: not the pages open in tabs, which take it there,
        # nor the pages it leaves, named below)
        box.setText(self._users_count_text(genies, len(closed), doc.display_name))
        box.setInformativeText(
            " ".join(
                x
                for x in (
                    with_elements if genies else pages_only,
                    self._TEMPLATE_TABS_TEXT
                    if kind == "template"
                    and app_settings().value("opt/auto_update_template", True, type=bool)
                    else "",
                    self._users_left_text(usage, doc.display_name, open_paths),
                )
                if x
            )
        )
        run = box.addButton("Update in Background", QtWidgets.QMessageBox.AcceptRole)
        # (a template: the Graphics Builder's own offer too, its chain box ticked so the
        # templates built on it are saved first)
        pages_btn = (
            box.addButton("Update Pages…", QtWidgets.QMessageBox.ActionRole) if kind == "template" else None
        )
        show = box.addButton("Show Users…", QtWidgets.QMessageBox.ActionRole)
        later = box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
        again = QtWidgets.QCheckBox("Don't ask again", box)  # (owned by the box)
        box.setCheckBox(again)
        self._exec_prompt(box)
        clicked = box.clickedButton()
        if again.isChecked() and clicked in (run, later):
            # (the choice is remembered; Show Users and Update Pages are no answer to the
            # question: it stays asked. Settings > Pages and libraries changes it)
            put("always" if clicked is run else "never")
        if clicked is run:
            self._update_closed_pages(doc)
        elif clicked is show:
            self._show_used_by(doc.path, doc.element_name)
        elif pages_btn is not None and clicked is pages_btn:
            self._update_pages(chain=True)

    def _open_page_paths(self) -> list:
        """Page files open in tabs a run may update."""
        return [e["doc"].path for w in _mw().MainWindow._instances for e in w._docs if _page_tab(e["doc"])]

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
            live = list(_mw().MainWindow._instances)
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
        return LibraryCommands._only_automatic_since_saved(d)

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

    # -- the chain helper's own writes -------------------------------------------------------
    #: the progress file of the chain after a library save running in its helper now ('':
    #: none); its answer so far is written beside it (genie_worker.GENIES_SUFFIX, the
    #: pages' records: RECORDS_SUFFIX)
    _chain_progress = ""

    @staticmethod
    def _chain_versions(res) -> dict:
        """What a chain's helper read and wrote."""

        def key(p):
            return os.path.normcase(os.path.abspath(str(p)))

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

        return cls._chain_versions(GW.chain_answer_so_far(LibraryCommands._chain_progress))

    def _chain_may_write(self, d) -> bool:
        """The chain after a library save running in its helper now may write ``d``'s file:
        a library element's tab (its plan saves elements), a page of its pages phase (its
        answer so far names it); never a new element, a read-only page or a tab not opened
        yet."""
        if isinstance(d, _PendingPage):
            return False
        if isinstance(d, ElementDocument):
            return not d.is_new
        if getattr(d, "read_only", False):
            return False
        so_far = self._chain_versions_so_far()
        where = os.path.normcase(os.path.abspath(d.path or ""))
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
            where = os.path.normcase(os.path.abspath(d.path or ""))
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
                and editing_mod.EditableDocument._stamp(d.path) == v[1]
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
        for w in _mw().MainWindow._instances:
            for e in getattr(w, "_docs", ()):
                d = e["doc"]
                # (an earlier version shown read-only is not the element's tab)
                if _element_tab(d):
                    rows.append(
                        "%s\t%s\t%s"
                        % ("clean" if LibraryCommands._tab_clean(d) else "dirty", d.path, d.element_name)
                    )
        return "\n".join(rows)

    def _start_users_run(self, lib_path: str, element: str, name: str) -> bool:
        """The chain of _update_closed_pages for ``lib_path``/``element`` (``name`` =
        'lib.element'), started now. Outside a running event loop (scripts, tests) it runs
        at once in this process. False when it could not start (said)."""
        import tempfile
        from ...workers import genie_worker as GW
        from ...infra import workers
        from ...infra.paths import data_dir
        from ...io import updatepages
        from ..jobs.helper_job import RecordTail
        from ..progress_text import hold_width
        from ..jobs.run_stats import RunStats
        from ..jobs.update_run import UpdatePagesRun

        try:
            from ...backend.graphics_builder import open_pages

            in_builder = [list(x) for x in open_pages()]
        except Exception:  # noqa: BLE001
            in_builder = []
        folder = data_dir("genieupdate")
        inline = QtCore.QThread.currentThread().loopLevel() == 0
        proc = None
        try:
            os.makedirs(folder, exist_ok=True)
            fd, req = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".json", dir=folder)
            os.close(fd)
            base = req[:-5]
            files = {
                "progress_file": base + ".progress",
                "skip_file": base + ".skip",
                "cancel_file": base + ".cancel",
                "editor_file": base + ".editors",
            }
            _write_text(files["skip_file"], "\n".join(self._open_page_paths()))
            _write_text(files["editor_file"], self._element_tab_states())
            open_at_start = _page_mtimes(self._open_page_paths())
            request = {
                "format": GW.FORMAT,
                "library": lib_path,
                "element": element,
                "skip": self._open_page_paths(),
                "builder_open": in_builder,
                "backup": updatepages.backup_dir(),
                "elements": True,
                **files,
            }
            with open(req, "w", encoding="utf-8") as fh:
                json.dump(request, fh)
            ans = base + ".answer.json"
            if not inline:
                proc = workers.track("pages", workers.start(GW.users_command(req, ans)))
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("main_window._update_closed_pages")
            from ..notify import notify, open_log_action

            notify(
                self,
                "Could not start the update of what uses %s." % name,
                "error",
                actions=(open_log_action(),),
            )
            return False
        UpdatePagesRun.users_busy = name
        # (its answer so far: a tab whose file it stored from the version the tab holds
        # takes it as its own at once, _take_chain_write)
        LibraryCommands._chain_progress = files["progress_file"]
        # (the open tabs' genie updates that have not landed: they land when they are done,
        # the run's end brings the tabs up to date again; with Settings 'Open tabs wait for
        # the update of what uses a saved genie' they wait for the run's end, composed once
        # (files._hold_tab_updates_for_run))
        if self._tabs_wait_for_runs():
            with contextlib.suppress(Exception):
                self._hold_tab_updates_for_run()
        job = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(job)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel("Finding what uses %s…" % name)
        lay.addWidget(label)
        btn = QtWidgets.QToolButton()
        btn.setText("Stop")
        btn.setToolTip("Stop after the genie or page being updated now. The pages done so far stay updated")
        lay.addWidget(btn)
        self.statusBar().addPermanentWidget(job)
        self._users_job = job  # (tests read its label)
        from ..prefs.job_options import poll_ms

        timer = QtCore.QTimer(self)
        timer.setInterval(poll_ms())
        # (``seen``: each page open during the run, with its file's time when first seen
        # open: else one closed before the run ended unsaved is updated by nobody)
        state = {"result": None, "took": "", "seen": open_at_start}
        # (the progress figures: the genies phase, then the pages with their records)
        stats, tail = RunStats.from_settings(), RecordTail()

        def remove_job():
            with contextlib.suppress(RuntimeError):
                self.statusBar().removeWidget(job)
                job.deleteLater()

        def cancel():
            if state["result"] is None:
                with contextlib.suppress(OSError):
                    open(files["cancel_file"], "w").close()
                btn.setEnabled(False)
                label.setText("Stopping after the genie or page being updated…")
                tasks.progress(task, 0, 0, "Stopping the update of what uses %s…" % name)
                return
            remove_job()

        # (a background task too: the tasks button lists it with Cancel, and its end stays
        # in the window's messages with Details: a user who was away finds it there)
        from ..jobs import tasks

        task = tasks.begin("users", "Finding what uses %s…" % name, cancel=cancel, window=self)

        def owner():
            # (the window that started the run, or another editor window when it was
            # closed meanwhile: the result is told where it can be seen)
            wins = list(_mw().MainWindow._instances)
            return self if self in wins or not wins else wins[0]

        def details():
            QtWidgets.QMessageBox.information(
                owner(),
                "Users Updated",
                "\n\n".join(
                    [self._users_run_details(state["result"] or {})] + [x for x in (state["took"],) if x]
                ),
            )

        def lists():
            # (pages opened since: the worker leaves them; element tabs edited since: left.
            # Each list is replaced whole: the worker never reads one half written)
            opened = self._open_page_paths()
            for p, t in _page_mtimes(p for p in opened if p not in state["seen"]).items():
                state["seen"][p] = t
            with contextlib.suppress(OSError):
                _write_text(files["skip_file"], "\n".join(opened))
            with contextlib.suppress(OSError, RuntimeError):
                _write_text(files["editor_file"], self._element_tab_states())

        def finish():
            # (CtDraw's cache notice of the genies it saved: once, when it has all ended)
            with self._cache_notice_held():
                finish_run()

        def finish_run():
            state["took"] = stats.total_text()
            try:
                with open(ans, encoding="utf-8") as fh:
                    res = json.load(fh)
            except OSError, ValueError:
                res = {"error": "the update gave no answer"}
            if res.get("error"):
                # (no answer, or only an error: what the helper saved before it stopped,
                # from its partial answer (written after each element) and the records of
                # its pages (a line each as it is done); read before they are removed below)
                try:
                    res = GW.chain_partial_answer(res, files["progress_file"], request.get("backup"))
                except Exception:  # noqa: BLE001 -- the error is said either way
                    from ...infra import logsetup

                    logsetup.note_exception("main_window._update_closed_pages/partial")
            leftovers = [
                req,
                ans,
                files["progress_file"] + GW.GENIES_SUFFIX,
                files["progress_file"] + GW.RECORDS_SUFFIX,
            ] + [p + x for p in files.values() for x in ("", ".tmp")]
            for x in leftovers:
                with contextlib.suppress(OSError):
                    os.remove(x)
            state["result"] = res
            UpdatePagesRun.users_busy = ""
            LibraryCommands._chain_progress = ""
            failed = bool(res.get("error"))
            if not res.get("error"):
                # (the pages it left because they were open, closed since and not saved)
                left = list(res.get("open_at_start") or []) + list(res.get("skipped_open_paths") or [])
                seen = {os.path.normcase(os.path.abspath(k)): t for k, t in state["seen"].items()}
                res["closed_meanwhile"] = self._closed_meanwhile(
                    {p: seen.get(os.path.normcase(os.path.abspath(p))) for p in left},
                    res.get("after_saves") or (),
                    names_from=list(res.get("pages") or ()) + left,
                )
            win = owner()
            if win is not self:
                # (its note goes with the result to a window that is open)
                with contextlib.suppress(RuntimeError):
                    win.statusBar().addPermanentWidget(job)
                    win._users_job = job
            try:
                text = win._users_run_finished(
                    res,
                    name,
                    {".ctt": "template", ".ctl": "symbol"}.get(
                        os.path.splitext(lib_path)[1].lower(), "genie"
                    ),
                )
            except Exception:  # noqa: BLE001 -- the run is over either way
                from ...infra import logsetup

                logsetup.note_exception("main_window._update_closed_pages/finish")
                text = "Updated what uses %s. Its result could not be read." % name
                failed = True
            backup = str(res.get("backup") or "")
            backup_text = (
                ("Backup: %s (Tools > Restore Backup puts the files back)." % backup) if backup else ""
            )
            label.setMinimumWidth(0)  # (the run's room: the result fits itself)
            label.setText(text)
            label.setToolTip("\n".join(x for x in (state["took"], backup_text) if x))
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
                more.clicked.connect(details)
                lay.insertWidget(1, more)
            if failed:
                # (the note names no log it cannot open: the button opens it)
                from ..messages import open_log_button

                log_btn = open_log_button(job)
                log_btn.setVisible(True)
                lay.insertWidget(1, log_btn)
            # (the end stays in the window's messages, Details and the backup with it)
            from ..notify import Action, notify

            tasks.end(task, ok=not res.get("error"), tell=False)
            level = self._chain_level(res)
            from ..notify import open_log_action

            acts = [Action("Details", details)] if has_details else []
            if failed:
                acts.append(open_log_action())  # (the log it points at)
            notify(win, text, level, actions=acts, key="users:%s" % name, detail=backup_text)
            btn.setText("")
            from ..style.theme import icon as _icon

            btn.setIcon(_icon("close"))
            btn.setToolTip("Close this note")
            btn.setEnabled(True)
            # (the note stays Settings > Long jobs 'Keep the note of a finished genie
            # update for', times Appearance > Messages stay; 0: until its Close)
            from ..prefs.job_options import users_note_s
            from ..notify import scaled_ms

            stay = users_note_s()
            if stay > 0:
                QtCore.QTimer.singleShot(scaled_ms(stay * 1000), remove_job)
            # (a save that waited for this run goes next)
            if UpdatePagesRun.users_queue:
                QtCore.QTimer.singleShot(0, win, win._next_users_run)

        def poll():
            lists()
            if proc is not None and proc.poll() is None:
                with contextlib.suppress(OSError, ValueError):
                    with open(files["progress_file"], encoding="utf-8") as fh:
                        pr = json.load(fh)
                    phase = "genies" if pr.get("phase") == "genies" else "pages"
                    stats.begin_phase(phase, int(pr["n"]))
                    if phase == "pages":
                        stats.add_records(tail.take([files["progress_file"] + GW.RECORDS_SUFFIX]))
                    stats.update(int(pr["i"]))
                    if btn.isEnabled():
                        what = (
                            "Saving the library genies that use %s"
                            if phase == "genies"
                            else "Updating the closed pages that use %s"
                        )
                        text = "%s: %d of %d (%s)" % (what % name, pr["i"] + 1, pr["n"], pr["name"])
                        tasks.progress(
                            task, int(pr["i"]) + 1, int(pr["n"]), "%s: %s…" % (what % name, stats.step_text())
                        )
                        label.setText(_with_figures(text, stats.time_text()))
                        label.setToolTip("\n".join(stats.lines()))
                        # (the figures change their length at every look: the note keeps
                        # room for the widest, so the fields beside it stay put)
                        hold_width(
                            label,
                            [_with_figures(text, t) for t in stats.widest_times()] if stats.show else (),
                        )
                return
            timer.stop()
            timer.deleteLater()
            finish()

        btn.clicked.connect(cancel)
        if inline:
            from ...format import projectlock

            try:
                res = GW.update_closed_pages(
                    lib_path,
                    element,
                    request["skip"],
                    in_builder,
                    request["backup"],
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
            with open(ans, "w", encoding="utf-8") as fh:
                json.dump(res, fh)
            timer.deleteLater()
            finish()
            return True
        timer.timeout.connect(poll)
        timer.start()
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
        if any(res.get(k) for k in LibraryCommands._CHAIN_WARN):
            return "warning"
        if any(not str(x).endswith(": " + SHIPPED_WHY) for x in res.get("elements_skipped") or ()):
            return "warning"
        return "success"

    def _users_run_finished(self, res: dict, name: str, kind: str = "genie") -> str:
        """The end of a chain: reload saved tabs, follow libraries and open tabs."""
        from ...infra import logsetup
        from ..names import plural
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
                if app_settings().value("opt/auto_update_genies", True, type=bool):
                    for w in _mw().MainWindow._instances:
                        with contextlib.suppress(Exception), self._updates_for("follow"):
                            w._queue_open_genie_updates()
            # (the open pages took the saved template at its save, _follow_saved_template:
            # not again here, an update the user undid stays undone)
            self._follow_used_by_windows()
            # (the pages it saved before it stopped are named too: their files changed)
            upd = list(res.get("updated") or [])
            parts = self._saved_kinds_parts(saved, kinds_of) + (
                [plural(len(upd), "closed page")] if upd else []
            )
            if parts:
                return "Saved %s using %s, then the update failed." % (self._and_list(parts), name)
            return "Could not update what uses %s." % name
        upd, failed = res.get("updated") or [], res.get("failed") or []
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
        if saved or upd or failed or res.get("elements_failed"):
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
        now = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        tabs = {
            os.path.normcase(os.path.abspath(p))
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
        for w in _mw().MainWindow._instances:
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
        now = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        follow = {os.path.normcase(os.path.abspath(p)) for p in after_saves or () if p}
        out = set()
        for p, t in (seen or {}).items():
            if not p or os.path.normcase(os.path.abspath(p)) in now:
                continue
            if (
                t is not None
                and _page_mtimes([p]).get(p) != t
                and os.path.normcase(os.path.abspath(p)) not in follow
            ):
                continue  # (saved before it was closed, and current)
            out.add(os.path.abspath(p))
        # (two of one name in two projects told apart by their project, as the run's other
        # lines name them)
        label = _page_label(list(out) + [p for p in names_from or () if p])
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
        for w in _mw().MainWindow._instances:
            with contextlib.suppress(RuntimeError):
                w._chain_templates_in_tabs(saved)
        # (then the placed symbols, as a page's open meets them before its genies; every
        # window's tabs: Settings > Pages and libraries, Update changed symbols)
        with contextlib.suppress(Exception):
            self._follow_symbols_in_tabs()
        if app_settings().value("opt/auto_update_genies", True, type=bool):
            for w in _mw().MainWindow._instances:
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
        tpl_on = app_settings().value("opt/auto_update_template", True, type=bool)
        changed, from_super = 0, 0
        for e in list(getattr(self, "_docs", ())):
            d = e["doc"]
            if docs is not None and not any(d is x for x in docs):
                continue
            if not _page_tab(d):
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
        libs = {os.path.normcase(os.path.abspath(str(p))) for p in res.get("libraries") or () if p}
        pages = {os.path.normcase(os.path.abspath(str(p))) for p in res.get("pages") or () if p}
        versions = self._chain_versions(res)
        views = self._reload_read_only_views(pages)
        listed = set()  # (the tabs it knew: _reload_saved_tabs)
        for r in res.get("tabs_saved") or ():
            with contextlib.suppress(TypeError, ValueError):
                p, el = r[:2]
                listed.add((os.path.normcase(os.path.abspath(str(p))), str(el).lower()))
        refs, reload_pages, own_pages = [], [], []
        for w in _mw().MainWindow._instances:
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                d = e["doc"]
                if isinstance(d, _PendingPage) or getattr(d, "read_only", False):
                    continue
                try:
                    where = os.path.normcase(os.path.abspath(d.path or ""))
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

        want = {os.path.normcase(os.path.abspath(str(p))) for p in paths or () if p}
        if not want:
            return 0
        n = 0
        for w in _mw().MainWindow._instances:
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                d = e["doc"]
                if (
                    isinstance(d, (ElementDocument, _PendingPage))
                    or is_version(d)
                    or not getattr(d, "read_only", False)
                ):
                    continue
                try:
                    if (
                        os.path.normcase(os.path.abspath(d.path or "")) not in want
                        or d.dirty
                        or not d.changed_on_disk()
                    ):
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
                want.add((os.path.normcase(os.path.abspath(str(p))), str(el).lower()))
        if not want:
            return
        from ..editor import notices
        from .files import NESTED_DONE

        versions = self._chain_versions(res) if res is not None else None
        look = []
        for w in _mw().MainWindow._instances:
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                # (never an earlier version shown read-only)
                key = (
                    (os.path.normcase(os.path.abspath(d.path or "")), (d.element_name or "").lower())
                    if _element_tab(d)
                    else None
                )
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
        if look and app_settings().value("opt/auto_update_genies", True, type=bool):
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
                    "stored:\n  %s" % "\n  ".join(LibraryCommands._not_composed_names(res["not_composed"]))
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

    # ------------------------------------------------------- colour tools
    def _colour_tool_targets(self, scope: str = "selection"):
        """The objects Swap / Adjust Colours change."""
        if self.doc is None:
            return None
        if scope != "page" and self._sel:
            idx = self._filter_template(self._sel, "recolour")
            return idx or None
        roots = [i for i, o in enumerate(self.doc.page.objects) if o.parent is None]
        return self._filter_template(roots, "recolour", quiet=True) or None

    def _colour_count(self, doc, indices) -> int:
        """How many objects 'Selection' recolours (the selection windows)."""
        if doc is None or doc is not self.doc or not indices:
            return 0
        n = len(doc.page.objects)
        sel = self._filter_template([i for i in indices if 0 <= i < n], "recolour", quiet=True)
        return len({doc.root_of(i) for i in sel})

    def _colours_in_use(self, doc, indices, scope: str = "selection") -> list:
        """The colours Swap / Adjust Colours would change in their scope, most used first:
        [((on, off), objects)] (the fill and line pairs of each object, a pipe's colour;
        what colourstate.colour_channels gives, the objects colour_targets reaches)."""
        if doc is None or doc is not self.doc:
            return []
        from ...format import colourstate, colourtools as T

        n = len(doc.page.objects)
        if scope == "page" or not indices:
            targets = None
        else:
            targets = (
                self._filter_template([i for i in indices if 0 <= i < n], "recolour", quiet=True) or None
            )
            if targets is None:
                return []
        objs = doc.parsed.page.objects
        counts: dict = {}
        for i in doc._colour_targets(targets):
            if not 0 <= i < len(objs):
                continue
            ch = colourstate.colour_channels(objs[i])
            if ch is None:
                continue
            seen = set()
            for pair in ch.values():
                # (the 'no colour' values are the one Transparent, as Swap Colours finds
                # them: colourtools.same_colour)
                p = tuple(T.TRANSPARENT if T.is_clear(c) else int(c) & 0xFFFFFFFF for c in pair[:2])
                if p in seen:
                    continue
                seen.add(p)
                counts[p] = counts.get(p, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def _swap_colors(self) -> None:
        """Tools > Colours > Swap Colours: a modeless window; Apply / OK swap
        the colours of the selection or the whole page, one undo step."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("swapcol")):
            self._sync_selection_windows()
            return
        from ..dialog.colour_dialogs import SwapColorsDialog

        targets = self._colour_tool_targets() if self._sel else None
        frm = self.doc.selection_colour(targets) if targets else None
        dlg = SwapColorsDialog(self, frm=frm, count_of=self._colour_count, colours_of=self._colours_in_use)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_swap(d))
        self._show_modeless(dlg, "swapcol", scope="selection")
        self._sync_selection_windows()

    def _apply_swap(self, dlg) -> None:
        """Apply / OK of Swap Colours: the targets as they are now."""
        targets = self._colour_tool_targets(dlg.scope())
        if targets is None:
            self.statusBar().showMessage("Nothing to change. Select objects or choose the whole page.", 5000)
            return
        frm, to, swap_range = dlg.values()
        n = self.doc.swap_colours(targets, frm, to, swap_range=swap_range)
        self._refresh()
        self.statusBar().showMessage(
            "Swapped the colours of %d object(s)." % n
            if n
            else "No object has the From colour. Nothing was changed.",
            5000,
        )

    def _adjust_colors(self) -> None:
        """Tools > Colours > Adjust Colours: a modeless window; Apply / OK
        adjust the colours of the selection or the whole page, one undo
        step."""
        if self.doc is None:
            return
        if self._raise_modeless(self._dialogs.get("adjustcol")):
            self._sync_selection_windows()
            return
        from ..dialog.colour_dialogs import AdjustColorsDialog

        dlg = AdjustColorsDialog(self, count_of=self._colour_count, colours_of=self._colours_in_use)
        dlg.applyRequested.connect(lambda d=dlg: self._apply_adjust_colours(d))
        self._show_modeless(dlg, "adjustcol", scope="selection")
        self._sync_selection_windows()

    def _apply_adjust_colours(self, dlg) -> None:
        """Apply / OK of Adjust Colours: the targets as they are now."""
        adj = dlg.adjustment()
        if adj.is_identity():
            self.statusBar().showMessage(
                "No hue, lightness or saturation change was set. Nothing was changed.", 5000
            )
            return
        targets = self._colour_tool_targets(dlg.scope())
        if targets is None:
            self.statusBar().showMessage("Nothing to change. Select objects or choose the whole page.", 5000)
            return
        n = self.doc.adjust_colours(targets, adj)
        self._refresh()
        self.statusBar().showMessage(
            "Adjusted the colours of %d object(s)." % n
            if n
            else "No colour is in the selected hue range. Nothing was changed.",
            5000,
        )

    def _favorite_colors(self) -> None:
        """Tools > Edit Favorite Colors: the favourites of this project's
        palette (its citect.pal, shared with the Graphics Builder)."""
        from .. import colour_picker

        folder = (
            os.path.dirname(os.path.abspath(self.doc.path))
            if self.doc is not None
            else colour_picker.context_dir(self)
        )
        colour_picker.edit_favourites(self, None, folder)

    # ------------------------------------------------------ bitmap editor
    def _bitmap_target(self, quiet: bool = False):
        """The embedded bitmap Tools > Bitmap Editor edits: the selected one,
        or the only one inside a selected small plain group (a logo symbol
        holds one bitmap). ``(index, None)`` or ``(None, reason)``;
        ``quiet`` = a state query (no status message)."""
        from ...format import bitmaps

        if self.doc is None or len(self._sel) != 1:
            return None, "Select one bitmap first"
        i = self._sel[0]
        objs = self.doc.parsed.page.objects
        if not 0 <= i < len(objs):
            return None, "Select one bitmap first"
        if bitmaps.dib_section(objs[i]) is None:
            sub = self.doc.subtree(i) if objs[i].type_code == 0x08 else []
            inner = [j for j in sub if len(sub) <= 64 and bitmaps.dib_section(objs[j]) is not None]
            if objs[i].type_code == 0x08 and len(inner) == 1:
                i = inner[0]
            elif objs[i].type_code == bitmaps.METAFILE_TC:
                return None, (
                    "The selection is a metafile (a vector drawing); use "
                    "Tools > Convert to Bitmap first to edit its pixels"
                )
            elif objs[i].type_code == bitmaps.BITMAP_TC and objs[i].subtype_size & 0x1:
                return None, (
                    "The selection is a library symbol; its picture belongs "
                    "to the library (right-click > Edit Definition, or Cut "
                    "Link first)"
                )
            else:
                return None, ("The selection is not a bitmap (use Tools > Convert to Bitmap first)")
        if not self._filter_template([i], "edit", quiet=quiet):
            return None, None  # locked / a genie's member: already said
        return i, None

    def _bitmap_editor(self) -> None:
        """Tools > Bitmap Editor (F9): edit the selected bitmap's pixels (both
        flash states) and put them back into the same record (a modeless
        window; one per bitmap)."""
        i, why = self._bitmap_target()
        if i is None:
            if why:
                self.statusBar().showMessage(why, 6000)
            return
        if self._raise_modeless(self._modeless_for("bitmap", i)):
            return
        from ...format import bitmaps

        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage(
                "The bitmap's picture could not be read (its stored data is damaged); "
                "import the picture again to replace it",
                8000,
            )
            return
        po = self.doc.parsed.page.objects[i]
        from ...format import geometry as geom_mod

        l, t, r, b = geom_mod.parse_geom_header(po.body_raw).rect1
        w, h = max(1, r - l), max(1, b - t)
        rgba, frgba = primary.rgba, flash.rgba if flash is not None else None
        note = ""
        if (w, h) != (primary.width, primary.height):
            # CtDraw edits a stretched bitmap at the size it has on the page
            rgba = bitmaps.resample(rgba, primary.width, primary.height, w, h)
            if frgba is not None:
                frgba = bitmaps.resample(frgba, primary.width, primary.height, w, h)
            note = (
                "The picture is %d x %d pixels, stretched to %d x %d on the page. "
                "It is edited at its size on the page; saving stores it at that "
                "size." % (primary.width, primary.height, w, h)
            )
        else:
            w, h = primary.width, primary.height
        from ..dialog.bitmap_editor import BitmapEditorDialog

        dlg = BitmapEditorDialog(rgba, w, h, self, flash_rgba=frgba, note=note)
        # modeless: Save writes into the record the editor opened, if it is
        # still on the page with the picture it had (else the edits stay)
        dlg.window_saves = True
        dlg._ctg_dib = self._dib_signature(po)
        # (the size it had on the page when the editor opened: a resize on the page after
        # it is kept by a Save of painted pixels, _save_bitmap)
        dlg._ctg_page_size = (r - l, b - t)
        dlg._ctg_no_reload = True
        # (through the editor window it belongs to NOW: Move to New Window takes it along
        # with its page, modeless._adopt_modeless)
        dlg._ctg_apply = lambda d=dlg: getattr(d, "_ctg_host", self)._save_bitmap(d)
        dlg.saveRequested.connect(lambda d=dlg: getattr(d, "_ctg_host", self)._save_bitmap(d))
        self._show_modeless(dlg, ("bitmap", i), scope="object", title="Bitmap Editor")

    @staticmethod
    def _dib_signature(po):
        """What the Bitmap Editor compares at Save: the record's DIB section
        (flags, size and both stored pictures)."""
        from ...format import bitmaps

        sec = bitmaps.dib_section(po)
        return None if sec is None else (sec.flags, sec.uncompressed, sec.primary, sec.flash)

    def _save_bitmap(self, dlg) -> bool:
        """Save of the Bitmap Editor: put its picture into its bitmap (one
        undo step). Refused in the editor, with the edits kept, when the
        bitmap is gone or its picture changed on the page since it opened.
        True when the editor closed."""
        if not dlg.modified():
            dlg.saved()
            return True
        i = self._modeless_target_index(dlg, getattr(dlg, "_ctg_index", -1))
        if i is None:
            dlg.show_problem(
                "The bitmap is no longer on the page, so the picture cannot "
                "be saved. Undo on the page brings the bitmap back."
            )
            return False
        from ...format import geometry as geom_mod

        po = self.doc.parsed.page.objects[i]
        if self._dib_signature(po) != getattr(dlg, "_ctg_dib", None):
            dlg.show_problem(
                "The bitmap's picture was changed on the page after the editor "
                "opened, so it cannot be saved over it."
            )
            return False
        l, t, r, b = geom_mod.parse_geom_header(po.body_raw).rect1
        # (a crop keeps the part it kept where it was on the page)
        dx, dy = dlg.origin_shift() if hasattr(dlg, "origin_shift") else (0, 0)
        rgba2, w2, h2 = dlg.result_rgba()
        flash = dlg.result_flash()
        # the size on the page: the editor's, unless the bitmap was resized on the page
        # after the editor opened. Painted pixels only: that newer size stays. A crop or a
        # resize in the editor too: asked which size to use (Cancel keeps the edits)
        opened = getattr(dlg, "_ctg_page_size", None)
        now = (r - l, b - t)
        out_w, out_h = w2, h2
        if opened is not None and now != opened:
            pixels_only = (w2, h2) == opened and (dx, dy) == (0, 0)
            if pixels_only:
                out_w, out_h = now
            else:
                box = QtWidgets.QMessageBox(dlg)
                box.setWindowTitle("Bitmap Editor")
                box.setText(
                    "The bitmap was resized on the page to %d x %d after the editor opened, "
                    "and the edited picture is %d x %d. Which size should it have on the page?"
                    % (now[0], now[1], w2, h2)
                )
                keep = box.addButton("Keep %d x %d" % now, QtWidgets.QMessageBox.AcceptRole)
                use = box.addButton("Use %d x %d" % (w2, h2), QtWidgets.QMessageBox.AcceptRole)
                box.addButton(QtWidgets.QMessageBox.Cancel)
                box.setDefaultButton(keep)
                box.exec()
                if box.clickedButton() is keep:
                    out_w, out_h = now
                elif box.clickedButton() is not use:
                    return False
        l, t = l + dx, t + dy
        if not self.doc.replace_bitmap(i, (l, t, l + out_w, t + out_h), rgba2, w2, h2, flash_rgba=flash):
            dlg.show_problem("The bitmap could not be changed.")
            return False
        self._refresh()
        size = "%d x %d" % (w2, h2)
        if (out_w, out_h) != (w2, h2):
            size += ", shown at %d x %d on the page" % (out_w, out_h)
        self.statusBar().showMessage(
            "Bitmap changed (%s%s)." % (size, ", flashing" if flash is not None else ""), 4000
        )
        dlg._ctg_page_size = (out_w, out_h)
        dlg.saved()
        return True

    # ------------------------------------------- replace / export / resample a picture
    def _picture_target(self, quiet: bool = False):
        """The one selected picture with its own pixels or drawing: ``(index, 'bitmap' |
        'metafile')``, or ``(None, reason)``. A small plain group holding one bitmap
        counts as that bitmap (as the Bitmap Editor)."""
        from ...format import bitmaps

        if self.doc is None or len(self._sel) != 1:
            return None, "Select one picture first"
        i = self._sel[0]
        objs = self.doc.parsed.page.objects
        if 0 <= i < len(objs) and bitmaps.metafile_bits(objs[i]) is not None:
            if not self._filter_template([i], "edit", quiet=quiet):
                return None, None
            return i, "metafile"
        j, why = self._bitmap_target(quiet=quiet)
        if j is None:
            if why and why.startswith("The selection is a metafile"):
                why = "The metafile's drawing could not be read"
            elif why and why.startswith("Select one bitmap"):
                why = "Select one picture first"
            elif why and why.startswith("The selection is not a bitmap"):
                why = "The selection is not a picture"
            return None, why
        return j, "bitmap"

    def _picture_rect(self, i: int):
        from ...format import geometry as geom_mod

        l, t, r, b = geom_mod.parse_geom_header(self.doc.parsed.page.objects[i].body_raw).rect1
        return l, t, max(l + 1, r), max(t + 1, b)

    def _replace_picture(self) -> None:
        """Edit > Replace Picture…: another picture file in the selected picture's place;
        it keeps its place, size (unless another is chosen), animations, commands, name
        and AN. The page's other copies of the same picture can be replaced with it."""
        i, kind = self._picture_target()
        if i is None:
            if kind:
                self.statusBar().showMessage(kind, 6000)
            return
        from ..dialog.bitmap_import import PictureError, add_recent, build, import_filter, open_source
        from ..dialog.picture_import import PictureImportDialog

        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Replace Picture", self._dialog_dir("import"), import_filter()
        )
        if not path:
            return
        self._remember_dir("import", path)
        try:
            src = open_source(path)
        except PictureError as e:
            QtWidgets.QMessageBox.warning(self, "Replace Picture", str(e))
            return
        if kind == "metafile" and src.kind != "metafile":
            QtWidgets.QMessageBox.warning(
                self,
                "Replace Picture",
                "The selection is a drawing (a metafile): it takes another drawing (.wmf or "
                ".emf). Delete it and import %s instead." % src.name,
            )
            return
        l, t, r, b = self._picture_rect(i)
        dlg = PictureImportDialog(
            [src], self, target=(r - l, b - t), action="Replace", drawing=kind == "metafile"
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        copies = self.doc.picture_copies(i) if kind == "bitmap" else []
        targets = [i]
        if copies:
            from ..messages import choose

            every = "Replace All %d" % (len(copies) + 1)
            got = choose(
                self,
                "Replace Picture",
                "The page shows this picture %d more time%s. Replace %s too?"
                % (len(copies), "" if len(copies) == 1 else "s", "it" if len(copies) == 1 else "them"),
                [every, "Only the Selected One"],
                every,
            )
            if got is None:
                return
            if got == every:
                targets += copies
        opts = dlg.options_for(src)
        done = 0
        try:
            with self.doc.batch("replace picture"):
                for n, j in enumerate(targets):
                    jl, jt, jr, jb = self._picture_rect(j)
                    o = (
                        opts
                        if n == 0
                        else opts._replace(
                            width=(jr - jl) * opts.width // max(1, r - l),
                            height=(jb - jt) * opts.height // max(1, b - t),
                        )
                    )
                    pic = build(src, o)
                    pw, ph = pic.size
                    rect = (jl, jt, jl + pw, jt + ph)
                    if kind == "metafile":
                        done += bool(self.doc.replace_metafile(j, rect, pic.data))
                    else:
                        done += bool(
                            self.doc.replace_bitmap(j, rect, pic.data, pic.width, pic.height, flash_rgba=None)
                        )
        except PictureError as e:
            QtWidgets.QMessageBox.warning(self, "Replace Picture", str(e))
            return
        add_recent([path])
        self._refresh()
        self.statusBar().showMessage(
            "Replaced the picture with %s%s" % (src.name, "" if done <= 1 else " (%d copies)" % done), 5000
        )

    def _export_picture(self) -> None:
        """File > Export Picture…: save the selected picture as a file (a bitmap as PNG,
        BMP or JPEG, both states of a flashing one; a drawing as .wmf or as PNG)."""
        i, kind = self._picture_target(quiet=True)
        if i is None:
            if kind:
                self.statusBar().showMessage(kind, 6000)
            return
        from ...format import bitmaps

        name = self.doc.object_name(i) or "picture"
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "picture"
        start = os.path.join(self._dialog_dir("export"), name)
        po = self.doc.parsed.page.objects[i]
        l, t, r, b = self._picture_rect(i)
        if kind == "metafile":
            flt = "Windows Metafile (*.wmf);;PNG picture (*.png)"
            path, chosen = QtWidgets.QFileDialog.getSaveFileName(self, "Export Picture", start + ".wmf", flt)
            if not path:
                return
            self._remember_dir("export", path)
            wmf = bitmaps.metafile_bits(po)
            try:
                if path.lower().endswith(".png") or chosen.startswith("PNG"):
                    from ...format.symbols import render_metafile

                    eb = render_metafile(wmf, 0, r - l, b - t, max_dim=max(r - l, b - t))
                    if eb is None or not self._image_of(eb.rgba, eb.width, eb.height).save(path):
                        raise OSError("the picture could not be drawn or written")
                else:
                    with open(path, "wb") as fh:
                        fh.write(bitmaps.placeable_wmf(wmf, r - l, b - t))
            except (OSError, bitmaps.MetafileError) as e:
                QtWidgets.QMessageBox.warning(self, "Export Picture", "%s could not be saved: %s" % (path, e))
                return
            self.statusBar().showMessage("Saved %s" % path, 5000)
            return
        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage("The bitmap's picture could not be read", 6000)
            return
        flt = "PNG picture (*.png);;Bitmap (*.bmp);;JPEG picture (*.jpg)"
        path, _chosen = QtWidgets.QFileDialog.getSaveFileName(self, "Export Picture", start + ".png", flt)
        if not path:
            return
        self._remember_dir("export", path)
        written = []
        stem, ext = os.path.splitext(path)
        # both names before either file is written: the file chooser asked about the one it
        # was given only, so a flashing picture's companion that exists is asked about here
        # (Cancel leaves both files as they are)
        outs = [
            (eb, out) for eb, out in ((primary, path), (flash, "%s (flashing)%s" % (stem, ext or ".png")))
        ]
        outs = [(eb, out) for eb, out in outs if eb is not None]
        companion = [out for eb, out in outs[1:] if os.path.exists(out)]
        if companion:
            from ..messages import confirm

            if not confirm(
                self,
                "Export Picture",
                "The flashing picture goes to %s, which already exists. Replace it?"
                % os.path.basename(companion[0]),
                "Replace",
            ):
                self.statusBar().showMessage("Export Picture cancelled: nothing was written.", 6000)
                return
        for eb, out in outs:
            img = self._image_of(eb.rgba, eb.width, eb.height)
            if ext.lower() in (".jpg", ".jpeg", ".bmp"):
                # no transparency there: the clear pixels over white
                flat = QtGui.QImage(img.size(), QtGui.QImage.Format_RGB32)
                flat.fill(QtGui.QColor(255, 255, 255))
                p = QtGui.QPainter(flat)
                p.drawImage(0, 0, img)
                p.end()
                img = flat
            if not img.save(out):
                QtWidgets.QMessageBox.warning(self, "Export Picture", "%s could not be saved." % out)
                return
            written.append(out)
        self.statusBar().showMessage("Saved %s" % " and ".join(os.path.basename(p) for p in written), 6000)

    def _find_duplicate_pictures(self) -> None:
        """Tools > Pictures > Find Duplicate Pictures…: the pictures the pages of this
        page's project store more than once (ui/dialog/duplicate_pictures)."""
        if self.doc is None or isinstance(self.doc, ElementDocument) or not self.doc.path:
            self.statusBar().showMessage("Open a page of the project first", 5000)
            return
        if getattr(self.doc, "is_new", False):
            self.statusBar().showMessage("Save the page into its project first", 5000)
            return
        from ..dialog.duplicate_pictures import DuplicatePicturesDialog

        DuplicatePicturesDialog(self, os.path.dirname(os.path.abspath(self.doc.path))).exec()

    @staticmethod
    def _image_of(rgba: bytes, w: int, h: int) -> QtGui.QImage:
        return QtGui.QImage(bytes(rgba), w, h, w * 4, QtGui.QImage.Format_RGBA8888).copy()

    def _resample_picture(self) -> None:
        """Tools > Resample Picture to Its Size: a stretched bitmap's pixels made the
        size it has on the page, scaled smoothly (the runtime stretches a bitmap
        coarsely). One undo step."""
        i, kind = self._picture_target()
        if i is None or kind != "bitmap":
            self.statusBar().showMessage(
                "A drawing (metafile) is always drawn at its size"
                if kind == "metafile"
                else kind or "Select one bitmap first",
                6000,
            )
            return
        from ..dialog.bitmap_import import smooth_resample
        from ...format import bitmaps

        primary, flash = self.doc.bitmap_pictures(i)
        if primary is None:
            self.statusBar().showMessage("The bitmap's picture could not be read", 6000)
            return
        l, t, r, b = self._picture_rect(i)
        w, h = r - l, b - t
        if (w, h) == (primary.width, primary.height):
            self.statusBar().showMessage(
                "The bitmap is already stored at its size on the page (%d x %d)" % (w, h), 5000
            )
            return
        if w > bitmaps.MAX_SIDE or h > bitmaps.MAX_SIDE:
            self.statusBar().showMessage("The bitmap is too large on the page to store at that size", 6000)
            return
        rgba = smooth_resample(primary.rgba, primary.width, primary.height, w, h)
        frgba = None if flash is None else smooth_resample(flash.rgba, flash.width, flash.height, w, h)
        if self.doc.replace_bitmap(i, (l, t, r, b), rgba, w, h, flash_rgba=frgba):
            self._refresh()
            self.statusBar().showMessage(
                "Resampled the bitmap from %d x %d to %d x %d" % (primary.width, primary.height, w, h), 5000
            )

    # --------------------------------------------- paste composite genie
    def _paste_composite_genie(self) -> None:
        """Edit > Paste Composite Genie…: the templates of the page's project
        and the projects it includes with their options; after OK a click on
        the page composes the chosen one with those options and places it
        (editing.paste_composite_genie). Not in a genie or symbol window."""
        if self.doc is None:
            return
        from ...editing import COMPOSITE_NOT_HERE

        if not self.doc.composites_allowed:
            self.statusBar().showMessage(COMPOSITE_NOT_HERE, 6000)
            return
        from ..dialog.composite_dialogs import PasteCompositeGenieDialog

        templates = self._composite_templates()
        if not templates:
            self.statusBar().showMessage(
                "No composite genie templates in this project or the projects it includes.", 6000
            )
            return
        comps = self._composite_completions()
        dlg = PasteCompositeGenieDialog(
            templates,
            {},
            self,
            completions=comps,
            advice=self._composite_advice(comps["Equipment"]),
            project_dir=self._doc_project_dir(),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        guid, params = dlg.selection()
        if guid is None:
            return
        self._arm_composite_placement(guid, templates[guid].name, params)

    def _arm_composite_placement(self, guid: str, name: str = "", params: dict | None = None) -> None:
        """The next click on the page places composite genie ``guid`` there
        with the options ``params`` (its top-left at the click, like Paste
        Genie)."""
        self._set_tool("symbol")
        self._pending_symbol = None
        self._pending_composite = guid
        self._pending_composite_params = dict(params or {})
        self.statusBar().showMessage(
            "Click on the page to place the composite genie %s (Esc cancels)" % (name or guid), 8000
        )

    def _place_composite(self, guid: str, pos, params: dict | None = None) -> None:
        """Compose composite template ``guid`` with the options ``params``
        into the project's composition cache and place it with its top-left
        at ``pos`` (page coordinates)."""
        if params is None:
            params = getattr(self, "_pending_composite_params", None) or {}
        self._pending_composite_params = {}
        # (the composition runs here: the busy note says so once it takes a moment)
        try:
            with self._busy("Composing the composite genie…"):
                idx = self.doc.paste_composite_genie(guid, (int(pos[0]), int(pos[1])), params)
        except Exception as e:  # noqa: BLE001 -- a refusal is explained, never raised
            QtWidgets.QMessageBox.information(self, "Paste Composite Genie", str(e))
            return
        self._after_composite_write()
        self._sel = [idx]
        self._refresh()
        self.statusBar().showMessage(
            "Composite genie placed (composed into the project's composition cache)", 7000
        )

    # ------------------------------------------------- attach super genies
    def _edit_genie_form(self) -> None:
        """Edit > Library > Genie Form…: the form of the genie open in its element window,
        or of the one placed genie selected."""
        doc = self.doc
        if doc is None:
            return
        if isinstance(doc, ElementDocument):
            if getattr(doc, "kind", "") != "genie":
                self.statusBar().showMessage("A genie form belongs to a genie", 5000)
                return
            self._open_genie_form(doc.path, doc.element_name)
            return
        if len(self._sel) != 1:
            self.statusBar().showMessage("Select one placed genie first", 4000)
            return
        root = self.doc.root_of(self._sel[0])
        link = doc.genie_link(root)
        path = doc.library_path(link[0], link[1], "ctm") if link is not None else None
        if not path:
            self.statusBar().showMessage("The genie's library was not found", 6000)
            return
        self._open_genie_form(
            path,
            link[1],
            project_dir=os.path.dirname(os.path.abspath(doc.path)),
            list_values=doc.form_list_values,
        )

    def _open_genie_form(self, lib_path: str, element: str, project_dir: str = "", list_values=None) -> None:
        """The genie form editor of ``element`` in the library at ``lib_path`` (one window per
        genie: an open one comes to the front)."""
        from ..dialog.genie_form_editor import GenieFormEditor
        from ...format import genie_place as GP

        key = (os.path.normcase(os.path.abspath(lib_path)), element.lower())
        wins = self.__dict__.setdefault("_genie_forms", {})
        w = wins.get(key)
        if w is not None:
            try:
                w.showNormal()
                w.raise_()
                w.activateWindow()
                return
            except RuntimeError:  # (closed and deleted)
                wins.pop(key, None)
        try:
            params = GP.declared_params_of(lib_path, element)
        except Exception:  # noqa: BLE001 -- a genie that does not load: no parameter list
            from ...infra import logsetup

            logsetup.note_exception("window._open_genie_form/params", page=lib_path)
            params = {}
        library = os.path.splitext(os.path.basename(lib_path))[0]
        if list_values is None:
            from ...io import project as PR

            pdir = project_dir or os.path.dirname(os.path.abspath(lib_path))

            def list_values(fld, d=pdir):
                if fld is None or not getattr(fld, "list_form", ""):
                    return None
                try:
                    return PR.list_values(d, fld.list_form, fld.list_field, fld.list_filter)
                except Exception:  # noqa: BLE001  # swallow-ok: a plain text box then
                    return None

        w = GenieFormEditor(
            lib_path, library, element, params, project_dir=project_dir, list_values=list_values, parent=self
        )
        w.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        w.destroyed.connect(lambda _o=None, k=key: wins.pop(k, None))
        w.saved.connect(lambda p: self.statusBar().showMessage("Saved the genie form in %s" % p, 6000))
        wins[key] = w
        w.show()

    def _attach_super_genies(self) -> None:
        """Edit > Attach Super Genies…: CtDraw offers it with the GENIE open in
        its element window (the list is saved with the genie); with a placed
        genie selected on a page, ctgedit opens its element, sets the list
        and saves the element in one go (after asking)."""
        if self.doc is None:
            return
        from ..dialog.supergenie_dialogs import AttachSuperGeniesDialog

        doc = self.doc
        in_element = isinstance(doc, ElementDocument)
        if in_element:
            if not doc.can_attach_super_genies:
                self.statusBar().showMessage("Super genies can only be attached to a genie", 5000)
                return
            attachments = doc.attachments()
            genie = doc.display_name
            idx = None
        else:
            if len(self._sel) != 1:
                self.statusBar().showMessage("Select one placed genie first", 4000)
                return
            idx = self._sel[0]
            attachments = doc.genie_attachments(idx)
            if attachments is None:
                is_super = False
                with contextlib.suppress(Exception):
                    from ...format.symbols import reference_from_object

                    ref = reference_from_object(doc.parsed.page.objects[idx])
                    is_super = ref is not None and "@super" in ref.element.lower()
                self.statusBar().showMessage(
                    "A super genie cannot have super genies attached. Select a genie."
                    if is_super
                    else "The selection is not a genie from a library.",
                    5000,
                )
                return
            target = doc._genie_template_target(idx)
            genie = "%s.%s" % (os.path.splitext(os.path.basename(target[0]))[0], target[1]) if target else ""
        dlg = AttachSuperGeniesDialog(
            attachments,
            self,
            genie=genie,
            project_dir=os.path.dirname(os.path.abspath(doc.path)),
            problem=doc.super_genie_problem,
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        new = dlg.names()
        if new == attachments:
            return
        try:
            if in_element:
                done = doc.set_attachments(new)
                if done:
                    self._update_title()
                    self.statusBar().showMessage(
                        "Attached super genies changed. Save the genie to write them into %s."
                        % os.path.basename(doc.path),
                        6000,
                    )
                return
            from ..messages import confirm

            if not confirm(
                self,
                "Attach Super Genies",
                "Save the new list into the genie %s now?\n\nThe genie's library is "
                "rewritten (like saving the genie in its own window): every page that "
                "places it opens the new list." % genie,
                "Save to Genie",
                safe_default=False,
            ):
                return
            if doc.set_genie_attachments(idx, new, page_thumbnail=self._page_thumbnail_for):
                self._after_library_write(target[0] if target else doc.path)
                self._library_cache_notice(getattr(doc, "save_warning", ""))
                self.statusBar().showMessage(
                    "Attached super genies of %s saved (%d)" % (genie, len(new)), 6000
                )
                self._report_super_pages(getattr(doc, "super_pages", None) or [])
        except (ValueError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, "Attach Super Genies", str(e))

    # ------------------------------------------------------ copy to library
    def _copy_to_library(self) -> None:
        """Edit > Copy to Library...: selection -> named library element."""
        if self.doc is None or not self._sel:
            self.statusBar().showMessage("Select the objects to copy first", 4000)
            return
        from ..dialog.library_dialogs import CopyToLibraryDialog

        dlg = CopyToLibraryDialog(
            os.path.dirname(os.path.abspath(self.doc.path)),
            self,
            exts=("ctl", "ctm"),
            **self._library_dialog_hooks(),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        lib, elem = dlg.values()
        if not lib or not elem:
            return
        if not self._confirm_shipped_template(self.doc, lib, False):
            return
        thumb = b""
        with contextlib.suppress(Exception):
            sel = self.doc.expand_to_subtrees(self._sel)
            box = self.doc.selection_extent(sel, bounds=True)
            if box is not None:
                w, h = box[2] - box[0], box[3] - box[1]
                s = min(200.0 / w, 200.0 / h, 1.0)
                size = (
                    (w, h)
                    if s >= 1.0
                    else ((200, max(1, int(h * s))) if w >= h else (max(1, int(w * s)), 200))
                )
                thumb = self._render_region((box[0], box[1], w, h), size, set(sel))
        # (a copy the new element brings up to date is drawn from the element as stored)
        from ...render.headless_thumb import element_thumbnail

        try:
            ok = self.doc.copy_to_library(
                self._sel, lib, elem, thumbnail=thumb, thumbnail_for=element_thumbnail
            )
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Copy to Library", str(e))
            return
        if ok:
            self.statusBar().showMessage("Copied selection to %s.%s" % (os.path.basename(lib), elem), 5000)
            self._after_library_write(lib)
            self._library_cache_notice(getattr(self.doc, "library_warning", ""))

    # ------------------------------------------ special objects (trend, web, ActiveX)
    #: the size a CLICK (no drag) with a special-object tool places
    _ACTIVEX_SIZES = {
        "activex": (160, 160),
        "process_analyst": (570, 320),
        "scheduler": (420, 300),
        "web_content": (480, 360),
        "trend": (301, 151),
        "db_exchange": (400, 250),
        "pelco": (320, 240),
    }

    def _place_special(self, tool: str, rect, click: bool = False) -> "int | None":
        """Place a special object, then open its sheet."""
        if self.doc is None:
            return None
        l, t = min(rect[0], rect[2]), min(rect[1], rect[3])
        r, b = max(rect[0], rect[2]), max(rect[1], rect[3])
        donor = None
        kind = tool
        if tool == "activex":
            choice = self._pending_control or ("kind", "activex")
            if choice[0] == "donor":
                donor = choice[1]
            else:
                kind = choice[1]
        if click:
            if donor is not None and donor.size[0] > 1 and donor.size[1] > 1:
                w, h = donor.size
            else:
                w, h = self._ACTIVEX_SIZES.get(kind, (200, 160))
            r, b = l + w, t + h
        if tool == "trend":
            # CtDraw's Trend tool: the box minus its last pixel --
            # a drag N pixels wide = N samples at one pixel per sample
            idx = self.doc.create_object("trend", (l, t, max(l + 1, r - 1), max(t + 1, b - 1)))
        elif tool == "cicode_object":
            # the click is the object's point; its f(x) icon sits above-right
            # of it (the point = the icon's bottom-left)
            from ...format.legacy import RECT2_DELTAS

            d = RECT2_DELTAS[0x1009]
            idx = self.doc.create_object("cicode_object", (l + d[0], t + d[1], l + d[2], t + d[3]))
        elif donor is not None:
            idx = self.doc.create_control(donor.path, donor.index, (l, t, r, b), donor.clsid)
        else:
            idx = self.doc.create_object(kind, (l, t, r, b))
        self._sel = [idx]
        self._refresh()
        # CtDraw opens the new object's sheet (not only with 'Open properties
        # after drawing'), to be filled in: it takes the keyboard
        QtCore.QTimer.singleShot(0, self, lambda i=idx: self.object_properties(i))
        return idx

    def _insert_activex_dialog(self) -> None:
        """Edit > Insert ActiveX Control: pick the control (the ones ctgedit
        carries + every control the project's pages use), then drag its box."""
        if self.doc is None:
            return
        from ..dialog.activex_insert import InsertActiveXDialog

        pdir = os.path.dirname(os.path.abspath(self.doc.path)) if self.doc.path else None
        dlg = InsertActiveXDialog(pdir, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted or dlg.choice() is None:
            return
        self._set_tool("activex")
        self._pending_control = dlg.choice()
        self.statusBar().showMessage(
            "Drag the box of the %s on the page (a click places it at its default size)" % dlg.choice_label(),
            8000,
        )

    # -------------------------------------------------------- legacy tools
    _LEGACY_SIZES = {
        "trend": (480, 280),
        "string": (140, 22),
        "cicode_object": (48, 48),
        "animation_keyboard": (32, 32),
        "pagewide_keyboard": (32, 32),
        "animated_symbol": (64, 64),
        "bar_graph": (40, 160),
        "slider": (40, 160),
        "digital_text": (140, 22),
        "color_flood": (80, 80),
    }

    def _create_legacy(self, kind: str) -> None:
        """Tools > V3/V4 Objects: create a legacy record at the page centre."""
        if self.doc is None:
            return
        w, h = self._LEGACY_SIZES.get(kind, (64, 64))
        pw = self.doc.page.appearance.width or 1024
        ph = self.doc.page.appearance.height or 768
        x, y = max(0, (pw - w) // 2), max(0, (ph - h) // 2)
        try:
            idx = self.doc.create_object(kind, (x, y, x + w, y + h), None)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Create Failed", str(e))
            return
        self._sel = [idx]
        self._refresh()
        if kind == "animated_symbol":
            # its states are the point of the object: pick them right away
            QtCore.QTimer.singleShot(0, self, lambda i=idx: self.symbol_set_properties(i))
            return
        self.statusBar().showMessage("Created %s. Open its properties to set it up." % kind, 6000)

    # ------------------------------------------------------- pack libraries
    @staticmethod
    def _open_tab_marks() -> dict:
        """{id(page document): (the document, how many automatic changes it had)} of every
        page open in a tab: taken when Update Pages or Update Users starts, so its step for
        the open pages can tell an automatic update that landed during the run
        (_update_open_documents)."""
        out = {}
        for w in _mw().MainWindow._instances:
            for e in w._docs:
                d = e["doc"]
                if _page_tab(d):
                    out[id(d)] = (d, ui_state(d).auto_count)
        return out

    def _open_documents_in(self, dirs, only=None) -> list:
        """The pages open in tabs that Update Pages' open-tabs step handles."""
        want = {os.path.normcase(os.path.abspath(d)) for d in dirs}
        only_n = None if only is None else {os.path.normcase(os.path.abspath(p)) for p in only}
        out = []
        for w in _mw().MainWindow._instances:
            for e in w._docs:
                d = e["doc"]
                if not _page_tab(d) or os.path.normcase(os.path.dirname(os.path.abspath(d.path))) not in want:
                    continue
                if only_n is not None and os.path.normcase(os.path.abspath(d.path)) not in only_n:
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
            steps = _history_depth(d)
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
            if _history_depth(d) != steps:
                if not failed:
                    # (changed by the open's own steps alone: the automatic update)
                    (res.updated if _history_depth(d) - steps > len(found.owed) else res.auto).append(d.path)
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
                # _set_current; a run without genies leaves that look to the tab)
                e["genie_gen"] = editing_mod._LIBRARY_GENERATION[0]
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
        found = _OpenPageFound()

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
            from .files import PageFiles

            PageFiles._remember_symbol_versions(d, stale)
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
            depth = _history_depth(d)
            try:
                n = step() or 0
            except Exception:  # noqa: BLE001 -- noted; the page is reported, the rest run
                logsetup.note_exception("ui.window.library.update_open_documents/" + count, page=d.path)
                failed.append(what)
                continue
            if mine:
                setattr(res, count, getattr(res, count) + int(n))
            elif _history_depth(d) != depth:
                found.owed.append(
                    (_open_step_text(d, count, found), d._undo[-1] if getattr(d, "_undo", None) else None)
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
                if app_settings().value("opt/auto_update_genies", True, type=bool):
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
        hold = _TabsHold("Update Pages")
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
            QtWidgets.QMessageBox.information(self, "Update Pages", "Update Pages is already running.")
            return True
        busy = UpdatePagesRun.busy_text("Run Update Pages")
        if busy:
            QtWidgets.QMessageBox.information(self, "Update Pages", busy)
            return True
        return False

    def _update_pages_run(self, hold=None, chain=None) -> None:
        """The run of :meth:`_update_pages`; ``hold`` = its hold on the other runs until its
        open tabs have followed (:class:`_TabsHold`; one of its own when None), ``chain`` =
        the chain box's state for this run (None: as last time)."""
        from ..jobs.run_stats import RunStats
        from ..jobs.update_run import UpdatePagesRun

        hold = hold if hold is not None else _TabsHold("Update Pages")
        if self._update_pages_refused():
            return
        from ...io import pgregister, updatepages
        from ..dialog import dialogs

        # an open page's project; editing a template / genie, the project of its library
        # (ElementDocument.path = the library file) is only a hint: the pages that use it
        # are in the projects that include it
        start_dir = library_dir = None
        if (
            self.doc is not None
            and not isinstance(self.doc, _PendingPage)
            and getattr(self.doc, "path", None)
        ):
            d = os.path.dirname(os.path.abspath(self.doc.path))
            if isinstance(self.doc, ElementDocument):
                library_dir = d
            else:
                start_dir = d
        dlg = dialogs.UpdatePagesDialog(
            start_dir, self, library_dir=library_dir, **({} if chain is None else {"chain": chain})
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        opts = dlg.options()
        dirs = list(dlg.projects())
        if not dirs:
            return
        # (the whole run's time, for the result box: the chain and the pages)
        clock = RunStats.from_settings()
        # a project database another program holds open: its pages cannot be saved now
        busy = []
        for d in dirs:
            try:
                pgregister.probe_writable(d)
            except pgregister.ProjectDatabaseInUse:
                busy.append(d)
        if busy:
            names = ", ".join(os.path.basename(os.path.normpath(d)) for d in busy)
            if len(busy) == len(dirs):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Update Pages",
                    "The project database of %s is in use (is Plant SCADA Studio open?). "
                    "Its pages cannot be updated now." % names,
                )
                return
            from ..messages import confirm

            if not confirm(
                self,
                "Update Pages",
                "The project database of %s is in use (is Plant SCADA Studio open?). Their "
                "pages cannot be updated now. Update the other projects?" % names,
                "Update the Others",
                safe_default=False,
            ):
                return
            run_dirs = [d for d in dirs if d not in busy]
        else:
            run_dirs = dirs
        backup = updatepages.backup_dir() if opts["backup"] else None
        # (the open pages' automatic changes so far: one that lands during the run is told)
        marks = self._open_tab_marks()
        # a page open in the Graphics Builder is skipped: the Builder keeps its
        # own copy and its next save would overwrite the update
        try:
            from ...backend.graphics_builder import open_pages

            in_builder = [list(x) for x in open_pages()]
        except Exception:  # noqa: BLE001
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
        # 'Save the library genies in the chain first': the genies and templates of the
        # projects' libraries that place a changed genie are saved again here first, as
        # Update Users saves them; the helpers then read the new stamps from disk. The
        # libraries are those of the ticked projects and of the tree's projects with no
        # pages (library projects: nothing to tick), never of a busy one. Meanwhile a second
        # Update Pages and Pack Libraries wait (the progress window processes events)
        chain = None
        if opts.get("chain"):
            chain_dirs = [d for d in dlg.chain_projects() if d not in busy]
            UpdatePagesRun.chain_busy = True
            try:
                chain = self._update_pages_chain(
                    chain_dirs, backup, compose_all=bool(opts.get("chain_compose_all"))
                )
            finally:
                UpdatePagesRun.chain_busy = False
            if chain is None:
                return  # (the chain could not be read: said)
            if chain.stopped:
                # (what it saved before the stop: the open tabs that nest it follow, and so
                # do the Used By windows)
                self._follow_run_saves(chain.elements_saved + chain.elements_in_editor, hold)
                QtWidgets.QMessageBox.information(
                    self,
                    "Update Pages",
                    "\n".join(
                        self._chain_summary(chain, backup)
                        + [x for x in (clock.total_text(),) if x]
                        + ["Stopped before the pages were updated."]
                    ),
                )
                self._follow_used_by_windows()
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
            if chain is not None:
                self._follow_run_saves(chain.elements_saved + chain.elements_in_editor, hold)
            hold.release(self)
            lines = self._chain_summary(chain, backup) if chain is not None else []
            if lines:
                # (the run could not start, said; what the chain saved is told still)
                QtWidgets.QMessageBox.information(
                    self, "Update Pages", "\n".join(lines + ["The pages were not updated."])
                )
            if chain is not None:
                self._follow_used_by_windows()
            return
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
                tabs.left_follow = _follow_words(
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
        # the pages left because they were open in an editor (when the run started, or
        # opened during it) and closed before it ended: nothing updated them
        now = {os.path.normcase(os.path.abspath(p)) for p in self._open_page_paths()}
        left = list(getattr(rep, "open_at_start", ()) or ()) + [
            r.get("path") for r in rep.records if r.get("outcome") == "skipped_open" and r.get("path")
        ]
        # (a page saved before it was closed, current now, is not named: the same rule as
        # the chain after a library save, _closed_meanwhile. Named as the summary's other
        # lines name a page, with its project in a run over several)
        label = _run_page_label(len(_unique_names(list(rep.projects) + tabs.projects())) > 1)
        rep.closed_meanwhile = sorted(
            {
                label(p)
                for p in left
                if os.path.normcase(os.path.abspath(p)) not in now and updatepages.page_needs_update(p)
            },
            key=str.lower,
        )
        if getattr(rep, "composites", 0) or tabs.composites:
            from ...editing import libraries_changed

            libraries_changed()
        # every window shows its page as it is now (else a page updated in a tab of another
        # window keeps the old scene, its rows no longer the document's), as Update Users does
        for w in _mw().MainWindow._instances:
            with contextlib.suppress(RuntimeError):
                w._refresh()
        lines = self._update_pages_summary(rep, backup, tabs, chain=chain)
        if clock.total_text():
            lines.append(clock.total_text())
        from ...infra import backups, logsetup

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
            if t.startswith(LibraryCommands._WARN_LINES) or any(
                part in t for part in LibraryCommands._WARN_PARTS
            ):
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
                % (LibraryCommands._and_list(LibraryCommands._saved_kinds_parts(saved, kinds)), _names(saved))
            )
        if chain.elements_current:
            n = len(chain.elements_current)
            lines.append(
                "%s already matched what %s: %s."
                % (
                    LibraryCommands._and_list(
                        LibraryCommands._saved_kinds_parts(chain.elements_current, kinds)
                    ),
                    "it places" if n == 1 else "they place",
                    _names(chain.elements_current),
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
                % _names(_short_path(f) + (" (%s)" % el if el else "") for f, el in unreadable)
            )
        # (compose-all: a copy that could not be composed keeps a current stamp, so only
        # this line tells it; a genie whose comparison failed was not checked)
        left = list(getattr(chain, "not_composed", None) or ())
        if left:
            lines.append(LibraryCommands._not_composed_text(left))
        not_compared = list(getattr(chain, "not_compared", None) or ())
        if not_compared:
            lines.append(
                "Not compared with their libraries, because of an error (the log "
                "says why): %s." % _names(x.split(": ", 1)[0] for x in not_compared)
            )
        if shipped:
            lines.append(
                "%s in libraries supplied with Plant SCADA %s left alone: %s."
                % (
                    LibraryCommands._and_list(
                        LibraryCommands._saved_kinds_parts(shipped, kinds, LibraryCommands._KIND_WORDS_BARE)
                    ),
                    "was" if len(shipped) == 1 else "were",
                    _names(shipped),
                )
            )
        lines += ["Could not save %s" % x for x in chain.failed[:10]]
        if chain.super_pages:
            lines.append("Super genie pages created: %s." % _names(chain.super_pages))
        if backup and saved:
            lines.append(
                "Backups of the changed libraries: %s (Tools > Restore Backup puts them back)" % backup
            )
        paths = [f for f, _el in unreadable]
        return [_stop(_shorten(x, paths)) for x in lines]

    @staticmethod
    def _update_pages_summary(rep, backup, tabs=None, chain=None) -> list:
        """The lines of Update Pages' result."""
        from ..names import plural

        names = _names
        if not isinstance(tabs, OpenTabsUpdate):
            tabs = OpenTabsUpdate()  # (none open)

        def its(n):
            return "its" if n == 1 else "their"

        lines = LibraryCommands._chain_summary(chain) if chain is not None else []
        chain_saved = chain is not None and bool(chain.elements_saved or chain.elements_in_editor)
        # (one project spelled two ways, its tab opened from a typed path: one project)
        projects = _unique_names(list(rep.projects) + tabs.projects())
        label = _run_page_label(len(projects) > 1)
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
        lines = [_stop(_shorten(x, paths)) for x in lines]

        def block(title, items, most):
            return "\n%s:\n%s" % (title, "\n".join(_stop(_shorten(x, paths)) for x in items[:most]))

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
        from ..progress_text import SteadyProgressLabel
        from ..jobs.run_stats import RunStats

        stats = RunStats.from_settings()
        prog = QtWidgets.QProgressDialog(
            "Finding the library genies to save…", "Stop After This Page", 0, 0, self
        )
        prog.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # (gone once closed)
        prog.setWindowTitle("Update Pages")
        prog.setWindowModality(QtCore.Qt.WindowModal)
        prog.setMinimumDuration(0)
        steady = SteadyProgressLabel(prog, stats)
        steady.set(["Finding the library genies to save…"])

        def ticker(text, phase):
            def tick(i, n, label):
                prog.setMaximum(max(1, int(n)))
                prog.setValue(min(int(i), max(1, int(n))))
                stats.begin_phase(phase, int(n))
                stats.update(int(i))
                steady.set([text % (label, int(i) + 1, int(n))] + stats.lines())
                QtWidgets.QApplication.processEvents()
                return not prog.wasCanceled()

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
                QtWidgets.QApplication.processEvents()
                if prog.wasCanceled():
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
                for w in _mw().MainWindow._instances:
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
        from ..progress_text import SteadyProgressLabel
        from ..jobs.update_run import UpdatePagesRun

        run = UpdatePagesRun(
            self, list(job.get("dirs") or ()), job, skip_paths=self._open_page_paths, parts=parts
        )
        # (a stop keeps the pages already updated: the button says so; Cancel, as in the
        # window before the run, read as 'nothing changes')
        prog = QtWidgets.QProgressDialog("Finding the pages to check…", "Stop After This Page", 0, 0, self)
        prog.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # (gone once closed)
        prog.setWindowTitle("Update Pages")
        prog.setWindowModality(QtCore.Qt.WindowModal)
        prog.setMinimumDuration(0)
        # (sized once for the widest figures: the window never jumps while the run goes on)
        steady = SteadyProgressLabel(prog, run.stats)
        steady.set(["Finding the pages to check…"])
        loop = QtCore.QEventLoop(self)
        state = {"res": None, "stopping": False}
        from ..jobs import taskbar, tasks

        def cancel():
            if run.done:
                return
            state["stopping"] = True
            run.stop()
            steady.set(["Stopping after the pages being updated…"])
            tasks.progress(task, 0, 0, "Stopping Update Pages…")

        def on_progress(i, n, name, phase):
            prog.setMaximum(max(1, int(n)))
            prog.setValue(min(int(i), max(1, int(n))))
            if state["stopping"]:
                return  # (no estimate while it stops: cancel() said so)
            # (the step named: its estimate is of that step; the composite pass after the
            # check has its own)
            tasks.progress(task, int(i), int(n), "Update Pages: %s…" % run.stats.step_text())
            head = (
                "Checking %s… (%d of %d)" if phase == "check" else "Updating composite genies: %s… (%d of %d)"
            ) % (name, int(i) + 1, int(n))
            steady.set([head] + run.stats.lines())

        def on_finished(res):
            state["res"] = res
            loop.quit()

        run.progress.connect(on_progress)
        run.finished.connect(on_finished)
        prog.canceled.connect(cancel)
        # (a background task in the status bar, the progress on the taskbar button)
        task = tasks.begin("update-pages", "Updating pages…", cancel=cancel, window=self)
        bar = taskbar.ProgressReporter(prog, self)
        if not run.start():
            bar.finish()
            prog.close()
            tasks.end(task, ok=False, tell=False)
            if UpdatePagesRun.active is not None:
                QtWidgets.QMessageBox.warning(self, "Update Pages", "Update Pages is already running.")
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
            try:
                from ...backend.graphics_builder import _ctdraw_pids

                if _ctdraw_pids():
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Restore Backup",
                        "The Graphics Builder is running: it would write its own copies of "
                        "the library elements back. Close Plant SCADA Studio and the "
                        "Graphics Builder, then restore again.",
                    )
                    return
            except Exception:  # noqa: BLE001
                pass
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

            if any(e.get("library") for e in b.entries):
                # an Update Users backup also holds library files
                try:
                    from ...backend.graphics_builder import _ctdraw_pids

                    if _ctdraw_pids():
                        QtWidgets.QMessageBox.warning(
                            self,
                            "Restore Backup",
                            "The Graphics Builder is running: it would write its own copies "
                            "of the library elements back. Close Plant SCADA Studio and the "
                            "Graphics Builder, then restore again.",
                        )
                        return
                except Exception:  # noqa: BLE001
                    pass
            names = [e.get("page", "") for e in b.entries if e.get("page")]
            # tabs showing those pages: their Save would write over the restore
            keys = {
                os.path.normcase(os.path.join(e.get("project", ""), e.get("page", "") + ".ctg"))
                for e in b.entries
            }
            open_tabs = [
                (w, x)
                for w in _mw().MainWindow._instances
                for x in w._docs
                if not isinstance(x["doc"], ElementDocument)
                and os.path.normcase(os.path.abspath(x["doc"].path)) in keys
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
            % (plural(len(done), "item"), when_text(b.created), _names(done) or "none")
        ]
        if b.kind != "pack" and done:
            lines.append("Compile the project to see them in the runtime.")
        if failed:
            lines.append("")
            lines.append("Not restored:")
            lines += [_stop(f) for f in failed[:20]]
            if len(failed) > 20:
                lines.append("%d more were not restored. The log lists them." % (len(failed) - 20))
        return lines, bool(b.kind != "pack" and done)

    def _update_template_here(self) -> None:
        """Re-apply the page's (changed) template to this page only. The template is named
        as its library spells it."""
        from ..names import template_name

        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        state, link = self.doc.template_status()
        link = template_name(link)
        if state == "none":
            self.statusBar().showMessage("This page has no template.", 5000)
            return
        if state == "unresolved":
            QtWidgets.QMessageBox.warning(
                self, "Update Template", "The template %s is not on this project's include path." % link
            )
            return
        n = self.doc.update_template()
        self._refresh()
        self._update_actions()
        from ..names import plural
        from ..notify import notify, undo_action

        if n:
            # (told over the page too, with Undo)
            notify(
                self,
                "Template %s applied again (%s changed)." % (link, plural(n, "object")),
                "success",
                actions=[undo_action(self)],
                toast=True,
                key="edit:template",
            )
        else:
            notify(self, "The page already matches its template %s." % link, key="edit:template")

    def _page_has_template(self) -> bool:
        """The open PAGE is linked to a template (cached per edit state)."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return False
        key = (id(self.doc), len(self.doc._undo), len(self.doc._redo), len(self.doc.parsed.page.objects))
        if getattr(self, "_has_tpl_key", None) != key:
            from ...format import template_place as TP

            try:
                self._has_tpl = bool(TP.template_roots(self.doc.parsed.page.objects))
            except Exception:  # noqa: BLE001
                self._has_tpl = False
            self._has_tpl_key = key
        return self._has_tpl

    def _edit_page_template(self) -> None:
        """Tools > Edit Page Template: open the page's template (.ctt
        element) in its own tab. Saving it there offers Update Pages."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        el = self.doc.template_element()
        if el is None:
            _i, ref = self.doc.template_reference()
            if ref is None:
                self.statusBar().showMessage("This page has no template.", 5000)
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Edit Page Template",
                    "The template %s.%s is not on this project's include path, so it "
                    "cannot be opened." % (ref.library, ref.element),
                )
            return
        self._open_element(*el)

    def _cut_template_link(self) -> None:
        """Edit > Library > Cut Link to Template: the page's template objects
        become ordinary objects of the page, which no longer follows the
        template. Asks first -- the link cannot be restored (only undone)."""
        if self.doc is None or isinstance(self.doc, ElementDocument):
            return
        state, link = self.doc.template_status()
        if state == "none":
            self.statusBar().showMessage("This page is not linked to a template.", 5000)
            return
        if state == "unresolved":
            from ..names import template_name

            QtWidgets.QMessageBox.warning(
                self,
                "Cut Link to Template",
                "The template %s is not on this project's include path, so its objects "
                "cannot be copied into the page. Include the template's project first." % template_name(link),
            )
            return
        from ..dialog.genie_dialogs import _friendly_ref

        from ..messages import confirm

        if not confirm(
            self,
            "Cut Link to Template",
            "Cut the link between this page and its template %s?\n\nThe template's "
            "objects become ordinary objects of this page that you can edit; the page "
            "no longer follows changes to the template. The link cannot be restored "
            "later (Undo can, until the page is closed)." % _friendly_ref(link),
            "Cut Link",
        ):
            return
        try:
            n = self.doc.cut_template_link()
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Cut Link to Template", str(e))
            return
        self._sel = []
        self._refresh()
        self._update_actions()
        self.statusBar().showMessage(
            "The page is no longer linked to %s: %d template object(s) are now page "
            "objects" % (_friendly_ref(link), n),
            8000,
        )

    def _page_defaults_dialog(self) -> None:
        """File > Page Defaults: the template style / resolution / title bar
        and background new pages of the current project start with (stored
        in the project, as CtDraw does)."""
        from ..dialog.dialogs import PageDefaultsDialog

        pdir = self._current_project_dir()
        dlg = PageDefaultsDialog(pdir, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.statusBar().showMessage(
                "New pages of %s now start from the %s style (%s)"
                % (
                    os.path.basename(os.path.normpath(pdir)),
                    dlg.values()["style"],
                    "saved in the project" if dlg.stored_in == "project" else "saved in ctgedit's settings",
                ),
                6000,
            )

    def _update_pages_running(self) -> bool:
        """Update Pages is running (its helpers read the libraries and the composition
        caches): say so; Pack Libraries waits until it has finished."""
        from ..jobs.update_run import UpdatePagesRun

        text = UpdatePagesRun.busy_text("Pack the libraries")
        if not text:
            return False
        QtWidgets.QMessageBox.information(self, "Pack Libraries", text)
        return True

    def _pack_libraries_dialog(self) -> None:
        """Tools > Pack Libraries: the project (the page's, or any with no page open) and the
        included projects, then the pack."""
        if self._update_pages_running():
            return
        from ..dialog.pack_dialog import PackLibrariesDialog

        here = os.path.dirname(os.path.abspath(self.doc.path)) if self.doc is not None else ""
        dlg = PackLibrariesDialog(self, here)
        try:
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            proj, chain = dlg.project_dir(), dlg.include_chain()
        finally:
            dlg.deleteLater()
        if proj:
            self._pack_libraries(chain, ask=False, project_dir=proj)

    def _pack_libraries(
        self, include_chain: bool, ask: bool = True, project_dir: Optional[str] = None
    ) -> None:
        """Tools > Pack Libraries."""
        if project_dir is None and self.doc is None:
            return
        if self._update_pages_running():
            return
        from ...format.projects import PackError, pack_libraries, include_search_dirs
        from ...infra import backups, logsetup

        try:
            from ...backend.graphics_builder import _ctdraw_pids

            builder = bool(_ctdraw_pids())
        except Exception:  # noqa: BLE001
            builder = False
        if builder:
            QtWidgets.QMessageBox.warning(
                self,
                "Pack Libraries",
                "The Graphics Builder is running (Plant SCADA Studio starts one too). It "
                "keeps the library elements it has loaded in memory and writes them back "
                "into the libraries, which would undo the pack or damage it.\n\nClose Plant "
                "SCADA Studio and the Graphics Builder, then run Pack Libraries again.",
            )
            return
        proj = project_dir or os.path.dirname(os.path.abspath(self.doc.path))
        dirs = [proj] + (include_search_dirs(proj) if include_chain else [])
        if ask:
            from ..messages import confirm

            if not confirm(
                self,
                "Pack Libraries",
                "Rewrite the symbol, genie and template libraries of %d project folder%s "
                "without their deleted elements?" % (len(dirs), "" if len(dirs) == 1 else "s"),
                "Pack Libraries",
                safe_default=False,
            ):
                return
        from ...format.projects import pack_composition_caches

        # the pages open here keep their instances: what they link now (unsaved
        # placements too) and what they linked earlier -- an Undo brings it back
        open_links = set()
        for w in _mw().MainWindow._instances:
            for e in w._docs:
                with contextlib.suppress(Exception):
                    open_links |= e["doc"].composite_links()
        unused = {}
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for d in dirs:
                try:
                    found = pack_composition_caches(d, dry_run=True, extra_links=open_links).unused
                except Exception as e:  # noqa: BLE001 -- the caches never stop a pack
                    logsetup.get("pack").warning("composition caches of %s: %s", d, e)
                    continue
                if found:
                    unused[d] = found
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        prune = False
        if unused:
            n = sum(len(v) for v in unused.values())
            per = "\n".join(
                "• %s: %d" % (os.path.basename(os.path.normpath(d)), len(v)) for d, v in unused.items()
            )
            from ..messages import choose

            got = choose(
                self,
                "Pack Libraries",
                "%d composite genie instance%s in the composition caches %s not used by "
                "any page or library element of these projects (or of a project that "
                "includes them):\n%s\n\nDelete them? This cannot be undone in the editor "
                "(their files are kept in the pack backup; Tools > Restore Backup puts them "
                "back)." % (n, "" if n == 1 else "s", "is" if n == 1 else "are", per),
                ["Delete Unused", "Keep Them"],
                "Keep Them",
            )
            if got is None:
                return
            prune = got == "Delete Unused"
        folder = backups.new_folder("pack")
        files = saved = 0
        packed, failures = [], []
        pruned = 0
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for d in dirs:
                res = pack_libraries(d, backup=folder)
                files += res.changed
                saved += res.saved
                packed += res.packed
                failures += res.failures
                if prune and d in unused:
                    try:
                        cres = pack_composition_caches(d, backup=folder, extra_links=open_links)
                        pruned += cres.deleted
                        packed += cres.files
                    except Exception as e:  # noqa: BLE001 -- the caches never stop a pack
                        failures.append((os.path.join(d, "_CompositionCaches"), str(e)))
        except PackError as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            logsetup.get("pack").error("%s", e)
            from ..messages import error_box, open_folder_action

            error_box(
                self,
                "Pack Libraries",
                str(e),
                next_steps=(
                    "Libraries packed in the project folders before this one "
                    "stay packed; their originals are in the backup %s "
                    "(Tools > Restore Backup puts them back)." % folder,
                )
                if packed
                else (),
                actions=(open_folder_action("Open Backup Folder", folder),)
                if packed and os.path.isdir(folder)
                else (),
            )
            return
        finally:
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()
        if packed:
            logsetup.files_written("packed libraries (originals in %s)" % folder, packed)
            from ...editing import libraries_changed

            libraries_changed()
            self._after_library_write(*packed)
        with contextlib.suppress(Exception):
            backups.prune()
        self._show_run_results(
            "Pack Libraries",
            self._pack_result_lines(files, saved, pruned, bool(packed), folder, failures),
            backup=folder if packed else "",
        )

    @staticmethod
    def _pack_result_lines(files: int, saved: int, pruned: int, packed: bool, folder: str, failures) -> list:
        """The lines of Pack Libraries' result (the results window, _show_run_results): the
        counts, the backup folder with Tools > Restore Backup when files were rewritten, the
        files left as they were."""
        from ..names import plural

        lines = ["%s packed, %s reclaimed." % (plural(files, "library file"), plural(saved, "byte"))]
        if pruned:
            lines.append(
                "%s deleted from the composition caches." % plural(pruned, "unused composite genie instance")
            )
        if packed:
            lines.append(
                "Backup of the files rewritten: %s (Tools > Restore Backup puts them back)." % folder
            )
        if failures:
            lines.append("")
            lines.append("These files were left as they were:")
            lines += ["%s: %s" % (os.path.basename(p), _stop(why)) for p, why in failures[:20]]
            if len(failures) > 20:
                lines.append("%d more were left. The log lists them." % (len(failures) - 20))
        return lines

    # --------------------------------------------------------- variable tags
    def _variable_tags(self) -> None:
        """Tools > Tags Panel (Ctrl+T; the Graphics Builder's Variable Tags): show and
        raise the Tags panel (it shows its own loading and empty state)."""
        self.dock_tags.show()
        self.dock_tags.raise_()
        self.tags_dock.focus_search()


def _mw():
    """ctgedit.ui.main_window (MainWindow is defined there, after this module is imported)."""
    from .. import main_window

    return main_window


def _used_by_windows() -> list:
    """The open Used By windows (ElementUsersDialog) of every editor window."""
    from ..dialog.library_dialogs import ElementUsersDialog

    out = []
    for w in _mw().MainWindow._instances:
        try:
            dlgs = list(getattr(w, "_dialogs", {}).values())
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
        out += [d for d in dlgs if isinstance(d, ElementUsersDialog)]
    return out


def _page_tab(d) -> bool:
    """Is ``d`` a page tab a run may update in place?"""
    from ..dialog.history_versions import is_version

    return (
        not isinstance(d, (ElementDocument, _PendingPage))
        and not is_version(d)
        and not getattr(d, "read_only", False)
    )


def _element_tab(d) -> bool:
    """``d`` is a library element's own tab: an element of its library (not a new one), not
    an earlier version of it shown read-only."""
    from ..dialog.history_versions import is_version

    return isinstance(d, ElementDocument) and not getattr(d, "is_new", False) and not is_version(d)


def _refresh_stale_used_by() -> None:
    """Refresh stale Used By windows once no run is writing."""
    from ..jobs.update_run import UpdatePagesRun as R

    if (
        R.users_busy
        or R.active is not None
        or R.chain_busy
        or R.tabs_following
        or LibraryCommands._USERS_READS[0]
        or QtWidgets.QApplication.activeModalWidget() is not None
    ):
        QtCore.QTimer.singleShot(500, _refresh_stale_used_by)
        return
    LibraryCommands._USED_BY_DUE[0] = False
    for d in _used_by_windows():
        try:
            if getattr(d, "stale", False) and d.isVisible():
                d.refresh()
        except RuntimeError:  # swallow-ok: a window closed meanwhile
            continue


def _run_page_label(multi: bool):
    """``label(path)``: a page's name in Update Pages' summary, with its project when the
    run went over several projects (``multi``), as the helpers name the pages
    (genie_worker's merged answer)."""

    def label(path) -> str:
        name = os.path.splitext(os.path.basename(path))[0]
        if multi:
            return "%s (%s)" % (name, os.path.basename(os.path.dirname(os.path.abspath(path))))
        return name

    return label


def _page_label(paths):
    """``label(path)``: a page's name, with its project when another of ``paths`` has the
    same name in another project ('!fx_detail (FxMain)'; updatepages.same_name_label)."""
    from ...io.updatepages import same_name_label

    rec_label = same_name_label(list(paths or ()))

    def label(path) -> str:
        a = os.path.abspath(str(path))
        return rec_label(
            {
                "name": os.path.splitext(os.path.basename(a))[0],
                "project": os.path.basename(os.path.dirname(a)),
            }
        )

    return label


def _follow_words(saved=(), kinds_of=None) -> str:
    """What the automatic update brings up to date after a run."""
    st = app_settings()
    kinds = {str((kinds_of or {}).get(str(s), "genie")) for s in saved or ()}
    words = []
    if "template" in kinds and st.value("opt/auto_update_template", True, type=bool):
        words.append("templates")
    if "super genie" in kinds:
        words.append("super genies")
    words += [
        w
        for w, key in (("genies", "opt/auto_update_genies"), ("symbols", "opt/auto_update_symbols"))
        if st.value(key, True, type=bool)
    ]
    return LibraryCommands._and_list(words)


def _page_mtimes(paths) -> Dict[str, Optional[int]]:
    """{path: its file's modification time (ns), None when it cannot be read} (the pages
    a run left because they were open: one whose time is the same at the end was not
    saved since)."""
    out: Dict[str, Optional[int]] = {}
    for p in paths:
        if not p:
            continue
        try:
            out[p] = os.stat(p).st_mtime_ns
        except OSError:  # swallow-ok: a page with no file (deleted): no time to compare
            out[p] = None
    return out


def _names(xs, most: int = 10) -> str:
    """The first ``most`` names, then 'and N more' when there are more (the result boxes;
    the log lists them all)."""
    xs = list(xs)
    if len(xs) <= most:
        return ", ".join(xs)
    return "%s and %d more" % (", ".join(xs[:most]), len(xs) - most)


def _unique_names(xs) -> List[str]:
    """``xs`` (project names) each once, in order, told apart without case (a folder name
    spelled two ways is one project on Windows)."""
    out: List[str] = []
    seen = set()
    for x in xs:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _history_depth(d) -> int:
    """How many steps the page ``d`` is from its open state: its undo steps with those the
    undo cap dropped (EditableDocument.history_depth). A step pushed while the cap drops an
    old one leaves the undo stack as long as it was, but this count still grows."""
    return len(getattr(d, "_undo", None) or ()) + int(getattr(d, "undo_trimmed", 0) or 0)


#: the scopes whose usage is being read on a worker thread (:func:`warm_element_users`)
_USERS_WARMING: dict = {}


def warm_element_users(lib_path: str) -> None:
    """Read what uses the elements of ``lib_path`` on a worker thread."""
    if not lib_path:
        return
    key = os.path.normcase(os.path.dirname(os.path.abspath(lib_path)))
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


def _open_step_text(d, count: str, found) -> str:
    """What an open's automatic step that Update Pages did in its place changed on the page
    ``d`` (LibraryCommands._update_open_document's ``owed``), said as the open says it in
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


def _write_text(path: str, text: str) -> None:
    """``text`` into ``path`` as one step (a .tmp file, then replaced): a helper process
    reading it meanwhile gets the old list or the new one, never an empty one. OSError
    when it could not (the file in use: the caller tries again at its next tick)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _with_figures(text: str, figures: str) -> str:
    """The chain's status bar note with its figures: 'Updating ...: 3 of 40 (Page). 12 s so
    far, about 2 min left.' (``text`` alone without figures)."""
    if not figures:
        return text
    return "%s. %s%s." % (text, figures[:1].upper(), figures[1:])


@dataclass
class _OpenPageFound:
    """What the checks of the step for one open page found out of date
    (LibraryCommands._update_open_document): the genie_changes, whether the template and
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


class _TabsHold:
    """A run's hold on the others until its open tabs have followed it."""

    def __init__(self, run: str = ""):
        self.run = run
        self.held = False

    def take(self) -> None:
        from ..jobs.update_run import UpdatePagesRun

        UpdatePagesRun.tabs_following = True
        UpdatePagesRun.tabs_run = self.run
        self.held = True

    def release(self, win) -> None:
        from ..jobs.update_run import UpdatePagesRun

        if not self.held:
            return
        self.held = False
        UpdatePagesRun.tabs_following = False
        UpdatePagesRun.tabs_run = ""
        if UpdatePagesRun.users_queue:
            QtCore.QTimer.singleShot(0, win, win._next_users_run)


def _stop(text: str) -> str:
    """``text`` ending in one full stop (a line of a result box)."""
    t = str(text).rstrip()
    while t.endswith((".", "\u2026")):
        t = t[:-1].rstrip()
    return t + "."


def _short_path(path: str) -> str:
    """'<project>\\<file>' of a project file: what a result box names (the log keeps the
    full path)."""
    p = os.path.normpath(os.path.abspath(path))
    return os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p))


def _shorten(text: str, paths) -> str:
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


@dataclass
class OpenTabsUpdate:
    """What Update Pages' (and Update Users') step for the pages open in tabs did
    (LibraryCommands._update_open_documents): the page files by outcome, and the counts."""

    updated: List[str] = field(default_factory=list)  # updated in their tab by the step
    #: updated in their tab and saved there: nothing on them was the user's (only the
    #: automatic update on open and this run had changed them)
    saved: List[str] = field(default_factory=list)
    auto: List[str] = field(default_factory=list)  # by the automatic update, in the run
    nothing: List[str] = field(default_factory=list)  # nothing to update
    #: out of date, but nothing on them changed (the log says why: updatepages.unchanged_reason)
    unchanged: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)  # could not be updated (the log)
    #: not done: Cancel was clicked in the open-tabs step (the run leaves them)
    left: List[str] = field(default_factory=list)
    #: what the automatic update brings up to date in them right after the run ('genies and
    #: symbols', ...: the run saved library elements, LibraryCommands._follow_run_saves);
    #: '' = nothing, they stay as they are
    left_follow: str = ""
    #: what follows in a page of ``left`` (its path as listed) when it is not
    #: ``left_follow``: a template or super genie the run saved reaches only the pages made
    #: from it (LibraryCommands._left_follow)
    left_words: Dict[str, str] = field(default_factory=dict)
    #: the pages of ``left`` whose template is still out of date in their tab
    left_template: List[str] = field(default_factory=list)
    genies: int = 0
    templates: int = 0
    symbols: int = 0
    composites: int = 0
    supergenies: int = 0
    placeholders: int = 0  # placeholder genies placed again

    def paths(self) -> List[str]:
        return self.saved + self.updated + self.auto + self.unchanged + self.nothing + self.failed

    def counts(self) -> tuple:
        """(genies, templates, symbols, composite genies) updated in the tabs (a placeholder
        placed again is a genie)."""
        return (
            self.genies + self.supergenies + self.placeholders,
            self.templates,
            self.symbols,
            self.composites,
        )

    def projects(self) -> List[str]:
        """Their projects' names, each once (whatever the case of its folder's name)."""
        return _unique_names(os.path.basename(os.path.dirname(os.path.abspath(p))) for p in self.paths())

    @property
    def stale(self) -> int:
        """The open pages that were out of date (updated here or by the automatic update
        during the run, out of date with nothing changed, or that could not be updated)."""
        return len(self.saved) + len(self.updated) + len(self.auto) + len(self.unchanged) + len(self.failed)

    def lines(self, label) -> List[str]:
        """The result box's lines: one per outcome, ``label(path)`` = a page's name."""

        def them(xs):
            return "it" if len(xs) == 1 else "them"

        out = []
        if self.saved:
            out.append(
                "Open in a tab with no changes of yours, updated and saved there: %s."
                % _names(label(p) for p in self.saved)
            )
        if self.updated:
            out.append(
                "Open in a tab and updated there (save %s to keep the changes): %s."
                % (them(self.updated), _names(label(p) for p in self.updated))
            )
        if self.auto:
            out.append(
                "Open in a tab and already updated there by the automatic update (save "
                "%s to keep the changes): %s." % (them(self.auto), _names(label(p) for p in self.auto))
            )
        if self.unchanged:
            out.append(
                "Open in a tab, out of date, but nothing on %s could be updated (the "
                "log says why): %s." % (them(self.unchanged), _names(label(p) for p in self.unchanged))
            )
        if self.nothing:
            out.append("Open in a tab, nothing to update: %s." % _names(label(p) for p in self.nothing))
        if self.failed:
            out.append(
                "Open in a tab, could not be updated (the log says why): %s."
                % _names(label(p) for p in self.failed)
            )
        # (by what follows in them: a page whose template stays out of date is told so)
        groups: Dict[tuple, List[str]] = {}
        for p in self.left:
            words = self.left_words.get(p, self.left_follow)
            groups.setdefault((words, bool(words) and p in self.left_template), []).append(p)
        for (words, kept), ps in groups.items():
            its = "its" if len(ps) == 1 else "their"
            if not words:
                out.append(
                    "Open in a tab, not updated because the run was stopped: %s."
                    % _names(label(p) for p in ps)
                )
                continue
            out.append(
                "Open in a tab, left when the run was stopped. The automatic update "
                "brings %s changed %s up to date there now%s: %s."
                % (
                    its,
                    words,
                    ", not %s template (Tools > Update Template on This Page does it)" % its if kept else "",
                    _names(label(p) for p in ps),
                )
            )
        return out
