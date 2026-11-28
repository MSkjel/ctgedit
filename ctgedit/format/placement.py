"""Where an object is: placed symbols and class extents."""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from . import geometry as G

# (neither imports this module: no cycle; a per-call import on the per-record paths costs
# microseconds under PySide6's import hook)
from . import colourstate, legacy
from .page import PageObject
from .textcodec import CODEC
from ..infra.logsetup import note_exception as _note_exc
from . import forest as _forest

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_XD = {0: 0, 1: 2, 2: 2, 3: 4}
BITMAP_TC = 0x07
BAR_TC, SLIDER_TC, ACTIVEX_TC = 0x1002, 0x100F, 0x0D


# ----------------------------------------------------------------- slots
def _n_doubles(mode: int) -> int:
    return _XD.get(mode, 6 if mode > 3 else 0)


#: mode byte -> its doubles (_n_doubles), and the readers of 2 / 4 / 6 doubles
_SLOT_DOUBLES = tuple(_n_doubles(m) for m in range(256))
_U16 = struct.Struct("<H").unpack_from
_D2 = struct.Struct("<2d").unpack_from
_D4 = struct.Struct("<4d").unpack_from
_D6 = struct.Struct("<6d").unpack_from


def read_slots(body: bytes):
    """([(mode, (a,b,c,d,h,k), offset, nbytes) x3], end_offset)."""
    off = 4
    ln = _U16(body, off)[0]
    off += 2 + ln
    out = []
    for _ in range(3):
        mode = body[off]
        n = _SLOT_DOUBLES[mode]
        a, b, c, d, h, k = IDENT
        if n == 6:
            a, b, c, d, h, k = _D6(body, off + 1)
        elif n == 4:
            a, d, h, k = _D4(body, off + 1)
        elif n == 2:
            if mode & 0x0F == 2:
                a, d = _D2(body, off + 1)
            else:
                h, k = _D2(body, off + 1)
        out.append((mode, (a, b, c, d, h, k), off, 1 + 8 * n))
        off += 1 + 8 * n
    return out, off


#: read_slots_of()'s per-record memo: ``(body_raw, slots tuple, end)``, kept only for an
#: immutable bytes body and valid while the record holds that same body object
SLOTS_MEMO_ATTR = "_slots_memo"


def read_slots_of(po):
    """:func:`read_slots` of the record's body (a new list each call; raises as it does),
    kept on the record: a derive and the page model read a record's slots up to four
    times."""
    body = po.body_raw
    d = getattr(po, "__dict__", None) if type(body) is bytes else None
    if d is not None:
        m = d.get(SLOTS_MEMO_ATTR)
        if m is not None and m[0] is body:
            return list(m[1]), m[2]
    slots, end = read_slots(body)
    if d is not None:
        d[SLOTS_MEMO_ATTR] = (body, tuple(slots), end)
    return slots, end


def encode_slot(mode: int, m6) -> bytes:
    a, b, c, d, h, k = m6
    n = _n_doubles(mode)
    if n == 0:
        vals = ()
    elif n == 2:
        vals = (a, d) if mode & 0x0F == 2 else (h, k)
    elif n == 4:
        vals = (a, d, h, k)
    else:
        vals = (a, b, c, d, h, k)
    return bytes([mode]) + (struct.pack("<%dd" % n, *vals) if n else b"")


def compose_after(out, mode_out, L, mode_L):
    """CtDraw's composition: out := L o out (by L's mode byte)."""
    a, b, c, d, h, k = out
    A, B, C, D, H, K = L
    if mode_L == 0:
        return out, mode_out
    if mode_out == 0:
        return L, mode_L
    if mode_L == 1:
        return (a, b, c, d, H + h, K + k), mode_out | mode_L
    if mode_L == 2:
        return (A * a, D * b, A * c, d * D, A * h, D * k), mode_out | mode_L
    if mode_L == 3:
        return (A * a, D * b, A * c, d * D, A * h + H, D * k + K), mode_out | mode_L
    return (
        (C * b + A * a),
        (B * a + D * b),
        (C * d + A * c),
        (B * c + D * d),
        (C * k + A * h + H),
        (D * k + B * h + K),
    ), mode_out | mode_L


def _post_translate(out, mode_out, tx, ty):
    a, b, c, d, h, k = out
    if mode_out != 0:
        tx, ty = tx + h, ty + k
    return (a, b, c, d, tx, ty), mode_out | 1


def slot2_for_linked(L, mode_L, e):
    """Local o T(-e), computed the way CtDraw does."""
    out, m = IDENT, 0
    ex, ey = e
    if ex != 0 or ey != 0:
        out, m = _post_translate(out, m, float(-ex), float(-ey))
    return compose_after(out, m, L, mode_L)


def apply_rect(M, mode, rect):
    """transform the 4 corners, bbox (by the FULL mode)."""
    l, t, r, b = (float(v) for v in rect)
    A, B, C, D, H, K = M
    res = []
    for x, y in ((l, t), (r, t), (l, b), (r, b)):
        if mode == 0:
            res.append((x, y))
        elif mode == 1:
            res.append((x + H, y + K))
        elif mode == 2:
            res.append((A * x, D * y))
        elif mode == 3:
            res.append((A * x + H, y * D + K))
        else:
            res.append((y * C + x * A + H, x * B + y * D + K))
    xs = [p[0] for p in res]
    ys = [p[1] for p in res]
    return [min(xs), min(ys), max(xs), max(ys)]


def derive_geom(total, mode, N):
    R = apply_rect(total, mode, N)
    rect1 = tuple(int(v) for v in R)  # truncated toward zero
    return rect1, (int(R[0]), int(R[3] - 1.0)), R


def write_slots(po: PageObject, slots) -> None:
    body = po.body_raw
    old, end = read_slots(body)
    start = old[0][2]
    blob = b"".join(encode_slot(m, x) for m, x in slots)
    po.body_raw = body[:start] + blob + body[end:]


