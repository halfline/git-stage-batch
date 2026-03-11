"""Command implementations for git-stage-batch."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

from .display import print_annotated_hunk_with_aligned_gutter, print_colored_patch
from .editor import (
    build_target_index_content_with_selected_lines,
    build_target_working_tree_content_with_discarded_lines,
    update_index_with_blob_content,
)
from .hashing import compute_stable_hunk_hash
from .i18n import _
from .line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from .models import CurrentLines, HunkHeader, LineEntry
from .parser import (
    build_current_lines_from_patch_text,
    parse_unified_diff_into_single_hunk_patches,
    write_snapshots_for_current_file_path,
)
from .state import (
    add_file_to_gitignore,
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
    get_blocked_files_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_git_repository_root_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_state_directory_path,
    get_working_tree_snapshot_file_path,
    read_file_paths_file,
    read_text_file_contents,
    remove_file_from_gitignore,
    remove_file_path_from_file,
    require_git_repository,
    resolve_file_path_to_repo_relative,
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

    # Get blocked files list
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Get already auto-added files to avoid redundant git add -N
    auto_added_path = get_auto_added_files_file_path()
    auto_added_files = set(read_file_paths_file(auto_added_path))

    # Add untracked files that aren't blocked and haven't been auto-added yet
    for file_path in untracked_files:
        if file_path not in blocked_files and file_path not in auto_added_files:
            result = run_git_command(["add", "-N", file_path], check=False)
            if result.returncode == 0:
                append_file_path_to_file(auto_added_path, file_path)


def command_start(unified: int = 3) -> None:
    """Start a new batch staging session and display first hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Save context lines for this session
    write_text_file_contents(get_context_lines_file_path(), str(unified))

    # Initialize abort state for new session
    initialize_abort_state()

    clear_current_hunk_state_files()
    if not find_and_cache_next_unblocked_hunk():
        sys.exit(2)


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


def clear_current_hunk_state_files() -> None:
    """Clear all cached current hunk state files."""
    get_current_hunk_patch_file_path().unlink(missing_ok=True)
    get_current_hunk_hash_file_path().unlink(missing_ok=True)
    get_current_lines_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)


def _snapshots_are_stale(file_path: str) -> bool:
    """Check if cached snapshots are stale (file changed since snapshots taken).

    Returns True if the file has been committed or otherwise changed such that
    the cached hunk no longer applies.
    """
    snapshot_base_path = get_index_snapshot_file_path()
    snapshot_new_path = get_working_tree_snapshot_file_path()

    # Missing snapshots means state is incomplete/stale
    if not snapshot_base_path.exists() or not snapshot_new_path.exists():
        return True

    # Read cached snapshots
    cached_index_content = read_text_file_contents(snapshot_base_path)
    cached_worktree_content = read_text_file_contents(snapshot_new_path)

    # Get current file content from index
    try:
        result = run_git_command(["show", f":{file_path}"], check=False)
        if result.returncode != 0:
            # File not in index (was deleted, or never added)
            current_index_content = ""
        else:
            current_index_content = result.stdout
    except Exception:
        return True  # Error reading means state is stale

    # Get current file content from working tree
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        current_worktree_content = read_text_file_contents(file_full_path)
    except Exception:
        return True  # Error reading means state is stale

    # Compare snapshots with current state
    return (cached_index_content != current_index_content or
            cached_worktree_content != current_worktree_content)


def require_current_hunk_and_check_stale() -> None:
    """Ensure current hunk exists and is not stale, exit with error otherwise."""
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error(_("No current hunk. Run 'start' first."))

    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def convert_current_lines_to_serializable_dict(current_lines: CurrentLines) -> dict[str, Any]:
    """Convert CurrentLines to a JSON-serializable dictionary."""
    return {
        "path": current_lines.path,
        "header": {
            "old_start": current_lines.header.old_start,
            "old_len": current_lines.header.old_len,
            "new_start": current_lines.header.new_start,
            "new_len": current_lines.header.new_len,
        },
        "lines": [
            {
                "id": line_entry.id,
                "kind": line_entry.kind,
                "old_lineno": line_entry.old_line_number,
                "new_lineno": line_entry.new_line_number,
                "text": line_entry.text,
            }
            for line_entry in current_lines.lines
        ],
    }


