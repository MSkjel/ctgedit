"""UI speed-ups keep the same behaviour."""

import os
import shutil
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import fxinstall
from qtkit import app as _app, canvas_picture


@pytest.fixture()
def fx(fx_install):
    """The pages and projects the tests use, in the test's copy of the fake install."""

    class Fx:
        LARGE = fx_install.page("FxLarge", "FxPerf")
        SMALL = fx_install.page("FxShapes")
        COMPOSITES = fx_install.page("FxComposites")
        MAIN = fx_install.project("FxMain")
        SIM = fx_install.project("FxSim")
        SA = fx_install.project("FxSa")
        install = fx_install

    return Fx


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    yield w
    for e in w._docs:
        e["doc"].dirty = False
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)
    w.hide()


def _pump(until=None, timeout=15.0):
    app = _app()
    t = time.perf_counter()
    while time.perf_counter() - t < timeout:
        app.processEvents()
        if until is None or until():
            return True
        time.sleep(0.01)
    return False


def _page_pair(tmp_path, fx, monkeypatch):
    """A copy of FxShapes in a (stub) project with the .ctF the writer builds for it (in
    place of CtDraw's: a trusted pair); its libraries resolve in the fake install's FxMain."""
    from ctgedit import editing as E
    from ctgedit.format import ctf as CTF

    monkeypatch.setenv("CTG_LIB_PATH", fx.MAIN)
    g = tmp_path / "FxShapes.ctg"
    shutil.copyfile(fx.SMALL, g)
    (tmp_path / "pages.DBF").write_bytes(b"")
    f = tmp_path / "FxShapes.ctF"
    f.write_bytes(CTF.build_ctf(g.read_bytes(), str(g), resolver=E._page_resolver(str(g))))
    return str(g), str(f)


