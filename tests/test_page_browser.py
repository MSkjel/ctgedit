"""The page browser."""

import os
import shutil
import struct

import pytest
from qtkit import app as _app

#: the fake install's paths of the current test (``_fx`` sets them)
PROJ_DIR = USER = START = THUMB = GENIES = ""
#: the start page's name and the project's
START_NAME = "FxTplLinked"
PROJECT = "FxMain"
SAMPLE_PAGES = ("FxTplLinked.ctg", "!FxSystem.ctg", "FxGenies.ctg", "FxThumb.ctg")


@pytest.fixture(autouse=True)
def _fx(fx_install):
    global PROJ_DIR, USER, START, THUMB, GENIES
    PROJ_DIR = fx_install.project(PROJECT)
    USER = fx_install.user
    START = fx_install.page(START_NAME)
    THUMB = fx_install.page("FxThumb")
    GENIES = fx_install.page("FxGenies")
    yield fx_install


# --------------------------------------------------------------------------- pagesummary
def _full_parse_facts(path):
    from ctgedit.format.page import _extract_bmp, parse_ctg
    from ctgedit.format.symbols import reference_from_object

    pb = parse_ctg(path).page
    ref = None
    for po in pb.objects:
        if po.type_code != 0x08:
            continue
        r = reference_from_object(po)
        if r is not None and r.ext == "ctt":
            ref = "%s.%s" % (r.library, r.element)
            break
    return (pb.page_width, pb.page_height, len(pb.objects), _extract_bmp(pb.thumbnail) or b"", ref)


def test_page_summary_matches_the_full_parser():
    from ctgedit.format.pagesummary import read_page_summary

    for fn in SAMPLE_PAGES:
        path = os.path.join(PROJ_DIR, fn)
        if not os.path.isfile(path):
            continue
        s = read_page_summary(path)
        w, h, n, bmp, ref = _full_parse_facts(path)
        assert s.ok, (fn, s.error)
        assert (s.width, s.height, s.object_count) == (w, h, n), fn
        assert s.thumb_bmp == bmp, fn
        assert (s.template.reference if s.template else None) == ref, fn
    mixer = read_page_summary(START)
    assert mixer.template is not None and mixer.template.library.lower() == "fx_style"
    assert mixer.template.name == "normal" and mixer.template.resolution


