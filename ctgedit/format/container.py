"""The .ctg / .ctF container format."""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import List
from .textcodec import CODEC

ENTRY_SIZE = 42
NAME_LEN = 32
HEADER_FIXED = 0x10  # directory entries start here


@dataclass
class Block:
    tag: int
    name: str
    offset: int
    size: int
    data: bytes = b""

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def is_deleted(self) -> bool:
        """A library element deleted in CtDraw keeps its
        data but clears the tag's low byte (0x0001 -> 0x0000, 0x0101 ->
        0x0100) and renames the entry "deleted" -- it 'can not be used' and
        stays in the file until Pack Libraries. Zero-size stubs count too."""
        return self.size == 0 or (self.name.lower() == "deleted" and (self.tag & 0xFF) == 0)


@dataclass
class Container:
    version: int
    word2: int
    ts_created: int
    ts_modified: int
    dword_c: int
    blocks: List[Block] = field(default_factory=list)
    # the whole file as parsed (b"" with ``keep_raw=False``)
    raw: bytes = b""
    # the word2 the file had when it was in an old form that format/upgrade.py brought to
    # the current one (0 = the file is current: the blocks are its own bytes)
    upgraded_from: int = 0

    @classmethod
    def parse(cls, data: bytes, keep_raw: bool = True) -> "Container":
        """``keep_raw=False`` does not keep the file bytes in :attr:`raw`: a
        process-cached library would otherwise hold its whole file twice (the
        bytes and a copy per block)."""
        version, word2, ts_c, ts_m, dword_c = struct.unpack_from("<HHIII", data, 0)
        c = cls(version, word2, ts_c, ts_m, dword_c, raw=data if keep_raw else b"")
        # Directory = fixed array of `dword_c` slots from HEADER_FIXED.
        capacity = dword_c if 0 < dword_c <= 1024 else 8
        entries = []
        for i in range(capacity):
            off = HEADER_FIXED + i * ENTRY_SIZE
            if off + ENTRY_SIZE > len(data):
                break
            (tag,) = struct.unpack_from("<H", data, off)
            name_raw = data[off + 2 : off + 2 + NAME_LEN]
            boff, bsize = struct.unpack_from("<II", data, off + 2 + NAME_LEN)
            if tag == 0 and boff == 0 and bsize == 0:
                continue  # unused slot
            name = name_raw.split(b"\x00", 1)[0].decode(CODEC, "replace")
            entries.append(Block(tag, name, boff, bsize))
        for blk in entries:
            blk.data = data[blk.offset : blk.offset + blk.size]
        c.blocks = entries
        return c

    def block(self, name: str) -> Block | None:
        for b in self.blocks:
            if b.name == name:
                return b
        return None

    # -- serialization -----------------------------------------------------
    def relayout(self) -> None:
        """Recompute block offsets/sizes from current data: directory then
        contiguous block data. Call after replacing a block's data."""
        capacity = self.dword_c if 0 < self.dword_c <= 1024 else max(8, len(self.blocks))
        off = HEADER_FIXED + capacity * ENTRY_SIZE
        for b in self.blocks:
            b.offset = off
            b.size = len(b.data)
            off += b.size

    def to_bytes(self) -> bytes:
        """Serialize back to .ctg bytes. Byte-exact when blocks are unmodified."""
        capacity = self.dword_c if 0 < self.dword_c <= 1024 else max(8, len(self.blocks))
        out = bytearray()
        out += struct.pack(
            "<HHIII", self.version, self.word2, self.ts_created, self.ts_modified, self.dword_c
        )
        for i in range(capacity):
            if i < len(self.blocks):
                b = self.blocks[i]
                name = b.name.encode(CODEC, "replace")[:NAME_LEN].ljust(NAME_LEN, b"\x00")
                out += struct.pack("<H", b.tag) + name + struct.pack("<II", b.offset, b.size)
            else:
                out += b"\x00" * ENTRY_SIZE
        # the blocks' bytes are joined once (a growing bytearray copies a big page twice)
        parts = [out]
        size = len(out)
        for b in sorted(self.blocks, key=lambda x: x.offset):
            if size < b.offset:
                parts.append(b"\x00" * (b.offset - size))
                size = b.offset
            parts.append(b.data)
            size += len(b.data)
        return b"".join(parts)