# ------------------------------------------------------------ the .ctF check
def test_ctf_matches_masks_only_the_write_stamps(fx):
    """ctf_matches masks only the write stamps."""
    import struct
    from ctgedit.editing import ctf_matches
    from ctgedit.format import ctf as CTF
    from ctgedit.format.container import Container

    disk = CTF.build_ctf(open(fx.SMALL, "rb").read(), fx.SMALL)
    c = Container.parse(disk)
    page, attrs = c.block("page").offset, c.block("attributes").offset
    assert ctf_matches(disk, disk)

    def stamped(b, created=None, modified=None, identity=True):
        a = bytearray(b)
        if created is not None:
            a[4:8] = struct.pack("<I", created)
        if modified is not None:
            a[8:12] = struct.pack("<I", modified)
            if identity:
                a[page + 0x20 : page + 0x24] = struct.pack("<I", modified)
                a[attrs + 0x20 : attrs + 0x24] = struct.pack("<I", modified)
        return bytes(a)

    ts = struct.unpack_from("<I", disk, 8)[0]
    later = stamped(disk, created=ts + 2, modified=ts + 2)
    assert later != disk and ctf_matches(disk, later)
    # the identity no longer carries the modified stamp: not a CtDraw write
    assert not ctf_matches(disk, stamped(disk, modified=ts + 2, identity=False))
    # a different page VERSION (the staleness key) is never masked
    v = bytearray(later)
    v[page + 0x24] ^= 0xFF
    assert not ctf_matches(disk, bytes(v))
    # nor a record byte
    r = bytearray(later)
    r[len(r) // 2] ^= 0x01
    assert not ctf_matches(disk, bytes(r))


@pytest.mark.golden
def test_ctf_matches_the_ctdraw_pair(fx):
    """CtDraw's own pair (the oracle's PageOpen + PageSave of FxShapes, in place of the page
    in the test's copy): equal once the stamps are masked (CtDraw writes its .ctF a second
    after the .ctg). Without the classic zlib both files have their DIBs deflated by
    ctf._compress first (tests/ctfcompare.py)."""
    import ctfcompare
    from ctgedit.editing import ctf_matches
    from ctgedit.format import ctf as CTF

    ctg = fxinstall.oracle_bytes("pages", "FxMain/FxShapes", "page.ctg")
    disk = fxinstall.oracle_bytes("pages", "FxMain/FxShapes", "page.ctF")
    with open(fx.SMALL, "wb") as fh:
        fh.write(ctg)
    ours = CTF.build_ctf(ctg, fx.SMALL)
    assert ctf_matches(*ctfcompare.comparable(ours, disk))


def test_ctf_check_runs_in_a_process_started_on_request(tmp_path, monkeypatch, fx):
    """Nothing runs at open (no thread); the check process starts when the
    editor asks (idle), one at a time, and its verdict is the in-process one;
    a save then rebuilds the .ctF."""
    from ctgedit import editing as E
    from ctgedit.format import ctf as CTF

    monkeypatch.delenv("CTG_NO_CTF_PRECHECK", raising=False)
    g, f = _page_pair(tmp_path, fx, monkeypatch)
    threads = threading.active_count()
    doc = E.EditableDocument.open(g)
    assert threading.active_count() == threads  # nothing started at open
    box = doc._ctf_pre
    assert box and isinstance(box["ctg"], str) and box["ok"] is None  # sha1s, no bytes
    assert doc.ctf_precheck_pending()
    # a second page's check waits while the first one runs
    other = tmp_path / "other"
    other.mkdir()
    g2, _f2 = _page_pair(other, fx, monkeypatch)
    doc2 = E.EditableDocument.open(g2)
    assert doc.start_ctf_precheck()
    proc = box["thread"].proc
    assert proc is not None and proc.pid != os.getpid()
    if proc.poll() is None:
        assert doc2.start_ctf_precheck() is False  # one check process at a time
    assert box["thread"].join() is True
    assert proc.returncode == 0
    inproc = E._ctf_verdict(open(g, "rb").read(), open(f, "rb").read(), g, resolver=doc._resolver())
    assert inproc is True
    assert doc2.start_ctf_precheck()  # the next one may start now
    doc2.stop_ctf_precheck()
    # the save uses the verdict: the trusted .ctF is rebuilt, not removed
    doc.create_object("rectangle", (10, 10, 60, 40), colour=0x2080FF)
    doc.save()
    assert "fast display rebuilt" in doc.save_report
    res = E._page_resolver(g)
    assert open(f, "rb").read() == CTF.build_ctf(open(g, "rb").read(), g, resolver=res)


def test_ctf_verdict_is_only_about_the_bytes_it_checked(tmp_path, monkeypatch, fx):
    from ctgedit import editing as E

    monkeypatch.delenv("CTG_NO_CTF_PRECHECK", raising=False)
    g, f = _page_pair(tmp_path, fx, monkeypatch)
    doc = E.EditableDocument.open(g)
    ctg, ctf = open(g, "rb").read(), open(f, "rb").read()
    assert doc._ctf_precheck_result(ctg + b"x", ctf) is None  # other bytes
    # the .ctF changed on disk before the check ran: no verdict at all
    open(f, "wb").write(ctf[:-1] + bytes([ctf[-1] ^ 1]))
    assert doc._ctf_precheck_result(ctg, ctf) is None
    doc.stop_ctf_precheck()
    assert doc._ctf_pre is None


# ------------------------------------------------------------ page open
def test_open_shows_the_page_before_its_checks(win, monkeypatch, fx):
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "DEFER_POST_OPEN", True)
    order = []
    real = MainWindow._post_open_checks

    def checks(self, doc, path, choice):
        order.append("checks")
        return real(self, doc, path, choice)

    monkeypatch.setattr(MainWindow, "_post_open_checks", checks)

    class Paint(QtCore.QObject):
        def eventFilter(self, o, e):
            if e.type() == QtCore.QEvent.Type.Paint and "paint" not in order:
                order.append("paint")
            return False

    win.show()
    _pump()
    pf = Paint()
    win.view.viewport().installEventFilter(pf)
    win.open_document(fx.LARGE)
    assert win.doc is not None and order == []  # shown; checks still pending
    assert _pump(lambda: "checks" in order)
    assert order == ["paint", "checks"]
    assert not win._post_open_pending


def test_open_checks_run_before_user_input(win, monkeypatch, fx):
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "DEFER_POST_OPEN", True)
    ran = []
    monkeypatch.setattr(MainWindow, "_post_open_checks", lambda self, doc, path, choice: ran.append(path))
    win.open_document(fx.LARGE)
    assert ran == [] and win._post_open_pending
    ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Shift, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(win.view, ev)
    assert ran == [fx.LARGE] and not win._post_open_pending


