"""Application entry point.

ctgedit                          reopen the last session
ctgedit <page.ctg> [...]         open pages, in the running editor if there is one
ctgedit --new-instance [...]     start a separate editor
ctgedit --smoke-test <page.ctg>... [--report out.json]
                                 headless open / edit / save / close check
ctgedit --version
"""

from __future__ import annotations

import os
import sys
from typing import List, NamedTuple, Optional

from PySide6 import QtCore, QtWidgets
from .infra.logsetup import note_exception as _note_exc

USAGE = (
    "Usage: ctgedit [page.ctg ...] [--new-instance]\n"
    "       ctgedit --smoke-test page.ctg [...] [--report result.json]\n"
    "       ctgedit --version"
)

#: Qt's own command-line options that take a value (passed on to Qt, never
#: taken for a page to open)
_QT_VALUE_OPTIONS = {
    "-platform",
    "-platformpluginpath",
    "-platformtheme",
    "-plugin",
    "-qwindowgeometry",
    "-qwindowtitle",
    "-qwindowicon",
    "-style",
    "-stylesheet",
    "-display",
    "-geometry",
    "-title",
    "-session",
}

#: named mutex the installer checks (Inno Setup AppMutex) so it can ask the
#: user to close ctgedit before replacing its files
APP_MUTEX = "ctgeditAppMutex"
#: the taskbar identity; the installer gives the Start menu shortcut the same
#: one, so a pinned ctgedit groups with the running editor
APP_USER_MODEL_ID = "ctgedit.editor"
_mutex = []


class Options(NamedTuple):
    files: List[str]
    new_instance: bool = False
    smoke: bool = False
    report: Optional[str] = None
    version: bool = False
    error: str = ""


def parse_args(args: List[str]) -> Options:
    """The command line (without the program name)."""
    files: List[str] = []
    names = {"--new-instance": "new_instance", "--smoke-test": "smoke", "--version": "version"}
    flags = dict.fromkeys(names.values(), False)
    report = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in names:
            flags[names[a]] = True
        elif a == "--report":
            if i + 1 >= len(args):
                return Options(files, error="--report needs a file name")
            i += 1
            report = os.path.abspath(args[i])
        elif a.startswith("--"):
            return Options(files, error="Unknown option %s" % a)
        elif a.startswith("-"):
            if a in _QT_VALUE_OPTIONS:
                i += 1  # Qt option + its value
        else:
            files.append(os.path.abspath(a))
        i += 1
    if flags["smoke"] and not files:
        return Options(files, error="--smoke-test needs at least one page")
    return Options(files, flags["new_instance"], flags["smoke"], report, flags["version"])


def _install_error_hook() -> None:
    """Show user-facing edit refusals (a character the page code page cannot
    store) as a message box instead of a console traceback; PySide6 routes an
    exception escaping a slot through ``sys.excepthook``. Every other
    exception is also written to the error log (``ctgedit.crashlog``)."""
    from .editing import TransformError, UnencodableTextError
    from . import crashlog

    prev = sys.excepthook

    busy = []

    def hook(etype, value, tb):
        from .ui.messages import warn

        if isinstance(value, UnencodableTextError):
            warn(QtWidgets.QApplication.activeWindow(), "Unsupported Character", str(value))
            return
        if isinstance(value, TransformError):
            # (an edit refused with the page left as it was: said, not a crash report)
            warn(QtWidgets.QApplication.activeWindow(), "Not Changed", str(value))
            return
        logged = None
        if not issubclass(etype, (KeyboardInterrupt, SystemExit)):
            logged = crashlog.log_exception(etype, value, tb)
            from .infra import logsetup

            logsetup.log_exception(etype, value, tb)
        prev(etype, value, tb)
        if (
            busy
            or issubclass(etype, (KeyboardInterrupt, SystemExit))
            or QtWidgets.QApplication.instance() is None
        ):
            return
        # else a failed command would seem to do nothing
        busy.append(1)
        try:
            import traceback

            box = QtWidgets.QMessageBox(QtWidgets.QApplication.activeWindow())
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle("Command Failed")
            # the user reads the failure's own message; the Python type name goes to the log
            # and the details
            box.setText(str(value).strip() or "Something went wrong.")
            if logged:
                box.setInformativeText(
                    "The details were added to %s. Include that file when you report the problem." % logged
                )
            box.setDetailedText("".join(traceback.format_exception(etype, value, tb)))
            box.exec()
        finally:
            busy.clear()

    sys.excepthook = hook


