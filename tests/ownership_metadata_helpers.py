"""Test helpers for loading batch ownership metadata."""

from __future__ import annotations

from importlib.util import find_spec

from git_stage_batch.batch.ownership import BatchOwnership


def acquire_ownership_for_metadata(metadata: dict):
    """Acquire ownership metadata through the available loader API."""
    if find_spec("git_stage_batch.batch.ownership_metadata_loading") is None:
        return BatchOwnership.acquire_for_metadata_dict(metadata)

    from git_stage_batch.batch.ownership_metadata_loading import (
        acquire_ownership_for_metadata_dict,
    )

    return acquire_ownership_for_metadata_dict(metadata)


def reject_materialized_ownership_metadata(
    monkeypatch,
    message: str = "command should use acquired ownership metadata",
) -> None:
    """Reject direct materialized metadata deserialization paths."""

    def fail_from_metadata_dict(cls, data):
        raise AssertionError(message)

    monkeypatch.setattr(
        BatchOwnership,
        "from_metadata_dict",
        classmethod(fail_from_metadata_dict),
        raising=False,
    )