def test_thumbnail_decodes_to_rgba_and_png():
    from ctgedit.format.pagesummary import bmp_dimensions, read_page_summary

    s = read_page_summary(THUMB)
    assert s.has_thumbnail and s.thumb_bmp[:2] == b"BM"
    w, h = s.thumbnail_size
    assert (w, h) == bmp_dimensions(s.thumb_bmp) and max(w, h) <= 256 and w and h
    dec = s.thumbnail_rgba()
    assert dec is not None and dec[:2] == (w, h) and len(dec[2]) == w * h * 4
    assert set(dec[2][3::4]) == {255}  # opaque
    png = s.thumbnail_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (w, h)
    # Qt reads both encodings to the same image
    _app()
    from PySide6 import QtGui

    a = QtGui.QImage.fromData(s.thumb_bmp, "BMP")
    b = QtGui.QImage.fromData(png, "PNG")
    assert not a.isNull() and a.size() == b.size()
    assert a.pixelColor(w // 2, h // 2).rgb() == b.pixelColor(w // 2, h // 2).rgb()


def test_template_reference_parsing_and_full_scan():
    from ctgedit.format.pagesummary import (
        find_template_ref,
        parse_template_ref,
        read_page_summary,
        split_element_name,
    )

    t = parse_template_ref("fx_sa.normal@-HD1080.<ctt")
    assert (t.library, t.element, t.name, t.resolution, t.title_bar) == (
        "fx_sa",
        "normal@-HD1080",
        "normal",
        "HD1080",
        False,
    )
    assert t.reference == "fx_sa.normal@-HD1080"
    assert split_element_name("blank") == ("blank", "", True)
    assert parse_template_ref("") is None and parse_template_ref("nodot.<ctt") is None
    # the bounded scan can miss; the full scan always agrees with the default one
    full = find_template_ref(START)
    assert full is not None and full.reference == read_page_summary(START).template.reference
    s = read_page_summary(START, template_scan=0)
    assert s.template is None and s.ok
    # (no fixture page has both a template and a stored thumbnail: the thumbnail is still
    # read without the scan on FxThumb)
    s = read_page_summary(THUMB, template_scan=0)
    assert s.template is None and s.ok and s.thumb_bmp


def test_library_elements_listed_and_deleted_skipped(tmp_path):
    from ctgedit.format.pagesummary import (
        list_element_names,
        read_directory,
        read_element_summaries,
        read_element_summary,
    )
    from ctgedit.format.symbols import SymbolLibrary

    lib_path = os.path.join(os.path.dirname(PROJ_DIR), "FxLib", "fxsym.ctl")
    lib = SymbolLibrary.open(lib_path)
    names = list_element_names(lib_path)
    assert names == lib.element_names() and names
    assert "deleted" not in [n.lower() for n in names]
    # this library really carries a CtDraw-deleted block (fx_gone)
    assert any(e.is_deleted and e.size for e in read_directory(lib_path).entries)
    for es in read_element_summaries(lib_path):
        pb = lib.element(es.name)
        assert es.ok and (es.width, es.height, es.object_count) == (
            pb.page_width,
            pb.page_height,
            len(pb.objects),
        )
        assert es.reference == "fxsym." + es.name
    assert read_element_summary(lib_path, "deleted") is None

    # synthetic: delete an element the way CtDraw does (rename + clear the tag's
    # low byte) -> no longer listed, no longer readable by name
    src = os.path.join(os.path.dirname(PROJ_DIR), "FxLib", "fxmore.ctl")
    dst = str(tmp_path / ("copy" + os.path.splitext(src)[1]))
    shutil.copyfile(src, dst)
    before = list_element_names(dst)
    assert len(before) >= 2
    d = read_directory(dst)
    victim = d.block(before[0])
    with open(dst, "r+b") as fh:
        off = 0x10 + victim.slot * 42
        fh.seek(off)
        tag = struct.unpack("<H", fh.read(2))[0]
        fh.seek(off)
        fh.write(struct.pack("<H", tag & 0xFF00) + b"deleted".ljust(32, b"\x00"))
    after = list_element_names(dst)
    assert after == before[1:]
    assert read_element_summary(dst, before[0]) is None
    assert read_element_summary(dst, before[1]).ok


def test_summary_reader_never_raises(tmp_path):
    from ctgedit.format.pagesummary import list_element_names, read_element_summaries, read_page_summary

    junk = tmp_path / "junk.ctg"
    junk.write_bytes(b"\x09\x00\x0c\x00" + os.urandom(64))
    assert not read_page_summary(str(junk)).ok
    assert not read_page_summary(str(tmp_path / "missing.ctg")).ok
    trunc = tmp_path / "trunc.ctg"
    with open(THUMB, "rb") as fh:  # (its thumbnail lies past 2000)
        trunc.write_bytes(fh.read(2000))
    s = read_page_summary(str(trunc))
    assert s.thumb_bmp == b""  # nothing past EOF
    assert isinstance(list_element_names(str(junk)), list)
    assert read_element_summaries(str(tmp_path / "missing.ctl")) == []


# --------------------------------------------------------------------------- catalog
@pytest.fixture()
def catalog(_fx):
    from ctgedit.io.catalog import catalog_for

    return catalog_for(USER)


def test_catalog_pages_join_pages_dbf(catalog):
    from ctgedit.io.project import Project

    ex = catalog.project(PROJECT)
    assert ex is not None and ex.is_live and ex.has_pages_dbf
    pages = catalog.pages(ex)
    files = sorted(f[:-4].lower() for f in os.listdir(PROJ_DIR) if f.lower().endswith(".ctg"))
    assert sorted(e.name.lower() for e in pages) == files
    rows = {r["NAME"].lower(): r for r in Project(PROJ_DIR).page_rows()}
    for e in pages:
        r = rows.get(e.name.lower()[:64])
        assert e.registered is (r is not None)
        if r is not None:
            assert (e.title, e.comment, e.area) == (
                r.get("TITLE", ""),
                r.get("COMMENT", ""),
                r.get("AREA", ""),
            )
    mixer = next(e for e in pages if e.name.lower() == "fxtpllinked")
    assert mixer.registered and mixer.title == rows["fxtpllinked"]["TITLE"]
    assert "fxtpllinked" in mixer.haystack and mixer.project == PROJECT
    # a row with its own title, comment and area
    anim = next(e for e in pages if e.name == "FxAnim")
    assert (anim.title, anim.comment, anim.area) == ("@(Fx Start)", "every animation key", "1")
    assert catalog.project_for_path(START) is ex
    assert catalog.entry_for_path(START).name == mixer.name
    # cached listing is reused until the folder / table changes
    assert catalog.pages(ex) is pages
    assert catalog.count(ex, "page") == len(pages)


def test_catalog_include_chain_matches_ctdraw_search_order(catalog):
    from ctgedit.format.projects import include_search_dirs

    ex = catalog.project(PROJECT)
    chain = catalog.closure(ex)
    assert chain[0] is ex
    names = [p.name.lower() for p in chain]
    assert "include" in names and "system" in names
    for inc in ex.includes:
        assert inc.lower() in names
    live = os.path.normcase(os.path.abspath(USER)) + os.sep
    expect = [
        os.path.normcase(os.path.abspath(d))
        for d in include_search_dirs(PROJ_DIR)
        if os.path.normcase(os.path.abspath(d)).startswith(live)
    ]
    assert [p.key for p in chain[1:]] == expect
    roots = [p.name.lower() for p in catalog.roots()]
    # (FxOther includes FxMain: FxOther is a root, FxMain is not)
    assert "fxother" in roots and "fxmain" not in roots
    assert "include" not in roots and "system" not in roots


def test_catalog_library_elements(catalog):
    from ctgedit.format.pagesummary import list_element_names, read_element_summary

    ex = catalog.project(PROJECT)
    chain = catalog.closure(ex)
    tpls = catalog.entries(chain, "template")
    assert tpls and all(e.kind == "template" and e.path.lower().endswith(".ctt") for e in tpls)
    # an empty library is hidden (FxLib's fxempty.ctl: its only element is deleted)
    assert all(list_element_names(e.path) for e in tpls[:50])
    syms = catalog.entries(chain, "symbol")
    assert syms and all(e.path.lower().endswith(".ctl") for e in syms)
    genies = catalog.entries(chain, "genie")
    assert genies and all(e.path.lower().endswith(".ctm") for e in genies)
    e = syms[0]
    s = read_element_summary(e.path, e.name, e.element_offset, e.element_size, e.word2)
    assert s is not None and s.ok and s.name == e.name
    t = next(x for x in tpls if "@" in x.name)
    assert t.resolution and t.template_name and t.reference == "%s.%s" % (t.library, t.name)
    # shadowing: the same library.element earlier on the chain wins
    first = {}
    for x in syms:
        k = (x.library.lower(), x.name.lower())
        if k in first and first[k] != x.project:
            assert x.shadowed_by == first[k]
        first.setdefault(k, x.project)


def test_catalog_unregistered_and_renamed_pages(tmp_path):
    from ctgedit.io.catalog import ProjectCatalog

    cat = ProjectCatalog(USER)
    proj = tmp_path / "Proj"
    proj.mkdir()
    shutil.copyfile(os.path.join(PROJ_DIR, "pages.DBF"), proj / "pages.DBF")
    shutil.copyfile(START, proj / "FxAssoc.ctg")
    # CtDraw keeps the OLD name as the TITLE when a page is renamed (FxAssoc's row:
    # TITLE 'Fx genie host')
    shutil.copyfile(START, proj / "Fx genie host.ctg")
    shutil.copyfile(START, proj / "Loose.ctg")
    info = cat.folder(str(proj))
    assert info.is_folder and info.has_pages_dbf
    pages = {e.name: e for e in cat.pages(info)}
    assert pages["FxAssoc"].registered is True
    assert pages["Loose"].registered is False and not pages["Loose"].superseded_by
    assert pages["Fx genie host"].registered is False
    assert pages["Fx genie host"].superseded_by == "FxAssoc"
    # a folder without pages.DBF: registration is not applicable
    bare = tmp_path / "Bare"
    bare.mkdir()
    shutil.copyfile(START, bare / "Page1.ctg")
    e = cat.entry_for_path(str(bare / "Page1.ctg"))
    assert e is not None and e.registered is None and not e.is_other_install
    # touch() forgets the listing: a new file shows up
    shutil.copyfile(START, bare / "Page2.ctg")
    cat.touch(str(bare))
    assert {x.name for x in cat.pages(cat.folder(str(bare)))} == {"Page1", "Page2"}


# --------------------------------------------------------------------------- naming
def test_page_name_validation(tmp_path):
    from ctgedit.ui.browser import naming

    existing = {"mixer": os.path.join(PROJ_DIR, "Mixer.ctg")}
    v = lambda n, **k: naming.validate_page_name(
        n,
        PROJ_DIR,
        existing=existing,  # noqa: E731
        registered={"ghost"},
        has_pages_dbf=True,
        **k,
    )
    for ok in (
        "Cap and Label_L3",
        "page 2",
        "FxPlant_Area_#ParseErr_353_301",
        "FxPlant_Area_Smoke&Lift",
        "NewPage",
    ):
        assert v(ok).level == naming.OK, ok
    assert v("").level == naming.ERROR
    assert v("Mix:er?").level == naming.ERROR and ":" in v("Mix:er?").message
    assert v("CON").level == naming.ERROR and v("lpt1").level == naming.ERROR
    assert v("trailing.").level == naming.ERROR and v("trailing ").level == naming.ERROR
    assert v(" lead").level == naming.ERROR
    assert v("x" * 65).level == naming.ERROR
    assert (
        naming.validate_page_name("x" * 65, str(tmp_path), existing={}, has_pages_dbf=False).level
        == naming.OK
    )
    rep = v("MIXER")
    assert rep.level == naming.WARN and rep.collision == existing["mixer"]
    assert v("Mixer", mode="new").level == naming.ERROR
    assert v("Mixer.ctg").collision  # a typed suffix is ignored
    assert v("ghost").level == naming.WARN  # registered, file missing
    assert v("a.b").level == naming.WARN and v("!Popup").level == naming.WARN
    assert naming.unique_page_name("Mixer", ["mixer"]) == "Mixer_2"
    assert naming.unique_page_name("Mixer_2", ["mixer_2", "mixer_3"]) == "Mixer_4"
    assert naming.unique_page_name("A_120_405", ["a_120_405"]) == "A_120_405_2"
    assert len(naming.unique_page_name("y" * 64, ["y" * 64])) <= 64


# --------------------------------------------------------------------------- model
def _entries():
    from ctgedit.io.catalog import Entry

    mk = lambda name, **k: Entry(
        "page",
        k.pop("project", "P"),
        name,  # noqa: E731
        "C:\\x\\%s.ctg" % name,
        **k,
    )
    return [
        mk("page10", title="Tenth", mtime_ns=5),
        mk("page2", title="Second", mtime_ns=9),
        mk("!Popup", title="A popup", mtime_ns=1),
        mk("Mixer", title="Plant Mixer", comment="milk", mtime_ns=3),
        mk("Stale", registered=False, mtime_ns=2),
        mk("Other", project="Q", title="Elsewhere", mtime_ns=4),
    ]


def test_model_filter_and_sort_in_python():
    _app()
    from PySide6 import QtCore
    from ctgedit.ui.browser.model import CatalogModel, dominant_prefix

    m = CatalogModel(None)
    counts = []
    m.countsChanged.connect(lambda *a: counts.append(a))
    m.set_entries(_entries(), "page")
    names = lambda: [e.name for e in m.entries()]  # noqa: E731
    assert names() == ["!Popup", "Mixer", "Other", "page2", "page10", "Stale"]  # natural
    assert counts[-1] == (6, 6, 0) and m.unregistered_total == 1
    m.set_query("milk")  # comment is searchable
    assert names() == ["Mixer"]
    m.set_query("plant mix")  # all tokens must match
    assert names() == ["Mixer"]
    m.set_query("proj:q")
    assert names() == ["Other"]
    m.set_query("!")
    assert names() == ["!Popup"]
    m.set_query("mxr")  # fuzzy fallback (subsequence)
    assert names() == ["Mixer"] and m.fuzzy_used
    m.set_query("")
    m.set_facets(hide_unregistered=True)
    assert "Stale" not in names() and m.hidden_unregistered == 1
    assert counts[-1] == (5, 6, 1)
    m.set_facets(popups="hide")
    assert "!Popup" not in names()
    m.set_facets(popups="any", hide_unregistered=False)
    m.set_sort("modified")  # newest first
    assert names()[0] == "page2" and names()[-1] == "!Popup"
    m.sort(0, QtCore.Qt.DescendingOrder)
    assert names()[0] == "Stale"
    assert m.row_of("C:\\x\\Mixer.ctg") == names().index("Mixer")
    assert m.find("MIXER") == names().index("Mixer")
    assert m.data(m.index(m.find("Mixer"), 1)) == "Plant Mixer"
    assert dominant_prefix(["FxPlant_Area_%d" % i for i in range(8)] + ["Other"]) == "FxPlant_Area_"
    assert dominant_prefix(["a", "b", "c", "d"]) == ""


# --------------------------------------------------------------------------- thumbnails
def test_thumbnail_service_loads_in_background(catalog):
    _app()
    from ctgedit.ui.browser.thumbs import CARD_SIZE, ThumbnailService

    svc = ThumbnailService(threads=2)
    try:
        e = catalog.entry_for_path(THUMB)
        assert svc.request(e) is None and svc.state(e) == "loading"
        assert svc.request(e) is None and len(svc._pending) == 1  # de-duplicated
        assert svc.wait_idle(10000)
        pm = svc.request(e)
        assert pm is not None and not pm.isNull()
        assert pm.width() <= CARD_SIZE.width() and pm.height() <= CARD_SIZE.height()
        assert svc.state(e) == "ready" and svc.pixmap(e, "row") is not None
        s = svc.summary(e)
        # (FxThumb: one rectangle; the summary keeps no thumbnail bytes once decoded)
        assert s is not None and s.object_count == 1 and s.thumb_bmp == b""
        from ctgedit.format.pagesummary import read_page_summary

        assert svc.request_full(e) is None
        svc.wait_idle(10000)
        full = svc.request_full(e)
        assert full is not None and (full.width(), full.height()) == read_page_summary(THUMB).thumbnail_size
        el = catalog.entries(catalog.closure(catalog.project(PROJECT)), "symbol")[0]
        svc.request(el)
        svc.wait_idle(10000)
        assert svc.state(el) in ("ready", "none")
    finally:
        svc.shutdown()
        svc.deleteLater()


# --------------------------------------------------------------------------- dialog
def _fake_run(action):
    """A PageBrowserDialog._run replacement: drive the dialog, never block."""

    def run(dlg):
        _app().processEvents()
        action(dlg)
        return dlg.result()

    return staticmethod(run)


def test_open_dialog_starts_on_the_page_project(catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=START)
    try:
        assert dlg.current_scope().anchor.name == PROJECT
        assert dlg.current_kind() in dlg.kinds
        dlg.set_kind("page")
        assert dlg.model.total == catalog.count(PROJECT, "page")
        cur = dlg.current_entry()
        assert cur is not None and cur.name == START_NAME
        assert dlg.preview.current_entry() is cur
        assert dlg.preview.fields["Template"].text().startswith("normal")
        assert dlg.select_name("FxGenies") and dlg.current_entry().name == "FxGenies"
        assert dlg.accept_current()
        r = dlg.result_value
        assert r.kind == "page" and r.action == "open"
        assert os.path.normcase(r.path) == os.path.normcase(GENIES)
    finally:
        dlg.deleteLater()


def test_get_open_path_entry_point(monkeypatch, catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    seen = {}

    def act(dlg):
        seen["tabs"] = dlg.tabs.isVisible() or dlg.tabs.count()
        seen["kinds"] = dlg.kinds
        dlg.select_name(START_NAME)
        dlg.accept_current()

    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(act))
    path = PageBrowserDialog.get_open_path(None, START, catalog=catalog)
    assert seen["kinds"] == ("page",)
    assert os.path.normcase(path) == os.path.normcase(START)
    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(lambda d: d.reject()))
    assert PageBrowserDialog.get_open_path(None, START, catalog=catalog) is None


