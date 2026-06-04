# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.7] — 2026-06-04

Documentation-only patch — no library API or behaviour changes.

### Added
- `README.md`: a short **Troubleshooting** section covering two
  failure modes seen in the wild:
  - The `certifi` half-install (`Could not find a suitable TLS CA
    certificate bundle, invalid path: …/certifi/cacert.pem`) — the
    package directory survives an environment prune but `cacert.pem`
    gets removed; fix is `pip install --force-reinstall --no-deps certifi`.
    From a scheduled job this manifests as a complete failure at the
    `requests.post(.../auth/login)` step — no MQTT traffic at all.
  - A reminder that missing `set_reply` is `unconfirmed`, not `failed`,
    cross-linking to the existing refinements #4 and #5.

## [0.1.6] — 2026-05-31

Documentation-only patch — no library API or behaviour changes.

### Added
- `docs/DISCOVERY.md`: fifth pattern-level refinement — **verify via
  `ac_in_power` telemetry, not via `set_reply` alone**.
  - Since `set_reply` is unreliable (see refinement #4), the
    reference optimiser now takes a second swing at confirmation by
    reading the Delta Pros' `ac_in_power` HA sensors 90 s after
    publishing. If both units' readings are within ±75 W of the
    published `chChargeWatt`, that's physical evidence the schedule
    was applied — independent of any MQTT ack arriving.
  - Used to promote `unconfirmed` → `confirmed` in the three-state
    classification introduced in v0.1.5.
  - Only runs during the charging window (when AC input is active);
    outside the window it's `skipped` rather than a false negative.
  - Cheaper than a chained `/get` round-trip and reuses telemetry
    we're already collecting.

## [0.1.5] — 2026-05-31

Documentation-only patch — **corrects v0.1.4's rate-limit hypothesis with
direct empirical evidence**.

### Changed
- `docs/DISCOVERY.md`: refinement #4 rewritten again (third time's the
  charm, we hope). The v0.1.4 framing — "broker has a sliding-window
  rate limit; bursts of publishes cause silent `set_reply` drops" —
  conflated two separate phenomena.
  - Direct evidence (2026-05-31): the reference optimiser published
    `chChargeWatt = 300` the previous night, reported "FAILED" because
    no `set_reply` arrived, sent the "set manually" notification — and
    the SHP had in fact applied the schedule. Confirmed visually in
    the EcoFlow app the next morning at 300 W.
  - So: **the publish is reliable; the ack is not.** The May 27 burst
    "failures" that motivated v0.1.4's narrative were almost certainly
    the same phenomenon — successful publishes mis-classified.
  - New guidance: treat a missing `set_reply` as **`unconfirmed`**
    (probably succeeded), not `failed`. Only paho-layer rejection
    (`rc != 0`) is a real failure. The reference optimiser now reports
    three states (`confirmed` / `unconfirmed` / `failed`) and only the
    real-failed state triggers the HA fallback or a "set manually"
    notification.
  - "Don't burst your publishes" is still good advice — burst publishes
    correlate with dropped acks empirically — but no longer the headline.
- `quota_requests` is restated as a monotonic lifetime counter, not a
  quota indicator; surfaced only as a diagnostic.

## [0.1.4] — 2026-05-30

Documentation-only patch — **corrects v0.1.3's quota-guard hypothesis**.

### Changed
- `docs/DISCOVERY.md`: rewrites refinement #4 ("Respect the EcoFlow
  account quota"). The v0.1.3 hypothesis — that
  `sensor.smart_home_panel_*_status.quota_requests` is a per-day
  counter and that publishing should be blocked above a threshold —
  has been **empirically disproved**.
  - `quota_requests` is **not a daily counter**: it grows
    monotonically across the HA integration's lifetime
    (178 → 401 over four days, no daily reset).
  - Anyone implementing the v0.1.3 guard found their threshold
    permanently tripped after a few days, skipping every scheduled
    publish.
  - Real broker behaviour: **sliding-window per-cert-account rate
    limit on publish frequency** (a few minutes window;
    single-publish-per-day is fine; bursts of 5+ in 10 min trigger
    silent `set_reply` drops even though publishes succeed at
    `paho rc=0`).
  - Replacement guidance: publish once per cron tick, single attempt
    with a long timeout (~30 s, no retry); don't burst.
