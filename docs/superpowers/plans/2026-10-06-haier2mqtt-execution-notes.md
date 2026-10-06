# haier2mqtt — execution notes (Tasks 1–14)

Branch feat/haier2mqtt, executed 2026-10-06 with per-task review and a final whole-branch review. 153 tests, ruff clean, add-on image builds.

## Rulings made during execution (decisions that deviate from or extend the plan)

- Task 5: Ruling: Important 2 (outdoor unknown + water known → no periodic circulation) conflicts with plan code (cold only when both unknown) — decided: treat outdoor None as cold regardless of water (spec: antifreeze is primary protection and must not rely on unit; unknown outdoor is an unknown risk) — cost if wrong: ~170 W pump 5 min/30 min while Tao and forecast are both unavailable.
- Task 5: Ruling: Important 1 (water dropout during continuous circulation never escalates) — decided: if continuous circulation is active (water was < 5 °C) and water has been unreadable for ≥ circulate_check_s, escalate to stage 2; periodic-only mode with dead sensor stays periodic (spec) — cost if wrong: compressor runs in emergency heat until the sensor returns and >10 °C is held 5 min.
- Task 5: Ruling: Minor 1 (NaN) promoted into the same fix round — normalise non-finite water/outdoor to None at top of update() — trivial and safety-relevant — cost if wrong: none.
- Task 8: Ruling: Important 1 (CH-write failure shares state backoff → heating never starts if 0x04 is wrong) — decided: CH writes get their own backoff (_next_ch_retry); a CH failure never gates state reconciliation; heat starts at the old CH target — cost if wrong: unit heats at the previous CH target until CH writes succeed.
- Task 8: Ruling: Important 2 (write_failed cleared in same cycle) — decided: per-kind failure flags (state, ch, performance); write_failed = OR; each flag clears only on that kind's success; performance request stays pending and is retried after its own backoff until it succeeds — cost if wrong: a stuck performance write keeps the write_failed problem on (visible, intended).
- Task 8: Ruling: Important 3 (min-on/off lost on restart) conflicts with plan tests — decided: Controller gains `startup_settled: bool = False`; when False, the first observation seeds the timers conservatively (HEAT observed → heat_since=now; otherwise off_since=now). Tests' make() passes startup_settled=True to keep the plan's scenarios; new tests cover the default. App (Task 12) uses the default — cost if wrong: after a service restart heating can start up to 10 min late, or HEAT is held up to 20 min.
- Task 8: Ruling: Minors 1, 3, 4 promoted into fix round 1 (HEAT delayed by min-off while antifreeze wants CIRCULATE → circulate; rejection log prints the checked field; mismatch test made meaningful) — cheap, frost/diagnostics relevant — cost if wrong: none.
- Task 12: Ruling: Task 10's deferred minor "Store.load returns non-dict JSON → App crashes at startup" promoted into Task 12 fix round 1 (load returns {} unless the JSON is a dict; test added) — startup crash = no frost protection — cost if wrong: none.
- Final: Ruling: Critical 1 (Twi/Two decode as unsigned 12-bit → -1 °C reads 408.6 °C, antifreeze treats as warm) — decided: decode Twi/Two as signed 12-bit (raw ≥ 0x800 → raw − 0x1000, same idea as Tao), then values outside −30..80 °C → None (anomaly kept); test helper gains raw/None encoding; decode→antifreeze tests for −1 °C and garbage — cost if wrong: if the unit actually encodes water unsigned, readings ≥ 204.8 °C would appear negative, but such readings are impossible anyway.
- Final: Ruling: Critical 2 (registers 104/105 sent back with 0xDD high bytes; PyHaier masks them) — decided: keep copying 102–106 unchanged. Evidence: on 2026-10-06 ha_haier wrote [0x0404, 0x3B1E, 0x0000, 0xDD01, 0xDD5A, 0x5C1E] and the unit applied the CH temperature (102 changed to 0x3B1E) — the 0xDD-preserving block is the only encoding observed to be accepted. Add probe option --mask-104-105 (PyHaier-style) and a before/after diff of 101–106 + 201 for every probe write, so Task 19 can compare both with the user present — cost if wrong: Task 19 reveals it and the default flips (one-line change).
- Final: Ruling: Important 3 — add probe subcommands `set-ch <temp>` and `set-performance <eco|quiet|turbo>` (with confirmation, read-back, diff); Task 19 gains those steps.
- Final: Ruling: Important 4 — min-on hold applies only when the unit is heat-only and on (low & 0x87 in {0x05}, pump bit ignored); stray on-states (cool/tank) are not held and get corrected immediately.
- Final: Ruling: Important 5 — restored last_auto accepted only if heat/standby; MQTT env defaults use `or` (empty strings fall back); run.sh exports without masking bashio exit status.
- Final: Ruling: Important 6 — invalid curve option no longer prevents startup: log error, use the built-in default curve (-20:40, 0:32, 10:29, 20:28, 25–45). No config.yaml watchdog key (Supervisor watchdog needs a URL/port; DOCS instructs enabling the Watchdog toggle).
- Final: Ruling: promoted minors — encode_ch_temp guards 20–55 °C (ValueError), controller keeps running; performance request cleared only if unchanged during the write. Others stay deferred (listed in final message).
- Final: parked — probe `set-ch nan/inf` raises a traceback before the encode guard (nothing written) — Ruling: cosmetic, safety contract holds; fix opportunistically.
- Final: parked — STANDBY → stray on-state (UNKNOWN) → STANDBY doesn't restart the min-off timer — Ruling: real but rare (unit switched itself on, already alerted as unexpected_state); deferred.
- Final: parked — bashio may return "null" for MQTT username when the broker has no credentials — Ruling: Mosquitto add-on provides service credentials; verify during Task 18 install.

