"""Library elements (symbols, genies, super genies, templates) as editable documents."""

from __future__ import annotations

import copy
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple  # noqa: F401 (re-exported)

from .cnum import c_atoi
from ctgedit.format.container import Block, ENTRY_SIZE, HEADER_FIXED, NAME_LEN, directory_capacity
from ctgedit.format.page import (
    PAGE_HEAD_SIZE,
    LINK_ENTRY_SIZE,
    PageBlock,
    PageObject,
    parse_page,
    _parse_object,
)
from ctgedit.format import writer
from ctgedit.format.fileio import write_atomic
from ctgedit.format.textcodec import CODEC

PAD = 0x200
DELETED_NAME = b"deleted"
# the external properties appendix starts with these 31 bytes
# (CtDraw compares them up to the NUL)
EXT_FINGERPRINT = b"FingerPrint Ext. Prop. <@*%$!>\0"

# CtDraw's marker keys (the 12-hex suffix is part of the key)
KEY_BACKGROUND = "@<BackgroundColor>008029EBCE64"
KEY_KEYBOARDS = "@<PageWideKeyboards>04AEF973F534"
KEY_MANUAL_GENIE = "@<ManualGenie>389400077399"
KEY_ASSOCIATIONS = "@<PageAssociations>96CAB5718B47"
KEY_GENIE_VERSION = "@<GenieVersion>B0D0B3007C2E"
KEY_COMPOSITE_ID = "@<CompositeTemplateId>72530B39326F"
MARKER_KEYS = (
    KEY_BACKGROUND,
    KEY_KEYBOARDS,
    KEY_MANUAL_GENIE,
    KEY_ASSOCIATIONS,
    KEY_GENIE_VERSION,
    KEY_COMPOSITE_ID,
)

KIND_BY_EXT = {"ctl": "symbol", "ctm": "genie", "ctt": "template"}


def element_kind(lib_path: str, element: str) -> str:
    """'symbol' | 'genie' | 'supergenie' | 'template' (CtDraw opens an element
    whose name contains '@SUPER' in the super-genie editor)."""
    ext = os.path.splitext(lib_path)[1].lower().lstrip(".")
    kind = KIND_BY_EXT.get(ext, "symbol")
    if kind == "genie" and "@super" in element.lower():
        return "supergenie"
    return kind


# =========================================================================== container
@dataclass
class Slot:
    """One 42-byte directory entry, kept RAW so untouched entries re-emit
    byte-exactly (name bytes after the NUL are not always zero)."""

    index: int
    raw: bytes

    @property
    def tag(self) -> int:
        return struct.unpack_from("<H", self.raw, 0)[0]

    @property
    def name(self) -> str:
        return self.raw[2 : 2 + NAME_LEN].split(b"\0", 1)[0].decode(CODEC, "replace")

    @property
    def offset(self) -> int:
        return struct.unpack_from("<I", self.raw, 2 + NAME_LEN)[0]

    @property
    def size(self) -> int:
        return struct.unpack_from("<I", self.raw, 6 + NAME_LEN)[0]

    @property
    def in_use(self) -> bool:
        return bool(self.tag & 0xFF)

    @property
    def growable(self) -> bool:
        return bool(self.tag >> 8)

    @property
    def unused(self) -> bool:
        return self.raw == b"\0" * ENTRY_SIZE

    def set(self, tag=None, name=None, offset=None, size=None) -> None:
        tag = self.tag if tag is None else tag
        offset = self.offset if offset is None else offset
        size = self.size if size is None else size
        if name is None:
            nm = self.raw[2 : 2 + NAME_LEN]
        else:
            nb = name if isinstance(name, bytes) else name.encode(CODEC)
            if len(nb) >= NAME_LEN:
                raise ValueError("element names are limited to %d bytes" % (NAME_LEN - 1))
            nm = nb.ljust(NAME_LEN, b"\0")
        self.raw = struct.pack("<H", tag) + nm + struct.pack("<II", offset, size)


