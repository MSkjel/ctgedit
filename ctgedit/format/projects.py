"""The project registry (MASTER.DBF) and include chains."""

from __future__ import annotations

from ..infra import backups

from ..infra.paths import path_key

import os
from typing import Dict, List, Optional, Tuple
from .dbfcols import citect_rows as _citect_rows
from .fileio import LIBRARY_EXTS, find_ci
from ..infra import runmemo as _runmemo

# ---------------------------------------------------------------------------
# Minimal dBASE reader (char fields only)
# ---------------------------------------------------------------------------


def read_dbf_rows(path: str) -> List[Dict[str, str]]:
    """Read a dBASE III table as a list of {FIELD: str} dicts."""
    return _citect_rows(path)


# ---------------------------------------------------------------------------
# Project registry / include chain
# ---------------------------------------------------------------------------


#: pages.DBF NAME is C(64): a longer page name is stored, and so matched, truncated
PAGE_NAME_LEN = 64


def pages_table(folder: str) -> Optional[str]:
    """The folder's pages.DBF (any case), or None: the folder is no project."""
    return find_ci(folder, "pages.DBF") if folder else None


def is_project_dir(folder: Optional[str]) -> bool:
    """A Citect project folder: it has a pages.DBF."""
    return pages_table(folder or "") is not None


def registered_names(project_dir: str) -> Optional[set]:
    """Lower-case names of the pages registered in the project's pages.DBF (compare a file
    name truncated to :data:`PAGE_NAME_LEN`), or None when the folder is no project (every
    page counts)."""
    path = pages_table(project_dir)
    if path is None:
        return None
    return {n for n in ((r.get("NAME") or "").strip().lower() for r in read_dbf_rows(path)) if n}


def master_projects(user_dir: str) -> Dict[str, str]:
    """MASTER.DBF: lower-case name -> project path."""
    path = find_ci(user_dir, "MASTER.DBF")
    if path is None:
        return {}
    out: Dict[str, str] = {}
    for row in read_dbf_rows(path):
        name = row.get("NAME", "")
        p = row.get("PATH", "")
        if name and p and name.lower() not in out:  # the first row wins
            out[name.lower()] = os.path.normpath(p)
    return out


def included_project_names(project_dir: str) -> List[str]:
    """The ordered NAME rows of a project's ``include.dbf`` (``[]`` if none)."""
    path = find_ci(project_dir, "include.dbf")
    if path is None:
        return []
    return [r.get("NAME", "") for r in read_dbf_rows(path) if r.get("NAME", "").strip()]


def sibling_install_user_dirs(user_dir: str) -> List[str]:
    """User folders of other installs next to this one."""
    user_dir = os.path.abspath(user_dir)
    install_root = os.path.dirname(user_dir)
    grandparent = os.path.dirname(install_root)
    if not grandparent or grandparent == install_root:
        return []
    user_base = os.path.basename(user_dir)
    out: List[str] = []
    try:
        sibs = sorted(os.listdir(grandparent), key=str.lower)
    except OSError:
        return []
    for sib in sibs:
        root = os.path.join(grandparent, sib)
        if os.path.normcase(root) == os.path.normcase(install_root):
            continue
        cand = os.path.join(root, user_base)
        if find_ci(cand, "MASTER.DBF"):
            out.append(cand)
    return out


def resolve_project_dirs(
    name: str, user_dirs: List[str], _masters: Optional[Dict[str, Dict[str, str]]] = None
) -> List[str]:
    """Project name -> every existing folder across installs."""
    low = name.lower()
    masters = _masters if _masters is not None else {}
    out: List[str] = []
    seen = set()
    for ud in user_dirs:
        reg = masters.get(ud)
        if reg is None:
            reg = masters[ud] = master_projects(ud)
        cands = []
        p = reg.get(low)
        if p:
            cands.append(p)
        cands.append(os.path.join(ud, name))
        for cand in cands:
            if os.path.isdir(cand):
                key = path_key(cand)
                if key not in seen:
                    seen.add(key)
                    out.append(cand)
                break  # registry path wins within one install
    return out


# Projects every Citect project includes IMPLICITLY (no include.dbf row):
# "Include" is the documented always-included project, "System" travels with it.
# CtDraw resolves the Example tank/product symbol libraries through them.
IMPLICIT_INCLUDE_PROJECTS = ("Include", "System")


