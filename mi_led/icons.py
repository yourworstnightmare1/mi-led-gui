"""Bundled UI icons (light / dark variants for CustomTkinter)."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from PIL import Image


def _assets_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "mi_led" / "assets"
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _assets_dir()

NAV_ICON_FILES = {
    "home": "nav_home",
    "draw": "nav_draw",
    "animate": "nav_animate",
    "text": "nav_text",
    "music": "nav_music",
    "bridge": "nav_bridge",
    "debug": "nav_debug",
    "settings": "nav_settings",
    "credits": "nav_credits",
}

OS_ICON_FILES = {
    "macos": "os_macos",
    "windows10": "os_windows10",
    "windows11": "os_windows11",
    "windows": "os_windows11",
    "linux": "os_linux",
    "unknown": "theme_system",
}

# Semantic action icons (from project assets/*.svg)
ACTION_ICON_FILES = {
    "palette": "action_palette",
    "eraser": "action_eraser",
    "clear": "action_clear",
    "upload": "action_upload",
    "import": "action_import",
    "save": "action_save",
    "play": "action_play",
    "stop": "action_stop",
    "power": "action_power",
    "add": "action_add",
    "copy": "action_copy",
    "delete": "action_delete",
    "speed": "action_speed",
    "update": "action_update",
    "info": "action_info",
    "scan": "action_scan",
    "apply": "action_apply",
    "listen": "action_listen",
    "terminal": "action_terminal",
    "github": "brand_github",
    "license": "brand_license",
    "lock": "status_lock",
    "sun": "theme_sun",
    "moon": "theme_moon",
    "system": "theme_system",
}


def _pair(stem: str) -> tuple[Path, Path]:
    base = _assets_dir()
    light = base / f"{stem}.png"
    dark = base / f"{stem}_dark.png"
    if not dark.exists():
        dark = light
    return light, dark


@lru_cache(maxsize=128)
def load_icon(stem: str, size: tuple[int, int] = (20, 20)) -> Optional[ctk.CTkImage]:
    light_path, dark_path = _pair(stem)
    if not light_path.exists():
        return None
    try:
        light = Image.open(light_path)
        dark = Image.open(dark_path)
        return ctk.CTkImage(light_image=light, dark_image=dark, size=size)
    except Exception:
        return None


def nav_icon(key: str, size: tuple[int, int] = (18, 18)) -> Optional[ctk.CTkImage]:
    stem = NAV_ICON_FILES.get(key)
    if not stem:
        return None
    return load_icon(stem, size)


def os_icon(os_id: str, size: tuple[int, int] = (24, 24)) -> Optional[ctk.CTkImage]:
    from .discovery import normalize_os_id

    key = normalize_os_id(os_id)
    stem = OS_ICON_FILES.get(key, "theme_system")
    return load_icon(stem, size)


def ui_icon(name: str, size: tuple[int, int] = (18, 18)) -> Optional[ctk.CTkImage]:
    """Load by stem name or semantic action key."""
    stem = ACTION_ICON_FILES.get(name, name)
    return load_icon(stem, size)


def action_icon(key: str, size: tuple[int, int] = (16, 16)) -> Optional[ctk.CTkImage]:
    stem = ACTION_ICON_FILES.get(key)
    if not stem:
        return None
    return load_icon(stem, size)
