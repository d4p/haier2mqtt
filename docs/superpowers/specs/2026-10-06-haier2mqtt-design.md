# haier2mqtt: design

Date: 2026-10-06
Status: draft for review

## 1. Purpose

Replace the `ha_haier` Home Assistant custom integration with a standalone service, `haier2mqtt`. The service controls the Haier heat pump reliably and keeps it protected from frost, independently of Home Assistant Core.

### What the user said
- The heat pump is a Haier **AU162FYCRA** monoblock. It is reached over RS485 through a **USR-DR164** Modbus TCP gateway (192.168.8.209:8899, slave 17).
- The YR-E27 wall panel was **removed**, so this service is the unit's only controller.
- The user chooses between the **Haier** and the **Vaillant** gas boiler **by hand**. The Vaillant is not integrated.
- The Haier **never heats hot water**. Its tank sensor reports real data (most likely the 900 L tank attached to the Haier).
- The internal circulation pump draws **~170 W** whenever the unit is on, so the unit must be in **standby when there is no demand**.
- Must have:
  - antifreeze protection, **not relying on the unit's built-in protection** (assumed not to work in standby)
  - standby without demand
  - a heating curve
  - alerts on any fault, any loss of communication, and any problem the unit reports
- If HA disappears, the unit must **keep its last state** (heating continues on the curve; standby stays standby).
- Deployment: **HA add-on**, code in a **new public GitHub repo** (`d4p/haier2mqtt`).
- Later (out of scope): optimise the thermostats ("virtual" ones?) and the heat pump plus its 900 L tank against current and forecast weather.

### Assumptions
- The register-101 high byte selects the command: `0x01` = on/off, `0x86` = mode, `0x20` = circulation pump. The unit reports `0xDD`. This must be confirmed by the supervised test (section 9) before any automated writes.
- One Modbus TCP client at a time on the gateway. `ha_haier` and `haier2mqtt` must never both be connected.

### Success criteria
1. The unit follows demand (standby / heat) within 2 minutes, unless a minimum on/off time delays it, in which case it follows as soon as that time allows.
2. No command is lost. A failed or rejected write is detected by read-back, retried, and reported.
3. Control and antifreeze continue through HA Core restarts and updates, and through MQTT broker outages.
4. Every fault, every communication loss and every unit-reported problem produces a push notification. Each also produces a matching "cleared" notification.
5. No write can produce cooling mode, tank mode or any other unintended state.

## 2. Why the current integration is replaced

Problems found in `ha_haier` on 2026-10-06:
1. **On/off writes are rejected and the code doesn't notice.** PyHaier `SetState` sends high byte `0x01`/`0x86`/`0x04`. The unit reports `0xDD84` and ignores those writes. Write verification logs "mismatch" and returns success. The compressor has not run since May 2026.
2. **PyHaier "on"/"off" compute `current ± 1`.** "off" sent to a unit that is already off yields `0x83` (on + cool + tank).
3. If the first connection fails, setup returns `False`, so there is no retry. The integration stayed dead from 2026-09-28 until an HA restart.
4. Control only reacts to demand changes. The 30-min rate limit **drops** commands, and failed writes are never retried.
5. Read-modify-write spans separate locks, and antifreeze writes up-to-30-s-old register snapshots.
6. Stale data is served as current for about 2.5 min of failed reads.
7. Antifreeze lives inside HA. It switches the whole unit off afterwards and sends no alerts.

## 3. Architecture

One add-on, one process, the only client of the gateway.

