"""Every place the simulator protocol lives agrees on its names."""

import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, "external", "cicode-sim", "engine")
HOST = os.path.join(ENGINE, "src", "CicodeSim.Engine", "Host")


def _client_requests():
    names = set()
    for rel in ("ctgedit/sim/client.py",) + tuple(
        "ctgedit/ui/simulate/" + f
        for f in os.listdir(os.path.join(REPO, "ctgedit", "ui", "simulate"))
        if f.endswith(".py")
    ):
        src = open(os.path.join(REPO, rel), encoding="utf-8").read()
        names |= set(re.findall(r'(?:request|request_async|_req)\("([a-zA-Z]+)"', src))
    from ctgedit.sim import client as C

    return names | set(C.REQUESTS)


def _engine_text():
    out = []
    for dp, _dn, fns in os.walk(HOST):
        for fn in fns:
            if fn.endswith(".cs"):
                out.append(open(os.path.join(dp, fn), encoding="utf-8").read())
    return "\n".join(out)


@pytest.mark.skipif(not os.path.isdir(HOST), reason="the engine checkout is not there")
def test_every_request_and_event_is_the_engines_and_documented():
    from ctgedit.sim import client as C

    engine = _engine_text()
    handled = set(re.findall(r'case "ctsim/([a-zA-Z]+)"', engine))
    sent = set(re.findall(r'"ctsim/([a-zA-Z]+)"', engine))
    doc = open(os.path.join(ENGINE, "PROTOCOL.md"), encoding="utf-8").read()
    documented = set(re.findall(r"ctsim/([a-zA-Z]+)", doc))
    reqs = _client_requests()
    assert reqs - handled == set(), "requests the engine does not handle"
    assert set(C.EVENTS) - sent == set(), "events the engine never sends"
    assert (reqs | set(C.EVENTS)) - documented == set(), "not in engine/PROTOCOL.md"


def test_the_fake_handles_what_the_client_sends():
    src = open(os.path.join(REPO, "tests", "fake_ctsim.py"), encoding="utf-8").read()
    fake = set(re.findall(r"def _r_([a-zA-Z]+)", src))
    # (usedTags: the client asks it only of the real engine's page listing)
    missing = _client_requests() - fake - {"usedTags"}
    assert not missing, "the fake does not handle: %s" % ", ".join(sorted(missing))
