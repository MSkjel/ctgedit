"""The composer against Plant SCADA's own composition caches."""

import glob
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

SA_R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\ExampleSA"
STORE = os.path.join(SA_R2, "_CompositionCaches")
MIXING_R2 = os.path.join(SA_R2, "Mixing_L3.ctg")
TPL_DIR = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User\SA_Library\Composite Genies"
DRIVE = "e91e1230-c421-4dec-b7cf-f6bc99b978df"
VALVE = "051512b8-937d-4392-98e8-7a1fda30ecac"
DRIVE_850 = "dd23dc2f0e57bd408d6411d9257bf00"
VALVE_850 = "fb09cc3d43be266b6521f783d8c54e8"

have_r2 = pytest.mark.skipif(
    not (os.path.isdir(STORE) and os.path.isdir(TPL_DIR)),
    reason="R2 ExampleSA composition caches not installed",
)


def _app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _snapshot():
    out = {}
    for p in glob.glob(os.path.join(STORE, "**", "*"), recursive=True):
        if os.path.isfile(p):
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture()
def corpus_untouched():
    """Fails the test if anything under the corpus caches changed."""
    before = _snapshot()
    yield
    assert _snapshot() == before, "the corpus composition caches were written"


@pytest.fixture()
def proj(tmp_path, monkeypatch, corpus_untouched):
    """A scratch project holding a copy of R2 Mixing_L3 (its libraries and
    templates found through CTG_LIB_PATH); NO composition cache of its own."""
    if not os.path.exists(MIXING_R2):
        pytest.skip("R2 ExampleSA not installed")
    monkeypatch.setenv("CTG_LIB_PATH", SA_R2)
    d = tmp_path / "Proj"
    d.mkdir()
    shutil.copy(MIXING_R2, d / "Mixing_L3.ctg")
    return d


def _instances(guid):
    d = os.path.join(STORE, "{%s}" % guid)
    return d, sorted(f[:-4] for f in os.listdir(os.path.join(d, "Instances")))


# -- the pure parts: names, digests, the template copy -------------------------------------
@have_r2
def test_instance_hashes_match_every_corpus_instance():
    """The instance name = md5(composition id + NAME[value] ...) of the
    adapted options, over the first composition whose prerequisite holds:
    every corpus instance file is named by it."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C

    n = 0
    for d in sorted(glob.glob(os.path.join(STORE, "{*}"))):
        guid = os.path.basename(d).strip("{}")
        model = CP.load_model(os.path.join(d, guid.replace("-", "") + ".xml"))
        for f in glob.glob(os.path.join(d, "Instances", "*.xml")):
            inst = C.load_instance(f)
            vals = CP.adapted_values(model, inst.values())
            comp = model.composition_for({k.lower(): v for k, v in vals.items()})
            assert CP.instance_hash(comp.id, vals) == os.path.basename(f)[:-4], f
            n += 1
    assert n >= 150


@have_r2
def test_template_digest_and_copy_match_the_caches():
    """metadata.xml's digest (either era: the 8.50 Digest attribute or the
    older signature's DigestValue) = base64(SHA1(C14N of the template as
    written)), and the cache's copy of the template = XDocument.Save of it
    (byte for byte)."""
    from ctgedit.format import composer as CP

    checked = 0
    for d in sorted(glob.glob(os.path.join(STORE, "{*}"))):
        guid = os.path.basename(d).strip("{}")
        copy = os.path.join(d, CP.copied_template_name(guid))
        md = CP.read_metadata(os.path.join(d, "metadata.xml"))
        with open(copy, "rb") as fh:
            data = fh.read()
        assert md.digest == CP.template_digest(data), d
        for src in glob.glob(os.path.join(TPL_DIR, "*.xml")):
            with open(src, "rb") as fh:
                raw = fh.read()
            if CP.template_digest(raw) == md.digest:
                assert CP.template_copy(raw) == data, src
                checked += 1
    assert checked >= 8


def _compose_ctx():
    from ctgedit.format import composer_build as CB

    return CB.ComposeContext(os.path.join(SA_R2, "x.ctg"))


@have_r2
def test_instance_files_of_the_850_instances_are_byte_exact():
    """Instance files of the shipped instances are byte-exact."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composites as C

    ctx = _compose_ctx()
    for guid, h in ((VALVE, VALVE_850), (DRIVE, DRIVE_850)):
        d = os.path.join(STORE, "{%s}" % guid)
        tp = os.path.join(d, CP.copied_template_name(guid))
        with open(tp, "rb") as fh:
            data = fh.read()
        model = CP.parse_model(data, tp)
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
            assert out == fh.read(), guid
    assert ctx.flag_sizes() == [(20, 20), (33, 33), (33, 29), (33, 29), (33, 33)]


