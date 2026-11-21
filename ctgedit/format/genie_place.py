"""Place a library genie or symbol on a page the way CtDraw's Paste Genie stores it."""

from __future__ import annotations

import copy as _copy
import operator
import os
import re
import struct
import sys
import threading
from collections import OrderedDict
import dataclasses
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ctgedit.format import geometry as G
from ctgedit.format import placement as P
from ctgedit.format import colourstate as CS
from ctgedit.format import textmetrics as TM
from ctgedit.format.page import PageObject, _parse_object, _geom_name_len, parse_ctg
from ctgedit.format.symbols import (
    LIBRARY_EXTENSIONS,
    SymbolResolver,
    library_version,
    open_library,
    reference_from_object,
)
from ctgedit.format.textcodec import CODEC, MULTIBYTE
from ctgedit.infra.logsetup import note_exception as _note_exc  # swallowed-error log
from . import codes as _codes
from . import forest as _forest
from . import vertexanim as _VA

GROUP_TC, SYMSET_TC, TEXT_TC, BITMAP_TC, ELLIPSE_TC = 0x08, 0x0C, 0x05, 0x07, 0x11
METAFILE_TC = 0x09
BUTTON_TC = 0x1001
INPUT_TCS = _codes.INPUT_TCS
#: legacy.POINT_TCS: classes positioned by their anchor point, never scaled
POINT_CLASSES = frozenset(
    {0x1000, 0x1004, 0x1005, 0x1006, 0x1008, 0x1009, _codes.TC.KEYBOARD, 0x100D, _codes.TC.TOUCH}
)  # (0x1004 Number: legacy.POINT_TCS, LIVE)
#: classes whose e0.b1 in-memory internal '!' pair is 11 bytes (else 10)
_OV11_TCS = frozenset({0x1007, 0x1009, _codes.TC.KEYBOARD, 0x100D, _codes.TC.TOUCH})
STATE_UNSET = "#\n"
#: records that do not contribute to a container's extent (DummyMarker, Touch, Keyboard)
UNION_SKIP = frozenset({0xFFFF, _codes.TC.TOUCH, _codes.TC.KEYBOARD})


# =========================================================================== strict record model
def _ascii_codec() -> bool:
    """The ANSI code page decodes 0x00..0x7F as ASCII and encodes ASCII back alike (every
    Windows ANSI code page does: single-byte and double-byte, whose lead bytes are >= 0x81)."""
    try:
        return all(
            bytes([c]).decode(CODEC) == chr(c) and chr(c).encode(CODEC) == bytes([c]) for c in range(0x80)
        )
    except Exception:  # noqa: BLE001  # swallow-ok: the registered codec for every string then
        return False


#: an all-ASCII string goes through the C codecs (the text the code page gives it), not the
#: registered codec's Python-level call: a decode / encode converts every bag key and value
#: (page._dec does the same for the page parse)
_ASCII = _ascii_codec()


def _sdec(b) -> str:
    return b.decode("latin-1") if _ASCII and b.isascii() else b.decode(CODEC)


def _senc(s: str) -> bytes:
    return s.encode("ascii") if _ASCII and s.isascii() else s.encode(CODEC)


@dataclass
class Bag:
    pairs: List[Tuple[str, str]]
    ov: int = 0  # size word overshoot = CtDraw's unwritten internal '!' pairs

    def size(self) -> int:
        return 2 + sum(len(_senc(k)) + len(_senc(v)) + 4 for k, v in self.pairs) + self.ov

    def get(self, key: str) -> Optional[str]:
        low = key.lower()  # the property index ignores ASCII case
        for k, v in self.pairs:
            if k.lower() == low:
                return v
        return None

    def set(self, key: str, value: str) -> None:
        """non-empty -> update in place / append; empty -> remove."""
        low = key.lower()
        for i, (k, _v) in enumerate(self.pairs):
            if k.lower() == low:
                if value:
                    self.pairs[i] = (k, value)
                else:
                    del self.pairs[i]
                return
        if value:
            self.pairs.append((key, value))

    def copy(self) -> "Bag":
        return Bag(list(self.pairs), self.ov)


@dataclass
class Rec:
    cf: int
    tc: int
    dw: int
    an: str
    res: bytes
    handle: int
    name: str
    slots: list  # [(mode, (a,b,c,d,h,k)) x3]
    raw_slots: bytes  # the original encoded slot bytes
    anchor: Tuple[int, int]
    rect1: Tuple[int, int, int, int]
    rect2: Tuple[int, int, int, int]
    link: Optional[str]
    entries: Optional[List[List[Bag]]]
    s40: Optional[bytes]
    s80: Optional[bytes]
    cs: bytes
    style: Optional[bytes]
    cls: bytes


def _lp(b: bytes, o: int):
    n = struct.unpack_from("<H", b, o)[0]
    return b[o + 2 : o + 2 + n], o + 2 + n


_U16 = struct.Struct("<H").unpack_from
_U16X2 = struct.Struct("<HH").unpack_from


def _enc_lp(s) -> bytes:
    b = s if isinstance(s, bytes) else _senc(s)
    return struct.pack("<H", len(b)) + b


_U16 = struct.Struct("<H").unpack_from
#: the bags a value_bag_only decode skips (shared: its pairs are a tuple, so it is never
#: changed)
_EMPTY_BAG = Bag(())


def decode(po: PageObject, value_bag_only: bool = False) -> Rec:
    """The strict record model. ``value_bag_only``: of the bags only entry 0's value bag
    (e0.b1) is decoded, every other bag is an empty Bag (walked as it is: it fails alike),
    for a reader of that bag alone."""
    b = po.body_raw
    gh = G.parse_geom_header(b)
    ne = _geom_name_len(b, 0)
    (s1, s2, s3), send = P.read_slots_of(po)
    o = gh.end
    dw = po.subtype_size
    link = None
    if dw & 1:
        s, o = _lp(b, o)
        link = _sdec(s)
    entries = None
    # (a skipped bag's strings are not decoded: the codec never fails on a single-byte
    # code page, so only the walk can, as it does here; a double-byte one decodes them)
    skip = value_bag_only and not MULTIBYTE
    if dw & 8:
        n = struct.unpack_from("<H", b, o)[0]
        o += 2
        entries = []
        u16 = _U16
        ascii_ok = _ASCII
        for _e in range(n + 1):
            bags = []
            for _b in range(3):
                size, cnt = _U16X2(b, o)
                o += 4
                if skip and (_e or _b != 1):
                    for _p in range(cnt):
                        o += 2 + _U16(b, o)[0]
                        o += 2 + _U16(b, o)[0]
                    bags.append(_EMPTY_BAG)
                    continue
                pairs = []
                # Bag.size() of the decoded pairs, from the raw lengths: the codec
                # is a lossless bijection (textcodec), so every string encodes back
                # to exactly its stored bytes
                tot = 2
                for _p in range(cnt):
                    # two lpstrs (_lp and _sdec, inline)
                    (kn,) = u16(b, o)
                    k = b[o + 2 : o + 2 + kn]
                    o += 2 + kn
                    (vn,) = u16(b, o)
                    v = b[o + 2 : o + 2 + vn]
                    o += 2 + vn
                    tot += len(k) + len(v) + 4
                    pairs.append(
                        (
                            k.decode("latin-1") if ascii_ok and k.isascii() else k.decode(CODEC),
                            v.decode("latin-1") if ascii_ok and v.isascii() else v.decode(CODEC),
                        )
                    )
                bag = Bag(pairs)
                bag.ov = size - tot
                bags.append(bag)
            entries.append(bags)
    s40 = s80 = None
    if dw & 0x40:
        st = o
        o = CS._lp_end(b, o) + 2 + 24
        s40 = b[st:o]
    if dw & 0x80:
        st = o
        o = CS._lp_end(b, o)
        s80 = b[st:o]
    L = CS.layout(po)
    cs = b[L["at"] : L["end"]]
    o = L["end"]
    style = None
    if dw & 2:
        style = b[o : o + 54]
        o += 54
    return Rec(
        po.class_flag,
        po.type_code,
        dw,
        po.an_str,
        b[0:2],
        struct.unpack_from("<H", b, 2)[0],
        _sdec(b[6:ne]),
        [(s[0], s[1]) for s in (s1, s2, s3)],
        b[s1[2] : send],
        gh.anchor,
        gh.rect1,
        gh.rect2,
        link,
        entries,
        s40,
        s80,
        cs,
        style,
        b[o:],
    )


def encode(r: Rec) -> bytes:
    """Payload bytes (prefix + body) in the saver's order."""
    an = _senc(r.an)
    out = bytearray(struct.pack("<HII", r.cf, r.tc, r.dw) + _enc_lp(an))
    out += r.res + struct.pack("<H", r.handle & 0xFFFF) + _enc_lp(r.name)
    out += r.raw_slots if r.slots is None else b"".join(P.encode_slot(m, x) for m, x in r.slots)
    out += struct.pack("<2h", *r.anchor) + struct.pack("<4h", *r.rect1) + struct.pack("<4h", *r.rect2)
    if r.dw & 1:
        out += _enc_lp(r.link)
    if r.dw & 8:
        out += struct.pack("<H", len(r.entries) - 1)
        for e in r.entries:
            for bag in e:
                # = Bag.size() + the pairs, each string encoded ONCE (in the
                # order size() encodes them, so an unencodable one fails alike)
                enc = [(_senc(k), _senc(v)) for k, v in bag.pairs]
                size = 2 + sum(len(kb) + len(vb) + 4 for kb, vb in enc) + bag.ov
                out += struct.pack("<HH", size & 0xFFFF, len(bag.pairs))
                for kb, vb in enc:
                    out += struct.pack("<H", len(kb)) + kb + struct.pack("<H", len(vb)) + vb
    if r.dw & 0x40:
        out += r.s40
    if r.dw & 0x80:
        out += r.s80
    out += r.cs
    if r.dw & 2:
        out += r.style
    out += r.cls
    return bytes(out)


def to_po(payload: bytes) -> PageObject:
    return _parse_object(0, 0, len(payload), 0, payload)


def _shell_po(payload: bytes) -> PageObject:
    """:func:`to_po` without the heuristic bag scan and the text extras (page._parse_object's
    prefix and body, the same fields): for a record that is only DECODED again -- decode reads
    nothing but the prefix fields and the body. Never raises (like _parse_object)."""
    n = len(payload)
    class_flag = struct.unpack_from("<H", payload, 0x00)[0] if n >= 2 else 0
    type_code = struct.unpack_from("<I", payload, 0x02)[0] if n >= 6 else 0
    subtype_size = struct.unpack_from("<I", payload, 0x06)[0] if n >= 10 else 0
    an_len = struct.unpack_from("<H", payload, 0x0A)[0] if n >= 12 else 0
    an_end = 0x0C + an_len
    an_str = payload[0x0C:an_end].decode(CODEC, "replace") if an_end <= n else "-1"
    raw_payload = None
    if an_end > n or n < 0x0C:
        an_end = min(0x0C, n)
        raw_payload = bytes(payload)
    return PageObject(
        index=0,
        record_offset=0,
        payload_size=n,
        next_link=0,
        class_flag=class_flag,
        type_code=type_code,
        subtype_size=subtype_size,
        an_str=an_str,
        body_offset=an_end,
        body_raw=bytes(payload[an_end:]),
        raw_payload=raw_payload,
    )


# =========================================================================== substitution
def _token(s: str, i: int):
    """the %token% starting at s[i] == '%' -> (token, next index).
    Inside a token '^%' is a literal '%'. Unterminated -> ('', len)."""
    j = i + 1
    out = []
    while j < len(s):
        c = s[j]
        if c == "^" and j + 1 < len(s) and s[j + 1] == "%":
            out.append("%")
            j += 2
            continue
        if c == "%":
            return "".join(out), j + 1
        out.append(c)
        j += 1
    return None, len(s)


def substitute(template: str, values: Bag, decls: Bag, key: str = "", specials: Optional[dict] = None) -> str:
    """for one template value: every %name% / %name=default%
    becomes the level value (values bag, without case), else the declaration
    default (decls bag) unless it is '\\n' (none) -> ''. Result capped at 254
    chars (0xfe). A key ending in '.NonGenie' (len >= 10) is copied verbatim."""
    if len(key) >= 10 and key.endswith(".NonGenie"):
        return template
    out = []
    n = 0
    i = 0
    while i < len(template):
        c = template[i]
        if c != "%":
            out.append(c)
            n += 1
            i += 1
            if n >= 0xFE:
                break
            continue
        tok, j = _token(template, i)
        if not tok:
            # an empty / unterminated token leaves the source
            # pointer at the '%': the REST of the template is copied verbatim
            rest = template[i:]
            if n + len(rest) > 0xFE:
                break
            out.append(rest)
            n += len(rest)
            if tok is None:
                break
            i = j
            continue
        name = tok.split("=", 1)[0]
        v = values.get(name) or ""
        if not v:
            d = decls.get(name)
            v = "" if (d is None or d == "\n") else d
        if not v and specials and name.lower() in specials:
            v = specials[name.lower()]
        if not v and key.upper() == "TXT_STR" and template[j : j + 3] == "):#" and "".join(out) == "(":
            # (only the exact shape '(%p%):#...' empties the value)
            return ""
        if n + len(v) > 0xFE:
            break
        out.append(v)
        n += len(v)
        i = j
    return "".join(out)


def top_level_templates(level) -> "Bag":
    """Effective values of the binding level under an element's own level."""
    vals = Bag(list(level[2].pairs), level[1].ov)
    for kk, vv in level[1].pairs:
        if vals.get(kk) is None:
            vals.set(kk, vv)
    return vals


def drop_instance_level(
    recs: Sequence[PageObject], members: Sequence[int], between_roots
) -> List[PageObject]:
    """Drop the genie level a dissolved container gave its members."""
    out = list(recs)
    for j in members:
        po = out[j]
        if not (po.subtype_size & 0x8):
            continue
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record keeps its levels
            continue
        e = r.entries
        if not e:
            continue
        is_root = bool(po.class_flag and (po.subtype_size & 0x200))
        L = 1 + between_roots(j) + (1 if is_root else 0)  # a root's own level comes first
        if len(e) <= L:
            continue
        ents = [[b.copy() for b in lvl] for lvl in e]
        del ents[L]
        if L == 1 and len(ents) == 1:
            ents[0][2] = Bag([], 0)  # a plain object: values only
        if is_root and len(ents) >= 2 and ents[L - 1][2].pairs:
            # the nested root's own placed level: its typed templates become its values
            lvl = ents[L - 1]
            vals = Bag([], lvl[1].ov)
            for key, t in lvl[2].pairs:
                v = substitute(t, Bag([]), Bag([]), key)
                if v:
                    vals.pairs.append((key, v))
            lvl[1], lvl[2] = vals, Bag([], 0)
        r.entries = ents
        out[j] = to_po(encode(r))
    return out


def _span_end(recs, k: int) -> int:
    end, pending = k, 1
    while pending and end < len(recs):
        pending -= 1
        pending += recs[end].class_flag
        end += 1
    return end


def declares_parameters(recs: Sequence[PageObject], parents) -> bool:
    """The element is a GENIE: a direct bagged record (not a nested genie root, not inside
    one) carries a genie level, or names a %parameter% in its values (the first one just
    added)."""
    for k in range(1, len(recs)):
        po = recs[k]
        if not (po.subtype_size & 0x8) or (po.class_flag and (po.subtype_size & 0x200)):
            continue
        a, nested = parents[k], False
        while a is not None and a != 0:
            if recs[a].class_flag and (recs[a].subtype_size & 0x200):
                nested = True
                break
            a = parents[a]
        if nested:
            continue
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record decides nothing
            continue
        if r.entries and (len(r.entries) >= 2 or template_refs(r.entries[0][1])):
            return True
    # a token typed into a nested instance's binding (a page-form root: typed values in
    # its last level's b1, no templates) makes the element a genie too (CtDraw's dialog
    # listed the element's parameters that way)
    for k in range(1, len(recs)):
        po = recs[k]
        if not (po.class_flag and (po.subtype_size & 0x200) and (po.subtype_size & 0x8)):
            continue
        a, inner = parents[k], False  # an OUTERMOST instance only: an inner genie's
        while a is not None and a != 0:  # own templates always name the outer's parameters
            if recs[a].class_flag and (recs[a].subtype_size & 0x200):
                inner = True
                break
            a = parents[a]
        if inner:
            continue
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001  # swallow-ok: decides nothing
            continue
        e = r.entries
        if e and len(e) >= 2 and not e[-1][2].pairs and template_refs(e[-1][1]):
            return True
        # ... and in the element form: its placed level's templates (under the element
        # level) name an outer token
        if e and len(e) >= 3 and template_refs(e[-2][2]):
            return True
    return False


def _unbound_page_form(dec, k: int, end: int) -> bool:
    """Is this nested copy a page-form copy with nothing bound?"""
    r0 = dec(k)
    if r0 is None or r0.entries is None:
        return False
    if len(r0.entries) == 1:
        return True
    if any(lv[1].pairs or lv[2].pairs for lv in r0.entries[1:]):
        return False
    for j in range(k + 1, end):
        r = dec(j)
        if r is not None and r.entries and len(r.entries) >= 2:
            top = r.entries[-1]
            if top[0].pairs and not top[2].pairs:
                return True
    return False


def convert_page_form(recs: Sequence[PageObject], genie: Optional[bool] = None) -> List[PageObject]:
    """Convert every page-form genie instance in an element to the element form."""
    recs = list(recs)
    parents = P.forest_parents(recs)
    n = len(recs)
    if genie is None:
        genie = declares_parameters(recs, parents)
    if not genie:
        return recs

    def is_root(i):
        po = recs[i]
        return bool(po.class_flag and (po.subtype_size & 0x200))

    decoded: Dict[int, Optional[Rec]] = {}

    def dec(i):
        if i not in decoded:
            try:
                decoded[i] = decode(recs[i])
            except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record is kept
                decoded[i] = None
        return decoded[i]

    def roots_above(i):
        """The nested roots on ``i``'s ancestor chain, outermost first (``i`` excluded)."""
        out = []
        a = parents[i]
        while a is not None and a != 0:
            if is_root(a):
                out.append(a)
            a = parents[a]
        out.reverse()
        return out

    k = 1
    while k < n:
        if not is_root(k) or roots_above(k):
            k += 1
            continue
        end = _span_end(recs, k)
        # the page form: an instance root's level holds typed bindings in b1 and NO
        # templates in b2 (a page composition never writes them; the element form derives
        # b1 from b2, so b2 is never empty beside a value)
        page_form = False
        for j in range(k, end):
            r = dec(j) if is_root(j) else None
            if (
                r is not None
                and r.entries
                and len(r.entries) >= 2
                and r.entries[-1][1].pairs
                and not r.entries[-1][2].pairs
            ):
                page_form = True
                break
        if not page_form:
            page_form = _unbound_page_form(dec, k, end)
        if not page_form:
            k = end
            continue
        typed_of: Dict[int, Bag] = {}  # a root's bindings as stored, before conversion
        for j in range(k, end):
            if is_root(j):
                r = dec(j)
                if r is not None and r.entries and len(r.entries) >= 2:
                    typed_of[j] = Bag(list(r.entries[-1][1].pairs), 0)
        for j in range(k, end):
            r = dec(j)
            if r is None or r.entries is None:
                continue
            ents = [[b.copy() for b in e] for e in r.entries]
            N = len(ents) - 1
            if N == 0:
                ents.append([Bag([], 0), Bag([], 0), Bag([], 0)])
                r.entries = ents
                recs[j] = to_po(encode(r))
                continue
            top = ents[N]
            if top[2].pairs:
                continue  # the element form already
            # the owner of the top level: the outermost root (self included) for which
            # this record has a level: N >= 1 + the roots between (+ 1 when the record
            # is itself a root below it)
            chain = roots_above(j) + ([j] if is_root(j) else [])
            owner = None
            for idx, c in enumerate(chain):
                inner = len(chain) - idx - 1  # roots below c on the chain
                if c == j or N >= 1 + inner:
                    owner = c
                    break
            typed = typed_of.get(owner) if owner is not None else None
            if typed is None:
                continue
            if not typed.pairs:
                # an owner placed with nothing bound (its placed level empty, or none): the
                # record keeps its levels and gains the element's level, empty, as every
                # record of a parameterless copy does (LIVE, the fixture oracle's
                # chain6/paramless_copy and chain7/paste_paramless)
                ents.append([Bag([], 0), Bag([], 0), Bag([], 0)])
                r.entries = ents
                recs[j] = to_po(encode(r))
                continue
            if j == owner:
                tmpl = Bag(list(typed.pairs), 0)
            else:
                decl = {nm.lower(): (nm, d) for nm, d in top[0].pairs}
                done = set()
                tmpl = Bag([], 0)
                for pk, pv in typed.pairs:
                    hit = decl.get(pk.lower())
                    if hit and pv and pk.lower() not in done:
                        tmpl.pairs.append((hit[0], pv))
                        done.add(pk.lower())
                for nm, d in top[0].pairs:
                    if nm.lower() not in done and d and d != NO_DEFAULT:
                        tmpl.pairs.append((nm, d))
            vals = Bag([], 0)
            for key, t in tmpl.pairs:
                v = substitute(t, Bag([]), Bag([]), key)
                if v:
                    vals.pairs.append((key, v))
            top[1], top[2] = vals, tmpl
            outer = (
                [(nm, d) for nm, d in template_decls(tmpl).items() if d is not None]
                if j == owner
                else [(nm, d if d is not None else NO_DEFAULT) for nm, d in template_decls(tmpl).items()]
            )
            ents.append([Bag(outer, 0), Bag([], 0), Bag([], 0)])
            r.entries = ents
            recs[j] = to_po(encode(r))
        k = end
    return recs


def subst_level(ent, outer, specials=None, templates_win: bool = False, outer_vals=None, decls=None) -> None:
    """Re-derive an intermediate genie level's value bag from its templates."""
    vals = ent[1]
    if outer_vals is None:
        outer_vals = top_level_templates(outer) if templates_win else outer[1]
    if decls is None:
        decls = outer[0]
    new = [(key, substitute(tmpl, outer_vals, decls, key, specials)) for key, tmpl in ent[2].pairs]
    have = {k.lower() for k, _v in vals.pairs}
    for key, v in new:
        if key.lower() in have:
            vals.set(key, v)
    rank = {k.lower(): i for i, (k, _v) in enumerate(outer[1].pairs)}
    tmpl_of = {key.lower(): tmpl for key, tmpl in ent[2].pairs}

    def order(kv):
        # the outer value the template names first in the outer order (a key named like
        # an outer value ranks by that value): LIVE, CtDraw's load of user
        # pages appended Equip ('%Equipment%') before Header ('%Header%') and kept the
        # template order between two keys naming the same value
        names = [kv[0].lower()] + [
            t.lower() for t in template_refs(Bag([(kv[0], tmpl_of.get(kv[0].lower(), ""))]))
        ]
        hits = [rank[x] for x in names if x in rank]
        return (0, min(hits)) if hits else (1, 0)

    add = [(key, v) for key, v in new if key.lower() not in have and v]
    add.sort(key=order)
    for key, v in add:
        vals.pairs.append((key, v))


def _non_genie_key(key: str) -> bool:
    """A key ending in '.NonGenie' (length >= 10, case-sensitive): its value is copied
    verbatim and declares nothing."""
    return len(key) >= 10 and key.endswith(".NonGenie")


def _token_name(tok: str):
    """(name, default or None) of a token as CtDraw reads it: strtok at '=' (so
    '%=abc%' names abc without a default), a default only when something follows the '='."""
    nm, eq, dflt = tok.partition("=")
    if not nm:
        return dflt.split("=", 1)[0], None
    return nm, (dflt if (eq and dflt) else None)


#: the composition's token reader, public: Replace Genie's mapping reads tokens as the
#: composition does (doc/genieswap)
read_token = _token
token_name = _token_name


def template_refs(bag: Bag) -> List[str]:
    """Parameter names referenced by a template bag's %name% tokens, in order (a
    '.NonGenie' key and an empty '%%' token pass over)."""
    out: List[str] = []
    seen = set()
    for _k, t in bag.pairs:
        if _non_genie_key(_k):
            continue
        i = 0
        while i < len(t):
            if t[i] != "%":
                i += 1
                continue
            tok, j = _token(t, i)
            if tok is None:
                break
            if not tok:
                i = j  # an empty '%%': the scan goes on
                continue
            nm, _d = _token_name(tok)
            if nm and nm.lower() not in seen:
                seen.add(nm.lower())
                out.append(nm)
            i = j
    return out


def template_decls(bag: Bag) -> "OrderedDict[str, Optional[str]]":
    """The declarations a template bag's tokens make."""
    out: "OrderedDict[str, Optional[str]]" = OrderedDict()
    key_of: Dict[str, str] = {}
    for _k, t in bag.pairs:
        if _non_genie_key(_k):
            continue  # copied verbatim, declares nothing
        i = 0
        while i < len(t):
            if t[i] != "%":
                i += 1
                continue
            tok, j = _token(t, i)
            if tok is None:
                break
            if not tok:
                i = j  # an empty '%%': the scan goes on
                continue
            nm, dflt = _token_name(tok)
            if nm:
                name = key_of.setdefault(nm.lower(), nm)
                if name not in out:
                    out[name] = None
                if dflt:
                    out[name] = dflt
            i = j
    return out


# =========================================================================== element
@dataclass
class Element:
    lib_path: str
    library: str  # the link's library token (file stem, as the link writes it)
    element: str  # block name
    ext: str  # 'ctm' / 'ctl'
    e: Tuple[int, int]  # element origin: page-head i32 @0x0C/0x10
    records: List[PageObject]
    N: Tuple[int, int, int, int]  # element root rect1
    identity: bytes = b""  # element page-head identity (20 bytes @0x20)
    #: loaded for a PAGE composition (load_element(compose=True)): a genie nested in it whose
    #: stored copy is older than its own element (a different record structure) was
    #: re-composed from that element (refresh_nested)
    compose: bool = False


# =========================================================================== load memo
# Loading an element re-composes every genie NESTED in it (refresh_nested: one
# load_element per nested INSTANCE, each doing the same, plus art_twins'
# loads). The loaded (refreshed) Element is therefore kept per process
# (_MEMO) and every result that is reused is proven to be what a load would
# compute NOW, from what the load read:
#   * its own library: the file must still be the version (mtime, size, change) it
#     was read at (open_library's own key, fileio.file_version; by version, not by library object: a library
#     the cache's byte bound dropped and read again is the same file);
#   * every reference it resolved: the resolver walk (SymbolResolver.resolve:
#     the same-named libraries of each extension up to the one holding the
#     element) must give the same library objects, re-walked with the
#     resolver the reuse would resolve with; for two FRESH resolvers of the same
#     search path (a load without a resolver builds SymbolResolver.for_page of
#     its library) the walk is the same when every search directory has its
#     recorded mtime (stat'ed before the resolver listed it; NTFS stamps a
#     directory on create / delete / rename) and every library is current;
#   * every SymbolResolver.for_page its nested loads built: the same search path;
#   * every tag-format lookup its text measures made (textmetrics._tag_format,
#     the editing layer's installed tag database): the same answers;
#   * the refresh flag is part of the key.
# (Inside symbols.held_libraries, "the version now" is the version the hold read: a copy
# and the stamp a save or a page update writes for it come from one read of the library.)
# Everything a load reads during one outermost call (an "operation", _Op) is
# taken to be static; one resolver per library folder is shared by its nested
# loads. ctgedit's own library writes bump editing._LIBRARY_GENERATION, which clears
# the memo (to keep memory low; the entries would fail their version checks anyway).
# Callers get a copy of the Element with its own records list (the PageObjects are
# shared: nothing mutates an Element's records in place).
_MEMO: "OrderedDict[tuple, _Entry]" = OrderedDict()
_MEMO_RECORDS = [0]
#: bound of the memo, in records held
MEMO_RECORD_LIMIT = int(os.environ.get("GP_MEMO_RECORDS", "60000") or 60000)
_MEMO_GEN = [None]
_OP = [None]
_RAISED = object()


def _memo_on() -> bool:
    """GP_MEMO=0 turns the memo off (every load computed afresh: the A/B reference)."""
    return os.environ.get("GP_MEMO", "1") != "0"


class _Deps:
    """What a memoised computation read besides its arguments (see above)."""

    __slots__ = ("libs", "probes", "fps", "calls", "none_ctx")

    def __init__(self, none_ctx: bool):
        self.libs: dict = {}  # library path -> the version read (SymbolLibrary.version)
        self.probes: dict = {}  # (tag, paths, library, element, ext) -> (walk, snapshot)
        self.fps: dict = {}  # folder -> SymbolResolver.for_page search path
        self.calls: dict = {}  # tag-format lookup: expression -> answer
        self.none_ctx = none_ctx  # made with no tag-format lookup installed


class _Entry:
    __slots__ = ("el", "deps", "size")

    def __init__(self, el, deps, size):
        self.el, self.deps, self.size = el, deps, size


class _Frame:
    """A memoised computation in progress: its own resolver, and whether that
    resolver is a FRESH one its load built (a load without a resolver) -- the
    probes made inside such a load are, for the frames around it, those of a
    fresh resolver of that search path whatever resolver object they hold."""

    __slots__ = ("key", "resolver", "fresh_load", "deps", "tainted")

    def __init__(self, key, resolver, fresh_load, none_ctx):
        self.key, self.resolver, self.fresh_load = key, resolver, fresh_load
        self.deps, self.tainted = _Deps(none_ctx), False


class _TagRecorder:
    """The installed tag-format lookup, recording what the operation's frames asked."""

    __slots__ = ("real", "op")

    def __init__(self, real, op):
        self.real, self.op = real, op

    def __call__(self, expr):
        op = self.op
        if threading.get_ident() != op.thread:
            # another thread's measure (a page-browser preview, the fast-display
            # check) went through the global lookup: not this operation's
            return self.real(expr)
        try:
            v = self.real(expr)
        except Exception:
            for f in op.frames:
                f.deps.calls[expr] = _RAISED
            raise
        for f in op.frames:
            f.deps.calls[expr] = v
        return v


