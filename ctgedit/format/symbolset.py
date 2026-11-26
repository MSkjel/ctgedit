"""Symbol sets: how CtDraw stores them."""

from __future__ import annotations

import struct
from typing import List, Optional, Sequence, Tuple

from .cnum import c_atoi
from . import geometry as G
from . import placement as P
from .page import PageObject
from . import codes as _codes
from . import forest as _forest

SET_TC = 0x0C
STATE_TC = 0x07
GROUP_TC = 0x08
DUMMY_TC = 0xFFFF
INPUT_TCS = _codes.INPUT_TCS
ANIMSYM_TC = 0x100D  # the V3/V4 Animated Symbol (OFF_SYMBOL + SYMBOL_1..8)

ON_OFF, MULTI_STATE, ARRAY, ANIMATED = 0, 1, 2, 3
TYPE_LABELS = {ON_OFF: "On / Off", MULTI_STATE: "Multi-state", ARRAY: "Array", ANIMATED: "Animated"}
CONDITION_KEYS = ("TXT_A", "TXT_B", "TXT_C", "TXT_D", "TXT_E")
EXPRESSION_KEYS = CONDITION_KEYS + ("SET_ARR",)
MAX_CONDITIONS = 5
MAX_STATES = 256
EXPRESSION_MAX = 128  # 'ON symbol when (128 Chars.)'
DEFAULT_STATES = (("lights", "light_1_black"), ("lights", "light_1_red"))

# the V3/V4 Animated Symbol's state keys (ctgedit.format.legacy.ANIMSYM_STATES)
ANIMSYM_STATES = ("OFF_SYMBOL",) + tuple("SYMBOL_%d" % i for i in range(1, 9))

IDENT = P.IDENT
ZERO_LOCAL = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# --------------------------------------------------------------------------- bag values
def set_type(props: dict) -> int:
    """The set's TYPE (the TXTMODE value; absent = On / Off)."""
    return c_atoi(props.get("TXTMODE", "0"))


def condition_count(props: dict) -> int:
    """n = the last non-blank of TXT_A..TXT_E (a blank condition before it is
    still a condition: it evaluates FALSE at runtime)."""
    n = 0
    for k, key in enumerate(CONDITION_KEYS):
        if str(props.get(key, "") or ""):
            n = k + 1
    return n


def capacity(kind: int, n_conditions: int = 1) -> int:
    """How many states a set of ``kind`` addresses (the class section's
    declared count)."""
    if kind == ON_OFF:
        return 2
    if kind == MULTI_STATE:
        return 2 ** max(0, min(MAX_CONDITIONS, n_conditions))
    if kind in (ARRAY, ANIMATED):
        return MAX_STATES
    return 0


def declared_count(props: dict) -> int:
    """The class section's first word as CtDraw's saver computes it:
    a Multi-state set without any condition declares 2**0 = 1."""
    return capacity(set_type(props), condition_count(props))


def expressions(props: dict) -> List[str]:
    """The set's expression list as its type stores it: [TXT_A] (On / Off,
    Animated), [TXT_A .. last condition] (Multi-state), [SET_ARR] (Array)."""
    t = set_type(props)
    if t == ARRAY:
        return [str(props.get("SET_ARR", "") or "")]
    if t == MULTI_STATE:
        n = max(1, condition_count(props))
        return [str(props.get(k, "") or "") for k in CONDITION_KEYS[:n]]
    return [str(props.get("TXT_A", "") or "")]


def expression_keys(kind: int, n: int = 1) -> Tuple[str, ...]:
    """The bag keys a set of ``kind`` with ``n`` expressions writes."""
    if kind == ARRAY:
        return ("SET_ARR",)
    if kind == MULTI_STATE:
        return CONDITION_KEYS[: max(1, min(MAX_CONDITIONS, n))]
    return ("TXT_A",)


def combination_label(index: int, n: int) -> str:
    """Multi-state row label, CtDraw's letters with an overbar on a FALSE
    condition ('A B̄ C̄' = A true, B and C false). Condition A is bit 0."""
    out = []
    for k in range(n):
        ch = "ABCDE"[k]
        out.append(ch if index >> k & 1 else ch + "̅")
    return " ".join(out)


