"""Edit object geometry (anchor, rects, nodes) on the raw record bytes."""

from __future__ import annotations

import copy
import importlib
import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .page import PageObject, _XFORM_DOUBLES, _geom_name_len
from .textcodec import CODEC
from . import codes as _codes
from ..infra.logsetup import note_exception as _note_exc

# Modules used by the per-record paths, imported on first use and then KEPT: placement and
# legacy import this module at load (a module-level import here would be circular), and a
# function-level ``from . import x`` runs the import machinery on every call -- PySide6 hooks
# builtins.__import__, so each costs microseconds.
_LAZY_MODULES: dict = {}


def _lazy(name: str):
    m = _LAZY_MODULES.get(name)
    if m is None:
        m = _LAZY_MODULES[name] = importlib.import_module("." + name, __package__)
    return m


# type codes whose geometry is node-based
LINE_TC = 0x01
PIPE_TC = 0x0B
POLYGON_TCS = (0x10, 0x0A)
# the free-hand shape (CtDraw's 'Draw' class): a style block, then a counted
# point array that runs to the body END (its bbox == rect1); a record without a
# style block (bit 0x2 clear) carries no points
FREEHAND_TC = 0x02
NODE_TCS = (LINE_TC, PIPE_TC) + POLYGON_TCS + (FREEHAND_TC,)

_BBOX_TOL = 2  # px tolerance between node-bbox and rect1 when locating arrays


# --------------------------------------------------------------------------- header fields
@dataclass
class GeomHeader:
    """Decoded offsets + values of the geometry header in ``body_raw``."""

    anchor_off: int
    rect1_off: int
    rect2_off: int
    end: int  # offset just past rect2
    anchor: Tuple[int, int]
    rect1: Tuple[int, int, int, int]
    rect2: Tuple[int, int, int, int]
    name: str = ""  # object name lpstr (genie members carry one)


# ---- object handle = the builder's animation number (AN) - 1 --------------
# The geometry header opens [lpstr (always empty)][u16 handle][lpstr name].
# The u16 handle is CtDraw's: the live AN is handle + 1 (COM getter, the GOTOAN
# dialog, the compiled pg*.DBF AN column). Every record's
# handle is page-UNIQUE at every depth (genie internals included), EXCEPT input
# records (Touch 0x1010 / Keyboard 0x100B), which carry their OWNER's handle.
# Usable range 0..0x7FFE (CtDraw needs
# (short)handle >= 0); 0xFFFF = unassigned. On load a DUPLICATE handle is kept
# by the first record in z-order and the later one is renumbered.
INPUT_TYPE_CODES = _codes.INPUT_TCS
MAX_HANDLE = 0x7FFE


def get_handle(po: PageObject) -> Optional[int]:
    """The record's u16 object handle (live AN - 1), or None when absent."""
    if len(po.body_raw) < 4:
        return None
    return struct.unpack_from("<H", po.body_raw, 2)[0]


def successor_index(objs, old, index: Optional[int] = None) -> Optional[int]:
    """Where the record that replaced ``old`` sits now, or None."""
    if old is None:
        return None
    try:
        handle, tc = get_handle(old), old.type_code
    except AttributeError, TypeError:  # swallow-ok: not a page record (nothing follows)
        return None
    if handle is None or handle == 0xFFFF:
        return None

    def same(po) -> bool:
        return po.type_code == tc and po is not old and get_handle(po) == handle

    if isinstance(index, int) and 0 <= index < len(objs) and same(objs[index]):
        return index
    hits = [k for k, po in enumerate(objs) if same(po)]
    if not hits:
        return None
    if not isinstance(index, int):
        return hits[0]
    return min(hits, key=lambda k: abs(k - index))


def set_handle(po: PageObject, handle: int) -> None:
    """Overwrite the record's u16 object handle (the name lpstr after it is
    left untouched)."""
    body = bytearray(po.body_raw)
    struct.pack_into("<H", body, 2, int(handle) & 0xFFFF)
    po.body_raw = bytes(body)


def set_object_name(po: PageObject, name: str) -> bool:
    """Set the object name in the geometry header."""
    body = po.body_raw
    end = _geom_name_len(body, 0)
    if end < 6:
        return False
    enc = str(name).encode(CODEC, "replace")
    po.body_raw = body[:4] + struct.pack("<H", len(enc)) + enc + body[end:]
    return True


def live_an(po: PageObject) -> int:
    """The animation number the Graphics Builder / compiler use (handle + 1),
    -1 when the record carries no valid handle."""
    h = get_handle(po)
    if h is None or h > MAX_HANDLE:
        return -1
    return h + 1


def parse_geom_header(body: bytes) -> Optional[GeomHeader]:
    """Decode the deterministic geometry header. Returns None if it overruns."""
    n = len(body)
    o = _geom_name_len(body, 0)
    if o == 0 or o > n:
        return None
    name = body[6:o].decode(CODEC, "replace") if o > 6 else ""
    skip = _XFORM_SKIP
    for _ in range(3):
        if o >= n:
            return None
        o += skip[body[o]]
        if o > n:
            return None
    if o + 4 + 8 + 8 > n:
        return None
    return GeomHeader(o, o + 4, o + 12, o + 20, _P2H(body, o), _P4H(body, o + 4), _P4H(body, o + 12), name)


#: transform mode byte -> the bytes of its slot (the mode byte and its doubles: _XFORM_DOUBLES)
_XFORM_SKIP = tuple(1 + _XFORM_DOUBLES.get(m, 6 if m > 3 else 0) * 8 for m in range(256))
_P2H = struct.Struct("<2h").unpack_from
_P4H = struct.Struct("<4h").unpack_from


def text_points(po) -> Optional[int]:
    """A text record's point size from its tail (None for other classes / no tail)."""
    if getattr(po, "type_code", None) != 0x05 or not (po.subtype_size & 0x2):
        return None
    try:
        t = parse_text_tail(po)
    except Exception:  # noqa: BLE001  # swallow-ok: a tail that does not parse has no size
        return None
    return t["size"] if t else None


def norm_rect(rect: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    l, t, r, b = rect
    return min(l, r), min(t, b), max(l, r), max(t, b)


def group_union(kids) -> Tuple[Optional[tuple], Optional[tuple]]:
    """(Extent, Bounds) of a group from its direct children."""
    kids = [k for k in kids if k[1] is not None]
    ext = [k for k in kids if k[0] not in INPUT_TYPE_CODES] or kids
    bnd = [k for k in ext if not (k[2] is not None and k[2][0] == k[2][2] and k[2][1] == k[2][3])] or ext

    def u(rs):
        rs = [r for r in rs if r is not None]
        if not rs:
            return None
        return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))

    return u([k[1] for k in ext]), u([k[2] for k in bnd])


def _bbox(pts: List[Tuple[int, int]]) -> Tuple[int, int, int, int]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_close(a, b, tol=_BBOX_TOL) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# --------------------------------------------------------------------------- node arrays
@dataclass
class NodeArray:
    """A located vertex array in ``body_raw``."""

    offset: int
    count: int
    counted: bool  # True when a u16 count prefix is present

    @property
    def data_off(self) -> int:
        return self.offset + (2 if self.counted else 0)

    @property
    def end(self) -> int:
        return self.data_off + self.count * 4


def read_nodes(body: bytes, na: NodeArray) -> List[Tuple[int, int]]:
    return list(struct.iter_unpack("<2h", body[na.data_off : na.end]))


#: per-record memo of :func:`find_node_array` = (body_raw, type_code, dwFlags, result tuple): the
#: search is a pure function of those three, and a move / resize / derive asks for the same
#: record's array several times. Keyed on the
#: body OBJECT (identity): kept only for an immutable ``bytes`` body, and dropped by the bag
#: re-scan once the body is replaced (writer.refresh_object_bags).
_NODE_MEMO = "_node_array_memo"


def find_node_array(po: PageObject) -> Optional[NodeArray]:
    """Locate the vertex array of a line, pipe or polygon, or None."""
    if po.type_code not in NODE_TCS:
        return None
    body = po.body_raw
    d = getattr(po, "__dict__", None) if type(body) is bytes else None
    if d is not None:
        m = d.get(_NODE_MEMO)
        if m is not None and m[0] is body and m[1] == po.type_code and m[2] == po.subtype_size:
            r = m[3]
            return None if r is None else NodeArray(*r)
    na = _find_node_array(po, body)
    if d is not None:
        d[_NODE_MEMO] = (
            body,
            po.type_code,
            po.subtype_size,
            None if na is None else (na.offset, na.count, na.counted),
        )
    return na


def _find_node_array(po: PageObject, body: bytes) -> Optional[NodeArray]:
    gh = parse_geom_header(body)
    if gh is None:
        return None
    rect = norm_rect(gh.rect1)

    if po.type_code == LINE_TC:
        # A FREE line (dwFlags bit 0x2) always ends with its two endpoints in
        # DRAW order: CtDraw reads them there
        # and draws MoveTo(p1) LineTo(p2). rect1
        # is only their NORMALISED extent, so an anti-diagonal line cannot be
        # recovered from rect1.
        if po.subtype_size & 0x2 and len(body) - 8 >= gh.end:
            return NodeArray(len(body) - 8, 2, counted=False)
        # linked (art-stripped) copy: optional redundant trailing pair
        pos = len(body) - 8
        if pos >= gh.end:
            pts = list(struct.iter_unpack("<2h", body[pos : pos + 8]))
            if _bbox_close(_bbox(pts), rect):
                return NodeArray(pos, 2, counted=False)
        return None

    if po.type_code == FREEHAND_TC:
        # the array ENDS the body; a hand-drawn stroke can hold hundreds of points
        if not po.subtype_size & 0x2:
            return None
        for pos in range(gh.end, len(body) - 5):
            (cnt,) = struct.unpack_from("<H", body, pos)
            if cnt >= 2 and pos + 2 + cnt * 4 == len(body):
                pts = list(struct.iter_unpack("<2h", body[pos + 2 :]))
                if _bbox_close(_bbox(pts), rect):
                    return NodeArray(pos, cnt, counted=True)
        return None

    # the array starts the record's CLASS section, where the class's loader reads it: right after
    # its style block, and after its metadata section on a record with one (dwFlags 0x100).
    # The backward scan below also passes a run INSIDE the real one, its count read from
    # one of the nodes (a first x equal to the node count, a y equal to the number of nodes
    # after it), whenever that run's bbox is rect1's within the tolerance, and it finds that run
    # first: the class section is read first
    na = _class_section_run(po, body, rect)
    if na is not None:
        return na

    # the general scan: every position holding a plausible count is a candidate, and a record
    # with big bags after its header has hundreds of them. A candidate is dropped at its first
    # point outside rect1 (plus the tolerance) instead of unpacking the whole run and taking
    # its bbox
    l, t, r, b = rect
    lo_x, hi_x, lo_y, hi_y = l - _BBOX_TOL, r + _BBOX_TOL, t - _BBOX_TOL, b + _BBOX_TOL
    n = len(body)
    u16 = struct.unpack_from
    iter_pts = struct.iter_unpack
    for pos in range(n - 2, gh.end - 1, -1):
        (cnt,) = u16("<H", body, pos)
        if not (2 <= cnt <= 512):
            continue
        end = pos + 2 + cnt * 4
        if end > n:
            continue
        minx = miny = 32767
        maxx = maxy = -32768
        for x, y in iter_pts("<2h", body[pos + 2 : end]):
            if x < lo_x or x > hi_x or y < lo_y or y > hi_y:
                break
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
        else:
            box = (minx, miny, maxx, maxy)
            if _bbox_close(box, rect):
                if box != rect:
                    pos = _better_overlapping_run(body, gh.end, pos, box, rect) or pos
                    (cnt,) = u16("<H", body, pos)
                return NodeArray(pos, cnt, counted=True)
    return None


