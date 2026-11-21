"""The composite genie composer."""

import glob
import os
import pathlib
import shutil

import pytest
from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

#: FxLib's FxDriveC template (Composite Genies/fx_drive_c.xml)
DRIVE = "8d3f5e4c-af60-4b7c-9d8e-9fa0b1c2d3e4"
DRIVE_XML = "fx_drive_c.xml"
#: the page the editor tests copy (nine instances of the six templates)
PAGE = "FxComposites"


@pytest.fixture()
def store(fx_install):
    """FxMain's _CompositionCaches in the fx_install copy."""
    return os.path.join(fx_install.project("FxMain"), "_CompositionCaches")


@pytest.fixture()
def tpl_dir(fx_install):
    """FxLib's Composite Genies folder in the fx_install copy."""
    return os.path.join(fx_install.project("FxLib"), "Composite Genies")


def _snapshot(store):
    out = {}
    for p in glob.glob(os.path.join(store, "**", "*"), recursive=True):
        if os.path.isfile(p):
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture()
def store_untouched(store):
    """Fails the test if anything under FxMain's caches changed."""
    before = _snapshot(store)
    yield
    assert _snapshot(store) == before, "FxMain's composition caches were written"


@pytest.fixture()
def proj(fx_install, store_untouched):
    """A scratch project FxScratch (including FxMain, FxLib, FxStyle: its libraries,
    templates and the caches its page's composites link) holding a copy of FxComposites;
    NO composition cache of its own, no page tables (every page counts)."""
    d = fx_install.new_project("FxScratch", includes=("FxMain", "FxLib", "FxStyle"), tables=False)
    shutil.copy(fx_install.page(PAGE), os.path.join(d, PAGE + ".ctg"))
    return pathlib.Path(d)


def _instances(store, guid):
    d = os.path.join(store, "{%s}" % guid)
    return d, sorted(f[:-4] for f in os.listdir(os.path.join(d, "Instances")))


def _caches(store):
    """(guid, cache folder) of every template cache of ``store``."""
    return [(os.path.basename(d).strip("{}"), d) for d in sorted(glob.glob(os.path.join(store, "{*}")))]


