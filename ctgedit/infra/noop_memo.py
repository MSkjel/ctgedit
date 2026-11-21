"""Remember on disk which pages already have up-to-date genies."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Optional

MAX_ENTRIES = 4000
#: the version of the genie composition (format/genie_place, genie_norm, doc/library): with
#: the app's version (ctgedit.__version__) and the digest of the composition's source
#: (:func:`code_digest`), so the memo and the answers recorded by other code are not used
COMPOSITION_VERSION = "2026-10-18.1"
_LOCK = threading.Lock()
_CACHE: dict = {"stamp": None, "entries": {}}


def _path() -> str:
    from .paths import data_dir

    return os.path.join(data_dir(), "genie_noop.json")


#: the source of the genie composition, relative to the ctgedit package: hashed into every
#: key (:func:`code_digest`), so a change of that code (a source run: the app's version stays)
#: makes new keys without anyone having to bump :data:`COMPOSITION_VERSION`. io/ is in it: the
#: composition reads the tag formats its texts are measured with (io/project.tag_format_map),
#: the tag scope and the link tables' stamps through it
COMPOSITION_SOURCES = ("format", "doc", "io", "editing.py", "editing_base.py", "workers/genie_worker.py")
#: {"digest": the hex digest of COMPOSITION_SOURCES as this process read them}
_CODE: dict = {}


def _source_files(root: str) -> list:
    out = []
    for rel in COMPOSITION_SOURCES:
        p = os.path.join(root, rel)
        if os.path.isdir(p):
            for dirpath, dirnames, files in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
                out += [os.path.join(dirpath, f) for f in sorted(files) if f.endswith(".py")]
        elif os.path.isfile(p):
            out.append(p)
    return out


def code_digest() -> str:
    """The SHA-1 of the composition's source files (:data:`COMPOSITION_SOURCES`), read once a
    process; '' in a frozen build (no source files there: its version says the build)."""
    got = _CODE.get("digest")
    if got is not None:
        return got
    import sys

    frozen = bool(getattr(sys, "frozen", False))
    h = hashlib.sha1()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not frozen:
        for p in _source_files(root):
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:  # swallow-ok: a file that went: the others still say the code
                continue
            try:
                name = os.path.relpath(p, root)
            except ValueError:  # (another drive than the package)
                name = os.path.abspath(p)
            h.update(name.replace(os.sep, "/").encode("utf-8") + b"\0")
            h.update(hashlib.sha1(data).digest())
    got = _CODE["digest"] = "" if frozen else h.hexdigest()
    return got


def _salt() -> bytes:
    try:
        from .. import __version__ as app
    except ImportError:  # swallow-ok: a partial install has no version: the constant alone
        app = ""
    return ("v3|%s|%s|%s|" % (COMPOSITION_VERSION, app, code_digest())).encode("utf-8")


def key(
    page_bytes: Optional[bytes],
    changes: dict,
    sources: dict,
    page_sha1: Optional[bytes] = None,
    mode: str = "",
    context=(),
) -> str:
    """The memo key for composing ``changes`` on a page."""
    h = hashlib.sha1(_salt() + mode.encode("ascii"))
    h.update(repr(context).encode("utf-8"))
    h.update(page_sha1 if page_sha1 is not None else hashlib.sha1(page_bytes or b"").digest())
    h.update(repr(sorted((k, None if v is None else sorted(v)) for k, v in changes.items())).encode("utf-8"))
    h.update(repr(sorted((k, sorted(v)) for k, v in sources.items())).encode("utf-8"))
    return h.hexdigest()


def _load() -> dict:
    p = _path()
    try:
        st = os.stat(p)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _CACHE["stamp"] == stamp:
        return _CACHE["entries"]
    try:
        with open(p, encoding="utf-8") as fh:
            entries = dict(json.load(fh))
    except OSError, ValueError, TypeError:
        entries = {}
    _CACHE.update(stamp=stamp, entries=entries)
    return entries


def known(k: Optional[str]) -> bool:
    if not k:
        return False
    with _LOCK:
        return k in _load()


def remember(k: Optional[str]) -> None:
    if not k:
        return
    with _LOCK:
        entries = dict(_load())
        entries[k] = time.time()
        if len(entries) > MAX_ENTRIES:
            entries = dict(sorted(entries.items(), key=lambda kv: kv[1])[-MAX_ENTRIES:])
        p = _path()
        tmp = "%s.%d.tmp" % (p, os.getpid())
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(entries, fh)
            os.replace(tmp, p)
        except OSError:
            return
        _CACHE.update(stamp=None, entries=entries)


def forget_all() -> None:
    """Empty the memo and the remembered answers (infra/answer_memo) on disk: View > Refresh
    composes every page's genies from the files again."""
    with _LOCK:
        try:
            os.remove(_path())
        except OSError:  # swallow-ok: no memo yet, or another process holds it: kept
            pass
        _CACHE.update(stamp=None, entries={})
    from . import answer_memo

    answer_memo.forget_all()


# the process caches of this module (infra/caches.py: when each is emptied)
from . import caches as _caches  # noqa: E402

_caches.register("noop_memo.code", _CODE, on=("tests",))
_caches.register(
    "noop_memo.file", _CACHE, on=("refresh", "tests"), clear=lambda: _CACHE.update(stamp=None, entries={})
)