## Deferred minor findings (not fixed; triaged by the final review)

- Task 1: minor (deferred): helpers.core() docstring says 0xDD on 101/104/105 but `high` only affects 101
- Task 1: minor (deferred): helpers.status(twi=None) encodes 0.0 (lossy missing-reading encoding)
- Task 2: minor (deferred): wrong-length advanced block silently treated as absent (no anomaly/log)
- Task 2: minor (deferred): status bit masks are bare literals; model.py repeats 0x01/0x20
- Task 2: minor (deferred): Twi/Two out-of-range anomaly branch untested
- Task 3: minor (deferred, safety hardening — final review should triage): encode_ch_temp has no range guard (hb outside 0..255 or outside 20–55 °C hard limits would be sent)
- Task 3: minor (deferred): _state_write/encode_ch_temp don't reject short core (<6 regs)
- Task 3: minor (deferred): HEAT→CIRCULATE emits transient 0x25 (pump before power-off); order is plan-mandated
- Task 3: minor (deferred): no test for unknown verify check / short core / out-of-range temp
- Task 4: minor (deferred): no test pinning 0.5 rounding on an off-step value (e.g. 7 °C → 30.0)
- Task 4: minor (deferred): render_svg marker y not clamped to plot area
- Task 5: minor (deferred): unused attribute self._water_seen in antifreeze.py (dead code)
- Task 5: minor (deferred): stage 2 with permanently dead water sensor never exits (safe; needs alert — problems task covers water readings only via water_low)
- Task 5: minor (deferred): _water_at_start never refreshed (rise then fall not flagged as not-rising)
- Task 6: minor (deferred): client.connect() not wrapped in asyncio.wait_for (app-level 60 s cycle timeout mitigates)
- Task 6: minor (deferred): read_raw failure logged at warning every poll while offline (log on state change)
- Task 6: minor (deferred): no test for write failure path / reachable recovery
- Task 7: minor (deferred): fallback could return CIRCULATE if last_auto were CIRCULATE (callers only persist heat/standby); unknown mode silently acts as auto
- Task 7: minor (deferred): no tests for heartbeat boundary (600 s), af CIRCULATE + mode wyłączona/grzanie
- Task 8: minor (deferred): TDD RED step skipped by implementer (code transcribed verbatim)
- Task 8: minor (deferred): _gate calls af.wants(now) an extra time (pure, harmless); mismatch-clear test lenient on first cycle
- Task 8: minor (deferred): stage-1 CIRCULATE waits out state-write backoff (non-urgent)
- Task 9: minor (deferred): unit_error_history fires alongside a new active error with "(już nieaktywny)" wording
- Task 9: minor (deferred): "od ponad 15 min" hardcoded though mismatch_alert_s configurable; history alert can outlive hold during bus outage
- Task 9: minor (deferred): no tests for write_failed / heater_on / raw register dump in unexpected_state
- Task 10: minor (deferred, robustness — final review should triage): Store.load returns non-dict JSON (e.g. []) → App .get() would crash at startup; save() has no fsync and OSError not caught by callers
- Task 10: minor (deferred): curve_from_dict/load_settings edge tests missing (NaN, missing offset, MQTT env defaults)
- Task 11: minor (deferred): binary sensors render null fields as OFF instead of unknown (bus availability/expire_after mitigate)
- Task 12: minor (deferred): mixed MqttError+other exception group sleeps 10 s; dead branch in fake publish; Store.save sync I/O in loop; svg rendered every poll
- Task 13: minor (deferred): probe tests couple to planner call counts (3rd/4th call); --yes has no warning line; dump/CLI parsing untested
- Task 14: minor (deferred): run.sh `export VAR="$(...)"` masks bashio failures (set -e ineffective); build.yaml build_from deprecated-style; stale PyYAML comment in test

## Required before rollout Task 19 (supervised test)

- Use the new probe commands: `set`, `set-ch`, `set-performance`; compare default vs `--mask-104-105` and read the register diffs.
- Confirm power/mode/pump/CH-temp/performance command bytes; update add-on options if they differ.
