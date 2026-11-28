"""Run reports and prompts after a library save."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _drop_win(w):
    from ctgedit.ui.main_window import MainWindow

    for ent in list(getattr(w, "_docs", ())):
        if hasattr(ent["doc"], "dirty"):
            ent["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.close()
    w.deleteLater()


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    _drop_win(w)


@pytest.fixture(autouse=True)
def _quiet_runs():
    """The save prompts off, earlier versions kept; the run holds and the history as they
    were after each test."""
    from ctgedit.infra import history
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    st = app_settings()
    for k, v in (
        ("opt/save_genie_warning", False),
        ("opt/genie_save_pages", "never"),
        ("opt/symbol_save_pages", "never"),
        ("opt/template_save_pages", "never"),
        ("opt/auto_update_genies", True),
        ("opt/auto_update_template", True),
    ):
        st.setValue(k, v)
    kept = dict(history._CONFIG)
    yield
    history._CONFIG.clear()
    history._CONFIG.update(kept)
    UpdatePagesRun.users_busy = ""
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = False
    UpdatePagesRun.tabs_run = ""
    UpdatePagesRun.active = None
    UpdatePagesRun.users_queue.clear()
    UpdatePagesRun.users_retry = False


def _settle(n=6):
    for _ in range(n):
        _app().processEvents()


def _edit_text(lib, name, text):
    """``name``'s first root text set to ``text`` and saved (its users are out of date)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    g = ElementDocument.open_element(lib, name)
    t = next(i for i, o in enumerate(g.page.objects) if o.parent is None and g.objects[i].type_code == 0x05)
    assert g.set_text(t, text)
    g.save()
    libraries_changed()


def _payload(doc) -> bytes:
    from ctgedit.format import writer

    return b"".join(writer.object_payload(o) for o in doc.parsed.page.objects)


def _status(lib, saved, user):
    from ctgedit.editing import libraries_changed
    from ctgedit.io import genieusage as GU

    libraries_changed()
    u = GU.element_users(lib, saved).of(GU.Ref("element", lib, user))
    return u.status if u else None


def _run(win, how, lib, element):
    """The chain after a save of ``element`` (inline: no event loop) or Update Users. The
    texts it gave (the chain's note, or Update Users' report lines)."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow

    texts = []
    if how == "gui":
        orig = MainWindow._report_users_update
        MainWindow._report_users_update = lambda self, text, **k: texts.extend(text.split("\n"))
        try:
            win._update_element_users_run(GU.element_users(lib, element), confirm=False)
        finally:
            MainWindow._report_users_update = orig
    else:
        c = notify.centre(win)
        c.clear()
        stem = os.path.splitext(os.path.basename(lib))[0]
        assert win._start_users_run(lib, element, "%s.%s" % (stem, element))
        texts = [m.text for m in c.entries]
    _settle()
    return texts


# -- an earlier version of a library element ----------------------------------------------------
@pytest.mark.parametrize("how", ["chain", "gui"])
def test_a_version_tab_of_an_element_is_not_its_tab(fx_install, win, how):
    """An earlier version of fxnest.n2 open read-only, its own tab closed; fxnest.n0 saved.
    The chain and Update Users save n2 from its library and leave the view."""
    from ctgedit.infra import history

    history.configure(5, 50)
    nest = fx_install.file("FxLib", "fxnest.ctm")
    win._open_element(nest, "n2")
    v = history.keep_version(nest, open(nest, "rb").read(), element="n2", force=True)
    assert v is not None
    win.open_version_read_only(v)
    ver = win.doc
    assert ver.read_only and ver.ui.version_view is not None
    win._close_doc_tab(next(k for k, e in enumerate(win._docs) if e["doc"] is not ver), ask=False)
    before = _payload(ver)
    _edit_text(nest, "n0", "%Label% CHANGED")
    texts = _run(win, how, nest, "n0")
    assert not any("read only" in t for t in texts), texts
    assert ver.read_only and ver.ui.version_view is not None and not ver.dirty
    assert _payload(ver) == before
    assert _status(nest, "n0", "n2") == "up to date"
    # (the version is not the element's tab for the helper's list nor for Update Users)
    from ctgedit.io import genieusage as GU

    assert "\tn2" not in win._element_tab_states()
    assert win._open_element_doc(GU.Ref("element", nest, "n2")) is None


def test_the_helpers_editor_list_reads_dirty_when_any_tab_is(tmp_path):
    """Two rows of one element (a version listed before the own tab): the element reads dirty
    when any of its tabs has unsaved changes."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import genieusage as GU

    lib = str(tmp_path / "fxnest.ctm")
    f = tmp_path / "editors.txt"
    f.write_text("clean\t%s\tn2\ndirty\t%s\tN2\nclean\t%s\tn3\n" % (lib, lib, lib), encoding="utf-8")
    state = GW._editor_state(str(f))
    assert state(GU.Ref("element", lib, "n2")) == "dirty"
    assert state(GU.Ref("element", lib, "n3")) == "clean"
    assert state(GU.Ref("element", lib, "n4")) is None


