"""Run Update Pages in helper processes."""

from __future__ import annotations

import os
import shutil
import time
from typing import Callable, List, Optional

from PySide6 import QtCore


def sweep_old_runs() -> int:
    """Remove the run folders (``<data>\\updatepages\\<editor pid>-<ms>``) of editors that
    ended during a run (a crash, a kill: _end never ran). A folder whose editor still runs is
    left: its run may be going on. The helpers end with their editor (the job object), so
    nothing writes into a dead editor's folder. Returns the folders removed."""
    import re
    from ...infra.paths import data_dir, pid_alive

    root = data_dir("updatepages")
    try:
        names = os.listdir(root)
    except OSError:  # swallow-ok: no run folder yet
        return 0
    n = 0
    for name in names:
        m = re.fullmatch(r"(\d+)-\d+", name)
        if m is None or int(m.group(1)) == os.getpid() or pid_alive(int(m.group(1))):
            continue
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        n += 1
    return n


class UpdatePagesRun(QtCore.QObject):
    """One Update Pages run."""

    progress = QtCore.Signal(int, int, str, str)
    finished = QtCore.Signal(dict)

    #: the run going on (one at a time; Pack Libraries waits for it)
    active: Optional["UpdatePagesRun"] = None
    #: Update Pages' chain is saving library genies (before a run is :attr:`active`): a
    #: second Update Pages and Pack Libraries wait for it too
    chain_busy: bool = False
    #: the chain after a genie or symbol save is running in its worker ('lib.element' of the
    #: element saved; ui/window/library._update_closed_pages): Update Pages and Pack
    #: Libraries wait for it, and a chain asked for meanwhile waits in :attr:`users_queue`
    users_busy: str = ""
    #: a run in the editor's process (Update Users, Update Pages with or without its chain)
    #: has not brought the open tabs up to date yet: Update Pages' pages, the open pages'
    #: step, the element tabs' updates queued (LibraryCommands._follow_run_saves). Simulate's
    #: library changes wait for it as for :attr:`users_busy` (ui/simulate/live_reload.
    #: library_run_going), and so do the other runs (:meth:`busy_text`) and a chain waiting
    #: in :attr:`users_queue`: one run at a time, its open tabs' step included
    tabs_following: bool = False
    #: the run that holds :attr:`tabs_following` ('Update Pages', 'Update Users'): the busy
    #: text names it ('' when not known)
    tabs_run: str = ""
    #: [(window, (library, element, 'lib.element'))] the chains waiting for their turn
    users_queue: list = []
    #: a look for the next waiting chain is due (one timer at a time)
    users_retry: bool = False

    @classmethod
    def busy_text(cls, what: str) -> str:
        """'' when no run that writes libraries or pages goes on, else why ``what`` waits
        ('Run Update Pages', 'Pack the libraries')."""
        if cls.users_busy:
            return (
                "The library genies and pages that use %s are being updated. %s when "
                "that has finished." % (cls.users_busy, what)
            )
        if cls.active is not None or cls.chain_busy:
            return "Update Pages is running. %s when it has finished." % what
        if cls.tabs_following:
            # (a run's open tabs' step: its progress window lets events through. Named: Update
            # Pages without its chain updates no library)
            return cls.following_tabs_message("%s when it has finished." % what)
        return ""

    @classmethod
    def following_tabs_message(cls, then: str) -> str:
        """'Update Pages is bringing the open tabs up to date.' and ``then``: the run that
        holds :attr:`tabs_following`."""
        if cls.tabs_run:
            return "%s is bringing the open tabs up to date. %s" % (cls.tabs_run, then)
        return "The open tabs are being brought up to date. %s" % then

    def __init__(
        self,
        parent,
        dirs: List[str],
        job: dict,
        skip_paths: Optional[Callable[[], List[str]]] = None,
        parts: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        super().__init__(parent)
        from .run_stats import RunStats

        self._dirs = list(dirs)
        self._job = dict(job)
        self._skip = skip_paths or (lambda: [])
        self._parts = parts
        self._helper = None
        self._folder = ""
        self._answers: List[dict] = []
        self.pages: List[str] = []
        self.unregistered: List[str] = []
        #: the run's pages left out because they were open in an editor when it started
        self.open_at_start: List[str] = []
        self.parts = 1
        self.stopping = False
        self.done = False
        #: the progress figures (the workers set when the helpers have started)
        self.stats = RunStats.from_settings(clock=clock)

    def start(self) -> bool:
        """Start the run; False when another run is going on or the helpers could not
        start (logged)."""
        from ...workers import genie_worker as GW
        from ...infra.paths import data_dir
        from ...io import updatepages
        from ..prefs.settings import update_parts

        if UpdatePagesRun.active is not None:
            return False
        try:
            skip = list(self._skip())
            run = {os.path.normcase(os.path.abspath(d)) for d in self._dirs}
            self.open_at_start = [
                p for p in skip if os.path.normcase(os.path.dirname(os.path.abspath(p))) in run
            ]
            self.pages, self.unregistered = updatepages.run_page_list(self._dirs, skip=skip)
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("update_run.start/pages")
            return False
        UpdatePagesRun.active = self
        if not self.pages:
            QtCore.QTimer.singleShot(0, self, lambda: self._end(None))
            return True
        # (a count chosen in the window is capped like Automatic: at least
        # opt/update_pages_per_helper pages a helper)
        n = update_parts(len(self.pages), self._parts)
        self.parts = n = max(1, min(int(n), len(self.pages)))
        sweep_old_runs()
        self._folder = data_dir("updatepages", "%d-%d" % (os.getpid(), int(time.time() * 1000)))
        claims = os.path.join(self._folder, "claims")
        try:
            os.makedirs(claims, exist_ok=True)
        except OSError:
            from ...infra import logsetup

            logsetup.note_exception("update_run.start/folder")
            UpdatePagesRun.active = None
            return False
        job = self._job
        self._request = {
            "format": GW.FORMAT,
            "mode": "pages",
            "pages": list(self.pages),
            "claims": claims,
            "templates": job.get("templates", True),
            "genies": job.get("genies", True),
            "symbols": job.get("symbols"),
            "page_properties": job.get("page_properties", False),
            "force": bool(job.get("force")),
            "backup": job.get("backup"),
            "builder_open": list(job.get("builder_open") or ()),
            "defer_composites": n > 1,
        }
        from .helper_job import HelperJob, HelperJobs

        if n == 1:
            helper = HelperJob(
                self,
                GW.users_command,
                self._request,
                self._folder,
                skip_paths=self._skip,
                label="Update Pages",
                task=False,
                stream=True,
            )
        else:
            # (fewer_ok: a part that cannot start leaves the started ones to go on alone;
            # each takes its pages by claims, so they still do them all, and the run's
            # folder stays until they have ended)
            helper = HelperJobs(
                self,
                GW.users_command,
                self._request,
                self._folder,
                n,
                len(self.pages),
                lambda answers: {"parts": answers},
                skip_paths=self._skip,
                label="Update Pages",
                stream=True,
                task=False,
                fewer_ok=True,
            )
        helper.progress.connect(lambda i, total, name: self._progressed(i, total, name, "check"))
        helper.finished.connect(self._checked)
        if not helper.start():
            # (no helper process was started: nothing can use the folder)
            UpdatePagesRun.active = None
            shutil.rmtree(self._folder, ignore_errors=True)
            return False
        if isinstance(helper, HelperJobs) and helper.started < n:
            from ...infra import logsetup

            logsetup.get("update-pages").warning(
                "Update Pages: %d of %d helper processes started", helper.started, n
            )
            self.parts = helper.started
        self._helper = helper
        self.stats.workers = self.parts
        self.stats.begin_phase("check", len(self.pages))
        return True

    def _progressed(self, i: int, n: int, name: str, phase: str) -> None:
        """A helper's progress: the figures take the records written since the last look
        (the pages finished), then ``progress`` is emitted."""
        self.take_records()
        self.progress.emit(i, n, name, phase)

    def take_records(self) -> None:
        """The records the helpers appended since the last look, into :attr:`stats`: the
        pages done in the phase that runs (phase 1's records, 'deferred' ones too, or the
        composite pass's)."""
        helper = self._helper
        if helper is None:
            return
        try:
            recs = helper.new_records()
        except Exception:  # noqa: BLE001 -- the figures never stop a run
            from ...infra import logsetup

            logsetup.note_exception("update_run.take_records")
            return
        if recs:
            self.stats.add_records(recs)
            self.stats.update(self.stats.done + len(recs))
        else:
            self.stats.update(self.stats.done)

    def stop(self) -> None:
        """Every helper stops after the page it is on; the composite pass is not
        started."""
        if self.done:
            return
        self.stopping = True
        if self._helper is not None:
            self._helper.stop()

    def _checked(self, res: dict) -> None:
        """Phase 1 ended: start the composite pass on the deferred pages, or end."""
        from ...workers import genie_worker as GW

        self.take_records()  # (the last part's last pages: phase 1's)
        self._answers = list(res.get("parts") or [res]) if isinstance(res, dict) else []
        final: dict = {}
        for a in self._answers:
            bad = bool(a.get("error")) or a.get("format") != GW.FORMAT
            for r in (a.get("streamed") or []) if bad else (a.get("records") or a.get("streamed") or []):
                final[int(r.get("i", -1))] = r
        deferred = [
            self.pages[i]
            for i in sorted(final)
            if 0 <= i < len(self.pages) and final[i].get("outcome") == "deferred"
        ]
        if self.stopping or not deferred:
            self._end(None)
            return
        from .helper_job import HelperJob

        req = dict(self._request, only=deferred, defer_composites=False)
        req.pop("claims", None)
        helper = HelperJob(
            self,
            GW.users_command,
            req,
            self._folder,
            skip_paths=self._skip,
            label="Update Pages",
            task=False,
            stream=True,
        )
        helper.progress.connect(lambda i, total, name: self._progressed(i, total, name, "compose"))
        helper.finished.connect(self._end)
        self._helper = helper
        # (one helper composes them: its pages over the time it takes)
        self.stats.workers = 1
        self.stats.begin_phase("compose", len(deferred))
        if not helper.start():
            self._end({"error": "the composite genie pass could not start"})

    def _end(self, phase2: Optional[dict]) -> None:
        from ...workers import genie_worker as GW
        from ...io import updatepages

        claimed = updatepages.claimed(os.path.join(self._folder, "claims")) if self._folder else set()
        merged = GW.merge_update_answers(
            self._answers, self.pages, self.unregistered, claimed, self._job.get("backup"), phase2=phase2
        )
        broken = [
            a
            for a in self._answers + ([phase2] if phase2 is not None else [])
            if a.get("error") or a.get("format") != GW.FORMAT
        ]
        if broken:
            # (a helper died: the database writes it left half done are rolled back now)
            try:
                from ...format import journal

                merged["recovered"] = list(merged.get("recovered") or ()) + journal.recover_stale()
            except Exception:  # noqa: BLE001
                from ...infra import logsetup

                logsetup.note_exception("update_run.end/recover")
        if self.stopping:
            merged["stopped"] = True
        merged["open_at_start"] = list(self.open_at_start)
        if self._folder:
            shutil.rmtree(self._folder, ignore_errors=True)
        self._helper = None
        self.done = True
        if UpdatePagesRun.active is self:
            UpdatePagesRun.active = None
        self.finished.emit(merged)
