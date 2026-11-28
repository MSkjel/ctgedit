"""What a page open found, for the activity log, status bar and reports."""

from __future__ import annotations

from ..infra.paths import lib_path_dirs, path_key


import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model.document import ObjectKind
from ..infra.logsetup import note_exception as _note_exc

#: link extension -> what the log calls it
KIND_OF_EXT = {"ctm": "genie", "ctl": "symbol", "ctt": "template"}
REASON_NO_LIBRARY = "library not found on the search path"
REASON_NO_ELEMENT = "element not in the library"
REASON_UNREADABLE = "element could not be read (the log says why)"


@dataclass
class Unresolved:
    library: str
    element: str
    ext: str
    reason: str
    count: int = 1
    first_index: int = -1
    files: List[str] = field(default_factory=list)  # the library files searched

    @property
    def reference(self) -> str:
        return "%s.%s" % (self.library, self.element)

    def text(self) -> str:
        return "%s (.%s, %s%s)" % (
            self.reference,
            self.ext,
            self.reason,
            ", %d objects" % self.count if self.count > 1 else "",
        )


@dataclass
class PageReport:
    path: str
    objects: int = 0
    placed: Dict[str, int] = field(default_factory=dict)  # 'genie' / 'symbol' / ...
    composites: int = 0
    unresolved: List[Unresolved] = field(default_factory=list)
    no_art: List[Unresolved] = field(default_factory=list)  # resolved, nothing drawn
    libraries: List[Tuple[str, List[str]]] = field(default_factory=list)  # key, paths
    search_paths: List[str] = field(default_factory=list)
    includes: List[Tuple[str, str, List[str]]] = field(default_factory=list)
    #: library files used from a folder OUTSIDE the page's project, CTG_LIB_PATH and include
    #: chain (the resolver's sibling-project fallback: it differs between machines)
    outside: List[str] = field(default_factory=list)
    resolver: bool = True
    serial: int = -1  # derive_serial it describes

    def counts_text(self) -> str:
        parts = ["%d %s%s" % (n, k, "s" if n != 1 else "") for k, n in sorted(self.placed.items()) if n]
        if self.composites:
            parts.append("%d composite genie%s" % (self.composites, "s" if self.composites != 1 else ""))
        return ", ".join(parts) or "no library links"

    def unresolved_text(self, limit: int = 10) -> str:
        if not self.unresolved:
            return "every library reference resolves"
        by: Dict[str, int] = {}
        for u in self.unresolved:
            by[u.reason] = by.get(u.reason, 0) + 1
        head = "%d reference(s) not found [%s]" % (
            len(self.unresolved),
            ", ".join("%d %s" % (n, r) for r, n in sorted(by.items())),
        )
        items = "; ".join(u.text() for u in self.unresolved[:limit])
        more = " ... %d more" % (len(self.unresolved) - limit) if len(self.unresolved) > limit else ""
        return "%s: %s%s" % (head, items, more)

    def search_text(self) -> str:
        n_inc = sum(1 for _n, _v, dirs in self.includes for _d in dirs)
        missing = [n for n, _v, dirs in self.includes if not dirs]
        out = "search path %d folders (%d from the include chain)" % (len(self.search_paths), n_inc)
        if missing:
            out += "; included projects NOT FOUND: %s" % ", ".join(missing)
        if self.outside:
            out += "; %d library file(s) from outside the include chain" % len(self.outside)
        return out

    def detail_lines(self) -> List[str]:
        """The per-open log's detail lines (include chain, libraries used)."""
        out = []
        if self.includes:
            inc = []
            for name, via, dirs in self.includes:
                inc.append(
                    "%s -> %s" % (name, " + ".join(dirs) if dirs else "NOT FOUND (included by %s)" % via)
                )
            out.append("included projects: " + "; ".join(inc))
        if self.libraries:
            outside = {os.path.normcase(p) for p in self.outside}

            def where(p: str) -> str:
                return p + (" [not in the include chain]" if os.path.normcase(p) in outside else "")

            out.append(
                "libraries used: "
                + "; ".join("%s <- %s" % (k, " + ".join(where(x) for x in p)) for k, p in self.libraries)
            )
        for u in self.unresolved:
            if u.files:
                out.append("%s: searched %s" % (u.reference, " + ".join(u.files)))
        if self.no_art:
            out.append("resolved but nothing drawn: " + "; ".join(u.text() for u in self.no_art[:10]))
        return out


def _reference(po, obj):
    """The library reference the reader resolves for record ``po`` (its
    dwFlags 0x1 link, else the SYMBOL bag value -- symbols.reference_from_object)."""
    from ..format.symbols import reference_from_body, reference_from_symbol_value

    if po.subtype_size & 0x1:
        ref = reference_from_body(po.body_raw)
        if ref is not None:
            return ref
    props = obj.raw.get("_props") if obj is not None else None
    if props is None:
        try:
            props = po.raw_properties()
        except Exception:  # noqa: BLE001
            _note_exc("open_report._reference")
            props = {}
    sym = props.get("SYMBOL") if props else None
    return reference_from_symbol_value(sym) if sym else None


