"""Unit tests for RideState — no hardware required."""

import time
import pytest
from src.bikecomputer.ride import RideState, GpsFix
from src.bikecomputer import config


@pytest.fixture
def strict_gps(monkeypatch):
    """
    The shipped defaults leave the quality filter off so a cold start still
    shows something; these tests are about the filter itself, so turn it on.
    Mirrors what ui.views.settings.apply_gps_filter does at runtime.
    """
    monkeypatch.setattr(config, "MIN_SATELLITES", 4)
    monkeypatch.setattr(config, "MAX_HDOP", 5.0)


def _fix(speed=5.0, lat=40.0, lon=-83.0, hdop=1.2, sats=8, ts=None) -> GpsFix:
    return GpsFix(
        lat=lat, lon=lon, alt=200.0, speed=speed,
        track=0.0, hdop=hdop, satellites=sats,
        timestamp=ts or time.time(),
    )


class TestFixValidation:
    def test_bad_hdop_rejected(self, strict_gps):
        s = RideState()
        s.update(_fix(hdop=6.0))
        assert not s.has_fix

    def test_low_sats_rejected(self, strict_gps):
        s = RideState()
        s.update(_fix(sats=3))
        assert not s.has_fix

    def test_filter_off_accepts_weak_fix(self):
        s = RideState()
        s.update(_fix(hdop=6.0, sats=3))
        assert s.has_fix

    def test_excessive_speed_rejected(self):
        s = RideState()
        s.update(_fix(speed=30.0))
        assert not s.has_fix

    def test_valid_fix_accepted(self):
        s = RideState()
        s.update(_fix())
        assert s.has_fix


class TestStats:
    def _two_fixes(self, speed=5.0, dt=1.0):
        s = RideState()
        t0 = 1_000_000.0
        s.update(_fix(speed=speed, lat=40.0, lon=-83.0, ts=t0))
        s.update(_fix(speed=speed, lat=40.0001, lon=-83.0, ts=t0 + dt))
        return s

    def test_moving_time_accumulates(self):
        s = self._two_fixes(speed=5.0, dt=2.0)
        assert s.moving_time > 0

    def test_autopause_below_threshold(self):
        s = self._two_fixes(speed=1.0, dt=2.0)
        assert s.paused

    def test_distance_positive_when_moving(self):
        s = self._two_fixes(speed=5.0, dt=1.0)
        assert s.distance > 0

    def test_avg_speed_calculated(self):
        s = self._two_fixes(speed=5.0, dt=1.0)
        assert s.avg_speed > 0

    def test_max_speed_tracked(self):
        s = RideState()
        t0 = 1_000_000.0
        s.update(_fix(speed=5.0,  ts=t0))
        s.update(_fix(speed=10.0, ts=t0 + 1))
        s.update(_fix(speed=7.0,  ts=t0 + 2))
        assert s.max_speed == pytest.approx(10.0)


class TestFormatDuration:
    def test_seconds_only(self):
        assert RideState.format_duration(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert RideState.format_duration(125) == "2:05"

    def test_hours(self):
        assert RideState.format_duration(3661) == "1:01:01"


class TestReset:
    def test_reset_clears_trip_but_keeps_position(self):
        s = RideState()
        t0 = 1_000_000.0
        s.update(_fix(speed=5.0, lat=40.0, lon=-83.0, ts=t0))
        s.update(_fix(speed=5.0, lat=40.0001, lon=-83.0, ts=t0 + 2))
        assert s.distance > 0

        s.reset()
        assert s.distance == 0
        assert s.moving_time == 0
        assert s.max_speed == 0
        assert s.lat == pytest.approx(40.0001)


class TestSensorFields:
    def test_sensors_default_to_none(self):
        s = RideState()
        assert s.heart_rate is None
        assert s.cadence is None
