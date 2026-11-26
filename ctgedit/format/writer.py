"""Serialize parsed .ctg structures back to bytes."""

from __future__ import annotations

import bisect
import copy
import re
import struct
from typing import List, Optional

from .container import Container
from . import page as _page
from .page import PageBlock, PageObject, _XFORM_DOUBLES, _geom_name_len, _parse_object
from .attributes import AttributesPage, PropertyList
from .textcodec import CODEC

_GENIE_PLACE = None


def _gp():
    """genie_place, imported on first use and kept (it imports this package's heavier modules;
    a per-call ``from . import genie_place`` runs the import machinery every time)."""
    global _GENIE_PLACE
    if _GENIE_PLACE is None:
        from . import genie_place

        _GENIE_PLACE = genie_place
    return _GENIE_PLACE


LINK_ENTRY_SIZE = 0x60
RECORD_HEADER_SIZE = 8
PAGE_TRAILING_PAD = 0x200
ATTR_TRAILING_PAD = 0x200


# --------------------------------------------------------------------------- geometry edits
def rect1_offset_in_body(body: bytes) -> int:
    """rect1 offset in body_raw (after u16 reserved + u16 handle + name lpstr +
    3 transforms + anchor)."""
    o = _geom_name_len(body, 0)
    for _ in range(3):
        mode = body[o]
        o += 1
        o += _XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0) * 8
    return o + 4  # skip anchor (point, 4 bytes)


def get_object_rect1(po: PageObject) -> tuple[int, int, int, int]:
    o = rect1_offset_in_body(po.body_raw)
    return struct.unpack_from("<4h", po.body_raw, o)


def set_object_rect1(po: PageObject, rect: tuple[int, int, int, int]) -> None:
    o = rect1_offset_in_body(po.body_raw)
    b = bytearray(po.body_raw)
    struct.pack_into("<4h", b, o, *rect)
    po.body_raw = bytes(b)


def move_object(po: PageObject, dx: int, dy: int) -> None:
    l, t, r, b = get_object_rect1(po)
    set_object_rect1(po, (l + dx, t + dy, r + dx, b + dy))


# --------------------------------------------------------------------------- property-bag edits
def _serialize_bag_body(pairs: list[tuple[str, str]]) -> bytes:
    body = bytearray(struct.pack("<H", len(pairs)))
    for k, v in pairs:
        kb = k.encode(CODEC, "replace")
        vb = v.encode(CODEC, "replace")
        body += struct.pack("<H", len(kb)) + kb
        body += struct.pack("<H", len(vb)) + vb
    return bytes(body)


#: Name of the attribute :func:`refresh_object_bags` leaves on a record -- the parse it has just
#: made, for a reader that would otherwise parse the same payload again (io.reader._fresh_record).
#:
#: ``po._fresh_parse = (payload, fresh)``:
#:
#: * ``payload`` = ``object_payload(po)`` (bytes) at the time of the refresh;
#: * ``fresh`` = ``page._parse_object(po.index, po.record_offset, len(payload), po.next_link,
#:   payload)`` -- exactly a whole-page parse of that payload, SHARED with the record: ``po.bags``
#:   / ``po.text_exprs`` are ``fresh.bags`` / ``fresh.text_exprs`` (the same lists), and
#:   ``fresh.body_raw`` is ``po.body_raw`` itself when that is an immutable bytes object
#:   (equal bytes either way). Its ``index`` / ``record_offset`` / ``next_link`` are the
#:   record's values at the refresh, NOT a caller's own numbering.
#:
#: It is valid only while ``object_payload(po) == payload`` (compare before use: every body /
#: prefix change since makes it stale; undo restores bodies without a refresh) and only with
#: ``page._WANT_COVERAGE`` off (it is never stored while that is on). Treat ``fresh`` as
#: read-only. Nothing in ctgedit mutates a PageObject's bag lists in place.
FRESH_PARSE_ATTR = "_fresh_parse"


