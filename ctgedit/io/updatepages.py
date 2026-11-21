"""Tools > Update Pages: bring a project's pages up to date with changed library genies and
templates."""

from __future__ import annotations

import contextlib
import glob
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


@dataclass
class UpdateReport:
    pages: int = 0
    stale: int = 0
    updated: List[str] = field(default_factory=list)
    instances: int = 0
    # placeholder genies placed again (their library opens again:)
    placeholders: int = 0
    templates: int = 0
    supergenies: int = 0  # super genie pages refreshed
    symbols: int = 0  # symbol placements re-derived
    composites: int = 0  # composite genies re-composed
    composites_skipped: int = 0  # no template in the project
    failed: List[str] = field(default_factory=list)
    backup: Optional[str] = None
    unregistered: List[str] = field(default_factory=list)  # .ctg without a pages.DBF row
    #: their files, in the same order (relabelled: two pages of one name in two projects)
    unregistered_paths: List[str] = field(default_factory=list)
    open_in_builder: List[str] = field(default_factory=list)  # skipped: open in CtDraw
    skipped_open: List[str] = field(default_factory=list)  # opened in an editor meanwhile
    # another save changed the page while it was being updated (run again)
    changed_meanwhile: List[str] = field(default_factory=list)
    # left: the project database is in use by another program (Plant SCADA Studio)
    db_in_use: List[str] = field(default_factory=list)
    # saved, but its project database rows are not written yet (io/pendingrows)
    rows_pending: List[str] = field(default_factory=list)
    # the rows of a page saved earlier without them, written now
    rows_written: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)  # 'page: the save's warning'
    # saved without the link-table entries of library elements (genies, symbols, templates)
    # no record uses any more; a language label entry dropped is no link (the record's
    # detail names it, ``labels_dropped`` counts it)
    links_dropped: List[str] = field(default_factory=list)
    # out of date, opened, but nothing on it could be updated (the record's detail says why)
    unchanged: List[str] = field(default_factory=list)
    not_checked: int = 0  # stopped before them
    records: List[dict] = field(default_factory=list)  # one per page (RECORD_KEYS)
    recovered: List[dict] = field(default_factory=list)  # journals rolled back meanwhile

    def add(self, rec: dict, label: Optional[Callable[[dict], str]] = None) -> None:
        """Count one page's record (:func:`page_record`) in: the counts and lists all come
        from the records. ``label(rec)`` = the page's name in the lists (default: its
        name)."""
        self.records.append(rec)
        fold_record(self, rec, label)


#: what a page's record says (update_pages, one per page it took). 'unchanged' = out of
#: date, but nothing on it could be updated (its detail says why: e.g. only a genie nested
#: in a genie it places changed, and the page already shows it)
OUTCOMES = (
    "current",
    "updated",
    "unchanged",
    "failed",
    "skipped_open",
    "open_in_builder",
    "changed_meanwhile",
    "db_in_use",
    "deferred",
    "not_checked",
)
RECORD_KEYS = (
    "i",
    "name",
    "project",
    "path",
    "outcome",
    "detail",
    "checked",
    "stale",
    "instances",
    "placeholders",
    "templates",
    "supergenies",
    "symbols",
    "composites",
    "composites_skipped",
    "links_dropped",
    "labels_dropped",
    "rows_pending",
    "rows_written",
    "warning",
)
#: an updated page's record also says the file's (mtime_ns, size) it read and wrote ('read',
#: 'wrote': the editor tells the chain's write of a page open in a tab from another program's)


def page_record(i: int, path: str, outcome: str, **kw) -> dict:
    """A page's record: its index in the run's list, its name and project, what became of
    it (OUTCOMES) and its counts (``links_dropped`` = the library elements' link-table
    entries its save left out: no record uses them any more; ``labels_dropped`` = the
    language label entries ('\\label') it left out, which are no links)."""
    p = os.path.abspath(path)
    rec = {
        "i": int(i),
        "name": os.path.splitext(os.path.basename(p))[0],
        "project": os.path.basename(os.path.dirname(p)),
        "path": p,
        "outcome": outcome,
        "detail": "",
        "checked": 0,
        "stale": 0,
        "instances": 0,
        "placeholders": 0,
        "templates": 0,
        "supergenies": 0,
        "symbols": 0,
        "composites": 0,
        "composites_skipped": 0,
        "links_dropped": 0,
        "labels_dropped": 0,
        "rows_pending": 0,
        "rows_written": 0,
        "warning": "",
    }
    rec.update(kw)
    return rec


def fold_record(rep: "UpdateReport", rec: dict, label: Optional[Callable[[dict], str]] = None) -> None:
    """Count the record ``rec`` into ``rep`` (not appended to rep.records)."""
    name = label(rec) if label is not None else rec.get("name", "")
    out = rec.get("outcome")
    rep.pages += int(rec.get("checked") or 0)
    rep.stale += int(rec.get("stale") or 0)
    rep.composites_skipped += int(rec.get("composites_skipped") or 0)
    if out == "updated":
        rep.updated.append(name)
        rep.instances += int(rec.get("instances") or 0)
        rep.placeholders += int(rec.get("placeholders") or 0)
        rep.symbols += int(rec.get("symbols") or 0)
        rep.composites += int(rec.get("composites") or 0)
        rep.templates += int(rec.get("templates") or 0)
        rep.supergenies += int(rec.get("supergenies") or 0)
        if rec.get("links_dropped"):
            rep.links_dropped.append(name)
    elif out == "unchanged":
        rep.unchanged.append(name)
    elif out == "failed":
        rep.failed.append("%s: %s" % (name, rec.get("detail") or "failed"))
    elif out == "skipped_open":
        rep.skipped_open.append(name)
    elif out == "open_in_builder":
        rep.open_in_builder.append(name)
    elif out == "changed_meanwhile":
        rep.changed_meanwhile.append(name)
    elif out == "db_in_use":
        rep.db_in_use.append(name)
    elif out in ("not_checked", "deferred"):
        rep.not_checked += 1
    if rec.get("rows_pending"):
        rep.rows_pending.append(name)
    if rec.get("rows_written"):
        rep.rows_written.append(name)
    if rec.get("warning"):
        rep.warnings.append("%s: %s" % (name, rec["warning"]))


def fold_records(records: Iterable[dict], label: Optional[Callable[[dict], str]] = None) -> "UpdateReport":
    """An UpdateReport of ``records`` (in their order)."""
    rep = UpdateReport()
    for r in records:
        rep.add(r, label)
    return rep


def same_name_label(paths) -> Callable[[dict], str]:
    """Label a page by name, adding the project when names clash."""
    projects: Dict[str, Set[str]] = {}
    for p in paths or ():
        if not p:
            continue
        a = os.path.abspath(str(p))
        name = os.path.splitext(os.path.basename(a))[0].lower()
        projects.setdefault(name, set()).add(os.path.basename(os.path.dirname(a)).lower())
    twice = {n for n, ps in projects.items() if len(ps) > 1}

    def label(rec: dict) -> str:
        name = str(rec.get("name") or "")
        if name.lower() in twice:
            return "%s (%s)" % (name, rec.get("project") or "")
        return name

    return label


def relabelled(rep: "UpdateReport", paths) -> "UpdateReport":
    """``rep`` (an update over the page files ``paths``) with its pages named as
    :func:`same_name_label` names them; the counts, the backup, the pages not registered
    (named the same way) and the journals rolled back carried over."""
    label = same_name_label(list(paths or ()) + list(rep.unregistered_paths))
    out = fold_records(rep.records, label)
    out.backup = rep.backup
    out.recovered = list(rep.recovered)
    out.unregistered_paths = list(rep.unregistered_paths)
    if len(rep.unregistered_paths) == len(rep.unregistered):
        out.unregistered = [label(page_record(0, p, "current")) for p in rep.unregistered_paths]
    else:
        out.unregistered = list(rep.unregistered)
    return out


@dataclass
class ProjectNode:
    """A project of an include tree (:func:`include_tree`): its folder, its name, its
    registered pages, the projects it includes (each project once, where the walk first
    meets it)."""

    dir: str
    name: str
    pages: int
    children: List["ProjectNode"] = field(default_factory=list)

    def walk(self):
        """This node and every node below it, in pre-order."""
        yield self
        for c in self.children:
            yield from c.walk()


