"""Shared code for the helper processes that edit closed pages (Replace All, duplicate
pictures, Replace Genie)."""

from __future__ import annotations

from ..infra.paths import path_key

import json
import os
import sys
from typing import Callable, List, Optional, Tuple


def write_json(path: str, obj) -> None:
    """``obj`` as JSON into ``path`` through a temp file (a reader never sees half of it);
    never raises: a progress or answer file the editor can do without."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError:  # swallow-ok: see above
        try:
            os.remove(tmp)
        except OSError:  # swallow-ok: never made
            pass


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


def helper_main(argv, option: str, fmt: str, dispatch: Callable, memo: bool = False) -> int:
    """``ctgedit <option> <request.json> <answer.json>``: ``dispatch(req, progress,
    skip_now)`` gives the answer (genie_worker.progress_and_skip from the request's files;
    ``req["request_path"]``: the request file, for data written beside it).
    ``memo``: the run's include chains and genie declarations are read once
    (infra/runmemo). Returns the exit code (2 for bad arguments)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [option]:
        args = args[1:]
    if len(args) != 2:
        return 2
    from ..doc import history as _history
    from ..render import headless_thumb

    _history.SAVE_THUMBNAIL = headless_thumb.page_thumbnail  # (fresh thumbnails)
    from .genie_worker import helper_waits, progress_and_skip

    helper_waits()
    req_path, ans_path = args
    try:
        with open(req_path, encoding="utf-8") as fh:
            req = json.load(fh)
        if req.get("format") != fmt:
            raise ValueError("request format %r" % req.get("format"))
        req["request_path"] = req_path
        stopped: list = []
        progress, skip_now = progress_and_skip(
            req.get("progress_file") or "", req.get("skip_file") or "", req.get("cancel_file") or "", stopped
        )
        if memo:
            from ..infra import runmemo

            with runmemo.kept():
                ans = dispatch(req, progress, skip_now)
        else:
            ans = dispatch(req, progress, skip_now)
        if stopped:
            ans["stopped"] = True
    except Exception as e:  # noqa: BLE001 -- swallow-ok: the answer carries it
        import traceback

        ans = {
            "format": fmt,
            "error": "%s: %s" % (type(e).__name__, e),
            "trace": traceback.format_exc()[-4000:],
        }
    write_json(ans_path, ans)
    return 0


def project_pages(projects) -> List[str]:
    """The registered pages of ``projects`` (folders), in project then name order."""
    from ..io.updatepages import project_pages as _registered

    out = []
    for d in projects:
        out.extend(_registered(d))
    return out


def page_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def batch_answer(fmt: str, backup: Optional[str] = None, **more) -> dict:
    """The keys every :func:`edit_pages` answer has."""
    out = {
        "format": fmt,
        "updated": [],
        "failed": [],
        "skipped_open": [],
        "open_in_builder": [],
        "changed_meanwhile": [],
        "db_in_use": [],
        "backup": backup,
    }
    out.update(more)
    return out


def edit_pages(
    pages, edit: Callable, out: dict, *, req: dict, kind: str, progress=None, skip_now=None
) -> List[Tuple[str, object]]:
    """Open, edit and save closed pages one after another, with progress and Stop."""
    from ..editing import PageChangedOnDisk, ProjectDatabaseInUse
    from ..format.projectlock import LockCancelled

    skip = {path_key(p) for p in req.get("skip") or ()}
    in_builder = {(str(p).strip().lower(), str(n).strip().lower()) for p, n in req.get("builder_open") or ()}
    backup = req.get("backup")
    pages = list(pages)
    done: List[Tuple[str, object]] = []
    for n, path in enumerate(pages):
        name = page_name(path)
        if progress is not None and progress(n, len(pages), name) is False:
            out["stopped"] = True
            break
        if path_key(path) in skip or (skip_now is not None and skip_now(path)):
            out.setdefault("skipped_open", []).append(name)
            continue
        proj = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
        try:
            got = edit(path)
            if not got or not got[1]:
                continue
            doc, result = got
            if (proj, name.lower()) in in_builder:
                out.setdefault("open_in_builder", []).append(name)
                continue
            doc.save(batch=True, backup=(backup, kind) if backup else None)
            out.setdefault("updated", []).append(name)
            done.append((name, result))
        except LockCancelled:
            # (Cancel while the page waited for a busy project: nothing was written)
            out["stopped"] = True
            break
        except PageChangedOnDisk:
            out.setdefault("changed_meanwhile", []).append(name)
            out.setdefault("failed", []).append(
                "%s: changed by another save while being updated. Run it again." % name
            )
        except ProjectDatabaseInUse as e:
            out.setdefault("db_in_use", []).append(name)
            out.setdefault("failed", []).append("%s: %s" % (name, e))
        except Exception as e:  # noqa: BLE001 -- swallow-ok: the page is reported, the run goes on
            out.setdefault("failed", []).append("%s: %s" % (name, e))
    return done
