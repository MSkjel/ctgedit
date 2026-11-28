"""The update after a genie save, and how it treats its own writes."""

from ctgedit.ui.main_window import MainWindow
import hashlib
import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow2 import _quiet_chain, win, win2  # noqa: E402,F401


def _lib(fx):
    return fx.file("FxLib", "fxnest.ctm")


def _set_n0(fx, text: str) -> None:
    """n0's text becomes '%Label% <text>', saved (not through a window)."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(_lib(fx), "n0")
    i = next(k for k, o in enumerate(d.parsed.page.objects) if o.type_code == 0x05)
    assert d.set_text(i, "%Label% " + text)
    d.save()
    libraries_changed()


def _marks(doc, text: str) -> int:
    from ctgedit.format import writer

    m = text.encode("cp1252")
    return sum(m in writer.object_payload(o) for o in doc.parsed.page.objects)


def _tab(w, fx, element: str):
    w._open_element(_lib(fx), element)
    return w.doc


def _move(doc) -> None:
    top = next(i for i, o in enumerate(doc.page.objects) if o.parent is None)
    doc.move_objects([top], 7, 0)


def _helper_run(fx, tmp_path, tabs=()) -> tuple:
    """The chain's helper run in this process for a save of n0, with an editor list naming
    ``tabs`` (element documents) clean: (its answer, its progress file)."""
    from ctgedit.workers import genie_worker as GW

    editors = tmp_path / "run.editors"
    editors.write_text("\n".join("clean\t%s\t%s" % (d.path, d.element_name) for d in tabs))
    prog = tmp_path / "run.progress"
    res = GW.update_closed_pages(
        _lib(fx), "n0", progress_file=str(prog), editor_file=str(editors), backup=str(tmp_path / "bk")
    )
    return res, str(prog)


def _disk_notices(doc) -> list:
    from ctgedit.ui.editor import notices

    return [n for n in notices.of(doc) if n.key == "disk"]


# -- what the helper says it read and stored -------------------------------------------------
def test_the_helpers_answer_says_what_it_read_and_stored(fx_install, win, tmp_path):
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import LibraryFile

    tab = _tab(win, fx_install, "n2")
    lib = LibraryFile.open(_lib(fx_install))
    before = hashlib.sha1(bytes(lib.block_data(lib.find("n2")))).hexdigest()
    _set_n0(fx_install, "FIRST")
    res, prog = _helper_run(fx_install, tmp_path, [tab])
    rows = {(os.path.basename(r[0]).lower(), r[1].lower()): r for r in res.get("element_versions") or ()}
    assert ("fxnest.ctm", "n2") in rows and ("fxnest.ctm", "n1") in rows, rows
    read, wrote = rows[("fxnest.ctm", "n2")][2:4]
    assert read == before == tab.baseline_block_sha1() and wrote != read
    assert ElementDocument.open_element(_lib(fx_install), "n2").baseline_block_sha1() == wrote
    assert tab.stored_block_sha1() == wrote and tab.changed_on_disk()
    pages = {os.path.basename(p).lower(): (r, w) for p, r, w in res["page_versions"]}
    assert "fxgenies.ctg" in pages, res["page_versions"]
    st = os.stat(fx_install.page("FxGenies"))
    assert tuple(pages["fxgenies.ctg"][1]) == (st.st_mtime_ns, st.st_size)
    # (the answer so far beside the progress file names them too)
    so_far = GW.chain_answer_so_far(prog)
    assert [r[:4] for r in so_far["element_versions"]] == [r[:4] for r in res["element_versions"]]


# -- the sub genie saved again while the update runs ----------------------------------------
@pytest.mark.parametrize("composed_first", [True, False])
def test_a_sub_genie_saved_again_during_the_update_reaches_the_row_tab(
    fx_install, win, monkeypatch, composed_first
):
    """The row tab (n2 here) took the first save; the update's helper saves it from that
    composition; the sub genie (n0) is saved again before the update ends. At its end the tab
    shows the second save."""
    from ctgedit.workers import genie_worker as GW

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    if composed_first:
        win._queue_open_genie_updates()  # (the tab takes the save: inline here)
        assert _marks(tab, "FIRST") and tab.dirty
    real = GW.update_closed_pages

    def run(*a, **k):
        res = real(*a, **k)
        _set_n0(fx_install, "SECOND")  # (saved again while it ran)
        return res

    monkeypatch.setattr(GW, "update_closed_pages", run)
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert _marks(tab, "SECOND"), "the second save reached the open tab"
    assert not tab.changed_on_disk() and not _disk_notices(tab)


def test_a_tab_the_update_saved_takes_the_next_save_while_it_runs(fx_install, win, tmp_path):
    """The update's helper has saved the row tab's element (its answer so far says so) and the
    sub genie is saved again: the tab takes the helper's block as its own and composes the
    sub genie at once."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    _res, prog = _helper_run(fx_install, tmp_path, [tab])
    assert tab.changed_on_disk()
    _set_n0(fx_install, "SECOND")

    try:
        UpdatePagesRun.users_busy = "fxnest.n0"
        UpdatePagesRun.chain_progress = prog
        names = win._update_stale_genies(tab)
    finally:
        UpdatePagesRun.chain_progress = ""
        UpdatePagesRun.users_busy = ""
    assert names, "composed at once"
    assert not tab.changed_on_disk() and _marks(tab, "SECOND")