# -- a read-only page tab --------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["chain", "gui"])
def test_a_version_tab_of_a_page_is_a_closed_page_for_the_runs(fx_install, win, how):
    """An earlier version of FxGenies open read-only, its own tab closed; fxnest.n0 saved. The
    runs update the file and leave the view."""
    from ctgedit.infra import history
    from ctgedit.io import updatepages as UP

    history.configure(5, 50)
    page = fx_install.page("FxGenies")
    v = history.keep_version(page, open(page, "rb").read(), force=True)
    assert v is not None
    win.open_document(page)
    win.open_version_read_only(v)
    ver = win.doc
    win._close_doc_tab(next(k for k, e in enumerate(win._docs) if e["doc"] is not ver), ask=False)
    before = _payload(ver)
    nest = fx_install.file("FxLib", "fxnest.ctm")
    _edit_text(nest, "n0", "%Label% CHANGED")
    assert UP.page_needs_update(page)
    texts = _run(win, how, nest, "n0")
    assert not any("open in a tab" in t.lower() for t in texts), texts
    assert not UP.page_needs_update(page)
    assert _payload(ver) == before and not ver.dirty and ver.read_only
    assert win._open_page_paths() == []


def test_a_read_only_page_tab_is_no_page_tab_for_the_runs(fx_install, win):
    """A super genie page opened to view, or a page whose file is read only (read_only): the
    open-tabs step of Update Pages and Update Users does not change it, and the runs list it
    as closed."""
    page = fx_install.page("FxGenies")
    win.open_document(page)
    d = win.doc
    assert win._open_page_paths() == [page]
    assert [x[2] for x in win._open_documents_in([os.path.dirname(page)])] == [d]
    d.read_only = True
    d.ui.supergenie_view = True
    assert win._open_page_paths() == []
    assert win._open_documents_in([os.path.dirname(page)]) == []
    assert win._open_tab_marks() == {}


def test_a_super_genie_save_leaves_a_read_only_tab(fx_install, win, monkeypatch):
    """A super genie page opened to view does not take the saved super genie in place; the
    page's own tab still does."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/save_supergenie_warning", False)
    page = os.path.join(fx_install.project("FxMain"), "!fx_popup.ctg")
    calls = []
    monkeypatch.setattr(EditableDocument, "supergenie_status", lambda self: ("stale", ""))
    monkeypatch.setattr(
        EditableDocument, "update_supergenie_page", lambda self, *a, **k: calls.append(self) or 1
    )
    view = win._open_page_file(page, checks=False)
    view.read_only = True
    view.ui.supergenie_view = True
    win.set_document(view)
    where = view.supergenie_element()
    assert where is not None
    sg = type(
        "Saved", (), {"path": where[0], "element_name": where[1], "display_name": "fxsuper." + where[1]}
    )()
    win._supergenie_saved_prompt(sg)
    assert calls == []
    view.read_only = False
    view.ui.supergenie_view = False
    win._supergenie_saved_prompt(sg)
    assert calls == [view]


# -- an undone template update stays undone ----------------------------------------------------
def _template_always(win, monkeypatch):
    """'always' for templates with the chain held back: the queued chains."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import set_template_save_pages

    set_template_save_pages("always")
    queued = []
    monkeypatch.setattr(
        MainWindow,
        "_update_closed_pages",
        lambda self, doc: queued.append((doc.path, doc.element_name, doc.display_name)),
    )
    return queued


