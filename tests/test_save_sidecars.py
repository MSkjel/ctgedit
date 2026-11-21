"""A save keeps the .rdb and never leaves a stale .ctF."""

import os
import shutil

import pytest

import fxinstall

#: the oracle units tried for a pair the native writer reproduces (simple pages first)
PAIR_UNITS = (
    "FxMain/FxThumb",
    "FxMain/FxTplBlank",
    "FxMain/FxShapes",
    "FxMain/FxSymbols",
    "FxMain/FxTextKinds",
)


def _page_copy(fx_page, tmp_path, name="FxShapes"):
    """A copy of FxMain's page ``name`` alone in tmp_path (no project folder)."""
    ctg = str(tmp_path / (name + ".ctg"))
    shutil.copyfile(fx_page(name), ctg)
    return ctg


def test_save_preserves_compiled_sidecars(tmp_path, fx_page):
    from ctgedit.editing import EditableDocument

    ctg = _page_copy(fx_page, tmp_path)
    # the compiled forms the runtime / Builder read (real one is uppercase)
    ctf = tmp_path / "FxShapes.ctF"
    rdb = tmp_path / "FxShapes.RDB"
    ctf.write_bytes(b"compiled-ctf")
    rdb.write_bytes(b"compiled-rdb")
    doc = EditableDocument.open(ctg)
    doc.save()
    assert os.path.exists(ctg)
    # both sidecars must survive -- the runtime fast-reload needs them present
    assert ctf.exists(), ".ctf must NOT be deleted on save"
    assert rdb.exists(), ".rdb compiled page must NOT be deleted on save"
    assert rdb.read_bytes() == b"compiled-rdb", ".rdb must be left untouched"
    # the freshly-written .ctg is the newest file (drives UseCTGIfNewer)
    assert os.path.getmtime(ctg) >= os.path.getmtime(rdb)


def test_save_as_preserves_target_sidecars(tmp_path, fx_page):
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(fx_page("FxShapes"))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = out_dir / "Copy.ctg"
    (out_dir / "Copy.rdb").write_bytes(b"compiled-rdb")
    (out_dir / "Copy.ctf").write_bytes(b"compiled-ctf")
    doc.save(str(out))
    assert out.exists()
    assert (out_dir / "Copy.rdb").exists()
    assert (out_dir / "Copy.ctf").exists()


def test_no_sidecar_deletion_helper():
    """save() does not delete compiled forms."""
    from ctgedit.editing import EditableDocument

    assert not hasattr(EditableDocument, "_invalidate_compiled_sidecars")


def test_project_save_never_leaves_a_stale_fast_display(tmp_path, fx_page):
    """In a project folder the .ctF is rebuilt from the new .ctg or removed --
    never left describing the previous version; the .rdb stays untouched."""
    from ctgedit.editing import EditableDocument

    ctg = _page_copy(fx_page, tmp_path)
    (tmp_path / "pages.DBF").write_bytes(b"")  # marks a project folder
    ctf = tmp_path / "FxShapes.ctF"
    ctf.write_bytes(b"stale-ctf")
    rdb = tmp_path / "FxShapes.RDB"
    rdb.write_bytes(b"compiled-rdb")
    doc = EditableDocument.open(ctg)
    doc.save()
    assert rdb.read_bytes() == b"compiled-rdb"
    if ctf.exists():
        from ctgedit.format import ctf as CTF

        assert ctf.read_bytes() == CTF.build_ctf(open(ctg, "rb").read(), ctg)


def _oracle_pair(fx_install):
    """A page CtDraw saved, and its .ctF."""
    import ctfcompare
    from ctgedit.format import ctf as CTF
    from ctgedit.format.symbols import SymbolResolver

    skipped = []
    for unit in PAIR_UNITS:
        try:
            a = fxinstall.oracle_bytes("pages", unit, "page.ctg")
            b = fxinstall.oracle_bytes("pages", unit, "page.ctF")
        except pytest.skip.Exception as e:
            skipped.append(str(e))
            continue
        project, name = unit.split("/")
        g = os.path.join(fx_install.project(project), name + ".ctg")
        f = g[:-1] + "F"
        for p in (g[:-1] + "F", g[:-1] + "f"):
            if os.path.exists(p):
                os.remove(p)
        with open(g, "wb") as fh:
            fh.write(a)
        ours = CTF.build_ctf(a, g, resolver=SymbolResolver.for_page(g))
        with open(f, "wb") as fh:
            fh.write(ctfcompare.comparable(ours, b)[1])
        if ctfcompare.page_identity(a)[4:] == ctfcompare.page_identity(b)[4:] and ctfcompare.same_ctf(
            ours, b, stamps=True
        ):
            return g, f
    if len(skipped) == len(PAIR_UNITS):
        pytest.skip("; ".join(skipped))
    return None