def include_tree(root_dir: str, user_dir: Optional[str] = None) -> ProjectNode:
    """The root project and its include tree, depth first."""
    from ..format import projects as PR

    root_dir = os.path.abspath(root_dir)
    user_dir = os.path.abspath(user_dir or os.path.dirname(root_dir))
    masters: Dict[str, Dict[str, str]] = {}
    implicit = {n.lower() for n in PR.IMPLICIT_INCLUDE_PROJECTS}

    def key(d):
        return os.path.normcase(os.path.abspath(d))

    def node(d: str, name: str) -> ProjectNode:
        try:
            n = len(project_pages(d))
        except Exception:  # noqa: BLE001 -- swallow-ok: an unreadable project counts no pages
            n = 0
        return ProjectNode(d, name, n)

    root = node(root_dir, os.path.basename(os.path.normpath(root_dir)))
    seen = {key(root_dir)}

    def walk(n: ProjectNode, depth: int) -> None:
        if depth > 64:
            return
        for name in PR.included_project_names(n.dir):
            name = name.strip()
            if not name or name.lower() in implicit:
                continue
            dirs = PR.resolve_project_dirs(name, [user_dir], masters)
            if not dirs or key(dirs[0]) in seen:
                continue
            seen.add(key(dirs[0]))
            child = node(dirs[0], name)
            n.children.append(child)
            walk(child, depth + 1)

    walk(root, 0)
    return root


def run_page_list(dirs: Iterable[str], skip: Iterable[str] = ()) -> Tuple[List[str], List[str]]:
    """The pages an Update Pages run checks, in the order of ``dirs`` and each project's
    pages sorted by name (case-insensitive), each page once; and the names of the .ctg
    files with no pages.DBF row (left alone, reported once). ``skip`` = pages open in an
    editor (updated there). Reads pages.DBF only, never a page's records."""
    skip_n = {os.path.normcase(os.path.abspath(p)) for p in skip}
    pages: List[str] = []
    unregistered: List[str] = []
    seen: Set[str] = set()
    for d in dirs:
        reg = registered_names(d)
        for p in project_pages(d, registered_only=False):
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                continue
            seen.add(k)
            if k in skip_n:
                continue
            name = os.path.splitext(os.path.basename(p))[0]
            if reg is not None and name.lower()[:64] not in reg:
                unregistered.append(name)
                continue
            pages.append(p)
    return pages, unregistered


def project_pages(project_dir: str, registered_only: bool = True) -> List[str]:
    """The project's page files, sorted by name."""
    paths = sorted(glob.glob(os.path.join(project_dir, "*.ctg")), key=lambda p: os.path.basename(p).lower())
    if not registered_only:
        return paths
    from .project import Project

    proj = Project(project_dir)
    if proj._pages_path() is None:
        return paths
    names = {n.lower()[: Project.NAME_LEN] for n in proj.page_names()}
    return [p for p in paths if os.path.splitext(os.path.basename(p))[0].lower()[: Project.NAME_LEN] in names]


def registered_names(project_dir: str) -> Optional[Set[str]]:
    """Lower-case names of the pages registered in the project's pages.DBF
    (NAME is C(64): longer file names are compared truncated), or None when
    the folder has no pages.DBF (not a project: every page counts)."""
    from ..format.projects import read_dbf_rows

    path = None
    for n in ("pages.DBF", "pages.dbf"):
        p = os.path.join(project_dir, n)
        if os.path.exists(p):
            path = p
            break
    if path is None:
        return None
    return {
        (r.get("NAME") or "").strip().lower() for r in read_dbf_rows(path) if (r.get("NAME") or "").strip()
    }


class _Current:
    """Current link table entries of library elements, cached."""

    def __init__(self, resolver):
        self.resolver = resolver
        self.cache: Dict[str, Optional[str]] = {}
        self._fast: Dict[str, tuple] = {}
        self.nested: Dict[str, frozenset] = {}  # nested_changes, per element entry
        #: library folder -> the _Current of its own search path (:meth:`for_library`)
        self.subs: Dict[str, "_Current"] = {}

    def clear(self) -> None:
        """Read every element's entry afresh (its library changed)."""
        self.cache.clear()
        self._fast.clear()
        self.nested.clear()
        self.subs.clear()

    def for_library(self, lib_path: str) -> "_Current":
        """The entries as resolved from a library file's own search path."""
        from ..format.symbols import SymbolResolver

        key = os.path.normcase(os.path.dirname(os.path.abspath(lib_path)))
        sub = self.subs.get(key)
        if sub is None:
            sub = _Current(SymbolResolver.for_page(lib_path))
            sub.subs = self.subs  # (one folder map for the whole walk)
            self.subs[key] = sub
        return sub

    def name(self, entry_name: str) -> Optional[str]:
        from ..format import links

        base = links.strip_stamp(entry_name)
        key = base.lower()
        if key in self.cache:
            return self.cache[key]
        fast = self._fast.get(key)
        if fast is None:
            fast = self._fast[key] = self._resolved_entry(base)
        cur, rs, lib, elem = fast
        if cur is None or cur.lower() == entry_name.lower():
            # unresolvable, or the entry the page already quotes: not stale
            # either way (the load's None would mean the same)
            return cur
        out = None
        try:
            from ..format import genie_place as GP

            out = GP.current_link_entry(rs.library_path, lib, elem)[1]
        except Exception:  # noqa: BLE001
            out = None
        self.cache[key] = out
        return out

    def stamp_name(self, entry_name: str) -> Optional[str]:
        """The element's current entry name, from its block's identity alone (never
        loading the element): what a version comparison needs. None = it does not
        resolve."""
        from ..format import links

        base = links.strip_stamp(entry_name)
        key = base.lower()
        fast = self._fast.get(key)
        if fast is None:
            fast = self._fast[key] = self._resolved_entry(base)
        return fast[0]

    def guid(self, entry_name: str) -> Optional[bytes]:
        """The element's current entry GUID (its block identity's, never loading the
        element); None = it does not resolve."""
        from ..format import genie_place as GP
        from ..format import links

        key = links.strip_stamp(entry_name).lower()
        if self.stamp_name(entry_name) is None:
            return None
        _cur, rs, lib, elem = self._fast[key]
        try:
            return GP.current_link_entry(rs.library_path, lib, elem)[0]
        except Exception:  # noqa: BLE001  # swallow-ok: its name says it does not resolve
            return None

    def _resolved_entry(self, base: str) -> tuple:
        """(entry name, ResolvedSymbol, library, element) -- the entry name
        of the element block load_element would read (the library as it is on
        disk now: open_library), None when it does not resolve or has no
        such element."""
        from ..format import genie_place as GP
        from ..format.symbols import SymbolReference, open_library

        stem, _, ext = base.rpartition(".")
        lib, _, elem = stem.rpartition(".")  # the LAST dot
        if not lib:
            lib, elem = elem, lib
        ext = ext.lstrip("<").lower()
        if not (lib and elem and ext in ("ctm", "ctl", "ctt")):
            return (None, None, lib, elem)
        try:
            # strict: an entry names its kind of library (CtDraw's with a
            # kind); a .ctm entry whose element only a same-named .ctl or .ctt holds does
            # not resolve (named after that file, it read as out of date for ever)
            rs = self.resolver.resolve(
                SymbolReference(lib, elem, ext, "genie" if ext == "ctm" else "symbol"), strict=True
            )
            if rs is None:
                return (None, None, lib, elem)
            L = open_library(rs.library_path)
            pb = L.element(elem)
            if pb is None or L._find_block(elem) is None:
                return (None, rs, lib, elem)  # load_element raises KeyError
            _stem, lext = os.path.splitext(os.path.basename(rs.library_path))
            el = GP.Element(
                rs.library_path,
                lib,
                elem,
                lext.lstrip(".").lower(),
                (0, 0),
                [],
                (0, 0, 0, 0),
                bytes(pb.page_identity),
            )
            return (GP.link_table_entry(el)[1], rs, lib, elem)
        except Exception:  # noqa: BLE001
            return (None, None, lib, elem)