def _class_section_run(po: PageObject, body: bytes, rect) -> Optional[NodeArray]:
    """The counted run at the start of ``po``'s class section (:func:`_find_node_array`) when
    it is a node array that fits rect1 as the scan tests one (2 to 512 nodes, the bbox within
    the tolerance), else None: no style block, a forward walk (colourstate.layout) that does
    not reach it, a record laid out otherwise."""
    if not po.subtype_size & 0x2 or body is not po.body_raw:
        return None
    try:
        L = _lazy("colourstate").layout(po)
        if L is None or "style_at" not in L:
            return None
        s = L["style_at"] + 54
        if po.subtype_size & 0x100:
            span = metadata_span(po)
            if span is None:
                return None
            s = span[1]
    except Exception:  # noqa: BLE001
        _note_exc("geometry._class_section_run")
        return None
    n = len(body)
    if s < 0 or s + 2 > n:
        return None
    (cnt,) = struct.unpack_from("<H", body, s)
    end = s + 2 + cnt * 4
    if not (2 <= cnt <= 512) or end > n:
        return None
    if not _bbox_close(_bbox(list(struct.iter_unpack("<2h", body[s + 2 : end]))), rect):
        return None
    return NodeArray(s, cnt, counted=True)


def _better_overlapping_run(body: bytes, start: int, pos: int, box, rect) -> Optional[int]:
    """A better-fitting counted run that overlaps the one found, or None."""

    def miss(b4):
        return sum(abs(p - q) for p, q in zip(b4, rect))

    l, t, r, b = rect
    lo_x, hi_x, lo_y, hi_y = l - _BBOX_TOL, r + _BBOX_TOL, t - _BBOX_TOL, b + _BBOX_TOL
    n = len(body)
    best, best_pos = miss(box), None
    for q in range(pos - 1, max(start, pos - 2 - 512 * 4) - 1, -1):
        (cq,) = struct.unpack_from("<H", body, q)
        end = q + 2 + cq * 4
        if not (2 <= cq <= 512) or end <= pos or end > n:
            continue
        pts = []
        for x, y in struct.iter_unpack("<2h", body[q + 2 : end]):
            if x < lo_x or x > hi_x or y < lo_y or y > hi_y:
                break
            pts.append((x, y))
        else:
            bq = _bbox(pts)
            e = miss(bq)
            if e < best and _bbox_close(bq, rect):
                best, best_pos = e, q
                if not e:
                    break
    return best_pos


def get_object_nodes(po: PageObject) -> Optional[List[Tuple[int, int]]]:
    """The object's vertices in page coordinates, or None."""
    if po.type_code == LINE_TC:
        if po.subtype_size & 0x2:
            na = find_node_array(po)
            if na is not None:
                return read_nodes(po.body_raw, na)
        gh = parse_geom_header(po.body_raw)
        if gh is None:
            return None
        x1, y1, x2, y2 = gh.rect1
        return [(x1, y1), (x2, y2)]
    na = find_node_array(po)
    if na is None:
        return None
    return read_nodes(po.body_raw, na)


def set_object_nodes(po: PageObject, pts: List[Tuple[int, int]]) -> bool:
    """Rewrite an object's vertex list and re-derive its header."""
    if po.type_code == LINE_TC:
        if len(pts) != 2:
            return False
        (x1, y1), (x2, y2) = pts
        return _set_line_endpoints(po, x1, y1, x2, y2)

    na = find_node_array(po)
    if na is None or not pts:
        return False
    gh = parse_geom_header(po.body_raw)
    d = _rect2_deltas(gh)
    flags = style_flags(po)
    follows = False
    if flags is not None:
        p_old = predict_bounds(po.type_code, gh.rect1, flags)
        follows = p_old is not None and tuple(gh.rect2) == tuple(p_old)
    new_bbox = _bbox(pts)

    body = bytearray(po.body_raw)
    new_blob = struct.pack("<H", len(pts)) + b"".join(struct.pack("<2h", int(x), int(y)) for x, y in pts)
    body[na.offset : na.end] = new_blob
    # header straight from the new bbox (the old rect may be axis-degenerate, so
    # affine-mapping it is unreliable); anchor tracks the first node.
    gh2 = parse_geom_header(bytes(body))
    nl, nt, nr, nb = new_bbox
    struct.pack_into("<4h", body, gh2.rect1_off, nl, nt, nr, nb)
    rect2 = predict_bounds(po.type_code, new_bbox, flags) if follows else None
    if rect2 is None:
        rect2 = (nl + d[0], nt + d[1], nr + d[2], nb + d[3])
    struct.pack_into("<4h", body, gh2.rect2_off, *rect2)
    struct.pack_into("<2h", body, gh2.anchor_off, int(pts[0][0]), int(pts[0][1]))
    po.body_raw = bytes(body)
    refresh_bags(po)
    return True


# --------------------------------------------------------------------------- reshape (nodes)
# Node editing (help 1172187 'Resize Objects' / 1172188 'Reshape Objects'):
# only the line, pipe and polyline
# classes take CtDraw's node edits -- the node
# under the cursor (a 7 x 7 box round each node), a segment under it,
# insert a node, move the selected nodes, delete them; no
# other class does (the free-hand 'Draw' shape included).
RESHAPE_TCS = (LINE_TC, PIPE_TC) + POLYGON_TCS
#: a node delete that would leave fewer nodes is refused (count - selected < 2:
#: nothing deleted); a LINE has exactly two and deleting one of them deletes the line
#: (help 1172188)
MIN_NODES = 2
#: i16 page coordinates
COORD_MIN, COORD_MAX = -32768, 32767


@dataclass
class ReshapeInfo:
    """A reshapable object: its class ('line' / 'pipe' / 'polyline'), its
    nodes in draw order (page coordinates) and whether the last node joins
    the first (a closed or filled polygon: CtDraw then also hit-
    tests the closing segment)."""

    kind: str
    nodes: List[Tuple[int, int]]
    closed: bool = False

    @property
    def can_insert(self) -> bool:
        """A line has exactly two nodes: 'to achieve the same result as
        adding a node to a Line object, create a Polyline object instead'."""
        return self.kind != "line"

    def segments(self) -> List[Tuple[int, int]]:
        """(i, j) node pairs of every segment, the closing one last."""
        n = len(self.nodes)
        out = [(i, i + 1) for i in range(n - 1)]
        if self.closed and n > 2:
            out.append((n - 1, 0))
        return out


def reshape_info(po: PageObject) -> Optional[ReshapeInfo]:
    """The object's ReshapeInfo, or None when CtDraw can't reshape it node by node."""
    if po.type_code not in RESHAPE_TCS or not po.subtype_size & 0x2:
        return None
    if get_local_transform(po) is not None:
        return None
    try:
        nodes = get_object_nodes(po)
    except Exception:  # noqa: BLE001 -- an odd body: not reshapable
        _note_exc("geometry.reshape_info")
        return None
    if not nodes or len(nodes) < 2:
        return None
    kind = {LINE_TC: "line", PIPE_TC: "pipe"}.get(po.type_code, "polyline")
    closed = False
    if kind == "polyline":
        flags = style_flags(po)
        if flags is not None:
            # the closing segment counts unless the polygon is open
            # (attribs bit 0) and hollow (fill_style 0) -- draws it so
            closed = not (flags[FLAG_ATTRIBS] & 0x01) or bool(flags[FLAG_FILL_STYLE] & 0x0F)
    return ReshapeInfo(kind, [tuple(p) for p in nodes], closed)


def segment_foot(a, b, p) -> Optional[Tuple[Tuple[float, float], float]]:
    """(the foot of ``p`` on segment a-b, its distance from ``p``), None for
    a point outside the segment's span or a zero-length segment."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll == 0:
        return None
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll
    if t < 0.0 or t > 1.0:
        return None
    fx, fy = ax + t * dx, ay + t * dy
    return (fx, fy), ((p[0] - fx) ** 2 + (p[1] - fy) ** 2) ** 0.5


def node_insert_point(info: ReshapeInfo, p, tol: float) -> Optional[Tuple[int, Tuple[int, int]]]:
    """Where a node inserted at ``p`` goes, or None if ``p`` isn't near a segment."""
    if not info.can_insert:
        return None
    best = None
    for i, j in info.segments():
        a, b = info.nodes[i], info.nodes[j]
        hit = segment_foot(a, b, p)
        if hit is None or hit[1] > tol:
            continue
        (fx, fy), dist = hit
        pt = (int(round(fx)), int(round(fy)))
        if pt == tuple(a) or pt == tuple(b):
            continue
        if best is None or dist < best[0]:
            best = (dist, i + 1, pt)
    return None if best is None else (best[1], best[2])


def _set_line_endpoints(po: PageObject, x1: int, y1: int, x2: int, y2: int) -> bool:
    """Set a line's endpoints the way the line class derives its header."""
    body = bytearray(po.body_raw)
    gh = parse_geom_header(bytes(body))
    if gh is None:
        return False
    if po.subtype_size & 0x2 and len(body) - 8 >= gh.end:
        n = len(body)
        rect1 = norm_rect((x1, y1, x2, y2))
        struct.pack_into("<2h", body, gh.anchor_off, x1, y1)
        struct.pack_into("<4h", body, gh.rect1_off, *rect1)
        struct.pack_into("<4h", body, n - 8, x1, y1, x2, y2)
        po.body_raw = bytes(body)
        flags = style_flags(po)
        rect2 = predict_bounds(LINE_TC, rect1, flags) if flags else None
        if rect2 is None:
            d = _rect2_deltas(gh)
            rect2 = (rect1[0] + d[0], rect1[1] + d[1], rect1[2] + d[2], rect1[3] + d[3])
        _set_rect2(po, rect2)
        refresh_bags(po)
        return True
    na = find_node_array(po)

    d = _rect2_deltas(gh)
    struct.pack_into("<4h", body, gh.rect1_off, x1, y1, x2, y2)
    nl, nt, nr, nb = norm_rect((x1, y1, x2, y2))
    struct.pack_into("<4h", body, gh.rect2_off, nl + d[0], nt + d[1], nr + d[2], nb + d[3])
    # anchor follows whichever endpoint it used to track; default = first point
    ax, ay = gh.anchor
    ox1, oy1, ox2, oy2 = gh.rect1
    if (ax, ay) == (ox2, oy2):
        struct.pack_into("<2h", body, gh.anchor_off, x2, y2)
    else:
        struct.pack_into("<2h", body, gh.anchor_off, x1, y1)
    if na is not None:
        struct.pack_into("<2h", body, na.data_off, x1, y1)
        struct.pack_into("<2h", body, na.data_off + 4, x2, y2)
    po.body_raw = bytes(body)
    refresh_bags(po)
    return True


# --------------------------------------------------------------------------- bounds (rect2)
# rect1 = CtDraw's "Extent", rect2 = its "Bounds": the bounding rect
# the saver caches and that the automation API reports as AttributeX/Y/ExtentX/
# ExtentY. Per class:
# base : 3D effect 1/2/4 inflates all sides by d, 3
#         (shadow) grows right/bottom by d; d = effect_depth, or when -1 auto:
#         (w+h)//500+1 (1/2/4) / (w+h)//200+1 (3); text uses d=1
# line/pline/pipe: l,t -= w//2;
#         r,b += w//2 + (w&1)
# rect : r,b += 1; inflate w//2; +1 more if attribs&0x10
# ellipse / button: r,b += 1 (no pen term)
#   text / bitmap: base
_BOUNDS_LINEISH = frozenset({0x01, 0x10, 0x0A, 0x0B})
_BOUNDS_RECTS = frozenset({0x0F, 0x0E, 0x03})
_BOUNDS_ROUND = frozenset({0x11, 0x04, 0x1001})
_BOUNDS_BASE = frozenset({0x05, 0x07})


