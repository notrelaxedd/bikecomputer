"""
tools/display_stress.py — Isolate a display failure from the application.

Written after several rounds of guessing at a white-screen fault. It
exercises the panel directly, so whatever it does is a property of the
display, the wiring and the power supply, with the UI, the menus, the
button handling and the render loop all out of the picture.

It runs in phases and asks what you see between them, because the panel
cannot be read back over SPI -- only your eyes can say whether a frame
landed.

    sudo systemctl stop bikecomputer
    cd /opt/bikecomputer
    venv/bin/python tools/display_stress.py
    venv/bin/python tools/display_stress.py --speed 8000000   # slower SPI

What the outcomes mean:

  Fails during phase 1        Raw full-frame SPI traffic is enough to
                              break it. Not an application bug at all.
  Survives 1, fails later     Related to duration or thermals rather
                              than to any particular operation.
  Never fails here            The display is fine under load and the
                              trigger is something the app does
                              differently -- worth knowing, it clears
                              the driver.
  reinit() restores it        The controller was resetting (brown-out or
                              a corrupted command), which the driver can
                              recover from automatically.
  reinit() does not restore   Hardware: supply current, wiring, or the
                              panel itself. No software fix applies.
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw                       # noqa: E402

from bikecomputer import config                        # noqa: E402
from bikecomputer.ui import theme                      # noqa: E402

W = config.DISPLAY_WIDTH
H = config.DISPLAY_HEIGHT

_PALETTE = [(200, 30, 30), (30, 180, 90), (40, 90, 220), (220, 190, 40)]


def frame(n: int, note: str) -> Image.Image:
    """A frame where every pixel changes, forcing a genuine full write."""
    img = Image.new("RGB", (W, H), _PALETTE[n % len(_PALETTE)])
    draw = ImageDraw.Draw(img)

    # Bands make a partially-delivered frame obvious: if the write is
    # truncated, the lower bands keep the previous colour.
    band = H // 8
    for i in range(8):
        shade = 30 + i * 22
        draw.rectangle([0, i * band, W, (i + 1) * band - 4],
                       fill=(shade, shade, shade) if i % 2 else
                       _PALETTE[(n + i) % len(_PALETTE)])

    theme.centred(draw, H // 2 - 60, str(n), theme.font(72, bold=True),
                  (255, 255, 255))
    theme.centred(draw, H // 2 + 30, note, theme.font(16), (255, 255, 255))
    theme.centred(draw, H - 60, "frame counter should be moving",
                  theme.font(13), (230, 230, 230))
    return img


def ask(question: str) -> bool:
    reply = input(f"    {question} [y/n] ").strip().lower()
    return reply.startswith("y")


def run_phase(display, label: str, count: int, delay: float) -> float:
    print(f"\n{label}")
    started = time.monotonic()
    for n in range(count):
        display.invalidate()             # force a full write every time
        display.blit(frame(n, label))
        if delay:
            time.sleep(delay)
        if n and n % 50 == 0:
            print(f"    {n} frames, {time.monotonic() - started:.0f}s")
    elapsed = time.monotonic() - started
    print(f"    done: {count} frames in {elapsed:.0f}s "
          f"({count / max(elapsed, 0.001):.1f} fps)")
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=int, default=config.SPI_SPEED_HZ,
                        help="SPI clock in Hz (default: config value)")
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args()

    config.SPI_SPEED_HZ = args.speed
    from bikecomputer.display import Display

    print(f"\nSPI {args.speed / 1e6:.0f} MHz, {W}x{H}, "
          f"{W * H * 3 / 1024:.0f} kB per full frame")

    display = Display()
    display.init()

    try:
        run_phase(display, "phase 1: full frames, back to back",
                  args.frames, 0.0)
        if not ask("Is the frame counter still updating?"):
            print("\n-> Fails under raw full-frame SPI load. Not the app.")
            return _recovery_check(display)

        run_phase(display, "phase 2: full frames, 4s apart (as the app does)",
                  15, 4.0)
        if not ask("Is the frame counter still updating?"):
            print("\n-> Survives bursts but fails when spread out: points "
                  "at duration or heat, not throughput.")
            return _recovery_check(display)

        print("\n-> Display survived both phases. The panel handles this "
              "load fine, so the trigger is something the application "
              "does differently. That is useful: it clears the driver.")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            display.close()
        except Exception:
            pass
    return 0


def _recovery_check(display) -> int:
    """Does re-running the init sequence bring it back?"""
    print("\nTrying a full re-init...")
    display.reinit()
    display.blit(frame(999, "after reinit"))

    if ask("Did the display come back?"):
        print("\n-> Controller was resetting and re-init recovers it.\n"
              "   Cause is a brown-out or a corrupted command; the driver\n"
              "   can detect nothing, but it can re-init periodically.\n"
              "   Check the display's 3.3V supply and try a lower SPI speed:\n"
              "     venv/bin/python tools/display_stress.py --speed 8000000")
    else:
        print("\n-> Re-init does not recover it, so no software change will.\n"
              "   Look at power: is the panel on the Pi's 3.3V pin, which is\n"
              "   current-limited? Try a separate supply, shorter or\n"
              "   better-grounded wiring, and confirm the backlight pin.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
