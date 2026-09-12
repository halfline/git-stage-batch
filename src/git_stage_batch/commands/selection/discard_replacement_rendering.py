"""Rebuild a displayed diff while retaining explicitly owned replacement rows."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace

from ...batch.line_matching.line_range_view import LineRangeView
from ...core.buffer import LineBuffer
from ...core.coordinates import FileSnapshot, RewrittenWorktreeSpace
from ...core.models import LineLevelChange
from ...data.file_hunk_display import build_file_hunk_from_buffer
from ...utils.repository_buffers import read_git_object_buffer_or_empty
from .discard_replacement_selection import (
    _line_body,
)


def _rewritten_replacement_new_range(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    rewritten_lines: LineBuffer,
    *,
    original_working_line_count: int,
    replacement_start: int,
    replacement_end: int,
) -> tuple[int, int]:
    """Return the rewritten-file line range occupied by replacement payload."""
    if not any(
        line.id is not None and line.id in selected_ids for line in line_changes.lines
    ):
        raise ValueError("replacement selection has no file coordinates")
    replacement_line_count = (
        len(rewritten_lines)
        - original_working_line_count
        + replacement_end
        - replacement_start
    )
    return (
        replacement_start + 1,
        replacement_start + max(replacement_line_count, 0),
    )


def _build_rewritten_line_changes(
    path: str,
    rewritten_lines: LineBuffer,
    *,
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace],
    materialized_new_start: int | None,
    materialized_new_end: int | None,
) -> LineLevelChange | None:
    """Build a diff that shows every replacement line as selectable.

    Git may match new text to identical old text and show it as unchanged.
    Replace the saved lines with unique markers while building the diff, then
    put their real bytes back into the resulting added rows.
    """
    line_changes = build_file_hunk_from_buffer(path, rewritten_lines)
    if materialized_new_start is None and materialized_new_end is None:
        return line_changes
    if materialized_new_start is None or materialized_new_end is None:
        raise ValueError("materialized replacement span is incomplete")
    if materialized_new_start > materialized_new_end:
        return line_changes
    if materialized_new_start < 1 or materialized_new_end > len(rewritten_lines):
        raise ValueError("materialized replacement span exceeds rewritten file")
    if line_changes is not None and _rewritten_span_is_additions(
        line_changes,
        start=materialized_new_start,
        end=materialized_new_end,
    ):
        return line_changes

    with read_git_object_buffer_or_empty(f"HEAD:{path}") as baseline_lines:
        mask_prefix = _unique_replacement_mask_prefix(
            rewritten_snapshot,
            baseline_lines,
            rewritten_lines,
        )
        with LineBuffer.from_chunks(
            _masked_replacement_chunks(
                rewritten_lines,
                start=materialized_new_start,
                end=materialized_new_end,
                mask_prefix=mask_prefix,
            )
        ) as masked_lines:
            line_changes = build_file_hunk_from_buffer(path, masked_lines)

    if line_changes is None:
        return None
    _restore_masked_replacement_rows(
        line_changes,
        rewritten_lines,
        start=materialized_new_start,
        end=materialized_new_end,
        mask_prefix=mask_prefix,
    )
    return line_changes


def _rewritten_span_is_additions(
    line_changes: LineLevelChange,
    *,
    start: int,
    end: int,
) -> bool:
    """Return whether every rewritten line in a span has an addition row."""
    expected_new_line = start
    for line in line_changes.lines:
        new_line_number = line.new_line_number
        if new_line_number is None or new_line_number < start:
            continue
        if new_line_number > end:
            break
        if line.kind != "+" or new_line_number != expected_new_line:
            return False
        expected_new_line += 1
    return expected_new_line == end + 1


def _unique_replacement_mask_prefix(
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace],
    baseline_lines: Sequence[bytes],
    rewritten_lines: Sequence[bytes],
) -> bytes:
    """Return a line prefix absent from both real diff endpoints."""
    stem = (
        b"git-stage-batch:explicit-replacement-mask:"
        + rewritten_snapshot.identity.value.encode("utf-8")
        + b":"
    )
    attempt = 0
    while True:
        candidate = stem + str(attempt).encode("ascii") + b":"
        if not any(
            _line_body(line).startswith(candidate)
            for lines in (baseline_lines, rewritten_lines)
            for line in lines
        ):
            return candidate
        attempt += 1


def _masked_replacement_chunks(
    rewritten_lines: Sequence[bytes],
    *,
    start: int,
    end: int,
    mask_prefix: bytes,
) -> Iterator[bytes]:
    """Yield the rewritten file with one line span replaced by unique markers."""
    yield from LineRangeView(rewritten_lines, 0, start - 1)
    for new_line_number in range(start, end + 1):
        original_line = rewritten_lines[new_line_number - 1]
        yield (
            mask_prefix
            + str(new_line_number).encode("ascii")
            + (b"\n" if original_line.endswith(b"\n") else b"")
        )
    yield from LineRangeView(rewritten_lines, end, len(rewritten_lines))


def _restore_masked_replacement_rows(
    line_changes: LineLevelChange,
    rewritten_lines: Sequence[bytes],
    *,
    start: int,
    end: int,
    mask_prefix: bytes,
) -> None:
    """Restore real bytes in the marked addition rows."""
    expected_new_line = start
    for index, line in enumerate(line_changes.lines):
        if line.kind != "+" or not line.text_bytes.startswith(mask_prefix):
            continue
        suffix = line.text_bytes[len(mask_prefix) :]
        if not suffix.isdigit():
            raise ValueError("materialized replacement mask has an invalid line")
        new_line_number = int(suffix)
        if (
            new_line_number != expected_new_line
            or line.new_line_number != new_line_number
        ):
            raise ValueError("materialized replacement rows are out of order")
        original_line = rewritten_lines[new_line_number - 1]
        line_changes.lines[index] = replace(
            line,
            text_bytes=(
                original_line[:-1] if original_line.endswith(b"\n") else original_line
            ),
            has_trailing_newline=original_line.endswith(b"\n"),
        )
        expected_new_line += 1
    if expected_new_line != end + 1:
        raise ValueError("materialized replacement rows are missing from the diff")
