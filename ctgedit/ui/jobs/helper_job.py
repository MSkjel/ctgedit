"""Run a job in a helper process and watch it from the GUI."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Callable, List, Optional

from PySide6 import QtCore

#: ms between looks at a helper's progress (Settings > Pages and libraries > Long jobs,
#: ui/prefs/job_options)
POLL_MS = 300


class HelperJob(QtCore.QObject):
    """One helper process run."""

    progress = QtCore.Signal(int, int, str)
    finished = QtCore.Signal(dict)

    def __init__(
        self,
        parent,
        command: Callable[[str, str], List[str]],
        request: dict,
        folder: str,
        skip_paths: Optional[Callable[[], List[str]]] = None,
        label: str = "job",
        task: bool = True,
        stream: bool = False,
        tell: bool = True,
    ):
        super().__init__(parent)
        self._command, self._request, self._folder = command, dict(request), folder
        self._skip = skip_paths or (lambda: [])
        self.label = label
        self._proc = None
        self._timer = None
        self._files: dict = {}
        self._paths: List[str] = []
        self.stopping = False
        #: shown as a background task in the status bar (ui/jobs/tasks.py; HelperJobs shows one
        #: task for its parts)
        self._show_task = task
        self._stream, self._tell = stream, tell
        self.task: Optional[int] = None
        #: the records read so far (:meth:`new_records`); the last ones are read at the end,
        #: before the files go, and wait in ``_unread`` for the next look
        self._tail = RecordTail()
        self._unread: List[dict] = []

    def start(self) -> bool:
        """Start the helper; False (logged) when it could not start."""
        from ...infra import workers

        try:
            os.makedirs(self._folder, exist_ok=True)
            fd, req = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".json", dir=self._folder)
            os.close(fd)
            base = req[:-5]
            self._files = {
                "progress_file": base + ".progress",
                "skip_file": base + ".skip",
                "cancel_file": base + ".cancel",
            }
            if self._stream:
                self._files["records_file"] = base + ".records"
            self._write_skip()
            with open(req, "w", encoding="utf-8") as fh:
                json.dump(dict(self._request, **self._files), fh)
            self._answer = base + ".answer.json"
            self._paths = [req, self._answer] + list(self._files.values())
            self._proc = workers.track("pages", workers.start(self._command(req, self._answer)))
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("helper_job.start")
            return False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        if self._show_task:
            self.task = begin_task(self, self.label)
        return True

    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def new_records(self) -> List[dict]:
        """The records the helper appended since the last look (``stream``; [] without),
        those it wrote after the last poll before it ended too (read before its files
        go)."""
        out, self._unread = self._unread, []
        path = self._files.get("records_file", "") if self._proc is not None else ""
        if path:
            out += self._tail.take([path])
        return out

    def stop(self) -> None:
        """Ask the helper to stop after the page it is on."""
        if self.busy():
            self.stopping = True
            with contextlib.suppress(OSError):
                open(self._files["cancel_file"], "w").close()
            if self.task is not None:
                from . import tasks

                tasks.progress(self.task, 0, 0, "Stopping %s…" % self.label.lower())

    def wait(self, seconds: float = 30) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.wait(seconds)

    def _write_skip(self) -> None:
        with contextlib.suppress(OSError):
            with open(self._files["skip_file"], "w", encoding="utf-8") as fh:
                fh.write("\n".join(self._skip()))

    def _poll(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._write_skip()
            with contextlib.suppress(OSError, ValueError, KeyError):
                with open(self._files["progress_file"], encoding="utf-8") as fh:
                    pr = json.load(fh)
                self.progress.emit(int(pr["i"]), int(pr["n"]), str(pr["name"]))
                if self.task is not None and not self.stopping:
                    from . import tasks

                    tasks.progress(self.task, int(pr["i"]) + 1, int(pr["n"]))
            return
        self._timer.stop()
        try:
            with open(self._answer, encoding="utf-8") as fh:
                res = json.load(fh)
        except OSError, ValueError:
            res = {"error": "the helper gave no answer"}
        if self._stream:
            # (read before the files go: the records of a helper that died are all there is)
            res["streamed"] = read_records(self._files.get("records_file", ""))
            self._unread += self._tail.take([self._files.get("records_file", "")])
        for x in self._paths:
            with contextlib.suppress(OSError):
                os.remove(x)
        self._proc = None
        if res.get("error"):
            from ...infra import logsetup

            logsetup.get("helpers").warning(
                "%s failed: %s\n%s", self.label, res["error"], res.get("trace", "")
            )
        end_task(self.task, self.label, res, self.stopping, tell=self._tell)
        self.task = None
        self.finished.emit(res)


class HelperJobs(QtCore.QObject):
    """Several helper runs on parts of one request, seen as one job."""

    progress = QtCore.Signal(int, int, str)
    finished = QtCore.Signal(dict)

    def __init__(
        self,
        parent,
        command: Callable[[str, str], List[str]],
        request: dict,
        folder: str,
        parts: int,
        total: int,
        merge: Callable[[List[dict]], dict],
        skip_paths: Optional[Callable[[], List[str]]] = None,
        label: str = "job",
        stream: bool = False,
        task: bool = True,
        tell: bool = True,
        fewer_ok: bool = False,
    ):
        super().__init__(parent)
        self.label = label
        self._total, self._merge = total, merge
        self._jobs = [
            HelperJob(
                self,
                command,
                dict(request, part=[k, parts]),
                folder,
                skip_paths=skip_paths,
                label="%s (%d of %d)" % (label, k + 1, parts),
                task=False,
                stream=stream,
            )
            for k in range(parts)
        ]
        self._done: dict = {}
        self._seen: dict = {}
        self.stopping = False
        self._show_task, self._tell = task, tell
        self._fewer_ok = fewer_ok
        #: the parts that started (:meth:`start`)
        self.started = 0
        self.task: Optional[int] = None

    def start(self) -> bool:
        """Start every part; False when one could not start: the started ones are stopped,
        or (``fewer_ok``, at least one started) they go on alone and this is True."""
        for k, job in enumerate(self._jobs):
            job.progress.connect(lambda i, n, name, k=k: self._part_progress(k, i, name))
            job.finished.connect(lambda res, k=k: self._part_finished(k, res))
            if not job.start():
                if self._fewer_ok and k:
                    self._jobs = self._jobs[:k]
                    break
                for j in self._jobs[:k]:
                    j.stop()
                self._jobs = []
                return False
            self.started = k + 1
        if self._show_task:
            self.task = begin_task(self, self.label)
        return True

    def busy(self) -> bool:
        return any(j.busy() for j in self._jobs)

    def new_records(self) -> List[dict]:
        """The records every part appended since the last look (HelperJob.new_records:
        an ended part's last ones too)."""
        return [r for j in self._jobs for r in j.new_records()]

    def stop(self) -> None:
        """Ask every part to stop after the item it is on."""
        if self.busy():
            self.stopping = True
            for j in self._jobs:
                j.stop()
            if self.task is not None:
                from . import tasks

                tasks.progress(self.task, 0, 0, "Stopping %s…" % self.label.lower())

    def wait(self, seconds: float = 30) -> None:
        for j in self._jobs:
            j.wait(seconds)

    def _part_progress(self, k: int, i: int, name: str) -> None:
        self._seen[k] = i + 1
        done = sum(self._seen.values())
        self.progress.emit(done - 1, self._total, name)
        if self.task is not None and not self.stopping:
            from . import tasks

            tasks.progress(self.task, done, self._total)

    def _part_finished(self, k: int, res: dict) -> None:
        self._done[k] = res
        if len(self._done) == len(self._jobs):
            merged = self._merge([self._done[j] for j in range(len(self._jobs))])
            end_task(self.task, self.label, merged, self.stopping, tell=self._tell)
            self.task = None
            self.finished.emit(merged)


# -- what a job that changes closed pages leaves (Find / Replace, Duplicate Pictures,
#    Replace Genie: batchjob.edit_pages reads them from the request)
def builder_open_pages() -> List[list]:
    """[project, page] of the pages open in the Graphics Builder (the request's
    'builder_open'); [] when it is not running."""
    try:
        from ...backend.graphics_builder import open_pages

        return [list(x) for x in open_pages()]
    except Exception:  # noqa: BLE001 -- swallow-ok: no Graphics Builder running
        return []


def open_documents(dirs, elements: bool = False) -> list:
    """(window, document) of the pages (``elements`` False) or library elements (True) of
    these project folders open in an editor window."""
    from .. import main_window as MW
    from ...editing import ElementDocument

    want = {os.path.normcase(os.path.abspath(d)) for d in dirs}
    out = []
    for w in list(MW.MainWindow._instances):
        for e in getattr(w, "_docs", ()):
            d = e.get("doc")
            path = getattr(d, "path", "") or ""
            if (
                d is None
                or not path
                or not hasattr(d, "parsed")
                or isinstance(d, ElementDocument) != elements
            ):
                continue
            if os.path.normcase(os.path.dirname(os.path.abspath(path))) in want:
                out.append((w, d))
    return out


def read_records(path: str) -> List[dict]:
    """The complete JSON lines of a helper's records file (a line cut short by a helper
    that died, or one that is not JSON, is left out)."""
    out: List[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.endswith("\n"):
                    break  # (cut short: the helper died writing it)
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return out
    return out


class RecordTail:
    """The records a helper appended to its records files since the last look (the
    progress figures of Update Pages: ui/jobs/run_stats). Only complete lines count; a line being
    written is read at the next look. Never raises."""

    def __init__(self):
        self._offsets: dict = {}

    def take(self, paths) -> List[dict]:
        out: List[dict] = []
        for path in paths:
            at = self._offsets.get(path, 0)
            try:
                with open(path, "rb") as fh:
                    fh.seek(at)
                    data = fh.read()
            except OSError:  # swallow-ok: not written yet, or removed at the end
                continue
            end = data.rfind(b"\n") + 1
            if end <= 0:
                continue
            self._offsets[path] = at + end
            for line in data[:end].splitlines():
                try:
                    rec = json.loads(line.decode("utf-8"))
                except ValueError:  # swallow-ok: not a record
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        return out


def begin_task(job, label: str) -> Optional[int]:
    """The job's background task (its editor window: the one its dialog belongs to)."""
    from . import tasks

    return tasks.begin("helper:%s" % label, "%s…" % label, cancel=job.stop, window=job.parent())


def end_task(token: Optional[int], label: str, res: dict, stopped: bool, tell: bool = True) -> None:
    """The job ended: failed (the error said in the window's messages), stopped, or done.
    ``tell`` False: the job's own window says its result (no message)."""
    if token is None:
        return
    from . import tasks

    t = tasks.registry().tasks.get(token)
    if t is not None and stopped:
        t.cancelled = True
    err = (res or {}).get("error")
    tasks.end(
        token,
        ok=not err,
        summary=("%s failed: %s." % (label, str(err).rstrip(".")))
        if err
        else summary_of(label, res or {}, stopped),
        tell=tell,
    )


def summary_of(label: str, res: dict, stopped: bool = False) -> Optional[str]:
    """What a finished helper says in the window's messages: 'Find in closed pages: 12
    matches in 40 pages.', 'Replace All in closed pages: 14 replacements in 3 pages.' (None:
    the task's own '<label>: done.')."""
    from ..names import plural

    how = " (stopped)" if stopped or res.get("stopped") else ""
    if isinstance(res.get("rows"), list):
        n = len(res["rows"])
        return "%s%s: %s%s in %s." % (
            label,
            how,
            "at least " if res.get("more") else "",
            plural(n, "match"),
            plural(int(res.get("searched") or 0), "page"),
        )
    if "replaced" in res:
        return "%s%s: %s in %s." % (
            label,
            how,
            plural(int(res.get("replaced") or 0), "replacement"),
            plural(len(res.get("updated") or ()), "page"),
        )
    return None