class LibraryFile:
    """A library container edited the way CtDraw edits it: the original file
    bytes are PATCHED (every untouched byte -- other elements, deleted
    blocks, orphaned gaps, stale slack, directory quirks -- is preserved)."""

    def __init__(self, data: bytes):
        self.buf = bytearray(data)
        self._dir_grown = False  # the directory grew; moves wait for close
        self._retiled = False  # the directory was retiled (before the first change)
        self._dirty = False  # the directory changed
        (self.version, self.word2, self.ts_created, self.ts_modified, self.dword_c) = struct.unpack_from(
            "<HHIII", data, 0
        )
        cap = directory_capacity(self.dword_c)
        self.slots = [
            Slot(i, bytes(data[HEADER_FIXED + i * ENTRY_SIZE : HEADER_FIXED + (i + 1) * ENTRY_SIZE]))
            for i in range(cap)
        ]
        # the external properties appendix (CtDraw reads it at the internal file size,
        # see _internal_size, when it starts with the fingerprint); None when there is none
        self._ext: Optional[bytes] = None
        try:
            isz = self._internal_size()
        except struct.error:
            isz = -1
        if 0 <= isz < len(data) and data[isz : isz + len(EXT_FINGERPRINT)] == EXT_FINGERPRINT:
            self._ext = bytes(data[isz:])

    @classmethod
    def new(cls, version: int = 9, word2: int = 0x0C, ts: int = 0, slots: int = 8) -> "LibraryFile":
        """An empty library container (as CtDraw creates one:
        version 9, 8 directory slots, both stamps = now; the
        first slot carries the 'last block' byte, tag 0x0100 size 0)."""
        return cls(
            struct.pack("<HHIII", version, word2, ts, ts, slots)
            + struct.pack("<H", 0x0100)
            + b"\0" * (ENTRY_SIZE * slots - 2)
        )

    @classmethod
    def open(cls, path: str) -> "LibraryFile":
        with open(path, "rb") as fh:
            return cls(fh.read())

    # -- lookup ----------------------------------------------------------------
    @staticmethod
    def _fold(name: str) -> str:
        """CtDraw compares element names without ASCII case (other letters as they are)."""
        return "".join(c.lower() if c.isascii() else c for c in name)

    def find(self, name: str) -> Optional[Slot]:
        """CtDraw finds the first in-use entry whose name matches
        case-insensitively (deleted / free entries are never found)."""
        low = self._fold(name)
        for s in self.slots:
            if s.in_use and s.size and self._fold(s.name) == low:
                return s
        return None

    def element_names(self) -> List[str]:
        return [
            s.name
            for s in self.slots
            if s.in_use and s.size and s.name and not (s.name.lower() == "deleted" and not (s.tag & 0xFF))
        ]

    def block_data(self, s: Slot) -> bytes:
        return bytes(self.buf[s.offset : self._extent_end(s)])

    def _extent_end(self, s: Slot) -> int:
        """Where a block's bytes end."""
        end = s.offset + s.size
        if not (s.in_use and s.offset and len(self.buf) > end and self.word2 >= 0x0C):
            return end
        try:
            objdata, _n, thumb = struct.unpack_from("<III", self.buf, s.offset)
            links = struct.unpack_from("<I", self.buf, s.offset + 0x1C)[0]
        except struct.error:
            return end
        need = 0x34 + links * 0x60 + objdata + thumb
        if s.size < need and s.offset + need <= len(self.buf):
            return s.offset + need
        return end

    def block(self, name: str) -> Optional[Block]:
        s = self.find(name)
        if s is None:
            return None
        return Block(s.tag, s.name, s.offset, s.size, self.block_data(s))

    def _blocks_end(self) -> int:
        """End of the physically last block (what follows is a stale tail or the
        external properties appendix)."""
        ends = [self._extent_end(s) for s in self.slots if s.size and s.offset]
        return max(ends) if ends else HEADER_FIXED + len(self.slots) * ENTRY_SIZE

    def _content_end(self, s: Slot) -> int:
        """the end of s's content by its head (0x34 + links x 0x60 +
        records + thumbnail; a 0x24 head before word2 0x0C has no thumbnail)."""
        if self.word2 >= 0x0C:
            objdata, _n, thumb = struct.unpack_from("<III", self.buf, s.offset)
            links = struct.unpack_from("<I", self.buf, s.offset + 0x1C)[0]
            return s.offset + 0x34 + links * LINK_ENTRY_SIZE + objdata + thumb
        objdata = struct.unpack_from("<I", self.buf, s.offset)[0]
        links = struct.unpack_from("<I", self.buf, s.offset + 0x0C)[0]
        return s.offset + 0x24 + links * LINK_ENTRY_SIZE + objdata

    def _internal_size(self) -> int:
        """The internal file size: the content end of the first entry in
        directory order with the 'last block' byte and a name, else
        the directory's end. The appendix starts there."""
        for s in self.slots:
            if s.raw[1] and s.raw[2] and s.offset:
                return self._content_end(s)
        return HEADER_FIXED + len(self.slots) * ENTRY_SIZE

    @property
    def appendix(self) -> bytes:
        """The external properties appendix as read (fingerprint + records), or b""."""
        return self._ext or b""

    def _retile(self) -> None:
        """Fix up the directory the way CtDraw's load does."""
        if self._retiled:
            return
        self._retiled = True
        dir_end = HEADER_FIXED + len(self.slots) * ENTRY_SIZE
        before = [s.raw for s in self.slots]
        for s in self.slots:
            if (not s.offset and s.size) or (s.offset and s.offset < dir_end):
                s.raw = b"\0" * ENTRY_SIZE
        order = sorted(self.slots, key=lambda s: s.offset)
        for a, b in zip(order, order[1:]):
            if a.offset and b.offset != a.offset + a.size:
                a.set(size=b.offset - a.offset)
        for a in order[:-1]:
            if a.raw[0] and a.raw[1]:
                a.set(tag=a.tag & 0x00FF)
        last = order[-1] if order else None
        if last is not None and last.raw[0] and not last.raw[1]:
            last.set(tag=(last.tag & 0x00FF) | 0x0100)
        if [s.raw for s in self.slots] != before:
            self._dirty = True

    # -- write -----------------------------------------------------------------
    def write_element(self, name: str, content: bytes) -> Slot:
        """Store ``content`` (head .. thumbnail, NO trailing pad) as element
        ``name`` following CtDraw's allocation rules (module docstring).
        Returns the slot now holding it."""
        if self.word2 < 0x0C:
            raise ValueError(
                "The library is in an old file form that this editor reads "
                "only. Save it once in the Graphics Builder first."
            )
        self._retile()
        needed = len(content)
        s = self.find(name)
        if s is not None:
            last = self._extent_end(s) >= self._blocks_end()
            if needed <= s.size or (s.growable and last):
                # CtDraw writes it in place; the growable last block may run past its size (the
                # file then ends at the content when it grew, the size is kept)
                self._put(s.offset, content)
                return s
            # no room: CtDraw marks it deleted (offset / size kept)
            s.set(tag=0x0000, name=DELETED_NAME)
        for t in self.slots:
            if (t.tag & 0xFF) == 0 and t.size and needed <= t.size and t.offset:
                self._put(t.offset, content)
                t.set(tag=(t.tag & 0xFF00) | 0x01, name=name)
                self._dirty = True
                return t
            if (t.tag & 0xFF) == 0 and t.size == 0:
                return self._append(t, name, content)
        return self._append(self._grow_directory(), name, content)

    def delete_element(self, name: str) -> Optional[Slot]:
        """Delete a directory entry, as CtDraw deletes an element."""
        self._retile()
        s = self.find(name)
        if s is None:
            return None
        s.set(tag=s.tag & 0xFF00, name=DELETED_NAME)
        self._dirty = True
        return s

    def rename_element(self, old: str, new: str) -> Slot:
        """Rename an element's directory entry."""
        self._retile()
        s = self.find(old)
        if s is None:
            raise KeyError("the library has no element %r" % old)
        other = self.find(new)
        if other is not None and other is not s:
            raise ValueError("an element named %r already exists" % new)
        s.set(name=new)
        self._dirty = True
        return s

    def _put(self, offset: int, content: bytes) -> None:
        """Write ``content`` at ``offset``, as CtDraw rewrites a block."""
        end = offset + len(content)
        if end > len(self.buf):
            self.buf += b"\0" * (end - len(self.buf))
        self.buf[offset:end] = content

    def _append(self, t: Slot, name: str, content: bytes) -> Slot:
        """A fresh slot: every entry loses the 'last block' byte, the new
        one is 0x0101 at the PHYSICAL end of the file (past a stale tail or the
        appendix), size = content + 0x200, zero filled."""
        size = len(content) + PAD
        end = len(self.buf)
        # the block that was last and grew in place past its directory size: its
        # size becomes the gap to the next block (what CtDraw's next load gives it) BEFORE
        # its growable flag goes, as a reader takes an unflagged block's bytes from the
        # directory
        offsets = sorted(o.offset for o in self.slots if o.offset) + [end]
        for o in self.slots:  # only the new block is last
            if o.raw != b"\0" * ENTRY_SIZE and o.tag >> 8:
                if o.in_use and o.size and o.offset and self._extent_end(o) > o.offset + o.size:
                    nxt = next(x for x in offsets if x > o.offset)
                    o.set(size=nxt - o.offset)
                o.set(tag=o.tag & 0x00FF)
        t.set(tag=0x0101, name=name, offset=end, size=size)
        self.buf += content + b"\0" * PAD
        self._dirty = True
        return t

    def _grow_directory(self) -> Slot:
        """Grow the directory by 5 slots, within CtDraw's chunk sizes."""
        n_old = len(self.slots)
        rem = n_old % 0x61
        n_new = n_old - rem + min(rem + 5, 0x61)
        self.slots += [Slot(i, b"\0" * ENTRY_SIZE) for i in range(n_old, n_new)]
        self.dword_c = len(self.slots)
        self._dir_grown = True
        return self.slots[n_old]

    def _page_to_end(self, s: Slot) -> int:
        """Copy the block to the end of the file, as CtDraw moves a block."""
        old, size = s.offset, s.size
        end = len(self.buf)  # the end of the file, as an append
        src, left = old, size + PAD
        while left > 0:  # in 0x1000-byte chunks
            n = min(0x1000, left)
            chunk = bytes(self.buf[src : src + n])
            if len(chunk) != n:
                raise ValueError(
                    "the library block at %d cannot be moved (it runs past the end of the file)" % old
                )
            self.buf += chunk
            src += n
            left -= n
        for o in self.slots:
            if o.tag >> 8:
                o.set(tag=o.tag & 0x00FF)
        s.set(tag=(s.tag & 0x00FF) | 0x0100, offset=end, size=size + PAD)
        return (old + size - HEADER_FIXED) // ENTRY_SIZE

    def _relocate_for_directory(self) -> None:
        """(at close, as CtDraw does) after the directory grew: while the
        lowest live block starts inside the directory, move it to the end
        (:meth:`_page_to_end`); then zero every deleted entry whose block
        starts before the last computed slot capacity."""
        n = len(self.slots)

        def lowest(live):
            c = [s for s in self.slots if s.size and bool(s.tag & 0xFF) == live]
            return min(c, key=lambda s: s.offset) if c else None

        first = lowest(True)
        if first is None:
            raise ValueError("a library directory grew without a live element")
        cap = (first.offset - HEADER_FIXED) // ENTRY_SIZE
        while cap < n:
            cap = self._page_to_end(lowest(True))
        while True:
            d = lowest(False)
            if d is None or HEADER_FIXED + cap * ENTRY_SIZE <= d.offset:
                break
            d.raw = b"\0" * ENTRY_SIZE
        dir_end = HEADER_FIXED + n * ENTRY_SIZE
        if len(self.buf) < dir_end:
            self.buf += b"\0" * (dir_end - len(self.buf))
        self._dir_grown = False

    def to_bytes(self) -> bytes:
        if self._dir_grown:
            self._relocate_for_directory()
        buf = self.buf
        dir_end = HEADER_FIXED + len(self.slots) * ENTRY_SIZE
        n_slots = len(self.slots)
        if len(buf) >= dir_end and all(
            len(s.raw) == ENTRY_SIZE and 0 <= s.index < n_slots for s in self.slots
        ):
            # the header and the directory patched in a copy of their own, the rest of the
            # file joined to it once (one pass over a large library)
            head = bytearray(buf[:dir_end])
            struct.pack_into(
                "<HHIII", head, 0, self.version, self.word2, self.ts_created, self.ts_modified, self.dword_c
            )
            for s in self.slots:
                o = HEADER_FIXED + s.index * ENTRY_SIZE
                head[o : o + ENTRY_SIZE] = s.raw
            end, ext = len(buf), b""
            if self._ext is not None and self._dirty:
                # (as below)
                try:
                    cut = self._internal_size()
                    live = [self._content_end(s) for s in self.slots if s.in_use and s.size and s.offset]
                except struct.error:
                    cut = -1
                if cut >= 0:
                    end = min(end, max([cut, dir_end] + live))
                    ext = self._ext
            with memoryview(buf) as mv:
                return b"".join((head, mv[dir_end:end], ext))
        out = bytearray(buf)
        struct.pack_into(
            "<HHIII", out, 0, self.version, self.word2, self.ts_created, self.ts_modified, self.dword_c
        )
        for s in self.slots:
            o = HEADER_FIXED + s.index * ENTRY_SIZE
            out[o : o + ENTRY_SIZE] = s.raw
        if self._ext is not None and self._dirty:
            # a close with a changed directory: the file is cut at the internal file size
            # and the appendix written there again (the old copy of an appendix an append
            # went past stays behind as stale bytes). Never cut into a live block's content.
            try:
                cut = self._internal_size()
                live = [self._content_end(s) for s in self.slots if s.in_use and s.size and s.offset]
            except struct.error:
                cut = -1
            if cut >= 0:
                cut = max([cut, HEADER_FIXED + len(self.slots) * ENTRY_SIZE] + live)
                del out[cut:]
                out += self._ext
        return bytes(out)


