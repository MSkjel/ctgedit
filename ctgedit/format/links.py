"""The link / import table in the page head."""

from __future__ import annotations

import re
import struct
from typing import List, Optional, Tuple
from .textcodec import CODEC

LINK_ENTRY_SIZE = 0x60

_STAMP_RE = re.compile(r"#[0-9a-fA-F]{8}$")


def strip_stamp(name: str) -> str:
    """Drop the trailing ``#%08x`` save stamp (CtDraw strips it at load)."""
    return _STAMP_RE.sub("", name)


def entry_stamp(name: str):
    """The trailing ``#%08x`` save stamp of a link-table name as an int (None without one):
    the stamp of the element version the entry was saved with."""
    m = _STAMP_RE.search(name)
    return int(m.group(0)[1:], 16) if m else None


def parse_link_table(raw: bytes, legacy: bool = False) -> Optional[Tuple[str, List[Tuple[bytes, str]]]]:
    """Decode the region into ``(area_code, [(guid16, reference), ...])``.
    Returns None when the region is absent/malformed (never raises). ``legacy`` = a
    word2 < 0x0C file, whose entries are the bare reference (CtDraw
    copies the 16-byte guid only from a current file): the guid comes back zero."""
    if len(raw) < 12:
        return ("    ", []) if not raw else None
    try:
        area = raw[0:4].decode(CODEC, "replace")
        n, total = struct.unpack_from("<II", raw, 4)
        if n > 4096 or total > len(raw):
            return None
        offs = struct.unpack_from("<%dI" % n, raw, 12) if n else ()
        base = 12 + 4 * n
        if base + total > len(raw):
            return None
        area_bytes = raw[base : base + total]
        entries: List[Tuple[bytes, str]] = []
        for i, off in enumerate(offs):
            end = offs[i + 1] if i + 1 < n else total
            blob = area_bytes[off:end]
            if legacy:
                entries.append((b"\x00" * 16, blob.split(b"\x00", 1)[0].decode(CODEC, "replace")))
                continue
            if len(blob) < 16:
                return None
            guid = bytes(blob[:16])
            name = blob[16:].split(b"\x00", 1)[0].decode(CODEC, "replace")
            entries.append((guid, name))
        return area, entries
    except struct.error, ValueError:
        return None


def serialize_link_table(area_code: str, entries: List[Tuple[bytes, str]]) -> Tuple[bytes, int]:
    """Re-serialise; returns ``(region_bytes, link_count)``. No entries = NO
    table (b'', 0) -- CtDraw writes link_count 0, not a zeroed 0x60 block."""
    if not entries:
        return b"", 0
    blobs = []
    offs = []
    pos = 0
    for guid, name in entries:
        offs.append(pos)
        blob = bytes(guid[:16]).ljust(16, b"\x00") + name.encode(CODEC, "replace") + b"\x00"
        blobs.append(blob)
        pos += len(blob)
    total = pos
    n = len(entries)
    # (CtDraw always writes four spaces, and the loader refuses a page whose
    # table starts otherwise; area_code is kept for its callers)
    body = b"    " + struct.pack("<II", n, total) + struct.pack("<%dI" % n, *offs) + b"".join(blobs)
    link_count = (12 + 4 * n + total + LINK_ENTRY_SIZE - 1) // LINK_ENTRY_SIZE
    return body.ljust(link_count * LINK_ENTRY_SIZE, b"\x00"), link_count


def super_genie_attachments(entries: List[Tuple[bytes, str]]) -> List[str]:
    """The attached-super-genie display names (text before the first '@')."""
    out = []
    for _guid, name in entries:
        clean = strip_stamp(name)
        if clean.startswith("\\\\"):
            continue
        if "@super" in clean.lower():
            out.append(clean.split("@", 1)[0])
    return out


def set_super_genie_attachments(
    entries: List[Tuple[bytes, str]], names: List[str], resolve=None
) -> List[Tuple[bytes, str]]:
    """Replace the @SUPER entries in a link table."""
    keep = [
        (g, nm)
        for g, nm in entries
        if "@super" not in strip_stamp(nm).lower() or strip_stamp(nm).startswith("\\\\")
    ]
    had = {}
    for g, nm in entries:
        base = strip_stamp(nm)
        if "@super" in base.lower() and not base.startswith("\\\\"):
            stamp = nm[len(base) + 1 :] if len(nm) > len(base) else ""
            had.setdefault(base.split("@", 1)[0].lower(), (bytes(g), stamp))
    seen = set()
    out = list(keep)
    for name in names:
        name = name.strip().split("@", 1)[0]
        key = name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        guid, stamp = b"\x00" * 16, 0
        hit = None
        if resolve is not None:
            try:
                hit = resolve(name)
            except Exception:  # noqa: BLE001
                hit = None
        if hit is not None:
            stamp = hit[1]
        old = had.get(key)
        if old is not None:
            guid = old[0]
            if hit is None:
                try:
                    stamp = int(old[1], 16) if old[1] else 0
                except ValueError:
                    stamp = 0
        out.append((guid, "%s@SUPER#%08x" % (name, stamp)))
    return out


def entry_key(name: str) -> Tuple[str, str]:
    """What makes two entries of one table the same entry: a label ('\\\\native') by its
    native, case-insensitively (one label per native, genie_place.label_key); a reference by
    its name without the stamp, case-insensitively (genie_place.add_link_entry's rule)."""
    if name.startswith("\\"):
        return "\\", name.lower()
    return "", strip_stamp(name).lower()


def merge_link_entries(
    base: List[Tuple[bytes, str]], theirs: List[Tuple[bytes, str]], mine: List[Tuple[bytes, str]]
) -> List[Tuple[bytes, str]]:
    """Three-way merge of link tables: ``mine`` plus what changed from ``base`` to ``theirs``."""

    def keyed(ents):
        seen: dict = {}
        out = []
        for g, n in ents:
            k = entry_key(n)
            c = seen.get(k, 0)
            seen[k] = c + 1
            out.append(((k, c), (bytes(g), n)))
        return out

    b = dict(keyed(base))
    t_rows = keyed(theirs)
    t = dict(t_rows)
    rows = []
    for k, e in keyed(mine):
        if b.get(k) != t.get(k) and e == b.get(k):
            if t.get(k) is None:
                continue  # (the update removed it)
            e = t[k]  # (the update restamped it)
        rows.append((k, e))
    have = {k for k, _e in rows}
    for i, (k, e) in enumerate(t_rows):
        if k in b or k in have:
            continue  # (not added by the update, or added by both)
        at = 0
        for j in range(i - 1, -1, -1):
            prev = t_rows[j][0]
            idx = next((x for x, (kk, _e) in enumerate(rows) if kk == prev), None)
            if idx is not None:
                at = idx + 1
                break
        rows.insert(at, (k, e))
        have.add(k)
    return [e for _k, e in rows]


def changed_references(base: List[Tuple[bytes, str]], theirs: List[Tuple[bytes, str]]) -> set:
    """The references (a reference's :func:`entry_key`: its name without the stamp, lower
    case) whose entry ``theirs`` restamped or added against ``base``: what an update says is
    current now."""
    b: dict = {}
    for g, n in base:
        b.setdefault(entry_key(n), (bytes(g), n))
    out = set()
    for g, n in theirs:
        k = entry_key(n)
        if not k[0] and b.get(k) != (bytes(g), n):
            out.add(k[1])
    return out
