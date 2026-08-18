"""
logger.py — Async GPX ride logger using gpxpy.
Appends a track point per GPS fix, flushes every GPX_FLUSH_EVERY fixes.
Filename derived from the first GPS timestamp.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import gpxpy
import gpxpy.gpx

from . import config
from .ride import GpsFix

log = logging.getLogger(__name__)


class RideLogger:
    def __init__(self) -> None:
        self._gpx: Optional[gpxpy.gpx.GPX] = None
        self._segment: Optional[gpxpy.gpx.GPXTrackSegment] = None
        self._path: Optional[Path] = None
        self._fix_count = 0
        self._loop = asyncio.get_event_loop()

    def start(self, first_fix: GpsFix) -> None:
        config.RIDES_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.fromtimestamp(first_fix.timestamp, tz=timezone.utc)
        fname = ts.strftime("ride_%Y%m%d_%H%M%S.gpx")
        self._path = config.RIDES_DIR / fname

        self._gpx = gpxpy.gpx.GPX()
        self._gpx.name = fname
        track = gpxpy.gpx.GPXTrack()
        self._gpx.tracks.append(track)
        self._segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(self._segment)

        log.info("Logging ride to %s", self._path)

    def record(self, fix: GpsFix) -> None:
        if self._gpx is None:
            self.start(fix)

        assert self._segment is not None
        pt = gpxpy.gpx.GPXTrackPoint(
            latitude=fix.lat,
            longitude=fix.lon,
            elevation=fix.alt,
            time=datetime.fromtimestamp(fix.timestamp, tz=timezone.utc),
            speed=fix.speed,
        )
        self._segment.points.append(pt)
        self._fix_count += 1

        if self._fix_count % config.GPX_FLUSH_EVERY == 0:
            self._flush()

    def _flush(self) -> None:
        if self._path is None or self._gpx is None:
            return
        try:
            xml = self._gpx.to_xml()
            self._path.write_text(xml, encoding="utf-8")
        except OSError as exc:
            log.error("GPX flush failed: %s", exc)

    def stop(self) -> Optional[Path]:
        """Flush final data and return the path to the completed file."""
        self._flush()
        return self._path

    @property
    def current_path(self) -> Optional[Path]:
        return self._path