def _outer_template(fx):
    """mytpl.made@XGA built on fx_style.normal@XGA (Copy to Library of FxTplLinked's template
    objects) and FromX, a page linked to it. (inner template file, FromX)."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import template_place as TP

    d = EditableDocument.open(fx.page("FxTplLinked"))
    a, b, _ref = TP.template_roots(d.parsed.page.objects)[0]
    lib = os.path.join(fx.project("FxMain"), "mytpl.ctt")
    assert d.copy_to_library(list(range(a, b)), lib, "made@XGA")
    libraries_changed()
    page = os.path.join(fx.project("FxMain"), "FromX.ctg")
    EditableDocument.new_from_template("mytpl.made@XGA.<ctt", page, linked=True)
    libraries_changed()
    return fx.file("FxStyle", "fx_style.ctt"), page


def _save_inner(win, inner):
    win._open_element(inner, "normal@XGA")
    win.doc.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    assert win.save()
    _settle()


def _undo_in(win, page_doc):
    win.doc_tabs.setCurrentIndex(next(k for k, e in enumerate(win._docs) if e["doc"] is page_doc))
    _settle()
    win.undo()
    page_doc.move_objects([0], 1, 0)  # (and goes on editing: the redo goes)
    assert not page_doc.can_redo()


@pytest.mark.parametrize("outer", [False, True])
def test_the_chains_end_leaves_a_template_update_the_user_undid(fx_install, win, monkeypatch, outer):
    """The page took the saved template at the save; the user undid that step while the chain
    ran. The chain's end does not apply the template again over the user's edit (outer:
    through the template built on it, which the chain saved again)."""
    queued = _template_always(win, monkeypatch)
    inner, fromx = _outer_template(fx_install)
    win.open_page(fromx if outer else fx_install.page("FxTplLast"))
    p = win.doc
    _settle()
    p.dirty = False
    steps = len(p._undo)
    _save_inner(win, inner)
    assert p.template_status()[0] == "current" and b"TPLNEW" in p.serialize()
    assert len(p._undo) == steps + 1
    _undo_in(win, p)
    assert p.template_status()[0] == "stale"
    assert win._start_users_run(*queued[0])
    _settle()
    assert p.template_status()[0] == "stale" and b"TPLNEW" not in p.serialize()
    assert p._undo[-1].label == "move"


def test_the_chains_end_brings_a_kept_outer_template_step_up_to_date_quietly(fx_install, win, monkeypatch):
    """Kept, the step the save made in a page of the outer template is followed at the
    chain's end by one that takes the outer template's new version (its link table only),
    with no second note."""
    from ctgedit.ui import notify

    queued = _template_always(win, monkeypatch)
    inner, fromx = _outer_template(fx_install)
    win.open_page(fromx)
    p = win.doc
    _settle()
    p.dirty = False
    _save_inner(win, inner)
    records = _payload(p)
    c = notify.centre(win)
    c.clear()
    assert win._start_users_run(*queued[0])
    _settle()
    assert p.template_status()[0] == "current" and _payload(p) == records
    assert not any(m.text.startswith("The template ") for m in c.entries), [m.text for m in c.entries]


# -- Cancel of the usage read ----------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["always", "ask"])
def test_a_cancelled_usage_read_says_nothing_was_updated(fx_install, win, monkeypatch, mode):
    """A cancelled usage read says nothing was updated."""
    from ctgedit.ui.jobs import job_progress as JP
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages(mode)
    started, asked, runs = [], [], []
    monkeypatch.setattr(MainWindow, "_start_users_run", lambda self, *a: started.append(a))
    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(lambda box: asked.append(1)))
    lib = fx_install.file("FxLib", "fxgenie.ctm")
    win._open_element(lib, "fx_value")
    el = win.doc
    i = next(k for k, o in enumerate(el.parsed.page.objects) if o.type_code == 0x05)
    el.move_object(i, 4, 6)
    monkeypatch.setattr(JP.JobProgress, "tick", lambda self, i, total, text: False)
    c = notify.centre(win)
    c.clear()
    assert win.save()
    _settle()
    assert started == [] and asked == []
    msgs = [m for m in c.entries if m.level == "warning"]
    # (only the saved genie is open: nothing is said of the open tabs)
    assert [m.text for m in msgs] == [
        "fxgenie.fx_value saved. Finding what uses it was stopped, so the library genies and "
        "closed pages that use it were not updated."
    ], [m.text for m in c.entries]
    assert win.statusBar().currentMessage() == msgs[0].text
    (act,) = msgs[0].actions
    assert act.label == "Update Users"
    monkeypatch.setattr(
        MainWindow, "_update_element_users", lambda self, usage, confirm=True: runs.append(usage)
    )
    monkeypatch.setattr(MainWindow, "_element_users", lambda self, path, element, title="": (path, element))
    act.run()
    assert runs == [(lib, "fx_value")]


def test_the_usage_note_takes_the_opening_notes_place_and_never_a_newer_ones(fx_install, win, monkeypatch):
    """A moment after a genie opens, the note of the pages that place it replaces the opening
    note; a newer note (what a quick save said) stays."""
    from types import SimpleNamespace

    from ctgedit.ui import lifecycle

    monkeypatch.setattr(lifecycle, "element_usage", lambda path, element: [SimpleNamespace(name="FxMain")])
    win._open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    doc, bar = win.doc, win.statusBar()
    opening = bar.currentMessage()
    assert opening.startswith("Editing ")
    win._usage_notice(doc, opening)
    assert bar.currentMessage().startswith("fxgenie.fx_value is placed directly on 1 page (FxMain).")
    bar.showMessage("fxgenie.fx_value saved.")
    win._usage_notice(doc, opening)
    assert bar.currentMessage() == "fxgenie.fx_value saved."


# -- a page of a template built on the saved one ------------------------------------------------
@pytest.mark.parametrize("how", ["later", "never"])
def test_an_open_page_of_a_template_built_on_the_saved_one_takes_it_at_the_save(
    fx_install, win, monkeypatch, how
):
    """An open page of a template built on the saved one takes it at the save."""
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import set_template_save_pages

    started, asked = [], []
    monkeypatch.setattr(MainWindow, "_start_users_run", lambda self, *a: started.append(a))

    def later(box):
        asked.append(box.informativeText())
        next(b for b in box.buttons() if b.text() == "Later").click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(later))
    set_template_save_pages("ask" if how == "later" else "never")
    inner, fromx = _outer_template(fx_install)
    win.open_page(fx_install.page("FxTplLast"))
    last = win.doc
    win.open_page(fromx)
    outer = win.doc
    _settle()
    for d in (last, outer):
        assert d.template_status()[0] == "current"
        d.dirty = False
    c = notify.centre(win)
    c.clear()
    _save_inner(win, inner)
    assert started == []
    for d in (last, outer):
        assert d.template_status()[0] == "current" and d.dirty and b"TPLNEW" in d.serialize()
    if how == "later":
        assert len(asked) == 1 and "Open pages are updated in their tabs." in asked[0]
    notes = sorted(m.text for m in c.entries if m.text.startswith("The template "))
    assert notes == [
        "The template fx_style.normal@XGA of FxTplLast changed since the page was saved and "
        "was applied again (Undo keeps the old version).",
        "The template mytpl.made@XGA of FromX changed since the page was saved and was "
        "applied again (Undo keeps the old version).",
    ], notes
    # (and the undo step is named so too)
    assert all(".<ctt" not in s for d in (last, outer) for s, _st in d.ui.auto_changes)


def test_a_template_is_named_as_its_library_spells_it():
    from ctgedit.ui.names import template_name

    assert template_name("fx_style.normal@XGA.<ctt") == "fx_style.normal@XGA"
    assert template_name("fx_style.normal@XGA.<ctt#ee80103c") == "fx_style.normal@XGA"
    assert template_name("plant.v2.tpl@HD1080.<ctt") == "plant.v2.tpl@HD1080"
    assert template_name("fx_style.normal") == "fx_style.normal"
    assert template_name("") == ""


# -- Cancel in the open tabs' step of a run that saved a template ------------------------------
def test_cancel_in_the_open_tabs_step_brings_a_saved_template_to_the_tab(fx_install, win, monkeypatch):
    """Update Users of normal@XGA with FromX open (its template stale); Cancel in 'Updating the
    pages open in tabs'. The run saved mytpl.made@XGA again: FromX takes it in its tab."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.jobs import job_progress as JP
    from ctgedit.ui.main_window import MainWindow

    inner, fromx = _outer_template(fx_install)
    win.open_document(fromx)
    tab = win.doc
    _settle()
    tab.dirty = False
    n = ElementDocument.open_element(inner, "normal@XGA")
    n.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    n.save()
    libraries_changed()
    assert tab.template_status()[0] == "stale"
    tick = JP.JobProgress.tick  # (Stop in the open tabs' step only)
    monkeypatch.setattr(
        JP.JobProgress,
        "tick",
        lambda self, i, total, text: self.phase != "tabs" and tick(self, i, total, text),
    )
    reports = []
    monkeypatch.setattr(MainWindow, "_report_users_update", lambda self, text, **k: reports.append(text))
    win._update_element_users_run(GU.element_users(inner, "normal@XGA"), confirm=False)
    _settle()
    lines = [x for x in reports[-1].split("\n") if x.startswith("Open in a tab")]
    assert lines == [
        "Open in a tab, left when the run was stopped. The automatic update brings its "
        "changed templates, genies and symbols up to date there now: FromX."
    ], lines
    assert tab.template_status()[0] == "current" and b"TPLNEW" in tab.serialize()