def refresh_object_bags(po: PageObject) -> None:
    """Re-scan an object's bags after body_raw changed."""
    payload = object_payload(po)
    d = getattr(po, "__dict__", None)
    if d is None:  # not a PageObject: parse, keep nothing
        fresh = _parse_object(po.index, po.record_offset, len(payload), po.next_link, payload)
        po.bags, po.text_exprs = fresh.bags, fresh.text_exprs
        po.font_face, po.bytes_covered = fresh.font_face, fresh.bytes_covered
        return
    nm = d.get(NODE_ARRAY_MEMO_ATTR)
    if nm is not None and nm[0] is not po.body_raw:
        del d[NODE_ARRAY_MEMO_ATTR]  # geometry.find_node_array's memo is stale
    coverage = _page._WANT_COVERAGE
    fp = d.get(FRESH_PARSE_ATTR)
    if fp is not None:
        if not coverage and fp[0] == payload:
            fresh = fp[1]
            if (
                po.bags is fresh.bags
                and po.text_exprs is fresh.text_exprs
                and po.font_face == fresh.font_face
                and po.bytes_covered == fresh.bytes_covered
            ):
                return
        if (
            not coverage
            and po.bags is fp[1].bags
            and po.text_exprs is fp[1].text_exprs
            and _same_parse_after(fp[0], payload, fp[1])
        ):
            # a geometry edit (move, resize) that left every bag alone: the parse of the
            # new bytes is the old one (proved by _same_parse_after), not parsed again
            fresh = copy.copy(fp[1])
            body = po.body_raw
            fresh.body_raw = (
                body
                if (getattr(po, "raw_payload", None) is None and type(body) is bytes)
                else bytes(payload[fresh.body_offset :])
            )
            d[FRESH_PARSE_ATTR] = (payload, fresh)
            return
        del d[FRESH_PARSE_ATTR]
    fresh = _parse_object(po.index, po.record_offset, len(payload), po.next_link, payload)
    po.bags = fresh.bags
    po.text_exprs = fresh.text_exprs
    po.font_face = fresh.font_face
    po.bytes_covered = fresh.bytes_covered
    if not coverage:
        body = po.body_raw
        if getattr(po, "raw_payload", None) is None and type(body) is bytes:
            fresh.body_raw = body  # == payload[body_offset:] by construction
        d[FRESH_PARSE_ATTR] = (payload, fresh)


#: switch for the parse-reuse proof (tests compare it with a full parse)
SAME_PARSE_FAST = True


def parse_state(po: PageObject):
    """The record's parse (:data:`FRESH_PARSE_ATTR`) when its bag view is that parse of its
    present payload, else None: :func:`restore_parse` puts it back once the record holds
    these bytes again (an undo / redo, without parsing it again)."""
    d = getattr(po, "__dict__", None)
    fp = d.get(FRESH_PARSE_ATTR) if d is not None else None
    if fp is None:
        return None
    fresh = fp[1]
    if (
        po.bags is not fresh.bags
        or po.text_exprs is not fresh.text_exprs
        or po.font_face != fresh.font_face
        or po.bytes_covered != fresh.bytes_covered
    ):
        return None
    return fp if fp[0] == object_payload(po) else None


def restore_parse(po: PageObject, state) -> bool:
    """Put back a :func:`parse_state` of the record's present payload: what
    :func:`refresh_object_bags` leaves after parsing these bytes (the parse is a pure
    function of them). False, and nothing changed, when ``state`` is not of these bytes."""
    if state is None or _page._WANT_COVERAGE:
        return False
    d = getattr(po, "__dict__", None)
    if d is None or state[0] != object_payload(po):
        return False
    nm = d.get(NODE_ARRAY_MEMO_ATTR)
    if nm is not None and nm[0] is not po.body_raw:
        del d[NODE_ARRAY_MEMO_ATTR]
    fresh = state[1]
    po.bags, po.text_exprs = fresh.bags, fresh.text_exprs
    po.font_face, po.bytes_covered = fresh.font_face, fresh.bytes_covered
    d[FRESH_PARSE_ATTR] = state
    return True


def _changed_positions(old: bytes, new: bytes, limit: int = 512) -> Optional[List[int]]:
    """The byte positions where two payloads of one length differ (None: more than
    ``limit`` of them -- then a full parse is cheaper than the proof)."""
    x = int.from_bytes(old, "little") ^ int.from_bytes(new, "little")
    out: List[int] = []
    while x:
        low = (x & -x).bit_length() - 1
        k = low >> 3
        out.append(k)
        if len(out) > limit:
            return None
        x &= ~(0xFF << (k << 3))
    return out


def _same_parse_after(old: bytes, new: bytes, fresh) -> bool:
    """Does parsing ``new`` give exactly ``fresh`` apart from the body bytes?"""
    if not SAME_PARSE_FAST or len(old) != len(new):
        return False
    if not _same_bags_after(old, new, fresh):
        return False
    if fresh.type_code == _page.TEXT_TYPE_CODE:
        exprs, font = _page.parse_text_extras(new, fresh.body_offset)
        return exprs == list(fresh.text_exprs or []) and font == fresh.font_face
    return True


def _same_bags_after(old: bytes, new: bytes, fresh) -> bool:
    """:func:`_same_parse_after`'s bag-scan proof."""
    if getattr(fresh, "raw_payload", None) is not None:
        return False
    start = fresh.body_offset
    if old[:start] != new[:start]:
        return False
    diffs = _changed_positions(old, new)
    if not diffs:
        return diffs is not None
    lo, hi = min(diffs), max(diffs)
    spans = [(b.offset, b.offset + b.consumed) for b in fresh.bags]
    starts = [s for s, _e in spans]
    for d in diffs:
        k = bisect.bisect_right(starts, d) - 1
        if k >= 0 and spans[k][0] <= d < spans[k][1]:
            return False
    n = len(new)
    parse_entry = _page._parse_entry
    for m in _page._BAG_CANDIDATE.finditer(new, start, min(n, hi + 8)):
        c = m.start()
        if c > hi:
            break
        k = bisect.bisect_right(starts, c) - 1
        if k >= 0 and spans[k][0] <= c < spans[k][1]:
            continue  # inside a found entry: never tried
        if c + 2 + int.from_bytes(new[c : c + 2], "little") < lo:
            continue  # its window ends before every change
        if parse_entry(new, c) is not None:
            return False
    return True


