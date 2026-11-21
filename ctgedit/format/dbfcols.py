"""Read selected columns of the project tables, with a shared cache."""

from __future__ import annotations

import codecs
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from . import textcodec as _tc
from .textcodec import CODEC

_DELETED = 0x2A  # '*'
_FIELD_TERM = 0x0D
_STREAM_MIN = 1 << 20  # smaller tables are read whole, bigger ones streamed
_CHUNK = 4 << 20  # bytes of whole records per streamed read
_HEAD_MAX = 1 << 16  # the header bytes read before the records (u16 header length)
_GLOB_MAGIC = re.compile(r"[*?[]")
#: a long loop on a worker thread lets go of the GIL every this many rows
YIELD_ROWS = 4096
#: how long the tag index build lets go of it (s), every YIELD_ROWS rows and between the
#: tables. A time.sleep(0) is taken back by the worker before the waiting GUI thread wakes
NAP_S = 0.0003
_MAIN_ID = threading.main_thread().ident


def breathe(nap: bool = False) -> None:
    """On a worker thread, release the GIL briefly so the GUI thread can run."""
    if threading.get_ident() != _MAIN_ID:
        time.sleep(NAP_S if nap else 0)


def _single_byte_table() -> Optional[str]:
    """The decoding table of a single-byte codec, for fast decodes."""
    table = getattr(_tc, "_DECODING_TABLE", None)
    if _tc.MULTIBYTE or not isinstance(table, str) or len(table) != 256:
        return None
    every = bytes(range(256))
    if (
        codecs.charmap_decode(every, "replace", table)[0] != every.decode(CODEC, "replace")
        or table.count("\x00") != 1
        or table.count(" ") != 1
        or table[0] != "\x00"
        or table[0x20] != " "
    ):
        return None
    return table


_TABLE = _single_byte_table()
_charmap = codecs.charmap_decode


