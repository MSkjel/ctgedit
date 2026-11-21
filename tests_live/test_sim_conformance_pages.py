"""The Simulate window against the live runtime's recordings."""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_sim_ctsim_live import R2_USER, _real_engine  # noqa: E402

CASES = os.environ.get("CTG_SIM_CONFORMANCE") or os.path.join(
    ROOT, "external", "cicode-sim", "conformance", "pages"
)

#: the differing pixels (percent, trends and ActiveX controls masked) measured per frame, per
#: recording format (the compare runs the project's startup code and draws the
#: page's Cicode output, as the Simulate window does; the engine with its own scheduler, so
#: ExampleSA's process simulation runs): ctsim-pagecase/0 = the first recordings (tags only);
#: /1 = the re-recording with the alarm server's and the equipment's state in every frame,
#: which the compare sets after the tags; a frame may come out at most MARGIN points worse.
MEASURED = {
    "ctsim-pagecase/1": {
        "Drives/drive01/f0_baseline": 0.27,
        "Drives/drive01/f1_running_manual": 0.35,
        "Drives/drive01/f2_op_low": 0.31,
        "Drives/drive01/f3_op_high": 0.31,
        "Drives/drive01/f4_fb_bad": 0.31,
        "FP_PMP_HD1080/pump05/f0_baseline": 0.24,
        "FP_PMP_HD1080/pump05/f1_oos": 0.24,
        "FP_PMP_HD1080/pump05/f2_maint": 0.24,
        "FP_PMP_HD1080/pump05/f3_manual_running": 0.32,
        "FP_PMP_HD1080/pump05/f4_running_bad": 0.34,
        "FP_Wind_HD1080/faceplate/f0_baseline": 0.51,
        "FP_Wind_HD1080/faceplate/f1_wind_45": 0.45,
        "FP_Wind_HD1080/faceplate/f2_wind_300": 0.59,
        "FP_Wind_HD1080/faceplate/f3_speed_bad": 0.69,
        "Filling_L3/batch_id/f0_baseline": 0.35,
        "Filling_L3/batch_id/f1_ids": 0.35,
        "Filling_L3/batch_id/f2_id_bad": 0.35,
        "Filling_L3/tank20/f0_baseline": 0.35,
        "Filling_L3/tank20/f1_low": 0.42,
        "Filling_L3/tank20/f2_high": 0.43,
        "Filling_L3/tank20/f3_tnk20_bad": 0.42,
        "Meters/meter/f0_baseline": 0.36,
        "Meters/meter/f1_pv_15": 0.36,
        "Meters/meter/f2_pv_85": 0.43,
        "Meters/meter/f3_pv_bad": 0.44,
        "Mining/mining_drive/f0_baseline": 0.14,
        "Mining/mining_drive/f1_low": 0.22,
        "Mining/mining_drive/f2_high": 0.17,
        "Mining/mining_drive/f3_op_bad": 0.17,
        "Mixing_L3/tank_level/f0_baseline": 0.28,
        "Mixing_L3/tank_level/f1_level_120": 0.28,
        "Mixing_L3/tank_level/f2_level_1350": 0.28,
        "Mixing_L3/tank_level/f3_level_bad": 0.28,
        "Others/polar_star/f0_baseline": 0.36,
        "Others/polar_star/f1_peak_high": 0.40,
        "Others/polar_star/f2_peak_low": 0.37,
        "Others/wind_compass/f0_baseline": 0.35,
        "Others/wind_compass/f1_wind_90": 0.35,
        "Others/wind_compass/f2_wind_225": 0.39,
        "Others/wind_compass/f3_windpv_bad": 0.41,
        "Overview_L2/recipe_text/f0_baseline": 1.48,
        "Overview_L2/recipe_text/f1_recipe": 1.48,
        "Overview_L2/sequence/f0_baseline": 1.47,
        "Overview_L2/sequence/f1_step3": 1.47,
        "Overview_L2/sequence/f2_step7": 1.48,
        "Overview_L2/sequence/f3_step_bad": 1.49,
        "Supply_L3/full_milk/f0_baseline": 0.86,
        "Supply_L3/full_milk/f1_low": 0.84,
        "Supply_L3/full_milk/f2_high": 0.86,
        "Supply_L3/full_milk/f3_ac01_bad": 0.84,
        "Valves/valve/f0_baseline": 0.14,
        "Valves/valve/f1_closed_auto": 0.15,
        "Valves/valve/f2_half_manual": 0.17,
        "Valves/valve/f3_open": 0.16,
        "Valves/valve/f4_fb_bad": 0.16,
    },
}
MARGIN = 0.5
#: a frame not measured yet (a new recording): a broken page would come out far worse
UNMEASURED_CEILING = 25.0


@pytest.mark.skipif(os.name != "nt", reason="named pipes and the live captures need Windows")
@pytest.mark.skipif(os.environ.get("CTG_SKIP_HEAVY") == "1", reason="heavy (about a minute)")
def test_the_simulated_pages_look_like_the_live_runtime(tmp_path):
    if not os.path.isdir(CASES):
        pytest.skip("no conformance recordings at %s" % CASES)
    if not os.path.isdir(os.path.join(R2_USER, "ExampleSA")):
        pytest.skip("the R2 ExampleSA project is not installed")
    loc, tried = _real_engine()
    if loc is None:
        pytest.skip("ctsim.exe is not built (looked in %s)" % ", ".join(tried))
    out = tmp_path / "out"
    report = tmp_path / "report.json"
    env = dict(os.environ, PYTHONPATH=ROOT, CTG_SIM_CONFORMANCE=CASES)
    env.pop("QT_QPA_PLATFORM", None)  # the tool's own (the native one on Windows)
    r = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "tools", "sim_page_compare.py"),
            "--cases",
            CASES,
            "--ctsim",
            loc.exe,
            "--out",
            str(out),
            "--json",
            str(report),
        ],
        env=env,
        capture_output=True,
        timeout=1800,
    )
    text = r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")
    if r.returncode == 3:
        pytest.skip(text.strip().splitlines()[-1])
    assert r.returncode == 0, text[-3000:]
    with open(report, encoding="utf-8") as fh:
        rows = json.load(fh)
    if any(r.get("liveStateUnavailable") for r in rows):
        pytest.skip(
            "this ctsim.exe has no ctsim/setAlarm (an engine before engine/live-alarms): "
            "the ctsim-pagecase/1 frames cannot be set up (%s)" % loc.exe
        )
    assert len(rows) >= len(MEASURED["ctsim-pagecase/1"]), (len(rows), text[-2000:])
    worse = []
    formats = {}
    for row in rows:
        key = "%s/%s/%s" % (row["page"], row["case"], row["frame"])
        case = os.path.join(CASES, "ExampleSA", row["page"], row["case"], "case.json")
        if case not in formats:
            with open(case, encoding="utf-8") as fh:
                formats[case] = json.load(fh).get("format")
        measured = MEASURED.get(formats[case]) or {}
        limit = measured[key] + MARGIN if key in measured else UNMEASURED_CEILING
        assert not row["tagsFailed"], (key, row["tagsFailed"])
        # a /1 frame's alarms and equipment state all reach the engine
        assert not row.get("liveStateFailed"), (key, row["liveStateFailed"])
        if row["masked"] > limit:
            worse.append((key, row["masked"], limit))
    assert not worse, "frames that look less like the live runtime than measured (diff images in %s): %s" % (
        out,
        worse,
    )