def start_window(argv: list[str], files: Optional[List[str]] = None, defer: bool = False, then=None):
    """Open the first editor window with the command-line pages, or the saved session."""
    from .ui.main_window import MainWindow
    from .ui.panels.doc_tabs import startup_mode

    files = list(argv[1:] if files is None else files)
    w = MainWindow()
    failed = []
    # Settings > Pages and libraries 'On start:': the last pages, the start page or nothing
    # (a page named on the command line always opens; with 'A page opened from Explorer also
    # reopens the last pages' the last pages come back with it)
    mode = startup_mode()
    from .ui.prefs.window_options import explorer_session

    reopen = (not files or explorer_session()) and mode == "session"
    if not files and mode == "none":
        w._start_page_off = True

    def open_files():
        for f in files:
            try:
                w.open_document(f)
            except Exception as e:  # noqa: BLE001 -- shown once the window is up
                _note_exc("app.start_window.open_files", path=f)
                failed.append((f, e))

    if defer:
        # explicit files override the saved session (restore prefs only, unless the
        # setting above); a restored session's tabs are there at once, their pages open later
        w.restore_session(reopen=reopen, defer=True)
        w.show()
        w._update_start_page()
        sp = getattr(w, "start_page", None)
        if files and sp is not None:
            # the pages named on the command line open next: not the start
            # page (the window made it) while they are read
            sp.hide()

        def later():
            if files:
                open_files()
                if failed:
                    w.report_open_failures(failed)
            w._update_start_page()
            if then is not None:
                then()

        # (the session's own deferred open was queued first, so it runs first)
        QtCore.QTimer.singleShot(0, w, later)
        return w
    if files:
        # explicit files override the saved session (restore prefs only, unless the setting
        # above: then they open after the last pages)
        w.restore_session(reopen=reopen)
        open_files()
    else:
        # no saved pages to reopen -> the start page (recent pages, Open, New);
        # session pages that do not reopen are reported by restore_session
        w.restore_session(reopen=reopen)
    w.show()
    w._update_start_page()
    if failed:
        w.report_open_failures(failed)
    if then is not None:
        then()
    return w


def _hold_app_mutex() -> None:
    if sys.platform != "win32" or _mutex:
        return
    try:
        import ctypes

        _mutex.append(ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX))
        # the taskbar groups and labels ctgedit as itself, not as python.exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 -- swallow-ok: cosmetic / installer convenience only
        pass


def release_app_mutex() -> None:
    """An update's installer starts and ctgedit quits right after: the installer must not
    find the mutex and ask to close ctgedit (ui/dialog/updates)."""
    if sys.platform != "win32":
        return
    import ctypes

    while _mutex:
        handle = _mutex.pop()
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)


def target_window():
    """The editor window a handed-over page opens in: the active one, else
    the newest visible one (None when no window is open)."""
    from .ui.main_window import MainWindow

    wins = [w for w in MainWindow.instances if w.isVisible()]
    for w in wins:
        if w.isActiveWindow():
            return w
    return wins[-1] if wins else None


