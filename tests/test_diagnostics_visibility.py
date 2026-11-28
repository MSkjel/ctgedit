"""Failures that would hide art are made visible."""

from ctgedit.ui.main_window import MainWindow
import logging
import os
import shutil
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtWidgets  # noqa: E402
from ctgedit.editing import ui_state
from qtkit import app as _app


def reader_part(name):
    """The reader part that owns a name (ctgedit/io/readerparts): a patch
    goes there, where the reader's code looks it up."""
    import importlib

    return importlib.import_module("ctgedit.io.readerparts." + name)


#: the busy page and the second page (FxMain), the library the busy page's genies come from
BUSY, SECOND, BUSY_LIB = "FxInputs", "FxGenies", "fxparts"
#: the page with resolved library art (children) and embedded bitmaps (FxMain)
ART = "FxSymbols2"


@pytest.fixture()
def pages(fx_install):
    """(the busy page, the second page) of the fake install's copy."""
    return fx_install.page(BUSY), fx_install.page(SECOND)


@pytest.fixture()
def art_page(fx_install):
    """The page whose art the reader adds (resolved symbols, embedded bitmaps). It must
    still have both: a fixture change that took them away would leave the art tests
    passing without testing art."""
    from ctgedit.io import reader

    path = fx_install.page(ART)
    objs = reader.read_page(path).objects
    assert any(o.children for o in objs), "%s has no resolved symbol art" % ART
    assert any(o.raw.get("_embedded_bmp") for o in objs), "%s has no embedded bitmap" % ART
    return path


@pytest.fixture()
def win():
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    yield w
    for e in w._docs:
        try:
            e["doc"].dirty = False
        except AttributeError:
            pass
    for name in ("_display_check_box", "_unresolved_dlg"):
        d = getattr(w, name, None)
        if d is not None:
            try:
                d.close()
            except RuntimeError:
                pass
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.hide()


@pytest.fixture()
def logs():
    """Every ctgedit log record (INFO and up) while the test runs."""
    recs = []

    class H(logging.Handler):
        def emit(self, r):
            recs.append(r)

    lg = logging.getLogger("ctgedit")
    h = H(level=logging.INFO)
    old = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    yield recs
    lg.removeHandler(h)
    lg.setLevel(old)


def _pump(until=None, timeout=20.0):
    app = _app()
    t = time.perf_counter()
    while time.perf_counter() - t < timeout:
        app.processEvents()
        if until is None or until():
            return True
        time.sleep(0.02)
    return False


def _msgs(recs, name=None, level=None):
    return [
        r.getMessage()
        for r in recs
        if (name is None or r.name == name) and (level is None or r.levelno == level)
    ]


# ------------------------------------------------------------------ the helper
def test_note_exception_is_rate_limited_and_counted(logs, monkeypatch):
    from ctgedit.infra import logsetup

    counts = logsetup.begin_error_scope()
    try:
        for k in range(10):
            try:
                raise KeyError("elem%d" % k)
            except Exception:  # noqa: BLE001
                logsetup.note_exception("test.place", element="e%d" % k)
        try:
            raise ValueError("other")
        except Exception:  # noqa: BLE001
            logsetup.note_exception("test.other")
    finally:
        logsetup.end_error_scope(counts)
    sw = [r for r in logs if r.name == "ctgedit.swallowed"]
    place = [r for r in sw if "test.place" in r.getMessage()]
    assert len(place) == logsetup.SWALLOW_LOG_MAX  # the first few, not all ten
    assert all(r.levelno == logging.WARNING and r.exc_info for r in place)
    assert "element=e0" in place[0].getMessage() and "KeyError" in place[0].getMessage()
    assert any("test.other" in r.getMessage() for r in sw)
    assert counts[("test.place", "KeyError")] == 10 and counts[("test.other", "ValueError")] == 1
    assert "test.place KeyError x10" in logsetup.format_counts(counts)
    # a new page open (scope) logs the first few again, after saying how many were not logged
    logs.clear()
    with logsetup.error_scope() as c2:
        try:
            raise KeyError("again")
        except Exception:  # noqa: BLE001
            logsetup.note_exception("test.place")
    msgs = _msgs(logs, "ctgedit.swallowed")
    assert any("7 more time(s)" in m for m in msgs), msgs
    assert any("swallowed KeyError" in m for m in msgs)
    assert c2 == {("test.place", "KeyError"): 1}
    # never raises, whatever it is given; note_problem logs without a traceback
    logsetup.note_exception("test.none", exc=None, weird=object())
    logsetup.note_problem("test.problem", "GDI said no", w=3)
    assert any("GDI said no (w=3)" in m for m in _msgs(logs, "ctgedit.swallowed"))


