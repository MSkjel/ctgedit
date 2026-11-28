"""Who uses a library element, directly or through other elements, and which users are out of date."""

from __future__ import annotations

from ..infra import backups

from ..infra.paths import lib_path_dirs, path_key

from ..format.fileio import LIBRARY_EXTS

import os
import struct
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple
from ..infra.logsetup import note_exception as _note_exc


# -- the element tables of a library file -------------------------------------------------
@dataclass
class ElementTable:
    """One library element's own stamp and its link-table names. ``error`` = why its block
    could not be read ('' when it could; ``stamp`` is None then)."""

    name: str
    stamp: Optional[int]
    names: Tuple[str, ...]
    error: str = ""
    #: the GUID each entry of ``names`` quotes (the same order; () when not read): a stamp
    #: is whole seconds, the GUID tells two saves with one stamp apart
    guids: Tuple[bytes, ...] = ()
    #: the element's own GUID (its identity after the stamp; b"" when not read)
    guid: bytes = b""


#: {library path: (its version (fileio.file_version), its tables, why it could not be read
#: or '')}
_LIB_CACHE: Dict[str, Tuple[tuple, Dict[str, ElementTable], str]] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _LIB_CACHE.clear()


def library_tables(lib_path: str) -> Dict[str, ElementTable]:
    """{element name: ElementTable} for every element of a library file."""
    return _library(lib_path)[1]


def library_error(lib_path: str) -> str:
    """Why the library file couldn't be read ('' if it was)."""
    return _library(lib_path)[2]


def library_read(lib_path: str) -> Tuple[Dict[str, ElementTable], str]:
    """(:func:`library_tables`, :func:`library_error`) of ONE read of the file: an I/O error
    is not cached, so two calls could see two answers."""
    _st, tables, err = _library(lib_path)
    return tables, err


def _library(lib_path: str) -> Tuple[tuple, Dict[str, ElementTable], str]:
    """The cache entry of a library file: (its (mtime_ns, size), its tables, its error)."""
    from ..format import pagesummary as PS
    from ..format.links import parse_link_table
    from ..infra import logsetup
    from ..format.fileio import file_version

    try:
        # (this process's own writes count: two of one size within one clock tick)
        st = file_version(lib_path)
    except OSError as e:
        logsetup.note_exception("genieusage.library_tables/stat", library=lib_path)
        return ((), {}, "it could not be opened: %s" % (e.strerror or e))
    with _LOCK:
        hit = _LIB_CACHE.get(lib_path)
        if hit is not None and hit[0] == st:
            return hit
    out: Dict[str, ElementTable] = {}
    err = ""
    # an I/O error (another program holds the file with no read share) passes: it is not
    # cached, the next call reads the file again; a content problem is fixed for the bytes
    transient = False
    try:
        # (unbuffered: a library's directory read seeks to every element's head)
        with open(lib_path, "rb", buffering=0) as fh:
            d = PS.read_dir(fh, lib_path, st[1])
            err = PS.directory_problem(d)
            # (a truncated file: the elements it holds whole are read still)
            if d.ok and not PS.header_problem(d):
                legacy = d.word2 < 0x0C
                hs = PS.PAGE_HEAD_SIZE_LEGACY if legacy else PS.PAGE_HEAD_SIZE
                for e in d.elements():
                    h = PS.read_head(fh, e.offset, e.size, d.word2)
                    if h is None:
                        out[e.name.lower()] = ElementTable(
                            e.name, None, (), "its block head could not be read (%d bytes)" % e.size
                        )
                        continue
                    fh.seek(e.offset)
                    raw = fh.read(hs)
                    ident = raw[0x20:0x24] if legacy else raw[0x20:0x34]
                    stamp = struct.unpack_from("<I", ident, 0)[0] if len(ident) >= 4 else None
                    names: Tuple[str, ...] = ()
                    guids: Tuple[bytes, ...] = ()
                    if h.link_count:
                        n = min(h.link_count * PS.LINK_ENTRY_SIZE, max(0, e.size - hs))
                        fh.seek(e.offset + hs)
                        t = parse_link_table(fh.read(n))
                        names = tuple(nm for _g, nm in (t[1] if t else []))
                        guids = tuple(bytes(g) for g, _nm in (t[1] if t else []))
                    own = bytes(ident[4:20]) if len(ident) >= 20 else b""
                    out[e.name.lower()] = ElementTable(e.name, stamp, names, guids=guids, guid=own)
    except (OSError, struct.error, ValueError) as e:
        logsetup.note_exception("genieusage.library_tables", library=lib_path)
        out = {}
        err = "it could not be read: %s" % (getattr(e, "strerror", None) or "%s: %s" % (type(e).__name__, e))
        transient = isinstance(e, OSError)
    if err:
        logsetup.get("genies").warning("library %s could not be read: %s", lib_path, err)
    entry = (st, out, err)
    with _LOCK:
        if transient:
            _LIB_CACHE.pop(lib_path, None)
        else:
            _LIB_CACHE[lib_path] = entry
    return entry


def element_stamp(lib_path: str, element: str) -> Optional[int]:
    t = library_tables(lib_path).get(element.lower())
    return t.stamp if t is not None else None