class Forwarder(QtCore.QObject):
    """Opens pages a later launch handed over, queued until the first window is ready."""

    def __init__(self, parent: Optional[QtCore.QObject] = None, ready: bool = True):
        super().__init__(parent)
        self._pending: List[List[str]] = []
        self._ready = ready
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._retry)

    def handle(self, files: List[str]) -> None:
        self._pending.append(list(files))
        self.flush()

    def start(self) -> None:
        """The editor is ready: open what arrived during startup."""
        self._ready = True
        self.flush()

    def _retry(self) -> None:
        self.flush(activate=False)

    def flush(self, activate: bool = True) -> None:
        if not self._pending or not self._ready:
            return
        win = target_window()
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal is not None:
            if win is not None:
                names = ", ".join(os.path.basename(f) for b in self._pending for f in b)
                if names:
                    win.statusBar().showMessage("Close the open dialog to open %s" % names, 3000)
            if activate:
                # a new hand-over shows the dialog that holds it back (once:
                # the 300 ms retries must not keep taking the focus, and the
                # window under its own modal dialog is not activated)
                modal.raise_()
                modal.activateWindow()
            self._timer.start()
            return
        if win is None:
            from .ui.main_window import MainWindow

            win = MainWindow()
            win.show()
        batches, self._pending = self._pending, []
        for files in batches:
            if files:
                win.open_files(files)
        win.bring_to_front()


def _report_crashes(win, reports: List[str]) -> None:
    """The previous run crashed: say where its report is (non-modal)."""
    box = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Warning,
        "ctgedit Closed Unexpectedly",
        "ctgedit did not close normally last time. A crash report was saved "
        "to:\n\n%s\n\nAttach it when you report the problem." % reports[-1],
        QtWidgets.QMessageBox.Ok,
        win,
    )
    folder = box.addButton("Open Report Folder", QtWidgets.QMessageBox.ActionRole)

    def clicked(button):
        if button is folder:
            from PySide6 import QtGui

            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(reports[-1])))

    box.buttonClicked.connect(clicked)
    box.setModal(False)
    box.show()
    win._crash_notice = box


def _say(text: str, title: str = "ctgedit") -> None:
    """Command-line feedback: the console when there is one, else a box (the
    windowed build has no console)."""
    if sys.stdout is not None:
        print(text)
        return
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from .ui.messages import inform

    inform(None, title, text)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    # Settings > Pages and libraries: the text code page and the extra library folders, before
    # anything reads a page (the helper processes inherit them)
    try:
        from .ui.prefs.settings import apply_codepage_at_start, apply_library_folders

        apply_codepage_at_start()
        apply_library_folders()
    except Exception:  # noqa: BLE001 -- swallow-ok: the defaults then
        pass
    if argv[1:2] == ["--ctf-check"]:
        # the internal options below run a helper in its own process (not for the command line)
        # the editor's fast-display trust check (editing.CtfPrecheck)
        from .editing import ctf_check_main

        return ctf_check_main(argv[2:])
    if argv[1:2] == ["--genie-update"]:
        # a page's genies brought up to date (genie_worker)
        from .workers.genie_worker import main as genie_update_main

        return genie_update_main(argv[2:])
    if argv[1:2] == ["--genie-serve"]:
        # the warm genie worker of an editor window (genie_worker.serve_main)
        from .workers.genie_worker import serve_main

        return serve_main(argv[2:])
    if argv[1:2] == ["--update-closed-pages"]:
        # the closed pages that use a saved genie (genie_worker)
        from .workers.genie_worker import users_main

        return users_main(argv[2:])
    if argv[1:2] == ["--find"]:
        # Find / Replace in a project's closed pages (find_worker)
        from .workers.find_worker import main as find_main

        return find_main(argv[2:])
    if argv[1:2] == ["--check"]:
        # Check Project (check_worker)
        from .workers.check_worker import main as check_main

        return check_main(argv[2:])
    if argv[1:2] == ["--rename"]:
        # Rename Pages (rename_worker)
        from .workers.rename_worker import main as rename_main

        return rename_main(argv[2:])
    if argv[1:2] == ["--pack"]:
        # Pack Database (pack_worker)
        from .workers.pack_worker import main as pack_main

        return pack_main(argv[2:])
    if argv[1:2] == ["--pictures"]:
        # Find Duplicate Pictures' scan and changes (picture_worker)
        from .workers.picture_worker import main as pictures_main

        return pictures_main(argv[2:])
    if argv[1:2] == ["--replace-genie"]:
        # Replace Genie on every page of some projects (genieswap_worker)
        from .workers.genieswap_worker import main as replace_genie_main

        return replace_genie_main(argv[2:])
    if argv[1:2] == ["--display-digest"]:
        # the display check's fresh read of a page (diag.display_check)
        from .diag.render_digest import display_digest_main

        return display_digest_main(argv[2:])
    if argv[1:2] == ["--sim-page-model"]:
        # a Simulate tab's page model (ui.simulate.modeljob)
        from .ui.simulate.modeljob import main as sim_page_model_main

        return sim_page_model_main(argv[2:])
    if argv[1:2] == ["--preview-render"]:
        # the page browser's live render (ui.browser.preview)
        from .ui.browser.preview import preview_main

        return preview_main(argv[2:])
    opts = parse_args(argv[1:])
    if opts.error:
        _say("%s\n\n%s" % (opts.error, USAGE), "ctgedit: bad command line")
        return 2
    if opts.version:
        from .buildinfo import version_string

        _say("ctgedit " + version_string())
        return 0
    from . import crashlog
    from .infra import logsetup

    crashlog.install()
    logsetup.install()
    try:
        from .ui.dialog.about import register_diagnostics

        register_diagnostics()
    except Exception:  # noqa: BLE001 -- the report then has the essentials only
        _note_exc("app.main")
    try:
        _install_error_hook()
        if opts.smoke:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
            if os.path.isdir(fonts):
                os.environ.setdefault("QT_QPA_FONTDIR", fonts)
            from .selftest import run

            return run(opts.files, opts.report)
        return _run_editor(argv, opts)
    finally:
        logsetup.uninstall()
        crashlog.uninstall()


