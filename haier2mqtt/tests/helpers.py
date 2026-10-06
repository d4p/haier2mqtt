"""Build raw Haier register blocks for tests (layouts as decoded by PyHaier 0.4.4)."""

from haier2mqtt.model import Raw


def core(state_low: int = 0x04, ch: float = 29.5, high: int = 0xDD) -> tuple[int, ...]:
    """Registers 101-106 as read from the unit (0xDD high byte on 101, 104, 105 like the real unit)."""
    return (high << 8 | state_low, round(ch * 2) << 8 | 0x1E, 0x0000, 0xDD01, 0xDD5A, 0x5C1E)


def status(
    twi: float | None = 15.4,
    two: float | None = 15.9,
    tank: float = 52.0,
    error: int = 0,
    hw_antifreeze: bool = False,
    defrost: bool = False,
    pump: bool = False,
    heater: bool = False,
) -> tuple[int, ...]:
    """Registers 141-156."""
    s = [0] * 16
    s[0] = error | (0x800 if hw_antifreeze else 0) | (0x2000 if defrost else 0)
    s[3] = (0x200 if pump else 0) | (0x100 if heater else 0)
    t1 = round((twi if twi is not None else 0) * 10)
    t2 = round((two if two is not None else 0) * 10)
    s[5] = ((t2 >> 8) & 0xF) << 4 | ((t1 >> 8) & 0xF)
    s[6] = (t1 & 0xFF) << 8 | (t2 & 0xFF)
    s[13] = round(tank * 10)
    return tuple(s)


def advanced(
    tao: float = 13.8,
    last_error: int = 0,
    archive: tuple[int, int, int] = (0, 0, 0),
    comp_set: int = 0,
    comp_actual: int = 0,
) -> tuple[int, ...]:
    """Registers 241-262."""
    a = [0] * 22
    a[0] = last_error
    a[2] = comp_actual & 0xFF
    a[3] = (comp_set & 0xFF) << 8
    t = round(tao * 10)
    if t < 0:
        t += 4095  # PyHaier GetTao subtracts 4095 for values > 2047
    a[12] = t & 0xFFF
    a[19] = archive[0] << 8 | archive[1]
    a[20] = archive[2] << 8
    return tuple(a)


def raw(core_regs=None, status_regs=None, mode=(0,), advanced_regs=None, with_advanced: bool = True) -> Raw:
    return Raw(
        core=core_regs if core_regs is not None else core(),
        status=status_regs if status_regs is not None else status(),
        mode=tuple(mode),
        advanced=(advanced_regs if advanced_regs is not None else advanced()) if with_advanced else None,
    )
