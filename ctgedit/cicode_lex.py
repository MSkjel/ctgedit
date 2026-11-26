"""Cicode tokens for the expression and command fields: what each piece of a field's text
is (keyword, string, number, function call, name, genie parameter ...), the call the
cursor is in, and a function signature's arguments. The rules follow the Cicode grammar
of the Cicode extension for VS Code; the names follow the compiler (cicode_names)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Tuple

from . import cicode_names as _cn
from .format.supergenie import TYPE_WORDS

# token kinds
COMMENT = "comment"
STRING = "string"
NUMBER = "number"
KEYWORD = "keyword"  # IF THEN ELSE ... END
OPERATOR_WORD = "operator_word"  # AND OR NOT MOD BITAND BITOR BITXOR
TYPE = "type"  # INT REAL STRING ... and GLOBAL MODULE PRIVATE PUBLIC FUNCTION
LABEL = "label"  # TRUE FALSE (the Include project's labels)
FUNCTION = "function"  # the name of a call
NAME = "name"  # a tag, local variable or Equipment.Item reference
FIELD = "field"  # '.Field' after a parameter or an index ('%Tag%.Value')
PARAM = "param"  # a genie parameter: %name% or %name=default%
ASSOCIATION = "association"  # a super genie association: ?name? or ?INT 2?
FORMAT = "format"  # a display format after ':' ('###.#')
RANGE = "range"  # the '^' of a stored range: Tag^(0,100)
VBA = "vba"  # the script of a 'CiVBA' command (not Cicode)
OPERATOR = "operator"
PUNCT = "punct"  # , ; ( ) [ ] . :
INVALID = "invalid"


class Token(NamedTuple):
    start: int
    end: int
    kind: str
    #: why an INVALID token (or an open string or comment) is wrong ('' otherwise)
    problem: str = ""


_WORDS = {
    **{
        w: KEYWORD
        for w in ("if", "then", "else", "while", "for", "to", "do", "select", "case", "is", "return", "end")
    },
    **{w: OPERATOR_WORD for w in ("and", "or", "not", "mod", "bitand", "bitor", "bitxor")},
    **{
        w: TYPE
        for w in (
            "global",
            "module",
            "private",
            "public",
            "function",
            "int",
            "real",
            "string",
            "object",
            "quality",
            "timestamp",
            "long",
        )
    },
    **{w: LABEL for w in ("true", "false")},
}
#: a command that starts with CiVBA is VBA script
_CIVBA = re.compile(r"\s*(civba)(?![%s])" % _cn.PART_CHARS, re.IGNORECASE)
#: reserved words the compiler refuses in a field
_RESERVED = frozenset(("nop", "var", "cicode", "civba"))

_NAME = re.compile("%s%s*" % (_cn.START, _cn.PART))
_SPACE = re.compile(r"\s*")
_TOO_BIG_RADIX = "The number is larger than 32 bits."
_TOO_BIG_DECIMAL = "The number is larger than 4294967295."
_RADIX = {"b": 2, "B": 2, "o": 8, "O": 8, "x": 16, "X": 16}
_HEX_DIGIT = {c: int(c, 16) for c in "0123456789abcdefABCDEF"}


def _digit(c: str) -> bool:
    """An ASCII digit, as the compiler reads one (``str.isdigit`` also takes superscripts)."""
    return "0" <= c <= "9" and len(c) == 1


def _radix_digit(c: str, radix: int) -> int:
    d = _HEX_DIGIT.get(c, -1)
    return d if d < radix else -1


def _scan_number(text: str, i: int) -> Tuple[int, str]:
    """The number token at ``text[i]`` (an ASCII digit) as CtCmp32 cuts it: (end, problem).
    A value past 32 bits is E2058: a hex number stops before the digit that passes 32 bits,
    a binary, octal or decimal one after it ('{0x10000000}0', '{0o40000000000}',
    '{9999999999}9'). A radix letter after other digits ('12b') is no radix number; the token
    is then the digits of that radix after the first one. The engine's Lexer.ScanNumber."""
    n = len(text)
    p = i
    while p < n and text[p] == "0":
        p += 1
    first = p
    while p < n and _digit(text[p]):
        p += 1
    c = text[p] if p < n else ""
    if c in (".", "e", "E"):
        if c == ".":
            p += 1
            while p < n and _digit(text[p]):
                p += 1
        if p < n and text[p] in ("e", "E"):
            p += 1
            if p < n and text[p] in ("+", "-"):
                p += 1
            while p < n and _digit(text[p]):
                p += 1
        return p, ""
    radix = _RADIX.get(c, 0)
    if radix:
        q = first + 1
        if p == first:
            v = 0
            while q < n and _radix_digit(text[q], radix) >= 0:
                v = v * radix + _radix_digit(text[q], radix)
                if v > 0xFFFFFFFF:
                    return (q if radix == 16 else q + 1), _TOO_BIG_RADIX
                q += 1
            return q, ""
        while q < n and _radix_digit(text[q], radix) >= 0:
            q += 1
        return q, ""
    v = 0
    for q in range(first, p):
        v = v * 10 + ord(text[q]) - 48
        if v > 0xFFFFFFFF:
            return q + 1, _TOO_BIG_DECIMAL
    return p, ""


