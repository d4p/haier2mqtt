from haier2mqtt.codec import Commands
from haier2mqtt.model import UnitState
from haier2mqtt.probe import run_set
from tests.fakes import FakeBus
from tests.helpers import core, status


async def nosleep(_s):
    return None


async def test_run_set_reaches_target_after_confirmation(capsys):
    bus = FakeBus(core(state_low=0x84), status())
    code = await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep)
    assert code == 0 and bus.core[0] & 0xFF == 0x05
    out = capsys.readouterr().out
    assert "0x8604" in out and "0x0105" in out


async def test_run_set_aborts_without_confirmation():
    bus = FakeBus(core(state_low=0x04), status())
    assert await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: False, sleep=nosleep) == 2
    assert bus.writes == []


async def test_run_set_reports_rejection():
    bus = FakeBus(core(state_low=0x04), status(), accept=lambda cmd, low: False)
    assert await run_set(bus, UnitState.HEAT, Commands(), confirm=lambda _q: True, sleep=nosleep) == 1
