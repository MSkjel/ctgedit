"""Run reports and prompts after a library save."""

from ctgedit.ui.window import open_tabs as open_tabs_mod
import json
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _make_win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


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
    w = _make_win()
    yield w
    _drop_win(w)


@pytest.fixture()
def win2():
    w = _make_win()
    yield w
    _drop_win(w)


@pytest.fixture(autouse=True)
def _quiet_runs():
    """The save prompts off; the run holds as they were after each test."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    st = app_settings()
    for k, v in (
        ("opt/genie_save_pages", "never"),
        ("opt/symbol_save_pages", "never"),
        ("opt/template_save_pages", "never"),
        ("opt/auto_update_genies", True),
    ):
        st.setValue(k, v)
    UpdatePagesRun.used_by_due = False
    yield


def _settle():
    for _ in range(4):
        _app().processEvents()


def _move_text(lib: str, element: str) -> None:
    """``element``'s first text moved and saved (its users are out of date)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(lib, element)
    t = next(i for i, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.move_object(t, 3, 0)
    d.save()
    libraries_changed()


def _used_by(win, lib: str, element: str):
    win._show_used_by(lib, element)
    key = ("usedby", os.path.normcase(os.path.abspath(lib)), element.lower())
    return win._dialogs[key]


def _stale(dlg) -> int:
    return sum(1 for r in dlg.users() if r[1] != "Up to date")


# -- an open Used By window follows -----------------------------------------------------------
def test_show_users_reads_an_open_used_by_window_again(fx_install, win):
    """The Genie Saved question's Show Users reads an open Used By window's statuses again."""
    gen = fx_install.file("FxLib", "fxgenie.ctm")
    dlg = _used_by(win, gen, "fx_value")
    assert _stale(dlg) == 0 and not dlg._btn_update.isEnabled()
    _move_text(gen, "fx_value")
    win._show_used_by(gen, "fx_value")
    assert _used_by(win, gen, "fx_value") is dlg  # (one window per element)
    assert _stale(dlg) == len(dlg.users()) > 0 and dlg._btn_update.isEnabled()
    dlg.close()


def test_an_open_used_by_window_follows_a_genie_save_and_the_chain(fx_install, win):
    gen = fx_install.file("FxLib", "fxgenie.ctm")
    dlg = _used_by(win, gen, "fx_value")
    dlg.show()
    assert _stale(dlg) == 0
    win._open_element(gen, "fx_value")
    d = win.doc
    t = next(i for i, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.move_object(t, 3, 0)
    assert win.save()
    _settle()
    n = len(dlg.users())
    assert _stale(dlg) == n > 0, dlg.users()
    # (the chain after the save: inline, no event loop)
    assert win._start_users_run(gen, "fx_value", "fxgenie.fx_value")
    _settle()
    from ctgedit.io import genieusage as GU

    fresh = GU.element_users(gen, "fx_value")
    assert _stale(dlg) == sum(1 for u in fresh.users if u.needs_update) < n
    dlg.close()


def test_the_used_by_window_waits_while_a_run_writes(fx_install, win):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    gen = fx_install.file("FxLib", "fxgenie.ctm")
    dlg = _used_by(win, gen, "fx_value")
    dlg.show()
    _move_text(gen, "fx_value")
    UpdatePagesRun.users_busy = "fxgenie.fx_value"
    win._follow_used_by_windows()
    _settle()
    assert dlg.stale and _stale(dlg) == 0  # (marked; read when the run ends)
    UpdatePagesRun.users_busy = ""
    win._follow_used_by_windows()

    open_tabs_mod.refresh_stale_used_by()
    assert not dlg.stale and _stale(dlg) > 0
    dlg.close()


def test_the_used_by_window_follows_a_library_another_program_saved(fx_install, win):
    from ctgedit.ui.window import libwatch

    gen = fx_install.file("FxLib", "fxgenie.ctm")
    dlg = _used_by(win, gen, "fx_value")
    dlg.show()
    _move_text(gen, "fx_value")  # (as the Graphics Builder saves it)
    libwatch.libraries_changed_on_disk([gen], first=win)
    _settle()
    assert not dlg.stale and _stale(dlg) == len(dlg.users()) > 0
    dlg.close()


def test_used_by_update_reads_the_users_again_first(app_like=None):
    _app()
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

    target = GU.Ref("element", "C:\\P\\lib.ctm", "e1", "P")
    page = GU.Ref("page", "C:\\P\\a.ctg", "", "P")
    old = GU.Usage(target, [GU.User(page, [], (), True, GU.OUT_OF_DATE)])
    new = GU.Usage(target, [GU.User(page, [], (), True, GU.UP_TO_DATE)])
    got = []
    dlg = ElementUsersDialog("C:\\P\\lib.ctm", "e1", old, on_update=got.append, on_refresh=lambda: new)
    dlg._update()
    assert got == [new] and dlg.usage is new
    got.clear()
    dlg = ElementUsersDialog("C:\\P\\lib.ctm", "e1", old, on_update=got.append, on_refresh=lambda: None)
    dlg._update()  # (the read cancelled: no update)
    assert got == []


def test_update_is_off_when_only_a_page_not_registered_is_out_of_date():
    _app()
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

    target = GU.Ref("element", "C:\\P\\lib.ctm", "e1", "P")
    page = GU.Ref("page", "C:\\P\\a.ctg", "", "P")
    lone = GU.Usage(target, [GU.User(page, [], (), True, GU.OUT_OF_DATE, registered=False)])
    dlg = ElementUsersDialog("C:\\P\\lib.ctm", "e1", lone, on_update=lambda u: None)
    assert not dlg._btn_update.isEnabled()
    dlg.set_usage(GU.Usage(target, [GU.User(page, [], (), True, GU.OUT_OF_DATE, registered=True)]))
    assert dlg._btn_update.isEnabled()


def test_update_users_of_only_pages_not_registered_says_they_are_left(win, monkeypatch):
    from ctgedit.io import genieusage as GU

    target = GU.Ref("element", "C:\\P\\lib.ctm", "e1", "P")
    page = GU.Ref("page", "C:\\P\\FxLone.ctg", "", "P")
    usage = GU.Usage(target, [GU.User(page, [], (), True, GU.OUT_OF_DATE, registered=False)])
    said = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda parent, title, text, *a: said.append(text)
    )
    ran = []
    monkeypatch.setattr(GU, "run_update", lambda *a, **k: ran.append(1))
    assert win._update_element_users_run(usage, confirm=False) is None
    assert not ran
    assert said == [
        "Nothing that uses lib.e1 can be updated here. Left alone: FxLone (not registered in its project)."
    ], said


# -- the pages a run leaves out of date ------------------------------------------------------
def _chain_with(win, monkeypatch, answer: dict, name="fxgenie.fx_pump", lib=None):
    """The chain after a library save of ``name`` run inline with the helper's answer faked:
    the note in the window's messages."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui import notify

    def fake(lib_path, element, *a, **k):
        return dict(
            {
                "format": GW.FORMAT,
                "pages": [],
                "updated": [],
                "failed": [],
                "open_in_builder": [],
                "unregistered": [],
                "open_at_start": [],
                "problems": [],
                "elements_saved": [],
                "elements_in_editor": [],
            },
            **answer,
        )

    monkeypatch.setattr(GW, "update_closed_pages", fake)
    c = notify.centre(win)
    c.clear()
    lib = lib or "C:\\P\\%s.ctm" % name.split(".")[0]
    assert win._start_users_run(lib, name.split(".", 1)[1], name)
    return c.entries[-1]


def test_the_chain_names_the_pages_it_left_out_of_date_as_a_warning(win, monkeypatch):
    m = _chain_with(win, monkeypatch, {"db_in_use": ["FxBig", "FxGenies"], "unregistered": ["FxMany"]})
    assert m.level == "warning", m.text
    assert m.text == (
        "Updated 0 closed pages that use fxgenie.fx_pump. 2 closed pages were "
        "left out of date (Details says why). 1 page not registered in its "
        "project was left alone."
    ), m.text
    for key in ("open_in_builder", "changed_meanwhile", "unchanged"):
        m = _chain_with(win, monkeypatch, {key: ["FxBig"], "updated": ["FxGenies"]})
        assert m.level == "warning" and " 1 closed page was left out of date (Details says why)." in m.text, (
            key,
            m.text,
        )
    m = _chain_with(win, monkeypatch, {"updated": ["FxGenies"], "rows_pending": ["FxGenies"]})
    assert (
        m.level == "warning"
        and "1 page was saved, but its project database rows are not written yet (Details says which)."
        in m.text
    ), m.text
    m = _chain_with(
        win,
        monkeypatch,
        {
            "elements_saved": [],
            "updated": ["FxGenies"],
            "elements_skipped": ["mylib.x: it has unsaved changes in the editor"],
        },
    )
    assert m.level == "warning", m.text
    m = _chain_with(win, monkeypatch, {"updated": ["FxGenies"], "unregistered": ["FxMany"]})
    assert m.level == "success", m.text


def test_the_result_lines_of_pages_left_out_of_date_make_a_warning(win, monkeypatch):
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import run_results
    from ctgedit.ui.main_window import MainWindow as L

    monkeypatch.setattr(run_results.RunResultsDialog, "exec", lambda self: 0)
    for line in (
        "Not updated because the project database is in use (is Plant SCADA Studio open?): FxBig.",
        "Skipped because they are open in the Graphics Builder: FxBig",
        "Changed by another save while being updated. Run Update Users again: FxBig",
        "Out of date, but nothing on them could be updated (the log says why): FxBig.",
        "Not updated, because they were open in an editor during the run and closed "
        "before it ended. Run Update Pages again: FxBig.",
        "Open in a tab, not updated because the run was stopped: FxBig.",
        "Saved, but not registered in their project yet (the project database could "
        "not be written). The next Update Pages run registers them: FxBig.",
        "1 composite genie left as it was: its template is not in the project or the projects it includes.",
        "\nFailed:\nFxBig: denied.",
    ):
        assert L._run_level(["6 pages checked, 6 out of date, 0 updated.", line]) == "warning", line
    assert (
        L._run_level(
            [
                "6 pages checked, 6 out of date, 6 updated.",
                "1 page file not registered in its project was left alone.",
            ]
        )
        == "success"
    )
    c = notify.centre(win)
    c.clear()
    win._show_run_results(
        "Update Users",
        [
            "6 pages checked, 6 out of date, 0 updated.",
            "Not updated because the project database is in use (is Plant SCADA Studio open?): FxBig.",
        ],
    )
    assert c.entries[-1].level == "warning"


# -- a template save reaches the templates built on it ---------------------------------------
def _outer_template(fx) -> tuple:
    """mytpl.made@XGA: Copy to Library of FxTplLinked's template objects (it holds
    fx_style.normal@XGA's copy); FromX: a page linked to it. (inner template file, the outer's
    library, FromX)."""
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
    return fx.file("FxStyle", "fx_style.ctt"), lib, page


def _save_inner_template(win, inner: str) -> None:
    win._open_element(inner, "normal@XGA")
    win.doc.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    assert win.save()


def test_a_template_save_reaches_the_templates_built_on_it_and_their_pages(fx_install, win, monkeypatch):
    """'Templates and closed pages that use a saved template': always. The chain saves the
    outer template, then the pages linked to it and to the inner one; an open page linked to
    the inner one takes it in its tab."""
    from ctgedit.io import updatepages as UP
    from ctgedit.ui import notify
    from ctgedit.ui.prefs.settings import set_template_save_pages

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    inner, _lib, page = _outer_template(fx_install)
    set_template_save_pages("always")
    win.open_page(fx_install.page("FxTplLast"))
    open_doc = win.doc
    assert open_doc.template_status()[0] == "current"
    c = notify.centre(win)
    _save_inner_template(win, inner)
    assert b"TPLNEW" in open(page, "rb").read() and not UP.page_needs_update(page)
    assert open_doc.template_status()[0] == "current" and open_doc.dirty
    texts = [m.text for m in c.entries]
    assert any(
        t.startswith("Updated 1 template and ") and t.endswith("1 page open in a tab takes the change there.")
        for t in texts
    ), texts


def test_the_template_saved_question_offers_the_chain_and_update_pages(fx_install, win, monkeypatch):
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_template_save_pages

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    inner, _lib, _page = _outer_template(fx_install)
    set_template_save_pages("ask")
    asked, runs = [], []

    def prompt(box):
        asked.append((box.windowTitle(), box.text(), sorted(b.text() for b in box.buttons())))
        next(b for b in box.buttons() if b.text() == "Update Pages…").click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(prompt))
    monkeypatch.setattr(MainWindow, "_update_pages", lambda self, **k: runs.append(k))
    _save_inner_template(win, inner)
    assert asked == [
        (
            "Template Saved",
            "1 template and 3 closed pages use fx_style.normal@XGA and still show its old version.",
            ["Later", "Show Users…", "Update Pages…", "Update in Background"],
        )
    ], asked
    assert runs == [{"chain": True}]
    # (the Graphics Builder's Save template warning off: nothing is asked)
    asked.clear()
    app_settings().setValue("opt/save_template_warning", False)
    _save_inner_template(win, inner)
    assert asked == [] and runs == [{"chain": True}]


def test_update_pages_chain_box_goes_with_the_templates_and_a_given_state(tmp_path):
    _app()
    from test_update_pages_dialog import _dlg
    from test_update_pages_parts import tmp_install
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    dlg = _dlg(user)
    try:
        dlg._chain.setChecked(True)
        dlg._gen.setChecked(False)
        assert dlg._chain.isEnabled() and dlg.options()["chain"] is True
    finally:
        dlg.deleteLater()
    app_settings().setValue("updatepages/chain", False)
    dlg = UpdatePagesDialog(
        None, None, projects=[(n, os.path.join(user, n)) for n in ("Root", "A", "B")], chain=True
    )
    try:
        assert dlg._chain.isChecked() and dlg.options()["chain"] is True
        dlg.accept()
    finally:
        dlg.deleteLater()
    # (a state given for one run is not remembered)
    assert app_settings().value("updatepages/chain") in (False, "false")


# -- 'always' with nothing to update -----------------------------------------------------------
def test_always_with_nothing_to_update_starts_no_helper(fx_install, win, monkeypatch):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui import notify
    from ctgedit.ui.prefs.settings import set_genie_save_pages

    set_genie_save_pages("always")
    mine = os.path.join(fx_install.project("FxLib"), "mylib.ctm")
    d = ElementDocument.new_element(mine, "lonely", "genie")
    d.create_object("text", (0, 0, 80, 16), text="L")
    d.save()
    libraries_changed()
    started = []
    monkeypatch.setattr(type(win), "_start_users_run", lambda self, *a: started.append(a))
    c = notify.centre(win)
    c.clear()
    win._users_saved_prompt(d)
    assert started == []
    assert [(m.level, m.text) for m in c.entries] == [
        ("info", "mylib.lonely saved. Nothing that uses it needs an update.")
    ]


def test_a_chain_that_found_nothing_says_so(win):
    text = win._users_run_finished(
        {"updated": [], "failed": [], "libraries": [], "tabs_saved": []}, "mylib.lonely"
    )
    assert text == "Nothing that uses mylib.lonely needed an update.", text


# -- Cancel in the open tabs' step --------------------------------------------------------------
def _users_run_left_a_tab(win, monkeypatch, fx, saved):
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    users = [
        types.SimpleNamespace(
            ref=types.SimpleNamespace(label="FxGenies", path=fx.page("FxGenies"), kind="page"),
            needs_update=True,
            links=[],
            registered=True,
        )
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan([], users))
    monkeypatch.setattr(
        GU,
        "run_update",
        lambda plan, **k: GU.UpdateUsersReport(
            elements_saved=list(saved), libraries_written=[fx.file("FxLib", "fxnest.ctm")] if saved else []
        ),
    )
    monkeypatch.setattr(
        type(win),
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: OpenTabsUpdate(left=[fx.page("FxGenies")]),
    )
    followed = []
    monkeypatch.setattr(type(win), "_queue_open_genie_updates", lambda self: followed.append(1))
    reports = []
    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: reports.append(text))
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fxnest.n0"))
    win._update_element_users(usage, confirm=False)
    return [x for x in reports[-1].split("\n") if x.startswith("Open in a tab")], followed


def test_cancel_in_the_open_tabs_step_says_what_the_automatic_update_does(fx_install, win, monkeypatch):
    lines, followed = _users_run_left_a_tab(win, monkeypatch, fx_install, ["fxnest.n1"])
    assert followed and lines == [
        "Open in a tab, left when the run was stopped. The automatic update brings its "
        "changed genies and symbols up to date there now: FxGenies."
    ], lines
    lines, followed = _users_run_left_a_tab(win, monkeypatch, fx_install, [])
    assert not followed and lines == ["Open in a tab, not updated because the run was stopped: FxGenies."], (
        lines
    )


# -- things named by their kind ------------------------------------------------------------------
def _edit_value_text(fx) -> str:
    from ctgedit.editing import ElementDocument, libraries_changed

    gen = fx.file("FxLib", "fxgenie.ctm")
    g = ElementDocument.open_element(gen, "fx_value")
    t = next(
        i
        for i, o in enumerate(g.page.objects)
        if o.parent is None and g.parsed.page.objects[i].type_code == 0x05
    )
    assert g.set_text(t, "%Description% EDITED")
    g.save()
    libraries_changed()
    return gen


def test_a_dropped_label_entry_is_no_link_to_a_genie(fx_install):
    """fx_value's text stops being a language label: the pages drop their '\\\\Fx Start' entry.
    That is no 'link to a genie no longer on the page'."""
    from ctgedit.io import genieusage as GU

    gen = _edit_value_text(fx_install)
    rep = GU.run_update(GU.update_plan(GU.element_users(gen, "fx_value")))
    pr = rep.pages
    assert pr.updated and pr.links_dropped == [], pr.links_dropped
    recs = [r for r in pr.records if r.get("outcome") == "updated"]
    assert all(
        r["labels_dropped"] == 1
        and r["links_dropped"] == 0
        and r["detail"].startswith("labels removed: \\\\Fx ")
        for r in recs
    ), recs


def test_nested_copies_are_spelled_as_their_import_table_and_a_template_is_named_so():
    from ctgedit.io import genieusage as GU

    doc = types.SimpleNamespace(
        _link_entries_now=lambda: (
            "    ",
            [
                (b"", "fx_style.normal@XGA.<ctt#0000abcd"),
                (b"", "fxnest.N0.<ctm#00000001"),
                (b"", "\\\\Fx Start"),
            ],
        )
    )
    assert GU.nested_copy_labels(doc, {("fx_style", "normal@xga"), ("fxnest", "n0"), ("fxa", "b")}) == [
        "the template fx_style.normal@XGA",
        "fxa.b",
        "fxnest.N0",
    ]


def test_a_template_copy_left_is_named_as_a_template_spelled_as_its_library(fx_install, monkeypatch):
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import template_place as TP
    from ctgedit.io import genieusage as GU

    inner, _lib, _page = _outer_template(fx_install)
    n = ElementDocument.open_element(inner, "normal@XGA")
    n.create_object("rectangle", (300, 300, 400, 360))
    n.save()
    libraries_changed()

    def fail(*_a, **_k):
        raise ValueError("cannot compose")

    monkeypatch.setattr(TP, "_compose_copy", fail)
    rep = GU.run_update(GU.UpdatePlan(GU.update_plan(GU.element_users(inner, "normal@XGA")).elements, []))
    assert (
        "mytpl.made@XGA: its copy of the template fx_style.normal@XGA could not be "
        "composed again" in rep.skipped
    ), rep.skipped


def test_the_results_call_them_nested_copies(win):
    from ctgedit.ui.main_window import MainWindow as L

    left = [("mytpl.made@XGA", ["the template fx_style.normal@XGA"])]
    assert L._not_composed_text(left) == (
        "Nested copies that could not be composed again and stay as stored: mytpl.made@XGA "
        "(its copy of the template fx_style.normal@XGA)."
    )
    res = {
        "elements_saved": ["mytpl.made@XGA"],
        "element_kinds": {"mytpl.made@XGA": "template"},
        "not_composed": left,
        "updated": [],
        "failed": [],
        "libraries": [],
        "tabs_saved": [],
    }
    text = win._users_run_finished(res, "fx_style.normal@XGA", "template")
    assert " 1 nested copy could not be composed again and stays as stored" in text, text
    assert (
        "Nested copies that could not be composed again and stay as stored:\n  "
        "mytpl.made@XGA (its copy of the template fx_style.normal@XGA)" in win._users_run_details(res)
    )
    assert "Saved without the links to library elements no longer on the page" in win._users_run_details(
        dict(res, links_dropped=["FxGenies"])
    )


def test_two_pages_of_one_name_are_told_apart_by_their_project(fx_install):
    """fxsym.fx_on is placed on !fx_detail of FxMain and on !fx_detail of FxSim: the chain's
    Details tell the two apart by their project."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    res = GW.update_closed_pages(lib, "fx_on")
    assert "!fx_detail (FxMain)" in res["updated"] and "!fx_detail (FxSim)" in res["updated"]
    assert "!fx_detail" not in res["updated"] and "FxSymbols" in res["updated"]


