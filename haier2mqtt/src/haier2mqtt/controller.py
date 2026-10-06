"""Desired-state decision and reconciliation with the unit."""

from __future__ import annotations

from dataclasses import dataclass

from .curve import Curve, choose_outdoor
from .curve import target as curve_target
from .model import UnitState

MODES = ("auto", "wyłączona", "grzanie")
EMERGENCY_CH = 30.0


@dataclass(frozen=True)
class ControlConfig:
    min_on_s: float = 1200.0
    min_off_s: float = 600.0
    heartbeat_timeout_s: float = 600.0
    forecast_stale_s: float = 1800.0
    curve_min_interval_s: float = 1200.0
    curve_min_delta: float = 0.5
    write_retries: int = 3
    retry_backoff_s: float = 300.0
    mismatch_alert_s: float = 900.0
    verify_delay_s: float = 1.0
    writes_enabled: bool = False


@dataclass
class Inputs:
    curve: Curve
    mode: str = "auto"
    demand: bool | None = None
    demand_at: float | None = None
    forecast: float | None = None
    forecast_at: float | None = None
    performance_request: str | None = None


@dataclass(frozen=True)
class Decision:
    target: UnitState
    reason: str
    ch_target: float | None
    outdoor: float | None
    outdoor_source: str | None


def heartbeat_ok(now: float, inputs: Inputs, cfg: ControlConfig) -> bool:
    return inputs.demand_at is not None and now - inputs.demand_at <= cfg.heartbeat_timeout_s


def decide(now: float, inputs: Inputs, tao: float | None, af_wants: UnitState | None,
           last_auto: UnitState, cfg: ControlConfig) -> Decision:
    age = None if inputs.forecast_at is None else now - inputs.forecast_at
    outdoor, source = choose_outdoor(inputs.forecast, age, tao, cfg.forecast_stale_s)
    curve_ch = curve_target(inputs.curve, outdoor) if outdoor is not None else None

    if af_wants is UnitState.HEAT:
        return Decision(UnitState.HEAT, "antifreeze", EMERGENCY_CH, outdoor, source)

    if inputs.mode == "wyłączona":
        base, reason = UnitState.STANDBY, "tryb"
    elif inputs.mode == "grzanie":
        base, reason = UnitState.HEAT, "tryb"
    elif heartbeat_ok(now, inputs, cfg):
        base, reason = (UnitState.HEAT if inputs.demand else UnitState.STANDBY), "zapotrzebowanie"
    else:
        base, reason = last_auto, "fallback"

    if af_wants is UnitState.CIRCULATE and base is not UnitState.HEAT:
        return Decision(UnitState.CIRCULATE, "antifreeze", None, outdoor, source)
    return Decision(base, reason, curve_ch if base is UnitState.HEAT else None, outdoor, source)
