"""Command implementations for the git-stage-batch tool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

from .display import print_annotated_hunk_with_aligned_gutter
from .editor import (
    build_target_index_content_with_selected_lines,
    build_target_working_tree_content_with_discarded_lines,
    update_index_with_blob_content,
)
from .hashing import compute_stable_hunk_hash
from .line_selection import (
    format_line_ids_as_ranges,
    parse_line_id_specification,
    read_line_ids_file,
    write_line_ids_file,
)
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
    clear_current_hunk_state_files,
    ensure_state_directory_exists,
    exit_with_error,
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_blocked_files_file_path,
    get_block_list_file_path,
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
    write_file_paths_file,
    write_text_file_contents,
)


# --------------------------- Helper functions ---------------------------

def is_hunk_hash_in_block_list(hunk_hash: str) -> bool:
    """Check if a hunk hash is in the blocklist."""
    return hunk_hash in set(read_text_file_contents(get_block_list_file_path()).splitlines())


def append_current_hunk_hash_to_block_list() -> None:
    """Add the current hunk's hash to the blocklist."""
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    if hunk_hash:
        append_lines_to_file(get_block_list_file_path(), [hunk_hash])
        unique = "\n".join(sorted(set(read_text_file_contents(get_block_list_file_path()).splitlines()))) + "\n"
        write_text_file_contents(get_block_list_file_path(), unique)


def summarize_current_hunk_header_line(current_patch_text: str) -> str:
    """Generate a summary line for a hunk."""
    current_lines = build_current_lines_from_patch_text(current_patch_text)
    header = current_lines.header
    return f"{current_lines.path} :: @@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"


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

    if not file_full_path.exists():
        # File was deleted
        current_worktree_content = ""
    else:
        current_worktree_content = read_text_file_contents(file_full_path)

    # Check if index content changed (file was staged/committed)
    # This is the most common case - changes were committed
    if current_index_content != cached_index_content:
        return True

    # Check if working tree changed but doesn't match our cached working tree snapshot
    # This means file was edited externally
    if current_worktree_content != cached_worktree_content:
        return True

    return False


def load_current_lines_from_state() -> CurrentLines:
    """Load the current hunk from saved state."""
    if not get_current_hunk_patch_file_path().exists() or not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")
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
    remaining = sorted(all_changed_ids - included_ids - skipped_ids)
    return remaining


def _recalculate_current_hunk_for_file(file_path: str) -> None:
    """Recalculate the current hunk for a specific file after modifications.

    After discard-line or include-line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.
    """
    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())

    # Get fresh diff
    auto_add_untracked_files()
    diff_text = run_git_command(["diff", "-U3", "--no-color"], check=False).stdout

    if not diff_text.strip():
        clear_current_hunk_state_files()
        print("No pending hunks.", file=sys.stderr)
        return

    # Parse diff and find first hunk for this file
    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    for single_hunk in single_hunk_patches:
        if single_hunk.old_path != file_path and single_hunk.new_path != file_path:
            continue

        patch_text = single_hunk.to_patch_text()
        hunk_hash = compute_stable_hunk_hash(patch_text)

        if is_hunk_hash_in_block_list(hunk_hash):
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
    find_and_cache_next_unblocked_hunk()


def advance_if_hunk_complete_else_show() -> None:
    """Advance to next hunk if current is complete, otherwise show current."""
    remaining_ids = compute_remaining_changed_line_ids()
    if not remaining_ids:
        append_current_hunk_hash_to_block_list()
        clear_current_hunk_state_files()
        find_and_cache_next_unblocked_hunk()
    else:
        print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())


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
    auto_added_files = set(read_file_paths_file(get_auto_added_files_file_path()))

    # Add untracked files that aren't blocked and haven't been auto-added yet
    for file_path in untracked_files:
        if file_path not in blocked_files and file_path not in auto_added_files:
            result = run_git_command(["add", "-N", file_path], check=False)
            if result.returncode == 0:
                append_file_path_to_file(get_auto_added_files_file_path(), file_path)


def find_and_cache_next_unblocked_hunk() -> bool:
    """Find the next hunk that isn't blocked and cache it as current."""
    auto_add_untracked_files()
    diff_text = run_git_command(["diff", "-U3", "--no-color"], check=False).stdout
    if not diff_text.strip():
        print("No pending hunks.", file=sys.stderr)
        return False

    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)
    if not single_hunk_patches:
        print("No pending hunks.", file=sys.stderr)
        return False

    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    for single_hunk in single_hunk_patches:
        patch_text = single_hunk.to_patch_text()
        hunk_hash = compute_stable_hunk_hash(patch_text)
        if is_hunk_hash_in_block_list(hunk_hash):
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

    print("No pending hunks.", file=sys.stderr)
    return False


