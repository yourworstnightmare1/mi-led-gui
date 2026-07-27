"""Tiny bitmap font for 16x16 LED frames."""

from __future__ import annotations

import math

# 3x5 glyphs — each row is a 3-bit mask (MSB = left).
_GLYPHS: dict[str, tuple[int, int, int, int, int]] = {
    " ": (0b000, 0b000, 0b000, 0b000, 0b000),
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b001, 0b001, 0b001),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
    "A": (0b010, 0b101, 0b111, 0b101, 0b101),
    "B": (0b110, 0b101, 0b110, 0b101, 0b110),
    "C": (0b011, 0b100, 0b100, 0b100, 0b011),
    "D": (0b110, 0b101, 0b101, 0b101, 0b110),
    "E": (0b111, 0b100, 0b110, 0b100, 0b111),
    "F": (0b111, 0b100, 0b110, 0b100, 0b100),
    "G": (0b011, 0b100, 0b101, 0b101, 0b011),
    "H": (0b101, 0b101, 0b111, 0b101, 0b101),
    "I": (0b111, 0b010, 0b010, 0b010, 0b111),
    "J": (0b001, 0b001, 0b001, 0b101, 0b010),
    "K": (0b101, 0b101, 0b110, 0b101, 0b101),
    "L": (0b100, 0b100, 0b100, 0b100, 0b111),
    "M": (0b101, 0b111, 0b111, 0b101, 0b101),
    "N": (0b101, 0b111, 0b111, 0b111, 0b101),
    "O": (0b010, 0b101, 0b101, 0b101, 0b010),
    "P": (0b110, 0b101, 0b110, 0b100, 0b100),
    "Q": (0b010, 0b101, 0b101, 0b111, 0b001),
    "R": (0b110, 0b101, 0b110, 0b101, 0b101),
    "S": (0b011, 0b100, 0b010, 0b001, 0b110),
    "T": (0b111, 0b010, 0b010, 0b010, 0b010),
    "U": (0b101, 0b101, 0b101, 0b101, 0b111),
    "V": (0b101, 0b101, 0b101, 0b101, 0b010),
    "W": (0b101, 0b101, 0b111, 0b111, 0b101),
    "X": (0b101, 0b101, 0b010, 0b101, 0b101),
    "Y": (0b101, 0b101, 0b010, 0b010, 0b010),
    "Z": (0b111, 0b001, 0b010, 0b100, 0b111),
    "%": (0b101, 0b001, 0b010, 0b100, 0b101),
    ":": (0b000, 0b010, 0b000, 0b010, 0b000),
    "-": (0b000, 0b000, 0b111, 0b000, 0b000),
    ".": (0b000, 0b000, 0b000, 0b000, 0b010),
    "!": (0b010, 0b010, 0b010, 0b000, 0b010),
    "?": (0b111, 0b001, 0b010, 0b000, 0b010),
    "'": (0b010, 0b010, 0b000, 0b000, 0b000),
    "+": (0b000, 0b010, 0b111, 0b010, 0b000),
}

GLYPH_W = 3
GLYPH_H = 5


def normalize_scale(scale: float | int) -> float:
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    return max(0.4, min(3.0, value))


def glyph_height(scale: float | int = 1) -> int:
    return max(1, int(math.ceil(GLYPH_H * normalize_scale(scale))))


def glyph_advance(scale: float | int = 1, spacing: int = 1) -> float:
    return GLYPH_W * normalize_scale(scale) + spacing


def draw_text(
    frame: list[tuple[int, int, int]],
    text: str,
    x: float,
    y: float,
    color: tuple[int, int, int],
    *,
    size: int = 16,
    spacing: int = 1,
    scale: float | int = 1,
) -> None:
    """
    Blit 3x5 text into a row-major ``size``x``size`` frame.

    ``scale`` may be fractional (e.g. 0.5, 0.7) or integer (1, 2, 3).
    """
    s = normalize_scale(scale)
    cx = float(x)
    for ch in text.upper():
        glyph = _GLYPHS.get(ch, _GLYPHS[" "])
        out_w = max(1, int(math.ceil(GLYPH_W * s)))
        out_h = max(1, int(math.ceil(GLYPH_H * s)))
        for oy in range(out_h):
            for ox in range(out_w):
                # Stretch the 3x5 glyph across the scaled footprint.
                gx = min(GLYPH_W - 1, (ox * GLYPH_W) // out_w)
                gy = min(GLYPH_H - 1, (oy * GLYPH_H) // out_h)
                row = glyph[gy]
                if not (row & (0b100 >> gx)):
                    continue
                px = int(round(cx + ox))
                py = int(round(y + oy))
                if 0 <= px < size and 0 <= py < size:
                    frame[py * size + px] = color
        cx += GLYPH_W * s + spacing


def text_width(text: str, spacing: int = 1, scale: float | int = 1) -> int:
    if not text:
        return 0
    s = normalize_scale(scale)
    n = len(text)
    return int(math.ceil(n * GLYPH_W * s + max(0, n - 1) * spacing))


def draw_centered_text(
    frame: list[tuple[int, int, int]],
    text: str,
    y: float,
    color: tuple[int, int, int],
    *,
    size: int = 16,
    spacing: int = 1,
    scale: float | int = 1,
) -> None:
    w = text_width(text, spacing, scale=scale)
    x = max(0, (size - w) // 2)
    draw_text(frame, text, x, y, color, size=size, spacing=spacing, scale=scale)
