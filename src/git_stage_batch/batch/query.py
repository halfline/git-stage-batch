"""Batch query operations: list, read metadata, get refs."""

from __future__ import annotations

import json
from typing import Optional

from .validation import validate_batch_name
from ..utils.file_io import read_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import get_batch_metadata_file_path


def read_batch_metadata(name: str) -> dict:
    """Read metadata for a batch (note and created_at only)."""
    validate_batch_name(name)

    metadata_path = get_batch_metadata_file_path(name)
    if not metadata_path.exists():
        return {"note": "", "created_at": ""}

    try:
        metadata = json.loads(read_text_file_contents(metadata_path))
        return {
            "note": metadata.get("note", ""),
            "created_at": metadata.get("created_at", "")
        }
    except (json.JSONDecodeError, KeyError):
        return {"note": "", "created_at": ""}


def get_batch_commit_sha(name: str) -> Optional[str]:
    """Get the commit SHA for a batch from its git ref."""
    validate_batch_name(name)

    result = run_git_command(
        ["rev-parse", "--verify", f"refs/batches/{name}"],
        check=False
    )
    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_batch_names() -> list[str]:
    """List all batch names by querying refs/batches/* refs."""
    result = run_git_command(["for-each-ref", "--format=%(refname)", "refs/batches/"], check=False)
    if result.returncode != 0:
        return []

    batch_names = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        if line.startswith("refs/batches/"):
            batch_name = line[len("refs/batches/"):]
            batch_names.append(batch_name)

    return sorted(batch_names)


def get_batch_tree_sha(name: str) -> Optional[str]:
    """Get the tree SHA from a batch commit."""
    validate_batch_name(name)

    commit_sha = get_batch_commit_sha(name)
    if not commit_sha:
        return None

    # Get tree SHA from commit
    result = run_git_command(
        ["rev-parse", f"{commit_sha}^{{tree}}"],
        check=False
    )
    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_batch_files(name: str) -> list[str]:
    """List all files in a batch by reading its tree."""
    validate_batch_name(name)

    tree_sha = get_batch_tree_sha(name)
    if not tree_sha:
        return []

    # Use git ls-tree to list files recursively
    result = run_git_command(
        ["ls-tree", "-r", "--name-only", tree_sha],
        check=False
    )
    if result.returncode != 0:
        return []

    files = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    return sorted(files)


def get_batch_baseline_commit(name: str) -> Optional[str]:
    """
    Get the baseline commit for a batch.

    Walks the batch commit chain back to the first commit, then returns
    its parent. This is the HEAD that was current when the batch was created.
    """
    validate_batch_name(name)

    commit_sha = get_batch_commit_sha(name)
    if not commit_sha:
        return None

    # Walk back the commit chain until we find a commit whose parent
    # is not a batch commit (i.e., the root of the batch chain)
    current = commit_sha
    while current:
        # Get parent commit
        parent_result = run_git_command(
            ["rev-parse", f"{current}^"],
            check=False
        )
        if parent_result.returncode != 0:
            # No parent (initial commit)
            return None

        parent = parent_result.stdout.strip()

        # Check if parent is a batch commit by seeing if it has the same
        # commit message format. We'll use a simpler heuristic: if the
        # parent commit message starts with "Batch:", keep walking.
        msg_result = run_git_command(
            ["log", "-1", "--format=%s", parent],
            check=False
        )
        if msg_result.returncode == 0 and msg_result.stdout.strip().startswith(f"Batch: {name}"):
            # Parent is also a batch commit, keep walking
            current = parent
        else:
            # Found the baseline (first non-batch parent)
            return parent

    return None
