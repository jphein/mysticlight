import math

from mysticlight import health


def test_normalize_below_warn_is_zero():
    assert health.normalize(50, 80, 95) == 0.0


def test_normalize_at_crit_is_one():
    assert health.normalize(95, 80, 95) == 1.0
    assert health.normalize(99, 80, 95) == 1.0  # clamped


def test_normalize_midpoint():
    assert health.normalize(87.5, 80, 95) == 0.5


def test_worst_of():
    name, score = health.worst_of({"cpu": 0.2, "disk": 0.73, "ram": 0.0})
    assert name == "disk" and score == 0.73


def test_worst_of_empty():
    assert health.worst_of({}) == ("none", 0.0)


def test_score_to_rgb_endpoints():
    assert health.score_to_rgb(0.0) == (0, 255, 0)
    assert health.score_to_rgb(1.0) == (255, 0, 0)


def test_score_to_rgb_midpoint_is_yellow():
    r, g, b = health.score_to_rgb(0.5)
    assert b == 0 and r == 255 and g == 255  # hue 60° = yellow


def test_pulse_factor_bounds():
    vals = [health.pulse_factor(t / 10) for t in range(0, 100)]
    assert min(vals) >= 0.25 - 1e-9
    assert max(vals) <= 1.0 + 1e-9


def test_pulse_factor_period():
    assert math.isclose(health.pulse_factor(0.0), health.pulse_factor(5.0), abs_tol=1e-9)


def test_parse_nvidia_smi():
    d = health.parse_nvidia_smi("3, 1015, 11264, 58")
    assert d["util"] == 3.0
    assert round(d["vram_pct"], 1) == 9.0
    assert d["temp"] == 58.0


def test_parse_nvidia_smi_malformed():
    assert health.parse_nvidia_smi("") is None
    assert health.parse_nvidia_smi("garbage") is None


def test_thresholds_cover_agreed_metrics():
    assert set(health.THRESHOLDS) == {"cpu", "cpu_temp", "ram", "disk", "net", "gpu"}