| Component | Job | Depends on |
|---|---|---|
| `bus` | Async Modbus TCP client. One lock around every read-modify-write. A read either returns fresh data or "no data" (never cached values). Reconnect with backoff. | pymodbus (async) |
| `codec` | Decode registers into typed values (reuse PyHaier decoders where correct). Encode writes as **explicit** values with the correct command byte, never ±1. Sanity-check decoded values. Pure functions. | PyHaier (decoders only) |
| `controller` | Every poll cycle, compute the desired state, compare it with the actual state, and reconcile. Enforce minimum on/off times and the curve write policy. | `codec`, `bus`, `antifreeze`, `curve` (**no MQTT**) |
| `antifreeze` | Stage logic (0/1/2) from water and outdoor temperatures. Pure, time-injected. | none |
| `curve` | Points curve plus offset, clamping, and choice of outdoor input (HA forecast or Tao). Pure. | none |
| `mqtt` | Publish availability, bus status, state JSON and HA discovery. Receive commands and inputs. Persist controls. Reconnects independently; its outages never block `controller`. | aiomqtt |
| `store` | Persist operating mode, curve offset, last desired state and antifreeze state to `/data/state.json`. | none |

### Failure behaviour
| Failure | Behaviour |
|---|---|
| MQTT broker or HA down | Control and antifreeze continue. After the heartbeat timeout (10 min): fallback, i.e. keep the last desired state, with the curve on Tao. |
| Gateway or heat pump unreachable | No writes possible. `bus` = unreachable; HA alerts. |
| Service crash or hang | Supervisor watchdog restarts it. HA alerts on offline, or on stale state (> 3 min). |
| Service restart while HA is down | Last desired state and controls are restored from `/data`, so the fallback still applies. |
| **HA VM / HA OS down** | **Nothing runs: no control, no antifreeze, no alerts. Known, accepted limitation.** Typical window: minutes (an OS update took about 5 min). Possible future mitigation: an external watchdog. |

## 4. Control logic

### Target states (register 101 low byte; heat only, tank bit always 0)
| State | Meaning | Low byte (to be confirmed) |
|---|---|---|
| `STANDBY` | off, pump off | `0x04` (heat, off) |
| `CIRCULATE` | off, circulation pump A05 forced on | `0x24` (bit 5 + heat, off) |
| `HEAT` | on, heating to the CH target | `0x05` (heat, on) |

The command byte for each write is confirmed in rollout step 2. Until then, writes stay disabled (shadow mode).

### Desired state each cycle (strict priority)
1. **Antifreeze** stage 1 → `CIRCULATE` (unless already `HEAT`). Stage 2 → `HEAT` at 30 °C.
2. **Operating mode** (HA select, persisted): `auto` / `wyłączona` / `grzanie`. `wyłączona` → `STANDBY`; `grzanie` → `HEAT`. Antifreeze overrides `wyłączona`.
3. **Auto:** demand true → `HEAT` at the curve target; false → `STANDBY`.
4. **Heartbeat lost** (no demand message for 10 min): keep the last desired state from step 3.

### Reconciliation
- Every cycle (10 s poll), compare actual vs desired. On a difference:
  1. Fresh read under the lock.
  2. Build the explicit value.
  3. Write.
  4. Wait 1 s, then read back.
- A read-back mismatch is a **failure**, retried up to 3 times. After that, raise the problem `write_failed` and retry reconciliation every 5 min.
- Any stray state is corrected: tank bit, cooling, a change made directly on the unit.

### Short-cycle protection
- Minimum 20 min in `HEAT`; minimum 10 min out of `HEAT`.
- An early command is **delayed** until it's allowed, never dropped.
- Only antifreeze stage 2 may skip the minimum off time.

### Heating curve
- Points (from the current config): −20 → 40, 0 → 32, 10 → 29, 20 → 28 °C. Linear interpolation, flat beyond the ends, clamped to 25–45 °C, plus an **offset** of −5 to +5 °C (HA-settable).
- Outdoor input: the HA forecast (`set/outdoor_forecast`). It is stale after 30 min; then use Tao.
- The CH target is written only in `HEAT`, and only when the change is ≥ 0.5 °C and ≥ 20 min have passed since the last curve write. There is no wait when entering `HEAT`.

### Antifreeze
Inputs: Twi and Two (water in the outdoor unit) and Tao. The tank temperature is shown but not used (indoors).

