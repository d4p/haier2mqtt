"""Problems published to HA (one binary sensor each); messages are Polish."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from .controller import Status

PROBLEM_IDS = (
    "unit_error", "unit_error_history", "hw_antifreeze", "heater_on", "unexpected_state",
    "antifreeze_stage1", "antifreeze_stage2", "water_low", "write_failed", "state_mismatch",
    "heartbeat_lost", "curve_rejected",
)
PROBLEM_NAMES = {
    "unit_error": "Błąd pompy ciepła",
    "unit_error_history": "Nowy wpis w historii błędów",
    "hw_antifreeze": "Sprzętowa ochrona przed zamarzaniem",
    "heater_on": "Grzałka włączona przez pompę",
    "unexpected_state": "Nieoczekiwany stan pompy",
    "antifreeze_stage1": "Ochrona przed zamarzaniem – cyrkulacja",
    "antifreeze_stage2": "Ochrona przed zamarzaniem – grzanie awaryjne",
    "water_low": "Niska temperatura wody",
    "write_failed": "Polecenie nie zostało przyjęte",
    "state_mismatch": "Stan pompy niezgodny z oczekiwanym",
    "heartbeat_lost": "Brak sygnału z Home Assistant",
    "curve_rejected": "Odrzucona zmiana krzywej grzewczej",
}
# Polish descriptions of Haier error codes, keyed by code. Intentionally empty until codes are taken from the
# unit's service manual; messages fall back to "kod nieznany – sprawdź instrukcję".
ERROR_DESCRIPTIONS: dict[int, str] = {}
_UNIT_IDS = {"unit_error", "unit_error_history", "hw_antifreeze", "heater_on", "unexpected_state", "water_low"}


@dataclass(frozen=True)
class Problem:
    id: str
    severity: str
    message: str
    since: float


def _describe(code: int) -> str:
    return ERROR_DESCRIPTIONS.get(code, "kod nieznany – sprawdź instrukcję")


class ProblemTracker:
    def __init__(self, water_heat: float = 3.0, mismatch_alert_s: float = 900.0, heartbeat_timeout_s: float = 600.0,
                 hold_s: float = 900.0, wall: Callable[[], float] = time.time) -> None:
        self._water_heat = water_heat
        self._mismatch_alert_s = mismatch_alert_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._hold_s = hold_s
        self._wall = wall
        self._active: dict[str, Problem] = {}
        self._prev_errors: tuple | None = None
        self._history: tuple[float, str] | None = None     # (until, message)
        self._curve: tuple[float, str] | None = None       # (until, message)

    def note_curve_error(self, message: str, now: float) -> None:
        self._curve = (now + self._hold_s, message)

    def clear_curve_error(self) -> None:
        self._curve = None

    def update(self, st: Status, now: float, mode: str, started_at: float) -> dict[str, Problem]:
        wanted: dict[str, tuple[str, str]] = {}
        r = st.reading
        if r is None:
            for pid in _UNIT_IDS & self._active.keys():
                p = self._active[pid]
                wanted[pid] = (p.severity, p.message)
        else:
            water = r.water_min
            cold = water is not None and water < self._water_heat
            if r.active_error:
                wanted["unit_error"] = ("warning", f"Pompa zgłasza błąd {r.active_error}: {_describe(r.active_error)}")
            errors = (r.last_error, r.error_archive)
            if self._prev_errors is not None and errors != self._prev_errors and r.last_error:
                self._history = (now + self._hold_s,
                                 f"Wystąpił błąd {r.last_error}: {_describe(r.last_error)} (już nieaktywny)")
            self._prev_errors = errors
            if self._history and now < self._history[0]:
                wanted["unit_error_history"] = ("warning", self._history[1])
            if r.hw_antifreeze:
                wanted["hw_antifreeze"] = ("critical" if cold else "warning",
                                           "Pompa włączyła własną ochronę przed zamarzaniem")
            if r.heater_on:
                wanted["heater_on"] = ("warning", "Pompa włączyła grzałkę elektryczną")
            if r.anomalies:
                raw = " ".join(f"{v:04X}" for v in st.raw.core) if st.raw else "-"
                wanted["unexpected_state"] = ("warning", "Nieoczekiwany stan pompy: " + "; ".join(r.anomalies)
                                              + f" (rejestry 101–106: {raw})")
            if cold:
                wanted["water_low"] = ("critical", f"Temperatura wody w jednostce zewnętrznej {water:g} °C")
        if st.antifreeze_stage == 1:
            wanted["antifreeze_stage1"] = ("warning", "Ochrona przed zamarzaniem: cyrkulacja wody")
        if st.antifreeze_stage == 2:
            wanted["antifreeze_stage2"] = ("critical", "Ochrona przed zamarzaniem: grzanie awaryjne 30 °C")
        if st.write_failed:
            wanted["write_failed"] = ("warning", f"Pompa nie przyjęła polecenia ({st.last_write})")
        if st.mismatch_since is not None and now - st.mismatch_since >= self._mismatch_alert_s:
            actual = r.unit_state.value if r else "?"
            wanted["state_mismatch"] = ("warning", (f"Pompa jest w stanie {actual}, oczekiwano "
                                                    f"{st.effective.value if st.effective else '?'} od ponad 15 min"))
        if mode == "auto" and not st.heartbeat_ok and now - started_at >= self._heartbeat_timeout_s:
            wanted["heartbeat_lost"] = ("warning", "Brak zapotrzebowania z Home Assistant – działa tryb awaryjny")
        if self._curve and now < self._curve[0]:
            wanted["curve_rejected"] = ("warning", f"Odrzucono zmianę krzywej: {self._curve[1]}")

        new: dict[str, Problem] = {}
        for pid, (severity, message) in wanted.items():
            old = self._active.get(pid)
            since = old.since if old is not None else self._wall()
            new[pid] = Problem(pid, severity, message, since)
        self._active = new
        return dict(new)

    def snapshot(self) -> dict[str, dict]:
        out = {}
        for pid in PROBLEM_IDS:
            p = self._active.get(pid)
            out[pid] = ({"active": True, **{k: v for k, v in asdict(p).items() if k != "id"}} if p
                        else {"active": False, "severity": None, "message": None, "since": None})
        return out
