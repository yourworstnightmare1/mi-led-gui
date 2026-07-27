"""CustomTkinter GUI for the Merkury MI Matrix LED Display."""

from __future__ import annotations

import atexit
import asyncio
import platform
import shlex
import socket
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from .color_preview import PreviewStyle
from .device import DeviceController
from .discovery import SessionBeacon, SessionInfo, preferred_lan_ip, scan_sessions
from .export_io import (
    load_animation_gif,
    load_animation_zip,
    save_animation_gif,
    save_animation_python,
    save_animation_zip,
    save_drawing_python,
    save_frame_png,
)
from .image_convert import blank_frame, load_image_as_matrix
from .presets import ANIMATION_BY_LABEL, ANIMATION_PRESETS, DRAWING_BY_LABEL, DRAWING_PRESETS
from .protocol import MATRIX_SIZE
from .proxy_protocol import DEFAULT_PROXY_PORT
from .proxy_server import BleProxyServer
from .settings import (
    apply_start_on_boot,
    load_settings,
    load_workspace,
    save_settings,
    save_workspace,
    settings_dir,
)
from .widgets import MatrixCanvas, MatrixThumb, rgb_to_hex

# App revision — bump when diagnosing stale-module startup crashes.
APP_REVISION = 9


NAV_ITEMS = (
    ("home", "Home"),
    ("draw", "Draw"),
    ("animate", "Animate"),
    ("bridge", "BLE Bridge"),
    ("debug", "BLE Debugging"),
    ("settings", "Settings"),
)


class MiLedApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MI LED Display")
        self.geometry("1040x720")
        self.minsize(900, 640)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.settings = load_settings()
        # Sharp LED cells — bloom read as a muddy color shadow on the canvas.
        if self.settings.preview_bloom > 0:
            self.settings.preview_bloom = 0.0
            try:
                save_settings(self.settings)
            except Exception:
                pass
        saved_drawing, saved_anim, saved_anim_index, saved_anim_playing = load_workspace()
        self.pixels: list[tuple[int, int, int]] = (
            saved_drawing if saved_drawing is not None else blank_frame()
        )
        self._resume_animation_on_connect = saved_anim_playing
        self.paint_color = (255, 0, 0)
        self.live_update = tk.BooleanVar(value=True)
        self.connection_mode = tk.StringVar(
            value="BLE Proxy" if self.settings.connection_mode == "proxy" else "Local BLE"
        )
        self.proxy_host = tk.StringVar(value=self.settings.proxy_host)
        self.proxy_port = tk.StringVar(value=str(self.settings.proxy_port))
        self.proxy_token = tk.StringVar(value=self.settings.proxy_token)
        self.bridge_host = tk.StringVar(value=self.settings.bridge_bind_host)
        self.bridge_port = tk.StringVar(value=str(self.settings.bridge_port))
        self.bridge_token = tk.StringVar(value=self.settings.bridge_token)

        self._status_var = tk.StringVar(value="Starting...")
        self._busy = False
        self._powered_on = False
        self._display_frame: list[tuple[int, int, int]] = blank_frame()
        self._keepalive_mode: Optional[str] = None  # None | "blank" | "frame"
        self._keepalive_job: Optional[str] = None
        self._last_live_send = 0.0
        self._stroke_dirty = False
        self._live_frame_job: Optional[str] = None
        self._live_frame_inflight = False
        self._live_frame_queued = False
        self._workspace_save_job: Optional[str] = None
        self._debug_lines: list[str] = []
        self._debug_max = 2000
        self._debug_pending: list[str] = []
        self._debug_flush_job: Optional[str] = None
        self._debug_log_path: Path = settings_dir() / "ble-debug.log"
        self._debug_file_ready = False
        self._debug_terminal_opened = False
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_page = "home"
        self._preview: Optional[MatrixCanvas] = None
        self._draw_canvas: Optional[MatrixCanvas] = None
        self._animate_canvas: Optional[MatrixCanvas] = None
        self._debug_textbox: Optional[ctk.CTkTextbox] = None
        self._debug_status_var = tk.StringVar(value="")
        self._bridge_log: Optional[ctk.CTkTextbox] = None
        self._color_preview: Optional[ctk.CTkButton] = None
        self._anim_color_preview: Optional[ctk.CTkButton] = None

        # Animation state
        self.anim_frames: list[list[tuple[int, int, int]]] = saved_anim
        self.anim_index = saved_anim_index
        self._anim_playing = False
        self._anim_job: Optional[str] = None
        self._anim_panel_var = tk.StringVar(value=f"Panel {self.anim_index + 1}")
        self._draw_preset_var = tk.StringVar(value=DRAWING_PRESETS[0].label)
        self._anim_preset_var = tk.StringVar(value=ANIMATION_PRESETS[0].label)
        self._live_preset_fn = None  # Callable[[int], frame] | None
        self._live_preset_tick = 0
        self._live_preset_ms: Optional[int] = None
        self._anim_preview_only = False
        self._anim_generation = 0
        self._preview_before_play = tk.BooleanVar(value=True)
        self._anim_thumbs: list[MatrixThumb] = []
        self._anim_thumb_strip: Optional[ctk.CTkScrollableFrame] = None
        self._last_ui_frame_time = 0.0
        self._last_ui_frame: Optional[list[tuple[int, int, int]]] = None
        self._last_sent_frame: Optional[list[tuple[int, int, int]]] = None
        self._active_preset_label: Optional[str] = None

        # Embedded BLE bridge
        self._bridge_server: Optional[BleProxyServer] = None
        self._bridge_thread: Optional[threading.Thread] = None
        self._bridge_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bridge_running = False
        self._session_beacon = SessionBeacon(self._discovery_info)
        self._session_scan_win: Optional[ctk.CTkToplevel] = None
        self._closing = False

        self.device = DeviceController(
            on_status=self._queue_status,
            on_debug=self._queue_debug,
        )

        self._preview_style = self._make_preview_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._session_beacon.start()

        if self.settings.start_minimized:
            self.after(50, self.iconify)

        atexit.register(self._power_off_for_logoff)
        self.after(200, self._auto_connect)

    def _make_preview_style(self) -> PreviewStyle:
        s = self.settings
        return PreviewStyle(
            enabled=bool(s.led_preview),
            gamma=float(s.preview_gamma),
            brightness=float(s.preview_brightness),
            saturation=float(s.preview_saturation),
            yellow_push=float(s.preview_yellow_push),
            bloom=float(s.preview_bloom),
        )

    def _apply_preview_style(self) -> None:
        self._preview_style = self._make_preview_style()
        for canvas in (self._preview, self._draw_canvas, self._animate_canvas):
            if canvas is not None:
                canvas.set_preview_style(self._preview_style)

    # ------------------------------------------------------------------ UI shell

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=180, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(len(NAV_ITEMS) + 2, weight=1)

        ctk.CTkLabel(
            sidebar, text="MI LED", font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(18, 4), sticky="w")
        ctk.CTkLabel(
            sidebar, text="Matrix Display", text_color=("gray40", "gray65")
        ).grid(row=1, column=0, padx=16, pady=(0, 16), sticky="w")

        for i, (key, label) in enumerate(NAV_ITEMS):
            btn = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=36,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray30"),
                command=lambda k=key: self._show_page(k),
            )
            btn.grid(row=i + 2, column=0, sticky="ew", padx=10, pady=3)
            self._nav_buttons[key] = btn

        conn = ctk.CTkFrame(sidebar, fg_color="transparent")
        conn.grid(row=len(NAV_ITEMS) + 2, column=0, sticky="sew", padx=10, pady=12)
        self.connect_btn = ctk.CTkButton(conn, text="Connect", height=32, command=self._on_connect)
        self.connect_btn.pack(fill="x", pady=(0, 6))
        self.disconnect_btn = ctk.CTkButton(
            conn, text="Disconnect", height=32, command=self._on_disconnect, state="disabled"
        )
        self.disconnect_btn.pack(fill="x")

        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(main, height=48)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top, text="Status:").grid(row=0, column=0, padx=(12, 4), pady=10)
        ctk.CTkLabel(top, textvariable=self._status_var, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=4, pady=10
        )

        self.content = ctk.CTkFrame(main)
        self.content.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._pages["home"] = self._build_home(self.content)
        self._pages["draw"] = self._build_draw(self.content)
        self._pages["animate"] = self._build_animate(self.content)
        self._pages["bridge"] = self._build_bridge(self.content)
        self._pages["debug"] = self._build_debug(self.content)
        self._pages["settings"] = self._build_settings(self.content)

        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self._show_page("home")

    def _show_page(self, key: str) -> None:
        self._current_page = key
        page = self._pages[key]
        page.tkraise()
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=("gray75", "gray25"))
            else:
                btn.configure(fg_color="transparent")
        if key == "home":
            self._sync_preview()
        elif key == "draw" and self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        elif key == "animate":
            self._load_anim_panel_to_canvas()

    # ------------------------------------------------------------------ pages

    def _build_home(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Home", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(1, weight=1)

        preview_wrap = ctk.CTkFrame(body)
        preview_wrap.grid(row=0, column=0, rowspan=2, padx=16, pady=16)
        ctk.CTkLabel(preview_wrap, text="Display preview").pack(anchor="w", padx=8, pady=(8, 0))
        self._preview = MatrixCanvas(
            preview_wrap,
            cell_size=26,
            editable=False,
            preview_style=self._preview_style,
        )
        self._preview.pack(padx=8, pady=8)
        self._preview.set_pixels(self.pixels)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="nw", padx=12, pady=24)
        for text, cmd in (
            ("Edit", lambda: self._show_page("draw")),
            ("Power On", self._on_power_on),
            ("Power Off", self._on_power_off),
            ("Enable Proxy", self._on_enable_proxy),
            ("Clear Screen", self._on_clear_screen),
        ):
            ctk.CTkButton(actions, text=text, width=180, height=36, command=cmd).pack(
                anchor="w", pady=6
            )

        tip = ctk.CTkLabel(
            body,
            text="Preview mirrors the current canvas. Clear Screen paints the matrix black.",
            text_color=("gray35", "gray65"),
            wraplength=360,
            justify="left",
        )
        tip.grid(row=1, column=1, sticky="sw", padx=12, pady=24)
        return page

    def _build_draw(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Draw", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(body)
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)

        self._color_preview = ctk.CTkButton(
            toolbar,
            text="",
            width=40,
            height=28,
            fg_color=rgb_to_hex(*self.paint_color),
            hover=False,
            command=self._pick_color,
        )
        self._color_preview.pack(side="left", padx=(8, 4), pady=8)
        ctk.CTkButton(toolbar, text="Pick Color", width=90, command=self._pick_color).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Eraser", width=80, command=self._use_eraser).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Clear", width=80, command=self._clear_canvas).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Upload Image...", command=self._upload_image).pack(
            side="left", padx=(16, 4), pady=8
        )
        ctk.CTkButton(toolbar, text="Save…", width=70, command=self._export_drawing).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkLabel(toolbar, text="Preset").pack(side="left", padx=(16, 4), pady=8)
        ctk.CTkOptionMenu(
            toolbar,
            variable=self._draw_preset_var,
            values=[p.label for p in DRAWING_PRESETS],
            width=150,
        ).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(toolbar, text="Apply", width=70, command=self._apply_drawing_preset).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkSwitch(
            toolbar, text="Live update", variable=self.live_update, command=self._on_live_toggled
        ).pack(side="left", padx=(16, 4), pady=8)
        ctk.CTkButton(toolbar, text="Send to Display", width=140, command=self._send_frame).pack(
            side="right", padx=8, pady=8
        )

        canvas_wrap = ctk.CTkFrame(body)
        canvas_wrap.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        body.grid_rowconfigure(1, weight=1)
        self._draw_canvas = MatrixCanvas(
            canvas_wrap,
            cell_size=30,
            editable=True,
            get_color=lambda: self.paint_color,
            on_paint=self._on_draw_paint,
            on_paint_end=self._on_draw_paint_end,
            preview_style=self._preview_style,
        )
        self._draw_canvas.pack(padx=12, pady=12)
        self._draw_canvas.set_pixels(self.pixels)
        return page

    def _build_animate(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Animate", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        panels = ctk.CTkScrollableFrame(body, width=220)
        panels.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(10, 6), pady=10)
        ctk.CTkLabel(panels, text="Panels").pack(anchor="w", padx=10, pady=(10, 4))
        self._anim_list = ctk.CTkOptionMenu(
            panels,
            variable=self._anim_panel_var,
            values=["Panel 1"],
            command=self._on_anim_panel_selected,
            width=190,
        )
        self._anim_list.pack(padx=10, pady=4)
        self._anim_thumb_strip = ctk.CTkScrollableFrame(panels, height=130, width=190)
        self._anim_thumb_strip.pack(fill="x", padx=6, pady=4)
        ctk.CTkButton(panels, text="Add Panel", command=self._anim_add).pack(
            fill="x", padx=10, pady=(8, 4)
        )
        ctk.CTkButton(panels, text="Duplicate", command=self._anim_duplicate).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkButton(panels, text="Delete", command=self._anim_delete).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkSwitch(
            panels,
            text="Preview before play",
            variable=self._preview_before_play,
        ).pack(anchor="w", padx=10, pady=(14, 4))
        ctk.CTkButton(panels, text="Preview", command=self._anim_preview).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkButton(panels, text="Play on Display", command=self._anim_play).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkButton(panels, text="Stop", command=self._anim_stop).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkLabel(panels, text="Presets").pack(anchor="w", padx=10, pady=(14, 4))
        ctk.CTkOptionMenu(
            panels,
            variable=self._anim_preset_var,
            values=[p.label for p in ANIMATION_PRESETS],
            width=190,
        ).pack(padx=10, pady=4)
        ctk.CTkButton(
            panels, text="Apply Preset", command=self._apply_animation_preset
        ).pack(fill="x", padx=10, pady=(8, 4))

        toolbar = ctk.CTkFrame(body)
        toolbar.grid(row=0, column=1, sticky="ew", padx=(6, 10), pady=10)
        self._anim_color_preview = ctk.CTkButton(
            toolbar,
            text="",
            width=40,
            height=28,
            fg_color=rgb_to_hex(*self.paint_color),
            hover=False,
            command=self._pick_color,
        )
        self._anim_color_preview.pack(side="left", padx=(8, 4), pady=8)
        ctk.CTkButton(toolbar, text="Pick Color", width=90, command=self._pick_color).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Eraser", width=80, command=self._use_eraser).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Clear Panel", width=100, command=self._anim_clear_panel).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Upload Image...", command=self._anim_upload).pack(
            side="left", padx=(12, 4), pady=8
        )
        ctk.CTkButton(toolbar, text="Import…", width=80, command=self._import_animation).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkButton(toolbar, text="Save…", width=70, command=self._export_animation).pack(
            side="left", padx=4, pady=8
        )
        ctk.CTkLabel(
            toolbar,
            text="Speed uses Settings → Limit animation speed",
            text_color=("gray35", "gray65"),
        ).pack(side="right", padx=10)

        canvas_wrap = ctk.CTkFrame(body)
        canvas_wrap.grid(row=1, column=1, sticky="nsew", padx=(6, 10), pady=(0, 10))
        self._animate_canvas = MatrixCanvas(
            canvas_wrap,
            cell_size=28,
            editable=True,
            get_color=lambda: self.paint_color,
            on_paint=self._on_anim_paint,
            on_paint_end=self._on_anim_paint_end,
            preview_style=self._preview_style,
        )
        self._animate_canvas.pack(padx=12, pady=12)
        self._refresh_anim_panel_menu()
        return page

    def _build_bridge(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(page, text="BLE Bridge", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkLabel(
            page,
            text="Run a WebSocket proxy on this machine so another PC can control the display over the LAN.",
            text_color=("gray35", "gray65"),
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        body = ctk.CTkFrame(page)
        body.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(body, text="Bind host").grid(row=0, column=0, padx=12, pady=8, sticky="w")
        host_row = ctk.CTkFrame(body, fg_color="transparent")
        host_row.grid(row=0, column=1, padx=8, pady=8, sticky="w")
        ctk.CTkEntry(host_row, textvariable=self.bridge_host, width=180).pack(side="left")
        ctk.CTkButton(
            host_row,
            text="Local IP",
            width=90,
            command=self._fill_bridge_local_ip,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(body, text="Port").grid(row=1, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkEntry(body, textvariable=self.bridge_port, width=100).grid(
            row=1, column=1, padx=8, pady=8, sticky="w"
        )
        ctk.CTkLabel(body, text="Token (optional)").grid(
            row=2, column=0, padx=12, pady=8, sticky="w"
        )
        ctk.CTkEntry(body, textvariable=self.bridge_token, width=220).grid(
            row=2, column=1, padx=8, pady=8, sticky="w"
        )

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=8)
        self._bridge_start_btn = ctk.CTkButton(
            btns, text="Start Bridge", width=130, command=self._bridge_start
        )
        self._bridge_start_btn.pack(side="left", padx=(0, 8))
        self._bridge_stop_btn = ctk.CTkButton(
            btns, text="Stop Bridge", width=130, command=self._bridge_stop, state="disabled"
        )
        self._bridge_stop_btn.pack(side="left", padx=4)

        client = ctk.CTkFrame(body)
        client.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=8)
        ctk.CTkLabel(client, text="GUI client connection", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        row = ctk.CTkFrame(client, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkLabel(row, text="Mode").pack(side="left", padx=(0, 6))
        self.mode_menu = ctk.CTkOptionMenu(
            row,
            values=["Local BLE", "BLE Proxy"],
            variable=self.connection_mode,
            width=120,
            command=self._on_mode_changed,
        )
        self.mode_menu.pack(side="left", padx=4)
        ctk.CTkLabel(row, text="Proxy host").pack(side="left", padx=(16, 6))
        self.host_entry = ctk.CTkEntry(row, textvariable=self.proxy_host, width=130)
        self.host_entry.pack(side="left", padx=4)
        ctk.CTkLabel(row, text="Port").pack(side="left", padx=(10, 6))
        self.port_entry = ctk.CTkEntry(row, textvariable=self.proxy_port, width=70)
        self.port_entry.pack(side="left", padx=4)
        ctk.CTkLabel(row, text="Token").pack(side="left", padx=(10, 6))
        self.token_entry = ctk.CTkEntry(row, textvariable=self.proxy_token, width=120)
        self.token_entry.pack(side="left", padx=4)
        ctk.CTkButton(
            row,
            text="Scan for sessions",
            width=140,
            command=self._scan_bridge_sessions,
        ).pack(side="left", padx=(12, 0))
        self._update_proxy_fields()

        self._bridge_log = ctk.CTkTextbox(body, height=180)
        self._bridge_log.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=12, pady=12)
        self._bridge_log.insert("end", "Bridge log…\n")
        self._bridge_log.configure(state="disabled")
        return page

    def _build_debug(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkLabel(header, text="BLE Debugging", font=ctk.CTkFont(size=22, weight="bold")).pack(
            side="left"
        )

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.pack(side="right")
        ctk.CTkButton(btns, text="Copy Log", width=100, command=self._copy_debug).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            btns, text="Open in Terminal", width=140, command=self._open_debug_terminal
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Clear Log", width=100, command=self._clear_debug).pack(
            side="left", padx=4
        )

        self._debug_textbox = ctk.CTkTextbox(page, font=ctk.CTkFont(family="Menlo", size=12))
        self._debug_textbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._debug_textbox.insert("end", "Verbose BLE / proxy traffic will appear here.\n")
        # Keep editable state so the user can select/copy text; block typing.
        self._debug_textbox.bind("<Key>", self._debug_textbox_key)
        ctk.CTkLabel(
            page,
            textvariable=self._debug_status_var,
            text_color=("gray35", "gray65"),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        return page

    def _build_settings(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Settings", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        scroll = ctk.CTkScrollableFrame(page)
        scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        scroll.grid_columnconfigure(0, weight=1)

        self._setting_vars: dict[str, tk.Variable] = {
            "start_on_boot": tk.BooleanVar(value=self.settings.start_on_boot),
            "start_minimized": tk.BooleanVar(value=self.settings.start_minimized),
            "power_off_on_logoff": tk.BooleanVar(value=self.settings.power_off_on_logoff),
            "fade_on_power_off": tk.BooleanVar(value=self.settings.fade_on_power_off),
            "fade_on_power_on": tk.BooleanVar(value=self.settings.fade_on_power_on),
            "led_preview": tk.BooleanVar(value=self.settings.led_preview),
        }
        self._anim_ms_var = tk.StringVar(value=str(self.settings.animation_frame_ms))
        self._live_ms_var = tk.StringVar(value=str(self.settings.live_update_ms))
        self._preview_gamma_var = tk.StringVar(value=str(self.settings.preview_gamma))
        self._preview_brightness_var = tk.StringVar(value=str(self.settings.preview_brightness))
        self._preview_saturation_var = tk.StringVar(value=str(self.settings.preview_saturation))
        self._preview_yellow_var = tk.StringVar(value=str(self.settings.preview_yellow_push))
        self._preview_bloom_var = tk.StringVar(value=str(self.settings.preview_bloom))

        specs = [
            (
                "start_on_boot",
                "Start on boot",
                "Starts the app when you turn your device on.",
            ),
            (
                "start_minimized",
                "Start minimized",
                "When the app starts on boot, it will start minimized and run in the background.",
            ),
            (
                "power_off_on_logoff",
                "Power off display on logoff",
                "Before you shut down or quit the app, blank the display (fade optional). "
                "If this is off, the app leaves your last drawing on the panel instead — "
                "recommended, because blanking/disconnect often makes the matrix fall back "
                "to a saved manufacturer image. USB power may still be connected either way.",
            ),
            (
                "fade_on_power_off",
                "Fade on power off",
                "Fades the LED display by changing the brightness of the colors before powering "
                "off the screen. It will still be receiving power if the USB is still plugged in; "
                "the app will just color the screen black.",
            ),
            (
                "fade_on_power_on",
                "Fade on power on",
                "Fades the LED display by changing the brightness of the last used colors before "
                "powering on.",
            ),
            (
                "led_preview",
                "LED-accurate preview",
                "Makes the on-screen canvas look closer to the physical matrix (brighter midtones, "
                "warmer yellow-green cast, diffuser bloom). This only changes the preview — the "
                "RGB values sent to the display stay the same.",
            ),
        ]

        for key, title, desc in specs:
            self._add_setting_toggle(scroll, key, title, desc)

        self._add_setting_ms(
            scroll,
            "Limit animation speed",
            "Milliseconds to wait between animation frames. Minimum is 10 ms — lower values "
            "cause pixel shifts and laggy updates on this display.",
            self._anim_ms_var,
        )
        self._add_setting_ms(
            scroll,
            "Limit live update speed",
            "Milliseconds to wait between live drawing updates. Minimum is 10 ms — lower "
            "values cause pixel shifts and laggy updates on this display.",
            self._live_ms_var,
        )
        self._add_setting_float(
            scroll,
            "Preview gamma",
            "Lower values brighten midtones to match how LEDs read on camera (typical 0.6–0.85).",
            self._preview_gamma_var,
        )
        self._add_setting_float(
            scroll,
            "Preview brightness",
            "Overall preview brightness multiplier (1.0 = unchanged).",
            self._preview_brightness_var,
        )
        self._add_setting_float(
            scroll,
            "Preview saturation",
            "How punchy colors look in the preview (1.0 = unchanged).",
            self._preview_saturation_var,
        )
        self._add_setting_float(
            scroll,
            "Preview yellow push",
            "How strongly greens shift toward the warm yellow-green cast of the panel (0–0.5).",
            self._preview_yellow_var,
        )
        self._add_setting_float(
            scroll,
            "Preview bloom",
            "Diffuser glow between neighboring cells (0 = sharp rounded LEDs, no color shadow).",
            self._preview_bloom_var,
        )

        ctk.CTkButton(scroll, text="Save Settings", width=140, command=self._save_settings_ui).pack(
            anchor="w", padx=12, pady=16
        )
        return page

    def _add_setting_toggle(
        self, parent: ctk.CTkScrollableFrame, key: str, title: str, desc: str
    ) -> None:
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", padx=8, pady=8)
        ctk.CTkSwitch(box, text=title, variable=self._setting_vars[key]).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkLabel(
            box, text=desc, wraplength=700, justify="left", text_color=("gray35", "gray65")
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _add_setting_ms(
        self, parent: ctk.CTkScrollableFrame, title: str, desc: str, var: tk.StringVar
    ) -> None:
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", padx=8, pady=8)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(row, text=title, font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkEntry(row, textvariable=var, width=90).pack(side="right")
        ctk.CTkLabel(row, text="ms").pack(side="right", padx=(0, 6))
        ctk.CTkLabel(
            box, text=desc, wraplength=700, justify="left", text_color=("gray35", "gray65")
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _add_setting_float(
        self, parent: ctk.CTkScrollableFrame, title: str, desc: str, var: tk.StringVar
    ) -> None:
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", padx=8, pady=8)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(row, text=title, font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkEntry(row, textvariable=var, width=90).pack(side="right")
        ctk.CTkLabel(
            box, text=desc, wraplength=700, justify="left", text_color=("gray35", "gray65")
        ).pack(anchor="w", padx=12, pady=(0, 10))

    # ------------------------------------------------------------------ shared helpers

    def _schedule_workspace_save(self) -> None:
        """Debounced autosave of drawing + animation panels."""
        if self._closing:
            return
        if self._workspace_save_job is not None:
            try:
                self.after_cancel(self._workspace_save_job)
            except Exception:
                pass
        self._workspace_save_job = self.after(800, self._save_workspace_now)

    def _save_workspace_now(self) -> None:
        self._workspace_save_job = None
        try:
            self._sync_pixels_from_draw()
            self._save_anim_canvas_to_panel()
            save_workspace(
                self.pixels,
                self.anim_frames,
                self.anim_index,
                # Only resume static panel loops — live presets (clock/metrics) aren't stored.
                animation_playing=self._anim_playing and self._live_preset_fn is None,
            )
        except Exception as exc:
            self._append_debug(f"Workspace save failed: {exc}")

    def _sync_pixels_from_draw(self) -> None:
        if self._draw_canvas is not None:
            self.pixels = self._draw_canvas.get_pixels()

    def _sync_preview(self) -> None:
        self._sync_pixels_from_draw()
        if self._preview is not None:
            self._preview.set_pixels(self.pixels)

    def _set_paint_previews(self) -> None:
        color = rgb_to_hex(*self.paint_color)
        if self._color_preview is not None:
            self._color_preview.configure(fg_color=color)
        if self._anim_color_preview is not None:
            self._anim_color_preview.configure(fg_color=color)

    def _pick_color(self) -> None:
        initial = rgb_to_hex(*self.paint_color)
        result = colorchooser.askcolor(color=initial, title="Paint color")
        if not result or not result[0]:
            return
        r, g, b = (int(c) for c in result[0])
        self.paint_color = (r, g, b)
        self._set_paint_previews()

    def _use_eraser(self) -> None:
        self.paint_color = (0, 0, 0)
        self._set_paint_previews()

    def _queue_status(self, message: str) -> None:
        self.after(0, lambda m=message: self._set_status(m))

    def _debug_textbox_key(self, event) -> Optional[str]:
        """Allow navigation/copy shortcuts; block typing into the debug log."""
        # Cmd (macOS) or Ctrl modifiers for copy/select-all/find-ish keys.
        if event.state & (0x4 | 0x8):  # Control | Mod1/Command
            if event.keysym.lower() in ("c", "a"):
                return None
        if event.keysym in (
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Next",
            "Prior",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Meta_L",
            "Meta_R",
            "Alt_L",
            "Alt_R",
            "Caps_Lock",
            "Escape",
            "Tab",
        ):
            return None
        return "break"

    def _queue_debug(self, message: str) -> None:
        """Batch debug lines onto the Tk thread — per-message after(0) burns CPU."""
        pending = self._debug_pending
        pending.append(message)
        if len(pending) > 120:
            dropped = len(pending) - 40
            self._debug_pending = pending[-40:]
            self._debug_pending.insert(0, f"… dropped {dropped} debug lines (high rate)")
        if self._debug_flush_job is None:
            self._debug_flush_job = self.after(200, self._flush_debug)

    def _flush_debug(self) -> None:
        self._debug_flush_job = None
        batch = self._debug_pending
        self._debug_pending = []
        if not batch:
            return
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines = [f"[{stamp}] {message}" for message in batch]
        self._debug_lines.extend(lines)
        if len(self._debug_lines) > self._debug_max:
            self._debug_lines = self._debug_lines[-self._debug_max :]
        text = "\n".join(lines) + "\n"
        if self._debug_file_ready:
            try:
                with self._debug_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                pass
        if self._debug_textbox is None:
            return
        self._debug_textbox.insert("end", text)
        self._debug_textbox.see("end")

    def _ensure_debug_log_file(self) -> Path:
        path = self._debug_log_path
        if not self._debug_file_ready:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(self._debug_lines) + ("\n" if self._debug_lines else ""),
                encoding="utf-8",
            )
            self._debug_file_ready = True
        return path

    def _append_debug(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{stamp}] {message}"
        self._debug_lines.append(line)
        if len(self._debug_lines) > self._debug_max:
            self._debug_lines = self._debug_lines[-self._debug_max :]
        if self._debug_file_ready:
            try:
                with self._debug_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass
        if self._debug_textbox is None:
            return
        self._debug_textbox.insert("end", line + "\n")
        self._debug_textbox.see("end")

    def _debug_text(self) -> str:
        return "\n".join(self._debug_lines) + ("\n" if self._debug_lines else "")

    def _copy_debug(self) -> None:
        text = self._debug_text()
        if not text.strip():
            self._debug_status_var.set("Log is empty — nothing to copy.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
            self._debug_status_var.set(f"Copied {len(self._debug_lines)} log line(s) to clipboard.")
        except Exception as exc:
            self._debug_status_var.set(f"Copy failed: {exc}")

    def _open_debug_terminal(self) -> None:
        path = self._ensure_debug_log_file()
        # Refresh file contents from the in-memory ring buffer before opening.
        try:
            path.write_text(self._debug_text(), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("BLE Debugging", f"Could not write debug log:\n{exc}")
            return

        quoted = shlex.quote(str(path))
        system = platform.system()
        try:
            if system == "Darwin":
                script = (
                    'tell application "Terminal"\n'
                    "    activate\n"
                    f'    do script "echo \\"MI LED BLE debug — Ctrl-C to stop\\"; '
                    f'tail -n +1 -f {quoted}"\n'
                    "end tell\n"
                )
                subprocess.Popen(["osascript", "-e", script])
            elif system == "Windows":
                # PowerShell Get-Content -Wait is the usual live-tail equivalent.
                ps = (
                    f"Write-Host 'MI LED BLE debug — Ctrl-C to stop'; "
                    f"Get-Content -Path '{path}' -Wait"
                )
                subprocess.Popen(
                    ["cmd", "/c", "start", "MI LED BLE Debug", "powershell", "-NoExit", "-Command", ps],
                    shell=False,
                )
            else:
                cmd = f"echo 'MI LED BLE debug — Ctrl-C to stop'; tail -n +1 -f {quoted}"
                for terminal in (
                    ["x-terminal-emulator", "-e", "bash", "-lc", cmd],
                    ["gnome-terminal", "--", "bash", "-lc", cmd],
                    ["konsole", "-e", "bash", "-lc", cmd],
                    ["xterm", "-e", "bash", "-lc", cmd],
                ):
                    try:
                        subprocess.Popen(terminal)
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise RuntimeError("No terminal emulator found")
        except Exception as exc:
            messagebox.showerror("BLE Debugging", f"Could not open terminal:\n{exc}")
            return

        self._debug_terminal_opened = True
        self._debug_status_var.set(f"Live log: {path}")

    def _clear_debug(self) -> None:
        self._debug_lines.clear()
        if self._debug_file_ready:
            try:
                self._debug_log_path.write_text("", encoding="utf-8")
            except Exception:
                pass
        if self._debug_textbox is None:
            return
        self._debug_textbox.delete("1.0", "end")
        self._debug_status_var.set("Log cleared.")

    def _bridge_append(self, message: str) -> None:
        if self._bridge_log is None:
            return
        self._bridge_log.configure(state="normal")
        self._bridge_log.insert("end", message + "\n")
        self._bridge_log.see("end")
        self._bridge_log.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)
        connected = self.device.is_connected
        self.connect_btn.configure(state="disabled" if connected or self._busy else "normal")
        self.disconnect_btn.configure(state="normal" if connected else "disabled")
        if hasattr(self, "mode_menu"):
            self.mode_menu.configure(
                state="disabled" if connected or self._busy else "normal"
            )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.connect_btn.configure(
            state="disabled" if busy or self.device.is_connected else "normal"
        )
        if hasattr(self, "mode_menu"):
            self.mode_menu.configure(
                state="disabled" if busy or self.device.is_connected else "normal"
            )

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
                # Don't enter graffiti here — init clears the panel. Live draws
                # enter graffiti on demand and restore the canvas afterward.
                self.after(50, self._restore_display_after_connect)
            else:
                self._set_status("Not connected — click Connect to retry")

        self.after(100, self._poll_future, fut, done)

    def _frame_has_content(self, frame: list[tuple[int, int, int]]) -> bool:
        return any(px != (0, 0, 0) for px in frame)

    def _animation_has_content(self) -> bool:
        return any(self._frame_has_content(panel) for panel in self.anim_frames)

    def _restore_display_after_connect(self) -> None:
        """Push the last drawing, or resume the last animation, onto the panel."""
        if not self.device.is_connected:
            return

        # Only resume animation if it was playing and panels aren't empty —
        # otherwise a blank panel loop will wipe the display continuously.
        if self._resume_animation_on_connect and self._animation_has_content():
            self._resume_animation_on_connect = False
            self._set_status("Resuming last animation…")
            self._anim_play()
            return

        self._resume_animation_on_connect = False
        self._anim_stop()
        self._sync_pixels_from_draw()
        frame = list(self.pixels)
        if not self._frame_has_content(frame):
            self._set_status(f"Connected to {self.device.device_label}")
            return

        fut = self.device.send_frame(frame)

        def done() -> None:
            try:
                fut.result(timeout=30)
                self._note_display_frame(frame)
                self._set_status(f"Restored last drawing on {self.device.device_label}")
            except Exception as exc:
                self._set_status(f"Connected, but restore failed: {exc}")

        self._set_status("Restoring last drawing…")
        self.after(100, self._poll_future, fut, done)

    def _on_disconnect(self) -> None:
        self._stop_keepalive()
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

    def _require_connection(self) -> bool:
        if self.device.is_connected:
            return True
        messagebox.showinfo("Not connected", "Connect to the MI Matrix Display first.")
        return False

    # ------------------------------------------------------------------ power / clear

    def _fade_kwargs(self) -> dict:
        return {
            "steps": self.settings.fade_steps,
            "step_delay": self.settings.fade_step_ms / 1000.0,
        }

    def _on_power_on(self) -> None:
        if not self._require_connection():
            return
        self._sync_pixels_from_draw()
        frame = list(self.pixels)

        async def sequence():
            backend = self.device._device
            # Never leave POWER_ON as the last command — it reveals the
            # manufacturer app's stored gallery image. Send our frame after.
            if self.settings.fade_on_power_on:
                await backend.fade_frame(frame, to_black=False, **self._fade_kwargs())
            else:
                await backend.send_frame(frame)

        fut = self.device.submit(sequence())
        self._powered_on = True
        self._display_frame = frame
        self._start_keepalive("frame")

        def done() -> None:
            try:
                fut.result(timeout=60)
                self._set_status("Power on")
            except Exception as exc:
                self._set_status(f"Power on failed: {exc}")

        self.after(100, self._poll_future, fut, done)

    def _on_power_off(self) -> None:
        if not self._require_connection():
            return
        self._sync_pixels_from_draw()
        frame = list(self.pixels)

        async def sequence():
            backend = self.device._device
            # Blank only. Raw BLE POWER_OFF restores the saved gallery image.
            if self.settings.fade_on_power_off:
                await backend.fade_frame(frame, to_black=True, **self._fade_kwargs())
            await backend.power_off()

        fut = self.device.submit(sequence())
        self._powered_on = False
        self._display_frame = blank_frame()
        self._start_keepalive("blank")

        def done() -> None:
            try:
                fut.result(timeout=60)
                self._set_status("Power off")
            except Exception as exc:
                self._set_status(f"Power off failed: {exc}")

        self.after(100, self._poll_future, fut, done)

    def _stop_keepalive(self) -> None:
        self._keepalive_mode = None
        if self._keepalive_job is not None:
            try:
                self.after_cancel(self._keepalive_job)
            except Exception:
                pass
            self._keepalive_job = None

    def _start_keepalive(self, mode: str) -> None:
        """Hold a full-frame or blank on the panel so gallery content doesn't return."""
        self._keepalive_mode = mode
        if self._keepalive_job is not None:
            try:
                self.after_cancel(self._keepalive_job)
            except Exception:
                pass
            self._keepalive_job = None
        if self.device.is_connected and mode in ("blank", "frame"):
            self._keepalive_job = self.after(4000, self._keepalive_tick)

    def _keepalive_tick(self) -> None:
        self._keepalive_job = None
        mode = self._keepalive_mode
        if (
            mode is None
            or not self.device.is_connected
            or self._anim_playing
            or self._busy
        ):
            if mode is not None and self.device.is_connected:
                self._keepalive_job = self.after(4000, self._keepalive_tick)
            return
        # Live graffiti drawing owns the link — never overwrite it with a full frame.
        if self.live_update.get() and mode != "blank":
            self._keepalive_job = self.after(4000, self._keepalive_tick)
            return
        frame = blank_frame() if mode == "blank" else list(self._display_frame)
        self.device.send_frame(frame)
        self._keepalive_job = self.after(4000, self._keepalive_tick)

    def _note_display_frame(self, frame: list[tuple[int, int, int]]) -> None:
        self._display_frame = list(frame)
        self._powered_on = True
        # Full-frame send exits graffiti mode; hold that image only when live
        # update is off. Live drawing should not get periodic full-frame refreshes.
        if self.live_update.get():
            self._stop_keepalive()
        else:
            self._start_keepalive("frame")

    def _on_clear_screen(self) -> None:
        self.pixels = blank_frame()
        if self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        self._sync_preview()
        self._schedule_workspace_save()
        if not self.device.is_connected:
            self._set_status("Canvas cleared (not connected)")
            return
        fut = self.device.clear_screen()
        self._powered_on = False
        self._display_frame = blank_frame()
        self._start_keepalive("blank")
        self.after(
            100,
            self._poll_future,
            fut,
            lambda: self._set_status("Screen cleared"),
        )

    def _on_enable_proxy(self) -> None:
        self._show_page("bridge")
        if not self._bridge_running:
            self._bridge_start()

    # ------------------------------------------------------------------ draw

    def _on_draw_paint(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        self.pixels[y * MATRIX_SIZE + x] = color
        if self._preview is not None:
            self._preview.set_pixel(x, y, color)
        if not self.live_update.get() or not self.device.is_connected:
            return
        # Drawing must win over a background animation / blank keepalive loop.
        if self._anim_playing:
            self._anim_stop()
        self._stop_keepalive()
        self._powered_on = True
        self._stroke_dirty = True
        self._schedule_live_frame()

    def _on_draw_paint_end(self) -> None:
        self._stroke_dirty = False
        self._schedule_workspace_save()
        if not self.live_update.get() or not self.device.is_connected:
            return
        if self._anim_playing:
            self._anim_stop()
        if self._live_frame_job is not None:
            try:
                self.after_cancel(self._live_frame_job)
            except Exception:
                pass
            self._live_frame_job = None
        self._send_live_frame(force=True)

    def _schedule_live_frame(self) -> None:
        if self._live_frame_job is not None:
            return
        delay = max(10, int(self.settings.live_update_ms))
        self._live_frame_job = self.after(delay, self._send_live_frame)

    def _send_live_frame(self, force: bool = False) -> None:
        self._live_frame_job = None
        if not self.live_update.get() or not self.device.is_connected:
            self._live_frame_queued = False
            return
        if self._anim_playing:
            # Never fight the animation loop with live canvas frames.
            self._live_frame_queued = False
            return
        if self._live_frame_inflight:
            self._live_frame_queued = True
            return

        self._sync_pixels_from_draw()
        frame = list(self.pixels)
        fut = self.device.send_frame(frame)
        self._live_frame_inflight = True
        self._last_live_send = time.monotonic()

        def done() -> None:
            self._live_frame_inflight = False
            try:
                fut.result(timeout=30)
                self._note_display_frame(frame)
            except Exception as exc:
                self._set_status(f"Live update failed: {exc}")
                return
            if self._anim_playing:
                self._live_frame_queued = False
                return
            if self._live_frame_queued or (not force and self._stroke_dirty):
                self._live_frame_queued = False
                self._schedule_live_frame()

        self.after(50, self._poll_future, fut, done)

    def _clear_canvas(self) -> None:
        self.pixels = blank_frame()
        if self._draw_canvas is not None:
            self._draw_canvas.clear()
        self._sync_preview()
        self._schedule_workspace_save()
        if self.live_update.get() and self.device.is_connected:
            self._send_frame()

    def _on_live_toggled(self) -> None:
        if self.live_update.get() and self.device.is_connected:
            self._stop_keepalive()
            # Push the current canvas once when enabling live mode.
            self._send_live_frame(force=True)

    def _send_frame(self) -> None:
        if not self._require_connection():
            return
        if self._anim_playing:
            self._anim_stop()
        self._sync_pixels_from_draw()
        frame = list(self.pixels)
        fut = self.device.send_frame(frame)

        def done() -> None:
            try:
                fut.result(timeout=30)
                self._note_display_frame(frame)
                self._set_status("Frame sent to display")
                # Do not enter graffiti mode here — those init commands wipe the
                # full-frame image. Live pixel draws will enter graffiti on demand.
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
        if self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        self._sync_preview()
        self._set_status(f"Loaded image: {path}")
        self._schedule_workspace_save()
        if self.live_update.get() and self.device.is_connected:
            self._send_frame()

    # ------------------------------------------------------------------ animate

    def _refresh_anim_panel_menu(self) -> None:
        values = [f"Panel {i + 1}" for i in range(len(self.anim_frames))]
        self._anim_list.configure(values=values)
        self._anim_panel_var.set(values[self.anim_index])
        self._refresh_anim_thumbs()

    def _refresh_anim_thumbs(self) -> None:
        strip = self._anim_thumb_strip
        if strip is None:
            return
        for child in strip.winfo_children():
            child.destroy()
        self._anim_thumbs = []

        # Lay thumbs out in a wrapping grid inside the scrollable strip.
        cols = 2
        for i, frame in enumerate(self.anim_frames):
            wrap = ctk.CTkFrame(strip, fg_color="transparent")
            wrap.grid(row=i // cols, column=i % cols, padx=4, pady=4)

            def make_click(idx: int):
                return lambda: self._select_anim_panel(idx)

            thumb = MatrixThumb(wrap, cell_size=5, on_click=make_click(i))
            thumb.pack()
            thumb.set_pixels(frame)
            thumb.set_selected(i == self.anim_index)
            ctk.CTkLabel(wrap, text=str(i + 1), font=ctk.CTkFont(size=11)).pack()
            self._anim_thumbs.append(thumb)

    def _select_anim_panel(self, index: int) -> None:
        if not (0 <= index < len(self.anim_frames)):
            return
        self._save_anim_canvas_to_panel()
        self.anim_index = index
        self._anim_panel_var.set(f"Panel {index + 1}")
        self._load_anim_panel_to_canvas()
        for i, thumb in enumerate(self._anim_thumbs):
            thumb.set_selected(i == index)
        self._schedule_workspace_save()

    def _on_anim_panel_selected(self, value: str) -> None:
        try:
            idx = int(value.split()[-1]) - 1
        except ValueError:
            return
        self._select_anim_panel(idx)

    def _save_anim_canvas_to_panel(self) -> None:
        if self._animate_canvas is None:
            return
        # Never clobber stored panels with a live/preview playback frame.
        if self._anim_playing:
            return
        if 0 <= self.anim_index < len(self.anim_frames):
            self.anim_frames[self.anim_index] = self._animate_canvas.get_pixels()
            if self.anim_index < len(self._anim_thumbs):
                self._anim_thumbs[self.anim_index].set_pixels(self.anim_frames[self.anim_index])

    def _load_anim_panel_to_canvas(self) -> None:
        if self._animate_canvas is None:
            return
        self._animate_canvas.set_pixels(self.anim_frames[self.anim_index])
        for i, thumb in enumerate(self._anim_thumbs):
            thumb.set_selected(i == self.anim_index)
    def _anim_add(self) -> None:
        self._save_anim_canvas_to_panel()
        self.anim_frames.append(blank_frame())
        self.anim_index = len(self.anim_frames) - 1
        self._refresh_anim_panel_menu()
        self._load_anim_panel_to_canvas()
        self._schedule_workspace_save()

    def _anim_duplicate(self) -> None:
        self._save_anim_canvas_to_panel()
        self.anim_frames.append(list(self.anim_frames[self.anim_index]))
        self.anim_index = len(self.anim_frames) - 1
        self._refresh_anim_panel_menu()
        self._load_anim_panel_to_canvas()
        self._schedule_workspace_save()

    def _anim_delete(self) -> None:
        if len(self.anim_frames) <= 1:
            messagebox.showinfo("Animate", "Keep at least one panel.")
            return
        del self.anim_frames[self.anim_index]
        self.anim_index = min(self.anim_index, len(self.anim_frames) - 1)
        self._refresh_anim_panel_menu()
        self._load_anim_panel_to_canvas()
        self._schedule_workspace_save()

    def _anim_clear_panel(self) -> None:
        self.anim_frames[self.anim_index] = blank_frame()
        self._load_anim_panel_to_canvas()
        if self.anim_index < len(self._anim_thumbs):
            self._anim_thumbs[self.anim_index].set_pixels(self.anim_frames[self.anim_index])
        self._schedule_workspace_save()

    def _anim_upload(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an image for this panel",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.anim_frames[self.anim_index] = load_image_as_matrix(path)
        except Exception as exc:
            messagebox.showerror("Image error", f"Could not load image:\n{exc}")
            return
        self._load_anim_panel_to_canvas()
        if self.anim_index < len(self._anim_thumbs):
            self._anim_thumbs[self.anim_index].set_pixels(self.anim_frames[self.anim_index])
        self._schedule_workspace_save()
    def _on_anim_paint(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        self.anim_frames[self.anim_index][y * MATRIX_SIZE + x] = color

    def _on_anim_paint_end(self) -> None:
        self._save_anim_canvas_to_panel()
        self._schedule_workspace_save()

    def _anim_preview(self) -> None:
        """Loop on the canvas only — does not require BLE or touch the display."""
        was_live = self._live_preset_fn is not None
        self._anim_stop()
        if was_live:
            self._load_anim_panel_to_canvas()
        else:
            self._save_anim_canvas_to_panel()
        self._start_animation(preview_only=True)

    def _anim_play(self) -> None:
        if not self._require_connection():
            return
        was_live = self._live_preset_fn is not None
        self._anim_stop()
        if was_live:
            # Re-run the active live preset on the device if we still have one labeled;
            # otherwise play stored panels.
            if self._active_preset_label:
                preset = ANIMATION_BY_LABEL.get(self._active_preset_label)
                if preset is not None and preset.kind == "live" and preset.build_live is not None:
                    self._start_animation(
                        preview_only=False,
                        live_fn=preset.build_live,
                        frame_ms=preset.frame_ms,
                        label=preset.label,
                    )
                    return
            self._load_anim_panel_to_canvas()
        else:
            self._save_anim_canvas_to_panel()
        self._start_animation(preview_only=False)

    def _start_animation(
        self,
        *,
        preview_only: bool,
        live_fn=None,
        frame_ms: Optional[int] = None,
        label: Optional[str] = None,
    ) -> None:
        self._anim_stop()
        self._anim_generation += 1
        gen = self._anim_generation
        if not preview_only:
            self._stop_keepalive()
            self._powered_on = True
            self._resume_animation_on_connect = False
        self._anim_preview_only = preview_only
        self._live_preset_fn = live_fn
        self._live_preset_tick = 0
        self._live_preset_ms = frame_ms
        self._last_sent_frame = None
        self._last_ui_frame = None
        self._last_ui_frame_time = 0.0
        self._anim_playing = True
        if label:
            self._active_preset_label = label
        if not preview_only and live_fn is None:
            self._schedule_workspace_save()
        if preview_only:
            self._set_status(
                f"Previewing{f': {label}' if label else ''} — click Play on Display to send"
            )
        elif label:
            self._set_status(f"Playing preset: {label}")
        else:
            self._set_status("Animation playing on display…")
        self._anim_tick(0, gen)

    def _anim_play_live(self, frame_fn, *, frame_ms: Optional[int] = None, label: str = "Live") -> None:
        preview = bool(self._preview_before_play.get())
        if preview:
            self._start_animation(
                preview_only=True, live_fn=frame_fn, frame_ms=frame_ms, label=label
            )
            return
        if not self._require_connection():
            return
        self._start_animation(
            preview_only=False, live_fn=frame_fn, frame_ms=frame_ms, label=label
        )

    def _anim_stop(self) -> None:
        was_playing = self._anim_playing
        was_preview = self._anim_preview_only
        self._anim_playing = False
        self._anim_preview_only = False
        self._live_preset_fn = None
        self._live_preset_ms = None
        self._last_sent_frame = None
        self._last_ui_frame = None
        self._anim_generation += 1
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        if was_playing and not was_preview and not self._closing:
            self._schedule_workspace_save()

    def _update_anim_ui_frame(self, frame: list[tuple[int, int, int]]) -> None:
        """Throttle expensive canvas redraws during playback (~8–10 UI fps)."""
        if self._last_ui_frame is not None and frame == self._last_ui_frame:
            return
        now = time.monotonic()
        if (now - self._last_ui_frame_time) < 0.12:
            return
        self._last_ui_frame_time = now
        self._last_ui_frame = frame
        if self._animate_canvas is not None:
            self._animate_canvas.set_pixels_fast(frame)
        if self._preview is not None and self._current_page == "home":
            self._preview.set_pixels_fast(frame)

    def _anim_tick(self, index: int, generation: Optional[int] = None) -> None:
        if not self._anim_playing:
            return
        if generation is not None and generation != self._anim_generation:
            return

        preview_only = self._anim_preview_only
        if not preview_only and not self.device.is_connected:
            self._anim_stop()
            self._set_status("Animation stopped — disconnected")
            return

        if self._live_preset_fn is not None:
            try:
                frame = self._live_preset_fn(self._live_preset_tick)
            except Exception as exc:
                self._anim_stop()
                self._set_status(f"Live preset error: {exc}")
                return
            self._live_preset_tick += 1
            delay = max(10, int(self._live_preset_ms or self.settings.animation_frame_ms))
            next_index = index
        else:
            if not self.anim_frames:
                self._anim_stop()
                return
            frame = self.anim_frames[index % len(self.anim_frames)]
            delay = max(10, int(self._live_preset_ms or self.settings.animation_frame_ms))
            next_index = (index + 1) % len(self.anim_frames)

        self._update_anim_ui_frame(frame)

        if preview_only:
            # Local preview does not need 100 Hz ticks — that alone pegs a core.
            preview_delay = max(delay, 50)
            self._anim_job = self.after(preview_delay, self._anim_tick, next_index, generation)
            return

        if self._last_sent_frame is not None and frame == self._last_sent_frame:
            self._anim_job = self.after(delay, self._anim_tick, next_index, generation)
            return

        fut = self.device.send_frame(frame)
        self._last_sent_frame = list(frame)
        self._display_frame = list(frame)
        self._powered_on = True

        def after_send() -> None:
            if not self._anim_playing:
                return
            if generation is not None and generation != self._anim_generation:
                return
            try:
                fut.result(timeout=0)
            except Exception as exc:
                self._anim_stop()
                self._set_status(f"Animation error: {exc}")
                return
            self._anim_job = self.after(delay, self._anim_tick, next_index, generation)

        self.after(40, self._poll_future, fut, after_send)

    def _apply_drawing_preset(self) -> None:
        preset = DRAWING_BY_LABEL.get(self._draw_preset_var.get())
        if preset is None:
            return
        if self._anim_playing:
            self._anim_stop()
        self.pixels = preset.build()
        if self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        if self._preview is not None and self._current_page == "home":
            self._preview.set_pixels(self.pixels)
        self._schedule_workspace_save()
        self._set_status(f"Applied drawing preset: {preset.label}")
        if self.live_update.get() and self.device.is_connected:
            self._send_frame()

    def _replace_anim_panels(
        self,
        frames: list[list[tuple[int, int, int]]],
        *,
        label: Optional[str] = None,
    ) -> None:
        """Atomically replace animation panels and refresh all UI chrome."""
        if not frames:
            raise ValueError("no frames")
        self._anim_stop()
        self.anim_frames = [list(f) for f in frames]
        self.anim_index = 0
        self._active_preset_label = label
        self._refresh_anim_panel_menu()
        self._load_anim_panel_to_canvas()
        self._schedule_workspace_save()

    def _apply_animation_preset(self) -> None:
        preset = ANIMATION_BY_LABEL.get(self._anim_preset_var.get())
        if preset is None:
            return

        # Always halt the previous preset/tick stream first.
        self._anim_stop()

        if preset.kind == "live":
            if preset.build_live is None:
                return
            try:
                snapshot = preset.build_live(0)
            except Exception as exc:
                messagebox.showerror("Animate", f"Could not build preset:\n{exc}")
                return
            self._replace_anim_panels([snapshot], label=preset.label)
            self._set_status(f"Loaded live preset: {preset.label}")
            self._anim_play_live(
                preset.build_live,
                frame_ms=preset.frame_ms,
                label=preset.label,
            )
            return

        if preset.build_static is None:
            return
        try:
            frames = preset.build_static()
        except Exception as exc:
            messagebox.showerror("Animate", f"Could not build preset:\n{exc}")
            return
        if not frames:
            messagebox.showwarning("Animate", "Preset produced no frames.")
            return

        self._replace_anim_panels(frames, label=preset.label)
        self._set_status(f"Loaded preset: {preset.label} ({len(frames)} frames)")

        if self._preview_before_play.get():
            self._start_animation(
                preview_only=True,
                frame_ms=preset.frame_ms,
                label=preset.label,
            )
            return

        if not self.device.is_connected:
            messagebox.showinfo(
                "Animate",
                f"Loaded “{preset.label}” ({len(frames)} frames). Connect, then Play on Display.",
            )
            return

        self._start_animation(
            preview_only=False,
            frame_ms=preset.frame_ms,
            label=preset.label,
        )

    def _export_drawing(self) -> None:
        self._sync_pixels_from_draw()
        path = filedialog.asksaveasfilename(
            title="Save drawing",
            defaultextension=".png",
            filetypes=[
                ("PNG image", "*.png"),
                ("Python script", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                save_drawing_python(path, self.pixels, name=Path(path).stem)
            else:
                if not lower.endswith(".png"):
                    path = path + ".png"
                save_frame_png(path, self.pixels, scale=16)
            self._set_status(f"Saved drawing: {path}")
        except Exception as exc:
            messagebox.showerror("Save drawing", f"Could not save:\n{exc}")

    def _export_animation(self) -> None:
        if self._anim_playing and self._live_preset_fn is None:
            self._anim_stop()
            self._save_anim_canvas_to_panel()
        elif not self._anim_playing:
            self._save_anim_canvas_to_panel()

        path = filedialog.asksaveasfilename(
            title="Save animation",
            defaultextension=".gif",
            filetypes=[
                ("GIF animation", "*.gif"),
                ("ZIP of PNGs", "*.zip"),
                ("Python script", "*.py"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        frames = [list(f) for f in self.anim_frames]
        frame_ms = int(self.settings.animation_frame_ms)
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                save_animation_python(
                    path, frames, frame_ms=frame_ms, name=Path(path).stem
                )
            elif lower.endswith(".zip"):
                save_animation_zip(path, frames, scale=16)
            else:
                if not lower.endswith(".gif"):
                    path = path + ".gif"
                save_animation_gif(path, frames, frame_ms=frame_ms, scale=16)
            self._set_status(f"Saved animation ({len(frames)} panels): {path}")
        except Exception as exc:
            messagebox.showerror("Save animation", f"Could not save:\n{exc}")

    def _import_animation(self) -> None:
        path = filedialog.askopenfilename(
            title="Import animation",
            filetypes=[
                ("GIF / ZIP", "*.gif *.zip"),
                ("GIF animation", "*.gif"),
                ("ZIP of PNGs", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".zip"):
                frames = load_animation_zip(path)
            else:
                frames = load_animation_gif(path)
        except Exception as exc:
            messagebox.showerror("Import animation", f"Could not import:\n{exc}")
            return
        self._replace_anim_panels(frames, label=None)
        self._set_status(f"Imported {len(frames)} panels from {Path(path).name}")
        if self._preview_before_play.get():
            self._start_animation(preview_only=True)

    def _lan_addresses(self) -> list[str]:
        addrs: list[str] = []
        preferred = self._preferred_lan_ip()
        if preferred:
            addrs.append(preferred)
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except Exception:
            pass
        return addrs

    def _preferred_lan_ip(self) -> Optional[str]:
        """Best-effort primary LAN IPv4 (does not send traffic)."""
        return preferred_lan_ip()

    def _discovery_info(self) -> dict:
        port = DEFAULT_PROXY_PORT
        try:
            port = int(self.bridge_port.get().strip() or DEFAULT_PROXY_PORT)
        except ValueError:
            pass
        return {
            "name": socket.gethostname() or "MI LED GUI",
            "ip": preferred_lan_ip() or "",
            "port": port,
            "bridge": bool(self._bridge_running),
            "auth_required": bool(self.bridge_token.get().strip()),
        }

    def _fill_bridge_local_ip(self) -> None:
        ip = self._preferred_lan_ip()
        if not ip:
            addrs = self._lan_addresses()
            ip = addrs[0] if addrs else None
        if not ip:
            messagebox.showwarning("BLE Bridge", "Could not detect a local LAN IP.")
            return
        self.bridge_host.set(ip)
        self._bridge_append(f"Bind host set to local IP: {ip}")

    def _scan_bridge_sessions(self) -> None:
        self._bridge_append("Scanning LAN for MI LED sessions…")
        self._set_status("Scanning LAN for sessions…")

        def worker() -> None:
            try:
                sessions = scan_sessions(timeout=1.8)
            except Exception as exc:
                self.after(0, lambda: self._session_scan_failed(str(exc)))
                return
            self.after(0, lambda: self._show_session_scan_results(sessions))

        threading.Thread(target=worker, name="mi-led-session-scan", daemon=True).start()

    def _session_scan_failed(self, error: str) -> None:
        self._bridge_append(f"Session scan failed: {error}")
        self._set_status(f"Session scan failed: {error}")
        messagebox.showerror("Scan for sessions", f"Could not scan the LAN:\n{error}")

    def _show_session_scan_results(self, sessions: list[SessionInfo]) -> None:
        own_ips = set(self._lan_addresses())
        preferred = self._preferred_lan_ip()
        if preferred:
            own_ips.add(preferred)

        if self._session_scan_win is not None:
            try:
                self._session_scan_win.destroy()
            except Exception:
                pass
            self._session_scan_win = None

        win = ctk.CTkToplevel(self)
        self._session_scan_win = win
        win.title("Available sessions")
        win.geometry("560x420")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="MI LED sessions on your local network",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(
            win,
            text="Select a host to fill Proxy host / Port. Bridge sessions can accept a GUI client.",
            text_color=("gray35", "gray65"),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        if not sessions:
            ctk.CTkLabel(
                win,
                text="No sessions found.\nMake sure the other PC has the app open on the same LAN\n"
                "(and allow UDP port 8766 through the firewall if needed).",
                justify="left",
            ).pack(anchor="w", padx=16, pady=20)
            ctk.CTkButton(win, text="Close", width=100, command=win.destroy).pack(
                pady=(8, 16)
            )
            self._bridge_append("Session scan: no hosts found")
            self._set_status("No LAN sessions found")
            return

        scroll = ctk.CTkScrollableFrame(win, height=280)
        scroll.pack(fill="both", expand=True, padx=12, pady=8)

        bridges = 0
        for session in sessions:
            is_self = session.ip in own_ips
            if session.bridge:
                bridges += 1
            row = ctk.CTkFrame(scroll)
            row.pack(fill="x", padx=4, pady=4)
            role = "Bridge" if session.bridge else "App only"
            auth = " · token required" if session.auth_required else ""
            self_tag = " · this PC" if is_self else ""
            detail = f"{session.ip}:{session.port}  ·  {role}{auth}{self_tag}"
            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(
                text_col, text=session.name, font=ctk.CTkFont(weight="bold"), anchor="w"
            ).pack(anchor="w")
            ctk.CTkLabel(
                text_col,
                text=detail,
                text_color=("gray35", "gray65"),
                anchor="w",
            ).pack(anchor="w")
            ctk.CTkButton(
                row,
                text="Use",
                width=70,
                command=lambda s=session: self._use_discovered_session(s, win),
            ).pack(side="right", padx=10, pady=8)

        self._bridge_append(
            f"Session scan: found {len(sessions)} host(s), {bridges} with bridge"
        )
        self._set_status(f"Found {len(sessions)} LAN session(s)")

        ctk.CTkButton(win, text="Close", width=100, command=win.destroy).pack(pady=(4, 14))

    def _use_discovered_session(self, session: SessionInfo, win: ctk.CTkToplevel) -> None:
        self.connection_mode.set("BLE Proxy")
        self.proxy_host.set(session.ip)
        self.proxy_port.set(str(session.port))
        self._update_proxy_fields()
        self._persist_connection_settings()
        note = ""
        if not session.bridge:
            note = " (host has the app open, but its bridge is not running yet)"
        elif session.auth_required:
            note = " — enter the shared token before connecting"
        self._bridge_append(f"Selected session {session.name} at {session.ip}:{session.port}{note}")
        self._set_status(f"Proxy set to {session.ip}:{session.port}")
        try:
            win.destroy()
        except Exception:
            pass
        self._session_scan_win = None

    def _persist_connection_settings(self) -> None:
        try:
            self.settings.connection_mode = "proxy" if self._is_proxy_mode() else "local"
            self.settings.proxy_host = self.proxy_host.get().strip() or "127.0.0.1"
            self.settings.proxy_token = self.proxy_token.get()
            try:
                self.settings.proxy_port = int(self.proxy_port.get().strip() or DEFAULT_PROXY_PORT)
            except ValueError:
                pass
            self.settings.bridge_bind_host = self.bridge_host.get().strip() or "0.0.0.0"
            self.settings.bridge_token = self.bridge_token.get()
            try:
                self.settings.bridge_port = int(self.bridge_port.get().strip() or DEFAULT_PROXY_PORT)
            except ValueError:
                pass
            save_settings(self.settings)
        except Exception:
            pass

    def _bridge_start(self) -> None:
        if self._bridge_running:
            return
        try:
            port = int(self.bridge_port.get().strip())
        except ValueError:
            messagebox.showerror("BLE Bridge", "Port must be a number.")
            return
        host = self.bridge_host.get().strip() or "0.0.0.0"
        token = self.bridge_token.get().strip() or None

        if self.device.is_connected and not self._is_proxy_mode():
            if not messagebox.askyesno(
                "BLE Bridge",
                "Starting the bridge will disconnect the local GUI BLE session "
                "(the proxy takes ownership of Bluetooth). Continue?",
            ):
                return
            self._on_disconnect()

        self._bridge_server = BleProxyServer(
            host=host, port=port, token=token, auto_connect_ble=True, advertise=False
        )
        loop = asyncio.new_event_loop()
        self._bridge_loop = loop

        def runner() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._bridge_server.run())
            except Exception as exc:
                self.after(0, lambda: self._bridge_append(f"Bridge error: {exc}"))
            finally:
                self.after(0, self._bridge_stopped_ui)

        self._bridge_thread = threading.Thread(target=runner, name="mi-led-bridge", daemon=True)
        self._bridge_thread.start()
        self._bridge_running = True
        self._bridge_start_btn.configure(state="disabled")
        self._bridge_stop_btn.configure(state="normal")
        self._bridge_append(f"Starting bridge on ws://{host}:{port}")
        for ip in self._lan_addresses():
            self._bridge_append(f"LAN URL: ws://{ip}:{port}")
        self._set_status("BLE Bridge running")

    def _bridge_stop(self) -> None:
        server = self._bridge_server
        loop = self._bridge_loop
        if server is None or loop is None:
            self._bridge_stopped_ui()
            return
        self._bridge_append("Stopping bridge…")
        try:
            loop.call_soon_threadsafe(server.request_stop)
        except Exception as exc:
            self._bridge_append(f"Stop failed: {exc}")

    def _bridge_stopped_ui(self) -> None:
        self._bridge_running = False
        self._bridge_server = None
        self._bridge_loop = None
        self._bridge_thread = None
        self._bridge_start_btn.configure(state="normal")
        self._bridge_stop_btn.configure(state="disabled")
        self._bridge_append("Bridge stopped")
        self._set_status("BLE Bridge stopped")

    # ------------------------------------------------------------------ settings

    def _save_settings_ui(self) -> None:
        try:
            anim_ms = int(self._anim_ms_var.get().strip())
            live_ms = int(self._live_ms_var.get().strip())
            preview_gamma = float(self._preview_gamma_var.get().strip())
            preview_brightness = float(self._preview_brightness_var.get().strip())
            preview_saturation = float(self._preview_saturation_var.get().strip())
            preview_yellow = float(self._preview_yellow_var.get().strip())
            preview_bloom = float(self._preview_bloom_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Settings", "Millisecond values must be integers; preview values must be numbers."
            )
            return

        prev_boot = self.settings.start_on_boot
        self.settings.start_on_boot = bool(self._setting_vars["start_on_boot"].get())
        self.settings.start_minimized = bool(self._setting_vars["start_minimized"].get())
        self.settings.power_off_on_logoff = bool(self._setting_vars["power_off_on_logoff"].get())
        self.settings.fade_on_power_off = bool(self._setting_vars["fade_on_power_off"].get())
        self.settings.fade_on_power_on = bool(self._setting_vars["fade_on_power_on"].get())
        self.settings.led_preview = bool(self._setting_vars["led_preview"].get())
        self.settings.animation_frame_ms = anim_ms
        self.settings.live_update_ms = live_ms
        self.settings.preview_gamma = preview_gamma
        self.settings.preview_brightness = preview_brightness
        self.settings.preview_saturation = preview_saturation
        self.settings.preview_yellow_push = preview_yellow
        self.settings.preview_bloom = preview_bloom
        self.settings.proxy_host = self.proxy_host.get().strip() or "127.0.0.1"
        try:
            self.settings.proxy_port = int(self.proxy_port.get().strip() or DEFAULT_PROXY_PORT)
            self.settings.bridge_port = int(self.bridge_port.get().strip() or DEFAULT_PROXY_PORT)
        except ValueError:
            messagebox.showerror("Settings", "Proxy/bridge ports must be numbers.")
            return
        self.settings.proxy_token = self.proxy_token.get()
        self.settings.bridge_bind_host = self.bridge_host.get().strip() or "0.0.0.0"
        self.settings.bridge_token = self.bridge_token.get()
        self.settings.connection_mode = "proxy" if self._is_proxy_mode() else "local"
        self.settings.clamp()
        save_settings(self.settings)
        self._apply_preview_style()

        if self.settings.start_on_boot != prev_boot or self.settings.start_on_boot:
            ok, msg = apply_start_on_boot(self.settings.start_on_boot)
            if not ok:
                messagebox.showwarning("Start on boot", msg)
            else:
                self._append_debug(msg)

        self._anim_ms_var.set(str(self.settings.animation_frame_ms))
        self._live_ms_var.set(str(self.settings.live_update_ms))
        self._preview_gamma_var.set(str(self.settings.preview_gamma))
        self._preview_brightness_var.set(str(self.settings.preview_brightness))
        self._preview_saturation_var.set(str(self.settings.preview_saturation))
        self._preview_yellow_var.set(str(self.settings.preview_yellow_push))
        self._preview_bloom_var.set(str(self.settings.preview_bloom))
        self._set_status("Settings saved")
        messagebox.showinfo("Settings", "Settings saved.")

    def _leave_last_frame_on_display(self) -> None:
        """Re-assert the last content so disconnect is less likely to show the gallery."""
        if not self.device.is_connected:
            return
        self._sync_pixels_from_draw()
        self._save_anim_canvas_to_panel()
        frame = list(self._display_frame)
        if all(c == (0, 0, 0) for c in frame):
            frame = list(self.pixels)
        try:
            fut = self.device.send_frame(frame)
            fut.result(timeout=15)
            self._display_frame = list(frame)
        except Exception:
            pass

    def _power_off_for_logoff(self) -> None:
        if self._closing:
            return
        if not self.device.is_connected:
            return
        if not self.settings.power_off_on_logoff:
            # Prefer leaving the user's art on the panel over blanking, which
            # often triggers the manufacturer gallery image after disconnect.
            self._leave_last_frame_on_display()
            return
        try:
            self._sync_pixels_from_draw()
            frame = list(self.pixels)

            async def sequence():
                backend = self.device._device
                if self.settings.fade_on_power_off:
                    await backend.fade_frame(frame, to_black=True, **self._fade_kwargs())
                await backend.power_off()

            fut = self.device.submit(sequence())
            fut.result(timeout=15)
        except Exception:
            pass

    def _on_close(self) -> None:
        # Capture play state before stopping so the next launch can resume.
        was_playing = self._anim_playing and self._live_preset_fn is None
        if self._workspace_save_job is not None:
            try:
                self.after_cancel(self._workspace_save_job)
            except Exception:
                pass
            self._workspace_save_job = None
        try:
            self._sync_pixels_from_draw()
            self._save_anim_canvas_to_panel()
            save_workspace(
                self.pixels,
                self.anim_frames,
                self.anim_index,
                animation_playing=was_playing,
            )
        except Exception:
            pass

        self._anim_stop()
        self._stop_keepalive()
        if self._live_frame_job is not None:
            try:
                self.after_cancel(self._live_frame_job)
            except Exception:
                pass
            self._live_frame_job = None
        if self._bridge_running:
            self._bridge_stop()
            time.sleep(0.2)
        try:
            self._session_beacon.stop()
        except Exception:
            pass
        self._power_off_for_logoff()
        self._closing = True
        try:
            # Persist connection prefs on exit
            self.settings.proxy_host = self.proxy_host.get().strip() or "127.0.0.1"
            self.settings.proxy_token = self.proxy_token.get()
            self.settings.connection_mode = "proxy" if self._is_proxy_mode() else "local"
            try:
                self.settings.proxy_port = int(self.proxy_port.get().strip() or DEFAULT_PROXY_PORT)
            except ValueError:
                pass
            save_settings(self.settings)
        except Exception:
            pass
        try:
            self.device.shutdown()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    print(f"MI LED GUI starting (rev {APP_REVISION})")
    app = MiLedApp()
    app.mainloop()


if __name__ == "__main__":
    main()
