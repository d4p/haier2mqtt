"""Antifreeze: circulate warm tank water through the outdoor unit, escalate to heating."""

from __future__ import annotations

from dataclasses import dataclass

from .model import UnitState


@dataclass(frozen=True)
class AntifreezeConfig:
    outdoor_start: float = 3.0
    water_circulate: float = 5.0
    water_heat: float = 3.0
    water_exit: float = 10.0
    exit_hold_s: float = 300.0
    periodic_on_s: float = 300.0
    periodic_every_s: float = 1800.0
    circulate_check_s: float = 600.0


class Antifreeze:
    def __init__(self, cfg: AntifreezeConfig) -> None:
        self.cfg = cfg
        self.stage = 0
        self.pump_failed = False
        self._continuous = False
        self._continuous_since: float | None = None
        self._water_at_start: float | None = None
        self._periodic_anchor: float | None = None
        self._warm_since: float | None = None

    def update(self, now: float, water: float | None, outdoor: float | None) -> int:
        c = self.cfg
        if water is not None and water > c.water_exit:
            if self._warm_since is None:
                self._warm_since = now
        else:
            self._warm_since = None
        warm_long = self._warm_since is not None and now - self._warm_since >= c.exit_hold_s

        if self.stage == 2:
            if not warm_long:
                return 2
            self.stage = 0
            self.pump_failed = False
            self._stop_continuous()

        if water is not None and water < c.water_circulate and not self._continuous:
            self._continuous = True
            self._continuous_since = now
            self._water_at_start = water
        elif self._continuous and warm_long:
            self._stop_continuous()

        # Unknown outdoor AND unknown water: cannot assess -> treat as cold (safe default).
        cold = (outdoor is not None and outdoor < c.outdoor_start) or (outdoor is None and water is None)
        if cold and self._periodic_anchor is None:
            self._periodic_anchor = now
        elif not cold:
            self._periodic_anchor = None

        not_rising = (
            self._continuous
            and self._continuous_since is not None
            and now - self._continuous_since >= c.circulate_check_s
            and water is not None
            and self._water_at_start is not None
            and water <= self._water_at_start
        )
        wants_circulation = self._continuous or cold
        if (water is not None and water < c.water_heat) or not_rising or (self.pump_failed and wants_circulation):
            self.stage = 2
        elif wants_circulation:
            self.stage = 1
        else:
            self.stage = 0
            self.pump_failed = False
        return self.stage

    def wants(self, now: float) -> UnitState | None:
        if self.stage == 2:
            return UnitState.HEAT
        if self.stage == 1:
            if self._continuous:
                return UnitState.CIRCULATE
            if self._periodic_anchor is not None and (now - self._periodic_anchor) % self.cfg.periodic_every_s < self.cfg.periodic_on_s:
                return UnitState.CIRCULATE
        return None

    def _stop_continuous(self) -> None:
        self._continuous = False
        self._continuous_since = None
        self._water_at_start = None
