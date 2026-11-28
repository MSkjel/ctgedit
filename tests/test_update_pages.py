"""Update Pages recomposes only elements whose stamp changed."""

import os

import pytest


def test_unchanged_libraries_leave_pages_alone(fx_page):
    """FxMain's FxGenies places ten fxgenie / fxnest genies, every link entry stamped with
    its element's current stamp."""
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    page = fx_page("FxGenies")
    cur = updatepages.Current(SymbolResolver.for_page(page))
    assert updatepages.stale_elements(page, cur) == set()


def test_a_changed_element_stamp_marks_its_instances_stale(fx_page, fx_project):
    """Rewrite one link-table stamp on a copy (beside FxGenies in FxMain, so the same
    include chain resolves it) -> exactly that element is stale."""
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    src = fx_page("FxGenies")
    pb = parse_ctg(src).page
    area, entries = links.parse_link_table(pb.link_table_raw)
    target = next(n for _g, n in entries if ".<ctm#" in n)
    raw = open(src, "rb").read()
    stamp = target.rsplit("#", 1)[1]
    bumped = target[: -len(stamp)] + ("%08x" % (int(stamp, 16) ^ 1))
    page = os.path.join(fx_project("FxMain"), "FxGeniesBumped.ctg")
    with open(page, "wb") as fh:
        fh.write(raw.replace(target.encode("cp1252"), bumped.encode("cp1252"), 1))
    cur = updatepages.Current(SymbolResolver.for_page(page))
    lib, _, rest = links.strip_stamp(target).partition(".")
    elem = rest.rpartition(".")[0]
    assert updatepages.stale_elements(page, cur) == {(lib.lower(), elem.lower())}


# -- a DEAD link entry: one no record uses any more ---------------------------------------------
@pytest.fixture
def base_page(fx_page):
    """The seed of the scratch pages (EditableDocument.new_blank keeps its appearance and
    drops its objects): FxMain's FxShapes, which has no template and no link entry."""
    return fx_page("FxShapes")


def _counting_stamps(monkeypatch):
    """The stamps count up (ctdraw_timestamp has whole seconds)."""
    from ctgedit.format import element as E

    now = [E.ctdraw_timestamp()]

    def stamp():
        now[0] += 1
        return now[0]

    monkeypatch.setattr(E, "ctdraw_timestamp", stamp)


def _element(lib, name, kind="genie", inner=None):
    from ctgedit.editing import ElementDocument

    d = ElementDocument.new_element(lib, name, kind, canvas=(300, 200))
    d.create_object("text", rect=(10, 10, 120, 26), text=name.upper())
    if inner:
        assert d.place_library_element("deadlib", inner, "ctm", (10, 40)) is not None
    d.save()


