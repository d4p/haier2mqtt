from haier2mqtt.controller import EMERGENCY_CH, ControlConfig, Inputs, decide
from haier2mqtt.curve import Curve, parse_points
from haier2mqtt.model import UnitState

CFG = ControlConfig()
CURVE = Curve(parse_points("-20:40, 0:32, 10:29, 20:28"))
S, C, H = UnitState.STANDBY, UnitState.CIRCULATE, UnitState.HEAT


def inp(**kw) -> Inputs:
    return Inputs(curve=CURVE, **kw)


def test_demand_on_heats_with_curve_from_fresh_forecast():
    d = decide(1000, inp(demand=True, demand_at=990, forecast=5.0, forecast_at=900), 12.0, None, S, CFG)
    assert (d.target, d.reason, d.ch_target, d.outdoor_source) == (H, "zapotrzebowanie", 30.5, "prognoza")


def test_demand_off_standby():
    d = decide(1000, inp(demand=False, demand_at=990), 12.0, None, H, CFG)
    assert (d.target, d.reason, d.ch_target) == (S, "zapotrzebowanie", None)


def test_stale_forecast_falls_back_to_tao():
    d = decide(5000, inp(demand=True, demand_at=4990, forecast=5.0, forecast_at=0), 10.0, None, S, CFG)
    assert (d.ch_target, d.outdoor_source) == (29.0, "Tao")


def test_heartbeat_lost_keeps_last_auto_target():
    d = decide(2000, inp(demand=False, demand_at=0), 10.0, None, H, CFG)
    assert (d.target, d.reason) == (H, "fallback")
    assert d.ch_target == 29.0


def test_mode_overrides_demand():
    assert decide(10, inp(mode="wyłączona", demand=True, demand_at=10), 10.0, None, S, CFG).target is S
    d = decide(10, inp(mode="grzanie", demand=False, demand_at=10), 10.0, None, S, CFG)
    assert (d.target, d.reason) == (H, "tryb")


def test_antifreeze_heat_beats_everything():
    d = decide(10, inp(mode="wyłączona"), -5.0, H, S, CFG)
    assert (d.target, d.reason, d.ch_target) == (H, "antifreeze", EMERGENCY_CH)


def test_antifreeze_circulate_only_when_not_heating():
    assert decide(10, inp(demand=False, demand_at=10), 1.0, C, S, CFG).target is C
    d = decide(10, inp(demand=True, demand_at=10), 1.0, C, S, CFG)
    assert (d.target, d.reason) == (H, "zapotrzebowanie")


def test_heat_without_any_outdoor_temperature_keeps_unit_target():
    d = decide(10, inp(demand=True, demand_at=10), None, None, S, CFG)
    assert d.target is H and d.ch_target is None
