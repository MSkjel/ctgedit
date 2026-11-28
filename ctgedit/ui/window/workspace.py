"""Situational Awareness workspace helpers."""

from __future__ import annotations

from ...infra.paths import folder_key, path_key

from ...infra.logsetup import note_exception as _note_exc


from ..main_window_base import editor_windows, main_window_class

import contextlib
from ..main_window_base import (
    EditableDocument,
    ElementDocument,
    QtCore,
    QtWidgets,
    app_settings,
    os,
    saved_message,
    ui_state,
)
from ..messages import warn


class WorkspaceTools:
    """MainWindow's Situational Awareness workspace aids."""

    # ---------------------------------------- Situational Awareness workspace
    def _check_workspace(self, show: bool = True, doc=None):
        """Tools > Check Workspace: the workspace checks of ``doc`` (default:
        the open page) into the Workspace Checks panel (sa_workspace; they
        only report). Returns the report (None = could not run)."""
        doc = doc if doc is not None else self.doc
        from ..notify import notify, open_log_action

        if doc is None or isinstance(doc, ElementDocument):
            if show:
                self.statusBar().showMessage("Open a page to check its workspace.", 4000)
            return None
        from ..panels.sa_checks import run_checks

        rep = run_checks(doc)
        if rep is None:
            if show:
                notify(
                    self,
                    "The workspace checks could not run.",
                    "error",
                    actions=[open_log_action()],
                    key="checks:workspace",
                )
            return None
        doc.ui.sa_report = rep  # the panel shows it again on this tab
        if doc is self.doc:
            self.sa_checks.show_report(rep)
        if show:
            self._show_sa_dock()
            if not rep.panes:
                notify(self, "%s has no Situational Awareness panes." % rep.page, key="checks:workspace")
            else:
                summary = rep.summary()
                notify(
                    self,
                    "Workspace checks of %s: %s." % (rep.page, summary or "no problems found"),
                    "warning" if summary else "success",
                    key="checks:workspace",
                )
        return rep

    # ---------------------------------------- Tools > Check Page
    def _check_page(self, show: bool = True, doc=None):
        """Tools > Check Page: the page checks (ctgedit.features.page_checks) of ``doc`` (default: the
        open page) into the Page Checks panel, the workspace checks of a page with panes
        added, then the engine compiles every expression and command (ui/expression_check)
        and its errors and warnings (tags and pages the project does not have) are added as
        they come. Returns the report (None = no page)."""
        doc = doc if doc is not None else self.doc
        if doc is None or isinstance(doc, ElementDocument):
            if show:
                self.statusBar().showMessage("Open a page to check it.", 4000)
            return None
        from ...features import page_checks as PC

        rep = PC.check_document(doc)
        # (the records as checked: a row finds its object again after an edit, PC.resolve)
        records = list(doc.parsed.page.objects)
        # the workspace checks of a page with panes, as rows of the same list
        from ...features import sa_workspace as SA

        if SA.has_panes(doc):
            sa = self._check_workspace(show=False, doc=doc)
            if sa is not None:
                for f in sa.findings:
                    rep.findings.append(
                        PC.Finding(
                            f.level,
                            "Workspace: " + f.message,
                            f.index,
                            f.field,
                            "workspace",
                            (f.pane.name or "(no name)") if f.pane is not None else "(page)",
                        )
                    )
        PC.stamp_records(rep.findings, records)
        doc.ui.page_report = rep
        self._show_page_report(doc)
        if show:
            self._show_page_checks_dock()
            from ..notify import notify

            summary = rep.summary()
            notify(
                self,
                "Page checks of %s: %s." % (rep.page, summary or "no problems found"),
                "warning" if summary else "success",
                key="checks:page:%s" % doc.path,
            )
        self._compile_page_fields(doc, rep, records)
        return rep

    def _compile_page_fields(self, doc, rep, records=None) -> None:
        """The engine phase: every expression and command field compiled one after the
        other on the project's checker; the errors go into ``rep`` and the panel follows."""
        from ...features import page_checks as PC
        from ..expression_check import checker_for_document

        fields = rep.fields  # (check_document's: the page as checked)
        rep.expressions = len(fields)
        if not fields:
            self._show_page_report(doc)
            return
        checker = checker_for_document(doc)
        if checker is None:
            rep.engine_note = "The expressions were not compiled (no project folder)."
            self._show_page_report(doc)
            return
        queue = list(fields)
        token = object()
        doc.ui.page_check_token = token
        from ..jobs import tasks

        name = os.path.splitext(os.path.basename(doc.path))[0]

        def stop():
            if getattr(doc.ui, "page_check_token", None) is token:
                doc.ui.page_check_token = None
                rep.engine_note = "The expression check was stopped after %d of %d." % (
                    rep.compiled,
                    rep.expressions,
                )
                self._show_page_report(doc)
            tasks.end(task)

        task = tasks.begin("expressions", "Checking expressions of %s:" % name, cancel=stop, window=self)
        tasks.progress(task, 0, len(fields))

        def step():
            if getattr(doc.ui, "page_check_token", None) is not token:
                tasks.drop(task)  # (a newer check of the page took over)
                return
            if not queue:
                self._show_page_report(doc)
                tasks.end(task, summary="Checked %d expressions of %s." % (rep.compiled, name))
                return
            f = queue.pop(0)

            def done(result, f=f):
                if getattr(doc.ui, "page_check_token", None) is not token:
                    tasks.drop(task)
                    return
                if result.get("error"):
                    rep.engine_note = "The expressions were not compiled: %s" % result["error"]
                    rep.compiled = rep.expressions
                    queue.clear()
                    self._show_page_report(doc)
                    tasks.end(
                        task,
                        ok=False,
                        summary="The expressions of %s were not "
                        "compiled: %s." % (name, str(result["error"]).rstrip(".")),
                    )
                    return
                rep.compiled += 1
                tasks.progress(task, rep.compiled, rep.expressions)
                new = PC.engine_findings(doc, f, result)
                if records is not None:
                    PC.stamp_records(new, records)
                if new:
                    rep.findings.extend(new)
                    rep.findings.sort(
                        key=lambda x: (
                            {PC.ERROR: 0, PC.WARNING: 1, PC.INFO: 2}.get(x.level, 3),
                            -1 if x.index is None else x.index,
                        )
                    )
                if new or not queue or rep.compiled % 10 == 0:
                    self._show_page_report(doc)
                step()

            try:
                checker.check(f.text, f.kind, done)
            except Exception:
                tasks.end(task, ok=False)  # (its task does not stay behind)
                raise

        step()

    def _show_page_report(self, doc=None) -> None:
        """The Page Checks panel follows the open tab (its last report)."""
        doc = doc if doc is not None else self.doc
        panel = getattr(self, "page_checks", None)
        if panel is None or doc is not self.doc:
            return
        rep = ui_state(doc).page_report if doc is not None else None
        if rep is not None:
            panel.show_report(rep)
        elif isinstance(doc, ElementDocument):
            panel.clear("The page checks apply to pages: %s is a library element." % doc.display_name)
        elif doc is not None:
            panel.clear(
                "%s has not been checked yet. Click Check Again to check it."
                % os.path.splitext(os.path.basename(doc.path))[0]
            )
        else:
            panel.clear("Open a page to check it.")

    def _show_page_checks_dock(self) -> None:
        d = self.dock_page_checks
        opening = d.isHidden()
        d.show()
        d.raise_()
        if opening and not d.isFloating():
            with contextlib.suppress(Exception):
                self.resizeDocks([d], [200], QtCore.Qt.Vertical)

    def _goto_page_finding(self, f) -> None:
        """A Page Checks row: select the object (as it is now) and bring it into view."""
        if self.doc is None or f is None or f.index is None:
            return
        from ...features import page_checks as PC

        i = PC.resolve(self.doc, f)
        if i is None:
            self.statusBar().showMessage("The object is no longer on the page. Use Check Again.", 6000)
            return
        self._goto_preview(i)

    def _page_finding_properties(self, f) -> None:
        """Properties of a Page Checks row: the object's sheet (an input field on its
        owner's Input tab), or Page Properties for a page row."""
        if self.doc is None or f is None:
            return
        if f.index is None:
            self.page_properties()
            return
        from ...features import page_checks as PC

        i = PC.resolve(self.doc, f)
        if i is None:
            self.statusBar().showMessage("The object is no longer on the page. Use Check Again.", 6000)
            return
        if (f.field or "").upper() in ("CMD", "DOWN_CMD", "REPEAT_CMD", "DBLCLK_CMD", "KEY_SEQ"):
            self.edit_input(self._owner_of(i))
            return
        self._open_sheet_of(i)

    def _owner_of(self, index: int) -> int:
        from ...format import inputs

        obj = self.doc.page.objects[index]
        if obj.raw.get("_type_code") in inputs.INPUT_TCS and obj.parent is not None:
            return obj.parent
        return index

    def _after_save_checks(self, errors=None) -> None:
        """After a page with SA panes was saved: its workspace checks (the
        panel opens when they find an error or a warning; 'Check when saving'
        in the panel turns this off). The save is never changed."""
        from ..panels.sa_checks import check_on_save
        from ...features import sa_workspace as SA

        self._sa_note = ""  # only this save's findings are reported
        doc = self.doc
        if doc is None or isinstance(doc, ElementDocument) or not check_on_save() or not SA.has_panes(doc):
            return
        rep = self._check_workspace(show=False, doc=doc)
        if rep is None:
            return
        serious = rep.count(SA.ERROR) + rep.count(SA.WARNING)
        if not serious:
            return
        found = ", ".join(p for p in rep.summary().split(", ") if "note" not in p)
        if errors is not None:  # Save All: in its summary
            errors.append(
                "%s: the workspace checks found %s (see the Workspace Checks panel)." % (rep.page, found)
            )
        else:  # Save / Save As: in their status message
            self._sa_note = "Workspace checks: %s (see the Workspace Checks panel)" % found
        self._show_sa_dock()

    def _show_sa_dock(self) -> None:
        """Open the Workspace Checks panel (a modest height the first time it
        opens docked: the canvas keeps most of the window)."""
        d = self.dock_sa_checks
        opening = d.isHidden()
        d.show()
        d.raise_()
        if opening and not d.isFloating():
            with contextlib.suppress(Exception):
                self.resizeDocks([d], [190], QtCore.Qt.Vertical)

    def _pop_sa_note(self) -> str:
        """The workspace-checks note of the save that just ran, as a
        status-message suffix; '' when none."""
        note, self._sa_note = getattr(self, "_sa_note", ""), ""
        return note

    def _show_sa_report(self) -> None:
        """The Workspace Checks panel follows the open tab (its last report)."""
        rep = ui_state(self.doc).sa_report if self.doc is not None else None
        if rep is not None:
            self.sa_checks.show_report(rep)
        elif isinstance(self.doc, ElementDocument):
            self.sa_checks.clear(
                "Workspace checks apply to pages: %s is a library element." % self.doc.display_name
            )
        else:
            self.sa_checks.clear(
                "%s has not been checked yet: save it, or use Tools > Check "
                "Workspace." % os.path.splitext(os.path.basename(self.doc.path))[0]
                if self.doc is not None
                else ""
            )

    def _goto_sa_finding(self, f) -> None:
        """A Workspace Checks row: select its pane's genie -- found on the
        page as it is NOW (an edit since the check moves records), not by
        the record index the check saw."""
        if self.doc is None or f is None or getattr(f, "pane", None) is None:
            return
        from ...features import sa_workspace as SA

        p = SA.locate_pane(self.doc.page, f.pane)
        if p is None:
            self.statusBar().showMessage(
                "%s is no longer on the page. Use Check Again in the Workspace Checks "
                "panel." % f.pane.label(),
                6000,
            )
            return
        self._goto_preview(p.root)

    def _page_properties_tab(self, tab: str = "") -> None:
        """Open Page Properties on ``tab`` (a workspace-check row of the page)."""
        self.page_properties()
        dlg = self._modeless_for("pageprops")
        tabs = getattr(dlg, "_tabs", None)
        if tab and isinstance(tabs, QtWidgets.QTabWidget):
            for k in range(tabs.count()):
                if tabs.tabText(k).replace("&", "") == tab:
                    tabs.setCurrentIndex(k)
                    break

    def _toggle_aliased_text(self) -> None:
        on = self.act_aliased_text.isChecked()
        app_settings().setValue("view/aliased_text", on)
        self.view.set_aliased_text(on)

    def _toggle_pane_labels(self) -> None:
        on = self.act_pane_labels.isChecked()
        app_settings().setValue("view/pane_labels", on)
        self.scene.set_pane_labels_visible(on)

    def _genie_pickers(self, own: int | None = None):
        """The SA parameter pickers of the genie dialogs (ui/panels/sa_pickers) for
        the open page: equipment, menu items, the page's panes (``own`` = the
        genie being edited, left out of its own pane lists)."""
        if self.doc is None:
            return None
        try:
            from ..panels.sa_pickers import ParamPickers
            from ...features import sa_workspace as SA

            panes = SA.model_panes(self.doc.page)
            if own is not None:  # the genie's own pane record (at any depth)
                mine = set(self.doc.subtree(own))
                panes = [p for p in panes if p.index not in mine]
            return ParamPickers(os.path.dirname(os.path.abspath(self.doc.path)), panes)
        except Exception:  # noqa: BLE001 -- plain text boxes then
            _note_exc("workspace._genie_pickers")
            return None

    @staticmethod
    def _save_error_text(target: str, exc: BaseException):
        """(title, what happened, what to do, Save As would help) for a failed
        save of ``target``. The writer is atomic (format/fileio), so the file
        on disk is always the old one."""
        import errno

        name = os.path.basename(target)
        if isinstance(exc, ValueError):  # a refused name / character
            return "Not Saved", str(exc), "", False
        if isinstance(exc, PermissionError):
            who = ""
            if os.path.exists(target):
                with contextlib.suppress(Exception):
                    from ...backend.filelock import describe

                    who = describe(target)
            if not os.path.exists(target):
                # a new file (Save As): the FOLDER refused it
                folder = os.path.dirname(os.path.abspath(target))
                why = "%s was not saved: Windows does not allow new files in %s." % (name, folder)
                todo = "Use Save As to save it into a folder you can write to."
            elif EditableDocument.file_read_only(target):
                why = "%s is read-only, so it was not saved." % name
                todo = (
                    "Use Save As to save your changes under another name, or clear "
                    "the file's read-only attribute and save again."
                )
            elif who:
                why = "%s is locked by %s, so it was not saved." % (name, who)
                todo = (
                    "Close it in that program and save again, or use Save As to "
                    "save your changes under another name."
                )
            else:
                why = "%s is read-only or locked by another program, so it was not saved." % name
                todo = (
                    "Close any program that has it open and save again, or use "
                    "Save As to save your changes under another name."
                )
            return "Not Saved", why, todo, True
        if isinstance(exc, OSError) and (
            exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112)
        ):
            return (
                "Disk Full",
                "The disk is full, so %s was not saved." % name,
                "Free some disk space and save again, or use Save As to save to another drive.",
                True,
            )
        if isinstance(exc, OSError):
            return (
                "Not Saved",
                "%s could not be saved: %s." % (name, exc.strerror or exc),
                "Save again, or use Save As to save your changes under another name.",
                True,
            )
        return (
            "Not Saved",
            "%s could not be saved: %s" % (name, main_window_class()._error_text(exc)),
            "Use Save As to save your changes under another name.",
            True,
        )

    def _save_failed(self, target: str, exc: BaseException, errors=None, offer_save_as: bool = True) -> bool:
        """Report a failed save."""
        title, why, todo, save_as_helps = self._save_error_text(target, exc)
        if errors is not None:
            errors.append(why)
            return False
        steps = []
        if save_as_helps:
            steps.append("Your changes are still open here; the file on disk was not changed.")
        if todo:
            steps.append(todo)
        from ..messages import error_box

        acts = [("Show in Explorer", lambda p=target: self._reveal_in_explorer(p))]
        if offer_save_as and save_as_helps:
            acts.insert(0, ("Save As…", None))
        chosen = error_box(self, title, why, exc=exc, next_steps=steps, actions=acts)
        if chosen == "Save As…":
            return self.save_as()
        return False

    def _confirm(
        self, title: str, text: str, ok_label: str, detail: str = "", icon=QtWidgets.QMessageBox.Warning
    ) -> bool:
        """A warning with one action button and Cancel (the default and Esc), as
        messages.confirm asks it. True when the action was chosen."""
        from ..messages import confirm

        return confirm(
            self, title, text, ok_label, detail=detail, warning=icon == QtWidgets.QMessageBox.Warning
        )

    def _refresh_runtime_page(self) -> None:
        """A running runtime showing this page re-displays it (CtAPI
        PageDisplay makes the client re-read the saved files) -- in the
        background, the save is already done."""
        if (
            self.doc is None
            or isinstance(self.doc, ElementDocument)
            or os.environ.get("CTG_NO_RUNTIME") == "1"
        ):
            return
        page = os.path.splitext(os.path.basename(self.doc.path))[0]

        def work():
            try:
                from ...backend import ctapi

                ctapi.redisplay(page)
            except Exception as e:  # noqa: BLE001 -- best effort
                from ...infra import logsetup

                logsetup.get("runtime").info("could not ask the runtime to re-display %s: %s", page, e)

        import threading
        from ...infra import gcguard

        gcguard.before_worker()
        threading.Thread(target=work, name="runtime-redisplay", daemon=True).start()

    def _confirm_builder_save(self, target: str, errors=None) -> bool:
        """Before a page is saved: when it is open in the Graphics Builder,
        ask first -- the Builder keeps its own copy in memory, still shows the
        old version after this save, and ITS next save of the page would
        overwrite this one. True = go ahead."""
        if self.doc is None or isinstance(self.doc, ElementDocument) or not self._open_in_builder(target):
            return True
        page = os.path.splitext(os.path.basename(target))[0]
        if self._confirm(
            "Open in the Graphics Builder",
            "%s is also open in the Graphics Builder." % page,
            "Save Anyway",
            (
                "The Graphics Builder has its own copy of this page and will not see this save. Saving "
                "the page there would overwrite it. Close the page there without saving first, or save "
                "anyway and reopen it there."
            ),
        ):
            note = "Reopen %s in the Graphics Builder to see this version" % page
            if errors is not None:  # Save All: listed in its summary
                errors.append(note + ".")
            else:  # Save / Save As: in their status message
                self._builder_note = note
            self.statusBar().showMessage(note + ".", 10000)
            return True
        if errors is not None:
            errors.append(
                "%s was not saved: it is open in the Graphics Builder, and you "
                "chose not to save over it." % page
            )
        return False

    def _pop_builder_note(self) -> str:
        """The 'reopen it in the Graphics Builder' note of the save that just
        ran (Save Anyway), as a status-message suffix; '' when none."""
        note, self._builder_note = getattr(self, "_builder_note", ""), ""
        return note

    def _confirm_damaged_save(self, errors=None) -> str:
        """The page was damaged when it was opened: overwriting it in place
        replaces the damaged file with what could be read. Ask (Save As keeps
        the original). 'overwrite' | 'saved' (saved as a copy) | 'cancel'."""
        doc = self.doc
        if ui_state(doc).damage_ok:
            return "overwrite"
        name = os.path.basename(doc.path)
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Save over a Damaged Page")
        box.setText("%s was damaged when it was opened (%s)." % (name, "; ".join(doc.damage)))
        box.setInformativeText(
            "Saving replaces the damaged file with what ctgedit could read of it; the "
            "parts it could not read are gone for good. Save a copy under another name "
            "instead to keep the damaged original?"
        )
        copy_btn = box.addButton("Save As…", QtWidgets.QMessageBox.AcceptRole)
        over = box.addButton("Overwrite", QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is over:
            doc.ui.damage_ok = True
            return "overwrite"
        if clicked is copy_btn:
            return "saved" if self.save_as() else "cancel"
        if errors is not None:
            errors.append(
                "%s was not saved: it was damaged when it was opened, and you "
                "chose not to overwrite it." % name
            )
        return "cancel"

    def save(self) -> bool:
        """Save the open page; False when the save was refused / failed (the
        caller must not proceed as if it were saved)."""
        if self.doc is None:
            return False
        if self._flush_text_edit():  # an open in-place text edit is saved too
            self._refresh()
        from ..notify import notify
        from ..dialog.history_versions import VERSION_SAVE_TEXT, is_version

        if is_version(self.doc):
            # an earlier version (File > Page History > Open Read-only): never over the page
            notify(
                self, VERSION_SAVE_TEXT % self._doc_name(self.doc), "warning", key="save:%s" % self.doc.path
            )
            return self.save_as()
        if self.doc.read_only:
            if EditableDocument.file_read_only(self.doc.path):
                # opened read-only: the file on disk stays as it is, the
                # changes go to a file the user picks
                notify(
                    self,
                    "%s is read-only. Choose where to save a copy with your "
                    "changes." % os.path.basename(self.doc.path),
                    "warning",
                    key="save:%s" % self.doc.path,
                )
                return self.save_as()
            self.doc.read_only = False  # made writable since it was opened
        if not self._save_to():
            return False
        self._saved_notices(self.doc)
        self._update_title()
        if isinstance(self.doc, ElementDocument):
            notify(
                self,
                "Saved %s into %s." % (self.doc.display_name, os.path.basename(self.doc.path)),
                "success",
                detail=self.doc.path,
                key="save:%s|%s" % (self.doc.path, self.doc.element_name),
            )
            return True
        report = getattr(self.doc, "save_report", "")
        notes = [n for n in (self._pop_builder_note(), self._pop_sa_note(), self._outside_note()) if n]
        notify(
            self,
            saved_message(self.doc.path, report, notes),
            "warning" if notes else "success",
            detail=self.doc.path,
            key="save:%s" % self.doc.path,
        )
        return True

    @staticmethod
    def _saved_notices(doc) -> None:
        """A save puts the page's notices of the version on disk away (it changed on disk:
        this version is on disk now)."""
        from ..editor import notices

        notices.remove(doc, "disk")

    def _version_saved_as(self) -> None:
        """An earlier version saved with Save As is a page (element) of its own now: no
        longer a version, writable."""
        from ..dialog.history_versions import is_version

        doc = self.doc
        if doc is not None and is_version(doc):
            doc.ui.version_view = None
            doc.read_only = False
            self._refresh_tabs()

    def _outside_note(self) -> str:
        """The save's note for a page outside a project folder."""
        try:
            outside = not self.doc.page_record_available()
        except Exception:  # noqa: BLE001
            _note_exc("workspace._outside_note")
            outside = False
        return (
            "The page is not in a project folder: it is not registered, and Plant SCADA does not compile it"
            if outside
            else ""
        )

    def _open_elsewhere(self, path: str):
        """'another tab' / 'another window' when page ``path`` is open there (not this tab's
        own page), else None."""
        from .. import main_window as _mw

        want = path_key(path)
        for win in [self] + [w for w in list(_mw.MainWindow.instances) if w is not self]:
            for e in getattr(win, "_docs", []):
                d = e["doc"]
                if d is self.doc or isinstance(d, ElementDocument) or getattr(d, "element", ""):
                    continue
                if not getattr(d, "path", ""):
                    continue
                if path_key(d.path) == want:
                    return "another tab" if win is self else "another window"
        return None

    def save_as(self) -> bool:
        if self.doc is None:
            return False
        if self._flush_text_edit():
            self._refresh()
        if isinstance(self.doc, ElementDocument):
            ok = self._save_element_as()
            if ok:
                self._version_saved_as()
            return ok
        from ..browser import PageBrowserDialog

        path = PageBrowserDialog.get_save_path(self, self.doc.path)
        if not path:
            return False
        other = self._open_elsewhere(path)
        if other is not None:
            # (two tabs on one file: the one saved last would overwrite the other's edits)
            warn(
                self,
                "Save As",
                "%s is open in %s. Close it there first, or save under another name."
                % (os.path.basename(path), other),
            )
            return False
        old_dir = os.path.dirname(os.path.abspath(self.doc.path))
        self._remember_dir("save", path)
        if not self._save_to(path):
            return False
        self._version_saved_as()
        # the document IS the new file now: title, MRU, project, docks follow
        self.doc._project_cache = None
        self.doc._tag_formats = None
        self._push_mru(path)
        self._save_guidelines()  # the copy keeps the page's guidelines
        self._update_title()
        # a Simulate tab following this document shows the new file now
        with contextlib.suppress(Exception):
            from ..simulate.window import simulate_window

            sw = simulate_window(create=False)
            if sw is not None:
                sw.document_renamed(self.doc, path)
        new_dir = os.path.dirname(os.path.abspath(path))
        if os.path.normcase(new_dir) != os.path.normcase(old_dir):
            self.library_dock.set_project_dir(new_dir)
            self.tags_dock.set_project_dir(new_dir)
            self._rebuild_labels_menu()
        notes = [n for n in (self._pop_builder_note(), self._pop_sa_note(), self._outside_note()) if n]
        self._saved_notices(self.doc)
        from ..notify import notify

        notify(
            self,
            saved_message(path, "", notes),
            "warning" if notes else "success",
            detail=path,
            key="save:%s" % path,
        )
        self._update_breadcrumb()
        return True

    def _save_element_as(self) -> bool:
        """Save element As: into another library and/or under another name
        (the original element stays as it was on disk)."""
        doc = self.doc
        ext = os.path.splitext(doc.path)[1].lower().lstrip(".")
        from ..dialog.library_dialogs import CopyToLibraryDialog

        advice = None
        if doc.kind == "supergenie":
            from ..dialog.supergenie_dialogs import supergenie_name_advice as advice
        dlg = CopyToLibraryDialog(
            os.path.dirname(os.path.abspath(doc.path)),
            self,
            exts=(ext,),
            library=doc.path,
            element=doc.element_name,
            title="Save %s As" % doc.display_name,
            name_advice=advice,
            **self._library_dialog_hooks(),
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return False
        lib, name = dlg.values()
        if not lib or not name:
            return False
        if not self._save_element_to(lib, name):
            return False
        if self.doc is doc:
            self._save_guidelines()  # the copy keeps the element's guidelines
        self._update_title()
        from ..notify import notify

        notify(
            self,
            "Saved %s into %s." % (doc.display_name, os.path.basename(lib)),
            "success",
            detail=lib,
            key="save:%s|%s" % (lib, name),
        )
        return True

    def _unsaved_in_compile(self, compiler) -> list:
        """The other documents, in every editor window, with unsaved edits that the compile of
        this page's project reads (pages and library elements of its include chain)."""
        try:
            from ...io import tagscope

            here = os.path.dirname(os.path.abspath(self.doc.path))
            root = compiler.root_project_for(here)
            dirs = {path_key(d) for d in tagscope.scope(here, root).dirs()}
        except Exception:  # noqa: BLE001 -- no project scope: this folder only
            _note_exc("workspace._unsaved_in_compile")
            dirs = {folder_key(self.doc.path)}
        out = []
        for win in editor_windows():
            for e in list(win._docs):
                d = e["doc"]
                if d is self.doc or not getattr(d, "dirty", False):
                    continue
                if folder_key(d.path) in dirs:
                    out.append(d)
        return out

    def _settle_other_unsaved(self, compiler) -> bool:
        """Compile reads the project from disk: other unsaved pages / genies of it would be
        compiled as they were last saved. Ask: Save All and Compile / Compile
        Saved Files / Cancel. False = do not compile."""
        others = self._unsaved_in_compile(compiler)
        if not others:
            return True
        names = [getattr(d, "display_name", "") or os.path.basename(d.path) for d in others]
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Compile")
        box.setText(
            "%d other open %s of this project %s unsaved changes. The compiler reads "
            "the saved files."
            % (len(others), "file" if len(others) == 1 else "files", "has" if len(others) == 1 else "have")
        )
        box.setInformativeText("\n".join(names[:12]) + ("\n..." if len(names) > 12 else ""))
        save_all = box.addButton("Save All and Compile", QtWidgets.QMessageBox.AcceptRole)
        saved = box.addButton("Compile Saved Files", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(save_all)
        box.exec()
        if box.clickedButton() is saved:
            return True
        if box.clickedButton() is not save_all:
            return False
        self.save_all()
        left = [d for d in others if getattr(d, "dirty", False)]
        if left:
            from ..names import plural
            from ..notify import notify

            notify(
                self,
                "Not compiled: %s could not be saved." % plural(len(left), "file"),
                "warning",
                key="compile",
            )
            return False
        return True

    #: seconds the most recent page's project is remembered for (read on every enable pass)
    COMPILE_TARGET_TTL = 2.0

    def _compile_target(self):
        """(project folder, page path or '') that Compile and Run act on: the open page's
        folder (File > Compile is enabled only for a page in a project), or with no page open
        the project of the most recent page; None when there is none."""
        doc = self.doc
        if doc is not None:
            page = "" if isinstance(doc, ElementDocument) else doc.path
            return os.path.dirname(os.path.abspath(doc.path)), page
        import time

        memo = self.__dict__.get("_compile_target_memo")
        if memo is not None and time.monotonic() - memo[0] < self.COMPILE_TARGET_TTL:
            return memo[1]
        target = None
        recent = self.recent_pages()
        if recent:
            pdir = os.path.dirname(os.path.abspath(recent[0]))
            try:
                if any(n.lower() == "pages.dbf" for n in os.listdir(pdir)):
                    target = (pdir, "")
            except OSError:  # swallow-ok: the folder is gone: nothing to compile
                target = None
        self._compile_target_memo = (time.monotonic(), target)
        return target

    def compile_project_command(self) -> None:
        """File > Compile (Alt+F10, its buttons and palette row): the results window opens
        as Settings > Pages and libraries > Run 'After Compile, show the results' says (by
        default with errors only)."""
        from ..prefs.window_options import compile_results

        self.compile_project(show=compile_results())

    def compile_project(self, show: str = "always"):
        """Compile the project and list its messages."""
        target = self._compile_target()
        if target is None:
            return None
        pdir, page_path = target
        from ..notify import notify

        if self._outside_install(pdir, "compiled"):
            return None
        if self.doc is not None and self.doc.dirty and not self.save():
            # the compiler reads the project on disk: compiling now would
            # build (and Run would start) the OLD version of this page
            notify(
                self,
                "Not compiled: %s was not saved, and the compiler reads the version "
                "on disk." % os.path.basename(self.doc.path),
                "warning",
                key="compile",
            )
            return None
        from ...backend import compiler

        if self.doc is not None and not self._settle_other_unsaved(compiler):
            return None
        from ..jobs import tasks, taskbar
        from ..prefs.job_options import poll_ms
        from ..jobs.job_progress import JobProgress

        token = None
        try:
            root = compiler.root_project_for(pdir)
            # (the window says the time so far; between two looks at the compiler the
            # event loop runs, so the window moves and Cancel is read at once, instead of
            # the GUI thread sleeping)
            prog = JobProgress(self, "Compile", "Compiling project %s…" % root)
            token = tasks.begin("compile", "Compiling %s…" % root, cancel=prog.cancel, window=self)
            bar = taskbar.ProgressReporter(prog.prog, self)

            def tick():
                return prog.pulse()

            def wait(seconds):
                prog.wait(int(max(poll_ms(), seconds * 1000)))

            try:
                result = compiler.compile_project(root, on_tick=tick, wait=wait, poll=poll_ms() / 1000.0)
            finally:
                bar.finish()
                prog.close()
        except compiler.CompileError as e:
            tasks.end(token, ok=False, summary="Compile of %s did not run." % os.path.basename(pdir))
            from ..messages import error_box

            error_box(
                self,
                "Compile",
                str(e),
                next_steps=("Check that Plant SCADA is installed on this computer, then compile again.",),
            )
            return None
        except Exception:
            tasks.end(token, ok=False)
            raise
        page = os.path.splitext(os.path.basename(page_path))[0] if page_path else ""
        from ..names import plural

        ok = result.outcome == "completed" and not result.errors
        from ..jobs.run_stats import duration_text

        text = "Compile of %s %s in %s: %s, %s." % (
            result.project,
            result.outcome,
            duration_text(result.seconds),
            plural(len(result.errors), "error"),
            plural(len(result.warnings), "warning"),
        )
        tasks.end(token, ok=ok, tell=False)  # (the message below says it)
        opens = bool(
            show == "always"
            or (show == "problems" and result.warnings)
            or result.errors
            or result.outcome != "completed"
        )
        acts = () if opens else [("Show Results", lambda r=result, p=page: self._show_compile_results(r, p))]
        notify(
            self,
            text,
            "success" if ok and not result.warnings else ("warning" if ok else "error"),
            actions=acts,
            key="compile",
        )
        if opens:
            self._show_compile_results(result, page)
        return result

    def _outside_install(self, pdir: str, verb: str) -> bool:
        """True, and said, when ``pdir`` is not a project of the active install: Compile and
        Run would build or start the registered project of the same name instead
        (backend/compiler.in_active_install). ``verb``: 'compiled' or 'run'."""
        from ...backend import compiler

        try:
            if compiler.in_active_install(pdir):
                return False
        except Exception:  # noqa: BLE001  # swallow-ok: the compile reports a broken install
            return False
        from ...io.citect_config import live_user_dir
        from ..notify import notify

        notify(
            self,
            "Not %s: %s is not a project of the active Plant SCADA install. Compile "
            "and Run work on the projects in %s. Open the page from there."
            % (verb, pdir, live_user_dir() or "the install's User folder"),
            "warning",
            key="compile",
        )
        return True

    def _show_compile_results(self, result, page: str = "") -> None:
        """The compile results window (one per editor window: the next compile replaces it)."""
        from ..dialog.dialogs import CompileResultsDialog

        old = self._dialogs.get("compile")
        if old is not None:
            self._drop_modeless(old)
        self._show_modeless(CompileResultsDialog(result, page, self), "compile", scope="window")
