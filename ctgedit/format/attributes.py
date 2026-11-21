"""Parser for the ``attributes`` block of a .ctg file."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .container import Block
from .textcodec import CODEC

HEAD_SIZE = 0x54  # 84 bytes fixed header
TRAILING_PAD = 0x200  # 512 bytes of zero padding at the end
NO_COLOUR = 0xFFFFFFFF  # COLORREF "no colour / default" sentinel


def _read_lpstr(data: bytes, off: int) -> Tuple[Optional[str], int]:
    """Read a length-prefixed string (u16 len + raw ANSI bytes, see textcodec).

    Returns (string, new_offset), or (None, off) on truncation; never raises.
    """
    if off + 2 > len(data):
        return None, off
    (n,) = struct.unpack_from("<H", data, off)
    if off + 2 + n > len(data):
        return None, off
    raw = data[off + 2 : off + 2 + n]
    return raw.decode(CODEC, "replace"), off + 2 + n


def colorref_to_rgb(value: int) -> Optional[Tuple[int, int, int]]:
    """A stored colour (0xAARRGGBB) -> (r, g, b)."""
    if (value >> 24) & 0xFF == 0xFF:
        return None
    r = (value >> 16) & 0xFF
    g = (value >> 8) & 0xFF
    b = value & 0xFF
    return (r, g, b)


@dataclass
class PropertyList:
    """A length-prefixed key->value property list.

    Grammar: ``u16 count; count * (key:lpstr value:lpstr)``.
    """

    count: int
    pairs: List[Tuple[str, str]] = field(default_factory=list)
    start: int = 0
    end: int = 0

    def as_dict(self) -> dict:
        return dict(self.pairs)


def _parse_property_list(data: bytes, off: int) -> PropertyList:
    """Parse one property list at ``off``; robust to truncation."""
    start = off
    if off + 2 > len(data):
        return PropertyList(0, [], start, off)
    (count,) = struct.unpack_from("<H", data, off)
    off += 2
    pairs: List[Tuple[str, str]] = []
    for _ in range(count):
        key, off2 = _read_lpstr(data, off)
        if key is None:
            break
        val, off3 = _read_lpstr(data, off2)
        if val is None:
            break
        pairs.append((key, val))
        off = off3
    return PropertyList(count, pairs, start, off)


@dataclass
class AttributesPage:
    """Decoded ``attributes`` block (page metadata)."""

    # Fixed header
    outer_len: int
    flag: int
    width: int
    height: int
    page_identity: bytes  # 20 raw bytes (== page-block identity)
    ts_modified: int  # first dword of page_identity
    inner_len: int
    bg_color0: int  # raw COLORREF
    bg_color1: int  # raw COLORREF (== bg_color0)
    page_type_flag: int
    editor_view_w: int
    editor_view_h: int

    # Property lists
    list1: PropertyList = field(default_factory=lambda: PropertyList(0))
    list2: PropertyList = field(default_factory=lambda: PropertyList(0))

    # Bookkeeping / coverage
    block_size: int = 0
    data_end: int = 0  # off2: start of the 512-byte zero pad
    trailing_pad_zero: bool = False
    bytes_consumed: int = 0  # header + lists + pad we accounted for

    @property
    def background_rgb(self) -> Optional[Tuple[int, int, int]]:
        return colorref_to_rgb(self.bg_color0)

    @property
    def page_identity_hex(self) -> str:
        return self.page_identity.hex()

    def page_properties(self) -> dict:
        """Combined page-property dict (list1 then list2)."""
        merged = self.list1.as_dict()
        merged.update(self.list2.as_dict())
        return merged

    def associations(self) -> List[Tuple[str, str, str, str]]:
        """List 2 as Page Properties > Associations rows:
        ``(name, default, value_on_error, description)`` in stored order."""
        return [(k,) + split_association(v) for k, v in self.list2.pairs]

    @classmethod
    def parse(cls, block: Block) -> "AttributesPage":
        return parse_attributes(block)


# --------------------------------------------------------------------------- associations
# Property list 2 holds the page's (Super Genie) associations: key = the
# association NAME, value = "default\nvalue-on-error\ndescription" (always
# exactly two '\n'). In memory CtDraw keeps each row as 'ASSOC_<n>' =
# "name\ndefault\nvalue-on-error\ndescription"; splits that into
# the Associations tab's four columns (an empty field stays empty, each field
# at most 255 characters) and the save writes list 2 as key =
# name, value = "%s\n%s\n%s" of the other three. A library element keeps the
# same bag as its trailer record B (format/element.py).

#: longest association name the Associations tab accepts (AVEVA help 1172108)
ASSOCIATION_NAME_MAX = 253


def split_association(value: str) -> Tuple[str, str, str]:
    """A list-2 value -> ``(default, value_on_error, description)``."""
    parts = (value or "").split("\n", 2)
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def join_association(default: str = "", value_on_error: str = "", description: str = "") -> str:
    """``(default, value_on_error, description)`` -> the list-2 value."""
    return "\n".join((default or "", value_on_error or "", description or ""))


def association_name_error(name: str) -> Optional[str]:
    """Why ``name`` can't be an association name, or None."""
    if not name:
        return "Enter a name for the association."
    if len(name) > ASSOCIATION_NAME_MAX:
        return "An association name can be at most %d characters long (this one has %d)." % (
            ASSOCIATION_NAME_MAX,
            len(name),
        )
    first = name[0]
    if not (first.isascii() and (first.isalnum() or first == "_")):
        return "An association name must start with a letter, a digit or an underscore (_)."
    for ch in name:
        if ch.isspace():
            return "An association name cannot contain spaces."
        if ch == ".":
            return "An association name cannot contain a period (.)."
        if not (ch.isascii() and (ch.isalnum() or ch in "_\\")):
            return (
                "An association name can only contain letters, digits, "
                "underscores (_) and backslashes (\\); '%s' is not allowed." % ch
            )
    return None


