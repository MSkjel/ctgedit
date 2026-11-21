"""The ctsim-page/0 export over the real corpus."""

import collections
import os

import pytest

from ctgedit.sim import pagemodel as PM

R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"
EX = os.path.join(R2, "Example")
SA = os.path.join(R2, "ExampleSA")
need = pytest.mark.skipif(
    not (os.path.isdir(EX) and os.path.isdir(SA)), reason="R2 Example / ExampleSA not installed"
)
heavy = pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="CTG_SKIP_HEAVY")


def _open(path):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(path)


def _roundtrip(doc):
    model = doc.export_page_model()
    text = PM.dumps(model)
    again = PM.loads(text)
    assert PM.check_model(again, doc) == []
    return again


def _compare_rows(project, page, model):
    """{table: (stored, exported, equal)} for the model's tables (pagemodel.compare_rows)."""
    return PM.compare_rows(project, model["page"], model["tables"])


def _compiled(project):
    names = os.listdir(project)
    rdb = {os.path.splitext(f)[0].lower() for f in names if f.lower().endswith(".rdb")}
    return [
        os.path.join(project, f)
        for f in sorted(names)
        if f.lower().endswith(".ctg") and os.path.splitext(f)[0].lower() in rdb
    ]


@need
@heavy
def test_every_compiled_page_rows_agree():
    """Every compiled Example / ExampleSA page: the exported rows against the project's. The
    rows that differ are stale stored rows (TXTMODE '0' rows stored blank, a WebContent
    registered as ActiveX, button WIDTH / HEIGHT, a duplicate pgadv row, a kept activex row)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.page import parse_ctg

    tot = collections.Counter()
    for project in (EX, SA):
        for path in _compiled(project):
            doc = EditableDocument(path=path)
            doc.parsed = parse_ctg(path)
            m = {"page": PM._page_name(doc)}
            m["tables"] = PM.page_tables(doc, m["page"])
            for _t, (stored, exported, equal) in _compare_rows(project, m["page"], m).items():
                tot["stored"] += stored
                tot["exported"] += exported
                tot["equal"] += equal
            tot["pages"] += 1
    assert tot["pages"] >= 180
    assert tot["equal"] >= 0.999 * tot["stored"], dict(tot)
    assert tot["equal"] >= 0.999 * tot["exported"], dict(tot)


@need
@heavy
def test_twenty_pages_round_trip():
    import random

    pages = _compiled(EX) + _compiled(SA)
    fixed = [
        p
        for p in pages
        if os.path.splitext(os.path.basename(p))[0] in ("Mixer", "Meters", "Overview_L2", "Others")
    ]
    rest = [p for p in pages if p not in fixed]
    random.Random(1).shuffle(rest)
    n = 0
    for p in fixed + rest[: 20 - len(fixed)]:
        doc = _open(p)
        m = _roundtrip(doc)
        n += len(m["objects"])
    assert n > 5000
