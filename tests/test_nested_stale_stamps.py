"""Nested genies that stayed old while their stamps said current."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _has(path, s):
    data = open(path, "rb").read()
    return s.encode("latin-1") in data or s.encode("utf-16-le") in data


def _text_genie(lib, name, text, rect=False):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.new_element(lib, name, "genie")
    d.create_object("text", (0, 0, 80, 16), text=text)
    if rect:
        d.create_object("rectangle", (0, 20, 40, 40))
    d.save()
    libraries_changed()


def _host(lib, name, inner, params, extra=None):
    from ctgedit.editing import ElementDocument, libraries_changed

    lname = os.path.splitext(os.path.basename(lib))[0]
    h = ElementDocument.new_element(lib, name, "genie")
    h.place_library_element(lname, inner, "ctm", (10, 10), params=params)
    if extra:
        h.create_object("rectangle", (0, 60, 30, 90))
    h.save()
    libraries_changed()


def _chain(fx, lib=None):
    """inner ('%Tag%_IN'), mid (Tag=%Name%_MID), outer (Name=%Line%_OUT); the library."""
    lib = lib or os.path.join(fx.project("FxLib"), "fxdeep.ctm")
    _text_genie(lib, "inner", "%Tag%_IN", rect=True)
    _host(lib, "mid", "inner", {"Tag": "%Name%_MID"})
    _host(lib, "outer", "mid", {"Name": "%Line%_OUT"}, extra=True)
    return lib


def _page(fx, places):
    """A page of a new project FxDeep placing ``places`` [(library, element, params)]."""
    from ctgedit.editing import EditableDocument, libraries_changed

    proj = fx.new_project("FxDeep", includes=("FxLib", "FxStyle"), tables=False)
    page = os.path.join(proj, "Deep.ctg")
    shutil.copyfile(fx.page("FxShapes"), page)
    p = EditableDocument.open(page)
    for k, (lib, el, params) in enumerate(places):
        p.place_library_element(lib, el, "ctm", (100, 100 + 120 * k), params=params)
    p.save()
    libraries_changed()
    return proj, page


def _set_text(lib, name, new):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib, name)
    j = next(j for j, po in enumerate(d.parsed.page.objects) if po.type_code == 0x05)
    assert d.set_text(j, new)
    d.save()
    libraries_changed()


def test_a_composition_that_failed_is_not_remembered_as_nothing_to_do(fx_install, monkeypatch):
    """The first run cannot read the inner genie's library (a lock, a write in progress):
    the page stays as it was. The 'nothing changes' memo, a file every later run asks, must
    not remember that: the next run, with the library readable, updates the page."""
    from ctgedit.editing import libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.io import updatepages

    lib = _chain(fx_install)
    proj, page = _page(fx_install, [("fxdeep", "outer", {"Line": "P1"})])
    assert _has(page, "P1_OUT_MID_IN")
    _set_text(lib, "inner", "%Tag%_NEW")
    real = GP._load_element

    def locked(lib_path, element, *a, **k):
        if element.lower() == "outer":
            raise PermissionError(13, "sharing violation (simulated)", lib_path)
        return real(lib_path, element, *a, **k)

    monkeypatch.setattr(GP, "_load_element", locked)
    libraries_changed()
    updatepages.update_pages(proj)
    assert not _has(page, "P1_OUT_MID_NEW")
    monkeypatch.setattr(GP, "_load_element", real)
    libraries_changed()
    updatepages.update_pages(proj)
    assert _has(page, "P1_OUT_MID_NEW")


def test_nested_copies_are_found_after_a_genie_composed_in_full(fx_install, monkeypatch):
    """Nested copies are found after a genie is composed in full."""
    monkeypatch.setenv("CTG_GENIE_NESTED_COPIES", "1")
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP

    lib = os.path.join(fx_install.project("FxLib"), "fxh.ctm")

    def genie(name, build):
        d = ElementDocument.new_element(lib, name, "genie")
        build(d)
        d.save()
        libraries_changed()

    def g0(d):
        d.create_object("text", (0, 0, 120, 16), text="%Tag%|%Unit%")
        r = d.create_object("rectangle", (0, 20, 40, 40))
        d.add_input(r, "touch", {"CMD": "Beep(%Tag%); Toggle(%Unit%)"})

    genie("g0", g0)

    def g1(d):
        d.create_object("text", (0, 0, 120, 16), text="g1:%Name%")
        d.place_library_element("fxh", "g0", "ctm", (10, 30), params={"Tag": "%Name%_X", "Unit": "u%Name%"})

    genie("g1", g1)

    def g2(d):
        d.create_object("text", (0, 0, 120, 16), text="g2:%Line%")
        d.place_library_element("fxh", "g1", "ctm", (10, 30), params={"Name": "%Line%A"})
        d.place_library_element("fxh", "g1", "ctm", (10, 130), params={"Name": "%Line%B"})

    genie("g2", g2)

    def g3(d):
        d.create_object("text", (0, 0, 120, 16), text="g3:%Area%")
        d.place_library_element("fxh", "g2", "ctm", (10, 30), params={"Line": "%Area%-L"})

    genie("g3", g3)
    page = fx_install.page("FxParts")
    doc = EditableDocument.open(page)
    doc.place_library_element("fxh", "g3", "ctm", (300, 300), params={"Area": "Z"})
    doc.save()
    libraries_changed()
    d = ElementDocument.open_element(lib, "g2")
    idx = [i for i, _l in d.genie_instances()][1]
    assert d.set_genie_binding(idx, "Name", "%Line%Q")
    d.save()
    libraries_changed()
    d = EditableDocument.open(page)
    ch = d.genie_changes()
    assert any(v is None for v in ch.values()) and ch.get(("fxh", "g3"))
    d.apply_genie_changes(ch)
    (i,) = [i for i, l in d.genie_instances() if l[1].lower() == "g3"]
    objs = d.parsed.page.objects[i : GP.subtree_end(d.parsed.page.objects, i)]
    data = b"".join(writer_payload(p) for p in objs)
    assert "Z-LQ".encode("utf-16-le") in data or b"Z-LQ" in data
    assert "Z-LB".encode("utf-16-le") not in data and b"Z-LB" not in data


def writer_payload(po):
    from ctgedit.format import writer

    return writer.object_payload(po)


def _copy_root(doc, name):
    from ctgedit.format.symbols import reference_from_object

    for j, p in enumerate(doc.parsed.page.objects):
        if p.class_flag and (p.subtype_size & 0x201) == 0x201:
            r = reference_from_object(p)
            if r is not None and r.element.lower() == name:
                return j
    raise AssertionError(name)


def test_undo_past_a_save_that_composed_takes_the_old_stamps_back(fx_install):
    """A mid tab: a step; inner changes elsewhere; the save composes the copy of inner and
    quotes inner's new stamp; Undo puts the copy from before the save back. The table goes
    back with it, so the next save composes the copy again: kept, the old copy was saved
    under inner's current stamp and nothing ever called mid out of date."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _chain(fx_install)
    d = ElementDocument.open_element(lib, "mid")
    d.move_objects([_copy_root(d, "inner")], 5, 0)
    _set_text(lib, "inner", "%Tag%_NEW")
    d.save()
    libraries_changed()
    d.undo()
    assert d.nested_genie_changes()
    d.save()
    libraries_changed()
    e = ElementDocument.open_element(lib, "mid")
    assert not e.nested_genie_changes()
    assert _has(lib, "%Name%_MID_NEW") or _has(lib, "_NEW")
    from ctgedit.format import writer

    copy = [writer.object_payload(e.parsed.page.objects[j]) for j in e.subtree(_copy_root(e, "inner"))]
    assert any(b"NEW" in p or "NEW".encode("utf-16-le") in p for p in copy)


