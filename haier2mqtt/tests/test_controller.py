from haier2mqtt.antifreeze import Antifreeze, AntifreezeConfig
from haier2mqtt.codec import Commands
from haier2mqtt.controller import ControlConfig, Controller, Inputs
from haier2mqtt.curve import Curve, parse_points
from haier2mqtt.model import UnitState
from tests.fakes import FakeBus, FakeClock
from tests.helpers import advanced, core, status

CURVE = Curve(parse_points("-20:40, 0:32, 10:29, 20:28"))
LIVE = ControlConfig(writes_enabled=True, verify_delay_s=0)


async def nosleep(_s):
    return None


def make(state_low=0x04, ch=29.5, cfg=LIVE, tao=13.8, twi=15.0, two=15.5, accept=None, last_auto=UnitState.STANDBY,
         startup_settled=True, accept_mode=None):
    bus = FakeBus(core(state_low=state_low, ch=ch), status(twi=twi, two=two), advanced=advanced(tao=tao), accept=accept,
                  accept_mode=accept_mode)
    clock = FakeClock(10_000)
    inputs = Inputs(curve=CURVE)
    ctrl = Controller(bus, Commands(), cfg, Antifreeze(AntifreezeConfig()), inputs, last_auto=last_auto,
                      startup_settled=startup_settled, clock=clock, sleep=nosleep)
    return ctrl, bus, clock, inputs


def demand(inputs, clock, on: bool):
    inputs.demand, inputs.demand_at = on, clock()


async def test_demand_on_turns_heat_on_and_sets_curve_first():
    ctrl, bus, clock, inputs = make(tao=10.0)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT
    kinds = [(a, v[0] >> 8) for a, v in bus.writes]
    assert kinds == [(101, 0x04), (101, 0x01)]          # CH temp (29 °C from Tao 10) before power on
    assert bus.core[0] & 0xFF == 0x05
    assert bus.core[1] >> 8 == 58


async def test_reported_tank_state_is_corrected_without_power_toggle():
    ctrl, bus, clock, inputs = make(state_low=0x84)
    demand(inputs, clock, False)
    await ctrl.cycle()
    assert [v[0] for _, v in bus.writes] == [0x8604]
    assert bus.core[0] == 0xDD04


async def test_min_on_time_delays_not_drops():
    ctrl, bus, clock, inputs = make()
    demand(inputs, clock, True)
    await ctrl.cycle()                                   # standby -> heat
    await ctrl.cycle()                                   # observe HEAT, heat_since recorded
    bus.writes.clear()
    clock.advance(60)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT and st.delayed_until is not None and bus.writes == []
    clock.advance(1200)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.STANDBY and bus.core[0] & 0xFF == 0x04


async def test_min_off_time_delays_heat():
    ctrl, _bus, clock, inputs = make(state_low=0x05)
    demand(inputs, clock, False)
    await ctrl.cycle()                                   # heat -> standby (startup: no min-on history)
    await ctrl.cycle()                                   # observe STANDBY, off_since recorded
    clock.advance(30)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.STANDBY and st.delayed_until is not None
    clock.advance(600)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT


async def test_rejected_write_is_retried_then_backs_off_and_flags():
    ctrl, bus, clock, inputs = make(accept=lambda cmd, low: False, ch=28.5)   # CH already on the curve
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.write_failed and "błąd" in st.last_write
    power_writes = [v for _, v in bus.writes if v[0] >> 8 == 0x01]
    assert len(power_writes) == 3                        # write_retries
    bus.writes.clear()
    clock.advance(60)
    demand(inputs, clock, True)
    await ctrl.cycle()
    assert bus.writes == []                              # backing off
    clock.advance(300)
    demand(inputs, clock, True)
    await ctrl.cycle()
    assert bus.writes != []                              # retried after backoff


async def test_shadow_mode_never_writes():
    ctrl, bus, clock, inputs = make(cfg=ControlConfig(writes_enabled=False))
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert bus.writes == []
    assert any(s.startswith("power:") for s in st.shadow_writes)


async def test_bus_down_reports_unreachable_and_writes_nothing():
    ctrl, bus, _clock, _inputs = make()
    bus.fail_read = True
    st = await ctrl.cycle()
    assert not st.bus_reachable and st.reading is None and bus.writes == []


async def test_antifreeze_stage2_heats_at_emergency_temperature_bypassing_min_off():
    ctrl, bus, clock, inputs = make(state_low=0x05, twi=2.5, two=2.8, tao=-8.0)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.decision.reason == "antifreeze" and st.effective is UnitState.HEAT
    assert bus.core[1] >> 8 == 60                         # 30 °C


async def test_antifreeze_circulation_failure_escalates():
    ctrl, bus, clock, inputs = make(twi=4.5, two=4.8, tao=1.0, accept=lambda cmd, low: cmd != 0x20)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.write_failed and ctrl.af.pump_failed
    clock.advance(10)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.antifreeze_stage == 2 and st.effective is UnitState.HEAT
    assert bus.core[0] & 0xFF == 0x05 and bus.core[1] >> 8 == 60   # emergency heat despite the pump-write backoff


async def test_curve_rewrite_respects_interval_and_delta():
    ctrl, bus, clock, inputs = make(state_low=0x05, ch=29.0, tao=10.0)
    demand(inputs, clock, True)
    await ctrl.cycle()
    assert bus.writes == []                              # already 29 °C, heating
    inputs.forecast, inputs.forecast_at = 0.0, clock()   # target 32 °C
    await ctrl.cycle()
    assert bus.core[1] >> 8 == 64                        # first curve write allowed (no previous write)
    bus.writes.clear()
    clock.advance(60)
    inputs.forecast, inputs.forecast_at = -10.0, clock()
    demand(inputs, clock, True)
    await ctrl.cycle()
    assert bus.writes == []                              # < 20 min since last curve write
    clock.advance(1200)
    inputs.forecast_at = clock()
    demand(inputs, clock, True)
    await ctrl.cycle()
    assert bus.core[1] >> 8 == 72                        # 36 °C


