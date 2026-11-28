"""Write a page's .ctF (the fast runtime display file)."""

from __future__ import annotations

import dataclasses
import os
import struct
import threading
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

from .zstream import inflate
from .cnum import c_atoi
from . import genie_place as GP
from . import geometry as G
from .container import Container
from .page import PageObject, parse_page
from .symbols import reference_from_object
from ..infra.logsetup import note_exception as _note_exc
from . import codes as _codes
from . import forest as _forest

PAGE_TRAILING_PAD = 512
TEXT_TC = 0x05
BITMAP_TC = 0x07


# --------------------------------------------------------------------------- forest helpers
forest_parents = _forest.parents

subtree_end = _forest.subtree_end


# --------------------------------------------------------------------------- appearance owners
class Owners:
    """The record owning each record's in-memory appearance (CtDraw's load:
    a linked record -- dwFlags 0x1 -- takes its library element's; a linked
    CONTAINER's members name their element record by AN (= its handle),
    recursively for containers linked again inside the element)."""

    def __init__(self, resolver):
        self.resolver = resolver
        self.elems: Dict[Tuple[str, str, str], Optional[List[PageObject]]] = {}
        self.owners_cache: Dict[Tuple[str, str, str], Optional[List[PageObject]]] = {}
        # id(element record) -> (element key, its index): where an owner record lives, so a
        # member without an owner of its own can take its parent owner's CHILDREN
        self._where: Dict[int, Tuple[Tuple[str, str, str], int]] = {}
        # (element key, records paired) -> its records' (handle, class) slots (_member_owners)
        self._slots: Dict[tuple, Dict[Tuple[int, int], List[int]]] = {}

    def _records(self, po: PageObject) -> Tuple[Optional[tuple], Optional[List[PageObject]]]:
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            _note_exc("ctf._Owners._records")
            ref = None
        if ref is None or self.resolver is None:
            return None, None
        key = (ref.library.lower(), ref.element.lower(), ref.ext.lower())
        if key not in self.elems:
            recs = None
            try:
                rs = self.resolver.resolve(ref)
                if rs is not None and rs.objects:
                    recs = [o for o in rs.objects]
                    # the element block's 3-record trailer is not part of the tree
                    end = subtree_end(recs, 0) if recs[0].class_flag else 1
                    recs = recs[:end]
            except Exception:  # noqa: BLE001
                _note_exc("ctf._Owners._records/resolve")
                recs = None
            self.elems[key] = recs
            for k, r in enumerate(recs or ()):
                self._where.setdefault(id(r), (key, k))
        return key, self.elems[key]

    def _owner_children(self, owner: PageObject) -> Optional[List[Tuple[PageObject, PageObject]]]:
        """The direct CHILDREN of the element record ``owner`` in the element it lives in, as
        (child record, its owner) -- the child's own owner when that element's owners are
        known, else the child itself."""
        loc = self._where.get(id(owner))
        if loc is None or not owner.class_flag:
            return None
        key, pk = loc
        recs = self.elems.get(key) or []
        if pk >= len(recs) or recs[pk] is not owner:
            return None
        owned = self.owners_cache.get(key)
        out: List[Tuple[PageObject, PageObject]] = []
        c = pk + 1
        for _n in range(owner.class_flag):
            if c >= len(recs):
                break
            ow = owned[c] if owned is not None and c < len(owned) else None
            out.append((recs[c], ow if ow is not None else recs[c]))
            c = subtree_end(recs, c)
        return out

    def element_owners(self, po: PageObject, depth: int = 0) -> Optional[List[PageObject]]:
        """Owners for the records of the element ``po`` links to."""
        key, recs = self._records(po)
        if recs is None:
            return None
        if key in self.owners_cache:
            return self.owners_cache[key]
        self.owners_cache[key] = None  # recursion guard
        out = self.resolve(recs, depth + 1, top=False)
        self.owners_cache[key] = out
        return out

    def _member_owners(
        self, po: PageObject, inst: Sequence[PageObject], sub: List[PageObject]
    ) -> List[Optional[PageObject]]:
        """Appearance owners for a linked container's subtree."""
        from .geometry import get_handle

        key0, erecs = self._records(po)
        erecs = erecs or []
        # the (handle, class) slots of the element's records, once per element (a page places
        # the same element many times)
        span = min(len(erecs), len(sub))
        slots: Optional[Dict[Tuple[int, int], List[int]]] = self._slots.get((key0, span))
        if slots is None:
            slots = {}
            for k in range(1, span):
                try:
                    slots.setdefault((get_handle(erecs[k]), erecs[k].type_code), []).append(k)
                except Exception:  # noqa: BLE001
                    _note_exc("ctf._Owners._member_owners")
                    continue
            self._slots[(key0, span)] = slots
        aligned = len(erecs) == len(inst) and all(a.type_code == b.type_code for a, b in zip(erecs, inst))
        used: Dict[Tuple[int, int], int] = {}
        out: List[Optional[PageObject]] = [sub[0] if sub else None]
        parents = forest_parents(inst)
        nth: Dict[int, int] = {}  # parent -> children seen so far
        kids: Dict[int, Optional[list]] = {}  # parent -> its owner's children
        taken: Dict[int, set] = {}  # parent -> owner children already given
        for j in range(1, len(inst)):
            x = inst[j]
            p = parents[j]
            ordn = nth.get(p, 0)
            nth[p] = ordn + 1
            try:
                key = (int(x.an_str), x.type_code)
            except ValueError:
                key = None
            lst = slots.get(key) if key is not None else None
            n = used.get(key, 0) if key is not None else 0
            if lst and n < len(lst):
                out.append(sub[lst[n]])
                used[key] = n + 1
            elif aligned and j < len(sub):
                out.append(sub[j])
            else:
                out.append(self._from_parent_owner(x, key, p, ordn, out, kids, taken, key0, get_handle))
        return out

    def _from_parent_owner(self, x, key, p, ordn, out, kids, taken, key0, get_handle):
        """The owner of an instance member no slot names, from its parent's owner's children
        (see _member_owners); None when there is none."""
        if p is None or p >= len(out) or out[p] is None:
            return None
        if p not in kids:
            pown = out[p]
            loc = self._where.get(id(pown))
            if loc is None:
                kids[p] = None
            elif loc[0] != key0:
                # (child record, its owner, the class a member must have to take it)
                kids[p] = [(rec, ow, rec.type_code) for rec, ow in (self._owner_children(pown) or ())]
            else:
                # this element's own records are all in the slot table already, and a
                # position match there would guess; except a LINKED LEAF child (a symbol-set
                # state or a symbol stored as one record, dwFlags 0x1): CtDraw's load
                # composes it again from the element it links to, so the instance's member
                # has that element root's class (a group root and its children since the
                # symbol became a group) and handles the stored copy never had (CtDraw
                # numbers the new records on). Its owner is that root.
                kids[p] = [
                    (rec, ow, ow.type_code) if _linked_leaf(rec) and ow is not rec else (rec, ow, None)
                    for rec, ow in (self._owner_children(pown) or ())
                ]
        ch = kids[p]
        if not ch:
            return None
        got = taken.setdefault(p, set())
        pick = None
        if key is not None:
            for i, (rec, _ow, cls) in enumerate(ch):
                if i in got or cls != x.type_code:
                    continue
                try:
                    if get_handle(rec) == key[0]:
                        pick = i
                        break
                except Exception:  # noqa: BLE001
                    _note_exc("ctf._Owners._from_parent_owner")
                    continue
        if pick is None and ordn < len(ch) and ordn not in got and ch[ordn][2] == x.type_code:
            pick = ordn
        if pick is None:
            return None
        got.add(pick)
        return ch[pick][1]

    def resolve(self, recs: Sequence[PageObject], depth: int = 0, top: bool = True) -> List[PageObject]:
        owners: List[PageObject] = list(recs)
        if depth > 8:
            return owners
        i = 0 if top else 0
        while i < len(recs):
            po = recs[i]
            linked = bool(po.subtype_size & 0x1)
            if linked and po.class_flag and not (not top and i == 0):
                end = subtree_end(recs, i)
                sub = self.element_owners(po, depth)
                if sub is not None:
                    for k, ow in enumerate(self._member_owners(po, recs[i:end], sub)):
                        if ow is not None:
                            owners[i + k] = ow
                    i = end
                    continue
                if sub and not sub[0].class_flag:
                    # a linked LEAF symbol that owns behaviour children (a
                    # Touch / Keyboard record): only the record itself links
                    owners[i] = sub[0]
            elif linked and not po.class_flag:
                sub = self.element_owners(po, depth)
                if sub:
                    owners[i] = sub[0]
            i += 1
        return owners


def _linked_leaf(po: PageObject) -> bool:
    """A record that links to a library element and has no children (dwFlags 0x1)."""
    return bool(po.subtype_size & 0x1) and not po.class_flag


def appearance_owners(objs: Sequence[PageObject], resolver) -> List[Optional[PageObject]]:
    """Per page record: the record that owns its in-memory appearance (the
    record itself when it is its own)."""
    return Owners(resolver).resolve(objs)


# --------------------------------------------------------------------------- per record
#: the appearance of an object that never had one (a group / genie root / a
#: bitmap): CtDraw's constructor defaults -- effects 0, line style 0,
#: fill hollow, attribs 0, line width 1, effect depth -1; line + fill black,
#: background = the no-colour sentinel, hilight white, lolight + shadow grey
DEFAULT_STYLE = bytes([0, 0, 0, 0, 1, 0xFF]) + struct.pack(
    "<12I",
    0,
    0,
    0,
    0,
    0xFF000000,
    0xFF000000,
    0x00FFFFFF,
    0x00FFFFFF,
    0x007F7F7F,
    0x007F7F7F,
    0x007F7F7F,
    0x007F7F7F,
)


def _lp(b: bytes, o: int) -> int:
    return o + 2 + struct.unpack_from("<H", b, o)[0]


def _metadata_len(cls: bytes) -> int:
    """Byte length of the dwFlags-0x100 metadata section at the start of a
    class section: u16 n + n x (lpstr name, lpstr value)."""
    n = struct.unpack_from("<H", cls, 0)[0]
    o = 2
    for _ in range(n):
        o = _lp(cls, _lp(cls, o))
    return o


SYMSET_TC = 0x0C


def appearance_part(t) -> bytes:
    """The APPEARANCE part of a decoded record's class section --
    what an art-stripped instance record lacks (genie_place.strip_class_section
    keeps [0x100 metadata] + [the string part] and drops this)."""
    if not (t.dw & 0x2):
        return b""
    cls = t.cls
    if t.dw & 0x100:
        cls = cls[_metadata_len(cls) :]
    if t.tc == TEXT_TC:
        return cls[GP.text_strings_len(cls) :] if len(cls) >= 4 else b""
    if t.tc == SYMSET_TC:
        return cls[4:]
    if t.tc == 0xFFFF:
        return cls[max(0, len(cls) - 12) :]
    return cls


def own_caption(cls: bytes, dw: int) -> Optional[bytes]:
    """The first lpstr of an instance text's string section (its caption)."""
    o = _metadata_len(cls) if dw & 0x100 else 0
    if len(cls) < o + 6:
        return None
    n = struct.unpack_from("<H", cls, o + 4)[0]
    return cls[o + 6 : o + 6 + n]


def _with_caption(part: bytes, caption: bytes) -> bytes:
    """A text appearance part = [i16 x,y anchor echo][u16 orient/charset]
    [u8 size][lpstr text][lpstr face]: the caption replaced."""
    if len(part) < 9:
        return part
    n = struct.unpack_from("<H", part, 7)[0]
    return part[:7] + struct.pack("<H", len(caption)) + caption + part[9 + n :]