#: geometry.find_node_array's per-record memo (``(body_raw, type_code, dwFlags, result)``),
#: dropped here once the body it was computed for is replaced
NODE_ARRAY_MEMO_ATTR = "_node_array_memo"


def _rewrite_bag(po: PageObject, bag, new_pairs: list[tuple[str, str]]) -> None:
    """Replace a discovered bag/entry's pairs in body_raw, preserving its trailer."""
    start = bag.offset - po.body_offset
    end = start + bag.consumed
    old_entry = po.body_raw[start:end]
    trailer = old_entry[len(old_entry) - bag.trailer_len :] if bag.trailer_len else b""
    new_body = _serialize_bag_body(new_pairs) + trailer
    new_entry = struct.pack("<H", len(new_body)) + new_body
    po.body_raw = po.body_raw[:start] + new_entry + po.body_raw[end:]
    refresh_object_bags(po)


# --------------------------------------------------------------------------- the value bag (strict)
# The dwFlags-0x8 section, as CtDraw's loader reads it:
# u16 N, then N+1 entries of 3 x [u16 size ; u16 count ; count x (lpstr key,
# lpstr value)] (count 0 is legal, the size word is advisory).
# Entry 0 bag 1 ("e0.b1") is the record's OWN VALUE bag: what the register
# writes into pg*.DBF (io.pgregister._value_bag) and what the loader turns into
# the object's properties. e0.b2 holds the TEMPLATES of those values on a
# record inside a genie structure (N >= 1); entries 1..N are the genie levels
# (declarations / bindings / binding templates). The heuristic bag scanner
# (page._scan_payload_bags) cannot see a count-0 bag, so a record whose value
# bag is empty looks 'bagless': every property write goes through this strict
# model instead.
# The saver sets 0x8 on EVERY record ((cc & 8 | 0x10) >> 1),
# and every CtDraw-written record carries the section.


def _strict(po: PageObject):
    """The strict record model (genie_place.decode), or None when the record
    does not walk (an unframed element-trailer record)."""
    if getattr(po, "raw_payload", None) is not None:
        return None
    GP = _gp()
    try:
        return GP.decode(po)
    except Exception:  # noqa: BLE001 -- no forward walk: the caller falls back
        return None


def _store(po: PageObject, r) -> None:
    """Write a strict record model back into ``po`` (flags + body) and re-scan
    its bags. genie_place.encode is the identity on decode()."""
    GP = _gp()
    payload = GP.encode(r)
    po.subtype_size = r.dw
    po.body_raw = payload[12 + len(r.an.encode(CODEC)) :]  # u16 + u32 + u32 + lpstr an
    refresh_object_bags(po)


def _empty_value_bag_overshoot(tc: int) -> int:
    """The size-word overshoot CtDraw writes on an EMPTY e0.b1 (its unwritten
    internal '!' pair): 10, 11 for the input / V4 animation classes, 0 for
    tc 0x1000 (genie_norm.class_ov)."""
    from .genie_norm import class_ov

    return class_ov(tc)


def ensure_bag_section(po: PageObject) -> bool:
    """Give ``po`` an empty property section if it has none."""
    if po.subtype_size & 0x8:
        return True
    if getattr(po, "raw_payload", None) is not None:
        return False
    from .geometry import parse_geom_header

    b = po.body_raw
    try:
        gh = parse_geom_header(b)
    except struct.error:
        gh = None
    if gh is None:
        return False
    o = gh.end
    if po.subtype_size & 0x1:
        (n,) = struct.unpack_from("<H", b, o)
        o += 2 + n
    ov = _empty_value_bag_overshoot(po.type_code)
    group = (
        struct.pack("<H", 0)
        + struct.pack("<HH", 2, 0)
        + struct.pack("<HH", 2 + ov, 0)
        + struct.pack("<HH", 2, 0)
    )
    po.body_raw = b[:o] + group + b[o:]
    po.subtype_size |= 0x8
    refresh_object_bags(po)
    return True


#: value_bag's per-record memo: (body_raw, class_flag, type_code, subtype_size, an_str,
#: pairs or None), valid while the record holds that same immutable body object and those
#: prefix values (the decode reads nothing else)
VALUE_BAG_MEMO_ATTR = "_value_bag_memo"


