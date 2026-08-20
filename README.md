# mysticlight

**Per-zone Home Assistant control for MSI Mystic Light motherboard RGB — via
OpenRGB and MQTT discovery.**

Home Assistant's built-in OpenRGB integration exposes each RGB device as a
single light. This bridge gives you **one HA light per motherboard zone**
(onboard LEDs, JRAINBOW/JRGB headers), the hardware effect list, and a bonus:
a **system-pressure health mode** that turns your onboard LEDs into a
glanceable machine-health indicator.

```
your PC                                         Home Assistant
┌─────────────────────────────────────┐
│ MSI Mystic Light USB (1462:b926)    │
│   ↑ hidraw                          │
│ openrgb --server (127.0.0.1:6742)   │        ┌──────────────────┐
│   ↑ OpenRGB SDK                     │  MQTT  │ MQTT broker      │
│ mysticlightd ───────────────────────┼───────→│  ↓ discovery     │
│   • per-zone lights + effect select │        │ lights, sensor,  │
│   • health sampler → LED painter    │        │ switch, select   │
└─────────────────────────────────────┘        └──────────────────┘
```

## Entities created (MQTT discovery — zero HA-side config)

| Entity | What it does |
|---|---|
| `light.mystic_light_<host>_<zone>` (one per zone) | RGB color, on/off, brightness (emulated by RGB scaling) |
| `select.mystic_light_<host>_effect` | Hardware effects (Breathing, Rainbow, Meteor, …) — device-wide by hardware design; `Direct` enables per-zone colors |
| `sensor.mystic_light_<host>_system_pressure` | 0–100 worst-of composite over CPU load, CPU temp, RAM, disk, network, GPU (per-metric attributes included) |
| `switch.mystic_light_<host>_health_mode` | ON: the onboard-LED zone becomes a green→red pressure gauge with a breathing pulse when critical; OFF: your manual color returns |

Availability is handled via MQTT LWT — kill the daemon and the entities go
`unavailable` instead of lying.

## Requirements

- Linux host with an MSI motherboard whose Mystic Light controller OpenRGB
  supports (USB `1462:b926` etc. — check `openrgb --list-devices`)
- [OpenRGB](https://openrgb.org) ≥ 0.9 (SDK server)
- An MQTT broker your Home Assistant uses (with MQTT discovery enabled)
- Python 3.11+; `nvidia-smi` optional (GPU metric skips gracefully without it)

## Install

```bash
git clone https://github.com/jphein/mysticlight && cd mysticlight
python3 -m venv venv && venv/bin/pip install -r requirements.txt

cat > .env <<EOF
MQTT_HOST=<your broker>
MQTT_PORT=1883
MQTT_USER=<user>
MQTT_PASSWORD=<password>
NET_IFACE=<your NIC, e.g. enp5s0>
EOF

# adjust paths/User in deploy/*.service, then:
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openrgb-sdk mysticlightd
```

Entities appear in HA automatically under one device named
`Mystic Light — <hostname>`.

### Configuration (env vars, all optional beyond MQTT_*)

| Var | Default | |
|---|---|---|
| `OPENRGB_HOST` / `OPENRGB_PORT` | `127.0.0.1` / `6742` | OpenRGB SDK server |
| `VERSION_PORT` | `7716` | `/api/version` HTTP endpoint |
| `POLL_SECONDS` | `30` | reconcile external color changes into HA |
| `HEALTH_SAMPLE_SECONDS` | `10` | health sampling cadence |
| `NET_IFACE` / `NET_MBPS` | `eth0` / `1000` | network-saturation metric |
| `HEALTH_ZONE` | `Onboard LEDs` | zone the health painter owns |
| `DEVICE_SLUG` / `DEVICE_NAME` | from hostname | HA device identity |

Health thresholds (warn → critical per metric) live in
`mysticlight/health.py:THRESHOLDS`.

## Notes & quirks

- **Keep the OpenRGB SDK server on localhost** — its protocol has no auth.
- Hardware effects are **device-wide** (MSI firmware limitation); per-zone
  colors only apply in `Direct` mode. The health pulse is software-animated
  for the same reason.
- Zone lists vary by board and are discovered at runtime (e.g. the Z390I
  Gaming Edge AC reports `JRAINBOW1` + `Onboard LEDs` only).
- The daemon reads system metrics locally, so the health LED keeps working
  even when Home Assistant is down.

## Development

```bash
venv/bin/pytest tests/ -q
```

All translation/health logic is pure and unit-tested (`bridge.py`,
`health.py`); `daemon.py` is thin wiring.

## License

[GPL-3.0-or-later](LICENSE)