# --------------------------- Abort state management ---------------------------

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

    # Add to snapshot list
    append_file_path_to_file(get_abort_snapshot_list_file_path(), file_path)


# --------------------------- Command handlers ---------------------------

def command_start() -> None:
    """Find and display the first unprocessed hunk."""
    require_git_repository()

    # Check if batch staging is already in progress
    state_dir = get_state_directory_path()
    if state_dir.exists() and any(state_dir.iterdir()):
        print("Batch staging already in process, starting again", file=sys.stderr)
        command_again()
        return

    # Ensure state directory exists before initializing abort state
    ensure_state_directory_exists()

    # Initialize abort state for new session
    initialize_abort_state()
    clear_current_hunk_state_files()
    if not find_and_cache_next_unblocked_hunk():
        sys.exit(2)


def command_show(porcelain: bool = False) -> None:
    """Display the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    has_hunk = get_current_hunk_patch_file_path().exists() and get_current_lines_json_file_path().exists()

    # Check for stale state and clear silently if detected
    if has_hunk:
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            has_hunk = False

    if porcelain:
        # Porcelain mode: exit 0 if hunk exists, 1 if not (no output)
        sys.exit(0 if has_hunk else 1)
    else:
        # Normal mode: display hunk or show error
        if not has_hunk:
            exit_with_error("No current hunk. Run 'start' first.")
        print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())


def command_include() -> None:
    """Stage the entire current hunk to the index."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk to include. Run 'start' first.")

    # Check for stale state
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue.")

    try:
        run_git_command(["apply", "--cached", "--index", str(get_current_hunk_patch_file_path())])
    except Exception as error:
        stderr = getattr(error, 'stderr', '').strip() if hasattr(error, 'stderr') else ''
        stdout = getattr(error, 'stdout', '').strip() if hasattr(error, 'stdout') else ''
        exit_with_error(f"Failed to apply hunk: {stderr or stdout or 'git apply failed.'}")
    append_current_hunk_hash_to_block_list()
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_skip() -> None:
    """Skip the current hunk (mark as skipped)."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_hash_file_path().exists():
        exit_with_error("No current hunk to skip. Run 'start' first.")

    # Check for stale state
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue.")

    append_current_hunk_hash_to_block_list()
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_discard() -> None:
    """Reverse-apply the current hunk to the working tree."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk to discard. Run 'start' first.")

    # Check for stale state
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue.")

        # Snapshot file if untracked before discarding
        snapshot_file_if_untracked(file_path)

    try:
        run_git_command(["apply", "-R", str(get_current_hunk_patch_file_path())])
    except Exception as error:
        stderr = getattr(error, 'stderr', '').strip() if hasattr(error, 'stderr') else ''
        stdout = getattr(error, 'stdout', '').strip() if hasattr(error, 'stdout') else ''
        exit_with_error(f"Failed to discard hunk: {stderr or stdout or 'git apply -R failed.'}")
    append_current_hunk_hash_to_block_list()
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_include_line(line_id_specification: str) -> None:
    """Stage only the specified lines to the index."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists() or not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    requested_ids = parse_line_id_specification(line_id_specification)
    already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    combined_include_ids = already_included_ids | set(requested_ids)

    current_lines = load_current_lines_from_state()
    # Save the current file path before modifying index
    current_file_path = current_lines.path

    # Snapshot file if untracked before modifying
    snapshot_file_if_untracked(current_file_path)

    base_text = read_text_file_contents(get_index_snapshot_file_path())
    target_index_content = build_target_index_content_with_selected_lines(current_lines, combined_include_ids, base_text)
    update_index_with_blob_content(current_lines.path, target_index_content)

    write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)

    # After modifying index, recalculate hunk for the SAME file
    _recalculate_current_hunk_for_file(current_file_path)


def command_skip_line(line_id_specification: str) -> None:
    """Mark the specified lines as skipped."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    requested_ids = parse_line_id_specification(line_id_specification)
    existing_skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    existing_skipped_ids |= set(requested_ids)
    write_line_ids_file(get_processed_skip_ids_file_path(), existing_skipped_ids)
    advance_if_hunk_complete_else_show()


