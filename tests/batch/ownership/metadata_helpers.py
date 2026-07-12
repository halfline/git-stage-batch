"""Test helpers for loading batch ownership metadata."""

from __future__ import annotations

from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.metadata_loading import (
    acquire_ownership_for_metadata_dict as acquire_ownership_for_metadata,
)


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
