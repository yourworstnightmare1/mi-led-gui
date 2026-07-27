"""Preview transforms so the on-screen canvas better matches the physical LEDs."""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import MATRIX_SIZE, PIXEL_COUNT


@dataclass
class PreviewStyle:
    """How to emulate the MI Matrix diffuser + LED response in the GUI."""

    enabled: bool = True
    # <1 brightens midtones (LEDs look punchier than sRGB).
    gamma: float = 0.72
    brightness: float = 1.12
    saturation: float = 1.05
    # Shift greens/yellows toward the warm chartreuse the panel tends to show.
    yellow_push: float = 0.12
    # Soft neighbor bleed through the diffuser (0 = sharp cells).
    bloom: float = 0.0


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _apply_pixel(r: int, g: int, b: int, style: PreviewStyle) -> tuple[int, int, int]:
    if r == 0 and g == 0 and b == 0:
        return 0, 0, 0

    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0

    # Gamma: LED midtones read brighter than the same values on a monitor.
    inv = max(0.05, float(style.gamma))
    rf, gf, bf = rf ** inv, gf ** inv, bf ** inv

    # Saturation around luma.
    luma = 0.2126 * rf + 0.7152 * gf + 0.0722 * bf
    sat = float(style.saturation)
    rf = luma + (rf - luma) * sat
    gf = luma + (gf - luma) * sat
    bf = luma + (bf - luma) * sat

    # Warm yellow-green bias typical of this diffused RGB matrix.
    push = max(0.0, min(0.5, float(style.yellow_push)))
    if push > 0:
        gf = gf + (rf - gf) * (push * 0.35) + push * 0.08
        rf = rf + push * 0.10
        bf = bf * (1.0 - push * 0.45)

    bright = max(0.5, float(style.brightness))
    rf, gf, bf = rf * bright, gf * bright, bf * bright

    return _clamp_byte(rf * 255), _clamp_byte(gf * 255), _clamp_byte(bf * 255)


def preview_rgb(
    r: int, g: int, b: int, style: PreviewStyle | None = None
) -> tuple[int, int, int]:
    """Map a stored LED RGB value to a monitor color that better matches the panel."""
    if style is None or not style.enabled:
        return int(r), int(g), int(b)
    return _apply_pixel(int(r), int(g), int(b), style)


def preview_frame(
    pixels: list[tuple[int, int, int]], style: PreviewStyle | None = None
) -> list[tuple[int, int, int]]:
    """
    Transform a full 16×16 frame for on-screen preview.

    When bloom > 0, each cell picks up a little light from its neighbors to
    approximate the diffuser glow on the real hardware.
    """
    if style is None or not style.enabled:
        return list(pixels)
    if len(pixels) != PIXEL_COUNT:
        raise ValueError(f"expected {PIXEL_COUNT} pixels")

    mapped = [_apply_pixel(r, g, b, style) for r, g, b in pixels]
    bloom = max(0.0, min(0.6, float(style.bloom)))
    if bloom <= 0:
        return mapped

    out: list[tuple[int, int, int]] = []
    for y in range(MATRIX_SIZE):
        for x in range(MATRIX_SIZE):
            idx = y * MATRIX_SIZE + x
            br, bg, bb = (float(c) for c in mapped[idx])
            weight = 1.0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < MATRIX_SIZE and 0 <= ny < MATRIX_SIZE):
                        continue
                    nr, ng, nb = mapped[ny * MATRIX_SIZE + nx]
                    # Diagonal neighbors contribute less than orthogonal ones.
                    w = bloom * (0.55 if dx != 0 and dy != 0 else 1.0)
                    br += nr * w
                    bg += ng * w
                    bb += nb * w
                    weight += w
            out.append(
                (
                    _clamp_byte(br / weight),
                    _clamp_byte(bg / weight),
                    _clamp_byte(bb / weight),
                )
            )
    return out
