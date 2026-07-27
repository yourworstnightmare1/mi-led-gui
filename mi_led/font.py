"""Tiny bitmap font for 16x16 LED frames."""

from __future__ import annotations

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
    "K": (0b101, 0b101, 0b110, 0b101, 0b101),
    "L": (0b100, 0b100, 0b100, 0b100, 0b111),
    "M": (0b101, 0b111, 0b111, 0b101, 0b101),
    "N": (0b101, 0b111, 0b111, 0b111, 0b101),
    "O": (0b010, 0b101, 0b101, 0b101, 0b010),
    "P": (0b110, 0b101, 0b110, 0b100, 0b100),
    "R": (0b110, 0b101, 0b110, 0b101, 0b101),
    "S": (0b011, 0b100, 0b010, 0b001, 0b110),
    "T": (0b111, 0b010, 0b010, 0b010, 0b010),
    "U": (0b101, 0b101, 0b101, 0b101, 0b111),
    "V": (0b101, 0b101, 0b101, 0b101, 0b010),
    "W": (0b101, 0b101, 0b111, 0b111, 0b101),
    "X": (0b101, 0b101, 0b010, 0b101, 0b101),
    "Y": (0b101, 0b101, 0b010, 0b010, 0b010),
    "%": (0b101, 0b001, 0b010, 0b100, 0b101),
    ":": (0b000, 0b010, 0b000, 0b010, 0b000),
    "-": (0b000, 0b000, 0b111, 0b000, 0b000),
    ".": (0b000, 0b000, 0b000, 0b000, 0b010),
}

GLYPH_W = 3
GLYPH_H = 5


def draw_text(
    frame: list[tuple[int, int, int]],
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    *,
    size: int = 16,
    spacing: int = 1,
) -> None:
    """Blit 3x5 text into a row-major `size`x`size` frame."""
    cx = x
    for ch in text.upper():
        glyph = _GLYPHS.get(ch, _GLYPHS[" "])
        for gy, row in enumerate(glyph):
            for gx in range(GLYPH_W):
                if row & (0b100 >> gx):
                    px, py = cx + gx, y + gy
                    if 0 <= px < size and 0 <= py < size:
                        frame[py * size + px] = color
        cx += GLYPH_W + spacing


def text_width(text: str, spacing: int = 1) -> int:
    if not text:
        return 0
    return len(text) * GLYPH_W + (len(text) - 1) * spacing


def draw_centered_text(
    frame: list[tuple[int, int, int]],
    text: str,
    y: int,
    color: tuple[int, int, int],
    *,
    size: int = 16,
    spacing: int = 1,
) -> None:
    w = text_width(text, spacing)
    x = max(0, (size - w) // 2)
    draw_text(frame, text, x, y, color, size=size, spacing=spacing)