#: Update Pages checks one page's elements, template and super genie in turn:
#: while _SHARE_PARSE is on, those checks share one parse of the page file
#: (the file bytes are compared, so a changed file is parsed again)
_SHARE_PARSE = [False]
_PARSE_SLOT: list = [None]


def _parse_page(ctg_path: str):
    from ..format.page import parse_ctg, parse_ctg_bytes

    if not _SHARE_PARSE[0]:
        return parse_ctg(ctg_path)
    with open(ctg_path, "rb") as fh:
        data = fh.read()
    got = _PARSE_SLOT[0]
    if got is not None and got[0] == ctg_path and got[1] == data:
        return got[2]
    pc = parse_ctg_bytes(data)
    _PARSE_SLOT[0] = (ctg_path, data, pc)
    return pc


def stale_elements(ctg_path: str, current: "_Current") -> Set[Tuple[str, str]]:
    """(library, element) of the page's genie (.ctm) elements whose identity
    stamp changed since the page was saved, that a record of the page still uses
    (:func:`stale_genie_links`: an entry no record uses is not a stale genie). The
    super genie a super genie PAGE was created from is not one (stale_supergenie)."""
    return stale_genie_links(ctg_path, current)[0]


def stale_genie_links(ctg_path: str, current: "_Current") -> Tuple[Set[Tuple[str, str]], List[str]]:
    """The page's out-of-date genie entries, split into used and unused."""
    from ..format import genie_place as GP
    from ..format import links
    from ..format import supergenie as SG

    pb = _parse_page(ctg_path).page
    if pb is None or not pb.link_count:
        return set(), []
    own = {
        links.strip_stamp(GP.decode(pb.objects[a]).link or ref.raw).lower()
        for a, _b, ref in SG.page_roots(pb.objects)
    }
    rows = _genie_entry_changes(pb.link_table_raw, current, own)
    live, dead = _split_dead(pb.objects, [(g, n) for g, n, _k, _v in rows])
    # (a dead entry with only nested changes quotes its genie's current stamp: not stale)
    own_changed = {n for _g, n, _k, v in rows if v is None}
    return ({k for g, n, k, _v in rows if (g, n) in live}, [n for n in dead if n in own_changed])


def _split_dead(records, entries) -> Tuple[Set[tuple], List[str]]:
    """``entries`` [(guid, name)] split into the set of those a record uses and the names
    of those none does (genie_place.used_link_entries; nothing is looked at when there are
    no entries)."""
    if not entries:
        return set(), []
    from ..format import genie_place as GP

    used = set(GP.used_link_entries(records, list(entries)))
    return used, [n for g, n in entries if (g, n) not in used]


def stale_genie_entries(
    link_table_raw: bytes, current: "_Current", own=(), nested: bool = True
) -> Set[Tuple[str, str]]:
    """(library, element) of the page's out-of-date genie entries."""
    return set(genie_changes(link_table_raw, current, own, nested))


def genie_changes(
    link_table_raw: bytes, current: "_Current", own=(), nested: bool = True
) -> Dict[Tuple[str, str], Optional[frozenset]]:
    """What changed in the page's genies since it was saved."""
    out: Dict[Tuple[str, str], Optional[frozenset]] = {}
    for _g, _name, key, ch in _genie_entry_changes(link_table_raw, current, own, nested):
        if ch is None:
            out[key] = None
        elif out.get(key, frozenset()) is not None:
            out[key] = frozenset(out.get(key) or ()) | ch
    return out


def _genie_entry_changes(
    link_table_raw: bytes, current: "_Current", own=(), nested: bool = True
) -> List[tuple]:
    """:func:`genie_changes` entry by entry: [(guid, entry name, (library, element), None
    (the element changed) or the nested genies that changed)], in table order."""
    from ..format import links

    t = links.parse_link_table(link_table_raw) if link_table_raw else None
    out: List[tuple] = []
    for guid, name in t[1] if t else []:
        if name.startswith("\\"):  # a '\\<native>' language label
            continue
        if links.strip_stamp(name).lower() in own:
            continue
        base = links.strip_stamp(name)
        stem, _, ext = base.rpartition(".")
        lib, _, elem = stem.rpartition(".")  # the LAST dot
        if not lib:
            lib, elem = elem, lib
        if ext.lstrip("<").lower() != "ctm":
            continue
        cur = current.stamp_name(name)
        if cur is None:
            continue
        key = (lib.lower(), elem.lower())
        if cur.lower() == name.lower() and guid and any(guid):
            # the same stamp with another GUID is another save of the element: the stamp is
            # whole seconds, ctgedit's quick saves step it past the clock (element.
            # stamp_after) and a later Graphics Builder save can write a stamp an earlier
            # save had. Every save writes a new GUID; a zero GUID is
            # legal and tells nothing
            now = current.guid(name)
            if now is not None and any(now) and bytes(now) != bytes(guid):
                cur = ""
        if cur.lower() != name.lower():
            out.append((guid, name, key, None))
        elif nested:
            ch = nested_changes(base, current)
            if ch:
                out.append((guid, name, key, ch))
    return out


def changed_genies(link_table_raw: bytes, resolver) -> Dict[Tuple[str, str], Optional[frozenset]]:
    """:func:`genie_changes` of a link table against the libraries ``resolver`` finds now."""
    return genie_changes(link_table_raw, _Current(resolver))


def genie_sources(entry_base: str, current: "_Current", tags: bool = True) -> frozenset:
    """The library files a placed genie is composed from."""
    from ..format import links
    from ..format.fileio import content_tag
    from . import genieusage as GU

    out = set()
    todo, seen = [(entry_base, current)], set()
    while todo:
        base, cur = todo.pop()
        key = (id(cur), base.lower())
        if key in seen or len(seen) > 20000:
            continue
        seen.add(key)
        _cur, rs, _lib, elem = cur._resolved_entry(base)
        if rs is None:
            continue
        try:
            st = os.stat(rs.library_path)
            out.add(
                (
                    os.path.normcase(rs.library_path),
                    st.st_mtime_ns,
                    st.st_size,
                    content_tag(rs.library_path) if tags else "",
                )
            )
        except OSError:
            continue
        if not key[1].endswith("ctm"):
            continue
        table = GU.library_tables(rs.library_path).get(elem.lower())
        sub = None
        for nm in table.names if table is not None else ():
            if nm.startswith("\\") or "@super" in nm.lower():
                continue
            b = links.strip_stamp(nm)
            if b.lower().rpartition(".")[2].lstrip("<") in ("ctm", "ctl"):
                sub = sub or cur.for_library(rs.library_path)
                todo.append((b, sub))
    return frozenset(out)


def library_files(link_table_raw: bytes, resolver, tags: bool = True) -> frozenset:
    """The library files (path, mtime_ns, size, content tag) a page with this link table is
    composed from: :func:`genie_sources` of every entry (its genies, symbols and template,
    and what they nest, at any depth); the content tags '' without ``tags``."""
    from ..format import links

    t = links.parse_link_table(link_table_raw) if link_table_raw else None
    cur = _Current(resolver)
    out = set()
    for _g, name in t[1] if t else []:
        base = links.strip_stamp(name)
        if base.startswith("\\") or "@super" in base.lower():
            continue
        out |= genie_sources(base, cur, tags=tags)
    return frozenset(out)


