"""Undo journal for files we patch in place."""

from __future__ import annotations

from ..infra.paths import path_key

import base64
import contextlib
import glob
import itertools
import json
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

_local = threading.local()
_seq = itertools.count(1)
SUFFIX = ".jnl"
#: the notes of the journals recover_project rolled back (take_notes / recover_stale drain it)
_NOTES: List[dict] = []
_NOTES_LOCK = threading.Lock()


class JournalRecoveryError(OSError):
    """A dead writer's journal of a project could not be rolled back (a table it patched
    cannot be written now): nothing may write the project until it is."""


def journal_dir() -> str:
    d = os.environ.get("CTG_JOURNAL_DIR")
    if d:
        return d
    from ..infra.paths import data_dir

    return data_dir("journal")


class _File:
    __slots__ = ("id", "path", "size", "end", "logged")

    def __init__(self, fid: int, path: str, size: int):
        self.id, self.path, self.size, self.end = fid, path, size, size
        self.logged = False  # its 'file' line is in the journal


class Transaction:
    """One journal (created lazily at the first write)."""

    def __init__(self, label: str):
        self.label = label
        self.path: Optional[str] = None
        self._fh = None
        self._files: Dict[str, _File] = {}
        self.depth = 0
        self.written: List[str] = []  # files this transaction changed
        # the project locks of the folders it writes (released at its end)
        self._locks = contextlib.ExitStack()
        self._locked: set = set()

    def hold(self, folder: str) -> None:
        """Take the project lock of ``folder`` for the rest of the transaction (once)."""
        from . import projectlock

        key = projectlock.key_of(folder)
        if key in self._locked:
            return
        self._locks.enter_context(projectlock.project(folder))
        self._locked.add(key)

    def _release_locks(self) -> None:
        self._locked.clear()
        stack, self._locks = self._locks, contextlib.ExitStack()
        stack.close()

    # -- journal file ------------------------------------------------------------
    def _line(self, obj: dict) -> None:
        if self._fh is None:
            d = journal_dir()
            os.makedirs(d, exist_ok=True)
            self.path = os.path.join(
                d, "%d-%d-%d%s" % (os.getpid(), next(_seq), int(time.time() * 1000), SUFFIX)
            )
            self._fh = open(self.path, "w", encoding="utf-8")
            from ..infra.paths import process_started

            # ('lock': written under the project lock protocol, so recover_project may
            # roll it back whatever its writer's pid says)
            self._fh.write(
                json.dumps(
                    {
                        "op": "begin",
                        "pid": os.getpid(),
                        "started": process_started(),
                        "lock": 1,
                        "label": self.label,
                        "time": time.time(),
                    }
                )
                + "\n"
            )
        self._fh.write(json.dumps(obj) + "\n")
        # to the OS before the data write it protects: a crash of this
        # process then keeps it (fsync would also cover a power cut, at a
        # cost per write)
        self._fh.flush()

    def _file(self, path: str, fh) -> _File:
        key = path_key(path)
        f = self._files.get(key)
        if f is None:
            pos = fh.tell()
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(pos)
            # the journal itself starts with the first WRITE: a file opened
            # for patching and only read (an index lookup) leaves none behind
            f = _File(len(self._files), os.path.abspath(path), size)
            self._files[key] = f
        return f

    def before_write(self, f: _File, fh, n: int) -> None:
        if not f.logged:
            self._line({"op": "file", "id": f.id, "path": f.path, "size": f.size})
            f.logged = True
        pos = fh.tell()
        old = b""
        if pos < f.size:
            old = fh.read(min(n, f.size - pos))
            fh.seek(pos)
        end = max(f.end, pos + n)
        self._line(
            {"op": "w", "id": f.id, "off": pos, "old": base64.b64encode(old).decode("ascii"), "end": end}
        )
        f.end = end
        if f.path not in self.written:
            self.written.append(f.path)

    # -- end -------------------------------------------------------------------------
    def _close_journal(self, committed: bool = False) -> None:
        if self._fh is not None:
            if committed:
                # a journal whose delete then fails (a virus scanner holding
                # it) must never be replayed over the finished update
                with contextlib.suppress(OSError):
                    self._fh.write(json.dumps({"op": "commit"}) + "\n")
                    self._fh.flush()
            with contextlib.suppress(OSError):
                self._fh.close()
            self._fh = None

    def commit(self) -> None:
        try:
            self._close_journal(committed=True)
            if self.path:
                with contextlib.suppress(OSError):
                    os.remove(self.path)
        finally:
            self._release_locks()

    def rollback(self) -> List[str]:
        """Restore every patched file; returns the files restored. The
        journal is kept when a file could not be restored (the next start
        tries again). The project locks are released after the replay."""
        try:
            self._close_journal()
            if not self.path:
                return []
            restored, failed = _replay(self.path, check_size=False)
            if not failed:
                # (kept when it can be neither removed nor marked: the next holder of the
                # project's lock replays it again, before anything is written)
                _retire(self.path)
            return restored
        finally:
            self._release_locks()


