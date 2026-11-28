"""Update Pages: save the library genies in the chain first."""

from ctgedit.ui.main_window import MainWindow
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
from PySide6 import QtCore, QtWidgets  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_update_pages_dialog import _dlg, _rows as _dialog_rows  # noqa: E402
from test_update_pages_parts import tmp_install  # noqa: E402


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _counting_stamps(monkeypatch):
    """The stamps count up (ctdraw_timestamp has whole seconds)."""
    from ctgedit.format import element as E

    now = [E.ctdraw_timestamp()]

    def stamp():
        now[0] += 1
        return now[0]

    monkeypatch.setattr(E, "ctdraw_timestamp", stamp)


def _element(path, name, inner=None, kind="genie", lib="chainlib"):
    """A genie (or template) ``name`` in library file ``path``: a text, and ``lib``'s
    ``inner`` placed when given."""
    from ctgedit.editing import ElementDocument

    size = dict(page_size=(800, 600)) if kind == "template" else dict(canvas=(300, 200))
    d = ElementDocument.new_element(path, name, kind, **size)
    d.create_object("text", rect=(10, 10, 120, 26), text=name.upper())
    if inner:
        assert d.place_library_element(lib, inner, "ctm", (10, 40)) is not None
    d.save()


def _change(path, name):
    """Move ``name``'s first object and save it: a new stamp its users do not quote."""
    from ctgedit.editing import ElementDocument, libraries_changed
    from ctgedit.io import genieusage as GU

    e = ElementDocument.open_element(path, name)
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    libraries_changed()
    GU.clear_cache()


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """Proj\\chainlib.ctm: genie a (changed after b was saved), b places a; d, and c places
    d (both current); Proj\\chaintpl.ctt: template t places b. Other\\otherlib.ctm (another
    project) and SA_Library\\salib.ctm (a supplied library's project) each hold a genie that
    places Proj's a."""
    _counting_stamps(monkeypatch)
    proj, other, sa = tmp_path / "Proj", tmp_path / "Other", tmp_path / "SA_Library"
    for d in (proj, other, sa):
        d.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib = str(proj / "chainlib.ctm")
    _element(lib, "a")
    _element(lib, "d")
    _element(lib, "b", "a")
    _element(lib, "c", "d")
    _element(str(proj / "chaintpl.ctt"), "t", "b", kind="template")
    _element(str(other / "otherlib.ctm"), "x", "a")
    _element(str(sa / "salib.ctm"), "s", "a")
    _change(lib, "a")
    return proj, other, sa


def _plan_labels(plan):
    return [u.ref.label for u in plan.elements]


# -- the plan -------------------------------------------------------------------------------
def test_the_chain_is_saved_deepest_first_and_the_current_ones_are_left(chain):
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    plan = GU.chain_plan([str(proj)])
    assert not plan.pages and not plan.stopped and not plan.skipped
    # b quotes a's old stamp; t quotes b's current one but places b, which is saved first
    assert _plan_labels(plan) == ["chainlib.b", "chaintpl.t"]
    b, t = plan.elements
    assert b.status == GU.OUT_OF_DATE and b.links[0].stale
    assert t.status == GU.WAITING and not t.links[0].stale
    assert t.ref.what == "template"
    # a (changed itself), d and c (c places d, current) need nothing
    assert not ({"chainlib.a", "chainlib.c", "chainlib.d"} & set(_plan_labels(plan)))


def test_other_projects_and_supplied_libraries_are_left_alone(chain):
    from ctgedit.io import genieusage as GU

    proj, other, sa = chain
    # Other's x and SA_Library's s place Proj's a too: not in Proj's chain
    assert _plan_labels(GU.chain_plan([str(proj)])) == ["chainlib.b", "chaintpl.t"]
    got = GU.chain_plan([str(proj), str(other), str(sa)])
    assert "otherlib.x" in _plan_labels(got)
    assert [(u.ref.label, why) for u, why in got.skipped] == [("salib.s", GU.SHIPPED_WHY)]
    assert "salib.s" not in _plan_labels(got)
    every = GU.chain_plan([str(proj), str(sa)], include_shipped=True)
    assert "salib.s" in _plan_labels(every) and not every.skipped


