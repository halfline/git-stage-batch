"""Skip command implementation."""

from __future__ import annotations

import sys

from ..core.diff_parser import build_current_lines_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..data.hunk_tracking import advance_to_next_hunk, record_hunk_skipped, require_current_hunk_and_check_stale
from ..i18n import _, ngettext
from ..utils.file_io import append_lines_to_file, read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_processed_skip_ids_file_path,
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

    # Record for progress tracking
    record_hunk_skipped(current_lines, patch_hash)

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


def command_skip_line(line_id_specification: str) -> None:
    """Mark only the specified lines as skipped.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    already_skipped_ids = set(read_line_ids_file(get_processed_skip_ids_file_path()))
    combined_skip_ids = already_skipped_ids | set(requested_ids)

    # Update processed skip IDs
    write_line_ids_file(get_processed_skip_ids_file_path(), combined_skip_ids)

    print(_("✓ Skipped line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)


def command_skip_to_batch(batch_name: str, line_ids: str | None = None, file_only: bool = False) -> None:
    """Save current changes to batch instead of just skipping."""
    from ..batch import add_file_to_batch, create_batch
    from ..batch.validation import batch_exists
    from ..core.hashing import compute_stable_hunk_hash
    from ..core.diff_parser import parse_unified_diff_streaming
    from ..data.hunk_tracking import record_hunk_skipped
    from ..data.line_state import convert_current_lines_to_serializable_dict
    from ..core.diff_parser import build_current_lines_from_patch_text, write_snapshots_for_current_file_path
    from ..utils.git import get_git_repository_root_path, run_git_command
    from ..utils.paths import (
        get_current_hunk_hash_file_path,
        get_current_hunk_patch_file_path,
        get_current_lines_json_file_path,
    )
    import json

    require_git_repository()
    ensure_state_directory_exists()

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff to find first non-blocked hunk
    current_patch = None
    current_hash = None
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)
        if patch_hash not in blocked_hashes:
            current_patch = patch
            current_hash = patch_hash
            break

    if current_patch is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Cache this hunk as current
    patch_text = current_patch.to_patch_text()
    write_text_file_contents(get_current_hunk_patch_file_path(), patch_text)
    write_text_file_contents(get_current_hunk_hash_file_path(), current_hash)

    # Cache CurrentLines state for progress tracking
    current_lines = build_current_lines_from_patch_text(patch_text)
    write_text_file_contents(get_current_lines_json_file_path(),
                            json.dumps(convert_current_lines_to_serializable_dict(current_lines),
                                      ensure_ascii=False, indent=0))

    # Save snapshots for staleness detection
    write_snapshots_for_current_file_path(current_lines.path)

    # Get the file path and read its current content from working tree
    file_path = current_patch.new_path
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    # Read current file content
    if full_path.exists():
        content = full_path.read_text(encoding="utf-8", errors="surrogateescape")
    else:
        # File deleted - save empty content
        content = ""

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", file_path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        # Format: <mode> <hash> <stage>\t<path>
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Save to batch
    add_file_to_batch(batch_name, file_path, content, file_mode)

    # Add hash to blocklist (mark as processed)
    append_lines_to_file(blocklist_path, [current_hash])

    # Record hunk as skipped for progress tracking
    record_hunk_skipped(current_lines, current_hash)

    print(_("✓ Hunk saved to batch '{name}' and skipped").format(name=batch_name), file=sys.stderr)