def test_same_name_label_and_relabelled():
    from ctgedit.io import updatepages as UP

    paths = ["C:\\U\\A\\p.ctg", "C:\\U\\B\\p.ctg", "C:\\U\\A\\q.ctg"]
    label = UP.same_name_label(paths)
    assert [label(UP.page_record(0, p, "updated")) for p in paths] == ["p (A)", "p (B)", "q"]
    rep = UP.UpdateReport()
    for i, p in enumerate(paths):
        rep.add(UP.page_record(i, p, "updated"))
    rep.unregistered, rep.unregistered_paths = ["r", "r"], ["C:\\U\\A\\r.ctg", "C:\\U\\B\\r.ctg"]
    out = UP.relabelled(rep, paths)
    assert out.updated == ["p (A)", "p (B)", "q"] and out.unregistered == ["r (A)", "r (B)"]


# -- the extras ------------------------------------------------------------------------------
def test_a_super_genie_page_the_chains_helper_creates_gets_a_thumbnail(fx_install, monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import genieusage as GU
    from ctgedit.render import headless_thumb

    _move_text(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    seen = []
    real = GU.run_update

    def spy(plan, **k):
        seen.append(k.get("page_thumbnail"))
        return real(plan, **k)

    monkeypatch.setattr(GU, "run_update", spy)
    GW.update_closed_pages(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    assert seen and seen[0] is headless_thumb.page_thumbnail


def test_the_result_of_a_chain_whose_window_closed_is_told_in_another(win, win2, monkeypatch):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui import notify
    from ctgedit.ui.main_window import MainWindow

    def fake(lib_path, element, *a, **k):
        MainWindow.instances.remove(win2)  # (closed while it ran)
        return {
            "format": GW.FORMAT,
            "updated": ["FxGenies"],
            "failed": [],
            "pages": [],
            "libraries": [],
            "tabs_saved": [],
        }

    monkeypatch.setattr(GW, "update_closed_pages", fake)
    notify.centre(win).clear()
    notify.centre(win2).clear()
    assert win2._start_users_run("C:\\P\\fxgenie.ctm", "fx_pump", "fxgenie.fx_pump")
    assert [m.text for m in notify.centre(win).entries] == [
        "Updated 1 closed page that uses fxgenie.fx_pump."
    ]
    assert not notify.centre(win2).entries
    MainWindow.instances.append(win2)  # (for the fixture's cleanup)


def test_the_chains_note_counts_the_pages_it_leaves_to_their_tabs(fx_install, win):
    from ctgedit.ui.prefs.settings import app_settings

    page = fx_install.page("FxGenies")
    win.open_document(page)
    res = {"updated": ["FxBig"], "failed": [], "libraries": [], "tabs_saved": [], "open_at_start": [page]}
    assert win._users_run_finished(dict(res), "fxgenie.fx_pump").endswith(
        "that uses fxgenie.fx_pump. 1 page open in a tab takes the change there."
    )
    # (with a setting off the save still updates what the page places directly,
    # _propagate_element, and a symbol's placements whatever Update changed symbols says)
    app_settings().setValue("opt/auto_update_symbols", False)
    assert win._users_run_finished(dict(res), "fxsym.fx_on", "symbol").endswith(
        " 1 page open in a tab takes the change there."
    )
    app_settings().setValue("opt/auto_update_genies", False)
    assert win._users_run_finished(dict(res), "fxgenie.fx_pump").endswith(
        " 1 page open in a tab takes it there only where it places it directly (Update "
        "changed genies is off)."
    )
    app_settings().setValue("opt/auto_update_template", False)
    assert win._users_run_finished(dict(res), "fx_style.normal@XGA", "template").endswith(
        " 1 page open in a tab is not updated there (Apply a changed template again is off)."
    )


def test_the_pages_phase_writes_its_partial_answer_once(fx_install, tmp_path, monkeypatch):
    """The records file holds each page as it is done (the editor folds it when the helper
    dies); the answer is written before the pages and, on an error, once more."""
    from ctgedit.workers import genie_worker as GW

    _move_text(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump")
    writes = []
    real = GW.write_json

    def count(path, obj):
        if path.endswith(GW.GENIES_SUFFIX) and obj.get("pages"):
            writes.append(len(obj.get("updated") or ()))
        return real(path, obj)

    monkeypatch.setattr(GW, "write_json", count)
    prog = str(tmp_path / "run.progress")
    res = GW.update_closed_pages(fx_install.file("FxLib", "fxgenie.ctm"), "fx_pump", progress_file=prog)
    assert len(res["updated"]) > 1 and writes == [0], (writes, res["updated"])
    with open(prog + GW.RECORDS_SUFFIX, encoding="utf-8") as fh:
        assert sum(1 for line in fh if line.strip()) == len(res["pages"]) - len(res["unregistered"])
    # (and a helper that dies: the editor folds the records)
    part = GW.chain_partial_answer({"error": "no answer"}, prog)
    assert sorted(part["updated"]) == sorted(res["updated"])
    assert json.loads(open(prog + GW.GENIES_SUFFIX, encoding="utf-8").read())["pages"]


def test_restore_defaults_puts_the_template_setting_back(win, monkeypatch):
    """Settings > Restore Defaults resets 'Templates and closed pages that use a saved
    template'."""
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import set_template_save_pages, template_save_pages

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    set_template_save_pages("always")
    dlg = SettingsDialog(win)
    try:
        assert dlg._template_pages.currentData() == "always"
        dlg.restore_defaults()
        assert dlg._template_pages.currentData() == "ask"
        dlg._apply()
        assert template_save_pages() == "ask"
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("how", ["never", "later", "setting off"])
def test_open_pages_take_a_saved_template_at_once(fx_install, win, monkeypatch, how):
    """An open page linked to the saved template takes it at the save, as a genie save
    updates the open pages that place it, also with 'never', with Later, and when nothing
    closed needs an update. With 'Apply a changed template again' off it stays as it is."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.prefs.settings import app_settings, set_template_save_pages

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    started, asked = [], []
    monkeypatch.setattr(MainWindow, "_start_users_run", lambda self, *a: started.append(a))

    def later(box):
        asked.append(box.informativeText())
        next(b for b in box.buttons() if b.text() == "Later").click()

    monkeypatch.setattr(MainWindow, "_exec_prompt", staticmethod(later))
    set_template_save_pages("ask" if how == "later" else "never")
    if how == "setting off":
        app_settings().setValue("opt/auto_update_template", False)
    win.open_page(fx_install.page("FxTplLast"))
    open_doc = win.doc
    assert open_doc.template_status()[0] == "current" and not open_doc.dirty
    _save_inner_template(win, fx_install.file("FxStyle", "fx_style.ctt"))
    assert started == []
    if how == "setting off":
        assert open_doc.template_status()[0] == "stale" and not open_doc.dirty
        return
    assert open_doc.template_status()[0] == "current" and open_doc.dirty
    if how == "later":
        assert len(asked) == 1 and "Open pages are updated in their tabs." in asked[0], asked


def test_a_template_save_that_wrote_nothing_asks_nothing(win, tmp_path, monkeypatch):
    from ctgedit.ui.main_window import MainWindow

    calls = []
    monkeypatch.setattr(MainWindow, "_users_saved_prompt", lambda self, d: calls.append("question"))
    monkeypatch.setattr(
        MainWindow, "_follow_saved_template", lambda self, kind, name: calls.append(("tabs", kind, name))
    )
    doc = types.SimpleNamespace(path=str(tmp_path / "mytpl.ctt"), display_name="mytpl.t", save_files=[])
    win._template_saved_prompt(doc)
    assert calls == []
    doc.save_files = [doc.path]
    win._template_saved_prompt(doc)
    assert calls == [("tabs", "template", "mytpl.t"), "question"]


def test_update_pages_names_the_pages_closed_meanwhile_with_their_project(tmp_path, monkeypatch):
    """Its other lines name a page with its project in a run over several projects, also the
    pages closed during the run."""
    _app()
    import test_update_pages_chain as UPC
    from ctgedit.io import updatepages

    pa = os.path.join(str(tmp_path), "A", "Dup.ctg")
    pb = os.path.join(str(tmp_path), "B", "Dup.ctg")
    fake = UPC._fake_report

    def report():
        rep = fake()
        rep.projects = ["A", "B"]
        rep.open_at_start = [pa]
        rep.records = [{"outcome": "skipped_open", "path": pb, "name": "Dup", "project": "B"}]
        return rep

    monkeypatch.setattr(UPC, "_fake_report", report)
    monkeypatch.setattr(updatepages, "page_needs_update", lambda p: True)
    _calls, shown, _w = UPC._run(tmp_path, monkeypatch, False)
    assert "Run Update Pages again: Dup (A), Dup (B)." in shown[-1], shown[-1]


def test_the_chain_names_a_page_with_its_project_also_beside_an_open_one(fx_install, win):
    """!fx_detail of FxMain is open in a tab, !fx_detail of FxSim is closed: the chain's answer
    names the closed one, and one closed meanwhile, with its project."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx_install.file("FxLib", "fxsym.ctl")
    d = ElementDocument.open_element(lib, "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    main = os.path.join(fx_install.project("FxMain"), "!fx_detail.ctg")
    sim = os.path.join(fx_install.project("FxSim"), "!fx_detail.ctg")
    res = GW.update_closed_pages(lib, "fx_on", skip=[main])
    assert "!fx_detail (FxSim)" in res["updated"], res["updated"]
    assert win._closed_meanwhile({main: None}, names_from=[sim]) == ["!fx_detail (FxMain)"]
    assert win._closed_meanwhile({main: None}) == ["!fx_detail"]
