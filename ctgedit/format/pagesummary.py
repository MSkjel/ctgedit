"""Read a page or element summary quickly, without parsing every object."""

from __future__ import annotations

import os
import re
import struct
import zlib
from dataclasses import dataclass, field, replace
from typing import BinaryIO, Iterable, List, Optional, Tuple

from .container import ENTRY_SIZE, HEADER_FIXED, NAME_LEN, Block, directory_capacity
from .page import LINK_ENTRY_SIZE, PAGE_HEAD_SIZE, PAGE_HEAD_SIZE_LEGACY
from .symbols import EXTENT_HEAD_SIZE, grown_block_end, is_page_file
from .textcodec import CODEC

__all__ = [
    "TemplateRef",
    "DirEntry",
    "LibraryDirectory",
    "PageSummary",
    "ElementSummary",
    "read_directory",
    "header_problem",
    "directory_problem",
    "list_elements",
    "list_element_names",
    "read_page_summary",
    "read_element_summary",
    "read_element_summaries",
    "find_template_ref",
    "read_link_names",
    "parse_template_ref",
    "split_element_name",
    "bmp_dimensions",
    "bmp_to_rgba",
    "rgba_to_png",
    "bmp_to_png",
]

# a page-shaped block's head (read at once; the legacy head is shorter)
_HEAD_READ = 0x38
# default bounded template scan (finds the ref on most pages)
DEFAULT_TEMPLATE_SCAN = 65536
# the template placement lpstr: printable text ending ".<ctt"
_CTT_RE = re.compile(rb"\.<ct[tT]")


# ---------------------------------------------------------------------------
# Template references
# ---------------------------------------------------------------------------


def split_element_name(element: str) -> Tuple[str, str, bool]:
    """``'normal@-HD1080'`` -> ``('normal', 'HD1080', False)``."""
    name, sep, deco = element.partition("@")
    if not sep:
        return element, "", True
    title_bar = not deco.startswith("-")
    return name, deco.lstrip("-"), title_bar


@dataclass(frozen=True)
class TemplateRef:
    """A page's template reference, e.g. ``sxw_style_1.normal@HD768``."""

    raw: str
    library: str
    element: str

    @property
    def name(self) -> str:
        """Template name without the resolution decoration (``normal``)."""
        return split_element_name(self.element)[0]

    @property
    def resolution(self) -> str:
        """Resolution tag (``HD1080``, ``XGA``...), ``''`` when undecorated."""
        return split_element_name(self.element)[1]

    @property
    def title_bar(self) -> bool:
        """False when the element name carries ``@-`` (no title bar)."""
        return split_element_name(self.element)[2]

    @property
    def reference(self) -> str:
        """``library.element`` (what CtDraw shows / the Page Properties combo)."""
        return "%s.%s" % (self.library, self.element)

    def __str__(self) -> str:  # compact label
        return self.reference


def parse_template_ref(text: str) -> Optional[TemplateRef]:
    """``'sxw_style_1.normal@HD768.<ctt'`` (or without the suffix) -> TemplateRef."""
    if not text:
        return None
    core = text
    idx = core.lower().rfind(".<ct")
    if idx >= 0:
        core = core[:idx]
    # (the library is all before the LAST dot: CtDraw's)
    lib, _, element = core.rpartition(".")
    lib, element = lib.strip(), element.strip()
    if not lib or not element:
        return None
    return TemplateRef(raw=text, library=lib, element=element)


def _printable(buf: bytes, a: int, z: int) -> bool:
    for c in buf[a:z]:
        if c < 0x20 or c == 0x7F:
            return False
    return True


def _template_in(buf: bytes) -> Optional[TemplateRef]:
    """First well-framed ``.<ctt`` lpstr in ``buf`` (u16 length prefix, all
    printable -- the framing :func:`ctgedit.format.symbols.reference_from_body`
    uses), else the longest printable run ending at the first marker."""
    fallback: Optional[str] = None
    for m in _CTT_RE.finditer(buf):
        end = m.end()
        lo = max(2, m.start() - 256)
        start = m.start()
        while start >= lo:
            (ln,) = struct.unpack_from("<H", buf, start - 2)
            if ln == end - start and _printable(buf, start, end):
                ref = parse_template_ref(buf[start:end].decode(CODEC, "replace"))
                if ref is not None:
                    return ref
                break
            start -= 1
        if fallback is None:
            s = m.start()
            while s > 0 and 0x20 <= buf[s - 1] < 0x7F and m.start() - s < 256:
                s -= 1
            fallback = buf[s:end].decode(CODEC, "replace")
    return parse_template_ref(fallback) if fallback else None


