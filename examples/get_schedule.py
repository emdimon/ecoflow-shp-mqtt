#!/usr/bin/env python3
"""
examples/get_schedule.py
────────────────────────
Read the SHP's currently-stored schedule by publishing a /get and listening
for the /get_reply. Useful for verifying what the SHP actually has after
a write, independent of any caching in the EcoFlow mobile app's UI.

Usage:
    python3 get_schedule.py --shp-sn SP10XXXXXXXXXXXXX

Caveat: in our testing the SHP's /get response was reliable only when the
mobile app had recently been opened on the same account. If you get no
reply, try (a) opening the EcoFlow app to "wake" the SHP's listener,
(b) running examples/capture.py while editing the schedule in the app
to see the equivalent payload directly.
"""
import argparse
import json
import os
import pathlib
import random
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
    return e, p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp-sn", required=True)
    ap.add_argument("--creds",  default=CREDS_PATH_DEFAULT)
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    email, pw = read_creds(args.creds)
    api_host, token, uid = ef.login(email, pw)
    print(f"  ✓ login user_id={uid}")
    creds = ef.get_mqtt_credentials(api_host, token, uid)

    msg_id      = str(random.randint(100_000_000, 999_999_999))
    get_topic   = f"/app/{uid}/{args.shp_sn}/thing/property/get"
    reply_topic = f"/app/{uid}/{args.shp_sn}/thing/property/get_reply"
    state       = {"reply": None}

    def on_connect(c, _u, _f, rc, *_a):
        if rc != 0: print(f"connect rc={rc}"); sys.exit(2)
        c.subscribe(reply_topic, qos=1)

    def on_message(_c, _u, msg):
        try: j = json.loads(msg.payload.decode())
        except Exception: return
        if str(j.get("id")) == msg_id:
            state["reply"] = j

    c = mqtt.Client(client_id=ef.make_client_id(creds.certificate_account, uid),
                    protocol=mqtt.MQTTv311)
    c.username_pw_set(creds.certificate_account, creds.certificate_password)
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(creds.url, creds.port, keepalive=60)
    c.loop_start()
    time.sleep(1.0)

    req = {
        "from":        "ecoflow_shp_mqtt",
        "operateType": "TCP",
        "id":          msg_id,
        "lang":        "en-us",
        "params":      {"cmdSet": ef.CMD_SET_SHP, "id": ef.CMD_ID_SCHEDULE_CONFIG, "cfgIndex": 0},
        "version":     "1.0",
    }
    c.publish(get_topic, json.dumps(req, separators=(",", ":")), qos=1)
    print(f"  /get sent, waiting {args.timeout}s for /get_reply...")

    deadline = time.time() + args.timeout
    while time.time() < deadline and state["reply"] is None:
        time.sleep(0.1)
    c.loop_stop(); c.disconnect()

    if state["reply"] is None:
        print("  ✗ no reply")
        return 1
    print(json.dumps(state["reply"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