def test_a_cancelled_chain_plans_nothing(chain):
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    plan = GU.chain_plan([str(proj)], progress=lambda i, n, name: False)
    assert plan.stopped and not plan.elements


def test_the_saved_chain_is_current(chain, tmp_path):
    """Saved in the plan's order, nothing of the chain is out of date after (b's new stamp
    reaches t), and the libraries are in the backup."""
    from ctgedit.infra import backups
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    plan = GU.chain_plan([str(proj)])
    bk = str(tmp_path / "backup")
    rep = GU.run_update(plan, backup=bk, backup_kind="update-pages")
    assert rep.elements_saved == ["chainlib.b", "chaintpl.t"] and not rep.failed
    assert not GU.chain_plan([str(proj)]).elements
    m = backups.read_manifest(bk)
    assert m["kind"] == "update-pages"
    assert sorted(os.path.basename(e["original"]) for e in m["entries"]) == ["chainlib.ctm", "chaintpl.ctt"]
    info = next(b for b in backups.list_backups(str(tmp_path)) if b.path == bk)
    assert info.noun == "files"


def test_the_update_order_helper_keeps_update_users_order():
    """update_plan and chain_plan share the order (_deepest_first)."""
    from ctgedit.io import genieusage as GU

    G = GU.Ref("element", r"c:\p\lib.ctm", "g", "P")
    A = GU.Ref("element", r"c:\p\lib.ctm", "a", "P")
    B = GU.Ref("element", r"c:\p\lib2.ctm", "b", "P")
    users = [
        GU.User(B, [GU.Link(B, A, "lib.a.<ctm#00000005", 5, 5)], (), False, GU.WAITING),
        GU.User(A, [GU.Link(A, G, "lib.g.<ctm#00000001", 1, 2)], (), True, GU.OUT_OF_DATE),
    ]
    assert [u.ref.element for u in GU._deepest_first(users)] == ["a", "b"]


# -- the window ----------------------------------------------------------------------------
def test_the_chain_box_follows_the_genies_and_templates_and_is_remembered(app, tmp_path):
    """The chain box goes with the genies or the templates (a template
    built on a changed template is saved by it too), is on at first (the Graphics Builder's
    Update Pages saves an outer template first), and is
    remembered."""
    from ctgedit.ui.prefs.settings import app_settings

    user = tmp_install(tmp_path)
    dlg = _dlg(user)
    try:
        assert dlg._chain.isChecked() and dlg._chain.isEnabled()
        assert dlg.options()["chain"] is True
        dlg._chain.setChecked(False)
        assert dlg.options()["chain"] is False
        dlg._chain.setChecked(True)
        assert dlg.options()["chain"] is True
        dlg._gen.setChecked(False)
        assert dlg._chain.isEnabled() and dlg.options()["chain"] is True
        dlg._tpl.setChecked(False)
        assert not dlg._chain.isEnabled() and dlg.options()["chain"] is False
        dlg._tpl.setChecked(True)
        dlg._gen.setChecked(True)
        assert dlg._chain.isEnabled() and dlg.options()["chain"] is True
        dlg.accept()
    finally:
        dlg.deleteLater()
    assert app_settings().value("updatepages/chain") in (True, "true")
    dlg = _dlg(user)
    try:
        assert dlg._chain.isChecked()
    finally:
        dlg.deleteLater()


def _fake_report():
    return types.SimpleNamespace(
        pages=2,
        projects=["Proj"],
        stale=1,
        updated=["p1 (Proj)"],
        stopped=False,
        symbols=0,
        templates=1,
        supergenies=0,
        instances=1,
        composites=0,
        composites_skipped=0,
        failed=[],
        unregistered=[],
        open_in_builder=[],
        skipped_open=[],
        changed_meanwhile=[],
        db_in_use=[],
        rows_pending=[],
        rows_written=[],
        warnings=[],
        not_checked=0,
        errors=[],
        recovered=[],
        records=[],
        open_at_start=[],
    )


