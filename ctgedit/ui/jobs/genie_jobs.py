"""The editor's side of updating genies on pages."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore

from ...workers import genie_worker as GW
from ...infra import logsetup

#: (a worker started with another one is out of date)
from ...infra.workers import environment_signature

log = logsetup.get("genies")


def _in_event_loop() -> bool:
    return QtCore.QThread.currentThread().loopLevel() > 0


#: the depth of a tab that does not nest the saved element (or nothing was saved)
NOT_NESTED = 99


def jobs_order() -> str:
    """Settings > Pages and libraries > When a page opens: the order the open tabs take a
    saved genie in after the tab shown (opt/genie_jobs_order): 'near' (the element tabs
    nearest the saved element first, then the pages, smaller ones first) or 'tabs' (tab
    order)."""
    from ..prefs.settings import app_settings

    v = str(app_settings().value("opt/genie_jobs_order", "near") or "near")
    return v if v in ("near", "tabs") else "near"


def helpers_count() -> int:
    """Settings > Pages and libraries > When a page opens: how many helpers update the
    open tabs' genies at a time (opt/genie_helpers, 1 to 8; the window's helper is the
    first). With one, the tabs take their turn in it, the page shown first."""
    from ..prefs.settings import app_settings

    try:
        n = int(app_settings().value("opt/genie_helpers", 3, type=int))
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        n = 3
    return max(1, min(8, n))


class WarmWorker:
    """The window's genie worker process, kept warm between requests."""

    IDLE_S = 300.0

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.last_used = time.monotonic()
        #: environment_signature() the process was started with
        self.env: Optional[tuple] = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def current(self) -> bool:
        """The worker composes with this process's settings as they are now."""
        return self.env == environment_signature()

    def submit(self, req: str, ans: str) -> None:
        from ...infra import workers

        if self.alive() and not self.current():
            # (the requests go one at a time: none is running in it now)
            log.info("genie worker: settings changed, starting a new one")
            self.stop()
        if not self.alive():
            self.env = environment_signature()
            self.proc = workers.start(GW.serve_command(), stdin=subprocess.PIPE)
        self.proc.stdin.write(("%s\t%s\n" % (req, ans)).encode("utf-8"))
        self.proc.stdin.flush()
        self.last_used = time.monotonic()

    #: idle seconds after which it ends when the machine is short of memory
    IDLE_LOW_MEMORY_S = 20.0
    #: available memory (MB) below which the machine counts as short of it
    LOW_MEMORY_MB = 1500

    def idle_stop(self, busy: bool) -> None:
        from ...infra import workers

        if busy:
            self.last_used = time.monotonic()
            return
        if not self.alive():
            return
        idle = time.monotonic() - self.last_used
        free = workers.free_memory_mb() if idle > self.IDLE_LOW_MEMORY_S else None
        if idle > self.IDLE_S or (free is not None and free < self.LOW_MEMORY_MB):
            self.stop()  # (its memory goes; the next request starts it again)

    def kill_now(self) -> None:
        """Ended at once: what it computes is not wanted any more (GenieJobs._free_worker);
        the next request starts it again."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            proc.stdin.close()  # (it ends at the end of its input)
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()


class GenieJob:
    """One page's update: its request and answer files, sent to the window's worker."""

    def __init__(
        self,
        doc,
        changes: dict,
        names: List[str],
        bindings: Optional[dict] = None,
        label: str = "update genies from library",
        template: bool = False,
        why: str = "",
        previous: Optional[dict] = None,
    ):
        self.doc = doc
        self.changes = changes
        self.names = names
        #: a genie save's versions before it: {(library, element): (its library file, the
        #: library's bytes before the save)}; the instances they reproduce take only the
        #: edit (genie_worker.apply_update). Copied into files when the job starts
        self.previous = dict(previous or {})
        self.prev_dir = ""
        #: the instances the previous versions did not reproduce (the answer's 'left')
        self.left = 0
        #: the update left a genie as stored (its library unreadable then, a composition
        #: that failed): the tab tries again on its next open or switch
        self.unfinished = False
        #: a genie dialog's OK ({"index", "values"}) instead of a library update
        self.bindings = bindings
        #: the page's template applied again (the open of a stale page) instead
        self.template = template
        self.label = label
        #: what queued it: 'save' (a library save brings the open tabs up to date), 'follow'
        #: (the end of a run that saved library genies again), '' (an open, a tab shown)
        self.why = why
        #: a 'follow' job of a tab that took the save already: no amber marks and no
        #: 'Updating' line while it runs, its step becomes part of the save's (the window's
        #: _genie_job_finished)
        self.quiet = False
        #: the tab's place in the queue after the tab shown (GenieJobs.order_key): (0 an
        #: element tab or 1 a page, how deep the saved element is nested in it, its records)
        self.rank = (1, NOT_NESTED, 0)
        #: a template step, a genie dialog's OK, an answer asked again: before other jobs
        self.urgent = template or bindings is not None
        #: the step on top of the page's undo history when its answer was applied
        self.below = None
        self.worker: Optional[WarmWorker] = None
        self.req = self.ans = ""
        self.attempts = 0
        #: the worker's environment_signature() when the job was sent
        self.env: Optional[tuple] = None
        #: its background task in the status bar (ui/jobs/tasks.py)
        self.task: Optional[int] = None
        #: its answer, when it landed, made an undo step (also one that changed only the
        #: page's link table: no range replaced, the page unsaved all the same)
        self.stepped = False
        #: an element tab's job its worker did not compose: the library no longer stored the
        #: block the tab held (genie_worker.compute_element; the window looks at the tab
        #: again, files._genie_job_finished)
        self.changed_on_disk = False

    def text(self) -> str:
        """What the status bar's tasks button says while it runs."""
        page = (
            getattr(self.doc, "display_name", "")
            if _is_element(self.doc)
            else os.path.splitext(os.path.basename(getattr(self.doc, "path", "") or ""))[0]
        )
        if self.template:
            return "Applying the template again on %s…" % page
        if self.bindings is not None:
            return "Applying the parameters of %s…" % (self.names[0] if self.names else page)
        if len(self.names) <= 2:
            what = " and ".join(self.names) or "the genies"
        else:
            what = "%d genies" % len(self.names)
        return "Updating %s on %s…" % (what, page)

    def start(self, worker: WarmWorker) -> None:
        from ...infra.paths import data_dir

        folder = data_dir("genieupdate")
        self.attempts += 1
        # (an element tab: the genies nested in it, ElementDocument.nested_update_request)
        element = self.doc.nested_update_request() if _is_element(self.doc) else None
        data = self.doc.serialize()
        # (the digest the editor keys the page's answer by: an old-form page's bytes here
        # are its upgraded form, EditableDocument.genie_page_digest)
        dig = None if element is not None else self.doc.genie_page_digest(data).hex()
        prev = None
        if self.previous and element is None:
            prev, self.prev_dir = previous_files(self.previous, folder)
        self.req = GW.write_request(
            folder,
            self.doc.path,
            data,
            self.changes,
            self.bindings,
            template=self.template,
            element=element,
            page_sha1=dig,
            previous=prev,
        )
        self.ans = self.req[:-5] + ".answer.json"
        self.worker = worker
        worker.submit(self.req, self.ans)
        self.env = worker.env

    def done(self) -> bool:
        return bool(self.ans) and (os.path.exists(self.ans) or self.worker is None or not self.worker.alive())

    def answer(self) -> dict:
        try:
            with open(self.ans, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError, ValueError:
            return {"error": "the worker gave no answer (it ended)"}

    def cleanup(self) -> None:
        for p in (self.req, self.req + ".page", self.ans):
            if p:
                with contextlib.suppress(OSError):
                    os.remove(p)
        if self.prev_dir:
            import shutil

            shutil.rmtree(self.prev_dir, ignore_errors=True)
            self.prev_dir = ""

    def kill(self) -> None:
        """The page closed: its answer is not wanted (the worker finishes the request; its
        files are removed now or when they appear)."""
        self.cleanup()


def previous_files(previous: dict, folder: str) -> tuple:
    """A genie job's ``previous`` ({key: (library file, bytes before the save)}) as files the
    worker reads: ({key: (library file, its copy)}, the folder of the copies). Each copy keeps
    its library's file name, in a folder of its own (as ElementDocument._snapshot_element
    composes it)."""
    import tempfile

    os.makedirs(folder, exist_ok=True)
    where = tempfile.mkdtemp(prefix="%d-prev-" % os.getpid(), dir=folder)
    out = {}
    for k, (lib_path, data) in previous.items():
        sub = os.path.join(where, str(len(out)))
        os.makedirs(sub)
        copy = os.path.join(sub, os.path.basename(lib_path))
        with open(copy, "wb") as fh:
            fh.write(data)
        out[k] = (lib_path, copy)
    return out, where


def _is_element(doc) -> bool:
    """``doc`` is a library element open in a tab (its job composes the genies nested in
    it: genie_worker.compute_element)."""
    return callable(getattr(doc, "nested_update_request", None)) and getattr(doc, "session", None) is not None


def _rank(doc, near: Optional[int] = None) -> tuple:
    """GenieJob.rank of ``doc``'s job: (0 an element tab or 1 a page, ``near`` = how deep
    the saved element is nested in it (an element tab that nests it directly first), its
    records (smaller first))."""
    try:
        n = len(doc.parsed.page.objects)
    except Exception:  # noqa: BLE001  # swallow-ok: a document with no records: first
        n = 0
    return (0 if _is_element(doc) else 1, NOT_NESTED if near is None else int(near), n)


def undo_top(doc):
    """The step on top of ``doc``'s undo history (None when it has none): an answer that
    made a step leaves another one there (also when the undo cap dropped an old step)."""
    undo = getattr(doc, "_undo", None)
    return undo[-1] if undo else None


def answer_facts(ans: dict) -> Optional[dict]:
    """The answer's genie_worker.answer_facts (None in an answer without them), its genie
    roots as {handle: signature} (None: an answer that listed the handles alone)."""
    if "fresh" not in ans and "roots" not in ans:
        return None
    roots = {}
    for r in ans.get("roots") or ():
        if isinstance(r, (list, tuple)) and len(r) == 2:
            roots[int(r[0])] = r[1]
        else:
            roots[int(r)] = None
    return {
        "fresh": list(ans.get("fresh") or ()),
        "roots": roots,
        "composed": [str(x).lower() for x in ans.get("composed") or ()],
    }


def apply_answer(doc, ans: dict, label: str = "update genies from library") -> Optional[int]:
    """Apply a worker answer to ``doc``: the ranges replaced, None when the page no longer
    holds what the answer replaces, or gained a genie copy the answer calls current (ask
    again), -1 on a worker error. The page as the user changed it since the request keeps
    one record per handle and its link table's changes (EditableDocument.apply_record_ops)."""
    if ans.get("error") or ans.get("format") != GW.FORMAT:
        log.warning(
            "genie update of %s failed: %s\n%s",
            getattr(doc, "path", ""),
            ans.get("error") or "format %r" % ans.get("format"),
            ans.get("trace", ""),
        )
        return -1
    ops = [
        (i1, i2, olds, [bytes.fromhex(x) for x in news], anchor)
        for i1, i2, olds, news, anchor in ans.get("ops", [])
    ]
    lt = ans.get("link_table")
    link = (bytes.fromhex(lt[0]), lt[1], bytes.fromhex(lt[2]), lt[3]) if lt else None
    if not ops and link is None:
        return 0
    return doc.apply_record_ops(ops, link, label=label, facts=answer_facts(ans))


def preapply_answer(doc):
    """Apply a remembered genie update answer before the page is derived."""
    changes = doc.genie_changes()
    if not changes:
        return None
    from ...infra import answer_memo, noop_memo

    memo_key = doc.genie_noop_key(changes)
    if memo_key is None or noop_memo.known(memo_key):
        return None
    ans = answer_memo.get(memo_key)
    if ans is None or ans.get("error") or ans.get("format") != GW.FORMAT:
        return None
    if (
        doc.supergenie_link()
        or doc.placeholder_genies()
        or doc.missing_library_objects()
        or doc.template_status()[0] == "stale"
    ):
        return None
    if doc.symbol_placements() and doc.stale_symbol_elements():
        return None
    ops = [
        (i1, i2, olds, [bytes.fromhex(x) for x in news], anchor)
        for i1, i2, olds, news, anchor in ans.get("ops", [])
    ]
    lt = ans.get("link_table")
    link = (bytes.fromhex(lt[0]), lt[1], bytes.fromhex(lt[2]), lt[3]) if lt else None
    if not ops and link is None:
        return None
    top = undo_top(doc)
    n = doc.apply_record_ops(ops, link, rerender=False, facts=answer_facts(ans))
    if n is None or undo_top(doc) is top:
        return None
    names = set()
    for k, v in changes.items():
        names |= {"%s.%s" % c for c in v} if v else {"%s.%s" % k}
    return (sorted(names, key=str.lower), doc._undo[-1], changes, doc.genie_sources(set(changes)), n)


class GenieJobs(QtCore.QObject):
    """One editor window's genie jobs, ordered by what the user looks at."""

    #: (doc, names, ranges replaced or -1, the job: .bindings set for a genie dialog's OK)
    finished = QtCore.Signal(object, object, int, object)
    started = QtCore.Signal(object, object)
    #: requests at a time in each helper (the window's warm helper and the others of its
    #: pool: :func:`helpers_count`)
    MAX_RUNNING = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: Dict[int, GenieJob] = {}  # id(doc) -> job
        self._queue: List[GenieJob] = []
        # answers that came back while the user was in the middle of something
        self._ready: List[tuple] = []  # (job, answer, time it came back)
        #: (doc, seconds waited) -> True when an answer may land now (the window: no mouse
        #: button held, no in-place edit, no modal dialog); None = always
        self.gate: Optional[Callable[[object, float], bool]] = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self.worker = WarmWorker()
        #: the other helpers of the pool, made when an update finds every helper busy
        #: (:func:`helpers_count`, :meth:`_free_worker`)
        self.extra: List[WarmWorker] = []
        #: {id(doc): n} the tabs shown since their job was queued (:meth:`promote`), the
        #: last shown the highest n
        self._front: Dict[int, int] = {}
        self._front_n = 0
        #: > 0: jobs are queued, not started (:meth:`hold`)
        self._held = 0
        #: running jobs dropped (their tab closed, read again, its update made by the caller):
        #: their helper computes the request all the same until its answer comes, so the next
        #: job goes to the other helper or waits (one sent to the same helper would wait
        #: behind it in the helper's input)
        self._dead: List[GenieJob] = []
        # (the workers end when they have been idle a while)
        self._idle = QtCore.QTimer(self)
        self._idle.setInterval(30000)
        self._idle.timeout.connect(self._idle_stop)
        self._idle.start()

    def _idle_stop(self) -> None:
        self.worker.idle_stop(self.busy())
        for w in self.extra:
            w.idle_stop(bool(self._load(w)))

    def helpers(self) -> List[WarmWorker]:
        """The pool: the window's helper first, then the others made so far."""
        return [self.worker] + list(self.extra)

    def busy(self, doc=None) -> bool:
        if doc is None:
            return bool(self._jobs or self._queue or self._ready)
        return (
            id(doc) in self._jobs
            or any(j.doc is doc for j in self._queue)
            or any(r[0].doc is doc for r in self._ready)
        )

    def pending(self, doc) -> list:
        """``doc``'s jobs that have not landed (running, queued, waiting for the user)."""
        out = [j for j in self._jobs.values() if j.doc is doc]
        return out + [j for j in self._queue if j.doc is doc] + [r[0] for r in self._ready if r[0].doc is doc]

    @contextlib.contextmanager
    def hold(self):
        """Jobs submitted in the block are queued and start when it ends, in their order
        (:meth:`order_key`): the first one submitted does not start before the others
        are known."""
        self._held += 1
        try:
            yield self
        finally:
            self._held -= 1
            if not self._held:
                self._start_more()

    def promote(self, doc) -> bool:
        """``doc``'s tab is shown: its queued jobs go first (before those of the tabs shown
        before it), and the one of the tab shown starts at once in the second helper while
        the window's helper composes another tab (:meth:`_free_worker`). True when it has one
        queued."""
        if not any(getattr(j, "doc", None) is doc for j in self._queue):
            return False
        self._front_n += 1
        self._front[id(doc)] = self._front_n
        self._start_more()
        return True

    def _shown_doc(self):
        """The document the window shows now (None without a window)."""
        try:
            return getattr(self.parent(), "doc", None)
        except RuntimeError:  # swallow-ok: the window is being destroyed
            return None

    def order_key(self, job, pos: int = 0) -> tuple:
        """Sort key for a job in the queue."""
        doc = getattr(job, "doc", None)
        shown = doc is not None and doc is self._shown_doc()
        rank = tuple(getattr(job, "rank", ()) or ()) if jobs_order() == "near" else ()
        return (
            1 if getattr(job, "quiet", False) else 0,
            0 if shown else 1,
            -self._front.get(id(doc), 0),
            0 if getattr(job, "urgent", False) else 1,
            rank,
            pos,
        )

    def submit(
        self,
        doc,
        changes: dict,
        names: List[str],
        first: bool = False,
        why: str = "",
        near: Optional[int] = None,
        quiet: bool = False,
        label: str = "",
        previous: Optional[dict] = None,
    ) -> None:
        """Queue a genie update for ``doc``."""
        if not changes and not previous:
            return
        if not _in_event_loop():
            n = -1
            top = undo_top(doc)
            job = GenieJob(
                doc, changes, names, why=why, previous=previous, **({"label": label} if label else {})
            )
            where = ""
            try:
                prev = None
                if previous and not _is_element(doc):
                    from ...infra.paths import data_dir

                    prev, where = previous_files(previous, data_dir("genieupdate"))
                rep: dict = {}
                n = GW.apply_update(doc, changes, prev, report=rep)
                job.left = int(rep.get("left", 0))
                job.unfinished = bool(
                    getattr(doc, "genie_update_left", None) or getattr(doc, "genie_update_unfinished", False)
                )
            except Exception:  # noqa: BLE001
                logsetup.note_exception("genie_jobs.submit/inline")
            finally:
                if where:
                    import shutil

                    shutil.rmtree(where, ignore_errors=True)
            # (composed here at once: the job says whether it made an undo step, also one
            # that changed only the page's link table, as a worker's answer does)
            job.quiet = quiet
            job.below = top
            job.stepped = undo_top(doc) is not top
            if label and job.stepped:
                with contextlib.suppress(AttributeError):
                    undo_top(doc).label = label
            self.finished.emit(doc, names, n, job)
            return
        running = self._jobs.get(id(doc))
        if (
            running is not None
            and getattr(running, "quiet", False)
            and not quiet
            and not running.template
            and running.bindings is None
        ):
            # a quiet job only finishes a step the tab shows already (the end of a background
            # update, a library it saved again): an update the tab waits for does not wait
            # behind it, it takes its place and its changes
            del self._jobs[id(doc)]
            self._drop_running(running)
            _close_task(running)
            changes = {**running.changes, **changes}
            previous = {**(getattr(running, "previous", None) or {}), **(previous or {})} or None
        for j in self._queue:
            # (into a queued genie update of the page only: a template job or a genie dialog's
            # OK composes something else, and the changes would be lost in it)
            if j.doc is doc and not j.template and j.bindings is None:
                j.changes = {**j.changes, **changes}
                # (the version the page holds is the one before the FIRST of the saves: a
                # later save's version before it is the earlier save's, which no copy on the
                # page is; the worker composes from the genie as it is then)
                for k, v in (previous or {}).items():
                    j.previous.setdefault(k, v)
                if not (j.why == "save" and quiet):
                    # (the end of the run on a tab whose save's update has not started: part
                    # of that update, named as it, not by the genies the run saved again)
                    j.names = sorted(set(j.names) | set(names), key=str.lower)
                # (a save's job stays one: its step is the save's; quiet only when both are)
                if j.why != "save":
                    j.why = why or j.why
                j.quiet = j.quiet and quiet
                _task_quiet(j)
                if near is not None and near < j.rank[1]:
                    j.rank = (j.rank[0], near, j.rank[2])
                from . import tasks

                tasks.progress(j.task, 0, 0, j.text())
                if first:
                    self.promote(doc)  # (the merged job too)
                # (a quiet job that became a save's may start in the second helper now)
                self._start_more()
                return
        job = GenieJob(doc, changes, names, why=why, previous=previous, **({"label": label} if label else {}))
        job.quiet = quiet
        job.rank = _rank(doc, near)
        self._begin_task(job)
        if first:
            self._queue.insert(0, job)
            self.promote(doc)
        else:
            self._queue.append(job)
        self._start_more()
        self._timer.start()

    def submit_template(self, doc, link: str) -> bool:
        """The page's template applied again in the worker (a stale page's open): the answer
        one undo step 'template <lib.template> re-applied'. False when no worker runs (no
        event loop): the caller does it inline."""
        if not _in_event_loop():
            return False
        from ..names import template_name

        job = GenieJob(doc, {}, [link], label="template %s re-applied" % template_name(link), template=True)
        self._begin_task(job)
        self._queue.insert(0, job)
        self._start_more()
        self._timer.start()
        return True

    def template_pending(self, doc) -> bool:
        """``doc`` has a template job that has not landed (queued, running, or its answer
        waiting for the user to finish): the open's template step in the worker."""
        return any(
            j.doc is doc and j.template
            for j in list(self._jobs.values()) + list(self._queue) + [r[0] for r in self._ready]
        )

    def submit_bindings(self, doc, index: int, values: dict, name: str) -> None:
        """A genie dialog's OK on a big genie: its re-composition in the worker, the
        answer one undo step 'genie parameters'."""
        job = GenieJob(
            doc, {}, [name], bindings={"index": int(index), "values": dict(values)}, label="genie parameters"
        )
        self._begin_task(job)
        self._queue.insert(0, job)
        self._start_more()
        self._timer.start()

    def _begin_task(self, job: GenieJob) -> None:
        """The job's background task (the status bar's tasks button; Cancel stops it)."""
        from . import tasks

        job.task = tasks.begin(
            "genie",
            job.text(),
            cancel=lambda j=job: self.stop_job(j),
            window=self.parent(),
            quiet=bool(getattr(job, "quiet", False)),
        )

    def stop_job(self, job: GenieJob) -> None:
        """Cancel one job; the page keeps its genies as they are."""
        self._queue = [j for j in self._queue if j is not job]
        self._ready = [r for r in self._ready if r[0] is not job]
        if self._jobs.get(id(job.doc)) is job:
            del self._jobs[id(job.doc)]
            self._drop_running(job)
        _close_task(job)
        self._start_more()
        w = self.parent()
        with contextlib.suppress(Exception):
            from ...editing import ui_state

            ui_state(job.doc).genie_sources_done.clear()
        fn = getattr(w, "_genies_updated", None)
        if callable(fn):
            with contextlib.suppress(RuntimeError):
                fn(job.doc)
        after = getattr(w, "_template_job_dropped", None)
        if job.template and callable(after):
            # (the open's steps that waited for its template: the symbols, the placeholder
            # genies and the genies are not what was cancelled)
            with contextlib.suppress(RuntimeError):
                after(job.doc)
        self._note_busy()

    def cancel(self, doc) -> None:
        """The page closed (or its task's Cancel): its job goes."""
        gone = [j for j in self._queue if j.doc is doc] + [r[0] for r in self._ready if r[0].doc is doc]
        self._queue = [j for j in self._queue if j.doc is not doc]
        self._ready = [r for r in self._ready if r[0].doc is not doc]
        self._front.pop(id(doc), None)
        job = self._jobs.pop(id(doc), None)
        if job is not None:
            self._drop_running(job)
            gone.append(job)
        for j in gone:
            _close_task(j)

    def cancel_automatic(self, doc, genies: bool = True, templates: bool = True) -> int:
        """Drop ``doc``'s pending automatic updates."""

        def automatic(j):
            return (
                j.doc is doc
                and j.bindings is None
                and (templates if getattr(j, "template", False) else genies)
            )

        gone = [j for j in self._queue if automatic(j)] + [r[0] for r in self._ready if automatic(r[0])]
        self._queue = [j for j in self._queue if not automatic(j)]
        self._ready = [r for r in self._ready if not automatic(r[0])]
        job = self._jobs.get(id(doc))
        if job is not None and automatic(job):
            del self._jobs[id(doc)]
            self._drop_running(job)
            gone.append(job)
        for j in gone:
            _close_task(j)
        if gone:
            self._start_soon()
        return len(gone)

    def _start_soon(self) -> None:
        """Start what may start when the caller's burst is over (the next turn of the event
        loop): the end of a background update reads its element tabs again one after another,
        each dropping its update, so a tab's update started in between would be dropped at
        once with its helper computing it all the same."""
        if not _in_event_loop():
            self._start_more()
            return
        QtCore.QTimer.singleShot(0, self, self._start_more)

    def _drop_running(self, job: GenieJob) -> None:
        """A running job is not wanted any more: its files go (GenieJob.kill), and its
        request stays its helper's load until the helper has answered it (:attr:`_dead`)."""
        job.kill()
        w = getattr(job, "worker", None)
        if w is not None and w.alive() and not job.done():
            self._dead.append(job)
            self._timer.start()

    def _prune_dead(self) -> None:
        """The dropped requests their helper has answered (or the helper ended): gone."""
        for job in [j for j in self._dead if j.done()]:
            self._dead.remove(job)
            job.cleanup()

    def _load(self, worker) -> list:
        """The requests ``worker`` computes now: the running jobs sent to it and the dropped
        ones it has not answered yet."""
        return [
            j for j in list(self._jobs.values()) + self._dead if getattr(j, "worker", self.worker) is worker
        ]

    def _free_worker(self, job) -> Optional[WarmWorker]:
        """The helper ``job`` can start in now, if any."""
        from ...infra import workers

        n = helpers_count()
        pool = self.helpers()[:n]
        free_mb = None
        for w in pool:
            if len(self._load(w)) >= self.MAX_RUNNING:
                continue
            if w is self.worker or w.alive():
                return w
            if free_mb is None:
                free_mb = workers.free_memory_mb()
            if free_mb is not None and free_mb < WarmWorker.LOW_MEMORY_MB:
                return None  # (short of memory: it waits for a running helper)
            return w
        if len(self.helpers()) < n:
            if free_mb is None:
                free_mb = workers.free_memory_mb()
            if free_mb is None or free_mb >= WarmWorker.LOW_MEMORY_MB:
                w = WarmWorker()
                self.extra.append(w)
                return w
        for w in pool:
            load = self._load(w)
            if load and all(j in self._dead for j in load):
                # it computes only requests nobody waits for any more (their tab closed or
                # read again, a quiet update a newer one took the place of): ended, and it
                # starts again with this one (not waiting for them, and no helper beside them)
                w.kill_now()
                for j in load:
                    self._dead.remove(j)
                    j.cleanup()
                return w
        return None

    def _start_more(self) -> None:
        if self._held:
            return  # (started when the block ends: :meth:`hold`)
        while self._queue:
            # (one at a time per page, until its answer has landed: a job started while the
            # page's earlier answer waits for the user would be computed without it, the open's
            # genies before its template)
            waiting = {id(r[0].doc) for r in self._ready}
            ready = [
                (self.order_key(j, k), k, j)
                for k, j in enumerate(self._queue)
                if id(j.doc) not in self._jobs and id(j.doc) not in waiting
            ]
            pick = None
            for _key, _k, j in sorted(ready, key=lambda x: x[:2]):
                worker = self._free_worker(j)
                if worker is not None:
                    pick = (j, worker)
                    break
            if pick is None:
                break
            job, worker = pick
            self._queue.remove(job)
            if not any(j.doc is job.doc for j in self._queue):
                self._front.pop(id(job.doc), None)
            try:
                job.start(worker)
            except Exception:  # noqa: BLE001 -- e.g. the data folder is not writable
                logsetup.note_exception("genie_jobs.start")
                self.finished.emit(job.doc, job.names, -1, job)
                from . import tasks

                tasks.end(job.task, ok=False, tell=self._tell(job))
                continue
            self._jobs[id(job.doc)] = job
            self.last_started = job  # (the window's status line reads its kind)
            self.started.emit(job.doc, job.names)
        self._note_busy()

    def _tell(self, job: GenieJob) -> bool:
        """Whether the job's task says how it ended: the window says the outcome itself for
        the page shown (and for a template or a genie dialog's job on any page); a plain genie
        update of a page in another tab is told by its task."""
        if job.template or job.bindings is not None or getattr(job, "quiet", False):
            return False  # (a quiet job: the tab shows its change already)
        try:
            return getattr(self.parent(), "doc", None) is not job.doc
        except RuntimeError:  # swallow-ok: the window is being destroyed
            return False

    def _note_busy(self) -> None:
        from ...infra import workers

        workers.set_busy("genie", bool(self._jobs))

    def _may_land(self, doc, waited: float) -> bool:
        try:
            return self.gate is None or bool(self.gate(doc, waited))
        except Exception:  # noqa: BLE001
            logsetup.note_exception("genie_jobs.gate")
            return True

    def _poll(self) -> None:
        from . import tasks

        self._prune_dead()
        for key, job in list(self._jobs.items()):
            if not job.done():
                continue
            del self._jobs[key]
            ans = job.answer()
            job.cleanup()
            if job.env is not None and job.env != environment_signature() and job.attempts < 3:
                # (composed with settings changed since it was sent: asked again, in a
                # worker with the settings as they are)
                job.urgent = True
                self._queue.insert(0, job)
                continue
            self._ready.append((job, ans, time.monotonic()))
        waiting = []
        for job, ans, t0 in self._ready:
            if not self._may_land(job.doc, time.monotonic() - t0):
                waiting.append((job, ans, t0))
                continue
            n = -1
            top = undo_top(job.doc)
            job.changed_on_disk = bool(ans.get("changed_on_disk"))
            job.below = top
            try:
                n = apply_answer(job.doc, ans, job.label)
            except Exception:  # noqa: BLE001
                logsetup.note_exception("genie_jobs.apply")
            job.stepped = undo_top(job.doc) is not top
            job.left = int(ans.get("left") or 0)
            job.unfinished = bool(ans.get("unfinished"))
            if n is None:
                # the page changed under the answer: ask again from the page as it is now
                if job.attempts < 3:
                    job.urgent = True
                    self._queue.insert(0, job)
                    continue
                n = -1
            self.finished.emit(job.doc, job.names, n, job)
            tasks.end(job.task, ok=n >= 0, tell=self._tell(job))
        self._ready = waiting
        self._start_more()
        self._note_busy()
        if not self._jobs and not self._queue and not self._ready and not self._dead:
            self._timer.stop()

    def shutdown(self) -> None:
        from . import tasks

        for job in list(self._jobs.values()) + list(self._queue) + [r[0] for r in self._ready]:
            tasks.drop(job.task)
        for job in list(self._jobs.values()):
            job.kill()
        for w in self.helpers():
            w.stop()
        self._front.clear()
        self._dead.clear()
        self._jobs.clear()
        self._queue.clear()
        self._ready.clear()
        self._timer.stop()
        self._note_busy()


def _task_quiet(job: GenieJob) -> None:
    """``job``'s task is as quiet as the job (GenieJob.quiet: a quiet job's task never says
    how it ended; one merged into a save's update does)."""
    from . import tasks

    t = tasks.registry().tasks.get(job.task) if job.task is not None else None
    if t is not None:
        t.quiet = bool(getattr(job, "quiet", False))


def _close_task(job: GenieJob) -> None:
    """A job that will not land: its task says it stopped (the user's Cancel) or just goes
    (its page closed)."""
    from . import tasks

    t = tasks.registry().tasks.get(job.task) if job.task is not None else None
    if t is not None and t.cancelled:
        tasks.end(job.task)
    else:
        tasks.drop(job.task)