# =========================================================================== trailer
def _read_bag(p: bytes, o: int = 0) -> Tuple[List[Tuple[str, str]], int]:
    (n,) = struct.unpack_from("<H", p, o)
    o += 2
    pairs = []
    for _ in range(n):
        (kl,) = struct.unpack_from("<H", p, o)
        k = p[o + 2 : o + 2 + kl].decode(CODEC)
        o += 2 + kl
        (vl,) = struct.unpack_from("<H", p, o)
        v = p[o + 2 : o + 2 + vl].decode(CODEC)
        o += 2 + vl
        pairs.append((k, v))
    return pairs, o


def _bag_bytes(pairs: Sequence[Tuple[str, str]]) -> bytes:
    out = bytearray(struct.pack("<H", len(pairs)))
    for k, v in pairs:
        kb, vb = k.encode(CODEC), v.encode(CODEC)
        out += struct.pack("<H", len(kb)) + kb + struct.pack("<H", len(vb)) + vb
    return bytes(out)


@dataclass
class ElementTrailer:
    """The records after the element's root tree (see module docstring)."""

    props: List[Tuple[str, str]]  # record A, in order
    associations: Optional[List[Tuple[str, str]]]  # record B
    keyboards: Optional[List[List[Tuple[str, str]]]]  # record C
    extra: List[bytes] = field(default_factory=list)  # 16-byte GUID records, unknown
    raw: List[bytes] = field(default_factory=list)  # original payloads

    @classmethod
    def parse(cls, payloads: Sequence[bytes]) -> "ElementTrailer":
        if not payloads:
            return cls([], None, None, [], [])
        props, _ = _read_bag(payloads[0])
        d = dict(props)
        rest = list(payloads[1:])
        assoc = keys = None
        if c_atoi(d.get(KEY_ASSOCIATIONS)) and rest:  # load order: B then C
            assoc, _ = _read_bag(rest.pop(0))
        if c_atoi(d.get(KEY_KEYBOARDS)) and rest:
            p = rest.pop(0)
            (n,) = struct.unpack_from("<H", p, 0)
            o, keys = 2, []
            for _ in range(n):
                bag, o = _read_bag(p, o)
                keys.append(bag)
        return cls(props, assoc, keys, rest, list(payloads))

    # --- decoded views (what the Page Properties dialog edits) --------------
    def page_properties(self) -> List[Tuple[str, str]]:
        return [(k, v) for k, v in self.props if k not in MARKER_KEYS]

    def background(self) -> Optional[Tuple[int, int]]:
        v = dict(self.props).get(KEY_BACKGROUND)
        if not v:
            return None
        try:
            a, b = (int(x, 16) for x in v.split(","))
            return a, b
        except ValueError:
            return None

    def with_changes(self, *, page_properties=None, associations=None, background="keep") -> "ElementTrailer":
        """A copy with edited properties; marker keys are kept in place (the
        background key is added / removed as CtDraw does)."""
        props = list(self.props)
        if page_properties is not None:
            markers = [(k, v) for k, v in props if k in MARKER_KEYS]
            props = list(page_properties) + markers
        if background != "keep":
            props = [(k, v) for k, v in props if k != KEY_BACKGROUND]
            if background is not None and not (
                (background[0] >> 24) == 0xFF and (background[1] >> 24) == 0xFF
            ):
                i = next((j for j, (k, _v) in enumerate(props) if k in MARKER_KEYS), len(props))
                props.insert(i, (KEY_BACKGROUND, "0x%08x,0x%08x" % tuple(background)))
        out = ElementTrailer(
            props,
            self.associations if associations is None else three_field(associations),
            self.keyboards,
            list(self.extra),
            list(self.raw),
        )
        if associations is not None and not any(k == KEY_ASSOCIATIONS for k, _ in props):
            out.props.append((KEY_ASSOCIATIONS, "1"))
        return out

    def payloads(self) -> List[bytes]:
        """Serialise; the ORIGINAL bytes when nothing decoded changed."""
        if self.raw and ElementTrailer.parse(self.raw) == ElementTrailer(
            self.props, self.associations, self.keyboards, self.extra, self.raw
        ):
            return list(self.raw)
        out = [_bag_bytes(self.props)]
        d = dict(self.props)
        if c_atoi(d.get(KEY_ASSOCIATIONS)):
            out.append(_bag_bytes(self.associations or []))
        if c_atoi(d.get(KEY_KEYBOARDS)):
            kb = bytearray(struct.pack("<H", len(self.keyboards or [])))
            for bag in self.keyboards or []:
                kb += _bag_bytes(bag)
            out.append(bytes(kb))
        return out + list(self.extra)

    def __eq__(self, other):  # raw bytes are provenance, not content
        return (
            isinstance(other, ElementTrailer)
            and self.props == other.props
            and self.associations == other.associations
            and self.keyboards == other.keyboards
            and self.extra == other.extra
        )