def test_a_tab_changed_by_the_running_update_it_has_not_named_waits_for_its_end(fx_install, win, tmp_path):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    _helper_run(fx_install, tmp_path, [tab])
    try:
        UpdatePagesRun.users_busy = "fxnest.n0"
        assert win._update_stale_genies(tab) == []
        assert any(d is tab for d in win.__dict__.get("_after_run_docs") or ()), (
            "it waits for the update's end, it is not dropped"
        )
    finally:
        UpdatePagesRun.users_busy = ""


# -- clean for the update: the steps since the last save ------------------------------------
def test_a_tab_is_clean_when_its_steps_since_the_save_are_automatic(fx_install, win):
    tab = _tab(win, fx_install, "n2")
    _move(tab)
    tab.save()  # (a saved step of the user's)
    _set_n0(fx_install, "FIRST")
    win._queue_open_genie_updates()
    assert tab.dirty and _marks(tab, "FIRST")
    assert MainWindow._tab_clean(tab), "a saved move and an automatic update: clean"
    assert "clean\t" in MainWindow._element_tab_states()
    tab.ui.auto_changes = []  # (an earlier run's end settled the bar)
    assert MainWindow._tab_clean(tab)
    _move(tab)
    assert not MainWindow._tab_clean(tab), "a step of the user's since the save"
    tab.undo()
    assert MainWindow._tab_clean(tab)
    tab.save()
    tab.undo()  # (the automatic step, below the save)
    assert tab.dirty and MainWindow._tab_clean(tab), "an automatic step undone: no change of theirs"
    tab.undo()  # (the user's saved move undone)
    assert tab.dirty and not MainWindow._tab_clean(tab)


# -- a tab with the user's changes takes what the helper stored as its own ------------------
def test_the_update_end_keeps_the_users_change_and_takes_the_stored_block(fx_install, win, monkeypatch):
    """The user edits the row tab after the update's helper saved it: at the end the tab
    keeps the edit, takes the stored block as its baseline (no 'changed by another program'
    notice) and its save does not ask to overwrite the element."""
    from ctgedit.workers import genie_worker as GW

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    win._queue_open_genie_updates()
    real = GW.update_closed_pages

    def run(*a, **k):
        res = real(*a, **k)
        _move(tab)  # (the user's edit after it saved the tab)
        return res

    monkeypatch.setattr(GW, "update_closed_pages", run)
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert tab.dirty and any(c.label == "move" for c in tab._undo)
    assert not tab.changed_on_disk(), "the helper's block is the tab's baseline"
    win._check_disk_changes([tab.path])
    assert not _disk_notices(tab)
    asked = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: asked.append(a[1]) or QtWidgets.QMessageBox.Cancel),
    )
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is tab)
    win.show_tab(k)
    assert win._save_element_to() and not asked


def test_the_disk_check_during_the_update_takes_its_write_as_the_tabs_own(fx_install, win, tmp_path):
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    _res, prog = _helper_run(fx_install, tmp_path, [tab])
    _move(tab)

    try:
        UpdatePagesRun.users_busy = "fxnest.n0"
        UpdatePagesRun.chain_progress = prog
        win._check_disk_changes([tab.path])
    finally:
        UpdatePagesRun.chain_progress = ""
        UpdatePagesRun.users_busy = ""
    assert not _disk_notices(tab), "ctgedit's own update is no other program"
    assert not tab.changed_on_disk() and tab._undo[-1].label == "move"