def value_bag(po: PageObject) -> "list[tuple[str, str]] | None":
    """The record's own value bag e0.b1 as (key, value) pairs; None when the
    record does not walk."""
    d = getattr(po, "__dict__", None)
    body = po.body_raw
    if d is not None and type(body) is bytes and getattr(po, "raw_payload", None) is None:
        m = d.get(VALUE_BAG_MEMO_ATTR)
        if (
            m is not None
            and m[0] is body
            and m[1] == po.class_flag
            and m[2] == po.type_code
            and m[3] == po.subtype_size
            and m[4] == po.an_str
        ):
            return None if m[5] is None else list(m[5])
        pairs = _value_bag_of(po)
        d[VALUE_BAG_MEMO_ATTR] = (
            body,
            po.class_flag,
            po.type_code,
            po.subtype_size,
            po.an_str,
            None if pairs is None else tuple(pairs),
        )
        return pairs
    return _value_bag_of(po)


def _value_bag_of(po: PageObject) -> "list[tuple[str, str]] | None":
    r = _strict(po)
    if r is None:
        return None
    return list(r.entries[0][1].pairs) if r.entries else []


def _find(pairs, key: str) -> int:
    """Index of ``key`` in ``pairs``: exact spelling first, then without case
    (CtDraw's property index is case-insensitive); -1 when absent."""
    for i, (k, _v) in enumerate(pairs):
        if k == key:
            return i
    low = key.lower()
    for i, (k, _v) in enumerate(pairs):
        if k.lower() == low:
            return i
    return -1


def _put(pairs, key: str, value: str) -> None:
    """Update ``key`` in place (keeping its stored spelling; an empty value is
    kept, not removed -- the V3/V4 seeds carry EXPR '' / COMMENT '') or append."""
    i = _find(pairs, key)
    if i >= 0:
        pairs[i] = (pairs[i][0], value)
    else:
        pairs.append((key, value))


def _value_for(r, key: str, value: str) -> str:
    """The SUBSTITUTED value of a user-entered template on a record inside a
    genie structure: %name% tokens resolve against its level-1 values (the
    chain build_instance_records derives e0.b1 with); no token -> the value."""
    if "%" not in value or len(r.entries) < 2:
        return value
    GP = _gp()
    lv1 = r.entries[1]
    return GP.substitute(value, lv1[1], lv1[0], key, {"an": str((r.handle + 1) & 0xFFFF)})


def _in_entry0(r, key: str) -> bool:
    """``key`` is one of the record's own properties: in e0.b1, or only in
    its template bag e0.b2 (a genie member whose template substituted to ''
    has no e0.b1 pair -- subst_level removes an empty result)."""
    if r is None or not r.entries:
        return False
    return _find(r.entries[0][1].pairs, key) >= 0 or (
        len(r.entries) >= 2 and _find(r.entries[0][2].pairs, key) >= 0
    )


def _set_value(po: PageObject, r, key: str, value: str) -> None:
    """Set or append ``key`` on the value bag."""
    b1 = r.entries[0][1]
    if len(r.entries) >= 2:
        _put(r.entries[0][2].pairs, key, value)
        _put(b1.pairs, key, _value_for(r, key, value))
    else:
        _put(b1.pairs, key, value)
    _store(po, r)


def set_object_property(po: PageObject, key: str, new_value: str) -> bool:
    """Set existing property ``key``: in the record's own value bag e0.b1 when
    it is one of the record's own properties (strict), else in whichever bag
    holds it (a genie binding level, a heuristic-only bag). Length-safe
    re-frame. Returns True if the key exists."""
    r = _strict(po)
    if _in_entry0(r, key):
        b1 = r.entries[0][1].pairs
        i = _find(b1, key)
        if i >= 0 and b1[i][1] == new_value:
            return True
        _set_value(po, r, key, new_value)
        return True
    for bag in po.bags:
        for i, (k, v) in enumerate(bag.pairs):
            if k == key:
                if v == new_value:
                    return True
                pairs = list(bag.pairs)
                pairs[i] = (k, new_value)
                _rewrite_bag(po, bag, pairs)
                return True
    return False


def object_property_keys(po: PageObject) -> list[str]:
    keys = []
    for bag in po.bags:
        keys.extend(k for k, _ in bag.pairs)
    return keys


def genie_bindings(po: PageObject) -> dict[str, str]:
    """A placed genie's parameter bindings: key/value pairs on its substitution
    bags (entry trailer >= 11 bytes), minus the editor-state ``!*`` keys and the
    ``AN`` anchor. Later bags win on a key collision."""
    out: dict[str, str] = {}
    for bag in po.bags:
        if bag.trailer_len >= 11:
            for k, v in bag.pairs:
                if k.startswith("!*") or k == "AN":
                    continue
                out[k] = v
    return out


def set_authored_genie_binding(po: PageObject, key: str, value: str) -> bool:
    """A genie inside a library genie (its record has more than two value levels): set
    ``key`` in its OWN level's authored bag (level 1, bag 2: the values with the outer
    genie's %parameters%), matched without case; an empty value removes it. False when the
    record has no such bag or nothing changed."""
    r = _strict(po)
    if r is None or not r.entries or len(r.entries) <= 2 or len(r.entries[1]) <= 2:
        return False
    bag = r.entries[1][2]
    if not bag.pairs:
        return False
    before = list(bag.pairs)
    bag.set(key, value)
    if bag.pairs == before:
        return False
    _store(po, r)
    return True