def ctf_rec(r0, t, memo: Optional[dict] = None) -> "GP.Rec":
    """The .ctF record for a decoded .ctg record."""
    r = dataclasses.replace(r0)
    own = bool(r.dw & 0x2)
    r.dw = (r.dw & ~0x8) | 0x2
    r.entries = None
    if not own:
        if t is not None:
            r.style = t.style if t.style is not None else DEFAULT_STYLE
            if t.style is not None and len(r.cs) >= 12:
                # the .ctg and the .ctF are written from the same in-memory
                # object and the saver writes state-0 from the slot
                # (fill_style ? fill : line) -- so that slot IS the instance's
                # state-0 pair (the twin's may be older)
                st = bytearray(r.style)
                slot = 1 if st[2] else 0
                st[6 + 8 * slot : 14 + 8 * slot] = r.cs[4:12]
                r.style = bytes(st)
            part = appearance_part(t)
            if r.tc == TEXT_TC and part:
                cap = own_caption(r.cls, r.dw)
                if cap is not None:
                    part = _with_caption(part, cap)
            r.cls = r.cls + part
        else:
            r.style = DEFAULT_STYLE
    if r.tc == BITMAP_TC:
        if memo is None:
            r.cls = bitmap_redeflated(r.cls, r.dw)
        else:
            k = (bytes(r.cls), r.dw & 0x100)
            got = memo.get(k)
            if got is None:
                got = memo[k] = bitmap_redeflated(r.cls, r.dw)
            r.cls = got
    return r


def _redeflate(stream: bytes, size: int) -> Optional[bytes]:
    """``stream`` inflated (it must give ``size`` bytes) and deflated by :func:`compress`;
    None when it is no such stream."""
    try:
        raw = inflate(stream, size)
    except zlib.error:
        _note_exc("ctf._redeflate")
        return None
    if len(raw) != size:
        return None
    return compress(raw)


def bitmap_redeflated(cls: bytes, dw: int) -> bytes:
    """A bitmap part with its DIBs deflated again."""
    if not cls:
        return cls
    o = _metadata_len(cls) if dw & 0x100 else 0
    if len(cls) < o + 20:
        return cls
    flags, size, n1 = struct.unpack_from("<III", cls, o + 8)
    if not flags & 0x1 or o + 20 + n1 > len(cls):
        return cls
    spans = [(o + 20, o + 20 + n1)]
    end = spans[0][1]
    if flags & 0x100:
        if end + 4 > len(cls):
            return cls
        (n2,) = struct.unpack_from("<I", cls, end)
        if end + 4 + n2 > len(cls):
            return cls
        spans.append((end + 4, end + 4 + n2))
        end += 4 + n2
    zs = []
    for a, b in spans:
        z = _redeflate(bytes(cls[a:b]), size)
        if z is None:
            return cls
        zs.append(z)
    out = bytes(cls[: o + 8]) + struct.pack("<III", flags, size, len(zs[0])) + zs[0]
    if len(zs) > 1:
        out += struct.pack("<I", len(zs[1])) + zs[1]
    return out + bytes(cls[end:])


# --------------------------------------------------------------------------- the bake pass
GROUP_TC = 0x08
#: the animation base (no region of their own):
#: never baked, never adds to the dynamic region
_ANIM_NO_REGION = frozenset(
    {0x1000, 0x1005, 0x1006, 0x1007, 0x1009, _codes.TC.KEYBOARD, 0x100D, 0x100E, 0x100F, _codes.TC.TOUCH}
)
#: Button / Bar / Trend / Number: the region is their bounds, unconditionally
_ANIM_RECT_REGION = frozenset({0x1001, 0x1002, 0x1003, 0x1004})
COLOURAN_TC = 0x1008
#: classes that are always dynamic (Text, ActiveX, WebContent)
_ALWAYS_DYNAMIC = frozenset({TEXT_TC, 0x0D, 0x13})
#: the probe string of a string display's region
_W270 = "Wg" + "W" * 268


def _nb(v: Optional[str]) -> bool:
    """== 0: the expression is not blank."""
    return bool(v) and not v.isspace()


def _cdiv(a: int, b: int) -> int:
    """C integer division (truncation toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


class _Grid:
    """The GDI dynamic region (CreateRectRgn / RGN_OR / RectInRegion) as a
    bucketed rect set: half-open [l, r) x [t, b)."""

    CELL = 64
    LO, HI = -64, 256  # cell range clamp (-4096 .. 16384 px)

    def __init__(self):
        self.cells: Dict[Tuple[int, int], List[Tuple[int, int, int, int]]] = {}
        self.big: List[Tuple[int, int, int, int]] = []

    def _span(self, lo: int, hi: int):
        c = self.CELL
        a = min(max(lo // c, self.LO), self.HI)
        b = min(max((hi - 1) // c, self.LO), self.HI)
        return range(a, b + 1)

    def add(self, r) -> None:
        l, t, rr, b = r
        if rr <= l or b <= t:
            return
        r = (l, t, rr, b)
        xs, ys = self._span(l, rr), self._span(t, b)
        if len(xs) * len(ys) > 48:
            self.big.append(r)
            return
        for cx in xs:
            for cy in ys:
                self.cells.setdefault((cx, cy), []).append(r)

    def hits(self, r) -> bool:
        l, t, rr, b = r
        if rr <= l or b <= t:
            return False
        for L, T, R, B in self.big:
            if l < R and L < rr and t < B and T < b:
                return True
        ys = self._span(t, b)
        cells = self.cells
        for cx in self._span(l, rr):
            for cy in ys:
                for L, T, R, B in cells.get((cx, cy), ()):
                    if l < R and L < rr and t < B and T < b:
                        return True
        return False


def _norm(r):
    l, t, rr, b = r
    return (min(l, rr), min(t, b), max(l, rr), max(t, b))


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _offset(r, dx, dy):
    return (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)


#: GetRgnBox of an empty (NULLREGION) region
EMPTY = (0, 0, 0, 0)


def _empty(r) -> bool:
    return r[2] <= r[0] or r[3] <= r[1]


def _set_rect(r):
    """SetRectRgn: the region of the box, ordered; an empty box gives an empty region."""
    r = _norm(r)
    return EMPTY if _empty(r) else r


def _or(a, b):
    """CombineRgn(RGN_OR) / UnionRect of two region boxes: an empty one adds nothing, two
    empty ones give an empty region. None = no region."""
    if a is None and b is None:
        return None
    if a is None or _empty(a):
        return EMPTY if b is None or _empty(b) else b
    if b is None or _empty(b):
        return a
    return _union(a, b)


class _Obj:
    """What the bake reads of one record: bounds (rect2), the
    in-memory property list (entry 0 bag 1), the appearance (style), the
    'Local' transform and, for text, the font + caption."""

    __slots__ = (
        "po",
        "tc",
        "dw",
        "bounds",
        "rect1",
        "anchor",
        "props",
        "style",
        "font",
        "caption",
        "m1",
        "_low",
    )

    def __init__(self, po: PageObject, r=None, c=None):
        """``r`` = the record's decode, ``c`` = its .ctF record (the in-memory
        appearance: an art-stripped member's owner style / font)."""
        self.po = po
        self.tc = po.type_code
        self.dw = po.subtype_size
        if r is None:
            r = GP.decode(po)
        self.bounds = _norm(r.rect2)
        self.rect1 = r.rect1
        self.anchor = r.anchor
        self.props = r.entries[0][1] if (r.entries and len(r.entries[0]) > 1) else GP.Bag([])
        self.style = r.style
        self.font = None
        self.caption = None
        self.m1 = (r.slots[0][0], r.slots[0][1]) if r.slots else (0, None)
        self._low = None
        if c is not None:
            self.style = c.style
            if self.tc == TEXT_TC:
                f = _text_font(c)
                if f is not None:
                    self.font, self.caption = f[:4], f[4]

    def get(self, key: str) -> Optional[str]:
        """Bag.get: the first pair whose key equals ``key`` without case (a lower-cased
        map made once)."""
        low = self._low
        if low is None:
            low = self._low = {}
            for k, v in self.props.pairs:
                low.setdefault(k.lower(), v)
        return low.get(key.lower())


def _text_font(r) -> Optional[tuple]:
    """(face, points, orient, charset, caption) from a text record's appearance
    part: [i16 x,y][u16 orient/charset][u8 size][lpstr text][lpstr face]."""
    part = appearance_part(r)
    if len(part) < 9:
        return None
    try:
        oc = struct.unpack_from("<H", part, 4)[0]
        size = part[6]
        n = struct.unpack_from("<H", part, 7)[0]
        caption = part[9 : 9 + n].decode(GP.CODEC, "replace")
        o = 9 + n
        m = struct.unpack_from("<H", part, o)[0]
        face = part[o + 2 : o + 2 + m].decode(GP.CODEC, "replace")
    except struct.error, IndexError:
        return None
    charset = (oc >> 8) if (oc & 0x80) else G.DEFAULT_CHARSET
    return face, size, oc & 0x7F, charset, caption


# --- which animations an object carries --------------
F_VIS, F_MVX, F_MVY, F_ROT, F_SZX, F_SZY, F_COL, F_FIL = 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80, 0x100
F_MVXSL, F_MVYSL, F_ROTSL, F_DIS = 0x1000, 0x2000, 0x4000, 0x8000
F_TXT = 0x80000


#: the keys whose value can set a flag of :func:`_anim_flags` (lower case); the others it
#: reads only choose among these (_ANIM_MODE_KEYS). tests/test_ctf.py checks the function
#: reads no key outside the two.
_ANIM_FLAG_KEYS = frozenset(
    k.lower()
    for k in (
        "VISIBLE",
        "MVX",
        "MVY",
        "ROT",
        "MVXSL",
        "MVYSL",
        "ROTSL",
        "SZX",
        "SZY",
        "COL_A",
        "COL_B",
        "COL_C",
        "COL_D",
        "COL_E",
        "COL_ARR",
        "COL_EXP",
        "FIL",
        "DISABLED",
        "TXT_A",
        "TXT_B",
        "TXT_C",
        "TXT_D",
        "TXT_E",
        "TXT_STR",
    )
)
_ANIM_MODE_KEYS = frozenset(k.lower() for k in ("MVXSLMODE", "MVYSLMODE", "ROTSLMODE", "COLMODE", "TXTMODE"))


def _anim_flags(o: _Obj) -> int:
    # (a static object has none of the keys that set a flag: nothing to look up)
    o.get("")
    if _ANIM_FLAG_KEYS.isdisjoint(o._low):
        return 0
    f = 0
    g = o.get
    if _nb(g("VISIBLE")):
        f |= F_VIS
    for key, sl, bit, slbit, slkey in (
        ("MVX", "MVXSLMODE", F_MVX, F_MVXSL, "MVXSL"),
        ("MVY", "MVYSLMODE", F_MVY, F_MVYSL, "MVYSL"),
        ("ROT", "ROTSLMODE", F_ROT, F_ROTSL, "ROTSL"),
    ):
        if c_atoi(g(sl)) & 1:
            if _nb(g(slkey)):
                f |= slbit
        elif _nb(g(key)):
            f |= bit
    if _nb(g("SZX")):
        f |= F_SZX
    if _nb(g("SZY")):
        f |= F_SZY
    mode = c_atoi(g("COLMODE"))
    if mode == 0:
        col = _nb(g("COL_A"))
    elif mode == 1:
        col = any(_nb(g("COL_" + c)) for c in "ABCDE")
    else:
        col = _nb(g("COL_ARR")) or _nb(g("COL_EXP"))
    if col:
        f |= F_COL
    if _nb(g("FIL")):
        f |= F_FIL
    if _nb(g("DISABLED")):
        f |= F_DIS
    if o.tc == TEXT_TC:
        tm = c_atoi(g("TXTMODE"))
        if tm in (0, 2):
            t = _nb(g("TXT_A"))
        elif tm == 1:
            t = any(_nb(g("TXT_" + c)) for c in "ABCDE")
        else:
            t = _nb(g("TXT_STR"))
        if t:
            f |= F_TXT
    return f


