"""The checks after a page opened, and the fast-display trust check."""

from __future__ import annotations

from ...infra.logsetup import note_exception as _note_exc

from ..prefs.settings import option

from ..main_window_base import (
    QtCore,
    contextlib,
    os,
    ui_state,
)
from ..main_window_base import (
    PaintWatch,
    app_filter,
    run_all_post_open,
)


class PageOpenChecks:
    """What runs after a page opened: its notices, its deferred checks and the .ctF check."""

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
        PaintWatch(self.view.viewport(), self._run_post_open)
        QtCore.QTimer.singleShot(self.POST_OPEN_FALLBACK_MS, self, self._run_post_open)
        f = app_filter()
        if f is not None:
            f.before_input = run_all_post_open

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
        if not doc.read_only and option("auto_placeholder_missing"):
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
        except Exception:  # noqa: BLE001  # swallow-ok: a hint only
            state, link = "", ""
        if state != "stale":
            return False
        n = 0
        if option("auto_update_template"):
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
        if not doc.read_only and option("auto_update_symbols"):
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
        if not doc.read_only and option("auto_update_genies"):
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
        except Exception:  # noqa: BLE001  # swallow-ok: not an EditableDocument
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
            _note_exc("page_open_checks._supergenie_guard")
            link = None
        if link is None:
            return "edit"
        try:
            from ...io.citect_config import allow_edit_super_genie_page

            allow = allow_edit_super_genie_page()
        except Exception:  # noqa: BLE001
            _note_exc("page_open_checks._supergenie_guard")
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
        except Exception:  # noqa: BLE001  # swallow-ok: a hint only
            return False

    def open_page(self, path: str) -> bool:
        """``open_document`` for the user's own commands (Open, Recent Pages,
        the start page): a page that cannot be opened is reported in plain
        words instead of escaping as a traceback. The Recent Pages entry is
        kept. False when it did not open."""
        try:
            self.open_document(path)
        except Exception as e:  # noqa: BLE001
            _note_exc("page_open_checks.open_page")
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
                _note_exc("page_open_checks.open_files")
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
