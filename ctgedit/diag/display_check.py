"""A second opinion on what a page shows."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional

from . import render_digest as RD


def display_digest_command(
    page: str,
    out: str,
    *,
    detail: bool = True,
    label_mode: Optional[str] = None,
    as_path: Optional[str] = None,
) -> List[str]:
    """The check process's command line: ``ctgedit.exe --display-digest`` in
    the frozen build, else this Python running render_digest (no Qt)."""
    extra = (
        (["--detail"] if detail else [])
        + (["--label-mode", label_mode] if label_mode else [])
        + (["--as", as_path] if as_path else [])
    )
    from ..infra import workers

    return workers.command(
        "--display-digest", "ctgedit.diag.render_digest", "display_digest_main", [page, out] + extra
    )


@dataclass
class Outcome:
    """A display check's verdict."""

    page: str
    identical: bool = False
    #: human lines 'object 12 (symbol): lost art ...' (the first few)
    differences: List[str] = field(default_factory=list)
    differing: int = 0  # objects whose digest differs
    count: int = 0  # objects shown here
    fresh_count: int = 0  # objects in the fresh read
    where: str = "process"  # 'process' | 'in-process'
    error: str = ""  # the check could not run (no verdict)
    fresh_swallowed: list = field(default_factory=list)
    fresh_derive_s: float = 0.0
    #: why the fresh read ran in this process ('' = it was asked for, or it did not)
    fallback: str = ""
    #: the document's derive_serial the verdict is about
    serial: int = -1
    when: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.error

    def text(self) -> str:
        if self.error:
            return "The display check of %s could not run: %s" % (os.path.basename(self.page), self.error)
        how = (
            "a fresh ctgedit process"
            if self.where == "process"
            else "a fresh read in this process"
            + (" (the check process failed: %s)" % self.fallback if self.fallback else "")
        )
        if self.identical:
            return "The display of %s is identical to what %s reads from the page's files (%d objects)." % (
                os.path.basename(self.page),
                how,
                self.count,
            )
        lines = [
            "The display of %s differs from what %s reads from the page's files: "
            "%d object(s) differ."
            % (os.path.basename(self.page), how, max(self.differing, len(self.differences)))
        ]
        lines += ["  " + d for d in self.differences]
        if self.fresh_swallowed:
            lines.append(
                "  (the fresh read skipped errors too: %s)"
                % ", ".join("%s %s x%d" % tuple(s) for s in self.fresh_swallowed[:5])
            )
        return "\n".join(lines)


def outcome_of(page, result: dict, path: str, limit: int = 10) -> Outcome:
    """Compare the derived ``page`` shown here with a fresh read's ``result``."""
    if not isinstance(result, dict) or "error" in result or "objects" not in result:
        err = result.get("error") if isinstance(result, dict) else None
        return Outcome(
            page=path,
            error=err or "no result",
            where=(result or {}).get("where", "process") if isinstance(result, dict) else "process",
        )
    c = RD.compare_page(page, result, limit=limit)
    lines = []
    for d in c["first"]:
        if d["index"] is None:
            lines.append(d["what"])
        else:
            lines.append("object %d (%s): %s" % (d["index"], d["kind"], d["what"]))
    return Outcome(
        page=path,
        identical=bool(c["identical"]),
        differences=lines,
        differing=int(c["differing"]),
        count=int(c["count"]),
        fresh_count=int(c["fresh_count"]),
        where=result.get("where", "process"),
        fresh_swallowed=list(result.get("swallowed") or ()),
        fresh_derive_s=float(result.get("derive_s") or 0.0),
    )


