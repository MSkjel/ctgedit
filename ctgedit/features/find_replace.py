"""Edit > Find / Replace: text in every field of a page."""

from __future__ import annotations

from ..infra.paths import lib_path_dirs

import os
import re
import weakref
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..format.cnum import c_atoi
from ..format import codes as _codes

# (read here, not in the functions: an import in a function runs PySide's import hook on
# every call)
from ..format import geometry as G
from ..format import writer
from ..format import genie_place as GP
from ..format.genie_place import root_key_kept
from ..format.symbols import reference_from_body
from ..infra.logsetup import note_exception as _note_exc

#: what a search looks in (FindOptions.scopes)
SCOPE_TEXT = "text"  # captions, display texts, button texts
SCOPE_EXPR = "expr"  # expressions, commands, the other object properties
SCOPE_PARAM = "param"  # genie parameters
SCOPE_NAME = "name"  # object names, descriptions, tool tips, metadata
SCOPE_PAGE = "page"  # page properties: title, events, keyboard, associations
ALL_SCOPES = frozenset((SCOPE_TEXT, SCOPE_EXPR, SCOPE_PARAM, SCOPE_NAME, SCOPE_PAGE))

#: how the text must match a field
MATCH_PART = "part"  # anywhere in the field (Studio / CtDraw default)
MATCH_WORD = "word"  # a whole tag name or word: not inside a longer name
MATCH_WHOLE = "whole"  # the whole field (CtDraw 'Match whole field only')

#: hit kinds
K_CAPTION, K_KEY, K_PARAM, K_NAME, K_META = "caption", "key", "param", "name", "metadata"
K_PAGEREC, K_PAGEKEY, K_ASSOC = "page_record", "page_keyboard", "association"

#: what a field can hold (CtDraw's field limits: an expression / command 254
#: -- dspval.EXPRESSION_MAX, genie substitution 0xfe --, a text 256
#: (dspval.TEXT_MAX), a metadata value 255)
LIMIT_EXPR, LIMIT_TEXT, LIMIT_META = 254, 256, 255

_TEXT_KEY = re.compile(r"^(TXT_OFF|TXT_ON|TEXT|STATE_\d+|ARRAY_\d+)$", re.IGNORECASE)
#: a state / array row key (upper case): its kind and number
_ROW_KEY = re.compile(r"^(STATE|ARRAY)_(\d+)$")
_NAME_KEYS = frozenset(("__AN_NAME__", "NAME", "DESC", "COMMENT"))
_UNSET = "#\n"  # a blank state / array row (dspval.UNSET)
_USER = "<User>"  # a genie member's own Access value (editing.USER_MARKED_KEYS)
_INPUT_TCS = _codes.INPUT_TCS

#: the object fields' plain names (the Field column); others show their key
FIELD_NAMES = {
    "VISIBLE": "Hidden when",
    "DISABLED": "Disable when",
    "CMD": "Command",
    "DOWN_CMD": "Down command",
    "REPEAT_CMD": "Repeat command",
    "DBLCLK_CMD": "Double-click command",
    "KEY_SEQ": "Key sequence",
    "ACTION": "Action",
    "MVX": "Horizontal movement",
    "MVY": "Vertical movement",
    "ROT": "Rotation",
    "SZX": "Horizontal scaling",
    "SZY": "Vertical scaling",
    "MVXSL": "Horizontal slider",
    "MVYSL": "Vertical slider",
    "ROTSL": "Rotational slider",
    "FIL": "Fill level",
    "COL_A": "Fill colour condition",
    "COL_ARR": "Fill colour array expression",
    "COL_EXP": "Fill colour expression",
    "TXT_A": "Display value expression",
    "TXT_STR": "Display value expression",
    "TXT_OFF": "Off text",
    "TXT_ON": "On text",
    "TEXT": "Button text",
    "__AN_NAME__": "Object name",
    "NAME": "Object name",
    "DESC": "Description",
    "COMMENT": "Tool tip",
    "AREA": "Area",
    "PRIV": "Privilege",
    "DEVICE": "Log device",
    "SET_ARR": "Symbol array expression",
    "AI_EXPR": "Alarm equipment",
    "EXPR": "Expression",
    "MSG_LOG": "Log message",
}
PAGE_COLUMNS = (
    "TITLE",
    "COMMENT",
    "PREV_PAGE",
    "NEXT_PAGE",
    "PARENT",
    "ENTRY_FUNC",
    "EXIT_FUNC",
    "BGND_FUNC",
    "READY_FUNC",
    "ACT_FUNC",
    "DEACT_FUNC",
)
PAGE_COLUMN_NAMES = {
    "TITLE": "Window title",
    "COMMENT": "Description",
    "PREV_PAGE": "Previous page",
    "NEXT_PAGE": "Next page",
    "PARENT": "Parent page",
    "ENTRY_FUNC": "On page entry",
    "EXIT_FUNC": "On page exit",
    "BGND_FUNC": "While page shown",
    "READY_FUNC": "On page shown",
    "ACT_FUNC": "On activate",
    "DEACT_FUNC": "On deactivate",
}
_ASSOC_PARTS = ("default value", "value on error", "description")

TEMPLATE_REASON = (
    "This object belongs to the page template. Edit the template (Tools > "
    "Edit Page Template), or allow editing template objects in Settings."
)
LOCK_REASON = "This object is locked (Edit > Lock Object). Unlock it or turn on Break Lock Mode."
COMPOSITE_REASON = "This object is part of a composite genie. Change the genie's options instead."
COMPOSITE_PARAM_REASON = "Change a composite genie's options with Edit Composite Genie."


@dataclass(frozen=True)
class NameFilter:
    """Names a search keeps: ``include`` / ``exclude`` are regular expressions, without case,
    found anywhere in a name ('' = no condition)."""

    include: str = ""
    exclude: str = ""

    @property
    def on(self) -> bool:
        return bool(self.include or self.exclude)

    def problem(self) -> str:
        """Why a pattern does not compile ('' = both do)."""
        for pat in (self.include, self.exclude):
            if pat:
                try:
                    re.compile(pat)
                except re.error as exc:
                    return str(exc)
        return ""

    def allows(self, names) -> bool:
        names = [n for n in names if n]
        if self.include:
            rx = _regex(self.include)
            if rx is None or not any(rx.search(n) for n in names):
                return False
        if self.exclude:
            rx = _regex(self.exclude)
            if rx is not None and any(rx.search(n) for n in names):
                return False
        return True


NO_FILTER = NameFilter()