class _Op:
    """One outermost load / compose call: its fresh resolvers, stats and verdicts."""

    def __init__(self):
        self.thread = threading.get_ident()
        self.depth = 0
        self.owner = None  # the frame of the call that opened it (_op_enter)
        self.frames: List[_Frame] = []
        self.by_dir: dict = {}  # folder -> SymbolResolver.for_page of a file in it
        self.fresh: dict = {}  # id(resolver) -> (resolver, paths, snapshot)
        self.by_paths: dict = {}  # search path -> a fresh resolver
        self.mtimes: dict = {}
        self.stats: dict = {}
        self.walks: dict = {}
        self.verdicts: dict = {}
        self.tf_real = TM._tag_format
        self.none_ctx = self.tf_real is None
        self.tf_rec = None
        #: id(nested element) -> (it, its description, its records, its twins, the digest of
        #: them the geometry memo's key takes): :func:`_sub_geometry_key`
        self.sub_keys: dict = {}

    # -- fresh resolvers ---------------------------------------------------
    def _register(self, r) -> None:
        paths = tuple(r.search_paths)
        # stat the folders BEFORE the resolver lists them (lazily, on its
        # first resolve): a file created after the stat changes the mtime
        self.fresh[id(r)] = (r, paths, tuple(self.mtime(d) for d in paths))
        self.by_paths.setdefault(paths, r)

    def for_page(self, path: str):
        """SymbolResolver.for_page(path) (what refresh_nested builds for a
        load without a resolver), one per folder and operation."""
        d = os.path.dirname(os.path.abspath(path))
        r = self.by_dir.get(d)
        if r is None:
            r = SymbolResolver.for_page(path)
            self._register(r)
            self.by_dir[d] = r
        return r

    def fresh_for_paths(self, paths: tuple):
        r = self.by_paths.get(paths)
        if r is None:
            r = SymbolResolver(list(paths))
            self._register(r)
        return r

    def snapshot(self, r):
        got = self.fresh.get(id(r))
        return got[2] if got is not None and got[0] is r else None

    # -- file state --------------------------------------------------------
    def mtime(self, d: str):
        v = self.mtimes.get(d, _RAISED)
        if v is _RAISED:
            try:
                v = os.stat(d).st_mtime_ns
            except OSError:
                v = None
            self.mtimes[d] = v
        return v

    def stat(self, p: str):
        v = self.stats.get(p, _RAISED)
        if v is _RAISED:
            try:
                # (the version open_library keys the library by: a second write of one
                # size within one clock tick is another version; inside held_libraries the
                # version the hold read, so a memo entry is reused only for that version)
                v = library_version(p)
            except OSError:
                v = None
            self.stats[p] = v
        return v

    def lib_current(self, lib) -> bool:
        """The library file is still the version ``lib`` was read at (stat once per
        operation; a library of unknown version never is)."""
        ver = getattr(lib, "version", None)
        return ver is not None and self.stat(lib.path) == ver

    def walk(self, r, library: str, element: str, ext: str):
        k = (id(r), library, element, ext)
        w = self.walks.get(k)
        if w is None:
            w = self.walks[k] = _walk(r, library, element, ext, self)
        return w


_OP_LOCK = threading.Lock()


def _taint_open_frames(_where: str = "", _etype: str = "") -> None:
    """A swallowed error was noted: don't memoise any open computation."""
    op = _OP[0]
    if op is None or op.thread != threading.get_ident():
        return
    for f in op.frames:
        f.tainted = True


from ctgedit.infra import logsetup as _logsetup  # noqa: E402

_logsetup.on_note(_taint_open_frames)


def _op_enter() -> Optional[_Op]:
    """Enter the current operation (a new one at the outermost call); None on a
    thread other than the operation's (then nothing is memoised there)."""
    op = _OP[0]
    if op is None:
        with _OP_LOCK:
            # ONE operation at a time: two threads opening one at once each set _OP, and
            # the one overwritten recorded its frames' dependencies nowhere (its memo
            # entries would be stored without them)
            if _OP[0] is None:
                return _op_open()
            op = _OP[0]
    if op.thread != threading.get_ident():
        return None
    op.depth += 1
    return op


def _op_open() -> _Op:
    """A new operation, made current (under _OP_LOCK, called by _op_enter only: the
    frame that called _op_enter owns it)."""
    m = sys.modules.get("ctgedit.editing")
    gen = getattr(m, "_LIBRARY_GENERATION", [None])[0] if m is not None else None
    if gen != _MEMO_GEN[0]:
        # a library was written here (a library the cache only dropped and read again keeps
        # the entries: they check each library by its file's version)
        clear_memo()
        _MEMO_GEN[0] = gen
    op = _Op()
    op.owner = sys._getframe(2)
    if op.tf_real is not None:
        op.tf_rec = _TagRecorder(op.tf_real, op)
        TM._tag_format = op.tf_rec
    op.depth = 1
    _OP[0] = op  # (nothing that can fail after this)
    return op


def _op_leave(op: Optional[_Op]) -> None:
    """Leave the operation. The call that opened it ends it."""
    if op is None:
        return
    op.depth -= 1
    if op.depth <= 0 or sys._getframe(1) is op.owner:
        _op_close(op)


def _op_close(op: _Op) -> None:
    if op.tf_rec is not None and TM._tag_format is op.tf_rec:
        TM._tag_format = op.tf_real
    if _OP[0] is op:
        _OP[0] = None
    op.owner = None
    op.depth = 0


def clear_memo() -> None:
    """Forget every loaded element (the next loads read the libraries again)."""
    _MEMO.clear()
    _MEMO_RECORDS[0] = 0
    _GEOM_MEMO.clear()


def _walk(r, library: str, element: str, ext: str, op: Optional[_Op] = None) -> tuple:
    """What resolving a symbol reference walks."""
    out = []
    for x in [ext] + [e for e in LIBRARY_EXTENSIONS if e != ext]:
        libs = tuple(r.libraries(library, x))
        opened = {L.path for L in libs}
        failed = tuple(
            (p, op.stat(p) if op is not None else None)
            for p in r._find_library_files(library, x)
            if p not in opened
        )
        out.append((x, libs, failed))
        if any(L.element(element) is not None for L in libs):
            break
    return tuple(out)


def _same_walk(a: tuple, b: tuple) -> bool:
    if len(a) != len(b):
        return False
    for (x1, l1, f1), (x2, l2, f2) in zip(a, b):
        if (
            x1 != x2
            or len(l1) != len(l2)
            or f1 != f2
            or any(p is not q and not _same_library(p, q) for p, q in zip(l1, l2))
        ):
            return False
    return True


def _same_library(a, b) -> bool:
    """Two reads of one library file at one version (a library the cache dropped and read
    again)."""
    va = getattr(a, "version", None)
    return va is not None and va == getattr(b, "version", None) and a.path == b.path


def _cur_op() -> Optional[_Op]:
    """The operation this thread is in (None outside one / on another thread)."""
    op = _OP[0]
    return op if op is not None and op.thread == threading.get_ident() else None


def _note(op: Optional[_Op], kind: str, *args) -> None:
    """Record a dependency into every frame of the operation."""
    if op is None or not op.frames:
        return
    if kind == "lib":
        path, lib = args
        ver = getattr(lib, "version", None)
        for f in op.frames:
            f.deps.libs[path] = ver
    elif kind == "fp":
        d, paths = args
        for f in op.frames:
            f.deps.fps[d] = paths
    elif kind == "probe":
        r, ref = args
        try:
            w = op.walk(r, ref.library, ref.element, ref.ext)
        except Exception:  # noqa: BLE001 -- an unwalkable resolver: nothing is kept
            _note_exc("genie_place._note")
            for f in op.frames:
                f.tainted = True
            return
        _add_probe(op, r, (ref.library, ref.element, ref.ext), w)


def _add_probe(op: _Op, r, pk: tuple, w: tuple, virtual=None) -> None:
    """Record a resolver walk in every open frame."""
    snap = op.snapshot(r)
    ctx_key = ("ctx", op.fresh[id(r)][1]) + pk if snap is not None else None
    own = {None: None, "passed": True, "fresh": False, "ctx": False}[virtual]
    for f in reversed(op.frames):
        if own is None and f.resolver is r:
            own = True  # the frame whose code resolved with r
        if own is True and f.resolver is r:
            f.deps.probes[("own", None) + pk] = (w, snap)
            if f.fresh_load:
                own = False  # its load built r: fresh for the frames around
        elif ctx_key is not None:
            f.deps.probes[ctx_key] = (w, snap)
        else:
            f.tainted = True  # an explicit resolver of another computation


def _probe_ok(op: _Op, key: tuple, val: tuple, use) -> bool:
    tag, paths, library, element, ext = key
    w, snap = val
    r = use if tag == "own" else op.fresh_for_paths(paths)
    if r is None:
        return False
    now = op.snapshot(r)
    if snap is not None and now is not None and "\\" not in library and "/" not in library:
        # two fresh resolvers of the same path: the same listing (no folder
        # changed since) and the same libraries (each file unchanged) walk alike
        if snap != now:
            return False
        for _x, libs, failed in w:
            if not all(op.lib_current(L) for L in libs):
                return False
            if any(op.stat(p) != st for p, st in failed):
                return False
        return True
    try:
        return _same_walk(op.walk(r, library, element, ext), w)
    except Exception:  # noqa: BLE001
        _note_exc("genie_place._probe_ok")
        return False


def _valid(op: _Op, deps: _Deps, use) -> bool:
    """``deps`` hold now, for a reuse that resolves with ``use`` (once per operation)."""
    k = (id(deps), id(use))
    got = op.verdicts.get(k)
    if got is None or got[0] is not deps or got[1] is not use:
        got = op.verdicts[k] = (deps, use, _check(op, deps, use))
    return got[2]


def _check(op: _Op, deps: _Deps, use) -> bool:
    if deps.none_ctx:
        if not op.none_ctx:
            return False
    elif deps.calls:
        # (an entry that asked nothing measured no bare tag: valid either way)
        if op.none_ctx:
            return False
        for expr, ans in deps.calls.items():
            try:
                now = op.tf_real(expr)
            except Exception:  # noqa: BLE001
                _note_exc("genie_place._check")
                now = _RAISED
            if now is not ans and now != ans:
                return False
    for p, ver in deps.libs.items():
        if ver is None or op.stat(p) != ver:
            return False
    for d, paths in deps.fps.items():
        try:
            r = op.for_page(os.path.join(d, "_"))
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._check")
            return False
        if tuple(r.search_paths) != paths:
            return False
    for key, val in deps.probes.items():
        if not _probe_ok(op, key, val, use):
            return False
    return True


def _merge_deps(op: _Op, deps: _Deps, use, fresh_load: bool) -> None:
    """A validated reuse (resolving with ``use``; ``fresh_load`` = a load that
    built ``use`` itself): the enclosing frames depend on what it depended on."""
    if not op.frames:
        return
    for f in op.frames:
        d = f.deps
        d.libs.update(deps.libs)
        d.fps.update(deps.fps)
        d.calls.update(deps.calls)
    for key, (w, _s) in deps.probes.items():
        tag, paths, library, element, ext = key
        if tag == "own":
            _add_probe(op, use, (library, element, ext), w, virtual="fresh" if fresh_load else "passed")
        else:
            _add_probe(op, op.fresh_for_paths(paths), (library, element, ext), w, virtual="ctx")


def _frame_push(op: _Op, key, resolver, fresh_load: bool = False) -> _Frame:
    f = _Frame(key, resolver, fresh_load, op.none_ctx)
    if key is not None and any(g.key == key for g in op.frames):
        # a cycle (an element nesting itself; _load_element refuses the load itself,
        # NestingCycle): what is computed on this stack depends on where the cycle was
        # entered, so nothing of it is kept
        for g in op.frames:
            g.tainted = True
        f.tainted = True
    op.frames.append(f)
    return f


def _frame_pop(op: _Op, f: _Frame) -> None:
    if op.frames and op.frames[-1] is f:
        op.frames.pop()
    else:  # never expected: keep the stack sane
        if f in op.frames:
            op.frames.remove(f)
        f.tainted = True


def _memo_put(key, el: "Element", deps: _Deps) -> None:
    el.__dict__["_gp_canon"] = el
    size = max(1, len(el.records))
    old = _MEMO.pop(key, None)
    if old is not None:
        _MEMO_RECORDS[0] -= old.size
    _MEMO[key] = _Entry(el, deps, size)
    _MEMO_RECORDS[0] += size
    while _MEMO_RECORDS[0] > MEMO_RECORD_LIMIT and len(_MEMO) > 1:
        _k, e = _MEMO.popitem(last=False)
        _MEMO_RECORDS[0] -= e.size


def _element_copy(el: "Element") -> "Element":
    """A caller's copy of a memoised Element: its own records list (callers
    re-assign .records / .library / .element / .lib_path)."""
    c = _copy.copy(el)
    c.records = list(el.records)
    nt = el.__dict__.get("nested_twins")
    if nt is not None:
        c.nested_twins = dict(nt)
    return c


def _canon_view(el) -> Optional["Element"]:
    """The memoised Element ``el`` is a copy of, while its records are still
    that element's records (the memos on it -- decoded records, declared
    parameters, art twins -- are functions of them)."""
    canon = getattr(el, "__dict__", {}).get("_gp_canon")
    if canon is None:
        return None
    a, b = el.records, canon.records
    if a is b:
        return canon
    if len(a) != len(b):
        return None
    for x, y in zip(a, b):
        if x is not y:
            return None
    return canon


# =========================================================================== nesting cycles
# A genie that holds a copy of itself, directly or through the genies it nests (n0 placed
# into n0, or n2 into n0 while n2 holds n1 and n1 holds n0). CtDraw places such a copy but
# refuses the save ('Attempt to save page with a link to itself', LIVE: the fixture oracle's
# chain5/cycles), so its own libraries hold none, but a library edited by other means can.
# Composing a genie in the cycle would recurse at each of its branches without end. The
# loads that compose (refresh) are kept on a stack per thread: a load of an element already on it is
# the cycle, refused at once (:class:`NestingCycle`) and taken by the composition as a copy
# that cannot be composed (kept as stored).
_LOADING = threading.local()


class NestingCycle(Exception):
    """An element met again while its own composition is in progress: a genie that holds a
    copy of itself. ``chain`` = the 'library.element' names from it back to itself."""

    def __init__(self, chain):
        self.chain = tuple(chain)
        super().__init__(cycle_text(self.chain))


def cycle_text(chain) -> str:
    """'fxnest.n0 > fxnest.n2 > fxnest.n1 > fxnest.n0' (each one holds the next)."""
    return " > ".join(chain)


def _loading_stack() -> list:
    st = getattr(_LOADING, "stack", None)
    if st is None:
        st = _LOADING.stack = []
    return st


def _met_cycle(el, chain) -> None:
    """``el``'s composition met the nesting cycle ``chain`` (its ``cycles``: the chains, for
    the caller to name)."""
    el.cycles = frozenset(getattr(el, "cycles", frozenset()) | {tuple(chain)})


def _load_key(lib_path: str, element: str) -> tuple:
    return os.path.normcase(os.path.abspath(lib_path)), (element or "").lower()


def _element_label(lib_path: str, element: str) -> str:
    return "%s.%s" % (os.path.splitext(os.path.basename(lib_path))[0], element)


def _copy_reference(po: PageObject):
    """The genie reference a record stands for in a nesting walk."""
    if po.class_flag and (po.subtype_size & 0x201) == 0x201:
        try:
            return reference_from_object(po)
        except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link nests nothing
            return None
    if po.type_code == 0xFFFF and not po.class_flag:
        got = placeholder_genie(po)
        return got[0] if got is not None else None
    return None


def nesting_chain(
    lib_path: str, element: str, target_path: str, target: str, resolver=None
) -> Optional[List[str]]:
    """The chain of genies by which an element holds a copy of ``target``."""
    want = _load_key(target_path, target)
    start = _load_key(lib_path, element)
    if start == want:
        return [_element_label(lib_path, element)]
    seen = {start}
    queue = [(lib_path, element, [_element_label(lib_path, element)])]
    while queue:
        path, name, chain = queue.pop(0)
        try:
            recs = _parse_root(path, name)
            res = resolver if resolver is not None else SymbolResolver.for_page(path)
        except Exception:  # noqa: BLE001  # swallow-ok: an unreadable genie nests nothing
            continue
        for po in recs[1:]:
            ref = _copy_reference(po)
            if ref is None:
                continue
            try:
                got = res.resolve(ref)
            except Exception:  # noqa: BLE001  # swallow-ok: an unresolved copy nests nothing
                continue
            if got is None or ref.ext != "ctm":
                continue
            key = _load_key(got.library_path, ref.element)
            step = chain + [_element_label(got.library_path, ref.element)]
            if key == want:
                return step
            if key not in seen:
                seen.add(key)
                queue.append((got.library_path, ref.element, step))
    return None


def records_nesting_chain(
    records, target_path: str, target: str, resolver, shortest: bool = False
) -> Optional[List[str]]:
    """The chain of genies by which ``records`` hold a copy of ``target``."""
    seen = set()
    best = None
    for po in records:
        ref = _copy_reference(po)
        if ref is None:
            continue
        try:
            got = resolver.resolve(ref) if resolver is not None else None
        except Exception:  # noqa: BLE001  # swallow-ok: an unresolved copy nests nothing
            continue
        if got is None or ref.ext != "ctm":
            continue
        key = _load_key(got.library_path, ref.element)
        if key in seen:
            continue
        seen.add(key)
        chain = nesting_chain(got.library_path, ref.element, target_path, target, resolver)
        if chain is None:
            continue
        if not shortest:
            return chain
        if best is None or len(chain) < len(best):
            best = chain
            if len(best) == 1:
                break  # (a copy of the target itself)
    return best


def load_element(
    lib_path: str,
    element: str,
    resolver=None,
    refresh: Optional[bool] = None,
    compose: bool = False,
    only=None,
) -> Element:
    """An element's first root subtree as CtDraw's load holds it."""
    return _load(
        lib_path, element, resolver, refresh, True, compose, frozenset(only) if only is not None else None
    )


def memoised_element(
    lib_path: str, element: str, resolver=None, refresh: Optional[bool] = None, compose: bool = False
) -> Optional[Element]:
    """What :func:`load_element` returns -- when the memo holds it valid now;
    None instead of loading (the caller then gets its answer another way)."""
    return _load(lib_path, element, resolver, refresh, False, compose)


def _load(
    lib_path: str, element: str, resolver, refresh, compute: bool, compose: bool = False, only=None
) -> Optional[Element]:
    memo_on = _memo_on()
    op = _op_enter()
    if op is None or not memo_on:
        try:
            return _load_element(lib_path, element, resolver, refresh, compose, only) if compute else None
        finally:
            _op_leave(op)
    try:
        eff = refresh if refresh is not None else True
        use = None
        if eff:
            if resolver is not None:
                use = resolver
                paths = getattr(resolver, "search_paths", None)
                if paths is None:  # not a SymbolResolver: no memo
                    return _load_element(lib_path, element, resolver, eff, compose, only) if compute else None
            else:
                try:
                    use = op.for_page(lib_path)
                except Exception:  # noqa: BLE001 -- refresh_nested fails alike: no memo
                    _note_exc("genie_place._load")
                    return _load_element(lib_path, element, None, eff, compose, only) if compute else None
                _note(op, "fp", os.path.dirname(os.path.abspath(lib_path)), tuple(use.search_paths))
        key = (
            lib_path,
            element,
            eff,
            tuple(use.search_paths) if eff else None,
            bool(compose and eff),
            only if eff else None,
        )
        fresh = eff and resolver is None
        ent = _MEMO.get(key)
        if ent is not None:
            if _valid(op, ent.deps, use):
                _MEMO.move_to_end(key)
                _merge_deps(op, ent.deps, use, fresh)
                return _element_copy(ent.el)
            _MEMO.pop(key, None)
            _MEMO_RECORDS[0] -= ent.size
        if not compute:
            return None
        f = _frame_push(op, key, use, fresh)
        try:
            el = _load_element(lib_path, element, use, eff, compose, only)
        finally:
            _frame_pop(op, f)
        if not f.tainted:
            _memo_put(key, el, f.deps)
            return _element_copy(el)
        return el
    finally:
        _op_leave(op)


def _load_element(
    lib_path: str,
    element: str,
    resolver=None,
    refresh: Optional[bool] = None,
    compose: bool = False,
    only=None,
) -> Element:
    lib = open_library(lib_path)
    _note(_cur_op(), "lib", lib_path, lib)
    blk = lib._find_block(element)
    pb = lib.element(element)
    if blk is None or pb is None:
        raise KeyError("%s: no element %r" % (lib_path, element))
    e = struct.unpack_from("<2i", blk.data, 0x0C) if lib.container.word2 >= 0x0C else (0, 0)
    recs = [o for o in pb.objects if o.raw_payload is None]
    # the instance = the FIRST ROOT subtree (the trailing raw records are the
    # element-definition trailer and are never copied)
    end, pending = 0, 1
    while pending and end < len(recs):
        pending -= 1
        pending += recs[end].class_flag
        end += 1
    recs = recs[:end]
    stem, ext = os.path.splitext(os.path.basename(lib_path))
    if ext.lower() == ".ctl":
        # a one-object symbol stored bare (CtDraw's save does not keep it so): CtDraw's
        # load holds it under a group root. LIVE (the fixture
        # oracle, place linked_symbol): LibraryObjectPlaceEx of the bare-rectangle fx_box
        # gave the instance forest 0x08 (dwFlags 0x9) > 0x0F, as for a group-root symbol
        from .element import wrapped_tree

        recs = wrapped_tree(recs, e)
    gh = G.parse_geom_header(recs[0].body_raw)
    el = Element(
        lib_path,
        stem,
        blk.name,
        ext.lstrip(".").lower(),
        tuple(e),
        recs,
        tuple(gh.rect1),
        bytes(pb.page_identity),
    )
    # templates are composed from their STORED records (refresh=False,
    # format/template_place); genies refresh nested genies like CtDraw's load
    if refresh if refresh is not None else True:
        key = _load_key(lib_path, element)
        stack = _loading_stack()
        if any(k == key for k, _l in stack):
            # the element is being composed further up: it holds a copy of itself
            at = next(i for i, (k, _l) in enumerate(stack) if k == key)
            for f in getattr(_cur_op(), "frames", ()):
                f.tainted = True  # (what depends on where the cycle was entered)
            raise NestingCycle([lab for _k, lab in stack[at:]] + [_element_label(lib_path, blk.name)])
        genie = (
            el.ext == "ctm"
            and "@super" not in el.element.lower()
            and declares_parameters(el.records, P.forest_parents(el.records))
        )
        if genie:
            # a genie element still holding nested copies in their PAGE form (pasted from a
            # page) as its save writes it and its window shows it: in the element form
            # (convert_page_form; genie_norm.normalise_records and element_session do the
            # same). Placed as stored, such copies keep their typed host tokens where any
            # save of the genie gives the bound values
            el.records = convert_page_form(el.records, genie=True)
        # (a genie element keeps its nested copies in the element form: _refresh_nested)
        el.element_form_copies = bool(genie)
        el.compose = bool(compose)
        stack.append((key, _element_label(lib_path, blk.name)))
        try:
            refresh_nested(el, resolver, compose=bool(compose), only=only)
            if compose and el.ext == "ctm":
                # a placeholder the element holds (its save while a nested genie's library did
                # not open) is that genie again once its library opens, as CtDraw's element
                # load holds it for a placement or a page's load
                res = resolver
                if res is None:
                    try:
                        res = SymbolResolver.for_page(lib_path)
                    except Exception:  # noqa: BLE001 -- the markers stay
                        _note_exc("genie_place._load_element/placeholders")
                        res = None
                if res is not None:
                    place_element_placeholders(el, res)
        finally:
            stack.pop()
    el.N = engine_root_extent(el)
    return el


def bag_keys(bag: "Bag") -> frozenset:
    """A bag's keys, lower case."""
    return frozenset(kk.lower() for kk, _v in bag.pairs)


def memory_values(ents) -> "Bag":
    """A current element record's value bag as CtDraw holds it after load."""
    vals = ents[0][1]
    if len(ents) < 3:
        return vals
    lvl = ents[1]
    tm = {kk.lower(): t for kk, t in ents[0][2].pairs}
    out = []
    have = set()
    for kk, v in vals.pairs:
        t = tm.get(kk.lower())
        if t is not None and not substitute(t, lvl[1], lvl[0], kk):
            continue
        out.append((kk, v))
        have.add(kk.lower())
    for kk, t in ents[0][2].pairs:
        if kk.lower() not in have:
            v = substitute(t, lvl[1], lvl[0], kk)
            if v:
                out.append((kk, v))
    return Bag(out, vals.ov)


def _level_defaults(level, templates: bool = False) -> None:
    """Append declared defaults missing from a nested copy member's binding level."""
    have = {kk.lower() for kk, _v in level[1].pairs}
    decl = [(nm, d) for nm, d in level[0].pairs if d and d != NO_DEFAULT and nm.lower() != "an"]
    if templates:
        _default_templates(level, decl)
    add = [(nm, d) for nm, d in decl if nm.lower() not in have]
    if add:
        level[1] = Bag(list(level[1].pairs) + add, level[1].ov)


def _default_templates(level, decl) -> None:
    """Append declared defaults missing from an element-form binding level's templates."""
    have_t = {kk.lower() for kk, _v in level[2].pairs}
    vals = {kk.lower(): v for kk, v in level[1].pairs}
    addt = [(nm, vals.get(nm.lower()) or d) for nm, d in decl if nm.lower() not in have_t]
    if addt:
        level[2] = Bag(list(level[2].pairs) + addt, level[2].ov)


def _bind_declared(ents, top: int, root_level, page_form: bool) -> None:
    """Bind a nested copy member's declarations, in place."""
    if root_level is None or top >= len(ents):
        return
    level = ents[top]
    decl = {nm.lower(): nm for nm, _d in level[0].pairs if nm.lower() != "an"}
    want = set(decl) - bag_keys(level[1]) - bag_keys(level[2])
    if not want:
        return
    rv, rt = root_level[1], root_level[2]
    if page_form:
        add = [(decl[kk.lower()], v) for kk, v in rv.pairs if kk.lower() in want and v]
        if add:
            level[1] = Bag(list(level[1].pairs) + add, level[1].ov)
        return
    seen = set()
    addt = []
    for kk, t in rt.pairs if rt.pairs else rv.pairs:
        low = kk.lower()
        if low in want and low not in seen and t:
            addt.append((decl[low], t))
            seen.add(low)
    if not addt:
        return
    vals = {kk.lower(): v for kk, v in rv.pairs}
    addv = [(nm, vals[nm.lower()]) for nm, _t in addt if vals.get(nm.lower())]
    level[2] = Bag(list(level[2].pairs) + addt, level[2].ov)
    if addv:
        level[1] = Bag(list(level[1].pairs) + addv, level[1].ov)
    if top + 1 < len(ents):
        outer = ents[top + 1]
        have = bag_keys(outer[0])
        new = [
            (nm, d if d is not None else NO_DEFAULT)
            for nm, d in template_decls(Bag(addt)).items()
            if nm.lower() not in have
        ]
        if new:
            outer[0] = Bag(list(outer[0].pairs) + new, outer[0].ov)


def _own_level_defaults(level, declared, templates: bool = False) -> None:
    """Update a nested genie root's own level against the genie's current parameters, in place."""
    decl = [(nm, d) for nm, d in declared.items() if d and nm.lower() != "an"]
    if decl and [tuple(p_) for p_ in level[0].pairs] != decl:
        level[0] = Bag(list(decl), level[0].ov)
    if templates:
        _default_templates(level, decl)
    have = {kk.lower() for kk, _v in level[1].pairs}
    add = [(nm, d) for nm, d in decl if nm.lower() not in have]
    if add:
        level[1] = Bag(list(level[1].pairs) + add, level[1].ov)


def _values_within_templates(ent) -> None:
    """A record level's value bag (b1) without the keys its template bag (b2) lacks
    (ASCII case folded), in place: what CtDraw's element save leaves on every record of a
    nested copy."""
    have = {kk.lower() for kk, _v in ent[2].pairs}
    if any(kk.lower() not in have for kk, _v in ent[1].pairs):
        ent[1] = Bag([(kk, v) for kk, v in ent[1].pairs if kk.lower() in have], ent[1].ov)


def _merge_bag(
    stale: "Bag", cur: "Bag", keep_sheet: bool = False, templated=frozenset(), sheet_values: bool = False
) -> "Bag":
    """Update a stale nested genie bag from the current element, as CtDraw does."""
    curd = {k.lower(): v for k, v in cur.pairs}
    have = {k.lower() for k, _v in stale.pairs}
    out = [
        (k, curd[k.lower()] if k.lower() in curd and not (sheet_values and k.startswith("!*")) else v)
        for k, v in stale.pairs
        if k.lower() in curd or k.lower() in templated or k.startswith("!*") or "<User>" in v
    ]
    out += [(k, v) for k, v in cur.pairs if k.lower() not in have]
    return Bag(out, stale.ov)


def refresh_nested(
    el: "Element", resolver=None, _depth: int = 0, compose: Optional[bool] = None, only=None, merge_only=None
) -> int:
    """Recompose every genie nested in an element from its current definition."""
    if _depth > 6:
        return 0
    if compose is None:
        compose = bool(getattr(el, "compose", False))
    op = _op_enter()
    try:
        return _refresh_nested(
            el,
            resolver,
            op,
            compose,
            frozenset(only) if only is not None else None,
            frozenset(merge_only) if merge_only is not None else None,
        )
    finally:
        _op_leave(op)


def _nests_any(sub: Optional["Element"], keys, resolver=None, _depth: int = 0, _seen=None) -> bool:
    """Does ``sub`` hold a copy of any of the genies ``keys``, at any depth?"""
    if sub is None or not keys:
        return False
    if _seen is None:
        _seen = set()
    inner = []
    for po in sub.records[1:]:
        if po.class_flag and po.subtype_size & 0x200 and po.subtype_size & 0x1:
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
                continue
            if ref is None:
                continue
            key = (ref.library.lower(), ref.element.lower())
            if key in keys:
                return True
            if key not in _seen:
                _seen.add(key)
                inner.append(ref)
    if resolver is None or _depth >= 6:
        return False
    for ref in inner:
        try:
            res = resolver.resolve(ref)
            el = load_element(res.library_path, ref.element, refresh=False) if res is not None else None
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._nests_any")
            continue
        if _nests_any(el, keys, resolver, _depth + 1, _seen):
            return True
    return False


