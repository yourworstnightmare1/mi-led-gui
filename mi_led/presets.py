"""Built-in drawing and animation presets for the 16x16 matrix."""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .font import draw_centered_text, draw_text
from .image_convert import blank_frame
from .metrics import ensure_psutil_warmup, sample_system, usage_color
from .protocol import MATRIX_SIZE, PIXEL_COUNT

Frame = list[tuple[int, int, int]]
FrameBuilder = Callable[[], Frame]
AnimBuilder = Callable[[], list[Frame]]
LiveBuilder = Callable[[int], Frame]  # tick index → frame


SOLID_COLORS: dict[str, tuple[int, int, int]] = {
    "White": (255, 255, 255),
    "Yellow": (255, 220, 0),
    "Cyan": (0, 220, 255),
    "Green": (0, 220, 60),
    "Pink": (255, 80, 180),
    "Red": (255, 30, 40),
    "Blue": (40, 110, 255),
}

RGB = (
    (255, 20, 20),
    (20, 220, 60),
    (40, 100, 255),
)


@dataclass(frozen=True)
class DrawingPreset:
    id: str
    label: str
    build: FrameBuilder


@dataclass(frozen=True)
class AnimationPreset:
    id: str
    label: str
    kind: str  # "static" | "live"
    build_static: Optional[AnimBuilder] = None
    build_live: Optional[LiveBuilder] = None
    frame_ms: Optional[int] = None  # override playback delay when set


def _idx(x: int, y: int) -> int:
    return y * MATRIX_SIZE + x


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(
    c0: tuple[int, int, int], c1: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(_lerp(c0[0], c1[0], t)),
        int(_lerp(c0[1], c1[1], t)),
        int(_lerp(c0[2], c1[2], t)),
    )


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _scale(c: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(c[0] * t), int(c[1] * t), int(c[2] * t))


# --------------------------------------------------------------------------- drawings


def draw_rainbow() -> Frame:
    frame = blank_frame()
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            frame[_idx(x, y)] = _hsv(x / MATRIX_SIZE)
    return frame


def draw_color_vertical(color: tuple[int, int, int]) -> Frame:
    """Vertical gradient: bright at top → dark at bottom."""
    frame = blank_frame()
    for y in range(MATRIX_SIZE):
        t = 1.0 - (y / (MATRIX_SIZE - 1)) * 0.85
        c = _scale(color, t)
        for x in range(MATRIX_SIZE):
            frame[_idx(x, y)] = c
    return frame


def draw_color_horizontal(color: tuple[int, int, int]) -> Frame:
    """Horizontal gradient: bright at left → dark at right."""
    frame = blank_frame()
    for x in range(MATRIX_SIZE):
        t = 1.0 - (x / (MATRIX_SIZE - 1)) * 0.85
        c = _scale(color, t)
        for y in range(MATRIX_SIZE):
            frame[_idx(x, y)] = c
    return frame


def _build_drawing_presets() -> list[DrawingPreset]:
    presets: list[DrawingPreset] = [
        DrawingPreset("rainbow", "Rainbow", draw_rainbow),
    ]
    for name, color in SOLID_COLORS.items():
        presets.append(
            DrawingPreset(
                f"{name.lower()}_v",
                f"{name} Vertical",
                (lambda c=color: draw_color_vertical(c)),
            )
        )
        presets.append(
            DrawingPreset(
                f"{name.lower()}_h",
                f"{name} Horizontal",
                (lambda c=color: draw_color_horizontal(c)),
            )
        )
    return presets


DRAWING_PRESETS = _build_drawing_presets()
DRAWING_BY_LABEL = {p.label: p for p in DRAWING_PRESETS}


# --------------------------------------------------------------------------- static animations


def anim_rainbow(frames: int = 32) -> list[Frame]:
    out: list[Frame] = []
    for i in range(frames):
        frame = blank_frame()
        shift = i / frames
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                frame[_idx(x, y)] = _hsv(x / MATRIX_SIZE + shift)
        out.append(frame)
    return out


def anim_rgb_smooth(frames_per_leg: int = 16) -> list[Frame]:
    out: list[Frame] = []
    for i in range(3):
        c0, c1 = RGB[i], RGB[(i + 1) % 3]
        for step in range(frames_per_leg):
            t = step / frames_per_leg
            out.append(blank_frame(_lerp_color(c0, c1, t)))
    return out


