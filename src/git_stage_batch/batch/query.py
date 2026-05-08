"""Batch query operations: list, read metadata, get refs."""

from __future__ import annotations

import json
from typing import Optional

from .ref_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
from .state_refs import (
    get_authoritative_batch_commit_sha,
    get_legacy_batch_ref_name,
    read_batch_state_metadata,
)
from .validation import validate_batch_name
from ..utils.file_io import read_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import get_batch_metadata_file_path


def read_batch_metadata(name: str) -> dict:
    """Read metadata for a batch.

    Returns metadata with structure:
    {
        "note": str,
        "created_at": str,
        "baseline": str | None,
        "files": {
            "path": {
                "batch_source_commit": str,  # Batch source SHA
                "presence_claims": list[dict],  # [{"source_lines": ["1-5"]}]
                "deletions": list[dict],  # [{"after_source_line": int|None, "blob": str}]
                "replacement_units": list[dict],  # optional presence/deletion coupling
                "mode": str,  # File mode (e.g. "100644")
                "change_type": str,  # optional text lifecycle: "added" or "deleted"
            }
        }
    }
    """
    validate_batch_name(name)

    state_metadata = read_batch_state_metadata(name)
    if state_metadata is not None:
        return state_metadata

    metadata_path = get_batch_metadata_file_path(name)
    if not metadata_path.exists():
        return {
            "note": "",
            "created_at": "",
            "baseline": None,
            "files": {}
        }

    try:
        metadata = json.loads(read_text_file_contents(metadata_path))
        return {
            "note": metadata.get("note", ""),
            "created_at": metadata.get("created_at", ""),
            "baseline": metadata.get("baseline", None),
            "files": metadata.get("files", {})
        }
    except (json.JSONDecodeError, KeyError):
        return {
            "note": "",
            "created_at": "",
            "baseline": None,
            "files": {}
        }


def get_batch_commit_sha(name: str) -> Optional[str]:
    """Get the commit SHA for a batch from its authoritative git ref."""
    validate_batch_name(name)

    commit_sha = get_authoritative_batch_commit_sha(name)
    if commit_sha:
        return commit_sha

    result = run_git_command(
        ["rev-parse", "--verify", get_legacy_batch_ref_name(name)],
        check=False
    )
    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_batch_names() -> list[str]:
    """List all batch names by querying authoritative refs and legacy imports."""
    batch_names: set[str] = set()
    for prefix in (BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX):
        result = run_git_command(["for-each-ref", "--format=%(refname)", prefix], check=False)
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            if line.startswith(prefix):
                batch_names.add(line[len(prefix):])

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

    Returns the HEAD commit that was selected when the batch was created.
    This is stored in the batch metadata.
    """
    validate_batch_name(name)

    metadata = read_batch_metadata(name)
    return metadata.get("baseline", None)
