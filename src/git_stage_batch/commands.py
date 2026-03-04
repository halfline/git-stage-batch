"""Command implementations for the git-stage-batch tool."""

from __future__ import annotations

import json
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
    get_auto_added_files_file_path,
    get_blocked_files_file_path,
    get_block_list_file_path,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_git_repository_root_path,
    get_index_snapshot_file_path,
    get_processed_exclude_ids_file_path,
    get_processed_include_ids_file_path,
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
    excluded_ids = set(read_line_ids_file(get_processed_exclude_ids_file_path()))
    remaining = sorted(all_changed_ids - included_ids - excluded_ids)
    return remaining


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

    for single_hunk in single_hunk_patches:
        patch_text = single_hunk.to_patch_text()
        hunk_hash = compute_stable_hunk_hash(patch_text)
        if is_hunk_hash_in_block_list(hunk_hash):
            continue

        write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
        write_text_file_contents(get_current_hunk_hash_file_path(), hunk_hash)

        current_lines = build_current_lines_from_patch_text(patch_text)
        write_text_file_contents(get_current_lines_json_file_path(),
                                 json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                            ensure_ascii=False, indent=0))
        write_snapshots_for_current_file_path(current_lines.path)

        print_annotated_hunk_with_aligned_gutter(current_lines)
        return True

    print("No pending hunks.", file=sys.stderr)
    return False


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

    ensure_state_directory_exists()
    clear_current_hunk_state_files()
    if not find_and_cache_next_unblocked_hunk():
        sys.exit(2)


def command_show() -> None:
    """Display the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists() or not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")
    print_annotated_hunk_with_aligned_gutter(load_current_lines_from_state())


def command_include() -> None:
    """Stage the entire current hunk to the index."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk to include. Run 'start' first.")
    try:
        run_git_command(["apply", "--cached", "--index", str(get_current_hunk_patch_file_path())])
    except Exception as error:
        stderr = getattr(error, 'stderr', '').strip() if hasattr(error, 'stderr') else ''
        stdout = getattr(error, 'stdout', '').strip() if hasattr(error, 'stdout') else ''
        exit_with_error(f"Failed to apply hunk: {stderr or stdout or 'git apply failed.'}")
    append_current_hunk_hash_to_block_list()
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_exclude() -> None:
    """Skip the current hunk (mark as excluded)."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_hash_file_path().exists():
        exit_with_error("No current hunk to exclude. Run 'start' first.")
    append_current_hunk_hash_to_block_list()
    clear_current_hunk_state_files()
    find_and_cache_next_unblocked_hunk()


def command_discard() -> None:
    """Reverse-apply the current hunk to the working tree."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_hunk_patch_file_path().exists():
        exit_with_error("No current hunk to discard. Run 'start' first.")
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
    base_text = read_text_file_contents(get_index_snapshot_file_path())
    target_index_content = build_target_index_content_with_selected_lines(current_lines, combined_include_ids, base_text)
    update_index_with_blob_content(current_lines.path, target_index_content)

    write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)
    advance_if_hunk_complete_else_show()


def command_exclude_line(line_id_specification: str) -> None:
    """Mark the specified lines as excluded (skip them)."""
    require_git_repository()
    ensure_state_directory_exists()
    if not get_current_lines_json_file_path().exists():
        exit_with_error("No current hunk. Run 'start' first.")
    requested_ids = parse_line_id_specification(line_id_specification)
    existing_excluded_ids = set(read_line_ids_file(get_processed_exclude_ids_file_path()))
    existing_excluded_ids |= set(requested_ids)
    write_line_ids_file(get_processed_exclude_ids_file_path(), existing_excluded_ids)
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

    new_working_text = build_target_working_tree_content_with_discarded_lines(current_lines, discard_ids, working_text)
    write_text_file_contents(absolute_path, new_working_text)

    existing_excluded_ids = set(read_line_ids_file(get_processed_exclude_ids_file_path()))
    existing_excluded_ids |= discard_ids
    write_line_ids_file(get_processed_exclude_ids_file_path(), existing_excluded_ids)
    advance_if_hunk_complete_else_show()


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


def command_exclude_file() -> None:
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
    try:
        for path in get_state_directory_path().glob("*"):
            path.unlink(missing_ok=True)
        get_state_directory_path().rmdir()
    except Exception:
        pass
    ensure_state_directory_exists()
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


def command_status() -> None:
    """Show current state summary."""
    require_git_repository()
    if get_current_hunk_patch_file_path().exists():
        print("current:",
              summarize_current_hunk_header_line(read_text_file_contents(get_current_hunk_patch_file_path())))
        remaining = compute_remaining_changed_line_ids()
        if remaining:
            print("remaining lines:", ",".join(map(str, remaining)))
        else:
            print("remaining lines: 0")
    else:
        print("current: none")
    block_list_lines = read_text_file_contents(get_block_list_file_path()).splitlines() if get_block_list_file_path().exists() else []
    print(f"blocked: {len([x for x in block_list_lines if x.strip()])}")
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