- **Stage 1:**
  - Trigger: Tao < 0 °C → `CIRCULATE` 5 min every 30 min.
  - Trigger: min(Twi, Two) < 5 °C → `CIRCULATE` continuously.
- **Stage 2:**
  - Trigger: water < 3 °C, **or** water still falling after 10 min of circulation, **or** the pump-only write fails.
  - Action: `HEAT` at 30 °C.
- **Exit:** both Twi and Two > 10 °C for 5 min → back to the otherwise-desired state.
- **Missing water readings** while the bus works and Tao < 0 °C (or Tao is missing and the HA outdoor input < 0 °C): periodic circulation as in stage 1.
- All thresholds and timings are add-on options.

## 5. MQTT interface

Base topic `haier2mqtt`. Discovery prefix `homeassistant`. One device: "Pompa ciepła Haier".

| Topic | Direction | Content |
|---|---|---|
| `haier2mqtt/availability` | out | `online` / `offline` (offline also set as the broker's last-will message) |
| `haier2mqtt/bus` | out | `reachable` / `unreachable` |
| `haier2mqtt/state` | out | JSON, on change and at least every 60 s |
| `haier2mqtt/problems` | out | JSON list of active problems: `{id, severity, message, since, data}` |
| `haier2mqtt/set/demand` | in | `{"demand": true}`. Sent by HA on change **and every 60 s** (heartbeat). Not retained. |
| `haier2mqtt/set/outdoor_forecast` | in | `{"value": 9.2}`. Sent on change and every 5 min. Not retained. |
| `haier2mqtt/set/mode` | in | `auto` / `wyłączona` / `grzanie` (persisted) |
| `haier2mqtt/set/performance` | in | `eco` / `quiet` / `turbo` (register 201) |
| `haier2mqtt/set/curve_offset` | in | −5 … +5 (persisted) |

Entities use availability = service online AND bus reachable.
- **Temperatures:** Twi, Two, tank, Tao, CH target, curve target, compressor.
- **Operation:** actual state, desired state, reason (`antifreeze` / `tryb` / `zapotrzebowanie` / `fallback`), antifreeze stage, curve input source (`prognoza` / `Tao`), compressor frequency and current, fan rpm, EEV.
- **Binary:** internal pump running, compressor running, defrost, hardware antifreeze, problem, bus reachable, heartbeat OK.
- **Faults:** active error, last error, error archive, last write result.
- **Controls:** mode, performance, curve offset.

Add-on options:
- gateway host, port, slave ID
- poll interval (10 s)
- curve points and limits
- minimum on/off times
- antifreeze thresholds and timings
- heartbeat timeout
- writes enabled (bool, default **false**)
- MQTT credentials (default: from the Mosquitto add-on service discovery)

## 6. Problems reported by the service (`haier2mqtt/problems`)
| id | Trigger | Severity |
|---|---|---|
| `unit_error` | active error code ≠ 0; message includes the code and a Polish description where known, otherwise "kod nieznany – sprawdź instrukcję" | warning |
| `unit_error_history` | new entry in the error archive / `last_error` changed since the previous poll (catches errors that clear between polls) | warning |
| `hw_antifreeze` | the unit's antifreeze flag is on | warning (critical if water < 3 °C) |
| `heater_on` | the backup heater was switched on by the unit | warning |
| `unexpected_state` | the codec sanity check fails (unknown bits, out-of-range values); raw registers attached | warning |
| `antifreeze_stage1` / `antifreeze_stage2` | antifreeze stage active | warning / critical |
| `water_low` | min(Twi, Two) < 3 °C | critical |
| `write_failed` | read-back mismatch after 3 retries | warning |
| `state_mismatch` | actual ≠ desired for > 15 min | warning |
| `heartbeat_lost` | no demand message for > 10 min | warning |

## 7. Home Assistant side
- `input_select.zrodlo_ciepla`: Haier / Vaillant.
- Template binary sensor `binary_sensor.haier_zapotrzebowanie` = source is Haier AND `binary_sensor.glowne_zapotrzebowanie_ciepla` is on.
- Automation **"Haier – zapotrzebowanie i prognoza"**: publishes `set/demand` on change and every 60 s, and `set/outdoor_forecast` (`sensor.temperatura_zewnetrzna_za_6h`) on change and every 5 min.
- Automation **"Haier – alarmy"** sends Polish notifications to the Pixel (critical ones on `alarm_stream`) and a persistent notification, plus a "cleared" message for each:
  - every entry in `haier2mqtt/problems`, using its severity
  - **"Brak komunikacji z pompą ciepła"**, saying which link failed:
    - service ↔ HA: availability offline, or no `state` message for > 3 min (3 min)
    - service ↔ unit: `bus` unreachable (5 min)
    - frozen values: temperatures unchanged for > 60 min while in `HEAT` (60 min)
  - Normal severity; critical when the HA outdoor sensor reads < 3 °C. Restore message: "Komunikacja przywrócona".
- **Dashboard `dashboard-ogrzewanie`:**
  - Haier box: actual state, reason, Twi/Two, tank temperature, ⚠ on any problem.
  - Heading: source selector.
  - Haier pop-up: mode, curve offset, performance, desired vs actual state, antifreeze stage, last write, error codes, active problems, 24 h chart.
- **Grafana:** re-run `tools/grafana_dashboards.py` with the new entities later.
- **Cutover:** delete the `ha_haier` entry, remove `d4p/ha_haier` via HACS.

## 8. Repository layout (`d4p/haier2mqtt`)
```
repository.yaml                 # HA add-on repository metadata
haier2mqtt/                     # the add-on
  config.yaml  Dockerfile  run.sh  CHANGELOG.md  DOCS.md  translations/
src/haier2mqtt/
  bus.py codec.py controller.py antifreeze.py curve.py mqtt.py store.py config.py main.py
tests/
  test_codec.py test_controller.py test_antifreeze.py test_curve.py test_bus.py fixtures/registers_2026-10-06.json
docs/superpowers/specs/
.github/workflows/ci.yaml       # pytest + ruff
```

## 9. Testing and rollout
**Automated tests** (pytest, CI):
- `codec`: every target state against real register dumps (`0xDD84` …). Regression test: an "off" request on an already-off unit still encodes `STANDBY`, never `0x83`.
- `controller` and `antifreeze`: simulated time. Covers demand changes, delayed (not dropped) commands, heartbeat loss leading to fallback, antifreeze stages 1→2→exit, write failures and retries, stray tank/cool bit corrected, mode overrides.
- `curve`: interpolation, clamping, offset, forecast staleness leading to Tao.
- `bus`: a fake Modbus server covering timeouts, partial answers, reconnect, and no stale data.

**Rollout:**
1. **Shadow mode** (`writes_enabled: false`): disable `ha_haier`, run the add-on read-only for a day, and compare the desired state with reality.
2. **Supervised encoding test** (user at the unit): STANDBY → HEAT → STANDBY → CIRCULATE → STANDBY, each read back and watched. Confirms the command bytes. Stop on any surprise.
3. **Live** (`writes_enabled: true`, mode `auto`): end-to-end alert tests with faked values. Warn the user before critical alerts.
4. **Cutover:** remove `ha_haier`, update the dashboards, document in the homeassistant workspace `CLAUDE.md`.
5. **Before the first frost:** temporarily raise the antifreeze thresholds to trigger stage 1 and stage 2 at current temperatures, then restore them.

## 10. Out of scope
- Optimising weather, tank and thermostats (only the hooks: curve offset, mode).
- Choosing the heat source automatically (stays manual).
- Hot water on the Haier.
- An external watchdog for HA VM outages.
- Re-syncing the zone-pump automations (`Sterowanie pompą …`); a separate task.