_PICTURE = re.compile(r"#[#0\-]*(?:!|(?:[.s]#*)?(?:[ \t]*(?:eu|engunit)\b)?)", re.IGNORECASE)
_FORMAT_SPEC = re.compile(
    _PICTURE.pattern
    + r"|~|(?:-[ \t]*)?(?:0+[xX][0-9A-Fa-f]*|0+[bB][01]*|0+[oO][0-7]*|[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]*)?)",
    re.IGNORECASE,
)
_ASSOCIATION = re.compile(r"\?\s*(?:(?:%s)\s+)?%s+\s*\?" % ("|".join(TYPE_WORDS), _cn.PART), re.IGNORECASE)
_BAD_OPERATOR = re.compile(r"==|=<|=>|><")
_OPERATOR = re.compile(r"<>|<=|>=|[<>=+\-*/]")


def _param_end(text: str, i: int, in_string: bool = False) -> int:
    """The end of the genie parameter token at text[i] == '%' (a '^%' inside it is a
    literal '%'), or -1 when it is not one. Its default may hold quotes
    ('%WebIP="127.0.0.1"%'); in a string, a quote ends the string instead."""
    stops = '\r\n"' if in_string else "\r\n"
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == "^" and j + 1 < len(text) and text[j + 1] == "%":
            j += 2
            continue
        if c == "%":
            return j + 1 if j > i + 1 else -1
        if c in stops:
            return -1
        j += 1
    return -1


def param_parts(token_text: str) -> Tuple[str, Optional[str]]:
    """(name, default or None) of a genie parameter token ('%Line3=" Hello"%' ->
    ('Line3', '" Hello"')): as CtDraw reads it, leading '='s skipped ('%=Q%' is Q), the
    default the text after the next '=', quotes and all."""
    name, _eq, default = token_text[1:-1].replace("^%", "%").lstrip("=").partition("=")
    return name.strip(), (default or None)


