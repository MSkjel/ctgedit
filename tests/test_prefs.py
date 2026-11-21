"""Preferences persist."""

from ctgedit.ui.prefs.settings import app_settings
import os


def _win():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    return MainWindow()


def _drop(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def test_prefs_round_trip(fx_page):
    PAGE = fx_page("FxShapes")

    st = app_settings()
    # snapshot keys we touch so we don't pollute the shared store
    keys = [
        "view/grid_size",
        "view/snap",
        "view/thumb",
        "draw/fill_colour",
        "draw/colour_mode",
        "sel/filter",
        "sel/marquee",
        "session/last_file",
        "win/geometry",
        "win/state",
        "dir/open",
    ]
    saved = {k: st.value(k) for k in keys}
    try:
        w = _win()
        try:
            w.open_document(PAGE)
            w._grid_size = 20
            w.act_snap.setChecked(False)
            w._show_thumb = True
            w.act_thumb.setChecked(True)
            from ctgedit.ui.editor import draw_styles as DS

            DS.set_colour("fill", 0x00ABCDEF)  # (the ribbon's fill swatch)
            w.sel_filter.setCurrentIndex(2)
            w.sel_marquee.setCurrentIndex(2)
            before_dir = st.value("dir/open")
            w.save_prefs()
            assert st.value("session/last_file", "", type=str) == os.path.abspath(PAGE)
            # save_prefs must not touch the dialog dirs (only the dialog does)
            assert st.value("dir/open") == before_dir
        finally:
            _drop(w)
        # a fresh window restores everything (without reopening for the test)
        w2 = _win()
        try:
            reopened = w2.restore_session(reopen=False)
            assert reopened is False
            assert w2._grid_size == 20
            assert not w2.act_snap.isChecked()
            assert w2._show_thumb is True
            assert DS.colours()["fill"] == 0x00ABCDEF and DS.colour_mode() == "fixed"
            assert w2.sel_filter.currentIndex() == 2
            assert w2.sel_marquee.currentIndex() == 2
        finally:
            _drop(w2)
        # reopen=True path actually loads the saved file
        w3 = _win()
        try:
            assert w3.restore_session(reopen=True) is True
            assert w3.doc is not None
            assert os.path.abspath(w3.doc.path) == os.path.abspath(PAGE)
        finally:
            _drop(w3)
    finally:
        for k, v in saved.items():
            if v is None:
                st.remove(k)
            else:
                st.setValue(k, v)


def test_dialog_dir_remembered(fx_page):
    PAGE = fx_page("FxShapes")

    st = app_settings()
    prev = st.value("dir/open")
    try:
        w = _win()
        try:
            w._remember_dir("open", PAGE)
            assert os.path.normcase(w._dialog_dir("open")) == os.path.normcase(os.path.dirname(PAGE))
        finally:
            _drop(w)
    finally:
        if prev is None:
            st.remove("dir/open")
        else:
            st.setValue("dir/open", prev)