#: how long a thread holds the GIL while another waits for it (Python's default is 5 ms).
#: The GUI thread lets go of the GIL in many Qt calls of one event; with a worker running
#: (the Tags panel's tag index and search, the Cicode catalogue) each one waits up to the
#: interval to get it back, which stalls the GUI thread
GUI_SWITCH_INTERVAL = 0.001


def tune_gil() -> None:
    """Set the GIL switch interval and a 1 ms timer resolution before any threads start."""
    sys.setswitchinterval(GUI_SWITCH_INTERVAL)
    if sys.platform == "win32" and not _timer_period:
        try:
            import ctypes

            if ctypes.windll.winmm.timeBeginPeriod(1) == 0:  # TIMERR_NOERROR
                _timer_period.append(1)
        except Exception:  # noqa: BLE001 -- swallow-ok: the default resolution then
            pass


#: the timer resolution tune_gil asked for (ended by clean_exit)
_timer_period: list = []


def hands_over(opts: Options) -> bool:
    """This start gives its pages to the editor already running (and becomes the one later
    starts give theirs to): not with --new-instance, CTG_NO_SINGLE_INSTANCE=1 or Settings >
    Pages and libraries 'Open pages in the ctgedit already running' off."""
    if opts.new_instance or os.environ.get("CTG_NO_SINGLE_INSTANCE") == "1":
        return False
    from .ui.prefs.window_options import single_instance

    return single_instance()