def test_a_reload_that_waits_for_the_user_is_no_change_by_another_program(
    fx_install, win, monkeypatch, tmp_path
):
    """A reload that waits for the user isn't treated as another program's change."""
    from ctgedit.editing import ElementDocument

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")  # (the tab's own update is held: not composed)
    res, _prog = _helper_run(fx_install, tmp_path, [tab])
    monkeypatch.setattr(type(win), "_may_change_now", lambda self, d, redo=True: False)
    win._users_run_finished(res, "fxnest.n0")  # (the run's end, as the window applies it)
    assert win._waits_until_idle(tab), "its reload waits for the user"
    assert not tab.changed_on_disk(), "the helper's block is its baseline meanwhile"
    win._check_disk_changes([tab.path])
    assert not _disk_notices(tab)
    monkeypatch.undo()
    _move(tab)  # (the user's edit when the drag ends)
    ElementDocument.for_nested_update(tab.nested_update_request(), tab.serialize())
    win._take_idle_wait(tab)
    win._reload_that_waited(tab, "element")
    assert _marks(tab, "FIRST"), "the tab composed the change on its records"
    assert tab.dirty and any(c.label == "move" for c in tab._undo)
    assert not tab.changed_on_disk() and not _disk_notices(tab)


@pytest.mark.parametrize("composed_first", [True, False])
def test_a_tab_that_took_the_helpers_block_saves_its_records(fx_install, win, tmp_path, composed_first):
    """The row tab took what the helper stored as its baseline and kept the user's move
    (composed on its records when its own update had not landed): its save writes what the
    tab shows, the move and the update, and the stored element is current."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import writer

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    if composed_first:
        win._queue_open_genie_updates()
    res, _prog = _helper_run(fx_install, tmp_path, [tab])
    _move(tab)
    win._users_run_finished(res, "fxnest.n0")
    assert not tab.changed_on_disk() and _marks(tab, "FIRST")
    k = next(i for i, e in enumerate(win._docs) if e["doc"] is tab)
    win.show_tab(k)
    assert win._save_element_to()
    libraries_changed()
    stored = ElementDocument.open_element(_lib(fx_install), "n2")
    payloads = [writer.object_payload(o) for o in tab.parsed.page.objects]
    assert [writer.object_payload(o) for o in stored.parsed.page.objects] == payloads
    assert not stored.nested_update_ops(every=True)[0] and not stored.nested_genie_changes()


def test_a_page_the_update_wrote_from_the_tabs_version_keeps_the_users_change(fx_install, win, monkeypatch):
    """A page opened during the update and edited at once; its helper wrote the page from the
    version the tab read: the tab keeps the edit and takes the file as its baseline (no
    'changed by another program' notice)."""
    from ctgedit.editing import EditableDocument

    page = fx_install.page("FxGenies")
    win.open_document(page)
    tab = win.doc
    read = tuple(tab._disk_stamp)
    _move(tab)
    other = EditableDocument.open(page)  # (the helper's write of the page)
    _move(other)
    other.save()
    wrote = tuple(other._disk_stamp)
    assert tab.changed_on_disk()
    res = {
        "pages": [page],
        "page_versions": [[page, list(read), list(wrote)]],
        "elements_saved": [],
        "elements_in_editor": [],
        "libraries": [],
        "tabs_saved": [],
    }
    win._settle_unlisted_tabs(res)
    assert not tab.changed_on_disk() and tab._undo[-1].label == "move"
    win._check_disk_changes([page])
    assert not _disk_notices(tab)


def test_a_change_by_another_program_is_still_told(fx_install, win, tmp_path):
    """A block another program stored (not the version the helper stored) keeps the notice."""
    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    res, _prog = _helper_run(fx_install, tmp_path, [tab])
    from ctgedit.editing import ElementDocument, libraries_changed

    o = ElementDocument.open_element(_lib(fx_install), "n2")
    _move(o)
    o.save()  # (another program, after the helper)
    libraries_changed()
    _move(tab)
    win._users_run_finished(res, "fxnest.n0")
    assert tab.changed_on_disk()
    win._check_disk_changes([tab.path])
    assert _disk_notices(tab)


# -- nested copies named as their library spells them ---------------------------------------
def test_a_template_copy_in_a_template_tab_is_named_as_a_template(fx_install, win):
    from test_save_chain_reports4 import _outer_template
    from ctgedit.editing import ElementDocument, libraries_changed

    inner, outer, _page = _outer_template(fx_install)
    win._open_element(outer, "made@XGA")
    tab = win.doc
    t = ElementDocument.open_element(inner, "normal@XGA")
    t.create_object("text", (20, 20, 200, 40), text="TPLNEW")
    t.save()
    libraries_changed()
    keys = tab.nested_genie_changes()
    win._update_stale_genies(tab)
    assert tab._undo and tab._undo[-1].label == "template fx_style.normal@XGA updated"
    assert [w for w, _c in tab.ui.auto_changes] == ["template fx_style.normal@XGA updated from its library"]
    names, label = win._nested_copy_names(tab, keys)
    assert names == ["template fx_style.normal@XGA"] and label == "template fx_style.normal@XGA updated"
    n0 = win._nested_copy_names(tab, {("fxnest", "n0")})
    assert n0 == (["fxnest.n0"], "")


def test_the_block_digests_of_an_element_document(fx_install):
    from ctgedit.editing import ElementDocument
    from ctgedit.format.element import LibraryFile

    d = ElementDocument.open_element(_lib(fx_install), "n2")
    lib = LibraryFile.open(_lib(fx_install))
    want = hashlib.sha1(bytes(lib.block_data(lib.find("n2")))).hexdigest()
    assert d.baseline_block_sha1() == want == d.stored_block_sha1()
    assert json.loads(json.dumps([want]))[0] == want


# -- saves and reloads while the helper runs ------------------------------------------------
def test_a_sub_genie_saved_while_the_helper_runs_lands_at_once_and_stays(fx_install, win, monkeypatch):
    """A sub genie saved while the helper runs lands at once and stays."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    win._queue_open_genie_updates()
    assert _marks(tab, "FIRST")
    real = GU.run_update
    seen = {}

    def run(plan, **k):
        told = k.get("on_element")

        def on_element(rep):
            if told is not None:
                told(rep)  # (the helper's answer so far)
            if not seen and any(str(t[1]).lower() == "n2" for t in rep.tabs_saved):
                _set_n0(fx_install, "SECOND")  # (the user saves the sub genie again)
                win._queue_open_genie_updates()
                seen.update(busy=UpdatePagesRun.users_busy, now=_marks(tab, "SECOND"))

        k["on_element"] = on_element
        return real(plan, **k)

    monkeypatch.setattr(GU, "run_update", run)
    assert win._start_users_run(_lib(fx_install), "n0", "fxnest.n0")
    assert seen.get("busy") == "fxnest.n0", seen
    assert seen.get("now"), "the open tab took the second save at once, during the run"
    assert _marks(tab, "SECOND"), "and kept it after the run's end"
    assert not tab.changed_on_disk() and not _disk_notices(tab)


