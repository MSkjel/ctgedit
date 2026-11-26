"""Genie forms (``<library>.frm``): the dialog CtDraw shows when a genie is placed."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .cnum import c_atoi
from . import textcodec

__all__ = [
    "FormField",
    "LayoutItem",
    "GenieForm",
    "parse_forms",
    "lookup_form",
    "load_form_file",
    "form_search_dirs",
    "find_form",
    "FormError",
    "DraftItem",
    "format_form",
    "replace_form",
    "form_spans",
    "layout_text",
    "form_from_draft",
    "draft_from_form",
    "check_form",
    "form_file_for",
    "default_form",
]

#: the tokenizer's keywords
KEYWORDS = ("form", "readonly", "readwrite", "normal", "extended", "readonly_foreign", "readwrite_foreign")
#: the longest field / list names the record definition keeps
NAME_MAX = 31
#: the longest list filter
FILTER_MAX = 599
#: the longest NORMAL string
LAYOUT_MAX = 0x1000
#: a prompt may not run past this column (0x100 < column)
MAX_COLUMN = 0x100


class FormError(ValueError):
    """Why CtDraw would not use a FORM block (it shows the default form)."""


@dataclass
class FormField:
    """One record definition: ``<number>: "<name>", <size>, <access>, ...``."""

    number: int  # as written (1-based)
    name: str  # the genie parameter it edits (%name%)
    size: int  # the value's maximum length (the form record width)
    access: str = "readwrite"  # read but not used by CtDraw (every field is editable)
    list_form: str = ""  # a project-database form (citect.frm) / help list
    list_field: str = ""
    list_filter: str = ""

    @property
    def index(self) -> int:
        return self.number - 1

    @property
    def has_list(self) -> bool:
        return bool(self.list_form)


@dataclass
class LayoutItem:
    """One piece of the NORMAL layout."""

    kind: str  # 'field' | 'text' | 'line'
    line: int  # 1-based line of the NORMAL string
    col: int  # 1-based column (0 for a rule)
    width: int  # columns (a field: incl. its braces)
    text: str = ""  # a prompt's text
    field: int = -1  # a field's FormField.number

    def dlu_rect(self) -> Tuple[int, int, int, int]:
        """(x, y, cx, cy) in dialog units, as CtDraw lays it out."""
        if self.kind == "field":
            return (self.col * 4, self.line * 15 - 12, self.width * 4, 12)
        if self.kind == "text":
            return (self.col * 4, self.line * 15 - 10, self.width * 4 - 4, 10)
        return (0, self.line * 15 - 5, self.width * 4 - 4, 1)


@dataclass
class GenieForm:
    """A FORM block of a .frm file."""

    name: str  # the genie (element) it is for
    title: str = ""  # the dialog caption ('' = lib.element)
    fields: List[FormField] = field(default_factory=list)
    layout: List[LayoutItem] = field(default_factory=list)
    source: str = ""  # the .frm path
    error: str = ""  # why CtDraw would not use it ('' = valid)

    @property
    def valid(self) -> bool:
        return not self.error

    def field_by_number(self, number: int) -> Optional[FormField]:
        """The record definition a layout ``{number}`` shows (the LAST one
        written with that number, as CtDraw fills its array)."""
        out = None
        for f in self.fields:
            if f.number == number:
                out = f
        return out

    def placed_fields(self) -> List[FormField]:
        """The fields the layout shows, in layout (reading) order; a field
        the layout does not place is not on the form (sa_controls Treeview:
        'Display Icons')."""
        out, seen = [], set()
        for it in self.layout:
            if it.kind == "field" and it.field not in seen:
                seen.add(it.field)
                f = self.field_by_number(it.field)
                if f is not None:
                    out.append(f)
        return out

    def label_for(self, number: int) -> str:
        """The prompt written just before field ``number`` on its line (or
        the parameter name when the field has no prompt of its own)."""
        prev = None
        for it in self.layout:
            if it.kind == "field" and it.field == number:
                if prev is not None and prev.kind == "text" and prev.line == it.line:
                    return prev.text.strip()
                break
            prev = it
        f = self.field_by_number(number)
        return f.name if f is not None else ""


# ----------------------------------------------------------------- tokenizer --
@dataclass
class _Tok:
    kind: str  # 'str' | 'num' | 'kw' | 'id' | 'punct' | 'eof'
    value: object
    line: int
    start: int = 0  # (its characters in the text: text[start:end])
    end: int = 0


_ESCAPES = {'"': '"', "'": "'", "^": "^", "n": "\n", "r": "\r", "t": "\t"}


def _digit(c: str) -> bool:
    """A digit as CtDraw's ANSI reader sees one: 0-9 only ('²' is text)."""
    return "0" <= c <= "9"


