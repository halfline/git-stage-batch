"""Byte streaming utilities for splitting chunks into lines."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import nullcontext
from typing import (
    Any,
    ContextManager,
    Protocol,
    TypeVar,
    cast,
    overload,
    runtime_checkable,
)


_LineT = TypeVar("_LineT")


@runtime_checkable
class AcquirableLineSequence(Protocol[_LineT]):
    """Indexed line sequence that can expose scoped line views."""

    def __len__(self) -> int: ...

    @overload
    def __getitem__(self, index: int) -> _LineT: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_LineT]: ...

    def acquire_lines(self) -> ContextManager[Sequence[_LineT]]: ...


class _PlainAcquirableLineSequence(Sequence[_LineT]):
    """Acquirable adapter for ordinary indexed line sequences."""

    def __init__(self, lines: Sequence[_LineT]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    @overload
    def __getitem__(self, index: int) -> _LineT: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_LineT]: ...

    def __getitem__(self, index: int | slice) -> _LineT | Sequence[_LineT]:
        return self._lines[index]

    def acquire_lines(self) -> ContextManager[Sequence[_LineT]]:
        return nullcontext(self)


def as_acquirable_line_sequence(
    lines: Sequence[_LineT],
) -> AcquirableLineSequence[_LineT]:
    """Return an indexed line sequence with scoped acquisition support."""
    if isinstance(lines, AcquirableLineSequence):
        return cast(AcquirableLineSequence[_LineT], lines)
    return _PlainAcquirableLineSequence(lines)


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


def normalize_line_ending(line: bytes) -> bytes:
    """Normalize one line entry's terminator to LF."""
    if line.endswith(b'\r\n'):
        return line[:-2] + b'\n'
    if line.endswith(b'\r'):
        return line[:-1] + b'\n'
    return line


class _LineEndingNormalizedSequence(Sequence[bytes]):
    """Normalize line endings for an existing line sequence on access."""

    def __init__(self, lines: Sequence[bytes]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    def acquire_lines(self) -> Any:
        """Return a scoped normalized line sequence."""
        acquire_lines = getattr(self._lines, "acquire_lines", None)
        if acquire_lines is None:
            return nullcontext(self)
        return _AcquiredNormalizedLineSequence(acquire_lines())

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> list[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | list[bytes]:
        if isinstance(index, slice):
            return [self[line_index] for line_index in range(*index.indices(len(self)))]

        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)

        return normalize_line_ending(self._lines[index])


class _AcquiredNormalizedLineSequence(Sequence[bytes]):
    """Context-managed normalized view over acquired line views."""

    def __init__(self, line_context: Any) -> None:
        self._line_context = line_context
        self._lines: _LineEndingNormalizedSequence | None = None

    def __enter__(self) -> _AcquiredNormalizedLineSequence:
        self._lines = _LineEndingNormalizedSequence(self._line_context.__enter__())
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._lines = None
        self._line_context.__exit__(exc_type, exc, traceback)

    def __len__(self) -> int:
        return len(self._require_lines())

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> list[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | list[bytes]:
        return self._require_lines()[index]

    def _require_lines(self) -> _LineEndingNormalizedSequence:
        if self._lines is None:
            raise ValueError("line view is closed")
        return self._lines


def normalize_line_sequence_endings(lines: Sequence[bytes]) -> Sequence[bytes]:
    """Return a line sequence with CRLF/CR terminators normalized to LF."""
    return _LineEndingNormalizedSequence(lines)


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
