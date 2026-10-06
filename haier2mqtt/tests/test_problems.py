from dataclasses import replace

from haier2mqtt.controller import Status
from haier2mqtt.model import Reading
from haier2mqtt.problems import PROBLEM_IDS, ProblemTracker

BASE = Reading(state_low=0x04, ch_target=30.0, twi=15.0, two=16.0, tank=50.0, tao=5.0, pump_running=False,
               heater_on=False, defrost=False, hw_antifreeze=False, active_error=0, last_error=0,
               error_archive=(0, 0, 0), performance="eco", comp_freq=0, comp_current=0.0, comp_temp=17.0,
               fan_rpm=0.0, eev=100)


def st(reading=BASE, **kw) -> Status:
    return Status(reading=reading, bus_reachable=reading is not None, heartbeat_ok=True, **kw)


def tracker() -> ProblemTracker:
    return ProblemTracker(wall=lambda: 1_700_000_000.0)


def test_all_ids_reported_in_snapshot():
    t = tracker()
    t.update(st(), 100, "auto", 0)
    assert set(t.snapshot()) == set(PROBLEM_IDS) and not any(v["active"] for v in t.snapshot().values())


def test_unit_error_with_unknown_code_message():
    t = tracker()
    active = t.update(st(replace(BASE, active_error=17)), 100, "auto", 0)
    assert "kod nieznany – sprawdź instrukcję" in active["unit_error"].message and "17" in active["unit_error"].message


def test_error_history_detects_transient_errors_and_expires():
    t = tracker()
    t.update(st(), 100, "auto", 0)                                       # baseline, no alert
    active = t.update(st(replace(BASE, last_error=9, error_archive=(9, 0, 0))), 110, "auto", 0)
    assert "już nieaktywny" in active["unit_error_history"].message
    assert "unit_error_history" not in t.update(st(replace(BASE, last_error=9, error_archive=(9, 0, 0))), 1100, "auto", 0)


def test_water_low_and_hw_antifreeze_critical():
    t = tracker()
    active = t.update(st(replace(BASE, twi=2.5, hw_antifreeze=True)), 100, "auto", 0)
    assert active["water_low"].severity == "critical"
    assert active["hw_antifreeze"].severity == "critical"


def test_antifreeze_stage_severity():
    t = tracker()
    assert t.update(st(antifreeze_stage=1), 100, "auto", 0)["antifreeze_stage1"].severity == "warning"
    assert t.update(st(antifreeze_stage=2), 110, "auto", 0)["antifreeze_stage2"].severity == "critical"


def test_unexpected_state_lists_anomalies_and_raw():
    t = tracker()
    active = t.update(st(replace(BASE, anomalies=("bit chłodzenia ustawiony",))), 100, "auto", 0)
    assert "bit chłodzenia" in active["unexpected_state"].message


def test_mismatch_after_fifteen_minutes():
    t = tracker()
    assert "state_mismatch" not in t.update(st(mismatch_since=0.0), 899, "auto", 0)
    assert "state_mismatch" in t.update(st(mismatch_since=0.0), 901, "auto", 0)


def test_heartbeat_lost_only_in_auto_and_after_grace():
    t = tracker()
    s = st()
    s.heartbeat_ok = False
    assert "heartbeat_lost" not in t.update(s, 300, "auto", 0)            # grace after start
    assert "heartbeat_lost" in t.update(s, 700, "auto", 0)
    assert "heartbeat_lost" not in t.update(s, 710, "wyłączona", 0)


def test_unit_problems_frozen_while_bus_down():
    t = tracker()
    t.update(st(replace(BASE, active_error=5)), 100, "auto", 0)
    assert "unit_error" in t.update(st(None), 110, "auto", 0)


def test_curve_rejected_expires_or_clears():
    t = tracker()
    t.note_curve_error("punkty muszą być posortowane", 100)
    assert "curve_rejected" in t.update(st(), 100, "auto", 0)
    t.clear_curve_error()
    assert "curve_rejected" not in t.update(st(), 110, "auto", 0)
