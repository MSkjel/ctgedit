"""A page opened from the command line fits the final window."""

from qtkit import app as _app


def _drop(w):
    from ctgedit.ui.main_window import MainWindow

    if w in MainWindow._instances:
        MainWindow._instances.remove(w)


def test_fit_deferred_until_shown(fx_page):
    page = fx_page("FxWork")
    app = _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1400, 900)
    try:
        # open BEFORE show() -- the command-line / restore-session startup path
        w.open_document(page)
        # auto-fit is armed, but the fit is deferred (no bogus tiny zoom yet)
        assert w.view._auto_fit is True
        # now show + let the layout settle: the deferred fit runs
        w.show()
        app.processEvents()
        app.processEvents()
        z = w.view.zoom()
        # a ~1366x746 page in a ~1400px viewport fits near ~0.9
        assert z > 0.3, "page did not fit on launch (zoom=%.3f)" % z
        assert z < 3.0
    finally:
        _drop(w)


def test_fit_tracks_final_size_then_stops_on_manual_zoom(fx_page):
    page = fx_page("FxWork")
    app = _app()
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(900, 700)
    w.show()
    app.processEvents()
    try:
        w.open_document(page)
        app.processEvents()
        z_small = w.view.zoom()
        assert z_small > 0.1
        # window grows (dock settle / maximize): auto-fit must TRACK it, so the
        # fit matches the final size -- not the intermediate one
        w.resize(1700, 1000)
        app.processEvents()
        app.processEvents()
        z_big = w.view.zoom()
        assert z_big > z_small * 1.2, "auto-fit did not track the larger window (%.3f -> %.3f)" % (
            z_small,
            z_big,
        )
        # user manually zooms -> auto-fit OFF; later resizes must NOT re-fit
        w.view.zoom_by(1.0)  # a manual zoom op (factor 1 = no change)
        assert w.view._auto_fit is False
        z_locked = w.view.zoom()
        w.resize(1100, 800)
        app.processEvents()
        app.processEvents()
        assert abs(w.view.zoom() - z_locked) < 1e-6, (
            "resize re-fitted after the user took manual zoom control"
        )
    finally:
        _drop(w)
