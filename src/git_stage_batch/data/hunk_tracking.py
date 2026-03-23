"""Hunk navigation and state management."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from ..core.hashing import compute_stable_hunk_hash
from ..core.models import CurrentLines
from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_streaming, write_snapshots_for_current_file_path
from ..core.line_selection import write_line_ids_file
from ..exceptions import exit_with_error
from ..i18n import _
from ..output.hunk import print_annotated_hunk_with_aligned_gutter
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_current_lines_to_serializable_dict


def clear_current_hunk_state_files() -> None:
    """Clear all cached current hunk state files."""
    get_current_hunk_patch_file_path().unlink(missing_ok=True)
    get_current_hunk_hash_file_path().unlink(missing_ok=True)
    get_current_lines_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)


def find_and_cache_next_unblocked_hunk() -> Optional[CurrentLines]:
    """Find the next hunk that isn't blocked and cache it as current.

    Returns:
        CurrentLines for the hunk if found, None otherwise
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Stream git diff and parse incrementally - stops after first unblocked hunk found
    try:
        for single_hunk in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
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

            return current_lines
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    return None


def advance_to_next_hunk() -> None:
    """Clear current hunk state and advance to the next unblocked hunk."""
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def show_current_hunk() -> None:
    """Display the currently cached hunk.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        current_lines = build_current_lines_from_patch_text(patch_text)
        print_annotated_hunk_with_aligned_gutter(current_lines)


def advance_to_and_show_next_hunk() -> None:
    """Advance to next hunk and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk with displaying it. If no more hunks exist, prints
    a message to stderr.
    """
    advance_to_next_hunk()

    # Check if a hunk was cached
    patch_path = get_current_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        current_lines = build_current_lines_from_patch_text(patch_text)
        print_annotated_hunk_with_aligned_gutter(current_lines)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def snapshots_are_stale(file_path: str) -> bool:
    """Check if cached snapshots are stale (file changed since snapshots taken).

    Args:
        file_path: Repository-relative path to check

    Returns:
        True if the file has been committed or otherwise changed such that
        the cached hunk no longer applies
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
        if snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_current_hunk_for_file(file_path: str) -> None:
    """Recalculate the current hunk for a specific file after modifications.

    After discard --line or include --line changes the working tree or index,
    the cached hunk is stale. This recalculates it for the same file.

    Args:
        file_path: Repository-relative path to recalculate hunk for
    """
    # Clear processed IDs since old line numbers don't apply to fresh hunk
    write_line_ids_file(get_processed_include_ids_file_path(), set())

    # Load blocklist
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Stream git diff and parse incrementally - stops after first matching hunk found
    try:
        for single_hunk in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
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
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_current_hunk_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return

    # No more hunks for this file, advance to next file
    clear_current_hunk_state_files()
    # Import here to avoid circular dependency
    from ..commands.show import command_show
    command_show()
