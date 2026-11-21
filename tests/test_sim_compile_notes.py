"""Load notes about code that didn't compile."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fake_ctsim as F  # noqa: E402
from ctgedit.sim import client as C  # noqa: E402
from ctgedit.ui.simulate.ctsim_source import compile_note_lines, is_compile_note  # noqa: E402
from test_sim_engine_source import _rect_page, _sim, hub, wait_for  # noqa: E402,F401

pytestmark = pytest.mark.skipif(os.name != "nt", reason="named pipes need Windows")

STOP = "the compile stops at E2021 Record already defined [C:/Projects/Include/Mixer.ci:15] {MixerWinNew}"
NOTES = (
    [
        "42 file(s) from source: a.ci, b.ci",
        "compile error: E2021 Record already defined [C:/Projects/Include/Mixer.ci:15] {MixerWinNew}",
        "compile error: E2021 Record already defined [C:/Projects/Include/Mixer.ci:113] {MixerSetPageTag}",
        "Mixer.ci does not compile (E2021 Record already defined [...] {MixerWinNew}): "
        "its functions are compiled from what the parser recovered",
    ]
    + ["not compiled from source: Fn%d: %s" % (k, STOP) for k in range(382)]
    + [
        "not compiled from source: Lonely: name sX not resolved",
    ]
)


@pytest.fixture
def servers(tmp_path, monkeypatch):
    folder = str(tmp_path / "servers")
    monkeypatch.setenv(C.SERVERS_ENV, folder)
    monkeypatch.delenv(C.DISABLE_ENV, raising=False)
    return folder


@pytest.fixture
def page_file(tmp_path):
    proj = tmp_path / "User" / "Proj"
    proj.mkdir(parents=True)
    path = proj / "Mixer.ctg"
    path.write_bytes(b"")
    return str(path)


def test_the_notes_are_summed_up_by_reason():
    lines = compile_note_lines(NOTES)
    levels = [lv for lv, _t in lines]
    texts = [t for _lv, t in lines]
    assert levels == ["error", "error", "error", "error", "warning"]
    assert texts[0] == (
        "Compile error: E2021 Record already defined [C:/Projects/Include/Mixer.ci:15] {MixerWinNew}"
    )
    assert texts[2] == "382 functions did not compile from source (Fn0, Fn1, Fn2, Fn3, Fn4, ...): " + STOP
    assert texts[3] == "Lonely did not compile from source: name sX not resolved"
    assert texts[4].startswith("Mixer.ci does not compile (E2021") and texts[4].endswith("recovered.")
    # the other notes are not compile notes
    assert not is_compile_note(NOTES[0]) and all(is_compile_note(n) for n in NOTES[1:])
    # many compile errors: the first ten, then a count
    many = ["compile error: E2031 FUNCTION expected [x.ci:%d] {F%d}" % (k, k) for k in range(14)]
    out = compile_note_lines(many)
    assert len(out) == 11 and out[-1] == ("error", "4 more compile errors.")
    assert compile_note_lines([]) == [] and compile_note_lines(["plain note"]) == []


def test_the_notes_reach_outputs_once_as_errors(servers, hub, page_file):
    srv = F.FakeServer(os.path.dirname(page_file), servers, pages=True)
    srv.load_notes = list(NOTES)
    srv.start()
    sim = _sim(hub)
    try:
        sim.start(page_file, _rect_page())
        assert wait_for(lambda: sim.page_active())
        seen = []
        assert wait_for(
            lambda: (seen.extend(sim.drain()), any("382 functions did not compile" in e.text for e in seen))[
                1
            ]
        )
        compile_lines = [
            e
            for e in seen
            if "did not compile" in e.text or "does not compile" in e.text or "Compile error" in e.text
        ]
        assert [e.level for e in compile_lines] == ["error", "error", "error", "error", "warning"]
        assert all(e.text.startswith("Startup: ") for e in compile_lines)
        # the raw notes are not logged one by one
        assert not any(e.text.startswith("Startup: not compiled from source") for e in seen)
        assert not any("Startup: 42 file(s)" in e.text for e in seen)  # (a hello note: tool tip only)
        # the status tool tip sums them up too
        detail = sim.status().detail
        assert "382 functions did not compile from source" in detail and "Compile error: E2021" in detail
        assert "not compiled from source: Fn3" not in detail
    finally:
        sim.stop()
        srv.stop()
