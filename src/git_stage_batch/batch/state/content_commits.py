"""Batch content commit tree publication helpers."""

from __future__ import annotations

from ...core.buffer import LineBuffer
from ...utils.git_index import (
    git_commit_tree,
    git_read_tree,
    git_update_gitlink,
    git_update_index,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_object_io import get_git_object_type
from .query import get_batch_baseline_commit, get_batch_commit_sha
from .references import read_file_backed_batch_metadata, sync_batch_state_refs


def batch_content_commit_parents(batch_name: str) -> list[str]:
    """Return parent commits for a batch content commit."""
    parents = []
    baseline = get_batch_baseline_commit(batch_name)
    if baseline and get_git_object_type(baseline) == "commit":
        parents.append(baseline)

    metadata = read_file_backed_batch_metadata(batch_name)
    batch_source_commits = {
        file_meta["batch_source_commit"]
        for file_meta in metadata.get("files", {}).values()
        if "batch_source_commit" in file_meta
    }
    parents.extend(sorted(batch_source_commits))
    return parents


def remove_file_from_batch_commit(
    batch_name: str,
    file_path: str,
    *,
    source_buffers: dict[str, LineBuffer] | None = None,
) -> None:
    """Remove a file from a batch content commit tree."""
    with temp_git_index() as env:
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            git_read_tree(existing_commit, env=env)

        # Remove the path regardless of working-tree state. `--remove` can
        # retain a baseline blob when the file exists locally.
        git_update_index(file_path=file_path, force_remove=True, check=False, env=env)
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=batch_content_commit_parents(batch_name),
        message=f"Batch: {batch_name}",
    )

    sync_batch_state_refs(
        batch_name,
        content_commit=commit_sha,
        source_buffers=source_buffers,
    )


def update_batch_commit(
    batch_name: str,
    file_path: str,
    blob_sha: str,
    file_mode: str,
    *,
    source_buffers: dict[str, LineBuffer] | None = None,
) -> None:
    """Update a file entry in a batch content commit tree."""
    with temp_git_index() as env:
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            git_read_tree(existing_commit, env=env)

        git_update_index(
            mode=file_mode,
            blob_sha=blob_sha,
            file_path=file_path,
            env=env,
        )
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=batch_content_commit_parents(batch_name),
        message=f"Batch: {batch_name}",
    )

    sync_batch_state_refs(
        batch_name,
        content_commit=commit_sha,
        source_buffers=source_buffers,
    )


def update_batch_gitlink_commit(
    batch_name: str,
    file_path: str,
    oid: str,
) -> None:
    """Update a submodule pointer entry in a batch content commit tree."""
    with temp_git_index() as env:
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            git_read_tree(existing_commit, env=env)

        git_update_gitlink(file_path=file_path, oid=oid, env=env)
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=batch_content_commit_parents(batch_name),
        message=f"Batch: {batch_name}",
    )

    sync_batch_state_refs(batch_name, content_commit=commit_sha)