def find_and_cache_next_unblocked_hunk() -> bool:
    """Find the next hunk that isn't blocked and cache it as current.

    Returns True if a hunk was found and cached, False otherwise.
    """
    diff_text = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"], check=False).stdout
    if not diff_text.strip():
        print(_("No pending hunks."), file=sys.stderr)
        return False

    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)
    if not single_hunk_patches:
        print(_("No pending hunks."), file=sys.stderr)
        return False

    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    for single_hunk in single_hunk_patches:
        patch_text = single_hunk.to_patch_text()
        hunk_hash = compute_stable_hunk_hash(patch_text)
        if hunk_hash in blocked_hashes:
            continue

        # Skip hunks from blocked files
        current_lines = build_current_lines_from_patch_text(patch_text)
        if current_lines.path in blocked_files:
            continue

        write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
        write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

        write_text_file_contents(get_current_lines_json_file_path(),
                                 json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                            ensure_ascii=False, indent=0))
        write_snapshots_for_current_file_path(current_lines.path)

        print_annotated_hunk_with_aligned_gutter(current_lines)
        return True

    print(_("No pending hunks."), file=sys.stderr)
    return False


def _recalculate_current_hunk_for_file(file_path: str) -> None:
    """Recalculate the current hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.
    """
    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())

    # Get fresh diff
    result = run_git_command(["diff", "--no-color"], check=False)
    diff_text = result.stdout

    if not diff_text.strip():
        clear_current_hunk_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return

    # Parse diff and find first hunk for this file
    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    for single_hunk in single_hunk_patches:
        if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
            continue

        patch_text = single_hunk.to_patch_text()
        hunk_hash = compute_stable_hunk_hash(patch_text)

        if hunk_hash in blocked_hashes:
            continue

        # Cache this hunk as current
        write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
        write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

        current_lines = build_current_lines_from_patch_text(patch_text)
        write_text_file_contents(get_current_lines_json_file_path(),
                                json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                          ensure_ascii=False, indent=0))
        write_snapshots_for_current_file_path(current_lines.path)

        print_annotated_hunk_with_aligned_gutter(current_lines)
        return

    # No more hunks for this file, advance to next file
    clear_current_hunk_state_files()
    command_show()


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
            # Cache this hunk as current
            write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_current_hunk_hash_file_path(), patch_hash)

            current_lines = build_current_lines_from_patch_text(patch_text)
            write_text_file_contents(get_current_lines_json_file_path(),
                                    json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                              ensure_ascii=False, indent=0))

            # Save snapshots of file state for staleness detection
            write_snapshots_for_current_file_path(current_lines.path)

            print_annotated_hunk_with_aligned_gutter(current_lines)
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


def command_block_file(file_path_arg: str) -> None:
    """Permanently exclude a file by adding it to .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for block-file command."))

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Add to .gitignore
    add_file_to_gitignore(file_path)

    # Add to blocked-files state
    append_file_path_to_file(get_blocked_files_file_path(), file_path)

    print(_("Blocked file: {}").format(file_path))


def command_unblock_file(file_path_arg: str) -> None:
    """Remove a file from .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for unblock-file command."))

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Remove from .gitignore
    removed_from_gitignore = remove_file_from_gitignore(file_path)

    # Remove from blocked-files state
    remove_file_path_from_file(get_blocked_files_file_path(), file_path)

    if removed_from_gitignore:
        print(_("Unblocked file: {}").format(file_path))
    else:
        print(_("Removed from blocked list: {} (was not in .gitignore)").format(file_path))


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

    # Check for cached current hunk with line details
    if get_current_hunk_patch_file_path().exists() and get_current_lines_json_file_path().exists():
        try:
            current_lines = load_current_lines_from_state()
            changed_ids = current_lines.changed_line_ids()
            if changed_ids:
                ids_str = ",".join(str(id) for id in changed_ids)
                print(_("Current hunk: {}:{} [#{}]").format(current_lines.path, current_lines.header.old_start, ids_str))
            else:
                print(_("Current hunk: {}:{}").format(current_lines.path, current_lines.header.old_start))
        except (json.JSONDecodeError, KeyError):
            # Fall back to basic file display if state is corrupted
            if current_file:
                print(_("Current file: {}").format(current_file))
    elif current_file:
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


