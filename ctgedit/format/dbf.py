"""Edit dBASE III tables and NDX indexes in place, the way the Project Editor does."""

from __future__ import annotations

import datetime as _dt
import os
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from .textcodec import CODEC, UnencodableTextError, encode_field, truncate


def _today() -> _dt.date:
    """A header's last-update date: today (infra/identity: CTG_FIXED_IDENTITY fixes it)."""
    from ..infra import identity

    return identity.today()


PAGE_SIZE = 512
EOF_MARK = b"\x1a"


def _ascii_same() -> bool:
    """The code page stores every ASCII character as its ASCII byte (Dbf.encode_many encodes
    an ASCII value with the C codec then)."""
    try:
        return all(chr(c).encode(CODEC) == bytes([c]) for c in range(0x80))
    except Exception:  # noqa: BLE001  # swallow-ok: a probe; every value takes the codec then
        return False


_ASCII_SAME = _ascii_same()
#: (for the readers of other modules: the page model's field widths)
ASCII_SAME = _ASCII_SAME


class Dbf:
    """A DBF opened for in-place update (``mode='r+b'``) or reading."""

    def __init__(self, path: str, writable: bool = True):
        from . import journal

        self.path = path
        self.fh = journal.open_patch(path) if writable else open(path, "rb")
        hdr = self.fh.read(32)
        if len(hdr) < 32:
            self.fh.close()
            raise ValueError("%s: not a DBF" % path)
        self.version = hdr[0]
        self.nrec, self.hlen, self.rlen = struct.unpack_from("<IHH", hdr, 4)
        self.fh.seek(32)
        desc = self.fh.read(self.hlen - 32)
        self.fields: List[Tuple[str, int, int]] = []  # (name, offset in record, length)
        pos = 1
        for o in range(0, len(desc) - 31, 32):
            if desc[o] == 0x0D:
                break
            name = desc[o : o + 11].split(b"\0")[0].decode(CODEC)
            ln = desc[o + 16]
            self.fields.append((name, pos, ln))
            pos += ln
        self.fmap = {n.upper(): (o, ln) for n, o, ln in self.fields}
        # name -> [(field index, offset, length)]: an encode walks the row's keys, not every
        # field (a page's register encodes a few keys into 60-field rows)
        self._by_name: Dict[str, List[Tuple[int, int, int]]] = {}
        for i, (n, o, ln) in enumerate(self.fields):
            self._by_name.setdefault(n.upper(), []).append((i, o, ln))
        self.writable = writable

    def close(self, ok: bool = True) -> None:
        """``ok`` False (a ``with`` block that raised): outside a journal
        transaction, this file's writes are rolled back."""
        if self.writable:
            self.fh.close(ok)
        else:
            self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, et, *exc):
        self.close(ok=et is None)

    # ---------------------------------------------------------------- records
    def _off(self, i: int) -> int:
        return self.hlen + i * self.rlen

    def read(self, i: int) -> bytes:
        self.fh.seek(self._off(i))
        return self.fh.read(self.rlen)

    def field(self, rec: bytes, name: str) -> str:
        o, ln = self.fmap[name.upper()]
        return rec[o : o + ln].decode(CODEC).rstrip()

    def decode(self, rec: bytes) -> Dict[str, str]:
        return {n: rec[o : o + ln].decode(CODEC).rstrip() for n, o, ln in self.fields}

    def encode(self, row: Dict[str, str], flag: bytes = b" ") -> bytes:
        """A record of ``row``'s values; ``UnencodableTextError`` when a value
        holds a character the code page cannot store (nothing is written)."""
        out = bytearray(b" " * self.rlen)
        out[0:1] = flag
        up = {k.upper(): v for k, v in row.items()}
        hits = []
        for k, v in up.items():
            if v:
                hits.extend((i, o, ln, v) for i, o, ln in self._by_name.get(k, ()))
        hits.sort(key=lambda t: t[0])  # (in field order: the first bad one is named)
        for i, o, ln, v in hits:
            try:
                raw = str(v).encode(CODEC)
            except UnicodeEncodeError:
                # (the message names the field: built only when it is needed -- a page's
                # register encodes thousands of fields)
                raw = encode_field(v, "the %s field of %s" % (self.fields[i][0], os.path.basename(self.path)))
            b = truncate(raw, ln)
            out[o : o + len(b)] = b
        return bytes(out)

    def encode_many(self, rows: Sequence[Dict[str, str]], flag: bytes = b" ") -> List[bytes]:
        """``[self.encode(r, flag) for r in rows]``: the same records and the same error.
        The fields a row's keys fill are worked out once per key list (the rows of one
        table share a few), and an ASCII value goes through the C codec."""
        blank = bytearray(b" " * self.rlen)
        blank[0:1] = flag
        plans: Dict[tuple, list] = {}
        out: List[bytes] = []
        for row in rows:
            keys = tuple(row)
            plan = plans.get(keys)
            if plan is None:
                last: Dict[str, str] = {}
                for k in keys:
                    last[k.upper()] = k  # (encode: the last key of a name wins)
                plan = sorted(
                    ((i, o, ln, k) for up, k in last.items() for i, o, ln in self._by_name.get(up, ())),
                    key=lambda t: t[0],
                )
                plans[keys] = plan
            rec = bytearray(blank)
            for i, o, ln, k in plan:
                v = row[k]
                if not v:
                    continue
                s = str(v)
                if _ASCII_SAME and s.isascii():
                    raw = s.encode("ascii")
                else:
                    try:
                        raw = s.encode(CODEC)
                    except UnicodeEncodeError:
                        raw = encode_field(
                            v, "the %s field of %s" % (self.fields[i][0], os.path.basename(self.path))
                        )
                b = raw if len(raw) <= ln else truncate(raw, ln)
                rec[o : o + len(b)] = b
            out.append(bytes(rec))
        return out

    def find(self, name: str, value: str) -> List[Tuple[int, bytes]]:
        """(record index, record bytes) of every record whose field ``name``
        equals ``value`` case-insensitively (deleted ones included). A value
        the code page cannot store is in no record."""
        o, ln = self.fmap[name.upper()]
        try:
            want = truncate(value.encode(CODEC), ln).lower().ljust(ln)
        except UnicodeEncodeError:
            return []
        rl = self.rlen
        out = []
        per = max(1, (4 << 20) // rl)  # ~4 MB of records per read
        i = 0
        while i < self.nrec:
            k = min(per, self.nrec - i)
            self.fh.seek(self._off(i))
            buf = self.fh.read(k * rl)
            for j in range(len(buf) // rl):
                base = j * rl
                if buf[base + o : base + o + ln].lower() == want:
                    out.append((i + j, buf[base : base + rl]))
            if len(buf) < k * rl:
                break
            i += k
        return out

    def write(self, i: int, rec: bytes) -> None:
        assert len(rec) == self.rlen
        self.fh.seek(self._off(i))
        self.fh.write(rec)

    def delete(self, i: int) -> None:
        self.fh.seek(self._off(i))
        self.fh.write(b"*")

    def append(self, recs: Sequence[bytes]) -> int:
        """Append records; returns the index of the first. Keeps the 0x1A
        terminator and the header count."""
        first = self.nrec
        if not recs:
            return first
        self.fh.seek(self._off(first))
        self.fh.write(b"".join(recs) + EOF_MARK)
        self.nrec += len(recs)
        self.fh.seek(4)
        self.fh.write(struct.pack("<I", self.nrec))
        return first

    def touch(self, when: Optional[_dt.date] = None) -> None:
        """The header's last-update date (YY since 1900, MM, DD)."""
        d = when or _today()
        self.fh.seek(1)
        self.fh.write(bytes((d.year - 1900, d.month, d.day)))

    def flush(self) -> None:
        self.fh.flush()


def _kcmp(a: Tuple[bytes, int], b: Tuple[bytes, int], case: bool = False) -> int:
    """The index's key order: bytes with ASCII letters without case, or with ``case``
    plain bytes (the index's case flag, 0x800 at its creation)."""
    ka, kb = (a[0], b[0]) if case else (a[0].lower(), b[0].lower())
    if ka != kb:
        return -1 if ka < kb else 1
    return (a[1] > b[1]) - (a[1] < b[1])


def create_ndx(path: str, expr: str, klen: int) -> None:
    """A new, empty index on the char field ``expr`` (the Project Editor's own
    layout: entry = 8 + key rounded up to 4, (512 - 8) // entry keys a node,
    root = page 1, an empty leaf)."""
    esize = 8 + ((klen + 3) & ~3)
    h = bytearray(PAGE_SIZE)
    struct.pack_into("<IIIHHHH", h, 0, 1, 2, 0, klen, (PAGE_SIZE - 8) // esize, 0, esize)
    e = expr.encode(CODEC)
    h[24 : 24 + len(e)] = e
    with open(path, "wb") as fh:
        fh.write(bytes(h) + bytes(PAGE_SIZE))


class Ndx:
    """A dBASE III index opened for insertion (``writable=False``: for lookups
    only, a plain read handle -- no write access needed, nothing journaled)."""

    #: keys compare with case (see __init__)
    case = False

    def __init__(self, path: str, writable: bool = True, case: bool = False):
        from . import journal

        self.path = path
        self.writable = writable
        #: keys compare with case (the index was made with the case flag
        #: 0x800: pglang under [LANGUAGE] CaseSensitive)
        self.case = case
        self.fh = journal.open_patch(path) if writable else open(path, "rb")
        h = self.fh.read(PAGE_SIZE)
        self.root, self.npages = struct.unpack_from("<II", h, 0)
        self.klen, self.kpn, self.ktype, self.esize = struct.unpack_from("<HHHH", h, 12)
        self.unique = h[23]
        self.expr = h[24 : 24 + 220].split(b"\0")[0].decode(CODEC).strip()
        #: insert_many's pages in memory ({page: (entries, last)}) and the ones it changed
        self._cache: Optional[dict] = None
        self._dirty: set = set()
        if not self.klen or not self.esize or self.kpn < 2:
            self.fh.close()
            raise ValueError("%s: unsupported NDX" % path)

    def close(self, ok: bool = True) -> None:
        if self.writable:
            self.fh.close(ok)
        else:
            self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, et, *exc):
        self.close(ok=et is None)

    # ---------------------------------------------------------------- nodes
    def _read(self, pg: int):
        if self._cache is not None and pg in self._cache:
            ents, last = self._cache[pg]
            return list(ents), last
        ents, last = self._read_disk(pg)
        if self._cache is not None:
            self._cache[pg] = (list(ents), last)
        return ents, last

    def _read_disk(self, pg: int):
        self.fh.seek(pg * PAGE_SIZE)
        b = self.fh.read(PAGE_SIZE)
        n = struct.unpack_from("<I", b, 0)[0]
        ents = []
        for i in range(n):
            o = 4 + i * self.esize
            child, rec = struct.unpack_from("<II", b, o)
            ents.append((child, rec, b[o + 8 : o + 8 + self.klen]))
        last = struct.unpack_from("<I", b, 4 + n * self.esize)[0] if 8 + n * self.esize <= PAGE_SIZE else 0
        return ents, last

    def _write(self, pg: int, ents, last: int) -> None:
        if self._cache is not None:
            self._cache[pg] = (list(ents), last)
            self._dirty.add(pg)
            return
        self._write_disk(pg, ents, last)

    def _write_disk(self, pg: int, ents, last: int) -> None:
        b = bytearray(PAGE_SIZE)
        struct.pack_into("<I", b, 0, len(ents))
        for i, (child, rec, key) in enumerate(ents):
            o = 4 + i * self.esize
            struct.pack_into("<II", b, o, child, rec)
            b[o + 8 : o + 8 + self.klen] = key
        if 8 + len(ents) * self.esize <= PAGE_SIZE:
            struct.pack_into("<I", b, 4 + len(ents) * self.esize, last)
        self.fh.seek(pg * PAGE_SIZE)
        self.fh.write(bytes(b))

    def _alloc(self) -> int:
        pg = self.npages
        self.npages += 1
        if self._cache is not None:
            self._cache[pg] = ([], 0)
            self._dirty.add(pg)
            return pg
        self.fh.seek(pg * PAGE_SIZE)
        self.fh.write(bytes(PAGE_SIZE))
        return pg

    def _is_leaf(self, ents, last) -> bool:
        return last == 0 and all(c == 0 for c, _r, _k in ents)

    def key_bytes(self, value: str) -> bytes:
        """The key ``value`` is stored under (``UnencodableTextError`` when
        the code page cannot store it)."""
        return truncate(
            encode_field(value, "the %s index %s" % (self.expr or "key", os.path.basename(self.path))),
            self.klen,
        ).ljust(self.klen)

    # ---------------------------------------------------------------- insert
    def insert(self, value: str, recno: int) -> None:
        """Insert (key, 1-based record number)."""
        key = self.key_bytes(value)
        split = self._ins(self.root, key, recno)
        if split is not None:
            lpg, lkey, lrec = split
            new_root = self._alloc()
            self._write(new_root, [(lpg, lrec, lkey)], self.root)
            self.root = new_root
        self._header()

    def insert_many(self, items) -> None:
        """Insert every (value, 1-based record number) of ``items``, in order: the same
        tree as one insert() each, its pages kept in memory and each written once."""
        items = list(items)
        if not items:
            return
        keys = [(self.key_bytes(v), r) for v, r in items]  # (a bad key fails before a write)
        self._cache, self._dirty = {}, set()
        root0, npages0 = self.root, self.npages
        try:
            for key, rec in keys:
                split = self._ins(self.root, key, rec)
                if split is not None:
                    lpg, lkey, lrec = split
                    new_root = self._alloc()
                    self._write(new_root, [(lpg, lrec, lkey)], self.root)
                    self.root = new_root
            cache, dirty = self._cache, self._dirty
        except BaseException:
            self.root, self.npages = root0, npages0  # (nothing was written)
            raise
        finally:
            self._cache, self._dirty = None, set()
        for pg in sorted(dirty):
            self._write_disk(pg, *cache[pg])
        self._header()

    def _ins(self, pg: int, key: bytes, rec: int):
        ents, last = self._read(pg)
        if self._is_leaf(ents, last) or (not ents and last == 0):
            i = 0
            while i < len(ents) and _kcmp((ents[i][2], ents[i][1]), (key, rec), self.case) <= 0:
                i += 1
            ents.insert(i, (0, rec, key))
            if len(ents) <= self.kpn:
                self._write(pg, ents, 0)
                return None
            mid = (len(ents) + 1) // 2
            left, right = ents[:mid], ents[mid:]
            lpg = self._alloc()
            self._write(lpg, left, 0)
            self._write(pg, right, 0)
            return lpg, left[-1][2], left[-1][1]
        # interior: the first child whose max >= the new key
        i = 0
        while i < len(ents) and _kcmp((ents[i][2], ents[i][1]), (key, rec), self.case) < 0:
            i += 1
        child = ents[i][0] if i < len(ents) else last
        split = self._ins(child, key, rec)
        if split is None:
            return None
        lpg, lkey, lrec = split
        ents.insert(i, (lpg, lrec, lkey))
        if len(ents) <= self.kpn:
            self._write(pg, ents, last)
            return None
        mid = (len(ents) + 1) // 2
        # left keeps entries [0, mid-1) with entry mid-1's child as its trailing child
        lents = ents[: mid - 1]
        llast = ents[mid - 1][0]
        sep = ents[mid - 1]
        rents = ents[mid:]
        lpg2 = self._alloc()
        self._write(lpg2, lents, llast)
        self._write(pg, rents, last)
        return lpg2, sep[2], sep[1]

    def _header(self) -> None:
        self.fh.seek(0)
        self.fh.write(struct.pack("<II", self.root, self.npages))

    # ---------------------------------------------------------------- read
    def find(self, value: str) -> List[int]:
        """Record numbers (1-based) whose key equals ``value`` case-insensitively (with
        case for an index made with the case flag), in index order; only the subtrees whose
        key range can hold it are read."""
        fold = (lambda b: b) if self.case else (lambda b: b.lower())
        try:
            key = fold(self.key_bytes(value))
        except UnencodableTextError:
            return []  # no key can hold it
        out: List[int] = []

        def walk(pg):
            ents, last = self._read(pg)
            if self._is_leaf(ents, last):
                out.extend(r for _c, r, k in ents if fold(k) == key)
                return
            for c, _r, k in ents:
                kl = fold(k)
                if kl >= key:
                    walk(c)
                    if kl > key:
                        return
            if last:
                walk(last)

        walk(self.root)
        return out

    def entries(self) -> List[Tuple[bytes, int]]:
        """All (key, record number) in index order."""
        out: List[Tuple[bytes, int]] = []

        def walk(pg):
            ents, last = self._read(pg)
            if self._is_leaf(ents, last):
                out.extend((k, r) for _c, r, k in ents)
                return
            for c, _r, _k in ents:
                walk(c)
            if last:
                walk(last)

        walk(self.root)
        return out

    def flush(self) -> None:
        self.fh.flush()


# ------------------------------------------------------------------------------- pack
def pack_table(
    path: str, indexes: Sequence[str] = (), when: Optional[_dt.date] = None, case: bool = False
) -> int:
    """Remove deleted records from a DBF and rebuild its indexes."""
    with open(path, "rb") as fh:
        hdr = bytearray(fh.read(32))
        if len(hdr) < 32:
            raise ValueError("%s: not a DBF" % path)
        nrec, hlen, rlen = struct.unpack_from("<IHH", hdr, 4)
        fh.seek(0)
        head = bytearray(fh.read(hlen))
        dropped = 0
        tmp = path + ".pack"
        kept = 0
        try:
            with open(tmp, "wb") as out:
                out.write(bytes(head))
                per = max(1, (4 << 20) // max(1, rlen))
                i = 0
                while i < nrec:
                    k = min(per, nrec - i)
                    buf = fh.read(k * rlen)
                    n = len(buf) // rlen
                    for j in range(n):
                        rec = buf[j * rlen : (j + 1) * rlen]
                        if rec[:1] == b"*":
                            dropped += 1
                        else:
                            out.write(rec)
                            kept += 1
                    if n < k:
                        break
                    i += k
                out.write(EOF_MARK)
            if not dropped:
                os.remove(tmp)
                return 0
            d = when or _today()
            with open(tmp, "r+b") as out:
                out.seek(1)
                out.write(bytes((d.year - 1900, d.month, d.day)))
                out.seek(4)
                out.write(struct.pack("<I", kept))
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    # the indexes, rebuilt from the packed records (built beside, swapped in after)
    built = []
    try:
        for ndx_path in indexes:
            with open(ndx_path, "rb") as fh:
                h = fh.read(PAGE_SIZE)
            klen = struct.unpack_from("<H", h, 12)[0]
            expr = h[24 : 24 + 220].split(b"\0")[0].decode(CODEC).strip()
            nt = ndx_path + ".pack"
            create_ndx(nt, expr, klen)
            with open(nt, "r+b") as fh:  # keep the original's unique flag
                fh.seek(23)
                fh.write(h[23:24])
            t = Dbf(tmp, writable=False)
            try:
                if expr.upper() in t.fmap:
                    items = []
                    for r in range(t.nrec):
                        items.append((t.field(t.read(r), expr), r + 1))
                    nx = Ndx(nt, writable=False)
                    nx.close()
                    _fill_index(nt, items, case)
            finally:
                t.close()
            built.append((nt, ndx_path))
    except BaseException:
        for nt, _p in built:
            if os.path.exists(nt):
                os.remove(nt)
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, path)
    for nt, ndx_path in built:
        os.replace(nt, ndx_path)
    return dropped


def _fill_index(path: str, items, case: bool = False) -> None:
    """Insert ``items`` (value, 1-based record number) into the new index at ``path``
    (a plain file handle: the index is not yet part of the project)."""
    nx = Ndx.__new__(Ndx)
    nx.path = path
    nx.writable = True
    nx.case = case
    nx.fh = open(path, "r+b")
    try:
        h = nx.fh.read(PAGE_SIZE)
        nx.root, nx.npages = struct.unpack_from("<II", h, 0)
        nx.klen, nx.kpn, nx.ktype, nx.esize = struct.unpack_from("<HHHH", h, 12)
        nx.unique = h[23]
        nx.expr = h[24 : 24 + 220].split(b"\0")[0].decode(CODEC).strip()
        nx._cache, nx._dirty = None, set()
        nx.insert_many(items)
    finally:
        nx.fh.close()


# --------------------------------------------------------------------------- new tables
def table_bytes(
    fields: Sequence[Tuple[str, int]], rows: Sequence = (), when: Optional[_dt.date] = None
) -> bytes:
    """A new dBASE III table of character fields."""
    fields = [(str(n), int(w)) for n, w in fields]
    if not fields or len(fields) > 254:
        raise ValueError("a table has 1 to 254 fields")
    desc = bytearray()
    for name, width in fields:
        raw = name.encode("ascii")
        if not 0 < len(raw) <= 10 or not 0 < width <= 254:
            raise ValueError("field %r: names have 1 to 10 letters, widths 1 to 254" % name)
        desc += raw.ljust(11, b"\0") + b"C" + bytes(4) + bytes((width, 0)) + bytes(14)
    hlen = 32 + len(desc) + 1
    rlen = 1 + sum(w for _n, w in fields)
    d = when or _today()
    head = struct.pack("<BBBBIHH20x", 3, d.year - 1900, d.month, d.day, len(rows), hlen, rlen)
    body = bytearray()
    for row in rows:
        if isinstance(row, dict):
            up = {str(k).upper(): v for k, v in row.items()}
            unknown = set(up) - {n.upper() for n, _w in fields}
            if unknown:
                raise ValueError("no such field: %s" % ", ".join(sorted(unknown)))
            vals = [up.get(n.upper(), "") for n, _w in fields]
        else:
            if len(row) > len(fields):
                raise ValueError("a row has %d values, the table has %d fields" % (len(row), len(fields)))
            vals = list(row) + [""] * (len(fields) - len(row))
        body += b" "
        for (name, width), v in zip(fields, vals):
            raw = encode_field("" if v is None else v, "the %s field" % name)
            body += truncate(raw, width).ljust(width, b" ")
    return head + bytes(desc) + b"\x0d" + bytes(body) + EOF_MARK


def create_table(
    path: str,
    fields: Sequence[Tuple[str, int]],
    rows: Sequence = (),
    when: Optional[_dt.date] = None,
    indexes: Sequence[str] = (),
) -> None:
    """Write a new table (:func:`table_bytes`) at ``path``, replacing a file there, and an
    index ``<table>.NDX`` beside it for each field named in ``indexes`` (the Project
    Editor's layout, its keys from the rows; one index per table, so at most one field)."""
    if len(indexes) > 1:
        raise ValueError("one index per table (<table>.NDX)")
    with open(path, "wb") as fh:
        fh.write(table_bytes(fields, rows, when))
    for field in indexes:
        width = dict((str(n).upper(), int(w)) for n, w in fields).get(field.upper())
        if width is None:
            raise ValueError("no such field: %s" % field)
        ndx = os.path.splitext(path)[0] + ".NDX"
        create_ndx(ndx, field.upper(), width)
        t = Dbf(path, writable=False)
        try:
            items = [(t.field(t.read(r), field), r + 1) for r in range(t.nrec)]
        finally:
            t.close()
        if items:
            _fill_index(ndx, items)
