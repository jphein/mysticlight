from mysticlight import bridge
from mysticlight.bridge import ZoneState


def test_slug():
    assert bridge.slug("JRGB1") == "jrgb1"
    assert bridge.slug("On Board LED") == "on_board_led"


def test_hw_color_off_is_black():
    assert bridge.hw_color(ZoneState(state="OFF", color=(255, 0, 0), brightness=255)) == (0, 0, 0)


def test_hw_color_scales_by_brightness():
    assert bridge.hw_color(ZoneState(state="ON", color=(200, 100, 0), brightness=128)) == (100, 50, 0)


def test_apply_command_on_with_color_and_brightness():
    zs = bridge.apply_command(ZoneState(), {"state": "ON", "color": {"r": 10, "g": 20, "b": 30}, "brightness": 100})
    assert (zs.state, zs.color, zs.brightness) == ("ON", (10, 20, 30), 100)


def test_apply_command_bare_on_restores_previous_color():
    prev = ZoneState(state="OFF", color=(1, 2, 3), brightness=42)
    zs = bridge.apply_command(prev, {"state": "ON"})
    assert (zs.state, zs.color, zs.brightness) == ("ON", (1, 2, 3), 42)


def test_apply_command_off_keeps_color():
    prev = ZoneState(state="ON", color=(9, 8, 7), brightness=200)
    zs = bridge.apply_command(prev, {"state": "OFF"})
    assert (zs.state, zs.color) == ("OFF", (9, 8, 7))


def test_apply_command_garbage_returns_unchanged():
    prev = ZoneState(state="ON", color=(9, 8, 7), brightness=200)
    assert bridge.apply_command(prev, {"bogus": True}) == prev


def test_state_payload_roundtrip():
    zs = ZoneState(state="ON", color=(10, 20, 30), brightness=99)
    p = bridge.state_payload(zs)
    assert p == {"state": "ON", "color_mode": "rgb", "color": {"r": 10, "g": 20, "b": 30}, "brightness": 99}


def test_reconcile_consistent_returns_none():
    zs = ZoneState(state="ON", color=(200, 100, 0), brightness=128)
    assert bridge.reconcile(zs, (100, 50, 0)) is None


def test_reconcile_external_color_adopted():
    zs = ZoneState(state="ON", color=(200, 100, 0), brightness=128)
    new = bridge.reconcile(zs, (0, 0, 255))
    assert (new.state, new.color, new.brightness) == ("ON", (0, 0, 255), 255)


def test_reconcile_external_black_means_off():
    zs = ZoneState(state="ON", color=(200, 100, 0), brightness=128)
    new = bridge.reconcile(zs, (0, 0, 0))
    assert new.state == "OFF" and new.color == (200, 100, 0)


def test_serialize_deserialize_states():
    states = {"jrgb1": ZoneState(state="ON", color=(1, 2, 3), brightness=50)}
    assert bridge.deserialize_states(bridge.serialize_states(states)) == states


def test_light_discovery_payload():
    topic, payload = bridge.light_discovery("JRGB1", "1.2.3")
    assert topic == "homeassistant/light/mysticlight_jrgb1/config"
    assert payload["schema"] == "json"
    assert payload["command_topic"] == "mysticlight/zone/jrgb1/set"
    assert payload["state_topic"] == "mysticlight/zone/jrgb1/state"
    assert payload["availability_topic"] == "mysticlight/availability"
    assert payload["brightness"] is True
    assert payload["supported_color_modes"] == ["rgb"]
    assert payload["unique_id"] == "mysticlight_katana_jrgb1"
    assert payload["device"]["identifiers"] == ["mysticlight_katana"]
    assert payload["device"]["sw_version"] == "1.2.3"


def test_pressure_sensor_discovery():
    topic, payload = bridge.pressure_sensor_discovery("1.2.3")
    assert topic == "homeassistant/sensor/mysticlight_system_pressure/config"
    assert payload["state_topic"] == "mysticlight/health/state"
    assert payload["value_template"] == "{{ value_json.pressure }}"
    assert payload["json_attributes_topic"] == "mysticlight/health/state"
    assert payload["unit_of_measurement"] == "%"
    assert payload["state_class"] == "measurement"
    assert payload["availability_topic"] == "mysticlight/availability"
    assert payload["unique_id"] == "mysticlight_katana_system_pressure"
    assert payload["device"]["identifiers"] == ["mysticlight_katana"]


def test_health_switch_discovery():
    topic, payload = bridge.health_switch_discovery("1.2.3")
    assert topic == "homeassistant/switch/mysticlight_health_mode/config"
    assert payload["command_topic"] == "mysticlight/health_mode/set"
    assert payload["state_topic"] == "mysticlight/health_mode/state"
    assert payload["payload_on"] == "ON" and payload["payload_off"] == "OFF"
    assert payload["availability_topic"] == "mysticlight/availability"
    assert payload["unique_id"] == "mysticlight_katana_health_mode"


def test_app_state_roundtrip():
    states = {"jrgb1": ZoneState(state="ON", color=(1, 2, 3), brightness=50)}
    raw = bridge.serialize_app_state(states, True)
    zones, health_mode = bridge.deserialize_app_state(raw)
    assert zones == states and health_mode is True


def test_app_state_v1_backcompat():
    raw = bridge.serialize_states({"jrgb1": ZoneState(state="ON", color=(1, 2, 3), brightness=50)})
    zones, health_mode = bridge.deserialize_app_state(raw)
    assert zones["jrgb1"].color == (1, 2, 3) and health_mode is False


def test_select_discovery_payload():
    topic, payload = bridge.select_discovery(["Direct", "Rainbow"], "1.2.3")
    assert topic == "homeassistant/select/mysticlight_effect/config"
    assert payload["options"] == ["Direct", "Rainbow"]
    assert payload["command_topic"] == "mysticlight/effect/set"
    assert payload["state_topic"] == "mysticlight/effect/state"