def test_open_without_an_event_loop_checks_at_once(win, fx):
    """A script / test (no running event loop): the checks have run when open_document returns."""
    win.open_document(fx.LARGE)
    assert not win.__dict__.get("_post_open_pending")


def test_a_large_page_shows_before_its_objects_tree_and_properties(win, monkeypatch, fx):
    """A page of more records than PANELS_AFTER_PAINT: its Objects tree and Properties form
    are made right after the canvas's first paint; then they are what they would have been at once."""
    from ctgedit.ui.main_window import MainWindow
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    monkeypatch.setattr(MainWindow, "DEFER_POST_OPEN", True)
    monkeypatch.setattr(MainWindow, "PANELS_AFTER_PAINT", 100)  # (FxLarge counts as large)
    order = []

    class Paint(QtCore.QObject):
        def eventFilter(self, o, e):
            if e.type() == QtCore.QEvent.Type.Paint and "paint" not in order:
                order.append(("paint", len(win.objects_dock._items)))
            return False

    win.show()
    _pump()
    pf = Paint()
    win.view.viewport().installEventFilter(pf)
    win.open_document(fx.LARGE)
    assert win.doc is not None and not win.objects_dock._items
    assert not win.props.widget().findChildren(QtWidgets.QGroupBox)  # (nothing yet)
    assert _pump(lambda: win.objects_dock._items and not win._post_open_pending)
    assert order[0] == ("paint", 0)  # the page painted first
    fresh = ObjectsDock()
    fresh.set_page(win.doc.page, [], hidden=win._hidden_roots)
    assert sorted((i, it.text(0)) for i, it in win.objects_dock._items.items()) == sorted(
        (i, it.text(0)) for i, it in fresh._items.items()
    )
    assert any(b.title() == "Page" for b in win.props.widget().findChildren(QtWidgets.QGroupBox))
    assert win.objects_dock.shown[0] is win.doc
    win.doc.dirty = False


def test_a_small_page_shows_its_panels_at_once(win, monkeypatch, fx):
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "DEFER_POST_OPEN", True)
    win.show()
    _pump()
    win.open_document(fx.LARGE)  # (1,191 records: under PANELS_AFTER_PAINT)
    assert win.objects_dock._items and win.objects_dock.shown[0] is win.doc
    assert any(b.title() == "Page" for b in win.props.widget().findChildren(QtWidgets.QGroupBox))
    _pump(lambda: not win._post_open_pending)
    win.doc.dirty = False


# ------------------------------------------------------------ session restore
def _session(paths, last):
    from ctgedit.ui.prefs.settings import app_settings

    st = app_settings()
    st.setValue("session/open_files", list(paths))
    st.setValue("session/last_file", last)


def test_restored_session_opens_only_the_active_page(win, fx):
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.main_window import _PendingPage

    _session([fx.LARGE, fx.COMPOSITES, fx.SMALL], fx.COMPOSITES)
    assert win.restore_session(reopen=True)
    # the tabs in the session's order: the active page last
    assert [os.path.basename(e["doc"].path) for e in win._docs] == [
        "FxLarge.ctg",
        "FxShapes.ctg",
        "FxComposites.ctg",
    ]
    assert [type(e["doc"]) for e in win._docs] == [_PendingPage, _PendingPage, EditableDocument]
    assert win.doc is win._docs[2]["doc"] and win.doc_tabs.currentIndex() == 2
    assert [win.doc_tabs.tabText(k) for k in range(3)] == ["FxLarge", "FxShapes", "FxComposites"]
    # a placeholder tab opens when shown
    win.doc_tabs.setCurrentIndex(0)
    assert isinstance(win._docs[0]["doc"], EditableDocument)
    assert os.path.basename(win.doc.path) == "FxLarge.ctg"
    # the session keeps every tab, opened or not
    win.save_prefs()
    from ctgedit.ui.prefs.settings import app_settings

    keep = [os.path.basename(p) for p in app_settings().value("session/open_files", [], type=list)]
    assert keep == ["FxLarge.ctg", "FxShapes.ctg", "FxComposites.ctg"]
    # a search over every open page reads the rest
    docs = win.open_documents()
    assert all(isinstance(d, EditableDocument) for d in docs) and len(docs) == 3


