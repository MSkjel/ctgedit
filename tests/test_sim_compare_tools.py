"""The parts of the sim compare tools that need no engine."""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")


@pytest.fixture(scope="module")
def spc():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    spec = importlib.util.spec_from_file_location(
        "sim_page_compare", os.path.join(TOOLS, "sim_page_compare.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _case_dir(tmp_path, frames, window=None):
    from PIL import Image

    d = tmp_path / "FxSim" / "Page1" / "c1"
    d.mkdir(parents=True)
    for f in frames:
        for k in [f.get("capture")] + list(f.get("captures") or []):
            if k and not (d / k).exists():
                Image.new("RGB", (40, 20), (len(k), 0, 0)).save(d / k)
    doc = {
        "format": "ctsim-pagecase/1",
        "runtime": {"project": "FxSim"},
        "page": {"name": "Page1", "ctg": {"file": "Page1.ctg"}},
        "window": window or {"number": 8, "client": [11, 11, 40, 20]},
        "frames": frames,
    }
    (d / "case.json").write_text(json.dumps(doc), encoding="utf-8")
    return str(d)


class _Session:
    """What CaseRun asks of an engine session, recorded."""

    def __init__(self, tags=None, fail=()):
        self.requests = []
        self.tags = tags or []
        self.fail = set(fail)

    def request(self, name, args=None, timeout=None):
        from ctgedit.sim import client as C

        self.requests.append((name, args))
        if name in self.fail:
            raise C.RequestFailed(name, "refused", {})
        if name == "tags":
            return {"tags": self.tags}
        if name == "call":
            return {"state": "stop", "result": "0"}
        return {}


def test_quality_calls_are_not_replayed_and_old_steps_ran_in_window_0(spc):
    frame = {
        "steps": [
            {"set": "T", "value": 1, "result": {"ok": True}},
            {"call": 'TagSetOverrideBad("T")', "result": {"ok": True}},
            {"call": 'PageSetStr("V", "1")', "result": {"ok": True}},
            {"call": 'PageSetInt("N", 1)', "window": 8, "result": {"ok": True}},
        ]
    }
    assert spc.call_steps({}, frame) == [('PageSetStr("V", "1")', 0), ('PageSetInt("N", 1)', 8)]


def test_calls_replay_in_the_window_they_ran_in(spc, tmp_path):
    frame = {
        "name": "p1",
        "capture": "p1.png",
        "steps": [{"call": 'PageSetInt("N", 1)', "window": 8}, {"call": 'PageSetStr("V", "1")'}],
    }
    run = spc.CaseRun(_case_dir(tmp_path, [frame]), None)
    run.session = _Session()
    run.window = 3  # the simulator shows the page in its window 3
    assert run._replay_calls(frame) == []
    assert [a for n, a in run.session.requests] == [
        {"expr": 'PageSetInt("N", 1)', "window": 3},
        {"expr": 'PageSetStr("V", "1")', "window": 0},
    ]
    # the page in the simulator's window 0: a call the recording ran in window 0 did not reach
    # the page live, so it is not replayed on the page
    run.session = _Session()
    run.window = 0
    notes = run._replay_calls(frame)
    assert [a["window"] for n, a in run.session.requests] == [0]
    assert len(notes) == 1 and "no simulator window matches" in notes[0]


def test_the_snapshot_tags_are_locked_once(spc, tmp_path):
    run = spc.CaseRun(_case_dir(tmp_path, [{"name": "f0", "capture": "f0.png"}]), None)
    run.session = _Session(fail={"lockTag"})  # (an alarm property: refused, ignored)
    run._lock(["A.B", "T1", "a.b"])
    run._lock(["T1", "T2"])
    assert [a["name"] for n, a in run.session.requests] == ["A.B", "T1", "T2"]
    assert all(a["locked"] is True for n, a in run.session.requests)


def test_drift_names_the_snapshot_tags_that_moved(spc, tmp_path):
    run = spc.CaseRun(_case_dir(tmp_path, [{"name": "f0", "capture": "f0.png"}]), None)
    # tags answer under their defined names; an Equip.Item is matched by equipment and item
    run.session = _Session(
        tags=[
            {"name": "TopMilk_Sequence_Step", "equipment": "Company.Seq", "item": "Step", "value": 1},
            {"name": "Pos", "value": 341.993},  # a quantised REAL: not a move
            {"name": "Txt", "value": "b"},
        ]
    )
    writes = [("Company.Seq.Step", 12, "good"), ("Pos", 342, "good"), ("Txt", "a", "good")]
    assert run.drifted(writes) == ["Company.Seq.Step: 12 -> 1", "Txt: 'a' -> 'b'"]


def test_live_captures_and_masks(spc, tmp_path):
    frames = [
        {"name": "f0", "capture": "f0.png", "capturesTaken": 4},
        {
            "name": "f1",
            "capture": "f1.png",
            "captures": ["f1.png", "f1_c.png"],
            "capture2": "f1_c.png",
            "capturesTaken": 4,
        },
    ]
    run = spc.CaseRun(_case_dir(tmp_path, frames, window={"number": 8, "client": [11, 11, 1920, 20]}), None)
    imgs, steady = spc.live_captures(run, frames[0])
    assert len(imgs) == 1 and steady
    imgs, steady = spc.live_captures(run, frames[1])
    assert len(imgs) == 2 and not steady

    class _Page:
        objects = []

    run.page = _Page()
    assert spc.masks_for(run) == [(1909, 0, 11, 20)]
