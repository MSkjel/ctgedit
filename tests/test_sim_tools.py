"""The Simulate window's tools."""

import json
import threading

import pytest

from ctgedit.sim import client as C
from ctgedit.ui.simulate import sim_tools as T
from ctgedit.ui.simulate.sources import InputEvent, TagInfo


class _Session:
    alive = True

    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or {}
        self.hello = {"capabilities": {"profile": True, "tagProperties": True}}

    def request_async(self, name, args, callback=None, timeout=None):
        self.sent.append((name, args))
        if callback is not None:
            callback(self.answers.get((name, args.get("action"))) or self.answers.get(name) or {}, None)


class _Clock:
    def __init__(self):
        self.time = 10.0


class _Sim:
    def __init__(self, session):
        self._lock = threading.Lock()
        self._session = session
        self._window = 3
        self.project = r"C:\proj\FxSim"
        self.page_name = "FxSim1"
        self.logs = []
        self.clock = _Clock()
        self.props = []
        self.vars = []

    def _add_log(self, e):
        self.logs.append(e.text)

    def _now(self):
        return self.clock.time

    def clock_state(self):
        return self.clock

    def _held_by_debugger(self, what):
        return False

    def tags(self, page_only=False):
        return [TagInfo("Level", overrides={"ENG_FULL": "500"}), TagInfo("Run")]

    def page_vars(self):
        return [TagInfo("Mode", type="INT", value=2, page_var=True)]

    def set_tag_property(self, name, field, value=None):
        self.props.append((name, field, value))

    def set_page_var(self, name, value, type_=""):
        self.vars.append((name, value, type_))


def test_each_generator_is_a_scenario_step():
    assert T.generator_step("L", {"kind": "ramp", "min": 0, "max": 50, "period": 5}) == {
        "at": 0,
        "ramp": {"tag": "L", "from": 0.0, "to": 50.0, "seconds": 5.0},
    }
    assert T.generator_step("L", {"kind": "sine", "min": 1, "max": 2, "period": 4})["sine"] == {
        "tag": "L",
        "min": 1.0,
        "max": 2.0,
        "period": 4.0,
    }
    assert T.generator_step("L", {"kind": "random", "period": 2})["random"]["every"] == 2.0
    assert T.generator_step("R", {"kind": "square", "duty": 3})["square"]["duty"] == 1.0
    with pytest.raises(ValueError):
        T.generator_step("L", {"kind": "triangle"})


def test_the_generators_run_as_one_scenario_and_stop_with_the_last():
    s = _Session()
    t = T.SimTools(_Sim(s))
    t.set_generator("Level", {"kind": "sine", "min": 0, "max": 100, "period": 10})
    t.set_generator("Run", {"kind": "square", "min": 0, "max": 1, "period": 2})
    name, args = s.sent[-1]
    assert name == "scenario" and args["action"] == "run"
    sc = args["scenario"]
    assert sc["description"] == T.GENERATORS and sc["end"] >= 1e9
    assert [list(st)[1] for st in sc["steps"]] == ["sine", "square"]
    t.set_generator("Level", None)
    t.set_generator("Run", None)
    assert s.sent[-1] == ("scenario", {"action": "stop"})
    assert t.generator_of("Level") is None


def test_a_scenario_end_is_hidden_only_when_it_is_the_generators():
    # (names the scenario that ended: a played scenario's end is never
    # hidden, however many generator scenarios were replaced before it)
    t = T.SimTools(_Sim(_Session()))
    for _k in range(3):
        t.set_generator("Level", {"kind": "sine", "min": 0, "max": 100, "period": 10})
    assert t.swallow_finished({"description": T.GENERATORS, "stopped": True})
    assert not t.swallow_finished({"description": "Recorded in ctgedit's Simulate window", "passed": 1})
    assert not t.swallow_finished({"description": "", "passed": 1})


def test_a_request_that_did_not_go_hides_no_end():
    class Gone(_Session):
        def request_async(self, *a, **kw):
            raise C.ConnectionLost("gone")

    t = T.SimTools(_Sim(Gone()))
    assert not t.set_generator("Level", {"kind": "sine", "min": 0, "max": 1, "period": 1})
    assert not t.play({"steps": []})
    assert not t.swallow_finished()  # (no event body: not the generators')


def test_a_recording_is_a_scenario_with_the_simulated_times():
    sim = _Sim(_Session())
    t = T.SimTools(sim)
    t.start_recording()
    sim.clock.time = 11.5
    t.note_write("Level", 42.0, "Good")
    sim.clock.time = 12.0
    t.note_input(3, InputEvent("down", an=11, x=5, y=6))
    t.note_input(3, InputEvent("move", x=1, y=1))  # (not recorded)
    t.note_write("Level", 1, "Bad")
    sc = t.stop_recording()
    assert sc["format"] == "ctsim-scenario/0" and sc["project"] == "FxSim"
    assert sc["page"] == {"window": 3, "name": "FxSim1"}
    assert sc["steps"] == [
        {"at": 1.5, "set": {"Level": 42.0}},
        {"at": 2.0, "input": {"window": 3, "kind": "down", "an": 11, "x": 5.0, "y": 6.0}},
        {"at": 2.0, "set": {"Level": {"value": 1, "quality": "bad"}}},
    ]
    t.note_write("Level", 3, None)
    assert t.recorded_steps() == 3  # (stopped)


