"""Crash-safe file writes."""

from __future__ import annotations

from ..infra.paths import path_key

import itertools
import os
import time
from typing import Optional

TMP_SUFFIX = ".ctgedit-tmp"
#: the replace is tried this many times, this many seconds apart, while Windows refuses it
#: with access denied (5) or a sharing violation (32)
REPLACE_TRIES = 20
REPLACE_STEP = 0.05


def file_stamp(path: Optional[str]):
    """(normalised path, mtime_ns, size) of a file: it changed when its stamp did. None
    when there is none."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path_key(path), st.st_mtime_ns, st.st_size)


def find_ci(folder: str, name: str):
    """The file ``name`` in ``folder`` whatever its case ('pages.DBF', 'pages.dbf'), or None.
    One stat on Windows (its names ignore case); a folder listing elsewhere."""
    p = os.path.join(folder, name)
    if os.path.isfile(p):
        return p
    if os.name == "nt":
        return None
    low = name.lower()
    try:
        for f in os.listdir(folder):
            if f.lower() == low:
                return os.path.join(folder, f)
    except OSError:  # swallow-ok: no such folder: no file
        pass
    return None


def _replace(tmp: str, path: str) -> None:
    for k in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) not in (5, 32) or k == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_STEP)


def read_json_dict(path: str) -> dict:
    """The JSON object in the file ``path``; {} when there is none or it is unreadable."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except OSError, ValueError:  # swallow-ok: no file, or a garbled one: nothing kept
        return {}
    return m if isinstance(m, dict) else {}


def write_atomic(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` through a flushed temp file + os.replace.
    Raises what the write raised (PermissionError, OSError ...); the temp
    file is gone and ``path`` unchanged in that case."""
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    note_written(path)


#: the files this process wrote last: normcase path -> (mtime_ns, size) as written (the
#: editor's library watch tells its own saves, already propagated, from another program's)
_WRITTEN: dict = {}
WRITTEN_KEEP = 512


def note_written(path: str) -> None:
    """Remember ``path`` as this process just wrote it (:func:`written_stamp`), and count it
    as a change (:func:`file_version`). Also for a file a helper process of this editor
    wrote (the chain after a genie save): its writes are the editor's own."""
    key = path_key(path)
    _bump(key)
    try:
        st = os.stat(path)
    except OSError:
        _WRITTEN.pop(key, None)
        return
    _WRITTEN[key] = (st.st_mtime_ns, st.st_size)
    while len(_WRITTEN) > WRITTEN_KEEP:
        _WRITTEN.pop(next(iter(_WRITTEN)))


def written_stamp(path: str):
    """The (mtime_ns, size) this process last wrote ``path`` with, None when it did not."""
    return _WRITTEN.get(path_key(path))


#: normcase path -> the last change of the file this process knows of: its own writes
#: (note_written) and changes another program made that the editor noticed (note_changed).
#: Part of :func:`file_version`: two writes of one size within one tick of the file system's
#: clock give the file the same (mtime_ns, size). Never trimmed: a count that went would make
#: an old version look current again.
_CHANGES: dict = {}
#: the change serials (next() is atomic: a write on a worker thread and one on the GUI
#: thread never share one)
_CHANGE_SERIAL = itertools.count(1)


def _bump(key: str) -> None:
    _CHANGES[key] = next(_CHANGE_SERIAL)


def note_changed(path: str) -> None:
    """``path`` changed in a way its (mtime_ns, size) may not show: another program or a
    helper process wrote it (the caches keyed by :func:`file_version` read it again)."""
    _bump(path_key(path))


#: the library files' extensions whose changes go to a helper process (library_changes)
LIBRARY_EXTS = (".ctm", ".ctl", ".ctt")
#: normcase path -> the change serial the editor had for it in its last request (a helper)
_ADOPTED: dict = {}


def library_changes() -> dict:
    """{normcase path: change serial} of the library files this process changed: sent with a
    request to a helper process (genie_worker.write_request), which adopts them."""
    # (a copy first: a thread's write may add a file meanwhile)
    return {k: v for k, v in dict(_CHANGES).items() if k.endswith(LIBRARY_EXTS)}


def adopt_changes(changes) -> None:
    """In a helper process: the editor's :func:`library_changes` when it sent a request. A
    file the editor changed since its last request counts as changed here too."""
    for k, serial in dict(changes or {}).items():
        if _ADOPTED.get(k) != serial:
            _ADOPTED[k] = serial
            _bump(k)


def file_version(path: str) -> tuple:
    """The version of the file at ``path`` that a cache of what was read from it is keyed by:
    (mtime_ns, size, the last change this process knows of). Raises OSError as os.stat."""
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size, _CHANGES.get(path_key(path), 0))


#: normcase path -> (its file_version, the SHA-1 of its bytes then) (content_tag)
_DIGESTS: dict = {}


def content_tag(path: str) -> str:
    """SHA-1 of the file at ``path``, read once per version."""
    key = path_key(path)
    ver = file_version(path)
    hit = _DIGESTS.get(key)
    if hit is not None and hit[0] == ver:
        return hit[1]
    import hashlib

    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:  # swallow-ok: a file another program holds locked: keyed by its stamp
        return ""
    tag = h.hexdigest()
    if file_version(path) == ver:  # (a write while it was read: read again next time)
        _DIGESTS[key] = (ver, tag)
    return tag


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("fileio.written", _WRITTEN, on=("tests",))
_caches.register("fileio.changes", _CHANGES, on=("tests",))
_caches.register("fileio.adopted", _ADOPTED, on=("tests",))
_caches.register("fileio.digests", _DIGESTS, on=("refresh", "tests"))
