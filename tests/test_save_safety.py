"""Save safety: no lost work, no temp files left."""

import os
import shutil
import stat
import subprocess
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtGui, QtWidgets  # noqa: E402
from qtkit import app as _app

#: the page the tests copy: FxMain's FxShapes of the fake install (tests/fixtures/FxInstall)
SAMPLE_PAGE = "FxShapes"

TMP = ".ctgedit-tmp"


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    yield w
    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


@pytest.fixture()
def pages(tmp_path, fx_page):
    """Copies of the sample page (in tmp_path, beside no project tables); read-only flags
    are cleared again at the end so tmp_path can be removed."""
    made = []
    sample = fx_page(SAMPLE_PAGE)

    def make(name):
        p = str(tmp_path / name)
        shutil.copy2(sample, p)
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        made.append(p)
        return p

    yield make
    for p in made:
        if os.path.exists(p):
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def _read_only(path):
    os.chmod(path, stat.S_IREAD)


def _edit(doc):
    doc.move_objects([doc.page.roots()[-1]], 2, 2)
    assert doc.dirty


class _Boxes:
    """Stands in for QMessageBox.exec (the editor's own boxes: save failure
    with Save As, the runtime confirmation): records the text and presses the
    button of the wanted role."""

    def __init__(self, monkeypatch, role=QtWidgets.QMessageBox.RejectRole):
        self.seen = []
        self.role = role
        monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda box: self._exec(box))

    def _exec(self, box):
        self.seen.append(box.text() + "\n" + box.informativeText())
        for b in box.buttons():
            if box.buttonRole(b) == self.role:
                b.click()
                break
        return 0

    @property
    def text(self):
        return "\n".join(self.seen)


def _warnings(monkeypatch):
    seen = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning", lambda *a, **k: seen.append(a[2]) or QtWidgets.QMessageBox.Ok
    )
    return seen


# ------------------------------------------------------------------ the writer
def test_refused_write_keeps_the_page_and_removes_the_temp_file(pages):
    from ctgedit.editing import EditableDocument

    p = pages("A.ctg")
    before = open(p, "rb").read()
    doc = EditableDocument.open(p)
    _edit(doc)
    _read_only(p)
    with pytest.raises(PermissionError):
        doc.save()
    assert open(p, "rb").read() == before
    assert not os.path.exists(p + TMP)
    assert doc.dirty  # nothing was lost


def test_write_atomic_cleans_up_after_a_failure(tmp_path, monkeypatch):
    from ctgedit.format import fileio

    p = str(tmp_path / "x.bin")
    open(p, "wb").write(b"old")

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        fileio.write_atomic(p, b"new")
    assert open(p, "rb").read() == b"old"
    assert not os.path.exists(p + TMP)


# ------------------------------------------------------------ read-only pages
def test_read_only_page_is_marked_and_save_goes_to_save_as(win, pages, tmp_path, monkeypatch):
    p = pages("A.ctg")
    before = open(p, "rb").read()
    _read_only(p)
    win.open_document(p)
    assert win.doc.read_only
    # (the tab shows a lock instead of '[read-only]': w1-tabs)
    assert win.doc_tabs.tab_state(0)["read_only"]
    assert "read-only" in win.doc_tabs.accessibleTabName(0)
    assert "[read-only]" in win.windowTitle()
    _edit(win.doc)
    copy = str(tmp_path / "A_copy.ctg")
    from ctgedit.ui import browser as _browser

    monkeypatch.setattr(_browser.PageBrowserDialog, "get_save_path", lambda *a, **k: copy)
    assert win.save() is True
    assert os.path.exists(copy) and not os.path.exists(p + TMP)
    assert open(p, "rb").read() == before  # the read-only page untouched
    assert os.path.normcase(win.doc.path) == os.path.normcase(copy)
    assert not win.doc.read_only and not win.doc.dirty
    assert not win.doc_tabs.tab_state(0)["read_only"]


