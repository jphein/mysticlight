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
  * The read loops to EOF but is bounded. Every failure is a LoginFileError that names the path and never the contents;
    no exception in the chain holds the file's bytes (the decode error is dropped before raising, not just hidden).
  * A CR is stripped only as part of CRLF: a bare CR at EOF is part of the password.
"""
from __future__ import annotations

import os
import stat

MAX_BYTES = 4096


class LoginFileError(ValueError):
    pass


def _close(fd: int) -> None:
    """A close failure on a read-only descriptor can't affect what was read, and must never mask (or replace) the
    validation result, so it is swallowed (xreview r2 #1)."""
    try:
        os.close(fd)
    except OSError:
        pass


def _read_all(fd: int, path: str) -> bytes:
    """Read until EOF or MAX_BYTES + 1: one os.read may legally return a short prefix (xreview r2 #2)."""
    chunks, total = [], 0
    while total <= MAX_BYTES:
        try:
            b = os.read(fd, MAX_BYTES + 1 - total)
        except OSError as e:
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot read ({e.strerror})") from None
        if not b:
            break
        chunks.append(b)
        total += len(b)
    return b"".join(chunks)


def _parse(text: str, path: str) -> tuple[str, str]:
    """LF-separated; a CR is stripped only where it is part of a CRLF, so a bare CR at EOF stays in the password."""
    parts = text.split("\n")
    terminated = [True] * (len(parts) - 1) + [False]
    if parts[-1] == "":  # the optional final newline
        parts.pop()
        terminated.pop()
    lines = [p[:-1] if t and p.endswith("\r") else p for p, t in zip(parts, terminated)]
    if len(lines) != 2 or not lines[0].strip() or not lines[1]:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: expected exactly two lines, the username then the password")
    return lines[0].strip(), lines[1]


def read_login_file(path: str) -> tuple[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot open ({e.strerror}; symlinks are refused)") from None
    try:
        try:
            st = os.fstat(fd)
        except OSError as e:
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: cannot stat the open file ({e.strerror})") from None
        if not stat.S_ISREG(st.st_mode):
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: not a regular file")
        if st.st_uid not in (os.getuid(), 0):
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: must be owned by this user or root")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: mode {mode:o} is group/world accessible; chmod 600 it")
        raw = _read_all(fd, path)
    finally:
        _close(fd)
    if len(raw) > MAX_BYTES:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: larger than {MAX_BYTES} bytes")
    # Decode OUTSIDE an except block: raising inside one would leave __context__ pointing at the UnicodeDecodeError,
    # whose .object holds the file's bytes. `from None` only hides that; this removes it (xreview r2 #4).
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise LoginFileError(f"MQTT_LOGIN_FILE {path!r}: not valid UTF-8")
    return _parse(text, path)