def _effect_depth(o: _Obj) -> int:
    """The 3D effect depth (the text class: its point size / 20, x1.5 bold)."""
    st = o.style
    if st is None:
        return 0
    d = st[5]
    if d != 0xFF:
        return d
    eff = st[0] & 0xF
    if o.tc == TEXT_TC:
        if not eff:
            return 0
        pts = o.font[1] if o.font else G.text_points(o.po)
        return G.text_auto_depth(pts, bool(st[3] & 0x10)) if pts else 1
    l, t, r, b = o.rect1
    ext = (b - t) + (r - l)
    if eff == 0:
        d = 0
    elif eff in (1, 2, 4):
        d = _cdiv(ext, 500) + 1
    elif eff == 3:
        d = _cdiv(ext, 200) + 1
    else:
        d = 1
    if not (st[2] & 0xF) and st[4] < d:
        d = st[4]
    return d


def _expand(o: _Obj, box, flags: int, rect=None) -> Tuple[bool, tuple]:
    """Grow the region by the animations' sweep;
    returns (animated, box). ``rect`` = the object's bounds the sweep is centred on (its
    bounds; default o.bounds): a translatable text's are its
    GROWN ones, as the sweep runs before the transform is restored."""
    g = o.get
    anim = bool(flags & (F_VIS | F_COL | F_FIL | F_DIS))
    rect = o.bounds if rect is None else rect
    if flags & F_SZX:
        anim = True
        org, mode = c_atoi(g("SZXORG")), c_atoi(g("SZXMODE"))
        if mode == 0:
            c = rect[0] if org == -1 else (rect[2] if org == 1 else _cdiv(rect[0] + rect[2], 2))
        else:
            c = _cdiv(rect[0] + rect[2], 2) + org
        s = max(c_atoi(g("SZXMINPC")), c_atoi(g("SZXMAXPC")))
        box = _set_rect((c - _cdiv((c - box[0]) * s, 100), box[1], c - _cdiv((c - box[2]) * s, 100), box[3]))
    if flags & F_SZY:
        anim = True
        org, mode = c_atoi(g("SZYORG")), c_atoi(g("SZYMODE"))
        if mode == 0:
            c = rect[1] if org == -1 else (rect[3] if org == 1 else _cdiv(rect[1] + rect[3], 2))
        else:
            c = _cdiv(rect[1] + rect[3], 2) + org
        s = max(c_atoi(g("SZYMINPC")), c_atoi(g("SZYMAXPC")))
        box = _set_rect((box[0], c - _cdiv((c - box[1]) * s, 100), box[2], c - _cdiv((c - box[3]) * s, 100)))
    # (an empty region stays empty: each step below turns, offsets and ORs it, so a scale
    # with no percentages, SetRectRgn of a flat box, sweeps nothing)
    if flags & (F_ROT | F_ROTSL):
        anim = True
        if not _empty(box):
            box = _rot_sweep(o, box, rect)
    if flags & (F_MVX | F_MVXSL):
        anim = True
        lo, hi = -c_atoi(g("MVXMINPT")), c_atoi(g("MVXMAXPT"))
        box = _or(box, _or(_offset(box, lo, 0), _offset(box, hi, 0)))
    if flags & (F_MVY | F_MVYSL):
        anim = True
        lo, hi = c_atoi(g("MVYMINPT")), -c_atoi(g("MVYMAXPT"))
        box = _or(box, _or(_offset(box, 0, lo), _offset(box, 0, hi)))
    if anim and o.style is not None:
        eff = o.style[0]
        if eff in (1, 2, 4):
            d = _effect_depth(o)
            box = _or(_offset(box, -d, -d), _offset(box, d, d))
        elif eff == 3:
            d = _effect_depth(o)
            box = _or(box, _offset(box, d, d))
    return anim, box


def rotation_origin(bounds, mode: int, xorg: int, yorg: int) -> Tuple[int, int]:
    """The point a rotation turns about, in page pixels."""
    l, t, r, b = bounds
    cx, cy = _cdiv(l + r, 2), _cdiv(t + b, 2)
    if mode:
        return cx + xorg, cy + yorg
    x = l if xorg == -1 else (r if xorg == 1 else cx)
    y = t if yorg == -1 else (b if yorg == 1 else cy)
    return x, y


#: CtDraw's constants: the runtime's pi is 3.14159265359, not math.pi
_PI, _HALF_PI, _PI_3_2 = 3.14159265359, 1.570796326795, 4.712388980385
_ROT_EPS = 0.0005


def _rot_matrix(deg: int, ox: int, oy: int):
    """The rotation matrix for ``deg`` degrees about (ox, oy)."""
    import math

    rad = (deg * _PI) / 180.0
    if abs(rad) < _ROT_EPS:
        return None
    org = abs(-ox) >= _ROT_EPS or abs(-oy) >= _ROT_EPS
    e, f = (-float(ox), -float(oy)) if org else (0.0, 0.0)
    if abs(rad - _HALF_PI) < _ROT_EPS:
        a, b, c, d = (-0.0 if org else 0.0), 1.0, -1.0, 0.0
        e, f = -f, e
    elif abs(rad - _PI) < _ROT_EPS:
        a, b, c, d = -1.0, (-0.0 if org else 0.0), (-0.0 if org else 0.0), -1.0
        e, f = -e, -f
    elif abs(rad + _HALF_PI) < _ROT_EPS or abs(rad - _PI_3_2) < _ROT_EPS:
        a, b, c, d = 0.0, -1.0, 1.0, (-0.0 if org else 0.0)
        e, f = f, -e
    else:
        cs, sn = math.cos(rad), math.sin(rad)
        a, b, c, d = cs, sn, -sn, cs
        if org:
            e, f = (-sn) * f + e * cs, f * cs + e * sn
    if org:
        e, f = e + ox, f + oy
    return a, b, c, d, e, f


def _rot_point(m, x: int, y: int) -> Tuple[int, int]:
    """The general case: C (int) truncation of each coordinate."""
    a, b, c, d, e, f = m
    return int(y * c + x * a + e), int(y * d + x * b + f)


def rotation_polygons(box, origin, lo: int, hi: int) -> List[List[Tuple[int, int]]]:
    """The box's corner polygon at every whole degree from ``lo`` to ``hi``
    (the precise region adds to the dynamic
    area): (l,t) (r,t) (r,b) (l,b) through :func:`_rot_matrix`."""
    l, t, r, b = box
    corners = ((l, t), (r, t), (r, b), (l, b))
    ox, oy = origin
    if hi - lo > 720:
        # beyond two full turns (never typed: the page limits both angles to
        # -360..360, so -ROTMINPC..ROTMAXPC spans at most 720): every angle
        # has been swept twice over
        hi = lo + 720
    out = []
    for deg in range(lo, hi + 1):
        m = _rot_matrix(deg, ox, oy)
        out.append([_rot_point(m, x, y) for x, y in corners] if m is not None else list(corners))
    return out


def _polygon_union_box(polys):
    """The box of the union of CreatePolygonRgn(ALTERNATE) regions -- real
    GDI when available (its fill rule leaves out right / bottom edge
    pixels), else the vertices' box."""
    api = _GdiApi.get()
    if api is not None:
        try:
            ct, g = api.ct, api.g
            rgn = g.CreateRectRgn(0, 0, 0, 0)
            try:
                for p in polys:
                    arr = (ct.c_long * 8)(*[v for pt in p for v in pt])
                    pr = g.CreatePolygonRgn(arr, 4, 1)
                    if pr:
                        g.CombineRgn(rgn, rgn, pr, 2)  # RGN_OR
                        g.DeleteObject(pr)
                box = (ct.c_long * 4)()
                g.GetRgnBox(rgn, box)
                return tuple(box)
            finally:
                g.DeleteObject(rgn)
        except Exception:  # noqa: BLE001 -- fall back to the vertex box
            _note_exc("ctf._polygon_union_box")
            pass
    xs = [x for p in polys for x, _y in p]
    ys = [y for p in polys for _x, y in p]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def _rot_sweep(o: _Obj, box, rect=None):
    """The area a rotating object sweeps."""
    g = o.get
    origin = rotation_origin(
        o.bounds if rect is None else rect, c_atoi(g("ROTMODE")), c_atoi(g("ROTXORG")), c_atoi(g("ROTYORG"))
    )
    lo, hi = sorted((-c_atoi(g("ROTMINPC")), c_atoi(g("ROTMAXPC"))))
    return _polygon_union_box(rotation_polygons(box, origin, lo, hi))


def _inflate3d(o: _Obj, box):
    """The base bounds after the class extent: the 3D-effect inflation."""
    eff = o.style[0] if o.style is not None else 0
    if eff in (1, 2, 4):
        d = _effect_depth(o)
        return (box[0] - d, box[1] - d, box[2] + d, box[3] + d)
    if eff == 3:
        d = _effect_depth(o)
        return (box[0], box[1], box[2] + d, box[3] + d)
    return box


def _grow_map(o: _Obj):
    """The 1.3x scale that translatable text labels get."""
    orient = o.font[2] if o.font is not None else 1
    ax, ay = o.anchor
    if orient in (1, 2):
        return (1.3, 0.0, 0.0, 1.0, ax - 1.3 * ax, 0.0)
    return (1.0, 0.0, 0.0, 1.3, 0.0, ay - 1.3 * ay)


def _map_box(T, box):
    from . import placement as PL

    R = PL.apply_rect(T, 8, box)
    return tuple(int(v) for v in R)


def _text_ext(o: _Obj, s: str, T=None):
    """the text's bounds measured with the string s
    (through the grown transform ``T`` when given)."""
    from . import textmetrics as TM

    if o.font is None or o.style is None:
        return None
    face, size, orient, charset = o.font
    box = TM.extent(s or " ", face, size, o.style[3], orient, charset, tuple(o.anchor))
    if box is None:
        return None
    if T is not None:
        box = _map_box(T, box)
    return _inflate3d(o, box)


def _translatable(o: _Obj, flags: int) -> bool:
    """the displayed string carries an '@(' language label."""
    from . import textmetrics as TM

    g = o.get
    if not (flags & F_TXT):
        s = TM.measured_string(o.po, o.caption)
        return s is not None and "@(" in s
    mode = c_atoi(g("TXTMODE"))
    if mode == 0:
        return "@(" in (g("TXT_OFF") or "") or "@(" in (g("TXT_ON") or "")
    if mode in (1, 2):
        pre, n = ("STATE_", 32) if mode == 1 else ("ARRAY_", 256)
        return any("@(" in (g("%s%d" % (pre, k)) or "") for k in range(n))
    return "@(" in (g("TXT_STR") or "")


