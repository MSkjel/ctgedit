"""Conftest for the corpus suite (tests that need the real installed projects)."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "tests")
# tests/ (shared_fixtures, hermetic, fxinstall and the helpers of tests/ a file here
# imports) and this folder (corpus) are importable on their own
for _p in (TESTS, HERE):  # (this folder first)
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

from shared_fixtures import *  # noqa: E402,F401,F403
import corpus  # noqa: E402
import hermetic  # noqa: E402

#: with the guard on, this folder's tests are not collected (they read the real install)
collect_ignore_glob = ["test_*.py"] if hermetic.enabled() else []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    corpus.report(item, outcome.get_result())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if collect_ignore_glob:
        terminalreporter.write_line(
            "tests_corpus: not collected with CTG_HERMETIC=1 (its tests read the real "
            "install); run it without the guard"
        )
