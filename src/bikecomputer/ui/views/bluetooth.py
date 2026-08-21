"""
ui/views/bluetooth.py — Pair and manage Bluetooth devices from the bars.

Three levels: the Bluetooth menu (power + known devices + scan), the scan
list, and a per-device action sheet.  Device lists are refreshed by tick()
into a cache rather than fetched during render, because a D-Bus round trip
inside the render loop would stall the frame.
"""

from __future__ import annotations
from typing import Optional

from ... import config
from ...bluetooth import KIND_AUDIO, KIND_HR
from ..nav import Action, AppContext, HANDLED, Pop, Push
from .menu import ConfirmView, MenuItem, MenuView


def _signal(rssi: Optional[int]) -> str:
    if rssi is None:
        return ""
    if rssi > -60:
        return "•••"
    if rssi > -75:
        return "••"
    return "•"


class BluetoothView(MenuView):
    title = "Bluetooth"
    hint_text = "SELECT: open   hold: back"
    refresh_interval = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._devices: list = []
        self._powered = False

    async def on_show(self, ctx: AppContext) -> None:
        await self.tick(ctx)

    async def on_hide(self, ctx: AppContext) -> None:
        # Discovery is expensive on battery and keeps the radio busy;
        # never leave it running once the rider navigates away.
        if ctx.bt and ctx.bt.scanning:
            await ctx.bt.stop_scan()

    async def tick(self, ctx: AppContext) -> None:
        if ctx.bt is None:
            return
        self._powered = await ctx.bt.is_powered()
        self._devices = [d for d in await ctx.bt.devices() if d.paired]

    def subtitle(self, ctx: AppContext) -> str:
        if ctx.bt is None or not ctx.bt.available:
            return "unavailable"
        return "on" if self._powered else "off"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        if ctx.bt is None or not ctx.bt.available:
            return [MenuItem("Bluetooth unavailable",
                             value=(ctx.bt.last_error[:18] if ctx.bt else ""))]

        items = [
            MenuItem("Bluetooth",
                     value=lambda c: "On" if self._powered else "Off",
                     action=self._toggle_power),
            MenuItem("Add a device", value="scan", action=_scan),
        ]

        for device in self._devices:
            items.append(MenuItem(
                device.display_name,
                value=self._device_value(ctx, device),
                action=_open_device(device.address),
            ))

        if not self._devices:
            items.append(MenuItem("No paired devices", value=""))

        items.append(MenuItem("Auto-connect on start",
                              value=lambda c: "On" if c.settings.auto_connect else "Off",
                              action=_toggle_auto_connect))
        return items

    def _device_value(self, ctx: AppContext, device) -> str:
        address = device.address.upper()
        roles = []
        if address and address == ctx.settings.hr_device.upper():
            roles.append("HR")
        if address and address == ctx.settings.audio_device.upper():
            roles.append("Audio")
        status = "connected" if device.connected else "saved"
        return f"{'/'.join(roles)} {status}".strip()

    async def _toggle_power(self, ctx: AppContext) -> Optional[Action]:
        ok, message = await ctx.bt.set_powered(not self._powered)
        ctx.toast(message)
        await self.tick(ctx)
        return HANDLED


class ScanView(MenuView):
    """Live discovery results, newest signal first."""

    title = "Add a device"
    hint_text = "SELECT: pair   hold: back"
    empty_text = "Put the device in pairing mode"
    refresh_interval = 1.5

    def __init__(self) -> None:
        super().__init__()
        self._devices: list = []
        self._pairing = ""

    async def on_show(self, ctx: AppContext) -> None:
        if ctx.bt is None:
            return
        ok, message = await ctx.bt.start_scan()
        if not ok:
            ctx.toast(message)
        await self.tick(ctx)

    async def on_hide(self, ctx: AppContext) -> None:
        if ctx.bt:
            await ctx.bt.stop_scan()

    async def tick(self, ctx: AppContext) -> None:
        if ctx.bt is None:
            return
        # Unpaired only: paired devices are managed from the level above.
        self._devices = [d for d in await ctx.bt.devices()
                         if not d.paired and d.name]

    def subtitle(self, ctx: AppContext) -> str:
        if self._pairing:
            return "pairing..."
        return "scanning" if (ctx.bt and ctx.bt.scanning) else ""

    def build(self, ctx: AppContext) -> list[MenuItem]:
        items = []
        for device in self._devices:
            items.append(MenuItem(
                device.display_name,
                value=f"{device.kind_label} {_signal(device.rssi)}".strip(),
                action=self._pair(device.address, device.display_name),
            ))
        return items

    def _pair(self, address: str, name: str):
        async def action(ctx: AppContext) -> Optional[Action]:
            self._pairing = name
            ctx.toast(f"Pairing {name}...", seconds=20)
            await ctx.bt.stop_scan()
            ok, message = await ctx.bt.pair(address)
            self._pairing = ""
            ctx.toast(f"{name}: {message}")
            if not ok:
                await ctx.bt.start_scan()
                return HANDLED

            # A freshly paired device almost always wants a role assigned,
            # so go straight to its action sheet instead of back to a list.
            device = await ctx.bt.device(address)
            if device is not None:
                _auto_assign(ctx, device)
            return Push(DeviceView(address, name))
        return action