def command_discard_line(line_id_specification: str) -> None:
    """Remove the specified lines from the working tree."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    requested_ids = parse_line_id_specification(line_id_specification)
    discard_ids = set(requested_ids)

    current_lines = load_current_lines_from_state()
    absolute_path = get_git_repository_root_path() / current_lines.path
    working_text = read_text_file_contents(absolute_path) if absolute_path.exists() else ""

    # Save the current file path before modifying working tree
    current_file_path = current_lines.path

    # Snapshot file if untracked before discarding lines
    snapshot_file_if_untracked(current_file_path)

    new_working_text = build_target_working_tree_content_with_discarded_lines(current_lines, discard_ids, working_text)
    write_text_file_contents(absolute_path, new_working_text)

    # After modifying working tree, recalculate hunk for the SAME file
    # Note: Don't track discarded lines in processed-skip since they no longer exist
    _recalculate_current_hunk_for_file(current_file_path)


def command_include_file() -> None:
    """Stage all remaining hunks from the current file."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    # Get current file path
    current_lines = load_current_lines_from_state()
    current_file_path = current_lines.path

    # Get all hunks from diff
    auto_add_untracked_files()
    diff_text = run_git_command(["diff", "-U3", "--no-color"], check=False).stdout
    if not diff_text.strip():
        return

    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    # Process all hunks from this file
    for single_hunk in single_hunk_patches:
        patch_text = single_hunk.to_patch_text()
        hunk_current_lines = build_current_lines_from_patch_text(patch_text)

        # Only process hunks from the current file
        if hunk_current_lines.path != current_file_path:
            continue

        # Stage all changes in this hunk
        changed_ids = hunk_current_lines.changed_line_ids()
        base_text = read_text_file_contents(get_index_snapshot_file_path())

        # Write snapshots for this file if not already done
        write_snapshots_for_current_file_path(hunk_current_lines.path)
        base_text_path = get_index_snapshot_file_path()
        if base_text_path.exists():
            base_text = read_text_file_contents(base_text_path)
        else:
            base_text = ""

        target_index_content = build_target_index_content_with_selected_lines(
            hunk_current_lines, changed_ids, base_text
        )
        update_index_with_blob_content(hunk_current_lines.path, target_index_content)

        # Block this hunk
        hunk_hash = compute_stable_hunk_hash(patch_text)
        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

    # Clear current state and advance
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_skip_file() -> None:
    """Skip all remaining hunks from the current file."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    # Get current file path
    current_lines = load_current_lines_from_state()
    current_file_path = current_lines.path

    # Get all hunks from diff
    auto_add_untracked_files()
    diff_text = run_git_command(["diff", "-U3", "--no-color"], check=False).stdout
    if not diff_text.strip():
        return

    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    # Block all hunks from this file
    for single_hunk in single_hunk_patches:
        patch_text = single_hunk.to_patch_text()
        hunk_current_lines = build_current_lines_from_patch_text(patch_text)

        # Only process hunks from the current file
        if hunk_current_lines.path != current_file_path:
            continue

        # Block this hunk
        hunk_hash = compute_stable_hunk_hash(patch_text)
        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

    # Clear current state and advance
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_again() -> None:
    """Clear all state and start fresh."""
    require_git_repository()
    # Reset auto-added files before clearing state
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    # Save persistent state (blocked-files) before clearing
    blocked_files = []
    if get_blocked_files_file_path().exists():
        blocked_files = read_file_paths_file(get_blocked_files_file_path())

    # Clear all state
    try:
        for path in get_state_directory_path().glob("*"):
            path.unlink(missing_ok=True)
        get_state_directory_path().rmdir()
    except Exception:
        pass
    ensure_state_directory_exists()

    # Restore persistent state
    if blocked_files:
        write_file_paths_file(get_blocked_files_file_path(), blocked_files)

    find_and_cache_next_unblocked_hunk()


def command_stop() -> None:
    """Clear all state."""
    require_git_repository()
    # Reset auto-added files before clearing state
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)
    try:
        for path in get_state_directory_path().glob("*"):
            path.unlink(missing_ok=True)
        get_state_directory_path().rmdir()
    except Exception:
        pass
    print("✓ State cleared.")


def command_abort() -> None:
    """Abort the session and undo all changes including commits and discards."""
    require_git_repository()

    # Check if abort state exists
    if not get_abort_head_file_path().exists():
        exit_with_error("No session to abort. Abort state not found.")

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

    print(f"Resetting to {abort_head[:7]}...", file=sys.stderr)
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
                print(f"Restored: {file_path}", file=sys.stderr)

    # Apply original stash if it exists (with --index to restore staged state)
    if abort_stash:
        print("Applying original changes...", file=sys.stderr)
        result = subprocess.run(
            ["git", "stash", "apply", "--index", abort_stash],
            env=env,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"⚠ Warning: Could not apply stash cleanly: {result.stderr}", file=sys.stderr)

    # Clear all state
    try:
        for path in get_state_directory_path().glob("*"):
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        get_state_directory_path().rmdir()
    except Exception:
        pass

    print("✓ Session aborted. All changes reverted.", file=sys.stderr)


def command_status(porcelain: bool = False) -> None:
    """Show current state summary."""
    require_git_repository()

    # Check for stale state and clear silently if detected
    if get_current_hunk_patch_file_path().exists() and get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()

    # Gather status information
    current_hunk = None
    remaining_line_ids = []
    if get_current_hunk_patch_file_path().exists():
        current_hunk = summarize_current_hunk_header_line(
            read_text_file_contents(get_current_hunk_patch_file_path())
        )
        remaining_line_ids = compute_remaining_changed_line_ids()

    block_list_lines = read_text_file_contents(get_block_list_file_path()).splitlines() if get_block_list_file_path().exists() else []
    blocked_count = len([x for x in block_list_lines if x.strip()])

    if porcelain:
        # Output machine-readable JSON
        status_data = {
            "current_hunk": current_hunk,
            "remaining_line_ids": remaining_line_ids,
            "blocked_hunks": blocked_count,
            "state_directory": str(get_state_directory_path()),
        }
        print(json.dumps(status_data, ensure_ascii=False))
    else:
        # Human-readable output
        if current_hunk:
            print("current:", current_hunk)
            if remaining_line_ids:
                print("remaining lines:", format_line_ids_as_ranges(remaining_line_ids))
            else:
                print("remaining lines: 0")
        else:
            print("current: none")
        print(f"blocked: {blocked_count}")
        print(f"state:   {get_state_directory_path()}")


def command_block_file(file_path_arg: str) -> None:
    """Add a file to .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    # Determine which file to block
    if not file_path_arg:
        # Try to infer from current hunk
        if not get_current_hunk_patch_file_path().exists():
            exit_with_error("No file path provided and no current hunk to infer from.")
        current_lines = load_current_lines_from_state()
        file_path = current_lines.path
    else:
        file_path = file_path_arg

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path)

    # Add to .gitignore
    add_file_to_gitignore(file_path)

    # Add to blocked-files state
    append_file_path_to_file(get_blocked_files_file_path(), file_path)

    # If file was auto-added, reset it and remove from auto-added list
    auto_added_files = read_file_paths_file(get_auto_added_files_file_path())
    if file_path in auto_added_files:
        run_git_command(["reset", "--", file_path], check=False)
        remove_file_path_from_file(get_auto_added_files_file_path(), file_path)

    # If current hunk is from this file, advance to next
    if get_current_hunk_patch_file_path().exists():
        current_lines = load_current_lines_from_state()
        if current_lines.path == file_path:
            append_current_hunk_hash_to_block_list()
            clear_current_hunk_state_files()
            find_and_cache_next_unblocked_hunk()

    print(f"Blocked file: {file_path}", file=sys.stderr)


