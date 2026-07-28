"""Persistent app settings for the MI LED GUI."""

from __future__ import annotations

import json
import os
import platform
import shlex
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


APP_DIR_NAME = "mi-led-gui"
SETTINGS_FILE = "settings.json"
WORKSPACE_FILE = "workspace.json"
LAUNCH_AGENT_LABEL = "com.mi-led-gui"
BRIDGE_LAUNCH_AGENT_LABEL = "com.mi-led-gui.bridge"
WINDOWS_RUN_VALUE = "MI LED Display"
WINDOWS_BRIDGE_RUN_VALUE = "MI LED BLE Bridge"
LINUX_BRIDGE_DESKTOP = f"{APP_DIR_NAME}-bridge.desktop"
PIXEL_COUNT = 256
MAX_ANIM_PANELS = 64


@dataclass
class AppSettings:
    start_on_boot: bool = False
    start_minimized: bool = False
    bridge_start_on_boot: bool = False
    power_off_on_logoff: bool = False
    fade_on_power_off: bool = True
    fade_on_power_on: bool = True
    animation_frame_ms: int = 200
    live_update_ms: int = 25
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8765
    proxy_token: str = ""
    bridge_bind_host: str = "0.0.0.0"
    bridge_port: int = 8765
    bridge_token: str = ""
    connection_mode: str = "local"  # "local" | "proxy"
    ble_last_address: str = ""
    fade_steps: int = 8
    fade_step_ms: int = 40
    # LED-accurate on-screen preview (does not change values sent to the panel).
    led_preview: bool = True
    preview_gamma: float = 0.72
    preview_brightness: float = 1.12
    preview_saturation: float = 1.05
    preview_yellow_push: float = 0.12
    preview_bloom: float = 0.0
    hide_connection_notice: bool = False
    # Appearance: "System" (default) | "Light" | "Dark"
    appearance_mode: str = "System"

    def clamp(self) -> "AppSettings":
        # Below ~10 ms the panel shows pixel shifts / laggy full-frame updates.
        self.animation_frame_ms = max(10, min(int(self.animation_frame_ms), 10_000))
        self.live_update_ms = max(10, min(int(self.live_update_ms), 5_000))
        self.proxy_port = max(1, min(int(self.proxy_port), 65535))
        self.bridge_port = max(1, min(int(self.bridge_port), 65535))
        self.fade_steps = max(1, min(int(self.fade_steps), 32))
        self.fade_step_ms = max(10, min(int(self.fade_step_ms), 500))
        if self.connection_mode not in ("local", "proxy"):
            self.connection_mode = "local"
        mode = str(self.appearance_mode or "System").strip().title()
        if mode not in ("System", "Light", "Dark"):
            mode = "System"
        self.appearance_mode = mode
        self.preview_gamma = max(0.3, min(float(self.preview_gamma), 1.5))
        self.preview_brightness = max(0.5, min(float(self.preview_brightness), 2.0))
        self.preview_saturation = max(0.5, min(float(self.preview_saturation), 2.0))
        self.preview_yellow_push = max(0.0, min(float(self.preview_yellow_push), 0.5))
        self.preview_bloom = max(0.0, min(float(self.preview_bloom), 0.6))
        return self


def settings_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home())) / APP_DIR_NAME
    else:
        # Linux / other Unix — XDG config home.
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
        base = base / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILE


def load_settings() -> AppSettings:
    path = settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}

    known = {f.name for f in fields(AppSettings)}
    filtered = {k: v for k, v in data.items() if k in known}
    return AppSettings(**filtered).clamp()


def save_settings(settings: AppSettings) -> None:
    settings.clamp()
    path = settings_path()
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


def workspace_path() -> Path:
    return settings_dir() / WORKSPACE_FILE


def _normalize_frame(raw: Any) -> list[tuple[int, int, int]] | None:
    if not isinstance(raw, list) or len(raw) != PIXEL_COUNT:
        return None
    frame: list[tuple[int, int, int]] = []
    for px in raw:
        if not isinstance(px, (list, tuple)) or len(px) != 3:
            return None
        try:
            r, g, b = (max(0, min(255, int(c))) for c in px)
        except (TypeError, ValueError):
            return None
        frame.append((r, g, b))
    return frame


def load_workspace() -> tuple[
    list[tuple[int, int, int]] | None,
    list[list[tuple[int, int, int]]],
    int,
    bool,
]:
    """
    Load last drawing + animation panels.

    Returns (drawing_or_None, anim_frames, anim_index, animation_playing).
    anim_frames always has at least one frame.
    """
    path = workspace_path()
    drawing = None
    frames: list[list[tuple[int, int, int]]] = []
    anim_index = 0
    animation_playing = False
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = None
        if isinstance(raw, dict):
            drawing = _normalize_frame(raw.get("drawing"))
            panels = raw.get("animation")
            if isinstance(panels, list):
                for panel in panels[:MAX_ANIM_PANELS]:
                    frame = _normalize_frame(panel)
                    if frame is not None:
                        frames.append(frame)
            try:
                anim_index = int(raw.get("animation_index", 0))
            except (TypeError, ValueError):
                anim_index = 0
            animation_playing = bool(raw.get("animation_playing", False))

    if not frames:
        frames = [[(0, 0, 0)] * PIXEL_COUNT]
    anim_index = max(0, min(anim_index, len(frames) - 1))
    return drawing, frames, anim_index, animation_playing


