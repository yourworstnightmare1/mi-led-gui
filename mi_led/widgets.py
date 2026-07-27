"""Reusable UI widgets for the MI LED GUI."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Optional

from .color_preview import PreviewStyle, preview_frame, preview_rgb
from .image_convert import blank_frame
from .protocol import MATRIX_SIZE, pixel_index


_HEX_CACHE: dict[tuple[int, int, int], str] = {}


def rgb_to_hex(r: int, g: int, b: int) -> str:
    key = (r, g, b)
    cached = _HEX_CACHE.get(key)
    if cached is None:
        cached = f"#{r:02x}{g:02x}{b:02x}"
        # Bound growth — animation palettes are tiny, but protect anyway.
        if len(_HEX_CACHE) > 4096:
            _HEX_CACHE.clear()
        _HEX_CACHE[key] = cached
    return cached


class MatrixThumb(tk.Canvas):
    """Tiny flat 16×16 thumbnail — cheap enough for a panel strip."""

    def __init__(
        self,
        master,
        *,
        cell_size: int = 4,
        on_click: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        size = MATRIX_SIZE * cell_size
        kwargs.setdefault("width", size)
        kwargs.setdefault("height", size)
        kwargs.setdefault("bg", "#0a0a0a")
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", "#333333")
        kwargs.setdefault("cursor", "hand2")
        super().__init__(master, **kwargs)
        self.cell_size = cell_size
        self.on_click = on_click
        self.pixels = blank_frame()
        self._rects: list[int] = []
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                x0 = x * cell_size
                y0 = y * cell_size
                rid = self.create_rectangle(
                    x0,
                    y0,
                    x0 + cell_size,
                    y0 + cell_size,
                    outline="",
                    fill="#000000",
                )
                self._rects.append(rid)
        self.bind("<Button-1>", self._clicked)

    def _clicked(self, _event=None) -> None:
        if self.on_click is not None:
            self.on_click()

    def set_selected(self, selected: bool) -> None:
        self.configure(highlightbackground="#4ea1ff" if selected else "#333333")

    def set_pixels(self, pixels: list[tuple[int, int, int]]) -> None:
        if len(pixels) != len(self.pixels):
            return
        self.pixels = list(pixels)
        for i, (r, g, b) in enumerate(self.pixels):
            self.itemconfigure(self._rects[i], fill=rgb_to_hex(r, g, b))


def _bresenham(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Inclusive line from (x0,y0) to (x1,y1)."""
    points: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def _create_round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    """Draw a rounded rectangle approximating the diffused LED cells."""
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


class MatrixCanvas(tk.Canvas):
    """16×16 LED matrix preview / paint surface."""

    def __init__(
        self,
        master,
        *,
        cell_size: int = 28,
        pad: int = 3,
        editable: bool = False,
        on_paint: Optional[Callable[[int, int, tuple[int, int, int]], None]] = None,
        on_paint_end: Optional[Callable[[], None]] = None,
        get_color: Optional[Callable[[], tuple[int, int, int]]] = None,
        preview_style: Optional[PreviewStyle] = None,
        **kwargs,
    ):
        size = MATRIX_SIZE * cell_size + 2
        kwargs.setdefault("width", size)
        kwargs.setdefault("height", size)
        kwargs.setdefault("bg", "#0a0a0a")
        kwargs.setdefault("highlightthickness", 0)
        if editable:
            kwargs.setdefault("cursor", "crosshair")
        super().__init__(master, **kwargs)

        self.cell_size = cell_size
        self.pad = pad
        self.editable = editable
        self.on_paint = on_paint
        self.on_paint_end = on_paint_end
        self.get_color = get_color or (lambda: (255, 0, 0))
        self.preview_style = preview_style
        self.pixels: list[tuple[int, int, int]] = blank_frame()
        self._painting = False
        self._last_painted: Optional[tuple[int, int]] = None
        self._cell_ids: list[int] = []

        corner = max(4, cell_size // 4)
        for y in range(MATRIX_SIZE):
            for x in range(MATRIX_SIZE):
                x0 = x * cell_size + pad
                y0 = y * cell_size + pad
                x1 = (x + 1) * cell_size - pad
                y1 = (y + 1) * cell_size - pad
                cell = _create_round_rect(
                    self,
                    x0,
                    y0,
                    x1,
                    y1,
                    corner,
                    fill="#000000",
                    outline="",
                    width=0,
                )
                self._cell_ids.append(cell)

        if editable:
            self.bind("<ButtonPress-1>", self._on_paint_start)
            self.bind("<B1-Motion>", self._on_paint_drag)
            self.bind("<ButtonRelease-1>", self._on_paint_end)

    def set_preview_style(self, style: Optional[PreviewStyle]) -> None:
        self.preview_style = style
        self.refresh()

    def set_pixels(self, pixels: list[tuple[int, int, int]]) -> None:
        if len(pixels) != len(self.pixels):
            raise ValueError("Pixel count mismatch")
        self.pixels = list(pixels)
        self.refresh()

    def set_pixels_fast(self, pixels: list[tuple[int, int, int]]) -> None:
        """Update only changed cells (no LED preview transforms)."""
        if len(pixels) != len(self.pixels):
            raise ValueError("Pixel count mismatch")
        old = self.pixels
        cell_ids = self._cell_ids
        for i, color in enumerate(pixels):
            if color != old[i]:
                old[i] = color
                self.itemconfigure(cell_ids[i], fill=rgb_to_hex(*color))

    def get_pixels(self) -> list[tuple[int, int, int]]:
        return list(self.pixels)

    def refresh(self) -> None:
        shown = preview_frame(self.pixels, self.preview_style)
        for i, color in enumerate(shown):
            self.itemconfigure(self._cell_ids[i], fill=rgb_to_hex(*color))

    def clear(self, color: tuple[int, int, int] = (0, 0, 0)) -> None:
        self.pixels = blank_frame(color)
        self.refresh()

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        idx = pixel_index(x, y)
        self.pixels[idx] = color
        style = self.preview_style
        if style is not None and style.enabled and style.bloom > 0:
            self.refresh()
            return
        shown = preview_rgb(*color, style)
        self.itemconfigure(self._cell_ids[idx], fill=rgb_to_hex(*shown))

    def _event_to_xy(self, event) -> Optional[tuple[int, int]]:
        x = event.x // self.cell_size
        y = event.y // self.cell_size
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
        if self.on_paint_end is not None:
            self.on_paint_end()

    def _paint_at_event(self, event) -> None:
        pos = self._event_to_xy(event)
        if pos is None:
            return
        if pos == self._last_painted:
            return

        color = self.get_color()
        if self._last_painted is None:
            path = [pos]
        else:
            path = _bresenham(self._last_painted[0], self._last_painted[1], pos[0], pos[1])
            path = path[1:]

        self._last_painted = pos
        for x, y in path:
            self.set_pixel(x, y, color)
            if self.on_paint is not None:
                self.on_paint(x, y, color)