def trailer_record(payload: bytes) -> PageObject:
    """A trailer payload as a PageObject that object_payload() re-emits
    verbatim (parse via the unframed-record path)."""
    po = _parse_object(0, 0, len(payload), 0, payload)
    if po.raw_payload is None:  # a >=12-byte bag would 'frame': force raw
        po.raw_payload = bytes(payload)
    return po


# =========================================================================== element
def root_tree_end(objs: Sequence[PageObject], start: int = 0) -> int:
    """Index just past the record tree rooted at ``start`` (child counts)."""

    def end(i):
        j = i + 1
        for _ in range(max(0, objs[i].class_flag)):
            if j >= len(objs):
                break
            j = end(j)
        return j

    return end(start) if start < len(objs) else start


@dataclass
class ParsedElement:
    lib_path: str
    name: str  # element (block) name, original case
    kind: str  # symbol | genie | supergenie | template
    library: LibraryFile
    word2: int
    block: Block  # raw block as stored (extent incl. slack)
    page: PageBlock  # ALL records (tree + trailer)
    root_end: int  # records [0:root_end] = the one root tree
    trailer: ElementTrailer
    slack: bytes  # block bytes after the thumbnail

    @property
    def origin(self) -> Tuple[int, int]:
        return struct.unpack_from("<2i", self.block.data, 0x0C)

    @property
    def extent(self) -> Tuple[int, int]:
        return self.page.page_width, self.page.page_height

    @property
    def tree(self) -> List[PageObject]:
        return self.page.objects[: self.root_end]

    @property
    def ext(self) -> str:
        return os.path.splitext(self.lib_path)[1].lower().lstrip(".")


