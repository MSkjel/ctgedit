"""Screenshots of every dialog (tests/dialog_gallery.py) at 100 % and 150 % text, with what the
layout check finds in each, to look at them side by side.

python tools/dialog_gallery.py [--out DIR] [--only NAME ...] [--theme dark|light]

It works on a copy of tests/fixtures/FxInstall with settings of its own; nothing in the
repository or the user's settings changes. Offscreen, as the tests draw.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _drop(w) -> None:
    """Close and delete window ``w`` and what it left open: no window piles up (each would be
    restyled and kept alive by the next ones)."""
    import gc

    from PySide6 import QtCore, QtWidgets

    from ctgedit.ui.main_window import MainWindow

    for e in list(w._docs):
        e["doc"].dirty = False
    for t in QtWidgets.QApplication.topLevelWidgets():
        if t is not w and t.isVisible():
            t.close()
    w.close()
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    gc.collect()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO, "build", "dialogs"), help="the folder to write")
    ap.add_argument("--only", nargs="*", help="only these dialogs (their gallery names)")
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    a = ap.parse_args(argv)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    sys.path[:0] = [REPO, os.path.join(REPO, "tests")]
    import fxinstall

    work = tempfile.mkdtemp(prefix="ctg_dialogs_")
    try:
        install = os.path.join(work, "install")
        os.makedirs(install)
        root = fxinstall.install_copy(install).root
        import hermetic

        # (the tests' environment: the fake install only, no AVEVA program, no question waits)
        for k, v in hermetic.FORCED_ENV.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ["CTG_NO_MODAL_QUESTIONS"] = "1"
        os.environ["CTG_SCADA_ROOT"] = root
        os.environ["CTG_LIB_SIBLINGS"] = "0"
        os.environ["CTG_SETTINGS_INI"] = os.path.join(work, "settings.ini")
        os.environ["CTG_DATA_DIR"] = os.path.join(work, "data")
        os.environ["CTG_NO_DISPLAY_CHECK"] = "1"
        from PySide6 import QtWidgets

        import dialog_gallery as G
        from ctgedit.ui.main_window import MainWindow
        from ctgedit.ui.style import theme

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        os.makedirs(a.out, exist_ok=True)
        report = []
        for scale in (1.0, 1.5):
            theme.apply_theme(app, a.theme, scale)
            for name in a.only or sorted(G.BUILDERS):
                w = MainWindow()
                w.open_document(os.path.join(root, "User", "FxMain", "FxShapes.ctg"))
                try:
                    dlg = G.BUILDERS[name](G.Context(w, root))
                    G.show(dlg)
                    problems = G.layout_problems(dlg)
                    path = os.path.join(a.out, "%s-%d.png" % (name.replace(" ", "_"), scale * 100))
                    dlg.grab().save(path)
                    report.append("%-28s %3d%%  %s" % (name, scale * 100, "; ".join(problems) or "ok"))
                    G.close(dlg)
                except Exception as e:  # noqa: BLE001 -- one dialog never stops the others
                    report.append("%-28s %3d%%  could not be built: %s" % (name, scale * 100, e))
                finally:
                    _drop(w)
        print("\n".join(report))
        print("wrote", a.out)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
