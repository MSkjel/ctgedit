"""Alarm indicators on groups and genies: the AI_* keys, frame geometry and bounds."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from .cnum import c_atoi, c_strtoul

KEYS = (
    "AI_ON",
    "AI_EXPR",
    "AI_EQHIER",
    "AI_INCLUDE",
    "AI_INSIDE",
    "AI_WIDTH",
    "AI_PADDING",
    "AI_COLOUR",
    "AI_FLAG_ON",
    "AI_FLAG_LOC",
)
#: the page's value when a key is absent
DEFAULTS: Dict[str, object] = {
    "AI_ON": 0,
    "AI_EXPR": "",
    "AI_EQHIER": 1,
    "AI_INCLUDE": 0,
    "AI_INSIDE": 0,
    "AI_WIDTH": 3,
    "AI_PADDING": 5,
    "AI_COLOUR": 0xBFBFBF,
    "AI_FLAG_ON": 1,
    "AI_FLAG_LOC": 0,
}
#: the Equipment scope combo's texts, index = AI_EQHIER
EQHIER = ("Equipment and Children", "Equipment Only", "Children Only")
#: the Position radios 4019..4026, index = AI_FLAG_LOC
FLAG_LOCATIONS = (
    "Top, Left",
    "Middle, Left",
    "Bottom, Left",
    "Top, Centre",
    "Bottom, Centre",
    "Top, Right",
    "Middle, Right",
    "Bottom, Right",
)
WIDTH_RANGE = (1, 20)
PADDING_RANGE = (0, 100)
EXPR_LIMIT = 254  # pgalmind EXPR
DEFAULT_FLAG = (20, 20)  # CtDraw's placeholder box / entry 0

Rect = Tuple[int, int, int, int]


def props_of(po) -> Dict[str, str]:
    """The record's own property map (CtDraw reads it without ASCII case)
    with UPPER-case keys: the level-0 VALUE bag (strict walk);
        never the %TEMPLATE% / declaration bags."""
    try:
        from . import genie_place as _gp

        r = _gp.decode(po)
        if r.entries:
            out: Dict[str, str] = {}
            for k, v in r.entries[0][1].pairs:
                out.setdefault(k.upper(), v)
            return out
    except Exception:  # noqa: BLE001
        pass
    return {str(k).upper(): v for k, v in (po.raw_properties() or {}).items()}


def settings(props: Dict[str, str]) -> Dict[str, object]:
    """The indicator settings as CtDraw reads them for drawing and Bounds
    from an UPPER-case property map."""
    p = props

    def num(key, mask=None):
        if key not in p:
            return DEFAULTS[key]
        v = c_atoi(p[key])
        return v & mask if mask else v

    return {
        "on": c_atoi(p.get("AI_ON")) != 0,
        "width": num("AI_WIDTH", 0xFFFF),
        "padding": num("AI_PADDING", 0xFFFF),
        "inside": num("AI_INSIDE") != 0,
        "colour": c_strtoul(p["AI_COLOUR"], 0) if "AI_COLOUR" in p else DEFAULTS["AI_COLOUR"],
        "flag_on": ("AI_FLAG_ON" not in p) or c_atoi(p["AI_FLAG_ON"]) != 0,
        "flag_loc": num("AI_FLAG_LOC"),
    }


def frame(U: Rect, s: Dict[str, object]):
    """(outer, inner, strip) of the frame around the children's union ``U``
    : not inside -> inner = U + 1 + pad, outer = inner + W + 1;
        inside -> outer = U, inner = U - (W + 1)."""
    W, pad = int(s["width"]), int(s["padding"])
    strip = W + 1
    if not s["inside"]:
        inner = (U[0] - 1 - pad, U[1] - 1 - pad, U[2] + 1 + pad, U[3] + 1 + pad)
        outer = (inner[0] - strip, inner[1] - strip, inner[2] + strip, inner[3] + strip)
    else:
        outer = tuple(U)
        inner = (U[0] + strip, U[1] + strip, U[2] - strip, U[3] - strip)
    return outer, inner, strip


def flag_xy(loc: int, outer, W: int, w: float, h: float):
    """top-left of the flag's Bounds for AI_FLAG_LOC loc against outer.
    0/1/2 = left edge top/middle/bottom, 3 = top centre, 4 = bottom centre, 5/6/7 = right edge
    top/middle/bottom; the flag centre sits min(w,h)/2 - (W-1) outside the frame. None for an
    unknown location (no flag)."""
    l, t, r, b = outer
    rw, rh = r - l, b - t
    if loc in (0, 7):
        d = min(W + 2 + h * 0.5, rh - 2)
    elif loc in (2, 5):
        d = max(2.0, rh - (W + 2 + h * 0.5))
    elif loc in (1, 6):
        d = rh * 0.5
    elif loc in (3, 4):
        d = rw * 0.5
    else:
        return None
    m = min(h, w) * 0.5 - (W - 1)
    if loc in (0, 1, 2):
        x, y = l - m - w * 0.5, t + d - h * 0.5
    elif loc == 3:
        x, y = r - d - w * 0.5, t - m - h * 0.5
    elif loc == 4:
        x, y = l + d - w * 0.5, b + m - h * 0.5
    else:
        x, y = r + m - w * 0.5, b - d - h * 0.5
    rnd = lambda v: int(v + 0.5) if v >= 0 else int(v - 0.5)  # noqa: E731
    return rnd(x), rnd(y)


def union(rs: Sequence[Rect]) -> Optional[Rect]:
    """Win32 UnionRect over ``rs``: empty rects are ignored."""
    out = None
    for a in rs:
        if a[2] <= a[0] or a[3] <= a[1]:
            continue
        out = (
            tuple(a)
            if out is None
            else (min(out[0], a[0]), min(out[1], a[1]), max(out[2], a[2]), max(out[3], a[3]))
        )
    return out


def grown_bounds(
    U: Rect, s: Dict[str, object], flag_sizes: Sequence[Tuple[float, float]] = (DEFAULT_FLAG,)
) -> Rect:
    """The Bounds of a group whose children's union is ``U`` when its
    indicator is on; U itself when it is off."""
    if not s["on"]:
        return tuple(U)
    W, pad = int(s["width"]), int(s["padding"])
    rect = tuple(U)
    if not s["inside"]:
        g = (W + 2) + pad
        rect = (rect[0] - g, rect[1] - g, rect[2] + g, rect[3] + g)
    if not s["flag_on"]:
        return rect
    acc = None
    for w, h in flag_sizes or (DEFAULT_FLAG,):
        xy = flag_xy(int(s["flag_loc"]), rect, W, w, h)
        box = rect if xy is None else union([rect, (xy[0], xy[1], xy[0] + int(w), xy[1] + int(h))])
        acc = box if acc is None else union([acc, box])
    return acc if acc is not None else rect


# ------------------------------------------------------------------ alarm priorities
_PRIORITY_CACHE: dict = {}


def _rgb_packed(v: int) -> tuple:
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _bases(page_path: str) -> List[str]:
    return [os.path.dirname(os.path.abspath(page_path))] + [
        d for d in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if d
    ]


def _cache_key(page_path: str, tables=("almprior",), own: bool = False) -> tuple:
    """The cache key of a read of ``tables`` for the page: its bases and the (path, mtime,
    size) of the table files that read opens (:func:`_table_stamps`), so a table Studio
    rewrote is read again (the fonts.dbf cache of the reader does the same)."""
    return tuple(os.path.normcase(os.path.abspath(d)) for d in _bases(page_path)) + tuple(
        _table_stamps(page_path, t, own) for t in tables
    )


def _table_stamps(page_path: str, table: str, own: bool = False) -> tuple:
    """((path, mtime_ns, size), ...) of the files :func:`_project_rows` (``own``) or
    :func:`_chain_rows` reads for ``table``, in that order (the same walk: the first base
    that has the table, or is a project, for the own read; the first base whose chain has
    any for the chain read)."""
    from .colourpalette import is_project_dir
    from .projects import include_search_dirs

    def stamp(p):
        try:
            st = os.stat(p)
        except OSError:
            return (os.path.normcase(os.path.abspath(p)), None, None)
        return (os.path.normcase(os.path.abspath(p)), st.st_mtime_ns, st.st_size)

    out = []
    for base in _bases(page_path):
        if own:
            p = _table_file(base, table)
            if p:
                return (stamp(p),)
            if is_project_dir(base):
                return ()
            continue
        for d in [base] + include_search_dirs(base):
            p = _table_file(d, table)
            if p:
                out.append(stamp(p))
        if out:
            break
    return tuple(out)


def _table_file(d: str, table: str) -> Optional[str]:
    for name in (table + ".DBF", table.upper() + ".DBF", table + ".dbf"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def _chain_rows(page_path: str, table: str) -> List[dict]:
    """Rows of ``table`` from the page's project and its include chain."""
    from .projects import include_search_dirs, read_dbf_rows

    rows: list = []
    for base in _bases(page_path):
        for d in [base] + include_search_dirs(base):
            p = _table_file(d, table)
            if p:
                rows += read_dbf_rows(p)
        if rows:
            break
    return rows


