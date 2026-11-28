"""Fixtures and hooks shared by tests/, tests_corpus/ and tests_live/."""

import gc
import os
import sys

import pytest

# the conditions the fixtures were written under (tools/make_fixtures.py), set before
# anything imports ctgedit (textcodec reads the code page at import, textmetrics keeps its
# DPI): text at 96 dpi in code page 1252 on any machine. An explicit value still wins.
os.environ.setdefault("CTG_TEXT_DPI", "96")
os.environ.setdefault("CTG_CODEPAGE", "1252")

# the hermetic guard (CTG_HERMETIC=1): imported before any test imports ctgedit, so the
# fake install and the hooks are in place first
import hermetic


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("hermetic"):
        config.pluginmanager.register(hermetic, "hermetic")
    config.addinivalue_line(
        "markers",
        "golden: needs a fact only the CtDraw capture of the fixtures gives "
        "(tests/fixtures/oracle); skipped until that capture is committed",
    )


def pytest_sessionfinish(session, exitstatus):
    """Empty the clipboard before exit, to avoid a PySide6 crash at shutdown."""
    qtw = sys.modules.get("PySide6.QtWidgets")
    app = qtw.QApplication.instance() if qtw is not None else None
    if app is not None:
        try:
            app.clipboard().clear()
            app.processEvents()
        except RuntimeError:
            pass


def _top_level_widgets() -> dict:
    """{C++ address: widget} of the top-level widgets Python owns (made without a parent
    in Python; a popup Qt owns, such as a completer's, is its owner's to delete)."""
    qtw = sys.modules.get("PySide6.QtWidgets")
    app = qtw.QApplication.instance() if qtw is not None else None
    if app is None:
        return {}
    from shiboken6 import Shiboken

    return {Shiboken.getCppPointer(w)[0]: w for w in app.topLevelWidgets() if Shiboken.ownedByPython(w)}


@pytest.fixture(autouse=True, scope="module")
def _free_process_caches():
    """Free the editor's process-wide caches between tests, and delete the top-level widgets
    the module left (panels and popups made without a parent): each one would be restyled
    by every later theme change in the process."""
    before = set(_top_level_widgets())
    yield
    import os

    # an editor window / the page browser made the GUI thread the only collector
    # (infra.gcguard): the next module gets the automatic collector back
    gg = sys.modules.get("ctgedit.infra.gcguard")
    if gg is not None:
        gg.uninstall()
    if os.environ.get("CTG_TEST_KEEP_CACHES") == "1":
        return
    mods = sys.modules
    # every registered process cache (infra/caches.py, event 'tests')
    cc = mods.get("ctgedit.infra.caches")
    if cc is not None:
        cc.clear("tests")
    # the application's Simulate window (hidden, not deleted, on close): the next module
    # starts without it
    sw = mods.get("ctgedit.ui.simulate.window")
    if sw is not None:
        for w in list(getattr(sw, "_WINDOWS", [])):
            try:
                w.hide()
                w.deleteLater()
            except RuntimeError:
                pass
        getattr(sw, "_WINDOWS", []).clear()
    mw = mods.get("ctgedit.ui.main_window")
    if mw is not None:
        for w in list(getattr(mw.MainWindow, "instances", [])):
            try:
                mw.MainWindow.instances.remove(w)
                w.hide()
                w.deleteLater()
            except RuntimeError, ValueError:
                pass
    left = [w for p, w in _top_level_widgets().items() if p not in before]
    for w in left:
        try:
            w.hide()
            w.deleteLater()
        except RuntimeError:  # (already being destroyed)
            pass
    qtw = sys.modules.get("PySide6.QtWidgets")
    app = qtw.QApplication.instance() if qtw is not None else None
    if app is not None:
        app.processEvents()
        # (processEvents does not run the deletes deleteLater posted at this loop level)
        from PySide6 import QtCore

        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    gc.collect()


@pytest.fixture
def collect_between_tests():
    """No automatic collection inside a test, a full one after it, as the editor runs
    (infra/gcguard): a collection in the middle of a sheet build can free Qt objects it uses."""
    from PySide6 import QtCore

    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        gc.collect()
        if was:
            gc.enable()


