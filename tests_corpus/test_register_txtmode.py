"""TXTMODE '0' in the pgdynobj rows CtDraw stored for the installed projects' texts: the rule
of io/pgregister._txtmode_dropped against every one (read only)."""

import collections
import glob
import os

import pytest

import corpus


def _stored_rows(pdir):
    from ctgedit.format.projects import read_dbf_rows
    from ctgedit.format.fileio import find_ci

    f = find_ci(pdir, "pgdynobj.DBF")
    out = collections.defaultdict(list)
    for r in read_dbf_rows(f) if f else []:
        out[(r.get("PAGE", "").strip().lower(), r.get("AN", "").strip())].append(r.get("TXTMODE", ""))
    return out


def test_txtmode_zero_reaches_the_row_as_ctdraw_stored_it():
    from ctgedit.format import geometry as G, writer
    from ctgedit.format.page import parse_ctg_bytes
    from ctgedit.io import pgregister as PR

    pages = sorted(glob.glob(os.path.join(corpus.R2_USER, "*", "*.ctg")))
    if not pages:
        pytest.skip("no installed projects")
    checked, wrong = 0, []
    rows = {}
    for path in pages:
        pdir = os.path.dirname(path)
        page = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as fh:
            try:
                pc = parse_ctg_bytes(fh.read())
            except Exception:  # noqa: BLE001  # swallow-ok: a page that does not parse has no rows to compare
                continue
        if pdir not in rows:
            rows[pdir] = _stored_rows(pdir)
        for po in pc.page.objects:
            if po.type_code != 0x05:
                continue
            bag = writer.value_bag(po) or []
            if {k.upper(): v for k, v in bag}.get("TXTMODE", "").strip() != "0":
                continue
            an = G.live_an(po)
            stored = rows[pdir].get((page.lower(), str(an)))
            if not stored:
                continue
            checked += 1
            if PR._txtmode_dropped(po, bag) != (stored[0].strip() == ""):
                wrong.append("%s/%s AN %d (stored %r)" % (os.path.basename(pdir), page, an, stored[0]))
    assert checked, "no text with TXTMODE '0' and a stored row"
    assert not wrong, "%d of %d: %s (a page saved again since? tools/pin_corpus.py)" % (
        len(wrong),
        checked,
        "; ".join(wrong[:10]),
    )
