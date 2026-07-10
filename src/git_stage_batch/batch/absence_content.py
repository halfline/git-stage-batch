"""Streaming builders for absence-claim content."""

from __future__ import annotations

from collections.abc import Sequence

from ..core.buffer import (
    LineBuffer,
    buffer_byte_chunks,
)
from ..editor.line_editor import LineEditor


class AbsenceContentBuilder:
    """Build absence content as a LineBuffer from appended line ranges."""

    def __init__(self) -> None:
        self._editor: LineEditor | None = LineEditor(())

    def __enter__(self) -> AbsenceContentBuilder:
        self._check_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def append_line_range(
        self,
        lines: Sequence[bytes],
        start: int,
        end: int,
    ) -> None:
        editor = self._check_open()
        editor.append_line_range(lines, start, end)

    def finish(self) -> LineBuffer:
        editor = self._check_open()
        try:
            return LineBuffer.from_chunks(editor.line_chunks())
        finally:
            self.close()

    def close(self) -> None:
        editor = self._editor
        if editor is None:
            return

        self._editor = None
        editor.close()

    def _check_open(self) -> LineEditor:
        editor = self._editor
        if editor is None:
            raise RuntimeError("absence content builder is closed")

        return editor


def copy_absence_content(content_lines: Sequence[bytes]) -> LineBuffer:
    """Copy absence content into an owned LineBuffer."""
    if isinstance(content_lines, LineBuffer):
        return LineBuffer.from_chunks(buffer_byte_chunks(content_lines))
    return build_absence_content_from_range(content_lines, 0, len(content_lines))


def build_absence_content_from_range(
    content_lines: Sequence[bytes],
    start: int,
    end: int,
) -> LineBuffer:
    """Build an owned LineBuffer from a source line range."""
    with AbsenceContentBuilder() as builder:
        builder.append_line_range(content_lines, start, end)
        return builder.finish()