def _run(
    tmp_path,
    monkeypatch,
    chain_on,
    stopped=False,
    projects=None,
    chain_projects=None,
    busy=(),
    plan=None,
    on_plan=None,
    on_save=None,
):
    """Tools > Update Pages with a dialog that says OK (the chain box ``chain_on``;
    ``projects`` / ``chain_projects`` = its answers, default Proj; ``busy`` = the projects
    whose database is in use, the other ones are updated): the calls in order and the result
    box. ``on_plan(w)`` / ``on_save(w)`` run inside chain_plan / run_update."""
    from ctgedit.io import genieusage as GU
    from ctgedit.io import pgregister
    from ctgedit.ui.dialog import dialogs
    from ctgedit.ui.main_window import MainWindow

    pdir = tmp_path / "Proj"
    pdir.mkdir(exist_ok=True)
    calls = []
    held = {}
    projects = [str(pdir)] if projects is None else projects
    chain_projects = list(projects) if chain_projects is None else chain_projects

    class _Dlg:
        def __init__(self, start_dir, parent=None, projects=None, library_dir=None):
            pass

        def exec(self):
            return QtWidgets.QDialog.Accepted

        def options(self):
            return dict(
                templates=True, genies=True, symbols=True, backup=True, page_properties=False, chain=chain_on
            )

        def projects(self):
            return list(projects)

        def chain_projects(self):
            return list(chain_projects)

        def processes(self):
            return 1

    monkeypatch.setattr(dialogs, "UpdatePagesDialog", _Dlg)

    def probe(d):
        if d in busy:
            raise pgregister.ProjectDatabaseInUse(os.path.join(d, "pages.DBF"))

    monkeypatch.setattr(pgregister, "probe_writable", probe)
    b = GU.Ref("element", str(pdir / "chainlib.ctm"), "b", "Proj")
    if plan is None:
        plan = GU.UpdatePlan([GU.User(b, [], (), True, GU.OUT_OF_DATE)], [])

    def chain_plan(dirs, **k):
        calls.append(("plan", list(dirs)))
        if on_plan is not None:
            on_plan(held["w"])
        return plan

    monkeypatch.setattr(GU, "chain_plan", chain_plan)

    def run_update(p, **k):
        calls.append(("save", [u.ref.label for u in p.elements], list(p.pages), k))
        if on_save is not None:
            on_save(held["w"])
        rep = GU.UpdateUsersReport(
            elements_saved=[] if stopped or not p.elements else ["chainlib.b"],
            stopped=stopped,
            backup=k.get("backup"),
        )
        rep.skipped = ["%s: %s" % (u.ref.label, why) for u, why in p.skipped]
        return rep

    monkeypatch.setattr(GU, "run_update", run_update)
    shown = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *a, **k: shown.append("WARN " + a[2]) or QtWidgets.QMessageBox.Ok,
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    from ctgedit.ui import messages

    # (the busy projects' question: 'Update the Others')
    monkeypatch.setattr(messages, "choose", lambda *a, **k: a[3][0])
    # (the result window of the run: its lines, as the box showed them)
    monkeypatch.setattr(
        MainWindow,
        "_show_run_results",
        lambda self, title, lines, **k: shown.append("\n".join(lines)) or False,
    )
    w = held["w"] = MainWindow()
    w._update_pages_in_worker = lambda job, parts=None: calls.append(("pages", job)) or _fake_report()
    try:
        w._update_pages()
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)
        w.close()
        w.deleteLater()
    return calls, shown, w


def test_update_pages_saves_the_chain_before_the_run(app, tmp_path, monkeypatch):
    calls, shown, w = _run(tmp_path, monkeypatch, True)
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    _plan, labels, pages, kw = calls[1]
    assert labels == ["chainlib.b"] and pages == []
    assert kw["open_element"] == w._open_element_doc
    assert kw["backup"] and kw["backup"] == calls[2][1]["backup"]  # (the run's backup)
    assert kw["backup_kind"] == "update-pages"
    text = shown[-1]
    assert text.startswith("1 library genie saved again first: chainlib.b."), text
    assert "Backups of the changed libraries, pages" in text
    assert "(s)" not in text