def _lex(text: str) -> List[_Tok]:
    """CtDraw's (CTUTIL32) tokenizer for .frm files: '!' and '//' comments to
    the end of the line, '/* */' comments, Cicode strings ('^' escapes the
    next character: ^" ^' ^^ ^n ^r ^t), numbers, keywords / identifiers,
    single-character punctuation."""
    out: List[_Tok] = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c == "!" or (c == "/" and text.startswith("//", i)):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise FormError("line %d: a comment is not closed with */" % line)
            line += text.count("\n", i, j)
            i = j + 2
            continue
        if c == '"':
            start = line
            s0 = i
            buf = []
            i += 1
            while True:
                if i >= n:
                    raise FormError("line %d: a string is not closed with a quote" % start)
                ch = text[i]
                if ch == "^" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append(_ESCAPES.get(nxt, nxt))
                    if nxt == "\n":
                        line += 1
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                if ch == "\n":
                    line += 1
                buf.append(ch)
                i += 1
            out.append(_Tok("str", "".join(buf), start, s0, i))
            continue
        if _digit(c):
            j = i
            while j < n and _digit(text[j]):
                j += 1
            out.append(_Tok("num", c_atoi(text[i:j]), line, i, j))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            low = word.lower()
            out.append(_Tok("kw", low, line, i, j) if low in KEYWORDS else _Tok("id", word, line, i, j))
            i = j
            continue
        out.append(_Tok("punct", c, line, i, i + 1))
        i += 1
    out.append(_Tok("eof", None, line, n, n))
    return out


class _Parser:
    def __init__(self, toks: List[_Tok], k: int):
        self.t = toks
        self.k = k

    @property
    def cur(self) -> _Tok:
        return self.t[self.k]

    def advance(self) -> None:
        if self.cur.kind != "eof":
            self.k += 1

    def punct(self, ch: str) -> bool:
        """The current token is ``ch`` -> consume it."""
        if self.cur.kind == "punct" and self.cur.value == ch:
            self.advance()
            return True
        return False

    def expect_punct(self, ch: str, what: str) -> None:
        if not self.punct(ch):
            raise FormError("line %d: '%s' expected %s" % (self.cur.line, ch, what))

    def string(self, what: str, limit: int) -> str:
        """A string token (kept up to ``limit`` characters)."""
        if self.cur.kind != "str":
            raise FormError("line %d: a quoted %s expected" % (self.cur.line, what))
        s = str(self.cur.value)[:limit]
        self.advance()
        return s


def _parse_block(toks: List[_Tok], k: int, name: str, source: str) -> GenieForm:
    """The FORM block whose name string is token ``k - 1``."""
    form = GenieForm(name=name, source=source)
    p = _Parser(toks, k)
    try:
        if p.punct(","):
            form.title = p.string("form title", LAYOUT_MAX)
        p.expect_punct(";", "after the form name")
        # -- record definitions: while the token is a number
        while p.cur.kind == "num":
            num = int(p.cur.value)
            line = p.cur.line
            p.advance()
            p.expect_punct(":", "after field number %d" % num)
            fname = p.string("field name", NAME_MAX)
            p.expect_punct(",", "after the field name %r" % fname)
            size = int(p.cur.value) if p.cur.kind == "num" else 0
            p.advance()
            f = FormField(num, fname, size)
            if p.punct(","):
                tok = p.cur
                f.access = str(tok.value) if tok.kind in ("kw", "id") else ""
                p.advance()
                if p.punct(","):
                    f.list_form = p.string("list form", NAME_MAX)
                    p.expect_punct(",", "after the list form %r" % f.list_form)
                    f.list_field = p.string("list field", NAME_MAX)
                    if p.punct(","):
                        f.list_filter = p.string("list filter", FILTER_MAX)
            p.expect_punct(";", "at the end of field %d (line %d)" % (num, line))
            form.fields.append(f)
        # -- the field array: numbers 1..count
        count = len(form.fields)
        for f in form.fields:
            if not 1 <= f.number <= count:
                raise FormError(
                    "field %d (%s): the form has %d fields, so the fields must be "
                    "numbered 1 to %d" % (f.number, f.name, count, count)
                )
        # -- NORMAL (optional keyword) + the layout string
        if p.cur.kind == "kw" and p.cur.value == "normal":
            p.advance()
        if p.cur.kind != "str":
            raise FormError("line %d: the NORMAL layout string expected" % p.cur.line)
        text = str(p.cur.value)[:LAYOUT_MAX]
        p.advance()
        form.layout = parse_layout(text, form)
    except FormError as e:
        form.error = str(e)
    return form