class DisplayCheck:
    """A fresh read of one page in its own process."""

    #: the check process now running (the editor starts the next when it ends)
    _running: List["DisplayCheck"] = []
    #: seconds before a check process is given up (killed)
    TIMEOUT = 180.0

    def __init__(
        self,
        path: str,
        *,
        data: Optional[bytes] = None,
        label_mode: Optional[str] = None,
        detail: bool = True,
    ):
        self.path = path
        self.data = data
        if label_mode is None:
            from ..io import reader

            label_mode = reader.get_label_mode()
        self.label_mode = label_mode
        self.detail = detail
        self.proc = None
        self.out: Optional[str] = None
        self.inp: Optional[str] = None
        self.result: Optional[dict] = None
        self.started_at = 0.0

    @classmethod
    def busy(cls) -> bool:
        cls._running[:] = [c for c in cls._running if c.proc is not None and c.proc.poll() is None]
        return bool(cls._running)

    def started(self) -> bool:
        return self.proc is not None or self.result is not None

    def start(self) -> bool:
        """Start the process. False = another check process is running (try
        again later); True when it runs or has a result (a start failure is a
        result: {'error': ...})."""
        if self.started():
            return True
        from ..infra import workers

        if self.busy() or workers.others_busy("display"):
            return False
        from ..infra.paths import data_dir

        folder = data_dir("displaycheck")
        try:
            os.makedirs(folder, exist_ok=True)
            self._sweep(folder)
            fd, self.out = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".json", dir=folder)
            os.close(fd)
            os.remove(self.out)
            page, as_path = self.path, None
            if self.data is not None:
                fd, self.inp = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".ctgbytes", dir=folder)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(self.data)
                page, as_path = self.inp, self.path
            self.proc = workers.track(
                "display",
                workers.start(
                    display_digest_command(
                        page, self.out, detail=self.detail, label_mode=self.label_mode, as_path=as_path
                    )
                ),
            )
        except Exception as e:  # noqa: BLE001 -- the caller falls back in-process
            self.proc = None
            self.result = {"error": "the check process could not start (%s: %s)" % (type(e).__name__, e)}
            self._cleanup()
            return True
        self.started_at = time.monotonic()
        self._running.append(self)
        return True

    _swept = False

    @classmethod
    def _sweep(cls, folder: str) -> None:
        if cls._swept:
            return
        cls._swept = True
        old = time.time() - 86400
        with contextlib.suppress(OSError):
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                with contextlib.suppress(OSError):
                    if os.path.getmtime(p) < old:
                        os.remove(p)

    def poll(self) -> Optional[dict]:
        """The result once the process ended (None while it runs)."""
        if self.result is not None:
            return self.result
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            if time.monotonic() - self.started_at > self.TIMEOUT:
                self.cancel()
                self.result = {"error": "the check process took longer than %d s" % self.TIMEOUT}
                return self.result
            return None
        res = None
        with contextlib.suppress(Exception):
            with open(self.out, "rb") as fh:
                res = json.loads(fh.read().decode("utf-8"))
        if not isinstance(res, dict):
            res = {"error": "the check process ended (exit code %s) without a result" % self.proc.returncode}
        self.result = res
        self._cleanup()
        return res

    def wait(self, timeout: Optional[float] = None) -> dict:
        """Wait for the result (starting the process when it did not run)."""
        if not self.started():
            while not self.start():
                time.sleep(0.05)
        t = time.monotonic()
        while True:
            r = self.poll()
            if r is not None:
                return r
            if timeout is not None and time.monotonic() - t > timeout:
                self.cancel()
                self.result = {"error": "no result within %d s" % timeout}
                return self.result
            time.sleep(0.05)

    def cancel(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            with contextlib.suppress(Exception):
                self.proc.kill()
                self.proc.wait(5)
        self._cleanup()

    def _cleanup(self) -> None:
        for p in (self.out, self.inp):
            if p:
                with contextlib.suppress(OSError):
                    os.remove(p)
        self.inp = None

    def run_in_process(self) -> dict:
        """The fresh read in THIS process (the fallback: this process's caches
        are shared, so a cache that went wrong goes wrong here too)."""
        try:
            if self.data is None:
                return RD.fresh_digest(
                    self.path, label_mode=self.label_mode, detail=self.detail, where="in-process"
                )
            from ..infra.paths import data_dir

            folder = data_dir("displaycheck")
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="%d-" % os.getpid(), suffix=".ctgbytes", dir=folder)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(self.data)
                return RD.fresh_digest(
                    tmp, as_path=self.path, label_mode=self.label_mode, detail=self.detail, where="in-process"
                )
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
        except Exception as e:  # noqa: BLE001 -- reported as no verdict
            return {"error": "%s: %s" % (type(e).__name__, e), "where": "in-process"}