def test_without_the_box_nothing_is_saved_first(app, tmp_path, monkeypatch):
    calls, shown, _w = _run(tmp_path, monkeypatch, False)
    assert [c[0] for c in calls] == ["pages"]
    assert shown[-1].startswith("2 pages checked in 1 project,"), shown[-1]


def test_cancel_while_saving_the_chain_stops_before_the_pages(app, tmp_path, monkeypatch):
    calls, shown, _w = _run(tmp_path, monkeypatch, True, stopped=True)
    assert [c[0] for c in calls] == ["plan", "save"]
    assert shown[-1].endswith("Stopped before the pages were updated."), shown


def test_the_summary_lists_the_chain_first():
    from ctgedit.io import genieusage as GU

    chain = GU.UpdateUsersReport(
        elements_saved=["l.b", "l.t"],
        elements_current=["l.c"],
        skipped=["l.e: it has unsaved changes in the editor"],
        failed=["l.f: denied"],
        super_pages=["!pop"],
    )
    rep = _fake_report()
    rep.updated = []
    lines = MainWindow._update_pages_summary(rep, "BK", (0, 0, 0, 0), chain=chain)
    # (every line ends in one full stop)
    assert lines[:5] == [
        "2 library genies saved again first: l.b, l.t.",
        "1 library genie already matched what it places: l.c.",
        "Not saved again: l.e: it has unsaved changes in the editor.",
        "Could not save l.f: denied.",
        "Super genie pages created: !pop.",
    ]
    assert lines[5].startswith("2 pages checked in 1 project,")
    assert any(x.startswith("Backups of the changed libraries: BK") for x in lines)
    # without the chain the box is as it was
    plain = MainWindow._update_pages_summary(rep, "BK", (0, 0, 0, 0))
    assert plain[0].startswith("2 pages checked") and not any("librar" in x for x in plain)


# -- the projects of the chain -------------------------------------------------------------
def test_chain_projects_add_the_projects_with_no_pages(app, tmp_path):
    """The chain reads the ticked projects and every listed project with no pages (it
    cannot be ticked); the page run keeps the ticked ones."""
    user = tmp_install(tmp_path)
    root, a, b = (os.path.join(user, n) for n in ("Root", "A", "B"))
    dlg = _dlg(user, root)
    try:
        assert dlg.projects() == [a, b]
        assert dlg.chain_projects() == [root, a, b]  # (Root has no pages)
        it = next(i for i in dlg._items() if i.text(0) == "A")
        it.setCheckState(0, QtCore.Qt.Unchecked)
        assert dlg.projects() == [b]
        assert dlg.chain_projects() == [root, b]
        dlg._none.click()
        assert dlg.projects() == [] and dlg.chain_projects() == [root]
    finally:
        dlg.deleteLater()


@pytest.fixture
def library_project(tmp_path, monkeypatch):
    """User\\{R, L}: R (one page) includes L, a project with no pages that holds the genie
    library llib.ctm: a (changed after b was saved), b places a. R\\rtpl.ctt: template t
    places L's b."""
    from test_update_pages_parts import include_dbf, pages_dbf, write_dbf

    _counting_stamps(monkeypatch)
    user = tmp_path / "Install" / "User"
    r, lp = user / "R", user / "L"
    for d in (r, lp):
        d.mkdir(parents=True)
    write_dbf(
        str(user / "MASTER.DBF"),
        [("NAME", 64), ("PATH", 240)],
        [{"NAME": n, "PATH": str(user / n)} for n in ("R", "L")],
    )
    include_dbf(str(r), ["L"])
    pages_dbf(str(r), ["p1"])
    (r / "p1.ctg").write_bytes(b"page")
    monkeypatch.setenv("CTG_LIB_PATH", str(lp))
    lib = str(lp / "llib.ctm")
    _element(lib, "a", lib="llib")
    _element(lib, "b", "a", lib="llib")
    _element(str(r / "rtpl.ctt"), "t", "b", kind="template", lib="llib")
    _change(lib, "a")
    return str(user), str(r), str(lp)


