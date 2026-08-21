"""
ui/theme.py — Drawing primitives shared by every view.

Sized for the 320x480 portrait panel.  Nothing here knows about ride
data or menus; it draws bars, lists, headers and toasts, and the views
supply the content.
"""

from __future__ import annotations
import time
from functools import lru_cache
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .. import config

W = config.DISPLAY_WIDTH
H = config.DISPLAY_HEIGHT

STATUS_H = 24          # top bar
HEADER_H = 34          # title bar under the status bar on menu views
ROW_H = 38             # menu row
PAD = 10


# Tried in order.  The configured DejaVu path is first (that is what the Pi
# has); the rest keep the offline preview tool honest on other machines and
# save the UI from collapsing to Pillow's 11px bitmap font if fonts-dejavu
# was never installed.
_FALLBACKS = {
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
}


@lru_cache(maxsize=48)
def font(size: int, bold: bool = False):
    configured = config.FONT_PATH_BOLD if bold else config.FONT_PATH
    for path in (configured, *_FALLBACKS[bold]):
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def new_frame(colour=config.CLR_BG) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), colour)
    return img, ImageDraw.Draw(img)


# ── Text helpers ────────────────────────────────────────────────────────────

def text_width(draw: ImageDraw.ImageDraw, text: str, fnt) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0]


