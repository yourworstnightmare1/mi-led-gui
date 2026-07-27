"""BLE device connection and command transport for the MI Matrix Display."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Optional, Union

from bleak import BleakClient, BleakScanner

from . import protocol as proto
from .proxy_client import ProxyDevice
from .proxy_protocol import DEFAULT_PROXY_URL


StatusCallback = Callable[[str], None]
Backend = Union["MiLedDevice", ProxyDevice]


class MiLedDevice:
    """Async BLE client for the Merkury MI Matrix Display."""

    def __init__(self, on_status: Optional[StatusCallback] = None):
        self._client: Optional[BleakClient] = None
        self._address: Optional[str] = None
        self._name: Optional[str] = None
        self._graffiti_ready = False
        self._on_status = on_status or (lambda _msg: None)
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def device_label(self) -> str:
        name = self._name or "MI Matrix Display"
        if self._address:
            return f"{name} ({self._address})"
        return name

    def _status(self, message: str) -> None:
        self._on_status(message)

    async def find_device(self, scan_timeout: float = 10.0):
        self._status("Scanning for BLE devices...")
        devices = await BleakScanner.discover(timeout=scan_timeout)

        for d in devices:
            if d.name and proto.DEVICE_NAME_HINT in d.name:
                self._status(f"Found by name: {d.name} [{d.address}]")
                return d

        self._status("Name not found; probing services...")
        for d in devices:
            self._status(f"Checking: {d.name or 'Unknown'} [{d.address}]")
            try:
                async with BleakClient(d, timeout=5.0) as client:
                    if not client.is_connected:
                        continue

                    services = client.services
                    if not services:
                        try:
                            services = await client.get_services()
                        except Exception:
                            services = []

                    for service in services:
                        if service.uuid.lower() == proto.SERVICE_UUID.lower():
                            self._status(f"Found by service UUID: {d.address}")
                            return d
            except Exception:
                continue

        return None

    async def connect(self, scan_timeout: float = 10.0) -> bool:
        if self.is_connected:
            self._status(f"Already connected to {self.device_label}")
            return True

        target = await self.find_device(scan_timeout=scan_timeout)
        if target is None:
            self._status("MI Matrix Display not found")
            return False

        self._status(f"Connecting to {target.name or 'device'} ({target.address})...")
        client = BleakClient(target)
        await client.connect()
        if not client.is_connected:
            self._status("Failed to connect")
            return False

        self._client = client
        self._address = target.address
        self._name = target.name
        self._graffiti_ready = False
        self._status(f"Connected to {self.device_label}")
        return True

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self._graffiti_ready = False
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
        self._status("Disconnected")

    async def _write(self, data: bytes | bytearray, delay: float = 0.02) -> None:
        if not self.is_connected or self._client is None:
            raise RuntimeError("Not connected")
        async with self._lock:
            await self._client.write_gatt_char(proto.CHARACTERISTIC_UUID, bytes(data))
            if delay > 0:
                await asyncio.sleep(delay)

    async def power_on(self) -> None:
        await self._write(proto.POWER_ON, delay=0.05)
        self._status("Power on")

    async def power_off(self) -> None:
        await self._write(proto.POWER_OFF, delay=0.05)
        self._status("Power off")

    async def enter_graffiti_mode(self) -> None:
        for cmd in proto.GRAFFITI_INIT:
            await self._write(cmd, delay=0.2)
        self._graffiti_ready = True

    async def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if not self._graffiti_ready:
            await self.enter_graffiti_mode()
        index = proto.pixel_index(x, y)
        await self._write(proto.set_pixel_command(index, r, g, b), delay=0.003)

    async def send_frame(self, picture: list[tuple[int, int, int]]) -> None:
        """Push a full 16x16 RGB frame to the display."""
        self._graffiti_ready = False
        for i, cmd in enumerate(proto.iter_full_frame_commands(picture)):
            # Start/end commands are short; blocks need a bit more settling time.
            delay = 0.002 if i == 0 or i == proto.BLOCK_COUNT + 1 else 0.025
            await self._write(cmd, delay=delay)
        self._status("Frame sent")


class DeviceController:
    """Runs MiLedDevice or ProxyDevice on a dedicated asyncio thread for Tkinter."""

    def __init__(
        self,
        on_status: Optional[StatusCallback] = None,
        *,
        mode: str = "local",
        proxy_url: str = DEFAULT_PROXY_URL,
        proxy_token: Optional[str] = None,
    ):
        self._on_status = on_status or (lambda _msg: None)
        self._mode = mode
        self._proxy_url = proxy_url
        self._proxy_token = proxy_token
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="mi-led-ble", daemon=True)
        self._device: Backend = self._make_backend()
        self._thread.start()

    def _make_backend(self) -> Backend:
        if self._mode == "proxy":
            return ProxyDevice(
                url=self._proxy_url,
                token=self._proxy_token,
                on_status=self._forward_status,
            )
        return MiLedDevice(on_status=self._forward_status)

    def configure(
        self,
        *,
        mode: str,
        proxy_url: str = DEFAULT_PROXY_URL,
        proxy_token: Optional[str] = None,
    ) -> None:
        """Switch local/proxy backend. Disconnects the previous backend."""
        old = self._device
        self._mode = mode
        self._proxy_url = proxy_url
        self._proxy_token = proxy_token
        self._device = self._make_backend()
        try:
            self.submit(old.disconnect())
        except Exception:
            pass

    @property
    def mode(self) -> str:
        return self._mode

    def _forward_status(self, message: str) -> None:
        self._on_status(message)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @property
    def is_connected(self) -> bool:
        return self._device.is_connected

    @property
    def device_label(self) -> str:
        return self._device.device_label

    def connect(self):
        return self.submit(self._device.connect())

    def disconnect(self):
        return self.submit(self._device.disconnect())

    def power_on(self):
        return self.submit(self._device.power_on())

    def power_off(self):
        return self.submit(self._device.power_off())

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int):
        return self.submit(self._device.set_pixel(x, y, r, g, b))

    def send_frame(self, picture: list[tuple[int, int, int]]):
        return self.submit(self._device.send_frame(picture))

    def enter_graffiti_mode(self):
        return self.submit(self._device.enter_graffiti_mode())

    def shutdown(self) -> None:
        try:
            fut = self.disconnect()
            fut.result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
