# SPDX-License-Identifier: AGPL-3.0-or-later
"""MQTT_LOGIN_FILE: read correctly, refuse unsafe or malformed files, never echo the password, win over env."""
import os

import pytest

from mysticlight.login_file import LoginFileError, read_login_file

PW = "pw with spaces 9z"


def write(tmp_path, text, mode=0o600):
    p = tmp_path / "mqtt-login"
    p.write_text(text, encoding="utf-8")
    os.chmod(p, mode)
    return str(p)


def test_reads_user_and_verbatim_password(tmp_path):
    assert read_login_file(write(tmp_path, f"mqtt-mysticlight\n{PW}\n")) == ("mqtt-mysticlight", PW)


def test_crlf_and_padded_username(tmp_path):
    assert read_login_file(write(tmp_path, f"  mqtt-x  \r\n{PW}\r\n")) == ("mqtt-x", PW)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_refuses_group_or_world_access_without_echoing_the_password(tmp_path, mode):
    with pytest.raises(LoginFileError) as e:
        read_login_file(write(tmp_path, f"u\n{PW}\n", mode))
    assert "chmod 600" in str(e.value) and PW not in str(e.value)


@pytest.mark.parametrize("text", ["only-a-user\n", "\n" + PW + "\n", "u\n\n", ""])
def test_refuses_malformed_without_echoing(tmp_path, text):
    with pytest.raises(LoginFileError) as e:
        read_login_file(write(tmp_path, text))
    assert "two lines" in str(e.value) and PW not in str(e.value)


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(LoginFileError) as e:
        read_login_file(str(tmp_path / "nope"))
    assert "nope" in str(e.value)


def test_config_login_file_wins_over_env(tmp_path):
    pytest.importorskip("paho")  # Config lives in daemon.py, which needs the runtime deps
    from mysticlight.daemon import Config
    f = write(tmp_path, f"mqtt-mysticlight\n{PW}\n")
    c = Config({"MQTT_USER": "jp", "MQTT_PASSWORD": "old", "MQTT_LOGIN_FILE": f})
    assert (c.mqtt_user, c.mqtt_password) == ("mqtt-mysticlight", PW)
    c2 = Config({"MQTT_USER": "jp", "MQTT_PASSWORD": "old"})
    assert (c2.mqtt_user, c2.mqtt_password) == ("jp", "old")  # unchanged when the variable is absent
