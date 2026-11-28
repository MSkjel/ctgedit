"""The names the editor shows for object kinds."""

from __future__ import annotations

from ..infra.paths import path_key

import re
from typing import NamedTuple

from PySide6 import QtCore, QtGui, QtWidgets

KIND_NAMES = {
    "line": "Line",
    "rectangle": "Rectangle",
    "ellipse": "Ellipse",
    "polygon": "Polygon",
    "pipe": "Pipe",
    "text": "Text",
    "number": "Number",
    "button": "Button",
    "symbol_set": "Symbol set",
    "trend": "Trend",
    "cicode_object": "Cicode object",
    "symbol": "Symbol",
    "genie": "Genie",
    "composite_genie": "Composite genie",
    "group": "Group",
    "unknown": "Object",
}


#: the plural of the words a count names that do not just take an 's' (:func:`plural`)
IRREGULAR_PLURALS = {
    "child": "children",
    "person": "people",
    "is": "are",
    "has": "have",
    "was": "were",
    "this": "these",
    "it": "they",
    "its": "their",
    "index": "indexes",
}


def plural_word(n: int, word: str) -> str:
    """``word`` for a count of ``n``: 'object' / 'objects', 'match' / 'matches', 'library' /
    'libraries' (and the few of :data:`IRREGULAR_PLURALS`)."""
    if n == 1 or not word:
        return word
    low = word.lower()
    many = IRREGULAR_PLURALS.get(low)
    if many is None:
        if low.endswith(("s", "x", "z", "ch", "sh")):
            many = word + "es"
        elif low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
            many = word[:-1] + "ies"
        else:
            many = word + "s"
    elif word[:1].isupper():
        many = many[:1].upper() + many[1:]
    return many


def plural(n: int, word: str, many: str = "") -> str:
    """'1 object' / '2 objects' / '0 objects' (status messages never say 'object(s)');
    ``many`` names an unusual plural ('1 genie member' / '3 genie members' need none)."""
    n = int(n)
    return "%d %s" % (n, word if n == 1 else (many or plural_word(n, word)))


def name_list(names, most: int = 10) -> str:
    """'a, b, c' for the first ``most`` names, then 'and 4 more' (a message names a few; the
    log or a list shows them all)."""
    names = [str(n) for n in names]
    head = ", ".join(names[:most])
    return head if len(names) <= most else "%s and %d more" % (head, len(names) - most)


def template_name(link: str) -> str:
    """A template named as its library spells it, 'fx_style.normal@XGA', from its reference
    as a page's link table holds it ('fx_style.normal@XGA.<ctt', with or without its stamp).
    A text that is no such reference is given back as it is."""
    from ..format import links
    from ..format.template_place import parse_ref_text

    text = str(link or "")
    lib, elem, ext = parse_ref_text(links.strip_stamp(text))
    return "%s.%s" % (lib, elem) if lib and elem and ext else text


def is_placed_symbol(obj) -> bool:
    """Is this a placed group-root symbol?"""
    if getattr(getattr(obj, "kind", None), "value", "") != "genie" or getattr(obj, "is_genie", False):
        return False
    ref = library_ref_of(obj)
    return bool(ref) and library_link_label(ref)[0] == "Symbol"


def kind_code(obj) -> str:
    """The object's kind code ('rectangle', 'symbol_set'), 'symbol' for a placed group-root
    symbol (:func:`is_placed_symbol`): what the selection filter and Select Same > Kind
    group by, as the rows name it."""
    k = getattr(getattr(obj, "kind", None), "value", "") or ""
    return "symbol" if k == "genie" and is_placed_symbol(obj) else k


def kind_name(obj) -> str:
    """The object's kind in words; the page's template root is 'Template', a placed
    group-root symbol 'Symbol' (:func:`is_placed_symbol`)."""
    raw = getattr(obj, "raw", {}) or {}
    if getattr(obj, "parent", None) is None:
        # (only a top-level object can be the template: the reference is read for those)
        ref = str(raw.get("_ref") or raw.get("_resolved_ref") or "").lower()
        if ref.endswith("<ctt"):
            return "Template"
    k = getattr(getattr(obj, "kind", None), "value", "") or ""
    if k == "genie" and is_placed_symbol(obj):
        return "Symbol"
    return KIND_NAMES.get(k, k.replace("_", " ").capitalize() or "Object")