def test_the_follow_words_name_what_follows(win):
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.window.run_text import follow_words

    kinds = {"mytpl.made@XGA": "template", "fxsuper.!fx_popup@SUPER": "super genie"}
    assert follow_words(["fxnest.n1"], {}) == "genies and symbols"
    assert follow_words(["mytpl.made@XGA"], kinds) == "templates, genies and symbols"
    assert follow_words(["fxsuper.!fx_popup@SUPER"], kinds) == "super genies, genies and symbols"
    app_settings().setValue("opt/auto_update_template", False)
    app_settings().setValue("opt/auto_update_symbols", False)
    assert follow_words(["mytpl.made@XGA"], kinds) == "genies"


# -- 'always' with only open pages using the template --------------------------------------------
@pytest.mark.parametrize("apply", [False, True])
def test_always_with_only_open_pages_says_what_they_did(fx_install, win, monkeypatch, apply):
    """Apply a changed template again off: the note says what the open page did with the
    saved template."""
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_template_save_pages

    _inner, fromx = _outer_template(fx_install)
    set_template_save_pages("always")
    app_settings().setValue("opt/auto_update_template", apply)
    started = []
    monkeypatch.setattr(MainWindow, "_start_users_run", lambda self, *a: started.append(a))
    win.open_page(fromx)
    t = win.doc
    _settle()
    t.dirty = False
    lib = os.path.join(fx_install.project("FxMain"), "mytpl.ctt")
    win._open_element(lib, "made@XGA")
    win.doc.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    c = notify.centre(win)
    c.clear()
    assert win.save()
    _settle()
    assert started == []
    text = [m.text for m in c.entries if m.text.startswith("mytpl.made@XGA saved.")]
    if apply:
        assert text == [
            "mytpl.made@XGA saved. 1 page open in a tab takes the change there. "
            "Nothing else that uses it needs an update."
        ], text
        assert t.template_status()[0] == "current"
    else:
        assert text == [
            "mytpl.made@XGA saved. 1 page open in a tab is not updated there "
            "(Apply a changed template again is off). Nothing else that uses it "
            "needs an update."
        ], text
        assert t.template_status()[0] == "stale"