def test_a_swallowed_reader_error_is_logged_and_the_page_still_derives(art_page, logs, monkeypatch):
    """Control flow is unchanged: the reader keeps going exactly as before,
    the error is logged and counted."""
    from ctgedit.infra import logsetup
    from ctgedit.io import reader

    MIXER = art_page
    base = reader.read_page(MIXER)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("picture decoder broke")

    monkeypatch.setattr(reader_part("symbols"), "attach_embedded_bitmap", boom)
    with logsetup.error_scope() as counts:
        page = reader.read_page(MIXER)
    assert calls and len(page.objects) == len(base.objects)
    assert counts[("reader.object_from/embedded bitmap", "RuntimeError")] == len(calls)
    msgs = _msgs(logs, "ctgedit.swallowed")
    assert any(
        "reader.object_from/embedded bitmap: swallowed RuntimeError: picture decoder broke" in m for m in msgs
    )


# ------------------------------------------------------------------ open / switch / close lines
def test_page_open_switch_and_close_are_logged(win, pages, logs):
    from ctgedit.infra import logsetup

    MIXER, GENIES = pages
    win.open_page(MIXER)
    _pump(lambda: ui_state(win.doc).diag_open_logged, 10)
    opened = [m for m in _msgs(logs, "ctgedit.open") if m.startswith("opened ")]
    assert len(opened) == 1, opened
    line = opened[0]
    assert MIXER in line and "%d objects" % len(win.doc.page.objects) in line
    assert "links: 1 genie, 3 symbols;" in line and "derived in" in line
    assert "every library reference resolves" in line and "unmodified" in line
    assert "search path" in line and "included projects:" in line
    assert "libraries used:" in line and "%s.ctm <- " % BUSY_LIB in line
    assert win._refs_chip.isHidden()
    # a second page, then back: a switch line; closing: a close line
    win.open_page(GENIES)
    _pump(lambda: ui_state(win.doc).diag_open_logged, 10)
    logs.clear()
    win.doc_tabs.setCurrentIndex(0)
    _pump(None, 0.2)
    assert any(m.startswith("switched to %s" % MIXER) for m in _msgs(logs, "ctgedit.open"))
    win._close_doc_tab(0)
    assert any(m.startswith("closed %s" % MIXER) for m in _msgs(logs, "ctgedit.open"))
    evs = logsetup.recent_events(("open", "switch", "close"))
    assert any(MIXER in e and " open: " in e for e in evs)
    assert any(MIXER in e and " close: " in e for e in evs)


def test_unresolved_references_give_the_chip_and_the_log(win, pages, logs, tmp_path, monkeypatch):
    """A page copied OUT of its project (no CTG_LIB_PATH): its libraries are
    not on the search path -- the chip, the list with reasons and the log say so."""
    monkeypatch.delenv("CTG_LIB_PATH", raising=False)
    # a folder of its own two levels down: no sibling project, no install around it
    iso = tmp_path / "a" / "b" / "Proj"
    iso.mkdir(parents=True)
    cp = iso / (BUSY + ".ctg")
    shutil.copyfile(pages[0], cp)
    win.open_page(str(cp))
    _pump(lambda: ui_state(win.doc).diag_open_logged, 10)
    from ctgedit.diag import open_report as OR

    rep = OR.report_for(win.doc)
    assert rep.unresolved and all(u.reason == OR.REASON_NO_LIBRARY for u in rep.unresolved)
    refs = {u.reference.lower() for u in rep.unresolved}
    assert any(r.startswith(BUSY_LIB + ".") for r in refs), refs
    chip = win._refs_chip
    assert not chip.isHidden() and "symbol" in chip.text() and "not found" in chip.text()
    assert chip.text().split()[1] == str(len(rep.unresolved))
    line = next(m for m in _msgs(logs, "ctgedit.open") if m.startswith("opened "))
    assert "reference(s) not found" in line and OR.REASON_NO_LIBRARY in line
    dlg = win._show_unresolved()
    assert dlg is not None and dlg.table.rowCount() == len(rep.unresolved)
    QtWidgets.QApplication.processEvents()
    for c in (0, 1, 3):  # every reference, file and count shows in full
        assert dlg.table.columnWidth(c) >= dlg.table.sizeHintForColumn(c), c
    text = dlg.copy()
    assert QtWidgets.QApplication.clipboard().text() == text
    assert BUSY_LIB in text.lower() and str(iso) in text  # the searched folders
    dlg.close()


