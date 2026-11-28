"""Show what a page open found."""

from __future__ import annotations

from ..style.theme import set_note_role

from ...infra.paths import path_key

from ..names import plural

import contextlib
import logging
import os
import time
from typing import Optional

from PySide6 import QtCore, QtWidgets

from ...infra import logsetup
from ..prefs.settings import option
from ...editing import ui_state
from ..messages import inform

DISPLAY_OUT_OF_DATE = "The display of this page may be out of date. Use View > Refresh."


def _is_page_doc(doc) -> bool:
    from ...editing import EditableDocument, ElementDocument

    return isinstance(doc, EditableDocument) and not isinstance(doc, ElementDocument)


class UnresolvedReferencesDialog(QtWidgets.QDialog):
    """The library references of a page that did not resolve: reference,
    reason, objects, and the folders that were searched, with Copy."""

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        page = os.path.splitext(os.path.basename(report.path or ""))[0]
        self.setWindowTitle("Symbols Not Found - %s" % page if page else "Symbols Not Found")
        lay = QtWidgets.QVBoxLayout(self)
        n = len(report.unresolved)
        head = QtWidgets.QLabel(
            "%d library reference%s on %s %s not found. Include the project that has the "
            "library, or relink the objects."
            % (n, "" if n == 1 else "s", os.path.basename(report.path), "was" if n == 1 else "were")
        )
        head.setWordWrap(True)
        lay.addWidget(head)
        self.table = QtWidgets.QTableWidget(n, 4)
        self.table.setHorizontalHeaderLabels(["Reference", "Library file", "Reason", "Objects"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for r, u in enumerate(report.unresolved):
            for c, v in enumerate((u.reference, "%s.%s" % (u.library, u.ext), u.reason, str(u.count))):
                it = QtWidgets.QTableWidgetItem(v)
                if c == 2 and u.files:
                    it.setToolTip("Searched: " + "\n".join(u.files))
                self.table.setItem(r, c, it)
        # the Reason column takes the extra width (UI guide section 9); the
        # others fit their text as the window's style draws it
        from ..tables import enable_row_copy, make_resizable

        make_resizable(self.table, "UnresolvedReferencesDialog/references", stretch=2)
        enable_row_copy(self.table)
        lay.addWidget(self.table, 1)
        lay.addWidget(QtWidgets.QLabel("Folders searched, in order:"))
        self.folders = QtWidgets.QPlainTextEdit("\n".join(report.search_paths))
        self.folders.setReadOnly(True)
        self.folders.setMaximumHeight(160)
        lay.addWidget(self.folders)
        missing = [name for name, _via, dirs in report.includes if not dirs]
        if missing:
            warn = QtWidgets.QLabel("Included projects not found on this computer: %s" % ", ".join(missing))
            set_note_role(warn, "warn", "padding: 0 4px;")
            warn.setWordWrap(True)
            lay.addWidget(warn)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.btn_copy = bb.addButton("Copy", QtWidgets.QDialogButtonBox.ActionRole)
        self.btn_copy.clicked.connect(self.copy)
        self.btn_copy.setAutoDefault(False)
        # (a reason that points at the log comes with the button that opens it)
        from ...diag.open_report import REASON_UNREADABLE

        if any(u.reason == REASON_UNREADABLE for u in report.unresolved):
            self.btn_log = bb.addButton("Open Log", QtWidgets.QDialogButtonBox.ActionRole)
            self.btn_log.setToolTip(
                "Open ctgedit's activity log, which says why the library could not be read"
            )
            self.btn_log.setAutoDefault(False)
            from ..notify import open_log

            self.btn_log.clicked.connect(open_log)
        bb.button(QtWidgets.QDialogButtonBox.Close).setDefault(True)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.resize(760, 460)

    def text(self) -> str:
        rep = self.report
        out = ["Library references not found on %s:" % rep.path]
        out += ["  %s" % u.text() for u in rep.unresolved]
        for u in rep.unresolved:
            if u.files:
                out.append("  %s: searched %s" % (u.reference, ", ".join(u.files)))
        missing = [name for name, _via, dirs in rep.includes if not dirs]
        if missing:
            out.append("Included projects not found: %s" % ", ".join(missing))
        out.append("Folders searched (%d):" % len(rep.search_paths))
        out += ["  " + p for p in rep.search_paths]
        return "\n".join(out)

    def copy(self) -> str:
        t = self.text()
        QtWidgets.QApplication.clipboard().setText(t)
        return t


class DiagnosticsMixin:
    """MainWindow's diagnostics (see the module docstring)."""

    #: ms between display-check polls, and between tries while another runs
    DISPLAY_CHECK_DELAY_MS = 1500
    #: (normcase path, file stamp) of every page checked by this process
    _display_checked: set = set()

    # -- the status bar -----------------------------------------------------------
    def _build_diag_chips(self, lay) -> tuple:
        """The status bar's problem chips, into ``lay`` (the Problems field of
        ui/panels/status_items.py): 'N symbols not found', 'Display may be out of date'."""
        self._refs_chip = QtWidgets.QToolButton(self)
        self._refs_chip.setAutoRaise(True)
        self._refs_chip.setVisible(False)
        set_note_role(self._refs_chip, "warn", "padding: 0 4px;")
        self._refs_chip.setAccessibleName("Symbols not found")
        self._refs_chip.clicked.connect(self._show_unresolved)
        lay.addWidget(self._refs_chip)
        self._display_chip = QtWidgets.QToolButton(self)
        self._display_chip.setAutoRaise(True)
        self._display_chip.setVisible(False)
        set_note_role(self._display_chip, "warn", "padding: 0 4px;")
        self._display_chip.setAccessibleName("Display may be out of date")
        self._display_chip.clicked.connect(self._on_display_chip)
        lay.addWidget(self._display_chip)
        return self._refs_chip, self._display_chip

    @staticmethod
    def _diag_report_current(doc) -> bool:
        """Whether ``doc``'s open report is of the page as derived now (open_report.report_for
        then reads no record)."""
        rep = getattr(getattr(doc, "ui", None), "diag_report", None)
        return rep is not None and rep.serial == getattr(doc, "derive_serial", -1)

    def _diag_after_paint(self, doc):
        """What _activate_doc leaves for after the page painted: the switch's log line (when
        it would have written one now) and the chips of the page then shown."""
        log = _is_page_doc(doc) and bool(ui_state(doc).diag_open_logged)

        def run() -> None:
            if log:
                self._log_switch(doc)
            self._update_diag_chips()

        return run

    def _hide_diag_chips(self) -> None:
        """Hide the status bar's diagnostic chips until _update_diag_chips shows the page's
        (they told of the page shown before)."""
        for name in ("_refs_chip", "_display_chip"):
            chip = getattr(self, name, None)
            if chip is not None:
                chip.setVisible(False)

    def _update_diag_chips(self) -> None:
        doc = getattr(self, "doc", None)
        chip = getattr(self, "_refs_chip", None)
        rep = None
        if chip is not None:
            if _is_page_doc(doc):
                from ...diag import open_report as OR

                rep = OR.report_for(doc)
            n = len(rep.unresolved) if rep is not None else 0
            chip.setVisible(n > 0)
            if n:
                objs = sum(u.count for u in rep.unresolved)
                chip.setText("⚠ %s not found" % (plural(n, "symbol")))
                chip.setToolTip(
                    "%d library reference%s (%d object%s) not found. Click to see "
                    "the list." % (n, "" if n == 1 else "s", objs, "" if objs == 1 else "s")
                )
        disp = getattr(self, "_display_chip", None)
        if disp is not None:
            out = ui_state(doc).display_check if doc is not None else None
            # (about the page as it was derived then: a re-derive may have mended it)
            show = bool(
                out is not None
                and out.ok
                and not out.identical
                and out.serial == getattr(doc, "derive_serial", 0)
            )
            disp.setVisible(show)
            if show:
                disp.setText("⚠ Display may be out of date")
                disp.setToolTip(out.text() + "\n\nClick to redraw the page (View > Refresh).")

    def _show_unresolved(self) -> Optional[UnresolvedReferencesDialog]:
        from ...diag import open_report as OR

        rep = OR.report_for(self.doc) if _is_page_doc(self.doc) else None
        if rep is None or not rep.unresolved:
            return None
        old = getattr(self, "_unresolved_dlg", None)
        if old is not None:
            with contextlib.suppress(RuntimeError):
                old.close()
        dlg = UnresolvedReferencesDialog(rep, self)
        dlg.setModal(False)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.show()
        self._unresolved_dlg = dlg
        return dlg

    def _on_display_chip(self) -> None:
        self._refresh_view()

    # -- opens, switches, closes ----------------------------------------------------
    def _open_page_file(self, path: str, checks: bool = False):
        """EditableDocument.open with the open's swallowed errors counted and
        its time kept (the open's log line reports both). ``checks`` = the open checks
        follow (_post_open_checks): a remembered genie update is applied before the
        page's derive (files._genie_answer_before_derive)."""
        from ...editing import EditableDocument

        t0 = time.perf_counter()
        counts = logsetup.begin_error_scope()
        hook = getattr(self, "_genie_answer_before_derive", None) if checks else None
        try:
            doc = EditableDocument.open(path, before_derive=hook)
        except Exception as e:
            logsetup.get("open").warning("could not open %s: %s: %s", path, type(e).__name__, e)
            raise
        finally:
            logsetup.end_error_scope(counts)
        doc.ui.open_diag = {
            "open_s": time.perf_counter() - t0,
            "swallowed": dict(counts),
            "derive_s": getattr(doc, "_derive_seconds", None),
        }
        return doc

    def _log_page_open(self, doc, verb: str = "opened") -> None:
        """The page open's line in the activity log (after the open checks)."""
        if not _is_page_doc(doc):
            return
        try:
            from ...diag import open_report as OR

            rep = OR.report_for(doc)
            if rep is None:
                return
            diag = ui_state(doc).open_diag or {}
            text = OR.open_log_text(
                doc,
                rep,
                open_s=diag.get("open_s"),
                swallowed=diag.get("swallowed"),
                verb=verb,
                derive_s=diag.get("derive_s"),
            )
            logsetup.get("open").info(text)
            logsetup.remember(
                "open",
                "%s: %d objects, %s%s"
                % (
                    doc.path,
                    rep.objects,
                    rep.unresolved_text(3),
                    "; unsaved changes after the open checks" if doc.dirty else "",
                ),
            )
            doc.ui.diag_open_logged = True
        except Exception:  # noqa: BLE001 -- a diagnostic never breaks an open
            logsetup.note_exception("diag_ui._log_page_open", page=getattr(doc, "path", ""))
        if doc is getattr(self, "doc", None):
            self._update_diag_chips()

    def _log_switch(self, doc) -> None:
        if not _is_page_doc(doc) or not ui_state(doc).diag_open_logged:
            return
        try:
            from ...diag import open_report as OR

            rep = OR.report_for(doc)
            n = len(rep.unresolved) if rep is not None else 0
            state = "unsaved changes" if doc.dirty else "unmodified"
            text = "switched to %s (%d objects, %d reference(s) not found, %s)" % (
                doc.path,
                len(doc.page.objects),
                n,
                state,
            )
            logsetup.get("open").info(text)
            logsetup.remember("switch", text[len("switched to ") :])
        except Exception:  # noqa: BLE001
            logsetup.note_exception("diag_ui._log_switch")

    def _log_close(self, doc) -> None:
        if not _is_page_doc(doc):
            return
        try:
            state = "its unsaved changes discarded" if doc.dirty else "no unsaved changes"
            logsetup.get("open").info("closed %s (%s)", doc.path, state)
            logsetup.remember("close", "%s (%s)" % (doc.path, state))
        except Exception:  # noqa: BLE001
            logsetup.note_exception("diag_ui._log_close")
        chk = getattr(self, "__dict__", {}).get("_display_running")
        if chk is not None and chk[0] is doc:
            chk[1].cancel()
            self._display_running = None
            from ..jobs import tasks

            tasks.drop(self.__dict__.pop("_display_task", None))  # (nothing to tell)
        q = self.__dict__.get("_display_queue") or []
        q[:] = [d for d in q if d is not doc]

    # -- automatic changes of the open checks --------------------------------------------
    def _auto_step(self, doc, what: str, fn, *args):
        """Run the open check's mutator ``fn`` and log what it changed (the
        records, the undo step it made, and -- a WARNING -- when the page's
        art count changed). Exceptions pass through after being logged."""
        from ...diag import open_report as OR

        try:
            snap = OR.change_snapshot(doc)
        except Exception:  # noqa: BLE001
            logsetup.note_exception("diag_ui._auto_step")
            snap = None
        steps = len(getattr(doc, "_undo", ()) or ())
        try:
            res = fn(*args)
        except Exception:
            logsetup.note_exception("open check: " + what, page=getattr(doc, "path", ""))
            raise
        note = getattr(self, "_note_auto_change", None)
        if note is not None:
            undo = getattr(doc, "_undo", None) or []
            note(doc, what, undo[-1] if len(undo) > steps else None, changed=len(undo) > steps)
        if snap is not None:
            try:
                ch = OR.describe_change(doc, snap)
                if ch["changed"] or ch["label"] is not None:
                    msg = (
                        "automatic change when opening %s: %s; undo step %s, %d record(s) "
                        "changed (%d -> %d records), %d -> %d objects with art%s"
                        % (
                            doc.path,
                            what,
                            "'%s'" % ch["label"] if ch["label"] else "none",
                            ch["changed"],
                            ch["before"],
                            ch["after"],
                            ch["art_before"],
                            ch["art_after"],
                            "; the page now has unsaved changes" if ch["dirty"] else "",
                        )
                    )
                    art_changed = ch["art_before"] != ch["art_after"]
                    logsetup.get("open").log(
                        logging.WARNING if art_changed else logging.INFO,
                        msg + ("; the page's art changed" if art_changed else ""),
                    )
                    logsetup.remember(
                        "auto",
                        "%s: %s (%d records%s)"
                        % (
                            os.path.basename(doc.path),
                            what,
                            ch["changed"],
                            ", art %d -> %d" % (ch["art_before"], ch["art_after"]) if art_changed else "",
                        ),
                    )
            except Exception:  # noqa: BLE001
                logsetup.note_exception("diag_ui._auto_step")
        return res

    # -- the display check -----------------------------------------------------------------
    @staticmethod
    def _display_check_problem(doc) -> str:
        """'' when ``doc``'s display can be checked against its FILE (a page,
        unmodified since it was opened, the file still the one it read)."""
        if not _is_page_doc(doc):
            return "not a page"
        if doc.dirty or doc.can_undo():
            return "the page has been changed since it was opened"
        if getattr(doc, "damage", None):
            return "the page file is damaged"
        stamp = doc.disk_stamp(doc.path)
        if stamp is None or stamp != getattr(doc, "_disk_stamp", None):
            return "the page file changed since it was opened"
        return ""

    @staticmethod
    def _display_key(doc):
        return (path_key(doc.path), getattr(doc, "_disk_stamp", None))

    @staticmethod
    def _display_memo_key(doc) -> str:
        """The persistent key of a page's display verdict: the file version, the code
        version and the label mode (a new build or another label mode checks again)."""
        import hashlib
        from ... import buildinfo
        from ...io import reader

        stamp = getattr(doc, "_disk_stamp", None)
        raw = repr(
            (
                path_key(doc.path),
                stamp,
                buildinfo.version_string(),
                reader.get_label_mode(),
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _display_memo_path() -> str:
        from ...infra.paths import data_dir

        return os.path.join(data_dir(), "display_checks.json")

    def _display_known_identical(self, doc) -> bool:
        import json

        try:
            with open(self._display_memo_path(), encoding="utf-8") as fh:
                return self._display_memo_key(doc) in json.load(fh)
        except OSError, ValueError, TypeError:
            return False

    def _remember_display_identical(self, doc) -> None:
        import json
        import time

        p = self._display_memo_path()
        try:
            with open(p, encoding="utf-8") as fh:
                memo = dict(json.load(fh))
        except OSError, ValueError, TypeError:
            memo = {}
        memo[self._display_memo_key(doc)] = time.time()
        memo = dict(sorted(memo.items(), key=lambda kv: kv[1])[-2000:])
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(memo, fh)
            os.replace(tmp, p)
        except OSError:
            pass

    def _display_check_wanted(self, doc) -> bool:
        if os.environ.get("CTG_NO_DISPLAY_CHECK") == "1" or not option("display_check"):
            return False
        if getattr(getattr(doc, "ui", None), "version_view", None) is not None:
            return False  # (an earlier version: the page's file is not what it shows)
        if self._display_check_problem(doc):
            return False
        if self._display_key(doc) in DiagnosticsMixin._display_checked:
            return False
        # (this file version showed identical before, with this build: not again)
        return not self._display_known_identical(doc)

    def _queue_display_check(self, doc) -> None:
        """Check ``doc``'s display in a separate process when the editor is
        idle (after the .ctF check), one page at a time, each file version once."""
        if not self._display_check_wanted(doc):
            return
        q = self.__dict__.setdefault("_display_queue", [])
        if not any(d is doc for d in q):
            q.append(doc)
        self._display_timer_start()

    def _display_timer_start(self) -> None:
        t = self.__dict__.get("_display_timer")
        if t is None:
            t = self._display_timer = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._display_tick)
        if not t.isActive():
            t.start(self.DISPLAY_CHECK_DELAY_MS)

    def _display_tick(self) -> None:
        run = self.__dict__.get("_display_running")
        if run is not None:
            doc, chk, key = run
            res = chk.poll()
            if res is None:
                self._display_timer_start()
                return
            self._display_running = None
            from ..jobs import tasks

            # (a check that runs by itself: its result is in the Problems chip and the log)
            tasks.end(self.__dict__.pop("_display_task", None), ok="error" not in res, tell=False)
            try:
                self._display_result(doc, chk, key, res)
            except Exception:  # noqa: BLE001 -- a diagnostic never breaks the editor
                logsetup.note_exception("diag_ui._display_result", page=doc.path)
        q = self.__dict__.get("_display_queue") or []
        open_docs = [e["doc"] for e in getattr(self, "_docs", [])]
        q[:] = [d for d in q if any(d is x for x in open_docs) and self._display_check_wanted(d)]
        jobs = self.__dict__.get("_genie_jobs")
        genie_busy = jobs is not None and jobs.busy()
        if q and not self.__dict__.get("_display_manual") and not genie_busy:
            # the .ctF trust checks go first (a save waits for them); a genie update of
            # the page first too (one helper process at a time)
            from ...editing import CtfPrecheck

            ctf_q = self.__dict__.get("_ctf_queue") or []
            if not CtfPrecheck.busy() and not ctf_q:
                from ...diag.display_check import DisplayCheck

                doc = q[0]
                chk = DisplayCheck(doc.path)
                if chk.start():
                    q.pop(0)
                    key = self._display_key(doc)
                    DiagnosticsMixin._display_checked.add(key)
                    self._display_running = (doc, chk, key)
                    from ..jobs import tasks

                    tasks.drop(self.__dict__.pop("_display_task", None))  # (never two)
                    self._display_task = tasks.begin(
                        "display",
                        "Checking the display of %s…" % os.path.splitext(os.path.basename(doc.path))[0],
                        cancel=self._cancel_display_check,
                        window=self,
                        quiet=True,
                    )
        if q or self.__dict__.get("_display_running") is not None:
            self._display_timer_start()

    def _cancel_display_check(self) -> None:
        """The display check task's Cancel: its process stops (the page is not checked again
        this run)."""
        from ..jobs import tasks

        run = self.__dict__.get("_display_running")
        self._display_running = None
        if run is not None:
            with contextlib.suppress(Exception):
                run[1].cancel()
        # (the task goes even when its check already ended: it never stays behind)
        tasks.end(self.__dict__.pop("_display_task", None), tell=False)

    def _display_result(self, doc, chk, key, res: dict):
        """A finished automatic check: compare, record, log (moot when the page
        changed meanwhile). A check process that failed is logged, not redone
        here (it would block the editor)."""
        from ...diag.display_check import outcome_of

        if not any(e["doc"] is doc for e in getattr(self, "_docs", [])):
            return None
        if "error" in res:
            logsetup.get("check").warning(
                "the display check of %s could not run: %s", doc.path, res.get("error")
            )
            return None
        if self._display_check_problem(doc) or self._display_key(doc) != key:
            logsetup.get("check").info(
                "display check of %s dropped: %s",
                doc.path,
                self._display_check_problem(doc) or "the file changed",
            )
            return None
        out = outcome_of(doc.page, res, doc.path)
        self._record_display_outcome(doc, out)
        return out

    def _record_display_outcome(self, doc, out) -> None:
        out.serial = getattr(doc, "derive_serial", 0)
        doc.ui.display_check = out
        if out.identical and not out.error:
            self._remember_display_identical(doc)
        log = logsetup.get("check")
        if out.error:
            log.warning(out.text())
        elif out.identical:
            log.info(
                "display check of %s: identical to a fresh read (%d objects; %s, fresh read %.2f s)",
                doc.path,
                out.count,
                out.where,
                out.fresh_derive_s,
            )
            logsetup.remember("check", "%s: identical (%s)" % (doc.path, out.where))
        else:
            diag = ui_state(doc).open_diag or {}
            hist = logsetup.recent_events(("open", "switch", "close", "auto", "recovery"), 15)
            log.warning(
                "%s\n    page: %s\n    swallowed errors at its open here: %s\n    undo steps: "
                "%s\n    recent pages / automatic changes this run:\n      %s",
                out.text(),
                doc.path,
                logsetup.format_counts(diag.get("swallowed") or {}) or "none",
                " | ".join(logsetup.undo_labels(doc)) or "none",
                "\n      ".join(hist) or "(none)",
            )
            logsetup.remember("check", "%s: differs: %s" % (doc.path, "; ".join(out.differences[:3])))
            if doc is getattr(self, "doc", None):
                self.statusBar().showMessage(DISPLAY_OUT_OF_DATE, 20000)
        if doc is getattr(self, "doc", None):
            self._update_diag_chips()

    def _check_page_display(self, in_process: bool = False, show: bool = True):
        """Tools > Check Page Display: the display check of the current page
        now (a page with unsaved changes is checked against its current
        bytes). Returns the Outcome (None when there is nothing to check or
        it was cancelled)."""
        from ...diag.display_check import DisplayCheck, outcome_of

        doc = getattr(self, "doc", None)
        if not _is_page_doc(doc):
            inform(self, "Check Page Display", "Open a page first.")
            return None
        # (an earlier version's tab, File > Page History, is checked against its own bytes:
        # the page's file on disk is another version)
        version = getattr(getattr(doc, "ui", None), "version_view", None) is not None
        changed = bool(doc.dirty or doc.can_undo() or doc.changed_on_disk() or version)
        chk = DisplayCheck(doc.path, data=doc.serialize() if changed else None)
        res = None
        if not in_process:
            self._display_manual = True
            try:
                res = self._wait_for_check(chk)
            finally:
                self._display_manual = False
            if res is False:
                self.statusBar().showMessage("Check Page Display cancelled.", 4000)
                return None
        fallback = ""
        if res is None or "error" in res:
            if res is not None:
                fallback = str(res.get("error"))
                logsetup.get("check").warning(
                    "the display check process for %s failed (%s): checking in this process",
                    doc.path,
                    fallback,
                )
            res = chk.run_in_process()
        out = outcome_of(doc.page, res, doc.path)
        out.fallback = fallback
        self._record_display_outcome(doc, out)
        if show:
            self._show_display_outcome(out)
        return out

    def _wait_for_check(self, chk):
        """Run a check's process with a progress window."""
        from ..jobs import taskbar
        from ..prefs.job_options import poll_ms
        from ..jobs.job_progress import JobProgress

        prog = JobProgress(self, "Check Page Display", "Checking the page display…")
        bar = taskbar.ProgressReporter(prog.prog, self)
        try:
            while not chk.start():
                if not prog.wait(poll_ms()):
                    return False
            while True:
                res = chk.poll()
                if res is not None:
                    return res
                if not prog.wait(poll_ms()):
                    chk.cancel()
                    return False
        finally:
            bar.finish()
            prog.close()

    def _show_display_outcome(self, out) -> QtWidgets.QMessageBox:
        old = getattr(self, "_display_check_box", None)
        if old is not None:
            with contextlib.suppress(RuntimeError):
                old.close()
        icon = (
            QtWidgets.QMessageBox.Information if out.identical and out.ok else QtWidgets.QMessageBox.Warning
        )
        box = QtWidgets.QMessageBox(icon, "Check Page Display", out.text(), QtWidgets.QMessageBox.Ok, self)
        if out.ok and not out.identical:
            b = box.addButton("Refresh Now", QtWidgets.QMessageBox.ActionRole)
            b.clicked.connect(self._refresh_view)
            from ..notify import open_log

            lb = box.addButton("Open Log", QtWidgets.QMessageBox.HelpRole)
            lb.setToolTip("The difference is written to the activity log too")
            lb.clicked.connect(open_log)
            box.setInformativeText(DISPLAY_OUT_OF_DATE)
        box.setModal(False)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        box.show()
        self._display_check_box = box
        return box

    def _drop_art_caches(self) -> None:
        """View > Refresh: forget the process-wide art caches too (the parsed
        libraries -- a failed element read is cached as 'missing' --, the
        metafile pictures, the decoded bitmaps, the art twins, the genie
        element memo), so the page is drawn from the files again."""
        from ...infra import caches, noop_memo

        caches.clear("refresh")  # (every cache read from files: infra/caches.py)
        # (and what genie updates remembered on disk: the pages compose them again)
        with contextlib.suppress(Exception):
            noop_memo.forget_all()
        for e in getattr(self, "_docs", []):
            with contextlib.suppress(Exception):
                e["doc"].ui.display_check = None