@pytest.fixture(autouse=True)
def _restore_theme():
    """A test that applied a theme (an application stylesheet, the stylesheet hook, a scale)
    leaves the application as it found it: later tests would run with other widget sizes."""
    qtw = sys.modules.get("PySide6.QtWidgets")
    app = qtw.QApplication.instance() if qtw is not None else None
    if app is None:
        yield
        return
    from PySide6 import QtGui

    T = sys.modules.get("ctgedit.ui.style.theme")
    SC = sys.modules.get("ctgedit.render.scene")
    snap = dict(
        style=app.style().name(),
        palette=QtGui.QPalette(app.palette()),
        font=QtGui.QFont(app.font()),
        sheet=app.styleSheet(),
        state=dict(T._state) if T else {},
        colors=dict(T.COLORS) if T else None,
        hooked=bool(T and T._orig_set_style_sheet is not None),
        margin=QtGui.QColor(SC.PageScene._MARGIN_COLOUR) if SC else None,
    )
    yield
    T = sys.modules.get("ctgedit.ui.style.theme")
    if T is None:
        return
    hooked = T._orig_set_style_sheet is not None
    if (
        app.styleSheet() == snap["sheet"]
        and hooked == snap["hooked"]
        and (snap["colors"] is None or dict(T.COLORS) == snap["colors"])
    ):
        return
    try:
        T.apply_theme(app, "dark", 1.0)  # every widget back to its sheet as written
        if snap["colors"] is not None:
            T.COLORS.clear()
            T.COLORS.update(snap["colors"])
        T._state.clear()
        T._state.update(snap["state"])
        T._update_tracked()
        if not snap["hooked"]:
            T._uninstall_stylesheet_hook()
        app.setStyleSheet(snap["sheet"])
        app.setPalette(snap["palette"])
        app.setFont(snap["font"])
        if app.style().name() != snap["style"]:
            app.setStyle(snap["style"])
        SC = sys.modules.get("ctgedit.render.scene")
        if SC is not None and snap["margin"] is not None:
            SC.PageScene._MARGIN_COLOUR = snap["margin"]
    except RuntimeError:  # (a widget of the test went meanwhile)
        pass


@pytest.fixture(autouse=True)
def _no_run_left():
    """Each test starts with no Update Pages run, chain or pack going on or waiting (the
    process's run state: ui/jobs/update_run.UpdatePagesRun)."""
    yield
    # (a test may have put a stand-in in their place: it is undone after this)
    for name, attr in (
        ("ctgedit.ui.jobs.update_run", "UpdatePagesRun"),
        ("ctgedit.ui.window.open_tabs", "SharedState"),
    ):
        reset = getattr(getattr(sys.modules.get(name), attr, None), "reset", None)
        if reset is not None:
            reset()


@pytest.fixture(autouse=True)
def _reap_leaked_windows():
    """After each test, an editor window it took out of the window list but did not delete
    is deleted now, while nothing else runs: left to the garbage collector, it is destroyed
    in a later test's event processing and crashes the run."""
    yield
    mw = sys.modules.get("ctgedit.ui.main_window")
    if mw is None:
        return
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    keep = set(map(id, getattr(mw.MainWindow, "instances", [])))
    for w in app.topLevelWidgets():
        if isinstance(w, mw.MainWindow) and id(w) not in keep:
            try:
                for e in getattr(w, "_docs", []):
                    e["doc"].dirty = False
                w.hide()
                w.deleteLater()
            except RuntimeError:  # (already being destroyed)
                pass
    # every deleteLater of the test (its own and these): done now, not never (no event loop
    # at this level runs them)
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


#: the made-up control each ActiveX kind of the factory is copied from in the tests
#: (factory.CONTROL_SOURCES reads a real install's Example project): the CLSID the editor
#: tells the control by, and a ProgID holding the factory's marker for it (the meter's own,
#: made-up ones for the others)
FAKE_CONTROLS = {
    "activex": ("{4E12AC47-9675-402F-8860-1E252886251E}", "CiMeterX.TechMeter"),
    "process_analyst": ("{37D4F22A-7950-45DF-B1F6-DD78E503D7A9}", "FxControls.Analyst"),
    "scheduler": ("{39A00672-6B3F-4DA4-A68D-1EB9935DED91}", "FxControls.Scheduler"),
}