def test_element_not_in_its_library_is_told_apart(fx_install):
    """FxSymbols names fxsym.fx_box, which the copy's fxsym.ctl no longer has (deleted
    here): 'element not in the library', with the library files searched."""
    from ctgedit.diag import open_report as OR
    from ctgedit.editing import EditableDocument, libraries_changed
    from ctgedit.format import element as E

    lib_path = fx_install.file("FxLib", "fxsym.ctl")
    lib = E.LibraryFile.open(lib_path)
    assert lib.delete_element("fx_box") is not None
    E._write_library(lib_path, lib)
    libraries_changed()
    doc = EditableDocument.open(fx_install.page("FxSymbols"))
    rep = OR.page_report(doc)
    logo = [u for u in rep.unresolved if u.reference.lower() == "fxsym.fx_box"]
    assert logo and logo[0].reason == OR.REASON_NO_ELEMENT
    assert logo[0].files and all(
        f.lower().endswith(("fxsym.ctl", "fxsym.ctm", "fxsym.ctt")) for f in logo[0].files
    )


# ------------------------------------------------------------------ automatic changes / recovery
def test_automatic_changes_of_the_open_checks_are_logged(win, art_page, logs, monkeypatch):
    """A template re-applied on open is one INFO line with its undo step and the
    records it changed -- a WARNING when the page's art count changed -- and the
    open line says the page is modified after the checks, and by what."""
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import logsetup

    cp = art_page

    def update_template(self):
        # a real undoable change that removes art: delete the first object with art
        i = next(
            k
            for k, o in enumerate(self.page.objects)
            if o.parent is None and (o.children or o.raw.get("_embedded_bmp"))
        )
        self.delete_objects([i])
        return 1

    monkeypatch.setattr(EditableDocument, "template_status", lambda self: ("stale", "fx_style.normal"))
    monkeypatch.setattr(EditableDocument, "update_template", update_template)
    win.open_page(str(cp))
    _pump(lambda: ui_state(win.doc).diag_open_logged, 10)
    auto = [
        r
        for r in logs
        if r.name == "ctgedit.open" and r.getMessage().startswith("automatic change when opening")
    ]
    assert len(auto) == 1, [r.getMessage() for r in logs]
    msg = auto[0].getMessage()
    assert "template fx_style.normal re-applied" in msg and "undo step '" in msg
    assert "record(s) changed" in msg and "art changed" in msg
    assert auto[0].levelno == logging.WARNING
    label = win.doc._undo[-1].label
    opened = next(m for m in _msgs(logs, "ctgedit.open") if m.startswith("opened "))
    assert "unsaved changes after the open checks ('%s')" % label in opened
    assert any("re-applied" in e for e in logsetup.recent_events(("auto",)))
    # an automatic step that changes nothing is not logged
    logs.clear()
    n = win._auto_step(win.doc, "nothing", lambda: 0)
    assert n == 0 and not _msgs(logs, "ctgedit.open")
    # Copy Diagnostics names the undo step that modified the page
    text = win._copy_diagnostics()
    assert "last undo steps: " in text and label in text
    assert "Recent page opens" in text and str(cp) in text


def test_recovery_restore_names_its_recovery_file(win, pages, logs):
    from ctgedit.editing import EditableDocument
    from ctgedit.infra import autosave, logsetup

    cp = pages[0]
    doc = EditableDocument.open(str(cp))
    doc.set_page_background(0x123456)
    snap = autosave.save_document(doc)
    logs.clear()
    assert win.restore_recovered([autosave.snapshots()[0]]) == 1
    msgs = _msgs(logs, "ctgedit.autosave")
    rest = [m for m in msgs if m.startswith("restoring ")]
    assert len(rest) == 1, msgs
    assert str(cp) in rest[0] and snap.data_path in rest[0] and "written 20" in rest[0]
    assert any(str(cp) in e for e in logsetup.recent_events(("recovery",)))


# ------------------------------------------------------------------ the display check
@pytest.mark.parametrize("which", ["busy", "art"])
def test_display_check_is_identical_on_an_unmodified_page(win, pages, art_page, logs, which):
    """The check process reads the page in a fresh interpreter: the same digest, on the
    busy page and on the page with the reader's art."""
    win.open_page(pages[0] if which == "busy" else art_page)
    out = win._check_page_display(show=False)
    assert out is not None and out.ok and out.where == "process", out.text()
    assert out.identical, out.text()
    assert out.count == len(win.doc.page.objects) == out.fresh_count
    assert win._display_chip.isHidden()
    assert any("identical to a fresh read" in m for m in _msgs(logs, "ctgedit.check"))
    box = win._show_display_outcome(out)
    assert "identical" in box.text()
    box.close()