def parse_attributes(block: Block) -> AttributesPage:
    """Parse an ``attributes`` Block into an :class:`AttributesPage`."""
    data = block.data
    if len(data) < HEAD_SIZE:
        raise ValueError("attributes block too small (%d bytes, need >= %d)" % (len(data), HEAD_SIZE))

    outer_len = struct.unpack_from("<I", data, 0x00)[0]
    flag = struct.unpack_from("<I", data, 0x04)[0]
    width = struct.unpack_from("<I", data, 0x14)[0]
    height = struct.unpack_from("<I", data, 0x18)[0]
    page_identity = bytes(data[0x20:0x34])
    ts_modified = struct.unpack_from("<I", data, 0x20)[0]
    inner_len = struct.unpack_from("<I", data, 0x34)[0]
    bg_color0 = struct.unpack_from("<I", data, 0x40)[0]
    bg_color1 = struct.unpack_from("<I", data, 0x44)[0]
    page_type_flag = struct.unpack_from("<I", data, 0x48)[0]
    editor_view_w = struct.unpack_from("<H", data, 0x4C)[0]
    editor_view_h = struct.unpack_from("<H", data, 0x4E)[0]

    list1 = _parse_property_list(data, HEAD_SIZE)
    list2 = _parse_property_list(data, list1.end)
    data_end = list2.end

    trailing = data[data_end:]
    trailing_pad_zero = trailing == b"\x00" * len(trailing)
    bytes_consumed = data_end + len(trailing)

    return AttributesPage(
        outer_len=outer_len,
        flag=flag,
        width=width,
        height=height,
        page_identity=page_identity,
        ts_modified=ts_modified,
        inner_len=inner_len,
        bg_color0=bg_color0,
        bg_color1=bg_color1,
        page_type_flag=page_type_flag,
        editor_view_w=editor_view_w,
        editor_view_h=editor_view_h,
        list1=list1,
        list2=list2,
        block_size=len(data),
        data_end=data_end,
        trailing_pad_zero=trailing_pad_zero,
        bytes_consumed=bytes_consumed,
    )
