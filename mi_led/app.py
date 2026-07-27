"""CustomTkinter GUI for the Merkury MI Matrix LED Display."""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from .device import DeviceController
from .image_convert import blank_frame, load_image_as_matrix
from .protocol import MATRIX_SIZE, pixel_index
from .proxy_protocol import DEFAULT_PROXY_PORT


PIXEL_CELL = 28
CANVAS_PAD = 1


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


class MiLedApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MI LED Display")
        self.geometry("760x700")
        self.minsize(680, 620)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.pixels: list[tuple[int, int, int]] = blank_frame()
        self.paint_color = (255, 0, 0)
        self.live_update = tk.BooleanVar(value=True)
        self.connection_mode = tk.StringVar(value="Local BLE")
        self.proxy_host = tk.StringVar(value="127.0.0.1")
        self.proxy_port = tk.StringVar(value=str(DEFAULT_PROXY_PORT))
        self.proxy_token = tk.StringVar(value="")
        self._painting = False
        self._last_painted: Optional[tuple[int, int]] = None
        self._status_var = tk.StringVar(value="Starting...")
        self._busy = False

        self.device = DeviceController(on_status=self._queue_status)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._auto_connect)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        conn = ctk.CTkFrame(self)
        conn.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        conn.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(conn, text="Status:").grid(row=0, column=0, padx=(10, 4), pady=8)
        self.status_label = ctk.CTkLabel(conn, textvariable=self._status_var, anchor="w")
        self.status_label.grid(row=0, column=1, sticky="ew", padx=4, pady=8)

        self.connect_btn = ctk.CTkButton(conn, text="Connect", width=100, command=self._on_connect)
        self.connect_btn.grid(row=0, column=2, padx=4, pady=8)
        self.disconnect_btn = ctk.CTkButton(
            conn, text="Disconnect", width=100, command=self._on_disconnect, state="disabled"
        )
        self.disconnect_btn.grid(row=0, column=3, padx=(4, 10), pady=8)

        transport = ctk.CTkFrame(self)
        transport.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        transport.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(transport, text="Transport:").grid(row=0, column=0, padx=(10, 4), pady=8)
        self.mode_menu = ctk.CTkOptionMenu(
            transport,
            values=["Local BLE", "BLE Proxy"],
            variable=self.connection_mode,
            width=120,
            command=self._on_mode_changed,
        )
        self.mode_menu.grid(row=0, column=1, padx=4, pady=8)

        ctk.CTkLabel(transport, text="Proxy host:").grid(row=0, column=2, padx=(12, 4), pady=8)
        self.host_entry = ctk.CTkEntry(transport, textvariable=self.proxy_host, width=140)
        self.host_entry.grid(row=0, column=3, padx=4, pady=8)

        ctk.CTkLabel(transport, text="Port:").grid(row=0, column=4, padx=(8, 4), pady=8)
        self.port_entry = ctk.CTkEntry(transport, textvariable=self.proxy_port, width=70)
        self.port_entry.grid(row=0, column=5, padx=4, pady=8, sticky="w")

        ctk.CTkLabel(transport, text="Token:").grid(row=1, column=0, padx=(10, 4), pady=(0, 8))
        self.token_entry = ctk.CTkEntry(
            transport, textvariable=self.proxy_token, width=220, placeholder_text="optional"
        )
        self.token_entry.grid(row=1, column=1, columnspan=3, padx=4, pady=(0, 8), sticky="w")
        ctk.CTkLabel(
            transport,
            text="Run `python run_proxy.py` on the BLE host machine",
            text_color=("gray30", "gray70"),
        ).grid(row=1, column=4, columnspan=2, padx=8, pady=(0, 8), sticky="w")

        self._update_proxy_fields()

        controls = ctk.CTkFrame(self)
        controls.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        for i in range(8):
            controls.grid_columnconfigure(i, weight=0)
        controls.grid_columnconfigure(7, weight=1)

        ctk.CTkButton(controls, text="Power On", width=90, command=self._on_power_on).grid(
            row=0, column=0, padx=(10, 4), pady=8
        )
        ctk.CTkButton(controls, text="Power Off", width=90, command=self._on_power_off).grid(
            row=0, column=1, padx=4, pady=8
        )

        self.color_preview = ctk.CTkButton(
            controls,
            text="",
            width=40,
            height=28,
            fg_color=rgb_to_hex(*self.paint_color),
            hover=False,
            command=self._pick_color,
        )
        self.color_preview.grid(row=0, column=2, padx=(16, 4), pady=8)
        ctk.CTkButton(controls, text="Pick Color", width=90, command=self._pick_color).grid(
            row=0, column=3, padx=4, pady=8
        )
        ctk.CTkButton(controls, text="Eraser", width=80, command=self._use_eraser).grid(
            row=0, column=4, padx=4, pady=8
        )
        ctk.CTkButton(controls, text="Clear", width=80, command=self._clear_canvas).grid(
            row=0, column=5, padx=4, pady=8
        )

        ctk.CTkSwitch(
            controls,
            text="Live update",
            variable=self.live_update,
            command=self._on_live_toggled,
        ).grid(row=0, column=6, padx=(16, 4), pady=8)

        self.send_btn = ctk.CTkButton(
            controls, text="Send to Display", width=130, command=self._send_frame
        )
        self.send_btn.grid(row=0, column=7, padx=(4, 10), pady=8, sticky="e")

        body = ctk.CTkFrame(self)
        body.grid(row=3, column=0, sticky="nsew", padx=12, pady=(6, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        canvas_wrap = ctk.CTkFrame(body)
        canvas_wrap.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        size = MATRIX_SIZE * PIXEL_CELL + 2
        self.canvas = tk.Canvas(
            canvas_wrap,
            width=size,
            height=size,
            bg="#111111",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(padx=8, pady=8)
        self.canvas.bind("<ButtonPress-1>", self._on_paint_start)
        self.canvas.bind("<B1-Motion>", self._on_paint_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_paint_end)

        self._cell_ids: list[int] = []
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                x0 = x * PIXEL_CELL + CANVAS_PAD
                y0 = y * PIXEL_CELL + CANVAS_PAD
                x1 = x0 + PIXEL_CELL - CANVAS_PAD
                y1 = y0 + PIXEL_CELL - CANVAS_PAD
                cell = self.canvas.create_rectangle(
                    x0, y0, x1, y1, fill="#000000", outline="#2a2a2a", width=1
                )
                self._cell_ids.append(cell)

        tools = ctk.CTkFrame(body)
        tools.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkButton(tools, text="Upload Image...", command=self._upload_image).pack(
            side="left", padx=(8, 4), pady=8
        )
        ctk.CTkLabel(
            tools,
            text="PNG / JPG / GIF → 16×16 with boosted color for the LED matrix",
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=8, pady=8)

        self._refresh_canvas()
        self._update_send_button_state()

    def _is_proxy_mode(self) -> bool:
        return self.connection_mode.get() == "BLE Proxy"

    def _proxy_url(self) -> str:
        host = self.proxy_host.get().strip() or "127.0.0.1"
        try:
            port = int(self.proxy_port.get().strip())
        except ValueError as exc:
            raise ValueError("Proxy port must be a number") from exc
        return f"ws://{host}:{port}"

    def _update_proxy_fields(self) -> None:
        state = "normal" if self._is_proxy_mode() else "disabled"
        self.host_entry.configure(state=state)
        self.port_entry.configure(state=state)
        self.token_entry.configure(state=state)

    def _on_mode_changed(self, _value: str | None = None) -> None:
        self._update_proxy_fields()
        if self.device.is_connected:
            self._on_disconnect()
        self._set_status(
            "Proxy mode — enter host and Connect"
            if self._is_proxy_mode()
            else "Local BLE mode"
        )

    def _apply_transport(self) -> None:
        token = self.proxy_token.get().strip() or None
        if self._is_proxy_mode():
            self.device.configure(mode="proxy", proxy_url=self._proxy_url(), proxy_token=token)
        else:
            self.device.configure(mode="local")

    def _queue_status(self, message: str) -> None:
        self.after(0, lambda m=message: self._set_status(m))

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)
        connected = self.device.is_connected
        self.connect_btn.configure(state="disabled" if connected or self._busy else "normal")
        self.disconnect_btn.configure(state="normal" if connected else "disabled")
        mode_state = "disabled" if connected or self._busy else "normal"
        self.mode_menu.configure(state=mode_state)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.connect_btn.configure(state="disabled" if busy or self.device.is_connected else "normal")
        self.mode_menu.configure(state="disabled" if busy or self.device.is_connected else "normal")

    def _auto_connect(self) -> None:
        if not self._is_proxy_mode():
            self._on_connect()
        else:
            self._set_status("Proxy mode — enter host and Connect")

    def _on_connect(self) -> None:
        if self._busy:
            return
        try:
            self._apply_transport()
        except ValueError as exc:
            messagebox.showerror("Proxy settings", str(exc))
            return

        self._set_busy(True)
        self._set_status("Connecting...")
        fut = self.device.connect()

        def done() -> None:
            try:
                ok = fut.result(timeout=60)
            except Exception as exc:
                self._set_status(f"Connect failed: {exc}")
                ok = False
            self._set_busy(False)
            if ok:
                self._set_status(f"Connected to {self.device.device_label}")
                if self.live_update.get():
                    self._safe_submit(self.device.enter_graffiti_mode)
            else:
                self._set_status("Not connected — click Connect to retry")

        self.after(100, self._poll_future, fut, done)

    def _on_disconnect(self) -> None:
        fut = self.device.disconnect()
        self.after(100, self._poll_future, fut, lambda: self._set_status("Disconnected"))

    def _poll_future(self, fut, on_done) -> None:
        if fut.done():
            on_done()
        else:
            self.after(100, self._poll_future, fut, on_done)

    def _safe_submit(self, action, *args, on_error: Optional[str] = None):
        if not self.device.is_connected:
            return None
        try:
            return action(*args)
        except Exception as exc:
            self._set_status(on_error or f"Error: {exc}")
            return None

    def _on_power_on(self) -> None:
        if not self._require_connection():
            return
        fut = self.device.power_on()
        self.after(100, self._poll_future, fut, lambda: None)

    def _on_power_off(self) -> None:
        if not self._require_connection():
            return
        fut = self.device.power_off()
        self.after(100, self._poll_future, fut, lambda: None)

    def _require_connection(self) -> bool:
        if self.device.is_connected:
            return True
        messagebox.showinfo("Not connected", "Connect to the MI Matrix Display first.")
        return False

    def _pick_color(self) -> None:
        initial = rgb_to_hex(*self.paint_color)
        result = colorchooser.askcolor(color=initial, title="Paint color")
        if not result or not result[0]:
            return
        r, g, b = (int(c) for c in result[0])
        self.paint_color = (r, g, b)
        self.color_preview.configure(fg_color=rgb_to_hex(r, g, b))

    def _use_eraser(self) -> None:
        self.paint_color = (0, 0, 0)
        self.color_preview.configure(fg_color="#000000")

    def _event_to_xy(self, event) -> Optional[tuple[int, int]]:
        x = event.x // PIXEL_CELL
        y = event.y // PIXEL_CELL
        if 0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE:
            return x, y
        return None

    def _on_paint_start(self, event) -> None:
        self._painting = True
        self._last_painted = None
        self._paint_at_event(event)

    def _on_paint_drag(self, event) -> None:
        if self._painting:
            self._paint_at_event(event)

    def _on_paint_end(self, _event) -> None:
        self._painting = False
        self._last_painted = None

    def _paint_at_event(self, event) -> None:
        pos = self._event_to_xy(event)
        if pos is None or pos == self._last_painted:
            return
        self._last_painted = pos
        x, y = pos
        self._set_local_pixel(x, y, self.paint_color)
        if self.live_update.get() and self.device.is_connected:
            r, g, b = self.paint_color
            self._safe_submit(self.device.set_pixel, x, y, r, g, b)

    def _set_local_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        idx = pixel_index(x, y)
        self.pixels[idx] = color
        self.canvas.itemconfigure(self._cell_ids[idx], fill=rgb_to_hex(*color))

    def _refresh_canvas(self) -> None:
        for i, color in enumerate(self.pixels):
            self.canvas.itemconfigure(self._cell_ids[i], fill=rgb_to_hex(*color))

    def _clear_canvas(self) -> None:
        self.pixels = blank_frame()
        self._refresh_canvas()
        if self.live_update.get() and self.device.is_connected:
            self._send_frame()

    def _on_live_toggled(self) -> None:
        self._update_send_button_state()
        if self.live_update.get() and self.device.is_connected:
            self._safe_submit(self.device.enter_graffiti_mode)

    def _update_send_button_state(self) -> None:
        self.send_btn.configure(state="normal")

    def _send_frame(self) -> None:
        if not self._require_connection():
            return
        frame = list(self.pixels)
        fut = self.device.send_frame(frame)

        def done() -> None:
            try:
                fut.result(timeout=30)
                self._set_status("Frame sent to display")
                if self.live_update.get():
                    self._safe_submit(self.device.enter_graffiti_mode)
            except Exception as exc:
                self._set_status(f"Send failed: {exc}")

        self._set_status("Sending frame...")
        self.after(100, self._poll_future, fut, done)

    def _upload_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.pixels = load_image_as_matrix(path)
        except Exception as exc:
            messagebox.showerror("Image error", f"Could not load image:\n{exc}")
            return

        self._refresh_canvas()
        self._set_status(f"Loaded image: {path}")
        if self.live_update.get() and self.device.is_connected:
            self._send_frame()

    def _on_close(self) -> None:
        try:
            self.device.shutdown()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = MiLedApp()
    app.mainloop()


if __name__ == "__main__":
    main()