def write_header_geom(po: PageObject, anchor, rect1, rect2) -> None:
    """Write a record's geometry header in place: its anchor point, rect1 and rect2
    (geometry.parse_geom_header's offsets; the transform slots are not touched)."""
    body = bytearray(po.body_raw)
    gh = G.parse_geom_header(bytes(body))
    struct.pack_into("<2h", body, gh.anchor_off, *(int(v) for v in anchor))
    struct.pack_into("<4h", body, gh.rect1_off, *(int(v) for v in rect1))
    struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in rect2))
    po.body_raw = bytes(body)


def slot3(po: PageObject):
    """(mode, m6) of the record's slot 3 (its Total)."""
    (s1, s2, s3), _e = read_slots(po.body_raw)
    return s3[0], s3[1]


# ----------------------------------------------------------------- strict walk
def _lp(body, o):
    n = struct.unpack_from("<H", body, o)[0]
    return body[o + 2 : o + 2 + n], o + 2 + n


def walk(po: PageObject) -> dict:
    """Strict forward walk after the geometry header (loader
    order): [link lpstr if dw&1] [anim section if dw&8: u16 N, (N+1) x 3 x
    (u16 size, u16 count, pairs)] [bit 0x40 section] [bit 0x80 lpstr]
    [colour-state u32 FLAG, bit-0x1 state-0 pair ...]."""
    body = po.body_raw
    gh = G.parse_geom_header(body)
    if gh is None:
        return {}  # (a record too short for a geometry header)
    o = gh.end
    out = {"geom_end": o}
    dw = po.subtype_size
    if dw & 1:
        s, o2 = _lp(body, o)
        out["link"] = (o, s.decode(CODEC))
        o = o2
    if dw & 8:
        out["anim_off"] = o
        n = struct.unpack_from("<H", body, o)[0]
        o += 2
        for _e in range(n + 1):
            for _b in range(3):
                _size, cnt = struct.unpack_from("<HH", body, o)
                o += 4
                for _p in range(cnt):
                    _k, o = _lp(body, o)
                    _v, o = _lp(body, o)
    if dw & 0x40:
        # the button FONT block (loader): lpstr face,
        # u8 charset, u8 attribs (justify nibble / bold 0x10 / italic 0x20 /
        # underline 0x40 / strikeout 0x80), u32 flags, u32 size (POINTS),
        # u32 text colour on/off, u32 background on/off (0xFF high byte = none)
        out["font_off"] = o
        s, o2 = _lp(body, o)
        try:
            vals = struct.unpack_from("<6I", body, o2 + 2)
            out["font"] = {
                "face": s.decode(CODEC, "replace"),
                "charset": body[o2],
                "attribs": body[o2 + 1],
                "size": vals[1],
                "text": (vals[2], vals[3]),
                "back": (vals[4], vals[5]),
            }
        except struct.error:
            pass
        o = o2 + 2 + 24
    if dw & 0x80:
        _s, o = _lp(body, o)
    out["cs_off"] = o
    flag = struct.unpack_from("<I", body, o)[0]
    out["cs_flag"] = flag
    o += 4
    if flag & 1:
        out["state0"] = struct.unpack_from("<II", body, o)
        out["state0_off"] = o
        o += 8
    out["after_cs"] = o
    return out


# ----------------------------------------------------------------- linked symbols
def is_linked_symbol(po: PageObject) -> bool:
    return po.type_code == BITMAP_TC and bool(po.subtype_size & 0x1)


# ------------------------------------------------------- moving a record forest
def _translatable(mode: int) -> bool:
    return (mode & 0x0F) in (1, 3) or (mode & 0x0F) >= 4


def carries(po: PageObject) -> bool:
    """A transform CARRIER: its position lives in its Local transform (a
    linked symbol, or any record whose slot-1 Local has a translation -- a
    genie instance, a symbol set). Baked shapes keep it in their geometry."""
    if is_linked_symbol(po):
        return True
    try:
        return _translatable(read_slots(po.body_raw)[0][0][0])
    except Exception:  # noqa: BLE001
        _note_exc("placement.carries")
        return False


def shift_slots(po: PageObject, shifts) -> None:
    """Add (dx, dy) to the H,K of each header slot (None = unchanged); a slot
    without a translation gains one (mode |= 0x01)."""
    if not any(s and (s[0] or s[1]) for s in shifts):
        return
    slots, _end = read_slots(po.body_raw)
    new = []
    for (mode, m6, _o, _n), s in zip(slots, shifts):
        if not s or not (s[0] or s[1]):
            new.append((mode, m6))
            continue
        a, b, c, d, h, k = m6
        if not _translatable(mode):
            mode |= 0x01
        new.append((mode, (a, b, c, d, h + s[0], k + s[1])))
    write_slots(po, new)


def _inverse_linear(total, dx, dy):
    """The Local-space delta whose image under ``total``'s linear part is
    (dx, dy) (point map x' = a*x + c*y, y' = b*x + d*y; compose_after)."""
    if total is None or (total[0] & 0x0F) in (0, 1):
        return float(dx), float(dy)
    a, b, c, d, _h, _k = total[1]
    det = a * d - b * c
    if abs(det) < 1e-12:
        return float(dx), float(dy)
    return (d * dx - c * dy) / det, (-b * dx + a * dy) / det


forest_parents = _forest.parents


def _same_linear(A, B, tol: float = 1e-9) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(A[:4], B[:4]))


