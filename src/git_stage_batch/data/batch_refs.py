"""Batch reference snapshot and restore for abort support."""

from __future__ import annotations

import json
import shutil
from typing import Any

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import (
    get_batch_directory_path,
    get_batch_metadata_file_path,
    get_batch_refs_snapshot_file_path,
)


def snapshot_batch_refs() -> None:
    """Save selected state of all batch refs to snapshot file for abort support.

    Stores a single JSON object mapping batch names to their state:
    {"batch-name": {"commit_sha": "...", "note": "...", "created_at": "..."}}

    This includes metadata so dropped batches can be fully restored.
    """
    # Get all batch refs
    result = run_git_command(["for-each-ref", "--format=%(objectname) %(refname)", "refs/batches/"], check=False)
    if result.returncode != 0:
        # No batches exist, save empty snapshot
        snapshot_data: dict[str, Any] = {}
        write_text_file_contents(get_batch_refs_snapshot_file_path(), json.dumps(snapshot_data))
        return

    snapshot_data = {}
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        commit_sha, ref = line.split(None, 1)
        if not ref.startswith("refs/batches/"):
            continue

        batch_name = ref[len("refs/batches/"):]

        # Read metadata if it exists
        metadata_path = get_batch_metadata_file_path(batch_name)
        note = ""
        created_at = ""
        if metadata_path.exists():
            try:
                metadata = json.loads(read_text_file_contents(metadata_path))
                note = metadata.get("note", "")
                created_at = metadata.get("created_at", "")
            except (json.JSONDecodeError, KeyError):
                pass

        snapshot_data[batch_name] = {
            "commit_sha": commit_sha,
            "note": note,
            "created_at": created_at
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
    selected_batches: dict[str, str] = {}
    result = run_git_command(["for-each-ref", "--format=%(objectname) %(refname)", "refs/batches/"], check=False)
    if result.returncode == 0:
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            commit_sha, ref = line.split(None, 1)
            if ref.startswith("refs/batches/"):
                batch_name = ref[len("refs/batches/"):]
                selected_batches[batch_name] = commit_sha

    # Drop batches created during session (in selected but not in snapshot)
    for batch_name in selected_batches:
        if batch_name not in snapshot_data:
            # Delete ref
            run_git_command(["update-ref", "-d", f"refs/batches/{batch_name}"], check=False)
            # Delete metadata directory
            metadata_dir = get_batch_directory_path(batch_name)
            if metadata_dir.exists():
                shutil.rmtree(metadata_dir, ignore_errors=True)

    # Restore/revert batches from snapshot
    for batch_name, batch_state in snapshot_data.items():
        commit_sha = batch_state["commit_sha"]
        note = batch_state.get("note", "")
        created_at = batch_state.get("created_at", "")

        # Restore or revert ref
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

        # Restore metadata
        metadata_path = get_batch_metadata_file_path(batch_name)
        metadata = {
            "note": note,
            "created_at": created_at
        }
        write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
