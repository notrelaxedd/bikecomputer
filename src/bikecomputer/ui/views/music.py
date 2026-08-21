"""
ui/views/music.py — Music menu and library browser.

Transport lives in a list rather than on dedicated buttons because there
are only three buttons and the data screens already claim them.  The one
control that survives outside this screen is play/pause, on a short
SELECT press from any data screen.
"""

from __future__ import annotations
from typing import Optional

from PIL import Image

from ... import config
from ...music import SOURCE_LOCAL, SOURCE_SPOTIFY, spotify_linked
from ..nav import Action, AppContext, HANDLED, Push
from .. import theme
from .menu import AdjustView, MenuItem, MenuView


class MusicView(MenuView):
    title = "Music"
    hint_text = "SELECT: activate   hold: back"
    refresh_interval = 3.0

    def subtitle(self, ctx: AppContext) -> str:
        return ctx.music.source_label() if ctx.music else ""

    async def tick(self, ctx: AppContext) -> None:
        # Neither backend pushes state at us, so the header would otherwise
        # keep showing the track that finished a minute ago.
        if ctx.music:
            await ctx.music.refresh()

    def build(self, ctx: AppContext) -> list[MenuItem]:
        music = ctx.music
        if music is None:
            return [MenuItem("Music unavailable")]

        items = [
            MenuItem("Play / Pause",
                     value=lambda c: "Playing" if c.music.playing else "Paused",
                     action=_toggle),
            MenuItem("Next track", action=_next),
            MenuItem("Previous track", action=_previous),
            MenuItem("Volume",
                     value=lambda c: f"{c.settings.volume}%",
                     action=_volume),
        ]

        if music.source == SOURCE_LOCAL:
            items += [
                MenuItem("Shuffle",
                         value=lambda c: "On" if c.settings.shuffle else "Off",
                         action=_shuffle),
                MenuItem("Library",
                         value=lambda c: f"{len(c.music.local.tracks)} tracks",
                         action=_library),
                MenuItem("Rescan library", action=_rescan),
            ]
        else:
            items.append(MenuItem("Play on...",
                                  value=lambda c: c.music.spotify.now.device,
                                  action=_devices))

        items.append(
            MenuItem("Source",
                     value=lambda c: c.music.source_label(),
                     action=_switch_source)
        )
        return items

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        top = theme.header(draw, self.title, self.subtitle(ctx))

        top = self._now_playing(draw, ctx, top)

        items = self._refresh_items(ctx)
        rows = [(item.label, item.value_text(ctx), item.colour) for item in items]
        theme.draw_list(draw, top, rows, self.selected)
        theme.hint(draw, self.hint_text)
        return img

    def _now_playing(self, draw, ctx: AppContext, top: int) -> int:
        height = 56
        draw.rectangle([0, top, theme.W, top + height], fill=(16, 16, 16))

        music = ctx.music
        label = music.now_playing() if music else "No player"
        playing = bool(music and music.playing)

        draw.text((theme.PAD, top + 10), "NOW PLAYING", font=theme.font(11),
                  fill=config.CLR_DIM)
        fnt = theme.font(16, bold=True)
        draw.text((theme.PAD, top + 26),
                  theme.ellipsise(draw, label, fnt, theme.W - 2 * theme.PAD - 20),
                  font=fnt,
                  fill=config.CLR_TEXT if playing else config.CLR_DIM)

        draw.line([0, top + height, theme.W, top + height], fill=config.CLR_FAINT)
        return top + height + 1


# ── Row actions ─────────────────────────────────────────────────────────────

async def _toggle(ctx: AppContext) -> Optional[Action]:
    await ctx.music.toggle()
    return HANDLED


async def _next(ctx: AppContext) -> Optional[Action]:
    await ctx.music.next()
    ctx.toast(ctx.music.now_playing())
    return HANDLED


async def _previous(ctx: AppContext) -> Optional[Action]:
    await ctx.music.previous()
    ctx.toast(ctx.music.now_playing())
    return HANDLED