def test_a_placeholder_tab_closes_and_a_vanished_page_is_reported(win, tmp_path, monkeypatch, fx):
    from ctgedit.ui.main_window import _PendingPage

    gone = tmp_path / "Gone.ctg"
    shutil.copyfile(fx.SMALL, gone)
    _session([str(gone), fx.LARGE], fx.LARGE)
    assert win.restore_session(reopen=True)
    assert isinstance(win._docs[0]["doc"], _PendingPage)
    os.remove(gone)  # deleted while not yet opened
    seen = []
    monkeypatch.setattr(win, "report_open_failures", lambda f: seen.extend(f))
    win.doc_tabs.setCurrentIndex(0)
    assert [os.path.basename(p) for p, _e in seen] == ["Gone.ctg"]
    assert len(win._docs) == 1 and os.path.basename(win.doc.path) == "FxLarge.ctg"
    assert win.doc_tabs.count() == 1
    # a placeholder tab closes like any tab (copies: a page open in another window is
    # brought forward, not opened twice)
    a, b = tmp_path / "P1.ctg", tmp_path / "P2.ctg"
    shutil.copyfile(fx.SMALL, a)
    shutil.copyfile(fx.LARGE, b)
    _session([str(a), str(b)], str(b))
    other = type(win)()
    try:
        other.restore_session(reopen=True)
        assert other._close_doc_tab(0) and len(other._docs) == 1
    finally:
        for e in other._docs:
            e["doc"].dirty = False
        type(win)._instances.remove(other)


def test_app_start_shows_the_window_before_the_session_opens(monkeypatch, fx):
    app = _app()
    from ctgedit.app import start_window
    from ctgedit.ui.main_window import MainWindow

    _session([fx.LARGE, fx.SMALL], fx.SMALL)
    after = []
    w = start_window(["ctgedit"], [], defer=True, then=lambda: after.append(True))
    try:
        assert w.isVisible() and w.doc is None and len(w._docs) == 2
        assert not w.start_page.isVisible()  # the tabs are there: no start page
        assert after == []
        assert _pump(lambda: w.doc is not None and after)
        assert os.path.basename(w.doc.path) == "FxShapes.ctg"
        # command-line pages: the window first too (a page no other window has: one open
        # in another window is brought forward instead)
        import tempfile

        cmd_page = os.path.join(tempfile.mkdtemp(), "Cmd.ctg")
        shutil.copyfile(fx.LARGE, cmd_page)
        w2 = start_window(["ctgedit"], [cmd_page], defer=True)
        try:
            assert w2.isVisible() and w2.doc is None
            # (not the start page while the named page is read)
            assert not w2.start_page.isVisible()
            assert _pump(lambda: w2.doc is not None)
        finally:
            MainWindow._instances.remove(w2)
            w2.hide()
    finally:
        for x in (w,):
            for e in x._docs:
                e["doc"].dirty = False
            if x in MainWindow._instances:
                MainWindow._instances.remove(x)
            x.hide()
        app.processEvents()


def test_the_editor_tunes_the_gil_before_its_window():
    """The editor tunes the GIL before its window."""
    import inspect
    import sys
    from ctgedit import app as A

    src = inspect.getsource(A._run_editor)
    assert src.index("tune_gil()") < src.index("QApplication(")
    old = sys.getswitchinterval()
    try:
        A.tune_gil()
        assert sys.getswitchinterval() == pytest.approx(A.GUI_SWITCH_INTERVAL)
        if sys.platform == "win32":
            assert A._timer_period == [1]
            A.tune_gil()  # (asked for once)
            assert A._timer_period == [1]
        A.clean_exit()
        assert A._timer_period == []
    finally:
        sys.setswitchinterval(old)


