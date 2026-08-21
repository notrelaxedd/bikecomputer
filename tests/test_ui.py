"""
Unit tests for the input, settings, units and navigation layers.
No hardware, no D-Bus, no display.
"""

import asyncio
import json

import pytest

from src.bikecomputer import config, units
from src.bikecomputer.buttons import Button, ButtonEvent, Buttons, Press
from src.bikecomputer.ride import RideState
from src.bikecomputer.settings import Settings
from src.bikecomputer.ui.nav import AppContext, HANDLED, Navigator, Pop, View
from src.bikecomputer.ui.views.menu import AdjustView, MenuItem, MenuView


def run(coro):
    return asyncio.run(coro)


# ── Units ───────────────────────────────────────────────────────────────────

class TestUnits:
    def test_speed_imperial(self):
        assert units.speed(10.0, metric=False) == pytest.approx(22.369, abs=1e-3)

    def test_speed_metric(self):
        assert units.speed(10.0, metric=True) == pytest.approx(36.0)

    def test_distance_switches_with_preference(self):
        assert units.distance(1609.344, metric=False) == pytest.approx(1.0)
        assert units.distance(1000.0, metric=True) == pytest.approx(1.0)

    def test_altitude_feet(self):
        assert units.altitude(100.0, metric=False) == pytest.approx(328.084, abs=1e-3)

    def test_duration_formats(self):
        assert units.fmt_duration(45) == "0:45"
        assert units.fmt_duration(125) == "2:05"
        assert units.fmt_duration(3661) == "1:01:01"


# ── Settings ────────────────────────────────────────────────────────────────