# -- the extras ------------------------------------------------------------------------------------
def test_the_shipped_template_question_ticks_the_chain_box(win, tmp_path, monkeypatch):
    """A shipped template's question ticks the chain box of its Update Pages, as the question
    of a template of the user's own does."""
    from ctgedit.ui.dialog import library_dialogs
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/save_template_warning", True)
    monkeypatch.setattr(library_dialogs, "shipped_library", lambda path: True)
    monkeypatch.setattr(MainWindow, "_follow_saved_template", lambda self, kind, name: 0)
    runs = []
    monkeypatch.setattr(MainWindow, "_update_pages", lambda self, **k: runs.append(k))
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "exec",
        lambda self: next(b for b in self.buttons() if b.text() == "Update Pages…").click(),
    )
    doc = type(
        "Saved",
        (),
        {
            "path": str(tmp_path / "style.ctt"),
            "display_name": "style.t",
            "save_files": [str(tmp_path / "style.ctt")],
        },
    )()
    win._template_saved_prompt(doc)
    assert runs == [{"chain": True}]


# -- version tabs and the steps the chain takes in -------------------------------------------------
def _version_of(win, page):
    """An earlier version of ``page`` open read-only (its own tab closed): the version's
    document."""
    from ctgedit.infra import history

    history.configure(5, 50)
    v = history.keep_version(page, open(page, "rb").read(), force=True)
    assert v is not None
    win.open_document(page)
    win.open_version_read_only(v)
    ver = win.doc
    win._close_doc_tab(next(k for k, e in enumerate(win._docs) if e["doc"] is not ver), ask=False)
    assert ver.read_only and ver.ui.version_view is not None
    return ver