def _verdict(resolver, ref) -> Tuple[str, List[str]]:
    """'' when ``ref`` resolves, else (reason, library files searched)."""
    try:
        if resolver.resolve(ref) is not None:
            return "", []
    except Exception:  # noqa: BLE001
        _note_exc("open_report._verdict", element=ref.element)
    from ..format.symbols import LIBRARY_EXTENSIONS

    files: List[str] = []
    readable_missing = False
    for ext in [ref.ext] + [e for e in LIBRARY_EXTENSIONS if e != ref.ext]:
        try:
            libs = resolver.libraries(ref.library, ext)
        except Exception:  # noqa: BLE001
            _note_exc("open_report._verdict", library=ref.library)
            libs = []
        for lib in libs:
            files.append(lib.path)
            try:
                if lib.has_element(ref.element):
                    readable_missing = True
            except Exception:  # noqa: BLE001
                _note_exc("open_report._verdict", element=ref.element)
    if not files:
        return REASON_NO_LIBRARY, []
    return (REASON_UNREADABLE if readable_missing else REASON_NO_ELEMENT), files


def include_chain(project_dir: str) -> List[Tuple[str, str, List[str]]]:
    """[(project name, included by, its folders)] in the resolver's order
    (format.projects.include_search_dirs), including the names that resolve
    to NO folder (empty list) -- the resolver skips those silently."""
    from ..format import projects as P

    project_dir = os.path.abspath(project_dir)
    user_dir = os.path.dirname(project_dir)
    user_dirs = [user_dir] + P.sibling_install_user_dirs(user_dir)
    masters: dict = {}
    out: List[Tuple[str, str, List[str]]] = []
    seen = {os.path.normcase(project_dir)}
    named = set()

    def add(name: str, via: str) -> None:
        dirs = P.resolve_project_dirs(name, user_dirs, masters)
        fresh = []
        for p in dirs:
            key = path_key(p)
            if key in seen:
                continue
            seen.add(key)
            fresh.append(p)
        if not dirs and name.lower() not in named:
            out.append((name, via, []))
        named.add(name.lower())
        if fresh:
            out.append((name, via, fresh))
            for p in fresh:
                walk(p)

    def walk(d: str) -> None:
        for name in P.included_project_names(d):
            add(name, os.path.basename(d))

    walk(project_dir)
    for name in P.IMPLICIT_INCLUDE_PROJECTS:
        add(name, "(always included)")
    return out


def page_report(doc) -> PageReport:
    """The report of document ``doc``'s page as derived now."""
    rep = PageReport(path=getattr(doc, "path", ""))
    page = getattr(doc, "page", None)
    parsed = getattr(doc, "parsed", None)
    records = list(parsed.page.objects) if parsed is not None and parsed.page else []
    objs = list(page.objects) if page is not None else []
    rep.objects = len(objs)
    rep.serial = getattr(doc, "derive_serial", -1)
    der = getattr(doc, "_derivation", None)
    resolver = getattr(der, "resolver", None) if der is not None else None
    rep.resolver = resolver is not None
    if resolver is not None:
        rep.search_paths = list(getattr(resolver, "search_paths", []) or [])
        cache = getattr(resolver, "_libs_cache", {}) or {}
        rep.libraries = sorted((k, [lib.path for lib in libs]) for k, libs in cache.items() if libs)
    pdir = os.path.dirname(os.path.abspath(rep.path))
    try:
        rep.includes = include_chain(pdir)
    except Exception:  # noqa: BLE001 -- a report only
        _note_exc("open_report.page_report", project=pdir)
        rep.includes = []
    chain = [pdir] + lib_path_dirs() + [d for _n, _v, dirs in rep.includes for d in dirs]
    roots = [path_key(d).rstrip("\\/") + os.sep for d in chain]
    rep.outside = [
        p for _k, paths in rep.libraries for p in paths if not any(path_key(p).startswith(r) for r in roots)
    ]
    seen: Dict[tuple, Unresolved] = {}
    no_art: Dict[tuple, Unresolved] = {}
    verdicts: Dict[tuple, Tuple[str, List[str]]] = {}
    for i, po in enumerate(records):
        o = objs[i] if i < len(objs) else None
        if o is not None and (o.kind is ObjectKind.COMPOSITE_GENIE or o.raw.get("_in_composite")):
            if o.kind is ObjectKind.COMPOSITE_GENIE:
                rep.composites += 1
            continue
        try:
            ref = _reference(po, o)
        except Exception:  # noqa: BLE001
            _note_exc("open_report.page_report")
            ref = None
        if ref is None or "_compositioncaches" in ref.library.lower():
            continue
        kind = KIND_OF_EXT.get(ref.ext, ref.ext)
        rep.placed[kind] = rep.placed.get(kind, 0) + 1
        if resolver is None:
            continue
        key = (ref.library.lower(), ref.element.lower(), ref.ext)
        if key not in verdicts:
            verdicts[key] = _verdict(resolver, ref)
        reason, files = verdicts[key]
        if reason:
            u = seen.get(key)
            if u is None:
                seen[key] = Unresolved(ref.library, ref.element, ref.ext, reason, first_index=i, files=files)
            else:
                u.count += 1
            continue
        # resolved: a LEAF placement (no inline members) whose art the reader
        # did not attach -- nothing is drawn where the element is
        if (
            o is not None
            and not po.class_flag
            and o.kind in (ObjectKind.SYMBOL,)
            and not o.children
            and not o.raw.get("_embedded_bmp")
            and not o.raw.get("_non_drawable")
        ):
            u = no_art.get(key)
            if u is None:
                no_art[key] = Unresolved(
                    ref.library, ref.element, ref.ext, "resolved, but no art was drawn", first_index=i
                )
            else:
                u.count += 1
    rep.unresolved = sorted(seen.values(), key=lambda u: u.first_index)
    rep.no_art = sorted(no_art.values(), key=lambda u: u.first_index)
    return rep