def test_get_template_entry_point(monkeypatch, catalog):
    _app()
    from ctgedit.format.pagesummary import list_element_names
    from ctgedit.ui.browser import PageBrowserDialog

    seen = {}

    def act(dlg):
        seen["scope"] = dlg.current_scope()
        seen["kind"] = dlg.current_kind()
        dlg.search.setText("normal")
        dlg._apply_search()
        assert dlg.model.rowCount() > 0
        dlg.select_row(0)
        dlg.accept_current()

    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(act))
    res = PageBrowserDialog.get_template(None, PROJ_DIR, catalog=catalog)
    assert seen["kind"] == "template" and seen["scope"].kind == "chain"
    assert res is not None
    ctt, element = res
    assert ctt.lower().endswith(".ctt") and element in list_element_names(ctt)
    assert "normal" in element.lower()


def test_get_library_element_entry_point(monkeypatch, catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    def act(dlg):
        dlg.select_row(0)
        dlg.accept_current()

    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(act))
    lib, element = PageBrowserDialog.get_library_element(None, PROJ_DIR, "symbol", catalog=catalog)
    assert lib.lower().endswith(".ctl") and element
    lib, element = PageBrowserDialog.get_library_element(None, PROJ_DIR, "genie", catalog=catalog)
    assert lib.lower().endswith(".ctm") and element
    with pytest.raises(ValueError):
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "page", catalog=catalog)


