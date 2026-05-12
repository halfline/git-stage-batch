"""Helpers for expressing replacement text in batch source space."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..core.line_selection import format_line_ids
from ..editor import EditorBuffer
from .ownership import BatchOwnership, DeletionClaim, ReplacementUnit


@dataclass(slots=True)
class ReplacementBatchView:
    """Batch source buffer and ownership produced for replacement text."""

    source_buffer: EditorBuffer
    ownership: BatchOwnership

    def close(self) -> None:
        """Close the generated source buffer."""
        self.source_buffer.close()

    def __enter__(self) -> ReplacementBatchView:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _format_presence_lines(line_numbers: list[int]) -> list[str]:
    """Format presence source line numbers as normalized range strings."""
    if not line_numbers:
        return []
    return [format_line_ids(line_numbers)]


def build_replacement_batch_view_from_lines(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    replacement_text: str,
) -> ReplacementBatchView:
    """Build replacement source content from an indexed byte-line sequence."""
    claimed_source_lines = sorted(ownership.presence_line_set())
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

        return ReplacementBatchView(
            source_buffer=EditorBuffer.from_chunks(
                _replacement_source_chunks(
                    source_lines=source_lines,
                    prefix_end=start_line - 1,
                    replacement_lines=replacement_lines,
                    suffix_start=end_line,
                )
            ),
            ownership=BatchOwnership.from_presence_lines(
                _format_presence_lines(new_claimed_lines),
                new_deletions,
                replacement_units=[
                    ReplacementUnit(
                        presence_lines=_format_presence_lines(new_claimed_lines),
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

    return ReplacementBatchView(
        source_buffer=EditorBuffer.from_chunks(
            _replacement_source_chunks(
                source_lines=source_lines,
                prefix_end=insert_at,
                replacement_lines=replacement_lines,
                suffix_start=insert_at,
            )
        ),
        ownership=BatchOwnership.from_presence_lines(
            _format_presence_lines(new_claimed_lines),
            new_deletions,
            replacement_units=[
                ReplacementUnit(
                    presence_lines=_format_presence_lines(new_claimed_lines),
                    deletion_indices=list(range(len(new_deletions))),
                )
            ] if new_claimed_lines and new_deletions else [],
        ),
    )


def _replacement_source_chunks(
    *,
    source_lines: Sequence[bytes],
    prefix_end: int,
    replacement_lines: Iterable[bytes],
    suffix_start: int,
) -> Iterable[bytes]:
    """Yield replacement source content without materializing source lines."""
    for line_index in range(prefix_end):
        yield source_lines[line_index]

    yield from replacement_lines

    for line_index in range(suffix_start, len(source_lines)):
        yield source_lines[line_index]