def anim_rgb_pulse(frames_per_color: int = 20) -> list[Frame]:
    out: list[Frame] = []
    for color in RGB:
        for step in range(frames_per_color):
            # sine pulse 0.15 → 1 → 0.15
            t = step / max(1, frames_per_color - 1)
            intensity = 0.15 + 0.85 * abs(math.sin(t * math.pi))
            out.append(blank_frame(_scale(color, intensity)))
    return out


def anim_rgb_jump(hold: int = 8) -> list[Frame]:
    out: list[Frame] = []
    for color in RGB:
        out.extend([blank_frame(color)] * hold)
    return out


def anim_rgb_flash() -> list[Frame]:
    """Each color flashes 3 times with a black beat between flashes and colors."""
    out: list[Frame] = []
    black = blank_frame()
    on_frames = 3
    off_frames = 3
    between_colors = 5
    for color in RGB:
        for _ in range(3):
            out.extend([blank_frame(color)] * on_frames)
            out.extend([black] * off_frames)
        out.extend([black] * between_colors)
    return out


def anim_white_black_fade(frames: int = 32) -> list[Frame]:
    out: list[Frame] = []
    white = (255, 255, 255)
    for i in range(frames):
        t = i / (frames - 1)
        out.append(blank_frame(_scale(white, 1.0 - t)))
    for i in range(frames):
        t = i / (frames - 1)
        out.append(blank_frame(_scale(white, t)))
    return out


