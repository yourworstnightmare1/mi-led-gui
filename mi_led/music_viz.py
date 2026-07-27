"""Music-reactive LED frame renderers (16x16)."""

from __future__ import annotations

import colorsys
import math
from typing import Callable

from .audio_capture import AudioFeatures, N_BANDS
from .image_convert import blank_frame
from .protocol import MATRIX_SIZE, PIXEL_COUNT

Frame = list[tuple[int, int, int]]
ModeFn = Callable[[AudioFeatures, float], Frame]


def _clamp_byte(v: float) -> int:
    return max(0, min(255, int(v)))


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (_clamp_byte(r * 255), _clamp_byte(g * 255), _clamp_byte(b * 255))


def _set(frame: Frame, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE:
        frame[y * MATRIX_SIZE + x] = color


def render_spectrum_bars(features: AudioFeatures, sensitivity: float) -> Frame:
    frame = blank_frame()
    sens = max(0.2, min(2.5, sensitivity))
    for x, band in enumerate(features.bands[:MATRIX_SIZE]):
        height = int(round(min(1.0, band * sens) * MATRIX_SIZE))
        for y in range(MATRIX_SIZE - height, MATRIX_SIZE):
            t = (MATRIX_SIZE - 1 - y) / max(1, MATRIX_SIZE - 1)
            color = _hsv(0.66 - t * 0.55, 0.95, 0.35 + 0.65 * (band * sens))
            _set(frame, x, y, color)
    return frame


def render_bass_pulse(features: AudioFeatures, sensitivity: float) -> Frame:
    frame = blank_frame()
    sens = max(0.2, min(2.5, sensitivity))
    strength = min(1.0, features.bass * sens)
    radius = 1.5 + strength * 9.0
    cx = cy = (MATRIX_SIZE - 1) / 2.0
    hue = 0.92 - features.level * 0.25
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            dist = math.hypot(x - cx, y - cy)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - dist / radius)
            v = (0.15 + 0.85 * strength) * (falloff ** 1.4)
            _set(frame, x, y, _hsv(hue, 0.9, v))
    return frame


def render_mirrored_eq(features: AudioFeatures, sensitivity: float) -> Frame:
    frame = blank_frame()
    sens = max(0.2, min(2.5, sensitivity))
    mid = MATRIX_SIZE // 2
    for i in range(mid):
        band = features.bands[min(len(features.bands) - 1, i * 2)]
        height = int(round(min(1.0, band * sens) * mid))
        color = _hsv(0.55 + i / mid * 0.35, 0.95, 0.4 + 0.6 * band)
        for dy in range(height):
            y_top = mid - 1 - dy
            y_bot = mid + dy
            _set(frame, i, y_top, color)
            _set(frame, MATRIX_SIZE - 1 - i, y_top, color)
            _set(frame, i, y_bot, color)
            _set(frame, MATRIX_SIZE - 1 - i, y_bot, color)
    return frame


def render_radial_bloom(features: AudioFeatures, sensitivity: float) -> Frame:
    frame = blank_frame()
    sens = max(0.2, min(2.5, sensitivity))
    level = min(1.0, features.level * sens)
    bass = min(1.0, features.bass * sens)
    cx = cy = (MATRIX_SIZE - 1) / 2.0
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            dist = math.hypot(x - cx, y - cy) / (MATRIX_SIZE * 0.75)
            wave = math.sin((1.0 - dist) * math.pi * (0.8 + bass * 2.2))
            v = max(0.0, wave) * (0.2 + 0.8 * level)
            if v <= 0.02:
                continue
            hue = (0.75 + dist * 0.4 + bass * 0.1) % 1.0
            _set(frame, x, y, _hsv(hue, 0.85, v))
    return frame


def render_volume_wash(features: AudioFeatures, sensitivity: float) -> Frame:
    sens = max(0.2, min(2.5, sensitivity))
    level = min(1.0, features.level * sens)
    bass = min(1.0, features.bass * sens)
    hue = (0.05 + level * 0.7) % 1.0
    base = _hsv(hue, 0.9, 0.12 + 0.88 * level)
    frame: Frame = [base] * PIXEL_COUNT
    # Brighten center with bass.
    cx = cy = (MATRIX_SIZE - 1) / 2.0
    glow = _hsv((hue + 0.08) % 1.0, 0.7, min(1.0, 0.3 + bass))
    radius = 2.0 + bass * 5.0
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            if math.hypot(x - cx, y - cy) <= radius:
                frame[y * MATRIX_SIZE + x] = glow
    return frame


MUSIC_MODES: list[tuple[str, str, ModeFn]] = [
    ("spectrum", "Spectrum Bars", render_spectrum_bars),
    ("pulse", "Bass Pulse", render_bass_pulse),
    ("mirror", "Mirrored EQ", render_mirrored_eq),
    ("bloom", "Radial Bloom", render_radial_bloom),
    ("wash", "Volume Wash", render_volume_wash),
]

MUSIC_MODE_BY_LABEL = {label: (key, fn) for key, label, fn in MUSIC_MODES}
MUSIC_MODE_LABELS = [label for _key, label, _fn in MUSIC_MODES]


def render_mode(label: str, features: AudioFeatures, sensitivity: float) -> Frame:
    entry = MUSIC_MODE_BY_LABEL.get(label)
    if entry is None:
        return render_spectrum_bars(features, sensitivity)
    return entry[1](features, sensitivity)
