"""WebSocket BLE proxy server — runs on the machine that owns Bluetooth."""

from __future__ import annotations

import argparse
import asyncio
import socket
from typing import Any, Optional

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .device import MiLedDevice
from .proxy_protocol import DEFAULT_PROXY_HOST, DEFAULT_PROXY_PORT, PROTOCOL_VERSION, dumps, loads


class BleProxyServer:
    """
    Exposes a local MiLedDevice over WebSocket so another PC can control it.

    Works in either direction:
      Mac BLE host  ← Windows GUI client
      Windows BLE host ← Mac GUI client
    """

    def __init__(
        self,
        host: str = DEFAULT_PROXY_HOST,
        port: int = DEFAULT_PROXY_PORT,
        token: Optional[str] = None,
        auto_connect_ble: bool = True,
    ):
        self.host = host
        self.port = port
        self.token = token
        self.auto_connect_ble = auto_connect_ble
        self._device = MiLedDevice(on_status=self._on_device_status)
        self._clients: set = set()
        self._lock = asyncio.Lock()
        self._stop: asyncio.Event | None = None

    def _on_device_status(self, message: str) -> None:
        print(f"[ble] {message}")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast({"type": "status", "message": message}))

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        raw = dumps(payload)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _reply(self, ws, req_id: Any, *, ok: bool, result: Any = None, error: str | None = None):
        payload: dict[str, Any] = {"id": req_id, "ok": ok}
        if ok:
            payload["result"] = result
        else:
            payload["error"] = error or "unknown error"
        await ws.send(dumps(payload))

    async def _handle_command(self, cmd: str, msg: dict[str, Any]) -> Any:
        if cmd == "ping":
            return {"pong": True, "version": PROTOCOL_VERSION}
        if cmd == "status":
            return {
                "connected": self._device.is_connected,
                "label": self._device.device_label,
                "version": PROTOCOL_VERSION,
            }
        if cmd == "connect":
            ok = await self._device.connect()
            return {"connected": ok, "label": self._device.device_label}
        if cmd == "disconnect":
            await self._device.disconnect()
            return {"connected": False}
        if cmd == "power_on":
            await self._device.power_on()
            return {"power": "on"}
        if cmd == "power_off":
            await self._device.power_off()
            return {"power": "off"}
        if cmd == "enter_graffiti":
            await self._device.enter_graffiti_mode()
            return {"mode": "graffiti"}
        if cmd == "sync_framebuffer":
            pixels = msg.get("pixels")
            if not isinstance(pixels, list) or len(pixels) != 256:
                raise ValueError("pixels must be a list of 256 [r,g,b] triples")
            picture = [tuple(int(c) for c in px) for px in pixels]
            await self._device.sync_framebuffer(picture)
            return {"synced": True}
        if cmd == "set_pixel":
            canvas = msg.get("canvas")
            canvas_tuples = None
            if canvas is not None:
                if not isinstance(canvas, list) or len(canvas) != 256:
                    raise ValueError("canvas must be a list of 256 [r,g,b] triples")
                canvas_tuples = [tuple(int(c) for c in px) for px in canvas]
            await self._device.set_pixel(
                int(msg["x"]),
                int(msg["y"]),
                int(msg["r"]),
                int(msg["g"]),
                int(msg["b"]),
                canvas=canvas_tuples,
            )
            return {"ok": True}
        if cmd == "send_frame":
            pixels = msg.get("pixels")
            if not isinstance(pixels, list) or len(pixels) != 256:
                raise ValueError("pixels must be a list of 256 [r,g,b] triples")
            picture = [tuple(int(c) for c in px) for px in pixels]
            await self._device.send_frame(picture)
            return {"sent": True}
        if cmd == "clear_screen":
            await self._device.clear_screen()
            return {"cleared": True}
        if cmd == "fade_frame":
            pixels = msg.get("pixels")
            if not isinstance(pixels, list) or len(pixels) != 256:
                raise ValueError("pixels must be a list of 256 [r,g,b] triples")
            picture = [tuple(int(c) for c in px) for px in pixels]
            await self._device.fade_frame(
                picture,
                to_black=bool(msg.get("to_black", True)),
                steps=int(msg.get("steps", 8)),
                step_delay=float(msg.get("step_delay", 0.04)),
            )
            return {"faded": True}
        raise ValueError(f"Unknown command: {cmd}")

    async def _client_handler(self, ws) -> None:
        remote = getattr(ws, "remote_address", None)
        print(f"[proxy] client connected: {remote}")
        self._clients.add(ws)
        try:
            await ws.send(
                dumps(
                    {
                        "type": "hello",
                        "version": PROTOCOL_VERSION,
                        "ble_connected": self._device.is_connected,
                        "label": self._device.device_label,
                        "auth_required": bool(self.token),
                    }
                )
            )

            if self.token:
                auth = await asyncio.wait_for(ws.recv(), timeout=15)
                auth_msg = loads(auth)
                if auth_msg.get("cmd") != "auth" or auth_msg.get("token") != self.token:
                    await self._reply(ws, auth_msg.get("id"), ok=False, error="unauthorized")
                    await ws.close(code=4001, reason="unauthorized")
                    return
                await self._reply(ws, auth_msg.get("id"), ok=True, result={"authed": True})

            async for raw in ws:
                try:
                    msg = loads(raw)
                except Exception as exc:
                    await ws.send(dumps({"ok": False, "error": f"bad json: {exc}"}))
                    continue

                req_id = msg.get("id")
                cmd = msg.get("cmd")
                if not cmd:
                    await self._reply(ws, req_id, ok=False, error="missing cmd")
                    continue

                try:
                    async with self._lock:
                        result = await self._handle_command(str(cmd), msg)
                    await self._reply(ws, req_id, ok=True, result=result)
                except Exception as exc:
                    await self._reply(ws, req_id, ok=False, error=str(exc))
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            print(f"[proxy] client disconnected: {remote}")

    def _lan_addresses(self) -> list[str]:
        addrs: list[str] = []
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except Exception:
            pass
        return addrs

    async def run(self) -> None:
        self._stop = asyncio.Event()
        if self.auto_connect_ble:
            print("[proxy] Connecting to MI Matrix Display over BLE...")
            ok = await self._device.connect()
            if not ok:
                print("[proxy] BLE connect failed — waiting for remote 'connect' commands")

        print(f"[proxy] Listening on ws://{self.host}:{self.port}")
        for ip in self._lan_addresses():
            print(f"[proxy] LAN URL: ws://{ip}:{self.port}")

        async with serve(self._client_handler, self.host, self.port) as ws_server:
            await self._stop.wait()
            ws_server.close()
            await ws_server.wait_closed()
        try:
            await self._device.disconnect()
        except Exception:
            pass
        print("[proxy] Stopped")

    def request_stop(self) -> None:
        if self._stop is not None:
            self._stop.set()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="BLE proxy for the MI Matrix Display (WebSocket bridge)"
    )
    parser.add_argument("--host", default=DEFAULT_PROXY_HOST, help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PROXY_PORT, help="Port (default 8765)")
    parser.add_argument("--token", default=None, help="Optional shared auth token")
    parser.add_argument(
        "--no-auto-connect",
        action="store_true",
        help="Do not connect to BLE until a client asks",
    )
    args = parser.parse_args(argv)

    server = BleProxyServer(
        host=args.host,
        port=args.port,
        token=args.token,
        auto_connect_ble=not args.no_auto_connect,
    )
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[proxy] Shutting down...")


if __name__ == "__main__":
    main()
