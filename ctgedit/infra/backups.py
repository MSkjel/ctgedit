"""Backups taken before a batch operation rewrites project files."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .paths import data_dir

MANIFEST = "manifest.json"
KINDS = {
    "update-pages": "Update Pages",
    "pack": "Pack Libraries",
    "update-users": "Update Users",
    "delete-page": "Delete Page",
    "pictures": "Duplicate Pictures",
    "replace": "Replace All",
    "replace-genie": "Replace Genie",
    "references": "Replace References",
}
#: the kinds whose entries are pages (restored by updatepages.restore_backup)
PAGE_KINDS = ("update-pages", "delete-page", "pictures", "replace", "replace-genie", "references")
#: the backups :func:`prune` keeps: the newest KEEP, none older than MAX_AGE_DAYS (0 = no age
#: limit), the newest MIN_KEEP always (Settings > Editing > Safety sets the first two)
KEEP = 10
MAX_AGE_DAYS = 30.0
MIN_KEEP = 3


def backups_root() -> str:
    return data_dir("backups")


def new_folder(kind: str, root: Optional[str] = None) -> str:
    """A fresh (not yet created) backup folder for a run of ``kind``."""
    root = root or backups_root()
    base = os.path.join(root, time.strftime(kind + "-%Y%m%d-%H%M%S"))
    path, n = base, 1
    while os.path.exists(path):
        n += 1
        path = "%s-%d" % (base, n)
    return path


def _manifest_path(folder: str) -> str:
    return os.path.join(folder, MANIFEST)


def read_manifest(folder: str) -> dict:
    from ..format.fileio import read_json_dict

    return read_json_dict(_manifest_path(folder))


def add_entry(folder: str, kind: str, entry: dict) -> None:
    """Record one backed-up item (a page / a library) in the manifest. Several processes
    back up into one folder at once (the parts of Update Pages): the read, the append and
    the write hold the manifest's lock (format/projectlock.file), which also keeps the
    write's fixed temp name to one writer."""
    from ..format import projectlock
    from ..format.fileio import write_atomic

    os.makedirs(folder, exist_ok=True)
    path = _manifest_path(folder)
    with projectlock.file(path):
        m = read_manifest(folder)
        m.setdefault("kind", kind)
        m.setdefault("created", time.time())
        m.setdefault("entries", []).append(entry)
        write_atomic(path, json.dumps(m, indent=1).encode("utf-8"))


@dataclass
class BackupInfo:
    path: str
    kind: str
    created: float
    entries: List[dict] = field(default_factory=list)
    size: int = 0

    @property
    def noun(self) -> str:
        """What its entries are, for their count: pages, files (pages and libraries: Update
        Users, or Update Pages that saved the library genies in the chain first) or
        libraries."""
        n = len(self.entries)
        if self.kind == "update-users" or (
            self.kind in PAGE_KINDS and any(e.get("original") for e in self.entries)
        ):
            return "file" if n == 1 else "files"
        if self.kind in PAGE_KINDS:
            return "page" if n == 1 else "pages"
        return "library" if n == 1 else "libraries"

    @property
    def title(self) -> str:
        what = KINDS.get(self.kind, self.kind)
        return "%s, %s: %d %s" % (
            what,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created)),
            len(self.entries),
            self.noun,
        )


def backup_file(folder: str, kind: str, path: str, data=None, *, under=None, once: bool = False) -> None:
    """Keep the file ``path`` (its bytes ``data``, else read from it) in the backup ``folder``,
    at <project>/<name> (``under``: the project folder whose subfolders the copy keeps), and
    list it in the manifest as {original, backup, size}; ``kind`` is the backup's when this is
    its first entry; ``once``: a copy this backup already holds stands (the first write of a
    run is the one to keep)."""
    from ..format.fileio import write_atomic

    if under:
        proj = os.path.basename(os.path.normpath(under))
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(under))
    else:
        proj = os.path.basename(os.path.dirname(os.path.abspath(path)))
        rel = os.path.basename(path)
    dest = os.path.join(folder, proj, rel)
    if once and os.path.exists(dest):
        return
    if data is None:
        with open(path, "rb") as fh:
            data = fh.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write_atomic(dest, data)
    add_entry(
        folder,
        kind,
        {"original": os.path.abspath(path), "backup": os.path.relpath(dest, folder), "size": len(data)},
    )


def restore_file(folder: str, entry: dict) -> str:
    """Put the file of a manifest ``entry`` (:func:`backup_file`) back; its path."""
    from ..format.fileio import write_atomic

    with open(os.path.join(folder, entry["backup"]), "rb") as fh:
        data = fh.read()
    write_atomic(entry["original"], data)
    return entry["original"]


def _folder_size(folder: str) -> int:
    total = 0
    for dp, _dn, fns in os.walk(folder):
        for fn in fns:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(dp, fn))
    return total


def list_backups(root: Optional[str] = None) -> List[BackupInfo]:
    """Every backup folder with a manifest, newest first."""
    root = root or backups_root()
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        folder = os.path.join(root, n)
        if not os.path.isdir(folder):
            continue
        m = read_manifest(folder)
        if not m:
            continue
        created = float(m.get("created") or os.path.getmtime(folder))
        out.append(
            BackupInfo(
                folder, str(m.get("kind", "")), created, list(m.get("entries") or []), _folder_size(folder)
            )
        )
    out.sort(key=lambda b: -b.created)
    return out


def prune(
    root: Optional[str] = None,
    keep: Optional[int] = None,
    max_age_days: Optional[float] = None,
    min_keep: Optional[int] = None,
    now: Optional[float] = None,
) -> List[str]:
    """Delete old backup folders; returns the folders removed. ``keep``, ``max_age_days``
    (0 = no age limit) and ``min_keep`` default to :data:`KEEP`, :data:`MAX_AGE_DAYS` and
    :data:`MIN_KEEP`."""
    now = time.time() if now is None else now
    keep = KEEP if keep is None else keep
    max_age_days = MAX_AGE_DAYS if max_age_days is None else max_age_days
    min_keep = MIN_KEEP if min_keep is None else min_keep
    removed = []
    for k, b in enumerate(list_backups(root)):
        old = max_age_days > 0 and (now - b.created) > max_age_days * 86400.0
        if k < min_keep or (k < keep and not old):
            continue
        shutil.rmtree(b.path, ignore_errors=True)
        if not os.path.exists(b.path):
            removed.append(b.path)
    return removed
