"""Discard command implementation."""

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

    log_journal("command_discard_start", quiet=quiet)

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

    # Extract filename for user feedback and snapshotting
    current_lines = build_current_lines_from_patch_bytes(patch_bytes)
    filename = current_lines.path

    # Snapshot file if untracked before discarding
    if filename != "/dev/null":
        snapshot_file_if_untracked(filename)

    # Apply the hunk in reverse to discard from working tree using streaming
    stderr_chunks = []
    exit_code = 0

    for event in stream_command(["git", "apply", "--reverse"], [patch_bytes]):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
        print(_("Failed to discard hunk: {}").format(stderr_text), file=sys.stderr)
        return

    # After reverse-applying a new file, delete it if it became empty
    # (git apply -R on new files empties them but doesn't delete them)
    is_new_file = b"--- /dev/null" in patch_bytes
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
    from ..data.hunk_tracking import find_and_cache_next_unblocked_hunk

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if find_and_cache_next_unblocked_hunk() is None:
        print(_("No more hunks to process."), file=sys.stderr)
        return

    # Get the target file from currently cached hunk
    current_lines = load_current_lines_from_state()
    target_file = current_lines.path

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Snapshot the file if it's untracked (for abort functionality)
    snapshot_file_if_untracked(target_file)

    # Stream through hunks and collect hashes from target file BEFORE removing it
    # (git rm -f will stage the deletion, making hunks disappear from git diff)
    hashes_to_block = []
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        if patch.new_path != target_file:
            continue

        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)

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
    from ..batch.storage import add_file_to_batch
    from ..batch.validation import batch_exists
    from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
    from ..data.hunk_tracking import recalculate_current_hunk_for_file, require_current_hunk_and_check_stale
    from ..data.line_state import load_current_lines_from_state
    from ..utils.git import get_git_repository_root_path, run_git_command
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
    from ..batch.ownership import BatchOwnership
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

    # Snapshot file if untracked (MUST happen before add_file_to_batch)
    snapshot_file_if_untracked(current_lines.path)

    # Save to batch using batch source model
    add_file_to_batch(batch_name, current_lines.path, ownership, file_mode)

    # Update global mask so batched lines are marked as processed
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
        current_lines, requested_ids, working_text)

    # Write back to working tree
    working_file_path.write_text(target_working_content, encoding="utf-8", errors="surrogateescape")

    if not quiet:
        print(_("✓ Discarded line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # After modifying working tree, recalculate hunk for the SAME file
    recalculate_current_hunk_for_file(current_lines.path)


def _command_discard_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch and discard from working tree (internal helper)."""
    from ..batch.storage import add_file_to_batch
    from ..batch import create_batch
    from ..batch.validation import batch_exists
    from ..core.line_selection import read_line_ids_file, write_line_ids_file
    from ..data.hunk_tracking import (
        advance_to_and_show_next_hunk,
        advance_to_next_hunk,
        find_and_cache_next_unblocked_hunk,
        record_hunk_discarded,
    )
    from ..core.diff_parser import build_current_lines_from_patch_bytes
    from ..utils.git import get_git_repository_root_path, run_git_command
    from ..utils.paths import get_batch_claimed_line_ids_file_path
    from ..data.session import snapshot_file_if_untracked

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Ensure cached hunk is fresh (handles case where file was modified externally)
    if find_and_cache_next_unblocked_hunk() is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Get the file path and hash from currently cached hunk
    patch_hash = read_text_file_contents(get_current_hunk_hash_file_path()).strip()
    patch_text = read_text_file_contents(get_current_hunk_patch_file_path())
    patch_bytes = patch_text.encode('utf-8')  # Convert stored text to bytes
    current_lines = build_current_lines_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
    file_path = current_lines.path

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

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
    patches_to_discard = []

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
            hunk_lines = build_current_lines_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
            all_lines_to_batch.extend(hunk_lines.lines)
            all_display_ids_to_batch.update(line.id for line in hunk_lines.lines if line.id is not None)
            patches_to_discard.append((patch_bytes_loop, patch_hash))
    else:
        # Just current hunk (already loaded above)
        all_lines_to_batch = current_lines.lines
        all_display_ids_to_batch = {line.id for line in current_lines.lines if line.id is not None}
        patches_to_discard = [(patch_bytes, patch_hash)]

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

    # Snapshot file before modifying (MUST happen before add_file_to_batch
    # because create_batch_source_commit needs the snapshot to exist)
    snapshot_file_if_untracked(file_path)

    # Save to batch using batch source model (once, with all accumulated data)
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Recompute global mask from all batch claims
    # The mask now uses stable batch source coordinates (not ephemeral display IDs),
    # so it's safe to recompute for all operations (whole-file and partial)
    from ..batch.mask import recompute_global_batch_mask
    recompute_global_batch_mask()

    # Check if this is a new file (before applying patches)
    is_new_file = any(b"--- /dev/null" in patch_bytes_item for patch_bytes_item, _ in patches_to_discard)

    # Apply reverse patches to discard from working tree
    for patch_bytes_item, patch_hash in patches_to_discard:
        # Check if this is an empty file patch (@@ -0,0 +0,0 @@)
        # Empty file patches are synthetic and cannot be reversed with git apply
        is_empty_file_patch = b"@@ -0,0 +0,0 @@" in patch_bytes_item

        if not is_empty_file_patch:
            # Use stream_command to apply reverse patch
            stderr_chunks = []
            exit_code = 0

            for event in stream_command(["git", "apply", "--reverse", "--unidiff-zero"], [patch_bytes_item]):
                if isinstance(event, ExitEvent):
                    exit_code = event.exit_code
                elif isinstance(event, OutputEvent):
                    if event.fd == 2:  # stderr
                        stderr_chunks.append(event.data)

            if exit_code != 0:
                stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
                exit_with_error(_("Failed to apply reverse patch: {error}").format(error=stderr_text))
        # else: skip reverse for empty files - nothing to reverse, cleanup code below handles file removal

        # Mark this hunk as processed
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record hunk as claimed by this batch
        from ..utils.paths import get_batch_claimed_hunks_file_path
        append_lines_to_file(get_batch_claimed_hunks_file_path(batch_name), [patch_hash])

        # Record hunk as discarded for progress tracking
        record_hunk_discarded(patch_hash)

    # Clean up file and index after discarding to batch
    # Only remove file if it's actually meant to be gone:
    # - New file that's now empty after reversal
    # - File deleted in diff (doesn't exist in working tree after reversal)
    if is_new_file or file_only:
        absolute_path = get_git_repository_root_path() / file_path

        # Check if file still exists after git apply --reverse
        if not absolute_path.exists():
            # File was deleted by reverse patches (was a file deletion diff)
            # Remove from index to complete the deletion
            run_git_command(["rm", "--cached", "--quiet", file_path], check=False)
        elif is_new_file:
            # New file: only remove if it's empty after reverse patches
            content = read_text_file_contents(absolute_path)
            if not content.strip():
                absolute_path.unlink()
                run_git_command(["rm", "--cached", "--quiet", file_path], check=False)
        # else: file still exists with content (reverted to HEAD state), leave it alone

    # Print success message
    if not quiet:
        if file_only:
            from ..i18n import ngettext
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}' and discarded",
                "✓ {count} hunks from {file} saved to batch '{name}' and discarded",
                len(patches_to_discard)
            ).format(count=len(patches_to_discard), file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
        else:
            print(_("✓ Hunk saved to batch '{name}' and discarded from working tree").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_hunk()
    else:
        advance_to_and_show_next_hunk()