def text_auto_depth(points: int, bold: bool) -> int:
    """The text class's 'auto' 3D depth: the point
    size / 20, at least 1, plus half of it when Bold."""
    d = max(1, int(points or 0) // 20)
    if bold:
        d += d >> 1
    return d


def effect_depth(type_code: int, rect1, flags: bytes, points: Optional[int] = None) -> int:
    """The 3D effect depth CtDraw draws with."""
    e, w = flags[FLAG_EFFECTS], flags[FLAG_LINE_WIDTH]
    dp = flags[FLAG_EFFECT_DEPTH]
    dp = dp - 256 if dp >= 128 else dp
    if dp != -1:
        return dp
    eff = e & 0x0F
    if type_code == 0x05:
        if not eff:
            return 0
        return text_auto_depth(points, bool(flags[FLAG_ATTRIBS] & 0x10)) if points else 1
    l, t, r, b = norm_rect(rect1)
    if eff == 0:
        d = 0
    elif eff in (1, 2, 4):
        d = ((r - l) + (b - t)) // 500 + 1
    elif eff == 3:
        d = ((r - l) + (b - t)) // 200 + 1
    else:
        d = 1
    if not (flags[FLAG_FILL_STYLE] & 0x0F) and w < d:
        d = w
    return d


def predict_bounds(type_code: int, rect1, flags: bytes, points: Optional[int] = None):
    """The Bounds (rect2) CtDraw derives for ``rect1`` + the 6 style flag bytes,
    or None for a class whose rule is not modelled. ``points`` = a text's point
    size (its auto depth, :func:`text_auto_depth`; 1 when not given)."""
    e, attribs, w = flags[FLAG_EFFECTS], flags[FLAG_ATTRIBS], flags[FLAG_LINE_WIDTH]
    dp = flags[FLAG_EFFECT_DEPTH]
    dp = dp - 256 if dp >= 128 else dp
    l, t, r, b = norm_rect(rect1)
    eff = e & 0x0F
    if eff in (1, 2, 4, 3):
        bw, bh = r - l, b - t
        if dp == -1:
            if type_code == 0x05:
                dp = text_auto_depth(points, bool(attribs & 0x10)) if points else 1
            else:
                dp = (bw + bh) // (200 if eff == 3 else 500) + 1
            # CtDraw clamps an auto depth to the pen width of a HOLLOW shape
            if type_code != 0x05 and not (flags[FLAG_FILL_STYLE] & 0x0F) and w < dp:
                dp = w
        if eff == 3:
            r, b = r + dp, b + dp
        else:
            l, t, r, b = l - dp, t - dp, r + dp, b + dp
    if type_code in _BOUNDS_LINEISH:
        h = w // 2
        return (l - h, t - h, r + h + (w & 1), b + h + (w & 1))
    if type_code in _BOUNDS_RECTS:
        h = w // 2
        l, t, r, b = l - h, t - h, r + 1 + h, b + 1 + h
        if attribs & 0x10:
            l, t, r, b = l - 1, t - 1, r + 1, b + 1
        return (l, t, r, b)
    if type_code in _BOUNDS_ROUND:
        return (l, t, r + 1, b + 1)
    if type_code in _BOUNDS_BASE:
        return (l, t, r, b)
    return None


def _set_rect2(po: PageObject, rect2) -> None:
    gh = parse_geom_header(po.body_raw)
    if gh is None:
        return
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in rect2))
    po.body_raw = bytes(body)


def rebound(
    po: PageObject, old_rect1, old_flags: Optional[bytes], new_rect1=None, new_flags: Optional[bytes] = None
) -> None:
    """Keep rect2 in step after a geometry/style change: move each edge by the
    change in the class rule's prediction (so a record that already follows the
    rule gets the exact new Bounds, and one that carries an unmodelled
    oddity keeps it). No-op for unmodelled classes / records without flags."""
    gh = parse_geom_header(po.body_raw)
    if gh is None or old_flags is None:
        return
    new_rect1 = gh.rect1 if new_rect1 is None else new_rect1
    new_flags = old_flags if new_flags is None else new_flags
    pts = text_points(po)
    p0 = predict_bounds(po.type_code, old_rect1, old_flags, pts)
    p1 = predict_bounds(po.type_code, new_rect1, new_flags, pts)
    if p0 is None or p1 is None or p0 == p1:
        return
    r2 = gh.rect2
    if tuple(r2) == tuple(p0):
        _set_rect2(po, p1)
    else:
        _set_rect2(po, tuple(r2[i] + p1[i] - p0[i] for i in range(4)))


def style_flags(po: PageObject) -> Optional[bytes]:
    """The 6 style flag bytes of the record's exact style block, or None."""
    cb = find_colour_block(po)
    return None if cb is None else bytes(cb.flags)


# --------------------------------------------------------------------------- translate / resize
def _rect2_deltas(gh: GeomHeader) -> Tuple[int, int, int, int]:
    """rect2's per-edge offsets from the normalised rect1 (preserved across edits)."""
    nl, nt, nr, nb = norm_rect(gh.rect1)
    l2, t2, r2, b2 = gh.rect2
    return l2 - nl, t2 - nt, r2 - nr, b2 - nb


def _update_rect_echo(po: PageObject, body: bytearray, old_rect1, new_rect1) -> None:
    """Keep the body tail's rect echo in step with rect1."""
    n = len(body)
    old = struct.pack("<4h", *old_rect1)
    new = struct.pack("<4h", *new_rect1)
    if po.type_code == 0x1001 and n >= 8 and (po.subtype_size & 0x2):
        # an own-appearance button's echo is its POSITION source; an
        # auto-width button's echo can carry a stale right edge -- shift it
        # whenever its top-left is the old one
        cur = struct.unpack_from("<4h", body, n - 8)
        if cur[:2] == tuple(old_rect1[:2]) and cur != tuple(old_rect1):
            dx, dy = new_rect1[0] - old_rect1[0], new_rect1[1] - old_rect1[1]
            struct.pack_into("<4h", body, n - 8, cur[0] + dx, cur[1] + dy, cur[2] + dx, cur[3] + dy)
            return
    if po.type_code in (0x01, 0x1001, 0x13) and n >= 8 and bytes(body[n - 8 : n]) == old:
        body[n - 8 : n] = new  # 0x13 WebContent also echoes rect1 last
    elif po.type_code == 0x0F and n >= 18 and bytes(body[n - 18 : n - 10]) == old:
        body[n - 18 : n - 10] = new
    elif po.type_code == 0x11 and n >= 21 and bytes(body[n - 21 : n - 13]) == old:
        body[n - 21 : n - 13] = new


def _retarget_header(po: PageObject, old_rect, new_rect) -> None:
    """Map anchor/rect1/rect2 from old normalised rect to new normalised rect."""
    body = bytearray(po.body_raw)
    gh = parse_geom_header(bytes(body))
    d = _rect2_deltas(gh)

    def mapx(x):
        ow = old_rect[2] - old_rect[0]
        return new_rect[0] + (round((x - old_rect[0]) * (new_rect[2] - new_rect[0]) / ow) if ow else 0)

    def mapy(y):
        oh = old_rect[3] - old_rect[1]
        return new_rect[1] + (round((y - old_rect[1]) * (new_rect[3] - new_rect[1]) / oh) if oh else 0)

    l, t, r, b = gh.rect1
    new_r1 = (mapx(l), mapy(t), mapx(r), mapy(b))
    struct.pack_into("<4h", body, gh.rect1_off, *new_r1)
    nl, nt, nr, nb = norm_rect(new_r1)
    new_r2 = (nl + d[0], nt + d[1], nr + d[2], nb + d[3])
    # a record following the class Bounds rule gets the exact new Bounds (the
    # auto 3D depth grows with the size); an odd one keeps its edge offsets
    flags = style_flags(po)
    if flags is not None:
        p_old = predict_bounds(po.type_code, gh.rect1, flags)
        if p_old is not None and tuple(gh.rect2) == tuple(p_old):
            new_r2 = predict_bounds(po.type_code, new_r1, flags)
    struct.pack_into("<4h", body, gh.rect2_off, *new_r2)
    ax, ay = gh.anchor
    struct.pack_into("<2h", body, gh.anchor_off, mapx(ax), mapy(ay))
    _update_rect_echo(po, body, gh.rect1, new_r1)
    _update_text_echo(po, body, (mapx(ax), mapy(ay)))
    po.body_raw = bytes(body)


def _local_anchor(po: PageObject) -> bool:
    """An art-stripped ellipse (dwFlags bit 0x2 clear: a genie member) keeps
    its library twin's anchor in ELEMENT coordinates (outside its box); the
    engine restores it, so it is never moved."""
    return po.type_code == 0x11 and not (po.subtype_size & 0x2)


def translate_object(
    po: PageObject,
    dx: int,
    dy: int,
    link_info=None,
    slot_shifts=None,
    parent_total=None,
    refresh: bool = True,
) -> None:
    """Move an object by (dx, dy)."""
    legacy, placement = _lazy("legacy"), _lazy("placement")
    if placement.is_linked_symbol(po):
        lshift = (dx, dy) if slot_shifts is None else slot_shifts[0]
        if link_info is not None:
            if lshift is None:  # inherited: Local stays, re-derive
                (m1, L, _o, _n), _s2, _s3 = placement.read_slots(po.body_raw)[0]
                placement.rederive_linked(po, L, m1, link_info["e"], link_info["N"], parent_total)
            else:
                placement.translate_linked(po, lshift[0], lshift[1], link_info, parent_total)
        else:
            placement.translate_linked_unresolved(po, dx, dy, slot_shifts)
        if refresh:
            refresh_bags(po)
        return
    native_old = parse_geom_header(po.body_raw)
    native_old = norm_rect(native_old.rect1) if native_old is not None else None
    ct = legacy.class_tail(po)  # validated against the OLD header
    ct_from_end = (len(po.body_raw) - ct[1]) if ct else None
    na = find_node_array(po)  # locate BEFORE the rects move (bbox check)
    body = bytearray(po.body_raw)
    gh = parse_geom_header(bytes(body))
    if gh is None:
        return
    ax, ay = gh.anchor
    if not _local_anchor(po):
        struct.pack_into("<2h", body, gh.anchor_off, ax + dx, ay + dy)
    l, t, r, b = gh.rect1
    struct.pack_into("<4h", body, gh.rect1_off, l + dx, t + dy, r + dx, b + dy)
    l2, t2, r2, b2 = gh.rect2
    struct.pack_into("<4h", body, gh.rect2_off, l2 + dx, t2 + dy, r2 + dx, b2 + dy)
    if na is not None:
        for i, (x, y) in enumerate(read_nodes(bytes(body), na)):
            struct.pack_into("<2h", body, na.data_off + i * 4, x + dx, y + dy)
    _update_rect_echo(po, body, gh.rect1, (l + dx, t + dy, r + dx, b + dy))
    _update_text_echo(po, body, (ax + dx, ay + dy))
    po.body_raw = bytes(body)
    # CtDraw positions a RESOLVED symbol / genie's composed art by the slot-1
    # 'Local' transform's H,K translation, NOT rect1. A carrier's slots 2/3
    # are Local-derived (a linked genie's
    # slot 2 = Local o T(-e) != Local), so each slot shifts by its own H,K
    # -- never re-echo Local into them. Plain shapes (mode 0/2) are untouched.
    if slot_shifts is None:
        slot_shifts = ((dx, dy),) * 3 if placement.carries(po) else (None,) * 3
    try:
        placement.shift_slots(po, slot_shifts)
    except Exception:  # noqa: BLE001 -- malformed header: geometry-only move
        _note_exc("geometry.translate_object")
    if ct_from_end is not None:
        legacy.shift_class_point(po, len(po.body_raw) - ct_from_end, dx, dy)
    if native_old is not None and (placement.has_native_rect(po) or po.type_code == placement.SLIDER_TC):
        placement.shift_class_native(po, dx, dy, native_old)
    if text_total(po) is not None:
        sync_text_echo(po)  # a mirrored text's echo is local
    if refresh:
        refresh_bags(po)


