"""Fill > Color and Fill > Level: how CtDraw reads them and what Apply writes."""

from __future__ import annotations

import functools

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import colourstate as CS
from .propget import getter as _getter, note_too_long, written_conditions  # noqa: F401 (written_conditions re-exported)

Pair = Tuple[int, int]
NO_COLOUR = 0xFF000000

ON_OFF, MULTI_STATE, ARRAY, THRESHOLD, GRADIENT = 0, 1, 2, 3, 4
TYPES = (ON_OFF, MULTI_STATE, ARRAY, THRESHOLD, GRADIENT)
TYPE_LABELS = {
    ON_OFF: "On / Off",
    MULTI_STATE: "Multi-state",
    ARRAY: "Array",
    THRESHOLD: "Threshold",
    GRADIENT: "Gradient",
}
#: the key each type keeps its expression in
EXPRESSION_KEY = {
    ON_OFF: "COL_A",
    MULTI_STATE: "COL_A",
    ARRAY: "COL_ARR",
    THRESHOLD: "COL_EXP",
    GRADIENT: "COL_EXP",
}
#: the types whose expression has a 'Specify range' (the ``^(min,max)`` suffix)
RANGED = (THRESHOLD, GRADIENT)
CONDITION_KEYS = ("COL_A", "COL_B", "COL_C", "COL_D", "COL_E")
MAX_CONDITIONS = 5
DEFAULT_CONDITIONS = 3
MAX_STATES = 32  # 2 ** MAX_CONDITIONS
ARRAY_SIZE = 256
MAX_THRESHOLDS = 100

#: field limits (help 1175478..1175482, 1172169): the ON colour condition,
#: the multi-state conditions, the array / colour expressions, the level
TEXT_LIMITS = {"on_when": 254, "condition": 128, "expression": 128, "level": 253}

DEFAULT_ON = 0x0000FF00  # an empty COL_MAXON reads green (0xff00)
#: a state colour nothing is stored for, by state & 7
STATE_DEFAULTS = (0x00FF00, 0xFFFF00, 0xFF0000, 0x00FFFF, 0xFF00FF, 0xFFFFFF, 0x0000FF, 0xBFBFBF)
#: the rows a new Threshold page starts with
DEFAULT_THRESHOLDS = (
    "<,5,0x000000FF,0x000000FF",
    "<,15,0x00000080,0x00000080",
    ">,85,0x00800000,0x00800000",
    ">,95,0x00FF0000,0x00FF0000",
)

#: every key the Fill > Color page writes or removes ('NAME_*' = NAME_<n>)
KEYS = (
    ("COLMODE",) + CONDITION_KEYS + ("COL_MAXON", "COL_ARR", "COL_EXP", "COL_STA_*", "COL_ARR_*", "COL_THR_*")
)

# -- Fill > Level
UP, DOWN, LEFT, RIGHT = 0, 1, 2, 3
DIRECTIONS = ((UP, "Up"), (DOWN, "Down"), (LEFT, "Left"), (RIGHT, "Right"))
LEVEL_KEYS = ("FIL", "FILMINPC", "FILMAXPC", "FILMODE", "FILCOL")
TRANSPARENT = (NO_COLOUR, NO_COLOUR)


def atoi(value) -> int:
    """C ``atoi`` -- how CtDraw reads COLMODE / FILMODE (0 without digits)."""
    m = re.match(r"\s*([+-]?\d+)", str(value or ""))
    return int(m.group(1)) if m else 0


def parse_pair(value) -> Optional[Pair]:
    """A stored 0x..,0x.. pair ("0x%x,0x%x"); None when the
    value is empty."""
    if not value:
        return None
    return CS._parse(value)


def fmt_pair(p: Pair) -> str:
    return CS.fmt_pair(p)


def is_transparent(p: Optional[Pair]) -> bool:
    return p is None or ((p[0] >> 24) & 0xFF == 0xFF and (p[1] >> 24) & 0xFF == 0xFF)


# --------------------------------------------------------------------------- threshold rows
@dataclass(frozen=True)
class Threshold:
    """One Threshold row: the fill turns ``(on, off)`` when the expression is
    ``<`` / ``<=`` / ``>`` / ``>=`` ``value`` percent of its range."""

    greater: bool
    equal: bool
    value: int
    on: int
    off: int

    @classmethod
    def parse(cls, text: str) -> Optional["Threshold"]:
        r = CS.parse_threshold(text)
        return cls(*r) if r is not None else None

    def text(self) -> str:
        return CS.format_threshold(self.greater, self.equal, self.value, self.on, self.off)

    @property
    def comparison(self) -> str:
        return (">" if self.greater else "<") + ("=" if self.equal else "")

    @property
    def pair(self) -> Pair:
        return (self.on, self.off)


