"""
Dirty-rectangle logic tests, with spidev and RPi.GPIO stubbed out.

The point of interest is not the pixel maths but the repaint policy. A
dirty-rectangle scheme is a bet that the panel's contents match what we
think we last sent, and SPI gives no read-back to check. If the panel ever
misses a write, the software will happily send nothing for the rest of the
session because as far as it knows the frame is already correct -- a
permanently corrupted screen that only a restart clears.

These tests pin the escape hatches: large changes repaint wholesale, and
an unconditional repaint happens periodically no matter what.
"""

import sys
import types

import pytest
from PIL import Image


# ── Hardware stubs (installed before importing the driver) ──────────────────

class _FakeSpi:
    def __init__(self):
        self.max_speed_hz = 0
        self.mode = 0
        self.writes = []          # every payload handed to the bus

    def open(self, bus, device):
        pass

    def close(self):
        pass

    def writebytes2(self, data):
        self.writes.append(bytes(data))


def _install_stubs():
    spidev = types.ModuleType("spidev")
    spidev.SpiDev = _FakeSpi
    sys.modules.setdefault("spidev", spidev)

    gpio = types.ModuleType("RPi.GPIO")
    gpio.BCM = gpio.OUT = gpio.IN = gpio.HIGH = gpio.LOW = 0
    gpio.PUD_UP = gpio.FALLING = gpio.BOTH = 0
    for name in ("setmode", "setwarnings", "setup", "output", "cleanup"):
        setattr(gpio, name, lambda *a, **k: None)

    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    sys.modules.setdefault("RPi", rpi)
    sys.modules.setdefault("RPi.GPIO", gpio)


_install_stubs()

from src.bikecomputer import config                       # noqa: E402
from src.bikecomputer import display as display_mod       # noqa: E402

W = config.DISPLAY_WIDTH
H = config.DISPLAY_HEIGHT
FULL_BYTES = W * H * 3


@pytest.fixture
def panel():
    d = display_mod.Display()
    d.init()
    d._spi.writes.clear()
    return d


def _frame(colour=(0, 0, 0)):
    return Image.new("RGB", (W, H), colour)


def _bytes_written(d):
    """Total payload since the last clear, ignoring command bytes."""
    return sum(len(w) for w in d._spi.writes if len(w) > 4)


class TestRepaintPolicy:
    def test_first_frame_is_a_full_write(self, panel):
        panel.blit(_frame((10, 20, 30)))
        assert _bytes_written(panel) == FULL_BYTES

    def test_unchanged_frame_sends_nothing(self, panel):
        img = _frame((10, 20, 30))
        panel.blit(img)
        panel._spi.writes.clear()
        panel.blit(img)
        assert _bytes_written(panel) == 0

    def test_small_change_sends_only_that_region(self, panel):
        panel.blit(_frame())
        panel._spi.writes.clear()

        changed = _frame()
        for x in range(10, 30):
            for y in range(10, 20):
                changed.putpixel((x, y), (255, 255, 255))
        panel.blit(changed)

        written = _bytes_written(panel)
        assert 0 < written < FULL_BYTES // 10

    def test_large_change_repaints_everything(self, panel):
        """
        Opening a menu replaces the whole screen. Sending that as a
        'dirty rectangle' is the same byte count as a full write with
        none of the resynchronisation, so it should just be a full write.
        """
        panel.blit(_frame((0, 0, 0)))
        panel._spi.writes.clear()
        panel.blit(_frame((255, 255, 255)))
        assert _bytes_written(panel) == FULL_BYTES

    def test_periodic_repaint_heals_an_undetectable_desync(self, panel, monkeypatch):
        img = _frame((10, 20, 30))
        panel.blit(img)
        panel._spi.writes.clear()

        # Same frame again: normally nothing to send.
        panel.blit(img)
        assert _bytes_written(panel) == 0

        # Once the refresh interval has passed, the identical frame must
        # still be pushed, because the panel may silently disagree.
        now = display_mod.time.monotonic()
        monkeypatch.setattr(
            display_mod.time, "monotonic",
            lambda: now + display_mod._FULL_REFRESH_INTERVAL + 1,
        )
        panel._spi.writes.clear()
        panel.blit(img)
        assert _bytes_written(panel) == FULL_BYTES

    def test_invalidate_forces_a_full_write(self, panel):
        img = _frame((10, 20, 30))
        panel.blit(img)
        panel.invalidate()
        panel._spi.writes.clear()
        panel.blit(img)
        assert _bytes_written(panel) == FULL_BYTES