def resize_object(
    po: PageObject,
    new_rect: Tuple[int, int, int, int],
    link_info=None,
    anchor=None,
    held=None,
    per_entry: bool = True,
    stretch: bool = True,
) -> None:
    """Resize an object to ``new_rect``, scaling its nodes and anchor."""
    legacy, placement = _lazy("legacy"), _lazy("placement")
    if legacy.move_to_rect(po, new_rect):
        return
    if placement.is_linked_symbol(po):
        # a placed symbol's SIZE is its Local scale x the native element size
        if link_info is not None:
            placement.set_linked_rect(po, norm_rect(new_rect), link_info)
        else:
            placement.set_linked_rect_unresolved(po, norm_rect(new_rect))
        refresh_bags(po)
        return
    if po.type_code == placement.SLIDER_TC:
        placement.set_slider_track(po, norm_rect(new_rect))
        refresh_bags(po)
        return
    if placement.has_native_rect(po) and placement.native_rect(po) is not None:
        old = placement.native_rect(po)
        placement.set_native_rect(po, norm_rect(new_rect))
        if po.type_code == placement.ACTIVEX_TC:
            placement.patch_ole_extent(po, old, norm_rect(new_rect))
        refresh_bags(po)
        return
    if (
        po.type_code == 0x05
        and (po.subtype_size & 0x2)
        and resize_text(po, new_rect, anchor=anchor, held=held, per_entry=per_entry, stretch=stretch)
    ):
        refresh_bags(po)
        return
    gh = parse_geom_header(po.body_raw)
    if gh is None:
        return
    old_rect = norm_rect(gh.rect1)
    nr = norm_rect(new_rect)
    na = find_node_array(po)
    if na is not None:
        pts = read_nodes(po.body_raw, na)
        ow = old_rect[2] - old_rect[0] or 1
        oh = old_rect[3] - old_rect[1] or 1
        scaled = [
            (
                nr[0] + round((x - old_rect[0]) * (nr[2] - nr[0]) / ow),
                nr[1] + round((y - old_rect[1]) * (nr[3] - nr[1]) / oh),
            )
            for x, y in pts
        ]
        body = bytearray(po.body_raw)
        for i, (x, y) in enumerate(scaled):
            struct.pack_into("<2h", body, na.data_off + i * 4, x, y)
        po.body_raw = bytes(body)
    if po.type_code == 0x1001:
        writer = _lazy("writer")
        props = po.raw_properties()
        for key, v in (("WIDTH", nr[2] - nr[0]), ("HEIGHT", nr[3] - nr[1])):
            if key in props and props[key] != str(v):
                writer.set_object_property(po, key, str(v))
    keep = parse_geom_header(po.body_raw).anchor if _local_anchor(po) else None
    _retarget_header(po, old_rect, nr)
    if keep is not None:
        gh2 = parse_geom_header(po.body_raw)
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, gh2.anchor_off, *keep)
        po.body_raw = bytes(body)
    elif anchor is not None:
        # the caller's transform places the hotspot (a unit resize maps it
        # through the unit's matrix, not this record's own box map)
        gh2 = parse_geom_header(po.body_raw)
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, gh2.anchor_off, int(anchor[0]), int(anchor[1]))
        po.body_raw = bytes(body)
    elif po.type_code == 0x11:
        # an ellipse's anchor is (l + (w-1)//2, t) --
        # the engine re-derives it; a scaled seed anchor is off by one
        gh2 = parse_geom_header(po.body_raw)
        if gh2 is not None:
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, gh2.anchor_off, nr[0] + (nr[2] - nr[0] - 1) // 2, nr[1])
            po.body_raw = bytes(body)
    refresh_bags(po)


# --------------------------------------------------------------------------- shape variant tails
# Per-kind variant data at the body END (dwFlags bit 0x2 only; the
# rectangle, circle and polyline classes):
#   RECT tc=0x0F: [4xi16 extent echo @ n-18][u8 corner_radius @ n-10]
#                 [u8 gradient_mode @ n-9][u32 grad_on @ n-8][u32 grad_off @ n-4]
#   ELLIPSE tc=0x11: [echo @ n-21][i16 start_angle @ n-13][i16 end_angle @ n-11]
#                 [u8 gradient_mode @ n-9][grad pair @ n-8]  (mode 0 ellipse /
#                 1 pie / 2 arc lives in the style-block 'attribs' byte)
#   POLYGON tc=0x10: [u8 gradient_mode][grad pair] (9 bytes after the vertex
#                 array; open/closed = attribs byte 1/0)
# Gradient modes (official docs): 0 off, 1 L>R, 2 R>L, 3 T>B, 4 B>T,
# 5 HEdge>Middle, 6 Middle>HEdge, 7 VEdge>Middle, 8 Middle>VEdge.


def parse_shape_tail(po: PageObject) -> Optional[dict]:
    """Decode the per-kind shape-variant tail, or None when absent."""
    if not (po.subtype_size & 0x2):
        return None
    body = po.body_raw
    n = len(body)
    gh = parse_geom_header(body)
    if gh is None:
        return None
    r1 = struct.pack("<4h", *gh.rect1)
    try:
        if po.type_code == 0x0F and n >= 18 and body[n - 18 : n - 10] == r1:
            radius, mode = body[n - 10], body[n - 9]
            g_on, g_off = struct.unpack_from("<II", body, n - 8)
            return {
                "kind": "rect",
                "radius": radius,
                "grad_mode": mode,
                "grad_on": g_on,
                "grad_off": g_off,
                "radius_off": n - 10,
                "grad_off_off": n - 9,
            }
        if po.type_code == 0x11 and n >= 21 and body[n - 21 : n - 13] == r1:
            sa, ea = struct.unpack_from("<2h", body, n - 13)
            mode = body[n - 9]
            g_on, g_off = struct.unpack_from("<II", body, n - 8)
            return {
                "kind": "ellipse",
                "start": sa,
                "end": ea,
                "grad_mode": mode,
                "grad_on": g_on,
                "grad_off": g_off,
                "angles_off": n - 13,
                "grad_off_off": n - 9,
            }
        if po.type_code in (0x10,) and n >= 9:
            mode = body[n - 9]
            g_on, g_off = struct.unpack_from("<II", body, n - 8)
            if (g_on >> 24) & 0xFF in (0, 0xFF) and (g_off >> 24) & 0xFF in (0, 0xFF) and mode <= 8:
                return {
                    "kind": "polygon",
                    "grad_mode": mode,
                    "grad_on": g_on,
                    "grad_off": g_off,
                    "grad_off_off": n - 9,
                }
    except struct.error:
        return None
    return None


def set_corner_radius(po: PageObject, radius: int) -> bool:
    """Rect corner radius (0 square .. 32; RoundRect corner = 2*radius)."""
    tail = parse_shape_tail(po)
    if tail is None or tail["kind"] != "rect":
        return False
    body = bytearray(po.body_raw)
    body[tail["radius_off"]] = max(0, min(32, int(radius)))
    po.body_raw = bytes(body)
    return True


def ctdraw_angle(a: int) -> int:
    """An ellipse angle the way CtDraw stores it, folded into [-90, 270]."""
    a = int(a)
    while a > 270:
        a -= 360
    while a < -90:
        a += 360
    return a


def set_ellipse_angles(po: PageObject, start: int, end: int) -> bool:
    """Ellipse pie/arc start/end angles in signed degrees (stored in CtDraw's
    range, :func:`ctdraw_angle`)."""
    tail = parse_shape_tail(po)
    if tail is None or tail["kind"] != "ellipse":
        return False
    body = bytearray(po.body_raw)
    struct.pack_into("<2h", body, tail["angles_off"], ctdraw_angle(start), ctdraw_angle(end))
    po.body_raw = bytes(body)
    return True


def set_gradient(
    po: PageObject, mode: int, colour: Optional[int] = None, off_colour: Optional[int] = None
) -> bool:
    """Gradient fill: mode 0..8 (+ optional second colour, packed RGB; a
    different ``off_colour`` makes it a flashing gradient colour -- 'Gradient
    fills support flashing colors', help 1172124)."""
    tail = parse_shape_tail(po)
    if tail is None:
        return False
    body = bytearray(po.body_raw)
    off = tail["grad_off_off"]
    body[off] = max(0, min(8, int(mode)))
    if colour is not None:
        c = colour & 0xFFFFFF
        c2 = c if off_colour is None else off_colour & 0xFFFFFF
        struct.pack_into("<II", body, off + 1, c, c2)
    po.body_raw = bytes(body)
    return True


# --------------------------------------------------------------------------- text font tail
# A text record's font lives in a tail at the very END of the body (dwFlags
# bit 0x2 only): ... [string section: u32 K + flag-gated lpstrs]
# [i16 x, i16 y == geometry ANCHOR echo] [u16 orient/charset] [u8 size(points)]
# [lpstr text] [lpstr face]. The 54-byte style block sits exactly 54 bytes
# before the string section; its 'attribs' byte = justify (low nibble:
# 0 Left / 1 Right / 2 Centre) + Bold 0x10 / Italic 0x20 / Underline 0x40 /
# StrikeOut 0x80; its 'effects' low nibble = 3D text effect (0 Normal,
# 1 Raised, 2 Lowered, 3 Shadowed, 4 Embossed).

_TEXTABLE = frozenset({0x09, 0x0A, 0x0D}) | frozenset(range(0x20, 0x100))


def _lpstr_ending_at(body: bytes, end: int) -> Optional[int]:
    """Start offset of the lpstr whose payload ends exactly at ``end``."""
    for ln in range(0, min(2048, end - 2) + 1):
        start = end - 2 - ln
        if start < 0:
            return None
        if struct.unpack_from("<H", body, start)[0] == ln and all(
            b in _TEXTABLE for b in body[start + 2 : end]
        ):
            return start
    return None


