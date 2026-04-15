"""Byte streaming utilities for splitting chunks into lines."""

from __future__ import annotations

from collections.abc import Iterable, Iterator


def normalize_line_endings(content: bytes) -> bytes:
    """Normalize line endings to LF for comparison.

    Converts CRLF (\\r\\n) and CR (\\r) to LF (\\n) for consistent
    comparison across different line ending styles.

    Args:
        content: Byte content with any line ending style

    Returns:
        Content with all line endings normalized to LF

    Examples:
        >>> normalize_line_endings(b"hello\\r\\nworld\\n")
        b'hello\\nworld\\n'

        >>> normalize_line_endings(b"mac\\rclassic\\n")
        b'mac\\nclassic\\n'
    """
    return content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def bytes_to_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Split byte chunks at \\n boundaries, preserving exact bytes and terminators.

    Git diff format uses \\n as the line terminator on all platforms, regardless
    of the file's actual line endings. This function splits on \\n while preserving
    all other bytes exactly, including \\r characters that are part of the content.

    This preserves:
    - Handling files with any encoding (UTF-8, Latin-1, etc.)
    - Preserving CRLF (\\r\\n) vs LF (\\n) line endings
    - Not corrupting binary-looking content that git treats as text

    Args:
        chunks: Iterable of byte chunks to split

    Yields:
        Lines including their \\n terminator (except possibly the last line)

    Examples:
        >>> list(bytes_to_lines([b"a\\nb", b"c\\n"]))
        [b'a\\n', b'bc\\n']

        >>> list(bytes_to_lines([b"a\\r\\nb\\n"]))
        [b'a\\r\\n', b'b\\n']

        >>> list(bytes_to_lines([b"no newline"]))
        [b'no newline']
    """
    buffer = bytearray()

    for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"expected bytes-like object, got {type(chunk).__name__}"
            )

        buffer.extend(chunk)

        # Find and yield complete lines (O(n) using find with offset)
        while True:
            idx = buffer.find(b'\n')
            if idx == -1:
                break
            yield bytes(buffer[:idx + 1])  # Include the \n
            del buffer[:idx + 1]

    # Yield any remaining bytes (last line without \n)
    if buffer:
        yield bytes(buffer)