def combination_text(index: int, n: int) -> str:
    """The same combination in words ('A true, B false, C false')."""
    return ", ".join("%s %s" % ("ABCDE"[k], "true" if index >> k & 1 else "false") for k in range(n))


def state_names(kind: int, count: int, n_conditions: int = 1) -> List[str]:
    """What each state slot is called in the Symbol Set sheet."""
    if kind == ON_OFF:
        return ["OFF symbol", "ON symbol"][:count] + ["Symbol %d" % i for i in range(2, count)]
    if kind == MULTI_STATE:
        return [combination_label(i, n_conditions) for i in range(count)]
    if kind == ANIMATED:
        return (["Off frame"] + ["Frame %d" % i for i in range(1, count)])[:count]
    return ["Symbol %d" % i for i in range(count)]


# --------------------------------------------------------------------------- the forest
def direct_children(objs: Sequence[PageObject], index: int) -> List[int]:
    """Direct child indices of record ``index`` in a DFS record list."""
    out, j = [], index + 1
    for _ in range(objs[index].class_flag):
        if j >= len(objs):
            break
        out.append(j)
        pend = 1
        while pend and j < len(objs):
            pend -= 1
            pend += objs[j].class_flag
            j += 1
    return out


subtree_end = _forest.subtree_end


def state_children(objs: Sequence[PageObject], index: int) -> List[int]:
    return [k for k in direct_children(objs, index) if objs[k].type_code not in INPUT_TCS]


def is_symbol_set(po: PageObject) -> bool:
    return po.type_code == SET_TC


def is_group_state(po: PageObject) -> bool:
    """A state that is the instance forest of a group-root symbol: a 0x08 root with
    children and a library link, not a genie (:func:`make_state_forest`)."""
    return (
        po.type_code == GROUP_TC
        and bool(po.class_flag)
        and bool(po.subtype_size & 0x1)
        and not po.subtype_size & _codes.DWF.GENIE
    )


def state_symbol(po: PageObject) -> Optional[Tuple[str, str, str]]:
    """(library, element, ext) a LINKED state shows (a tc 0x07 record, or the root of a
    group-root symbol's instance forest), else None."""
    group = is_group_state(po)
    if not group and (po.type_code != STATE_TC or not (po.subtype_size & 0x1)):
        return None
    try:
        from .symbols import reference_from_object

        ref = reference_from_object(po)
    except Exception:  # noqa: BLE001
        ref = None
    if ref is None or (group and (ref.ext or "ctl").lower() != "ctl"):
        return None
    return ref.library, ref.element, ref.ext or "ctl"


def state_kind(po: PageObject) -> str:
    """'linked' (a library symbol), 'empty' (a DummyMarker), 'embedded' (an
    own picture), 'stripped' (an art-stripped genie copy) or 'other'."""
    if po.type_code == DUMMY_TC:
        return "empty"
    if po.type_code == STATE_TC:
        if po.subtype_size & 0x1:
            return "linked"
        return "embedded" if po.subtype_size & 0x2 else "stripped"
    if po.type_code == GROUP_TC and po.class_flag:
        # a group-root symbol's instance forest: linked on a page; in a genie an
        # art-stripped copy (its members draw their element twins), else an own group
        if state_symbol(po) is not None:
            return "linked"
        if not po.subtype_size & 0x3:
            return "stripped"
        return "embedded"
    return "other"


# --------------------------------------------------------------------------- class section
def class_section(po: PageObject) -> Optional[dict]:
    """The end-anchored class section: {'at', 'declared', 'states',
    'hotspot'} (hotspot None without dwFlags bit 0x2)."""
    b = po.body_raw
    own = bool(po.subtype_size & 0x2)
    at = len(b) - (8 if own else 4)
    gh = G.parse_geom_header(b)
    if gh is None or at < gh.end:
        return None
    declared, states = struct.unpack_from("<2H", b, at)
    hot = struct.unpack_from("<2h", b, at + 4) if own else None
    return {"at": at, "declared": declared, "states": states, "hotspot": hot}


def hotspot(po: PageObject) -> Optional[Tuple[int, int]]:
    cs = class_section(po)
    return None if cs is None or cs["hotspot"] is None else tuple(cs["hotspot"])


