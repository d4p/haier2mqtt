import json

from haier2mqtt.app import App
from haier2mqtt.config import load_settings
from haier2mqtt.model import UnitState
from tests.fakes import FakeBus, FakeClock
from tests.helpers import advanced, core, status
from tests.test_config_store import ENV, write


def make(tmp_path, state_low=0x04, **opts):
    settings = load_settings(write(tmp_path, **opts), ENV, tmp_path)
    bus = FakeBus(core(state_low=state_low), status(), advanced=advanced(tao=10.0))
    clock = FakeClock(1000)
    app = App(settings, bus=bus, clock=clock, wall=lambda: 1_700_000_000.0)
    return app, bus, clock


async def test_step_produces_state_and_persists_mode(tmp_path):
    app, _, _ = make(tmp_path)
    app.handle_command("mode", "wyłączona")
    state = await app.step()
    assert state["mode"] == "wyłączona" and state["desired"] == "standby"
    assert json.loads((tmp_path / "state.json").read_text())["mode"] == "wyłączona"


async def test_restore_keeps_heating_without_heartbeat(tmp_path):
    # Review Focus 4: last auto target "heat" persisted, service restarts, HA is down
    (tmp_path / "state.json").write_text(json.dumps({"mode": "auto", "last_auto": "heat"}))
    app, _, _ = make(tmp_path, state_low=0x05)
    state = await app.step()
    assert state["desired"] == "heat" and state["reason"] == "fallback"
    assert app.controller.last_auto is UnitState.HEAT


async def test_last_auto_is_persisted_when_demand_changes(tmp_path):
    app, _, _ = make(tmp_path)
    app.handle_command("demand", '{"demand": true}')
    await app.step()
    assert json.loads((tmp_path / "state.json").read_text())["last_auto"] == "heat"


async def test_invalid_curve_command_raises_problem(tmp_path):
    app, _, _ = make(tmp_path)
    app.handle_command("curve_points", "10:29, 0:32")
    state = await app.step()
    assert state["problems"]["curve_rejected"]["active"] is True


async def test_cycle_exception_does_not_kill_step(tmp_path):
    app, bus, _ = make(tmp_path)

    async def boom():
        raise RuntimeError("unexpected")

    bus.read_raw = boom
    state = await app.step()
    assert state["bus_reachable"] is False


async def test_save_failure_does_not_stop_step(tmp_path, monkeypatch):
    app, _, _ = make(tmp_path)

    def boom(_data):
        raise OSError("disk full")

    monkeypatch.setattr(app.store, "save", boom)
    app.handle_command("mode", "wyłączona")
    state = await app.step()
    assert state["mode"] == "wyłączona"


async def test_run_survives_iteration_exception(tmp_path, monkeypatch):
    import asyncio

    import haier2mqtt.app as app_mod

    app, _, _ = make(tmp_path)
    real_sleep = asyncio.sleep

    async def fast_sleep(_s):
        await real_sleep(0)

    monkeypatch.setattr(app_mod.asyncio, "sleep", fast_sleep)

    class Link:
        calls = 0

        def offer(self, *args):
            Link.calls += 1
            if Link.calls == 1:
                raise RuntimeError("offer failed")

        async def run(self):
            await asyncio.Event().wait()

    task = asyncio.create_task(app.run(Link()))
    for _ in range(50):
        await real_sleep(0.001)
        if Link.calls >= 3:
            break
    task.cancel()
    assert Link.calls >= 3


def test_restore_accepts_only_heat_or_standby_last_auto(tmp_path):
    for saved, expected in (("heat", UnitState.HEAT), ("standby", UnitState.STANDBY),
                            ("unknown", UnitState.STANDBY), ("circulate", UnitState.STANDBY),
                            ("bogus", UnitState.STANDBY), (None, UnitState.STANDBY)):
        (tmp_path / "state.json").write_text(json.dumps({"mode": "auto", "last_auto": saved}))
        app, _, _ = make(tmp_path)
        assert app.controller.last_auto is expected, saved
