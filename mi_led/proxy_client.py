"""WebSocket client that talks to a remote BLE proxy as if it were local."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Optional

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .proxy_protocol import DEFAULT_PROXY_URL, dumps, loads


StatusCallback = Callable[[str], None]


class ProxyDevice:
    """
    Drop-in async stand-in for MiLedDevice that forwards commands to a proxy host.

    The proxy machine (Mac or Windows) owns the real BLE connection.
    """

    def __init__(
        self,
        url: str = DEFAULT_PROXY_URL,
        token: Optional[str] = None,
        on_status: Optional[StatusCallback] = None,
    ):
        self.url = url
        self.token = token
        self._on_status = on_status or (lambda _msg: None)
        self._ws = None
        self._connected = False
        self._label = "Remote proxy"
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def device_label(self) -> str:
        return self._label

    def _status(self, message: str) -> None:
        self._on_status(message)

    async def _ensure_socket(self) -> None:
        if self._ws is not None:
            return

        self._status(f"Connecting to proxy {self.url}...")
        self._ws = await ws_connect(self.url, open_timeout=10, max_size=2**20)
        self._recv_task = asyncio.create_task(self._recv_loop())

        if self.token:
            await self._request("auth", token=self.token)

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = loads(raw)
                except Exception:
                    continue

                msg_type = msg.get("type")
                if msg_type == "status":
                    self._status(f"[proxy] {msg.get('message', '')}")
                    continue
                if msg_type == "hello":
                    self._label = msg.get("label") or self._label
                    self._status(
                        f"Proxy hello (v{msg.get('version', '?')}); "
                        f"BLE={'up' if msg.get('ble_connected') else 'down'}"
                    )
                    continue
                if msg_type == "event" and msg.get("event") == "disconnected":
                    self._connected = False
                    self._status("Remote BLE disconnected")
                    continue

                req_id = msg.get("id")
                if req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if msg.get("ok"):
                            fut.set_result(msg.get("result"))
                        else:
                            fut.set_exception(RuntimeError(msg.get("error") or "proxy error"))
        except ConnectionClosed:
            self._status("Proxy connection closed")
        finally:
            self._connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Proxy connection closed"))
            self._pending.clear()
            self._ws = None

    async def _request(self, cmd: str, **fields: Any) -> Any:
        async with self._lock:
            await self._ensure_socket()
            assert self._ws is not None
            self._req_id += 1
            req_id = self._req_id
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut
            payload = {"id": req_id, "cmd": cmd, **fields}
            await self._ws.send(dumps(payload))

        try:
            return await asyncio.wait_for(fut, timeout=60)
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def connect(self, scan_timeout: float = 10.0) -> bool:
        _ = scan_timeout
        result = await self._request("connect")
        self._connected = bool(result and result.get("connected"))
        if result and result.get("label"):
            self._label = f"{result['label']} via proxy"
        if self._connected:
            self._status(f"Connected via proxy to {self._label}")
        else:
            self._status("Proxy could not connect to display")
        return self._connected

    async def disconnect(self) -> None:
        try:
            if self._ws is not None:
                await asyncio.wait_for(self._request("disconnect"), timeout=5)
        except Exception:
            pass
        self._connected = False

        recv = self._recv_task
        ws = self._ws
        self._recv_task = None
        self._ws = None

        if recv is not None:
            recv.cancel()
            try:
                await recv
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self._status("Disconnected from proxy")

    async def power_on(self) -> None:
        await self._request("power_on")
        self._status("Power on")

    async def power_off(self) -> None:
        await self._request("power_off")
        self._status("Power off")

    async def enter_graffiti_mode(self) -> None:
        await self._request("enter_graffiti")

    async def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        await self._request("set_pixel", x=x, y=y, r=r, g=g, b=b)

    async def send_frame(self, picture: list[tuple[int, int, int]]) -> None:
        pixels = [[int(r), int(g), int(b)] for r, g, b in picture]
        await self._request("send_frame", pixels=pixels)
        self._status("Frame sent")

    async def ping(self) -> dict[str, Any]:
        return await self._request("ping")
