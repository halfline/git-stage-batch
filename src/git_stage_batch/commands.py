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
    append_lines_to_file,
    clear_current_hunk_state_files,
    ensure_state_directory_exists,
    exit_with_error,
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
    read_text_file_contents,
    require_git_repository,
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


def find_and_cache_next_unblocked_hunk() -> bool:
    """Find the next hunk that isn't blocked and cache it as current."""
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


def command_again() -> None:
    """Clear all state and start fresh."""
    require_git_repository()
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
