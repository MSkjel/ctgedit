"""A text's Display Value: how CtDraw reads it and what Apply writes."""

from __future__ import annotations

import functools

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple
from .propget import getter as _getter, note_too_long, written_conditions  # noqa: F401 (written_conditions re-exported)

ON_OFF, MULTI_STATE, ARRAY, NUMERIC, STRING = 0, 1, 2, 3, 4
TYPES = (ON_OFF, MULTI_STATE, ARRAY, NUMERIC, STRING)
TYPE_LABELS = {
    ON_OFF: "On / Off",
    MULTI_STATE: "Multi-state",
    ARRAY: "Array",
    NUMERIC: "Numeric",
    STRING: "String",
}
CONDITION_KEYS = ("TXT_A", "TXT_B", "TXT_C", "TXT_D", "TXT_E")
MAX_CONDITIONS = 5
DEFAULT_CONDITIONS = 3
MAX_STATES = 32  # 2 ** MAX_CONDITIONS
ARRAY_SIZE = 256
UNSET = "#\n"  # a state / array row left blank

#: field limits: the OFF / ON / state / array texts (256 characters), the
#: expression fields (254 characters; the pgdynobj TXT_* / TXT_STR columns
#: are 254 wide), the multi-state conditions (help 1172131 / 1175479), the
#: numeric format (CtDraw keeps 0xb bytes)
TEXT_MAX = 256
EXPRESSION_MAX = 254
CONDITION_MAX = 128
FORMAT_MAX = 10

#: every key the page writes or removes ('NAME_*' = NAME_<n>)
KEYS = ("TXTMODE", "TXT_OFF", "TXT_ON", "TXT_STR") + CONDITION_KEYS + ("STATE_*", "ARRAY_*")

#: the frozen copy of Bin\SCADAHELP.dbf TYPE 'FORMAT' (AVEVA Plant SCADA 2023
#: R2), used when no install is found
SCADAHELP_FORMATS = (
    "##",
    "###",
    "####",
    "#####",
    "#####.#",
    "#####.##",
    "####.#",
    "####.##",
    "####.###",
    "###.#",
    "###.##",
    "##.#",
    "##.##",
    "#-#",
    "#-##",
    "#-###",
    "#-####",
    "#-#####",
    "#0#",
    "#0##",
    "#0###",
    "#0###.#",
    "#0###.##",
    "#0##.#",
    "#0##.##",
    "#0##.###",
    "#0#.#",
    "#0#.##",
    "#0-#",
    "#0-##",
    "#0-###",
    "#0-####",
    "#0-#####",
)
DEFAULT_FORMAT_LABEL = "<default>"


def atoi(value) -> int:
    """C ``atoi`` -- how CtDraw reads TXTMODE (0 when there are no digits)."""
    m = re.match(r"\s*([+-]?\d+)", str(value or ""))
    return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------- text forms
def to_caret(text: str) -> str:
    """A stored text as the state / array lists show it: CRLF -> ``^n``
    (strCRLFPairToCaretN)."""
    return (text or "").replace("\r\n", "^n")


def from_caret(text: str) -> str:
    """The list's text as it is stored: ``^n`` -> CRLF (strCaretNToCRLFPair)."""
    return (text or "").replace("^n", "\r\n")


def to_edit(text: str) -> str:
    """A stored text for a multi-line field (the line breaks as '\\n')."""
    return (text or "").replace("\r\n", "\n")


