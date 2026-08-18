"""Shared helpers for all screen renderers."""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .. import config


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = config.FONT_PATH_BOLD if bold else config.FONT_PATH
    return _load_font(path, size)


def new_frame() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT), config.CLR_BG)
    draw = ImageDraw.Draw(img)
    return img, draw


def draw_status_bar(
    draw: ImageDraw.ImageDraw,
    *,
    satellites: int,
    hdop: float,
    clock: str,
    y: int = 0,
    height: int = 18,
) -> None:
    """Thin status bar at the top of every screen."""
    draw.rectangle([0, y, config.DISPLAY_WIDTH, y + height], fill=(20, 20, 20))

    font_sm = load_font(12)

    # Satellite count + HDOP
    sat_txt = f"SAT:{satellites}  HDOP:{hdop:.1f}"
    draw.text((4, y + 2), sat_txt, font=font_sm, fill=config.CLR_DIM)

    # Clock (right-aligned)
    bbox = draw.textbbox((0, 0), clock, font=font_sm)
    tw = bbox[2] - bbox[0]
    draw.text((config.DISPLAY_WIDTH - tw - 4, y + 2), clock, font=font_sm, fill=config.CLR_DIM)