def anim_white_black_collapse(steps: int = 20) -> list[Frame]:
    """
    White cells fall downward, leaving a much darker shadow trail.
    """
    white = (255, 255, 255)
    trail = (28, 28, 32)
    # grid of brightness / falling particles: start all white "mass"
    mass = [[1.0 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]
    trails = [[0.0 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]
    out: list[Frame] = []

    def to_frame() -> Frame:
        frame = blank_frame()
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                if mass[y][x] > 0.5:
                    frame[_idx(x, y)] = white
                elif trails[y][x] > 0.05:
                    frame[_idx(x, y)] = _scale(trail, min(1.0, trails[y][x]))
        return frame

    out.append(to_frame())
    for _ in range(steps):
        new_mass = [[0.0 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]
        # decay trails
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                trails[y][x] *= 0.72
        # fall one row
        for y in range(MATRIX_SIZE - 1, -1, -1):
            for x in range(MATRIX_SIZE):
                if mass[y][x] <= 0.5:
                    continue
                trails[y][x] = max(trails[y][x], 0.9)
                dest = min(MATRIX_SIZE - 1, y + 1)
                if dest == y:
                    new_mass[y][x] = 1.0
                else:
                    # pile at bottom
                    if dest == MATRIX_SIZE - 1 and new_mass[dest][x] > 0.5:
                        new_mass[y][x] = 1.0
                    else:
                        new_mass[dest][x] = 1.0
        mass = new_mass
        out.append(to_frame())

    # settle: fade remaining white into trail then black
    for fade in range(10):
        t = 1.0 - fade / 9
        frame = blank_frame()
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                if mass[y][x] > 0.5:
                    frame[_idx(x, y)] = _scale(white, t)
                elif trails[y][x] > 0.05:
                    frame[_idx(x, y)] = _scale(trail, trails[y][x] * t)
        out.append(frame)
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                trails[y][x] *= 0.7
    out.append(blank_frame())
    return out


# --------------------------------------------------------------------------- live render helpers


def _draw_hbar(
    frame: Frame,
    y: int,
    percent: float,
    *,
    x0: int = 1,
    x1: int = 14,
    color: Optional[tuple[int, int, int]] = None,
) -> None:
    color = color or usage_color(percent)
    width = x1 - x0 + 1
    filled = int(round((max(0.0, min(100.0, percent)) / 100.0) * width))
    for x in range(x0, x1 + 1):
        if x - x0 < filled:
            frame[_idx(x, y)] = color
        else:
            frame[_idx(x, y)] = (24, 24, 28)


def _draw_pie(
    frame: Frame,
    percent: float,
    *,
    cx: float = 7.5,
    cy: float = 7.5,
    radius: float = 6.2,
    color: Optional[tuple[int, int, int]] = None,
) -> None:
    color = color or usage_color(percent)
    frac = max(0.0, min(100.0, percent)) / 100.0
    # Start at 12 o'clock, clockwise
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            dx = (x + 0.5) - cx
            dy = (y + 0.5) - cy
            dist = math.hypot(dx, dy)
            if dist > radius or dist < 2.2:
                continue
            # angle from north, clockwise
            ang = (math.atan2(dx, -dy) + 2 * math.pi) % (2 * math.pi)
            if ang <= frac * 2 * math.pi:
                frame[_idx(x, y)] = color
            else:
                frame[_idx(x, y)] = (28, 28, 32)


def _pct_label(percent: float) -> str:
    return f"{int(round(max(0.0, min(100.0, percent))))}"


def live_clock(tick: int) -> Frame:
    frame = blank_frame()
    now = datetime.now()
    hh = now.strftime("%H")
    mm = now.strftime("%M")
    ss = now.strftime("%S")
    # Top: HH  Bottom-ish: MM  blink colon via seconds
    draw_centered_text(frame, hh, 1, (255, 220, 80))
    colon = (180, 180, 200) if (now.microsecond // 500_000) % 2 == 0 else (40, 40, 48)
    # tiny colon dots between rows
    frame[_idx(7, 7)] = colon
    frame[_idx(8, 7)] = colon
    draw_centered_text(frame, mm, 9, (255, 220, 80))
    # seconds as 2 tiny pixels on bottom edge progressing
    sec = now.second
    for x in range(MATRIX_SIZE):
        frame[_idx(x, 15)] = (60, 60, 80) if x <= (sec * 15) // 59 else (15, 15, 20)
    return frame


def _metric_bar_frame(title: str, percent: float) -> Frame:
    frame = blank_frame()
    draw_centered_text(frame, title[:4], 1, (200, 200, 210), spacing=1)
    for y in (7, 8):
        _draw_hbar(frame, y, percent)
    draw_centered_text(frame, _pct_label(percent), 11, usage_color(percent))
    return frame


def _metric_pie_frame(title: str, percent: float) -> Frame:
    frame = blank_frame()
    _draw_pie(frame, percent)
    # title strip top
    draw_centered_text(frame, title[:3], 0, (180, 180, 190), spacing=1)
    # percent in hole
    label = _pct_label(percent)
    if len(label) <= 2:
        draw_centered_text(frame, label, 6, (255, 255, 255), spacing=1)
    else:
        # 100 — squeeze
        draw_text(frame, "100", 2, 6, (255, 255, 255), spacing=0)
    return frame


def live_cpu_cores(tick: int) -> Frame:
    snap = sample_system(want_cores=True)
    cores = snap.cores
    n = len(cores)
    frame = blank_frame()
    if n == 0:
        draw_centered_text(frame, "CPU", 5, (80, 80, 90))
        return frame
    # If too many cores to show one-per-cell with a readable grid, fall back to bar.
    if n > PIXEL_COUNT or n > 64:
        return live_cpu_bar(tick)

    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    cell_w = MATRIX_SIZE // cols
    cell_h = MATRIX_SIZE // rows
    if cell_w < 1 or cell_h < 1:
        return live_cpu_bar(tick)

    for i, core in enumerate(cores):
        row, col = i // cols, i % cols
        x0 = col * cell_w
        y0 = row * cell_h
        color = usage_color(core.usage)
        x1 = x0 + cell_w - (1 if cell_w > 1 else 0)
        y1 = y0 + cell_h - (1 if cell_h > 1 else 0)
        for y in range(y0, min(MATRIX_SIZE, y1)):
            for x in range(x0, min(MATRIX_SIZE, x1)):
                frame[_idx(x, y)] = color
    return frame


def live_cpu_bar(tick: int) -> Frame:
    snap = sample_system(want_cpu=True)
    return _metric_bar_frame("CPU", snap.cpu_percent)


def live_cpu_pie(tick: int) -> Frame:
    snap = sample_system(want_cpu=True)
    return _metric_pie_frame("CPU", snap.cpu_percent)


def live_ram_bar(tick: int) -> Frame:
    snap = sample_system(want_ram=True)
    return _metric_bar_frame("RAM", snap.ram_percent)


def live_ram_pie(tick: int) -> Frame:
    snap = sample_system(want_ram=True)
    return _metric_pie_frame("RAM", snap.ram_percent)


def live_ram_blocks(tick: int) -> Frame:
    """Extra RAM view: used blocks vs free."""
    snap = sample_system(want_ram=True)
    frame = blank_frame()
    draw_centered_text(frame, "RAM", 0, (200, 200, 210))
    used = int(round(snap.ram_percent / 100.0 * 48))  # 3 rows x 16
    color = usage_color(snap.ram_percent)
    for i in range(48):
        y = 5 + i // MATRIX_SIZE
        x = i % MATRIX_SIZE
        frame[_idx(x, y)] = color if i < used else (24, 24, 28)
    draw_centered_text(frame, _pct_label(snap.ram_percent), 11, color)
    return frame


def live_disk_bar(tick: int) -> Frame:
    snap = sample_system(want_disk=True)
    return _metric_bar_frame("DSK", snap.disk_percent)


def live_disk_pie(tick: int) -> Frame:
    snap = sample_system(want_disk=True)
    return _metric_pie_frame("DSK", snap.disk_percent)


def live_gpu_bar(tick: int) -> Frame:
    snap = sample_system(want_gpu=True)
    if snap.gpu_percent is None:
        frame = blank_frame()
        draw_centered_text(frame, "GPU", 3, (120, 120, 130))
        draw_centered_text(frame, "N/A", 9, (80, 80, 90))
        return frame
    return _metric_bar_frame("GPU", snap.gpu_percent)


def live_gpu_pie(tick: int) -> Frame:
    snap = sample_system(want_gpu=True)
    if snap.gpu_percent is None:
        return live_gpu_bar(tick)
    return _metric_pie_frame("GPU", snap.gpu_percent)


def live_net_bar(tick: int) -> Frame:
    """Network throughput — maps mbps onto a soft 0–100 scale (cap 100 Mbps = full)."""
    snap = sample_system(want_net=True)
    pct = max(0.0, min(100.0, snap.net_mbps))
    frame = blank_frame()
    draw_centered_text(frame, "NET", 1, (200, 200, 210))
    for y in (7, 8):
        _draw_hbar(frame, y, pct, color=(40, 180, 255) if pct < 80 else usage_color(pct))
    if snap.net_mbps < 100:
        label = str(int(round(snap.net_mbps)))
    else:
        label = "HI"
    draw_centered_text(frame, label, 11, (40, 180, 255))
    return frame


def _build_animation_presets() -> list[AnimationPreset]:
    ensure_psutil_warmup()
    return [
        AnimationPreset("rainbow", "Rainbow", "static", build_static=anim_rainbow),
        AnimationPreset("rgb_smooth", "RGB Smooth", "static", build_static=anim_rgb_smooth),
        AnimationPreset("rgb_pulse", "RGB Pulse", "static", build_static=anim_rgb_pulse),
        AnimationPreset("rgb_jump", "RGB Jump", "static", build_static=anim_rgb_jump),
        AnimationPreset("rgb_flash", "RGB Flash x3", "static", build_static=anim_rgb_flash),
        AnimationPreset(
            "wb_fade", "White–Black Fade", "static", build_static=anim_white_black_fade
        ),
        AnimationPreset(
            "wb_collapse",
            "White–Black Collapse",
            "static",
            build_static=anim_white_black_collapse,
            frame_ms=80,
        ),
        AnimationPreset("clock", "Clock", "live", build_live=live_clock, frame_ms=500),
        AnimationPreset(
            "cpu_cores", "CPU Cores", "live", build_live=live_cpu_cores, frame_ms=1000
        ),
        AnimationPreset("cpu_bar", "CPU Bar", "live", build_live=live_cpu_bar, frame_ms=1000),
        AnimationPreset("cpu_pie", "CPU Pie", "live", build_live=live_cpu_pie, frame_ms=1000),
        AnimationPreset("ram_bar", "RAM Bar", "live", build_live=live_ram_bar, frame_ms=1500),
        AnimationPreset("ram_pie", "RAM Pie", "live", build_live=live_ram_pie, frame_ms=1500),
        AnimationPreset(
            "ram_blocks", "RAM Blocks", "live", build_live=live_ram_blocks, frame_ms=1500
        ),
        AnimationPreset(
            "disk_bar", "Storage Bar", "live", build_live=live_disk_bar, frame_ms=3000
        ),
        AnimationPreset(
            "disk_pie", "Storage Pie", "live", build_live=live_disk_pie, frame_ms=3000
        ),
        AnimationPreset("gpu_bar", "GPU Bar", "live", build_live=live_gpu_bar, frame_ms=2000),
        AnimationPreset("gpu_pie", "GPU Pie", "live", build_live=live_gpu_pie, frame_ms=2000),
        AnimationPreset(
            "net_bar", "Network Bar", "live", build_live=live_net_bar, frame_ms=1000
        ),
    ]


ANIMATION_PRESETS = _build_animation_presets()
ANIMATION_BY_LABEL = {p.label: p for p in ANIMATION_PRESETS}
