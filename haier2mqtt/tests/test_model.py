from haier2mqtt.model import Reading, UnitState


def make(state_low: int, twi=15.0, two=16.0) -> Reading:
    return Reading(
        state_low=state_low, ch_target=30.0, twi=twi, two=two, tank=50.0, tao=5.0,
        pump_running=False, heater_on=False, defrost=False, hw_antifreeze=False,
        active_error=0, last_error=0, error_archive=(0, 0, 0), performance="eco",
        comp_freq=0, comp_current=0.0, comp_temp=17.0, fan_rpm=0.0, eev=100,
    )


def test_unit_state_from_low_byte():
    assert make(0x04).unit_state is UnitState.STANDBY
    assert make(0x84).unit_state is UnitState.STANDBY      # tank bit does not change the classification
    assert make(0x24).unit_state is UnitState.CIRCULATE
    assert make(0x05).unit_state is UnitState.HEAT
    assert make(0x25).unit_state is UnitState.HEAT         # power wins over forced pump


def test_water_min_requires_both_readings():
    assert make(0x04, twi=4.0, two=6.0).water_min == 4.0
    assert make(0x04, twi=None, two=6.0).water_min is None