def test_a_symbol_save_leaves_a_version_tab(fx_install, win):
    """The save's own update of the open pages (_propagate_symbol) leaves an earlier version
    of a page that places the symbol (a read-only view) as it is."""
    ver = _version_of(win, fx_install.page("FxSymbols"))
    before = _payload(ver)
    win._open_element(fx_install.file("FxLib", "fxsym.ctl"), "fx_on")
    win.doc.move_objects([0], 3, 2)
    assert win.save()
    _settle()
    assert _payload(ver) == before and not ver.dirty and ver.read_only


def test_a_genie_save_leaves_a_read_only_page_tab(fx_install, win, monkeypatch):
    """The genie save's update of the open pages (_propagate_element) leaves an earlier
    version's genies as they are; the page's own tab still takes them."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxGenies")
    ver = _version_of(win, page)
    win.open_document(page)
    own = win.doc
    seen = []
    orig = EditableDocument.update_genie_instances
    monkeypatch.setattr(
        EditableDocument,
        "update_genie_instances",
        lambda self, *a, **k: seen.append(self) or orig(self, *a, **k),
    )
    win._open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    i = next(k for k, o in enumerate(win.doc.parsed.page.objects) if o.type_code == 0x05)
    win.doc.move_object(i, 4, 6)
    assert win.save()
    _settle()
    assert not any(d is ver for d in seen) and any(d is own for d in seen)
    own.dirty = False


def test_the_chains_end_makes_a_kept_link_table_step_part_of_the_saves_step(fx_install, win, monkeypatch):
    """Kept, the save's template step in a page of the outer template and the chain's end step
    that changes only the link table are one step: one Undo takes the template back, and Redo
    brings it with the outer template's new stamp."""
    queued = _template_always(win, monkeypatch)
    inner, fromx = _outer_template(fx_install)
    win.open_page(fromx)
    p = win.doc
    _settle()
    p.dirty = False
    steps = len(p._undo)
    _save_inner(win, inner)
    assert len(p._undo) == steps + 1
    assert win._start_users_run(*queued[0])
    _settle()
    assert p.template_status()[0] == "current" and len(p._undo) == steps + 1
    assert [w for w, _c in p.ui.auto_changes] == ["template mytpl.made@XGA re-applied"]
    assert p.ui.auto_changes[0][1] is p._undo[-1]
    win.doc_tabs.setCurrentIndex(next(k for k, e in enumerate(win._docs) if e["doc"] is p))
    _settle()
    win.undo()
    assert p.template_status()[0] == "stale" and b"TPLNEW" not in p.serialize()
    assert not p.dirty
    win.redo()
    assert p.template_status()[0] == "current" and b"TPLNEW" in p.serialize()
    p.dirty = False


