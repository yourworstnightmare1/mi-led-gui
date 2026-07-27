# Merkury Innovations Matrix LED Display SDK

Python toolkit and desktop GUI for the Merkury Innovations Multicolor Matrix LED Display (MI-LNL62-999W), based on reverse engineering of its BLE protocol. The display is sold at [Walmart](https://www.walmart.com/ip/Merkury-Innovations-Bluetooth-Matrix-LED-Pixel-Display/5150283693).

# What this app can do
- Allows remote control of your Matrix LED Display straight from your Windows or macOS device with ease
- Create drawings and display them on the LED display
- Create animations with unlimited frames and display them on the LED display
- Create text with custom backgrounds and animations and display them on the LED display
- Support for uploading custom PNG files and displaying them on the LED display
- Support for uploading animated GIFs and displaying them on the LED display
- Support for saving animations and drawings as Python (`.py`) scripts for preservation of exact color codes and data
- Support for saving animations as PNGs and GIFs
- Support for saving drawings as PNGs
- Support for BLE proxies with the BLE bridge feature, allowing non-Bluetooth PCs to control the LED display
- Support for full 16x16 (256 pixel) display
- Support for all RGB colors
- Support for audio reactivity via microphone or system audio*
- Verbose BLE logs between the app and the LED's display
- Ability to open BLE verbose output in system default shell interface
- Support for animations of 10ms per frame update (display hardware limitation)
- Support for launching app on device startup
- Support for auto minimizing app on device startup if launched on startup

*On macOS, you must download [BlackHole](https://existential.audio/blackhole/) in order to use system sound as it is not natively supported.

# Why this exists?
This GUI and its backend mostly exist because the official iOS app (and yes, it's only for iOS for some unknown reason) for this display is very poorly supported by the manufacturers and they don't seem to care at all. The app is also extremely limited in what it can do and is also extremely buggy and crashes trying to create animations or even navigating the app. People have complained about this time and time again and Merkury doesn't care, so me and others such as [offe](https://github.com/offe) have created tools for this display to unlock its full potential. I personally think this is a really cool LED display that has so much potential, it's just locked behind arbitrary restrictions and poor support. This app allows you to do basically everything the official app does, but without limitations. All animation frame limits, text length limits, music only allowing you to play prepicked songs, and others are gone, allowing you to do basically whatever you please with this thing.

# About music mode
Music mode is limited because the hardware in the display causes the audio's reactivity animation to have a slight delay because the display can only show so many frames at a lower ms value before it starts experiencing pixel shift and other bugs. To be honest I didn't really want to add this because it's honestly not that great, but if you really want it, i've included it. Don't be surprised when your animation is delayed, though. That's something this app can't fix. Also we did not include the likely stolen music (it could be copyright free but i'm not risking it given the people who made this thing) from the official app to avoid copyright and DMCA.

### Setup

```bash
python -m venv venv
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


## Collecting Bluetooth Snoop Logs

See `snoop_instructions.md` for capturing Android Bluetooth HCI snoop logs.

# License
Uses MIT, just like the project this was forked from.