def _text_region(o: _Obj, flags: int):
    """A text's dynamic region."""
    g = o.get
    grow = _translatable(o, flags)
    T = _grow_map(o) if grow else None
    if not (flags & F_TXT):
        if not grow:
            anim, box = _expand(o, o.bounds, flags)
            return box if anim else None
        # bounds + 'Wg' under the grown transform; no sweep
        box = _inflate3d(o, _map_box(T, o.rect1))
        return _union(box, _text_ext(o, "Wg", T))
    mode = c_atoi(g("TXTMODE"))
    if mode == 4:
        return _expand(o, _union(o.bounds, _text_ext(o, _W270)), flags)[1]
    if mode == 0:
        box = _union(_text_ext(o, g("TXT_OFF") or "", T), _text_ext(o, g("TXT_ON") or "", T))
    elif mode in (1, 2):
        box = None
        pre, n = ("STATE_", 32) if mode == 1 else ("ARRAY_", 256)
        for k in range(n):
            v = g("%s%d" % (pre, k))
            if v:
                box = _union(box, _text_ext(o, v, T))
    else:
        box = _inflate3d(o, _map_box(T, o.rect1)) if grow else o.bounds
    if grow:
        box = _union(box, _text_ext(o, "Wg", T))
    if box is None:
        box = o.bounds
    # (the sweep runs under the grown transform: it sees the grown bounds)
    return _expand(o, box, flags, _inflate3d(o, _map_box(T, o.rect1)) if grow else None)[1]


def _region(o: _Obj, flags: int):
    """The dynamic region of a non-container record: its box, or
    None (static)."""
    tc = o.tc
    if tc == TEXT_TC:
        return _text_region(o, flags)
    if tc in _ANIM_NO_REGION:
        return None
    if tc in _ANIM_RECT_REGION:
        return o.bounds
    anim, box = _expand(o, o.bounds, flags)
    return box if anim else None


def bake_plan(
    objs: Sequence[PageObject], recs: Optional[Sequence[tuple]] = None, page_size: Tuple[int, int] = (0, 0)
):
    """every top-level object gets the dynamic
    region.  Returns (baked, keep, par, drawn) -- baked: records with the
    in-memory 0x200 bake flag; keep: groups with a dynamic child (0x8000);
    drawn: the baked leaves in the order they were drawn into the DIB."""
    grid = _Grid()
    baked: set = set()
    keep: set = set()
    drawn: List[int] = []
    children: Dict[int, List[int]] = {}
    par = forest_parents(objs)
    for i, p in enumerate(par):
        if p is not None:
            children.setdefault(p, []).append(i)
    cache: Dict[int, _Obj] = {}

    def obj(i: int) -> _Obj:
        o = cache.get(i)
        if o is None:
            o = cache[i] = _Obj(objs[i], *recs[i]) if recs is not None else _Obj(objs[i])
        return o

    def dynamic(i: int) -> bool:
        # dynamic: the saver's dwFlags 0x10 records it (a member defers to
        # its group / set, already folded in); text / ActiveX / WebContent: 1
        po = objs[i]
        return bool(po.subtype_size & 0x10) or po.type_code in _ALWAYS_DYNAMIC

    def child_region(c: int):
        tc = objs[c].type_code
        if tc == SYMSET_TC:
            return set_region(c)
        if tc == GROUP_TC:
            return group_region(c)
        oc = obj(c)
        return _region(oc, _anim_flags(oc))

    def set_region(i: int):
        """A symbol set's region: the union of its children's regions / bounds
        (Keyboard / Touch / DummyMarker skipped), grown by its own sweep."""
        box = None
        for c in children.get(i, []):
            if objs[c].type_code in (_codes.TC.KEYBOARD, _codes.TC.TOUCH, 0xFFFF):
                continue
            rg = child_region(c)
            box = _or(box, rg if rg is not None else obj(c).bounds)
        if box is None:
            return None
        o = obj(i)
        return _expand(o, box, _anim_flags(o))[1]

    def group_region(i: int):
        """A group's region: the union of its children's regions, then its sweep."""
        box = None
        for c in children.get(i, []):
            box = _or(box, child_region(c))
        if box is None:
            return None
        o = obj(i)
        anim, box2 = _expand(o, box, _anim_flags(o))
        return box2

    def visit(i: int) -> int:
        po = objs[i]
        tc = po.type_code
        if tc == GROUP_TC:
            # set the region: flag itself, recurse, 0x8000 = a dynamic child
            baked.add(i)
            res = 0
            kids = children.get(i, [])
            for c in kids:
                res |= visit(c)
            if res:
                keep.add(i)
            return res
        if tc in _ANIM_NO_REGION:
            return 1
        if tc == COLOURAN_TC:
            grid.add((0, 0, page_size[0], page_size[1]))
            return 1
        o = obj(i)
        if tc == SYMSET_TC:
            rg = set_region(i)
        else:
            rg = _region(o, _anim_flags(o))
        if rg is None:
            # bake a static object no dynamic area covers
            r = o.bounds
            if not dynamic(i) and not grid.hits(r):
                baked.add(i)
                drawn.append(i)
                return 0
            grid.add(r)
            return 1
        grid.add(_norm(rg))
        return 1

    tops = [i for i, p in enumerate(par) if p is None]
    for i in tops:
        visit(i)
    return baked, keep, par, drawn


def ctf_plan(
    objs: Sequence[PageObject], recs: Optional[Sequence[tuple]] = None, page_size: Tuple[int, int] = (0, 0)
):
    """(layout, drawn): per record 'write', 'marker' or 'skip', and the baked leaves in
    draw order; ``recs`` = per record (its decode, its .ctF record)."""
    baked, keep, par, drawn = bake_plan(objs, recs, page_size)
    out = []
    skip_sub = set()
    for i, po in enumerate(objs):
        p = par[i]
        if p is not None and p in skip_sub:
            skip_sub.add(i)
            out.append("skip")
            continue
        if (
            i in baked
            and not (po.subtype_size & 0x10)
            and po.type_code not in _ALWAYS_DYNAMIC
            and i not in keep
            and not _in_set(objs, par, i)
        ):
            if p is None or p not in keep:
                out.append("skip")
            else:
                out.append("marker")
            skip_sub.add(i)
            continue
        out.append("write")
    return out, drawn


def _in_set(objs, par, i) -> bool:
    p = par[i]
    while p is not None:
        if objs[p].type_code == SYMSET_TC:
            return True
        p = par[p]
    return False


# --------------------------------------------------------------------------- the background DIB
class _GdiApi:
    """gdi32 / user32 through ctypes (Windows); ``get()`` is None elsewhere."""

    _inst = None
    _failed = False

    @classmethod
    def get(cls):
        if cls._inst is None and not cls._failed:
            try:
                cls._inst = cls()
            except Exception:  # noqa: BLE001
                _note_exc("ctf._GdiApi.get")
                cls._failed = True
        return cls._inst

    def __init__(self):
        import ctypes
        from ctypes import wintypes as W

        self.ct = ctypes
        g = ctypes.WinDLL("gdi32")
        u = ctypes.WinDLL("user32")
        H, I, B = W.HANDLE, ctypes.c_int, W.BOOL
        sig = {
            "CreateCompatibleDC": (H, [H]),
            "DeleteDC": (B, [H]),
            "CreateDIBSection": (
                H,
                [H, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), H, W.DWORD],
            ),
            "SelectObject": (H, [H, H]),
            "DeleteObject": (B, [H]),
            "GetStockObject": (H, [I]),
            "CreatePen": (H, [I, I, W.DWORD]),
            "ExtCreatePen": (H, [W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.c_void_p]),
            "CreateSolidBrush": (H, [W.DWORD]),
            "CreateHatchBrush": (H, [I, W.DWORD]),
            "Rectangle": (B, [H, I, I, I, I]),
            "RoundRect": (B, [H, I, I, I, I, I, I]),
            "Ellipse": (B, [H, I, I, I, I]),
            "Pie": (B, [H, I, I, I, I, I, I, I, I]),
            "Arc": (B, [H, I, I, I, I, I, I, I, I]),
            "Chord": (B, [H, I, I, I, I, I, I, I, I]),
            "Polygon": (B, [H, ctypes.c_void_p, I]),
            "Polyline": (B, [H, ctypes.c_void_p, I]),
            "MoveToEx": (B, [H, I, I, ctypes.c_void_p]),
            "LineTo": (B, [H, I, I]),
            "SetWindowOrgEx": (B, [H, I, I, ctypes.c_void_p]),
            "SetBkMode": (I, [H, I]),
            "SetBkColor": (W.DWORD, [H, W.DWORD]),
            "SaveDC": (I, [H]),
            "RestoreDC": (B, [H, I]),
            "GdiFlush": (B, []),
            "CreateRectRgn": (H, [I, I, I, I]),
            "CreateRoundRectRgn": (H, [I, I, I, I, I, I]),
            "SelectClipRgn": (I, [H, H]),
            "SetPolyFillMode": (I, [H, I]),
            "StretchDIBits": (
                I,
                [H, I, I, I, I, I, I, I, I, ctypes.c_void_p, ctypes.c_void_p, W.UINT, W.DWORD],
            ),
            "SetStretchBltMode": (I, [H, I]),
            "BitBlt": (B, [H, I, I, I, I, H, I, I, W.DWORD]),
            "StretchBlt": (B, [H, I, I, I, I, H, I, I, I, I, W.DWORD]),
            "CreatePolygonRgn": (H, [ctypes.c_void_p, I, I]),
            "CombineRgn": (I, [H, H, H, I]),
            "CreateEllipticRgn": (H, [I, I, I, I]),
            "GetRgnBox": (I, [H, ctypes.c_void_p]),
            "BeginPath": (B, [H]),
            "EndPath": (B, [H]),
            "FillPath": (B, [H]),
            "PathToRegion": (H, [H]),
            "SetMapMode": (I, [H, I]),
            "GetWindowOrgEx": (B, [H, ctypes.c_void_p]),
            "SetViewportOrgEx": (B, [H, I, I, ctypes.c_void_p]),
            "SetViewportExtEx": (B, [H, I, I, ctypes.c_void_p]),
            "SetMetaFileBitsEx": (H, [W.UINT, ctypes.c_void_p]),
            "PlayMetaFile": (B, [H, H]),
            "DeleteMetaFile": (B, [H]),
        }
        for name, (res, args) in sig.items():
            f = getattr(g, name)
            f.restype, f.argtypes = res, args
        u.GetDC.restype, u.GetDC.argtypes = H, [H]
        u.ReleaseDC.restype, u.ReleaseDC.argtypes = I, [H, H]
        m = ctypes.WinDLL("msimg32")
        m.GradientFill.restype = B
        m.GradientFill.argtypes = [H, ctypes.c_void_p, W.ULONG, ctypes.c_void_p, W.ULONG, W.ULONG]
        self.g, self.u, self.m = g, u, m


def _cref(c: int) -> int:
    """0x00RRGGBB -> COLORREF 0x00BBGGRR (the draw code's byte swap)."""
    return ((c & 0xFF) << 16) | (c & 0xFF00) | ((c >> 16) & 0xFF)


