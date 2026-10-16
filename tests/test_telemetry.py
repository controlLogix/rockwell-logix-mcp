import json

import telemetry


POINTS = [
    {"name": "CHWS_Temp", "kind": "analog", "behavior": "sine", "lo": 40, "hi": 55, "unit": "degF"},
    {"name": "CDU_Flow", "kind": "analog", "behavior": "walk", "lo": 0, "hi": 300},
    {"name": "Const_SP", "kind": "analog", "behavior": "constant", "lo": 10, "hi": 20},
    {"name": "Pump1_Run", "kind": "bool", "behavior": "toggle", "period_s": 2},
]


def test_simulate_shape_and_count():
    data = telemetry.simulate(POINTS, samples=5, interval_s=1.0)
    assert len(data) == 5 * len(POINTS)
    rec = data[0]
    assert set(rec) == {"t", "name", "value", "unit", "kind"}


def test_simulate_analog_in_range():
    data = telemetry.simulate(POINTS, samples=50, interval_s=0.5)
    for s in data:
        if s["name"] == "CHWS_Temp":
            assert 40 <= s["value"] <= 55
        if s["name"] == "CDU_Flow":
            assert 0 <= s["value"] <= 300


def test_simulate_constant_is_midpoint():
    data = telemetry.simulate([p for p in POINTS if p["name"] == "Const_SP"], samples=3)
    assert {s["value"] for s in data} == {15.0}


def test_simulate_bool_toggle_alternates():
    pts = [{"name": "B", "kind": "bool", "behavior": "toggle", "period_s": 1}]
    vals = [s["value"] for s in telemetry.simulate(pts, samples=4, interval_s=1.0)]
    assert vals == [0, 1, 0, 1]
    assert all(v in (0, 1) for v in vals)


def test_simulate_time_steps_use_interval():
    data = telemetry.simulate([{"name": "X", "kind": "analog"}], samples=3, interval_s=2.0)
    assert [s["t"] for s in data] == [0.0, 2.0, 4.0]


def test_latest_values_keys():
    lv = telemetry.latest_values(POINTS, t=0.0)
    assert set(lv) == {p["name"] for p in POINTS}


def test_to_jsonl_writes_all_records(tmp_path):
    data = telemetry.simulate(POINTS, samples=3)
    path = tmp_path / "out.jsonl"
    telemetry.to_jsonl(data, str(path))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == len(data)
    assert json.loads(lines[0])["name"] == "CHWS_Temp"