# ---------------------------------------------------------------------------
# Container directory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirEntry:
    """One used directory slot: a block's tag / name / extent. For a
    library's growable last block ``size`` is the READABLE extent (the
    content its head describes when CtDraw grew it in place past the
    directory's size, :func:`_grow_last_block`), else the directory size."""

    slot: int
    tag: int
    name: str
    offset: int
    size: int

    @property
    def is_deleted(self) -> bool:
        """Deleted in CtDraw (or a zero-size stub) -- see ``Block.is_deleted``."""
        return Block(self.tag, self.name, self.offset, self.size).is_deleted


@dataclass
class LibraryDirectory:
    """The container header + directory of a ``.ctg``/``.ctl``/``.ctm``/``.ctt``."""

    path: str
    version: int = 0
    word2: int = 0
    ts_created: int = 0
    ts_modified: int = 0
    dword_c: int = 0
    entries: List[DirEntry] = field(default_factory=list)
    file_size: int = 0
    ok: bool = False
    #: the directory's slots the file holds fewer bytes for than ``dword_c`` says (0: all)
    slots_missing: int = 0
    #: the first LIVE block that runs past the end of the file (its name, and the byte it
    #: should end at): the file is truncated. Its ``size`` above is cut at the end of file.
    cut_block: str = ""
    cut_end: int = 0

    def block(self, name: str) -> Optional[DirEntry]:
        """The first live entry named ``name`` (exact, then case-insensitive)."""
        for e in self.entries:
            if e.name == name and not e.is_deleted:
                return e
        low = name.lower()
        for e in self.entries:
            if e.name.lower() == low and not e.is_deleted:
                return e
        return None

    def elements(self) -> List[DirEntry]:
        """Named, live element blocks in directory order."""
        return [e for e in self.entries if e.name and not e.is_deleted]


def read_dir(fh: BinaryIO, path: str, file_size: int) -> LibraryDirectory:
    d = LibraryDirectory(path=path, file_size=file_size)
    hdr = fh.read(HEADER_FIXED)
    if len(hdr) < HEADER_FIXED:
        return d
    d.version, d.word2, d.ts_created, d.ts_modified, d.dword_c = struct.unpack_from("<HHIII", hdr, 0)
    cap = directory_capacity(d.dword_c)
    raw = fh.read(cap * ENTRY_SIZE)
    d.slots_missing = cap - len(raw) // ENTRY_SIZE
    for i in range(cap):
        o = i * ENTRY_SIZE
        if o + ENTRY_SIZE > len(raw):
            break
        (tag,) = struct.unpack_from("<H", raw, o)
        off, size = struct.unpack_from("<II", raw, o + 2 + NAME_LEN)
        if tag == 0 and off == 0 and size == 0:
            continue  # unused slot
        name = raw[o + 2 : o + 2 + NAME_LEN].split(b"\x00", 1)[0].decode(CODEC, "replace")
        # a block must lie inside the file (a truncated / foreign file must
        # not produce reads past EOF)
        if file_size and off + size > file_size:
            if not d.cut_block and name and not DirEntry(i, tag, name, off, size).is_deleted:
                d.cut_block, d.cut_end = name, off + size
            size = max(0, file_size - off) if off < file_size else 0
        d.entries.append(DirEntry(i, tag, name, off, size))
    if file_size and d.word2 >= 0x0C and not is_page_file(path):
        _grow_last_block(fh, d)
    d.ok = True
    return d


def _grow_last_block(fh: BinaryIO, d: LibraryDirectory) -> None:
    """Grow a library's last block to the size CtDraw actually wrote."""
    for k, e in enumerate(d.entries):
        # (any in-use block: an append clears the growable flag and keeps the size)
        if not (e.tag & 0xFF) or e.is_deleted or e.offset + e.size >= d.file_size:
            continue
        fh.seek(e.offset)
        end = grown_block_end(e.tag, e.offset, e.size, fh.read(EXTENT_HEAD_SIZE), d.file_size, d.word2)
        if end > e.offset + e.size:
            d.entries[k] = replace(e, size=end - e.offset)


