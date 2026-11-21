"""Link table stamps after a library save."""

import os
import struct

from ctgedit.format import writer

KEY = ("fxgenie", "fx_value")


def _genie_text(lib_dir, text):
    """Set fxgenie.fx_value's top-level text to ``text`` and save the genie: the element
    document (its previous_element = the version before)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(os.path.join(lib_dir, "fxgenie.ctm"), "fx_value")
    t = next(i for i, o in enumerate(d.page.objects) if o.parent is None and d.objects[i].type_code == 0x05)
    d.set_text(t, "%Description% " + text)
    d.save()
    libraries_changed()
    return d


def _count(doc, root, mark: bytes) -> int:
    return sum(mark in writer.object_payload(doc.objects[k]) for k in doc.subtree(root))


def _names(link_table_raw):
    from ctgedit.format import links

    t = links.parse_link_table(link_table_raw) if link_table_raw else None
    return [n for _g, n in (t[1] if t else []) if not n.startswith("\\")]


# --------------------------------------------------------------- every instance, or none
def test_an_instance_left_by_the_open_page_pass_keeps_the_page_out_of_date(fx_install):
    """The open-page pass after a genie save (previous version) updates the instance it
    reproduces and leaves one with a hand-moved member; the page keeps the genie's old
    stamp, so the full pass (_update_stale_genies, Update Pages) composes the one left."""
    from ctgedit.editing import EditableDocument

    lib_dir = fx_install.project("FxLib")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    for k in range(2):
        pd.place_library_element(*KEY, "ctm", (100 + 200 * k, 500), {"Description": "D%d" % k})
    (a, _k), (b, _k2) = list(pd.genie_instances())[-2:]
    pd.move_objects([pd.subtree(b)[1]], 7, 0)  # b no longer reproduces
    d = _genie_text(lib_dir, "EDITED")
    n = pd.update_genie_instances({KEY}, {KEY: d.previous_element})
    assert (n, pd.genie_update_skipped) == (1, 1)
    assert _count(pd, a, b"EDITED") == 1 and _count(pd, b, b"EDITED") == 0
    # (the page keeps the old stamp: {} would leave the instance old for good)
    assert pd.genie_changes() == {KEY: None}
    assert KEY in pd.stale_genie_elements()
    pd.update_genie_instances({KEY})  # the full pass
    a, b = [i for i, _k in pd.genie_instances()][-2:]
    assert _count(pd, a, b"EDITED") == 1 and _count(pd, b, b"EDITED") == 1
    assert pd.genie_changes() == {}


def test_an_instance_whose_composition_fails_keeps_the_page_out_of_date(fx_install, monkeypatch):
    """The full pass: one instance of a genie composes, the other's composition fails;
    the page still quotes the genie's old stamp."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP

    lib_dir = fx_install.project("FxLib")
    pd = EditableDocument.open(fx_install.page("FxShapes"))
    for k in range(2):
        pd.place_library_element(*KEY, "ctm", (100 + 200 * k, 500), {"Description": "D%d" % k})
    _genie_text(lib_dir, "EDITED")
    assert pd.genie_changes() == {KEY: None}
    real, calls = GP.refresh_instance, []

    def flaky(inst, *a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("a malformed instance")
        return real(inst, *a, **k)

    monkeypatch.setattr(GP, "refresh_instance", flaky)
    assert pd.update_genie_instances({KEY}) == 1
    assert pd.genie_changes() == {KEY: None}
    monkeypatch.setattr(GP, "refresh_instance", real)
    pd.update_genie_instances({KEY})
    assert pd.genie_changes() == {}


def _change_symbol(path, element):
    from ctgedit.editing import ElementDocument, libraries_changed

    e = ElementDocument.open_element(path, element)
    e.move_objects([list(e.page.roots())[0]], 4, 0)
    e.save()
    libraries_changed()


def test_a_symbol_group_whose_composition_fails_keeps_the_page_out_of_date(fx_install, monkeypatch):
    """FxMotion places fxsym.fx_box twice (group roots): one composes, the other fails;
    the page keeps the old stamp of fx_box."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP

    _change_symbol(fx_install.file("FxLib", "fxsym.ctl"), "fx_box")
    pd = EditableDocument.open(fx_install.page("FxMotion"))
    assert ("fxsym", "fx_box") in pd.stale_symbol_elements()
    real, calls = GP.refresh_instance, []

    def flaky(inst, *a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("a malformed instance")
        return real(inst, *a, **k)

    monkeypatch.setattr(GP, "refresh_instance", flaky)
    pd.update_symbol_placements()
    assert len(calls) == 2
    assert ("fxsym", "fx_box") in pd.stale_symbol_elements()
    monkeypatch.setattr(GP, "refresh_instance", real)
    pd.update_symbol_placements()
    assert ("fxsym", "fx_box") not in pd.stale_symbol_elements()


def test_a_leaf_symbol_whose_element_does_not_read_keeps_the_page_out_of_date(fx_install):
    """FxSymbols2 places icons.help1 twice (leaf records): one re-derives, the other's
    element does not read; the page keeps the old stamp of help1."""
    from ctgedit.editing import EditableDocument

    _change_symbol(fx_install.file("Include", "icons.ctl"), "help1")
    pd = EditableDocument.open(fx_install.page("FxSymbols2"))
    key = ("icons", "help1")
    assert key in pd.stale_symbol_elements()
    leaves = [i for i, k, kind in pd.symbol_placements() if k == key and kind == "leaf"]
    assert len(leaves) == 2
    real = pd._link_info
    pd._link_info = lambda po: None if po is pd.parsed.page.objects[leaves[1]] else real(po)
    pd.update_symbol_placements()
    assert key in pd.stale_symbol_elements()
    del pd._link_info
    pd.update_symbol_placements()
    assert key not in pd.stale_symbol_elements()


# --------------------------------------------------------------------------------- paste
def test_a_pasted_out_of_date_copy_keeps_the_page_out_of_date(fx_install):
    """FxShapes holds an fx_value of an older version; a copy of it pasted on FxArrange,
    which places fx_value at its current version, keeps FxArrange out of date for it."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import updatepages
    from ctgedit.format.symbols import SymbolResolver

    lib_dir = fx_install.project("FxLib")
    p1 = EditableDocument.open(fx_install.page("FxShapes"))
    p1.place_library_element(*KEY, "ctm", (100, 500), {"Description": "OLD"})
    p1.save()
    libraries_changed()
    _genie_text(lib_dir, "EDITED")
    p2 = EditableDocument.open(fx_install.page("FxArrange"))
    p2.place_library_element(*KEY, "ctm", (100, 300), {"Description": "NEW"})
    assert p2.genie_changes() == {}
    p1 = EditableDocument.open(fx_install.page("FxShapes"))
    i1 = [i for i, k in p1.genie_instances() if k == KEY][-1]
    stale = [n for n in _names(p1.parsed.page.link_table_raw) if n.lower().startswith("fxgenie.fx_value.")]
    p2.paste_payload(p1.copy_payload([i1]))
    assert p2.genie_changes() == {KEY: None}
    got = [n for n in _names(p2.parsed.page.link_table_raw) if n.lower().startswith("fxgenie.fx_value.")]
    assert got == stale  # one entry, the older stamp
    p2.save()
    libraries_changed()
    cur = updatepages._Current(SymbolResolver.for_page(p2.path))
    assert KEY in updatepages.stale_elements(p2.path, cur)
    p2.update_genie_instances({KEY})
    assert all(_count(p2, i, b"EDITED") == 1 for i, k in p2.genie_instances() if k == KEY)
    assert p2.genie_changes() == {}
    p2.undo()  # (the paste's table comes back)
    p2.undo()
    assert p2.genie_changes() == {}


def test_a_current_copy_pasted_where_an_older_one_is_listed_keeps_the_older(fx_install):
    """add_link_entry keep_older: the older of the two stamps stays, whichever comes
    first; without keep_older the listed entry stays as it is."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import links

    class _PB:
        area_code = "    "
        link_table_raw, link_count = b"", 0

    old = (b"\x01" * 16, "Lib.Elem.<ctm#00000010")
    new = (b"\x02" * 16, "lib.elem.<ctm#00000020")
    for first, second, keep, want in (
        (old, new, True, old),
        (new, old, True, (old[0], "lib.elem.<ctm#00000010")),
        (new, old, False, new),
    ):
        pb = _PB()
        GP.add_link_entry(pb, first)
        GP.add_link_entry(pb, second, keep_older=keep)
        assert links.parse_link_table(pb.link_table_raw)[1] == [want]


def test_a_stale_copy_pasted_into_an_element_is_composed_by_its_save(fx_install):
    """The same in an element tab: the element nests fx_value at its current version, a
    stale copy pasted into it makes the genie a nested change, and the save composes it."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.format import element as E

    lib_dir = fx_install.project("FxLib")
    p1 = EditableDocument.open(fx_install.page("FxShapes"))
    p1.place_library_element(*KEY, "ctm", (100, 500), {"Description": "OLD"})
    p1.save()
    libraries_changed()
    _genie_text(lib_dir, "EDITED")
    host = os.path.join(lib_dir, "fxhost.ctm")
    d = ElementDocument.new_element(host, "h", "genie")
    d.create_object("text", (0, 0, 80, 16), text="H")
    d.place_library_element(*KEY, "ctm", (10, 40), params={"Description": "NEW"})
    d.save()
    libraries_changed()
    d = ElementDocument.open_element(host, "h")
    assert KEY not in d.nested_genie_changes()
    p1 = EditableDocument.open(fx_install.page("FxShapes"))
    i1 = [i for i, k in p1.genie_instances() if k == KEY][-1]
    d.paste_payload(p1.copy_payload([i1]))
    assert KEY in d.nested_genie_changes()
    d.save()
    libraries_changed()
    pe = E.open_element(host, "h")
    assert sum(b"EDITED" in writer.object_payload(o) for o in pe.tree) == 2
    assert KEY not in ElementDocument.open_element(host, "h").nested_genie_changes()


# ----------------------------------------------------------- nested: the element's path
def test_nested_genies_resolve_on_the_nesting_elements_own_path(fx_install):
    """FxMain holds its own fxnest.ctm with an n0; a host in FxMain nests FxLib's n1,
    which nests FxLib's n0. The composition takes FxLib's n0 (n1's own search path), and
    so does the stamp check: the host is current after its save."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E

    main = fx_install.project("FxMain")
    d = ElementDocument.new_element(os.path.join(main, "fxnest.ctm"), "n0", "genie")
    d.create_object("text", (0, 0, 80, 16), text="SHADOW %Label%")
    d.save()
    libraries_changed()
    host = os.path.join(main, "fxhost.ctm")
    d = ElementDocument.new_element(host, "h", "genie")
    d.create_object("text", (0, 0, 80, 16), text="H")
    assert d.place_library_element("fxnest", "n1", "ctm", (10, 40), params={"Name": "X"})
    d.save()
    libraries_changed()
    assert not any(b"SHADOW" in writer.object_payload(o) for o in E.open_element(host, "h").tree)
    h = ElementDocument.open_element(host, "h")
    assert h.nested_genie_changes() == frozenset()
    # a change of FxLib's n0 (the one composed) is seen; FxMain's n0 is not n1's
    _bump(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    assert ("fxnest", "n0") in ElementDocument.open_element(host, "h").nested_genie_changes()


def _bump(lib, element):
    from ctgedit.editing import ElementDocument, libraries_changed

    e = ElementDocument.open_element(lib, element)
    e.move_objects([list(e.page.roots())[0]], 3, 0)
    e.save()
    libraries_changed()


# ------------------------------------------------------------------ the last-dot rule
def test_a_library_with_a_dot_in_its_name_keeps_its_import_entry(fx_install):
    """A genie of library file plant.v2.ctm nested in an element: the element's import
    table lists it (the last-dot rule), its change is a nested change, and the usage index
    reads the entry's library as plant.v2."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E
    from ctgedit.io import genieusage

    lib_dir = fx_install.project("FxLib")
    dotted = os.path.join(lib_dir, "plant.v2.ctm")
    host = os.path.join(lib_dir, "fxhost.ctm")
    d = ElementDocument.new_element(dotted, "pump", "genie")
    d.create_object("text", (0, 0, 80, 16), text="PUMP")
    d.save()
    libraries_changed()
    d = ElementDocument.new_element(host, "h", "genie")
    d.create_object("text", (0, 0, 80, 16), text="H")
    assert d.place_library_element("plant.v2", "pump", "ctm", (10, 40))
    d.save()
    libraries_changed()
    names = _names(E.open_element(host, "h").page.link_table_raw)
    assert [n.rsplit("#", 1)[0] for n in names] == ["plant.v2.pump.<ctm"]
    assert genieusage._entry_key(names[0]) == ("plant.v2", "pump", "ctm")
    d = ElementDocument.open_element(dotted, "pump")
    t = next(i for i, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.set_text(t, "PUMP EDITED")
    d.save()
    libraries_changed()
    h = ElementDocument.open_element(host, "h")
    assert h.nested_genie_changes() == frozenset({("plant.v2", "pump")})
    h.save(refresh=True)
    libraries_changed()
    assert ElementDocument.open_element(host, "h").nested_genie_changes() == frozenset()


def test_the_page_genie_sources_split_at_the_last_dot(fx_install):
    """EditableDocument.genie_sources reads a dotted library's entry (the open check)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed

    lib_dir = fx_install.project("FxLib")
    d = ElementDocument.new_element(os.path.join(lib_dir, "plant.v2.ctm"), "pump", "genie")
    d.create_object("text", (0, 0, 80, 16), text="PUMP")
    d.save()
    libraries_changed()
    pd = EditableDocument.open(fx_install.page("FxShapes"))  # (FxMain includes FxLib)
    assert pd.library_path("plant.v2", "pump", "ctm")
    pd.place_library_element("plant.v2", "pump", "ctm", (100, 500))
    got = pd.genie_sources({("plant.v2", "pump")})
    assert ("plant.v2", "pump") in got
    assert any(x[0].endswith("plant.v2.ctm") for x in got[("plant.v2", "pump")])


# ------------------------------------------------------------ strict kind of library
def test_a_ctm_entry_whose_element_only_a_ctl_holds_is_not_out_of_date(fx_install):
    """A page quotes fxgenie.fx_value.<ctm; the library file is renamed fxgenie.ctl. The
    .ctm entry does not resolve (an entry names its kind of library), so it is not called
    out of date for ever."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import links
    from ctgedit.format.symbols import SymbolResolver
    from ctgedit.io import updatepages

    lib_dir = fx_install.project("FxLib")
    page = fx_install.page("FxShapes")
    pd = EditableDocument.open(page)
    pd.place_library_element(*KEY, "ctm", (100, 500), {"Description": "A"})
    pd.save()
    libraries_changed()
    os.replace(os.path.join(lib_dir, "fxgenie.ctm"), os.path.join(lib_dir, "fxgenie.ctl"))
    libraries_changed()
    pd = EditableDocument.open(page)
    entry = next(
        n
        for n in _names(pd.parsed.page.link_table_raw)
        if links.strip_stamp(n).lower() == "fxgenie.fx_value.<ctm"
    )
    cur = updatepages._Current(SymbolResolver.for_page(page))
    assert cur.stamp_name(entry) is None
    assert KEY not in pd.genie_changes()


# ----------------------------------------------------------------------- unique stamps
def _stamp(lib, element):
    from ctgedit.format import element as E

    return struct.unpack_from("<I", bytes(E.open_element(lib, element).page.page_identity), 0)[0]


def test_two_saves_in_one_second_give_the_element_two_stamps(fx_install, monkeypatch):
    """The clock stands still (two saves within one second): the second save's stamp is
    the first one's + 1, so n1, updated between them, sees n0 change again."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E

    monkeypatch.setattr(E, "ctdraw_timestamp", lambda: 0x40000000)
    lib = fx_install.file("FxLib", "fxnest.ctm")

    def edit_n0(txt):
        d = ElementDocument.open_element(lib, "n0")
        i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
        assert d.set_text(i, txt)
        d.save()
        libraries_changed()

    edit_n0("%Label% AAAA")
    s1 = _stamp(lib, "n0")
    ElementDocument.open_element(lib, "n1").save()
    libraries_changed()
    edit_n0("%Label% BBBB")
    s2 = _stamp(lib, "n0")
    assert s2 == s1 + 1
    h = ElementDocument.open_element(lib, "n1")
    assert ("fxnest", "n0") in h.nested_genie_changes()
    h.save()
    libraries_changed()
    assert any(b"BBBB" in writer.object_payload(o) for o in E.open_element(lib, "n1").tree)
    # the container's time stamp follows the element's
    assert E.LibraryFile.open(lib).ts_modified == s2


def test_stamp_after():
    from ctgedit.format import element as E

    head = bytearray(0x40)

    def block(stamp):
        b = bytearray(head)
        struct.pack_into("<I", b, 0x20, stamp)
        return bytes(b)

    assert E.stamp_after(block(5), None) == block(5)
    assert E.stamp_after(block(9), block(5)) == block(9)  # newer: kept
    assert E.stamp_after(block(5), block(5)) == block(6)
    assert E.stamp_after(block(3), block(5)) == block(6)  # a clock set back
    assert E.stamp_after(block(3), block(0xFFFFFFFF)) == block(3)


# ------------------------------------------------------------------ the note after a save
def test_the_genie_saved_note_counts_the_copies_left(fx_install, monkeypatch):
    """The Genie Saved note counts the copies left."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui import notify as N
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    lib_dir = fx_install.project("FxLib")
    w = MainWindow()
    try:
        w.open_document(fx_install.page("FxShapes"))
        pd = w.doc
        for k in range(2):
            pd.place_library_element(*KEY, "ctm", (100 + 200 * k, 500), {"Description": "D%d" % k})
        b = list(pd.genie_instances())[-1][0]
        pd.move_objects([pd.subtree(b)[1]], 7, 0)
        notes, queued = [], []
        monkeypatch.setattr(N, "notify", lambda _w, text, *a, **k: notes.append(text))
        monkeypatch.setattr(w, "_queue_open_genie_updates", lambda: queued.append(1))
        for auto, text in (
            (False, "1 placed fxgenie.fx_value on FxShapes is left out of date. Update Pages updates it."),
            (True, None),
        ):
            app_settings().setValue("opt/auto_update_genies", auto)
            d = _genie_text(lib_dir, "EDITED %s" % auto)
            notes.clear()
            w._propagate_element(d)
            if text is None:
                assert notes == []
            else:
                assert text in notes, notes
                assert not any("Graphics Builder" in t or "in full" in t for t in notes)
            assert pd.genie_changes() == {KEY: None}
        assert queued == [1]
        app_settings().setValue("opt/auto_update_genies", True)
    finally:
        for ent in list(w._docs):
            if hasattr(ent["doc"], "dirty"):
                ent["doc"].dirty = False
        w.doc = None
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.close()


def test_a_fixed_identity_keeps_the_fixed_clocks_stamp(fx_install, monkeypatch):
    """CTG_FIXED_IDENTITY stands the clock still for the fixture generator: every save
    takes the fixed clock's stamp, so tools/make_fixtures.py writes tests/fixtures again
    byte for byte."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.infra import identity

    monkeypatch.setenv(identity.ENV, "fx")
    lib = fx_install.file("FxLib", "fxnest.ctm")
    for txt in ("%Label% AAAA", "%Label% BBBB"):
        d = ElementDocument.open_element(lib, "n0")
        i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
        assert d.set_text(i, txt)
        d.save()
        libraries_changed()
        assert _stamp(lib, "n0") == identity.now_stamp()


def test_a_genie_of_a_dotted_library_nested_in_a_placed_genie_is_derived_again(fx_install, monkeypatch):
    """FxShapes places fxhost.h, which nests plant.v2.pump: after pump's save the page's
    units that read plant.v2.ctm are derived again (reader._library_stale_records walks
    the genies' import tables, library and element split at the LAST dot)."""
    from ctgedit.editing import EditableDocument, ElementDocument, libraries_changed
    from ctgedit.io import reader

    lib_dir = fx_install.project("FxLib")
    dotted = os.path.join(lib_dir, "plant.v2.ctm")
    d = ElementDocument.new_element(dotted, "pump", "genie")
    d.create_object("text", (0, 0, 80, 16), text="PUMP")
    d.save()
    libraries_changed()
    host = os.path.join(lib_dir, "fxhost.ctm")
    d = ElementDocument.new_element(host, "h", "genie")
    d.create_object("text", (0, 0, 80, 16), text="H")
    assert d.place_library_element("plant.v2", "pump", "ctm", (10, 40))
    d.save()
    libraries_changed()
    pg = EditableDocument.open(fx_install.page("FxShapes"))
    pg.place_library_element("fxhost", "h", "ctm", (100, 500))
    pg._derive()
    seen = []
    real = reader._library_stale_records

    def spy(prev, path):
        got = real(prev, path)
        seen.append(got)
        return got

    monkeypatch.setattr(reader, "_library_stale_records", spy)
    e = ElementDocument.open_element(dotted, "pump")
    t = next(i for i, o in enumerate(e.parsed.page.objects) if o.type_code == 0x05)
    e.set_text(t, "PUMP EDITED")
    e.save()
    libraries_changed()
    pg._derive()
    assert seen and seen[-1] is not None and seen[-1][1]
