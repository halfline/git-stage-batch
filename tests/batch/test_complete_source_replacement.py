"""Tests for complete saved/live source replacement snapshots."""

from git_stage_batch.batch.complete_source_replacement import (
    resolve_complete_source_replacement,
)
from git_stage_batch.batch.file_state import SourceBoundOwnership
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.coordinates import (
    BatchSourceSpace,
    LineBoundary,
    LineSpan,
    content_snapshot,
)


def _complete_ownership(
    path: str,
    source_lines: LineBuffer,
    *,
    saved_line_count: int,
) -> SourceBoundOwnership:
    live_lines = source_lines[saved_line_count:]
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{saved_line_count}"],
        [
            AbsenceClaim(
                content_lines=live_lines,
                source_alternative=True,
                complete_file_pair=True,
            )
        ],
        replacement_units=[ReplacementUnit([f"1-{saved_line_count}"], [0])],
    )
    return SourceBoundOwnership(
        content_snapshot(path, source_lines, space=BatchSourceSpace),
        ownership,
    )


def test_resolve_complete_source_replacement_binds_both_file_snapshots() -> None:
    """The strict persisted shape becomes two snapshot-bound spans."""
    with LineBuffer.from_chunks(
        [b"saved one\n", b"saved two\n", b"live one\n"]
    ) as source_lines:
        resolved = resolve_complete_source_replacement(
            source_lines,
            _complete_ownership(
                "file.txt",
                source_lines,
                saved_line_count=2,
            ),
        )

        assert resolved is not None
        assert resolved.saved.span == LineSpan(
            LineBoundary(0),
            LineBoundary(2),
        )
        assert resolved.live.span == LineSpan(
            LineBoundary(2),
            LineBoundary(3),
        )
