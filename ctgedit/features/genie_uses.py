"""Where a genie's parameters go: every object field a parameter is substituted into,
and what kind of value that makes it (the genie form's fields and their help)."""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..format.cnum import is_digits
from .. import cicode_names as _cn
from ..format import genie_place as GP
from ..format.codes import TC
from .find_replace import FIELD_NAMES
from .page_checks import COMMAND_KEYS, EXPRESSION_KEYS

#: what a parameter's value is, from its uses (strongest first)
ROLE_COMMAND = "command"  # a whole Cicode command
ROLE_EXPRESSION = "expression"  # a whole expression, or a part of one (a tag, a number)
ROLE_NAME = "name"  # a part of the names it composes ('%Tag%_PV'): a tag prefix
ROLE_STRING = "string"  # a whole string a function is given ('AssResolve("%Equipment%")')
ROLE_TEXT = "text"  # display text: captions, tool tips, strings
ROLE_OTHER = "other"  # a privilege, an area, a colour, a mode

#: the fields that show text (the rest of the non-Cicode keys hold numbers and modes)
_TEXT_KEY = re.compile(
    r"^(TXT_OFF|TXT_ON|TEXT|STATE_\d+|ARRAY_\d+|COMMENT|NAME|DESC|__AN_NAME__|TITLE)$", re.IGNORECASE
)
_NAME_CHAR = re.compile("[%s.]" % _cn.PART_CHARS)
#: a field's object, by record type
_OBJECT = {
    TC.TEXT: "Text",
    TC.SYMBOL_SET: "Symbol set",
    TC.BUTTON: "Button",
    TC.BAR: "Bar graph",
    TC.TREND: "Trend",
    TC.STRING: "Text",
    TC.TEXT_AN: "Text",
    TC.NUMBER_LEGACY: "Number",
    TC.COLOUR_AN: "Colour",
    TC.CICODE: "Cicode object",
    TC.KEYBOARD: "Keyboard command",
    TC.ANIM_SYMBOL: "Animated symbol",
    TC.SLIDER: "Slider",
    TC.TOUCH: "Touch command",
    TC.GROUP: "Group",
    TC.RECTANGLE: "Rectangle",
    TC.ELLIPSE: "Ellipse",
    TC.POLYGON: "Polygon",
    TC.LINE: "Line",
    TC.PIPE: "Pipe",
    TC.ACTIVEX: "ActiveX object",
}


@dataclass(frozen=True)
class Use:
    """One field a parameter reaches: the field's key, its label, its kind (command,
    expression, text, other) and its template in the genie's own parameters."""

    key: str
    label: str
    kind: str
    template: str


@dataclass
class ParamUses:
    """A parameter's uses and the role they give it. ``patterns`` = the names it composes
    as (before, after) around the value ('%Tag%_PV' -> ('', '_PV'))."""

    name: str
    uses: List[Use] = field(default_factory=list)
    roles: set = field(default_factory=set)
    patterns: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def role(self) -> str:
        """The strongest role: the one the form's field is made for."""
        for r in (ROLE_COMMAND, ROLE_EXPRESSION, ROLE_NAME, ROLE_STRING, ROLE_TEXT, ROLE_OTHER):
            if r in self.roles:
                return r
        return ""

    def names(self, value: str) -> List[str]:
        """The names ``value`` composes ('Pump1' -> 'Pump1_PV', 'Pump1_SP')."""
        v = (value or "").strip()
        if not v:
            return []
        out, seen = [], set()
        for before, after in self.patterns:
            n = before + v + after
            if n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
        return out


def _kind_of(key: str) -> str:
    up = key.upper()
    if up in COMMAND_KEYS:
        return ROLE_COMMAND
    if up in EXPRESSION_KEYS:
        return ROLE_EXPRESSION
    if _TEXT_KEY.match(up):
        return ROLE_TEXT
    return ROLE_OTHER


def _label(tc: int, key: str) -> str:
    what = FIELD_NAMES.get(key.upper(), key)
    obj = _OBJECT.get(tc)
    if key.upper() in ("TXT_A", "TXT_STR") and tc == TC.SYMBOL_SET:
        what = "Expression"
    return "%s: %s" % (obj, what.lower() if obj else what) if obj else what


def _expand(template: str, bag) -> str:
    """``template`` with each %name% replaced by its template in ``bag`` (one nesting
    level out); a name the level does not template keeps the level's value."""
    out, i = [], 0
    while i < len(template):
        c = template[i]
        if c != "%":
            out.append(c)
            i += 1
            continue
        tok, j = GP.read_token(template, i)
        if not tok:
            out.append(template[i:] if tok is None else "%%")
            if tok is None:
                break
            i = j
            continue
        nm, dflt = GP.token_name(tok)
        t = bag[2].get(nm)
        if t is None:
            t = bag[1].get(nm) or dflt or ""
        out.append(t)
        i = j
    return "".join(out)


def _spans(template: str):
    """(start, end, name) of each %name% token of ``template``."""
    i = 0
    while i < len(template):
        if template[i] != "%":
            i += 1
            continue
        tok, j = GP.read_token(template, i)
        if tok is None:
            return
        if tok:
            nm, _d = GP.token_name(tok)
            if nm:
                yield i, j, nm
        i = j


