"""Find's index of the closed pages: the searchable fields of each page a search of the
closed pages read (workers/find_worker), kept on disk with what they were read from. A later
search, whatever its text and options, matches the kept fields of a page that is as it was
(:class:`KeptSearch`, in the editor's process: no record is parsed there) and sends only the
other pages to a helper process.

A page's kept fields hold while its file, every library file its read opened, what each
library lookup found on its search path (a library of the same name put nearer changes it),
the page's search path itself, the ctgedit switches (CTG_* / GP_*) and the editor's code are
as they were. Its pages.DBF row and page keyboard commands are read at each search
(find_replace.closed_page_rows)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zlib
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from ..infra.logsetup import note_exception as _note_exc
from ..infra.paths import data_dir, path_key
from . import find_replace as FR

FORMAT = "ctgedit-find-index/1"
#: the index's folder (infra/paths.data_dir)
FOLDER = "find_index"
#: the index keeps this many pages at most (the least recently written go first)
KEEP_PAGES = 3000

_code: Optional[str] = None


def code_stamp() -> str:
    """The editor's code as a digest of its files' names, sizes and times (a page kept by
    another build is read again), worked out once a process."""
    global _code
    if _code is None:
        h = hashlib.sha1()
        if getattr(sys, "frozen", False):
            st = os.stat(sys.executable)
            h.update(("%s|%d|%d" % (sys.executable, st.st_size, st.st_mtime_ns)).encode())
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for d, subs, files in os.walk(root):
            subs[:] = sorted(s for s in subs if s != "__pycache__")
            for f in sorted(files):
                if f.endswith((".py", ".pyd")):
                    p = os.path.join(d, f)
                    st = os.stat(p)
                    h.update(("%s|%d|%d\n" % (os.path.relpath(p, root), st.st_size, st.st_mtime_ns)).encode())
        _code = h.hexdigest()[:20]
    return _code


def _environment() -> list:
    from ..infra.workers import environment_signature

    return [list(kv) for kv in environment_signature()]


def index_file(page: str) -> str:
    """The file the index keeps the page at ``page`` in."""
    name = hashlib.sha1(path_key(page).encode("utf-8")).hexdigest()[:24]
    return os.path.join(data_dir(FOLDER), name + ".json.z")


def _page_search_path(page: str) -> List[str]:
    """The folders the page's read looks for libraries in (symbols.SymbolResolver.for_page_env)."""
    from ..format.symbols import SymbolResolver

    res = SymbolResolver.for_page_env(page)
    return list(res.search_paths) if res is not None else []


def _lower(v: str) -> str:
    """``v`` in lower case as find_replace.match_spans compares it."""
    low = v.lower()
    if len(low) != len(v):
        low = "".join(c.lower() if len(c.lower()) == 1 else c for c in v)
    return low


