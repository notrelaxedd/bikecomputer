"""
app.py — Main asyncio event loop.

Four cooperating tasks: GPS ingest, button dispatch, rendering, and slow
housekeeping (Bluetooth link status).  Everything the UI touches lives in
the shared AppContext, so views never reach for globals.

Subsystems are optional by design.  No mbtiles, no dbus, no mpv, no GPS —
each failure degrades one screen and the rest of the computer keeps
riding.
"""

from __future__ import annotations
import asyncio
import logging
import signal
import time
from typing import Optional

from PIL import Image

from . import config
from .bluetooth import BluetoothManager
from .buttons import Buttons
from .display import Display
from .gps import GpsClient
from .logger import RideLogger
from .mapview import MapView as MapRenderer
from .music import MusicController
from .ride import GpsFix, RideState
from .sensors import SensorHub
from .settings import Settings
from .ui import AppContext, Navigator
from .ui.views import (
    DetailView, MapView, RideView, RootMenu, apply_gps_filter,
)

log = logging.getLogger(__name__)

HOUSEKEEPING_INTERVAL = 4.0


class BikeComputer:
    def __init__(self) -> None:
        self._settings = Settings.load()
        apply_gps_filter(self._settings)

        self._display = Display()
        self._gps = GpsClient()
        self._state = RideState()
        self._logger = RideLogger()
        self._buttons = Buttons()
        self._bt = BluetoothManager()
        self._music = MusicController(self._settings)
        self._sensors = SensorHub(self._bt, self._state, self._settings)
        self._maprender: Optional[MapRenderer] = None

        self._ctx: Optional[AppContext] = None
        self._nav: Optional[Navigator] = None
        self._running = False
        self._post_exit_command = ""

    # ── Startup ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        log.info("Starting bike computer (%dx%d portrait)",
                 config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
        self._running = True

        # Bring the screen up before anything slow, so the rider sees the
        # boot message instead of a dark panel for several seconds.
        self._display.init()
        self._splash("Starting…")

        self._buttons.setup()

        try:
            self._maprender = MapRenderer()
        except Exception as exc:
            log.warning("MapView init failed: %s", exc)

        self._ctx = AppContext(
            state=self._state,
            settings=self._settings,
            mapview=self._maprender,
            logger=self._logger,
            request_shutdown=self._request_system_command,
        )
        self._ctx.music = self._music
        self._ctx.bt = self._bt

        self._nav = Navigator(
            pages=[
                RideView(),
                MapView(self._maprender),
                DetailView(),
            ],
            ctx=self._ctx,
        )
        self._nav.set_menu_factory(RootMenu)
        await self._nav.start(home=self._settings.home_screen)

        await self._start_subsystems()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_stop)

        await asyncio.gather(
            self._gps_task(),
            self._button_task(),
            self._render_task(),
            self._housekeeping_task(),
        )

        await self._shutdown()

    async def _start_subsystems(self) -> None:
        """Start everything optional, each failure isolated to its own line."""
        self._splash("Starting music…")
        try:
            await self._music.start()
        except Exception as exc:
            log.warning("Music unavailable: %s", exc)

        self._splash("Starting Bluetooth…")
        try:
            if await self._bt.start():
                await self._sensors.start()
                if self._settings.auto_connect:
                    asyncio.create_task(self._auto_connect(), name="bt-autoconnect")
        except Exception as exc:
            log.warning("Bluetooth unavailable: %s", exc)

        self._splash("Uploading rides…")
        try:
            from .upload import upload_pending
            await asyncio.wait_for(upload_pending(), timeout=30.0)
        except Exception as exc:
            log.info("Upload skipped: %s", exc)

        await self._buttons.start()
        await self._gps.start()

    async def _auto_connect(self) -> None:
        """
        Reconnect saved devices in the background.

        BlueZ can sit on a connect attempt for the better part of a minute
        when a strap is out of range, so this must never be awaited on the
        startup path.
        """
        await self._bt.connect_remembered(
            [self._settings.hr_device, self._settings.audio_device]
        )

    def _splash(self, message: str) -> None:
        from PIL import ImageDraw
        from .ui import theme
        img = Image.new("RGB", (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT),
                        config.CLR_BG)
        draw = ImageDraw.Draw(img)
        theme.centred(draw, config.DISPLAY_HEIGHT // 2 - 40, "BIKE COMPUTER",
                      theme.font(24, bold=True), config.CLR_TEXT)
        theme.centred(draw, config.DISPLAY_HEIGHT // 2 + 4, message,
                      theme.font(14), config.CLR_DIM)
        try:
            self._display.blit(img)
        except Exception as exc:
            log.debug("Splash blit failed: %s", exc)

    # ── Shutdown ────────────────────────────────────────────────────────────

    def _request_stop(self) -> None:
        log.info("Shutdown requested")
        self._running = False

    def _request_system_command(self, command: str) -> None:
        """Called from the Settings menu; halt cleanly, then poweroff/reboot."""
        self._post_exit_command = command
        self._running = False

    # ── GPS ─────────────────────────────────────────────────────────────────

    async def _gps_task(self) -> None:
        while self._running:
            try:
                fix: GpsFix = await asyncio.wait_for(self._gps.queue.get(),
                                                     timeout=2.0)
            except asyncio.TimeoutError:
                continue

            self._state.update(fix)
            if self._state.has_fix:
                self._logger.record(fix)

    # ── Input ───────────────────────────────────────────────────────────────

    async def _button_task(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._buttons.queue.get(),
                                               timeout=1.0)
            except asyncio.TimeoutError:
                continue
            log.debug("Button %s", event)
            await self._nav.dispatch(event)

    # ── Render ──────────────────────────────────────────────────────────────

    async def _render_task(self) -> None:
        while self._running:
            frame_time = 1.0 / max(1, self._nav.fps)
            started = time.monotonic()

            try:
                await self._nav.tick()
                self._display.blit(self._nav.render())
            except Exception as exc:
                log.error("Render error: %s", exc, exc_info=True)
                # The panel may hold a half-written frame, so the cached
                # baseline is no longer trustworthy. Repaint everything
                # next time rather than diffing against a lie.
                self._display.invalidate()

            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, frame_time - elapsed))

    # ── Housekeeping ────────────────────────────────────────────────────────

    async def _housekeeping_task(self) -> None:
        """
        Slow-changing status the status bar needs but no view should block on.
        """
        while self._running:
            try:
                address = self._settings.audio_device
                if address and self._bt.available:
                    device = await self._bt.device(address)
                    self._ctx.audio_connected = bool(device and device.connected)
                else:
                    self._ctx.audio_connected = False
            except Exception as exc:
                log.debug("Housekeeping error: %s", exc)
            await asyncio.sleep(HOUSEKEEPING_INTERVAL)

    # ── Teardown ────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        log.info("Shutting down")
        self._splash("Saving ride…")

        await self._gps.stop()
        await self._buttons.stop()
        await self._sensors.stop()
        await self._music.stop()
        await self._bt.stop()

        path = self._logger.stop()
        if path:
            log.info("Ride saved to %s", path)

        self._settings.save()

        self._display.fill((0, 0, 0))
        self._display.close()

        if self._post_exit_command:
            log.info("Issuing system %s", self._post_exit_command)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", self._post_exit_command
                )
                await proc.wait()
            except OSError as exc:
                log.error("Could not %s: %s", self._post_exit_command, exc)


def main() -> None:
    # Button presses log at DEBUG, so at the default level a dead button
    # and a working one look identical from the logs. BIKECOMPUTER_LOG
    # exists to tell those apart without editing code on the bike:
    #   BIKECOMPUTER_LOG=DEBUG venv/bin/python -m bikecomputer.app
    import os
    level = os.environ.get("BIKECOMPUTER_LOG", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(BikeComputer().run())


if __name__ == "__main__":
    main()
