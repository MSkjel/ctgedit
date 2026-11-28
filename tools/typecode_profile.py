"""Profile type codes against object kinds."""

from __future__ import annotations

import collections
import glob
import json
import os
import sys

EX = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\*.ctg"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.format.page import parse_ctg  # noqa: E402

CAT = json.load(
    open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ctgedit", "format", "catalog.json"
        ),
        encoding="utf-8",
    )
)
KEY_FAMILY = {p["key"]: p.get("family", "") for p in CAT.get("properties", [])}
KEY_APPLIES = {p["key"]: p.get("applies", []) for p in CAT.get("properties", [])}


def main():
    by_type = collections.defaultdict(
        lambda: {
            "count": 0,
            "class_flags": collections.Counter(),
            "keys": collections.Counter(),
            "families": collections.Counter(),
            "has_text_expr": 0,
            "has_font": 0,
            "shape": collections.Counter(),  # square-ish / line-ish / wide / tall
            "pages": set(),
        }
    )
    files = glob.glob(EX)
    for fp in files:
        try:
            parsed = parse_ctg(fp)
        except Exception as e:  # noqa: BLE001
            print("FAIL", os.path.basename(fp), e, file=sys.stderr)
            continue
        if parsed.page is None:
            continue
        name = os.path.basename(fp)
        for po in parsed.page.objects:
            tc = po.type_code
            d = by_type[tc]
            d["count"] += 1
            d["class_flags"][po.class_flag] += 1
            d["pages"].add(name)
            rp = po.raw_properties()
            for k in rp:
                base = _strip_index(k)
                d["keys"][base] += 1
                fam = KEY_FAMILY.get(base) or KEY_FAMILY.get(k) or ""
                if fam:
                    d["families"][fam] += 1
            if po.text_exprs:
                d["has_text_expr"] += 1
            if po.font_face:
                d["has_font"] += 1
            # crude shape from rect1 in geometry header
            sh = _shape(po.body_raw)
            if sh:
                d["shape"][sh] += 1

    out = {}
    for tc in sorted(by_type, key=lambda t: -by_type[t]["count"]):
        d = by_type[tc]
        top_keys = [k for k, _ in d["keys"].most_common(18)]
        top_fams = d["families"].most_common(8)
        out[tc] = {
            "type_code_hex": hex(tc),
            "count": d["count"],
            "n_pages": len(d["pages"]),
            "class_flags": dict(d["class_flags"].most_common(6)),
            "has_text_expr": d["has_text_expr"],
            "has_font": d["has_font"],
            "shape": dict(d["shape"]),
            "top_families": top_fams,
            "top_keys": top_keys,
        }
    json.dump(out, sys.stdout, indent=1, default=str)


def _strip_index(k: str) -> str:
    # collapse FOO_3 / FOO3 / STATE_12 to a base token for family lookup
    import re

    m = re.match(r"^(.*?)(_?\d+)$", k)
    if m and m.group(1):
        return m.group(1)
    return k


def _shape(body: bytes):
    """Read rect1 from the geometry header; classify aspect."""
    import struct
    from ctgedit.format.page import XFORM_DOUBLES

    o = 0
    n = len(body)
    if o + 6 > n:
        return None
    o += 6  # u16 reserved + u32 handle
    for _ in range(3):
        if o >= n:
            return None
        mode = body[o]
        o += 1
        nd = XFORM_DOUBLES.get(mode, 6 if mode > 3 else 0)
        if o + nd * 8 > n:
            return None
        o += nd * 8
    if o + 4 + 8 + 8 > n:
        return None
    o += 4  # anchor
    try:
        l, t, r, b = struct.unpack_from("<4h", body, o)
    except struct.error:
        return None
    w = abs(r - l)
    h = abs(b - t)
    if w == 0 and h == 0:
        return "point"
    if h == 0 or w == 0:
        return "line"
    if w > 4 * h:
        return "wide"
    if h > 4 * w:
        return "tall"
    return "box"


if __name__ == "__main__":
    main()
