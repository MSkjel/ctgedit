"""The chain after a library save."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from ctgedit.format import writer  # noqa: E402

MARK = b"MARKXYZ"


def _qt():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _count(path: str) -> int:
    with open(path, "rb") as fh:
        return fh.read().count(MARK)


def _records_marked(records) -> int:
    return sum(MARK in writer.object_payload(o) for o in records)


def _edit_value(fx) -> str:
    """fxgenie.fx_value's text becomes MARKXYZ, saved (not through a window); its library."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx.file("FxLib", "fxgenie.ctm")
    d = ElementDocument.open_element(lib, "fx_value")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, MARK.decode())
    d.save()
    libraries_changed()
    return lib


def _edit_n0(fx) -> str:
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx.file("FxLib", "fxnest.ctm")
    d = ElementDocument.open_element(lib, "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% CHANGED")
    d.move_object(i, 30, 0)
    d.save()
    libraries_changed()
    return lib


def _waiting(lib, element):
    """(kind, label, registered) of every user still needing an update."""
    from ctgedit.editing import libraries_changed
    from ctgedit.io import genieusage as GU

    libraries_changed()
    return [
        (u.ref.kind, u.ref.label, u.registered)
        for u in GU.element_users(lib, element).users
        if u.needs_update
    ]


@pytest.fixture
def window(fx_install, monkeypatch):
    """A main window whose prompts are answered by ``answers`` ('click': the button text,
    'again': tick Don't ask again); the asked texts in ``asked``."""
    _qt()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_genie_save_pages
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    app_settings().setValue("opt/auto_update_genies", True)
    set_genie_save_pages("ask")
    asked, answers = [], {"click": None, "again": False}

    def prompt(box):
        asked.append((box.text(), box.informativeText()))
        if answers["again"] and box.checkBox() is not None:
            box.checkBox().setChecked(True)
        for b in box.buttons():
            if answers["click"] and b.text() == answers["click"]:
                b.click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(prompt))
    # (Update Users' result window: its text is kept, nothing shown)
    reports = []
    monkeypatch.setattr(MainWindow, "_report_users_update", lambda self, text, **kw: reports.append(text))
    w = MainWindow()
    w.reports = reports
    w.asked, w.answers = asked, answers
    try:
        yield w
    finally:
        for e in list(w._docs):
            e["doc"].dirty = False
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        UpdatePagesRun.users_busy = ""
        UpdatePagesRun.users_queue.clear()
        UpdatePagesRun.chain_busy = False


# ------------------------------------------------------------------- templates, super genies
def test_the_chain_applies_a_saved_template_again_on_its_pages(fx_install, fx_page):
    from ctgedit.workers import genie_worker as GW

    lib = _edit_value(fx_install)
    pages = [fx_page(n) for n in ("FxTplLast", "FxTplLinked", "FxTplNoTitle")]
    assert [_count(p) for p in pages[:2]] == [0, 0]
    res = GW.update_closed_pages(lib, "fx_value")
    assert "fx_style.normal@XGA" in res["elements_saved"]
    assert all(_count(p) > 0 for p in pages[:2])
    assert {"FxTplLast", "FxTplLinked", "FxTplNoTitle"} <= set(res["updated"])
    assert res["templates"] >= 3
    # nothing registered is left out of date (FxLarge is not registered: left, named)
    assert _waiting(lib, "fx_value") == [("page", "FxLarge", False)]
    assert res["unregistered"] == ["FxLarge"]


def test_the_chain_makes_the_super_genie_pages_again(fx_install, fx_project):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    sg = os.path.join(fx_project("FxMain"), "!fx_detail.ctg")
    before = open(sg, "rb").read()
    res = GW.update_closed_pages(lib, "fx_on")
    assert "fxsuper.!fx_detail@SUPER" in res["elements_saved"]
    assert open(sg, "rb").read() != before
    assert res["supergenies"] >= 1 and res["templates"] >= 1
    assert all(reg is False for _k, _l, reg in _waiting(lib, "fx_on"))


