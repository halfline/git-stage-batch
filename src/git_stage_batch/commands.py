"""Command implementations for the git-stage-batch tool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

from .display import Colors, print_annotated_hunk_with_aligned_gutter
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
    get_context_lines,
    get_context_lines_file_path,
    get_current_hunk_hash_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_iteration_count_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_git_repository_root_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_start_head_file_path,
    get_start_index_tree_file_path,
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
    diff_text = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"], check=False).stdout

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
    diff_text = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"], check=False).stdout
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


# --------------------------- Progress tracking ---------------------------

def get_iteration_count() -> int:
    """Get current iteration count, defaulting to 1."""
    count_path = get_iteration_count_file_path()
    if not count_path.exists():
        return 1
    try:
        return int(read_text_file_contents(count_path).strip())
    except ValueError:
        return 1

def increment_iteration_count() -> None:
    """Increment the iteration counter."""
    current = get_iteration_count()
    write_text_file_contents(get_iteration_count_file_path(), str(current + 1))

def record_hunk_included(hunk_hash: str) -> None:
    """Record that a hunk was included (staged)."""
    included_path = get_included_hunks_file_path()
    content = read_text_file_contents(included_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(included_path, "\n".join(sorted(existing)) + "\n" if existing else "")

def record_hunk_skipped(current_lines: CurrentLines, hunk_hash: str) -> None:
    """Record that a hunk was skipped with metadata for display."""
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

def record_hunk_discarded(hunk_hash: str) -> None:
    """Record that a hunk was discarded (removed from working tree)."""
    discarded_path = get_discarded_hunks_file_path()
    content = read_text_file_contents(discarded_path)
    existing = set(content.splitlines()) if content else set()
    existing.add(hunk_hash)
    write_text_file_contents(discarded_path, "\n".join(sorted(existing)) + "\n" if existing else "")

def format_id_range(ids: list[int]) -> str:
    """Format list of IDs as compact range string (e.g., '1-5,7,9-11')."""
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


# --------------------------- Command handlers ---------------------------

def command_start(unified: int = 3) -> None:
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

    # Save context lines for this session
    write_text_file_contents(get_context_lines_file_path(), str(unified))

    # Initialize iteration counter
    write_text_file_contents(get_iteration_count_file_path(), "1")

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

    # Record hunk as included for progress tracking
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    record_hunk_included(hunk_hash)

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

    # Record hunk as skipped for progress tracking
    current_lines = load_current_lines_from_state()
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    record_hunk_skipped(current_lines, hunk_hash)

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

    # After reverse-applying a new file, delete it if it became empty
    # (git apply -R on new files empties them but doesn't delete them)
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]

        # Check if this was a new file by looking at the patch header
        patch_content = read_text_file_contents(get_current_hunk_patch_file_path())
        is_new_file = "--- /dev/null" in patch_content

        if is_new_file:
            absolute_path = get_git_repository_root_path() / file_path
            if absolute_path.exists():
                content = read_text_file_contents(absolute_path)
                if not content.strip():  # File is empty
                    absolute_path.unlink()

    # Record hunk as discarded for progress tracking
    hunk_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    record_hunk_discarded(hunk_hash)

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
    target_file_path = current_lines.path

    # Repeatedly include hunks while we're still on the same file
    while True:
        # Check if we still have a current hunk
        if not get_current_hunk_patch_file_path().exists():
            break

        # Check if current hunk is still from the target file
        current_lines = load_current_lines_from_state()
        if current_lines.path != target_file_path:
            break

        # Include this hunk (handles bookkeeping, invalidation, and advancing)
        command_include()


def command_skip_file() -> None:
    """Skip all remaining hunks from the current file."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    # Get current file path
    current_lines = load_current_lines_from_state()
    target_file_path = current_lines.path

    # Repeatedly skip hunks while we're still on the same file
    while True:
        # Check if we still have a current hunk
        if not get_current_hunk_patch_file_path().exists():
            break

        # Check if current hunk is still from the target file
        current_lines = load_current_lines_from_state()
        if current_lines.path != target_file_path:
            break

        # Skip this hunk (handles bookkeeping, invalidation, and advancing)
        command_skip()


