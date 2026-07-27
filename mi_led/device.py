"""BLE device connection and command transport for the MI Matrix Display."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Optional, Union

from bleak import BleakClient, BleakScanner

from . import protocol as proto
from .proxy_client import ProxyDevice
from .proxy_protocol import DEFAULT_PROXY_URL


StatusCallback = Callable[[str], None]
DebugCallback = Callable[[str], None]
Backend = Union["MiLedDevice", ProxyDevice]


def _scale_frame(
    picture: list[tuple[int, int, int]], factor: float
) -> list[tuple[int, int, int]]:
    factor = max(0.0, min(1.0, factor))
    return [(int(r * factor), int(g * factor), int(b * factor)) for r, g, b in picture]


class MiLedDevice:
    """Async BLE client for the Merkury MI Matrix Display."""

    def __init__(
        self,
        on_status: Optional[StatusCallback] = None,
        on_debug: Optional[DebugCallback] = None,
    ):
        self._client: Optional[BleakClient] = None
        self._address: Optional[str] = None
        self._name: Optional[str] = None
        self._graffiti_ready = False
        self._framebuffer: list[tuple[int, int, int]] = [(0, 0, 0)] * proto.PIXEL_COUNT
        self._on_status = on_status or (lambda _msg: None)
        self._on_debug = on_debug or (lambda _msg: None)
        self._lock = asyncio.Lock()
        self._quiet_tx = False

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

    def _debug(self, message: str) -> None:
        self._on_debug(message)

    async def find_device(self, scan_timeout: float = 10.0):
        self._status("Scanning for BLE devices...")
        self._debug(f"BLE scan start (timeout={scan_timeout}s)")
        devices = await BleakScanner.discover(timeout=scan_timeout)
        self._debug(f"BLE scan found {len(devices)} device(s)")

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
        self._framebuffer = [(0, 0, 0)] * proto.PIXEL_COUNT
        self._status(f"Connected to {self.device_label}")
        self._debug(f"GATT connected: {self.device_label}")
        return True

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        self._graffiti_ready = False
        self._framebuffer = [(0, 0, 0)] * proto.PIXEL_COUNT
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
        self._status("Disconnected")
        self._debug("GATT disconnected")

    async def _write(
        self, data: bytes | bytearray, delay: float = 0.02, *, label: str = ""
    ) -> None:
        async with self._lock:
            await self._write_locked(data, delay=delay, label=label)

    async def _write_locked(
        self, data: bytes | bytearray, delay: float = 0.02, *, label: str = ""
    ) -> None:
        """GATT write; caller must already hold ``self._lock``."""
        if not self.is_connected or self._client is None:
            raise RuntimeError("Not connected")
        payload = bytes(data)
        quiet = self._quiet_tx
        tag = f" {label}" if label else ""
        if not quiet:
            # Full hex dumps are expensive at animation rates — keep short.
            if len(payload) > 16:
                self._debug(f"BLE TX{tag}: {len(payload)} bytes")
            else:
                self._debug(f"BLE TX{tag}: {payload.hex()} ({len(payload)} bytes)")
        t0 = time.perf_counter()
        await self._client.write_gatt_char(proto.CHARACTERISTIC_UUID, payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if not quiet:
            self._debug(f"BLE TX ack{tag}: {elapsed_ms:.1f} ms")
        if delay > 0:
            await asyncio.sleep(delay)

    async def power_on(self) -> None:
        """
        Send the protocol wake command.

        Note: on this hardware, POWER_ON often reveals a stored gallery/slideshow
        image from the manufacturer app. Callers should immediately send_frame()
        afterward if they want to show live content instead.
        """
        await self._write(proto.POWER_ON, delay=0.05, label="power_on")
        self._status("Power on")

    async def power_off(self) -> None:
        """
        Blank the matrix by painting black.

        Do not use the raw POWER_OFF opcode here — on this panel it leaves live
        mode and restores the last image saved by the manufacturer app.
        """
        await self.clear_screen()
        # Reinforce: a single temp-frame write can be replaced by gallery content.
        await asyncio.sleep(0.05)
        await self.clear_screen()
        self._status("Power off")

    async def enter_graffiti_mode(self) -> None:
        """Switch to graffiti mode and repaint the software framebuffer."""
        async with self._lock:
            await self._enter_graffiti_locked(restore=True)

    async def _enter_graffiti_locked(self, *, restore: bool) -> None:
        """Enter graffiti mode. Caller must hold ``self._lock``."""
        self._debug("Entering graffiti mode")
        for i, cmd in enumerate(proto.GRAFFITI_INIT):
            await self._write_locked(cmd, delay=0.2, label=f"graffiti_init[{i}]")
        self._graffiti_ready = True
        if not restore:
            return
        # Mode switch clears the panel — replay lit pixels from our framebuffer.
        restored = 0
        for index, (r, g, b) in enumerate(self._framebuffer):
            if r == 0 and g == 0 and b == 0:
                continue
            await self._write_locked(
                proto.set_pixel_command(index, r, g, b),
                delay=0.002,
                label=f"restore[{index}]",
            )
            restored += 1
        self._debug(f"Graffiti restore wrote {restored} lit pixel(s)")

    async def set_pixel(
        self,
        x: int,
        y: int,
        r: int,
        g: int,
        b: int,
        canvas: list[tuple[int, int, int]] | None = None,
    ) -> None:
        index = proto.pixel_index(x, y)
        color = (r & 0xFF, g & 0xFF, b & 0xFF)
        async with self._lock:
            if canvas is not None:
                if len(canvas) != proto.PIXEL_COUNT:
                    raise ValueError(f"Canvas must have {proto.PIXEL_COUNT} pixels")
                self._framebuffer = [
                    (int(cr) & 0xFF, int(cg) & 0xFF, int(cb) & 0xFF) for cr, cg, cb in canvas
                ]
            else:
                self._framebuffer[index] = color
            if not self._graffiti_ready:
                # Restores framebuffer (including this pixel) after the clear.
                await self._enter_graffiti_locked(restore=True)
            else:
                await self._write_locked(
                    proto.set_pixel_command(index, *self._framebuffer[index]),
                    delay=0.003,
                    label=f"pixel({x},{y})",
                )

    async def send_frame(self, picture: list[tuple[int, int, int]]) -> None:
        """Push a full 16x16 RGB frame to the display."""
        if len(picture) != proto.PIXEL_COUNT:
            raise ValueError(f"Picture must have {proto.PIXEL_COUNT} pixels")
        frame = [(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF) for r, g, b in picture]
        async with self._lock:
            self._framebuffer = frame
            self._graffiti_ready = False
            self._debug("Sending full frame")
            # Suppress per-block TX spam — it floods the Tk thread during animation.
            self._quiet_tx = True
            t0 = time.perf_counter()
            try:
                for i, cmd in enumerate(proto.iter_full_frame_commands(frame)):
                    delay = 0.002 if i == 0 or i == proto.BLOCK_COUNT + 1 else 0.025
                    if i == 0:
                        label = "frame_start"
                    elif i == proto.BLOCK_COUNT + 1:
                        label = "frame_end"
                    else:
                        label = f"block[{i - 1}]"
                    await self._write_locked(cmd, delay=delay, label=label)
            finally:
                self._quiet_tx = False
            elapsed_ms = (time.perf_counter() - t0) * 1000
            # One summary line instead of ~20 TX/ack lines per frame.
            self._debug(f"Frame TX {elapsed_ms:.0f} ms")
        # Skip per-frame status updates — they marshal onto the Tk thread every tick.

    async def clear_screen(self) -> None:
        """Paint the matrix black (USB may still supply power)."""
        await self.send_frame([(0, 0, 0)] * proto.PIXEL_COUNT)
        self._status("Screen cleared")

    async def sync_framebuffer(self, picture: list[tuple[int, int, int]]) -> None:
        """Update the software framebuffer without sending (e.g. before graffiti)."""
        if len(picture) != proto.PIXEL_COUNT:
            raise ValueError(f"Picture must have {proto.PIXEL_COUNT} pixels")
        async with self._lock:
            self._framebuffer = [
                (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF) for r, g, b in picture
            ]

    async def fade_frame(
        self,
        picture: list[tuple[int, int, int]],
        *,
        to_black: bool,
        steps: int = 8,
        step_delay: float = 0.04,
    ) -> None:
        """Fade a frame toward black or up from black to the target colors."""
        steps = max(1, steps)
        if to_black:
            factors = [i / steps for i in range(steps - 1, -1, -1)]
        else:
            factors = [i / steps for i in range(1, steps + 1)]
        self._debug(f"Fade {'out' if to_black else 'in'} ({steps} steps)")
        for factor in factors:
            await self.send_frame(_scale_frame(picture, factor))
            if step_delay > 0:
                await asyncio.sleep(step_delay)


class DeviceController:
    """Runs MiLedDevice or ProxyDevice on a dedicated asyncio thread for Tkinter."""

    def __init__(
        self,
        on_status: Optional[StatusCallback] = None,
        on_debug: Optional[DebugCallback] = None,
        *,
        mode: str = "local",
        proxy_url: str = DEFAULT_PROXY_URL,
        proxy_token: Optional[str] = None,
    ):
        self._on_status = on_status or (lambda _msg: None)
        self._on_debug = on_debug or (lambda _msg: None)
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
                on_debug=self._forward_debug,
            )
        return MiLedDevice(on_status=self._forward_status, on_debug=self._forward_debug)

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

    def _forward_debug(self, message: str) -> None:
        self._on_debug(message)

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

    def set_pixel(
        self,
        x: int,
        y: int,
        r: int,
        g: int,
        b: int,
        canvas: list[tuple[int, int, int]] | None = None,
    ):
        return self.submit(self._device.set_pixel(x, y, r, g, b, canvas=canvas))

    def send_frame(self, picture: list[tuple[int, int, int]]):
        return self.submit(self._device.send_frame(picture))

    def enter_graffiti_mode(self):
        return self.submit(self._device.enter_graffiti_mode())

    def sync_framebuffer(self, picture: list[tuple[int, int, int]]):
        return self.submit(self._device.sync_framebuffer(picture))

    def clear_screen(self):
        return self.submit(self._device.clear_screen())

    def fade_frame(
        self,
        picture: list[tuple[int, int, int]],
        *,
        to_black: bool,
        steps: int = 8,
        step_delay: float = 0.04,
    ):
        return self.submit(
            self._device.fade_frame(
                picture, to_black=to_black, steps=steps, step_delay=step_delay
            )
        )

    def shutdown(self) -> None:
        try:
            fut = self.disconnect()
            fut.result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
