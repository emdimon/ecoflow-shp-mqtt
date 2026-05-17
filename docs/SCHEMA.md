# SHP Schedule Payload Schema

Reference for the `cmdSet:11, id:81` schedule-config message exchanged
between the EcoFlow mobile app and the Smart Home Panel (SP10, gen 1)
over the EcoFlow private MQTT broker.

## Topics

```
publish (set):     /app/{user_id}/{shp_serial}/thing/property/set
subscribe (ack):   /app/{user_id}/{shp_serial}/thing/property/set_reply
publish (read):    /app/{user_id}/{shp_serial}/thing/property/get
subscribe (state): /app/{user_id}/{shp_serial}/thing/property/get_reply
subscribe (tele):  /app/device/property/{shp_serial}
```

## Write payload (set)

```json
{
  "from":        "<sender-label>",
  "operateType": "TCP",
  "id":          "<random-9-digit-msg-id>",
  "lang":        "en-us",
  "params": {
    "cfg": {
      "param": {
        "lowBattery":   85,
        "chChargeWatt": 500,
        "chSta":        [1, 1],
        "hightBattery": 90
      },
      "comCfg": {
        "timeRange": { ... },
        "timeScale": [255, 255, 255, 255, 255, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "isCfg":     1,
        "type":      1,
        "isEnable":  1,
        "setTime":   { ... }
      }
    },
    "id":       81,
    "cmdSet":   11,
    "cfgIndex": 0
  },
  "version": "1.0"
}
```

### Outer envelope

| Field | Type | Notes |
|---|---|---|
| `from` | string | Free-form sender label. Mobile app sends `"iOS"` / `"Android"`. Broker doesn't enforce the value. |
| `operateType` | string | Always `"TCP"` for control messages. |
| `id` | string | Random 9-digit id. Used to correlate with `set_reply`. |
| `lang` | string | UI language hint, typically `"en-us"`. |
| `version` | string | Always `"1.0"`. |

### `params.cfg.param` — the actual settable values

| Field | Type | Range | Meaning |
|---|---|---|---|
| `lowBattery` | int | 0–30 | Discharge floor SOC %. SHP won't discharge below this. |
| `chChargeWatt` | int | 200–2900 | **Per-Delta-Pro AC charging rate in watts.** Stepped to nearest 100 W on most firmwares. |
| `chSta` | [int, int] | each 0 or 1 | Per-Delta-Pro active flag. `[1, 1]` = charge both. `[1, 0]` = unit 1 only. |
| `hightBattery` | int | 50–100 | Charge cap SOC %. **Note typo `hight` not `high`.** |

### `params.cfg.comCfg` — slot timing and validity

| Field | Type | Meaning |
|---|---|---|
| `timeRange` | object | Calendar validity window of the schedule. Contains `startTime`, `endTime` (date objects), `mode1` (weekday selector), `isCfg`, `timeMode`, `isEnable`. Treat as opaque template — set once, don't touch. |
| `timeScale` | array[18 int] | **Time-of-day bitmask. See below.** |
| `isCfg` | int | Always `1` when actively configured. |
| `type` | int | `1` = charging task, `2` = discharging task. |
| `isEnable` | int | `1` to enable, `0` to disable this schedule slot. |
| `setTime` | object | Wall-clock timestamp of *when* this write was made. Contains `hour`, `min`, `sec`, `day`, `week`, `month`, `year`. The SHP appears to use this for ordering / staleness checks; safest to set to "now". |

### `timeScale` — the bitmask

`timeScale` is a packed bit array, **18 bytes = 144 bits**, where each
bit represents a 10-minute slot in a 24-hour day:

- Bit `N` of the packed array represents minutes `[N * 10, N * 10 + 10)`.
- Bits are packed **little-endian within each byte** (bit 0 = least
  significant of byte 0 = `00:00–00:10`).

Example: `00:00–07:00` (7 hours = 42 ten-minute slots) sets bits 0..41:

```
bytes: [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x03, 0x00, 0x00, ...]
        ^---- bits 0..39 ----^   ^--bits 40-41
```

See `examples/set_schedule.py:time_scale_from_slot()` for an HH:MM-HH:MM
→ bitmask converter.

### `params.cfgIndex`

Index of the schedule slot to write. The SHP supports multiple
named schedule slots (visible in the mobile-app UI as separate
"Charge task" entries). `cfgIndex=0` is the first slot.

## Reply payload (set_reply)

```json
{
  "id":          663389195,
  "version":     "1.0",
  "operateType": "TCP",
  "code":        "0",
  "data": {
    "sta":      0,
    "cfgIndex": 0,
    "cmdSet":   11,
    "ack":      0,
    "id":       81
  }
}
```

| Field | Value on success | Meaning |
|---|---|---|
| `code` | `"0"` | Broker-level: command accepted. |
| `data.sta` | `0` | SHP-level: schedule applied. |
| `data.ack` | `0` | SHP-level: protocol acknowledgement. |
| `data.id` | matches your `params.id` (i.e. 81 for schedule config) | echoes the command type |
| `data.cfgIndex` | matches your `params.cfgIndex` | echoes the slot |
| `id` | matches your outer envelope `id` | correlates the reply to your request |

If `code != "0"` or any of `sta`/`ack` ≠ 0, the SHP did *not* accept the
schedule. The reply doesn't include a human-readable error message;
diagnose by capturing what the mobile app sends for an equivalent
operation and diffing.

## Telemetry payload (incoming)

The SHP publishes telemetry on `/app/device/property/{shp_serial}` every
second or so. Many message types use this channel, distinguished by
`params.cmdSet` and `params.id`. The most useful for monitoring active
schedules is:

```json
{
  "params": {
    "rtc":   "2026-5-17",
    "watth": [[491, 499, 491, 492, 491, 492, 493, 0, ... 0],
              [491, 500, 492, 492, 491, 491, 490, 0, ... 0]],
    "cmdSet": 11,
    "id":     50
  },
  "version":   "1.0",
  "cmdId":     0,
  "cmdFunc":   0,
  "id":        2270119024221127942,
  "addr":      0,
  "timestamp": 1779012636938
}
```

`watth` is a 2-element array (one per Delta Pro) of 24 hourly average
watt readings for the current day. Above shows ~492 W per unit during
hours 0–6 (00:00–06:59) — the charging window — and 0 W during daytime
(no grid charge happening). Useful for verifying that a scheduled rate
was actually drawn from grid.