def test_get_save_path_validates_as_you_type(monkeypatch, catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    asked = []

    def act(dlg):
        assert os.path.normcase(dlg.target_dir()) == os.path.normcase(PROJ_DIR)
        # the start page suggests a FREE name, never the page itself
        assert dlg.ed_name.text().lower() != START_NAME.lower()
        dlg.set_page_name("Mix:er?")
        assert dlg.verdict().level == "error" and not dlg.ok_button.isEnabled()
        assert not dlg.accept_current()
        dlg.set_page_name(START_NAME)
        assert dlg.verdict().collision and dlg.ok_button.text().startswith("Replace")
        dlg.confirm_replace = lambda p: asked.append(p) or False
        assert not dlg.accept_current() and asked  # declined -> stays open
        dlg.set_page_name("FxBrowserTest_NewPage")
        assert dlg.verdict().level == "ok" and dlg.ok_button.isEnabled()
        assert dlg.accept_current()

    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(act))
    path = PageBrowserDialog.get_save_path(None, START, catalog=catalog)
    assert os.path.normcase(path) == os.path.normcase(os.path.join(PROJ_DIR, "FxBrowserTest_NewPage.ctg"))
    assert not os.path.exists(path)  # the dialog never writes

    def replace(dlg):
        dlg.set_page_name(START_NAME.lower())
        dlg.confirm_replace = lambda p: True
        dlg.accept_current()

    monkeypatch.setattr(PageBrowserDialog, "_run", _fake_run(replace))
    path = PageBrowserDialog.get_save_path(None, START, catalog=catalog)
    assert os.path.normcase(path) == os.path.normcase(START)  # the existing file's case


def test_keyboard_search_and_accept(catalog):
    _app()
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import set_option

    # every page listed ('!' pages are hidden by default, as in CtDraw)
    set_option("list_system_pages", True)
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=START, kinds=("page",))
    try:
        dlg.show()
        QTest.keyClicks(dlg.grid, "genie")  # type in the grid -> search
        assert dlg.search.text() == "genie"
        dlg.search.flush()
        assert 0 < dlg.model.rowCount() < dlg.model.total
        assert all("genie" in e.haystack for e in dlg.model.entries())
        QTest.keyClick(dlg.search, QtCore.Qt.Key_Escape)  # Esc clears first
        assert dlg.search.text() == "" and dlg.model.rowCount() == dlg.model.total
        assert dlg.isVisible()
        dlg.select_name(START_NAME)
        QTest.keyClick(dlg.grid, QtCore.Qt.Key_Return)  # Enter accepts
        assert dlg.result() == QtWidgets.QDialog.Accepted
        assert dlg.result_value.path.lower().endswith(START_NAME.lower() + ".ctg")
    finally:
        dlg.deleteLater()


