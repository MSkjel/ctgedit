"""Earlier versions of pages and library elements (File > Page History)."""

from __future__ import annotations

from .paths import data_dir, path_key

import contextlib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


MANIFEST = "manifest.json"
#: the bytes all versions take, by history root: kept up to date by the copies and the
#: removals made here, read from disk when not known (a save prunes its own page's folder
#: and looks at the others only while the total is over the cap)
_TOTAL: dict = {}
from . import caches as _caches  # noqa: E402

_caches.register("history.total", _TOTAL, on=("tests",))
#: how many versions of each page are kept / all versions together at most (MB): set by
#: :func:`configure`; 0 versions = no copies (the default of a process nobody configured);
#: a new version at most every ``every_min`` minutes (0 = at every save)
_CONFIG = {"versions": 0, "mb": 200, "every_min": 0}


def configure(versions: int, mb: int, every_min: int = 0) -> None:
    """Keep ``versions`` earlier versions of each page (0 = none), ``mb`` MB of them in all,
    a new one at most every ``every_min`` minutes (0 = at every save)."""
    _CONFIG["versions"] = max(0, int(versions or 0))
    _CONFIG["mb"] = max(1, int(mb or 1))
    _CONFIG["every_min"] = max(0, int(every_min or 0))


def enabled() -> bool:
    return _CONFIG["versions"] > 0


def history_root() -> str:
    return data_dir("history")


def key_for(path: str, element: str = "") -> str:
    """The folder name of ``path``'s versions (``element``: of that library element)."""
    name = path_key(path)
    if element:
        name += "|" + element.lower()
    return hashlib.sha1(name.encode("utf-8", "replace")).hexdigest()[:16]


def folder_for(path: str, element: str = "", root: Optional[str] = None) -> str:
    return os.path.join(root or history_root(), key_for(path, element))


@dataclass
class Version:
    """One earlier version of a page or library element."""

    path: str  # the page / library it is a version of
    element: str  # the element name ('' for a page)
    file: str  # the copy
    saved_at: float  # when this version was saved (it was on disk from then)
    replaced_at: float  # when a save replaced it
    size: int = 0
    objects: int = 0
    sha1: str = ""

    @property
    def when(self) -> str:
        """'2026-10-11 14:02:31' (local time) of the version's save."""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.saved_at))


def read_manifest(folder: str) -> dict:
    from ..format.fileio import read_json_dict

    return read_json_dict(os.path.join(folder, MANIFEST))


def _write_manifest(folder: str, m: dict) -> None:
    from ..format.fileio import write_atomic

    write_atomic(os.path.join(folder, MANIFEST), json.dumps(m, indent=1).encode("utf-8"))


def _element_saved_at(data: bytes, element: str) -> float:
    """The save time an element's head records (CtDraw's stamp: local seconds since
    1899-12-30), 0.0 when it cannot be read."""
    try:
        from ..format.element import LibraryFile
        import datetime
        import struct

        lib = LibraryFile(data)
        s = lib.find(element)
        if s is None:
            return 0.0
        body = lib.block_data(s)
        stamp = struct.unpack_from("<I", body, 0x20)[0]
        if not stamp:
            return 0.0
        dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(seconds=stamp)
        return time.mktime(dt.timetuple())
    except Exception:  # noqa: BLE001  # swallow-ok: the library's file time is used then
        return 0.0


def _object_count(file: str, element: str) -> int:
    """The version's object count, from its page (element) head: no record parse."""
    try:
        from ..format import pagesummary as PS

        if element:
            es = PS.read_element_summary(file, element, want_thumbnail=False)
            return int(es.object_count) if es is not None and es.ok else 0
        s = PS.read_page_summary(file, template_scan=0, want_thumbnail=False)
        return int(s.object_count) if s.ok else 0
    except Exception:  # noqa: BLE001  # swallow-ok: a count for the list only
        return 0


