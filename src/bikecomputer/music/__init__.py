"""
music — one transport interface over two very different backends.

The UI never asks which source is active; it calls toggle()/next()/
previous() on MusicController and the controller routes to whichever
backend the rider selected in Settings.  Switching sources pauses the old
one first, so two players can never talk to the audio sink at once.
"""

from __future__ import annotations
import asyncio
import logging

from .local import LocalPlayer, Track, scan_library
from .spotify import SpotifyPlayer, is_linked as spotify_linked

log = logging.getLogger(__name__)

SOURCE_LOCAL = "local"
SOURCE_SPOTIFY = "spotify"

__all__ = [
    "MusicController", "LocalPlayer", "SpotifyPlayer", "Track",
    "scan_library", "spotify_linked", "SOURCE_LOCAL", "SOURCE_SPOTIFY",
]


class MusicController:
    def __init__(self, settings) -> None:
        self._settings = settings
        self.local = LocalPlayer()
        self.spotify = SpotifyPlayer()
        self._poll_task = None
        self._running = False

    @property
    def source(self) -> str:
        return self._settings.music_source

    @property
    def backend(self):
        return self.spotify if self.source == SOURCE_SPOTIFY else self.local

    @property
    def playing(self) -> bool:
        if self.source == SOURCE_SPOTIFY:
            return self.spotify.now.playing
        return self.local.playing

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        volume = self._settings.volume

        # The local player always comes up: it is the fallback whenever
        # Spotify is unlinked or offline, and starting mpv idle is cheap.
        await self.local.start(volume=volume)

        if self.source == SOURCE_SPOTIFY:
            await self.spotify.start(volume=volume)

        self._poll_task = asyncio.create_task(self._poll(), name="music-poll")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        await self.local.stop()
        await self.spotify.stop()

    async def _poll(self) -> None:
        """
        Neither backend pushes state at us: Spotify's lives on their
        servers, and mpv advances its own playlist without telling us.
        """
        while self._running:
            await asyncio.sleep(5.0)
            await self.refresh()

    async def refresh(self) -> None:
        try:
            if self.source == SOURCE_SPOTIFY:
                if self.spotify.available:
                    await self.spotify.refresh_state()
            else:
                await self.local.sync()
        except Exception as exc:
            log.debug("Music refresh failed: %s", exc)

    # ── Source switching ────────────────────────────────────────────────────

    async def set_source(self, source: str) -> tuple[bool, str]:
        if source == self.source:
            return True, ""

        # Silence the outgoing backend before the new one can start.
        try:
            await self.backend.pause()
        except Exception:
            pass

        self._settings.set("music_source", source)

        if source == SOURCE_SPOTIFY:
            if not spotify_linked():
                self._settings.set("music_source", SOURCE_LOCAL)
                return False, "Link Spotify over SSH first"
            ok = await self.spotify.start(volume=self._settings.volume)
            if not ok:
                self._settings.set("music_source", SOURCE_LOCAL)
                return False, self.spotify.error or "Spotify unavailable"
            return True, "Source: Spotify"

        return True, "Source: Local files"

    # ── Transport (routed) ──────────────────────────────────────────────────

    async def toggle(self) -> None:
        await self.backend.toggle()

    async def next(self) -> None:
        await self.backend.next()

    async def previous(self) -> None:
        await self.backend.previous()

    async def pause(self) -> None:
        await self.backend.pause()

    async def set_volume(self, volume: int) -> int:
        volume = max(0, min(100, volume))
        self._settings.set("volume", volume)
        try:
            await self.backend.set_volume(volume)
        except Exception as exc:
            log.debug("Volume change failed: %s", exc)
        return volume

    async def nudge_volume(self, delta: int) -> int:
        return await self.set_volume(self._settings.volume + delta)

    async def set_shuffle(self, on: bool) -> None:
        self._settings.set("shuffle", on)
        if self.source == SOURCE_LOCAL:
            await self.local.set_shuffle(on)

    # ── Display ─────────────────────────────────────────────────────────────

    def now_playing(self) -> str:
        return self.backend.status_text()

    def source_label(self) -> str:
        return "Spotify" if self.source == SOURCE_SPOTIFY else "Local files"