@dataclass(frozen=True)
class FindOptions:
    """What to look for and where (the Find / Replace dialog's options)."""

    text: str
    scopes: frozenset = ALL_SCOPES
    match: str = MATCH_PART
    #: the objects a placed genie or a linked symbol places
    inside_genies: bool = True
    #: the page template's objects
    inside_templates: bool = False
    #: template objects may be changed (Settings 'Allow editing template
    #: objects on pages', or Break Lock Mode): else their rows are read-only
    template_editable: bool = False
    #: Break Lock Mode: a locked object (Edit > Lock, dwFlags 0x4 on its
    #: unit's root -- MainWindow._unit_locked) may be changed; else its rows
    #: are read-only, like every other edit of a locked object
    break_lock: bool = False
    #: the Find box's '.*': ``text`` is a regular expression (without case), and the
    #: replacement may name its groups (\1, \g<name>)
    regex: bool = False
    #: the pages searched, by name ('Mixing_L3')
    pages: NameFilter = NO_FILTER
    #: the fields, by key or Field column name ('CMD', 'Command', 'Tag')
    fields: NameFilter = NO_FILTER
    #: the genie or symbol an object belongs to, as library.element ('pumps.pump_1')
    genies: NameFilter = NO_FILTER
    #: the objects by name or AN ('Pump1', 'AN12'), or the group or genie they are in
    objects: NameFilter = NO_FILTER

    def filter_problem(self) -> str:
        """'<what>: <why>' for the first filter whose pattern does not compile ('' = none)."""
        for what, f in (
            ("Pages", self.pages),
            ("Fields", self.fields),
            ("Genies and symbols", self.genies),
            ("Objects", self.objects),
        ):
            err = f.problem()
            if err:
                return "%s: %s" % (what, err)
        return ""


def page_name_of(path: str) -> str:
    """The page name a path stands for (the Pages filter's name)."""
    return os.path.splitext(os.path.basename(path or ""))[0]


def page_allowed(opts: FindOptions, path: str) -> bool:
    """The Pages filter keeps the page at ``path``."""
    return opts.pages.allows([page_name_of(path)])


@dataclass
class Hit:
    """One field that contains the text."""

    doc: object
    po: object  # the record (identity survives index shifts); None = page
    index: int  # the record's index when found; -1 = the page itself
    scope: str
    kind: str
    key: str  # property key / parameter name / page column / association
    value: str  # the field's text when found (a <User> marker stripped)
    label: str  # the field's plain name
    reason: str = ""  # why it cannot be replaced ('' = it can)
    genie: Optional[int] = None  # the outermost placed genie (member / parameter rows)
    genie_po: object = None
    params: Tuple[str, ...] = ()  # the genie parameters a member row's replacement changes
    sub: int = 0  # page keyboard row / association part

    @property
    def replaceable(self) -> bool:
        return not self.reason

    @property
    def via_genie(self) -> bool:
        return self.kind == K_PARAM or bool(self.params)


@dataclass
class ReplaceReport:
    """What a replace did (per call; several documents add up)."""

    replaced: int = 0  # fields changed
    parameters: int = 0  # ... of them genie parameters
    read_only: int = 0  # rows that cannot be replaced
    gone: int = 0  # the text is no longer in the field
    problems: List[str] = field(default_factory=list)  # fields not changed, and why
    notes: List[str] = field(default_factory=list)  # changed, with a caveat
    docs: List[object] = field(default_factory=list)  # documents changed

    def add(self, other: "ReplaceReport") -> None:
        self.replaced += other.replaced
        self.parameters += other.parameters
        self.read_only += other.read_only
        self.gone += other.gone
        self.problems.extend(other.problems)
        self.notes.extend(other.notes)
        for d in other.docs:
            if not any(d is x for x in self.docs):  # (documents compare by identity)
                self.docs.append(d)


# --------------------------------------------------------------------------- matching
def _word(c: str) -> bool:
    return c.isalnum() or c == "_"


def _regex(needle: str):
    """The compiled expression of the Find box (without case), None when it does not compile."""
    hit = _RX_CACHE.get(needle)
    if hit is None and needle not in _RX_CACHE:
        try:
            hit = re.compile(needle, re.IGNORECASE)
        except re.error:
            hit = None
        if len(_RX_CACHE) > 64:
            _RX_CACHE.clear()
        _RX_CACHE[needle] = hit
    return hit


#: Find's compiled regular expressions (by the text)
_RX_CACHE: Dict[str, object] = {}

_folds_wide: Optional[frozenset] = None


def _wide_folds() -> frozenset:
    """The ASCII letters a case-blind regular expression also matches a non-ASCII character
    of the pages' code page with (code page 1254: 'i' matches the dotless i), read once."""
    global _folds_wide
    if _folds_wide is None:
        from ..format.textcodec import dec

        wide = set()
        for b in range(0x80, 0x100):
            try:
                c = dec(bytes([b]))
            except Exception:  # noqa: BLE001  # swallow-ok: a byte the code page has no character for
                continue
            for a in "abcdefghijklmnopqrstuvwxyz":
                if len(c) == 1 and not c.isascii() and re.fullmatch(a, c, re.IGNORECASE):
                    wide |= {a, a.upper()}
        _folds_wide = frozenset(wide)
    return _folds_wide


#: the ASCII letters a case-blind regular expression matches a non-ASCII letter with too
#: (the dotless and the dotted I, the long s, the Kelvin sign)
_UNICODE_FOLDS = frozenset("iskISK")


def required_text(pattern: str, unicode: bool = False) -> str:
    """The longest text (lower case, ASCII) that every match of the regular expression
    ``pattern`` holds, '' when none is known: a closed page whose bytes do not hold it cannot
    match (PagePrefilter); ``unicode``: in text, not the pages' bytes (find_index). Read from
    the parsed expression: a run of plain characters in its top-level sequence, in a group,
    or in a part repeated at least once."""
    try:
        from re import _constants as C, _parser as P

        tree = P.parse(pattern, re.IGNORECASE)
    except Exception:  # noqa: BLE001  # swallow-ok: no known text (every page is read)
        return ""
    repeats = {C.MAX_REPEAT, C.MIN_REPEAT, getattr(C, "POSSESSIVE_REPEAT", C.MAX_REPEAT)}
    wide = _UNICODE_FOLDS if unicode else _wide_folds()
    best = [""]

    def walk(seq) -> None:
        run: List[str] = []

        def flush() -> None:
            s = "".join(run)
            run.clear()
            if len(s) > len(best[0]):
                best[0] = s

        for op, av in seq:
            if op is C.LITERAL and av < 128 and chr(av) not in wide:
                run.append(chr(av).lower())
                continue
            flush()
            if op is C.SUBPATTERN:
                walk(av[-1])
            elif op in repeats and av[0] >= 1:
                walk(av[2])
        flush()

    walk(tree)
    return best[0]


def _regex_matches(text: str, needle: str, match: str):
    """The regular expression's matches in ``text`` as match objects (MATCH_WHOLE: the whole
    field; MATCH_WORD: not inside a longer name), [] when it does not compile."""
    rx = _regex(needle)
    if rx is None or not text:
        return []
    if match == MATCH_WHOLE:
        m = rx.fullmatch(text)
        return [m] if m is not None else []
    out = []
    for m in rx.finditer(text):
        j, k = m.span()
        if k == j:
            continue  # (an empty match replaces nothing)
        if match == MATCH_WORD and (
            (j > 0 and _word(text[j - 1]) and _word(text[j]))
            or (k < len(text) and _word(text[k]) and _word(text[k - 1]))
        ):
            continue
        out.append(m)
    return out


