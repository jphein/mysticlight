# SPDX-License-Identifier: AGPL-3.0-or-later
"""MQTT login from a private file (MQTT_LOGIN_FILE), as an alternative to MQTT_USER / MQTT_PASSWORD in the environment.

Why: env vars come from a unit's EnvironmentFile, and some setups can't (or shouldn't) rewrite that file to rotate a
credential. A separate 0600 file, written from a password manager, lets the login change without touching it. When
MQTT_LOGIN_FILE is set it wins over MQTT_USER / MQTT_PASSWORD.

Format: two lines, the username then the password (the password is taken verbatim, spaces included).
The file must not be group- or world-accessible. Error messages name the path, never the contents.
"""
from __future__ import annotations

import os
import stat


class LoginFileError(ValueError):
    pass


def read_login_file(path: str) -> tuple[str, str]:
    try:
        st = os.stat(path)
    except OSError as e:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot read ({e.strerror})") from None
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: mode {mode:o} is group/world accessible; chmod 600 it")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1]:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: expected two lines, the username then the password")
    return lines[0].strip(), lines[1]