def genie_substitution_params(po: PageObject) -> dict[str, str]:
    """All substitution values of a genie root, including the implicit AN."""
    out: dict[str, str] = {}
    for bag in po.bags:
        if bag.trailer_len >= 11:
            for k, v in bag.pairs:
                if not k.startswith("!*"):
                    out[k] = v
    return out


def set_genie_binding(po: PageObject, key: str, new_value: str) -> bool:
    """Set a genie parameter binding, targeting the substitution bag (trailer >=
    11) so the instance binding is updated, not a same-named animation value."""
    for bag in po.bags:
        if bag.trailer_len < 11:
            continue
        for i, (k, v) in enumerate(bag.pairs):
            if k == key:
                if v == new_value:
                    return True
                pairs = list(bag.pairs)
                pairs[i] = (k, new_value)
                _rewrite_bag(po, bag, pairs)
                return True
    # no substitution bag carries the key: fall back to any bag (or add)
    return set_object_property(po, key, new_value)


# genie %parameter% / %parameter=default% template markers (leading '='s skipped, as CtDraw's strtok)
_PARAM_MARKER_RE = re.compile(r"%=*([A-Za-z0-9_]+)(=[^%]*)?%")


def resubstitute_member(po: PageObject, params: dict, old_params: dict | None = None) -> bool:
    """Re-apply genie parameter substitution to a member record."""

    def value_for(m):
        name = m.group(1)
        if name in params:
            return str(params[name])
        if m.group(2) is not None:
            return m.group(2)[1:]
        return m.group(0)

    # only values whose template references a CHANGED parameter are rebuilt;
    # every other substituted value keeps its current text (so a marker whose
    # value we cannot know is never written back raw)
    changed_names = (
        {n for n in params if str(old_params.get(n)) != str(params[n])}
        if old_params is not None
        else set(params)
    )

    def touches_changed(tmpl: str) -> bool:
        return any(m.group(1) in changed_names for m in _PARAM_MARKER_RE.finditer(tmpl))

    changed = False
    progress = True
    templated_keys: set = set()
    rewritten: set = set()  # value bags written: never read as a template bag again
    passes = 0
    while progress and passes <= len(po.bags) + 1:
        # (a value that itself keeps a %token% -- '%Name% Q' bound on a nested genie --
        # would otherwise read as a template bag and be re-substituted for ever)
        passes += 1
        progress = False
        bags = list(po.bags)
        for ti, tbag in enumerate(bags):
            if ti in rewritten:
                continue
            tpairs = list(tbag.pairs)
            if not any(_PARAM_MARKER_RE.search(v) for _k, v in tpairs):
                continue
            tkeys = [k for k, _ in tpairs]
            templated_keys.update(tkeys)
            for ci in range(ti):
                cbag = bags[ci]
                if [k for k, _ in cbag.pairs] != tkeys:
                    continue
                new_pairs = [
                    (k, _PARAM_MARKER_RE.sub(value_for, tmpl) if touches_changed(tmpl) else _cur)
                    for (k, _cur), (_k2, tmpl) in zip(cbag.pairs, tpairs)
                ]
                if new_pairs != list(cbag.pairs):
                    _rewrite_bag(po, cbag, new_pairs)
                    changed = True
                    progress = True  # offsets moved -- rescan from fresh bags
                rewritten.add(ci)
                break
            if progress:
                break

    # value-level fallback for keys whose template bag the scanner missed
    if old_params:
        repl = [
            (str(old_params[name]), str(params[name]))
            for name in params
            if name in old_params
            and len(str(old_params[name])) >= 3
            and str(old_params[name]) != str(params.get(name))
        ]
        if repl:
            # ONE pass per bag, computed from the ORIGINAL values: a re-scan
            # loop would replace forever when the new value contains the old one
            # ('Tank1' -> 'Tank10')
            nbags = len(po.bags)
            for bi in range(nbags):
                if bi >= len(po.bags):
                    break
                bag = po.bags[bi]
                new_pairs = []
                dirty = False
                for k, v in bag.pairs:
                    nv = v
                    if k not in templated_keys and not _PARAM_MARKER_RE.search(v):
                        for old, new in repl:
                            if old in nv:
                                nv = nv.replace(old, new)
                    if nv != v:
                        dirty = True
                    new_pairs.append((k, nv))
                if dirty:
                    _rewrite_bag(po, bag, new_pairs)  # refreshes po.bags
                    changed = True
    return changed


