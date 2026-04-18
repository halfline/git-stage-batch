"""Batch reference snapshot and restore for abort support."""

from __future__ import annotations

import json
import shutil
from typing import Any

from ..batch.ref_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from ..batch.query import read_batch_metadata
from ..batch.state_refs import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    get_batch_state_ref_name,
    remove_file_backed_batch_metadata,
    sync_batch_state_refs,
)
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import run_git_command, update_git_refs
from ..utils.paths import (
    get_batch_directory_path,
    get_batch_metadata_file_path,
    get_batch_refs_snapshot_file_path,
)


def _list_batch_content_refs() -> dict[str, str]:
    refs: dict[str, str] = {}
    prefixes = (
        (BATCH_CONTENT_REF_PREFIX, len(BATCH_CONTENT_REF_PREFIX)),
        (LEGACY_BATCH_REF_PREFIX, len(LEGACY_BATCH_REF_PREFIX)),
    )
    for prefix, prefix_len in prefixes:
        result = run_git_command(["for-each-ref", "--format=%(objectname) %(refname)", prefix], check=False)
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            commit_sha, ref = line.split(None, 1)
            if ref.startswith(prefix):
                refs.setdefault(ref[prefix_len:], commit_sha)
    return refs


def _get_batch_state_ref_commit(batch_name: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", get_batch_state_ref_name(batch_name)],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def snapshot_batch_refs() -> None:
    """Save selected state of all batch refs to snapshot file for abort support.

    Stores a single JSON object mapping batch names to their state:
    {"batch-name": {"commit_sha": "...", "metadata": {...}}}

    This includes complete metadata so dropped batches can be fully restored.
    """
    snapshot_data = {}
    for batch_name, commit_sha in _list_batch_content_refs().items():
        full_metadata = read_batch_metadata(batch_name)

        snapshot_data[batch_name] = {
            "commit_sha": commit_sha,
            "state_commit_sha": _get_batch_state_ref_commit(batch_name),
            "metadata": full_metadata
        }

    write_text_file_contents(get_batch_refs_snapshot_file_path(), json.dumps(snapshot_data, indent=2))


def restore_batch_refs() -> None:
    """Restore batch refs from snapshot, reverting all batch changes made during session.

    Compares snapshot with selected refs:
    - Batches in selected but not snapshot: drop (delete ref + metadata)
    - Batches in snapshot but not selected: restore (recreate ref + metadata)
    - Batches in both with different SHAs: revert (update ref to snapshot SHA)
    """
    snapshot_path = get_batch_refs_snapshot_file_path()
    if not snapshot_path.exists():
        return

    # Load snapshot
    try:
        snapshot_data: dict[str, Any] = json.loads(read_text_file_contents(snapshot_path))
    except (json.JSONDecodeError, KeyError):
        return

    # Get selected batch refs
    selected_batches = _list_batch_content_refs()

    # Drop batches created during session (in selected but not in snapshot)
    for batch_name in selected_batches:
        if batch_name not in snapshot_data:
            delete_batch_state_refs(batch_name)
            # Delete metadata directory
            metadata_dir = get_batch_directory_path(batch_name)
            if metadata_dir.exists():
                shutil.rmtree(metadata_dir, ignore_errors=True)

    # Restore/revert batches from snapshot
    for batch_name, batch_state in snapshot_data.items():
        commit_sha = batch_state["commit_sha"]
        state_commit_sha = batch_state.get("state_commit_sha")
        full_metadata = batch_state.get("metadata", {})

        # Restore complete metadata
        metadata_path = get_batch_metadata_file_path(batch_name)
        write_text_file_contents(metadata_path, json.dumps(full_metadata, indent=2))

        if state_commit_sha:
            update_git_refs(
                updates=[
                    (get_batch_content_ref_name(batch_name), commit_sha),
                    (get_batch_state_ref_name(batch_name), state_commit_sha),
                ],
                deletes=[f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"],
            )
            remove_file_backed_batch_metadata(batch_name)
        else:
            sync_batch_state_refs(batch_name, content_commit=commit_sha)
