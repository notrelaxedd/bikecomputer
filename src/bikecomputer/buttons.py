"""
buttons.py — Three-button input, delivered as an asyncio event stream.

The pins are polled from a coroutine rather than driven by RPi.GPIO edge
interrupts.  Polling three pins at 100 Hz costs nothing measurable on a
Zero 2 W, and it keeps every timing decision (debounce, long-press,
auto-repeat) on the event loop thread instead of in a callback thread
that would need locking to talk to the UI.

Event model
-----------
UP / DOWN   fire SHORT the instant they go down, then REPEAT while held,
            so scrolling a long list feels immediate.
SELECT      fires SHORT on release if it was held briefly, or LONG once
            the hold threshold is crossed — released-vs-held has to be
            distinguishable, so SELECT cannot also auto-repeat.
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from . import config

log = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):   # not on a Pi (dev machine, tests)
    GPIO = None


class Button(Enum):
    UP     = "up"
    SELECT = "select"
    DOWN   = "down"


class Press(Enum):
    SHORT  = "short"
    LONG   = "long"
    REPEAT = "repeat"


@dataclass(frozen=True)
class ButtonEvent:
    button: Button
    press: Press

    def __str__(self) -> str:
        return f"{self.button.value}:{self.press.value}"


_PINS = {
    Button.UP:     config.BUTTON_UP_PIN,
    Button.SELECT: config.BUTTON_SELECT_PIN,
    Button.DOWN:   config.BUTTON_DOWN_PIN,
}

_REPEATABLE = (Button.UP, Button.DOWN)

_POLL_INTERVAL = 0.01   # 100 Hz


class _PinState:
    __slots__ = ("down", "since", "long_sent", "next_repeat", "last_change")

    def __init__(self) -> None:
        self.down = False
        self.since = 0.0
        self.long_sent = False
        self.next_repeat = 0.0
        self.last_change = 0.0


class Buttons:
    """
    Reads the three buttons and publishes ButtonEvents on `self.queue`.

    On a machine without RPi.GPIO the object still constructs and can be
    fed synthetic events with `inject()`, which is what the offline UI
    preview uses.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[ButtonEvent] = asyncio.Queue(maxsize=16)
        self._states = {b: _PinState() for b in Button}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._available = GPIO is not None

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def setup(self) -> None:
        """Configure the GPIO pins.  Safe to call when GPIO is absent."""
        if not self._available:
            log.warning("RPi.GPIO unavailable — buttons disabled")
            return
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for button, pin in _PINS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            log.info("Button %s on BCM %d", button.value, pin)

    async def start(self) -> None:
        self._running = True
        if self._available:
            self._task = asyncio.create_task(self._poll_loop(), name="buttons")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── Event production ────────────────────────────────────────────────────

    def inject(self, button: Button, press: Press = Press.SHORT) -> None:
        """Push a synthetic event (used by the preview tool and tests)."""
        self._emit(ButtonEvent(button, press))

    def _emit(self, event: ButtonEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # The renderer is wedged; dropping input beats unbounded growth.
            log.debug("Button queue full, dropped %s", event)

    async def _poll_loop(self) -> None:
        while self._running:
            now = time.monotonic()
            for button, pin in _PINS.items():
                pressed = GPIO.input(pin) == GPIO.LOW
                self._update(button, pressed, now)
            await asyncio.sleep(_POLL_INTERVAL)

    def _update(self, button: Button, pressed: bool, now: float) -> None:
        st = self._states[button]

        if pressed == st.down:
            # Steady state — the only thing left to do is repeat / long-press.
            if not st.down:
                return
            held = now - st.since
            if button in _REPEATABLE:
                if held >= config.BUTTON_REPEAT_DELAY and now >= st.next_repeat:
                    self._emit(ButtonEvent(button, Press.REPEAT))
                    st.next_repeat = now + config.BUTTON_REPEAT_RATE
            elif not st.long_sent and held >= config.BUTTON_LONG_PRESS:
                st.long_sent = True
                self._emit(ButtonEvent(button, Press.LONG))
            return

        # Edge — ignore contact bounce.
        if now - st.last_change < config.BUTTON_DEBOUNCE:
            return
        st.last_change = now
        st.down = pressed

        if pressed:
            st.since = now
            st.long_sent = False
            st.next_repeat = now + config.BUTTON_REPEAT_DELAY
            if button in _REPEATABLE:
                self._emit(ButtonEvent(button, Press.SHORT))
        else:
            held = now - st.since
            if button not in _REPEATABLE and not st.long_sent:
                if held >= config.BUTTON_LONG_PRESS:
                    self._emit(ButtonEvent(button, Press.LONG))
                else:
                    self._emit(ButtonEvent(button, Press.SHORT))
