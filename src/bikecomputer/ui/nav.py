"""
ui/nav.py — View protocol, navigation stack, and the shared app context.

Interaction model, three buttons total:

    Data screens (ride / map / detail)
        UP / DOWN     previous / next screen
        SELECT        play-pause music
        SELECT held   open the menu

    Menus and lists (everything else)
        UP / DOWN     move the selection, auto-repeating when held
        SELECT        activate the highlighted row
        SELECT held   go back one level

The rule that makes it learnable: held-SELECT always means "back", and it
is the only way out of a menu, so the rider can never get stranded.
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

from .. import config
from ..buttons import Button, ButtonEvent, Press
from . import theme

log = logging.getLogger(__name__)


# ── Navigation results ──────────────────────────────────────────────────────

class Action:
    """Base for what a view asks the navigator to do next."""


@dataclass
class Push(Action):
    view: "View"


@dataclass
class Pop(Action):
    # A confirmation dialog often needs to close the screen that opened it
    # too: after "Forget device", the device sheet it came from is about to
    # describe something that no longer exists.
    levels: int = 1


@dataclass
class Home(Action):
    """Drop every overlay and return to the data screens."""


HANDLED = Action()      # consumed the event, no navigation change


# ── Shared context ──────────────────────────────────────────────────────────

@dataclass
class AppContext:
    """Everything a view is allowed to touch, passed to every call."""
    state: object                     # ride.RideState
    settings: object                  # settings.Settings
    music: object = None              # music.MusicController
    bt: object = None                 # bluetooth.BluetoothManager
    mapview: object = None
    logger: object = None
    request_shutdown: object = None   # callable set by the app

    _toast_text: str = field(default="", repr=False)
    _toast_until: float = field(default=0.0, repr=False)
    audio_connected: bool = False     # refreshed by the app's housekeeping task

    def toast(self, text: str, seconds: float = 2.5) -> None:
        self._toast_text = text
        self._toast_until = time.monotonic() + seconds
        log.info("Toast: %s", text)

    @property
    def active_toast(self) -> str:
        if time.monotonic() < self._toast_until:
            return self._toast_text
        return ""

    @property
    def metric(self) -> bool:
        return self.settings.metric


# ── View protocol ───────────────────────────────────────────────────────────

class View:
    """
    Subclasses override render(), and usually handle().

    Returning None from handle() means "not interested"; the navigator
    then applies its own default for that button.
    """

    fps: int = config.MENU_FPS
    refresh_interval: float = 0.0     # >0 to have tick() called periodically

    async def on_show(self, ctx: AppContext) -> None:
        pass

    async def on_hide(self, ctx: AppContext) -> None:
        pass

    async def tick(self, ctx: AppContext) -> None:
        pass

    async def handle(self, event: ButtonEvent, ctx: AppContext) -> Optional[Action]:
        return None

    def render(self, ctx: AppContext) -> Image.Image:
        raise NotImplementedError


# ── Navigator ───────────────────────────────────────────────────────────────

class Navigator:
    """
    Holds a carousel of data screens plus a stack of overlay views.

    The carousel is the floor: popping the last overlay lands back on
    whichever data screen was showing, rather than on a blank root.
    """

    def __init__(self, pages: list[View], ctx: AppContext) -> None:
        self._pages = pages
        self._page = 0
        self._stack: list[View] = []
        self._ctx = ctx
        self._menu_factory = None     # set by the app to avoid a circular import
        self._last_tick = 0.0

    def set_menu_factory(self, factory) -> None:
        self._menu_factory = factory

    @property
    def active(self) -> View:
        return self._stack[-1] if self._stack else self._pages[self._page]

    @property
    def in_menu(self) -> bool:
        return bool(self._stack)

    async def start(self, home: str = "ride") -> None:
        for index, page in enumerate(self._pages):
            if getattr(page, "name", "") == home:
                self._page = index
                break
        await self.active.on_show(self._ctx)

    # ── Events ──────────────────────────────────────────────────────────────

    async def dispatch(self, event: ButtonEvent) -> None:
        # "Held SELECT goes back" is enforced here rather than trusted to
        # each view.  A view that forgot to handle it — or one that threw
        # while handling it — would otherwise strand the rider in a menu
        # with no way back to the speed readout.
        if (event.button is Button.SELECT and event.press is Press.LONG
                and self._stack):
            await self._pop()
            return

        # Navigation itself is inside the guard, not just the view's
        # handler.  Pushing a view runs its on_show(), and an exception
        # escaping here would propagate out of the button task and take
        # down the gather that owns the render loop with it -- leaving a
        # frozen screen and no way back short of a restart.
        view = self.active
        try:
            result = await view.handle(event, self._ctx)

            if result is HANDLED:
                return
            if result is not None:
                await self._apply(result)
                return

            await self._default(event)
        except Exception as exc:
            log.error("View %s failed handling %s: %s",
                      type(view).__name__, event, exc, exc_info=True)
            self._ctx.toast("Something went wrong")

    async def _apply(self, action: Action) -> None:
        if isinstance(action, Push):
            await self.active.on_hide(self._ctx)
            self._stack.append(action.view)
            await action.view.on_show(self._ctx)
        elif isinstance(action, Pop):
            for _ in range(max(1, action.levels)):
                await self._pop()
        elif isinstance(action, Home):
            while self._stack:
                await self._pop()

    async def _pop(self) -> None:
        if not self._stack:
            return
        view = self._stack.pop()
        await view.on_hide(self._ctx)
        await self.active.on_show(self._ctx)

    async def _default(self, event: ButtonEvent) -> None:
        """Navigator-level fallbacks for events no view claimed."""
        if event.button is Button.SELECT and event.press is Press.LONG:
            if self._stack:
                await self._pop()
            elif self._menu_factory is not None:
                await self._apply(Push(self._menu_factory()))
            return

        if self._stack:
            return      # overlays handle their own scrolling

        if event.button is Button.UP and event.press in (Press.SHORT, Press.REPEAT):
            await self._change_page(-1)
        elif event.button is Button.DOWN and event.press in (Press.SHORT, Press.REPEAT):
            await self._change_page(+1)
        elif event.button is Button.SELECT and event.press is Press.SHORT:
            await self._toggle_music()

    async def _change_page(self, delta: int) -> None:
        await self.active.on_hide(self._ctx)
        self._page = (self._page + delta) % len(self._pages)
        await self.active.on_show(self._ctx)

    async def _toggle_music(self) -> None:
        music = self._ctx.music
        if music is None:
            return
        try:
            await music.toggle()
            self._ctx.toast(("Paused  " if not music.playing else "")
                            + music.now_playing())
        except Exception as exc:
            log.debug("Music toggle failed: %s", exc)
            self._ctx.toast("Music unavailable")

    # ── Frame production ────────────────────────────────────────────────────

    async def tick(self) -> None:
        """Give the active view a chance to refresh async data."""
        view = self.active
        if view.refresh_interval <= 0:
            return
        now = time.monotonic()
        if now - self._last_tick < view.refresh_interval:
            return
        self._last_tick = now
        try:
            await view.tick(self._ctx)
        except Exception as exc:
            log.debug("tick() failed on %s: %s", type(view).__name__, exc)

    def render(self) -> Image.Image:
        view = self.active
        img = view.render(self._ctx)
        message = self._ctx.active_toast
        if message:
            from PIL import ImageDraw
            theme.toast(ImageDraw.Draw(img), message)
        return img

    @property
    def fps(self) -> int:
        return self.active.fps