def parse_text_tail(po: PageObject) -> Optional[dict]:
    """Decode a text record's font tail to a dict of values + offsets (face,
    text, size, orient, charset, block offset, anchor-echo offset), or ``None``
    when absent (no dwFlags bit 2 / parse failure)."""
    if po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return None
    body = po.body_raw
    n = len(body)
    s_face = _lpstr_ending_at(body, n)
    if s_face is None:
        return None
    s_text = _lpstr_ending_at(body, s_face)
    if s_text is None or s_text < 12:
        return None
    size = body[s_text - 1]
    orient_cs = struct.unpack_from("<H", body, s_text - 3)[0]
    charset = (orient_cs >> 8) if (orient_cs & 0x80) else DEFAULT_CHARSET
    orient = orient_cs & 0x7F
    echo = struct.unpack_from("<2h", body, s_text - 7)
    sec_end = s_text - 7
    gh = parse_geom_header(body)
    lo = gh.end if gh is not None else 0
    k_off = None
    loose = None
    # with METADATA (dwFlags 0x100) the section follows the metadata section,
    # not the style block
    md = metadata_span(po)
    md_end = md[1] if md is not None else None
    for start in range(sec_end - 4, lo - 1, -1):
        k = struct.unpack_from("<I", body, start)[0]
        if not (1 <= k <= 0x0F):
            continue
        o = start + 4
        ok = True
        for bit in (1, 2):
            if ok and (k & bit):
                if o + 2 > n:
                    ok = False
                    break
                ln = struct.unpack_from("<H", body, o)[0]
                o += 2 + ln
        for bit in (4, 8):
            if ok and (k & bit):
                if o + 2 > n:
                    ok = False
                    break
                cnt = struct.unpack_from("<H", body, o)[0]
                o += 2
                if cnt > 256:
                    ok = False
                    break
                for _ in range(cnt):
                    if o + 2 > n:
                        ok = False
                        break
                    ln = struct.unpack_from("<H", body, o)[0]
                    o += 2 + ln
        if ok and o == sec_end:
            # the saver's flag is always odd (1 / 3 / 5 / 9 by
            # TXTMODE) and the section follows the style block -- or the 0x100
            # metadata section right after it: a LATER offset that also walks
            # to the end is not the start (an empty caption + two unset states
            # '05000000 0000 0200 0000 0000' also parses as flag 2 at +6). The
            # first plain match stays the fallback.
            if (k & 1) and (
                start == md_end if md_end is not None else _block_at(body, start - 54) is not None
            ):
                k_off = start
                break
            if loose is None:
                loose = start
    if k_off is None:
        k_off = loose
    # the style block ends where the section -- or the metadata before it -- starts
    blk_end = md[0] if md_end is not None and k_off == md_end else k_off
    return {
        "face": body[s_face + 2 : n].decode(CODEC, "replace"),
        "text": body[s_text + 2 : s_face].decode(CODEC, "replace"),
        "size": size,
        "size_off": s_text - 1,
        "orient": orient,
        "charset": charset,
        "orient_off": s_text - 3,
        "echo": tuple(echo),
        "echo_off": s_text - 7,
        "s_text": s_text,
        "s_face": s_face,
        "k_off": k_off,
        "block_off": (blk_end - 54) if blk_end is not None and blk_end >= 54 else None,
    }


# ---------------------------------------------------------------------------
# Geometry-header transforms: each of the 3 slots = u8 mode + doubles named
# A,B,C,D,H,K (x' = A·x + C·y + H; y' = B·x + D·y + K, Win32 XFORM order on
# disk). Mode is a BITFIELD: 0x01 translate (H,K), 0x02 scale/flip (A,D),
# 0x04 rotate, high nibble = 90°-CW quarter-turn count; doubles serialized by
# the LOW nibble {0:0, 1:(H,K), 2:(A,D), 3:(A,D,H,K), >=4: all six}.
# Slot 1 = 'Local' (the ONLY slot the loader uses); slots 2/3 are discarded
# at load and rewritten at save, so the writer echoes slot 1 into them.
# ---------------------------------------------------------------------------


def _xform_count(mode: int) -> int:
    # stride switches on the FULL mode byte (default = 6 doubles), matching
    # parse_geom_header + page.py/reader.py, also on the high-nibble modes
    # (0x11/0x12/0x31/...) of legacy library elements.
    return _XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0)


def transform_spans(body: bytes):
    """The 3 header transform slots as ``[(mode_off, mode, n_doubles)]``."""
    o = _geom_name_len(body, 0)
    if not o:
        return None
    out = []
    for _ in range(3):
        if o >= len(body):
            return None
        mode = body[o]
        n = _xform_count(mode)
        out.append((o, mode, n))
        o += 1 + n * 8
        if o > len(body):
            return None
    return out


def get_local_transform(po: PageObject):
    """Slot 1 ('Local') as ``(mode, (A,B,C,D,H,K))``, or None when identity."""
    spans = transform_spans(po.body_raw)
    if not spans:
        return None
    off, mode, n = spans[0]
    if mode == 0 or n == 0:
        return None
    vals = struct.unpack_from("<%dd" % n, po.body_raw, off + 1)
    a, b, c, d, h, k = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    # map by the ACTUAL double count (robust to high-nibble modes whose stride is 6):
    # 2 -> (A,D) for the scale nibble, else (H,K); 4 -> (A,D,H,K); >=6 -> all six.
    if len(vals) >= 6:
        a, b, c, d, h, k = vals[:6]
    elif len(vals) == 4:
        a, d, h, k = vals
    elif len(vals) == 2:
        if mode & 0x0F == 2:
            a, d = vals
        else:
            h, k = vals
    return mode, (a, b, c, d, h, k)


def get_rotation_transform(po: PageObject):
    """The rotation / flip ``(A, B, C, D)`` in any of the transform slots, or None."""
    try:
        spans = transform_spans(po.body_raw)
    except Exception:  # noqa: BLE001
        _note_exc("geometry.get_rotation_transform")
        return None
    slots = []
    for off, mode, n in spans or ():
        if mode == 0 or n == 0:
            continue
        try:
            slots.append((mode, struct.unpack_from("<%dd" % n, po.body_raw, off + 1)))
        except struct.error:
            continue
    return rotation_from_transforms(slots)


def rotation_from_transforms(transforms):
    """:func:`get_rotation_transform` from the decoded slots ``[(mode, values), ...]`` (what
    the reader's geometry read already holds: no second walk of the header)."""
    for mode, vals in transforms or ():
        if mode == 0 or not vals:
            continue
        a, b, c, d = 1.0, 0.0, 0.0, 1.0
        if len(vals) >= 6:
            a, b, c, d = vals[0], vals[1], vals[2], vals[3]
        elif len(vals) == 4:
            a, d = vals[0], vals[1]  # (A,D,H,K) -- pure scale, no rotation
        elif len(vals) == 2 and (mode & 0x0F) == 2:
            a, d = vals  # pure scale
        if not (abs(b) > 1e-6 or abs(c) > 1e-6 or a < 0 or d < 0):
            continue  # this slot carries no rotation/flip
        # normalise out the scale -> a unit orthogonal matrix the scene can apply cleanly
        sx = (a * a + b * b) ** 0.5 or 1.0
        sy = (c * c + d * d) ** 0.5 or 1.0
        ua, ub, uc, ud = a / sx, b / sx, c / sy, d / sy
        # snap near-axis components to exact {-1,0,1} (these are quarter-turns / mirrors)
        snap = lambda v: round(v) if abs(v - round(v)) < 0.05 else v  # noqa: E731
        return (snap(ua), snap(ub), snap(uc), snap(ud))
    return None


def _doubles_for_mode(mode: int, m6) -> tuple:
    a, b, c, d, h, k = m6
    n = _xform_count(mode)  # full-byte stride (matches the read path)
    if n == 0:
        return ()
    if n == 2:
        return (a, d) if mode & 0x0F == 2 else (h, k)
    if n == 4:
        return (a, d, h, k)
    return tuple(m6)


def set_local_transforms(po: PageObject, mode: int, m6) -> bool:
    """Rewrite all three transform slots with ``mode`` + the matching subset of
    ``(A,B,C,D,H,K)`` (CtDraw writes Local=Mods=Total for top-level records; the
    loader reads only slot 1)."""
    spans = transform_spans(po.body_raw)
    if not spans:
        return False
    vals = _doubles_for_mode(mode, m6)
    blob = bytes([mode & 0xFF])
    if vals:
        blob += struct.pack("<%dd" % len(vals), *vals)
    body = po.body_raw
    for off, _old_mode, old_n in reversed(spans):
        body = body[:off] + blob + body[off + 1 + old_n * 8 :]
    po.body_raw = body
    return True


def compose_transform(m1, m2):
    """Full 2x3 multiply: apply ``m2`` AFTER ``m1`` (both (A,B,C,D,H,K))."""
    a1, b1, c1, d1, h1, k1 = m1
    a2, b2, c2, d2, h2, k2 = m2
    return (
        a2 * a1 + c2 * b1,
        b2 * a1 + d2 * b1,
        a2 * c1 + c2 * d1,
        b2 * c1 + d2 * d1,
        a2 * h1 + c2 * k1 + h2,
        b2 * h1 + d2 * k1 + k2,
    )


def set_text_orient(po: PageObject, orient: int) -> bool:
    """The text-tail u16 'orient' IS the rotation: 1=0°, 3=90°CW, 2=180°,
    4=270°CW (treat the legacy 0 as 1)."""
    tail = parse_text_tail(po)
    if tail is None:
        return False
    body = bytearray(po.body_raw)
    cur = struct.unpack_from("<H", body, tail["orient_off"])[0]
    struct.pack_into("<H", body, tail["orient_off"], (cur & 0xFF80) | (orient & 0x7F))
    po.body_raw = bytes(body)
    return True


# orient value <-> CW quarter turns
ORIENT_TO_TURNS = {0: 0, 1: 0, 3: 1, 2: 2, 4: 3}
TURNS_TO_ORIENT = {0: 1, 1: 3, 2: 2, 3: 4}


def set_text_font_size(po: PageObject, points: int) -> bool:
    """Poke the text font size byte (points, 1..255)."""
    tail = parse_text_tail(po)
    if tail is None:
        return False
    body = bytearray(po.body_raw)
    body[tail["size_off"]] = max(1, min(255, int(points)))
    po.body_raw = bytes(body)
    return True


def set_text_face(po: PageObject, face: str) -> bool:
    """Splice-replace the face lpstr at the body end (length-safe)."""
    tail = parse_text_tail(po)
    if tail is None:
        return False
    enc = face.encode(CODEC, "replace")
    po.body_raw = po.body_raw[: tail["s_face"]] + struct.pack("<H", len(enc)) + enc
    return True


#: the Windows character set of each ANSI code page (TranslateCharsetInfo TCI_SRCCODEPAGE)
CODEPAGE_CHARSETS = {
    874: 222,
    932: 128,
    936: 134,
    949: 129,
    950: 136,
    1250: 238,
    1251: 204,
    1252: 0,
    1253: 161,
    1254: 162,
    1255: 177,
    1256: 178,
    1257: 186,
    1258: 163,
    1361: 130,
}


def _system_charset() -> int:
    from .textcodec import CODEPAGE

    return CODEPAGE_CHARSETS.get(CODEPAGE, 0)


#: the system's character set (the code page's, ANSI_CHARSET 0 on cp1252): a
#: text tail without the charset flag has it (the loader), and the
#: saver stores the charset only when it differs from it
DEFAULT_CHARSET = _system_charset()


def set_text_charset(po: PageObject, charset: int) -> bool:
    """A text's Script (Windows charset). The tail u16 is the orient in the
    low 7 bits; the saver adds bit 0x80 + the
    charset in the high byte only when it is not the system default -- the
    loader reads it back the same way."""
    tail = parse_text_tail(po)
    if tail is None:
        return False
    charset = int(charset) & 0xFF
    body = bytearray(po.body_raw)
    cur = struct.unpack_from("<H", body, tail["orient_off"])[0]
    orient = cur & 0x7F
    new = orient if charset == DEFAULT_CHARSET else ((charset << 8) | 0x80 | orient)
    struct.pack_into("<H", body, tail["orient_off"], new)
    po.body_raw = bytes(body)
    return True


