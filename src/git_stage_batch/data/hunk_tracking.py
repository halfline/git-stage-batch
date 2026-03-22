"""Hunk navigation, state management, staleness detection, and progress tracking."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from ..core.hashing import compute_stable_hunk_hash
from ..core.models import LineLevelChange
from ..core.diff_parser import build_line_changes_from_patch_text, parse_unified_diff_streaming, write_snapshots_for_selected_file_path
from ..core.line_selection import write_line_ids_file
from ..exceptions import exit_with_error
from ..i18n import _
from ..output.hunk import print_line_level_changes
from ..utils.file_io import read_file_paths_file, read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_working_tree_snapshot_file_path,
)
from .line_state import convert_line_changes_to_serializable_dict


def clear_selected_change_state_files() -> None:
    """Clear all cached selected hunk state files."""
    get_selected_hunk_patch_file_path().unlink(missing_ok=True)
    get_selected_hunk_hash_file_path().unlink(missing_ok=True)
    get_line_changes_json_file_path().unlink(missing_ok=True)
    get_index_snapshot_file_path().unlink(missing_ok=True)
    get_working_tree_snapshot_file_path().unlink(missing_ok=True)
    get_processed_include_ids_file_path().unlink(missing_ok=True)


def fetch_next_change() -> Optional[LineLevelChange]:
    """Find the next hunk that isn't blocked and cache it as selected.

    Returns:
        LineLevelChange for the hunk if found, None otherwise
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
            line_changes = build_line_changes_from_patch_text(patch_text)
            if line_changes.path in blocked_files:
                continue

            write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)

            write_text_file_contents(get_line_changes_json_file_path(),
                                     json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                                ensure_ascii=False, indent=0))
            write_snapshots_for_selected_file_path(line_changes.path)

            return line_changes
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        pass

    return None


def advance_to_next_change() -> None:
    """Clear selected hunk state and advance to the next unblocked hunk."""
    clear_selected_change_state_files()
    fetch_next_change()


def show_selected_change() -> None:
    """Display the currently cached hunk.

    This is a helper for commands that need to display the cached hunk
    without advancing (e.g., start, again).
    """
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        line_changes = build_line_changes_from_patch_text(patch_text)
        print_line_level_changes(line_changes)


def advance_to_and_show_next_change() -> None:
    """Advance to next hunk and display it (CLI workflow helper).

    This is a convenience wrapper for CLI commands that combines advancing
    to the next hunk with displaying it. If no more hunks exist, prints
    a message to stderr.
    """
    advance_to_next_change()

    # Check if a hunk was cached
    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        patch_text = read_text_file_contents(patch_path)
        line_changes = build_line_changes_from_patch_text(patch_text)
        print_line_level_changes(line_changes)
    else:
        print(_("No more hunks to process."), file=sys.stderr)


def get_selected_change_file_path() -> Optional[str]:
    """Get the file path of the currently cached hunk.

    Returns:
        Repository-relative path if a hunk is selected, None otherwise
    """
    json_path = get_line_changes_json_file_path()
    if not json_path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(json_path))
        return data.get("path")
    except Exception:
        return None


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

    # Get selected file content from index
    try:
        result = run_git_command(["show", f":{file_path}"], check=False)
        if result.returncode != 0:
            # File not in index (was deleted, or never added)
            selected_index_content = ""
        else:
            selected_index_content = result.stdout
    except Exception:
        return True  # Error reading means state is stale

    # Get selected file content from working tree
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / file_path
    try:
        selected_worktree_content = read_text_file_contents(file_full_path)
    except Exception:
        return True  # Error reading means state is stale

    # Compare snapshots with selected state
    return (cached_index_content != selected_index_content or
            cached_worktree_content != selected_worktree_content)


def require_selected_hunk() -> None:
    """Ensure selected hunk exists and is not stale, exit with error otherwise."""
    if not get_selected_hunk_patch_file_path().exists():
        exit_with_error(_("No selected hunk. Run 'start' first."))

    if get_line_changes_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_line_changes_json_file_path()))
        file_path = data["path"]
        if snapshots_are_stale(file_path):
            clear_selected_change_state_files()
            exit_with_error(_("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."))


def recalculate_selected_hunk_for_file(file_path: str) -> None:
    """Recalculate the selected hunk for a specific file after modifications.

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

            # Cache this hunk as selected
            write_text_file_contents(get_selected_hunk_patch_file_path(), patch_text)
            write_text_file_contents(get_selected_hunk_hash_file_path(), hunk_hash)

            line_changes = build_line_changes_from_patch_text(patch_text)
            write_text_file_contents(get_line_changes_json_file_path(),
                                    json.dumps(convert_line_changes_to_serializable_dict(line_changes),
                                              ensure_ascii=False, indent=0))
            write_snapshots_for_selected_file_path(line_changes.path)

            print_line_level_changes(line_changes)
            return
    except subprocess.CalledProcessError:
        # Git diff failed (e.g., no changes)
        clear_selected_change_state_files()
        print(_("No pending hunks."), file=sys.stderr)
        return

    # No more hunks for this file, advance to next file
    clear_selected_change_state_files()
    # Import here to avoid circular dependency
    from ..commands.show import command_show
    command_show()


def record_hunk_included(hunk_hash: str) -> None:
    """Record that a hunk was included (staged)."""
    included_path = get_included_hunks_file_path()
    content = read_text_file_contents(included_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(included_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_discarded(hunk_hash: str) -> None:
    """Record that a hunk was discarded (removed from working tree)."""
    discarded_path = get_discarded_hunks_file_path()
    content = read_text_file_contents(discarded_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(discarded_path, "\n".join(sorted(existing)) + "\n" if existing else "")


def record_hunk_skipped(line_changes: LineLevelChange, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display.

    Args:
        line_changes: Current hunk's lines
        hunk_hash: SHA-1 hash of the hunk
    """
    # Extract first changed line number for display
    first_changed_line = None
    for entry in line_changes.lines:
        if entry.kind != " ":  # Not context
            first_changed_line = entry.old_line_number or entry.new_line_number
            break

    # Build metadata object
    metadata = {
        "hash": hunk_hash,
        "file": line_changes.path,
        "line": first_changed_line or 0,
        "ids": line_changes.changed_line_ids()
    }

    # Append to JSONL file
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metadata) + "\n")


def format_id_range(ids: list[int]) -> str:
    """Format list of IDs as compact range string (e.g., '1-5,7,9-11').

    Args:
        ids: List of integer IDs

    Returns:
        Compact range string
    """
    if not ids:
        return ""

    ids = sorted(ids)
    ranges = []
    start = ids[0]
    end = ids[0]

    for i in range(1, len(ids)):
        if ids[i] == end + 1:
            end = ids[i]
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = end = ids[i]

    # Add final range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ",".join(ranges)