def set_body_reference(po: PageObject, new_ref: str) -> bool:
    """Point a placed symbol, genie or template at ``new_ref``."""
    from .symbols import body_reference_span

    span = body_reference_span(po.body_raw)
    if span is None:
        return False
    start, end = span
    enc = new_ref.encode(CODEC, "replace")
    po.body_raw = po.body_raw[:start] + struct.pack("<H", len(enc)) + enc + po.body_raw[end:]
    refresh_object_bags(po)
    return True


def add_object_property(po: PageObject, key: str, value: str) -> bool:
    """Add a new key to the record's value bag."""
    if ensure_bag_section(po):
        r = _strict(po)
        if r is not None and r.entries:
            _set_value(po, r, key, value)
            return True
    for bag in po.bags:
        if bag.is_property:
            pairs = list(bag.pairs) + [(key, value)]
            _rewrite_bag(po, bag, pairs)
            return True
    return False


def ensure_object_property(po: PageObject, key: str, value: str) -> bool:
    """Set ``key`` to ``value``, adding the pair when the key is absent."""
    if set_object_property(po, key, value):
        return True
    return add_object_property(po, key, value)


def remove_object_property(po: PageObject, key: str) -> bool:
    """Delete a key/value pair: from the value bag e0.b1 (and its template in
    e0.b2 on a genie-structure record) when it is there, else from whichever
    bag holds it (length-safe)."""
    r = _strict(po)
    if _in_entry0(r, key):
        for bi in (1, 2) if len(r.entries) >= 2 else (1,):
            pairs = r.entries[0][bi].pairs
            i = _find(pairs, key)
            if i >= 0:
                del pairs[i]
        _store(po, r)
        return True
    for bag in po.bags:
        for i, (k, _v) in enumerate(bag.pairs):
            if k == key:
                pairs = list(bag.pairs)
                del pairs[i]
                _rewrite_bag(po, bag, pairs)
                return True
    return False


def set_object_properties(po: PageObject, edits, template: bool = True) -> bool:
    """Apply several ``(key, value)`` writes to one record."""
    edits = [(k, v) for k, v in edits]
    if not edits:
        return True
    if any(v is not None for _k, v in edits) and not ensure_bag_section(po):
        return False
    r = _strict(po)
    if r is None or not r.entries:
        ok = True
        for k, v in edits:
            if v is None:
                remove_object_property(po, k)
            else:
                ok = ensure_object_property(po, k, v) and ok
        return ok
    b1 = r.entries[0][1].pairs
    b2 = r.entries[0][2].pairs if len(r.entries) >= 2 and template else None
    elsewhere = {k for bag in po.bags for k, _v in bag.pairs}
    later = []
    for k, v in edits:
        if not _in_entry0(r, k) and k in elsewhere:
            later.append((k, v))  # a heuristic-only bag holds it
            continue
        if v is None:
            for pairs in (b1, b2) if b2 is not None else (b1,):
                i = _find(pairs, k)
                if i >= 0:
                    del pairs[i]
        elif b2 is not None:
            _put(b2, k, v)
            _put(b1, k, _value_for(r, k, v))
        else:
            _put(b1, k, v)
    _store(po, r)
    ok = True
    for k, v in later:
        if v is None:
            remove_object_property(po, k)
        else:
            ok = set_object_property(po, k, v) and ok
    return ok


# --------------------------------------------------------------------------- metadata
METADATA_FLAG = 0x100


def rebuild_metadata_section(po: PageObject) -> bool:
    """Re-derive the metadata section from the value bag."""
    r = _strict(po)
    if r is None or not r.entries:
        return False
    if not _gp().rederive_metadata(r):  # (the one rule: genie_place)
        return False
    _store(po, r)
    return True


def set_text_caption(po: PageObject, new_text: str) -> bool:
    """Rewrite only a text record's caption."""
    from . import geometry as G

    tail = G.parse_text_tail(po)
    if tail is None:
        return False
    enc = new_text.encode(CODEC, "replace")
    body = po.body_raw
    if body[tail["s_text"] + 2 : tail["s_face"]] == enc:
        return True
    po.body_raw = body[: tail["s_text"]] + struct.pack("<H", len(enc)) + enc + body[tail["s_face"] :]
    refresh_object_bags(po)
    return True