def test_a_library_project_with_no_pages_is_in_the_chain(app, library_project):
    """L's genie b (it places the changed a) and R's template t (it places b) are
    saved first, although L has no pages to tick."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.dialogs import UpdatePagesDialog

    user, r, lp = library_project
    dlg = UpdatePagesDialog(r, None, projects=[("R", r), ("L", lp)])
    try:
        assert [(x[0], x[1], x[3], x[4]) for x in _dialog_rows(dlg)] == [
            ("R", "1 page", True, True),
            ("L", "No pages", False, False),
        ]
        assert dlg.projects() == [r]
        assert dlg.chain_projects() == [r, lp]
        # (the ticked projects alone: L's library is never read, so nothing is planned)
        assert _plan_labels(GU.chain_plan(dlg.projects())) == []
        assert _plan_labels(GU.chain_plan(dlg.chain_projects())) == ["llib.b", "rtpl.t"]
    finally:
        dlg.deleteLater()


def test_update_pages_gives_the_chain_its_own_projects(app, tmp_path, monkeypatch):
    """The chain reads chain_projects() less the busy ones; the page run keeps the ticked
    ones that are not busy."""
    pdir, busy, lib = (str(tmp_path / n) for n in ("Proj", "Busy", "Lib"))
    calls, shown, _w = _run(
        tmp_path, monkeypatch, True, projects=[pdir, busy], chain_projects=[pdir, busy, lib], busy=(busy,)
    )
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    assert calls[0] == ("plan", [pdir, lib])
    assert calls[2][1]["dirs"] == [pdir]


# -- the summary ---------------------------------------------------------------------------
def test_the_summary_puts_the_skips_to_act_on_first():
    """The supplied libraries' genies are one counted line after the skips the user can act
    on; past 10 of those a line says how many more (the log has them all)."""
    from ctgedit.io import genieusage as GU

    shipped = ["sxw.g%02d: %s" % (i, GU.SHIPPED_WHY) for i in range(12)]
    chain = GU.UpdateUsersReport(
        elements_saved=["l.b"],
        skipped=shipped + ["l.w: it has unsaved changes in the editor (save it there, then update again)"],
    )
    lines = MainWindow._chain_summary(chain)
    # (a cut list ends 'and N more')
    assert lines == [
        "1 library genie saved again first: l.b.",
        "Not saved again: l.w: it has unsaved changes in the editor (save it there, then update again).",
        "12 genies in libraries supplied with Plant SCADA were left alone: sxw.g00, sxw.g01, "
        "sxw.g02, sxw.g03, sxw.g04, sxw.g05, sxw.g06, sxw.g07, sxw.g08, sxw.g09 and 2 more.",
    ]
    one = GU.UpdateUsersReport(skipped=["sxw.g: %s" % GU.SHIPPED_WHY])
    assert MainWindow._chain_summary(one) == [
        "1 genie in libraries supplied with Plant SCADA was left alone: sxw.g."
    ]
    many = GU.UpdateUsersReport(skipped=["l.e%02d: its library file is read only" % i for i in range(13)])
    lines = MainWindow._chain_summary(many)
    assert lines[:10] == ["Not saved again: l.e%02d: its library file is read only." % i for i in range(10)]
    assert lines[10] == "3 more were not saved again. The log lists them."
    assert len(lines) == 11


# -- a library that cannot be read ---------------------------------------------------------
def _norm(xs):
    return [(os.path.normcase(f), el) for f, el in xs]


def test_an_unreadable_library_is_in_the_plan(chain, monkeypatch, caplog):
    import logging
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    bad = proj / "broken.ctm"
    bad.write_bytes(b"not a library" * 20)
    GU.clear_cache()
    with caplog.at_level(logging.WARNING, logger="ctgedit.genies"):
        plan = GU.chain_plan([str(proj)])
    assert _plan_labels(plan) == ["chainlib.b", "chaintpl.t"]
    assert _norm(plan.unreadable) == [(os.path.normcase(str(bad)), "")]
    why = "it is not a Citect library, or its header is damaged"
    assert [os.path.normcase(x) for x in plan.problems] == [
        os.path.normcase("%s could not be read: %s" % (bad, why))
    ]
    # (the reason is logged once, where the file is read)
    assert any(str(bad) in r.getMessage() and why in r.getMessage() for r in caplog.records)
    # an element whose head cannot be read (no stamp, no links) is named too
    real = GU.library_read

    def read(path):
        got, err = real(path)
        got = dict(got)
        if path.lower().endswith("chainlib.ctm"):
            got["h"] = GU.ElementTable("h", None, ())
        return got, err

    monkeypatch.setattr(GU, "library_read", read)
    plan = GU.chain_plan([str(proj)])
    lib = str(proj / "chainlib.ctm")
    assert (os.path.normcase(lib), "h") in _norm(plan.unreadable)
    assert any(
        os.path.normcase(x).startswith(os.path.normcase("%s: h could not be read" % lib))
        for x in plan.problems
    )


def test_an_empty_library_is_no_problem(chain):
    """A new library (CtDraw's empty container: a header and an empty directory, 352
    bytes), one with no directory entry at all and one whose elements were all deleted read
    fine: nothing to check, no problem, in the chain and in Update Users' index."""
    from ctgedit.format.container import Container
    from ctgedit.format.element import LibraryFile
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    empty = proj / "empty.ctm"
    empty.write_bytes(LibraryFile.new(ts=7).to_bytes())
    bare = proj / "bare.ctl"
    bare.write_bytes(Container(9, 0x0C, 7, 7, 8).to_bytes())
    gone = str(proj / "gone.ctm")
    _element(gone, "x", lib="gone")
    _element(gone, "y", lib="gone")
    lf = LibraryFile.open(gone)
    assert lf.delete_element("x") and lf.delete_element("y")
    with open(gone, "wb") as fh:
        fh.write(lf.to_bytes())
    GU.clear_cache()
    assert os.path.getsize(empty) == os.path.getsize(bare) == 352
    for f in (str(empty), str(bare), gone):
        assert GU.library_tables(f) == {} and GU.library_error(f) == "", f
    plan = GU.chain_plan([str(proj)])
    assert _plan_labels(plan) == ["chainlib.b", "chaintpl.t"]
    assert plan.problems == [] and plan.unreadable == []
    idx = GU.build_index([str(proj)])
    assert idx.problems == [] and idx.unreadable == []


