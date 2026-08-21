"""
music/spotify.py — Spotify Connect control (optional, needs Premium).

Two halves, because Spotify splits them:

  * librespot runs on the Pi and registers as a Spotify Connect speaker.
    It does the audio; this module only supervises the process.
  * The Web API does the control — play/pause/skip and "what's playing".
    Spotify offers no local control protocol, so transport commands go
    out over the network.

Consequence worth knowing before relying on it mid-ride: skipping a track
needs a working data connection.  Audio itself keeps streaming from
librespot's buffer, but with no signal the buttons stop responding.  Local
files (music/local.py) have no such dependency, which is why they stay the
default source.

Linking the account is a one-time step run over SSH:

    python -m bikecomputer.music.spotify auth

Authorization Code with PKCE is used, so no client secret ever lands on
the Pi — only a refresh token, which is stored 0600.
"""

from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .. import config

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    playing: bool = False
    device: str = ""

    @property
    def label(self) -> str:
        if not self.title:
            return "Nothing playing"
        return f"{self.artist} - {self.title}" if self.artist else self.title


# ── Token storage ───────────────────────────────────────────────────────────

def _load_tokens() -> dict:
    try:
        return json.loads(config.SPOTIFY_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_tokens(tokens: dict) -> None:
    path = config.SPOTIFY_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_linked() -> bool:
    return bool(_load_tokens().get("refresh_token"))


# ── Runtime controller ──────────────────────────────────────────────────────

class SpotifyPlayer:
    """Drives Spotify Connect playback on the librespot device."""

    def __init__(self) -> None:
        self.error = ""
        self.now = NowPlaying()
        self._tokens = _load_tokens()
        self._access: str = ""
        self._expires_at: float = 0.0
        self._session = None
        self._librespot: Optional[asyncio.subprocess.Process] = None
        self._device_id: str = ""

    @property
    def linked(self) -> bool:
        return bool(self._tokens.get("refresh_token"))

    @property
    def available(self) -> bool:
        return self.linked and not self.error

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self, volume: int = 70) -> bool:
        self._tokens = _load_tokens()
        if not self.linked:
            self.error = "Not linked"
            return False
        if not config.SPOTIFY_CLIENT_ID:
            self.error = "No client ID in config"
            return False

        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
        except ImportError:
            self.error = "aiohttp missing"
            return False

        await self._start_librespot(volume)
        ok = await self._refresh_access_token()
        if not ok:
            return False
        await self.refresh_state()
        return True

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        if self._librespot and self._librespot.returncode is None:
            self._librespot.terminate()
            try:
                await asyncio.wait_for(self._librespot.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._librespot.kill()
        self._librespot = None

    @property
    def librespot_logged_in(self) -> bool:
        """
        True once librespot has cached a credential blob.

        Until then it can only announce itself over zeroconf to devices on
        the same LAN, which means the Web API cannot see it and playback
        can never be transferred here.  The one-time fix is an interactive
        sign-in over SSH; see the README.
        """
        return (config.SPOTIFY_CACHE_DIR / "credentials.json").exists()

    async def _start_librespot(self, volume: int) -> None:
        binary = shutil.which("librespot")
        if binary is None:
            log.info("librespot not installed - control-only mode, which is "
                     "all that is needed to drive playback on your phone")
            return
        if not self.librespot_logged_in:
            log.warning("librespot has no cached credentials in %s; run "
                        "`librespot -n %s -c %s -j` once to sign in. Until "
                        "then the Pi cannot be a playback target.",
                        config.SPOTIFY_CACHE_DIR, config.SPOTIFY_DEVICE_NAME,
                        config.SPOTIFY_CACHE_DIR)
            return

        cmd = [
            binary,
            "--name", config.SPOTIFY_DEVICE_NAME,
            "--bitrate", "160",
            "--device-type", "computer",
            "--initial-volume", str(volume),
            "--cache", str(config.SPOTIFY_CACHE_DIR),
            # Credentials still cache; only the audio files are not kept.
            # The SD card would not thank us for caching those.
            "--disable-audio-cache",
        ]
        if config.SPOTIFY_BACKEND:
            cmd += ["--backend", config.SPOTIFY_BACKEND]
        cmd += list(config.SPOTIFY_LIBRESPOT_ARGS)

        try:
            self._librespot = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            log.info("librespot started as %r", config.SPOTIFY_DEVICE_NAME)
        except OSError as exc:
            log.warning("Could not start librespot: %s", exc)

    # ── Auth ────────────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> bool:
        if self._session is None:
            self.error = "Not started"
            return False
        if time.time() < self._expires_at - 60 and self._access:
            return True

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._tokens.get("refresh_token", ""),
            "client_id": config.SPOTIFY_CLIENT_ID,
        }
        try:
            async with self._session.post(TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self.error = f"Auth failed ({resp.status})"
                    log.error("Spotify token refresh failed: %s", body[:200])
                    return False
                payload = await resp.json()
        except Exception as exc:
            self.error = "Network error"
            log.warning("Spotify token refresh error: %s", exc)
            return False

        self._access = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 3600)
        # Spotify rotates refresh tokens; persist the new one when given.
        if payload.get("refresh_token"):
            self._tokens["refresh_token"] = payload["refresh_token"]
            _save_tokens(self._tokens)
        self.error = ""
        return True

    async def _api(self, method: str, path: str, **kwargs):
        if not await self._refresh_access_token():
            return None
        headers = {"Authorization": f"Bearer {self._access}"}
        try:
            async with self._session.request(
                method, API_BASE + path, headers=headers, **kwargs
            ) as resp:
                if resp.status == 204:
                    return {}
                if resp.status == 404:
                    self.error = "No active device"
                    return None
                if resp.status >= 400:
                    body = await resp.text()
                    log.debug("Spotify %s %s -> %s %s", method, path,
                              resp.status, body[:160])
                    self.error = f"API {resp.status}"
                    return None
                self.error = ""
                return await resp.json(content_type=None)
        except Exception as exc:
            self.error = "Network error"
            log.debug("Spotify request failed: %s", exc)
            return None

    # ── Transport ───────────────────────────────────────────────────────────

    async def refresh_state(self) -> NowPlaying:
        data = await self._api("GET", "/me/player")
        if not data:
            self.now = NowPlaying()
            return self.now

        item = data.get("item") or {}
        artists = item.get("artists") or []
        device = data.get("device") or {}
        if device.get("id"):
            self._device_id = device["id"]

        self.now = NowPlaying(
            title=item.get("name", ""),
            artist=artists[0].get("name", "") if artists else "",
            playing=bool(data.get("is_playing")),
            device=device.get("name", ""),
        )
        return self.now

    async def toggle(self) -> None:
        if self.now.playing:
            await self._api("PUT", "/me/player/pause")
            self.now.playing = False
        else:
            await self._api("PUT", "/me/player/play")
            self.now.playing = True

    async def next(self) -> None:
        await self._api("POST", "/me/player/next")
        await asyncio.sleep(0.4)      # let Spotify settle before re-reading
        await self.refresh_state()

    async def previous(self) -> None:
        await self._api("POST", "/me/player/previous")
        await asyncio.sleep(0.4)
        await self.refresh_state()

    async def pause(self) -> None:
        if self.now.playing:
            await self.toggle()

    async def set_volume(self, volume: int) -> None:
        await self._api("PUT", "/me/player/volume",
                        params={"volume_percent": str(max(0, min(100, volume)))})

    async def devices(self) -> list[dict]:
        """
        Every Spotify Connect device on the account.

        The phone belongs in this list as much as the Pi does: with the
        headphones paired to the phone, the phone streams and the bike
        computer is only a remote.  That keeps audio off the Pi's radio
        entirely, so a stalled render or a dropped hotspot cannot glitch
        the music.
        """
        payload = await self._api("GET", "/me/player/devices")
        if not payload:
            return []
        return payload.get("devices", [])

    async def transfer_to(self, device_id: str, name: str = "") -> tuple[bool, str]:
        result = await self._api(
            "PUT", "/me/player",
            json={"device_ids": [device_id], "play": True},
        )
        if result is None:
            return False, self.error or "Transfer failed"
        self._device_id = device_id
        await asyncio.sleep(0.4)
        await self.refresh_state()
        return True, f"Playing on {name}" if name else "Playback moved"

    async def transfer_here(self) -> tuple[bool, str]:
        """Move playback onto the Pi's own librespot device."""
        for device in await self.devices():
            if device.get("name") == config.SPOTIFY_DEVICE_NAME:
                return await self.transfer_to(device["id"], "this Pi")

        if not self.librespot_logged_in:
            return False, "librespot not signed in - see README"
        return False, "Pi not visible to Spotify"

    def status_text(self) -> str:
        if not self.linked:
            return "Spotify not linked"
        if self.error:
            return self.error
        return self.now.label


