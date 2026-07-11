"""Tests for batch source refresh helpers."""

from __future__ import annotations

from git_stage_batch.batch.source_refresh import (
    RefreshedBatchSelection,
    ensure_batch_source_current_for_selection,
)
from git_stage_batch.batch.selected_line_source_refresh import (
    refresh_selected_lines_against_source_lines,
)
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.source_advancement import (
    advance_source_lines_preserving_existing_presence,
)
from git_stage_batch.core.models import LineEntry
from git_stage_batch.core.buffer import LineBuffer


def _advance_source_from_content(
    *,
    old_source_buffer: bytes,
    working_buffer: bytes,
    ownership: BatchOwnership,
):
    with (
        LineBuffer.from_bytes(old_source_buffer) as old_source_lines,
        LineBuffer.from_bytes(working_buffer) as working_lines,
    ):
        return advance_source_lines_preserving_existing_presence(
            old_lines=old_source_lines,
            working_lines=working_lines,
            ownership=ownership,
        )


def test_refreshed_batch_selection_dataclass():
    """Test RefreshedBatchSelection dataclass construction."""
    refresh = RefreshedBatchSelection(
        batch_source_commit="abc123",
        ownership=None,
        selected_lines=[],
        source_was_advanced=False
    )

    assert refresh.batch_source_commit == "abc123"
    assert refresh.ownership is None
    assert refresh.selected_lines == []
    assert refresh.source_was_advanced is False


def test_ensure_batch_source_current_non_stale_source():
    """Test ensure_batch_source_current_for_selection with non-stale source."""
    # Lines with valid source_line values (not stale)
    lines = [
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=1,
            text_bytes=b"new line", text="new line", source_line=1
        ),
    ]

    ownership = BatchOwnership.from_presence_lines(["1"], [])

    # Should return original values unchanged
    result = ensure_batch_source_current_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit="old_source",
        existing_ownership=ownership,
        selected_lines=lines
    )

    assert result.batch_source_commit == "old_source"
    assert result.ownership == ownership
    assert result.selected_lines == lines
    assert result.source_was_advanced is False


def test_ensure_batch_source_current_first_time_stale():
    """Test ensure_batch_source_current_for_selection for first-time discard."""
    # Lines with source_line=None (stale) but no existing ownership
    lines = [
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=1,
            text_bytes=b"new line", text="new line", source_line=None
        ),
    ]

    # First time - stale is normal, but ownership translation still needs
    # source-space line numbers before add_file_to_batch creates the source.
    result = ensure_batch_source_current_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership is None
    assert result.selected_lines[0].source_line == 1
    assert result.source_was_advanced is False


def test_refresh_selected_lines_uses_synthesized_working_line_provenance():
    """Repeated working lines should use known synthesis identity."""
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])
    with _advance_source_from_content(
        old_source_buffer=b"owned before\nsame\nsame\nowned after\n",
        working_buffer=b"same\nsame\n",
        ownership=ownership,
    ) as source_with_provenance:
        selected_lines = [
            LineEntry(
                id=1, kind='+', old_line_number=None, new_line_number=1,
                text_bytes=b"same", text="same", source_line=None
            ),
            LineEntry(
                id=2, kind='+', old_line_number=None, new_line_number=2,
                text_bytes=b"same", text="same", source_line=None
            ),
        ]

        refreshed = refresh_selected_lines_against_source_lines(
            selected_lines,
            source_lines=source_with_provenance.source_buffer,
            working_lines=(),
            lineage=source_with_provenance.lineage,
        )

    assert [line.source_line for line in refreshed] == [3, 4]


def test_refresh_selected_lines_accepts_non_list_source_sequences(line_sequence):
    """Source refresh can use already indexed line sequences."""
    selected_lines = [
        LineEntry(
            id=None, kind=' ', old_line_number=2, new_line_number=2,
            text_bytes=b"line3", text="line3", source_line=None
        ),
    ]

    refreshed = refresh_selected_lines_against_source_lines(
        selected_lines,
        source_lines=line_sequence([b"line1\n", b"line2\n", b"line3\n"]),
        working_lines=line_sequence([b"line1\n", b"line3\n"]),
    )

    assert refreshed[0].source_line == 3


def test_refresh_selected_lines_accepts_non_list_line_sequences(line_sequence):
    """Source refresh matching only requires sized indexable line sequences."""
    selected_lines = [
        LineEntry(
            id=None, kind=' ', old_line_number=2, new_line_number=2,
            text_bytes=b"line3", text="line3", source_line=None
        ),
    ]

    refreshed = refresh_selected_lines_against_source_lines(
        selected_lines,
        source_lines=line_sequence([b"line1\n", b"line2\n", b"line3\n"]),
        working_lines=line_sequence([b"line1\n", b"line3\n"]),
    )

    assert refreshed[0].source_line == 3