def tokenize(text: str) -> List[Token]:
    """The tokens of ``text`` (whitespace between them left out), in order. A string or a
    block comment that is not closed runs to the end with its problem; a genie parameter
    inside a string is a token of its own between the string's parts."""
    out: List[Token] = []
    n = len(text)
    i = 0
    m = _CIVBA.match(text)
    if m:
        out.append(Token(m.start(1), m.end(1), KEYWORD))
        if m.end(1) < n:
            out.append(Token(m.end(1), n, VBA))
        return out
    after_colon = False  # the next token may be a display format
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        # comments
        if c == "!" or text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(Token(i, j, COMMENT))
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                out.append(Token(i, n, COMMENT, "The comment is not closed: */ is missing."))
                i = n
            else:
                out.append(Token(i, j + 2, COMMENT))
                i = j + 2
            continue
        if after_colon:
            after_colon = False
            m = _FORMAT_SPEC.match(text, i)
            if m and m.end() > i:
                # a number spec is a number token: past 32 bits it is E2058 too
                k = len(m.group(0)) - len(m.group(0).lstrip("- 	")) + i
                j, problem = _scan_number(text, k) if _digit(text[k : k + 1]) else (0, "")
                if problem:
                    out.append(Token(i, j, INVALID, problem))
                    i = j
                else:
                    out.append(Token(i, m.end(), FORMAT))
                    i = m.end()
                continue
        # strings, with the genie parameters inside them
        if c == '"':
            i = _string(text, i, out)
            continue
        if c == "%":
            j = _param_end(text, i)
            if j > 0:
                out.append(Token(i, j, PARAM))
                i = j
                continue
        if c == "?":
            m = _ASSOCIATION.match(text, i)
            if m:
                out.append(Token(i, m.end(), ASSOCIATION))
                i = m.end()
                continue
        # '.Field' after a parameter, an association or an index
        if c == "." and (
            (out and out[-1].end == i and out[-1].kind in (PARAM, ASSOCIATION, FIELD))
            or text[i - 1 : i] == "]"
        ):
            m = _NAME.match(text, i + 1)
            if m:
                out.append(Token(i, m.end(), FIELD))
                i = m.end()
                continue
        m = _NAME.match(text, i)
        if m:
            j = m.end()
            word = text[i:j].lower()
            kind = _WORDS.get(word)
            if kind is None and word in _RESERVED:
                out.append(Token(i, j, INVALID, "'%s' is reserved in Cicode." % text[i:j]))
                i = j
                continue
            if kind is None or kind == LABEL:
                # a dotted reference: Tag.Field, Equipment.Item, Area.Equipment.Item
                while j + 1 < n and text[j] == "." and _cn.is_start(text[j + 1]):
                    j = _NAME.match(text, j + 1).end()
                if j > m.end():
                    kind = None  # 'True.Value' is a name
                rest = _SPACE.match(text, j).end()
                if kind is None:
                    kind = FUNCTION if rest < n and text[rest] == "(" and j == m.end() else NAME
            out.append(Token(i, j, kind))
            i = j
            continue
        if _digit(c):
            j, problem = _scan_number(text, i)
            out.append(Token(i, j, INVALID if problem else NUMBER, problem))
            i = j
            continue
        m = _BAD_OPERATOR.match(text, i)
        if m:
            out.append(Token(i, m.end(), INVALID, "Cicode has no operator %s." % m.group(0)))
            i = m.end()
            continue
        m = _OPERATOR.match(text, i)
        if m:
            out.append(Token(i, m.end(), OPERATOR))
            i = m.end()
            continue
        if c == ":":
            out.append(Token(i, i + 1, PUNCT))
            after_colon = True
            i += 1
            continue
        if c in ",;()[].":
            out.append(Token(i, i + 1, PUNCT))
            i += 1
            continue
        if c == "^" and text[_SPACE.match(text, i + 1).end() : _SPACE.match(text, i + 1).end() + 1] == "(":
            out.append(Token(i, i + 1, RANGE))
            i += 1
            continue
        if c == "#":
            m = _PICTURE.match(text, i)
            out.append(Token(i, m.end(), INVALID, "A display format needs a ':' before it."))
            i = m.end()
            continue
        out.append(Token(i, i + 1, INVALID, "Cicode does not use the character %s." % c))
        i += 1
    return out


def _string(text: str, i: int, out: List[Token]) -> int:
    """The string at text[i] == '"' into ``out``; returns its end."""
    n = len(text)
    j = i + 1
    part = i
    first = len(out)
    while j < n:
        c = text[j]
        if c == "^" and j + 1 < n:
            j += 2
            continue
        if c == '"':
            out.append(Token(part, j + 1, STRING))
            return j + 1
        if c == "\n":
            break
        if c == "%":
            e = _param_end(text, j, in_string=True)
            if e > 0:
                if j > part:
                    out.append(Token(part, j, STRING))
                out.append(Token(j, e, PARAM))
                part = j = e
                continue
        j += 1
    problem = 'The string is not closed: a " is missing.'
    if j > part:
        out.append(Token(part, j, STRING, problem))
    else:
        # it ends with a parameter: the problem goes on the piece with the opening quote
        out[first] = out[first]._replace(problem=problem)
    return j


def token_at(tokens: List[Token], pos: int) -> Optional[Token]:
    """The token under character ``pos`` (else the one ending at ``pos``)."""
    hit = None
    for t in tokens:
        if t.start <= pos < t.end:
            return t
        if t.end == pos:
            hit = t
        if t.start > pos:
            break
    return hit


@dataclass(frozen=True)
class Call:
    """The call the cursor is in: its function's name, where the name and its '(' are,
    and which argument (0 the first) the cursor is in."""

    name: str
    name_start: int
    open_paren: int
    argument: int


