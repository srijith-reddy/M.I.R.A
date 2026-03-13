from __future__ import annotations

import struct


def inspect_image_bytes(payload: bytes) -> tuple[str | None, int | None, int | None]:
    """
    Best-effort image metadata extraction using only the standard library.

    Supported headers:
    - PNG
    - JPEG
    - GIF
    """

    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        width, height = struct.unpack(">II", payload[16:24])
        return "image/png", int(width), int(height)

    if payload.startswith((b"GIF87a", b"GIF89a")) and len(payload) >= 10:
        width, height = struct.unpack("<HH", payload[6:10])
        return "image/gif", int(width), int(height)

    if payload.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(payload)
        return "image/jpeg", width, height

    return None, None, None


def _jpeg_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    index = 2
    length = len(payload)
    while index + 9 < length:
        if payload[index] != 0xFF:
            index += 1
            continue
        marker = payload[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > length:
            break
        segment_length = struct.unpack(">H", payload[index : index + 2])[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3} and index + 7 < length:
            height, width = struct.unpack(">HH", payload[index + 3 : index + 7])
            return int(width), int(height)
        index += segment_length
    return None, None
