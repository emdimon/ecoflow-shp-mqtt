"""
ecoflow_shp_mqtt.py
───────────────────
Reverse-engineered control of the EcoFlow Smart Home Panel (SP10, gen 1)
charging schedule via the EcoFlow private MQTT API.

Provides the four primitives needed to read or write an SHP's daily
charging schedule from outside the EcoFlow mobile app:

  - login(email, password) → (api_host, token, user_id)
  - get_mqtt_credentials(api_host, token, user_id) → dict
  - build_schedule_payload(charge_watt, high_battery, ...) → dict
  - publish_and_verify(mqtt_creds, user_id, shp_sn, payload, ...) → dict

See README.md for the discovery story and docs/SCHEMA.md for the full
payload field reference. No protobuf required — the SHP write protocol
is plain JSON over MQTT.

CAVEAT EMPTOR: this is reverse-engineered. EcoFlow can change the wire
format in any mobile-app update. Last verified: 2026-05-17 against a UK
account on api-e.ecoflow.com / mqtt-e.ecoflow.com:8883 with a gen-1 SP10
panel firmware as shipped that month.
"""
from __future__ import annotations
import base64
import json
import random
import ssl
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import requests
import paho.mqtt.client as mqtt

__version__ = "0.1.11"


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

API_HOSTS_DEFAULT = (
    "https://api-e.ecoflow.com",   # EU
    "https://api.ecoflow.com",     # global fallback
)

# SHP schedule command identifiers (cmdSet:11, id:81 = "set/get charging task config")
CMD_SET_SHP            = 11
CMD_ID_SCHEDULE_CONFIG = 81

# 18-byte (144-bit) time-of-day bitmask, 10-minute slot resolution over 24h.
# Bit N corresponds to minutes (N*10)..(N*10+10). Set bit = "active in this slot".
# Example: 00:00–07:00 = bits 0..41 = [0xFF,0xFF,0xFF,0xFF,0xFF,0x03,0,...]
TIME_SCALE_LENGTH = 18

# Verified working time-range "container" fields. Their semantics aren't fully
# understood; the app just stores values like these. Treat as opaque template.
DEFAULT_TIME_RANGE = {
    "startTime": {"hour": 0, "min": 0, "sec": 0, "day": 26, "week": 1, "month": 11, "year": 2023},
    "mode1":     {"thur": 0, "tues": 0, "sat": 0, "fri": 0, "sun": 0, "mon": 0, "wed": 0},
    "endTime":   {"hour": 23, "min": 59, "sec": 59, "day": 16, "week": 1, "month": 5, "year": 2027},
    "isCfg":     1,
    "timeMode":  0,
    "isEnable":  1,
}


# ─────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────

class EcoFlowAuthError(RuntimeError):
    """Raised when the EcoFlow REST auth flow fails."""

class EcoFlowMqttError(RuntimeError):
    """Raised when the EcoFlow MQTT broker rejects a connection or operation."""


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MqttCredentials:
    url: str
    port: int
    certificate_account: str
    certificate_password: str
    protocol: str = "mqtts"


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

def login(email: str, password: str, api_hosts: Iterable[str] = API_HOSTS_DEFAULT) -> tuple[str, str, str]:
    """
    POST /auth/login against EcoFlow's REST API.

    Returns (api_host, token, user_id). The api_host returned is whichever
    of api_hosts accepted the login — use the SAME host for the subsequent
    /iot-auth/app/certification call (regional broker routing).
    """
    payload = {
        "email":    email,
        "password": base64.b64encode(password.encode()).decode(),
        "scene":    "IOT_APP",
        "userType": "ECOFLOW",
    }
    headers = {"lang": "en_US", "content-type": "application/json"}
    last_err: Optional[str] = None
    for host in api_hosts:
        try:
            r = requests.post(f"{host}/auth/login", json=payload, headers=headers, timeout=15)
            r.raise_for_status()
            j = r.json()
            if j.get("code") in ("0", 0, None):
                return host, j["data"]["token"], str(j["data"]["user"]["userId"])
            last_err = f"{host}: code={j.get('code')} message={j.get('message')}"
        except Exception as e:  # noqa: BLE001 — surface as auth error
            last_err = f"{host}: {e}"
    raise EcoFlowAuthError(f"login failed: {last_err}")