def nested_changes(entry_base: str, current: "_Current", _seen=None, _depth: int = 0) -> frozenset:
    """(library, element) of nested genies and symbols whose stamp changed."""
    from ..format import links
    from ..format.template_place import shipped_project
    from . import genieusage as GU

    key = entry_base.lower()
    memo = current.nested
    if key in memo:
        return memo[key]
    from ..format.symbols import split_lib_element

    seen = set() if _seen is None else _seen
    if (id(current), key) in seen or _depth > 64:
        return frozenset()
    seen.add((id(current), key))
    _cur, rs, _lib, elem = current._resolved_entry(entry_base)
    out = set()
    if rs is not None and shipped_project(rs.library_path) is None:
        table = GU.library_tables(rs.library_path).get(elem.lower())
        sub = None
        names = table.names if table is not None else ()
        guids = getattr(table, "guids", ()) or ()
        for n, nm in enumerate(names):
            if nm.startswith("\\") or "@super" in nm.lower():
                continue
            base = links.strip_stamp(nm)
            stem, _, ext = base.lower().rpartition(".")
            ext = ext.lstrip("<")
            if ext not in ("ctm", "ctl"):
                continue
            lib, el_name = split_lib_element(stem)  # the LAST dot
            sub = sub or current.for_library(rs.library_path)
            cur = sub.stamp_name(nm)
            if cur is not None and cur.lower() == nm.lower() and n < len(guids) and any(guids[n]):
                # (the same stamp from another save: the GUID tells, as on a page,
                # _genie_entry_changes)
                now = sub.guid(nm)
                if now is not None and any(now) and bytes(now) != guids[n]:
                    cur = ""
            if cur is not None and cur.lower() != nm.lower():
                out.add((lib, el_name))
            elif ext == "ctm":
                out |= nested_changes(base, sub, seen, _depth + 1)
    res = frozenset(out)
    memo[key] = res
    return res


def stale_symbol_entries(link_table_raw: bytes, current: "_Current") -> Set[Tuple[str, str]]:
    """(library, element) of the page's out-of-date symbol entries."""
    from ..format import links

    t = links.parse_link_table(link_table_raw) if link_table_raw else None
    return _stale_ctl([name for _guid, name in (t[1] if t else [])], current)


def stale_template_entries(link_table_raw: bytes, current: "_Current") -> Set[Tuple[str, str]]:
    """(library, element) of out-of-date template entries in an element's import table."""
    from ..format import links

    t = links.parse_link_table(link_table_raw) if link_table_raw else None
    out: Set[Tuple[str, str]] = set()
    for _guid, name in t[1] if t else []:
        if name.startswith("\\"):
            continue
        stem, _, ext = links.strip_stamp(name).rpartition(".")
        if ext.lstrip("<").lower() != "ctt":
            continue
        lib, _, elem = stem.rpartition(".")  # the LAST dot
        if not lib:
            lib, elem = elem, lib
        cur = current.stamp_name(name)
        if cur is not None and cur.lower() != name.lower():
            out.add((lib.lower(), elem.lower()))
    return out


def changed_templates(link_table_raw: bytes, resolver) -> Set[Tuple[str, str]]:
    """:func:`stale_template_entries` of a link table against the libraries ``resolver``
    finds now."""
    return stale_template_entries(link_table_raw, _Current(resolver))


def _stale_ctl(names, current: "_Current") -> Set[Tuple[str, str]]:
    return {key for _name, key in _stale_ctl_names(names, current)}


def _stale_ctl_names(names, current: "_Current") -> List[Tuple[str, Tuple[str, str]]]:
    """[(entry name, (library, element))] of the out-of-date symbol (.ctl) entries of
    ``names``."""
    from ..format import links

    out = []
    for name in names:
        if name.startswith("\\"):
            continue
        base = links.strip_stamp(name)
        stem, _, ext = base.rpartition(".")
        lib, _, elem = stem.rpartition(".")  # the LAST dot
        if not lib:
            lib, elem = elem, lib
        if ext.lstrip("<").lower() != "ctl":
            continue
        cur = current.name(name)
        if cur is None or cur.lower() == name.lower():
            continue
        out.append((name, (lib.lower(), elem.lower())))
    return out


def stale_symbols(ctg_path: str, current: "_Current") -> Set[Tuple[str, str]]:
    """:func:`stale_symbol_entries` of a page file that a record of the page still uses
    (:func:`stale_symbol_links`)."""
    return stale_symbol_links(ctg_path, current)[0]


def stale_symbol_links(ctg_path: str, current: "_Current") -> Tuple[Set[Tuple[str, str]], List[str]]:
    """The page's out-of-date symbol entries, split into used and unused."""
    from ..format.pagesummary import read_link_names

    rows = _stale_ctl_names(read_link_names(ctg_path), current)
    if not rows:
        return set(), []
    pb = _parse_page(ctg_path).page
    if pb is None:
        return {k for _n, k in rows}, []
    live, dead = _split_dead(pb.objects, [(b"", n) for n, _k in rows])
    return {k for n, k in rows if (b"", n) in live}, dead


def page_needs_update(ctg_path: str) -> bool:
    """Does the page still quote an out-of-date genie, symbol, template or super genie?"""
    from ..format.symbols import SymbolResolver

    try:
        cur = _Current(SymbolResolver.for_page(ctg_path))
        shared = _SHARE_PARSE[0]
        _SHARE_PARSE[0] = True
        try:
            stale, dead = stale_genie_links(ctg_path, cur)
            sym, sym_dead = stale_symbol_links(ctg_path, cur)
            return bool(
                stale
                or dead
                or sym
                or sym_dead
                or stale_template(ctg_path, cur)
                or stale_supergenie(ctg_path, cur)
            )
        finally:
            _SHARE_PARSE[0] = shared
            if not shared:
                _PARSE_SLOT[0] = None
    except Exception:  # noqa: BLE001 -- named rather than hidden
        from ..infra import logsetup

        logsetup.note_exception("updatepages.page_needs_update", page=ctg_path)
        return True


def stale_template(ctg_path: str, current: "_Current") -> bool:
    """Has the page's template changed since the page was saved?"""
    pair = _template_entry_pair(ctg_path)
    if pair is None:
        return False
    guid, entry = pair
    cur = current.name(entry)
    if cur is not None and cur.lower() != entry.lower():
        return True
    if cur is not None and current.guid(entry) not in (None, guid):
        return True
    return bool(
        template_copies(ctg_path, current)
        or template_placeholders(ctg_path, current)
        or copy_placeholders(ctg_path, current)
    ) and not _shows_composition(ctg_path, current)


def _shows_composition(ctg_path: str, current: "_Current") -> bool:
    """The page's template placement holds the records its template, as the template's
    load holds it, gives it (template_place.shows_composition): an update changes none."""
    from ..format import genie_place as GP
    from ..format import template_place as TP

    pb = _parse_page(ctg_path).page
    roots = TP.template_roots(pb.objects) if pb is not None else []
    if not roots:
        return True
    try:
        tpl = TP.resolve_template(current.resolver, roots[0][2])
        if tpl is None:
            return True  # (it does not resolve: nothing to compose)
        link = GP.decode(pb.objects[roots[0][0]]).link or roots[0][2].raw
        return TP.shows_composition(pb.objects, tpl, link, os.path.splitext(os.path.basename(ctg_path))[0])
    except Exception:  # noqa: BLE001 -- named out of date rather than hidden
        from ..infra import logsetup

        logsetup.note_exception("updatepages._shows_composition", page=ctg_path)
        return False


def _template_entry(ctg_path: str) -> Optional[str]:
    """The page's link-table entry of the template its ROOT placement links, None when it
    has none."""
    pair = _template_entry_pair(ctg_path)
    return pair[1] if pair is not None else None


def _template_entry_pair(ctg_path: str) -> Optional[Tuple[bytes, str]]:
    """(GUID, name) of :func:`_template_entry`."""
    from ..format import genie_place as GP
    from ..format import links
    from ..format import template_place as TP

    pb = _parse_page(ctg_path).page
    if pb is None or not pb.link_count:
        return None
    roots = TP.template_roots(pb.objects)
    if not roots:
        return None
    want = links.strip_stamp(GP.decode(pb.objects[roots[0][0]]).link or roots[0][2].raw).lower()
    t = links.parse_link_table(pb.link_table_raw)
    for guid, name in t[1] if t else []:
        if links.strip_stamp(name).lower() == want:
            return guid, name
    return None