def open_element(lib_path: str, element: str, library: Optional[LibraryFile] = None) -> ParsedElement:
    """Element ``element`` of the library file ``lib_path``; ``library`` = that file's
    bytes as a LibraryFile already in memory (an element composed but not stored: the
    file is not read)."""
    lib = LibraryFile.open(lib_path) if library is None else library
    s = lib.find(element)
    if s is None:
        raise KeyError("%s has no element %r" % (os.path.basename(lib_path), element))
    blk = Block(s.tag, s.name, s.offset, s.size, lib.block_data(s))
    # (lazily: an element window reads these records for their bodies and serialises them
    # into the page it edits, whose own parse it derives: page.LazyPageObject)
    pb = parse_page(blk, lib.word2, lazy=True)
    if pb.objdata_consumed != pb.objdata_size:
        raise ValueError("element %r: record walk does not tile" % element)
    root_end = root_tree_end(pb.objects, 0)
    trailer = ElementTrailer.parse([writer.object_payload(o) for o in pb.objects[root_end:]])
    need = pb.data_start + pb.objdata_size + pb.thumb_size
    return ParsedElement(
        lib_path,
        s.name,
        element_kind(lib_path, s.name),
        lib,
        lib.word2,
        blk,
        pb,
        root_end,
        trailer,
        bytes(blk.data[need:]),
    )


def element_content(
    pe: ParsedElement, objects: Optional[List[PageObject]] = None, trailer: Optional[ElementTrailer] = None
) -> bytes:
    """The element block CONTENT (head .. thumbnail, no pad) for the tree
    ``objects`` (default: the parsed one) + the trailer."""
    pb = copy.copy(pe.page)
    tree = pe.tree if objects is None else list(objects)
    tr = pe.trailer if trailer is None else trailer
    pb.objects = tree + [trailer_record(p) for p in tr.payloads()]
    return writer.serialize_page_block(pb, pe.block.data)[: -writer.PAGE_TRAILING_PAD]


def serialize_element(
    pe: ParsedElement, content: Optional[bytes] = None, name: Optional[str] = None
) -> bytes:
    """The library FILE bytes with the element written back (CtDraw's rules).
    Unmodified content -> the original file, byte for byte. ``name`` saves
    under another element name (Save As; <= 31 bytes, CtDraw keeps 31)."""
    lib = LibraryFile(pe.library.to_bytes())
    content = element_content(pe) if content is None else content
    name = name or pe.name
    s = lib.find(name)
    if s is not None and content == lib.block_data(s)[: len(content)]:
        return lib.to_bytes()  # nothing changed
    lib.write_element(name, content)
    return lib.to_bytes()


def save_element(
    pe: ParsedElement, content: bytes, path: Optional[str] = None, name: Optional[str] = None
) -> str:
    """Write the element into its library atomically."""
    return store_element(
        path or pe.lib_path, name or pe.name, content, version=pe.library.version, word2=pe.library.word2
    )


def ctdraw_timestamp() -> int:
    """A save stamp in CtDraw's epoch: seconds since 1899-12-30 in LOCAL time
    (the container ts_modified / element identity stamp CtDraw writes; any
    increasing value works as the staleness key other pages quote; infra/identity:
    CTG_FIXED_IDENTITY fixes it)."""
    from ..infra import identity

    return identity.now_stamp()


def refresh_identity(content: bytes, stamp: Optional[int] = None) -> bytes:
    """``content`` with a fresh page identity (u32 save stamp + a new GUID), as
    CtDraw writes on every save of an element (CoCreateGuid; infra/identity)."""
    from ..infra import identity

    stamp = ctdraw_timestamp() if stamp is None else stamp
    out = bytearray(content)
    out[0x20:0x34] = struct.pack("<I", stamp & 0xFFFFFFFF) + identity.guid_bytes()
    return bytes(out)


def stored_content_equal(target: str, name: str, content: bytes, *, ignore_identity: bool = False) -> bool:
    """True when ``target`` already stores ``content`` as element ``name``."""
    try:
        lib = LibraryFile.open(target)
    except OSError:
        return False
    s = lib.find(name)
    if s is None:
        return False
    cur = lib.block_data(s)[: len(content)]
    if ignore_identity and len(cur) == len(content) >= 0x34:
        return cur[:0x20] == content[:0x20] and cur[0x34:] == content[0x34:]
    return cur == content


class ElementChangedMeanwhile(ValueError):
    """The library no longer stores the element block a caller read (another process or
    tab saved the element since): :func:`store_element` with ``expect`` writes nothing."""


def stamp_after(content: bytes, stored: Optional[bytes]) -> bytes:
    """``content`` with a save stamp newer than ``stored``'s."""
    if stored is None or len(stored) < 0x24 or len(content) < 0x24:
        return content
    old = struct.unpack_from("<I", stored, 0x20)[0]
    new = struct.unpack_from("<I", content, 0x20)[0]
    if new > old or old >= 0xFFFFFFFF:
        return content
    out = bytearray(content)
    struct.pack_into("<I", out, 0x20, old + 1)
    return bytes(out)


