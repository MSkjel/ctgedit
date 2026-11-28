"""Run holds and results after a library save."""

from ctgedit.ui.main_window import MainWindow
import json
import os
import subprocess
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
        ("opt/auto_update_genies", True),
    ):
        st.setValue(k, v)
    yield
    UpdatePagesRun.users_busy = ""
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = False
    UpdatePagesRun.tabs_run = ""
    UpdatePagesRun.active = None
    UpdatePagesRun.users_queue.clear()
    UpdatePagesRun.users_retry = False


# -- one run at a time, its open tabs' step included -----------------------------------------
def _fake_users_run(win, monkeypatch, fx, during_tabs):
    """Update Users of fxnest.n0 in ``win`` with its plan and saves faked (a page to update,
    one library genie saved): ``during_tabs(win)`` runs in its open tabs' step."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    users = [
        types.SimpleNamespace(
            ref=types.SimpleNamespace(label="FxGenies", path=fx.page("FxGenies"), kind="page"),
            needs_update=True,
            links=[],
        )
    ]
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan([], users))
    monkeypatch.setattr(
        GU,
        "run_update",
        lambda plan, **k: GU.UpdateUsersReport(
            elements_saved=["fxnest.n1"], libraries_written=[fx.file("FxLib", "fxnest.ctm")]
        ),
    )
    monkeypatch.setattr(
        type(win),
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: during_tabs(self) or OpenTabsUpdate(),
    )
    monkeypatch.setattr(type(win), "_queue_open_genie_updates", lambda self: None)
    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: None)
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="fxnest.n0"))
    return win._update_element_users(usage, confirm=False)


def test_a_waiting_chain_starts_when_update_users_open_tabs_have_followed(fx_install, win, monkeypatch):
    """A genie saved in another window during Update Users waits in the queue; its chain does
    not start inside the run's open tabs' step (its progress window lets events through)."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    started = []
    stage = {"now": "run"}
    monkeypatch.setattr(
        type(win), "_start_users_run", lambda self, lib, el, name: started.append(stage["now"]) or True
    )
    UpdatePagesRun.users_queue.append(
        (win, (fx_install.file("FxLib", "fxgenie.ctm"), "fx_value", "fxgenie.fx_value"))
    )

    def during_tabs(w):
        stage["now"] = "tabs"
        _app().processEvents()
        w._next_users_run()  # (the queue's retry timer, in the step)
        assert UpdatePagesRun.busy_text("Pack the libraries")
        stage["now"] = "after"

    _fake_users_run(win, monkeypatch, fx_install, during_tabs)
    assert started == []
    for _ in range(3):
        _app().processEvents()
    assert started == ["after"], started
    assert not UpdatePagesRun.tabs_following