async def test_performance_request_is_applied_once():
    ctrl, bus, _clock, inputs = make()
    inputs.performance_request = "quiet"
    await ctrl.cycle()
    assert bus.mode == [1] and inputs.performance_request is None


async def test_mismatch_since_tracks_actual_vs_effective():
    ctrl, _bus, clock, inputs = make(accept=lambda cmd, low: False)
    t0 = clock()
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.mismatch_since == t0
    clock.advance(60)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.mismatch_since == t0


async def test_mismatch_since_clears_after_successful_write():
    ctrl, _bus, clock, inputs = make()
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.mismatch_since is None or st.mismatch_since == clock()
    clock.advance(10)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.mismatch_since is None


async def test_ch_write_failure_does_not_block_heating():
    ctrl, bus, clock, inputs = make(tao=10.0, ch=25.0, accept=lambda cmd, low: cmd != 0x04)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert bus.core[0] & 0xFF == 0x05
    assert st.write_failed is True


async def test_ch_failure_keeps_write_failed_while_heating():
    ctrl, _bus, clock, inputs = make(state_low=0x05, tao=10.0, ch=25.0, accept=lambda cmd, low: cmd != 0x04)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.write_failed is True
    clock.advance(60)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.write_failed is True


async def test_failed_performance_is_retried_after_backoff():
    accepting = {"on": False}
    ctrl, bus, clock, inputs = make(accept_mode=lambda v: accepting["on"])
    inputs.performance_request = "quiet"
    st = await ctrl.cycle()
    assert inputs.performance_request == "quiet" and st.write_failed is True
    n = len(bus.writes)
    clock.advance(60)
    await ctrl.cycle()
    assert len(bus.writes) == n                          # backing off
    clock.advance(300)
    accepting["on"] = True
    st = await ctrl.cycle()
    assert inputs.performance_request is None and st.write_failed is False
    assert bus.mode == [1]


async def test_restart_while_heating_holds_min_on():
    ctrl, _bus, clock, inputs = make(state_low=0x05, startup_settled=False)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT and st.delayed_until is not None
    clock.advance(1200)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.STANDBY


async def test_restart_in_standby_delays_heat_by_min_off():
    ctrl, _bus, clock, inputs = make(state_low=0x04, startup_settled=False)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.STANDBY and st.delayed_until is not None
    clock.advance(600)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT


async def test_antifreeze_circulation_continues_while_heat_waits_for_min_off():
    ctrl, bus, clock, inputs = make(state_low=0x05)
    demand(inputs, clock, False)
    await ctrl.cycle()                                   # -> standby
    await ctrl.cycle()                                   # observe STANDBY
    bus.status = status(twi=4.5, two=4.8)
    bus.advanced = advanced(tao=1.0)
    clock.advance(30)
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.effective is UnitState.CIRCULATE and st.delayed_until is not None


async def test_restart_stray_on_states_are_corrected_immediately():
    for stray in (0x03, 0x85):                           # on+cool, on+heat+tank
        ctrl, bus, clock, inputs = make(state_low=stray, startup_settled=False)
        demand(inputs, clock, False)
        st = await ctrl.cycle()
        assert st.effective is UnitState.STANDBY and st.delayed_until is None, hex(stray)
        assert [v[0] >> 8 for _, v in bus.writes] == [0x86, 0x01], hex(stray)   # mode, then power off
        assert bus.core[0] & 0xFF == 0x04, hex(stray)


async def test_restart_heat_only_with_pump_bit_is_still_held():
    ctrl, bus, clock, inputs = make(state_low=0x25, startup_settled=False)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.HEAT and st.delayed_until is not None
    assert bus.core[0] & 0x01                            # still on (only the stray pump bit is cleared)


async def test_stray_on_state_while_running_is_not_held():
    ctrl, bus, clock, inputs = make(state_low=0x04)
    demand(inputs, clock, False)
    await ctrl.cycle()
    bus.core[0] = 0xDD83                                 # unit switched itself on in tank mode
    clock.advance(10)
    demand(inputs, clock, False)
    st = await ctrl.cycle()
    assert st.effective is UnitState.STANDBY and st.delayed_until is None
    assert bus.core[0] & 0xFF == 0x04


async def test_unencodable_ch_target_is_a_failed_ch_write_not_a_crash():
    ctrl, bus, clock, inputs = make(tao=10.0)
    inputs.curve = Curve(parse_points("-20:58, 20:58"), 25, 58)    # 58 °C is outside the 20-55 °C guard
    demand(inputs, clock, True)
    st = await ctrl.cycle()
    assert st.write_failed is True and st.last_write.startswith("ok: heat")
    assert all(v[0] >> 8 != 0x04 for _, v in bus.writes)            # no CH write sent
    assert bus.core[0] & 0xFF == 0x05                               # heating still started


async def test_performance_request_changed_during_write_survives():
    ctrl, bus, _clock, inputs = make()
    inputs.performance_request = "quiet"
    real_write = bus.write

    async def write_and_change(address, values):
        await real_write(address, values)
        inputs.performance_request = "turbo"                        # newer request arrives mid-write

    bus.write = write_and_change
    await ctrl.cycle()
    assert bus.mode == [1] and inputs.performance_request == "turbo"
