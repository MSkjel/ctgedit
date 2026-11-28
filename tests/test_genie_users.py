"""Used By and Update Users."""

import os
import shutil

import pytest
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: the genie in between (A), the genie nested in it that changed (B), the page placing A
A, B, PAGE = "n4", "n3", "FxGenies"


def _make_stale(lib):
    """Save element B of ``lib`` again after a colour change of its text (no record added):
    its stamp moves on, and A's link to it keeps the old one."""
    from ctgedit.editing import ElementDocument

    e = ElementDocument.open_element(lib, B)
    leaf = next(i for i, o in enumerate(e.page.objects) if o.parent is None and o.kind.value == "text")
    e.set_colour(leaf, 0x123456)
    e.save()
    assert e.save_files


@pytest.fixture()
def proj(tmp_path, monkeypatch, fx_install):
    """tmp/FxNest with a copy of fxnest.ctm (B saved after A) and the page that places A."""
    d = tmp_path / "FxNest"
    d.mkdir()
    shutil.copyfile(fx_install.file("FxLib", "fxnest.ctm"), d / "fxnest.ctm")
    shutil.copyfile(fx_install.page(PAGE), d / (PAGE + ".ctg"))
    monkeypatch.setenv("CTG_LIB_PATH", fx_install.project("FxLib"))
    from ctgedit.io import genieusage as GU

    _make_stale(str(d / "fxnest.ctm"))
    GU.clear_cache()
    return d


def _labels(users):
    return {(u.ref.label, u.status, " > ".join(r.label for r in u.via)) for u in users}


