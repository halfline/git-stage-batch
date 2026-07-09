"""Line export helpers for editor output."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from ..core.buffer import LineBuffer
from .line_endings import detect_line_ending, restore_line_endings_in_chunks
from .piece_table import LineLike


def export_lines_as_buffer(
    lines: Iterable[LineLike],
    *,
    has_trailing_newline: bool = True,
    add_trailing_newline_when_nonempty: bool = False,
    line_endings_from: Sequence[bytes] | None = None,
) -> LineBuffer:
    """Export generated lines to a buffer without editor state."""
    chunks = line_body_chunks(
        (line_body(line) for line in lines),
        has_trailing_newline=has_trailing_newline,
        add_trailing_newline_when_nonempty=(
            add_trailing_newline_when_nonempty
        ),
    )
    if line_endings_from is not None:
        chunks = restore_line_endings_in_chunks(
            chunks,
            detect_line_ending(line_endings_from),
        )
    return LineBuffer.from_chunks(chunks)


def line_body(line: LineLike) -> bytes:
    """Return one line without a trailing line ending."""
    line_bytes = _line_bytes(line)
    if line_bytes.endswith(b"\r\n"):
        return line_bytes[:-2]
    if line_bytes.endswith(b"\n"):
        return line_bytes[:-1]
    return line_bytes


def line_body_chunks(
    lines: Iterable[bytes],
    *,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> Iterator[bytes]:
    """Yield line bodies joined by LF according to final-newline settings."""
    previous_line = b""
    has_previous_line = False
    for line in lines:
        if has_previous_line:
            yield previous_line + b"\n"
        previous_line = line
        has_previous_line = True

    if not has_previous_line:
        return

    if has_trailing_newline or add_trailing_newline_when_nonempty:
        yield previous_line + b"\n"
    else:
        yield previous_line


def _line_bytes(line: LineLike) -> bytes:
    if isinstance(line, (bytes, bytearray, memoryview)):
        return bytes(line)
    if hasattr(line, "__bytes__"):
        return bytes(line)
    raise TypeError(f"expected bytes-compatible line, got {type(line).__name__}")
