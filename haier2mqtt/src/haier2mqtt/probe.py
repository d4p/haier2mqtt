"""Supervised hardware probe. Run only with the user at the unit and ha_haier/haier2mqtt disconnected."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from .bus import BusError, HaierBus
from .codec import Commands, decode, plan_state_writes, verify
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
                  sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
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
            await tx.write(w.address, w.values)
            await sleep(1.0)
            core = list(await tx.read_core())
            ok = verify(w, core, None)
            print(f"  {w.what}: wrote 0x{w.values[0]:04X}, read back 0x{core[0]:04X} -> {'OK' if ok else 'REJECTED'}")
            if not ok:
                return 1
        # Check that target was reached
        plan = plan_state_writes(core, target, cmds)
        if plan:
            print("target not reached")
            return 1
    for delay in (10, 30):
        await sleep(delay)
        raw = await bus.read_raw()
        if raw is not None:
            r = decode(raw)
            print(f"  after +{delay}s: state={r.unit_state.value} pump={r.pump_running} comp={r.comp_freq} Hz "
                  f"Twi={r.twi} Two={r.two}")
        else:
            print(f"  after +{delay}s: unit not reachable")
    return 0


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
    s.add_argument("--yes", action="store_true")
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
            cmds = Commands(power=a.cmd_power, mode=a.cmd_mode, pump=a.cmd_pump)
            return await run_set(bus, UnitState(a.target), cmds,
                                 confirm=(lambda _q: True) if a.yes else confirm_prompt)
        except BusError as exc:
            print(f"bus error: {exc}")
            return 1
        finally:
            await bus.close()

    sys.exit(asyncio.run(go()))


if __name__ == "__main__":
    main()