# ------------------------------------------------------------ one event filter
def test_one_application_filter_runs_the_three_handlers(win, monkeypatch):
    app = _app()
    f = app._ctg_app_filter
    names = [type(h).__name__ for h, _t in f._handlers]
    # (the canvas's Space pan, a rung added once a canvas exists, keys only)
    assert "SpacePanForwarder" in names
    names = [n for n in names if n != "SpacePanForwarder"]
    # the order Qt called them as three filters (the last installed first);
    # a standalone accessibility.install() of an earlier test keeps its own
    assert names[-2:] == ["_F1Filter", "_WheelGuard"]
    assert app._ctg_wheel_guard in [h for h, _t in f._handlers]
    # the wheel guard: an unfocused spin box keeps its value
    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    field = QtWidgets.QLineEdit(box)
    spin = QtWidgets.QSpinBox(box)
    lay.addWidget(field)
    lay.addWidget(spin)
    spin.setValue(5)
    box.show()
    _pump()
    field.setFocus()
    _pump()
    assert not spin.hasFocus()
    wheel = QtGui.QWheelEvent(
        QtCore.QPointF(5, 5),
        QtCore.QPointF(spin.mapToGlobal(QtCore.QPoint(5, 5))),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, 120),
        QtCore.Qt.NoButton,
        QtCore.Qt.NoModifier,
        QtCore.Qt.NoScrollPhase,
        False,
    )
    QtWidgets.QApplication.sendEvent(spin, wheel)
    assert spin.value() == 5
    # F1 opens the help (through the combined filter)
    from ctgedit.ui import help as H

    shown = []
    monkeypatch.setattr(H, "show_help", lambda topic, main=None: shown.append(topic))
    key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_F1, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(spin, key)
    assert shown
    # accessible names follow a new widget's tool tip
    from ctgedit.ui.style import accessibility as A

    b = QtWidgets.QToolButton(box)
    b.setIcon(QtGui.QIcon(QtGui.QPixmap(8, 8)))
    b.setToolTip("Zoom to fit")
    b.show()
    _pump()
    assert A.accessible_name(b) == "Zoom to fit"
    box.close()
    box.deleteLater()
    _pump()


# ------------------------------------------------------------ docks
def test_tags_dock_reads_a_project_once_and_only_when_shown(monkeypatch, fx):
    ONE, TWO = fx.MAIN, fx.SIM
    _app()
    from ctgedit.io import project as P
    from ctgedit.ui.panels import tags_dock as TD

    P.clear_scope_caches()
    builds = []
    real = P.TagIndex

    class Counted(real):
        def __init__(self, *a, **k):
            builds.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(P, "TagIndex", Counted)
    d = TD.TagsDock()
    d.set_project_dir(ONE)  # hidden: nothing read
    assert builds == []
    names = d.tag_names()  # asked for: read once
    assert names and len(builds) == 1
    d.set_project_dir(ONE)
    d.set_project_dir(TWO)
    assert len(builds) == 1  # hidden again: not read
    assert d.tag_names() and len(builds) == 2
    d.set_project_dir(ONE)
    assert d.tag_names() == names and len(builds) == 2  # cached
    # shown: the list follows the project (after the page's paint)
    d.show()
    d.set_project_dir(TWO)
    assert _pump(lambda: d._loaded_key == TD._tag_key(TWO))
    assert d._model.rowCount() == len(d.tag_names())
    d.close()


def test_library_panel_is_a_no_op_for_the_same_project(monkeypatch, fx):
    ONE, TWO = fx.MAIN, fx.SA
    _app()
    from ctgedit import editing as E
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(320, 800)
    p.set_project_dir(ONE)
    p.ensure_loaded()
    rebuilt = []
    real = LibraryPanel._rebuild
    monkeypatch.setattr(
        LibraryPanel, "_rebuild", lambda self, *a, **k: rebuilt.append(1) or real(self, *a, **k)
    )
    p.set_project_dir(ONE)  # a page re-open / tab switch
    assert not p._dirty
    p.ensure_loaded()
    assert rebuilt == []
    E.libraries_changed()  # a library was saved here
    p.set_project_dir(ONE)
    assert p._dirty
    p.ensure_loaded()
    assert rebuilt == [1]
    # shown: a real rebuild waits for the next event-loop turn
    p.show()
    _pump()
    rebuilt.clear()
    p.set_project_dir(TWO)
    assert rebuilt == [] and p._load_timer.isActive()
    assert _pump(lambda: rebuilt)
    p.close()


