#!/usr/bin/env python3
"""Launch the MI LED Display desktop GUI."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is on sys.path when run as a script.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mi_led.app import main

if __name__ == "__main__":
    main()