def test_a_truncated_or_garbage_library_is_a_problem(chain):
    """A real read failure is one: an empty (0 byte) file, one too short for a header, one
    cut inside its directory, one cut inside an element block (the elements it holds whole
    are still known to library_tables, but nothing of it is planned or saved), and garbage."""
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    data = (proj / "chainlib.ctm").read_bytes()
    files = {
        "zero.ctm": b"",
        "tiny.ctm": data[:10],
        "dir.ctm": data[:100],
        "cut.ctm": data[: len(data) - 64],
        "junk.ctl": b"\xff" * 400,
    }
    for n, b in files.items():
        (proj / n).write_bytes(b)
    GU.clear_cache()
    why = {n: GU.library_error(str(proj / n)) for n in files}
    assert why["zero.ctm"] == "the file is empty (0 bytes)"
    assert why["tiny.ctm"] == "the file is too short to be a library (10 bytes)"
    assert why["dir.ctm"].startswith("the file is truncated: it ends inside its directory")
    assert why["cut.ctm"].startswith("the file is truncated: its block ")
    assert why["junk.ctl"] == "it is not a Citect library, or its header is damaged"
    assert GU.library_tables(str(proj / "cut.ctm"))  # (what it holds whole)
    plan = GU.chain_plan([str(proj)])
    assert sorted(os.path.basename(f) for f, el in plan.unreadable if not el) == sorted(files)
    assert not any(u.ref.library == "cut" for u in plan.elements)
    assert _plan_labels(plan) == ["chainlib.b", "chaintpl.t"]


