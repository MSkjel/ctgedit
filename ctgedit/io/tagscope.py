"""Which projects a page's tags, equipment and lists come from (its tag scope)."""

from __future__ import annotations

from ..infra.paths import path_key

from ..format.fileio import file_stamp, find_ci

import os
import stat as _stat
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from ..infra.logsetup import note_exception as _note_exc

OWN, INCLUDE, INCLUDER, COMPILED = "own", "include", "includer", "compiled"


def project_name(path: str) -> str:
    """A project's name: its folder's name."""
    return os.path.basename(os.path.normpath(path)) or path


@dataclass(frozen=True)
class ScopeProject:
    """One project of a scope: its name, folder and how it relates to the page's."""

    name: str
    dir: str
    relation: str


@dataclass(frozen=True)
class TagScope:
    """The projects of one page folder's scope, in search order (see the module doc)."""

    project_dir: str
    root: Optional[str]
    roots: Tuple[str, ...]
    projects: Tuple[ScopeProject, ...]

    @property
    def project(self) -> str:
        return project_name(self.project_dir)

    @property
    def key(self) -> tuple:
        """What identifies the scope for caches built over it."""
        return tuple(path_key(p.dir) for p in self.projects)

    def dirs(self) -> List[str]:
        return [p.dir for p in self.projects]

    def names(self) -> List[str]:
        return [p.name for p in self.projects]

    def near(self) -> List[ScopeProject]:
        """The page's project and the projects it includes."""
        return [p for p in self.projects if p.relation in (OWN, INCLUDE)]

    def far(self) -> List[ScopeProject]:
        """The projects compiled with it (includers and the rest of the root)."""
        return [p for p in self.projects if p.relation in (INCLUDER, COMPILED)]

    def project_for_dir(self, directory: str) -> Optional[ScopeProject]:
        k = path_key(directory)
        return next((p for p in self.projects if path_key(p.dir) == k), None)

    def by_name(self, name: str) -> Optional[ScopeProject]:
        low = (name or "").lower()
        return next((p for p in self.projects if p.name.lower() == low), None)

    def tables(self, table: str) -> List[Tuple[ScopeProject, str]]:
        """(project, path) of every project of the scope that has ``table``
        ('variable.dbf'), in scope order."""
        return [(p, path) for p, path, _st in self.stamped_tables(table)]

    def stamped_tables(self, table: str) -> List[Tuple[ScopeProject, str, tuple]]:
        """(project, path, :func:`file_stamp`) of every project of the scope
        that has ``table``, in scope order (one stat each)."""
        out = []
        for p in self.projects:
            path, st = stamp_in(p.dir, table)
            if st is not None:
                out.append((p, path, st))
        return out


# ------------------------------------------------------------------ table files
def stamp_in(folder: str, name: str):
    """(path, stamp) of ``folder``'s table ``name`` whatever its case, (None,
    None) when absent: :func:`~ctgedit.format.fileio.find_ci` + :func:`file_stamp` in one stat on
    Windows (the scope checks run on every edit)."""
    if os.name != "nt":
        p = find_ci(folder, name)
        st = file_stamp(p)
        return (p, st) if st is not None else (None, None)
    p = os.path.join(folder, name)
    try:
        st = os.stat(p)
    except OSError:
        return None, None
    if not _stat.S_ISREG(st.st_mode):
        return None, None
    return p, (path_key(p), st.st_mtime_ns, st.st_size)


# ------------------------------------------------------------------ root choice
_CHOSEN: Dict[str, str] = {}
_LOCK = threading.RLock()


def choose_root(project: str, root: Optional[str]) -> None:
    """Make ``root`` the compiled project the scope of ``project`` (a name or
    folder) uses when several compile it (None = back to the default)."""
    key = project_name(project).lower()
    with _LOCK:
        if root:
            _CHOSEN[key] = project_name(root)
        else:
            _CHOSEN.pop(key, None)


def chosen_root(project: str) -> Optional[str]:
    with _LOCK:
        return _CHOSEN.get(project_name(project).lower())


def _last_database() -> str:
    try:
        from . import citect_config

        return (citect_config.ini_value("ctedit", "lastdatabase") or "").strip()
    except Exception:  # noqa: BLE001 -- swallow-ok: no install, an unreadable ini
        return ""


