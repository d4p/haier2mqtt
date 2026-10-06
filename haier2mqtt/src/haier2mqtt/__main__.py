"""Entry point: python -m haier2mqtt"""

import asyncio
import logging
import os
from pathlib import Path

from .app import App
from .config import load_settings
from .mqtt import MqttLink
from .payloads import discovery_messages


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", "/data"))
    settings = load_settings(data / "options.json", os.environ, data)
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = App(settings)
    link = MqttLink(settings.mqtt, app.handle_command, discovery_messages())
    asyncio.run(app.run(link))


if __name__ == "__main__":
    main()
