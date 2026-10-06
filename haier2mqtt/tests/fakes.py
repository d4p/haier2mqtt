"""Test doubles."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from haier2mqtt.bus import BusError
from haier2mqtt.model import Raw


class FakeModbusClient:
    """Mimics pymodbus AsyncModbusTcpClient (3.8 API) over a register dict."""

    def __init__(self, regs: dict[int, int], connect_ok: bool = True, fail_reads: set[int] | None = None,
                 short_reads: set[int] | None = None, raise_reads: set[int] | None = None) -> None:
        self.regs = regs
        self.connect_ok = connect_ok
        self.fail_reads = fail_reads or set()
        self.short_reads = short_reads or set()
        self.raise_reads = raise_reads or set()
        self.connected = False
        self.writes: list[tuple[int, list[int]]] = []
        self.closed = 0

    async def connect(self) -> bool:
        self.connected = self.connect_ok
        return self.connect_ok

    def close(self) -> None:
        self.connected = False
        self.closed += 1

    async def read_holding_registers(self, address: int, *, count: int, slave: int):
        if address in self.raise_reads:
            raise TimeoutError("simulated timeout")
        if address in self.fail_reads:
            return SimpleNamespace(isError=lambda: True, registers=[])
        n = count - 1 if address in self.short_reads else count
        return SimpleNamespace(isError=lambda: False, registers=[self.regs.get(address + i, 0) for i in range(n)])

    async def write_registers(self, address: int, values: list[int], *, slave: int):
        self.writes.append((address, list(values)))
        for i, v in enumerate(values):
            self.regs[address + i] = v
        return SimpleNamespace(isError=lambda: False)


def unit_registers() -> dict[int, int]:
    regs = {101 + i: v for i, v in enumerate((0xDD84, 0x3B1E, 0x0000, 0xDD01, 0xDD5A, 0x5C1E))}
    regs.update({141 + i: 0 for i in range(16)})
    regs[201] = 0
    regs.update({241 + i: 0 for i in range(22)})
    return regs


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeBus:
    """Simulated unit: applies register-101 commands the way the real unit is assumed to (reports 0xDD high byte)."""

    def __init__(self, core, status, mode=(0,), advanced=None, accept=None, accept_mode=None) -> None:
        self.core = list(core)
        self.status = tuple(status)
        self.mode = [mode[0]]
        self.advanced = advanced
        self.accept = accept or (lambda cmd, low: True)
        self.accept_mode = accept_mode or (lambda value: True)
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.fail_read = False
        self.fail_writes = False
        self.reachable = True

    async def read_raw(self):
        if self.fail_read:
            self.reachable = False
            return None
        self.reachable = True
        return Raw(tuple(self.core), self.status, tuple(self.mode), self.advanced)

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def read_core(self):
        if self.fail_read:
            raise BusError("down")
        return tuple(self.core)

    async def read_mode(self):
        return tuple(self.mode)

    async def write(self, address, values):
        if self.fail_writes:
            raise BusError("write down")
        self.writes.append((address, tuple(values)))
        if address == 101:
            cmd, low = values[0] >> 8, values[0] & 0xFF
            if self.accept(cmd, low):
                if cmd == 0x04:
                    self.core[1] = values[1]
                else:
                    self.core[0] = 0xDD00 | low
        elif address == 201 and self.accept_mode(values[0] & 0xFF):
            self.mode = [values[0] & 0xFF]