@pytest.fixture
def cut(tmp_path, monkeypatch):
    """Proj\\chainlib.ctm: genie a; Proj\\cutlib.ctm: x places a, then y; Proj\\toplib.ctm:
    z places cutlib.x. Then a changes and cutlib.ctm is cut short inside y's block (its last):
    x is still held whole."""
    from ctgedit.format import pagesummary as PS
    from ctgedit.io import genieusage as GU

    _counting_stamps(monkeypatch)
    proj = tmp_path / "Proj"
    proj.mkdir()
    monkeypatch.setenv("CTG_LIB_PATH", str(proj))
    lib, cutlib = str(proj / "chainlib.ctm"), str(proj / "cutlib.ctm")
    _element(lib, "a")
    _element(cutlib, "x", "a")
    _element(cutlib, "y")
    _element(str(proj / "toplib.ctm"), "z", "x", lib="cutlib")
    _change(lib, "a")
    last = max(PS.list_elements(cutlib), key=lambda e: e.offset)
    assert last.name.lower() == "y", last
    data = open(cutlib, "rb").read()
    with open(cutlib, "wb") as fh:
        fh.write(data[: last.offset + 40])
    GU.clear_cache()
    assert GU.library_error(cutlib).startswith("the file is truncated: its block 'y'")
    assert GU.library_tables(cutlib)["x"].names  # (held whole)
    return proj, lib, cutlib