@pytest.fixture(scope="session")
def _fake_control_pages(tmp_path_factory):
    """{kind: page}: a page holding one made-up control of each kind."""
    from ctgedit.format import records as R
    from ctgedit.format import template_place as TP
    from ctgedit.format import writer
    from ctgedit.format.page import parse_ctg_bytes

    out = tmp_path_factory.mktemp("control_pages")
    pages = {}
    for kind, (clsid, prog_id) in FAKE_CONTROLS.items():
        parsed = parse_ctg_bytes(TP.new_page_bytes(640, 480, background=(0xD8D8D8, 0xD8D8D8)))
        parsed.page.objects = [
            R.to_po(R.activex(handle=1, rect1=(20, 20, 220, 160), clsid=clsid, prog_id=prog_id))
        ]
        path = str(out / (kind + ".ctg"))
        with open(path, "wb") as fh:
            fh.write(writer.serialize_ctg(parsed.container, parsed.page, parsed.attributes))
        pages[kind] = path
    return pages


@pytest.fixture(autouse=True)
def _controls_from_fake_pages(_fake_control_pages, monkeypatch):
    """A new ActiveX control comes from the made-up pages (``.real``: the factory's own
    lookup in the install, for the test of it)."""
    from ctgedit.format import factory

    real = factory._control_page

    def fake(kind):
        return _fake_control_pages.get(kind)

    fake.real = getattr(real, "real", real)
    monkeypatch.setattr(factory, "_control_page", fake)
    factory._control_record.cache_clear()


@pytest.fixture(autouse=True)
def _no_live_runtime(monkeypatch):
    """A save must not reach a running Plant SCADA runtime
    (it re-displays the saved page over CtAPI)."""
    monkeypatch.setenv("CTG_NO_RUNTIME", "1")
    # the background .ctF pre-check at open is a speed-up only; keep tests
    # deterministic (the save computes the same verdict synchronously)
    monkeypatch.setenv("CTG_NO_CTF_PRECHECK", "1")
    # nor the display check process after a page open (Tools > Check Page Display
    # still runs on demand)
    monkeypatch.setenv("CTG_NO_DISPLAY_CHECK", "1")
    # a Simulate window never starts or joins a real Cicode simulator (ctsim) unless the
    # test asks for one (tests/test_sim_client.py turns this off around its fake server)
    monkeypatch.setenv("CTG_NO_SIM_ENGINE", "1")
    # nor the control host that takes the embedded controls' pictures (tests/test_controlhost.py
    # turns it on with a fake host)
    monkeypatch.setenv("CTG_NO_CONTROL_HOST", "1")
    # zoom changes land at once (no 160 ms glide), so a test reads zoom() right after a
    # call (tests/test_canvas_nav.py turns it off to test the glide)
    monkeypatch.setenv("CTG_NO_ANIMATION", "1")


@pytest.fixture(autouse=True)
def _isolated_qsettings(tmp_path, monkeypatch):
    """Point settings at a fresh per-test INI."""
    monkeypatch.setenv("CTG_SETTINGS_INI", str(tmp_path / "ctgedit_settings.ini"))
    # ctgedit's own files (autosave copies, the database journal, backups,
    # logs) go to the test's folder, never the user's %LOCALAPPDATA%\ctgedit
    monkeypatch.setenv("CTG_DATA_DIR", str(tmp_path / "ctgedit_data"))
    # (a test that waits in an event loop of its own may still read a dialog it ran: the
    # dialog reaper is off, tests/test_dialog_release turns it on itself)
    monkeypatch.setenv("CTG_NO_DIALOG_REAP", "1")
    # (a question a test does not answer (ui/messages.choose) fails at once: offscreen,
    # its window would wait for a click forever)
    monkeypatch.setenv("CTG_NO_MODAL_QUESTIONS", "1")
    yield


@pytest.fixture(autouse=True)
def _history_versions_off():
    """A MainWindow turns earlier versions on for the whole process (Settings > Editing >
    Safety); a later test must not keep them on (a copy at every save it makes)."""
    yield
    from ctgedit.infra import history

    history.configure(0, 200)


@pytest.fixture(autouse=True)
def _pages_count_as_installed(monkeypatch):
    """The tests' pages are copies in tmp_path, which Compile and Run refuse as not projects
    of the active install (backend/compiler.in_active_install: the compiler would build the
    registered project of the same name). A test of the check sets ``.real`` back."""
    from ctgedit.backend import compiler

    def installed(_project_dir):
        return True

    installed.real = compiler.in_active_install
    monkeypatch.setattr(compiler, "in_active_install", installed)


