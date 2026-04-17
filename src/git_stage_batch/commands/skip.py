"""Skip command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import advance_to_and_show_next_change, advance_to_next_change, fetch_next_change, record_hunk_skipped, require_selected_hunk
from ..data.session import require_session_started
from ..exceptions import NoMoreHunks
from ..i18n import _, ngettext
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_processed_skip_ids_file_path,
)


def command_skip(*, quiet: bool = False) -> None:
    """Skip the selected hunk or binary file without staging it."""
    log_journal("command_skip_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Find and cache the next item
    try:
        item = fetch_next_change()
    except NoMoreHunks:
        if not quiet:
            print(_("No more hunks to process."), file=sys.stderr)
        return

    # Read cached hash
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

    # Handle based on item type
    if isinstance(item, BinaryFileChange):
        # Binary file - just add to blocklist
        file_path = item.new_path if item.new_path != "/dev/null" else item.old_path

        # Add hash to blocklist (without staging)
        blocklist_path = get_block_list_file_path()
        append_lines_to_file(blocklist_path, [patch_hash])

        # Binary files don't have line-level tracking, so skip record_hunk_skipped

        if not quiet:
            change_desc = "added" if item.is_new_file() else ("deleted" if item.is_deleted_file() else "modified")
            print(_("✓ Binary file {desc} skipped: {file}").format(desc=change_desc, file=file_path), file=sys.stderr)

        if quiet:
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()
        return

    # Text hunk - item is LineLevelChange here
    filename = item.path

    # Add hash to blocklist (without staging)
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    # Record for progress tracking
    record_hunk_skipped(item, patch_hash)

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
    def is_unblocked(patch_bytes: bytes) -> bool:
        return compute_stable_hunk_hash(patch_bytes) not in blocked_hashes

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

        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)

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
