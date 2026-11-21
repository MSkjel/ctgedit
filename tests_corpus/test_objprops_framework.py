"""Expression ranges over the installed pages split and rejoin."""

import glob
import os

import pytest

from ctgedit.ui.objprops.range_field import join_range, split_range

U = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
EX = os.path.join(U, "Example")
SA = os.path.join(U, "ExampleSA")


@pytest.mark.skipif(not (os.path.isdir(EX) and os.path.isdir(SA)), reason="corpus missing")
def test_every_corpus_range_round_trips():
    """Every Example / ExampleSA MVX / MVY / SZX / SZY / ROT / FIL value with a
    range suffix splits and re-joins to the identical text."""
    from ctgedit.format.page import parse_ctg

    seen = {}
    for proj in (EX, SA):
        for f in glob.glob(os.path.join(proj, "*.ctg")):
            for po in parse_ctg(f).page.objects:
                for b in po.bags:
                    for k, v in b.pairs:
                        if k in ("MVX", "MVY", "SZX", "SZY", "ROT", "FIL") and v and "^(" in v:
                            seen.setdefault(k, set()).add(v)
    assert len(seen.get("MVX", ())) > 500 and len(seen.get("ROT", ())) > 20
    bad = [
        (k, v)
        for k, vs in seen.items()
        for v in vs
        if (lambda r: r is None or join_range(r.expr, r.lo, r.hi, r.trail) != v)(split_range(v))
    ]
    assert bad == []
