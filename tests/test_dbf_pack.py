"""Packing a project's page tables."""

import datetime
import os
import shutil

import pytest

from ctgedit.format import dbf as D
from ctgedit.io import pgregister as PR


@pytest.fixture
def ex(fx_project):
    """FxMain of the fake install: its pgtouch (30 rows) and pgkey (25 rows), each with its
    NDX on the page field (tools/make_fixtures.py writes them with FxButtons and FxInputs)."""
    return fx_project("FxMain")


def _copy(ex, tmp_path, table):
    for ext in (".DBF", ".NDX"):
        shutil.copyfile(os.path.join(ex, table + ext), tmp_path / (table + ext))
    return str(tmp_path / (table + ".DBF")), str(tmp_path / (table + ".NDX"))


def _flag(path, every=3):
    """Flag every ``every``-th record deleted (as a page delete does)."""
    with D.Dbf(path) as t:
        n = 0
        for i in range(0, t.nrec, every):
            if t.read(i)[:1] != b"*":
                t.delete(i)
                n += 1
        return n


def test_pack_removes_deleted_records_and_rebuilds_the_index(ex, tmp_path):
    p, n = _copy(ex, tmp_path, "pgtouch")
    _flag(p)
    t = D.Dbf(p, writable=False)
    live = [t.read(i) for i in range(t.nrec) if t.read(i)[:1] != b"*"]
    total = t.nrec
    t.close()
    removed = D.pack_table(p, [n], when=datetime.date(2026, 10, 9))
    assert removed == total - len(live) > 0
    t = D.Dbf(p, writable=False)
    try:
        assert t.nrec == len(live)
        assert [t.read(i) for i in range(t.nrec)] == live
        nx = D.Ndx(n, writable=False)
        try:
            ents = nx.entries()
            assert len(ents) == t.nrec
            keys = [k.lower() for k, _r in ents]
            assert keys == sorted(keys)
            for i in range(t.nrec):
                assert i + 1 in nx.find(t.field(t.read(i), nx.expr))
        finally:
            nx.close()
        hl, rl, nr = t.hlen, t.rlen, t.nrec
    finally:
        t.close()
    data = open(p, "rb").read()
    assert data[1:4] == bytes((126, 10, 9))
    assert data[-1:] == b"\x1a" and len(data) == hl + nr * rl + 1
    assert not os.path.exists(p + ".pack") and not os.path.exists(n + ".pack")


def test_a_table_without_deleted_records_is_left_alone(ex, tmp_path):
    p, n = _copy(ex, tmp_path, "pgtouch")
    D.pack_table(p, [n])  # now clean
    before = open(p, "rb").read(), open(n, "rb").read()
    assert D.pack_table(p, [n]) == 0
    assert (open(p, "rb").read(), open(n, "rb").read()) == before


def test_pack_page_tables_of_a_project(ex, tmp_path):
    for t in ("pgtouch", "pgkey"):
        _copy(ex, tmp_path, t)
        _flag(str(tmp_path / (t + ".DBF")), every=2)
    got = PR.pack_page_tables(str(tmp_path))
    assert set(got) == {"pgtouch", "pgkey"} and all(v > 0 for v in got.values())
    assert PR.pack_page_tables(str(tmp_path)) == {}
