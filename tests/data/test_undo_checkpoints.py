"""Focused tests for undo checkpoint compatibility behavior."""

from pathlib import Path

import pytest

from git_stage_batch.data import undo_checkpoints
from git_stage_batch.exceptions import CommandError


def test_legacy_ita_fallback_does_not_guess_from_empty_index_blobs(monkeypatch):
    """An empty blob cannot distinguish intent-to-add from a fully staged empty file."""
    restored = []
    monkeypatch.setattr(
        undo_checkpoints._undo_restore,
        "restore_intent_to_add_entries",
        lambda paths: restored.extend(paths),
    )

    undo_checkpoints._restore_intent_to_add_state(
        {
            "tracked_index_paths": ["staged.txt", "intent.txt"],
            "index_entries": {
                "staged.txt": {"mode": "100644", "object_id": "content-blob"},
                "intent.txt": {"mode": "100644", "object_id": "empty-blob"},
            },
        }
    )

    assert restored == []


def test_legacy_ita_fallback_without_index_identity_fails_closed(monkeypatch):
    """Very old checkpoints do not guess intent-to-add state from append-only history."""
    restored = []
    monkeypatch.setattr(
        undo_checkpoints._undo_restore,
        "restore_intent_to_add_entries",
        lambda paths: restored.extend(paths),
    )

    undo_checkpoints._restore_intent_to_add_state(
        {"tracked_index_paths": ["possibly-staged.txt"]}
    )

    assert restored == []


def test_legacy_gitlink_absence_is_normalized_for_conflict_checks():
    """Old index-based existence matches a currently absent worktree."""
    legacy = {
        "path": "sub",
        "kind": "gitlink",
        "exists": True,
        "worktree_oid": None,
    }
    current = {**legacy, "exists": False}

    assert undo_checkpoints._worktree_state_by_path([legacy]) == (
        undo_checkpoints._worktree_state_by_path([current])
    )


def test_redo_conflicts_fail_closed_without_after_undo_state():
    """A partial redo node must require an explicit force override."""
    assert undo_checkpoints._detect_redo_conflicts({}) == ["incomplete checkpoint"]


def test_nested_checkpoint_fails_when_pending_reference_moved(monkeypatch):
    """A nested operation must not silently discard a mismatched pending node."""
    monkeypatch.setattr(undo_checkpoints, "_PENDING_CHECKPOINT", "pending")
    monkeypatch.setattr(
        undo_checkpoints,
        "_PENDING_CHECKPOINT_REPOSITORY",
        Path("/repo/.git"),
    )
    monkeypatch.setattr(
        undo_checkpoints,
        "get_git_directory_path",
        lambda: Path("/repo/.git"),
    )
    monkeypatch.setattr(undo_checkpoints, "current_undo_commit", lambda: "moved")

    with pytest.raises(CommandError, match="pending checkpoint reference moved"):
        with undo_checkpoints.undo_checkpoint("nested", worktree_paths=[]):
            pass

    assert undo_checkpoints._PENDING_CHECKPOINT is None


def test_pending_checkpoint_from_another_repository_is_cleared(monkeypatch):
    """Process-local checkpoint state must not leak between repositories."""
    monkeypatch.setattr(undo_checkpoints, "_PENDING_CHECKPOINT", "old-pending")
    monkeypatch.setattr(
        undo_checkpoints,
        "_PENDING_CHECKPOINT_REPOSITORY",
        Path("/old/.git"),
    )
    monkeypatch.setattr(
        undo_checkpoints,
        "get_git_directory_path",
        lambda: Path("/new/.git"),
    )
    monkeypatch.setattr(undo_checkpoints, "current_redo_commit", lambda: None)
    monkeypatch.setattr(undo_checkpoints, "_create_undo_checkpoint", lambda *args, **kwargs: None)

    with undo_checkpoints.undo_checkpoint("new operation", worktree_paths=[]):
        pass

    assert undo_checkpoints._PENDING_CHECKPOINT is None
    assert undo_checkpoints._PENDING_CHECKPOINT_REPOSITORY is None


def test_checkpoint_finalization_fails_when_stack_reference_moved(monkeypatch):
    """Finalization must not silently abandon a displaced checkpoint."""
    monkeypatch.setattr(undo_checkpoints, "_PENDING_CHECKPOINT", "pending")
    monkeypatch.setattr(
        undo_checkpoints,
        "_PENDING_CHECKPOINT_REPOSITORY",
        Path("/repo/.git"),
    )
    monkeypatch.setattr(undo_checkpoints, "current_undo_commit", lambda: "moved")

    with pytest.raises(CommandError, match="stack reference moved"):
        undo_checkpoints.finalize_pending_checkpoint()

    assert undo_checkpoints._PENDING_CHECKPOINT is None
    assert undo_checkpoints._PENDING_CHECKPOINT_REPOSITORY is None
