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
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from .audio_capture import AudioCapture, audio_available
from .color_preview import PreviewStyle
from .device import DeviceController
from .discovery import (
    SessionBeacon,
    SessionInfo,
    detect_os_id,
    preferred_lan_ip,
    scan_sessions,
)
from .export_io import (
    load_animation_gif,
    load_animation_python,
    load_animation_zip,
    load_drawing_python,
    load_frame_png,
    save_animation_gif,
    save_animation_python,
    save_animation_zip,
    save_drawing_python,
    save_frame_png,
)
from .icons import action_icon, nav_icon, os_icon, ui_icon
from .image_convert import blank_frame, load_image_as_matrix
from .music_viz import MUSIC_MODE_LABELS, render_mode
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
from .text_render import apply_brightness, max_line_width, render_text_frame
from .version import (
    APP_REVISION,
    APP_VERSION,
    GITHUB_RELEASES_URL,
    GITHUB_URL,
    check_for_updates,
    license_text,
)
from .widgets import MatrixCanvas, MatrixThumb, rgb_to_hex


NAV_ITEMS = (
    ("home", "Home"),
    ("draw", "Draw"),
    ("animate", "Animate"),
    ("text", "Text"),
    ("music", "Music"),
    ("bridge", "BLE Bridge"),
    ("debug", "BLE Debugging"),
    ("settings", "Settings"),
    ("credits", "Credits"),
)


class MiLedApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"MI LED Display {APP_VERSION}")
        self.geometry("1040x720")
        self.minsize(900, 640)

        self.settings = load_settings()
        appearance = getattr(self.settings, "appearance_mode", "System") or "System"
        ctk.set_appearance_mode(appearance)
        ctk.set_default_color_theme("blue")
        self._appearance_var = tk.StringVar(value=appearance)
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
        self._nav_buttons: dict[str, ctk.CTkFrame] = {}
        self._icon_refs: list = []
        self._current_page = "home"
        self._preview: Optional[MatrixCanvas] = None
        self._draw_canvas: Optional[MatrixCanvas] = None
        self._animate_canvas: Optional[MatrixCanvas] = None
        self._music_canvas: Optional[MatrixCanvas] = None
        self._text_canvas: Optional[MatrixCanvas] = None
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

        # Music reactive state
        self._audio = AudioCapture()
        self._music_source = tk.StringVar(value="Microphone")
        self._music_mode = tk.StringVar(value=MUSIC_MODE_LABELS[0])
        self._music_sensitivity = tk.DoubleVar(value=1.0)
        self._music_level_var = tk.StringVar(value="Level: —")
        self._music_status_var = tk.StringVar(
            value=(
                "Audio ready"
                if audio_available()
                else "Install sounddevice + numpy for music modes"
            )
        )
        self._music_listening = False
        self._music_on_display = False
        self._music_preview_job: Optional[str] = None
        self._music_listen_btn: Optional[ctk.CTkButton] = None
        self._music_display_btn: Optional[ctk.CTkButton] = None

        # Shared playback controls (Draw / Animate / Text / Music)
        self._fx_speed = tk.DoubleVar(value=1.0)
        self._fx_brightness = tk.DoubleVar(value=1.0)

        # Text tab
        self._text_line1 = tk.StringVar(value="HELLO")
        self._text_line2 = tk.StringVar(value="")
        self._text_color = (255, 220, 40)
        self._text_scale_label = tk.StringVar(value="1")
        self._text_bg_mode = tk.StringVar(value="Solid")
        self._text_bg_color = (0, 0, 0)
        self._text_bg_frames: list[list[tuple[int, int, int]]] = [blank_frame()]
        self._text_scroll = tk.BooleanVar(value=True)
        self._text_playing = False
        self._text_job: Optional[str] = None
        self._text_tick = 0
        self._text_color_btn: Optional[ctk.CTkButton] = None
        self._text_bg_color_btn: Optional[ctk.CTkButton] = None
        self._text_play_btn: Optional[ctk.CTkButton] = None

        # Embedded BLE bridge
        self._bridge_server: Optional[BleProxyServer] = None
        self._bridge_thread: Optional[threading.Thread] = None
        self._bridge_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bridge_running = False
        self._session_beacon = SessionBeacon(self._discovery_info)
        self._session_scan_win: Optional[ctk.CTkToplevel] = None
        self._closing = False
        self._want_connection = False
        self._reconnect_job: Optional[str] = None
        self._connection_watch_job: Optional[str] = None

        self.device = DeviceController(
            on_status=self._queue_status,
            on_debug=self._queue_debug,
            on_connection_lost=self._queue_connection_lost,
        )

        self._preview_style = self._make_preview_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._session_beacon.start()

        # Defer minimize until after the Connection Notice — otherwise the modal
        # dialog is hidden and grab_set freezes the main window on "Starting…".
        self._defer_minimize = bool(self.settings.start_minimized)

        atexit.register(self._power_off_for_logoff)
        self.after(200, self._startup_sequence)
        self.after(2500, self._connection_watch)

    def _startup_sequence(self) -> None:
        self._maybe_show_connection_notice(on_done=self._after_connection_notice)

    def _after_connection_notice(self) -> None:
        # Never minimize immediately after the user just dismissed a dialog —
        # that makes the UI look like it disappeared. Only honor start_minimized
        # when the notice was skipped (already acknowledged).
        self._defer_minimize = False
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._auto_connect()

    def _maybe_show_connection_notice(self, *, on_done=None) -> None:
        if getattr(self.settings, "hide_connection_notice", False):
            if self._defer_minimize:
                self._defer_minimize = False
                try:
                    self.after(50, self.iconify)
                except Exception:
                    pass
            if on_done is not None:
                on_done()
            return

        self._set_status("Please read the Connection Notice…")
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass

        win = ctk.CTkToplevel(self)
        win.title("Connection Notice")
        win.geometry("560x380" if platform.system() == "Linux" else "560x320")
        win.transient(self)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.lift()
        win.focus_force()
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="Connection Notice",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 8))
        notice = (
            "If you have the official Matrix Panel Plus or MIMatrixPanel apps "
            "installed and opened, please close them as they are known to block "
            "other devices from connecting to the LED display. We recommend also "
            "just uninstalling them as they are not very good and extremely buggy "
            "and limited compared to what this tool and its CLI equivalent can do."
        )
        if platform.system() == "Linux":
            notice += (
                "\n\nLinux: BLE needs BlueZ. Make sure bluetooth is enabled "
                "(bluetoothctl power on) and your user can access it "
                "(often membership in the bluetooth group)."
            )
        ctk.CTkLabel(
            win,
            text=notice,
            wraplength=520,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        skip_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(win, text="Do not show again", variable=skip_var).pack(
            anchor="w", padx=18, pady=(0, 16)
        )

        def close() -> None:
            if skip_var.get():
                self.settings.hide_connection_notice = True
                try:
                    save_settings(self.settings)
                except Exception:
                    pass
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.attributes("-topmost", False)
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            if on_done is not None:
                on_done()

        ctk.CTkButton(win, text="OK", width=100, command=close).pack(pady=(4, 18))
        win.protocol("WM_DELETE_WINDOW", close)
        win.after(50, lambda: (win.lift(), win.focus_force()))

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
        for canvas in (
            self._preview,
            self._draw_canvas,
            self._animate_canvas,
            self._music_canvas,
            self._text_canvas,
        ):
            if canvas is not None:
                canvas.set_preview_style(self._preview_style)

    # ------------------------------------------------------------------ UI shell

    def _retain_icon(self, icon) -> object:
        if icon is not None:
            self._icon_refs.append(icon)
        return icon

    def _icon_btn(self, parent, *, text: str, command, icon: str | None = None, **kwargs):
        """Create a CTkButton with an optional semantic SVG-derived icon."""
        image = self._retain_icon(action_icon(icon, size=(15, 15))) if icon else None
        if image is not None:
            kwargs.setdefault("compound", "left")
            text = text.lstrip()
        kwargs.setdefault("height", 32)
        return ctk.CTkButton(parent, text=text, image=image, command=command, **kwargs)

    def _make_nav_item(self, parent: ctk.CTkFrame, key: str, label: str) -> ctk.CTkFrame:
        """Sidebar row with vertically centered icon + label."""
        row = ctk.CTkFrame(parent, height=40, corner_radius=8, fg_color="transparent")
        row.pack_propagate(False)

        icon = self._retain_icon(nav_icon(key, size=(18, 18)))
        icon_lbl = ctk.CTkLabel(row, text="", image=icon, width=22, height=22)
        icon_lbl.place(x=12, rely=0.5, anchor="w")

        text_lbl = ctk.CTkLabel(
            row,
            text=label,
            anchor="w",
            font=ctk.CTkFont(size=13),
            text_color=("gray10", "gray90"),
        )
        text_lbl.place(x=40, rely=0.5, anchor="w")

        def activate(_event=None, k=key) -> None:
            self._show_page(k)

        for widget in (row, icon_lbl, text_lbl):
            widget.bind("<Button-1>", activate)
            widget.configure(cursor="hand2")

        row._nav_icon = icon_lbl  # type: ignore[attr-defined]
        row._nav_text = text_lbl  # type: ignore[attr-defined]
        return row

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        # Spacer row after all nav items so Credits stays with the list.
        sidebar.grid_rowconfigure(len(NAV_ITEMS) + 2, weight=1)

        ctk.CTkLabel(
            sidebar, text="MI LED", font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(18, 0), sticky="w")
        ctk.CTkLabel(
            sidebar,
            text=f"v{APP_VERSION}",
            text_color=("gray40", "gray65"),
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, padx=16, pady=(0, 14), sticky="w")

        for i, (key, label) in enumerate(NAV_ITEMS):
            row = self._make_nav_item(sidebar, key, label)
            row.grid(row=i + 2, column=0, sticky="ew", padx=10, pady=2)
            self._nav_buttons[key] = row

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
        self._pages["text"] = self._build_text(self.content)
        self._pages["music"] = self._build_music(self.content)
        self._pages["bridge"] = self._build_bridge(self.content)
        self._pages["debug"] = self._build_debug(self.content)
        self._pages["settings"] = self._build_settings(self.content)
        self._pages["credits"] = self._build_credits(self.content)

        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self._show_page("home")

    def _show_page(self, key: str) -> None:
        self._current_page = key
        page = self._pages[key]
        page.tkraise()
        for k, row in self._nav_buttons.items():
            if k == key:
                row.configure(fg_color=("gray75", "gray25"))
            else:
                row.configure(fg_color="transparent")
        if key == "home":
            self._sync_preview()
        elif key == "draw" and self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        elif key == "animate":
            self._load_anim_panel_to_canvas()
        elif key == "music":
            self._music_status_var.set(
                self._audio.system_audio_hint()
                if self._music_source.get() == "System audio"
                else (
                    "Listening…"
                    if self._music_listening
                    else "Start listening to preview reactive modes on the matrix."
                )
            )
        elif key == "text":
            self._refresh_text_preview()

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
        for text, cmd, icon in (
            ("Edit", lambda: self._show_page("draw"), "palette"),
            ("Power On", self._on_power_on, "power"),
            ("Power Off", self._on_power_off, "power"),
            ("Enable Proxy", self._on_enable_proxy, "scan"),
            ("Clear Screen", self._on_clear_screen, "clear"),
        ):
            self._icon_btn(
                actions, text=text, command=cmd, icon=icon, width=180, height=36
            ).pack(anchor="w", pady=6)

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

        tools = ctk.CTkFrame(body)
        tools.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        self._color_preview = ctk.CTkButton(
            tools,
            text="",
            width=32,
            height=28,
            fg_color=rgb_to_hex(*self.paint_color),
            hover=False,
            command=self._pick_color,
        )
        self._color_preview.pack(side="left", padx=(8, 4), pady=6)
        self._icon_btn(
            tools, text="Color", command=self._pick_color, icon="palette", width=78
        ).pack(side="left", padx=3, pady=6)
        self._icon_btn(
            tools, text="Eraser", command=self._use_eraser, icon="eraser", width=82
        ).pack(side="left", padx=3, pady=6)
        self._icon_btn(
            tools, text="Clear", command=self._clear_canvas, icon="clear", width=78
        ).pack(side="left", padx=3, pady=6)
        self._icon_btn(
            tools, text="Upload", command=self._upload_image, icon="upload", width=86
        ).pack(side="left", padx=(12, 3), pady=6)
        self._icon_btn(
            tools, text="Save", command=self._export_drawing, icon="save", width=74
        ).pack(side="left", padx=3, pady=6)
        ctk.CTkLabel(tools, text="Preset").pack(side="left", padx=(12, 4), pady=6)
        ctk.CTkOptionMenu(
            tools,
            variable=self._draw_preset_var,
            values=[p.label for p in DRAWING_PRESETS],
            width=130,
        ).pack(side="left", padx=3, pady=6)
        self._icon_btn(
            tools, text="Apply", command=self._apply_drawing_preset, icon="apply", width=78
        ).pack(side="left", padx=3, pady=6)

        controls = ctk.CTkFrame(body)
        controls.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        ctk.CTkSwitch(
            controls, text="Live update", variable=self.live_update, command=self._on_live_toggled
        ).pack(side="left", padx=(8, 8), pady=6)
        self._pack_fx_sliders(controls)
        self._icon_btn(
            controls, text="Send to Display", command=self._send_frame, icon="play", width=150
        ).pack(side="right", padx=8, pady=6)

        canvas_wrap = ctk.CTkFrame(body)
        canvas_wrap.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        body.grid_rowconfigure(2, weight=1)
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
        self._icon_btn(panels, text="Add Panel", command=self._anim_add, icon="add").pack(
            fill="x", padx=10, pady=(8, 4)
        )
        self._icon_btn(panels, text="Duplicate", command=self._anim_duplicate, icon="copy").pack(
            fill="x", padx=10, pady=4
        )
        self._icon_btn(panels, text="Delete", command=self._anim_delete, icon="delete").pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkSwitch(
            panels,
            text="Preview before play",
            variable=self._preview_before_play,
        ).pack(anchor="w", padx=10, pady=(14, 4))
        self._icon_btn(panels, text="Preview", command=self._anim_preview, icon="play").pack(
            fill="x", padx=10, pady=4
        )
        self._icon_btn(panels, text="Play on Display", command=self._anim_play, icon="play").pack(
            fill="x", padx=10, pady=4
        )
        self._icon_btn(panels, text="Stop", command=self._anim_stop, icon="stop").pack(
            fill="x", padx=10, pady=4
        )
        self._pack_fx_sliders(panels, fill=True)
        ctk.CTkLabel(panels, text="Presets").pack(anchor="w", padx=10, pady=(14, 4))
        ctk.CTkOptionMenu(
            panels,
            variable=self._anim_preset_var,
            values=[p.label for p in ANIMATION_PRESETS],
            width=190,
        ).pack(padx=10, pady=4)
        self._icon_btn(
            panels, text="Apply Preset", command=self._apply_animation_preset, icon="apply"
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
        self._icon_btn(
            toolbar, text="Pick Color", command=self._pick_color, icon="palette", width=100
        ).pack(side="left", padx=4, pady=8)
        self._icon_btn(
            toolbar, text="Eraser", command=self._use_eraser, icon="eraser", width=90
        ).pack(side="left", padx=4, pady=8)
        self._icon_btn(
            toolbar, text="Clear Panel", command=self._anim_clear_panel, icon="clear", width=110
        ).pack(side="left", padx=4, pady=8)
        self._icon_btn(
            toolbar, text="Upload…", command=self._anim_upload, icon="upload", width=95
        ).pack(side="left", padx=(12, 4), pady=8)
        self._icon_btn(
            toolbar, text="Import…", command=self._import_animation, icon="import", width=95
        ).pack(side="left", padx=4, pady=8)
        self._icon_btn(
            toolbar, text="Save…", command=self._export_animation, icon="save", width=85
        ).pack(side="left", padx=4, pady=8)
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

    def _build_text(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Text", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        preview_wrap = ctk.CTkFrame(body)
        preview_wrap.grid(row=0, column=0, sticky="nw", padx=16, pady=16)
        ctk.CTkLabel(preview_wrap, text="LED preview (import only)").pack(
            anchor="w", padx=8, pady=(8, 0)
        )
        self._text_canvas = MatrixCanvas(
            preview_wrap,
            cell_size=28,
            editable=False,
            preview_style=self._preview_style,
        )
        self._text_canvas.pack(padx=8, pady=8)

        controls = ctk.CTkFrame(body)
        controls.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            controls,
            text="Overlay text on an imported solid color, image, or animation. "
            "The preview cannot be drawn on — use Import for backgrounds.",
            wraplength=440,
            justify="left",
            text_color=("gray35", "gray65"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))

        ctk.CTkLabel(controls, text="Line 1").grid(row=1, column=0, sticky="w", padx=12, pady=8)
        entry1 = ctk.CTkEntry(controls, textvariable=self._text_line1, width=260)
        entry1.grid(row=1, column=1, sticky="ew", padx=8, pady=8)
        entry1.bind("<KeyRelease>", lambda _e: self._refresh_text_preview())

        ctk.CTkLabel(controls, text="Line 2").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        entry2 = ctk.CTkEntry(controls, textvariable=self._text_line2, width=260)
        entry2.grid(row=2, column=1, sticky="ew", padx=8, pady=8)
        entry2.bind("<KeyRelease>", lambda _e: self._refresh_text_preview())

        ctk.CTkLabel(controls, text="Text color").grid(
            row=3, column=0, sticky="w", padx=12, pady=8
        )
        color_row = ctk.CTkFrame(controls, fg_color="transparent")
        color_row.grid(row=3, column=1, sticky="w", padx=8, pady=8)
        self._text_color_btn = ctk.CTkButton(
            color_row,
            text="",
            width=40,
            height=28,
            fg_color=rgb_to_hex(*self._text_color),
            hover=False,
            command=self._pick_text_color,
        )
        self._text_color_btn.pack(side="left")
        self._icon_btn(
            color_row, text="Pick…", command=self._pick_text_color, icon="palette", width=80
        ).pack(side="left", padx=8)

        ctk.CTkLabel(controls, text="Size").grid(row=4, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkOptionMenu(
            controls,
            variable=self._text_scale_label,
            values=["0.5", "0.7", "1", "2", "3"],
            width=80,
            command=self._on_text_scale_changed,
        ).grid(row=4, column=1, sticky="w", padx=8, pady=8)

        ctk.CTkLabel(controls, text="Background").grid(
            row=5, column=0, sticky="w", padx=12, pady=8
        )
        bg_row = ctk.CTkFrame(controls, fg_color="transparent")
        bg_row.grid(row=5, column=1, sticky="w", padx=8, pady=8)
        ctk.CTkOptionMenu(
            bg_row,
            variable=self._text_bg_mode,
            values=["Solid", "Image", "Animation"],
            width=120,
            command=self._on_text_bg_mode_changed,
        ).pack(side="left")
        self._text_bg_color_btn = ctk.CTkButton(
            bg_row,
            text="",
            width=40,
            height=28,
            fg_color=rgb_to_hex(*self._text_bg_color),
            hover=False,
            command=self._pick_text_bg_color,
        )
        self._text_bg_color_btn.pack(side="left", padx=(8, 4))
        self._icon_btn(
            bg_row,
            text="Import…",
            command=self._text_import_background,
            icon="import",
            width=100,
        ).pack(side="left", padx=4)

        ctk.CTkSwitch(
            controls,
            text="Scroll when a line is wider than the panel",
            variable=self._text_scroll,
            command=self._refresh_text_preview,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        self._pack_fx_sliders(controls, grid=True, row=7)

        btns = ctk.CTkFrame(controls, fg_color="transparent")
        btns.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 16))
        self._icon_btn(
            btns,
            text="Refresh Preview",
            command=self._refresh_text_preview,
            icon="apply",
            width=140,
        ).pack(side="left", padx=(0, 8))
        self._text_play_btn = self._icon_btn(
            btns,
            text="Play on Display",
            command=self._text_toggle_play,
            icon="play",
            width=150,
        )
        self._text_play_btn.pack(side="left", padx=4)

        self._refresh_text_preview()
        return page

    def _build_music(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Music", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        preview_wrap = ctk.CTkFrame(body)
        preview_wrap.grid(row=0, column=0, rowspan=2, padx=16, pady=16, sticky="nw")
        ctk.CTkLabel(preview_wrap, text="LED preview").pack(anchor="w", padx=8, pady=(8, 0))
        self._music_canvas = MatrixCanvas(
            preview_wrap,
            cell_size=28,
            editable=False,
            preview_style=self._preview_style,
        )
        self._music_canvas.pack(padx=8, pady=8)
        self._music_canvas.set_pixels(blank_frame())
        ctk.CTkLabel(
            preview_wrap,
            textvariable=self._music_level_var,
            text_color=("gray35", "gray65"),
        ).pack(anchor="w", padx=8, pady=(0, 8))

        controls = ctk.CTkFrame(body)
        controls.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            controls,
            text="Reactive visuals from microphone or system audio. "
            "The panel updates ~5×/sec (BLE limit); preview is faster.",
            wraplength=420,
            justify="left",
            text_color=("gray35", "gray65"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))

        ctk.CTkLabel(controls, text="Source").grid(row=1, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkOptionMenu(
            controls,
            variable=self._music_source,
            values=["Microphone", "System audio"],
            width=180,
            command=self._on_music_source_changed,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=8)

        ctk.CTkLabel(controls, text="Mode").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        ctk.CTkOptionMenu(
            controls,
            variable=self._music_mode,
            values=MUSIC_MODE_LABELS,
            width=180,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=8)

        ctk.CTkLabel(controls, text="Sensitivity").grid(
            row=3, column=0, sticky="w", padx=12, pady=8
        )
        ctk.CTkSlider(
            controls,
            from_=0.4,
            to=2.2,
            number_of_steps=18,
            variable=self._music_sensitivity,
            width=220,
        ).grid(row=3, column=1, sticky="w", padx=8, pady=8)

        self._pack_fx_sliders(controls, grid=True, row=4)

        btns = ctk.CTkFrame(controls, fg_color="transparent")
        btns.grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))
        self._music_listen_btn = self._icon_btn(
            btns,
            text="Start Listening",
            command=self._music_toggle_listen,
            icon="listen",
            width=150,
        )
        self._music_listen_btn.pack(side="left", padx=(0, 8))
        self._music_display_btn = self._icon_btn(
            btns,
            text="Play on Display",
            command=self._music_toggle_display,
            icon="play",
            width=150,
            state="disabled",
        )
        self._music_display_btn.pack(side="left", padx=4)

        ctk.CTkLabel(
            controls,
            textvariable=self._music_status_var,
            wraplength=420,
            justify="left",
            text_color=("gray35", "gray65"),
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 16))
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
        self._icon_btn(
            host_row,
            text="Local IP",
            command=self._fill_bridge_local_ip,
            icon="scan",
            width=100,
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
        self._bridge_start_btn = self._icon_btn(
            btns, text="Start Bridge", command=self._bridge_start, icon="play", width=140
        )
        self._bridge_start_btn.pack(side="left", padx=(0, 8))
        self._bridge_stop_btn = self._icon_btn(
            btns,
            text="Stop Bridge",
            command=self._bridge_stop,
            icon="stop",
            width=140,
            state="disabled",
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
        self._icon_btn(
            row,
            text="Scan for sessions",
            command=self._scan_bridge_sessions,
            icon="scan",
            width=155,
        ).pack(side="left", padx=(12, 0))
        self.connect_btn = self._icon_btn(
            row, text="Connect", command=self._on_connect, icon="play", width=110
        )
        self.connect_btn.pack(side="left", padx=(8, 0))
        self.disconnect_btn = self._icon_btn(
            row,
            text="Disconnect",
            command=self._on_disconnect,
            icon="stop",
            width=120,
            state="disabled",
        )
        self.disconnect_btn.pack(side="left", padx=(8, 0))
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
        self._icon_btn(
            btns, text="Copy Log", command=self._copy_debug, icon="copy", width=110
        ).pack(side="left", padx=4)
        self._icon_btn(
            btns,
            text="Open in Terminal",
            command=self._open_debug_terminal,
            icon="terminal",
            width=155,
        ).pack(side="left", padx=4)
        self._icon_btn(
            btns, text="Clear Log", command=self._clear_debug, icon="clear", width=110
        ).pack(side="left", padx=4)

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

        # Appearance
        theme_box = ctk.CTkFrame(scroll)
        theme_box.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(
            theme_box, text="Appearance", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            theme_box,
            text="Light, Dark, or follow the system setting (default).",
            wraplength=700,
            justify="left",
            text_color=("gray35", "gray65"),
        ).pack(anchor="w", padx=12, pady=(0, 6))
        theme_row = ctk.CTkFrame(theme_box, fg_color="transparent")
        theme_row.pack(anchor="w", padx=12, pady=(0, 12))
        theme_menu = ctk.CTkOptionMenu(
            theme_row,
            values=["System", "Light", "Dark"],
            variable=self._appearance_var,
            width=140,
            command=self._on_appearance_changed,
        )
        theme_menu.pack(side="left")
        for mode, icon_key in (
            ("System", "system"),
            ("Light", "sun"),
            ("Dark", "moon"),
        ):
            icon = self._retain_icon(action_icon(icon_key, size=(16, 16)))
            if icon is not None:
                ctk.CTkLabel(theme_row, text="", image=icon).pack(side="left", padx=(10, 0))

        # Updates
        update_box = ctk.CTkFrame(scroll)
        update_box.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(
            update_box, text="Updates", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            update_box,
            text=f"Current version: {APP_VERSION}",
            text_color=("gray35", "gray65"),
        ).pack(anchor="w", padx=12, pady=(0, 6))
        update_row = ctk.CTkFrame(update_box, fg_color="transparent")
        update_row.pack(anchor="w", padx=12, pady=(0, 12))
        self._icon_btn(
            update_row,
            text="Check for Updates",
            command=self._check_for_updates,
            icon="update",
            width=180,
        ).pack(side="left")

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
                "Starts the app when you log in (macOS Login Item, Windows Startup, "
                "or Linux XDG autostart).",
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

    def _build_credits(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(page, text="Credits", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        body = ctk.CTkFrame(page)
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            body,
            text=f"MI LED Display  ·  version {APP_VERSION}",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 6))
        ctk.CTkLabel(
            body,
            text="Desktop GUI and BLE toolkit for the Merkury Innovations Matrix LED Display.",
            wraplength=720,
            justify="left",
            text_color=("gray35", "gray65"),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))

        links = ctk.CTkFrame(body, fg_color="transparent")
        links.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 12))
        self._icon_btn(
            links,
            text="View on GitHub",
            command=lambda: webbrowser.open(GITHUB_URL),
            icon="github",
            width=180,
        ).pack(side="left", padx=(0, 8))
        self._icon_btn(
            links,
            text="Check for Updates",
            command=self._check_for_updates,
            icon="update",
            width=180,
        ).pack(side="left", padx=4)

        license_box = ctk.CTkFrame(body)
        license_box.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))
        license_box.grid_columnconfigure(0, weight=1)
        license_box.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(license_box, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        lic_icon = self._retain_icon(action_icon("license", size=(16, 16)))
        if lic_icon is not None:
            ctk.CTkLabel(header, text="", image=lic_icon).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(header, text="License", font=ctk.CTkFont(weight="bold")).pack(side="left")
        text = ctk.CTkTextbox(license_box, font=ctk.CTkFont(family="Menlo", size=12))
        text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        text.insert("end", license_text())
        text.configure(state="disabled")
        return page

    def _on_appearance_changed(self, value: str | None = None) -> None:
        mode = (value or self._appearance_var.get() or "System").strip().title()
        if mode not in ("System", "Light", "Dark"):
            mode = "System"
        self._appearance_var.set(mode)
        ctk.set_appearance_mode(mode)
        self.settings.appearance_mode = mode
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def _check_for_updates(self) -> None:
        self._set_status("Checking GitHub for updates…")

        def worker() -> None:
            try:
                info = check_for_updates()
            except Exception as exc:
                self.after(0, lambda: self._update_check_failed(str(exc)))
                return
            self.after(0, lambda: self._show_update_result(info))

        threading.Thread(target=worker, name="mi-led-update-check", daemon=True).start()

    def _update_check_failed(self, error: str) -> None:
        self._set_status(f"Update check failed: {error}")
        go = messagebox.askyesno(
            "Check for Updates",
            f"Could not reach GitHub releases:\n{error}\n\n"
            "Open the releases page in your browser instead?",
        )
        if go:
            webbrowser.open(GITHUB_RELEASES_URL)

    def _show_update_result(self, info) -> None:
        if info.newer:
            self._set_status(f"Update available: {info.latest}")
            go = messagebox.askyesno(
                "Update available",
                f"A newer release is available.\n\n"
                f"Current: {info.current}\n"
                f"Latest: {info.latest} ({info.release_name})\n\n"
                f"Open the release page on GitHub?",
            )
            if go:
                webbrowser.open(info.release_url)
        else:
            self._set_status(f"Up to date ({info.current})")
            messagebox.showinfo(
                "Check for Updates",
                f"You're on the latest version ({info.current}).",
            )

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

    def _queue_connection_lost(self) -> None:
        self.after(0, self._on_connection_lost)

    def _stop_reconnect(self) -> None:
        if self._reconnect_job is not None:
            try:
                self.after_cancel(self._reconnect_job)
            except Exception:
                pass
            self._reconnect_job = None

    def _schedule_reconnect(self, delay_ms: int = 5000) -> None:
        if self._closing or not self._want_connection:
            return
        if self._reconnect_job is not None:
            return
        self._reconnect_job = self.after(max(0, int(delay_ms)), self._reconnect_tick)

    def _on_connection_lost(self) -> None:
        if self._closing or not self._want_connection:
            return
        if self.device.is_connected:
            return
        self._stop_keepalive()
        if self._anim_playing and not self._anim_preview_only:
            self._anim_stop()
        self._set_status("Connection lost — reconnecting in 5s…")
        self._schedule_reconnect(5000)

    def _connection_watch(self) -> None:
        """Fallback poll in case Bleak/proxy callbacks miss a drop."""
        self._connection_watch_job = None
        if self._closing:
            return
        try:
            if (
                self._want_connection
                and not self._busy
                and not self.device.is_connected
                and self._reconnect_job is None
            ):
                self._on_connection_lost()
        finally:
            if not self._closing:
                self._connection_watch_job = self.after(2000, self._connection_watch)

    def _reconnect_tick(self) -> None:
        self._reconnect_job = None
        if self._closing or not self._want_connection:
            return
        if self.device.is_connected:
            return
        if self._busy:
            self._schedule_reconnect(1000)
            return
        self._ensure_usable_transport()
        try:
            self._apply_transport()
        except ValueError as exc:
            self._set_status(f"Reconnect paused: {exc}")
            self._schedule_reconnect(5000)
            return

        self._set_busy(True)
        if self._is_proxy_mode():
            self._set_status("Reconnecting to proxy…")
        else:
            self._set_status("Reconnecting to MI Matrix Display…")
        fut = self.device.connect()

        def done() -> None:
            try:
                ok = fut.result(timeout=60)
            except Exception as exc:
                self._set_status(f"Reconnect failed ({exc}) — retrying in 5s…")
                ok = False
            self._set_busy(False)
            if ok:
                self._set_status(f"Reconnected to {self.device.device_label}")
                self.after(50, self._restore_display_after_connect)
            elif self._want_connection and not self._closing:
                self._set_status("Not connected — retrying in 5s…")
                self._schedule_reconnect(5000)

        self.after(100, self._poll_future, fut, done)

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
                    ["xfce4-terminal", "-e", f"bash -lc {shlex.quote(cmd)}"],
                    ["mate-terminal", "-e", f"bash -lc {shlex.quote(cmd)}"],
                    ["tilix", "-e", "bash", "-lc", cmd],
                    ["kitty", "bash", "-lc", cmd],
                    ["alacritty", "-e", "bash", "-lc", cmd],
                    ["xterm", "-e", "bash", "-lc", cmd],
                ):
                    try:
                        subprocess.Popen(terminal)
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise RuntimeError(
                        "No terminal emulator found. Install xterm/gnome-terminal "
                        "or open the log file manually."
                    )
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

    def _proxy_is_localhost(self) -> bool:
        host = self.proxy_host.get().strip().lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _ensure_usable_transport(self) -> None:
        """
        Proxy → 127.0.0.1 only works while this app's bridge is running.
        After a restart (or if the bridge was never started), fall back to Local BLE
        so the GUI can find the display again.
        """
        if self._is_proxy_mode() and self._proxy_is_localhost() and not self._bridge_running:
            self.connection_mode.set("Local BLE")
            self._update_proxy_fields()
            try:
                self.settings.connection_mode = "local"
                save_settings(self.settings)
            except Exception:
                pass

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
        # Prefer local Bluetooth on this machine. Proxy mode is only for driving
        # a remote bridge (or this app's own bridge via 127.0.0.1).
        self._ensure_usable_transport()
        if self._is_proxy_mode():
            host = self.proxy_host.get().strip()
            if not host:
                self._set_status("Proxy mode — enter host and Connect")
                return
            self._set_status(f"Connecting to proxy {host}…")
        else:
            self._set_status("Scanning for MI Matrix Display…")
        self._on_connect()

    def _on_connect(self) -> None:
        if self._busy:
            return
        self._want_connection = True
        self._stop_reconnect()
        self._ensure_usable_transport()
        try:
            self._apply_transport()
        except ValueError as exc:
            messagebox.showerror("Proxy settings", str(exc))
            self._schedule_reconnect(5000)
            return

        self._set_busy(True)
        if self._is_proxy_mode():
            self._set_status(f"Connecting to proxy {self.proxy_host.get().strip()}…")
        else:
            self._set_status("Scanning for MI Matrix Display…")
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
            elif self._want_connection and not self._closing:
                self._set_status("Not connected — retrying in 5s…")
                self._schedule_reconnect(5000)

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
        self._want_connection = False
        self._stop_reconnect()
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

    def _pack_fx_sliders(
        self,
        parent,
        *,
        fill: bool = False,
        grid: bool = False,
        row: int = 0,
    ) -> None:
        """Shared Speed / Brightness controls for tabs that drive the display."""
        box = ctk.CTkFrame(parent, fg_color="transparent")
        if grid:
            box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=8)
            box.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(box, text="Speed").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
            ctk.CTkSlider(
                box, from_=0.4, to=2.0, number_of_steps=16, variable=self._fx_speed
            ).grid(row=0, column=1, sticky="ew", pady=(0, 6))
            ctk.CTkLabel(box, text="Bright").grid(row=1, column=0, sticky="w", padx=(0, 8))
            ctk.CTkSlider(
                box,
                from_=0.15,
                to=1.0,
                number_of_steps=17,
                variable=self._fx_brightness,
                command=lambda _v: self._on_fx_brightness_changed(),
            ).grid(row=1, column=1, sticky="ew")
            return

        if fill:
            # Narrow side panels: stack Speed / Bright so neither clips.
            box.pack(fill="x", padx=10, pady=(10, 4))
            box.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(box, text="Speed").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
            ctk.CTkSlider(
                box, from_=0.4, to=2.0, number_of_steps=16, variable=self._fx_speed
            ).grid(row=0, column=1, sticky="ew", pady=(0, 6))
            ctk.CTkLabel(box, text="Bright").grid(row=1, column=0, sticky="w", padx=(0, 8))
            ctk.CTkSlider(
                box,
                from_=0.15,
                to=1.0,
                number_of_steps=17,
                variable=self._fx_brightness,
                command=lambda _v: self._on_fx_brightness_changed(),
            ).grid(row=1, column=1, sticky="ew")
            return

        box.pack(side="left", padx=(8, 4), pady=6)
        ctk.CTkLabel(box, text="Speed").pack(side="left", padx=(0, 4))
        ctk.CTkSlider(
            box, from_=0.4, to=2.0, number_of_steps=16, variable=self._fx_speed, width=100
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(box, text="Bright").pack(side="left", padx=(0, 4))
        ctk.CTkSlider(
            box,
            from_=0.15,
            to=1.0,
            number_of_steps=17,
            variable=self._fx_brightness,
            width=100,
            command=lambda _v: self._on_fx_brightness_changed(),
        ).pack(side="left")

    def _on_fx_brightness_changed(self) -> None:
        if self._current_page == "text":
            self._refresh_text_preview()

    def _speed_ms(self, base_ms: int) -> int:
        speed = max(0.25, float(self._fx_speed.get()))
        return max(10, int(base_ms / speed))

    def _brighten_frame(
        self, frame: list[tuple[int, int, int]]
    ) -> list[tuple[int, int, int]]:
        return apply_brightness(frame, float(self._fx_brightness.get()))

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
        delay = self._speed_ms(int(self.settings.live_update_ms))
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
        frame = self._brighten_frame(list(self.pixels))
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
        frame = self._brighten_frame(list(self.pixels))
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
            title="Upload drawing",
            filetypes=[
                ("PNG image", "*.png"),
                ("Python script", "*.py"),
                ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                self.pixels = load_drawing_python(path)
            elif lower.endswith(".png"):
                self.pixels = load_frame_png(path)
            else:
                self.pixels = load_image_as_matrix(path)
        except Exception as exc:
            messagebox.showerror("Upload", f"Could not load file:\n{exc}")
            return
        if self._draw_canvas is not None:
            self._draw_canvas.set_pixels(self.pixels)
        self._sync_preview()
        self._set_status(f"Loaded: {Path(path).name}")
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
            title="Upload panel",
            filetypes=[
                ("PNG image", "*.png"),
                ("Python script", "*.py"),
                ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                # Drawing export → this panel; animation export → first panel.
                try:
                    frame = load_drawing_python(path)
                except Exception:
                    frames = load_animation_python(path)
                    frame = frames[0]
            elif lower.endswith(".png"):
                frame = load_frame_png(path)
            else:
                frame = load_image_as_matrix(path)
            self.anim_frames[self.anim_index] = frame
        except Exception as exc:
            messagebox.showerror("Upload", f"Could not load file:\n{exc}")
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
        self._text_stop_play()
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
        if self._music_on_display:
            self._music_on_display = False
            if self._music_display_btn is not None:
                self._music_display_btn.configure(text="Play on Display")
        if self._text_playing:
            self._text_playing = False
            if self._text_play_btn is not None:
                self._text_play_btn.configure(text="Play on Display")
            if self._text_job is not None:
                try:
                    self.after_cancel(self._text_job)
                except Exception:
                    pass
                self._text_job = None

    # ------------------------------------------------------------------ text

    def _text_scale(self) -> float:
        try:
            return float(self._text_scale_label.get())
        except ValueError:
            return 1.0

    def _text_content(self) -> str:
        line1 = self._text_line1.get().strip()
        line2 = self._text_line2.get().strip()
        if line2:
            return f"{line1}\n{line2}"
        return line1

    def _on_text_scale_changed(self, _value: str | None = None) -> None:
        self._refresh_text_preview()

    def _pick_text_color(self) -> None:
        result = colorchooser.askcolor(
            color=rgb_to_hex(*self._text_color), title="Text color"
        )
        if not result or not result[0]:
            return
        r, g, b = (int(c) for c in result[0])
        self._text_color = (r, g, b)
        if self._text_color_btn is not None:
            self._text_color_btn.configure(fg_color=rgb_to_hex(r, g, b))
        self._refresh_text_preview()

    def _pick_text_bg_color(self) -> None:
        result = colorchooser.askcolor(
            color=rgb_to_hex(*self._text_bg_color), title="Background color"
        )
        if not result or not result[0]:
            return
        r, g, b = (int(c) for c in result[0])
        self._text_bg_color = (r, g, b)
        if self._text_bg_color_btn is not None:
            self._text_bg_color_btn.configure(fg_color=rgb_to_hex(r, g, b))
        self._text_bg_mode.set("Solid")
        self._text_bg_frames = [blank_frame(self._text_bg_color)]
        self._refresh_text_preview()

    def _on_text_bg_mode_changed(self, _value: str | None = None) -> None:
        mode = self._text_bg_mode.get()
        if mode == "Solid":
            self._text_bg_frames = [blank_frame(self._text_bg_color)]
            self._refresh_text_preview()
        else:
            self._set_status(f"Text background: {mode} — use Import… to load a file")

    def _text_import_background(self) -> None:
        mode = self._text_bg_mode.get()
        if mode == "Solid":
            self._pick_text_bg_color()
            return
        if mode == "Image":
            path = filedialog.askopenfilename(
                title="Import text background image",
                filetypes=[
                    ("PNG image", "*.png"),
                    ("Python script", "*.py"),
                    ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                    ("All files", "*.*"),
                ],
            )
            if not path:
                return
            try:
                lower = path.lower()
                if lower.endswith(".py"):
                    frame = load_drawing_python(path)
                elif lower.endswith(".png"):
                    frame = load_frame_png(path)
                else:
                    frame = load_image_as_matrix(path)
                self._text_bg_frames = [frame]
            except Exception as exc:
                messagebox.showerror("Text", f"Could not import image:\n{exc}")
                return
            self._refresh_text_preview()
            self._set_status(f"Text background image: {Path(path).name}")
            return

        # Animation
        path = filedialog.askopenfilename(
            title="Import text background animation",
            filetypes=[
                ("Python script", "*.py"),
                ("GIF animation", "*.gif"),
                ("ZIP of PNGs", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                frames = load_animation_python(path)
            elif lower.endswith(".zip"):
                frames = load_animation_zip(path)
            else:
                frames = load_animation_gif(path)
            self._text_bg_frames = frames
            self._text_bg_mode.set("Animation")
        except Exception as exc:
            messagebox.showerror("Text", f"Could not import animation:\n{exc}")
            return
        self._refresh_text_preview()
        self._set_status(f"Text background animation: {Path(path).name} ({len(frames)} frames)")

    def _text_compose_frame(self, tick: int = 0) -> list[tuple[int, int, int]]:
        frames = self._text_bg_frames or [blank_frame(self._text_bg_color)]
        if self._text_bg_mode.get() == "Solid":
            bg = blank_frame(self._text_bg_color)
        else:
            bg = frames[tick % len(frames)]
        scale = self._text_scale()
        text = self._text_content()
        scroll_x = 0
        width = max_line_width(text, scale=scale)
        if self._text_scroll.get() and width > MATRIX_SIZE:
            # Continuous marquee: enter from the right, exit left, then keep
            # scrolling through one full panel of empty (background-only) space
            # before the text comes back in — no hard reset to the left edge.
            gap = MATRIX_SIZE
            period = width + MATRIX_SIZE + gap
            offset = tick % period
            # render_text_frame draws at x = -scroll_x; we want draw_x =
            # MATRIX_SIZE - offset (off-right at offset 0).
            scroll_x = offset - MATRIX_SIZE
        return self._brighten_frame(
            render_text_frame(
                text=text,
                color=self._text_color,
                scale=scale,
                background=bg,
                scroll_x=scroll_x,
            )
        )

    def _refresh_text_preview(self) -> None:
        frame = self._text_compose_frame(self._text_tick)
        if self._text_canvas is not None:
            self._text_canvas.set_pixels_fast(frame)
        if self._preview is not None and self._current_page == "home" and self._text_playing:
            self._preview.set_pixels_fast(frame)

    def _text_toggle_play(self) -> None:
        if self._text_playing:
            self._text_stop_play()
        else:
            self._text_start_play()

    def _text_start_play(self) -> None:
        if not self._require_connection():
            return
        self._anim_stop()
        if self._music_on_display:
            self._music_stop_display()
        self._text_playing = True
        self._text_tick = 0
        self._stop_keepalive()
        self._powered_on = True
        if self._text_play_btn is not None:
            self._text_play_btn.configure(text="Stop")
        self._set_status("Playing text on display…")
        self._text_tick_once()

    def _text_stop_play(self) -> None:
        was = self._text_playing
        self._text_playing = False
        if self._text_job is not None:
            try:
                self.after_cancel(self._text_job)
            except Exception:
                pass
            self._text_job = None
        if self._text_play_btn is not None:
            self._text_play_btn.configure(text="Play on Display")
        if was:
            self._set_status("Text playback stopped")

    def _text_tick_once(self) -> None:
        if not self._text_playing:
            return
        if not self.device.is_connected:
            self._text_stop_play()
            self._set_status("Text stopped — disconnected")
            return
        frame = self._text_compose_frame(self._text_tick)
        self._text_tick += 1
        if self._text_canvas is not None:
            self._text_canvas.set_pixels_fast(frame)
        if self._preview is not None and self._current_page == "home":
            self._preview.set_pixels_fast(frame)
        fut = self.device.send_frame(frame)
        self._display_frame = list(frame)

        def after_send() -> None:
            if not self._text_playing:
                return
            try:
                fut.result(timeout=0)
            except Exception as exc:
                self._text_stop_play()
                self._set_status(f"Text error: {exc}")
                return
            delay = self._speed_ms(200)
            self._text_job = self.after(delay, self._text_tick_once)

        self.after(40, self._poll_future, fut, after_send)

    def _on_music_source_changed(self, _value: str | None = None) -> None:
        if self._music_source.get() == "System audio":
            self._music_status_var.set(self._audio.system_audio_hint())
        else:
            self._music_status_var.set("Microphone selected.")
        if self._music_listening:
            on_display = self._music_on_display
            self._music_stop_listen()
            self._music_start_listen()
            if on_display and self._music_listening:
                self._music_start_display()

    def _music_render_frame(self) -> list[tuple[int, int, int]]:
        # Brightness is applied in _anim_tick when sending / showing.
        return render_mode(
            self._music_mode.get(),
            self._audio.features(),
            float(self._music_sensitivity.get()),
        )

    def _music_toggle_listen(self) -> None:
        if self._music_listening:
            self._music_stop_listen()
        else:
            self._music_start_listen()

    def _music_start_listen(self) -> None:
        if not audio_available():
            messagebox.showerror(
                "Music",
                "Audio packages are missing.\nInstall with:\npip install sounddevice numpy",
            )
            return
        source = "system" if self._music_source.get() == "System audio" else "microphone"
        try:
            self._audio.start(source=source)
        except Exception as exc:
            self._music_status_var.set(str(exc))
            messagebox.showerror("Music", str(exc))
            return
        self._music_listening = True
        if self._music_listen_btn is not None:
            self._music_listen_btn.configure(text="Stop Listening")
        if self._music_display_btn is not None:
            self._music_display_btn.configure(state="normal")
        self._music_status_var.set("Listening — preview updating on the LED matrix view.")
        self._set_status("Music listening started")
        self._schedule_music_preview()

    def _music_stop_listen(self) -> None:
        if self._music_on_display:
            self._music_stop_display()
        self._music_listening = False
        self._audio.stop()
        if self._music_preview_job is not None:
            try:
                self.after_cancel(self._music_preview_job)
            except Exception:
                pass
            self._music_preview_job = None
        if self._music_listen_btn is not None:
            self._music_listen_btn.configure(text="Start Listening")
        if self._music_display_btn is not None:
            self._music_display_btn.configure(state="disabled", text="Play on Display")
        self._music_level_var.set("Level: —")
        self._music_status_var.set("Stopped.")
        if self._music_canvas is not None:
            self._music_canvas.set_pixels(blank_frame())
        self._set_status("Music listening stopped")

    def _schedule_music_preview(self) -> None:
        if self._music_preview_job is not None:
            try:
                self.after_cancel(self._music_preview_job)
            except Exception:
                pass
        self._music_preview_job = self.after(70, self._music_preview_tick)

    def _music_preview_tick(self) -> None:
        self._music_preview_job = None
        if not self._music_listening or self._closing:
            return
        features = self._audio.features()
        frame = self._brighten_frame(
            render_mode(
                self._music_mode.get(), features, float(self._music_sensitivity.get())
            )
        )
        self._music_level_var.set(
            f"Level: {features.level:.2f}   Bass: {features.bass:.2f}"
        )
        if self._music_canvas is not None:
            self._music_canvas.set_pixels_fast(frame)
        if (
            self._preview is not None
            and self._current_page == "home"
            and self._music_on_display
        ):
            self._preview.set_pixels_fast(frame)
        self._schedule_music_preview()

    def _music_toggle_display(self) -> None:
        if self._music_on_display:
            self._music_stop_display()
        else:
            self._music_start_display()

    def _music_start_display(self) -> None:
        if not self._music_listening:
            self._music_start_listen()
            if not self._music_listening:
                return
        if not self._require_connection():
            return
        self._music_on_display = True
        if self._music_display_btn is not None:
            self._music_display_btn.configure(text="Stop Display")
        self._start_animation(
            preview_only=False,
            live_fn=lambda _tick: self._music_render_frame(),
            frame_ms=self._speed_ms(200),
            label="Music",
        )
        self._music_status_var.set(
            "Playing on display (~5 fps over BLE). Preview stays snappier."
        )

    def _music_stop_display(self) -> None:
        self._music_on_display = False
        if self._music_display_btn is not None:
            self._music_display_btn.configure(text="Play on Display")
        if self._anim_playing and self._active_preset_label == "Music":
            self._anim_stop()
        self._music_status_var.set(
            "Display playback stopped. Preview continues while listening."
            if self._music_listening
            else "Stopped."
        )

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
            delay = self._speed_ms(
                int(self._live_preset_ms or self.settings.animation_frame_ms)
            )
            next_index = index
        else:
            if not self.anim_frames:
                self._anim_stop()
                return
            frame = self.anim_frames[index % len(self.anim_frames)]
            delay = self._speed_ms(
                int(self._live_preset_ms or self.settings.animation_frame_ms)
            )
            next_index = (index + 1) % len(self.anim_frames)

        shown = self._brighten_frame(frame)
        self._update_anim_ui_frame(shown)

        if preview_only:
            # Local preview does not need 100 Hz ticks — that alone pegs a core.
            preview_delay = max(delay, 50)
            self._anim_job = self.after(preview_delay, self._anim_tick, next_index, generation)
            return

        if self._last_sent_frame is not None and shown == self._last_sent_frame:
            self._anim_job = self.after(delay, self._anim_tick, next_index, generation)
            return

        fut = self.device.send_frame(shown)
        self._last_sent_frame = list(shown)
        self._display_frame = list(shown)
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

    def _confirm_non_python_save(self, format_label: str) -> str:
        """
        Warn before lossy image exports.

        Returns: "format" | "python" | "cancel"
        """
        result = {"choice": "cancel"}
        win = ctk.CTkToplevel(self)
        win.title("Save Notice")
        win.geometry("520x280")
        win.transient(self)
        win.grab_set()
        win.focus_force()

        ctk.CTkLabel(
            win,
            text="Save Notice",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            win,
            text=(
                "When saving as an image, the app has to estimate the color of the image, "
                "which is unpredictable and can change every time you upload it. It is "
                "strongly recommended you save as a Python (.py) file as the script contains "
                "the exact color codes of each pixel, making sure each import is the same "
                "as the last."
            ),
            wraplength=480,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=18, pady=(0, 16))

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(8, 18))

        def choose(choice: str) -> None:
            result["choice"] = choice
            win.destroy()

        ctk.CTkButton(
            btns,
            text=f"Save as {format_label} anyways",
            width=200,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=lambda: choose("format"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns,
            text="Save as Python (.py)",
            width=180,
            command=lambda: choose("python"),
        ).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
        self.wait_window(win)
        return result["choice"]

    def _export_drawing(self) -> None:
        self._sync_pixels_from_draw()
        path = filedialog.asksaveasfilename(
            title="Save drawing",
            defaultextension=".py",
            filetypes=[
                ("Python script", "*.py"),
                ("PNG image", "*.png"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        lower = path.lower()
        if lower.endswith(".py"):
            fmt = "py"
        else:
            fmt = "png"
            if not lower.endswith(".png"):
                path = path + ".png"
            choice = self._confirm_non_python_save("PNG")
            if choice == "cancel":
                return
            if choice == "python":
                path = str(Path(path).with_suffix(".py"))
                fmt = "py"
        try:
            if fmt == "py":
                save_drawing_python(path, self.pixels, name=Path(path).stem)
            else:
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
            defaultextension=".py",
            filetypes=[
                ("Python script", "*.py"),
                ("GIF animation", "*.gif"),
                ("ZIP of PNGs", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        lower = path.lower()
        if lower.endswith(".py"):
            fmt = "py"
            format_label = "Python"
        elif lower.endswith(".zip"):
            fmt = "zip"
            format_label = "ZIP"
        else:
            fmt = "gif"
            format_label = "GIF"
            if not lower.endswith(".gif"):
                path = path + ".gif"

        if fmt != "py":
            choice = self._confirm_non_python_save(format_label)
            if choice == "cancel":
                return
            if choice == "python":
                path = str(Path(path).with_suffix(".py"))
                fmt = "py"

        frames = [list(f) for f in self.anim_frames]
        frame_ms = int(self.settings.animation_frame_ms)
        try:
            if fmt == "py":
                save_animation_python(
                    path, frames, frame_ms=frame_ms, name=Path(path).stem
                )
            elif fmt == "zip":
                save_animation_zip(path, frames, scale=16)
            else:
                save_animation_gif(path, frames, frame_ms=frame_ms, scale=16)
            self._set_status(f"Saved animation ({len(frames)} panels): {path}")
        except Exception as exc:
            messagebox.showerror("Save animation", f"Could not save:\n{exc}")

    def _import_animation(self) -> None:
        path = filedialog.askopenfilename(
            title="Import animation",
            filetypes=[
                ("Python script", "*.py"),
                ("GIF animation", "*.gif"),
                ("ZIP of PNGs", "*.zip"),
                ("GIF / ZIP / Python", "*.gif *.zip *.py"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".py"):
                frames = load_animation_python(path)
            elif lower.endswith(".zip"):
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
            "os": detect_os_id(),
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
        self._bridge_append("Scanning LAN for other MI LED hosts…")
        self._set_status("Scanning LAN for sessions…")
        own_ips = set(self._lan_addresses())
        preferred = self._preferred_lan_ip()
        if preferred:
            own_ips.add(preferred)

        def worker() -> None:
            try:
                # Other PCs only. Prefer hosts that are not already running a bridge
                # (available peers); fall back list is built in the UI if empty.
                sessions = scan_sessions(
                    timeout=1.8,
                    bridges_only=False,
                    exclude_ips=own_ips,
                )
                # Keep peers whose bridge is not open (available to start / not busy as server).
                sessions = [s for s in sessions if not s.bridge]
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
        if self._session_scan_win is not None:
            try:
                self._session_scan_win.destroy()
            except Exception:
                pass
            self._session_scan_win = None

        win = ctk.CTkToplevel(self)
        self._session_scan_win = win
        win.title("Server list")
        win.geometry("780x460")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="Other MI LED hosts (bridge not open)",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(
            win,
            text="Shows other devices on your LAN that are running the app but do not currently "
            "have a BLE bridge open. Select a row to fill Proxy host / Port.",
            text_color=("gray35", "gray65"),
            wraplength=740,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        if not sessions:
            ctk.CTkLabel(
                win,
                text="No matching hosts found.\n"
                "Make sure the other PC has this app open on the same LAN,\n"
                "and that its BLE Bridge is not already running.\n"
                "(UDP port 8766 must be allowed through the firewall.)",
                justify="left",
            ).pack(anchor="w", padx=16, pady=20)
            ctk.CTkButton(win, text="Close", width=100, command=win.destroy).pack(
                pady=(8, 16)
            )
            self._bridge_append("Session scan: no eligible hosts found")
            self._set_status("No eligible LAN hosts found")
            return

        table = ctk.CTkScrollableFrame(win, height=300)
        table.pack(fill="both", expand=True, padx=12, pady=8)
        cols = (1, 3, 3, 2, 2, 1)
        for c, weight in enumerate(cols):
            table.grid_columnconfigure(c, weight=weight)

        headers = ("OS", "Hostname", "IP:Port", "Ping", "Locked?", "")
        for col, title in enumerate(headers):
            ctk.CTkLabel(
                table,
                text=title,
                font=ctk.CTkFont(weight="bold"),
                anchor="w",
            ).grid(row=0, column=col, sticky="ew", padx=6, pady=(4, 8))

        for row_i, session in enumerate(sessions, start=1):
            icon = self._retain_icon(os_icon(session.os_id, size=(24, 24)))
            os_cell = ctk.CTkFrame(table, fg_color="transparent", width=36, height=32)
            os_cell.grid(row=row_i, column=0, sticky="w", padx=6, pady=4)
            os_cell.pack_propagate(False)
            if icon is not None:
                ctk.CTkLabel(os_cell, text="", image=icon, width=28, height=28).place(
                    relx=0.5, rely=0.5, anchor="center"
                )
            else:
                ctk.CTkLabel(os_cell, text=(session.os_id or "?")[:3]).place(
                    relx=0.5, rely=0.5, anchor="center"
                )

            ctk.CTkLabel(table, text=session.hostname or session.name, anchor="w").grid(
                row=row_i, column=1, sticky="ew", padx=6, pady=4
            )
            ctk.CTkLabel(table, text=session.endpoint, anchor="w").grid(
                row=row_i, column=2, sticky="ew", padx=6, pady=4
            )
            ping = f"{session.ping_ms}ms" if session.ping_ms is not None else "—"
            ctk.CTkLabel(table, text=ping, anchor="w").grid(
                row=row_i, column=3, sticky="ew", padx=6, pady=4
            )

            locked_cell = ctk.CTkFrame(table, fg_color="transparent")
            locked_cell.grid(row=row_i, column=4, sticky="w", padx=6, pady=4)
            if session.auth_required:
                lock = self._retain_icon(action_icon("lock", size=(14, 14)))
                if lock is not None:
                    ctk.CTkLabel(locked_cell, text="", image=lock).pack(side="left", padx=(0, 4))
                ctk.CTkLabel(locked_cell, text="Y").pack(side="left")
            else:
                ctk.CTkLabel(locked_cell, text="N").pack(side="left")

            ctk.CTkButton(
                table,
                text="Use",
                width=64,
                command=lambda s=session: self._use_discovered_session(s, win),
            ).grid(row=row_i, column=5, sticky="e", padx=6, pady=4)

        self._bridge_append(f"Session scan: found {len(sessions)} host(s) without an open bridge")
        self._set_status(f"Found {len(sessions)} LAN host(s)")
        ctk.CTkButton(win, text="Close", width=100, command=win.destroy).pack(pady=(4, 14))

    def _use_discovered_session(self, session: SessionInfo, win: ctk.CTkToplevel) -> None:
        self.connection_mode.set("BLE Proxy")
        self.proxy_host.set(session.ip)
        self.proxy_port.set(str(session.port))
        self._update_proxy_fields()
        self._persist_connection_settings()
        note = ""
        if not session.bridge:
            note = " (host app is open; start its BLE Bridge before connecting)"
        elif session.auth_required:
            note = " — enter the shared token before connecting"
        self._bridge_append(
            f"Selected session {session.hostname or session.name} at {session.ip}:{session.port}{note}"
        )
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
        self._set_status("BLE Bridge running — connecting this GUI via localhost…")

        # Bridge owns Bluetooth; point this GUI at the local proxy so drawing
        # / animations still work on the same machine.
        self.connection_mode.set("BLE Proxy")
        self.proxy_host.set("127.0.0.1")
        self.proxy_port.set(str(port))
        if token:
            self.proxy_token.set(token)
        self._update_proxy_fields()
        self._persist_connection_settings()
        self.after(600, self._on_connect)

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
        was_proxy_localhost = (
            self._is_proxy_mode()
            and self.proxy_host.get().strip() in {"127.0.0.1", "localhost"}
        )
        self._bridge_running = False
        self._bridge_server = None
        self._bridge_loop = None
        self._bridge_thread = None
        self._bridge_start_btn.configure(state="normal")
        self._bridge_stop_btn.configure(state="disabled")
        self._bridge_append("Bridge stopped")
        if was_proxy_localhost:
            # Return this machine to direct BLE now that the bridge released it.
            if self.device.is_connected:
                self._on_disconnect()
            self.connection_mode.set("Local BLE")
            self._update_proxy_fields()
            self._persist_connection_settings()
            self._set_status("BLE Bridge stopped — reconnecting locally…")
            self.after(400, self._on_connect)
        else:
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
        self.settings.appearance_mode = (
            self._appearance_var.get().strip().title()
            if self._appearance_var.get().strip().title() in ("System", "Light", "Dark")
            else "System"
        )
        self.settings.clamp()
        save_settings(self.settings)
        self._apply_preview_style()
        ctk.set_appearance_mode(self.settings.appearance_mode)

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
        self._want_connection = False
        self._stop_reconnect()
        try:
            self._music_stop_listen()
        except Exception:
            pass
        try:
            self._text_stop_play()
        except Exception:
            pass
        if self._connection_watch_job is not None:
            try:
                self.after_cancel(self._connection_watch_job)
            except Exception:
                pass
            self._connection_watch_job = None
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
            self._ensure_usable_transport()
            self.settings.proxy_host = self.proxy_host.get().strip() or "127.0.0.1"
            self.settings.proxy_token = self.proxy_token.get()
            # Don't leave the next launch stuck on localhost proxy with no bridge.
            if self._is_proxy_mode() and self._proxy_is_localhost() and not self._bridge_running:
                self.settings.connection_mode = "local"
            else:
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
    print(f"MI LED GUI starting (v{APP_VERSION}, rev {APP_REVISION})")
    app = MiLedApp()
    app.mainloop()


if __name__ == "__main__":
    main()
