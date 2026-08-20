# mysticlight

MSI Mystic Light → Home Assistant MQTT bridge. Runs on the machine that hosts
the RGB hardware, exposing per-zone RGB control of an MSI motherboard (USB HID
controller 1462:b926) to HA via MQTT discovery.

## Architecture

`openrgb --server` (systemd `openrgb-sdk.service`, localhost:6742, no auth —
keep localhost-bound) owns the USB HID controller. `mysticlightd.service` runs
`python -m mysticlight`: openrgb-python client → paho-mqtt → the MQTT broker
configured in `.env`. realm-sigil `/api/version` on :7716.

- `mysticlight/bridge.py` — pure logic (state machine, discovery payloads,
  brightness-by-RGB-scaling, reconcile). All unit tests live here. Device
  identity is hostname-derived (override: `DEVICE_SLUG`/`DEVICE_NAME` env).
- `mysticlight/health.py` — pure health logic (worst-of, color map, pulse).
- `mysticlight/daemon.py` — wiring: reconnect loops, MQTT callbacks, 30s poll,
  health sampler/painter threads, state persistence (`state.json`).
- `mysticlight/version_api.py` — sigil endpoint.
- `deploy/*.service` — systemd units (installed copies in /etc/systemd/system).

## HA entities (per host slug, e.g. `katana`)

`light.mystic_light_<host>_onboard_leds`, `light.mystic_light_<host>_jrainbow1`
(RGB + brightness), `select.mystic_light_<host>_effect` (hardware modes,
device-wide), `sensor.mystic_light_<host>_system_pressure` (0-100 worst-of
composite; per-metric attrs), `switch.mystic_light_<host>_health_mode`.
Availability topic `mysticlight/availability` (LWT).

## Health mode

Worst-of across cpu load1/ncpu, coretemp, ram, disk /, net (NET_IFACE vs
NET_MBPS), gpu (nvidia-smi). Thresholds in `health.THRESHOLDS`. While the
switch is ON the daemon paints the `HEALTH_ZONE` ("Onboard LEDs") green→red
every 10s, re-asserting each sample tick (other writers can repaint the zone —
never trust the painter's cache alone); 1s software breathing pulse when any
metric ≥ critical. OFF restores the stored manual color. Requires Direct mode
to paint; sensors publish regardless.

## Hardware facts (MPG Z390I GAMING EDGE AC, verified 2026-08-15)

- This board is the **162-byte protocol variant** in OpenRGB: zones are
  `JRAINBOW1` and `Onboard LEDs` only (no JRGB zone despite the physical
  header docs). Other MSI boards report their own zone lists at runtime.
- Mode list has `Direct` but **no `Static`**. Per-zone colors only apply in
  Direct; effects are device-wide (hardware limitation, not a bug).
- openrgb-python 0.3.6: `Device.update()` refreshes (there is no `.refresh()`).

## Dev

```bash
venv/bin/pytest tests/ -q          # unit tests
sudo systemctl restart mysticlightd && journalctl -u mysticlightd -f
```

`.env` (git-ignored) holds MQTT broker address + creds and per-host settings
(NET_IFACE etc.). Never commit `.env` or `state.json`.
