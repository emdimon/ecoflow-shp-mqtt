# ecoflow-shp-mqtt

Remote control of the EcoFlow Smart Home Panel (gen 1, **SP10**) charging schedule
via the EcoFlow private MQTT API. Reverse-engineered, plain JSON, no protobuf.

If you own an SHP + Delta Pro setup and you've tried to automate the daily
charging rate from Home Assistant or anywhere else outside the EcoFlow mobile
app, you've probably hit this wall:

> Whatever you set on the Delta Pros' AC charging power, the SHP silently
> overrides it with whatever rate is in the SHP schedule. The HA integrations
> (`hassio-ecoflow-cloud` etc.) expose the SHP as read-only — schedule writes
> are not implemented anywhere public as of mid-2026.

This repo is the missing piece: a 200-line library and three example scripts
that let you publish a new charging rate, charge cap, and time slot directly
to the SHP's MQTT control topic. **It is the same call the EcoFlow mobile app
makes** when you edit the schedule on your phone.

## Status

- ✅ Set charging rate (`chChargeWatt`) — verified
- ✅ Set charge cap (`hightBattery` — *sic*, the typo is in the EcoFlow protocol) — verified
- ✅ Set discharge floor (`lowBattery`) — verified
- ✅ Set per-Delta-Pro active flag (`chSta`) — verified
- ✅ Set time slot (`timeScale` 18-byte bitmask, 10-min resolution) — verified
- ✅ Verify success via `set_reply` (`code:"0"`, `sta:0`, `ack:0`) — verified
- ✅ Read SHP state via `/get` → `/get_reply` — works (intermittent; see Caveats)
- ⚠️ Last verified 2026-05-17 against a UK EU-region account and SHP gen-1 firmware
  as shipped that month.

See [docs/DISCOVERY.md](docs/DISCOVERY.md) for the full reverse-engineering story
and [docs/SCHEMA.md](docs/SCHEMA.md) for the complete payload field reference.

## Install

```sh
git clone https://github.com/emdimon/ecoflow-shp-mqtt.git
cd ecoflow-shp-mqtt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Credentials

Create `~/.ecoflow_creds` with your EcoFlow account login (the same one you
use in the mobile app):

```sh
umask 077
cat > ~/.ecoflow_creds <<'EOF'
EMAIL=you@example.com
PASSWORD=your-ecoflow-password
EOF
chmod 600 ~/.ecoflow_creds
```

The scripts refuse to run unless the file is mode 600. Credentials never
appear on the command line, in logs, or in MQTT topic names.

## Quick start

### Set a new charging schedule

```sh
python3 examples/set_schedule.py \
    --shp-sn SP10XXXXXXXXXXXXX \
    --watts 500 \
    --cap 90
```

Sets `chChargeWatt=500 W per Delta Pro`, `hightBattery=90%`,
slot `00:00–07:00` (default). On success prints `🟢 SHP accepted`.

Use a custom slot:

```sh
python3 examples/set_schedule.py --shp-sn SP10... --watts 800 --cap 85 \
    --slot 23:00-06:00
```

### Inspect what your EcoFlow app actually sends

Run the capture and edit the SHP schedule in the mobile app — the JSON
payload it publishes is logged to `./captures/`. Useful for confirming
field semantics on newer firmwares or different SHP models.

```sh
python3 examples/capture.py --shp-sn SP10XXXXXXXXXXXXX --dp-sn DCKBZ8XXXX
# Ctrl+C to stop
```

### Read the SHP's currently-stored schedule

```sh
python3 examples/get_schedule.py --shp-sn SP10XXXXXXXXXXXXX
```

## Using as a library

```python
import ecoflow_shp_mqtt as ef

api_host, token, user_id = ef.login("you@example.com", "your-password")
creds = ef.get_mqtt_credentials(api_host, token, user_id)

payload = ef.build_schedule_payload(
    charge_watt = 500,       # W per Delta Pro
    high_battery= 90,        # SOC cap %
    low_battery = 85,        # SOC floor %
)

result = ef.publish_and_verify(creds, user_id, "SP10XXXXXXXXXXXXX", payload)
assert result["success"], result["error"]
```

## Topic and payload reference

**Write topic:** `/app/{user_id}/{shp_serial}/thing/property/set`
**Reply topic:** `/app/{user_id}/{shp_serial}/thing/property/set_reply`
**Identifier:** `cmdSet:11`, `id:81`, `cfgIndex:0` (or another slot index)

Full payload schema → [docs/SCHEMA.md](docs/SCHEMA.md).

## How this was found

A two-hour reverse-engineering session in May 2026 using passive MQTT
sniffing. The full story (including dead ends — protobuf was thankfully
not needed; the `hassio-ecoflow-cloud` integration was a dead end for
write capability; `tess1o/go-ecoflow` has the most comprehensive SHP
Go bindings but still doesn't expose schedule writes) is in
[docs/DISCOVERY.md](docs/DISCOVERY.md).

## Caveats

- **Reverse-engineered private protocol.** EcoFlow can change the wire
  format in any mobile-app update; this library will then need re-
  capturing and re-validating. The `examples/capture.py` script is the
  tool for that.
- **The `/get_reply` is intermittent.** In testing the SHP sometimes
  returned the full schedule blob promptly, sometimes not at all. The
  set-and-verify path (publish → `set_reply`) is much more reliable
  than the read-back path.
- **The broker silently drops some `set_reply` messages.** The library
  retries once by default with a fresh `msg_id`; tune via `retry=N`.
- **Region matters.** This is tested against `api-e.ecoflow.com` (EU).
  US/Asia accounts should hit `api.ecoflow.com`. The library falls
  back automatically; if a third region exists, add it to `API_HOSTS_DEFAULT`.
- **Only SHP gen 1 (`SP10*`).** SHP 2 / SHP 3 / Ocean panels use
  different protocols; this library does not address them.
- **Your account, your device, your responsibility.** Sending bad
  payloads to your SHP could leave it in an unexpected state. Always
  start with `examples/capture.py` to verify what your app sends today,
  and prefer `examples/set_schedule.py` (which only touches fields it
  has been told to) over building raw payloads.

## Licence

[MIT](LICENSE) — do whatever you want, no warranty.

## Contributing

Issues, PRs, and capture logs from other SHP firmwares / regions all
welcome. If you have an SHP 2 and can run `examples/capture.py` while
editing its schedule, the resulting payload(s) would be very useful to
the project even if you don't want to write code.
