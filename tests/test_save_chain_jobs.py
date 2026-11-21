"""Genie worker answers that land after the page changed."""

import collections
import json
import os
import shutil
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from qtkit import app as _app

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TEMPLATE = "fx_style.normal@XGA.<ctt"
#: the template as the step labels name it (without '.<ctt')
SHOWN = "fx_style.normal@XGA"


def _make_win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop_win(w):
    from ctgedit.ui.main_window import MainWindow

    jobs = w.__dict__.get("_genie_jobs")
    if jobs is not None:
        jobs.shutdown()
    for ent in list(getattr(w, "_docs", ())):
        d = ent["doc"]
        if hasattr(d, "dirty"):
            d.dirty = False
    w.doc = None
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.close()


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
def _quiet_chain():
    """The save prompts off, the chain after a save left, the open's updates on."""
    from ctgedit.ui.prefs.settings import app_settings
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    st = app_settings()
    for k, v in (
        ("opt/save_symbol_warning", False),
        ("opt/save_template_warning", False),
        ("opt/genie_save_pages", "never"),
        ("opt/symbol_save_pages", "never"),
        ("opt/auto_update_genies", True),
        ("opt/auto_update_symbols", True),
        ("opt/auto_update_template", True),
    ):
        st.setValue(k, v)
    yield
    UpdatePagesRun.users_busy = ""
    UpdatePagesRun.chain_busy = False
    UpdatePagesRun.tabs_following = False
    UpdatePagesRun.users_queue.clear()


def _handles(doc):
    from ctgedit.format import geometry as geom

    return [geom.get_handle(o) for o in doc.parsed.page.objects]


def _dups(doc):
    return sorted(h for h, c in collections.Counter(_handles(doc)).items() if c > 1 and h is not None)


def _table(doc):
    from ctgedit.format import links

    pb = doc.parsed.page
    t = links.parse_link_table(pb.link_table_raw) if pb.link_count else None
    return [n for _g, n in (t[1] if t else []) if not n.startswith("\\")]


def _stamp(names, prefix):
    from ctgedit.format import links

    return next(links.entry_stamp(n) for n in names if n.lower().startswith(prefix.lower()))


def _gains_a_record(path, element, rect=(0, 40, 10, 50)):
    """``element`` of the library ``path`` gains a rectangle, saved not through a window."""
    from ctgedit.editing import ElementDocument, libraries_changed

    e = ElementDocument.open_element(path, element)
    e.create_object("rectangle", rect, colour=0x00FF00)
    e.save()
    libraries_changed()


def _genie_page(fx):
    """A page placing fxgenie.fx_value, saved; then fx_value gains a rectangle."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pp = os.path.join(fx.project("FxMain"), "FxJob.ctg")
    p = EditableDocument.new_from_template(TEMPLATE, pp, linked=False)
    p.place_library_element("fxgenie", "fx_value", "ctm", (100, 300), params={"Description": "P1"})
    p.save()
    libraries_changed()
    _gains_a_record(fx.file("FxLib", "fxgenie.ctm"), "fx_value")
    return pp


def _stale_template_page(fx, mode="symbol"):
    """A page linked to fx_style's normal@XGA that places fxsym.fx_box (``mode`` 'symbol') or
    holds a placeholder genie whose library opens (``mode`` 'placeholder'), saved; then the
    template (and the symbol) gain a rectangle, saved not through a window."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pp = os.path.join(fx.project("FxMain"), "FxOrder.ctg")
    p = EditableDocument.new_from_template(TEMPLATE, pp, linked=True)
    if mode == "symbol":
        p.place_library_element("fxsym", "fx_box", "ctl", (400, 400))
    else:
        src = EditableDocument.open(fx.page("FxReturned"))
        ph = src.placeholder_genies()
        p.paste_payload(src.copy_payload([ph[0][0]]), offset=0)
    p.save()
    libraries_changed()
    _gains_a_record(fx.file("FxStyle", "fx_style.ctt"), "normal@XGA", (500, 500, 540, 540))
    if mode == "symbol":
        _gains_a_record(fx.file("FxLib", "fxsym.ctl"), "fx_box", (0, 0, 5, 5))
    return pp