class TestSettings:
    def test_defaults_when_file_missing(self, tmp_path):
        s = Settings.load(tmp_path / "nope.json")
        assert s.units == "imperial"
        assert s.music_source == "local"

    def test_round_trip(self, tmp_path):
        path = tmp_path / "settings.json"
        s = Settings.load(path)
        s.set("hr_device", "AA:BB:CC:DD:EE:FF")
        s.set("volume", 42)

        again = Settings.load(path)
        assert again.hr_device == "AA:BB:CC:DD:EE:FF"
        assert again.volume == 42

    def test_unknown_keys_ignored(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"volume": 30, "from_a_newer_build": True}))
        s = Settings.load(path)
        assert s.volume == 30
        assert not hasattr(s, "from_a_newer_build")

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{ not json")
        assert Settings.load(path).volume == config.DEFAULT_VOLUME

    def test_toggle_persists(self, tmp_path):
        path = tmp_path / "settings.json"
        s = Settings.load(path)
        assert s.toggle("autopause") is False
        assert Settings.load(path).autopause is False


# ── Button decoding ─────────────────────────────────────────────────────────

class TestButtons:
    def _drain(self, buttons):
        events = []
        while not buttons.queue.empty():
            events.append(buttons.queue.get_nowait())
        return events

    def test_short_press_on_release(self):
        b = Buttons()
        b._update(Button.SELECT, True, 100.0)
        b._update(Button.SELECT, False, 100.2)
        assert self._drain(b) == [ButtonEvent(Button.SELECT, Press.SHORT)]

    def test_long_press_fires_while_still_held(self):
        b = Buttons()
        b._update(Button.SELECT, True, 100.0)
        b._update(Button.SELECT, True, 100.0 + config.BUTTON_LONG_PRESS + 0.01)
        assert self._drain(b) == [ButtonEvent(Button.SELECT, Press.LONG)]

    def test_long_press_not_followed_by_short(self):
        b = Buttons()
        b._update(Button.SELECT, True, 100.0)
        b._update(Button.SELECT, True, 100.0 + config.BUTTON_LONG_PRESS + 0.01)
        b._update(Button.SELECT, False, 101.5)
        assert self._drain(b) == [ButtonEvent(Button.SELECT, Press.LONG)]

    def test_up_fires_immediately_then_repeats(self):
        b = Buttons()
        t = 100.0
        b._update(Button.UP, True, t)
        b._update(Button.UP, True, t + config.BUTTON_REPEAT_DELAY + 0.01)
        events = self._drain(b)
        assert events[0].press is Press.SHORT
        assert events[1].press is Press.REPEAT

    def test_bounce_is_ignored(self):
        b = Buttons()
        b._update(Button.DOWN, True, 100.0)
        b._update(Button.DOWN, False, 100.0 + config.BUTTON_DEBOUNCE / 2)
        # The spurious release is swallowed, so no second edge is recorded.
        assert len(self._drain(b)) == 1


# ── Navigation ──────────────────────────────────────────────────────────────

class Page(View):
    def __init__(self, name):
        self.name = name
        self.shown = 0

    async def on_show(self, ctx):
        self.shown += 1

    def render(self, ctx):
        raise AssertionError("render should not be called in these tests")


class Overlay(View):
    async def handle(self, event, ctx):
        return HANDLED

    def render(self, ctx):
        raise AssertionError


def make_nav():
    ctx = AppContext(state=RideState(), settings=Settings())
    nav = Navigator([Page("ride"), Page("map"), Page("detail")], ctx)
    return nav, ctx


class TestNavigator:
    def test_starts_on_configured_home_screen(self):
        nav, _ = make_nav()
        run(nav.start(home="detail"))
        assert nav.active.name == "detail"

    def test_up_down_cycle_pages(self):
        nav, _ = make_nav()
        run(nav.start(home="ride"))
        run(nav.dispatch(ButtonEvent(Button.DOWN, Press.SHORT)))
        assert nav.active.name == "map"
        run(nav.dispatch(ButtonEvent(Button.UP, Press.SHORT)))
        assert nav.active.name == "ride"

    def test_pages_wrap_around(self):
        nav, _ = make_nav()
        run(nav.start(home="ride"))
        run(nav.dispatch(ButtonEvent(Button.UP, Press.SHORT)))
        assert nav.active.name == "detail"

    def test_held_select_opens_menu_and_backs_out(self):
        nav, _ = make_nav()
        run(nav.start())
        nav.set_menu_factory(Overlay)

        run(nav.dispatch(ButtonEvent(Button.SELECT, Press.LONG)))
        assert nav.in_menu

        # The overlay consumes everything except the way back out.
        run(nav.dispatch(ButtonEvent(Button.SELECT, Press.LONG)))
        assert not nav.in_menu

    def test_paging_disabled_while_a_menu_is_open(self):
        nav, _ = make_nav()
        run(nav.start(home="ride"))
        nav.set_menu_factory(Overlay)
        run(nav.dispatch(ButtonEvent(Button.SELECT, Press.LONG)))

        run(nav.dispatch(ButtonEvent(Button.DOWN, Press.SHORT)))
        assert nav.in_menu

    def test_menu_that_throws_on_open_does_not_kill_the_app(self):
        """
        Opening the menu runs the new view's on_show inside dispatch. If
        that escaped, it would propagate out of the button task and take
        the render loop down with it -- a frozen screen needing a restart.
        """
        class Exploding(View):
            async def on_show(self, ctx):
                raise RuntimeError("boom")

            def render(self, ctx):
                raise AssertionError

        nav, ctx = make_nav()
        run(nav.start())
        nav.set_menu_factory(Exploding)
        run(nav.dispatch(ButtonEvent(Button.SELECT, Press.LONG)))
        assert ctx.active_toast

    def test_view_exception_does_not_propagate(self):
        class Exploding(View):
            async def handle(self, event, ctx):
                raise RuntimeError("boom")

            def render(self, ctx):
                raise AssertionError

        nav, ctx = make_nav()
        run(nav.start())
        nav._stack.append(Exploding())
        run(nav.dispatch(ButtonEvent(Button.SELECT, Press.SHORT)))
        assert ctx.active_toast          # the rider is told, the loop survives


# ── Menus ───────────────────────────────────────────────────────────────────

class Sample(MenuView):
    def __init__(self):
        super().__init__()
        self.fired = []

    def build(self, ctx):
        async def hit(c):
            self.fired.append("a")
            return HANDLED
        return [MenuItem("A", action=hit), MenuItem("B"), MenuItem("C")]


class TestMenuView:
    def test_selection_wraps(self):
        menu, ctx = Sample(), AppContext(state=RideState(), settings=Settings())
        run(menu.handle(ButtonEvent(Button.UP, Press.SHORT), ctx))
        assert menu.selected == 2

    def test_select_runs_the_row_action(self):
        menu, ctx = Sample(), AppContext(state=RideState(), settings=Settings())
        run(menu.handle(ButtonEvent(Button.SELECT, Press.SHORT), ctx))
        assert menu.fired == ["a"]

    def test_held_select_pops(self):
        menu, ctx = Sample(), AppContext(state=RideState(), settings=Settings())
        result = run(menu.handle(ButtonEvent(Button.SELECT, Press.LONG), ctx))
        assert isinstance(result, Pop)

    def test_rows_without_actions_are_inert(self):
        menu, ctx = Sample(), AppContext(state=RideState(), settings=Settings())
        menu._refresh_items(ctx)
        menu.selected = 1
        assert run(menu.handle(ButtonEvent(Button.SELECT, Press.SHORT), ctx)) is HANDLED


class TestAdjustView:
    def test_up_increases_down_decreases(self):
        ctx = AppContext(state=RideState(), settings=Settings())
        ctx.settings.volume = 50

        async def apply(c, value):
            c.settings.volume = value

        view = AdjustView("Volume", get=lambda c: c.settings.volume,
                          apply=apply, step=5)

        run(view.handle(ButtonEvent(Button.UP, Press.SHORT), ctx))
        assert ctx.settings.volume == 55
        run(view.handle(ButtonEvent(Button.DOWN, Press.SHORT), ctx))
        assert ctx.settings.volume == 50

    def test_clamped_to_range(self):
        ctx = AppContext(state=RideState(), settings=Settings())
        ctx.settings.volume = 98

        async def apply(c, value):
            c.settings.volume = value

        view = AdjustView("Volume", get=lambda c: c.settings.volume,
                          apply=apply, step=5)
        run(view.handle(ButtonEvent(Button.UP, Press.SHORT), ctx))
        assert ctx.settings.volume == 100
