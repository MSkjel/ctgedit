"""Garbage is collected on the GUI thread only."""

import gc
import threading

import pytest

from ctgedit.infra import gcguard


class _Cyclic:
    """A cycle whose finaliser records the thread that freed it."""

    freed_on: list = []

    def __init__(self):
        self.me = self

    def __del__(self):
        _Cyclic.freed_on.append(threading.get_ident())


@pytest.fixture
def guard():
    was = gcguard.installed()
    gcguard.install()
    try:
        yield
    finally:
        if not was:
            gcguard.uninstall()


def test_a_worker_thread_never_collects(guard):
    main = threading.get_ident()
    _Cyclic.freed_on.clear()
    for _ in range(50):
        _Cyclic()  # garbage left for the collector
    assert not gc.isenabled()

    def work():
        # far past threshold0: the automatic collector would run HERE
        junk = [[] for _ in range(200_000)]
        for j in junk:
            j.append(j)
        del junk

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert not _Cyclic.freed_on, "a worker thread collected garbage"
    # the worker's own call is refused too
    out = []
    t = threading.Thread(target=lambda: out.append(gcguard.collect_due()))
    t.start()
    t.join()
    assert out == [0] and not _Cyclic.freed_on
    # the GUI (owner) thread collects it
    freed = gcguard.collect_due()
    assert freed > 0
    assert _Cyclic.freed_on and set(_Cyclic.freed_on) == {main}


def test_full_collection_after_enough_survivors(guard):
    """Old-generation garbage (a cycle that became garbage after surviving a young collection:
    a closed tab's scene) is freed by the full collection the survivor count triggers."""
    _Cyclic.freed_on.clear()
    old = [_Cyclic() for _ in range(20)]
    gcguard.collect_due()  # (maybe nothing due yet)
    gc.collect(0)  # they survive a young collection: old
    del old
    before = gcguard.stats()["full"]
    keep = []
    for _ in range(12):  # many live objects: survivors pile up
        keep.append(
            [object() for _ in range(max(gcguard.FULL_MIN // 8, 2000))]
            + [[] for _ in range(max(gcguard.FULL_MIN // 8, 2000))]
        )
        gcguard.collect_due()
    assert gcguard.stats()["full"] > before
    assert len(_Cyclic.freed_on) >= 20
    del keep


def test_the_timer_runs_full_collections_when_quiet(guard, monkeypatch):
    """A full collection (~0.3 s at 1 M objects) waits for a quiet moment: while every tick
    has young garbage to collect it does not run (unless memory needs it); then it does. The
    derive checkpoint never runs a regular one."""
    ll = gcguard._state["long_lived"]
    due = max(gcguard.FULL_MIN, ll // 4) + 1  # due, not urgent
    monkeypatch.setitem(gcguard._state, "pending", due)
    full0 = gcguard.stats()["full"]
    gcguard.checkpoint()
    assert gcguard.stats()["full"] == full0  # the derive: young only
    for _ in range(gcguard.QUIET_TICKS + 2):  # busy: young garbage every tick
        junk = [[] for _ in range(gc.get_threshold()[0] + 10)]
        for j in junk:
            j.append(j)
        del junk
        gcguard.tick()
        gcguard._state["pending"] = max(gcguard._state["pending"], due)
    assert gcguard.stats()["full"] == full0
    for _ in range(gcguard.QUIET_TICKS + 1):  # quiet
        gcguard.tick()
    assert gcguard.stats()["full"] == full0 + 1


def test_uninstall_gives_the_automatic_collector_back():
    was = gcguard.installed()
    gcguard.install()
    gcguard.uninstall()
    assert not gcguard.installed() and gc.isenabled()
    if was:
        gcguard.install()


def test_the_editor_window_installs_it():
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from ctgedit.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert gcguard.installed() and not gc.isenabled()
        t = gcguard._state["timer"]
        assert t is not None and t.isActive()
    finally:
        MainWindow.instances.remove(w)
        w.deleteLater()
        app.processEvents()
        gcguard.uninstall()


def test_ui_worker_threads_install_it_first(monkeypatch, tmp_path):
    """A property sheet or the Tags panel starts a tag-index worker without an editor window
    (the tests): the worker allocates millions of objects, so the guard goes on first."""
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    was = gcguard.installed()
    gcguard.uninstall()
    started = []
    real_start = threading.Thread.start
    try:
        monkeypatch.setattr(
            threading.Thread, "start", lambda self: started.append((self.name, gcguard.installed()))
        )
        from ctgedit.ui.objprops import expression

        monkeypatch.setattr(expression, "_WARMED", set())
        nowhere = str(tmp_path / "no_project")
        expression.warm_tags(nowhere)
        from ctgedit.io import cicode_catalog

        monkeypatch.setattr(cicode_catalog, "_PRELOADED", set())
        cicode_catalog.preload(nowhere)
        assert started == [("tag-index", True), ("cicode-catalog", True)]
        assert gcguard._state["timer"] is not None
        # off the GUI thread it does nothing
        gcguard.uninstall()
        monkeypatch.setattr(threading.Thread, "start", real_start)
        out = []
        t = threading.Thread(target=lambda: out.append(gcguard.before_worker()))
        t.start()
        t.join()
        assert out == [False] and not gcguard.installed()
    finally:
        monkeypatch.setattr(threading.Thread, "start", real_start)
        gcguard.uninstall()
        app.processEvents()
        if was:
            gcguard.install()