def command_again() -> None:
    """Clear all state and start fresh."""
    require_git_repository()
    # Reset auto-added files before clearing state
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    # Save persistent state before clearing
    blocked_files = []
    if get_blocked_files_file_path().exists():
        blocked_files = read_file_paths_file(get_blocked_files_file_path())

    # Increment iteration counter before clearing
    next_iteration = get_iteration_count() + 1

    # Clear all state
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    ensure_state_directory_exists()

    # Restore persistent state
    if blocked_files:
        write_file_paths_file(get_blocked_files_file_path(), blocked_files)

    # Restore incremented iteration count
    write_text_file_contents(get_iteration_count_file_path(), str(next_iteration))

    find_and_cache_next_unblocked_hunk()


def command_stop() -> None:
    """Clear all state."""
    require_git_repository()
    # Reset auto-added files before clearing state
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
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
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)

    print("✓ Session aborted. All changes reverted.", file=sys.stderr)


def estimate_remaining_hunks() -> int:
    """Estimate number of remaining unprocessed hunks."""
    # Run git diff and count hunks
    auto_add_untracked_files()
    diff_text = run_git_command(["diff", f"-U{get_context_lines()}", "--no-color"], check=False).stdout

    if not diff_text.strip():
        return 0

    # Parse to count total hunks
    single_hunk_patches = parse_unified_diff_into_single_hunk_patches(diff_text)

    # Filter out blocked hunks
    blocklist_content = read_text_file_contents(get_block_list_file_path())
    blocked_hashes = set(blocklist_content.splitlines()) if blocklist_content else set()

    # Filter out hunks from blocked files
    blocked_files = read_file_paths_file(get_blocked_files_file_path())

    remaining = 0
    for patch in single_hunk_patches:
        hunk_hash = compute_stable_hunk_hash(patch.to_patch_text())
        file_path = patch.old_path if patch.old_path != "/dev/null" else patch.new_path
        file_path = file_path.removeprefix("a/").removeprefix("b/")

        if hunk_hash not in blocked_hashes and file_path not in blocked_files:
            remaining += 1

    return remaining


