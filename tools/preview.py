"""
tools/preview.py — Render every screen to PNG without a Pi.

Layout mistakes on a 320x480 panel are much cheaper to find here than by
deploying and squinting at the bike.  Run from the repo root:

    python tools/preview.py [output_dir]

Writes one PNG per screen plus contact_sheet.png.  Imports only the UI
layer, so spidev/RPi.GPIO are never needed.
"""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image                                            # noqa: E402

from bikecomputer import config                                  # noqa: E402
from bikecomputer.ride import RideState                          # noqa: E402
from bikecomputer.settings import Settings                       # noqa: E402
from bikecomputer.ui import AppContext                           # noqa: E402
from bikecomputer.ui.views import (                              # noqa: E402
    BluetoothView, DetailView, DeviceView, MapView, MusicView,
    RideView, RootMenu, ScanView,
)
from bikecomputer.ui.views.menu import AdjustView, ConfirmView   # noqa: E402


# ── Stand-ins for the hardware-backed objects ───────────────────────────────

class FakeDevice:
    def __init__(self, name, address, kind="audio", connected=False,
                 paired=True, rssi=-58):
        self.display_name = name
        self.name = name
        self.address = address
        self.connected = connected
        self.paired = paired
        self.rssi = rssi
        self._kind = kind
        self.path = "/org/bluez/hci0/dev_" + address.replace(":", "_")

    @property
    def kind(self):
        return self._kind

    @property
    def kind_label(self):
        return {"hr": "Heart rate", "audio": "Audio"}.get(self._kind, "Device")


DEVICES = [
    FakeDevice("Soundcore Life Q30", "AA:BB:CC:11:22:33", "audio", connected=True),
    FakeDevice("Polar H10 8B2F1C", "DD:EE:FF:44:55:66", "hr", connected=True),
    FakeDevice("Wahoo CADENCE 3A9", "11:22:33:44:55:66", "cadence", paired=False,
               rssi=-79),
]


class FakeBt:
    available = True
    scanning = True
    last_error = ""

    async def is_powered(self):
        return True

    async def start_scan(self):
        return True, "Scanning..."

    async def stop_scan(self):
        return True, ""

    async def devices(self):
        return DEVICES

    async def device(self, address):
        return next((d for d in DEVICES if d.address == address), None)


class FakeLocal:
    def __init__(self):
        self.tracks = [type("T", (), {"label": f"Artist {i} - Track {i}"})()
                       for i in range(1, 24)]
        self.index = 3
        self.playing = True

    def status_text(self):
        return "Khruangbin - Time (You and I)"


class FakeMusic:
    playing = True
    source = "local"

    def __init__(self):
        self.local = FakeLocal()

    def now_playing(self):
        return "Khruangbin - Time (You and I)"

    def source_label(self):
        return "Local files"


# ── Scene setup ─────────────────────────────────────────────────────────────

def make_context() -> AppContext:
    state = RideState()
    state.has_fix = True
    state.speed = 8.42            # m/s ~ 18.8 mph
    state.lat, state.lon = 41.4993, -81.6944
    state.altitude = 199.0
    state.max_altitude = 264.0
    state.track = 47.0
    state.satellites = 11
    state.hdop = 0.9
    state.distance = 27_360.0     # m
    state.moving_time = 4_215.0
    state.elapsed_time = 4_890.0
    state.avg_speed = 6.49
    state.max_speed = 12.7
    state.heart_rate = 148
    state.cadence = 87

    state._paused = False

    settings = Settings()
    settings._path = Path(__file__).parent / "_preview_settings.json"
    settings.hr_device = "DD:EE:FF:44:55:66"
    settings.audio_device = "AA:BB:CC:11:22:33"

    ctx = AppContext(state=state, settings=settings, logger=None)
    ctx.music = FakeMusic()
    ctx.bt = FakeBt()
    ctx.audio_connected = True
    return ctx


async def render_all(out_dir: Path) -> list[tuple[str, Image.Image]]:
    ctx = make_context()
    frames: list[tuple[str, Image.Image]] = []

    async def shot(name: str, view, select: int = 0):
        await view.on_show(ctx)
        if hasattr(view, "selected"):
            view.selected = select
        img = view.render(ctx)
        frames.append((name, img))

    await shot("01_ride", RideView())

    searching = RideView()
    ctx.state.has_fix = False
    ctx.state.satellites = 3
    frames.append(("02_ride_searching", searching.render(ctx)))
    ctx.state.has_fix = True
    ctx.state.satellites = 11

    await shot("03_map", MapView(None))
    await shot("04_detail", DetailView())
    await shot("05_menu", RootMenu())
    await shot("06_music", MusicView(), select=1)
    await shot("07_bluetooth", BluetoothView(), select=2)
    await shot("08_scan", ScanView())
    await shot("09_device", DeviceView("DD:EE:FF:44:55:66", "Polar H10 8B2F1C"),
               select=1)

    volume = AdjustView("Volume", get=lambda c: c.settings.volume,
                        apply=_noop, unit="%")
    frames.append(("10_volume", volume.render(ctx)))

    confirm = ConfirmView("Reset trip", "Clear distance, time and averages?",
                          _noop_action)
    frames.append(("11_confirm", confirm.render(ctx)))

    # The navigator normally overlays toasts; do it by hand here.
    from PIL import ImageDraw
    from bikecomputer.ui import theme
    ctx.toast("Polar H10: Connected")
    toasted = RideView().render(ctx)
    theme.toast(ImageDraw.Draw(toasted), ctx.active_toast)
    frames.append(("12_toast", toasted))

    return frames


async def _noop(ctx, value):
    pass


async def _noop_action(ctx):
    return None


def contact_sheet(frames: list[tuple[str, Image.Image]]) -> Image.Image:
    cols = 6
    scale = 0.55
    tw = int(config.DISPLAY_WIDTH * scale)
    th = int(config.DISPLAY_HEIGHT * scale)
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 8) + 8, rows * (th + 8) + 8),
                      (20, 20, 20))
    for index, (_, img) in enumerate(frames):
        thumb = img.resize((tw, th), Image.LANCZOS)
        x = 8 + (index % cols) * (tw + 8)
        y = 8 + (index // cols) * (th + 8)
        sheet.paste(thumb, (x, y))
    return sheet


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = asyncio.run(render_all(out_dir))
    for name, img in frames:
        img.save(out_dir / f"{name}.png")
    contact_sheet(frames).save(out_dir / "contact_sheet.png")

    print(f"Wrote {len(frames)} screens + contact_sheet.png to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