#: words a Title Case step name keeps in lower case (not the first)
_SMALL_WORDS = frozenset(
    ("a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "into")
)
#: words a step name writes its own way
_STEP_WORDS = {"3d": "3D"}


def _key_words(key: str) -> str:
    """A record key in words: the Find / Replace field name (VISIBLE -> 'Hidden when'), a
    genie member's metadata ('METADATA_Speed' -> 'Metadata Speed'), else the key's parts
    ('DOWN_KEY' -> 'Down key')."""
    import re
    from ..features.find_replace import FIELD_NAMES

    k = key.strip().strip("%")
    up = k.upper()
    if up in FIELD_NAMES:
        return FIELD_NAMES[up]
    m = re.match(r"^(STATE|ARRAY)_(\d+)$", up)
    if m:
        return "%s %s text" % ("State" if m.group(1) == "STATE" else "Array row", m.group(2))
    if up.startswith("METADATA_"):
        return "Metadata " + k[len("METADATA_") :]
    parts = [p for p in re.split(r"[_\s]+", k) if p]
    return " ".join(p.lower() if p.isupper() else p for p in parts).capitalize() or "Property"


def step_title(label: str) -> str:
    """An undo step's name as the Edit menu, the History panel and the status bar show it:
    Title Case ('move' -> 'Move', 'update genies from library' -> 'Update Genies from
    Library'), a record key in words ('property VISIBLE' -> 'Hidden When'), no placeholder
    ('%Tag%'). The stored label stays as it is (the journal and the diagnostics read it)."""
    import re

    s = " ".join(str(label or "").split())
    if not s:
        return "Edit"
    m = re.match(r"^(remove )?property\s+(\S+)$", s, re.I)
    if m:
        s = ("remove " if m.group(1) else "") + _key_words(m.group(2))
    # a %parameter% placeholder and an empty '()' it leaves
    s = re.sub(r"%[A-Za-z_][\w.]*%", "", s)
    s = re.sub(r"\(\s*\)", "", s)
    # an upper-case record key left in the text (a label made from a key)
    s = re.sub(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[A-Z]{4,}\b", lambda k: _key_words(k.group(0)), s)
    s = " ".join(s.split()) or "Edit"
    out = []
    for k, w in enumerate(s.split(" ")):
        low = w.lower()
        if low in _STEP_WORDS:
            out.append(_STEP_WORDS[low])
        elif w != low:
            out.append(w)  # a word with capitals of its own: as it is
        elif k and low in _SMALL_WORDS:
            out.append(low)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


class ElidedLabel(QtWidgets.QLabel):
    """A one-line label that shrinks with its panel: the text cut with '...' where it does not
    fit, the whole text in its tool tip."""

    #: where the text is cut (a message: at its end)
    elide_mode = QtCore.Qt.ElideMiddle

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setText(text)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802 -- Qt naming
        self._full = str(text or "")
        self.setToolTip(self._full if len(self._full) > 24 else "")
        super().setText(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def text(self) -> str:  # (the whole text: what is shown may be cut)
        return self._full

    def minimumSizeHint(self):  # noqa: N802
        return QtCore.QSize(40, super().minimumSizeHint().height())

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._elide()

    def _elide(self) -> None:
        fm = QtGui.QFontMetrics(self.font())
        w = max(10, self.width() - 2)
        shown = fm.elidedText(self._full, self.elide_mode, w)
        if shown != QtWidgets.QLabel.text(self):
            super().setText(shown)


#: (project folder, template guid) -> the template's name (a composite genie's composition
#: cache names its template by guid only); bounded, emptied on a library refresh
_COMPOSITE_NAMES: dict = {}
from ..infra import caches as _caches  # noqa: E402

_caches.register("names.composite_templates", _COMPOSITE_NAMES, on=("refresh", "libraries", "tests"))


def composite_template_name(ref, project_dir: str) -> str:
    """The template name of a composite genie's reference ('_CompositionCaches\\{guid}\\
    cache.<hash>') from the composition cache of ``project_dir``, '' when not known."""
    import os
    import re as _re

    m = _re.search(r"\{([0-9a-fA-F-]{36})\}", str(ref or ""))
    if not m or not project_dir:
        return ""
    guid = m.group(1).lower()
    key = (path_key(project_dir), guid)
    hit = _COMPOSITE_NAMES.get(key)
    if hit is not None:
        return hit
    name = ""
    try:
        from ..format import composer as CP

        path = os.path.join(CP.store_dir(project_dir, guid), CP.copied_template_name(guid))
        if os.path.isfile(path):
            name = str(CP.load_model(path).template.name or "")
    except Exception:  # noqa: BLE001  # swallow-ok: a name for a label; the id shows then
        name = ""
    if len(_COMPOSITE_NAMES) > 512:
        _COMPOSITE_NAMES.clear()
    _COMPOSITE_NAMES[key] = name
    return name


def library_link_label(ref, project_dir: str = "") -> tuple:
    """('Genie' | 'Symbol' | 'Template' | 'Library element', 'lib.element') for a reference."""
    import re as _re

    s = " ".join(str(ref or "").split())
    s = _re.sub(r"#[0-9a-fA-F]{8}$", "", s)
    if "_CompositionCaches" in s:
        name = composite_template_name(s, project_dir)
        if not name:
            m = _re.search(r"cache\.([0-9a-fA-F]+)", s)
            name = ("instance " + m.group(1)[:8]) if m else "composite genie"
        return "Composite genie", name
    kind = "Library element"
    m = _re.search(r"\.<(ctm|ctl|ctt)$", s, _re.I)
    if m:
        kind = {"ctm": "Genie", "ctl": "Symbol", "ctt": "Template"}[m.group(1).lower()]
        s = s[: m.start()]
    else:
        m = _re.search(r"\s*\((ctm|ctl|ctt)\)$", s, _re.I)
        if m:
            kind = {"ctm": "Genie", "ctl": "Symbol", "ctt": "Template"}[m.group(1).lower()]
            s = s[: m.start()]
    return kind, s


# --------------------------------------------------------------------------- row labels
# What an object is known by (the Objects panel's rows; Goto Object and Find can share it):
# its name, a genie's element and the tag it is bound to, a text's caption. Qt-free: the
# model only (strings the derive made), nothing parsed.

#: the touch / keyboard child records of an object (its commands, not members)
_TOUCH_TC, _KEYBOARD_TC = 0x1010, 0x100B

#: a genie's bindings tried first for its main binding, in this order (lower case)
MAIN_BINDING_KEYS = ("tag", "tagprefix", "equipment", "item")
#: binding keys whose value is an equipment path (a row shows its last part)
EQUIPMENT_KEYS = ("equipment", "equip", "equipmentref")
#: the most of one expression the search keywords keep
KEYWORD_CHARS = 200

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_\\.]*")
_STRING = re.compile(r'"[^"]*"')
#: Cicode words that are never a tag
_CICODE_WORDS = frozenset(
    (
        "and",
        "or",
        "not",
        "bitand",
        "bitor",
        "bitxor",
        "mod",
        "true",
        "false",
        "if",
        "then",
        "else",
        "end",
        "return",
        "on",
        "off",
        "do",
        "while",
        "for",
        "to",
        "select",
        "case",
        "is",
        "int",
        "real",
        "string",
        "object",
        "quality",
        "timestamp",
        "global",
        "module",
        "local",
    )
)


def object_name_of(obj) -> str:
    """The object's name (Object Properties > Access > General > Name): its __AN_NAME__
    value, else the name its record carries; '' when it has none."""
    raw = getattr(obj, "raw", None) or {}
    props = raw.get("_props")
    name = props.get("__AN_NAME__") if isinstance(props, dict) else None
    if not name:
        name = raw.get("_name") or ""
    return " ".join(str(name).split())


def input_badges(page, obj) -> tuple:
    """(drawable member count, badge text): an object's touch / keyboard child records are
    its commands, shown as a badge ('[touch, 2 keys]') instead of members."""
    if page is None:
        return len(obj.members), ""
    objs = page.objects
    touch = keys = 0
    for m in obj.members:
        tc = objs[m].raw.get("_type_code") if m < len(objs) else None
        if tc == _TOUCH_TC:
            touch += 1
        elif tc == _KEYBOARD_TC:
            keys += 1
    parts = []
    if touch:
        parts.append("touch")
    if keys:
        parts.append(plural(keys, "key"))
    return len(obj.members) - touch - keys, ("[%s]" % ", ".join(parts)) if parts else ""


def library_ref_of(obj) -> str:
    """The library reference the object links to ('example.pump_b.<ctm'), or that a genie
    copy nested in a placed genie places (model/extras ``_def_ref``); '' for none."""
    raw = getattr(obj, "raw", None) or {}
    return str(
        getattr(obj, "genie_template", "")
        or raw.get("_ref")
        or raw.get("_resolved_ref")
        or raw.get("_def_ref")
        or ""
    )


def element_label(obj, page=None, short: bool = True) -> str:
    """The library element of a placed genie, symbol or template: its name ('pump_b'), or
    'lib.element' with ``short`` off; a composite genie's template name; '' for none."""
    ref = library_ref_of(obj)
    if not ref:
        return ""
    import os

    src = getattr(page, "source_path", "") or ""
    _kind, s = library_link_label(ref, os.path.dirname(src) if src else "")
    if "_CompositionCaches" in ref:
        return s
    if short and "." in s:
        s = s.split(".", 1)[1]
    return s


def _plain_binding(value) -> bool:
    """A binding value that names something (not a label '@(..)', TRUE / FALSE or a
    number)."""
    v = str(value or "").strip()
    if not v:
        return False
    if v.startswith("@(") and v.endswith(")"):
        return False
    if v.upper() in ("TRUE", "FALSE"):
        return False
    return not _NUMBER.match(v)


def main_binding(obj, subs=None) -> tuple:
    """(key, value) of the genie binding that tells this genie apart: Tag, TagPrefix,
    Equipment, Item, else the first one that names something; ('', '') for none. ``subs``:
    the bindings to read ({name: value}; None: the object's stored ones)."""
    if subs is None:
        subs = getattr(obj, "substitutions", None) or {}
    if not subs:
        return "", ""
    low = {}
    for k, v in subs.items():
        low.setdefault(str(k).strip("%").lower(), (str(k).strip("%"), v))
    for key in MAIN_BINDING_KEYS:
        hit = low.get(key)
        if hit is not None and _plain_binding(hit[1]):
            return hit[0], " ".join(str(hit[1]).split())
    for k, v in subs.items():
        if _plain_binding(v):
            return str(k).strip("%"), " ".join(str(v).split())
    return "", ""


def binding_text(key: str, value: str) -> str:
    """How a row shows a binding: an equipment path by its last part ('SPRAY02')."""
    if str(key).lower() in EQUIPMENT_KEYS and "." in value:
        return value.rsplit(".", 1)[1] or value
    return value


def composite_binding(page, index: int) -> tuple:
    """The main binding of the first genie inside composite genie ``index`` (its own
    bindings live in its content genies)."""
    if page is None or not 0 <= index < len(page.objects):
        return "", ""
    objs = page.objects
    stack = list(reversed(objs[index].members))
    seen = 0
    while stack and seen < 400:
        i = stack.pop()
        seen += 1
        if i >= len(objs):
            continue
        o = objs[i]
        if o.substitutions:
            k, v = main_binding(o)
            if v:
                return k, v
        stack.extend(reversed(o.members))
    return "", ""


def parameters_of(page, index: int) -> list:
    """[(parameter, value)] the genie ``index`` passes its element: its bindings (a genie
    nested in another one: the values that came down to it), a composite genie's main
    binding (its content genies hold the rest); [] for an object that is no genie."""
    if page is None or not 0 <= index < len(page.objects):
        return []
    obj = page.objects[index]
    if obj.kind.value == "composite_genie":
        k, v = composite_binding(page, index)
        return [(k, v)] if v else []
    subs = getattr(obj, "substitutions", None) or {}
    return [(str(k).strip("%"), " ".join(str(v).split())) for k, v in subs.items()]


def genie_chain(page, index: int) -> list:
    """The placed genies, composite genies and symbols ``index`` is part of, the outermost
    first, ``index`` last when it is one: what places each level of a nested tree."""
    if page is None or not 0 <= index < len(page.objects):
        return []
    objs = page.objects
    out = [a for a in reversed(page.ancestors(index)) if 0 <= a < len(objs) and library_ref_of(objs[a])]
    if library_ref_of(objs[index]):
        out.append(index)
    return out


def values_text(page, index: int, values=None) -> str:
    """'Equipment = Plant.P1; Label = P1': the values genie ``index`` gets, ``values(index)``
    (the document's genie_values: as the Graphics Builder composes them) when it gives any,
    else :func:`parameters_of`."""
    got = values(index) if values is not None else None
    return "; ".join("%s = %s" % kv for kv in (got or parameters_of(page, index)))


def chain_lines(page, index: int, memo: dict | None = None, values=None) -> list:
    """The tool tip lines of the genie tree ``index`` is in: one a level, the element and the
    values it gets ('sa_valve.base_info: Equipment = Plant.OV08', :func:`values_text`), the
    outermost first. ``memo``: each level's line, kept by a caller that asks for many rows."""
    lines = []
    for depth, a in enumerate(genie_chain(page, index)):
        line = memo.get(a) if memo is not None else None
        if line is None:
            el = element_label(page.objects[a], page, short=False) or kind_name(page.objects[a])
            params = values_text(page, a, values)
            line = el + (": " + params if params else "")
            if memo is not None:
                memo[a] = line
        lines.append("  " * depth + line)
    return lines


def expressions_of(obj, commands: bool = True) -> list:
    """The object's expressions (with ``commands`` also its touch and keyboard commands),
    in the order Object Properties shows them; empty ones left out."""
    a = getattr(obj, "animations", None)
    if a is None:
        return []
    out = []
    add = out.append
    if a.visibility is not None:
        add(a.visibility.expression)
    m = a.movement
    if m is not None:
        for e in (
            m.rotation_expr,
            m.scale_h_expr,
            m.scale_v_expr,
            m.move_h_expr,
            m.move_v_expr,
            m.slider_expr,
        ):
            add(e)
    if a.fill_colour is not None:
        for t in a.fill_colour.thresholds:
            add(t.expression)
    if a.fill_level is not None:
        add(a.fill_level.expression)
    if a.symbol_set is not None:
        out.extend(a.symbol_set.expressions)
    if a.display_value is not None:
        add(a.display_value.expression)
    if a.access_disable is not None:
        add(a.access_disable.expression)
    if a.trend is not None:
        out.extend(e for e, _c in a.trend.pens)
    add(a.cicode_expression)
    if commands:
        for t in a.touch:
            add(t.command)
        for k in a.keyboard:
            add(k.command)
    return [str(e) for e in out if e and str(e).strip()]


def _names_read(expr: str):
    """The names an expression reads, in order: not a function, a Cicode word, a genie
    parameter or a label."""
    s = _STRING.sub('""', expr)
    for m in _IDENT.finditer(s):
        word = m.group(0).rstrip(".\\")
        if not word or word.lower() in _CICODE_WORDS:
            continue
        if s[m.end() :].lstrip()[:1] == "(":
            continue  # a function
        if m.start() and s[m.start() - 1] in "%@":
            continue  # a genie parameter / a label
        yield word


def first_tag(obj) -> str:
    """The first tag the object's expressions read ('' when none)."""
    for expr in expressions_of(obj, commands=False):
        for word in _names_read(expr):
            return word
    return ""


def what_label(page, index: int, obj, values=None) -> str:
    """What the object shows or is made from: a genie's element and main binding
    ('pump_b  D100'), a text's or button's caption in quotes, a number's or symbol set's
    expression, a symbol's 'lib.element', another object's own text or class; '' for a
    plain shape or group."""
    k = getattr(getattr(obj, "kind", None), "value", "") or ""
    if k in ("genie", "composite_genie") or getattr(obj, "is_genie", False):
        ref = library_ref_of(obj)
        if "_CompositionCaches" in ref:
            # a composite genie: its template's name, '' when the project's composition
            # cache does not have it (the row leads with its binding then, not with the
            # 'instance 1a2b3c4d' of the Element column and the tool tip)
            import os

            src = getattr(page, "source_path", "") or ""
            el = composite_template_name(ref, os.path.dirname(src) if src else "")
        else:
            el = element_label(obj, page)
        key, val = (
            composite_binding(page, index)
            if k == "composite_genie"
            else main_binding(obj, copy_bindings(values, index, obj))
        )
        val = binding_text(key, val) if val else ""
        return "  ".join(x for x in (el, val) if x)
    if k in ("text", "button"):
        cap = " ".join(str(obj.style.text or "").split())
        return "'%s'" % cap[:60] if cap else ""
    if k == "number":
        dv = obj.animations.display_value
        return " ".join(str(dv.expression if dv is not None else "").split())[:60]
    if k == "symbol_set":
        ss = obj.animations.symbol_set
        return " ".join(str(ss.expressions[0] if ss is not None and ss.expressions else "").split())[:60]
    if k == "symbol":
        el = element_label(obj, page, short=False)
        if el:
            return el
    # (another object with a text of its own, or a class)
    cap = " ".join(str(obj.style.text or "").split())
    if cap:
        return "'%s'" % cap[:60]
    return " ".join(str(getattr(obj, "cls", "") or "").split())[:60]


class RowInfo(NamedTuple):
    """What :func:`row_info` finds for a row, in one pass over the object."""

    primary: str
    secondary: str
    name: str
    keywords: str  # what a search matches beyond the row's text
    tags: str  # the binding values and the names the expressions read
    lib: str  # the library reference
    animated: bool  # an expression is set
    commands: bool  # touch or keyboard commands


def copy_bindings(values, index: int, obj):
    """{name: value} of ``values(index)`` (the document's genie_values) for a genie copy nested
    in a placed genie (model/extras ``_def_ref``: what it stores is its element composed
    alone), None for another object or when it gives none (its stored bindings stand)."""
    if values is None or not (getattr(obj, "raw", None) or {}).get("_def_ref"):
        return None
    got = values(index)
    return dict(got) if got else None


def row_info(page, index: int, obj, values=None) -> RowInfo:
    """Everything a row shows and a search matches (:func:`row_label`,
    :func:`row_search_text`), the expressions read once."""
    kind = kind_name(obj)
    genie = kind in ("Genie", "Composite genie") or obj.is_genie
    members, badges = input_badges(page, obj)
    if members:
        kind += " (%d)" % members
    name = object_name_of(obj)
    # (a stored name that is not a name a user gives, '0', is a definition's own mark: the
    # row is not known by it; the search still finds it)
    shown = name if name[:1].isalpha() else ""
    what = what_label(page, index, obj, values)
    exprs = expressions_of(obj, commands=False)
    a = obj.animations
    cmds = (
        ([str(t.command) for t in a.touch if t.command] + [str(c.command) for c in a.keyboard if c.command])
        if (a.touch or a.keyboard)
        else []
    )
    words, first = [], ""
    for e in exprs:
        ws = list(_names_read(e[:KEYWORD_CHARS]))
        if ws and not first:
            first = ws[0]
        words.extend(ws)
    for e in cmds:
        words.extend(_names_read(e[:KEYWORD_CHARS]))
    primary = shown or what
    sec = []
    if primary:
        sec.append(kind)
        if shown and what:
            sec.append(what)
    else:
        primary = kind
    if not genie and first and first not in primary and first not in what:
        sec.append(first)
    if badges:
        sec.append(badges)
    # the search's keywords
    parts = [name] if name else []
    vals = []
    for kk, v in (copy_bindings(values, index, obj) or obj.substitutions or {}).items():
        v = " ".join(str(v).split())
        parts.append("%s=%s" % (str(kk).strip("%"), v))
        vals.append(v)
    ref = library_ref_of(obj)
    if ref:
        parts.append(ref)
    text = " ".join(str(obj.style.text or "").split())
    if text:
        parts.append(text[:KEYWORD_CHARS])
    parts.extend(e[:KEYWORD_CHARS] for e in exprs)
    parts.extend(e[:KEYWORD_CHARS] for e in cmds)
    return RowInfo(
        primary,
        "  ".join(sec),
        name,
        " ".join(parts),
        " ".join(vals + words),
        ref,
        bool(exprs),
        bool(cmds or badges),
    )


def row_label(page, index: int, obj) -> tuple:
    """(primary, secondary) of the object's row. The primary label is what the object is
    known by: its name, else :func:`what_label`, else its kind. The secondary part: the kind
    (when the primary label is not), how many objects it holds, what it is made from
    (when a name took the primary label), the first tag it reads and its command badges."""
    info = row_info(page, index, obj)
    return info.primary, info.secondary


def row_search_text(page, index: int, obj) -> tuple:
    """(keywords, tags, library reference) a search matches beyond the row's text: the
    name, every genie binding as key=value, the library reference, the caption and the
    expressions and commands (:data:`KEYWORD_CHARS` of each). ``tags`` = the binding
    values and the names the expressions read (the 'tag:' token)."""
    info = row_info(page, index, obj)
    return info.keywords, info.tags, info.lib
