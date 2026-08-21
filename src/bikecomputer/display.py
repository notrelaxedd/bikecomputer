"""
display.py — ILI9488 SPI display driver.

Pillow RGB Image → BGR565 bytes → /dev/spidev0.0
DC and RST driven via RPi.GPIO (BCM numbering).

Dirty-rectangle writes use the ILI9488's CASET/RASET/RAMWR command set
to push only changed screen regions, keeping SPI bandwidth manageable.
"""

from __future__ import annotations
import logging
import time
import struct
from typing import Tuple

import numpy as np
import spidev
import RPi.GPIO as GPIO
from PIL import Image

from . import config

log = logging.getLogger(__name__)

# ILI9488 command bytes
_CMD_SWRESET = 0x01
_CMD_SLPOUT  = 0x11
_CMD_DISPON  = 0x29
_CMD_CASET   = 0x2A   # Column Address Set
_CMD_RASET   = 0x2B   # Row Address Set
_CMD_RAMWR   = 0x2C   # Memory Write
_CMD_MADCTL  = 0x36   # Memory Access Control
_CMD_COLMOD  = 0x3A   # Interface Pixel Format

_INIT_SEQ = [
    # Positive/Negative Gamma Control
    (_CMD_SLPOUT, None),                   # Sleep Out (needs 120 ms)
    (0xE0, bytes([0x00, 0x03, 0x09, 0x08, 0x16, 0x0A, 0x3F,
                  0x78, 0x4C, 0x09, 0x0A, 0x08, 0x16, 0x1A, 0x0F])),
    (0xE1, bytes([0x00, 0x16, 0x19, 0x03, 0x0F, 0x05, 0x32,
                  0x45, 0x46, 0x04, 0x0E, 0x0D, 0x35, 0x37, 0x0F])),
    (0xC0, bytes([0x17, 0x15])),           # Power Control 1
    (0xC1, bytes([0x41])),                 # Power Control 2
    (0xC5, bytes([0x00, 0x12, 0x80])),    # VCOM Control
    (_CMD_MADCTL, bytes([config.MADCTL])), # Orientation + BGR
    (_CMD_COLMOD, bytes([0x66])),
    (0xB0, bytes([0x00])),                 # Interface Mode Control
    (0xB1, bytes([0xA0])),                 # Frame Rate: 60 Hz
    (0xB4, bytes([0x02])),                 # Display Inversion: 2-dot
    (0xB6, bytes([0x02, 0x02, 0x3B])),    # Display Function Control
    (0xE9, bytes([0x00])),                 # Disable 24-bit data
    (0xF7, bytes([0xA9, 0x51, 0x2C, 0x82])),  # Adjust Control 3
    (_CMD_DISPON, None),                   # Display On
]

# Bytes per spidev write call.  Each call asserts and releases CS, and a
# full-screen update is ~460 kB, so a small chunk means over a hundred CS
# toggles inside a single RAMWR -- which some ILI9488 panels treat as
# terminating the write.  Fewer, larger chunks means fewer chances to
# desync.  Requires spidev bufsiz to match; see README step 5.
_SPI_CHUNK = 65536

# Force a full repaint at least this often.  Dirty-rectangle writes are an
# optimisation built on the assumption that the panel matches _prev_frame,
# and nothing in the protocol lets us verify that -- there is no read-back.
# If the panel ever loses a write, the software would happily send nothing
# for the rest of the session, since as far as it knows the frame is
# already correct. A periodic unconditional repaint bounds how long any
# such desync can last.
_FULL_REFRESH_INTERVAL = 4.0   # seconds

# Above this fraction of changed pixels, skip the bounding-box arithmetic
# and just repaint everything: the saving no longer justifies the risk of
# drifting out of sync.
_FULL_REFRESH_AREA = 0.6


def _image_to_bgr565(img: Image.Image) -> bytes:
    r, g, b = img.convert("RGB").split()
    return Image.merge("RGB", (b, g, r)).tobytes()


