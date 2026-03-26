"""Include command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.diff_parser import build_line_changes_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..data.hunk_tracking import (
    advance_to_and_show_next_change,
    advance_to_next_change,
    recalculate_selected_hunk_for_file,
    record_hunk_included,
    require_selected_hunk,
)
from ..data.line_state import load_line_changes_from_state
from ..data.session import require_session_started
from ..i18n import _, ngettext
from ..staging.operations import build_target_index_content_with_selected_lines, update_index_with_blob_content
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
)


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the selected hunk."""
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
        print(_("Failed to apply hunk: {}").format(e.stderr), file=sys.stderr)
        return

    # Add hash to blocklist
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    # Record for progress tracking
    record_hunk_included(patch_hash)

    if not quiet:
        print(_("✓ Hunk staged from {file}").format(file=filename), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def command_include_file() -> None:
    """Include (stage) all hunks from the selected file."""
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
        print(_("No changes to stage."), file=sys.stderr)
        return

    # Stream through hunks and stage all from target file
    hunks_staged = 0
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        # Skip if already blocked
        if patch_hash in blocked_hashes:
            continue

        # Apply the hunk to the index
        try:
            subprocess.run(
                ["git", "apply", "--cached"],
                input=patch_text,
                text=True,
                check=True,
                capture_output=True,
            )
            # Add to blocklist so we don't try to stage it again
            append_lines_to_file(blocklist_path, [patch_hash])
            blocked_hashes.add(patch_hash)
            hunks_staged += 1
        except subprocess.CalledProcessError as e:
            print(_("Failed to apply hunk: {error}").format(error=e.stderr), file=sys.stderr)
            break

    if hunks_staged == 0:
        print(_("No hunks staged from {file}").format(file=target_file), file=sys.stderr)
        return

    # Print summary message
    msg = ngettext(
        "✓ Staged {count} hunk from {file}",
        "✓ Staged {count} hunks from {file}",
        hunks_staged
    ).format(count=hunks_staged, file=target_file)
    print(msg, file=sys.stderr)

    # Advance to next file's hunk
    advance_to_and_show_next_change()


def command_include_line(line_id_specification: str) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    require_selected_hunk()

    requested_ids = parse_line_selection(line_id_specification)
    already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    combined_include_ids = already_included_ids | set(requested_ids)

    line_changes = load_line_changes_from_state()

    # Get base content from index snapshot (captured when hunk was loaded)
    base_text = read_text_file_contents(get_index_snapshot_file_path())

    target_index_content = build_target_index_content_with_selected_lines(line_changes, combined_include_ids, base_text)
    update_index_with_blob_content(line_changes.path, target_index_content)

    # Update processed include IDs
    write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)

    # After modifying index, recalculate hunk for the SAME file
    recalculate_selected_hunk_for_file(line_changes.path)

    print(_("✓ Included line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)
