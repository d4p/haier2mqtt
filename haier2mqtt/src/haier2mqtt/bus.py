"""Async Modbus TCP access to the unit; one lock around every operation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from pymodbus.client import AsyncModbusTcpClient

from .model import Raw

_LOG = logging.getLogger(__name__)
CORE, STATUS, MODE, ADVANCED = (101, 6), (141, 16), (201, 1), (241, 22)


class BusError(Exception):
    pass


class HaierBus:
    def __init__(self, host: str, port: int, slave: int, timeout: float = 5.0,
                 client_factory: Callable[[], Any] | None = None) -> None:
        self._slave = slave
        self._factory = client_factory or (lambda: AsyncModbusTcpClient(host, port=port, timeout=timeout, retries=1))
        self._client: Any = None
        self._lock = asyncio.Lock()
        self.reachable = False

    async def _ensure(self) -> Any:
        if self._client is None or not self._client.connected:
            self._drop()
            client = self._factory()
            if not await client.connect():
                client.close()
                raise BusError("connect failed")
            self._client = client
        return self._client

    def _drop(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: S110, BLE001 - closing a broken client must never raise
                pass
        self._client = None

    async def _read(self, address: int, count: int) -> tuple[int, ...]:
        client = await self._ensure()
        try:
            resp = await client.read_holding_registers(address, count=count, slave=self._slave)
        except Exception as exc:
            self._drop()
            raise BusError(f"read {address}+{count}: {exc!r}") from exc
        if resp.isError() or len(resp.registers) != count:
            self._drop()
            raise BusError(f"read {address}+{count}: bad response {resp!r}")
        return tuple(resp.registers)

    async def _write(self, address: int, values: tuple[int, ...]) -> None:
        client = await self._ensure()
        try:
            resp = await client.write_registers(address, list(values), slave=self._slave)
        except Exception as exc:
            self._drop()
            raise BusError(f"write {address}: {exc!r}") from exc
        if resp.isError():
            self._drop()
            raise BusError(f"write {address}: error response {resp!r}")

    async def read_raw(self) -> Raw | None:
        async with self._lock:
            try:
                core = await self._read(*CORE)
                status = await self._read(*STATUS)
                mode = await self._read(*MODE)
            except BusError as exc:
                _LOG.warning("unit read failed: %s", exc)
                self.reachable = False
                return None
            try:
                advanced: tuple[int, ...] | None = await self._read(*ADVANCED)
            except BusError as exc:
                _LOG.debug("advanced block unavailable: %s", exc)
                advanced = None
            self.reachable = True
            return Raw(core, status, mode, advanced)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Tx]:
        async with self._lock:
            yield _Tx(self)

    async def close(self) -> None:
        async with self._lock:
            self._drop()


class _Tx:
    def __init__(self, bus: HaierBus) -> None:
        self._bus = bus

    async def read_core(self) -> tuple[int, ...]:
        return await self._bus._read(*CORE)

    async def read_mode(self) -> tuple[int, ...]:
        return await self._bus._read(*MODE)

    async def write(self, address: int, values: tuple[int, ...]) -> None:
        await self._bus._write(address, values)