def nested_keys(records) -> frozenset:
    """(library, element), lower case, of every genie nested in ``records`` (an element's
    records) whose copy carries its link: the ``merge_only`` of :func:`refresh_nested` that
    composes every copy again, the structurally changed ones too (Edit > Library > Compose
    Nested Genies Again; ``merge_only=None`` merges only the copies that still line up)."""
    out = set()
    for po in records:
        if po.class_flag and (po.subtype_size & 0x201) == 0x201:
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
                continue
            if ref is not None:
                out.add((ref.library.lower(), ref.element.lower()))
    return frozenset(out)


def _aligns(sub_recs, recs, k: int, end: int) -> bool:
    """Does the stored nested copy line up with the element's records?"""
    return len(sub_recs) == end - k and all(
        sub_recs[j].type_code == recs[k + j].type_code
        and sub_recs[j].class_flag == recs[k + j].class_flag
        and (j == 0 or not (sub_recs[j].subtype_size ^ recs[k + j].subtype_size) & 0x200)
        for j in range(end - k)
    )


def _rekey_copy(recs, k: int, end: int, sub_recs) -> int:
    """Re-key an aligned nested copy's AN strings to its element records, in place."""
    n = 0
    for j in range(1, end - k):
        h = G.get_handle(sub_recs[j])
        po = recs[k + j]
        if h is None or po.an_str == str(h):
            continue
        r = decode(po)
        r.an = str(h)
        recs[k + j] = to_po(encode(r))
        n += 1
    return n


def _refresh_nested(
    el: "Element", resolver, op: Optional[_Op], compose: bool = False, only=None, merge_only=None
) -> int:
    if resolver is None:
        try:
            # one fresh resolver per library folder and operation (the same
            # search path SymbolResolver.for_page builds for each call)
            resolver = op.for_page(el.lib_path) if op is not None else SymbolResolver.for_page(el.lib_path)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._refresh_nested")
            return 0
    recs = el.records
    patched = 0
    # the element's outermost nested genie copies: [k, end, current element]
    spans = []
    hits = []  # (only=) per span: a changed genie's own copy
    bag_flags = []  # (merge_only=) per span: its bags are merged
    keys = []  # per span: its genie's (library, element), lower
    k = 1
    while k < len(recs):
        po = recs[k]
        if not (po.class_flag and (po.subtype_size & 0x201) == 0x201):
            k += 1
            continue
        end, pending = k, 1
        while pending and end < len(recs):
            pending -= 1
            pending += recs[end].class_flag
            end += 1
        sub = ref = res = None
        hit = False
        try:
            ref = reference_from_object(po)
            res = resolver.resolve(ref) if ref is not None else None
            if ref is not None:
                _note(op, "probe", resolver, ref)
            if res is not None:
                # a changed genie's copy is composed in full (everything in it is its
                # current definition); a genie that only NESTS one keeps the rest as stored
                hit = only is not None and (ref.library.lower(), ref.element.lower()) in only
                sub = load_element(res.library_path, ref.element, compose=compose, only=None if hit else only)
        except NestingCycle as cyc:
            # a genie that holds a copy of itself: this copy cannot be composed (it stays as
            # stored), and the element says which cycle it met
            _met_cycle(el, cyc.chain)
            sub = None
        except RecursionError:
            raise  # (never a failed load: the caller says so)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._refresh_nested/nested load")
            sub = None
        if sub is not None:
            for chain in getattr(sub, "cycles", ()):
                _met_cycle(el, chain)
        if only is not None and not (
            sub is not None
            and ref is not None
            and ((ref.library.lower(), ref.element.lower()) in only or getattr(sub, "affected", False))
        ):
            k = end  # neither a changed genie nor one that nests it
            continue
        flag = merge_only is None or (
            ref is not None
            and (
                (ref.library.lower(), ref.element.lower()) in merge_only
                or _nests_any(sub, merge_only, resolver)
            )
        )
        if not compose and merge_only is not None and flag and sub is not None and res is not None:
            # a copy of a genie that changed (or nests one) since the element was saved:
            # when the genie's records changed in number (an object added to or removed
            # from it, or from a genie it nests), its current definition is the one
            # composed as a page takes it, so a genie between the two that was not saved
            # again since still shows the change (fxnest.n2 over a stale n1 over n0)
            try:
                sub_c = load_element(res.library_path, ref.element, compose=True, only=None if hit else only)
                if not _aligns(sub_c.records, sub.records, 0, len(sub.records)):
                    sub = sub_c
            except NestingCycle as cyc:  # (the element's own load then)
                _met_cycle(el, cyc.chain)
            except RecursionError:
                raise
            except Exception:  # noqa: BLE001 -- the element's own load then
                _note_exc("genie_place._refresh_nested/composed load")
        spans.append([k, end, sub, frozenset()])
        hits.append(
            only is not None and ref is not None and (ref.library.lower(), ref.element.lower()) in only
        )
        bag_flags.append(flag)
        keys.append((ref.library.lower(), ref.element.lower()) if ref is not None else None)
        k = end
    if not compose and merge_only is not None:
        # an element load or save that composes only the changed genies: a copy of one
        # whose records no longer line up with its current element (an object added to
        # it or removed) is composed again from it structurally, as a page composition
        # does, then refreshed like an aligned copy (else the import table takes the new
        # stamp over the old copy, and the element reads as current).
        which = {
            i
            for i, ((k0, e0, sub, _kp), b) in enumerate(zip(spans, bag_flags))
            if b and sub is not None and not _aligns(sub.records, recs, k0, e0)
        }
        if which:
            try:
                patched += _recompose_nested_copies(el, spans, resolver, which=which, element_file=True)
            except NestingCycle as cyc:  # (the stored copies stay)
                _met_cycle(el, cyc.chain)
            except RecursionError:
                raise
            except Exception:  # noqa: BLE001 -- keep the stored copies
                _note_exc("genie_place._refresh_nested/recompose")
        # the changed genies whose copy still does not line up (it could not be composed
        # again), or whose library could not be read: the element's save keeps their old
        # import-table stamps, so it stays reported as out of date
        el.stale_nested = frozenset(
            key
            for (k0, e0, sub, _kp), b, key in zip(spans, bag_flags, keys)
            if b and key is not None and (sub is None or not _aligns(sub.records, recs, k0, e0))
        )
    if compose:
        # a PAGE composition: a copy older than its element (fewer records than its
        # current element stores: stale, not an element form; CtDraw's element save keeps
        # it while the stamps are current, its page composition adds the missing records,
        # as Compose Nested Genies Again does) is re-composed from the element by the
        # AN + class key first, then refreshed like any aligned copy
        try:
            patched += _recompose_nested_copies(el, spans, resolver)
        except NestingCycle as cyc:  # (the stored copies stay)
            _met_cycle(el, cyc.chain)
        except RecursionError:
            raise
        except Exception:  # noqa: BLE001 -- keep the stored copies
            _note_exc("genie_place._refresh_nested")
    unbuilt = set()  # the changed genies whose copy's geometry failed
    for (k, end, sub, keep_geom), bags, key in zip(spans, bag_flags, keys):
        if sub is not None and _aligns(sub.records, recs, k, end):
            # each member names the element record it is merged with
            patched += _rekey_copy(recs, k, end, sub.records)
        if sub is not None and _aligns(sub.records, recs, k, end) and not bags:
            # a copy of a genie that did not change: its bags as stored, its geometry
            # derived again
            if not hasattr(el, "nested_twins"):
                el.nested_twins = {}
            for j in range(1, end - k):
                el.nested_twins.setdefault(k + j, sub.records[j])
            try:
                moved = _refresh_nested_geometry(
                    el,
                    k,
                    end,
                    sub,
                    resolver,
                    {},
                    keep=keep_geom,
                    matched=getattr(el, "recomposed_spans", {}).get(k),
                )
            except _Unbuilt:
                moved = {}
            # (only a DummyMarker's box: CtDraw's load + save keeps the stored geometry of
            # the quarter-turned members of an unchanged copy, LIVE)
            moved = {j: b for j, b in moved.items() if recs[k + j].type_code == 0xFFFF}
            patched += len(moved)
            for j in sorted(moved):
                recs[k + j] = to_po(moved[j])
            continue
        if sub is not None and _aligns(sub.records, recs, k, end):
            if not hasattr(el, "nested_twins"):
                el.nested_twins = {}
            # each member is decoded once for both steps: its bags merged here,
            # its geometry grafted below; a changed record is parsed once, at
            # the end (a Rec encodes to the bytes decode reads it back from, so
            # this is what re-decoding the merged record gave)
            decs: dict = {}
            merged: dict = {}
            r0 = _decoded(sub, 0)
            own_level = r0.entries is not None and len(r0.entries) >= 2
            # a copy in the PAGE form (a template, a super genie page and an element without
            # parameters keep it): its root has as many levels as the nested element's root
            # (the placed level in place of the element's own), so has each member; in the
            # element form one more (the host element's level after the binding level). A
            # genie element keeps every copy in the element form (convert_page_form brought
            # a copy pasted from a page to it): a copy of a genie that had no level when it
            # was placed (CtDraw's paste gives each record the host's level only) and has one
            # now is not in the page form for having as many levels as its genie's root
            # (LIVE, the fixture oracle's chain4/param_gained: the copy took one more level)
            host_ef = _copies_in_element_form(el)
            # a record with fewer levels than its element record and the copy's own ones
            # (``extra``: 1 in the element form, 0 in the page form) is padded; the missing
            # levels go before the copy's own last ``after`` levels (its host element's
            # level, when the copy has one)
            extra = after = None
            try:
                ra = decode(recs[k])
                if ra.entries is not None and r0.entries is not None:
                    if host_ef:
                        extra = 1
                        # (a copy placed with its bindings but without the host's level: placed
                        # in the page form before the element had parameters of its own. Its
                        # last level is the binding; the host's level goes after it)
                        top = ra.entries[-1]
                        bound_top = 2 <= len(ra.entries) <= len(r0.entries) and bool(
                            top[1].pairs or top[2].pairs
                        )
                        after = 0 if bound_top else 1
                    elif own_level and len(ra.entries) <= len(r0.entries):
                        extra = after = 0
                if extra is not None and _pad_levels(ra, len(r0.entries) + extra, after):
                    # (a root placed before its genie had this many levels)
                    decs[0] = ra
                    merged[0] = encode(ra)
                    patched += 1
                page_form = (
                    not host_ef
                    and own_level
                    and ra.entries is not None
                    and len(ra.entries) == len(r0.entries)
                )
            except Exception:  # noqa: BLE001 -- the element form's rule then
                _note_exc("genie_place._refresh_nested/copy form")
                page_form = False
            # the records a page composition just gained (CtDraw's clones of their element
            # records: their levels are those records' own, LIVE) keep their levels
            kept = getattr(el, "recomposed_spans", {}).get(k) if compose else None
            try:
                a0 = decs[0] if 0 in decs else decode(recs[k])
                if a0.entries is not None and len(a0.entries) >= 2 and own_level:
                    before = encode(a0)
                    _own_level_defaults(a0.entries[1], declared_params(sub), templates=not page_form)
                    if encode(a0) != before:
                        decs[0] = a0
                        merged[0] = encode(a0)
                        patched += 1
            except Exception:  # noqa: BLE001 -- the stored level then
                _note_exc("genie_place._refresh_nested/root level")
            if not compose:
                # an element load: the nested root's own value bag holds only the keys
                # its template bag holds (as in the corpus libraries; LIVE, CtDraw's save
                # of a genie drops a nested root's '!*' sheet keys that have no template)
                a0 = decs[0] if 0 in decs else decode(recs[k])
                if a0.entries is not None and len(a0.entries) >= 2:
                    before = encode(a0)
                    _values_within_templates(a0.entries[0])
                    decs[0] = a0
                    blob = encode(a0)
                    if blob != before:
                        merged[0] = blob
                        patched += 1
            # the copy root's binding level (the host's binding of the nested genie), which
            # a member that starts using a parameter takes its binding from
            try:
                r_root = decs[0] if 0 in decs else decode(recs[k])
                root_level = (
                    r_root.entries[1] if r_root.entries is not None and len(r_root.entries) >= 2 else None
                )
            except Exception:  # noqa: BLE001 -- no binding to take then
                _note_exc("genie_place._refresh_nested/root binding")
                root_level = None
            blobs: dict = {}  # j -> encode(decs[j]) as the merge left it
            for j in range(1, end - k):
                el.nested_twins.setdefault(k + j, sub.records[j])
                a, c = decode(recs[k + j]), _decoded(sub, j)
                decs[j] = a
                if a.entries is None or c.entries is None:
                    continue
                before = None
                if extra is not None and (kept is None or j in kept):
                    # a member with fewer levels than its element record and the copy's own:
                    # the levels of the genies between are missing (a record a host saved
                    # while the genie between was out of date took the clone's levels; a
                    # copy placed before its genie had a level). Empty ones go in their
                    # place, and the merge below binds them as it binds the kept members.
                    # LIVE, the fixture oracle's chain5/stale_host: CtDraw's save of n2
                    # once n1 was saved gave the record its four levels, bound; and
                    # chain4/param_gained: n1's copy of n0's copy of a genie that gained a
                    # level took one more
                    before = encode(a)
                    _pad_levels(a, len(c.entries) + extra, after)
                before = before if before is not None else encode(a)
                a.entries[0][1] = _merge_bag(
                    a.entries[0][1], memory_values(c.entries), compose, sheet_values=True
                )
                a.entries[0][2] = _merge_bag(a.entries[0][2], c.entries[0][2], compose, sheet_values=True)
                # the nested genie's own level: the sub element's last one, when the
                # sub element has a level (a parameterless sub element that keeps its
                # nested copies in the page form has none: every level of its records
                # above e0 is an inner genie's, LIVE, a tab genie)
                top = len(c.entries) - 1 if own_level else len(c.entries)
                if top >= 2 and len(a.entries) == len(c.entries) + (0 if page_form else 1):
                    # a member of a genie nested INSIDE the nested genie: the levels
                    # below the nested genie's own are its element's too -- the inner
                    # genies' binding templates and declarations come from the CURRENT
                    # element (LIVE, a page as CtDraw composes it: the
                    # instrument TAG template of a large genie, its nested
                    # element's own prefix, where the row genie's stored
                    # copies had the filter's prefix); the nested genie's own
                    # bindings (level ``top``: values, templates) are the outer
                    # element's, its declarations the current ones
                    # An element load does the same (LIVE, CtDraw's save of a
                    # genie: the members of a tab genie nested in a nested genie took a
                    # PageFilter binding the middle element's copy holds, appended after
                    # their own). A level of the middle element still in the page form
                    # (values, no templates) gives its values as the templates.
                    # A copy in the PAGE form (its placed level at ``top``) takes them as a
                    # page does (EditableDocument._merge_instance, LIVE): each
                    # stored key stays in place, the element's or not, with the element's
                    # value, and its new keys after them; a templated value stays as
                    # stored, the levels are derived again from the placed one
                    # (genie_norm.normalise_records), so a copy whose bindings did not
                    # change is kept as stored.
                    for lvl in range(1, top):
                        ea, ec = a.entries[lvl], c.entries[lvl]
                        ea[0] = Bag(list(ec[0].pairs), ea[0].ov)
                        if page_form:
                            ea[2] = _merge_bag(ea[2], ec[2], templated=bag_keys(ea[2]))
                            tk = bag_keys(ea[2])
                            cur = Bag([(kk, v) for kk, v in ec[1].pairs if kk.lower() not in tk])
                            ea[1] = _merge_bag(ea[1], cur, templated=bag_keys(ea[1]))
                            continue
                        ea[2] = _merge_bag(ea[2], top_level_templates(ec), True)
                        ea[1] = _merge_bag(ea[1], ec[1], True, bag_keys(ea[2]))
                    if own_level:
                        a.entries[top][0] = Bag(list(c.entries[top][0].pairs), a.entries[top][0].ov)
                        _bind_declared(a.entries, top, root_level, page_form)
                        _level_defaults(a.entries[top], templates=not page_form)
                elif len(a.entries) >= 2 and len(c.entries) >= 2:
                    a.entries[1][0] = Bag(list(c.entries[1][0].pairs), a.entries[1][0].ov)
                    if own_level and len(a.entries) == len(c.entries) + (0 if page_form else 1):
                        _bind_declared(a.entries, 1, root_level, page_form)
                    _level_defaults(
                        a.entries[1],
                        templates=not page_form and own_level and len(a.entries) == len(c.entries) + 1,
                    )
                # (a MEMBER keeps the '!*' sheet keys of its value bag that its templates
                # lack: LIVE, CtDraw's load + save of a table genie whose nested row genie
                # had changed kept them; only the nested ROOT's value bag is cut to its
                # templates)
                # a stored METADATA bit and section whose values the merge took away go
                # with them (kept, the Graphics Builder refuses the host); every other
                # stored section is kept as stored
                drop_stale_metadata(a)
                blob = encode(a)
                blobs[j] = blob  # (the geometry memo's key takes it)
                if blob != before:
                    merged[j] = blob
                    patched += 1
            try:
                moved = _refresh_nested_geometry(
                    el,
                    k,
                    end,
                    sub,
                    resolver,
                    decs,
                    keep=keep_geom,
                    matched=getattr(el, "recomposed_spans", {}).get(k),
                    dec_blobs=blobs,
                )
            except _Unbuilt:
                # (its bags are merged; its geometry stays as stored, so its import-table
                # stamp does too: the element stays out of date for this genie)
                moved = {}
                if key is not None:
                    unbuilt.add(key)
            # a button's WIDTH / HEIGHT are its box, whatever the current element says
            for j in range(1, end - k):
                if recs[k + j].type_code != BUTTON_TC:
                    continue
                blob = moved.get(j, merged.get(j))
                rb = decode(to_po(blob)) if blob is not None else decode(recs[k + j])
                if _button_box_size(rb):
                    moved[j] = encode(rb)
            patched += len(moved)
            for j in sorted(set(merged) | set(moved)):
                recs[k + j] = to_po(moved[j] if j in moved else merged[j])
    if unbuilt and not compose and merge_only is not None:
        # a changed genie whose copy lines up but whose geometry could not be derived
        # again keeps its old stamp too
        el.stale_nested = frozenset(getattr(el, "stale_nested", frozenset()) | unbuilt)
    if only is not None:
        # the copies of the changed genies themselves, at any depth (record indexes of
        # this element): a copy of a genie that only NESTS one keeps its other records as
        # stored here -- older than what a page composed from their current definitions
        # holds --, so a caller replaces just these
        inner = []
        for (k, end, sub, _keep), hit in zip(spans, hits):
            if hit or sub is None:
                inner.append((k, end))
            elif end - k == len(sub.records):
                inner += [(k + a, k + b) for a, b in getattr(sub, "affected_spans", ())]
            else:
                inner.append((k, end))
        el.affected = bool(spans)
        el.affected_spans = inner
    return patched


def shared_decode(el: "Element", j: int) -> Rec:
    """:func:`_decoded` for other modules: the decode of ``el.records[j]``, SHARED with
    every reader of the memoised element (read it, never change it)."""
    return _decoded(el, j)


def _decoded(el: "Element", j: int) -> Rec:
    """decode(el.records[j]) -- once per record of a memoised element (its
    nested instances decode the same element records again and again). The Rec
    is SHARED: read it, never change it."""
    canon = getattr(el, "__dict__", {}).get("_gp_canon")
    po = el.records[j]
    if canon is None:
        return decode(po)
    memo = canon.__dict__.setdefault("_gp_dec", {})
    hit = memo.get(j)
    if hit is not None and hit[0] is po:
        return hit[1]
    r = decode(po)
    memo[j] = (po, r)
    return r


class _Unbuilt(Exception):
    """:func:`_nested_geometry` could not build the nested element the way the copy places
    it (its reference build), so the copy keeps its stored geometry: an element save keeps
    the copy's old import-table stamp (``stale_nested``), as for a copy that could not be
    composed again. The answer is never memoised."""


#: _refresh_nested_geometry's answers, kept like the element loads (_MEMO): by what the call
#: reads (the copy's records and its ancestors', the members' merged decodes, the nested
#: element's records and fields, keep, matched, the search path) and validated by the same
#: dependencies (the libraries, resolver walks and tag-format answers its reference build
#: read). Every save of an element re-derives the geometry of every nested copy.
_GEOM_MEMO: "OrderedDict[tuple, tuple]" = OrderedDict()
GEOM_MEMO_LIMIT = 512


def _hash_part(h, x) -> None:
    """``x`` into the hash ``h``, unambiguously (bytes by length, the rest by repr)."""
    if isinstance(x, (bytes, bytearray, memoryview)):
        h.update(b"b%d:" % len(x))
        h.update(x)
    else:
        r = repr(x).encode("utf-8", "surrogatepass")
        h.update(b"r%d:" % len(r))
        h.update(r)


def _rec_parts(parts: list, po) -> None:
    """What a record's payload is made of (writer.object_payload), appended to ``parts`` (the
    pieces of one hash update), unambiguously: the numbers as text, the strings and bytes
    after their lengths, for one update of the joined pieces."""
    an = po.an_str.encode("utf-8", "surrogatepass")
    body = po.body_raw
    raw = po.raw_payload
    parts.append(b"%d,%d,%d,%d:" % (po.class_flag, po.type_code, po.subtype_size, len(an)))
    parts.append(an)
    parts.append(b"%d:" % len(body))
    parts.append(body)
    if raw is None:
        parts.append(b"-")
    else:
        parts.append(b"%d:" % len(raw))
        parts.append(raw)


def _same_objects(a, b) -> bool:
    """``a`` and ``b`` (sequences) hold the same objects, in order (by identity)."""
    return len(a) == len(b) and all(map(operator.is_, a, b))


def _sub_geometry_key(op, sub) -> bytes:
    """Cache key for _refresh_nested_geometry over the nested element ``sub``."""
    import hashlib

    twins = getattr(sub, "__dict__", {}).get("nested_twins") or {}
    meta = (
        repr(
            (
                sub.lib_path,
                sub.library,
                sub.element,
                sub.ext,
                tuple(sub.e),
                tuple(sub.N),
                sub.compose,
                len(sub.records),
                sorted(twins),
            )
        ).encode("utf-8", "surrogatepass")
        + b"|"
        + bytes(sub.identity)
    )
    tw = tuple(twins[j] for j in sorted(twins))
    cache = getattr(op, "sub_keys", None)
    got = cache.get(id(sub)) if cache is not None else None
    if (
        got is not None
        and got[0] is sub
        and got[1] == meta
        and _same_objects(got[2], sub.records)
        and _same_objects(got[3], tw)
    ):
        return got[4]
    parts = [b"%d:" % len(meta), meta]
    for r in sub.records:
        _rec_parts(parts, r)
    parts.append(b"twins")
    for r in tw:
        _rec_parts(parts, r)
    digest = hashlib.sha1(b"".join(parts)).digest()
    if cache is not None:
        # (the records and twins are held: no other object takes their ids meanwhile)
        cache[id(sub)] = (sub, meta, tuple(sub.records), tw, digest)
    return digest


def _refresh_nested_geometry(
    el: "Element",
    k: int,
    end: int,
    sub: "Element",
    resolver,
    decs: Optional[dict] = None,
    keep=frozenset(),
    matched=None,
    dec_blobs: Optional[dict] = None,
) -> dict:
    """:func:`_nested_geometry` memoised (_GEOM_MEMO). On a reuse ``decs`` are left as they
    were (the callers read only the payloads returned). ``dec_blobs``: {j: encode(decs[j])}
    that the caller made already (the key encodes the others)."""
    op = _op_enter()
    try:
        paths = getattr(resolver, "search_paths", None)
        if op is None or not _memo_on() or sub is None or paths is None:
            return _nested_geometry(el, k, end, sub, resolver, decs, keep, matched)
        import hashlib

        recs = el.records
        parents = P.forest_parents(recs)
        # the key: a SHA-1 of everything the call reads
        h = hashlib.sha1(b"geometry")
        parts = [b"%d;" % (end - k)]
        for r in recs[k:end]:
            _rec_parts(parts, r)
        q = parents[k]
        while q is not None:
            parts.append(b"anc")
            _rec_parts(parts, recs[q])
            q = parents[q]
        blobs = dec_blobs or {}
        for j, r in sorted((decs or {}).items()):
            b = blobs.get(j)
            if b is None:
                b = encode(r)
            parts.append(b"%d,%d:" % (j, len(b)))
            parts.append(b)
        h.update(b"".join(parts))
        if isinstance(matched, dict):
            mk = ("d", tuple(sorted(matched.items())))
        elif matched:
            mk = ("s", tuple(sorted(matched)))
        else:
            mk = matched
        h.update(_sub_geometry_key(op, sub))
        _hash_part(h, (tuple(sorted(keep or ())), repr(mk), tuple(paths)))
        key = h.digest()
        ent = _GEOM_MEMO.get(key)
        if ent is not None:
            out, deps = ent
            if _valid(op, deps, resolver):
                _GEOM_MEMO.move_to_end(key)
                _merge_deps(op, deps, resolver, False)
                return dict(out)
            _GEOM_MEMO.pop(key, None)
        f = _frame_push(op, None, resolver)
        try:
            out = _nested_geometry(el, k, end, sub, resolver, decs, keep, matched)
        finally:
            _frame_pop(op, f)
        if not f.tainted:
            _GEOM_MEMO[key] = (dict(out), f.deps)
            while len(_GEOM_MEMO) > GEOM_MEMO_LIMIT:
                _GEOM_MEMO.popitem(last=False)
        return out
    finally:
        _op_leave(op)


def _nested_geometry(
    el: "Element",
    k: int,
    end: int,
    sub: "Element",
    resolver,
    decs: Optional[dict] = None,
    keep=frozenset(),
    matched=None,
) -> dict:
    """Re-derive a recomposed nested instance's geometry from the current element."""
    recs = el.records
    parents = P.forest_parents(recs)
    q = parents[k]
    while q is not None:
        s3 = P.read_slots(recs[q].body_raw)[0][2]
        if s3[0] and tuple(s3[1]) != P.IDENT:
            return {}
        q = parents[q]
    try:
        rebuilt = [_shell_po(encode(x)) for x in _nested_build(recs, k, end, sub, resolver)]
    except Exception as exc:  # noqa: BLE001 -- the stored geometry stays; the caller says so
        _note_exc("genie_place._refresh_nested_geometry")
        raise _Unbuilt(str(exc)) from exc
    # each record decoded once; a stored member's decode is changed only by the last loop
    # that reads it
    decs = _DecodeOnce(recs, k, decs)
    rebuilt = _DecodeOnce(rebuilt, 0)
    # a member with a Local of its own (slot 2 mode not 0) where the CURRENT element's
    # member has none keeps it, its Total and geometry following (LIVE,
    # CtDraw's load + save of a genie: a nested alarm genie's state set kept
    # slot 2 T(2, 0) where the changed element has none, as on a page); every other
    # member takes the current element's slots. A member whose element record has a Local
    # of its own too (a genie or group nested in the nested genie) takes the element's:
    # the user moved it in the genie's definition (the copy's old Local would leave it
    # where it was in every host and page). Nor does a member whose
    # Local the definition placed (_local_of_definition: ctgedit's Cut Link of a symbol
    # in the genie; the copy would show it at twice the offset). The same rule
    # for a copy re-composed structurally (its matched members), so that composing it
    # again changes nothing
    own = {j for j in range(1, end - k) if _own_local(decs[j], rebuilt[j], sub.records[j])}
    if matched:
        own &= set(matched)  # (a gained record takes the rebuilt slots)
    if matched or own:
        return _restructured_geometry(recs, k, end, rebuilt, decs, keep, own, sub.records)
    out = {}
    for j in range(end - k):
        if j in keep:
            continue
        a, b = decs[j], rebuilt[j]
        # the slots too: a member's placement in the nested copy is the CURRENT element's
        # (LIVE, CtDraw's save of a genie: the records of nested copies took
        # the scaled slot 2 / slot 3 their current tab element gives them)
        same_slots = a.slots == b.slots
        if (a.anchor, a.rect1, a.rect2) == (b.anchor, b.rect1, b.rect2) and same_slots:
            continue
        a.anchor, a.rect1, a.rect2 = b.anchor, b.rect1, b.rect2
        if not same_slots:
            a.slots = list(b.slots)
        out[j] = encode(a)
    return out


def _own_local(a: Rec, b: Rec, el_po: PageObject) -> bool:
    """The stored copy member ``a`` keeps its Local (slot 2): it has one, the member built
    from the current element (``b``) has none, and the definition did not place it
    (:func:`_local_of_definition`; ``el_po`` = the member's current element record)."""
    return bool(_slot(a, 1)[0]) and not _slot(b, 1)[0] and not _local_of_definition(a, el_po)


def _local_of_definition(a: Rec, el_po: PageObject) -> bool:
    """Did the copy member's Local come from a definition that no longer has it?"""
    if a.dw & 0x200 or el_po.subtype_size & 0x200:
        return False  # a genie root: its Local is never baked
    if el_po.subtype_size & 0x1:
        return True
    if a.tc == TEXT_TC and el_po.type_code == TEXT_TC:
        m, s = _slot(a, 1)
        if m and any(abs(v - w) > 1e-9 for v, w in zip(s[:4], (1.0, 0.0, 0.0, 1.0))):
            return True
    return a.tc == GROUP_TC and el_po.type_code == GROUP_TC


class _DecodeOnce:
    """The decodes of ``records[base + j]`` by j, each made once (``given`` = Recs some j
    already have: those are used)."""

    __slots__ = ("records", "base", "got")

    def __init__(self, records, base: int, given=None):
        self.records, self.base = records, base
        self.got = dict(given) if given else {}

    def __getitem__(self, j: int) -> Rec:
        r = self.got.get(j)
        if r is None:
            r = self.got[j] = decode(self.records[self.base + j])
        return r


