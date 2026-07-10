"""Tests for line-change body builder helpers."""

from __future__ import annotations

from git_stage_batch.core.line_change_body import LineChangeBodyBuilder
from git_stage_batch.core.models import HunkHeader


def test_body_builder_appends_context_deletion_and_addition_lines():
    """Body builder should assign display IDs and line numbers."""
    builder = LineChangeBodyBuilder()

    builder.reset_for_hunk_header(HunkHeader(10, 2, 20, 2))
    builder.append_patch_line(b" context")
    builder.append_patch_line(b"-old")
    builder.append_patch_line(b"+new")

    assert [(entry.kind, entry.id) for entry in builder.line_entries] == [
        (" ", None),
        ("-", 1),
        ("+", 2),
    ]
    assert [
        (entry.old_line_number, entry.new_line_number)
        for entry in builder.line_entries
    ] == [
        (10, 20),
        (11, None),
        (None, 21),
    ]
    assert builder.old_line_number == 12
    assert builder.new_line_number == 22


def test_body_builder_marks_previous_line_without_trailing_newline():
    """No-newline markers should update the previous parsed body line."""
    builder = LineChangeBodyBuilder()

    builder.reset_for_hunk_header(HunkHeader(1, 1, 1, 1))
    builder.append_patch_line(b"+new")
    builder.append_patch_line(b"\\ No newline at end of file")

    assert builder.line_entries[0].has_trailing_newline is False


def test_body_builder_treats_empty_and_unknown_lines_as_context():
    """Empty and unknown body lines should be represented as context."""
    builder = LineChangeBodyBuilder()

    builder.reset_for_hunk_header(HunkHeader(3, 2, 4, 2))
    builder.append_patch_line(b"")
    builder.append_patch_line(b"!body")

    assert [(entry.kind, entry.text_bytes) for entry in builder.line_entries] == [
        (" ", b""),
        (" ", b"body"),
    ]
    assert builder.old_line_number == 5
    assert builder.new_line_number == 6