def test_failed_save_says_read_only_and_offers_save_as(win, pages, monkeypatch):
    p = pages("A.ctg")
    win.open_document(p)
    _edit(win.doc)
    _read_only(p)  # became read-only after it was opened
    boxes = _Boxes(monkeypatch)  # Cancel
    assert win.save() is False
    assert "A.ctg is read-only" in boxes.text and "Save As" in boxes.text
    assert "Traceback" not in boxes.text
    assert win.doc.dirty and not os.path.exists(p + TMP)


def test_failed_save_names_the_program_that_locks_the_page(win, pages, monkeypatch):
    p = pages("A.ctg")
    win.open_document(p)
    _edit(win.doc)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; f = open(sys.argv[1], 'rb'); print('open', flush=True); sys.stdin.read()",
            p,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "open"
        from ctgedit.backend import filelock

        assert filelock.lockers(p), "Restart Manager did not report the holder"
        boxes = _Boxes(monkeypatch)
        assert win.save() is False
        assert "A.ctg is locked by" in boxes.text and "PID" in boxes.text
    finally:
        holder.stdin.close()
        holder.wait(10)
    assert not os.path.exists(p + TMP)


# ---------------------------------------------------------------- close / all
def test_close_with_a_failed_save_keeps_the_window_and_asks_the_other_pages(win, pages, monkeypatch):
    from ctgedit.ui.main_window import MainWindow

    a, b = pages("A.ctg"), pages("B.ctg")
    win.open_document(a)
    doc_a = win.doc
    _edit(doc_a)
    win.open_document(b)
    doc_b = win.doc
    _edit(doc_b)
    _read_only(b)
    asked = []
    # (two unsaved pages: one Save Changes list, w1-tabs; Save All saves them in tab
    # order and stops at the first that fails)
    from ctgedit.ui.dialog.save_changes import SaveChangesDialog

    def answer(dlg):
        asked.extend(dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount()))
        return SaveChangesDialog.SAVE

    monkeypatch.setattr(MainWindow, "_exec_save_changes", staticmethod(answer))
    boxes = _Boxes(monkeypatch)  # the failure box: Cancel
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)  # no exception escapes
    assert not ev.isAccepted()  # the window stays open
    assert asked == ["A", "B"]
    assert not doc_a.dirty  # the good page was still saved
    assert doc_b.dirty and "B.ctg is read-only" in boxes.text
    assert win in MainWindow._instances and win.doc is doc_b
    assert not os.path.exists(a + TMP) and not os.path.exists(b + TMP)


def test_save_all_saves_the_rest_and_lists_the_failure(win, pages, monkeypatch):
    a, b, c = pages("A.ctg"), pages("B.ctg"), pages("C.ctg")
    docs = []
    for p in (a, b, c):
        win.open_document(p)
        _edit(win.doc)
        docs.append(win.doc)
    _read_only(b)
    from ctgedit.ui import messages

    seen = []
    monkeypatch.setattr(
        messages, "list_box", lambda _p, _t, text, items, **k: seen.append((text, list(items)))
    )
    win.save_all()
    assert not docs[0].dirty and not docs[2].dirty
    assert docs[1].dirty
    # (the sentence in the box, the reason behind Show Details)
    assert len(seen) == 1 and "Saved 2 pages" in seen[0][0] and "1 page was not saved" in seen[0][0]
    assert any("B.ctg is read-only" in x for x in seen[0][1])
    assert not any(os.path.exists(p + TMP) for p in (a, b, c))