def match_spans(
    text: str, needle: str, match: str = MATCH_PART, regex: bool = False
) -> List[Tuple[int, int]]:
    """(start, end) spans of ``needle`` in ``text``, case-insensitive, not overlapping."""
    if regex:
        return [m.span() for m in _regex_matches(text, needle, match)] if needle else []
    if not needle or not text:
        return []
    low, n = text.lower(), needle.lower()
    if len(low) != len(text):  # a character whose lower case differs in length
        low = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
    if match == MATCH_WHOLE:
        return [(0, len(text))] if low == n else []
    out: List[Tuple[int, int]] = []
    i = 0
    while True:
        j = low.find(n, i)
        if j < 0:
            break
        k = j + len(n)
        if match == MATCH_WORD and (
            (j > 0 and _word(text[j - 1]) and _word(n[0]))
            or (k < len(text) and _word(text[k]) and _word(n[-1]))
        ):
            i = j + 1
            continue
        out.append((j, k))
        i = k
    return out


def replace_spans(text: str, spans: Sequence[Tuple[int, int]], replacement: str) -> str:
    out, last = [], 0
    for a, b in spans:
        out.append(text[last:a])
        out.append(replacement)
        last = b
    out.append(text[last:])
    return "".join(out)


def replaced(text: str, opts: FindOptions, replacement: str) -> Optional[str]:
    r"""``text`` with every match replaced; None when it has none. A regular expression's
    replacement may name its groups (\1, \g<name>); a group that is not there leaves the
    replacement as it was typed."""
    if opts.regex:
        ms = _regex_matches(text, opts.text, opts.match)
        if not ms:
            return None
        out, last = [], 0
        for m in ms:
            out.append(text[last : m.start()])
            try:
                out.append(m.expand(replacement))
            except re.error, IndexError:
                out.append(replacement)
            last = m.end()
        out.append(text[last:])
        return "".join(out)
    spans = match_spans(text, opts.text, opts.match, opts.regex)
    return replace_spans(text, spans, replacement) if spans else None


# --------------------------------------------------------------------------- reading
def _is_element_template(doc, index: int) -> bool:
    f = getattr(doc, "_template_record", None)
    try:
        return bool(f(index)) if f is not None else False
    except Exception:  # noqa: BLE001
        _note_exc("find_replace._is_element_template", index=index)
        return False


#: per record: what a search reads of it that only its payload decides (its strict value bag,
#: its traced parameters), kept while the record lives and its body is the same bytes object
#: (an edit replaces a record or its body_raw). {id(po): (weakref, body_raw, {what: value})}
_MEMO: Dict[int, tuple] = {}


def _memo(po) -> dict:
    k = id(po)
    hit = _MEMO.get(k)
    if hit is not None and hit[0]() is po and hit[1] is po.body_raw:
        return hit[2]
    data: dict = {}
    _MEMO[k] = (weakref.ref(po, lambda _r, k=k: _MEMO.pop(k, None)), po.body_raw, data)
    return data


def _value_bag(po):
    """writer.value_bag, memoised (a copy: the caller may change its list)."""
    m = _memo(po)
    if "vb" not in m:
        m["vb"] = writer.value_bag(po)
    vb = m["vb"]
    return None if vb is None else list(vb)


def own_values(doc, index: int) -> List[Tuple[str, str]]:
    """The record's OWN property values, in stored order: its strict value
    bag e0.b1 (a genie member: the substituted values), a genie-element
    member's TEMPLATE bag (what CtDraw's genie editor edits), else -- a
    record the strict walk cannot frame -- the object's properties."""
    po = doc.parsed.page.objects[index]
    if _is_element_template(doc, index):
        return list(doc.object_properties(index).items())
    vb = _value_bag(po)
    if vb is None:
        return list(doc.object_properties(index).items())
    return vb


def _get(pairs, key: str) -> Optional[str]:
    low = key.lower()
    for k, v in pairs:
        if k.lower() == low:
            return v
    return None


def traced_params(po, key: str, levels: int) -> Optional[frozenset]:
    """The outermost genie's parameters a member field is substituted from."""
    m = _memo(po)
    k = ("traced", key, levels)
    if k not in m:
        if "decoded" not in m:
            try:
                m["decoded"] = GP.decode(po)
            except Exception:  # noqa: BLE001 -- swallow-ok: a record the strict walk cannot read
                m["decoded"] = None
        m[k] = _traced_params(m["decoded"], key, levels)
    return m[k]


def _traced_params(r, key: str, levels: int) -> Optional[frozenset]:
    """traced_params of the record's strict walk ``r`` (None: it cannot be read)."""
    if r is None:
        return None
    ents = r.entries
    if not ents:
        return None
    n = len(ents) - 1
    tmpl = ents[0][2].get(key)
    if tmpl is None or n < max(1, levels):
        return frozenset()
    names = {x.lower() for x in GP.template_refs(GP.Bag([("", tmpl)]))}
    for lvl in range(1, n):
        nxt = set()
        for nm in names:
            t = ents[lvl][2].get(nm)
            if t:
                nxt |= {x.lower() for x in GP.template_refs(GP.Bag([("", t)]))}
        names = nxt
    return frozenset(names)


def field_label(key: str) -> str:
    up = key.upper()
    name = FIELD_NAMES.get(up)
    m = _ROW_KEY.match(up)
    if m:
        name = "%s %s text" % ("State" if m.group(1) == "STATE" else "Array row", m.group(2))
    return "%s (%s)" % (name, key) if name else key


def own_fields(doc, index: int) -> list:
    """(scope, kind, key, value, label) of every searchable own field of record ``index``
    (a placed genie root: only the keys CtDraw's page load keeps). For the page checks."""
    po = doc.parsed.page.objects[index]
    try:
        root = _genie_root(doc, index, po, [])
    except Exception:  # noqa: BLE001  # swallow-ok: a record the walk cannot frame is no root
        root = False
    return _own_fields(doc, index, po, root)


