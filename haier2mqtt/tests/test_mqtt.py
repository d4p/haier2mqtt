import asyncio

import aiomqtt

import haier2mqtt.mqtt as mqtt_mod
from haier2mqtt.config import MqttSettings
from haier2mqtt.mqtt import MqttLink


class _Msg:
    class topic:
        value = "haier2mqtt/set/mode"

    payload = bytearray(b"auto")


async def test_run_reconnects_and_cancels_sibling(monkeypatch):
    entered = []
    writer_cancelled = []
    commands = []

    class FakeClient:
        def __init__(self, **_kw):
            entered.append(self)
            self.n = len(entered)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def publish(self, *a, **k):
            if self.n == 1 and a[0].endswith("/curve_svg"):
                pass

        async def subscribe(self, *a, **k):
            pass

        @property
        def messages(self):
            return self._gen()

        async def _gen(self):
            if self.n >= 2:
                await asyncio.Event().wait()
            yield _Msg()
            await asyncio.sleep(0)
            raise aiomqtt.MqttError("lost")

    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)
        await real_sleep(0)

    monkeypatch.setattr(mqtt_mod.aiomqtt, "Client", FakeClient)
    monkeypatch.setattr(mqtt_mod.asyncio, "sleep", fake_sleep)

    link = MqttLink(MqttSettings(host="x", port=1, username="", password=""), lambda s, p: commands.append((s, p)), [])
    orig_writer = link._writer

    async def tracking_writer(client):
        try:
            await orig_writer(client)
        except asyncio.CancelledError:
            writer_cancelled.append(client.n)
            raise

    link._writer = tracking_writer
    task = asyncio.create_task(link.run())
    for _ in range(100):
        await real_sleep(0.001)
        if len(entered) >= 2:
            break
    task.cancel()
    await asyncio.wait({task}, timeout=2)
    assert len(entered) >= 2
    assert 5 in sleeps
    assert commands[0] == ("mode", "auto")
    assert 1 in writer_cancelled
