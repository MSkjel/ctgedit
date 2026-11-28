"""Parser for the ``page`` block of a .ctg file."""

from __future__ import annotations

import os
import re
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .cnum import is_digits

_intern = sys.intern

from .container import Block, Container
from . import upgrade
from .attributes import (
    AttributesPage,
    colorref_to_rgb,
    parse_attributes,
)
from .textcodec import CODEC, MULTIBYTE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_HEAD_SIZE = 0x34  # fixed head when container.word2 >= 0x0c
PAGE_HEAD_SIZE_LEGACY = 0x24  # the head of an old form (word2 < 0x0C): the first 0x24
#                               bytes of the current one, no GUID (format/upgrade.py)
LINK_ENTRY_SIZE = 0x60  # 96 bytes per link/import-table entry
RECORD_HEADER_SIZE = 8  # u32 payload_size + u32 next_link
TEXT_TYPE_CODE = 0x05  # Text (format/codes.py TC names every type code)

# Known animation/interaction property keys (+ array stems). Classify a bag as a
# real property bag vs. a genie/template substitution table; every bag is
# preserved regardless.
_KNOWN_PROPERTY_KEYS = frozenset(
    {
        # DisplayValue / Text / Number
        "TXTMODE",
        "TXT_OFF",
        "TXT_ON",
        "TXT_STR",
        "TXT_A",
        "TXT_B",
        "TXT_C",
        "TXT_D",
        "TXT_E",
        "FORMAT",
        "ENG_ZERO",
        "ENG_FULL",
        "ENG_UNITS",
        "RAW_ZERO",
        "RAW_FULL",
        "DIGITAL",
        # FillColour
        "COLMODE",
        "COL_MAXON",
        "!COL_STATIC",
        "COL_A",
        "COL_B",
        "COL_C",
        "COL_D",
        "COL_E",
        "COL_EXP",
        "COL_ARR",
        # FillLevel
        "FILMODE",
        "FILMINPC",
        "FILMAXPC",
        "FILCOL",
        # Transformation
        "MVXMINPT",
        "MVXMAXPT",
        "MVXSLMODE",
        "MVYMINPT",
        "MVYMAXPT",
        "MVYSLMODE",
        "MVX",
        "MVY",
        "ROTMINPC",
        "ROTMAXPC",
        "ROTMODE",
        "ROTSLMODE",
        "ROTXORG",
        "ROTYORG",
        "SZXMINPC",
        "SZXMAXPC",
        "SZXMODE",
        "SZXORG",
        "SZX",
        "SZYMINPC",
        "SZYMAXPC",
        "SZYMODE",
        "SZYORG",
        "SZY",
        # SymbolSet
        "SYMBOL",
        "SET_ARR",
        # InputKeyboard
        "KEY_SEQ",
        "KEY_LBUTTON",
        "CMD",
        # InputTouch
        "ACTION",
        "RPT_RATE",
        "DOWN_CMD",
        "REPEAT_CMD",
        "MSG_LOG",
        "MSG_LOGDN",
        "MSG_LOGRPT",
        # AccessDisable
        "DISABLED",
        "DISMODE",
        "GREY_STYLE",
        "Disable",
        # Visibility
        "VIS_MODE",
        "VISIBLE",
        # misc/geometry
        "WIDTH",
        "HEIGHT",
        "TARGET",
        "COMMENT",
        "AN",
        # attributes-style page specials seen on objects too
        "!*PropSheet.Focus",
        # ---- Genie / widget control keys (grid / table / tree / tab / calendar) ----
        # Data-driven bag keys from the library widget genies (LibTab / LibTree /
        # Table / Calendar), known so they surface via .properties() instead of
        # being dropped as "unknown".
        "Name",
        "ID",
        "Table",
        "Calendar",
        "Command",
        "InitFn",
        "SelectFn",
        "Text",
        "ToolTip",
        "HiddenWhen",
        "Width",
        "Height",
        "Rows",
        "Col",
        "Cols",
        "RowHeight",
        "HdrHeight",
        "ColPadding",
        "HdrPadding",
        "TextPadding",
        "IconPadding",
        "ColWidthMin",
        "ColSepMode",
        "ColSepAllRow",
        "ColSelectMode",
        "ColSelIcon",
        "AllowSel",
        "AllowDragSort",
        "AllowDragDel",
        "AllowDblClick",
        "AutoWidth",
        "AutoHide",
        "ScanDelay",
        "RefreshRate",
        "Weekday",
        "WeekNum",
        "FontHdr",
        "FontSel",
        "FontData",
        "FontLabel",
        "ItemHeight",
        "Items",
        "ExpandLevel",
        "IsDragUpdate",
        "UseWinInst",
        "ShowChkBox",
        "ForceChild",
        "SymHideIcons",
        "SymUnchecked",
        "SymChecked",
        "SymCheckedDis",
        "SymExpand",
        "SymCollapse",
        "SymLib",
        # widget callbacks / cell layout / equipment-browser genie keys
        "Row",
        "RowAN",
        "RowPadding",
        "ColWidthMax",
        "Hidden",
        "Selected",
        "Type",
        "Item",
        "ItemIconCount",
        "Element",
        "Month",
        "Menu",
        "MenuName",
        "NodeSym",
        "Tag",
        "TagPrefix",
        "Equip",
        "Equipment",
        "EquipSymLib",
        "FontCell",
        "ScrollBar",
        "SBarHorz",
        "SBarVert",
        "IconPading",
        "Privilege",
        "ReloadFn",
        "RClickFn",
        "LClickFn",
        "DClickFn",
        "ScalingFn",
        "CallbackFn",
        "DisableExpression",
        "ObjectID",
        "ShowCluster",
        "AlarmType",
        "MaxRows",
        "argTagName",
        "index",
        "monitor",
        "!*Input.Touch",
        "!*Input.Keyboard Commands",
    }
)

# array-key stems: a key like COL_STA_3 / METADATA_1 / SYMBOL_2 is "known"
_KNOWN_ARRAY_STEMS = (
    "COL_STA_",
    "COL_ARR_",
    "COL_THR_",
    "METADATA_",
    "STATE_",
    "SYMBOL_",
    "VERTEX_VERTFROMX",
    "VERTEX_VERTFROMY",
    "VERTEX_VERTTOX",
    "VERTEX_VERTTOY",
    "VERTEX_VERTEXP",
    "TXT_",
    "COL_",
)

_MODE_DIGIT_KEYS = frozenset(
    {
        "TXTMODE",
        "COLMODE",
        "FILMODE",
        "ROTMODE",
        "ROTSLMODE",
        "SZXMODE",
        "SZYMODE",
        "MVXSLMODE",
        "MVYSLMODE",
    }
)


# ---------------------------------------------------------------------------
# Primitive readers (the leaf fields CtDraw reads)
# ---------------------------------------------------------------------------


def _ascii_compatible_codec() -> bool:
    """The ANSI code page decodes every byte 0x00..0x7F as that ASCII character (every Windows
    ANSI code page does: single-byte AND double-byte, whose lead bytes are >= 0x81)."""
    try:
        return all(bytes([b]).decode(CODEC) == chr(b) for b in range(0x80))
    except LookupError, ValueError:
        return False


#: an all-ASCII byte string may be decoded with the C 'latin-1' codec: the same string as the
#: code page gives it (checked above), without the Python-level call of the registered codec
_ASCII_FAST = _ascii_compatible_codec()
#: ... the same, for the readers of other modules (the writer, the register)
ASCII_FAST = _ASCII_FAST