def _restructured_geometry(recs, k: int, end: int, rebuilt, decs, keep, own, sub_recs) -> dict:
    """Geometry for a structurally recomposed copy, or one whose members keep their own Local."""
    par = P.forest_parents(recs[k:end])
    new_slots: Dict[int, list] = {}
    links: Dict[int, tuple] = {}
    root_link = P.link_of(rebuilt.records[rebuilt.base])
    out = {}
    final: Dict[int, Rec] = {}
    for j in range(end - k):
        a, b = decs[j], rebuilt[j]
        if j == 0:
            slots = list(b.slots)
        else:
            src = a if j in own else b
            m2, s2 = _slot(src, 1)
            chain = []
            q = par[j]
            while q is not None:
                if q == 0:
                    chain.append(root_link)
                else:
                    if q not in links:
                        links[q] = _member_link(sub_recs[q], new_slots[q][1])
                    chain.append(links[q])
                q = par[q]
            s3, m3 = P.apply_parent(s2, m2, chain)
            slots = [_slot(src, 0), (m2, s2), (m3, tuple(s3))]
        new_slots[j] = slots
        bm, bt = _slot(b, 2)
        D = P.affine_mul(slots[2][1] if slots[2][0] else P.IDENT, P._affine_inv(bt if bm else P.IDENT))
        anchor, rect1, rect2 = b.anchor, b.rect1, b.rect2
        if any(abs(x - y) > 1e-9 for x, y in zip(D, P.IDENT)):
            ax = D[0] * anchor[0] + D[2] * anchor[1] + D[4]
            ay = D[1] * anchor[0] + D[3] * anchor[1] + D[5]
            anchor = (int(round(ax)), int(round(ay)))
            rect1, rect2 = _trunc_box_round(D, rect1), _trunc_box_round(D, rect2)
        if j in keep:
            anchor, rect1, rect2 = a.anchor, a.rect1, a.rect2
        if (a.anchor, a.rect1, a.rect2) == (anchor, rect1, rect2) and a.slots == slots:
            final[j] = a
            continue
        a.anchor, a.rect1, a.rect2 = anchor, rect1, rect2
        a.slots = slots
        out[j] = encode(a)
        final[j] = a
    # a group of the copy (its root too) bounds its members as they are now (LIVE:
    # a nested alarm genie whose state set kept its own Local T(2, 0) is two
    # pixels wider in CtDraw's save, its root and the group above it with it), by the
    # union rule _build_recs gives the rebuilt groups (geometry.group_union: a DummyMarker
    # in the Extent, a zero-size child out of the Bounds, input records out)
    for j in range(end - k - 1, -1, -1):
        r = final.get(j)
        if r is None or r.tc != GROUP_TC or not r.cf:
            continue
        kids = [final[c] for c in range(j + 1, end - k) if par[c] == j and c in final]
        r1, r2 = G.group_union([(x.tc, x.rect1, x.rect2) for x in kids])
        if r1 is None or r2 is None or (tuple(r.rect1), tuple(r.rect2)) == (r1, r2):
            continue
        r.rect1, r.rect2, r.anchor = r1, r2, (r1[0], r1[3] - 1)
        out[j] = encode(r)
    return out


def _trunc_box_round(M, rect):
    """``rect`` through an affine ``M`` that is an integer move in practice (a kept
    slot 2 translation), rounded, as a box."""
    a, b, c, d, h, k = M
    pts = [
        (int(round(a * x + c * y + h)), int(round(b * x + d * y + k)))
        for x, y in ((rect[0], rect[1]), (rect[2], rect[3]))
    ]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def _nested_build(
    recs,
    k: int,
    end: int,
    sub: "Element",
    resolver,
    handles: Optional[Sequence[int]] = None,
    element_form: bool = False,
) -> List[Rec]:
    """Compose the nested element ``sub`` the way the stored copy places it."""
    (m1, L, _o, _n), _s2, _s3 = P.read_slots(recs[k].body_raw)[0]
    r = decode(recs[k])
    params = OrderedDict()
    if r.entries and len(r.entries) >= 2:
        own = r.entries[1]
        src = own[2].pairs if own[2].pairs else own[1].pairs
        params = OrderedDict((kk, v) for kk, v in src if kk.lower() != "an")
    if handles is None:
        sp = P.forest_parents(sub.records)
        handles = [
            G.get_handle(recs[k + j])
            for j in range(end - k)
            if not (sub.records[j].type_code in INPUT_TCS and sp[j] is not None)
        ]
    local = (L[4], L[5]) if m1 == 1 else (m1, tuple(L))
    return _build_recs(
        sub,
        local,
        params,
        handles=handles,
        resolver=resolver,
        opts=_ELEMENT_FORM_OPTS if element_form else _BUILD_OPTS,
    )


def _copy_extra_levels(recs, k: int, sub: "Element", element_form: bool = False) -> int:
    """How many levels each record of the nested copy carries beyond its element record."""
    try:
        a, c = decode(recs[k]), _decoded(sub, 0)
    except Exception:  # noqa: BLE001  # swallow-ok: an undecodable root has no levels
        return 1 if element_form else 0
    got = max(0, len(a.entries or ()) - len(c.entries or ()))
    return max(got, 1) if element_form else got


def ctdraw_instance_handles(
    inst: Sequence[PageObject], el_records: Sequence[PageObject], outside: set
) -> List[int]:
    """Per element record (DFS), the handle CtDraw's page load gives it when it composes the
    placed genie ``inst`` alone (:func:`page_instance_handles` with one instance).
    ``outside`` = the handles of the page's other records (after the template)."""
    return page_instance_handles([(inst, el_records)], outside)[0]


def link_instance(inst: Sequence[PageObject], el_records: Sequence[PageObject]) -> List[Optional[int]]:
    """For each element record, the stored instance record CtDraw's load links it with."""
    n = len(el_records)
    out: List[Optional[int]] = [None] * n
    if not n or not inst:
        return out
    by_key: Dict[tuple, List[int]] = {}
    for s_ in range(1, len(inst)):
        by_key.setdefault((inst[s_].an_str, inst[s_].type_code), []).append(s_)
    out[0] = 0
    taken = {0}
    for k in range(1, n):
        q = by_key.get((str(G.get_handle(el_records[k])), el_records[k].type_code))
        while q and q[0] in taken:
            q.pop(0)
        if q:
            out[k] = q.pop(0)
            taken.add(out[k])
    return out


def page_instance_handles(
    cases: Sequence[Tuple[Sequence[PageObject], Sequence[PageObject]]],
    outside: set,
    claim: Optional[Sequence[bool]] = None,
) -> List[List[int]]:
    """The handles CtDraw's page load gives the genie instances it recomposes."""
    used = set(outside)
    used.discard(None)
    links = [
        link_instance(inst, el) if claim is None or claim[c] else [None] * len(el)
        for c, (inst, el) in enumerate(cases)
    ]
    got: List[List[Optional[int]]] = []
    for (inst, el), m in zip(cases, links):
        row: List[Optional[int]] = [None] * len(el)
        for j, s_ in enumerate(m):
            if s_ is not None:
                row[j] = G.get_handle(inst[s_])
                used.add(row[j])
        got.append(row)
    pars = [P.forest_parents(el) for _inst, el in cases]

    def is_input(c: int, j: int) -> bool:
        return cases[c][1][j].type_code in INPUT_TCS and pars[c][j] is not None

    for c, (_inst, el) in enumerate(cases):
        if claim is not None and not claim[c]:
            continue
        for j in range(len(el)):
            if got[c][j] is None and not is_input(c, j):
                h = G.get_handle(el[j])
                if h is not None and h not in used:
                    got[c][j] = h
                    used.add(h)
    next_free = handle_allocator(used)
    for c, (_inst, el) in enumerate(cases):
        for j in range(len(el)):
            if got[c][j] is None and not is_input(c, j):
                got[c][j] = next_free()
    for c, (_inst, el) in enumerate(cases):
        for j in range(len(el)):
            if is_input(c, j):
                got[c][j] = got[c][pars[c][j]]
    return [[h if h is not None else 0 for h in row] for row in got]


def _slot(r: Rec, i: int):
    s = r.slots[i] if r.slots is not None and len(r.slots) > i else None
    return (0, P.IDENT) if s is None else (s[0], tuple(s[1]))


def _recompose_nested_copies(
    el: "Element", spans: list, resolver, which=None, element_file: bool = False
) -> int:
    """Recompose every nested genie copy that no longer lines up with its element."""
    recs = el.records
    plans = []
    for i, (k, end, sub, _keep) in enumerate(spans):
        if which is not None and i not in which:
            continue
        if sub is None or _aligns(sub.records, recs, k, end) or not sub.records:
            continue
        stale = recs[k:end]
        m = match_instance(stale, sub.records)
        # (a copy where only the root matches, every member of the genie redrawn, is
        # built in full from the current element below, with the copy's bindings)
        plans.append((i, m))
    if not plans:
        return 0
    host_ef = _copies_in_element_form(el)
    # -- handles, element-wide ---------------------------------------------------
    dropped = set()
    for i, m in plans:
        k, end = spans[i][0], spans[i][1]
        taken = {s for s in m if s is not None}
        dropped |= {k + s for s in range(end - k) if s not in taken}
    used = {G.get_handle(po) for i, po in enumerate(recs) if i not in dropped}
    used.discard(None)
    want = []  # (plan, j) of the gained non-input records
    for pi, (i, m) in enumerate(plans):
        subr = spans[i][2].records
        subp = P.forest_parents(subr)
        for j in range(len(subr)):
            if m[j] is None and not (subr[j].type_code in INPUT_TCS and subp[j] is not None):
                want.append((pi, j))
    got: Dict[tuple, int] = {}
    for pi, j in want:
        tw = G.get_handle(spans[plans[pi][0]][2].records[j])
        if tw is not None and tw not in used:
            got[(pi, j)] = tw
            used.add(tw)
    next_free = handle_allocator(used)
    for pi, j in want:
        if (pi, j) not in got:
            got[(pi, j)] = next_free()
    # -- the new copies (built first: the element changes only when all were) ----
    built = {}
    for pi, (i, m) in enumerate(plans):
        k, end, sub, _keep = spans[i]
        built[i] = _build_nested_copy(
            recs,
            k,
            end,
            sub,
            m,
            {j: h for (p, j), h in got.items() if p == pi},
            resolver,
            full=sum(1 for s in m if s is not None) <= 1,
            element_file=element_file,
            element_form=host_ef,
        )
    added = 0
    shift = 0
    for i, span in enumerate(spans):
        k, end, sub, keep = span
        k, end = k + shift, end + shift
        if i in built:
            new, dms = built[i]
            recs[k:end] = new
            shift += len(new) - (end - k)
            added += max(0, len(new) - (end - k))
            span[:] = [k, k + len(new), sub, dms]
            m = next(mm for ii, mm in plans if ii == i)
            if not hasattr(el, "recomposed_spans"):
                el.recomposed_spans = {}
            el.recomposed_spans[k] = frozenset(j for j, s_ in enumerate(m) if s_ is not None)
        else:
            span[:] = [k, end, sub, keep]
    return added


def _build_nested_copy(
    recs,
    k: int,
    end: int,
    sub: "Element",
    m: List[Optional[int]],
    handles: Dict[int, int],
    resolver,
    full: bool = False,
    element_file: bool = False,
    element_form: bool = False,
):
    """Recompose a nested copy from ``sub``."""
    stale = recs[k:end]
    subr = sub.records
    subp = P.forest_parents(subr)
    new_h: List[Optional[int]] = [None] * len(subr)
    for j in range(len(subr)):
        if m[j] is not None:
            new_h[j] = G.get_handle(stale[m[j]])
        elif subr[j].type_code in INPUT_TCS and subp[j] is not None:
            new_h[j] = new_h[subp[j]]  # input records share their owner's
        else:
            new_h[j] = handles[j]
    hs = [new_h[j] for j in range(len(subr)) if not (subr[j].type_code in INPUT_TCS and subp[j] is not None)]
    regrown = {
        j for j in range(len(subr)) if m[j] is not None and stale[m[j]].class_flag != subr[j].class_flag
    }
    gained = [j for j in range(len(subr)) if m[j] is None]
    bind = full or element_file
    extra = _copy_extra_levels(recs, k, sub, element_form) if bind else 0
    r0s = _decoded(sub, 0)
    own_level = r0s.entries is not None and len(r0s.entries) >= 2
    fresh = (
        _nested_build(recs, k, end, sub, resolver, handles=hs, element_form=extra >= 1)
        if (gained or regrown)
        else None
    )
    out: List[PageObject] = []
    decs: Dict[int, Rec] = {}
    dms = set()
    for j in range(len(subr)):
        se = _decoded(sub, j).entries
        if m[j] is not None:
            po = stale[m[j]]
            if j in regrown:
                # its children follow the element, and so does the class section that
                # counts them -- with the METADATA bit of that section (dwFlags 0x100 says
                # the class section starts with one): the stored bit over the element's
                # section without metadata makes the Graphics Builder refuse the host
                r = decode(po)
                r.cf = subr[j].class_flag
                r.cls = fresh[j].cls
                r.dw = (r.dw & ~0x100) | (fresh[j].dw & 0x100)
                po = to_po(encode(r))
            out.append(po)
            continue
        r = _copy.copy(fresh[j])
        bound = len(se) - 1 if se else 0
        if r.entries is not None and bound >= 1 and len(r.entries) > bound and not bind and own_level:
            # a record the nested genie gained is CtDraw's clone of its element record: the
            # copy's binding level (where the nested element has its own level) stays
            # unbound (LIVE, CtDraw's load of a page: the gained members of
            # a nested alarm genie held no Equipment / ItemName; their command read '. = .').
            # In a parameterless nested element the last level is an inner genie's, bound
            # by that element: the clone keeps it
            r.entries = [list(lv) for lv in r.entries]
            lv = r.entries[bound]
            r.entries[bound] = [lv[0], Bag([], lv[1].ov), Bag([], lv[2].ov)]
        if bind and se is not None:
            _fit_levels(r, len(se) + extra, file_form=extra >= 1)
        p = subp[j]
        pr = decs.get(p) if p in decs else decode(out[p])
        decs[p] = pr
        pm, pt = _slot(pr, 2)
        m2, s2 = _slot(r, 1)
        s3, m3 = P.compose_after(s2, m2, pt, pm) if pm else (s2, m2)
        r.slots = [(0, P.IDENT), (m2, s2), (m3, s3)]
        if r.tc == 0xFFFF:
            # a new DummyMarker sits at its Total's image of its slot-2 point, 1 x 1
            A, B, C, D, H, K = s3 if m3 else P.IDENT
            x0, y0 = (s2[4], s2[5]) if m2 else (0.0, 0.0)
            ax, ay = int(A * x0 + C * y0 + H), int(B * x0 + D * y0 + K)
            w = 0 if (not m2 and pr.tc != SYMSET_TC) else 1
            # (a free marker without a slot-2 point is the empty rect: build_instance's
            # rule, LIVE FxParts)
            r.anchor, r.rect1, r.rect2 = (ax, ay), (ax, ay, ax + w, ay + w), (ax, ay, ax + w, ay + w)
            dms.add(j)
        else:
            # the element record's geometry through (the parent's Total here) o
            # (its Total there)^-1
            t0m, t0 = _slot(_decoded(sub, p), 2)
            mp = _mapper(t0 if t0m else P.IDENT, pt if pm else P.IDENT)
            er = _decoded(sub, j)
            r.anchor = mp(*er.anchor)
            r.rect1 = _map_rect(mp, er.rect1)
            r.rect2 = tuple(r.rect1[i] + (er.rect2[i] - er.rect1[i]) for i in range(4))
        decs[j] = r
        out.append(to_po(encode(r)))
    return out, frozenset(dms)


def _pad_levels(r: Rec, count: int, after: int) -> bool:
    """``r`` with at least ``count`` levels: empty ones inserted before its last ``after``
    levels (the copy's own: the host element's level in the element form, none in the page
    form), never before e0. Returns whether it changed."""
    if r.entries is None or len(r.entries) >= count:
        return False
    at = max(1, len(r.entries) - after)
    r.entries = (
        list(r.entries[:at])
        + [[Bag([], 0), Bag([], 0), Bag([], 0)] for _ in range(count - len(r.entries))]
        + list(r.entries[at:])
    )
    return True


def _copies_in_element_form(el) -> bool:
    """``el`` keeps its nested genie copies in the ELEMENT form: a genie element (a .ctm
    element, not a super genie page, that declares parameters: convert_page_form's rule).
    ``el.element_form_copies`` says it when the caller knows (_load_element,
    genie_norm.normalise_records)."""
    v = getattr(el, "element_form_copies", None)
    if v is not None:
        return bool(v)
    if (el.ext or "").lower() != "ctm" or "@super" in (el.element or "").lower():
        return False
    try:
        return declares_parameters(el.records, P.forest_parents(el.records))
    except Exception:  # noqa: BLE001  # swallow-ok: the page form's rule then
        return False


def _fit_levels(r: Rec, count: int, file_form: bool = False) -> None:
    """``r`` padded to at least ``count`` levels."""
    if r.entries is None:
        return
    if len(r.entries) < count:
        r.entries = list(r.entries) + [
            [Bag([], 0), Bag([], 0), Bag([], 0)] for _ in range(count - len(r.entries))
        ]
    if file_form:
        root = bool(r.cf and r.dw & 0x200)
        r.entries = [r.entries[0]] + [
            [
                Bag(list(b.pairs), 11 if root and L == 1 and (i == 1 or (i == 2 and b.pairs)) else 0)
                for i, b in enumerate(lv)
            ]
            for L, lv in enumerate(r.entries[1:], start=1)
        ]


def engine_root_extent(el: "Element") -> Tuple[int, int, int, int]:
    """The element root's extent as CtDraw re-derives it on load."""
    recs = el.records
    parents = P.forest_parents(recs)
    n = len(recs)
    r1 = [None] * n
    children = [[] for _ in range(n)]
    for c in range(n):  # ascending: each list in record order
        if parents[c] is not None:
            children[parents[c]].append(c)
    for k in range(n - 1, -1, -1):
        po = recs[k]
        gh = G.parse_geom_header(po.body_raw)
        rect = tuple(gh.rect1)
        if po.type_code == TEXT_TC and po.subtype_size & 2:
            try:
                r = decode(po)
                tail = G.parse_text_tail(po)
                L = CS.layout(po)
                # LIVE (multimon.mm_monitor TXTMODE 4): the props are the
                # SUBSTITUTED bag e0.b1; the caption is the tail text (the
                # %TEMPLATE% caption in a genie element)
                bag = r.entries[0][1] if r.entries else Bag([])
                s = TM.measured_string(_Props(bag), tail["text"])
                M = flip_total(po)
                if s is not None and M is not None:
                    # a mirrored text: measured in its local frame (at its echo) and
                    # mapped through its Total, its box left of its anchor (LIVE,
                    # chain/mirror_back)
                    rect = cut_box(
                        M,
                        TM.extent(
                            s,
                            tail["face"],
                            tail["size"],
                            po.body_raw[L["style_at"] + 3],
                            tail["orient"],
                            tail["charset"],
                            tuple(tail["echo"]),
                        ),
                    )
                elif s is not None:
                    rect = tuple(
                        TM.extent(
                            s,
                            tail["face"],
                            tail["size"],
                            po.body_raw[L["style_at"] + 3],
                            tail["orient"],
                            tail["charset"],
                            tuple(gh.anchor),
                        )
                    )
            except Exception:  # noqa: BLE001 -- keep the stored rect
                _note_exc("genie_place.engine_root_extent")
                pass
        kids = children[k]
        if po.class_flag and kids and po.type_code in (GROUP_TC, SYMSET_TC):
            if po.type_code == SYMSET_TC:
                # a SYMBOL SET's extent is its FIRST state's (corpus pages and elements;
                # LIVE example.bottle)
                ks = [c for c in kids if recs[c].type_code not in UNION_SKIP] or kids
                rect = r1[ks[0]]
            else:
                # (a group's Extent counts its DummyMarkers: geometry.group_union)
                rect = G.group_union([(recs[c].type_code, r1[c], r1[c]) for c in kids])[0]
        r1[k] = rect
    return r1[0]


def declared_params(el: Element) -> "OrderedDict[str, str]":
    """The genie's parameters as the placement dialog lists them: every name
    declared in a record's OUTERMOST level declaration bag (last entry b0), in
    DFS first-declaration order; value = the default ('' when the declaration
    is '\\n' = no default)."""
    canon = _canon_view(el)
    if canon is not None:
        got = canon.__dict__.get("_gp_decl")
        if got is None:
            got = canon.__dict__["_gp_decl"] = _declared_params(el)
        return OrderedDict(got)
    return _declared_params(el)


def _declared_params(el: Element) -> "OrderedDict[str, str]":
    out: "OrderedDict[str, str]" = OrderedDict()
    seen: Dict[str, str] = {}
    recs = convert_page_form(el.records)
    if (
        not _is_composition_cache(el.lib_path, el.library)
        and not declares_parameters(recs, P.forest_parents(recs))
        and not _root_has_level(recs)
    ):
        return out  # not a genie: CtDraw opens no dialog
    for po in recs:
        r = decode(po)
        if not r.entries or len(r.entries) < 2:
            continue
        for k, v in r.entries[-1][0].pairs:
            v = "" if v == "\n" else v
            if k.lower() in seen:
                if v and not out.get(seen[k.lower()]):
                    out[seen[k.lower()]] = v  # a later default fills an empty one
                continue
            seen[k.lower()] = k
            out[k] = v
    return out


def _is_composition_cache(*names) -> bool:
    """A composite genie's composition cache (its parameters live on its members)."""
    return any("_compositioncaches" in (n or "").lower() for n in names)


def _root_has_level(recs) -> bool:
    """The element root carries a genie level of its own."""
    if not recs:
        return False
    try:
        r = decode(recs[0])
    except Exception:  # noqa: BLE001  # swallow-ok: no level then
        return False
    return bool(r.entries) and len(r.entries) >= 2


class _Cycle(Exception):
    pass


def declared_params_of(lib_path: str, element: str, resolver=None) -> "OrderedDict[str, str]":
    """The genie dialog's rows, without recomposing nested genies."""
    op = _op_enter()
    try:
        try:
            view, bad = _decl_view(lib_path, element, resolver, op, {}, ())
        except _Cycle:
            return declared_params(load_element(lib_path, element, resolver))
        if bad:
            raise ValueError("%s: record %d does not decode" % (element, min(bad)))
        out: "OrderedDict[str, str]" = OrderedDict()
        seen: Dict[str, str] = {}
        for v in view:
            if v is None or v[0] < 2:
                continue
            for k, val in v[2]:
                val = "" if val == "\n" else val
                if k.lower() in seen:
                    if val and not out.get(seen[k.lower()]):
                        out[seen[k.lower()]] = val
                    continue
                seen[k.lower()] = k
                out[k] = val
        return out
    finally:
        _op_leave(op)


def _decl_view(lib_path: str, element: str, resolver, op, memo: dict, stack: tuple):
    """(per record [levels, e1.b0 pairs, outermost b0 pairs] or None, indices
    of the records that do not decode) of the element as load_element holds
    it; raises where load_element raises."""
    key = (lib_path, element, None if resolver is None else id(resolver))
    if key in memo:
        got = memo[key]
        if isinstance(got, Exception):
            raise got
        return got
    if key in stack:
        raise _Cycle()
    try:
        got = _decl_view_of(lib_path, element, resolver, op, memo, stack + (key,))
    except _Cycle:
        raise
    except Exception as ex:  # noqa: BLE001 -- the same failure next time
        memo[key] = ex
        raise
    memo[key] = got
    return got


def _decl_view_of(lib_path, element, resolver, op, memo, stack):
    lib = open_library(lib_path)
    blk = lib._find_block(element)
    pb = lib.element(element)
    if blk is None or pb is None:
        raise KeyError("%s: no element %r" % (lib_path, element))
    recs = [o for o in pb.objects if o.raw_payload is None]
    end, pending = 0, 1
    while pending and end < len(recs):
        pending -= 1
        pending += recs[end].class_flag
        end += 1
    recs = recs[:end]
    if G.parse_geom_header(recs[0].body_raw) is None:
        raise ValueError("%s: no geometry header" % element)
    recs = convert_page_form(recs)
    if (
        not _is_composition_cache(lib_path, element)
        and not declares_parameters(recs, P.forest_parents(recs))
        and not _root_has_level(recs)
    ):
        return [None] * len(recs), set()  # not a genie: no rows (CtDraw opens no dialog)
    view: list = []
    bad = set()
    for i, po in enumerate(recs):
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._decl_view_of")
            bad.add(i)
            view.append(None)
            continue
        e = r.entries
        view.append(
            None
            if e is None
            else [len(e), list(e[1][0].pairs) if len(e) >= 2 else None, list(e[-1][0].pairs)]
        )
    res = resolver
    if res is None:
        try:
            res = op.for_page(lib_path) if op is not None else SymbolResolver.for_page(lib_path)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._decl_view_of")
            res = None
    k = 1
    while res is not None and k < len(recs):
        po = recs[k]
        if not (po.class_flag and (po.subtype_size & 0x201) == 0x201):
            k += 1
            continue
        e2, pending = k, 1
        while pending and e2 < len(recs):
            pending -= 1
            pending += recs[e2].class_flag
            e2 += 1
        sub = None
        try:
            ref = reference_from_object(po)
            rs = res.resolve(ref) if ref is not None else None
            if rs is not None:
                sub = (
                    _parse_root(rs.library_path, ref.element),
                    _decl_view(rs.library_path, ref.element, None, op, memo, stack),
                )
        except _Cycle:
            raise
        except Exception:  # noqa: BLE001 -- the nested load fails: skipped
            _note_exc("genie_place._decl_view_of")
            sub = None
        if sub is not None:
            srecs, (sview, sbad) = sub
            if len(srecs) == e2 - k and all(
                srecs[j].type_code == recs[k + j].type_code and srecs[j].class_flag == recs[k + j].class_flag
                for j in range(e2 - k)
            ):
                for j in range(1, e2 - k):
                    if (k + j) in bad or j in sbad:
                        raise ValueError("%s: nested record does not decode" % element)
                    a, c = view[k + j], sview[j]
                    if a is None or c is None:
                        continue
                    if a[0] >= 2 and c[0] >= 2:
                        a[1] = list(c[1])
                        if a[0] == 2:
                            a[2] = list(c[1])
        k = e2
    if any(G.parse_geom_header(po.body_raw) is None for po in recs):
        raise ValueError("%s: a record without a geometry header" % element)
    return view, bad


def _parse_root(lib_path: str, element: str) -> List[PageObject]:
    """The element's first root subtree as parsed (load_element before its refresh)."""
    pb = open_library(lib_path).element(element)
    recs = [o for o in pb.objects if o.raw_payload is None]
    end, pending = 0, 1
    while pending and end < len(recs):
        pending -= 1
        pending += recs[end].class_flag
        end += 1
    return recs[:end]


def art_twins(el: Element, resolver=None, _depth: int = 0) -> List[PageObject]:
    """The record that owns each instance record's art (font, style block)."""
    if resolver is None or _depth > 8:
        return list(el.records)
    memo_on = _memo_on()
    op = _op_enter()
    if op is None or not memo_on:
        try:
            return _art_twins(el, resolver, _depth, op)
        finally:
            _op_leave(op)
    try:
        canon = _canon_view(el)
        paths = getattr(resolver, "search_paths", None)
        if canon is None or paths is None:
            return _art_twins(el, resolver, _depth, op)
        key = (tuple(paths), _depth)
        memo = canon.__dict__.setdefault("_gp_twins", {})
        hit = memo.get(key)
        if hit is not None and hit[2] == _MEMO_GEN[0] and _valid(op, hit[1], resolver):
            _merge_deps(op, hit[1], resolver, False)
            return list(hit[0])
        f = _frame_push(op, ("twins", id(canon), key), resolver)
        try:
            tw = _art_twins(el, resolver, _depth, op)
        finally:
            _frame_pop(op, f)
        if not f.tainted:
            memo[key] = (tuple(tw), f.deps, _MEMO_GEN[0])
        return tw
    finally:
        _op_leave(op)


def _art_twins(el: Element, resolver, _depth: int, op: Optional[_Op]) -> List[PageObject]:
    recs = el.records
    out = list(recs)
    k = 1
    while k < len(recs):
        po = recs[k]
        if po.class_flag and (po.subtype_size & 0x201) == 0x201:
            end, pending = k, 1
            while pending and end < len(recs):
                pending -= 1
                pending += recs[end].class_flag
                end += 1
            try:
                ref = reference_from_object(po)
                res = resolver.resolve(ref) if ref is not None else None
                if ref is not None:
                    _note(op, "probe", resolver, ref)
            except Exception:  # noqa: BLE001
                _note_exc("genie_place._art_twins/resolve")
                res = None
            if res is not None:
                try:
                    # (the nested element as the element's own load took it: a page
                    # composition's copy was re-composed from its compose load)
                    sub = load_element(
                        res.library_path, ref.element, compose=bool(getattr(el, "compose", False))
                    )
                except NestingCycle:
                    sub = None  # (a genie holding a copy of itself: its own records)
                except RecursionError:
                    raise
                except Exception:  # noqa: BLE001
                    _note_exc("genie_place._art_twins/load")
                    sub = None
                if sub is not None and len(sub.records) == end - k:
                    tw = art_twins(sub, resolver, _depth + 1)
                    for j in range(1, end - k):
                        out[k + j] = tw[j]
            k = end
            continue
        k += 1
    return out


# =========================================================================== class sections
def text_strings_len(cls: bytes) -> int:
    """Length of a text's string section at the start of cls."""
    (flag,) = struct.unpack_from("<I", cls, 0)
    o = 4
    _s, o = _lp(cls, o)
    if flag & 2:
        _s, o = _lp(cls, o)
    if flag & 4:
        (n,) = struct.unpack_from("<H", cls, o)
        o += 2
        for _ in range(n):
            _s, o = _lp(cls, o)
    if flag & 8:
        (n,) = struct.unpack_from("<H", cls, o)
        o += 2
        for _ in range(n):
            _s, o = _lp(cls, o)
    return o


def own_string_caption(r: Rec) -> Optional[str]:
    """The caption in an art-stripped text record's own string section ([u32
    flag][lpstr caption]..., after the dwFlags 0x100 metadata): the caption CtDraw's load
    keeps for the member (ctf._own_caption's reading); None when it has none."""
    cls = r.cls or b""
    try:
        o = metadata_len(cls) if r.dw & 0x100 else 0
        if len(cls) < o + 6:
            return None
        (n,) = struct.unpack_from("<H", cls, o + 4)
        if len(cls) < o + 6 + n:
            return None
        return cls[o + 6 : o + 6 + n].decode(CODEC, "replace")
    except Exception:  # noqa: BLE001  # swallow-ok: no readable string section
        return None


def metadata_len(cls: bytes) -> int:
    (n,) = struct.unpack_from("<H", cls, 0)
    o = 2
    for _ in range(n):
        _s, o = _lp(cls, o)
        _s, o = _lp(cls, o)
    return o


