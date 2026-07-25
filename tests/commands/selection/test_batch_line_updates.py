"""Tests for selected-line batch update command boundaries."""

from types import SimpleNamespace

import pytest

import git_stage_batch.commands.selection.batch_line_updates as batch_line_updates
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.exceptions import CommandError


def test_invalid_comparison_snapshot_becomes_command_error(monkeypatch):
    """Malformed selected-file state must not escape the command boundary."""
    reference_buffer = LineBuffer.from_bytes(b"old\n")
    persisted = []

    monkeypatch.setattr(batch_line_updates, "batch_exists", lambda _name: True)
    monkeypatch.setattr(
        batch_line_updates,
        "detect_file_mode",
        lambda _path: "100644",
    )
    monkeypatch.setattr(
        batch_line_updates,
        "read_batch_metadata",
        lambda _name: {"files": {}},
    )
    monkeypatch.setattr(
        batch_line_updates,
        "get_validated_baseline_commit",
        lambda _name: "baseline",
    )
    buffer_reader_name = (
        "read_git_object_buffer_or_empty"
        if hasattr(batch_line_updates, "read_git_object_buffer_or_empty")
        else "read_git_object_buffer_or_none"
    )
    monkeypatch.setattr(
        batch_line_updates,
        buffer_reader_name,
        lambda _refspec: reference_buffer,
    )
    monkeypatch.setattr(
        batch_line_updates,
        "load_selected_file_comparison_base_buffer",
        lambda _path: (_ for _ in ()).throw(
            ValueError("selected-file snapshot path does not match selection")
        ),
    )
    monkeypatch.setattr(
        batch_line_updates,
        "add_file_to_batch",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    with pytest.raises(
        CommandError,
        match="selected-file snapshot path does not match selection",
    ) as error:
        batch_line_updates.add_selected_lines_to_batch(
            batch_name="saved",
            file_path="module.py",
            selected_lines=[],
            stale_source_action="Cannot include lines to batch",
            hunk_lines=[
                SimpleNamespace(kind="-"),
                SimpleNamespace(kind="+"),
            ],
        )

    assert error.value.exit_code == 1
    assert persisted == []
    with pytest.raises(ValueError, match="buffer is closed"):
        reference_buffer.byte_count
