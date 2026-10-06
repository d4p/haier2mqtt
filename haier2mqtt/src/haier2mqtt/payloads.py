"""MQTT payloads: incoming commands, outgoing state and HA discovery (Polish names)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace

from .codec import PERFORMANCE
from .controller import MODES, Inputs, Status
from .curve import Curve, CurveError, format_points, parse_points, validate
from .problems import PROBLEM_IDS, PROBLEM_NAMES

BASE = "haier2mqtt"
AVAIL_SERVICE = {"topic": f"{BASE}/availability", "payload_available": "online", "payload_not_available": "offline"}
AVAIL_BUS = {"topic": f"{BASE}/bus", "payload_available": "reachable", "payload_not_available": "unreachable"}
DEVICE = {"identifiers": ["haier2mqtt"], "name": "Pompa ciepła Haier", "manufacturer": "Haier",
          "model": "AU162FYCRA (haier2mqtt)"}


@dataclass(frozen=True)
class CommandResult:
    persist: bool = False
    error: str | None = None
    curve_changed: bool = False


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("not finite")
    return value


def apply_command(suffix: str, payload: str, inputs: Inputs, initial_curve: Curve, now: float) -> CommandResult:
    try:
        if suffix == "demand":
            value = json.loads(payload)["demand"]
            if not isinstance(value, bool):
                raise ValueError("demand must be true/false")
            inputs.demand, inputs.demand_at = value, now
            return CommandResult()
        if suffix == "outdoor_forecast":
            value = json.loads(payload)["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("value must be a number")
            inputs.forecast, inputs.forecast_at = float(value), now
            return CommandResult()
        if suffix == "mode":
            if payload not in MODES:
                raise ValueError(f"unknown mode {payload!r}")
            inputs.mode = payload
            return CommandResult(persist=True)
        if suffix == "performance":
            if payload not in PERFORMANCE:
                raise ValueError(f"unknown performance {payload!r}")
            inputs.performance_request = payload
            return CommandResult()
        if suffix in ("curve_offset", "curve_points", "curve_limits", "curve_reset"):
            return _curve_command(suffix, payload, inputs, initial_curve)
        raise ValueError(f"unknown command {suffix!r}")
    except CurveError as exc:
        return CommandResult(error=str(exc))
    except (ValueError, KeyError, TypeError) as exc:
        return CommandResult(error=f"niepoprawne polecenie {suffix}: {exc}")


def _curve_command(suffix: str, payload: str, inputs: Inputs, initial_curve: Curve) -> CommandResult:
    c = inputs.curve
    if suffix == "curve_offset":
        new = replace(c, offset=_number(payload))
    elif suffix == "curve_points":
        new = replace(c, points=parse_points(payload))
    elif suffix == "curve_limits":
        d = json.loads(payload)
        new = replace(c, min_water=_number(str(d.get("min", c.min_water))),
                      max_water=_number(str(d.get("max", c.max_water))))
    else:
        new = initial_curve
    inputs.curve = validate(new)
    return CommandResult(persist=True, curve_changed=True)


def problems_list(problems: dict[str, dict]) -> list[dict]:
    return [{"id": pid, "severity": p["severity"], "message": p["message"], "since": p["since"]}
            for pid, p in problems.items() if p["active"]]


def state_payload(st: Status, inputs: Inputs, problems: dict[str, dict], now: float) -> dict:
    r, d = st.reading, st.decision
    return {
        "twi": r.twi if r else None, "two": r.two if r else None, "tank": r.tank if r else None,
        "tao": r.tao if r else None, "ch_target": r.ch_target if r else None,
        "curve_target": st.curve_target, "comp_temp": r.comp_temp if r else None,
        "comp_freq": r.comp_freq if r else None, "comp_current": r.comp_current if r else None,
        "fan_rpm": r.fan_rpm if r else None, "eev": r.eev if r else None,
        "actual": r.unit_state.value if r else None,
        "desired": d.target.value if d else None,
        "effective": st.effective.value if st.effective else None,
        "reason": d.reason if d else None,
        "outdoor": d.outdoor if d else None, "outdoor_source": d.outdoor_source if d else None,
        "antifreeze_stage": st.antifreeze_stage,
        "pump_running": r.pump_running if r else None,
        "compressor_running": (r.comp_freq or 0) > 0 if r else None,
        "defrost": r.defrost if r else None, "hw_antifreeze": r.hw_antifreeze if r else None,
        "active_error": r.active_error if r else None, "last_error": r.last_error if r else None,
        "error_archive": ", ".join(str(e) for e in r.error_archive) if r and r.error_archive else None,
        "performance": r.performance if r else None,
        "last_write": st.last_write, "write_failed": st.write_failed,
        "shadow_writes": ", ".join(st.shadow_writes),
        "delayed_s": max(0, round(st.delayed_until - now)) if st.delayed_until is not None else 0,
        "bus_reachable": st.bus_reachable, "heartbeat_ok": st.heartbeat_ok,
        "mode": inputs.mode,
        "curve_points": format_points(inputs.curve.points), "curve_min": inputs.curve.min_water,
        "curve_max": inputs.curve.max_water, "curve_offset": inputs.curve.offset,
        "problem": any(p["active"] for p in problems.values()),
        "problems": problems,
    }


# (component, key, name, value key / template, extra)
_SENSORS = [
    ("woda_wlot", "Woda – wlot (Twi)", "twi", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
    ("woda_wylot", "Woda – wylot (Two)", "two", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
    ("zbiornik", "Temperatura zbiornika", "tank", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
    ("temp_zewnetrzna", "Temperatura zewnętrzna (Tao)", "tao", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
    ("temp_zadana", "Temperatura zadana CO", "ch_target", {"device_class": "temperature", "unit_of_measurement": "°C"}),
    ("temp_z_krzywej", "Temperatura z krzywej", "curve_target", {"device_class": "temperature", "unit_of_measurement": "°C"}),
    ("sprezarka_temp", "Sprężarka – temperatura", "comp_temp", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
    ("sprezarka_czestotliwosc", "Sprężarka – częstotliwość", "comp_freq", {"device_class": "frequency", "unit_of_measurement": "Hz", "state_class": "measurement"}),
    ("sprezarka_prad", "Sprężarka – prąd", "comp_current", {"device_class": "current", "unit_of_measurement": "A", "state_class": "measurement"}),
    ("wentylator", "Wentylator", "fan_rpm", {"unit_of_measurement": "rpm", "state_class": "measurement", "icon": "mdi:fan"}),
    ("eev", "Zawór EEV", "eev", {"state_class": "measurement", "icon": "mdi:valve"}),
    ("stan", "Stan pompy", "actual", {"device_class": "enum", "options": ["standby", "circulate", "heat"], "icon": "mdi:heat-pump"}),
    ("stan_docelowy", "Stan docelowy", "desired", {"device_class": "enum", "options": ["standby", "circulate", "heat"], "icon": "mdi:target"}),
    ("powod", "Powód decyzji", "reason", {"icon": "mdi:information-outline"}),
    ("antifreeze_stopien", "Ochrona przed zamarzaniem – stopień", "antifreeze_stage", {"icon": "mdi:snowflake-alert"}),
    ("zrodlo_temp_zewn", "Źródło temperatury zewnętrznej", "outdoor_source", {"icon": "mdi:thermometer"}),
    ("blad", "Aktywny błąd", "active_error", {"icon": "mdi:alert-circle-outline"}),
    ("ostatni_blad", "Ostatni błąd", "last_error", {"icon": "mdi:history"}),
    ("archiwum_bledow", "Archiwum błędów", "error_archive", {"icon": "mdi:history"}),
]
_UNIT_BINARY = [
    ("pompa_wewnetrzna", "Pompa obiegowa (wewnętrzna)", "pump_running", {"device_class": "running"}),
    ("sprezarka", "Sprężarka pracuje", "compressor_running", {"device_class": "running"}),
    ("odszranianie", "Odszranianie", "defrost", {"icon": "mdi:snowflake-melt"}),
    ("antifreeze_sprzetowy", "Sprzętowa ochrona przed zamarzaniem", "hw_antifreeze", {"icon": "mdi:snowflake-alert"}),
]


def _base(component: str, key: str, name: str) -> dict:
    return {"name": name, "unique_id": f"haier2mqtt_{key}", "default_entity_id": f"{component}.haier_{key}",
            "device": DEVICE}


def _value(field: str) -> str:
    return f"{{{{ value_json.{field} if value_json.{field} is not none else 'None' }}}}"


def discovery_messages(prefix: str = "homeassistant") -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    state = f"{BASE}/state"
    unit_avail = {"availability": [AVAIL_SERVICE, AVAIL_BUS], "availability_mode": "all", "expire_after": 180}

    def add(component: str, key: str, payload: dict) -> None:
        out.append((f"{prefix}/{component}/haier2mqtt/{key}/config", payload))

    for key, name, field, extra in _SENSORS:
        unit_dependent = field not in ("reason", "antifreeze_stage", "outdoor_source", "desired")
        add("sensor", key, {**_base("sensor", key, name), "state_topic": state, "value_template": _value(field),
                            **(unit_avail if unit_dependent else {"availability": [AVAIL_SERVICE], "expire_after": 180}),
                            **extra})
    add("sensor", "ostatni_zapis", {**_base("sensor", "ostatni_zapis", "Ostatni zapis"), "state_topic": state,
                                    "value_template": "{{ value_json.last_write }}", "availability": [AVAIL_SERVICE],
                                    "icon": "mdi:content-save-cog"})
    for key, name, field, extra in _UNIT_BINARY:
        add("binary_sensor", key, {**_base("binary_sensor", key, name), "state_topic": state,
                                   "value_template": f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}",
                                   **unit_avail, **extra})
    service_binary = [
        ("problem", "Problem z pompą ciepła", "problem", {"device_class": "problem"}),
        ("magistrala", "Komunikacja z pompą", "bus_reachable", {"device_class": "connectivity"}),
        ("heartbeat", "Sygnał z Home Assistant", "heartbeat_ok", {"device_class": "connectivity"}),
    ]
    for key, name, field, extra in service_binary:
        add("binary_sensor", key, {**_base("binary_sensor", key, name), "state_topic": state,
                                   "value_template": f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}",
                                   "availability": [AVAIL_SERVICE], "expire_after": 180, **extra})
    for pid in PROBLEM_IDS:
        key = f"problem_{pid}"
        add("binary_sensor", key, {**_base("binary_sensor", key, PROBLEM_NAMES[pid]), "state_topic": state,
                                   "value_template": f"{{{{ 'ON' if value_json.problems.{pid}.active else 'OFF' }}}}",
                                   "json_attributes_topic": state,
                                   "json_attributes_template": f"{{{{ value_json.problems.{pid} | tojson }}}}",
                                   "availability": [AVAIL_SERVICE], "device_class": "problem"})
    add("select", "tryb", {**_base("select", "tryb", "Tryb pracy"), "state_topic": state,
                           "value_template": "{{ value_json.mode }}", "command_topic": f"{BASE}/set/mode",
                           "options": list(MODES), "availability": [AVAIL_SERVICE], "icon": "mdi:cog"})
    unit_avail.pop("expire_after")          # selects/numbers/text do not accept expire_after
    add("select", "wydajnosc", {**_base("select", "wydajnosc", "Tryb wydajności"), "state_topic": state,
                                "value_template": "{{ value_json.performance }}",
                                "command_topic": f"{BASE}/set/performance", "options": list(PERFORMANCE),
                                **unit_avail, "icon": "mdi:speedometer"})
    add("number", "krzywa_przesuniecie", {**_base("number", "krzywa_przesuniecie", "Krzywa – przesunięcie"),
                                          "state_topic": state, "value_template": "{{ value_json.curve_offset }}",
                                          "command_topic": f"{BASE}/set/curve_offset", "min": -5, "max": 5,
                                          "step": 0.5, "unit_of_measurement": "°C", "mode": "box",
                                          "availability": [AVAIL_SERVICE]})
    for key, name, field in (("krzywa_min", "Krzywa – min. temperatura wody", "min"),
                             ("krzywa_max", "Krzywa – maks. temperatura wody", "max")):
        add("number", key, {**_base("number", key, name), "state_topic": state,
                            "value_template": f"{{{{ value_json.curve_{field} }}}}",
                            "command_topic": f"{BASE}/set/curve_limits",
                            "command_template": f'{{"{field}": {{{{ value }}}}}}',
                            "min": 20, "max": 55, "step": 1, "unit_of_measurement": "°C", "mode": "box",
                            "availability": [AVAIL_SERVICE]})
    add("text", "krzywa_punkty", {**_base("text", "krzywa_punkty", "Krzywa – punkty (zewn.:woda)"),
                                  "state_topic": state, "value_template": "{{ value_json.curve_points }}",
                                  "command_topic": f"{BASE}/set/curve_points", "max": 120,
                                  "availability": [AVAIL_SERVICE], "icon": "mdi:chart-line"})
    add("button", "krzywa_reset", {**_base("button", "krzywa_reset", "Krzywa – przywróć domyślną"),
                                   "command_topic": f"{BASE}/set/curve_reset", "payload_press": "reset",
                                   "availability": [AVAIL_SERVICE], "icon": "mdi:restore"})
    add("image", "krzywa", {**_base("image", "krzywa", "Krzywa grzewcza"), "image_topic": f"{BASE}/curve_svg",
                            "content_type": "image/svg+xml", "availability": [AVAIL_SERVICE]})
    return out