@have_r2
def test_layout_places_the_corpus_contents():
    """The WPF layout + GenieLayout paste: the element origin (hot spot) and
    extent of every instance of three templates as the corpus stores them."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composer_build as CB
    from ctgedit.format import composites as C
    from ctgedit.format import element as E

    ctx = _compose_ctx()
    tot = ok = 0
    for guid in (VALVE, DRIVE, "1e49c603-fcbc-4913-abd1-97920283c6bd"):
        d, hashes = _instances(guid)
        model = CP.load_model(os.path.join(d, CP.copied_template_name(guid)))
        for h in hashes:
            try:
                pe = E.open_element(os.path.join(d, "cache.ctm"), h)
            except KeyError:
                continue
            lay = ctx.compose(
                model, C.load_instance(os.path.join(d, "Instances", h + ".xml")).values(), final=False
            )
            recs, hot = CB.build_records(lay, ctx)
            content = E.new_element_content(recs, origin=hot, ext="ctm", resolver=ctx.resolver)
            import struct

            tot += 1
            if struct.unpack_from("<2i", content, 0x0C) == tuple(pe.origin):
                ok += 1
    assert tot >= 20 and ok >= tot - 2, (ok, tot)


# -- the cache writer ------------------------------------------------------------------------
@have_r2
def test_compose_writes_ctdraws_cache_files(proj):
    """compose writes CtDraw's cache files."""
    from ctgedit.format import composer as CP
    from ctgedit.format import composer_store as CS
    from ctgedit.format import composites as C
    from ctgedit.format import element as E
    from ctgedit.format import genie_place as GP
    from ctgedit.format import writer

    page = str(proj / "Mixing_L3.ctg")
    for guid, h, name, fuzzy in ((VALVE, VALVE_850, "Valve", 0), (DRIVE, DRIVE_850, "Drive", 1)):
        d = os.path.join(STORE, "{%s}" % guid)
        inst = C.load_instance(os.path.join(d, "Instances", h + ".xml"))
        res = CS.compose(page, os.path.join(TPL_DIR, name + ".xml"), inst.values(), final=False)
        assert res.hash == h and res.new_instance and res.new_template_copy
        assert res.cache_dir == os.path.join(str(proj), "_CompositionCaches", "{%s}" % guid)
        for fn in (os.path.join("Instances", h + ".xml"), CP.copied_template_name(guid)):
            with open(os.path.join(res.cache_dir, fn), "rb") as a, open(os.path.join(d, fn), "rb") as b:
                assert a.read() == b.read(), fn
        md = CP.read_metadata(os.path.join(res.cache_dir, "metadata.xml"))
        old = CP.read_metadata(os.path.join(d, "metadata.xml"))
        assert (md.digest, md.versions) == (old.digest, old.versions)
        mine, theirs = E.open_element(res.library_path, h), E.open_element(os.path.join(d, "cache.ctm"), h)
        ma, ta = mine.page.objects[: mine.root_end], theirs.page.objects[: theirs.root_end]
        assert len(ma) == len(ta)
        diff = [
            k for k, (x, y) in enumerate(zip(ma, ta)) if writer.object_payload(x) != writer.object_payload(y)
        ]
        assert len(diff) <= fuzzy, diff
        for k in diff:  # only a bag size word differs
            ra, rb = GP.decode(ma[k]), GP.decode(ta[k])
            assert [[b.pairs for b in e] for e in ra.entries] == [[b.pairs for b in e] for e in rb.entries]
        assert mine.trailer == theirs.trailer
        assert mine.page.link_table_raw == theirs.page.link_table_raw
        # the cache library's container stamp is the element's save stamp (the identity's
        # first u32), as every library save gives it
        import struct

        lib = E.LibraryFile.open(res.library_path)
        blk = lib.block_data(lib.find(h))
        assert lib.ts_modified == struct.unpack_from("<I", blk, 0x20)[0]
        assert (
            CS.compose(page, os.path.join(TPL_DIR, name + ".xml"), inst.values(), final=False).written == []
        )
    assert not os.path.exists(os.path.join(str(proj), "_CompositionCaches", CS.LOCK_FILE))
