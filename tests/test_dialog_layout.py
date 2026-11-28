"""Every dialog lays out for the window's text size: no text cut off, the window no smaller than
its layout, no control outside it or over another (tests/dialog_gallery.py)."""

import os

import pytest

from qtkit import app as _app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dialog_gallery as G  # noqa: E402


@pytest.fixture
def ctx(fx_install):
    _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.open_document(fx_install.page("FxShapes"))
    w.resize(1280, 860)
    yield G.Context(w, fx_install.root)
    for e in list(w._docs):
        e["doc"].dirty = False
    if w in MainWindow.instances:
        MainWindow.instances.remove(w)
    w.close()


@pytest.mark.parametrize("scale", [1.0, 1.5])
@pytest.mark.parametrize("name", sorted(G.BUILDERS))
def test_the_dialog_fits_its_text(ctx, name, scale):
    from PySide6 import QtWidgets

    from ctgedit.ui.style import theme

    theme.apply_theme(QtWidgets.QApplication.instance(), "dark", scale)
    dlg = G.BUILDERS[name](ctx)
    try:
        G.show(dlg)
        problems = G.layout_problems(dlg)
        assert not problems, "%s at %d%%: %s" % (name, scale * 100, "; ".join(problems))
    finally:
        G.close(dlg)


@pytest.mark.parametrize("name", sorted(G.BUILDERS))
def test_the_dialog_keys(ctx, name):
    """Enter, the first focus and the shortcut letters (G.keyboard_problems)."""
    dlg = G.BUILDERS[name](ctx)
    try:
        G.show(dlg)
        dlg.activateWindow()
        problems = G.keyboard_problems(dlg)
        assert not problems, "%s: %s" % (name, "; ".join(problems))
    finally:
        G.close(dlg)