# ------------------------------------------------------------- compile / run
def test_compile_and_run_stop_when_the_save_fails(win, pages, monkeypatch):
    from ctgedit.backend import compiler, runtime

    p = pages("A.ctg")
    win.open_document(p)
    _edit(win.doc)
    _read_only(p)
    calls = []
    monkeypatch.setattr(compiler, "compile_project", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(compiler, "root_project_for", lambda d: "X")
    monkeypatch.setattr(runtime, "start", lambda *a, **k: calls.append(("start",) + a))
    _Boxes(monkeypatch)
    assert win.compile_project() is None
    win._run_project()
    assert calls == []
    assert win.doc.dirty


def _fake_compile(win, monkeypatch):
    monkeypatch.setattr(
        win,
        "compile_project",
        lambda show="always": types.SimpleNamespace(
            errors=[], warnings=[], outcome="completed", project="MyProject"
        ),
    )


def test_run_asks_before_stopping_a_runtime_of_another_project(win, pages, monkeypatch):
    from ctgedit.backend import runtime

    win.open_document(pages("A.ctg"))
    _fake_compile(win, monkeypatch)
    monkeypatch.setattr(runtime, "is_running", lambda: True)
    monkeypatch.setattr(runtime, "runs_project", lambda d: False)
    monkeypatch.setattr(runtime, "running_project", lambda: "LivePlant")
    stops, starts = [], []
    monkeypatch.setattr(runtime, "stop", lambda *a, **k: stops.append(1))
    monkeypatch.setattr(runtime, "start", lambda *a, **k: starts.append(a) or ["run project: MyProject"])
    boxes = _Boxes(monkeypatch)  # Cancel
    win._run_project()
    assert "LivePlant" in boxes.text and "MyProject" in boxes.text
    assert stops == [] and starts == []
    boxes.role = QtWidgets.QMessageBox.AcceptRole
    win._run_project()
    assert len(starts) == 1


def test_run_asks_before_restarting_when_ctapi_cannot_redisplay(win, pages, monkeypatch):
    from ctgedit.backend import ctapi, runtime

    win.open_document(pages("A.ctg"))
    _fake_compile(win, monkeypatch)
    monkeypatch.setattr(runtime, "is_running", lambda: True)
    monkeypatch.setattr(runtime, "runs_project", lambda d: True)

    def no_ctapi(page):
        raise RuntimeError("CtAPI is not available on this machine")

    monkeypatch.setattr(ctapi, "redisplay", no_ctapi)
    starts = []
    monkeypatch.setattr(runtime, "start", lambda *a, **k: starts.append(a) or [])
    boxes = _Boxes(monkeypatch)
    win._run_project()
    assert "could not ask it" in boxes.text and starts == []


def test_stop_shuts_down_gracefully_before_forcing(monkeypatch):
    from ctgedit.backend import ctapi, runtime

    alive = {"on": True}
    monkeypatch.setattr(runtime, "running_processes", lambda: {"Citect32.exe": [1]} if alive["on"] else {})
    monkeypatch.setattr(runtime, "is_running", lambda: alive["on"])
    killed, cic = [], []

    def run(args, **k):
        killed.append(list(args))
        if "/F" in args or alive.get("close"):
            alive["on"] = False
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime.subprocess, "run", run)

    def shutdown(*cmds, **k):
        cic.append(cmds)
        alive["on"] = False
        return [""]

    monkeypatch.setattr(ctapi, "cicode", shutdown)
    # 1. Plant SCADA's own shutdown over CtAPI: nothing is killed
    assert runtime.stop(timeout=0.5, graceful=0.5) == "shut down"
    assert cic == [("DelayShutdown(1000)",)] and killed == []

    # 2. no CtAPI: the windows are asked to close (taskkill without /F)
    def no_ctapi(*cmds, **k):
        raise RuntimeError("the runtime did not answer CtAPI")

    monkeypatch.setattr(ctapi, "cicode", no_ctapi)
    alive.update(on=True, close=True)
    assert runtime.stop(timeout=0.5, graceful=0.5) == "closed"
    assert killed and all("/F" not in a for a in killed)
    # 3. only a runtime that ignores both is force-killed
    killed.clear()
    alive.update(on=True, close=False)
    assert runtime.stop(timeout=0.5, graceful=0.5) == "forced"
    assert any("/F" in a for a in killed)


