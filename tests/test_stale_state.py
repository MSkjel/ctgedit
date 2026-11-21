"""Changes made by other programs are noticed without a refresh."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from qtkit import app as _app


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _later(path, seconds=3):
    """Move the file's mtime on (a rewrite within the stamp's resolution is still seen)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))


def _make_win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop_win(w):
    from ctgedit.ui.main_window import MainWindow

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


def _no_jobs(w, monkeypatch):
    """The window's genie update jobs are recorded, not run (no worker process)."""
    submitted = []
    monkeypatch.setattr(w._genie_job_runner(), "submit", lambda *a, **k: submitted.append(a))
    return submitted


def _other_program_saves(lib, element, tmp_path):
    """Another program's save of ``element`` in library ``lib``: the element changed in a
    copy, the copy's bytes put in place by a plain copy (not this process's write path)."""
    from ctgedit.editing import ElementDocument

    tmp = os.path.join(str(tmp_path), "other_" + os.path.basename(lib))
    shutil.copyfile(lib, tmp)
    e = ElementDocument.open_element(tmp, element)
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    shutil.copyfile(tmp, lib)
    _later(lib)


# -- 1. a library saved by another program --------------------------------------------------
def test_a_symbol_library_saved_by_another_program_redraws_the_page(fx_install, win, tmp_path, monkeypatch):
    """FxSymbols draws symbols of FxLib's fxsym.ctl: the Graphics Builder saves one; the
    watch sees the file, the page is drawn again from it (its derivation read the new
    file), without a tab switch or View > Refresh."""
    _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    assert win._lib_watch is not None
    assert _key(fx_install.project("FxLib")) in {_key(f) for f in win._lib_watch.folders}
    opened = dict(doc._derivation.resolver.opened)
    assert _key(lib) in opened
    serial = doc.derive_serial
    _other_program_saves(lib, "fx_sym1" if "fx_sym1" in _names(lib) else _names(lib)[0], tmp_path)
    changed = win._lib_watch.rescan()
    assert _key(lib) in {_key(p) for p in changed} and win._lib_timer.isActive()
    done = win._check_library_changes()
    assert doc in done and doc.derive_serial > serial
    now = dict(doc._derivation.resolver.opened)
    assert now[_key(lib)] != opened[_key(lib)]
    assert "changed by another program" in win.statusBar().currentMessage()
    # the same change again is nothing new (a second window's watch reports it too)
    assert win._check_library_changes() == []
    win._on_library_files_changed([lib])
    assert win._check_library_changes() == []


def _names(lib):
    from ctgedit.format.element import LibraryFile

    return [s.name for s in LibraryFile.open(lib).slots]


def test_a_genie_library_saved_by_another_program_composes_the_genies_again(
    fx_install, win, tmp_path, monkeypatch
):
    """FxParts places fxparts.fx_row, each nesting fxnest.n1: another program's save of
    fxnest.n1 queues the genie update of the page (the path a genie saved here takes)."""
    submitted = _no_jobs(win, monkeypatch)
    lib = fx_install.file("FxLib", "fxnest.ctm")
    page = fx_install.page("FxParts")
    from ctgedit.editing import EditableDocument, libraries_changed

    libraries_changed()
    d = EditableDocument.open(page)
    d.update_genie_instances()  # (FxStale1 is older than its element)
    with open(page, "wb") as fh:
        fh.write(d.serialize())
    win.open_document(page)
    doc = win.doc
    submitted.clear()
    assert not doc.genie_changes()
    _other_program_saves(lib, "n1", tmp_path)
    assert doc.genie_changes()
    win._lib_watch.rescan()
    done = win._check_library_changes()
    assert doc in done
    assert submitted, "the changed genies go to a worker"


