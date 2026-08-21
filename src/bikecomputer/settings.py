"""
settings.py — JSON-backed user preferences.

Anything the rider can change from the on-screen Settings menu lives here
rather than in config.py, so it survives a restart without editing code.
Writes are atomic (temp file + rename) because the Pi loses power the
moment the bike is switched off.
"""

from __future__ import annotations
import json
import logging
import os
import tempfile
from dataclasses import dataclass, asdict, field, fields
from typing import Any

from . import config

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # Units
    units: str = "imperial"            # "imperial" | "metric"

    # Ride behaviour
    autopause: bool = True
    gps_filter: bool = False           # apply MIN_SATELLITES / MAX_HDOP checks

    # Music
    music_source: str = "local"        # "local" | "spotify"
    volume: int = config.DEFAULT_VOLUME
    shuffle: bool = False
    repeat: bool = True

    # Bluetooth — MAC addresses ("AA:BB:CC:DD:EE:FF") of remembered devices.
    # These are auto-reconnected at startup.
    hr_device: str = ""                # BLE heart-rate strap
    audio_device: str = ""             # A2DP headphones / speaker
    auto_connect: bool = True

    # Which data screen to show on boot
    home_screen: str = "ride"          # "ride" | "map" | "detail"

    # Internal
    _path: Any = field(default=None, repr=False, compare=False)

    # ── Persistence ─────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path=None) -> "Settings":
        path = path or config.SETTINGS_FILE
        inst = cls()
        inst._path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.info("No settings file at %s; using defaults", path)
            return inst
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read settings (%s); using defaults", exc)
            return inst

        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for key, value in raw.items():
            if key in known:
                setattr(inst, key, value)
            else:
                log.debug("Ignoring unknown setting %r", key)
        return inst

    def save(self) -> None:
        path = self._path or config.SETTINGS_FILE
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)

    # ── Convenience ─────────────────────────────────────────────────────────

    @property
    def metric(self) -> bool:
        return self.units == "metric"

    def toggle(self, name: str) -> bool:
        """Flip a boolean setting, persist, and return the new value."""
        value = not getattr(self, name)
        setattr(self, name, value)
        self.save()
        return value

    def set(self, name: str, value) -> None:
        setattr(self, name, value)
        self.save()
