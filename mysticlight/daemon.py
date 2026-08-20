# SPDX-License-Identifier: GPL-3.0-or-later
"""mysticlightd — OpenRGB <-> Home Assistant MQTT bridge daemon.

All translation logic lives in bridge.py (unit-tested); this module is wiring:
OpenRGB SDK client, paho-mqtt client, reconnect loops, state persistence.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time

import paho.mqtt.client as mqtt
import psutil
from openrgb import OpenRGBClient
from openrgb.utils import DeviceType, RGBColor

from mysticlight import bridge, health, version_api

log = logging.getLogger("mysticlightd")

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modes in which per-zone colors actually apply to the hardware.
COLOR_MODES = ("direct", "static")


class Config:
    def __init__(self, env=os.environ):
        self.mqtt_host = env.get("MQTT_HOST", "localhost")
        self.mqtt_port = int(env.get("MQTT_PORT", "1883"))
        self.mqtt_user = env.get("MQTT_USER", "")
        self.mqtt_password = env.get("MQTT_PASSWORD", "")
        self.openrgb_host = env.get("OPENRGB_HOST", "127.0.0.1")
        self.openrgb_port = int(env.get("OPENRGB_PORT", "6742"))
        self.version_port = int(env.get("VERSION_PORT", "7716"))
        self.state_file = env.get("STATE_FILE", os.path.join(_PROJECT, "state.json"))
        self.poll_seconds = int(env.get("POLL_SECONDS", "30"))
        self.health_sample_seconds = int(env.get("HEALTH_SAMPLE_SECONDS", "10"))
        self.net_iface = env.get("NET_IFACE", "eth0")
        self.net_mbps = int(env.get("NET_MBPS", "1000"))


# Zone the health painter owns (runtime name from OpenRGB).
HEALTH_ZONE = os.environ.get("HEALTH_ZONE", "Onboard LEDs")


class Sampler:
    """Reads raw metric values; units match health.THRESHOLDS."""

    def __init__(self, net_iface: str, net_mbps: int):
        self.net_iface = net_iface
        self.net_capacity_bps = net_mbps * 125_000  # bytes/sec
        self.ncpu = os.cpu_count() or 1
        self._last_net = None
        self.gpu_temp = None

    def _cpu_temp(self):
        try:
            entries = psutil.sensors_temperatures().get("coretemp", [])
            pkg = [e.current for e in entries if e.label.startswith("Package")]
            if pkg:
                return pkg[0]
            return max((e.current for e in entries), default=None)
        except Exception:
            return None

    def _net_pct(self):
        try:
            io = psutil.net_io_counters(pernic=True).get(self.net_iface)
            if io is None:
                return None
            now = time.monotonic()
            cur = (now, io.bytes_recv, io.bytes_sent)
            prev, self._last_net = self._last_net, cur
            if prev is None:
                return 0.0
            dt = max(now - prev[0], 1e-6)
            rate = ((cur[1] - prev[1]) + (cur[2] - prev[2])) / dt
            return max(0.0, 100.0 * rate / self.net_capacity_bps)
        except Exception:
            return None

    def _gpu_pct(self):
        try:
            p = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3)
            g = health.parse_nvidia_smi(p.stdout)
            self.gpu_temp = g["temp"] if g else None
            return max(g["util"], g["vram_pct"]) if g else None
        except Exception:
            self.gpu_temp = None
            return None

    def sample(self) -> dict:
        return {
            "cpu": os.getloadavg()[0] / self.ncpu,
            "cpu_temp": self._cpu_temp(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage("/").percent,
            "net": self._net_pct(),
            "gpu": self._gpu_pct(),
        }


class Hardware:
    """Wraps the OpenRGB SDK client for the one MSI motherboard device."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.client = None
        self.device = None
        self.lock = threading.Lock()

    def connect(self, stop: threading.Event) -> bool:
        """Connect with capped exponential backoff until success or stop."""
        delay = 1
        while not stop.is_set():
            try:
                with self.lock:
                    self.client = OpenRGBClient(self.host, self.port, "mysticlightd")
                    boards = self.client.get_devices_by_type(DeviceType.MOTHERBOARD)
                    if not boards:
                        raise RuntimeError("no motherboard device in OpenRGB")
                    self.device = boards[0]
                log.info("OpenRGB connected: %s (%d zones)", self.device.name, len(self.device.zones))
                return True
            except Exception as e:
                log.warning("OpenRGB connect failed (%s); retry in %ds", e, delay)
                with self.lock:
                    self._close()
                stop.wait(delay)
                delay = min(delay * 2, 60)
        return False

    def _close(self):
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client, self.device = None, None

    def zone_names(self) -> list:
        with self.lock:
            return [z.name for z in self.device.zones]

    def mode_names(self) -> list:
        with self.lock:
            return [m.name for m in self.device.modes]

    def active_mode_name(self) -> str:
        with self.lock:
            return self.device.modes[self.device.active_mode].name

    def set_mode(self, name: str):
        with self.lock:
            for m in self.device.modes:
                if m.name.lower() == name.lower():
                    self.device.set_mode(m)
                    return
        raise ValueError(f"unknown mode: {name}")

    def color_capable(self) -> bool:
        return self.active_mode_name().lower() in COLOR_MODES

    def set_zone_color(self, zone_name: str, rgb: tuple):
        with self.lock:
            for z in self.device.zones:
                if z.name == zone_name:
                    z.set_color(RGBColor(*rgb))
                    return
        raise ValueError(f"unknown zone: {zone_name}")

    def zone_colors(self) -> dict:
        """Refresh and return {zone_name: (r,g,b)} (first LED of each zone)."""
        with self.lock:
            self.device.update()
            out = {}
            for z in self.device.zones:
                if z.colors:
                    c = z.colors[0]
                    out[z.name] = (c.red, c.green, c.blue)
            return out


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stop = threading.Event()
        self.hw = Hardware(cfg.openrgb_host, cfg.openrgb_port)
        self.states = {}          # slug -> ZoneState
        self.zone_by_slug = {}    # slug -> hardware zone name
        self.sw_version = "dev"
        self.health_mode = False
        self.sampler = Sampler(cfg.net_iface, cfg.net_mbps)
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mysticlightd")
        self.mqtt.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        self.mqtt.will_set(bridge.AVAILABILITY_TOPIC, "offline", retain=True)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message

    # ---- persistence ----

    def load_states(self):
        try:
            with open(self.cfg.state_file) as f:
                self.states, self.health_mode = bridge.deserialize_app_state(f.read())
            log.info("restored state for %d zones (health_mode=%s)", len(self.states), self.health_mode)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("state file unreadable (%s); starting fresh", e)

    def save_states(self):
        try:
            with open(self.cfg.state_file, "w") as f:
                f.write(bridge.serialize_app_state(self.states, self.health_mode))
        except Exception as e:
            log.warning("state file write failed: %s", e)

    # ---- publishing ----

    def _pub(self, topic: str, payload, retain: bool = True):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self.mqtt.publish(topic, payload, retain=retain)

    def publish_discovery(self):
        for zslug, zname in self.zone_by_slug.items():
            topic, payload = bridge.light_discovery(zname, self.sw_version)
            self._pub(topic, payload)
        topic, payload = bridge.select_discovery(self.hw.mode_names(), self.sw_version)
        self._pub(topic, payload)
        for topic, payload in (bridge.pressure_sensor_discovery(self.sw_version),
                               bridge.health_switch_discovery(self.sw_version)):
            self._pub(topic, payload)

    def publish_all_states(self):
        for zslug in self.zone_by_slug:
            self._pub(bridge.zone_state_topic(zslug), bridge.state_payload(self.states[zslug]))
        self._pub(bridge.EFFECT_STATE_TOPIC, self.hw.active_mode_name())
        self._pub(bridge.HEALTH_MODE_STATE_TOPIC, "ON" if self.health_mode else "OFF")

    def publish_availability(self, online: bool):
        self._pub(bridge.AVAILABILITY_TOPIC, "online" if online else "offline")

    # ---- hardware sync ----

    def adopt_zones(self):
        """(Re)build the zone map after (re)connect; seed unknown zones from hardware."""
        self.zone_by_slug = {bridge.slug(n): n for n in self.hw.zone_names()}
        observed = self.hw.zone_colors()
        for zslug, zname in self.zone_by_slug.items():
            if zslug not in self.states:
                rgb = observed.get(zname, (0, 0, 0))
                self.states[zslug] = bridge.ZoneState(
                    state="OFF" if rgb == (0, 0, 0) else "ON",
                    color=rgb if rgb != (0, 0, 0) else (255, 255, 255),
                    brightness=255,
                )

    def push_all_colors(self):
        if not self.hw.color_capable():
            return
        for zslug, zname in self.zone_by_slug.items():
            if self.health_mode and zname == HEALTH_ZONE:
                continue  # health painter owns this zone
            self.hw.set_zone_color(zname, bridge.hw_color(self.states[zslug]))

    # ---- mqtt callbacks ----

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.warning("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        client.subscribe([("mysticlight/zone/+/set", 0), (bridge.EFFECT_COMMAND_TOPIC, 0),
                          (bridge.HEALTH_MODE_COMMAND_TOPIC, 0), ("homeassistant/status", 0)])
        if self.zone_by_slug:
            self.publish_discovery()
            self.publish_all_states()
            self.publish_availability(True)

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            if topic == "homeassistant/status":
                if msg.payload.decode(errors="replace").strip() == "online" and self.zone_by_slug:
                    log.info("HA birth — republishing discovery + state")
                    self.publish_discovery()
                    self.publish_all_states()
                    self.publish_availability(True)
                return
            if topic == bridge.HEALTH_MODE_COMMAND_TOPIC:
                self.health_mode = msg.payload.decode(errors="replace").strip() == "ON"
                self._pub(bridge.HEALTH_MODE_STATE_TOPIC, "ON" if self.health_mode else "OFF")
                self.save_states()
                log.info("health mode -> %s", self.health_mode)
                if not self.health_mode:
                    self.push_all_colors()  # hand the onboard zone back to manual state
                return
            if topic == bridge.EFFECT_COMMAND_TOPIC:
                name = msg.payload.decode(errors="replace").strip()
                self.hw.set_mode(name)
                self._pub(bridge.EFFECT_STATE_TOPIC, self.hw.active_mode_name())
                self.push_all_colors()  # returning to Direct/Static restores zone colors
                return
            if topic.startswith("mysticlight/zone/") and topic.endswith("/set"):
                zslug = topic.split("/")[2]
                if zslug not in self.zone_by_slug:
                    log.warning("command for unknown zone %s", zslug)
                    return
                payload = json.loads(msg.payload)
                self.states[zslug] = bridge.apply_command(self.states[zslug], payload)
                if self.hw.color_capable():
                    self.hw.set_zone_color(self.zone_by_slug[zslug], bridge.hw_color(self.states[zslug]))
                self._pub(bridge.zone_state_topic(zslug), bridge.state_payload(self.states[zslug]))
                self.save_states()
        except Exception:
            log.exception("error handling message on %s (ignored)", msg.topic)

    # ---- health mode ----

    def health_loop(self):
        """1s tick: sample every health_sample_seconds, paint onboard zone.

        Runs for the daemon's lifetime; all hardware access is guarded so
        OpenRGB reconnects in the main loop don't crash this thread.
        """
        tick = 0
        scores, worst_name, worst, pulsing = {}, "none", 0.0, False
        last_painted = None
        while not self.stop.is_set():
            self.stop.wait(1)
            if self.stop.is_set():
                return
            tick += 1
            sampled = tick % self.cfg.health_sample_seconds == 1
            if sampled:
                raw = self.sampler.sample()
                scores = {k: health.normalize(v, *health.THRESHOLDS[k])
                          for k, v in raw.items() if v is not None}
                worst_name, worst = health.worst_of(scores)
                pulsing = any(s >= 1.0 for s in scores.values())
                payload = {
                    "pressure": round(worst * 100, 1),
                    "worst": worst_name,
                    "status": "critical" if pulsing else ("warn" if worst > 0 else "ok"),
                    "cpu_load_ratio": None if raw["cpu"] is None else round(raw["cpu"], 2),
                    "cpu_temp": raw["cpu_temp"],
                    "ram_pct": raw["ram"],
                    "disk_pct": raw["disk"],
                    "net_pct": None if raw["net"] is None else round(raw["net"], 2),
                    "gpu_pct": None if raw["gpu"] is None else round(raw["gpu"], 1),
                    "gpu_temp": self.sampler.gpu_temp,
                }
                self._pub(bridge.HEALTH_STATE_TOPIC, payload)
            if not self.health_mode:
                last_painted = None
                continue
            try:
                if not self.hw.color_capable():
                    continue
                rgb = health.score_to_rgb(worst)
                if pulsing:
                    f = health.pulse_factor(time.monotonic())
                    rgb = tuple(round(c * f) for c in rgb)
                # Repaint on change, and re-assert on every sample tick: other
                # writers (boot push, manual sets) can invalidate the cache.
                if (rgb != last_painted or sampled) and self.health_mode:
                    self.hw.set_zone_color(HEALTH_ZONE, rgb)
                    last_painted = rgb
            except Exception:
                last_painted = None  # hardware mid-reconnect; main loop handles it

    # ---- main loop ----

    def poll_once(self):
        """Reconcile external changes (OpenRGB GUI etc.) into HA state."""
        if not self.hw.color_capable():
            self._pub(bridge.EFFECT_STATE_TOPIC, self.hw.active_mode_name())
            return
        self._pub(bridge.EFFECT_STATE_TOPIC, self.hw.active_mode_name())
        observed = self.hw.zone_colors()
        changed = False
        for zslug, zname in self.zone_by_slug.items():
            if zname not in observed:
                continue
            if self.health_mode and zname == HEALTH_ZONE:
                continue  # health painter owns this zone; don't adopt its colors
            adopted = bridge.reconcile(self.states[zslug], observed[zname])
            if adopted is not None:
                self.states[zslug] = adopted
                self._pub(bridge.zone_state_topic(zslug), bridge.state_payload(adopted))
                changed = True
        if changed:
            self.save_states()

    def run(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        signal.signal(signal.SIGTERM, lambda *a: self.stop.set())
        signal.signal(signal.SIGINT, lambda *a: self.stop.set())

        self.sw_version = version_api.build_version().get("version", "dev")
        version_api.start_in_thread(self.cfg.version_port)
        self.load_states()

        self.mqtt.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.mqtt.loop_start()
        threading.Thread(target=self.health_loop, name="health", daemon=True).start()

        while not self.stop.is_set():
            if not self.hw.connect(self.stop):
                break  # stopped
            bridge.DEVICE_MODEL = self.hw.device.name
            self.adopt_zones()
            self.save_states()
            self.publish_discovery()
            self.push_all_colors()
            self.publish_all_states()
            self.publish_availability(True)
            while not self.stop.is_set():
                self.stop.wait(self.cfg.poll_seconds)
                if self.stop.is_set():
                    break
                try:
                    self.poll_once()
                except Exception as e:
                    log.warning("OpenRGB lost (%s); reconnecting", e)
                    self.publish_availability(False)
                    with self.hw.lock:
                        self.hw._close()
                    break  # outer loop reconnects

        self.publish_availability(False)
        self.mqtt.loop_stop()
        try:
            self.mqtt.disconnect()
        except Exception:
            pass
        log.info("stopped")


def main():
    Daemon(Config()).run()
