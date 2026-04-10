"""Batch storage operations: file management and diff generation."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from .operations import create_batch
from .query import get_batch_baseline_commit, get_batch_commit_sha, read_batch_metadata
from .validation import batch_exists, validate_batch_name
from ..data.batch_sources import (
    create_batch_source_commit,
    get_batch_source_for_file,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ..utils.file_io import write_text_file_contents
from ..utils.git import create_git_blob, read_git_blob, run_git_command
from ..utils.paths import get_batch_metadata_file_path, get_state_directory_path


def add_file_to_batch(
    batch_name: str,
    file_path: str,
    ownership: 'BatchOwnership',
    file_mode: str = "100644"
) -> None:
    """Add or update a file in a batch using batch source-based storage.

    This stores the file's batch source commit (working tree at session start),
    claimed line ranges, and deletions in the batch metadata. It then builds
    realized content and updates the batch commit tree.

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file
        ownership: BatchOwnership specifying claimed lines and deletions
        file_mode: Git file mode (default: 100644)
    """
    validate_batch_name(batch_name)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Get or create batch source commit for this file
    batch_source_commit = get_batch_source_for_file(file_path)
    if not batch_source_commit:
        # Create batch source commit
        batch_source_commit = create_batch_source_commit(file_path)
        # Save to session cache
        batch_sources = load_session_batch_sources()
        batch_sources[file_path] = batch_source_commit
        save_session_batch_sources(batch_sources)

    # Read baseline and batch source content
    baseline_commit = get_batch_baseline_commit(batch_name)
    if not baseline_commit:
        raise ValueError(f"Batch {batch_name} has no baseline commit")

    # Read base file content as bytes
    base_result = run_git_command(["show", f"{baseline_commit}:{file_path}"], check=False, text_output=False)
    base_content = base_result.stdout if base_result.returncode == 0 else b""

    # Read batch source content as bytes
    batch_source_result = run_git_command(["show", f"{batch_source_commit}:{file_path}"], check=False, text_output=False)
    batch_source_content = batch_source_result.stdout if batch_source_result.returncode == 0 else b""

    # Build realized content: base + claimed changes + deletions
    realized_content_bytes = _build_realized_content(
        base_content,
        batch_source_content,
        ownership
    )

    # Create blob for realized content (already bytes, no encoding needed)
    blob_sha = create_git_blob([realized_content_bytes])

    # Update batch metadata
    metadata = read_batch_metadata(batch_name)
    if "files" not in metadata:
        metadata["files"] = {}

    metadata["files"][file_path] = {
        "batch_source_commit": batch_source_commit,
        **ownership.to_metadata_dict(),
        "mode": file_mode
    }

    # Write updated metadata
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    # Build batch commit tree with realized content
    _update_batch_commit(batch_name, file_path, blob_sha, file_mode)


def _build_realized_content(
    base_content: bytes,
    batch_source_content: bytes,
    ownership: 'BatchOwnership'
) -> bytes:
    """Build realized batch content from base + claimed changes + deletions.

    Materialization rule:
    - Outside changed blocks: preserve base lines UNLESS claimed for deletion
    - Inside changed blocks: emit only claimed batch-source lines
    - Deletion records act as suppression markers, not emitted content

    This answers: what would the file look like if only this batch's
    claimed changes were applied to base?

    Args:
        base_content: Content from baseline commit (bytes)
        batch_source_content: Content from batch source commit (bytes)
        ownership: BatchOwnership specifying claimed lines and deletions

    Returns:
        Realized batch content as bytes (full file for display)
    """
    import difflib

    base_lines = base_content.splitlines(keepends=True) if base_content else []
    source_lines = batch_source_content.splitlines(keepends=True) if batch_source_content else []

    # Resolve ownership into shared representation (use bytes for exact preservation)
    resolved = ownership.resolve(as_bytes=True)
    claimed_line_set = resolved.claimed_line_set
    deletion_claims_by_position = resolved.deletions_by_position

    # Compute structural change: base → batch source
    matcher = difflib.SequenceMatcher(None, base_lines, source_lines)

    result_lines = []

    # Process diff operations with line-level ownership rule
    for tag, base_start, base_end, batch_start, batch_end in matcher.get_opcodes():
        if tag == 'equal':
            # Outside changed blocks: preserve base lines UNLESS claimed for deletion
            for offset in range(batch_end - batch_start):
                source_line_num = batch_start + offset + 1
                base_line = base_lines[base_start + offset]

                # Check if this base line is claimed for deletion
                # Deletions are attached after the preceding source line
                after_line = source_line_num - 1 if source_line_num > 1 else None
                is_claimed_for_deletion = False

                if after_line in deletion_claims_by_position:
                    # Check if this base line matches any deletion claim at this position
                    for claimed_deletion in deletion_claims_by_position[after_line]:
                        if base_line == claimed_deletion:
                            is_claimed_for_deletion = True
                            break

                # Emit base line only if not claimed for deletion
                if not is_claimed_for_deletion:
                    result_lines.append(base_line)

        elif tag == 'replace':
            # Inside changed block: emit only claimed batch-source lines
            for i in range(batch_start, batch_end):
                line_num = i + 1
                if line_num in claimed_line_set:
                    result_lines.append(source_lines[i])

        elif tag == 'delete':
            # Deleted base lines not emitted (already handled by base → source diff)
            pass

        elif tag == 'insert':
            # Inside changed block: emit only claimed batch-source lines
            for i in range(batch_start, batch_end):
                line_num = i + 1
                if line_num in claimed_line_set:
                    result_lines.append(source_lines[i])

    return b"".join(result_lines)


def _update_batch_commit(batch_name: str, file_path: str, blob_sha: str, file_mode: str) -> None:
    """Update batch commit tree with new/updated file.

    Creates a new batch commit with parents=[baseline, ...batch sources].

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file
        blob_sha: Blob SHA for the file content
        file_mode: File mode
    """
    # Create temporary index
    temp_index_path = get_state_directory_path() / f".batch_index_{batch_name}"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index_path)

    try:
        # Load existing batch tree if exists
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            subprocess.run(
                ["git", "read-tree", existing_commit],
                env=env,
                check=True,
                capture_output=True
            )

        # Update index with new blob
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"{file_mode},{blob_sha},{file_path}"],
            env=env,
            check=True,
            capture_output=True
        )

        # Write tree
        tree_result = subprocess.run(
            ["git", "write-tree"],
            env=env,
            check=True,
            capture_output=True,
            text=True
        )
        tree_sha = tree_result.stdout.strip()

        # Collect parent commits: baseline + batch sources
        baseline = get_batch_baseline_commit(batch_name)
        metadata = read_batch_metadata(batch_name)

        parents = []
        if baseline:
            parents.append(baseline)

        # Add batch source commits as parents
        batch_source_commits = set()
        for file_meta in metadata.get("files", {}).values():
            if "batch_source_commit" in file_meta:
                batch_source_commits.add(file_meta["batch_source_commit"])

        parents.extend(sorted(batch_source_commits))

        # Create commit with multi-parent
        parent_args = []
        for parent in parents:
            parent_args.extend(["-p", parent])

        if parent_args:
            commit_result = subprocess.run(
                ["git", "commit-tree", tree_sha] + parent_args + ["-m", f"Batch: {batch_name}"],
                capture_output=True,
                text=True,
                check=True
            )
        else:
            commit_result = subprocess.run(
                ["git", "commit-tree", tree_sha, "-m", f"Batch: {batch_name}"],
                capture_output=True,
                text=True,
                check=True
            )

        commit_sha = commit_result.stdout.strip()

        # Update batch ref
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

    finally:
        if temp_index_path.exists():
            temp_index_path.unlink(missing_ok=True)


def read_file_from_batch(batch_name: str, file_path: str) -> Optional[str]:
    """
    Read a file's content from a batch.

    Returns None if the batch doesn't exist or the file is not in the batch.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return None

    # Use git show to read file from commit
    result = run_git_command(
        ["show", f"{commit_sha}:{file_path}"],
        check=False
    )
    if result.returncode != 0:
        return None

    return result.stdout


def get_batch_diff(batch_name: str, context_lines: int = 3) -> bytes:
    """
    Get the unified diff from baseline to batch.

    This shows what changes the batch represents. Returns empty bytes
    if baseline cannot be determined or batch doesn't exist.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return b""

    baseline = get_batch_baseline_commit(batch_name)
    if not baseline:
        # No baseline, diff against empty tree
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        baseline = empty_tree

    # Generate diff as bytes
    result = run_git_command(
        ["diff", f"-U{context_lines}", baseline, commit_sha],
        check=False,
        text_output=False
    )
    if result.returncode != 0:
        return b""

    return result.stdout