# -- the graph ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    """A page (``element`` '') or a library element."""

    kind: str  # 'page' | 'element'
    path: str  # the .ctg, or the library file
    element: str = ""  # the element's name as its library spells it
    project: str = ""

    @property
    def key(self) -> tuple:
        return (self.kind, path_key(self.path), self.element.lower())

    @property
    def library(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lstrip(".").lower()

    @property
    def label(self) -> str:
        if self.kind == "page":
            return os.path.splitext(os.path.basename(self.path))[0]
        return "%s.%s" % (self.library, self.element)

    @property
    def what(self) -> str:
        """'page', 'genie', 'super genie', 'template', 'symbol'."""
        if self.kind == "page":
            return "page"
        if self.ext == "ctt":
            return "template"
        if self.ext == "ctl":
            return "symbol"
        return "super genie" if "@super" in self.element.lower() else "genie"


@dataclass
class Link:
    """``user``'s link-table entry of ``target``."""

    user: Ref
    target: Ref
    entry: str
    stored: Optional[int]  # the stamp the entry quotes
    current: Optional[int]  # the target's stamp now
    #: the GUID the entry quotes and the target's GUID now (b"" when not known): one stamp
    #: written by two saves (whole seconds) is told apart by them
    stored_guid: bytes = b""
    current_guid: bytes = b""

    @property
    def stale(self) -> bool:
        if self.stored is None:
            return self.unstamped
        if (
            self.current is not None
            and self.stored == self.current
            and any(self.stored_guid)
            and any(self.current_guid)
        ):
            return self.stored_guid != self.current_guid
        return self.current is not None and self.stored != self.current

    @property
    def unstamped(self) -> bool:
        """An unstamped entry whose element exists now."""
        return (
            self.stored is None
            and self.current is not None
            and self.target.ext in ("ctm", "ctl", "ctt")
            and bool(self.entry)
            and "#" not in self.entry
        )


UP_TO_DATE = "up to date"
OUT_OF_DATE = "out of date"
WAITING = "waiting"  # up to date itself, an element between is out of date


@dataclass
class User:
    """One user of the element: how it uses it and whether it needs an update."""

    ref: Ref
    links: List[Link]  # its entries of the elements on its chains
    via: Tuple[Ref, ...]  # the shortest chain to the element (empty: direct)
    direct: bool
    status: str = UP_TO_DATE
    registered: Optional[bool] = None  # pages: in pages.DBF (None: no pages.DBF)
    shipped: bool = False  # in a library supplied with Plant SCADA
    #: why its library file could not be read whole ('' when it could): it is never saved
    damaged: str = ""
    #: saved with every copy of the genies nested in it composed again (chain_plan
    #: ``compose_all``: its stamps say current but a copy differs in content from a fresh
    #: composition, or it is saved anyway and the box asks for every copy)
    compose_all: bool = False

    @property
    def needs_update(self) -> bool:
        return self.status != UP_TO_DATE


@dataclass
class Usage:
    target: Ref
    users: List[User] = field(default_factory=list)
    projects: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    #: (library file, element or '') that could not be read (UsageIndex.unreadable)
    unreadable: List[Tuple[str, str]] = field(default_factory=list)
    #: the index was stopped (its progress said so): the users are not all known
    stopped: bool = False

    def direct(self) -> List[User]:
        return [u for u in self.users if u.direct]

    def indirect(self) -> List[User]:
        return [u for u in self.users if not u.direct]

    def of(self, ref: Ref) -> Optional[User]:
        k = ref.key
        return next((u for u in self.users if u.ref.key == k), None)

    def counts(self) -> Dict[str, int]:
        c = {"pages": 0, "elements": 0, "direct_pages": 0, "direct_elements": 0, "out_of_date": 0}
        for u in self.users:
            page = u.ref.kind == "page"
            c["pages" if page else "elements"] += 1
            if u.direct:
                c["direct_pages" if page else "direct_elements"] += 1
            if u.needs_update:
                c["out_of_date"] += 1
        return c


def _entry_key(name: str) -> Optional[Tuple[str, str, str]]:
    """'lib.elem.<ext#stamp' -> (lib, elem, ext) lower case; None for a label / other."""
    from ..format.links import strip_stamp

    if not name or name.startswith("\\"):
        return None
    from ..format.symbols import split_lib_element

    base = strip_stamp(name)
    stem, _, ext = base.rpartition(".")
    lib, elem = split_lib_element(stem)  # the LAST dot
    ext = ext.lstrip("<").lower()
    if not (lib and elem) or ext not in ("ctm", "ctt", "ctl"):
        return None
    return lib.lower(), elem.lower(), ext


def _entry_stamp(name: str) -> Optional[int]:
    """links.entry_stamp (one rule for the stamp: exactly '#%08x' at the end)."""
    from ..format.links import entry_stamp

    return entry_stamp(name)


class _Referrer:
    """A page or element with its link-table names, grouped by entry key. ``damaged`` = why
    the element's library file could not be read whole ('' when it could): an element of a
    truncated file whose links could be read uses what it places, but it is never saved."""

    __slots__ = ("ref", "entries", "registered", "damaged", "guids")

    def __init__(
        self,
        ref: Ref,
        names: Iterable[str],
        registered=None,
        attachments=False,
        damaged: str = "",
        guids: Iterable[bytes] = (),
    ):
        self.ref = ref
        self.registered = registered
        self.damaged = damaged
        self.entries: Dict[Tuple[str, str, str], str] = {}
        #: the GUID of each entry of ``entries`` (when the table's GUIDs were read)
        self.guids: Dict[Tuple[str, str, str], bytes] = {}
        gs = list(guids)
        for n, nm in enumerate(names):
            k = _entry_key(nm)
            if k is None:
                continue
            if attachments and "@super" in k[1]:
                continue  # an element's attached super genie: not a placement
            if k not in self.entries and n < len(gs):
                self.guids[k] = gs[n]
            self.entries.setdefault(k, nm)


class UsageIndex:
    """The link tables of every page and library element of some projects, indexed by the
    (lib, elem, ext) names they quote. Build once (:func:`build_index`), ask :meth:`usage`."""

    def __init__(self):
        self.by_name: Dict[Tuple[str, str, str], List[_Referrer]] = {}
        self.referrers: List[_Referrer] = []  # in the order they were added
        self.projects: List[str] = []
        self.problems: List[str] = []  # what could not be read, and why
        #: (library file, element: '' for the whole file) that could not be read
        self.unreadable: List[Tuple[str, str]] = []
        self.stopped = False  # progress said stop: not every project
        self._resolvers: Dict[str, object] = {}
        self._resolved: Dict[tuple, Optional[str]] = {}

    def add(self, r: _Referrer) -> None:
        self.referrers.append(r)
        for k in r.entries:
            self.by_name.setdefault(k, []).append(r)

    # -- resolution: the user's own search path ---------------------------------------------
    def _resolver(self, folder: str):
        key = os.path.normcase(folder)
        res = self._resolvers.get(key)
        if res is None:
            from ..format.symbols import SymbolResolver

            extra = lib_path_dirs()
            res = self._resolvers[key] = SymbolResolver.for_page(os.path.join(folder, "_.ctg"), extra)
        return res

    def resolve(self, user: Ref, key: Tuple[str, str, str]) -> Optional[str]:
        """The library file ``user``'s entry ``key`` names (the first same-named library on
        its project's search path that holds the element), None when none does."""
        folder = os.path.dirname(os.path.abspath(user.path))
        ck = (os.path.normcase(folder), key)
        if ck in self._resolved:
            return self._resolved[ck]
        out = None
        unread = False
        try:
            res = self._resolver(folder)
            for f in res._find_library_files(key[0], key[2]):
                tables, err = library_read(f)
                if key[1] in tables:
                    out = f
                    break
                unread = unread or bool(err and not tables)
            if out is None and unread:
                # (a library file that could not be read this once, a scanner holding it just
                # after its save: not remembered as 'not there')
                return None
        except Exception:  # noqa: BLE001 -- unresolved: nothing to compare with
            from ..infra import logsetup

            logsetup.note_exception("genieusage.resolve", page=user.path)
            # (not remembered: the failure may be transient)
            return None
        self._resolved[ck] = out
        return out

    def users_of(self, target: Ref) -> List[Tuple[_Referrer, str]]:
        """The referrers whose entry resolves to ``target``, with the entry."""
        key = (target.library.lower(), target.element.lower(), target.ext)
        want = path_key(target.path)
        out = []
        for r in self.by_name.get(key, ()):
            if r.ref.key == target.key:
                continue
            f = self.resolve(r.ref, key)
            if f is not None and path_key(f) == want:
                out.append((r, r.entries[key]))
        return out

    # -- the users ---------------------------------------------------------------------------
    def usage(self, lib_path: str, element: str) -> Usage:
        """Every direct and indirect user of ``lib_path``'s ``element``."""
        from ..format.template_place import shipped_project

        t = library_tables(lib_path).get(element.lower())
        target = Ref(
            "element",
            lib_path,
            t.name if t else element,
            os.path.basename(os.path.dirname(os.path.abspath(lib_path))),
        )
        out = Usage(
            target,
            projects=list(self.projects),
            problems=list(self.problems),
            unreadable=list(self.unreadable),
            stopped=bool(self.stopped),
        )
        # breadth first from the element: the shortest chain to each user
        users: Dict[tuple, User] = {}
        order: List[tuple] = []
        queue = deque([(target, ())])
        seen_targets = {target.key}
        while queue:
            tgt, chain = queue.popleft()
            cur = element_stamp(tgt.path, tgt.element)
            tt = library_tables(tgt.path).get(tgt.element.lower())
            for r, entry in self.users_of(tgt):
                ek = (tgt.library.lower(), tgt.element.lower(), tgt.ext)
                link = Link(
                    r.ref,
                    tgt,
                    entry,
                    _entry_stamp(entry),
                    cur,
                    r.guids.get(ek, b""),
                    tt.guid if tt is not None else b"",
                )
                u = users.get(r.ref.key)
                if u is None:
                    shipped = r.ref.kind == "element" and shipped_project(r.ref.path) is not None
                    u = users[r.ref.key] = User(
                        r.ref,
                        [link],
                        chain,
                        not chain,
                        registered=r.registered,
                        shipped=shipped,
                        damaged=r.damaged,
                    )
                    order.append(r.ref.key)
                else:
                    u.links.append(link)
                    if not chain:
                        u.direct = True
                if r.ref.kind == "element" and r.ref.key not in seen_targets:
                    seen_targets.add(r.ref.key)
                    queue.append((r.ref, chain + (r.ref,)))
        # status: out of date = one of its entries is stale; waiting = an element it
        # (directly or indirectly) uses on the way to the target needs an update. Not one
        # in a library supplied with Plant SCADA: no save brings it up to date, and its
        # users already show what it nests as it is now (CtDraw's load + save of a page
        # placing a shipped genie whose own table quotes older nested stamps leaves the
        # page as it is: updatepages.nested_changes)
        for k in order:
            u = users[k]
            if any(lk.stale for lk in u.links):
                u.status = OUT_OF_DATE
        changed = True
        while changed:
            changed = False
            for k in order:
                u = users[k]
                if u.status != UP_TO_DATE:
                    continue
                for lk in u.links:
                    below = users.get(lk.target.key)
                    if below is not None and below.status != UP_TO_DATE and not below.shipped:
                        u.status = WAITING
                        changed = True
                        break
        out.users = [users[k] for k in order]
        return out


def scope_projects(lib_path: str, catalog=None) -> list:
    """The ProjectInfo of the library's project and of every project that includes it."""
    if catalog is None:
        from .catalog import catalog_for

        catalog = catalog_for()
    d = os.path.dirname(os.path.abspath(lib_path))
    info = catalog.project_for_dir(d) or catalog.folder(d)
    out = [info] + list(catalog.dependents(info))
    # the other projects whose search path reaches the library outside the include chain
    # (SymbolResolver.for_page): a library folder of Settings > Pages and libraries
    # (CTG_LIB_PATH) is searched by every project, and with the sibling search on
    # (CTG_LIB_SIBLINGS) every project under the same User folder searches the others.
    norm = os.path.normcase
    extra = lib_path_dirs()
    in_lib_path = any(norm(os.path.abspath(p)) == norm(d) for p in extra)
    siblings = os.environ.get("CTG_LIB_SIBLINGS") == "1"
    if in_lib_path or siblings:
        seen = {norm(os.path.abspath(p.dir)) for p in out}
        parent = norm(os.path.dirname(d))
        for p in catalog.projects():
            pd = norm(os.path.abspath(p.dir))
            if pd in seen:
                continue
            if in_lib_path or norm(os.path.dirname(pd)) == parent:
                seen.add(pd)
                out.append(p)
    return out


def build_index(projects, catalog=None, progress=None, pages: bool = True) -> UsageIndex:
    """Build the usage index of these projects."""
    from ..infra import logsetup

    if catalog is None:
        from .catalog import catalog_for

        catalog = catalog_for()
    idx = UsageIndex()
    infos = []
    for p in projects:
        if isinstance(p, str):
            p = catalog.project_for_dir(p) or catalog.folder(p)
        if p is not None and getattr(p, "exists", True):
            infos.append(p)
    # (the files first, so the progress says 'i of n' over them all: the pages' link
    # tables and the library files of every project)
    seen = set()
    work = []  # (info, the pages, the library files)
    for info in infos:
        if info.key in seen:
            continue
        seen.add(info.key)
        idx.projects.append(info.name)
        page_entries = []
        try:
            page_entries = list(catalog.pages(info)) if pages else []
        except Exception as ex:  # noqa: BLE001 -- the project's libraries are read still
            logsetup.note_exception("genieusage.build_index/pages", project=info.dir)
            idx.problems.append("%s: its pages could not be listed: %s" % (info.name, ex))
        try:
            files = [f for f in sorted(os.listdir(info.dir)) if f.lower().endswith(LIBRARY_EXTS)]
        except OSError:
            logsetup.note_exception("genieusage.build_index/listdir", project=info.dir)
            files = []
        work.append((info, page_entries, files))
    total = sum(len(p) + len(f) for _i, p, f in work)
    at = 0

    def report(name: str) -> bool:
        """``progress`` before file ``name`` of its project; False = stop."""
        nonlocal at
        i, at = at, at + 1
        return progress is None or progress(i, total, name) is not False

    for info, page_entries, files in work:
        if idx.stopped:
            break
        for e in page_entries:
            if not report("%s\\%s" % (info.name, os.path.basename(e.path))):
                idx.stopped = True
                break
            try:
                names = catalog.link_names(e.path)
            except Exception as ex:  # noqa: BLE001 -- one page never stops the rest
                logsetup.note_exception("genieusage.build_index/page", page=e.path)
                idx.problems.append("%s: its link table could not be read: %s" % (e.path, ex))
                continue
            idx.add(_Referrer(Ref("page", e.path, "", info.name), names, registered=e.registered))
        if idx.stopped:
            break
        for f in files:
            if not report("%s\\%s" % (info.name, f)):
                idx.stopped = True
                break
            path = os.path.join(info.dir, f)
            tables, err = library_read(path)
            if err:
                # its elements are never saved (chain_plan, update_plan); those whose links
                # could be read (a truncated file) still count as users of what they place
                idx.unreadable.append((path, ""))
                idx.problems.append("%s could not be read: %s" % (path, err))
            for t in tables.values():
                if t.names:
                    idx.add(
                        _Referrer(
                            Ref("element", path, t.name, info.name),
                            t.names,
                            attachments=True,
                            damaged=err,
                            guids=t.guids,
                        )
                    )
                elif t.stamp is None and not err:
                    # (its head could not be read: neither its links nor its stamp are known;
                    # in a file that could not be read whole, the file is named already)
                    idx.unreadable.append((path, t.name))
                    idx.problems.append(
                        "%s: %s could not be read: %s"
                        % (path, t.name, t.error or "its block head could not be read")
                    )
    return idx


def element_users(lib_path: str, element: str, catalog=None, progress=None) -> Usage:
    """Every page and library element that uses ``lib_path``'s ``element``, directly or
    through other elements, with its status. ``progress`` as :func:`build_index` takes it
    (per file; False stops: the Usage says ``stopped``)."""
    idx = build_index(scope_projects(lib_path, catalog), catalog, progress)
    return idx.usage(lib_path, element)


# -- the update order -----------------------------------------------------------------------
@dataclass
class UpdatePlan:
    elements: List[User]  # saved again, in this order (the deepest first)
    pages: List[User]  # then these pages are updated
    skipped: List[Tuple[User, str]] = field(default_factory=list)
    stopped: bool = False  # chain_plan: progress said stop (the plan is not whole)
    #: what could not be read and why ('<path> could not be read: <why>'; the log line), and
    #: the (library file, element or '') of each: their elements are never planned (chain_plan
    #: leaves them out, update_plan puts them in ``skipped``). A library with no live element
    #: is not one.
    problems: List[str] = field(default_factory=list)
    unreadable: List[Tuple[str, str]] = field(default_factory=list)
    #: chain_plan(compose_all): 'lib.element: why' of the elements whose comparison with a
    #: fresh composition failed (left out of the plan; their lines are in ``problems`` too)
    not_compared: List[str] = field(default_factory=list)
    #: chain_plan(compose_all): (label, ['lib.genie', ...]) of the elements left out of the
    #: plan whose copies of those nested genies could not be composed to compare them
    not_composed: List[Tuple[str, List[str]]] = field(default_factory=list)


#: why an element of a library supplied with Plant SCADA is left out of a plan
SHIPPED_WHY = "it is in a library supplied with Plant SCADA"
#: why an element of a library file that could not be read whole is left out of a plan
DAMAGED_WHY = "its library file could not be read whole"


def _deepest_first(elements: List[User]) -> List[User]:
    """``elements`` in the order to save them: each after the ones among them its links
    target (topological order over the usage links; the ready ones of a pass in their
    given order)."""
    keys = {u.ref.key for u in elements}
    # u depends on the elements among its link targets (they must be saved first)
    deps = {u.ref.key: {lk.target.key for lk in u.links if lk.target.key in keys} for u in elements}
    out: List[User] = []
    done: Set[tuple] = set()
    while len(done) < len(elements):
        ready = [u for u in elements if u.ref.key not in done and deps[u.ref.key] <= done]
        if not ready:  # a cycle: keep the order
            ready = [next(u for u in elements if u.ref.key not in done)]
        for u in ready:
            done.add(u.ref.key)
            out.append(u)
    return out


def update_plan(usage: Usage) -> UpdatePlan:
    """The users that need an update, in update order."""
    want = [u for u in usage.users if u.needs_update]
    els = [u for u in want if u.ref.kind == "element"]
    plan = UpdatePlan([], [], problems=list(usage.problems), unreadable=list(usage.unreadable))
    keep = []
    for u in els:
        if u.damaged:
            plan.skipped.append((u, DAMAGED_WHY))
        elif u.shipped:
            plan.skipped.append((u, SHIPPED_WHY))
        else:
            keep.append(u)
    plan.elements = _deepest_first(keep)
    plan.pages = [u for u in want if u.ref.kind == "page"]
    return plan


def chain_plan(
    project_dirs,
    catalog=None,
    progress=None,
    include_shipped: bool = False,
    compose_all: bool = False,
    compose_progress=None,
) -> UpdatePlan:
    """Update Pages' chain: the library elements to save again before their pages."""
    from ..format.template_place import shipped_project

    idx = build_index(project_dirs, catalog, progress, pages=False)
    plan = UpdatePlan(
        [], [], stopped=idx.stopped, problems=list(idx.problems), unreadable=list(idx.unreadable)
    )
    if idx.stopped:
        return plan
    users: Dict[tuple, User] = {}
    for r in idx.referrers:
        if r.ref.kind != "element" or r.ref.key in users:
            continue
        if r.damaged:
            # (its library file could not be read whole: never saved; the box names the
            # file in ``unreadable``, and the copies of what places it stay as they are)
            continue
        links = []
        for key, entry in r.entries.items():
            f = idx.resolve(r.ref, key)
            if f is None:
                continue  # not on its search path: nothing to compare with
            t = library_tables(f).get(key[1])
            target = Ref(
                "element", f, t.name if t else key[1], os.path.basename(os.path.dirname(os.path.abspath(f)))
            )
            if target.key == r.ref.key:
                continue
            links.append(
                Link(
                    r.ref,
                    target,
                    entry,
                    _entry_stamp(entry),
                    t.stamp if t else None,
                    r.guids.get(key, b""),
                    t.guid if t else b"",
                )
            )
        users[r.ref.key] = User(r.ref, links, (), True, shipped=shipped_project(r.ref.path) is not None)
    order = list(users.values())
    for u in order:
        if any(lk.stale for lk in u.links):
            u.status = OUT_OF_DATE
    uncomposed: list = []
    if compose_all:
        uncomposed = _mark_compose_all(order, plan, include_shipped, compose_progress)
        if plan.stopped:
            plan.not_composed = [(u.ref.label, keys) for u, keys in uncomposed]
            return plan

    def saved(u: User) -> bool:
        return u.needs_update and (include_shipped or not u.shipped)

    # waiting = it places (at any depth) an element that is saved again: its copy goes old
    changed = True
    while changed:
        changed = False
        for u in order:
            if u.status != UP_TO_DATE:
                continue
            if any(lk.target.key in users and saved(users[lk.target.key]) for lk in u.links):
                u.status = WAITING
                u.direct = False
                changed = True
    keep = []
    for u in order:
        if not u.needs_update:
            continue
        if saved(u):
            keep.append(u)
            if compose_all and _composes(u):
                # (saved anyway, by its stamps or waiting: with every copy composed in this
                # save, or a copy that drifted stays under current stamps until the next
                # run flags it, its users saved twice)
                u.compose_all = True
        else:
            plan.skipped.append((u, SHIPPED_WHY))
    plan.elements = _deepest_first(keep)
    # (the copies compose-all could not compose, in the elements left out of the plan; one
    # the waiting pass planned after all is saved, and its save names them)
    planned = {u.ref.key for u in keep}
    plan.not_composed = [(u.ref.label, keys) for u, keys in uncomposed if u.ref.key not in planned]
    return plan


def _composes(u: User) -> bool:
    """``u`` is a genie or template (a library that nests genies: .ctm / .ctt)."""
    return os.path.splitext(u.ref.path)[1].lower() in (".ctm", ".ctt")


def _mark_compose_all(order, plan, include_shipped: bool, progress=None) -> list:
    """Mark genies and templates whose nested copies differ from a fresh composition."""
    from ..editing import ElementDocument

    left: list = []
    todo = [
        u
        for u in order
        if not u.needs_update and (include_shipped or not u.shipped) and u.links and _composes(u)
    ]
    for i, u in enumerate(todo):
        if progress is not None and progress(i, len(todo), u.ref.label) is False:
            plan.stopped = True
            return left
        report: dict = {}
        try:
            doc = ElementDocument.open_element(u.ref.path, u.ref.element)
            if getattr(doc, "read_only", False):
                continue
            ops, _link = doc.nested_update_ops(every=True, report=report)
        except Exception as e:  # noqa: BLE001 -- one element never stops the rest
            from ..infra import logsetup

            logsetup.note_exception("io.genieusage.chain_plan/compose_all", page=u.ref.path)
            plan.problems.append("%s: %s" % (u.ref.label, e))
            plan.not_compared.append("%s: %s" % (u.ref.label, e))
            continue
        if ops:
            u.status = OUT_OF_DATE
            u.compose_all = True
        elif report.get("stale_nested"):
            # (not saved, so its save never names them: the box does)
            left.append((u, nested_copy_labels(doc, report["stale_nested"])))
    return left


# -- running the update ---------------------------------------------------------------------
@dataclass
class UpdateUsersReport:
    elements_saved: List[str] = field(default_factory=list)  # 'lib.element'
    elements_current: List[str] = field(default_factory=list)  # nothing to write
    elements_in_editor: List[str] = field(default_factory=list)  # saved in their tab
    #: (library file, absolute; element) of the elements open in a tab that were saved
    #: there or found current: the tab is found by its file, not by 'lib.element' (two
    #: projects can have libraries of one name)
    tabs_saved: List[Tuple[str, str]] = field(default_factory=list)
    #: (library file, absolute; element; the SHA-1 of the block it read; of the block it
    #: stored; the library files its nested copies were composed from, or None when one of
    #: them changed during the save) of each element it saved or found current: an open tab
    #: that holds the block it read takes the stored one as its own (the editor tells its
    #: helper's write from another program's: :func:`element_version`)
    element_versions: List[tuple] = field(default_factory=list)
    #: the library files written, in order
    libraries_written: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # 'lib.element: why'
    failed: List[str] = field(default_factory=list)
    #: (library file, element or '') not checked: it could not be read (chain_plan)
    unreadable: List[Tuple[str, str]] = field(default_factory=list)
    #: (label, ['lib.genie', ...]): an element saved, or compared (chain_plan's
    #: ``not_composed``), whose copies of those nested genies could not be composed again
    #: (they stay as stored)
    not_composed: List[Tuple[str, List[str]]] = field(default_factory=list)
    #: 'lib.element: why' of the elements chain_plan(compose_all) could not compare
    not_compared: List[str] = field(default_factory=list)
    #: {'lib.element': 'genie' | 'super genie' | 'template' | 'symbol'} of the plan's elements
    #: (Ref.what): the results count them by kind, a template or a symbol is not a genie
    element_kinds: Dict[str, str] = field(default_factory=dict)
    pages: object = None  # updatepages.UpdateReport
    super_pages: List[str] = field(default_factory=list)  # pages a genie save made
    backup: Optional[str] = None
    save_warning: str = ""
    stopped: bool = False


#: public name (genieswap_worker): a link-table entry's (library, element, ext)
entry_key = _entry_key


#: the word before a template's name among the nested copies (nested_copy_labels): a
#: template built on another holds that template's copy, which is no nested genie
TEMPLATE_COPY = "the template "


def nested_copy_labels(doc, keys) -> List[str]:
    """Names for nested copies, as the import table shows them."""
    from ..format.links import strip_stamp
    from ..format.symbols import split_lib_element

    spelled: Dict[Tuple[str, str], str] = {}
    try:
        entries = doc._link_entries_now()[1]
    except Exception:  # noqa: BLE001  # swallow-ok: no table read, the keys name them
        entries = []
    for entry in entries or ():
        name = entry[1] if isinstance(entry, (tuple, list)) else entry
        k = _entry_key(str(name))
        if k is None:
            continue
        lib, elem = split_lib_element(strip_stamp(str(name)).rpartition(".")[0])
        label = "%s.%s" % (lib, elem)
        spelled.setdefault((k[0], k[1]), TEMPLATE_COPY + label if k[2] == "ctt" else label)
    out = [spelled.get((str(a).lower(), str(b).lower()), "%s.%s" % (a, b)) for a, b in keys or ()]
    return sorted(out, key=lambda s: (s[len(TEMPLATE_COPY) :] if s.startswith(TEMPLATE_COPY) else s).lower())


def element_version(doc) -> tuple:
    """(the SHA-1 (hex) of the block the element document ``doc`` holds as stored, '' when
    it has none; the library files its nested copies are composed from,
    ElementDocument.nested_genie_sources, None when they could not be read): what the
    chain's helper reports of each element it saves (UpdateUsersReport.element_versions)."""
    import hashlib

    sess = getattr(doc, "session", None)
    if sess is None:
        return "", None
    sha = hashlib.sha1(bytes(sess.pe.block.data)).hexdigest()
    try:
        sources = frozenset(doc.nested_genie_sources())
    except Exception:  # noqa: BLE001 -- not known: the editor composes the tab again
        from ..infra import logsetup

        logsetup.note_exception("io.genieusage.element_version", page=getattr(doc, "path", ""))
        sources = None
    return sha, sources


def _left_stale(doc) -> List[str]:
    """The genies (and template copies) nested in the element ``doc`` that are still out of
    date after its save (ElementDocument.nested_genie_changes: a copy the save could not
    compose again keeps its old import-table stamp), named as :func:`nested_copy_labels`
    names them."""
    try:
        return nested_copy_labels(doc, doc.nested_genie_changes())
    except Exception:  # noqa: BLE001
        from ..infra import logsetup

        logsetup.note_exception("io.genieusage._left_stale", page=getattr(doc, "path", ""))
        return []


def pages_using_saved(users: Iterable["User"], saved: Iterable[str]) -> List[str]:
    """The pages of ``users`` that need an update and use one of ``saved``."""
    saved_l = {str(x).lower() for x in saved or ()}
    if not saved_l:
        return []
    return [
        os.path.abspath(u.ref.path)
        for u in users
        if u.ref.kind == "page"
        and u.needs_update
        and any(lk.target.label.lower() in saved_l for lk in u.links)
    ]


def update_user_pages(
    pages: Iterable[str],
    *,
    skip: Iterable[str] = (),
    backup: Optional[str] = None,
    builder_open: Iterable[Tuple[str, str]] = (),
    progress=None,
    on_record=None,
    skip_now=None,
    records: Optional[str] = None,
    save_thumbnail=None,
):
    """The pages step of Update Users and the chain after a library save."""
    from ..editing import page_save_thumbnails
    from . import updatepages

    pages = [os.path.abspath(p) for p in pages]
    dirs = []
    for p in pages:
        d = os.path.dirname(p)
        if d not in dirs:
            dirs.append(d)
    with page_save_thumbnails(save_thumbnail):
        rep = updatepages.update_pages(
            dirs,
            skip=list(skip),
            only=pages,
            backup=backup,
            builder_open=[tuple(x) for x in builder_open],
            templates=True,
            genies=True,
            supergenies=True,
            symbols=True,
            composites=True,
            progress=progress,
            on_record=on_record,
            skip_now=skip_now,
            records=records,
        )
    # (two pages of one name in two projects are told apart by their project, as Update
    # Pages' box tells them)
    return updatepages.relabelled(rep, pages)


def _element_for_update(u, rep: UpdateUsersReport, open_element, editor_state, tab_clean):
    """(the document of element user ``u``, whether it is open in a tab, its tab's state),
    or None when it is skipped (unsaved changes in its tab, a read-only library)."""
    from ..editing import ElementDocument

    label = u.ref.label
    doc = open_element(u.ref) if open_element is not None else None
    state = editor_state(u.ref) if doc is None and editor_state is not None else None
    dirty = (
        doc is not None and getattr(doc, "dirty", False) and not (tab_clean is not None and tab_clean(doc))
    )
    if dirty or state == "dirty":
        rep.skipped.append(
            "%s: it has unsaved changes in the editor (save it there, then update again)" % label
        )
        return None
    if doc is not None:
        in_tab = True
    else:
        doc = ElementDocument.open_element(u.ref.path, u.ref.element)
        in_tab = state == "clean"
    if getattr(doc, "read_only", False):
        rep.skipped.append("%s: its library file is read only" % label)
        return None
    return doc, in_tab, state


def _note_element_saved(rep: UpdateUsersReport, u, doc, report: dict, read, in_tab: bool) -> None:
    """What the save of element user ``u`` did, into ``rep`` (``read``: the element's
    version before the save)."""
    label = u.ref.label
    wrote = element_version(doc)
    if read[0] and wrote[0]:
        # (what it read and stored, and the files its copies were composed from:
        # the same before and after the save, or not known)
        same = read[1] is not None and read[1] == wrote[1]
        rep.element_versions.append(
            (
                os.path.abspath(u.ref.path),
                u.ref.element,
                read[0],
                wrote[0],
                sorted(list(x) for x in wrote[1]) if same else None,
            )
        )
    if in_tab:
        # (saved or already current: the tab's automatic changes are in the store)
        rep.tabs_saved.append((os.path.abspath(u.ref.path), u.ref.element))
    # the copies the save could not compose again: they stay as stored (a stamp
    # current already stays current, so only this says it: compose-all)
    left = nested_copy_labels(doc, report.get("stale_nested", ()))
    if getattr(doc, "save_files", None):
        (rep.elements_in_editor if in_tab else rep.elements_saved).append(label)
        if u.ref.path not in rep.libraries_written:
            rep.libraries_written.append(u.ref.path)
        rep.save_warning = rep.save_warning or getattr(doc, "save_warning", "")
        if left:
            rep.not_composed.append((label, left))
    elif left or (left := _left_stale(doc)):
        # nothing written, yet a nested genie is still out of date: its copy could
        # not be composed again (the save kept its old stamp). Said, never counted
        # as 'already matched'
        rep.skipped.append("%s: its copy of %s could not be composed again" % (label, ", ".join(left)))
    else:
        rep.elements_current.append(label)
    for r in getattr(doc, "super_pages", None) or ():
        if r.get("status") == "created":
            rep.super_pages.append(str(r.get("name") or r.get("element") or ""))


def run_update(
    plan: UpdatePlan,
    *,
    backup: Optional[str] = None,
    open_element=None,
    skip_pages: Iterable[str] = (),
    builder_open: Iterable[Tuple[str, str]] = (),
    progress=None,
    page_thumbnail=None,
    backup_kind: str = "update-users",
    editor_state=None,
    tab_clean=None,
    page_progress=None,
    on_record=None,
    skip_now=None,
    element_thumbnail=None,
    save_thumbnail=None,
    on_element=None,
) -> UpdateUsersReport:
    """Update an element's users in plan order."""
    from ..editing import ElementChangedMeanwhile, libraries_changed

    rep = UpdateUsersReport(backup=backup)
    rep.element_kinds = {
        u.ref.label: str(getattr(u.ref, "what", "genie"))
        for u in list(plan.elements) + [u for u, _w in plan.skipped]
    }
    for u, why in plan.skipped:
        rep.skipped.append("%s: %s" % (u.ref.label, why))
    steps = len(plan.elements) + (1 if plan.pages else 0)
    for i, u in enumerate(plan.elements):
        label = u.ref.label
        if progress is not None and progress(i, steps, label) is False:
            rep.stopped = True
            return rep
        try:
            got = _element_for_update(u, rep, open_element, editor_state, tab_clean)
            if got is None:
                continue
            doc, in_tab, state = got
            if backup:
                backups.backup_file(backup, backup_kind, u.ref.path, once=True)
            if state is not None and editor_state(u.ref) == "dirty":
                # (edited in its tab since it was looked at)
                rep.skipped.append(
                    "%s: it has unsaved changes in the editor (save it there, then update again)" % label
                )
                continue
            report: dict = {}
            read = element_version(doc)
            try:
                doc.save(
                    page_thumbnail=page_thumbnail,
                    refresh=True,
                    expect_stored=True,
                    thumbnail_for=element_thumbnail,
                    every_nested=bool(getattr(u, "compose_all", False)),
                    report=report,
                )
            except ElementChangedMeanwhile:
                rep.skipped.append(
                    "%s: it was saved somewhere else while it was being updated (update again)" % label
                )
                continue
            _note_element_saved(rep, u, doc, report, read, in_tab)
        except Exception as ex:  # noqa: BLE001 -- one element never stops the rest
            _note_exc("genieusage.run_update", element=label)
            rep.failed.append("%s: %s" % (label, getattr(ex, "strerror", None) or ex))
        finally:
            if on_element is not None:
                on_element(rep)
    if rep.elements_saved or rep.elements_in_editor:
        libraries_changed()
    if plan.pages:
        if progress is not None and progress(len(plan.elements), steps, "pages") is False:
            rep.stopped = True
            return rep
        said_stop: list = []

        def each_page(i, n, name):
            if page_progress(i, n, name) is False:
                said_stop.append(int(n))
                return False
            return True

        rep.pages = update_user_pages(
            [u.ref.path for u in plan.pages],
            skip=skip_pages,
            backup=backup,
            builder_open=builder_open,
            progress=each_page if page_progress is not None else None,
            on_record=on_record,
            skip_now=skip_now,
            save_thumbnail=save_thumbnail,
        )
        if said_stop:
            rep.stopped = True
            # (the pages it did not get to: ``n`` = the pages of the step)
            rep.pages.not_checked += max(0, said_stop[0] - len(rep.pages.records))
    return rep


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("genieusage.libraries", _LIB_CACHE, clear=clear_cache, on=("refresh", "tests"))