def write_class_section(
    po: PageObject,
    declared: Optional[int] = None,
    states: Optional[int] = None,
    hotspot_xy: Optional[Tuple[int, int]] = None,
) -> None:
    cs = class_section(po)
    if cs is None:
        raise ValueError("record carries no symbol-set class section")
    body = bytearray(po.body_raw)
    struct.pack_into(
        "<2H",
        body,
        cs["at"],
        cs["declared"] if declared is None else int(declared) & 0xFFFF,
        cs["states"] if states is None else int(states) & 0xFFFF,
    )
    if hotspot_xy is not None and cs["hotspot"] is not None:
        struct.pack_into("<2h", body, cs["at"] + 4, int(hotspot_xy[0]), int(hotspot_xy[1]))
    po.body_raw = bytes(body)


# --------------------------------------------------------------------------- blanks
def blank_record(name: str) -> PageObject:
    """The blank record a new set ('symbol_set') or DummyMarker
    ('symbol_set_dummy') starts from, built from its fields (records.py)."""
    from . import records

    return records.new_record(name)


def set_expressions(po: PageObject, kind: int, exprs: Sequence[str]) -> None:
    """TXTMODE + the type's expression keys; the other expression keys go."""
    from . import writer

    keys = expression_keys(kind, len(exprs) or 1)
    vals = list(exprs) + [""] * (len(keys) - len(exprs))
    props = po.raw_properties()
    if "TXTMODE" in props or int(kind) != ON_OFF:
        # an absent TXTMODE reads as 0 (atoi): an On / Off set may omit it
        writer.ensure_object_property(po, "TXTMODE", str(int(kind)))
    for key, val in zip(keys, vals):
        if val or key in props:  # an absent expression reads as blank
            writer.ensure_object_property(po, key, str(val))
    for key in EXPRESSION_KEYS:
        if key not in keys:
            writer.remove_object_property(po, key)


def make_set_record(handle: int, kind: int, exprs: Sequence[str], hot: Tuple[int, int]) -> PageObject:
    """A FREE symbol-set record (records.symbol_set): identity-translation
    Local (a transform carrier, so a move composes onto it and the hotspot
    stays put), hotspot = ``hot``, no states yet (the caller appends them and
    calls :func:`finish_set`)."""
    from . import writer

    po = blank_record("symbol_set")
    G.set_handle(po, handle)
    set_expressions(po, kind, exprs)
    P.rederive_slots(po, ZERO_LOCAL, 0x01, (0, 0), None)
    write_class_section(po, 0, 0, hot)
    po.subtype_size = 0x1A  # own appearance + bags + dynamic
    writer.refresh_object_bags(po)
    return po


def set_chain(set_po: PageObject, outer_chain=None) -> list:
    """The (Local, mode, e) ancestor list a state child's Total composes."""
    return [P.link_of(set_po)] + list(outer_chain or [])


def make_state_record(
    handle: int, symbol: Tuple[str, str, str], info: dict, hot: Tuple[int, int], chain
) -> PageObject:
    """A linked state child: the placed-symbol factory record with Local =
    T(hotspot), everything else re-derived from the element + the set."""
    from . import factory, writer

    lib, elem, ext = symbol
    po = factory.create_object(
        "symbol", None, symbol=(lib, elem, ext or "ctl"), handle=handle, link_info=info
    )
    L = (1.0, 0.0, 0.0, 1.0, float(hot[0]), float(hot[1]))
    P.rederive_linked(po, L, 0x01, info["e"], info["N"], chain or None)
    po.subtype_size |= 0x10  # every state is registered (dynamic)
    writer.refresh_object_bags(po)
    return po


def make_state_forest(
    element, handles: Sequence[int], hot: Tuple[int, int], chain, resolver=None
) -> List[PageObject]:
    """The records of a symbol set state that shows a group-root symbol."""
    from . import genie_place as GP, writer

    recs = GP.build_instance_records(
        element,
        (float(hot[0]), float(hot[1])),
        {},
        handles=list(handles),
        resolver=resolver,
        parent_chain=chain or None,
    )
    parents = P.forest_parents(recs)
    for k, po in enumerate(recs):
        p = parents[k]
        if p is None or (recs[p].type_code in (GROUP_TC, SET_TC) and recs[p].subtype_size & 0x10):
            if not po.subtype_size & 0x10:
                po.subtype_size |= 0x10
                writer.refresh_object_bags(po)
    return recs