def header_problem(d: LibraryDirectory) -> str:
    """Why the container header of ``d`` (:func:`read_directory`) is not one the Graphics
    Builder opens (the rules of integrity.check_openable), in plain words; '' when it is."""
    from .upgrade import MIN_WORD2

    if not d.ok:
        if d.file_size == 0:
            return "the file is empty (0 bytes)"
        return "the file is too short to be a library (%d bytes)" % d.file_size
    if d.version == 0 or d.version > 0x40 or d.word2 > 0x100:
        return "it is not a Citect library, or its header is damaged"
    if not 7 <= d.version <= 9:
        return "its file version is %d, and the Graphics Builder reads versions 7 to 9 only" % d.version
    if d.word2 < MIN_WORD2:
        return "it is in an old library format (form %d) that is not read" % d.word2
    return ""


def directory_problem(d: LibraryDirectory) -> str:
    """Why the container can't be read as a whole ('' if it can)."""
    head = header_problem(d)
    if head:
        return head
    if d.slots_missing:
        return "the file is truncated: it ends inside its directory (%d bytes)" % d.file_size
    if d.cut_block:
        return "the file is truncated: its block %r should end at byte %d, but the file has only %d bytes" % (
            d.cut_block,
            d.cut_end,
            d.file_size,
        )
    return ""


def read_directory(path: str) -> LibraryDirectory:
    """Header + directory of a container file (``ok=False`` when unreadable)."""
    try:
        size = os.path.getsize(path)
        # unbuffered: a library's directory read seeks to every element's head for 32 bytes
        # (_grow_last_block), and a buffered read fills 8 KB at each
        with open(path, "rb", buffering=0) as fh:
            return read_dir(fh, path, size)
    except OSError, struct.error, ValueError:
        return LibraryDirectory(path=path)


def list_elements(lib_path: str) -> List[DirEntry]:
    """The live element blocks of a library (deleted blocks / stubs skipped)."""
    return read_directory(lib_path).elements()


def list_element_names(lib_path: str) -> List[str]:
    """Element names of a library, directory order, reading ONLY the directory."""
    return [e.name for e in list_elements(lib_path)]


# ---------------------------------------------------------------------------
# Thumbnail helpers (BMP -> RGBA / PNG, pure stdlib)
# ---------------------------------------------------------------------------


def _th_to_bmp(th: bytes) -> bytes:
    """``"TH"`` blob -> BMP bytes (``page._extract_bmp``); ``b''`` if not one."""
    if len(th) < 54 or th[0:2] != b"TH":
        return b""
    # (one copy of the pixels)
    return b"".join((b"BM", struct.pack("<I", len(th)), memoryview(th)[6:]))


def bmp_dimensions(bmp: bytes) -> Tuple[int, int]:
    """``(width, height)`` of a BMP (``(0, 0)`` if it isn't one)."""
    if len(bmp) < 26 or bmp[0:2] != b"BM":
        return 0, 0
    w, h = struct.unpack_from("<ii", bmp, 18)
    return abs(w), abs(h)