def _dec(raw) -> str:
    """``raw.decode(CODEC, 'replace')``, exactly."""
    if _ASCII_FAST and type(raw) in (bytes, bytearray) and raw.isascii():
        return raw.decode("latin-1")
    return raw.decode(CODEC, "replace")


def read_lpstr(data: bytes, off: int) -> Tuple[Optional[str], int]:
    """ReadString (lpstr): u16 len + len raw ANSI (code page) bytes, decoded with the lossless textcodec. No NUL on disk."""
    if off + 2 > len(data):
        return None, off
    (n,) = _U16(data, off)
    if off + 2 + n > len(data):
        return None, off
    return _dec(data[off + 2 : off + 2 + n]), off + 2 + n


# ---------------------------------------------------------------------------
# Value decoding (the bag VALUE string -> typed Python value)
# ---------------------------------------------------------------------------


@dataclass
class DecodedValue:
    """A decoded bag value."""

    raw: str
    kind: str
    value: object

    def __repr__(self) -> str:  # compact, for CLI dumps
        return "%s(%r)" % (self.kind, self.value)


#: "0x%08x,0x%08x" (a colour pair) and "0x%08x" (one colour), as CtDraw writes them: hex
#: digits only (int(.., 16) would also take '_' and a sign)
_COLOUR_PAIR_RE = re.compile(r"0x[0-9A-Fa-f]{8},0x[0-9A-Fa-f]{8}")
_COLOUR_RE = re.compile(r"0x[0-9A-Fa-f]{8}")
#: a decimal integer: ASCII digits only (str.isdigit also takes '²' and other scripts' digits)
_INT_RE = re.compile(r"-?[0-9]+")


def _looks_like_colour_pair(s: str) -> bool:
    return _COLOUR_PAIR_RE.fullmatch(s) is not None


#: (key, raw) -> DecodedValue: a page repeats a few hundred distinct pairs over its records;
#: a decoded value is never changed by its readers, so one object serves them all. Bounded;
#: emptied when full (a pure function)
_DECODE_MEMO: Dict[Tuple[str, str], "DecodedValue"] = {}
_DECODE_MEMO_MAX = 1 << 16


def decode_value(key: str, raw: str) -> DecodedValue:
    """Decode a bag value string."""
    memo_key = (key, raw)
    hit = _DECODE_MEMO.get(memo_key)
    if hit is not None:
        return hit
    if len(_DECODE_MEMO) >= _DECODE_MEMO_MAX:
        _DECODE_MEMO.clear()
    hit = _DECODE_MEMO[memo_key] = _decode_value(key, raw)
    return hit


def _decode_value(key: str, raw: str) -> DecodedValue:
    s = raw

    # mode digit (single 0-4) for known mode keys
    if key in _MODE_DIGIT_KEYS and len(s) == 1 and "0" <= s <= "9":
        return DecodedValue(raw, "mode", int(s))

    # colour pair "0x........,0x........"
    if _looks_like_colour_pair(s):
        on = int(s[2:10], 16)
        off = int(s[13:21], 16)
        return DecodedValue(
            raw,
            "color_pair",
            {
                "on": on,
                "off": off,
                "on_rgb": colorref_to_rgb(on),
                "off_rgb": colorref_to_rgb(off),
            },
        )

    # single COLORREF "0x........"
    if _COLOUR_RE.fullmatch(s) is not None:
        v = int(s[2:], 16)
        return DecodedValue(raw, "color", {"value": v, "rgb": colorref_to_rgb(v)})

    # integer (decimal ASCII, optional leading '-')
    if _INT_RE.fullmatch(s) is not None:
        try:
            return DecodedValue(raw, "int", int(s))
        except ValueError:
            pass  # swallow-ok: a digit run longer than int() converts stays a string

    # booleans / common enum keywords
    if s in ("TRUE", "FALSE"):
        return DecodedValue(raw, "bool", s == "TRUE")

    # display / language expression  @( ... )
    if s.startswith("@(") and s.endswith(")"):
        return DecodedValue(raw, "expr", s)

    # metadata Name=Value
    if "=" in s and key.startswith("METADATA_"):
        name, _, val = s.partition("=")
        return DecodedValue(raw, "metadata", (name, val))

    # internal / super reference  !*  or  !
    if s.startswith("!*") or s.startswith("!"):
        return DecodedValue(raw, "internal_ref", s)

    # object / AN reference  #...
    if s.startswith("#"):
        return DecodedValue(raw, "object_ref", s)

    # cicode expression: leading $ or a function-call shape "name(...)"
    if s.startswith("$") or (s.endswith(")") and "(" in s):
        return DecodedValue(raw, "cicode", s)

    # everything else: free-form / enum / keyword token
    return DecodedValue(raw, "string", s)


# ---------------------------------------------------------------------------
# Property bag / animation-entry scanning
# ---------------------------------------------------------------------------

_NON_KEYISH_RE = re.compile(rb"[^\x20-\x7e]")
_U16 = struct.Struct("<H").unpack_from


def _is_keyish(raw: bytes) -> bool:
    """A plausible bag key: short, non-empty, printable ASCII. Hot path in the brute-force bag
    scan, so the printable test uses a compiled C regex."""
    n = len(raw)
    if n == 0 or n > 96:
        return False
    return _NON_KEYISH_RE.search(raw) is None


def _parse_bag(p: bytes, off: int) -> Optional[Tuple[List[Tuple[str, str]], int]]:
    """Parse a property bag at ``off``."""
    # the hot loop of every parse: _is_keyish inlined (same tests), precompiled unpackers
    n = len(p)
    if off + 2 > n:
        return None
    (count,) = _U16(p, off)
    if count == 0 or count > 256:
        return None
    oo = off + 2
    pairs: List[Tuple[str, str]] = []
    append = pairs.append
    nonkey = _NON_KEYISH_RE.search
    fast = _ASCII_FAST
    keys = _KEY_STR_MEMO
    shorts = _SHORT_VALUE_MEMO
    for _ in range(count):
        if oo + 2 > n:
            return None
        (klen,) = _U16(p, oo)
        kend = oo + 2 + klen
        if kend > n:
            return None
        if klen == 0 or klen > 96:  # _is_keyish: short, non-empty ...
            return None
        kraw = p[oo + 2 : kend]
        # a page uses a few hundred distinct keys: a known one skips the printable test,
        # the decode and the intern
        k = keys.get(kraw)
        if k is None:
            if nonkey(kraw) is not None:  # ... printable ASCII
                return None
            k = _intern(kraw.decode("latin-1" if fast else CODEC))
            if len(keys) >= _KEY_STR_MEMO_MAX:
                keys.clear()
            keys[kraw] = k
        oo = kend
        if oo + 2 > n:
            return None
        (vlen,) = _U16(p, oo)
        vend = oo + 2 + vlen
        if vend > n:
            return None
        vraw = p[oo + 2 : vend]
        oo = vend
        # the keys (and the short values) are interned: a page repeats a few hundred of
        # them over its records
        if vlen <= 24:
            v = shorts.get(vraw)
            if v is None:
                if fast:
                    v = vraw.decode("latin-1") if vraw.isascii() else vraw.decode(CODEC)
                else:
                    v = vraw.decode(CODEC)
                v = _intern(v)
                if len(shorts) >= _KEY_STR_MEMO_MAX:
                    shorts.clear()
                shorts[vraw] = v
            append((k, v))
        elif fast:
            # the key is printable ASCII (_is_keyish); an ASCII value too decodes as itself
            append((k, vraw.decode("latin-1") if vraw.isascii() else vraw.decode(CODEC)))
        else:
            append((k, vraw.decode(CODEC)))
    return pairs, oo


