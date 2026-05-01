"""Helpers for expressing replacement text in batch source space."""

from __future__ import annotations

from ..core.line_selection import format_line_ids, parse_line_selection
from .ownership import BatchOwnership, DeletionClaim, ReplacementUnit


def _format_claimed_lines(line_numbers: list[int]) -> list[str]:
    """Format claimed line numbers as normalized range strings."""
    if not line_numbers:
        return []
    return [format_line_ids(line_numbers)]


def build_replacement_batch_view(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    replacement_text: str,
) -> tuple[bytes, BatchOwnership]:
    """Build a temporary batch-source snapshot and ownership for replacement text."""
    source_lines = batch_source_content.splitlines(keepends=True)
    claimed_source_lines = sorted({
        line_num
        for line_range in ownership.claimed_lines
        for line_num in parse_line_selection(line_range)
    }) if ownership.claimed_lines else []
    replacement_bytes = replacement_text.encode("utf-8", errors="surrogateescape")
    replacement_lines = [line + b"\n" for line in replacement_bytes.splitlines()]

    if claimed_source_lines:
        expected_claimed = list(range(claimed_source_lines[0], claimed_source_lines[-1] + 1))
        if claimed_source_lines != expected_claimed:
            raise ValueError("Replacement selection must resolve to one contiguous batch-source line range.")

        start_line = claimed_source_lines[0]
        end_line = claimed_source_lines[-1]
        removed_count = end_line - start_line + 1
        added_count = len(replacement_lines)

        new_source_lines = (
            source_lines[:start_line - 1]
            + replacement_lines
            + source_lines[end_line:]
        )

        new_claimed_lines = list(range(start_line, start_line + added_count))
        new_deletions = []
        for deletion in ownership.deletions:
            anchor = deletion.anchor_line
            if anchor is None:
                new_anchor = None
            elif anchor < start_line:
                new_anchor = anchor
            elif anchor > end_line:
                new_anchor = anchor - removed_count + added_count
            elif added_count > 0:
                new_anchor = start_line + added_count - 1
            elif start_line > 1:
                new_anchor = start_line - 1
            else:
                new_anchor = None

            new_deletions.append(DeletionClaim(
                anchor_line=new_anchor,
                content_lines=deletion.content_lines,
            ))

        return (
            b"".join(new_source_lines),
            BatchOwnership(
                claimed_lines=_format_claimed_lines(new_claimed_lines),
                deletions=new_deletions,
                replacement_units=[
                    ReplacementUnit(
                        claimed_lines=_format_claimed_lines(new_claimed_lines),
                        deletion_indices=list(range(len(new_deletions))),
                    )
                ] if new_claimed_lines and new_deletions else [],
            ),
        )

    distinct_anchors = {deletion.anchor_line for deletion in ownership.deletions}
    if len(distinct_anchors) > 1:
        raise ValueError("Replacement selection must resolve to one contiguous batch-source region.")

    anchor_line = next(iter(distinct_anchors), None)
    insert_at = 0 if anchor_line is None else anchor_line
    added_count = len(replacement_lines)

    new_source_lines = (
        source_lines[:insert_at]
        + replacement_lines
        + source_lines[insert_at:]
    )
    if added_count == 0:
        new_claimed_lines: list[int] = []
    elif anchor_line is None:
        new_claimed_lines = list(range(1, added_count + 1))
    else:
        new_claimed_lines = list(range(anchor_line + 1, anchor_line + added_count + 1))

    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            new_anchor = None
        elif anchor_line is None:
            new_anchor = deletion.anchor_line + added_count
        elif deletion.anchor_line <= anchor_line:
            new_anchor = deletion.anchor_line
        else:
            new_anchor = deletion.anchor_line + added_count

        new_deletions.append(DeletionClaim(
            anchor_line=new_anchor,
            content_lines=deletion.content_lines,
        ))

    return (
        b"".join(new_source_lines),
        BatchOwnership(
            claimed_lines=_format_claimed_lines(new_claimed_lines),
            deletions=new_deletions,
            replacement_units=[
                ReplacementUnit(
                    claimed_lines=_format_claimed_lines(new_claimed_lines),
                    deletion_indices=list(range(len(new_deletions))),
                )
            ] if new_claimed_lines and new_deletions else [],
        ),
    )
