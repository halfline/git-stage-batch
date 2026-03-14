"""Core batch operations using git plumbing for storage."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .i18n import _
from .state import (
    batch_exists,
    exit_with_error,
    get_batch_directory_path,
    get_batch_metadata_file_path,
    read_text_file_contents,
    run_git_command,
    validate_batch_name,
    write_text_file_contents,
)


def create_batch(name: str, note: str = "") -> None:
    """
    Create a new batch with metadata and initial git ref.

    Creates an empty tree commit to establish the batch ref immediately.
    """
    validate_batch_name(name)

    if batch_exists(name):
        exit_with_error(_("Batch '{name}' already exists").format(name=name))

    # Create metadata
    metadata = {
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    metadata_path = get_batch_metadata_file_path(name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    # Create initial git ref with empty tree
    # Get HEAD as parent (establishes baseline)
    head_result = run_git_command(["rev-parse", "HEAD"], check=False)
    parent_commit = None
    if head_result.returncode == 0:
        parent_commit = head_result.stdout.strip()

    # Create empty tree using git mktree with no input
    import subprocess
    mktree_result = subprocess.run(
        ["git", "mktree"],
        input="",
        check=True,
        capture_output=True,
        text=True
    )
    tree_sha = mktree_result.stdout.strip()

    # Create commit
    if parent_commit:
        commit_result = run_git_command([
            "commit-tree", tree_sha, "-p", parent_commit,
            "-m", f"Batch: {name}"
        ])
    else:
        # No parent (initial commit in empty repo)
        commit_result = run_git_command([
            "commit-tree", tree_sha,
            "-m", f"Batch: {name}"
        ])

    commit_sha = commit_result.stdout.strip()

    # Update batch ref
    run_git_command(["update-ref", f"refs/batches/{name}", commit_sha])


def delete_batch(name: str) -> None:
    """Delete a batch, removing both git ref and metadata."""
    validate_batch_name(name)

    if not batch_exists(name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=name))

    # Delete git ref
    run_git_command(["update-ref", "-d", f"refs/batches/{name}"])

    # Delete metadata directory
    metadata_dir = get_batch_directory_path(name)
    if metadata_dir.exists():
        shutil.rmtree(metadata_dir, ignore_errors=True)


def update_batch_note(name: str, note: str) -> None:
    """Update the note/description for a batch."""
    validate_batch_name(name)

    if not batch_exists(name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=name))

    # Read existing metadata
    metadata_path = get_batch_metadata_file_path(name)
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(read_text_file_contents(metadata_path))
        except (json.JSONDecodeError, KeyError):
            pass

    # Update note
    metadata["note"] = note
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))


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