def keep_version(
    path: str,
    data: bytes,
    element: str = "",
    now: Optional[float] = None,
    force: bool = False,
) -> Optional[Version]:
    """Keep ``data`` as the newest earlier version of ``path``, then prune."""
    if not enabled() or not data:
        return None
    now = time.time() if now is None else now
    folder = folder_for(path, element)
    digest = hashlib.sha1(data).hexdigest()
    m = read_manifest(folder)
    rows = [r for r in (m.get("versions") or []) if isinstance(r, dict)]
    if (
        rows
        and rows[-1].get("sha1") == digest
        and os.path.exists(os.path.join(folder, str(rows[-1].get("file", ""))))
    ):
        return None
    gap = _CONFIG["every_min"] * 60.0
    if gap and rows and not force:
        newest = max(float(r.get("replaced_at") or 0.0) for r in rows)
        if 0.0 <= now - newest < gap:
            return None
    os.makedirs(folder, exist_ok=True)
    ext = os.path.splitext(path)[1].lower() or ".ctg"
    stem = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + "Z"
    name, n = stem + ext, 1
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = "%s-%d%s" % (stem, n, ext)
    from ..format.fileio import write_atomic

    file = os.path.join(folder, name)
    write_atomic(file, bytes(data))
    saved_at = _element_saved_at(data, element) if element else 0.0
    if not saved_at:
        try:
            saved_at = os.path.getmtime(path)
        except OSError:
            saved_at = now
    row = {
        "file": name,
        "saved_at": saved_at,
        "replaced_at": now,
        "size": len(data),
        "objects": _object_count(file, element),
        "sha1": digest,
    }
    rows.append(row)
    m.update({"path": os.path.abspath(path), "element": element, "versions": rows})
    rkey = _root_key(None)
    known = rkey in _TOTAL
    # (this page's folder by count: the others are as the last prune left them)
    rows.sort(key=lambda r: float(r.get("replaced_at") or 0.0))
    versions = _CONFIG["versions"]
    gone = rows[:-versions] if 0 < versions < len(rows) else []
    if gone:
        _drop(folder, m, rows[-versions:])
    else:
        _write_manifest(folder, m)
    if known:
        _TOTAL[rkey] += len(data) - sum(int(r.get("size") or 0) for r in gone)
    total = _total()
    if total > _CONFIG["mb"] * 1024 * 1024:
        prune(keep_file=file)
    return _version(m, folder, row)


def _root_key(root: Optional[str]) -> str:
    return path_key(root or history_root())


def _total(root: Optional[str] = None) -> int:
    """The bytes all versions take (known, else read from the manifests once)."""
    rkey = _root_key(root)
    if rkey not in _TOTAL:
        total = 0
        for folder in _folders(root):
            for r in read_manifest(folder).get("versions") or []:
                if isinstance(r, dict):
                    total += _size(os.path.join(folder, str(r.get("file", ""))))
        _TOTAL[rkey] = total
    return _TOTAL[rkey]


def _version(m: dict, folder: str, r: dict) -> Version:
    return Version(
        str(m.get("path", "")),
        str(m.get("element", "") or ""),
        os.path.join(folder, str(r.get("file", ""))),
        float(r.get("saved_at") or 0.0),
        float(r.get("replaced_at") or 0.0),
        int(r.get("size") or 0),
        int(r.get("objects") or 0),
        str(r.get("sha1", "")),
    )


def list_versions(path: str, element: str = "", root: Optional[str] = None) -> List[Version]:
    """The earlier versions of ``path`` (``element``) whose copy is there, newest first."""
    folder = folder_for(path, element, root)
    m = read_manifest(folder)
    out = [_version(m, folder, r) for r in (m.get("versions") or []) if isinstance(r, dict)]
    out = [v for v in out if os.path.isfile(v.file)]
    out.sort(key=lambda v: -v.replaced_at)
    return out


def read_bytes(v: Version) -> bytes:
    with open(v.file, "rb") as fh:
        return fh.read()


def _folders(root: Optional[str] = None) -> List[str]:
    root = root or history_root()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return [os.path.join(root, n) for n in names if os.path.isdir(os.path.join(root, n))]