def default_thresholds() -> List[Threshold]:
    return [Threshold.parse(t) for t in DEFAULT_THRESHOLDS]


# --------------------------------------------------------------------------- the page
@dataclass
class FillColour:
    """What the Fill > Color page shows."""

    mode: int = ON_OFF
    static: Optional[Pair] = None
    static_editable: bool = True
    expression: str = ""
    on: Pair = (DEFAULT_ON, DEFAULT_ON)
    conditions: List[str] = field(default_factory=lambda: [""] * DEFAULT_CONDITIONS)
    states: List[Pair] = field(default_factory=list)
    array: List[Pair] = field(default_factory=list, repr=False)
    array_base: List[Pair] = field(default_factory=list, repr=False)
    array_default: List[Pair] = field(default_factory=list, repr=False)
    thresholds: List[Threshold] = field(default_factory=default_thresholds)
    known_mode: bool = True
    #: Clear Property was used and nothing entered since (:func:`is_blank`)
    cleared: bool = False

    def copy(self) -> "FillColour":
        return replace(
            self,
            conditions=list(self.conditions),
            states=list(self.states),
            array=list(self.array),
            thresholds=list(self.thresholds),
        )


def condition_count(get, stored_states: int = 0) -> int:
    """n = the last non-blank of COL_A..COL_E. None filled in:
    CtDraw shows 3 blank conditions; ctgedit keeps the number of states the
    record stores (2 ** n = ``stored_states``: a record may store 2 or 4
    states without a condition), so an apply does not add states."""
    for k in range(MAX_CONDITIONS - 1, -1, -1):
        if get(CONDITION_KEYS[k]) or "":
            return k + 1
    if stored_states > 1:
        n = 1
        while 2**n < stored_states and n < MAX_CONDITIONS:
            n += 1
        return n
    return DEFAULT_CONDITIONS


def state_default(i: int, stored_states: Sequence[Pair] = ()) -> Pair:
    """A state colour nothing is stored for: ``STATE_DEFAULTS[i & 7]``."""
    if 0 <= i < len(stored_states):
        return tuple(stored_states[i])
    c = STATE_DEFAULTS[i & 7]
    return (c, c)


def read(
    props: Dict[str, str],
    static: Optional[Pair],
    *,
    static_editable: bool = True,
    palette: Optional[Sequence[Pair]] = None,
    binary_array: Optional[Sequence[Pair]] = None,
    binary_states: Optional[Sequence[Pair]] = None,
) -> FillColour:
    """What the page shows for an object with properties ``props``."""
    get = _getter(props)
    mode = atoi(get("COLMODE"))
    known = mode in TYPES
    if not known:
        mode = ON_OFF
    expr = get(EXPRESSION_KEY[mode]) or ""
    on = parse_pair(get("COL_MAXON")) or (DEFAULT_ON, DEFAULT_ON)
    sta_bin = list(binary_states or ())
    n_sta = 0
    if mode == MULTI_STATE:
        n_sta = max(len(sta_bin), max((i + 1 for i in range(MAX_STATES) if get("COL_STA_%d" % i)), default=0))
    n = condition_count(get, n_sta)
    conds = [get(k) or "" for k in CONDITION_KEYS[:n]]
    if mode == MULTI_STATE:
        expr = conds[0]
    else:
        # the shared expression is condition A -- without a Threshold /
        # Gradient range: the range is its own field (FillColor_Expression
        # carries the expression alone to the Multi-state panel)
        conds[0] = _strip_range(expr) if mode in RANGED else expr
    states = [parse_pair(get("COL_STA_%d" % i)) or state_default(i, sta_bin) for i in range(2**n)]
    pal = list(palette) if palette is not None else [(NO_COLOUR, NO_COLOUR)] * ARRAY_SIZE
    pal += [(NO_COLOUR, NO_COLOUR)] * (ARRAY_SIZE - len(pal))
    arr_bin = list(binary_array or ())
    base = [tuple(arr_bin[i]) if i < len(arr_bin) else tuple(pal[i]) for i in range(ARRAY_SIZE)]
    array = [parse_pair(get("COL_ARR_%d" % i)) or base[i] for i in range(ARRAY_SIZE)]
    rows: List[Threshold] = []
    for i in range(MAX_THRESHOLDS):
        v = get("COL_THR_%d" % i)
        if not v:
            break
        rows.append(Threshold.parse(v))
    if not rows:
        rows = default_thresholds()
    if static is None:
        static_editable = False
    else:
        states[0] = tuple(static)
        array[0] = tuple(static)
    return FillColour(
        mode,
        None if static is None else tuple(static),
        static_editable,
        expr,
        on,
        conds,
        states,
        array,
        base,
        [tuple(p) for p in pal],
        rows,
        known,
    )


