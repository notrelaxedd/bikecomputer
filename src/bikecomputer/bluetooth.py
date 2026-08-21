"""
bluetooth.py — BlueZ control over D-Bus.

Talks to bluetoothd directly rather than shelling out to bluetoothctl,
whose output format is interactive and unstable to parse.  Everything is
async so scanning and pairing never block the render loop.

A NoInputNoOutput pairing agent is registered on startup.  Without one,
bluetoothd has nowhere to send a passkey request and pairing fails on a
headless box; with one, "just works" pairing — which is what heart-rate
straps and Bluetooth headphones use — completes unattended.
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from . import config

log = logging.getLogger(__name__)

try:
    from dbus_fast import BusType, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method
    _DBUS_AVAILABLE = True
except ImportError:                     # dev machine, or dbus-fast not installed
    _DBUS_AVAILABLE = False
    MessageBus = None                   # type: ignore
    Variant = ()                        # type: ignore
    ServiceInterface = object           # type: ignore

    def method(*a, **kw):               # type: ignore
        def deco(fn):
            return fn
        return deco


BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
OBJ_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

AGENT_PATH = "/org/bikecomputer/agent"


# ── Device model ────────────────────────────────────────────────────────────

KIND_HR = "hr"
KIND_AUDIO = "audio"
KIND_CADENCE = "cadence"
KIND_OTHER = "other"

_KIND_LABEL = {
    KIND_HR:      "Heart rate",
    KIND_AUDIO:   "Audio",
    KIND_CADENCE: "Cadence",
    KIND_OTHER:   "Device",
}

_AUDIO_ICONS = {"audio-headset", "audio-headphones", "audio-card", "audio-speakers"}


@dataclass
class BtDevice:
    address: str
    name: str = ""
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    rssi: Optional[int] = None
    icon: str = ""
    uuids: tuple[str, ...] = ()
    path: str = ""

    @property
    def kind(self) -> str:
        lowered = set(self.uuids)
        if config.UUID_HEART_RATE_SERVICE in lowered:
            return KIND_HR
        if config.UUID_CYCLING_CADENCE_SVC in lowered:
            return KIND_CADENCE
        if config.UUID_A2DP_SINK in lowered or self.icon in _AUDIO_ICONS:
            return KIND_AUDIO
        return KIND_OTHER

    @property
    def kind_label(self) -> str:
        return _KIND_LABEL[self.kind]

    @property
    def display_name(self) -> str:
        return self.name or self.address


# ── Pairing agent ───────────────────────────────────────────────────────────

class _PairingAgent(ServiceInterface):
    """Accepts every pairing request; there is no keypad to type a PIN on."""

    def __init__(self) -> None:
        super().__init__(AGENT_IFACE)

    @method()
    def Release(self):
        log.debug("Pairing agent released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: F821, N802
        log.info("PIN requested for %s - replying 0000", device)
        return "0000"

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: F821, N802
        log.info("Passkey requested for %s - replying 0", device)
        return 0

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821, N802
        log.info("Pairing %s, PIN %s", device, pincode)

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821, N802
        log.info("Pairing %s, passkey %06d", device, passkey)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821, N802
        log.info("Auto-confirming pairing with %s", device)

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: F821, N802
        log.info("Auto-authorising %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821, N802
        log.info("Auto-authorising service %s on %s", uuid, device)

    @method()
    def Cancel(self):
        log.debug("Pairing cancelled by bluetoothd")


# ── Manager ─────────────────────────────────────────────────────────────────

class BluetoothManager:
    """
    Thin async wrapper over the BlueZ D-Bus API.

    Every action coroutine returns an (ok, message) pair so the UI can show
    the failure reason without needing to know anything about D-Bus.
    """

    def __init__(self, adapter: str = config.BT_ADAPTER) -> None:
        self._adapter_name = adapter
        self._adapter_path = "/org/bluez/" + adapter
        self._bus = None
        self._agent: Optional[_PairingAgent] = None
        self._scanning = False
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._bus is not None

    @property
    def bus(self):
        """The shared system bus, for GATT subscriptions in sensors.py."""
        return self._bus

    @property
    def scanning(self) -> bool:
        return self._scanning

    # ── Connection ──────────────────────────────────────────────────────────

    async def start(self) -> bool:
        if not _DBUS_AVAILABLE:
            self.last_error = "dbus-fast not installed"
            log.warning("Bluetooth disabled: %s", self.last_error)
            return False
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            await self._register_agent()
            await self.set_powered(True)
            log.info("Bluetooth ready on %s", self._adapter_name)
            return True
        except Exception as exc:
            self._bus = None
            self.last_error = str(exc)
            log.warning("Bluetooth unavailable: %s", exc)
            return False

    async def stop(self) -> None:
        if self._bus is None:
            return
        if self._scanning:
            try:
                await self.stop_scan()
            except Exception:
                pass
        try:
            self._bus.disconnect()
        except Exception:
            pass
        self._bus = None

    async def _register_agent(self) -> None:
        self._agent = _PairingAgent()
        self._bus.export(AGENT_PATH, self._agent)
        mgr = await self._interface("/org/bluez", AGENT_MANAGER_IFACE)
        await mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
        try:
            await mgr.call_request_default_agent(AGENT_PATH)
        except Exception as exc:
            # Another agent (an interactive bluetoothctl, say) holds the
            # default slot.  Ours still serves pairings we initiate.
            log.debug("Could not become default agent: %s", exc)

    async def _interface(self, path: str, iface: str):
        introspection = await self._bus.introspect(BLUEZ, path)
        obj = self._bus.get_proxy_object(BLUEZ, path, introspection)
        return obj.get_interface(iface)

    # ── Adapter ─────────────────────────────────────────────────────────────

    async def is_powered(self) -> bool:
        if not self.available:
            return False
        try:
            adapter = await self._interface(self._adapter_path, ADAPTER_IFACE)
            return bool(await adapter.get_powered())
        except Exception:
            return False

    async def set_powered(self, on: bool) -> tuple[bool, str]:
        if not self.available:
            return False, "Bluetooth unavailable"
        try:
            adapter = await self._interface(self._adapter_path, ADAPTER_IFACE)
            await adapter.set_powered(on)
            return True, "Bluetooth on" if on else "Bluetooth off"
        except Exception as exc:
            return False, _short(exc)

    async def start_scan(self) -> tuple[bool, str]:
        if not self.available:
            return False, "Bluetooth unavailable"
        if self._scanning:
            return True, "Already scanning"
        try:
            adapter = await self._interface(self._adapter_path, ADAPTER_IFACE)
            await adapter.call_start_discovery()
            self._scanning = True
            return True, "Scanning..."
        except Exception as exc:
            return False, _short(exc)

    async def stop_scan(self) -> tuple[bool, str]:
        if not self.available or not self._scanning:
            self._scanning = False
            return True, ""
        try:
            adapter = await self._interface(self._adapter_path, ADAPTER_IFACE)
            await adapter.call_stop_discovery()
        except Exception as exc:
            log.debug("StopDiscovery failed: %s", exc)
        finally:
            self._scanning = False
        return True, ""

    # ── Devices ─────────────────────────────────────────────────────────────

    async def devices(self) -> list[BtDevice]:
        """Every device bluetoothd knows about — paired or merely seen."""
        if not self.available:
            return []
        try:
            introspection = await self._bus.introspect(BLUEZ, "/")
            root = self._bus.get_proxy_object(BLUEZ, "/", introspection)
            om = root.get_interface(OBJ_MANAGER_IFACE)
            managed = await om.call_get_managed_objects()
        except Exception as exc:
            log.debug("GetManagedObjects failed: %s", exc)
            return []

        found: list[BtDevice] = []
        for path, ifaces in managed.items():
            props = ifaces.get(DEVICE_IFACE)
            if not props or not path.startswith(self._adapter_path):
                continue
            found.append(_device_from_props(path, props))

        # Connected first, then paired, then strongest signal.
        found.sort(key=lambda d: (not d.connected, not d.paired,
                                  -(d.rssi if d.rssi is not None else -999)))
        return found

    async def device(self, address: str) -> Optional[BtDevice]:
        wanted = address.upper()
        for dev in await self.devices():
            if dev.address.upper() == wanted:
                return dev
        return None

    def _path_for(self, address: str) -> str:
        return self._adapter_path + "/dev_" + address.upper().replace(":", "_")

    async def pair(self, address: str) -> tuple[bool, str]:
        """
        Pair, trust, and connect in one step — that is always the intent.

        Pairing is best-effort, and a failure here is not fatal.  Plenty of
        BLE sensors (heart-rate straps especially) expose their GATT
        services without bonding at all, and BlueZ's Pair() answers
        AuthenticationFailed or NotSupported for them while a plain
        Connect() works perfectly.  The connection is therefore the
        verdict; the pairing error is only reported if the connect fails
        too, since that is when it actually explains something.
        """
        if not self.available:
            return False, "Bluetooth unavailable"
        try:
            dev = await self._interface(self._path_for(address), DEVICE_IFACE)
        except Exception:
            return False, "Device not found"

        pair_error = ""
        try:
            if not await dev.get_paired():
                await asyncio.wait_for(dev.call_pair(),
                                       timeout=config.BT_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            pair_error = "Pairing timed out"
        except Exception as exc:
            msg = _short(exc)
            if "AlreadyExists" not in msg:
                pair_error = msg
        if pair_error:
            log.info("Pair() failed for %s (%s) - trying to connect anyway",
                     address, pair_error)

        # Trusting is what lets the device reconnect on its own later.
        try:
            await dev.set_trusted(True)
        except Exception as exc:
            log.debug("Could not trust %s: %s", address, exc)

        ok, message = await self.connect(address)
        if ok:
            return True, message
        return False, pair_error or message

    async def connect(self, address: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Bluetooth unavailable"
        try:
            dev = await self._interface(self._path_for(address), DEVICE_IFACE)
            await asyncio.wait_for(dev.call_connect(),
                                   timeout=config.BT_CONNECT_TIMEOUT)
            return True, "Connected"
        except asyncio.TimeoutError:
            return False, "Connect timed out"
        except Exception as exc:
            return False, _short(exc)

    async def disconnect(self, address: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Bluetooth unavailable"
        try:
            dev = await self._interface(self._path_for(address), DEVICE_IFACE)
            await dev.call_disconnect()
            return True, "Disconnected"
        except Exception as exc:
            return False, _short(exc)

    async def forget(self, address: str) -> tuple[bool, str]:
        """Remove the pairing entirely."""
        if not self.available:
            return False, "Bluetooth unavailable"
        try:
            adapter = await self._interface(self._adapter_path, ADAPTER_IFACE)
            await adapter.call_remove_device(self._path_for(address))
            return True, "Forgotten"
        except Exception as exc:
            return False, _short(exc)

    async def connect_remembered(self, addresses: list[str]) -> None:
        """Best-effort reconnect of saved devices at startup."""
        for address in addresses:
            if not address:
                continue
            dev = await self.device(address)
            if dev is None:
                log.info("Remembered device %s not known to BlueZ yet", address)
                continue
            if dev.connected:
                continue
            ok, msg = await self.connect(address)
            log.info("Auto-connect %s (%s): %s", dev.display_name, address, msg)


def _device_from_props(path: str, props: dict) -> BtDevice:
    def val(key, default=None):
        variant = props.get(key)
        return variant.value if hasattr(variant, "value") else default

    return BtDevice(
        address=val("Address", "") or "",
        name=val("Alias") or val("Name") or "",
        paired=bool(val("Paired", False)),
        trusted=bool(val("Trusted", False)),
        connected=bool(val("Connected", False)),
        rssi=val("RSSI"),
        icon=val("Icon", "") or "",
        uuids=tuple(u.lower() for u in (val("UUIDs", []) or [])),
        path=path,
    )


def _short(exc: Exception) -> str:
    """Strip the D-Bus error prefix so the message fits a 320 px screen."""
    text = str(exc)
    for prefix in ("org.bluez.Error.", "org.freedesktop.DBus.Error."):
        text = text.replace(prefix, "")
    return text[:60] or "Failed"
