"""Tests for batch source refresh helpers."""

from __future__ import annotations

import git_stage_batch.batch.source.refresh as source_refresh
from git_stage_batch.batch.source.refresh import (
    RefreshedBatchSelection,
    ensure_batch_source_current_for_selection,
    prepare_initial_batch_source_for_selection,
)
from git_stage_batch.batch.source.selected_line_refresh import (
    refresh_selected_lines_against_new_source,
    refresh_selected_lines_against_source_lines,
)
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.source.advancement import (
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


def test_prepare_initial_batch_source_maps_selection(monkeypatch):
    """A first session source receives matching selection coordinates."""
    lines = [
        LineEntry(
            id=1, kind='+', old_line_number=None, new_line_number=2,
            text_bytes=b"new line", text="new line", source_line=None
        ),
    ]
    cached_sources = {}
    monkeypatch.setattr(
        source_refresh,
        "load_session_batch_sources",
        lambda: dict(cached_sources),
    )
    monkeypatch.setattr(
        source_refresh,
        "save_session_batch_sources",
        lambda sources: cached_sources.update(sources),
    )
    monkeypatch.setattr(
        source_refresh,
        "create_batch_source_commit",
        lambda _file_path: "new_source",
    )
    monkeypatch.setattr(
        source_refresh,
        "read_git_object_buffer_or_none",
        lambda _object_name: LineBuffer.from_bytes(
            b"header\nremoved earlier\nnew line\n"
        ),
    )
    monkeypatch.setattr(
        source_refresh,
        "load_working_tree_file_as_buffer",
        lambda _file_path: LineBuffer.from_bytes(b"header\nnew line\n"),
    )

    batch_source_commit, prepared_lines = (
        prepare_initial_batch_source_for_selection(
            "test.py",
            lines,
        )
    )

    assert batch_source_commit == "new_source"
    assert prepared_lines[0].source_line == 3
def test_refresh_consecutive_leading_deletions_share_file_start_anchor():
    """Later rows in one leading deletion run must remain before line one."""
    first_deletion = LineEntry(
        id=1,
        kind="-",
        old_line_number=1,
        new_line_number=None,
        text_bytes=b"first",
        source_line=None,
    )
    second_deletion = LineEntry(
        id=2,
        kind="-",
        old_line_number=2,
        new_line_number=None,
        text_bytes=b"second",
        source_line=1,
    )
    trailing_context = LineEntry(
        id=None,
        kind=" ",
        old_line_number=3,
        new_line_number=1,
        text_bytes=b"remaining",
        source_line=1,
    )

    refreshed = refresh_selected_lines_against_new_source(
        [second_deletion],
        coordinate_lines=[
            first_deletion,
            second_deletion,
            trailing_context,
        ],
    )

    assert refreshed[0].source_line is None


def test_refresh_translates_deletion_anchor_after_synthetic_gap():
    """A later hunk must not inherit source context from an earlier hunk."""
    selected_deletion = LineEntry(
        id=2,
        kind="-",
        old_line_number=5,
        new_line_number=None,
        text_bytes=b"deleted",
        source_line=None,
    )
    coordinate_lines = [
        LineEntry(
            id=1,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"added",
        ),
        LineEntry(
            id=None,
            kind=" ",
            old_line_number=1,
            new_line_number=2,
            text_bytes=b"one",
        ),
        LineEntry(
            id=None,
            kind=" ",
            old_line_number=None,
            new_line_number=None,
            text_bytes=b"... 3 more lines ...",
        ),
        selected_deletion,
        LineEntry(
            id=None,
            kind=" ",
            old_line_number=6,
            new_line_number=6,
            text_bytes=b"six",
        ),
    ]
    working_lines = [
        b"added\n",
        b"one\n",
        b"two\n",
        b"three\n",
        b"four\n",
        b"six\n",
    ]

    refreshed_new = refresh_selected_lines_against_new_source(
        [selected_deletion],
        coordinate_lines=coordinate_lines,
    )
    refreshed_mapped = refresh_selected_lines_against_source_lines(
        [selected_deletion],
        source_lines=working_lines,
        working_lines=working_lines,
        coordinate_lines=coordinate_lines,
    )

    assert refreshed_new[0].source_line == 5
    assert refreshed_mapped[0].source_line == 5


def test_source_refresh_preserves_missing_final_newline():
    """Coordinate refresh must not change selected-line byte identity."""
    selected_lines = [
        LineEntry(
            id=1,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"unterminated",
            source_line=None,
            has_trailing_newline=False,
        ),
    ]

    refreshed_new = refresh_selected_lines_against_new_source(selected_lines)
    refreshed_mapped = refresh_selected_lines_against_source_lines(
        selected_lines,
        source_lines=[b"unterminated"],
        working_lines=[b"unterminated"],
    )

    assert refreshed_new[0].has_trailing_newline is False
    assert refreshed_mapped[0].has_trailing_newline is False
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