#: raw key bytes -> the interned key, raw short value bytes -> the interned value: the parse
#: of a page and of every library element repeats the same few hundred of each. Bounded;
#: emptied when full (pure functions of the bytes)
_KEY_STR_MEMO: Dict[bytes, str] = {}
_SHORT_VALUE_MEMO: Dict[bytes, str] = {}
_KEY_STR_MEMO_MAX = 1 << 15


def parse_entry(p: bytes, off: int) -> Optional[Tuple[List[Tuple[str, str]], int, int]]:
    """Parse an animation entry at ``off``."""
    if off + 2 > len(p):
        return None
    (entry_len,) = _U16(p, off)
    if entry_len < 4 or off + 2 + entry_len > len(p):
        return None
    body_start = off + 2
    body_end = body_start + entry_len
    bag = _parse_bag(p, body_start)
    if bag is None:
        return None
    pairs, bag_end = bag
    trailer_len = body_end - bag_end
    if trailer_len < 0:
        return None
    return pairs, trailer_len, body_end


# CtDraw's loader reads the animation/property region as `u16 N` then (N+1)
# entries, each entry = 3 x [u16 size; bag], where bag
# = `u16 count; count*(lpstr,lpstr)` (count 0 ok) and the u16 size is ADVISORY
# (the bag self-terminates by count). The 3 bags per entry are [SUBSTITUTED][%TEMPLATE%]
# [DECLARATION] for a genie member. The fast parse does not walk them strictly: the
# brute-force scan's `entry_len` overshoot lands on the SUBSTITUTED bag.


def _key_is_known_uncached(key: str) -> bool:
    if key in _KNOWN_PROPERTY_KEYS:
        return True
    for stem in _KNOWN_ARRAY_STEMS:
        if key.startswith(stem):
            return True
    # editor object-reference keys: "!*#1403" -> "#1406" (an animation-number link)
    if key.startswith("!*#") and is_digits(key[3:]):
        return True
    return False


#: :func:`_key_is_known` answers (a pure function of the key string: the known-key tables are
#: constants). Keys repeat heavily: every record of a page asks for the same few dozen.
_KEY_KNOWN_MEMO: Dict[str, bool] = {}
_KEY_KNOWN_MEMO_MAX = 1 << 16


def _key_is_known(key: str) -> bool:
    r = _KEY_KNOWN_MEMO.get(key)
    if r is None:
        r = _key_is_known_uncached(key)
        if len(_KEY_KNOWN_MEMO) >= _KEY_KNOWN_MEMO_MAX:
            _KEY_KNOWN_MEMO.clear()
        _KEY_KNOWN_MEMO[key] = r
    return r


# ---------------------------------------------------------------------------
# Per-object binary header (geometry) + structural body walk
# ---------------------------------------------------------------------------
#
# Every drawing object is read
# through the leaf readers. After the prefix (class_flag / type_code /
# subtype_size / an_str) the common geometry header is:
#
#     u16   reserved (0)
#     u32   object handle / z-index
# 3 × transform (u8 mode + N × 8-byte double; see table)
# point (4 bytes, a helper(1) = ReadPoints(1))
# rect (8 bytes, ReadRect)
# rect (8 bytes, ReadRect)
#
# then flag-gated sections (name / metadata / binary colour block) and the
# animation-entry group (u32 count + count × (u16 len + body)).
#
# The header start is deterministic; the sections after it are type/flag
# dependent so they are NOT hard-framed. The walk credits the geometry
# prefix, then whatever self-validating structure it recognises: animation-entry
# groups, property bags, length-prefixed strings (style refs, symbol paths, Text
# exprs, font faces). Unidentified binary fields stay uncredited.
#
# The geometry prefix lands at or before the first bag, so crediting it is safe; it is
# also clamped to never overrun the first entry/bag.

# transform mode -> number of 8-byte doubles that follow the mode byte
XFORM_DOUBLES = {0: 0, 1: 2, 2: 2, 3: 4, 8: 6}


def geom_name_len(p: bytes, start: int) -> int:
    """Bytes taken by the reserved word, handle and object name."""
    n = len(p)
    o = start
    if o + 6 > n:
        return start
    (name_len,) = _U16(p, o + 4)
    o += 6
    if name_len and name_len <= 96 and o + name_len <= n:
        seg = p[o : o + name_len]
        # every byte a _name_byte (the bytes that are not deleted: at C speed)
        if len(seg.translate(None, _NOT_NAME_BYTES)) == name_len:
            o += name_len
    return o


#: bytes the ANSI (cp1252) code page leaves undefined -- never text
_UNDEFINED_1252 = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))


def _name_byte(b: int) -> bool:
    """A text byte of an object name: printable ASCII or a code-page letter ('Rør' -- CtDraw's
    name rule forbids only punctuation)."""
    return 0x20 <= b < 0x7F or (b >= 0x80 and b not in _UNDEFINED_1252)


#: the bytes that are not a :func:`_name_byte`
_NOT_NAME_BYTES = bytes(b for b in range(256) if not _name_byte(b))


def _geom_prefix_len(p: bytes, start: int) -> int:
    """Length of the geometry header starting at ``start``."""
    n = len(p)
    o = geom_name_len(p, start)
    if o == start:
        return start
    for _ in range(3):
        if o >= n:
            return start
        mode = p[o]
        o += 1
        ndoubles = XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0)
        o += ndoubles * 8
        if o > n:
            return start
    o += 4 + 8 + 8  # point + rect + rect
    return o if o <= n else start


