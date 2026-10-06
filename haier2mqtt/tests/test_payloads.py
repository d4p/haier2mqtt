import json

from haier2mqtt.controller import Inputs, Status
from haier2mqtt.curve import Curve, parse_points
from haier2mqtt.payloads import apply_command, discovery_messages, problems_list, state_payload
from haier2mqtt.problems import PROBLEM_IDS

INITIAL = Curve(parse_points("-20:40, 0:32, 10:29, 20:28"))


def inputs() -> Inputs:
    return Inputs(curve=INITIAL)


def test_demand_and_forecast():
    i = inputs()
    assert apply_command("demand", '{"demand": true}', i, INITIAL, 50).error is None
    assert (i.demand, i.demand_at) == (True, 50)
    apply_command("outdoor_forecast", '{"value": -3.5}', i, INITIAL, 60)
    assert (i.forecast, i.forecast_at) == (-3.5, 60)


def test_mode_performance_offset_points_limits_reset():
    i = inputs()
    assert apply_command("mode", "grzanie", i, INITIAL, 1).persist and i.mode == "grzanie"
    apply_command("performance", "turbo", i, INITIAL, 1)
    assert i.performance_request == "turbo"
    r = apply_command("curve_offset", "1.5", i, INITIAL, 1)
    assert r.persist and r.curve_changed and i.curve.offset == 1.5
    assert apply_command("curve_points", "-15:38, 15:28", i, INITIAL, 1).curve_changed
    assert i.curve.points == ((-15.0, 38.0), (15.0, 28.0))
    apply_command("curve_limits", '{"min": 26}', i, INITIAL, 1)
    assert (i.curve.min_water, i.curve.max_water) == (26.0, 45.0)
    apply_command("curve_reset", "reset", i, INITIAL, 1)
    assert i.curve == INITIAL


def test_malformed_commands_are_rejected():
    # Review Focus 5
    i = inputs()
    before = (i.mode, i.demand, i.curve)
    assert apply_command("demand", '{"demand": "on"}', i, INITIAL, 1).error
    assert apply_command("demand", "not json", i, INITIAL, 1).error
    assert apply_command("outdoor_forecast", '{"value": "x"}', i, INITIAL, 1).error
    assert apply_command("mode", "turbo", i, INITIAL, 1).error
    assert apply_command("performance", "boost", i, INITIAL, 1).error
    r = apply_command("curve_offset", "NaN", i, INITIAL, 1)
    assert r.error and r.curve_changed is False
    r = apply_command("curve_points", "10:29, 0:32", i, INITIAL, 1)
    assert "posortowane" in r.error
    assert apply_command("unknown_topic", "1", i, INITIAL, 1).error
    assert (i.mode, i.demand, i.curve) == before


def test_state_payload_shape():
    st = Status(bus_reachable=True, heartbeat_ok=True)
    snap = {pid: {"active": pid == "unit_error", "severity": "warning" if pid == "unit_error" else None,
                  "message": "x" if pid == "unit_error" else None, "since": None} for pid in PROBLEM_IDS}
    p = state_payload(st, inputs(), snap, 0)
    assert p["mode"] == "auto" and p["curve_points"] == "-20:40, 0:32, 10:29, 20:28"
    assert p["problem"] is True and p["actual"] is None and p["bus_reachable"] is True
    assert set(p["problems"]) == set(PROBLEM_IDS)
    json.dumps(p)                                          # must be serialisable
    assert problems_list(snap) == [{"id": "unit_error", "severity": "warning", "message": "x", "since": None}]


def test_discovery_covers_entities_and_problem_sensors():
    msgs = discovery_messages()
    ids = {payload["default_entity_id"] for _, payload in msgs}
    for expected in ("sensor.haier_woda_wlot", "sensor.haier_stan", "binary_sensor.haier_magistrala",
                     "select.haier_tryb", "text.haier_krzywa_punkty", "image.haier_krzywa",
                     "button.haier_krzywa_reset", "number.haier_krzywa_min"):
        assert expected in ids
    assert {f"binary_sensor.haier_problem_{pid}" for pid in PROBLEM_IDS} <= ids
    topics = [t for t, _ in msgs]
    assert len(topics) == len(set(topics))
    for topic, payload in msgs:
        assert topic.startswith("homeassistant/") and topic.endswith("/config")
        assert payload["device"]["identifiers"] == ["haier2mqtt"]
        assert payload["unique_id"].startswith("haier2mqtt_")