class _Dib:
    """A top-down 32bpp DIB section
    filled with the page colour, selected into a memory DC."""

    def __init__(self, api, w: int, h: int, colour: int, fill: bool = True):
        ct = api.ct
        self.api, self.w, self.h = api, w, h
        hdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bmi = ct.create_string_buffer(hdr + b"\0" * 16)
        self.bits = ct.c_void_p()
        screen = api.u.GetDC(None)
        self.dc = api.g.CreateCompatibleDC(screen)
        api.u.ReleaseDC(None, screen)
        self.bmp = api.g.CreateDIBSection(self.dc, self.bmi, 0, ct.byref(self.bits), None, 0)
        if not self.bmp or not self.bits:
            raise OSError("CreateDIBSection failed")
        self.old = api.g.SelectObject(self.dc, self.bmp)
        if fill:
            px = struct.pack("<I", colour & 0x00FFFFFF)  # B,G,R,0 = 0x00RRGGBB
            ct.memmove(self.bits, px * (w * h), 4 * w * h)

    def load(self, pixels: bytes) -> None:
        self.api.ct.memmove(self.bits, pixels, 4 * self.w * self.h)

    def pixels(self) -> bytes:
        self.api.g.GdiFlush()
        return self.api.ct.string_at(self.bits, 4 * self.w * self.h)

    def close(self) -> None:
        g = self.api.g
        g.SelectObject(self.dc, self.old)
        g.DeleteObject(self.bmp)
        g.DeleteDC(self.dc)


class _Look:
    """The in-memory appearance fields the draw code reads."""

    __slots__ = ("effects", "line_style", "fill_style", "attribs", "width", "depth", "pairs")

    def __init__(self, style: bytes):
        st = style if style is not None else DEFAULT_STYLE
        self.effects, self.line_style, self.fill_style, self.attribs = st[0], st[1], st[2], st[3]
        self.width = st[4]
        self.depth = st[5] - 256 if st[5] >= 128 else st[5]
        self.pairs = [struct.unpack_from("<II", st, 6 + 8 * k) for k in range(6)]


_PAIR_OF_SEL = {0: 0, 1: 3, 2: 4, 3: 5}  # 0 own line/fill, 1 hilight, 2 lolight, 3 shadow


class _Painter:
    """CtDraw's per-class drawing over the bake DC."""

    def __init__(self, api, dib: _Dib, page_bg: int):
        self.api, self.g, self.dc, self.page_bg = api, api.g, dib.dc, page_bg
        self.org = (0, 0)

    # -- GDI objects (pen / brush) ----------------
    def _pen(self, look: _Look, sel: int, width: Optional[int] = None):
        g = self.g
        if sel == 4:
            off = self.page_bg
        else:
            off = look.pairs[_PAIR_OF_SEL[sel]][1]
        if off & 0xFF000000:
            return g.GetStockObject(8)  # NULL_PEN
        w = look.width if width is None else width
        ls = look.line_style
        if not (ls & 0x30) and not (ls & 0xC0):
            return g.CreatePen(ls & 0xF, w, _cref(off))
        lb = struct.pack("<IIi", 0, _cref(off), 0)
        buf = self.api.ct.create_string_buffer(lb)
        return g.ExtCreatePen(((ls & 0xC0) << 6) | ((ls & 0x30) << 4) | (ls & 0xF) | 0x10000, w, buf, 0, None)

    def _brush(self, look: _Look, sel: int):
        g = self.g
        if sel == 4:
            off = self.page_bg
        else:
            off = look.pairs[1 if sel == 0 else _PAIR_OF_SEL[sel]][1]
        fs = look.fill_style & 0xF
        if fs and not (off & 0xFF000000):
            if fs == 1:
                return g.CreateSolidBrush(_cref(off))
            return g.CreateHatchBrush(fs - 2, _cref(off))
        return g.GetStockObject(5)  # NULL_BRUSH

    def _org(self, dx: int, dy: int) -> None:
        self.org = (dx, dy)
        self.g.SetWindowOrgEx(self.dc, dx, dy, None)

    def _with(self, pen, brush, fn) -> None:
        g, dc = self.g, self.dc
        g.SetBkMode(dc, 1)
        op = g.SelectObject(dc, pen)
        ob = g.SelectObject(dc, brush)
        fn()
        g.DeleteObject(g.SelectObject(dc, ob))
        g.DeleteObject(g.SelectObject(dc, op))

    # -- Square
    def gradient(self, box, c1: int, c2: int, mode: int) -> None:
        """A gradient fill of ``box`` from c1 to c2 by ``mode``: msimg32
        GradientFill over TRIVERTEX (x, y, R<<8, G<<8, B<<8, 0xff00);
        1 L>R, 2 R>L, 3 T>B, 4 B>T, 5/6 H edge<->middle, 7/8 V edge<->middle."""
        l, t, r, b = box
        if mode < 1 or mode > 8:
            return

        def v(x, y, c):
            return struct.pack(
                "<iiHHHH", x, y, ((c >> 16) & 0xFF) << 8, ((c >> 8) & 0xFF) << 8, (c & 0xFF) << 8, 0xFF00
            )

        if mode in (1, 2, 3, 4):
            a, z = (c1, c2) if mode in (1, 3) else (c2, c1)
            verts = v(l, t, a) + v(r, b, z)
            mesh = struct.pack("<II", 0, 1)
            nv, nm = 2, 1
            fm = 0 if mode in (1, 2) else 1
        else:
            e, m_ = (c1, c2) if mode in (5, 7) else (c2, c1)
            if mode in (5, 6):
                mid = l + _cdiv(r - l, 2)
                verts = v(l, t, e) + v(mid, b, m_) + v(mid, t, m_) + v(r, b, e)
                fm = 0
            else:
                mid = t + _cdiv(b - t, 2)
                verts = v(l, t, e) + v(r, mid, m_) + v(l, mid, m_) + v(r, b, e)
                fm = 1
            mesh = struct.pack("<IIII", 0, 1, 2, 3)
            nv, nm = 4, 2
        ct = self.api.ct
        vb, mb = ct.create_string_buffer(verts), ct.create_string_buffer(mesh)
        self.api.m.GradientFill(self.dc, vb, nv, mb, nm, fm)

    def square(self, look: _Look, rect, radius: int, depth: int, gmode: int = 0, gcol: int = 0) -> None:
        l, t, r, b = rect
        eff = look.effects & 0xF

        def body(ps, bs):
            g, dc = self.g, self.dc
            brush = None
            if bs == 0 and (look.fill_style & 0xF) and gmode:
                # the gradient clipped to the (round) rect, then
                # the outline over it with the hollow brush
                if radius == 0:
                    rg = g.CreateRectRgn(l, t, r + 1, b + 1)
                else:
                    rg = g.CreateRoundRectRgn(l, t, r + 2, b + 2, radius * 2, radius * 2)
                if rg:
                    g.SelectClipRgn(dc, rg)
                    self.gradient((l, t, r + 1, b + 1), look.pairs[1][0], gcol, gmode)
                    g.SelectClipRgn(dc, None)
                    g.DeleteObject(rg)
                brush = g.GetStockObject(5)

            def fn():
                if radius == 0:
                    g.Rectangle(dc, l, t, r + 1, b + 1)
                else:
                    g.RoundRect(dc, l, t, r + 1, b + 1, radius * 2, radius * 2)

            self._with(self._pen(look, ps), brush if brush is not None else self._brush(look, bs), fn)

        d = depth
        hollow_or_round = (look.fill_style & 0xF) == 0 or radius != 0
        if eff == 1:
            if hollow_or_round:
                self._org(-d, -d)
                body(2, 2)
                self._org(d, d)
                body(1, 1)
                self._org(0, 0)
                body(0, 0)
                return
            body(0, 0)
            self._bevel_br(look, rect, 2, d)
            self._bevel_tl(look, rect, 1, d)
        elif eff == 2:
            if hollow_or_round:
                self._org(-d, -d)
                body(1, 1)
                self._org(d, d)
                body(2, 2)
                self._org(0, 0)
                body(0, 0)
                return
            body(0, 0)
            self._bevel_tl(look, rect, 2, d)
            self._bevel_br(look, rect, 1, d)
        elif eff == 3:
            self._org(-d, -d)
            body(3, 3)
            self._org(0, 0)
            body(0, 0)
        elif eff == 4:
            if (look.fill_style & 0xF) == 0 and look.width == 1:
                body(1, 1)
                self._org(d, d)
                body(2, 2)
                self._org(0, 0)
            else:
                self._org(-d, -d)
                body(1, 1)
                self._org(d, d)
                body(2, 2)
                self._org(0, 0)
                body(4, 4)
            return
        else:
            body(0, 0)
        self._frame(look, rect, depth)

    def _bevel_rect(self, look: _Look, rect, d: int):
        l, t, r, b = rect
        r, b = r + 1, b + 1
        w = look.width
        h = w // 2
        l, t, r, b = l - h, t - h, r + h, b + h
        odd = (w + 1) & 1
        r, b = r - odd, b - odd
        return (l - d, t - d, r + d, b + d)

    def _bevel_pen(self, look: _Look, sel: int):
        pair = look.pairs[4] if sel == 2 else look.pairs[3]
        off = pair[1]
        if off & 0xFF000000:
            return None
        return self.g.CreatePen(0, 1, _cref(off))

    def _bevel_br(self, look: _Look, rect, sel: int, d: int) -> None:
        """the bottom / right bevel strokes."""
        pen = self._bevel_pen(look, sel)
        if pen is None:
            return
        l, t, r, b = self._bevel_rect(look, rect, d)
        n = d + (1 if (sel == 2 and look.attribs & 0x20) else 0)
        g, dc = self.g, self.dc
        op = g.SelectObject(dc, pen)
        for i in range(n):
            g.MoveToEx(dc, r - i - 1, b - 1, None)
            g.LineTo(dc, r - i - 1, t + i)
            g.MoveToEx(dc, r - 1, b - i - 1, None)
            g.LineTo(dc, l - 1 + i, b - i - 1)
        g.DeleteObject(g.SelectObject(dc, op))

    def _bevel_tl(self, look: _Look, rect, sel: int, d: int) -> None:
        """the top / left bevel strokes."""
        pen = self._bevel_pen(look, sel)
        if pen is None:
            return
        l, t, r, b = self._bevel_rect(look, rect, d)
        n = d + (1 if (sel == 2 and look.attribs & 0x20) else 0)
        g, dc = self.g, self.dc
        op = g.SelectObject(dc, pen)
        for i in range(n):
            g.MoveToEx(dc, l + i, t, None)
            g.LineTo(dc, l + i, b - i)
            g.MoveToEx(dc, l, t + i, None)
            g.LineTo(dc, r - i, t + i)
        g.DeleteObject(g.SelectObject(dc, op))

    def _frame(self, look: _Look, rect, depth: int) -> None:
        """attribs 0x10 = the extra outer black frame."""
        if not (look.attribs & 0x10):
            return
        l, t, r, b = rect
        r, b = r + 1, b + 1
        w = look.width
        k = w // 2 + 1
        l, t, r, b = l - k, t - k, r + k, b + k
        odd = (w + 1) & 1
        r, b = r - odd, b - odd
        if (look.effects & 0xF) in (1, 2, 4):
            l, t, r, b = l - depth, t - depth, r + depth, b + depth
        g, dc = self.g, self.dc
        op = g.SelectObject(dc, g.GetStockObject(7))  # BLACK_PEN
        ob = g.SelectObject(dc, g.GetStockObject(5))  # NULL_BRUSH
        g.Rectangle(dc, l, t, r, b)
        g.SelectObject(dc, ob)
        g.SelectObject(dc, op)

    # -- Bitmap
    def bitmap(self, dest, w: int, h: int, pixels: bytes) -> None:
        """Masked stretch blit of a DIB section."""
        g, api = self.g, self.api
        l, t, r, b = dest
        n = w * h
        alpha = pixels[3::4]
        m = alpha.translate(bytes([0]) + bytes([0xFF]) * 255)
        mask = bytearray(4 * n)
        mask[0::4] = m
        mask[1::4] = m
        mask[2::4] = m
        A = _Dib(api, w, h, 0, fill=False)
        B = _Dib(api, w, h, 0, fill=False)
        img = bytearray(pixels)
        img[3::4] = bytes(n)  # the image copy carries no mask byte
        try:
            A.load(bytes(mask))
            B.load(bytes(img))
            g.BitBlt(B.dc, 0, 0, w, h, A.dc, 0, 0, 0x220326)  # DSna
            g.SetStretchBltMode(self.dc, 2)  # WHITEONBLACK
            g.StretchBlt(self.dc, l, t, r - l, b - t, A.dc, 0, 0, w, h, 0x8800C6)  # SRCAND
            g.SetStretchBltMode(self.dc, 3)  # COLORONCOLOR
            g.StretchBlt(self.dc, l, t, r - l, b - t, B.dc, 0, 0, w, h, 0xEE0086)  # SRCPAINT
        finally:
            A.close()
            B.close()

    # -- the 3D-effect switch shared by Line / PolyLine / Circle
    #: body(pen sel, brush sel)
    def effects(self, look: _Look, depth: int, body) -> None:
        d = depth
        eff = look.effects & 0xF
        if eff == 0:
            body(0, 0)
        elif eff == 1:
            self._org(-d, -d)
            body(2, 2)
            self._org(d, d)
            body(1, 1)
            self._org(0, 0)
            body(0, 0)
        elif eff == 2:
            self._org(-d, -d)
            body(1, 1)
            self._org(d, d)
            body(2, 2)
            self._org(0, 0)
            body(0, 0)
        elif eff == 3:
            self._org(-d, -d)
            body(3, 3)
            self._org(0, 0)
            body(0, 0)
        elif eff == 4:
            if (look.fill_style & 0xF) == 0 and look.width == 1:
                body(1, 1)
                self._org(d, d)
                body(2, 2)
                self._org(0, 0)
            else:
                self._org(-d, -d)
                body(1, 1)
                self._org(d, d)
                body(2, 2)
                self._org(0, 0)
                body(4, 4)

    # -- Line
    def line(self, look: _Look, p1, p2, depth: int) -> None:
        def body(ps, _bs):
            def fn():
                self.g.MoveToEx(self.dc, p1[0], p1[1], None)
                self.g.LineTo(self.dc, p2[0], p2[1])

            self._with(self._pen(look, ps), self.g.GetStockObject(5), fn)

        self.effects(look, depth, body)

    def _clip_gradient(self, rgn, c1: int, c2: int, mode: int) -> None:
        """Select ``rgn`` as the clip, gradient fill its box, unclip."""
        g, dc = self.g, self.dc
        if not rgn:
            return
        box = (self.api.ct.c_long * 4)()
        g.GetRgnBox(rgn, box)
        g.SelectClipRgn(dc, rgn)
        self.gradient(tuple(box), c1, c2, mode)
        g.SelectClipRgn(dc, None)
        g.DeleteObject(rgn)

    # -- PolyLine / polygon
    def polygon(self, look: _Look, pts, depth: int, gmode: int, gcol: int) -> None:
        ct, g, dc = self.api.ct, self.g, self.dc
        n = len(pts)
        arr = (ct.c_long * (2 * n))(*[v for p in pts for v in p])

        def body(ps, bs):
            brush = None
            if bs == 0 and (look.fill_style & 0xF) and gmode:
                self._clip_gradient(g.CreatePolygonRgn(arr, n, 1), look.pairs[1][0], gcol, gmode)
                brush = g.GetStockObject(5)

            def fn():
                if look.attribs == 0:
                    g.Polygon(dc, arr, n)
                elif look.attribs == 1:
                    if look.fill_style & 0xF:
                        op = g.SelectObject(dc, g.GetStockObject(8))
                        g.Polygon(dc, arr, n)
                        g.SelectObject(dc, op)
                    g.Polyline(dc, arr, n)

            self._with(self._pen(look, ps), brush if brush is not None else self._brush(look, bs), fn)

        self.effects(look, depth, body)

    # -- Circle: attribs 0 ellipse / 1 pie / 2 arc
    def ellipse(self, look: _Look, rect, depth: int, gmode: int, gcol: int, angles=(0, 0)) -> None:
        g, dc = self.g, self.dc
        l, t, r, b = rect
        r, b = r + 1, b + 1

        def s16(v):
            v &= 0xFFFF
            return v - 0x10000 if v & 0x8000 else v

        w = s16(look.width)
        hw = _cdiv(r - l, 2)
        u9 = w if w <= hw else s16(hw)
        hh = _cdiv(b - t, 2)
        u8 = u9 if u9 <= hh else s16(hh)
        if u8 < 1:
            u8 = 1
        k = _cdiv(u8 - 1, 2)
        box = (l + (u8 >> 1), t + (u8 >> 1), r - k, b - k)
        mode = look.attribs
        rad = _pie_points((l, t, r, b), angles) if mode in (1, 2) else None

        def body(ps, bs):
            brush = None
            if bs == 0 and (look.fill_style & 0xF) and gmode:
                if mode == 0:
                    rgn = g.CreateEllipticRgn(*box)
                else:
                    rgn = None
                    if g.BeginPath(dc):
                        (g.Pie if mode == 1 else g.Arc)(dc, *box, *rad)
                        if g.EndPath(dc):
                            rgn = g.PathToRegion(dc)
                self._clip_gradient(rgn, look.pairs[1][0], gcol, gmode)
                brush = g.GetStockObject(5)

            def fn():
                if mode == 0:
                    g.Ellipse(dc, *box)
                elif mode == 1:
                    g.Pie(dc, *box, *rad)
                elif mode == 2:
                    if look.fill_style & 0xF:
                        g.BeginPath(dc)
                        g.Arc(dc, *box, *rad)
                        g.EndPath(dc)
                        g.FillPath(dc)
                    g.Arc(dc, *box, *rad)

            self._with(self._pen(look, ps), brush if brush is not None else self._brush(look, bs), fn)

        self.effects(look, depth, body)

    # -- Pipe (+ its width table)
    def pipe(self, look: _Look, pts) -> None:
        if len(pts) < 2:
            return
        ct, g, dc = self.api.ct, self.g, self.dc
        hi, lo = look.pairs[3][1], look.pairs[4][1]
        n = len(pts)
        g.SetBkMode(dc, 1)
        for pw, off, pct in _PIPE_TABLE[min(max(look.width, 1), 32)]:
            col = 0
            for sh in (16, 8, 0):
                a, z = (lo >> sh) & 0xFF, (hi >> sh) & 0xFF
                col |= ((a + _cdiv((z - a) * pct, 100)) & 0xFF) << sh
            pen = g.CreatePen(0, pw, _cref(col))
            op = g.SelectObject(dc, pen)
            arr = (ct.c_long * (2 * n))(*[v for x, y in pts for v in (x - off, y - off)])
            g.Polyline(dc, arr, n)
            g.SelectObject(dc, op)
            g.DeleteObject(pen)


