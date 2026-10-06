"""Wiring: controller loop + MQTT. The control loop never waits for MQTT."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from .antifreeze import Antifreeze
from .bus import HaierBus
from .config import Settings
from .controller import MODES, Controller, Inputs, Status
from .curve import render_svg
from .model import UnitState
from .mqtt import MqttLink
from .payloads import apply_command, problems_list, state_payload
from .problems import ProblemTracker
from .store import Store, curve_from_dict, curve_to_dict

_LOG = logging.getLogger(__name__)
CYCLE_TIMEOUT_S = 60.0


class App:
    def __init__(self, settings: Settings, bus=None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._clock = clock
        self.store = Store(Path(settings.data_dir) / "state.json")
        saved = self.store.load()
        mode = saved.get("mode", "auto")
        self.inputs = Inputs(curve=curve_from_dict(saved.get("curve", {}), settings.initial_curve),
                             mode=mode if mode in MODES else "auto")
        try:
            last_auto = UnitState(saved.get("last_auto", "standby"))
        except ValueError:
            last_auto = UnitState.STANDBY
        self.bus = bus or HaierBus(settings.host, settings.port, settings.slave, timeout=settings.timeout_s)
        self.controller = Controller(self.bus, settings.commands, settings.control, Antifreeze(settings.antifreeze),
                                     self.inputs, last_auto=last_auto, clock=clock)
        self.tracker = ProblemTracker(water_heat=settings.antifreeze.water_heat,
                                      mismatch_alert_s=settings.control.mismatch_alert_s,
                                      heartbeat_timeout_s=settings.control.heartbeat_timeout_s, wall=wall)
        self._started = clock()
        self._persisted = self._persist_snapshot()

    def _persist_snapshot(self) -> dict:
        return {"mode": self.inputs.mode, "curve": curve_to_dict(self.inputs.curve),
                "last_auto": self.controller.last_auto.value}

    def _persist_if_changed(self) -> None:
        snap = self._persist_snapshot()
        if snap != self._persisted:
            try:
                self.store.save(snap)
            except OSError:
                _LOG.exception("could not persist state; will retry next cycle")
                return
            self._persisted = snap

    def handle_command(self, suffix: str, payload: str) -> None:
        now = self._clock()
        result = apply_command(suffix, payload, self.inputs, self.settings.initial_curve, now)
        if result.error:
            _LOG.warning("rejected command %s=%r: %s", suffix, payload, result.error)
            if suffix.startswith("curve_"):
                self.tracker.note_curve_error(result.error, now)
            return
        if result.curve_changed:
            self.tracker.clear_curve_error()
        if result.persist:
            self._persist_if_changed()

    async def step(self) -> dict:
        try:
            status = await asyncio.wait_for(self.controller.cycle(), timeout=CYCLE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - the loop must survive anything
            _LOG.exception("control cycle failed")
            status = self.controller.status
            status.bus_reachable = False
            status.reading = None
        now = self._clock()
        self.tracker.update(status, now, self.inputs.mode, self._started)
        self._persist_if_changed()
        return state_payload(status, self.inputs, self.tracker.snapshot(), now)

    def svg(self, status: Status) -> str:
        d = status.decision
        return render_svg(self.inputs.curve, d.outdoor if d else None, status.curve_target)

    @staticmethod
    def _link_done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("MQTT link task ended unexpectedly", exc_info=task.exception())

    async def run(self, link: MqttLink) -> None:
        link_task = asyncio.create_task(link.run())
        link_task.add_done_callback(self._link_done)
        try:
            while True:
                try:
                    state = await self.step()
                    link.offer(state, problems_list(state["problems"]), state["bus_reachable"],
                               self.svg(self.controller.status))
                except Exception:  # noqa: BLE001 - keep the control loop alive
                    _LOG.exception("loop iteration failed")
                await asyncio.sleep(self.settings.poll_s)
        finally:
            link_task.cancel()
