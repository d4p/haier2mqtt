import argparse
from unittest.mock import patch

from haier2mqtt.codec import Commands
from haier2mqtt.codec import plan_state_writes as real_plan_state_writes
from haier2mqtt.model import UnitState
from haier2mqtt.probe import byte_value, run_set
from tests.fakes import FakeBus
from tests.helpers import core, status


async def nosleep(_s):
    return None


async def test_run_set_reaches_target_after_confirmation(capsys):
    bus = FakeBus(core(state_low=0x84), status())
    code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)
    assert code == 0 and bus.core[0] & 0xFF == 0x05
    out = capsys.readouterr().out
    assert "0x8604" in out and "0x0105" in out


async def test_run_set_aborts_without_confirmation():
    bus = FakeBus(core(state_low=0x04), status())
    assert await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: False, sleep=nosleep) == 2
    assert bus.writes == []


async def test_run_set_reports_rejection():
    bus = FakeBus(core(state_low=0x04), status(), accept=lambda cmd, low: False)
    assert await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep) == 1


async def test_run_set_multi_write_rejection_stops_after_first(capsys):
    # Start at 0x84, target HEAT (0x05), but reject all writes
    # Should attempt exactly 1 write and return 1
    bus = FakeBus(core(state_low=0x84), status(), accept=lambda cmd, low: False)
    code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)
    assert code == 1
    assert len(bus.writes) == 1
    out = capsys.readouterr().out
    assert "REJECTED" in out


async def test_run_set_stops_at_len_confirmed_writes(capsys):
    # Verify that run_set only attempts len(confirmed) writes max
    # Start with 0xC4 (requires 2 writes), but first write always rejected
    bus = FakeBus(core(state_low=0xC4), status(), accept=lambda cmd, low: False)
    code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)
    assert code == 1
    # Should have exactly 1 write attempted before returning on rejection
    assert len(bus.writes) == 1
    out = capsys.readouterr().out
    assert "REJECTED" in out


async def test_run_set_sleep_times_and_labels(capsys):
    # Verify sleep timings are correct: [1.0, 1.0, ..., 10, 20]
    # and labels are cumulative: "+10s" and "+30s"
    bus = FakeBus(core(state_low=0x84), status())
    sleeps = []

    async def tracking_sleep(s):
        sleeps.append(s)

    code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=tracking_sleep)
    assert code == 0

    # Should have sleeps: [1.0 (after mode write), 10 (polling), 20 (polling)]
    # Or if mode/power together: [1.0, 1.0, ..., 10, 20]
    assert sleeps[-2:] == [10, 20], f"Expected last two sleeps [10, 20], got {sleeps}"

    out = capsys.readouterr().out
    assert "after +10s:" in out
    assert "after +30s:" in out


async def test_run_set_aborts_if_plan_changes(capsys):
    # Monkeypatch plan_state_writes so 2nd call returns different plan
    # Start 0xC4 (needs 2 writes), but 2nd re-plan returns mode write instead of power
    # Should send exactly 1 write, return 1 with "plan changed" message
    bus = FakeBus(core(state_low=0xC4), status())

    call_count = [0]
    def patched_plan(core_list, target, cmds):
        call_count[0] += 1
        real_plan = real_plan_state_writes(core_list, target, cmds)
        # On 2nd iteration re-plan (call 3), return different plan than confirmed[1]
        if call_count[0] == 3 and real_plan:
            # confirmed[1] expects power write, return mode write instead
            from haier2mqtt.codec import Write
            different_write = Write("mode", 101, (0x8644, *real_plan[0].values[1:6]), "core0_low", 0x44)
            return [different_write]
        return real_plan

    with patch("haier2mqtt.probe.plan_state_writes", side_effect=patched_plan):
        code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)

    assert code == 1
    assert len(bus.writes) == 1  # Only first write sent before plan changed
    out = capsys.readouterr().out
    assert "plan changed" in out


async def test_run_set_returns_1_when_target_not_reached(capsys):
    # Monkeypatch plan_state_writes so final re-plan (after loop) returns non-empty
    # All writes in loop verify, but target not reached after loop
    # Should return 1 with "target not reached", and no extra write sent
    bus = FakeBus(core(state_low=0xC4), status())

    call_count = [0]
    def patched_plan(core_list, target, cmds):
        call_count[0] += 1
        real_plan = real_plan_state_writes(core_list, target, cmds)
        # On 4th call (after loop re-plan), return non-empty to trigger "target not reached"
        if call_count[0] == 4:
            from haier2mqtt.codec import Write
            # Return a dummy write to make final plan non-empty
            return [Write("power", 101, (0x0101, 0, 0, 0, 0, 0), "core0_low", 0x01)]
        return real_plan

    with patch("haier2mqtt.probe.plan_state_writes", side_effect=patched_plan):
        code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)

    assert code == 1
    # Should have 2 writes (the planned ones in loop), not 3
    assert len(bus.writes) == 2
    out = capsys.readouterr().out
    assert "target not reached" in out


def test_byte_value_valid():
    assert byte_value("0x86") == 0x86
    assert byte_value("134") == 134
    assert byte_value("255") == 255
    assert byte_value("0") == 0
    assert byte_value("0x00") == 0


def test_byte_value_invalid():
    import pytest
    with pytest.raises(argparse.ArgumentTypeError):
        byte_value("0x186")
    with pytest.raises(argparse.ArgumentTypeError):
        byte_value("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        byte_value("256")
    with pytest.raises(argparse.ArgumentTypeError):
        byte_value("abc")