# -- the pure parts: names, digests, the template copy -------------------------------------
def test_instance_hashes_match_every_fixture_instance(store):
    """The instance name = md5(composition id + NAME[value] ...) of the
    adapted options, over the first composition whose prerequisite holds:
    every one of the nine fixture instance files is named by it."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C

    n = 0
    for guid, d in _caches(store):
        model = CP.load_model(os.path.join(d, guid.replace("-", "") + ".xml"))
        for f in glob.glob(os.path.join(d, "Instances", "*.xml")):
            inst = C.load_instance(f)
            vals = CP.adapted_values(model, inst.values())
            comp = model.composition_for({k.lower(): v for k, v in vals.items()})
            assert CP.instance_hash(comp.id, vals) == os.path.basename(f)[:-4], f
            n += 1
    assert n == 10


def test_template_digest_and_copy_match_the_caches(store, tpl_dir):
    """metadata.xml's digest = base64(SHA1(C14N of the template as written)), and the
    cache's copy of the template = XDocument.Save of it (byte for byte): each of the six
    caches has its FxLib template."""
    from ctgedit.format import composer as CP

    checked = 0
    for guid, d in _caches(store):
        copy = os.path.join(d, CP.copied_template_name(guid))
        md = CP.read_metadata(os.path.join(d, "metadata.xml"))
        with open(copy, "rb") as fh:
            data = fh.read()
        assert md.digest == CP.template_digest(data), d
        for src in glob.glob(os.path.join(tpl_dir, "*.xml")):
            with open(src, "rb") as fh:
                raw = fh.read()
            if CP.template_digest(raw) == md.digest:
                assert CP.template_copy(raw) == data, src
                checked += 1
    assert checked == 6


def _compose_ctx(fx_install):
    from ctgedit.format import composer_build as CB

    return CB.ComposeContext(os.path.join(fx_install.project("FxMain"), "x.ctg"))


def test_instance_files_are_the_composers(fx_install, store):
    """Instance files are written as the composer writes them."""
    import shutil
    from ctgedit.editing import libraries_changed
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C

    ctx = _compose_ctx(fx_install)
    n = 0
    for guid, d in _caches(store):
        tp = os.path.join(d, CP.copied_template_name(guid))
        with open(tp, "rb") as fh:
            data = fh.read()
        model = CP.parse_model(data, tp)
        for h in _instances(store, guid)[1]:
            lay = ctx.compose(
                model, C.load_instance(os.path.join(d, "Instances", h + ".xml")).values(), final=False
            )
            assert lay.hash == h
            flags = ctx.flag_sizes() if lay.has_alarm_flags() else None
            out = CP.instance_xml(
                guid,
                CP.template_digest(data),
                lay.values,
                [(it, ctx.version(it)) for it in lay.used_items()],
                flags,
            )
            with open(os.path.join(d, "Instances", h + ".xml"), "rb") as fh:
                assert out == fh.read(), (guid, h)
            n += 1
    assert n == 10
    assert ctx.flag_sizes() == [(20, 20)]  # Include's rows are not FxMain's own
    inc, main = fx_install.project("Include"), fx_install.project("FxMain")
    for name in ("almprior.DBF", "almprior.NDX", "almmodes.DBF", "almmodes.NDX"):
        assert not os.path.exists(os.path.join(main, name)), name
        shutil.copy(os.path.join(inc, name), os.path.join(main, name))
    libraries_changed()
    # (fx_p1 .. fx_p3: a 20 x 12, 24 x 14, 16 x 10 rectangle and a text whose TXT_STR reads
    # the Label, measured as the display "=Label" shows it)
    assert _compose_ctx(fx_install).flag_sizes() == [(20, 20), (35, 14), (40, 16), (25, 11)]


def test_layout_places_the_fixture_contents(fx_install, store):
    """The WPF layout + GenieLayout paste: the element origin (hot spot) of every fixture
    instance as its cache stores it."""
    import struct
    from ctgedit.format import composer as CP
    from ctgedit.format import composer_build as CB
    from ctgedit.format import composites as C
    from ctgedit.format import element as E

    ctx = _compose_ctx(fx_install)
    tot = ok = 0
    for guid, d in _caches(store):
        model = CP.load_model(os.path.join(d, CP.copied_template_name(guid)))
        for h in _instances(store, guid)[1]:
            pe = E.open_element(os.path.join(d, "cache.ctm"), h)
            lay = ctx.compose(
                model, C.load_instance(os.path.join(d, "Instances", h + ".xml")).values(), final=False
            )
            recs, hot = CB.build_records(lay, ctx)
            content = E.new_element_content(recs, origin=hot, ext="ctm", resolver=ctx.resolver)
            tot += 1
            if struct.unpack_from("<2i", content, 0x0C) == tuple(pe.origin):
                ok += 1
    assert tot == 10 and ok == tot, (ok, tot)


# -- the cache writer ------------------------------------------------------------------------
def test_compose_writes_the_fixture_cache_files(proj, store, tpl_dir):
    """compose writes the fixture's cache files into a project without a cache."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composer_store as CS
    from ctgedit.format import composites as C
    from ctgedit.format import element as E
    from ctgedit.format import writer

    page = str(proj / (PAGE + ".ctg"))
    n = 0
    for guid, name in ((DRIVE, DRIVE_XML), ("7c2e4d3b-9e5f-4a6b-8c7d-8e9f0a1b2c3d", "fx_valve_c.xml")):
        d, hashes = _instances(store, guid)
        for k, h in enumerate(hashes):
            inst = C.load_instance(os.path.join(d, "Instances", h + ".xml"))
            res = CS.compose(page, os.path.join(tpl_dir, name), inst.values(), final=False)
            assert res.hash == h and res.new_instance
            assert res.new_template_copy == (k == 0)  # (the template's first instance)
            assert res.cache_dir == os.path.join(str(proj), "_CompositionCaches", "{%s}" % guid)
            for fn in (os.path.join("Instances", h + ".xml"), CP.copied_template_name(guid)):
                with open(os.path.join(res.cache_dir, fn), "rb") as a, open(os.path.join(d, fn), "rb") as b:
                    assert a.read() == b.read(), fn
            md = CP.read_metadata(os.path.join(res.cache_dir, "metadata.xml"))
            old = CP.read_metadata(os.path.join(d, "metadata.xml"))
            assert md.digest == old.digest
            mine, theirs = (
                E.open_element(res.library_path, h),
                E.open_element(os.path.join(d, "cache.ctm"), h),
            )
            ma, ta = mine.page.objects[: mine.root_end], theirs.page.objects[: theirs.root_end]
            assert len(ma) == len(ta)
            assert [writer.object_payload(x) for x in ma] == [writer.object_payload(y) for y in ta]
            assert mine.trailer == theirs.trailer
            assert mine.page.link_table_raw == theirs.page.link_table_raw
            # the cache library's container stamp is the element's save stamp (the
            # identity's first u32), as every library save gives it
            import struct

            lib = E.LibraryFile.open(res.library_path)
            blk = lib.block_data(lib.find(h))
            assert lib.ts_modified == struct.unpack_from("<I", blk, 0x20)[0]
            assert CS.compose(page, os.path.join(tpl_dir, name), inst.values(), final=False).written == []
            n += 1
        # every instance of the template composed: the versions are the committed cache's
        assert md.versions == old.versions
    assert n == 4
    assert not os.path.exists(os.path.join(str(proj), "_CompositionCaches", CS.LOCK_FILE))