def _project_rows(page_path: str, table: str) -> List[dict]:
    """Rows of ``table`` from the page's own project only."""
    from .colourpalette import is_project_dir
    from .projects import read_dbf_rows

    for base in _bases(page_path):
        p = _table_file(base, table)
        if p:
            return read_dbf_rows(p)
        if is_project_dir(base):
            return []
    return []


def priorities(page_path: str, own: bool = False) -> List[tuple]:
    """The project's alarm priorities."""
    key = (("own",) if own else ()) + _cache_key(page_path, ("almprior",), own)
    hit = _PRIORITY_CACHE.get(key)
    if hit is not None:
        return hit
    out: List[tuple] = []
    try:
        for r in (_project_rows if own else _chain_rows)(page_path, "almprior"):
            pr = c_atoi(r.get("PRIORITY"))
            if pr <= 0:
                continue
            v = c_strtoul(r.get("UNACKONFG"), None)
            lib, el = (r.get("LIBNAME") or "").strip(), (r.get("LIBOBJNAME") or "").strip()
            out.append((pr, _rgb_packed(v) if v is not None else None, (lib, el) if lib and el else None))
        out.sort(key=lambda t: t[0])
    except Exception:  # noqa: BLE001 -- editor chrome only
        out = []
    # (another thread may have read them meanwhile: every caller shares the first list)
    return _PRIORITY_CACHE.setdefault(key, out)


