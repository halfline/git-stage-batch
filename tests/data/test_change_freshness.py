"""Tests for cached change freshness helpers."""

import git_stage_batch.data.change_freshness as change_freshness


def test_empty_text_lifecycle_batched_uses_bulk_metadata(monkeypatch):
    """Empty text lifecycle checks should not read each batch individually."""
    calls = []

    monkeypatch.setattr(
        change_freshness,
        "detect_empty_text_lifecycle_change",
        lambda _path: "deleted",
    )
    monkeypatch.setattr(
        change_freshness,
        "list_batch_names",
        lambda: ["batch-a", "batch-b"],
    )

    def fake_read_batch_metadata_for_batches(batch_names):
        calls.append(tuple(batch_names))
        return {
            "batch-a": {"files": {}},
            "batch-b": {
                "files": {
                    "gone.txt": {
                        "change_type": "deleted",
                    },
                },
            },
        }

    monkeypatch.setattr(
        change_freshness,
        "read_batch_metadata_for_batches",
        fake_read_batch_metadata_for_batches,
    )

    assert change_freshness.empty_text_lifecycle_change_is_batched("gone.txt")
    assert calls == [("batch-a", "batch-b")]
