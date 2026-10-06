from haier2mqtt.bus import BusError, HaierBus
from tests.fakes import FakeModbusClient, unit_registers


def make(**kw) -> tuple[HaierBus, list[FakeModbusClient]]:
    created: list[FakeModbusClient] = []

    def factory():
        c = FakeModbusClient(unit_registers(), **kw)
        created.append(c)
        return c

    return HaierBus("x", 1, 17, client_factory=factory), created


async def test_read_raw_reads_all_blocks():
    bus, _ = make()
    raw = await bus.read_raw()
    assert raw.core[0] == 0xDD84 and len(raw.status) == 16 and raw.mode == (0,) and len(raw.advanced) == 22
    assert bus.reachable


async def test_connect_failure_returns_none_and_unreachable():
    bus, _ = make(connect_ok=False)
    assert await bus.read_raw() is None
    assert not bus.reachable


async def test_required_block_error_returns_none_never_stale():
    bus, created = make()
    assert await bus.read_raw() is not None
    created[0].fail_reads.add(141)
    assert await bus.read_raw() is None          # no cached data returned
    assert not bus.reachable


async def test_short_block_is_a_failure():
    bus, _ = make(short_reads={101})
    assert await bus.read_raw() is None


async def test_timeout_drops_connection_and_reconnects():
    bus, created = make(raise_reads={101})
    assert await bus.read_raw() is None
    assert created[0].closed >= 1
    created_count = len(created)
    assert await bus.read_raw() is None
    assert len(created) > created_count          # a fresh client was created


async def test_advanced_block_is_optional():
    bus, _ = make(fail_reads={241})
    raw = await bus.read_raw()
    assert raw is not None and raw.advanced is None


async def test_transaction_read_write():
    bus, created = make()
    async with bus.transaction() as tx:
        core = await tx.read_core()
        await tx.write(101, (0x0105, *core[1:]))
        assert (await tx.read_core())[0] == 0x0105
    assert created[0].writes == [(101, [0x0105, *core[1:]])]


async def test_transaction_errors_raise_buserror():
    bus, _ = make(fail_reads={101})
    try:
        async with bus.transaction() as tx:
            await tx.read_core()
    except BusError:
        pass
    else:
        raise AssertionError("BusError expected")