def state_records(
    handle: int,
    symbol: Tuple[str, str, str],
    info: dict,
    hot: Tuple[int, int],
    chain,
    used: set,
    lib_path: Optional[str] = None,
    resolver=None,
) -> List[PageObject]:
    """The records of a linked symbol set state."""
    from . import genie_place as GP, writer

    lib, elem, ext = symbol
    if lib_path and GP.places_as_forest(lib_path, elem):
        el = GP.load_element(lib_path, elem)
        el.library = lib  # the link token keeps the caller's spelling
        el.element = elem
        # the root is the state's (a stored root it matches keeps its handle)
        root = GP.to_po(writer.object_payload(el.records[0]))
        G.set_handle(root, int(handle))
        hs = GP.page_instance_handles([([root], el.records)], set(used) | {int(handle)})[0]
        used.update(hs)
        return make_state_forest(el, hs, hot, chain, resolver)
    return [make_state_record(handle, symbol, info, hot, chain)]


def make_dummy_record(handle: int, hot: Tuple[int, int], chain) -> PageObject:
    """A DummyMarker placeholder for a cleared state (records.dummy: the plain
    black style)."""
    from . import writer

    po = blank_record("symbol_set_dummy")
    G.set_handle(po, handle)
    hx, hy = int(hot[0]), int(hot[1])
    L = (1.0, 0.0, 0.0, 1.0, float(hx), float(hy))
    s3, m3 = P.apply_parent(L, 0x01, chain or None)
    P.write_slots(po, [(0x01, L), (0x01, L), (m3, s3)])
    body = bytearray(po.body_raw)
    struct.pack_into("<6h", body, len(body) - 12, hx, hy, hx + 1, hy + 1, hx, hy)
    po.body_raw = bytes(body)
    R = P.apply_rect(s3, m3, (hx, hy, hx + 1, hy + 1))
    r1 = tuple(int(v) for v in R)
    P.write_header_geom(po, (r1[0], r1[1]), r1, r1)
    writer.refresh_object_bags(po)
    return po


def finish_set(
    set_po: PageObject, states: Sequence[PageObject], n_children: int, kind: Optional[int] = None
) -> None:
    """Re-derive the set record from its states: child count, the class
    section (declared + state count), anchor = Total(hotspot), rect1 / rect2 =
    the first real state's (a 1 x 1 / 22 x 22 box at the hotspot without one)."""
    from . import writer

    set_po.class_flag = int(n_children)
    props = set_po.raw_properties()
    write_class_section(set_po, declared_count(props), len(states))
    hot = hotspot(set_po)
    gh = G.parse_geom_header(set_po.body_raw)
    if hot is not None:
        (_s1, _s2, (m3, T3, _o, _n)), _e = P.read_slots(set_po.body_raw)
        R = P.apply_rect(T3, m3, (hot[0], hot[1], hot[0], hot[1]))
        anchor = (int(R[0]), int(R[1]))
    else:
        anchor = tuple(gh.anchor)
    real = [s for s in states if s.type_code != DUMMY_TC]
    if real:
        sh = G.parse_geom_header(real[0].body_raw)
        r1, r2 = tuple(sh.rect1), tuple(sh.rect2)
    else:
        ax, ay = anchor
        r1, r2 = (ax, ay, ax + 1, ay + 1), (ax, ay, ax + 22, ay + 22)
    P.write_header_geom(set_po, anchor, r1, r2)
    writer.refresh_object_bags(set_po)


def hotspot_for_topleft(point: Tuple[int, int], info: Optional[dict]) -> Tuple[int, int]:
    """The hotspot that puts the first state's top-left at ``point`` (its
    element origin sits on the hotspot); the point itself without a state."""
    x, y = int(point[0]), int(point[1])
    if info is None:
        return x, y
    ex, ey = info["e"]
    nl, nt = info["N"][0], info["N"][1]
    return int(round(x + ex - nl)), int(round(y + ey - nt))


# --------------------------------------------------------------------------- V3/V4 Animated Symbol
def animsym_states(props: dict) -> List[Optional[Tuple[str, str]]]:
    """OFF_SYMBOL, SYMBOL_1..8 as (library, element) or None."""
    out = []
    for key in ANIMSYM_STATES:
        v = str(props.get(key, "") or "").strip()
        lib, _, elem = v.partition(".")
        out.append((lib, elem) if lib and elem else None)
    return out