def centred(draw: ImageDraw.ImageDraw, y: int, text: str, fnt, fill) -> None:
    draw.text((W // 2 - text_width(draw, text, fnt) // 2, y), text,
              font=fnt, fill=fill)


def right(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fnt, fill) -> None:
    draw.text((x - text_width(draw, text, fnt), y), text, font=fnt, fill=fill)


def ellipsise(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> str:
    """Trim to fit, with a trailing ellipsis. Menus have no room to wrap."""
    if text_width(draw, text, fnt) <= max_w:
        return text
    while text and text_width(draw, text + "...", fnt) > max_w:
        text = text[:-1]
    return text + "..."


# ── Status bar ──────────────────────────────────────────────────────────────

def status_bar(draw: ImageDraw.ImageDraw, ctx) -> None:
    """
    Always-visible top strip: GPS quality, live sensor/link indicators,
    and the clock.  Drawn on every view so the rider never loses track of
    whether the strap or headphones dropped out.
    """
    draw.rectangle([0, 0, W, STATUS_H], fill=(18, 18, 18))
    small = font(13)

    state = ctx.state
    sat_colour = config.CLR_OK if state.has_fix else config.CLR_WARN
    draw.text((PAD, 5), f"GPS {state.satellites}", font=small, fill=sat_colour)

    # Indicator chips, laid out left to right after the GPS label.
    x = PAD + 56
    if state.heart_rate is not None:
        draw.text((x, 5), "HR", font=small, fill=config.CLR_HR)
        x += 26
    if ctx.audio_connected:
        draw.text((x, 5), "BT", font=small, fill=config.CLR_OK)
        x += 26
    if ctx.music and ctx.music.playing:
        draw.text((x, 5), "♪", font=font(15), fill=config.CLR_ACCENT)
        x += 18

    right(draw, W - PAD, 5, time.strftime("%H:%M"), small, config.CLR_DIM)
    draw.line([0, STATUS_H, W, STATUS_H], fill=config.CLR_FAINT)


def header(draw: ImageDraw.ImageDraw, title: str, subtitle: str = "") -> int:
    """Title bar for menu views. Returns the y of the first content row."""
    y = STATUS_H + 1
    draw.rectangle([0, y, W, y + HEADER_H], fill=config.CLR_PANEL)
    draw.text((PAD, y + 8), title, font=font(18, bold=True), fill=config.CLR_TEXT)
    if subtitle:
        right(draw, W - PAD, y + 12, subtitle, font(13), config.CLR_DIM)
    draw.line([0, y + HEADER_H, W, y + HEADER_H], fill=config.CLR_FAINT)
    return y + HEADER_H + 1


# ── List / menu ─────────────────────────────────────────────────────────────

def visible_rows(top: int) -> int:
    return max(1, (H - top - 26) // ROW_H)


def scroll_window(selected: int, count: int, rows: int) -> int:
    """First index to draw so that `selected` stays on screen."""
    if count <= rows:
        return 0
    first = selected - rows // 2
    return max(0, min(first, count - rows))


def draw_list(
    draw: ImageDraw.ImageDraw,
    top: int,
    rows: Sequence[tuple[str, str, tuple]],
    selected: int,
    empty_text: str = "Nothing here",
) -> None:
    """
    rows: (label, value, value_colour) triples.
    The selected row gets a filled highlight bar; values are right-aligned.
    """
    if not rows:
        centred(draw, top + 40, empty_text, font(15), config.CLR_DIM)
        return

    count = len(rows)
    per_page = visible_rows(top)
    first = scroll_window(selected, count, per_page)
    label_font = font(16)
    value_font = font(14)

    for slot, index in enumerate(range(first, min(first + per_page, count))):
        label, value, value_colour = rows[index]
        y = top + slot * ROW_H
        is_selected = index == selected

        if is_selected:
            draw.rectangle([0, y, W, y + ROW_H - 2], fill=config.CLR_SELECT)
            label_colour = config.CLR_SELECT_TEXT
            value_colour = config.CLR_SELECT_TEXT
        else:
            label_colour = config.CLR_TEXT
            draw.line([PAD, y + ROW_H - 2, W - PAD, y + ROW_H - 2],
                      fill=(34, 34, 34))

        value_w = text_width(draw, value, value_font) if value else 0
        max_label = W - 2 * PAD - value_w - (12 if value else 0)
        draw.text((PAD, y + 10), ellipsise(draw, label, label_font, max_label),
                  font=label_font, fill=label_colour)
        if value:
            right(draw, W - PAD, y + 12, value, value_font, value_colour)

    if count > per_page:
        _scrollbar(draw, top, per_page, count, first)


def _scrollbar(draw, top: int, per_page: int, count: int, first: int) -> None:
    track_top = top
    track_h = per_page * ROW_H
    bar_h = max(20, int(track_h * per_page / count))
    bar_y = track_top + int(track_h * first / count)
    draw.rectangle([W - 4, track_top, W - 2, track_top + track_h],
                   fill=(30, 30, 30))
    draw.rectangle([W - 4, bar_y, W - 2, bar_y + bar_h], fill=config.CLR_DIM)


# ── Footer hint ─────────────────────────────────────────────────────────────

def hint(draw: ImageDraw.ImageDraw, text: str) -> None:
    """One-line reminder of what the buttons do, pinned to the bottom."""
    draw.rectangle([0, H - 22, W, H], fill=(14, 14, 14))
    centred(draw, H - 18, text, font(12), config.CLR_DIM)


# ── Toast ───────────────────────────────────────────────────────────────────

def toast(draw: ImageDraw.ImageDraw, text: str) -> None:
    """Transient status message; drawn over whatever the view produced."""
    fnt = font(15)
    tw = text_width(draw, text, fnt)
    box_w = min(W - 2 * PAD, tw + 28)
    x0 = W // 2 - box_w // 2
    y0 = H - 132
    draw.rectangle([x0, y0, x0 + box_w, y0 + 34], fill=(45, 45, 45),
                   outline=config.CLR_ACCENT)
    centred(draw, y0 + 9, ellipsise(draw, text, fnt, box_w - 20), fnt,
            config.CLR_TEXT)


# ── Misc widgets ────────────────────────────────────────────────────────────

def slider(draw: ImageDraw.ImageDraw, y: int, value: int,
           lo: int = 0, hi: int = 100) -> None:
    x0, x1 = PAD + 10, W - PAD - 10
    draw.rectangle([x0, y, x1, y + 8], fill=(40, 40, 40))
    frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
    draw.rectangle([x0, y, x0 + int((x1 - x0) * frac), y + 8],
                   fill=config.CLR_ACCENT)


def stat_cell(draw: ImageDraw.ImageDraw, x: int, y: int, w: int,
              label: str, value: str, unit: str = "",
              colour=config.CLR_TEXT) -> None:
    """Label above a large value, used by the ride and detail screens."""
    draw.text((x, y), label, font=font(12), fill=config.CLR_DIM)
    value_font = font(30, bold=True)
    draw.text((x, y + 15), value, font=value_font, fill=colour)
    if unit:
        vw = text_width(draw, value, value_font)
        draw.text((x + vw + 5, y + 30), unit, font=font(12), fill=config.CLR_DIM)
