"""UDP LAN discovery for MI LED GUI / BLE bridge sessions."""

from __future__ import annotations

import json
import platform
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

DISCOVERY_PORT = 8766
DISCOVERY_MAGIC = "mi-led"
DISCOVERY_VERSION = 2


def detect_os_id() -> str:
    """Return a coarse OS id for discovery UI icons."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        try:
            build = int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
        except Exception:
            build = 0
        return "windows11" if build >= 22000 else "windows10"
    if system == "Linux":
        return "linux"
    return "unknown"


@dataclass(frozen=True)
class SessionInfo:
    name: str
    ip: str
    port: int
    bridge: bool
    auth_required: bool
    hostname: str
    os_id: str = "unknown"
    ping_ms: Optional[int] = None

    @property
    def label(self) -> str:
        role = "bridge" if self.bridge else "app"
        auth = " · auth" if self.auth_required else ""
        return f"{self.name}  —  {self.ip}:{self.port}  ({role}{auth})"

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{self.port}"


def preferred_lan_ip() -> Optional[str]:
    """Best-effort primary LAN IPv4 (does not send traffic)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not str(ip).startswith("127."):
            return str(ip)
    except Exception:
        pass
    return None


def local_ipv4_addresses() -> list[str]:
    addrs: list[str] = []
    preferred = preferred_lan_ip()
    if preferred:
        addrs.append(preferred)
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except Exception:
        pass
    return addrs


def _subnet_broadcasts(ips: list[str]) -> list[str]:
    """Assume /24 for common home/office LANs."""
    out: list[str] = []
    for ip in ips:
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        if bcast not in out:
            out.append(bcast)
    return out


def _parse_announce(data: bytes, from_ip: str, *, ping_ms: Optional[int] = None) -> Optional[SessionInfo]:
    try:
        msg = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(msg, dict):
        return None
    if msg.get("magic") != DISCOVERY_MAGIC or msg.get("type") != "announce":
        return None
    ip = str(msg.get("ip") or from_ip).strip()
    if not ip or ip.startswith("127."):
        ip = from_ip
    try:
        port = int(msg.get("port") or 8765)
    except (TypeError, ValueError):
        port = 8765
    hostname = str(msg.get("hostname") or "").strip() or ip
    name = str(msg.get("name") or hostname).strip() or hostname
    os_id = str(msg.get("os") or msg.get("os_id") or "unknown").strip().lower() or "unknown"
    return SessionInfo(
        name=name,
        ip=ip,
        port=port,
        bridge=bool(msg.get("bridge")),
        auth_required=bool(msg.get("auth_required")),
        hostname=hostname,
        os_id=os_id,
        ping_ms=ping_ms,
    )


class SessionBeacon:
    """
    Responds to LAN discovery queries while the app (or standalone proxy) is running.
    """

    def __init__(self, info_provider: Optional[Callable[[], dict[str, Any]]] = None):
        self._info_provider = info_provider or (lambda: {})
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="mi-led-discovery", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as poke:
                    poke.sendto(b"{}", ("127.0.0.1", DISCOVERY_PORT))
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
        self._sock = None

    def _announce_payload(self) -> bytes:
        info = dict(self._info_provider())
        hostname = socket.gethostname() or "MI LED"
        ip = str(info.get("ip") or preferred_lan_ip() or "").strip()
        payload = {
            "magic": DISCOVERY_MAGIC,
            "type": "announce",
            "v": DISCOVERY_VERSION,
            "name": str(info.get("name") or hostname),
            "hostname": hostname,
            "ip": ip,
            "port": int(info.get("port") or 8765),
            "bridge": bool(info.get("bridge")),
            "auth_required": bool(info.get("auth_required")),
            "os": str(info.get("os") or detect_os_id()),
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            sock.close()
            return
        sock.settimeout(0.5)
        self._sock = sock
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if self._stop.is_set():
                    break
                try:
                    msg = json.loads(data.decode("utf-8"))
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("magic") != DISCOVERY_MAGIC or msg.get("type") != "discover":
                    continue
                try:
                    sock.sendto(self._announce_payload(), addr)
                except OSError:
                    break
        finally:
            try:
                sock.close()
            except Exception:
                pass
            if self._sock is sock:
                self._sock = None


def scan_sessions(
    timeout: float = 1.8,
    *,
    bridges_only: bool = False,
    exclude_ips: Optional[set[str]] = None,
) -> list[SessionInfo]:
    """Broadcast a discovery query and collect announce replies."""
    query = json.dumps(
        {"magic": DISCOVERY_MAGIC, "type": "discover", "v": DISCOVERY_VERSION},
        separators=(",", ":"),
    ).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    sock.settimeout(0.25)

    targets = [("255.255.255.255", DISCOVERY_PORT)]
    for bcast in _subnet_broadcasts(local_ipv4_addresses()):
        targets.append((bcast, DISCOVERY_PORT))

    sent_at = time.monotonic()
    for target in targets:
        try:
            sock.sendto(query, target)
        except OSError:
            continue

    found: dict[tuple[str, int], SessionInfo] = {}
    exclude = exclude_ips or set()
    deadline = time.monotonic() + max(0.4, float(timeout))
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            ping_ms = max(0, int(round((time.monotonic() - sent_at) * 1000)))
            session = _parse_announce(data, addr[0], ping_ms=ping_ms)
            if session is None:
                continue
            if session.ip in exclude:
                continue
            if bridges_only and not session.bridge:
                continue
            key = (session.ip, session.port)
            prev = found.get(key)
            if prev is None or (
                session.ping_ms is not None
                and (prev.ping_ms is None or session.ping_ms < prev.ping_ms)
            ):
                found[key] = session
    finally:
        sock.close()

    sessions = list(found.values())
    sessions.sort(key=lambda s: (s.hostname.lower(), s.ip, s.port))
    return sessions
