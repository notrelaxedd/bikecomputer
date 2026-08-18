"""
screens/detail_screen.py
Detail screen: altitude, max/avg speed, elapsed vs moving time, lat/lon.
"""

from __future__ import annotations
import time

from PIL import Image

from .. import config
from ..ride import RideState
from ._base import new_frame, load_font, draw_status_bar

_STATUS_H = 20
_W = config.DISPLAY_WIDTH
_H = config.DISPLAY_HEIGHT


class DetailScreen:
    def __init__(self) -> None:
        self._font_val  = load_font(32, bold=True)
        self._font_lbl  = load_font(14)
        self._font_coord = load_font(20)

    def render(self, state: RideState) -> Image.Image:
        img, draw = new_frame()

        clock = time.strftime("%H:%M:%S")
        draw_status_bar(
            draw,
            satellites=state.satellites,
            hdop=state.hdop,
            clock=clock,
            height=_STATUS_H,
        )

        rows = [
            ("ALTITUDE",       f"{state.altitude:.0f} m",
             "MAX ALT",        f"{state.max_altitude:.0f} m"),
            ("AVG SPEED",      f"{state.avg_speed_kmh:.1f} km/h",
             "MAX SPEED",      f"{state.max_speed_kmh:.1f} km/h"),
            ("MOVING TIME",    RideState.format_duration(state.moving_time),
             "ELAPSED",        RideState.format_duration(state.elapsed_time)),
        ]

        col_w = _W // 2
        y = _STATUS_H + 10

        for lbl_l, val_l, lbl_r, val_r in rows:
            # Left cell
            draw.text((10, y),      lbl_l, font=self._font_lbl, fill=config.CLR_DIM)
            draw.text((10, y + 16), val_l, font=self._font_val, fill=config.CLR_TEXT)
            # Right cell
            draw.text((col_w + 10, y),      lbl_r, font=self._font_lbl, fill=config.CLR_DIM)
            draw.text((col_w + 10, y + 16), val_r, font=self._font_val, fill=config.CLR_TEXT)

            y += 66
            draw.line([0, y - 4, _W, y - 4], fill=(40, 40, 40), width=1)

        # Coordinates
        if state.lat is not None:
            coord_txt = f"{state.lat:.6f}°N  {state.lon:.6f}°E"
        else:
            coord_txt = "No GPS fix"

        bbox = draw.textbbox((0, 0), coord_txt, font=self._font_coord)
        tw = bbox[2] - bbox[0]
        draw.text((_W // 2 - tw // 2, y + 4), coord_txt,
                  font=self._font_coord, fill=config.CLR_DIM)

        return img
