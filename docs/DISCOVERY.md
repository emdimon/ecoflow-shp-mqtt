# The Discovery

How the SHP schedule-write MQTT message was reverse-engineered, May 2026.

## The problem

EcoFlow Smart Home Panel (SP10, gen 1) lets you define a daily charging
schedule on the panel itself: a time slot (e.g. 00:00–07:00), an AC charging
rate per Delta Pro (e.g. 500 W), and an SOC cap (e.g. 90 %). Outside that
time slot the SHP doesn't draw from the grid.

Many SHP owners use Home Assistant integrations to monitor and control their
EcoFlow gear. The community-standard integration is
[`tolwi/hassio-ecoflow-cloud`](https://github.com/tolwi/hassio-ecoflow-cloud).
It exposes the Delta Pros' AC charging power as a writeable HA `number` entity
(`number.{sn}_ac_charging_power`) — but if you write to it during a window
where the SHP has its own schedule running, your value is silently overridden.
The SHP charges at the rate stored in its own schedule, full stop.

So the only way to dynamically adapt the charging rate to, say, tomorrow's
solar forecast and last-week's demand baseline is to **modify the SHP's
schedule itself** — and that schedule lives on the SHP, editable only from
the EcoFlow mobile app. No HA integration writes it. No public EcoFlow
Developer API exposes it. `tess1o/go-ecoflow` is the most comprehensive
third-party SHP control library, but its SHP module exposes channel/current/
EPS/SOC-threshold writes and *not* schedule writes.

This is the gap we filled.

## The approach

There were a few possible routes:

1. **Decompile the EcoFlow Android APK with JADX.** Extract the protobuf
   `.proto` definitions, infer command schemas. High effort, brittle —
   any new app version starts over.
2. **MITM the mobile app's traffic.** TLS interception (e.g. mitmproxy
   with a CA installed on a rooted phone) to capture HTTPS and MQTT.
   Medium effort, fragile.
3. **Subscribe to the EcoFlow MQTT broker directly as a second client**
   using your own account credentials, sniff the topics the app
   publishes to. **Lowest effort** if the broker doesn't enforce
   strict single-client-per-user.

Option 3 turned out to work. The EcoFlow MQTT broker (`mqtt-e.ecoflow.com`
for EU accounts; `8883/mqtts`) allows multiple concurrent clients per user
as long as each connection authenticates with a freshly-minted cert pair
from `/iot-auth/app/certification`. So we could:

- Log in as the user via the same REST flow as `hassio-ecoflow-cloud`
- Get an MQTT cert pair
- Subscribe to every topic for the user's SHP (the broker doesn't filter
  what *other* clients on the same account publish or receive)
- Edit the schedule in the mobile app
- Watch the resulting MQTT traffic

## What surfaced

The mobile app, on saving a schedule change, publishes a single message to:

```
/app/{user_id}/{shp_serial}/thing/property/set
```

with a payload like:

```json
{
  "from": "iOS",
  "operateType": "TCP",
  "id": "663389195",
  "lang": "en-us",
  "params": {
    "cfg": {
      "param": {
        "lowBattery": 85,
        "chChargeWatt": 500,
        "chSta": [1, 1],
        "hightBattery": 90
      },
      "comCfg": {
        "timeRange": { ... opaque calendar window ... },
        "timeScale": [255, 255, 255, 255, 255, 3, 0, ... ],
        "isCfg": 1,
        "type": 1,
        "isEnable": 1,
        "setTime": { ... wall-clock timestamp ... }
      }
    },
    "id": 81,
    "cmdSet": 11,
    "cfgIndex": 0
  },
  "version": "1.0"
}
```

The SHP acknowledges with a short reply on
`/app/{user_id}/{shp_serial}/thing/property/set_reply`:

```json
{"id": 663389195, "code": "0", "data": {"sta": 0, "ack": 0, "id": 81, "cmdSet": 11}}
```

`code:"0" sta:0 ack:0` = success.

That's it. **Plain JSON.** No protobuf decoding required. (The Delta Pro
telemetry topics *do* use protobuf for some message types — but the SHP
schedule write happens to be JSON.)

## The key insight on `timeScale`

