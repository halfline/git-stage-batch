"""Skip command implementation."""

from __future__ import annotations

from ..core.hashing import compute_stable_hunk_hash
from ..core.diff_parser import get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..data.hunk_tracking import advance_to_next_hunk
from ..i18n import _, ngettext
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
)


def command_skip(*, quiet: bool = False) -> None:
    """Skip the current hunk without staging it."""
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

        # Add hash to blocklist (without staging)
        append_lines_to_file(blocklist_path, [patch_hash])

        if not quiet:
            print(_("✓ Hunk skipped from {}").format(filename))
        break

    if not quiet:
        print(_("No more hunks to process."))

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
        print(_("No changes to process."))
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
    print(msg)

    advance_to_next_hunk()
