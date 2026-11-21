"""Which characters make a Cicode name, matching the compiler."""

from __future__ import annotations

import re

from .format.textcodec import CODEC

#: the code page bytes that are letters (Windows-1252), CicodeSim Lexer's 'high' table
_HIGH_LETTERS = (
    [0x83, 0x8A, 0x8C, 0x8E, 0x9A, 0x9C, 0x9E, 0x9F, 0xAA, 0xB5, 0xBA]
    + list(range(0xC0, 0xD7))
    + list(range(0xD8, 0xF7))
    + list(range(0xF8, 0x100))
)
#: the superscript digits a name may contain after its first character (2, 3, 1)
_HIGH_DIGITS = [0xB2, 0xB3, 0xB9]


def _decode(bs):
    return "".join(bytes([b]).decode(CODEC) for b in bs)


_START_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_" + _decode(_HIGH_LETTERS)
_PART_CHARS = _START_CHARS + "0123456789" + _decode(_HIGH_DIGITS) + "\\"
_START_SET = frozenset(_START_CHARS)
_PART_SET = frozenset(_PART_CHARS)

#: the regex-escaped characters (to put inside a [...] class with others)
START_CHARS = re.escape(_START_CHARS)
PART_CHARS = re.escape(_PART_CHARS)
#: regex character classes
START = "[" + START_CHARS + "]"
PART = "[" + PART_CHARS + "]"


def is_start(ch: str) -> bool:
    return ch in _START_SET


def is_part(ch: str) -> bool:
    return ch in _PART_SET