def test_the_windows_own_library_save_is_not_acted_on_twice(fx_install, win, monkeypatch):
    """A symbol saved here is propagated by the save (format/fileio recorded the write):
    the watch's notice of the same file is skipped."""
    _no_jobs(win, monkeypatch)
    from ctgedit.editing import ElementDocument
    from ctgedit.format.fileio import written_stamp

    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    serial = doc.derive_serial
    e = ElementDocument.open_element(lib, _names(lib)[0])
    e.move_objects([list(e.page.roots())[0]], 6, 0)
    e.save()
    st = os.stat(lib)
    assert written_stamp(lib) == (st.st_mtime_ns, st.st_size)
    win._lib_watch.rescan()
    assert win._check_library_changes() == []
    assert doc.derive_serial == serial


def test_a_page_that_does_not_read_the_changed_library_is_left_alone(fx_install, win, tmp_path, monkeypatch):
    """FxSymbols reads fxsym.ctl, fxinc.ctl and lights.ctl, not fxmore.ctl: a change of
    fxmore.ctl draws nothing again (the stale check is by the files the derivation read)."""
    _no_jobs(win, monkeypatch)
    from ctgedit.ui.window import libwatch as LW

    lib = fx_install.file("FxLib", "fxmore.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    assert _key(lib) not in doc._derivation.resolver.opened
    serial = doc.derive_serial
    _other_program_saves(lib, _names(lib)[0], tmp_path)
    assert not LW._affected(doc, {_key(lib)})
    assert doc not in LW.libraries_changed_on_disk([lib], first=win)
    assert doc.derive_serial == serial


def test_a_same_named_library_put_in_a_nearer_project_is_noticed(fx_install, win, tmp_path, monkeypatch):
    """FxSymbols reads FxLib's fxsym.ctl; a copy of it saved into FxMain (nearer on the
    search path) shadows it: the page did not open that file, but asked for its name, so it
    is drawn again from the copy."""
    _no_jobs(win, monkeypatch)
    from ctgedit.ui.window import libwatch as LW

    lib = fx_install.file("FxLib", "fxsym.ctl")
    win.open_document(fx_install.page("FxSymbols"))
    doc = win.doc
    near = os.path.join(fx_install.project("FxMain"), "fxsym.ctl")
    assert _key(near) not in doc._derivation.resolver.opened
    shutil.copyfile(lib, near)
    _later(near)
    assert LW._affected(doc, {_key(near)})
    serial = doc.derive_serial
    assert doc in LW.libraries_changed_on_disk([near], first=win)
    assert doc.derive_serial > serial
    assert _key(near) in doc._derivation.resolver.opened


def test_the_library_watch_follows_its_settings(fx_install, win):
    from ctgedit.ui.prefs.settings import app_settings

    win.open_document(fx_install.page("FxSymbols"))
    assert win._lib_watch is not None
    st = app_settings()
    st.setValue("opt/library_watch_delay_ms", 2500)
    win._apply_library_watch()
    assert win._lib_timer.interval() == 2500
    st.setValue("opt/library_watch", False)
    win._apply_library_watch()
    assert win._lib_watch is None
    st.setValue("opt/library_watch", True)
    win._apply_library_watch()
    assert win._lib_watch is not None
    st.remove("opt/library_watch")
    st.remove("opt/library_watch_delay_ms")


def test_the_watch_settings_are_declared_with_rows_and_a_hook():
    from ctgedit.ui.prefs.settings import OPTION_DEFAULTS
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog

    assert OPTION_DEFAULTS["library_watch"] is True
    assert OPTION_DEFAULTS["library_watch_delay_ms"] == 1000
    rows = {r.key: r for r in SettingsDialog.ROWS}
    assert rows["opt/library_watch"].apply == "window:_apply_library_watch"
    delay = rows["opt/library_watch_delay_ms"]
    assert delay.kind == "int" and delay.span == (100, 10000) and delay.suffix == " ms"
    assert delay.apply == "window:_apply_library_watch"


def test_a_tab_show_watches_the_folders_of_the_pages_open_now(fx_install, win, monkeypatch):
    """The watched folders follow the open documents: a page of another project opened in
    a second tab adds its folders; the tab show compares the folders too."""
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    have = {_key(f) for f in win._lib_watch.folders}
    assert _key(fx_install.project("FxOther")) not in have
    win.open_document(fx_install.page("FxShapes", "FxOther"))
    assert _key(fx_install.project("FxOther")) in {_key(f) for f in win._lib_watch.folders}
    seen = []
    monkeypatch.setattr(win._lib_watch, "rescan", lambda folder=None: seen.append(folder) or [])
    win.show_tab(0)
    assert seen


# -- 6. the window activated: the panels re-check -------------------------------------------
def test_the_panels_re_check_their_sources_when_the_window_is_activated(fx_install, win, monkeypatch):
    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    calls = []
    monkeypatch.setattr(win.tags_dock, "set_project_dir", lambda p: calls.append(("tags", p)))
    monkeypatch.setattr(win.library_dock, "set_project_dir", lambda p: calls.append(("library", p)))
    monkeypatch.setattr(win._lib_watch, "rescan", lambda folder=None: calls.append(("rescan", folder)) or [])
    win._window_activated()
    proj = os.path.dirname(fx_install.page("FxSymbols"))
    assert ("tags", proj) in calls and ("library", proj) in calls
    assert ("rescan", None) in calls


def test_the_windows_activation_change_runs_the_re_checks(fx_install, win, monkeypatch):
    """Alt+Tab back to the editor (QEvent.ActivationChange with the window active) is what
    runs them; a change that leaves the window inactive runs nothing."""
    from PySide6 import QtCore

    _no_jobs(win, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    ran = []
    monkeypatch.setattr(win, "_window_activated", lambda: ran.append(True))
    monkeypatch.setattr(win, "isActiveWindow", lambda: False)
    QtWidgets.QApplication.sendEvent(win, QtCore.QEvent(QtCore.QEvent.ActivationChange))
    assert not ran
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)
    QtWidgets.QApplication.sendEvent(win, QtCore.QEvent(QtCore.QEvent.ActivationChange))
    assert ran


def test_the_library_panel_re_checks_its_stamps_when_shown(fx_install):
    """A library of the chain changed while the panel was hidden (no tab switch since):
    showing the panel lists again."""
    app = _app()
    from ctgedit.ui.panels.library_panel import LibraryPanel

    p = LibraryPanel()
    p.resize(320, 600)
    p.set_project_dir(fx_install.project("FxMain"))
    p.ensure_loaded()
    p.set_kind("symbol")
    p.show()
    app.processEvents()
    p.ensure_loaded()
    assert not p._dirty and p._loaded_stamp is not None
    p.hide()
    p.show()
    assert not p._load_timer.isActive()  # nothing changed: nothing to do
    p.hide()
    _later(fx_install.file("FxLib", "fxsym.ctl"))
    p.show()
    assert p._dirty and p._load_timer.isActive()
    p.close()


# -- 7, 8. every editor window ---------------------------------------------------------------
def test_a_genie_save_queues_the_updates_of_every_window(fx_install, win, win2, monkeypatch):
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    win2.open_document(fx_install.page("FxParts"))
    asked = []
    monkeypatch.setattr(
        win, "_update_stale_genies", lambda doc, first=False: asked.append((win, doc, first)) or []
    )
    monkeypatch.setattr(
        win2, "_update_stale_genies", lambda doc, first=False: asked.append((win2, doc, first)) or []
    )
    win._queue_open_genie_updates()
    assert (win2, win2.doc, True) in asked and (win, win.doc, True) in asked

    class _Saved:
        kind = "genie"
        library_name, element_name = "fxnest", "n1"
        path = fx_install.file("FxLib", "fxnest.ctm")
        previous_element = None
        display_name = "fxnest.n1"

    asked.clear()
    win._propagate_element(_Saved())
    assert (win2, win2.doc, True) in asked


def test_view_refresh_redraws_the_pages_of_every_window(fx_install, win, win2, monkeypatch):
    _no_jobs(win, monkeypatch)
    _no_jobs(win2, monkeypatch)
    win.open_document(fx_install.page("FxSymbols"))
    win2.open_document(fx_install.page("FxShapes"))
    win2.open_document(fx_install.page("FxTextKinds"))
    hidden = next(e for e in win2._docs if e["doc"] is not win2.doc)
    hidden.pop("stale", None)
    s1, s2 = win.doc.derive_serial, win2.doc.derive_serial
    win._refresh_view()
    assert win.doc.derive_serial > s1 and win2.doc.derive_serial > s2
    assert hidden.get("stale") is True and hidden["doc"]._derivation is None


# -- 2. the expression context's tag index -----------------------------------------------------
def _add_tag(fx_install, name):
    from ctgedit.format.dbf import Dbf

    path = fx_install.file("FxMain", "variable.DBF")
    with Dbf(path) as t:
        t.append(
            [
                t.encode(
                    {
                        "NAME": name,
                        "TYPE": "INT",
                        "UNIT": "FxIO",
                        "ADDR": "I%d" % 900,
                        "COMMENT": "added in Studio",
                    }
                )
            ]
        )
    _later(path)


def test_the_expression_advice_knows_a_tag_added_in_studio(fx_install):
    from ctgedit.ui.objprops.expression import ExpressionContext

    ctx = ExpressionContext(fx_install.project("FxMain"))
    assert ctx.is_name("FxStudioTag") is False
    names = ctx.tag_names()
    assert "FxStudioTag" not in names
    _add_tag(fx_install, "FxStudioTag")
    assert ctx.is_name("FxStudioTag") is True
    assert "FxStudioTag" in ctx.tag_names()
    assert ctx.tag_range("FxStudioTag") is not None


# -- 3. a new Cicode function ------------------------------------------------------------------
def _add_function(fx_install, name):
    path = fx_install.file("FxMain", "fxmain.ci")
    with open(path, "ab") as fh:
        fh.write(("\r\nINT FUNCTION %s(INT nArg)\r\n    RETURN nArg;\r\nEND\r\n" % name).encode("ascii"))
    _later(path)


def test_a_new_function_is_known_to_quick_open_and_the_catalogue(fx_install):
    from ctgedit.io.cicode_catalog import FunctionCatalog
    from ctgedit.ui.dialog import quick_open as Q

    proj = fx_install.project("FxMain")
    cat = FunctionCatalog(proj, builtins=())
    assert cat.is_function("FxStudioFn") is False
    assert "FxStudioFn" not in {h.name for h in Q.function_hits(proj)}
    _add_function(fx_install, "FxStudioFn")
    assert cat.is_function("FxStudioFn") is True
    assert cat.get("FxStudioFn") is not None
    assert "FxStudioFn" in {h.name for h in Q.function_hits(proj)}
    # the list is a memo while the files are the same
    assert Q.function_hits(proj) is Q.function_hits(proj)


# -- 4. the alarm priorities -------------------------------------------------------------------
def test_alarm_priorities_follow_the_table(fx_install):
    from ctgedit.format import alarmind
    from ctgedit.format.dbf import Dbf

    page = fx_install.page("FxAlarms")
    rows = alarmind.priorities(page)
    assert [r[0] for r in rows] == [1, 2, 3]
    assert set(alarmind.priority_details(page)) == {1, 2, 3}
    table = fx_install.file("Include", "almprior.DBF")
    with Dbf(table) as t:
        t.delete(0)
    _later(table)
    assert [r[0] for r in alarmind.priorities(page)] == [2, 3]
    assert set(alarmind.priority_details(page)) == {2, 3}


# -- 5. the language tables --------------------------------------------------------------------
def test_a_translation_changed_in_studio_is_read_again(fx_install):
    from ctgedit.io.lang import LanguageDB

    page = fx_install.page("FxTextKinds")
    db = LanguageDB.for_page(page)
    assert db.translate("Fx Start", "Fxlandic(Fxland)") == "Fx Anfang"
    assert LanguageDB.for_page(page) is db
    table = fx_install.file("FxMain", "fxlandic.DBF")
    with open(table, "rb") as fh:
        data = fh.read()
    assert b"Fx Anfang" in data
    with open(table, "wb") as fh:
        fh.write(data.replace(b"Fx Anfang", b"Fx Neu   "))
    _later(table)
    db2 = LanguageDB.for_page(page)
    assert db2 is not db
    assert db2.translate("Fx Start", "Fxlandic(Fxland)") == "Fx Neu"
    assert LanguageDB.for_page(page) is db2


# -- 9. the include chain ----------------------------------------------------------------------
def test_an_include_removed_in_studio_leaves_the_chain_without_a_reload(fx_install):
    from ctgedit.format.dbf import Dbf
    from ctgedit.io.catalog import catalog_for
    from ctgedit.ui.panels.library_panel import LibraryPanel

    _app()
    main = fx_install.project("FxMain")
    cat = catalog_for()
    info = cat.project_for_dir(main)
    assert info.includes == ["FxLib", "FxStyle"]
    assert [p.name for p in cat.closure(info)][:3] == ["FxMain", "FxLib", "FxStyle"]
    p = LibraryPanel()
    p.set_project_dir(main)
    p.ensure_loaded()
    assert not p._dirty
    stamp = p._chain_stamp()
    table = fx_install.file("FxMain", "include.DBF")
    with Dbf(table) as t:
        t.delete(0)  # FxLib
    _later(table)
    assert cat.refresh_topology() is True
    assert cat.refresh_topology() is False
    info = cat.project_for_dir(main)
    assert info.includes == ["FxStyle"]  # (FxStyle includes FxLib: still reached)
    assert [x.name for x in cat.closure(info)][:3] == ["FxMain", "FxStyle", "FxLib"]
    assert p._chain_stamp() != stamp
    p.set_project_dir(main)
    assert p._dirty
    p.ensure_loaded()
    assert [x.name for x in p._chain][:3] == ["FxMain", "FxStyle", "FxLib"]
    p.close()


def test_the_browsers_refresh_reads_a_changed_topology(fx_install):
    from ctgedit.io.catalog import catalog_for

    cat = catalog_for()
    main = fx_install.project("FxMain")
    cat.closure(cat.project_for_dir(main))
    master = os.path.join(fx_install.user, "MASTER.DBF")
    assert os.path.isfile(master)
    cat.refresh()
    assert cat._topo  # unchanged: kept
    _later(master)
    cat.refresh()
    assert not cat._topo  # MASTER.DBF changed: read again


# -- 10. Save As renames the Simulate tab -------------------------------------------------------
def test_save_as_renames_the_simulate_tab_that_follows_the_document(fx_install, win, monkeypatch):
    _no_jobs(win, monkeypatch)
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from test_sim_engine_source import _rect_page
    from test_sim_live_reload import _ReloadSim
    from ctgedit.ui.browser import PageBrowserDialog
    from ctgedit.ui.simulate import window as W

    monkeypatch.setattr(W, "runtime_page", lambda path, parsed=None, doc=None: _rect_page())
    win.open_document(fx_install.page("FxShapes"))
    doc = win.doc
    sw = W.simulate_window()
    try:
        sp = sw.open_page(doc.path, sim=_ReloadSim(), doc=doc, parsed=doc.parsed)
        assert sp.doc is doc and _key(sp.page_path) == _key(doc.path)
        new = os.path.join(os.path.dirname(doc.path), "FxShapesCopy.ctg")
        monkeypatch.setattr(PageBrowserDialog, "get_save_path", staticmethod(lambda *a, **k: new))
        assert win.save_as()
        assert _key(doc.path) == _key(new)
        assert _key(sp.page_path) == _key(new) and sp.doc is doc
        assert sp.title() == "FxShapesCopy"
        assert sw.find(new) is sp and sw.find(fx_install.page("FxShapes")) is None
        assert sp.file_stamp == (os.stat(new).st_mtime_ns, os.stat(new).st_size)
    finally:
        sw.close()
        if sw in W._WINDOWS:
            W._WINDOWS.remove(sw)