The `timeScale` field is the only non-obvious part of the payload:

```
[255, 255, 255, 255, 255, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
```

18 bytes = 144 bits. 144 bits = 24 hours × 6 ten-minute slots per hour.
First 42 bits set (5×0xFF + 0x03) = 42 × 10 min = 420 min = 7 h = 00:00–07:00.

So `timeScale` is a 10-minute-resolution bitmask of when the schedule is
active during the day. Confirmed by matching the mobile-app-visible slot
boundary against bit positions across multiple captured payloads.

## Dead ends (so others don't repeat them)

- `hassio-ecoflow-cloud` Discussion
  [#212](https://github.com/tolwi/hassio-ecoflow-cloud/discussions/212) —
  SHP+Delta-Pro schedule write requested since Feb 2024, unimplemented as
  of Aug 2025.
- `foxthefox/ioBroker.ecoflow-mqtt` — supports SHP for monitoring +
  AC-channel switching, no schedule write commands.
- `TarasKhust/ecoflow-api-mqtt` (uses the *official* EcoFlow Developer
  API at `developer.ecoflow.com`) — does not support SHP as a device class.
- `tess1o/go-ecoflow` — has rich SHP bindings including `SetLoadChannelControl`,
  `SetChannelCurrentConfiguration`, `SetEspMode`, `PushStandByCharging
  DischargingParameters(forceChargeHigh, discLower)`. The last one is *not*
  rate control — it's just SOC thresholds. No schedule writes.
- Setting `number.{sn}_ac_charging_power` via the HA integration — accepted
  at the HA layer, silently ignored by the SHP's schedule override.
- Trying to send the `/get` request to read current schedule — works *some*
  of the time; appears to require recent mobile-app activity to "wake" the
  SHP's listener. Treat `/get_reply` as best-effort.

## A handful of gotchas

- **`client_id` is enforced.** EcoFlow's broker rejects (CONNACK rc=5)
  any MQTT client_id that doesn't match the exact pattern
  `ANDROID_{cert_account_tail}_{user_id}`. Adding any suffix is fatal.
- **Field name typo.** The cap field is `hightBattery` (not "height"
  and not "high"). It's in the protocol; don't auto-correct it.
- **`set_reply` is occasionally dropped.** The SHP applies the schedule
  but the broker doesn't deliver the ack. Library retries once with a
  fresh `msg_id` (the broker filters duplicates by id).
- **EU vs Global region.** EU accounts use `api-e.ecoflow.com` +
  `mqtt-e.ecoflow.com`. US/Asia use unprefixed `api.ecoflow.com`. The
  login call returns a `redirectUrl` in the error case so you can pick
  the right one — the library tries both with EU first.

## What this opens up

With remote schedule control, an HA-side optimiser can now:

1. Read tomorrow's solar forecast (e.g. Solcast HACS integration).
2. Read the last N days of household demand history.
3. Read the current battery SOC.
4. Compute the optimal charging rate that fills the batteries to the
   target SOC during the cheap-rate window, no more, no less.
5. Publish that rate (+ cap) to the SHP via this library.
6. Repeat nightly.

The author runs this as a `launchd` job at 00:05 daily on macOS. The
schedule for the night that *just* started gets updated within seconds
of the cheap-rate window opening; the SHP picks up the new rate
mid-window seamlessly.

### Five refinements worth borrowing

Five things the author ended up doing in the reference optimiser that
the library itself doesn't need to know about, but which materially
improved nightly outcomes — pattern-level lessons others might want
to copy:

1. **Persistent demand history.** Home Assistant's recorder defaults
   to a 10-day window and the `total_increasing` extraction can
   silently return no usable rows depending on how the source sensor
   reports state. The author's optimiser originally fell back to a
   hardcoded `14 kWh/day` assumption — which turned out to be ~30 %
   below actual demand and consistently undersized the nightly
   charge. The fix was a tiny SQLite collector that writes one row
   per night (`date, grid_import_kwh, battery_out_kwh, solar_kwh,
   ...`) and an optimiser that reads from that DB first, falling
   back to the HA path only if the DB is missing. Once 10+ nights of
   real data accumulated the planning got noticeably more accurate.

2. **Multiplicative demand contingency.** The historical baseline is
   a *mean*. Half of nights, by definition, exceed the mean. A
   simple `daily_demand *= 1.15` after computing the baseline covers
   most of those above-average nights without significantly
   over-charging on the typical ones. On the author's data, 15 %
   covered ~65 % of nights; 20 % covered ~83 %. Beyond that the long
   tail (occasional 28–30 kWh days) isn't worth buffering for — the
   ratio of cheap-rate overcharge to expensive-rate undercharge
   gets unfavourable.

3. **Self-calibrating Solcast confidence.** Solcast forecasts a site's
   theoretical generation under perfect alignment, but the real-world
   ratio of (actual ÷ forecast) is heavily site-dependent — shading,
   minor azimuth/tilt mis-configuration, soiling, inverter clipping.
   The author's pergola turned out to deliver only ~46 % of forecast
   on the median day (24 days, IQR ~0.40–0.55), which is a long way
   from the hardcoded 0.85 the optimiser was using. After 5+ days of
   real `(forecast, actual)` pairs accumulate in the SQLite store,
   the optimiser reads them and uses the rolling median over the
   last 21 days as its Solcast multiplier; falls back to the
   hardcoded default only while the sample is too thin. The clamp
   `[0.30, 1.10]` keeps a single bad day from skewing the multiplier.

4. **Treat a missing `set_reply` as "unconfirmed," not "failed."**
   This refinement has now been corrected twice; v0.1.3 wrongly
   blamed a daily quota counter, v0.1.4 wrongly blamed a
   sliding-window rate limit. The actually-empirical truth (verified
   2026-05-31) is simpler and more important: **the publish itself
   is reliable, but the `set_reply` ack is not.**
   - On the night of 2026-05-30 the reference optimiser published
     `chChargeWatt = 300` via MQTT. `paho rc = 0`, no `set_reply`
     within 30 s, optimiser logged "FAILED" and sent the
     "set 300 W/unit manually" notification.
   - Next morning the owner opened the EcoFlow app and the schedule
     was already showing 300 W. The publish had been applied. The
     ack just never came back.
   - The May 27 "burst failures" that motivated v0.1.4's rate-limit
     framing were almost certainly the same phenomenon — successful
     publishes with dropped acks, mis-classified as failures.
   So the operational guidance is:
   - **Don't equate "no `set_reply`" with "write failed."** Treat
     it as a third state: *publish accepted by broker, ack missing,
     SHP almost certainly applied it.* The reference optimiser now
     reports `confirmed` / `unconfirmed` / `failed` based on `paho.rc`
     and `set_reply` arrival; only the `failed` state (paho rejected
     at the broker layer) triggers the HA fallback and a
     "set manually" notification.
   - **Still publish gently.** One attempt per scheduled run,
     30-second timeout, no retry. Bursts of publishes still
     correlate with dropped acks empirically, even if they don't
     prevent the writes themselves from being applied — so no point
     spamming the broker.
   - **`quota_requests` on the SHP status sensor is a monotonic
     lifetime counter** from the HA integration's polling; it is not
     a daily quota, has no reset, and should not be used to gate
     publishing. The reference optimiser still surfaces it in its
     notification as a diagnostic data point only.

5. **Verify via `ac_in_power` telemetry, not via `set_reply` alone.**
   Given the broker's unreliable `set_reply` (refinement #4), an
   "unconfirmed" status by itself is uncomfortable — we believe the
   publish worked but have no proof. The reference optimiser now
   takes a second swing at confirmation by reading the Delta Pros'
   `ac_in_power` HA sensors 90 seconds after publishing. If the
   reading is within ±75 W of `chChargeWatt` on both units, we have
   *physical* evidence the SHP applied the schedule — and that
   evidence is independent of any MQTT ack arriving. The optimiser
   uses this to promote an `unconfirmed` status to `confirmed`. The
   verification only runs during the charging window (when AC input
   is active); outside it, the result is `skipped` rather than a
   false negative. This pattern is also cheaper than a chained
   `/get` round-trip and uses telemetry we're already collecting.

All five refinements land entirely in the optimiser's own code; the
library just gets a `chChargeWatt` value to publish.