def _ctdraw_order(pp):
    """The handles of the page brought up to date in CtDraw's load order (the open's steps
    at once, the template first: apply_open_updates), on a copy."""
    from ctgedit.editing import EditableDocument

    cp = pp[:-4] + "_B.ctg"
    shutil.copyfile(pp, cp)
    b = EditableDocument.open(cp)
    assert b.apply_open_updates()
    return _handles(b), _table(b)


def _worker_here(monkeypatch, tmp_path, by_hand=False):
    """The genie worker as the window drives it, in this process: a job's answer is computed
    from the page's bytes when the job is sent (GenieJob.start, as the worker process reads
    them) and lands when GenieJobs._poll looks (the window's timer). ``by_hand``: it lands
    only when the test calls _poll."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.ui.jobs import genie_jobs as gj

    monkeypatch.setattr(gj, "_in_event_loop", lambda: True)
    if by_hand:
        real_init = gj.GenieJobs.__init__

        def init(self, *a, **k):
            real_init(self, *a, **k)
            self._timer.timeout.disconnect(self._poll)

        monkeypatch.setattr(gj.GenieJobs, "__init__", init)

    def start(self, worker):
        self.attempts += 1
        element = self.doc.nested_update_request() if gj._is_element(self.doc) else None
        data = self.doc.serialize()
        self.req = GW.write_request(
            str(tmp_path),
            self.doc.path,
            data,
            self.changes,
            self.bindings,
            template=self.template,
            element=element,
        )
        self.ans = self.req[:-5] + ".answer.json"
        with open(self.req, encoding="utf-8") as fh:
            req = json.load(fh)
        ans = (
            GW.compute_element(element, data)
            if element
            else GW.compute(
                self.doc.path,
                data,
                GW.changes_from_json(req["changes"]),
                self.bindings,
                template=self.template,
            )
        )
        with open(self.ans, "w", encoding="utf-8") as fh:
            json.dump(ans, fh)
        self.worker = worker
        self.env = gj.environment_signature()

    monkeypatch.setattr(gj.GenieJob, "start", start)


def _labels(doc):
    return [c.label for c in doc._undo]


def _answer_handles(ans) -> set:
    """The handles of an answer's new records."""
    from ctgedit.format import genie_place as GP
    from ctgedit.format import geometry as geom

    return {
        geom.get_handle(GP.to_po(bytes.fromhex(x))) for _i1, _i2, _olds, news, _a in ans["ops"] for x in news
    }