#: CtDraw's shading table: per pipe width, the
#: strokes (pen width, offset, percent hilight over lolight), darkest first
_PIPE_TABLE = {
    1: [(1, 0, 100)],
    2: [(2, 0, 0), (1, 1, 100)],
    3: [(3, 0, 0), (2, 0, 70), (1, 0, 100)],
    4: [(4, 0, 0), (3, 1, 30), (2, 1, 50), (1, 1, 100)],
    5: [(5, 0, 0), (4, 0, 20), (3, 1, 50), (2, 0, 80), (1, 1, 100)],
    6: [(6, 0, 0), (5, 1, 20), (4, 1, 45), (3, 1, 70), (2, 1, 90), (1, 2, 100)],
    7: [(7, 0, 0), (6, 0, 20), (5, 2, 45), (4, 2, 60), (3, 2, 75), (2, 2, 90), (1, 2, 100)],
    8: [(8, 0, 0), (7, 0, 20), (6, 1, 35), (5, 2, 50), (4, 1, 65), (3, 2, 80), (2, 2, 90), (1, 2, 100)],
    9: [(9, 0, 0), (8, 1, 20), (7, 1, 35), (7, 2, 50), (6, 3, 65), (4, 3, 80), (2, 3, 90), (1, 3, 100)],
    10: [(10, 0, 0), (9, 0, 20), (8, 1, 35), (7, 1, 50), (6, 2, 65), (5, 2, 80), (3, 2, 90), (1, 2, 100)],
    11: [(11, 0, 0), (10, 1, 20), (9, 1, 35), (8, 2, 50), (7, 2, 65), (5, 3, 80), (3, 3, 90), (1, 3, 100)],
    12: [(12, 0, 0), (11, 0, 20), (10, 1, 35), (9, 1, 50), (8, 2, 65), (5, 3, 80), (3, 3, 90), (1, 3, 100)],
    13: [(13, 0, 0), (12, 1, 20), (11, 1, 35), (9, 2, 50), (8, 3, 65), (6, 3, 80), (4, 3, 90), (1, 4, 100)],
    14: [(14, 0, 0), (13, 0, 20), (11, 1, 35), (10, 2, 50), (9, 2, 65), (6, 3, 80), (4, 3, 90), (1, 3, 100)],
    15: [(15, 0, 0), (14, 1, 20), (12, 2, 35), (11, 2, 50), (10, 3, 65), (7, 3, 80), (4, 3, 90), (1, 4, 100)],
    16: [(16, 0, 0), (15, 0, 20), (13, 1, 35), (12, 2, 50), (10, 3, 65), (7, 3, 80), (4, 3, 90), (1, 4, 100)],
    17: [(17, 0, 0), (15, 1, 20), (14, 2, 35), (12, 3, 45), (11, 3, 65), (8, 4, 80), (5, 4, 90), (2, 4, 100)],
    18: [(18, 0, 0), (16, 1, 20), (15, 1, 35), (13, 2, 45), (11, 3, 65), (8, 4, 80), (5, 4, 90), (2, 4, 100)],
    19: [(19, 0, 0), (17, 1, 20), (16, 2, 35), (14, 3, 45), (12, 4, 65), (9, 4, 80), (5, 5, 90), (2, 5, 100)],
    20: [(20, 0, 0), (18, 1, 20), (16, 2, 35), (15, 2, 45), (13, 3, 65), (9, 3, 80), (5, 4, 90), (2, 4, 100)],
    21: [
        (21, 0, 0),
        (19, 1, 20),
        (17, 2, 35),
        (15, 3, 45),
        (13, 4, 60),
        (10, 5, 75),
        (6, 5, 90),
        (2, 5, 100),
    ],
    22: [
        (22, 0, 0),
        (20, 1, 20),
        (18, 2, 35),
        (16, 3, 45),
        (14, 4, 60),
        (10, 4, 75),
        (6, 4, 90),
        (2, 4, 100),
    ],
    23: [
        (23, 0, 0),
        (21, 1, 20),
        (19, 2, 35),
        (17, 3, 45),
        (15, 4, 60),
        (10, 5, 75),
        (6, 5, 90),
        (2, 5, 100),
    ],
    24: [
        (24, 0, 0),
        (22, 1, 20),
        (20, 2, 35),
        (17, 3, 45),
        (15, 4, 60),
        (11, 4, 75),
        (7, 4, 90),
        (2, 5, 100),
    ],
    25: [
        (25, 0, 0),
        (23, 1, 20),
        (20, 3, 35),
        (18, 4, 45),
        (16, 5, 60),
        (11, 5, 75),
        (7, 5, 90),
        (2, 6, 100),
    ],
    26: [
        (26, 0, 0),
        (24, 1, 20),
        (21, 2, 35),
        (19, 3, 45),
        (17, 4, 60),
        (12, 5, 75),
        (7, 4, 90),
        (2, 5, 100),
    ],
    27: [
        (27, 0, 0),
        (25, 1, 20),
        (22, 3, 35),
        (20, 4, 45),
        (17, 5, 60),
        (12, 6, 75),
        (7, 5, 90),
        (2, 6, 100),
    ],
    28: [
        (28, 0, 0),
        (25, 1, 20),
        (23, 2, 35),
        (20, 4, 45),
        (18, 5, 60),
        (13, 6, 75),
        (8, 5, 90),
        (3, 6, 100),
    ],
    29: [
        (29, 0, 0),
        (26, 2, 20),
        (24, 3, 35),
        (21, 4, 45),
        (18, 6, 60),
        (13, 5, 75),
        (8, 6, 90),
        (3, 6, 100),
    ],
    30: [
        (30, 0, 0),
        (27, 1, 20),
        (25, 2, 35),
        (22, 4, 45),
        (19, 5, 60),
        (14, 5, 75),
        (8, 5, 90),
        (3, 6, 100),
    ],
    31: [
        (31, 0, 0),
        (28, 2, 20),
        (25, 3, 35),
        (23, 4, 45),
        (20, 6, 60),
        (14, 6, 75),
        (8, 6, 90),
        (3, 7, 100),
    ],
    32: [
        (32, 0, 0),
        (29, 1, 20),
        (26, 3, 35),
        (23, 4, 45),
        (20, 6, 60),
        (15, 5, 75),
        (9, 5, 90),
        (3, 6, 100),
    ],
}


