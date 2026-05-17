#!/usr/bin/env python3
"""
examples/set_schedule.py
────────────────────────
Publish a single SHP schedule update via EcoFlow's MQTT broker.

Usage:
    python3 set_schedule.py --watts 500 --cap 90 --shp-sn SP10XXXXXXXXXXXXX

Reads EcoFlow credentials from ~/.ecoflow_creds:
    EMAIL=your-ecoflow-email@example.com
    PASSWORD=your-ecoflow-password

Set mode 600 (chmod 600 ~/.ecoflow_creds). Never paste credentials in shell
history or commit them to a repo.

What this sets in the SHP's schedule (cfgIndex=0):
  - chChargeWatt = --watts            (per-Delta-Pro charging rate, W)
  - hightBattery = --cap              (charge cap, SOC %)
  - lowBattery   = --low (default 85) (discharge floor, SOC %)
  - timeScale    = 00:00–07:00 active by default; override with --slot

The slot --slot HH:MM-HH:MM lets you reshape the bitmask; default keeps the
existing UK off-peak window.
"""
import argparse
import os
import sys
import pathlib

# Make the library importable when running from examples/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import ecoflow_shp_mqtt as ef


CREDS_PATH_DEFAULT = os.path.expanduser("~/.ecoflow_creds")


def read_creds(path: str) -> tuple[str, str]:
    if not os.path.isfile(path):
        sys.exit(f"FATAL: {path} not found. See README for setup.")
    if os.stat(path).st_mode & 0o077:
        sys.exit(f"FATAL: {path} is group/world-readable. Run: chmod 600 {path}")
    email = pw = None
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().upper(); v = v.strip()
        if k == "EMAIL": email = v
        elif k == "PASSWORD": pw = v
    if not email or not pw:
        sys.exit(f"FATAL: {path} must define EMAIL=... and PASSWORD=...")
    return email, pw


def time_scale_from_slot(slot: str) -> list[int]:
    """Convert 'HH:MM-HH:MM' to an 18-byte (144-bit) 10-minute slot bitmask."""
    a, b = slot.split("-")
    def to_minute(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    start_min = to_minute(a)
    end_min   = to_minute(b)
    if not (0 <= start_min < end_min <= 24 * 60):
        sys.exit(f"FATAL: invalid slot {slot!r}; need 00:00-24:00 with start < end")
    bits = [0] * (24 * 6)
    for slot_idx in range(start_min // 10, end_min // 10):
        bits[slot_idx] = 1
    # Pack 144 bits into 18 bytes, little-endian per-byte bit ordering
    out = []
    for byte_idx in range(18):
        b_val = 0
        for bit_idx in range(8):
            b_val |= bits[byte_idx * 8 + bit_idx] << bit_idx
        out.append(b_val)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Set SHP charging schedule via MQTT.")
    p.add_argument("--watts", type=int, required=True,
                   help="Per-Delta-Pro charging rate in W (200..2900).")
    p.add_argument("--cap",   type=int, required=True,
                   help="Charge cap SOC %% (hightBattery).")
    p.add_argument("--low",   type=int, default=85,
                   help="Discharge floor SOC %% (lowBattery). Default 85.")
    p.add_argument("--shp-sn", required=True,
                   help="Smart Home Panel serial number, e.g. SP10XXXXXXXXXXXXX.")
    p.add_argument("--slot",   default="00:00-07:00",
                   help="Charging slot HH:MM-HH:MM. Default 00:00-07:00.")
    p.add_argument("--creds",  default=CREDS_PATH_DEFAULT,
                   help=f"Credentials file path. Default {CREDS_PATH_DEFAULT}.")
    p.add_argument("--cfg-index", type=int, default=0,
                   help="Schedule slot index in the SHP (default 0).")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="Seconds to wait for set_reply per attempt.")
    p.add_argument("--retry",   type=int, default=1,
                   help="Retries on missing set_reply.")
    args = p.parse_args()

    if not (200 <= args.watts <= 2900):
        sys.exit(f"FATAL: --watts {args.watts} out of range [200, 2900]")
    if not (50 <= args.cap <= 100):
        sys.exit(f"FATAL: --cap {args.cap} out of range [50, 100]")
    if not (0 <= args.low <= 30):
        sys.exit(f"FATAL: --low {args.low} out of range [0, 30]")

    email, pw = read_creds(args.creds)
    print(f"=== SHP schedule set ===")
    print(f"  watts={args.watts}  cap={args.cap}%  low={args.low}%  slot={args.slot}")

    api_host, token, user_id = ef.login(email, pw)
    print(f"  ✓ login  user_id={user_id}")
    creds = ef.get_mqtt_credentials(api_host, token, user_id)
    print(f"  ✓ broker {creds.url}:{creds.port}")

    payload = ef.build_schedule_payload(
        charge_watt = args.watts,
        high_battery= args.cap,
        low_battery = args.low,
        time_scale  = time_scale_from_slot(args.slot),
        cfg_index   = args.cfg_index,
    )
    result = ef.publish_and_verify(
        creds, user_id, args.shp_sn, payload,
        timeout=args.timeout, retry=args.retry, verbose=True,
    )
    if result["success"]:
        print("  🟢 SHP accepted (set_reply code=0).")
        return 0
    print(f"  ✗ FAILED: {result['error']}")
    if result["reply"]:
        import json as _j
        print(_j.dumps(result["reply"], indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