def resized_states(states: Sequence[Pair], n_conditions: int, stored: Sequence[Pair] = ()) -> List[Pair]:
    """The state colours for ``n_conditions`` conditions: kept by index, new
    states take their default."""
    want = 2 ** max(0, min(MAX_CONDITIONS, n_conditions))
    out = [tuple(p) for p in states[:want]]
    return out + [state_default(i, stored) for i in range(len(out), want)]


def _relevant(fc: FillColour, with_static: bool):
    m = fc.mode
    static = (fc.static,) if with_static else ()
    if m == ON_OFF:
        return (m, fc.expression, tuple(fc.on), is_blank(fc)) + static
    if m == MULTI_STATE:
        conds = written_conditions(fc.conditions)
        states = resized_states(fc.states, len(conds))
        return (m, tuple(conds), tuple(states[1:])) + static
    if m == ARRAY:
        return (m, fc.expression, tuple(fc.array[1:])) + static
    if m == THRESHOLD:
        return (m, fc.expression, tuple(fc.thresholds))
    return (m, fc.expression, tuple(fc.on)) + static


def has_changes(before: FillColour, after: FillColour) -> bool:
    return _relevant(before, True) != _relevant(after, True)


def cleared(fc: FillColour) -> FillColour:
    """Clear Property: the object's colour animation removed -- the page back
    to On / Off with nothing configured (Apply then removes every COL_ key
    and COLMODE); the static colour stays."""
    out = fc.copy()
    out.mode = ON_OFF
    out.expression = ""
    out.on = (DEFAULT_ON, DEFAULT_ON)
    out.conditions = [""] * DEFAULT_CONDITIONS
    out.states = resized_states([fc.static or state_default(0)], DEFAULT_CONDITIONS)
    out.array = [fc.array[0]] + list(fc.array_base[1:])
    out.thresholds = default_thresholds()
    out.known_mode = True
    out.cleared = True
    return out


def is_blank(fc: FillColour) -> bool:
    """Was the colour animation cleared?"""
    return fc.cleared and fc.mode == ON_OFF and not fc.expression.strip()


def _array_value(fc: FillColour, i: int, stored: Optional[str]) -> Optional[str]:
    """COL_ARR_<i> for an array entry: removed when it is the default and the
    stored binary entry is the default too; a stored key with
    the same colour is kept as it is written."""
    c = tuple(fc.array[i])
    if c == tuple(fc.array_default[i]) and tuple(fc.array_base[i]) == c:
        return None
    if stored and parse_pair(stored) == c:
        return stored
    if not stored and tuple(fc.array_base[i]) == c:
        return None  # the stored binary entry keeps it
    return fmt_pair(c)


def target_values(fc: FillColour, get=None) -> Dict[str, Optional[str]]:
    """Every key CtDraw's Apply of the page writes for ``fc`` (None =
    removed): COLMODE, the other types' keys removed, then the
    type's own (its apply). A blank On / Off page (no condition) stores no
    colour animation: every key removed."""
    get = get or (lambda _k: None)
    m = fc.mode
    out: Dict[str, Optional[str]] = {"COLMODE": str(m) if m else None}
    if m != ON_OFF or is_blank(fc):
        out["COL_A"] = None
        out["COL_MAXON"] = None
    if m != MULTI_STATE:
        for k in CONDITION_KEYS:
            out[k] = None
        for i in range(MAX_STATES):
            out["COL_STA_%d" % i] = None
    if m != ARRAY:
        out["COL_ARR"] = None
        for i in range(ARRAY_SIZE):
            out["COL_ARR_%d" % i] = None
    out["COL_EXP"] = None
    if m != THRESHOLD:
        for i in range(MAX_THRESHOLDS):
            out["COL_THR_%d" % i] = None
    if m == ON_OFF:
        if not is_blank(fc):
            out["COL_A"] = fc.expression or None
            out["COL_MAXON"] = fmt_pair(fc.on)
    elif m == MULTI_STATE:
        conds = written_conditions(fc.conditions)
        for k, key in enumerate(CONDITION_KEYS):
            out[key] = (conds[k] or None) if k < len(conds) else None
        states = resized_states(fc.states, len(conds))
        for i in range(MAX_STATES):
            out["COL_STA_%d" % i] = fmt_pair(states[i]) if i < len(states) else None
    elif m == ARRAY:
        out["COL_ARR"] = fc.expression or None
        out["COL_ARR_0"] = fmt_pair(fc.array[0])
        for i in range(1, ARRAY_SIZE):
            out["COL_ARR_%d" % i] = _array_value(fc, i, get("COL_ARR_%d" % i))
    elif m == THRESHOLD:
        out["COL_EXP"] = fc.expression or None
        rows = fc.thresholds[:MAX_THRESHOLDS]
        for i in range(MAX_THRESHOLDS):
            out["COL_THR_%d" % i] = rows[i].text() if i < len(rows) else None
    else:
        out["COL_EXP"] = fc.expression or None
        out["COL_MAXON"] = fmt_pair(fc.on)
    return out