def command_status(porcelain: bool = False) -> None:
    """Show session progress and current state."""
    require_git_repository()
    ensure_state_directory_exists()

    # Gather metrics
    iteration = get_iteration_count()

    # Count processed hunks this iteration
    included_content = read_text_file_contents(get_included_hunks_file_path())
    included_count = len([h for h in included_content.splitlines() if h.strip()])

    discarded_content = read_text_file_contents(get_discarded_hunks_file_path())
    discarded_count = len([h for h in discarded_content.splitlines() if h.strip()])

    # Parse skipped hunks JSONL
    skipped_hunks = []
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    if jsonl_path.exists():
        for line in read_text_file_contents(jsonl_path).splitlines():
            if line.strip():
                try:
                    skipped_hunks.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines

    # Check for current hunk
    has_current = get_current_hunk_patch_file_path().exists()
    current_summary = None
    if has_current:
        if get_current_lines_json_file_path().exists():
            data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
            file_path = data["path"]
            if _snapshots_are_stale(file_path):
                clear_current_hunk_state_files()
                has_current = False
            else:
                current_lines = load_current_lines_from_state()
                current_summary = {
                    "file": current_lines.path,
                    "line": current_lines.header.old_start,
                    "ids": current_lines.changed_line_ids()
                }

    # Estimate remaining hunks
    remaining_estimate = estimate_remaining_hunks()

    if porcelain:
        # JSON output
        output = {
            "session": {
                "iteration": iteration,
                "in_progress": has_current
            },
            "current": current_summary,
            "progress": {
                "included": included_count,
                "skipped": len(skipped_hunks),
                "discarded": discarded_count,
                "remaining": remaining_estimate
            },
            "skipped_hunks": skipped_hunks
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable progress report
        status = "in progress" if has_current else "complete"
        print(f"Session: iteration {iteration} ({status})")
        print()

        if current_summary:
            ids_str = format_id_range(current_summary["ids"])
            print(f"Current hunk:")
            print(f"  {current_summary['file']}:{current_summary['line']}")
            print(f"  [#{ids_str}]")
            print()

        print(f"Progress this iteration:")
        print(f"  Included:  {included_count} hunks")
        print(f"  Skipped:   {len(skipped_hunks)} hunks")
        print(f"  Discarded: {discarded_count} hunks")
        print(f"  Remaining: ~{remaining_estimate} hunks")

        if skipped_hunks:
            print()
            print("Skipped hunks:")
            for hunk in skipped_hunks:
                ids_str = format_id_range(hunk["ids"])
                print(f"  {hunk['file']}:{hunk['line']} [#{ids_str}]")


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


def prompt_quit_session() -> str:
    """Prompt user for what to do when quitting. Returns 'keep', 'undo', or 'cancel'."""
    use_color = Colors.enabled()
    print()

    try:
        while True:
            if use_color:
                response = input(f"{Colors.BOLD}Keep staged changes?{Colors.RESET} {Colors.BOLD}{Colors.GREEN}[y]{Colors.RESET}es / {Colors.BOLD}{Colors.RED}[n]{Colors.RESET}o: ").strip().lower()
            else:
                response = input("Keep staged changes? [y]es / [n]o: ").strip().lower()

            if response in ('y', 'yes'):
                return 'keep'
            elif response in ('n', 'no'):
                return 'undo'
            else:
                print(f"Invalid response: '{response}'")
    except (EOFError, KeyboardInterrupt):
        print()
        return 'cancel'


def print_interactive_help() -> None:
    """Print help for interactive mode."""
    use_color = Colors.enabled()

    if use_color:
        print(f"""
{Colors.BOLD}y{Colors.RESET} - yes, stage this hunk
{Colors.BOLD}n{Colors.RESET} - no, skip this hunk for now
{Colors.BOLD}d{Colors.RESET} - discard this hunk from working tree
{Colors.BOLD}q{Colors.RESET} - quit interactive mode
{Colors.BOLD}a{Colors.RESET} - stage this hunk and all remaining hunks
{Colors.BOLD}l{Colors.RESET} - interactively select lines from this hunk
{Colors.BOLD}f{Colors.RESET} - stage or skip all hunks in current file
{Colors.BOLD}b{Colors.RESET} - block current file (add to .gitignore)
{Colors.BOLD}?{Colors.RESET} - print help
""")
    else:
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
    use_color = Colors.enabled()
    current_lines = load_current_lines_from_state()
    changed_ids = current_lines.changed_line_ids()

    if not changed_ids:
        print("No changed lines in this hunk")
        return

    if use_color:
        print(f"{Colors.CYAN}Changed line IDs:{Colors.RESET} {','.join(map(str, changed_ids))}")
    else:
        print(f"Changed line IDs: {','.join(map(str, changed_ids))}")

    try:
        # Get action
        while True:
            if use_color:
                action = input(f"Action for lines {Colors.BOLD}{Colors.GREEN}[i]{Colors.RESET}nclude, {Colors.BOLD}[s]{Colors.RESET}kip, or {Colors.BOLD}{Colors.RED}[d]{Colors.RESET}iscard? ").strip().lower()
            else:
                action = input("Action for lines [i]nclude, [s]kip, or [d]iscard? ").strip().lower()

            if action in ('i', 's', 'd'):
                break
            print(f"Invalid action: '{action}'")

        # Get line IDs
        while True:
            if use_color:
                line_spec = input(f"{Colors.BOLD}Enter line IDs{Colors.RESET} (e.g., 1,3,5-7): ").strip()
            else:
                line_spec = input("Enter line IDs (e.g., 1,3,5-7): ").strip()

            if line_spec:
                break
            print("Line IDs required")

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
    use_color = Colors.enabled()
    current_lines = load_current_lines_from_state()

    try:
        while True:
            if use_color:
                action = input(f"Action for all hunks in {Colors.BOLD}{current_lines.path}{Colors.RESET} - {Colors.BOLD}{Colors.GREEN}[i]{Colors.RESET}nclude or {Colors.BOLD}[s]{Colors.RESET}kip? ").strip().lower()
            else:
                action = input(f"Action for all hunks in {current_lines.path} - [i]nclude or [s]kip? ").strip().lower()

            if action == 'i':
                command_include_file()
                break
            elif action == 's':
                command_skip_file()
                break
            else:
                print(f"Invalid action: '{action}'")
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
        # Initialize abort state for new session
        initialize_abort_state()
        # Record starting state for quit check
        start_head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
        start_index_tree = run_git_command(["write-tree"]).stdout.strip()
        write_text_file_contents(get_start_head_file_path(), start_head)
        write_text_file_contents(get_start_index_tree_file_path(), start_index_tree)
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
        # Show beginner-friendly prompt with colors
        use_color = Colors.enabled()
        print()

        if use_color:
            print(f"{Colors.BOLD}What do you want to do with this hunk?{Colors.RESET}")
            print(f"  {Colors.BOLD}{Colors.GREEN}[i]{Colors.RESET}nclude  - Stage this hunk to the index")
            print(f"  {Colors.BOLD}[s]{Colors.RESET}kip     - Skip this hunk for now")
            print(f"  {Colors.BOLD}{Colors.RED}[d]{Colors.RESET}iscard  - Remove this hunk from working tree (DESTRUCTIVE)")
            print(f"  {Colors.BOLD}[q]{Colors.RESET}uit     - Exit interactive mode")
            print()
            print(f"{Colors.CYAN}More options:{Colors.RESET} {Colors.BOLD}[a]{Colors.RESET}ll, {Colors.BOLD}[l]{Colors.RESET}ines, {Colors.BOLD}[f]{Colors.RESET}ile, {Colors.BOLD}[b]{Colors.RESET}lock, {Colors.BOLD}[?]{Colors.RESET}help")
        else:
            print("What do you want to do with this hunk?")
            print("  [i]nclude  - Stage this hunk to the index")
            print("  [s]kip     - Skip this hunk for now")
            print("  [d]iscard  - Remove this hunk from working tree (DESTRUCTIVE)")
            print("  [q]uit     - Exit interactive mode")
            print()
            print("More options: [a]ll, [l]ines, [f]ile, [b]lock, [?]help")

        print()

        try:
            if use_color:
                choice = input(f"{Colors.BOLD}Action:{Colors.RESET} ").strip().lower()
            else:
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
            # Quit interactive mode - check if the session made any changes
            # Compare current state against start state
            start_head = read_text_file_contents(get_start_head_file_path()).strip()
            start_index_tree = read_text_file_contents(get_start_index_tree_file_path()).strip()

            end_head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
            end_index_tree = run_git_command(["write-tree"]).stdout.strip()

            # Check if any files were discarded (snapshots created)
            snapshot_list = get_abort_snapshot_list_file_path()
            files_were_discarded = snapshot_list.exists() and bool(read_file_paths_file(snapshot_list))

            # If HEAD, index tree are unchanged, and no files were discarded, nothing is pending
            # (Note: working tree is expected to have changes - that's what we're reviewing)
            if start_head == end_head and start_index_tree == end_index_tree and not files_were_discarded:
                # Session made no changes, just clear and exit
                command_stop()
                break

            # Prompt for what to do with changes
            action = prompt_quit_session()
            if action == 'keep':
                command_stop()
                break
            elif action == 'undo':
                command_abort()
                break
            else:  # cancel
                print("Continuing interactive mode...")
                if get_current_hunk_patch_file_path().exists():
                    print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())
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