def test_play_keeps_the_generators_running():
    s = _Session()
    t = T.SimTools(_Sim(s))
    t.set_generator("Level", {"kind": "sine"})
    t.play({"format": "ctsim-scenario/0", "steps": [{"at": 1, "set": {"Run": 1}}]})
    sc = s.sent[-1][1]["scenario"]
    assert [list(st)[1] for st in sc["steps"]] == ["set", "sine"] and sc["end"] >= 1e9


def test_a_snapshot_saves_the_tags_the_overrides_and_the_page_variables(tmp_path):
    s = _Session(
        {
            ("scenario", "save"): {
                "scenario": {
                    "format": "ctsim-scenario/0",
                    "clock": {"start": 0},
                    "tags": {"Level": {"value": 7}},
                }
            }
        }
    )
    sim = _Sim(s)
    t = T.SimTools(sim)
    path = str(tmp_path / "snap.json")
    errs = []
    assert t.save_snapshot(path, errs.append) and errs == [None]
    snap = json.load(open(path, encoding="utf-8"))
    assert snap["format"] == T.SNAPSHOT_FORMAT
    assert snap["tagProperties"] == {"Level": {"ENG_FULL": "500"}}
    assert snap["pageVars"] == {"Mode": {"value": 2, "type": "INT"}}
    # loaded: the tags played (no clock: time goes on), the overrides and page variables set
    assert t.load_snapshot(path) == ""
    played = s.sent[-1][1]["scenario"]
    assert "clock" not in played and played["tags"] == {"Level": {"value": 7}}
    assert sim.props == [("Level", "ENG_FULL", "500")]
    assert sim.vars == [("Mode", 2, "int")]
    other = tmp_path / "x.json"
    other.write_text('{"format": "ctsim-scenario/0"}', encoding="utf-8")
    assert "not a ctgedit snapshot" in t.load_snapshot(str(other))


def test_a_call_runs_in_the_tabs_window_and_its_outcome_is_kept():
    s = _Session({"call": {"value": 42, "state": "stop"}})
    t = T.SimTools(_Sim(s))
    assert t.call('TagRead("Level")')
    assert s.sent[-1] == ("call", {"expr": 'TagRead("Level")', "timeoutMs": 2000, "window": 3})
    assert T.SimTools.outcome_text(t.outcome('TagRead("Level")')) == "42"
    assert (
        T.SimTools.outcome_text({"state": "halt", "error": 424, "errorText": "Tag not found"})
        == "error 424: Tag not found"
    )


def test_a_watch_reads_a_tag_with_tagread_else_runs_the_call():
    from ctgedit.ui.simulate.watch_panel import watch_call

    assert watch_call("Level") == 'TagRead("Level")'
    assert watch_call("Pump.Run") == 'TagRead("Pump.Run")'
    assert watch_call('TagGetProperty("L", "Units")') == 'TagGetProperty("L", "Units")'


def test_the_parts_of_an_expression_are_its_calls_innermost_first():
    from ctgedit.ui.simulate.inspector import call_parts

    assert call_parts('(fillLevel("A_LT01"))^(0.0,100)') == ['fillLevel("A_LT01")']
    assert call_parts('IF Pump AND F(G(1), "a(b)") THEN X(2) END') == ["G(1)", 'F(G(1), "a(b)")', "X(2)"]


def test_a_snapshot_write_that_fails_is_reported_and_logged(tmp_path):
    s = _Session({("scenario", "save"): {"scenario": {"format": "ctsim-scenario/0", "tags": {}}}})
    sim = _Sim(s)
    t = T.SimTools(sim)
    errs = []
    path = str(tmp_path / "gone" / "snap.json")
    assert t.save_snapshot(path, errs.append)
    assert len(errs) == 1 and errs[0] and not (tmp_path / "gone").exists()
    assert any("was not saved" in x for x in sim.logs)


def test_a_snapshot_held_by_the_debugger_changes_nothing(tmp_path):
    """Load Snapshot while the debugger holds the simulation: refused before any part is
    put back (no scenario, no property, no page variable), and the reason given."""
    s = _Session()
    sim = _Sim(s)
    path = tmp_path / "snap.json"
    path.write_text(
        json.dumps(
            {
                "format": T.SNAPSHOT_FORMAT,
                "scenario": {"format": "ctsim-scenario/0", "tags": {"Level": {"value": 7}}},
                "tagProperties": {"Level": {"ENG_FULL": "500"}},
                "pageVars": {"Mode": {"value": 2, "type": "INT"}},
            }
        ),
        encoding="utf-8",
    )
    sim._held_by_debugger = lambda what: True
    t = T.SimTools(sim)
    why = t.load_snapshot(str(path))
    assert "debugger" in why
    assert s.sent == [] and sim.props == [] and sim.vars == []
