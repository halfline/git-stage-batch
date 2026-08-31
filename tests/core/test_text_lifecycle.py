"""Tests for text lifecycle classification."""

from __future__ import annotations

from git_stage_batch.core.text_lifecycle import (
    TextFileChangeType,
    resolve_text_change_type,
    selected_text_discard_change_type,
    selected_text_target_change_type,
    sifted_text_path_change_type,
)
from git_stage_batch.core.buffer import LineBuffer


def test_resolve_text_change_type_accepts_line_sequences(line_sequence):
    """Lifecycle classification can compare indexed line sequences."""
    source_buffer = line_sequence([b"one\n", b"two\n"])
    realized_buffer = line_sequence([b"one\n", b"two\n"])

    change_type = resolve_text_change_type(
        file_path="new.txt",
        baseline_exists=False,
        batch_source_content=source_buffer,
        realized_content=realized_buffer,
        working_exists=True,
    )

    assert change_type == TextFileChangeType.ADDED


def test_resolve_text_change_type_detects_empty_line_sequence():
    """Deletion classification can read an empty indexed line sequence."""
    change_type = resolve_text_change_type(
        file_path="deleted.txt",
        baseline_exists=True,
        batch_source_content=b"",
        realized_content=[],
        requested_change_type=TextFileChangeType.DELETED,
        working_exists=False,
    )

    assert change_type == TextFileChangeType.DELETED


def test_selected_target_change_type_accepts_empty_buffer():
    """Selected apply lifecycle checks can read buffers."""
    with LineBuffer.from_bytes(b"") as buffer:
        change_type = selected_text_target_change_type(
            TextFileChangeType.DELETED,
            {1},
            buffer,
        )

    assert change_type == TextFileChangeType.DELETED


def test_selected_discard_change_type_accepts_empty_buffer():
    """Selected discard lifecycle checks can read buffers."""
    with LineBuffer.from_bytes(b"") as buffer:
        change_type = selected_text_discard_change_type(
            TextFileChangeType.ADDED,
            {1},
            buffer,
            baseline_exists=False,
        )

    assert change_type == TextFileChangeType.DELETED


def test_sifted_text_path_change_type_uses_current_and_target_existence():
    """A sifted batch should describe the transition from its new predecessor."""
    assert sifted_text_path_change_type(
        target_exists=True,
        working_exists=False,
    ) == TextFileChangeType.ADDED
    assert sifted_text_path_change_type(
        target_exists=False,
        working_exists=True,
    ) == TextFileChangeType.DELETED
    assert sifted_text_path_change_type(
        target_exists=True,
        working_exists=True,
    ) == TextFileChangeType.MODIFIED