class _Patched:
    """A file opened for in-place update whose writes are journaled."""

    def __init__(self, fh, txn: Transaction, f: _File, own: bool):
        self._fh, self._txn, self._f, self._own = fh, txn, f, own
        self.closed_ok = None

    def write(self, data) -> int:
        self._txn.before_write(self._f, self._fh, len(data))
        n = self._fh.write(data)
        # another handle on the same file (and the next pre-image read)
        # must see it
        self._fh.flush()
        return n

    def truncate(self, size=None):
        raise OSError("journaled files cannot be truncated")

    def close(self, ok: bool = True) -> None:
        if self.closed_ok is not None:
            return
        self.closed_ok = ok
        try:
            self._fh.close()
        finally:
            if self._own:
                (self._txn.commit if ok else self._txn.rollback)()

    def __getattr__(self, name):  # read / seek / tell / flush / fileno
        return getattr(self._fh, name)

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        # a file of its own transaction rolls back when its block fails
        self.close(ok=et is None)


def _stack() -> List[Transaction]:
    s = getattr(_local, "stack", None)
    if s is None:
        s = _local.stack = []
    return s


def active() -> Optional[Transaction]:
    s = _stack()
    return s[-1] if s else None


def _pop(txn: Transaction, ok: bool) -> None:
    s = _stack()
    txn.depth -= 1
    if txn.depth > 0:
        return
    if s and s[-1] is txn:
        s.pop()
    if ok:
        txn.commit()
    else:
        txn.rollback()


@contextlib.contextmanager
def transaction(label: str):
    """Journal every :func:`open_patch` write made inside the block (on this
    thread) as ONE unit: an exception rolls every file back, completion
    discards the journal. Nested blocks join the outer one. The files must
    be closed before the block ends (the rollback rewrites them)."""
    cur = active()
    if cur is not None:
        cur.depth += 1
        try:
            yield cur
        finally:
            cur.depth -= 1
        return
    txn = Transaction(label)
    txn.depth = 1
    _stack().append(txn)
    try:
        yield txn
    except BaseException:
        _pop(txn, ok=False)
        raise
    _pop(txn, ok=True)


def open_patch(path: str):
    """``open(path, 'r+b')`` with journaled writes."""
    txn = active()
    own = txn is None
    if own:
        txn = Transaction("patch %s" % os.path.basename(path))
    try:
        txn.hold(os.path.dirname(os.path.abspath(path)))
        fh = open(path, "r+b")
    except BaseException:
        if own:
            txn._release_locks()
        raise
    try:
        f = txn._file(path, fh)
    except BaseException:
        fh.close()
        if own:
            txn.commit()
        raise
    return _Patched(fh, txn, f, own)


# ------------------------------------------------------------------------------ replay
def _read(path: str) -> Tuple[dict, Dict[int, dict], List[dict]]:
    """(head, files, writes) of a journal; a committed one has nothing to
    undo (no writes)."""
    head, files, writes = {}, {}, []
    torn = False
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            try:
                obj = json.loads(line)
            except ValueError:
                # a torn last line: its write never started (only the commit mark
                # _retire adds can follow it)
                torn = True
                continue
            op = obj.get("op")
            if op == "commit":
                return head, files, []
            if torn:
                continue
            if n == 0 and op == "begin":
                head = obj
            elif op == "file":
                files[obj["id"]] = dict(obj, end=obj["size"])
            elif op == "w" and obj.get("id") in files:
                writes.append(obj)
                files[obj["id"]]["end"] = obj["end"]
    return head, files, writes