def test_a_page_open_in_a_tab_takes_the_template_the_chain_saved(window, fx_install, fx_page):
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("never")
    window.open_document(fx_page("FxTplLast"))
    page = window.doc
    assert _records_marked(page.parsed.page.objects) == 0
    _edit_value(fx_install)

    class Saved:  # (what _update_closed_pages reads)
        path, element_name = fx_install.file("FxLib", "fxgenie.ctm"), "fx_value"
        display_name = "fxgenie.fx_value"

    window._update_closed_pages(Saved())
    assert _records_marked(page.parsed.page.objects) > 0
    assert page.dirty and page.template_status()[0] == "current"
    assert _count(fx_page("FxTplLast")) == 0  # (open: left to its tab)


# ------------------------------------------------------------------- one result for both paths
def _snapshot(root: str) -> dict:
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).lower()] = fh.read()
    return out


def test_update_users_and_the_chain_write_the_same_files(window, fx_install, tmp_path, monkeypatch):
    import datetime as dt

    import fxinstall
    from ctgedit.editing import libraries_changed
    from ctgedit.infra import caches, identity
    from ctgedit.io import genieusage as GU

    # (the same stamps and GUIDs in both runs: a fixed clock later than the fixture's)
    when = dt.datetime(2026, 6, 1, 12, 0, 0)
    monkeypatch.setattr(identity, "now", lambda: when)
    monkeypatch.setattr(identity, "now_time", lambda: 1780315200.0)
    monkeypatch.setenv(identity.ENV, "chain-parity")
    parent = tmp_path / "other"
    parent.mkdir()
    other = fxinstall.install_copy(str(parent))
    results = {}
    for tag, fx in (("users", fx_install), ("chain", other)):
        monkeypatch.setenv("CTG_SCADA_ROOT", fx.root)
        caches.clear("tests", "refresh", "libraries")
        libraries_changed()
        before = _snapshot(fx.root)
        identity.reset()
        lib = _edit_value(fx)
        if tag == "users":
            rep = window._update_element_users(GU.element_users(lib, "fx_value"), confirm=False)
            assert rep is not None and rep.elements_saved
        else:

            class Saved:
                path, element_name, display_name = lib, "fx_value", "fxgenie.fx_value"

            window._update_closed_pages(Saved())
        after = _snapshot(fx.root)
        results[tag] = {k: v for k, v in after.items() if before.get(k) != v}
    a, b = results["users"], results["chain"]
    assert any(k.endswith("fxtpllast.ctg") for k in a)
    assert sorted(a) == sorted(b)
    assert [k for k in a if a[k] != b[k]] == []


# ------------------------------------------------------------------- what it leaves, said
def test_unregistered_pages_are_named_not_counted(window, fx_install):
    from ctgedit.workers import genie_worker as GW

    lib = _edit_n0(fx_install)
    res = GW.update_closed_pages(lib, "n0")
    assert sorted(res["unregistered"]) == ["FxMembers", "FxParts"]
    text = window._users_run_finished(dict(res), "fxnest.n0")
    assert "2 pages not registered in their project were left alone." in text
    details = window._users_run_details(res)
    assert "not registered" in details and "FxMembers" in details