def whole_slot2(po: PageObject):
    """``(slot 2, mode)`` of a record whose slot 2 isn't ``Local o T(-e)``."""
    (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
    return _whole_slot2(m1, L, m2, S2)


def _whole_slot2(m1, L, m2, S2):
    if not m2 & 0x06 or _same_linear(L if m1 else IDENT, S2):
        return None
    return S2, m2


def link_of(po: PageObject):
    """(Local, mode, e) of a record: what it contributes to a descendant's
    Total (Local o T(-e)); a slot 2 kept as a whole (:func:`whole_slot2`)
    contributes itself, as (slot 2, its mode, (0, 0))."""
    (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
    whole = _whole_slot2(m1, L, m2, S2)
    if whole is not None:
        return (whole[0], whole[1], (0, 0))
    return (L, m1, origin_of(po))


def _write_whole(po: PageObject, L, mode_L, s2, m2, parent_total=None) -> None:
    """Write Local and a slot 2 kept as a whole (:func:`whole_slot2`); the Total is
    composed from that slot 2 in the engine's order (:func:`apply_parent`)."""
    s3, m3 = apply_parent(s2, m2, parent_total)
    write_slots(po, [(mode_L, L), (m2, s2), (m3, s3)])


def apply_parent(s2, m2, parent):
    """A record's Total from its slot 2 and its parent context."""
    if parent is None:
        return s2, m2
    if isinstance(parent, list):
        acc, am = s2, m2
        for L, m, e in parent:
            if e != (0, 0):
                acc, am = _post_translate(acc, am, float(-e[0]), float(-e[1]))
            acc, am = compose_after(acc, am, L, m)
        return acc, am
    return compose_after(s2, m2, parent[1], parent[0])


def chain_total(chain):
    """The Total of the record whose ancestors are ``chain`` = ITS parent's
    Total (None for a root)."""
    if not chain:
        return None
    t, m = apply_parent(IDENT, 0, chain)
    return (m, t) if m else None


def _chains(pos, parents, outer_chain, memo: bool = False):
    """Ancestor chains for records, nearest first."""

    def plain(i):
        ch, k, top = [], parents[i], i
        while k is not None:
            ch.append(link_of(pos[k]))
            top, k = k, parents[k]
        if outer_chain is not None:
            ch.extend(outer_chain(top) or [])
        return ch

    if not memo:
        return plain
    under: dict = {}  # k -> chain of a record whose parent is k (a tuple)
    outer: dict = {}  # top -> outer_chain(top) (a tuple)

    def outer_of(top):
        if outer_chain is None:
            return ()
        r = outer.get(top)
        if r is None:
            r = outer[top] = tuple(outer_chain(top) or ())
        return r

    def chain_of(i):
        k = parents[i]
        if k is None:
            return list(outer_of(i))
        c = under.get(k)
        if c is None:
            path, a, bound = [], k, i
            while a is not None and a not in under:
                if a >= bound:
                    return plain(i)  # not in DFS order: nothing is memoised
                path.append(a)
                bound, a = a, parents[a]
            if a is not None and a >= bound:
                return plain(i)
            c = under[a] if a is not None else None
            for b in reversed(path):
                c = (link_of(pos[b]),) + (c if c is not None else outer_of(b))
                under[b] = c
        return list(c)

    return chain_of


def translate_records(pos, parents, dx, dy, link_info=None, outer_chain=None) -> None:
    """Move a set of records the way CtDraw re-derives them."""
    chain_of = _chains(pos, parents, outer_chain, memo=True)  # record i is written at step i only
    moved = set()  # carriers whose Total moved
    for i, po in enumerate(pos):
        p = parents[i]
        linked = is_linked_symbol(po)
        info = link_info(po) if (linked and link_info is not None) else None
        inherited = p is not None and p in moved
        top_carrier = not inherited and carries(po)
        if not (inherited or top_carrier):
            G.translate_object(po, dx, dy, slot_shifts=(None, None, None))
            continue
        chain = chain_of(i)
        e = origin_of(po)
        (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
        whole = None if linked else _whole_slot2(m1, L, m2, S2)
        if top_carrier:
            ldx, ldy = _inverse_linear(chain_total(chain), dx, dy)
            a, b, c, d, h, k = L
            L, m1 = (a, b, c, d, h + ldx, k + ldy), (m1 | 0x01)
            if whole is not None:
                # the move composed onto Local is composed onto the kept slot 2 too
                whole = compose_after(whole[0], whole[1], (1.0, 0.0, 0.0, 1.0, ldx, ldy), 0x01)
        if whole is not None:
            G.translate_object(po, dx, dy, slot_shifts=(None, None, None), refresh=False)
            _write_whole(po, L, m1, whole[0], whole[1], chain or None)
            if G.text_total(po) is not None:
                G.sync_text_echo(po)
            G.refresh_bags(po)
        elif linked and info is not None:
            rederive_linked(po, L, m1, info["e"], info["N"], chain or None)
            G.refresh_bags(po)
        elif linked:
            # unresolvable element: shift everything by d
            translate_linked_unresolved(po, dx, dy)
            G.refresh_bags(po)
        else:
            # ONE bag re-scan per record: translate_object's own would be overwritten by the
            # one below before anything reads the bags (rederive_slots / sync_text_echo read
            # the body only)
            G.translate_object(po, dx, dy, slot_shifts=(None, None, None), refresh=False)
            rederive_slots(po, L, m1, e, chain or None)
            if G.text_total(po) is not None:
                G.sync_text_echo(po)  # a mirrored text: echo = Total^-1(anchor)
            G.refresh_bags(po)  # slots may change length: re-index bags
        moved.add(i)


def affine_mul(A, B):
    """A o B for (a,b,c,d,h,k) with x' = a*x + c*y + h, y' = b*x + d*y + k."""
    a1, b1, c1, d1, h1, k1 = A
    a2, b2, c2, d2, h2, k2 = B
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * h2 + c1 * k2 + h1,
        b1 * h2 + d1 * k2 + k1,
    )


def affine_inv(A):
    a, b, c, d, h, k = A
    det = a * d - b * c
    if abs(det) < 1e-12:
        return IDENT
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * h + ic * k), -(ib * h + id_ * k))


