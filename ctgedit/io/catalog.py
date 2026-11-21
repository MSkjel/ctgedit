"""A fast index of the projects a page browser shows."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple, Union

from ..format import pagesummary
from ..format.projects import (
    IMPLICIT_INCLUDE_PROJECTS,
    included_project_names,
    read_dbf_rows,
    sibling_install_user_dirs,
)
from .citect_config import live_user_dir

__all__ = [
    "KINDS",
    "LIB_EXT",
    "InstallInfo",
    "ProjectInfo",
    "LibraryInfo",
    "Entry",
    "ProjectCatalog",
    "catalog_for",
    "natural_key",
    "is_super_genie",
    "SUPER_KIND",
    "INCLUDE_OK",
    "INCLUDE_CYCLE",
    "INCLUDE_MISSING",
    "INCLUDE_IMPLICIT",
]

#: :meth:`ProjectCatalog.include_children` row states
INCLUDE_OK = "ok"  # an include to show with its own includes
INCLUDE_CYCLE = "cycle"  # already on the path above (shown as a leaf)
INCLUDE_MISSING = "missing"  # no MASTER.DBF row, or the row's folder is gone
INCLUDE_IMPLICIT = "implicit"  # Include / System under a top-level project

KINDS = ("page", "template", "symbol", "genie")
LIB_EXT = {"template": ".ctt", "symbol": ".ctl", "genie": ".ctm"}
#: the Open dialog's Super Genie tab (CtDraw lists them apart): the '@SUPER'
#: elements of the genie libraries; ``elements`` / ``entries`` / ``count``
#: accept it like the other kinds ('genie' still lists every .ctm element)
SUPER_KIND = "supergenie"
_EXT_KIND = {v: k for k, v in LIB_EXT.items()}
_CT_EXTS = (".ctg", ".ctt", ".ctl", ".ctm")
# pages.DBF NAME is C(64) (io.project.Project.NAME_LEN)
NAME_LEN = 64
# a library that is only the container header + an 8-slot directory holds nothing
_EMPTY_LIBRARY_SIZE = 0x10 + 8 * 42
_IMPLICIT = tuple(n.lower() for n in IMPLICIT_INCLUDE_PROJECTS)
#: a folder listing is kept only when the folder was last changed this long before it was
#: listed (symbols._DIR_RACY_NS: the file-system time has a granularity of up to 2 s)
_DIR_RACY_NS = 3_000_000_000


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


_NAT_RE = re.compile(r"(\d+)")


def natural_key(s: str) -> tuple:
    """Case-insensitive natural sort key (``page2`` < ``page10``)."""
    return tuple(int(t) if t.isdigit() else t for t in _NAT_RE.split(s.lower()))


def is_super_genie(element: str) -> bool:
    """A super genie library element (``<name>@SUPER`` in a .ctm). CtDraw's
    library list leaves these out of Paste Genie and lists
    only these for Paste Super Genie (strstr, case-sensitive: CtDraw names
    them with '@SUPER')."""
    return "@SUPER" in (element or "")


def _stamp(path: str) -> Tuple[int, int]:
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return (0, -1)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallInfo:
    """One Citect install's ``User`` directory."""

    user_dir: str
    is_live: bool = False

    @property
    def label(self) -> str:
        """``AVEVA Plant SCADA 2023 R2`` (the install folder name)."""
        return os.path.basename(os.path.dirname(os.path.abspath(self.user_dir))) or self.user_dir

    @property
    def short_label(self) -> str:
        """``2023 R2`` (the product prefix dropped)."""
        lab = self.label
        for pre in ("AVEVA Plant SCADA ", "Plant SCADA ", "Citect SCADA ", "Citect "):
            if lab.startswith(pre):
                return lab[len(pre) :] or lab
        return lab

    @property
    def key(self) -> str:
        return _norm(self.user_dir)


