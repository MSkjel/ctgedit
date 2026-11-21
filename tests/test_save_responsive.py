"""Saving a big genie doesn't freeze the window."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_save_chain_follow2 import _quiet_chain, win  # noqa: E402,F401

KEY = ("fxgenie", "fx_value")


def _genie_text(lib_dir, text):
    """fxgenie.fx_value's top-level text set to ``text`` and the genie saved (outside the
    window): its element document."""
    from ctgedit.editing import ElementDocument, libraries_changed

    d = ElementDocument.open_element(os.path.join(lib_dir, "fxgenie.ctm"), "fx_value")
    t = next(i for i, o in enumerate(d.page.objects) if o.parent is None and d.objects[i].type_code == 0x05)
    d.set_text(t, "%Description% " + text)
    d.save()
    libraries_changed()
    return d


def _count(doc, root, mark: bytes) -> int:
    from ctgedit.format import writer

    return sum(mark in writer.object_payload(doc.objects[k]) for k in doc.subtree(root))


def _two_instances(win, fx_install, moved=True):
    """FxShapes open in ``win`` with two fx_value instances; the second one's member moved by
    hand (the genie's version before a save no longer reproduces it)."""
    win.open_document(fx_install.page("FxShapes"))
    pd = win.doc
    for k in range(2):
        pd.place_library_element(*KEY, "ctm", (100 + 200 * k, 500), {"Description": "D%d" % k})
    if moved:
        b = list(pd.genie_instances())[-1][0]
        pd.move_objects([pd.subtree(b)[1]], 7, 0)
    return pd


def _spin_until(cond, limit_s=60.0):
    """Run a real event loop until ``cond()`` (looked at every 50 ms) or ``limit_s``."""
    loop = QtCore.QEventLoop()
    t0 = time.monotonic()

    def look():
        if cond() or time.monotonic() - t0 > limit_s:
            loop.quit()
            return
        QtCore.QTimer.singleShot(50, look)

    QtCore.QTimer.singleShot(0, look)
    loop.exec()
    return cond()


def test_a_genie_save_composes_nothing_in_the_window_for_the_open_pages(fx_install, win, monkeypatch):
    """The save composes nothing in _propagate_element: the update of every out of date genie
    (_queue_open_genie_updates, in the worker) takes the pages, the page shown first."""
    from ctgedit.editing import EditableDocument, ElementDocument
    from ctgedit.format import genie_place as GP

    pd = _two_instances(win, fx_install)
    d = _genie_text(fx_install.project("FxLib"), "EDITED")
    composed, queued = [], []
    real_upd, real_snap, real_load = (
        EditableDocument.update_genie_instances,
        ElementDocument._snapshot_element,
        GP.load_element,
    )
    monkeypatch.setattr(
        EditableDocument,
        "update_genie_instances",
        lambda self, *a, **k: composed.append("instances") or real_upd(self, *a, **k),
    )
    monkeypatch.setattr(
        ElementDocument,
        "_snapshot_element",
        lambda self, *a, **k: composed.append("snapshot") or real_snap(self, *a, **k),
    )
    monkeypatch.setattr(GP, "load_element", lambda *a, **k: composed.append("load") or real_load(*a, **k))
    monkeypatch.setattr(win, "_queue_open_genie_updates", lambda: queued.append(1))
    win._propagate_element(d)
    assert composed == [] and queued == [1]
    assert pd.genie_changes() == {KEY: None}, "the page waits for its update"
    assert not pd._undo or pd._undo[-1].label != "update genies from library"


def test_no_note_of_an_update_that_updated_nothing(fx_install, win, monkeypatch):
    """No 'Updated 0 placed ... 1 more to update in full.' note comes at a save before the
    page takes the change: the update's own note says it when it lands."""
    from ctgedit.ui import notify as N

    win.open_document(fx_install.page("FxShapes"))
    pd = win.doc
    pd.place_library_element(*KEY, "ctm", (100, 500), {"Description": "D0"})
    b = list(pd.genie_instances())[-1][0]
    pd.move_objects([pd.subtree(b)[1]], 7, 0)
    notes = []
    monkeypatch.setattr(N, "notify", lambda _w, text, *a, **k: notes.append(text))
    monkeypatch.setattr(win, "_queue_open_genie_updates", lambda: None)
    win._propagate_element(_genie_text(fx_install.project("FxLib"), "EDITED"))
    assert notes == []


def test_the_previous_version_update_runs_in_the_worker_and_is_automatic(fx_install, win, monkeypatch):
    """Settings' Update changed genies off: the open page that places the saved genie takes
    the edit where the genie's version before the save reproduces the instance, composed in
    the genie worker (a real event loop: nothing is composed in this process), as one
    automatic step (the bar, the close question); the instance it leaves is said."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui import notify as N
    from ctgedit.ui.prefs.settings import app_settings

    pd = _two_instances(win, fx_install)
    a, b = [i for i, _k in pd.genie_instances()][-2:]
    app_settings().setValue("opt/auto_update_genies", False)
    d = _genie_text(fx_install.project("FxLib"), "EDITED")
    notes, here = [], []
    real_notify = N.notify
    monkeypatch.setattr(
        N, "notify", lambda w, text, *x, **k: notes.append(text) or real_notify(w, text, *x, **k)
    )
    real_upd = EditableDocument.update_genie_instances
    monkeypatch.setattr(
        EditableDocument,
        "update_genie_instances",
        lambda self, *x, **k: here.append(1) or real_upd(self, *x, **k),
    )
    steps = len(pd._undo)
    QtCore.QTimer.singleShot(0, lambda: win._propagate_element(d))
    jobs = lambda: win.__dict__.get("_genie_jobs")  # noqa: E731
    assert _spin_until(lambda: len(pd._undo) > steps and not (jobs() and jobs().busy()))
    assert here == [], "composed in the worker, not in the window"
    a, b = [i for i, _k in pd.genie_instances()][-2:]
    assert _count(pd, a, b"EDITED") == 1 and _count(pd, b, b"EDITED") == 0
    assert pd.genie_changes() == {KEY: None}, "the instance left keeps the page out of date"
    assert pd._undo[-1].label == "update genies from library"
    assert [c for _w, c in pd.ui.auto_changes] == [pd._undo[-1]]
    assert any("1 placed fxgenie.fx_value on FxShapes is left out of date" in t for t in notes), notes
    assert not any("Updated 0" in t or "update in full" in t for t in notes), notes
    app_settings().setValue("opt/auto_update_genies", True)


def test_a_run_the_tabs_wait_for_still_lets_the_saves_own_update_land(fx_install, win, monkeypatch):
    """Settings 'Open tabs wait for the update of what uses a saved genie' on and a run going:
    the page that places the saved genie takes the edit at the save, from the worker (here at
    once: no event loop), the rest at the run's end, and the page stays out of date for the
    run's end."""
    from ctgedit.ui.main_window import MainWindow

    pd = _two_instances(win, fx_install)
    monkeypatch.setattr(MainWindow, "_runs_hold_tabs", classmethod(lambda cls: True))
    queued = []
    monkeypatch.setattr(win, "_queue_open_genie_updates", lambda: queued.append(1))
    win._propagate_element(_genie_text(fx_install.project("FxLib"), "EDITED"))
    a, b = [i for i, _k in pd.genie_instances()][-2:]
    assert _count(pd, a, b"EDITED") == 1 and _count(pd, b, b"EDITED") == 0
    assert queued == [1] and pd.genie_changes() == {KEY: None}
    assert [c for _w, c in pd.ui.auto_changes] == [pd._undo[-1]]


def test_the_workers_update_by_the_previous_version_is_the_editors(fx_install):
    """genie_worker.apply_update with the library bytes before the save gives the page the
    editor's update by ElementDocument.previous_element gave it."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.format import writer
    from ctgedit.ui.jobs import genie_jobs

    page = fx_install.page("FxShapes")
    pd = EditableDocument.open(page)
    for k in range(2):
        pd.place_library_element(*KEY, "ctm", (100 + 200 * k, 500), {"Description": "D%d" % k})
    b = list(pd.genie_instances())[-1][0]
    pd.move_objects([pd.subtree(b)[1]], 7, 0)
    with open(page, "wb") as fh:
        fh.write(pd.serialize())
    d = _genie_text(fx_install.project("FxLib"), "EDITED")
    data = d.previous_bytes()
    assert data is not None
    ref = EditableDocument.open(page)
    n_ref = ref.update_genie_instances({KEY}, {KEY: d.previous_element})
    doc = EditableDocument.open(page)
    prev, where = genie_jobs.previous_files({KEY: (d.path, data)}, str(fx_install.root))
    try:
        ans = GW.compute(page, doc.serialize(), {}, previous=prev)
    finally:
        import shutil

        shutil.rmtree(where, ignore_errors=True)
    assert ans.get("left") == 1 and not ans.get("error"), ans.get("error")
    assert n_ref == 1 and genie_jobs.apply_answer(doc, ans) >= 1
    assert [writer.object_payload(o) for o in doc.parsed.page.objects] == [
        writer.object_payload(o) for o in ref.parsed.page.objects
    ]
    assert doc.parsed.page.link_table_raw == ref.parsed.page.link_table_raw


def test_a_save_composes_the_version_before_only_when_asked(fx_install, monkeypatch):
    """A genie save does not compose the version it overwrote (previous_element): the save
    keeps the bytes only."""
    from ctgedit.editing import ElementDocument

    calls = []
    real = ElementDocument._snapshot_element
    monkeypatch.setattr(
        ElementDocument, "_snapshot_element", lambda self, *a, **k: calls.append(1) or real(self, *a, **k)
    )
    d = _genie_text(fx_install.project("FxLib"), "EDITED")
    assert calls == [] and d.previous_bytes() is not None


# -- the save of a large element normalises in a helper ---------------------------------------
def _records_of(doc, content):
    from ctgedit.format import element as E, writer
    from ctgedit.format.element_session import open_element_session

    lib = E.LibraryFile(doc.session.pe.library.to_bytes())
    lib.write_element(doc.session.pe.name, content)
    _s, parsed = open_element_session(doc.path, doc.session.pe.name, library=lib)
    return [writer.object_payload(o) for o in parsed.page.objects], parsed.page.link_table_raw


def test_the_helpers_memo_is_the_editors_normalisation(fx_install):
    """ElementDocument.content_memo (the helper's side) gives the content the editor's own
    normalisation gives; a memo of other records is refused (the save normalises itself)."""
    from ctgedit.editing import ElementDocument

    lib = fx_install.file("FxLib", "fxparts.ctm")
    d = ElementDocument.open_element(lib, "fx_row")
    d.move_objects([list(d.page.roots())[0]], 6, 0)
    here = d.serialize_element(refresh=True, memo={})
    data = d.content_memo(True)
    memo = d.memo_from_helper(data, True)
    assert memo is not None and d.serialize_element(refresh=True, memo=memo) == here
    assert d.memo_from_helper(data, False) is None, "another choice"
    d.move_objects([list(d.page.roots())[0]], 1, 0)
    assert d.memo_from_helper(data, True) is None, "other records"


def test_a_large_element_save_runs_its_normalisation_in_the_helper(fx_install, win, monkeypatch):
    """A save from the window of an element of at least Settings' size (here 1 object) is
    normalised in the window's helper process: the window's event loop runs meanwhile (its
    timers fire), the status bar says 'Saving', nothing is normalised in this process, and
    the element stored is the one the window's own normalisation gives."""
    from ctgedit.format import genie_norm as GN
    from ctgedit.ui.dialog import element_save
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/save_helper_records", 1)
    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    d = win.doc
    d.move_objects([list(d.page.roots())[0]], 6, 0)
    want = _records_of(d, d.serialize_element(refresh=bool(d.nested_genie_changes()), memo={}))
    normalised, ticks, said = [], [], []
    real = GN.normalise_records
    monkeypatch.setattr(GN, "normalise_records", lambda *a, **k: normalised.append(1) or real(*a, **k))
    beat = QtCore.QTimer()
    beat.timeout.connect(lambda: ticks.append(time.monotonic()))
    win.statusBar().messageChanged.connect(lambda t: said.append(t))
    done = []

    def save():
        beat.start(10)
        done.append(win.save())
        beat.stop()

    QtCore.QTimer.singleShot(0, save)
    assert _spin_until(lambda: bool(done), 90)
    try:
        assert done == [True]
        helper = win.__dict__.get("_save_helper")
        assert helper is not None and helper.last_wait_s > 0
        assert normalised == [], "normalised in the helper"
        assert len(ticks) >= 3, "the window's event loop ran during the save"
        assert any(t.startswith("Saving fxparts.fx_row") for t in said), said
        from ctgedit.format import writer

        got = ([writer.object_payload(o) for o in d.parsed.page.objects], d.parsed.page.link_table_raw)
        assert got == want
        assert not d.dirty
    finally:
        element_save.stop(win)
        app_settings().setValue("opt/save_helper_records", 1000)


def test_a_small_element_or_a_script_saves_in_the_window(fx_install, win):
    """Under Settings' size, or with no event loop (a script, a test), no helper is used."""
    from ctgedit.ui.dialog import element_save

    win._open_element(fx_install.file("FxLib", "fxparts.ctm"), "fx_row")
    assert element_save.prepare_for(win) is None  # (no event loop)
    helper = element_save.SaveHelper(win)
    try:
        d = win.doc
        assert helper.prepare(win, d, True, False) is None  # (fx_row: under 1000)
        assert not helper.worker.alive()
    finally:
        helper.stop()


def test_the_tab_being_saved_takes_no_automatic_update(fx_install, win):
    win.open_document(fx_install.page("FxShapes"))
    d = win.doc
    win._saving_doc = d
    try:
        assert not win._genie_may_land(d)
        assert win._genie_may_land(object())
    finally:
        win._saving_doc = None
    assert win._genie_may_land(d)


def test_the_save_settings_are_rows_of_long_jobs():
    from ctgedit.ui.prefs import job_options
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    rows = {r.key: r for r in SettingsDialog.ROWS}
    for key in ("opt/save_helper_records", "opt/save_window_ms"):
        assert rows[key].section == "Long jobs" and key in job_options.KEYS
    assert job_options.save_helper_records() == 1000 and job_options.save_window_ms() == 10000


def test_a_progress_window_waits_for_its_delay_past_qts_own(win):
    """JobProgress's window shows after its own delay, not after the 4 s timer Qt's
    QProgressDialog starts when it is made. A short delay still shows it."""
    from ctgedit.ui.jobs.job_progress import JobProgress

    p = JobProgress(win, "Saving", "Saving fxparts.fx_row…", cancel=None, show_after_ms=10000)
    q = JobProgress(win, "Saving", "Saving fxparts.fx_row…", cancel=None, show_after_ms=300)
    seen, t0 = [], time.monotonic()
    try:

        def look():
            p.pulse()
            q.pulse()
            seen.append((p.prog.isVisible(), q.prog.isVisible()))
            return time.monotonic() - t0 > 4.4

        _spin_until(look, 10)
        assert not any(a for a, _b in seen), "shown before its delay"
        assert seen[-1][1] and q.shown, "a delay under Qt's own still shows it"
    finally:
        p.close()
        q.close()


def test_a_disk_change_during_the_save_waits_for_its_end(fx_install, win):
    """A disk change during the save waits for its end."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.ui.editor import notices

    lib = fx_install.file("FxLib", "fxparts.ctm")
    win._open_element(lib, "fx_row")
    d = win.doc
    d.move_objects([list(d.page.roots())[0]], 6, 0)
    other = ElementDocument.open_element(lib, "fx_row")  # (the chain's helper)
    other.move_objects([list(other.page.roots())[0]], 0, 4)
    other.save()
    libraries_changed()
    assert d.changed_on_disk()
    win._saving_doc = d
    try:
        win._check_disk_changes([d.path])
        assert notices.get(d, "disk") is None and d.path in win._disk_pending
    finally:
        win._saving_doc = None
    d.save()  # (the save goes on: it replaces that write)
    win._check_disk_changes()
    assert notices.get(d, "disk") is None and not d.changed_on_disk()


def test_two_saves_before_the_pages_update_starts_keep_the_version_it_holds(fx_install, win, monkeypatch):
    """Two saves before the page's update starts keep the version it holds."""
    from ctgedit.ui import notify as N
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_install.page("FxShapes"))
    pd = win.doc
    pd.place_library_element(*KEY, "ctm", (100, 500), {"Description": "D0"})
    root = list(pd.genie_instances())[-1][0]
    app_settings().setValue("opt/auto_update_genies", False)
    jobs = win._genie_job_runner()
    monkeypatch.setattr(jobs, "MAX_RUNNING", 0)  # (the page's job waits in the queue)
    notes, state = [], {"sent": False}
    real_notify = N.notify
    monkeypatch.setattr(
        N, "notify", lambda w, text, *x, **k: notes.append(text) or real_notify(w, text, *x, **k)
    )
    lib = fx_install.project("FxLib")

    def saves():
        win._propagate_element(_genie_text(lib, "ONE"))
        win._propagate_element(_genie_text(lib, "TWO"))
        jobs.MAX_RUNNING = 1
        jobs._start_more()
        state["sent"] = True

    QtCore.QTimer.singleShot(0, saves)
    assert _spin_until(lambda: state["sent"] and not jobs.busy(), 90)
    root = list(pd.genie_instances())[-1][0]
    assert _count(pd, root, b"TWO") == 1, "the copy takes the genie as saved last"
    assert pd.genie_changes() == {}, "the page is up to date"
    assert not any("left out of date" in t for t in notes), notes


def test_an_update_by_the_version_before_leaves_nothing_on_a_page_current_for_it(fx_install):
    """A page whose update ran meanwhile (it composed the genie as saved) and that quotes the
    genie's current stamp has nothing left out of date."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs

    page = fx_install.page("FxShapes")
    pd = EditableDocument.open(page)
    pd.place_library_element(*KEY, "ctm", (100, 500), {"Description": "D0"})
    with open(page, "wb") as fh:
        fh.write(pd.serialize())
    d = _genie_text(fx_install.project("FxLib"), "EDITED")
    cur = EditableDocument.open(page)
    assert cur.apply_genie_changes(cur.genie_changes()) and cur.genie_changes() == {}
    prev, where = genie_jobs.previous_files({KEY: (d.path, d.previous_bytes())}, str(fx_install.root))
    try:
        ans = GW.compute(page, cur.serialize(), {}, previous=prev)
    finally:
        import shutil

        shutil.rmtree(where, ignore_errors=True)
    assert not ans.get("error"), ans.get("error")
    assert ans.get("left") == 0 and ans["ops"] == []


def test_a_save_that_brings_a_nested_copy_up_to_date_draws_it_in_the_helper(fx_install, win, monkeypatch):
    """A large element's save that composes a nested genie that changed meanwhile stores a
    thumbnail drawn from the element as stored by the helper that normalised it; the window
    draws nothing."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.format import element as E
    from ctgedit.render.headless_thumb import element_thumbnail
    from ctgedit.ui.dialog import element_save
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/save_helper_records", 1)
    app_settings().setValue("opt/auto_update_genies", False)  # (the tab keeps the old copy)
    n1 = ElementDocument.open_element(fx_install.file("FxLib", "fxnest.ctm"), "n1")
    n1.move_objects([list(n1.page.roots())[-1]], 0, 9)
    n1.save()
    libraries_changed()
    lib = fx_install.file("FxLib", "fxparts.ctm")
    win._open_element(lib, "fx_row")
    d = win.doc
    assert d.nested_genie_changes(), "the row's copy of n1 is out of date"
    d.move_objects([list(d.page.roots())[0]], 6, 0)
    old_picture = win._element_thumbnail()
    drawn = []
    real = ElementDocument._thumbnail_as_stored
    monkeypatch.setattr(
        ElementDocument, "_thumbnail_as_stored", lambda self, *a, **k: drawn.append(1) or real(self, *a, **k)
    )
    done = []
    QtCore.QTimer.singleShot(0, lambda: done.append(win.save()))
    try:
        assert _spin_until(lambda: bool(done), 90) and done == [True]
        assert drawn == [], "drawn in the helper, not in the window"
        stored = E.open_element(lib, "fx_row").page.thumbnail
        assert stored and stored != old_picture, "the element as stored, drawn again"
        assert not d.nested_genie_changes()
        monkeypatch.setattr(ElementDocument, "_thumbnail_as_stored", real)
        here = ElementDocument.open_element(lib, "fx_row")
        pe = here.session.pe
        body = bytes(pe.block.data)
        content = body[: len(body) - len(pe.slack)] if pe.slack else body
        assert real(here, content, element_thumbnail) == stored, "as the window draws it"
    finally:
        element_save.stop(win)


@pytest.fixture(autouse=True)
def _settings_back():
    yield
    from ctgedit.ui.prefs.settings import app_settings

    for k in ("opt/save_helper_records", "opt/save_window_ms"):
        app_settings().remove(k)
    app_settings().setValue("opt/auto_update_genies", True)