class DeviceView(MenuView):
    """Per-device actions: connect, assign a role, forget."""

    hint_text = "SELECT: activate   hold: back"
    refresh_interval = 2.0

    def __init__(self, address: str, name: str) -> None:
        super().__init__()
        self.title = name[:22]
        self._address = address
        self._device = None

    async def on_show(self, ctx: AppContext) -> None:
        await self.tick(ctx)

    async def tick(self, ctx: AppContext) -> None:
        if ctx.bt:
            self._device = await ctx.bt.device(self._address)

    def subtitle(self, ctx: AppContext) -> str:
        if self._device is None:
            return ""
        return "connected" if self._device.connected else "offline"

    def build(self, ctx: AppContext) -> list[MenuItem]:
        device = self._device
        if device is None:
            return [MenuItem("Device not found", value=self._address)]

        items: list[MenuItem] = []

        if device.connected:
            items.append(MenuItem("Disconnect", action=self._disconnect))
        else:
            items.append(MenuItem("Connect", action=self._connect))

        is_hr = self._address.upper() == ctx.settings.hr_device.upper()
        is_audio = self._address.upper() == ctx.settings.audio_device.upper()

        items.append(MenuItem(
            "Use as heart rate",
            value="Yes" if is_hr else "No",
            action=self._assign("hr_device", is_hr),
        ))
        items.append(MenuItem(
            "Use as audio out",
            value="Yes" if is_audio else "No",
            action=self._assign("audio_device", is_audio),
        ))
        items.append(MenuItem("Address", value=self._address))
        items.append(MenuItem("Forget this device", action=self._forget,
                              colour=config.CLR_ERR))
        return items

    async def _connect(self, ctx: AppContext) -> Optional[Action]:
        ctx.toast("Connecting...", seconds=15)
        ok, message = await ctx.bt.connect(self._address)
        ctx.toast(message)
        await self.tick(ctx)
        return HANDLED

    async def _disconnect(self, ctx: AppContext) -> Optional[Action]:
        ok, message = await ctx.bt.disconnect(self._address)
        ctx.toast(message)
        await self.tick(ctx)
        return HANDLED

    def _assign(self, field: str, currently: bool):
        async def action(ctx: AppContext) -> Optional[Action]:
            ctx.settings.set(field, "" if currently else self._address)
            label = "heart rate" if field == "hr_device" else "audio output"
            ctx.toast(f"{'Cleared' if currently else 'Set as'} {label}")
            return HANDLED
        return action

    async def _forget(self, ctx: AppContext) -> Optional[Action]:
        async def confirmed(c: AppContext) -> Optional[Action]:
            ok, message = await c.bt.forget(self._address)
            for field in ("hr_device", "audio_device"):
                if getattr(c.settings, field).upper() == self._address.upper():
                    c.settings.set(field, "")
            c.toast(message)
            # Close the dialog and the device sheet behind it: the device
            # it describes no longer exists.
            return Pop(levels=2)

        return Push(ConfirmView("Forget device",
                                f"Remove {self.title}?", confirmed))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _auto_assign(ctx: AppContext, device) -> None:
    """
    Fill an empty role automatically after pairing.

    A strap that advertises the heart-rate service has exactly one use, so
    asking would be busywork.  An already-assigned role is never
    overwritten — that stays a deliberate choice in the device sheet.
    """
    if device.kind == KIND_HR and not ctx.settings.hr_device:
        ctx.settings.set("hr_device", device.address)
        ctx.toast(f"{device.display_name} set as heart rate")
    elif device.kind == KIND_AUDIO and not ctx.settings.audio_device:
        ctx.settings.set("audio_device", device.address)
        ctx.toast(f"{device.display_name} set as audio out")


async def _scan(ctx: AppContext) -> Optional[Action]:
    return Push(ScanView())


def _open_device(address: str):
    async def action(ctx: AppContext) -> Optional[Action]:
        device = await ctx.bt.device(address)
        name = device.display_name if device else address
        return Push(DeviceView(address, name))
    return action


async def _toggle_auto_connect(ctx: AppContext) -> Optional[Action]:
    value = ctx.settings.toggle("auto_connect")
    ctx.toast(f"Auto-connect {'on' if value else 'off'}")
    return HANDLED