def _own_fields(doc, index: int, po, genie_root: bool, captions: bool = True):
    """(scope, kind, key, value, label) of every searchable own field (``captions`` False:
    a static text's caption is left out, not decoded)."""
    vals = own_values(doc, index)
    out = []
    skip = set()
    tc = po.type_code
    if tc == 0x05:
        mode = c_atoi(_get(vals, "TXTMODE"))
        if mode in (0, 1, 2):
            # a static / on-off / multi-state / array text: its OFF text IS
            # the caption (the tail the loader reads) -- TXT_OFF / STATE_0 /
            # ARRAY_0 mirror it (dspval.read)
            if captions:
                try:
                    cap = doc.text_caption(index)
                except Exception:  # noqa: BLE001
                    _note_exc("find_replace._own_fields", index=index)
                    cap = None
                static = cap if cap is not None else (_get(vals, "TXT_OFF") or "")
                if static:
                    out.append((SCOPE_TEXT, K_CAPTION, "", static, "Text"))
            skip |= {"TXT_OFF", "STATE_0", "ARRAY_0"}
        # (a numeric / string display shows its expression's value: its
        # caption is the mask, not a text; a TXT_OFF it keeps is listed as
        # the property it is)
    has_name = False
    # the key that holds the object's name (editing._name_key): __AN_NAME__,
    # else the legacy NAME an older builder wrote (the SA AlarmList / TabBar
    # objects, NAME == the header name) -- set_object_name keeps that form
    name_key = next((k for k, _v in vals if k.upper() == "__AN_NAME__"), None)
    if name_key is None:
        name_key = next((k for k, _v in vals if k == "NAME"), None)
    for k, v in vals:
        up = k.upper()
        if not v or k.startswith("!") or up in skip or v == _UNSET:
            continue
        if genie_root and not root_key_kept(k):
            continue  # CtDraw's page load drops a genie root's other keys
        if up == "AN" and genie_root:
            continue
        if up.startswith("METADATA_"):
            name, _eq, value = v.partition("=")
            if value:
                out.append((SCOPE_NAME, K_META, k, value, "Metadata %s" % name))
            continue
        if v.endswith(_USER):
            v = v[: -len(_USER)]
            if not v:
                continue
        if k == name_key:
            has_name = True
            out.append((SCOPE_NAME, K_NAME, k, v, "Object name"))
        elif up in _NAME_KEYS:
            out.append((SCOPE_NAME, K_KEY, k, v, field_label(k)))
        elif _TEXT_KEY.match(up):
            out.append((SCOPE_TEXT, K_KEY, k, v, field_label(k)))
        else:
            out.append((SCOPE_EXPR, K_KEY, k, v, field_label(k)))
    if not has_name and not _is_element_template(doc, index):
        hdr = G.parse_geom_header(po.body_raw)
        if hdr is not None and hdr.name:
            # a name the header holds alone (a genie member's 'SizeReference')
            out.append((SCOPE_NAME, K_NAME, "__AN_NAME__", hdr.name, "Object name"))
    return out


def _template_root(sem, a: int) -> bool:
    """``a`` is the page template's own (top-level, .ctt-linked) container."""
    try:
        o = sem[a]
        return o is not None and o.parent is None and bool(o.raw.get("_from_template"))
    except Exception:  # noqa: BLE001 -- swallow-ok: an index the model does not hold: not a template root
        return False


def _genie_root(doc, index: int, po, genies) -> bool:
    """``index`` is an OUTERMOST placed genie instance (its parameters are
    the genie dialog's rows; a nested genie's are derived from them)."""
    if genies or not po.class_flag or po.type_code != 0x08:
        return False
    if (po.subtype_size & 0x201) == 0x201:
        return True
    try:
        return doc.page.objects[index].kind.value == "composite_genie"
    except Exception:  # noqa: BLE001 -- swallow-ok: an index the page does not hold: not a genie root
        return False


def _is_composite(doc, index: int) -> bool:
    try:
        return doc.page.objects[index].kind.value == "composite_genie"
    except Exception:  # noqa: BLE001 -- swallow-ok: an index the page does not hold: not a composite
        return False


def genie_params(doc, root: int, cache: Optional[dict] = None) -> Dict[str, str]:
    """The genie dialog's rows of placed genie ``root`` (name -> value;
    EditableDocument.genie_all_params), without the implicit AN."""
    if cache is not None and root in cache:
        return cache[root]
    try:
        rows = dict(doc.genie_all_params(root))
    except Exception:  # noqa: BLE001
        _note_exc("find_replace.genie_params", root=root)
        rows = {}
    rows = {
        k: ("" if v is None else str(v))
        for k, v in rows.items()
        if str(k).upper() != "AN" and not str(k).startswith("!")
    }
    if cache is not None:
        cache[root] = rows
    return rows


def _link_name(po) -> str:
    try:
        ref = reference_from_body(po.body_raw) if po.subtype_size & 0x1 else None
    except Exception:  # noqa: BLE001
        _note_exc("find_replace._link_name")
        ref = None
    return "%s.%s" % (ref.library, ref.element) if ref is not None else ""


_user_marked = None


def _user_marked_keys():
    """editing.USER_MARKED_KEYS, imported once (editing is not imported with this module: the
    find helper process reads it too)."""
    global _user_marked
    if _user_marked is None:
        from ..editing import USER_MARKED_KEYS

        _user_marked = USER_MARKED_KEYS
    return _user_marked


def _member_route(pf, e, slot: int, kind: str, key: str, opts: FindOptions) -> Tuple[str, Tuple[str, ...]]:
    """How a hit in field ``slot`` of genie member ``e`` (an ObjectEntry of ``pf``) is
    replaced: (reason, parameters)."""
    USER_MARKED_KEYS = _user_marked_keys()
    outer = e.outer
    if pf.composite(outer):
        # its members are a composition-cache snapshot (no %parameter%
        # tokens): only the genie's own parameter rows can change
        return ("", ()) if key.upper() in USER_MARKED_KEYS else (COMPOSITE_REASON, ())
    params = pf.params(outer)
    tkey = "TXT_OFF" if kind == K_CAPTION else key
    traced = pf.traced(e, slot, tkey) if tkey else frozenset()
    holds = tuple(n for n, v in params.items() if match_spans(v, opts.text, opts.match, opts.regex))
    if traced is None:
        # the strict walk cannot frame the record: a field is taken to follow
        # every parameter holding the text -- a NAME never (a member's name is
        # the definition's; CtDraw greys it)
        uses = tuple(params) if kind != K_NAME else ()
    else:
        uses = tuple(n for n in params if n.lower() in traced)
    cands = tuple(n for n in holds if n in uses)
    if cands:
        return "", cands
    if key.upper() in USER_MARKED_KEYS:
        return "", ()
    link = pf.link(outer)
    what = "the genie %s" % link if link else "its genie"
    if uses:
        return (
            "This object is part of %s, and the genie parameter%s of this field (%s) "
            "%s not contain '%s'. Edit the genie definition to change it."
            % (
                what,
                "" if len(uses) == 1 else "s",
                ", ".join(uses),
                "does" if len(uses) == 1 else "do",
                opts.text,
            )
        ), ()
    return (
        "This object is part of %s. This field comes from the genie definition; edit "
        "the definition to change it." % what
    ), ()


# --------------------------------------------------------------------------- a page's fields
@dataclass(slots=True)
class ObjectEntry:
    """What every search reads of one record, whatever it looks for (PageFields.entries)."""

    index: int
    #: the record (None in a kept index: features/find_index)
    po: object
    #: its ancestors, innermost first (Page.ancestors)
    anc: Tuple[int, ...]
    #: placed by a genie or a linked symbol (Options > Inside genies)
    inside: bool
    #: the page template's (Options > Inside the page template)
    template: bool
    #: its unit is locked (Edit > Lock: dwFlags 0x4 on its root)
    locked: bool
    #: an outermost placed genie: its parameters are rows of their own
    root: bool
    #: the outermost genie it is a member of
    outer: Optional[int]
    #: the placed level of its fields: 1 + the genies between it and the outermost one (the
    #: record itself counted when it is a nested genie root)
    level: int