def origin_of(po: PageObject):
    """The element origin e of a record's slot 2 = Local o T(-e) (0,0 for a
    free record), recovered from its stored slots."""
    (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
    if m2 == 0:
        return (0, 0)
    T = affine_mul(affine_inv(L if m1 else IDENT), S2)
    return (int(round(-T[4])), int(round(-T[5])))


def rederive_slots(po: PageObject, L, mode_L, e, parent_total=None) -> None:
    """Write Local and the slots the engine derives from it, in its own
    floating-point order: slot2 = Local o T(-e), slot3 = parentTotal o slot2
    (CtDraw recomputes both at load; a record whose stored slots differ
    by one ulp is rewritten)."""
    s2, m2 = (
        slot2_for_linked(L, mode_L, e)
        if mode_L
        else ((IDENT, 0) if e == (0, 0) else slot2_for_linked(IDENT, 0, e))
    )
    s3, m3 = apply_parent(s2, m2, parent_total)
    write_slots(po, [(mode_L, L), (m2, s2), (m3, s3)])


def _mode_of(m6) -> int:
    a, b, c, d, h, k = m6
    mode = 0x01 if (h or k) else 0
    if b or c:
        mode |= 0x04 | 0x02
    elif a != 1.0 or d != 1.0:
        mode |= 0x02
    return mode or 0x01


#: classes whose box mapping (geometry.resize_object) READS the record's bags: a button's
#: WIDTH / HEIGHT, a slider's X_SPAN / Y_SPAN, a styled text's measured string
_BOX_MAP_READS_BAGS = (0x1001, SLIDER_TC)


def _box_map_reads_bags(po) -> bool:
    return po.type_code in _BOX_MAP_READS_BAGS or (po.type_code == 0x05 and bool(po.subtype_size & 0x2))


def _map_record_geometry(po, M6, via=None, anchor_only=False, refresh_owed=False) -> None:
    """Map one record's geometry through the affine ``M6``."""
    gh = G.parse_geom_header(po.body_raw)
    if gh is None:
        if refresh_owed:
            G.refresh_bags(po)
        return

    def mp(x, y):
        # the engine maps through its double matrix and truncates. A record
        # under a transformed carrier lives in LOCAL element coordinates: its
        # page point is trunc(Total'(local)) with local = Total^-1(page) -- the
        # double rounding differs from M6(page) at integer edges
        if via is not None:
            T0, T1 = via
            ia, ib, ic, id_, ih, ik = affine_inv(T0)
            lx = round(ia * x + ic * y + ih)
            ly = round(ib * x + id_ * y + ik)
            a, b, c, d, h, k = T1
            return (int(a * lx + c * ly + h), int(b * lx + d * ly + k))
        a, b, c, d, h, k = M6
        return (int(a * x + c * y + h), int(b * x + d * y + k))

    if anchor_only:
        # a symbol-set container: its extent is refitted from its states, its
        # hotspot is mapped (live: Genies 759). No re-scan follows the anchor
        # write, so an owed one comes first.
        if refresh_owed:
            G.refresh_bags(po)
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, gh.anchor_off, *mp(*gh.anchor))
        po.body_raw = bytes(body)
        return
    if po.type_code in G.INPUT_TYPE_CODES or po.type_code in legacy.POINT_TCS or po.type_code == 0x1004:
        # an input record's icon / a point class's natural extent: the
        # object is placed by its point, never scaled
        ax, ay = gh.anchor
        nx, ny = mp(ax, ay)
        G.translate_object(po, nx - ax, ny - ay, slot_shifts=(None, None, None))
        return
    if po.type_code == 0x01:
        pts = G.get_object_nodes(po) or [gh.rect1[:2], gh.rect1[2:]]
        (x1, y1), (x2, y2) = mp(*pts[0]), mp(*pts[1])
        G.set_line_endpoints(po, x1, y1, x2, y2)
        return
    na = G.find_node_array(po)
    if na is not None:
        G.set_object_nodes(po, [mp(x, y) for x, y in G.read_nodes(po.body_raw, na)])
        return
    l, t, r, b = G.norm_rect(gh.rect1)
    (l2, t2), (r2, b2) = mp(l, t), mp(r, b)
    box = (min(l2, r2), min(t2, b2), max(l2, r2), max(t2, b2))
    # a bitmap/symbol record's hotspot is re-derived from its box (l, b-1),
    # like a placed symbol's (live: Genies 760..768); others are mapped
    anchor = (box[0], box[3] - 1) if po.type_code == BITMAP_TC else mp(*gh.anchor)
    if refresh_owed and _box_map_reads_bags(po):
        G.refresh_bags(po)
    G.resize_object(po, box, anchor=anchor, per_entry=False)


def scale_records(
    pos, parents, M6, link_info=None, outer_chain=None, containers=frozenset(), stretch_texts: bool = True
) -> None:
    """Resize a record set by the affine ``M6``, as CtDraw re-derives it."""
    chain_of = _chains(pos, parents, outer_chain, memo=True)  # record i is written at step i only
    moved = set()
    for i, po in enumerate(pos):
        p = parents[i]
        inherited = p is not None and p in moved
        linked = is_linked_symbol(po)
        info = link_info(po) if (linked and link_info is not None) else None
        via = None
        owed = False
        keeps = G.text_keeps_matrix(po)
        if keeps and not inherited:
            # the map composed onto its matrix in all three slots (mode 0x0F), the box and
            # the anchor laid out from its echo through the new Total (baked when that
            # brings it to a uniform scale with no flip, geometry.scale_text)
            G.scale_text(po, M6, stretch=stretch_texts)
            G.refresh_bags(po)
            moved.add(i)
            continue
        if (
            not inherited
            and po.type_code == 0x05
            and po.subtype_size & 0x2
            and not carries(po)
            and G.parse_text_tail(po) is not None
        ):
            # a text with no matrix: the group's scale reaches it and its
            # transform class decides: a uniform scale is baked into its font
            # (LIVE, chain5/scaled_groups), an uneven one kept as a matrix, as for the text
            # resized alone (chain6/text_resize)
            gh = G.parse_geom_header(po.body_raw)
            a, b, c, d, h, k = M6
            ax, ay = gh.anchor
            if G.scale_text(
                po, M6, anchor=(int(a * ax + c * ay + h), int(b * ax + d * ay + k)), stretch=stretch_texts
            ):
                G.refresh_bags(po)
                continue
        if inherited or carries(po):
            chain = chain_of(i)
            P = chain_total(chain)
            Pm = P[1] if P is not None else IDENT
            Mloc = affine_mul(affine_inv(Pm), affine_mul(M6, Pm))
            T0 = slot3(po)
            e = origin_of(po)
            (m1, L1, _, _), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
            whole = None if linked else _whole_slot2(m1, L1, m2, S2)
            if not inherited:
                # the engine composes the drag transform onto Local
                L1, m1 = compose_after(L1, m1, Mloc, _mode_of(Mloc)) if m1 else (Mloc, _mode_of(Mloc))
                if whole is not None:
                    whole = compose_after(whole[0], whole[1], Mloc, _mode_of(Mloc))
            if linked and info is not None:
                rederive_linked(po, L1, m1, info["e"], info["N"], chain or None)
                G.refresh_bags(po)
                moved.add(i)
                continue
            if whole is not None:
                _write_whole(po, L1, m1, whole[0], whole[1], chain or None)
            else:
                rederive_slots(po, L1, m1, e, chain or None)
            if keeps:
                # (a text that keeps a matrix under the moved carrier: its new Total holds
                # the map, so it is laid out from its echo, its font kept)
                G.relayout_text(po)
                G.refresh_bags(po)
                moved.add(i)
                continue
            # slots may change length: the bags are re-indexed before anything reads
            # them -- by _map_record_geometry, which usually re-scans at its end anyway
            owed = True
            moved.add(i)
            if T0[0]:
                via = (T0[1], slot3(po)[1])
        if i not in containers:
            _map_record_geometry(po, M6, via, refresh_owed=owed)
        elif po.type_code == 0x0C:
            _map_record_geometry(po, M6, via, anchor_only=True, refresh_owed=owed)
        elif owed:
            G.refresh_bags(po)


