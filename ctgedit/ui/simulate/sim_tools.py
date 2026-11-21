"""The Simulate window's tools."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from ...infra import logsetup
from ...sim import client as C
from . import sources as S

#: the scenario description the generators' scenario has (its end never comes; stopping it is
#: not reported)
GENERATORS = "ctgedit: tag generators"
#: the generators' scenario ends this far away (simulated seconds): never in practice
_FOREVER = 1.0e9
#: the generator kinds and their fields
KINDS = ("ramp", "sine", "random", "square")
SNAPSHOT_FORMAT = "ctgedit-sim-snapshot/1"


def generator_step(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The scenario step of generator ``spec`` {kind, min, max, period, duty?} on tag ``name``:
    a ramp goes from min to max in period seconds (and stays), a sine between min and max with
    that period, random values between them every period seconds, a square wave min / max."""
    kind = str(spec.get("kind") or "")
    lo, hi = float(spec.get("min", 0.0)), float(spec.get("max", 1.0))
    period = max(0.01, float(spec.get("period", 10.0)))
    if kind == "ramp":
        g = {"tag": name, "from": lo, "to": hi, "seconds": period}
    elif kind == "sine":
        g = {"tag": name, "min": lo, "max": hi, "period": period}
    elif kind == "random":
        g = {"tag": name, "min": lo, "max": hi, "every": period, "seed": int(spec.get("seed", 0))}
    elif kind == "square":
        g = {
            "tag": name,
            "min": lo,
            "max": hi,
            "period": period,
            "duty": min(1.0, max(0.0, float(spec.get("duty", 0.5)))),
        }
    else:
        raise ValueError("no generator %r (%s)" % (kind, ", ".join(KINDS)))
    return {"at": 0, kind: g}