# --------------------------------------------------------------------------- the kept form
def kept_form(doc, page: str, stamp: Tuple[int, int], hold) -> dict:
    """What the index keeps of the page ``doc`` read from ``page`` (its file at ``stamp`` =
    (mtime_ns, size)); ``hold``: the symbols.LibraryHold the read ran in."""
    pf = FR.page_fields(doc)
    records: Dict[int, list] = {}
    params: Dict[int, list] = {}

    def record(a: int) -> None:
        if a not in records:
            name, an = pf.names(a)
            records[a] = [name, an, pf.link(a), int(pf.linked(a)), int(pf.composite(a))]

    objects, labels, hay = [], {}, []
    from .object_labels import object_label

    for e in pf.entries:
        for a in (e.index,) + e.anc:
            record(a)
        # (the parameter rows of a genie, and those its members' routes read)
        for x in ([e.index] if e.root else []) + ([e.outer] if e.outer is not None else []):
            record(x)
            if x not in params:
                params[x] = [[k, v] for k, v in pf.params(x).items()]
                hay.extend(_lower(v) for _k, v in params[x])
        fields = []
        member = e.outer is not None and not pf.composite(e.outer)
        for slot, (scope, kind, key, value, label) in enumerate(pf.fields(e, True)):
            traced = None
            if member:
                tkey = "TXT_OFF" if kind == FR.K_CAPTION else key
                t = pf.traced(e, slot, tkey) if tkey else frozenset()
                traced = sorted(t) if t is not None else None
            fields.append([scope, kind, key, value, label, traced])
            hay.append(_lower(value))
        flags = (
            (1 if e.inside else 0) | (2 if e.template else 0) | (4 if e.locked else 0) | (8 if e.root else 0)
        )
        objects.append([e.index, list(e.anc), flags, -1 if e.outer is None else e.outer, e.level, fields])
        labels[e.index] = object_label(doc, e.index)
    assocs = [list(a) for a in pf.associations()]
    for a in assocs:
        hay.extend(_lower(v) for v in a[1:] if v)
    libs = sorted(
        [lib.path, int(lib.version[0]), int(lib.version[1])] for lib in hold.pins.values() if lib.version
    )
    paths: Dict[Tuple[str, ...], int] = {}
    found = []
    for (sp, key), files in sorted(hold.found.items()):
        k = paths.setdefault(sp, len(paths))
        found.append([k, key, list(files)])
    return {
        "format": FORMAT,
        "code": code_stamp(),
        "env": _environment(),
        "path": page,
        "page": [int(stamp[0]), int(stamp[1])],
        "search": _page_search_path(page),
        "libs": libs,
        "search_paths": [list(sp) for sp in paths],
        "found": found,
        "hay": "\n".join(hay),
        "objects": objects,
        "records": {str(a): r for a, r in records.items()},
        "params": {str(a): p for a, p in params.items()},
        "labels": {str(i): s for i, s in labels.items()},
        "assocs": assocs,
    }


def write(form: dict) -> None:
    """Keep ``form`` (:func:`kept_form`) in the index (noted when it cannot be written)."""
    from ..format.fileio import write_atomic

    path = index_file(form["path"])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, zlib.compress(json.dumps(form, separators=(",", ":")).encode("utf-8"), 6))
    except OSError:
        _note_exc("find_index.write", page=form.get("path"))


def trim(keep: int = KEEP_PAGES) -> None:
    """Remove the pages written longest ago past the first ``keep``."""
    folder = data_dir(FOLDER)
    try:
        items = [(e.stat().st_mtime_ns, e.path) for e in os.scandir(folder) if e.name.endswith(".json.z")]
    except OSError:  # swallow-ok: no index yet
        return
    for _t, p in sorted(items, reverse=True)[keep:]:
        try:
            os.remove(p)
        except OSError:  # swallow-ok: in use; the next trim takes it
            pass