@pytest.mark.golden
def test_trusted_page_gets_a_rebuilt_fast_display(fx_install):
    """The writer reproduces CtDraw's .ctF for the page's previous version ->
    the save writes a fresh .ctF of the NEW version."""
    pair = _oracle_pair(fx_install)
    if pair is None:
        pytest.skip("no byte-exact oracle pair among %s" % ", ".join(PAIR_UNITS))
    from ctgedit.editing import EditableDocument
    from ctgedit.format import ctf as CTF
    from ctgedit.format.symbols import SymbolResolver

    g, f = pair
    doc = EditableDocument.open(g)
    doc.create_object("rectangle", (10, 10, 60, 40), colour=0x2080FF)
    doc.save()
    assert "fast display rebuilt" in doc.save_report
    got = open(f, "rb").read()
    res = SymbolResolver.for_page(g)
    assert got == CTF.build_ctf(open(g, "rb").read(), g, resolver=res)


def test_unverifiable_page_drops_the_stale_fast_display(fx_page):
    """A page the writer cannot reproduce (here: its .ctF does not belong to
    its .ctg) loses the stale .ctF -- the runtime then draws the .ctg. (FxMain's
    FxShapes in the install's copy: a project folder with its pages.DBF.)"""
    from ctgedit.editing import EditableDocument

    g = fx_page("FxShapes")
    ctf = g[:-1] + "F"
    with open(ctf, "wb") as fh:
        fh.write(b"not-this-page")
    doc = EditableDocument.open(g)
    doc.save()
    assert not os.path.exists(ctf)
    assert "stale fast display removed" in doc.save_report


@pytest.mark.parametrize("name", ["FxShapes", "FxSymbols", "FxGenies"])
def test_build_ctf_with_the_documents_parse_gives_the_same_bytes(fx_page, name):
    """The save passes the document's own parse to build_ctf (page=): it gives exactly what
    the plain path gives (on fixture pages of shapes, symbols and genies)."""
    from ctgedit.format import ctf as CTF
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import SymbolResolver

    path = fx_page(name)
    raw = open(path, "rb").read()
    res = SymbolResolver.for_page(path)
    assert CTF.build_ctf(raw, path, resolver=res, page=parse_ctg(path).page) == CTF.build_ctf(
        raw, path, resolver=res
    )


@pytest.mark.golden
def test_fast_display_speedups_do_not_change_the_bytes(fx_install, monkeypatch):
    """The save passes the document's own parse to build_ctf (page=) and takes
    the previous-version verdict from a background check started at open:
    both must give exactly what the plain synchronous path gives."""
    pair = _oracle_pair(fx_install)
    if pair is None:
        pytest.skip("no byte-exact oracle pair among %s" % ", ".join(PAIR_UNITS))
    from ctgedit.editing import EditableDocument
    from ctgedit.format import ctf as CTF
    from ctgedit.format.page import parse_ctg
    from ctgedit.format.symbols import SymbolResolver

    g, _f = pair
    raw = open(g, "rb").read()
    res = SymbolResolver.for_page(g)
    assert CTF.build_ctf(raw, g, resolver=res, page=parse_ctg(g).page) == CTF.build_ctf(raw, g, resolver=res)
    monkeypatch.delenv("CTG_NO_CTF_PRECHECK", raising=False)
    doc = EditableDocument.open(g)
    assert doc._ctf_pre is not None
    doc._ctf_pre["thread"].join()
    assert doc._ctf_pre["ok"] is True
    doc.save()
    assert "fast display rebuilt" in doc.save_report
