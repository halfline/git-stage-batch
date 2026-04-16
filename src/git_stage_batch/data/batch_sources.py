"""Session batch source management for batch operations."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import create_git_blob, get_git_repository_root_path, run_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_session_batch_sources_file_path,
)


def get_saved_session_file_content(file_path: str) -> bytes:
    """Get file content as it was at session start.

    For tracked files, extracts from the git stash created by
    initialize_abort_state(). For untracked files, reads from the lazy
    snapshot taken before first modification.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        File content as bytes, preserving exact encoding and line endings

    Raises:
        CommandError: If file content cannot be retrieved
    """
    # Check if file was untracked and snapshotted
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        if file_path in snapshotted_files:
            # Read from snapshot directory
            snapshot_path = get_abort_snapshots_directory_path() / file_path
            if snapshot_path.exists():
                return snapshot_path.read_bytes()
            else:
                raise CommandError(
                    _("Snapshot for untracked file not found: {file}").format(file=file_path)
                )

    # File was tracked - extract from stash if it exists, otherwise from baseline
    stash_file_path = get_abort_stash_file_path()
    if stash_file_path.exists():
        stash_commit = read_text_file_contents(stash_file_path).strip()
        if stash_commit:
            # Extract file from stash commit
            # The stash commit contains the working tree state
            result = run_git_command(["show", f"{stash_commit}:{file_path}"], check=False, text_output=False)
            if result.returncode == 0:
                return result.stdout

    # No stash or file not in stash - file was unchanged at session start
    # Read from baseline (abort HEAD)
    abort_head_path = get_abort_head_file_path()
    if not abort_head_path.exists():
        raise CommandError(_("No session found"))

    baseline_commit = read_text_file_contents(abort_head_path).strip()
    result = run_git_command(["show", f"{baseline_commit}:{file_path}"], check=False, text_output=False)
    if result.returncode != 0:
        # File might not exist in baseline (new file)
        return b""

    return result.stdout


def create_batch_source_commit(
    file_path: str,
    *,
    file_content_override: bytes | None = None
) -> str:
    """Create batch source commit for a file.

    The batch source commit captures the working tree state of the file at session
    start. It serves as a stable reference point for batch operations.

    Args:
        file_path: Repository-relative path to the file
        file_content_override: Optional exact content to store. Used by stale-source
            advancement, where the new source may be synthesized from current
            working tree content plus already-owned lines rather than the
            original session-start snapshot.

    Returns:
        Batch source commit SHA

    Raises:
        CommandError: If batch source commit cannot be created
    """
    # Get baseline commit (HEAD at session start)
    baseline_commit = read_text_file_contents(get_abort_head_file_path()).strip()

    # Check if file existed at session start
    baseline_result = run_git_command(["cat-file", "-e", f"{baseline_commit}:{file_path}"], check=False)
    file_existed_at_session_start = baseline_result.returncode == 0

    if file_content_override is not None:
        file_content = file_content_override
    else:
        # Get file content at session start (as bytes)
        file_content = get_saved_session_file_content(file_path)

    # For new files (didn't exist at session start), use selected working tree content
    # This ensures the batch source has the lines we're actually claiming
    if file_content_override is None and not file_existed_at_session_start:
        repo_root = get_git_repository_root_path()
        file_full_path = repo_root / file_path
        if file_full_path.exists():
            file_content = file_full_path.read_bytes()

    log_journal(
        "batch_source_creating",
        file_path=file_path,
        baseline_commit=baseline_commit,
        file_existed_at_session_start=file_existed_at_session_start,
        content_len=len(file_content),
        content_lines=len(file_content.splitlines()) if file_content else 0,
        content_preview=file_content[:200] if file_content else b"(empty)"
    )

    # Create a blob for the file content (already bytes)
    blob_sha = create_git_blob([file_content])

    # Detect file mode
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if full_path.exists():
        st = full_path.stat()
        if st.st_mode & stat.S_IXUSR:
            mode = "100755"
        else:
            mode = "100644"
    else:
        mode = "100644"

    # Create new tree by modifying baseline tree
    # Use a temporary index to build the tree
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.index') as tmp:
        temp_index = tmp.name

    try:
        # Set up environment to use temp index
        env = os.environ.copy()
        env['GIT_INDEX_FILE'] = temp_index

        # Read baseline tree into temp index
        subprocess.run(
            ["git", "read-tree", baseline_commit],
            env=env,
            capture_output=True,
            check=True
        )

        # Update the file in the index
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"{mode},{blob_sha},{file_path}"],
            env=env,
            capture_output=True,
            check=True
        )

        # Write tree from the index
        tree_result = subprocess.run(
            ["git", "write-tree"],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        new_tree = tree_result.stdout.strip()
    finally:
        # Clean up temp index
        if os.path.exists(temp_index):
            os.unlink(temp_index)

    # Create commit with baseline as parent
    commit_message = f"Batch source for {file_path}"
    commit_result = subprocess.run(
        ["git", "commit-tree", new_tree, "-p", baseline_commit, "-m", commit_message],
        capture_output=True,
        text=True,
        check=True
    )
    batch_source_commit = commit_result.stdout.strip()

    # Verify the content in the batch source commit
    verify_result = run_git_command(["show", f"{batch_source_commit}:{file_path}"], check=False, text_output=False)
    log_journal(
        "batch_source_created",
        file_path=file_path,
        batch_source_commit=batch_source_commit,
        blob_sha=blob_sha,
        mode=mode,
        tree=new_tree,
        verified_content_len=len(verify_result.stdout) if verify_result.returncode == 0 else None,
        verified_lines=len(verify_result.stdout.splitlines()) if verify_result.returncode == 0 else None
    )

    return batch_source_commit


def get_batch_source_for_file(file_path: str) -> str | None:
    """Retrieve existing batch source commit for a file from session cache.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        Batch source commit SHA if found, None otherwise
    """
    batch_sources = load_session_batch_sources()
    return batch_sources.get(file_path)


def load_session_batch_sources() -> dict[str, str]:
    """Load session batch sources from session-batch-sources.json.

    Returns:
        Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    if not batch_sources_path.exists():
        return {}

    try:
        content = read_text_file_contents(batch_sources_path)
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_session_batch_sources(batch_sources: dict[str, str]) -> None:
    """Save session batch sources to session-batch-sources.json.

    Args:
        batch_sources: Dictionary mapping file paths to batch source commit SHAs
    """
    batch_sources_path = get_session_batch_sources_file_path()
    content = json.dumps(batch_sources, indent=2)
    write_text_file_contents(batch_sources_path, content)
