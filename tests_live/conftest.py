"""Conftest for the live suite (COM, Studio, the compiler, the runtime, the engine)."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TESTS = os.path.join(REPO, "tests")
CORPUS = os.path.join(REPO, "tests_corpus")
# this folder (livekit, the files that import each other), tests/ (shared_fixtures,
# fake_ctsim, the helpers of tests/ a file here imports) and tests_corpus/ (corpus)
for _p in (CORPUS, TESTS, HERE):  # (this folder first)
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

from shared_fixtures import *  # noqa: E402,F401,F403
import corpus  # noqa: E402
import hermetic  # noqa: E402

#: with the guard on, this folder's tests are not collected (they drive the real tools)
collect_ignore_glob = ["test_*.py"] if hermetic.enabled() else []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A live test that reads an R2 page has the corpus suite's 'input changed' report."""
    outcome = yield
    corpus.report(item, outcome.get_result())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if collect_ignore_glob:
        terminalreporter.write_line(
            "tests_live: not collected with CTG_HERMETIC=1 (its tests drive the real "
            "tools); run it without the guard"
        )
