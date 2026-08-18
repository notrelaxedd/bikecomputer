"""
upload.py — Strava OAuth2 + activity upload.

Client secret is read from the STRAVA_CLIENT_SECRET environment variable.
Tokens are persisted to STRAVA_TOKEN_FILE (outside the repo).

OAuth flow (first-time setup):
    python -m bikecomputer.upload auth
This starts a local HTTP server on port 8765, prints an auth URL, and
exchanges the code for tokens automatically.

Subsequent uploads happen automatically on startup if unuploaded rides exist.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

from . import config

log = logging.getLogger(__name__)

_UPLOADED_SUFFIX = ".uploaded"


def _client_secret() -> str:
    secret = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if not secret:
        raise RuntimeError("STRAVA_CLIENT_SECRET environment variable not set")
    return secret


def _load_tokens() -> Optional[dict]:
    if not config.STRAVA_TOKEN_FILE.exists():
        return None
    try:
        return json.loads(config.STRAVA_TOKEN_FILE.read_text())
    except Exception:
        return None


def _save_tokens(tokens: dict) -> None:
    config.STRAVA_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STRAVA_TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


async def _refresh_tokens(session: aiohttp.ClientSession, tokens: dict) -> dict:
    async with session.post(config.STRAVA_TOKEN_URL, data={
        "client_id":     config.STRAVA_CLIENT_ID,
        "client_secret": _client_secret(),
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }) as resp:
        resp.raise_for_status()
        new_tokens = await resp.json()
    _save_tokens(new_tokens)
    return new_tokens


async def _ensure_valid_tokens(session: aiohttp.ClientSession) -> Optional[dict]:
    tokens = _load_tokens()
    if tokens is None:
        log.info("No Strava tokens found; skipping upload. Run: python -m bikecomputer.upload auth")
        return None
    if tokens.get("expires_at", 0) < time.time() + 60:
        log.info("Refreshing Strava access token")
        try:
            tokens = await _refresh_tokens(session, tokens)
        except Exception as exc:
            log.warning("Token refresh failed: %s", exc)
            return None
    return tokens


def _pending_rides() -> list[Path]:
    if not config.RIDES_DIR.exists():
        return []
    return sorted(
        p for p in config.RIDES_DIR.glob("*.gpx")
        if not (p.parent / (p.name + _UPLOADED_SUFFIX)).exists()
    )


async def upload_pending() -> None:
    """Upload any unuploaded GPX rides to Strava."""
    rides = _pending_rides()
    if not rides:
        return

    try:
        secret = _client_secret()
    except RuntimeError as exc:
        log.info("%s — skipping upload", exc)
        return

    async with aiohttp.ClientSession() as session:
        tokens = await _ensure_valid_tokens(session)
        if tokens is None:
            return

        for gpx_path in rides:
            log.info("Uploading %s to Strava", gpx_path.name)
            try:
                upload_id = await _upload_file(session, tokens, gpx_path)
                await _wait_for_processing(session, tokens, upload_id)
                # Mark as uploaded
                marker = gpx_path.parent / (gpx_path.name + _UPLOADED_SUFFIX)
                marker.touch()
                log.info("Uploaded: %s", gpx_path.name)
            except Exception as exc:
                log.warning("Upload failed for %s: %s", gpx_path.name, exc)


async def _upload_file(
    session: aiohttp.ClientSession,
    tokens: dict,
    path: Path,
) -> int:
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    data = aiohttp.FormData()
    data.add_field("data_type", "gpx")
    data.add_field("name", path.stem)
    data.add_field(
        "file",
        path.read_bytes(),
        filename=path.name,
        content_type="application/octet-stream",
    )
    async with session.post(config.STRAVA_UPLOAD_URL, headers=headers, data=data) as resp:
        resp.raise_for_status()
        result = await resp.json()
    return result["id"]


async def _wait_for_processing(
    session: aiohttp.ClientSession,
    tokens: dict,
    upload_id: int,
    timeout: float = 60.0,
) -> None:
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with session.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            status = await resp.json()
        if status.get("status") == "Your activity is ready.":
            return
        if status.get("error"):
            raise RuntimeError(f"Strava error: {status['error']}")
        await asyncio.sleep(3)
    raise TimeoutError("Strava processing timed out")


# ── First-time OAuth flow ────────────────────────────────────────────────────

async def run_oauth_flow() -> None:
    """
    Interactive OAuth: print an auth URL, listen for the redirect,
    exchange the code for tokens, and save them.
    """
    params = {
        "client_id":     config.STRAVA_CLIENT_ID,
        "redirect_uri":  config.STRAVA_REDIRECT_URI,
        "response_type": "code",
        "scope":         "activity:write,read",
    }
    url = config.STRAVA_AUTH_URL + "?" + urlencode(params)
    print("\nOpen this URL in a browser:\n")
    print(url)
    print("\nWaiting for redirect on http://localhost:8765/callback …\n")

    code: Optional[str] = None

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal code
        request = (await reader.read(1024)).decode(errors="replace")
        line = request.splitlines()[0] if request else ""
        # GET /callback?code=XXX HTTP/1.1
        path = line.split(" ")[1] if " " in line else ""
        qs = parse_qs(urlparse(path).query)
        code = qs.get("code", [None])[0]

        body = b"<h1>Authorised! You can close this tab.</h1>"
        writer.write(
            b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", 8765)
    async with server:
        await server.start_serving()
        while code is None:
            await asyncio.sleep(0.2)

    async with aiohttp.ClientSession() as session:
        async with session.post(config.STRAVA_TOKEN_URL, data={
            "client_id":     config.STRAVA_CLIENT_ID,
            "client_secret": _client_secret(),
            "code":          code,
            "grant_type":    "authorization_code",
        }) as resp:
            resp.raise_for_status()
            tokens = await resp.json()

    _save_tokens(tokens)
    print(f"Tokens saved to {config.STRAVA_TOKEN_FILE}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        asyncio.run(run_oauth_flow())
    else:
        asyncio.run(upload_pending())