def test_the_users_of_a_genie_in_a_library_folder_are_found(fx_install, monkeypatch, tmp_path):
    """A genie in a library folder of Settings > Pages and libraries (CTG_LIB_PATH) is
    searched by every project: a genie of a project that places it is one of its users."""
    from ctgedit.editing import libraries_changed
    from ctgedit.io import genieusage as GU

    folder = tmp_path / "libfolder"
    folder.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(folder))
    libraries_changed()
    glib = os.path.join(str(folder), "hunt.ctm")
    _text_genie(glib, "g", "%Tag%")
    proj = fx_install.new_project("FxUser", includes=("FxStyle",), tables=False)
    ulib = os.path.join(proj, "ul.ctm")
    from ctgedit.editing import ElementDocument

    h = ElementDocument.new_element(ulib, "u", "genie")
    h.place_library_element("hunt", "g", "ctm", (10, 10), params={"Tag": "X"})
    h.save()
    libraries_changed()
    _set_text(glib, "g", "%Tag%_NEW")
    usage = GU.element_users(glib, "g")
    assert any(u.ref.element.lower() == "u" for u in usage.users), usage.users


def test_force_composes_a_page_with_composites(fx_install, monkeypatch):
    """Update Pages' 'every page' in one helper composes a page with composite genies."""
    from ctgedit.io import updatepages

    proj = fx_install.new_project("FxForceComp", includes=("FxLib", "FxStyle"), tables=False)
    shutil.copyfile(fx_install.page("FxShapes"), os.path.join(proj, "FxShapes.ctg"))
    monkeypatch.setattr(updatepages, "has_composites", lambda p: True)
    rep = updatepages.update_pages(proj, force=True)
    assert not rep.failed, rep.failed
    assert rep.updated == ["FxShapes"]