class _File:
    """A table file read by position: held whole (``data``) or streamed from ``fh``."""

    def __init__(self, fh, size: int, data: Optional[bytes] = None):
        self.fh, self.size, self.data = fh, size, data

    def read_at(self, off: int, n: int) -> bytes:
        if self.data is not None:
            return self.data[off : off + n]
        self.fh.seek(off)
        return self.fh.read(n)

    def records(self, start: int, reclen: int, count: int) -> Iterator[Tuple[bytes, int, int]]:
        """(buffer, offset of its first record, records in it) over ``count`` whole
        ``reclen``-byte records from ``start`` (fewer when the file ends first)."""
        if count <= 0:
            return
        if self.data is not None:
            k = min(count, max(0, (len(self.data) - start) // reclen))
            if k > 0:
                yield self.data, start, k
            return
        per = max(1, _CHUNK // reclen)
        self.fh.seek(start)
        left = count
        while left > 0:
            want = min(per, left)
            buf = self.fh.read(want * reclen)
            got = len(buf) // reclen
            if got:
                yield buf, 0, got
            if got < want:
                return
            left -= got


def _open(fh) -> _File:
    size = os.fstat(fh.fileno()).st_size
    return _File(fh, size, fh.read() if size < _STREAM_MIN else None)


def _picks(names: Sequence[str], columns: Sequence[str]) -> List[Optional[int]]:
    """Per requested column the index of the LAST field so named (None: absent)."""
    last = {}
    for k, n in enumerate(names):
        last[n] = k
    return [last.get(c) for c in columns]


def _slicer(
    offsets: Sequence[int], lengths: Sequence[int], wanted: Sequence[int], reclen: int
) -> Tuple[struct.Struct, dict]:
    """A Struct unpacking [flag byte] + the wanted fields of one ``reclen``-byte
    record, and field index -> position in its result."""
    order = sorted(set(wanted), key=lambda k: offsets[k])
    parts, cur, where = ["<1s"], 1, {}
    for pos, k in enumerate(order, 1):
        gap = offsets[k] - cur
        if gap:
            parts.append("%dx" % gap)
        parts.append("%ds" % lengths[k])
        cur = offsets[k] + lengths[k]
        where[k] = pos
    if reclen > cur:
        parts.append("%dx" % (reclen - cur))
    return struct.Struct("".join(parts)), where


# --------------------------------------------------------------------------- "citect"
def citect_rows(path: str, columns: Optional[Sequence[str]] = None, default: str = "") -> list:
    """:func:`projects.read_dbf_rows` of ``path`` -- every row as a {FIELD: value}
    dict (``columns`` None), or as a tuple of the ``columns`` values (``default``
    for a column the table lacks). A fresh list every call; ``[]`` when the file
    cannot be read or is not a table."""
    try:
        with open(path, "rb") as fh:
            return _citect(_open(fh), columns, default)
    except OSError:
        return []


def _citect(f: _File, columns, default) -> list:
    n = f.size if f.data is None else len(f.data)
    if n < 33:
        return []
    top = f.read_at(0, 12)
    if len(top) < 12:
        return []  # (the file shrank under us)
    nrec, hdr_size, rec_size = struct.unpack_from("<IHH", top, 4)
    if rec_size <= 0 or hdr_size < 33 or hdr_size > n:
        return []
    head = f.read_at(0, hdr_size)
    if len(head) < hdr_size:
        return []  # (the file shrank under us)
    names, offsets, lengths = [], [], []
    off, pos = 32, 1
    while off + 32 <= hdr_size and head[off] != _FIELD_TERM:
        names.append(head[off : off + 11].split(b"\x00")[0].decode(CODEC, "replace").strip())
        ln = head[off + 16]
        offsets.append(pos)
        lengths.append(ln)
        pos += ln
        off += 32
    if not names or pos > rec_size:
        return []
    count = min(nrec, (n - hdr_size) // rec_size)
    rows: list = []
    append = rows.append
    table = _TABLE
    if columns is None:
        # a dict of every field (a later field of the same name overwrites the value
        # at the first one's position, as a dict comprehension does)
        spans = [(offsets[k], offsets[k] + lengths[k]) for k in range(len(names))]
        for buf, base, k in f.records(hdr_size, rec_size, count):
            for i in range(k):
                o = base + i * rec_size
                if buf[o] == _DELETED:
                    continue
                if table is not None:
                    s = _charmap(buf[o : o + pos], "replace", table)[0]
                    append(dict(zip(names, [s[a:b].strip() for a, b in spans])))
                else:
                    rec = buf[o : o + rec_size]
                    append(dict(zip(names, [rec[a:b].decode(CODEC, "replace").strip() for a, b in spans])))
        return rows
    picks = _picks(names, columns)
    st, where = _slicer(offsets, lengths, [k for k in picks if k is not None], rec_size)
    unpack = st.unpack_from
    slots = [where[k] if k is not None else None for k in picks]
    fast = table is not None and None not in slots
    for buf, base, k in f.records(hdr_size, rec_size, count):
        for i in range(k):
            if not i % YIELD_ROWS:
                breathe(True)
            t = unpack(buf, base + i * rec_size)
            if t[0] == b"*":
                continue
            if fast:
                # the bytes' own strip() first (ASCII blanks: what the text's strip()
                # would take off anyway), then decode only what is left
                append(
                    tuple(
                        [
                            _charmap(x, "replace", table)[0].strip() if (x := t[s].strip()) else ""
                            for s in slots
                        ]
                    )
                )
            else:
                append(
                    tuple(
                        [t[s].decode(CODEC, "replace").strip() if s is not None else default for s in slots]
                    )
                )
    return rows


# --------------------------------------------------------------------------- "dbfread"
def dbfread_columns(path: str, columns: Sequence[str], fallback: bool = True):
    """Selected ``columns`` of every row, as dbfread would read them."""
    if not _GLOB_MAGIC.search(path):
        got = None
        try:
            with open(path, "rb") as fh:
                got = _dbfread_fast(_open(fh), columns)
        except OSError:
            got = None  # dbfread reports it
        if got is not None:
            return got
    if not fallback:
        return None
    return _dbfread_itself(path, columns)


def _dbfread_itself(path: str, columns: Sequence[str]) -> list:
    from dbfread import DBF

    table = DBF(path, encoding=CODEC, load=False, ignore_missing_memofile=True, char_decode_errors="replace")
    return [tuple(r.get(c) for c in columns) for r in table]


def _dbfread_fast(f: _File, columns) -> Optional[list]:
    """dbfread's rows of a table of character fields; None = let dbfread read it
    (another field type, a header dbfread would fail on)."""
    n = f.size if f.data is None else len(f.data)
    if n < 32:
        return None
    head = f.read_at(0, min(n, _HEAD_MAX))
    if len(head) < 32:
        return None
    headerlen, reclen = struct.unpack_from("<HH", head, 8)
    names, offsets, lengths = [], [], []
    pos, cur = 32, 1
    while pos < n:
        if pos + 32 > len(head) and len(head) < n:
            return None  # descriptors past the first 64 KB: dbfread reads it
        sep = head[pos]
        if sep in (0x0D, 0x0A):
            break
        if pos + 32 > n:
            return None  # dbfread's struct.error
        if head[pos + 11] != 0x43:  # 'C'
            return None
        ln = head[pos + 16] | (head[pos + 17] << 8)
        names.append(head[pos : pos + 11].split(b"\x00")[0].decode(CODEC, "replace"))
        offsets.append(cur)
        lengths.append(ln)
        cur += ln
        pos += 32
    if reclen < 1:
        return None
    picks = _picks(names, columns)
    rows: list = []
    append = rows.append

    def value(b: bytes) -> str:
        return b.rstrip(b"\x00 ").decode(CODEC, "replace")

    if cur != reclen:
        # a yielded row reads its fields in order (1 + their lengths), a skipped one seeks
        # ``reclen`` on: no fixed stride -- dbfread's own walk over the whole file
        data = f.data if f.data is not None else f.read_at(0, n)
        _walk(data, headerlen, reclen, lengths, picks, value, append)
        return rows
    # every record is ``reclen`` bytes long: one struct call per record
    st, where = _slicer(offsets, lengths, [k for k in picks if k is not None], reclen)
    unpack = st.unpack_from
    full = max(0, (n - headerlen) // reclen)
    table = _TABLE
    slots = [where[k] if k is not None else None for k in picks]
    fast = table is not None and None not in slots
    for buf, base, k in f.records(headerlen, reclen, full):
        for i in range(k):
            t = unpack(buf, base + i * reclen)
            s = t[0]
            if s == b" ":
                if fast:
                    # decode only what rstrip(b'\0 ') leaves (most fields are blank)
                    append(
                        tuple(
                            [
                                _charmap(x, "replace", table)[0] if (x := t[j].rstrip(b"\x00 ")) else ""
                                for j in slots
                            ]
                        )
                    )
                else:
                    append(tuple([value(t[j]) if j is not None else None for j in slots]))
            elif s == b"\x1a":
                return rows
    # the part record after the whole ones (the file's end), as dbfread reads it
    at = headerlen + full * reclen
    if at < n:
        _walk(f.read_at(at, n - at), 0, reclen, lengths, picks, value, append)
    return rows


def _walk(data, at: int, reclen: int, lengths, picks, value, append) -> None:
    """dbfread's record loop over ``data`` from ``at`` (reads past the end give short bytes)."""
    n = len(data)
    while at < n:
        s = data[at]
        at += 1
        if s == 0x20:
            vals = []
            for ln in lengths:
                vals.append(bytes(data[at : at + ln]))
                at += ln
            append(tuple(value(vals[k]) if k is not None else None for k in picks))
        elif s == 0x1A:
            break
        else:
            at += reclen - 1


# --------------------------------------------------------------------------- the cache
_LOCK = threading.Lock()
_CACHE: "OrderedDict[tuple, Tuple[object, int]]" = OrderedDict()
#: the bound: entries, and cells (rows x columns of what the entries were built from)
MAX_ENTRIES = 64
MAX_CELLS = 2_500_000
_cells = [0]


def stamp(path: str) -> Optional[Tuple[str, int, int]]:
    """(normalised path, mtime_ns, size) of a file, None when it cannot be stat'ed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)


def cached(path: str, kind, build: Callable[[str], object], weight: Callable[[object], int] = len):
    """``build(path)``, cached while the file is unchanged."""
    st = stamp(path)
    if st is None:
        return build(path)
    key = st + (kind,)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit[0]
    value = build(path)
    if stamp(path) != st:
        return value
    try:
        w = max(1, int(weight(value)))
    except Exception:  # noqa: BLE001
        w = 1
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:  # another thread built it meanwhile: share one
            _CACHE.move_to_end(key)
            return hit[0]
        _CACHE[key] = (value, w)
        _cells[0] += w
        while len(_CACHE) > 1 and (len(_CACHE) > MAX_ENTRIES or _cells[0] > MAX_CELLS):
            _k, (_v, ow) = _CACHE.popitem(last=False)
            _cells[0] -= ow
    return value


def clear() -> None:
    """Forget every cached table (the row cache and the column tables)."""
    with _LOCK:
        _CACHE.clear()
        _cells[0] = 0
    with _COL_LOCK:
        _COLS.clear()
        _col_bytes[0] = 0


def rows(path: str) -> list:
    """:func:`citect_rows` of every column, cached (the list and its dicts are
    shared: read-only)."""
    return cached(path, ("rows",), citect_rows, lambda v: len(v) * (len(v[0]) if v else 1))


# --------------------------------------------------------------------------- column tables
# The tag, equipment and trend lists of a scope (io.tagscope) read the same few columns of
# many large tables. A ColumnTable keeps each column as ONE tuple, with equal values shared,
# which takes far less memory than row tuples. They have their own memory budget (the row
# cache's is less than one large scope) and are least recently used first out, keyed by the
# table's stamp and the columns read, so every page of a scope shares them.

_COL_LOCK = threading.Lock()
_COLS: "OrderedDict[tuple, ColumnTable]" = OrderedDict()
#: the column tables' memory budget (an estimate of the bytes they hold)
COLUMN_BUDGET = 320 << 20
_col_bytes = [0]
#: keys being built: key -> Event (a second thread waits for the first build)
_COL_BUILDING: dict = {}
_MISSING = object()


class ColumnTable:
    """Some columns of one table, stored column by column."""

    __slots__ = ("path", "stamp", "columns", "_cols", "rows", "weight", "_derived", "_lock", "__weakref__")

    def __init__(self, path: str, stamp, columns: Sequence[str], cols: Sequence[tuple]):
        self.path = path
        self.stamp = stamp
        self.columns = tuple(columns)
        self._cols = {c.upper(): tuple(v) for c, v in zip(self.columns, cols)}
        self.rows = len(cols[0]) if cols else 0
        self.weight = 0
        self._derived: dict = {}
        # re-entrant: a build may ask for another structure of the same table
        # (the search text of the names reads their sorted order)
        self._lock = threading.RLock()

    def __len__(self) -> int:
        return self.rows

    def column(self, name: str) -> tuple:
        """The column's values ('' in every row for a column not read)."""
        col = self._cols.get(name.upper())
        if col is None:
            col = self._cols[name.upper()] = ("",) * self.rows
        return col

    def has(self, name: str) -> bool:
        return name.upper() in self._cols

    def derived(self, key, build: Callable[["ColumnTable"], object], weight: Callable[[object], int] = None):
        """``build(self)``, built once (thread-safe) and kept with the table.
        ``weight(value)`` adds its size to the budget."""
        v = self._derived.get(key, _MISSING)
        if v is not _MISSING:
            return v
        with self._lock:
            v = self._derived.get(key, _MISSING)
            if v is _MISSING:
                v = build(self)
                self._derived[key] = v
                if weight is not None:
                    try:
                        w = max(0, int(weight(v)))
                    except Exception:  # noqa: BLE001
                        w = 0
                    self.weight += w
                    with _COL_LOCK:
                        if any(t is self for t in _COLS.values()):
                            _col_bytes[0] += w
        return v

    @classmethod
    def from_columns(
        cls, columns: Sequence[str], cols: Sequence[Sequence[str]], path: str = ""
    ) -> "ColumnTable":
        """A table made in memory (not cached)."""
        return cls(path, None, columns, [tuple(c) for c in cols])


def _shared(values) -> tuple:
    """``values`` with equal strings made one object (a dict of the distinct values)."""
    memo: dict = {}
    get = memo.setdefault
    return tuple([get(v, v) for v in values])


def _str_bytes(values) -> int:
    return sum(49 + len(v) for v in values)


def field_names(path: str) -> List[str]:
    """The field names of a table's header ('citect' dialect), [] when unreadable."""
    try:
        with open(path, "rb") as fh:
            top = fh.read(12)
            if len(top) < 12:
                return []
            hdr_size = struct.unpack_from("<H", top, 8)[0]
            fh.seek(0)
            head = fh.read(min(hdr_size, _HEAD_MAX))
    except OSError:
        return []
    names, off = [], 32
    while off + 32 <= len(head) and head[off] != _FIELD_TERM:
        names.append(head[off : off + 11].split(b"\x00")[0].decode(CODEC, "replace").strip())
        off += 32
    return names


def _build_columns(path: str, st, columns: Sequence[str], unique: Sequence[str]) -> ColumnTable:
    # a column is named as the header spells it (the lists ask 'Name' of a NAME field)
    have = field_names(path)
    exact = set(have)
    upper = {}
    for n in have:
        upper.setdefault(n.upper(), n)
    read = [c if c in exact else upper.get(c.upper(), c) for c in columns]
    rows_ = citect_rows(path, read)
    if rows_:
        cols = list(zip(*rows_))
    else:
        cols = [() for _c in columns]
    del rows_
    breathe(True)
    uniq = {u.upper() for u in unique}
    out, weight = [], 0
    for name, col in zip(columns, cols):
        if name.upper() in uniq:
            out.append(tuple(col))
            weight += 8 * len(col) + _str_bytes(col)
        else:
            shared = _shared(col)
            out.append(shared)
            weight += 8 * len(shared) + _str_bytes({id(v): v for v in shared}.values())
    t = ColumnTable(path, st, columns, out)
    t.weight = weight + 200
    return t


def column_table(
    path: str, columns: Sequence[str], unique: Sequence[str] = ("NAME",)
) -> Optional[ColumnTable]:
    """The ``columns`` of the table at ``path`` as a :class:`ColumnTable`, kept
    while the file's stamp is unchanged (None when the file cannot be stat'ed).
    ``unique`` = columns whose values are mostly distinct (not shared). A
    second thread asking for a table being read waits for it."""
    st = stamp(path)
    if st is None:
        return None
    key = st + (tuple(c.upper() for c in columns),)
    while True:
        with _COL_LOCK:
            hit = _COLS.get(key)
            if hit is not None:
                _COLS.move_to_end(key)
                return hit
            ev = _COL_BUILDING.get(key)
            if ev is None:
                ev = _COL_BUILDING[key] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            ev.wait(120)
            continue
        try:
            t = _build_columns(path, st, columns, unique)
            if stamp(path) == st:
                with _COL_LOCK:
                    _COLS[key] = t
                    _col_bytes[0] += t.weight
                    while len(_COLS) > 1 and _col_bytes[0] > COLUMN_BUDGET:
                        _k, old = _COLS.popitem(last=False)
                        _col_bytes[0] -= old.weight
            return t
        finally:
            with _COL_LOCK:
                _COL_BUILDING.pop(key, None)
            ev.set()


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("dbfcols.tables", _CACHE, clear=clear, on=("tests",))


# (the column tables: re-validated by file stamps; Refresh re-reads them)
_caches.register("dbfcols.columns", _COLS, clear=clear, on=("refresh", "tests"))
