import argparse

from haier2mqtt.codec import Commands
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
