import json

import pytest

from haier2mqtt.config import load_settings
from haier2mqtt.curve import Curve, CurveError, parse_points
from haier2mqtt.store import Store, curve_from_dict, curve_to_dict

OPTIONS = {
    "gateway_host": "192.168.8.209", "gateway_port": 8899, "slave_id": 17, "poll_interval_s": 10,
    "modbus_timeout_s": 5, "writes_enabled": False,
    "cmd_power": 1, "cmd_mode": 134, "cmd_pump": 32, "cmd_ch_temp": 4,
    "curve_points": "-20:40, 0:32, 10:29, 20:28", "curve_min_water": 25, "curve_max_water": 45,
    "min_heat_on_min": 20, "min_heat_off_min": 10, "heartbeat_timeout_min": 10, "forecast_stale_min": 30,
    "curve_min_interval_min": 20, "antifreeze_outdoor_start": 3, "antifreeze_water_circulate": 5,
    "antifreeze_water_heat": 3, "antifreeze_water_exit": 10, "antifreeze_exit_hold_min": 5,
    "antifreeze_periodic_on_min": 5, "antifreeze_periodic_every_min": 30, "antifreeze_circulate_check_min": 10,
    "log_level": "info",
}
ENV = {"MQTT_HOST": "core-mosquitto", "MQTT_PORT": "1883", "MQTT_USER": "addons", "MQTT_PASSWORD": "secret"}


def write(tmp_path, **override):
    p = tmp_path / "options.json"
    p.write_text(json.dumps({**OPTIONS, **override}))
    return p


def test_load_settings_converts_units(tmp_path):
    s = load_settings(write(tmp_path), ENV, tmp_path)
    assert (s.host, s.port, s.slave, s.poll_s) == ("192.168.8.209", 8899, 17, 10)
    assert s.commands.mode == 0x86 and s.commands.ch_temp == 0x04
    assert s.control.min_on_s == 1200 and s.control.heartbeat_timeout_s == 600 and not s.control.writes_enabled
    assert s.antifreeze.outdoor_start == 3 and s.antifreeze.periodic_every_s == 1800
    assert s.initial_curve.points[0] == (-20.0, 40.0)
    assert (s.mqtt.host, s.mqtt.port, s.mqtt.username) == ("core-mosquitto", 1883, "addons")


def test_load_settings_rejects_invalid_curve(tmp_path):
    with pytest.raises(CurveError):
        load_settings(write(tmp_path, curve_points="10:29, 0:32"), ENV, tmp_path)


def test_store_roundtrip_and_corrupt_file(tmp_path):
    store = Store(tmp_path / "state.json")
    assert store.load() == {}
    store.save({"mode": "auto"})
    assert store.load() == {"mode": "auto"}
    (tmp_path / "state.json").write_text("{not json")
    assert store.load() == {}


def test_curve_dict_roundtrip_and_fallback():
    c = Curve(parse_points("-10:38, 15:28"), 26, 44, 1.5)
    fallback = Curve(parse_points("-20:40, 20:28"))
    assert curve_from_dict(curve_to_dict(c), fallback) == c
    assert curve_from_dict({"points": "broken"}, fallback) == fallback
    assert curve_from_dict({}, fallback) == fallback


def test_store_load_non_dict_json_returns_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("[]")
    assert Store(p).load() == {}