def template_copies(ctg_path: str, current: "_Current") -> Set[Tuple[str, str]]:
    """Templates whose copies in the page's template are out of date."""
    from .genieusage import library_tables

    entry = _template_entry(ctg_path)
    if entry is None:
        return set()
    current.stamp_name(entry)
    from ..format import links

    hit = current._fast.get(links.strip_stamp(entry).lower())
    if not hit or hit[0] is None or hit[1] is None:
        return set()
    lib_path, elem = hit[1].library_path, hit[3]
    t = library_tables(lib_path).get(elem.lower())
    if t is None or not t.names:
        return set()
    sub = current.for_library(lib_path)
    out: Set[Tuple[str, str]] = set()
    for name in t.names:
        if name.startswith("\\"):
            continue
        stem, _, ext = links.strip_stamp(name).rpartition(".")
        if ext.lstrip("<").lower() != "ctt":
            continue
        lib, _, el = stem.rpartition(".")  # the LAST dot
        cur = sub.stamp_name(name)
        if cur is not None and cur.lower() != name.lower():
            out.add((lib.lower(), el.lower()))
    return out


def _template_element(ctg_path: str, current: "_Current") -> Optional[Tuple[str, str]]:
    """(library file, element name) of the template the page's template entry names, as
    ``current`` resolves it; None when the page has no template entry or it does not
    resolve."""
    from ..format import links

    entry = _template_entry(ctg_path)
    if entry is None:
        return None
    current.stamp_name(entry)
    hit = current._fast.get(links.strip_stamp(entry).lower())
    if not hit or hit[0] is None or hit[1] is None:
        return None
    return hit[1].library_path, hit[3]


def copy_placeholders(ctg_path: str, current: "_Current") -> Set[Tuple[str, str]]:
    """Placeholder genies in the page's template copy whose library opens now."""
    from ..format import genie_place as GP
    from ..format import template_place as TP

    pb = _parse_page(ctg_path).page
    roots = TP.template_roots(pb.objects) if pb is not None else []
    if not roots:
        return set()
    a, b, _ref = roots[0]
    refs = [got[0] for got in (GP.placeholder_genie(po) for po in pb.objects[a + 1 : b]) if got is not None]
    if not refs:
        return set()
    got = _template_element(ctg_path, current)
    if got is None:
        return set()
    sub = current.for_library(got[0])
    return {
        (r.library.lower(), r.element.lower())
        for r in refs
        if sub.stamp_name("%s.%s.<ctm" % (r.library, r.element)) is not None
    }


def template_placeholders(ctg_path: str, current: "_Current") -> Set[Tuple[str, str]]:
    """Placeholder genies in the page's template whose library opens now."""
    from .genieusage import library_tables
    from ..format import links

    got = _template_element(ctg_path, current)
    if got is None:
        return set()
    lib_path, elem = got
    t = library_tables(lib_path).get(elem.lower())
    if t is None or not t.names:
        return set()
    sub = current.for_library(lib_path)
    out: Set[Tuple[str, str]] = set()
    for name in t.names:
        if name.startswith("\\") or links.entry_stamp(name) is not None:
            continue
        stem, _, ext = name.rpartition(".")
        if ext.lstrip("<").lower() != "ctm":
            continue
        lib, _, el = stem.rpartition(".")  # the LAST dot
        if sub.stamp_name(name) is not None:
            out.add((lib.lower(), el.lower()))
    return out


def stale_supergenie(ctg_path: str, current: "_Current") -> bool:
    """The page was created from a super genie whose element changed since
    the page was saved: the link entry of the element its locked root links
    carries a stamp that differs from the element's current one."""
    from ..format import genie_place as GP
    from ..format import links
    from ..format import supergenie as SG

    pb = _parse_page(ctg_path).page
    if pb is None or not pb.link_count:
        return False
    roots = SG.page_roots(pb.objects)
    if not roots:
        return False
    want = links.strip_stamp(GP.decode(pb.objects[roots[0][0]]).link or roots[0][2].raw).lower()
    t = links.parse_link_table(pb.link_table_raw)
    for _guid, name in t[1] if t else []:
        if links.strip_stamp(name).lower() != want:
            continue
        cur = current.name(name)
        return cur is not None and cur.lower() != name.lower()
    return False


def has_composites(ctg_path: str) -> bool:
    """The page links a composition cache (a placed composite genie)."""
    try:
        with open(ctg_path, "rb") as fh:
            return b"_compositioncaches" in fh.read().lower()
    except OSError:
        return False


def composites_current(ctg_path: str, ctx, memo: dict) -> bool:
    """Are all the page's composite instances current?"""
    from ..format import composer as CP
    from ..format import composer_store as CS
    from ..format import composites as C

    try:
        with open(ctg_path, "rb") as fh:
            found = CS.links_in(fh.read())
    except OSError:
        return False
    pdir = os.path.normcase(os.path.dirname(os.path.abspath(ctg_path)))
    tkey = ("templates", pdir)
    if tkey not in memo:
        memo[tkey] = C.load_templates(ctg_path)
    templates = memo[tkey]
    for guid, h in sorted(found):
        key = (pdir, guid, h)
        if key not in memo:
            state = "unknown"
            try:
                info = C.composite_info(ctg_path, "%s.%s.<ctm" % (CP.cache_library(guid), h), templates)
                if info is None or info.template is None or info.template_source != "project":
                    state = "unknown"
                elif info.instance is None or not info.cache_dir:
                    state = "missing"
                else:
                    state = CS.state_of(
                        ctg_path,
                        info.template.path,
                        info.instance.values(),
                        info.cache_dir,
                        info.hash,
                        ctx=ctx,
                    )
            except Exception:  # noqa: BLE001 -- open the page and let it decide
                state = "unknown"
            memo[key] = state
        if memo[key] != "current":
            return False
    return True


def backup_dir(stamp: Optional[str] = None) -> str:
    """Where a batch keeps the page files it rewrites (one folder per run,
    under ``<data folder>\\backups``; see infra/backups)."""
    from ..infra import backups

    if stamp:
        return os.path.join(backups.backups_root(), stamp)
    return backups.new_folder("update-pages")


def _backup_page(path: str, dest_root: str, kind: str = "update-pages") -> None:
    """Back up a page's files and its database rows."""
    import json
    import shutil
    from ..format import journal, projectlock
    from ..infra import backups

    pdir = os.path.dirname(os.path.abspath(path))
    proj = os.path.basename(pdir)
    d = os.path.join(dest_root, proj)
    stem = os.path.splitext(path)[0]
    name = os.path.basename(stem)
    out = os.path.join(d, name + ".pgrows.json")
    if os.path.exists(out) or os.path.exists(os.path.join(d, name + ".ctg")):
        return  # already backed up in this run
    try:
        from . import pgregister

        rows = pgregister.export_page_rows(pdir, name)
    except projectlock.LockCancelled, projectlock.ProjectBusyError, journal.JournalRecoveryError:
        raise
    except Exception as e:  # noqa: BLE001 -- the page files are the backup that matters
        rows = {"_error": "the project database rows could not be read: %s" % e}
    os.makedirs(d, exist_ok=True)
    files = []
    seen = set()
    for ext in (".ctg", ".ctF", ".ctf"):
        src = stem + ext
        key = os.path.normcase(os.path.abspath(src))
        if os.path.exists(src) and key not in seen:
            seen.add(key)
            shutil.copy2(src, d)
            files.append(os.path.basename(src))
    # written even when the page has no rows yet: a restore then takes out
    # the rows the update's save added
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"project": pdir, "page": name, "tables": rows}, fh, indent=1)
    entry = {"project": pdir, "page": name, "folder": proj, "files": files, "rows": os.path.basename(out)}
    try:
        from .project import Project

        rec = Project(pdir).page_record(name)
        if rec:
            # the pages.DBF row (a restore of a DELETED page registers it again)
            entry["record"] = {k: v for k, v in rec.items() if not str(k).startswith("_")}
    except Exception:  # noqa: BLE001 -- the page files are the backup that matters
        pass
    backups.add_entry(dest_root, kind, entry)


def backup_page_for_delete(path: str) -> Optional[str]:
    """Back up a page about to be deleted. Returns the folder, or None."""
    import shutil
    from ..format import journal, projectlock
    from ..infra import backups

    folder = backups.new_folder("delete-page")
    try:
        _backup_page(path, folder, kind="delete-page")
    except projectlock.ProjectBusyError, projectlock.LockCancelled, journal.JournalRecoveryError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except OSError:
        return None
    if not backups.read_manifest(folder).get("entries"):
        return None
    with contextlib.suppress(Exception):
        backups.prune()
    return folder


