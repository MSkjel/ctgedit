"""A nested genie copy's metadata follows its values."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _md(po):
    from ctgedit.format import genie_place as GP

    return [
        k
        for e in GP.decode(po).entries or ()
        for b in e
        for k, _v in b.pairs
        if k.upper().startswith("METADATA_")
    ]


def _stray(doc):
    """(index, dwFlags) of the records with the METADATA bit and no METADATA_ value."""
    return [
        (j, hex(p.subtype_size))
        for j, p in enumerate(doc.parsed.page.objects)
        if p.subtype_size & 0x100 and not _md(p)
    ]


def _genie(lib, name, meta):
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.new_element(lib, name, "genie")
    d.create_object("text", (0, 0, 80, 16), text="%Tag%")
    d.create_object("rectangle", (0, 20, 40, 40))
    if meta:
        assert d.set_metadata(len(d.parsed.page.objects) - 1, [("alm", "%Tag%.alm")])
    d.save()
    libraries_changed()


def _setup(fx):
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = os.path.join(fx.project("FxLib"), "fxmeta.ctm")
    _genie(lib, "old", True)
    _genie(lib, "new", False)
    h = ElementDocument.new_element(lib, "host", "genie")
    h.place_library_element("fxmeta", "old", "ctm", (10, 10), params={"Tag": "%Name%_X"})
    h.save()
    libraries_changed()
    o = ElementDocument.new_element(lib, "outer", "genie")
    o.place_library_element("fxmeta", "host", "ctm", (10, 10), params={"Name": "%Line%"})
    o.save()
    libraries_changed()
    assert any(_md(p) for p in ElementDocument.open_element(lib, "outer").parsed.page.objects)
    return lib


def test_replace_genie_run_leaves_no_stray_metadata_bit_in_the_hosts(fx_install):
    """The project-wide run: 'host' changed and saved, the chain saves 'outer' again."""
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _setup(fx_install)
    res = GW.apply(
        {
            "pages": [],
            "elements": [{"library": lib, "element": "host"}],
            "old": [["fxmeta", "old"]],
            "new": ["fxmeta", "new"],
            "mapping": {"Tag": "%Tag%"},
            "old_params": ["Tag"],
            "builder_open": [],
            "backup": None,
        }
    )
    assert res["elements_updated"] == ["fxmeta.host"] and "fxmeta.outer" in res["chain_elements_saved"], res
    libraries_changed()
    for el in ("host", "outer"):
        d = ElementDocument.open_element(lib, el)
        assert _stray(d) == [], el
    # the rectangle of the copy is the definition's: no bit, no metadata
    o = ElementDocument.open_element(lib, "outer").parsed.page.objects
    rect = [p for p in o if p.type_code == 0x0F]
    assert rect and all(not p.subtype_size & 0x100 and not _md(p) for p in rect)


def test_a_nested_copy_takes_the_metadata_its_genie_lost(fx_install):
    """Any edit: 'old' loses its metadata, 'host' and then 'outer' are composed again."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = _setup(fx_install)
    d = ElementDocument.open_element(lib, "old")
    rect = next(j for j, p in enumerate(d.parsed.page.objects) if p.type_code == 0x0F)
    assert d.set_metadata(rect, [])
    d.save()
    libraries_changed()
    for el in ("host", "outer"):
        e = ElementDocument.open_element(lib, el)
        e.update_nested_genies(every=True)
        assert _stray(e) == [], el
        e.save()
        libraries_changed()
    assert _stray(ElementDocument.open_element(lib, "outer")) == []


def _rect_with_metadata(fx):
    """The decoded rectangle of genie 'old' (a METADATA_0 value, the bit and the section)."""
    from ctgedit.editing import ElementDocument
    from ctgedit.format import genie_place as GP

    lib = _setup(fx)
    po = next(p for p in ElementDocument.open_element(lib, "old").parsed.page.objects if p.type_code == 0x0F)
    r = GP.decode(po)
    assert r.dw & 0x100 and GP.has_metadata(r.entries[0][1])
    return r


