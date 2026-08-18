"""
screens/ride_screen.py
Ride data screen: speed (large), distance, moving time, avg speed.
No BLE sensors — HR and cadence fields show dashes.
"""

from __future__ import annotations
import time

from PIL import Image, ImageDraw

from .. import config
from ..ride import RideState
from ._base import new_frame, load_font, draw_status_bar

_STATUS_H = 20
_W = config.DISPLAY_WIDTH
_H = config.DISPLAY_HEIGHT


class RideScreen:
    def __init__(self) -> None:
        self._font_huge  = load_font(96, bold=True)
        self._font_large = load_font(36, bold=True)
        self._font_med   = load_font(24)
        self._font_sm    = load_font(14)

    def render(self, state: RideState) -> Image.Image:
        img, draw = new_frame()

        # ── Status bar ────────────────────────────────────────────────────
        clock = time.strftime("%H:%M")
        draw_status_bar(
            draw,
            satellites=state.satellites,
            hdop=state.hdop,
            clock=clock,
            height=_STATUS_H,
        )

        y_top = _STATUS_H + 4

        if not state.has_fix:
            self._draw_searching(draw, y_top)
            return img

        # ── Speed (dominant) ──────────────────────────────────────────────
        spd_txt = f"{state.speed_kmh:.1f}"
        bbox = draw.textbbox((0, 0), spd_txt, font=self._font_huge)
        sw = bbox[2] - bbox[0]
        draw.text((_W // 2 - sw // 2, y_top), spd_txt,
                  font=self._font_huge, fill=config.CLR_TEXT)

        unit_txt = "km/h"
        bbox2 = draw.textbbox((0, 0), unit_txt, font=self._font_sm)
        uw = bbox2[2] - bbox2[0]
        draw.text((_W // 2 - uw // 2, y_top + 100), unit_txt,
                  font=self._font_sm, fill=config.CLR_DIM)

        y_mid = y_top + 124
        draw.line([0, y_mid, _W, y_mid], fill=(50, 50, 50), width=1)
        y_mid += 6

        # ── Two-column stat grid ─────────────────────────────────────────
        col_w = _W // 2
        stats = [
            ("DIST",    f"{state.distance_km:.2f} km"),
            ("TIME",    RideState.format_duration(state.moving_time)),
            ("AVG",     f"{state.avg_speed_kmh:.1f} km/h"),
            ("MAX",     f"{state.max_speed_kmh:.1f} km/h"),
            ("HR",      "—"),
            ("CAD",     "—"),
        ]

        for i, (label, value) in enumerate(stats):
            col = i % 2
            row = i // 2
            x = col * col_w + 10
            y = y_mid + row * 56

            draw.text((x, y),      label, font=self._font_sm, fill=config.CLR_DIM)
            draw.text((x, y + 16), value, font=self._font_large, fill=config.CLR_TEXT)

        # Paused indicator
        if state.paused and state.has_fix:
            draw.text((_W - 70, _H - 24), "PAUSED",
                      font=self._font_sm, fill=config.CLR_ACCENT)

        return img

    def _draw_searching(self, draw: ImageDraw.ImageDraw, y: int) -> None:
        txt = "Searching for GPS..."
        bbox = draw.textbbox((0, 0), txt, font=self._font_med)
        tw = bbox[2] - bbox[0]
        draw.text((_W // 2 - tw // 2, _H // 2 - 20), txt,
                  font=self._font_med, fill=config.CLR_DIM)
