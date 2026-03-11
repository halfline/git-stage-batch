"""Command implementations for git-stage-batch."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .display import print_colored_patch
from .hashing import compute_stable_hunk_hash
from .i18n import _
from .parser import parse_unified_diff_streaming
from .state import (
    append_file_path_to_file,
    append_lines_to_file,
    ensure_state_directory_exists,
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_block_list_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_git_repository_root_path,
    get_state_directory_path,
    read_file_paths_file,
    read_text_file_contents,
    require_git_repository,
    run_git_command,
    stream_git_command,
    write_text_file_contents,
)


def initialize_abort_state() -> None:
    """Save current HEAD and stash for abort functionality."""
    # Save current HEAD
    head_result = run_git_command(["rev-parse", "HEAD"])
    write_text_file_contents(get_abort_head_file_path(), head_result.stdout.strip())

    # Create stash of tracked file changes
    # Note: git stash create (without -u) only captures changes to tracked files
    # Untracked files that we modify will be handled by lazy snapshots
    stash_result = run_git_command(["stash", "create"], check=False)
    if stash_result.returncode == 0 and stash_result.stdout.strip():
        write_text_file_contents(get_abort_stash_file_path(), stash_result.stdout.strip())


def snapshot_file_if_untracked(file_path: str) -> None:
    """Snapshot an untracked file before modification for abort functionality."""
    # Check index status using git ls-files --stage
    # - Not in output: untracked (should snapshot)
    # - Empty blob hash (e69de29...): intent-to-add (should snapshot)
    # - Real blob hash: tracked with content (don't snapshot)
    EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

    stage_result = run_git_command(["ls-files", "--stage", "--", file_path], check=False)
    if not stage_result.stdout.strip():
        # File not in index at all - it's untracked
        pass  # Continue to snapshot
    else:
        # File is in index - check if it has real content or is intent-to-add
        # Format: <mode> <hash> <stage>\t<path>
        parts = stage_result.stdout.strip().split()
        if len(parts) >= 2:
            blob_hash = parts[1]
            if blob_hash != EMPTY_BLOB_HASH:
                return  # File has real content in index, don't snapshot

    # Check if already snapshotted
    snapshotted_files = read_file_paths_file(get_abort_snapshot_list_file_path())
    if file_path in snapshotted_files:
        return  # Already snapshotted

    # Read current file content
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if not full_path.exists():
        return  # File doesn't exist

    # Save snapshot (use binary copy to handle all file types)
    snapshot_dir = get_abort_snapshots_directory_path()
    snapshot_path = snapshot_dir / file_path
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(full_path, snapshot_path)

    # Record snapshot in list
    append_file_path_to_file(get_abort_snapshot_list_file_path(), file_path)


def command_start(unified: int = 3) -> None:
    """Start a new batch staging session."""
    require_git_repository()
    ensure_state_directory_exists()

    # Save context lines for this session
    write_text_file_contents(get_context_lines_file_path(), str(unified))

    # Initialize abort state for new session
    initialize_abort_state()


def command_stop() -> None:
    """Stop the current batch staging session."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(_("✓ State cleared."))


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    ensure_state_directory_exists()


def command_show() -> None:
    """Show the first unprocessed hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff and show first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            # Display this unprocessed hunk
            print_colored_patch(patch_text)
            return

    # Either no changes or all hunks are blocked
    print(_("No more hunks to process."))


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Apply the hunk to the index
        try:
            subprocess.run(
                ["git", "apply", "--cached"],
                input=patch_text,
                text=True,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(_("Failed to apply hunk: {}").format(e.stderr))
            return

        # Add hash to blocklist
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk staged from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))


def command_skip(*, quiet: bool = False) -> None:
    """Skip the current hunk without staging it."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Add hash to blocklist (without staging)
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk skipped from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))


def command_discard(*, quiet: bool = False) -> None:
    """Discard the current hunk from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    hunk_count = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        hunk_count += 1
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

        # Apply the hunk in reverse to discard from working tree
        try:
            subprocess.run(
                ["git", "apply", "--reverse"],
                input=patch_text,
                text=True,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print(_("Failed to discard hunk: {}").format(e.stderr))
            return

        # After reverse-applying a new file, delete it if it became empty
        # (git apply -R on new files empties them but doesn't delete them)
        is_new_file = "--- /dev/null" in patch_text
        if is_new_file:
            absolute_path = get_git_repository_root_path() / filename
            if absolute_path.exists():
                content = read_text_file_contents(absolute_path)
                if not content.strip():  # File is empty
                    absolute_path.unlink()

        # Add hash to blocklist
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk discarded from {}").format(filename))
            return
        break

    if not quiet:
        if hunk_count == 0:
            print(_("No changes to discard."))
        else:
            print(_("No more hunks to process."))


def command_status() -> None:
    """Show current session status."""
    require_git_repository()

    # Check if session is active
    state_dir = get_state_directory_path()
    if not state_dir.exists():
        print(_("No batch staging session in progress."))
        print(_("Run 'git-stage-batch start' to begin."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines()) if blocklist_text else set()
    processed_count = len(blocked_hashes)

    # Count remaining hunks and find current file
    remaining_hunks = 0
    current_file = None
    total_hunks = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        total_hunks += 1
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            if current_file is None:
                current_file = patch.new_path
            remaining_hunks += 1

    # Display status
    print(_("Session active"))
    print(_("Processed: {} hunks").format(processed_count))
    print(_("Remaining: {} hunks").format(remaining_hunks))

    if current_file:
        print(_("Current file: {}").format(current_file))
    elif total_hunks == 0:
        print(_("No changes in working tree"))
    else:
        print(_("All hunks processed"))