# --------------------------------------------------------------------------- button font block
# dwFlags 0x40 (loader): lpstr face,
# u8 charset, u8 attribs (low nibble justify 0 L / 1 R / 2 C for multi-line
# captions, Bold 0x10 / Italic 0x20 / Underline 0x40 / StrikeOut 0x80), u32 flags
# (the saver sets bit 0x80000000 when the charset is not the system default; the
# loader strips it), u32 size (POINTS), u32 text colour on / off, u32 background
# on / off (no longer used: 'The Background Color property is no longer supported
# for button fonts', help 1172135).
BUTTON_TC = 0x1001


def parse_button_font(po: PageObject) -> Optional[dict]:
    """A button's font block: face, charset, attribs, flags, size, text (on,
    off), back (on, off) and the offsets (``off`` = the face lpstr); None when
    the record has no dwFlags 0x40 section."""
    if po.type_code != BUTTON_TC or not (po.subtype_size & 0x40):
        return None
    placement = _lazy("placement")
    try:
        w = placement.walk(po)
    except Exception:  # noqa: BLE001
        _note_exc("geometry.parse_button_font")
        return None
    o = w.get("font_off")
    if o is None:
        return None
    body = po.body_raw
    try:
        (n,) = struct.unpack_from("<H", body, o)
        p = o + 2 + n
        charset, attribs = body[p], body[p + 1]
        vals = struct.unpack_from("<6I", body, p + 2)
    except struct.error, IndexError:
        return None
    return {
        "face": body[o + 2 : o + 2 + n].decode(CODEC, "replace"),
        "charset": charset,
        "attribs": attribs,
        "flags": vals[0],
        "size": vals[1],
        "text": (vals[2], vals[3]),
        "back": (vals[4], vals[5]),
        "off": o,
        "fixed_off": p,
    }


def set_button_font(
    po: PageObject,
    *,
    face: Optional[str] = None,
    size: Optional[int] = None,
    charset: Optional[int] = None,
    attribs: Optional[int] = None,
    text=None,
) -> bool:
    """Rewrite fields of a button's font block (``text`` = (on, off) packed
    RGB). The face is a length-changing splice; the rest is fixed size."""
    f = parse_button_font(po)
    if f is None:
        return False
    body = bytearray(po.body_raw)
    p = f["fixed_off"]
    if charset is not None:
        cs = int(charset) & 0xFF
        body[p] = cs
        fl = f["flags"] & 0x7FFFFFFF
        struct.pack_into("<I", body, p + 2, fl | (0x80000000 if cs != DEFAULT_CHARSET else 0))
    if attribs is not None:
        body[p + 1] = int(attribs) & 0xFF
    if size is not None:
        struct.pack_into("<I", body, p + 6, max(1, min(255, int(size))))
    if text is not None:
        on, off = text
        struct.pack_into("<II", body, p + 10, on & 0xFFFFFFFF, off & 0xFFFFFFFF)
    if face is not None:
        enc = face.encode(CODEC, "replace")
        o = f["off"]
        body = body[:o] + struct.pack("<H", len(enc)) + enc + body[p:]
    po.body_raw = bytes(body)
    refresh_bags(po)
    return True


def button_caption_lines(text: str) -> List[str]:
    """A button caption's lines: CtDraw breaks at '^n' / '^N' and CR LF
    (help 1172135 'Start^nMotor'). A lone LF
    breaks too (the editor's own multi-line entry)."""
    s = (text or "").replace("^n", "\n").replace("^N", "\n")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.split("\n")


def _button_auto_width(po: PageObject) -> bool:
    """A button sizes its width to its caption when its WIDTH property is
    absent or 0 (atoi(WIDTH) == 0)."""
    from .cnum import c_atoi

    value_bag = _lazy("writer").value_bag
    vb = value_bag(po)
    props = dict(vb) if vb else po.raw_properties()
    # C atoi, as CtDraw reads it: '12px' is 12, a non-number is 0 (auto)
    return c_atoi(props.get("WIDTH")) == 0


def refit_button(po: PageObject) -> bool:
    """Resize an auto-width button to its caption."""
    if po.type_code != BUTTON_TC or not _button_auto_width(po):
        return False
    textmetrics = _lazy("textmetrics")
    value_bag = _lazy("writer").value_bag
    vb = value_bag(po)
    props = dict(vb) if vb else po.raw_properties()
    cap = props.get("TEXT") or ""
    if cap.startswith("#"):
        return False
    f = parse_button_font(po)
    face, size, attribs, cs = (
        ("Arial", 11, 0, 0)
        if f is None
        else (f["face"] or "Arial", f["size"] or 11, f["attribs"], f["charset"])
    )
    tw = 0
    for line in button_caption_lines(cap) or [" "]:
        try:
            ex = textmetrics.extent(line or " ", face, size, attribs, 1, cs, (0, 0))
        except Exception:  # noqa: BLE001
            _note_exc("geometry.refit_button")
            ex = None
        if ex is None:
            return False
        tw = max(tw, ex[2] - ex[0])
    gh = parse_geom_header(po.body_raw)
    if gh is None:
        return False
    l, t, r, b = norm_rect(gh.rect1)
    w = max(21, tw + 11)
    if r - l == w:
        return False
    old_flags = style_flags(po)
    new_r1 = (l, t, l + w, b)
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, gh.rect1_off, *new_r1)
    if old_flags is not None and bytes(body[len(body) - 8 :]) == struct.pack("<4h", *gh.rect1):
        struct.pack_into("<4h", body, len(body) - 8, *new_r1)  # the native rect echo
    po.body_raw = bytes(body)
    if old_flags is not None:
        rebound(po, gh.rect1, old_flags, new_r1, old_flags)
    else:
        l2, t2, r2, b2 = gh.rect2
        _set_rect2(po, (l2, t2, r2 + (l + w - r), b2))
    return True


def refit_text(po: PageObject) -> bool:
    """Write the extent the engine measures for a free text record (rect1 =
    :func:`textmetrics.text_extent`, rect2 = its Bounds). False when it
    cannot be derived here (the stored box is kept)."""
    textmetrics = _lazy("textmetrics")
    try:
        e = textmetrics.text_extent(po)
    except Exception:  # noqa: BLE001
        _note_exc("geometry.refit_text")
        e = None
    if e is None:
        return False
    gh = parse_geom_header(po.body_raw)
    flags = style_flags(po)
    if gh is None or flags is None:
        return False
    r2 = predict_bounds(0x05, e, flags) or e
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, gh.rect1_off, *(int(v) for v in e))
    struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in r2))
    po.body_raw = bytes(body)
    return True


def resize_text(
    po: PageObject, new_rect, anchor=None, held=None, per_entry: bool = True, stretch: bool = True
) -> bool:
    """CtDraw's text resize."""
    tail = parse_text_tail(po)
    gh = parse_geom_header(po.body_raw)
    if tail is None or gh is None:
        return False
    return scale_text(
        po,
        box_map(norm_rect(gh.rect1), norm_rect(new_rect)),
        anchor=anchor,
        held=held,
        per_entry=per_entry,
        stretch=stretch,
    )


def box_map(old, new):
    """The page map (kx, 0, 0, ky, tx, ty) of box ``old`` onto box ``new`` (normalised); an
    axis without extent in either box keeps its scale (a text resized to no width or height:
    :func:`keep_collapsed_axes`)."""
    ol, ot, orr, ob = old
    nl, nt, nr, nb = new
    kx = (nr - nl) / (orr - ol) if orr > ol and nr > nl else 1.0
    ky = (nb - nt) / (ob - ot) if ob > ot and nb > nt else 1.0
    return (kx, 0.0, 0.0, ky, nl - kx * ol, nt - ky * ot)


def keep_collapsed_axes(M6, old):
    """Keep collapsed axes of ``M6`` at their old scale."""
    a, b, c, d, h, k = M6
    if abs(b) > 1e-12 or abs(c) > 1e-12:
        return tuple(M6)
    if abs(a) < 1e-9:
        a, h = 1.0, h - old[0]
    if abs(d) < 1e-9:
        d, k = 1.0, k - old[1]
    return (a, b, c, d, h, k)


#: CtDraw's tolerance (0.05): a transform is 'uniform' while it changes
#: the aspect of the object's box by less than this
UNIFORM_ASPECT_TOLERANCE = 0.05


def text_transform_bakes(m6, box) -> bool:
    """Does CtDraw bake the Local ``m6`` into a text?"""
    a, b, c, d = m6[:4]
    if a * d - b * c <= 0:
        return False
    w, h = box[2] - box[0], box[3] - box[1]
    eps = UNIFORM_ASPECT_TOLERANCE
    top = math.hypot(a * w, b * w)
    if abs(w) < eps or top < eps:
        return abs(w) < eps and top < eps
    return abs(h / w - math.hypot(c * h, d * h) / top) < eps


