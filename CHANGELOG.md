# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/emdimon/ecoflow-shp-mqtt/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/emdimon/ecoflow-shp-mqtt/releases/tag/v0.1.0
