import pytest

from haier2mqtt.codec import decode
from tests.helpers import advanced, core, raw, status


def test_decode_real_core_from_2026_10_06():
    # Core block read from the unit on 2026-10-06: 0xDD84 = heat+tank, off; CH 29.5 °C (0x3B high byte)
    r = decode(raw(core_regs=(0xDD84, 0x3B1E, 0x0000, 0xDD01, 0xDD5A, 0x5C1E)))
    assert r.state_low == 0x84
    assert r.ch_target == 29.5
    assert not r.power_on
    assert "bit zbiornika CWU ustawiony" in r.anomalies


def test_decode_status_and_advanced():
    r = decode(raw(
        core_regs=core(state_low=0x05, ch=32.0),
        status_regs=status(twi=15.4, two=15.9, tank=52.0, error=7, hw_antifreeze=True, defrost=True,
                           pump=True, heater=True),
        mode=(1,),
        advanced_regs=advanced(tao=-5.0, last_error=12, archive=(3, 4, 5), comp_set=40, comp_actual=38),
    ))
    assert (r.twi, r.two, r.tank) == (15.4, 15.9, 52.0)
    assert r.active_error == 7
    assert r.hw_antifreeze and r.defrost and r.pump_running and r.heater_on
    assert r.performance == "quiet"
    assert r.tao == -5.0
    assert r.last_error == 12
    assert r.error_archive == (3, 4, 5)
    assert r.comp_freq == 38
    assert r.anomalies == ()


def test_decode_without_advanced_block():
    r = decode(raw(with_advanced=False))
    assert r.tao is None and r.last_error is None and r.error_archive is None and r.comp_freq is None


def test_decode_rejects_bad_lengths():
    with pytest.raises(ValueError):
        decode(raw(core_regs=(0xDD04, 0x3B1E)))
    with pytest.raises(ValueError):
        decode(raw(status_regs=(0,) * 15))


def test_anomalies_cool_unknown_bits_and_ranges():
    r = decode(raw(core_regs=core(state_low=0x04 | 0x02 | 0x08, ch=10.0), mode=(9,),
                   advanced_regs=advanced(tao=70.0)))
    text = " | ".join(r.anomalies)
    assert "bit chłodzenia ustawiony" in text
    assert "nieznane bity stanu 0x08" in text
    assert "temperatura zadana poza zakresem" in text
    assert "Tao poza zakresem" in text
    assert "nieznany tryb wydajności 9" in text
