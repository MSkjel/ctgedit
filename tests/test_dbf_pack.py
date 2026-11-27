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


def test_pack_project_tables_of_a_project(ex, tmp_path):
    for t in ("pgtouch", "pgkey"):
        _copy(ex, tmp_path, t)
        _flag(str(tmp_path / (t + ".DBF")), every=2)
    got = PR.pack_project_tables(str(tmp_path))
    assert set(got["packed"]) == {"pgtouch", "pgkey"} and all(v > 0 for v in got["packed"].values())
    assert got["saved"] > 0 and not got["kept"] and not got["failed"]
    assert PR.pack_project_tables(str(tmp_path))["packed"] == {}


def test_a_table_the_change_log_names_keeps_its_record_numbers(ex, tmp_path):
    from ctgedit.format.dbf import create_table

    p, _n = _copy(ex, tmp_path, "pgtouch")
    _flag(p, every=2)
    before = open(p, "rb").read()
    create_table(
        str(tmp_path / "changes.DBF"),
        [("NAME", 16), ("KEY", 64), ("RECORD_NO", 10)],
        [{"NAME": "pgtouch", "KEY": "X", "RECORD_NO": "3"}],
    )
    got = PR.pack_project_tables(str(tmp_path))
    assert got["kept"] == ["pgtouch"] and "pgtouch" not in got["packed"]
    assert open(p, "rb").read() == before


def test_pack_database_in_a_helper_process_and_its_result(ex, tmp_path):
    from ctgedit.ui.window.library import LibraryCommands
    from ctgedit.workers import pack_worker as PW

    for t in ("pgtouch", "pgkey"):
        _copy(ex, tmp_path, t)
        _flag(str(tmp_path / (t + ".DBF")), every=2)
    ans = PW.pack({"dirs": [str(tmp_path)]})
    (proj,) = ans["projects"]
    assert set(proj["packed"]) == {"pgtouch", "pgkey"}
    lines = LibraryCommands._pack_database_lines(ans)
    assert lines[0].startswith("2 tables packed,") and any("pgtouch" in x for x in lines)
    assert (
        LibraryCommands._pack_database_lines(PW.pack({"dirs": [str(tmp_path)]}))[0]
        == "No deleted rows: nothing to pack."
    )