@pytest.mark.parametrize("same_guid", [True, False])
def test_a_stamp_written_again_by_another_save_is_told_by_its_guid(fx_install, same_guid):
    """Two saves of an element can write one stamp (whole seconds; ctgedit steps its quick
    saves past the clock, a later Graphics Builder save uses the clock): the page entry's
    GUID tells them apart."""
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    _chain(fx_install)
    proj, page = _page(fx_install, [("fxdeep", "outer", {"Line": "P1"})])
    pb = parse_ctg(page).page
    head, ents = links.parse_link_table(pb.link_table_raw)
    if not same_guid:
        ents = [(bytes(reversed(g)) if "outer" in n.lower() else g, n) for g, n in ents]
    raw, _cnt = links.serialize_link_table(head, ents)
    cur = updatepages._Current(SymbolResolver.for_page(page))
    got = updatepages.genie_changes(raw, cur)
    assert (("fxdeep", "outer") in got) is (not same_guid)


def test_a_nested_entry_with_a_reused_stamp_is_told_by_its_guid(fx_install, monkeypatch):
    """The genie-in-genie side of the GUID rule: an element's table quoting a nested genie's
    current stamp with another GUID (another save of it) is a nested change, for Update Pages
    (nested_changes) and for the chain (genieusage.Link)."""
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages

    lib = _chain(fx_install)
    proj, page = _page(fx_install, [("fxdeep", "outer", {"Line": "P1"})])
    cur = updatepages._Current(SymbolResolver.for_page(page))
    assert not updatepages.nested_changes("fxdeep.outer.<ctm", cur)
    monkeypatch.setattr(updatepages._Current, "guid", lambda self, name: b"\x01" * 16)
    cur = updatepages._Current(SymbolResolver.for_page(page))
    assert ("fxdeep", "mid") in updatepages.nested_changes("fxdeep.outer.<ctm", cur)
    t = GU.library_tables(lib)["outer"]
    assert len(t.guids) == len(t.names) and len(t.guid) == 16
    same = GU.Ref("element", lib, "mid", "FxLib")
    assert not GU.Link(same, same, "x", 5, 5, b"\x01" * 16, b"\x01" * 16).stale
    assert GU.Link(same, same, "x", 5, 5, b"\x01" * 16, b"\x02" * 16).stale
    assert not GU.Link(same, same, "x", 5, 5, bytes(16), b"\x02" * 16).stale