def _plausible_lpstr(s: bytes) -> bool:
    """Does this length-prefixed run look like a real string?"""
    if not s:
        return False
    if not all(0x20 <= b < 0x7F for b in s):
        return False
    letters = sum(1 for b in s if 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A)
    return letters >= 1 and letters >= (len(s) // 3 or 1)


@dataclass(slots=True)
class Bag:
    """A discovered property bag / animation entry inside an object payload (slots: a page
    holds tens of thousands)."""

    offset: int  # offset within the payload
    pairs: List[Tuple[str, str]]
    trailer_len: int  # 0 / 10 / 11+ trailer bytes (genie subst.)
    is_property: bool  # any pair key is a known animation property
    consumed: int  # bytes consumed by this entry (incl. len + trailer)

    @property
    def keys(self) -> List[str]:
        return [k for k, _ in self.pairs]


# Compute the (diagnostic-only) byte-coverage metric during parsing? Off by default for speed;
# tools/verify_parser sets it True. See the note inside _scan_payload_bags.
WANT_COVERAGE = False


# ---------------------------------------------------------------------------
# Candidate prefilters for the two brute-force scans. Both scans try a structure at EVERY byte
# offset of a body; almost every offset fails the
# first checks, but each attempt still costs a Python call and a few struct unpacks. A compiled
# lookahead regex enumerates only the offsets where NECESSARY conditions of a successful parse
# hold, and the ORIGINAL checks then run at those offsets alone, in the same (ascending) order
# and with the same skip-past-a-match rule -- so the result is identical by construction:
#
# * an animation entry (parse_entry) needs u16 entry_len >= 4, a bag count 1..256, a first key
#   length 1..96 and a printable first key byte (_is_keyish): 7 bytes, all in the regex;
# * a Text expression needs the lpstr to start '@(' (bytes 2 and 3), a font-face token a length
#   of 1..32 characters (1..32 bytes; 1..64 on a double-byte code page) and a first character
#   that is alphanumeric or ' -_' -- an ASCII letter / digit or a byte >= 0x80 (every byte
#   >= 0x80 is admitted: whatever the code page makes of it).
#
# The character-level test of the Text prefilter assumes the ANSI code page decodes 0x00..0x7F
# as ASCII (every Windows ANSI code page does; _ASCII_FAST checks it -- if not, the byte loops
# run instead).
BAG_CANDIDATE = re.compile(
    rb"(?=(?:[\x04-\xff].|[\x00-\x03][\x01-\xff])"  # u16 entry_len >= 4
    rb"(?:[\x01-\xff]\x00|\x00\x01)"  # u16 bag count 1..256
    rb"[\x01-\x60]\x00"  # u16 first key length 1..96
    rb"[\x20-\x7e])",  # the key's first byte printable
    re.S,
)
# The font token's 1..32 is a CHARACTER count: on a single-byte code page the lpstr's byte
# length; on a double-byte one (MULTIBYTE) a character is 1 or 2 bytes (textcodec: lead bytes
# >= 0x81, so the first character still comes from the first byte), so 1..64 bytes
_TEXT_CANDIDATE = re.compile(
    (
        rb"(?=..@\(|[\x01-\x40]\x00[ \-0-9A-Z_a-z\x80-\xff])"
        if MULTIBYTE
        else rb"(?=..@\(|[\x01-\x20]\x00[ \-0-9A-Z_a-z\x80-\xff])"
    ),
    re.S,
)
# the text scan searches each alternative on its own, anchored on a literal: the '@(' at
# c + 2 of an expression, the length's high byte (the \x00 at c + 1, the length byte in a
# lookbehind) of a font token
_TEXT_EXPR_AT = re.compile(rb"@\(")
_TEXT_FONT_AT = re.compile(
    (
        rb"\x00(?<=[\x01-\x40]\x00)[ \-0-9A-Z_a-z\x80-\xff]"
        if MULTIBYTE
        else rb"\x00(?<=[\x01-\x20]\x00)[ \-0-9A-Z_a-z\x80-\xff]"
    ),
    re.S,
)
# a font-face token's characters: ``all(ch.isalnum() or ch in " -_" for ch in s)`` -- the regex
# \w of a str pattern is exactly str.isalnum() or '_' (CPython sre: Py_UNICODE_ISALNUM || '_';
# tests/test_perf_core.py checks every code point); used on the single-byte code pages (every
# character a decoded lpstr can hold is checked at import), the generator on a DBCS one
_FONT_CHARS = re.compile(r"[\w \-]+")


def _code_page_chars() -> Optional[str]:
    """Every character a decoded lpstr can hold (single-byte code pages), else None."""
    if MULTIBYTE:
        return None
    try:
        table = bytes(range(256)).decode(CODEC)
    except LookupError, ValueError:
        return None
    return table if len(table) == 256 else None


def _font_chars_ok(table: Optional[str]) -> bool:
    return table is not None and all(
        (_FONT_CHARS.fullmatch(ch) is not None) == (ch.isalnum() or ch in " -_") for ch in table
    )


_CP_CHARS = _code_page_chars()
_FONT_RE_OK = _font_chars_ok(_CP_CHARS)
#: the code page's alphabetic characters: ``not _ALPHA_CHARS.isdisjoint(s)`` is
#: ``any(ch.isalpha() for ch in s)`` for a string of them (the set test runs in C)
_ALPHA_CHARS = frozenset(ch for ch in (_CP_CHARS or "") if ch.isalpha())


_PREFILTER_OK = _ASCII_FAST

# The scan's search for the next candidate: the same seven-byte condition as BAG_CANDIDATE,
# anchored on the first key length's high byte (the \x00 at c + 5: sre runs a pattern with a
# literal first byte as a tight loop), the five bytes before it in a fixed-width lookbehind.
# A match at a is the candidate
# c = a - 5, so search(p, pos + 5) gives the first candidate at or after pos (a lookbehind
# reads before the search position, never before the start of the string).
_BAG_ANCHOR = re.compile(
    rb"\x00(?<=(?:[\x04-\xff].|[\x00-\x03][\x01-\xff])"  # u16 entry_len >= 4
    rb"(?:[\x01-\xff]\x00|\x00\x01)"  # u16 bag count 1..256
    rb"[\x01-\x60]\x00)"  # u16 first key length 1..96
    rb"[\x20-\x7e]",  # the key's first byte printable
    re.S,
)
_ANCHOR_BACK = 5

#: an animation entry's parse by its bytes (the u16 entry_len and the body after it): what
#: parse_entry reads, for it fails on a read past them. A page repeats a few thousand
#: distinct entries over its records. The value is (the pairs as a tuple, trailer_len,
#: is_property), None for bytes that do not parse; each Bag gets a list of its own. Entries up
#: to _ENTRY_MEMO_SPAN bytes. Bounded (about 1 KB an entry: 16 MB when full); emptied when full
#: (a pure function of the bytes)
_ENTRY_MEMO: Dict[bytes, Optional[Tuple[Tuple[Tuple[str, str], ...], int, bool]]] = {}
_ENTRY_MEMO_MAX = 1 << 14
_ENTRY_MEMO_SPAN = 1024
_NO_ENTRY = object()


def _entry_spec(p: bytes, c: int) -> Optional[Tuple[Tuple[Tuple[str, str], ...], int, bool]]:
    """:func:`parse_entry` at ``c`` as the scan keeps it (pairs, trailer_len, is_property)."""
    entry = parse_entry(p, c)
    if entry is None:
        return None
    pairs = entry[0]
    known = _key_is_known
    return tuple(pairs), entry[1], any(known(k) for k, _ in pairs)


def _scan_bags_prefiltered(p: bytes, start: int) -> List[Bag]:
    """The first pass of :func:`_scan_payload_bags` at the prefiltered offsets only: the next
    candidate at or after the position the byte loop would try next (the end of an entry just
    found, else the candidate + 1)."""
    bags: List[Bag] = []
    if type(p) is not bytes:
        p = bytes(p)  # the memo's keys are slices of it
    n = len(p)
    search = _BAG_ANCHOR.search
    back = _ANCHOR_BACK
    memo = _ENTRY_MEMO
    get = memo.get
    u16 = _U16
    span = _ENTRY_MEMO_SPAN
    miss = _NO_ENTRY
    m = search(p, start + back)
    while m is not None:
        c = m.start() - back
        (entry_len,) = u16(p, c)
        end = c + 2 + entry_len
        if end > n:  # parse_entry: the entry overruns the payload
            m = search(p, c + 1 + back)
            continue
        if end - c <= span:
            key = p[c:end]
            hit = get(key, miss)
            if hit is miss:
                hit = _entry_spec(p, c)
                if len(memo) >= _ENTRY_MEMO_MAX:
                    memo.clear()
                memo[key] = hit
        else:
            hit = _entry_spec(p, c)
        if hit is None:
            m = search(p, c + 1 + back)
            continue
        bags.append(Bag(c, list(hit[0]), hit[1], hit[2], end - c))
        # most entries are followed by the next one at once (the three bags of a level): an
        # entry the memo knows to parse there is the candidate the search would return (the
        # anchor's conditions are necessary ones), so it is taken without the search
        while end + 2 <= n:
            c = end
            (entry_len,) = u16(p, c)
            end = c + 2 + entry_len
            if end > n or end - c > span:
                break
            hit = get(p[c:end])  # (None: not known, or known not to parse)
            if hit is None:
                break
            bags.append(Bag(c, list(hit[0]), hit[1], hit[2], end - c))
        else:
            c = end
        m = search(p, c + back)
    return bags


def _scan_payload_bags(p: bytes, start: int) -> Tuple[List[Bag], int]:
    """Walk the payload body and pick out the parts we recognise."""
    if not WANT_COVERAGE and _PREFILTER_OK:
        return _scan_bags_prefiltered(p, start), 0
    n = len(p)
    bags: List[Bag] = []

    # First pass: discover the entry/bag structures (they anchor everything).
    o = start
    while o < n - 2:
        entry = parse_entry(p, o)
        if entry is not None:
            pairs, trailer_len, end = entry
            consumed = end - o
            is_prop = any(_key_is_known(k) for k, _ in pairs)
            bags.append(Bag(o, pairs, trailer_len, is_prop, consumed))
            o = end
        else:
            o += 1

    # The byte-coverage metric (covered_mask + the second lpstr-gap scan below) is a PARSE-QUALITY
    # DIAGNOSTIC only -- never consumed by the reader/render/edit path, just summed for reports and
    # tools/verify_parser. It is by far the costliest part of parsing (a byte-by-byte bitmap + a
    # second whole-body lpstr scan per object), so SKIP it unless a tool
    # explicitly asks (`WANT_COVERAGE`).
    if not WANT_COVERAGE:
        return bags, 0

    # Coverage bitmap over the body region [start, n).
    covered_mask = bytearray(n - start)

    def mark(a: int, b: int) -> None:
        a = max(a, start)
        b = min(b, n)
        if b > a:
            for i in range(a - start, b - start):
                covered_mask[i] = 1

    # Geometry header, clamped to the first discovered entry/bag.
    first_struct = bags[0].offset if bags else n
    geom_end = _geom_prefix_len(p, start)
    if geom_end > start:
        mark(start, min(geom_end, first_struct))

    # Animation-entry GROUP header: 6 bytes ``u16 reserved(0) + u32 count`` just
    # before the first entry (count = animation objects in the group; the u16
    # is 0). Credit it only past the geometry header to avoid double-count.
    if bags and first_struct - 6 >= start:
        hdr = first_struct - 6
        if struct.unpack_from("<H", p, hdr)[0] == 0:
            cnt = struct.unpack_from("<I", p, hdr + 2)[0]
            if 1 <= cnt <= 256:
                mark(hdr, first_struct)

    # Entry/bag bytes.
    for bag in bags:
        mark(bag.offset, bag.offset + bag.consumed)

    # Length-prefixed strings in the still-uncredited gaps (style/symbol/expr/font).
    o = start
    while o < n - 2:
        if covered_mask[o - start]:
            o += 1
            continue
        (ln,) = struct.unpack_from("<H", p, o)
        if 1 <= ln <= 512 and o + 2 + ln <= n and _plausible_lpstr(p[o + 2 : o + 2 + ln]):
            mark(o, o + 2 + ln)
            o += 2 + ln
        else:
            o += 1

    covered = sum(covered_mask)
    return bags, covered


# ---------------------------------------------------------------------------
# Object record
# ---------------------------------------------------------------------------


@dataclass
class PageObject:
    """One drawing-object record from the page object-data region."""

    index: int  # z-order index (record order on disk)
    record_offset: int  # offset of the record (within page block)
    payload_size: int
    next_link: int  # forward cumulative link (0 on last); redundant

    # Verified payload prefix
    class_flag: int  # u16 @0 -- the CHILD COUNT (alias child_count)
    type_code: int  # u32 @2 -- object type (format/codes.TC)
    subtype_size: int  # u32 @6 -- the dwFlags word (alias dwflags; codes.DWF)
    an_str: str  # animation-number id text ("-1" = none)

    # Discovered structures
    # a tuple: the cached parse shares it (writer.FRESH_PARSE_ATTR), so it is never changed in
    # place (build a new tuple)
    bags: Tuple[Bag, ...] = ()

    # Undecoded binary body (between an_str and the first bag); kept raw.
    body_offset: int = 0  # offset within payload where decoding begins
    body_raw: bytes = b""  # the full post-prefix payload bytes
    bytes_covered: int = 0  # payload bytes covered by parsed bags

    # Text-object extras (only populated for type_code == 0x05)
    text_exprs: List[str] = field(default_factory=list)
    font_face: Optional[str] = None
    # The verbatim payload of a record whose prefix does not frame (payload
    # shorter than the 12-byte prefix, or an an_len overrunning it): the
    # element-definition trailer records every library element block ends with
    # (a ~130-byte definition record + two 2-byte records).
    # object_payload() emits it unchanged so library blocks round-trip.
    raw_payload: Optional[bytes] = None

    @property
    def dwflags(self) -> int:
        """The record's dwFlags word (stored as ``subtype_size``: format/codes.DWF)."""
        return self.subtype_size

    @dwflags.setter
    def dwflags(self, value: int) -> None:
        self.subtype_size = value

    @property
    def child_count(self) -> int:
        """How many records follow as this one's children (stored as ``class_flag``)."""
        return self.class_flag

    @child_count.setter
    def child_count(self, value: int) -> None:
        self.class_flag = value

    @property
    def an_id(self) -> int:
        """Numeric animation number; -1 = none."""
        try:
            return int(self.an_str)
        except ValueError, TypeError:
            return -1

    @property
    def is_text(self) -> bool:
        return self.type_code == TEXT_TYPE_CODE

    @property
    def type_kind(self) -> str:
        """Best-effort human label for the type code (only 0x05=Text verified)."""
        if self.type_code == TEXT_TYPE_CODE:
            return "Text"
        if (self.type_code & 0xF000) == 0x1000:
            return "Symbol/Library(0x%X)" % self.type_code
        return "Type(0x%X)" % self.type_code

    def properties(self) -> Dict[str, DecodedValue]:
        """The known property bags as a decoded key -> value dict."""
        out: Dict[str, DecodedValue] = {}
        for bag in self.bags:
            if not bag.is_property:
                continue
            for k, v in bag.pairs:
                out[k] = decode_value(k, v)
        return out

    def raw_properties(self) -> Dict[str, str]:
        """All discovered key->value pairs (raw strings), including substitutions (a fresh
        dict each call; the merge is memoised on the bags tuple, which is never changed in
        place)."""
        d = self.__dict__
        m = d.get("_raw_props_memo")
        if m is not None and m[0] is self.bags:
            return dict(m[1])
        out: Dict[str, str] = {}
        for bag in self.bags:
            for k, v in bag.pairs:
                out[k] = v
        d["_raw_props_memo"] = (self.bags, out)
        return dict(out)


def parse_text_extras(payload: bytes, prefix_end: int) -> Tuple[List[str], Optional[str]]:
    """A Text record's @() expressions and font face (:func:`_parse_text_extras`): what the
    parse of a TEXT record reads besides its bags (writer._same_parse_after)."""
    return _parse_text_extras(payload, prefix_end)


def _parse_text_extras(payload: bytes, prefix_end: int) -> Tuple[List[str], Optional[str]]:
    """A text object's expressions and font face."""
    if _PREFILTER_OK:
        return _text_extras_prefiltered(payload, prefix_end)
    exprs: List[str] = []
    font: Optional[str] = None
    o = prefix_end
    n = len(payload)
    while o < n - 2:
        s, no = read_lpstr(payload, o)
        if s is not None and s.startswith("@(") and s.endswith(")") and no > o + 2:
            if s not in exprs:
                exprs.append(s)
            o = no
            continue
        # font face: short pure-alpha token (e.g. Arial, Tahoma)
        if (
            s is not None
            and 1 <= len(s) <= 32
            and all(c.isalnum() or c in " -_" for c in s)
            and any(c.isalpha() for c in s)
            and not s.startswith("@")
        ):
            # only a font if not a known property key/value token
            if not _key_is_known(s):
                font = s
        o += 1
    return exprs, font


def _lpstr_verdict(raw: bytes) -> Tuple[int, Optional[str]]:
    """What the text scan makes of the lpstr ``raw`` (u16 length + its bytes, all there):
    (1, the string) for an @() expression, (2, the string) for a font-face token, else
    (0, None)."""
    s = _dec(raw[2:])
    if s.startswith("@(") and s.endswith(")") and len(raw) > 2:
        return 1, s
    font_re = _FONT_CHARS.fullmatch if _FONT_RE_OK else None
    if (
        1 <= len(s) <= 32
        and (font_re(s) is not None if font_re is not None else all(ch.isalnum() or ch in " -_" for ch in s))
        and (not _ALPHA_CHARS.isdisjoint(s) if font_re is not None else any(ch.isalpha() for ch in s))
        and not s.startswith("@")
    ):
        if not _key_is_known(s):
            return 2, s
    return 0, None


#: :func:`_lpstr_verdict` by the lpstr's bytes: the text scan's candidates are mostly the
#: same few bag keys and values. Up to
#: _LPSTR_MEMO_SPAN bytes. Bounded; emptied when full (a pure function of the bytes)
_LPSTR_MEMO: Dict[bytes, Tuple[int, Optional[str]]] = {}
_LPSTR_MEMO_MAX = 1 << 14
_LPSTR_MEMO_SPAN = 130


def _text_extras_prefiltered(payload: bytes, prefix_end: int) -> Tuple[List[str], Optional[str]]:
    """:func:`_parse_text_extras` at the prefiltered offsets only (the byte loop does nothing at
    any other offset but step on)."""
    exprs: List[str] = []
    font: Optional[str] = None
    if type(payload) is not bytes:
        payload = bytes(payload)  # the memo's keys are slices of it
    n = len(payload)
    memo = _LPSTR_MEMO
    get = memo.get
    u16 = _U16
    span = _LPSTR_MEMO_SPAN
    # the candidates of _TEXT_CANDIDATE's two alternatives, each from its own search (both
    # anchored on a literal), merged in
    # order: ca / cb = the first candidate of each at or after the scan's position (none: n)
    search_a = _TEXT_EXPR_AT.search
    search_b = _TEXT_FONT_AT.search
    m = search_a(payload, prefix_end + 2)
    ca = m.start() - 2 if m is not None else n
    m = search_b(payload, prefix_end + 1)
    cb = m.start() - 1 if m is not None else n
    while True:
        c = ca if ca < cb else cb
        if c >= n - 2:  # (no candidate left: c == n)
            break
        (ln,) = u16(payload, c)
        no = c + 2 + ln
        nxt = c + 1
        if no <= n:  # (else read_lpstr: no string here)
            if ln + 2 <= span:
                raw = payload[c:no]
                hit = get(raw)
                if hit is None:
                    hit = _lpstr_verdict(raw)
                    if len(memo) >= _LPSTR_MEMO_MAX:
                        memo.clear()
                    memo[raw] = hit
            else:
                hit = _lpstr_verdict(payload[c:no])
            kind = hit[0]
            if kind == 1:
                s = hit[1]
                if s not in exprs:
                    exprs.append(s)
                nxt = no
            elif kind == 2:
                font = hit[1]
        if ca < nxt:
            m = search_a(payload, nxt + 2)
            ca = m.start() - 2 if m is not None else n
        if cb < nxt:
            m = search_b(payload, nxt + 1)
            cb = m.start() - 1 if m is not None else n
    return exprs, font


# ---------------------------------------------------------------------------
# Page block
# ---------------------------------------------------------------------------


@dataclass
class PageBlock:
    """Decoded ``page`` block: head fields + list of objects."""

    # Head (0x34 fixed when word2 >= 0x0c)
    objdata_size: int
    object_count: int
    thumb_size: int
    page_width: int
    page_height: int
    link_count: int
    page_identity: bytes  # 20 raw bytes
    area_code: str  # 4 chars (default four spaces)
    head_size: int  # 0x34 or 0x24 (legacy)

    # Regions
    data_start: int  # offset where the object-data region begins
    link_table_raw: bytes = b""  # link/import table (skipped, kept raw)
    objects: List[PageObject] = field(default_factory=list)
    thumbnail: bytes = b""  # raw "TH" blob (BMP w/ swapped signature)

    # Bookkeeping / coverage
    block_size: int = 0
    objdata_consumed: int = 0  # bytes walked across object records
    trailing_pad_zero: bool = False

    @property
    def page_identity_hex(self) -> str:
        return self.page_identity.hex()

    @classmethod
    def parse(cls, block: Block, word2: int = 0x0C) -> "PageBlock":
        return parse_page(block, word2)


def extract_bmp(thumb: bytes) -> Optional[bytes]:
    """ "TH" thumbnail blob -> usable BMP (swap sig, fix bfSize)."""
    if len(thumb) < 6 or thumb[0:2] != b"TH":
        return None
    # (one copy of the pixels)
    return b"".join((b"BM", struct.pack("<I", len(thumb)), memoryview(thumb)[6:]))


def parse_page(block: Block, word2: int = 0x0C, lazy: bool = False) -> PageBlock:
    """Parse a ``page`` block."""
    d = block.data
    if word2 < 0x0C:
        # an old form (format/upgrade.py): read as the current one. The block itself
        # is left as it is; parse_ctg_bytes upgrades a page file's container, which
        # is what a save writes (a library in an old form is read only: element.py)
        d = upgrade.upgrade_page_block(d, word2)
    head_size = PAGE_HEAD_SIZE
    if len(d) < head_size:
        raise ValueError("page block too small (%d bytes)" % len(d))

    objdata_size = struct.unpack_from("<I", d, 0x00)[0]
    object_count = struct.unpack_from("<I", d, 0x04)[0]
    thumb_size = struct.unpack_from("<I", d, 0x08)[0]
    page_width = struct.unpack_from("<I", d, 0x14)[0]
    page_height = struct.unpack_from("<I", d, 0x18)[0]
    link_count = struct.unpack_from("<I", d, 0x1C)[0]
    page_identity = bytes(d[0x20:0x34])
    # (the area code is the link table's first 4 bytes: a page with no table has none,
    # and 0x34 is its first record then)
    area_code = d[0x34:0x38].decode(CODEC, "replace") if link_count else "    "

    data_start = head_size + link_count * LINK_ENTRY_SIZE
    link_table_raw = bytes(d[head_size:data_start])

    objects: List[PageObject] = []
    off = data_start
    region_end = data_start + objdata_size
    nd = len(d)
    header = _RECORD_HEADER
    parse_one = _parse_object_lazy if lazy and not WANT_COVERAGE else parse_object
    append = objects.append
    for i in range(object_count):
        if off + RECORD_HEADER_SIZE > nd:
            break
        payload_size, next_link = header(d, off)
        start = off + RECORD_HEADER_SIZE
        append(parse_one(i, off, payload_size, next_link, d[start : start + payload_size]))
        off = start + payload_size

    objdata_consumed = off - data_start

    # Thumbnail
    thumbnail = b""
    if thumb_size:
        thumbnail = bytes(d[region_end : region_end + thumb_size])

    pad_start = region_end + thumb_size
    trailing = d[pad_start:]
    trailing_pad_zero = trailing == b"\x00" * len(trailing)

    return PageBlock(
        objdata_size=objdata_size,
        object_count=object_count,
        thumb_size=thumb_size,
        page_width=page_width,
        page_height=page_height,
        link_count=link_count,
        page_identity=page_identity,
        area_code=area_code,
        head_size=head_size,
        data_start=data_start,
        link_table_raw=link_table_raw,
        objects=objects,
        thumbnail=thumbnail,
        block_size=len(d),
        objdata_consumed=objdata_consumed,
        trailing_pad_zero=trailing_pad_zero,
    )


_RECORD_HEADER = struct.Struct("<II").unpack_from
_PREFIX = struct.Struct("<HIIH").unpack_from


def parse_object(
    index: int, record_offset: int, payload_size: int, next_link: int, payload: bytes
) -> PageObject:
    """Parse one object record payload into a :class:`PageObject`."""
    # Verified prefix
    if type(payload) is not bytes:
        payload = bytes(payload)  # the memos' keys are slices of it
    np = len(payload)
    if np >= 12:
        class_flag, type_code, subtype_size, an_len = _PREFIX(payload, 0)
    else:
        class_flag = _U16(payload, 0x00)[0] if np >= 2 else 0
        type_code = struct.unpack_from("<I", payload, 0x02)[0] if np >= 6 else 0
        subtype_size = struct.unpack_from("<I", payload, 0x06)[0] if np >= 10 else 0
        an_len = 0
    an_end = 0x0C + an_len
    raw_payload = None
    if an_end <= np:
        an_raw = payload[0x0C:an_end]  # _dec, inline
        an_str = (
            an_raw.decode("latin-1") if _ASCII_FAST and an_raw.isascii() else an_raw.decode(CODEC, "replace")
        )
    else:
        an_str = "-1"
    if an_end > np or np < 0x0C:
        an_end = min(0x0C, np)
        raw_payload = bytes(payload)  # unframed prefix: keep verbatim

    body_offset = an_end
    body_raw = bytes(payload[body_offset:])

    if not WANT_COVERAGE and _PREFILTER_OK:
        bags = _scan_bags_prefiltered(payload, body_offset)
        covered = 0
    else:
        bags, covered = _scan_payload_bags(payload, body_offset)

    if type_code == TEXT_TYPE_CODE:
        text_exprs, font_face = _parse_text_extras(payload, body_offset)
    else:
        text_exprs, font_face = [], None

    return PageObject(
        index,
        record_offset,
        payload_size,
        next_link,
        class_flag,
        type_code,
        subtype_size,
        an_str,
        tuple(bags),
        body_offset,
        body_raw,
        covered,
        text_exprs,
        font_face,
        raw_payload,
    )


def parse_payload(payload: bytes) -> PageObject:
    """One record payload (prefix + body) as a record of its own (index 0, offset 0)."""
    return parse_object(0, 0, len(payload), 0, payload)


def parse_record_shell(payload: bytes) -> PageObject:
    """:func:`parse_object` of ``payload`` (index, offset and link 0) without the bag scan
    and the text extras: the prefix fields and the body alike, no bags. For a reader of
    nothing else (pagesummary's flashing test of a symbol element)."""
    if type(payload) is not bytes:
        payload = bytes(payload)
    np = len(payload)
    if np >= 12:
        class_flag, type_code, subtype_size, an_len = _PREFIX(payload, 0)
    else:
        class_flag = _U16(payload, 0x00)[0] if np >= 2 else 0
        type_code = struct.unpack_from("<I", payload, 0x02)[0] if np >= 6 else 0
        subtype_size = struct.unpack_from("<I", payload, 0x06)[0] if np >= 10 else 0
        an_len = 0
    an_end = 0x0C + an_len
    raw_payload = None
    an_str = _dec(payload[0x0C:an_end]) if an_end <= np else "-1"
    if an_end > np or np < 0x0C:
        an_end = min(0x0C, np)
        raw_payload = payload
    return PageObject(
        0,
        0,
        np,
        0,
        class_flag,
        type_code,
        subtype_size,
        an_str,
        (),
        an_end,
        payload[an_end:],
        0,
        [],
        None,
        raw_payload,
    )


# ---------------------------------------------------------------------------
# Lazily scanned records
# ---------------------------------------------------------------------------
# A library element's records are parsed when the element is first resolved, but most of them
# are only ever read for their body (an owner record's style block, a handle, a link). A
# LazyPageObject scans them (the bags and the text extras) when one of the four scanned
# fields is first read: the values are those of the parse
# made then (the payload it read is kept until then; the scan reads nothing else).

#: the fields the bag scan and the text extras give (parse_object)
_SCANNED_FIELDS = ("bags", "bytes_covered", "text_exprs", "font_face")

_LAZY_SRC = "_lazy_scan_src"


def _lazy_scan(obj) -> None:
    """Fill the scanned fields of a :class:`LazyPageObject` not set yet (a field set since
    the parse keeps its value) from the payload it was parsed from."""
    d = obj.__dict__
    src = d.get(_LAZY_SRC)
    if src is None:
        return  # (scanned meanwhile)
    payload, body_offset, type_code = src
    # (the scan parse_object makes with the coverage diagnostic off)
    if _PREFILTER_OK:
        bags = _scan_bags_prefiltered(payload, body_offset)
    else:
        bags, _covered = _scan_payload_bags(payload, body_offset)
    text_exprs: List[str] = []
    font_face: Optional[str] = None
    if type_code == TEXT_TYPE_CODE:
        text_exprs, font_face = _parse_text_extras(payload, body_offset)
    # (bytes_covered: 0, as a parse with the coverage diagnostic off gives -- a lazy parse is
    # made only then)
    d.setdefault("bags", tuple(bags))
    d.setdefault("bytes_covered", 0)
    d.setdefault("text_exprs", text_exprs)
    d.setdefault("font_face", font_face)
    d.pop(_LAZY_SRC, None)


class _ScannedField:
    """A scanned field of a LazyPageObject: read from the instance once it is there (a
    non-data descriptor: the instance's own value wins), else scanned first."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        _lazy_scan(obj)
        try:
            return obj.__dict__[self.name]
        except KeyError:
            raise AttributeError(self.name) from None


class LazyPageObject(PageObject):
    """A PageObject whose bags and text extras are scanned on first use (parse_page lazy);
    in every other way, equality with a PageObject included, a PageObject."""

    bags = _ScannedField("bags")
    bytes_covered = _ScannedField("bytes_covered")
    text_exprs = _ScannedField("text_exprs")
    font_face = _ScannedField("font_face")

    def __eq__(self, other):
        if not isinstance(other, PageObject):
            return NotImplemented
        names = PageObject.__dataclass_fields__
        return all(getattr(self, n) == getattr(other, n) for n in names)

    __hash__ = None

    def __repr__(self) -> str:
        return PageObject.__repr__(self).replace(type(self).__qualname__, PageObject.__qualname__, 1)


def _parse_object_lazy(
    index: int, record_offset: int, payload_size: int, next_link: int, payload: bytes
) -> PageObject:
    """:func:`parse_object` with the bag scan and the text extras left for later
    (:class:`LazyPageObject`): the prefix and the body as it reads them."""
    payload = bytes(payload)
    class_flag = struct.unpack_from("<H", payload, 0x00)[0] if len(payload) >= 2 else 0
    type_code = struct.unpack_from("<I", payload, 0x02)[0] if len(payload) >= 6 else 0
    subtype_size = struct.unpack_from("<I", payload, 0x06)[0] if len(payload) >= 10 else 0
    an_len = struct.unpack_from("<H", payload, 0x0A)[0] if len(payload) >= 12 else 0
    an_end = 0x0C + an_len
    an_str = _dec(payload[0x0C:an_end]) if an_end <= len(payload) else "-1"
    raw_payload = None
    if an_end > len(payload) or len(payload) < 0x0C:
        an_end = min(0x0C, len(payload))
        raw_payload = payload
    obj = LazyPageObject(
        index=index,
        record_offset=record_offset,
        payload_size=payload_size,
        next_link=next_link,
        class_flag=class_flag,
        type_code=type_code,
        subtype_size=subtype_size,
        an_str=an_str,
        body_offset=an_end,
        body_raw=payload[an_end:],
        raw_payload=raw_payload,
    )
    d = obj.__dict__
    for name in _SCANNED_FIELDS:
        d.pop(name, None)
    d[_LAZY_SRC] = (payload, an_end, type_code)
    return obj


# ---------------------------------------------------------------------------
# Convenience top-level parse
# ---------------------------------------------------------------------------


@dataclass
class ParsedCtg:
    """Whole-file parse result for a page ``.ctg``."""

    container: Container
    page: Optional[PageBlock]
    attributes: Optional[AttributesPage]


def parse_ctg(path: str) -> ParsedCtg:
    """Parse a ``.ctg`` file path into its page + attributes blocks."""
    with open(path, "rb") as fh:
        data = fh.read()
    return parse_ctg_bytes(data)


def parse_ctg_bytes(data: bytes, lazy: bool = False) -> ParsedCtg:
    """Parse in-memory ``.ctg`` bytes into its page + attributes blocks (the file bytes are
    not kept: every block holds its own copy, and the writer serialises from the blocks).
    ``lazy``: the records' bags are scanned when first read (parse_page)."""
    c = Container.parse(data, keep_raw=False)
    # an old form: the container is upgraded in place (version 9 / word2 0x0C, the GUID,
    # the link table, the record bodies), which is what the Graphics Builder's own save
    # writes for it (format/upgrade.py)
    upgrade.upgrade_container(c)
    pg_block = c.block("page")
    at_block = c.block("attributes")
    page = parse_page(pg_block, c.word2, lazy=lazy) if pg_block is not None else None
    attrs = parse_attributes(at_block) if at_block is not None else None
    return ParsedCtg(container=c, page=page, attributes=attrs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _summarise(path: str, max_objects: int = 0) -> None:
    parsed = parse_ctg(path)
    c = parsed.container
    page = parsed.page
    attrs = parsed.attributes

    print("FILE  %s  (%d bytes)" % (path, os.path.getsize(path)))
    print(
        "  container: version=%d word2=%d ts_created=0x%08X ts_modified=0x%08X dir_capacity=%d"
        % (c.version, c.word2, c.ts_created, c.ts_modified, c.dword_c)
    )
    for b in c.blocks:
        print("    block %-12s tag=0x%04X off=0x%X size=%d" % (b.name, b.tag, b.offset, b.size))

    if attrs is not None:
        print("\nATTRIBUTES")
        print(
            "  size=%d  outer_len=0x%X  inner_len=0x%X  flag=%d"
            % (attrs.block_size, attrs.outer_len, attrs.inner_len, attrs.flag)
        )
        print(
            "  page extent: %d x %d   editor viewport: %d x %d"
            % (attrs.width, attrs.height, attrs.editor_view_w, attrs.editor_view_h)
        )
        print(
            "  bg_color=0x%08X rgb=%s  page_type_flag=0x%X"
            % (attrs.bg_color0, attrs.background_rgb, attrs.page_type_flag)
        )
        print("  identity=%s  ts_modified=0x%08X" % (attrs.page_identity_hex, attrs.ts_modified))
        print("  list1 (%d): %s" % (attrs.list1.count, attrs.list1.pairs))
        print("  list2 (%d): %s" % (attrs.list2.count, attrs.list2.pairs))
        print("  trailing 512-pad all-zero: %s" % attrs.trailing_pad_zero)

    if page is not None:
        walk_end = page.data_start + page.objdata_consumed
        expected_end = page.data_start + page.objdata_size
        print("\nPAGE")
        print(
            "  size=%d  head=0x%X  objdata_size=0x%X  object_count=%d  thumb_size=%d  link_count=%d"
            % (
                page.block_size,
                page.head_size,
                page.objdata_size,
                page.object_count,
                page.thumb_size,
                page.link_count,
            )
        )
        print(
            "  page extent: %d x %d   area=%r   data_start=0x%X"
            % (page.page_width, page.page_height, page.area_code, page.data_start)
        )
        print("  identity=%s" % page.page_identity_hex)
        print(
            "  object walk: %d records, tiles=%s (end 0x%X == 0x%X)  trailing-pad-zero=%s"
            % (len(page.objects), walk_end == expected_end, walk_end, expected_end, page.trailing_pad_zero)
        )
        if attrs is not None:
            print("  identity matches attributes: %s" % (page.page_identity == attrs.page_identity))
            print(
                "  extent matches attributes: %s"
                % (page.page_width == attrs.width and page.page_height == attrs.height)
            )

        # coverage of object payloads by parsed bags
        total_body = sum(len(o.body_raw) for o in page.objects)
        total_cov = sum(o.bytes_covered for o in page.objects)
        n_prop = sum(1 for o in page.objects for b in o.bags if b.is_property)
        print(
            "  object payload coverage: %d / %d body bytes in parsed bags (%.1f%%); %d property bags"
            % (total_cov, total_body, (100.0 * total_cov / total_body) if total_body else 0.0, n_prop)
        )

        print("\n  OBJECTS")
        objs = page.objects if max_objects <= 0 else page.objects[:max_objects]
        for o in objs:
            props = o.properties()
            extra = ""
            if o.is_text:
                extra = "  text_exprs=%d font=%r" % (len(o.text_exprs), o.font_face)
            print(
                "   [%4d] z=%-4d class=0x%-4X type=%-18s an=%-4s sub=0x%-3X bags=%d props=%d%s"
                % (
                    o.index,
                    o.index,
                    o.class_flag,
                    o.type_kind,
                    o.an_str,
                    o.subtype_size,
                    len(o.bags),
                    len(props),
                    extra,
                )
            )
            if props:
                shown = list(props.items())[:6]
                for k, dv in shown:
                    print("            %-16s = %r" % (k, dv))
                if len(props) > 6:
                    print("            ... (%d more)" % (len(props) - 6))
        if max_objects > 0 and len(page.objects) > max_objects:
            print("   ... (%d more objects)" % (len(page.objects) - max_objects))


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m ctgedit.format.page <file.ctg> [max_objects]", file=sys.stderr)
        return 2
    path = argv[0]
    max_objects = int(argv[1]) if len(argv) > 1 else 0
    _summarise(path, max_objects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("page.known_keys", _KEY_KNOWN_MEMO, on=())
# pure functions of their keys, bounded and self-emptying (no event empties them)
_caches.register("page.decoded_values", _DECODE_MEMO, on=())
_caches.register("page.key_strings", _KEY_STR_MEMO, on=())
_caches.register("page.short_values", _SHORT_VALUE_MEMO, on=())
_caches.register("page.entries", _ENTRY_MEMO, on=())
_caches.register("page.text_lpstrs", _LPSTR_MEMO, on=())