def store_element(
    target: str,
    name: str,
    content: bytes,
    *,
    version: int = 9,
    word2: int = 0x0C,
    stamp: Optional[int] = None,
    expect: Optional[bytes] = None,
    newer: bool = False,
) -> str:
    """Write an element into a library by CtDraw's allocation rules, atomically."""
    from ..infra import identity
    from . import projectlock

    with projectlock.file(target):
        if os.path.exists(target):
            lib = LibraryFile.open(target)
        else:
            ts = ctdraw_timestamp() if stamp is None else stamp
            lib = LibraryFile.new(version, word2, ts)
        s = lib.find(name)
        if s is not None and lib.block_data(s)[: len(content)] == content:
            return target  # nothing to write
        if expect is not None and (s is None or lib.block_data(s) != bytes(expect)):
            raise ElementChangedMeanwhile(
                "%s changed in %s since it was read" % (name, os.path.basename(target))
            )
        if newer and s is not None and not identity.fixed():
            content = stamp_after(content, bytes(lib.block_data(s)[:0x24]))
            got = struct.unpack_from("<I", content, 0x20)[0]
            if stamp is not None and got > (stamp & 0xFFFFFFFF):
                stamp = got
        lib.write_element(name, content)
        if stamp is not None:
            lib.ts_modified = stamp & 0xFFFFFFFF
        _write_library(target, lib)
    return target


def _write_library(target: str, lib: LibraryFile) -> None:
    data = lib.to_bytes()
    write_atomic(target, data)


ELEMENT_NAME_LIMIT = NAME_LEN - 1  # CtDraw keeps 31 bytes of a name


def check_element_name(name: str) -> str:
    """'' when ``name`` can be a library element name, else what is wrong."""
    if not name or not name.strip():
        return "Enter an element name"
    if name != name.strip():
        return "An element name cannot start or end with a space"
    if "." in name:
        return "An element name cannot contain a dot (it separates library and element)"
    try:
        enc = name.encode(CODEC, "strict")
    except UnicodeEncodeError:
        return "The name holds characters a Citect library cannot store"
    if len(enc) > ELEMENT_NAME_LIMIT:
        return "Element names are limited to %d characters" % ELEMENT_NAME_LIMIT
    return ""


#: CtDraw's New Library dialog refuses these names (without case):
#: DOS device names
RESERVED_LIBRARY_NAMES = ("LPT1", "LPT2", "LPT3", "COM1", "COM2", "COM3", "COM4", "CON", "AUX", "PRN", "NUL")
LIBRARY_NAME_LIMIT = 64  # the Name field takes 64 characters


def check_library_name(name: str) -> str:
    """'' if ``name`` can be a new library, else what's wrong with it."""
    if not name or not name.strip():
        return "Enter a library name"
    if len(name) > LIBRARY_NAME_LIMIT:
        return "Library names are limited to %d characters" % LIBRARY_NAME_LIMIT
    try:
        name.encode(CODEC, "strict")
    except UnicodeEncodeError:
        return "The name holds characters a Citect project cannot store"
    bad = sorted({ch for ch in name if not (ch.isalnum() or ch in "_!")})
    if bad:
        return "A library name may only hold letters, digits, '_' and '!' (not %s)" % " ".join(
            "'%s'" % ch for ch in bad
        )
    if name.upper() in RESERVED_LIBRARY_NAMES:
        return "%s is a reserved device name: choose another name" % name
    return ""


def delete_library_element(lib_path: str, element: str) -> bool:
    """Delete an element from a library, as CtDraw's Open dialog does."""
    lib = LibraryFile.open(lib_path)
    if lib.delete_element(element) is None:
        return False
    # CtDraw's close writes the changed directory back with a new container
    # ts_modified (header now)
    lib.ts_modified = ctdraw_timestamp()
    _write_library(lib_path, lib)
    return True


def rename_library_element(lib_path: str, old: str, new: str) -> str:
    """Rename element ``old`` of ``lib_path`` to ``new`` (atomic; see
    :meth:`LibraryFile.rename_element`). A super genie keeps its '@SUPER'
    suffix. Returns the name written."""
    if "@super" in old.lower() and "@super" not in new.lower():
        new += "@SUPER"
    err = check_element_name(new)
    if err:
        raise ValueError(err)
    lib = LibraryFile.open(lib_path)
    lib.rename_element(old, new)
    lib.ts_modified = ctdraw_timestamp()
    _write_library(lib_path, lib)
    return new


# =========================================================================== new elements
def default_trailer(page_properties=None, associations=None, background=None) -> ElementTrailer:
    """The trailer CtDraw writes for a new element."""
    props = list(page_properties or [])
    if background is not None and not ((background[0] >> 24) == 0xFF and (background[1] >> 24) == 0xFF):
        props.append((KEY_BACKGROUND, "0x%08x,0x%08x" % tuple(background)))
    props += [(KEY_KEYBOARDS, "1"), (KEY_ASSOCIATIONS, "1")]
    return ElementTrailer(props, three_field(associations), [], [], [])


def three_field(associations) -> List[Tuple[str, str]]:
    """Associations (record B) in the form CtDraw saves them: 'default\\nvalue-on-error
    \\ndescription' (LIVE, the fixture oracle)."""
    from .attributes import join_association, split_association

    return [(k, join_association(*split_association(v))) for k, v in (associations or [])]


def keeps_bare_root(po: PageObject) -> bool:
    """Is ``po`` kept as the bare root of a .ctl element, without a group around it?"""
    tc, linked = po.type_code, bool(po.subtype_size & 0x1)
    if tc in (0x1002, 0x1003):
        return True
    if tc == 0x07:
        return not po.class_flag
    return tc == 0x08 and po.class_flag > 0 and not linked


