"""Compose scrolling / static text over imported background frames."""

from __future__ import annotations

from .font import draw_text, glyph_height, normalize_scale, text_width
from .image_convert import blank_frame
from .protocol import MATRIX_SIZE, PIXEL_COUNT

Frame = list[tuple[int, int, int]]


def apply_brightness(frame: Frame, brightness: float) -> Frame:
    b = max(0.05, min(1.0, float(brightness)))
    if b >= 0.999:
        return list(frame)
    return [
        (max(0, min(255, int(r * b))), max(0, min(255, int(g * b))), max(0, min(255, int(bl * b))))
        for r, g, bl in frame
    ]


def _split_lines(text: str) -> list[str]:
    """Support up to two display lines (newline or explicit blanks)."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = raw.split("\n", 1)
    lines = [p.strip() for p in parts]
    # Drop trailing empty second line; keep a single blank as empty overlay.
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    if not lines:
        return []
    return lines[:2]


def line_block_height(scale: float | int, *, lines: int, line_gap: int = 1) -> int:
    lines = max(1, min(2, int(lines)))
    h = glyph_height(scale)
    if lines <= 1:
        return h
    return h * 2 + max(0, int(line_gap))


def render_text_frame(
    *,
    text: str,
    color: tuple[int, int, int],
    scale: float | int,
    background: Frame,
    scroll_x: int = 0,
    y: int | None = None,
    spacing: int = 1,
    line_gap: int = 1,
) -> Frame:
    """Copy background and overlay up to two lines of text (optionally scrolled)."""
    if len(background) != PIXEL_COUNT:
        bg = blank_frame()
    else:
        bg = list(background)

    lines = _split_lines(text)
    if not lines:
        return bg

    s = normalize_scale(scale)
    # Prefer slightly tighter spacing for tiny scales so more letters fit.
    if s < 1.0:
        spacing = 0
    h = glyph_height(s)
    total_h = line_block_height(s, lines=len(lines), line_gap=line_gap)
    if y is None:
        y = max(0, (MATRIX_SIZE - total_h) // 2)

    for i, line in enumerate(lines):
        if not line:
            continue
        ly = y + i * (h + max(0, int(line_gap)))
        # Each line scrolls independently from the same scroll_x origin.
        draw_text(bg, line, -int(scroll_x), ly, color, spacing=spacing, scale=s)
    return bg


def text_needs_scroll(text: str, scale: float | int = 1, spacing: int = 1) -> bool:
    s = normalize_scale(scale)
    if s < 1.0:
        spacing = 0
    return any(text_width(line, spacing=spacing, scale=s) > MATRIX_SIZE for line in _split_lines(text))


def max_line_width(text: str, scale: float | int = 1, spacing: int = 1) -> int:
    s = normalize_scale(scale)
    if s < 1.0:
        spacing = 0
    lines = _split_lines(text)
    if not lines:
        return 0
    return max(text_width(line, spacing=spacing, scale=s) for line in lines)
