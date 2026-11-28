"""Version, build identity, the command line and single instance."""

import glob
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import uuid

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6 import QtCore, QtNetwork, QtWidgets  # noqa: E402
from qtkit import app as _app

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: the page of the hand-over and smoke tests (FxMain's)
PAGE = "FxShapes"


def _child_env(tmp_path, **extra):
    """A child process that imports THIS checkout, headless, logging into
    tmp_path (the settings INI of the test is inherited)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["CTG_LOG_DIR"] = str(tmp_path / "logs")
    env.update(extra)
    return env


def _pump(until, timeout=10.0):
    app = _app()
    end = time.time() + timeout
    while not until() and time.time() < end:
        app.processEvents()
        QtCore.QThread.msleep(10)
    return until()


def _release_check():
    spec = importlib.util.spec_from_file_location(
        "release_check", os.path.join(REPO, "tools", "release_check.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pins(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s;#\\]+)", line)
            if m:
                out[m.group(1).lower()] = m.group(2)
    return out


# --------------------------------------------------------------- version / pins
def test_version_has_one_source_and_the_pins_agree():
    from ctgedit import __version__

    assert re.match(r"^\d+\.\d+\.\d+$", __version__)
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as fh:
        project = tomllib.load(fh)
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "ctgedit.__version__"}
    assert project["project"]["requires-python"].startswith(">=%d.%d" % sys.version_info[:2])
    deps = {}
    for spec in project["project"]["dependencies"]:
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", spec)
        assert m, "pyproject dependency %r is not pinned" % spec
        deps[m.group(1).lower()] = m.group(2)
    req = _pins(os.path.join(REPO, "requirements.txt"))
    lock = _pins(os.path.join(REPO, "requirements.lock"))
    for name, ver in deps.items():
        assert req.get(name) == ver, "requirements.txt and pyproject.toml disagree on %s" % name
        assert lock.get(name) == ver, "requirements.lock has an other %s" % name
    # the dev pins: the same in pyproject; Pillow is for the tools and their tests only
    dev = dict(s.split("==") for s in project["project"]["optional-dependencies"]["dev"])
    assert {k.lower(): v for k, v in dev.items()} == {k: req[k] for k in ("pytest", "pillow")}
    assert "pillow" not in deps
    for d, _subs, files in os.walk(os.path.join(REPO, "ctgedit")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(d, f), encoding="utf-8").read()
                assert not re.search(r"^\s*(from|import) PIL\b", src, re.M), f
    assert {"pyside6", "numpy", "dbfread", "pywin32"} <= set(deps)
    # the build tools: pinned once, the same in pyproject and the build file
    build = _pins(os.path.join(REPO, "packaging", "requirements-build.txt"))
    extra = dict(s.split("==") for s in project["project"]["optional-dependencies"]["build"])
    assert build["pyinstaller"] == extra["pyinstaller"]


def test_build_identity_from_git_and_from_the_build_stamp(tmp_path, monkeypatch):
    from ctgedit import __version__, buildinfo

    if not buildinfo.git_identity()["commit"]:
        pytest.skip("not a git checkout / git not installed")
    monkeypatch.setattr(buildinfo, "BUILD_INFO_FILE", str(tmp_path / "none.json"))
    buildinfo.build_info.cache_clear()
    try:
        info = buildinfo.build_info()
        assert info["source"] == "git" and info["version"] == __version__
        assert info["commit"] and re.match(r"^\d{4}-\d\d-\d\d$", info["date"])
        assert buildinfo.version_string(info) == "%s (%s, %s)" % (__version__, info["commit"], info["date"])
        # what the PyInstaller spec stamps into the frozen build
        stamp = tmp_path / "_build_info.json"
        written = buildinfo.write_build_info(str(stamp))
        assert written["commit"] == info["commit"] and written["built"].endswith("UTC")
        assert written["version"] == info["version"]
        # a built program: its stamp says the version (it has no tags to read)
        data = json.loads(stamp.read_text(encoding="utf-8"))
        data["version"] = "7.8.9"
        stamp.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(buildinfo, "BUILD_INFO_FILE", str(stamp))
        buildinfo.build_info.cache_clear()
        frozen = buildinfo.build_info()
        assert frozen["source"] == "build" and frozen["version"] == "7.8.9" == buildinfo.version()
        assert frozen["commit"] == written["commit"] and frozen["built"] == written["built"]
    finally:
        buildinfo.build_info.cache_clear()


@pytest.mark.parametrize(
    "described, version",
    [
        ("v0.1.11", "0.1.11"),  # the tagged commit
        ("v0.1.11-3-g6d9d2bb", "0.1.12"),  # a later one: told apart from the release
        ("v2.0.9-1-gabcdef0", "2.0.10"),
        ("", "0.0.0"),  # no release tag yet
        ("6d9d2bb", "0.0.0"),
    ],
)
def test_the_version_is_the_newest_release_tag(described, version):
    from ctgedit import buildinfo

    assert buildinfo.version_from_describe(described) == version


def test_a_release_tag_names_the_version(tmp_path):
    """In a repository: the tag's version on its commit, the next patch level after it,
    and a pre-release tag (v0.3.0-rc1) is not a release."""
    import subprocess

    from ctgedit import buildinfo

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    try:
        git("init", "-q")
    except OSError, subprocess.CalledProcessError:
        pytest.skip("git is not installed")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    assert buildinfo.git_identity(str(tmp_path))["version"] == "0.0.0"
    git("tag", "v0.2.4")
    assert buildinfo.git_identity(str(tmp_path))["version"] == "0.2.4"
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "b")
    git("tag", "v0.3.0-rc1")
    assert buildinfo.git_identity(str(tmp_path))["version"] == "0.2.5"


def test_unknown_commit_is_said_plainly():
    from ctgedit import buildinfo

    assert (
        buildinfo.version_string({"version": "1.0.0", "commit": "", "date": ""}) == "1.0.0 (unknown commit)"
    )


# --------------------------------------------------------------- About
_FAKE = {
    "version": "1.2.3",
    "commit": "v1.2.3-4-gabc1234",
    "date": "2026-09-25",
    "built": "2026-09-25 10:00 UTC",
    "source": "build",
}


def test_about_shows_version_commit_and_date(monkeypatch):
    _app()
    from ctgedit import buildinfo
    from ctgedit.ui import help as H
    from ctgedit.ui.dialog.about import AboutDialog

    monkeypatch.setattr(buildinfo, "build_info", lambda: dict(_FAKE))
    dlg = AboutDialog()
    shown = {
        lab.objectName(): lab.text()
        for lab in dlg.findChildren(QtWidgets.QLabel)
        if lab.objectName().startswith("about_")
    }
    assert shown["about_version"] == "1.2.3"
    assert shown["about_commit"] == "v1.2.3-4-gabc1234"
    assert shown["about_commit_date"] == "2026-09-25"
    assert shown["about_built"] == "2026-09-25 10:00 UTC"
    assert shown["about_python"] == sys.version.split()[0]
    dlg.copy_details()
    clip = QtWidgets.QApplication.clipboard().text()
    assert "Version: 1.2.3" in clip and "Commit: v1.2.3-4-gabc1234" in clip
    assert "AboutDialog" in H.DIALOG_TOPICS
    dlg.deleteLater()


def test_help_menu_about_opens_the_about_dialog(monkeypatch):
    _app()
    from ctgedit.ui.dialog import about
    from ctgedit.ui.main_window import MainWindow

    opened = []
    monkeypatch.setattr(about.AboutDialog, "exec", lambda self: opened.append(self) or 0)
    w = MainWindow()
    try:
        acts = [
            a
            for m in w.menuBar().findChildren(QtWidgets.QMenu)
            for a in m.actions()
            if a.text().startswith("About ctgedit")
        ]
        assert acts
        acts[0].trigger()
        assert len(opened) == 1 and opened[0].windowTitle() == "About ctgedit"
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_app_icon_is_a_real_ico(tmp_path):
    _app()
    from PySide6 import QtGui
    from ctgedit.ui.style.theme import APP_ICON_SIZES, app_icon, write_app_ico

    path = write_app_ico(str(tmp_path / "ctgedit.ico"))
    head = open(path, "rb").read(6)
    assert head[:4] == b"\x00\x00\x01\x00" and head[4] == len(APP_ICON_SIZES)
    assert not QtGui.QImage(path).isNull()
    assert not app_icon().isNull()


# --------------------------------------------------------------- command line
def test_command_line():
    from ctgedit.app import parse_args

    o = parse_args(["a.ctg", "--new-instance", r"C:\x\b.ctg"])
    assert o.files == [os.path.abspath("a.ctg"), r"C:\x\b.ctg"] and o.new_instance and not o.error
    # Qt's own options (and their values) are not pages
    assert parse_args(["-style", "fusion", "-platform", "offscreen", "p.ctg"]).files == [
        os.path.abspath("p.ctg")
    ]
    o = parse_args(["--smoke-test", "p.ctg", "--report", "r.json"])
    assert o.smoke and o.report == os.path.abspath("r.json")
    assert "needs at least one page" in parse_args(["--smoke-test"]).error
    assert "--report needs a file name" in parse_args(["p.ctg", "--report"]).error
    assert "Unknown option --frobnicate" in parse_args(["--frobnicate"]).error
    assert parse_args(["--version"]).version


def test_version_and_bad_options(capsys):
    from ctgedit import __version__
    from ctgedit.app import main

    assert main(["ctgedit", "--version"]) == 0
    assert capsys.readouterr().out.startswith("ctgedit %s (" % __version__)
    assert main(["ctgedit", "--frobnicate"]) == 2
    out = capsys.readouterr().out
    assert "Unknown option --frobnicate" in out and "Usage: ctgedit" in out


# --------------------------------------------------------------- single instance
def test_second_launch_hands_its_pages_to_the_running_editor(tmp_path):
    """The whole hand-over: `ctgedit a.ctg b.ctg` while an editor listens
    sends both pages to it and exits at once (no second window)."""
    _app()
    from ctgedit.ui import single_instance

    got = []
    srv = single_instance.InstanceServer()  # the name follows CTG_SETTINGS_INI
    assert srv.listen()
    srv.filesRequested.connect(got.append)
    a, b = str(tmp_path / "a.ctg"), str(tmp_path / "with space" / "b.ctg")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ctgedit.app", a, b],
        cwd=str(tmp_path),
        env=_child_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _pump(lambda: proc.poll() is not None, timeout=90), (
            "the second launch did not hand over and exit"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
        out, err = proc.communicate()
        srv.close()
    assert proc.returncode == 0, err.decode(errors="replace")
    assert got == [[a, b]]


def test_a_launch_that_hands_over_leaves_crash_reports_to_the_editor(tmp_path):
    """Only the process that becomes the editor collects an earlier crash:
    a launch that hands its page over and exits must not take the report
    (nobody would ever show it)."""
    _app()
    from ctgedit.ui import single_instance

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    logs = tmp_path / "logs"
    logs.mkdir()
    left = logs / ("crash-20260101-000000-%d.log" % dead.pid)
    left.write_text("ctgedit 0.0.1 -- started ...\nWindows fatal exception: access violation\n")
    got = []
    srv = single_instance.InstanceServer()
    assert srv.listen()
    srv.filesRequested.connect(got.append)
    page = str(tmp_path / "a.ctg")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ctgedit.app", page],
        cwd=str(tmp_path),
        env=_child_env(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _pump(lambda: proc.poll() is not None, timeout=90)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()
        srv.close()
    assert proc.returncode == 0 and got == [[page]]
    assert left.exists() and not glob.glob(str(logs / "crash-*.txt"))


def test_server_name_is_per_user_session_and_settings_store(monkeypatch):
    from ctgedit.ui import single_instance

    assert isinstance(single_instance._session_id(), int)
    monkeypatch.setattr(single_instance, "_session_id", lambda: 1)
    first = single_instance.server_name()
    assert first == single_instance.server_name()  # stable
    monkeypatch.setattr(single_instance, "_session_id", lambda: 2)
    second = single_instance.server_name()
    assert second != first  # another logon session (RDP)
    monkeypatch.setenv("CTG_SETTINGS_INI", os.path.join(os.getcwd(), "other.ini"))
    assert single_instance.server_name() not in (first, second)


def test_a_waiting_hand_over_shows_the_dialog_once(monkeypatch):
    """While a modal dialog is open a handed-over page waits; the dialog is
    raised when the page arrives, not again on every 300 ms retry (that would
    keep taking the keyboard focus from the user)."""
    _app()
    from ctgedit import app as appmod

    class Modal:
        raised = 0

        def raise_(self):
            Modal.raised += 1

        def activateWindow(self):
            pass

    monkeypatch.setattr(appmod, "target_window", lambda: None)
    monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: Modal()))
    f = appmod.Forwarder()
    try:
        f.handle(["x.ctg"])
        assert Modal.raised == 1 and f._timer.isActive()
        f._retry()
        f._retry()
        assert Modal.raised == 1 and f._pending == [["x.ctg"]]
        f.handle(["y.ctg"])  # a new hand-over shows it again
        assert Modal.raised == 2
    finally:
        f._timer.stop()
        f._pending = []


def test_no_running_editor_means_no_hand_over():
    _app()
    from ctgedit.ui.single_instance import forward

    assert forward(["x.ctg"], name="ctgedit-test-%s" % uuid.uuid4().hex, timeout_ms=300) is False


def test_instance_server_rejects_garbage_and_keeps_an_early_leaver():
    _app()
    from ctgedit.ui.single_instance import InstanceServer

    srv = InstanceServer(name="ctgedit-test-%s" % uuid.uuid4().hex)
    assert srv.listen()
    got = []
    srv.filesRequested.connect(got.append)
    sock, late = QtNetwork.QLocalSocket(), QtNetwork.QLocalSocket()
    try:
        sock.connectToServer(srv.name)
        assert _pump(lambda: sock.state() == QtNetwork.QLocalSocket.ConnectedState)
        sock.write(b"this is not a request\n")
        reply = []
        assert _pump(lambda: reply.append(bytes(sock.readAll())) or b"\n" in b"".join(reply))
        assert b"".join(reply) == b"error\n" and got == []
        sock.disconnectFromServer()
        # a sender that leaves without waiting for the reply is still served
        late.connectToServer(srv.name)
        assert _pump(lambda: late.state() == QtNetwork.QLocalSocket.ConnectedState)
        late.write(json.dumps({"files": [r"C:\p\x.ctg"]}).encode() + b"\n")
        late.disconnectFromServer()
        assert _pump(lambda: got == [[r"C:\p\x.ctg"]])
        assert _pump(lambda: not srv._buffers)  # both connections cleaned up
    finally:
        sock.abort()
        late.abort()
        srv.close()
        _pump(lambda: False, timeout=0.2)  # let the deferred deletes run


def test_handed_over_pages_open_as_tabs_in_the_running_window(tmp_path, monkeypatch, fx_page):
    app = _app()
    from ctgedit import app as appmod
    from ctgedit.ui.main_window import MainWindow

    page = fx_page(PAGE)
    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warned.append(a) or QtWidgets.QMessageBox.Ok),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard)
    )
    w = MainWindow()
    w.show()
    app.processEvents()
    monkeypatch.setattr(appmod, "target_window", lambda: w)
    try:
        # during startup the pages wait for the first window
        f = appmod.Forwarder(ready=False)
        f.handle([page])
        assert w.doc is None
        f.start()
        assert w.doc is not None and os.path.samefile(w.doc.path, page)
        # the same page again: its tab is shown, not a second copy
        tabs = len(w._docs)
        f.handle([page])
        assert len(w._docs) == tabs
        # a page that is gone: one plain message, nothing opened
        f.handle([str(tmp_path / "gone.ctg")])
        assert len(warned) == 1 and "does not exist" in warned[0][2]
        assert len(w._docs) == tabs
        # while a modal dialog is open the page waits, then opens
        w.close_current_document()
        assert w.doc is None
        monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: w))
        f.handle([page])
        assert w.doc is None and f._timer.isActive()
        assert "Close the open dialog" in w.statusBar().currentMessage()
        monkeypatch.setattr(QtWidgets.QApplication, "activeModalWidget", staticmethod(lambda: None))
        f.flush()
        assert w.doc is not None and os.path.samefile(w.doc.path, page)
    finally:
        w.close()
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


def test_target_window_is_a_visible_editor(monkeypatch):
    _app()
    from ctgedit.app import target_window
    from ctgedit.ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "instances", [])
    assert target_window() is None
    w = MainWindow()
    try:
        assert target_window() is None  # not shown yet
        w.show()
        assert target_window() is w
    finally:
        w.hide()


def test_open_files_explains_what_it_cannot_open(tmp_path, monkeypatch):
    _app()
    from ctgedit.ui.main_window import MainWindow

    warned = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warned.append(a) or QtWidgets.QMessageBox.Ok),
    )
    lib = tmp_path / "pumps.ctm"
    lib.write_bytes(b"")
    w = MainWindow()
    try:
        failed = w.open_files([str(tmp_path / "gone.ctg"), str(lib)])
        assert [os.path.basename(p) for p, _ in failed] == ["gone.ctg", "pumps.ctm"]
        assert len(warned) == 1 and warned[0][1] == "Could Not Open Pages"
        assert "does not exist" in warned[0][2] and "File > Open" in warned[0][2]
    finally:
        if w in MainWindow.instances:
            MainWindow.instances.remove(w)


# --------------------------------------------------------------- crash / error logs
def test_a_crash_leaves_a_report_the_next_start_finds(tmp_path, monkeypatch):
    code = "from ctgedit import crashlog; crashlog.install(); import faulthandler; faulthandler._sigsegv()"
    r = subprocess.run(
        [sys.executable, "-c", code], env=_child_env(tmp_path), capture_output=True, timeout=120
    )
    assert r.returncode != 0
    monkeypatch.setenv("CTG_LOG_DIR", str(tmp_path / "logs"))
    from ctgedit import __version__, crashlog

    reports = crashlog.collect_previous_crashes()
    assert len(reports) == 1 and reports[0].endswith(".txt")
    with open(reports[0], encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    assert text.startswith("ctgedit %s" % __version__)
    assert "fatal" in text.lower() and "<string>" in text  # the crashing stack
    assert not glob.glob(str(tmp_path / "logs" / "crash-*.log"))
    assert crashlog.collect_previous_crashes() == []  # reported once


def test_a_clean_exit_leaves_no_report_and_output_is_kept(tmp_path):
    code = (
        "import sys; sys.stdout = sys.stderr = None; "  # a windowed (no console) build
        "from ctgedit import crashlog; p = crashlog.install(); "
        "print('printed by a windowed build'); crashlog.uninstall()"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], env=_child_env(tmp_path), capture_output=True, timeout=120
    )
    assert r.returncode == 0, r.stderr
    logs = tmp_path / "logs"
    assert not glob.glob(str(logs / "crash-*"))
    assert "printed by a windowed build" in (logs / "output.log").read_text(encoding="utf-8")


def test_left_over_files_of_killed_and_live_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("CTG_LOG_DIR", str(tmp_path))
    from ctgedit import crashlog

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        killed = tmp_path / ("crash-20260101-000000-%d.log" % dead.pid)
        killed.write_text("ctgedit 0.0.1 -- started ...\n")  # header only
        running = tmp_path / ("crash-20260101-000001-%d.log" % live.pid)
        running.write_text("ctgedit 0.0.1 -- started ...\nThread 0x1:\n  File x\n")
        assert crashlog.collect_previous_crashes() == []
        assert not killed.exists()  # killed, not crashed: nothing to report
        assert running.exists()  # still running: not a crash
    finally:
        live.kill()
        live.wait()


def test_a_failed_command_is_logged_and_shown(tmp_path, monkeypatch):
    _app()
    monkeypatch.setenv("CTG_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored afterwards
    from ctgedit import app as appmod

    shown = []

    def fake_exec(box):
        shown.append((box.windowTitle(), box.text(), box.informativeText()))
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    appmod._install_error_hook()
    try:
        raise ValueError("the grid cannot be 0 pixels")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "ValueError: the grid cannot be 0 pixels" in log and "ctgedit " in log
    assert shown and shown[0][0] == "Command Failed"
    assert "the grid cannot be 0 pixels" in shown[0][1]
    assert str(tmp_path / "errors.log") in shown[0][2]


# --------------------------------------------------------------- release gate
def test_release_gate_fails_on_any_test_failure(tmp_path, capsys):
    rc = _release_check()
    good = tmp_path / "test_good.py"
    good.write_text("def test_ok():\n    assert True\n")
    bad = tmp_path / "test_bad.py"
    bad.write_text("def test_ok():\n    assert True\n\n\ndef test_broken():\n    assert 1 == 2\n")
    broken = tmp_path / "test_broken_import.py"
    broken.write_text("import no_such_module_for_the_gate\n")
    assert rc.main(["--only", "tests", "--tests", str(good)]) == 0
    assert "PASS  tests" in capsys.readouterr().out
    assert rc.main(["--only", "tests", "--tests", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  tests" in out and "test_broken" in out and "Release check FAILED: tests" in out
    assert rc.main(["--only", "tests", "--tests", str(broken)]) == 1  # a collection error too


def test_release_gate_version_check(tmp_path, monkeypatch):
    rc = _release_check()
    real = rc.check_version()
    assert real.ok, real.detail  # the pins are this environment's
    # a pin that does not match the environment fails the gate
    import shutil

    shutil.copy2(os.path.join(REPO, "pyproject.toml"), tmp_path / "pyproject.toml")
    (tmp_path / "requirements.txt").write_text("numpy==0.0.1\n")
    (tmp_path / "requirements.lock").write_text("numpy==0.0.1 \\\n    --hash=sha256:00\n")
    monkeypatch.setattr(rc, "REPO", str(tmp_path))
    r = rc.check_version()
    assert not r.ok and "numpy is pinned to 0.0.1" in r.detail
    # ... and so does a lock file that was not regenerated
    (tmp_path / "requirements.lock").write_text("")
    assert "requirements.lock has numpy==(none)" in rc.check_version().detail


def test_release_gate_skips_and_frozen_errors(tmp_path):
    rc = _release_check()
    assert rc.check_sources(allow_dirty=True).skipped
    r = rc.check_frozen(str(tmp_path / "ctgedit.exe"), [str(tmp_path / "FxShapes.ctg")], 10)
    assert not r.ok and "build it with packaging\\build.ps1" in r.detail
    assert rc.check_compile().ok


def test_release_gate_frozen_check_needs_the_built_program(tmp_path, monkeypatch):
    """--frozen runs the exe without this source tree on PYTHONPATH and fails
    when the report says it did not run as a frozen build."""
    rc = _release_check()
    exe = tmp_path / "ctgedit.exe"
    exe.write_bytes(b"")
    page = str(tmp_path / "FxShapes.ctg")  # (only a path in the report)
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        report = cmd[cmd.index("--report") + 1]
        with open(report, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": "1.0.0 (x)",
                    "ok": True,
                    "frozen": seen["frozen"],
                    "executable": r"C:\Python\python.exe",
                    "pages": [{"page": page, "ok": True, "error": ""}],
                },
                fh,
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    seen["frozen"] = False
    r = rc.check_frozen(str(exe), [page], 10)
    assert "PYTHONPATH" not in seen["env"]
    assert not r.ok and "ran as a source run" in r.detail
    seen["frozen"] = True
    assert rc.check_frozen(str(exe), [page], 10).ok


def test_smoke_test_edits_copies_and_leaves_the_project_alone(tmp_path, fx_install):
    from ctgedit import __version__

    proj = fx_install.project("FxMain")
    page = fx_install.page(PAGE)

    # the page and every project file a save could write: its .ctF / .rdb,
    # the project database (pages / pg* / changes .DBF + .NDX) and PROJECT.CIT
    watched = sorted(
        {
            p
            for pat in (PAGE + ".*", "*.DBF", "*.dbf", "*.NDX", "*.ndx", "PROJECT.CIT", "project.cit")
            for p in glob.glob(os.path.join(proj, pat))
        }
    )
    assert any(p.lower().endswith("pages.dbf") for p in watched)

    def state():
        with open(page, "rb") as fh:
            digest = hashlib.sha1(fh.read()).hexdigest()
        return digest, [(p, os.path.getmtime(p), os.path.getsize(p)) for p in watched]

    before = state()
    report = tmp_path / "smoke.json"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "ctgedit.app",
            "--smoke-test",
            page,
            str(tmp_path / "missing.ctg"),
            "--report",
            str(report),
        ],
        cwd=REPO,
        env=_child_env(tmp_path),
        capture_output=True,
        timeout=600,
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert r.returncode == 1  # one page failed -> the run fails
    assert data["version"].startswith(__version__) and not data["ok"]
    assert data["frozen"] is False  # a source run says so
    first, missing = data["pages"]
    assert first["ok"], first
    assert first["objects"] > 0 and first["drawn_pixels"] > 20 and not first["dialogs"]
    assert set(first["ms"]) == {"open", "render", "edit", "save", "close", "reopen"}
    assert not missing["ok"] and "does not exist" in missing["error"]
    assert state() == before  # the project is never written


# --------------------------------------------------------------- packaging files
def test_packaging_files_agree_with_the_code():
    from fnmatch import fnmatch
    from ctgedit.app import APP_MUTEX, APP_USER_MODEL_ID

    def read(*p):
        with open(os.path.join(REPO, *p), encoding="utf-8") as fh:
            return fh.read()

    spec = read("packaging", "ctgedit.spec")
    assert 'collect_data_files("ctgedit")' in spec and "launcher.py" in spec
    assert "console=False" in spec and "write_build_info" in spec
    iss = read("packaging", "installer.iss")
    assert "AppMutex=%s" % APP_MUTEX in iss and APP_USER_MODEL_ID in iss
    assert r"Software\Classes\.ctg\OpenWithProgids" in iss
    assert '"""{app}\\{#AppExe}"" ""%1"""' in iss  # the page path reaches ctgedit
    build = read("packaging", "build.ps1")
    assert "ctgedit.spec" in build and "installer.iss" in build and "--frozen" in build
    launcher = read("packaging", "launcher.py")
    assert "from ctgedit.app import main" in launcher
    # a pip install ships every data file of the package
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as fh:
        pkg_data = tomllib.load(fh)["tool"]["setuptools"]["package-data"]
    pkg_root = os.path.join(REPO, "ctgedit")
    for root, dirs, files in os.walk(pkg_root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".py", ".pyc")):
                continue
            rel = os.path.relpath(os.path.join(root, name), REPO).replace(os.sep, "/")
            owner = next(
                (
                    pkg
                    for pkg in sorted(pkg_data, key=len, reverse=True)
                    if rel.startswith(pkg.replace(".", "/") + "/")
                ),
                None,
            )
            assert owner, "%s is in no package-data package" % rel
            inner = rel[len(owner.replace(".", "/")) + 1 :]
            assert any(fnmatch(inner, pat) for pat in pkg_data[owner]), (
                "%s is not matched by package-data %s" % (rel, owner)
            )
