# Merkury Innovations Matrix LED Display SDK

Python toolkit and desktop GUI for the Merkury Innovations Multicolor Matrix LED Display (MI-LNL62-999W), based on reverse engineering of its BLE protocol. The display is sold at [Walmart](https://www.walmart.com/ip/Merkury-Innovations-Bluetooth-Matrix-LED-Pixel-Display/5150283693).

## Desktop GUI

The `mi_led` package provides a CustomTkinter app with:

- Auto-connect (name match, then service-UUID fallback used on macOS)
- Power on / off
- 16×16 pixel canvas with live BLE updates or manual **Send to Display**
- Image upload (PNG/JPG/GIF) resized and color-boosted for the matrix
- **BLE Proxy** so one machine owns Bluetooth and another controls the panel over the LAN

### Setup

```bash
python3 -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Run (local Bluetooth)

```bash
python run_gui.py
```

On macOS, grant Bluetooth permission to Terminal/Python when prompted. Keep the display powered and nearby.

### BLE Proxy (Windows ↔ macOS)

USB on the panel is power-only; control is always BLE. If the display is near a Mac but you want to drive it from Windows (or the reverse), run a proxy on the BLE host:

```text
Windows GUI  --WebSocket-->  Mac proxy  --BLE-->  MI Matrix Display
```

**On the BLE host** (machine next to the display):

```bash
python run_proxy.py
# optional: python run_proxy.py --port 8765 --token secret
```

The proxy prints LAN URLs such as `ws://192.168.1.20:8765`. Allow that port through the host firewall if needed.

**On the control PC** (GUI):

1. Run `python run_gui.py`
2. Set **Transport** to **BLE Proxy**
3. Enter the BLE host’s LAN IP and port (default `8765`)
4. Enter the same token if you started the proxy with `--token`
5. Click **Connect**

Either OS can be the BLE host or the GUI client.

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