def text_strings(values: Bag, caption: str) -> bytes:
    """writer.text_string_section over an explicit value bag."""
    try:
        mode = int((values.get("TXTMODE") or "0").strip() or "0")
    except ValueError:
        mode = 0
    flag = {0: 3, 1: 5, 2: 9}.get(mode, 1)
    g = lambda k: values.get(k) or ""
    st = lambda v: "" if v == STATE_UNSET else v
    out = struct.pack("<I", flag) + _enc_lp(g("TXT_OFF") or caption)
    if flag & 2:
        out += _enc_lp(g("TXT_ON"))
    if flag & 4:
        n = 0
        for i in range(32, -1, -1):
            if g("STATE_%d" % i):
                n = i + 1
                break
        out += struct.pack("<H", n) + b"".join(_enc_lp(st(g("STATE_%d" % i))) for i in range(n))
    if flag & 8:
        out += struct.pack("<H", 256) + b"".join(_enc_lp(st(g("ARRAY_%d" % i))) for i in range(256))
    return out


def metadata_section(values: Bag) -> bytes:
    """The metadata cache section built from the METADATA_ bag values."""
    items = []
    i = 0
    while True:
        v = values.get("METADATA_%d" % i)
        if not v:
            break
        name, _, val = v.lstrip("=").partition("=")
        items.append((name, "*" + val))
        i += 1
    return struct.pack("<H", len(items)) + b"".join(_enc_lp(a) + _enc_lp(b) for a, b in items)


def has_metadata(values: Bag) -> bool:
    """The saver sets dwFlags 0x100 when ANY property key starts with
    'METADATA_' and is longer than that (strncmp, not only
    METADATA_0)."""
    return any(len(k) > 9 and k.startswith("METADATA_") for k, _v in values.pairs)


def drop_stale_metadata(r: Rec) -> bool:
    """Drop the metadata bit and section from records that no longer have metadata."""
    if not (r.dw & 0x100) or not r.entries or has_metadata(r.entries[0][1]):
        return False
    try:
        old = metadata_len(r.cls) if len(r.cls) >= 2 else 0
    except struct.error, IndexError:  # swallow-ok: no section where the bit says one is
        old = 0
    r.cls = r.cls[old:] if old <= len(r.cls) else r.cls
    r.dw &= ~0x100
    return True


def rederive_metadata(r: Rec) -> bool:
    """Re-derive the metadata bit and section from the value bag, as CtDraw's saver does."""
    if not r.entries:
        return False
    values = r.entries[0][1]
    has = has_metadata(values)
    old = metadata_len(r.cls) if (r.dw & 0x100 and len(r.cls) >= 2) else 0
    new = metadata_section(values) if has else b""
    dw = (r.dw | 0x100) if has else (r.dw & ~0x100)
    if dw == r.dw and r.cls[:old] == new:
        return False
    r.cls = new + r.cls[old:]
    r.dw = dw
    return True


#: string part (kept) lengths for classes whose string part is a fixed-size block;
#: everything after it is the appearance section (stripped)
_FIXED_42F = {SYMSET_TC: 4, 0xFFFF: None}


def strip_class_section(r: Rec, values: Optional[Bag], caption: Optional[str], el_dw: int) -> bytes:
    """The instance class section = [0x100 metadata, rebuilt] + [the string part,
    rebuilt for text]; the element's appearance section (present
    only when the element record owns its appearance, dwFlags 0x2) is dropped."""
    cls = r.cls
    head = b""
    if el_dw & 0x100:
        ml = metadata_len(cls)
        head = metadata_section(values) if values is not None else cls[:ml]
        cls = cls[ml:]
    if r.tc == TEXT_TC:
        sl = text_strings_len(cls) if len(cls) >= 4 else 0
        body = cls[:sl]
        if values is not None and sl:
            body = text_strings(values, caption if caption is not None else "")
        return head + body
    if r.tc == SYMSET_TC and values is not None and len(cls) >= 4:
        # (declared states, states): CtDraw's saver counts the declared states from the
        # values in memory, so a condition the instance binds counts (LIVE:
        # Example Genies' xp_controls.Button with Disabled = FALSE declares 8,
        # its element, Disabled unbound, 4)
        from . import symbolset as SS

        cls = struct.pack("<H", SS.declared_count(dict(values.pairs)) & 0xFFFF) + cls[2:]
    if not (el_dw & 2):
        return head + cls  # already art-stripped in the element
    if r.tc == SYMSET_TC:
        return head + cls[:4]
    if r.tc == 0xFFFF:
        return head + cls[: max(0, len(cls) - 12)]
    return head  # the whole class section was appearance


def rebuild_colour_state(
    el_po: PageObject, values: Bag, p0_po: Optional[PageObject] = None
) -> Optional[bytes]:
    """colourstate.rebuild over the SUBSTITUTED value bag: the
    FLAG bits follow bag presence, the pairs are parsed from the bag values,
    state-0 = the library twin's (the instance has no style block)."""
    L = CS.layout(el_po)
    if L is None:
        return None
    b = el_po.body_raw
    props = {k: v for k, v in values.pairs}
    P0 = CS.state0_source(b, L["style_at"]) if "style_at" in L else CS._pair_at(b, L["state0_at"])
    if p0_po is not None and p0_po is not el_po:
        # a member of a genie NESTED in the element: state-0 comes from the
        # CURRENT nested element definition (LIVE dpsalarm.DPSStateRow)
        L2 = CS.layout(p0_po)
        if L2 is not None:
            b2 = p0_po.body_raw
            P0 = (
                CS.state0_source(b2, L2["style_at"]) if "style_at" in L2 else CS._pair_at(b2, L2["state0_at"])
            )
    mx = props.get("COL_MAXON", "")
    flag = (
        1
        | (2 if mx else 0)
        | (4 if props.get("COL_STA_0", "") else 0)
        | (8 if props.get("COL_ARR_0", "") else 0)
        | (0x10 if props.get("COL_THR_0", "") else 0)
        | (0x20 if mx else 0)
        | (0x40 if props.get("FILCOL", "") else 0)
    )
    out = struct.pack("<I", flag) + struct.pack("<II", *P0)
    if flag & 2:
        out += struct.pack("<II", *CS._parse(mx))
    if flag & 4:
        hi = max(i for i in range(256) if props.get("COL_STA_%d" % i, ""))
        out += struct.pack("<H", hi + 1) + struct.pack("<II", *P0)
        for i in range(1, hi + 1):
            out += struct.pack("<II", *CS._parse(props.get("COL_STA_%d" % i, "")))
    if flag & 8:
        n_old = L.get("arr_n", 0)
        out += struct.pack("<H", 256) + struct.pack("<II", *P0)
        for i in range(1, 256):
            v = props.get("COL_ARR_%d" % i, "")
            if v:
                pair = CS._parse(v)
            elif i < n_old:
                pair = CS._pair_at(b, L["arr_at"] + 8 * i)
            else:
                # a NEW array: the palette default the saver writes (as
                # colourstate.rebuild; NO_COLOUR would come back as transparent
                # COL_ARR_<n> keys on CtDraw's next load)
                pair = CS.array_default(i)
            out += struct.pack("<II", *pair)
    if flag & 0x10:
        thr = []
        for i in range(256):
            v = props.get("COL_THR_%d" % i, "")
            if not v:
                break
            thr.append(CS._parse_thr(v))
        out += struct.pack("<H", len(thr)) + b"".join(thr)
    if flag & 0x20:
        out += struct.pack("<II", *CS._parse(mx))
    if flag & 0x40:
        out += struct.pack("<II", *CS._parse(props.get("FILCOL", "")))
    return out


# =========================================================================== text extent
class _Props:
    def __init__(self, bag: Bag):
        self._d = {k: v for k, v in bag.pairs}

    def raw_properties(self):
        return dict(self._d)


def text_extent(el_po: PageObject, values: Bag, anchor, caption=None) -> Optional[Tuple[int, int, int, int]]:
    """The engine re-measures a text on load with the LIBRARY twin's font
    (style-block attribs + text tail face/size/orient/charset) and the
    instance's (substituted) string."""
    from ctgedit.format import textmetrics as TM

    if not (el_po.subtype_size & 2):
        return None
    tail = G.parse_text_tail(el_po)
    L = CS.layout(el_po)
    if tail is None or L is None or "style_at" not in L:
        return None
    attribs = el_po.body_raw[L["style_at"] + 3]
    cap = caption if caption is not None else (values.get("TXT_OFF") or tail["text"])
    s = TM.measured_string(_Props(values), cap)
    if s is None:
        return None
    return TM.extent(s, tail["face"], tail["size"], attribs, tail["orient"], tail["charset"], tuple(anchor))


def flip_total(po: PageObject) -> Optional[Tuple[float, ...]]:
    """A text record's Total (slot 3) when it mirrors or turns (its linear part is not the
    identity:, a mirror keeps the matrix in a text's slots), else None."""
    try:
        s3 = P.read_slots(po.body_raw)[0][2]
    except Exception:  # noqa: BLE001  # swallow-ok: no slots, no transform
        return None
    if not s3[0] or all(abs(v - w) < 1e-9 for v, w in zip(s3[1][:4], (1.0, 0.0, 0.0, 1.0))):
        return None
    return tuple(s3[1])


def cut_box(M, box) -> Tuple[int, int, int, int]:
    """``box`` through the affine ``M`` as textmetrics.text_extent maps a mirrored text's
    box: each corner rounded when within 1e-9 of a whole pixel, else truncated toward
    zero."""
    a, b, c, d, h, k = M

    def cut(v):
        n = round(v)
        return int(n) if abs(v - n) < 1e-9 else int(v)

    xs = [a * x + c * y + h for x, y in ((box[0], box[1]), (box[2], box[3]))]
    ys = [b * x + d * y + k for x, y in ((box[0], box[1]), (box[2], box[3]))]
    return (cut(min(xs)), cut(min(ys)), cut(max(xs)), cut(max(ys)))


def flipped_text_extent(el_po: PageObject, values: Bag, M, caption=None):
    """(rect1, rect2) of a mirrored text, measured in its local frame."""
    tail = G.parse_text_tail(el_po)
    if tail is None:
        return None
    ext = text_extent(el_po, values, tuple(tail["echo"]), caption)
    if ext is None:
        return None
    L = CS.layout(el_po)
    flags = el_po.body_raw[L["style_at"] :][:6] if L and "style_at" in L else None
    b2 = tuple(G.predict_bounds(TEXT_TC, ext, flags, G.text_points(el_po)) or ext)
    return cut_box(M, ext), cut_box(M, b2)


# =========================================================================== the placement
@dataclass
class Options:
    ov_e0b2: int = 0  # instance template bag overshoot
    bitmap_b2_ov10: bool = True  # tc 7 keeps 10
    add_an_name: bool = False  # named members get __AN_NAME__ (DPS-era pages)
    an_first: bool = True  # AN leads the root substitution bag
    root_name_from_bag: bool = True
    #: LIVE (LibraryObjectPlaceEx): a fresh paste writes NO 'AN' key (the corpus AN
    #: entries are older CtDraw / paste-dialog residue)
    root_an: bool = False
    #: the genie is placed INTO A GENIE ELEMENT (an element window, not a page): its
    #: bindings are templates of the outer element (CtDraw's shipped tagextensions.block_tag
    #: / block_element, myspecialday.MonthPanel): the placed level keeps the typed bindings
    #: in its TEMPLATE bag, its value bag holds them substituted with the outer element's
    #: (unbound) level, and an element level follows (the outer parameters each record's
    #: bindings name)
    into_genie_element: bool = False
    #: the genie dialog's OK: 'AN' = the root's handle + 1 leads the placed level's bindings
    #: (set in place when the key is there; LIVE, CtDraw's genie dialog on a page
    #: and in an element window: [AN, Label]; in the element form the template bag too).
    #: A paste without the dialog writes none (``root_an``)
    dialog_an: bool = False
    #: the instance is composed AGAIN (CtDraw's page load, the genie dialog's OK): a member's
    #: placed level is its declared defaults, then its bound names set in declaration order
    #: (LIVE, CtDraw's load of pages)
    recompose: bool = False


def _holds_library(r: "Rec") -> bool:
    """A decoded DummyMarker is a PLACEHOLDER genie's (its value bag names the genie in
    '_Library', :func:`placeholder_record`)."""
    return bool(r.entries) and bool(str(r.entries[0][1].get("_Library") or "").strip())


def union_rect(rs):
    rs = [r for r in rs if r is not None]
    if not rs:
        return None
    return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))


def _shift(r, d):
    return (r[0] + d[0], r[1] + d[1], r[2] + d[0], r[3] + d[1])


def root_params(el: Element, params: Optional[dict], an_value: str, opts: Options) -> Bag:
    """The root's placement-level value bag: the genie's substitutions."""
    bag = Bag([])
    first = opts.an_first or opts.dialog_an
    if (opts.root_an or opts.dialog_an) and first:
        bag.pairs.append(("AN", an_value))
    for name, dflt in declared_params(el).items():
        if name.lower() != "an" and dflt:
            bag.pairs.append((name, dflt))
    for k, v in (params or {}).items():
        if k.lower() != "an":
            bag.set(k, str(v))
    if opts.root_an and not first:
        bag.pairs.append(("AN", an_value))
    return bag


def binds_outer_tokens(el: "Element", params: Optional[dict]) -> bool:
    """Do the placement's bindings name a %parameter% of the host element?"""
    return bool(template_refs(root_params(el, params, "0", Options())))


def set_dialog_an(bag: Bag, handle: int) -> None:
    """The genie dialog's OK on ``bag`` (the placed level's bindings): 'AN' = ``handle``
    + 1, in place when the key is there, else first (:attr:`Options.dialog_an`)."""
    an = str(handle + 1)
    for i, (k, _v) in enumerate(bag.pairs):
        if k.lower() == "an":
            bag.pairs[i] = (k, an)
            return
    bag.pairs.insert(0, ("AN", an))


_BUILD_OPTS = Options()
#: a nested copy built in the element form (_nested_build(element_form=True))
_ELEMENT_FORM_OPTS = Options(into_genie_element=True)
#: a declaration without a default (the bag value CtDraw stores for '%name%')
NO_DEFAULT = "\n"


def build_instance_records(
    el: Element,
    local_hk: Tuple[float, float],
    params=None,
    *,
    handles: Sequence[int],
    page_name: str = "",
    root_bag: Optional[Bag] = None,
    opts: Options = _BUILD_OPTS,
    parent_chain=None,
    resolver=None,
    shells: bool = False,
    keep=None,
    member_locals=None,
) -> List[PageObject]:
    """The instance records for element ``el`` placed at ``local_hk``."""
    op = _op_enter()  # its art-twin / nested loads share one operation
    try:
        recs = _build_recs(
            el,
            local_hk,
            params,
            handles=handles,
            page_name=page_name,
            root_bag=root_bag,
            opts=opts,
            parent_chain=parent_chain,
            resolver=resolver,
            member_locals=member_locals,
        )
    finally:
        _op_leave(op)
    make = _shell_po if shells else to_po
    if keep is None:
        return [make(encode(r)) for r in recs]
    return [make(encode(r)) if j in keep else _Skipped(r) for j, r in enumerate(recs)]


class _Skipped:
    """A record build_instance_records(keep=) did not encode: its child count, type
    and flag word only (what the root step's parent walk reads)."""

    __slots__ = ("class_flag", "type_code", "subtype_size")

    def __init__(self, r: "Rec"):
        self.class_flag, self.type_code, self.subtype_size = r.cf, r.tc, r.dw


def _build_recs(
    el: Element,
    local_hk,
    params=None,
    *,
    handles: Sequence[int],
    page_name: str = "",
    root_bag: Optional[Bag] = None,
    opts: Options = _BUILD_OPTS,
    parent_chain=None,
    resolver=None,
    member_locals=None,
) -> List[Rec]:
    """build_instance_records' records, before they are encoded. ``member_locals`` =
    {record index: (mode, Local)}: a member's own Local the placement keeps (a slot 1 on
    a member record, composed before its element slot 2)."""
    # a memoised element's records are decoded once (_decoded); the build only
    # ever re-assigns a Rec's attributes (its bags are copied before they are
    # changed), so a shallow copy of the shared decode is a decode of its own
    recs = (
        [_copy.copy(_decoded(el, j)) for j in range(len(el.records))]
        if _canon_view(el) is not None
        else [decode(po) for po in el.records]
    )
    twins = art_twins(el, resolver)
    n = len(recs)
    parents = P.forest_parents(el.records)
    if len(local_hk) == 2 and not isinstance(local_hk[1], tuple):
        H, K = float(local_hk[0]), float(local_hk[1])
        LOCAL = (0x01, (1.0, 0.0, 0.0, 1.0, H, K))
    else:
        LOCAL = (local_hk[0], tuple(float(v) for v in local_hk[1]))  # (mode, m6): scale / rotate
    # -- handles / an ---------------------------------------------------------
    if len(handles) == len(recs) and any(
        r.tc in INPUT_TCS and parents[k] is not None for k, r in enumerate(recs)
    ):
        new_h = list(handles)  # one per record, input records too
    else:
        it = iter(handles)
        new_h = []
        for k, r in enumerate(recs):
            p = parents[k]
            if r.tc in INPUT_TCS and p is not None:
                new_h.append(new_h[p])
            else:
                new_h.append(next(it))
    an_value = str(new_h[0] + 1)
    P0 = root_bag if root_bag is not None else root_params(el, params, an_value, opts)
    specials = {"page": "", "an": an_value}  # (LIVE: '%Page%' -> '', not the page name)

    out_vals: List[Optional[Bag]] = [None] * n  # substituted e0.b1 per record
    # the keys a root without a level of its own took from the placement (no animation)
    root_value_keys: set = set()
    captions: List[Optional[str]] = [None] * n
    untrimmed: Dict[int, Dict[str, str]] = {}  # {k: {key: value before its left trim}}
    _bind_record_levels(
        P0, captions, el, new_h, out_vals, parents, recs, root_value_keys, specials, untrimmed, opts
    )
    _set_dynamic_bits_and_slots(LOCAL, el, parents, recs, root_value_keys, parent_chain, member_locals)
    return _compose_geometry(captions, el, n, out_vals, parents, recs, twins, opts, resolver, member_locals)


def _bind_record_levels(
    P0, captions, el, new_h, out_vals, parents, recs, root_value_keys, specials, untrimmed, opts
):
    # a GENIE element's records all end with its own level (the element form,
    # _copies_in_element_form): placed on a page, that level is the placed one also on a
    # record short of the levels of the genies between (a clone a page composition of a
    # host not saved since its nested genie changed gained: its element record's levels;
    # decided at the first such record)
    own_top: Optional[bool] = None
    for k, r in enumerate(recs):
        el_po = el.records[k]
        root = k == 0
        r.handle = new_h[k]
        r.an = str(G.get_handle(el_po))  # the library-twin link number
        # -- dwFlags ------------------------------------------------------------
        if root:
            # LIVE: a GENIE root gets 0x201; a linked GROUP-ROOT SYMBOL (.ctl
            # element whose root is a tc 8 group: WMFPUMPS, BOOKS.coverx) is
            # pasted with the SAME art-stripped instance forest but root 0x1
            r.dw = (r.dw & ~0x2) | (0x201 if el.ext == "ctm" else 0x1)
            r.link = "%s.%s.<%s" % (el.library, el.element, el.ext)
        else:
            r.dw &= ~0x3
            r.link = None
        # -- bags: top-down substitution chain -----------------------------------
        if r.entries is not None:
            ents = [[b.copy() for b in e] for e in r.entries]
            if not root and len(ents) >= 3:
                # a member of a genie nested in the element starts from its values as
                # CtDraw holds them in memory (the file has the save form): a key whose
                # value the element leaves empty is set after the others (LIVE,
                # CtDraw's load of pages: COMMENT after VISIBLE on a nested alarm
                # member, COMMENT before TXT_STR where both were empty in the element)
                ents[0][1] = memory_values(r.entries).copy()
            N = len(ents) - 1
            # genie levels this record would have with the PLACED level on
            # top: 1 + the genie roots nested in the element around (and
            # including) it. LIVE (servermon.list_cluster): a nested genie
            # whose bindings are constants carries NO placed level -- its top
            # level is its own and keeps the element's values.
            dk = 1
            a = k
            while a is not None and a != 0:
                if el.records[a].class_flag and (el.records[a].subtype_size & 0x200):
                    dk += 1
                a = parents[a]
            has_placed = root or N >= dk
            if not has_placed and N >= 1 and own_top is None:
                own_top = (
                    not opts.into_genie_element
                    and recs[0].entries is not None
                    and len(recs[0].entries) >= 2
                    and _copies_in_element_form(el)
                )
            if not has_placed and N >= 1 and own_top:
                # LIVE, the fixture oracle's chain6/stale_page: n2 placed with Name 'P2'
                # (n0 gained '%Label% X', n1 saved, n2 not): CtDraw's page load + save and a
                # new placement bind the gained text, its three levels n1's record's (Label
                # 'P2 L1', Name 'P2'), 'P2 L1 X' 49 wide, the genie's box refit to it. An
                # element save keeps such a record unbound (chain5/stale_host: the Label
                # binding level empty, 'X')
                has_placed = True
            if root and N == 0 and P0.pairs and not opts.into_genie_element and not opts.recompose:
                # a genie without a level of its own (it declares no parameter) PLACED with
                # values: they go to the root's own value bag, set as sets
                # them, and no member takes them (LIVE, the fixture oracle's
                # chain6/paramless_copy same/page: fx_none placed with P and Label stored
                # them in its root's e0.b1 only, every text unbound, the root not dynamic:
                # dw 0x209). A genie form can ask for such values, so they are kept.
                # (not on a composition again, the genie dialog's OK)
                have = {kk.lower() for kk, _v in ents[0][1].pairs}
                for key, v in P0.pairs:
                    ents[0][1].set(key, v)
                    if key.lower() not in have:
                        root_value_keys.add(key.lower())
            if N >= 1 and not has_placed:
                for lvl in range(N - 1, 0, -1):
                    subst_level(ents[lvl], ents[lvl + 1], specials)
                e0, lv1 = ents[0], ents[1]
                for key, tmpl in e0[2].pairs:
                    v = substitute(tmpl, lv1[1], lv1[0], key, specials)
                    e0[1].set(key, v)
                    if v[:1].isspace():
                        # the left trim of every derived value (below): a genie without
                        # parameters holding n2 placed with Name unbound would show ' L1'
                        # where its element and a placement of n2 show 'L1'
                        untrimmed.setdefault(k, {})[key.lower()] = v
                        low = key.lower()
                        e0[1].pairs = [
                            (kk, vv.lstrip() if kk.lower() == low else vv) for kk, vv in e0[1].pairs
                        ]
            if N >= 1 and has_placed:
                top = ents[N]
                if root:
                    top[1] = Bag(list(P0.pairs), top[1].ov)
                else:
                    # LIVE (switches.MiniToggleSwitch): the member's level-N
                    # value bag lists its declared names in the ROOT BAG's
                    # order (P0), spelled as the member declares them;
                    # declared-but-unset names fall back to their default
                    vals = Bag([], top[1].ov)
                    decl = {nm.lower(): (nm, d) for nm, d in top[0].pairs}
                    # (the names come from the stored declaration bag only: a typed binding
                    # the level below's tokens reference without a declaration is NOT
                    # taken either -- Example !EquipControl's tabbar (CtDraw-authored) has
                    # no PageLabel on its placed level though typed and referenced; the
                    # PageFilter CtDraw's save of a genie added came from the middle
                    # element's own copy, refresh_nested's inner-level merge)
                    done = set()
                    if opts.recompose:
                        for name, dflt in top[0].pairs:
                            if dflt and dflt != "\n" and name.lower() not in done:
                                vals.pairs.append((name, dflt))
                                done.add(name.lower())
                        p0 = {pk.lower(): pv for pk, pv in P0.pairs}
                        for name, _dflt in top[0].pairs:
                            pv = p0.get(name.lower())
                            if pv:
                                vals.set(name, pv)
                    else:
                        for pk, pv in P0.pairs:
                            hit = decl.get(pk.lower())
                            if hit and pv and pk.lower() not in done:
                                vals.pairs.append((hit[0], pv))
                                done.add(pk.lower())
                        for name, dflt in top[0].pairs:
                            if name.lower() in done:
                                continue
                            v = "" if dflt == "\n" else dflt
                            if v:
                                vals.pairs.append((name, v))
                    top[1] = vals
                if opts.into_genie_element:
                    # the typed bindings are TEMPLATES of the outer element: the value bag
                    # = their substitution with the outer element's unbound level
                    typed = Bag(list(top[1].pairs), 11)
                    vals = Bag([], 11)
                    for key, tmpl in typed.pairs:
                        v = substitute(tmpl, Bag([]), Bag([]), key, specials)
                        if v:
                            vals.pairs.append((key, v))
                    if root:
                        top[0] = Bag([(nm, d) for nm, d in declared_params(el).items() if d], 0)
                    top[1], top[2] = vals, typed
                nested_root = bool(el_po.class_flag and (el_po.subtype_size & 0x200)) and not root
                member_rule = opts.into_genie_element and not nested_root
                # a member of the placed genie: each level under the placed one is its
                # templates with the EFFECTIVE values above (the placed level's: its
                # templates as typed), top_level_templates
                eff = top_level_templates(ents[N]) if member_rule else None
                for lvl in range(N - 1, 0, -1):
                    ent, outer = ents[lvl], ents[lvl + 1]
                    subst_level(ent, outer, specials, outer_vals=eff)
                    if eff is not None:
                        eff = ent[1]
                e0, lv1 = ents[0], ents[1]
                lv1_vals = eff if eff is not None else lv1[1]
                for key, tmpl in e0[2].pairs:
                    v = substitute(tmpl, lv1_vals, lv1[0], key, specials)
                    e0[1].set(key, v)
                    if v[:1].isspace():
                        untrimmed.setdefault(k, {})[key.lower()] = v
                        # a value is left-trimmed after it is set, so one of only blanks
                        # stays as '' (LIVE: face1 '%TAG% @(Output to Auto)'; CtDraw's load
                        # of pages: COMMENT '%NAME%  ' and TXT_OFF '%header%' + CR LF under
                        # unbound tokens)
                        low = key.lower()
                        e0[1].pairs = [
                            (kk, vv.lstrip() if kk.lower() == low else vv) for kk, vv in e0[1].pairs
                        ]
            # LIVE: the placed root's level-N value bag always carries an
            # 11-byte internal pair; a genie NESTED in the element carries
            # it on its own level's value AND template bags
            if N >= 1 and root:
                ents[N][1].ov = 11
            elif N >= 1 and el.records[k].class_flag and (el.records[k].subtype_size & 0x200):
                # a nested genie root's OWN level is always level 1; its
                # template bag carries the pair only when it holds templates
                ents[1][1].ov = 11
                ents[1][2].ov = 11 if ents[1][2].pairs else 0
                ents[1][2].ov = 11 if ents[1][2].pairs else 0
            # overshoots: e0.b1 keeps the class's internal pair, e0.b2 is rebuilt
            if ents[0][1].ov > 11:
                ents[0][1].ov = 11 if r.tc in _OV11_TCS else 10
            # LIVE (B2/B3): the instance template bag
            # carries the 10-byte internal pair only on a LINKED symbol
            # member (element tc 7 with dwFlags 0x1); everything else 0
            twin_dw = getattr(el, "nested_twins", {}).get(k, el_po).subtype_size
            ents[0][2].ov = (
                10
                if (r.tc == BITMAP_TC and (twin_dw & 0x1 or el_po.subtype_size & 0x1) and opts.bitmap_b2_ov10)
                else opts.ov_e0b2
            )
            if opts.add_an_name and r.name and not root and ents[0][1].get("__AN_NAME__") is None:
                ents[0][1].pairs.append(("__AN_NAME__", r.name))
            if opts.into_genie_element and N >= 1 and has_placed:
                # the outer element's level: the outer parameters this record's bindings
                # name (a nested root: none)
                # (a nested root: the defaults its members' bindings name, LIVE
                # '%Q=5%' -> Q = 5 on the root's element level, [] without a default)
                decl = (
                    [(nm, d) for nm, d in template_decls(ents[N][2]).items() if d is not None]
                    if root
                    else [
                        (nm, d if d is not None else NO_DEFAULT)
                        for nm, d in template_decls(ents[N][2]).items()
                    ]
                )
                ents.append([Bag(decl, 0), Bag([], 0), Bag([], 0)])
            elif opts.into_genie_element:
                # a record without a placed level (every record of a genie without
                # parameters; a member of a genie it nests bound with constants): the
                # outer element's level all the same, empty. LIVE, the fixture oracle's
                # chain4/param_gained: CtDraw's paste of fx_plain (its records e0 only)
                # into n0 gave its root and members two levels, the second empty, and n1's
                # copy of it three (with e0 only, a record the genie gains later would take
                # one level more than its siblings)
                ents.append([Bag([], 0), Bag([], 0), Bag([], 0)])
            r.entries = ents
            out_vals[k] = ents[0][1]
        # -- class section / button caption ---------------------------------------
        caption = None
        if r.tc == TEXT_TC:
            t = G.parse_text_tail(el_po) if el_po.subtype_size & 2 else None
            caption = t["text"] if t else None
            # LIVE: when the template bag carries TXT_OFF the caption IS the
            # substituted value -- '' when the parameter is unset (DPSStateRow
            # '%StateName%', MonthPanel '%Calendar%': string section lpstr ''
            # and the extent measured as ' '), never the twin's template text
            if r.entries is not None and r.entries[0][2].get("TXT_OFF") is not None:
                caption = out_vals[k].get("TXT_OFF") or ""
                # (the string section keeps the caption as substituted, before the bag
                # value's left trim: CR LF for '%header%' + CR LF under an unbound
                # header, LIVE)
                caption = untrimmed.get(k, {}).get("txt_off", caption)
        captions[k] = caption
        r.cls = strip_class_section(r, out_vals[k], caption, el_po.subtype_size)
        if out_vals[k] is not None:
            cs = rebuild_colour_state(el_po, out_vals[k], getattr(el, "nested_twins", {}).get(k))
            if cs is not None:
                r.cs = cs
        if r.dw & 0x80 and out_vals[k] is not None and out_vals[k].get("TEXT") is not None:
            r.s80 = _enc_lp(out_vals[k].get("TEXT"))
        r.style = None
        # -- header name --------------------------------------------------------
        if root and opts.root_name_from_bag and out_vals[k] is not None:
            nm = out_vals[k].get("__AN_NAME__")
            if nm:
                r.name = nm