class PageFields:
    """The searchable fields of an open document, read once while it is the same
    (:func:`page_fields`); a search only matches them (:func:`match_fields`). Each part is
    read when a search first needs it. features/find_index.KeptFields is the same of a closed
    page, kept on disk."""

    def __init__(self, doc):
        self.doc = doc
        self.page_name = _doc_page_name(doc)
        objs = self._objs = doc.parsed.page.objects
        sem = self._sem = doc.page.objects
        self._fields: Dict[int, tuple] = {}
        self._params: Dict[int, Dict[str, str]] = {}
        self._names: Dict[int, Tuple[str, ...]] = {}
        self._links: Dict[int, str] = {}
        self._composite: Dict[int, bool] = {}
        entries = []
        for i, po in enumerate(objs):
            if i >= len(sem) or sem[i] is None:
                continue
            try:
                anc = tuple(doc.page.ancestors(i))
            except Exception:  # noqa: BLE001
                _note_exc("find_replace.PageFields", index=i)
                anc = ()
            genies = [a for a in anc if a < len(objs) and objs[a].subtype_size & 0x200]
            # (the page template's own linked root is not a genie / symbol: its records are
            # 'Inside the page template', whatever that option says)
            inside = bool(genies) or any(
                a < len(objs)
                and objs[a].class_flag
                and objs[a].subtype_size & 0x1
                and not _template_root(sem, a)
                for a in anc
            )
            top = anc[-1] if anc else i
            locked = bool(
                0 <= top < len(objs)
                and objs[top].subtype_size & 0x4
                and not (top < len(sem) and sem[top] is not None and sem[top].raw.get("_from_template"))
            )
            self_genie = bool(po.class_flag and po.subtype_size & 0x200)
            entries.append(
                ObjectEntry(
                    i,
                    po,
                    anc,
                    inside,
                    bool(sem[i].raw.get("_from_template")),
                    locked,
                    _genie_root(doc, i, po, genies),
                    genies[-1] if genies else None,
                    len(genies) + (1 if self_genie else 0),
                )
            )
        self.entries: List[ObjectEntry] = entries

    def fields(self, e: ObjectEntry, with_text: bool = True) -> tuple:
        """(scope, kind, key, value, label) of every searchable own field of ``e``
        (``with_text`` False: a static text's caption is left out, not decoded)."""
        got = self._fields.get(e.index)
        if got is not None and (got[0] or not with_text):
            return got[1]
        out = tuple(_own_fields(self.doc, e.index, e.po, e.root, captions=with_text))
        self._fields[e.index] = (with_text, out)
        return out

    def traced(self, e: ObjectEntry, slot: int, tkey: str) -> Optional[frozenset]:
        """The outermost genie's parameters field ``slot`` (key ``tkey``) is substituted from."""
        return traced_params(e.po, tkey, e.level)

    def params(self, i: int) -> Dict[str, str]:
        return genie_params(self.doc, i, self._params)

    def names(self, a: int) -> Tuple[str, ...]:
        return _object_names(self.doc, a, self._objs, self._names)

    def linked(self, a: int) -> bool:
        return _linked(self._objs, self._sem, a)

    def link(self, a: int) -> str:
        """'library.element' record ``a`` links ('' = none)."""
        got = self._links.get(a)
        if got is None:
            got = self._links[a] = _link_name(self._objs[a]) if 0 <= a < len(self._objs) else ""
        return got

    def composite(self, a: int) -> bool:
        got = self._composite.get(a)
        if got is None:
            got = self._composite[a] = _is_composite(self.doc, a)
        return got

    def associations(self) -> list:
        """Page Properties > Associations (read each time: not part of the objects' state)."""
        try:
            return list(self.doc.associations())
        except Exception:  # noqa: BLE001
            _note_exc("find_replace.PageFields.associations")
            return []


def page_fields(doc) -> PageFields:
    """``doc``'s PageFields, kept while the document is the same (its derive, in-place change
    and edit counters and the library generation: an edit, a Replace, an Undo and a library
    change each move one)."""
    state = (
        getattr(doc, "derive_serial", 0),
        getattr(doc, "touch_serial", 0),
        getattr(doc, "edit_serial", 0),
        _library_generation(),
    )
    memo = doc.__dict__.get("_find_fields")
    if memo is not None and memo[0] == state:
        return memo[1]
    pf = PageFields(doc)
    doc.__dict__["_find_fields"] = (state, pf)
    return pf


_editing_mod = None


def _library_generation() -> int:
    """editing.LIBRARY_GENERATION (imported once, as _user_marked_keys)."""
    global _editing_mod
    if _editing_mod is None:
        from .. import editing

        _editing_mod = editing
    return _editing_mod.LIBRARY_GENERATION[0]


#: one match: (record index; -1 = the page, scope, kind, key, value, label, reason, the genie
#: whose parameters it is or is a member of, the parameters its replacement changes, sub)
Match = Tuple[int, str, str, str, str, str, str, Optional[int], Tuple[str, ...], int]


def match_fields(pf, opts: FindOptions, page_rows=None) -> List[Match]:
    """Every field of ``pf`` (PageFields, or a closed page's find_index.KeptFields) that holds
    ``opts.text``: the page's own properties first, then the objects in page order (a
    genie's parameter rows on its root). ``page_rows()`` = (the pages.DBF row as the next
    save writes it or None, its page keyboard commands), read only when the page's own
    properties are searched."""
    if not opts.text:
        return []
    if opts.pages.on and not opts.pages.allows([pf.page_name]):
        return []
    text, how, rx = opts.text, opts.match, opts.regex
    out: List[Match] = []
    if SCOPE_PAGE in opts.scopes and opts.genies.allows(()) and opts.objects.allows(()):
        rec, keys = page_rows() if page_rows is not None else (None, [])
        out.extend(
            m for m in _page_matches(rec, keys, pf.associations(), opts) if opts.fields.allows((m[3], m[5]))
        )
    own_scopes = bool(opts.scopes - {SCOPE_PARAM, SCOPE_PAGE})
    with_text = SCOPE_TEXT in opts.scopes
    for e in pf.entries:
        i = e.index
        if opts.objects.on and not opts.objects.allows([n for a in (i,) + e.anc for n in pf.names(a)]):
            continue
        if opts.genies.on and not opts.genies.allows([pf.link(a) for a in (i,) + e.anc if pf.linked(a)]):
            continue
        if e.inside and not opts.inside_genies:
            continue
        if e.template and not opts.inside_templates:
            continue
        reason = TEMPLATE_REASON if e.template and not opts.template_editable else ""
        if not reason and not e.template and not opts.break_lock and e.locked:
            # a locked unit is not edited until Break Lock Mode, like every other edit of it
            reason = LOCK_REASON
        if e.root and SCOPE_PARAM in opts.scopes:
            # CtDraw's load re-composes a composite from its cache.<hash>
            # element and restores every option, root and members alike
            p_reason = reason or (COMPOSITE_PARAM_REASON if pf.composite(i) else "")
            for name, value in pf.params(i).items():
                label = "Genie parameter %s" % name
                if opts.fields.on and not opts.fields.allows((name, label)):
                    continue
                if match_spans(value, text, how, rx):
                    out.append((i, SCOPE_PARAM, K_PARAM, name, value, label, p_reason, i, (), 0))
        if not own_scopes:
            continue
        for slot, (scope, kind, key, value, label) in enumerate(pf.fields(e, with_text)):
            if scope not in opts.scopes or (opts.fields.on and not opts.fields.allows((key, label))):
                continue
            if not match_spans(value, text, how, rx):
                continue
            why, params = reason, ()
            if not why and e.outer is not None:
                why, params = _member_route(pf, e, slot, kind, key, opts)
            out.append((i, scope, kind, key, value, label, why, e.outer, params, 0))
    return out