def test_the_genie_saved_question_counts_registered_pages_only(window, fx_install):
    window.answers["click"] = "Later"
    window._open_element(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    n0 = window.doc
    i = next(k for k, o in enumerate(n0.parsed.page.objects) if o.type_code == 0x05)
    n0.set_text(i, "%Label% CHANGED")
    assert window.save()
    text, info = window.asked[0]
    assert "1 closed page" in text and "3 closed pages" not in text
    assert "2 pages not registered in their project are left alone: FxMembers, FxParts" in info


def test_a_save_used_by_unregistered_pages_only_says_so(window, fx_install, monkeypatch):
    from ctgedit.ui import notify as N

    said = []
    monkeypatch.setattr(N, "notify", lambda win, text, level="info", **kw: said.append((text, level)))
    # (fxparts.fx_row: only FxParts places it, and FxParts is not registered)
    window._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    row = window.doc
    roots = [k for k, o in enumerate(row.page.objects) if o.parent is None]
    row.move_objects(roots, 0, 2)
    assert window.save()
    assert not window.asked  # (nothing to update: not asked)
    assert (
        "fxparts.fx_row saved. 1 page not registered in its project is left alone: FxParts.",
        "info",
    ) in said


def test_a_library_that_could_not_be_read_is_said(window, fx_install):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import genieusage as GU

    lib = _edit_n0(fx_install)
    parts = fx_install.file("FxLib", "fxparts.ctm")
    data = bytearray(open(parts, "rb").read())
    data[0:16] = b"\0" * 16
    with open(parts, "wb") as fh:
        fh.write(bytes(data))
    GU.clear_cache()
    res = GW.update_closed_pages(lib, "n0")
    assert any("fxparts.ctm" in p for p in res["problems"])
    assert any(u[0].endswith("fxparts.ctm") for u in res["unreadable"])
    text = window._users_run_finished(dict(res), "fxnest.n0")
    assert "1 file could not be read" in text
    assert "Not checked (could not be read)" in window._users_run_details(res)
    info = window._users_left_text(GU.element_users(lib, "n0"), "fxnest.n0")
    assert "1 file could not be read, so what uses fxnest.n0 through it was not checked" in info


def test_a_page_closed_unsaved_during_the_run_is_named(window, fx_install, fx_page):
    from ctgedit.workers import genie_worker as GW

    lib = _edit_n0(fx_install)
    host = fx_page("FxGenies")
    res = GW.update_closed_pages(lib, "n0", skip=[host])
    assert res["open_at_start"] == [os.path.abspath(host)]
    assert "FxGenies" not in res["updated"]
    # (it was open when the run started; closed now, its file as it was)
    seen = {host: os.stat(host).st_mtime_ns}
    assert window._closed_meanwhile(seen) == ["FxGenies"]
    res["closed_meanwhile"] = window._closed_meanwhile(seen)
    assert "1 page closed during the update was not updated." in window._users_run_finished(
        dict(res), "fxnest.n0"
    )
    assert "run Update Users again" in window._users_run_details(res)
    # saved before it was closed: not named
    assert window._closed_meanwhile({host: 1}) == []
    # still open: not named
    window.open_document(host)
    assert window._closed_meanwhile(seen) == []


# ------------------------------------------------------------------- the Symbol Saved question
def _save_symbol(window, fx, dy: int):
    window._open_element(fx.file("FxLib", "fxsym.ctl"), "fx_box")
    sym = window.doc
    roots = [k for k, o in enumerate(sym.page.objects) if o.parent is None]
    sym.move_objects(roots, 0, dy)
    assert window.save()


def test_dont_ask_again_with_update_keeps_updating(window, fx_install, fx_page, monkeypatch):
    import datetime as dt

    from ctgedit.infra import identity
    from ctgedit.ui.prefs.settings import symbol_save_pages

    page = fx_page("FxSymbols")
    window.answers.update(click="Update in Background", again=True)
    _save_symbol(window, fx_install, 2)
    assert window.asked and symbol_save_pages() == "always"
    before = open(page, "rb").read()
    first = window._users_job
    window.asked.clear()
    # (the next save a minute on: its stamp differs from the first one's, whole seconds)
    later = identity.now() + dt.timedelta(minutes=1)
    monkeypatch.setattr(identity, "now", lambda: later)
    _save_symbol(window, fx_install, 2)
    assert not window.asked  # (not asked again)
    assert window._users_job is not first  # (the chain ran again)
    assert open(page, "rb").read() != before  # (and updated the page)


def test_dont_ask_again_with_later_leaves_them(window, fx_install, fx_page):
    from ctgedit.ui.prefs.settings import symbol_save_pages

    page = fx_page("FxSymbols")
    window.answers.update(click="Later", again=True)
    before = open(page, "rb").read()
    _save_symbol(window, fx_install, 2)
    assert symbol_save_pages() == "never"
    window.asked.clear()
    _save_symbol(window, fx_install, 2)
    assert not window.asked and open(page, "rb").read() == before


# ------------------------------------------------------------------- thumbnails
def test_the_elements_the_chain_saves_get_a_fresh_thumbnail(fx_install):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.format import pagesummary as PS
    from ctgedit.render import headless_thumb

    lib = _edit_n0(fx_install)
    assert PS.read_element_summary(lib, "n1").thumbnail_size == (0, 0)  # (none stored)
    res = GW.update_closed_pages(lib, "n0")
    assert "fxnest.n1" in res["elements_saved"]
    s = PS.read_element_summary(lib, "n1")
    assert s.thumbnail_size != (0, 0)
    # the element as stored, drawn: the same picture
    want = headless_thumb.element_thumbnail(ElementDocument.open_element(lib, "n1"))
    from ctgedit.format import element as E

    assert E.open_element(lib, "n1").block.data.find(want) > 0


def test_update_users_writes_fresh_page_thumbnails(window, fx_install, fx_page):
    from ctgedit.editing import EditableDocument
    from ctgedit.io import genieusage as GU
    from ctgedit.render import headless_thumb

    lib = _edit_n0(fx_install)
    page = fx_page("FxGenies")
    old = EditableDocument.open(page).parsed.page.thumbnail
    rep = window._update_element_users(GU.element_users(lib, "n0"), confirm=False)
    assert "FxGenies" in rep.pages.updated
    doc = EditableDocument.open(page)
    assert doc.parsed.page.thumbnail != old
    assert doc.parsed.page.thumbnail == headless_thumb.page_thumbnail(doc)


# ------------------------------------------------------------------- template saves
def test_a_tab_whose_template_the_chain_did_not_save_keeps_it(window, fx_install, fx_page):
    """The chain re-applies in the open tabs only the templates and super genies it saved
    again: a tab whose template is out of date for another reason (the user undid its
    automatic update at the open) is left."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.prefs.settings import app_settings, set_genie_save_pages

    set_genie_save_pages("never")
    tpl = ElementDocument.open_element(fx_install.file("FxStyle", "fx_style.ctt"), "normal@XGA")
    tpl.move_object(0, 3, 2)
    tpl.save()
    libraries_changed()
    st = app_settings()
    st.setValue("opt/auto_update_template", False)  # (as if undone at the open)
    window.open_document(fx_page("FxTplLast"))
    page = window.doc
    assert page.template_status()[0] == "stale"
    st.setValue("opt/auto_update_template", True)
    lib = _edit_n0(fx_install)

    class Saved:
        path, element_name, display_name = lib, "n0", "fxnest.n0"

    window._update_closed_pages(Saved())
    assert page.template_status()[0] == "stale"
    assert not page.dirty


def test_the_symbol_saved_question_skips_unregistered_pages_only(window, fx_install, monkeypatch):
    """A symbol whose only out-of-date users are pages not registered in their project: no
    question (the chain would leave them), a note names them, as for a genie."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui import notify as N

    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    GW.update_closed_pages(lib, "fx_on")
    assert sorted(lbl for _k, lbl, _r in _waiting(lib, "fx_on")) == ["FxArrange", "FxSimBig"]
    said = []
    monkeypatch.setattr(N, "notify", lambda win, text, level="info", **kw: said.append((text, level)))
    window.answers["click"] = "Later"

    class Saved:
        path, element_name, display_name, kind = lib, "fx_on", "fxsym.fx_on", "symbol"

    window._users_saved_prompt(Saved())
    assert not window.asked
    assert (
        "fxsym.fx_on saved. 2 pages not registered in their project are left alone: FxArrange, FxSimBig.",
        "info",
    ) in said


def test_a_page_saved_during_the_run_before_its_genies_were_saved_is_named(window, fx_install, fx_page):
    """A page open when the chain started that uses a genie the chain saves again takes that
    genie in its tab only at the end of the run: closed before then, it is named even when
    its tab was saved meanwhile (with no genie saved again, a save of its tab makes it
    current: not named)."""
    from ctgedit.workers import genie_worker as GW

    lib = _edit_n0(fx_install)
    host = fx_page("FxGenies")
    t0 = os.stat(host).st_mtime_ns
    res = GW.update_closed_pages(lib, "n0", skip=[host])
    assert os.path.abspath(host) in res["after_saves"]
    os.utime(host, ns=(t0 + 10**9, t0 + 10**9))  # (its tab saved during the run)
    assert window._closed_meanwhile({host: t0}, res["after_saves"]) == ["FxGenies"]
    assert window._closed_meanwhile({host: t0}) == []


def test_the_headless_thumbnail_leaves_the_editor_environment(fx_install, fx_page, monkeypatch):
    """Update Users renders thumbnails in the editor's own process: its environment is left
    as it is (an offscreen platform set there would hide the windows of the programs it
    starts later)."""
    _qt()
    from ctgedit.editing import EditableDocument
    from ctgedit.render import headless_thumb

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    assert headless_thumb.page_thumbnail(EditableDocument.open(fx_page("FxGenies")))
    assert "QT_QPA_PLATFORM" not in os.environ
