"""Tests for selected-change path helpers."""

from git_stage_batch.core.models import (
    BinaryFileChange,
    HunkHeader,
    LineLevelChange,
    RenameChange,
)
from git_stage_batch.data.selected_change.paths import worktree_paths_for_selected_change


def test_worktree_paths_for_line_level_change_uses_path_attribute():
    """Text hunks carry their repository path as an attribute."""
    change = LineLevelChange(
        path="notes.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[],
    )

    assert worktree_paths_for_selected_change(change) == ["notes.txt"]


def test_worktree_paths_for_rename_include_source_and_destination():
    """Rename actions can affect both sides of the rename."""
    change = RenameChange(old_path="old.txt", new_path="new.txt")

    assert worktree_paths_for_selected_change(change) == ["old.txt", "new.txt"]


def test_worktree_paths_for_wrapped_change_uses_path_method():
    """Binary/gitlink/deletion wrappers expose their path through path()."""
    change = BinaryFileChange(
        old_path="image.png",
        new_path="image.png",
        change_type="modified",
    )

    assert worktree_paths_for_selected_change(change) == ["image.png"]