def test_only_a_bit_without_metadata_values_is_dropped(fx_install):
    """genie_place.drop_stale_metadata: the bit and section go only when the values hold
    no METADATA_ key; a section that differs from its values is kept as stored (CtDraw
    saves such nested members), and values without the bit are left
    without it."""
    from ctgedit.format import genie_place as GP

    r = _rect_with_metadata(fx_install)
    section = r.cls[: GP.metadata_len(r.cls)]
    rest = r.cls[len(section) :]
    # (a) the values lost their metadata: the bit and the section go, the rest stays
    a = GP.decode(GP.to_po(GP.encode(r)))
    a.entries[0][1] = GP.Bag(
        [(k, v) for k, v in a.entries[0][1].pairs if not k.upper().startswith("METADATA_")],
        a.entries[0][1].ov,
    )
    assert GP.drop_stale_metadata(a)
    assert not a.dw & 0x100 and a.cls == rest
    # (b) values with metadata and a section that differs from them: kept as stored
    b = GP.decode(GP.to_po(GP.encode(r)))
    b.entries[0][1].set("METADATA_0", "alm=something else")
    before = GP.encode(b)
    assert not GP.drop_stale_metadata(b) and GP.encode(b) == before
    # (c) metadata values without the bit: left without it
    c = GP.decode(GP.to_po(GP.encode(r)))
    c.cls, c.dw = rest, c.dw & ~0x100
    before = GP.encode(c)
    assert not GP.drop_stale_metadata(c) and GP.encode(c) == before


def test_copies_with_metadata_keep_their_records(fx_install):
    """The run changes nothing in a copy's records that still hold metadata: 'outer' places
    'old' too (not replaced: only 'host' places what is replaced), and that copy's records
    come back from the chain byte for byte."""
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import writer

    lib = _setup(fx_install)
    o = ElementDocument.open_element(lib, "outer")
    _k = o.place_library_element("fxmeta", "old", "ctm", (200, 10), params={"Tag": "%Line%_Y"})
    o.save()
    libraries_changed()
    o = ElementDocument.open_element(lib, "outer")
    keep = [i for i, l in o.genie_instances() if l == ("fxmeta", "old")]
    before = [writer.object_payload(o.parsed.page.objects[j]) for j in o.subtree(keep[0])]
    GW.apply(
        {
            "pages": [],
            "elements": [{"library": lib, "element": "host"}],
            "old": [["fxmeta", "old"]],
            "new": ["fxmeta", "new"],
            "mapping": {"Tag": "%Tag%"},
            "old_params": ["Tag"],
            "builder_open": [],
            "backup": None,
        }
    )
    libraries_changed()
    o = ElementDocument.open_element(lib, "outer")
    keep = [i for i, l in o.genie_instances() if l == ("fxmeta", "old")]
    after = [writer.object_payload(o.parsed.page.objects[j]) for j in o.subtree(keep[0])]
    assert after == before
    assert _stray(o) == []


def test_a_rebuilt_member_takes_the_metadata_bit_of_its_new_class_section(fx_install):
    """A rebuilt member takes the metadata bit of its new class section."""
    from ctgedit.workers import genieswap_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = os.path.join(fx_install.project("FxLib"), "fxmeta.ctm")
    _genie(lib, "old", True)
    d = ElementDocument.new_element(lib, "new", "genie")
    d.create_object("text", (0, 0, 80, 16), text="%Tag%")
    d.create_object("rectangle", (0, 20, 40, 40))
    d.add_input(len(d.parsed.page.objects) - 1, "touch", {"CMD": "Beep(%Tag%)"})
    d.save()
    libraries_changed()
    h = ElementDocument.new_element(lib, "host", "genie")
    h.place_library_element("fxmeta", "old", "ctm", (10, 10), params={"Tag": "%Name%_X"})
    h.save()
    libraries_changed()
    o = ElementDocument.new_element(lib, "outer", "genie")
    o.place_library_element("fxmeta", "host", "ctm", (10, 10), params={"Name": "%Line%"})
    o.save()
    libraries_changed()
    res = GW.apply(
        {
            "pages": [],
            "elements": [{"library": lib, "element": "host"}],
            "old": [["fxmeta", "old"]],
            "new": ["fxmeta", "new"],
            "mapping": {"Tag": "%Tag%"},
            "old_params": ["Tag"],
            "builder_open": [],
            "backup": None,
        }
    )
    assert "fxmeta.outer" in res["chain_elements_saved"], res
    libraries_changed()
    out = ElementDocument.open_element(lib, "outer")
    assert _stray(out) == []
    rects = [p for p in out.parsed.page.objects if p.type_code == 0x0F]
    assert rects and all(not p.subtype_size & 0x100 for p in rects)
    # the copy has the touch command the new genie gave its rectangle
    from ctgedit.format.codes import TC

    assert any(p.type_code == TC.TOUCH for p in out.parsed.page.objects)
