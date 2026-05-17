#!/usr/bin/env python3
"""
examples/capture.py
───────────────────
Passively subscribe to the EcoFlow MQTT broker and log every message on the
SHP and Delta Pro topics. Use this to inspect the JSON payloads the EcoFlow
mobile app sends, or to verify that the SHP is publishing what you expect.

Usage:
    python3 capture.py --shp-sn SP10XXXXXXXXXXXXX [--dp-sn DCKBZ8XXXX]...

Reads EcoFlow credentials from ~/.ecoflow_creds (see set_schedule.py).
Output goes to a timestamped file in ./captures/.

What this DOES NOT do: publish anything. It is read-only by design.
"""
import argparse
import base64
import binascii
import datetime
import json
import os
import pathlib
import ssl
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import ecoflow_shp_mqtt as ef
import paho.mqtt.client as mqtt

CREDS_PATH_DEFAULT = os.path.expanduser("~/.ecoflow_creds")


def read_creds(path: str) -> tuple[str, str]:
    if not os.path.isfile(path):
        sys.exit(f"FATAL: {path} not found")
    if os.stat(path).st_mode & 0o077:
        sys.exit(f"FATAL: {path} not mode 600")
    e = p = None
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().upper() == "EMAIL": e = v.strip()
        elif k.strip().upper() == "PASSWORD": p = v.strip()
    if not e or not p:
        sys.exit(f"FATAL: {path} missing EMAIL or PASSWORD")
    return e, p


def main() -> int:
    ap = argparse.ArgumentParser(description="EcoFlow MQTT traffic capture.")
    ap.add_argument("--shp-sn", required=True, help="Smart Home Panel serial.")
    ap.add_argument("--dp-sn",  action="append", default=[],
                    help="Delta Pro serial (repeatable). Subscribes to telemetry only.")
    ap.add_argument("--creds",  default=CREDS_PATH_DEFAULT)
    ap.add_argument("--out-dir", default="./captures")
    args = ap.parse_args()

    email, pw = read_creds(args.creds)
    api_host, token, uid = ef.login(email, pw)
    print(f"  ✓ login user_id={uid}")
    creds = ef.get_mqtt_credentials(api_host, token, uid)
    print(f"  ✓ broker {creds.url}:{creds.port}")

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"ecoflow_capture_{ts}.log"
    out = out_path.open("w", buffering=1)
    out.write(f"# capture started {ts}  user_id={uid}\n")
    out.write(f"# broker={creds.url}:{creds.port}\n")
    out.write(f"# shp_sn={args.shp_sn}  dp_sns={args.dp_sn}\n\n")

    client_id = ef.make_client_id(creds.certificate_account, uid)
    c = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    c.username_pw_set(creds.certificate_account, creds.certificate_password)
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(_c, _u, _f, rc, *_a):
        if rc != 0:
            print(f"FATAL: CONNACK rc={rc}"); sys.exit(2)
        # All schedule activity (write + reply) for the SHP
        for pat in (
            f"/app/device/property/{args.shp_sn}",
            f"/app/{uid}/{args.shp_sn}/thing/property/set",
            f"/app/{uid}/{args.shp_sn}/thing/property/set_reply",
            f"/app/{uid}/{args.shp_sn}/thing/property/get",
            f"/app/{uid}/{args.shp_sn}/thing/property/get_reply",
            f"/app/{uid}/{args.shp_sn}/thing/event/post",
        ):
            _c.subscribe(pat, qos=1)
            print(f"  subscribed: {pat}")
        # Delta Pro telemetry only (we don't expect schedule writes here)
        for dp_sn in args.dp_sn:
            for pat in (
                f"/app/device/property/{dp_sn}",
                f"/app/{uid}/{dp_sn}/thing/property/set",
                f"/app/{uid}/{dp_sn}/thing/property/set_reply",
                f"/app/{uid}/{dp_sn}/thing/event/post",
            ):
                _c.subscribe(pat, qos=1)
                print(f"  subscribed: {pat}")

    def on_message(_c, _u, msg):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = msg.payload
        try: decoded = json.loads(payload.decode())
        except Exception: decoded = None
        block = [
            f"--- {now}  topic={msg.topic}  len={len(payload)}  qos={msg.qos} ---",
            f"hex:    {binascii.hexlify(payload).decode()}",
            f"base64: {base64.b64encode(payload).decode()}",
        ]
        if decoded is not None:
            block.append(f"json:   {json.dumps(decoded, separators=(',',':'))}")
        block.append("")
        block_text = "\n".join(block)
        print(block_text)
        out.write(block_text + "\n"); out.flush()

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(creds.url, creds.port, keepalive=60)
    print(f"  capture → {out_path}\n  Ctrl+C to stop\n")
    try:
        c.loop_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        try: c.disconnect()
        except Exception: pass
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