def get_mqtt_credentials(api_host: str, token: str, user_id: str) -> MqttCredentials:
    """GET /iot-auth/app/certification. Returns short-lived MQTT broker credentials."""
    r = requests.get(
        f"{api_host}/iot-auth/app/certification",
        headers={"lang": "en_US", "authorization": f"Bearer {token}"},
        params={"userId": user_id},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json().get("data") or {}
    try:
        return MqttCredentials(
            url=d["url"],
            port=int(d["port"]),
            certificate_account=d["certificateAccount"],
            certificate_password=d["certificatePassword"],
            protocol=d.get("protocol", "mqtts"),
        )
    except KeyError as e:
        raise EcoFlowAuthError(f"/iot-auth/app/certification missing field {e}") from None


def make_client_id(certificate_account: str, user_id: str) -> str:
    """
    Build an MQTT client_id that EcoFlow's broker will accept.

    Empirical: the broker rejects (CONNACK rc=5 "not authorised") any
    client_id that doesn't match the exact pattern below. Adding a
    suffix (e.g. `_capture`) is enough to be refused.
    """
    tail = certificate_account.split("-", 1)[1] if "-" in certificate_account else certificate_account
    return f"ANDROID_{tail}_{user_id}"


# ─────────────────────────────────────────────────────────────
# Schedule payload
# ─────────────────────────────────────────────────────────────

def build_schedule_payload(
    charge_watt: int,
    high_battery: int,
    *,
    low_battery: int = 85,
    ch_sta: tuple[int, int] = (1, 1),
    time_scale: Optional[list[int]] = None,
    time_range: Optional[dict] = None,
    set_time: Optional[dict] = None,
    msg_id: Optional[str] = None,
    cfg_index: int = 0,
    from_label: str = "ecoflow_shp_mqtt",
) -> dict:
    """
    Build a cmdSet:11/id:81 SHP schedule-update payload.

    Required:
        charge_watt   - per-Delta-Pro AC charging rate (W). Hardware accepts
                        values in a stepped range (verified working: 200–2900).
        high_battery  - SHP charge cap (SOC %). Charging stops at this level.

    Optional:
        low_battery   - SHP discharge floor (SOC %). Default 85.
        ch_sta        - per-Delta-Pro active flag, e.g. (1,1) for both DPs.
        time_scale    - 18-byte (144-bit) bitmask, 10-min slots over 24h.
                        If None, defaults to 00:00–07:00 active.
        time_range    - opaque container fields, default DEFAULT_TIME_RANGE.
        set_time      - dict with hour/min/sec/day/week/month/year. Default
                        is the current local time.
        msg_id        - outer envelope id (random 9-digit if None).
        cfg_index     - which schedule slot to overwrite (0..N).
        from_label    - free-form sender identifier the broker accepts.
    """
    if time_scale is None:
        # 00:00–07:00 = 42 bits set
        time_scale = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x03] + [0] * 12
    if len(time_scale) != TIME_SCALE_LENGTH:
        raise ValueError(f"time_scale must be exactly {TIME_SCALE_LENGTH} bytes")
    if time_range is None:
        time_range = DEFAULT_TIME_RANGE
    if set_time is None:
        import datetime
        now = datetime.datetime.now()
        set_time = {
            "hour":  now.hour,  "min":   now.minute, "sec":   now.second,
            "day":   now.day,   "week":  now.isoweekday() % 7,
            "month": now.month, "year":  now.year,
        }
    if msg_id is None:
        msg_id = str(random.randint(100_000_000, 999_999_999))

    return {
        "from":        from_label,
        "operateType": "TCP",
        "id":          msg_id,
        "lang":        "en-us",
        "params": {
            "cfg": {
                "param": {
                    "lowBattery":   int(low_battery),
                    "chChargeWatt": int(charge_watt),
                    "chSta":        list(ch_sta),
                    "hightBattery": int(high_battery),   # SIC: typo is in the EcoFlow protocol
                },
                "comCfg": {
                    "timeRange": time_range,
                    "timeScale": list(time_scale),
                    "isCfg":     1,
                    "type":      1,                       # 1 = charging task
                    "isEnable":  1,
                    "setTime":   set_time,
                },
            },
            "id":       CMD_ID_SCHEDULE_CONFIG,
            "cmdSet":   CMD_SET_SHP,
            "cfgIndex": cfg_index,
        },
        "version": "1.0",
    }


