"""Help > Check for Updates, and the check when ctgedit starts (the installed ctgedit only:
it can install the new version itself; run from source, the release page opens instead)."""

from __future__ import annotations

import glob
import os
import sys
import tempfile
import threading
import time
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ... import __version__
from ...infra import updates
from ...infra.logsetup import note_exception
from ..messages import warn

#: the check when ctgedit starts: at most once in this many seconds, this long after the start
STARTUP_INTERVAL_S = 20 * 3600
STARTUP_DELAY_MS = 10_000
#: what the installer is started with: no questions, and ctgedit opens again when it is done
#: (packaging/installer.iss, RelaunchAfterUpdate)
INSTALLER_ARGS = ("/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1")


def installed() -> bool:
    """This ctgedit was installed by its installer (its uninstaller is next to it)."""
    if not getattr(sys, "frozen", False):
        return False
    return bool(glob.glob(os.path.join(glob.escape(os.path.dirname(sys.executable)), "unins*.exe")))


def download_folder() -> str:
    return os.path.join(tempfile.gettempdir(), "ctgedit-update")


def _settings():
    from ..prefs.settings import app_settings

    return app_settings()


def last_check() -> float:
    try:
        return float(_settings().value("updates/last_check", 0.0))
    except TypeError, ValueError:
        return 0.0


def _checked_now() -> None:
    _settings().setValue("updates/last_check", time.time())


def skipped_version() -> str:
    return str(_settings().value("updates/skip_version", "") or "")


