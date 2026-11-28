"""Correlate type codes with object kinds across the example pages."""

from __future__ import annotations

import glob
import io
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ctgedit.format.page import parse_ctg, extract_bmp  # noqa: E402
from ctgedit.io.reader import read_geometry  # noqa: E402

EXAMPLE_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"

try:
    from PIL import Image
    import numpy as np

    HAVE_PIL = True
except Exception:  # noqa: BLE001
    HAVE_PIL = False


# --------------------------------------------------------------------------- keys
# Which animation/property families a given key set implies (for inference).
def family_flags(keys: set[str]) -> set[str]:
    fam = set()
    for k in keys:
        if k.startswith("TXT") or k in (
            "FORMAT",
            "ENG_ZERO",
            "ENG_FULL",
            "ENG_UNITS",
            "RAW_ZERO",
            "RAW_FULL",
        ):
            fam.add("text/number")
        if k.startswith("VERTEX_"):
            fam.add("vertex")
        if k in ("SYMBOL", "SET_ARR") or k.startswith("SYMBOL_") or k.startswith("STATE_"):
            fam.add("symbolset")
        if k.startswith("COL"):
            fam.add("fillcolour")
        if k.startswith("FIL"):
            fam.add("filllevel")
        if k.startswith("MV") or k.startswith("SZ") or k.startswith("ROT"):
            fam.add("transform")
        if k in ("CMD", "ACTION", "DOWN_CMD", "REPEAT_CMD", "KEY_SEQ", "KEY_LBUTTON"):
            fam.add("input")
        if k in ("TEXT", "WIDTH", "HEIGHT"):
            fam.add("button?")
        if k in (
            "Table",
            "Calendar",
            "Rows",
            "Cols",
            "Col",
            "RowHeight",
            "HdrHeight",
            "Items",
            "ExpandLevel",
            "Equip",
            "Equipment",
        ):
            fam.add("widget/genie")
    return fam


# --------------------------------------------------------------------------- thumb
def thumb_image(pb):
    if not HAVE_PIL or not pb.thumbnail:
        return None
    bmp = extract_bmp(pb.thumbnail)
    if not bmp:
        return None
    try:
        img = Image.open(io.BytesIO(bmp)).convert("RGB")
        return np.asarray(img)  # shape (H, W, 3)
    except Exception:  # noqa: BLE001
        return None


