"""
units.py — Formatting helpers that respect the rider's unit preference.

Every value stored in RideState is SI (metres, m/s).  Conversion happens
only at render time so that logged GPX files and Strava uploads stay
unit-neutral.
"""

from __future__ import annotations

_MS_TO_MPH  = 2.236936
_MS_TO_KMH  = 3.6
_M_TO_MILE  = 1.0 / 1609.344
_M_TO_KM    = 1.0 / 1000.0
_M_TO_FEET  = 3.280840


def speed(ms: float, metric: bool) -> float:
    """m/s → mph or km/h."""
    return ms * (_MS_TO_KMH if metric else _MS_TO_MPH)


def speed_unit(metric: bool) -> str:
    return "km/h" if metric else "mph"


def distance(metres: float, metric: bool) -> float:
    """metres → km or miles."""
    return metres * (_M_TO_KM if metric else _M_TO_MILE)


def distance_unit(metric: bool) -> str:
    return "km" if metric else "mi"


def altitude(metres: float, metric: bool) -> float:
    """metres → metres or feet."""
    return metres if metric else metres * _M_TO_FEET


def altitude_unit(metric: bool) -> str:
    return "m" if metric else "ft"


def fmt_speed(ms: float, metric: bool, decimals: int = 1) -> str:
    return f"{speed(ms, metric):.{decimals}f}"


def fmt_distance(metres: float, metric: bool, decimals: int = 2) -> str:
    return f"{distance(metres, metric):.{decimals}f}"


def fmt_altitude(metres: float, metric: bool) -> str:
    return f"{altitude(metres, metric):.0f}"


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"