def root_group(
    recs: Sequence[PageObject], roots: Sequence[int], origin, handle: Optional[int] = None
) -> PageObject:
    """The synthesized 0x08 element root over the subtrees ``roots`` of ``recs`` (the
    form of every shipped root, 8.2): rect1 / rect2 = the union of theirs (input
    records left out), anchor (l, b - 1), the lowest handle no record uses, slots
    (I, T(-e), I)."""
    from ctgedit.format import factory, placement
    from ctgedit.format import geometry as G

    r1s, r2s = [], []
    for k in roots:
        gh = G.parse_geom_header(recs[k].body_raw)
        if gh is not None and recs[k].type_code not in G.INPUT_TYPE_CODES:
            r1s.append(G.norm_rect(gh.rect1))
            r2s.append(tuple(gh.rect2))
    u1 = (min(r[0] for r in r1s), min(r[1] for r in r1s), max(r[2] for r in r1s), max(r[3] for r in r1s))
    u2 = (min(r[0] for r in r2s), min(r[1] for r in r2s), max(r[2] for r in r2s), max(r[3] for r in r2s))
    if handle is None:
        used = {G.get_handle(o) for o in recs}
        handle = next(h for h in range(G.MAX_HANDLE) if h not in used)
    ex, ey = origin
    t_e = (1, (1.0, 0.0, 0.0, 1.0, float(-ex), float(-ey))) if (ex or ey) else (0, placement.IDENT)
    root = factory.make_group_record(handle, u1, len(roots), bounds=u2)
    placement.write_slots(root, [(0, placement.IDENT), t_e, (0, placement.IDENT)])
    writer.refresh_object_bags(root)
    return root


def to_member_form(po: PageObject, origin) -> None:
    """A former single root (element form: slot 2 = Local o T(-e), slot 3 = I) as a
    member of a new root group: slot 2 = slot 3 = Local, the root's T(-e) dropped
    (CtDraw's wrapped fx_box: (I, I, I) under (I, T(-e), I)). A linked record keeps its
    slots: they derive from its own element."""
    from ctgedit.format import placement

    if po.subtype_size & 0x1:
        return
    if _bake_marker(po):
        return
    (m1, L, _o, _n), (m2, S2, _o2, _n2), _s3 = placement.read_slots(po.body_raw)[0]
    s2, ms2 = placement.slot2_for_linked(L, m1, tuple(origin))
    if m2 == ms2 and all(abs(a - b) < 1e-9 for a, b in zip(S2, s2)):
        placement.write_slots(po, [(m1, L)] * 3)
        writer.refresh_object_bags(po)


def _bake_marker(po: PageObject) -> bool:
    """Bake a marker's transform into its class rect, as CtDraw's save does."""
    import struct
    from ctgedit.format import placement
    from ctgedit.format import genie_place as GP

    if po.type_code != 0xFFFF or not po.subtype_size & 0x2 or po.raw_payload is not None:
        return False
    slots = placement.read_slots(po.body_raw)[0]
    if all(s[0] == 0 for s in slots):
        return False
    r = GP.decode(po)
    if r.cls is None or len(r.cls) < 12:
        return False
    if slots[0][0]:
        T = slots[0][1]
    else:
        T = slots[2][1] if slots[2][0] else placement.IDENT
    a, b, c, d, h, k = T
    l0, t0, r0, b0, px, py = struct.unpack_from("<4h2h", r.cls, len(r.cls) - 12)
    xs, ys = [], []
    for x, y in ((l0, t0), (r0, t0), (l0, b0), (r0, b0)):
        xs.append(int(a * x + c * y + h))
        ys.append(int(b * x + d * y + k))
    box = (min(xs), min(ys), max(xs), max(ys))
    r.cls = r.cls[:-12] + struct.pack("<4h2h", *box, px, py)
    r.anchor, r.rect1, r.rect2 = (px, py), box, box
    r.slots = [(0, placement.IDENT)] * 3
    out = GP.to_po(GP.encode(r))
    po.body_raw = out.body_raw
    return True


def wrapped_tree(tree: Sequence[PageObject], origin) -> List[PageObject]:
    """The element tree ``tree`` (one root, element form) as CtDraw's .ctl save holds
    it: a root that :func:`keeps_bare_root` refuses goes under a new group root
    (:func:`root_group`, the member in :func:`to_member_form`, a copy of the record);
    else ``tree`` as is. The records given are not changed."""
    from ctgedit.format import geometry as G

    tree = list(tree)
    if (
        not tree
        or keeps_bare_root(tree[0])
        or tree[0].type_code in G.INPUT_TYPE_CODES
        or G.parse_geom_header(tree[0].body_raw) is None
        or root_tree_end(tree, 0) != len(tree)
    ):
        return tree
    blob = writer.object_payload(tree[0])
    member = _parse_object(0, 0, len(blob), 0, blob)
    # (the member first: a marker's baked box is the root's, _bake_marker)
    to_member_form(member, origin)
    root = root_group([member] + list(tree[1:]), [0], origin)
    return [root, member] + tree[1:]