def test_library_grid_batched_layout_ends_where_a_single_pass_does(fx):
    """(FxSa gets a generated library of 500 symbols: more cards than one batch.)"""
    from ctgedit.editing import libraries_changed

    fx.install.big_library("FxSa", "fxbulk.ctl", 500)
    libraries_changed()
    TWO = fx.SA
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(320, 800)
    p.set_project_dir(TWO)
    p.ensure_loaded()
    p.set_kind("symbol")
    p.show()
    grid = p.grid_view()
    assert grid.layoutMode() == QtWidgets.QListView.Batched
    n = p._model.rowCount()
    assert n > grid.BATCH  # more rows than one batch
    _pump(timeout=2.0)
    for _ in range(50):
        _pump(timeout=0.05)
    batched = [grid.visualRect(p._model.index(r, 0)) for r in range(n)]
    grid.full_layout()
    single = [grid.visualRect(p._model.index(r, 0)) for r in range(n)]
    assert batched == single
    p.close()


def test_objects_tree_selection_matches_a_full_reselect(fx):
    _app()
    import random
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc = EditableDocument.open(fx.LARGE)
    dock = ObjectsDock()
    dock.set_page(doc.page, [])
    idx = list(dock._items)
    rnd = random.Random(7)
    for _ in range(25):
        want = rnd.sample(idx, rnd.randint(0, 6))
        dock.set_selection(want)
        got = sorted(it.data(0, QtCore.Qt.UserRole) for it in dock._tree.selectedItems())
        assert got == sorted(want)
        if want:
            first = next(i for i in dock._items if i in set(want))
            assert dock._tree.currentItem() is dock._items[first]


def test_nudge_leaves_the_objects_tree_and_matches_a_full_refresh(win, fx):
    win.show()
    win.open_document(fx.LARGE)
    _pump()
    doc = win.doc
    root = next(
        r
        for r in doc.page.roots()
        if not win._unit_locked(r)
        and not doc.page.objects[r].raw.get("_non_drawable")
        and doc.page.objects[r].kind.value == "rectangle"
    )
    win._on_canvas_selection([root])
    win.view.set_selection([root])
    calls = []
    for name in ("set_page", "update_units"):
        real = getattr(win.objects_dock, name)
        setattr(win.objects_dock, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))
    before = doc.objects[root].body_raw
    win.nudge(1, 0)
    win.commit_nudge()  # (an arrow-key burst reaches the page in one move)
    assert doc.objects[root].body_raw != before and calls == []
    _pump()
    moved = canvas_picture(win.view)
    win.scene.shown = None  # a full rebuild of the scene
    win._refresh(rebuild_tree=False)
    _pump()
    assert canvas_picture(win.view) == moved
    doc.dirty = False


# ------------------------------------------------------------ small helpers
def test_pids_of_needs_no_tasklist(monkeypatch):
    import sys
    from ctgedit.backend import winui

    if sys.platform != "win32":
        pytest.skip("Windows only")

    def refuse(*a, **k):
        raise AssertionError("tasklist spawned")

    monkeypatch.setattr(winui.subprocess, "run", refuse)
    image = os.path.basename(sys.executable)
    assert os.getpid() in winui.pids_of(image)
    assert os.getpid() in winui.parent_pids(image)


def test_open_pages_enumerates_nothing_without_a_builder_window(monkeypatch, tmp_path):
    from ctgedit.backend import graphics_builder as gb

    monkeypatch.setattr(gb, "_builder_window", lambda: False)
    monkeypatch.setattr(gb, "_ctdraw_pids", lambda: (_ for _ in ()).throw(AssertionError("enumerated")))
    assert gb.open_pages() == []
    assert gb.page_open_in_builder(str(tmp_path / "FxShapes.ctg")) is False


def test_composite_links_are_read_lazily(fx):
    from ctgedit.editing import EditableDocument
    from ctgedit.format import composer_store as CS

    for path in (fx.LARGE, fx.COMPOSITES):
        data = open(path, "rb").read()
        doc = EditableDocument.open(path)
        if b"\\{" in data:
            assert doc.__dict__.get("_composite_open_data") is not None
            assert "_composite_seen" not in doc.__dict__
        else:
            assert doc.__dict__.get("_composite_seen") == set()
        assert doc.composite_links() == CS.links_in(data) | CS.links_in(doc.serialize())
        assert "_composite_open_data" not in doc.__dict__
    assert CS.links_in(open(fx.COMPOSITES, "rb").read())  # FxComposites places composites


