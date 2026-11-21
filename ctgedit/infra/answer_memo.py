"""Remember a genie update's answer on disk."""

from __future__ import annotations

import json
import os
from typing import Optional

#: the folder's size bound, in bytes
MAX_BYTES = 256 * 1024 * 1024
#: an answer bigger than this is not kept
MAX_ANSWER = 48 * 1024 * 1024


def _folder() -> str:
    from .paths import data_dir

    return data_dir("genie_answers")


def _path(key: str) -> Optional[str]:
    if not key or not all(c in "0123456789abcdef" for c in key):
        return None
    return os.path.join(_folder(), key + ".json")


def get(key: Optional[str]) -> Optional[dict]:
    """The answer kept under ``key``, or None."""
    p = _path(key or "")
    if p is None:
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            ans = json.load(fh)
    except OSError, ValueError:
        return None
    try:
        os.utime(p)  # (recently used: pruned last)
    except OSError:
        pass
    return ans if isinstance(ans, dict) else None


def put(key: Optional[str], answer: dict) -> None:
    """Keep ``answer`` under ``key`` (silently not, when it cannot be written)."""
    p = _path(key or "")
    if p is None or answer.get("error"):
        return
    try:
        data = json.dumps(answer)
        if len(data) > MAX_ANSWER:
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, p)
        _prune()
    except OSError:
        return


def forget_all() -> None:
    """Remove every remembered answer (View > Refresh, through noop_memo.forget_all)."""
    folder = _folder()
    try:
        names = os.listdir(folder)
    except OSError:  # swallow-ok: no answers yet
        return
    for n in names:
        if n.endswith(".json") or n.endswith(".tmp"):
            try:
                os.remove(os.path.join(folder, n))
            except OSError:  # swallow-ok: one another process is writing stays
                pass


def _prune() -> None:
    folder = _folder()
    try:
        files = []
        for n in os.listdir(folder):
            if n.endswith(".json"):
                st = os.stat(os.path.join(folder, n))
                files.append((st.st_mtime, st.st_size, n))
    except OSError:
        return
    total = sum(f[1] for f in files)
    for _t, size, n in sorted(files):
        if total <= MAX_BYTES:
            break
        try:
            os.remove(os.path.join(folder, n))
            total -= size
        except OSError:
            pass
