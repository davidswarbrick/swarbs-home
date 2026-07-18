"""Play recorded mixes through MPD, if it's running on this host.

Uses the ``mpc`` client (default connection localhost:6600, or MPD_HOST/MPD_PORT).
Availability is checked by actually talking to MPD, with a short cache so the
status poll doesn't spawn a subprocess every couple of seconds.

Playing a file appends it to MPD's queue and starts playback of that track. We
pass an absolute ``file://`` URI, so MPD plays the mix directly without needing a
database update — MPD must simply be able to read the file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time


class PlayerError(RuntimeError):
    pass


_cache = {"ts": 0.0, "ok": False}
_CACHE_TTL = 10.0

# MPD forbids adding local file:// URIs over TCP; a unix socket connection is
# treated as local and is allowed. Prefer a socket when one exists.
_SOCKETS = ("/run/mpd/socket", "/var/run/mpd/socket")


def _mpd_env() -> dict:
    env = dict(os.environ)
    if "MPD_HOST" not in env:
        for sock in _SOCKETS:
            if os.path.exists(sock):
                env["MPD_HOST"] = sock
                break
    return env


def _mpc(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["mpc", *args], capture_output=True, text=True, timeout=timeout,
        env=_mpd_env(),
    )


def available(now: float | None = None) -> bool:
    """True if mpc is installed and MPD answers. Cached for a few seconds."""
    now = time.time() if now is None else now
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["ok"]
    ok = False
    if shutil.which("mpc"):
        try:
            ok = _mpc(["status"], timeout=2).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    _cache["ts"] = now
    _cache["ok"] = ok
    return ok


def play_file(abs_path: str) -> None:
    """Append abs_path to the MPD queue and play it."""
    if not shutil.which("mpc"):
        raise PlayerError("mpc not installed")
    try:
        listing = _mpc(["playlist"])
        if listing.returncode != 0:
            raise PlayerError((listing.stderr or "cannot reach MPD").strip())
        pos = len([ln for ln in listing.stdout.splitlines() if ln.strip()]) + 1

        # MPD does not percent-decode file:// URIs, so pass the raw absolute path
        # (mpc protocol-quotes the argument, so spaces/specials are fine).
        uri = "file://" + abs_path
        added = _mpc(["add", uri])
        if added.returncode != 0:
            raise PlayerError((added.stderr or "MPD could not add the file").strip())

        played = _mpc(["play", str(pos)])
        if played.returncode != 0:
            raise PlayerError((played.stderr or "MPD could not start playback").strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlayerError(f"MPD command failed: {exc}") from exc