def test_compile_and_run_refuse_a_page_outside_the_active_install(win, pages, monkeypatch):
    """The compiler and the runtime look a project up by NAME in the active install: Compile
    or Run of a copy of a project elsewhere refuse and say why; nothing is saved, compiled or
    started."""
    from ctgedit.backend import compiler, runtime
    from ctgedit.ui import notify

    p = pages("A.ctg")
    win.open_document(p)
    _edit(win.doc)
    calls = []
    monkeypatch.setattr(compiler, "in_active_install", lambda d: False)
    monkeypatch.setattr(compiler, "compile_project", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(runtime, "start", lambda *a, **k: calls.append(("start",) + a))
    _Boxes(monkeypatch)

    def said():
        return notify.centre(win).newest_first()[0].text

    assert win.compile_project() is None
    assert said().startswith("Not compiled: ") and "not a project of the active Plant SCADA install" in said()
    win._run_project()
    assert said().startswith("Not run: ")
    assert calls == [] and win.doc.dirty


def test_in_active_install(monkeypatch):
    from ctgedit.backend import compiler
    import ctgedit.io.citect_config as cc

    # (the real check, not the suite's stand-in: conftest)
    monkeypatch.setattr(compiler, "in_active_install", compiler.in_active_install.real)
    monkeypatch.setattr(cc, "live_user_dir", lambda: r"C:\U")
    monkeypatch.setattr(compiler, "_registered_root", lambda d, u: None)
    assert not compiler.in_active_install(r"C:\Temp\FxMain")
    assert compiler.root_project_for(r"C:\Temp\FxMain") == "FxMain"  # (as before)
    monkeypatch.setattr(compiler, "_registered_root", lambda d, u: "Root")
    assert compiler.in_active_install(r"C:\U\FxMain")
    monkeypatch.setattr(cc, "live_user_dir", lambda: None)
    assert compiler.in_active_install(r"C:\Temp\FxMain")  # (no install: the compile says so)


def test_set_run_project_backs_up_citect_ini_and_writes_atomically(tmp_path, monkeypatch):
    from ctgedit.backend import runtime

    ini = tmp_path / "citect.ini"
    old = b"[CtEdit]\r\nRun =C:\\Old\\Plant\r\nUser =C:\\U\r\n"
    ini.write_bytes(old)
    monkeypatch.setattr(runtime, "active_ini", lambda: str(ini))
    monkeypatch.setattr(runtime, "root_project_for", lambda d: "X")
    import ctgedit.io.citect_config as cc

    monkeypatch.setattr(cc, "live_user_dir", lambda: None)
    runtime.set_run_project(r"C:\P\X")
    bak = tmp_path / ("citect.ini" + runtime.BACKUP_SUFFIX)
    assert bak.read_bytes() == old
    assert ini.read_bytes() == b"[CtEdit]\r\nRun =C:\\P\\X\r\nUser =C:\\U\r\n"
    assert not (tmp_path / ("citect.ini" + TMP)).exists()


# ----------------------------------------------------------------- reopening
def test_session_pages_that_do_not_reopen_are_reported(win, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    missing = str(tmp_path / "Gone.ctg")
    damaged = str(tmp_path / "Damaged.ctg")
    open(damaged, "wb").write(b"not a page" * 8)
    st = app_settings()
    st.setValue("session/open_files", [damaged, missing])
    st.setValue("session/last_file", missing)
    seen = _warnings(monkeypatch)
    assert win.restore_session(reopen=True) is False
    _app().processEvents()  # the report waits for the event loop
    assert len(seen) == 1
    assert "Gone.ctg was not found" in seen[0] and "Damaged.ctg" in seen[0]
    assert "damaged" in seen[0]


def test_command_line_page_that_does_not_open_is_reported(tmp_path, monkeypatch):
    _app()
    from ctgedit.app import start_window
    from ctgedit.ui.main_window import MainWindow

    seen = _warnings(monkeypatch)
    w = start_window(["ctgedit", str(tmp_path / "Nope.ctg")])
    try:
        assert len(seen) == 1 and "Nope.ctg was not found" in seen[0]
    finally:
        if w in MainWindow._instances:
            MainWindow._instances.remove(w)
        w.hide()


def test_recent_page_that_does_not_open_is_reported_and_kept(win, tmp_path, monkeypatch):
    from ctgedit.ui.prefs.settings import app_settings

    bad = str(tmp_path / "Bad.ctg")
    open(bad, "wb").write(b"garbage" * 8)
    app_settings().setValue("mru", [bad])
    seen = _warnings(monkeypatch)
    menu = QtWidgets.QMenu()
    win._fill_mru_into(menu)
    menu.actions()[0].trigger()
    assert len(seen) == 1 and "Bad.ctg" in seen[0]
    assert app_settings().value("mru", [], type=list) == [bad]
    assert win.doc is None


def test_session_element_tabs_are_skipped_quietly(win, tmp_path, monkeypatch):
    """An element tab stores its LIBRARY path in the session; the session does
    not reopen element tabs, so it must not report the library as 'not a
    page' at every start."""
    from ctgedit.ui.prefs.settings import app_settings

    lib = str(tmp_path / "mylib.ctm")
    open(lib, "wb").write(b"library" * 8)
    st = app_settings()
    st.setValue("session/open_files", [lib])
    st.setValue("session/last_file", lib)
    seen = _warnings(monkeypatch)
    assert win.restore_session(reopen=True) is False
    _app().processEvents()
    assert seen == []


def test_truncated_page_is_reported_by_name_as_damaged(win, tmp_path, monkeypatch, fx_page):
    bad = str(tmp_path / "Half.ctg")
    data = open(fx_page(SAMPLE_PAGE), "rb").read()
    open(bad, "wb").write(data[: len(data) // 2])
    seen = _warnings(monkeypatch)
    assert win.open_page(bad) is False
    assert len(seen) == 1
    assert "Half.ctg could not be opened" in seen[0] and "damaged" in seen[0]
    assert win.doc is None


def test_refused_new_file_names_the_folder(tmp_path):
    from ctgedit.ui.main_window import MainWindow

    target = str(tmp_path / "New.ctg")  # does not exist: a Save As
    title, why, todo, save_as = MainWindow._save_error_text(target, PermissionError(13, "Access is denied"))
    assert "New.ctg was not saved" in why and str(tmp_path) in why
    assert "read-only" not in why and "folder you can write to" in todo and save_as


def test_compile_asks_about_other_unsaved_files_of_the_project(win, pages, monkeypatch):
    """The compiler reads the saved files: another unsaved page of the project is named and
    saved first (Save All and Compile), or left out on purpose (Compile Saved Files), or the
    compile does not run."""
    from ctgedit.backend import compiler

    a, b = pages("A.ctg"), pages("B.ctg")
    win.open_document(a)
    _edit(win.doc)
    doc_a = win.doc
    win.open_document(b)
    _edit(win.doc)
    calls = []
    monkeypatch.setattr(
        compiler,
        "compile_project",
        lambda *x, **k: (
            calls.append(x)
            or compiler.CompileResult(project="X", outcome="completed", exit_code=0, seconds=0.1)
        ),
    )
    monkeypatch.setattr(compiler, "root_project_for", lambda d: "X")
    monkeypatch.setattr(win, "_show_modeless", lambda *a, **k: None, raising=False)
    boxes = _Boxes(monkeypatch, QtWidgets.QMessageBox.RejectRole)  # Cancel
    assert win.compile_project() is None
    assert calls == [] and "A.ctg" in boxes.text and doc_a.dirty
    _edit(win.doc)
    boxes = _Boxes(monkeypatch, QtWidgets.QMessageBox.AcceptRole)  # Save All and Compile
    win.compile_project()
    assert calls and not doc_a.dirty


def test_a_page_open_in_another_window_is_brought_forward_not_opened_twice(win, pages):
    """Opening a page that another window has editable goes to that window, so two editable
    copies never report each other's saves as 'another program'."""
    from ctgedit.ui.main_window import MainWindow

    p = pages("A.ctg")
    win.open_document(p)
    other = MainWindow()
    try:
        other.open_document(p)
        assert len(other._docs) == 0 or all(e["doc"] is not win.doc for e in other._docs)
        assert sum(len(w._docs) for w in (win, other)) == 1
    finally:
        if other in MainWindow._instances:
            MainWindow._instances.remove(other)
        other.close()
