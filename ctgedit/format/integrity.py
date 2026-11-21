"""Check that a page file is complete."""

from __future__ import annotations

import os
import struct
from typing import List

from .upgrade import MIN_WORD2

HEADER = 0x10


class DamagedFileError(ValueError):
    """The file is damaged or truncated (the message names it and says how)."""


#: the loader's limits on a page's size (CtDraw)
PAGE_EXTENT_MAX = 0x4000
PAGE_AREA_MAX = 0x1000000


def check_openable(path: str, data: bytes) -> None:
    name = os.path.basename(path)
    if not data:
        raise DamagedFileError(
            "%s could not be opened: the file is empty (0 bytes). A copy or save was "
            "probably interrupted. Restore it from a backup." % name
        )
    if len(data) < HEADER:
        raise DamagedFileError(
            "%s could not be opened: the file is damaged (only %d bytes). Restore it from a "
            "backup." % (name, len(data))
        )
    version, word2 = struct.unpack_from("<HH", data, 0)
    if version == 0 or version > 0x40 or word2 > 0x100:
        raise DamagedFileError(
            "%s could not be opened: it is not a Citect graphics page, or its header is damaged." % name
        )
    if not 7 <= version <= 9:
        # CtDraw opens only versions 7 to 9 (7 and 8 for reading; a save
        # writes a version 9 file, LIVE)
        raise DamagedFileError(
            "%s could not be opened: its file version is %d, and the Graphics Builder opens "
            "versions 7 to 9 only." % (name, version)
        )
    if word2 < MIN_WORD2:
        # LIVE: CtDraw's PageOpen fails on a word2 4 page too
        raise DamagedFileError(
            "%s could not be opened: it is in an old page format (form %d) that the Graphics "
            "Builder does not open either." % (name, word2)
        )


def problems(parsed, data_len: int) -> List[str]:
    """What is wrong with a parsed page (``format.page.ParsedCtg``) whose
    file is ``data_len`` bytes long; [] when nothing is."""
    out: List[str] = []
    cont = parsed.container
    for b in cont.blocks:
        if getattr(cont, "upgraded_from", 0):
            break  # the blocks are the upgraded form, not the file's own bytes
        if b.size and b.offset + b.size > data_len and (b.tag & 0xFF):
            out.append(
                "the file is truncated: its %r block should end at byte %d, but the "
                "file has only %d bytes" % (b.name, b.offset + b.size, data_len)
            )
    pb = parsed.page
    if pb is None:
        return out
    if len(pb.objects) < pb.object_count:
        out.append(
            "only %d of the page's %d objects could be read%s"
            % (
                len(pb.objects),
                pb.object_count,
                " (the page is in an older file format, which ctgedit may not read completely)"
                if pb.head_size < 0x34
                else "",
            )
        )
    elif pb.objdata_consumed != pb.objdata_size:
        out.append(
            "the object data does not match its recorded size (%d of %d bytes)"
            % (pb.objdata_consumed, pb.objdata_size)
        )
    if pb.data_start + pb.objdata_size > pb.block_size:
        out.append(
            "the object data runs past the end of the page block (%d of %d bytes "
            "present)" % (max(0, pb.block_size - pb.data_start), pb.objdata_size)
        )
    elif pb.thumb_size and len(pb.thumbnail) < pb.thumb_size:
        out.append("the page thumbnail is cut short")
    # the record forest: every group's child count must be satisfied
    pending: List[int] = []
    for o in pb.objects:
        while pending and pending[-1] == 0:
            pending.pop()
        if pending:
            pending[-1] -= 1
        if o.class_flag:
            pending.append(o.class_flag)
    missing = sum(pending)
    if missing and len(pb.objects) == pb.object_count:
        out.append("a group expects %d more member record(s) than the page holds" % missing)
    if parsed.attributes is None:
        out.append("the page attributes block (size, background, page properties) is missing")
    # the record chain: CtDraw's reader follows next_link (a
    # broken link = 'Objects Lost'), so every record's link must lead to the next
    off = pb.data_start
    for k, o in enumerate(pb.objects):
        nxt = off + 8 + o.payload_size
        last = k + 1 == len(pb.objects)
        want = 0 if last else nxt - pb.data_start
        if o.next_link != want:
            out.append(
                "record %d links to byte %d of the object data, but %s: the Graphics "
                "Builder would stop reading there"
                % (
                    k,
                    o.next_link,
                    "it is the last record" if last else "the next record starts at byte %d" % want,
                )
            )
            break
        off = nxt
    # the loader's extent limits (CtDraw): a page wider or higher than
    # 16384 pixels, or over 16 Mpixels, does not open
    w, h = pb.page_width, pb.page_height
    if w > PAGE_EXTENT_MAX or h > PAGE_EXTENT_MAX or w * h > PAGE_AREA_MAX:
        out.append(
            "the page size %d x %d is beyond what the Graphics Builder opens (at most "
            "%d pixels a side, %d million pixels in all)" % (w, h, PAGE_EXTENT_MAX, PAGE_AREA_MAX // 1000000)
        )
    return out