@dataclass(eq=False)
class ProjectInfo:
    """A project (or loose folder) inside one install."""

    name: str
    dir: str
    install: Optional[InstallInfo]
    registered: bool = True  # listed in MASTER.DBF
    exists: bool = True
    includes: List[str] = field(default_factory=list)  # direct include.dbf rows
    included_by: List[str] = field(default_factory=list)
    is_root: bool = False
    has_pages_dbf: bool = False
    is_folder: bool = False  # a loose folder, not a Citect project

    @property
    def is_live(self) -> bool:
        return bool(self.install and self.install.is_live)

    @property
    def is_implicit(self) -> bool:
        return self.name.lower() in _IMPLICIT

    @property
    def key(self) -> str:
        return _norm(self.dir)

    @property
    def scope_id(self) -> str:
        """Stable id for settings: ``<install>|<name>`` (folders: ``folder|<dir>``)."""
        if self.is_folder or self.install is None:
            return "folder|" + self.key
        return "%s|%s" % (self.install.key, self.name.lower())

    def __repr__(self) -> str:
        return "ProjectInfo(%r, %r%s)" % (self.name, self.dir, "" if self.is_live else ", other install")


@dataclass(eq=False)
class LibraryInfo:
    """One library file (``.ctt``/``.ctl``/``.ctm``) and its live elements."""

    path: str
    kind: str
    project: str
    mtime_ns: int = 0
    size: int = 0
    word2: int = 0x0C
    elements: List[pagesummary.DirEntry] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower()


@dataclass(eq=False)
class Entry:
    """One browsable item: a page file or a library element."""

    kind: str  # page | template | symbol | genie | supergenie
    project: str  # project display name
    name: str  # page stem, or element name
    path: str  # the .ctg, or the library file
    library: str = ""  # library stem (elements)
    title: str = ""
    comment: str = ""
    area: str = ""
    content_type: str = ""  # pages.DBF CON_TYPE (L1..L4, FP, Master ...)
    registered: Optional[bool] = True  # None = the folder has no pages.DBF (n/a)
    mtime_ns: int = 0
    size: int = 0
    project_dir: str = ""
    install_dir: str = ""  # "" for loose folders outside a User tree
    is_live: bool = True
    shadowed_by: str = ""  # project whose same-named element wins
    superseded_by: str = ""  # registered page this stale file was renamed to
    element_offset: int = -1  # block extent (fast element summary reads)
    element_size: int = 0
    word2: int = 0x0C
    haystack: str = ""

    def __post_init__(self) -> None:
        if not self.haystack:
            self.haystack = " ".join(
                s for s in (self.name, self.title, self.comment, self.area, self.project, self.library) if s
            ).lower()

    # -- identity ---------------------------------------------------------
    @property
    def key(self) -> str:
        """Thumbnail/summary cache key: changes whenever the file changes."""
        return "ctgthumb|%s|%d|%d|%s" % (
            os.path.normcase(self.path),
            self.mtime_ns,
            self.size,
            self.name if self.is_element else "",
        )

    @property
    def is_element(self) -> bool:
        return self.kind != "page"

    @property
    def is_popup(self) -> bool:
        return self.kind == "page" and self.name.startswith("!")

    @property
    def is_other_install(self) -> bool:
        """In a known install that is NOT the live one (loose folders: False)."""
        return bool(self.install_dir) and not self.is_live

    @property
    def shadowed(self) -> bool:
        return bool(self.shadowed_by)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def reference(self) -> str:
        """``library.element`` for elements, the page name for pages."""
        return "%s.%s" % (self.library, self.name) if self.is_element else self.name

    # -- template element names: 'normal@-HD1080' -------------------------
    @property
    def template_name(self) -> str:
        return pagesummary.split_element_name(self.name)[0]

    @property
    def resolution(self) -> str:
        return pagesummary.split_element_name(self.name)[1] if self.kind == "template" else ""

    @property
    def title_bar(self) -> bool:
        return pagesummary.split_element_name(self.name)[2]

    def __repr__(self) -> str:
        return "Entry(%s %s/%s)" % (self.kind, self.project, self.reference)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

ProjectLike = Union[ProjectInfo, str]