# --------------------------- Line-level helpers ---------------------------

def convert_current_lines_to_serializable_dict(current_lines: CurrentLines) -> dict[str, Any]:
    """Convert CurrentLines to a JSON-serializable dictionary."""
    return {
        "path": current_lines.path,
        "header": {
            "old_start": current_lines.header.old_start,
            "old_len": current_lines.header.old_len,
            "new_start": current_lines.header.new_start,
            "new_len": current_lines.header.new_len,
        },
        "lines": [
            {
                "id": line_entry.id,
                "kind": line_entry.kind,
                "old_lineno": line_entry.old_line_number,
                "new_lineno": line_entry.new_line_number,
                "text": line_entry.text,
            }
            for line_entry in current_lines.lines
        ],
    }


def load_current_lines_from_state() -> CurrentLines:
    """Load the current hunk from saved state."""
    if not get_current_hunk_patch_file_path().exists() or not get_current_lines_json_file_path().exists():
        exit_with_error(_("No current hunk. Run 'start' first."))
    data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
    header = HunkHeader(**data["header"])
    lines = [LineEntry(id=le["id"],
                       kind=le["kind"],
                       old_line_number=le["old_lineno"],
                       new_line_number=le["new_lineno"],
                       text=le["text"])
             for le in data["lines"]]
    return CurrentLines(path=data["path"], header=header, lines=lines)


def compute_remaining_changed_line_ids() -> list[int]:
    """Compute which changed line IDs haven't been processed yet."""
    current_lines = load_current_lines_from_state()
    all_changed_ids = set(current_lines.changed_line_ids())
    included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    processed_ids = included_ids | skipped_ids
    remaining_ids = all_changed_ids - processed_ids
    return sorted(remaining_ids)


# --------------------------- Line-level commands ---------------------------

def command_include_line(line_id_specification: str) -> None:
    """Stage only the specified lines to the index."""
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    combined_include_ids = already_included_ids | set(requested_ids)

    current_lines = load_current_lines_from_state()

    # Get base content from index snapshot (captured when hunk was loaded)
    base_text = read_text_file_contents(get_index_snapshot_file_path())

    target_index_content = build_target_index_content_with_selected_lines(current_lines, combined_include_ids, base_text)
    update_index_with_blob_content(current_lines.path, target_index_content)

    # Update processed include IDs
    write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)

    # After modifying index, recalculate hunk for the SAME file
    _recalculate_current_hunk_for_file(current_lines.path)

    print(f"✓ Included line(s): {line_id_specification}")


def command_skip_line(line_id_specification: str) -> None:
    """Mark only the specified lines as skipped."""
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    already_skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    combined_skip_ids = already_skipped_ids | set(requested_ids)

    # Update processed skip IDs
    write_line_ids_file(get_processed_skip_ids_file_path(), combined_skip_ids)

    print(f"✓ Skipped line(s): {line_id_specification}")


def command_discard_line(line_id_specification: str) -> None:
    """Discard only the specified lines from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    current_lines = load_current_lines_from_state()

    # Get current working tree content
    working_file_path = get_git_repository_root_path() / current_lines.path
    if working_file_path.exists():
        working_text = working_file_path.read_text(encoding="utf-8", errors="surrogateescape")
    else:
        exit_with_error(f"File not found in working tree: {current_lines.path}")

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_with_discarded_lines(
        current_lines, set(requested_ids), working_text)

    # Write back to working tree
    working_file_path.write_text(target_working_content, encoding="utf-8", errors="surrogateescape")

    # After modifying working tree, recalculate hunk for the SAME file
    _recalculate_current_hunk_for_file(current_lines.path)
