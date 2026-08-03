"""Tests for cached change freshness helpers."""

import git_stage_batch.data.change_freshness as change_freshness
from git_stage_batch.core.models import FileModeChange


def test_mode_freshness_detects_changed_rename_pairing(monkeypatch):
    """Equal mode bits do not make stale source-index topology current."""
    cached = FileModeChange(
        "new.sh",
        "100644",
        "100755",
        index_path="old.sh",
    )
    monkeypatch.setattr(
        change_freshness,
        "render_mode_change",
        lambda *_args, **_kwargs: FileModeChange(
            "new.sh",
            "100644",
            "100755",
        ),
    )

    assert change_freshness.file_mode_change_is_stale(cached)


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