def test_the_other_runs_wait_while_a_run_brings_its_open_tabs_up_to_date(win, win2, monkeypatch):
    """Update Users in window A brings its open tabs up to date (users_busy is clear, its
    hold is not): Pack Libraries, Update Pages and Update Users in window B are refused,
    and their refusal leaves A's hold alone."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog import dialogs
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    told = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(GU, "update_plan", lambda usage: pytest.fail("it ran"))
    monkeypatch.setattr(dialogs, "UpdatePagesDialog", lambda *a, **k: pytest.fail("Update Pages started"))
    UpdatePagesRun.tabs_following = True  # (window A's run, in its open tabs' step)
    assert "open tabs are being brought up to date" in UpdatePagesRun.busy_text("Pack the libraries")
    assert win2._update_pages_running()
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="lib.a"))
    assert win2._update_element_users(usage, confirm=False) is None
    win2._update_pages()
    assert UpdatePagesRun.tabs_following, "a refused run cleared the other run's hold"
    assert len(told) == 3 and all("open tabs are being brought up to date" in t for t in told), told


def test_a_run_started_while_its_question_was_open_goes_first(win, monkeypatch):
    """Update Users' question and Update Pages' window let events through: a run started
    meanwhile in another window goes first, and these are refused."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog import dialogs
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    told = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    page = types.SimpleNamespace(
        ref=types.SimpleNamespace(label="P", path="C:/x/P.ctg", kind="page"), needs_update=True
    )
    monkeypatch.setattr(GU, "update_plan", lambda usage: GU.UpdatePlan([], [page]))
    monkeypatch.setattr(GU, "run_update", lambda *a, **k: pytest.fail("it ran"))

    def confirm(*a, **k):
        UpdatePagesRun.chain_busy = True  # (Update Pages' chain, window B)
        return True

    monkeypatch.setattr(type(win), "_confirm", confirm)
    usage = types.SimpleNamespace(target=types.SimpleNamespace(label="lib.a"))
    assert win._update_element_users(usage, confirm=True) is None
    assert told[-1].startswith("Update Pages is running."), told
    UpdatePagesRun.chain_busy = False

    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            UpdatePagesRun.users_busy = "fxnest.n0"  # (a chain after a save, window B)
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(
                templates=True, genies=True, symbols=True, backup=False, page_properties=False, chain=False
            )

        def projects(self):
            return ["C:/x"]

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)
    from ctgedit.io import pgregister

    monkeypatch.setattr(pgregister, "probe_writable", lambda d: None)
    monkeypatch.setattr(win, "_update_pages_in_worker", lambda *a, **k: pytest.fail("Update Pages ran"))
    win._update_pages()
    assert "fxnest.n0" in told[-1], told


def test_only_the_run_that_took_the_hold_releases_it(win):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window.open_tabs import TabsHold

    mine, theirs = TabsHold(), TabsHold()
    mine.take()
    theirs.release(win)  # (a run that never took it)
    assert UpdatePagesRun.tabs_following
    mine.release(win)
    assert not UpdatePagesRun.tabs_following and not UpdatePagesRun.busy_text("x")


def test_update_pages_without_its_chain_holds_the_others_through_its_open_tabs(tmp_path, monkeypatch):
    """Update Pages' helpers end (``active`` is cleared) before its open tabs' step: the
    step holds the other runs and the queue too, with or without the chain."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.simulate.live_reload import library_run_going
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate
    from test_update_pages_chain import _run

    seen = []
    monkeypatch.setattr(
        MainWindow,
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: (
            seen.append((bool(UpdatePagesRun.busy_text("Pack the libraries")), library_run_going()))
            or OpenTabsUpdate()
        ),
    )
    calls, _shown, _w = _run(tmp_path, monkeypatch, False)
    assert [c[0] for c in calls] == ["pages"]
    assert seen == [(True, True)], seen
    assert not UpdatePagesRun.tabs_following


def test_a_second_update_pages_refused_in_the_same_window_keeps_the_runs_hold(tmp_path, monkeypatch):
    """A second Update Pages clicked in the window of the run (its chain lets events
    through) is refused; its end does not clear the first run's hold."""
    _app()
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate
    from test_update_pages_chain import _run

    seen = []
    monkeypatch.setattr(
        MainWindow,
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: seen.append(UpdatePagesRun.tabs_following) or OpenTabsUpdate(),
    )
    calls, shown, _w = _run(tmp_path, monkeypatch, True, on_plan=lambda w: w._update_pages())
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    assert "Update Pages is already running." in shown
    assert seen == [True], "the refused run cleared the first run's hold"
    assert not UpdatePagesRun.tabs_following