class ProjectCatalog:
    """Index of an install's projects, pages and library elements."""

    def __init__(self, user_dir: Optional[str] = None, other_installs: bool = True):
        live = user_dir or live_user_dir()
        self.installs: List[InstallInfo] = []
        if live and os.path.isdir(live):
            self.installs.append(InstallInfo(os.path.abspath(live), True))
            if other_installs:
                for u in sibling_install_user_dirs(live):
                    self.installs.append(InstallInfo(os.path.abspath(u), False))
        self._lock = threading.RLock()
        self._topo: Dict[str, Dict[str, ProjectInfo]] = {}
        #: the stamps the topology was read from (:meth:`topology_stamp`), None until read
        self._topo_stamp: Optional[tuple] = None
        self._folders: Dict[str, ProjectInfo] = {}
        self._pages: Dict[str, tuple] = {}  # project key -> (stamp, entries)
        self._dbf: Dict[str, tuple] = {}  # dbf path -> (stamp, rows)
        self._libs: Dict[str, tuple] = {}  # lib path -> (stamp, LibraryInfo)
        self._counts: Dict[str, tuple] = {}  # dir key -> (dir mtime, counts)
        self._links: Dict[str, tuple] = {}  # page path -> (stamp, link names)
        self._lib_paths: Dict[str, tuple] = {}  # dir -> (dir mtime, {ext: library paths})

    # -- installs / topology -------------------------------------------------
    @property
    def live_install(self) -> Optional[InstallInfo]:
        return self.installs[0] if self.installs and self.installs[0].is_live else None

    @property
    def user_dir(self) -> Optional[str]:
        li = self.live_install
        return li.user_dir if li else None

    def install_for_path(self, path: str) -> Optional[InstallInfo]:
        """The install whose ``User`` tree contains ``path`` (or None)."""
        p = _norm(path)
        for inst in self.installs:
            k = inst.key
            if p == k or p.startswith(k + os.sep):
                return inst
        return None

    def _topology(self, install: InstallInfo) -> Dict[str, ProjectInfo]:
        with self._lock:
            topo = self._topo.get(install.key)
            if topo is not None:
                return topo
            topo = {}
            ud = install.user_dir
            master = os.path.join(ud, "MASTER.DBF")
            if not os.path.isfile(master):
                master = os.path.join(ud, "master.dbf")
            for row in read_dbf_rows(master) if os.path.isfile(master) else []:
                name, path = row.get("NAME", "").strip(), row.get("PATH", "").strip()
                if not name or not path:
                    continue
                path = os.path.normpath(path)
                if name.lower() in topo:
                    continue
                topo[name.lower()] = ProjectInfo(
                    name, path, install, registered=True, exists=os.path.isdir(path)
                )
            registered_dirs = {p.key for p in topo.values()}
            try:
                subdirs = sorted(os.listdir(ud), key=str.lower)
            except OSError:
                subdirs = []
            for d in subdirs:
                full = os.path.join(ud, d)
                if (
                    d.startswith(".")
                    or d.lower() in topo
                    or _norm(full) in registered_dirs
                    or not os.path.isdir(full)
                ):
                    continue
                try:
                    has_ct = any(f.lower().endswith(_CT_EXTS) for f in os.listdir(full))
                except OSError:
                    has_ct = False
                if has_ct:
                    topo[d.lower()] = ProjectInfo(d, full, install, registered=False)
            for info in topo.values():
                if not info.exists:
                    continue
                info.includes = included_project_names(info.dir)
                info.has_pages_dbf = self._find_file(info.dir, "pages.dbf") is not None
            for info in topo.values():
                for inc in info.includes:
                    tgt = topo.get(inc.lower())
                    if tgt is not None and info.name not in tgt.included_by:
                        tgt.included_by.append(info.name)
            for low, info in topo.items():
                info.is_root = (
                    info.exists and info.registered and not info.included_by and low not in _IMPLICIT
                )
            self._topo[install.key] = topo
            self._topo_stamp = self.topology_stamp()
            return topo

    @staticmethod
    def _find_file(directory: str, lower_name: str) -> Optional[str]:
        for cand in (
            lower_name,
            lower_name.upper(),
            lower_name.capitalize(),
            os.path.splitext(lower_name)[0] + os.path.splitext(lower_name)[1].upper(),
        ):
            p = os.path.join(directory, cand)
            if os.path.isfile(p):
                return p
        try:
            for fn in os.listdir(directory):
                if fn.lower() == lower_name:
                    return os.path.join(directory, fn)
        except OSError:
            pass
        return None

    def projects(
        self, install: Optional[InstallInfo] = None, existing_only: bool = True
    ) -> List[ProjectInfo]:
        """Projects of ``install`` (default live), alphabetical."""
        inst = install or self.live_install
        if inst is None:
            return []
        out = [p for p in self._topology(inst).values() if p.exists or not existing_only]
        return sorted(out, key=lambda p: p.name.lower())

    def roots(self, install: Optional[InstallInfo] = None) -> List[ProjectInfo]:
        """Root projects (compiled on their own; nobody includes them)."""
        return [p for p in self.projects(install) if p.is_root]

    def project(self, name: str, install: Optional[InstallInfo] = None) -> Optional[ProjectInfo]:
        """Project by (case-insensitive) name in ``install`` (default live)."""
        inst = install or self.live_install
        if inst is None or not name:
            return None
        return self._topology(inst).get(name.lower())

    def project_for_dir(self, directory: str) -> Optional[ProjectInfo]:
        """The project whose folder is ``directory`` (any install), else None."""
        if not directory:
            return None
        k = _norm(directory)
        for inst in self.installs:
            for p in self._topology(inst).values():
                if p.exists and p.key == k:
                    return p
        return None

    def project_for_path(self, path: str) -> Optional[ProjectInfo]:
        """The project containing file ``path`` (any install), else None."""
        if not path:
            return None
        return self.project_for_dir(os.path.dirname(os.path.abspath(path)))

    def folder(self, directory: str) -> ProjectInfo:
        """A loose folder (outside any project) as a browsable pseudo-project."""
        k = _norm(directory)
        info = self.project_for_dir(directory)
        if info is not None:
            return info
        with self._lock:
            info = self._folders.get(k)
            if info is None:
                name = os.path.basename(os.path.normpath(directory)) or directory
                info = ProjectInfo(
                    name,
                    os.path.abspath(directory),
                    self.install_for_path(directory),
                    registered=False,
                    exists=os.path.isdir(directory),
                    is_folder=True,
                )
                info.has_pages_dbf = self._find_file(directory, "pages.dbf") is not None
                self._folders[k] = info
            return info

    def _as_project(self, project: ProjectLike) -> Optional[ProjectInfo]:
        if isinstance(project, ProjectInfo):
            return project
        if isinstance(project, str):
            if os.sep in project or "/" in project:
                return self.project_for_dir(project) or self.folder(project)
            return self.project(project)
        return None

    def closure(self, project: ProjectLike) -> List[ProjectInfo]:
        """``project`` + its recursive include chain + the implicit includes
        (Include / System), dedup-guarded, in CtDraw's search order -- names
        resolved within the project's own install. Missing projects skipped."""
        root = self._as_project(project)
        if root is None:
            return []
        if root.install is None or root.is_folder:
            return [root]
        topo = self._topology(root.install)
        out: List[ProjectInfo] = []
        seen = set()

        def add(info: Optional[ProjectInfo]) -> None:
            if info is None or not info.exists or info.key in seen:
                return
            seen.add(info.key)
            out.append(info)
            for inc in info.includes:
                add(topo.get(inc.lower()))

        add(root)
        for n in IMPLICIT_INCLUDE_PROJECTS:
            add(topo.get(n.lower()))
        return out

    def dependents(self, project: ProjectLike) -> List[ProjectInfo]:
        """The projects whose include chain reaches ``project`` (not the
        project itself): they compile it in. Every project of the install
        reaches the implicit Include / System."""
        info = self._as_project(project)
        if info is None or info.install is None or info.is_folder:
            return []
        topo = self._topology(info.install)
        if info.is_implicit:
            return sorted(
                (p for p in topo.values() if p.exists and p.key != info.key), key=lambda p: p.name.lower()
            )
        out: List[ProjectInfo] = []
        seen = {info.key}
        todo = list(info.included_by)
        while todo:
            p = topo.get(todo.pop(0).lower())
            if p is None or not p.exists or p.key in seen:
                continue
            seen.add(p.key)
            out.append(p)
            todo.extend(p.included_by)
        return out

    def top_level(self, install: Optional[InstallInfo] = None) -> List[ProjectInfo]:
        """The top level of a project tree: projects nothing else includes."""
        inst = install or self.live_install
        if inst is None:
            return []
        topo = self._topology(inst)
        out = [p for low, p in topo.items() if not p.included_by and low not in _IMPLICIT]
        if not out:
            out = [p for low, p in topo.items() if low in _IMPLICIT and p.exists]
        out.sort(key=lambda p: natural_key(p.name))
        # projects that only include each other (X <-> Y, nobody else
        # includes them) have an includer each: the first of such a group
        # becomes a top-level row, or the tree would not show them at all
        reached = set()
        todo = list(out)
        while todo:
            p = todo.pop()
            if p.key in reached:
                continue
            reached.add(p.key)
            todo.extend(t for t in (topo.get(n.lower()) for n in p.includes) if t is not None and t.exists)
        for low, p in sorted(topo.items(), key=lambda kv: natural_key(kv[1].name)):
            if p.exists and low not in _IMPLICIT and p.key not in reached:
                out.append(p)
                todo = [p]
                while todo:
                    q = todo.pop()
                    if q.key in reached:
                        continue
                    reached.add(q.key)
                    todo.extend(
                        t for t in (topo.get(n.lower()) for n in q.includes) if t is not None and t.exists
                    )
        return out

    def include_children(
        self, project: ProjectLike, path: Iterable[ProjectInfo] = (), top: Optional[bool] = None
    ) -> List[Tuple[ProjectInfo, str]]:
        """The rows under ``project`` in a project tree."""
        info = self._as_project(project)
        if info is None or info.install is None or info.is_folder or not info.exists:
            return []
        topo = self._topology(info.install)
        above = {p.key for p in path if p is not None and p.exists}
        above.add(info.key)
        out: List[Tuple[ProjectInfo, str]] = []
        seen = set()
        for inc in info.includes:
            low = inc.strip().lower()
            if not low or low in seen:
                continue
            seen.add(low)
            tgt = topo.get(low)
            if tgt is None:
                out.append(
                    (
                        ProjectInfo(inc.strip(), "", info.install, registered=False, exists=False),
                        INCLUDE_MISSING,
                    )
                )
            elif not tgt.exists:
                out.append((tgt, INCLUDE_MISSING))
            elif tgt.key in above:
                out.append((tgt, INCLUDE_CYCLE))
            else:
                out.append((tgt, INCLUDE_OK))
        if top is None:
            top = not info.included_by and not info.is_implicit
        if top:
            for n in IMPLICIT_INCLUDE_PROJECTS:
                tgt = topo.get(n.lower())
                if tgt is not None and tgt.exists and n.lower() not in seen and tgt.key != info.key:
                    out.append((tgt, INCLUDE_IMPLICIT))
        return out

    def pages_elsewhere(self, project: ProjectLike) -> Dict[str, str]:
        """Page name -> project for pages of other projects compiled with ``project``."""
        info = self._as_project(project)
        if info is None or info.is_folder:
            return {}
        group: List[ProjectInfo] = self.closure(info)[1:]
        deps = self.dependents(info)
        group += deps
        for d in deps:
            if d.is_root:
                group += self.closure(d)
        out: Dict[str, str] = {}
        seen = {info.key}
        for p in group:
            if p.key in seen:
                continue
            seen.add(p.key)
            for e in self.pages(p):
                if e.registered is not False:
                    out.setdefault(e.name.lower()[:NAME_LEN], p.name)
        return out

    def link_names(self, page_path: str) -> List[str]:
        """The page's link/import-table names (pagesummary.read_link_names),
        cached by the file's (mtime, size) -- the Library panel's Used By and
        the usage warnings read every page of a project."""
        st = _stamp(page_path)
        with self._lock:
            hit = self._links.get(page_path)
            if hit is not None and hit[0] == st:
                return hit[1]
        names = pagesummary.read_link_names(page_path)
        with self._lock:
            self._links[page_path] = (st, names)
        return names

    def usage_index(self, project: ProjectLike) -> Dict[str, List[Entry]]:
        """Reference key -> the pages that use it, for a library's project."""
        from ..format.links import strip_stamp

        info = self._as_project(project)
        if info is None:
            return {}
        out: Dict[str, List[Entry]] = {}
        for p in [info] + self.dependents(info):
            for e in self.pages(p):
                seen = set()
                for n in self.link_names(e.path):
                    k = strip_stamp(n).lower()
                    if k not in seen:
                        seen.add(k)
                        out.setdefault(k, []).append(e)
        return out

    def element_usage(self, lib_path: str, element: str) -> List[Entry]:
        """The pages that place ``library.element``."""
        stem, ext = os.path.splitext(os.path.basename(lib_path))
        want = ("%s.%s.<%s" % (stem, element, ext.lstrip("."))).lower()
        d = os.path.dirname(os.path.abspath(lib_path))
        info = self.project_for_dir(d) or self.folder(d)
        return list(self.usage_index(info).get(want, []))

    # -- cheap counts (tree labels) -----------------------------------------
    def counts(self, project: ProjectLike) -> Dict[str, int]:
        """``{kind: n}``: .ctg files for pages, non-empty library FILES for the
        element kinds -- one scandir per folder, cached by the folder mtime."""
        info = self._as_project(project)
        empty = {k: 0 for k in KINDS}
        if info is None or not info.exists:
            return empty
        dm = _stamp(info.dir)[0]
        with self._lock:
            hit = self._counts.get(info.key)
            if hit is not None and hit[0] == dm:
                return hit[1]
        c = dict(empty)
        try:
            with os.scandir(info.dir) as it:
                for e in it:
                    low = e.name.lower()
                    ext = os.path.splitext(low)[1]
                    if ext == ".ctg":
                        c["page"] += 1
                    elif ext in _EXT_KIND:
                        try:
                            if e.stat().st_size <= _EMPTY_LIBRARY_SIZE:
                                continue
                        except OSError:
                            continue
                        c[_EXT_KIND[ext]] += 1
        except OSError:
            pass
        with self._lock:
            self._counts[info.key] = (dm, c)
        return c

    def count(self, project: ProjectLike, kind: str) -> int:
        if kind == SUPER_KIND:
            # genie libraries holding a super genie (directory reads, cached)
            return sum(
                1
                for lib in self.libraries(project, "genie")
                if any(is_super_genie(de.name) for de in lib.elements)
            )
        return self.counts(project).get(kind, 0)

    # -- pages ---------------------------------------------------------------
    def _dbf_rows(self, path: Optional[str]) -> List[dict]:
        if not path:
            return []
        st = _stamp(path)
        with self._lock:
            hit = self._dbf.get(path)
            if hit is not None and hit[0] == st:
                return hit[1]
        rows = read_dbf_rows(path)
        with self._lock:
            self._dbf[path] = (st, rows)
        return rows

    def page_rows(self, project: ProjectLike) -> List[dict]:
        """The project's pages.DBF rows (cached by the table's stamp)."""
        info = self._as_project(project)
        if info is None or not info.exists:
            return []
        return self._dbf_rows(self._find_file(info.dir, "pages.dbf"))

    def pages(self, project: ProjectLike) -> List[Entry]:
        """Page entries of a project / folder, natural-sorted by name."""
        info = self._as_project(project)
        if info is None or not info.exists:
            return []
        dbf = self._find_file(info.dir, "pages.dbf")
        stamp = (_stamp(info.dir)[0],) + (_stamp(dbf) if dbf else (0, -1))
        with self._lock:
            hit = self._pages.get(info.key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        rows = self._dbf_rows(dbf)
        has_dbf = dbf is not None
        by_name = {}
        by_title = {}
        for r in rows:
            n = r.get("NAME", "").strip()
            if n:
                by_name.setdefault(n.lower()[:NAME_LEN], r)
                t = r.get("TITLE", "").strip()
                if t:
                    by_title.setdefault(t.lower(), n)
        install = info.install
        out: List[Entry] = []
        try:
            with os.scandir(info.dir) as it:
                for e in it:
                    if not e.name.lower().endswith(".ctg"):
                        continue
                    try:
                        if not e.is_file():
                            continue
                        st = e.stat()
                    except OSError:
                        continue
                    stem = e.name[:-4]
                    r = by_name.get(stem.lower()[:NAME_LEN])
                    out.append(
                        Entry(
                            "page",
                            info.name,
                            stem,
                            e.path,
                            title=(r or {}).get("TITLE", ""),
                            comment=(r or {}).get("COMMENT", ""),
                            area=(r or {}).get("AREA", ""),
                            content_type=(r or {}).get("CON_TYPE", ""),
                            registered=(r is not None) if has_dbf else None,
                            mtime_ns=st.st_mtime_ns,
                            size=st.st_size,
                            project_dir=info.dir,
                            install_dir=install.user_dir if install else "",
                            is_live=bool(install and install.is_live),
                        )
                    )
        except OSError:
            pass
        self._mark_superseded(out, by_title)
        out.sort(key=lambda x: natural_key(x.name))
        with self._lock:
            self._pages[info.key] = (stamp, out)
        return out

    _SUFFIX_RE = re.compile(r"((?:_\d+){2,})$")

    @classmethod
    def _mark_superseded(cls, entries: List[Entry], by_title: Dict[str, str]) -> None:
        """An UNREGISTERED file whose stem is a registered row's TITLE (CtDraw
        keeps the old name as the title on rename), or that shares its numeric
        suffix with exactly one newer registered page, was probably renamed."""
        files = {e.name.lower(): e for e in entries}
        reg = [e for e in entries if e.registered]
        for e in entries:
            if e.registered is not False:
                continue
            new = by_title.get(e.name.lower())
            if new and new.lower() in files and new.lower() != e.name.lower():
                e.superseded_by = files[new.lower()].name
                continue
            m = cls._SUFFIX_RE.search(e.name)
            if not m:
                continue
            suf = m.group(1).lower()
            cands = [
                r
                for r in reg
                if r.name.lower().endswith(suf)
                and r.mtime_ns > e.mtime_ns
                and r.name.lower() != e.name.lower()
            ]
            if len(cands) == 1:
                e.superseded_by = cands[0].name

    def page_names(self, project: ProjectLike) -> Dict[str, str]:
        """lower page name -> file path of the project's ``.ctg`` files."""
        return {e.name.lower(): e.path for e in self.pages(project)}

    def registered_names(self, project: ProjectLike) -> set:
        """lower NAMEs registered in the project's pages.DBF."""
        return {
            r.get("NAME", "").strip().lower() for r in self.page_rows(project) if r.get("NAME", "").strip()
        }

    def entry_for_path(self, path: str) -> Optional[Entry]:
        """The page Entry for any ``.ctg`` path (project-joined when the file is
        in a known project, else a loose-folder entry). None if absent."""
        if not path or not os.path.isfile(path):
            return None
        info = self.project_for_path(path) or self.folder(os.path.dirname(os.path.abspath(path)))
        k = _norm(path)
        for e in self.pages(info):
            if _norm(e.path) == k:
                return e
        return None

    def live_copy_of(self, path: str) -> Optional[str]:
        """For a page in a NON-live install: the same project + page in the live
        install, when it exists (else None)."""
        info = self.project_for_path(path)
        live = self.live_install
        if info is None or live is None or info.is_live:
            return None
        tgt = self.project(info.name, live)
        if tgt is None or not tgt.exists:
            return None
        cand = os.path.join(tgt.dir, os.path.basename(path))
        return cand if os.path.isfile(cand) else None

    # -- libraries / elements -----------------------------------------------
    def _library(self, path: str, kind: str, project: str) -> Optional[LibraryInfo]:
        st = _stamp(path)
        with self._lock:
            hit = self._libs.get(path)
            if hit is not None and hit[0] == st:
                lib = hit[1]
                lib.project = project
                return lib
        d = pagesummary.read_directory(path)
        if not d.ok:
            return None
        lib = LibraryInfo(
            path, kind, project, mtime_ns=st[0], size=st[1], word2=d.word2, elements=d.elements()
        )
        with self._lock:
            self._libs[path] = (st, lib)
        return lib

    def _library_paths(self, directory: str) -> Optional[Dict[str, List[str]]]:
        """{extension: library files of ``directory``}, from one listing."""
        dm = _stamp(directory)[0]
        with self._lock:
            hit = self._lib_paths.get(directory)
        if hit is not None and hit[0] == dm:
            return hit[1]
        listed_at = time.time_ns()
        by_ext: Dict[str, List[str]] = {x: [] for x in _EXT_KIND}
        try:
            with os.scandir(directory) as it:
                for e in it:
                    low = e.name.lower()
                    for x, paths in by_ext.items():
                        if low.endswith(x) and e.is_file():
                            paths.append(e.path)
        except OSError:
            return None
        for paths in by_ext.values():
            paths.sort(key=lambda s: os.path.basename(s).lower())
        if dm and dm < listed_at - _DIR_RACY_NS:
            with self._lock:
                self._lib_paths[directory] = (dm, by_ext)
        return by_ext

    def libraries(self, project: ProjectLike, kind: str, include_empty: bool = False) -> List[LibraryInfo]:
        """The project's ``kind`` libraries (directory-only reads, cached)."""
        info = self._as_project(project)
        ext = LIB_EXT.get(kind)
        if info is None or not info.exists or ext is None:
            return []
        listing = self._library_paths(info.dir)
        if listing is None:
            return []
        out = []
        for p in listing[ext]:
            lib = self._library(p, kind, info.name)
            if lib is not None and (lib.elements or include_empty):
                out.append(lib)
        return out

    def elements(self, project: ProjectLike, kind: str) -> List[Entry]:
        """Element entries of the project's ``kind`` libraries (directory order;
        ``SUPER_KIND``: the super genies of its genie libraries)."""
        info = self._as_project(project)
        if info is None:
            return []
        install = info.install
        out: List[Entry] = []
        supers = kind == SUPER_KIND
        for lib in self.libraries(info, "genie" if supers else kind):
            stem = lib.stem
            for de in lib.elements:
                if supers and not is_super_genie(de.name):
                    continue
                out.append(
                    Entry(
                        kind,
                        info.name,
                        de.name,
                        lib.path,
                        library=stem,
                        registered=None,
                        mtime_ns=lib.mtime_ns,
                        size=lib.size,
                        project_dir=info.dir,
                        install_dir=install.user_dir if install else "",
                        is_live=bool(install and install.is_live),
                        element_offset=de.offset,
                        element_size=de.size,
                        word2=lib.word2,
                    )
                )
        return out

    def entries(self, projects: Iterable[ProjectLike], kind: str, shadow: bool = True) -> List[Entry]:
        """Every ``kind`` entry across ``projects``."""
        out: List[Entry] = []
        first: Dict[Tuple[str, str, str], str] = {}
        seen_dirs = set()
        for p in projects:
            info = self._as_project(p)
            if info is None or info.key in seen_dirs:
                continue
            seen_dirs.add(info.key)
            if kind == "page":
                out.extend(self.pages(info))
                continue
            for e in self.elements(info, kind):
                if shadow:
                    k = (e.library.lower(), e.ext, e.name.lower())
                    winner = first.get(k)
                    if winner is None:
                        first[k] = e.project
                    elif winner != e.project:
                        e.shadowed_by = winner
                out.append(e)
        return out

    # -- cache control ------------------------------------------------------
    def topology_stamp(self) -> tuple:
        """The (path, mtime_ns, size) of what the topology is read from: every install's
        MASTER.DBF and the include.dbf of every project read so far (a project added or
        an include changed in Studio moves one of them)."""

        def stamp(p):
            if not p:
                return None
            try:
                st = os.stat(p)
            except OSError:
                return (p, None, None)
            return (p, st.st_mtime_ns, st.st_size)

        out = []
        for inst in self.installs:
            master = os.path.join(inst.user_dir, "MASTER.DBF")
            if not os.path.isfile(master):
                master = os.path.join(inst.user_dir, "master.dbf")
            out.append(stamp(master))
        with self._lock:
            infos = [i for t in self._topo.values() for i in t.values()]
        for info in infos:
            if info.exists:
                out.append((info.key, stamp(self._find_file(info.dir, "include.dbf"))))
        return tuple(out)

    def refresh_topology(self) -> bool:
        """Forget everything (:meth:`reload`) when MASTER.DBF or an include.dbf changed
        since the topology was read: True then. A few stats; the Library panel's chain and
        the page browser's tree follow a project or an include added in Studio."""
        with self._lock:
            if not self._topo or self._topo_stamp is None:
                return False
            if self.topology_stamp() == self._topo_stamp:
                return False
            self.reload()
            return True

    def refresh(self) -> None:
        """Drop the listing caches (page lists), keeping the stamp-keyed DBF /
        library / count caches; the topology too when its tables changed
        (:meth:`refresh_topology`). Cheap; call when a browser opens."""
        self.refresh_topology()
        with self._lock:
            self._pages.clear()
            self._lib_paths.clear()

    def touch(self, project: ProjectLike) -> None:
        """Forget cached listings of one project (after our own save / New)."""
        info = self._as_project(project)
        if info is None:
            return
        with self._lock:
            self._pages.pop(info.key, None)
            self._counts.pop(info.key, None)
            self._lib_paths.pop(info.dir, None)

    def reload(self) -> None:
        """Forget EVERYTHING incl. the topology (MASTER.DBF / include.dbf)."""
        with self._lock:
            self._topo.clear()
            self._topo_stamp = None
            self._folders.clear()
            self._pages.clear()
            self._dbf.clear()
            self._libs.clear()
            self._counts.clear()
            self._links.clear()
            self._lib_paths.clear()


_CATALOGS: Dict[str, ProjectCatalog] = {}
_CAT_LOCK = threading.Lock()


def catalog_for(user_dir: Optional[str] = None) -> ProjectCatalog:
    """The shared catalog of ``user_dir`` (default: the live install)."""
    ud = user_dir or live_user_dir() or ""
    key = _norm(ud) if ud else ""
    with _CAT_LOCK:
        cat = _CATALOGS.get(key)
        if cat is None:
            cat = ProjectCatalog(ud or None)
            _CATALOGS[key] = cat
        return cat


from ..infra import caches as _caches  # noqa: E402

_caches.register("catalog.catalogs", _CATALOGS, on=("refresh", "tests"))
