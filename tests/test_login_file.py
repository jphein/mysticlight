# SPDX-License-Identifier: AGPL-3.0-or-later
"""MQTT_LOGIN_FILE: read correctly, refuse unsafe or malformed files, never echo the password, win over env."""
import os

import pytest

from mysticlight import login_file
from mysticlight.login_file import LoginFileError, read_login_file

PW = " pw with spaces 9z "  # leading/trailing spaces are part of the password


def write(tmp_path, data, mode=0o600, name="mqtt-login"):
    p = tmp_path / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.chmod(p, mode)
    return str(p)


def refused(path, needle):
    with pytest.raises(LoginFileError) as e:
        read_login_file(path)
    msg = str(e.value)
    assert needle in msg and PW.strip() not in msg
    return msg


def test_reads_user_and_verbatim_password(tmp_path):
    assert read_login_file(write(tmp_path, f"mqtt-mysticlight\n{PW}\n")) == ("mqtt-mysticlight", PW)


def test_no_final_newline_and_crlf_and_padded_username(tmp_path):
    assert read_login_file(write(tmp_path, f"  mqtt-x  \r\n{PW}")) == ("mqtt-x", PW)


def test_unicode_line_separator_stays_inside_the_password(tmp_path):
    pw = "a b\x0cc"  # U+2028 and form feed: splitlines() would cut here, the format must not
    assert read_login_file(write(tmp_path, f"u\n{pw}\n")) == ("u", pw)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_refuses_group_or_world_access(tmp_path, mode):
    refused(write(tmp_path, f"u\n{PW}\n", mode), "chmod 600")


@pytest.mark.parametrize("text", ["only-a-user\n", "\n" + PW + "\n", "u\n\n", "", f"u\n{PW}\nextra\n"])
def test_refuses_anything_but_exactly_two_lines(tmp_path, text):
    refused(write(tmp_path, text), "exactly two lines")


def test_refuses_invalid_utf8_without_carrying_the_bytes(tmp_path):
    with pytest.raises(LoginFileError) as e:
        read_login_file(write(tmp_path, b"u\nsecret\xff\xfe\n"))
    assert "UTF-8" in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


def test_refuses_a_symlink_even_to_a_good_file(tmp_path):
    good = write(tmp_path, f"u\n{PW}\n")
    link = tmp_path / "link"
    link.symlink_to(good)
    refused(str(link), "cannot open")


def test_refuses_a_fifo_without_hanging(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    refused(str(fifo), "not a regular file")


def test_refuses_a_file_owned_by_someone_else(tmp_path, monkeypatch):
    p = write(tmp_path, f"u\n{PW}\n")
    monkeypatch.setattr(login_file.os, "getuid", lambda: os.stat(p).st_uid + 4242)
    if os.stat(p).st_uid == 0:
        pytest.skip("running as root: root-owned files are accepted by design")
    refused(p, "owned by this user or root")


def test_refuses_oversized(tmp_path):
    refused(write(tmp_path, "u\n" + "x" * (login_file.MAX_BYTES + 10) + "\n"), "larger than")


def test_validation_uses_the_opened_descriptor_not_a_path_lookup(tmp_path, monkeypatch):
    """TOCTOU guard: the checks must come from fstat on the open fd, never from a second lookup of the path."""
    p = write(tmp_path, f"u\n{PW}\n")
    def no_path_lookup(*a, **k):
        raise AssertionError("path re-lookup")
    monkeypatch.setattr(login_file.os, "stat", no_path_lookup)
    monkeypatch.setattr(login_file.os, "lstat", no_path_lookup)
    assert read_login_file(p) == ("u", PW)


def test_missing_file_names_the_path(tmp_path):
    refused(str(tmp_path / "nope"), "nope")


def test_config_login_file_wins_over_env_and_fails_closed(tmp_path):
    pytest.importorskip("paho")  # Config lives in daemon.py, which needs the runtime deps
    from mysticlight.daemon import Config
    f = write(tmp_path, f"mqtt-mysticlight\n{PW}\n")
    c = Config({"MQTT_USER": "jp", "MQTT_PASSWORD": "old", "MQTT_LOGIN_FILE": f})
    assert (c.mqtt_user, c.mqtt_password) == ("mqtt-mysticlight", PW)
    assert (Config({"MQTT_USER": "jp", "MQTT_PASSWORD": "old"}).mqtt_user) == "jp"  # absent → env unchanged
    bad = write(tmp_path, f"u\n{PW}\n", 0o644, name="bad")
    with pytest.raises(LoginFileError):  # a bad file never silently falls back to the env credentials
        Config({"MQTT_USER": "jp", "MQTT_PASSWORD": "old", "MQTT_LOGIN_FILE": bad})
