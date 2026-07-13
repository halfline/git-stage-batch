"""Focused tests for undo checkpoint compatibility behavior."""

from git_stage_batch.data import undo_checkpoints


def test_legacy_ita_fallback_does_not_guess_from_empty_index_blobs(monkeypatch):
    """An empty blob cannot distinguish ITA from a fully staged empty file."""
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
    """Very old checkpoints do not guess ITA state from append-only history."""
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
