"""Crash recovery copies of unsaved documents."""

from __future__ import annotations

from .paths import data_dir, is_this_process, path_key, pid_alive, process_started

import contextlib
import glob
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .logsetup import note_exception as _note_exc

DATA_EXT = ".ctgrec"
META_EXT = ".json"


def autosave_dir() -> str:
    return data_dir("autosave")


@dataclass
class Snapshot:
    key: str
    pid: int
    path: str  # the page / library file the document belongs to
    kind: str = "page"  # 'page' | 'element'
    element: str = ""  # element name (elements)
    element_kind: str = ""  # symbol / genie / supergenie / template
    new: bool = False  # an element never saved yet
    title: str = ""
    saved_at: float = 0.0
    base_stamp: Optional[Tuple[int, int]] = None
    size: int = 0
    extra: dict = field(default_factory=dict)
    started: int = 0  # the writing process's start time (pids are reused)

    @property
    def data_path(self) -> str:
        return os.path.join(autosave_dir(), self.key + DATA_EXT)

    @property
    def meta_path(self) -> str:
        return os.path.join(autosave_dir(), self.key + META_EXT)

    def changed_on_disk(self) -> bool:
        """The document's file on disk is not the one the copy started from
        (saved by another program since, or gone)."""
        if self.kind != "page" or self.base_stamp is None:
            return False
        try:
            st = os.stat(self.path)
        except OSError:
            return True
        return (st.st_mtime_ns, st.st_size) != tuple(self.base_stamp)


def key_for(path: str, element: str = "", pid: Optional[int] = None, started: Optional[int] = None) -> str:
    """The copy's file stem: per process (two editors never share a copy --
    nor does an editor that got the pid of a crashed one: the process start
    time is in the hash) and per document."""
    pid = os.getpid() if pid is None else pid
    if started is None:
        started = process_started() if pid == os.getpid() else 0
    h = hashlib.sha1(
        ("%s|%s|%d" % (path_key(path), element.lower(), started or 0)).encode("utf-8", "replace")
    ).hexdigest()
    return "%d-%s" % (pid, h[:16])


def _write_file(path: str, data: bytes) -> None:
    from ..format.fileio import write_atomic

    write_atomic(path, data)


def write(snap: Snapshot, data: bytes) -> Snapshot:
    """Store ``data`` as ``snap`` (data first, then its description -- a
    description without its data is never left behind)."""
    d = autosave_dir()
    os.makedirs(d, exist_ok=True)
    snap.saved_at = time.time()
    snap.size = len(data)
    if snap.pid == os.getpid() and not snap.started:
        snap.started = process_started()
    _write_file(snap.data_path, data)
    meta = dict(snap.__dict__)
    meta["base_stamp"] = list(snap.base_stamp) if snap.base_stamp else None
    _write_file(snap.meta_path, json.dumps(meta, indent=1).encode("utf-8"))
    return snap


def read(meta_path: str) -> Optional[Snapshot]:
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        snap = Snapshot(**{k: v for k, v in meta.items() if k in Snapshot.__dataclass_fields__})
    except OSError, ValueError, TypeError:
        return None
    if snap.base_stamp:
        snap.base_stamp = tuple(snap.base_stamp)
    return snap if os.path.exists(snap.data_path) else None


def read_bytes(snap: Snapshot) -> bytes:
    with open(snap.data_path, "rb") as fh:
        return fh.read()


def remove(key: str) -> None:
    for ext in (META_EXT, DATA_EXT):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(autosave_dir(), key + ext))


def snapshots() -> List[Snapshot]:
    out = []
    for mp in sorted(glob.glob(os.path.join(autosave_dir(), "*" + META_EXT))):
        s = read(mp)
        if s is not None:
            out.append(s)
    return out


def orphans() -> List[Snapshot]:
    """Copies left by processes that are no longer running (newest first)."""
    out = [
        s for s in snapshots() if not is_this_process(s.pid, s.started) and not pid_alive(s.pid, s.started)
    ]
    out.sort(key=lambda s: -s.saved_at)
    return out


