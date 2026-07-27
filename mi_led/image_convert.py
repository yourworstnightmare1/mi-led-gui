"""Image loading and LED-friendly conversion for the 16x16 matrix."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance

from .protocol import MATRIX_SIZE, PIXEL_COUNT


def load_image_as_matrix(
    path: str | Path,
    *,
    saturation: float = 1.35,
    contrast: float = 1.1,
) -> list[tuple[int, int, int]]:
    """
    Load an image and convert it to a 16x16 RGB pixel list (row-major).

    Applies a mild saturation/contrast boost so soft photos read better on LEDs.
    GIF animations use the first frame only.
    """
    path = Path(path)
    with Image.open(path) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        frame = img.convert("RGBA")

    # Composite onto black so transparent pixels become off LEDs.
    background = Image.new("RGBA", frame.size, (0, 0, 0, 255))
    composited = Image.alpha_composite(background, frame).convert("RGB")

    resized = composited.resize((MATRIX_SIZE, MATRIX_SIZE), Image.Resampling.LANCZOS)
    boosted = ImageEnhance.Color(resized).enhance(saturation)
    boosted = ImageEnhance.Contrast(boosted).enhance(contrast)

    pixels: list[tuple[int, int, int]] = []
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            r, g, b = boosted.getpixel((x, y))
            pixels.append((int(r), int(g), int(b)))

    if len(pixels) != PIXEL_COUNT:
        raise RuntimeError("Converted image has unexpected pixel count")
    return pixels


def blank_frame(color: tuple[int, int, int] = (0, 0, 0)) -> list[tuple[int, int, int]]:
    return [color] * PIXEL_COUNT
