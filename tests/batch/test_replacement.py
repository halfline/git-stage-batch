"""Tests for replacement batch-source helpers."""

from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim
from git_stage_batch.batch.replacement import (
    ReplacementBatchView,
    build_replacement_batch_view_from_lines,
)


def test_build_replacement_batch_view_accepts_non_list_line_sequences(line_sequence):
    """Replacement source construction accepts indexed byte-line sequences."""
    source_lines = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    view = build_replacement_batch_view_from_lines(
        source_lines,
        ownership,
        "new",
    )

    with view:
        assert isinstance(view, ReplacementBatchView)
        assert view.source_buffer.to_bytes() == b"line1\nnew\nline3\n"
        assert view.ownership.presence_line_set() == {2}


def test_build_replacement_batch_view_returns_named_result(line_sequence):
    """Replacement source construction names generated content and ownership."""
    source_lines = line_sequence([b"line1\n", b"line2\n"])
    ownership = BatchOwnership(
        [],
        [
            DeletionClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
            )
        ],
    )

    view = build_replacement_batch_view_from_lines(
        source_lines,
        ownership,
        "new",
    )

    with view:
        assert view.source_buffer.to_bytes() == b"line1\nnew\nline2\n"
        assert view.ownership.presence_line_set() == {2}
