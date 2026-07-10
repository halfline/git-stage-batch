"""Tests for selected atomic batch change helpers."""

from __future__ import annotations

from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.data import batch_selected_changes


def test_load_current_selected_batch_binary_file_returns_current_selection(
    monkeypatch,
):
    """Current batch-binary selection should be returned unchanged."""
    binary_change = BinaryFileChange(
        old_path="/dev/null",
        new_path="image.png",
        change_type="added",
    )
    checked_files = []

    monkeypatch.setattr(
        batch_selected_changes,
        "load_selected_binary_file",
        lambda: binary_change,
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "selected_batch_binary_batch_name",
        lambda: "batch",
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "read_batch_metadata",
        lambda batch_name: {"files": {"image.png": {"file_type": "binary"}}},
    )

    def selected_batch_binary_file_for_batch(batch_name, all_files):
        checked_files.append((batch_name, all_files))
        return "image.png"

    monkeypatch.setattr(
        batch_selected_changes,
        "selected_batch_binary_file_for_batch",
        selected_batch_binary_file_for_batch,
    )

    assert (
        batch_selected_changes.load_current_selected_batch_binary_file()
        is binary_change
    )
    assert checked_files == [("batch", {"image.png": {"file_type": "binary"}})]


def test_load_current_selected_batch_binary_file_clears_stale_selection(
    monkeypatch,
):
    """Stale batch-binary selection should be cleared and marked."""
    binary_change = BinaryFileChange(
        old_path="/dev/null",
        new_path="image.png",
        change_type="added",
    )
    cleared = []
    marked = []

    monkeypatch.setattr(
        batch_selected_changes,
        "load_selected_binary_file",
        lambda: binary_change,
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "selected_batch_binary_batch_name",
        lambda: "batch",
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "read_batch_metadata",
        lambda batch_name: {"files": {}},
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "selected_batch_binary_file_for_batch",
        lambda batch_name, all_files: None,
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "clear_selected_change_state_files",
        lambda: cleared.append(True),
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "mark_selected_change_cleared_by_stale_batch_selection",
        lambda **kwargs: marked.append(kwargs),
    )

    assert batch_selected_changes.load_current_selected_batch_binary_file() is None
    assert cleared == [True]
    assert marked == [{"batch_name": "batch", "file_path": "image.png"}]


def test_load_current_selected_batch_binary_file_clears_missing_batch_name(
    monkeypatch,
):
    """Missing selected batch name should clear the selected change."""
    binary_change = BinaryFileChange(
        old_path="image.png",
        new_path="/dev/null",
        change_type="deleted",
    )
    cleared = []

    monkeypatch.setattr(
        batch_selected_changes,
        "load_selected_binary_file",
        lambda: binary_change,
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "selected_batch_binary_batch_name",
        lambda: None,
    )
    monkeypatch.setattr(
        batch_selected_changes,
        "clear_selected_change_state_files",
        lambda: cleared.append(True),
    )

    assert batch_selected_changes.load_current_selected_batch_binary_file() is None
    assert cleared == [True]
