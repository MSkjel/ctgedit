"""Manual links into the installed AVEVA help."""

import os

import pytest

from ctgedit import help as H


def test_every_aveva_link_names_an_installed_topic():
    if not H.aveva_available():
        pytest.skip("the AVEVA Plant SCADA help is not installed")
    bad = []
    seen = 0
    for t in H.topics():
        for target in H.links(t):
            if target.startswith("aveva:"):
                seen += 1
                if not os.path.isfile(os.path.join(H.aveva_docs(), "%s.html" % target[6:])):
                    bad.append("%s: %s (no such AVEVA topic)" % (t, target))
    assert seen
    assert not bad, bad
