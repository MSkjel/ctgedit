"""Stress the .ctg parser.

python tools/verify_parser.py [--ctf] [--json out.json]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import traceback
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from ctgedit.format.container import Container
from ctgedit.format import page as _page
from ctgedit.format.page import parse_page, decode_value
from ctgedit.format.attributes import parse_attributes

# this tool reports the body-coverage metric, so enable its (otherwise-skipped) computation
_page._WANT_COVERAGE = True

EXAMPLE_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"


@dataclass
class FileReport:
    path: str
    name: str
    kind: str  # "ctg" or "ctF"
    size: int
    ok: bool = False
    error: str = ""

    # container
    container_tiles: bool = False
    n_blocks: int = 0

    # page
    has_page: bool = False
    object_count: int = 0  # header-declared
    objects_parsed: int = 0  # records actually walked
    objdata_size: int = 0
    objdata_consumed: int = 0
    objdata_tiles: bool = False  # walk lands exactly on objdata_size
    count_matches: bool = False  # objects_parsed == object_count
    short_record: bool = False  # a record header/payload ran off the end
    thumb_size: int = 0
    trailing_pad_zero: bool = False
    body_bytes: int = 0
    body_covered: int = 0
    decode_pct: float = 0.0  # body_covered / body_bytes (weak metric)
    n_bags: int = 0
    n_property_bags: int = 0

    # full-block byte accounting + framing invariants
    block_size: int = 0
    block_accounted: int = 0  # head + links + objdata + thumb + pad
    full_block_tiles: bool = False  # block fully accounted AND trailing pad all-zero
    next_link_ok: bool = False  # cumulative record-end invariant holds for all records
    an_str_int_ok: bool = False  # every record's an_str parses as an int
    records_no_bags: int = 0  # objects with zero discovered structure (pure binary)

    # attributes
    has_attrs: bool = False
    attrs_consumes_all: bool = False
    attrs_pad_zero: bool = False
    identity_match: bool = False
    extent_match: bool = False

    # cross
    notes: List[str] = field(default_factory=list)


def verify_file(path: str) -> FileReport:
    name = os.path.basename(path)
    kind = "ctF" if path.lower().endswith(".ctf") else "ctg"
    rep = FileReport(path=path, name=name, kind=kind, size=os.path.getsize(path))
    try:
        data = open(path, "rb").read()
        c = Container.parse(data)
        rep.n_blocks = len(c.blocks)

        # container tiling: each block ends where the next begins, the last at EOF
        blocks = sorted(c.blocks, key=lambda b: b.offset)
        tiles = True
        if blocks:
            prev_end = blocks[0].offset
            for b in blocks:
                if b.offset != prev_end:
                    tiles = False
                prev_end = b.offset + b.size
            if prev_end != len(data):
                tiles = False
        rep.container_tiles = tiles

        pg_block = c.block("page")
        at_block = c.block("attributes")

        attrs = None
        if at_block is not None:
            rep.has_attrs = True
            attrs = parse_attributes(at_block)
            rep.attrs_consumes_all = attrs.bytes_consumed == attrs.block_size
            rep.attrs_pad_zero = attrs.trailing_pad_zero

        if pg_block is not None:
            rep.has_page = True
            page = parse_page(pg_block, c.word2)
            rep.object_count = page.object_count
            rep.objects_parsed = len(page.objects)
            rep.objdata_size = page.objdata_size
            rep.objdata_consumed = page.objdata_consumed
            rep.objdata_tiles = page.objdata_consumed == page.objdata_size
            rep.count_matches = len(page.objects) == page.object_count
            rep.thumb_size = page.thumb_size
            rep.trailing_pad_zero = page.trailing_pad_zero

            # short record detection: walked fewer records than declared,
            # or a payload spilled past the block.
            d = pg_block.data
            if len(page.objects) < page.object_count:
                rep.short_record = True
            for o in page.objects:
                rec_end = o.record_offset + 8 + o.payload_size
                if rec_end > len(d):
                    rep.short_record = True

            rep.body_bytes = sum(len(o.body_raw) for o in page.objects)
            rep.body_covered = sum(o.bytes_covered for o in page.objects)
            rep.decode_pct = (100.0 * rep.body_covered / rep.body_bytes) if rep.body_bytes else 100.0
            rep.n_bags = sum(len(o.bags) for o in page.objects)
            rep.n_property_bags = sum(1 for o in page.objects for b in o.bags if b.is_property)
            rep.records_no_bags = sum(1 for o in page.objects if not o.bags)

            # ---- FULL-BLOCK byte accounting -------------------------
            # The whole page block must tile as:
            #   head + link_table + objdata_size + thumb_size + trailing_pad
            # and the trailing pad must be all-zero. This is the strong coverage
            # metric (100% of every block byte attributed to a named region).
            region_end = page.data_start + page.objdata_size
            pad_start = region_end + page.thumb_size
            pad_len = len(d) - pad_start
            rep.block_size = len(d)
            rep.block_accounted = pad_start + max(pad_len, 0)
            rep.full_block_tiles = (
                rep.block_accounted == rep.block_size
                and pad_len >= 0
                and page.trailing_pad_zero
                and rep.objdata_tiles
            )

            # ---- record-framing INVARIANTS --------------------------
            # next_link[i] is the cumulative record-end offset relative to
            # data_start: sum(payload_size+8 for records 0..i); 0 on the last
            # record (terminator). an_str must always parse as an int.
            cum = 0
            n_obj = len(page.objects)
            nl_ok = True
            an_ok = True
            for i, o in enumerate(page.objects):
                cum += o.payload_size + 8
                if i == n_obj - 1:
                    if o.next_link != 0:
                        nl_ok = False
                elif o.next_link != cum:
                    nl_ok = False
                try:
                    int(o.an_str)
                except ValueError, TypeError:
                    an_ok = False
            rep.next_link_ok = nl_ok and n_obj > 0
            rep.an_str_int_ok = an_ok

            if attrs is not None:
                rep.identity_match = page.page_identity == attrs.page_identity
                rep.extent_match = page.page_width == attrs.width and page.page_height == attrs.height

        rep.ok = True
    except Exception as e:  # noqa: BLE001 -- adversarial: catch everything
        rep.ok = False
        rep.error = "%s: %s" % (type(e).__name__, e)
        rep.notes.append(traceback.format_exc().splitlines()[-1])
    return rep


def collect_property_stats(paths: List[str]):
    """Aggregate raw property keys + value-kinds across all parsed objects."""
    key_counter: Counter = Counter()
    known_key_counter: Counter = Counter()
    unknown_key_counter: Counter = Counter()
    kind_counter: Counter = Counter()
    typecode_counter: Counter = Counter()
    classflag_counter: Counter = Counter()
    from ctgedit.format.page import _key_is_known

    for path in paths:
        try:
            data = open(path, "rb").read()
            c = Container.parse(data)
            pg = c.block("page")
            if pg is None:
                continue
            page = parse_page(pg, c.word2)
            for o in page.objects:
                typecode_counter[o.type_code] += 1
                classflag_counter[o.class_flag] += 1
                for bag in o.bags:
                    for k, v in bag.pairs:
                        key_counter[k] += 1
                        if _key_is_known(k):
                            known_key_counter[k] += 1
                        else:
                            unknown_key_counter[k] += 1
                        dv = decode_value(k, v)
                        kind_counter[dv.kind] += 1
        except Exception:
            continue
    return {
        "all_keys": key_counter,
        "known_keys": known_key_counter,
        "unknown_keys": unknown_key_counter,
        "value_kinds": kind_counter,
        "type_codes": typecode_counter,
        "class_flags": classflag_counter,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctf", action="store_true", help="also test .ctF cache siblings")
    ap.add_argument("--dir", default=EXAMPLE_DIR)
    ap.add_argument("--json", default="")
    ap.add_argument("--worst", type=int, default=15)
    args = ap.parse_args(argv)

    ctg_paths = sorted(glob.glob(os.path.join(args.dir, "*.ctg")))
    ctf_paths = sorted(glob.glob(os.path.join(args.dir, "*.ctF")))

    targets = list(ctg_paths)
    if args.ctf:
        targets += ctf_paths

    reports = [verify_file(p) for p in targets]

    def bucket(kind: str) -> List[FileReport]:
        return [r for r in reports if r.kind == kind]

    for kind in ["ctg", "ctF"] if args.ctf else ["ctg"]:
        rs = bucket(kind)
        if not rs:
            continue
        n = len(rs)
        parsed = [r for r in rs if r.ok]
        n_ok = len(parsed)
        with_page = [r for r in parsed if r.has_page]
        n_tiles = sum(1 for r in with_page if r.objdata_tiles)
        n_count = sum(1 for r in with_page if r.count_matches)
        n_short = sum(1 for r in with_page if r.short_record)
        n_ctiles = sum(1 for r in parsed if r.container_tiles)
        n_identity = sum(1 for r in parsed if r.identity_match)
        n_extent = sum(1 for r in parsed if r.extent_match)
        n_attrs_full = sum(1 for r in parsed if r.attrs_consumes_all)
        decode_pcts = [r.decode_pct for r in with_page]
        med_decode = statistics.median(decode_pcts) if decode_pcts else 0.0
        n_fullblock = sum(1 for r in with_page if r.full_block_tiles)
        n_nextlink = sum(1 for r in with_page if r.next_link_ok)
        n_anstr = sum(1 for r in with_page if r.an_str_int_ok)

        print("=" * 78)
        print("BUCKET %s : %d files" % (kind, n))
        print("=" * 78)
        print("  parse without exception : %d / %d" % (n_ok, n))
        print("  container tiles file     : %d / %d" % (n_ctiles, n_ok))
        print("  has page block           : %d / %d" % (len(with_page), n_ok))
        print("  objdata region tiles     : %d / %d" % (n_tiles, len(with_page)))
        print("  object_count matches     : %d / %d" % (n_count, len(with_page)))
        print("  short/missing record     : %d / %d" % (n_short, len(with_page)))
        print(
            "  FULL page-block tiles    : %d / %d  (head+links+objdata+thumb+pad, pad all-zero)"
            % (n_fullblock, len(with_page))
        )
        print(
            "  next_link invariant ok   : %d / %d  (cumulative record-end; 0 on last)"
            % (n_nextlink, len(with_page))
        )
        print("  an_str parses as int     : %d / %d" % (n_anstr, len(with_page)))
        print("  attrs consumes full block: %d / %d" % (n_attrs_full, n_ok))
        print("  page<->attrs identity    : %d / %d" % (n_identity, n_ok))
        print("  page<->attrs extent      : %d / %d" % (n_extent, n_ok))
        print("  median decode-coverage   : %.1f%% (weak metric; binary header undecoded)" % med_decode)

        # worst files: the strong structural invariants first, the weak decode metric last
        def worst_key(r: FileReport):
            return (
                0 if r.ok else -1,  # exceptions first
                0 if r.full_block_tiles else -1,  # then incomplete block accounting
                0 if r.objdata_tiles else -1,  # then non-tiling objdata
                0 if r.count_matches else -1,  # then count mismatch
                0 if r.next_link_ok else -1,  # then broken record links
                r.decode_pct,  # finally low decode coverage
            )

        worst = sorted(rs, key=worst_key)[: args.worst]
        print("\n  WORST %d FILES (ranked by strong invariants, then decode%%):" % len(worst))
        print(
            "    %-32s %8s %7s %4s %4s %4s %4s %7s %s"
            % ("file", "size", "objs", "full", "tile", "cnt", "link", "decode%", "note")
        )
        for r in worst:
            note = r.error if not r.ok else ""
            print(
                "    %-32s %8d %3d/%-3d %4s %4s %4s %4s %7.1f %s"
                % (
                    r.name[:32],
                    r.size,
                    r.objects_parsed,
                    r.object_count,
                    "Y" if r.full_block_tiles else "N",
                    "Y" if r.objdata_tiles else "N",
                    "Y" if r.count_matches else "N",
                    "Y" if r.next_link_ok else "N",
                    r.decode_pct,
                    note,
                )
            )
        print()

    # property stats (ctg only)
    stats = collect_property_stats(ctg_paths)
    print("=" * 78)
    print("PROPERTY / VALUE STATS (ctg)")
    print("=" * 78)
    print(
        "  distinct keys: %d  (known: %d, unknown: %d)"
        % (len(stats["all_keys"]), len(stats["known_keys"]), len(stats["unknown_keys"]))
    )
    print("  value-kind histogram:")
    for kind, cnt in stats["value_kinds"].most_common():
        print("      %-14s %d" % (kind, cnt))
    print("  top 25 UNKNOWN keys (undecoded property candidates):")
    for k, cnt in stats["unknown_keys"].most_common(25):
        print("      %-28s %d" % (repr(k), cnt))
    print("  type_code histogram (top 20):")
    for tc, cnt in stats["type_codes"].most_common(20):
        print("      0x%-6X %d" % (tc, cnt))
    print("  class_flag histogram (top 20):")
    for cf, cnt in stats["class_flags"].most_common(20):
        print("      0x%-6X %d" % (cf, cnt))

    if args.json:
        out = {
            "files": [vars(r) for r in reports],
            "unknown_keys": dict(stats["unknown_keys"].most_common()),
            "value_kinds": dict(stats["value_kinds"]),
            "type_codes": {hex(k): v for k, v in stats["type_codes"].items()},
        }
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print("\nwrote %s" % args.json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
