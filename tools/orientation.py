"""
tools/orientation.py — Find the right MADCTL value by looking at the panel.

Guessing MADCTL from arithmetic is unreliable: whether a given value comes
out rotated or mirrored depends on how the glass is bonded and how the
panel is physically mounted, neither of which is in the datasheet.  Four
values are possible in portrait, so just show all four and let your eyes
decide.

The test pattern is built around a letter F because it is asymmetric on
both axes — it is the one glyph where "upside down" and "mirrored" cannot
be confused for each other. A correct display shows the F reading
normally, the red corner block at TOP-LEFT, and the arrow pointing up.

Run on the Pi:

    sudo systemctl stop bikecomputer
    cd /opt/bikecomputer
    venv/bin/python tools/orientation.py

Press Enter to step through the candidates, then put the value it prints
into MADCTL in src/bikecomputer/config.py.
"""

from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw                       # noqa: E402

from bikecomputer import config                        # noqa: E402
from bikecomputer.ui import theme                      # noqa: E402

# Display is imported inside main() so the pattern itself can be rendered
# on a dev machine, where spidev and RPi.GPIO do not exist.

W = config.DISPLAY_WIDTH
H = config.DISPLAY_HEIGHT

# Portrait candidates only (the MV bit, 0x20, is what selects landscape).
# 0x00 and 0xC0 are true rotations; 0x40 and 0x80 include a mirror.
CANDIDATES = [
    (0x00, "0x00  no flip"),
    (0xC0, "0xC0  rotated 180"),
    (0x40, "0x40  mirrored horizontally"),
    (0x80, "0x80  mirrored vertically"),
]


def test_pattern(madctl: int, label: str) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Red block marks where the top-left corner should be.
    draw.rectangle([0, 0, 46, 46], fill=(220, 40, 40))
    draw.text((8, 12), "TL", font=theme.font(20, bold=True), fill=(255, 255, 255))

    # Green strip along the top edge, blue along the bottom.
    draw.rectangle([50, 8, W - 8, 30], fill=(40, 170, 80))
    draw.text((56, 10), "TOP", font=theme.font(15, bold=True), fill=(0, 0, 0))
    draw.rectangle([8, H - 30, W - 8, H - 8], fill=(50, 110, 220))
    draw.text((14, H - 28), "BOTTOM", font=theme.font(15, bold=True),
              fill=(0, 0, 0))

    # The F: asymmetric on both axes, so it distinguishes flips from
    # rotations at a glance.
    theme.centred(draw, 70, "F", theme.font(190, bold=True), (255, 255, 255))

    # Arrow pointing up, drawn explicitly rather than as a glyph so no
    # font substitution can turn it into a box.
    cx, top, bot = W // 2, 280, 350
    draw.line([cx, bot, cx, top], fill=config.CLR_ACCENT, width=6)
    draw.polygon([(cx, top - 14), (cx - 18, top + 10), (cx + 18, top + 10)],
                 fill=config.CLR_ACCENT)

    theme.centred(draw, 370, "this way up", theme.font(16), config.CLR_DIM)
    theme.centred(draw, 400, label, theme.font(19, bold=True),
                  config.CLR_ACCENT)
    theme.centred(draw, 426, "readable + TL top-left = correct",
                  theme.font(12), config.CLR_DIM)
    return img


def main() -> int:
    from bikecomputer.display import Display

    display = Display()
    display.init()
    print(f"\nPanel driven as {W}x{H} portrait.")
    print("Look for: F reads normally, red TL block top-left, arrow up.\n")

    try:
        for madctl, label in CANDIDATES:
            display.set_madctl(madctl)
            display.blit(test_pattern(madctl, label))
            print(f"  showing MADCTL = {label}")
            input("    Enter for next... ")

        print("\nSet the value that looked right in src/bikecomputer/config.py:")
        print("    MADCTL = 0x??\n")
        print("If none were correct, the panel is mounted rotated 90 degrees")
        print("relative to what this build assumes -- say so and the UI can")
        print("be laid out for landscape instead.\n")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        display.fill((0, 0, 0))
        display.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