@pytest.fixture(autouse=True)
def _reset_tag_formats():
    """``textmetrics`` keeps the tag-format lookup of the document being
    edited (process-global); a test must not inherit another page's."""
    from ctgedit.format import textmetrics

    textmetrics.use_tag_formats(None)
    yield
    textmetrics.use_tag_formats(None)


@pytest.fixture(autouse=True)
def _reset_label_mode():
    """Reset the reader's global label mode."""
    from ctgedit.io import reader

    reader.set_label_mode("native")
    reader.set_composite_members(True)
    yield
    reader.set_label_mode("native")
    reader.set_composite_members(True)


#: the process's copy of the fake install: [FxInstall, its fxinstall.Snapshot]
_FX_COPY: list = []


def _fx_copy(tmp_path_factory):
    """The process's copy, as it was made: about 3,000 tests take the install, and a copy
    costs about 800 file creations, the restore one read of each file."""
    import fxinstall

    if not _FX_COPY:
        parent = tmp_path_factory.mktemp("fx")
        fx = fxinstall.install_copy(str(parent))
        _FX_COPY[:] = [fx, fxinstall.Snapshot(fx.root)]
    return _FX_COPY[0]


def _fx_restore():
    """Put the copy back after a test, and take away what the test put beside it (another
    install: the copy's folder holds nothing else). The process caches are emptied too: the
    next test finds none of this one's (as with a new copy at a new path). A copy that cannot
    be put back is left, and the next test makes a new copy."""
    import os
    import shutil

    if not _FX_COPY:
        return
    root = _FX_COPY[0].root
    parent = os.path.dirname(root)
    try:
        _FX_COPY[1].restore()
        for name in os.listdir(parent):
            p = os.path.join(parent, name)
            if os.path.normcase(p) == os.path.normcase(root):
                continue
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    except OSError:
        _FX_COPY.clear()
        return
    cc = sys.modules.get("ctgedit.infra.caches")
    if cc is not None:
        cc.clear("tests")


@pytest.fixture
def fx_install(tmp_path_factory, monkeypatch):
    """A writable copy of the fake install tests/fixtures/FxInstall (tests/fxinstall.py)
    as THE install: CTG_SCADA_ROOT names it, its MASTER.DBF and citect.ini point at the
    copy, and nothing else is searched (no CTG_LIB_PATH, no sibling installs). Returns a
    fxinstall.FxInstall (.root, .user, .project(name), .page(name, project), .file).
    The copy is the process's, put back as it was after each test (CTG_TEST_FRESH_INSTALL=1:
    a new copy for each test)."""
    import os

    if os.environ.get("CTG_TEST_FRESH_INSTALL") == "1":
        _FX_COPY.clear()
    fx = _fx_copy(tmp_path_factory)
    monkeypatch.setenv("CTG_SCADA_ROOT", fx.root)
    monkeypatch.setenv("CTG_LIB_SIBLINGS", "0")
    for k in ("CTG_LIB_PATH", "CTG_SCADA_BIN", "CTG_AVEVA_HELP"):
        monkeypatch.delenv(k, raising=False)
    from ctgedit.editing import libraries_changed

    libraries_changed()
    yield fx
    libraries_changed()
    _fx_restore()


@pytest.fixture
def fx_page(fx_install):
    """``fx_page(name, project='FxMain')``: the path of a page of the fx_install copy."""
    return fx_install.page


@pytest.fixture
def fx_project(fx_install):
    """``fx_project(name='FxMain')``: the folder of a project of the fx_install copy."""
    return fx_install.project


@pytest.fixture
def composite_cache():
    """Draw composite genies from their composition cache element for a test of that
    path."""
    from ctgedit.io import reader

    reader.set_composite_members(False)
    yield
    reader.set_composite_members(True)


__all__ = [
    "pytest_configure",
    "pytest_sessionfinish",
    "_free_process_caches",
    "_reap_leaked_windows",
    "_no_run_left",
    "_restore_theme",
    "collect_between_tests",
    "_no_live_runtime",
    "_fake_control_pages",
    "_controls_from_fake_pages",
    "_isolated_qsettings",
    "_history_versions_off",
    "_pages_count_as_installed",
    "_reset_tag_formats",
    "_reset_label_mode",
    "fx_install",
    "fx_page",
    "fx_project",
    "composite_cache",
]