def _read(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as fh:
            form = json.loads(zlib.decompress(fh.read()).decode("utf-8"))
    except OSError, ValueError, zlib.error:  # swallow-ok: none kept, or a damaged file: read again
        return None
    return form if isinstance(form, dict) and form.get("format") == FORMAT else None


class KeptFields:
    """A page's kept fields as find_replace.match_fields reads them (find_replace.PageFields
    of a closed page)."""

    def __init__(self, form: dict):
        self.page_name = FR.page_name_of(form["path"])
        self._records = {int(a): r for a, r in form["records"].items()}
        self._params = {int(a): dict((k, v) for k, v in p) for a, p in form["params"].items()}
        self._labels = {int(i): s for i, s in form["labels"].items()}
        self._assocs = [tuple(a) for a in form["assocs"]]
        self.entries: List[FR.ObjectEntry] = []
        self._fields: Dict[int, tuple] = {}
        # (a genie member's: the parameters each field is substituted from)
        self._traced: Dict[int, tuple] = {}
        # (the scopes, kinds, keys and labels repeat: one string each, shared)
        share: Dict[str, str] = {}
        one = share.setdefault
        sets: Dict[tuple, frozenset] = {}
        for i, anc, flags, outer, level, fields in form["objects"]:
            self.entries.append(
                FR.ObjectEntry(
                    i,
                    None,
                    tuple(anc),
                    bool(flags & 1),
                    bool(flags & 2),
                    bool(flags & 4),
                    bool(flags & 8),
                    None if outer < 0 else outer,
                    level,
                )
            )
            self._fields[i] = tuple(
                (one(s, s), one(k, k), one(key, key), v, one(lb, lb)) for s, k, key, v, lb, _t in fields
            )
            if outer >= 0:
                self._traced[i] = tuple(None if f[5] is None else self._set(sets, f[5]) for f in fields)

    @staticmethod
    def _set(sets: dict, names: list) -> frozenset:
        """frozenset(``names``), one object for each set (most fields trace the same few)."""
        k = tuple(names)
        got = sets.get(k)
        if got is None:
            got = sets[k] = frozenset(names)
        return got

    def fields(self, e, with_text: bool = True) -> tuple:
        return self._fields[e.index]

    def traced(self, e, slot: int, tkey: str):
        return self._traced[e.index][slot]

    def params(self, i: int) -> Dict[str, str]:
        return self._params.get(i, {})

    def names(self, a: int) -> Tuple[str, ...]:
        r = self._records.get(a)
        return (r[0], r[1]) if r is not None else ("", "")

    def linked(self, a: int) -> bool:
        r = self._records.get(a)
        return bool(r is not None and r[3])

    def link(self, a: int) -> str:
        r = self._records.get(a)
        return r[2] if r is not None else ""

    def composite(self, a: int) -> bool:
        r = self._records.get(a)
        return bool(r is not None and r[4])

    def associations(self) -> list:
        return list(self._assocs)

    def label(self, i: int) -> str:
        return self._labels.get(i, "?")


class _NoFields:
    """A kept page whose fields cannot hold the text: its page rows only."""

    entries: list = []

    def __init__(self, page: str):
        self.page_name = FR.page_name_of(page)

    def associations(self) -> list:
        return []


def rows_of(page: str, pf, matches) -> List[dict]:
    """The Find dialog's rows (find_worker's answer rows) of the ``matches``
    (find_replace.match_fields) of ``page``, whose kept fields ``pf`` label its objects."""
    name = FR.page_name_of(page)
    project = os.path.basename(os.path.dirname(page))
    return [
        {
            "page": page,
            "name": name,
            "project": project,
            "index": i,
            "object": pf.label(i) if i >= 0 else "Page",
            "scope": scope,
            "kind": kind,
            "key": key,
            "value": value,
            "label": label,
            "reason": reason,
            "params": list(params),
            "sub": sub,
        }
        for i, scope, kind, key, value, label, reason, _g, params, sub in matches
    ]


def page_rows(page: str, form: dict, opts) -> List[dict]:
    """The rows of a search ``opts`` of the kept ``form`` of ``page`` (the helper's answer:
    matched as a later search from the index matches them)."""
    pf = KeptFields(form)
    return rows_of(page, pf, FR.match_fields(pf, opts, lambda: FR.closed_page_rows(page)))


# --------------------------------------------------------------------------- searching
#: the index files read: path key -> (the index file's (mtime_ns, size), the form without its
#: fields, the KeptFields once a search needed them or None), least recently used first
_KEPT: "OrderedDict[str, list]" = OrderedDict()
#: the KeptFields kept in memory at most (the forms' heads and texts stay)
KEEP_FIELDS = 64


class KeptSearch:
    """One search of the closed pages' kept fields: :meth:`rows` of each page, None for a
    page not kept as it is now (the helper reads it)."""

    def __init__(self, opts):
        self.opts = opts
        text = opts.text or ""
        # the text every hit's field holds in lower case (find_replace.match_spans)
        self.needle = FR.required_text(text, unicode=True) if opts.regex else text.lower()
        self._stats: Dict[str, Optional[tuple]] = {}
        self._search: Dict[str, List[str]] = {}
        self._found: Dict[Tuple[Tuple[str, ...], str], List[str]] = {}
        self._env = _environment()
        self._code = code_stamp()

    def _stat(self, path: str) -> Optional[tuple]:
        k = path_key(path)
        if k not in self._stats:
            try:
                st = os.stat(path)
                self._stats[k] = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._stats[k] = None
        return self._stats[k]

    def _current(self, page: str, head: dict) -> bool:
        """The kept ``head`` of ``page`` is as the page and its libraries are now."""
        if head.get("code") != self._code or head.get("env") != self._env:
            return False
        st = self._stat(page)
        if st is None or list(st) != head.get("page"):
            return False
        d = os.path.dirname(os.path.abspath(page))
        if d not in self._search:
            try:
                self._search[d] = _page_search_path(page)
            except Exception:  # noqa: BLE001
                _note_exc("find_index.KeptSearch._current", page=page)
                self._search[d] = None
        if self._search[d] is None or self._search[d] != head.get("search"):
            return False
        for p, mt, size in head.get("libs") or ():
            if self._stat(p) != (mt, size):
                return False
        from ..format.symbols import SymbolResolver

        sps = head.get("search_paths") or []
        for k, key, files in head.get("found") or ():
            sp = tuple(sps[k])
            fk = (sp, key)
            if fk not in self._found:
                lib, _dot, ext = key.rpartition(".")
                self._found[fk] = [path_key(p) for p in SymbolResolver(list(sp)).library_files(lib, ext)]
            if self._found[fk] != files:
                return False
        return True

    def _entry(self, page: str):
        """([stamp, head, fields], the whole form when it was read now) of the page's index
        file; (None, None) when none is kept."""
        pk = path_key(page)
        f = index_file(page)
        try:
            st = os.stat(f)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            _KEPT.pop(pk, None)
            return None, None
        got = _KEPT.get(pk)
        if got is not None and got[0] == stamp:
            _KEPT.move_to_end(pk)
            return got, None
        form = _read(f)
        if form is None or path_key(form.get("path") or "") != pk:
            _KEPT.pop(pk, None)
            return None, None
        got = [stamp, {k: v for k, v in form.items() if k not in _BODY}, None]
        _KEPT[pk] = got
        return got, form

    @staticmethod
    def _keep_fields(got: list, form: dict) -> None:
        got[2] = KeptFields(form)
        held = [v for v in _KEPT.values() if v[2] is not None]
        for v in held[: max(0, len(held) - KEEP_FIELDS)]:
            v[2] = None

    def rows(self, page: str) -> Optional[List[dict]]:
        """The rows of ``page`` from its kept fields; None when it is not kept as it is now."""
        got, form = self._entry(page)
        if got is None or not self._current(page, got[1]):
            return None
        if self.needle and self.needle not in (got[1].get("hay") or ""):
            # (no field holds the text: the page's own rows still may)
            nf = _NoFields(page)
            return rows_of(page, nf, FR.match_fields(nf, self.opts, lambda: FR.closed_page_rows(page)))
        if got[2] is None:
            form = form if form is not None else _read(index_file(page))
            if form is None:
                return None
            self._keep_fields(got, form)
        pf = got[2]
        return rows_of(page, pf, FR.match_fields(pf, self.opts, lambda: FR.closed_page_rows(page)))


#: the parts of a kept form only KeptFields reads (the rest stays in memory: its checks and text)
_BODY = ("objects", "records", "params", "labels", "assocs")


from ..infra import caches as _caches  # noqa: E402

# (each entry is checked against its file at every search)
_caches.register("find_index.kept", _KEPT, on=("tests",))