def _drop(folder: str, m: dict, rows: list) -> None:
    """Keep ``rows`` in ``folder``'s manifest; remove the copies of the others."""
    keep = {str(r.get("file", "")) for r in rows}
    for r in m.get("versions") or []:
        f = str(r.get("file", "")) if isinstance(r, dict) else ""
        if f and f not in keep:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(folder, f))
    if rows:
        m["versions"] = rows
        _write_manifest(folder, m)
    else:
        shutil.rmtree(folder, ignore_errors=True)


def prune(
    versions: Optional[int] = None, mb: Optional[int] = None, root: Optional[str] = None, keep_file: str = ""
) -> int:
    """Keep the newest ``versions`` of each page (default: the configured number; 0 keeps
    them all), then remove the oldest versions while all of them are larger than ``mb`` MB
    (``keep_file``, the copy just made, stays). Returns how many copies were removed."""
    versions = _CONFIG["versions"] if versions is None else max(0, int(versions))
    cap = (_CONFIG["mb"] if mb is None else max(0, int(mb))) * 1024 * 1024
    removed = 0
    every: List[Tuple[float, str, str]] = []  # (replaced_at, folder, file)
    for folder in _folders(root):
        m = read_manifest(folder)
        rows = [
            r
            for r in (m.get("versions") or [])
            if isinstance(r, dict) and os.path.isfile(os.path.join(folder, str(r.get("file", ""))))
        ]
        if not m or not rows:
            shutil.rmtree(folder, ignore_errors=True)
            continue
        rows.sort(key=lambda r: float(r.get("replaced_at") or 0.0))
        # (0 = no copies are made any more: the ones there stay until cleared)
        kept = rows[-versions:] if versions > 0 else rows
        if len(kept) != len(m.get("versions") or []):
            removed += len(rows) - len(kept)
            _drop(folder, m, kept)
        for r in kept:
            every.append((float(r.get("replaced_at") or 0.0), folder, str(r.get("file", ""))))
    total = sum(_size(os.path.join(f, n)) for _t, f, n in every)
    every.sort()
    for _t, folder, name in every:
        if total <= cap:
            break
        file = os.path.join(folder, name)
        if keep_file and os.path.normcase(file) == os.path.normcase(keep_file):
            continue
        size = _size(file)
        m = read_manifest(folder)
        rows = [
            r for r in (m.get("versions") or []) if isinstance(r, dict) and str(r.get("file", "")) != name
        ]
        _drop(folder, m, rows)
        total -= size
        removed += 1
    _TOTAL[_root_key(root)] = max(0, total)
    return removed


def _size(file: str) -> int:
    try:
        return os.path.getsize(file)
    except OSError:
        return 0


def disk_use(root: Optional[str] = None) -> Tuple[int, int]:
    """(bytes, number of versions) the earlier versions of every page take."""
    total = count = 0
    for folder in _folders(root):
        for dp, _dn, fns in os.walk(folder):
            for fn in fns:
                total += _size(os.path.join(dp, fn))
                if fn != MANIFEST:
                    count += 1
    return total, count


def clear(root: Optional[str] = None) -> int:
    """Remove every earlier version of every page; returns how many copies went."""
    n = disk_use(root)[1]
    for folder in _folders(root):
        shutil.rmtree(folder, ignore_errors=True)
    _TOTAL[_root_key(root)] = 0
    return n


def clear_page(path: str, element: str = "", root: Optional[str] = None) -> int:
    """Remove the earlier versions of one page (or library element); returns how many went."""
    n = len(list_versions(path, element, root))
    shutil.rmtree(folder_for(path, element, root), ignore_errors=True)
    _TOTAL.clear()  # (read again when next wanted)
    return n


def remove_version(v: Version) -> None:
    """Remove one earlier version (its copy and its manifest row)."""
    folder = os.path.dirname(v.file)
    m = read_manifest(folder)
    name = os.path.basename(v.file)
    rows = [r for r in (m.get("versions") or []) if isinstance(r, dict) and str(r.get("file", "")) != name]
    _drop(folder, m, rows)
    _TOTAL.clear()  # (read again when next wanted)
