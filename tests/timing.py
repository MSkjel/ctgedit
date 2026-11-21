"""Time limits of the tests, checked when CTG_SPEED_TESTS=1 is set."""

import os

SPEED_TESTS = os.environ.get("CTG_SPEED_TESTS") == "1"


def check_speed(took: float, seconds: float, what: str) -> None:
    """``took`` (seconds) is under ``seconds``, with CTG_SPEED_TESTS=1."""
    if SPEED_TESTS:
        assert took < seconds, "%s took %.0f ms (the limit: %.0f ms)" % (what, took * 1000, seconds * 1000)