# ─────────────────────────────────────────────────────────────
# MQTT publish/verify
# ─────────────────────────────────────────────────────────────

def publish_and_verify(
    mqtt_creds: MqttCredentials,
    user_id: str,
    shp_sn: str,
    payload: dict,
    *,
    timeout: float = 10.0,
    retry: int = 1,
    verbose: bool = False,
) -> dict:
    """
    Connect, publish the schedule payload, and wait for the SHP's set_reply.

    The EcoFlow broker occasionally drops the set_reply even when the SHP
    has applied the update. `retry` controls how many times to re-publish
    with a fresh msg_id if no matching reply arrives within `timeout`.

    Returns a dict:
        {
            "success": bool,
            "reply":   dict | None,    # the matching set_reply JSON
            "error":   str  | None,
        }
    """
    set_topic   = f"/app/{user_id}/{shp_sn}/thing/property/set"
    reply_topic = f"/app/{user_id}/{shp_sn}/thing/property/set_reply"
    client_id   = make_client_id(mqtt_creds.certificate_account, user_id)

    state = {"connected_rc": None, "subscribed": False, "reply": None}
    accepted_ids = {str(payload["id"])}

    def on_connect(c, _u, _f, rc, *_a):
        state["connected_rc"] = rc
        if rc == 0:
            c.subscribe(reply_topic, qos=1)

    def on_subscribe(_c, _u, _mid, _granted_qos, *_a):
        state["subscribed"] = True

    def on_message(_c, _u, msg):
        try:
            j = json.loads(msg.payload.decode())
        except Exception:
            return
        if str(j.get("id")) in accepted_ids:
            state["reply"] = j

    c = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    c.username_pw_set(mqtt_creds.certificate_account, mqtt_creds.certificate_password)
    c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    c.on_connect   = on_connect
    c.on_subscribe = on_subscribe
    c.on_message   = on_message

    try:
        c.connect(mqtt_creds.url, mqtt_creds.port, keepalive=60)
    except Exception as e:
        return {"success": False, "reply": None, "error": f"mqtt connect: {e}"}
    c.loop_start()

    # Wait for CONNACK
    deadline = time.time() + 8
    while time.time() < deadline and state["connected_rc"] is None:
        time.sleep(0.1)
    if state["connected_rc"] != 0:
        c.loop_stop(); c.disconnect()
        return {"success": False, "reply": None,
                "error": f"mqtt auth rejected (CONNACK rc={state['connected_rc']})"}

    # Wait for SUBACK
    deadline = time.time() + 5
    while time.time() < deadline and not state["subscribed"]:
        time.sleep(0.1)
    if not state["subscribed"]:
        c.loop_stop(); c.disconnect()
        return {"success": False, "reply": None, "error": "subscribe ack not received within 5s"}
    time.sleep(0.3)  # extra buffer for broker routing

    def _attempt(p, label):
        if verbose:
            print(f"   {label}: msg_id={p['id']}")
        pub = c.publish(set_topic, json.dumps(p, separators=(",", ":")), qos=1)
        try:
            pub.wait_for_publish(timeout=5)
        except Exception:
            pass
        if pub.rc != 0:
            return f"mqtt publish rc={pub.rc}"
        d = time.time() + timeout
        while time.time() < d:
            if state["reply"] is not None:
                return None
            time.sleep(0.1)
        return f"no set_reply within {timeout}s"

    err = _attempt(payload, "publish")
    attempts_left = retry
    while state["reply"] is None and err is not None and attempts_left > 0:
        attempts_left -= 1
        new_id = str(random.randint(100_000_000, 999_999_999))
        accepted_ids.add(new_id)
        retry_payload = dict(payload, id=new_id)
        if verbose:
            print(f"   ⚠ {err} — retrying (id={new_id})")
        err = _attempt(retry_payload, "retry")

    c.loop_stop(); c.disconnect()

    if state["reply"] is None:
        return {"success": False, "reply": None, "error": err}

    r = state["reply"]
    data = r.get("data", {})
    if r.get("code") == "0" and data.get("ack") == 0 and data.get("sta") == 0:
        return {"success": True, "reply": r, "error": None}
    return {
        "success": False,
        "reply":   r,
        "error":   f"set_reply non-success: code={r.get('code')} sta={data.get('sta')} ack={data.get('ack')}",
    }
