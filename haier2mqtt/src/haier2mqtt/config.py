"""Add-on options (/data/options.json) and MQTT settings (env from run.sh)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .antifreeze import AntifreezeConfig
from .codec import Commands
from .controller import ControlConfig
from .curve import Curve, parse_points, validate


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    username: str | None
    password: str | None


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    slave: int
    poll_s: float
    timeout_s: float
    commands: Commands
    control: ControlConfig
    antifreeze: AntifreezeConfig
    initial_curve: Curve
    mqtt: MqttSettings
    data_dir: Path
    log_level: str


def load_settings(options_path: Path, env: Mapping[str, str], data_dir: Path) -> Settings:
    o = json.loads(Path(options_path).read_text())
    m = 60.0
    curve = validate(Curve(parse_points(o["curve_points"]), float(o["curve_min_water"]), float(o["curve_max_water"])))
    return Settings(
        host=o["gateway_host"],
        port=int(o["gateway_port"]),
        slave=int(o["slave_id"]),
        poll_s=float(o["poll_interval_s"]),
        timeout_s=float(o["modbus_timeout_s"]),
        commands=Commands(power=int(o["cmd_power"]), mode=int(o["cmd_mode"]), pump=int(o["cmd_pump"]),
                          ch_temp=int(o["cmd_ch_temp"])),
        control=ControlConfig(
            min_on_s=o["min_heat_on_min"] * m, min_off_s=o["min_heat_off_min"] * m,
            heartbeat_timeout_s=o["heartbeat_timeout_min"] * m, forecast_stale_s=o["forecast_stale_min"] * m,
            curve_min_interval_s=o["curve_min_interval_min"] * m, writes_enabled=bool(o["writes_enabled"]),
        ),
        antifreeze=AntifreezeConfig(
            outdoor_start=float(o["antifreeze_outdoor_start"]),
            water_circulate=float(o["antifreeze_water_circulate"]),
            water_heat=float(o["antifreeze_water_heat"]),
            water_exit=float(o["antifreeze_water_exit"]),
            exit_hold_s=o["antifreeze_exit_hold_min"] * m,
            periodic_on_s=o["antifreeze_periodic_on_min"] * m,
            periodic_every_s=o["antifreeze_periodic_every_min"] * m,
            circulate_check_s=o["antifreeze_circulate_check_min"] * m,
        ),
        initial_curve=curve,
        mqtt=MqttSettings(env.get("MQTT_HOST", "core-mosquitto"), int(env.get("MQTT_PORT", "1883")),
                          env.get("MQTT_USER") or None, env.get("MQTT_PASSWORD") or None),
        data_dir=Path(data_dir),
        log_level=str(o.get("log_level", "info")),
    )