# -- the tables -----------------------------------------------------------------------------
def test_library_tables_match_the_parsed_element_link_tables(fx_install):
    """The head-only reader gives each element's own stamp and its link-table names exactly as
    a full parse of the element block does."""
    import struct
    from ctgedit.format import links
    from ctgedit.format.symbols import open_library
    from ctgedit.io import genieusage as GU

    for lib in (
        fx_install.file("FxLib", "fxnest.ctm"),
        fx_install.file("FxLib", "fxparts.ctm"),
        fx_install.file("FxLib", "fxgenie.ctm"),
    ):
        tables = GU.library_tables(lib)
        L = open_library(lib)
        assert tables
        for name, t in tables.items():
            pb = L.element(t.name)
            want = [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []
            assert list(t.names) == want, (lib, name)
            assert t.stamp == struct.unpack_from("<I", pb.page_identity, 0)[0]


# -- the users -----------------------------------------------------------------------------
def test_direct_and_indirect_users_with_their_status(proj):
    from ctgedit.io import genieusage as GU

    u = GU.element_users(str(proj / "fxnest.ctm"), B)
    got = _labels(u.users)
    assert ("fxnest.n4", GU.OUT_OF_DATE, "") in got
    assert (PAGE, GU.WAITING, "fxnest.n4") in got
    row = u.of(GU.Ref("element", str(proj / "fxnest.ctm"), A))
    assert row.direct and row.links[0].stale
    page = next(x for x in u.users if x.ref.kind == "page")
    assert not page.direct and [r.label for r in page.via] == ["fxnest.n4"]
    c = u.counts()
    assert c["direct_elements"] == 1 and c["pages"] == 1 and c["out_of_date"] == 2
    # the genie in between: its page places it directly, and is out of date by its stamp?
    v = GU.element_users(str(proj / "fxnest.ctm"), A)
    assert any(x.ref.kind == "page" and x.direct for x in v.users)


def test_entries_resolve_through_the_users_own_search_path(proj, tmp_path):
    """A user is one whose entry RESOLVES to the element's library file: Other's
    n4 places Other's own copy of n3, not this one."""
    from ctgedit.io import genieusage as GU

    other = tmp_path / "Other"
    other.mkdir()
    shutil.copyfile(proj / "fxnest.ctm", other / "fxnest.ctm")
    idx = GU.build_index([str(proj), str(other)])
    mine = idx.usage(str(proj / "fxnest.ctm"), B)
    theirs = idx.usage(str(other / "fxnest.ctm"), B)
    assert {u.ref.project for u in mine.users} == {"FxNest"}
    assert {(u.ref.project, u.ref.label) for u in theirs.users} == {("Other", "fxnest.n4")}


def test_update_order_is_deepest_first():
    """G is nested in A, A in B; a page places B, another page places G: A is saved before B,
    then the pages."""
    from ctgedit.io import genieusage as GU

    G = GU.Ref("element", r"c:\p\lib.ctm", "g", "P")
    A = GU.Ref("element", r"c:\p\lib.ctm", "a", "P")
    B = GU.Ref("element", r"c:\p\lib2.ctm", "b", "P")
    P1 = GU.Ref("page", r"c:\p\one.ctg", "", "P")
    P2 = GU.Ref("page", r"c:\p\two.ctg", "", "P")
    users = [
        GU.User(P2, [GU.Link(P2, G, "lib.g.<ctm#00000001", 1, 2)], (), True, GU.OUT_OF_DATE),
        GU.User(B, [GU.Link(B, A, "lib.a.<ctm#00000005", 5, 5)], (A,), False, GU.WAITING),
        GU.User(A, [GU.Link(A, G, "lib.g.<ctm#00000001", 1, 2)], (), True, GU.OUT_OF_DATE),
        GU.User(P1, [GU.Link(P1, B, "lib2.b.<ctm#00000009", 9, 9)], (A, B), False, GU.WAITING),
    ]
    plan = GU.update_plan(GU.Usage(G, users))
    assert [u.ref.element for u in plan.elements] == ["a", "b"]
    assert [u.ref.label for u in plan.pages] == ["two", "one"]
    # a supplied library is left alone (its users then stay waiting)
    users[1].shipped = True
    plan = GU.update_plan(GU.Usage(G, users))
    assert [u.ref.element for u in plan.elements] == ["a"]
    assert [u.ref.element for u, _w in plan.skipped] == ["b"]


# -- the update ------------------------------------------------------------------------------
def test_update_saves_the_genie_then_the_page_and_everything_is_current(
    proj, tmp_path, monkeypatch, fx_install
):
    from ctgedit.infra import backups
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages as UP

    monkeypatch.setenv("CTG_NO_CTF_PRECHECK", "1")
    lib = str(proj / "fxnest.ctm")
    page = str(proj / (PAGE + ".ctg"))
    lib_before = open(lib, "rb").read()
    page_before = open(page, "rb").read()
    u = GU.element_users(lib, B)
    plan = GU.update_plan(u)
    assert [x.ref.label for x in plan.elements] == ["fxnest.n4"]
    steps = []
    bk = str(tmp_path / "backup")
    rep = GU.run_update(plan, backup=bk, progress=lambda i, n, l: steps.append(l) or True)
    assert steps == ["fxnest.n4", "pages"]
    assert rep.elements_saved == ["fxnest.n4"] and not rep.failed
    assert [os.path.basename(p) for p in [page]][0][:-4] in rep.pages.updated
    after = GU.element_users(lib, B)
    assert all(x.status == GU.UP_TO_DATE for x in after.users), _labels(after.users)
    # the page still holds the genie's art (nothing lost on the way)
    from ctgedit.io import reader
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
    import genie_recompose_sweep as GS

    a = GS.page_digest(reader.read_page(fx_install.page(PAGE)))
    b = GS.page_digest(reader.read_page(page))
    assert b["art"] >= a["art"] and b["objects"] == a["objects"]
    # a second update has nothing to do
    again = GU.update_plan(GU.element_users(lib, B))
    assert not again.elements and not again.pages
    # the backup holds the library and the page; restoring puts both back
    m = backups.read_manifest(bk)
    assert {bool(e.get("original")) for e in m["entries"]} == {True, False}
    res = UP.restore_backup(bk)
    assert res.libraries == [os.path.abspath(lib)] and not res.failed
    assert open(lib, "rb").read() == lib_before
    assert open(page, "rb").read() == page_before


def test_refresh_save_of_a_current_element_writes_nothing(proj):
    """An element whose nested genies are current is a fixed point of the refresh save
    (after one update the stored form is ctgedit's / CtDraw's normal form)."""
    from ctgedit.editing import ElementDocument

    lib = str(proj / "fxnest.ctm")
    d = ElementDocument.open_element(lib, A)
    d.save(refresh=True)
    assert d.save_files  # its nested copy was out of date: written
    d2 = ElementDocument.open_element(lib, A)
    d2.save(refresh=True)
    assert not d2.save_files  # now current: nothing to write
    # a plain save of an unedited element still writes nothing (as before)
    d3 = ElementDocument.open_element(lib, B)
    d3.save()
    assert not d3.save_files


def test_a_dirty_open_element_is_skipped(proj):
    from ctgedit.io import genieusage as GU

    lib = str(proj / "fxnest.ctm")
    plan = GU.update_plan(GU.element_users(lib, B))

    class Dirty:
        dirty = True

    rep = GU.run_update(GU.UpdatePlan(plan.elements, []), open_element=lambda ref: Dirty())
    assert not rep.elements_saved and "unsaved changes" in rep.skipped[0]


# -- the window -----------------------------------------------------------------------------
def test_used_by_window_lists_direct_and_indirect_users(proj):
    _app()
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

    lib = str(proj / "fxnest.ctm")
    usage = GU.element_users(lib, B)
    ran = []
    dlg = ElementUsersDialog(lib, B, usage, on_update=ran.append, on_open=lambda r: None)
    dlg.show()
    rows = dlg.users()
    assert ("fxnest.n4", "Out of date", "") in rows
    assert (PAGE, "Out of date once fxnest.n4 is updated", "fxnest.n4") in rows
    assert "2 of them need an update" in dlg._head.text()
    assert dlg._btn_update.isEnabled()
    dlg._btn_update.click()
    assert ran == [usage]
    dlg._only_stale.setChecked(True)
    assert len(dlg.users()) == 2
    dlg.close()
