"""Batch reference snapshot and restore for abort support."""

from __future__ import annotations

import json
import shutil
from typing import TypeAlias, TypedDict, cast

from ..batch.state.metadata_types import BatchMetadataDict
from ..batch.state.reference_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from ..batch.state.query import read_batch_metadata
from ..batch.state.compatibility_metadata import write_file_backed_batch_metadata
from ..batch.state.references import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    get_batch_state_ref_name,
    remove_file_backed_batch_metadata,
    sync_batch_state_refs,
)
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_refs import update_git_refs
from ..utils.paths import get_batch_directory_path, get_batch_refs_snapshot_file_path


class BatchRefSnapshotEntry(TypedDict):
    """One batch's refs plus validated application metadata."""

    commit_sha: str
    state_commit_sha: str | None
    metadata: BatchMetadataDict


BatchRefSnapshot: TypeAlias = dict[str, BatchRefSnapshotEntry]


def _list_batch_content_refs() -> dict[str, str]:
    refs: dict[str, str] = {}
    prefixes = (
        (BATCH_CONTENT_REF_PREFIX, len(BATCH_CONTENT_REF_PREFIX)),
        (LEGACY_BATCH_REF_PREFIX, len(LEGACY_BATCH_REF_PREFIX)),
    )
    for prefix, prefix_len in prefixes:
        result = run_git_command(
            ["for-each-ref", "--format=%(objectname) %(refname)", prefix],
            check=False,
            requires_index_lock=False,
        )
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
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def snapshot_batch_refs() -> BatchRefSnapshot:
    """Save selected state of all batch refs to snapshot file for abort support.

    Stores a single JSON object mapping batch names to their state:
    {"batch-name": {"commit_sha": "...", "metadata": {...}}}

    This includes complete metadata so dropped batches can be fully restored.
    """
    snapshot_data: BatchRefSnapshot = {}
    for batch_name, commit_sha in _list_batch_content_refs().items():
        full_metadata = read_batch_metadata(batch_name)

        snapshot_data[batch_name] = {
            "commit_sha": commit_sha,
            "state_commit_sha": _get_batch_state_ref_commit(batch_name),
            "metadata": full_metadata,
        }

    write_text_file_contents(get_batch_refs_snapshot_file_path(), json.dumps(snapshot_data, indent=2))
    return snapshot_data


def _decode_batch_ref_snapshot(payload: str) -> BatchRefSnapshot | None:
    """Return a structurally valid snapshot mapping, or ignore stale garbage."""
    value: object = json.loads(payload)
    if not isinstance(value, dict):
        return None
    for batch_name, entry in value.items():
        if not isinstance(batch_name, str) or not isinstance(entry, dict):
            return None
        commit_sha = entry.get("commit_sha")
        state_commit_sha = entry.get("state_commit_sha")
        metadata = entry.get("metadata")
        if not isinstance(commit_sha, str):
            return None
        if state_commit_sha is not None and not isinstance(state_commit_sha, str):
            return None
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) for key in metadata
        ):
            return None
    return cast(BatchRefSnapshot, value)


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
        snapshot_data = _decode_batch_ref_snapshot(
            read_text_file_contents(snapshot_path)
        )
    except json.JSONDecodeError:
        return
    if snapshot_data is None:
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
        full_metadata = batch_state["metadata"]

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
            metadata_model = write_file_backed_batch_metadata(
                batch_name,
                full_metadata,
            )
            sync_batch_state_refs(
                batch_name,
                metadata_model,
                content_commit=commit_sha,
            )