def new_element_content(
    records: List[PageObject],
    *,
    origin=(0, 0),
    page_size=None,
    trailer: Optional[ElementTrailer] = None,
    thumbnail: bytes = b"",
    word2: int = 0x0C,
    handle: Optional[int] = None,
    resolver=None,
    ext: str = "ctl",
    normal_form: bool = True,
    carried=(),
    lib_path: str = "",
) -> bytes:
    """Content of a new element holding ``records``."""
    from ctgedit.format import placement
    from ctgedit.infra import identity
    from ctgedit.format import geometry as G

    recs = list(records)
    if not recs:
        raise ValueError("a library element needs at least one object")
    roots = []
    i = 0
    while i < len(recs):
        roots.append(i)
        i = root_tree_end(recs, i)
    ex, ey = origin
    boxed = [
        k
        for k in roots
        if recs[k].type_code not in G.INPUT_TYPE_CODES and G.parse_geom_header(recs[k].body_raw) is not None
    ]
    if len(roots) == 1 and (
        not boxed or ((ext or "").lower() not in ("ctm", "ctt") and keeps_bare_root(recs[0]))
    ):
        tree = recs
        root = tree[0]
        (m1, L, _o, _n), _s2, _s3 = placement.read_slots(root.body_raw)[0]
        if ex or ey:  # single root: slot2 = Local o T(-e), slot3 = I
            s2, m2 = placement.slot2_for_linked(L, m1, (ex, ey))
            placement.write_slots(root, [(m1, L), (m2, s2), (0, placement.IDENT)])
            writer.refresh_object_bags(root)
    else:
        tree = [root_group(recs, roots, origin, handle)] + recs
    gh = G.parse_geom_header(tree[0].body_raw)
    if page_size is not None:
        w, h = page_size
    else:
        w, h = gh.rect2[2] - gh.rect2[0], gh.rect2[3] - gh.rect2[1]
    tr = trailer or default_trailer()
    head = bytearray(PAGE_HEAD_SIZE)
    struct.pack_into("<5i", head, 0x08, 0, ex, ey, w, h)
    ident = struct.pack("<I", int(identity.now_time()) & 0xFFFFFFFF) + identity.guid_bytes()
    head[0x20:0x34] = ident
    # LIVE: the engine writes the import table from the records -- linked
    # symbols + the @(native) labels of the texts ('\Hello' for a factory
    # text '@(Hello)'); no entries = no table (link_count 0)
    from . import genie_norm as GN

    carried = carried_import_entries(carried)
    # (a TEMPLATE whose own records name a %parameter% is a genie template: its records
    # take the genie form, genie_norm.convert_template_records)
    genie = (ext == "ctm" and _carries_tokens(tree)) or (ext == "ctt" and GN.template_declares(tree))
    normalised = genie or (normal_form and ext in ("ctm", "ctl"))
    raw_lt, n_lt = GN.link_table_bytes(
        "    ",
        GN.rebuild_link_table(
            tree,
            carried,
            resolver,
            keep_stale=carried_keys(carried, genies=not normalised),
            page_like=ext == "ctt" or page_size is not None,
        ),
    )
    pb = PageBlock(
        0,
        0,
        0,
        w,
        h,
        n_lt,
        ident,
        "    ",
        PAGE_HEAD_SIZE,
        PAGE_HEAD_SIZE,
        raw_lt,
        tree + [trailer_record(p) for p in tr.payloads()],
        thumbnail,
    )
    content = writer.serialize_page_block(pb, bytes(head))[: -writer.PAGE_TRAILING_PAD]
    if normalised:
        # (a plain new element in the normal form too: CtDraw's first load + save rewrites
        # it otherwise; ``normal_form`` False = the composite genie cache, whose bytes are
        # the 8.50 composer's, tests/test_composer.py)
        content = _genie_form(
            content, ext, origin, page_size, resolver, convert=genie, carried=carried, lib_path=lib_path
        )
    return content


def carried_import_entries(entries) -> List[Tuple[bytes, str]]:
    """The import entries of ``entries`` (a link table) a new element carries: library
    references only (no '\\\\native' labels, no attached super genies: those the element
    window adds itself)."""
    return [(g, n) for g, n in (entries or ()) if not n.startswith("\\") and "@super" not in n.lower()]


def carried_keys(entries, genies: bool = False) -> frozenset:
    """(library, element) of carried link entries whose stamps a new element keeps."""
    from . import links
    from .symbols import split_lib_element

    out = set()
    for _g, n in entries or ():
        base = links.strip_stamp(n)
        stem, _, ext = base.rpartition(".")
        ext = ext.lstrip("<").lower()
        if ext in ("ctl", "ctt") or (genies and ext == "ctm"):
            lib, elem = split_lib_element(stem)
            out.add((lib.lower(), elem.lower()))
    return frozenset(out)


def _carries_tokens(tree: Sequence[PageObject]) -> bool:
    """The new element is a GENIE: a direct record names a %parameter%, or a nested
    instance's typed binding does (genie_place.declares_parameters)."""
    from .genie_place import declares_parameters
    from . import placement

    return declares_parameters(list(tree), placement.forest_parents(list(tree)))


def _genie_form(
    content: bytes,
    ext: str,
    origin,
    page_size,
    resolver,
    convert: bool = True,
    carried=(),
    lib_path: str = "",
) -> bytes:
    """Put a new element into CtDraw's normal form."""
    from . import genie_norm as GN
    from . import geometry as G
    from . import genie_place as GP

    blk = Block(1, "new", 0, len(content) + PAD, content + bytes(PAD))
    pb = parse_page(blk, 0x0C)
    end = root_tree_end(pb.objects, 0)
    rep: dict = {}
    tree = GN.normalise_records(
        pb.objects[:end],
        ext=ext,
        convert=convert,
        e=tuple(origin),
        resolver=resolver,
        nested_only=GP.nested_keys(pb.objects[:end]),
        report=rep,
        fonts_path=lib_path,
    )
    pb2 = copy.copy(pb)
    if page_size is None:
        g = G.parse_geom_header(tree[0].body_raw)
        pb2.page_width, pb2.page_height = g.rect2[2] - g.rect2[0], g.rect2[3] - g.rect2[1]
    keep = carried_keys(carried) | frozenset(rep.get("stale_nested", ()))
    pb2.link_table_raw, pb2.link_count = GN.link_table_bytes(
        "    ",
        GN.rebuild_link_table(
            tree, list(carried), resolver, keep_stale=keep, page_like=page_size is not None
        ),
    )
    pb2.objects = tree + pb.objects[end:]
    return writer.serialize_page_block(pb2, blk.data)[: -writer.PAGE_TRAILING_PAD]


def element_changed_on_disk(pe: ParsedElement) -> bool:
    """True when THIS element's stored block differs from what was opened
    (another tool / tab rewrote it). The library FILE stamp is the wrong test
    for an element document: saving any sibling element changes it."""
    try:
        lib = LibraryFile.open(pe.lib_path)
    except OSError:
        return True
    s = lib.find(pe.name)
    return s is None or lib.block_data(s) != pe.block.data
