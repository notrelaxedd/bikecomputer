"""
gps.py — Async gpsd client.
Polls the gpsd socket, emits GpsFix objects via an asyncio.Queue.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

from .ride import GpsFix
from . import config

log = logging.getLogger(__name__)


class GpsClient:
    """
    Connects to gpsd, reads TPV (time-position-velocity) reports,
    and pushes valid GpsFix objects onto self.queue.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[GpsFix] = asyncio.Queue(maxsize=4)
        self._running = False
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._run(), name="gps-client")

    async def stop(self) -> None:
        self._running = False
        if self._writer:
            self._writer.close()

    async def _connect(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(config.GPSD_HOST, config.GPSD_PORT),
                timeout=5.0,
            )
            # gpsd sends a banner; consume it
            await asyncio.wait_for(self._reader.readline(), timeout=3.0)
            # enable JSON watch mode
            self._writer.write(b'?WATCH={"enable":true,"json":true}\n')
            await self._writer.drain()
            log.info("Connected to gpsd at %s:%d", config.GPSD_HOST, config.GPSD_PORT)
            return True
        except Exception as exc:
            log.warning("gpsd connect failed: %s", exc)
            return False

    async def _run(self) -> None:
        import json

        while self._running:
            if not await self._connect():
                await asyncio.sleep(5)
                continue

            try:
                while self._running:
                    line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
                    if not line:
                        break
                    try:
                        msg = json.loads(line.decode())
                    except json.JSONDecodeError:
                        continue

                    if msg.get("class") != "TPV":
                        continue

                    fix = self._parse_tpv(msg)
                    if fix is not None:
                        # drop oldest if consumer is slow
                        if self.queue.full():
                            try:
                                self.queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        await self.queue.put(fix)

            except asyncio.TimeoutError:
                log.warning("gpsd read timeout, reconnecting")
            except Exception as exc:
                log.warning("gpsd read error: %s", exc)
            finally:
                if self._writer:
                    try:
                        self._writer.close()
                    except Exception:
                        pass

            await asyncio.sleep(2)

    @staticmethod
    def _parse_tpv(msg: dict) -> Optional[GpsFix]:
        # mode 2=2D fix, 3=3D fix
        mode = msg.get("mode", 0)
        if mode < 2:
            return None

        lat = msg.get("lat")
        lon = msg.get("lon")
        if lat is None or lon is None:
            return None

        # gpsd reports speed in m/s
        speed = float(msg.get("speed", 0.0))
        alt   = float(msg.get("alt", 0.0))
        track = float(msg.get("track", 0.0))
        hdop  = float(msg.get("hdop", 99.9))
        sats  = int(msg.get("satellites", 0))  # not always present in TPV

        # gpsd time string → unix timestamp
        t_str = msg.get("time")
        if t_str:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                ts = dt.timestamp()
            except ValueError:
                ts = time.time()
        else:
            ts = time.time()

        return GpsFix(
            lat=float(lat),
            lon=float(lon),
            alt=alt,
            speed=speed,
            track=track,
            hdop=hdop,
            satellites=sats,
            timestamp=ts,
        )