@dataclass
class RestoreReport:
    restored: List[str] = field(default_factory=list)  # page names
    failed: List[str] = field(default_factory=list)  # "page: why"
    rows: Dict[str, dict] = field(default_factory=dict)  # page -> table report
    libraries: List[str] = field(default_factory=list)  # library files put back


def restore_backup(
    folder: str,
    pages: Optional[Iterable[str]] = None,
    builder_open: Iterable[Tuple[str, str]] = (),
    libraries: bool = True,
) -> RestoreReport:
    """Tools > Restore Backup: put pages back as they were before an Update Pages run."""
    from ..format.fileio import write_atomic
    from ..infra import backups

    rep = RestoreReport()
    want = {p.lower() for p in pages} if pages is not None else None
    in_builder = {(str(p).strip().lower(), str(n).strip().lower()) for p, n in builder_open}
    for e in backups.read_manifest(folder).get("entries") or []:
        if e.get("library"):
            # a library file an update of an element's users saved (genieusage.run_update)
            lib = e["library"]
            if libraries:
                try:
                    src = os.path.join(folder, e.get("folder") or "", e.get("file") or os.path.basename(lib))
                    with open(src, "rb") as fh:
                        write_atomic(lib, fh.read())
                    rep.libraries.append(lib)
                    rep.restored.append(os.path.basename(lib))
                except Exception as ex:  # noqa: BLE001
                    rep.failed.append("%s: %s" % (os.path.basename(lib), getattr(ex, "strerror", None) or ex))
            continue
        name, pdir = e.get("page", ""), e.get("project", "")
        if not name or (want is not None and name.lower() not in want):
            continue
        if (os.path.basename(pdir).lower(), name.lower()) in in_builder:
            rep.failed.append("%s: open in the Graphics Builder. Close it there and restore again." % name)
            continue
        src = os.path.join(folder, e.get("folder") or os.path.basename(pdir))
        try:
            names = [f for f in e.get("files") or () if f.lower().endswith((".ctg", ".ctf"))]
            if not any(f.lower().endswith(".ctg") for f in names):
                raise FileNotFoundError("the backup holds no page file")
            # the page's lock, then the project's (the order every writer keeps): no save
            # of the page and no register of the project runs in between
            from ..format import projectlock

            with projectlock.page(os.path.join(pdir, name + ".ctg")), projectlock.project(pdir):
                _restore_page(src, pdir, name, names, e, rep)
            rep.restored.append(name)
        except Exception as ex:  # noqa: BLE001 -- one page never stops the rest
            rep.failed.append("%s: %s" % (name, getattr(ex, "strerror", None) or ex))
    return rep


def _restore_page(src: str, pdir: str, name: str, names: List[str], e: dict, rep: RestoreReport) -> None:
    """restore_backup of one page (its locks held): the page files, its pages.DBF row
    (a deleted page) and its project database rows."""
    import json
    from ..format.fileio import write_atomic
    from . import pgregister

    for f in names:
        with open(os.path.join(src, f), "rb") as fh:
            write_atomic(os.path.join(pdir, f), fh.read())
    if not any(f.lower().endswith(".ctf") for f in names):
        for ext in (".ctF", ".ctf"):
            p = os.path.join(pdir, name + ext)
            if os.path.exists(p):
                os.remove(p)
    rec = e.get("record")
    if rec:
        # a deleted page: register it again (its pages.DBF row)
        from .project import Project

        proj = Project(pdir)
        if proj.page_record(name) is None:
            proj.add_page_record(name, {k: v for k, v in rec.items() if str(k).upper() != "NAME" and v})
    from . import pendingrows

    page = os.path.join(pdir, name + ".ctg")
    if e.get("rows"):
        with open(os.path.join(src, e["rows"]), encoding="utf-8") as fh:
            saved = json.load(fh)
        tables = saved.get("tables") or {}
        if "_error" in tables:
            # the backup could not read the rows: they are written from the restored page
            # file instead, or left pending (the next save or Update Pages run writes them)
            pendingrows.mark(page)
            try:
                rep.rows[name] = pgregister.register_page(pdir, name, page)
            except Exception as ex:  # noqa: BLE001 -- the marker stays and says so
                from ..infra import logsetup

                logsetup.note_exception("updatepages.restore/rows", page=page)
                rep.rows[name] = {"_error": str(ex)}
                return
        else:
            rep.rows[name] = pgregister.restore_page_rows(pdir, saved.get("page", name), tables)
    # the page and its rows are the backup's pair again: no rows are pending for it
    pendingrows.clear(page)


def update_pages(
    project_dir=None,
    skip: Iterable[str] = (),
    progress: Optional[Callable[[int, int, str], bool]] = None,
    templates: bool = True,
    genies: bool = True,
    backup: Optional[str] = None,
    builder_open: Iterable[Tuple[str, str]] = (),
    page_properties: Optional[bool] = None,
    supergenies: Optional[bool] = None,
    symbols: Optional[bool] = None,
    composites: Optional[bool] = None,
    only: Optional[Iterable[str]] = None,
    skip_now: Optional[Callable[[str], bool]] = None,
    *,
    pages: Optional[List[str]] = None,
    claims: Optional[str] = None,
    records: Optional[str] = None,
    defer_composites: bool = False,
    part: int = 0,
    on_record: Optional[Callable[[dict], None]] = None,
    force: bool = False,
) -> UpdateReport:
    """Update Pages: bring every registered page of the projects up to date."""
    from ..format.symbols import SymbolResolver

    rep = UpdateReport()
    rep.backup = backup
    if supergenies is None:
        supergenies = genies
    if symbols is None:
        symbols = genies
    if composites is None:
        composites = genies
    only_n = None if only is None else {os.path.normcase(os.path.abspath(p)) for p in only}
    if pages is None:
        dirs = [project_dir] if isinstance(project_dir, str) else list(project_dir or ())
        skip_n = {os.path.normcase(os.path.abspath(p)) for p in skip}
        pages = []
        for d in dirs:
            reg = registered_names(d)
            # every .ctg: the unregistered ones are REPORTED, not silently dropped
            for p in project_pages(d, registered_only=False):
                if os.path.normcase(os.path.abspath(p)) in skip_n:
                    continue
                if only_n is not None and os.path.normcase(os.path.abspath(p)) not in only_n:
                    continue
                name = os.path.splitext(os.path.basename(p))[0]
                if reg is not None and name.lower()[:64] not in reg:
                    rep.unregistered.append(name)
                    rep.unregistered_paths.append(os.path.abspath(p))
                    continue
                pages.append(p)
    else:
        pages = list(pages)
    want = [
        i for i, p in enumerate(pages) if only_n is None or os.path.normcase(os.path.abspath(p)) in only_n
    ]
    run = _Run(
        templates=templates,
        genies=genies,
        supergenies=supergenies,
        symbols=symbols,
        composites=composites,
        page_properties=page_properties,
        backup=backup,
        builder_open=builder_open,
        skip_now=skip_now,
        defer=defer_composites,
        force=force,
        resolver_for=lambda path: SymbolResolver.for_page(
            path, [d for d in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if d]
        ),
    )
    out = open(records, "a", encoding="utf-8") if records else None
    try:
        taken = 0
        for i in want:
            path = pages[i]
            if claims is not None and not claim(claims, i):
                continue  # (another part has it)
            from ..infra.paths import kill_point

            kill_point("after_records:%d:%d" % (len(rep.records), part))
            taken += 1
            name = os.path.splitext(os.path.basename(path))[0]
            if progress is not None and progress(taken - 1, len(want), name) is False:
                if claims is not None:
                    _emit(rep, page_record(i, path, "not_checked"), out, part, on_record)
                break
            rec = run.page(i, path)
            _emit(rep, rec, out, part, on_record)
            if rec["outcome"] == "not_checked":
                break  # (a cancelled wait: the run stops)
    finally:
        if out is not None:
            out.close()
    from ..format import journal

    rep.recovered = journal.take_notes()
    return rep


