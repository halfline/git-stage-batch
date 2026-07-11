"""Batch display and line selection utilities."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from ...i18n import ngettext

if TYPE_CHECKING:
    from .model import BatchOwnership
    from .absence_claims import AbsenceClaim


LineForDisplay = TypeVar("LineForDisplay", bytes, str)


def _decode_display_line(line: bytes) -> str:
    return line.decode("utf-8", errors="replace")


def build_display_lines_from_batch_source_lines(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    context_lines: int | None = None,
) -> list[dict]:
    """Build display representation from indexed batch-source lines."""
    return _build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=context_lines,
        source_line_to_text=_decode_display_line,
    )


def _build_display_lines_from_batch_source_lines(
    source_lines: Sequence[LineForDisplay],
    ownership: 'BatchOwnership',
    context_lines: int | None,
    *,
    source_line_to_text: Callable[[LineForDisplay], str],
) -> list[dict]:
    """Build display representation from indexed batch-source lines."""
    if context_lines is None:
        context_lines = 0
    claimed_set = ownership.presence_line_set()

    display_lines = []
    display_id = 1

    # Build map of absence claim positions
    deletions_by_position: dict[int | None, list[tuple[int, 'AbsenceClaim']]] = {}
    for idx, claim in enumerate(ownership.deletions):
        anchor = claim.anchor_line
        if anchor not in deletions_by_position:
            deletions_by_position[anchor] = []
        deletions_by_position[anchor].append((idx, claim))

    # Add deletions at start of file (anchor=None)
    if None in deletions_by_position:
        for idx, claim in deletions_by_position[None]:
            for line_bytes in claim.content_lines:
                line_str = line_bytes.decode("utf-8", errors="replace")
                display_lines.append({
                    "id": display_id,
                    "type": "deletion",
                    "deletion_index": idx,
                    "content": line_str
                })
                display_id += 1

    # Collect displayed source ranges without expanding claimed ranges to lines.
    display_ranges: list[tuple[int, int]] = []

    def add_display_range(start: int, end: int) -> None:
        if start <= end:
            display_ranges.append((start, end))

    for claimed_start, claimed_end in claimed_set.ranges():
        add_display_range(
            max(1, claimed_start - context_lines),
            claimed_end + context_lines,
        )

    for position in deletions_by_position:
        if position is not None:
            add_display_range(
                max(1, position - context_lines),
                position + context_lines,
            )

    if None in deletions_by_position and context_lines > 0:
        add_display_range(1, context_lines)

    ranges: list[tuple[int, int]] = []
    for start, end in sorted(display_ranges):
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))

    # Add claimed lines, source context, and deletions in batch source order.
    # Context prevents unrelated owned lines from being visually glued together
    # (for example, showing a function header followed by its closing paren while
    # omitting the unchanged signature/body between them).
    previous_range_end: int | None = None
    for range_start, range_end in ranges:
        if previous_range_end is not None:
            omitted_line_count = range_start - previous_range_end - 1
            if omitted_line_count > 0:
                display_lines.append({
                    "id": None,
                    "type": "gap",
                    "omitted_line_count": omitted_line_count,
                    "content": ngettext(
                        "... {count} more line ...",
                        "... {count} more lines ...",
                        omitted_line_count,
                    ).format(count=omitted_line_count) + "\n"
                })

        for batch_line_num in range(range_start, range_end + 1):
            source_line = _source_line_or_none(source_lines, batch_line_num)
            if source_line is not None:
                if batch_line_num in claimed_set:
                    display_lines.append({
                        "id": display_id,
                        "type": "claimed",
                        "source_line": batch_line_num,
                        "content": source_line_to_text(
                            source_line
                        )
                    })
                    display_id += 1
                else:
                    display_lines.append({
                        "id": None,
                        "type": "context",
                        "source_line": batch_line_num,
                        "content": source_line_to_text(
                            source_line
                        )
                    })

            # Add deletions after this line
            if batch_line_num in deletions_by_position:
                for idx, claim in deletions_by_position[batch_line_num]:
                    for line_bytes in claim.content_lines:
                        line_str = line_bytes.decode("utf-8", errors="replace")
                        display_lines.append({
                            "id": display_id,
                            "type": "deletion",
                            "deletion_index": idx,
                            "content": line_str
                        })
                        display_id += 1

        previous_range_end = range_end

    return display_lines


def _source_line_or_none(
    source_lines: Sequence[LineForDisplay],
    line_number: int,
) -> LineForDisplay | None:
    if line_number < 1:
        return None
    try:
        return source_lines[line_number - 1]
    except IndexError:
        return None