def exact_box(M6, box):
    """The bounding box of ``box``'s corners through the affine ``M6``, floats (no
    truncation)."""
    a, b, c, d, h, k = M6
    pts = [(a * x + c * y + h, b * x + d * y + k) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def local_box(L, box):
    """The page ``box`` in the frame of the Local ``L`` (its corners through L^-1), floats."""
    return exact_box(_affine_inverse(L), box)


def compose_text_map(L, mode, M6, per_entry: bool = False):
    """Compose a resize onto a text's Local."""
    placement = _lazy("placement")
    if per_entry and abs(M6[1]) < 1e-12 and abs(M6[2]) < 1e-12:
        kx, _b, _c, ky, tx, ty = M6
        a, b, c, d, h, k = L if mode else placement.IDENT
        return (kx * a, ky * b, kx * c, ky * d, kx * h + tx, ky * k + ty), mode | 0x0F
    return placement.compose_after(L, mode, tuple(M6), 0x0F)


def _text_local(po: PageObject, held=None):
    """The (mode, L) a text's resize composes onto: ``held`` (the Local in memory) or its
    slot 1."""
    if held is not None:
        return held[0], tuple(held[1])
    return get_local_transform(po) or (0, _lazy("placement").IDENT)


def _top_level_text(po: PageObject) -> bool:
    """The text's three slots hold one transform (no parent's in its Total)."""
    try:
        (m1, L, _o1, _n1), (m2, S2, _o2, _n2), (m3, T, _o3, _n3) = _lazy("placement").read_slots(po.body_raw)[
            0
        ]
    except Exception:  # noqa: BLE001
        _note_exc("geometry._top_level_text")
        return False
    return m1 == m2 == m3 and all(abs(x - y) < 1e-9 and abs(y - z) < 1e-9 for x, y, z in zip(L, S2, T))


def _text_scale_plan(po: PageObject, M6, held=None, per_entry: bool = False, stretch: bool = True):
    """How CtDraw applies the page map ``M6`` to a text."""
    gh = parse_geom_header(po.body_raw)
    if gh is None:
        return None
    old = norm_rect(gh.rect1)
    keeps = text_keeps_matrix(po)
    m0, L0 = _text_local(po, held)
    L2 = compose_text_map(L0, m0, M6, per_entry)[0]
    if keeps and not _top_level_text(po):
        return "matrix", L2  # (a parent's transform in its Total: kept)
    if not keeps and not stretch and L2[0] * L2[3] - L2[1] * L2[2] > 0:
        return "bake", L2  # (Settings > Editing: no stretched texts)
    if keeps and not stretch and not text_transform_bakes(M6, old):
        return "bake resize", L2  # (the same for a mirrored or stretched text)
    if not text_transform_bakes(L2, local_box(L0, old)):
        return "matrix", L2
    if not keeps:
        return "bake", L2
    return ("bake matrix" if _unit_is_identity(L2) else "matrix"), L2


def text_resize_bakes(po: PageObject, new_rect, held=None, from_box=None, stretch: bool = True) -> bool:
    """True when a resize of the text to ``new_rect`` is baked into it (:func:`resize_text`,
    :func:`text_transform_bakes`), False when it keeps a matrix. ``from_box`` = the box the
    resize starts from (its extent, rect1, by default)."""
    gh = parse_geom_header(po.body_raw)
    if gh is None:
        return True
    old = norm_rect(from_box) if from_box is not None else norm_rect(gh.rect1)
    plan = _text_scale_plan(po, box_map(old, norm_rect(new_rect)), held, True, stretch)
    return plan is None or plan[0] != "matrix"


def _unit_is_identity(m6) -> bool:
    """The matrix's columns point along the axes (a scale, no turn or flip)."""
    a, b, c, d = m6[:4]
    return a > 0 and d > 0 and abs(b) < 1e-9 and abs(c) < 1e-9


def scale_text(
    po: PageObject, M6, anchor=None, held=None, per_entry: bool = False, stretch: bool = True
) -> bool:
    """Scale a text by the page map ``M6``, as CtDraw does."""
    tail = parse_text_tail(po)
    gh = parse_geom_header(po.body_raw)
    if tail is None or gh is None or po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return False
    old = norm_rect(gh.rect1)
    kept = keep_collapsed_axes(M6, old)
    if kept != tuple(M6):
        M6, anchor = kept, None  # (the caller's anchor went with the collapsed map)
    kind, L2 = _text_scale_plan(po, M6, held, per_entry, stretch)
    if kind == "matrix":
        return scale_text_matrix(po, M6, held=held, per_entry=per_entry)
    if kind == "bake resize":
        return _bake_resize_keep_matrix(po, M6, held, per_entry, old) or scale_text_matrix(
            po, M6, held=held, per_entry=per_entry
        )
    s = math.hypot(L2[2], L2[3])
    if kind == "bake matrix":
        return _bake_text_matrix(po, L2, s, M6, old)
    if not stretch and held is None and tail["orient"] in (3, 4):
        s = math.hypot(L2[0], L2[1])  # (no stretch: a vertical text's height is across)
    return _bake_plain_text(po, M6, s, anchor)


def _bake_resize_keep_matrix(po: PageObject, M6, held, per_entry: bool, old) -> bool:
    """Apply an uneven resize to a text that keeps a matrix, without stretching it."""
    placement = _lazy("placement")
    tail = parse_text_tail(po)
    m0, L0 = _text_local(po, held)
    a, b, c, d = L0[:4]
    gx, gy = (1.0, 0.0) if tail["orient"] in (3, 4) else (0.0, 1.0)
    vx, vy = a * gx + c * gy, b * gx + d * gy
    n = math.hypot(vx, vy)
    A, B, C, D = M6[:4]
    s = math.hypot(A * vx + C * vy, B * vx + D * vy) / n if n > 1e-12 else 1.0
    L2 = compose_text_map(L0, m0, M6, per_entry)[0]
    ex, ey = tail["echo"]
    px, py = L2[0] * ex + L2[2] * ey + L2[4], L2[1] * ex + L2[3] * ey + L2[5]
    L1 = (a, b, c, d, px - (a * ex + c * ey), py - (b * ex + d * ey))
    if not bake_text_scale(po, s):
        return False
    placement.write_slots(po, [((m0 | 0x01) if m0 else 0x0F, L1)] * 3)
    relayout_text(po, fallback=(old, tuple(M6)))
    return True


def fit_new_text(po: PageObject, rect) -> bool:
    """A NEW text put in ``rect`` (factory.create_object): its point size scaled by the
    box's height against its seed's, the anchor mapped, the box measured again; never a
    matrix (this is no resize: CtDraw draws a new text at a point in its font). False when
    the record is not a text with a tail."""
    gh = parse_geom_header(po.body_raw)
    if po.type_code != 0x05 or not (po.subtype_size & 0x2) or gh is None or parse_text_tail(po) is None:
        return False
    M6 = box_map(norm_rect(gh.rect1), norm_rect(rect))
    if not _bake_plain_text(po, M6, M6[3]):
        _retarget_header(po, norm_rect(gh.rect1), norm_rect(rect))  # (no text metrics)
    return True


def _bake_plain_text(po: PageObject, M6, s: float, anchor=None) -> bool:
    """CtDraw's bake of a text without a matrix: the anchor (and the echo) mapped through
    ``M6`` (``anchor`` when the caller has it), the point size MulDiv(trunc(px * s), 72,
    LOGPIXELSY), the box measured again (without text metrics: False, nothing changed)."""
    tail = parse_text_tail(po)
    gh = parse_geom_header(po.body_raw)
    old = norm_rect(gh.rect1)
    textmetrics = _lazy("textmetrics")
    g = textmetrics._Gdi.get()
    if g is None:
        return False
    ax, ay = tail["echo"]
    kx, b, c, ky, tx, ty = M6
    # the drag transform as the engine applies it (double matrix, truncated)
    if anchor is None:
        anchor = (int(kx * ax + c * ay + tx), int(b * ax + ky * ay + ty))
    anchor = (int(anchor[0]), int(anchor[1]))
    px = (g.logpixelsy * tail["size"]) // 72
    size = max(1, min(255, (int(px * s) * 72 + g.logpixelsy // 2) // g.logpixelsy))
    body = bytearray(po.body_raw)
    struct.pack_into("<2h", body, gh.anchor_off, *anchor)
    struct.pack_into("<2h", body, tail["echo_off"], *anchor)
    body[tail["size_off"]] = size
    po.body_raw = bytes(body)
    if not refit_text(po):
        _retarget_header(po, old, _map_box(M6, old))
    return True


def _bake_text_matrix(po: PageObject, L2, s: float, M6, old) -> bool:
    """A text whose matrix comes to the uniform, unmirrored, unturned ``L2``: the scale ``s``
    baked into its point size, the slots the identity, the anchor and the echo
    trunc(L2(echo)), the box measured again."""
    tail = parse_text_tail(po)
    a, b, c, d, h, k = L2
    ex, ey = tail["echo"]
    anchor = (int(a * ex + c * ey + h), int(b * ex + d * ey + k))
    bake_text_scale(po, s)
    _lazy("placement").write_slots(po, [(0, _lazy("placement").IDENT)] * 3)
    gh = parse_geom_header(po.body_raw)
    tail = parse_text_tail(po)
    body = bytearray(po.body_raw)
    struct.pack_into("<2h", body, gh.anchor_off, *anchor)
    struct.pack_into("<2h", body, tail["echo_off"], *anchor)
    po.body_raw = bytes(body)
    if not refit_text(po):
        _retarget_header(po, old, _map_box(M6, old))
    return True


def _update_text_echo(po: PageObject, body: bytearray, new_anchor) -> None:
    """Keep the text tail's anchor echo in step on move/resize."""
    if po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return
    tail = parse_text_tail(po)
    if tail is not None:
        struct.pack_into("<2h", body, tail["echo_off"], int(new_anchor[0]), int(new_anchor[1]))


def text_total(po: PageObject):
    """The (A,B,C,D,H,K) Total (slot 3) of a text that carries a transform
    -- a MIRRORED text (CtDraw keeps a text's flip as a matrix: Local = Mods
    = Total) -- else None."""
    if po.type_code != 0x05:
        return None
    read_slots = _lazy("placement").read_slots
    try:
        m6 = read_slots(po.body_raw)[0][2][1]
    except Exception:  # noqa: BLE001
        _note_exc("geometry.text_total")
        return None
    if all(abs(v - w) < 1e-9 for v, w in zip(m6, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))):
        return None
    return m6


def _inverse_point(m6, x, y):
    a, b, c, d, h, k = m6
    det = a * d - b * c
    x, y = x - h, y - k
    return (d * x - c * y) / det, (-b * x + a * y) / det


def sync_text_echo(po: PageObject) -> bool:
    """Keep a text's tail anchor echo in its local frame."""
    tail = parse_text_tail(po)
    gh = parse_geom_header(po.body_raw) if tail is not None else None
    if gh is None:
        return False
    m6 = text_total(po) or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # identity: echo = anchor
    if abs(m6[0] * m6[3] - m6[1] * m6[2]) < 1e-12:
        return False
    a, b, c, d, h, k = m6
    anchor = tuple(gh.anchor)

    def lays_out(p):
        # CtDraw's anchor = trunc(Total(echo))
        return (int(a * p[0] + c * p[1] + h), int(b * p[0] + d * p[1] + k)) == anchor

    if lays_out(tail["echo"]):
        return False  # (the echo the anchor came from stays)
    ex, ey = _inverse_point(m6, *anchor)
    echo = (int(round(ex)), int(round(ey)))
    # with a half-pixel offset, Total^-1(anchor) falls on a half pixel, which a rounding
    # may take to the wrong side: the echo is the whole point whose Total truncates to the
    # anchor
    near = [(fx(ex), fy(ey)) for fx in (math.floor, math.ceil) for fy in (math.floor, math.ceil)]
    echo = next((p for p in [echo] + near if lays_out(p)), echo)
    if echo == tuple(tail["echo"]):
        return False
    body = bytearray(po.body_raw)
    struct.pack_into("<2h", body, tail["echo_off"], *echo)
    po.body_raw = bytes(body)
    return True


def _linear_is_identity(m6) -> bool:
    return all(abs(v - w) < 1e-9 for v, w in zip(m6[:4], (1.0, 0.0, 0.0, 1.0)))


def text_keeps_matrix(po: PageObject) -> bool:
    """Does this text keep a matrix (e.g. a mirror)?"""
    if po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return False
    m6 = text_total(po)
    return m6 is not None and not _linear_is_identity(m6) and parse_text_tail(po) is not None


def text_scale(po: PageObject):
    """(sx, sy) font scale of a text that keeps a matrix, or None at (1, 1)."""
    if not text_keeps_matrix(po):
        return None
    a, b, c, d = text_total(po)[:4]
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6:
        return None
    return (sx, sy)


def relayout_text(po: PageObject, fallback=None) -> None:
    """Lay a text that keeps a matrix out from its echo through its Total: the anchor
    trunc(Total(echo)), the box measured at the echo and mapped (:func:`refit_text`). Its
    font and echo stay. ``fallback`` = (old box, page map): without text metrics the old box
    goes where the map puts it."""
    if text_total(po) is None or parse_text_tail(po) is None:
        return
    if not refit_text(po) and fallback is not None:
        old, M6 = fallback
        _retarget_header(po, old, _map_box(M6, old))
    tail = parse_text_tail(po)
    gh = parse_geom_header(po.body_raw)
    a, b, c, d, h, k = text_total(po)
    ex, ey = tail["echo"]
    body = bytearray(po.body_raw)
    struct.pack_into("<2h", body, gh.anchor_off, int(a * ex + c * ey + h), int(b * ex + d * ey + k))
    po.body_raw = bytes(body)


def uniform_text_scale(m6, box=None):
    """Split a uniform-scale matrix into (scale, unit matrix)."""
    a, b, c, d = m6[:4]
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if sy < 1e-12 or sx < 1e-12 or a * d - b * c <= 0:
        return None
    if box is not None:
        if _linear_is_identity(m6) or not text_transform_bakes(m6, box):
            return None
    elif abs(sy - 1.0) < 1e-9 or abs(sx / sy - 1.0) >= 0.05:
        return None
    unit = (a / sx, b / sx, c / sy, d / sy)
    if any(abs(v - round(v)) > 1e-9 for v in unit):
        return None
    return sy, tuple(float(round(v)) for v in unit)


def bake_text_scale(po: PageObject, scale: float) -> bool:
    """A text's point size times ``scale`` the way CtDraw bakes it:
    MulDiv(trunc(px * scale), 72, LOGPIXELSY), px = LOGPIXELSY * points / 72. False without
    text metrics or a tail."""
    g = _lazy("textmetrics")._Gdi.get()
    tail = parse_text_tail(po)
    if g is None or tail is None:
        return False
    px = (g.logpixelsy * tail["size"]) // 72
    return set_text_font_size(po, (int(px * scale) * 72 + g.logpixelsy // 2) // g.logpixelsy)


def _affine_inverse(A):
    a, b, c, d, h, k = A
    det = a * d - b * c
    if abs(det) < 1e-12:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * h + ic * k), -(ib * h + id_ * k))


def _map_box(M6, box):
    a, b, c, d, h, k = M6
    xs = [a * x + c * y + h for x in (box[0], box[2]) for y in (box[1], box[3])]
    ys = [b * x + d * y + k for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def scale_text_matrix(po: PageObject, M6, held=None, per_entry: bool = False) -> bool:
    """Compose a resize onto a text that keeps a matrix."""
    placement = _lazy("placement")
    if po.type_code != 0x05 or parse_text_tail(po) is None:
        return False
    old = norm_rect(parse_geom_header(po.body_raw).rect1)
    (m1, L, _o1, _n1), (m2, S2, _o2, _n2), (m3, T, _o3, _n3) = placement.read_slots(po.body_raw)[0]
    ident = placement.IDENT
    L0, S20, T0 = (L if m1 else ident), (S2 if m2 else ident), (T if m3 else ident)

    def same(A, B):
        return all(abs(x - y) < 1e-9 for x, y in zip(A, B))

    if same(T0, S20):
        Mloc = tuple(M6)
    else:
        # the parent part P of the Total (Total = P o slot 2): the map in the parent's frame
        P = placement.affine_mul(T0, _affine_inverse(S20))
        Mloc = placement.affine_mul(_affine_inverse(P), placement.affine_mul(M6, P))
    Lh, mh = (tuple(held[1]), held[0]) if held is not None else (L0, m1)
    L1, n1 = compose_text_map(Lh, mh, Mloc, per_entry)
    if same(S20, L0) and m2 == m1:
        S21, n2 = L1, n1
    else:
        S21, n2 = placement.compose_after(S20, m2, Mloc, 0x0F)
    if same(T0, S20) and m3 == m2:
        T1, n3 = S21, n2
    else:
        T1, n3 = placement.compose_after(T0, m3, tuple(M6), 0x0F)
    placement.write_slots(po, [(n1, L1), (n2, S21), (n3, T1)])
    relayout_text(po, fallback=(old, tuple(M6)))
    return True


# --------------------------------------------------------------------------- colours
@dataclass
class ColourBlock:
    """The 54-byte binary style/colour block: 6 u8 flags + 6 (on, off) COLORREF."""

    offset: int  # offset of the 6 flag bytes within body_raw
    flags: bytes
    slots: List[Tuple[int, int]]  # 6 x (on, off)


# slot roles, in CtDraw's own words
# ("line col", "fill col", "bgnd col", "hilight",
# "lolight", "shadow").
SLOT_LINE = 0
SLOT_FILL = 1
SLOT_BACK = 2
SLOT_HILIGHT = 3
SLOT_LOLIGHT = 4
SLOT_SHADOW = 5

# the 6 flag bytes ahead of the pairs: [effects, line_style, fill_style,
# attribs, line_width, effect_depth].
FLAG_EFFECTS = 0
FLAG_LINE_STYLE = 1
FLAG_FILL_STYLE = 2
FLAG_ATTRIBS = 3
FLAG_LINE_WIDTH = 4
FLAG_EFFECT_DEPTH = 5


def set_colour_block_flag(po: PageObject, flag: int, value: int) -> bool:
    """Patch one of the 6 style flag bytes (in place, fixed size). A width /
    3D-effect / border-style change also re-derives rect2 (the Bounds depend
    on them -- see :func:`predict_bounds`)."""
    cb = find_colour_block(po)
    if cb is None or not (0 <= flag < 6):
        return False
    old_flags = bytes(cb.flags)
    body = bytearray(po.body_raw)
    body[cb.offset + flag] = value & 0xFF
    po.body_raw = bytes(body)
    if flag == FLAG_FILL_STYLE:
        sync_state0_pair(po)
    if flag in (FLAG_EFFECTS, FLAG_ATTRIBS, FLAG_LINE_WIDTH, FLAG_EFFECT_DEPTH):
        gh = parse_geom_header(po.body_raw)
        if gh is not None:
            rebound(po, gh.rect1, old_flags, gh.rect1, bytes(po.body_raw[cb.offset : cb.offset + 6]))
    return True


def _block_at(body: bytes, off: int) -> Optional[ColourBlock]:
    """Read + validate the 54-byte block at ``off`` (colour high bytes only)."""
    if off < 0 or off + 54 > len(body):
        return None
    slots = []
    oo = off + 6
    for _ in range(6):
        on, of = struct.unpack_from("<II", body, oo)
        oo += 8
        if (on >> 24) & 0xFF not in (0x00, 0xFF) or (of >> 24) & 0xFF not in (0x00, 0xFF):
            return None
        slots.append((on, of))
    return ColourBlock(off, bytes(body[off : off + 6]), slots)


def metadata_span(po: PageObject) -> Optional[Tuple[int, int]]:
    """(start, end) of a record's metadata section in ``body_raw``."""
    if not (po.subtype_size & 0x100):
        return None
    colourstate = _lazy("colourstate")
    try:
        L = colourstate.layout(po)
    except Exception:  # noqa: BLE001
        _note_exc("geometry.metadata_span")
        L = None
    if L is None:
        return None
    b = po.body_raw
    s = L["style_at"] + 54 if "style_at" in L else L["end"]
    o = s
    try:
        (n,) = struct.unpack_from("<H", b, o)
        o += 2
        for _ in range(2 * n):
            (ln,) = struct.unpack_from("<H", b, o)
            o += 2 + ln
    except struct.error:
        return None
    return (s, o) if o <= len(b) else None


def find_colour_block_exact(po: PageObject) -> Optional[ColourBlock]:
    """Locate the style block exactly (records with dwFlags 0x2)."""
    if not (po.subtype_size & 0x2):
        return None
    span = metadata_span(po)
    if span is not None:
        s, e = span
        view = copy.copy(po)
        view.body_raw = po.body_raw[:s] + po.body_raw[e:]
        view.subtype_size = po.subtype_size & ~0x100
        cb = find_colour_block_exact(view)
        return cb if cb is not None and cb.offset + 54 <= s else None
    body = po.body_raw
    n = len(body)
    gh = parse_geom_header(body)
    if gh is None:
        return None
    r1 = struct.pack("<4h", *gh.rect1)
    if po.type_code == 0x01:
        # a LINE's 8-byte endpoint echo stores the two points in DRAW order = ANY of the 4 corner
        # permutations of the NORMALISED rect1 (the line can run L->R/R->L, T->B/B->T, or either
        # diagonal). The style block (colour/width) sits 54 bytes before the echo.
        lx, ty, rx, by = gh.rect1
        nl, nt, nr, nb = min(lx, rx), min(ty, by), max(lx, rx), max(ty, by)
        echo = body[n - 8 : n]
        for p in ((nl, nt, nr, nb), (nr, nb, nl, nt), (nr, nt, nl, nb), (nl, nb, nr, nt)):
            if echo == struct.pack("<4h", *p):
                return _block_at(body, n - 62)
        return None
    if po.type_code == 0x0F:
        cand = _block_at(body, n - 72)
        if cand is not None and body[n - 18 : n - 10] == r1:
            return cand
        for off in range(n - 62, gh.end - 1, -1):
            if body[off + 54 : off + 62] == r1:
                cand = _block_at(body, off)
                if cand is not None:
                    return cand
        return None
    if po.type_code == 0x11:
        # ellipse: 21-byte variant tail (extent echo + angles + gradient)
        if body[n - 21 : n - 13] == r1:
            return _block_at(body, n - 75)
        return None
    if po.type_code in (0x0A, 0x0B, 0x10, FREEHAND_TC):
        na = find_node_array(po)
        if na is not None:
            return _block_at(body, na.offset - 54)
    if po.type_code == 0x05:
        tail = parse_text_tail(po)
        if tail is not None and tail["block_off"] is not None:
            return _block_at(body, tail["block_off"])
        return None
    if po.type_code == 0x1001:
        # button: rect1 echo in the last 8 bytes, block right before it
        if body[n - 8 : n] == r1:
            return _block_at(body, n - 62)
    return None


def find_colour_block(po: PageObject) -> Optional[ColourBlock]:
    """Locate the 54-byte style block."""
    if not (po.subtype_size & 0x2):
        return None
    colourstate = _lazy("colourstate")
    L = colourstate.layout(po)
    if L is not None and "style_at" in L:
        cb = _block_at(po.body_raw, L["style_at"])
        if cb is not None:
            return cb
    try:
        return find_colour_block_exact(po)
    except Exception:  # noqa: BLE001
        _note_exc("geometry.find_colour_block")
        return None


def sync_state0_pair(po: PageObject) -> bool:
    """Re-derive the whole colour-state array from the style block + bags the
    way the saver does (the loader DISCARDS the stored state-0
    pair) -- see :func:`colourstate.rebuild`."""
    colourstate = _lazy("colourstate")
    return colourstate.rebuild(po)


def find_trailing_colour_pair(po: PageObject) -> Optional[int]:
    """Offset of the trailing static colour pair, or None."""
    body = po.body_raw
    if len(body) < 9:
        return None
    pos = len(body) - 8
    on, off = struct.unpack_from("<II", body, pos)
    if on != off or (on >> 24) & 0xFF not in (0x00, 0xFF):
        return None
    # An all-zero (0,0) "pair" at the END of an art-stripped RECTANGLE or ELLIPSE is the
    # zeroed transform echo ('...mode 1, H 0, K 0'), not a real face colour. A genuine
    # static-black RECT/ELLIPSE carries a style block, so the no-style-block path never has a
    # legit (0,0) face. Polygons/lines KEEP the (0,0) pair: they can be legitimately black.
    if on & 0x00FFFFFF == 0 and getattr(po, "type_code", 0) in (0x0F, 0x11):
        return None
    u8_flag = body[pos - 1] == 1
    u32_flag = pos >= 4 and struct.unpack_from("<I", body, pos - 4)[0] == 1
    if u8_flag or u32_flag:
        return pos
    return None


def trailing_colour_run(po: PageObject) -> Optional[int]:
    """State-0 colour of the trailing colour-state array, or None."""
    body = po.body_raw
    n = len(body)
    if n < 28:
        return None
    off = n - 4
    cnt = 0
    while off >= 0:
        (d,) = struct.unpack_from("<I", body, off)
        if (d >> 24) & 0xFF in (0x00, 0xFF) and (d & 0xFFFFFF) >= 0x1000:
            cnt += 1
            off -= 4
        else:
            break
    if cnt < 6 or off < 0:
        return None
    (marker,) = struct.unpack_from("<I", body, off)
    if marker != 0x23:
        return None
    (state0,) = struct.unpack_from("<I", body, off + 4)
    return state0


def refresh_bags(po: PageObject) -> None:
    _lazy("writer").refresh_object_bags(po)