# -------------------------------------------------------------------- answers landing late
def test_a_genie_answer_landing_after_the_user_drew_keeps_one_handle_per_record(fx_install):
    """fx_value gains a rectangle; its update is computed from the page as it was, the user
    draws a rectangle meanwhile (it takes the lowest free handle, the one the genie's new
    member took in the worker): the answer gives its new member a free handle."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.format import geometry as geom
    from ctgedit.ui.jobs import genie_jobs as J

    pp = _genie_page(fx_install)
    ans = GW.compute(pp, open(pp, "rb").read())
    a = EditableDocument.open(pp)
    a.create_object("rectangle", (600, 600, 640, 640))
    mine = geom.get_handle(a.parsed.page.objects[-1])
    assert mine in _answer_handles(ans)  # (the clash)
    assert J.apply_answer(a, ans) > 0
    assert not _dups(a)
    assert geom.get_handle(a.parsed.page.objects[-1]) == mine  # (the user's keeps its own)
    assert not a.genie_changes()
    a.undo()
    assert not _dups(a) and a.genie_changes()
    a.redo()
    assert not _dups(a) and not a.genie_changes()


def test_a_genie_answer_landing_after_a_symbol_was_placed_keeps_its_link_table(fx_install):
    """The user places a symbol before the answer lands (the page's table changed): the
    answer's restamped entry is merged in; the page reads current and stays so after a save."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.io import updatepages as U
    from ctgedit.ui.jobs import genie_jobs as J

    pp = _genie_page(fx_install)
    ans = GW.compute(pp, open(pp, "rb").read())
    assert "link_table" in ans
    a = EditableDocument.open(pp)
    before = _stamp(_table(a), "fxgenie.fx_value.")
    a.place_library_element("fxsym", "fx_box", "ctl", (600, 600))
    assert J.apply_answer(a, ans) > 0
    names = _table(a)
    assert any(n.lower().startswith("fxsym.fx_box.") for n in names)
    assert _stamp(names, "fxgenie.fx_value.") != before
    assert not a.genie_changes()
    a.save()
    libraries_changed()
    assert not U.page_needs_update(pp)


def test_a_template_answer_after_an_inline_step_reads_current(fx_install):
    """The open's template answer applied after the symbol step changed the page (the order
    the window had): no handle twice, the template's new stamp taken."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as J

    pp = _stale_template_page(fx_install, "symbol")
    ans = GW.compute(pp, open(pp, "rb").read(), template=True)
    a = EditableDocument.open(pp)
    assert a.template_status()[0] == "stale"
    assert a.update_symbol_placements(a.stale_symbol_elements())
    assert J.apply_answer(a, ans, "template re-applied") > 0
    assert a.template_status()[0] == "current" and not _dups(a)
    assert not a.stale_symbol_elements()


def test_an_element_tabs_answer_landing_after_the_user_drew_keeps_one_handle_per_record(fx_install, tmp_path):
    """An element tab's nested genie update (compute_element) lands after the user drew two
    rectangles in the tab: the copy's new record takes a free handle."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.ui.jobs import genie_jobs as J

    lib = fx_install.file("FxLib", "fxnest.ctm")
    _gains_a_record(lib, "n0")
    tab = ElementDocument.open_element(lib, "n4")
    assert ("fxnest", "n0") in tab.nested_genie_changes()
    had = set(_handles(tab))
    ans = GW.compute_element(tab.nested_update_request(), tab.serialize())
    assert ans["ops"]
    tab.create_object("rectangle", (300, 300, 340, 340))
    tab.create_object("rectangle", (350, 300, 390, 340))
    # (the clash: a handle the copy's new record took, the user's now)
    assert (_answer_handles(ans) - had) & set(_handles(tab))
    assert J.apply_answer(tab, ans) > 0
    assert not _dups(tab)


def test_an_element_tabs_answer_after_a_nested_copy_was_pasted_is_asked_again(fx_install):
    """The user pastes a copy of the tab's nested genie before the answer lands: the copy holds
    the stale nested genie the answer composed (answer_facts' composed), so the answer is asked
    again; the second one composes both copies."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import ElementDocument
    from ctgedit.format import writer
    from ctgedit.ui.jobs import genie_jobs as J

    lib = fx_install.file("FxLib", "fxnest.ctm")
    _gains_a_record(lib, "n0")
    tab = ElementDocument.open_element(lib, "n4")
    ans = GW.compute_element(tab.nested_update_request(), tab.serialize())
    assert ans["ops"] and "fxnest.n3.<ctm" in ans["composed"]
    root = next(
        i for i, o in enumerate(tab.parsed.page.objects) if o.class_flag and (o.subtype_size & 0x201) == 0x201
    )
    tab.paste_payload(tab.copy_payload([root]), offset=40)
    steps = len(tab._undo)
    assert J.apply_answer(tab, ans) is None and len(tab._undo) == steps
    assert J.apply_answer(tab, GW.compute_element(tab.nested_update_request(), tab.serialize())) > 0
    before = [writer.object_payload(p) for p in tab.parsed.page.objects]
    again = GW.compute_element(tab.nested_update_request(), tab.serialize())
    assert not again["ops"], "a copy stayed stale"
    assert [writer.object_payload(p) for p in tab.parsed.page.objects] == before


def test_a_genie_copy_pasted_meanwhile_is_composed_too(fx_install):
    """The user duplicates the stale genie before its update lands: the answer, which calls
    the genie current, did not compose the copy, so it is asked again (None); the answer
    asked again composes both copies."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as J

    pp = _genie_page(fx_install)
    ans = GW.compute(pp, open(pp, "rb").read())
    a = EditableDocument.open(pp)
    had = len(a.genie_instances())
    root = a.genie_instances()[-1][0]
    a.paste_payload(a.copy_payload([root]), offset=40)
    assert len(a.genie_instances()) == had + 1
    steps = len(a._undo)
    assert J.apply_answer(a, ans) is None and len(a._undo) == steps
    again = GW.compute(pp, a.serialize())
    assert J.apply_answer(a, again) > len(ans["ops"])  # (the pasted copy's range too)
    assert not a.genie_changes() and not _dups(a)


def _composed_again(doc, changes) -> int:
    """How many records a further composition of ``changes`` changes on ``doc``: 0 when every
    copy is up to date (undone after)."""
    from ctgedit.format import writer

    before = [writer.object_payload(p) for p in doc.parsed.page.objects]
    steps = len(doc._undo)
    doc.apply_genie_changes(changes)
    after = [writer.object_payload(p) for p in doc.parsed.page.objects]
    if len(doc._undo) > steps:
        doc.undo()
    return len(before) != len(after) or sum(x != y for x, y in zip(before, after))


def test_a_copy_pasted_meanwhile_of_a_genie_whose_nested_genie_changed_is_composed(
    fx_install, monkeypatch, tmp_path
):
    """A copy pasted meanwhile, of a genie whose nested genie changed, is composed."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as gj

    pp = fx_install.page("FxGenies")
    _gains_a_record(fx_install.file("FxLib", "fxnest.ctm"), "n0")
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    d = EditableDocument.open(pp)
    own = _dups(d)  # (the handles the page's input records share)
    changes = d.genie_changes()
    assert changes == {("fxnest", "n4"): frozenset({("fxnest", "n0")})}
    jobs = gj.GenieJobs()
    landed = []
    jobs.finished.connect(lambda doc, names, n, job: landed.append(n))
    try:
        jobs.submit(d, changes, ["fxnest.n4"])  # (its answer computed now)
        root = next(i for i, k in d.genie_instances() if k == ("fxnest", "n4"))
        d.paste_payload(d.copy_payload([root]), offset=40)
        steps = len(d._undo)
        jobs._poll()  # (refused: asked again)
        assert not landed and len(d._undo) == steps and jobs.busy(d)
        jobs._poll()  # (the second answer lands)
        assert landed and landed[0] > 0 and not jobs.busy(d)
    finally:
        jobs.shutdown()
    assert not _composed_again(d, changes) and _dups(d) == own


def test_a_copy_pasted_meanwhile_at_the_handle_of_a_deleted_genie_is_composed(fx_install):
    """The user deletes a genie copy and pastes a copy of the stale genie, whose root takes the
    deleted root's handle: the request held a genie root at that handle, but another one
    (answer_facts' root signatures), so the answer is asked again and composes the copy."""
    from ctgedit.workers import genie_worker as GW
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import geometry as geom
    from ctgedit.ui.jobs import genie_jobs as J

    pp = os.path.join(fx_install.project("FxMain"), "FxJob.ctg")
    p = EditableDocument.new_from_template(TEMPLATE, pp, linked=False)
    p.place_library_element("fxgenie", "fx_value", "ctm", (100, 300), params={"Description": "P1"})
    p.place_library_element("fxgenie", "fx_lamp", "ctm", (300, 300))
    p.save()
    libraries_changed()
    _gains_a_record(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    ans = GW.compute(pp, open(pp, "rb").read())
    a = EditableDocument.open(pp)
    inst = a.genie_instances()
    lamp = next(i for i, k in inst if k == ("fxgenie", "fx_lamp"))
    value = next(i for i, k in inst if k == ("fxgenie", "fx_value"))
    freed = geom.get_handle(a.parsed.page.objects[lamp])
    pay = a.copy_payload([value])
    a.delete_objects([lamp])
    a.paste_payload(pay, offset=40)
    pasted = [i for i, k in a.genie_instances() if k == ("fxgenie", "fx_value")][-1]
    assert geom.get_handle(a.parsed.page.objects[pasted]) == freed  # (the case)
    steps = len(a._undo)
    assert J.apply_answer(a, ans) is None and len(a._undo) == steps
    assert J.apply_answer(a, GW.compute(pp, a.serialize())) > 0
    assert not a.genie_changes() and not _dups(a)
    assert not _composed_again(a, {("fxgenie", "fx_value"): None})


def test_the_link_tables_merge_keeps_both_sides():
    from ctgedit.format import links

    g0, g1, g2 = b"\0" * 16, b"\1" * 16, b"\2" * 16
    base = [(g0, "lib.gen.<ctm#00000001"), (g0, "\\\\Old")]
    theirs = [(g1, "lib.gen.<ctm#00000002"), (g0, "\\\\New")]
    mine = [(g0, "lib.gen.<ctm#00000001"), (g0, "\\\\Old"), (g2, "sym.box.<ctl#00000005")]
    assert links.merge_link_entries(base, theirs, mine) == [
        (g1, "lib.gen.<ctm#00000002"),
        (g0, "\\\\New"),
        (g2, "sym.box.<ctl#00000005"),
    ]
    # the user's own change of an entry the update changed too stays (an older copy pasted)
    mine2 = [(g2, "lib.gen.<ctm#00000000"), (g0, "\\\\Old")]
    assert links.merge_link_entries(base, theirs, mine2)[0] == (g2, "lib.gen.<ctm#00000000")
    assert links.changed_references(base, theirs) == {"lib.gen.<ctm"}


# ------------------------------------------------------------- the jobs' queue
def test_a_genie_update_is_not_merged_into_a_queued_template_job(fx_install, monkeypatch, tmp_path):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as gj

    _worker_here(monkeypatch, tmp_path)
    jobs = gj.GenieJobs()
    try:
        monkeypatch.setattr(jobs, "_start_more", lambda: None)  # (all stay queued)
        d = EditableDocument.open(_genie_page(fx_install))
        assert jobs.submit_template(d, TEMPLATE)
        changes = d.genie_changes()
        jobs.submit(d, changes, ["fxgenie.fx_value"])
        tpl, gen = jobs._queue
        assert tpl.template and not tpl.changes and tpl.names == [TEMPLATE]
        assert not gen.template and gen.changes == changes
    finally:
        jobs.shutdown()


def test_a_pages_next_job_waits_until_its_answer_has_landed(fx_install, monkeypatch, tmp_path):
    """A page's template answer waits for the user (a drag): its genie job is not started,
    which would compute it without the template; another page's job starts."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.jobs import genie_jobs as gj

    _worker_here(monkeypatch, tmp_path, by_hand=True)
    jobs = gj.GenieJobs()
    try:
        d = EditableDocument.open(_genie_page(fx_install))
        other = EditableDocument.open(fx_install.page("FxGenies"))
        jobs.gate = lambda doc, waited: doc is not d  # (d's user is dragging)
        assert jobs.submit_template(d, TEMPLATE)
        jobs._poll()  # (its answer waits)
        assert jobs.busy(d) and not jobs._jobs and jobs._ready
        jobs.submit(d, d.genie_changes(), ["fxgenie.fx_value"])
        jobs.submit(other, {("fxnest", "n4"): None}, ["fxnest.n4"])
        assert [j.doc for j in jobs._jobs.values()] == [other]
        assert [j.doc for j in jobs._queue] == [d]
    finally:
        jobs.shutdown()


# -------------------------------------------------------------------- the open's order
@pytest.mark.parametrize("mode", ["symbol", "placeholder"])
def test_the_open_runs_its_steps_after_the_template_the_worker_applies(
    fx_install, win, mode, monkeypatch, tmp_path
):
    """The open's template step goes to the worker (the default settings): the symbol (or
    placeholder genie) step waits for it and runs when it lands, so the page takes the
    handles and stamps of CtDraw's load (template first) and reads current."""
    pp = _stale_template_page(fx_install, mode)
    want, want_table = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    jobs = win._genie_job_runner()
    assert not d._undo  # (the symbol step waits for the template)
    assert jobs.template_pending(d) and d.ui.steps_after_template
    jobs._poll()
    assert not jobs.busy(d) and not d.ui.steps_after_template
    second = "update symbols from library" if mode == "symbol" else "place genies"
    assert _labels(d) == ["template %s re-applied" % SHOWN, second]
    assert d.template_status()[0] == "current"
    assert not _dups(d) and _handles(d) == want
    assert [w for w, _c in d.ui.auto_changes][0] == "template %s re-applied" % SHOWN


def test_a_symbol_step_asked_while_the_template_is_in_the_worker_waits_for_it(
    fx_install, win, monkeypatch, tmp_path
):
    """The tab's own look for changed symbols (a tab shown, the idle wait, a symbol saved in
    another tab) while the open's template step is in the worker: it waits, and the template
    comes first."""
    pp = _stale_template_page(fx_install, "symbol")
    want, _t = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    assert win._bring_up_to_date(d, gate=False) == [] and not d._undo
    assert not win._follow_symbols_in_tabs() and not d._undo
    win._genie_job_runner()._poll()
    assert _labels(d)[0] == "template %s re-applied" % SHOWN
    assert _handles(d) == want and not _dups(d)


def test_a_genie_saved_while_the_template_is_in_the_worker_is_composed_after_it(
    fx_install, win, win2, monkeypatch, tmp_path
):
    """fx_value is saved in another window while the open's template step of a page that
    places it is in the worker: the save's update of the page waits for the template, and
    the page's genies are composed after it (CtDraw's load order, the handles it gives)."""
    from ctgedit.editing import EditableDocument, libraries_changed

    pp = os.path.join(fx_install.project("FxMain"), "FxOrder.ctg")
    p = EditableDocument.new_from_template(TEMPLATE, pp, linked=True)
    p.place_library_element("fxgenie", "fx_value", "ctm", (100, 300), params={"Description": "P1"})
    p.save()
    libraries_changed()
    _gains_a_record(fx_install.file("FxStyle", "fx_style.ctt"), "normal@XGA", (500, 500, 540, 540))
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    win2._open_element(fx_install.file("FxLib", "fxgenie.ctm"), "fx_value")
    win2.doc.create_object("rectangle", (0, 40, 10, 50), colour=0x00FF00)
    assert win2._save_element_to()
    assert not d._undo  # (the save's update waits for the template)
    want, _t = _ctdraw_order(pp)
    jobs = win._genie_job_runner()
    jobs._poll()  # (the template lands; the genie job is sent)
    jobs._poll()  # (the genies land)
    assert _labels(d) == ["template %s re-applied" % SHOWN, "update genies from library"]
    assert _handles(d) == want and not _dups(d) and not d.genie_changes()


def test_the_template_tasks_cancel_runs_the_steps_that_waited(fx_install, win, monkeypatch, tmp_path):
    pp = _stale_template_page(fx_install, "symbol")
    _worker_here(monkeypatch, tmp_path)
    assert win.open_page(pp)
    d = win.doc
    assert not d._undo  # (the symbol step waits for the template)
    jobs = win._genie_job_runner()
    job = next(j for j in list(jobs._jobs.values()) + list(jobs._queue) if j.template)
    jobs.stop_job(job)
    assert not jobs.busy(d) and not d.ui.steps_after_template
    assert _labels(d) == ["update symbols from library"]
    assert d.template_status()[0] == "stale"  # (the user's Cancel)
    assert not d.stale_symbol_elements()


def test_a_tasks_cancel_stops_that_job_alone(fx_install, win, monkeypatch, tmp_path):
    """A genie dialog's OK queued behind the open's template step, cancelled from its task:
    the template step goes on and the steps that wait for it run when it lands. The other
    way round, the template's Cancel keeps the dialog's OK."""
    from ctgedit.ui.jobs import genie_jobs as gj

    pp = _stale_template_page(fx_install, "symbol")
    want, _t = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    jobs = win._genie_job_runner()
    jobs.submit_bindings(d, 0, {}, "a genie")  # (queued: one job at a time a page)
    dialog = next(j for j in jobs._queue if j.bindings is not None)
    jobs.stop_job(dialog)
    assert jobs.template_pending(d) and d.ui.steps_after_template
    jobs._poll()
    assert _labels(d) == ["template %s re-applied" % SHOWN, "update symbols from library"]
    assert _handles(d) == want and not d.ui.steps_after_template
    # the template's Cancel keeps a dialog's OK queued behind it
    other = gj.GenieJobs()
    try:
        monkeypatch.setattr(other, "_start_more", lambda: None)  # (all stay queued)
        assert other.submit_template(d, TEMPLATE)
        other.submit_bindings(d, 0, {}, "a genie")
        tpl = next(j for j in other._queue if j.template)
        other.stop_job(tpl)
        assert [j.bindings is not None for j in other._queue] == [True]
    finally:
        other.shutdown()


def test_a_tab_moved_while_its_template_is_in_the_worker_keeps_the_order(
    fx_install, win, win2, monkeypatch, tmp_path
):
    pp = _stale_template_page(fx_install, "symbol")
    want, _t = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    win._move_entry_to(win._docs[0], win2)
    assert win2.doc is d and not d._undo
    jobs2 = win2._genie_job_runner()
    assert jobs2.template_pending(d) and d.ui.steps_after_template
    jobs2._poll()
    assert _labels(d) == ["template %s re-applied" % SHOWN, "update symbols from library"]
    assert _handles(d) == want and d.template_status()[0] == "current"


def test_update_pages_without_templates_does_the_pending_template_first(
    fx_install, win, monkeypatch, tmp_path
):
    """Update Pages with Templates unticked while the open's template job is pending: the job
    is dropped and the run's step does the template first, as an automatic change, then its
    symbols."""
    pp = _stale_template_page(fx_install, "symbol")
    want, want_table = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    d = win.doc
    jobs = win._genie_job_runner()
    assert jobs.busy(d)
    res = win._update_open_documents(
        [os.path.dirname(pp)], {"genies": True, "templates": False, "symbols": True}
    )
    assert d.template_status()[0] == "current"
    assert _handles(d) == want and not _dups(d)
    assert not jobs.busy(d) and not d.ui.steps_after_template and len(d._undo) == 2
    # (nothing on it was the user's: updated and saved in its tab)
    assert res.saved == [d.path] and not res.updated and not d.dirty
    assert res.templates == 0 and res.symbols == 1
    assert [w for w, _c in d.ui.auto_changes] == ["template %s re-applied" % SHOWN]
    jobs._poll()
    assert len(d._undo) == 2  # (nothing lands after the run)


def test_update_pages_with_nothing_ticked_does_the_opens_steps_as_automatic(
    fx_install, win, monkeypatch, tmp_path
):
    """Nothing ticked: the open's template and symbol steps are still done in their order,
    told as the automatic update, not counted as the run's."""
    pp = _stale_template_page(fx_install, "symbol")
    want, _t = _ctdraw_order(pp)
    _worker_here(monkeypatch, tmp_path)
    assert win.open_page(pp)
    d = win.doc
    res = win._update_open_documents(
        [os.path.dirname(pp)], {"genies": False, "templates": False, "symbols": False}
    )
    assert _handles(d) == want and d.template_status()[0] == "current"
    # (nothing on it was the user's: updated and saved in its tab)
    assert res.saved == [d.path] and not res.auto and not res.updated and not d.dirty
    assert res.templates == 0 and res.symbols == 0
    assert [w for w, _c in d.ui.auto_changes] == [
        "template %s re-applied" % SHOWN,
        "symbols updated from their library (fxsym.fx_box)",
    ]


@pytest.mark.parametrize("mode", ["symbol", "placeholder"])
def test_the_open_runs_the_template_inline_first_when_it_takes_page_properties(
    fx_install, win, mode, monkeypatch
):
    """[Animator] TemplateUpdate on: the open's template step runs inline (it also sets the
    page's properties), first, and names the template as its library spells it."""
    from ctgedit.ui.main_window import MainWindow

    pp = _stale_template_page(fx_install, mode)
    monkeypatch.setattr(MainWindow, "_template_takes_page_properties", staticmethod(lambda: True))
    assert win.open_page(pp)
    d = win.doc
    second = "update symbols from library" if mode == "symbol" else "place genies"
    assert len(_labels(d)) == 2 and _labels(d)[1] == second
    assert [w for w, _c in d.ui.auto_changes][0] == "template %s re-applied" % SHOWN
    assert d.template_status()[0] == "current"


def test_a_closed_windows_jobs_leave_no_helper_busy(fx_install, win, monkeypatch, tmp_path):
    """A window closed with a genie job running leaves no helper counted as busy, so the
    next .ctF check can start."""
    from ctgedit.infra import workers

    pp = _stale_template_page(fx_install, "symbol")
    _worker_here(monkeypatch, tmp_path, by_hand=True)
    assert win.open_page(pp)
    jobs = win._genie_job_runner()
    assert jobs.busy() and workers.others_busy("ctf")
    jobs.shutdown()
    assert not workers.others_busy("ctf")