def _retire(path: str) -> bool:
    """A journal that was replayed (or needs no replay) never replays again: it is
    removed, or, when another program holds it (a virus scanner), marked committed.
    False when neither could be done."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        pass
    try:
        with open(path, "a", encoding="utf-8") as fh:
            # (a new line first: the journal may end in a torn line)
            fh.write("\n" + json.dumps({"op": "commit"}) + "\n")
        return True
    except OSError:
        return False


def _replay(path: str, check_size: bool) -> Tuple[List[str], List[str]]:
    """Put the pre-images back and truncate files to their original length."""
    restored, skipped, errors = _replay3(path, check_size)
    return restored, skipped + errors


def _replay3(path: str, check_size: bool) -> Tuple[List[str], List[str], List[str]]:
    """:func:`_replay` with the files it left apart: (restored, skipped -- changed since
    or gone --, errors -- they cannot be written now)."""
    _head, files, writes = _read(path)
    restored, skipped, errors = [], [], []
    for fid, f in files.items():
        mine = [w for w in writes if w["id"] == fid]
        if not mine:
            continue
        try:
            if check_size and not (f["size"] <= os.path.getsize(f["path"]) <= max(f["size"], f["end"])):
                skipped.append(f["path"])
                continue
            with open(f["path"], "r+b") as fh:
                for w in reversed(mine):
                    old = base64.b64decode(w["old"])
                    if old:
                        fh.seek(w["off"])
                        fh.write(old)
                fh.truncate(f["size"])
            restored.append(f["path"])
        except FileNotFoundError:
            skipped.append(f["path"])  # (gone: nothing to put back)
        except OSError:
            errors.append(f["path"])
    return restored, skipped, errors


def _dead(head: dict, path: str = "") -> bool:
    """The journal's writer has ended (neither this process nor a live one). A journal with
    no begin line on disk yet (its writer is between the create and the first flush) is
    told by the pid its file name starts with."""
    from ..infra.paths import pid_alive

    pid = int(head.get("pid") or 0)
    started = int(head.get("started") or 0)
    if not pid and path:
        with contextlib.suppress(ValueError):
            pid = int(os.path.basename(path).split("-", 1)[0])
    return not (pid and pid_alive(pid, started))


def _journals() -> List[str]:
    return sorted(glob.glob(os.path.join(journal_dir(), "*" + SUFFIX)))


def _note(head: dict, restored, skipped, failed=()) -> dict:
    out = {"label": head.get("label", ""), "restored": list(restored), "skipped": list(skipped)}
    if failed:
        out["failed"] = list(failed)
    return out


def recover_project(project_dir: str) -> List[dict]:
    """Roll back unfinished journals for files in ``project_dir``."""
    from . import projectlock

    want = projectlock.key_of(project_dir)
    own = {path_key(t.path) for t in _stack() if t.path}
    name = os.path.basename(os.path.normpath(project_dir))
    notes = []
    for path in _journals():
        if path_key(path) in own:
            continue
        try:
            head, files, writes = _read(path)
        except OSError:
            continue  # (removed meanwhile: recovered elsewhere)
        if not files:
            # (nothing logged yet: a writer of another project may be starting it)
            if _dead(head, path):
                with contextlib.suppress(OSError):
                    os.remove(path)
            continue
        if any(projectlock.key_of(os.path.dirname(f["path"])) != want for f in files.values()):
            continue  # (another project's, or several projects')
        if writes:
            restored, skipped, errors = _replay3(path, check_size=True)
            if errors:
                raise JournalRecoveryError(
                    "An unfinished update of %s by ctgedit could not be undone: %s cannot "
                    "be written now (is Plant SCADA Studio open?)."
                    % (name, ", ".join(os.path.basename(p) for p in errors))
                )
            if restored or skipped:
                notes.append(_note(head, restored, skipped))
        if not _retire(path):
            raise JournalRecoveryError(
                "An unfinished update of %s by ctgedit was undone, but its journal %s "
                "cannot be removed now." % (name, os.path.basename(path))
            )
    if notes:
        with _NOTES_LOCK:
            _NOTES.extend(notes)
    return notes


def take_notes() -> List[dict]:
    """The notes of the journals :func:`recover_project` rolled back since the last call
    (a helper's answer carries them)."""
    with _NOTES_LOCK:
        out = list(_NOTES)
        del _NOTES[:]
    return out


def recover_stale() -> List[dict]:
    """Roll back the journals of processes that died before finishing them."""
    from . import projectlock

    out = []
    for path in _journals():
        try:
            head, files, _writes = _read(path)
        except OSError:
            continue
        if not _dead(head, path):
            continue  # still running (this process, too)
        folders = sorted({os.path.dirname(f["path"]) for f in files.values()}, key=projectlock.key_of)
        locked = {projectlock.key_of(d) for d in folders}
        try:
            with contextlib.ExitStack() as st:
                for d in folders:
                    st.enter_context(projectlock.project(d, timeout=2.0))
                if not os.path.exists(path):
                    continue  # (rolled back when its lock was taken)
                try:
                    head, files, _writes = _read(path)
                except OSError:
                    continue
                if not _dead(head, path) or any(
                    projectlock.key_of(os.path.dirname(f["path"])) not in locked for f in files.values()
                ):
                    continue  # (its writer had only begun: it is alive)
                restored, skipped, errors = _replay3(path, check_size=True)
                if not errors:
                    _retire(path)
                if restored or skipped or errors:
                    out.append(_note(head, restored, skipped, errors))
        except projectlock.ProjectBusyError, projectlock.LockCancelled:
            continue
        except JournalRecoveryError as e:
            out.append(_note(head, (), (), [str(e)]))
    return take_notes() + out