class _Job(QtCore.QObject):
    """``fn(job)`` on a worker thread; ``finished(result, error text or None)`` on the GUI
    thread. ``job.cancel()`` asks a download to stop."""

    finished = QtCore.Signal(object, object)
    progressed = QtCore.Signal(int, int)

    def __init__(self, fn: Callable[["_Job"], object], parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()

    def start(self) -> None:
        from ...infra import gcguard

        gcguard.before_worker()
        threading.Thread(target=self._run, name="ctgedit-update", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _run(self) -> None:
        result, error = None, None
        try:
            result = self._fn(self)
        except updates.UpdateError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 -- the dialog says it failed
            note_exception("updates.job", e)
            error = "The update failed: %s" % e
        try:
            self.finished.emit(result, error)
        except RuntimeError:  # swallow-ok: the dialog was closed meanwhile
            pass


class UpdateDialog(QtWidgets.QDialog):
    """Checks for a newer ctgedit (or shows ``release``), downloads its installer and
    accepts with :attr:`installer` set: the caller then runs :func:`install_and_quit`."""

    def __init__(self, parent=None, release: Optional[updates.Release] = None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.installer: Optional[str] = None
        self.release: Optional[updates.Release] = None
        self._job: Optional[_Job] = None

        lay = QtWidgets.QVBoxLayout(self)
        self._message = QtWidgets.QLabel()
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self._message)
        self._notes = QtWidgets.QTextBrowser()
        self._notes.setOpenExternalLinks(True)
        self._notes.setMinimumHeight(180)
        lay.addWidget(self._notes, 1)
        self._bar = QtWidgets.QProgressBar()
        lay.addWidget(self._bar)

        self._buttons = QtWidgets.QDialogButtonBox()
        self._install = self._buttons.addButton("Install", QtWidgets.QDialogButtonBox.AcceptRole)
        self._skip = self._buttons.addButton("Skip This Version", QtWidgets.QDialogButtonBox.ActionRole)
        self._skip.setToolTip("Do not mention this version when ctgedit starts")
        self._retry = self._buttons.addButton("Try Again", QtWidgets.QDialogButtonBox.ActionRole)
        self._stop = self._buttons.addButton("Cancel", QtWidgets.QDialogButtonBox.RejectRole)
        self._close = self._buttons.addButton(QtWidgets.QDialogButtonBox.Close)
        for b in (self._install, self._skip, self._retry, self._stop, self._close):
            b.setAutoDefault(False)
        # (the box's accepted / rejected signals are not used: each button has its own slot)
        self._install.clicked.connect(self._install_clicked)
        self._skip.clicked.connect(self._skip_clicked)
        self._retry.clicked.connect(self.check)
        self._stop.clicked.connect(self._stop_download)
        self._close.clicked.connect(self.reject)
        lay.addWidget(self._buttons)
        self.setMinimumWidth(480)

        if release is not None:
            self._show_release(release)
        else:
            self.check()

    # -- states -----------------------------------------------------------------------------
    def _state(self, text: str, buttons=(), notes: str = "", bar: bool = False) -> None:
        self._message.setText(text)
        self._notes.setVisible(bool(notes))
        if notes:
            self._notes.setMarkdown(notes)
        self._bar.setVisible(bar)
        for b in (self._install, self._skip, self._retry, self._stop):
            b.setVisible(b in buttons)
        self._close.setVisible(self._stop not in buttons)
        (buttons[0] if buttons else self._close).setDefault(True)
        self.adjustSize()

    def check(self) -> None:
        self._state("Checking for a newer version of ctgedit…")
        self._run(lambda job: updates.latest_release(), self._checked)

    def _checked(self, release, error) -> None:
        if error:
            self._failed(error)
            return
        _checked_now()
        if not updates.is_newer(release.version):
            self._state("You have the latest version of ctgedit (%s)." % __version__)
            return
        self._show_release(release)

    def _show_release(self, release: updates.Release) -> None:
        self.release = release
        if installed():
            self._install.setText("Install")
            self._install.setToolTip("Download the installer, close ctgedit and install the new version")
        else:
            self._install.setText("Open Release Page")
            self._install.setToolTip(
                "This ctgedit was not installed with its installer: get the new version there"
            )
        self._state(
            "ctgedit %s is out. You have %s." % (release.version, __version__),
            (self._install, self._skip),
            notes=release.notes,
        )

    def _failed(self, error: str) -> None:
        self._state(error, (self._retry,))

    # -- actions ----------------------------------------------------------------------------
    def _install_clicked(self) -> None:
        release = self.release
        if release is None:
            return
        if not installed():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(release.page))
            self.reject()
            return
        self._bar.setRange(0, max(1, release.size))
        self._bar.setValue(0)
        self._state("Downloading ctgedit %s…" % release.version, (self._stop,), bar=True)
        self._run(
            lambda job: updates.download(
                release, download_folder(), progress=job.progressed.emit, cancelled=job.cancelled
            ),
            self._downloaded,
            progress=True,
        )

    def _downloaded(self, path, error) -> None:
        if error:
            self._failed(error)
            return
        if path is None:  # cancelled
            if self.release is not None:
                self._show_release(self.release)
            return
        self.installer = path
        self.accept()

    def _skip_clicked(self) -> None:
        if self.release is not None:
            _settings().setValue("updates/skip_version", self.release.version)
        self.reject()

    def _stop_download(self) -> None:
        if self._job is not None:
            self._job.cancel()

    def reject(self) -> None:
        self._stop_download()
        super().reject()

    def _run(self, fn, done, progress: bool = False) -> None:
        if self._job is not None:
            self._job.cancel()
        job = _Job(fn, self)
        self._job = job

        def finished(result, error):
            if self._job is job:
                self._job = None
                done(result, error)
            job.deleteLater()

        job.finished.connect(finished)
        if progress:
            job.progressed.connect(lambda n, total: self._bar.setValue(min(n, self._bar.maximum())))
        job.start()


def run_update_dialog(window, release: Optional[updates.Release] = None) -> None:
    """Help > Check for Updates (and the status bar's notice of a new version)."""
    dlg = UpdateDialog(window, release)
    try:
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.installer:
            install_and_quit(dlg.installer, window)
    finally:
        dlg.deleteLater()


def install_and_quit(installer: str, window=None) -> bool:
    """Close every editor window (each asks about its unsaved pages), start ``installer``
    and quit. False when a window stayed open: nothing is installed."""
    from ..main_window import MainWindow

    for w in list(MainWindow.editor_windows()):
        if not w.close():
            w.statusBar().showMessage("The update was not installed: this window stayed open.", 12000)
            return False
    from ... import app

    app.release_app_mutex()
    if not QtCore.QProcess.startDetached(installer, list(INSTALLER_ARGS)):
        warn(
            None,
            "Check for Updates",
            "The installer did not start. Run it yourself: %s" % installer,
        )
    QtWidgets.QApplication.quit()
    return True


# -- the check when ctgedit starts ------------------------------------------------------------
def schedule_startup_check(window) -> None:
    """Check for a new version a little after the start, at most once a day, when this
    ctgedit was installed and the setting is on (Settings > Advanced > Updates)."""
    from ..prefs.settings import option

    if not installed() or not option("check_updates"):
        return
    if time.time() - last_check() < STARTUP_INTERVAL_S:
        return
    QtCore.QTimer.singleShot(STARTUP_DELAY_MS, window, lambda: _startup_check(window))


def _startup_check(window) -> None:
    job = _Job(lambda job: updates.latest_release(), window)

    def finished(release, error):
        job.deleteLater()
        if error:
            return  # offline: no notice, the next start tries again
        _checked_now()
        if updates.is_newer(release.version) and release.version != skipped_version():
            show_available(window, release)

    job.finished.connect(finished)
    job.start()


def show_available(window, release: updates.Release) -> QtWidgets.QToolButton:
    """A button in the status bar that says a new version is out and opens the dialog."""
    b = QtWidgets.QToolButton()
    b.setObjectName("update_available")
    b.setAutoRaise(True)
    b.setText("ctgedit %s is out" % release.version)
    b.setToolTip("See what is new and install it")

    def clicked():
        window.statusBar().removeWidget(b)
        b.deleteLater()
        run_update_dialog(window, release)

    b.clicked.connect(clicked)
    window.statusBar().addPermanentWidget(b)
    return b
