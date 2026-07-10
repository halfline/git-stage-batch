"""Batch operations: create, delete, and update."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from .query import read_batch_metadata
from .validation import batch_exists, validate_batch_name
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_index import git_commit_tree
from ..utils.git_object_io import get_empty_git_tree_object_id, get_git_object_type
from ..utils.paths import get_batch_directory_path, get_batch_metadata_file_path


def create_batch(name: str, note: str = "", baseline_commit: str | None = None) -> None:
    """
    Create a new batch with metadata and initial git ref.

    Creates a commit using HEAD's tree as the starting point, establishing
    both the batch ref and its baseline for computing diffs.
    """
    validate_batch_name(name)

    if batch_exists(name):
        raise CommandError(_("Batch '{name}' already exists").format(name=name))

    # Determine baseline (selected HEAD or caller-provided baseline)
    if baseline_commit is None:
        head_result = run_git_command(["rev-parse", "--verify", "HEAD"], check=False, requires_index_lock=False)
        baseline_commit = (
            head_result.stdout.strip()
            if head_result.returncode == 0
            else get_empty_git_tree_object_id()
        )

    metadata = {
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline_commit,
        "files": {}
    }
    metadata_path = get_batch_metadata_file_path(name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    tree_result = run_git_command(["rev-parse", f"{baseline_commit}^{{tree}}"], requires_index_lock=False)
    tree_sha = tree_result.stdout.strip()
    parents = [baseline_commit] if get_git_object_type(baseline_commit) == "commit" else []
    commit_sha = git_commit_tree(tree_sha, parents=parents, message=f"Batch: {name}")

    from .state_refs import sync_batch_state_refs
    sync_batch_state_refs(name, content_commit=commit_sha)


def delete_batch(name: str) -> None:
    """Delete a batch, removing both git ref and metadata."""
    validate_batch_name(name)

    if not batch_exists(name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=name))

    from .state_refs import delete_batch_state_refs
    delete_batch_state_refs(name)

    # Delete metadata directory
    metadata_dir = get_batch_directory_path(name)
    if metadata_dir.exists():
        shutil.rmtree(metadata_dir, ignore_errors=True)


def update_batch_note(name: str, note: str) -> None:
    """Update the note/description for a batch."""
    validate_batch_name(name)

    if not batch_exists(name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=name))

    metadata_path = get_batch_metadata_file_path(name)
    metadata = read_batch_metadata(name)

    # Update note
    metadata["note"] = note
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    from .state_refs import sync_batch_state_refs
    sync_batch_state_refs(name)