def test_a_held_cache_lock_refuses_the_update(tmp_path):
    """Another program holding _000.cache.lock (CtDraw's CacheLock) blocks
    the compose with the composer's message; a stale lock is taken over."""
    from ctgedit.format import composer_store as CS

    proj = tmp_path / "FxLockProj"
    proj.mkdir()
    root = proj / "_CompositionCaches"
    root.mkdir()
    lock = root / CS.LOCK_FILE
    lock.write_bytes(b"")  # stale: nobody has it open
    with CS.cache_lock(str(proj)):
        pass
    assert not lock.exists()
    fh = open(lock, "wb")  # held open: cannot be taken
    try:
        with pytest.raises(CS.CacheLockError) as e:
            with CS.cache_lock(str(proj)):
                pass
        assert str(e.value) == CS.MSG_LOCKED
    finally:
        fh.close()


def _drive_without_status(doc):
    """The FxDriveC instance of the page whose DisplayStatus is None (FxPlant.Pump2)."""
    return next(
        k
        for k in doc.composite_roots()
        if doc.composite_link(k)[0] == DRIVE and doc.composite_info(k).values()["displaystatus"] == "None"
    )


# -- the editor ----------------------------------------------------------------------------
def _doc(proj):
    from ctgedit.editing import EditableDocument

    return EditableDocument.open(str(proj / (PAGE + ".ctg")))


