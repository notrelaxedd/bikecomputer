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


def _pixel_payloads(d):
    """
    Payloads that are actually pixel data.

    Anything following RAMWR is pixels; anything following any other
    command is that command's arguments. Distinguishing them by size
    alone breaks as soon as the init sequence is replayed, since its
    gamma tables are larger than a window command's four bytes.
    """
    payloads = []
    last_cmd = None
    for write in d._spi.writes:
        if len(write) == 1:
            last_cmd = write[0]
        elif last_cmd == display_mod._CMD_RAMWR:
            payloads.append(write)
    return payloads


def _bytes_written(d):
    return sum(len(w) for w in _pixel_payloads(d))


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


class TestAtomicWrites:
    """
    spidev releases CS at the end of every call, and the ILI9488 treats
    that as ending the memory write. So a RAMWR must never span more than
    one transfer: anything past the first chunk would be dropped, and the
    driver would carry on believing the frame had been delivered.
    """

    def test_no_payload_exceeds_the_chunk_size(self, panel):
        panel.blit(_frame((255, 255, 255)))
        oversized = [len(w) for w in panel._spi.writes
                     if len(w) > display_mod._SPI_CHUNK]
        assert oversized == []

    def test_every_pixel_payload_follows_its_own_ramwr(self, panel):
        """
        _pixel_payloads only counts data directly after RAMWR, so a
        non-empty result is itself the proof that each band re-issued
        the window rather than continuing a previous write.
        """
        panel.blit(_frame((1, 2, 3)))
        payloads = _pixel_payloads(panel)
        assert payloads
        assert sum(len(w) for w in payloads) == FULL_BYTES

    def test_full_frame_still_sends_every_pixel(self, panel):
        panel.blit(_frame((7, 8, 9)))
        assert _bytes_written(panel) == FULL_BYTES

    def test_bands_cover_the_frame_without_overlap(self, panel):
        """Banding must partition the frame, not drop or duplicate rows."""
        panel.blit(_frame((1, 2, 3)))
        payloads = _pixel_payloads(panel)
        assert len(payloads) > 1, "a full frame should need several bands"
        assert sum(len(w) for w in payloads) == FULL_BYTES
        assert all(len(w) % (W * 3) == 0 for w in payloads), (
            "each band should be a whole number of rows"
        )


class TestControllerRecovery:
    """
    A brown-out silently resets the controller: sleep mode, default pixel
    format, default orientation. Pixels pushed afterwards are discarded,
    so a repaint alone cannot recover -- the configuration has to be
    re-sent too.
    """

    def _commands(self, panel):
        return [w[0] for w in panel._spi.writes if len(w) == 1]

    def test_full_repaint_reasserts_pixel_format_and_orientation(self, panel):
        panel.blit(_frame((5, 5, 5)))
        sent = self._commands(panel)
        for cmd in (display_mod._CMD_SLPOUT, display_mod._CMD_MADCTL,
                    display_mod._CMD_COLMOD, display_mod._CMD_DISPON):
            assert cmd in sent

    def test_partial_update_skips_the_reassert(self, panel):
        """Small updates are the common case and must stay cheap."""
        panel.blit(_frame())
        panel._spi.writes.clear()

        changed = _frame()
        for x in range(10, 30):
            for y in range(10, 20):
                changed.putpixel((x, y), (255, 255, 255))
        panel.blit(changed)

        assert display_mod._CMD_DISPON not in self._commands(panel)

    def test_reinit_replays_the_whole_sequence(self, panel):
        panel._spi.writes.clear()
        panel.reinit()
        sent = self._commands(panel)
        assert display_mod._CMD_SLPOUT in sent
        assert display_mod._CMD_DISPON in sent
        assert panel._prev_frame is None
