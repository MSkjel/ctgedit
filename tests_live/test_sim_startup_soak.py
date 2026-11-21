"""The Simulate window draws what the simulator has, whatever the timing."""

import json
import os
import subprocess
import sys

import pytest

import livekit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "sim_startup_soak.py")
R2 = r"C:\ProgramData\AVEVA Plant SCADA 2023 R2\User"


def _engine():
    loc, _tried = livekit.real_engine()
    return loc


heavy = pytest.mark.skipif(
    os.environ.get("CTG_SKIP_HEAVY") == "1"
    or os.name != "nt"
    or not os.path.isfile(os.path.join(R2, "Example", "Mixer.ctg")),
    reason="heavy (the real simulator): CTG_SKIP_HEAVY=1, or R2 Example is not installed",
)


@heavy
def test_the_drawn_states_equal_the_simulators_after_start_up(tmp_path):
    loc = _engine()
    if loc is None:
        pytest.skip("ctsim.exe is not built")
    work = os.environ.get("CTG_SIM_SOAK_WORK") or os.path.join(
        os.environ.get("TEMP", str(tmp_path)), "ctgedit_sim_soak"
    )
    out = tmp_path / "soak.json"
    env = dict(os.environ, PYTHONPATH=ROOT, QT_QPA_PLATFORM="offscreen")
    env.pop("CTG_SOAK_CODE", None)
    r = subprocess.run(
        [
            sys.executable,
            TOOL,
            "--cases",
            "mixer,alarm",
            "--runs",
            "2",
            "--seed",
            "11",
            "--jitter",
            "1",
            "--run-s",
            "1.5",
            "--work",
            work,
            "--engine",
            loc.exe,
            "--out",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=1500,
        cwd=ROOT,
    )
    assert out.is_file(), "the soak did not finish:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    rep = json.loads(out.read_text(encoding="utf-8"))
    summary = rep["summary"]
    assert len(summary) == 4, list(summary)
    for group, s in summary.items():
        assert s["runs"] == 2, group
        assert not [e for e in s["errors"] if "not live" in e or "run:" in e], (group, s["errors"])
        lost = {k: d for k, d in s["drawn_vs_fresh"].items() if (d.get("kinds") or {}).get("lost")}
        assert not lost, (group, json.dumps(lost, indent=1)[:3000])
