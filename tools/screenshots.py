"""Take the README's screenshots from the fake install's FxDemo project.

python tools/screenshots.py [--out DIR] [--native] [--only NAME ...]

It works on a copy of tests/fixtures/FxInstall with settings of its own, so nothing in the
repository or the user's settings changes. The Simulate shot needs the engine built
(external/cicode-sim, net48). Offscreen by default, so CI and a desktop give the same
pictures; --native draws with the Windows platform instead.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: name -> (theme, page, the object to select, simulate)
SHOTS = {
    "editor-dark": ("dark", "FxDemo/Overview.ctg", "P101", False),
    "editor-light": ("light", "FxDemo/Overview.ctg", "T101", False),
    "simulate": ("dark", "FxDemo/Overview.ctg", None, True),
}
WINDOW = (1600, 960)
#: how long the simulated plant runs before the Simulate shot (real seconds)
SIMULATE_S = 20.0


def _pump(app, seconds: float) -> None:
    from PySide6 import QtCore

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)


def _index_named(doc, name: str) -> int:
    for i in range(len(doc.parsed.page.objects)):
        if doc.object_name(i) == name:
            return i
    raise LookupError("no object named %s on %s" % (name, doc.path))


def take(name: str, root: str, work: str, out: str) -> str:
    """One screenshot, in this process (a QApplication per process: one shot each)."""
    theme, page, select, simulate = SHOTS[name]
    os.environ["CTG_SCADA_ROOT"] = root
    os.environ["CTG_LIB_SIBLINGS"] = "0"
    os.environ["CTG_SETTINGS_INI"] = os.path.join(work, "settings-%s.ini" % name)
    os.environ["CTG_DATA_DIR"] = os.path.join(work, "data-%s" % name)
    os.environ["CTG_NO_DISPLAY_CHECK"] = "1"
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication(["ctgedit"])
    from ctgedit.ui.prefs.settings import app_settings

    s = app_settings()
    s.setValue("ui/style/theme", theme)
    s.setValue("ui/keys_hint_shown", True)
    s.setValue("opt/check_updates", False)
    s.sync()
    from ctgedit.ui.style.theme import app_icon, apply_theme

    apply_theme(app)
    app.setWindowIcon(app_icon())
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(*WINDOW)
    w.show()
    _pump(app, 1.0)
    w.open_document(os.path.join(root, "User", *page.split("/")))
    _pump(app, 2.0)
    if select:
        w._set_selection([_index_named(w.doc, select)])
        _pump(app, 1.0)
    target = w
    if simulate:
        from ctgedit.ui.simulate.window import simulate_window

        w.act_simulate.trigger()
        _pump(app, SIMULATE_S)
        target = simulate_window(create=False)
        if target is None:
            raise RuntimeError("the Simulate window did not open (is the engine built?)")
        top = target.window()
        top.showNormal()
        top.resize(1500, 900)
        _pump(app, 1.5)
        target.clock.act_zoom_fit.trigger()
        _pump(app, 2.0)
    path = os.path.join(out, name + ".png")
    if not target.grab().save(path):
        raise RuntimeError("could not save " + path)
    w.close()
    _pump(app, 0.5)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO, "build", "screens"), help="the folder to write")
    ap.add_argument("--native", action="store_true", help="draw with the Windows platform, not offscreen")
    ap.add_argument("--only", nargs="*", choices=sorted(SHOTS), help="only these shots")
    ap.add_argument("--one", help=argparse.SUPPRESS)  # (a child process: one shot)
    ap.add_argument("--root", help=argparse.SUPPRESS)
    ap.add_argument("--work", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not a.native:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ.setdefault(
            "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        )
    sys.path[:0] = [REPO, os.path.join(REPO, "tests")]
    if a.one:
        print(take(a.one, a.root, a.work, a.out))
        return 0

    import subprocess

    import fxinstall

    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="ctg_screens_")
    try:
        install = os.path.join(work, "install")
        os.makedirs(install)
        root = fxinstall.install_copy(install).root
        failed = []
        for name in a.only or sorted(SHOTS):
            cmd = [sys.executable, os.path.abspath(__file__), "--one", name, "--root", root, "--work", work]
            cmd += ["--out", a.out] + (["--native"] if a.native else [])
            r = subprocess.run(cmd, cwd=REPO)
            if r.returncode:
                failed.append(name)
            else:
                print("wrote", os.path.join(a.out, name + ".png"))
        if failed:
            print("failed:", ", ".join(failed))
            return 1
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
