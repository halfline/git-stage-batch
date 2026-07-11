"""Gitlink batch persistence."""

from __future__ import annotations

from ..core.models import GitlinkChange
from . import content_commits as _content_commits
from .lifecycle import create_batch
from .metadata_io import write_file_backed_batch_metadata
from .query import read_batch_metadata
from .validation import batch_exists, validate_batch_name


def add_gitlink_to_batch(
    batch_name: str,
    gitlink_change: GitlinkChange,
) -> None:
    """Add a submodule pointer change to a batch as an atomic unit."""
    validate_batch_name(batch_name)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_path = gitlink_change.path()
    metadata = read_batch_metadata(batch_name)
    if "files" not in metadata:
        metadata["files"] = {}

    metadata["files"][file_path] = {
        "file_type": "gitlink",
        "change_type": gitlink_change.change_type,
        "mode": "160000",
        "old_oid": gitlink_change.old_oid,
        "new_oid": gitlink_change.new_oid,
    }

    write_file_backed_batch_metadata(batch_name, metadata)

    if gitlink_change.is_deleted_file():
        _content_commits.remove_file_from_batch_commit(batch_name, file_path)
        return

    if gitlink_change.new_oid is None:
        raise ValueError(
            "new_oid is required for added or modified submodule pointers"
        )

    _content_commits.update_batch_gitlink_commit(
        batch_name,
        file_path,
        gitlink_change.new_oid,
    )
