import pytest

from haier2mqtt.codec import (
    Commands,
    Write,
    ch_temp_matches,
    encode_ch_temp,
    encode_performance,
    plan_state_writes,
    verify,
)
from haier2mqtt.model import UnitState
from tests.helpers import core

CMDS = Commands()


def lows(writes: list[Write]) -> list[tuple[str, int, int]]:
    return [(w.what, w.values[0] >> 8, w.values[0] & 0xFF) for w in writes]


def test_plan_standby_from_reported_tank_state():
    # Review Focus 1: unit reports 0xDD84 (heat+tank, off); STANDBY wanted -> one mode write, no power toggle
    writes = plan_state_writes(list(core(state_low=0x84)), UnitState.STANDBY, CMDS)
    assert lows(writes) == [("mode", 0x86, 0x04)]


def test_plan_heat_from_standby():
    assert lows(plan_state_writes(list(core(state_low=0x04)), UnitState.HEAT, CMDS)) == [("power", 0x01, 0x05)]


def test_plan_standby_from_heat():
    assert lows(plan_state_writes(list(core(state_low=0x05)), UnitState.STANDBY, CMDS)) == [("power", 0x01, 0x04)]


def test_plan_circulate_and_back():
    assert lows(plan_state_writes(list(core(state_low=0x04)), UnitState.CIRCULATE, CMDS)) == [("pump", 0x20, 0x24)]
    assert lows(plan_state_writes(list(core(state_low=0x24)), UnitState.STANDBY, CMDS)) == [("pump", 0x20, 0x04)]


def test_plan_heat_from_circulate_clears_pump_then_powers_on():
    assert lows(plan_state_writes(list(core(state_low=0x24)), UnitState.HEAT, CMDS)) == [
        ("pump", 0x20, 0x04), ("power", 0x01, 0x05)]


def test_plan_cooling_on_is_corrected_to_heat_only():
    assert lows(plan_state_writes(list(core(state_low=0x83)), UnitState.HEAT, CMDS)) == [("mode", 0x86, 0x05)]


def test_plan_is_idempotent_no_plus_minus_one():
    # The PyHaier bug: "off" on an already-off unit produced 0x83. Our planner must produce nothing.
    assert plan_state_writes(list(core(state_low=0x04)), UnitState.STANDBY, CMDS) == []
    assert plan_state_writes(list(core(state_low=0x05)), UnitState.HEAT, CMDS) == []


def test_state_writes_copy_registers_102_to_106_unchanged():
    c = list(core(state_low=0x04))
    (w,) = plan_state_writes(c, UnitState.HEAT, CMDS)
    assert w.address == 101
    assert w.values[1:] == tuple(c[1:])


def test_plan_rejects_unknown_target():
    with pytest.raises(ValueError):
        plan_state_writes(list(core()), UnitState.UNKNOWN, CMDS)


def test_custom_command_bytes():
    cmds = Commands(power=0x86)
    assert lows(plan_state_writes(list(core(state_low=0x04)), UnitState.HEAT, cmds)) == [("power", 0x86, 0x05)]


def test_encode_ch_temp_keeps_state_and_other_registers():
    c = list(core(state_low=0x05, ch=29.5))
    w = encode_ch_temp(c, 31.5, CMDS)
    assert w.values[0] == 0x04 << 8 | 0x05
    assert w.values[1] == 63 << 8 | 0x1E
    assert w.values[2:] == tuple(c[2:])
    assert (w.check, w.expected) == ("core1_high", 63)
    assert ch_temp_matches(c, 29.5) and not ch_temp_matches(c, 31.5)


def test_encode_performance():
    w = encode_performance("turbo")
    assert (w.address, w.values, w.check, w.expected) == (201, (0x102,), "mode0_low", 2)
    with pytest.raises(ValueError):
        encode_performance("boost")


def test_verify_ignores_command_high_byte():
    # Review Focus 2: written 0x0105, unit reports 0xDD05 -> success
    (w,) = plan_state_writes(list(core(state_low=0x04)), UnitState.HEAT, CMDS)
    assert verify(w, list(core(state_low=0x05, high=0xDD)), None)
    assert not verify(w, list(core(state_low=0x04, high=0xDD)), None)
    assert not verify(w, None, None)


def test_verify_ch_temp_and_performance():
    c = list(core(ch=29.5))
    w = encode_ch_temp(c, 31.0, CMDS)
    assert verify(w, list(core(ch=31.0)), None)
    assert not verify(w, list(core(ch=29.5)), None)
    p = encode_performance("quiet")
    assert verify(p, None, [0x0001]) and not verify(p, None, [0x0000])
