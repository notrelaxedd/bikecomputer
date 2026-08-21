"""
ui/views/mapview.py — Full-bleed map with a speed and cue overlay.

Portrait shows more of the road ahead than landscape did, so the position
marker sits below centre: the useful part of the viewport is what you are
riding into, not what you have passed.
"""

from __future__ import annotations
from typing import Optional

from PIL import Image, ImageDraw

from ... import config, units
from ..nav import AppContext, View
from .. import theme

W = theme.W
H = theme.H

MAP_TOP = theme.STATUS_H + 1
MAP_H = H - MAP_TOP - 22          # leave the hint strip visible
MARKER_Y = MAP_TOP + int(MAP_H * 0.62)


class MapView(View):
    name = "map"
    fps = config.MAP_FPS

    def __init__(self, mapview) -> None:
        self._mapview = mapview

    def render(self, ctx: AppContext) -> Image.Image:
        state = ctx.state

        if self._mapview is None or not getattr(self._mapview, "available", False):
            return self._message(ctx, "Map unavailable",
                                 f"No tiles at {config.MBTILES_PATH.name}")
        if state.lat is None:
            return self._message(ctx, "Waiting for GPS fix",
                                 f"{state.satellites} satellites")

        img = Image.new("RGB", (W, H), config.CLR_MAP_BG)
        try:
            tiles = self._mapview.render_around(
                state.lat, state.lon,
                width=W, height=MAP_H, zoom=config.MAP_ZOOM,
            )
            if tiles is not None:
                img.paste(tiles, (0, MAP_TOP))
        except Exception:
            pass

        draw = ImageDraw.Draw(img)
        self._marker(draw, state.track)
        self._overlay(draw, ctx)

        cue = self._cue(state)
        if cue:
            self._cue_banner(draw, cue)

        theme.status_bar(draw, ctx)
        theme.hint(draw, "UP/DOWN: screen   hold SELECT: menu")
        return img

    def _cue(self, state) -> Optional[str]:
        try:
            return self._mapview.nearest_cue(state.lat, state.lon)
        except Exception:
            return None

    def _marker(self, draw: ImageDraw.ImageDraw, track: float) -> None:
        cx, cy, r = W // 2, MARKER_Y, 8
        draw.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3],
                     fill=(0, 0, 0))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=config.CLR_POS_MARKER)

        # Short heading whisker, so the marker shows which way is forward.
        import math
        angle = math.radians(track - 90.0)
        draw.line([cx, cy,
                   cx + math.cos(angle) * 22, cy + math.sin(angle) * 22],
                  fill=config.CLR_POS_MARKER, width=3)

    def _overlay(self, draw: ImageDraw.ImageDraw, ctx: AppContext) -> None:
        state = ctx.state
        metric = ctx.metric
        y = H - 74

        draw.rectangle([0, y, W, y + 52], fill=(0, 0, 0))
        draw.line([0, y, W, y], fill=config.CLR_FAINT)

        speed_font = theme.font(34, bold=True)
        speed = units.fmt_speed(state.speed, metric, decimals=0)
        draw.text((theme.PAD, y + 8), speed, font=speed_font, fill=config.CLR_TEXT)
        sw = theme.text_width(draw, speed, speed_font)
        draw.text((theme.PAD + sw + 6, y + 26), units.speed_unit(metric),
                  font=theme.font(12), fill=config.CLR_DIM)

        dist = (f"{units.fmt_distance(state.distance, metric)} "
                f"{units.distance_unit(metric)}")
        theme.right(draw, W - theme.PAD, y + 8, dist,
                    theme.font(22, bold=True), config.CLR_TEXT)
        theme.right(draw, W - theme.PAD, y + 34,
                    units.fmt_duration(state.moving_time),
                    theme.font(13), config.CLR_DIM)

    def _cue_banner(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        fnt = theme.font(18, bold=True)
        y = MAP_TOP + 8
        draw.rectangle([theme.PAD, y, W - theme.PAD, y + 38],
                       fill=config.CLR_ACCENT)
        theme.centred(draw, y + 9, text, fnt, (0, 0, 0))

    def _message(self, ctx: AppContext, title: str, detail: str) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        theme.centred(draw, H // 2 - 40, title, theme.font(19, bold=True),
                      config.CLR_TEXT)
        theme.centred(draw, H // 2 - 10, detail, theme.font(14), config.CLR_DIM)
        theme.hint(draw, "UP/DOWN: screen   hold SELECT: menu")
        return img