def parse_layout(text: str, form: Optional[GenieForm] = None) -> List[LayoutItem]:
    """Parse a form's NORMAL string into layout items."""
    text = re.sub(r"\r(.|$)", "\x00", text.replace("\n", " "), flags=re.S).replace("\x00", "\n")
    items: List[LayoutItem] = []
    widths: Dict[int, int] = {}
    count = 0
    if form is not None:
        count = len(form.fields)
        for f in form.fields:
            widths[f.number] = f.size
    line, col, i, n = 1, 1, 0, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            col = 1
            i += 1
            continue
        if c.isspace():
            col += 1
            i += 1
            continue
        if c == "{":
            j = i + 1
            if j < n and _digit(text[j]):
                k = j
                while k < n and _digit(text[k]):
                    k += 1
                num = c_atoi(text[j:k])
                end = text.find("}", k)
                nl = text.find("\n", k)
                if end < 0 or (0 <= nl < end):
                    raise FormError("layout line %d: field {%d is not closed with }" % (line, num))
                width = end - i + 1
                if form is not None and (num < 1 or num > count or not widths.get(num)):
                    raise FormError("layout line %d: {%d} is not a defined field of the form" % (line, num))
                items.append(LayoutItem("field", line, col, width, field=num))
                col += width
                i = end + 1
                continue
            k = j
            while k < n and text[k].isalpha():
                k += 1
            word = text[j:k]
            if not word:
                raise FormError(
                    "layout line %d: '{' must be followed by a field number (no space) or LINE" % line
                )
            if word.upper() != "LINE":
                raise FormError("layout line %d: unknown layout item {%s" % (line, word))
            end, value = k, 0
            while end < n and text[end] not in "}\n":
                if _digit(text[end]):
                    value = value * 10 + int(text[end])
                end += 1
            if value == 0:
                raise FormError("layout line %d: {LINE} needs a length, e.g. {LINE, 100}" % line)
            if end >= n or text[end] != "}":
                raise FormError("layout line %d: {LINE is not closed with }" % line)
            items.append(LayoutItem("line", line, 0, value))
            i = end + 1  # (CtDraw does not advance the column)
            continue
        j = i
        while j < n and text[j] not in "{\n":
            j += 1
        if col + (j - i) - 1 > MAX_COLUMN:
            raise FormError("layout line %d is longer than %d characters" % (line, MAX_COLUMN))
        items.append(LayoutItem("text", line, col, j - i + 1, text=text[i:j]))
        col += j - i
        i = j
    return items


def parse_forms(text: str, source: str = "") -> List[GenieForm]:
    """Every FORM block of a .frm file, in file order, each with its own
    ``error`` ('' = CtDraw would use it). A tokenizer error (an unclosed
    string or comment) leaves the blocks before it."""
    try:
        toks = _lex(text)
    except FormError as e:
        # the blocks before the bad token still stand
        head = text
        m = re.search(r"line (\d+)", str(e))
        if m:
            head = "\n".join(text.split("\n")[: max(0, int(m.group(1)) - 1)])
        forms = parse_forms(head, source) if head != text else []
        forms.append(GenieForm(name="", source=source, error=str(e)))
        return forms
    out = []
    for k in range(len(toks) - 1):
        t = toks[k]
        if t.kind == "kw" and t.value == "form" and toks[k + 1].kind == "str":
            out.append(_parse_block(toks, k + 2, str(toks[k + 1].value), source))
    return out


def lookup_form(forms: Sequence[GenieForm], element: str) -> Optional[GenieForm]:
    """The form CtDraw uses for ``element`` from one file's blocks: the last
    matching block that parses, up to the first matching block that fails."""
    found = None
    low = element.lower()
    for f in forms:
        if f.name.lower() != low:
            continue
        if not f.valid:
            break
        found = f
    return found


