"""Line-ending helpers for byte buffers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence


_BytesLike = bytes | bytearray | memoryview


def detect_line_ending(buffer: _BytesLike | Sequence[bytes]) -> bytes | None:
    """Return the first line ending used by a buffer."""
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        return _detect_line_ending_in_bytes(bytes(buffer))

    for line in buffer:
        line_ending = _detect_line_ending_from_line_suffix(line)
        if line_ending is not None:
            return line_ending
    return None


def _detect_line_ending_in_bytes(data: bytes) -> bytes | None:
    lf_index = data.find(b"\n")
    if lf_index != -1:
        if lf_index > 0 and data[lf_index - 1:lf_index] == b"\r":
            return b"\r\n"
        return b"\n"

    if b"\r" in data:
        return b"\r"
    return None


def _detect_line_ending_from_line_suffix(line: bytes) -> bytes | None:
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    if line.endswith(b"\r"):
        return b"\r"
    return None


def choose_line_ending(*buffers: Sequence[bytes]) -> bytes | None:
    """Return the first line ending found in the provided buffers."""
    for buffer in buffers:
        line_ending = detect_line_ending(buffer)
        if line_ending is not None:
            return line_ending
    return None


def restore_line_endings(data: bytes, line_ending: bytes | None) -> bytes:
    """Restore normalized LF output to a chosen line ending."""
    if line_ending in (None, b"\n"):
        return data
    return data.replace(b"\n", line_ending)


def restore_line_endings_in_chunks(
    chunks: Iterable[bytes],
    line_ending: bytes | None,
) -> Iterator[bytes]:
    """Restore normalized LF output chunks to a chosen line ending."""
    if line_ending in (None, b"\n"):
        yield from chunks
        return

    for chunk in chunks:
        yield chunk.replace(b"\n", line_ending)
