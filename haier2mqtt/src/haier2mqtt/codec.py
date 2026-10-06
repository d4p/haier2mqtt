"""Register decoding and explicit write encoding for the Haier unit.

Register 101: low byte = state bits, high byte = command code on write / 0xDD on read.
"""

from __future__ import annotations

import PyHaier

from .model import Raw, Reading

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
