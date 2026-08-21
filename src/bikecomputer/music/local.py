"""
music/local.py — Local file playback driven by mpv's JSON IPC socket.

mpv runs as a long-lived idle child process and is commanded over a unix
socket.  That buys gapless playback, every codec, and correct behaviour
when the Bluetooth sink disappears mid-song, for about a hundred lines —
none of which would be true of an in-process decoder on a Zero 2 W.

Audio routing is left to the system: with PipeWire (the default on
Bookworm) the connected A2DP headset becomes the default sink
automatically, so mpv needs no output configuration.
"""

from __future__ import annotations
import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config

log = logging.getLogger(__name__)

_HAS_UNIX_SOCKETS = hasattr(asyncio, "open_unix_connection")


@dataclass
class Track:
    path: Path
    title: str
    artist: str = ""

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


def scan_library(directory: Path | None = None) -> list[Track]:
    """Find playable files, recursively, sorted by path."""
    directory = directory or config.MUSIC_DIR
    if not directory.exists():
        return []

    tracks: list[Track] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in config.MUSIC_EXTENSIONS:
            continue
        tracks.append(Track(path=path, **_read_tags(path)))
    return tracks


def _read_tags(path: Path) -> dict:
    """Title/artist from tags when mutagen is present, else the filename."""
    try:
        import mutagen
        meta = mutagen.File(path, easy=True)
        if meta:
            title = (meta.get("title") or [None])[0]
            artist = (meta.get("artist") or [None])[0]
            if title:
                return {"title": title, "artist": artist or ""}
    except Exception as exc:
        log.debug("Tag read failed for %s: %s", path.name, exc)
    return {"title": path.stem, "artist": ""}