def command_suggest_fixup(boundary: str = "@{upstream}") -> None:
    """Suggest which commit the current hunk should be fixed up to.

    Analyzes the current hunk by looking at which commits modified the
    lines being changed (using git log -L), and suggests the most recent
    matching commit in the range boundary..HEAD.

    Args:
        boundary: Git ref to use as the lower bound for commit search
                 (default: @{upstream})
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Check for current hunk
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    # Check for stale state
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue.")

    # Load current hunk
    current_lines = load_current_lines_from_state()

    # Extract old line numbers, excluding distant context lines
    # Include: changed lines and context within 1 line of any change
    changed_line_indices = [i for i, entry in enumerate(current_lines.lines) if entry.kind != " "]

    if not changed_line_indices:
        exit_with_error("No changes found in hunk.")

    old_line_numbers = []
    for index, entry in enumerate(current_lines.lines):
        if entry.old_line_number is None:
            continue
        # Include if it's a changed line or within 1 line of a changed line
        if any(abs(index - changed_index) <= 1 for changed_index in changed_line_indices):
            old_line_numbers.append(entry.old_line_number)

    if not old_line_numbers:
        exit_with_error("No old line numbers found in hunk (this appears to be a new file addition).")

    # Get the range of old lines
    min_line = min(old_line_numbers)
    max_line = max(old_line_numbers)

    # Validate boundary ref
    try:
        run_git_command(["rev-parse", "--verify", boundary], check=True)
    except subprocess.CalledProcessError:
        exit_with_error(f"Invalid boundary ref: {boundary}")

    # Check if there are any commits in the range
    try:
        rev_list_result = run_git_command(
            ["rev-list", f"{boundary}..HEAD"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(f"Failed to get commit range {boundary}..HEAD")

    if not rev_list_result.stdout.strip():
        exit_with_error(f"No commits found in range {boundary}..HEAD")

    # Use git log -L to find commits that modified this line range
    # This directly gives us commits in boundary..HEAD that touched these lines,
    # already sorted newest-first
    try:
        log_result = run_git_command(
            ["log", "-L", f"{min_line},{max_line}:{current_lines.path}", f"{boundary}..HEAD", "--format=%H"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(f"Failed to run git log -L on {current_lines.path}")

    # Parse commits (already in reverse chronological order)
    commits = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]

    if not commits:
        print(f"No commits in range {boundary}..HEAD modified these lines.")
        print("The changes may be fixing code from before the boundary.")
        sys.exit(1)

    # First commit is the most recent
    suggested_commit = commits[0]

    # Display the suggestion
    try:
        show_result = run_git_command(
            ["show", "--no-patch", "--format=%h %s", suggested_commit],
            check=True
        )
        commit_info = show_result.stdout.strip()
    except subprocess.CalledProcessError:
        commit_info = suggested_commit[:7]

    print(f"Suggested fixup target: {commit_info}")
    print(f"Run: git commit --fixup={suggested_commit[:7]}")


def command_suggest_fixup_line(line_id_specification: str, boundary: str = "@{upstream}") -> None:
    """Suggest which commit specific lines should be fixed up to.

    Analyzes specific line IDs from the current hunk to find which commits
    modified those lines, and suggests the most recent matching commit in
    the range boundary..HEAD.

    Args:
        line_id_specification: Line IDs to analyze (e.g., "1,3,5-7")
        boundary: Git ref to use as the lower bound for commit search
                 (default: @{upstream})
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Check for current hunk
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")

    # Check for stale state
    if get_current_lines_json_file_path().exists():
        data = json.loads(read_text_file_contents(get_current_lines_json_file_path()))
        file_path = data["path"]
        if _snapshots_are_stale(file_path):
            clear_current_hunk_state_files()
            exit_with_error("Cached hunk is stale (file was changed). Run 'start' or 'again' to continue.")

    # Load current hunk
    current_lines = load_current_lines_from_state()

    # Parse the line IDs
    requested_ids = parse_line_id_specification(line_id_specification)

    # Extract old line numbers only for the specified line IDs
    old_line_numbers = []
    for entry in current_lines.lines:
        if entry.id in requested_ids and entry.old_line_number is not None:
            old_line_numbers.append(entry.old_line_number)

    if not old_line_numbers:
        exit_with_error("No old line numbers found for specified lines (they may be newly added lines).")

    # Get the range of old lines
    min_line = min(old_line_numbers)
    max_line = max(old_line_numbers)

    # Validate boundary ref
    try:
        run_git_command(["rev-parse", "--verify", boundary], check=True)
    except subprocess.CalledProcessError:
        exit_with_error(f"Invalid boundary ref: {boundary}")

    # Check if there are any commits in the range
    try:
        rev_list_result = run_git_command(
            ["rev-list", f"{boundary}..HEAD"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(f"Failed to get commit range {boundary}..HEAD")

    if not rev_list_result.stdout.strip():
        exit_with_error(f"No commits found in range {boundary}..HEAD")

    # Use git log -L to find commits that modified this line range
    # This directly gives us commits in boundary..HEAD that touched these lines,
    # already sorted newest-first
    try:
        log_result = run_git_command(
            ["log", "-L", f"{min_line},{max_line}:{current_lines.path}", f"{boundary}..HEAD", "--format=%H"],
            check=True
        )
    except subprocess.CalledProcessError:
        exit_with_error(f"Failed to run git log -L on {current_lines.path}")

    # Parse commits (already in reverse chronological order)
    commits = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]

    if not commits:
        print(f"No commits in range {boundary}..HEAD modified these lines.")
        print("The changes may be fixing code from before the boundary.")
        sys.exit(1)

    # First commit is the most recent
    suggested_commit = commits[0]

    # Display the suggestion
    try:
        show_result = run_git_command(
            ["show", "--no-patch", "--format=%h %s", suggested_commit],
            check=True
        )
        commit_info = show_result.stdout.strip()
    except subprocess.CalledProcessError:
        commit_info = suggested_commit[:7]

    print(f"Suggested fixup target: {commit_info}")
    print(f"Run: git commit --fixup={suggested_commit[:7]}")