def set_object_text(po: PageObject, new_text: str, label: bool | None = None) -> bool:
    """Rewrite a text object's display string."""
    already = new_text.startswith("@(") and new_text.endswith(")")
    if label and not already:
        new_text = "@(%s)" % new_text
    if not po.text_exprs:
        return _set_plain_caption(po, new_text)
    targets = {e.encode(CODEC, "replace") for e in po.text_exprs}
    wrapped = new_text if (already or label is not None) else "@(%s)" % new_text
    repl = wrapped.encode(CODEC, "replace")
    body = po.body_raw
    # the bag mirrors (TXT_OFF/TXT_ON) are rewritten through the bag writer
    # below, which keeps each entry's size word in step
    in_bag = []
    for bag in po.bags:
        s0 = bag.offset - po.body_offset
        in_bag.append((s0, s0 + bag.consumed))
    out = bytearray()
    i = 0
    n = len(body)
    changed = False
    while i < n:
        if any(a <= i < b for a, b in in_bag):
            out.append(body[i])
            i += 1
            continue
        if i + 2 <= n:
            (ln,) = struct.unpack_from("<H", body, i)
            if 2 <= ln <= 1024 and i + 2 + ln <= n and body[i + 2 : i + 2 + ln] in targets:
                out += struct.pack("<H", len(repl)) + repl
                i += 2 + ln
                changed = True
                continue
        out.append(body[i])
        i += 1
    if not changed:
        return False
    po.body_raw = bytes(out)
    refresh_object_bags(po)
    for key in ("TXT_OFF", "TXT_ON"):
        for bag in po.bags:
            if any(k == key for k, _ in bag.pairs):
                set_object_property(po, key, wrapped)
                break
    rebuild_text_strings(po)
    return True


STATE_UNSET = "#\n"  # a state string written as ''


def text_string_section(po: PageObject, caption: str) -> bytes:
    """The text class's string section as the saver writes it."""
    p = po.raw_properties()
    # atoi(TXTMODE), as the saver reads it
    m = re.match(r"\s*([+-]?\d+)", p.get("TXTMODE") or "")
    mode = int(m.group(1)) if m else 0
    flag = {0: 3, 1: 5, 2: 9}.get(mode, 1)

    def lp(s):
        b = s.encode(CODEC, "replace")
        return struct.pack("<H", len(b)) + b

    def st(v):
        return "" if v == STATE_UNSET else v

    out = struct.pack("<I", flag) + lp(p.get("TXT_OFF", "") or caption)
    if flag & 2:
        out += lp(p.get("TXT_ON", ""))
    if flag & 4:
        n = 0
        for i in range(32, -1, -1):
            if p.get("STATE_%d" % i, ""):
                n = i + 1
                break
        out += struct.pack("<H", n) + b"".join(lp(st(p.get("STATE_%d" % i, ""))) for i in range(n))
    if flag & 8:
        out += struct.pack("<H", 256) + b"".join(lp(st(p.get("ARRAY_%d" % i, ""))) for i in range(256))
    return out


def rebuild_text_strings(po: PageObject) -> bool:
    """Re-serialise a text record's string section (see
    :func:`text_string_section`). True when it changed."""
    from . import geometry as G

    if po.type_code != 0x05 or not (po.subtype_size & 0x2):
        return False
    tail = G.parse_text_tail(po)
    if tail is None or tail.get("k_off") is None:
        return False
    new = text_string_section(po, tail["text"])
    a, b = tail["k_off"], tail["echo_off"]
    if po.body_raw[a:b] == new:
        return False
    po.body_raw = po.body_raw[:a] + new + po.body_raw[b:]
    refresh_object_bags(po)
    return True


def sync_button_caption(po: PageObject) -> bool:
    """A button's dwFlags-0x80 caption lpstr (just before its colour state)
    carries the TEXT bag value (engine-rewritten: factory 'button')."""
    from . import colourstate

    if po.type_code != 0x1001:
        return False
    L = colourstate.layout(po)
    if L is None or "sec80_at" not in L:
        return False
    o = L["sec80_at"]
    (n,) = struct.unpack_from("<H", po.body_raw, o)
    enc = po.raw_properties().get("TEXT", "").encode(CODEC, "replace")
    if po.body_raw[o + 2 : o + 2 + n] == enc:
        return False
    po.body_raw = po.body_raw[:o] + struct.pack("<H", len(enc)) + enc + po.body_raw[o + 2 + n :]
    refresh_object_bags(po)
    return True


def _set_plain_caption(po: PageObject, new_text: str) -> bool:
    """Rewrite a plain (non-``@()``) text caption."""
    from . import geometry as G

    props = po.raw_properties()
    if (props.get("TXTMODE") or "").strip() in ("3", "4"):
        return False
    tail = G.parse_text_tail(po)
    if tail is None:
        return False
    old = tail["text"]
    enc = new_text.encode(CODEC, "replace")
    body = po.body_raw
    po.body_raw = body[: tail["s_text"]] + struct.pack("<H", len(enc)) + enc + body[tail["s_face"] :]
    refresh_object_bags(po)
    mirrors = [
        k
        for k, v in props.items()
        if v == old and (k in ("TXT_OFF", "TXT_ON") or k.startswith(("ARRAY_", "STATE_")))
    ]
    if old == "" or not mirrors:
        mirrors = ["TXT_OFF"]
    for key in mirrors:
        ensure_object_property(po, key, new_text)
    rebuild_text_strings(po)
    return True


# --------------------------------------------------------------------------- objects
#: an all-ASCII string encodes through the C codec (the bytes the code page gives it), not the
#: registered codec's Python-level call (genie_place._ASCII: the same check)
_ASCII_ENC = _page.ASCII_FAST and all(chr(c).encode(CODEC) == bytes([c]) for c in range(0x80))
_PREFIX = struct.Struct("<HIIH").pack
_RECORD_HEADER = struct.Struct("<II").pack


