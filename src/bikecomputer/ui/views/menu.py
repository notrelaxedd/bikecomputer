"""
ui/views/menu.py — The generic list view every menu is built from.

Subclasses only supply build(), which returns the rows.  Rows are rebuilt
on every frame so values like "Connected" or the current volume are never
stale; anything that needs I/O to compute is cached by the subclass in
tick() instead.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from PIL import Image

from ... import config
from ...buttons import Button, Press, ButtonEvent
from .. import theme
from ..nav import Action, AppContext, HANDLED, Pop, View


ValueFn = Callable[[AppContext], str]
ActionFn = Callable[[AppContext], Awaitable[Optional[Action]]]


@dataclass
class MenuItem:
    label: str
    value: str | ValueFn = ""
    action: Optional[ActionFn] = None
    colour: tuple = config.CLR_DIM

    def value_text(self, ctx: AppContext) -> str:
        return self.value(ctx) if callable(self.value) else self.value


class MenuView(View):
    title = "Menu"
    hint_text = "Hold SELECT: back"
    empty_text = "Nothing here"
    fps = config.MENU_FPS

    def __init__(self) -> None:
        self.selected = 0
        self._items: list[MenuItem] = []
        self._busy = ""          # shown instead of the list during slow work

    # ── Content ─────────────────────────────────────────────────────────────

    def build(self, ctx: AppContext) -> list[MenuItem]:
        raise NotImplementedError

    def subtitle(self, ctx: AppContext) -> str:
        return ""

    def _refresh_items(self, ctx: AppContext) -> list[MenuItem]:
        self._items = self.build(ctx)
        if self._items:
            self.selected = max(0, min(self.selected, len(self._items) - 1))
        else:
            self.selected = 0
        return self._items

    # ── Input ───────────────────────────────────────────────────────────────

    async def handle(self, event: ButtonEvent, ctx: AppContext) -> Optional[Action]:
        if event.button is Button.SELECT and event.press is Press.LONG:
            return Pop()

        if not self._items:
            self._refresh_items(ctx)

        moving = event.press in (Press.SHORT, Press.REPEAT)
        if event.button is Button.UP and moving:
            self._move(-1)
            return HANDLED
        if event.button is Button.DOWN and moving:
            self._move(+1)
            return HANDLED

        if event.button is Button.SELECT and event.press is Press.SHORT:
            return await self._activate(ctx)

        return HANDLED

    def _move(self, delta: int) -> None:
        if not self._items:
            return
        self.selected = (self.selected + delta) % len(self._items)

    async def _activate(self, ctx: AppContext) -> Optional[Action]:
        if not self._items:
            return HANDLED
        item = self._items[self.selected]
        if item.action is None:
            return HANDLED
        self._busy = item.label
        try:
            return await item.action(ctx) or HANDLED
        finally:
            self._busy = ""

    # ── Render ──────────────────────────────────────────────────────────────

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        top = theme.header(draw, self.title, self.subtitle(ctx))

        items = self._refresh_items(ctx)
        rows = [(item.label, item.value_text(ctx), item.colour) for item in items]
        theme.draw_list(draw, top, rows, self.selected, self.empty_text)
        theme.hint(draw, self.hint_text)
        return img


class AdjustView(View):
    """
    Full-screen slider for a single numeric setting.

    UP/DOWN change the value and apply it immediately, so the rider hears
    the volume change while holding the button rather than after leaving
    the screen.
    """

    fps = config.MENU_FPS

    def __init__(self, title: str, get, apply, step: int = 5,
                 lo: int = 0, hi: int = 100, unit: str = "") -> None:
        self.title = title
        self._get = get
        self._apply = apply
        self._step = step
        self._lo = lo
        self._hi = hi
        self._unit = unit

    async def handle(self, event: ButtonEvent, ctx: AppContext) -> Optional[Action]:
        if event.button is Button.SELECT:
            return Pop()
        if event.press not in (Press.SHORT, Press.REPEAT):
            return HANDLED

        # UP is the top button, so UP means "more".
        delta = self._step if event.button is Button.UP else -self._step
        value = max(self._lo, min(self._hi, self._get(ctx) + delta))
        await self._apply(ctx, value)
        return HANDLED

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        top = theme.header(draw, self.title)

        value = self._get(ctx)
        big = theme.font(64, bold=True)
        theme.centred(draw, top + 50, f"{value}{self._unit}", big, config.CLR_TEXT)
        theme.slider(draw, top + 140, value, self._lo, self._hi)
        theme.hint(draw, "UP/DOWN: adjust   SELECT: done")
        return img


class ConfirmView(MenuView):
    """Yes/no gate for anything destructive or hard to undo."""

    hint_text = "Hold SELECT: cancel"

    def __init__(self, title: str, question: str, on_confirm: ActionFn) -> None:
        super().__init__()
        self.title = title
        self._question = question
        self._on_confirm = on_confirm
        self.selected = 1          # default to "No"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        async def yes(c: AppContext):
            result = await self._on_confirm(c)
            return result or Pop()

        async def no(c: AppContext):
            return Pop()

        return [MenuItem("Yes", action=yes), MenuItem("No", action=no)]

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        top = theme.header(draw, self.title)

        draw.text((theme.PAD, top + 14),
                  theme.ellipsise(draw, self._question, theme.font(15),
                                  theme.W - 2 * theme.PAD),
                  font=theme.font(15), fill=config.CLR_TEXT)

        items = self._refresh_items(ctx)
        rows = [(item.label, "", item.colour) for item in items]
        theme.draw_list(draw, top + 50, rows, self.selected)
        theme.hint(draw, self.hint_text)
        return img