def test_the_busy_text_names_the_run_in_its_open_tabs_step(fx_install, win, tmp_path, monkeypatch):
    """The other runs' busy text in a run's open tabs' step names that run (Update Pages
    without its chain updates no library)."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate
    from test_update_pages_chain import _run

    seen = []
    _fake_users_run(
        win, monkeypatch, fx_install, lambda w: seen.append(UpdatePagesRun.busy_text("Pack the libraries"))
    )
    monkeypatch.setattr(
        MainWindow,
        "_update_open_documents_shown",
        lambda self, dirs, opts, title, **k: (
            seen.append(UpdatePagesRun.busy_text("Run Update Pages")) or OpenTabsUpdate()
        ),
    )
    _run(tmp_path, monkeypatch, False)
    assert seen == [
        "Update Users is bringing the open tabs up to date. Pack the libraries when it has finished.",
        "Update Pages is bringing the open tabs up to date. Run Update Pages when it has finished.",
    ], seen
    assert not UpdatePagesRun.tabs_following and not UpdatePagesRun.tabs_run


def test_following_the_saves_without_a_hold_leaves_the_runs_hold_alone(win):
    """_follow_run_saves without a hold of its own leaves the hold of the run going on: only
    the run that took it releases it."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun
    from ctgedit.ui.window.open_tabs import TabsHold

    mine = TabsHold()
    mine.take()
    win._follow_run_saves([])
    assert UpdatePagesRun.tabs_following, "a call with no hold cleared the run's hold"
    win._follow_run_saves([], mine)
    assert not UpdatePagesRun.tabs_following


def test_a_copy_left_as_stored_makes_the_run_result_a_warning(win, monkeypatch):
    """Update Users' and Update Pages' result that names a copy a save could not compose
    again is a warning, as the chain's note after a library save gives it."""
    from ctgedit.ui import notify
    from ctgedit.ui.jobs import run_results

    monkeypatch.setattr(run_results.RunResultsDialog, "exec", lambda self: 0)
    c = notify.centre(win)
    c.clear()
    win._show_run_results(
        "Update Users",
        [
            "1 library genie saved again: mylib.hh.",
            "Nested copies that could not be composed again and stay as stored: mylib.hh (its "
            "copy of fxnest.n0).",
        ],
    )
    assert c.entries[-1].level == "warning", c.entries[-1]