def test_a_tab_the_running_chain_names_later_composes_before_its_end(fx_install, win, tmp_path, monkeypatch):
    """A tab the running chain names later is composed before the chain ends."""
    from PySide6 import QtCore
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs import genie_jobs
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    # (the job composes at once when it is made: the timer is what is looked at)
    monkeypatch.setattr(genie_jobs, "_in_event_loop", lambda: False)
    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    _res, prog = _helper_run(fx_install, tmp_path, [tab])
    so_far = prog + GW.GENIES_SUFFIX
    with open(so_far, encoding="utf-8") as fh:
        full = fh.read()
    named = json.loads(full)
    with open(so_far, "w", encoding="utf-8") as fh:
        json.dump(
            dict(named, element_versions=[r for r in named["element_versions"] if str(r[1]).lower() != "n2"]),
            fh,
        )
    assert tab.changed_on_disk()
    _set_n0(fx_install, "SECOND")
    UpdatePagesRun.users_busy = "fxnest.n0"
    UpdatePagesRun.chain_progress = prog
    assert win._update_stale_genies(tab) == [] and not _marks(tab, "SECOND")
    seen = {}
    loop = QtCore.QEventLoop()

    def name_it():
        with open(so_far, "w", encoding="utf-8") as fh:
            fh.write(full)  # (its answer so far after the element)

    def look():
        if _marks(tab, "SECOND") and "busy" not in seen:
            seen["busy"] = UpdatePagesRun.users_busy
            loop.quit()

    QtCore.QTimer.singleShot(50, name_it)
    t = QtCore.QTimer()
    t.timeout.connect(look)
    t.start(20)
    QtCore.QTimer.singleShot(8000, loop.quit)
    loop.exec()
    t.stop()
    assert seen.get("busy") == "fxnest.n0", "composed while the run still runs"
    assert not tab.changed_on_disk() and not _disk_notices(tab)