def from_edit(text: str) -> str:
    """A multi-line field's text as it is stored: every line break CRLF, and
    ``^n`` typed the CtDraw way, too."""
    s = from_caret(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\n", "\r\n")


# --------------------------------------------------------------------------- numeric format
_MASK = re.compile(r"^\s*[#0\-.,s]*#[#0\-.,s]*\s*(?:EU|!)?\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:EU|!)?\s*$", re.IGNORECASE)
_BAD_TAIL = re.compile(r"[()\"':]")


def is_format(fmt: str) -> bool:
    """Is ``fmt`` a number format ctgedit accepts?"""
    fmt = fmt or ""
    return (
        bool(fmt.strip())
        and len(fmt.strip()) <= FORMAT_MAX
        and (_MASK.match(fmt) is not None or _NUMBER.match(fmt) is not None)
    )


def split_numeric(text: str) -> Tuple[str, str]:
    """A numeric display's TXT_STR -> (expression, format)."""
    text = text or ""
    p = text.rfind(":")
    if p > 0:
        tail = text[p + 1 :]
        if text[0] == "(" and text[p - 1] == ")" and tail.strip() and _BAD_TAIL.search(tail) is None:
            return text[1 : p - 1], tail
        if _MASK.match(tail) is not None and text[:p].strip():
            return text[:p], tail
    return text, ""


def join_numeric(expression: str, fmt: str) -> str:
    """CtDraw's (%s):%s, the bare expression without a
    format; '' for an empty expression (the key is removed)."""
    if not (expression or "").strip():
        return ""
    return "(%s):%s" % (expression, fmt) if fmt else expression


# --------------------------------------------------------------------------- the page
@dataclass
class TextDisplay:
    """What the Display Value page shows (and the General page's Text).
    Every text is the STORED form (CRLF line breaks); ``states`` has
    2 ** len(conditions) rows and ``array`` 256, row 0 of both = ``static``."""

    mode: int = ON_OFF
    static: str = ""
    on_text: str = ""
    expression: str = ""
    conditions: List[str] = field(default_factory=lambda: [""] * DEFAULT_CONDITIONS)
    states: List[str] = field(default_factory=lambda: [""] * (2**DEFAULT_CONDITIONS))
    array: List[str] = field(default_factory=lambda: [""] * ARRAY_SIZE, repr=False)
    fmt: str = ""
    known_mode: bool = True  # the stored TXTMODE is one of the five types

    def copy(self) -> "TextDisplay":
        return replace(
            self, conditions=list(self.conditions), states=list(self.states), array=list(self.array)
        )


def _row(v: Optional[str]) -> str:
    return "" if v is None or v == UNSET else v


def condition_count(get) -> int:
    """n = the last non-blank of TXT_A..TXT_E; 3 when none."""
    for k in range(MAX_CONDITIONS - 1, -1, -1):
        if get(CONDITION_KEYS[k]) or "":
            return k + 1
    return DEFAULT_CONDITIONS


def read(props: Dict[str, str], caption: Optional[str]) -> TextDisplay:
    """What the page shows for a text whose properties are ``props`` and
    whose caption (the text tail) is ``caption`` (None: no tail -- TXT_OFF
    stands in)."""
    get = _getter(props)
    mode = atoi(get("TXTMODE"))
    known = mode in TYPES
    if not known:
        mode = ON_OFF
    static = caption if caption is not None else (get("TXT_OFF") or "")
    txt_str = get("TXT_STR") or ""
    num_expr, fmt = split_numeric(txt_str)
    if mode == NUMERIC:
        expr = num_expr
    elif mode == STRING:
        expr = txt_str
    else:
        expr = get("TXT_A") or ""
    n = condition_count(get)
    conds = [get(k) or "" for k in CONDITION_KEYS[:n]]
    conds[0] = expr
    states = [_row(get("STATE_%d" % i)) for i in range(2**n)]
    states[0] = static
    array = [_row(get("ARRAY_%d" % i)) for i in range(ARRAY_SIZE)]
    array[0] = static
    return TextDisplay(mode, static, get("TXT_ON") or "", expr, conds, states, array, fmt, known)


def resized_states(states: Sequence[str], n_conditions: int) -> List[str]:
    """The state rows for ``n_conditions`` conditions: kept by index, blank
    rows added / the rest dropped."""
    want = 2 ** max(0, min(MAX_CONDITIONS, n_conditions))
    out = list(states[:want])
    return out + [""] * (want - len(out))


def unstored_states(td: TextDisplay) -> List[int]:
    """The Multi-state rows holding a text that would be lost: rows past the
    2 ** n a reload shows (n = the last non-blank condition, 3 when all are
    blank) -- a condition added and left blank does not keep its rows."""
    if td.mode != MULTI_STATE:
        return []
    conds = written_conditions(td.conditions)
    n = len(conds) if any(c.strip() for c in conds) else DEFAULT_CONDITIONS
    return [i for i in range(2**n, len(td.states)) if td.states[i]]


def _relevant(td: TextDisplay, with_static: bool):
    m = td.mode
    static = (td.static,) if with_static else ()
    if m == ON_OFF:
        return (m, td.expression, td.on_text) + static
    if m == MULTI_STATE:
        conds = written_conditions(td.conditions)
        states = resized_states(td.states, len(conds))
        return (m, tuple(conds), tuple(states[1:])) + static
    if m == ARRAY:
        return (m, td.expression, tuple(td.array[1:])) + static
    if m == NUMERIC:
        return (m, td.expression, td.fmt)
    return (m, td.expression)


def has_changes(before: TextDisplay, after: TextDisplay) -> bool:
    return _relevant(before, True) != _relevant(after, True)


def cleared(td: TextDisplay) -> TextDisplay:
    """Clear Property: the CURRENT panel emptied (its expression, ON text,
    conditions back to 3 blank, rows after 0); the type and the static text
    stay (CtDraw's per-type clear functions)."""
    out = td.copy()
    out.expression = ""
    out.conditions[0] = ""
    if td.mode == ON_OFF:
        out.on_text = ""
    elif td.mode == MULTI_STATE:
        out.conditions = [""] * DEFAULT_CONDITIONS
        out.states = [td.static] + [""] * (2**DEFAULT_CONDITIONS - 1)
    elif td.mode == ARRAY:
        out.array = [td.static] + [""] * (ARRAY_SIZE - 1)
    elif td.mode == NUMERIC:
        out.fmt = ""
    return out


def target_values(td: TextDisplay) -> Dict[str, Optional[str]]:
    """Every key CtDraw's Apply of the page writes for ``td`` (None = removed), in the
    order it writes them: TXTMODE, the other types' keys removed, then the type's own."""
    m = td.mode
    out: Dict[str, Optional[str]] = {"TXTMODE": str(m)}
    if m != ON_OFF:
        out["TXT_ON"] = None
    if m != MULTI_STATE:
        for k in CONDITION_KEYS:
            out[k] = None
        for i in range(MAX_STATES):
            out["STATE_%d" % i] = None
    if m != ARRAY:
        for i in range(ARRAY_SIZE):
            out["ARRAY_%d" % i] = None
    if m not in (NUMERIC, STRING):
        out["TXT_STR"] = None
    static = td.static or None
    if m == ON_OFF:
        out["TXT_A"] = td.expression or None
        out["TXT_OFF"] = static
        out["TXT_ON"] = td.on_text or None
    elif m == MULTI_STATE:
        conds = written_conditions(td.conditions)
        for k, key in enumerate(CONDITION_KEYS):
            out[key] = (conds[k] or None) if k < len(conds) else None
        states = resized_states(td.states, len(conds))
        states[0] = td.static
        for i in range(MAX_STATES):
            out["STATE_%d" % i] = (states[i] or UNSET) if i < len(states) else None
        out["TXT_OFF"] = static
    elif m == ARRAY:
        out["TXT_A"] = td.expression or None
        out["TXT_OFF"] = static
        out["ARRAY_0"] = td.static or UNSET
        for i in range(1, ARRAY_SIZE):
            out["ARRAY_%d" % i] = td.array[i] or None
    elif m == NUMERIC:
        out["TXT_STR"] = join_numeric(td.expression, td.fmt) or None
    else:
        out["TXT_STR"] = td.expression or None
    return out


def apply_edits(
    props: Dict[str, str], caption: Optional[str], before: TextDisplay, after: TextDisplay
) -> Tuple[List[Tuple[str, Optional[str]]], Optional[str]]:
    """What Apply writes: ``([(key, value or None)], new caption or None)``."""
    get = _getter(props)
    m = after.mode
    if _relevant(before, False) != _relevant(after, False):
        target = target_values(after)
        if m == NUMERIC:
            stored = get("TXT_STR") or ""
            if (after.expression, after.fmt) == split_numeric(stored) and stored.strip():
                target["TXT_STR"] = stored
    elif after.static != before.static and m in (ON_OFF, MULTI_STATE, ARRAY):
        target = {"TXT_OFF": after.static or None}
        if m == MULTI_STATE:
            target["STATE_0"] = after.static or UNSET
        elif m == ARRAY:
            target["ARRAY_0"] = after.static or UNSET
    else:
        return [], None
    edits = [(k, v) for k, v in target.items() if get(k) != v]
    new_caption = None
    if caption is not None and m in (ON_OFF, MULTI_STATE, ARRAY) and after.static != caption:
        new_caption = after.static
    return edits, new_caption


def problems(td: TextDisplay, before: Optional[TextDisplay] = None) -> List[Tuple[str, str]]:
    """[(field, message)] of what stops the page being stored."""
    out: List[Tuple[str, str]] = []

    long = functools.partial(note_too_long, out)

    m = td.mode
    # (the object's text itself is not limited: it is the General page's caption)
    if m == ON_OFF:
        long("expression", "ON text when", td.expression, EXPRESSION_MAX)
        long("on_text", "ON text", td.on_text, TEXT_MAX)
    elif m == MULTI_STATE:
        conds = written_conditions(td.conditions)
        for k, c in enumerate(conds):
            long("condition:%d" % k, "Condition %s" % "ABCDE"[k], c, CONDITION_MAX)
        for i, s in enumerate(resized_states(td.states, len(conds))):
            if i:
                long("state:%d" % i, "The text of state %d" % i, s, TEXT_MAX)
        lost = unstored_states(td)
        if lost:
            kept = len(conds) if any(c.strip() for c in conds) else DEFAULT_CONDITIONS
            blank = "ABCDE"[kept : max(kept, len(td.conditions))]
            if len(blank) == 1:
                which = "Condition %s is blank" % blank
            elif blank:
                which = "Conditions %s and %s are blank" % (", ".join(blank[:-1]), blank[-1])
            else:
                which = "The conditions after %s are blank" % "ABCDE"[kept - 1]
            out.append(
                (
                    "state:%d" % lost[0],
                    "%s, so the text of state %d would not be stored (only states 0 to "
                    "%d are kept). Enter the condition, or delete it." % (which, lost[0], 2**kept - 1),
                )
            )
    elif m == ARRAY:
        long("expression", "Array expression", td.expression, EXPRESSION_MAX)
        for i in range(1, ARRAY_SIZE):
            long("array:%d" % i, "The text for value %d" % i, td.array[i], TEXT_MAX)
    elif m == NUMERIC:
        kept = before is not None and before.fmt == td.fmt
        if td.fmt and not kept and not is_format(td.fmt):
            out.append(
                (
                    "format",
                    "Format '%s' is not a number format. Use # for each digit "
                    "(at most %d characters), for example ####.#, #.## or #! "
                    "for the tag's engineering units." % (td.fmt, FORMAT_MAX),
                )
            )
        long(
            "expression",
            "Numeric expression with its format",
            join_numeric(td.expression, td.fmt),
            EXPRESSION_MAX,
        )
    else:
        long("expression", "String expression", td.expression, EXPRESSION_MAX)
    return out


def written_texts(td: TextDisplay) -> List[str]:
    """Every text the current type stores (for the code-page check)."""
    m = td.mode
    out = [td.static] if m in (ON_OFF, MULTI_STATE, ARRAY) else []
    if m == ON_OFF:
        out += [td.expression, td.on_text]
    elif m == MULTI_STATE:
        conds = written_conditions(td.conditions)
        out += conds + resized_states(td.states, len(conds))
    elif m == ARRAY:
        out += [td.expression] + list(td.array)
    else:
        out.append(td.expression)
        if m == NUMERIC:
            out.append(td.fmt)
    return out