MARKER = struct.pack("<HI", 0, 0xFFFE)


# --------------------------------------------------------------------------- page block
def _page_block(
    head: bytes, head_size: int, link_raw: bytes, payloads: List[bytes], link_count: Optional[int] = None
) -> bytes:
    # the object region: per record u32 payload size, u32 next_link (the
    # cumulative offset of the next record, 0 on the last), payload
    # (writer.serialize_objects, straight from the payload bytes)
    out = bytearray()
    running = 0
    for k, pl in enumerate(payloads):
        running += 8 + len(pl)
        out += struct.pack("<II", len(pl), running if k + 1 < len(payloads) else 0)
        out += pl
    objdata = bytes(out)
    h = bytearray(head[:head_size])
    struct.pack_into("<I", h, 0x00, len(objdata))
    struct.pack_into("<I", h, 0x04, len(payloads))
    if head_size >= 0x34:
        struct.pack_into("<I", h, 0x08, 0)
        if link_count is not None:
            struct.pack_into("<I", h, 0x1C, link_count)
    return bytes(h) + link_raw + objdata + b"\x00" * PAGE_TRAILING_PAD


def _ctf_links(raw: bytes, written_links, baked_links) -> Tuple[bytes, Optional[int]]:
    """The .ctF import table: the .ctg's, less the library references that
    only BAKED records used (their records are no longer in the file); label
    entries, @SUPER binds and references no record uses stay. Returns (raw,
    link_count); link_count None = the .ctg table unchanged."""
    from . import links as LK

    parsed = LK.parse_link_table(raw)
    if parsed is None:
        return raw, None
    area, entries = parsed
    drop = {w.lower() for w in baked_links} - {w.lower() for w in written_links}
    kept = [(gd, nm) for gd, nm in entries if LK.strip_stamp(nm).lower() not in drop]
    if len(kept) == len(entries):
        return raw, None
    return LK.serialize_link_table(area, kept)


def _depth(look: _Look, rect1) -> int:
    """The 3D effect depth."""
    if look.depth != -1:
        return look.depth
    eff = look.effects & 0xF
    l, t, r, b = rect1
    ext = (b - t) + (r - l)
    if eff == 0:
        d = 0
    elif eff in (1, 2, 4):
        d = _cdiv(ext, 500) + 1
    elif eff == 3:
        d = _cdiv(ext, 200) + 1
    else:
        d = 1
    if not (look.fill_style & 0xF) and look.width < d:
        d = look.width
    return d


def _line_points(r0, c):
    """A line's draw-order endpoints (the class part's last 8 bytes, local
    coordinates) through the Total transform."""
    x1, y1, x2, y2 = struct.unpack_from("<4h", c.cls, len(c.cls) - 8)
    T = _total(r0)
    return _xform_pt(T, x1, y1), _xform_pt(T, x2, y2)


def _pie_points(rect, angles):
    """The two radial points of a pie / arc (untransformed angles) -- angle 0 at 12 o'clock, clockwise: (cx + sin(a) rx, cy - cos(a) ry)
    with rx, ry the half extents; |a| < 0.05 rad takes the top point."""
    import math

    l, t, r, b = rect
    rx, ry = _cdiv(r - l, 2), _cdiv(b - t, 2)
    out = []
    for deg in (angles[1], angles[0]):
        a = deg * 3.14159265359 / 180.0
        if abs(a) >= 0.05:
            x = int(math.sin(a) * rx + (l + rx))
            y = int((ry + t) - math.cos(a) * ry)
        else:
            x, y = rx + l, t
        out += [x, y]
    return out


def _total(r0):
    """The record's in-memory Total transform: the stored slot 3 (what the
    engine derives from the Locals) as (mode, (a, b, c, d, h, k))."""
    if r0.slots and len(r0.slots) > 2:
        return r0.slots[2]
    return (0, None)


def _xform_pt(T, x: int, y: int) -> Tuple[int, int]:
    """map a point through a transform (by the full
    mode byte, C truncation)."""
    mode, m = T
    if m is None or mode == 0:
        return x, y
    a, b, c, d, h, k = m
    if mode == 1:
        return x + int(h), y + int(k)
    if mode == 2:
        return int(x * a), int(y * d)
    if mode == 3:
        return int(x * a + h), int(y * d + k)
    return int(y * c + x * a + h), int(y * d + x * b + k)


def _vertices(c, T):
    """PolyLine / Pipe appearance part [u16 n][n x (i16 x, i16 y)][...]:
    the vertices mapped to page coordinates."""
    part = appearance_part(c)
    if len(part) < 2:
        return None
    n = struct.unpack_from("<H", part, 0)[0]
    if n == 0 or len(part) < 2 + 4 * n:
        return None
    return [_xform_pt(T, *struct.unpack_from("<2h", part, 2 + 4 * k)) for k in range(n)]


def _bitmap_dib(c):
    """(w, h, top-down pixels) of a Bitmap-class record's in-memory DIB (its
    appearance part: native rect, u32 flags (bit 0x1 zlib), u32 raw size, u32
    packed size, the DIB); None when it carries none."""
    cls = c.cls
    o = _metadata_len(cls) if c.dw & 0x100 else 0
    if len(cls) < o + 20:
        return None
    flags, unc, comp = struct.unpack_from("<III", cls, o + 8)
    try:
        if flags & 0x1:
            raw = inflate(cls[o + 20 : o + 20 + comp])
        else:
            raw = cls[o + 16 : o + 16 + unc]
        size, w, h, planes, bpp = struct.unpack_from("<IiiHH", raw, 0)
    except zlib.error, struct.error:
        return None
    if size != 40 or bpp != 32 or w <= 0 or h == 0:
        return None
    ah = abs(h)
    px = raw[40 : 40 + 4 * w * ah]
    if len(px) != 4 * w * ah:
        return None
    if h > 0:  # bottom-up -> top-down rows
        stride = 4 * w
        px = b"".join(px[y * stride : (y + 1) * stride] for y in range(ah - 1, -1, -1))
    return w, ah, px


def _orient_pixels(
    w: int, h: int, px: bytes, a: float, b: float, c: float, d: float
) -> Optional[Tuple[int, int, bytes]]:
    """Turn a DIB the way the record's transform turns it."""
    from array import array

    src = memoryview(px).cast("I")
    if b == 0 and c == 0 and a and d:
        rows = []
        for v in range(h):
            y = v if d > 0 else h - 1 - v
            row = src[y * w : (y + 1) * w]
            rows.append(bytes(row) if a > 0 else array("I", reversed(row)).tobytes())
        return w, h, b"".join(rows)
    if a == 0 and d == 0 and b and c:
        W, H = h, w
        out = array("I", bytes(4 * W * H))
        for v in range(H):
            x = v if b > 0 else w - 1 - v
            for u in range(W):
                y = u if c > 0 else h - 1 - u
                out[v * W + u] = src[y * w + x]
        return W, H, out.tobytes()
    return None


def _draw_bitmap(pt: "_Painter", r0, c) -> bool:
    """The Bitmap draw: the DIB in the record's orientation (:func:`_orient_pixels`)
    stretched into its box; False for a DIB it cannot read or a turn that is no mirror /
    quarter turn."""
    dib = _bitmap_dib(c)
    if dib is None:
        return False
    w, h, px = dib
    slot = r0.slots[2] if r0.slots and r0.slots[2][0] else (r0.slots[0] if r0.slots else (0, None))
    if slot[1] is not None and slot[0]:
        a, b, cc, d = slot[1][:4]
        if b != 0 or cc != 0 or a <= 0 or d <= 0:
            got = _orient_pixels(w, h, px, a, b, cc, d)
            if got is None:
                return False
            w, h, px = got
    pt.bitmap(r0.rect1, w, h, px)
    return True


def _metafile_bits(c) -> Optional[bytes]:
    """The memory metafile of a MetaFile-class record's IN-MEMORY appearance (its .ctF
    record ``c``: an art-stripped member's comes from its owner, the element record): the
    class part is the native rect then the metafile (METAHEADER: type 1 / 2, header size
    9, the size in words at +6); None when it holds none."""
    cls = c.cls or b""
    o = (_metadata_len(cls) if c.dw & 0x100 else 0) + 8
    if o + 18 > len(cls) or struct.unpack_from("<HH", cls, o) not in ((1, 9), (2, 9)):
        return None
    words = struct.unpack_from("<I", cls, o + 6)[0]
    return bytes(cls[o : o + words * 2]) if o + words * 2 <= len(cls) else None


def _draw_metafile(api, dc, bits, rect) -> bool:
    """The MetaFile class draw: MM_ANISOTROPIC, the viewport at
    the record's extent less one pixel each way from the window origin, the memory
    metafile ``bits`` (:func:`_metafile_bits`) played into it (its own records set the
    window); False when there is none."""
    if not bits or rect is None:
        return False
    ct, g = api.ct, api.g
    l, t, r, b = rect
    org = (ct.c_long * 2)()
    g.GetWindowOrgEx(dc, ct.byref(org))
    g.SetMapMode(dc, 8)
    g.SetViewportOrgEx(dc, l - org[0], t - org[1], None)
    g.SetViewportExtEx(dc, (r - l) - 1, (b - t) - 1, None)
    buf = ct.create_string_buffer(bits, len(bits))
    hmf = g.SetMetaFileBitsEx(len(bits), buf)
    if not hmf:
        return False
    try:
        g.PlayMetaFile(dc, hmf)
    finally:
        g.DeleteMetaFile(hmf)
    return True


