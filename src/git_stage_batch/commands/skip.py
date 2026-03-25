"""Skip command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import build_current_lines_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..data.hunk_tracking import advance_to_next_hunk
from ..i18n import _, ngettext
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
)


def command_skip(*, quiet: bool = False) -> None:
    """Skip the current hunk without staging it."""
    from ..data.hunk_tracking import find_and_cache_next_unblocked_hunk

    require_git_repository()
    ensure_state_directory_exists()

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if find_and_cache_next_unblocked_hunk(quiet=quiet) is None:
        return

    # Read cached hunk
    patch_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    patch_text = read_text_file_contents(get_current_hunk_patch_file_path())

    # Extract filename for user feedback
    current_lines = build_current_lines_from_patch_text(patch_text)
    filename = current_lines.path

    # Add hash to blocklist (without staging)
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    if not quiet:
        print(_("✓ Hunk skipped from {file}").format(file=filename), file=sys.stderr)

    advance_to_next_hunk(quiet=quiet)


def command_skip_file() -> None:
    """Skip all remaining hunks from the current file."""
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

    advance_to_next_hunk()
