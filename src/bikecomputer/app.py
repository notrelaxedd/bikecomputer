"""
app.py — Main asyncio event loop.

Ties together GPS, display, ride state, logging, and upload.
Button on BUTTON_PIN (BCM) cycles through screens.
"""

from __future__ import annotations
import asyncio
import logging
import signal
import time
from enum import IntEnum
from typing import Optional

import RPi.GPIO as GPIO
from PIL import Image

from . import config
from .display import Display
from .gps import GpsClient
from .ride import RideState, GpsFix
from .logger import RideLogger
from .mapview import MapView
from .screens import RideScreen, MapScreen, DetailScreen

log = logging.getLogger(__name__)


class Screen(IntEnum):
    RIDE   = 0
    MAP    = 1
    DETAIL = 2


class BikeComputer:
    def __init__(self) -> None:
        self._display   = Display()
        self._gps       = GpsClient()
        self._state     = RideState()
        self._logger    = RideLogger()
        self._mapview: Optional[MapView] = None

        self._screen = Screen.RIDE
        self._running = False
        self._last_button = 0.0

        # Screens are instantiated after mapview is ready
        self._ride_screen:   Optional[RideScreen]   = None
        self._map_screen:    Optional[MapScreen]     = None
        self._detail_screen: Optional[DetailScreen]  = None

    async def run(self) -> None:
        log.info("Starting bike computer")
        self._running = True

        # Attempt upload of previous rides before starting
        try:
            from .upload import upload_pending
            await asyncio.wait_for(upload_pending(), timeout=30.0)
        except Exception as exc:
            log.info("Upload skipped: %s", exc)

        # Init map (may be unavailable)
        try:
            self._mapview = MapView()
        except Exception as exc:
            log.warning("MapView init failed: %s", exc)

        # Init screens
        self._ride_screen   = RideScreen()
        self._map_screen    = MapScreen(self._mapview if (self._mapview and self._mapview.available) else None)
        self._detail_screen = DetailScreen()

        # Init display hardware
        self._display.init()
        self._display.fill()

        # GPIO button
        GPIO.setup(config.BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            config.BUTTON_PIN,
            GPIO.FALLING,
            callback=self._button_isr,
            bouncetime=int(config.BUTTON_DEBOUNCE * 1000),
        )

        # Start GPS
        await self._gps.start()

        # Register SIGTERM / SIGINT for clean shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_stop)

        # Run render and GPS tasks concurrently
        await asyncio.gather(
            self._gps_task(),
            self._render_task(),
        )

        await self._shutdown()

    def _request_stop(self) -> None:
        log.info("Shutdown requested")
        self._running = False

    def _button_isr(self, channel: int) -> None:
        now = time.monotonic()
        if now - self._last_button < config.BUTTON_DEBOUNCE:
            return
        self._last_button = now
        self._screen = Screen((self._screen + 1) % len(Screen))
        log.debug("Screen → %s", self._screen.name)

    # ── GPS consumer ────────────────────────────────────────────────────────

    async def _gps_task(self) -> None:
        logging_started = False
        while self._running:
            try:
                fix: GpsFix = await asyncio.wait_for(
                    self._gps.queue.get(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue

            self._state.update(fix)

            if self._state.has_fix:
                if not logging_started:
                    logging_started = True
                self._logger.record(fix)

    # ── Render loop ──────────────────────────────────────────────────────────

    async def _render_task(self) -> None:
        while self._running:
            fps = config.MAP_FPS if self._screen == Screen.MAP else config.DATA_FPS
            frame_time = 1.0 / fps

            t0 = time.monotonic()
            try:
                img = self._render_current_screen()
                self._display.blit(img)
            except Exception as exc:
                log.error("Render error: %s", exc, exc_info=True)

            elapsed = time.monotonic() - t0
            sleep = max(0.0, frame_time - elapsed)
            await asyncio.sleep(sleep)

    def _render_current_screen(self) -> Image.Image:
        if self._screen == Screen.RIDE:
            return self._ride_screen.render(self._state)

        if self._screen == Screen.MAP:
            cue = None
            if (self._mapview and self._mapview.available
                    and self._state.lat is not None):
                cue = self._mapview.nearest_cue(self._state.lat, self._state.lon)
            return self._map_screen.render(self._state, cue_text=cue)

        if self._screen == Screen.DETAIL:
            return self._detail_screen.render(self._state)

        return self._ride_screen.render(self._state)

    # ── Shutdown ─────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        log.info("Shutting down")
        await self._gps.stop()
        path = self._logger.stop()
        if path:
            log.info("Ride saved to %s", path)
        self._display.fill((0, 0, 0))
        self._display.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = BikeComputer()
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
