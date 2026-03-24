"""Discard command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..core.diff_parser import build_current_lines_from_patch_text, get_first_matching_file_from_diff, parse_unified_diff_streaming
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection
from ..data.hunk_tracking import (
    advance_to_and_show_next_hunk,
    advance_to_next_hunk,
    recalculate_current_hunk_for_file,
    record_hunk_discarded,
    require_current_hunk_and_check_stale,
)
from ..data.line_state import load_current_lines_from_state
from ..data.session import require_session_started, snapshot_file_if_untracked
from ..exceptions import exit_with_error
from ..i18n import _
from ..staging.operations import build_target_working_tree_content_with_discarded_lines
from ..utils.file_io import append_lines_to_file, read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, stream_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
)


def command_discard(*, quiet: bool = False) -> None:
    """Discard the current hunk from the working tree."""
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

    # Extract filename for user feedback and snapshotting
    current_lines = build_current_lines_from_patch_text(patch_text)
    filename = current_lines.path

    # Snapshot file if untracked before discarding
    if filename != "/dev/null":
        snapshot_file_if_untracked(filename)

    # Apply the hunk in reverse to discard from working tree
    try:
        subprocess.run(
            ["git", "apply", "--reverse"],
            input=patch_text,
            text=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard hunk: {}").format(e.stderr), file=sys.stderr)
        return

    # After reverse-applying a new file, delete it if it became empty
    # (git apply -R on new files empties them but doesn't delete them)
    is_new_file = "--- /dev/null" in patch_text
    if is_new_file:
        absolute_path = get_git_repository_root_path() / filename
        if absolute_path.exists():
            content = read_text_file_contents(absolute_path)
            if not content.strip():  # File is empty
                absolute_path.unlink()

    # Add hash to blocklist
    blocklist_path = get_block_list_file_path()
    append_lines_to_file(blocklist_path, [patch_hash])

    # Record for progress tracking
    record_hunk_discarded(patch_hash)

    if not quiet:
        print(_("✓ Hunk discarded from {file}").format(file=filename), file=sys.stderr)

    if quiet:
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()


def command_discard_file() -> None:
    """Discard the entire current file from the working tree."""
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
        print(_("No changes to discard."), file=sys.stderr)
        return

    # Snapshot the file if it's untracked (for abort functionality)
    snapshot_file_if_untracked(target_file)

    # Stream through hunks and collect hashes from target file BEFORE removing it
    # (git rm -f will stage the deletion, making hunks disappear from git diff)
    hashes_to_block = []
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_text = patch.to_patch_text()
        patch_hash = compute_stable_hunk_hash(patch_text)

        if patch_hash not in blocked_hashes:
            hashes_to_block.append(patch_hash)

    # Remove the file from working tree
    try:
        subprocess.run(
            ["git", "rm", "-f", target_file],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(_("Failed to discard file: {}").format(e.stderr.decode()), file=sys.stderr)
        return

    # Mark all collected hashes as processed
    for patch_hash in hashes_to_block:
        append_lines_to_file(blocklist_path, [patch_hash])

    print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

    advance_to_and_show_next_hunk()


def command_discard_line(line_id_specification: str) -> None:
    """Discard only the specified lines from the working tree.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    require_current_hunk_and_check_stale()

    requested_ids = parse_line_selection(line_id_specification)
    current_lines = load_current_lines_from_state()

    # Get current working tree content
    working_file_path = get_git_repository_root_path() / current_lines.path
    if working_file_path.exists():
        working_text = working_file_path.read_text(encoding="utf-8", errors="surrogateescape")
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=current_lines.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_with_discarded_lines(
        current_lines, set(requested_ids), working_text)

    # Write back to working tree
    working_file_path.write_text(target_working_content, encoding="utf-8", errors="surrogateescape")

    # After modifying working tree, recalculate hunk for the SAME file
    recalculate_current_hunk_for_file(current_lines.path)

    print(_("✓ Discarded line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)


def command_discard_to_batch(batch_name: str, line_ids: str | None = None, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save to batch then discard from working tree."""
    require_git_repository()
    ensure_state_directory_exists()

    if line_ids is not None:
        _command_discard_lines_to_batch(batch_name, line_ids, quiet=quiet)
        return

    # Whole-hunk or file-level batch operation
    _command_discard_hunk_to_batch(batch_name, file_only=file_only, quiet=quiet)


def _command_discard_lines_to_batch(batch_name: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Save specific lines to batch and discard them from working tree (internal helper)."""
    from ..batch import add_file_to_batch, read_file_from_batch
    from ..batch.validation import batch_exists
    from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
    from ..data.hunk_tracking import recalculate_current_hunk_for_file, require_current_hunk_and_check_stale
    from ..data.line_state import load_current_lines_from_state
    from ..staging.operations import build_target_index_content_with_selected_lines
    from ..utils.file_io import read_text_file_contents
    from ..utils.git import get_git_repository_root_path, run_git_command
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

    # Now discard those lines from working tree
    working_file_path = get_git_repository_root_path() / current_lines.path
    if working_file_path.exists():
        working_text = working_file_path.read_text(encoding="utf-8", errors="surrogateescape")
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=current_lines.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_with_discarded_lines(
        current_lines, set(requested_ids), working_text)

    # Write back to working tree
    working_file_path.write_text(target_working_content, encoding="utf-8", errors="surrogateescape")

    if not quiet:
        print(_("✓ Discarded line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # After modifying working tree, recalculate hunk for the SAME file
    recalculate_current_hunk_for_file(current_lines.path)


def _command_discard_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch and discard from working tree (internal helper)."""
    from ..batch import add_file_to_batch, create_batch
    from ..batch.validation import batch_exists
    from ..data.hunk_tracking import advance_to_and_show_next_hunk, advance_to_next_hunk, record_hunk_discarded
    from ..data.line_state import convert_current_lines_to_serializable_dict
    from ..core.diff_parser import build_current_lines_from_patch_text, write_snapshots_for_current_file_path
    from ..utils.file_io import write_text_file_contents
    from ..utils.git import run_git_command
    from ..utils.paths import (
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

    # Get the file path
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

    # Save to batch (save file content once)
    add_file_to_batch(batch_name, file_path, content, file_mode)

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    if file_only:
        # File-level operation: process all hunks from this file
        hunks_processed = 0
        for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
            # Only process hunks from the target file
            if patch.new_path != file_path:
                continue

            patch_text = patch.to_patch_text()
            patch_hash = compute_stable_hunk_hash(patch_text)

            # Skip already blocked hunks
            if patch_hash in blocked_hashes:
                continue

            # Apply reverse patch to working tree
            reverse_result = subprocess.run(
                ["git", "apply", "--reverse", "--unidiff-zero"],
                input=patch_text,
                capture_output=True,
                text=True,
                check=False
            )

            if reverse_result.returncode != 0:
                exit_with_error(_("Failed to apply reverse patch: {error}").format(error=reverse_result.stderr))

            # Mark this hunk as processed
            append_lines_to_file(blocklist_path, [patch_hash])
            blocked_hashes.add(patch_hash)

            # Record hunk as claimed by this batch
            from ..utils.paths import get_batch_claimed_hunks_file_path
            append_lines_to_file(get_batch_claimed_hunks_file_path(batch_name), [patch_hash])

            # Record hunk as discarded for progress tracking
            record_hunk_discarded(patch_hash)

            hunks_processed += 1

        # Recompute global mask from all batch claims
        from ..batch.mask import recompute_global_batch_mask
        recompute_global_batch_mask()

        if not quiet:
            from ..i18n import ngettext
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}' and discarded",
                "✓ {count} hunks from {file} saved to batch '{name}' and discarded",
                hunks_processed
            ).format(count=hunks_processed, file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
    else:
        # Single hunk operation
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

        # Apply reverse patch to working tree
        reverse_result = subprocess.run(
            ["git", "apply", "--reverse", "--unidiff-zero"],
            input=patch_text,
            capture_output=True,
            text=True,
            check=False
        )

        if reverse_result.returncode != 0:
            exit_with_error(_("Failed to apply reverse patch: {error}").format(error=reverse_result.stderr))

        # Add hash to blocklist (mark as processed)
        append_lines_to_file(blocklist_path, [current_hash])

        # Record hunk as claimed by this batch
        from ..utils.paths import get_batch_claimed_hunks_file_path
        append_lines_to_file(get_batch_claimed_hunks_file_path(batch_name), [current_hash])

        # Recompute global mask from all batch claims
        from ..batch.mask import recompute_global_batch_mask
        recompute_global_batch_mask()

        # Record hunk as discarded for progress tracking
        record_hunk_discarded(current_hash)

        if not quiet:
            print(_("✓ Hunk saved to batch '{name}' and discarded from working tree").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()