def test_settings_remembered_through_app_settings(tmp_path, catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    assert os.path.normcase(str(tmp_path)) in os.path.normcase(app_settings().fileName())
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=START)
    dlg.set_view("list")
    dlg.set_kind("symbol")
    dlg.set_hide_unregistered(True)
    dlg.reject()
    dlg.deleteLater()
    st = app_settings()
    # per purpose: pagebrowser/open/..., shared: hide_unregistered / popups
    assert st.value("pagebrowser/open/view") == "list"
    assert st.value("pagebrowser/open/kind") == "symbol"
    assert st.value("pagebrowser/hide_unregistered", False, type=bool) is True
    dlg2 = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog)
    try:
        assert dlg2.view_mode() == "list" and dlg2.current_kind() == "symbol"
        assert dlg2.current_scope() is not None  # last scope restored
        assert dlg2.model.hide_unregistered
    finally:
        dlg2.deleteLater()


# --------------------------------------------------------------------------- settings / modes
def _dlg(mode, catalog, **kw):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    return PageBrowserDialog(mode, catalog=catalog, **kw)


def test_system_pages_filter_starts_on_all_pages_and_is_remembered(catalog):
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings, set_option

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
    try:
        assert dlg.cb_popups.currentData() == "any"  # a fresh settings file
        assert [dlg.cb_popups.itemText(k) for k in range(dlg.cb_popups.count())] == [
            "All pages",
            "Hide system pages (!)",
            "Only system pages (!)",
        ]
        assert any(e.is_popup for e in dlg.model.entries())
        dlg.cb_popups.setCurrentIndex(dlg.cb_popups.findData("hide"))
        assert not any(e.is_popup for e in dlg.model.entries())
    finally:
        dlg.reject()
        dlg.deleteLater()
    assert app_settings().value("pagebrowser/popups") == "hide"
    for opt in (True, False):  # Settings > List system pages: not read here
        set_option("list_system_pages", opt)
        dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
        try:
            assert dlg.cb_popups.currentData() == "hide"
        finally:
            dlg.deleteLater()


def test_unregistered_pages_shown_by_default_and_remembered(catalog):
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
    try:
        assert not dlg.model.hide_unregistered
        if dlg.model.unregistered_total:
            assert dlg.chip_unreg.text().endswith("not registered · Hide")
            assert dlg.chip_unreg.toolTip() == "Pages with no pages.DBF row"
            dlg.chip_unreg.click()
            assert dlg.chip_unreg.text().endswith("not registered hidden · Show")
        else:
            dlg.set_hide_unregistered(True)
    finally:
        dlg.reject()
        dlg.deleteLater()
    assert app_settings().value("pagebrowser/hide_unregistered", False, type=bool) is True


def test_pickers_list_only_what_the_page_can_place(catalog):
    """The pickers show the page's project and its include chain (no install
    rows, no other projects), and a library element hidden by an earlier
    copy on the chain is not listed at all."""
    from ctgedit.io.catalog import Entry
    from ctgedit.ui.browser import PageBrowserDialog

    ex = catalog.project(PROJECT)
    chain = catalog.closure(ex)
    names = {p.name for p in chain}
    for view in ("tree", "flat"):
        from ctgedit.ui.browser.dialog import set_project_view

        set_project_view(view)
        dlg = _dlg(PageBrowserDialog.PICK_ELEMENT, catalog, project_dir=PROJ_DIR, kinds=("symbol",))
        try:
            assert dlg.tree.chain_anchor() is ex
            shown = set(dlg.tree.project_names())
            assert shown and shown <= names, shown - names
            assert dlg.tree.install_scope_id() not in dlg.tree.all_scopes()
            s = dlg.current_scope()
            assert s.kind == "chain" and dlg.chk_includes.isChecked()
            ents = dlg.model.all_entries()
            assert ents and not any(e.shadowed_by for e in ents)
            keys = {(e.library.lower(), e.name.lower()) for e in ents}
            assert len(keys) == len(ents)  # one copy each
            # an entry from outside the chain can never be selected
            outsider = Entry(
                "symbol", "FxNowhere", "x", r"C:\nowhere\lib.ctl", library="lib", project_dir=r"C:\nowhere"
            )
            assert not dlg._on_chain(outsider)
            assert dlg._on_chain(ents[0])
        finally:
            dlg.deleteLater()


def test_empty_picker_says_so(tmp_path, catalog):
    from ctgedit.ui.browser import PageBrowserDialog

    bare = tmp_path / "Bare"
    bare.mkdir()
    dlg = _dlg(PageBrowserDialog.PICK_ELEMENT, catalog, project_dir=str(bare), kinds=("genie",))
    try:
        assert dlg.model.total == 0
        assert dlg.status.text() == "No genies on this page's include path"
        assert not dlg.ok_button.isEnabled()
    finally:
        dlg.deleteLater()


def test_default_window_titles(catalog):
    from ctgedit.ui.browser import PageBrowserDialog

    made = []
    real = PageBrowserDialog._finish

    def finish(dlg):
        made.append(dlg.windowTitle())
        dlg.deleteLater()
        return None

    try:
        PageBrowserDialog._finish = staticmethod(finish)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "symbol", catalog=catalog)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "genie", catalog=catalog)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "genie", catalog=catalog, super_genies=True)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "supergenie", catalog=catalog)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "template", catalog=catalog)
        PageBrowserDialog.get_template(None, PROJ_DIR, catalog=catalog)
        PageBrowserDialog.get_library_element(None, PROJ_DIR, "symbol", catalog=catalog, title="Paste Symbol")
        PageBrowserDialog.get_open_path(None, START, catalog=catalog)
        PageBrowserDialog.get_save_path(None, START, catalog=catalog)
    finally:
        PageBrowserDialog._finish = real
    assert made == [
        "Select Symbol",
        "Select Genie",
        "Select Super Genie",
        "Select Super Genie",
        "Select Template",
        "Select Template",
        "Paste Symbol",
        "Open Page",
        "Save Page As",
    ]