# ------------------------------------------------------------------ topology
def _catalog_for(project_dir: str):
    """The catalog of the install the folder is in, and the folder's project in it
    (None for a folder that is no project of an install)."""
    from .catalog import catalog_for

    parent = os.path.dirname(os.path.abspath(project_dir))
    user = parent if find_ci(parent, "MASTER.DBF") else None
    cat = catalog_for(user)
    try:
        info = cat.project_for_dir(project_dir)
    except Exception:  # noqa: BLE001
        _note_exc("tagscope._catalog_for", project=project_dir)
        info = None
    return cat, info


def _topology_stamps(cat) -> tuple:
    """The stamps of what the topology is read from: every install's MASTER.DBF and
    every project's include.dbf."""
    out = []
    for inst in getattr(cat, "installs", ()):
        out.append(stamp_in(inst.user_dir, "MASTER.DBF")[1])
    try:
        projects = cat.projects()
    except Exception:  # noqa: BLE001
        _note_exc("tagscope._topology_stamps")
        projects = []
    for p in projects:
        out.append((p.name.lower(), stamp_in(p.dir, "include.dbf")[1]))
    return tuple(out)


_TOPO_SEEN: Dict[int, tuple] = {}


def _fresh_catalog(project_dir: str):
    """The catalog + project, re-read when MASTER.DBF or an include.dbf changed
    since this module last used the catalog."""
    cat, info = _catalog_for(project_dir)
    st = _topology_stamps(cat)
    with _LOCK:
        seen = _TOPO_SEEN.get(id(cat))
        _TOPO_SEEN[id(cat)] = st
    if seen is not None and seen != st:
        try:
            cat.reload()
        except Exception:  # noqa: BLE001
            _note_exc("tagscope._fresh_catalog")
        cat, info = _catalog_for(project_dir)
        st = _topology_stamps(cat)
        with _LOCK:
            _TOPO_SEEN[id(cat)] = st
    return cat, info, st


def _roots_of(cat, info) -> List:
    """The root projects whose closure holds ``info`` (itself when it is a root)."""
    if info is None:
        return []
    if info.is_root:
        return [info]
    out = []
    for r in cat.roots():
        try:
            if any(p.key == info.key for p in cat.closure(r)):
                out.append(r)
        except Exception:  # noqa: BLE001
            _note_exc("tagscope._roots_of")
            continue
    return sorted(out, key=lambda p: p.name.lower())


def roots(project_dir: str) -> List[str]:
    """The names of the compiled projects (roots) that include the folder's
    project, by name ([] for a folder that is no project; [itself] for a root)."""
    try:
        cat, info, _st = _fresh_catalog(project_dir)
        return [r.name for r in _roots_of(cat, info)]
    except Exception:  # noqa: BLE001
        _note_exc("tagscope.roots", project=project_dir)
        return []


def _pick_root(project: str, candidates: List, root: Optional[str]):
    if not candidates:
        return None
    by = {c.name.lower(): c for c in candidates}
    for want in (root, chosen_root(project)):
        if want and project_name(want).lower() in by:
            return by[project_name(want).lower()]
    if len(candidates) > 1:
        last = _last_database()
        if last and last.lower() in by:
            return by[last.lower()]
    return candidates[0]


def _down(project_dir: str) -> List[str]:
    from ..format.projects import include_search_dirs

    try:
        return include_search_dirs(project_dir)
    except Exception:  # noqa: BLE001 -- a broken registry still has the project
        _note_exc("tagscope._down", project=project_dir)
        return []