class LocalPlayer:
    """
    Controls an mpv child process.

    All public methods degrade to no-ops when mpv is missing or the socket
    is not connected, so the Music screen still renders (showing "mpv not
    installed") instead of the app dying.
    """

    def __init__(self) -> None:
        self.tracks: list[Track] = []
        self.index = 0
        self.playing = False
        self.error = ""
        self._loaded = False        # mpv holds the playlist

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._volume = config.DEFAULT_VOLUME
        self._reply_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._err_task: Optional[asyncio.Task] = None

    @property
    def available(self) -> bool:
        return self._writer is not None

    @property
    def current(self) -> Optional[Track]:
        if not self.tracks:
            return None
        return self.tracks[self.index % len(self.tracks)]

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self, volume: int | None = None) -> bool:
        self.refresh_library()
        if volume is not None:
            self._volume = volume

        if not _HAS_UNIX_SOCKETS:
            self.error = "Unix sockets unavailable"
            return False
        if shutil.which(config.MPV_BINARY) is None:
            self.error = "mpv not installed"
            log.warning("mpv binary not found; local playback disabled")
            return False

        try:
            config.MPV_IPC_SOCKET.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("Could not clear mpv socket: %s", exc)

        cmd = [
            config.MPV_BINARY,
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            # Not --really-quiet: mpv reports audio-device failures on
            # stderr, and discarding them turns "no sound" into a silent
            # mystery. Warnings and errors only, drained into our log.
            "--msg-level=all=warn",
            "--gapless-audio=yes",
            "--audio-display=no",
            f"--volume={self._volume}",
            f"--input-ipc-server={config.MPV_IPC_SOCKET}",
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            self.error = f"mpv failed: {exc}"
            log.error("Could not start mpv: %s", exc)
            return False

        if not await self._connect_socket():
            self.error = "mpv IPC did not come up"
            return False

        self._read_task = asyncio.create_task(self._read_loop(), name="mpv-ipc")
        self._err_task = asyncio.create_task(self._drain_stderr(), name="mpv-err")
        log.info("mpv ready, %d tracks in library", len(self.tracks))
        return True

    async def _drain_stderr(self) -> None:
        """Surface mpv's own complaints; it knows why it is not playing."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            try:
                line = await proc.stderr.readline()
            except (asyncio.CancelledError, ValueError):
                raise
            except Exception:
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                log.warning("mpv: %s", text)

    async def _connect_socket(self, attempts: int = 25) -> bool:
        """mpv creates the socket a moment after exec, so poll for it."""
        for _ in range(attempts):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    str(config.MPV_IPC_SOCKET)
                )
                return True
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                await asyncio.sleep(0.2)
        return False

    async def stop(self) -> None:
        for task in (self._read_task, self._err_task):
            if task:
                task.cancel()
        self._read_task = None
        self._err_task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    # ── IPC plumbing ────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        while self._reader is not None:
            try:
                line = await self._reader.readline()
            except (asyncio.CancelledError, ConnectionResetError):
                raise
            except Exception:
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            if "request_id" in msg:
                future = self._pending.pop(msg["request_id"], None)
                if future and not future.done():
                    future.set_result(msg)
            elif msg.get("event") == "idle":
                # mpv ran off the end of the playlist.
                self.playing = False

        log.info("mpv IPC closed")
        self._writer = None

    async def _command(self, *args, expect_reply: bool = False):
        if self._writer is None:
            return None
        self._reply_id += 1
        request_id = self._reply_id
        payload = json.dumps({"command": list(args), "request_id": request_id})

        future: Optional[asyncio.Future] = None
        if expect_reply:
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future

        try:
            self._writer.write(payload.encode() + b"\n")
            await self._writer.drain()
        except Exception as exc:
            log.debug("mpv write failed: %s", exc)
            self._pending.pop(request_id, None)
            self._writer = None
            return None

        if future is None:
            return None
        try:
            reply = await asyncio.wait_for(future, timeout=2.0)
            return reply.get("data")
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return None

    # ── Library ─────────────────────────────────────────────────────────────

    def refresh_library(self) -> int:
        self.tracks = scan_library()
        if self.index >= len(self.tracks):
            self.index = 0
        # New files on disk mean mpv's copy of the playlist is out of date.
        self._loaded = False
        return len(self.tracks)

    # ── Transport ───────────────────────────────────────────────────────────

    async def _ensure_playlist(self) -> bool:
        """
        Hand mpv the whole library as one playlist.

        Loading a single file per track would stop the music dead at the
        end of every song, which is exactly when the rider is least able
        to reach for a button.  With a real playlist mpv rolls on by
        itself, gaplessly, and skip becomes a playlist seek.
        """
        if self._loaded or not self.tracks or not self.available:
            return self._loaded

        for position, track in enumerate(self.tracks):
            mode = "replace" if position == 0 else "append"
            await self._command("loadfile", str(track.path), mode)

        await self._command("set_property", "loop-playlist", "inf")
        self._loaded = True
        return True

    async def _sync_index(self) -> None:
        """Adopt mpv's position, which advances on its own between tracks."""
        position = await self._command("get_property", "playlist-pos",
                                       expect_reply=True)
        if isinstance(position, int) and 0 <= position < len(self.tracks):
            self.index = position

    async def play_index(self, index: int) -> None:
        if not self.tracks or not await self._ensure_playlist():
            return
        self.index = index % len(self.tracks)
        await self._command("set_property", "playlist-pos", self.index)
        await self._command("set_property", "pause", False)
        self.playing = True
        log.info("Playing %s", self.tracks[self.index].label)

    async def toggle(self) -> None:
        if not self.available or not self.tracks:
            return
        if not self._loaded:
            await self.play_index(self.index)
            return
        await self._command("cycle", "pause")
        self.playing = not self.playing

    async def next(self) -> None:
        await self.play_index(self.index + 1)

    async def previous(self) -> None:
        await self.play_index(self.index - 1)

    async def sync(self) -> None:
        """Re-read mpv's position so the UI follows automatic advances."""
        if self._loaded:
            await self._sync_index()

    async def set_shuffle(self, on: bool) -> None:
        if not await self._ensure_playlist():
            return
        # mpv has no shuffle flag to clear, only shuffle/unshuffle commands.
        await self._command("playlist-shuffle" if on else "playlist-unshuffle")
        await self._sync_index()

    async def pause(self) -> None:
        if self.playing:
            await self._command("set_property", "pause", True)
            self.playing = False

    async def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, volume))
        await self._command("set_property", "volume", self._volume)

    @property
    def volume(self) -> int:
        return self._volume

    def status_text(self) -> str:
        if self.error:
            return self.error
        if not self.tracks:
            return f"No music in {config.MUSIC_DIR}"
        track = self.current
        return track.label if track else "-"