def priority_details(page_path: str) -> Dict[int, dict]:
    """{PRIORITY: {'short': SHORTNAME, 'name': DISPNAME, 'flag': (LIBNAME, LIBOBJNAME) | None,
    'thumbnail': (LIBNAME, THUMBNAIL) | None}} from the same almprior.DBF rows as
    :func:`priorities` (the runtime's alarm indicator labels its flag with SHORTNAME; an SA
    alarm list's PriorityAndState column draws the THUMBNAIL element)."""
    key = ("details",) + _cache_key(page_path, ("almprior",))
    hit = _PRIORITY_CACHE.get(key)
    if hit is not None:
        return hit
    out: Dict[int, dict] = {}
    try:
        for r in _chain_rows(page_path, "almprior"):
            pr = c_atoi(r.get("PRIORITY"))
            if pr <= 0 or pr in out:
                continue
            lib = (r.get("LIBNAME") or "").strip()
            el, th = (r.get("LIBOBJNAME") or "").strip(), (r.get("THUMBNAIL") or "").strip()
            out[pr] = {
                "short": (r.get("SHORTNAME") or "").strip(),
                "name": (r.get("DISPNAME") or "").strip(),
                "flag": (lib, el) if lib and el else None,
                "thumbnail": (lib, th) if lib and th else None,
            }
    except Exception:  # noqa: BLE001 -- simulator chrome only
        out = {}
    return _PRIORITY_CACHE.setdefault(key, out)


def top_priority(page_path: str, own: bool = False):
    """(fill rgb, (libname, libobjname) | None) of the HIGHEST alarm priority
    (lowest PRIORITY number; UNACKONFG failure -> red); ``own`` as in
    :func:`priorities`."""
    rows = priorities(page_path, own)
    if not rows:
        return (255, 0, 0), None
    _pr, fill, ref = rows[0]
    return (fill if fill is not None else (255, 0, 0)), ref


#: the buckets of CtDraw's flag set (a helper(0x10, ..): 8 lo / hi pairs; the 9th
#: pair rehashes it)
_FLAG_BUCKETS = 8


def _fnv1a(s: str) -> int:
    from .textcodec import enc

    h = 0x811C9DC5
    for c in enc(s):
        h = ((c ^ h) * 0x01000193) & 0xFFFFFFFF
    return h


