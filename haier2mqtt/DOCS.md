# haier2mqtt

Controls a Haier monoblock heat pump through a USR-DR164 RS485→Modbus TCP gateway and exposes it to Home Assistant via MQTT discovery (device "Pompa ciepła Haier").

## Important
- Only one Modbus client may use the gateway. Disable the `ha_haier` integration before starting this add-on.
- `writes_enabled` is **off** by default (shadow mode): the add-on reads and decides, but never writes. Enable it only after the command bytes were confirmed with `python -m haier2mqtt.probe` while someone is at the unit.
- Enable the add-on **Watchdog** so the Supervisor restarts it if it crashes.

## What it does
- Standby when there is no demand (internal pump off), heating on the curve when there is demand.
- Antifreeze: periodic circulation below +3 °C outside, continuous below 5 °C water, emergency heating below 3 °C.
- Keeps working when HA or MQTT is down (fallback: keep the last state).
- Publishes problems as binary sensors (`binary_sensor.haier_problem_*`) for HA alerts.

## Inputs from HA (MQTT, not retained)
- `haier2mqtt/set/demand` `{"demand": true}`, on change and every 60 s (heartbeat)
- `haier2mqtt/set/outdoor_forecast` `{"value": 9.2}`, on change and every 5 min