def find_hits(doc, opts: FindOptions) -> List[Hit]:
    """Every field of ``doc`` that contains ``opts.text`` (:func:`match_fields`)."""
    if doc is None or not opts.text or getattr(doc, "page", None) is None:
        return []
    if opts.pages.on and not opts.pages.allows([_doc_page_name(doc)]):
        return []
    objs = doc.parsed.page.objects
    return [
        Hit(
            doc,
            objs[i] if i >= 0 else None,
            i,
            scope,
            kind,
            key,
            value,
            label,
            reason,
            genie=g,
            genie_po=objs[g] if g is not None else None,
            params=params,
            sub=sub,
        )
        for i, scope, kind, key, value, label, reason, g, params, sub in match_fields(
            page_fields(doc), opts, lambda: _doc_page_rows(doc)
        )
    ]


def _doc_page_name(doc) -> str:
    path = getattr(doc, "path", "") or ""
    if path:
        return page_name_of(path)
    return getattr(getattr(doc, "page", None), "name", "") or ""


def _linked(objs, sem, a: int) -> bool:
    """Record ``a`` places a genie or symbol (the page template's root does not count)."""
    return (
        0 <= a < len(objs)
        and bool(objs[a].class_flag and objs[a].subtype_size & 0x1)
        and not _template_root(sem, a)
    )


def _object_names(doc, a: int, objs, memo: dict) -> Tuple[str, ...]:
    """Record ``a``'s name and 'AN<n>' (the Objects filter's names of it)."""
    got = memo.get(a)
    if got is None:
        try:
            name = doc.object_name(a)
        except Exception:  # noqa: BLE001  # swallow-ok: a record without a name has its AN only
            name = ""
        an = G.live_an(objs[a]) if 0 <= a < len(objs) else -1
        got = memo[a] = (name or "", "AN%d" % an if an > 0 else "")
    return got


def _doc_page_rows(doc) -> tuple:
    """(the pages.DBF row as the next save writes it or None, the page keyboard commands)."""
    try:
        rec = doc.page_record() if doc.page_record_available() else None
    except Exception:  # noqa: BLE001
        _note_exc("find_replace._doc_page_rows")
        rec = None
    try:
        keys = doc.page_keyboard_commands() if rec is not None else []
    except Exception:  # noqa: BLE001
        _note_exc("find_replace._doc_page_rows")
        keys = []
    return rec, keys


#: the page rows of the closed pages (closed_page_rows): project folder -> (the stamps of its
#: pages.DBF and pgkey.DBF, {page name: the rows})
_PAGE_ROWS: Dict[str, tuple] = {}


def closed_page_rows(path: str) -> tuple:
    """:func:`_doc_page_rows` of the page at ``path`` as it is on disk (the document a read of
    it gives has no staged edits), kept while the project's tables are the same."""
    from ..format.fileio import file_stamp, find_ci
    from ..io import pgregister
    from ..io.project import Project

    d = os.path.dirname(os.path.abspath(path))
    name = page_name_of(path)
    stamps = (file_stamp(find_ci(d, "pages.DBF")), file_stamp(find_ci(d, "pgkey.DBF")))
    kept = _PAGE_ROWS.get(d)
    if kept is None or kept[0] != stamps:
        kept = _PAGE_ROWS[d] = (stamps, {}, [])
    low = name.lower()
    got = kept[1].get(low)
    if got is not None:
        return dict(got[0]) if got[0] is not None else None, [dict(r) for r in got[1]]
    if stamps[0] is None:
        got = (None, [])
    else:
        if not kept[2]:
            try:
                kept[2].append(Project(d).page_rows())
            except Exception:  # noqa: BLE001
                _note_exc("find_replace.closed_page_rows", path=path)
                kept[2].append([])
        from ..format.projects import PAGE_NAME_LEN

        # (io.project.Project.page_record: the first row of the name, matched truncated)
        trunc = name.strip().lower()[:PAGE_NAME_LEN]
        disk = next((r for r in kept[2][0] if r.get("NAME", "").strip().lower() == trunc), None)
        rec = dict(disk) if disk is not None else {"NAME": name, "TITLE": name}
        try:
            rows = pgregister.page_keyboard_rows(d, name)
        except Exception:  # noqa: BLE001
            _note_exc("find_replace.closed_page_rows", path=path)
            rows = []
        got = (rec, [{k: v for k, v in r.items() if k.upper() not in ("PAGE", "AN")} for r in rows])
    kept[1][low] = got
    return dict(got[0]) if got[0] is not None else None, [dict(r) for r in got[1]]


def _page_matches(rec, keys, assocs, opts: FindOptions) -> List[Match]:
    """The matches in the page's own properties: its pages.DBF row ``rec``, its page keyboard
    commands ``keys`` and its associations ``assocs``."""
    text, how, rx = opts.text, opts.match, opts.regex
    out: List[Match] = []
    for col in PAGE_COLUMNS:
        v = ((rec or {}).get(col) or "").rstrip()
        if v and match_spans(v, text, how, rx):
            out.append(
                (
                    -1,
                    SCOPE_PAGE,
                    K_PAGEREC,
                    col,
                    v,
                    "Page %s" % PAGE_COLUMN_NAMES.get(col, col).lower(),
                    "",
                    None,
                    (),
                    0,
                )
            )
    for n, row in enumerate(keys or ()):
        for col in ("CMD", "MSG_LOG"):
            v = (row.get(col) or "").rstrip()
            if v and match_spans(v, text, how, rx):
                seq = (row.get("KEY_SEQ") or "").strip()
                label = "Page keyboard %s (%s)" % ("command" if col == "CMD" else "log message", seq or n + 1)
                out.append((-1, SCOPE_PAGE, K_PAGEKEY, col, v, label, "", None, (), n))
    for name, *parts in assocs or ():
        for p, v in enumerate(parts):
            if v and match_spans(v, text, how, rx):
                label = "Association %s (%s)" % (name, _ASSOC_PARTS[p])
                out.append((-1, SCOPE_PAGE, K_ASSOC, name, v, label, "", None, (), p))
    return out


