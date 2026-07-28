# Merkury Innovations Matrix LED Display SDK

Python toolkit and desktop GUI for the Merkury Innovations Multicolor Matrix LED Display (MI-LNL62-999W), based on reverse engineering of its BLE protocol. The display is sold at [Walmart](https://www.walmart.com/ip/Merkury-Innovations-Bluetooth-Matrix-LED-Pixel-Display/5150283693).

## Desktop GUI

The `mi_led` package provides a CustomTkinter app with:

- Auto-connect (name match, then service-UUID fallback used on macOS)
- Power on / off
- 16×16 pixel canvas with live BLE updates or manual **Send to Display**
- Image upload (PNG/JPG/GIF) resized and color-boosted for the matrix
- **BLE Proxy** so one machine owns Bluetooth and another controls the panel over the LAN
- Text overlay, music-reactive modes, and LAN session discovery

Supported desktops: **macOS**, **Windows**, and **Linux**.

### Setup

```bash
python3 -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

#### Linux packages

Bluetooth (BlueZ) and Tk are usually needed from the distro:

```bash
# Debian / Ubuntu
sudo apt install python3-venv python3-tk bluez libbluetooth-dev \
  portaudio19-dev   # for sounddevice / Music tab

# Fedora
sudo dnf install python3-tkinter bluez-libs portaudio-devel

# Arch
sudo pacman -S tk bluez bluez-utils portaudio
```

Add your user to the `bluetooth` group if scans fail without root, then re-login:

```bash
sudo usermod -aG bluetooth "$USER"
```

### Run (local Bluetooth)

```bash
python run_gui.py
```

- **macOS:** grant Bluetooth permission to Terminal/Python when prompted.
- **Linux:** ensure the adapter is on (`bluetoothctl power on`) and BlueZ is running.
- Keep the display powered and nearby.

### BLE Proxy (Windows ↔ macOS ↔ Linux)

USB on the panel is power-only; control is always BLE. Any desktop can be the BLE host or the GUI client:

```text
Windows/Linux GUI  --WebSocket-->  Mac/Linux proxy  --BLE-->  MI Matrix Display
```

**On the BLE host** (machine next to the display):

```bash
python run_proxy.py
# optional: python run_proxy.py --port 8765 --token secret
```

The proxy prints LAN URLs such as `ws://192.168.1.20:8765`. Allow that port through the host firewall if needed (UDP **8766** for session discovery, TCP **8765** for the proxy).

**On the control PC** (GUI):

1. Run `python run_gui.py`
2. Open **BLE Bridge**, set Mode to **BLE Proxy**
3. Enter the BLE host’s LAN IP and port (default `8765`)
4. Enter the same token if you started the proxy with `--token`
5. Click **Connect** (or **Scan for sessions**)

### Music tab (system audio)

| OS | System audio |
|----|----------------|
| Windows | WASAPI loopback when available |
| macOS | Needs BlackHole (or similar) |
| Linux | Needs a PulseAudio/PipeWire **Monitor of …** input (see pavucontrol) |

Microphone works on all three without extra setup.

### Packaging

Build on the same OS you want to distribute to:

```bash
# Linux
chmod +x scripts/build_linux.sh
./scripts/build_linux.sh

# macOS / Windows (example)
python3 -m pip install pyinstaller
python3 -m PyInstaller --noconfirm --windowed --name "MI LED" \
  --collect-all customtkinter --collect-all bleak \
  --hidden-import sounddevice --hidden-import numpy --hidden-import certifi \
  --add-data "mi_led/assets:mi_led/assets" \
  run_gui.py
```

On macOS, the `.app` Info.plist must include Bluetooth (and optionally microphone) usage strings — see the existing `MI LED.spec` / rebuild notes if you use a `.spec` file.

## Scripts

- `draw_picture.py` — full-frame picture updates (includes two-pass discovery)
- `draw_pixels.py` — graffiti-mode pixel streaming
- `draw_file.py` — load an image file and push it to the display

Browser demos (Web Bluetooth can be unreliable with this hardware):

- [Graffiti](https://htmlpreview.github.io/?https://github.com/offe/mi-led-display/blob/main/grafitti.html)
- [Show image](https://htmlpreview.github.io/?https://github.com/offe/mi-led-display/blob/main/show_image.html)

## Collecting Bluetooth Snoop Logs

See `snoop_instructions.md` for capturing Android Bluetooth HCI snoop logs.

## License

MIT
