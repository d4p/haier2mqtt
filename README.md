# haier2mqtt

Home Assistant add-on repository for **haier2mqtt**, a reliable controller for Haier heat pumps (PyHaier register map) over Modbus TCP, with local antifreeze and MQTT discovery.

Add `https://github.com/d4p/haier2mqtt` in **Settings → Add-ons → Add-on store → ⋮ → Repositories**, then install "haier2mqtt". See `haier2mqtt/DOCS.md`.

Development: `cd haier2mqtt && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest`.
Design: `docs/superpowers/specs/2026-10-06-haier2mqtt-design.md`.