# ------------------------------------------------ rotate / mirror (Arrange)
class RecordTransformError(Exception):
    """A record of a rotate / mirror could not be transformed; ``index`` is
    its position in the record list handed to :func:`turn_records`."""

    def __init__(self, index: int, error: Exception):
        super().__init__("record %d: %s" % (index, error))
        self.index = index
        self.error = error


def _translate_step(L, mode, tx, ty):
    """The translate primitive of CtDraw's rotate / mirror about a point
    : H,K += t (a mode-0 Local starts from the
        identity), mode |= 0x01; a zero offset is skipped."""
    if abs(tx) < 1e-9 and abs(ty) < 1e-9:
        return L, mode
    a, b, c, d, h, k = L if mode else IDENT
    return (a, b, c, d, h + tx, k + ty), mode | 0x01


def _quarter_step(L, mode, q):
    """The matrix step for ``q`` clockwise quarter turns."""
    q %= 4
    if not q:
        return L, mode
    if mode == 0:
        return (
            {
                1: (0.0, 1.0, -1.0, 0.0, 0.0, 0.0),
                2: (-1.0, 0.0, 0.0, -1.0, 0.0, 0.0),
                3: (0.0, -1.0, 1.0, 0.0, 0.0, 0.0),
            }[q],
            0x04 | (q << 4),
        )
    a, b, c, d, h, k = L
    if q == 1:
        m6 = (-b, a, -d, c, -k, h)
    elif q == 2:
        m6 = (-a, -b, -c, -d, -h, -k)
    else:
        m6 = (b, -a, d, -c, k, -h)
    if not mode & 0x04:
        mode |= 0x04
        if not mode & 0x08:
            mode |= q << 4
    elif mode & 0xF0:
        nib = ((mode >> 4) + q) & 3
        mode = (mode & 0x0B) if nib == 0 else ((nib << 4) | (mode & 0x0F))
    return m6, mode


def turn_local(L, mode, q, pivot, shift=(0.0, 0.0)):
    """Rotate a Local by ``q`` CW quarter turns about ``pivot`` the way
    CtDraw's Arrange > Rotate composes it (T(-p), rotate,
    T(p)); ``shift`` is added to the final translate (a half-pixel pivot's
    whole-pixel landing, see ``EditableDocument._quarter_map``)."""
    px, py = pivot
    L, mode = _translate_step(L, mode, -px, -py)
    L, mode = _quarter_step(L, mode, q)
    return _translate_step(L, mode, px + shift[0], py + shift[1])


def mirror_local(L, mode, horizontal, pivot):
    """Mirror a Local about pivot (T(-p), scale by -1 on
    one axis -- A,C,H by sx and B,D,K by sy, mode |= 0x02 --, T(p))."""
    px, py = pivot
    L, mode = _translate_step(L, mode, -px, -py)
    sx, sy = (-1.0, 1.0) if horizontal else (1.0, -1.0)
    if mode == 0:
        L = (sx, 0.0, 0.0, sy, 0.0, 0.0)
    else:
        a, b, c, d, h, k = L
        L = (sx * a, sy * b, sx * c, sy * d, sx * h, sy * k)
    mode |= 0x02
    return _translate_step(L, mode, px, py)


def linked_extent(po: PageObject, info, parent_total=None):
    """A linked symbol's exact page box, bbox(Total x N), before the engine
    truncates it into rect1 (a half-pixel placement is common: !PID 42)."""
    (m1, L, _o, _n), _s2, _s3 = read_slots(po.body_raw)[0]
    s2, m2 = slot2_for_linked(L, m1, info["e"])
    s3, m3 = apply_parent(s2, m2, parent_total)
    return apply_rect(s3, m3, info["N"])