# -- a helper that dies in the pages phase ---------------------------------------------------
def _edit_pump(fx) -> str:
    """fxgenie.fx_pump's text moved and saved: only pages use it."""
    from ctgedit.editing import ElementDocument, libraries_changed

    lib = fx.file("FxLib", "fxgenie.ctm")
    d = ElementDocument.open_element(lib, "fx_pump")
    t = next(i for i, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    d.move_object(t, 3, 0)
    d.save()
    libraries_changed()
    return lib


def test_the_chains_helper_writes_its_answer_after_each_page(fx_install, tmp_path, monkeypatch):
    """The pages phase writes the answer so far after each page; an error of a later page
    comes back with the pages saved before it and the backup."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.io import updatepages

    lib = _edit_pump(fx_install)
    real = updatepages._Run.page
    count = {"n": 0}

    def page(self, i, path):
        count["n"] += 1
        if count["n"] == 2:
            raise MemoryError("out of memory")
        return real(self, i, path)

    monkeypatch.setattr(updatepages._Run, "page", page)
    prog = str(tmp_path / "run.progress")
    bk = str(tmp_path / "bk")
    res = GW.update_closed_pages(lib, "fx_pump", progress_file=prog, backup=bk)
    assert "out of memory" in res["error"]
    assert len(res["updated"]) == 1 and res["backup"] == bk, res
    with open(prog + GW.GENIES_SUFFIX, encoding="utf-8") as fh:
        side = json.load(fh)
    assert side["updated"] == res["updated"] and side["backup"] == bk
    assert len(side["pages"]) > 1


def test_the_editor_names_the_pages_a_dead_helper_saved(fx_install, win, tmp_path, monkeypatch):
    """The chain's helper (a real process) dies when it takes its second page: no answer.
    The note names the page it saved and the backup, with Details."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.infra import workers
    from ctgedit.ui import notify

    lib = _edit_pump(fx_install)
    ran = {}

    def in_a_helper(
        lib_path,
        element,
        skip,
        builder_open,
        backup,
        progress_file,
        skip_file,
        cancel_file,
        editor_file,
        elements=True,
    ):
        req, ans = str(tmp_path / "req.json"), str(tmp_path / "ans.json")
        with open(req, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "format": GW.FORMAT,
                    "library": lib_path,
                    "element": element,
                    "skip": list(skip),
                    "builder_open": list(builder_open),
                    "backup": backup,
                    "elements": elements,
                    "progress_file": progress_file,
                    "skip_file": skip_file,
                    "cancel_file": cancel_file,
                    "editor_file": editor_file,
                },
                fh,
            )
        env = dict(workers.environment(), CTG_TEST_EXIT_AT="after_records:1:0")
        r = subprocess.run(
            GW.users_command(req, ans),
            env=env,
            timeout=600,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ran["code"], ran["answer"] = r.returncode, os.path.exists(ans)
        raise RuntimeError("the helper gave no answer")

    monkeypatch.setattr(GW, "update_closed_pages", in_a_helper)
    c = notify.centre(win)
    c.clear()
    assert win._start_users_run(lib, "fx_pump", "fxgenie.fx_pump")
    assert ran == {"code": 3, "answer": False}
    m = c.entries[-1]
    assert m.text == "Saved 1 closed page using fxgenie.fx_pump, then the update failed.", m.text
    assert "Backup: " in m.detail and m.level == "error"
    assert [a.label for a in m.actions][:1] == ["Details"]


def test_a_failed_chain_names_the_pages_it_saved(win):
    res = {
        "error": "the update gave no answer",
        "elements_saved": ["fxnest.n1"],
        "element_kinds": {"fxnest.n1": "genie"},
        "updated": ["FxBig", "FxMain"],
        "libraries": [],
        "tabs_saved": [],
    }
    assert win._users_run_finished(res, "fxnest.n0") == (
        "Saved 1 library genie and 2 closed pages using fxnest.n0, then the update failed."
    )


# -- the copies a save could not compose -----------------------------------------------------
def _uncomposable_host(fx, monkeypatch) -> str:
    """mylib.hh places fxnest.n0 and fxgenie.fx_value; n0 gains a text (its copies are built
    again), fx_value's text changes, and building a copy fails (tests/
    test_compose_all_criterion simulates it so): fx_value's genie library."""
    from ctgedit.format import genie_place as GP
    from test_compose_all_criterion import _add_text_to_n0, _edit_text, _genies, _host

    _host(fx)
    _add_text_to_n0(fx)
    _edit_text(_genies(fx), "fx_value", "%Description% EDITED")

    def fail(*_a, **_k):
        raise ValueError("cannot")

    monkeypatch.setattr(GP, "_build_nested_copy", fail)
    return _genies(fx)


def test_update_users_names_a_copy_it_could_not_compose(fx_install, win, monkeypatch):
    from ctgedit.io import genieusage as GU

    gen = _uncomposable_host(fx_install, monkeypatch)
    reports = []
    monkeypatch.setattr(type(win), "_report_users_update", lambda self, text, **k: reports.append(text))
    rep = win._update_element_users_run(GU.element_users(gen, "fx_value"), confirm=False)
    assert ("mylib.hh", ["fxnest.n0"]) in rep.not_composed
    text = reports[-1]
    # (counted by kind: two of the three are templates)
    assert text.startswith("1 library genie and 2 templates saved again: mylib.hh, "), text
    assert (
        "Nested copies that could not be composed again and stay as stored: mylib.hh "
        "(its copy of fxnest.n0)." in text
    ), text
    assert "(s)" not in text


def test_the_chain_names_a_copy_it_could_not_compose(fx_install, win, monkeypatch):
    from ctgedit.workers import genie_worker as GW

    gen = _uncomposable_host(fx_install, monkeypatch)
    res = GW.update_closed_pages(gen, "fx_value")
    assert res["not_composed"] == [["mylib.hh", ["fxnest.n0"]]], res.get("not_composed")
    assert "mylib.hh" in res["elements_saved"]
    text = win._users_run_finished(res, "fxgenie.fx_value")
    assert " 1 nested copy could not be composed again and stays as stored (Details says which)." in text, (
        text
    )
    details = win._users_run_details(res)
    assert (
        "Nested copies that could not be composed again and stay as stored:\n  mylib.hh "
        "(its copy of fxnest.n0)" in details
    ), details


# -- Update Pages' chain box ------------------------------------------------------------------------
def test_compose_all_names_a_copy_once_when_its_genie_waits_and_is_saved(fx_install, win, monkeypatch):
    """mylib.x's only difference is a copy compose-all cannot compose, but x nests n1, which
    the chain saves: the waiting pass plans x, and its save names the copy once."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import genie_place as GP
    from ctgedit.io import genieusage as GU
    from test_compose_all_criterion import _edit_text, _genies, _host, _nest, _save_keeping_stamps_current

    fx = fx_install
    _edit_text(_nest(fx), "n0", "%Label% CHANGED")
    _save_keeping_stamps_current(_nest(fx), "n1")  # (current by its stamps, not in fact)
    mine = _host(
        fx, "x", genies=(("fxgenie", "fx_value", {"Description": "D"}), ("fxnest", "n1", {"Label": "L"}))
    )
    v = ElementDocument.open_element(_genies(fx), "fx_value")
    v.create_object("text", (10, 60, 120, 76), text="NEWOBJ")
    v.save()
    libraries_changed()
    _save_keeping_stamps_current(mine, "x")

    def fail(*_a, **_k):
        raise ValueError("cannot")

    monkeypatch.setattr(GP, "_build_nested_copy", fail)
    plan = GU.chain_plan([fx.project("FxLib")], compose_all=True)
    x = next(u for u in plan.elements if u.ref.label == "mylib.x")
    assert x.status == GU.WAITING and x.compose_all
    assert not plan.not_composed, plan.not_composed  # (its save names it)
    rep = win._update_pages_chain([fx.project("FxLib")], None, compose_all=True)
    assert rep.not_composed == [("mylib.x", ["fxgenie.fx_value"])], rep.not_composed
    lines = MainWindow._chain_summary(rep)
    assert sum(x.count("mylib.x (its copy of fxgenie.fx_value)") for x in lines) == 1, lines


def test_the_chain_box_counts_what_it_saved_by_kind(fx_install):
    """After a symbol save the chain saves two symbols, a super genie and a template: the
    box counts them by kind, as the chain's note does."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU

    fx = fx_install
    d = ElementDocument.open_element(fx.file("FxLib", "fxsym.ctl"), "fx_on")
    d.move_object(0, 3, 2)
    d.save()
    libraries_changed()
    plan = GU.chain_plan([fx.project(p) for p in ("FxLib", "FxStyle", "FxMain")])
    rep = GU.run_update(GU.UpdatePlan(plan.elements, [], plan.skipped))
    assert rep.element_kinds["fx_alt.sets@-XGA"] == "template"
    lines = MainWindow._chain_summary(rep)
    assert lines[0] == (
        "1 super genie, 1 template and 2 library symbols saved again first: "
        "fxmore.fx_dspsym, fxsuper.!fx_detail@SUPER, fxsym.fx_set, "
        "fx_alt.sets@-XGA."
    ), lines


def test_the_chain_box_counts_the_matched_and_the_supplied_ones_by_kind():
    from ctgedit.io import genieusage as GU

    chain = GU.UpdateUsersReport(
        elements_current=["fx_alt.sets@-XGA", "fxsym.fx_set"],
        skipped=["sxw.t@-XGA: %s" % GU.SHIPPED_WHY, "sxw.g: %s" % GU.SHIPPED_WHY],
        element_kinds={
            "fx_alt.sets@-XGA": "template",
            "fxsym.fx_set": "symbol",
            "sxw.t@-XGA": "template",
            "sxw.g": "genie",
        },
    )
    assert MainWindow._chain_summary(chain) == [
        "1 template and 1 library symbol already matched what they place: fx_alt.sets@-XGA, fxsym.fx_set.",
        "1 genie and 1 template in libraries supplied with Plant SCADA were left alone: sxw.t@-XGA, sxw.g.",
    ]


# -- placeholder genies whose library is back ------------------------------------------------
def _link_names(data_or_path):
    from ctgedit.format import links
    from ctgedit.format.page import parse_ctg, parse_ctg_bytes

    pb = (parse_ctg_bytes(data_or_path) if isinstance(data_or_path, bytes) else parse_ctg(data_or_path)).page
    t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
    return sorted(n for _g, n in (t[1] if t else []) if not n.startswith("\\"))


def test_update_pages_places_placeholder_genies_whose_library_is_back(fx_install):
    """FxReturned holds four placeholders of fxback.ctm's genies (their library was gone at
    its save, and is back): Update Pages places them, its entries take the current stamp, and
    Used By agrees."""
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages as U

    path = fx_install.page("FxReturned")
    back = fx_install.file("FxMain", "fxback.ctm")
    assert U.page_needs_update(path)
    status = {u.ref.label: u.status for u in GU.element_users(back, "fx_value").users}
    assert status.get("FxReturned") == GU.OUT_OF_DATE, status
    rep = U.update_pages(pages=[path])
    (rec,) = rep.records
    assert rec["outcome"] == "updated" and rec["placeholders"] == 4, rec
    assert rep.placeholders == 4
    libraries_changed()
    assert EditableDocument.open(path).placeholder_genies() == []
    assert not U.page_needs_update(path)
    status = {u.ref.label: u.status for u in GU.element_users(back, "fx_value").users}
    assert status.get("FxReturned") == GU.UP_TO_DATE, status
    assert all("#" in n for n in _link_names(path))
    (again,) = U.update_pages(pages=[path]).records
    assert again["outcome"] == "current"


@pytest.mark.golden
def test_update_pages_writes_the_link_table_ctdraws_load_and_save_writes(fx_install):
    """CtDraw's PageOpen + PageSave of FxReturned (the oracle) places the four genies again
    and stamps their entries: Update Pages' save lists the same entries."""
    import fxinstall
    from ctgedit.io import updatepages as U

    path = fx_install.page("FxReturned")
    theirs = fxinstall.oracle_bytes("pages", "FxMain/FxReturned", "page.ctg")
    assert U.update_pages(pages=[path]).records[0]["outcome"] == "updated"
    assert _link_names(path) == _link_names(theirs)


def test_a_placeholder_that_cannot_be_placed_is_said_so(fx_install, monkeypatch):
    """A placeholder ctgedit does not place (a turned or scaled one) keeps the page out of
    date: its record says so, not that an older version is quoted."""
    from ctgedit.editing import EditableDocument
    from ctgedit.io import updatepages as U

    monkeypatch.setattr(EditableDocument, "placeholder_genies", lambda self: [])
    rec = U.update_pages(pages=[fx_install.page("FxReturned")]).records[0]
    assert rec["outcome"] == "unchanged"
    assert rec["detail"].startswith(
        "a placeholder of fxback.fx_plain, fxback.fx_value could not be placed again"
    ), rec["detail"]
    assert "older version" not in rec["detail"]


def test_the_open_tabs_step_places_them_too(fx_install):
    """A page open in a tab whose placeholders' library came back after its open: Update
    Pages' step for the open pages places them, as the helper does on a closed page."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.window.open_tabs import OpenTabsUpdate

    d = EditableDocument.open(fx_install.page("FxReturned"))
    assert len(d.placeholder_genies()) == 4
    res = OpenTabsUpdate()
    failed, _found = MainWindow._update_open_document(
        d, {"genies": True, "templates": True, "symbols": True}, res
    )
    assert failed == [] and res.placeholders == 4
    assert d.placeholder_genies() == [] and res.counts()[0] >= 4


def test_a_stamp_less_entry_is_out_of_date_for_pages_and_elements():
    """CtDraw writes the bare entry in a library element too, and its load + save places the
    genie again (LIVE oracle chain5/element_placeholder; tests/test_save_chain_links.py)."""
    from ctgedit.io import genieusage as GU

    page = GU.Ref("page", "C:/p/FxReturned.ctg")
    elem = GU.Ref("element", "C:/p/mylib.ctm", "hh")
    target = GU.Ref("element", "C:/p/fxback.ctm", "fx_value")
    assert GU.Link(page, target, "fxback.fx_value.<ctm", None, 0x1234).stale
    assert not GU.Link(page, target, "fxback.fx_value.<ctm", None, None).stale  # (gone)
    assert GU.Link(elem, target, "fxback.fx_value.<ctm", None, 0x1234).stale
    assert not GU.Link(elem, target, "fxback.fx_value.<ctm", None, None).stale  # (gone)
    # (a page's symbol and template entries too, as Update Pages reads them)
    tpl = GU.Ref("element", "C:/p/fx_style.ctt", "normal@XGA")
    assert GU.Link(page, tpl, "fx_style.normal@XGA.<ctt", None, 0x1234).stale
    sym = GU.Ref("element", "C:/p/fxsym.ctl", "fx_box")
    assert GU.Link(page, sym, "fxsym.fx_box.<ctl", None, 0x1234).stale
    assert GU.Link(elem, sym, "fxsym.fx_box.<ctl", None, 0x1234).stale


def _strip_stamps(path: str, ext: str) -> None:
    """The page's link-table entries of ``ext`` ('.<ctl', '.<ctt') written without their
    stamp, as another program's save left them in pages of a project."""
    from ctgedit.format import links, writer
    from ctgedit.format.page import parse_ctg

    pc = parse_ctg(path)
    area, ents = links.parse_link_table(pc.page.link_table_raw)
    ents = [(g, links.strip_stamp(n) if links.strip_stamp(n).lower().endswith(ext) else n) for g, n in ents]
    pc.page.link_table_raw, pc.page.link_count = links.serialize_link_table(area, ents)
    writer.write_ctg(path, pc.container, pc.page, pc.attributes)


def test_a_pages_stamp_less_symbol_and_template_entries_are_out_of_date_in_both(fx_install):
    """A page's symbol and template entries with no stamp whose element is there: Update
    Pages reads them out of date (and its run stamps them), and Used By agrees, before and
    after the run."""
    from ctgedit.editing import libraries_changed
    from ctgedit.io import genieusage as GU
    from ctgedit.io import updatepages as U

    fx = fx_install
    cases = (
        (fx.page("FxSymbols"), ".<ctl", fx.file("FxLib", "fxsym.ctl"), "fx_box"),
        (fx.page("FxTplLinked"), ".<ctt", fx.file("FxStyle", "fx_style.ctt"), "normal@XGA"),
    )

    def status(lib, el, name):
        return {u.ref.label: u.status for u in GU.element_users(lib, el).users}.get(name)

    for path, ext, lib, el in cases:
        name = os.path.splitext(os.path.basename(path))[0]
        assert not U.page_needs_update(path)
        _strip_stamps(path, ext)
        libraries_changed()
        assert not all("#" in n for n in _link_names(path))
        assert U.page_needs_update(path)
        assert status(lib, el, name) == GU.OUT_OF_DATE, (name, status(lib, el, name))
        (rec,) = U.update_pages(pages=[path]).records
        assert rec["outcome"] == "updated", rec
        libraries_changed()
        assert all("#" in n for n in _link_names(path))
        assert not U.page_needs_update(path)
        assert status(lib, el, name) == GU.UP_TO_DATE


def test_the_summary_counts_the_placeholders_placed():
    from test_update_pages_chain import _fake_report

    rep = _fake_report()
    rep.placeholders = 4
    lines = MainWindow._update_pages_summary(rep, "BK", None)
    assert "4 placeholder genies placed again from their library." in lines, lines
