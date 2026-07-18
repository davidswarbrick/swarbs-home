"""Configuration loading for swarbs-home.

Config is TOML. Resolution order (first match wins):
  1. an explicit path passed to load_config()
  2. $SWARBS_HOME_CONFIG
  3. ./swarbs-home.conf
  4. /etc/swarbs-home.conf
  5. built-in defaults (below)

Anything present in the file is merged over the defaults; the ``cards`` list,
if present, replaces the default list wholesale.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS: dict = {
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "title": "swarbs home",
        # Optional custom font (not shipped in the repo). Copy a font file to the
        # NAS, set font_file to its absolute path and font_family to its name.
        # Left empty, the pages use a system sans-serif stack.
        "font_family": "",
        "font_file": "",
    },
    "recorder": {
        "enabled": True,
        "device": "hw:CODEC",
        "mixes_dir": "~/Music/Mixes",
        "sample_rate": 44100,
        "channels": 2,
        "state_dir": "/run/swarbs-home",
        "recent_count": 10,
    },
    # Default cards; override the whole list in the config file.
    "cards": [
        {"name": "Recorder", "url": "/recorder"},
        {"name": "Music", "url": "http://nas.local:8533"},    # myMPD
        {"name": "Library", "url": "http://nas.local:8337"},  # beets web
        {"name": "Torrent", "url": "http://nas.local:9091"},
        {"name": "Syncthing", "url": "http://nas.local:8384"},
        {"name": "Admin", "url": "http://nas.local:8080"},
    ],
}

_FILE_CANDIDATES = ["./swarbs-home.conf", "/etc/swarbs-home.conf"]


@dataclass
class Config:
    server: dict
    recorder: dict
    cards: list
    source: str  # path we loaded, or "defaults"


def _deep_merge(base: dict, over: dict) -> dict:
    for key, value in over.items():
        if key == "cards":
            # lists are replaced, not merged
            base[key] = value
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | os.PathLike | None = None) -> Config:
    data = copy.deepcopy(DEFAULTS)

    if path:
        candidates = [str(path)]
    elif os.environ.get("SWARBS_HOME_CONFIG"):
        candidates = [os.environ["SWARBS_HOME_CONFIG"]]
    else:
        candidates = _FILE_CANDIDATES

    source = "defaults"
    for candidate in candidates:
        p = Path(candidate).expanduser()
        if p.is_file():
            with open(p, "rb") as fh:
                _deep_merge(data, tomllib.load(fh))
            source = str(p)
            break

    return Config(
        server=data["server"],
        recorder=data["recorder"],
        cards=data["cards"],
        source=source,
    )