def test_automatic_steps_made_one_stay_automatic(fx_install, win):
    """Two automatic steps made one (EditableDocument.merge_last_steps: the chain's template
    step and the link-table step after it) are still no step of the user's: the tab stays
    clean for the update after a genie save, and undone it is nothing to redo that holds
    the next automatic update."""

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    win._queue_open_genie_updates()
    _set_n0(fx_install, "SECOND")
    win._queue_open_genie_updates()
    assert len(tab._undo) == 2 and all(c.automatic for c in tab._undo)
    one = tab.merge_last_steps(2)
    assert one is not None and one.automatic, "still automatic"
    assert MainWindow._tab_clean(tab)
    tab.undo()
    assert not win._user_can_redo(tab), "an automatic update undone: nothing of the user's"


def test_a_page_reload_that_waits_lets_a_closed_window_go(fx_install, win2, monkeypatch):
    """A page the update wrote whose reload waits for the user, in a window that is closed:
    the window keeps nothing of it (a closed window lets its documents go)."""
    import gc
    import weakref
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.main_window import MainWindow

    page = fx_install.page("FxGenies")
    win2.show()
    win2.open_document(page)
    tab = win2.doc
    read = tuple(tab._disk_stamp)
    other = EditableDocument.open(page)  # (the helper's write of the page)
    _move(other)
    other.save()
    wrote = tuple(other._disk_stamp)
    other = None
    res = {
        "pages": [page],
        "page_versions": [[page, list(read), list(wrote)]],
        "elements_saved": [],
        "elements_in_editor": [],
        "libraries": [],
        "tabs_saved": [],
    }
    monkeypatch.setattr(MainWindow, "_may_change_now", lambda self, d, redo=True: False)
    win2._settle_unlisted_tabs(res)
    assert win2._waits_until_idle(tab)
    monkeypatch.undo()
    ref = weakref.ref(tab)
    tab.dirty = False
    tab = None
    win2.close()
    assert win2 not in MainWindow.instances
    gc.collect()
    assert ref() is None, "the closed window let its document go"


def test_a_window_closed_while_its_tab_waits_for_the_chain_lets_it_go(fx_install, win2, tmp_path):
    """A tab of a closed window that waited for the running chain's answer to name it
    (files._when_chain_names): its timer stops with the window, and the window keeps
    nothing of it, as of the tabs that waited for the user or for a run's end
    (shell._release_closed_window)."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    win2.show()
    tab = _tab(win2, fx_install, "n2")
    UpdatePagesRun.users_busy = "fxnest.n0"
    UpdatePagesRun.chain_progress = str(tmp_path / "run.progress")
    win2._when_chain_names(tab)
    win2._chain_writes_waiting()[id(tab)] = (lambda: tab, {})
    timer = win2.__dict__.get("_chain_named_timer")
    assert timer is not None and timer.isActive()
    win2.close()
    assert win2 not in MainWindow.instances
    assert not timer.isActive(), "its timer stops with the window"
    assert not win2.__dict__.get("_chain_named_docs"), "nothing of its tabs is kept"
    assert not win2.__dict__.get("_chain_waits")


def test_an_update_the_worker_refused_for_the_chains_write_lands_at_once(
    fx_install, win, tmp_path, monkeypatch
):
    """An update the worker refused for the chain's write lands at once."""
    import time
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs import genie_jobs
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    tab = _tab(win, fx_install, "n2")
    _set_n0(fx_install, "FIRST")
    win._queue_open_genie_updates()
    assert _marks(tab, "FIRST")
    jobs = win._genie_job_runner()
    # (the job waits in the queue: no worker process here; it is answered below)
    monkeypatch.setattr(type(jobs), "_start_more", lambda self: None)
    monkeypatch.setattr(genie_jobs, "_in_event_loop", lambda: True)
    _set_n0(fx_install, "SECOND")
    assert win._update_stale_genies(tab), "the second save's job is sent"
    job = next(j for j in jobs._queue if j.doc is tab)
    monkeypatch.setattr(genie_jobs, "_in_event_loop", lambda: False)
    _res, prog = _helper_run(fx_install, tmp_path, [tab])  # (stores n2: FIRST and SECOND)
    assert tab.changed_on_disk()
    UpdatePagesRun.users_busy = "fxnest.n0"
    UpdatePagesRun.chain_progress = prog
    jobs._queue.remove(job)
    jobs._ready.append(
        (job, {"format": GW.FORMAT, "updated": 0, "ops": [], "changed_on_disk": True}, time.monotonic())
    )
    jobs._poll()  # (the worker's answer, as the window takes it)
    assert UpdatePagesRun.users_busy == "fxnest.n0"
    assert not tab.changed_on_disk(), "the helper's block is the tab's own"
    assert _marks(tab, "SECOND"), "the second save landed at once"
    assert not _disk_notices(tab)