def test_paste_drive_into_a_project_without_an_instance(proj, store, tpl_dir):
    """Paste Composite Genie into a project without a matching instance."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C
    from ctgedit.format import element as E
    from ctgedit.format import genie_place as GP
    from ctgedit.format import geometry as G
    from ctgedit.format import links

    doc = _doc(proj)
    n0 = len(doc.parsed.page.objects)
    opts = {"Equipment": "FxPlant.Mixing.NEWDRIVE", "DriveType": "Fan"}
    idx = doc.paste_composite_genie(DRIVE, (100, 700), opts)
    assert idx == n0 and doc.can_undo()
    cache = os.path.join(str(proj), "_CompositionCaches", "{%s}" % DRIVE)
    link = doc.composite_link(idx)
    assert link is not None and link[0] == DRIVE
    h = link[1]
    assert os.path.isfile(os.path.join(cache, "Instances", h + ".xml"))
    assert h not in _instances(store, DRIVE)[1]
    inst = C.load_instance(os.path.join(cache, "Instances", h + ".xml"))
    assert inst.values()["Equipment"] == opts["Equipment"] and not inst.problems
    with open(os.path.join(tpl_dir, DRIVE_XML), "rb") as fh:
        digest = CP.template_digest(fh.read())
    assert CP.read_metadata(os.path.join(cache, "metadata.xml")).digest == digest
    assert CP.read_metadata(os.path.join(cache, "Instances", h + ".xml")).digest == digest
    el = E.open_element(os.path.join(cache, "cache.ctm"), h)
    assert dict(el.trailer.props)[E.KEY_COMPOSITE_ID] == "1"
    root = doc.parsed.page.objects[idx]
    gh = G.parse_geom_header(root.body_raw)
    assert (gh.rect1[0], gh.rect1[1]) == (100, 700) and root.subtype_size & 0x201 == 0x201
    t = links.parse_link_table(doc.parsed.page.link_table_raw)
    assert any(h in n for _g, n in t[1])
    assert doc.composite_status(idx) == "current"
    assert doc.page.objects[idx].kind.value == "composite_genie"
    out = str(proj / "Out.ctg")
    doc.save(out)
    from ctgedit.editing import EditableDocument

    d2 = EditableDocument.open(out)
    # (the page links the new cache element; drawn from its own member records)
    assert "_compositioncaches" in (d2.page.objects[idx].raw.get("_ref") or "").lower()
    assert len(d2.parsed.page.objects) == len(doc.parsed.page.objects)
    doc.composite_files = []
    idx2 = doc.paste_composite_genie(DRIVE, (300, 700), opts)
    assert doc.composite_files == [] and doc.composite_link(idx2)[1] == h
    doc.undo()
    doc.undo()
    assert len(doc.parsed.page.objects) == n0
    assert GP.decode(root).link.endswith(h + ".<ctm")


def test_invalid_options_are_refused_before_anything_is_written(proj):
    doc = _doc(proj)
    n0 = len(doc.parsed.page.objects)
    with pytest.raises(ValueError):
        doc.paste_composite_genie(DRIVE, (0, 0), {"Equipment": ""})  # MinLength 1
    with pytest.raises(ValueError):
        doc.paste_composite_genie("00000000-0000-0000-0000-000000000000", (0, 0), {})
    assert len(doc.parsed.page.objects) == n0
    assert not (proj / "_CompositionCaches").exists()


def test_edit_composite_options_relinks_and_keeps_the_hot_spot(proj):
    """Edit Composite Genie > OK: a new instance is composed, the placed
    genie relinks to it (its Local -- the hot spot's page point -- kept),
    the link table lists the new element; undo goes back to the old
    instance, whose files stay until Pack Libraries."""
    from ctgedit.format import links, placement

    doc = _doc(proj)
    comp = _drive_without_status(doc)
    old = doc.composite_link(comp)
    local0 = placement.read_slots(doc.parsed.page.objects[comp].body_raw)[0][0]
    assert doc.set_composite_options(comp, {"DisplayStatus": "Inside Right"}) is True
    new = doc.composite_link(comp)
    assert new[1] != old[1] and new[2] == old[2]
    assert doc.composite_info(comp).values()["displaystatus"] == "Inside Right"
    from ctgedit.format import element

    lib_path = proj / "_CompositionCaches" / ("{%s}" % DRIVE) / "cache.ctm"
    preview = element.open_element(str(lib_path), new[1]).page.thumbnail
    assert preview.startswith(b"TH")
    pixels = preview[54:]
    assert len(set(pixels[i : i + 4] for i in range(0, len(pixels), 4))) > 1
    local1 = placement.read_slots(doc.parsed.page.objects[comp].body_raw)[0][0]
    assert local1[:2] == local0[:2]
    t = links.parse_link_table(doc.parsed.page.link_table_raw)
    names = [n for _g, n in t[1]]
    assert any(new[1] in n for n in names)
    assert doc.set_composite_options(comp, {"DisplayStatus": "Inside Right"}) is False
    doc.undo()
    assert doc.composite_link(comp)[1] == old[1]
    assert os.path.isfile(
        os.path.join(str(proj), "_CompositionCaches", "{%s}" % DRIVE, "Instances", new[1] + ".xml")
    )


def test_stale_composite_uses_the_replacement_options_saved_for_that_instance(proj, tpl_dir):
    import xml.etree.ElementTree as ET
    from ctgedit.format import composites

    templates = proj / "Composite Genies"
    templates.mkdir()
    path = templates / DRIVE_XML
    shutil.copy(os.path.join(tpl_dir, DRIVE_XML), path)
    doc = _doc(proj)
    idx = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.D01"})
    info = doc.composite_info(idx)
    source = info.instance.path
    tree = ET.parse(source)
    replacements = ET.SubElement(tree.getroot(), "ParameterMigrations")
    for param in tree.getroot().find("Parameters"):
        attrs = dict(param.attrib)
        if attrs["Name"].lower() == "equipment":
            attrs["Value"] = "FxPlant.Area.D02"
        ET.SubElement(replacements, "Parameter", attrs)
    tree.write(source, encoding="utf-8")
    path.write_bytes(path.read_bytes().replace(b'Thumbnail="fx_motor"', b'Thumbnail="fx_motor2"'))
    assert doc.update_composite(idx)
    updated = doc.composite_info(idx)
    assert updated.hash != info.hash
    assert updated.values()["equipment"] == "FxPlant.Area.D02"
    assert doc.composite_status(idx) == "current"
    assert not doc.update_composite(idx)
    # Another page may still refer to the original cache entry.
    old = composites.load_instance(source)
    assert old.update_values(info.template)["Equipment"] == "FxPlant.Area.D02"


def test_template_change_makes_instances_stale_and_update_fixes_them(proj, monkeypatch, tpl_dir):
    """A changed template (its digest) makes the instances composed from it
    stale; Update Composite Genie re-composes one with its options under the
    SAME name, and Update Pages does it for every page of the project."""
    from ctgedit.format import composer as CP
    from ctgedit.io import updatepages

    tdir = proj / "Composite Genies"
    tdir.mkdir()
    src = os.path.join(tpl_dir, DRIVE_XML)
    dst = tdir / DRIVE_XML
    shutil.copy(src, dst)
    doc = _doc(proj)
    idx = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.D01"})
    h = doc.composite_link(idx)[1]
    assert doc.composite_info(idx).template.path == str(dst)
    doc.save()
    assert doc.composite_status(idx) == "current"
    dst.write_bytes(dst.read_bytes().replace(b'Thumbnail="fx_motor"', b'Thumbnail="fx_motor2"'))
    assert doc.composite_status(idx) == "stale"
    assert doc.update_composite(idx) is True
    assert doc.composite_link(idx)[1] == h
    inst = os.path.join(str(proj), "_CompositionCaches", "{%s}" % DRIVE, "Instances", h + ".xml")
    assert CP.read_metadata(inst).digest == CP.template_digest(dst.read_bytes())
    assert doc.composite_status(idx) == "current"
    assert doc.update_composite(idx) is False
    doc.save()
    # Update Pages over the project
    dst.write_bytes(dst.read_bytes().replace(b"fx_motor2", b"fx_motor3"))
    rep = updatepages.update_pages(str(proj))
    assert rep.composites >= 1 and PAGE in rep.updated, rep
    d2 = _doc(proj)
    k = next(i for i in d2.composite_roots() if d2.composite_link(i)[1] == h)
    assert d2.composite_status(k) == "current"
    # nothing stale now: the page is not even opened (its instances are
    # checked from the page file + the cache, once per instance and run)
    from ctgedit.editing import EditableDocument

    opened = []
    real_open = EditableDocument.open.__func__
    monkeypatch.setattr(
        EditableDocument, "open", classmethod(lambda cls, p: (opened.append(p), real_open(cls, p))[1])
    )
    rep2 = updatepages.update_pages(str(proj))
    assert rep2.composites == 0 and rep2.updated == [] and opened == []


def test_pack_deletes_only_unused_instances(proj):
    """Pack Libraries' composition-cache part: an instance no page links is
    deleted (element + options file); the ones the project's pages -- or a
    page open with unsaved edits -- link stay."""
    from ctgedit.format import composer_store as CS
    from ctgedit.format import element as E
    from ctgedit.format.projects import pack_composition_caches

    doc = _doc(proj)
    used = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.USED"})
    h_used = doc.composite_link(used)[1]
    doc.save()
    spare = doc.compose_composite(DRIVE, {"Equipment": "FxPlant.Area.SPARE"})
    unsaved = doc.paste_composite_genie(DRIVE, (300, 700), {"Equipment": "FxPlant.Area.OPEN"})
    h_open = doc.composite_link(unsaved)[1]
    lib = os.path.join(str(proj), "_CompositionCaches", "{%s}" % DRIVE, "cache.ctm")
    dry = pack_composition_caches(str(proj), dry_run=True)
    assert {u.hash for u in dry.unused} == {spare.hash, h_open}
    assert set(E.LibraryFile.open(lib).element_names()) >= {spare.hash, h_open, h_used}
    backup = str(proj.parent / "bak")
    res = pack_composition_caches(str(proj), [doc.serialize()], backup=backup)
    assert res.deleted == 1 and [u.hash for u in res.unused] == [spare.hash]
    names = set(E.LibraryFile.open(lib).element_names())
    assert spare.hash not in names and {h_used, h_open} <= names
    inst = os.path.join(os.path.dirname(lib), "Instances")
    assert not os.path.exists(os.path.join(inst, spare.hash + ".xml"))
    assert os.path.exists(os.path.join(inst, h_used + ".xml"))
    from ctgedit.format.projects import restore_pack_backup

    restore_pack_backup(backup)
    assert spare.hash in set(E.LibraryFile.open(lib).element_names())
    assert os.path.exists(os.path.join(inst, spare.hash + ".xml"))
    assert CS.referenced_instances(str(proj)) >= {(DRIVE, h_used)}


def test_pack_keeps_what_an_open_documents_undo_can_bring_back(proj, monkeypatch):
    """Pack keeps instances an open document's undo can bring back."""
    from ctgedit.format.projects import pack_composition_caches

    doc = _doc(proj)
    calls = []
    real = doc._use_tag_formats
    monkeypatch.setattr(doc, "_use_tag_formats", lambda: (calls.append(1), real())[1])
    idx = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.A01"})
    assert calls
    h_a = doc.composite_link(idx)[1]
    doc.save()
    assert doc.set_composite_options(idx, {"Equipment": "FxPlant.Area.B01"}) is True
    h_b = doc.composite_link(idx)[1]
    doc.save()
    on_disk = {u.hash for u in pack_composition_caches(str(proj), dry_run=True).unused}
    assert h_a in on_disk and h_b not in on_disk
    links = doc.composite_links()
    assert {(DRIVE, h_a), (DRIVE, h_b)} <= links
    kept = pack_composition_caches(str(proj), dry_run=True, extra_links=links)
    assert h_a not in {u.hash for u in kept.unused}
    doc.undo()
    assert doc.composite_link(idx)[1] == h_a


def test_update_leaves_composites_inside_a_genie_to_that_genie(proj, tpl_dir):
    """Update Composite Genie / Update Pages re-compose a composite in a plain
    group, but not one inside a placed genie: CtDraw composes that genie from
    its element, which names its own instance."""
    tdir = proj / "Composite Genies"
    tdir.mkdir()
    dst = tdir / DRIVE_XML
    shutil.copy(os.path.join(tpl_dir, DRIVE_XML), dst)
    doc = _doc(proj)
    idx = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.G01"})
    h = doc.composite_link(idx)[1]
    g = doc.group_objects([idx, doc.root_of(idx - 1)])
    assert g is not None

    def comp():
        return next(k for k in doc.composite_roots() if doc.composite_link(k)[1] == h)

    assert doc.page.objects[comp()].parent == g
    dst.write_bytes(dst.read_bytes().replace(b'Thumbnail="fx_motor"', b'Thumbnail="fx_motor2"'))
    assert doc.composite_status(comp()) == "stale"
    # (the page's other Drive composites follow the project's template too)
    assert doc.update_composites() >= 1 and doc.composite_status(comp()) == "current"
    dst.write_bytes(dst.read_bytes().replace(b"fx_motor2", b"fx_motor3"))
    doc.parsed.page.objects[g].subtype_size |= 0x200  # the group is a placed genie
    assert doc.composite_status(comp()) == "stale"
    doc.update_composites()
    assert doc.composite_status(comp()) == "stale"  # left to its genie


# -- dialogs + window ----------------------------------------------------------------------
def test_paste_dialog_lists_templates_with_editable_options(tpl_dir):
    from ctgedit.format import composites as C
    from ctgedit.ui.dialog.composite_dialogs import PasteCompositeGenieDialog

    _app()
    templates = {}
    for f in glob.glob(os.path.join(tpl_dir, "*.xml")):
        t = C.load_template(f)
        if t is not None:
            templates[t.guid] = t
    dlg = PasteCompositeGenieDialog(templates, {})
    for i in range(dlg._list.count()):
        if dlg._list.item(i).data(0x0100) == DRIVE:
            dlg._list.setCurrentRow(i)
    assert not dlg._form.is_read_only()
    eq = dlg._form.widget("Equipment")
    eq.setText("")  # invalid: MinLength 1
    assert dlg.selection() == (None, {})
    assert not dlg._buttons.button(dlg._buttons.StandardButton.Ok).isEnabled()
    eq.setText("FxPlant.Area.P01")
    guid, vals = dlg.selection()
    assert guid == DRIVE and vals["Equipment"] == "FxPlant.Area.P01"
    assert dlg._buttons.button(dlg._buttons.StandardButton.Ok).isEnabled()


@pytest.fixture()
def win(proj):
    _app()
    from PySide6 import QtWidgets
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(str(proj / (PAGE + ".ctg")))
    w.show()
    QtWidgets.QApplication.processEvents()
    yield w
    for d in list(w._dialogs.values()):
        d.reject()
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    for e in w._docs:
        e["doc"].dirty = False
    w.close()


def test_window_presentation_options_ok_composes(win):
    """Edit Composite Genie opens the options EDITABLE (the template is in
    the include chain); OK composes and relinks the genie, one undo step."""
    from ctgedit.ui.dialog.composite_dialogs import PresentationOptionsDialog

    comp = _drive_without_status(win.doc)
    old = win.doc.composite_link(comp)[1]
    win._edit_genie(comp, win.doc.page.objects[comp])
    dlg = next(d for d in win._dialogs.values() if isinstance(d, PresentationOptionsDialog))
    form = dlg.panel.form
    assert not form.is_read_only()
    # a template file with problems cannot compose: the options open read-only
    import types

    bad = types.SimpleNamespace(
        template_source="project", template=types.SimpleNamespace(problems=["x"], path=DRIVE_XML)
    )
    assert "has problems" in win._composite_read_only_reason(comp, bad)
    w = form.widget("DisplayStatus")
    w.setCurrentIndex(w.findText("Inside Right"))
    dlg.accept()
    new = win.doc.composite_link(comp)[1]
    assert new != old and win.doc.can_undo()
    win.doc.undo()
    assert win.doc.composite_link(comp)[1] == old


def test_window_update_composite_refuses_a_locked_one(win, monkeypatch):
    """Context menu > Update Composite Genie leaves a locked composite alone
    and says why (the reason the Presentation Options give)."""
    from PySide6 import QtWidgets

    comp = _drive_without_status(win.doc)
    told = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(
        win.doc, "update_composite", lambda *a, **k: pytest.fail("a locked composite was updated")
    )
    win._locked.add(comp)
    win._update_composite(comp)
    assert told and "locked" in told[0]


def test_window_paste_composite_with_options(win):
    from ctgedit.format import geometry as G

    win._arm_composite_placement(DRIVE, "FxDriveC", {"Equipment": "FxPlant.Area.W01"})
    n0 = len(win.doc.parsed.page.objects)
    win._on_draw_committed("symbol", {"rect": (300, 400, 364, 464), "click": True})
    idx = win._sel[0]
    assert idx == n0
    gh = G.parse_geom_header(win.doc.parsed.page.objects[idx].body_raw)
    assert (gh.rect1[0], gh.rect1[1]) == (300, 400)
    assert win.doc.composite_info(idx).values()["equipment"] == "FxPlant.Area.W01"


def test_window_pack_asks_then_deletes_only_unused_instances(win, monkeypatch):
    """Tools > Pack Libraries lists the unused composite instances and asks:
    No packs the libraries and keeps them; Yes deletes exactly those (the
    instance the open page placed -- saved or not -- stays)."""
    import ctgedit.backend.graphics_builder as gb
    from ctgedit.format import element as E

    monkeypatch.setattr(gb, "_ctdraw_pids", lambda: [])
    # (the pack's result window: not shown here)
    monkeypatch.setattr(type(win), "_show_run_results", lambda self, title, lines, **k: False)
    doc = win.doc
    idx = doc.paste_composite_genie(DRIVE, (100, 700), {"Equipment": "FxPlant.Area.K01"})
    h_keep = doc.composite_link(idx)[1]
    spare = doc.compose_composite(DRIVE, {"Equipment": "FxPlant.Area.S01"})
    lib = os.path.join(spare.cache_dir, "cache.ctm")
    asked = []
    from ctgedit.ui import messages

    answer = ["Keep Them"]
    monkeypatch.setattr(messages, "choose", lambda *a, **k: (asked.append(a[2]), answer[0])[1])
    win._pack_libraries(False, ask=False)
    assert len(asked) == 1 and "1 composite genie instance" in asked[0]
    assert spare.hash in E.LibraryFile.open(lib).element_names()
    answer[0] = "Delete Unused"
    win._pack_libraries(False, ask=False)
    names = set(E.LibraryFile.open(lib).element_names())
    assert spare.hash not in names and h_keep in names
    assert not os.path.exists(os.path.join(spare.cache_dir, "Instances", spare.hash + ".xml"))
