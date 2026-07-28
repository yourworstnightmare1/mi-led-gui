#!/usr/bin/env python3
"""Launch the MI LED Display desktop GUI (or BLE bridge with --bridge)."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when run as a script.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _check_runtime() -> None:
    """Give actionable errors for common Linux packaging gaps."""
    try:
        import tkinter  # noqa: F401
    except ImportError as exc:
        if platform.system() == "Linux":
            raise SystemExit(
                "Tkinter is required but missing.\n"
                "Debian/Ubuntu: sudo apt install python3-tk\n"
                "Fedora:        sudo dnf install python3-tkinter\n"
                "Arch:          sudo pacman -S tk\n"
            ) from exc
        raise SystemExit(
            "Tkinter is required. Install the Tk bindings for your Python build."
        ) from exc


def _run_bridge(argv: list[str]) -> None:
    from mi_led.proxy_server import main as proxy_main

    proxy_main(argv)


if __name__ == "__main__":
    if "--bridge" in sys.argv:
        # Frozen/startup entry: MI LED.exe --bridge --host ... --port ...
        bridge_argv = [a for a in sys.argv[1:] if a != "--bridge"]
        _run_bridge(bridge_argv)
    else:
        _check_runtime()
        from mi_led.app import main

        main()
