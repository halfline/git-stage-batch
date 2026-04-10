"""Include command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..batch.display import annotate_with_batch_source
from ..batch.ownership import merge_batch_ownership, translate_lines_to_batch_ownership
from ..utils.journal import log_journal
from ..utils.command import stream_command, ExitEvent, OutputEvent
from ..core.diff_parser import (
    build_current_lines_from_patch_bytes,
    get_first_matching_file_from_diff,
    parse_unified_diff_streaming,
)
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

    log_journal("command_include_start", quiet=quiet)

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
    patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes

    # Extract filename for user feedback
    current_lines = build_current_lines_from_patch_bytes(patch_bytes)
    filename = current_lines.path

    # Apply the hunk to the index using streaming
    stderr_chunks = []
    exit_code = 0

    for event in stream_command(["git", "apply", "--cached"], [patch_bytes]):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
        print(_("Failed to apply hunk: {}").format(stderr_text), file=sys.stderr)
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
    def is_unblocked(patch_bytes: bytes) -> bool:
        return compute_stable_hunk_hash(patch_bytes) not in blocked_hashes

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

        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)

        # Skip if already blocked
        if patch_hash in blocked_hashes:
            continue

        # Apply the hunk to the index using streaming
        stderr_chunks = []
        exit_code = 0

        for event in stream_command(["git", "apply", "--cached"], [patch_bytes]):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
            elif isinstance(event, OutputEvent):
                if event.fd == 2:  # stderr
                    stderr_chunks.append(event.data)

        if exit_code == 0:
            # Add to blocklist so we don't try to stage it again
            append_lines_to_file(blocklist_path, [patch_hash])
            blocked_hashes.add(patch_hash)
            hunks_staged += 1
        else:
            stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
            print(_("Failed to apply hunk: {error}").format(error=stderr_text), file=sys.stderr)
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
    from ..batch import add_file_to_batch
    from ..batch.validation import batch_exists
    from ..core.line_selection import format_line_ids, parse_line_selection, read_line_ids_file, write_line_ids_file
    from ..batch.ownership import BatchOwnership
    from ..data.hunk_tracking import recalculate_current_hunk_for_file, require_current_hunk_and_check_stale
    from ..data.line_state import load_current_lines_from_state
    from ..utils.git import create_git_blob, run_git_command
    from ..utils.paths import get_batch_claimed_line_ids_file_path

    require_current_hunk_and_check_stale()

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        from ..batch import create_batch
        create_batch(batch_name, "Auto-created")

    requested_ids = set(parse_line_selection(line_id_specification))
    current_lines = load_current_lines_from_state()

    # Filter to requested display line IDs
    selected_lines = [line for line in current_lines.lines if line.id in requested_ids]
    if not selected_lines:
        exit_with_error(_("No matching lines found for selection: {ids}").format(ids=line_id_specification))

    # Translate to batch source ownership
    new_ownership = translate_lines_to_batch_ownership(selected_lines)

    # Merge with existing batch ownership (if file already in batch)
    from ..batch.query import read_batch_metadata
    metadata = read_batch_metadata(batch_name)
    if current_lines.path in metadata.get("files", {}):
        # File already in batch - merge ownership
        existing_ownership = BatchOwnership.from_metadata_dict(metadata["files"][current_lines.path])
        ownership = merge_batch_ownership(existing_ownership, new_ownership)
    else:
        ownership = new_ownership

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", current_lines.path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Save to batch using batch source model
    add_file_to_batch(batch_name, current_lines.path, ownership, file_mode)

    # Update global mask so batched lines are hidden from future views
    # Users can see batched lines with `show --from batch-a` and restore with `apply --from batch-a`
    from ..batch.mask import recompute_global_batch_mask
    recompute_global_batch_mask()

    if not quiet:
        print(_("✓ Included line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # Recalculate current hunk to show remaining lines with fresh IDs
    # The mask filter will hide the batched lines
    recalculate_current_hunk_for_file(current_lines.path)


def _filter_current_hunk_excluding_batched_lines(*, quiet: bool = False) -> None:
    """Filter the current hunk to exclude lines that have been batched and display it."""
    from ..data.hunk_tracking import (
        advance_to_and_show_next_hunk,
        advance_to_next_hunk,
        apply_line_level_batch_filter_to_cached_hunk,
        clear_current_hunk_state_files,
    )
    from ..data.line_state import load_current_lines_from_state
    from ..output.hunk import print_annotated_hunk_with_aligned_gutter

    # Apply filtering
    if apply_line_level_batch_filter_to_cached_hunk():
        # All lines were batched, advance to next hunk
        clear_current_hunk_state_files()
        if not quiet:
            print(_("No more lines in this hunk."), file=sys.stderr)

        if quiet:
            advance_to_next_hunk()
        else:
            advance_to_and_show_next_hunk()
        return

    # Display filtered hunk
    if not quiet:
        current_lines = load_current_lines_from_state()
        if current_lines is not None:
            print_annotated_hunk_with_aligned_gutter(current_lines)


def _command_include_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch (internal helper)."""
    from ..batch import add_file_to_batch, create_batch
    from ..batch.validation import batch_exists
    from ..core.hashing import compute_stable_hunk_hash
    from ..core.diff_parser import build_current_lines_from_patch_bytes, parse_unified_diff_streaming, write_snapshots_for_current_file_path
    from ..core.line_selection import read_line_ids_file, write_line_ids_file
    from ..data.hunk_tracking import advance_to_and_show_next_hunk, advance_to_next_hunk, record_hunk_skipped
    from ..data.line_state import convert_current_lines_to_serializable_dict
    from ..utils.file_io import append_lines_to_file, read_text_file_contents, write_text_file_contents
    from ..utils.git import run_git_command, stream_git_command
    from ..utils.paths import (
        get_batch_claimed_hunks_file_path,
        get_batch_claimed_line_ids_file_path,
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
        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)
        if patch_hash not in blocked_hashes:
            current_patch = patch
            current_hash = patch_hash
            break

    if current_patch is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Get the file path
    file_path = current_patch.new_path

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", file_path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Collect all lines to batch (either current hunk or all hunks from file)
    all_lines_to_batch = []
    all_display_ids_to_batch = set()
    patches_to_process = []

    if file_only:
        # Collect ALL hunks from this file
        for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            if patch.new_path != file_path:
                continue

            patch_bytes_loop = patch.to_patch_bytes()
            patch_hash = compute_stable_hunk_hash(patch_bytes_loop)

            if patch_hash in blocked_hashes:
                continue

            # Parse hunk to get lines
            current_lines = build_current_lines_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
            all_lines_to_batch.extend(current_lines.lines)
            all_display_ids_to_batch.update(line.id for line in current_lines.lines if line.id is not None)
            patches_to_process.append((patch_bytes_loop, patch_hash))
    else:
        # Just current hunk
        patch_bytes_current = current_patch.to_patch_bytes()
        current_lines = build_current_lines_from_patch_bytes(patch_bytes_current, annotator=annotate_with_batch_source)
        all_lines_to_batch = current_lines.lines
        all_display_ids_to_batch = {line.id for line in current_lines.lines if line.id is not None}
        patches_to_process = [(patch_bytes_current, current_hash)]

    # Translate all collected lines to batch source ownership
    new_ownership = translate_lines_to_batch_ownership(all_lines_to_batch)

    # Merge with existing batch ownership (if file already in batch)
    from ..batch.query import read_batch_metadata
    from ..batch.ownership import BatchOwnership
    metadata = read_batch_metadata(batch_name)
    if file_path in metadata.get("files", {}):
        # File already in batch - merge ownership
        existing_ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])
        ownership = merge_batch_ownership(existing_ownership, new_ownership)
    else:
        ownership = new_ownership

    # Save to batch using batch source model (once, with all accumulated data)
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Mark hunks as processed
    for patch_bytes_item, patch_hash in patches_to_process:
        # Mark this hunk as processed
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record hunk as claimed by this batch
        append_lines_to_file(get_batch_claimed_hunks_file_path(batch_name), [patch_hash])

        # Record hunk as skipped for progress tracking
        hunk_lines = build_current_lines_from_patch_bytes(patch_bytes_item)
        record_hunk_skipped(hunk_lines, patch_hash)

    # Recompute global mask from all batch claims (after recording hunk claims)
    from ..batch.mask import recompute_global_batch_mask
    recompute_global_batch_mask()

    # Print success message
    if not quiet:
        if file_only:
            from ..i18n import ngettext
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}'",
                "✓ {count} hunks from {file} saved to batch '{name}'",
                len(patches_to_process)
            ).format(count=len(patches_to_process), file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
        else:
            print(_("✓ Hunk saved to batch '{name}'").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()
