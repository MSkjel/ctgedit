"""Edit > Find / Replace: text in every field of a page."""

from __future__ import annotations

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
_PAGE_COLUMN_NAMES = {
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
    """Which names a search keeps: ``include`` and ``exclude`` are regular expressions,
    without case, found anywhere in a name ('' = no condition). A name list passes when one
    of its names matches ``include`` and none matches ``exclude``; an empty list passes
    only when there is no ``include``."""

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
    #: the fields, by key or by the name the Field column shows ('CMD', 'Command (CMD)',
    #: 'Tag', 'Genie parameter Tag')
    fields: NameFilter = NO_FILTER
    #: the genies and symbols an object is placed by or inside, as library.element
    #: ('pumps.pump_1'); an object in none has no name here
    genies: NameFilter = NO_FILTER
    #: the objects by name or AN ('Pump1', 'AN12'), their own or a group's they are in;
    #: the page's own properties have none
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
        m[k] = _traced_params(po, key, levels)
    return m[k]


def _traced_params(po, key: str, levels: int) -> Optional[frozenset]:
    try:
        r = GP.decode(po)
    except Exception:  # noqa: BLE001
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
    m = re.match(r"^(STATE|ARRAY)_(\d+)$", up)
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


def _kept_own_fields(doc, index: int, po, genie_root: bool, scopes) -> list:
    """:func:`_own_fields`, kept per record between searches while the document is the same
    (its derive and in-place change counters: an edit, a Replace and an Undo move one); a
    search whose scopes leave out the texts decodes no caption."""
    state = (getattr(doc, "derive_serial", 0), getattr(doc, "touch_serial", 0))
    memo = doc.__dict__.get("_find_fields_memo")
    if memo is None or memo[0] != state:
        memo = (state, {})
        doc.__dict__["_find_fields_memo"] = memo
    with_text = SCOPE_TEXT in scopes
    key = (index, id(po), bool(genie_root))
    got = memo[1].get(key)
    if got is not None and got[0] is po and (got[1] or not with_text):
        return got[2]
    fields = _own_fields(doc, index, po, genie_root, captions=with_text)
    memo[1][key] = (po, with_text, fields)
    return fields


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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        return False


def _is_composite(doc, index: int) -> bool:
    try:
        return doc.page.objects[index].kind.value == "composite_genie"
    except Exception:  # noqa: BLE001
        return False


def genie_params(doc, root: int, cache: Optional[dict] = None) -> Dict[str, str]:
    """The genie dialog's rows of placed genie ``root`` (name -> value;
    EditableDocument.genie_all_params), without the implicit AN."""
    if cache is not None and root in cache:
        return cache[root]
    try:
        rows = dict(doc.genie_all_params(root))
    except Exception:  # noqa: BLE001
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


def _member_route(
    doc, po, kind: str, key: str, genies: List[int], self_genie: bool, cache: dict, opts: FindOptions
) -> Tuple[str, Tuple[str, ...]]:
    """How a hit in a genie member is replaced: (reason, parameters)."""
    USER_MARKED_KEYS = _user_marked_keys()
    outer = genies[-1]
    try:
        composite = doc.page.objects[outer].kind.value == "composite_genie"
    except Exception:  # noqa: BLE001
        composite = False
    if composite:
        # its members are a composition-cache snapshot (no %parameter%
        # tokens): only the genie's own parameter rows can change
        return ("", ()) if key.upper() in USER_MARKED_KEYS else (COMPOSITE_REASON, ())
    params = genie_params(doc, outer, cache)
    tkey = "TXT_OFF" if kind == K_CAPTION else key
    # the placed level: 1 + the genies between the member and the outermost
    # one (the member itself counted when it is a nested genie root)
    level = len(genies) + (1 if self_genie else 0)
    traced = traced_params(po, tkey, level) if tkey else frozenset()
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
    objs = doc.parsed.page.objects
    link = _link_name(objs[outer]) if 0 <= outer < len(objs) else ""
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


def find_hits(doc, opts: FindOptions) -> List[Hit]:
    """Every field of ``doc`` that contains ``opts.text``: the page's own
    properties first, then the objects in page order (a genie's parameter
    rows on its root)."""
    if doc is None or not opts.text or getattr(doc, "page", None) is None:
        return []
    if opts.pages.on and not opts.pages.allows([_doc_page_name(doc)]):
        return []
    out: List[Hit] = []
    if SCOPE_PAGE in opts.scopes and opts.genies.allows(()) and opts.objects.allows(()):
        out.extend(h for h in _page_hits(doc, opts) if opts.fields.allows((h.key, h.label)))
    objs = doc.parsed.page.objects
    sem = doc.page.objects
    cache: dict = {}
    names: Dict[int, Tuple[str, ...]] = {}  # the Objects filter's names, by record
    for i, po in enumerate(objs):
        if i >= len(sem) or sem[i] is None:
            continue
        g = sem[i]
        try:
            anc = doc.page.ancestors(i)
        except Exception:  # noqa: BLE001
            anc = []
        if opts.objects.on and not opts.objects.allows(
            [n for a in [i] + list(anc) for n in _object_names(doc, a, objs, names)]
        ):
            continue
        if opts.genies.on and not opts.genies.allows(
            [_link_name(objs[a]) for a in [i] + list(anc) if _linked(objs, sem, a)]
        ):
            continue
        genies = [a for a in anc if a < len(objs) and objs[a].subtype_size & 0x200]
        # (the page template's own linked root is not a genie / symbol: its
        # records are 'Inside the page template', whatever this option says)
        inside = bool(genies) or any(
            a < len(objs) and objs[a].class_flag and objs[a].subtype_size & 0x1 and not _template_root(sem, a)
            for a in anc
        )
        if inside and not opts.inside_genies:
            continue
        template = bool(g.raw.get("_from_template"))
        if template and not opts.inside_templates:
            continue
        tpl_reason = TEMPLATE_REASON if template and not opts.template_editable else ""
        if not tpl_reason and not template and not opts.break_lock:
            # a locked unit (Edit > Lock: dwFlags 0x4 on its root) is not
            # edited until Break Lock Mode, like every other edit of it
            top = anc[-1] if anc else i
            if (
                0 <= top < len(objs)
                and objs[top].subtype_size & 0x4
                and not (top < len(sem) and sem[top] is not None and sem[top].raw.get("_from_template"))
            ):
                tpl_reason = LOCK_REASON
        root = _genie_root(doc, i, po, genies)
        if root and SCOPE_PARAM in opts.scopes:
            # CtDraw's load re-composes a composite from its cache.<hash>
            # element and restores every option, root and members alike
            p_reason = tpl_reason or (COMPOSITE_PARAM_REASON if _is_composite(doc, i) else "")
            for name, value in genie_params(doc, i, cache).items():
                if opts.fields.on and not opts.fields.allows((name, "Genie parameter %s" % name)):
                    continue
                if match_spans(value, opts.text, opts.match, opts.regex):
                    out.append(
                        Hit(
                            doc,
                            po,
                            i,
                            SCOPE_PARAM,
                            K_PARAM,
                            name,
                            value,
                            "Genie parameter %s" % name,
                            p_reason,
                            genie=i,
                            genie_po=po,
                        )
                    )
        if not opts.scopes - {SCOPE_PARAM, SCOPE_PAGE}:
            continue
        outer = genies[-1] if genies else None
        self_genie = bool(po.class_flag and po.subtype_size & 0x200)
        for scope, kind, key, value, label in _kept_own_fields(doc, i, po, root, opts.scopes):
            if scope not in opts.scopes or (opts.fields.on and not opts.fields.allows((key, label))):
                continue
            if not match_spans(value, opts.text, opts.match, opts.regex):
                continue
            reason, params = tpl_reason, ()
            if not reason and outer is not None:
                reason, params = _member_route(doc, po, kind, key, genies, self_genie, cache, opts)
            out.append(
                Hit(
                    doc,
                    po,
                    i,
                    scope,
                    kind,
                    key,
                    value,
                    label,
                    reason,
                    genie=outer,
                    genie_po=objs[outer] if outer is not None else None,
                    params=params,
                )
            )
    return out


def _doc_page_name(doc) -> str:
    path = getattr(doc, "path", "") or ""
    if path:
        return page_name_of(path)
    return getattr(getattr(doc, "page", None), "name", "") or ""


def _linked(objs, sem, a: int) -> bool:
    """Record ``a`` places a library element (a genie or a symbol; the page template's own
    root is not one)."""
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


def _page_hits(doc, opts: FindOptions) -> List[Hit]:
    out: List[Hit] = []
    try:
        rec = doc.page_record() if doc.page_record_available() else None
    except Exception:  # noqa: BLE001
        rec = None
    for col in PAGE_COLUMNS:
        v = (rec or {}).get(col) or ""
        v = v.rstrip()
        if v and match_spans(v, opts.text, opts.match, opts.regex):
            out.append(
                Hit(
                    doc,
                    None,
                    -1,
                    SCOPE_PAGE,
                    K_PAGEREC,
                    col,
                    v,
                    "Page %s" % _PAGE_COLUMN_NAMES.get(col, col).lower(),
                )
            )
    try:
        keys = doc.page_keyboard_commands() if rec is not None else []
    except Exception:  # noqa: BLE001
        keys = []
    for n, row in enumerate(keys):
        for col in ("CMD", "MSG_LOG"):
            v = (row.get(col) or "").rstrip()
            if v and match_spans(v, opts.text, opts.match, opts.regex):
                seq = (row.get("KEY_SEQ") or "").strip()
                out.append(
                    Hit(
                        doc,
                        None,
                        -1,
                        SCOPE_PAGE,
                        K_PAGEKEY,
                        col,
                        v,
                        "Page keyboard %s (%s)"
                        % ("command" if col == "CMD" else "log message", seq or n + 1),
                        sub=n,
                    )
                )
    try:
        assocs = doc.associations()
    except Exception:  # noqa: BLE001
        assocs = []
    for name, *parts in assocs:
        for p, v in enumerate(parts):
            if v and match_spans(v, opts.text, opts.match, opts.regex):
                out.append(
                    Hit(
                        doc,
                        None,
                        -1,
                        SCOPE_PAGE,
                        K_ASSOC,
                        name,
                        v,
                        "Association %s (%s)" % (name, _ASSOC_PARTS[p]),
                        sub=p,
                    )
                )
    return out


# --------------------------------------------------------------------------- closed pages
class PagePrefilter:
    """Can a closed page hold a hit? Checked from its bytes, before a full parse."""

    def __init__(self, opts: "FindOptions"):
        from ..format.page import ASCII_FAST
        from ..format.textcodec import MULTIBYTE

        text = opts.text or ""
        self.on = bool(text) and not opts.regex and not MULTIBYTE and ASCII_FAST and text.isascii()
        self.needle = text.lower()
        self.needle_bytes = self.needle.encode("ascii") if self.on else b""
        self.scopes = frozenset(opts.scopes)
        self._rows: Dict[str, dict] = {}  # project dir -> {name: pages.DBF row}
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
            name = os.path.splitext(os.path.basename(path))[0]
            if SCOPE_PAGE in self.scopes and self._page_tables_have(d, name):
                return True
            if SCOPE_PARAM in self.scopes and self._elements_have(path, d):
                return True
        except Exception:  # noqa: BLE001  # swallow-ok: may (the page is opened and searched)
            return True
        return False

    def _page_tables_have(self, d: str, name: str) -> bool:
        """The page's own rows hold the text (_page_hits: the pages.DBF row, else the one its
        registration writes, and its page keyboard commands)."""
        from ..io import pgregister
        from ..io.project import Project

        rows = self._rows.get(d)
        if rows is None:
            pj = Project(d)
            rows = None if not pj.has_page_table() else {}
            if rows is not None:
                for r in pj.page_rows():
                    # (Project.page_record: the first row of the name, matched truncated)
                    rows.setdefault(r.get("NAME", "").strip().lower(), r)
            self._rows[d] = rows
        if rows is None:
            return False  # (no page table: no page fields)
        n = self.needle
        rec = rows.get(name.strip().lower()[: Project.NAME_LEN]) or {"NAME": name, "TITLE": name}
        if any(n in (rec.get(c) or "").rstrip().lower() for c in PAGE_COLUMNS):
            return True
        for row in pgregister.page_keyboard_rows(d, name):
            if any(n in (row.get(c) or "").rstrip().lower() for c in ("CMD", "MSG_LOG")):
                return True
        return False

    def _elements_have(self, path: str, d: str) -> bool:
        """An element the page links (or one nested in it) holds the text: a genie row's
        declared default may come from it."""
        from ..format.pagesummary import read_link_names

        res = self._resolvers.get(d)
        if res is None:
            from ..format.symbols import SymbolResolver

            extra = [p for p in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if p]
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
def _resolve(doc, hit: Hit) -> Optional[int]:
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
        i = _resolve(doc, h)
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
            gi = _resolve(doc, Hit(doc, gpo, -1, "", "", "", "", ""))
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
            return None
        return td.static
    vals = own_values(doc, i)
    if h.kind == K_NAME and _get(vals, h.key) is None:
        try:
            return doc.object_name(i)  # a name the header holds alone
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
