"""Markers for pages saved without their project database rows."""

from __future__ import annotations

from ..infra.paths import path_key

import contextlib
import glob
import hashlib
import json
import os
import time
from typing import Iterable, List, Optional


def folder() -> str:
    from ..infra.paths import data_dir

    return data_dir("pending-rows")


def _path(page: str) -> str:
    """The marker file of ``page``: named from the page lock's key (its real path), so
    every spelling of one page (a junction, a mapped or subst drive) finds the same one."""
    from ..format import projectlock

    key = projectlock.key_of(page)
    return os.path.join(folder(), hashlib.sha1(key.encode("utf-8")).hexdigest()[:32] + ".json")


def mark(page: str) -> None:
    """The page's rows are about to fall behind its .ctg (a save starts writing it)."""
    from ..format.fileio import write_atomic
    from ..infra.paths import process_started

    os.makedirs(folder(), exist_ok=True)
    page = os.path.abspath(page)
    rec = {
        "page": page,
        "project": os.path.dirname(page),
        "pid": os.getpid(),
        "started": process_started(),
        "time": time.time(),
    }
    write_atomic(_path(page), json.dumps(rec).encode("utf-8"))


def clear(page: str) -> None:
    """The page's rows match its .ctg again. Never raises: a marker that cannot be removed
    (another program reading it) is retried for a second, then stays, and its rows are
    written again later (the register is idempotent)."""
    p = _path(page)
    for k in range(20):
        try:
            os.remove(p)
            return
        except FileNotFoundError:
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) in (5, 32) and k < 19:
                time.sleep(0.05)
                continue
            from ..infra import logsetup

            logsetup.note_exception("pendingrows.clear", page=page)
            return
        except OSError:
            from ..infra import logsetup

            logsetup.note_exception("pendingrows.clear", page=page)
            return


def marked(page: str) -> Optional[dict]:
    """The page's marker, or None."""
    from ..format.fileio import read_json_dict

    return read_json_dict(_path(page)) or None


def registered(page: str) -> Optional[bool]:
    """The page has a live pages.DBF row in its folder's project: False when the page file
    or the row is gone (or the folder is no project any more), None when the table cannot
    be read now."""
    if not os.path.exists(page):
        return False
    from ..format.fileio import find_ci

    pp = find_ci(os.path.dirname(os.path.abspath(page)), "pages.DBF")
    if pp is None:
        return False
    try:
        from ..format.dbf import Dbf

        with Dbf(pp, writable=False) as d:
            name = os.path.splitext(os.path.basename(page))[0]
            return any(r[:1] != b"*" for _i, r in d.find("NAME", name))
    except Exception:  # noqa: BLE001 -- swallow-ok: unknown, the marker is kept
        return None


def listing(dirs: Optional[Iterable[str]] = None) -> List[dict]:
    """The markers whose writer has ended or is this process (a marker of another live
    process is a save in progress), of the projects ``dirs`` (default: all), by page. A
    marker of a page that is gone or no longer registered (deleted, removed from its
    project: no Update Pages run visits it, and it has no rows to write) is removed."""
    from ..infra.paths import is_this_process, pid_alive

    want = None if dirs is None else {path_key(d) for d in dirs}
    out = []
    for p in sorted(glob.glob(os.path.join(folder(), "*.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                m = json.load(fh)
        except OSError, ValueError:
            continue
        if not isinstance(m, dict) or not m.get("page"):
            continue
        pid, started = int(m.get("pid") or 0), int(m.get("started") or 0)
        if pid and pid_alive(pid, started) and not is_this_process(pid, started):
            continue
        if want is not None and path_key(m.get("project") or os.path.dirname(m["page"])) not in want:
            continue
        if registered(str(m["page"])) is False:
            with contextlib.suppress(OSError):
                os.remove(p)
            continue
        out.append(m)
    return sorted(out, key=lambda m: str(m["page"]).lower())


def _remove_stale_ctf(page: str) -> None:
    """A fast display file older than the page's .ctg (the save that left the marker
    stopped before it rebuilt it) is removed: the runtime then draws the .ctg, as the
    save's own fallback does. OSError when it cannot be removed."""
    try:
        ctg_m = os.stat(page).st_mtime_ns
    except OSError:
        return
    stem = os.path.splitext(page)[0]
    seen = set()
    for ext in (".ctF", ".ctf"):
        p = stem + ext
        key = path_key(p)
        if key in seen or not os.path.exists(p):
            continue
        seen.add(key)
        if os.stat(p).st_mtime_ns < ctg_m:
            os.remove(p)


def finish(page: str) -> str:
    """Write the rows of a page that has a marker from its .ctg on disk, under the page's
    lock: 'done' (no marker any more), 'gone' (no page file: the marker goes), 'written'.
    Raises OSError when the database is still refused (the marker stays)."""
    from ..format import projectlock
    from . import pgregister

    with projectlock.page(page):
        if marked(page) is None:
            return "done"
        if not os.path.exists(page):
            clear(page)
            return "gone"
        _remove_stale_ctf(page)
        pdir = os.path.dirname(os.path.abspath(page))
        name = os.path.splitext(os.path.basename(page))[0]
        pgregister.register_page(pdir, name, page)
        clear(page)
        return "written"
