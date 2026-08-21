"""
sensors.py — BLE heart-rate and cadence over the BlueZ GATT D-Bus API.

Notifications are read straight from bluetoothd rather than through a
separate BLE stack.  Sharing bluetoothd's connection matters: the strap
is already connected (and reconnecting on its own) because Settings
trusted it, and a second library opening its own link would fight over
the same handle.

Both characteristics use the standard Bluetooth SIG layouts:
  Heart Rate Measurement (0x2A37) — flags byte, then 8- or 16-bit BPM
  CSC Measurement (0x2A5B)        — cumulative crank revs + event time,
                                    differentiated here into RPM
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

from . import config

log = logging.getLogger(__name__)

GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
OBJ_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
BLUEZ = "org.bluez"

_RESUBSCRIBE_INTERVAL = 5.0   # seconds between "is it back yet?" checks


class SensorHub:
    """
    Keeps `RideState.heart_rate` and `.cadence` fed from BLE sensors.

    Runs a supervisor loop that (re)subscribes whenever a remembered
    sensor turns up connected, so a strap that drops out mid-ride and
    reconnects starts reporting again with no intervention.
    """

    def __init__(self, bt, state, settings) -> None:
        self._bt = bt
        self._state = state
        self._settings = settings
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscribed: dict[str, set] = {}   # address -> set of char paths
        # Cadence needs the previous sample to differentiate.
        self._last_crank: Optional[tuple[int, int]] = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._supervise(), name="sensors")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── Supervisor ──────────────────────────────────────────────────────────

    async def _supervise(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("Sensor supervisor error: %s", exc)
            await asyncio.sleep(_RESUBSCRIBE_INTERVAL)

    async def _tick(self) -> None:
        self._expire_stale()

        if not self._bt.available:
            return

        wanted = [a for a in (self._settings.hr_device,) if a]
        if not wanted:
            return

        for address in wanted:
            device = await self._bt.device(address)
            if device is None or not device.connected:
                self._subscribed.pop(address.upper(), None)
                continue
            if address.upper() in self._subscribed:
                continue
            await self._subscribe(device)

    def _expire_stale(self) -> None:
        """Blank readings that have gone quiet, rather than showing a stale BPM."""
        now = time.monotonic()
        if (self._state.heart_rate is not None
                and now - self._state.hr_updated > config.HR_STALE_SECONDS):
            self._state.heart_rate = None
        if (self._state.cadence is not None
                and now - self._state.cadence_updated > config.HR_STALE_SECONDS):
            self._state.cadence = None
            self._last_crank = None

    # ── Subscription ────────────────────────────────────────────────────────

    async def _subscribe(self, device) -> None:
        bus = self._bt.bus                       # same connection on purpose
        if bus is None:
            return

        try:
            introspection = await bus.introspect(BLUEZ, "/")
            root = bus.get_proxy_object(BLUEZ, "/", introspection)
            managed = await root.get_interface(OBJ_MANAGER_IFACE).call_get_managed_objects()
        except Exception as exc:
            log.debug("Could not enumerate GATT objects: %s", exc)
            return

        targets = {
            config.UUID_HEART_RATE_MEASURE: self._on_heart_rate,
            config.UUID_CSC_MEASUREMENT:    self._on_cadence,
        }

        started: set = set()
        for path, ifaces in managed.items():
            props = ifaces.get(GATT_CHAR_IFACE)
            if not props or not path.startswith(device.path):
                continue

            uuid_variant = props.get("UUID")
            uuid = (uuid_variant.value if hasattr(uuid_variant, "value") else "").lower()
            handler = targets.get(uuid)
            if handler is None:
                continue

            if await self._start_notify(bus, path, handler):
                started.add(path)
                log.info("Subscribed to %s on %s", uuid[4:8], device.display_name)

        if started:
            self._subscribed[device.address.upper()] = started
        else:
            log.debug("No known sensor characteristics on %s", device.display_name)

    async def _start_notify(self, bus, path: str, handler) -> bool:
        try:
            introspection = await bus.introspect(BLUEZ, path)
            obj = bus.get_proxy_object(BLUEZ, path, introspection)
            char = obj.get_interface(GATT_CHAR_IFACE)
            props = obj.get_interface(PROPS_IFACE)

            def on_changed(iface: str, changed: dict, invalidated: list) -> None:
                if iface != GATT_CHAR_IFACE:
                    return
                value = changed.get("Value")
                if value is None:
                    return
                raw = value.value if hasattr(value, "value") else value
                try:
                    handler(bytes(raw))
                except Exception as exc:
                    log.debug("Sensor decode error: %s", exc)

            props.on_properties_changed(on_changed)
            await char.call_start_notify()
            return True
        except Exception as exc:
            log.debug("StartNotify failed on %s: %s", path, exc)
            return False

    # ── Decoders ────────────────────────────────────────────────────────────

    def _on_heart_rate(self, data: bytes) -> None:
        if len(data) < 2:
            return
        flags = data[0]
        if flags & 0x01:                        # 16-bit BPM
            if len(data) < 3:
                return
            bpm = int.from_bytes(data[1:3], "little")
        else:
            bpm = data[1]
        if 0 < bpm < 250:
            self._state.heart_rate = bpm
            self._state.hr_updated = time.monotonic()

    def _on_cadence(self, data: bytes) -> None:
        if len(data) < 1:
            return
        flags = data[0]
        offset = 1
        if flags & 0x01:                        # wheel revolution data present
            offset += 6
        if not flags & 0x02:                    # no crank data — nothing to do
            return
        if len(data) < offset + 4:
            return

        revs = int.from_bytes(data[offset:offset + 2], "little")
        event = int.from_bytes(data[offset + 2:offset + 4], "little")

        if self._last_crank is not None:
            prev_revs, prev_event = self._last_crank
            d_revs = (revs - prev_revs) & 0xFFFF
            d_time = (event - prev_event) & 0xFFFF   # units of 1/1024 s
            if d_time > 0:
                rpm = d_revs * 1024 * 60 / d_time
                if 0 <= rpm < 250:
                    self._state.cadence = int(round(rpm))
                    self._state.cadence_updated = time.monotonic()

        self._last_crank = (revs, event)