def test_view_settings_are_kept_per_purpose(catalog):
    """A list view (and card size) chosen in Open does not follow into Paste Symbol."""
    from PySide6 import QtCore
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START)
    dlg.set_view("list")
    dlg.set_card_width(240)
    dlg.reject()
    dlg.deleteLater()
    pick = _dlg(PageBrowserDialog.PICK_ELEMENT, catalog, project_dir=PROJ_DIR, kinds=("symbol",))
    try:
        assert pick.view_mode() == "grid" and pick.card_width() == 168
        # Ctrl+wheel over the grid sizes the cards
        ev = _wheel(120, QtCore.Qt.ControlModifier)
        assert pick.eventFilter(pick.grid.viewport(), ev)
        assert pick.card_width() == 176 and pick.slider.value() == 176
        assert pick.grid.gridSize().width() == 176 + 16
    finally:
        pick.reject()
        pick.deleteLater()
    st = app_settings()
    assert st.value("pagebrowser/open/view") == "list"
    assert int(st.value("pagebrowser/open/card_size")) == 240
    assert st.value("pagebrowser/pick/view") == "grid"
    assert int(st.value("pagebrowser/pick/card_size")) == 176
    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START)
    try:
        assert dlg.view_mode() == "list" and dlg.card_width() == 240
    finally:
        dlg.deleteLater()


def test_fit_columns_in_the_list_asks_only_for_the_thumbnails_on_screen(catalog, monkeypatch):
    """Fit Columns to Contents (and a double-click on a divider) measures the Name column
    from the model's text (tables.text_width, the tables package): it asks for the first
    row's thumbnail at most, never one of a row further down."""
    from PySide6 import QtWidgets
    from ctgedit.ui import tables
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START)
    try:
        dlg.set_view("list")
        dlg.resize(900, 420)
        dlg.show()
        for _ in range(6):
            QtWidgets.QApplication.processEvents()
        view = dlg.table
        assert view.header().resizeContentsPrecision() == 0
        rows = dlg.model.rowCount()
        asked = set()
        real = dlg.thumbs.request

        def request(e, size, *a, **k):
            if size == "row":
                asked.add(id(e))
            return real(e, size, *a, **k)

        monkeypatch.setattr(dlg.thumbs, "request", request)
        tables.state_of(view).fit_action.trigger()
        QtWidgets.QApplication.processEvents()
        shown = view.viewport().height() // max(1, view.sizeHintForRow(0)) + 2
        if rows > shown + 5:
            assert len(asked) <= shown, (len(asked), shown, rows)
    finally:
        dlg.reject()
        dlg.deleteLater()


def _wheel(dy, mods):
    from PySide6 import QtCore, QtGui

    return QtGui.QWheelEvent(
        QtCore.QPointF(10, 10),
        QtCore.QPointF(10, 10),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, dy),
        QtCore.Qt.NoButton,
        mods,
        QtCore.Qt.NoScrollPhase,
        False,
    )


def test_save_as_message_row_and_help_button(catalog, monkeypatch):
    from ctgedit.ui import help as H
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.SAVE_AS, catalog, start_path=START, kinds=("page",))
    try:
        dlg.resize(1300, 760)
        dlg.show()
        _app().processEvents()
        dlg.set_page_name("Browser_Test_Page")
        assert dlg.msg.text() == "17 / 64 characters"
        assert dlg.msg.geometry().top() >= dlg.ed_name.geometry().bottom()  # its own row
        dlg.set_page_name(START_NAME)
        assert dlg.msg.text() == "'%s' already exists. Saving replaces it." % START_NAME
        assert dlg.ok_button.text() == "Replace…"
        dlg.set_page_name("!Popup_Probe")
        assert dlg.msg.text() == "Names starting with '!' are system pages."
        dlg.set_page_name("a.b")
        assert dlg.msg.text() == "A dot in a page name looks like a library reference."
        assert dlg.btn_browse.text() == "Browse…"
        assert dlg.btn_browse.toolTip() == "Save to another folder"
        # the project combo: the page's project and its includes first
        items = [dlg.cb_project.itemText(k) for k in range(dlg.cb_project.count())]
        assert items[0].startswith(PROJECT + " (") and items[0].endswith(" pages)")
        seps = [k for k in range(len(items)) if not items[k]]
        assert seps and seps[0] == len(catalog.closure(catalog.project(PROJECT)))
        opened = []
        monkeypatch.setattr(H, "show_help", lambda topic="", **kw: opened.append(topic))
        assert dlg.btn_help.text() == "Help"
        dlg.btn_help.click()
        assert opened == ["opening-and-saving#the-page-browser"]
    finally:
        dlg.reject()
        dlg.deleteLater()


def test_open_texts(catalog):
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START)
    try:
        assert dlg.btn_browse.text() == "Browse…"
        assert dlg.btn_browse.toolTip() == "Open a page from another folder"
        dlg.set_kind("page")
        n = dlg.model.total
        assert dlg.search.placeholderText() == "Search %d pages" % n
        assert "tpl:" in dlg.search.toolTip() and "proj:" in dlg.search.toolTip()
        assert dlg.scope_lbl.text() == "%s · %d pages" % (PROJECT, n)
        menu = dlg.build_context_menu(dlg.current_entry())
        texts = [a.text() for a in menu.actions() if a.text()]
        assert texts[:2] == ["Open", "Live Render"]
        for want in ("Copy Path", "Copy Name", "Show in Explorer", "Delete…"):
            assert want in texts
        dlg.set_kind("symbol")
        el = dlg.current_entry()
        if el is not None:
            texts = [a.text() for a in dlg.build_context_menu(el).actions() if a.text()]
            assert "Used By…" in texts and any(t.startswith("Copy Reference (") for t in texts)
            assert dlg.table.isColumnHidden(3)  # no Resolution column
    finally:
        dlg.deleteLater()