# --------------------------------------------------------------------------- closed pages
class PagePrefilter:
    """Can a closed page hold a hit? Checked from its bytes, before a full parse."""

    def __init__(self, opts: "FindOptions"):
        from ..format.page import ASCII_FAST
        from ..format.textcodec import MULTIBYTE

        text = opts.text or ""
        # (a regular expression: the text each of its matches holds)
        self.needle = required_text(text) if opts.regex else text.lower() if text.isascii() else ""
        self.on = bool(self.needle) and not MULTIBYTE and ASCII_FAST
        self.needle_bytes = self.needle.encode("ascii") if self.on else b""
        self.opts = opts
        self.scopes = frozenset(opts.scopes)
        self._resolvers: Dict[str, object] = {}  # project dir -> SymbolResolver
        self._elements: Dict[tuple, bool] = {}  # (library file, stamp, element) -> may

    def may_match(self, path: str, data: bytes) -> bool:
        """``path``'s page (its file's ``data``) may hold a hit: False only when it cannot."""
        if not self.on:
            return True
        try:
            if self.needle_bytes in data.lower():
                return True
            d = os.path.dirname(os.path.abspath(path))
            if SCOPE_PAGE in self.scopes:
                # the page's own rows (the pages.DBF row, else the one its registration
                # writes, and its page keyboard commands)
                rec, keys = closed_page_rows(path)
                if _page_matches(rec, keys, (), self.opts):
                    return True
            if SCOPE_PARAM in self.scopes and self._elements_have(path, d):
                return True
        except Exception:  # noqa: BLE001  # swallow-ok: may (the page is opened and searched)
            return True
        return False

    def _elements_have(self, path: str, d: str) -> bool:
        """An element the page links (or one nested in it) holds the text: a genie row's
        declared default may come from it."""
        from ..format.pagesummary import read_link_names

        res = self._resolvers.get(d)
        if res is None:
            from ..format.symbols import SymbolResolver

            extra = lib_path_dirs()
            res = self._resolvers[d] = SymbolResolver.for_page(path, extra_paths=extra)
        return any(self._element_has(res, n, set()) for n in read_link_names(path))

    def _element_has(self, res, link: str, seen: set) -> bool:
        from ..format.links import parse_link_table, strip_stamp
        from ..format.symbols import open_library

        n = strip_stamp(link)
        if n.startswith("\\\\"):
            return False  # (a language label: the page's own text)
        left, sep, ext = n.rpartition(".<")
        head, slash, last = left.rpartition("\\")
        lib, dot, elem = last.partition(".")
        if not sep or not dot:
            return True  # (not a library reference read here: may)
        lib = head + slash + lib
        files: List[str] = []
        # (the resolver tries a symbol set's value as a genie and a template too)
        for x in dict.fromkeys((ext.lower(), "ctm", "ctl", "ctt")):
            files += [f for f in res.library_files(lib, x) if f not in files]
        if not files:
            return True  # (not found here: may)
        low = elem.lower()
        for f in files:
            st = os.stat(f)
            key = (os.path.normcase(f), st.st_mtime_ns, st.st_size, low)
            if key in seen:
                return True  # (nested in itself: may)
            hit = self._elements.get(key)
            if hit is None:
                seen.add(key)
                L = open_library(f)
                blk = next(
                    (b for b in L.container.blocks if b.name.lower() == low and not b.is_deleted), None
                )
                if blk is None:
                    hit = False
                elif self.needle_bytes in bytes(blk.data).lower():
                    hit = True
                else:
                    pb = L.element(blk.name)
                    lt = parse_link_table(pb.link_table_raw) if pb is not None else None
                    hit = (
                        pb is None or lt is None or any(self._element_has(res, nm, seen) for _g, nm in lt[1])
                    )
                seen.discard(key)
                self._elements[key] = hit
            if hit:
                return True
        return False


# --------------------------------------------------------------------------- replacing
def resolve_hit(doc, hit: Hit) -> Optional[int]:
    """The record's current index, or None if it's gone."""
    objs = doc.parsed.page.objects
    if 0 <= hit.index < len(objs) and objs[hit.index] is hit.po:
        return hit.index
    for k, o in enumerate(objs):
        if o is hit.po:
            return k
    return G.successor_index(objs, hit.po, hit.index)


def _limit(kind: str, key: str) -> int:
    if kind == K_CAPTION or (key and _TEXT_KEY.match(key.upper())):
        return LIMIT_TEXT
    if kind == K_META:
        return LIMIT_META
    return LIMIT_EXPR


def replace_hits(
    doc, hits: Sequence[Hit], opts: FindOptions, replacement: str, label: str = "replace"
) -> ReplaceReport:
    """Replace the text in ``hits`` as one undo step."""
    from ..editing import UnencodableTextError, guard_text

    rep = ReplaceReport()
    hits = [h for h in hits if h.doc is doc]
    if not hits:
        return rep
    try:
        guard_text(replacement)
    except UnencodableTextError as exc:
        rep.problems.append("%s. Nothing was replaced." % exc)
        return rep
    own: List[Tuple[int, Hit]] = []
    genie: Dict[int, Tuple[object, List[str]]] = {}  # id(po) -> (po, params)
    page: List[Hit] = []
    for h in hits:
        if h.reason:
            rep.read_only += 1
            continue
        if h.po is None:
            page.append(h)
            continue
        i = resolve_hit(doc, h)
        if i is None:
            rep.gone += 1
            continue
        if h.kind == K_PARAM or h.params:
            gpo = h.genie_po if h.genie_po is not None else h.po
            names = [h.key] if h.kind == K_PARAM else list(h.params)
            entry = genie.setdefault(id(gpo), (gpo, []))
            for n in names:
                if n not in entry[1]:
                    entry[1].append(n)
            continue
        own.append((i, h))
    with doc.batch(label):
        # 1. the objects' own fields (body edits: no record index moves)
        done = set()
        for i, h in own:
            k = (id(h.po), h.kind, h.key.upper())
            if k in done:
                continue
            done.add(k)
            _replace_own(doc, i, h, opts, replacement, rep)
        # 2. genie parameters: one re-composition per genie, the LAST genie
        # first (a re-composition may change its subtree's length)
        order = []
        for gpo, names in genie.values():
            gi = resolve_hit(doc, Hit(doc, gpo, -1, "", "", "", "", ""))
            if gi is None:
                rep.gone += len(names)
                continue
            order.append((gi, names))
        for gi, names in sorted(order, key=lambda t: -t[0]):
            _replace_params(doc, gi, names, opts, replacement, rep)
        # 3. the page's own properties
        if page:
            _replace_page(doc, page, opts, replacement, rep)
    if rep.replaced:
        rep.docs.append(doc)
    return rep


def _current(doc, i: int, h: Hit) -> Optional[str]:
    """The field's current text (a <User> marker stripped)."""
    if h.kind == K_CAPTION:
        try:
            td = doc.text_display(i)
        except Exception:  # noqa: BLE001
            _note_exc("find_replace._current", index=i)
            return None
        return td.static
    vals = own_values(doc, i)
    if h.kind == K_NAME and _get(vals, h.key) is None:
        try:
            return doc.object_name(i)  # a name the header holds alone
        except Exception:  # noqa: BLE001
            _note_exc("find_replace._current", index=i)
            return None
    v = _get(vals, h.key)
    if v is None:
        return None
    if h.kind == K_META:
        return v.partition("=")[2]
    if v.endswith(_USER):
        v = v[: -len(_USER)]
    return v