def test_display_check_reports_lost_art(win, art_page, logs):
    """Drop one object's art from the model this window shows: the check says
    which object lost its art, the chip and the status bar say View > Refresh,
    the log has the details -- and View > Refresh puts the art back."""
    from ctgedit.infra import logsetup
    from ctgedit.ui.panels.diag_ui import DISPLAY_OUT_OF_DATE

    MIXER = art_page
    win.open_page(MIXER)
    k, o = next((k, o) for k, o in enumerate(win.doc.page.objects) if o.children)
    o.children = []
    out = win._check_page_display(in_process=True, show=False)
    assert out.ok and not out.identical and out.where == "in-process"
    assert out.differing == 1
    assert out.differences[0].startswith("object %d (" % k) and "lost art" in out.differences[0]
    assert not win._display_chip.isHidden() and "out of date" in win._display_chip.text()
    assert win.statusBar().currentMessage() == DISPLAY_OUT_OF_DATE
    warn = [r for r in logs if r.name == "ctgedit.check" and r.levelno == logging.WARNING]
    assert warn and "lost art" in warn[0].getMessage() and MIXER in warn[0].getMessage()
    assert "recent pages / automatic changes" in warn[0].getMessage()
    assert "display check:" in logsetup.diagnostics_text([win.doc])
    win._refresh_view()
    assert win.doc.page.objects[k].children  # the art is back
    assert win._display_chip.isHidden()


def test_display_check_falls_back_in_process_when_the_process_fails(win, art_page, monkeypatch):
    from ctgedit.diag import display_check as DC

    monkeypatch.setattr(
        DC, "display_digest_command", lambda *a, **k: [sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    win.open_page(art_page)
    out = win._check_page_display(show=False)
    assert out.ok and out.identical and out.where == "in-process"
    assert "exit code 3" in out.fallback and "the check process failed" in out.text()


def test_display_check_of_unsaved_changes_reads_the_current_bytes(win, pages):
    """A page with unsaved changes is checked against its CURRENT content
    (the bytes a save would write), read --as the page in the fresh process."""
    win.open_page(pages[0])
    # a page object of its own (not the template: a moved template copy no longer
    # aligns with its template, and a full derive -- the fresh read -- drops the art
    # the in-place move kept)
    k = win.doc.page.roots()[-1]
    assert not win.doc.page.objects[k].raw.get("_from_template")
    win.doc.move_objects([k], 5, 5)
    out = win._check_page_display(show=False)
    assert out.ok and out.where == "process" and out.identical, out.text()


def test_display_digest_command_and_app_dispatch(art_page, tmp_path):
    import json
    from ctgedit import app
    from ctgedit.diag import render_digest as RD
    from ctgedit.editing import EditableDocument

    MIXER = art_page
    out = tmp_path / "d.json"
    assert (
        app.main(["ctgedit", "--display-digest", MIXER, str(out), "--detail", "--label-mode", "native"]) == 0
    )
    res = json.loads(out.read_text("utf-8"))
    doc = EditableDocument.open(MIXER)
    mine = RD.page_digest(doc.page)
    assert res["total"] == mine["total"] and res["count"] == len(doc.page.objects)
    assert len(res["detail"]) == res["count"] and res["stamp"] and res["sha1"]
    assert RD.compare_page(doc.page, res)["identical"]
    assert app.main(["ctgedit", "--display-digest", str(tmp_path / "missing.ctg"), str(out)]) == 1
    assert "error" in json.loads(out.read_text("utf-8"))


def test_automatic_display_check_runs_once_per_file_version(win, pages, monkeypatch, logs):
    from ctgedit.ui.panels.diag_ui import DiagnosticsMixin
    from ctgedit.ui.prefs.settings import app_settings

    monkeypatch.delenv("CTG_NO_DISPLAY_CHECK", raising=False)
    monkeypatch.setattr(DiagnosticsMixin, "DISPLAY_CHECK_DELAY_MS", 50)
    monkeypatch.setattr(DiagnosticsMixin, "_display_checked", set())
    app_settings().setValue("opt/display_check", True)  # (off by default)
    MIXER = pages[0]
    win.open_page(MIXER)
    doc = win.doc
    assert _pump(lambda: ui_state(doc).display_check is not None, 90)
    assert doc.ui.display_check.identical and doc.ui.display_check.where == "process"
    assert (os.path.normcase(os.path.abspath(MIXER)), doc._disk_stamp) in MainWindow._display_checked
    # the same file version is not checked again; a changed page is not checked
    win._close_doc_tab(0)
    win.open_page(MIXER)
    assert not win.__dict__.get("_display_queue")
    from ctgedit.ui.prefs.settings import app_settings

    app_settings().setValue("opt/display_check", False)
    MainWindow._display_checked.clear()
    win._queue_display_check(win.doc)
    assert not win.__dict__.get("_display_queue")


def test_display_check_setting_is_on_the_advanced_tab(win):
    from ctgedit.ui.prefs.settings_dialog import SettingsDialog
    from ctgedit.ui.prefs.settings import OPTION_DEFAULTS

    # off by default (a second process per opened page), on demand in Tools
    assert OPTION_DEFAULTS["display_check"] is False
    dlg = SettingsDialog(win)
    tabs = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    adv = dlg._tabs.widget(tabs.index("Advanced"))
    assert adv.isAncestorOf(dlg._flags["display_check"])
    assert not dlg._flags["display_check"].isChecked()
    dlg.close()