def _run_editor(argv: List[str], opts: Options) -> int:
    from . import __version__, crashlog

    tune_gil()
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("ctgedit")
    app.setOrganizationName("ctgedit")
    app.setApplicationVersion(__version__)
    # before any worker thread exists: garbage is collected on this (the GUI) thread only
    from .infra import gcguard

    gcguard.install()
    gcguard.install_timer(app)
    forwarder = None
    if hands_over(opts):
        from .ui import single_instance

        lock = single_instance.StartupLock()
        lock.acquire()
        try:
            if single_instance.forward(opts.files):
                return 0  # the running editor has the pages now
            server = single_instance.InstanceServer(parent=app)
            if server.listen():
                # connected before anything can process events: a page handed
                # over while this window is still starting is queued, not lost
                forwarder = Forwarder(app, ready=False)
                server.filesRequested.connect(forwarder.handle)
                # an editor that is quitting takes no more pages: a launch
                # from then on starts an editor of its own
                app.aboutToQuit.connect(server.close)
            # (not listening: still usable, just without the hand-over)
        finally:
            lock.release()
    # only the process that became the editor reports an earlier crash (a
    # launch that handed its pages over would take the report and exit)
    earlier = crashlog.collect_previous_crashes()
    # the editor's lock waits (format/projectlock): its GUI waits briefly for another
    # ctgedit process's database write, then warns
    editor_waits()
    # project-database updates a crashed run left half done are undone
    # BEFORE any page opens (format/journal)
    journal_notes = recover_journals() + pending_rows_notes()
    _hold_app_mutex()
    from .ui.style.theme import apply_theme, app_icon

    apply_theme(app)
    app.setWindowIcon(app_icon())
    # the window shows first, its pages open right after (start_window);
    # pages a later launch handed over meanwhile open after them
    w = start_window(argv, opts.files, defer=True, then=forwarder.start if forwarder is not None else None)
    if earlier:
        QtCore.QTimer.singleShot(0, w, lambda: _report_crashes(w, earlier))
    if journal_notes:
        QtCore.QTimer.singleShot(0, w, lambda: w.report_journal_recovery(journal_notes))
    # the unsaved changes a crashed / killed run left behind (infra/autosave)
    QtCore.QTimer.singleShot(0, w, w.offer_recovery)
    # a newer ctgedit on GitHub (the installed ctgedit, once a day: ui/dialog/updates)
    from .ui.dialog.updates import schedule_startup_check

    schedule_startup_check(w)
    try:
        return app.exec()
    finally:
        clean_exit()


def editor_waits() -> None:
    """The lock waits from Settings > Pages and libraries > Busy projects."""
    try:
        from .ui.prefs.settings import apply_lock_waits

        apply_lock_waits()
    except Exception:  # noqa: BLE001 -- swallow-ok: the module defaults then
        pass


def pending_rows_notes() -> list:
    """Start-up: the pages saved without their project database rows (io/pendingrows: a
    run stopped during a save, or the database was in use), as one note for
    report_journal_recovery; [] when there are none."""
    try:
        from .io import pendingrows

        marks = pendingrows.listing()
    except Exception:  # noqa: BLE001 -- never stop the editor from starting
        from .infra import logsetup

        logsetup.note_exception("app.pending_rows_notes")
        return []
    if not marks:
        return []
    from .ui.names import plural

    pages = [
        "%s (%s)"
        % (os.path.splitext(os.path.basename(m["page"]))[0], os.path.basename(os.path.dirname(m["page"])))
        for m in marks
    ]
    one = len(pages) == 1
    # (discover: the note carries the page files, so the box can open them)
    return [
        {
            "text": "%s %s saved but not registered in %s project yet (ctgedit stopped "
            "during a save, or the project database was in use). Save %s again, or "
            "run Tools > Update Pages on %s: %s"
            % (
                plural(len(pages), "page"),
                "was" if one else "were",
                "its" if one else "their",
                "it" if one else "them",
                "its project" if one else "their projects",
                ", ".join(pages[:20]) + (", …" if len(pages) > 20 else ""),
            ),
            "pages": [str(m["page"]) for m in marks if m.get("page")],
        }
    ]


def recover_journals() -> list:
    """Roll back the database journals of runs that died mid-update."""
    try:
        from .format import journal
        from .infra import logsetup

        notes = journal.recover_stale()
        for n in notes:
            logsetup.get("journal").warning(
                "rolled back an unfinished update (%s): restored %s; left alone (changed since) %s",
                n.get("label"),
                n.get("restored"),
                n.get("skipped"),
            )
        return notes
    except Exception:  # noqa: BLE001 -- never stop the editor from starting
        _note_exc("app.recover_journals")
        return []


def clean_exit() -> None:
    """A normal exit: this run's recovery copies go, and a fast-display check process still
    running is stopped."""
    import contextlib

    with contextlib.suppress(Exception):
        from .infra import autosave

        autosave.remove_process()
    with contextlib.suppress(Exception):
        from .editing import CtfPrecheck

        CtfPrecheck.cancel_all()
    with contextlib.suppress(Exception):
        while _timer_period:
            import ctypes

            ctypes.windll.winmm.timeEndPeriod(_timer_period.pop())


if __name__ == "__main__":
    raise SystemExit(main())
