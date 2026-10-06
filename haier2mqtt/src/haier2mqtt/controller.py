"""Desired-state decision and reconciliation with the unit."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .antifreeze import Antifreeze
from .bus import BusError
from .codec import (
    Commands,
    Write,
    ch_temp_matches,
    decode,
    encode_ch_temp,
    encode_performance,
    plan_state_writes,
    verify,
)
from .curve import Curve, choose_outdoor
from .curve import target as curve_target
from .model import Raw, Reading, UnitState

_LOG = logging.getLogger(__name__)

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


@dataclass
class Status:
    raw: Raw | None = None
    reading: Reading | None = None
    decision: Decision | None = None
    effective: UnitState | None = None
    delayed_until: float | None = None
    antifreeze_stage: int = 0
    bus_reachable: bool = False
    heartbeat_ok: bool = False
    write_failed: bool = False
    last_write: str = "brak"
    mismatch_since: float | None = None
    shadow_writes: tuple[str, ...] = field(default_factory=tuple)
    last_curve_write: float | None = None
    curve_target: float | None = None


class Controller:
    def __init__(self, bus, cmds: Commands, cfg: ControlConfig, antifreeze: Antifreeze, inputs: Inputs,
                 last_auto: UnitState = UnitState.STANDBY, startup_settled: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._bus = bus
        self.cmds = cmds
        self.cfg = cfg
        self.af = antifreeze
        self.inputs = inputs
        self.last_auto = last_auto
        self._clock = clock
        self._sleep = sleep
        self._heat_since = -math.inf
        self._off_since = -math.inf
        self._startup_settled = startup_settled
        self._prev_actual: UnitState | None = None
        self._next_retry = -math.inf
        self._next_ch_retry = -math.inf
        self._next_perf_retry = -math.inf
        self._failed = {"state": False, "ch": False, "performance": False}
        self.status = Status()

    async def cycle(self) -> Status:
        now = self._clock()
        st = self.status
        st.heartbeat_ok = heartbeat_ok(now, self.inputs, self.cfg)
        st.shadow_writes = ()
        raw = await self._bus.read_raw()
        st.bus_reachable = raw is not None
        st.raw = raw
        if raw is None:
            st.reading = None
            return st
        try:
            reading = decode(raw)
        except ValueError as exc:
            _LOG.warning("decode failed: %s", exc)
            st.reading = None
            return st
        st.reading = reading
        actual = reading.unit_state
        # Min-on timing tracks only genuine heat-only operation; a stray on-state (cool/tank) counts as
        # not heating (UNKNOWN) so it is never held and gets corrected immediately.
        heating = actual is UnitState.HEAT and reading.heat_only_on
        self._track_actual(now, actual if heating or actual is not UnitState.HEAT else UnitState.UNKNOWN)

        forecast_fresh = (self.inputs.forecast_at is not None
                          and now - self.inputs.forecast_at <= self.cfg.forecast_stale_s)
        af_outdoor = reading.tao if reading.tao is not None else (self.inputs.forecast if forecast_fresh else None)
        st.antifreeze_stage = self.af.update(now, reading.water_min, af_outdoor)

        dec = decide(now, self.inputs, reading.tao, self.af.wants(now), self.last_auto, self.cfg)
        if dec.reason == "zapotrzebowanie":
            self.last_auto = dec.target
        st.decision = dec
        st.curve_target = (curve_target(self.inputs.curve, dec.outdoor) if dec.outdoor is not None else None)
        effective, delayed_until = self._gate(now, dec, actual, heating)
        st.effective, st.delayed_until = effective, delayed_until

        urgent = dec.reason == "antifreeze" and effective is UnitState.HEAT   # emergency heat ignores backoff
        await self._reconcile_ch(now, dec, effective, reading, urgent)
        await self._reconcile_state(now, effective, urgent)
        await self._apply_performance(now)
        st.write_failed = any(self._failed.values())

        if actual is not effective:
            st.mismatch_since = st.mismatch_since if st.mismatch_since is not None else now
        else:
            st.mismatch_since = None
        return st

    def _track_actual(self, now: float, actual: UnitState) -> None:
        prev = self._prev_actual
        if prev is None and not self._startup_settled:
            if actual is UnitState.HEAT:
                self._heat_since = now
            else:
                self._off_since = now
        if prev is not None and prev is not actual:
            if actual is UnitState.HEAT:
                self._heat_since = now
            elif prev is UnitState.HEAT:
                self._off_since = now
        self._prev_actual = actual

    def _gate(self, now: float, dec: Decision, actual: UnitState,
              heating: bool) -> tuple[UnitState, float | None]:
        t = dec.target
        if heating and t is not UnitState.HEAT:
            until = self._heat_since + self.cfg.min_on_s
            if now < until:
                return UnitState.HEAT, until
        if actual is not UnitState.HEAT and t is UnitState.HEAT and dec.reason != "antifreeze":
            until = self._off_since + self.cfg.min_off_s
            if now < until:
                if self.af.wants(now) is UnitState.CIRCULATE:
                    return UnitState.CIRCULATE, until
                return actual, until
        return t, None

    async def _reconcile_state(self, now: float, effective: UnitState, urgent: bool = False) -> None:
        st = self.status
        planner = lambda core: plan_state_writes(core, effective, self.cmds)
        pending = planner(list(st.raw.core))
        if not pending:
            self._failed["state"] = False
            return
        if not self.cfg.writes_enabled:
            st.shadow_writes = st.shadow_writes + tuple(f"{w.what}:0x{w.values[0]:04X}" for w in pending)
            return
        if now < self._next_retry and not urgent:
            return
        if await self._execute(planner):
            self._failed["state"] = False
            st.last_write = f"ok: {effective.value}"
            if effective is UnitState.CIRCULATE:
                self.af.pump_failed = False
        else:
            self._failed["state"] = True
            st.last_write = f"błąd: {effective.value}"
            self._next_retry = now + self.cfg.retry_backoff_s
            if effective is UnitState.CIRCULATE:
                self.af.pump_failed = True

    async def _reconcile_ch(self, now: float, dec: Decision, effective: UnitState, reading: Reading,
                            urgent: bool = False) -> None:
        st = self.status
        t = dec.ch_target
        if effective is not UnitState.HEAT or t is None or ch_temp_matches(list(st.raw.core), t):
            self._failed["ch"] = False
            return
        entering = reading.unit_state is not UnitState.HEAT
        emergency = dec.reason == "antifreeze"
        last = st.last_curve_write
        due = (last is None or now - last >= self.cfg.curve_min_interval_s) and \
            abs(reading.ch_target - t) >= self.cfg.curve_min_delta
        if not (entering or emergency or due):
            return
        if not self.cfg.writes_enabled:
            st.shadow_writes = st.shadow_writes + (f"ch_temp:{t:g}",)
            return
        if now < self._next_ch_retry and not urgent:
            return
        try:
            encode_ch_temp(list(st.raw.core), t, self.cmds)
        except ValueError as exc:
            _LOG.error("CH write not sent: %s", exc)
            self._failed["ch"] = True
            st.last_write = f"błąd: temperatura {t:g} °C"
            self._next_ch_retry = now + self.cfg.retry_backoff_s
            return
        planner = lambda core: [] if ch_temp_matches(core, t) else [encode_ch_temp(core, t, self.cmds)]
        if await self._execute(planner):
            self._failed["ch"] = False
            st.last_curve_write = now
            st.last_write = f"ok: temperatura {t:g} °C"
        else:
            self._failed["ch"] = True
            st.last_write = f"błąd: temperatura {t:g} °C"
            self._next_ch_retry = now + self.cfg.retry_backoff_s

    async def _apply_performance(self, now: float) -> None:
        name = self.inputs.performance_request
        if name is None:
            self._failed["performance"] = False
            return
        if not self.cfg.writes_enabled:
            self.inputs.performance_request = None
            self.status.shadow_writes = self.status.shadow_writes + (f"performance:{name}",)
            return
        if now < self._next_perf_retry:
            return
        write = encode_performance(name)
        ok = await self._execute(lambda core: [write], verify_mode=True)
        self.status.last_write = f"{'ok' if ok else 'błąd'}: wydajność {name}"
        if ok:
            if self.inputs.performance_request == name:     # a newer request during the write stays pending
                self.inputs.performance_request = None
            self._failed["performance"] = False
        else:
            self._failed["performance"] = True
            self._next_perf_retry = now + self.cfg.retry_backoff_s

    async def _execute(self, planner: Callable[[list[int]], list[Write]], verify_mode: bool = False) -> bool:
        """Run planned writes one at a time with fresh reads and read-back checks; retry the whole plan."""
        for attempt in range(1, self.cfg.write_retries + 1):
            try:
                async with self._bus.transaction() as tx:
                    core = list(await tx.read_core())
                    for _step in range(4):
                        writes = planner(core)
                        if not writes:
                            return True
                        w = writes[0]
                        await tx.write(w.address, w.values)
                        await self._sleep(self.cfg.verify_delay_s)
                        core_after = list(await tx.read_core())
                        mode_after = list(await tx.read_mode()) if w.check == "mode0_low" else None
                        if not verify(w, core_after, mode_after):
                            seen = {"core0_low": core_after[0], "core1_high": core_after[1],
                                    "mode0_low": mode_after[0] if mode_after else None}[w.check]
                            _LOG.warning("write %s rejected (attempt %d): wrote 0x%04X, read %s (%s)",
                                         w.what, attempt, w.values[0],
                                         "?" if seen is None else f"0x{seen:04X}", w.check)
                            break
                        _LOG.info("write %s ok: 0x%04X", w.what, w.values[0])
                        core = core_after
                        if verify_mode:
                            return True
            except BusError as exc:
                _LOG.warning("write attempt %d failed: %s", attempt, exc)
        return False