def _set_dynamic_bits_and_slots(LOCAL, el, parents, recs, root_value_keys, parent_chain, member_locals):
    # -- dynamic bit 0x10: own flag from the element, or inherited
    #    from a dynamic parent group / symbol set (the default);
    #    text is always dynamic --------------------------------------------------
    for k, r in enumerate(recs):
        p = parents[k]
        dyn = bool(el.records[k].subtype_size & 0x10) or r.tc == TEXT_TC
        e0 = r.entries[0][1] if r.entries else Bag([])
        if k == 0 and root_value_keys:
            # (the placement's values on a root without a level are no animation)
            e0 = Bag([(kk, vv) for kk, vv in e0.pairs if kk.lower() not in root_value_keys])
        # (a PLACEHOLDER genie's marker: its '_Library' / '_Transform_*' keys are no
        # animation, LIVE oracle chain5/element_placeholder: 0x8 in a placed genie)
        dyn = dyn or bool(
            [
                x
                for x in CS.dynamic_keys(dict(e0.pairs))
                if not x.upper().endswith("MODE") and not (r.tc == 0xFFFF and x.startswith("_"))
            ]
        )
        if p is not None and recs[p].dw & 0x10 and recs[p].tc in (GROUP_TC, SYMSET_TC):
            dyn = True
        r.dw = (r.dw | 0x10) if dyn else (r.dw & ~0x10)

    # -- slots ------------------------------------------------------------------
    for k, r in enumerate(recs):
        if k == 0:
            mL, L = LOCAL
            s2, m2 = P.slot2_for_linked(L, mL, el.e)
            s3, m3 = P.apply_parent(s2, m2, parent_chain)
            r.slots = [(mL, L), (m2, s2), (m3, s3)]
        elif member_locals and k in member_locals:
            # the member's own Local before its element slot 2 (LIVE: CtDraw's
            # load of a page kept a nested root's T(1, 0) and composed slot 2 = T(1, 0)
            # o its element slot 2)
            mL, L = member_locals[k]
            m2, s2 = r.slots[1]
            s2n, m2n = P.compose_after(s2, m2, L, mL) if m2 else (tuple(L), mL)
            r.slots = [(mL, tuple(L)), (m2n, s2n), None]
        else:
            m2, s2 = r.slots[1]
            r.slots = [(0, P.IDENT), (m2, s2), None]
    # slot3 = parent Total o slot2, in the engine's incremental order (only the
    # ROOT's record is read back: its Local and e, P.link_of)
    root_po = to_po(encode(_with_slot3(recs[0])))
    links = {}
    for k, r in enumerate(recs):
        if k == 0:
            continue
        chain = []
        a = parents[k]
        while a is not None:
            if a == 0:
                chain.append(P.link_of(root_po))  # (Local, 1, e)
            else:
                if a not in links:
                    links[a] = _member_link(el.records[a], recs[a].slots[1])
                chain.append(links[a])
            a = parents[a]
        chain.extend(parent_chain or [])
        m2, s2 = r.slots[1]
        s3, m3 = P.apply_parent(s2, m2, chain)
        r.slots[2] = (m3, s3)


def _compose_geometry(captions, el, n, out_vals, parents, recs, twins, opts, resolver, member_locals):
    # -- geometry (bottom-up: containers are the union of their children) -------
    children = [[] for _ in range(n)]
    for k in range(n):
        if parents[k] is not None:
            children[parents[k]].append(k)
    for k in range(n - 1, -1, -1):
        r, el_po = recs[k], el.records[k]
        # element page -> the root's Total (live: Mixer genie 810; exact for integral AND
        # fractional H,K); T0 = the library twin's Total (its slot 3)
        T0 = P.read_slots(el_po.body_raw)[0][2]
        T0 = T0[1] if T0[0] else P.IDENT
        # each member through ITS element Total (back to its native integer coordinates)
        # and its final Total = the root's Total o its element Total: CtDraw composes in
        # memory from the natives, so a member whose element Total is fractional lands
        # where trunc(final(native)) does, not where the element's truncated coordinate
        # mapped again does (LIVE)
        # (an element Total with a quarter turn keeps the element coordinate: its
        # members match CtDraw's page composition that way, a page's instrument
        # genie under a turned group with half-pixel offsets)
        # (T1 = the member's final Total, its slot 3: the root's Total o its element Total,
        # with a member Local kept on the way)
        RT = recs[0].slots[2][1]
        t3 = r.slots[2] if r.slots[2] is not None else (0, P.IDENT)
        T1 = t3[1] if t3[0] else P.IDENT
        if k and T0 != P.IDENT and not T0[1] and not T0[2]:
            mp = _mapper(T0, T1)
        elif k and member_locals:
            mp = _mapper(P.IDENT, P.affine_mul(T1, P._affine_inv(T0)))
        else:
            mp = _mapper(P.IDENT, RT)
        if r.cf and children[k] and r.tc in (GROUP_TC, SYMSET_TC):
            kids = [c for c in children[k] if recs[c].tc not in UNION_SKIP] or children[k]
            if r.tc == SYMSET_TC:  # extent/bounds = the FIRST state's
                r1, r2 = recs[kids[0]].rect1, recs[kids[0]].rect2
            else:
                # (a DummyMarker counts in the Extent; the Bounds leave a zero-size child
                # out, an empty text; LIVE a page's genie root: geometry.group_union)
                r1, r2 = G.group_union([(recs[c].tc, recs[c].rect1, recs[c].rect2) for c in children[k]])
            r.rect1, r.rect2 = r1, r2
            if r.tc == GROUP_TC:
                r.anchor = (r1[0], r1[3] - 1)  # group / genie hotspot
            else:
                # a symbol set: its Total's image of the hotspot its class part holds
                # (symbolset.finish_set; LIVE, a two-thirds lamp state set of a genie)
                hot = None
                try:
                    from .symbolset import hotspot as _hotspot

                    hot = _hotspot(el_po)
                except Exception:  # noqa: BLE001  # swallow-ok: the mapped anchor then
                    hot = None
                if hot is not None and k:
                    A, B, C, D, H, K = T1
                    r.anchor = (int(A * hot[0] + C * hot[1] + H), int(B * hot[0] + D * hot[1] + K))
                else:
                    r.anchor = mp(*r.anchor)
            continue
        # a member of a genie nested in the element whose nested twin (its record in the
        # nested element) carries no transform of its own: its native box IS the twin's,
        # mapped through the member's final Total (LIVE, CtDraw's load of pages: the
        # element's truncated pixel can allow several natives)
        src_a, src_1, src_2 = r.anchor, r.rect1, r.rect2
        tw_n = getattr(el, "nested_twins", {}).get(k) if k and T0 != P.IDENT else None
        if tw_n is not None and r.tc not in POINT_CLASSES:
            s3n = P.read_slots(tw_n.body_raw)[0][2]
            if (not s3n[0] or tuple(s3n[1]) == P.IDENT) and tw_n.type_code == r.tc:
                gtn = G.parse_geom_header(tw_n.body_raw)
                src_a, src_1, src_2 = tuple(gtn.anchor), tuple(gtn.rect1), tuple(gtn.rect2)
                mp = _mapper(P.IDENT, T1)
        anchor = mp(*src_a)
        if r.tc == ELLIPSE_TC:
            anchor = r.anchor  # art-stripped ellipse keeps the twin's
        r1 = _map_rect(mp, src_1)
        # Bounds keep the twin's inflation deltas (rect2 - rect1)
        r2 = tuple(r1[i] + (src_2[i] - src_1[i]) for i in range(4))
        # a linked symbol is its symbol's native box through its final Total, as a page
        # re-derives it (placement.linked_extent; LIVE, a genie's two-thirds lamp); so
        # is a stripped copy whose nested twin is one (a turned sensor of a instrument)
        link_src = None
        if k and r.tc == BITMAP_TC and T0 != P.IDENT:
            if el_po.subtype_size & 0x1:
                link_src = el_po
            elif tw_n is not None and tw_n.type_code == BITMAP_TC and tw_n.subtype_size & 0x1:
                link_src = tw_n
        if link_src is not None and resolver is not None:
            nat = _linked_native(link_src, resolver)
            if nat is not None:
                r1 = _trunc_box(T1, nat)
                r2 = tuple(r1[i] + (src_2[i] - src_1[i]) for i in range(4))
        tw_ = twins[k] if k < len(twins) else None
        stripped_text = bool(k and r.tc == TEXT_TC and not (el_po.subtype_size & 2))
        tw_frame = (
            _twin_frame(T1, tw_) if stripped_text and tw_ is not None and tw_.type_code == TEXT_TC else None
        )
        if tw_frame is not None and tuple(tw_frame[:4]) != (1.0, 0.0, 0.0, 1.0):
            # an art-stripped text under a turned Total: its box is its art twin's native
            # box through the member's Total (LIVE, CtDraw's load of a page where the
            # element still held the unturned box),
            # the twin's own Total taken out first (_twin_frame: a text of a turned symbol
            # in the genie holds its turned box, and T1 turns it again)
            gt = G.parse_geom_header(tw_.body_raw)
            r1 = _trunc_box(tw_frame, gt.rect1)
            r2 = _trunc_box(tw_frame, gt.rect2)
        if r.tc in POINT_CLASSES:
            # a point class (Touch / Keyboard / AdvancedAnimation icon ...) is
            # placed by its anchor and keeps its natural extent (placement.py)
            ax, ay = anchor
            ox, oy = r.anchor
            r1 = (r.rect1[0] + ax - ox, r.rect1[1] + ay - oy, r.rect1[2] + ax - ox, r.rect1[3] + ay - oy)
            r2 = tuple(r1[i] + (r.rect2[i] - r.rect1[i]) for i in range(4))
        if r.tc == BUTTON_TC and out_vals[k] is not None and out_vals[k].get("WIDTH") is None:
            # LIVE (CONTROLS.sg1_open_close): a button WITHOUT a
            # WIDTH property auto-sizes to its (substituted) caption:
            # width = max(twin width, GetTextExtent(caption, Arial 11pt) + 11)
            from ctgedit.format import textmetrics as TM

            cap = out_vals[k].get("TEXT") or ""
            ex = TM.extent(cap or " ", "Arial", 11, 0, 1, 0, (0, 0))
            w = max(r1[2] - r1[0], (ex[2] - ex[0]) + 11)
            d2 = r2[2] - r1[2]
            r1 = (r1[0], r1[1], r1[0] + w, r1[3])
            r2 = (r2[0], r2[1], r1[2] + d2, r2[3])
        slot_marker = r.tc == 0xFFFF and r.slots[1] is not None and bool(r.slots[1][0])
        if slot_marker and r.slots[2] is not None:
            # a DummyMarker with a slot-2 point: its box is its Total's image of that
            # point and of the point + (1, 1) (corpus pages; LIVE, CtDraw's load of a
            # page placed one 1 x 1 where its element holds it at its Total's origin,
            # 0 x 0)
            A, B, C, D, H, K = r.slots[2][1] if r.slots[2][0] else P.IDENT
            x0, y0 = r.slots[1][1][4], r.slots[1][1][5]
            p0 = (int(A * x0 + C * y0 + H), int(B * x0 + D * y0 + K))
            p1 = (int(A * (x0 + 1) + C * (y0 + 1) + H), int(B * (x0 + 1) + D * (y0 + 1) + K))
            r1 = (min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))
            anchor = (r1[0], r1[1])
            r2 = tuple(r1[i] + (r.rect2[i] - r.rect1[i]) for i in range(4))
        elif (
            r.tc == 0xFFFF
            and k
            and recs[parents[k]].tc != SYMSET_TC
            and (opts.recompose or not _holds_library(r))
        ):
            # a free DummyMarker (a genie member, not a set state) without one: the empty
            # rect at its Total's origin (LIVE, the fixture oracle: CtDraw's load of FxParts
            # put both markers of each fxparts.fx_dummy copy there, empty, where the element
            # holds them 1 high; a template's marker on its page likewise, compose_prefix).
            # Every corpus marker is a state.
            # A PLACEHOLDER genie's marker ('_Library') keeps its box mapped at a placement
            # and takes this one at the page's load (LIVE, oracle chain5/element_placeholder)
            A, B, C, D, H, K = T1
            anchor, r1 = (int(H), int(K)), (int(H), int(K), int(H), int(K))
            r2 = r1
        elif r.tc == 0xFFFF and not (k and recs[parents[k]].tc != SYMSET_TC):
            # a symbol-set state DummyMarker without one sits at the mapped point and keeps
            # its twin's size (1x1, or 0x1: LIVE Mixer PLT_LINE)
            x, y = r1[:2]
            w, h = r.rect1[2] - r.rect1[0], r.rect1[3] - r.rect1[1]
            anchor, r1 = (x, y), (x, y, x + w, y + h)
            r2 = tuple(r1[i] + (r.rect2[i] - r.rect1[i]) for i in range(4))
        if r.tc == TEXT_TC and out_vals[k] is not None:
            tw = twins[k] if twins[k].type_code == TEXT_TC else el_po
            # (under a turned Total the text is measured in its twin's own frame and its box
            # turned with it: the stripped-text rule above. The frame is the twin's: a
            # twin with a Total of its own, a mirrored text in the genie (
            # its box measured at its anchor) or a text of a turned symbol there, already
            # holds that Total, so only the rest of T1 turns it; mapping it through T1
            # would mirror or turn it twice)
            tw_m = _twin_frame(T1, tw)
            turned = bool(k and tuple(tw_m[:4]) != (1.0, 0.0, 0.0, 1.0))
            at = G.parse_geom_header(tw.body_raw).anchor if turned else anchor
            ext = text_extent(tw, out_vals[k], at, captions[k])
            tmpl_off = (r.entries[0][2].get("TXT_OFF") or "") if r.entries else ""
            if (
                captions[k] is not None
                and captions[k].strip() == ""
                and ("\r" in tmpl_off or "\n" in tmpl_off)
            ) or (ext is not None and tuple(ext) == (0, 0, 0, 0)):
                # a multi-line caption trimmed to '' measures nothing: a zero box at its
                # Total's origin (a single-line one is measured as ' '), and so does any
                # string with no line to measure (a value of CR LF alone)
                A, B, C, D, H, K = T1
                ext, turned = None, False
                r1 = r2 = (int(H), int(K), int(H), int(K))
            # (a twin that mirrors in the genie, a Total of its own with a flip: measured in
            # its local frame, at its echo, and mapped through the member's Total, so its box
            # reaches left of its anchor as CtDraw's does, LIVE chain/mirror_back)
            flipped = (
                flipped_text_extent(tw, out_vals[k], T1, captions[k])
                if k and ext is not None and flip_total(tw) is not None
                else None
            )
            if flipped is not None:
                r1, r2 = flipped
            elif ext is not None:
                flags = tw.body_raw[CS.layout(tw)["style_at"] :][:6]
                b2 = tuple(G.predict_bounds(TEXT_TC, ext, flags, G.text_points(tw)) or ext)
                if turned:
                    r1, r2 = _trunc_box(tw_m, ext), _trunc_box(tw_m, b2)
                else:
                    r1, r2 = tuple(ext), b2
        if r.tc in _ANCHOR_LB1:
            # a box class re-derives its anchor from its extent: bitmap
            # / metafile / ActiveX (l, b - 1), rectangle / button
            # (l, b): the MAPPED twin anchor lands on another corner of a turned
            # member (a library's valve / coil symbols turned 90 deg)
            anchor = (r1[0], r1[3] - 1)
        elif r.tc in _ANCHOR_LB:
            anchor = (r1[0], r1[3])
        r.anchor, r.rect1, r.rect2 = anchor, r1, r2
        if k:
            _button_box_size(r)
    return recs


def _button_box_size(r: Rec) -> bool:
    """Set a member button's WIDTH / HEIGHT to its box."""
    if r.tc != BUTTON_TC or not r.entries or r.rect1 is None:
        return False
    r1 = r.rect1
    size = {"WIDTH": str(r1[2] - r1[0]), "HEIGHT": str(r1[3] - r1[1])}
    hit = False
    for b in (1, 2):
        if len(r.entries[0]) <= b:
            continue
        bag = r.entries[0][b]
        if any(kk.upper() in size and v != size[kk.upper()] for kk, v in bag.pairs):
            r.entries[0][b] = Bag([(kk, size.get(kk.upper(), v)) for kk, v in bag.pairs], bag.ov)
            hit = True
    return hit


#: box classes whose anchor is a corner of their extent (build_instance_records)
_ANCHOR_LB1 = frozenset({BITMAP_TC, METAFILE_TC, 0x0D})  # (l, b - 1)
_ANCHOR_LB = frozenset({0x0F, BUTTON_TC})  # (l, b)


def _linked_native(po: PageObject, resolver) -> Optional[Tuple[int, int, int, int]]:
    """The native box N of the library symbol a linked symbol record names (its element's
    first record), or None when it does not resolve."""
    try:
        ref = reference_from_object(po)
        if ref is None:
            return None
        info = P.element_info(resolver, ref.library, ref.element, ref.ext or "ctl")
    except Exception:  # noqa: BLE001  # swallow-ok: the mapped box then
        return None
    return tuple(info["N"]) if info and info.get("N") else None


def _twin_frame(T1, tw: PageObject):
    """The affine from an art twin's box frame to the member's place."""
    s3 = P.read_slots(tw.body_raw)[0][2]
    if not s3[0] or tuple(s3[1][:4]) == (1.0, 0.0, 0.0, 1.0) and not s3[1][4] and not s3[1][5]:
        return T1
    M = P.affine_mul(T1, P._affine_inv(tuple(s3[1])))
    if all(abs(x - y) <= 1e-9 for x, y in zip(M[:4], (1.0, 0.0, 0.0, 1.0))):
        M = (1.0, 0.0, 0.0, 1.0, M[4], M[5])
    return M


def _trunc_box(M, rect):
    """``rect``'s corners through the affine ``M``, truncated, as a box."""
    a, b, c, d, h, k = M
    pts = [
        (int(a * x + c * y + h), int(b * x + d * y + k)) for x, y in ((rect[0], rect[1]), (rect[2], rect[3]))
    ]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def _map_rect(mp, rect):
    (x1, y1), (x2, y2) = mp(*rect[:2]), mp(*rect[2:])
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _untrunc(p: int, scale: float, off: float, guess: float) -> int:
    """Undo CtDraw's truncation: the integer N whose stored image is ``p``."""
    import math

    best = None
    for n in range(math.floor(guess) - 2, math.ceil(guess) + 3):
        if int(scale * n + off) == p:
            # (several natives truncate to one pixel under a scale below 1, and which one
            # CtDraw holds is not in the element's pixel: a member with a nested twin
            # takes the twin's native box instead, _build_recs)
            if best is None or abs(n - guess) < abs(best - guess):
                best = n
    return best if best is not None else int(round(guess))


def _mapper(T0, T1):
    ia, ib, ic, id_, ih, ik = P._affine_inv(T0)
    a0, b0, c0, d0, h0, k0 = T0
    a, b, c, d, h, k = T1
    ident0 = T0 == P.IDENT
    axis0 = not b0 and not c0 and a0 and d0  # T0 without rotation / shear

    def mp(x, y):
        if ident0:
            lx, ly = x, y
        elif axis0:
            lx = _untrunc(x, a0, h0, ia * x + ih)
            ly = _untrunc(y, d0, k0, id_ * y + ik)
        else:
            lx = round(ia * x + ic * y + ih)
            ly = round(ib * x + id_ * y + ik)
        return (int(a * lx + c * ly + h), int(b * lx + d * ly + k))

    return mp


def _with_slot3(r: Rec) -> Rec:
    import copy

    c = copy.copy(r)
    c.slots = [s if s is not None else (0, P.IDENT) for s in r.slots]
    return c


def _member_link(el_po: PageObject, slot2):
    """What a member contributes to its descendants' Total."""
    m2, s2 = slot2
    L, m, e = P.link_of(el_po)
    if e != (0, 0) and P.slot2_for_linked(L, m, e) == (s2, m2):
        return (L, m, e)
    return (s2, m2, (0, 0))


# =========================================================================== page-level API
def page_handles(page_path: str) -> set:
    pc = parse_ctg(page_path)
    return {G.get_handle(po) for po in pc.page.objects if G.get_handle(po) is not None}


def alloc_handles(used: set, count: int) -> List[int]:
    """The handles CtDraw gives ``count`` new objects, each recorded in ``used``: the lowest
    free slot in 0..0x7FFE (for every new
    object). LIVE: the lowest free ones, not max + 1."""
    next_free = handle_allocator(used)
    return [next_free() for _ in range(count)]


def handle_allocator(used: set):
    """A handle allocator: ``next_free()`` returns the lowest free handle."""
    at = [0]

    def next_free() -> int:
        h = at[0]
        while h in used:
            h += 1
        if h > G.MAX_HANDLE:
            raise ValueError("no free handle")
        used.add(h)
        at[0] = h + 1
        return h

    return next_free


def _name_key_at(pairs: Sequence[Tuple[str, str]], comment_bound: bool) -> int:
    """Where '__AN_NAME__' goes in a value bag that lacks it."""
    backed = set()
    for k, v in pairs:
        pk = _VA.parse_key(k)
        if pk is not None and pk[0] == _VA.EXPR and v != "":
            backed.add(pk[1])
    at = len(pairs)
    while at:
        k = pairs[at - 1][0]
        pk = _VA.parse_key(k)
        if not (pk is not None and pk[1] not in backed or comment_bound and k.upper() == "COMMENT"):
            break
        at -= 1
    return at


def with_name_keys(objs: Sequence[PageObject]) -> List[PageObject]:
    """The records as CtDraw's save writes them, with name keys in the bags."""
    out = None
    for k, po in enumerate(objs):
        body = po.body_raw
        if po.raw_payload is not None or len(body) < 6 or not body[4] and not body[5]:
            continue
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place.with_name_keys")
            continue
        if not r.name or not r.entries:
            continue
        e0 = r.entries[0]
        b1, b2 = e0[1], e0[2] if len(e0) > 2 else None
        need1 = b1.get("__AN_NAME__") is None
        # (b2 takes it only with b1: oracle newpage/cut_link reopened.ctg)
        need2 = (
            need1
            and b2 is not None
            and (bool(b2.pairs) or len(r.entries) >= 2)
            and b2.get("__AN_NAME__") is None
        )
        if not (need1 or need2):
            continue
        if need1:
            tmpl = b2.get("COMMENT") if b2 is not None and len(r.entries) >= 2 else None
            at = _name_key_at(b1.pairs, tmpl is not None and "%" in tmpl)
            pairs = list(b1.pairs)
            pairs.insert(at, ("__AN_NAME__", r.name))
            e0[1] = Bag(pairs, b1.ov)
        if need2:
            e0[2] = Bag(list(b2.pairs) + [("__AN_NAME__", r.name)], b2.ov)
        if out is None:
            out = list(objs)
        out[k] = to_po(encode(r))
    return out if out is not None else objs


def text_extent_now(po: PageObject) -> Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]]:
    """(rect1, rect2) of a text with its own appearance, as CtDraw's load measures it."""
    if po.type_code != TEXT_TC or po.raw_payload is not None or not po.subtype_size & 0x2:
        return None
    try:
        r = decode(po)
        tail = G.parse_text_tail(po)
    except Exception:  # noqa: BLE001  # swallow-ok: a text that does not parse keeps its extent
        return None
    if tail is None or not r.entries or r.rect1 is None:
        return None
    for m, x in r.slots or ():
        if m and tuple(x[:4]) != (1.0, 0.0, 0.0, 1.0):
            # a turned or mirrored text: its extent follows its Local, which the measure
            # at the anchor does not see (LIVE, CtDraw's mirror of a text: the
            # box the mirror gives stays through its load + save)
            return None
    tmpl = r.entries[0][2].get("TXT_OFF") if len(r.entries[0]) > 2 else None
    s = TM.measured_string(_Props(r.entries[0][1]), tail["text"] if tmpl is None else tmpl)
    if s is None:
        return None
    L = CS.layout(po)
    body = po.body_raw
    attribs = body[L["style_at"] + 3] if L and "style_at" in L else 0
    try:
        ext = TM.extent(
            s, tail["face"], tail["size"], attribs, tail["orient"], tail["charset"], tuple(r.anchor)
        )
    except Exception:  # noqa: BLE001
        _note_exc("genie_place.text_extent_now")
        return None
    r1 = tuple(int(v) for v in ext)
    if r1 == tuple(r.rect1):
        return None
    flags = body[L["style_at"] : L["style_at"] + 6] if L and "style_at" in L else None
    r2 = tuple(int(v) for v in (G.predict_bounds(TEXT_TC, ext, flags, tail["size"]) or ext))
    return r1, r2


_UNBOUND_TOKEN = re.compile(r"%([A-Za-z_][^%=]*)(?:=([^%]*))?%")


def measured_values(r: Rec) -> Bag:
    """A member's value bag as CtDraw measures its text."""
    values = r.entries[0][1]
    if len(r.entries) < 2 or len(r.entries[0]) < 3:
        return values
    lv1 = r.entries[1]
    out = None
    for key, tmpl in r.entries[0][2].pairs:
        v = values.get(key)
        if v is None or "%" not in tmpl or v[:1].isspace():
            continue
        sub = substitute(tmpl, lv1[1], lv1[0], key)
        if sub != v and sub[:1].isspace() and sub.lstrip() == v:
            if out is None:
                out = values.copy()
            out.set(key, sub)
    return out if out is not None else values


def _blank_text_box(
    caption: Optional[str], tmpl_off: Optional[str], total, empty: bool = False
) -> Optional[Tuple[int, int, int, int]]:
    """The box of a text member that measures nothing."""
    if not empty and not (
        caption is not None and caption.strip() == "" and tmpl_off and ("\r" in tmpl_off or "\n" in tmpl_off)
    ):
        return None
    m, T = total if total is not None else (0, None)
    h, k = (T[4], T[5]) if m and T is not None else (0.0, 0.0)
    return (int(h), int(k), int(h), int(k))


def stripped_text_extent(
    po: PageObject, twin: PageObject
) -> Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]]:
    """(rect1, rect2) of an art-stripped text member, as CtDraw's load measures it."""
    if po.type_code != TEXT_TC or po.raw_payload is not None or po.subtype_size & 0x2:
        return None
    if twin is None or twin is po or not twin.subtype_size & 0x2:
        return None
    try:
        r = decode(po)
        tail = G.parse_text_tail(twin)
        L = CS.layout(twin)
    except Exception:  # noqa: BLE001  # swallow-ok: a member that does not parse keeps its extent
        return None
    if not r.entries or r.rect1 is None or tail is None or L is None or "style_at" not in L:
        return None
    for m, x in r.slots or ():
        if m and tuple(x[:4]) != (1.0, 0.0, 0.0, 1.0):
            return None
    got = _stripped_box(r, twin, tuple(r.anchor))
    if got is None or got[0] == tuple(r.rect1):
        return None
    return got


def _member_string(r: Rec, twin: PageObject, values: Bag) -> Optional[str]:
    """The string CtDraw's load measures for the art-stripped text member ``r`` (its value
    bag ``values`` as measured, :func:`measured_values`, over its twin's caption): a genie
    parameter still in it measured as its default or nothing (an unbound '%Name%' reads
    blank), unless it is the twin's caption template as typed (:func:`_tokens_as_typed`)."""
    tail = G.parse_text_tail(twin)
    if tail is None:
        return None
    tmpl_off = r.entries[0][2].get("TXT_OFF") if r.entries and len(r.entries[0]) > 2 else None
    cap = values.get("TXT_OFF")
    if not cap and tmpl_off is not None:
        # a caption template substituted to nothing (the value bag holds no TXT_OFF): the
        # caption is the record's own string section, the substitution before its left
        # trim, which CtDraw's load keeps ('' for an unbound '%Array0%', ' ' for a template
        # of one blank), not the twin's template text with its tokens blanked. An array or
        # multi-state text with an empty caption then measures its first row that is not
        # empty (LIVE, oracle chain7/array_token; a STATE_1 'M' under a ' ' template keeps
        # the blank's width)
        own = own_string_caption(r)
        cap = own if own is not None else ""
    elif not cap:
        cap = tail["text"]
    s = TM.measured_string(_Props(values), cap)
    if s is None:
        return None
    if not _tokens_as_typed(values, twin):
        s = _UNBOUND_TOKEN.sub(lambda m: m.group(2) or "", s) or " "
    return s


def _stripped_box(r: Rec, twin: PageObject, anchor):
    """(rect1, rect2) of the art-stripped text member ``r`` measured at ``anchor`` as CtDraw's
    load measures it (:func:`stripped_text_extent`), or None when it cannot be measured."""
    tail = G.parse_text_tail(twin)
    L = CS.layout(twin)
    if tail is None or L is None or "style_at" not in L or not r.entries:
        return None
    values = measured_values(r)
    blank = _blank_text_box(
        values.get("TXT_OFF"),
        r.entries[0][2].get("TXT_OFF") if len(r.entries[0]) > 2 else None,
        r.slots[2] if r.slots else None,
    )
    if blank is not None:
        return blank, blank
    s = _member_string(r, twin, values)
    if s is None:
        return None
    body = twin.body_raw
    attribs = body[L["style_at"] + 3]
    try:
        ext = TM.extent(
            s, tail["face"], tail["size"], attribs, tail["orient"], tail["charset"], tuple(anchor)
        )
    except Exception:  # noqa: BLE001
        _note_exc("genie_place.stripped_text_extent")
        return None
    if ext is None:
        return None
    if tuple(ext) == (0, 0, 0, 0):
        # (no line to measure: the zero box at the Total's origin, _blank_text_box)
        blank = _blank_text_box(None, None, r.slots[2] if r.slots else None, empty=True)
        return blank, blank
    r1 = tuple(int(v) for v in ext)
    flags = body[L["style_at"] : L["style_at"] + 6]
    r2 = tuple(int(v) for v in (G.predict_bounds(TEXT_TC, ext, flags, tail["size"]) or ext))
    return r1, r2