def _strings(template: str) -> List[Tuple[int, int]]:
    """(start, end) of the string literals of ``template`` (quotes included)."""
    out, i, n = [], 0, len(template)
    while i < n:
        if template[i] != '"':
            i += 1
            continue
        j = i + 1
        while j < n and template[j] != '"':
            j += 2 if template[j] == "^" else 1
        out.append((i, min(j + 1, n)))
        i = j + 1
    return out


def _composed(template: str, start: int, end: int) -> Optional[Tuple[str, str]]:
    """(before, after) of the name around the token at start..end when the token is a
    part of a longer name ('%Tag%_PV', 'Area%N%.Level'), else None. Another token in
    the same name is no name this parameter makes alone: None too."""
    a = start
    while a > 0 and _NAME_CHAR.match(template[a - 1]):
        a -= 1
    b = end
    while b < len(template) and _NAME_CHAR.match(template[b]):
        b += 1
    before, after = template[a:start], template[end:b]
    if not before and not after:
        return None
    if (a > 0 and template[a - 1] == "%") or (b < len(template) and template[b] == "%"):
        return None
    if is_digits(before[:1]):
        return None
    return before, after


def _classify(p: ParamUses, use: Use) -> None:
    t = use.template
    strings = _strings(t)
    for s, e, nm in _spans(t):
        if nm.lower() != p.name.lower():
            continue
        if use.kind in (ROLE_TEXT, ROLE_OTHER):
            p.roles.add(use.kind)
            continue
        inside = next(((a, b) for a, b in strings if a < s and e <= b), None)
        comp = _composed(t, s, e)
        if comp is not None:
            # a part of a name: a tag in Cicode, or a tag name passed as a string
            p.roles.add(ROLE_NAME)
            if comp not in p.patterns:
                p.patterns.append(comp)
            continue
        if inside is not None:
            # the whole string: a name or a text a function is given; part of one: a text
            whole = t[inside[0] + 1 : inside[1] - 1] == t[s:e]
            p.roles.add(ROLE_STRING if whole else ROLE_TEXT)
            continue
        if use.kind == ROLE_COMMAND and t.strip() == t[s:e]:
            p.roles.add(ROLE_COMMAND)
        else:
            p.roles.add(ROLE_EXPRESSION)


def param_uses(records: Sequence, params: Sequence[str] = ()) -> "OrderedDict[str, ParamUses]":
    """The uses of each parameter of a genie element's ``records`` (its records as the
    element holds them), keyed by the lower-case name; ``params`` first, in order."""
    out: "OrderedDict[str, ParamUses]" = OrderedDict((p.lower(), ParamUses(p)) for p in params)
    for po in GP.convert_page_form(list(records)):
        try:
            r = GP.decode(po)
        except Exception:  # noqa: BLE001  # swallow-ok: a record that does not decode substitutes nothing
            continue
        ents = r.entries
        if not ents or len(ents) < 2:
            continue
        for key, tmpl in ents[0][2].pairs:
            if "%" not in tmpl or key.endswith(".NonGenie"):
                continue
            t = tmpl
            for lvl in range(1, len(ents) - 1):
                t = _expand(t, ents[lvl])
            kind = _kind_of(key)
            use = Use(key, _label(po.type_code, key), kind, t)
            for _s, _e, nm in _spans(t):
                p = out.get(nm.lower())
                if p is None:
                    p = out[nm.lower()] = ParamUses(nm)
                if use not in p.uses:
                    p.uses.append(use)
                    _classify(p, use)
    return out


def element_uses(lib_path: str, element: str, params: Sequence[str] = ()) -> "OrderedDict[str, ParamUses]":
    """:func:`param_uses` of library element ``element`` as the library stores it (kept
    while the library file is the same version)."""
    from ..format.symbols import library_version, open_library

    key = (os.path.normcase(lib_path), element.lower(), library_version(lib_path), tuple(params))
    hit = _USES.get(key)
    if hit is None:
        if len(_USES) > 64:
            _USES.clear()
        hit = _USES[key] = param_uses(open_library(lib_path).element_objects(element), params)
    return hit


def substitute(template: str, values: Dict[str, str], keep_missing: bool = False) -> str:
    """``template`` with the parameters' values (``values`` keyed by lower-case name;
    a missing one: its default, else nothing, or its token with ``keep_missing``)."""
    out, i = [], 0
    while i < len(template):
        c = template[i]
        if c != "%":
            out.append(c)
            i += 1
            continue
        tok, j = GP.read_token(template, i)
        if not tok:
            out.append(template[i:] if tok is None else "%%")
            if tok is None:
                break
            i = j
            continue
        nm, dflt = GP.token_name(tok)
        out.append(values.get(nm.lower()) or dflt or (template[i:j] if keep_missing else ""))
        i = j
    return "".join(out)


#: (library, element, version, parameters) -> the element's uses
_USES: dict = {}

from ..infra import caches as _caches  # noqa: E402

_caches.register("genie_uses.elements", _USES, on=("libraries", "refresh", "tests"))