def bmp_to_rgba(bmp: bytes) -> Optional[Tuple[int, int, bytes]]:
    """Decode an uncompressed 24/32-bpp BMP to ``(w, h, RGBA)``."""
    try:
        if len(bmp) < 54 or bmp[0:2] != b"BM":
            return None
        pix_off = struct.unpack_from("<I", bmp, 10)[0]
        hsize, w, h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", bmp, 14)
        if hsize < 40 or w <= 0 or h == 0 or bpp not in (24, 32) or comp not in (0, 3):
            return None
        top_down = h < 0
        h = abs(h)
        stride = ((w * bpp + 31) // 32) * 4
        if pix_off + stride * h > len(bmp):
            return None
        bpp_bytes = bpp // 8
        out = bytearray(w * h * 4)
        row_len = w * 4
        for y in range(h):
            src_y = y if top_down else h - 1 - y
            row = bmp[pix_off + src_y * stride : pix_off + src_y * stride + w * bpp_bytes]
            o = y * row_len
            dst = out[o : o + row_len]
            dst[0::4] = row[2::bpp_bytes]  # R
            dst[1::4] = row[1::bpp_bytes]  # G
            dst[2::4] = row[0::bpp_bytes]  # B
            dst[3::4] = b"\xff" * w  # A (thumbnail alpha is unused)
            out[o : o + row_len] = dst
        return w, h, bytes(out)
    except struct.error, ValueError:
        return None


def rgba_to_png(w: int, h: int, rgba: bytes) -> bytes:
    """Encode top-down RGBA bytes as a PNG (pure stdlib)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    row = w * 4
    raw = b"".join(b"\x00" + rgba[y * row : (y + 1) * row] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def bmp_to_png(bmp: bytes) -> bytes:
    """BMP -> PNG bytes (``b''`` when the BMP can't be decoded)."""
    dec = bmp_to_rgba(bmp)
    return rgba_to_png(*dec) if dec else b""


class _ThumbMixin:
    thumb_bmp: bytes

    @property
    def has_thumbnail(self) -> bool:
        return bool(self.thumb_bmp)

    @property
    def thumbnail_size(self) -> Tuple[int, int]:
        return bmp_dimensions(self.thumb_bmp)

    def thumbnail_rgba(self) -> Optional[Tuple[int, int, bytes]]:
        """``(w, h, RGBA)`` of the embedded thumbnail, or ``None``."""
        return bmp_to_rgba(self.thumb_bmp) if self.thumb_bmp else None

    def thumbnail_png(self) -> bytes:
        """The embedded thumbnail as PNG bytes (``b''`` when absent)."""
        return bmp_to_png(self.thumb_bmp) if self.thumb_bmp else b""


# ---------------------------------------------------------------------------
# Page-shaped block summary
# ---------------------------------------------------------------------------


@dataclass
class _Head:
    objdata: int = 0
    count: int = 0
    thumb_size: int = 0
    width: int = 0
    height: int = 0
    link_count: int = 0
    data_start: int = 0
    area_code: str = ""


def read_head(fh: BinaryIO, off: int, size: int, word2: int) -> Optional[_Head]:
    legacy = word2 < 0x0C
    head_size = PAGE_HEAD_SIZE_LEGACY if legacy else PAGE_HEAD_SIZE
    if size < head_size:
        return None
    fh.seek(off)
    raw = fh.read(min(_HEAD_READ, size))
    if len(raw) < head_size:
        return None
    h = _Head()
    h.objdata, h.count = struct.unpack_from("<II", raw, 0)
    # (a legacy head, word2 < 0x0C, is the first 0x24 bytes of the current one: no GUID
    # after the save stamp; format/upgrade.py)
    (h.thumb_size,) = struct.unpack_from("<I", raw, 0x08)
    h.width, h.height, h.link_count = struct.unpack_from("<III", raw, 0x14)
    if h.link_count and len(raw) >= head_size + 4:  # (no table: no area code)
        h.area_code = raw[head_size : head_size + 4].decode(CODEC, "replace").strip()
    # implausible heads (foreign data) -> no geometry
    if h.link_count > 100000:
        return None
    h.data_start = head_size + h.link_count * LINK_ENTRY_SIZE
    if h.width > 100000 or h.height > 100000:
        h.width = h.height = 0
    return h


def _read_thumb(fh: BinaryIO, off: int, size: int, h: _Head) -> bytes:
    if not h.thumb_size:
        return b""
    t_off = h.data_start + h.objdata
    if t_off + h.thumb_size > size or h.thumb_size < 54:
        return b""
    fh.seek(off + t_off)
    return _th_to_bmp(fh.read(h.thumb_size))


def _scan_template(
    fh: BinaryIO, off: int, size: int, h: _Head, limit: Optional[int]
) -> Optional[TemplateRef]:
    region = max(0, min(h.objdata, size - h.data_start))
    n = region if limit is None else min(region, max(0, int(limit)))
    if n <= 0:
        return None
    fh.seek(off + h.data_start)
    return _template_in(fh.read(n))


@dataclass
class PageSummary(_ThumbMixin):
    """What the browser shows for a page, read without the object parse."""

    path: str
    ok: bool = False  # container + page head decoded
    width: int = 0
    height: int = 0
    object_count: int = 0
    thumb_bmp: bytes = b""  # BMP bytes ('TH' swapped to 'BM'); b'' = none
    template: Optional[TemplateRef] = None
    template_scanned: int = 0  # bytes of the object region searched
    area_code: str = ""
    ts_created: int = 0
    ts_modified: int = 0
    word2: int = 0
    mtime_ns: int = 0
    size: int = 0
    error: str = ""

    @property
    def template_ref(self) -> str:
        """``library.element`` of the template, ``''`` when none was found."""
        return self.template.reference if self.template else ""

    def without_thumbnail(self) -> "PageSummary":
        """A copy with the (large) BMP bytes dropped -- for metadata caches."""
        return replace(self, thumb_bmp=b"")


def read_page_summary(
    path: str, template_scan: Optional[int] = DEFAULT_TEMPLATE_SCAN, want_thumbnail: bool = True
) -> PageSummary:
    """Summarise a page without parsing its objects."""
    s = PageSummary(path=path)
    try:
        st = os.stat(path)
        s.mtime_ns, s.size = st.st_mtime_ns, st.st_size
        with open(path, "rb") as fh:
            d = read_dir(fh, path, st.st_size)
            if not d.ok:
                s.error = "not a Citect container"
                return s
            s.word2, s.ts_created, s.ts_modified = d.word2, d.ts_created, d.ts_modified
            pg = None
            for e in d.entries:
                if e.name == "page" and not e.is_deleted:
                    pg = e
                    break
            if pg is None:
                s.error = "no page block"
                return s
            h = read_head(fh, pg.offset, pg.size, d.word2)
            if h is None:
                s.error = "bad page head"
                return s
            s.width, s.height, s.object_count = h.width, h.height, h.count
            s.area_code = h.area_code
            if want_thumbnail:
                s.thumb_bmp = _read_thumb(fh, pg.offset, pg.size, h)
            if template_scan is None or template_scan > 0:
                s.template = _scan_template(fh, pg.offset, pg.size, h, template_scan)
                region = max(0, min(h.objdata, pg.size - h.data_start))
                s.template_scanned = region if template_scan is None else min(region, template_scan)
            s.ok = True
    except (OSError, struct.error, ValueError) as e:
        s.error = "%s: %s" % (type(e).__name__, e)
    return s


def read_link_names(path: str) -> List[str]:
    """The reference names in a page's link/import table (``lib.elem.<ext#stamp``
    per placed library element, ``\\\\native`` per language label -- see
    :mod:`ctgedit.format.links`), read from the page head + table only.
    ``[]`` when the page has none or cannot be read (never raises)."""
    from .links import parse_link_table

    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            d = read_dir(fh, path, size)
            if not d.ok:
                return []
            pg = next((e for e in d.entries if e.name == "page" and not e.is_deleted), None)
            if pg is None:
                return []
            h = read_head(fh, pg.offset, pg.size, d.word2)
            if h is None or not h.link_count:
                return []
            head_size = PAGE_HEAD_SIZE_LEGACY if d.word2 < 0x0C else PAGE_HEAD_SIZE
            n = min(h.link_count * LINK_ENTRY_SIZE, max(0, pg.size - head_size))
            fh.seek(pg.offset + head_size)
            t = parse_link_table(fh.read(n), legacy=d.word2 < 0x0C)
    except OSError, struct.error, ValueError:
        return []
    return [name for _guid, name in t[1]] if t else []


def find_template_ref(path: str) -> Optional[TemplateRef]:
    """The page's template reference from a FULL scan of the object region
    (for the few pages whose template placement lies past the bounded scan)."""
    return read_page_summary(path, template_scan=None, want_thumbnail=False).template


# ---------------------------------------------------------------------------
# Library elements
# ---------------------------------------------------------------------------


@dataclass
class ElementSummary(_ThumbMixin):
    """One library element (``.ctl``/``.ctm``/``.ctt`` block), no object parse."""

    library_path: str
    name: str
    ok: bool = False
    width: int = 0
    height: int = 0
    object_count: int = 0
    thumb_bmp: bytes = b""
    offset: int = 0
    size: int = 0
    tag: int = 0
    #: a bitmap of the element carries a second (flash) picture -- the
    #: diamond badge of CtDraw's symbol picker (see :func:`_element_flashing`)
    flashing: bool = False

    @property
    def library(self) -> str:
        """Library stem (``sa_valve``)."""
        return os.path.splitext(os.path.basename(self.library_path))[0]

    @property
    def reference(self) -> str:
        """``library.element`` as a placed reference names it."""
        return "%s.%s" % (self.library, self.name)

    def without_thumbnail(self) -> "ElementSummary":
        return replace(self, thumb_bmp=b"")


def _element_summary(
    fh: BinaryIO, lib_path: str, e: DirEntry, word2: int, want_thumbnail: bool
) -> ElementSummary:
    es = ElementSummary(library_path=lib_path, name=e.name, offset=e.offset, size=e.size, tag=e.tag)
    try:
        h = read_head(fh, e.offset, e.size, word2)
        if h is None:
            return es
        es.width, es.height, es.object_count = h.width, h.height, h.count
        if want_thumbnail:
            es.thumb_bmp = _read_thumb(fh, e.offset, e.size, h)
        es.ok = True
        if lib_path.lower().endswith(".ctl"):
            es.flashing = _element_flashing(fh, e.offset, e.size, h)
    except OSError, struct.error, ValueError:
        pass
    return es


#: the flashing test reads at most this many bytes / records of a SYMBOL
#: element (a bigger one -- or a genie / template -- is not read)
_FLASH_SCAN = 512 * 1024
_FLASH_RECORDS = 64


def _element_flashing(fh: BinaryIO, off: int, size: int, h: _Head) -> bool:
    """Does the symbol's root tree hold a flashing bitmap?"""
    try:
        from .bitmaps import dib_section
        from .page import RECORD_HEADER_SIZE, parse_record_shell

        n = min(h.objdata, max(0, size - h.data_start))
        if n <= 0 or n > _FLASH_SCAN:
            return False
        fh.seek(off + h.data_start)
        data = fh.read(n)
        o, pending, seen = 0, 1, 0
        while pending and o + RECORD_HEADER_SIZE <= len(data) and seen < _FLASH_RECORDS:
            seen += 1
            psize = struct.unpack_from("<I", data, o)[0]
            payload = data[o + RECORD_HEADER_SIZE : o + RECORD_HEADER_SIZE + psize]
            o += RECORD_HEADER_SIZE + psize
            # (the prefix and the body: the test reads no bags)
            po = parse_record_shell(payload)
            pending += po.class_flag - 1
            if po.type_code == 0x07 and not (po.subtype_size & 0x1):
                sec = dib_section(po)
                if sec is not None and sec.flags & 0x100:
                    return True
        return False
    except Exception:  # noqa: BLE001 -- swallow-ok: a hint in a list: never an error
        return False


def read_element_summaries(
    lib_path: str, names: Optional[Iterable[str]] = None, want_thumbnail: bool = True
) -> List[ElementSummary]:
    """Summaries of a library's elements (all live ones, or just ``names``,
    in directory order). One open, one small read per element."""
    try:
        size = os.path.getsize(lib_path)
        with open(lib_path, "rb") as fh:
            d = read_dir(fh, lib_path, size)
            if not d.ok:
                return []
            elems = d.elements()
            if names is not None:
                want = {n.lower() for n in names}
                elems = [e for e in elems if e.name.lower() in want]
            return [_element_summary(fh, lib_path, e, d.word2, want_thumbnail) for e in elems]
    except OSError, struct.error, ValueError:
        return []


def read_element_summary(
    lib_path: str,
    name: str,
    offset: Optional[int] = None,
    size: Optional[int] = None,
    word2: Optional[int] = None,
    want_thumbnail: bool = True,
) -> Optional[ElementSummary]:
    """Summary of ONE element. With ``offset``/``size``/``word2`` from a prior
    directory read (the catalog keeps them) the directory is not re-read.
    ``None`` when the element is absent / deleted or the file unreadable."""
    try:
        fsize = os.path.getsize(lib_path)
        with open(lib_path, "rb") as fh:
            if offset is None or size is None or word2 is None:
                d = read_dir(fh, lib_path, fsize)
                if not d.ok:
                    return None
                e = d.block(name)
                if e is None:
                    return None
                word2 = d.word2
            else:
                if offset + size > fsize:
                    return None
                e = DirEntry(-1, 0x0001, name, offset, size)
            return _element_summary(fh, lib_path, e, word2, want_thumbnail)
    except OSError, struct.error, ValueError:
        return None
