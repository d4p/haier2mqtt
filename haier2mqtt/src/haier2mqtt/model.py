"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnitState(str, Enum):
    STANDBY = "standby"
    CIRCULATE = "circulate"
    HEAT = "heat"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Raw:
    """Raw register blocks: core 101-106, status 141-156, mode 201, advanced 241-262 (optional)."""

    core: tuple[int, ...]
    status: tuple[int, ...]
    mode: tuple[int, ...]
    advanced: tuple[int, ...] | None


@dataclass(frozen=True)
class Reading:
    """Decoded unit data."""

    state_low: int
    ch_target: float
    twi: float | None
    two: float | None
    tank: float | None
    tao: float | None
    pump_running: bool
    heater_on: bool
    defrost: bool
    hw_antifreeze: bool
    active_error: int
    last_error: int | None
    error_archive: tuple[int, ...] | None
    performance: str | None
    comp_freq: int | None
    comp_current: float | None
    comp_temp: float | None
    fan_rpm: float | None
    eev: int | None
    anomalies: tuple[str, ...] = ()

    @property
    def power_on(self) -> bool:
        return bool(self.state_low & 0x01)

    @property
    def pump_forced(self) -> bool:
        return bool(self.state_low & 0x20)

    @property
    def heat_only_on(self) -> bool:
        """On and heating heat-only: power + heat bits, cool and tank bits clear (pump bit ignored)."""
        return (self.state_low & 0x87) == 0x05

    @property
    def unit_state(self) -> UnitState:
        if self.power_on:
            return UnitState.HEAT
        if self.pump_forced:
            return UnitState.CIRCULATE
        return UnitState.STANDBY

    @property
    def water_min(self) -> float | None:
        """Lowest outdoor-unit water temperature; None unless both Twi and Two are known."""
        if self.twi is None or self.two is None:
            return None
        return min(self.twi, self.two)