def test_a_truncated_librarys_whole_genies_still_use_what_they_place(app, cut):
    """Used By of a lists cutlib.x (out of date) and toplib.z (through it), as before the
    file was cut. No plan saves x: Update Users
    leaves it with the reason, the chain leaves it out, and the window names the file."""
    from ctgedit.io import genieusage as GU
    from ctgedit.ui.dialog.library_dialogs import ElementUsersDialog

    proj, lib, cutlib = cut
    usage = GU.element_users(lib, "a")
    got = {(u.ref.label, u.status, bool(u.damaged)) for u in usage.users}
    assert ("cutlib.x", GU.OUT_OF_DATE, True) in got, got
    assert ("toplib.z", GU.WAITING, False) in got, got
    assert (os.path.normcase(cutlib), "") in _norm(usage.unreadable)
    plan = GU.update_plan(usage)
    assert "cutlib.x" not in _plan_labels(plan)
    assert [(u.ref.label, why) for u, why in plan.skipped] == [("cutlib.x", GU.DAMAGED_WHY)]
    chain = GU.chain_plan([str(proj)])
    assert not any(u.ref.library == "cutlib" for u in chain.elements)
    assert not any(u.ref.library == "cutlib" for u, _w in chain.skipped)
    assert (os.path.normcase(cutlib), "") in _norm(chain.unreadable)
    dlg = ElementUsersDialog(lib, "a", usage)
    try:
        assert dlg.problems_text(usage) == (
            "Some library files could not be read in full: Proj\\cutlib.ctm. "
            "Their genies are listed as far as they could be read, and Update skips them."
        )
        assert ("cutlib.x", "Out of date", "") in dlg.users()
    finally:
        dlg.close()


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows share lock")
def test_a_lock_that_passed_is_not_remembered(chain):
    """Another program holds the library with no read share: it could not be read. Once it
    lets go (the file unchanged), the next call reads it (the error is not cached)."""
    import ctypes
    from ctypes import wintypes
    from ctgedit.io import genieusage as GU

    proj, _other, _sa = chain
    lib = str(proj / "chainlib.ctm")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    GU.clear_cache()
    h = k32.CreateFileW(lib, 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no share
    assert h and h != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    try:
        assert GU.library_error(lib).startswith("it could not be read")
        assert GU.library_tables(lib) == {}
    finally:
        k32.CloseHandle(h)
    assert GU.library_error(lib) == ""
    assert set(GU.library_tables(lib)) == {"a", "b", "c", "d"}


def test_update_pages_tells_the_libraries_that_could_not_be_read(app, tmp_path, monkeypatch, caplog):
    """The box names them '<project>\\<file>', plainly, in one line; the log line has the
    full paths and the reasons."""
    import logging
    from ctgedit.io import genieusage as GU

    pdir = tmp_path / "Proj"
    b = GU.Ref("element", str(pdir / "chainlib.ctm"), "b", "Proj")
    s = GU.Ref("element", str(pdir / "sa.ctm"), "s", "Proj")
    bad = str(pdir / "broken.ctm")
    why = "%s could not be read: it is not a Citect library, or its header is damaged" % bad
    plan = GU.UpdatePlan(
        [GU.User(b, [], (), True, GU.OUT_OF_DATE)],
        [],
        skipped=[(GU.User(s, [], (), True, GU.OUT_OF_DATE), GU.SHIPPED_WHY)],
        problems=[why],
        unreadable=[(bad, ""), (str(pdir / "x.ctm"), "h")],
    )
    with caplog.at_level(logging.INFO, logger="ctgedit.update-pages"):
        calls, shown, _w = _run(tmp_path, monkeypatch, True, plan=plan)
    assert [c[0] for c in calls] == ["plan", "save", "pages"]
    text = shown[-1].split("\n")
    assert text[:3] == [
        "1 library genie saved again first: chainlib.b.",
        "Not checked, could not be read (the log says why): Proj\\broken.ctm, Proj\\x.ctm (h).",
        "1 genie in libraries supplied with Plant SCADA was left alone: sa.s.",
    ]
    logged = [r.getMessage() for r in caplog.records if "saved first" in r.getMessage()]
    assert logged and "skipped: sa.s: %s" % GU.SHIPPED_WHY in logged[-1]
    assert "not read: %s" % why in logged[-1]


# -- Cancel during the last save, and one run at a time ------------------------------------
def test_cancel_during_the_last_save_stops_before_the_pages(app, tmp_path, monkeypatch):
    """A Cancel click still queued when the last save ends (nothing in a save processes
    events) is seen: the pages are not updated."""
    from PySide6 import QtCore as QC

    def click_cancel(w):
        prog = w.findChildren(QtWidgets.QProgressDialog)[-1]
        QC.QTimer.singleShot(0, prog.cancel)  # (runs at the next event processing)

    calls, shown, _w = _run(tmp_path, monkeypatch, True, on_save=click_cancel)
    assert [c[0] for c in calls] == ["plan", "save"]
    assert shown[-1].endswith("Stopped before the pages were updated."), shown


def test_the_chain_counts_as_a_running_update(app, tmp_path, monkeypatch):
    """While the chain saves libraries, a second Update Pages (another window) and Pack
    Libraries are refused; afterwards both are free again."""
    from ctgedit.ui.jobs.update_run import UpdatePagesRun

    seen, box = {}, []

    def during(w):
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "information", lambda *a, **k: box.append(a[2]) or QtWidgets.QMessageBox.Ok
        )
        seen["busy"] = UpdatePagesRun.chain_busy
        seen["pack"] = w._update_pages_running()
        w._update_pages()  # (a second run: refused)

    calls, shown, w = _run(tmp_path, monkeypatch, True, on_plan=during)
    assert seen["busy"] is True and seen["pack"] is True
    assert "Pack the libraries when it has finished" in box[0]
    assert box[1] == "Update Pages is already running."
    assert [c[0] for c in calls] == ["plan", "save", "pages"]  # (one plan: the first run)
    assert UpdatePagesRun.chain_busy is False and UpdatePagesRun.active is None
    # (the first run's result window, after the refusals)
    assert shown[-1].startswith("1 library genie saved again first: chainlib.b."), shown
