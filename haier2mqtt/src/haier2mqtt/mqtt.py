"""MQTT connection: availability/LWT, discovery, commands in, state out. Never blocks control."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import aiomqtt

from .config import MqttSettings
from .payloads import BASE

_LOG = logging.getLogger(__name__)
REPUBLISH_S = 60.0


class MqttLink:
    def __init__(self, settings: MqttSettings, on_command: Callable[[str, str], None],
                 discovery: list[tuple[str, dict]]) -> None:
        self._s = settings
        self._on_command = on_command
        self._discovery = discovery
        self._latest: tuple[dict, list[dict], bool, str] | None = None
        self._event = asyncio.Event()
        self._last_svg: str | None = None

    def offer(self, state: dict, problems: list[dict], bus_reachable: bool, svg: str) -> None:
        self._latest = (state, problems, bus_reachable, svg)
        self._event.set()

    async def run(self) -> None:
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._s.host, port=self._s.port, username=self._s.username, password=self._s.password,
                    identifier="haier2mqtt",
                    will=aiomqtt.Will(f"{BASE}/availability", "offline", qos=1, retain=True),
                ) as client:
                    await client.publish(f"{BASE}/availability", "online", qos=1, retain=True)
                    for topic, payload in self._discovery:
                        await client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)
                    await client.subscribe(f"{BASE}/set/#", qos=1)
                    self._last_svg = None
                    _LOG.info("MQTT connected to %s:%s", self._s.host, self._s.port)
                    await asyncio.gather(self._reader(client), self._writer(client))
            except aiomqtt.MqttError as exc:
                _LOG.warning("MQTT disconnected: %s; retrying in 5 s", exc)
                await asyncio.sleep(5)

    async def _reader(self, client: aiomqtt.Client) -> None:
        prefix = f"{BASE}/set/"
        async for message in client.messages:
            topic = message.topic.value
            if not topic.startswith(prefix):
                continue
            payload = message.payload.decode("utf-8", "replace") if isinstance(message.payload, bytes) \
                else str(message.payload)
            try:
                self._on_command(topic[len(prefix):], payload)
            except Exception:  # noqa: BLE001 - a bad command must never break the link
                _LOG.exception("command handler failed for %s", topic)

    async def _writer(self, client: aiomqtt.Client) -> None:
        while True:
            try:
                await asyncio.wait_for(self._event.wait(), timeout=REPUBLISH_S)
            except TimeoutError:
                pass
            self._event.clear()
            if self._latest is None:
                continue
            state, problems, bus_reachable, svg = self._latest
            await client.publish(f"{BASE}/bus", "reachable" if bus_reachable else "unreachable", qos=1, retain=True)
            await client.publish(f"{BASE}/state", json.dumps(state, ensure_ascii=False), qos=1)
            await client.publish(f"{BASE}/problems", json.dumps(problems, ensure_ascii=False), qos=1, retain=True)
            if svg != self._last_svg:
                await client.publish(f"{BASE}/curve_svg", svg, qos=1, retain=True)
                self._last_svg = svg