def _replace_own(doc, i: int, h: Hit, opts: FindOptions, replacement: str, rep: ReplaceReport) -> None:
    cur = _current(doc, i, h)
    new = replaced(cur, opts, replacement) if cur is not None else None
    if new is None:
        rep.gone += 1
        return
    lim = _limit(h.kind, h.key)
    if len(new) > lim:
        rep.problems.append(
            "%s of AN %s: the new text would be %d characters long; the "
            "field holds %d." % (h.label, _an(h.po), len(new), lim)
        )
        return
    try:
        if h.kind == K_CAPTION:
            before = doc.text_display(i)
            after = before.copy()
            after.static = new
            if after.states:
                after.states[0] = new
            if after.array:
                after.array[0] = new
            ok = doc.apply_text_display(i, before, after)
        elif h.kind == K_NAME:
            ok = doc.set_object_name(i, new)
        elif h.kind == K_META:
            name = (_get(own_values(doc, i), h.key) or "").partition("=")[0]
            ok = doc.set_property(i, h.key, "%s=%s" % (name, new))
        else:
            ok = doc.set_property(i, h.key, new)
    except ValueError as exc:
        rep.problems.append("%s of AN %s: %s" % (h.label, _an(h.po), exc))
        return
    if ok:
        rep.replaced += 1
    elif ok is False:
        rep.problems.append("%s of AN %s could not be stored." % (h.label, _an(h.po)))


def _replace_params(
    doc, gi: int, names: List[str], opts: FindOptions, replacement: str, rep: ReplaceReport
) -> None:
    params = genie_params(doc, gi)
    low = {k.lower(): k for k in params}
    vals = {}
    for n in names:
        k = low.get(n.lower())
        new = replaced(params[k], opts, replacement) if k is not None else None
        if new is None:
            rep.gone += 1
            continue
        if len(new) > LIMIT_EXPR:
            rep.problems.append(
                "Genie parameter %s of AN %s: the new value would be %d "
                "characters long; a parameter holds %d."
                % (k, _an(doc.parsed.page.objects[gi]), len(new), LIMIT_EXPR)
            )
            continue
        vals[k] = new
    if not vals:
        return
    try:
        ok = doc.set_genie_bindings(gi, vals, label="replace")
    except ValueError as exc:
        rep.problems.append("Genie AN %s: %s" % (_an(doc.parsed.page.objects[gi]), exc))
        return
    if not ok:
        rep.problems.append(
            "The parameters of genie AN %s could not be changed." % _an(doc.parsed.page.objects[gi])
        )
        return
    rep.replaced += len(vals)
    rep.parameters += len(vals)
    try:
        composite = doc.page.objects[gi].kind.value == "composite_genie"
    except Exception:  # noqa: BLE001 -- swallow-ok: an index the page does not hold: not a composite
        composite = False
    if composite:
        # ctgedit cannot compose a composite genie (its element is a
        # composition-cache snapshot): only its parameters change, as with
        # the Presentation Options dialog
        rep.notes.append(
            "Composite genie AN %s: its parameters changed. Use Edit "
            "Composite Genie to update its objects." % _an(doc.parsed.page.objects[gi])
        )


def _replace_page(doc, hits: List[Hit], opts: FindOptions, replacement: str, rep: ReplaceReport) -> None:
    rec = {}
    cur = {}
    try:
        if doc.page_record_available():
            cur = doc.page_record() or {}
    except Exception:  # noqa: BLE001
        _note_exc("find_replace._replace_page")
        cur = {}
    keys = None
    n_keys = 0
    assocs = None  # [name, stored packed value, its parts] -- list2 order
    n_assocs = 0
    for h in hits:
        if h.kind == K_PAGEREC:
            new = replaced((cur.get(h.key) or "").rstrip(), opts, replacement)
            if new is None:
                rep.gone += 1
                continue
            # (checked one by one: a value too long for its column must not
            # keep the page's other fields from changing)
            err = doc.page_record_problem(h.key, new)
            if err:
                rep.problems.append("%s: %s" % (h.label, err))
                continue
            rec[h.key] = new
        elif h.kind == K_PAGEKEY:
            if keys is None:
                keys = [dict(r) for r in doc.page_keyboard_commands()]
            if h.sub >= len(keys):
                rep.gone += 1
                continue
            new = replaced((keys[h.sub].get(h.key) or "").rstrip(), opts, replacement)
            if new is None:
                rep.gone += 1
                continue
            row = dict(keys[h.sub], **{h.key: new})
            err = doc.page_keyboard_problem(row)
            if err:
                rep.problems.append("%s: %s" % (h.label, err))
                continue
            keys[h.sub] = row
            n_keys += 1
        elif h.kind == K_ASSOC:
            if assocs is None:
                at = doc.parsed.attributes
                assocs = [[k, v, v.split("\n", 2)] for k, v in (at.list2.pairs if at is not None else [])]
            row = next((a for a in assocs if a[0] == h.key), None)
            parts = row[2] if row is not None else []
            cur_v = parts[h.sub] if h.sub < len(parts) else ""
            new = replaced(cur_v, opts, replacement) if row is not None else None
            if new is None:
                rep.gone += 1
                continue
            parts = parts + [""] * (h.sub + 1 - len(parts))
            parts[h.sub] = new
            row[2] = parts
            # only this association's value is packed again, with the parts it
            # stores (list2 keeps 'Tag' as 'Tag', not 'Tag\n\n'); the others
            # keep their stored value
            row[1] = "\n".join(parts)
            n_assocs += 1
    if rec:
        try:
            if doc.set_page_record(rec, label="replace"):
                rep.replaced += len(rec)
        except ValueError as exc:
            rep.problems.append("Page properties: %s" % exc)
    if n_keys:
        try:
            if doc.set_page_keyboard_commands(keys, label="replace"):
                rep.replaced += n_keys
        except ValueError as exc:
            rep.problems.append("Page keyboard commands: %s" % exc)
    if n_assocs:
        try:
            if doc.set_associations([(a[0], a[1]) for a in assocs], label="replace"):
                rep.replaced += n_assocs
        except ValueError as exc:
            rep.problems.append("Associations: %s" % exc)


def _an(po) -> str:
    an = G.live_an(po) if po is not None else -1
    return str(an) if an > 0 else "?"


def replace_all(
    docs: Sequence[object], opts: FindOptions, replacement: str, label: str = "replace all"
) -> ReplaceReport:
    """Replace every replaceable match in ``docs`` -- ONE undo step per
    document; the search is run again first, so it replaces what is there
    now."""
    rep = ReplaceReport()
    for d in docs:
        hits = find_hits(d, opts)
        rep.add(replace_hits(d, hits, opts, replacement, label))
    return rep


from ..infra import caches as _caches  # noqa: E402

# (a record's own payload decides an entry: a library change leaves it right)
_caches.register("find_replace.memo", _MEMO, on=("tests",))
_caches.register("find_replace.regex", _RX_CACHE, on=("tests",))
# (keyed by the tables' stamps)
_caches.register("find_replace.page_rows", _PAGE_ROWS, on=("refresh", "tests"))