def classify_region(arr, pw, ph, l, t, r, b):
    """Classify the appearance of a bbox in the thumbnail."""
    if arr is None or pw <= 0 or ph <= 0:
        return None
    H, W = arr.shape[0], arr.shape[1]
    sx = W / float(pw)
    sy = H / float(ph)
    x0 = max(0, int(round(min(l, r) * sx)))
    x1 = min(W, int(round(max(l, r) * sx)))
    y0 = max(0, int(round(min(t, b) * sy)))
    y1 = min(H, int(round(max(t, b) * sy)))
    if x1 <= x0:
        x1 = min(W, x0 + 1)
    if y1 <= y0:
        y1 = min(H, y0 + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = arr[y0:y1, x0:x1, :]
    if crop.size == 0:
        return None
    pix = crop.reshape(-1, 3)
    # quantize to reduce AA noise
    q = (pix // 24).astype(np.int32)
    codes = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    uniq, counts = np.unique(codes, return_counts=True)
    ncol = len(uniq)
    npix = len(codes)
    dominant = counts.max() / float(npix)
    label = "busy"
    if npix < 4:
        label = "tiny"
    elif ncol <= 2 and dominant > 0.9:
        label = "uniform_fill"
    elif dominant > 0.7 and ncol <= 6:
        label = "uniform_fill"
    elif ncol <= 8 and dominant > 0.45:
        label = "outline_or_text"
    else:
        label = "busy"
    return (label, ncol, round(dominant, 2), npix)


# --------------------------------------------------------------------------- main
def main():
    files = sorted(glob.glob(os.path.join(EXAMPLE_DIR, "*.ctg")))
    print("=" * 90)
    print("type_code -> kind correlation over %d example pages   (PIL=%s)" % (len(files), HAVE_PIL))
    print("=" * 90)

    # per type_code aggregates
    count = Counter()
    files_with = defaultdict(set)
    classflag = defaultdict(Counter)
    subtype = defaultdict(Counter)
    an_present = defaultdict(lambda: [0, 0])  # [with_an, without_an]
    key_families = defaultdict(Counter)  # family -> count
    key_tokens = defaultdict(Counter)  # raw key -> count
    has_font = defaultdict(lambda: [0, 0])  # [font, no_font]
    has_text_expr = defaultdict(lambda: [0, 0])
    rect1_degenerate = defaultdict(lambda: [0, 0])  # [degenerate(point), real-area]
    rect2_vs_rect1 = defaultdict(Counter)  # which is bigger
    aspect_samples = defaultdict(list)  # (w,h) of rect1 (or rect2 if r1 degenerate)
    thumb_labels = defaultdict(Counter)
    examples = defaultdict(list)  # small concrete examples

    for f in files:
        base = os.path.basename(f)
        try:
            parsed = parse_ctg(f)
        except Exception as e:  # noqa: BLE001
            print("  !! parse failed %s: %s" % (base, e))
            continue
        pb = parsed.page
        if pb is None:
            continue
        arr = thumb_image(pb)
        pw, ph = pb.page_width, pb.page_height
        for o in pb.objects:
            tc = o.type_code
            count[tc] += 1
            files_with[tc].add(base)
            classflag[tc][o.class_flag] += 1
            subtype[tc][o.subtype_size] += 1
            an_present[tc][0 if o.an_id != -1 else 1] += 1

            keys = set(o.raw_properties().keys())
            for fam in family_flags(keys):
                key_families[tc][fam] += 1
            for k in keys:
                key_tokens[tc][k] += 1

            has_font[tc][0 if o.font_face else 1] += 1
            has_text_expr[tc][0 if o.text_exprs else 1] += 1

            g = read_geometry(o.body_raw, 0)
            r1 = g["rect1"] if g else None
            r2 = g["rect2"] if g else None
            box = None
            if r1:
                l, t, rr, bb = r1
                w1, h1 = abs(rr - l), abs(bb - t)
                degen = w1 <= 1 and h1 <= 1
                rect1_degenerate[tc][0 if degen else 1] += 1
                box = r1 if not degen else (r2 or r1)
                if r2:
                    l2, t2, r2r, b2 = r2
                    a1 = w1 * h1
                    a2 = abs(r2r - l2) * abs(b2 - t2)
                    if a2 > a1 + 1:
                        rect2_vs_rect1[tc]["r2>r1"] += 1
                    elif a1 > a2 + 1:
                        rect2_vs_rect1[tc]["r1>r2"] += 1
                    else:
                        rect2_vs_rect1[tc]["equal"] += 1
            if box:
                bl, bt, br, bb = box
                bw, bh = abs(br - bl), abs(bb - bt)
                aspect_samples[tc].append((bw, bh))
                if arr is not None:
                    cls = classify_region(arr, pw, ph, bl, bt, br, bb)
                    if cls:
                        thumb_labels[tc][cls[0]] += 1
            # collect up to 4 concrete examples per type
            if len(examples[tc]) < 4:
                examples[tc].append(
                    {
                        "file": base,
                        "cf": o.class_flag,
                        "sub": o.subtype_size,
                        "an": o.an_str,
                        "r1": r1,
                        "r2": r2,
                        "keys": sorted(keys)[:8],
                        "font": o.font_face,
                        "txt": o.text_exprs[:1],
                    }
                )

    # ----- report -----
    order = sorted(count.keys(), key=lambda t: -count[t])
    print("\n%-8s %-7s %-6s  %s" % ("type", "count", "files", "summary"))
    print("-" * 90)
    for tc in order:
        n = count[tc]
        nf = len(files_with[tc])
        lib = " LIB" if (tc & 0xF000) == 0x1000 else ""
        with_an, no_an = an_present[tc]
        fonts = has_font[tc][0]
        texts = has_text_expr[tc][0]
        degen = rect1_degenerate[tc]
        fams = ",".join("%s:%d" % (k, v) for k, v in key_families[tc].most_common(6))
        tl = ",".join("%s:%d" % (k, v) for k, v in thumb_labels[tc].most_common(4))
        # aspect stats
        asp = aspect_samples[tc]
        med_w = med_h = 0
        if asp:
            ws = sorted(a[0] for a in asp)
            hs = sorted(a[1] for a in asp)
            med_w = ws[len(ws) // 2]
            med_h = hs[len(hs) // 2]
        print(
            "\n0x%-5X (%d)%s  count=%d files=%d  an:[set=%d none=%d]" % (tc, tc, lib, n, nf, with_an, no_an)
        )
        print("   subtype top: %s" % subtype[tc].most_common(5))
        print("   class_flag top: %s" % classflag[tc].most_common(5))
        print(
            "   rect1 [point=%d area=%d]  rect2_vs_rect1=%s  median box=%dx%d"
            % (degen[0], degen[1], dict(rect2_vs_rect1[tc]), med_w, med_h)
        )
        print("   font_faces=%d  text_exprs=%d" % (fonts, texts))
        print("   key families: %s" % (fams or "(none)"))
        topkeys = ",".join("%s:%d" % (k, v) for k, v in key_tokens[tc].most_common(8))
        print("   top keys: %s" % (topkeys or "(none)"))
        print("   thumb appearance: %s" % (tl or "(no thumb / n/a)"))
        for ex in examples[tc][:2]:
            print(
                "   eg %-22s cf=%d sub=%d an=%s r1=%s r2=%s font=%s txt=%s keys=%s"
                % (
                    ex["file"],
                    ex["cf"],
                    ex["sub"],
                    ex["an"],
                    ex["r1"],
                    ex["r2"],
                    ex["font"],
                    ex["txt"],
                    ex["keys"],
                )
            )

    # ----- compact CSV-ish table at end -----
    print("\n" + "=" * 90)
    print("COMPACT TABLE")
    print("=" * 90)
    print(
        "%-8s %-7s %-6s %-9s %-7s %-8s %s"
        % ("type", "count", "files", "rect1pt%", "fonts", "txtexpr", "top_families")
    )
    for tc in order:
        n = count[tc]
        degen = rect1_degenerate[tc]
        pct = 100.0 * degen[0] / (degen[0] + degen[1]) if (degen[0] + degen[1]) else 0
        fams = ",".join(k for k, _ in key_families[tc].most_common(3))
        print(
            "0x%-6X %-7d %-6d %-9.0f %-7d %-8d %s"
            % (tc, n, len(files_with[tc]), pct, has_font[tc][0], has_text_expr[tc][0], fams)
        )


if __name__ == "__main__":
    main()
