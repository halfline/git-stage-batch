"""Core batch operations using git plumbing for storage."""

from __future__ import annotations

import json
import os
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
    get_state_directory_path,
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


def add_file_to_batch(batch_name: str, file_path: str, content: str, file_mode: str = "100644") -> None:
    """
    Add or update a file in a batch using git plumbing.

    This creates a new commit with the file's content, using the existing
    batch commit as parent (or HEAD for new batches). The batch commit
    chain allows us to track history and compute diffs.

    Args:
        batch_name: Name of the batch
        file_path: Repository-relative path to the file
        content: File content to store
        file_mode: Git file mode (default: 100644)
    """
    validate_batch_name(batch_name)

    # Auto-create batch if it doesn't exist (with metadata)
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Create temporary index file
    temp_index_path = get_state_directory_path() / f".batch_index_{batch_name}"

    # Set up environment with temporary index
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index_path)

    try:
        # Load existing tree if batch exists
        existing_commit = get_batch_commit_sha(batch_name)
        if existing_commit:
            # Use subprocess directly to ensure GIT_INDEX_FILE is respected
            import subprocess
            subprocess.run(
                ["git", "read-tree", existing_commit],
                env=env,
                check=True,
                capture_output=True,
                text=True
            )

        # Write file content as blob
        temp_blob_path = get_state_directory_path() / f".batch_blob_{batch_name}"
        write_text_file_contents(temp_blob_path, content)
        blob_result = run_git_command(["hash-object", "-w", str(temp_blob_path)])
        blob_sha = blob_result.stdout.strip()
        temp_blob_path.unlink(missing_ok=True)

        # Detect file mode from existing index if available
        detected_mode = file_mode
        try:
            # Use subprocess directly to read from temporary index
            import subprocess
            ls_result = subprocess.run(
                ["git", "ls-files", "-s", "--", file_path],
                env=env,
                capture_output=True,
                text=True,
                check=False
            )
            if ls_result.returncode == 0 and ls_result.stdout.strip():
                detected_mode = ls_result.stdout.strip().split()[0]
        except Exception:
            pass

        # Update temporary index with blob
        # Use subprocess with env to ensure GIT_INDEX_FILE is used
        import subprocess
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"{detected_mode},{blob_sha},{file_path}"],
            env=env,
            check=True,
            capture_output=True,
            text=True
        )

        # Write tree from temporary index
        tree_result = subprocess.run(
            ["git", "write-tree"],
            env=env,
            check=True,
            capture_output=True,
            text=True
        )
        tree_sha = tree_result.stdout.strip()

        # Determine parent commit
        parent_commit = None
        if existing_commit:
            # Use existing batch commit as parent (preserves history)
            parent_commit = existing_commit
        else:
            # Use HEAD as parent (establishes baseline)
            head_result = run_git_command(["rev-parse", "HEAD"], check=False)
            if head_result.returncode == 0:
                parent_commit = head_result.stdout.strip()

        # Create commit
        if parent_commit:
            commit_result = run_git_command([
                "commit-tree", tree_sha, "-p", parent_commit,
                "-m", f"Batch: {batch_name}"
            ])
        else:
            # No parent (initial commit in empty repo)
            commit_result = run_git_command([
                "commit-tree", tree_sha,
                "-m", f"Batch: {batch_name}"
            ])

        commit_sha = commit_result.stdout.strip()

        # Update batch ref
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

    finally:
        # Clean up temporary files
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


def get_batch_diff(batch_name: str, context_lines: int = 3) -> str:
    """
    Get the unified diff from baseline to batch.

    This shows what changes the batch represents. Returns empty string
    if baseline cannot be determined or batch doesn't exist.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return ""

    baseline = get_batch_baseline_commit(batch_name)
    if not baseline:
        # No baseline, diff against empty tree
        empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        baseline = empty_tree

    # Generate diff
    result = run_git_command(
        ["diff", f"-U{context_lines}", baseline, commit_sha],
        check=False
    )
    if result.returncode != 0:
        return ""

    return result.stdout
