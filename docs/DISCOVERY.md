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
2. Read the last N days of household demand from HA history.
3. Read the current battery SOC.
4. Compute the optimal charging rate that fills the batteries to the
   target SOC during the cheap-rate window, no more, no less.
5. Publish that rate (+ cap) to the SHP via this library.
6. Repeat nightly.

The author runs this as a `launchd` job at 00:05 daily on macOS. The
schedule for the night that *just* started gets updated within seconds
of the cheap-rate window opening; the SHP picks up the new rate
mid-window seamlessly.