def test_the_template_step_that_took_in_the_chains_step_stays_the_automatic_one(fx_install, win, monkeypatch):
    """The merged template step stays the automatic one."""
    import types
    from ctgedit.ui.simulate import window as SW

    queued = _template_always(win, monkeypatch)
    inner, fromx = _outer_template(fx_install)
    win.open_page(fromx)
    p = win.doc
    _settle()
    p.dirty = False
    _save_inner(win, inner)
    first = p._undo[-1]
    sim = types.SimpleNamespace(doc=p, doc_steps=SW._doc_steps(p))
    assert win._start_users_run(*queued[0])
    _settle()
    assert p.template_status()[0] == "current"
    assert p._undo[-1] is first and first.automatic, "the save's step itself"
    assert SW._auto_only(sim), "the editor's own update for the Simulate window"
    win.doc_tabs.setCurrentIndex(next(k for k, e in enumerate(win._docs) if e["doc"] is p))
    _settle()
    win.undo()
    assert not win._user_can_redo(p), "an automatic update undone, no step of the user's"
    p.dirty = False


def test_merge_last_steps_keeps_a_saved_state_reachable(fx_install):
    from ctgedit.editing import EditableDocument

    d = EditableDocument.open(fx_install.page("FxGenies"))
    d.move_objects([0], 1, 0)
    d.move_objects([0], 1, 0)
    one = d.merge_last_steps(2)
    assert one is not None and len(d._undo) == 1 and one.label == "move"
    d.undo()
    assert not d.dirty
    d.redo()
    d.move_objects([0], 1, 0)
    d.mark_saved()  # (the state between the two steps is saved)
    d.move_objects([0], 1, 0)
    assert d.merge_last_steps(2) is None and len(d._undo) == 3


