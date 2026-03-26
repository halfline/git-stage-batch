"""Skip command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import build_line_changes_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..data.hunk_tracking import advance_to_and_show_next_change, advance_to_next_change, record_hunk_skipped, require_selected_hunk
from ..data.session import require_session_started
from ..i18n import _, ngettext
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_processed_skip_ids_file_path,
)


def command_skip(*, quiet: bool = False) -> None:
    """Skip the selected hunk without staging it."""
    from ..data.hunk_tracking import fetch_next_change

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if fetch_next_change() is None:
        if not quiet:
            print(_("No more hunks to process."), file=sys.stderr)
        return

    # Read cached hunk
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    patch_text = read_text_file_contents(get_selected_hunk_patch_file_path())

    # Extract filename for user feedback
    line_changes = build_line_changes_from_patch_text(patch_text)
    filename = line_changes.path

    # Add hash to blocklist (without staging)
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    # Record for progress tracking
    record_hunk_skipped(line_changes, patch_hash)

    if not quiet:
        print(_("✓ Hunk skipped from {file}").format(file=filename), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def command_skip_file() -> None:
    """Skip all remaining hunks from the selected file."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Find first non-blocked hunk to get the target file
    def is_unblocked(patch_text: str) -> bool:
        return compute_stable_hunk_hash(patch_text) not in blocked_hashes

    target_file = get_first_matching_file_from_diff(
        context_lines=get_context_lines(),
        predicate=is_unblocked
    )

    if target_file is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Stream through hunks and skip all from target file
    hunks_skipped = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        # Skip if already blocked
        if patch_hash in blocked_hashes:
            continue

        # Add to blocklist without staging
        append_lines_to_file(blocklist_path, [patch_hash])
        blocked_hashes.add(patch_hash)
        hunks_skipped += 1

    msg = ngettext(
        "✓ Skipped {count} hunk from {file}",
        "✓ Skipped {count} hunks from {file}",
        hunks_skipped
    ).format(count=hunks_skipped, file=target_file)
    print(msg, file=sys.stderr)

    advance_to_and_show_next_change()


def command_skip_line(line_id_specification: str) -> None:
    """Mark only the specified lines as skipped.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    require_selected_hunk()

    requested_ids = parse_line_selection(line_id_specification)
    already_skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    combined_skip_ids = already_skipped_ids | set(requested_ids)

    # Update processed skip IDs
    write_line_ids_file(get_processed_skip_ids_file_path(), combined_skip_ids)

    print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
