# SPDX-License-Identifier: AGPL-3.0-or-later
"""MQTT login from a private file (MQTT_LOGIN_FILE), as an alternative to MQTT_USER / MQTT_PASSWORD in the environment.

Why: env vars come from a unit's EnvironmentFile, and some setups can't (or shouldn't) rewrite that file to rotate a
credential. A separate 0600 file, written from a password manager, lets the login change without touching it. When
MQTT_LOGIN_FILE is set it wins over MQTT_USER / MQTT_PASSWORD, and a bad file fails closed (no fallback to the env).

Format: exactly two lines, the username then the password, separated by LF or CRLF, with an optional final newline.
The username is stripped and the password is taken verbatim (spaces and any other characters kept).

Security model (each point is tested):
  * The file is opened ONCE (O_NOFOLLOW: a symlink is refused, O_NONBLOCK: a FIFO can't hang startup), and every check
    runs on THAT descriptor (fstat), so nothing can be swapped in between the check and the read.
  * It must be a regular file, owned by this process's user or by root, with no group/world permission bits.
  * The read is bounded. Every failure is a LoginFileError that names the path and never the contents: open/read/decode
    errors are converted, `from None`, so no exception carries the bytes.
"""
from __future__ import annotations

import os
import stat

MAX_BYTES = 4096


class LoginFileError(ValueError):
    pass


def read_login_file(path: str) -> tuple[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot open ({e.strerror}; symlinks are refused)") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: not a regular file")
        if st.st_uid not in (os.getuid(), 0):
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: must be owned by this user or root")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: mode {mode:o} is group/world accessible; chmod 600 it")
        try:
            raw = os.read(fd, MAX_BYTES + 1)
        except OSError as e:
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot read ({e.strerror})") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: larger than {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: not valid UTF-8") from None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the optional final newline
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]
    if len(lines) != 2 or not lines[0].strip() or not lines[1]:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: expected exactly two lines, the username then the password")
    return lines[0].strip(), lines[1]
