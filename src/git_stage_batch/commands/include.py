"""Include command implementation."""

from __future__ import annotations

import subprocess

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..data.hunk_tracking import (
    advance_to_next_hunk,
    recalculate_current_hunk_for_file,
    require_current_hunk_and_check_stale,
)
from ..data.line_state import load_current_lines_from_state
from ..i18n import _, ngettext
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..staging.operations import build_target_index_content_with_selected_lines, update_index_with_blob_content
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
)


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the current hunk."""
    require_git_repository()
    ensure_state_directory_exists()

    # Load blocklist to skip already-processed hunks
    blocklist_path = get_block_list_file_path()
    if blocklist_path.exists():
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())
    else:
        blocked_hashes = set()

    # Stream diff and find first unblocked hunk
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash in blocked_hashes:
            continue

        # Extract filename for user feedback
        filename = patch.new_path if patch.new_path else "unknown"

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
            print(_("Failed to apply hunk: {}").format(e.stderr))
            return

        # Add hash to blocklist
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk staged from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))

    advance_to_next_hunk(quiet=quiet)


def command_include_file() -> None:
    """Include (stage) all hunks from the current file."""
    require_git_repository()
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
        print(_("No changes to stage."))
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
            print(_("Failed to apply hunk: {error}").format(error=e.stderr))
            break

    if hunks_staged == 0:
        print(_("No hunks staged from {file}").format(file=target_file))
        return

    # Print summary message
    msg = ngettext(
        "✓ Staged {count} hunk from {file}",
        "✓ Staged {count} hunks from {file}",
        hunks_staged
    ).format(count=hunks_staged, file=target_file)
    print(msg)

    # Advance to next file's hunk
    advance_to_next_hunk(quiet=True)


def command_include_line(line_id_specification: str) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
    combined_include_ids = already_included_ids | set(requested_ids)

    current_lines = load_current_lines_from_state()

    # Get base content from index snapshot (captured when hunk was loaded)
    base_text = read_text_file_contents(get_index_snapshot_file_path())

    target_index_content = build_target_index_content_with_selected_lines(current_lines, combined_include_ids, base_text)
    update_index_with_blob_content(current_lines.path, target_index_content)

    # Update processed include IDs
    write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)

    # After modifying index, recalculate hunk for the SAME file
    recalculate_current_hunk_for_file(current_lines.path)

    print(_("✓ Included line(s): {lines}").format(lines=line_id_specification))