def apply_edits(
    props: Dict[str, str], before: FillColour, after: FillColour
) -> Tuple[List[Tuple[str, Optional[str]]], Optional[Pair]]:
    """What Apply writes: ``([(key, value or None)], new static pair or None)``."""
    get = _getter(props)
    if _relevant(before, False) != _relevant(after, False):
        target = target_values(after, get)
        if after.mode == THRESHOLD:
            for i, row in enumerate(after.thresholds[:MAX_THRESHOLDS]):
                stored = get("COL_THR_%d" % i)
                if stored and Threshold.parse(stored) == row:
                    target["COL_THR_%d" % i] = stored
    else:
        target = {}
    edits = [(k, v) for k, v in target.items() if get(k) != v]
    static = None
    if after.static_editable and after.static is not None and after.static != before.static:
        static = tuple(after.static)
    return edits, static


def problems(fc: FillColour) -> List[Tuple[str, str]]:
    """[(field, message)] of what stops the page being stored."""
    out: List[Tuple[str, str]] = []

    long = functools.partial(note_too_long, out)

    m = fc.mode
    if m == ON_OFF:
        long("expression", "ON color when", fc.expression, TEXT_LIMITS["on_when"])
    elif m == MULTI_STATE:
        for k, c in enumerate(written_conditions(fc.conditions)):
            long("condition:%d" % k, "Condition %s" % "ABCDE"[k], c, TEXT_LIMITS["condition"])
    elif m == ARRAY:
        long("expression", "Array expression", fc.expression, TEXT_LIMITS["expression"])
    else:
        long("expression", "Color expression", _strip_range(fc.expression), TEXT_LIMITS["expression"])
        if m == THRESHOLD:
            if len(fc.thresholds) > MAX_THRESHOLDS:
                out.append(
                    (
                        "threshold:%d" % MAX_THRESHOLDS,
                        "There are %d thresholds; at most %d can be stored. Delete some "
                        "and apply again." % (len(fc.thresholds), MAX_THRESHOLDS),
                    )
                )
            for i, row in enumerate(fc.thresholds):
                if not -32768 <= row.value <= 32767:
                    out.append(
                        (
                            "threshold:%d" % i,
                            "Threshold %d (%d %%) is out of range: use -32768 to 32767." % (i + 1, row.value),
                        )
                    )
    return out


def _strip_range(text: str) -> str:
    """The expression without a ``^(min,max)`` suffix (its length; the
    Multi-state condition A it becomes)."""
    m = re.match(r"^\((?P<e>.*)\)\^\([^,()]*,[^,()]*\)\s*$", text or "", re.S)
    return m.group("e") if m else (text or "")


def written_texts(fc: FillColour) -> List[str]:
    """Every text the current type stores (for the code-page check)."""
    if fc.mode == MULTI_STATE:
        return written_conditions(fc.conditions)
    return [fc.expression]


# --------------------------------------------------------------------------- Fill > Level
def direction(props: Dict[str, str]) -> int:
    """FILMODE as the page shows it: 0..3, anything else Up."""
    d = atoi(_getter(props)("FILMODE"))
    return d if 0 <= d <= 3 else UP


def level_background(props: Dict[str, str]) -> Pair:
    """FILCOL: the unfilled part's colour; absent = transparent."""
    return parse_pair(_getter(props)("FILCOL")) or TRANSPARENT