# ── One-time account linking (CLI) ──────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorise() -> int:
    """Interactive PKCE flow. Run once over SSH, then never again."""
    import http.server

    if not config.SPOTIFY_CLIENT_ID:
        print("Set SPOTIFY_CLIENT_ID in src/bikecomputer/config.py first.")
        print("Create an app at https://developer.spotify.com/dashboard and add")
        print(f"  {config.SPOTIFY_REDIRECT_URI}")
        print("as a Redirect URI.")
        return 1

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "scope": config.SPOTIFY_SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    print("\nOpen this URL in any browser (phone is fine) and approve:\n")
    print(AUTH_URL + "?" + urllib.parse.urlencode(params))
    print("\nWaiting for the redirect...\n")

    parsed = urllib.parse.urlparse(config.SPOTIFY_REDIRECT_URI)
    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Spotify linked. You can close this tab.")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer((parsed.hostname, parsed.port or 80), Handler)
    server.handle_request()
    server.server_close()

    if received.get("state") != state:
        print("State mismatch - aborting.")
        return 1
    if "code" not in received:
        print(f"Authorisation failed: {received.get('error', 'no code returned')}")
        return 1

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": received["code"],
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "client_id": config.SPOTIFY_CLIENT_ID,
        "code_verifier": verifier,
    }).encode()

    request = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        payload = json.loads(resp.read().decode())

    _save_tokens({"refresh_token": payload["refresh_token"]})
    print(f"Linked. Refresh token saved to {config.SPOTIFY_TOKEN_FILE}")
    print("Set music source to Spotify in Settings > Music.")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        raise SystemExit(authorise())
    print("Usage: python -m bikecomputer.music.spotify auth")
    raise SystemExit(2)
