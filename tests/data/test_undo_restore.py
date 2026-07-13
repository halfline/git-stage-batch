"""Tests for undo checkpoint restoration helpers."""

from __future__ import annotations

import os
import pytest
import subprocess

from git_stage_batch.data import undo_restore
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git_object_io import create_git_blob


def test_restore_worktree_rejects_missing_saved_blob(tmp_path, monkeypatch):
    """An incomplete checkpoint must not silently preserve post-operation bytes."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    with pytest.raises(CommandError, match="missing worktree content for file.txt"):
        undo_restore.restore_worktree(
            "checkpoint",
            {
                "worktree_paths": [
                    {
                        "path": "file.txt",
                        "exists": True,
                        "mode": "100644",
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("saved_mode", "saved_content", "current_kind"),
    [
        ("100644", b"regular bytes\n", "symlink"),
        ("120000", b"saved-target", "regular"),
    ],
)
def test_restore_tree_paths_uses_saved_git_mode(
    tmp_path,
    monkeypatch,
    saved_mode,
    saved_content,
    current_kind,
):
    """Restore replaces the current path type instead of following it."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    blob_sha = create_git_blob([saved_content])
    monkeypatch.setattr(
        undo_restore,
        "_tree_entries",
        lambda *_args: [(saved_mode, blob_sha, "state/entry")],
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "entry"
    referent = tmp_path / "referent"
    referent.write_text("untouched\n")
    if current_kind == "symlink":
        target.symlink_to(referent)
    else:
        target.write_text("current\n")

    undo_restore.restore_tree_paths(
        "checkpoint",
        prefix="state",
        target_dir=target_dir,
        tracked_paths=["entry"],
    )

    if saved_mode == "120000":
        assert target.is_symlink()
        assert os.readlink(target) == "saved-target"
    else:
        assert not target.is_symlink()
        assert target.read_bytes() == saved_content
        assert referent.read_text() == "untouched\n"
def test_restore_intent_to_add_entries_checks_git_failures(tmp_path, monkeypatch):
    """intent-to-add restoration does not silently accept failed index commands."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    (tmp_path / "new.txt").write_text("content\n")
    update_calls = []
    add_calls = []
    monkeypatch.setattr(
        undo_restore,
        "git_update_index",
        lambda **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(
        undo_restore,
        "git_add_paths",
        lambda paths, **kwargs: add_calls.append((paths, kwargs)),
    )

    undo_restore.restore_intent_to_add_entries(["new.txt"])

    assert update_calls == [{"file_path": "new.txt", "force_remove": True}]
    assert add_calls == [(["new.txt"], {"intent_to_add": True})]
