"""Check that our writer round-trips the live pages byte for byte."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ctgedit.format.page import parse_ctg
from ctgedit.format.writer import serialize_ctg
from ctgedit.format.container import Container

PAGES = [
    ("R2 (CtDraw save, LIVE)", r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example\Mixer.ctg"),
]

for label, p in PAGES:
    if not os.path.exists(p):
        print("===", label, "MISSING", p)
        continue
    data = open(p, "rb").read()
    c = Container.parse(data)
    print("===", label, "len", len(data))
    print(
        "  header version=%d word2=%d dword_c=%d ts_c=%d ts_m=%d"
        % (c.version, c.word2, c.dword_c, c.ts_created, c.ts_modified)
    )
    print("  blocks:", [(hex(b.tag), b.name, b.size) for b in c.blocks])
    pp = parse_ctg(p)
    out = serialize_ctg(pp.container, pp.page, pp.attributes)
    exact = out == data
    print("  objects parsed:", len(pp.page.objects) if pp.page else None)
    print("  byte-exact round-trip:", exact, "(out len %d, delta %d)" % (len(out), len(out) - len(data)))
    if not exact:
        n = min(len(data), len(out))
        diffs = 0
        first = None
        for i in range(n):
            if data[i] != out[i]:
                if first is None:
                    first = i
                diffs += 1
        print("  first diff @ 0x%X, total differing bytes (over common) = %d" % (first or -1, diffs))
        if first is not None:
            print("    orig:", data[max(0, first - 12) : first + 12].hex(" "))
            print("    ours:", out[max(0, first - 12) : first + 12].hex(" "))
    print()