def _no_boxes(monkeypatch, win):
    seen = []
    monkeypatch.setattr(win, "report_open_failures", lambda f: seen.extend(f))
    return seen


@pytest.mark.parametrize("defer", [False, True])
def test_restore_shows_the_last_tab_when_the_active_page_is_gone(win, tmp_path, monkeypatch, defer, fx):
    """The active page no longer exists: the LAST remaining tab is shown (addTab alone would leave
    the FIRST tab current)."""
    from ctgedit.ui.main_window import _PendingPage

    seen = _no_boxes(monkeypatch, win)
    gone = str(tmp_path / "Gone.ctg")
    _session([fx.LARGE, fx.SMALL, gone], gone)
    win.restore_session(reopen=True, defer=defer)
    # (the failure report is queued for after the window shows)
    assert _pump(lambda: win.doc is not None and seen)
    assert os.path.basename(win.doc.path) == "FxShapes.ctg"
    assert win.doc_tabs.currentIndex() == 1
    assert isinstance(win._docs[0]["doc"], _PendingPage)
    assert [os.path.basename(p) for p, _e in seen] == ["Gone.ctg"]


def test_closing_the_open_tab_beside_a_vanished_placeholder(win, tmp_path, monkeypatch, fx):
    """Closing the only opened tab shows the next one; when that restored
    page no longer opens, no page is left -- the window must not keep the
    closed page as its document (an edit / save would write a closed page)."""
    seen = _no_boxes(monkeypatch, win)
    gone = tmp_path / "Gone.ctg"
    shutil.copyfile(fx.SMALL, gone)
    _session([str(gone), fx.LARGE], fx.LARGE)
    win.restore_session(reopen=True)
    os.remove(gone)
    assert win._close_doc_tab(1)
    assert win._docs == [] and win.doc is None and win.doc_tabs.count() == 0
    assert [os.path.basename(p) for p, _e in seen] == ["Gone.ctg"]
    # closing a NON-current tab whose neighbour placeholder fails: no IndexError
    gone2 = tmp_path / "Gone2.ctg"
    shutil.copyfile(fx.SMALL, gone2)
    _session([fx.LARGE, fx.COMPOSITES, str(gone2)], fx.LARGE)
    other = type(win)()
    monkeypatch.setattr(other, "report_open_failures", lambda f: None)
    try:
        other.restore_session(reopen=True)
        order = [os.path.basename(e["doc"].path) for e in other._docs]
        assert order == ["FxComposites.ctg", "Gone2.ctg", "FxLarge.ctg"]
        os.remove(gone2)
        other.doc_tabs.setCurrentIndex(2)
        assert other._close_doc_tab(0)  # FxComposites, not shown
        # (a tab closed in the background leaves the page shown as it is: the
        # placeholder is not opened, so it stays until it is shown)
        assert os.path.basename(other.doc.path) == "FxLarge.ctg"
        assert other.doc_tabs.currentIndex() == 1
        assert [os.path.basename(e["doc"].path) for e in other._docs] == ["Gone2.ctg", "FxLarge.ctg"]
    finally:
        for e in other._docs:
            e["doc"].dirty = False
        type(win)._instances.remove(other)


def test_objects_tree_selection_also_clears_rows_the_filter_hides(fx):
    """selectedItems() leaves rows the filter hides out: they must still be
    deselected, or they come back selected when the filter is cleared."""
    _app()
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.panels.objects_dock import ObjectsDock

    doc = EditableDocument.open(fx.LARGE)
    dock = ObjectsDock()
    dock.set_page(doc.page, [])
    a, b = list(dock._items)[3], list(dock._items)[9]
    dock.set_selection([a])
    dock._search.setText("zzz-no-such-object")
    dock._apply_filter(dock._search.text())
    assert dock._items[a].isHidden()
    dock.set_selection([b])
    dock._search.setText("")
    dock._apply_filter("")
    sel = sorted(it.data(0, QtCore.Qt.UserRole) for it in dock._tree.selectedItems())
    assert sel == [b]