def map_header_box(po: PageObject, fn, box_anchor: bool = False, rect2_is_rect1: bool = False) -> None:
    """Write a record's header through the page-space point map ``fn`` (an
    exact quarter turn / mirror): rect1 / rect2 = the normalised boxes of
    their mapped corners; the anchor is re-derived as (l, b-1) when
    ``box_anchor`` (a group / genie hotspot, a bitmap class), else mapped."""
    gh = G.parse_geom_header(po.body_raw)
    if gh is None:
        return

    def box(r):
        pts = [fn((r[0], r[1])), fn((r[2], r[3]))]
        xs = [int(round(p[0])) for p in pts]
        ys = [int(round(p[1])) for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    r1 = box(gh.rect1)
    r2 = r1 if rect2_is_rect1 else box(gh.rect2)
    if box_anchor:
        anchor = (r1[0], r1[3] - 1)
    else:
        ax, ay = fn(tuple(gh.anchor))
        anchor = (int(round(ax)), int(round(ay)))
    write_header_geom(po, anchor, r1, r2)


def turn_records(
    pos,
    parents,
    local_op,
    fn,
    link_info=None,
    outer_chain=None,
    containers=frozenset(),
    content=None,
    held_local=None,
    kept_local=None,
    nested_op=None,
) -> None:
    """Rotate or mirror a record set."""
    chain_of = _chains(pos, parents, outer_chain)
    moved = set()
    for i, po in enumerate(pos):
        try:
            p = parents[i]
            inherited = p is not None and p in moved
            linked = is_linked_symbol(po)
            if inherited or linked or (i in containers and carries(po)):
                chain = chain_of(i)
                e = origin_of(po)
                (m1, L1, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(po.body_raw)[0]
                whole = None if linked else _whole_slot2(m1, L1, m2, S2)
                if not inherited:
                    P = chain_total(chain)
                    if held_local is not None:
                        m1, L1 = held_local(i, (m1, L1))
                    if nested_op is not None and p is not None:
                        L1, m1 = nested_op(i, L1, m1, P)
                    else:
                        L1, m1 = local_op(L1, m1, P)
                    if kept_local is not None:
                        kept_local(i, m1, L1)
                    if whole is not None:
                        # (local_op composes after its matrix: the kept slot 2 turns alike)
                        whole = local_op(whole[0], whole[1], P)
                info = link_info(po) if (linked and link_info is not None) else None
                if linked and info is not None:
                    rederive_linked(po, L1, m1, info["e"], info["N"], chain or None)
                    G.refresh_bags(po)
                    moved.add(i)
                    continue
                if whole is not None:
                    _write_whole(po, L1, m1, whole[0], whole[1], chain or None)
                else:
                    rederive_slots(po, L1, m1, e, chain or None)
                moved.add(i)
                if linked:
                    # unresolvable element: the header box turns with the
                    # transform (a bitmap-class box: rect2 == rect1, (l, b-1));
                    # map_header_box reads the body only, so ONE bag re-scan
                    map_header_box(po, fn, box_anchor=True, rect2_is_rect1=True)
                    G.refresh_bags(po)
                    continue
                # the slots may change length: re-index the bags before any
                # geometry write (a button's WIDTH/HEIGHT bag edit)
                G.refresh_bags(po)
            if i not in containers and content is not None:
                content(i, po, inherited)
        except RecordTransformError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported with the record
            raise RecordTransformError(i, exc) from exc


def translate_subtrees(pos, dx, dy, link_info=None) -> None:
    """Move detached record subtrees (a clipboard / duplicate payload list in
    DFS order) as root-level units."""
    translate_records(pos, forest_parents(pos), dx, dy, link_info)


def element_info(resolver, library: str, element: str, ext: str = "ctl") -> Optional[dict]:
    """(e origin, N native rect, root handle, root state-0 pair) of a library
    element; None when it does not resolve (CtDraw would show BROKENLNK)."""
    try:
        libs = resolver.libraries(library, ext)
    except Exception:  # noqa: BLE001
        _note_exc("placement.element_info")
        return None
    for lib in libs:
        blk = lib._find_block(element)
        pb = lib.element(element) if blk is not None else None
        if pb is None or not pb.objects:
            continue
        e = struct.unpack_from("<2i", blk.data, 0x0C) if lib.container.word2 >= 0x0C else (0, 0)
        root = pb.objects[0]
        gh = G.parse_geom_header(root.body_raw)
        if gh is None:
            continue
        try:
            s0 = walk(root).get("state0")
        except Exception:  # noqa: BLE001
            _note_exc("placement.element_info")
            s0 = None
        N = tuple(gh.rect1)
        # LIVE: the engine derives a linked symbol from the element ROOT's own frame --
        # e = -(its slot-2 translation), N = a bitmap root's class-native rect
        # -- not from the page-head origin / rect1. Every engine-normal element
        # has slot2 = T(-head e) and native == rect1, so this only differs on a
        # stale element.
        try:
            (_m1, _L, _o, _n), (m2, S2, _o2, _n2), _s3 = read_slots(root.body_raw)[0]
            if m2 == 0x01 and not pb.objects[0].class_flag:
                e = (-S2[4] + 0.0, -S2[5] + 0.0)
                if has_native_rect(root):
                    nat = native_rect(root)
                    if nat is not None and nat[2] > nat[0] and nat[3] > nat[1]:
                        N = tuple(nat)
        except Exception:  # noqa: BLE001
            _note_exc("placement.element_info")
            pass
        return dict(e=tuple(e), N=N, root_handle=G.get_handle(root), root_tc=root.type_code, state0=s0)
    return None


def rederive_linked(po, L, mode_L, e, N, parent_total=None) -> None:
    """Write slot1=L and the engine-derived slot2/slot3/rect1/rect2/anchor."""
    s2, m2 = slot2_for_linked(L, mode_L, e)
    s3, m3 = apply_parent(s2, m2, parent_total)
    write_slots(po, [(mode_L, L), (m2, s2), (m3, s3)])
    rect1, anchor, _R = derive_geom(s3, m3, N)
    write_header_geom(po, anchor, rect1, rect1)  # bitmap class: rect2 == rect1


def rederive_from_element(po, info, parent_total=None) -> bool:
    """A linked symbol after its library element changed, as CtDraw's load + save leaves it."""
    before = bytes(po.body_raw)
    (m1, L, _o, _n), _s2, _s3 = read_slots(po.body_raw)[0]
    rederive_linked(po, L, m1, info["e"], info["N"], parent_total)
    s0 = info.get("state0")
    if s0 is not None:
        try:
            w = walk(po)
        except Exception:  # noqa: BLE001 -- a record the walk cannot read keeps its pair
            _note_exc("placement.rederive_from_element")
            w = {}
        if "state0_off" in w:
            body = bytearray(po.body_raw)
            struct.pack_into("<II", body, w["state0_off"], *s0)
            po.body_raw = bytes(body)
    return bytes(po.body_raw) != before


def linear_ops(m6, tol: float = 1e-6):
    """Decompose a Total's linear part into CtDraw's Arrange steps."""
    a, b, c, d = (float(v) for v in m6[:4])
    if abs(b) <= tol and abs(c) <= tol:
        if abs(a) <= tol or abs(d) <= tol:
            return None
        ops = (["mh"] if a < 0 else []) + (["mv"] if d < 0 else [])
        return ops, (abs(a), abs(d))
    if abs(a) <= tol and abs(d) <= tol:
        sc, sb = (1 if c > 0 else -1), (1 if b > 0 else -1)
        # cw: (x, y) -> (-y, x) = c -1 / b +1; acw: (y, -x) = c +1 / b -1;
        # (y, x) = cw then a horizontal mirror; (-y, -x) = acw then one
        ops = {(-1, 1): ["cw"], (1, -1): ["acw"], (1, 1): ["cw", "mh"], (-1, -1): ["acw", "mh"]}[(sc, sb)]
        return ops, (abs(c), abs(b))
    return None


def translate_linked(po, dx, dy, info, parent_total=None) -> None:
    """Move a placed symbol: T(d) onto Local, everything else re-derived."""
    (m1, L, _o, _n), _s2, _s3 = read_slots(po.body_raw)[0]
    a, b, c, d, h, k = L
    rederive_linked(po, (a, b, c, d, h + dx, k + dy), m1 | 0x01, info["e"], info["N"], parent_total)


def translate_linked_unresolved(po, dx, dy, shifts=None) -> None:
    """Fallback when the element cannot be resolved: shift H,K of the three
    slots (``shifts``, default all by d) and the header rects by d (exact
    for integer placements)."""
    shift_slots(po, shifts if shifts is not None else ((dx, dy),) * 3)
    gh = G.parse_geom_header(po.body_raw)
    l, t, r, b = gh.rect1
    l2, t2, r2, b2 = gh.rect2
    write_header_geom(
        po,
        (gh.anchor[0] + dx, gh.anchor[1] + dy),
        (l + dx, t + dy, r + dx, b + dy),
        (l2 + dx, t2 + dy, r2 + dx, b2 + dy),
    )


def set_linked_rect_unresolved(po, target) -> None:
    """Fallback resize when the element cannot be resolved: scale every slot
    by the map from the current rect1 onto ``target``."""
    gh = G.parse_geom_header(po.body_raw)
    l0, t0, r0, b0 = G.norm_rect(gh.rect1)
    tl, tt, tr, tb = (float(v) for v in target)
    sx = (tr - tl) / (r0 - l0) if r0 > l0 else 1.0
    sy = (tb - tt) / (b0 - t0) if b0 > t0 else 1.0
    S = (sx, 0.0, 0.0, sy, tl - sx * l0, tt - sy * t0)
    slots, _end = read_slots(po.body_raw)
    new = [compose_after(m6, mode, S, 0x03) if mode else (S, 0x03) for mode, m6, _o, _n in slots]
    # (the mode by the matrix's content: a scale undone again is mode 1, the form of
    # an unscaled Local)
    write_slots(po, [(_mode_of(x), x) for x, _m in new])
    tr1 = tuple(int(v) for v in target)
    write_header_geom(po, (tr1[0], tr1[3] - 1), tr1, tr1)


def set_linked_rect(po, target, info, parent_total=None) -> None:
    """Resize/reposition a placed symbol to page rect ``target``: its size is
    Local's SCALE x the native element size, so post-compose the page-space
    scale+translate mapping the current Total bbox onto the target (keeps a
    rotation / mirror)."""
    e, N = info["e"], info["N"]
    (m1, L, _o, _n), _s2, _s3 = read_slots(po.body_raw)[0]
    s2, m2 = slot2_for_linked(L, m1, e)
    if parent_total is None:
        T, mT = s2, m2
    else:
        T, mT = compose_after(s2, m2, parent_total[1], parent_total[0])
    R = apply_rect(T, mT, N)
    tl, tt, tr, tb = (float(v) for v in target)
    sx = (tr - tl) / (R[2] - R[0]) if R[2] > R[0] else 1.0
    sy = (tb - tt) / (R[3] - R[1]) if R[3] > R[1] else 1.0
    S = (sx, 0.0, 0.0, sy, tl - sx * R[0], tt - sy * R[1])
    ms = 0x03 if (sx != 1.0 or sy != 1.0) else 0x01
    if m1 == 0:
        L2, mL2 = S, ms
    else:
        L2, mL2 = compose_after(L, m1, S, ms)
    # anti-truncation bias: every derived edge lands on (not just below) its
    # integer target
    for _ in range(3):
        s2n, m2n = slot2_for_linked(L2, mL2, e)
        r1, _anc, _Rn = derive_geom(s2n, m2n, N)
        fx = 1e-9 if r1[0] < int(tl) or r1[2] < int(tr) else 0.0
        fy = 1e-9 if r1[1] < int(tt) or r1[3] < int(tb) else 0.0
        if not fx and not fy:
            break
        a, b, c, d, h, k = L2
        L2 = (a, b, c, d, h + fx * max(1.0, abs(h)), k + fy * max(1.0, abs(k)))
    rederive_linked(po, L2, mL2, e, N, parent_total)


def init_linked_symbol(po, rect, info) -> None:
    """Place a (cloned-seed) linked symbol record at ``rect``: Local = native
    translate (mode 1), plus the resize scale when the box differs from the
    element's native size; state-0 pair = the element root's; dwFlags = LINK
    + bags (bit 0x10 only when animated, bit 0x2 never)."""
    ex, ey = info["e"]
    nl, nt, nr, nb = info["N"]
    l, t, r, b = (int(v) for v in rect)
    rederive_linked(
        po, (1.0, 0.0, 0.0, 1.0, float(l - nl + ex), float(t - nt + ey)), 0x01, info["e"], info["N"]
    )
    if (r - l) != (nr - nl) or (b - t) != (nb - nt):
        set_linked_rect(po, rect, info)
    w = walk(po)
    if info.get("state0") is not None and "state0_off" in w:
        body = bytearray(po.body_raw)
        struct.pack_into("<II", body, w["state0_off"], *info["state0"])
        po.body_raw = bytes(body)
    has_anim = any(not k.startswith("!") for k in po.raw_properties())
    po.subtype_size = (po.subtype_size & ~0x10 & ~0x02) | 0x09 | (0x10 if has_anim else 0)


# ----------------------------------------------------------------- class-native extents
def class_section_offset(po: PageObject) -> int:
    """Where the class section starts."""
    L = colourstate.layout(po)
    if L is not None:
        o = L["end"] + (54 if po.subtype_size & 2 else 0)
        if po.subtype_size & 0x100:
            try:
                o = colourstate.bag_end(po.body_raw, o)
            except struct.error:
                o = None
        if o is not None and o <= len(po.body_raw):
            return o
    return walk(po)["after_cs"] + (54 if po.subtype_size & 2 else 0)


def has_native_rect(po: PageObject) -> bool:
    """Classes whose class section carries their own native rect, which the
    engine positions them by: a moved unlinked bitmap / MetaFile (WMF, tc 9)
    must shift it too."""
    return po.type_code in (BAR_TC, ACTIVEX_TC) or (
        po.type_code in (BITMAP_TC, 0x09) and not (po.subtype_size & 0x1)
    )


def native_rect(po: PageObject) -> Optional[Tuple[int, int, int, int]]:
    """The class-section rect, or None. Most unlinked bitmaps end where it would start:
    no rect, quietly, as shift_class_native has it."""
    try:
        o = class_section_offset(po)
        if o + 8 > len(po.body_raw):
            return None
        return struct.unpack_from("<4h", po.body_raw, o)
    except Exception:  # noqa: BLE001
        _note_exc("placement.native_rect")
        return None


def set_native_rect(po: PageObject, rect) -> None:
    """Bar / ActiveX / embedded Bitmap: write the class-section rect; header
    rect1 = rect2 = rect, anchor = (l, b-1) (Local stays identity)."""
    l, t, r, b = (int(v) for v in rect)
    o = class_section_offset(po)
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, o, l, t, r, b)
    po.body_raw = bytes(body)
    write_header_geom(po, (l, b - 1), (l, t, r, b), (l, t, r, b))


def slider_track(po: PageObject):
    o = class_section_offset(po)
    return o, struct.unpack_from("<4h", po.body_raw, o)


def set_slider_track(po: PageObject, rect) -> None:
    """Slider: track p1 -> p2 in the class section; rect1 = bbox(p1,p2) + 1,
    anchor = p1, X_SPAN / Y_SPAN = p2 - p1; rect2 keeps the thumb deltas."""
    from . import writer

    l, t, r, b = (int(v) for v in rect)
    gh = G.parse_geom_header(po.body_raw)
    o, (x1, y1, x2, y2) = slider_track(po)
    vertical = x1 == x2
    if vertical:
        cx = (l + r) // 2
        p1, p2 = (cx, b - 1), (cx, t)
    else:
        cy = (t + b) // 2
        p1, p2 = (l, cy), (r - 1, cy)
    d = [gh.rect2[i] - gh.rect1[i] for i in range(4)]
    body = bytearray(po.body_raw)
    struct.pack_into("<4h", body, o, p1[0], p1[1], p2[0], p2[1])
    po.body_raw = bytes(body)
    r1 = (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]) + 1, max(p1[1], p2[1]) + 1)
    write_header_geom(po, p1, r1, tuple(r1[i] + d[i] for i in range(4)))
    key, val = ("Y_SPAN", p2[1] - p1[1]) if vertical else ("X_SPAN", p2[0] - p1[0])
    writer.set_object_property(po, key, str(val))