def _own_scope(project_dir: str, root: Optional[str]):
    """(projects, chosen root name, candidate root names, topology stamps) of one
    folder, CTG_LIB_PATH not applied."""
    pdir = os.path.abspath(project_dir)
    out: List[ScopeProject] = []
    seen = set()

    def add(name: str, d: str, rel: str) -> None:
        k = path_key(d)
        if k in seen or not os.path.isdir(d):
            return
        seen.add(k)
        out.append(ScopeProject(name, d, rel))

    try:
        cat, info, st = _fresh_catalog(pdir)
    except Exception:  # noqa: BLE001 -- no catalog: the folder and its includes
        _note_exc("tagscope._own_scope", project=pdir)
        cat, info, st = None, None, ()
    add(info.name if info is not None else project_name(pdir), pdir, OWN)
    for d in _down(pdir):
        add(project_name(d), d, INCLUDE)
    cands = _roots_of(cat, info) if cat is not None else []
    chosen = _pick_root(project_name(pdir), cands, root)
    if cat is not None and info is not None and chosen is not None and chosen.key != info.key:
        try:
            closure = cat.closure(chosen)
        except Exception:  # noqa: BLE001
            _note_exc("tagscope._own_scope", project=pdir)
            closure = []
        inside = {p.key for p in closure}
        try:
            ups = [p for p in cat.dependents(info) if p.key in inside]
        except Exception:  # noqa: BLE001
            _note_exc("tagscope._own_scope", project=pdir)
            ups = []
        for p in ups:
            add(p.name, p.dir, INCLUDER)
        for p in closure:
            add(p.name, p.dir, COMPILED)
    elif cat is not None and info is not None and chosen is None:
        # included, but by no compiled project: its includers only
        try:
            for p in cat.dependents(info):
                add(p.name, p.dir, INCLUDER)
        except Exception:  # noqa: BLE001
            _note_exc("tagscope._own_scope", project=pdir)
    return (out, chosen.name if chosen is not None else None, tuple(c.name for c in cands), st)


def _extra_dirs() -> List[str]:
    from ..infra.paths import lib_path_dirs

    return [os.path.abspath(p) for p in lib_path_dirs() if os.path.isdir(p)]


_SCOPES: Dict[tuple, tuple] = {}
_MAX_SCOPES = 64


def scope(project_dir: str, root: Optional[str] = None) -> TagScope:
    """The tag scope of the page folder ``project_dir`` (see the module doc);
    ``root`` = the compiled project to use when several include it."""
    pdir = os.path.abspath(project_dir or os.curdir)
    extra = tuple(d for d in _extra_dirs() if path_key(d) != path_key(pdir))
    key = (
        path_key(pdir),
        (root or "").lower(),
        (chosen_root(pdir) or "").lower(),
        tuple(path_key(d) for d in extra),
        _last_database().lower(),
    )
    try:
        sig = tuple(_fresh_catalog(d)[2] for d in (pdir,) + extra)
    except Exception:  # noqa: BLE001
        _note_exc("tagscope.scope", project=pdir)
        sig = None
    with _LOCK:
        hit = _SCOPES.get(key)
        if hit is not None and sig is not None and hit[0] == sig:
            return hit[1]
    projects, chosen, cands, _st = _own_scope(pdir, root)
    seen = {path_key(p.dir) for p in projects}
    for d in extra:
        more = _own_scope(d, None)[0]
        for p in more:
            if path_key(p.dir) not in seen:
                seen.add(path_key(p.dir))
                rel = OWN if path_key(p.dir) == path_key(d) else p.relation
                projects.append(ScopeProject(p.name, p.dir, rel))
    sc = TagScope(pdir, chosen, cands, tuple(projects))
    with _LOCK:
        if len(_SCOPES) >= _MAX_SCOPES:
            _SCOPES.clear()
        if sig is not None:
            _SCOPES[key] = (sig, sc)
    return sc


def clear() -> None:
    """Forget the cached scopes and root choices (tests)."""
    with _LOCK:
        _SCOPES.clear()
        _TOPO_SEEN.clear()
        _CHOSEN.clear()


def relation_label(relation: str) -> str:
    """How a project relates to the page's, for tool tips."""
    return {
        OWN: "this page's project",
        INCLUDE: "included",
        INCLUDER: "includes this project",
        COMPILED: "compiled with this project",
    }.get(relation, relation)


from ..infra import caches as _caches  # noqa: E402


# (the scopes and include topologies; not _CHOSEN: the user's From: choice is state)
def _clear_scope_tables() -> None:
    with _LOCK:
        _SCOPES.clear()
        _TOPO_SEEN.clear()


_caches.register("tagscope.scopes", _SCOPES, clear=_clear_scope_tables, on=("refresh", "tests"))
_caches.register("tagscope.topologies", _TOPO_SEEN, clear=_clear_scope_tables, on=("refresh", "tests"))
