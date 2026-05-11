"""Tests for batch source refresh helpers.

These tests verify the centralized stale-source repair logic
and ensure include/discard commands use the same helpers.
"""

from __future__ import annotations


import inspect
from git_stage_batch.commands import include, discard

from git_stage_batch.batch.source_refresh import (
    RefreshedBatchSelection,
    PreparedBatchUpdate,
    _refresh_selected_lines_against_source_buffer,
    _refresh_selected_lines_against_source_lines,
    ensure_batch_source_current_for_selection,
    prepare_batch_ownership_update_for_selection,
)
from git_stage_batch.batch.ownership import (
    BatchOwnership,
    _advance_source_lines_preserving_existing_presence,
)
from git_stage_batch.core.models import LineEntry
from git_stage_batch.editor import EditorBuffer


def _advance_source_from_content(
    *,
    old_source_buffer: bytes,
    working_buffer: bytes,
    ownership: BatchOwnership,
):
    with (
        EditorBuffer.from_bytes(old_source_buffer) as old_source_lines,
        EditorBuffer.from_bytes(working_buffer) as working_lines,
    ):
        return _advance_source_lines_preserving_existing_presence(
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


def test_prepared_batch_update_dataclass():
    """Test PreparedBatchUpdate dataclass construction."""
    ownership = BatchOwnership.from_presence_lines(["1-3"], [])

    update = PreparedBatchUpdate(
        batch_source_commit="def456",
        ownership_before=None,
        ownership_after=ownership
    )

    assert update.batch_source_commit == "def456"
    assert update.ownership_before is None
    assert update.ownership_after == ownership


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


def test_prepare_batch_ownership_update_first_time_stale_blank_context():
    """First-time stale selections re-annotate blank context before translation."""
    lines = [
        LineEntry(
            id=None, kind=' ', old_line_number=1, new_line_number=1,
            text_bytes=b"", text="", source_line=None
        ),
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=2,
            text_bytes=b"new line", text="new line", source_line=None
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after.presence_claims[0].source_lines == ["1-2"]


def test_prepare_batch_ownership_update_first_time_deletion_anchor():
    """First-time deletion-only selections keep their source anchor."""
    lines = [
        LineEntry(
            id=1, kind='-', old_line_number=2, new_line_number=None,
            text_bytes=b"old line", text="old line", source_line=None
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after.deletions[0].anchor_line == 1


def test_prepare_batch_ownership_update_first_time():
    """Test prepare_batch_ownership_update_for_selection for first-time add."""
    # Lines with valid source_line (first add to batch)
    lines = [
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=1,
            text_bytes=b"line1\n", text="line1\n", source_line=1
        ),
        LineEntry(
            id=2, kind='+', old_line_number=None, new_line_number=2,
            text_bytes=b"line2\n", text="line2\n", source_line=2
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit=None,
        existing_ownership=None,
        selected_lines=lines
    )

    assert result.batch_source_commit is None
    assert result.ownership_before is None
    assert result.ownership_after is not None
    assert result.ownership_after.presence_claims[0].source_lines == ["1-2"]


def test_prepare_batch_ownership_update_with_existing():
    """Test prepare_batch_ownership_update_for_selection merging with existing ownership."""
    # Existing ownership claims lines 1-2
    existing = BatchOwnership.from_presence_lines(["1-2"], [])

    # New lines claim lines 3-4
    lines = [
        LineEntry(
            id=3, kind='+', old_line_number=None, new_line_number=3,
            text_bytes=b"line3\n", text="line3\n", source_line=3
        ),
        LineEntry(
            id=4, kind='+', old_line_number=None, new_line_number=4,
            text_bytes=b"line4\n", text="line4\n", source_line=4
        ),
    ]

    result = prepare_batch_ownership_update_for_selection(
        batch_name="test-batch",
        file_path="test.py",
        current_batch_source_commit="source123",
        existing_ownership=existing,
        selected_lines=lines
    )

    assert result.ownership_before == existing
    assert result.ownership_after is not None
    # Should merge 1-2 with 3-4
    assert "1-4" in ",".join(result.ownership_after.presence_claims[0].source_lines)


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

        refreshed = _refresh_selected_lines_against_source_buffer(
            selected_lines,
            source_buffer=source_with_provenance.source_buffer,
            working_buffer=None,
            working_line_map=source_with_provenance.working_line_map,
        )

    assert [line.source_line for line in refreshed] == [3, 4]


def test_refresh_selected_lines_buffer_accepts_non_list_line_sequences(line_sequence):
    """Source refresh can use already indexed line sequences."""
    selected_lines = [
        LineEntry(
            id=None, kind=' ', old_line_number=2, new_line_number=2,
            text_bytes=b"line3", text="line3", source_line=None
        ),
    ]

    refreshed = _refresh_selected_lines_against_source_buffer(
        selected_lines,
        source_buffer=line_sequence([b"line1\n", b"line2\n", b"line3\n"]),
        working_buffer=line_sequence([b"line1\n", b"line3\n"]),
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

    refreshed = _refresh_selected_lines_against_source_lines(
        selected_lines,
        source_lines=line_sequence([b"line1\n", b"line2\n", b"line3\n"]),
        working_lines=line_sequence([b"line1\n", b"line3\n"]),
    )

    assert refreshed[0].source_line == 3


def test_both_commands_use_same_helper_interface():
    """Test that both include and discard use prepare_batch_ownership_update_for_selection.

    This test verifies the refactoring succeeded: both command paths now use
    the same centralized helper instead of duplicated inline logic.
    """

    # Read the source code of include and discard modules

    include_source = inspect.getsource(include)
    discard_source = inspect.getsource(discard)

    # Both should import from batch.source_refresh
    assert "from ..batch.source_refresh import prepare_batch_ownership_update_for_selection" in include_source
    assert "from ..batch.source_refresh import prepare_batch_ownership_update_for_selection" in discard_source

    # Both should call prepare_batch_ownership_update_for_selection
    assert "prepare_batch_ownership_update_for_selection(" in include_source
    assert "prepare_batch_ownership_update_for_selection(" in discard_source

    # Neither should have the old inline implementation pattern
    # (checking they don't manually handle stale source advancement)
    assert "ensure_batch_source_current_and_reannotate" not in include_source
    assert "ensure_batch_source_current_and_reannotate" not in discard_source
