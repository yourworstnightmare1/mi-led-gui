#!/usr/bin/env python3
"""
Run a BLE proxy on the machine that can see the MI Matrix Display.

Example (Mac has Bluetooth to the panel; Windows runs the GUI):

  # On Mac
  python run_proxy.py

  # On Windows GUI: Transport = BLE Proxy, host = <Mac LAN IP>, Connect
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mi_led.proxy_server import main

if __name__ == "__main__":
    main()
