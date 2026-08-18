"""
screens/map_screen.py
Map screen: renders basemap tiles + position marker + optional route polyline.
Delegates tile fetching and vector rendering to mapview.MapView.
"""

from __future__ import annotations
import math
import time
from typing import Optional

from PIL import Image, ImageDraw

from .. import config
from ..ride import RideState
from ._base import new_frame, load_font, draw_status_bar

_STATUS_H = 20
_W = config.DISPLAY_WIDTH
_H = config.DISPLAY_HEIGHT


def _tile_coords(lat: float, lon: float, zoom: int) -> tuple[int, int, float, float]:
    """Return (tile_x, tile_y, pixel_x_frac, pixel_y_frac) for a lat/lon."""
    n = 2 ** zoom
    x_frac = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y_frac = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    tx = int(x_frac)
    ty = int(y_frac)
    return tx, ty, x_frac - tx, y_frac - ty


class MapScreen:
    def __init__(self, mapview) -> None:
        """
        mapview: mapview.MapView instance (or None if mbtiles absent).
        """
        self._mapview = mapview
        self._font_sm  = load_font(13)
        self._font_cue = load_font(16, bold=True)

    def render(self, state: RideState, cue_text: Optional[str] = None) -> Image.Image:
        if self._mapview is None:
            return self._no_map(state)

        if state.lat is None:
            return self._no_fix(state)

        # Ask mapview for a rendered tile image centred on current position
        tile_img = self._mapview.render_around(
            state.lat, state.lon,
            width=_W, height=_H - _STATUS_H,
            zoom=config.MAP_ZOOM,
        )

        img = Image.new("RGB", (_W, _H), config.CLR_MAP_BG)

        # Status bar
        _, draw_tmp = image_draw_pair(img)
        clock = time.strftime("%H:%M")
        draw_status_bar(
            draw_tmp,
            satellites=state.satellites,
            hdop=state.hdop,
            clock=clock,
            height=_STATUS_H,
        )

        if tile_img is not None:
            img.paste(tile_img, (0, _STATUS_H))

        draw = ImageDraw.Draw(img)

        # Position marker (centre of map)
        cx, cy = _W // 2, (_H - _STATUS_H) // 2 + _STATUS_H
        r = 6
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=config.CLR_POS_MARKER, outline=(0, 0, 0), width=2)

        # Speed overlay (bottom-left)
        spd = f"{state.speed_kmh:.0f} mph"
        draw.rectangle([0, _H - 26, 100, _H], fill=(0, 0, 0, 180))
        draw.text((4, _H - 22), spd, font=self._font_sm, fill=config.CLR_TEXT)

        # Scale bar
        self._draw_scale_bar(draw, state.lat, _W - 110, _H - 22)

        # Cue banner
        if cue_text:
            self._draw_cue(draw, cue_text)

        return img

    def _draw_scale_bar(self, draw: ImageDraw.ImageDraw, lat: float, x: int, y: int) -> None:
        """Draw a 100m scale bar."""
        zoom = config.MAP_ZOOM
        tile_size = 256  # MVT tiles are 4096 units but we render at 256px
        metres_per_px = (156543.03392 * math.cos(math.radians(lat))) / (2 ** zoom) * (256 / tile_size)
        bar_px = int(100 / metres_per_px)
        bar_px = min(bar_px, 90)

        draw.rectangle([x, y, x + bar_px, y + 4], fill=config.CLR_TEXT)
        draw.text((x, y + 6), "100m", font=self._font_sm, fill=config.CLR_TEXT)

    def _draw_cue(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        bbox = draw.textbbox((0, 0), text, font=self._font_cue)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pad = 8
        bx0 = _W // 2 - tw // 2 - pad
        bx1 = _W // 2 + tw // 2 + pad
        by0 = _STATUS_H + 4
        by1 = by0 + th + pad * 2
        draw.rectangle([bx0, by0, bx1, by1], fill=config.CLR_ACCENT)
        draw.text((_W // 2 - tw // 2, by0 + pad), text,
                  font=self._font_cue, fill=(0, 0, 0))

    def _no_map(self, state: RideState) -> Image.Image:
        img, draw = new_frame()
        clock = time.strftime("%H:%M")
        draw_status_bar(draw, satellites=state.satellites, hdop=state.hdop,
                        clock=clock, height=_STATUS_H)
        msg = "Map unavailable\n(mbtiles not found)"
        draw.text((20, _H // 2 - 20), msg, font=self._font_sm, fill=config.CLR_DIM)
        return img

    def _no_fix(self, state: RideState) -> Image.Image:
        img, draw = new_frame()
        clock = time.strftime("%H:%M")
        draw_status_bar(draw, satellites=state.satellites, hdop=state.hdop,
                        clock=clock, height=_STATUS_H)
        draw.text((20, _H // 2 - 10), "Waiting for GPS fix…",
                  font=self._font_sm, fill=config.CLR_DIM)
        return img


def image_draw_pair(img: Image.Image) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    return img, ImageDraw.Draw(img)
