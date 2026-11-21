"""Updates: the newest release on GitHub, the installer's download and check, the dialog and
the check when ctgedit starts. Nothing here reaches the network: GitHub's answers are faked."""

import hashlib
import io
import json
import time
import urllib.error

import pytest
from PySide6 import QtCore, QtGui, QtWidgets
from qtkit import app as _app

from ctgedit import __version__
from ctgedit.infra import updates
from ctgedit.ui.dialog import updates as dlg_updates

PAYLOAD = b"fx installer " * 1000


def _release_data(version="99.0.0", payload=PAYLOAD, **asset):
    a = {
        "name": updates.installer_name(version),
        "browser_download_url": "https://example.invalid/ctgedit-%s-setup.exe" % version,
        "size": len(payload),
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }
    a.update(asset)
    return {
        "tag_name": "v" + version,
        "html_url": "https://example.invalid/releases/v" + version,
        "body": "## What is new\n\n- Faster pages.",
        "assets": [{"name": "other.zip"}, a],
    }


def _fake_github(monkeypatch, data=None, payload=PAYLOAD, error=None):
    """updates._open answers as GitHub would: the release JSON, then the installer's bytes."""
    seen = []

    def fake_open(url, timeout, accept):
        seen.append(url)
        if error is not None:
            raise error
        if url == updates.LATEST_URL:
            return io.BytesIO(json.dumps(data if data is not None else _release_data()).encode())
        return io.BytesIO(payload)

    monkeypatch.setattr(updates, "_open", fake_open)
    return seen


def _wait(cond, ms=5000):
    end = time.monotonic() + ms / 1000
    while not cond():
        assert time.monotonic() < end, "timed out"
        QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)


# -- the release and the download ---------------------------------------------------------------
def test_versions_compare_as_numbers():
    assert updates.version_key("v1.10.0") == (1, 10, 0)
    assert updates.version_key("1.2") == (1, 2)
    assert updates.version_key("1.2-beta") is None
    assert updates.is_newer("v0.0.10", "0.0.9")
    assert updates.is_newer("1.1", "1.0.9")
    assert not updates.is_newer("1.0", "1.0.0")
    assert not updates.is_newer("garbage", "1.0")


def test_a_release_names_its_installer_and_checksum():
    r = updates.parse_release(_release_data())
    assert r.version == "99.0.0"
    assert r.asset_name == "ctgedit-99.0.0-setup.exe"
    assert r.size == len(PAYLOAD)
    assert r.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert "Faster pages" in r.notes


@pytest.mark.parametrize(
    "data, words",
    [
        ({"tag_name": "nightly"}, "no version"),
        ({"tag_name": "v99.0.0", "assets": []}, "no installer"),
        (_release_data(digest=""), "no checksum"),
        (_release_data(browser_download_url="http://example.invalid/x.exe"), "no secure"),
    ],
)
def test_a_release_without_a_safe_installer_is_refused(data, words):
    with pytest.raises(updates.UpdateError, match=words):
        updates.parse_release(data)


@pytest.mark.parametrize(
    "error, words",
    [
        (urllib.error.HTTPError(updates.LATEST_URL, 404, "Not Found", None, None), "No release"),
        (urllib.error.HTTPError(updates.LATEST_URL, 503, "Busy", None, None), "error 503"),
        (urllib.error.URLError("offline"), "Could not reach GitHub"),
    ],
)
def test_a_failed_check_says_why(monkeypatch, error, words):
    _fake_github(monkeypatch, error=error)
    with pytest.raises(updates.UpdateError, match=words):
        updates.latest_release()


def test_the_download_is_checked_against_its_digest(monkeypatch, tmp_path):
    _fake_github(monkeypatch)
    r = updates.parse_release(_release_data())
    steps = []
    path = updates.download(r, str(tmp_path), progress=lambda n, total: steps.append((n, total)))
    assert open(path, "rb").read() == PAYLOAD
    assert steps[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert sorted(p.name for p in tmp_path.iterdir()) == [r.asset_name]

    _fake_github(monkeypatch, payload=PAYLOAD[:-1] + b"X")
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.download(r, str(tmp_path / "bad"))
    assert list((tmp_path / "bad").iterdir()) == []


def test_a_cancelled_download_leaves_nothing(monkeypatch, tmp_path):
    _fake_github(monkeypatch)
    r = updates.parse_release(_release_data())
    assert updates.download(r, str(tmp_path), cancelled=lambda: True) is None
    assert list(tmp_path.iterdir()) == []


# -- the dialog ---------------------------------------------------------------------------------
def test_the_dialog_says_when_ctgedit_is_up_to_date(monkeypatch):
    _app()
    _fake_github(monkeypatch, data=_release_data(version=__version__))
    d = dlg_updates.UpdateDialog()
    _wait(lambda: "latest version" in d._message.text())
    assert not d._install.isVisible() and dlg_updates.last_check() > 0
    d.deleteLater()


def test_a_failed_check_offers_try_again(monkeypatch):
    _app()
    _fake_github(monkeypatch, error=urllib.error.URLError("offline"))
    d = dlg_updates.UpdateDialog()
    d.show()
    _wait(lambda: "Could not reach" in d._message.text())
    assert d._retry.isVisible() and not d._install.isVisible()
    _fake_github(monkeypatch)
    d._retry.click()
    _wait(lambda: "99.0.0 is out" in d._message.text())
    d.close()
    d.deleteLater()


def test_run_from_source_the_release_page_opens(monkeypatch):
    _app()
    _fake_github(monkeypatch)
    opened = []
    monkeypatch.setattr(
        QtGui.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString()))
    )
    d = dlg_updates.UpdateDialog()
    d.show()
    _wait(lambda: d.release is not None)
    assert d._install.text() == "Open Release Page"
    assert "Faster pages" in d._notes.toPlainText()
    d._install.click()
    assert opened == ["https://example.invalid/releases/v99.0.0"]
    assert d.result() == QtWidgets.QDialog.Rejected and d.installer is None
    d.deleteLater()