def flag_refs(page_path: str) -> List[Tuple[str, str]]:
    """(LIBNAME, LIBOBJNAME) of every flag genie CtDraw measures, in its order."""
    key = ("flags",) + _cache_key(page_path, ("almprior", "almmodes"), own=True)
    hit = _PRIORITY_CACHE.get(key)
    if hit is not None:
        return hit
    pairs: List[Tuple[str, str]] = []
    try:
        from .textcodec import enc

        def pair(r):
            return ((r.get("LIBNAME") or "").strip(), (r.get("LIBOBJNAME") or "").strip())

        def same(a, b):  # without case: ASCII letters only
            return enc(a[0]).lower() == enc(b[0]).lower() and enc(a[1]).lower() == enc(b[1]).lower()

        rows = _project_rows(page_path, "almprior")
        prio = {}
        for r in rows:
            if c_atoi(r.get("PRIORITY")) > 0:
                prio[c_atoi(r.get("PRIORITY"))] = pair(r)
        modes = {}
        for r in _project_rows(page_path, "almmodes") if rows else ():
            modes[(r.get("DISPNAME") or "").strip()] = pair(r)
        lst: List[Tuple[str, str]] = []
        buckets: Dict[int, List[Tuple[str, str]]] = {}  # in list order
        for q in [prio[k] for k in sorted(prio)] + [modes[k] for k in sorted(modes, key=enc)]:
            if len(lst) >= _FLAG_BUCKETS:  # rehashed: not decoded
                if not any(same(q, o) for o in lst):
                    lst.append(q)
                continue
            b = (((_fnv1a(q[1]) * 2) & 0xFFFFFFFF) ^ _fnv1a(q[0])) & (_FLAG_BUCKETS - 1)
            mine = buckets.setdefault(b, [])
            if any(same(q, o) for o in mine):
                continue
            lst.insert(lst.index(mine[0]) if mine else len(lst), q)
            mine.insert(0, q)
        pairs = [q for q in lst if q[0] and q[1]]
    except Exception:  # noqa: BLE001 -- the default flag box still applies
        from ..infra.logsetup import note_exception

        note_exception("alarmind.flag_refs", page=page_path)
        pairs = []
    return _PRIORITY_CACHE.setdefault(key, pairs)


def flag_sizes(page_path: str, resolver=None) -> List[Tuple[int, int]]:
    """The flag sizes the group bounds grow by."""
    out = [DEFAULT_FLAG]
    if resolver is None:
        return out
    from .symbols import SymbolReference

    for lib, el in flag_refs(page_path):
        try:
            res = resolver.resolve(SymbolReference(lib, el, "ctm", "genie"), strict=True)
        except Exception:  # noqa: BLE001
            res = None
        if res is not None and (res.width or 0) > 0 and (res.height or 0) > 0:
            out.append((int(res.width), int(res.height)))
    return out


# ------------------------------------------------------------------ the page's apply
def page_values(props: Dict[str, str]) -> Dict[str, object]:
    """What the Alarm Indicator page shows for an UPPER-case property map
    : has, expr, eqhier (0..2), include, inside, width,
        padding, colour, flag_on, flag_loc (0..7)."""
    p = props
    s = settings(p)
    eq = c_atoi(p["AI_EQHIER"]) if "AI_EQHIER" in p else DEFAULTS["AI_EQHIER"]
    if not 0 <= eq < 3:
        eq = DEFAULTS["AI_EQHIER"]
    loc = int(s["flag_loc"])
    if not 0 <= loc < len(FLAG_LOCATIONS):
        loc = 0
    return {
        "has": s["on"],
        "expr": p.get("AI_EXPR", "") or "",
        "eqhier": eq,
        "include": c_atoi(p.get("AI_INCLUDE")) != 0,
        "inside": bool(s["inside"]),
        "width": int(s["width"]),
        "padding": int(s["padding"]),
        "colour": int(s["colour"]) & 0xFFFFFFFF,
        "flag_on": bool(s["flag_on"]),
        "flag_loc": loc,
    }


def apply_edits(values: Dict[str, object]) -> List[Tuple[str, Optional[str]]]:
    """The (key, value or None = remove) writes of the page's apply
    for the page values (see :func:`page_values`)."""
    out: List[Tuple[str, Optional[str]]] = [
        ("AI_ON", "%d" % (1 if values["has"] else 0)),
        ("AI_EXPR", str(values.get("expr") or "") or None),
        ("AI_EQHIER", "%d" % int(values["eqhier"])),
        ("AI_INCLUDE", "%d" % (1 if values["include"] else 0)),
        ("AI_INSIDE", "%d" % (1 if values["inside"] else 0)),
        ("AI_WIDTH", "%d" % int(values["width"])),
    ]
    if not values["inside"]:
        out.append(("AI_PADDING", "%d" % int(values["padding"])))
    out += [
        ("AI_COLOUR", "%d" % (int(values["colour"]) & 0xFFFFFFFF)),
        ("AI_FLAG_ON", "%d" % (1 if values["flag_on"] else 0)),
        ("AI_FLAG_LOC", "%d" % int(values["flag_loc"])),
    ]
    return out


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("alarmind.priorities", _PRIORITY_CACHE, on=("libraries", "refresh", "tests"))