def call_at(text: str, pos: int, tokens: Optional[List[Token]] = None) -> Optional[Call]:
    """The innermost call whose brackets hold ``pos`` (brackets that group, not call, pass
    to the call around them); None outside every call."""
    tokens = tokenize(text) if tokens is None else tokens
    stack: List[list] = []  # [name token or None, '(' position, argument]
    prev = None
    for t in tokens:
        if t.start >= pos:
            break
        if t.kind in (COMMENT, STRING) and t.end > pos:
            return None  # the cursor is inside a comment or a string: no hint
        if t.kind == PUNCT:
            ch = text[t.start]
            if ch == "(":
                stack.append([prev if prev is not None and prev.kind == FUNCTION else None, t.start, 0])
            elif ch == ")" and stack:
                stack.pop()
            elif ch == "," and stack:
                stack[-1][2] += 1
        prev = t
    for name_tok, paren, arg in reversed(stack):
        if name_tok is not None:
            return Call(text[name_tok.start : name_tok.end], name_tok.start, paren, arg)
    return None


class Argument(NamedTuple):
    text: str
    optional: bool


def split_signature(signature: str) -> Optional[Tuple[str, List[Argument], str]]:
    """'INT AlarmAck(INT Mode, INT Value [, STRING ClusterName])' -> ('INT AlarmAck(',
    [INT Mode, INT Value, STRING ClusterName (optional)], ')'). An argument in square
    brackets or with a default ('= 0') is optional. None when the text has no brackets."""
    a = signature.find("(")
    b = signature.rfind(")")
    if a < 0 or b < a:
        return None
    inner = signature[a + 1 : b]
    args: List[Argument] = []
    depth = 0
    part: List[str] = []
    part_depth = 0
    in_string = False

    def flush():
        t = " ".join("".join(part).split())
        if t:
            args.append(Argument(t, part_depth > 0 or "=" in t))

    for ch in inner:
        if ch == '"':
            in_string = not in_string
        if not in_string and ch == "[":
            depth += 1
            if not "".join(part).strip():
                part_depth = depth
            continue
        if not in_string and ch == "]":
            depth = max(0, depth - 1)
            continue
        if not in_string and ch == ",":
            flush()
            part = []
            part_depth = depth
            continue
        part.append(ch)
    flush()
    return signature[: a + 1], args, signature[b:]


# ------------------------------------------------------------------ genie templates
#: the kinds whose text is no name of the project
_TEMPLATE = (PARAM, ASSOCIATION)


def beside_template(tokens: List[Token], k: int) -> bool:
    """Token ``k`` touches a genie parameter or super genie association: it is a part of
    a name the genie makes ('%Tag%_PV')."""
    t = tokens[k]
    before = tokens[k - 1] if k else None
    after = tokens[k + 1] if k + 1 < len(tokens) else None
    return (before is not None and before.end == t.start and before.kind in _TEMPLATE) or (
        after is not None and after.start == t.end and after.kind in (*_TEMPLATE, FIELD)
    )


#: what fill_template puts in place of a genie parameter or super genie association
PLACEHOLDER = "Gp"


def fill_template(text: str, tokens: Optional[List[Token]] = None) -> Tuple[str, List[Tuple[int, int]]]:
    """``text`` with each genie parameter and super genie association replaced by a name of
    the same length ('%Tag%_PV' -> 'Gp___PV'), so that the compiler can read it, and the
    span of each name the replacements make (a name the genie makes: what the compiler
    says about it is not known until the genie is placed)."""
    tokens = tokenize(text) if tokens is None else tokens
    chars = list(text)
    marks = []
    for t in tokens:
        if t.kind in _TEMPLATE:
            n = t.end - t.start
            chars[t.start : t.end] = list((PLACEHOLDER + "_" * n)[:n])
            marks.append(t.start)
    filled = "".join(chars)
    spans: List[Tuple[int, int]] = []
    for i in marks:
        s = i
        while s > 0 and _cn.is_part(filled[s - 1]):
            s -= 1
        e = i
        while e < len(filled) and _cn.is_part(filled[e]):
            e += 1
        if not spans or spans[-1] != (s, e):
            spans.append((s, e))
    return filled, spans