async def _volume(ctx: AppContext) -> Optional[Action]:
    async def apply(c: AppContext, value: int) -> None:
        await c.music.set_volume(value)

    return Push(AdjustView("Volume",
                           get=lambda c: c.settings.volume,
                           apply=apply, step=5, unit="%"))


async def _shuffle(ctx: AppContext) -> Optional[Action]:
    on = not ctx.settings.shuffle
    await ctx.music.set_shuffle(on)
    ctx.toast(f"Shuffle {'on' if on else 'off'}")
    return HANDLED


async def _library(ctx: AppContext) -> Optional[Action]:
    return Push(LibraryView())


async def _rescan(ctx: AppContext) -> Optional[Action]:
    count = ctx.music.local.refresh_library()
    ctx.toast(f"{count} tracks found")
    return HANDLED


async def _devices(ctx: AppContext) -> Optional[Action]:
    return Push(SpotifyDevicesView())


async def _switch_source(ctx: AppContext) -> Optional[Action]:
    current = ctx.music.source
    target = SOURCE_SPOTIFY if current == SOURCE_LOCAL else SOURCE_LOCAL

    if target == SOURCE_SPOTIFY and not spotify_linked():
        ctx.toast("Run: python -m bikecomputer.music.spotify auth")
        return HANDLED

    ok, message = await ctx.music.set_source(target)
    ctx.toast(message or ("Source changed" if ok else "Could not switch"))
    return HANDLED


# ── Library browser ─────────────────────────────────────────────────────────

class LibraryView(MenuView):
    title = "Library"
    hint_text = "SELECT: play   hold: back"
    empty_text = f"Copy files to {config.MUSIC_DIR}"

    def subtitle(self, ctx: AppContext) -> str:
        tracks = ctx.music.local.tracks if ctx.music else []
        return f"{len(tracks)} tracks"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        tracks = ctx.music.local.tracks if ctx.music else []

        current = ctx.music.local.index
        items = []
        for index, track in enumerate(tracks):
            items.append(MenuItem(
                track.label,
                value="▶" if index == current and ctx.music.local.playing else "",
                action=_play_index(index),
                colour=config.CLR_ACCENT,
            ))
        return items


def _play_index(index: int):
    async def action(ctx: AppContext) -> Optional[Action]:
        ctx.settings.set("music_source", SOURCE_LOCAL)
        await ctx.music.local.play_index(index)
        ctx.toast(ctx.music.local.status_text())
        return HANDLED
    return action


# ── Spotify Connect device picker ───────────────────────────────────────────

class SpotifyDevicesView(MenuView):
    """
    Choose which Spotify Connect device plays.

    Worth understanding before picking: sending playback to the *phone*
    means the phone streams to your headphones and the Pi is only a
    remote. Sending it to the Pi means the Pi streams over its own
    connection and the headphones must be paired here instead. The phone
    is usually the better bet on a bike -- it has the better antenna and
    the bigger battery.
    """

    title = "Play on"
    hint_text = "SELECT: move playback   hold: back"
    empty_text = "No Spotify devices found"
    refresh_interval = 3.0

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[dict] = []

    async def on_show(self, ctx: AppContext) -> None:
        await self.tick(ctx)

    async def tick(self, ctx: AppContext) -> None:
        if ctx.music and ctx.music.source == SOURCE_SPOTIFY:
            self._devices = await ctx.music.spotify.devices()

    def subtitle(self, ctx: AppContext) -> str:
        return f"{len(self._devices)} devices" if self._devices else ""

    def build(self, ctx: AppContext) -> list[MenuItem]:
        items = []
        for device in self._devices:
            name = device.get("name", "?")
            label = name
            if name == config.SPOTIFY_DEVICE_NAME:
                label = f"{name} (this Pi)"
            items.append(MenuItem(
                label,
                value="playing" if device.get("is_active") else
                      device.get("type", "").lower(),
                action=_transfer_to(device.get("id", ""), name),
            ))
        return items


def _transfer_to(device_id: str, name: str):
    async def action(ctx: AppContext) -> Optional[Action]:
        if not device_id:
            return HANDLED
        ok, message = await ctx.music.spotify.transfer_to(device_id, name)
        ctx.toast(message)
        return HANDLED
    return action
