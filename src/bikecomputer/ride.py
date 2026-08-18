"""
ride.py — RideState dataclass and pure stats accumulation.
No I/O; safe to unit-test without hardware.
"""

from __future__ import annotations
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class GpsFix:
    lat: float
    lon: float
    alt: float          # metres
    speed: float        # m/s  (from gpsd velocity solution)
    track: float        # degrees true
    hdop: float
    satellites: int
    timestamp: float    # unix time


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fix_valid(fix: GpsFix, prev: Optional[GpsFix]) -> bool:
    if fix.hdop > config.MAX_HDOP:
        return False
    if fix.satellites < config.MIN_SATELLITES:
        return False
    if fix.speed > config.MAX_SPEED_MS:
        return False
    if prev is not None:
        dt = fix.timestamp - prev.timestamp
        if dt > 0:
            implied = _haversine(prev.lat, prev.lon, fix.lat, fix.lon) / dt
            if implied > config.MAX_SPEED_MS:
                return False
    return True


@dataclass
class RideState:
    # Current sensor values
    speed: float = 0.0          # m/s
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude: float = 0.0       # m
    track: float = 0.0          # degrees

    # GPS quality
    satellites: int = 0
    hdop: float = 99.9
    has_fix: bool = False

    # Accumulated stats
    distance: float = 0.0       # m
    moving_time: float = 0.0    # s
    elapsed_time: float = 0.0   # s
    avg_speed: float = 0.0      # m/s
    max_speed: float = 0.0      # m/s
    max_altitude: float = 0.0   # m

    # Internal
    _paused: bool = field(default=True, repr=False)
    _ride_start: Optional[float] = field(default=None, repr=False)
    _last_fix: Optional[GpsFix] = field(default=None, repr=False)
    _last_update: Optional[float] = field(default=None, repr=False)

    def update(self, fix: GpsFix) -> None:
        """
        Ingest one GPS fix.  Pure function with respect to I/O —
        mutates only self fields.
        """
        now = fix.timestamp

        if self._ride_start is None:
            self._ride_start = now

        self.satellites = fix.satellites
        self.hdop = fix.hdop

        if not _fix_valid(fix, self._last_fix):
            self._last_update = now
            return

        self.has_fix = True
        self.lat = fix.lat
        self.lon = fix.lon
        self.altitude = fix.alt
        self.track = fix.track

        speed = fix.speed
        self.speed = speed

        # elapsed wall time
        if self._last_update is not None:
            dt = now - self._last_update
            self.elapsed_time += dt

            # auto-pause
            moving = speed >= config.AUTOPAUSE_MS
            if moving:
                self._paused = False
                self.moving_time += dt

                if self._last_fix is not None and self._last_fix.lat is not None:
                    d = _haversine(self._last_fix.lat, self._last_fix.lon, fix.lat, fix.lon)
                    self.distance += d

                if speed > self.max_speed:
                    self.max_speed = speed
            else:
                self._paused = True

        if fix.alt > self.max_altitude:
            self.max_altitude = fix.alt

        if self.moving_time > 0:
            self.avg_speed = self.distance / self.moving_time

        self._last_fix = fix
        self._last_update = now

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed_kmh(self) -> float:
        return self.speed * 2.23694

    @property
    def avg_speed_kmh(self) -> float:
        return self.avg_speed * 2.23694

    @property
    def max_speed_kmh(self) -> float:
        return self.max_speed * 2.23694

    @property
    def distance_km(self) -> float:
        return self.distance / 1609.34

    @staticmethod
    def format_duration(seconds: float) -> str:
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{sec:02d}"
        return f"{m}:{sec:02d}"
