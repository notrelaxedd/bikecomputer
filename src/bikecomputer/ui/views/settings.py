"""
ui/views/settings.py — The root menu reached by holding SELECT.

Rows that need more than a toggle push a sub-view; everything else flips
a value in Settings and persists immediately, so a power cut mid-ride
never loses a preference the rider just set.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional

from ... import config
from ...music import spotify_linked
from ..nav import Action, AppContext, HANDLED, Home, Push
from .bluetooth import BluetoothView
from .menu import ConfirmView, MenuItem, MenuView
from .music import MusicView

log = logging.getLogger(__name__)


class RootMenu(MenuView):
    title = "Menu"
    hint_text = "SELECT: open   hold: back to ride"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        return [
            MenuItem("Music",
                     value=lambda c: c.music.now_playing() if c.music else "",
                     action=_push(MusicView)),
            MenuItem("Bluetooth",
                     value=_bluetooth_summary,
                     action=_push(BluetoothView)),
            MenuItem("Ride", action=_push(RideSettingsView)),
            MenuItem("Display & units", action=_push(DisplaySettingsView)),
            MenuItem("System", action=_push(SystemView)),
        ]


class RideSettingsView(MenuView):
    title = "Ride"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        return [
            MenuItem("Auto-pause",
                     value=lambda c: _on_off(c.settings.autopause),
                     action=_toggle("autopause", "Auto-pause")),
            MenuItem("GPS quality filter",
                     value=lambda c: _on_off(c.settings.gps_filter),
                     action=_toggle_gps_filter),
            MenuItem("Reset trip", action=_reset_trip, colour=config.CLR_WARN),
            MenuItem("Upload rides to Strava", action=_upload_now),
        ]


class DisplaySettingsView(MenuView):
    title = "Display & units"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        return [
            MenuItem("Units",
                     value=lambda c: "Metric" if c.settings.metric else "Imperial",
                     action=_toggle_units),
            MenuItem("Start screen",
                     value=lambda c: c.settings.home_screen.title(),
                     action=_cycle_home_screen),
        ]


class SystemView(MenuView):
    title = "System"
    refresh_interval = 2.0

    def build(self, ctx: AppContext) -> list[MenuItem]:
        state = ctx.state
        return [
            MenuItem("Satellites", value=str(state.satellites)),
            MenuItem("HDOP", value=f"{state.hdop:.1f}"),
            MenuItem("Heart rate",
                     value=(f"{state.heart_rate} bpm"
                            if state.heart_rate is not None else "no sensor")),
            MenuItem("Spotify",
                     value="linked" if spotify_linked() else "not linked"),
            MenuItem("Music folder", value=_music_count),
            MenuItem("Ride file", value=_ride_file),
            MenuItem("Shut down", action=_shutdown, colour=config.CLR_ERR),
            MenuItem("Reboot", action=_reboot, colour=config.CLR_ERR),
        ]


# ── Row helpers ─────────────────────────────────────────────────────────────

def _on_off(value: bool) -> str:
    return "On" if value else "Off"


def _push(view_cls):
    async def action(ctx: AppContext) -> Optional[Action]:
        return Push(view_cls())
    return action


def _toggle(field: str, label: str):
    async def action(ctx: AppContext) -> Optional[Action]:
        value = ctx.settings.toggle(field)
        ctx.toast(f"{label} {_on_off(value).lower()}")
        return HANDLED
    return action


def _bluetooth_summary(ctx: AppContext) -> str:
    if ctx.bt is None or not ctx.bt.available:
        return "off"
    parts = []
    if ctx.settings.hr_device:
        parts.append("HR")
    if ctx.audio_connected:
        parts.append("audio")
    return ", ".join(parts) if parts else "none paired"


async def _toggle_units(ctx: AppContext) -> Optional[Action]:
    metric = not ctx.settings.metric
    ctx.settings.set("units", "metric" if metric else "imperial")
    ctx.toast("Metric" if metric else "Imperial")
    return HANDLED


async def _cycle_home_screen(ctx: AppContext) -> Optional[Action]:
    order = ["ride", "map", "detail"]
    index = (order.index(ctx.settings.home_screen) + 1) % len(order) \
        if ctx.settings.home_screen in order else 0
    ctx.settings.set("home_screen", order[index])
    ctx.toast(f"Start on {order[index]}")
    return HANDLED


async def _toggle_gps_filter(ctx: AppContext) -> Optional[Action]:
    value = ctx.settings.toggle("gps_filter")
    apply_gps_filter(ctx.settings)
    ctx.toast(f"GPS filter {_on_off(value).lower()}")
    return HANDLED


def apply_gps_filter(settings) -> None:
    """
    Push the rider's choice into the thresholds ride.py checks.

    Kept here rather than read inside the hot path so the fix-validation
    code stays a pure function of config, which is what the tests rely on.
    """
    if settings.gps_filter:
        config.MIN_SATELLITES = 4
        config.MAX_HDOP = 5.0
    else:
        config.MIN_SATELLITES = 0
        config.MAX_HDOP = 999.0


async def _reset_trip(ctx: AppContext) -> Optional[Action]:
    async def confirmed(c: AppContext) -> Optional[Action]:
        c.state.reset()
        c.toast("Trip reset")
        return Home()

    return Push(ConfirmView("Reset trip",
                            "Clear distance, time and averages?", confirmed))


async def _upload_now(ctx: AppContext) -> Optional[Action]:
    ctx.toast("Uploading...", seconds=30)
    try:
        from ...upload import upload_pending
        await asyncio.wait_for(upload_pending(), timeout=120.0)
        ctx.toast("Upload finished")
    except asyncio.TimeoutError:
        ctx.toast("Upload timed out")
    except Exception as exc:
        log.warning("Manual upload failed: %s", exc)
        ctx.toast(f"Upload failed: {str(exc)[:30]}")
    return HANDLED


def _music_count(ctx: AppContext) -> str:
    if ctx.music is None:
        return "-"
    return f"{len(ctx.music.local.tracks)} files"


def _ride_file(ctx: AppContext) -> str:
    path = getattr(ctx.logger, "current_path", None)
    return path.name if path else "not started"


async def _shutdown(ctx: AppContext) -> Optional[Action]:
    async def confirmed(c: AppContext) -> Optional[Action]:
        c.toast("Shutting down", seconds=30)
        await _run_system("poweroff", c)
        return Home()

    return Push(ConfirmView("Shut down", "Power off the bike computer?",
                            confirmed))


async def _reboot(ctx: AppContext) -> Optional[Action]:
    async def confirmed(c: AppContext) -> Optional[Action]:
        c.toast("Rebooting", seconds=30)
        await _run_system("reboot", c)
        return Home()

    return Push(ConfirmView("Reboot", "Restart the bike computer?", confirmed))


async def _run_system(command: str, ctx: AppContext) -> None:
    """
    Stop the app cleanly first so the GPX is flushed, then hand off to
    systemd.  Killing power with an unflushed ride file loses the ride.
    """
    if ctx.request_shutdown is not None:
        ctx.request_shutdown(command)
        return
    try:
        proc = await asyncio.create_subprocess_exec("sudo", "systemctl", command)
        await proc.wait()
    except OSError as exc:
        ctx.toast(f"{command} failed: {exc}")
