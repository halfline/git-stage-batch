"""Editor mutation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from .buffer import EditorBuffer


def edit_lines_as_buffer(
    source_lines: Sequence[bytes],
    edited_lines: Iterable[bytes],
    *,
    selection_start: int,
    selection_end: int,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> EditorBuffer:
    """Apply edited lines to an indexed selection and return a buffer."""
    if (
        selection_start < 0
        or selection_end < selection_start
        or selection_end > len(source_lines)
    ):
        raise ValueError("invalid line selection")

    def output_lines() -> Iterator[bytes]:
        for line_index in range(selection_start):
            yield source_lines[line_index]
        yield from edited_lines
        for line_index in range(selection_end, len(source_lines)):
            yield source_lines[line_index]

    return EditorBuffer.from_chunks(
        _line_body_chunks(
            (_line_body(line) for line in output_lines()),
            has_trailing_newline=has_trailing_newline,
            add_trailing_newline_when_nonempty=(
                add_trailing_newline_when_nonempty
            ),
        )
    )


def _line_body(line: bytes) -> bytes:
    if not isinstance(line, bytes):
        raise TypeError(f"expected bytes object, got {type(line).__name__}")
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _line_body_chunks(
    lines: Iterable[bytes],
    *,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> Iterator[bytes]:
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
