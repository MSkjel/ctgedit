"""Export a document's page model in a helper process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from ...infra import gcguard, logsetup, workers

#: the internal command line option of the helper (app.main)
OPTION = "--sim-page-model"
#: how long a user of the model waits for the helper (s; Settings > Pages and libraries >
#: Long jobs, ui/prefs/job_options)
WAIT_S = 120.0


class ModelJob:
    """One page model on its way from a helper process."""

    def __init__(self, path: str, data: bytes, unsaved: bool = False):
        self.path = path
        self.unsaved = bool(unsaved)
        self.error = ""
        self.seconds: Optional[float] = None
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._raw: Optional[bytes] = None
        self._model: Optional[dict] = None
        self._decoded = False
        self._t0 = time.monotonic()
        args = [path] + (["--unsaved"] if self.unsaved else [])
        try:
            self._proc = workers.start(
                workers.command(OPTION, __name__, "main", args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                low_priority=False,
            )
        except OSError as e:
            self._proc = None
            self.error = "the page model helper did not start (%s)" % e
            self._done.set()
            return
        gcguard.before_worker()
        threading.Thread(target=self._pump, args=(bytes(data),), daemon=True, name="sim-page-model").start()

    def _pump(self, data: bytes) -> None:
        """Feed the page's bytes, read the model's (no decode here)."""
        try:
            out, err = self._proc.communicate(data, timeout=WAIT_S)
            if self._proc.returncode == 0 and out:
                self._raw = out
            else:
                text = (err or b"").decode("utf-8", "replace").strip().splitlines()
                self.error = "the page model helper failed (%s)" % (
                    text[-1] if text else "exit code %s" % self._proc.returncode
                )
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self.error = "the page model helper took longer than %d s" % WAIT_S
        except Exception as e:  # noqa: BLE001 -- the job then gives no model
            logsetup.note_exception("sim.modeljob._pump")
            self.error = "the page model helper failed (%s: %s)" % (type(e).__name__, e)
        finally:
            self.seconds = time.monotonic() - self._t0
            self._done.set()

    def done(self) -> bool:
        return self._done.is_set()

    def result(self, timeout: Optional[float] = None) -> Optional[dict]:
        """The model (None when the job failed or did not end within ``timeout``, by
        default :data:`WAIT_S`)."""
        if not self._done.wait(WAIT_S if timeout is None else timeout):
            return None
        with self._lock:
            if not self._decoded:
                self._decoded = True
                if self._raw is not None:
                    try:
                        self._model = json.loads(self._raw.decode("utf-8"))
                    except ValueError as e:
                        self.error = "the page model helper wrote no model (%s)" % e
                    self._raw = None
            return self._model


def unsaved(doc) -> bool:
    """Whether the engine must run ``doc`` from its rows, not its .RDB: it has unsaved edits,
    its file changed since it was read, or it has no file (sim/pagemodel export_document's
    'source')."""
    path = getattr(doc, "path", "") or ""
    try:
        moved = bool(doc.changed_on_disk())
    except Exception:  # noqa: BLE001  # swallow-ok: no stamp: as export_document
        moved = False
    return bool(getattr(doc, "dirty", False)) or moved or not (path and os.path.exists(path))


def start(doc) -> ModelJob:
    """A job for the model of the editor document ``doc`` as it is now (GUI thread: the
    document is serialised here)."""
    return ModelJob(os.path.abspath(doc.path), doc.serialize(), unsaved(doc))


def main(argv) -> int:
    """``ctgedit --sim-page-model <page.ctg> [--unsaved]``: the page's bytes on stdin (none:
    the file), its ctsim-page/0 model as JSON on stdout."""
    args = list(argv)
    if not args:
        sys.stderr.write("no page\n")
        return 2
    path = args[0]
    is_unsaved = "--unsaved" in args[1:]
    data = sys.stdin.buffer.read()
    if not data:
        with open(path, "rb") as fh:
            data = fh.read()
    from ...editing import EditableDocument

    # (the model reads the records' bodies: their bags are scanned only where one is read)
    doc = EditableDocument.from_bytes(data, path, records_only=True, lazy_bags=True)
    model = doc.export_page_model()
    if is_unsaved:
        # (this document is its bytes: the editor's had edits the file has not)
        model["source"] = "unsaved"
        c = model.get("compiled")
        if isinstance(c, dict) and "current" in c:
            c["current"] = False
    out = json.dumps(model).encode("utf-8")
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return 0
