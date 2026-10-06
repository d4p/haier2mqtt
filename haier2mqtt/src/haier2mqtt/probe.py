"""Supervised hardware probe. Run only with the user at the unit and ha_haier/haier2mqtt disconnected."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from .bus import BusError, HaierBus
from .codec import (
    PERFORMANCE,
    Commands,
    Write,
    ch_temp_matches,
    decode,
    encode_ch_temp,
    encode_performance,
    mask_like_pyhaier,
    plan_state_writes,
    verify,
)
from .model import UnitState


def byte_value(text: str) -> int:
    """Parse and validate a byte value (0-255) for argparse."""
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {text}") from exc
    if not 0 <= value <= 255:
        raise argparse.ArgumentTypeError(f"byte value out of range [0-255]: {value}")
    return value


DIFF_REGISTERS = (101, 102, 103, 104, 105, 106, 201)


def _hex(values) -> str:
    return " ".join(f"0x{v:04X}" for v in values)


async def _registers(tx) -> dict[int, int]:
    core = await tx.read_core()
    mode = await tx.read_mode()
    regs = {101 + i: v for i, v in enumerate(core)}
    regs[201] = mode[0]
    return regs


def _wire_values(w: Write, mask_104_105: bool) -> tuple[int, ...]:
    return mask_like_pyhaier(w.values) if mask_104_105 and w.address == 101 else w.values


async def _send(tx, w: Write, mask_104_105: bool,
                sleep: Callable[[float], Awaitable[None]]) -> tuple[list[int], list[int]]:
    """Write once, wait, read back; print a before/after diff of registers 101-106 and 201."""
    before = await _registers(tx)
    values = _wire_values(w, mask_104_105)
    print(f"  sending {w.what} to {w.address}: {_hex(values)}" + (" (--mask-104-105)" if values != w.values else ""))
    await tx.write(w.address, values)
    await sleep(1.0)
    after = await _registers(tx)
    print("  registers before -> after:")
    for addr in DIFF_REGISTERS:
        b, a = before.get(addr), after.get(addr)
        print(f"    {addr}: {'?' if b is None else f'0x{b:04X}'} -> {'?' if a is None else f'0x{a:04X}'}"
              f"{' *' if a != b else ''}")
    return [after[101 + i] for i in range(len(after) - 1)], [after[201]]


async def dump(bus, out: str | None) -> int:
    raw = await bus.read_raw()
    if raw is None:
        print("unit not reachable")
        return 1
    reading = decode(raw)
    data = {"raw": {"core": [f"0x{v:04X}" for v in raw.core], "status": list(raw.status), "mode": list(raw.mode),
                    "advanced": list(raw.advanced) if raw.advanced else None},
            "reading": {**asdict(reading), "unit_state": reading.unit_state.value}}
    text = json.dumps(data, indent=1, ensure_ascii=False)
    print(text)
    if out:
        with open(out, "w", encoding="utf-8") as f:  # noqa: ASYNC230
            f.write(text)
    return 0


async def run_set(bus, target: UnitState, cmds: Commands, confirm: Callable[[str], bool],
                  sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, mask_104_105: bool = False) -> int:
    async with bus.transaction() as tx:
        core = list(await tx.read_core())
        plan = plan_state_writes(core, target, cmds)
        print(f"current register 101: 0x{core[0]:04X} -> target {target.value}")
        if not plan:
            print("already in target state, nothing to write")
            return 0
        for w in plan:
            print(f"  planned {w.what}: write 0x{w.values[0]:04X}, expect low byte 0x{w.expected:02X}")
        if not confirm("Send these writes to the unit? [y/N] "):
            print("aborted, nothing written")
            return 2
        # Keep the confirmed write values to detect plan changes
        confirmed = [w.values[0] for w in plan]
        for i in range(len(confirmed)):
            plan = plan_state_writes(core, target, cmds)
            if not plan:
                break
            w = plan[0]
            # Check if plan changed since confirmation
            if w.values[0] != confirmed[i]:
                print("plan changed since confirmation (unit state moved) – aborting")
                return 1
            core, _mode = await _send(tx, w, mask_104_105, sleep)
            ok = verify(w, core, None)
            print(f"  {w.what}: wrote 0x{w.values[0]:04X}, read back 0x{core[0]:04X} -> {'OK' if ok else 'REJECTED'}")
            if not ok:
                return 1
        # Check that target was reached
        plan = plan_state_writes(core, target, cmds)
        if plan:
            print("target not reached")
            return 1
    for sleep_delta, label_delta in ((10, 10), (20, 30)):
        await sleep(sleep_delta)
        raw = await bus.read_raw()
        if raw is not None:
            r = decode(raw)
            print(f"  after +{label_delta}s: state={r.unit_state.value} pump={r.pump_running} comp={r.comp_freq} Hz "
                  f"Twi={r.twi} Two={r.two}")
        else:
            print(f"  after +{label_delta}s: unit not reachable")
    return 0


async def run_set_ch(bus, temp: float, cmds: Commands, confirm: Callable[[str], bool],
                     sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, mask_104_105: bool = False) -> int:
    async with bus.transaction() as tx:
        core = list(await tx.read_core())
        print(f"current CH target {(core[1] >> 8) / 2:g} °C (register 102: 0x{core[1]:04X}) -> {temp:g} °C")
        if ch_temp_matches(core, temp):
            print("already at target CH temperature, nothing to write")
            return 0
        try:
            w = encode_ch_temp(core, temp, cmds)
        except ValueError as exc:
            print(f"refused: {exc}")
            return 1
        print(f"  planned {w.what}: write 101-106 {_hex(_wire_values(w, mask_104_105))}, "
              f"expect register 102 high byte 0x{w.expected:02X}")
        if not confirm("Send this write to the unit? [y/N] "):
            print("aborted, nothing written")
            return 2
        if encode_ch_temp(list(await tx.read_core()), temp, cmds).values != w.values:
            print("plan changed since confirmation (unit state moved) – aborting")
            return 1
        core, _mode = await _send(tx, w, mask_104_105, sleep)
        ok = verify(w, core, None)
        print(f"  {w.what}: wrote 0x{w.values[1]:04X} to 102, read back 0x{core[1]:04X} -> {'OK' if ok else 'REJECTED'}")
        return 0 if ok else 1


async def run_set_performance(bus, name: str, confirm: Callable[[str], bool],
                              sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    w = encode_performance(name)
    async with bus.transaction() as tx:
        mode = list(await tx.read_mode())
        print(f"current register 201: 0x{mode[0]:04X} -> {name}")
        if (mode[0] & 0xFF) == w.expected:
            print("already in target performance mode, nothing to write")
            return 0
        print(f"  planned {w.what}: write 0x{w.values[0]:04X} to 201, expect low byte 0x{w.expected:02X}")
        if not confirm("Send this write to the unit? [y/N] "):
            print("aborted, nothing written")
            return 2
        _core, mode = await _send(tx, w, False, sleep)
        ok = verify(w, None, mode)
        print(f"  {w.what}: wrote 0x{w.values[0]:04X}, read back 0x{mode[0]:04X} -> {'OK' if ok else 'REJECTED'}")
        return 0 if ok else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m haier2mqtt.probe")
    p.add_argument("--host", default="192.168.8.209")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--slave", type=int, default=17)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--out")
    s = sub.add_parser("set")
    s.add_argument("target", choices=["standby", "circulate", "heat"])
    s.add_argument("--cmd-power", type=byte_value, default=0x01)
    s.add_argument("--cmd-mode", type=byte_value, default=0x86)
    s.add_argument("--cmd-pump", type=byte_value, default=0x20)
    s.add_argument("--mask-104-105", action="store_true",
                   help="send register 104 as value & 0x0F and 105 as value & 0xFF (PyHaier style)")
    s.add_argument("--yes", action="store_true")
    c = sub.add_parser("set-ch")
    c.add_argument("temp", type=float)
    c.add_argument("--cmd-ch-temp", type=byte_value, default=0x04)
    c.add_argument("--mask-104-105", action="store_true",
                   help="send register 104 as value & 0x0F and 105 as value & 0xFF (PyHaier style)")
    c.add_argument("--yes", action="store_true")
    f = sub.add_parser("set-performance")
    f.add_argument("mode", choices=list(PERFORMANCE))
    f.add_argument("--yes", action="store_true")
    a = p.parse_args()
    bus = HaierBus(a.host, a.port, a.slave)

    def confirm_prompt(q: str) -> bool:
        try:
            return input(q).strip().lower() == "y"
        except EOFError:
            return False

    async def go() -> int:
        try:
            if a.cmd == "dump":
                return await dump(bus, a.out)
            confirm = (lambda _q: True) if a.yes else confirm_prompt
            if a.cmd == "set-ch":
                return await run_set_ch(bus, a.temp, Commands(ch_temp=a.cmd_ch_temp), confirm=confirm,
                                        mask_104_105=a.mask_104_105)
            if a.cmd == "set-performance":
                return await run_set_performance(bus, a.mode, confirm=confirm)
            cmds = Commands(power=a.cmd_power, mode=a.cmd_mode, pump=a.cmd_pump)
            return await run_set(bus, UnitState(a.target), cmds, confirm=confirm, mask_104_105=a.mask_104_105)
        except BusError as exc:
            print(f"bus error: {exc}")
            return 1
        finally:
            await bus.close()

    sys.exit(asyncio.run(go()))


if __name__ == "__main__":
    main()
