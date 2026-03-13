"""Command implementations for git-stage-batch."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .display import print_colored_patch
from .hashing import compute_stable_hunk_hash
from .i18n import _
from .parser import parse_unified_diff_into_single_hunk_patches
from .state import (
    append_file_path_to_file,
    append_lines_to_file,
    ensure_state_directory_exists,
    exit_with_error,
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_block_list_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_git_repository_root_path,
    get_state_directory_path,
    read_file_paths_file,
    read_text_file_contents,
    require_git_repository,
    run_git_command,
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


def auto_add_untracked_files() -> None:
    """Automatically run git add -N on untracked files (except blocked ones)."""
    # Get list of untracked files
    result = run_git_command(["ls-files", "--others", "--exclude-standard"], check=False)
    if result.returncode != 0:
        return

    untracked_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not untracked_files:
        return

    # Get already auto-added files to avoid redundant git add -N
    auto_added_path = get_auto_added_files_file_path()
    auto_added_files = set(read_file_paths_file(auto_added_path))

    # Add untracked files that haven't been auto-added yet
    for file_path in untracked_files:
        if file_path not in auto_added_files:
            result = run_git_command(["add", "-N", file_path], check=False)
            if result.returncode == 0:
                append_file_path_to_file(auto_added_path, file_path)


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
    # Reset auto-added files before clearing state
    auto_added_path = get_auto_added_files_file_path()
    if auto_added_path.exists():
        auto_added = read_file_paths_file(auto_added_path)
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(_("✓ State cleared."))


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    # Reset auto-added files before clearing state
    auto_added_path = get_auto_added_files_file_path()
    if auto_added_path.exists():
        auto_added = read_file_paths_file(auto_added_path)
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    ensure_state_directory_exists()


def command_show() -> None:
    """Show the first unprocessed hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to show."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            # Display this unprocessed hunk
            print_colored_patch(patch_text)
            return

    # All hunks are blocked
    print(_("No more hunks to process."))