@pytest.mark.parametrize("how", ["chain", "gui"])
def test_a_super_genie_view_reads_the_file_a_run_updated(fx_install, win, how):
    """A super genie page opened to view is a closed page for the runs (O5-2): its file is
    updated, and the tab reads the file again, still a read-only view, with no 'changed by
    another program' notice."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.editor import notices
    from ctgedit.ui import notify

    page = os.path.join(fx_install.project("FxMain"), "!fx_detail.ctg")
    view = win._open_page_file(page, checks=False)
    view.read_only = True
    view.ui.supergenie_view = True
    win.set_document(view)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    c = notify.centre(win)
    c.clear()
    _run(win, how, lib, "fx_on")
    win._check_disk_changes([page])  # (the file watch)
    _settle()
    tab = win._docs[0]["doc"]
    assert tab is not view and tab.read_only and tab.ui.supergenie_view
    assert not tab.changed_on_disk() and notices.get(tab, "disk") is None
    assert not any("another program" in m.text for m in c.entries), [m.text for m in c.entries]


def test_a_read_only_view_waits_for_the_runs_end_to_be_told(fx_install, win):
    """While a run goes on, a read-only view whose file changed is looked at again after it
    (the run's end reads it again), as an element tab the chain may save is."""
    from ctgedit.ui.editor import notices
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    page = fx_install.page("FxGenies")
    view = win._open_page_file(page, checks=False)
    view.read_only = True
    win.set_document(view)
    view._disk_stamp = (1, 1)  # (its file changed since it was read)
    UpdatePagesRun.users_busy = "fxnest.n0"
    win._check_disk_changes([page])
    assert notices.get(view, "disk") is None and page in win._disk_pending
    UpdatePagesRun.users_busy = ""
    win._check_disk_changes([page])
    assert notices.get(view, "disk") is not None


def test_update_template_here_names_the_template_as_its_library_spells_it(fx_install, win):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui import notify

    win.open_document(fx_install.page("FxTplLast"))
    page = win.doc
    n = ElementDocument.open_element(fx_install.file("FxStyle", "fx_style.ctt"), "normal@XGA")
    n.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    n.save()
    libraries_changed()
    c = notify.centre(win)
    c.clear()
    win._update_template_here()
    win._update_template_here()
    texts = [m.text for m in c.entries]
    assert texts and all(".<ctt" not in t for t in texts), texts
    assert "The page already matches its template fx_style.normal@XGA." in texts
    page.dirty = False


def test_the_worker_template_step_names_the_template(win):
    """The status bar names the template while a stale page's template is applied in the
    worker."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.__new__(EditableDocument)
    win.doc = doc
    had = win.__dict__.get("_genie_jobs")
    win._genie_jobs = type("J", (), {"last_started": type("T", (), {"template": True})()})()
    try:
        win._genie_job_started(doc, ["fx_style.normal@XGA.<ctt"])
    finally:
        win._genie_jobs = had
        win.doc = None
    assert (
        win.statusBar().currentMessage()
        == "Applying the template fx_style.normal@XGA again (you can go on working)…"
    )


def test_cancel_in_the_open_tabs_step_names_what_reaches_each_page(fx_install, win, monkeypatch):
    """Cancel in the open tabs' step of Update Users of normal@XGA, FxTplLast (linked to it)
    and FromX (linked to a template built on it, which the run saved again) open, both out
    of date: the line says what reaches each page (only FromX's template was saved by the
    run)."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.jobs import job_progress as JP
    from ctgedit.ui.main_window import MainWindow

    inner, fromx = _outer_template(fx_install)
    win.open_document(fx_install.page("FxTplLast"))
    last = win.doc
    win.open_document(fromx)
    outer = win.doc
    _settle()
    n = ElementDocument.open_element(inner, "normal@XGA")
    n.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    n.save()
    libraries_changed()
    for d in (last, outer):
        d.dirty = False
        assert d.template_status()[0] == "stale"
    tick = JP.JobProgress.tick  # (Stop in the open tabs' step only)
    monkeypatch.setattr(
        JP.JobProgress,
        "tick",
        lambda self, i, total, text: self.phase != "tabs" and tick(self, i, total, text),
    )
    reports = []
    monkeypatch.setattr(MainWindow, "_report_users_update", lambda self, text, **k: reports.append(text))
    win._update_element_users_run(GU.element_users(inner, "normal@XGA"), confirm=False)
    _settle()
    lines = sorted(x for x in reports[-1].split("\n") if x.startswith("Open in a tab"))
    assert lines == [
        "Open in a tab, left when the run was stopped. The automatic update brings its "
        "changed genies and symbols up to date there now, not its template (Tools > Update "
        "Template on This Page does it): FxTplLast.",
        "Open in a tab, left when the run was stopped. The automatic update brings its "
        "changed templates, genies and symbols up to date there now: FromX.",
    ], lines
    assert last.template_status()[0] == "stale" and outer.template_status()[0] == "current"
