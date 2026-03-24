"""Include command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.diff_parser import build_current_lines_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..data.hunk_tracking import (
    advance_to_and_show_next_hunk,
    advance_to_next_hunk,
    recalculate_current_hunk_for_file,
    record_hunk_included,
    require_current_hunk_and_check_stale,
)
from ..data.line_state import load_current_lines_from_state
from ..data.session import require_session_started
from ..i18n import _, ngettext
from ..staging.operations import build_target_index_content_with_selected_lines, update_index_with_blob_content
from ..utils.file_io import append_lines_to_file, read_text_file_contents
from ..utils.git import require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
)


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the current hunk."""
    from ..data.hunk_tracking import find_and_cache_next_unblocked_hunk

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if find_and_cache_next_unblocked_hunk() is None:
        if not quiet:
            print(_("No more hunks to process."), file=sys.stderr)
        return

    # Read cached hunk
    patch_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    patch_text = read_text_file_contents(get_current_hunk_patch_file_path())

    # Extract filename for user feedback
    current_lines = build_current_lines_from_patch_text(patch_text)
    filename = current_lines.path

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
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()


def command_include_file() -> None:
    """Include (stage) all hunks from the current file."""
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
    advance_to_and_show_next_hunk()


def command_include_line(line_id_specification: str) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
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

    print(_("✓ Included line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)


def command_include_to_batch(batch_name: str, line_ids: str | None = None, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save current changes to batch instead of staging."""
    require_git_repository()
    ensure_state_directory_exists()

    # Line-level batch operation
    if line_ids is not None:
        _command_include_lines_to_batch(batch_name, line_ids, quiet=quiet)
        return

    # Whole-hunk or file-level batch operation
    _command_include_hunk_to_batch(batch_name, file_only=file_only, quiet=quiet)


def _command_include_lines_to_batch(batch_name: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Save specific lines to batch (internal helper)."""
    from ..batch import add_file_to_batch, read_file_from_batch
    from ..batch.validation import batch_exists
    from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
    from ..data.hunk_tracking import recalculate_current_hunk_for_file, require_current_hunk_and_check_stale
    from ..data.line_state import load_current_lines_from_state
    from ..staging.operations import build_target_index_content_with_selected_lines
    from ..utils.file_io import read_text_file_contents
    from ..utils.git import run_git_command
    from ..utils.paths import get_index_snapshot_file_path, get_processed_batch_ids_file_path

    require_current_hunk_and_check_stale()

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        from ..batch import create_batch
        create_batch(batch_name, "Auto-created")

    requested_ids = parse_line_selection(line_id_specification)
    already_batched_ids = set(read_line_ids_file(get_processed_batch_ids_file_path()))
    combined_batch_ids = already_batched_ids | set(requested_ids)

    current_lines = load_current_lines_from_state()

    # Get base content: what's in batch, or index if not in batch yet
    base_text = read_file_from_batch(batch_name, current_lines.path)
    if base_text is None:
        # Not in batch yet, use index as base
        base_text = read_text_file_contents(get_index_snapshot_file_path())

    # Apply selected lines to create synthetic batch content
    target_batch_content = build_target_index_content_with_selected_lines(current_lines, combined_batch_ids, base_text)

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", current_lines.path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Save to batch
    add_file_to_batch(batch_name, current_lines.path, target_batch_content, file_mode)

    # Update batch's claimed line IDs
    from ..utils.paths import get_batch_claimed_line_ids_file_path
    write_line_ids_file(get_batch_claimed_line_ids_file_path(batch_name), combined_batch_ids)

    # Recompute global mask from all batch claims
    from ..batch.mask import recompute_global_batch_mask
    recompute_global_batch_mask()

    if not quiet:
        print(_("✓ Included line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # Filter hunk to show remaining lines
    _filter_current_hunk_excluding_batched_lines()


def _filter_current_hunk_excluding_batched_lines() -> None:
    """Filter the current hunk to exclude lines that have been batched and display it."""
    from ..data.hunk_tracking import apply_line_level_batch_filter_to_cached_hunk, clear_current_hunk_state_files
    from ..data.line_state import load_current_lines_from_state
    from ..output.hunk import print_annotated_hunk_with_aligned_gutter

    # Apply filtering
    if apply_line_level_batch_filter_to_cached_hunk():
        # All lines were batched, clear the hunk
        clear_current_hunk_state_files()
        print(_("No more lines in this hunk."), file=sys.stderr)
        return

    # Display filtered hunk
    current_lines = load_current_lines_from_state()
    if current_lines is not None:
        print_annotated_hunk_with_aligned_gutter(current_lines)


def _command_include_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch (internal helper)."""
    from ..batch import add_file_to_batch, create_batch
    from ..batch.validation import batch_exists
    from ..core.hashing import compute_stable_hunk_hash
    from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_streaming, write_snapshots_for_current_file_path
    from ..data.hunk_tracking import advance_to_and_show_next_hunk, advance_to_next_hunk, record_hunk_skipped
    from ..data.line_state import convert_current_lines_to_serializable_dict
    from ..utils.file_io import append_lines_to_file, read_text_file_contents, write_text_file_contents
    from ..utils.git import get_git_repository_root_path, run_git_command, stream_git_command
    from ..utils.paths import (
        get_block_list_file_path,
        get_context_lines,
        get_current_hunk_hash_file_path,
        get_current_hunk_patch_file_path,
        get_current_lines_json_file_path,
    )
    import json

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

    # Record hunk as claimed by this batch
    from ..utils.paths import get_batch_claimed_hunks_file_path
    append_lines_to_file(get_batch_claimed_hunks_file_path(batch_name), [current_hash])

    # Recompute global mask from all batch claims
    from ..batch.mask import recompute_global_batch_mask
    recompute_global_batch_mask()

    # Record hunk as skipped for progress tracking
    record_hunk_skipped(current_lines, current_hash)

    if not quiet:
        print(_("✓ Hunk saved to batch '{name}'").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()
