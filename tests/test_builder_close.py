"""Tools > Close Graphics Builder and its setting (ui/builder_close.py,
backend/graphics_builder.py): a builder with nothing open is ended at once; one with a page
open is closed only after a warning, as its Close button closes it, and ended on request
when it stays open. No real process is touched."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtkit import app as _app

from ctgedit.backend import graphics_builder as GB
from ctgedit.editing_base import CTDRAW_CACHE_WARNING
from ctgedit.ui import builder_close as BC


class Builders:
    """Fake Graphics Builder processes: pid -> its main windows; ``open`` = the titles of
    what is open in them."""

    def __init__(self, procs, opened=()):
        self.procs = dict(procs)
        self.open = list(opened)
        self.posted = []
        self.ended = []

    def install(self, monkeypatch):
        import win32gui

        monkeypatch.setattr(GB, "_ctdraw_pids", lambda: list(self.procs))
        monkeypatch.setattr(GB, "builder_windows", lambda pid: list(self.procs.get(pid, ())))
        monkeypatch.setattr(GB, "open_windows", lambda: list(self.open))
        monkeypatch.setattr(win32gui, "PostMessage", lambda h, msg, w, lp: self.posted.append(h))

        def end(pids):
            n = 0
            for p in list(pids):
                if self.procs.pop(p, None) is not None:
                    self.ended.append(p)
                    n += 1
            return n

        monkeypatch.setattr(GB, "end_builders", end)
        return self


def test_nothing_open_ends_it_and_an_open_page_asks_it_to_close(monkeypatch):
    b = Builders({10: [101], 20: [201]}).install(monkeypatch)
    assert GB.ask_builders_to_close() == []
    assert b.ended == [10, 20] and b.posted == []
    b = Builders({10: [], 20: [201]}, ["ExampleSA - Overview"]).install(monkeypatch)
    assert GB.ask_builders_to_close() == [20]
    assert b.ended == [10] and b.posted == [201]
    assert GB.builders_running() == [20] and GB.builders_running([10]) == []


def test_open_pages_come_from_the_open_windows(monkeypatch):
    monkeypatch.setattr(GB, "open_windows", lambda: ["ExampleSA - Overview", "include.genie_lib"])
    assert GB.open_pages() == [("ExampleSA", "Overview")]


def _run(closer, until=5.0):
    from PySide6 import QtWidgets

    got = []
    closer.finished.connect(got.append)
    closer.start()
    end = time.monotonic() + until
    while not got and time.monotonic() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    return got


@pytest.fixture
def parent():
    from PySide6 import QtWidgets

    _app()
    return QtWidgets.QWidget()


def test_nothing_to_close(monkeypatch, parent):
    Builders({}).install(monkeypatch)
    assert _run(BC.BuilderCloser(parent)) == [BC.NOT_RUNNING]


def test_a_builder_with_nothing_open_is_ended_without_a_question(monkeypatch, parent):
    b = Builders({20: [201]}).install(monkeypatch)
    monkeypatch.setattr(BC.BuilderCloser, "confirm", lambda self, opened: pytest.fail("asked"))
    assert _run(BC.BuilderCloser(parent)) == [BC.CLOSED]
    assert b.ended == [20]


def test_an_open_page_is_warned_about_first(monkeypatch, parent):
    b = Builders({20: [201]}, ["ExampleSA - Overview"]).install(monkeypatch)
    seen = []
    monkeypatch.setattr(BC.BuilderCloser, "confirm", lambda self, opened: seen.append(opened) or False)
    assert _run(BC.BuilderCloser(parent)) == [BC.LEFT_OPEN]
    assert seen == [["ExampleSA - Overview"]] and b.posted == [] and b.ended == []
    # Close It: asked to close as its Close button does, and it closes
    monkeypatch.setattr(BC.BuilderCloser, "confirm", lambda self, opened: True)
    real = GB.ask_builders_to_close
    monkeypatch.setattr(GB, "ask_builders_to_close", lambda: (real(), b.procs.clear())[0])
    assert _run(BC.BuilderCloser(parent)) == [BC.CLOSED]
    assert b.posted == [201] and b.ended == []


@pytest.mark.parametrize(
    "answers, said, ended",
    [
        (["end"], BC.CLOSED, [20]),
        (["cancel"], BC.LEFT_OPEN, []),
        (["wait", "end"], BC.CLOSED, [20]),
    ],
)
def test_a_builder_that_stays_open_is_ended_only_on_request(monkeypatch, parent, answers, said, ended):
    b = Builders({20: [201]}, ["ExampleSA - Overview"]).install(monkeypatch)
    monkeypatch.setattr(BC.BuilderCloser, "confirm", lambda self, opened: True)
    asked = []

    def question(self):
        asked.append(1)
        return answers[len(asked) - 1]

    monkeypatch.setattr(BC.BuilderCloser, "question", question)
    closer = BC.BuilderCloser(parent, wait_s=0.05)
    assert _run(closer) == [said]
    assert b.ended == ended and b.posted == [201] and len(asked) == len(answers)


def test_the_notice_offers_it(monkeypatch, parent):
    from PySide6 import QtWidgets

    box = QtWidgets.QMessageBox(parent)
    called = []
    monkeypatch.setattr(BC, "close_builder", lambda p, report=None: called.append(p))
    BC.add_close_button(box, parent).click()
    assert called == [parent]


def test_the_setting_closes_it_after_a_library_save(monkeypatch, parent):
    from ctgedit.ui.prefs import settings

    called = []
    monkeypatch.setattr(BC, "close_builder", lambda p, report=None: called.append(p))
    warn = "pages.DBF is locked.\n" + CTDRAW_CACHE_WARNING
    assert settings.option("auto_close_builder") is False
    assert BC.auto_close(parent, warn) == warn and called == []
    monkeypatch.setattr(settings, "option", lambda key: key == "auto_close_builder")
    assert BC.auto_close(parent, warn) == "pages.DBF is locked."
    assert BC.auto_close(parent, CTDRAW_CACHE_WARNING) == ""
    assert BC.auto_close(parent, "Other.") == "Other."
    assert called == [parent, parent]
