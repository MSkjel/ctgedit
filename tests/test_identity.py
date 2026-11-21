"""CTG_FIXED_IDENTITY gives repeatable GUIDs and stamps."""

import datetime
import uuid

import pytest

from ctgedit.infra import identity


@pytest.fixture
def fixed(monkeypatch):
    monkeypatch.setenv("CTG_FIXED_IDENTITY", "fx-seed")
    identity.reset()
    yield
    identity.reset()


def test_fixed_guids_repeat_from_the_seed(fixed, monkeypatch):
    first = [identity.new_guid() for _k in range(4)]
    assert len(set(first)) == 4 and all(g.version == 4 for g in first)
    identity.reset()
    assert [identity.new_guid() for _k in range(4)] == first
    monkeypatch.setenv("CTG_FIXED_IDENTITY", "fx-other")  # another seed, other GUIDs
    assert identity.new_guid() not in first
    monkeypatch.setenv("CTG_FIXED_IDENTITY", "fx-seed")  # back: that seed's first one
    assert identity.new_guid() == first[0]


def test_fixed_time_is_new_year_2026(fixed):
    assert identity.now() == datetime.datetime(2026, 1, 1)
    assert identity.today() == datetime.date(2026, 1, 1)
    assert identity.now_stamp() == int(
        (datetime.datetime(2026, 1, 1) - datetime.datetime(1899, 12, 30)).total_seconds()
    )
    assert datetime.datetime.fromtimestamp(identity.now_time()) == datetime.datetime(2026, 1, 1)


def test_unfixed_is_random_and_now(monkeypatch):
    monkeypatch.delenv("CTG_FIXED_IDENTITY", raising=False)
    a, b = identity.new_guid(), identity.new_guid()
    assert a != b
    assert abs((identity.now() - datetime.datetime.now()).total_seconds()) < 5


def test_new_files_are_the_same_bytes_under_a_seed(fixed, tmp_path):
    from ctgedit.format import dbf, element as E, template_place as TP

    one = TP.new_page_bytes(200, 100)
    identity.reset()
    assert TP.new_page_bytes(200, 100) == one
    assert E.ctdraw_timestamp() == identity.now_stamp()
    content = bytes(0x40)
    identity.reset()
    r1 = E.refresh_identity(content)
    identity.reset()
    assert E.refresh_identity(content) == r1
    identity.reset()
    assert r1[0x24:0x34] == identity.guid_bytes()  # the seed's first GUID
    # a database header's last-update date
    p = tmp_path / "fx.dbf"
    head = bytearray(32)
    head[0] = 3
    head[8:12] = (33).to_bytes(2, "little") + (1).to_bytes(2, "little")
    p.write_bytes(bytes(head) + b"\x0d\x1a")
    with dbf.Dbf(str(p)) as t:
        t.touch()
    assert p.read_bytes()[1:4] == bytes((126, 1, 1))


def test_new_files_differ_without_a_seed(monkeypatch):
    from ctgedit.format import template_place as TP

    monkeypatch.delenv("CTG_FIXED_IDENTITY", raising=False)
    assert TP.new_page_bytes(200, 100, stamp=7) != TP.new_page_bytes(200, 100, stamp=7)


def test_uuid4_stays_patchable(monkeypatch):
    """Tests that pin a GUID patch uuid.uuid4 (test_undo_clean_point): still honoured."""
    monkeypatch.delenv("CTG_FIXED_IDENTITY", raising=False)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0x1234))
    assert identity.guid_bytes() == uuid.UUID(int=0x1234).bytes