def claim(folder: str, i: int) -> bool:
    """Take page ``i`` of a parallel run (its claim file, O_EXCL; it holds this process's
    pid). False: another part has it."""
    try:
        fd = os.open(os.path.join(folder, str(i)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except PermissionError:
        return False  # (being created by another part this instant)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    return True


def claimed(folder: str) -> Set[int]:
    """The page indices the parts of a run took (their claim files)."""
    out: Set[int] = set()
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if n.isdigit():
            out.add(int(n))
    return out


def _emit(rep: UpdateReport, rec: dict, out, part: int, on_record=None) -> None:
    import json

    rep.add(rec)
    if out is not None:
        out.write(json.dumps(rec) + "\n")
        out.flush()
    if on_record is not None:
        try:
            on_record(rec)
        except Exception:  # noqa: BLE001 -- the figures never stop a run
            from ..infra import logsetup

            logsetup.note_exception("updatepages.emit/on_record")


class _Run:
    """One update_pages run's options and what its pages share: the element stamps
    (_Current) and composer per project, the composite states, the projects whose
    database was in use."""

    #: a page another save changed meanwhile is read and checked again this many times
    RETRIES = 2

    def __init__(
        self,
        *,
        templates,
        genies,
        supergenies,
        symbols,
        composites,
        page_properties,
        backup,
        builder_open,
        skip_now,
        defer,
        resolver_for,
        force=False,
    ):
        self.force = bool(force)
        self.templates, self.genies, self.supergenies = templates, genies, supergenies
        self.symbols, self.composites = symbols, composites
        self.page_properties, self.backup = page_properties, backup
        self.in_builder = {(str(p).strip().lower(), str(n).strip().lower()) for p, n in builder_open}
        self.skip_now, self.defer = skip_now, defer
        self.resolver_for = resolver_for
        self.currents: Dict[str, _Current] = {}
        self.composers: Dict[str, object] = {}  # project dir -> shared ComposeContext
        self.comp_memo: Dict[tuple, object] = {}  # composites_current's instance states
        self.db_busy: Set[str] = set()  # projects whose database was in use

    def page(self, i: int, path: str) -> dict:
        """The record of page ``i`` (its update, retried when another save changed it)."""
        from ..editing import PageChangedOnDisk
        from ..format.projectlock import LockCancelled

        for attempt in range(self.RETRIES + 1):
            if self.skip_now is not None and self.skip_now(path):
                return page_record(i, path, "skipped_open")
            try:
                return self._page(i, path)
            except PageChangedOnDisk:
                continue
            except LockCancelled:
                return page_record(i, path, "not_checked")
            except Exception as e:  # noqa: BLE001 -- one bad page never stops the pass
                return page_record(i, path, "failed", checked=1, detail=str(e))
        return page_record(
            i,
            path,
            "changed_meanwhile",
            checked=1,
            stale=1,
            detail="another save changed it while it was being updated",
        )

    def _page(self, i: int, path: str) -> dict:
        from ..editing import (
            EditableDocument,
            PageBusyError,
            PageChangedOnDisk,
            ProjectBusyError,
            ProjectDatabaseInUse,
        )
        from ..format.projectlock import LockCancelled
        from . import pendingrows

        name = os.path.splitext(os.path.basename(path))[0]
        extra: dict = {}
        if pendingrows.marked(path) is not None:
            # an earlier save left the rows behind the page file: write them now
            try:
                if pendingrows.finish(path) == "written":
                    extra["rows_written"] = 1
            except LockCancelled:
                raise
            except OSError as e:
                extra["rows_pending"] = 1
                extra["warning"] = "the project database rows of an earlier save could not be written: %s" % e
        pdir = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        current = self.currents.get(pdir)
        if current is None:
            # the same search path as the document's resolver (CTG_LIB_PATH
            # carries a real project dir for pages outside their project)
            current = self.currents[pdir] = _Current(self.resolver_for(path))
        _SHARE_PARSE[0] = True
        try:
            # (an out-of-date entry no record uses: 'dead', the page's save drops it)
            stale, dead = stale_genie_links(path, current) if self.genies else (set(), [])
            tpl_stale = stale_template(path, current) if self.templates else False
            sg_stale = stale_supergenie(path, current) if self.supergenies else False
            sym_stale, sym_dead = stale_symbol_links(path, current) if self.symbols else (set(), [])
        finally:
            _SHARE_PARSE[0] = False
            _PARSE_SLOT[0] = None
        dead = list(dead) + list(sym_dead)
        comp_here = bool(self.composites) and has_composites(path)
        other = bool(stale or tpl_stale or sg_stale or sym_stale)
        # out of date only through dead entries: saved all the same (CtDraw's load + save
        # rebuilds the link table from the records), else every run calls it out of date
        save_for_links = bool(dead)
        force = self.force
        if force:
            # (every page opened, composed again and saved: no stamp test. A page with
            # composites waits for the one helper that composes them, as a stale one)
            if comp_here and self.defer:
                return page_record(i, path, "deferred", **extra)
        if comp_here and self.composers.get(pdir) is None:
            from ..format.composer_build import ComposeContext

            self.composers[pdir] = ComposeContext(path, current.resolver)
        if comp_here and not force:
            cctx = self.composers[pdir]
            # (with deferral it is always asked: a page that must compose waits for the
            # one helper that composes at the end, and is not opened here)
            ok = composites_current(path, cctx, self.comp_memo) if (self.defer or not other) else False
            if self.defer and not ok:
                return page_record(i, path, "deferred", **extra)
            if ok and not other:
                comp_here = False  # nothing to do: the page is not opened
            elif ok:
                comp_here = True  # (opened anyway: composes nothing)
        if not other and not comp_here and not save_for_links and not force:
            return page_record(i, path, "current", checked=1, **extra)
        proj = os.path.basename(os.path.dirname(os.path.abspath(path))).lower()
        if (proj, name.lower()) in self.in_builder:
            # its next save there would win
            due = other or save_for_links or force
            return page_record(
                i, path, "open_in_builder" if due else "current", checked=1, stale=int(due), **extra
            )
        if pdir in self.db_busy:
            from . import pgregister

            try:
                pgregister.probe_writable(os.path.dirname(os.path.abspath(path)))
                self.db_busy.discard(pdir)
            except ProjectDatabaseInUse as e:
                return page_record(i, path, "db_in_use", checked=1, stale=1, detail=str(e), **extra)
        doc = EditableDocument.open(path)
        c = 0
        if comp_here:
            c = (
                doc.update_composites(ctx=self.composers[pdir], force=True)
                if force
                else doc.update_composites(ctx=self.composers[pdir])
            )
        skipped = int(getattr(doc, "composite_update_skipped", 0) or 0)
        if c:
            # the page's composition-cache elements changed: other pages'
            # stamps of them are read afresh
            current.clear()
        if not other and not c and not save_for_links and not force:
            return page_record(i, path, "current", checked=1, composites_skipped=skipped, **extra)
        n = 0
        ph = 0
        ch: dict = {}
        # the template first, then the super genie and the symbols, then the placeholders
        # and the genies: the open's order (EditableDocument.apply_open_updates) and
        # CtDraw's load (the oracle chain5/order: a page whose template and genie both
        # gained records gives the template's new members the lowest free handles first)
        if force:
            # (the template and the super genie composed again also when current; every
            # placed symbol re-derived, not only the stale ones)
            t = doc.update_template(force=True, page_properties=self.page_properties) if self.templates else 0
            sg = doc.update_supergenie_page(force=True) if self.supergenies else 0
            sy = doc.update_symbol_placements(None) if self.symbols else 0
        else:
            t = doc.update_template(page_properties=self.page_properties) if tpl_stale else 0
            sg = doc.update_supergenie_page() if sg_stale else 0
            sy = doc.update_symbol_placements(sym_stale) if sym_stale else 0
        if self.genies and doc.placeholder_genies():
            # PLACEHOLDER genies whose library opens again (CtDraw's save of the page while
            # the library was missing: a marker and a stamp-less entry):
            # placed again first, as the editor's open and CtDraw's load + save do (the
            # oracle: FxReturned), so their entries take the current stamp. Otherwise the page
            # reads out of date on every run
            ph = int(doc.instantiate_placeholder_genies() or 0)
        # (a placed genie whose instance holds a placeholder of its element that can be
        # placed now: composed again in full, the genie placed in it, as CtDraw's load)
        hosts = doc.placeholder_hosts() if (self.genies and stale) else set()
        if force and self.genies:
            from ..format.symbols import held_libraries

            # every placed genie composed again in full from its element as it is now
            ch = {(a.lower(), b.lower()): None for _i, (a, b) in doc.genie_instances()}
            with held_libraries():
                n = doc.apply_genie_changes(ch, force=True) if ch else 0
        elif stale:
            from ..format.symbols import held_libraries

            # (one read of each library: what changed, the composition and the stamps the
            # page quotes from the same version of each, also when the editor saves one
            # meanwhile: format/symbols.held_libraries)
            with held_libraries():
                # a genie whose own definition changed is composed again in full; one
                # where only nested genies changed gets just their copies
                ch = {k: v for k, v in doc.genie_changes().items() if k in stale or k in hosts}
                n = doc.apply_genie_changes(ch) if ch else 0
        # (a genie left as stored: a nested genie's library could not be read then, or its
        # composition failed. The page keeps that genie's old stamp, so the next run tries
        # again; the result says so instead of a plain 'updated')
        left = sorted("%s.%s" % k for k in (getattr(doc, "genie_update_left", None) or ()))
        if left or getattr(doc, "genie_update_unfinished", False):
            extra["warning"] = "; ".join(
                filter(
                    None,
                    (
                        extra.get("warning", ""),
                        (
                            "not every genie could be updated (%s); the log says why. The next run "
                            "tries again" % ", ".join(left)
                        )
                        if left
                        else "not every genie could be updated; the log says why. The next run tries again",
                    ),
                )
            )
        restamped = (bool(sym_stale) and getattr(doc, "symbol_stamps_updated", False)) or (
            bool(stale) and getattr(doc, "genie_stamps_updated", False)
        )
        if not (n or ph or t or sg or sy or c or restamped or save_for_links or force):
            # out of date, and nothing on it changes: its own outcome, said why
            objs = doc.parsed.page.objects
            return page_record(
                i,
                path,
                "unchanged",
                checked=1,
                stale=1,
                detail=unchanged_reason(
                    stale,
                    ch,
                    tpl_stale,
                    sg_stale,
                    sym_stale,
                    placeholders=placeholder_keys(objs),
                    in_template=template_placeholder_keys(objs),
                ),
                composites_skipped=skipped,
                **extra,
            )
        read = getattr(doc, "_disk_stamp", None)
        try:
            doc.save(batch=True, backup=(self.backup, "update-pages") if self.backup else None)
        except PageChangedOnDisk:
            raise
        except ProjectDatabaseInUse as e:
            self.db_busy.add(pdir)
            return page_record(
                i, path, "db_in_use", checked=1, stale=1, detail=str(e), composites_skipped=skipped, **extra
            )
        except (ProjectBusyError, PageBusyError) as e:
            return page_record(
                i, path, "failed", checked=1, stale=1, detail=str(e), composites_skipped=skipped, **extra
            )
        warning = "; ".join(
            filter(None, (extra.pop("warning", ""), (doc.save_warning or "").replace("\n", " ")))
        )
        pending = int(bool(doc.rows_pending))
        extra.pop("rows_pending", None)
        # (the save left out every entry no record uses: the dead ones found above, and any
        # other the page carried)
        dropped = list(getattr(doc, "_pruned_links", None) or ())
        # (a language label entry ('\label') no text uses any more is no link to a library
        # element: named in the detail, counted apart)
        labels = [n for _g, n in dropped if str(n).startswith("\\")]
        links_gone = [n for _g, n in dropped if not str(n).startswith("\\")]
        detail = "; ".join(
            x
            for x in (
                "links removed: %s" % ", ".join(links_gone) if links_gone else "",
                "labels removed: %s" % ", ".join(labels) if labels else "",
            )
            if x
        )
        # (the file's (mtime_ns, size) it read and wrote: an editor tab that holds what it
        # read takes the file as its own, never 'changed by another program')
        wrote = getattr(doc, "_disk_stamp", None)
        return page_record(
            i,
            path,
            "updated",
            checked=1,
            stale=1,
            instances=int(n or 0),
            placeholders=ph,
            templates=int(bool(t)),
            supergenies=int(bool(sg)),
            symbols=int(sy or 0),
            composites=int(c or 0),
            composites_skipped=skipped,
            links_dropped=len(links_gone),
            labels_dropped=len(labels),
            detail=detail,
            rows_pending=pending,
            warning=warning,
            read=list(read) if read else None,
            wrote=list(wrote) if wrote else None,
            **extra,
        )


def placeholder_keys(records) -> Set[Tuple[str, str]]:
    """(library, element), lower case, of the genies the PLACEHOLDER DummyMarkers of a page
       stand for (their '_Library' bag: what CtDraw's save writes for a genie whose library it
    could not open), whether or not they can be placed again now."""
    from ..format.symbols import reference_from_body, reference_from_symbol_value

    out: Set[Tuple[str, str]] = set()
    for po in records:
        if po.type_code != 0xFFFF or po.class_flag:
            continue
        lib = str(po.raw_properties().get("_Library") or "").strip()
        if not lib:
            continue
        ref = (
            reference_from_body(lib.encode("latin-1", "replace"))
            if ".<ct" in lib.lower()
            else reference_from_symbol_value(lib)
        )
        if ref is not None and ref.library and ref.element:
            out.add((ref.library.lower(), ref.element.lower()))
    return out


def template_placeholder_keys(records) -> Set[Tuple[str, str]]:
    """:func:`placeholder_keys` of the placeholders inside the page's template copy: its
    template element holds them (its save while their library did not open), and the
    template's load places them again once their library opens, so the page's template
    step does (template_place._place_placeholders)."""
    from ..format import template_place as TP

    out: Set[Tuple[str, str]] = set()
    for a, b, _ref in TP.template_roots(records):
        out |= placeholder_keys(records[a:b])
    return out


def unchanged_reason(stale, changes, tpl_stale, sg_stale, sym_stale, placeholders=(), in_template=()) -> str:
    """Why an out-of-date page that was opened ended up unchanged."""

    def names(keys):
        return ", ".join(sorted("%s.%s" % k for k in keys))

    out = []
    held = {tuple(k) for k in placeholders or ()}
    tpl_held = {tuple(k) for k in in_template or ()}
    loose = [k for k in stale if k not in changes and k not in held]
    if loose:
        out.append(
            "its link table quotes an older version of %s, but no genie placed on the "
            "page is one" % names(loose)
        )
    in_tpl = [k for k in stale if k not in changes and k in held and k in tpl_held]
    if in_tpl:
        # (the template element holds the placeholder: the template step places the genie
        # in the page's copy, as CtDraw's load does, oracle chain6/template_placeholder;
        # here it could not)
        out.append(
            "a placeholder of %s in the page's template could not be placed again "
            "(the log says why)" % names(in_tpl)
        )
    waiting = [k for k in stale if k not in changes and k in held and k not in tpl_held]
    if waiting:
        # (a stamp-less entry: the placeholder stands for a genie that opens again)
        out.append("a placeholder of %s could not be placed again (the log says why)" % names(waiting))
    nested = [k for k, v in changes.items() if v is not None]
    if nested:
        out.append(
            (
                "only genies nested in %s changed, and the page already shows them. The library genie "
                "still has the old copy: tick 'Save the library genies in the chain first' to update it"
            )
            % names(nested)
        )
    whole = [k for k, v in changes.items() if v is None]
    if whole:
        out.append(
            "%s could not be composed again (its library or its instances could not be read)" % names(whole)
        )
    if tpl_stale:
        out.append("its template could not be applied again")
    if sg_stale:
        out.append("its super genie could not be applied again")
    if sym_stale:
        out.append("no placed symbol of %s could be updated" % names(sym_stale))
    return "; ".join(out) or "nothing on it changed"


def backup_page(path: str, dest_root: str, kind: str = "update-pages") -> None:
    """:func:`_backup_page` for the other batch jobs (picture_worker: Find Duplicate
    Pictures)."""
    _backup_page(path, dest_root, kind)
