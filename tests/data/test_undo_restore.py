"""Tests for undo checkpoint restoration helpers."""

from __future__ import annotations

import pytest
import subprocess

from git_stage_batch.data import undo_restore
from git_stage_batch.exceptions import CommandError


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
