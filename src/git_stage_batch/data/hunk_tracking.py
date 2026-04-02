"""Hunk navigation, state management, staleness detection, and progress tracking."""

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
    get_batched_hunks_file_path,
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_index_snapshot_file_path,
    get_processed_batch_ids_file_path,
    get_processed_include_ids_file_path,
    get_skipped_hunks_jsonl_file_path,
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
    get_processed_batch_ids_file_path().unlink(missing_ok=True)


def apply_line_level_batch_filter_to_cached_hunk() -> bool:
    """Filter cached hunk to exclude batched lines.

    Returns:
        True if hunk should be skipped (all lines filtered), False otherwise
    """
    from dataclasses import replace
    from ..core.line_selection import read_line_ids_file
    from .line_state import load_current_lines_from_state, convert_current_lines_to_serializable_dict

    current_lines = load_current_lines_from_state()
    if current_lines is None:
        return True

    batched_ids = set(read_line_ids_file(get_processed_batch_ids_file_path()))
    if not batched_ids:
        return False  # No filtering needed

    # Filter out batched lines and renumber
    filtered_lines = []
    new_id = 1
    for line_entry in current_lines.lines:
        if line_entry.id in batched_ids:
            continue  # Skip batched lines
        # Create new line with renumbered ID
        filtered_lines.append(replace(line_entry, id=new_id if line_entry.kind != " " else 0))
        if line_entry.kind != " ":
            new_id += 1

    # If all lines were batched, skip this hunk
    if not any(line.kind in ("+", "-") for line in filtered_lines):
        return True

    # Create filtered CurrentLines
    filtered_current_lines = CurrentLines(
        path=current_lines.path,
        header=current_lines.header,
        lines=filtered_lines
    )

    # Update cached hunk with filtered version
    write_text_file_contents(get_current_lines_json_file_path(),
                            json.dumps(convert_current_lines_to_serializable_dict(filtered_current_lines),
                                      ensure_ascii=False, indent=0))

    return False


def find_and_cache_next_unblocked_hunk() -> Optional[CurrentLines]:
    """Find the next hunk that isn't blocked and cache it as current.

    Returns:
        CurrentLines for the hunk if found, None otherwise
    """
    # Get list of blocked files
    blocked_files = set(read_file_paths_file(get_blocked_files_file_path()))

    # Load blocklist (includes current iteration)
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines())

    # Load batched hunks (permanent, survives 'again')
    batched_content = read_text_file_contents(get_batched_hunks_file_path())
    batched_hashes = set(batched_content.splitlines()) if batched_content else set()
    blocked_hashes.update(batched_hashes)

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

            # Apply line-level batch filtering
            if apply_line_level_batch_filter_to_cached_hunk():
                # All lines were batched, skip this hunk and continue
                clear_current_hunk_state_files()
                continue

            # Return filtered hunk (or original if no filtering applied)
            from .line_state import load_current_lines_from_state
            return load_current_lines_from_state()
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


def record_hunk_skipped(current_lines: CurrentLines, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display.

    Args:
        current_lines: Current hunk's lines
        hunk_hash: SHA-1 hash of the hunk
    """
    # Extract first changed line number for display
    first_changed_line = None
    for entry in current_lines.lines:
        if entry.kind != " ":  # Not context
            first_changed_line = entry.old_line_number or entry.new_line_number
            break

    # Build metadata object
    metadata = {
        "hash": hunk_hash,
        "file": current_lines.path,
        "line": first_changed_line or 0,
        "ids": current_lines.changed_line_ids()
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