def render_background(
    objs, recs, drawn, width: int, height: int, colour: int, unsupported: Optional[list] = None
) -> Optional[bytes]:
    """The background DIB: the page colour + every baked object drawn in
    z-order with CtDraw's class draw code; the top-down 32bpp pixels, or None
    without GDI."""
    api = _GdiApi.get()
    if api is None:
        return None
    dib = _Dib(api, width, height, colour)
    try:
        pt = _Painter(api, dib, colour)
        for i in drawn:
            r0, c = recs[i]
            tc = objs[i].type_code
            look = _Look(c.style)
            g = api.g
            g.SaveDC(dib.dc)
            try:
                if tc in (0x03, 0x0E, 0x0F):
                    tail = c.cls[-18:] if len(c.cls) >= 18 else bytes(18)
                    pt.square(
                        look,
                        r0.rect1,
                        tail[8],
                        _depth(look, r0.rect1),
                        tail[9],
                        struct.unpack_from("<I", tail, 10)[0],
                    )
                elif tc == 0x01 and len(c.cls) >= 8:
                    p1, p2 = _line_points(r0, c)
                    pt.line(look, p1, p2, _depth(look, r0.rect1))
                elif tc == 0x07 and _draw_bitmap(pt, r0, c):
                    pass
                elif tc == 0x09 and _draw_metafile(api, dib.dc, _metafile_bits(c), r0.rect1):
                    pass
                elif tc in (0x0A, 0x10) and look.attribs in (0, 1) and _vertices(c, _total(r0)):
                    tail = c.cls[-9:]
                    pt.polygon(
                        look,
                        _vertices(c, _total(r0)),
                        _depth(look, r0.rect1),
                        tail[0],
                        struct.unpack_from("<I", tail, 1)[0],
                    )
                elif (
                    tc in (0x04, 0x11)
                    and len(c.cls) >= 21
                    and (look.attribs == 0 or (look.attribs in (1, 2) and _total(r0)[0] in (0, 1)))
                ):
                    tail = c.cls[-21:]
                    pt.ellipse(
                        look,
                        r0.rect1,
                        _depth(look, r0.rect1),
                        tail[12],
                        struct.unpack_from("<I", tail, 13)[0],
                        struct.unpack_from("<2h", tail, 8),
                    )
                elif tc == 0x0B and _vertices(c, _total(r0)):
                    pt.pipe(look, _vertices(c, _total(r0)))
                elif unsupported is not None:
                    unsupported.append(i)
            finally:
                g.RestoreDC(dib.dc, -1)
                pt.org = (0, 0)
        return dib.pixels()
    finally:
        dib.close()


# --------------------------------------------------------------------------- the file
def _bg_parts():
    from . import records as R

    # prefix (tc 0, dwFlags 0xA, an '-1') + reserved, handle 0xFFFF, no name, identity
    # slots; then the free group's tail (empty level, black state 0, plain style)
    head = R.prefix(0, R.STYLE | R.BAGS) + struct.pack("<HH", 0, 0xFFFF) + R.lpstr("") + R.slots()
    return head, R.GROUP_TAIL


_BG_HEAD, _BG_MID = _bg_parts()
#: the loaded zlib.dll by path (False = none there; '' = the install has none)
_ZLIB_LOADED: dict = {}


#: this thread's build_ctf: the zlib it found at its start (its deflates look it up once)
_ZLIB_RUN = threading.local()


def _zlib_lib():
    """The install's classic zlib.dll (infra/install.zlib_dll, read at each call outside a
    build_ctf, once per build inside one: a CTG_SCADA_ROOT set later counts), loaded once
    per path; False without one."""
    run = getattr(_ZLIB_RUN, "lib", None)
    if run is not None:
        return run
    from ..infra import install

    path = install.zlib_dll() or ""
    lib = _ZLIB_LOADED.get(path)
    if lib is None:
        import ctypes

        try:
            lib = ctypes.CDLL(path) if path else False
        except OSError:  # (not a DLL this process can load: Python's zlib then)
            _note_exc("ctf._zlib_lib")
            lib = False
        _ZLIB_LOADED[path] = lib
    return lib


def compress(raw: bytes) -> bytes:
    """CtDraw's deflate: classic zlib 1.2.13 compress2 level 5 (the AVEVA
    zlib.dll when present -- Python's zlib-ng output differs byte-wise)."""
    try:
        import ctypes

        lib = _zlib_lib()
        if lib:
            n = ctypes.c_ulong(len(raw) + len(raw) // 1000 + 64)
            out = ctypes.create_string_buffer(n.value)
            if lib.compress2(out, ctypes.byref(n), raw, ctypes.c_ulong(len(raw)), 5) == 0:
                return out.raw[: n.value]
    except Exception:  # noqa: BLE001
        _note_exc("ctf.compress")
        pass
    return zlib.compress(raw, 5)


_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def session_ranges(data: bytes, c) -> list:
    """Byte ranges of a .ctF that hold session state (editing_base.ctf_matches): the Dynamic CTF
    Bitmap record's colour-state pair and style block (ctf._BG_MID), and the created /
    modified times (+100 .. +116) of every directory entry of each embedded OLE compound
    file (an ActiveX control's storage)."""
    import struct

    out = []
    blk = c.block("page")
    if blk is None or blk.size < 0x34:
        return out
    links = struct.unpack_from("<I", data, blk.offset + 0x1C)[0]
    rec0 = blk.offset + 0x34 + links * 0x60 + 8
    a = rec0 + len(_BG_HEAD) + 20
    out.append((a, a + len(_BG_MID)))
    m = data.find(_CFB_MAGIC, blk.offset)
    while m >= 0:
        try:
            ss = 1 << struct.unpack_from("<H", data, m + 0x1E)[0]
            if ss not in (512, 4096):
                raise ValueError(ss)
            fat = [x for x in struct.unpack_from("<109I", data, m + 0x4C) if x < 0xFFFFFFFA]
            sect = struct.unpack_from("<I", data, m + 0x30)[0]
            for _n in range(64):
                if sect >= 0xFFFFFFFA:
                    break
                d0 = m + ss + sect * ss
                for e in range(ss // 128):
                    t = d0 + e * 128 + 100
                    if t + 16 <= len(data):
                        out.append((t, t + 16))
                k, slot = divmod(sect, ss // 4)
                if k >= len(fat):
                    break
                sect = struct.unpack_from("<I", data, m + ss + fat[k] * ss + 4 * slot)[0]
        except struct.error, ValueError:  # swallow-ok: not a compound file there
            pass
        m = data.find(_CFB_MAGIC, m + 8)
    return out


def background_record(width: int, height: int, pixels: bytes) -> bytes:
    """The Dynamic CTF Bitmap record: tc 0, handle 0xFFFF,
    a Bitmap-class body = native rect + u32 1 (zlib) + u32 raw + u32 packed +
    zlib(BITMAPINFOHEADER(w, -h, 32bpp) + top-down pixels)."""
    geom = struct.pack("<2h", 0, height - 1) + struct.pack("<4h", 0, 0, width, height) * 2
    dib = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0) + pixels
    z = compress(dib)
    return (
        _BG_HEAD
        + geom
        + _BG_MID
        + struct.pack("<4h", 0, 0, width, height)
        + struct.pack("<III", 1, len(dib), len(z))
        + z
    )


def build_ctf(
    ctg_bytes: bytes, page_path: str, resolver=None, bake: bool = True, page=None, owners=None
) -> bytes:
    """Build the .ctF CtDraw's save would write for this .ctg."""
    before = getattr(_ZLIB_RUN, "lib", None)
    _ZLIB_RUN.lib = _zlib_lib()
    try:
        return _build_ctf(ctg_bytes, page_path, resolver, bake, page, owners)
    finally:
        _ZLIB_RUN.lib = before


def _build_ctf(ctg_bytes: bytes, page_path: str, resolver, bake: bool, page, owners) -> bytes:
    from .attributes import parse_attributes

    c = Container.parse(ctg_bytes)
    blk = c.block("page")
    pb = page if page is not None else parse_page(blk, c.word2)
    if resolver is None:
        from .symbols import SymbolResolver

        resolver = SymbolResolver.for_page(page_path)
    objs = pb.objects
    w, h = pb.page_width, pb.page_height
    at = c.block("attributes")
    colour = parse_attributes(at).bg_color0 if at is not None else 0xFFFFFF
    if owners is None or len(owners) != len(objs):
        owners = appearance_owners(objs, resolver)
    tcache: Dict[int, object] = {}
    zmemo: dict = {}
    recs = []
    for po, ow in zip(objs, owners):
        # (the .ctF writes no bags: only the value bag is read, by the bake's _Obj)
        r0 = GP.decode(po, value_bag_only=True)
        t = None
        if ow is not None and ow is not po:
            t = tcache.get(id(ow))
            if t is None:
                t = tcache[id(ow)] = GP.decode(ow, value_bag_only=True)
        recs.append((r0, ctf_rec(r0, t, zmemo)))
    if bake:
        layout, drawn = ctf_plan(objs, recs, (w, h))
    else:
        layout, drawn = ["write"] * len(objs), []
    unsupported: list = []
    pixels = render_background(objs, recs, drawn, w, h, colour, unsupported) if drawn else None
    if unsupported:
        # a record the plan would bake but our renderer cannot draw yet: baking
        # it would DROP it from the file undrawn (it would vanish in the
        # runtime) -- bake nothing instead (CtDraw's own shape for an
        # all-dynamic page: page-colour bitmap + every record)
        layout, pixels = ["write"] * len(objs), None
        drawn = []
    if pixels is None:
        if drawn:  # no GDI: bake nothing
            layout = ["write"] * len(objs)
        pixels = struct.pack("<I", colour & 0xFFFFFF) * (w * h)
    payloads = [background_record(w, h, pixels)]
    written, baked = set(), set()
    for (r0, cr), lay in zip(recs, layout):
        if lay == "write":
            payloads.append(GP.encode(cr))
        elif lay == "marker":
            payloads.append(MARKER)
        if r0.link:
            (written if lay == "write" else baked).add(r0.link)
    link_raw, link_count = pb.link_table_raw, None
    if baked:
        link_raw, link_count = _ctf_links(pb.link_table_raw, written, baked)
    blk.data = _page_block(blk.data, pb.head_size, link_raw, payloads, link_count)
    c.relayout()
    return c.to_bytes()


def ctf_path_for(ctg_path: str) -> Optional[str]:
    base = ctg_path[:-1]
    for cand in (base + "F", base + "f"):
        if os.path.exists(cand):
            return cand
    return None


def page_version(path: str) -> Optional[bytes]:
    """The 16-byte page VERSION of a page file (.ctg or .ctF): bytes 4..20 of
    the page block's identity (after the u32 save stamp),
    read from the block head only. None when the file cannot be read."""
    from .pagesummary import read_directory

    d = read_directory(path)
    if not d.ok:
        return None
    pg = next((e for e in d.entries if e.name == "page" and not e.is_deleted), None)
    if d.word2 < 0x0C:
        return None  # the legacy head carries the save stamp only (format/upgrade.py)
    at = 0x20
    if pg is None or pg.size < at + 20:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(pg.offset + at)
            ident = fh.read(20)
    except OSError:
        return None
    return ident[4:20] if len(ident) == 20 else None


def ctf_is_stale(ctg_path: str) -> Optional[bool]:
    """Is the page's .ctF out of date? CtDraw's own test."""
    f = ctf_path_for(ctg_path)
    if f is None:
        return None
    a, b = page_version(ctg_path), page_version(f)
    if a is None or b is None:
        return None
    return a != b


def stale_ctf_files(project_dir: str) -> List[str]:
    """The out-of-date .ctF files (:func:`ctf_is_stale`) of the pages in
    ``project_dir``, sorted by name."""
    out = []
    try:
        names = sorted(os.listdir(project_dir), key=str.lower)
    except OSError:
        return out
    for n in names:
        if not n.lower().endswith(".ctg"):
            continue
        p = os.path.join(project_dir, n)
        if ctf_is_stale(p):
            out.append(ctf_path_for(p))
    return out