def test_escape_clears_search_then_scope_then_closes(catalog):
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
    try:
        dlg.show()
        start = dlg.tree.current_base_scope().id
        dlg.search.setText("fxt")
        QTest.keyClick(dlg.grid, QtCore.Qt.Key_Escape)
        assert dlg.search.text() == "" and dlg.isVisible()
        assert dlg.tree.select_scope_id(dlg.tree.install_scope_id())
        assert dlg.current_scope().kind == "install"
        QTest.keyClick(dlg.grid, QtCore.Qt.Key_Escape)
        assert dlg.tree.current_base_scope().id == start and dlg.isVisible()
        QTest.keyClick(dlg.grid, QtCore.Qt.Key_Escape)
        assert dlg.result() == QtWidgets.QDialog.Rejected and not dlg.isVisible()
    finally:
        dlg.deleteLater()


def test_typing_in_the_grid_appends_to_the_search(catalog):
    from PySide6.QtTest import QTest
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
    try:
        dlg.show()
        dlg.search.setText("fx")
        dlg.search.setCursorPosition(0)
        QTest.keyClicks(dlg.grid, "t")
        assert dlg.search.text() == "fxt"
    finally:
        dlg.deleteLater()


def test_with_included_projects_and_saved_chain_scope(catalog):
    """'With included projects' shows the project and its include chain;
    a saved 'c:' scope id comes back as that (the check box ticked)."""
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    ex = catalog.project(PROJECT)
    app_settings().setValue("pagebrowser/open/scope", "c:" + ex.scope_id)
    dlg = _dlg(PageBrowserDialog.OPEN, catalog, kinds=("page",))
    try:
        s = dlg.current_scope()
        assert s.kind == "chain" and s.anchor is ex and dlg.chk_includes.isChecked()
        assert [p.key for p in s.projects] == [p.key for p in catalog.closure(ex)]
        assert dlg.scope_lbl.text().startswith(PROJECT + " with included projects · ")
        dlg.set_with_includes(False)
        assert dlg.current_scope().kind == "project"
        assert dlg.model.total == catalog.count(ex, "page")
    finally:
        dlg.reject()
        dlg.deleteLater()
    assert app_settings().value("pagebrowser/open/scope") == "p:" + ex.scope_id
    assert app_settings().value("pagebrowser/open/with_includes", True, type=bool) is False


def test_active_install_row_lists_every_project(catalog):
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=START, kinds=("page",))
    try:
        sid = dlg.tree.install_scope_id()
        assert dlg.tree.select_scope_id(sid)
        s = dlg.current_scope()
        assert s.kind == "install" and s.label == "All projects in %s" % catalog.live_install.short_label
        assert {p.key for p in s.projects} == {p.key for p in catalog.projects()}
        assert dlg.model.total == sum(catalog.count(p, "page") for p in catalog.projects())
        assert dlg.model.spans_projects
        e = dlg.model.entry(0)
        assert dlg.model.subtitle(e).startswith(e.project + " · ") or dlg.model.subtitle(e) == e.project
        assert dlg.scope_lbl.text().startswith("All projects in ")
    finally:
        dlg.deleteLater()


def test_element_search_ranks_like_the_library_panel(catalog):
    from ctgedit.ui.browser import PageBrowserDialog

    dlg = _dlg(PageBrowserDialog.PICK_ELEMENT, catalog, project_dir=PROJ_DIR, kinds=("genie",))
    try:
        dlg.search.setText("fx_agitator")
        dlg._apply_search()
        names = [e.name.lower() for e in dlg.model.entries()]
        assert names and names[0] == "fx_agitator"  # the exact name first
        assert "fx_agitator_mixer" in names
        dlg.search.setText("fxparts.fx_agitator_mixer")  # a pasted reference
        dlg._apply_search()
        assert [e.reference for e in dlg.model.entries()][:1] == ["fxparts.fx_agitator_mixer"]
    finally:
        dlg.deleteLater()


def test_live_render_of_the_selected_page(catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    small = THUMB
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=small, kinds=("page",))
    try:
        assert os.path.normcase(dlg.current_entry().path) == os.path.normcase(small)
        img = dlg.preview.render_now()
        assert img is not None and not img.isNull() and img.width() > 100
        # a different selection drops a stale result
        tok_before = dlg.preview._token
        dlg.select_name(START_NAME)
        assert dlg.preview._token != tok_before and dlg.preview.live_image is None
    finally:
        dlg.deleteLater()


def test_live_render_runs_in_its_own_process(catalog, tmp_path, monkeypatch):
    """Live render (Space) parses + renders in a separate process (browser.preview): no reader
    work on another thread of the editor, and a newer request stops the running one."""
    app = _app()
    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "data"))
    import threading
    from ctgedit.ui.browser import PageBrowserDialog, preview
    from ctgedit.io import reader

    small = THUMB
    calls = []
    real = reader.read_page
    monkeypatch.setattr(reader, "read_page", lambda p: calls.append(p) or real(p))
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=small, kinds=("page",))
    try:
        rnd = preview.renderer()
        threads = threading.active_count()
        dlg.preview.live_render()
        assert rnd.busy() and dlg.preview.live_status.text().startswith("Rendering")
        assert threading.active_count() == threads  # no worker thread
        first = rnd._job["proc"]
        # a newer request (another page) kills the running process
        dlg.select_name(START_NAME)
        dlg.preview.live_render()
        assert first.poll() is not None
        assert rnd.wait(120)
        # a waiting spare once the render is in (killed with the last pane). Read before
        # the event loop runs: it also deletes the earlier tests' dialogs, and the runner
        # takes two panes off its count for one of them, so the spare can go here while
        # this dialog's pane is still open
        spare = rnd._spare
        assert spare is not None and spare["proc"].poll() is None
        app.processEvents()
        img = dlg.preview.live_image
        assert img is not None and not img.isNull() and img.width() > 100
        assert "objects" in dlg.preview.live_status.text()
        assert not calls, "the editor process parsed the page itself"
        # the process leaves nothing behind but the waiting spare
        left = [f for f in os.listdir(tmp_path / "data" / "preview") if not f.endswith(".req")]
        assert not left, left
    finally:
        dlg.deleteLater()
        app.processEvents()
        from PySide6 import QtCore

        app.sendPostedEvents(None, int(QtCore.QEvent.DeferredDelete.value))
    # the dialog (its pane) is gone: so is the waiting process
    if spare is not None:
        spare["proc"].wait(10)
        assert rnd._spare is None


