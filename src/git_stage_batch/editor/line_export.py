"""Line export helpers for editor output."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .piece_table import LineLike


def ensure_line_chunk_boundaries(
    lines: Iterable[LineLike],
    *,
    default_line_ending: bytes = b"\n",
) -> Iterator[bytes]:
    """Terminate non-final logical lines while preserving the final EOF state."""
    if default_line_ending not in (b"\n", b"\r\n"):
        raise ValueError("default line ending must be LF or CRLF")

    previous_line: bytes | None = None
    for line in lines:
        current_line = _line_bytes(line)
        if previous_line is not None:
            if previous_line.endswith(b"\n"):
                yield previous_line
            else:
                if current_line.endswith(b"\r\n"):
                    current_ending = b"\r\n"
                elif current_line.endswith(b"\n"):
                    current_ending = b"\n"
                else:
                    current_ending = default_line_ending
                yield previous_line + current_ending
        previous_line = current_line

    if previous_line is not None:
        yield previous_line


def _line_bytes(line: LineLike) -> bytes:
    if isinstance(line, (bytes, bytearray, memoryview)):
        return bytes(line)
    if hasattr(line, "__bytes__"):
        return bytes(line)
    raise TypeError(f"expected bytes-compatible line, got {type(line).__name__}")