def command_include() -> None:
    """Include (stage) the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to stage."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk
    current_patch = None
    current_hash = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            current_patch = patch
            current_hash = patch_hash
            break

    if current_patch is None:
        print(_("No more hunks to process."))
        return

    # Save current hunk info
    patch_text = current_patch.to_patch_text()
    write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_current_hunk_hash_file_path(), current_hash)

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
    append_lines_to_file(blocklist_path, [current_hash])

    print(_("✓ Hunk staged from {}").format(current_patch.new_path))


def command_include_file() -> None:
    """Include (stage) all hunks from the current file."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff to determine target file
    result = run_git_command(["diff", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to stage."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the target file
    target_file = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            target_file = patch.new_path
            break

    if target_file is None:
        print(_("No more hunks to process."))
        return

    # Repeatedly include hunks while we're still on the same file
    # Each call to command_include() stages one hunk and adds it to blocklist,
    # so subsequent calls automatically find the next unprocessed hunk
    while True:
        # Get fresh diff (index may have changed after previous include)
        result = run_git_command(["diff", "--no-color"])
        diff_text = result.stdout

        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        if not patches:
            break

        # Reload blocklist (updated by command_include)
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())

        # Find next unprocessed hunk
        found_target_file_hunk = False
        for patch in patches:
            patch_text = patch.to_patch_text()
            patch_hash = compute_stable_hunk_hash(patch_text)
            if patch_hash not in blocked_hashes:
                if patch.new_path == target_file:
                    found_target_file_hunk = True
                break

        if not found_target_file_hunk:
            # No more hunks from target file
            break

        # Include this hunk
        command_include()


def command_skip() -> None:
    """Skip the current hunk without staging it."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to process."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk
    current_patch = None
    current_hash = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            current_patch = patch
            current_hash = patch_hash
            break

    if current_patch is None:
        print(_("No more hunks to process."))
        return

    # Save current hunk info
    patch_text = current_patch.to_patch_text()
    write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_current_hunk_hash_file_path(), current_hash)

    # Add hash to blocklist (without staging)
    append_lines_to_file(blocklist_path, [current_hash])

    print(_("✓ Hunk skipped from {}").format(current_patch.new_path))


def command_skip_file() -> None:
    """Skip all hunks from the current file."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff to determine target file
    result = run_git_command(["diff", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to process."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the target file
    target_file = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            target_file = patch.new_path
            break

    if target_file is None:
        print(_("No more hunks to process."))
        return

    # Repeatedly skip hunks while we're still on the same file
    # Each call to command_skip() skips one hunk and adds it to blocklist,
    # so subsequent calls automatically find the next unprocessed hunk
    while True:
        # Get fresh diff
        result = run_git_command(["diff", "--no-color"])
        diff_text = result.stdout

        patches = parse_unified_diff_into_single_hunk_patches(diff_text)
        if not patches:
            break

        # Reload blocklist (updated by command_skip)
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())

        # Find next unprocessed hunk
        found_target_file_hunk = False
        for patch in patches:
            patch_text = patch.to_patch_text()
            patch_hash = compute_stable_hunk_hash(patch_text)
            if patch_hash not in blocked_hashes:
                if patch.new_path == target_file:
                    found_target_file_hunk = True
                break

        if not found_target_file_hunk:
            # No more hunks from target file
            break

        # Skip this hunk
        command_skip()


def command_discard() -> None:
    """Discard the current hunk from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to discard."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk
    current_patch = None
    current_hash = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            current_patch = patch
            current_hash = patch_hash
            break

    if current_patch is None:
        print(_("No more hunks to process."))
        return

    # Save current hunk info
    patch_text = current_patch.to_patch_text()
    write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_current_hunk_hash_file_path(), current_hash)

    # Snapshot file if untracked before discarding
    file_path = current_patch.new_path if current_patch.new_path != "/dev/null" else current_patch.old_path
    snapshot_file_if_untracked(file_path)

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
        file_path = current_patch.new_path
        absolute_path = get_git_repository_root_path() / file_path
        if absolute_path.exists():
            content = read_text_file_contents(absolute_path)
            if not content.strip():  # File is empty
                absolute_path.unlink()

    # Add hash to blocklist
    append_lines_to_file(blocklist_path, [current_hash])

    print(_("✓ Hunk discarded from {}").format(current_patch.new_path))


def command_discard_file() -> None:
    """Discard the entire current file from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    if not patches:
        print(_("No changes to discard."))
        return

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the file
    target_file = None
    for patch in patches:
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            target_file = patch.new_path
            break

    if target_file is None:
        print(_("No more hunks to process."))
        return

    # Snapshot the file if it's untracked (for abort functionality)
    snapshot_file_if_untracked(target_file)

    # Remove the file from working tree
    try:
        subprocess.run(
            ["git", "rm", "-f", target_file],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard file: {}").format(e.stderr.decode()))
        return

    # Mark all hunks from this file as processed in blocklist
    for patch in patches:
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash not in blocked_hashes:
            append_lines_to_file(blocklist_path, [patch_hash])

    print(_("✓ File discarded: {}").format(target_file))


def command_status() -> None:
    """Show current session status."""
    require_git_repository()

    # Check if session is active
    state_dir = get_state_directory_path()
    if not state_dir.exists():
        print(_("No batch staging session in progress."))
        print(_("Run 'git-stage-batch start' to begin."))
        return

    # Auto-add untracked files
    auto_add_untracked_files()

    # Get the current diff
    result = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])
    diff_text = result.stdout

    # Parse into hunks
    patches = parse_unified_diff_into_single_hunk_patches(diff_text)
    total_hunks = len(patches)

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines()) if blocklist_text else set()
    processed_count = len(blocked_hashes)

    # Count remaining hunks
    remaining_hunks = 0
    current_file = None
    for patch in patches:
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


def command_abort() -> None:
    """Abort the session and undo all changes including commits and discards."""
    require_git_repository()

    # Check if abort state exists
    if not get_abort_head_file_path().exists():
        exit_with_error(_("No session to abort. Abort state not found."))

    # Read abort state
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    abort_stash_path = get_abort_stash_file_path()
    abort_stash = read_text_file_contents(abort_stash_path).strip() if abort_stash_path.exists() else None

    # Reset auto-added files first
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    # Reset to start HEAD (undoes commits, resets index and tracked files)
    # Set GIT_REFLOG_ACTION for clear reflog entries
    env = os.environ.copy()
    env["GIT_REFLOG_ACTION"] = "stage-batch abort"

    print(_("Resetting to {}...").format(abort_head[:7]), file=sys.stderr)
    subprocess.run(
        ["git", "reset", "--hard", abort_head],
        env=env,
        check=True,
        capture_output=True,
        text=True
    )

    # Restore snapshotted untracked files
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        repo_root = get_git_repository_root_path()
        snapshots_dir = get_abort_snapshots_directory_path()

        for file_path in snapshotted_files:
            snapshot_path = snapshots_dir / file_path
            if snapshot_path.exists():
                target_path = repo_root / file_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot_path, target_path)
                print(_("Restored: {}").format(file_path), file=sys.stderr)

    # Apply original stash if it exists (with --index to restore staged state)
    if abort_stash:
        print(_("Applying original changes..."), file=sys.stderr)
        result = subprocess.run(
            ["git", "stash", "apply", "--index", abort_stash],
            env=env,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(_("⚠ Warning: Could not apply stash cleanly: {}").format(result.stderr), file=sys.stderr)

    # Clear all state
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)

    print(_("✓ Session aborted. All changes reverted."), file=sys.stderr)