def remove_process(pid: Optional[int] = None) -> int:
    """A clean exit: drop every copy of process ``pid`` (default: this one
    -- not the copies of a crashed earlier process that had the same pid)."""
    n = 0
    for s in snapshots():
        if is_this_process(s.pid, s.started) if pid is None else s.pid == pid:
            remove(s.key)
            n += 1
    # a description of this process whose data file vanished
    for mp in glob.glob(
        os.path.join(autosave_dir(), "%d-*%s" % (os.getpid() if pid is None else pid, META_EXT))
    ):
        if os.path.exists(mp[: -len(META_EXT)] + DATA_EXT):
            continue
        try:
            with open(mp, encoding="utf-8") as fh:
                started = int(json.load(fh).get("started") or 0)
        except OSError, ValueError, TypeError, AttributeError:
            started = 0
        if pid is not None or is_this_process(os.getpid(), started):
            with contextlib.suppress(OSError):
                os.remove(mp)
    return n


# --------------------------------------------------------------------------- documents
def describe(doc) -> Snapshot:
    """The Snapshot description of an open document (no data)."""
    from ..editing import ElementDocument

    if isinstance(doc, ElementDocument):
        snap = Snapshot(
            key_for(doc.path, doc.element_name),
            os.getpid(),
            os.path.abspath(doc.path),
            kind="element",
            element=doc.element_name,
            element_kind=doc.kind,
            new=doc.is_new,
            title=doc.display_name,
        )
        snap.extra = {
            "template_edited": sorted(int(h) for h in (getattr(doc, "_template_edited", ()) or ())),
            "page_size": list(doc._page_size) if doc._page_size else None,
        }
        return snap
    stamp = getattr(doc, "_disk_stamp", None)
    snap = Snapshot(
        key_for(doc.path),
        os.getpid(),
        os.path.abspath(doc.path),
        title=os.path.splitext(os.path.basename(doc.path))[0],
        base_stamp=tuple(stamp) if stamp else None,
    )
    # the Page Properties edits a save would write to pages.DBF / pgkey.DBF
    # (staged in the document, not in its bytes)
    staged = getattr(doc, "staged_page_state", None)
    if staged is not None:
        snap.extra = dict(staged())
    return snap


def save_document(doc) -> Snapshot:
    """Write the recovery copy of ``doc`` now."""
    snap = describe(doc)
    return write(snap, doc.serialize())


def forget_document(doc) -> None:
    """``doc`` was saved / closed / reverted: its copy is not needed."""
    from ..editing import ElementDocument

    element = doc.element_name if isinstance(doc, ElementDocument) else ""
    remove(key_for(doc.path, element))
    old = getattr(getattr(doc, "ui", None), "autosave_key", None)
    if old:
        remove(old)


def restore_document(snap: Snapshot):
    """The document the copy holds, opened at its OWN path as unsaved
    changes (``dirty``). Its serialised bytes are the copy's, byte for byte."""
    from ..editing import EditableDocument, ElementDocument
    from ..format.page import parse_ctg_bytes

    data = read_bytes(snap)
    parsed = parse_ctg_bytes(data)
    if parsed.page is None:
        raise ValueError("the recovery copy of %s is damaged" % (snap.title or snap.path))
    if snap.kind == "element":
        doc = None
        if not snap.new:
            try:
                doc = ElementDocument.open_element(snap.path, snap.element)
            except Exception:  # noqa: BLE001 -- the element is gone: a new one
                _note_exc("autosave.restore_document", path=snap.path, element=snap.element)
                doc = None
        if doc is None:
            size = tuple(snap.extra.get("page_size") or ()) or None
            doc = ElementDocument.new_element(
                snap.path, snap.element, snap.element_kind or "symbol", page_size=size
            )
        doc._template_edited = set(snap.extra.get("template_edited") or ())
        doc.parsed = parsed
        doc._rerender_page()
    else:
        doc = EditableDocument.from_bytes(data, snap.path)
        doc._disk_stamp = tuple(snap.base_stamp) if snap.base_stamp else None
        doc.restore_staged_page_state(snap.extra or {})
    doc.dirty = True
    doc.recovered_from = snap
    _log_restore(snap, len(data))
    return doc


def _log_restore(snap: Snapshot, size: int) -> None:
    """Log which page came back from which recovery copy, written when."""
    from . import logsetup

    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.saved_at)) if snap.saved_at else "unknown time"
    )
    what = snap.path + ("  [element %s]" % snap.element if snap.kind == "element" else "")
    logsetup.get("autosave").info(
        "restoring %s from the recovery copy %s (%d bytes, written %s by process %d%s)",
        what,
        snap.data_path,
        size,
        when,
        snap.pid,
        "; the page file changed on disk since" if snap.changed_on_disk() else "",
    )
    logsetup.remember("recovery", "%s <- recovery copy of %s" % (what, when))