- The reference optimiser still surfaces `quota_requests` in its
  diagnostic notification line for visibility but no longer gates
  publishing on it.

## [0.1.3] — 2026-05-27

Documentation-only patch — no library API or behaviour changes.

### Changed
- `docs/DISCOVERY.md`: now lists **four** pattern-level refinements
  borrowed back from the reference optimiser, adding **respect the
  EcoFlow account quota**.
  - The EcoFlow API has an undocumented per-account daily quota.
    Around ~150 daily requests the broker starts silently dropping
    `set_reply` messages even when publishes succeed at the `paho rc=0`
    level. The optimiser now reads the live `quota_requests` counter
    from HA's `sensor.smart_home_panel_*_status` attribute and
    short-circuits the publish if the counter exceeds a block
    threshold (default 150).
  - When publishing, prefer **one long-timeout attempt** (30 s) over
    many short attempts — both burn quota per attempt, but the long
    attempt is gentler on the broker and more likely to actually
    catch the ack on a slow night.

## [0.1.2] — 2026-05-19

Documentation-only patch — no library API or behaviour changes.

### Changed
- `docs/DISCOVERY.md`: closing "What this opens up" section now lists
  **three** pattern-level refinements borrowed back from the reference
  optimiser, adding **self-calibrating Solcast confidence**. The
  optimiser now reads recent `(forecast, actual)` pairs from its
  SQLite history and uses the rolling-21-day median as its solar
  multiplier, replacing a hardcoded 0.85 default. On the author's site
  this dropped the multiplier to ~0.46 — Solcast forecasts roughly
  2× the actual delivered solar (pergola shading the model doesn't
  see).

## [0.1.1] — 2026-05-19

Documentation-only patch — no library API or behaviour changes.

### Changed
- `docs/DISCOVERY.md`: expanded the closing **"What this opens up"** section
  to describe two production-grade refinements the author has since adopted
  in the reference optimiser that drives this library:
  - **Persistent demand history.** Instead of relying on Home Assistant's
    10-day recorder window (whose `total_increasing` extraction occasionally
    failed in practice), the reference optimiser now reads from a SQLite
    store written by a nightly collector. The library itself is unchanged;
    this is purely an application-level pattern.
  - **Multiplicative demand contingency.** A 15 %-by-default buffer is now
    layered on top of the weighted historical average to absorb
    unscheduled extra usage (laundry, guests, hot showers). Empirically
    covers ~65 % of days in the author's data; raise to 20 % for ~83 %.

## [0.1.0] — 2026-05-17

Initial public release.

### Added
- `ecoflow_shp_mqtt.py` — Python library exposing `login`,
  `get_mqtt_credentials`, `build_schedule_payload`, `publish_and_verify`,
  and the `MqttCredentials` dataclass. Sub-400 LOC; uses `paho-mqtt` and
  `requests`.
- `examples/set_schedule.py` — CLI to publish a new SHP schedule
  (`chChargeWatt`, `hightBattery`, `lowBattery`, time slot bitmask).
- `examples/get_schedule.py` — CLI to read the SHP's current state.
- `examples/capture.py` — CLI to passively dump EcoFlow MQTT traffic
  for inspection (useful for new firmware / SHP 2 / SHP 3 captures).
- `docs/DISCOVERY.md` — the reverse-engineering story, including
  dead ends and gotchas.
- `docs/SCHEMA.md` — full payload field reference for the
  `cmdSet:11, id:81` schedule message.
- MIT licence.

[Unreleased]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.7...HEAD
[0.1.7]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/emdimon/ecoflow-shp-mqtt/releases/tag/v0.1.0