def test_an_instance_whose_nested_genie_failed_keeps_its_old_stamp(fx_install, monkeypatch):
    """outer itself and inner changed; inner cannot be loaded during the run (its copy in
    outer's instance stays as stored): the page must not quote outer's current stamp, or
    the old copy reads up to date."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.io import updatepages

    lib = _chain(fx_install)
    proj, page = _page(fx_install, [("fxdeep", "outer", {"Line": "P1"})])
    _set_text(lib, "inner", "%Tag%_NEW")
    d = ElementDocument.open_element(lib, "outer")
    d.create_object("rectangle", (60, 60, 90, 90))
    d.save()
    libraries_changed()
    real = GP._load_element

    def locked(lib_path, element, *a, **k):
        if element.lower() == "inner":
            raise PermissionError(13, "sharing violation (simulated)", lib_path)
        return real(lib_path, element, *a, **k)

    monkeypatch.setattr(GP, "_load_element", locked)
    libraries_changed()
    updatepages.update_pages(proj)
    monkeypatch.setattr(GP, "_load_element", real)
    libraries_changed()
    if not _has(page, "P1_OUT_MID_NEW"):
        assert EditableDocument.open(page).genie_changes().get(("fxdeep", "outer"), 1) is None
    updatepages.update_pages(proj)
    assert _has(page, "P1_OUT_MID_NEW")


def _locked_inner(monkeypatch):
    from ctgedit.format import genie_place as GP

    real = GP._load_element

    def locked(lib_path, element, *a, **k):
        if element.lower() == "inner":
            raise PermissionError(13, "sharing violation (simulated)", lib_path)
        return real(lib_path, element, *a, **k)

    monkeypatch.setattr(GP, "_load_element", locked)
    return real


def test_a_genie_left_as_stored_is_said_and_not_remembered(fx_install, monkeypatch):
    """A genie left as stored (its nested genie's library locked then) is said by Update
    Pages' result, and a helper's answer says it is unfinished (no remembered answer; the
    tab tries again on its next open, not only on the next change of a file)."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.infra import answer_memo
    from ctgedit.io import updatepages

    lib = _chain(fx_install)
    proj, page = _page(fx_install, [("fxdeep", "outer", {"Line": "P1"})])
    _set_text(lib, "inner", "%Tag%_NEW")
    d = ElementDocument.open_element(lib, "outer")
    d.create_object("rectangle", (60, 60, 90, 90))
    d.save()
    libraries_changed()
    real = _locked_inner(monkeypatch)
    libraries_changed()
    doc = EditableDocument.open(page)
    ch = doc.genie_changes()
    key = doc.genie_noop_key(ch)
    ans = GW.compute(page, doc.serialize(), ch, page_sha1=doc.genie_page_digest().hex())
    assert ans.get("unfinished")
    assert answer_memo.get(key) is None
    rep = updatepages.update_pages(proj)
    assert any("not every genie could be updated" in w for w in rep.warnings), (
        rep.warnings,
        rep.records,
        rep.updated,
        rep.stale,
    )
    monkeypatch.setattr(GP, "_load_element", real)
    libraries_changed()


def test_an_unfinished_answer_lets_the_tab_try_again(fx_install):
    """The window's handler of a landed genie job: an unfinished one empties the tab's
    'composed with these files' record, as a failed one does."""
    from types import SimpleNamespace
    from ctgedit.ui.window import files
    from ctgedit.editing_base import DocumentUiState

    doc = SimpleNamespace(ui=DocumentUiState(), path="x.ctg")
    doc.ui.genie_sources_done[("a", "b")] = frozenset({1})
    called = []
    win = SimpleNamespace(
        _docs=[{"doc": doc}],
        _note_left_by_previous=lambda d, j: None,
        _genies_updated=lambda d: called.append(d),
    )
    job = SimpleNamespace(
        unfinished=True, bindings=None, template=False, stepped=False, why="", quiet=True, below=None
    )
    try:
        files.PageFiles._genie_job_finished(win, doc, ["a.b"], 0, job)
    except Exception:  # noqa: BLE001 -- the rest of the handler needs a real window
        pass
    assert doc.ui.genie_sources_done == {}
