"""Live DPI check: does ctgedit measure text boxes the way the Graphics
Builder does in THIS Windows session?

    python tools/text_extent_check.py <page.ctg | project folder> [...] [--show 10]

CtDraw re-measures every text when it opens a page (textmetrics docstring),
so right after CtDraw has opened + saved a page in this session, every styled
text's stored box (rect1) is CtDraw's measure at this session's system DPI.
For each page this prints the DPI ctgedit measures at (the system DPI a
system-aware process sees -- 96 at 100 %, 120 at 125 %, 144 at 150 %), the
process's own DPI awareness, and how many texts ctgedit predicts exactly.
Only reads the pages. Exit status 1 when a page has a mismatch.
"""

from __future__ import annotations

import glob
import os
import sys


def check(path: str, show: int = 10) -> int:
    os.environ.setdefault("CTG_NO_CTF_PRECHECK", "1")
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as G, textmetrics as TM

    doc = EditableDocument.open(path)
    doc._use_tag_formats()
    ok, bad, skip = 0, [], 0
    for i, po in enumerate(doc.parsed.page.objects):
        if po.type_code != 0x05 or not po.subtype_size & 0x2:
            continue
        box = TM.text_extent(po)
        if box is None:
            skip += 1
            continue
        stored = tuple(G.parse_geom_header(po.body_raw).rect1)
        if tuple(box) == stored:
            ok += 1
        else:
            tail = G.parse_text_tail(po) or {}
            bad.append((i, tail.get("text"), tail.get("face"), tail.get("size"), box, stored))
    print("%s: %d exact, %d different, %d not derivable" % (os.path.basename(path), ok, len(bad), skip))
    for i, text, face, size, box, stored in bad[:show]:
        print("   [%d] %r %s %spt  ctgedit %s  stored %s" % (i, (text or "")[:30], face, size, box, stored))
    return len(bad)


def main(argv) -> int:
    show = 10
    if "--show" in argv:
        k = argv.index("--show")
        show = int(argv[k + 1])
        argv = argv[:k] + argv[k + 2 :]
    from ctgedit.format import textmetrics as TM

    rep = TM.dpi_report()
    names = {0: "unaware", 1: "system aware", 2: "per-monitor aware"}
    print(
        "text extents measured at %s dpi (GDI reports %s dpi to this %s process)"
        % (rep["text_dpi"], rep["process_dpi"], names.get(rep["awareness"], "?"))
    )
    pages = []
    for a in argv:
        pages += sorted(glob.glob(os.path.join(a, "*.ctg"))) if os.path.isdir(a) else [a]
    if not pages:
        print(__doc__)
        return 2
    worst = 0
    for p in pages:
        try:
            worst = max(worst, check(p, show))
        except Exception as e:  # noqa: BLE001 -- a report tool: go on with the next page
            print("%s: could not be read (%s)" % (os.path.basename(p), e))
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
