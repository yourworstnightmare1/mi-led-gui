"""BLE command builders for the MI Matrix Display."""

from __future__ import annotations

SERVICE_UUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ffd1-0000-1000-8000-00805f9b34fb"

DEVICE_NAME_HINT = "MI Matrix Display"
MATRIX_SIZE = 16
PIXEL_COUNT = MATRIX_SIZE * MATRIX_SIZE
BLOCK_PIXELS = 32
BLOCK_COUNT = PIXEL_COUNT // BLOCK_PIXELS

POWER_OFF = bytes.fromhex("bcff00ff55")
POWER_ON = bytes.fromhex("bcff010055")

GRAFFITI_INIT = (
    bytes.fromhex("bc00010155"),
    bytes.fromhex("bc000d0d55"),
)

FULL_FRAME_START = bytes.fromhex("bc0ff1080855")
FULL_FRAME_END = bytes.fromhex("bc0ff2080955")


def pixel_index(x: int, y: int) -> int:
    if not (0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE):
        raise ValueError(f"Pixel ({x}, {y}) out of range")
    return y * MATRIX_SIZE + x


def set_pixel_command(pixel_index_: int, r: int, g: int, b: int) -> bytearray:
    """Build a graffiti-mode single-pixel update command."""
    if not (0 <= pixel_index_ < PIXEL_COUNT):
        raise ValueError(f"Pixel index {pixel_index_} out of range")

    end_index = (pixel_index_ + 1) % 256
    if pixel_index_ == 0:
        end_index = 0xFF

    return bytearray(
        [
            0xBC,
            0x01,
            0x01,
            0x00,
            pixel_index_,
            r & 0xFF,
            g & 0xFF,
            b & 0xFF,
            end_index,
            0x55,
        ]
    )


def full_picture_block_command(block_index: int, block_pixels: list[tuple[int, int, int]]) -> bytearray:
    """Build one of the 8 full-frame blocks (32 RGB pixels each)."""
    if not (0 <= block_index < BLOCK_COUNT):
        raise ValueError(f"Block index {block_index} out of range")
    if len(block_pixels) != BLOCK_PIXELS:
        raise ValueError("block_pixels must contain exactly 32 pixels")

    header = bytearray(3)
    header[0] = 0xBC
    header[1] = 0x0F
    header[2] = (block_index + 1) & 0xFF

    pixel_data = bytearray()
    for r, g, b in block_pixels:
        pixel_data.extend([r & 0xFF, g & 0xFF, b & 0xFF])

    return header + pixel_data + bytearray([0x55])


def iter_full_frame_commands(picture: list[tuple[int, int, int]]):
    """Yield start, block, and end commands for a 256-pixel RGB frame."""
    if len(picture) != PIXEL_COUNT:
        raise ValueError(f"Picture must have {PIXEL_COUNT} pixels")

    yield FULL_FRAME_START
    for block_index in range(BLOCK_COUNT):
        start = block_index * BLOCK_PIXELS
        end = start + BLOCK_PIXELS
        yield full_picture_block_command(block_index, picture[start:end])
    yield FULL_FRAME_END