_FILE_CACHE: Dict[str, Tuple[Tuple[int, int], List[GenieForm]]] = {}


def load_form_file(path: str) -> List[GenieForm]:
    """The FORM blocks of a .frm file (ANSI text, as CtDraw reads it), cached
    until the file changes."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    key = os.path.normcase(os.path.abspath(path))
    hit = _FILE_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, "rb") as fh:
            text = textcodec.dec(fh.read())
    except OSError:
        return []
    forms = parse_forms(text, path)
    _FILE_CACHE[key] = (sig, forms)
    return forms


def form_search_dirs(project_dir: str) -> List[str]:
    """The folders CtDraw searches for ``<library>.frm``, in order."""
    from .projects import included_project_names, resolve_project_dirs, sibling_install_user_dirs

    project_dir = os.path.abspath(project_dir)
    user_dir = os.path.dirname(project_dir)
    user_dirs = [user_dir] + sibling_install_user_dirs(user_dir)
    masters: Dict[str, Dict[str, str]] = {}
    out: List[str] = []
    seen = set()

    def add(d: str) -> None:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)

    for name in ("System", "Include"):
        for d in resolve_project_dirs(name, user_dirs, masters):
            add(d)
    walking = set()

    def walk(d: str) -> None:
        key = os.path.normcase(os.path.abspath(d))
        if key in walking:  # an include cycle
            return
        walking.add(key)
        base = os.path.basename(d.rstrip("\\/")).lower()
        if base not in ("system", "include"):
            for name in included_project_names(d):
                for sub in resolve_project_dirs(name, user_dirs, masters):
                    walk(sub)
        add(d)

    walk(project_dir)
    return out


def _find_file(folder: str, name: str) -> Optional[str]:
    low = name.lower()
    try:
        for f in os.listdir(folder):
            if f.lower() == low:
                return os.path.join(folder, f)
    except OSError:
        pass
    return None


def find_form(project_dir: str, library: str, element: str) -> Optional[GenieForm]:
    """The FORM CtDraw's genie dialog uses for ``library.element`` placed on
    a page of ``project_dir`` -- None means the default form (the genie's
    declared parameters)."""
    if not library or not element or not project_dir:
        return None
    for d in form_search_dirs(project_dir):
        path = _find_file(d, library + ".frm")
        if path is None:
            continue
        form = lookup_form(load_form_file(path), element)
        if form is not None:
            return form
    return None


def form_problems(project_dir: str, library: str, element: str) -> List[Tuple[str, str]]:
    """(path, reason) of every FORM block for ``library.element`` on the
    search path that CtDraw would NOT use -- why a genie shows its default
    form although a .frm names it."""
    out = []
    if not library or not element:
        return out
    low = element.lower()
    for d in form_search_dirs(project_dir):
        path = _find_file(d, library + ".frm")
        if path is None:
            continue
        for f in load_form_file(path):
            if f.name.lower() == low and not f.valid:
                out.append((path, f.error))
            elif not f.name and f.error:
                out.append((path, f.error))
    return out


# ----------------------------------------------------------------- writing --
@dataclass
class DraftItem:
    """One piece of a form being edited: a prompt, a field (by its definition) or a rule,
    at its line and column (1-based) and width in columns (a field: with its braces; a rule:
    its length)."""

    kind: str  # 'text' | 'field' | 'line'
    line: int
    col: int
    width: int
    text: str = ""  # a prompt's text
    field: Optional[FormField] = None  # a field's definition (its number is given on save)


def _quote(s: str) -> str:
    """A Cicode string: ``^`` escapes a quote and itself."""
    return '"%s"' % s.replace("^", "^^").replace('"', '^"')


def draft_from_form(form: GenieForm) -> Tuple[List[DraftItem], List[FormField]]:
    """The draft of a parsed form: its layout items (a field with its definition) and the
    definitions the layout does not place."""
    items: List[DraftItem] = []
    placed = set()
    for it in form.layout:
        if it.kind == "field":
            f = form.field_by_number(it.field)
            if f is None:
                continue
            placed.add(id(f))
            items.append(DraftItem("field", it.line, it.col, it.width, field=f))
        elif it.kind == "text":
            items.append(DraftItem("text", it.line, it.col, len(it.text), text=it.text))
        else:
            items.append(DraftItem("line", it.line, 0, it.width))
    extra = [f for f in form.fields if id(f) not in placed]
    return items, extra


def _reading_order(items: Sequence[DraftItem]) -> List[DraftItem]:
    return sorted(items, key=lambda it: (it.line, it.kind != "line", it.col))


def form_from_draft(
    name: str, title: str, items: Sequence[DraftItem], extra: Sequence[FormField] = ()
) -> GenieForm:
    """The FORM of a draft: the placed fields numbered 1..n in reading order (a definition
    placed twice keeps one number), then the definitions not placed; the layout items
    pointing at those numbers."""
    form = GenieForm(name=name, title=title)
    numbers: Dict[int, int] = {}
    layout: List[LayoutItem] = []
    for it in _reading_order(items):
        if it.kind == "field" and it.field is not None:
            k = numbers.get(id(it.field))
            if k is None:
                k = numbers[id(it.field)] = len(numbers) + 1
                f = it.field
                form.fields.append(
                    FormField(k, f.name, f.size, f.access, f.list_form, f.list_field, f.list_filter)
                )
            layout.append(LayoutItem("field", it.line, it.col, it.width, field=k))
        elif it.kind == "text":
            layout.append(LayoutItem("text", it.line, it.col, len(it.text) + 1, text=it.text))
        elif it.kind == "line":
            layout.append(LayoutItem("line", it.line, 0, it.width))
    for f in extra:
        if id(f) in numbers:
            continue
        numbers[id(f)] = k = len(numbers) + 1
        form.fields.append(FormField(k, f.name, f.size, f.access, f.list_form, f.list_field, f.list_filter))
    form.layout = layout
    return form


def layout_text(layout: Sequence[LayoutItem], newline: str = "\r\n") -> str:
    """The NORMAL string of layout items (as :func:`parse_layout` reads it back): each line's
    rules first (a rule takes no columns), then its prompts and fields at their columns, blanks
    between them. FormError when two items of a line overlap, or a field is too narrow for its
    number and braces."""
    lines: Dict[int, List[LayoutItem]] = {}
    for it in layout:
        lines.setdefault(it.line, []).append(it)
    if not lines:
        return ""
    out = []
    for ln in range(1, max(lines) + 1):
        row = lines.get(ln, [])
        rules = [it for it in row if it.kind == "line"]
        rest = sorted((it for it in row if it.kind != "line"), key=lambda it: it.col)
        s = "".join("{LINE, %d}" % it.width for it in rules)
        col = 1
        for it in rest:
            if it.col < col:
                raise FormError("line %d: two items overlap at column %d" % (ln, it.col))
            s += " " * (it.col - col)
            if it.kind == "field":
                head = "{%d" % it.field
                if it.width < len(head) + 1:
                    raise FormError(
                        "line %d: field %d is too narrow (at least %d columns)"
                        % (ln, it.field, len(head) + 1)
                    )
                piece = head + " " * (it.width - len(head) - 1) + "}"
            else:
                if "{" in it.text or "\n" in it.text or "\r" in it.text:
                    raise FormError("line %d: a prompt may not hold { or a line break" % ln)
                piece = it.text
            s += piece
            col = it.col + len(piece)
        out.append(s)
    return newline.join(out)


def format_form(form: GenieForm, newline: str = "\r\n") -> str:
    """The FORM block of ``form`` as a .frm file holds it."""
    head = "FORM %s" % _quote(form.name)
    if form.title:
        head += ", %s" % _quote(form.title)
    lines = [head + ";", ""]
    for f in form.fields:
        s = "%d: %s, %d, %s" % (f.number, _quote(f.name), f.size, f.access or "readwrite")
        if f.list_form:
            s += ", %s, %s" % (_quote(f.list_form), _quote(f.list_field))
            if f.list_filter:
                s += ", %s" % _quote(f.list_filter)
        lines.append(s + ";")
    lines += ["", "NORMAL", _quote(layout_text(form.layout, newline))]
    return newline.join(lines)


def check_form(form: GenieForm) -> str:
    """Why the Graphics Builder would not use ``form`` once written ('' = it would): the
    block written and read back by the same rules."""
    if not form.name:
        return "the form needs the genie's name"
    if len(form.name) > LAYOUT_MAX:
        return "the genie's name is too long"
    try:
        text = format_form(form)
    except FormError as e:
        return str(e)
    if not form.layout:
        return "the form shows nothing: add a prompt or a field"
    for f in form.fields:
        if not f.name:
            return "field %d has no parameter" % f.number
        if len(f.name) > NAME_MAX:
            return "the parameter name %r is longer than %d characters" % (f.name, NAME_MAX)
        if f.size < 1:
            return "the field %s needs a length of 1 or more" % f.name
        for what, v, lim in (
            ("list form", f.list_form, NAME_MAX),
            ("list field", f.list_field, NAME_MAX),
            ("list filter", f.list_filter, FILTER_MAX),
        ):
            if len(v) > lim:
                return "the %s of %s is longer than %d characters" % (what, f.name, lim)
        if f.list_form and not f.list_field:
            return "the list of %s needs a field as well as a form" % f.name
    if len(layout_text(form.layout)) > LAYOUT_MAX:
        return "the layout is longer than %d characters" % LAYOUT_MAX
    back = lookup_form(parse_forms(text), form.name)
    if back is None:
        bad = [f for f in parse_forms(text) if f.error]
        return bad[0].error if bad else "the form does not read back"
    return ""


def form_spans(text: str) -> List[Tuple[str, int, int]]:
    """(genie name, start, end) of every FORM block of a .frm file's text: from its FORM
    keyword to the end of its last token before the next FORM (a comment after it belongs to
    what follows). A file whose tokens do not read (an unclosed string or comment) gives
    the blocks before that point."""
    try:
        toks = _lex(text)
    except FormError:
        return []
    heads = [
        k
        for k in range(len(toks) - 1)
        if toks[k].kind == "kw" and toks[k].value == "form" and toks[k + 1].kind == "str"
    ]
    out = []
    for n, k in enumerate(heads):
        stop = heads[n + 1] if n + 1 < len(heads) else len(toks) - 1
        last = toks[stop - 1]
        out.append((str(toks[k + 1].value), toks[k].start, last.end))
    return out


def replace_form(text: str, form: GenieForm, newline: str = "") -> str:
    """``text`` (a .frm file) with ``form``'s block in place of the first block for its genie;
    any other block for that genie goes (the Graphics Builder would take the last one that
    reads). Without one, the block is added at the end. Everything else stays as it was.
    ``newline`` = the file's line end (default: as the file has it, else CR LF)."""
    if not newline:
        newline = "\r\n" if "\r\n" in text or "\n" not in text else "\n"
    block = format_form(form, "\r\n")
    if newline != "\r\n":
        # (the layout string keeps its CR LF: a lone LF is a blank in it)
        head, sep, tail = block.rpartition("NORMAL\r\n")
        block = head.replace("\r\n", newline) + "NORMAL" + newline + tail
    spans = [(a, b) for name, a, b in form_spans(text) if name.lower() == form.name.lower()]
    if not spans:
        sep = "" if not text else (newline if text.endswith(("\n", "\r")) else newline * 2)
        if text and not text.endswith((newline + newline, "\n\n")):
            sep = newline if text.endswith(("\n", "\r")) else newline * 2
        return text + sep + block + newline
    out, at = [], 0
    for k, (a, b) in enumerate(spans):
        out.append(text[at:a])
        if k == 0:
            out.append(block)
        else:
            # (the removed block's line end goes with it)
            while b < len(text) and text[b] in " \t":
                b += 1
            if text.startswith("\r\n", b):
                b += 2
            elif b < len(text) and text[b] in "\r\n":
                b += 1
        at = b
    out.append(text[at:])
    return "".join(out)


def form_file_for(lib_path: str) -> str:
    """The .frm file of the library at ``lib_path``: ``<library>.frm`` in its folder (the
    one there, whatever its case), else that name to create."""
    folder = os.path.dirname(os.path.abspath(lib_path))
    name = os.path.splitext(os.path.basename(lib_path))[0] + ".frm"
    return _find_file(folder, name) or os.path.join(folder, name)


def default_form(name: str, params: Sequence[str], title: str = "") -> GenieForm:
    """A first form for a genie: a prompt and a field per parameter, one a line (as
    CtDraw's default form lists them)."""
    pad = max([len(p) for p in params] + [4]) + 2
    items: List[DraftItem] = []
    for k, p in enumerate(params, 1):
        items.append(DraftItem("text", k, 1, len(p), text=p))
        items.append(DraftItem("field", k, pad + 1, 32, field=FormField(k, p, 254)))
    return form_from_draft(name, title, items)


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("genieform.files", _FILE_CACHE, on=("refresh", "tests"))
