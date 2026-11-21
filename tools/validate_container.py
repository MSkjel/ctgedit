"""Check the container directory layout across all example .ctg and .ctF files."""

import os, sys, glob
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctgedit.format.container import Container

EXAMPLE = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\Example"


def check(path):
    data = open(path, "rb").read()
    try:
        c = Container.parse(data)
    except Exception as e:
        return path, "PARSE_ERROR: %s" % e, None
    blocks = sorted(c.blocks, key=lambda b: b.offset)
    if not blocks:
        return path, "NO_BLOCKS", c
    issues = []
    first = min(b.offset for b in blocks)
    # contiguity from first block to EOF
    cur = first
    for b in blocks:
        if b.offset != cur:
            issues.append("gap/overlap at %s: expected off %d got %d" % (b.name, cur, b.offset))
        cur = b.end
    if cur != len(data):
        issues.append("last block ends at %d, file is %d" % (cur, len(data)))
    return path, ("OK" if not issues else "; ".join(issues)), c


def main():
    files = glob.glob(os.path.join(EXAMPLE, "*.ctg")) + glob.glob(os.path.join(EXAMPLE, "*.ctF"))
    ok = 0
    name_counter = Counter()
    tag_counter = Counter()
    versions = Counter()
    bad = []
    for p in files:
        path, status, c = check(p)
        if status == "OK":
            ok += 1
        else:
            bad.append((os.path.basename(path), status))
        if c:
            versions[(c.version, c.word2, c.dword_c)] += 1
            for b in c.blocks:
                name_counter[b.name] += 1
                tag_counter[b.tag] += 1
    print("Files: %d   OK(tiled-to-EOF): %d   anomalies: %d" % (len(files), ok, len(bad)))
    print("\nHeader (version, word2, dword_c) variants:")
    for k, v in versions.most_common():
        print("   ver=%d word2=%d dwordC=%d  -> %d files" % (k[0], k[1], k[2], v))
    print("\nBlock names seen:")
    for n, c2 in name_counter.most_common():
        print("   %-24r %d" % (n, c2))
    print("\nBlock tags seen (hex):")
    for t, c2 in tag_counter.most_common():
        print("   0x%04X  %d" % (t, c2))
    if bad:
        print("\nAnomalies (first 20):")
        for name, st in bad[:20]:
            print("   %-28s %s" % (name, st))


if __name__ == "__main__":
    main()
