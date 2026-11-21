"""Conftest for the hermetic suite. Fixtures live in shared_fixtures.py."""

import os

os.environ["CTG_HERMETIC"] = "1"
# every test's windows offscreen, whichever module makes the application first
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

# (shared_fixtures sets the fixtures' text conditions and imports the hermetic guard before
# anything imports ctgedit)
from shared_fixtures import *  # noqa: E402,F401,F403
