"""Batch storage operations: file management and diff generation."""

from __future__ import annotations

import os
from typing import Optional

from .operations import create_batch
from .query import get_batch_baseline_commit, get_batch_commit_sha
from .validation import batch_exists, validate_batch_name
from ..utils.file_io import write_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import get_state_directory_path


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