def report_for(doc) -> Optional[PageReport]:
    """``doc``'s report, computed again only when the page was derived again."""
    if doc is None or getattr(doc, "parsed", None) is None:
        return None
    rep = getattr(getattr(doc, "ui", None), "diag_report", None)
    if rep is not None and rep.serial == getattr(doc, "derive_serial", -1):
        return rep
    try:
        rep = page_report(doc)
    except Exception:  # noqa: BLE001 -- a report only
        from ..infra import logsetup

        logsetup.note_exception("diag.page_report", page=getattr(doc, "path", ""))
        return None
    doc.ui.diag_report = rep
    return rep


def open_log_text(
    doc,
    rep: PageReport,
    *,
    open_s: Optional[float] = None,
    swallowed: Optional[dict] = None,
    verb: str = "opened",
    derive_s: Optional[float] = None,
) -> str:
    """The per-open activity-log entry (first line + detail lines). ``derive_s``
    = the open's derive time (default: the document's last derive)."""
    from ..infra import logsetup

    if derive_s is None:
        derive_s = getattr(doc, "_derive_seconds", None)
    timing = []
    if derive_s is not None:
        timing.append("derived in %.2f s" % derive_s)
    if open_s is not None:
        timing.append("open %.2f s" % open_s)
    dirty = bool(getattr(doc, "dirty", False))
    labels = logsetup.undo_labels(doc, 12)
    state = (
        (
            "unsaved changes after the open checks (%s)"
            % (", ".join("'%s'" % lab for lab in labels) or "no undo step")
        )
        if dirty
        else "unmodified"
    )
    head = "%s %s: %d objects; links: %s; %s; %s; %s; swallowed errors: %s; %s" % (
        verb,
        rep.path,
        rep.objects,
        rep.counts_text(),
        ", ".join(timing) or "timing n/a",
        rep.unresolved_text(),
        rep.search_text(),
        logsetup.format_counts(swallowed) if swallowed else "none",
        state,
    )
    if not rep.resolver:
        head += " (symbol resolution is off: CTG_NO_SYMBOLS=1)"
    return "\n    ".join([head] + rep.detail_lines())


# ---------------------------------------------------------------------------
# Automatic changes of the open checks


def art_count(page) -> int:
    """Objects that draw a picture or resolved library art."""
    n = 0
    for o in list(getattr(page, "objects", None) or ()):
        if o.raw.get("_embedded_bmp") or o.children:
            n += 1
    return n


def change_snapshot(doc) -> dict:
    """What :func:`describe_change` compares against (taken just before an
    automatic step)."""
    recs = list(doc.parsed.page.objects) if doc.parsed and doc.parsed.page else []
    return {
        "records": recs,
        "sigs": {id(po): _sig(po) for po in recs},
        "art": art_count(doc.page),
        "undo": len(getattr(doc, "_undo", []) or []),
        "dirty": bool(getattr(doc, "dirty", False)),
    }


def _sig(po) -> int:
    body = po.body_raw
    return hash(
        (
            po.class_flag,
            po.type_code,
            po.subtype_size,
            po.an_str,
            body if type(body) is bytes else bytes(body),
        )
    )


def describe_change(doc, snap: dict) -> dict:
    """{'changed': records changed / added / removed, 'before', 'after' (record
    counts), 'art_before', 'art_after', 'label': the new undo step's label or
    None, 'dirty'} of the step since ``snap``."""
    recs = list(doc.parsed.page.objects) if doc.parsed and doc.parsed.page else []
    old = snap["sigs"]
    now = {id(po) for po in recs}
    changed = sum(1 for po in recs if old.get(id(po)) != _sig(po))
    changed += sum(1 for po in snap["records"] if id(po) not in now)
    undo = list(getattr(doc, "_undo", []) or [])
    label = getattr(undo[-1], "label", None) if len(undo) > snap["undo"] else None
    return {
        "changed": changed,
        "before": len(snap["records"]),
        "after": len(recs),
        "art_before": snap["art"],
        "art_after": art_count(doc.page),
        "label": label,
        "dirty": bool(getattr(doc, "dirty", False)),
    }
