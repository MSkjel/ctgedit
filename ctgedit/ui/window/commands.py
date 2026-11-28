"""Arrange, tools, text, find, print, view, import and guideline commands."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ...infra.paths import path_key

from ..names import plural

from ..main_window_base import editor_windows

from ..main_window_base import (
    ElementDocument,
    QtCore,
    QtWidgets,
    app_settings,
    contextlib,
    hashlib,
    json,
    option,
    os,
)
from ..messages import warn


class Commands:
    """MainWindow's Arrange / Tools, lock mode, the Text menu, find / print / view, import /
    bitmaps, save all / run / defaults and guidelines."""

    # ------------------------------------------------------------ Text menu
    def _text_targets(self, indices=None, quiet: bool = False) -> list:
        """The selected TEXT records and buttons (font edits apply to these;
        a button's font is its caption font block) -- not a placed genie's
        texts: CtDraw re-composes those from the genie's element on load.
        ``indices`` = another selection; ``quiet`` = no status message."""
        if self.doc is None:
            return []
        from ..objprops.registry import placed_genie_of

        n = len(self.doc.page.objects)
        out, in_genie = [], 0
        for i in self._sel if indices is None else indices:
            if not 0 <= i < n:
                continue
            for j in self.doc.subtree(i):
                if self.doc.page.objects[j].raw.get("_type_code") in (0x05, 0x1001):
                    if placed_genie_of(self.doc, j) is not None:
                        in_genie += 1
                        continue
                    out.append(j)
        if in_genie and not out and not quiet:
            self.statusBar().showMessage(
                "The selected text is part of a genie, whose objects come from its "
                "definition. Right-click the genie > Edit Definition to change it.",
                8000,
            )
        return sorted(set(out))

    def _font_dialog(self) -> None:
        """Text > Font: a modeless window on the selected texts (it follows
        the selection); Apply / OK change them, one undo step."""
        if self._raise_modeless(self._dialogs.get("font")):
            self._sync_selection_windows()
            return
        targets = self._text_targets()
        if not targets:
            return
        from ..dialog.dialogs import FontDialog

        dlg = FontDialog(
            self.doc.page.objects[targets[0]].style,
            self,
            targets=lambda doc, sel: self._text_targets(sel, quiet=True) if doc is self.doc else [],
        )
        dlg.applyRequested.connect(lambda d=dlg: self._apply_font(d))
        self._show_modeless(dlg, "font", scope="selection")

    def _apply_font(self, dlg) -> None:
        """Apply / OK of the Font window: the texts selected now."""
        targets = self._text_targets()
        if not targets:
            return
        vals = dlg.values()
        changed = False
        with self.doc.batch("font"):
            for i in targets:
                changed |= bool(self.doc.set_font(i, **vals))
        dlg.mark_applied()
        if changed:
            self._refresh()

    def _toggle_font_flag(self, key: str) -> None:
        targets = self._text_targets()
        if not targets:
            return
        cur = bool(getattr(self.doc.page.objects[targets[0]].style, key))
        changed = False
        with self.doc.batch(key):
            for i in targets:
                changed |= bool(self.doc.set_font(i, **{key: not cur}))
        if changed:
            self._refresh()

    def _set_justify(self, mode: str) -> None:
        changed = False
        with self.doc.batch("justify"):
            for i in self._text_targets():
                changed |= bool(self.doc.set_font(i, justify=mode))
        if changed:
            self._refresh()

    def _set_text_effect(self, effect: int) -> None:
        changed = False
        with self.doc.batch("text effect"):
            for i in self._text_targets():
                changed |= bool(self.doc.set_text_effect(i, effect))
        if changed:
            self._refresh()

    # ------------------------------------------- save all / run / defaults
    def save_all(self) -> None:
        """File > Save All."""
        from ..jobs.job_progress import JobProgress

        n = n_failed = 0
        failed, warned = [], []
        todo = []
        for win in editor_windows():
            if win._flush_text_edit():
                win._refresh()
            todo += [(win, e["doc"]) for e in list(win._docs) if e["doc"].dirty]
        prog = (
            JobProgress(
                self,
                "Save All",
                "Saving the pages…",
                phase="save",
                total=len(todo),
                cancel="Stop After This Page",
            )
            if len(todo) > 1
            else None
        )
        left = 0
        try:
            for k, (win, doc) in enumerate(todo):
                name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
                if prog is not None and not prog.tick(
                    k, len(todo), "Saving %s… (%d of %d)" % (name, k + 1, len(todo))
                ):
                    left = len(todo) - k
                    break
                errs: list = []
                try:
                    ok = win._save_document(doc, errs)
                except RuntimeError:  # swallow-ok: its window closed meanwhile: not saved
                    ok = False
                if ok:
                    n += 1
                    warned.extend(errs)
                else:
                    n_failed += 1
                    failed.extend(errs or ["%s was not saved." % name])
        finally:
            if prog is not None:
                prog.close()
        for win in editor_windows():
            with contextlib.suppress(RuntimeError):
                win._update_title()
        self.statusBar().showMessage(
            "Saved %s%s%s."
            % (
                plural(n, "page"),
                (", %d not saved" % n_failed) if n_failed else "",
                (", %d left when Save All was stopped" % left) if left else "",
            ),
            8000,
        )
        if failed or warned:
            from ..messages import list_box

            text = "Saved %s." % plural(n, "page")
            if failed:
                text += (
                    " %s not saved: %s changes are still open here. To keep them, show %s tab "
                    "and use Save As to save under another name or in another folder."
                    % (
                        "1 page was" if n_failed == 1 else "%d pages were" % n_failed,
                        "its" if n_failed == 1 else "their",
                        "its" if n_failed == 1 else "each page's",
                    )
                )
            if warned:
                text += " %s saved with a problem." % (
                    "1 was" if len(warned) == 1 else "%d were" % len(warned)
                )
            list_box(
                self,
                "Save All",
                text,
                ["Not saved: " + t for t in failed] + ["Saved, with a problem: " + t for t in warned],
            )

    def _project_needs_compile(self) -> bool:
        """Whether Run has something to compile: the page has unsaved changes,
        or its project -- or the root project Run compiles -- is UNCOMPILED
        in its PROJECT.CIT (every save that registers a page marks it so;
        CtDraw's Run tests the same status)."""
        if self.doc is None:
            return False
        if self.doc.dirty:
            return True
        from ...io import projectcit

        pdir = os.path.dirname(os.path.abspath(self.doc.path))
        dirs = [pdir]
        try:
            from ...backend.compiler import root_project_for
            from ...format.projects import master_projects
            from ...io.citect_config import live_user_dir

            root = root_project_for(pdir)
            user = live_user_dir()
            master = master_projects(user) if user else {}
            path = master.get(root.lower()) or master.get(root)
            if path:
                dirs.append(path)
        except Exception:  # noqa: BLE001 -- the page's own project decides then
            _note_exc("commands._project_needs_compile")
            pass
        for d in dirs:
            try:
                cit = projectcit.find(d)
                state = projectcit.read_streams(cit).get("CompState", b"") if cit else b""
            except Exception:  # noqa: BLE001
                _note_exc("commands._project_needs_compile")
                state = b""
            if bytes(state).strip(b"\0 ").upper().startswith(b"UNCOMPILED"):
                return True
        return False

    def _ask_compile_before_run(self):
        """Ask whether to compile before running."""
        if not option("compile_enquiry") or not self._project_needs_compile():
            return True
        pdir = os.path.dirname(os.path.abspath(self.doc.path))
        try:
            from ...backend.compiler import root_project_for

            project = root_project_for(pdir)
        except Exception:  # noqa: BLE001
            _note_exc("commands._ask_compile_before_run")
            project = os.path.basename(pdir)
        answer = self._compile_enquiry(project, bool(self.doc.dirty))
        return {"compile": True, "run": False}.get(answer)

    def _compile_enquiry(self, project: str, dirty: bool) -> str:
        """The Compile enquiry message: 'compile', 'run' (without compiling)
        or 'cancel'."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Run")
        box.setText("Compile project %s before running it?" % project)
        info = (
            "The project has changes that are not compiled. Without compiling, the "
            "runtime runs the project as it was last compiled."
        )
        if dirty:
            info += (
                " This page's unsaved changes are saved only when you compile; "
                "they stay in the editor otherwise."
            )
        box.setInformativeText(info)
        comp = box.addButton("Compile and Run", QtWidgets.QMessageBox.YesRole)
        run = box.addButton("Run Without Compiling", QtWidgets.QMessageBox.NoRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(comp)
        box.exec()
        clicked = box.clickedButton()
        return "compile" if clicked is comp else ("run" if clicked is run else "cancel")

    def _run_project(self) -> None:
        """File > Run: save, compile and start the runtime."""
        # (no page open: the project of the most recent page, as File > Compile)
        target = None if isinstance(self.doc, ElementDocument) else self._compile_target()
        if target is None:
            self._launch_external("RuntimeManager.exe")
            return
        if self._outside_install(target[0], "run"):
            return
        compile_now = self._ask_compile_before_run()
        if compile_now is None:
            return
        project = self._run_compile_step(target[0], compile_now)
        if project is None:
            return
        from ...backend import runtime

        running = runtime.is_running()
        if not self._runtime_ready_for(target, project, compile_now, running):
            return
        self._start_runtime_waiting(target[0], running)

    def _run_compile_step(self, pdir: str, compile_now: bool):
        """File > Run's compile of the project of ``pdir`` (``compile_now``), else its root
        project's name: the project's name, None when the compile did not complete."""
        if compile_now:
            result = self.compile_project(show="errors")
            if result is None or result.errors or result.outcome != "completed":
                return None
            return getattr(result, "project", "") or os.path.basename(pdir)
        from ...backend.compiler import root_project_for

        try:
            return root_project_for(pdir)
        except Exception:  # noqa: BLE001
            _note_exc("commands._run_project")
            return os.path.basename(pdir)

    def _runtime_ready_for(self, target, project: str, compile_now: bool, running: bool) -> bool:
        """File > Run with a runtime ``running``: one that runs this project shows the page
        again (said); another one stops only when the user says so. True when the runtime is
        to be started."""
        from ...backend import ctapi, runtime

        pdir = target[0]
        page = os.path.splitext(os.path.basename(target[1]))[0] if target[1] else project
        done = "Compiled" if compile_now else "Not compiled"
        stop_note = (
            "Plant SCADA is shut down first: everything that runtime does on this "
            "computer stops (displays, alarms, trends, logging, device "
            "communication) until it has started again."
        )
        if running and not target[1] and runtime.runs_project(pdir):
            self.statusBar().showMessage(
                "%s. The runtime is running %s and shows the changes when it next displays "
                "a page." % (done, project),
                8000,
            )
            return False
        # the runtime already running THIS project re-reads a re-displayed page
        # (drawing + the recompiled .rdb; live-verified) -- no restart needed
        if running and runtime.runs_project(pdir):
            try:
                if ctapi.redisplay(page):
                    self.statusBar().showMessage("%s. The runtime shows the updated %s." % (done, page), 8000)
                    return False
                self.statusBar().showMessage(
                    "%s. The runtime shows the changes when it next displays %s." % (done, page), 8000
                )
                return False
            except Exception as e:  # noqa: BLE001 -- no CtAPI: a restart shows it
                _note_exc("commands._run_project")
                why = str(e) or type(e).__name__
            if not self._confirm(
                "Restart the Runtime",
                "The runtime is running %s, but ctgedit could not ask it to show "
                "the updated %s (%s)." % (project, page, why),
                "Restart Runtime",
                "Restart the runtime to see the change? " + stop_note,
            ):
                self.statusBar().showMessage(
                    "%s. The runtime shows the changes when it next displays %s." % (done, page), 10000
                )
                return False
        elif running:
            other = runtime.running_project()
            if other:
                text = "The Plant SCADA runtime on this computer is running project %s." % other
            else:
                text = (
                    "A Plant SCADA runtime is running on this computer, and ctgedit "
                    "cannot tell which project it runs."
                )
            if not self._confirm(
                "Stop the Running Runtime",
                text,
                "Stop and Run %s" % project,
                "Stop it and run %s instead? %s" % (project, stop_note),
            ):
                self.statusBar().showMessage(
                    "%s %s. The running runtime was left as it is." % (done, project), 10000
                )
                return False
        return True

    def _start_runtime_waiting(self, pdir: str, running: bool) -> None:
        """Start the runtime with the project of ``pdir`` (a ``running`` one is stopped
        first) on a worker thread: stopping a runtime takes up to a minute. The window says
        the time so far and its Cancel leaves the wait: the runtime goes on being stopped
        and started on the thread, and the status bar says how it ended."""
        from ...backend import runtime
        from ..prefs.job_options import poll_ms
        from ..jobs.job_progress import JobProgress

        text = (
            "Stopping the running runtime (this can take a minute)…" if running else "Starting the runtime…"
        )
        prog = JobProgress(self, "Run", text, cancel="Stop Waiting")
        out: dict = {}

        def work():
            from ...infra import gcguard

            gcguard.before_worker()
            try:
                out["notes"] = runtime.start(pdir)
            except Exception as e:  # noqa: BLE001 -- said below
                _note_exc("commands._run_project")
                out["error"] = e

        import threading

        th = threading.Thread(target=work, name="ctgedit-run", daemon=True)
        th.start()
        waiting = True
        try:
            while th.is_alive() and waiting:
                waiting = prog.wait(poll_ms())
        finally:
            prog.close()

        def finish():
            if "error" in out:
                warn(
                    self,
                    "Run",
                    "The runtime could not be started: %s\n\nStart it from "
                    "the Runtime Manager or Plant SCADA Studio instead." % out["error"],
                )
                return
            self.statusBar().showMessage("Runtime starting: " + "; ".join(out.get("notes") or []) + ".", 8000)

        if not th.is_alive():
            finish()
            return
        # (the wait was left: the thread goes on, and says how it ended when it has)
        self.statusBar().showMessage(
            "The runtime is still being %s in the background. The status bar says when it "
            "has." % ("stopped and started" if running else "started"),
            8000,
        )
        timer = QtCore.QTimer(self)
        timer.setInterval(poll_ms())

        def look():
            if th.is_alive():
                return
            timer.stop()
            timer.deleteLater()
            with contextlib.suppress(RuntimeError):  # (the window closed meanwhile)
                finish()

        timer.timeout.connect(look)
        timer.start()

    # ------------------------------------------------------- guidelines
    def _guidelines_dialog(self) -> None:
        """View > Guidelines Setup: a modeless window on the guidelines of
        the page shown (it follows the page tabs and the guidelines dragged
        on the canvas while nothing is typed); Apply / OK set them."""
        if self._raise_modeless(self._dialogs.get("guides")):
            self._sync_guidelines_dialog()
            return
        from ..dialog.dialogs import GuidelinesDialog

        vert, horiz = self.view.guidelines()
        dlg = GuidelinesDialog(
            vert,
            horiz,
            self.view.guides_visible(),
            self.view.snap_guides(),
            self,
            page=self._doc_name(self.doc) if self.doc is not None else "",
        )
        dlg._ctg_page_doc = self.doc
        dlg._ctg_reads_page = True
        dlg._ctg_follow = lambda _doc, d=dlg: self._sync_guidelines_dialog()
        dlg.applyRequested.connect(lambda d=dlg: self._apply_guidelines(d))
        self._show_modeless(dlg, "guides", scope="window")

    def _apply_guidelines(self, dlg) -> None:
        """Apply / OK of Guidelines Setup: the page it shows (a page not
        shown now keeps them for its tab)."""
        vert, horiz, display, snap = dlg.values()
        target = getattr(dlg, "_ctg_page_doc", None)
        if target is None or target is self.doc:
            self.view.set_guidelines(vert, horiz)
            self._save_guidelines()
        else:
            self._store_guidelines(target, vert, horiz)
        self.view.set_guides_visible(display)
        self.act_show_guides.setChecked(display)
        self.view.set_snap_guides(snap)
        self.act_snap_guides.setChecked(snap)
        dlg.mark_applied()

    def _sync_guidelines_dialog(self) -> None:
        """The open Guidelines Setup shows the page shown now, while nothing
        is typed in it."""
        dlg = self._dialogs.get("guides")
        if dlg is None or self.doc is None:
            return
        try:
            if dlg.has_pending_edits():
                return
            vert, horiz = self.view.guidelines()
            dlg.load(
                vert,
                horiz,
                self.view.guides_visible(),
                self.view.snap_guides(),
                page=self._doc_name(self.doc),
            )
            dlg._ctg_page_doc = self.doc
        except RuntimeError:
            pass

    def _toggle_snap_guides(self) -> None:
        self.view.set_snap_guides(self.act_snap_guides.isChecked())
        self._sync_guidelines_dialog()

    def _toggle_show_guides(self) -> None:
        self.view.set_guides_visible(self.act_show_guides.isChecked())
        self._sync_guidelines_dialog()

    # Guideline positions are EDITOR state, never written to the .ctg: kept per
    # page in the settings (CtDraw keeps one set for the session in citect.ini
    # [CTDRAW] GuideHorz / GuideVert -- ctgedit shows several pages at once, and
    # each page keeps its own)
    @staticmethod
    def _guidelines_page(doc) -> str | None:
        """What the guidelines are stored for: the page file (a library
        element: its library file + element name), or None (nothing to key)."""
        path = getattr(doc, "path", None)
        if not path:
            return None
        name = path_key(path)
        if isinstance(doc, ElementDocument):
            name += "|" + str(doc.element_name).lower()
        return name

    @classmethod
    def _guidelines_key(cls, doc) -> str | None:
        # a hash, not the path itself: a settings key must not hold '\' / ':'
        name = cls._guidelines_page(doc)
        if name is None:
            return None
        return "guides/%s" % hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]

    def _load_guidelines(self, doc) -> tuple[list, list]:
        key = self._guidelines_key(doc) if doc is not None else None
        if key is None:
            return [], []
        raw = app_settings().value(key, "", type=str)
        try:
            d = json.loads(raw) if raw else {}
            return ([int(v) for v in d.get("v", [])], [int(h) for h in d.get("h", [])])
        except ValueError, TypeError, AttributeError:
            return [], []

    def _save_guidelines(self) -> None:
        """Store the canvas's guidelines for the current page."""
        if self.doc is not None:
            self._store_guidelines(self.doc, *self.view.guidelines())

    def _store_guidelines(self, doc, vert, horiz) -> None:
        """Store ``vert`` / ``horiz`` as the guidelines of ``doc``."""
        key = self._guidelines_key(doc) if doc is not None else None
        if key is None:
            return
        st = app_settings()
        vert, horiz = list(vert), list(horiz)
        if vert or horiz:
            st.setValue(key, json.dumps({"page": self._guidelines_page(doc), "v": vert, "h": horiz}))
        else:
            st.remove(key)

    def _on_guidelines_changed(self, message: str) -> None:
        """A guideline was dragged, added or deleted on the canvas."""
        self._save_guidelines()
        self._sync_guidelines_dialog()
        if message:
            self.statusBar().showMessage(message, 4000)