def save_workspace(
    drawing: list[tuple[int, int, int]],
    animation: list[list[tuple[int, int, int]]],
    animation_index: int = 0,
    animation_playing: bool = False,
) -> None:
    """Persist the current drawing canvas and animation panels."""
    drawing_frame = _normalize_frame(drawing)
    if drawing_frame is None:
        raise ValueError("drawing must be 256 RGB triples")

    panels: list[list[list[int]]] = []
    for panel in animation[:MAX_ANIM_PANELS]:
        frame = _normalize_frame(panel)
        if frame is None:
            raise ValueError("each animation panel must be 256 RGB triples")
        panels.append([[r, g, b] for r, g, b in frame])
    if not panels:
        panels = [[[0, 0, 0]] * PIXEL_COUNT]

    animation_index = max(0, min(int(animation_index), len(panels) - 1))
    payload = {
        "version": 1,
        "drawing": [[r, g, b] for r, g, b in drawing_frame],
        "animation": panels,
        "animation_index": animation_index,
        "animation_playing": bool(animation_playing),
    }
    workspace_path().write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def _python_launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    root = Path(__file__).resolve().parent.parent
    run_gui = root / "run_gui.py"
    return [sys.executable, str(run_gui)]


def _bridge_launch_command(
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    token: str = "",
) -> list[str]:
    """Command line that starts the standalone BLE bridge/proxy."""
    host = (host or "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = max(1, min(int(port), 65535))
    except (TypeError, ValueError):
        port = 8765
    token = (token or "").strip()

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--bridge", "--host", host, "--port", str(port)]
    else:
        root = Path(__file__).resolve().parent.parent
        cmd = [
            sys.executable,
            str(root / "run_proxy.py"),
            "--host",
            host,
            "--port",
            str(port),
        ]
    if token:
        cmd.extend(["--token", token])
    return cmd


def apply_start_on_boot(enabled: bool) -> tuple[bool, str]:
    """Enable/disable OS login-item launch. Returns (ok, message)."""
    system = platform.system()
    if system == "Darwin":
        return _apply_macos_login_item(enabled)
    if system == "Windows":
        return _apply_windows_startup(enabled)
    if system == "Linux":
        return _apply_linux_autostart(enabled)
    return False, f"Start on boot is not supported on {system or 'this OS'}"


def apply_bridge_start_on_boot(
    enabled: bool,
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    token: str = "",
) -> tuple[bool, str]:
    """Enable/disable launching the BLE bridge at login. Returns (ok, message)."""
    system = platform.system()
    if system == "Darwin":
        return _apply_macos_bridge_login_item(enabled, host=host, port=port, token=token)
    if system == "Windows":
        return _apply_windows_bridge_startup(enabled, host=host, port=port, token=token)
    if system == "Linux":
        return _apply_linux_bridge_autostart(enabled, host=host, port=port, token=token)
    return False, f"Bridge startup is not supported on {system or 'this OS'}"


def bridge_start_on_boot_installed() -> bool:
    """True if a bridge login/startup entry is present on this OS."""
    system = platform.system()
    if system == "Darwin":
        return _macos_bridge_plist_path().exists()
    if system == "Windows":
        return _windows_bridge_run_value_exists()
    if system == "Linux":
        return _linux_bridge_autostart_path().exists()
    return False


def _apply_macos_login_item(enabled: bool) -> tuple[bool, str]:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    if not enabled:
        if plist_path.exists():
            try:
                plist_path.unlink()
            except OSError as exc:
                return False, f"Could not remove LaunchAgent: {exc}"
        return True, "Removed macOS login item"

    cmd = _python_launch_command()
    program_args = "".join(f"\n        <string>{arg}</string>" for arg in cmd)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>{program_args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{Path(__file__).resolve().parent.parent}</string>
</dict>
</plist>
"""
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write LaunchAgent: {exc}"
    return True, f"Installed login item at {plist_path}"


def _macos_bridge_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{BRIDGE_LAUNCH_AGENT_LABEL}.plist"


def _plist_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _apply_macos_bridge_login_item(
    enabled: bool,
    *,
    host: str,
    port: int,
    token: str,
) -> tuple[bool, str]:
    import subprocess

    plist_path = _macos_bridge_plist_path()
    label = BRIDGE_LAUNCH_AGENT_LABEL

    def _bootout() -> None:
        try:
            uid = os.getuid()
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
                capture_output=True,
                check=False,
            )
        except Exception:
            try:
                subprocess.run(
                    ["launchctl", "unload", str(plist_path)],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass

    if not enabled:
        _bootout()
        if plist_path.exists():
            try:
                plist_path.unlink()
            except OSError as exc:
                return False, f"Could not remove bridge LaunchAgent: {exc}"
        return True, "Removed BLE bridge from macOS startup"

    cmd = _bridge_launch_command(host=host, port=port, token=token)
    program_args = "".join(
        f"\n        <string>{_plist_escape(arg)}</string>" for arg in cmd
    )
    log_dir = settings_dir()
    stdout_log = _plist_escape(str(log_dir / "bridge-stdout.log"))
    stderr_log = _plist_escape(str(log_dir / "bridge-stderr.log"))
    workdir = _plist_escape(str(Path(__file__).resolve().parent.parent))
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>{program_args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        # Unload any previous agent, then write the updated plist for the next login.
        _bootout()
        plist_path.write_text(plist, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write bridge LaunchAgent: {exc}"
    return True, f"Installed BLE bridge startup service at {plist_path}"


def _apply_windows_startup(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False, "Windows registry module unavailable"

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = _python_launch_command()
                # Quote paths that may contain spaces.
                value = " ".join(f'"{part}"' if " " in part else part for part in cmd)
                winreg.SetValueEx(key, WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, value)
                return True, "Added Windows startup entry"
            try:
                winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
            except FileNotFoundError:
                pass
            return True, "Removed Windows startup entry"
    except OSError as exc:
        return False, f"Could not update startup registry: {exc}"


def _windows_quote_cmd(cmd: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in cmd)


def _windows_bridge_run_value_exists() -> bool:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, WINDOWS_BRIDGE_RUN_VALUE)
            return True
    except OSError:
        return False


def _apply_windows_bridge_startup(
    enabled: bool,
    *,
    host: str,
    port: int,
    token: str,
) -> tuple[bool, str]:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False, "Windows registry module unavailable"

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = _bridge_launch_command(host=host, port=port, token=token)
                winreg.SetValueEx(
                    key,
                    WINDOWS_BRIDGE_RUN_VALUE,
                    0,
                    winreg.REG_SZ,
                    _windows_quote_cmd(cmd),
                )
                return True, "Added BLE bridge to Windows startup"
            try:
                winreg.DeleteValue(key, WINDOWS_BRIDGE_RUN_VALUE)
            except FileNotFoundError:
                pass
            return True, "Removed BLE bridge from Windows startup"
    except OSError as exc:
        return False, f"Could not update bridge startup registry: {exc}"


def _linux_autostart_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "autostart" / f"{APP_DIR_NAME}.desktop"


def _linux_bridge_autostart_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "autostart" / LINUX_BRIDGE_DESKTOP


def _apply_linux_autostart(enabled: bool) -> tuple[bool, str]:
    desktop_path = _linux_autostart_path()
    if not enabled:
        if desktop_path.exists():
            try:
                desktop_path.unlink()
            except OSError as exc:
                return False, f"Could not remove autostart entry: {exc}"
        return True, "Removed Linux autostart entry"

    cmd = _python_launch_command()
    exec_line = " ".join(shlex.quote(part) for part in cmd)
    workdir = str(Path(__file__).resolve().parent.parent)
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=MI LED\n"
        "Comment=Merkury Matrix LED Display controller\n"
        f"Exec={exec_line}\n"
        f"Path={workdir}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(body, encoding="utf-8")
        desktop_path.chmod(desktop_path.stat().st_mode | 0o111)
    except OSError as exc:
        return False, f"Could not write autostart entry: {exc}"
    return True, f"Installed Linux autostart at {desktop_path}"


def _apply_linux_bridge_autostart(
    enabled: bool,
    *,
    host: str,
    port: int,
    token: str,
) -> tuple[bool, str]:
    desktop_path = _linux_bridge_autostart_path()
    if not enabled:
        if desktop_path.exists():
            try:
                desktop_path.unlink()
            except OSError as exc:
                return False, f"Could not remove bridge autostart entry: {exc}"
        return True, "Removed BLE bridge from Linux startup"

    cmd = _bridge_launch_command(host=host, port=port, token=token)
    exec_line = " ".join(shlex.quote(part) for part in cmd)
    workdir = str(Path(__file__).resolve().parent.parent)
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=MI LED BLE Bridge\n"
        "Comment=Start the MI LED BLE WebSocket bridge at login\n"
        f"Exec={exec_line}\n"
        f"Path={workdir}\n"
        "Terminal=false\n"
        "Categories=Utility;Network;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(body, encoding="utf-8")
        desktop_path.chmod(desktop_path.stat().st_mode | 0o111)
    except OSError as exc:
        return False, f"Could not write bridge autostart entry: {exc}"
    return True, f"Installed BLE bridge startup at {desktop_path}"
