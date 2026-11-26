"""C's atoi and strtoul: how CtDraw reads a number out of a property value or a form file;
and the digit test that goes with them."""

from __future__ import annotations

import re

_ATOI_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?)0*([0-9]{1,11})")
_STRTOUL_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?)(0[xX][0-9A-Fa-f]+|0[0-7]*|[1-9][0-9]*)")


def is_digits(text: str) -> bool:
    """``text`` is one or more ASCII digits, the digits int() and the C readers take
    (``str.isdigit`` also takes superscripts such as '²', which int() refuses)."""
    return text.isascii() and text.isdigit()


def _text(value) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def c_atoi(value) -> int:
    """C atoi: blanks, a sign, the digits 0-9; 0 without digits, LONG_MIN / LONG_MAX past
    the 32-bit range (whatever the length of the digit run)."""
    m = _ATOI_RE.match(_text(value))
    if m is None:
        return 0
    v = int(m.group(2))  # (11 digits at most: past the range either way)
    v = -v if m.group(1) == "-" else v
    return max(-0x80000000, min(0x7FFFFFFF, v))


def c_strtoul(value, default: int | None = 0) -> int | None:
    """C strtoul(s, NULL, 0): blanks, a sign, 0x.. hex, 0.. octal or decimal; ``default``
    without a number, ULONG_MAX past the 32-bit range, a negated value modulo 2**32."""
    m = _STRTOUL_RE.match(_text(value))
    if m is None:
        return default
    t = m.group(2)
    if t[:2] in ("0x", "0X"):
        base, digits = 16, t[2:].lstrip("0")
    elif t[0] == "0":
        base, digits = 8, t.lstrip("0")
    else:
        base, digits = 10, t
    # (a run longer than any 32-bit value is past the range: no int() of it)
    v = int(digits or "0", base) if len(digits) <= 11 else 1 << 32
    if v > 0xFFFFFFFF:
        return 0xFFFFFFFF
    return -v & 0xFFFFFFFF if m.group(1) == "-" else v
