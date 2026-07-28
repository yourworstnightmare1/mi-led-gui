#!/usr/bin/env bash
# Build a Linux binary with PyInstaller (run on Linux).
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm \
  --windowed \
  --name "MI LED" \
  --collect-all customtkinter \
  --collect-all bleak \
  --hidden-import sounddevice \
  --hidden-import numpy \
  --hidden-import certifi \
  --add-data "mi_led/assets:mi_led/assets" \
  run_gui.py

echo
echo "Built: dist/MI LED/MI LED"
echo "Run:   ./dist/MI\ LED/MI\ LED"