def include_search_dirs(project_dir: str) -> List[str]:
    """The included project folders of ``project_dir``, recursively, in order."""
    project_dir = os.path.abspath(project_dir)
    # (a batch run reads it once a project: infra/runmemo)
    memo = _runmemo.table("projects.include_search_dirs")
    if memo is not None:
        hit = memo.get(os.path.normcase(project_dir))
        if hit is not None:
            return list(hit)
    user_dir = os.path.dirname(project_dir)
    user_dirs = [user_dir] + sibling_install_user_dirs(user_dir)
    masters: Dict[str, Dict[str, str]] = {}
    out: List[str] = []
    seen = {os.path.normcase(project_dir)}

    def add(name: str) -> None:
        for p in resolve_project_dirs(name, user_dirs, masters):
            key = path_key(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
            walk(p)

    def walk(d: str) -> None:
        for name in included_project_names(d):
            add(name)

    walk(project_dir)
    for name in IMPLICIT_INCLUDE_PROJECTS:
        add(name)
    if memo is not None:
        memo[os.path.normcase(project_dir)] = list(out)
    return out


class PackError(RuntimeError):
    """Pack Libraries stopped; every library this run had rewritten was put
    back as it was (the message says which file failed and why)."""


class PackResult(tuple):
    """``(files_rewritten, bytes_saved)`` -- unpacks like a pair -- plus
    ``packed`` (the libraries rewritten), ``failures`` ([(path, reason)] of
    the files that could not be read as libraries and were left alone) and
    ``backup`` (the folder holding the originals, or None)."""

    def __new__(cls, changed: int, saved: int, packed=(), failures=(), backup=None):
        self = super().__new__(cls, (changed, saved))
        self.changed, self.saved = changed, saved
        self.packed = list(packed)
        self.failures = list(failures)
        self.backup = backup
        return self


def _element_content(lib, slot) -> bytes:
    """What CtDraw copies of an element."""
    import struct

    blk = lib.block_data(slot)
    if len(blk) >= 0x34:
        legacy = lib.word2 < 0x0C
        objdata, _n, thumb = struct.unpack_from("<III", blk, 0)
        links = struct.unpack_from("<I", blk, 0x0C if legacy else 0x1C)[0]
        need = (0x24 if legacy else 0x34) + links * 0x60 + objdata + (0 if legacy else thumb)
        if len(blk) < need <= len(lib.buf) - slot.offset:
            blk = bytes(lib.buf[slot.offset : slot.offset + need])
    legacy = lib.word2 < 0x0C
    head = 0x24 if legacy else 0x34
    if len(blk) < head:
        return blk
    objdata = struct.unpack_from("<I", blk, 0)[0]
    thumb = 0 if legacy else struct.unpack_from("<I", blk, 0x08)[0]
    links = struct.unpack_from("<I", blk, 0x0C if legacy else 0x1C)[0]
    need = head + links * 0x60 + objdata + thumb
    return blk[:need] if head <= need <= len(blk) else blk


def _library_properties(lib) -> bytes:
    """The library properties a pack carries over."""
    import struct

    tail = lib.appendix
    if not tail:
        return b""
    keep, o = [], 32
    while o + 36 <= len(tail):
        name = tail[o : o + 32].split(b"\0", 1)[0]
        size = struct.unpack_from("<I", tail, o + 32)[0]
        if o + 36 + size > len(tail):
            break
        if name.lower().startswith(b"library."):
            keep.append(tail[o : o + 36 + size])
        o += 36 + size
    return tail[:32] + b"".join(keep) if keep else b""


def packed_library(data: bytes) -> Optional[bytes]:
    """Pack one library file, as CtDraw does."""
    from .element import LibraryFile, ctdraw_timestamp

    src = LibraryFile(data)
    live, dead = [], 0
    for s in src.slots:
        if s.unused or (s.tag == 0x0100 and not s.size and not s.offset):
            continue  # free, or a new container's first slot
        if s.in_use and s.size and s.offset:
            live.append(s)
        else:
            dead += 1
    out = LibraryFile.new(src.version, src.word2, ctdraw_timestamp())
    contents = [(s.name, _element_content(src, s)) for s in live]
    for name, content in contents:
        out.write_element(name, content)
    packed = out.to_bytes() + _library_properties(src)
    if not dead and len(packed) >= len(data):
        return None
    check = LibraryFile(packed)
    if check.element_names() != [n for n, _c in contents]:
        raise ValueError("the packed library lists other elements than the original")
    for name, content in contents:
        s = check.find(name)
        if s is None or check.block_data(s)[: len(content)] != content:
            raise ValueError("element %s did not survive the pack unchanged" % name)
    return packed


def pack_libraries(project_dir: str, backup: Optional[str] = None) -> PackResult:
    """Tools > Pack Libraries: pack every library in ``project_dir``."""
    from .fileio import write_atomic

    failures, plan = [], []
    try:
        names = sorted(os.listdir(project_dir), key=str.lower)
    except OSError:
        return PackResult(0, 0)
    for fn in names:
        if not fn.lower().endswith(LIBRARY_EXTS):
            continue
        path = os.path.join(project_dir, fn)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            packed = packed_library(data)
        except Exception as e:  # noqa: BLE001 -- swallow-ok: never die on a foreign file; the failure goes back to the caller
            failures.append((path, "not packed: it could not be read as a library (%s)" % e))
            continue
        if packed is not None:
            plan.append((path, data, packed))
    done: List[Tuple[str, bytes]] = []
    saved = 0
    for path, data, packed in plan:
        stage = "backed up"
        try:
            if backup:
                backups.backup_file(backup, "pack", path, data)
            stage = "written"
            write_atomic(path, packed)
        except Exception as e:  # noqa: BLE001 -- put back what this run changed
            undo_failed = []
            for p, old in reversed(done):
                try:
                    write_atomic(p, old)
                except OSError:
                    undo_failed.append(os.path.basename(p))
            why = getattr(e, "strerror", None) or str(e) or type(e).__name__
            msg = "Pack Libraries stopped: %s could not be %s (%s)." % (os.path.basename(path), stage, why)
            if undo_failed:
                msg += " %s could not be put back. Restore them from the backup in %s." % (
                    ", ".join(undo_failed),
                    backup or "(no backup was taken)",
                )
            elif done:
                msg += " The %d librar%s packed before it %s put back as %s were." % (
                    len(done),
                    "y" if len(done) == 1 else "ies",
                    "was" if len(done) == 1 else "were",
                    "it" if len(done) == 1 else "they",
                )
            else:
                msg += " No library was changed."
            raise PackError(msg) from e
        done.append((path, data))
        saved += max(0, len(data) - len(packed))
    return PackResult(len(done), saved, [p for p, _d in done], failures, backup)


class CompositePackResult(tuple):
    """``(instances_deleted, files_changed)`` -- plus ``unused`` (the
    composer_store.UnusedInstance list found), ``files`` (the files changed)
    and ``backup``."""

    def __new__(cls, unused=(), files=(), backup=None, deleted=0):
        self = super().__new__(cls, (deleted, len(files)))
        self.unused = list(unused)
        self.files = list(files)
        self.backup = backup
        self.deleted = deleted
        return self


def pack_composition_caches(
    project_dir: str, extra_refs=(), backup: Optional[str] = None, dry_run: bool = False, extra_links=()
) -> CompositePackResult:
    """Delete composite genie instances no page uses."""
    from . import composer_store as CS

    refs = CS.referenced_instances(project_dir, extra_refs)
    refs |= {(str(g).lower(), str(h).lower()) for g, h in extra_links}
    unused = CS.unused_instances(project_dir, refs)
    if dry_run or not unused:
        return CompositePackResult(unused, (), backup, 0)
    files = CS.delete_instances(unused, backup=backup)
    return CompositePackResult(unused, files, backup, len(unused))


def restore_pack_backup(folder: str, only: Optional[List[str]] = None) -> PackResult:
    """Put the libraries of a Pack Libraries backup back (atomically).
    ``only`` = the original paths to restore (default: all). Returns a
    PackResult: ``packed`` = the files restored, ``failures`` the others."""
    from ..infra import backups

    m = backups.read_manifest(folder)
    want = {path_key(p) for p in only} if only else None
    done, failures = [], []
    for e in m.get("entries") or []:
        orig = e.get("original", "")
        if want is not None and path_key(orig) not in want:
            continue
        try:
            backups.restore_file(folder, e)
            done.append(orig)
        except OSError as ex:
            failures.append((orig, ex.strerror or str(ex)))
    return PackResult(len(done), 0, done, failures, folder)