def member_text_box(r: Rec, twin: PageObject, anchor):
    """(rect1, rect2) of a text member of a template's copy."""
    if flip_total(twin) is None:
        return _stripped_box(r, twin, anchor)
    tail = G.parse_text_tail(twin)
    L = CS.layout(twin)
    if tail is None or L is None or "style_at" not in L or not r.entries or not r.slots:
        return None
    m3, T = r.slots[2]
    T = tuple(T) if m3 else P.IDENT
    values = measured_values(r)
    blank = _blank_text_box(
        values.get("TXT_OFF"), r.entries[0][2].get("TXT_OFF") if len(r.entries[0]) > 2 else None, r.slots[2]
    )
    if blank is not None:
        return blank, blank
    s = _member_string(r, twin, values)
    if s is None:
        return None
    try:
        ext = TM.extent(
            s,
            tail["face"],
            tail["size"],
            twin.body_raw[L["style_at"] + 3],
            tail["orient"],
            tail["charset"],
            tuple(tail["echo"]),
        )
    except Exception:  # noqa: BLE001
        _note_exc("genie_place.member_text_box")
        return None
    if ext is None:
        return None
    if tuple(ext) == (0, 0, 0, 0):
        blank = _blank_text_box(None, None, r.slots[2], empty=True)
        return blank, blank
    flags = twin.body_raw[L["style_at"] : L["style_at"] + 6]
    b2 = tuple(G.predict_bounds(TEXT_TC, ext, flags, G.text_points(twin)) or ext)
    return cut_box(T, ext), cut_box(T, b2)


def _tokens_as_typed(values: Bag, twin: PageObject) -> bool:
    """A text member's caption is its twin's caption template as typed, a token in it (a
    template's own parameter left unbound on a new page, template_place._unbound_as_typed):
    it is measured as written (LIVE, oracle chain4/outer_param new_page: '%P%' 33 wide)."""
    cap = values.get("TXT_OFF")
    if not cap or "%" not in cap:
        return False
    try:
        t = decode(twin)
    except Exception:  # noqa: BLE001  # swallow-ok: an undecodable twin: the blank rule
        return False
    return bool(
        t.entries
        and len(t.entries) >= 2
        and t.entries[0][2].get("TXT_OFF") == cap
        and template_refs(Bag([("TXT_OFF", cap)]))
    )


def _refit_containers(objs: List[PageObject], changed: Sequence[int]) -> None:
    """Refit the groups above the changed records."""
    par = P.forest_parents(objs)
    todo = sorted({p for k in changed for p in [par[k]] if p is not None}, reverse=True)
    seen = set()

    def box(po):
        gh = G.parse_geom_header(po.body_raw)
        return gh if gh is not None else decode(po)  # (decode raises)

    while todo:
        g = todo.pop(0)
        if g in seen:
            continue
        seen.add(g)
        po = objs[g]
        if po.type_code != GROUP_TC or po.raw_payload is not None:
            continue
        rs = []
        c = g + 1
        for _n in range(po.class_flag):
            if c >= len(objs):
                break
            ch = objs[c]
            if ch.type_code not in INPUT_TCS and ch.raw_payload is None:
                rs.append(box(ch))
            c = subtree_end(objs, c)
        if not rs:
            continue
        u1 = union_rect([x.rect1 for x in rs])
        u2 = union_rect([x.rect2 for x in rs])
        gh = box(po)
        if u1 is None or u2 is None:
            continue
        anchor = (u1[0], u1[3] - 1)
        if (tuple(gh.rect1), tuple(gh.rect2), tuple(gh.anchor)) == (tuple(u1), tuple(u2), anchor):
            continue
        r = decode(po)
        r.rect1, r.rect2, r.anchor = tuple(u1), tuple(u2), anchor
        objs[g] = to_po(encode(r))
        if par[g] is not None:
            todo.append(par[g])
            todo.sort(reverse=True)


def save_form(objs: Sequence[PageObject], owners=None, fonts=None, tag_format=None) -> List[PageObject]:
    """The page records as CtDraw's load + save would write them."""
    from . import legacy as _legacy

    out = with_name_keys(objs)
    rep = None
    # (a fonts table that names nothing: the page is outside a project, its Numbers and
    # Strings keep their Bounds)
    got_legacy, moved = _legacy.save_extents(
        out, fonts or None, *(() if tag_format is None else (tag_format,))
    )
    if moved:
        rep = list(got_legacy)
    stripped = list(moved)
    for k, po in enumerate(out):
        got = text_extent_now(po)
        if got is None and owners is not None and k < len(owners):
            got = stripped_text_extent(po, owners[k])
            if got is not None:
                stripped.append(k)
        if got is None:
            continue
        r = decode(po)
        r.rect1, r.rect2 = got
        if rep is None:
            rep = list(out)
        rep[k] = to_po(encode(r))
    if stripped:
        _refit_containers(rep, stripped)
    return rep if rep is not None else out


def paste_handles(records: Sequence[PageObject], used: set) -> List[int]:
    """The handle CtDraw's paste gives each record of a library object."""
    hs = page_instance_handles([([], records)], used)[0]
    used.update(hs)
    return hs


def handle_count(el: Element) -> int:
    """Handles an instance consumes (input records share their owner's)."""
    return sum(1 for k, po in enumerate(el.records) if not (po.type_code in INPUT_TCS and k))


def local_for_topleft(el: Element, x: float, y: float) -> Tuple[float, float]:
    """Root Local H,K that puts the instance's bbox top-left (the element root
    rect1 l,t) at (x, y): slot2 = Local o T(-e) = T(x - N.l, y - N.t)."""
    return (x - el.N[0] + el.e[0], y - el.N[1] + el.e[1])


def current_link_entry(lib_path: str, library: str, element: str) -> Tuple[bytes, str]:
    """The page link entry for an element as its library holds it now."""
    lib = open_library(lib_path)
    pb = lib.element(element)
    if pb is None or lib._find_block(element) is None:
        raise KeyError("%s: no element %r" % (lib_path, element))
    ext = os.path.splitext(os.path.basename(lib_path))[1].lstrip(".").lower()
    return link_table_entry(
        Element(lib_path, library, element, ext, (0, 0), [], (0, 0, 0, 0), bytes(pb.page_identity))
    )


def link_table_entry(el: Element) -> Tuple[bytes, str]:
    """The link table entry CtDraw keeps for a library reference."""
    ident = el.identity.ljust(20, b"\x00")
    stamp = struct.unpack_from("<I", ident, 0)[0]
    return ident[4:20], "%s.%s.<%s#%08x" % (el.library, el.element, el.ext, stamp)


_NATIVE = re.compile(r"@\(([^)]*)\)")
LABEL_KEYS = ("TXT_OFF", "TXT_ON", "TXT_STR")
#: a multi-state text's state captions and an array text's rows also carry labels
_LABEL_ROW = re.compile(r"(?:STATE|ARRAY)_\d+$")


def is_label_key(key: str) -> bool:
    """A text's bag key whose '@(native)' labels the link table lists: TXT_OFF, TXT_ON,
    TXT_STR, STATE_n and ARRAY_n (LIVE, oracle chain5/label_states; pglang's rule too)."""
    k = str(key).upper()
    return k in LABEL_KEYS or bool(_LABEL_ROW.match(k))


def label_key(name: str) -> str:
    """The key that makes two link table labels the same."""
    return str(name).lower()


def natives(v: str) -> List[str]:
    """The @(native) multi-language labels of a value; inside the label a
    '^' escapes the next character ('^(' '^)' '^,'), which is kept. The native
    ends at ')' or at an unescaped ',' (CtDraw, as
    pgregister.natives: '@(Tank 11, made here)' lists 'Tank 11')."""
    out = []
    i = 0
    while True:
        j = v.find("@(", i)
        if j < 0:
            return out
        k = j + 2
        buf = []
        while k < len(v):
            ch = v[k]
            if ch == "^" and k + 1 < len(v):
                buf.append(v[k + 1])
                k += 2
                continue
            if ch in "),":
                break
            buf.append(ch)
            k += 1
        out.append("".join(buf))
        i = k + 1


def label_entries(records, template: bool = False) -> List[Tuple[bytes, str]]:
    """Link table entries for the @(native) multi-language labels of text records."""
    out = []
    for po in records:
        if po.type_code != TEXT_TC or not (po.subtype_size & 0x8):
            continue
        r = decode(po)
        # an ELEMENT lists the natives of the TEMPLATE and of the substituted
        # value (LIVE: '@(%Label% units)' -> '\\%Label% units' + '\\ units')
        srcs = [r.entries[0][2], r.entries[0][1]] if (template and len(r.entries) >= 2) else [r.entries[0][1]]
        keys: List[str] = []
        for src in srcs:
            for k, _v in src.pairs:
                if k not in keys and is_label_key(k):
                    keys.append(k)
        for key in keys:
            for src in srcs:
                for nat in natives(src.get(key) or ""):
                    if nat:
                        out.append((bytes(16), "\\\\" + nat))
    return out


def add_record_labels(entries, records, after: Optional[str] = None) -> List[Tuple[bytes, str]]:
    """``entries`` plus the labels ``records`` give that it doesn't list yet."""
    from ctgedit.format import links

    out = list(entries)
    have = {label_key(n) for _g, n in out if n.startswith("\\")}
    new = []
    for g, n in label_entries(records):
        if label_key(n) not in have:
            have.add(label_key(n))
            new.append((g, n))
    if not new:
        return out
    pos = len(out)
    if after:
        key = links.strip_stamp(after).lower()
        for i, (_g, n) in enumerate(out):
            if not n.startswith("\\") and links.strip_stamp(n).lower() == key:
                pos = i + 1
                while pos < len(out) and out[pos][1].startswith("\\"):
                    pos += 1
                break
    out[pos:pos] = new
    return out


def with_record_labels(records, entries) -> List[Tuple[bytes, str]]:
    """``entries`` with one label per native, plus the labels ``records`` give."""
    out: List[Tuple[bytes, str]] = []
    have = set()
    for g, n in entries:
        if n.startswith("\\"):
            k = label_key(n)
            if k in have:
                continue
            have.add(k)
        out.append((g, n))
    # (a bytes look first, as record_order_entries: a text without '@(' lists nothing)
    texts = [
        po for po in records if po.type_code == TEXT_TC and po.subtype_size & 0x8 and b"@(" in po.body_raw
    ]
    for po in texts:
        try:
            labs = label_entries([po])
        except Exception:  # noqa: BLE001 -- an undecodable text lists no label
            _note_exc("genie_place.with_record_labels")
            continue
        for g, n in labs:
            if label_key(n) not in have:
                have.add(label_key(n))
                out.append((g, n))
    return out


def _in_template_copy(records, parents, k: int) -> bool:
    """Record ``k`` sits in a page's TEMPLATE copy (an ancestor linked to a '.<ctt')."""
    a = parents[k]
    while a is not None:
        po = records[a]
        if po.subtype_size & 0x1 and not po.subtype_size & 0x200 and po.class_flag:
            try:
                link = decode(po).link or ""
            except Exception:  # noqa: BLE001 -- a link that does not read: not a template's
                _note_exc("genie_place._in_template_copy")
                link = ""
            if link.lower().endswith(".<ctt"):
                return True
        a = parents[a]
    return False


def placeholder_entries(records) -> List[Tuple[bytes, str]]:
    """The bare link entries of the placeholder genies in ``records``."""
    from ctgedit.format import links

    marks = [
        k
        for k, po in enumerate(records)
        if po.type_code == 0xFFFF and not po.class_flag and b"_Library" in po.body_raw
    ]
    if not marks:
        return []
    parents = P.forest_parents(records)
    out, seen = [], set()
    for k in marks:
        lib = str(records[k].raw_properties().get("_Library") or "").strip()
        if not lib or _in_template_copy(records, parents, k):
            continue
        bare = links.strip_stamp(lib)
        if bare.lower() not in seen:
            seen.add(bare.lower())
            out.append((bytes(16), bare))
    return out


def with_placeholder_entries(records, entries) -> List[Tuple[bytes, str]]:
    """``entries`` plus the bare entry of every placeholder genie it doesn't list."""
    from ctgedit.format import links

    have = {n.lower() for _g, n in entries if not n.startswith("\\") and links.entry_stamp(n) is None}
    add = [(g, n) for g, n in placeholder_entries(records) if n.lower() not in have]
    return list(entries) + add if add else list(entries)


def instance_link_entries(el: "Element", records) -> List[Tuple[bytes, str]]:
    """Everything one placement adds to the page link table, in order: the element's entry,
    then per record the bare entry of a PLACEHOLDER genie the element holds (its '_Library'
    name with a zero GUID: LIVE, oracle chain5/element_placeholder, a placement of an
    element saved while a nested library was missing carries it) and the labels."""
    from ctgedit.format import links

    out = [link_table_entry(el)]
    for po in records:
        if po.type_code == 0xFFFF and not po.class_flag:
            lib = str(po.raw_properties().get("_Library") or "").strip()
            if lib:
                out.append((bytes(16), links.strip_stamp(lib)))
            continue
        out += label_entries([po])
    return out


def page_link_entries(records, resolver, carried=(), fresh=frozenset()) -> List[Tuple[bytes, str]]:
    """Link table entries for new page records."""
    from ctgedit.format import links

    by_ref = {}
    for g, n in carried or ():
        if not n.startswith("\\"):
            by_ref.setdefault(links.strip_stamp(n).lower(), (g, n))
    out = []
    bare = set()
    for po in records:
        if po.type_code == 0xFFFF and not po.class_flag:
            lib = str(po.raw_properties().get("_Library") or "").strip()
            if lib and links.strip_stamp(lib).lower() not in bare:
                bare.add(links.strip_stamp(lib).lower())
                out.append((bytes(16), links.strip_stamp(lib)))
            continue
        if po.subtype_size & 0x1:
            link = decode(po).link or ""
            ent = by_ref.get(links.strip_stamp(link).lower())
            if ent is not None and fresh and link.lower().endswith(".<ctm") and _entry_key(link) in fresh:
                ent = None
            if ent is not None:
                # (the name as this copy's link spells it, the carried guid and stamp)
                ent = (ent[0], link + ent[1][len(links.strip_stamp(ent[1])) :])
            else:
                ent = element_link_entry(link, resolver)
            if ent is not None:
                out.append(ent)
        out += label_entries([po])
    return out


def _entry_key(name: str) -> Tuple[str, str]:
    """(library, element), lower case, of a link token or entry name ('lib.elem.<ext',
    stamped or not): the LAST dot before the extension splits them."""
    from ctgedit.format import links
    from ctgedit.format.symbols import split_lib_element

    stem = links.strip_stamp(name or "").rpartition(".")[0]
    lib, elem = split_lib_element(stem)
    return lib.lower(), elem.lower()


def element_table_entries(lib_path: str, element: str) -> List[Tuple[bytes, str]]:
    """The library element's own import-table entries [(guid, name)] as the library stores
    them, the '\\\\native' labels left out: each quotes the version of a nested genie or
    symbol the element's stored copies were composed from. [] when the element has no
    table or does not read."""
    from ctgedit.format import links

    try:
        pb = open_library(lib_path).element(element)
        if pb is None or not pb.link_count:
            return []
        t = links.parse_link_table(pb.link_table_raw)
    except Exception:  # noqa: BLE001
        _note_exc("genie_place.element_table_entries")
        return []
    return [(g, n) for g, n in (t[1] if t else []) if not n.startswith("\\")]


def merged_nested_keys(el: "Element", resolver) -> frozenset:
    """(library, element) of nested genies whose every copy lines up after refresh."""
    recs = el.records
    good, bad = set(), set()
    k = 1
    while k < len(recs):
        po = recs[k]
        if not (po.class_flag and (po.subtype_size & 0x201) == 0x201):
            k += 1
            continue
        end = subtree_end(recs, k)
        key = None
        ok = False
        try:
            ref = reference_from_object(po)
            if ref is not None:
                key = (ref.library.lower(), ref.element.lower())
                res = resolver.resolve(ref) if resolver is not None else None
                if res is not None:
                    sub = load_element(res.library_path, ref.element)
                    ok = _aligns(sub.records, recs, k, end)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place.merged_nested_keys")
            ok = False
        if key is not None:
            (good if ok else bad).add(key)
        k = end
    return frozenset(good - bad)


def element_link_entry(link: str, resolver) -> Optional[Tuple[bytes, str]]:
    """The current link-table entry of the element a link token names
    ('lib.elem.<ext'; names as the link spells them), or None when it does
    not resolve on ``resolver``'s search path."""
    base, _, ext = (link or "").rpartition(".")  # 'lib.elem.<ctm': the type tag last
    lib, _, elem = base.rpartition(".")  # the LAST dot splits library / element
    if not lib:
        lib, elem = elem, lib
    try:
        for L in resolver.libraries(lib, ext.lstrip("<")):
            if L.element(elem) is not None:
                return current_link_entry(L.path, lib, elem)
    except Exception:  # noqa: BLE001
        _note_exc("genie_place.element_link_entry")
        pass
    return None


def used_link_entries(
    records, entries, keep_super: bool = True, values_only: bool = False
) -> List[Tuple[bytes, str]]:
    """``entries`` minus what nothing in ``records`` uses."""
    from ctgedit.format import geometry as G
    from ctgedit.format import links

    refs, nats = set(), set()
    for po in records:
        # a bytes look first: a record whose body holds no '@(' and no '.<' keeps nothing
        # alive but its link, and a linked one whose only '.<' is its link keeps just that.
        # Both marks are ASCII, so a double-byte code page can only add a false hit, which
        # takes the full path.
        body = po.body_raw
        dw = po.subtype_size
        has_at = b"@(" in body
        n_ref = body.count(b".<")
        if not dw & 0x1 and not has_at and not n_ref:
            continue
        if dw & 0x1 and not has_at and n_ref <= 1 and po.type_code != TEXT_TC:
            try:
                o = G.parse_geom_header(body).end
                n = int.from_bytes(body[o : o + 2], "little")
                lb = body[o + 2 : o + 2 + n]
                link = lb.decode(CODEC) if lb.count(b".<") == n_ref else None
            except Exception:  # noqa: BLE001  # swallow-ok: the full path below decodes it
                link = None
            if link is not None:
                if link:
                    refs.add(links.strip_stamp(link).lower())
                continue
        srcs = []
        labs = []  # (values_only) the values whose labels count
        if po.type_code == TEXT_TC:
            t = G.parse_text_tail(po)
            if t and t.get("text"):
                srcs.append(t["text"])
                labs.append(t["text"])
        if dw & 0x9:
            try:
                r = decode(po)
            except Exception:  # noqa: BLE001 -- an undecodable record keeps nothing alive
                _note_exc("genie_place.used_link_entries")
                r = None
            if r is not None and dw & 0x1 and r.link:
                refs.add(links.strip_stamp(r.link).lower())
            for li, e in enumerate((r.entries or ()) if r is not None else ()):
                for bi, bag in enumerate(e):
                    vals = [v for _k, v in bag.pairs]
                    srcs.extend(vals)
                    if li == 0 and bi == 1:
                        labs.extend(vals)
        for s in labs if values_only else srcs:
            nats.update(label_key(n) for n in natives(s) if n)
        for s in srcs:
            if ".<" in s:
                refs.add(links.strip_stamp(s.strip()).lower())
    out = []
    for g, nm in entries:
        if nm.startswith("\\"):
            if label_key(nm[2:]) in nats:
                out.append((g, nm))
        elif (keep_super and "@super" in nm.lower()) or links.strip_stamp(nm).lower() in refs:
            out.append((g, nm))
    return out


def record_order_entries(records, entries) -> List[Tuple[bytes, str]]:
    """``entries`` in the order ``records`` first use them."""
    from ctgedit.format import geometry as G
    from ctgedit.format import links

    if len(entries) < 2:
        return list(entries)
    first: Dict[str, int] = {}

    def note(key):
        if key not in first:
            first[key] = len(first)

    for po in records:
        dw = po.subtype_size
        tc = po.type_code
        body = po.body_raw
        if dw & 0x1:
            link = None
            if tc != TEXT_TC:
                # (the link follows the geometry header: used_link_entries' bytes look)
                try:
                    o = G.parse_geom_header(body).end
                    n = int.from_bytes(body[o : o + 2], "little")
                    link = body[o + 2 : o + 2 + n].decode(CODEC)
                except Exception:  # noqa: BLE001  # swallow-ok: the full decode reads it
                    link = None
            if not link or ".<" not in link:
                try:
                    link = decode(po).link
                except Exception:  # noqa: BLE001 -- a record whose link does not read
                    _note_exc("genie_place.record_order_entries")
                    link = None
            if link:
                note(links.strip_stamp(link).lower())
        if tc == 0xFFFF and not po.class_flag:
            lib = str(po.raw_properties().get("_Library") or "").strip()
            if lib:
                note(links.strip_stamp(lib).lower())
        elif tc == TEXT_TC and dw & 0x8 and b"@(" in body:
            try:
                for _g, nm in label_entries([po]):
                    note(label_key(nm))
            except Exception:  # noqa: BLE001 -- an undecodable text lists no label
                _note_exc("genie_place.record_order_entries")

    def key_of(nm):
        return label_key(nm) if nm.startswith("\\") else links.strip_stamp(nm).lower()

    slots = [k for k, (_g, nm) in enumerate(entries) if key_of(nm) in first]
    ordered = sorted((entries[k] for k in slots), key=lambda e: first[key_of(e[1])])
    out = list(entries)
    for k, e in zip(slots, ordered):
        out[k] = e
    return out


def add_link_entries(pb, entries, keep_older: bool = False, current=None) -> int:
    n = 0
    for e in entries:
        n += bool(add_link_entry(pb, e, keep_older=keep_older, current=current))
    return n


def same_version(a: Tuple[bytes, str], b: Tuple[bytes, str]) -> bool:
    """Two link-table entries of one reference quote the same element version: the same
    stamp and the same guid (each save of an element writes a new guid)."""
    from ctgedit.format import links

    return links.entry_stamp(a[1]) == links.entry_stamp(b[1]) and bytes(a[0]) == bytes(b[0])


def carried_entry_wins(
    listed: Tuple[bytes, str], entry: Tuple[bytes, str], cur: Optional[Tuple[bytes, str]] = None
) -> bool:
    """Does a carried ``entry`` replace the listed entry of the same reference?"""
    from ctgedit.format import links

    if same_version(listed, entry):
        return False
    if cur is not None and same_version(listed, cur):
        return not same_version(entry, cur)
    new_s = links.entry_stamp(entry[1])
    old_s = links.entry_stamp(listed[1])
    return new_s is not None and old_s is not None and new_s < old_s


def current_entry_lookup(resolver):
    """A function: entry name ('lib.elem.<ext#stamp') -> the current entry of the element
    it names on ``resolver``'s search path (:func:`element_link_entry`), None when it does
    not resolve. Each reference is looked up once."""
    from ctgedit.format import links

    memo: dict = {}

    def get(name: str):
        ref = links.strip_stamp(name)
        k = ref.lower()
        if k not in memo:
            memo[k] = element_link_entry(ref, resolver) if resolver is not None else None
        return memo[k]

    return get


def add_link_entry(pb, entry: Tuple[bytes, str], keep_older: bool = False, current=None) -> bool:
    """Append ``entry`` to the link table unless its reference is already listed."""
    from ctgedit.format import links

    t = links.parse_link_table(pb.link_table_raw)
    area, ents = t if t is not None else ((pb.area_code or "    "), [])
    if entry[1].startswith("\\"):
        # (labels: one per native, case-insensitively: label_key)
        if any(n.startswith("\\") and label_key(n) == label_key(entry[1]) for _g, n in ents):
            return False
    else:
        want = links.strip_stamp(entry[1]).lower()
        at = next(
            (
                k
                for k, (_g, n) in enumerate(ents)
                if not n.startswith("\\") and links.strip_stamp(n).lower() == want
            ),
            None,
        )
        if at is not None:
            if not keep_older:
                return False
            listed = ents[at]
            cur = None
            if current is not None and not same_version(listed, entry):
                cur = current(entry[1])
            if not carried_entry_wins(listed, entry, cur):
                return False
            ents = list(ents)
            base = links.strip_stamp(ents[at][1])
            ents[at] = (entry[0], base + entry[1][len(links.strip_stamp(entry[1])) :])
            raw, count = links.serialize_link_table(area, ents)
            pb.link_table_raw = raw
            pb.link_count = count
            return True
    ents = list(ents) + [entry]
    raw, count = links.serialize_link_table(area, ents)
    pb.link_table_raw = raw
    pb.link_count = count
    return True


# =========================================================================== copy / paste
subtree_end = _forest.subtree_end


def instance_roots(records) -> List[int]:
    """Positions of the placed genie instance roots in a record list."""
    return [k for k, po in enumerate(records) if po.class_flag and (po.subtype_size & 0x201) == 0x201]


def _refs_an(r: Rec) -> bool:
    """The record declares the implicit AN parameter or a template uses %AN%."""
    for e in r.entries or ():
        if len(e) < 3:
            continue
        if e[0].get("AN") is not None:
            return True
        for key, t in e[2].pairs:
            if "%" in t and any(n.lower() == "an" for n in template_refs(Bag([(key, t)]))):
                return True
    return False


def resubstitute_an(
    records: Sequence[PageObject], old_an, new_an, page_name: str = ""
) -> Optional[List[PageObject]]:
    """Re-substitute %AN% in a copied genie instance whose root AN changed."""
    old_an, new_an = str(old_an), str(new_an)
    if old_an == new_an or not records:
        return None
    decoded: List[Optional[Rec]] = []
    uses = False
    for po in records:
        r = None
        if po.subtype_size & 0x8 and getattr(po, "raw_payload", None) is None:
            try:
                r = decode(po)
            except Exception:  # noqa: BLE001 -- an undecodable record is kept as is
                _note_exc("genie_place.resubstitute_an")
                r = None
        decoded.append(r)
        uses = uses or (r is not None and _refs_an(r))
    if not uses:
        return None
    from ctgedit.format import writer as W

    sp_old = {"an": old_an, "page": page_name}
    sp_new = {"an": new_an, "page": page_name}
    out = list(records)
    changed = False
    for k, (po, r) in enumerate(zip(records, decoded)):
        if r is None or not r.entries:
            continue
        r.slots = None  # re-encode the stored slot bytes verbatim
        try:
            if encode(r) != W.object_payload(po):
                continue  # not a strict round trip: never rewrite it
        except Exception:  # noqa: BLE001
            _note_exc("genie_place.resubstitute_an")
            continue
        ents = r.entries
        before = [e[1].copy() if len(e) >= 2 else Bag([]) for e in ents]
        dirty = False
        top = ents[-1]
        # the PLACED level (the instance's bindings: no template of its own for
        # AN) keeps the AN the instance was pasted with -- a nested genie's
        # constant 'AN' parameter (list rows: AN '2' from template '2') is
        # re-derived below from its template instead
        if len(top) >= 3 and top[1].get("AN") == old_an and top[2].get("AN") is None:
            top[1].set("AN", new_an)
            dirty = True
        for li in range(len(ents) - 2, -1, -1):
            ent, outer = ents[li], ents[li + 1]
            if len(ent) < 3 or len(outer) < 2:
                continue
            for key, tmpl in ent[2].pairs:
                if "%" not in tmpl:
                    continue
                a = substitute(tmpl, before[li + 1], outer[0], key, sp_old)
                b = substitute(tmpl, outer[1], outer[0], key, sp_new)
                if a == b or ent[1].get(key) != a:
                    continue  # unchanged, or a value we do not reproduce
                ent[1].set(key, b)
                dirty = True
        if dirty:
            out[k] = to_po(encode(r))
            changed = True
    return out if changed else None


def pasted_link_entries(records, resolver=None, carried=()) -> List[Tuple[bytes, str]]:
    """The link table entries pasted records need on the target page."""
    from ctgedit.format import links

    by_ref = {}
    for g, n in carried or ():
        if not n.startswith("\\"):
            by_ref.setdefault(links.strip_stamp(n).lower(), (g, n))
    out: List[Tuple[bytes, str]] = []
    for po in records:
        if po.subtype_size & 0x1:
            try:
                link = decode(po).link or ""
            except Exception:  # noqa: BLE001
                _note_exc("genie_place.pasted_link_entries")
                link = ""
            ref = links.strip_stamp(link).lower()
            ent = by_ref.get(ref)
            if ent is None and resolver is not None and link:
                ent = element_link_entry(link, resolver)
            if ent is not None:
                out.append(ent)
        try:
            out += label_entries([po])
        except Exception:  # noqa: BLE001 -- an undecodable text lists no label
            _note_exc("genie_place.pasted_link_entries")
            pass
    have = {links.strip_stamp(n).lower() if not n.startswith("\\") else label_key(n) for _g, n in out}
    # (a carried label only while a caption or a value bag holds it: a genie element's own
    # table lists its members' template labels too, which a page never lists)
    for g, n in used_link_entries(records, list(carried or ()), keep_super=False, values_only=True):
        key = links.strip_stamp(n).lower() if not n.startswith("\\") else label_key(n)
        if key not in have:
            have.add(key)
            out.append((g, n))
    return out


def build_instance_ex(
    page_path: str,
    lib_path: str,
    element: str,
    x: float,
    y: float,
    params: Optional[dict] = None,
    *,
    origin: str = "topleft",
    page_name: Optional[str] = None,
    used_handles: Optional[set] = None,
    resolver=None,
    opts: Options = Options(),
) -> dict:
    """build_instance + everything a caller needs: {'records': [PageObject],
    'payloads': [bytes], 'link_entry': (guid16, name), 'element': Element,
    'params': declared params (name -> default)}."""
    el = load_element(lib_path, element, compose=True)
    hk = local_for_topleft(el, x, y) if origin == "topleft" else (x, y)
    used = set(used_handles) if used_handles is not None else page_handles(page_path)
    handles = paste_handles(el.records, used)
    if page_name is None:
        page_name = os.path.splitext(os.path.basename(page_path))[0]
    if resolver is None:
        try:
            from ctgedit.format.symbols import SymbolResolver

            resolver = SymbolResolver.for_page(page_path)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place.build_instance_ex")
            resolver = None
    recs = build_instance_records(
        el, hk, params, handles=handles, page_name=page_name, resolver=resolver, opts=opts
    )
    from ctgedit.format import writer

    return dict(
        records=recs,
        payloads=[writer.object_payload(po) for po in recs],
        link_entry=link_table_entry(el),
        link_entries=instance_link_entries(el, recs),
        element=el,
        params=declared_params(el),
    )


def build_instance(
    page_path: str,
    lib_path: str,
    element: str,
    x: float,
    y: float,
    params: Optional[dict] = None,
    *,
    origin: str = "topleft",
    page_name: Optional[str] = None,
) -> List[bytes]:
    """Payload bytes of every record of a new instance of ``element``."""
    return build_instance_ex(page_path, lib_path, element, x, y, params, origin=origin, page_name=page_name)[
        "payloads"
    ]