def test_skip_this_version_is_remembered(monkeypatch):
    _app()
    _fake_github(monkeypatch)
    d = dlg_updates.UpdateDialog()
    d.show()
    _wait(lambda: d.release is not None)
    d._skip.click()
    assert dlg_updates.skipped_version() == "99.0.0"
    d.deleteLater()


def test_install_downloads_the_installer_then_accepts(monkeypatch, tmp_path):
    _app()
    _fake_github(monkeypatch)
    monkeypatch.setattr(dlg_updates, "installed", lambda: True)
    monkeypatch.setattr(dlg_updates, "download_folder", lambda: str(tmp_path))
    d = dlg_updates.UpdateDialog()
    d.show()
    _wait(lambda: d.release is not None)
    assert d._install.text() == "Install"
    d._install.click()
    _wait(lambda: d.installer is not None)
    assert d.result() == QtWidgets.QDialog.Accepted
    assert open(d.installer, "rb").read() == PAYLOAD
    d.deleteLater()


# -- installing ---------------------------------------------------------------------------------
def test_install_closes_the_windows_starts_the_installer_and_quits(monkeypatch):
    _app()
    from ctgedit import app as ctg_app
    from ctgedit.ui.main_window import MainWindow

    started, quit_ = [], []
    monkeypatch.setattr(MainWindow, "editor_windows", classmethod(lambda cls: []))
    monkeypatch.setattr(ctg_app, "release_app_mutex", lambda: started.append("mutex"))
    monkeypatch.setattr(
        QtCore.QProcess, "startDetached", staticmethod(lambda exe, args: started.append((exe, args)) or True)
    )
    monkeypatch.setattr(QtWidgets.QApplication, "quit", staticmethod(lambda: quit_.append(1)))
    assert dlg_updates.install_and_quit(r"C:\fx\ctgedit-99.0.0-setup.exe")
    assert started == ["mutex", (r"C:\fx\ctgedit-99.0.0-setup.exe", list(dlg_updates.INSTALLER_ARGS))]
    assert "/RELAUNCH=1" in dlg_updates.INSTALLER_ARGS and quit_ == [1]


def test_a_window_that_stays_open_stops_the_install(monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow

    class Stays(QtWidgets.QMainWindow):
        def close(self):
            return False

    w = Stays()
    started = []
    monkeypatch.setattr(MainWindow, "editor_windows", classmethod(lambda cls: [w]))
    monkeypatch.setattr(QtCore.QProcess, "startDetached", staticmethod(lambda *a: started.append(a) or True))
    assert not dlg_updates.install_and_quit(r"C:\fx\setup.exe")
    assert started == [] and "not installed" in w.statusBar().currentMessage()
    w.deleteLater()


# -- the check when ctgedit starts --------------------------------------------------------------
def test_the_startup_check_runs_for_the_installed_ctgedit_once_a_day(monkeypatch):
    _app()
    from ctgedit.ui.prefs.settings import app_settings, set_option

    timers = []
    monkeypatch.setattr(QtCore.QTimer, "singleShot", staticmethod(lambda ms, ctx, fn: timers.append(ms)))
    w = QtWidgets.QMainWindow()
    dlg_updates.schedule_startup_check(w)  # run from source
    assert timers == []
    monkeypatch.setattr(dlg_updates, "installed", lambda: True)
    dlg_updates.schedule_startup_check(w)
    assert timers == [dlg_updates.STARTUP_DELAY_MS]
    app_settings().setValue("updates/last_check", time.time() - 60)
    dlg_updates.schedule_startup_check(w)
    app_settings().setValue("updates/last_check", 0.0)
    set_option("check_updates", False)
    dlg_updates.schedule_startup_check(w)
    assert timers == [dlg_updates.STARTUP_DELAY_MS]
    w.deleteLater()


def test_a_new_version_shows_in_the_status_bar_unless_skipped(monkeypatch):
    _app()
    from ctgedit.ui.prefs.settings import app_settings

    _fake_github(monkeypatch)
    w = QtWidgets.QMainWindow()
    dlg_updates._startup_check(w)
    _wait(lambda: w.findChild(QtWidgets.QToolButton, "update_available") is not None)
    b = w.findChild(QtWidgets.QToolButton, "update_available")
    assert b.text() == "ctgedit 99.0.0 is out"
    assert dlg_updates.last_check() > 0
    w.deleteLater()

    app_settings().setValue("updates/skip_version", "99.0.0")
    app_settings().setValue("updates/last_check", 0.0)
    w2 = QtWidgets.QMainWindow()
    dlg_updates._startup_check(w2)
    _wait(lambda: dlg_updates.last_check() > 0)
    assert w2.findChild(QtWidgets.QToolButton, "update_available") is None
    w2.deleteLater()
