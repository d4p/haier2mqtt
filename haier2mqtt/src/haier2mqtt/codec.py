"""Register decoding and explicit write encoding for the Haier unit.

Register 101: low byte = state bits, high byte = command code on write / 0xDD on read.
"""

from __future__ import annotations

from dataclasses import dataclass

import PyHaier

from .model import Raw, Reading, UnitState

BIT_ON, BIT_COOL, BIT_HEAT, BIT_PUMP, BIT_TANK = 0x01, 0x02, 0x04, 0x20, 0x80
KNOWN_BITS = BIT_ON | BIT_COOL | BIT_HEAT | BIT_PUMP | BIT_TANK
CORE_LEN, STATUS_LEN, ADVANCED_LEN = 6, 16, 22
PERFORMANCE = ("eco", "quiet", "turbo")


def decode(raw: Raw) -> Reading:
    core, status = list(raw.core), list(raw.status)
    if len(core) != CORE_LEN or len(status) != STATUS_LEN:
        raise ValueError(f"bad block length: core={len(core)} status={len(status)}")
    adv = list(raw.advanced) if raw.advanced is not None and len(raw.advanced) == ADVANCED_LEN else None

    low = core[0] & 0xFF
    ch_target = (core[1] >> 8) / 2
    twi, two = PyHaier.GetTwiTwo(status)
    tao = PyHaier.GetTao(adv) if adv else None
    comp = PyHaier.GetCompInfo(adv) if adv else None
    perf_val = (raw.mode[0] & 0xFF) if raw.mode else None

    return Reading(
        state_low=low,
        ch_target=ch_target,
        twi=twi,
        two=two,
        tank=status[13] / 10,
        tao=tao,
        pump_running=bool(status[3] & 0x200),
        heater_on=bool(status[3] & 0x100),
        defrost=bool(status[0] & 0x2000),
        hw_antifreeze=bool(status[0] & 0x800),
        active_error=status[0] & 0xFF,
        last_error=(adv[0] & 0xFF) if adv else None,
        error_archive=tuple(PyHaier.GetArchError(adv)) if adv else None,
        performance=PERFORMANCE[perf_val] if perf_val is not None and perf_val < len(PERFORMANCE) else None,
        comp_freq=comp[1] if comp else None,
        comp_current=comp[2] if comp else None,
        comp_temp=comp[4] if comp else None,
        fan_rpm=PyHaier.GetFanRpm(adv)[0] if adv else None,
        eev=PyHaier.GetEEVLevel(adv) if adv else None,
        anomalies=_anomalies(low, ch_target, twi, two, tao, perf_val),
    )


def _anomalies(low: int, ch_target: float, twi, two, tao, perf_val) -> tuple[str, ...]:
    out: list[str] = []
    if low & BIT_COOL:
        out.append("bit chłodzenia ustawiony")
    if low & BIT_TANK:
        out.append("bit zbiornika CWU ustawiony")
    unknown = low & ~KNOWN_BITS & 0xFF
    if unknown:
        out.append(f"nieznane bity stanu 0x{unknown:02X}")
    if not 20 <= ch_target <= 60:
        out.append(f"temperatura zadana poza zakresem: {ch_target} °C")
    for name, value in (("Twi", twi), ("Two", two)):
        if value is not None and not -30 <= value <= 80:
            out.append(f"{name} poza zakresem: {value} °C")
    if tao is not None and not -40 <= tao <= 60:
        out.append(f"Tao poza zakresem: {tao} °C")
    if perf_val is not None and perf_val >= len(PERFORMANCE):
        out.append(f"nieznany tryb wydajności {perf_val}")
    return tuple(out)


@dataclass(frozen=True)
class Commands:
    """Register-101 high byte (command code) used for each kind of write."""

    power: int = 0x01
    mode: int = 0x86
    pump: int = 0x20
    ch_temp: int = 0x04


@dataclass(frozen=True)
class Write:
    what: str                 # "mode" | "pump" | "power" | "ch_temp" | "performance"
    address: int              # 101 or 201
    values: tuple[int, ...]
    check: str                # "core0_low" | "core1_high" | "mode0_low"
    expected: int


TARGET_LOW = {
    UnitState.STANDBY: BIT_HEAT,
    UnitState.CIRCULATE: BIT_HEAT | BIT_PUMP,
    UnitState.HEAT: BIT_HEAT | BIT_ON,
}
_MODE_BITS = BIT_HEAT | BIT_COOL | BIT_TANK


def _state_write(what: str, core: list[int], cmd: int, low: int) -> Write:
    return Write(what, 101, ((cmd & 0xFF) << 8 | low, *core[1:6]), "core0_low", low)


def plan_state_writes(core: list[int], target: UnitState, cmds: Commands) -> list[Write]:
    """Explicit writes (mode -> pump -> power) that bring register 101 to the heat-only target.

    Each write sets absolute bits computed from the current low byte; never `current ± 1`.
    """
    if target not in TARGET_LOW:
        raise ValueError(f"cannot plan writes for {target}")
    want = TARGET_LOW[target]
    cur = core[0] & 0xFF
    writes: list[Write] = []
    if (cur & _MODE_BITS) != BIT_HEAT:
        cur = (cur & ~_MODE_BITS & 0xFF) | BIT_HEAT
        writes.append(_state_write("mode", core, cmds.mode, cur))
    if (cur & BIT_PUMP) != (want & BIT_PUMP):
        cur = (cur & ~BIT_PUMP & 0xFF) | (want & BIT_PUMP)
        writes.append(_state_write("pump", core, cmds.pump, cur))
    if (cur & BIT_ON) != (want & BIT_ON):
        cur = (cur & ~BIT_ON & 0xFF) | (want & BIT_ON)
        writes.append(_state_write("power", core, cmds.power, cur))
    return writes


def _temp_byte(temp: float) -> int:
    return round(temp * 2)


def ch_temp_matches(core: list[int], temp: float) -> bool:
    return (core[1] >> 8) == _temp_byte(temp)


def encode_ch_temp(core: list[int], temp: float, cmds: Commands) -> Write:
    hb = _temp_byte(temp)
    values = ((cmds.ch_temp & 0xFF) << 8 | (core[0] & 0xFF), hb << 8 | (core[1] & 0xFF), *core[2:6])
    return Write("ch_temp", 101, values, "core1_high", hb)


def encode_performance(name: str) -> Write:
    if name not in PERFORMANCE:
        raise ValueError(f"unknown performance mode {name!r}")
    n = PERFORMANCE.index(name)
    return Write("performance", 201, (0x100 | n,), "mode0_low", n)


def verify(write: Write, core_after: list[int] | None, mode_after: list[int] | None) -> bool:
    """Check the read-back. Never compares the register-101 high byte (unit reports 0xDD)."""
    if write.check == "core0_low":
        return core_after is not None and (core_after[0] & 0xFF) == write.expected
    if write.check == "core1_high":
        return core_after is not None and (core_after[1] >> 8) == write.expected
    if write.check == "mode0_low":
        return mode_after is not None and (mode_after[0] & 0xFF) == write.expected
    raise ValueError(f"unknown check {write.check!r}")
