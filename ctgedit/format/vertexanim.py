"""Polygon vertex animation (Appearance > Vertices)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

PREFIX = "VERTEX_"
EXPR = "VERTEXP"
FROM_X, FROM_Y, TO_X, TO_Y = "VERTFROMX", "VERTFROMY", "VERTTOX", "VERTTOY"
#: CtDraw's apply order per vertex (the offsets, then the expression)
NAMES = (FROM_X, FROM_Y, TO_X, TO_Y, EXPR)
OFFSETS = (FROM_X, FROM_Y, TO_X, TO_Y)
#: the offset spin range (0xffff8001 .. 0x7fff) and default
OFFSET_MIN, OFFSET_MAX, OFFSET_DEFAULT = -32767, 32767, 0
#: an expression is at most 253 characters (the sheet's expression limit)
EXPR_LIMIT = 253

#: the keys CtDraw prints ("VERTEX_%s%i") and finds without case: no leading zero, ASCII
#: digits, at most an int's ten
_KEY = re.compile(r"^VERTEX_(VERTEXP|VERTFROMX|VERTFROMY|VERTTOX|VERTTOY)(0|[1-9][0-9]{0,9})\Z", re.I)


def key(name: str, n: int) -> str:
    return "%s%s%d" % (PREFIX, name, n)


def parse_key(k: str) -> Optional[Tuple[str, int]]:
    """(name, vertex number) of a vertex key, None for any other key."""
    m = _KEY.match(k or "")
    return (m.group(1).upper(), int(m.group(2))) if m else None


@dataclass
class VertexAnim:
    """One vertex's stored settings: the texts as stored (None = no key)."""

    expr: Optional[str] = None
    offsets: Dict[str, Optional[str]] = field(default_factory=lambda: {n: None for n in OFFSETS})

    def copy(self) -> "VertexAnim":
        return replace(self, offsets=dict(self.offsets))

    @property
    def animated(self) -> bool:
        return bool((self.expr or "").strip())

    def stored(self) -> Dict[str, Optional[str]]:
        out = dict(self.offsets)
        out[EXPR] = self.expr
        return out


def offset_value(text: Optional[str]) -> Optional[int]:
    """What an offset control shows for a stored text (an absent key = 0),
    None for a text it cannot show (a genie '%param%', a real number)."""
    if text is None or not str(text).strip():
        return OFFSET_DEFAULT
    t = str(text).strip()
    if re.fullmatch(r"[-+]?0*[0-9]{1,5}", t) and OFFSET_MIN <= int(t) <= OFFSET_MAX:  # (longer: out of range)
        return int(t)
    return None


def read(props: Dict[str, str], count: int) -> List[VertexAnim]:
    """The vertex settings of an object with ``count`` nodes (CtDraw's key
    lookup is case-insensitive)."""
    out = [VertexAnim() for _ in range(max(0, count))]
    for k, v in props.items():
        pk = parse_key(k)
        if pk is None:
            continue
        name, n = pk
        if n >= len(out):
            continue
        if name == EXPR:
            out[n].expr = v
        else:
            out[n].offsets[name] = v
    return out


def stored_keys(props: Dict[str, str]) -> Dict[Tuple[str, int], str]:
    """{(name, n): the stored key spelling} of every vertex key."""
    out = {}
    for k in props:
        pk = parse_key(k)
        if pk is not None:
            out[pk] = k
    return out


def edits(
    props: Dict[str, str], old: Sequence[VertexAnim], new: Sequence[VertexAnim]
) -> List[Tuple[str, Optional[str]]]:
    """The writes that turn vertex settings ``old`` into ``new``."""
    spell = stored_keys(props)
    out: List[Tuple[str, Optional[str]]] = []
    for n, (a, b) in enumerate(zip(old, new)):
        if a.stored() == b.stored():
            continue
        want = b.stored()
        if b.animated:
            for name in OFFSETS:
                if want[name] is None or not str(want[name]).strip():
                    want[name] = str(OFFSET_DEFAULT)
        else:
            want[EXPR] = None
        have = a.stored()
        for name in NAMES:
            v = want[name]
            if name != EXPR and v is not None and not str(v).strip():
                v = None
            if v == have[name]:
                continue
            out.append((spell.get((name, n), key(name, n)), v))
    return out


def renumber_edits(
    props: Dict[str, str], mapping: Dict[int, Optional[int]], new_count: Optional[int] = None
) -> List[Tuple[str, Optional[str]]]:
    """The writes that renumber vertex keys by ``mapping``."""
    spell = stored_keys(props)
    have: Dict[Tuple[str, int], str] = {}
    for (name, n), k in spell.items():
        have[(name, n)] = props[k]
    count = len(mapping)
    shift = 0 if new_count is None else new_count - count
    target: Dict[Tuple[str, int], str] = {}
    for (name, n), v in have.items():
        m = mapping.get(n, n + shift if n >= count else n)
        if m is not None:
            target[(name, m)] = v
    out: List[Tuple[str, Optional[str]]] = []
    for pk in sorted(have, key=lambda p: (p[1], NAMES.index(p[0]))):
        if pk not in target:
            out.append((spell[pk], None))
    for pk in sorted(target, key=lambda p: (p[1], NAMES.index(p[0]))):
        v = target[pk]
        if have.get(pk) == v:
            continue
        out.append((spell.get(pk, key(*pk)), v))
    return out


def insert_mapping(count: int, at: int) -> Dict[int, Optional[int]]:
    """Old -> new vertex numbers after a node is inserted at ``at``."""
    return {k: (k if k < at else k + 1) for k in range(count)}


def delete_mapping(count: int, deleted) -> Dict[int, Optional[int]]:
    """Old -> new vertex numbers after the nodes ``deleted`` are removed."""
    gone = set(deleted)
    out: Dict[int, Optional[int]] = {}
    m = 0
    for k in range(count):
        if k in gone:
            out[k] = None
        else:
            out[k] = m
            m += 1
    return out
