# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure health-metric logic: normalization, worst-of composite, LED color map.

Raw metric units: cpu = load1/ncpu ratio; cpu_temp = °C; ram/disk/gpu = percent
used; net = percent of link capacity. Each normalizes to 0.0 (≤warn) … 1.0 (≥crit).
"""
from __future__ import annotations

import colorsys
import math

# metric: (warn, crit) — agreed 2026-08-19
THRESHOLDS = {
    "cpu": (0.7, 1.5),
    "cpu_temp": (80.0, 95.0),
    "ram": (80.0, 95.0),
    "disk": (80.0, 95.0),
    "net": (60.0, 90.0),
    "gpu": (80.0, 95.0),
}

PULSE_PERIOD = 5.0
PULSE_FLOOR = 0.25


def normalize(value: float, warn: float, crit: float) -> float:
    if value <= warn:
        return 0.0
    if value >= crit:
        return 1.0
    return (value - warn) / (crit - warn)


def worst_of(scores: dict) -> tuple:
    if not scores:
        return ("none", 0.0)
    name = max(scores, key=scores.get)
    return (name, scores[name])


def score_to_rgb(score: float) -> tuple:
    """Green (0.0) → yellow (0.5) → red (1.0) via HSV hue 120°→0°."""
    hue = 120.0 * (1.0 - max(0.0, min(1.0, score)))
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 1.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def pulse_factor(t: float, period: float = PULSE_PERIOD, floor: float = PULSE_FLOOR) -> float:
    """Breathing brightness factor in [floor, 1.0], sine with the given period."""
    wave = 0.5 + 0.5 * math.sin(2 * math.pi * t / period)
    return floor + (1.0 - floor) * wave


def parse_nvidia_smi(output: str) -> dict | None:
    """Parse 'util, mem.used, mem.total, temp' CSV from nvidia-smi."""
    try:
        util, used, total, temp = (float(x.strip()) for x in output.strip().split(","))
        return {"util": util, "vram_pct": 100.0 * used / total, "temp": temp}
    except (ValueError, ZeroDivisionError):
        return None
