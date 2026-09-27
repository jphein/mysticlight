# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure translation logic: HA MQTT JSON light <-> zone state <-> hardware RGB."""
from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass, replace


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


BASE = "mysticlight"
AVAILABILITY_TOPIC = f"{BASE}/availability"
EFFECT_COMMAND_TOPIC = f"{BASE}/effect/set"
EFFECT_STATE_TOPIC = f"{BASE}/effect/state"

# Device identity: hostname-derived by default so entity ids are stable per
# machine; override with DEVICE_SLUG / DEVICE_NAME env vars.
HOST_SLUG = os.environ.get("DEVICE_SLUG") or slug(socket.gethostname())
DEVICE_MODEL = "unknown"  # set by the daemon from the OpenRGB device name
DEVICE = {
    "identifiers": [f"mysticlight_{HOST_SLUG}"],
    "name": os.environ.get("DEVICE_NAME", f"Mystic Light — {socket.gethostname()}"),
    "manufacturer": "MSI",
}


@dataclass(frozen=True)
class ZoneState:
    state: str = "OFF"
    color: tuple = (255, 255, 255)
    brightness: int = 255


def zone_command_topic(zone_slug: str) -> str:
    return f"{BASE}/zone/{zone_slug}/set"


def zone_state_topic(zone_slug: str) -> str:
    return f"{BASE}/zone/{zone_slug}/state"


def hw_color(zs: ZoneState) -> tuple:
    if zs.state != "ON":
        return (0, 0, 0)
    return tuple(round(c * zs.brightness / 255) for c in zs.color)


def apply_command(zs: ZoneState, payload: dict) -> ZoneState:
    if payload.get("state") not in ("ON", "OFF"):
        return zs
    new = replace(zs, state=payload["state"])
    if "color" in payload:
        c = payload["color"]
        new = replace(new, color=(int(c["r"]), int(c["g"]), int(c["b"])))
    if "brightness" in payload:
        new = replace(new, brightness=int(payload["brightness"]))
    return new


def state_payload(zs: ZoneState) -> dict:
    r, g, b = zs.color
    return {"state": zs.state, "color_mode": "rgb", "color": {"r": r, "g": g, "b": b}, "brightness": zs.brightness}


def reconcile(zs: ZoneState, observed: tuple) -> ZoneState | None:
    """Adopt an externally-changed hardware color. None if hardware matches us."""
    if tuple(observed) == hw_color(zs):
        return None
    if tuple(observed) == (0, 0, 0):
        return replace(zs, state="OFF")
    return replace(zs, state="ON", color=tuple(observed), brightness=255)


def serialize_states(states: dict) -> str:
    return json.dumps({k: {"state": v.state, "color": list(v.color), "brightness": v.brightness} for k, v in states.items()})


def deserialize_states(raw: str) -> dict:
    out = {}
    for k, v in json.loads(raw).items():
        out[k] = ZoneState(state=v["state"], color=tuple(v["color"]), brightness=v["brightness"])
    return out


HEALTH_STATE_TOPIC = f"{BASE}/health/state"
HEALTH_MODE_COMMAND_TOPIC = f"{BASE}/health_mode/set"
HEALTH_MODE_STATE_TOPIC = f"{BASE}/health_mode/state"


def serialize_app_state(states: dict, health_mode: bool) -> str:
    return json.dumps({"zones": json.loads(serialize_states(states)), "health_mode": health_mode})


def deserialize_app_state(raw: str) -> tuple:
    """Returns (zone_states, health_mode). Accepts v1 zones-only blobs."""
    d = json.loads(raw)
    if "zones" not in d:
        return deserialize_states(raw), False
    return deserialize_states(json.dumps(d["zones"])), bool(d.get("health_mode", False))


def _device(sw_version: str) -> dict:
    return {**DEVICE, "model": DEVICE_MODEL, "sw_version": sw_version}


def light_discovery(zone_name: str, sw_version: str) -> tuple:
    s = slug(zone_name)
    topic = f"homeassistant/light/mysticlight_{s}/config"
    payload = {
        "name": zone_name,
        "unique_id": f"mysticlight_{HOST_SLUG}_{s}",
        "schema": "json",
        "command_topic": zone_command_topic(s),
        "state_topic": zone_state_topic(s),
        "availability_topic": AVAILABILITY_TOPIC,
        "brightness": True,
        "supported_color_modes": ["rgb"],
        "device": _device(sw_version),
    }
    return topic, payload


def pressure_sensor_discovery(sw_version: str) -> tuple:
    topic = "homeassistant/sensor/mysticlight_system_pressure/config"
    payload = {
        "name": "System pressure",
        "unique_id": f"mysticlight_{HOST_SLUG}_system_pressure",
        "state_topic": HEALTH_STATE_TOPIC,
        "value_template": "{{ value_json.pressure }}",
        "json_attributes_topic": HEALTH_STATE_TOPIC,
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "icon": "mdi:gauge",
        "availability_topic": AVAILABILITY_TOPIC,
        "device": _device(sw_version),
    }
    return topic, payload


def health_switch_discovery(sw_version: str) -> tuple:
    topic = "homeassistant/switch/mysticlight_health_mode/config"
    payload = {
        "name": "Health mode",
        "unique_id": f"mysticlight_{HOST_SLUG}_health_mode",
        "command_topic": HEALTH_MODE_COMMAND_TOPIC,
        "state_topic": HEALTH_MODE_STATE_TOPIC,
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:heart-pulse",
        "availability_topic": AVAILABILITY_TOPIC,
        "device": _device(sw_version),
    }
    return topic, payload


def select_discovery(modes: list, sw_version: str) -> tuple:
    topic = "homeassistant/select/mysticlight_effect/config"
    payload = {
        "name": "Effect",
        "unique_id": f"mysticlight_{HOST_SLUG}_effect",
        "command_topic": EFFECT_COMMAND_TOPIC,
        "state_topic": EFFECT_STATE_TOPIC,
        "availability_topic": AVAILABILITY_TOPIC,
        "options": list(modes),
        "device": _device(sw_version),
    }
    return topic, payload