def object_payload(po: PageObject) -> bytes:
    """Reconstruct an object's payload bytes (prefix + body)."""
    raw = getattr(po, "raw_payload", None)
    if raw is not None:
        # an unframed record (library element-definition trailer): verbatim,
        # with only a structural child-count change carried over
        if len(raw) >= 2 and struct.unpack_from("<H", raw, 0)[0] != po.class_flag:
            raw = struct.pack("<H", po.class_flag) + raw[2:]
        return raw
    s = po.an_str
    an = s.encode("ascii") if _ASCII_ENC and s.isascii() else s.encode(CODEC, "replace")
    # u16 class flag, u32 type code, u32 dwFlags, lpstr an
    return _PREFIX(po.class_flag, po.type_code, po.subtype_size, len(an)) + an + po.body_raw


def serialize_objects(objects: list[PageObject]) -> bytes:
    """Serialize the object data region, recomputing the next-link chain."""
    payloads = [object_payload(o) for o in objects]
    parts = []
    append = parts.append
    header = _RECORD_HEADER
    last = len(payloads) - 1
    # running = offset (from region start) of record i+1's header = end of record i
    running = 0
    for i, pl in enumerate(payloads):
        running += RECORD_HEADER_SIZE + len(pl)
        append(header(len(pl), running if i < last else 0))
        append(pl)
    return b"".join(parts)


# --------------------------------------------------------------------------- page block
def serialize_page_block(pb: PageBlock, original: bytes) -> bytes:
    """Reassemble a ``page`` block. Byte-exact when ``pb`` is unmodified."""
    head = bytearray(original[: pb.head_size])
    objdata = serialize_objects(pb.objects)
    objdata_size = len(objdata)
    # patch content-dependent head fields
    struct.pack_into("<I", head, 0x00, objdata_size)
    struct.pack_into("<I", head, 0x04, len(pb.objects))
    if pb.head_size >= 0x34:
        struct.pack_into("<I", head, 0x08, len(pb.thumbnail))
        struct.pack_into("<I", head, 0x14, pb.page_width)
        struct.pack_into("<I", head, 0x18, pb.page_height)
        struct.pack_into("<I", head, 0x1C, pb.link_count)
        head[0x20:0x34] = pb.page_identity
    return b"".join((head, pb.link_table_raw, objdata, pb.thumbnail, b"\x00" * PAGE_TRAILING_PAD))


# --------------------------------------------------------------------------- attributes
def serialize_property_list(pl: PropertyList) -> bytes:
    out = bytearray(struct.pack("<H", len(pl.pairs)))
    for k, v in pl.pairs:
        kb = k.encode(CODEC, "replace")
        vb = v.encode(CODEC, "replace")
        out += struct.pack("<H", len(kb)) + kb
        out += struct.pack("<H", len(vb)) + vb
    return bytes(out)


def serialize_attributes_block(at: AttributesPage, original: bytes) -> bytes:
    """Reassemble an ``attributes`` block. Byte-exact when unmodified."""
    head = bytearray(original[:0x54])
    list1 = serialize_property_list(at.list1)
    list2 = serialize_property_list(at.list2)
    data_end = 0x54 + len(list1) + len(list2)  # start of the 512-byte zero pad
    outer_len = data_end - 0x34  # @0x00
    inner_len = data_end - 0x3C  # @0x34 (= outer_len - 8)
    struct.pack_into("<I", head, 0x00, outer_len)
    struct.pack_into("<I", head, 0x14, at.width)
    struct.pack_into("<I", head, 0x18, at.height)
    head[0x20:0x34] = at.page_identity
    struct.pack_into("<I", head, 0x34, inner_len)
    struct.pack_into("<I", head, 0x40, at.bg_color0)
    struct.pack_into("<I", head, 0x44, at.bg_color1)
    out = bytearray(head)
    out += list1
    out += list2
    out += b"\x00" * ATTR_TRAILING_PAD
    return bytes(out)


# --------------------------------------------------------------------------- whole file
def serialize_ctg(container: Container, page: PageBlock | None, attributes: AttributesPage | None) -> bytes:
    """Re-serialize a whole page .ctg from its parsed parts."""
    pblk = container.block("page")
    ablk = container.block("attributes")
    if page is not None and pblk is not None:
        pblk.data = serialize_page_block(page, pblk.data)
    if attributes is not None and ablk is not None:
        ablk.data = serialize_attributes_block(attributes, ablk.data)
    container.relayout()
    return container.to_bytes()


def write_ctg(
    path: str, container: Container, page: PageBlock | None, attributes: AttributesPage | None
) -> None:
    """Write the page atomically (format/fileio): a failed write leaves the
    old file."""
    from .fileio import write_atomic

    write_atomic(path, serialize_ctg(container, page, attributes))