def shift_class_native(po: PageObject, dx: int, dy: int, old_rect1) -> None:
    """Keep a class-native extent in step with a move (Bar / ActiveX /
    embedded Bitmap rect, Slider track points)."""
    try:
        o = class_section_offset(po)
    except Exception:  # noqa: BLE001
        _note_exc("placement.shift_class_native")
        return
    body = bytearray(po.body_raw)
    if o + 8 > len(body):
        return
    if po.type_code == SLIDER_TC:
        x1, y1, x2, y2 = struct.unpack_from("<4h", body, o)
        struct.pack_into("<4h", body, o, x1 + dx, y1 + dy, x2 + dx, y2 + dy)
    elif has_native_rect(po):
        cur = struct.unpack_from("<4h", body, o)
        if tuple(cur) != tuple(old_rect1):
            return  # not the extent we think it is
        l, t, r, b = cur
        struct.pack_into("<4h", body, o, l + dx, t + dy, r + dx, b + dy)
    else:
        return
    po.body_raw = bytes(body)


def himetric(px: int) -> int:
    """MulDiv(px, 2540, 96) (rounded)."""
    return (px * 2540 + 48) // 96


def patch_ole_extent(po: PageObject, old_rect, new_rect) -> int:
    """ActiveX: the control also persists its extent in HIMETRIC inside its
    OLE Contents stream; patch the u32 occurrences of the old (w, h)."""
    o = class_section_offset(po)
    body = bytearray(po.body_raw)
    size = struct.unpack_from("<I", body, o + 8)[0]
    cfb0 = o + 12
    ow, oh = old_rect[2] - old_rect[0], old_rect[3] - old_rect[1]
    nw, nh = new_rect[2] - new_rect[0], new_rect[3] - new_rect[1]
    n = 0
    ocx, ocy, ncx, ncy = himetric(ow), himetric(oh), himetric(nw), himetric(nh)
    snap = bytes(body)  # (searched once: a value just written must not be
    # found again, on a resize to a width equal to the old height or a quarter turn)
    cx_at = snap.find(struct.pack("<I", ocx), cfb0 + 1536, cfb0 + size)
    pair = cx_at >= 0 and cx_at + 10 <= len(snap) and struct.unpack_from("<I", snap, cx_at + 6)[0] == ocy
    if pair:
        if ocx != ncx:
            struct.pack_into("<I", body, cx_at, ncx)
            n += 1
        if ocy != ncy:
            struct.pack_into("<I", body, cx_at + 6, ncy)
            n += 1
    else:
        for old, new in ((ocx, ncx), (ocy, ncy)):
            if old == new:
                continue
            i = snap.find(struct.pack("<I", old), cfb0 + 1536, cfb0 + size)
            if i >= 0:
                struct.pack_into("<I", body, i, new)
                n += 1
    po.body_raw = bytes(body)
    return n