def test_element_save_opens_the_restored_tabs_that_place_it(win, fx):
    """A genie / symbol / super genie save updates the open pages placing it;
    a restored tab not shown yet is opened for that when its page names the
    element (else it stays a placeholder). FxLarge places fxparts.fx_pipe and
    fxgenie.fx_pump."""
    from ctgedit.editing import EditableDocument
    from ctgedit.ui.main_window import _PendingPage

    _session([fx.LARGE, fx.SMALL], fx.SMALL)
    win.restore_session(reopen=True)
    assert isinstance(win._docs[0]["doc"], _PendingPage)
    assert b"fx_pump" in open(fx.LARGE, "rb").read().lower()
    win._open_pending_placing("no_such_element_zz")
    assert isinstance(win._docs[0]["doc"], _PendingPage)
    win._open_pending_placing("FX_Pump")  # case-insensitive
    assert isinstance(win._docs[0]["doc"], EditableDocument)
    assert os.path.basename(win.doc.path) == "FxShapes.ctg"  # the tab shown stays


def test_save_waits_for_a_running_check_process(tmp_path, monkeypatch, fx):
    """A check process still reading the page / its .ctF ends before the save
    replaces them (Windows cannot replace a file another process holds open)."""
    from ctgedit import editing as E

    monkeypatch.delenv("CTG_NO_CTF_PRECHECK", raising=False)
    g, f = _page_pair(tmp_path, fx, monkeypatch)
    doc = E.EditableDocument.open(g)
    before = open(g, "rb").read()
    check = doc._ctf_pre["thread"]
    events = []

    class Proc:
        def poll(self):
            return None if not events else 0

        def wait(self, t=None):
            events.append(("wait", open(g, "rb").read() == before))
            return 0

        def kill(self):
            pass

    check.proc = Proc()
    doc.create_object("rectangle", (10, 10, 60, 40), colour=0x2080FF)
    doc.save()
    assert events and events[0] == ("wait", True)  # the page was still the old one


def test_library_grid_scrolls_to_a_row_past_the_first_batch(fx):
    """A row the batched layout has not reached has no rectangle; scrolling
    to it (Show in Library, keyboard) lays the rest out first. (FxSa gets a generated
    library of 500 symbols.)"""
    from ctgedit.editing import libraries_changed

    fx.install.big_library("FxSa", "fxbulk.ctl", 500)
    libraries_changed()
    TWO = fx.SA
    _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(320, 600)
    p.set_project_dir(TWO)
    p.ensure_loaded()
    p.set_kind("symbol")
    p.show()
    grid = p.grid_view()
    n = p._model.rowCount()
    assert n > grid.BATCH + 50
    grid.doItemsLayout()  # a fresh batched layout
    ix = p._model.index(n - 1, 0)
    grid.scrollTo(ix)
    assert grid.visualRect(ix).isValid()
    assert grid.viewport().rect().intersects(grid.visualRect(ix))
    p.close()


def test_a_failing_filter_handler_does_not_stop_the_next(monkeypatch):
    """One handler's error is reported and the next still sees the event (the F1 dialog decoration
    after an accessible-name error)."""
    import sys

    _app()
    from ctgedit.ui.main_window import _AppEventFilter

    f = _AppEventFilter()
    seen, errors = [], []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[0]))

    class Boom:
        def handle(self, obj, ev, t):
            raise ValueError("boom")

    class Rec:
        def handle(self, obj, ev, t):
            seen.append(t)
            return False

    f.add(Rec(), {QtCore.QEvent.Type.Show})
    f.add(Boom(), {QtCore.QEvent.Type.Show})  # called first (added last)
    f.before_input = lambda: (_ for _ in ()).throw(RuntimeError("hook"))
    assert f.eventFilter(QtCore.QObject(), QtCore.QEvent(QtCore.QEvent.Type.Show)) is False
    assert seen == [QtCore.QEvent.Type.Show] and errors == [ValueError]
    key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_A, QtCore.Qt.NoModifier)
    assert f.eventFilter(QtCore.QObject(), key) is False  # the input still goes on
    assert errors == [ValueError, RuntimeError] and f.before_input is None
