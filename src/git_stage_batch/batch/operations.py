"""Batch operations: create, delete, and update."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from .validation import batch_exists, validate_batch_name
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import get_batch_directory_path, get_batch_metadata_file_path


def create_batch(name: str, note: str = "") -> None:
    """
    Create a new batch with metadata and initial git ref.

    Creates a commit using HEAD's tree as the starting point, establishing
    both the batch ref and its baseline for computing diffs.
    """
    validate_batch_name(name)

    if batch_exists(name):
        exit_with_error(_("Batch '{name}' already exists").format(name=name))

    # Determine baseline (selected HEAD or None)
    head_result = run_git_command(["rev-parse", "--verify", "HEAD"], check=False)
    baseline_commit = head_result.stdout.strip() if head_result.returncode == 0 else None

    metadata = {
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline_commit,
    }
    metadata_path = get_batch_metadata_file_path(name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    if baseline_commit:
        head_sha = baseline_commit
        tree_result = run_git_command(["rev-parse", "HEAD^{tree}"])
        tree_sha = tree_result.stdout.strip()

        commit_result = run_git_command([
            "commit-tree",
            tree_sha,
            "-p",
            head_sha,
            "-m",
            f"Batch: {name}",
        ])
    else:
        tree_result = run_git_command(["hash-object", "-t", "tree", "/dev/null"])
        tree_sha = tree_result.stdout.strip()

        commit_result = run_git_command([
            "commit-tree",
            tree_sha,
            "-m",
            f"Batch: {name}",
        ])

    commit_sha = commit_result.stdout.strip()

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
