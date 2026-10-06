from haier2mqtt.antifreeze import Antifreeze, AntifreezeConfig
from haier2mqtt.model import UnitState

CFG = AntifreezeConfig()


def run(af: Antifreeze, t: float, water, outdoor):
    af.update(t, water, outdoor)
    return af.stage, af.wants(t)


def test_warm_weather_does_nothing():
    af = Antifreeze(CFG)
    assert run(af, 0, 20.0, 10.0) == (0, None)


def test_periodic_circulation_below_plus_three_outside():
    af = Antifreeze(CFG)
    assert run(af, 0, 20.0, 2.9) == (1, UnitState.CIRCULATE)       # window starts immediately
    assert run(af, 299, 20.0, 2.9) == (1, UnitState.CIRCULATE)
    assert run(af, 301, 20.0, 2.9) == (1, None)                    # pause
    assert run(af, 1801, 20.0, 2.9) == (1, UnitState.CIRCULATE)    # next window
    assert run(af, 1900, 20.0, 3.5) == (0, None)                   # warmed up outside


def test_continuous_circulation_below_five_and_exit_after_hold():
    af = Antifreeze(CFG)
    assert run(af, 0, 4.5, 5.0) == (1, UnitState.CIRCULATE)
    assert run(af, 400, 6.0, 5.0) == (1, UnitState.CIRCULATE)      # still continuous (not > 10)
    assert run(af, 500, 10.5, 5.0) == (1, UnitState.CIRCULATE)     # warm, hold starts
    assert run(af, 799, 10.5, 5.0) == (1, UnitState.CIRCULATE)
    assert run(af, 801, 10.5, 5.0) == (0, None)                    # held > 10 °C for 5 min


def test_stage_two_when_water_below_three():
    af = Antifreeze(CFG)
    assert run(af, 0, 2.9, 5.0) == (2, UnitState.HEAT)


def test_stage_two_when_not_rising_after_ten_minutes():
    af = Antifreeze(CFG)
    run(af, 0, 4.5, 5.0)
    assert run(af, 599, 4.5, 5.0)[0] == 1
    assert run(af, 601, 4.4, 5.0) == (2, UnitState.HEAT)


def test_rising_water_stays_in_stage_one():
    af = Antifreeze(CFG)
    run(af, 0, 4.0, 5.0)
    assert run(af, 601, 4.6, 5.0)[0] == 1


def test_stage_two_when_pump_command_fails():
    af = Antifreeze(CFG)
    run(af, 0, 4.5, 5.0)
    af.pump_failed = True
    assert run(af, 10, 4.5, 5.0) == (2, UnitState.HEAT)


def test_stage_two_exit_resets_pump_failed_and_returns_to_zero():
    af = Antifreeze(CFG)
    run(af, 0, 2.0, 5.0)
    af.pump_failed = True
    run(af, 100, 11.0, 5.0)
    assert run(af, 401, 11.0, 5.0) == (0, None)
    assert af.pump_failed is False


def test_missing_water_with_cold_outdoor_circulates_periodically():
    af = Antifreeze(CFG)
    assert run(af, 0, None, 1.0) == (1, UnitState.CIRCULATE)


def test_unknown_everything_circulates_periodically():
    # Review Focus 3: no water, no Tao, no forecast -> treat as cold
    af = Antifreeze(CFG)
    assert run(af, 0, None, None) == (1, UnitState.CIRCULATE)
    assert run(af, 301, None, None) == (1, None)
