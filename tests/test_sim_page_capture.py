"""The parts of the page capture tool that need no runtime."""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def spc():
    spec = importlib.util.spec_from_file_location(
        "sim_page_capture", os.path.join(ROOT, "tools", "sim_page_capture.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- qualities
def test_quality_word_layout_matches_the_engine(spc):
    # engine World/Quality.cs: byte 0 general << 6 | substatus << 2 | limit, byte 1 extended,
    # bit 16 override, bit 17 control inhibit, bits 32..63 the data source error
    assert spc.quality_word({"g": 3}) == 0xC0
    assert spc.quality_word({"g": 0, "ovr": 1}) == 0x10000
    assert spc.quality_word({"g": 3, "ovr": 1}) == 0x100C0
    assert spc.quality_word({"g": 0, "s": 6}) == 0x18
    assert spc.quality_word({"g": 1, "e": 0x14, "ctl": 1}) == 0x40 | 0x1400 | 0x20000
    assert spc.quality_word({"g": 0, "err": 0x10000107}) == 0x10000107 << 32
    assert spc.quality_name(0x18) == "bad_comm_failure"
    assert spc.quality_name(0x10000) == "0x10000"


def test_snapshot_entry_numbers_text_and_errors(spc):
    e = spc.snapshot_entry({"type": "REAL"}, {"ok": True, "value": "42.5", "g": 3})
    assert e["value"] == 42.5 and e["text"] == "42.5" and e["quality"] == "good"
    assert "qualityWord" not in e
    e = spc.snapshot_entry({"type": "INT"}, {"ok": True, "value": "7", "g": 0, "ovr": 1})
    assert e["value"] == 7 and e["quality"] == "0x10000" and e["qualityWord"] == "0x10000"
    e = spc.snapshot_entry({"type": "STRING"}, {"ok": True, "value": "12", "g": 3})
    assert e["value"] == "12"
    e = spc.snapshot_entry({"type": "INT"}, {"ok": False, "error": 0x10000000 + 433})
    assert e == {"error": 0x10000000 + 433, "code": 433}


def test_quality_held_needs_the_general_quality_and_the_override_flag(spc):
    assert spc.quality_held({"quality": "0x10000", "qualityWord": "0x10000"}, "bad")
    # the 2026-10-01 batch: good + override where bad was asked
    assert not spc.quality_held({"quality": "0x100c0", "qualityWord": "0x100c0"}, "bad")
    assert spc.quality_held({"quality": "0x100c0", "qualityWord": "0x100c0"}, "good")
    assert not spc.quality_held({"quality": "good"}, "good")  # no override flag
    assert not spc.quality_held({"quality": "bad"}, "bad")
    assert not spc.quality_held({"error": 5}, "bad")
    assert not spc.quality_held(None, "bad")


# --------------------------------------------------------------------------- citect.ini
_INI = (
    b"[CtEdit]\r\nRun =C:\\User\\FxSim\r\nLASTDATABASE =FxSim\r\n\r\n[Win]\r\nAltSpace =1\r\n"
    b"[Kernel.Cluster1.TrendServer1.WindowPositions]\r\nShell=0,26,1006,633\r\n"
    b"[Kernel.Cluster1.ReportServer1.WindowPositions]\r\nShell=0,26,1006,633\r\n"
)


def test_ini_differences_ignore_window_positions_and_run(spc):
    swapped = (
        b"[CtEdit]\r\nRun =C:\\User\\CiSimRT\r\nLASTDATABASE =FxSim\r\n\r\n[Win]\r\nAltSpace =1\r\n"
        b"[Kernel.Cluster1.ReportServer1.WindowPositions]\r\nShell=0,26,1006,633\r\n"
        b"[Kernel.Cluster1.TrendServer1.WindowPositions]\r\nShell=0,20,900,600\r\n"
    )
    assert spc.ini_differences(_INI, swapped) == []
    assert spc.ini_differences(_INI, swapped, ignore_run=False) == ["ctedit"]
    other = _INI.replace(b"LASTDATABASE =FxSim", b"LASTDATABASE =FxMain")
    assert spc.ini_differences(_INI, other) == ["ctedit"]
    added = _INI + b"[Page]\r\nScanTime=250\r\n"
    assert spc.ini_differences(_INI, added) == ["page"]


# --------------------------------------------------------------------------- IO server caches
def _element(name, value, generic=192):
    return (
        '        <element name="%s">\r\n'
        '          <v type="Int32" size="1">\r\n            <item>%s</item>\r\n          </v>\r\n'
        "          <q>\r\n            <generic>%d</generic>\r\n            <status>0</status>\r\n"
        "            <error>0</error>\r\n          </q>\r\n"
        "          <t>2026-10-01T14:49:24.209572-07:00</t>\r\n"
        "          <qt>2026-10-01T14:49:24.209572-07:00</qt>\r\n"
        "          <vt>2026-10-01T14:49:24.209572-07:00</vt>\r\n"
        "          <eventid>0</eventid>\r\n        </element>\r\n"
    ) % (name, value, generic)


def _cache(tags):
    body = "".join(
        '    <tag name="%s">\r\n      <elements>\r\n%s      </elements>\r\n    </tag>\r\n'
        % (name, "".join(_element(*e) for e in els))
        for name, els in tags
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        '<datasource xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns="http://www.schneider-electric.com/Platform/PSI/DataSource/PersistenceCache/V1/">'
        "\r\n  <properties />\r\n  <tags>\r\n%s  </tags>\r\n</datasource>" % body
    ).encode("utf-8")


def test_override_cleanup_removes_only_the_new_idle_elements(spc, tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    before = _cache(
        [
            ("A_PV", [("Field", 45), ("OverrideMode", 0), ("Valid", 45)]),
            ("B_PV", [("Field", 1), ("OverrideMode", 0), ("Override", 7, 0)]),
            ("C_PV", [("Field", 2), ("OverrideMode", 0)]),
        ]
    )
    (data / "Cluster1.IOServer1Cluster1.PLC.cache").write_bytes(before)
    inv0 = spc.override_inventory(str(data))
    assert inv0 == {"Cluster1.IOServer1Cluster1.PLC.cache": {"B_PV": "0"}}

    # the batch overrides A_PV (then OverrideMode back to 0) and C_PV (left in mode 1)
    after = _cache(
        [
            ("A_PV", [("Field", 45), ("OverrideMode", 0), ("Valid", 45), ("Override", 88, 0)]),
            ("B_PV", [("Field", 1), ("OverrideMode", 0), ("Override", 7, 0)]),
            ("C_PV", [("Field", 2), ("OverrideMode", 1), ("Override", 2, 0)]),
        ]
    )
    path = data / "Cluster1.IOServer1Cluster1.PLC.cache"
    path.write_bytes(after)
    new = spc.new_overrides(inv0, spc.override_inventory(str(data)))
    assert new == {"Cluster1.IOServer1Cluster1.PLC.cache": {"A_PV": "0", "C_PV": "1"}}

    idle = [t for t, m in new["Cluster1.IOServer1Cluster1.PLC.cache"].items() if m == "0"]
    backup = tmp_path / "bak"
    assert spc.remove_override_elements(str(path), idle, str(backup)) == ["A_PV"]
    assert (backup / path.name).read_bytes() == after
    # byte for byte what the cache was with A_PV never overridden (C_PV in mode 1 stays)
    expect = _cache(
        [
            ("A_PV", [("Field", 45), ("OverrideMode", 0), ("Valid", 45)]),
            ("B_PV", [("Field", 1), ("OverrideMode", 0), ("Override", 7, 0)]),
            ("C_PV", [("Field", 2), ("OverrideMode", 1), ("Override", 2, 0)]),
        ]
    )
    assert path.read_bytes() == expect
    assert spc.remove_override_elements(str(path), ["A_PV"], str(backup)) == []


# --------------------------------------------------------------------------- images
def test_compare_images_tolerance_masks_and_either_capture(spc):
    from PIL import Image

    live = Image.new("RGB", (20, 10), (200, 200, 200))
    live2 = live.copy()
    live2.putpixel((5, 5), (0, 0, 0))  # the other flash phase
    sim = live.copy()
    assert spc.compare_images([live], sim)[0] == 0
    sim.putpixel((1, 1), (205, 195, 200))  # within 8
    assert spc.compare_images([live], sim)[0] == 0
    sim.putpixel((2, 2), (0, 0, 0))
    n, total, bbox, bad = spc.compare_images([live], sim)
    assert (n, total, bbox) == (1, 200, [2, 2, 3, 3])
    assert spc.compare_images([live], sim, masks=[(2, 2, 1, 1)])[:2] == (0, 199)
    sim2 = live.copy()
    sim2.putpixel((5, 5), (0, 0, 0))
    assert spc.compare_images([live, live2], sim2)[0] == 0
    assert spc.compare_images([live], sim2)[0] == 1
    with pytest.raises(ValueError):
        spc.compare_images([live], Image.new("RGB", (10, 10)))
    assert spc.diff_image(live, bad).getpixel((2, 2)) == (255, 0, 0)


# --------------------------------------------------------------------------- verify
def _case(tmp_path, spc, **over):
    from PIL import Image

    d = tmp_path / "pages" / "FxSim" / "Page1" / "c1"
    d.mkdir(parents=True)
    Image.new("RGB", (40, 20)).save(d / "f0.png")
    doc = {
        "format": spc.FORMAT,
        "window": {"client": [11, 11, 40, 20]},
        "scenario": {"format": "ctsim-scenario/0", "steps": []},
        "tags": {"T1": {"kind": "tag"}},
        "frames": [
            {
                "name": "f0",
                "at": 1.0,
                "capture": "f0.png",
                "snapshot": {"T1": {"value": 1, "text": "1", "quality": "good"}},
                "alarms": {},
                "equipment": {},
            }
        ],
        "restore": [{"set": "T1", "value": "0", "result": {"ok": True}}],
        "alarmsRead": {"notAdded": []},
        "alarmDefs": {},
        "equipmentRead": {},
        "equipmentDefs": {},
    }
    doc.update(over)
    (d / "case.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path / "pages", d


def test_verify_accepts_a_good_case(spc, tmp_path):
    root, _ = _case(tmp_path, spc)
    out = spc.verify_cases(str(root))
    assert out["cases"] == 1 and out["frames"] == 1 and out["problems"] == [] and out["notes"] == []


def test_verify_finds_size_stray_files_and_restore_failures(spc, tmp_path):
    root, d = _case(
        tmp_path,
        spc,
        window={"client": [0, 0, 41, 20]},
        restore=[{"set": "T1", "value": "0", "result": {"ok": False}}],
    )
    (d / "stray.png").write_bytes(b"x")
    problems = spc.verify_cases(str(root))["problems"]
    assert any("is 40x20, the client area 41x20" in p for p in problems)
    assert any("stray.png is not named" in p for p in problems)
    assert any("restore of T1 failed" in p for p in problems)


def _engine_pages():
    for d in (
        os.environ.get("CTSIM_PAGES", ""),
        os.path.join(ROOT, "external", "cicode-sim", "conformance", "pages"),
    ):
        if d and os.path.isdir(d):
            return d
    return None


def test_recorded_cases_verify(spc):
    """The engine's conformance/pages (when checked out): every case verifies, every
    quality a frame asked for is in its snapshot."""
    root = _engine_pages()
    if not root:
        pytest.skip("no conformance/pages checkout (CTSIM_PAGES)")
    out = spc.verify_cases(root)
    if not out["cases"]:
        pytest.skip("no case under %s yet" % root)
    assert out["problems"] == []
    assert not [n for n in out["notes"] if "quality" in n]


def test_verify_notes_a_quality_that_did_not_take(spc, tmp_path):
    frames = [
        {
            "name": "f0",
            "at": 1.0,
            "capture": "f0.png",
            "steps": [{"call": 'TagSetOverrideBad("T1")', "result": {"ok": True}}],
            "snapshot": {"T1": {"value": 1, "text": "1", "quality": "0x100c0", "qualityWord": "0x100c0"}},
            "alarms": {},
            "equipment": {},
        }
    ]
    root, _ = _case(tmp_path, spc, frames=frames)
    out = spc.verify_cases(str(root))
    assert out["problems"] == []
    assert out["notes"] == ["FxSim/Page1/c1 f0: the quality of T1 did not take (snapshot 0x100c0)"]


def test_cicode_string_literal(spc):
    assert spc.cicode_str("FxPlant.Drive01.FB") == '"FxPlant.Drive01.FB"'
    assert spc.cicode_str('a"b^c') == '"a^"b^^c"'


# --------------------------------------------------------------------------- alarms, equipment
def _ans(v):
    return {"ok": True, "value": str(v)}


def _props(on, ack, dis, state, ontime=0, ms=0, t=0):
    return [_ans(on), _ans(ack), _ans(dis), _ans(state), _ans(ontime), _ans(ms), _ans(t)]


def test_alarm_states_leave_out_normal_alarms_and_convert_times(spc):
    assert spc.ALARM_PROPS == ("On", "Ack", "Disabled", "State", "OnTime", "Millisec", "Time")
    # live (a runtime): an HH alarm OFF unacknowledged (State 256),
    # a fault alarm never triggered (OnTime 0, a stray Millisec), a disabled alarm
    items = (
        _props(0, 0, 0, 256, 1790925952, 501, 1790925952)
        + _props(0, 1, 0, 0, 0, 344, 1781022600)
        + _props(1, 1, 0, 1, 1790891230, 752, 1790891230)
        + _props(0, 1, 1, 512)
    )
    out = spc.alarm_states(["A_HH", "A_Faulted", "B_On", "C_Dis"], items)
    assert list(out) == ["A_HH", "B_On", "C_Dis"]
    assert out["A_HH"] == {
        "on": 0,
        "ack": 0,
        "disabled": 0,
        "state": 256,
        "onTime": "2026-10-02T07:25:52.501Z",
        "time": "2026-10-02T07:25:52.000Z",
    }
    assert out["B_On"]["on"] == 1 and out["B_On"]["ack"] == 1
    assert out["C_Dis"] == {"on": 0, "ack": 1, "disabled": 1, "state": 512}
    bad = spc.alarm_states(["X"], [{"ok": False, "error": 0x10000000 + 433}] + _props(0, 1, 0, 0)[1:])
    assert bad == {"X": {"error": 0x10000000 + 433}}
    assert spc._alarm_moves(out, dict(out)) == []
    moved = dict(out)
    moved["A_HH"] = dict(out["A_HH"], ack=1, state=0)
    assert spc._alarm_moves(out, moved) == ["A_HH"]
    assert spc._alarm_moves(out, {}) == ["A_HH", "B_On", "C_Dis"]


def test_live_state_requests_feed_every_checked_alarm_and_the_equipment(spc):
    doc = {"alarmDefs": {"A1": {}, "A2": {}, "A3": {}}}
    frame = {
        "alarms": {"A1": {"on": 0, "ack": 0, "disabled": 0, "state": 256}, "A3": {"error": 5}},
        "equipment": {"E1": {"MODE": "0", "STATE": "Active", "DRMODE": {"error": 7}}, "E2": {}},
    }
    assert spc.live_state_requests(doc, frame) == [
        ("ctsim/setAlarm", {"name": "A1", "state": {"on": 0, "ack": 0, "disabled": 0, "state": 256}}),
        ("ctsim/setAlarm", {"name": "A2", "state": "off"}),
        ("ctsim/setEquipment", {"name": "E1", "fields": {"MODE": "0", "STATE": "Active"}}),
    ]
    assert spc.live_state_requests({}, {"snapshot": {}}) == []


def test_equipment_call_is_one_call_with_literal_arguments(spc):
    assert (
        spc.equipment_call("FxPlant.Mixing", "STATE") == 'EquipGetProperty("FxPlant.Mixing", "STATE", 1, "")'
    )
    assert spc.EQUIP_FIELDS == ("MODE", "STATE", "DRMODE")


def _v1(spc, **frame_over):
    fr = {
        "name": "f0",
        "at": 1.0,
        "capture": "f0.png",
        "snapshot": {"T1": {"value": 1, "text": "1", "quality": "good"}},
        "alarms": {"A1": {"on": 0, "ack": 0, "disabled": 0, "state": 256}},
        "equipment": {"E1": {"MODE": "0", "STATE": "Active", "DRMODE": "0"}},
    }
    fr.update(frame_over)
    return dict(
        alarmsRead={"notAdded": []},
        alarmDefs={"A1": {"kind": "digital"}},
        equipmentRead={"fields": ["MODE", "STATE", "DRMODE"]},
        equipmentDefs={"E1": {"scheduled": True}},
        frames=[fr],
    )


def test_verify_v1_checks_alarms_and_equipment(spc, tmp_path):
    assert spc.FORMAT == "ctsim-pagecase/1" and "ctsim-pagecase/0" in spc.FORMATS
    root, _ = _case(tmp_path / "a", spc, **_v1(spc))
    assert spc.verify_cases(str(root))["problems"] == []
    root, _ = _case(
        tmp_path / "b",
        spc,
        **_v1(spc, alarms={"A2": {"on": 1}}, equipment={"E1": {"MODE": "0", "STATE": {"error": 5}}}),
    )
    problems = spc.verify_cases(str(root))["problems"]
    assert any("alarm A2 is not in alarmDefs" in p for p in problems)
    assert any("E1 STATE not read" in p for p in problems)
    assert any("E1 DRMODE not read" in p for p in problems)
    over = _v1(spc)
    del over["frames"][0]["equipment"]
    root, _ = _case(tmp_path / "c", spc, **over)
    assert any("has no equipment" in p for p in spc.verify_cases(str(root))["problems"])
    # a /0 case (the first recordings) needs neither
    root, _ = _case(tmp_path / "d", spc, format="ctsim-pagecase/0")
    assert spc.verify_cases(str(root))["problems"] == []


def test_project_alarms_follow_the_engine_kind_order(spc, fx_install):
    """FxSim's alarms: four digital (FX_DA_1 .. 4), three analog (FX_AA_1 .. 3) and one
    advanced (FX_ADV_1), with equipment, items and categories 1 to 3; FxSim.Line is
    scheduled. The category priorities come from FxSim's category.DBF (category 1 is
    priority 3, 2 is 2, 3 is 1)."""
    d = fx_install.project("FxSim")
    m = spc.ProjectModel(d)
    kinds = [a["kind"] for a in m.alarms.values()]
    order = [k for k, _ in spc.ALARM_TABLES]
    assert kinds == sorted(kinds, key=order.index)
    assert kinds == ["digital"] * 4 + ["analog"] * 3 + ["advanced"]
    a = m.alarms["fx_da_2"]
    assert (a["kind"], a["equipment"], a["item"], a["category"], a["priority"]) == (
        "digital",
        "FxSim.Line",
        "Alarm",
        2,
        2,
    )
    assert m.alarms["fx_aa_1"]["kind"] == "analog" and m.alarms["fx_aa_1"]["item"] == "LevelAlarm"
    assert m.alarms["fx_adv_1"]["priority"] == 1
    assert m.alarms["fx_da_1"]["priority"] == 3
    assert m.equipment["fxsim.line"]["SCHEDULED"].upper() == "TRUE"
    assert m.equipment["fxsim.unit2"]["SCHEDULED"].upper() == "FALSE"


# --------------------------------------------------------------------------- captures
class _FakeSnap:
    """A Snapshotter of nothing: every read is the same (no tag moves)."""

    def read_full(self, names=None):
        return {}, {}


class _FakeWin:
    hwnd = 1
    number = 8


def _captures(spc, monkeypatch, tmp_path, images, **kw):
    """capture_frame over synthetic window pictures (``images``: one per capture taken, in
    order), the clock and the sleep faked: the frame's record."""
    from PIL import Image

    pics = [Image.new("RGB", (8, 4), c) for c in images]
    taken = []
    now = [100.0]

    def capture_client(hwnd):
        taken.append(now[0])
        return pics[min(len(taken) - 1, len(pics) - 1)]

    def sleep(s):
        now[0] += max(0.0, s)

    monkeypatch.setattr(spc, "capture_client", capture_client)
    monkeypatch.setattr(spc.time, "sleep", sleep)
    monkeypatch.setattr(spc.time, "time", lambda: now[0])
    fr = spc.capture_frame(None, _FakeWin(), _FakeSnap(), str(tmp_path), "f0", [], [], **kw)
    return fr, taken


def test_a_frame_takes_four_captures_250_ms_apart(spc, monkeypatch, tmp_path):
    white, grey = (255, 255, 255), (128, 128, 128)
    fr, taken = _captures(spc, monkeypatch, tmp_path, [white, white, grey, grey])
    assert len(taken) == 4 == fr["capturesTaken"]
    assert fr["captureGap"] == 0.25
    gaps = [round(b - a, 3) for a, b in zip(taken, taken[1:])]
    assert gaps == [0.25, 0.25, 0.25]
    # the first and the first that differs are kept (identical ones once)
    assert fr["captures"] == ["f0.png", "f0_c.png"] and fr["capture2"] == "f0_c.png"
    assert (tmp_path / "f0_c.png").exists() and not (tmp_path / "f0_b.png").exists()
    assert "capturesIdentical" not in fr
    names, steady = spc.frame_captures(fr)
    assert names == ["f0.png", "f0_c.png"] and not steady


def test_identical_captures_say_the_runtime_was_steady(spc, monkeypatch, tmp_path):
    fr, taken = _captures(spc, monkeypatch, tmp_path, [(9, 9, 9)] * 4)
    assert fr["capturesTaken"] == 4 and fr["capturesIdentical"] is True
    assert "captures" not in fr and "capture2" not in fr
    assert spc.frame_captures(fr) == (["f0.png"], True)


def test_the_capture_count_and_gap_are_options(spc, monkeypatch, tmp_path):
    monkeypatch.setattr(spc, "CAPTURE_COUNT", 2)
    monkeypatch.setattr(spc, "CAPTURE_GAP", 0.5)
    fr, taken = _captures(spc, monkeypatch, tmp_path, [(0, 0, 0), (1, 2, 3)])
    assert len(taken) == 2 and fr["captureGap"] == 0.5
    assert round(taken[1] - taken[0], 3) == 0.5


def test_older_frames_read_as_before(spc):
    # a 2026-10-02 frame: capture + capture2 0.53 s apart, nothing about the captures taken
    assert spc.frame_captures({"capture": "a.png", "capture2": "a_b.png"}) == (["a.png", "a_b.png"], False)
    assert spc.frame_captures({"capture": "a.png"}) == (["a.png"], False)
    assert spc.frame_captures(
        {
            "capture": "a.png",
            "captures": ["a.png", "a_b.png", "a_d.png"],
            "capture2": "a_b.png",
            "capturesTaken": 4,
        }
    ) == (["a.png", "a_b.png", "a_d.png"], False)


def test_the_client_area_off_the_screen_is_masked(spc):
    # the 1920-wide pages at WinNewAt x 10: client x 11 .. 1930 on a 1920 x 1080 desktop
    doc = {"window": {"client": [11, 11, 1920, 760]}}
    assert spc.offscreen_masks(doc) == [(1909, 0, 11, 760)]
    doc = {"window": {"client": [11, 11, 716, 216]}}
    assert spc.offscreen_masks(doc) == []
    doc = {"window": {"client": [-5, 1000, 100, 100], "screen": [0, 0, 2560, 1440]}}
    assert spc.offscreen_masks(doc) == [(0, 0, 5, 100)]
    doc = {"window": {"client": [0, 1000, 100, 100]}}
    assert spc.offscreen_masks(doc) == [(0, 80, 100, 20)]


def test_a_call_step_runs_in_the_page_window(spc):
    calls = []

    class Cap:
        def cicode(self, stmt, window=0):
            calls.append((stmt, window))
            return {"ok": True, "value": "0"}

    done = spc.apply_step(Cap(), {"call": ['PageSetStr("V", "1")']}, {}, 8)
    assert calls == [('PageSetStr("V", "1")', 8)]
    assert done == [{"call": 'PageSetStr("V", "1")', "window": 8, "result": {"ok": True, "value": "0"}}]
    spc.apply_step(Cap(), {"call": ["X()"], "window": 0}, {}, 8)
    assert calls[-1] == ("X()", 0)


def test_verify_names_every_kept_capture(spc, tmp_path):
    from PIL import Image

    root, d = _case(tmp_path, spc)
    Image.new("RGB", (40, 20)).save(d / "f0_c.png")
    doc = json.loads((d / "case.json").read_text(encoding="utf-8"))
    doc["frames"][0].update(captures=["f0.png", "f0_c.png"], capture2="f0_c.png", capturesTaken=4)
    (d / "case.json").write_text(json.dumps(doc), encoding="utf-8")
    assert spc.verify_cases(str(root))["problems"] == []
