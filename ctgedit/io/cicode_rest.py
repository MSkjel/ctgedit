"""Evaluate Cicode visibility expressions at rest state."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Optional, Tuple, Union

from .. import cicode_names as _cicode_names


class _Unknown:
    """Poison value: a quantity with no rest default (substitution-derived).
    Any operation touching it stays unknown; the verdict becomes None."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<unknown>"


UNKNOWN = _Unknown()

Value = Union[int, float, str, _Unknown]


def _call_rest_default(name: str):
    """CtDraw's editor has no Cicode interpreter, so unknown calls get a default."""
    return UNKNOWN


_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<str>"(?:[^"\\]|\\.)*")
      | (?P<num>\d+\.\d*|\.\d+|\d+)
      | (?P<id>[%s\\.][%s\\.]*)
      | (?P<op><>|<=|>=|[()=<>+\-*/,])
    )"""
    % (_cicode_names.START_CHARS, _cicode_names.PART_CHARS),
    re.VERBOSE,
)
# (names by the compiler's rule: ctgedit.cicode_names)

_KEYWORDS = {"not", "and", "or", "xor", "true", "false"}


def _tokenize(expr: str) -> Optional[List[Tuple[str, str]]]:
    out: List[Tuple[str, str]] = []
    pos = 0
    n = len(expr)
    while pos < n:
        m = _TOKEN_RE.match(expr, pos)
        if m is None:
            # trailing whitespace ok; anything else is unparseable
            return None if expr[pos:].strip() else out
        pos = m.end()
        if m.lastgroup == "str":
            out.append(("str", m.group("str")[1:-1]))
        elif m.lastgroup == "num":
            out.append(("num", m.group("num")))
        elif m.lastgroup == "id":
            word = m.group("id")
            if word.lower() in _KEYWORDS:
                out.append(("kw", word.lower()))
            else:
                out.append(("id", word))
        else:
            out.append(("op", m.group("op")))
    return out


def _truthy(v: Value) -> bool:
    if isinstance(v, str):
        return bool(v)
    return v != 0


class _Parser:
    """Recursive-descent over the token list; raises ValueError on surprises."""

    def __init__(self, tokens: List[Tuple[str, str]]):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def expect_op(self, op: str) -> None:
        kind, val = self.take()
        if kind != "op" or val != op:
            raise ValueError("expected %r" % op)

    # precedence: or < and < not < comparison < additive < multiplicative < unary
    def parse(self) -> Value:
        v = self.parse_or()
        if self.peek() != (None, None):
            raise ValueError("trailing tokens")
        return v

    def parse_or(self) -> Value:
        v = self.parse_and()
        while self.peek() == ("kw", "or") or self.peek() == ("kw", "xor"):
            _, op = self.take()
            r = self.parse_and()
            if isinstance(v, _Unknown) or isinstance(r, _Unknown):
                v = UNKNOWN
            elif op == "or":
                v = 1 if (_truthy(v) or _truthy(r)) else 0
            else:
                v = 1 if (_truthy(v) != _truthy(r)) else 0
        return v

    def parse_and(self) -> Value:
        v = self.parse_not()
        while self.peek() == ("kw", "and"):
            self.take()
            r = self.parse_not()
            if isinstance(v, _Unknown) or isinstance(r, _Unknown):
                # False AND unknown is still False
                if (not isinstance(v, _Unknown) and not _truthy(v)) or (
                    not isinstance(r, _Unknown) and not _truthy(r)
                ):
                    v = 0
                else:
                    v = UNKNOWN
            else:
                v = 1 if (_truthy(v) and _truthy(r)) else 0
        return v

    def parse_not(self) -> Value:
        if self.peek() == ("kw", "not"):
            self.take()
            r = self.parse_not()
            if isinstance(r, _Unknown):
                return UNKNOWN
            return 0 if _truthy(r) else 1
        return self.parse_cmp()

    def parse_cmp(self) -> Value:
        v = self.parse_add()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("=", "<>", "<", ">", "<=", ">="):
                self.take()
                r = self.parse_add()
                if isinstance(v, _Unknown) or isinstance(r, _Unknown):
                    v = UNKNOWN
                else:
                    v = self._compare(val, v, r)
            else:
                return v

    @staticmethod
    def _compare(op: str, a: Value, b: Value) -> int:
        mixed = isinstance(a, str) != isinstance(b, str)
        if mixed:
            # defaulted number vs string literal: never equal, no ordering
            return 1 if op == "<>" else 0
        try:
            res = {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        except TypeError:
            raise ValueError("bad comparison")
        return 1 if res else 0

    def parse_add(self) -> Value:
        v = self.parse_mul()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("+", "-"):
                self.take()
                r = self.parse_mul()
                if isinstance(v, _Unknown) or isinstance(r, _Unknown):
                    v = UNKNOWN
                elif val == "+" and (isinstance(v, str) or isinstance(r, str)):
                    v = "%s%s" % (
                        v if isinstance(v, str) else _numstr(v),
                        r if isinstance(r, str) else _numstr(r),
                    )
                elif isinstance(v, str) or isinstance(r, str):
                    raise ValueError("string arithmetic")
                else:
                    v = v + r if val == "+" else v - r
            else:
                return v

    def parse_mul(self) -> Value:
        v = self.parse_unary()
        while True:
            kind, val = self.peek()
            if kind == "op" and val in ("*", "/"):
                self.take()
                r = self.parse_unary()
                if isinstance(v, _Unknown) or isinstance(r, _Unknown):
                    v = UNKNOWN
                elif isinstance(v, str) or isinstance(r, str):
                    raise ValueError("string arithmetic")
                else:
                    v = v * r if val == "*" else (v / r if r else 0)
            else:
                return v

    def parse_unary(self) -> Value:
        kind, val = self.peek()
        if kind == "op" and val == "-":
            self.take()
            r = self.parse_unary()
            if isinstance(r, _Unknown):
                return UNKNOWN
            if isinstance(r, str):
                raise ValueError("negated string")
            return -r
        return self.parse_primary()

    def parse_primary(self) -> Value:
        kind, val = self.take()
        if kind == "num":
            return float(val) if "." in val else int(val)
        if kind == "str":
            return val
        if kind == "kw" and val in ("true", "false"):
            return 1 if val == "true" else 0
        if kind == "id":
            # function call: args parsed and discarded to keep the stream in sync.
            if self.peek() == ("op", "("):
                self.take()
                if self.peek() != ("op", ")"):
                    self.parse_or()
                    while self.peek() == ("op", ","):
                        self.take()
                        self.parse_or()
                self.expect_op(")")
                return _call_rest_default(val)
            # bare identifier: a Cicode var (SA_DisableLabel) or __genie macro;
            # no assumable runtime default -> UNKNOWN, verdict None, object draws
            # (CtDraw shows the OV07 label gated "SA_DisableLabel = 1").
            return UNKNOWN
        if kind == "op" and val == "(":
            v = self.parse_or()
            self.expect_op(")")
            return v
        raise ValueError("unexpected token %r" % (val,))


def _numstr(v: Value) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@lru_cache(maxsize=4096)
def eval_at_rest(expr: str) -> Optional[Value]:
    """Evaluate ``expr`` in the no-runtime rest context.

    ``None`` when unparseable or the value depends on a substitution-derived
    quantity (UNKNOWN poisoned the result)."""
    if not expr or len(expr) > 4096:
        return None
    toks = _tokenize(expr)
    if not toks:
        return None
    try:
        v = _Parser(toks).parse()
    except ValueError, IndexError, RecursionError:
        return None
    return None if isinstance(v, _Unknown) else v


def visible_at_rest(expr: str) -> Optional[bool]:
    """Is an object with this VISIBLE expression shown at rest?"""
    v = eval_at_rest(expr.strip()) if expr else None
    if v is None:
        return None
    return not _truthy(v)


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("cicode_rest.eval", eval_at_rest, on=())