# --------------------------- Interactive mode helpers ---------------------------

def confirm_destructive_operation(operation: str, message: str) -> bool:
    """Confirm a destructive operation with the user. Returns True if confirmed."""
    print()
    print(f"⚠️  {message}")
    try:
        response = input("Are you sure? [yes/NO]: ").strip().lower()
        return response in ('yes', 'y')
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def print_interactive_help() -> None:
    """Print help for interactive mode."""
    print("""
y - yes, stage this hunk
n - no, skip this hunk for now
d - discard this hunk from working tree
q - quit interactive mode
a - stage this hunk and all remaining hunks
l - interactively select lines from this hunk
f - stage or skip all hunks in current file
b - block current file (add to .gitignore)
? - print help
""")


def handle_interactive_line_selection() -> None:
    """Handle line-level selection in interactive mode."""
    current_lines = load_current_lines_from_state()
    changed_ids = current_lines.changed_line_ids()

    if not changed_ids:
        print("No changed lines in this hunk")
        return

    print(f"Changed line IDs: {','.join(map(str, changed_ids))}")

    try:
        action = input("Action for lines [i]nclude, [s]kip, or [d]iscard? ").strip().lower()
        if action not in ('i', 's', 'd'):
            print("Cancelled")
            print_annotated_hunk_with_aligned_gutter(current_lines)
            return

        line_spec = input("Enter line IDs (e.g., 1,3,5-7): ").strip()
        if not line_spec:
            print("Cancelled")
            print_annotated_hunk_with_aligned_gutter(current_lines)
            return

        if action == 'i':
            command_include_line(line_spec)
        elif action == 's':
            command_skip_line(line_spec)
        elif action == 'd':
            command_discard_line(line_spec)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        print_annotated_hunk_with_aligned_gutter(current_lines)


