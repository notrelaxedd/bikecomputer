"""
ui/views/detail.py — Secondary numbers: altitude, extremes, times, position.

Portrait gives room for one stat per row at a readable size, which suits
this screen better than the cramped grid the landscape layout needed.
"""

from __future__ import annotations

from PIL import Image

from ... import config, units
from ..nav import AppContext, View
from .. import theme

W = theme.W
H = theme.H


class DetailView(View):
    name = "detail"
    fps = config.DATA_FPS

    def render(self, ctx: AppContext) -> Image.Image:
        img, draw = theme.new_frame()
        theme.status_bar(draw, ctx)
        top = theme.header(draw, "Ride detail")

        state = ctx.state
        metric = ctx.metric
        alt_unit = units.altitude_unit(metric)
        speed_unit = units.speed_unit(metric)

        rows = [
            ("ALTITUDE", units.fmt_altitude(state.altitude, metric), alt_unit),
            ("MAX ALTITUDE", units.fmt_altitude(state.max_altitude, metric), alt_unit),
            ("AVG SPEED", units.fmt_speed(state.avg_speed, metric), speed_unit),
            ("MAX SPEED", units.fmt_speed(state.max_speed, metric), speed_unit),
            ("MOVING TIME", units.fmt_duration(state.moving_time), ""),
            ("ELAPSED", units.fmt_duration(state.elapsed_time), ""),
        ]

        y = top + 8
        for label, value, unit in rows:
            theme.stat_cell(draw, theme.PAD, y, W - 2 * theme.PAD,
                            label, value, unit)
            y += 60
            draw.line([theme.PAD, y - 6, W - theme.PAD, y - 6], fill=(32, 32, 32))

        if state.lat is not None:
            coords = f"{state.lat:.5f}, {state.lon:.5f}"
        else:
            coords = "No GPS fix"
        theme.centred(draw, y + 4, coords, theme.font(15), config.CLR_DIM)

        theme.hint(draw, "UP/DOWN: screen   hold SELECT: menu")
        return img