class SimTools:
    """The tools of one EngineSimulation (see the module)."""

    def __init__(self, sim):
        self.sim = sim
        self._lock = threading.Lock()
        self.generators: Dict[str, Dict[str, Any]] = {}  # tag name -> spec
        self.recording = False
        self._rec_t0 = 0.0
        self._rec_steps: List[Dict[str, Any]] = []
        self.calls: Dict[str, Dict[str, Any]] = {}  # expr -> the last outcome
        self._calls_busy: set = set()
        self.profile: Optional[Dict[str, Any]] = None
        self.profile_error = ""
        self.scenario_running = ""  # the scenario played (its description), '' = none

    # -- the session --------------------------------------------------------------------------
    def _session(self):
        with self.sim._lock:
            s = self.sim._session
        return s if s is not None and s.alive else None

    def available(self) -> bool:
        return self._session() is not None

    def _log(self, kind: str, text: str) -> None:
        self.sim._add_log(S.LogEntry(self.sim._now(), kind, text, category="simulator"))

    def _request(
        self,
        name: str,
        args: Dict[str, Any],
        done: Optional[Callable[[Any, Any], None]] = None,
        timeout: float = 30.0,
    ) -> bool:
        if self.sim._held_by_debugger("a simulator tool"):
            return False
        s = self._session()
        if s is None:
            self._log("error", "The Cicode simulator does not run this page.")
            return False

        def cb(body, err):
            if err is not None and not isinstance(err, C.ConnectionLost) and done is None:
                self._log("error", "%s: %s" % (name, err))
            if done is not None:
                try:
                    done(body, err)
                except Exception:  # noqa: BLE001 -- a tool's bug must not end the reader
                    logsetup.note_exception("sim.sim_tools." + name)

        try:
            s.request_async(name, args, callback=cb, timeout=timeout)
        except C.CtsimError as e:
            self._log("error", "%s: %s" % (name, e))
            return False
        return True

    def swallow_finished(self, body: Optional[Dict[str, Any]] = None) -> bool:
        """A scenarioFinished event is the generators' own (not reported): the event names
        its scenario."""
        return isinstance(body, dict) and str(body.get("description") or "") == GENERATORS

    # -- generators ---------------------------------------------------------------------------
    def set_generator(self, name: str, spec: Optional[Dict[str, Any]]) -> bool:
        """Drive tag ``name`` with ``spec`` (generator_step), or stop driving it (None); every
        generator of the simulation runs as one scenario, started again from now."""
        if spec is not None:
            generator_step(name, spec)  # (a bad spec raises before anything)
        with self._lock:
            if spec is None:
                self.generators.pop(name, None)
            else:
                self.generators[name] = dict(spec)
        return self._run_generators()

    def _generator_steps(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [generator_step(n, g) for n, g in self.generators.items()]

    def _run_generators(self) -> bool:
        steps = self._generator_steps()
        if not steps:
            return self._request("scenario", {"action": "stop"})
        sc = {"format": "ctsim-scenario/0", "description": GENERATORS, "steps": steps, "end": _FOREVER}
        return self._request("scenario", {"action": "run", "scenario": sc})

    def generator_of(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            g = self.generators.get(name)
            return dict(g) if g is not None else None

    # -- recording ------------------------------------------------------------------------------
    def start_recording(self) -> None:
        cs = self.sim.clock_state()
        with self._lock:
            self.recording = True
            self._rec_t0 = cs.time if cs is not None else 0.0
            self._rec_steps = []
        self._log("log", "Recording: the tag writes and clicks from now on.")

    def _at(self) -> float:
        cs = self.sim.clock_state()
        return round(max(0.0, (cs.time if cs is not None else 0.0) - self._rec_t0), 3)

    def note_write(self, name: str, value: Any, quality: Optional[str]) -> None:
        """A tag write made in the window (EngineSimulation.set_tag)."""
        if not self.recording:
            return
        v: Any = value if not quality or quality == "Good" else {"value": value, "quality": quality.lower()}
        at = self._at()  # (the clock: before this lock, it takes the source's)
        with self._lock:
            self._rec_steps.append({"at": at, "set": {name: v}})

    def note_input(self, window: Optional[int], event) -> None:
        """A click or key on the page (EngineSimulation.input)."""
        if not self.recording or window is None or event.kind not in ("down", "up", "key"):
            return
        step: Dict[str, Any] = {"window": int(window), "kind": event.kind}
        if getattr(event, "an", None):
            step["an"] = int(event.an)
        if event.kind == "key":
            step["key"] = event.key
        else:
            step["x"], step["y"] = round(float(event.x or 0.0), 1), round(float(event.y or 0.0), 1)
        at = self._at()
        with self._lock:
            self._rec_steps.append({"at": at, "input": step})

    def stop_recording(self) -> Dict[str, Any]:
        """The recording as a ctsim-scenario/0 scenario (the page, the steps)."""
        with self._lock:
            self.recording = False
            steps = list(self._rec_steps)
        sc: Dict[str, Any] = {
            "format": "ctsim-scenario/0",
            "description": "Recorded in ctgedit's Simulate window",
            "steps": steps,
        }
        if self.sim.project:
            sc["project"] = self.sim.project.rstrip("\\/").rsplit("\\", 1)[-1]
        if self.sim.page_name:
            with self.sim._lock:
                w = self.sim._window
            sc["page"] = {"window": int(w or 0), "name": self.sim.page_name}
        self._log("log", "Recording stopped: %d steps." % len(steps))
        return sc

    def recorded_steps(self) -> int:
        with self._lock:
            return len(self._rec_steps)

    # -- scenarios ----------------------------------------------------------------------------
    def play(self, scenario: Dict[str, Any]) -> bool:
        """Run ``scenario`` (a ctsim-scenario/0 object) from now; the generators running go on
        beside it (their steps are added)."""
        sc = dict(scenario)
        steps = list(sc.get("steps") or ()) + self._generator_steps()
        sc["steps"] = steps
        if self.generators:
            sc["end"] = _FOREVER if sc.get("end") is None else sc["end"]
        desc = str(sc.get("description") or "a scenario")
        with self._lock:
            self.scenario_running = desc

        def done(body, err):
            if err is not None:
                with self._lock:
                    self.scenario_running = ""
                self._log("error", "The scenario did not start: %s" % err)
            else:
                self._log("log", "Playing %s (%d steps)." % (desc, len(steps)))

        ok = self._request("scenario", {"action": "run", "scenario": sc}, done)
        if not ok:
            with self._lock:
                self.scenario_running = ""
        return ok

    def stop_scenario(self) -> bool:
        """Stop the scenario played; the generators go on (they start again)."""
        with self._lock:
            self.scenario_running = ""
        if self.generators:
            return self._run_generators()
        return self._request("scenario", {"action": "stop"})

    # -- snapshots ----------------------------------------------------------------------------
    def save_snapshot(self, path: str, done: Callable[[Optional[str]], None]) -> bool:
        """Write the simulation's state to ``path``: the tags written since the start with
        their values (ctsim/scenario save), the tag property overrides, the window's page
        variables. ``done(error)`` on the reader thread."""
        props: Dict[str, Dict[str, str]] = {}
        try:
            for t in self.sim.tags(page_only=False):
                if t.overrides:
                    props[t.name] = dict(t.overrides)
        except Exception:  # noqa: BLE001 -- the snapshot still saves the tags
            logsetup.note_exception("sim.sim_tools.save_snapshot")
        page_vars = {t.name: {"value": t.value, "type": t.type} for t in self.sim.page_vars()}

        def got(body, err):
            if err is not None:
                done(str(err))
                return
            sc = body.get("scenario") if isinstance(body, dict) else None
            out = {
                "format": SNAPSHOT_FORMAT,
                "page": self.sim.page_name,
                "project": self.sim.project,
                "scenario": sc or {},
                "tagProperties": props,
                "pageVars": page_vars,
            }
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(out, fh, indent=1)
            except OSError as e:
                self._log("error", "The snapshot was not saved to %s: %s" % (path, e))
                done(str(e))
                return
            n = len((sc or {}).get("tags") or {})
            self._log(
                "log",
                "Snapshot saved: %d tags, %d tag property overrides, %d page "
                "variables (%s)." % (n, len(props), len(page_vars), path),
            )
            done(None)

        return self._request("scenario", {"action": "save"}, got)

    def load_snapshot(self, path: str) -> str:
        """Put a snapshot back: its tag values (its scenario played), its property overrides,
        its page variables. '' when sent, else why not."""
        try:
            with open(path, encoding="utf-8") as fh:
                snap = json.load(fh)
        except (OSError, ValueError) as e:
            return str(e)
        if not isinstance(snap, dict) or snap.get("format") != SNAPSHOT_FORMAT:
            return "not a ctgedit snapshot (%s)" % SNAPSHOT_FORMAT
        # (nothing is put back while the debugger holds the simulation: a part alone would
        # leave a state that was never saved)
        if self.sim._held_by_debugger("Load Snapshot"):
            return "the simulation is paused by the debugger. Continue it, then load the snapshot again"
        sc = snap.get("scenario") or {}
        if sc:
            sc = dict(sc)
            sc.pop("clock", None)  # (the clock goes on: only the values come back)
            sc.setdefault("description", "Snapshot %s" % path)
            if not self.play(sc):
                return "its tag values could not be sent to the simulation, so nothing was changed"
        # (from here a part that fails is named: the tag values are already on their way)
        failed: List[str] = []
        for name, fields in (snap.get("tagProperties") or {}).items():
            for field_, value in (fields or {}).items():
                try:
                    self.sim.set_tag_property(name, field_, value)
                except Exception as e:  # noqa: BLE001  # swallow-ok: named in the result and the log
                    failed.append("%s.%s (%s)" % (name, field_, e))
        for name, v in (snap.get("pageVars") or {}).items():
            if isinstance(v, dict):
                fn = getattr(self.sim, "set_page_var", None)
                if fn is not None:
                    try:
                        fn(
                            name,
                            v.get("value"),
                            "string" if str(v.get("type")).upper() == "STRING" else "int",
                        )
                    except Exception as e:  # noqa: BLE001  # swallow-ok: named in the result and the log
                        failed.append("%s (%s)" % (name, e))
        if failed:
            self._log("error", "Snapshot partly loaded (%s): not set: %s." % (path, ", ".join(failed)))
            return "the tag values were put back, but these were not: %s" % ", ".join(failed)
        self._log("log", "Snapshot loaded: %s." % path)
        return ""

    # -- calls --------------------------------------------------------------------------------
    def call(self, expr: str, done: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """Evaluate one Cicode call (literal arguments) in the tab's window; the outcome
        {value, result, state, error, errorText, ...} goes to ``calls[expr]`` and ``done``."""
        expr = (expr or "").strip()
        if not expr:
            return False
        with self._lock:
            if expr in self._calls_busy:
                return False
            self._calls_busy.add(expr)
        with self.sim._lock:
            window = self.sim._window
        args: Dict[str, Any] = {"expr": expr, "timeoutMs": 2000}
        if window is not None:
            args["window"] = int(window)

        def got(body, err):
            out = dict(body) if isinstance(body, dict) else {}
            if err is not None:
                out.setdefault("errorText", str(err))
                out["failed"] = True
            with self._lock:
                self._calls_busy.discard(expr)
                self.calls[expr] = out
            if done is not None:
                done(out)

        if not self._request("call", args, got, timeout=10.0):
            with self._lock:
                self._calls_busy.discard(expr)
            return False
        return True

    def outcome(self, expr: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            o = self.calls.get((expr or "").strip())
            return dict(o) if o is not None else None

    @staticmethod
    def outcome_text(o: Optional[Dict[str, Any]]) -> str:
        """An outcome as the Watch panel shows it: the value, else the error."""
        if o is None:
            return ""
        if o.get("failed") or str(o.get("state") or "").lower() == "halt":
            code = o.get("error")
            text = o.get("errorText") or o.get("haltReason") or "the call failed"
            return "error %s: %s" % (code, text) if code else "error: %s" % text
        if o.get("timedOut"):
            return "(still running)"
        v = o.get("value")
        return str(o.get("result") if v is None else v)

    # -- the profiler -------------------------------------------------------------------------
    def ask_profile(self, action: str = "get", top: int = 200) -> bool:
        def got(body, err):
            with self._lock:
                if err is not None:
                    self.profile_error = str(err)
                else:
                    self.profile_error = ""
                    self.profile = body if isinstance(body, dict) else None

        args = {"action": action, "top": int(top), "tree": True}
        return self._request("profile", args, got)


def tools_of(sim) -> Optional[SimTools]:
    """The tools of simulation ``sim`` (made on first use), None for one the Cicode simulator
    does not run (the demo values)."""
    if not hasattr(sim, "_session") or not hasattr(sim, "_add_log"):
        return None
    t = getattr(sim, "_tools", None)
    if t is None:
        t = SimTools(sim)
        sim._tools = t
    return t
