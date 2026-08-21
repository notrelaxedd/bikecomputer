"""
ui/views/ride.py — The main riding screen, portrait.

Speed takes the top third because it is the only number read at a glance
while moving; everything else is a two-column grid below it.  The bottom
strip shows what is playing, so the rider can confirm a track change
without leaving the screen.
"""

from __future__ import annotations
from typing import Optional

from PIL import Image

from ... import config, units
from ..nav import AppContext, View
from .. import theme

W = theme.W
H = theme.H


class RideView(View):
    name = "ride"
    fps = config.DATA_FPS

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)

        state = ctx.state
        metric = ctx.metric

        if not state.has_fix:
            self._searching(draw, ctx)
            return img

        # ── Speed ────────────────────────────────────────────────────────
        speed_font = theme.font(92, bold=True)
        speed_text = units.fmt_speed(state.speed, metric, decimals=1)
        theme.centred(draw, 32, speed_text, speed_font, config.CLR_TEXT)
        theme.centred(draw, 132, units.speed_unit(metric), theme.font(16),
                      config.CLR_DIM)

        if state.paused:
            theme.centred(draw, 154, "PAUSED", theme.font(13, bold=True),
                          config.CLR_ACCENT)

        draw.line([theme.PAD, 176, W - theme.PAD, 176], fill=config.CLR_FAINT)

        # ── Stat grid ────────────────────────────────────────────────────
        cells = [
            ("DIST", units.fmt_distance(state.distance, metric),
             units.distance_unit(metric), config.CLR_TEXT),
            ("TIME", units.fmt_duration(state.moving_time), "", config.CLR_TEXT),
            ("AVG", units.fmt_speed(state.avg_speed, metric),
             units.speed_unit(metric), config.CLR_TEXT),
            ("MAX", units.fmt_speed(state.max_speed, metric),
             units.speed_unit(metric), config.CLR_TEXT),
            ("HR", _sensor(state.heart_rate), "bpm", config.CLR_HR),
            ("CAD", _sensor(state.cadence), "rpm", config.CLR_CADENCE),
        ]

        col_w = W // 2
        for index, (label, value, unit, colour) in enumerate(cells):
            x = (index % 2) * col_w + theme.PAD
            y = 188 + (index // 2) * 70
            faded = colour if value != "--" else config.CLR_DIM
            theme.stat_cell(draw, x, y, col_w, label, value, unit, faded)

        self._music_strip(draw, ctx)
        return img

    def _music_strip(self, draw, ctx: AppContext) -> None:
        y = H - 76
        draw.rectangle([0, y, W, y + 46], fill=config.CLR_PANEL)
        draw.line([0, y, W, y], fill=config.CLR_FAINT)

        if ctx.music is None:
            return
        playing = ctx.music.playing
        icon = "▶" if playing else "‖"
        draw.text((theme.PAD, y + 14), icon, font=theme.font(14),
                  fill=config.CLR_ACCENT if playing else config.CLR_DIM)

        label = ctx.music.now_playing()
        fnt = theme.font(14)
        draw.text((theme.PAD + 22, y + 14),
                  theme.ellipsise(draw, label, fnt, W - theme.PAD * 2 - 30),
                  font=fnt, fill=config.CLR_TEXT if playing else config.CLR_DIM)

        theme.hint(draw, "SELECT: play/pause   hold: menu")

    def _searching(self, draw, ctx: AppContext) -> None:
        theme.centred(draw, H // 2 - 60, "Searching for GPS",
                      theme.font(20, bold=True), config.CLR_TEXT)
        theme.centred(draw, H // 2 - 30,
                      f"{ctx.state.satellites} satellites  "
                      f"HDOP {ctx.state.hdop:.1f}",
                      theme.font(14), config.CLR_DIM)
        self._music_strip(draw, ctx)


def _sensor(value: Optional[int]) -> str:
    return str(value) if value is not None else "--"