def _change(lib, name):
    """Move ``name``'s first object and save it: a new stamp the page does not quote."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU

    e = ElementDocument.open_element(lib, name)
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    libraries_changed()
    GU.clear_cache()


def _entries(page):
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg

    pb = parse_ctg(page).page
    return [n for _g, n in links.parse_link_table(pb.link_table_raw)[1]] if pb.link_count else []


def _current(page):
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    return updatepages.Current(SymbolResolver.for_page(page))


@pytest.fixture
def dead(tmp_path, monkeypatch, base_page):
    """Proj (a scratch folder: every page counts) with deadlib.ctm (genies g1, g2), deadsym.ctl
    (symbol s1) and p1.ctg placing g1, g2 and s1; then g2 and s1 are deleted from the page and
    the page is written WITHOUT dropping their link-table entries (what a save that kept them
    left: ctgedit's own save drops them)."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.fileio import write_atomic

    _counting_stamps(monkeypatch)
    proj = tmp_path / "Proj"
    proj.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib, sym = str(proj / "deadlib.ctm"), str(proj / "deadsym.ctl")
    _element(lib, "g1")
    _element(lib, "g2")
    _element(sym, "s1", kind="symbol")
    page = str(proj / "p1.ctg")
    doc = EditableDocument.new_blank(base_page, page, register=False)
    assert doc.place_library_element("deadlib", "g1", "ctm", (100, 100)) is not None
    assert doc.place_library_element("deadlib", "g2", "ctm", (300, 100)) is not None
    assert doc.place_library_element("deadsym", "s1", "ctl", (500, 100)) is not None
    doc.save()
    doc = EditableDocument.open(page)
    gone = [i for i, (_a, b) in doc.genie_instances() if b.lower() == "g2"]
    gone += [i for i, (_a, b), _k in doc.symbol_placements() if b.lower() == "s1"]
    doc.delete_objects(gone)
    write_atomic(page, doc.serialize())
    names = _entries(page)
    assert [n.split("#")[0] for n in names] == ["deadlib.g1.<ctm", "deadlib.g2.<ctm", "deadsym.s1.<ctl"], (
        names
    )
    return proj, lib, sym, page


def test_a_dead_entry_is_no_stale_genie_and_is_told_apart(dead):
    from ctgedit.io import updatepages

    _proj, lib, sym, page = dead
    assert updatepages.stale_genie_links(page, _current(page)) == (set(), [])
    _change(lib, "g2")
    _change(sym, "s1")
    cur = _current(page)
    g2 = next(n for n in _entries(page) if n.startswith("deadlib.g2."))
    s1 = next(n for n in _entries(page) if n.startswith("deadsym.s1."))
    assert updatepages.stale_genie_links(page, cur) == (set(), [g2])
    assert updatepages.stale_symbol_links(page, cur) == (set(), [s1])
    assert updatepages.stale_elements(page, cur) == set()
    assert updatepages.stale_symbols(page, cur) == set()
    # a used entry stays a stale genie
    _change(lib, "g1")
    live, dead_names = updatepages.stale_genie_links(page, _current(page))
    assert live == {("deadlib", "g1")} and dead_names == [g2]


@pytest.mark.parametrize("which", ["genie", "symbol"])
def test_page_needs_update_counts_a_dead_out_of_date_entry(dead, which):
    """A page out of date only through a dead entry needs an update: page_needs_update counts
    it, as Used By does."""
    from ctgedit.io import updatepages

    proj, lib, sym, page = dead
    assert not updatepages.page_needs_update(page)
    if which == "genie":
        _change(lib, "g2")
    else:
        _change(sym, "s1")
    assert updatepages.page_needs_update(page)
    updatepages.update_pages(str(proj))
    assert not updatepages.page_needs_update(page)


def test_the_run_saves_a_page_out_of_date_only_through_dead_entries(dead, tmp_path):
    """The page is saved (backed up first): its save drops the entries, as CtDraw's load +
    save does; the record says 'updated' with the links it dropped, and the next run calls
    the page current."""
    from ctgedit.infra import backups
    from ctgedit.io import updatepages

    proj, lib, sym, page = dead
    _change(lib, "g2")
    _change(sym, "s1")
    bk = str(tmp_path / "bk")
    rep = updatepages.update_pages(str(proj), backup=bk)
    (rec,) = rep.records
    assert (rec["outcome"], rec["stale"], rec["links_dropped"], rec["instances"]) == ("updated", 1, 2, 0), rec
    assert "deadlib.g2.<ctm" in rec["detail"] and "deadsym.s1.<ctl" in rec["detail"]
    assert rep.updated == ["p1"] and rep.links_dropped == ["p1"] and rep.stale == 1
    assert [n.split("#")[0] for n in _entries(page)] == ["deadlib.g1.<ctm"]
    assert [e["page"] for e in backups.read_manifest(bk)["entries"]] == ["p1"]
    again = updatepages.update_pages(str(proj))
    assert [(r["outcome"], r["stale"]) for r in again.records] == [("current", 0)]


def test_a_dead_entry_of_a_current_element_leaves_the_page_alone(dead):
    """No needless write: the page is not opened, its file stays as it is."""
    from ctgedit.io import updatepages

    proj, _lib, _sym, page = dead
    before = open(page, "rb").read()
    rep = updatepages.update_pages(str(proj))
    assert [(r["outcome"], r["stale"], r["links_dropped"]) for r in rep.records] == [("current", 0, 0)]
    assert open(page, "rb").read() == before


def test_a_page_out_of_date_that_changes_in_nothing_has_its_own_outcome(tmp_path, monkeypatch, base_page):
    """Only a genie nested in a genie the page places changed, and the page already shows
    it (its first run updated the copies): the library genie that nests it still quotes the
    old stamp, so the page stays out of date until that genie is saved. The run records it
    'unchanged' with the reason."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages

    _counting_stamps(monkeypatch)
    proj = tmp_path / "Proj"
    proj.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib = str(proj / "deadlib.ctm")
    _element(lib, "a")
    _element(lib, "b", inner="a")
    page = str(proj / "p2.ctg")
    doc = EditableDocument.new_blank(base_page, page, register=False)
    assert doc.place_library_element("deadlib", "b", "ctm", (100, 100)) is not None
    doc.save()
    _change(lib, "a")
    first = updatepages.update_pages(str(proj))
    assert [r["outcome"] for r in first.records] == ["updated"], first.records
    rep = updatepages.update_pages(str(proj))
    (rec,) = rep.records
    assert (rec["outcome"], rec["stale"]) == ("unchanged", 1), rec
    assert "only genies nested in deadlib.b changed" in rec["detail"]
    assert rep.unchanged == ["p2"] and not rep.updated and rep.stale == 1


def test_a_dead_entry_whose_nested_genie_changed_leaves_the_page_alone(tmp_path, monkeypatch, base_page):
    """b nests a; the page places g1 and b, then b is deleted with its entry kept. Only a
    changes: b's entry still quotes b's current stamp and nothing on the page shows b, so
    the page is current and its file is not written."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format.fileio import write_atomic
    from ctgedit.format.page import parse_ctg
    from ctgedit.io import updatepages

    _counting_stamps(monkeypatch)
    proj = tmp_path / "Proj"
    proj.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib = str(proj / "deadlib.ctm")
    _element(lib, "a")
    _element(lib, "g1")
    _element(lib, "b", inner="a")
    page = str(proj / "p1.ctg")
    doc = EditableDocument.new_blank(base_page, page, register=False)
    assert doc.place_library_element("deadlib", "g1", "ctm", (100, 100)) is not None
    assert doc.place_library_element("deadlib", "b", "ctm", (300, 100)) is not None
    doc.save()
    doc = EditableDocument.open(page)
    doc.delete_objects([i for i, (_a, b) in doc.genie_instances() if b.lower() == "b"])
    write_atomic(page, doc.serialize())
    assert [n.split("#")[0] for n in _entries(page)] == ["deadlib.g1.<ctm", "deadlib.b.<ctm"]
    _change(lib, "a")
    cur = _current(page)
    # (the case: b's own stamp is current, only the genie nested in it changed)
    raw = parse_ctg(page).page.link_table_raw
    assert updatepages.genie_changes(raw, cur) == {("deadlib", "b"): frozenset({("deadlib", "a")})}
    assert updatepages.stale_genie_links(page, cur) == (set(), [])
    before = open(page, "rb").read()
    rep = updatepages.update_pages(str(proj))
    assert [(r["outcome"], r["stale"], r["links_dropped"]) for r in rep.records] == [("current", 0, 0)], (
        rep.records
    )
    assert not rep.updated and not rep.links_dropped
    assert open(page, "rb").read() == before