def test_recent_pages_scope(catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    genies = GENIES
    app_settings().setValue("mru", [START, r"C:\nowhere\gone.ctg", genies])
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, kinds=("page",))
    try:
        assert dlg.tree.select_scope_id("recent")
        assert dlg.current_scope().kind == "recent"
        # missing files dropped; most-recent-first order kept
        assert [e.name for e in dlg.model.entries()] == [START_NAME, "FxGenies"]
    finally:
        dlg.deleteLater()


def test_unregistered_start_page_is_shown_in_a_loose_folder(tmp_path, catalog):
    _app()
    from ctgedit.ui.browser import PageBrowserDialog

    proj = tmp_path / "Loose"
    proj.mkdir()
    shutil.copyfile(os.path.join(PROJ_DIR, "pages.DBF"), proj / "pages.DBF")
    shutil.copyfile(START, proj / (START_NAME + ".ctg"))
    shutil.copyfile(START, proj / "Unlisted.ctg")
    start = str(proj / "Unlisted.ctg")
    dlg = PageBrowserDialog(PageBrowserDialog.OPEN, catalog=catalog, start_path=start, kinds=("page",))
    try:
        s = dlg.current_scope()
        assert s.kind == "folder" and s.anchor.is_folder
        cur = dlg.current_entry()
        assert cur is not None and cur.name == "Unlisted" and cur.registered is False
        assert not dlg.model.hide_unregistered  # revealed for the page being edited
        assert dlg.preview.banner.isVisibleTo(dlg.preview)
    finally:
        dlg.deleteLater()


def _second_install(fx_install):
    """A second install beside the fake one (a sibling of its root, as an older install
    sits next to the active one): a copy of its User folder whose MASTER.DBF names the
    copy's projects. Every page of it shares its name with the live install's."""
    import fxinstall

    second = os.path.join(os.path.dirname(fx_install.root), "FxSecond")
    user = os.path.join(second, "User")
    shutil.copytree(fx_install.user, user)
    fxinstall._rewrite_master(os.path.join(user, "MASTER.DBF"), user)
    return user


def test_other_install_copy_points_at_the_live_one(fx_install):
    """A page of another install (FxSecond, made beside the fake install) points at the
    same page of the same project in the live install."""
    from ctgedit.io.catalog import catalog_for

    second = _second_install(fx_install)
    catalog = catalog_for(USER)
    others = [i for i in catalog.installs if not i.is_live]
    assert [os.path.normcase(i.user_dir) for i in others] == [os.path.normcase(second)]
    for inst in others:
        for info in catalog.projects(inst):
            for e in catalog.pages(info):
                assert e.is_other_install and not e.is_live
                live = catalog.live_copy_of(e.path)
                if live is not None:
                    assert catalog.project_for_path(live).is_live
                    assert os.path.basename(live).lower() == os.path.basename(e.path).lower()
                    return
    pytest.fail("no page of the second install has a live copy")


def test_a_start_scope_without_the_kind_shows_its_includes(catalog):
    """A remembered 'With included projects' off never opens on an empty
    list: a picker, and Open on its last scope, show the include chain when
    the project itself has none of the kind (the setting stays as it was)."""
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.prefs.settings import app_settings

    ex = catalog.project(PROJECT)
    assert catalog.count(ex, "template") == 0 and any(
        catalog.count(p, "template") for p in catalog.closure(ex)[1:]
    )
    st = app_settings()
    st.setValue("pagebrowser/pick/with_includes", False)
    dlg = _dlg(PageBrowserDialog.PICK_TEMPLATE, catalog, project_dir=PROJ_DIR, kinds=("template",))
    try:
        assert dlg.current_scope().kind == "chain" and dlg.model.total
    finally:
        dlg.reject()
        dlg.deleteLater()
    assert st.value("pagebrowser/pick/with_includes", True, type=bool) is False
    st.setValue("pagebrowser/open/scope", "p:" + ex.scope_id)
    st.setValue("pagebrowser/open/kind", "template")
    st.setValue("pagebrowser/open/with_includes", False)
    dlg = _dlg(PageBrowserDialog.OPEN, catalog)
    try:
        assert dlg.current_kind() == "template"
        assert dlg.current_scope().kind == "chain" and dlg.model.total
        # unticked by hand: the project alone, and the status says why it is empty
        dlg.set_with_includes(False)
        assert dlg.model.total == 0 and "With included projects" in dlg.status.text()
    finally:
        dlg.deleteLater()
    st.setValue("pagebrowser/pick/with_includes", True)
    dlg = _dlg(PageBrowserDialog.PICK_TEMPLATE, catalog, project_dir=PROJ_DIR, kinds=("template",))
    try:
        dlg.set_with_includes(False)
        assert dlg.model.total == 0
        # a picker on one project: not 'nothing on the include path'
        assert "With included projects" in dlg.status.text()
    finally:
        dlg.deleteLater()


def test_open_from_a_library_element_starts_on_its_kind(catalog):
    """File > Open from an element's window (the start path is its library):
    the element's kind, an element of that library selected."""
    from ctgedit.ui.browser import PageBrowserDialog

    lib = next(
        (os.path.join(PROJ_DIR, f) for f in sorted(os.listdir(PROJ_DIR)) if f.lower().endswith(".ctm")), None
    )
    assert lib is not None  # (FxMain's fxback.ctm)
    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=lib)
    try:
        assert dlg.current_kind() == "genie"
        e = dlg.current_entry()
        assert e is not None and os.path.normcase(e.path) == os.path.normcase(lib)
        names = [
            x.name
            for x in dlg.model.entries()
            if x.is_element and os.path.normcase(x.path) == os.path.normcase(lib)
        ]
    finally:
        dlg.deleteLater()
    if len(names) < 2:
        return
    # the element open in the window is selected, not the library's first
    dlg = _dlg(PageBrowserDialog.OPEN, catalog, start_path=lib, start_element=names[-1])
    try:
        e = dlg.current_entry()
        assert e is not None and e.name == names[-1]
        assert os.path.normcase(e.path) == os.path.normcase(lib)
    finally:
        dlg.deleteLater()