class Display:
    def __init__(self) -> None:
        self._spi = spidev.SpiDev()
        self._prev_frame: bytes | None = None
        self._last_full = 0.0

    def init(self) -> None:
        """Open SPI, configure GPIO, send ILI9488 init sequence."""
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(config.DC_PIN,    GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(config.RESET_PIN, GPIO.OUT, initial=GPIO.HIGH)

        self._reset()

        self._spi.open(config.SPI_BUS, config.SPI_DEVICE)
        self._spi.max_speed_hz = config.SPI_SPEED_HZ
        self._spi.mode = 0

        for cmd, data in _INIT_SEQ:
            self._cmd(cmd)
            if data:
                self._data(data)
            if cmd == _CMD_SLPOUT:
                time.sleep(0.12)

        log.info("ILI9488 initialised (%dx%d)", config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)

    def close(self) -> None:
        self._spi.close()
        GPIO.cleanup()

    def _reset(self) -> None:
        GPIO.output(config.RESET_PIN, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(config.RESET_PIN, GPIO.LOW)
        time.sleep(0.02)
        GPIO.output(config.RESET_PIN, GPIO.HIGH)
        time.sleep(0.15)

    def _cmd(self, cmd: int) -> None:
        GPIO.output(config.DC_PIN, GPIO.LOW)
        self._spi.writebytes2([cmd])

    def _data(self, data: bytes | bytearray) -> None:
        GPIO.output(config.DC_PIN, GPIO.HIGH)
        mv = memoryview(data)
        for offset in range(0, len(data), _SPI_CHUNK):
            self._spi.writebytes2(mv[offset:offset + _SPI_CHUNK])

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._cmd(_CMD_CASET)
        self._data(struct.pack(">HH", x0, x1))
        self._cmd(_CMD_RASET)
        self._data(struct.pack(">HH", y0, y1))
        self._cmd(_CMD_RAMWR)

    def invalidate(self) -> None:
        """
        Force the next blit to be a full write.

        Dirty-rectangle updates assume the panel's contents match
        _prev_frame.  If a write dies partway through, that assumption is
        wrong for every later frame and the display can never repaint
        itself -- so any failed blit has to drop the cache.
        """
        self._prev_frame = None

    def set_madctl(self, value: int) -> None:
        """
        Change orientation at runtime.

        Used by tools/orientation.py to cycle through the candidates without
        an edit-and-restart for each one.  The cached frame is dropped
        because the previous contents no longer describe what is on the
        panel, so the next blit must be a full write.
        """
        self._cmd(_CMD_MADCTL)
        self._data(bytes([value]))
        self._prev_frame = None

    def blit(self, img: Image.Image) -> None:
        """
        Write a full 320×480 Pillow image to the display.
        Computes a dirty bounding box against the previous frame and only
        transmits the changed rectangle.
        """
        raw = _image_to_bgr565(img)

        if self._prev_frame is None:
            # First frame: full write
            self._full_write(raw)
        else:
            self._dirty_write(raw)

        self._prev_frame = raw

    def _reassert_state(self) -> None:
        """
        Re-send the registers that determine how pixel data is interpreted.

        If the controller browns out during a heavy SPI burst it silently
        resets: back to sleep mode, default pixel format, default
        orientation. Every frame we push after that is discarded, and
        because nothing in the driver ever re-ran the init sequence, the
        panel stayed white until the process restarted.

        These four commands are about ten bytes and are no-ops on a
        healthy panel, so paying them on each full repaint costs nothing
        and makes a controller reset self-healing within one refresh
        cycle. SLPOUT normally wants 120 ms afterwards; that delay is
        skipped deliberately rather than stuttering the UI every time --
        a frame lost immediately after a wake is picked up by the next
        refresh.
        """
        self._cmd(_CMD_SLPOUT)
        self._cmd(_CMD_MADCTL)
        self._data(bytes([config.MADCTL]))
        self._cmd(_CMD_COLMOD)
        self._data(bytes([0x66]))
        self._cmd(_CMD_DISPON)

    def reinit(self) -> None:
        """Full hardware reset and re-init, for recovering a wedged panel."""
        log.warning("Re-initialising display")
        self._reset()
        for cmd, data in _INIT_SEQ:
            self._cmd(cmd)
            if data:
                self._data(data)
            if cmd == _CMD_SLPOUT:
                time.sleep(0.12)
        self._prev_frame = None

    def _write_region(self, arr, x0: int, y0: int, x1: int, y1: int) -> None:
        """
        Send a rectangle as horizontal bands, each one a complete
        CASET/RASET/RAMWR sequence whose pixel data fits in a single SPI
        transfer.

        This matters because spidev releases CS at the end of every call,
        and the ILI9488 treats CS going high as ending the memory write.
        A payload large enough to be split therefore loses everything
        after the first chunk -- the panel keeps whatever was there, and
        because dirty-rectangle logic then believes the frame was
        delivered, it never repaints. Banding keeps each write atomic, so
        the split happens between commands rather than inside one.
        """
        width = x1 - x0 + 1
        row_bytes = width * 3
        rows_per_band = max(1, _SPI_CHUNK // row_bytes)

        for band_y0 in range(y0, y1 + 1, rows_per_band):
            band_y1 = min(band_y0 + rows_per_band - 1, y1)
            self._set_window(x0, band_y0, x1, band_y1)
            band = arr[band_y0:band_y1 + 1, x0:x1 + 1]
            GPIO.output(config.DC_PIN, GPIO.HIGH)
            self._spi.writebytes2(np.ascontiguousarray(band).tobytes())

    def _full_write(self, raw: bytes) -> None:
        # Cheap insurance: if the controller reset since the last frame,
        # this is what makes the repaint below actually land.
        self._reassert_state()
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(
            config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3)
        self._write_region(arr, 0, 0,
                           config.DISPLAY_WIDTH - 1, config.DISPLAY_HEIGHT - 1)
        self._last_full = time.monotonic()

    def _dirty_write(self, raw: bytes) -> None:
        W = config.DISPLAY_WIDTH
        H = config.DISPLAY_HEIGHT
        curr = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)
        if self._prev_frame is None:
            self._set_window(0, 0, W - 1, H - 1)
            self._data(raw)
            self._prev_frame = raw
            return
        prev = np.frombuffer(self._prev_frame, dtype=np.uint8).reshape(H, W, 3)
        diff = np.any(curr != prev, axis=2)
        rows = np.any(diff, axis=1)
        cols = np.any(diff, axis=0)

        stale = time.monotonic() - self._last_full > _FULL_REFRESH_INTERVAL
        if not rows.any():
            if stale:
                self._full_write(raw)   # heal a desync we cannot detect
                self._prev_frame = raw
            return

        y0 = int(np.argmax(rows))
        y1 = int(H - 1 - np.argmax(rows[::-1]))
        x0 = int(np.argmax(cols))
        x1 = int(W - 1 - np.argmax(cols[::-1]))

        area = (y1 - y0 + 1) * (x1 - x0 + 1) / float(W * H)
        if stale or area > _FULL_REFRESH_AREA:
            self._full_write(raw)
            self._prev_frame = raw
            return

        self._write_region(curr, x0, y0, x1, y1)
        self._prev_frame = raw

    def fill(self, colour: Tuple[int, int, int] = (0, 0, 0)) -> None:
        """Blank the display to a solid colour."""
        img = Image.new("RGB", (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT), colour)
        self._prev_frame = None
        self.blit(img)