def place_on_page(page_path: str, out_path: str, placements) -> List[Tuple[int, int]]:
    """Write a COPY of ``page_path`` to ``out_path`` with each placement
    (lib_path, element, x, y, params) appended + its link-table entry.
    Returns [(first record index, record count)]. Never writes page_path."""
    from ctgedit.format import writer

    assert os.path.abspath(out_path) != os.path.abspath(page_path)
    pc = parse_ctg(page_path)
    objs = pc.page.objects
    used = {G.get_handle(po) for po in objs if G.get_handle(po) is not None}
    spans = []
    for lib_path, element, x, y, params in placements:
        ex = build_instance_ex(page_path, lib_path, element, x, y, params, used_handles=used)
        used |= {G.get_handle(po) for po in ex["records"]}
        spans.append((len(objs), len(ex["records"])))
        for blob in ex["payloads"]:
            objs.append(_parse_object(len(objs), 0, len(blob), 0, blob))
        add_link_entries(pc.page, ex["link_entries"])
    pc.page.object_count = len(objs)
    blk = pc.container.block("page")
    blk.data = writer.serialize_page_block(pc.page, blk.data)
    pc.container.relayout()
    with open(out_path, "wb") as fh:
        fh.write(pc.container.to_bytes())
    return spans


# =========================================================================== symbols
def _group_root(el: Element) -> bool:
    return el.records[0].type_code == GROUP_TC and bool(el.records[0].class_flag)


def places_as_forest(lib_path: str, element: str) -> bool:
    """True when a linked placement of the symbol ``element`` of ``lib_path`` is an
    instance forest: its root is a group, also the one CtDraw's load holds a bare
    one-object symbol under (element.keeps_bare_root). Only a bitmap, a bar or a trend
    root is placed as one record."""
    return _group_root(load_element(lib_path, element))


def place_symbol(
    page_path: str,
    lib_path: str,
    element: str,
    x: int,
    y: int,
    linked: bool = True,
    used_handles: Optional[set] = None,
    handles: Optional[List[int]] = None,
    library_name: Optional[str] = None,
    element_name: Optional[str] = None,
) -> dict:
    """Place a library symbol."""
    from ctgedit.format import factory, writer
    from ctgedit.format.symbols import SymbolResolver

    stem = library_name or os.path.splitext(os.path.basename(lib_path))[0]
    ext = os.path.splitext(lib_path)[1].lstrip(".").lower()
    res = SymbolResolver.for_page(lib_path)
    used = set(used_handles) if used_handles is not None else page_handles(page_path)
    it = iter(handles) if handles is not None else None
    next_free = handle_allocator(used)

    def _h():
        return next(it) if it is not None else next_free()

    el0 = load_element(lib_path, element)
    # LIVE (B5): the link token keeps the names as the CALLER spelled them
    el0.library = stem
    if element_name:
        el0.element = element_name
    if linked and _group_root(el0):
        # LIVE: a group-root symbol pastes as an instance forest (genie
        # recipe, root dwFlags 0x1), NOT as one tc 7 linked record
        hs = list(handles) if handles is not None else paste_handles(el0.records, used)
        recs = build_instance_records(el0, local_for_topleft(el0, x, y), {}, handles=hs, resolver=res)
        return dict(
            records=recs,
            payloads=[writer.object_payload(po) for po in recs],
            link_entry=link_table_entry(el0),
            link_entries=instance_link_entries(el0, recs),
        )
    if linked:
        info = P.element_info(res, stem, element, ext)
        if info is None:
            raise KeyError(element)
        nl, nt, nr, nb = info["N"]
        rect = (int(x), int(y), int(x) + (nr - nl), int(y) + (nb - nt))
        h = _h()
        po = factory.create_object(
            "symbol", rect, symbol=(stem, element_name or element, ext), handle=h, link_info=info
        )
        return dict(
            records=[po],
            payloads=[writer.object_payload(po)],
            link_entry=link_table_entry(el0),
            link_entries=[link_table_entry(el0)],
        )
    el = load_element(lib_path, element)
    copies = [to_po(writer.object_payload(po)) for po in el.records]
    cache = {}

    def link_info(po):
        from ctgedit.format.symbols import reference_from_object

        ref = reference_from_object(po)
        if ref is None:
            return None
        key = (ref.library.lower(), ref.element.lower(), ref.ext)
        if key not in cache:
            cache[key] = P.element_info(res, ref.library, ref.element, ref.ext)
        return cache[key]

    dx, dy = int(x) - el.N[0], int(y) - el.N[1]
    # LIVE: the unlinked copy's ROOT drops the element's T(-e) slot 2 (all
    # three slots identity, Down1 / WMFPUMPS / BOOKS.coverx)
    # LIVE (unlinked example.Log_sim, a STALE element: Local T(-707,-199),
    # native rect at 807,299): the copy's root takes identity slots whenever
    # its Local is a plain translation, and a bitmap's class-native rect = its
    # rect1 (the element normalisation of a single-object root). Dropped BEFORE
    # the move: the move derives the members' Totals from the root's slots (with
    # the T(-e) still there a symbol set under the root would take Total T(-e) o its
    # slot 2, drawn e off)
    (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = P.read_slots(copies[0].body_raw)[0]
    plain = (m1 in (0, 1)) and L[:4] == (1.0, 0.0, 0.0, 1.0)
    if plain and (m1 or m2):
        P.write_slots(copies[0], [(0, P.IDENT)] * 3)
        writer.refresh_object_bags(copies[0])
    natives = {
        k: tuple(G.norm_rect(G.parse_geom_header(po.body_raw).rect1))
        for k, po in enumerate(copies)
        if po.type_code == METAFILE_TC and not (po.subtype_size & 0x1)
    }
    P.translate_subtrees(copies, dx, dy, link_info)
    # LIVE (unlinked WMFPUMPS copy): a MetaFile (tc 9) keeps its class-native
    # rect in step like an embedded bitmap -- placement.has_native_rect
    # misses tc 9, so shift it here
    for k, old in natives.items():
        po = copies[k]
        o = P.class_section_offset(po)
        body = bytearray(po.body_raw)
        if tuple(struct.unpack_from("<4h", body, o)) == old:
            l, t, rr, b = old
            struct.pack_into("<4h", body, o, l + dx, t + dy, rr + dx, b + dy)
            po.body_raw = bytes(body)
            writer.refresh_object_bags(po)
    if not copies[0].class_flag and P.has_native_rect(copies[0]):
        r1 = tuple(G.norm_rect(G.parse_geom_header(copies[0].body_raw).rect1))
        if P.native_rect(copies[0]) not in (None, r1):
            o = P.class_section_offset(copies[0])
            body = bytearray(copies[0].body_raw)
            struct.pack_into("<4h", body, o, *r1)
            copies[0].body_raw = bytes(body)
            writer.refresh_object_bags(copies[0])
    stack = []
    for po in copies:  # fresh handles; inputs take the owner's
        while stack and stack[-1][1] <= 0:
            stack.pop()
        owner = None
        if stack:
            owner = stack[-1][0]
            stack[-1][1] -= 1
        if po.type_code in INPUT_TCS and owner is not None:
            h = owner
            if it is not None:
                next(it)
        else:
            h = _h()
        G.set_handle(po, h)
        if po.class_flag:
            stack.append([h, po.class_flag])
    copies[0].an_str = "-1"
    # the copy holds the element's stored nested copies (its load merges only the genie
    # copies that still line up with their genie; nested symbols are never derived again):
    # their entries are the element's own, the versions it holds
    entries = page_link_entries(
        copies, res, carried=element_table_entries(lib_path, element), fresh=merged_nested_keys(el, res)
    )
    return dict(
        records=copies,
        payloads=[writer.object_payload(po) for po in copies],
        link_entry=None,
        link_entries=entries,
    )


# =========================================================================== page-load refresh
def refresh_instance(
    inst: List[PageObject],
    resolver,
    page_name: str = "",
    used_handles: Optional[set] = None,
    parent_chain=None,
    element: Optional["Element"] = None,
    bindings: Optional[dict] = None,
    shells: bool = False,
    only=None,
    info: Optional[dict] = None,
    outside_handles: Optional[set] = None,
    opts: "Options" = None,
    handles: Optional[Sequence[int]] = None,
) -> Optional[List[PageObject]]:
    """Recompose a placed genie from its element, as CtDraw does on page load."""
    op = _op_enter()  # the load and the build share one operation
    try:
        return _refresh_instance(
            inst,
            resolver,
            page_name,
            used_handles,
            parent_chain,
            element,
            bindings,
            shells,
            only,
            info,
            outside_handles,
            opts or _BUILD_OPTS,
            handles,
        )
    finally:
        _op_leave(op)


def _refresh_instance(
    inst,
    resolver,
    page_name,
    used_handles,
    parent_chain,
    element,
    bindings,
    shells=False,
    only=None,
    info=None,
    outside_handles=None,
    opts=None,
    handles=None,
) -> Optional[List[PageObject]]:
    opts = dataclasses.replace(opts or _BUILD_OPTS, recompose=True)
    root = inst[0]
    if element is not None:
        el = _copy.copy(element)
    else:
        try:
            ref = reference_from_object(root)
            res = resolver.resolve(ref)
            el = load_element(res.library_path, ref.element, compose=True, only=only)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place._refresh_instance")
            return None
    if only is not None:
        if not getattr(el, "affected", False):
            return None
        if info is not None:
            info["spans"] = list(getattr(el, "affected_spans", ()))
    r0 = decode(root)
    base, _, _ext = (r0.link or "").rpartition(".")
    lib, _, elem = base.rpartition(".")  # the LAST dot
    if not lib:
        lib, elem = elem, lib
    el.library, el.element = lib or el.library, elem or el.element
    (m1, L, _o, _n), _s2, _s3 = P.read_slots(root.body_raw)[0]
    local = (L[4], L[5]) if m1 == 1 else (m1, tuple(L))
    N = len(r0.entries) - 1 if r0.entries else 0
    if opts.into_genie_element:
        # the element form: the placed level sits under the element level; its typed
        # bindings are its templates (b2), the values (b1) their substitution
        placed = N - 1 if N >= 2 else None
        if placed is not None and placed >= 1:
            lvl = r0.entries[placed]
            root_bag = lvl[2].copy() if lvl[2].pairs else lvl[1].copy()
        elif N == 1:
            # a copy still in the page form (the element had no parameters): its bindings
            # are the placed level's values
            root_bag = r0.entries[1][1].copy()
        else:
            root_bag = Bag([])
    else:
        root_bag = r0.entries[N][1].copy() if (r0.entries and N >= 1) else Bag([])
        # a declared parameter with a default that the stored bindings lack is appended
        # with its default (LIVE, CtDraw's load of a page: a genie whose
        # element gained parameters got them after its stored bindings, and its
        # members too)
        try:
            have = {kk.lower() for kk, _v in root_bag.pairs}
            for name, dflt in declared_params(el).items():
                if dflt and name.lower() != "an" and name.lower() not in have:
                    root_bag.pairs.append((name, dflt))
                    have.add(name.lower())
        except Exception:  # noqa: BLE001 -- the stored bindings alone then
            _note_exc("genie_place._refresh_instance/defaults")
    for k, v in (bindings or {}).items():
        root_bag.set(k, v)
    if opts.dialog_an and G.get_handle(root) is not None:
        set_dialog_an(root_bag, G.get_handle(root))
    if handles is not None:
        # the caller allocated them with the page's other re-composed instances
        # (page_instance_handles: CtDraw deletes every unlinked record first, page-wide)
        hs = list(handles)
    else:
        # each element record keeps the handle of the stored record it links with
        # (AN number + class), whether or not the structure lines up: an
        # aligned instance whose AN strings no longer name its element records gets new
        # objects there too (LIVE, a page's genie: records with stale AN
        # strings took new handles and AN strings in CtDraw's load); keeping handles
        # by POSITION would renumber every member after a first difference
        if outside_handles is not None:
            outside = set(outside_handles)
        else:
            outside = set(used_handles or ()) - {G.get_handle(po) for po in inst}
        outside.discard(None)
        hs = ctdraw_instance_handles(inst, el.records, outside)
    keep = None
    if only is not None:
        keep = {0}
        for a, b in getattr(el, "affected_spans", ()):
            keep.update(range(a, b))
    # a linked member keeps its own Local (a stored slot 1 on a member record)
    mlocals = {}
    try:
        for j, s_ in enumerate(link_instance(inst, el.records)):
            if j and s_ is not None:
                s1 = P.read_slots(inst[s_].body_raw)[0][0]
                if s1[0]:
                    mlocals[j] = (s1[0], tuple(s1[1]))
    except Exception:  # noqa: BLE001 -- the element's placement then
        _note_exc("genie_place._refresh_instance/locals")
        mlocals = {}
    out = build_instance_records(
        el,
        local,
        None,
        handles=hs,
        page_name=page_name,
        root_bag=root_bag,
        opts=opts,
        resolver=resolver,
        parent_chain=parent_chain,
        shells=shells,
        keep=keep,
        member_locals=mlocals or None,
    )
    if out and out[0].an_str != root.an_str:
        # the root keeps the element link it was placed with (the roots always
        # match): LIVE, CtDraw's load of a page re-composed an
        # instance whose element had gained a wrapper group and kept the root's '0',
        # the old element root's handle (a template root does the same, template_place)
        out[0] = _copy.copy(out[0])
        out[0].an_str = root.an_str
    lock = _codes.DWF.LOCK
    if out and root.subtype_size & lock and not out[0].subtype_size & lock:
        # Edit > Lock is the placement's (LIVE: CtDraw's load kept 0x20d on
        # re-composed genies)
        out[0] = _copy.copy(out[0])
        out[0].subtype_size |= lock
    return _keep_root_properties(out, r0)


def match_instance(inst: Sequence[PageObject], el_records: Sequence[PageObject]) -> List[Optional[int]]:
    """For each element record, the placed record it's linked with, or None."""
    ep = P.forest_parents(el_records)
    ipar = P.forest_parents(inst)
    kids: Dict[Optional[int], List[int]] = {}
    by_key: Dict[tuple, List[int]] = {}

    def gbit(po) -> int:
        return po.subtype_size & 0x200

    for s in range(1, len(inst)):
        kids.setdefault(ipar[s], []).append(s)
        by_key.setdefault((inst[s].an_str, inst[s].type_code, gbit(inst[s])), []).append(s)
    out: List[Optional[int]] = [None] * len(el_records)
    if not el_records or not inst:
        return out
    out[0] = 0
    taken = {0}
    linked_parent: Dict[int, int] = {0: 0}  # instance index -> element index
    for k in range(1, len(el_records)):
        key = (str(G.get_handle(el_records[k])), el_records[k].type_code, gbit(el_records[k]))
        pk = ep[k]
        pm = out[pk] if pk is not None else None
        hit = None
        if pm is not None:
            hit = next(
                (
                    s
                    for s in kids.get(pm, ())
                    if s not in taken and (inst[s].an_str, inst[s].type_code, gbit(inst[s])) == key
                ),
                None,
            )
        if hit is None:
            hit = next(
                (
                    s
                    for s in by_key.get(key, ())
                    if s not in taken
                    and (
                        ipar[s] is None
                        or ipar[s] not in linked_parent
                        or linked_parent[ipar[s]] == pk
                        or (pk is not None and pm is None)
                    )
                ),
                None,
            )
        if hit is not None:
            out[k] = hit
            taken.add(hit)
            linked_parent[hit] = k
    for k in range(1, len(el_records)):
        if out[k] is not None or ep[k] is None or out[ep[k]] is None:
            continue
        tc, g = el_records[k].type_code, gbit(el_records[k])
        hit = next(
            (
                s
                for s in kids.get(out[ep[k]], ())
                if s not in taken and inst[s].type_code == tc and gbit(inst[s]) == g
            ),
            None,
        )
        if hit is not None:
            out[k] = hit
            taken.add(hit)
    return out


def root_key_kept(key: str) -> bool:
    """Does CtDraw keep ``key`` as an own property of a genie instance root?"""
    k = key.upper()
    return k.startswith("!") or k in ("__AN_NAME__", "NAME") or k.startswith("AI_")


def _keep_root_properties(out: List[PageObject], r0: Rec) -> List[PageObject]:
    """Keep the instance root's own properties (name, alarm indicator) through a recompose."""
    if not out or not r0.entries:
        return out
    own = [Bag([(k, v) for k, v in bag.pairs if root_key_kept(k)]) for bag in r0.entries[0]]
    if not (own[1].pairs or own[2].pairs):
        return out
    rn = decode(out[0])
    if not rn.entries:
        return out
    for bi in (1, 2):
        have = {k.lower() for k, _v in rn.entries[0][bi].pairs}
        rn.entries[0][bi].pairs += [(k, v) for k, v in own[bi].pairs if k.lower() not in have]
    root = to_po(encode(rn))
    out = [root] + list(out[1:])
    if CS.is_dynamic(root, dict(rn.entries[0][1].pairs)) and not (root.subtype_size & 0x10):
        root.subtype_size |= 0x10
        parents = P.forest_parents(out)
        for k in range(1, len(out)):
            p = parents[k]
            if p is not None and out[p].type_code in CS.INHERIT_TCS and out[p].subtype_size & 0x10:
                out[k].subtype_size |= 0x10
    return out


# =========================================================================== CLI
def _main(argv=None):
    """genie_place.py PAGE LIB ELEMENT X Y [name=value ...] --out OUT.ctg
    Writes a COPY of PAGE with the instance appended (+ link-table entry)."""
    import argparse

    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("page")
    ap.add_argument("lib")
    ap.add_argument("element")
    ap.add_argument("x", type=float)
    ap.add_argument("y", type=float)
    ap.add_argument("params", nargs="*", help="name=value")
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbol", action="store_true", help="place a linked .ctl symbol instead")
    a = ap.parse_args(argv)
    params = dict(p.split("=", 1) for p in a.params)
    if a.symbol:
        from ctgedit.format import writer

        pc = parse_ctg(a.page)
        used = {G.get_handle(po) for po in pc.page.objects if G.get_handle(po) is not None}
        ex = place_symbol(a.page, a.lib, a.element, int(a.x), int(a.y), used_handles=used)
        for blob in ex["payloads"]:
            pc.page.objects.append(_parse_object(len(pc.page.objects), 0, len(blob), 0, blob))
        add_link_entries(pc.page, ex["link_entries"])
        pc.page.object_count = len(pc.page.objects)
        blk = pc.container.block("page")
        blk.data = writer.serialize_page_block(pc.page, blk.data)
        pc.container.relayout()
        with open(a.out, "wb") as fh:
            fh.write(pc.container.to_bytes())
        print("placed symbol", a.element, "->", a.out)
        return
    el = load_element(a.lib, a.element)
    print("parameters (name: default):", dict(declared_params(el)))
    spans = place_on_page(a.page, a.out, [(a.lib, a.element, a.x, a.y, params)])
    print("placed", a.element, "records", spans, "->", a.out)


if __name__ == "__main__":
    _main()


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("genie_place.elements", _MEMO, clear=clear_memo, on=("refresh", "tests"))
_caches.register("genie_place.nested_geometry", _GEOM_MEMO, on=("refresh", "tests"))


# =========================================================================== placeholders
def placeholder_record(root: PageObject, handle: int, link: str) -> PageObject:
    """CtDraw's placeholder record for a library object whose library won't open."""
    from .symbolset import blank_record

    r0 = decode(root)
    po = blank_record("symbol_set_dummy")
    r = decode(po)
    (m1, L, _o, _n), _s2, _s3 = P.read_slots(root.body_raw)[0]
    L = tuple(L) if m1 else P.IDENT
    l, t, rr, b = r0.rect1
    vals = [("_Library", link)]
    vals += [("_Transform_%s" % c, "%f" % v) for c, v in zip("ABCDHK", L)]
    if m1:
        vals.append(("_Transform_Byte", str(int(m1))))
    genie = bool(root.subtype_size & 0x200)
    r.cf = 0
    r.dw = (root.subtype_size & ~0x201) | 0x2
    r.an = "-1"
    r.handle = handle
    r.name = ""
    r.slots = [(0, P.IDENT), (0, P.IDENT), (0, P.IDENT)]
    r.anchor, r.rect1, r.rect2 = (l, b - 1), (l, t, rr, b), (l, t, rr, b)
    r.link = None
    r.entries = [[Bag([], 0), Bag(vals, 10), Bag(list(vals) if genie else [], 0)]]
    if genie and r0.entries and len(r0.entries) >= 2:
        r.entries.append([Bag(list(b_.pairs), 0) for b_ in r0.entries[-1]])
    r.cls = struct.pack("<6h", l, t, rr, b, l, b - 1)
    # the colour state is the root's, and the style's four colour words its colour
    if r0.cs:
        r.cs = r0.cs
        if r.style and len(r.style) >= 22 and len(r0.cs) >= 8:
            r.style = r.style[:6] + r0.cs[4:8] * 4 + r.style[22:]
    return to_po(encode(r))


def placeholder_genie(po: PageObject):
    """(reference, Local) of a placeholder genie record."""
    from ctgedit.format.symbols import reference_from_body, reference_from_symbol_value

    if po.type_code != 0xFFFF or po.class_flag:
        return None
    props = po.raw_properties()
    lib = str(props.get("_Library") or "").strip()
    if not lib:
        return None
    try:
        m6 = tuple(
            float(props.get("_Transform_%s" % k, v))
            for k, v in (("A", 1), ("B", 0), ("C", 0), ("D", 1), ("H", 0), ("K", 0))
        )
        mode = int(str(props.get("_Transform_Byte") or "0").strip() or 0)
    except ValueError:
        return None
    ref = (
        reference_from_body(lib.encode("latin-1", "replace"))
        if ".<ct" in lib.lower()
        else reference_from_symbol_value(lib)
    )
    if ref is None or (ref.ext or "").lower() != "ctm":
        return None
    if m6[:4] == (1.0, 0.0, 0.0, 1.0):
        return ref, (m6[4], m6[5])
    if not mode:
        # (no mode byte: translation 0x1, a scale 0x2, a turn or shear 0x6)
        a, b, c, d, h, k = m6
        mode = (0x01 if (h or k) else 0) | (0x06 if (b or c) else 0x02)
    return ref, (mode, m6)


def with_placeholder_template(root: PageObject, marker: PageObject) -> PageObject:
    """The root of a genie placed from a placeholder, with the marker's template bag."""
    r = decode(root)
    m = decode(marker)
    if not r.entries or not m.entries or len(r.entries) != len(m.entries):
        return root
    top = m.entries[-1][2]
    if list(r.entries[-1][2].pairs) == list(top.pairs):
        return root
    r.entries[-1][2] = top.copy()
    return to_po(encode(r))


#: the keys of a PLACEHOLDER genie's own bags that say where the genie goes (its library and
#: Local), not what the object was (:func:`placeholder_record`)
_PLACEHOLDER_KEY = re.compile(r"_(library|transform_[a-z]+)$", re.I)


def _marker_name_into(r: "Rec", m: "Rec") -> None:
    """Put a placeholder's object name back on the placed genie root."""
    if m.name:
        r.name = m.name
    if not r.entries or not m.entries:
        return
    for bi in (1, 2):
        add = [(k, v) for k, v in m.entries[0][bi].pairs if not _PLACEHOLDER_KEY.match(k)]
        if not add:
            continue
        bag = r.entries[0][bi]
        have = {k.lower() for k, _v in bag.pairs}
        new = [(k, v) for k, v in add if k.lower() not in have]
        if new:
            r.entries[0][bi] = Bag(new + list(bag.pairs), bag.ov)


def placeholder_root(root: PageObject, marker: PageObject) -> PageObject:
    """The root of a genie placed again from PLACEHOLDER ``marker`` in the PAGE form, as
    CtDraw's page and template loads write it: its top level's template bag the marker's
    (:func:`with_placeholder_template`) and the marker's object name back
    (:func:`_marker_name_into`)."""
    root = with_placeholder_template(root, marker)
    r = decode(root)
    m = decode(marker)
    before = (r.name, [list(b.pairs) for b in (r.entries[0] if r.entries else [])])
    _marker_name_into(r, m)
    if (r.name, [list(b.pairs) for b in (r.entries[0] if r.entries else [])]) == before:
        return root
    return to_po(encode(r))


def place_page_placeholders(
    records,
    hits,
    *,
    outside,
    resolver,
    page_name: str = "",
    opts_for=None,
    chain_for=None,
    where: str = "genie_place.place_page_placeholders",
):
    """Place placeholder genies again in page form, as CtDraw's page load does."""
    if not hits:
        return list(records), [], []
    try:
        # (a marker links to no record of the genie: every record is a new one)
        all_hs = page_instance_handles([([], el.records) for _i, el in hits], outside)
    except Exception:  # noqa: BLE001 -- each then takes the lowest free ones
        _note_exc(where + "/handles")
        used = set(outside)
        all_hs = []
        for _i, el in hits:
            hs = paste_handles(el.records, used)
            used |= set(hs)
            all_hs.append(hs)
    order = list(records)
    parents = None
    entries: List[Tuple[bytes, str]] = []
    placed = []
    for (i, el), handles in reversed(list(zip(hits, all_hs))):
        marker = records[i]
        got = placeholder_genie(marker)
        if got is None:
            continue
        params = placeholder_params(marker)
        try:
            if chain_for is not None:
                chain = chain_for(i)
            else:
                if parents is None:
                    parents = P.forest_parents(records)
                chain, a = [], parents[i]
                while a is not None:
                    chain.append(P.link_of(records[a]))
                    a = parents[a]
            opts = opts_for(i, el, params) if opts_for is not None else None
            kw = {"opts": opts} if opts is not None else {}
            built = build_instance_records(
                el,
                got[1],
                params,
                handles=handles,
                page_name=page_name,
                resolver=resolver,
                parent_chain=chain or None,
                **kw,
            )
        except Exception:  # noqa: BLE001 -- that marker stays as it is
            _note_exc(where)
            continue
        if not built:
            continue
        built[0] = placeholder_root(built[0], marker)
        order[i : i + 1] = built
        CS.derive_dynamic_flags(order, i)
        entries[:0] = instance_link_entries(el, built)
        placed.append(i)
    return order, entries, sorted(placed)


def placeholder_params(po: PageObject) -> Dict[str, str]:
    """The parameter values a PLACEHOLDER genie record keeps (its binding level as stored,
    a template over a value: raw_properties' order), without its '_*' bags and its AN."""
    props = po.raw_properties()
    return {k: v for k, v in props.items() if not str(k).startswith(("_", "!")) and str(k).upper() != "AN"}


#: (library file, element) lower case of the elements whose placeholders are being placed
#: on this thread (a genie whose placeholder names itself, or one that nests it: placed once)
_PLACING = threading.local()


def place_element_placeholders(el: "Element", resolver) -> int:
    """Place an element's placeholder genies again."""
    recs = el.records
    if len(recs) < 2 or not any(po.type_code == 0xFFFF for po in recs):
        return 0
    stack = getattr(_PLACING, "keys", None)
    if stack is None:
        stack = _PLACING.keys = set()
    me = (os.path.normcase(os.path.abspath(el.lib_path)), el.element.lower())
    if me in stack:
        return 0
    parents = P.forest_parents(recs)
    hits = []
    for k, po in enumerate(recs):
        if not k:
            continue
        got = placeholder_genie(po)
        if got is None:
            continue
        a, inside = parents[k], False
        while a is not None and a != 0:
            if recs[a].subtype_size & 0x1:
                inside = True
                break
            a = parents[a]
        if inside:
            continue
        ref, local = got
        try:
            hit = resolver.resolve(ref) if resolver is not None else None
            if resolver is not None:
                # (the element memo then knows the answer: a library that opens later, or
                # goes, gives the element's load again)
                _note(_cur_op(), "probe", resolver, ref)
        except Exception:  # noqa: BLE001
            _note_exc("genie_place.place_element_placeholders/resolve")
            hit = None
        if hit is None:
            continue
        hits.append((k, ref, local, hit.library_path))
    if not hits:
        return 0
    stack.add(me)
    try:
        subs = []
        for k, ref, local, path in hits:
            key = (os.path.normcase(os.path.abspath(path)), ref.element.lower())
            cycle = nesting_chain(path, ref.element, el.lib_path, el.element, resolver)
            if cycle is not None:
                # (the genie is this element, or holds a copy of it: the marker stays)
                _met_cycle(el, [_element_label(el.lib_path, el.element)] + cycle)
                subs.append(None)
                continue
            if key in stack:
                subs.append(None)
                continue
            try:
                sub = load_element(path, ref.element, compose=True)
                sub.library, sub.element = ref.library, ref.element
            except Exception:  # noqa: BLE001 -- that marker stays
                _note_exc("genie_place.place_element_placeholders/load")
                sub = None
            subs.append(sub)
        # (a marker links to no record of the genie: every record is a new one)
        cases = [([], sub.records) for sub in subs if sub is not None]
        if not cases:
            return 0
        mine = {k for (k, *_r), sub in zip(hits, subs) if sub is not None}
        outside = {G.get_handle(po) for j, po in enumerate(recs) if j not in mine}
        handles = iter(page_instance_handles(cases, outside))
        order = list(recs)
        placed = []
        for (k, ref, local, _path), sub in zip(hits, subs):
            if sub is None:
                continue
            hs = next(handles)
            chain = []
            a = parents[k]
            while a is not None and a != 0:
                chain.append(P.link_of(recs[a]))
                a = parents[a]
            try:
                built = build_instance_records(
                    sub, local, {}, handles=hs, resolver=resolver, parent_chain=chain or None
                )
            except Exception:  # noqa: BLE001 -- that marker stays
                _note_exc("genie_place.place_element_placeholders/build")
                continue
            if not built:
                continue
            r = decode(built[0])
            m = decode(recs[k])
            r.entries = [r.entries[0]] + [[b.copy() for b in lvl] for lvl in (m.entries or [])[1:]]
            if m.entries:
                # (the top level's template bag the marker's: with_placeholder_template)
                r.entries[-1][2] = m.entries[-1][2].copy()
            _marker_name_into(r, m)
            built[0] = to_po(encode(r))
            placed.append((k, built))
        for k, built in sorted(placed, key=lambda x: -x[0]):
            order[k : k + 1] = built
        if placed:
            el.records = order
        return len(placed)
    finally:
        stack.discard(me)