def handle_interactive_file_selection() -> None:
    """Handle file-level selection in interactive mode."""
    current_lines = load_current_lines_from_state()

    try:
        action = input(f"Action for all hunks in {current_lines.path} - [i]nclude or [s]kip? ").strip().lower()
        if action == 'i':
            command_include_file()
        elif action == 's':
            command_skip_file()
        else:
            print("Cancelled")
            print_annotated_hunk_with_aligned_gutter(current_lines)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        print_annotated_hunk_with_aligned_gutter(current_lines)


# --------------------------- Command implementations ---------------------------

def command_unblock_file(file_path_arg: str) -> None:
    """Remove a file from .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    # Require file path argument
    if not file_path_arg:
        exit_with_error("File path required for unblock-file command.")

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Remove from .gitignore (only if it has our marker)
    removed = remove_file_from_gitignore(file_path)

    # Remove from blocked-files state
    remove_file_path_from_file(get_blocked_files_file_path(), file_path)

    if removed:
        print(f"Unblocked file: {file_path}", file=sys.stderr)
    else:
        print(f"Removed from blocked list: {file_path} (was not in .gitignore with our marker)", file=sys.stderr)


def command_interactive() -> None:
    """Interactive mode similar to git add -p."""
    require_git_repository()

    # Initialize session if needed, otherwise use existing
    state_dir = get_state_directory_path()
    if not state_dir.exists() or not any(state_dir.iterdir()):
        ensure_state_directory_exists()
        clear_current_hunk_state_files()
        if not find_and_cache_next_unblocked_hunk():
            sys.exit(2)
    elif not get_current_hunk_patch_file_path().exists():
        # Session exists but no current hunk, find next
        if not find_and_cache_next_unblocked_hunk():
            print("No pending hunks.", file=sys.stderr)
            sys.exit(2)
    else:
        # Current hunk exists, display it
        print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())

    # Main interactive loop
    while get_current_hunk_patch_file_path().exists():
        # Show beginner-friendly prompt
        print()
        print("What do you want to do with this hunk?")
        print("  [i]nclude  - Stage this hunk to the index")
        print("  [s]kip     - Skip this hunk for now")
        print("  [d]iscard  - Remove this hunk from working tree (DESTRUCTIVE)")
        print("  [q]uit     - Exit interactive mode")
        print()
        print("More options: [a]ll, [l]ines, [f]ile, [b]lock, [?]help")
        print()

        try:
            choice = input("Action: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()  # New line after ^C
            break

        if choice in ('y', 'i', 'include'):
            # Include - stage this hunk
            command_include()
        elif choice in ('n', 's', 'skip'):
            # Skip - skip this hunk for now
            command_skip()
        elif choice in ('d', 'discard'):
            # Discard - remove from working tree (with confirmation)
            if confirm_destructive_operation("discard", "This will permanently remove the changes from your working tree."):
                command_discard()
            else:
                print("Cancelled.")
                print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
        elif choice in ('q', 'quit'):
            # Quit interactive mode
            break
        elif choice in ('a', 'all'):
            # Accept all remaining hunks (with confirmation)
            if confirm_destructive_operation("all", "This will stage ALL remaining hunks."):
                while get_current_hunk_patch_file_path().exists():
                    command_include()
            else:
                print("Cancelled.")
                print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
        elif choice in ('l', 'lines'):
            # Line-level operations
            handle_interactive_line_selection()
        elif choice in ('f', 'file'):
            # File-level operations
            handle_interactive_file_selection()
        elif choice in ('b', 'block'):
            # Block current file (with confirmation)
            current_lines = load_current_lines_from_state()
            if confirm_destructive_operation("block", f"This will add '{current_lines.path}' to .gitignore permanently."):
                command_block_file("")
            else:
                print("Cancelled.")
                print_annotated_hunk_with_aligned_gutter(current_lines)
        elif choice in ('e', 'edit'):
            # Edit hunk manually (future enhancement)
            print("Edit mode not yet implemented")
            print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
        elif choice == '?':
            print_interactive_help()
            print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
        elif choice == '':
            # Empty input, re-display hunk
            print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
        else:
            print(f"Unknown option: '{choice}'")
            print_interactive_help()
            print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())

    if not get_current_hunk_patch_file_path().exists():
        print("No pending hunks.")
