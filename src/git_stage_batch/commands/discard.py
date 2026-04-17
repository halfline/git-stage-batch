"""Discard command implementation."""

from __future__ import annotations

import subprocess
import sys

from ..batch import add_file_to_batch, create_batch
from ..batch.display import annotate_with_batch_source
from ..batch.ownership import BatchOwnership
from ..batch.query import read_batch_metadata
from ..batch.source_refresh import prepare_batch_ownership_update_for_selection
from ..batch.validation import batch_exists
from ..core.diff_parser import (
    build_line_changes_from_patch_bytes,
    parse_unified_diff_streaming,
)
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import (
    advance_to_and_show_next_change,
    advance_to_next_change,
    cache_file_as_single_hunk,
    fetch_next_change,
    get_selected_change_file_path,
    recalculate_selected_hunk_for_file,
    record_hunk_discarded,
    require_selected_hunk,
)
from ..data.line_state import load_line_changes_from_state
from ..data.session import require_session_started, snapshot_file_if_untracked
from ..exceptions import exit_with_error, NoMoreHunks
from ..i18n import _, ngettext
from ..output import print_line_level_changes
from ..staging.operations import build_target_working_tree_content_bytes_with_discarded_lines
from ..utils.command import ExitEvent, OutputEvent, stream_command
from ..utils.file_io import append_lines_to_file, read_file_bytes, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command, stream_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)


def command_discard(*, quiet: bool = False) -> None:
    """Discard the selected hunk or binary file from the working tree."""

    log_journal("command_discard_start", quiet=quiet)

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
        # Binary file - restore from HEAD or delete
        file_path = item.new_path if item.new_path != "/dev/null" else item.old_path

        # Snapshot file if untracked before discarding
        if file_path != "/dev/null":
            snapshot_file_if_untracked(file_path)

        log_journal("command_discard_binary_file", file_path=file_path, change_type=item.change_type)

        if item.is_new_file():
            # New file: delete from working tree
            absolute_path = get_git_repository_root_path() / file_path
            if absolute_path.exists():
                absolute_path.unlink()
                log_journal("command_discard_binary_deleted", file_path=file_path)
        elif item.is_deleted_file():
            # Deleted file: restore from HEAD
            result = run_git_command(["checkout", "HEAD", "--", file_path], check=False)
            if result.returncode != 0:
                print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
                return
            log_journal("command_discard_binary_restored", file_path=file_path)
        else:
            # Modified file: restore from HEAD
            result = run_git_command(["checkout", "HEAD", "--", file_path], check=False)
            if result.returncode != 0:
                print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
                return
            log_journal("command_discard_binary_restored", file_path=file_path)

        # Add hash to blocklist
        blocklist_path = get_block_list_file_path()
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record for progress tracking
        record_hunk_discarded(patch_hash)

        if not quiet:
            change_desc = "added" if item.is_new_file() else ("deleted" if item.is_deleted_file() else "modified")
            print(_("✓ Binary file {desc} discarded: {file}").format(desc=change_desc, file=file_path), file=sys.stderr)

        if quiet:
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()
        return

    # Text hunk - use git apply -R
    patch_bytes = read_file_bytes(get_selected_hunk_patch_file_path())

    # Extract filename for user feedback and snapshotting
    line_changes = build_line_changes_from_patch_bytes(patch_bytes)
    filename = line_changes.path

    # Snapshot file if untracked before discarding
    if filename != "/dev/null":
        snapshot_file_if_untracked(filename)

    # Apply the hunk in reverse to discard from working tree using streaming
    log_journal("command_discard_before_git_apply", filename=filename, patch_hash=patch_hash)
    stderr_chunks = []
    exit_code = 0

    for event in stream_command(["git", "apply", "--reverse"], [patch_bytes]):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

    stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace') if stderr_chunks else ""
    log_journal("command_discard_after_git_apply", exit_code=exit_code, stderr_len=len(stderr_text), filename=filename)

    if exit_code != 0:
        log_journal("command_discard_git_apply_failed", exit_code=exit_code, stderr=stderr_text, filename=filename)
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

    log_journal("command_discard_success", filename=filename, patch_hash=patch_hash)

    if not quiet:
        print(_("✓ Hunk discarded from {file}").format(file=filename), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def command_discard_file(file: str) -> None:
    """Discard the entire specified file from the working tree.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.
    """

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Determine target file
    if file == "":
        # --file with no arg: use selected hunk's file
        try:
            fetch_next_change()
        except NoMoreHunks:
            print(_("No more hunks to process."), file=sys.stderr)
            return

        # Get the target file from currently cached hunk
        line_changes = load_line_changes_from_state()
        target_file = line_changes.path
    else:
        # Explicit path provided
        target_file = file

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
        print(_("Failed to discard file: {}").format(e.stderr.decode("utf-8", errors="replace")), file=sys.stderr)
        return

    # Mark all collected hashes as processed
    for patch_hash in hashes_to_block:
        append_lines_to_file(blocklist_path, [patch_hash])
        # Record for progress tracking
        record_hunk_discarded(patch_hash)

    print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

    advance_to_and_show_next_change()


def command_discard_line(line_id_specification: str) -> None:
    """Discard only the specified lines from the working tree.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    require_selected_hunk()

    requested_ids = parse_line_selection(line_id_specification)
    line_changes = load_line_changes_from_state()

    # Get selected working tree content
    working_file_path = get_git_repository_root_path() / line_changes.path
    if working_file_path.exists():
        working_bytes = working_file_path.read_bytes()
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=line_changes.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
        line_changes, set(requested_ids), working_bytes)

    # Write back to working tree
    working_file_path.write_bytes(target_working_content)

    # After modifying working tree, recalculate hunk for the SAME file
    recalculate_selected_hunk_for_file(line_changes.path)

    print(_("✓ Discarded line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)


def command_discard_to_batch(batch_name: str, line_ids: str | None = None, file: str | None = None, *, quiet: bool = False) -> None:
    """Save to batch then discard from working tree.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to discard
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
    """
    require_git_repository()
    ensure_state_directory_exists()

    if file is not None:
        # File-scoped operation

        # Determine target file
        if file == "":
            # --file with no arg: use selected hunk's file
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file

        if line_ids is None:
            # --file without --line: discard entire file
            _command_discard_file_to_batch(batch_name, target_file, quiet=quiet)
        else:
            # --file with --line: discard specific lines from file
            _command_discard_file_lines_to_batch(batch_name, target_file, line_ids, quiet=quiet)
    else:
        # Hunk-scoped operation (selected behavior)
        if line_ids is not None:
            _command_discard_lines_to_batch(batch_name, line_ids, quiet=quiet)
        else:
            # Discard entire selected hunk
            _command_discard_hunk_to_batch(batch_name, file_only=False, quiet=quiet)


def _command_discard_file_to_batch(batch_name: str, file_path: str, *, quiet: bool = False) -> None:
    """Discard entire file to batch (internal helper for file-scoped operations)."""

    log_journal("discard_file_to_batch_start", batch_name=batch_name, file_path=file_path, quiet=quiet)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

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

    # Collect ALL hunks from this file (live working tree state)
    all_lines_to_batch = []
    patches_to_discard = []

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", file_path])):
        patch_bytes_loop = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes_loop)

        if patch_hash in blocked_hashes:
            continue

        # Parse hunk to get lines
        hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
        all_lines_to_batch.extend(hunk_lines.lines)
        patches_to_discard.append((patch_bytes_loop, patch_hash))

    if not all_lines_to_batch:
        # Special case: empty new file (exists in working tree but not in HEAD)
        repo_root = get_git_repository_root_path()
        full_path = repo_root / file_path
        if full_path.exists():
            # Check if file exists in HEAD
            head_result = run_git_command(["cat-file", "-e", f"HEAD:{file_path}"], check=False)
            if head_result.returncode != 0:
                # File doesn't exist in HEAD - it's a new empty file
                # Save empty file metadata to batch and delete it
                empty_ownership = BatchOwnership(claimed_lines=[], deletions=[])

                # Snapshot before deleting
                snapshot_file_if_untracked(file_path)

                # Save to batch
                add_file_to_batch(batch_name, file_path, empty_ownership, file_mode)

                # Delete from working tree
                full_path.unlink()

                # Remove from index if present (from intent-to-add)
                run_git_command(["rm", "--cached", "--quiet", "--", file_path], check=False)

                if not quiet:
                    print(_("Discarded file '{file}' to batch '{batch}'").format(file=file_path, batch=batch_name), file=sys.stderr)

                log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)
                return

        if not quiet:
            print(_("No changes in file '{file}' to discard.").format(file=file_path), file=sys.stderr)
        return

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=all_lines_to_batch
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard file to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Record hunks as discarded for progress tracking
    for patch_bytes, patch_hash in patches_to_discard:
        record_hunk_discarded(patch_hash)

    # Discard from working tree (reverse patches)
    for patch_bytes_item, patch_hash in patches_to_discard:
        log_journal("discard_file_to_batch_before_git_apply", batch_name=batch_name, patch_hash=patch_hash, file_path=file_path, patch_content=patch_bytes_item.decode("utf-8", errors="replace"))

        exit_code = 0
        stderr_chunks = []

        for event in stream_command(
            ["git", "apply", "--reverse", "--unidiff-zero"],
            stdin_chunks=[patch_bytes_item]
        ):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
            elif isinstance(event, OutputEvent) and event.fd == 2:  # stderr
                stderr_chunks.append(event.data)

        if exit_code != 0:
            stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace')
            exit_with_error(_("Failed to discard changes from file: {err}").format(err=stderr_text))

        log_journal("discard_file_to_batch_after_git_apply", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code)

    # If file was deleted from working tree, remove from index too
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if not full_path.exists():
        # File was deleted by git apply --reverse, remove from index
        run_git_command(["rm", "--cached", "--quiet", "--", file_path], check=False)

    if not quiet:
        print(_("Discarded file '{file}' to batch '{batch}'").format(file=file_path, batch=batch_name), file=sys.stderr)

    log_journal("discard_file_to_batch_end", batch_name=batch_name, file_path=file_path)

    # Show next hunk
    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def _command_discard_file_lines_to_batch(batch_name: str, file_path: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Discard specific lines from a file to batch (file-scoped with line IDs)."""

    # Cache entire file as a single hunk
    cached_lines = cache_file_as_single_hunk(file_path)
    if cached_lines is None:
        exit_with_error(_("No changes in file '{file}'.").format(file=file_path))

    # Annotate with batch source line numbers
    line_changes = annotate_with_batch_source(file_path, cached_lines)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Parse line IDs and filter to selected lines
    requested_ids = set(parse_line_selection(line_id_specification))
    selected_lines = [line for line in line_changes.lines if line.id in requested_ids]

    if not selected_lines:
        if not quiet:
            print(_("No lines match the specified IDs in file '{file}'.").format(file=file_path), file=sys.stderr)
        return

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", file_path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=selected_lines
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Now discard selected lines from working tree
    working_file_path = get_git_repository_root_path() / file_path
    if working_file_path.exists():
        working_bytes = working_file_path.read_bytes()
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=file_path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
        line_changes, requested_ids, working_bytes)

    # Write back to working tree
    working_file_path.write_bytes(target_working_content)

    if not quiet:
        print(_("Discarded line(s) from file '{file}' to batch '{batch}': {lines}").format(
            file=file_path,
            batch=batch_name,
            lines=line_id_specification
        ), file=sys.stderr)

    # Show next hunk
    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def _command_discard_lines_to_batch(batch_name: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Save specific lines to batch and discard them from working tree (internal helper)."""

    log_journal("discard_lines_to_batch_start", batch_name=batch_name, line_ids=line_id_specification, quiet=quiet)

    require_selected_hunk()

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    requested_ids = set(parse_line_selection(line_id_specification))
    line_changes = load_line_changes_from_state()

    # Filter to requested display line IDs
    selected_lines = [line for line in line_changes.lines if line.id in requested_ids]
    if not selected_lines:
        exit_with_error(_("No matching lines found for selection: {ids}").format(ids=line_id_specification))

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if line_changes.path in metadata.get("files", {}):
        file_metadata = metadata["files"][line_changes.path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=line_changes.path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=selected_lines
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        exit_with_error(
            _("Cannot discard lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=line_changes.path, batch=batch_name, error=str(e))
        )

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", line_changes.path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # add_file_to_batch creates the batch source commit from this snapshot.
    snapshot_file_if_untracked(line_changes.path)

    log_journal("discard_lines_to_batch_before_add", batch_name=batch_name, file_path=line_changes.path)

    # Save to batch using batch source model
    add_file_to_batch(batch_name, line_changes.path, ownership, file_mode)

    log_journal("discard_lines_to_batch_after_add", batch_name=batch_name, file_path=line_changes.path)

    # Now discard those lines from working tree
    working_file_path = get_git_repository_root_path() / line_changes.path
    if working_file_path.exists():
        working_bytes = working_file_path.read_bytes()
    else:
        exit_with_error(_("File not found in working tree: {file}").format(file=line_changes.path))

    # Build new working tree content with selected lines discarded
    target_working_content = build_target_working_tree_content_bytes_with_discarded_lines(
        line_changes, requested_ids, working_bytes)

    # Write back to working tree
    log_journal("discard_lines_to_batch_before_write", file_path=str(working_file_path))
    working_file_path.write_bytes(target_working_content)
    log_journal("discard_lines_to_batch_after_write", file_path=str(working_file_path))

    if not quiet:
        print(_("✓ Discarded line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # After modifying working tree, recalculate and show the updated hunk for this file
    recalculate_selected_hunk_for_file(line_changes.path)

    # Show the updated hunk (or next hunk if this file is now complete)
    if not quiet:
        line_changes_updated = load_line_changes_from_state()
        if line_changes_updated is not None:
            print_line_level_changes(line_changes_updated)

    log_journal("discard_lines_to_batch_success", batch_name=batch_name, line_ids=line_id_specification, file_path=line_changes.path)


def _command_discard_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch and discard from working tree (internal helper)."""

    log_journal("discard_hunk_to_batch_start", batch_name=batch_name, file_only=file_only, quiet=quiet)

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        log_journal("discard_hunk_to_batch_creating_batch", batch_name=batch_name)
        create_batch(batch_name, "Auto-created")

    # Ensure cached hunk is selected
    try:
        fetch_next_change()
    except NoMoreHunks:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Get the file path and hash from currently cached hunk
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()
    patch_bytes = read_file_bytes(get_selected_hunk_patch_file_path())
    line_changes = build_line_changes_from_patch_bytes(patch_bytes, annotator=annotate_with_batch_source)
    file_path = line_changes.path

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

    # Collect all lines to batch (either selected hunk or all hunks from file)
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
            hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
            all_lines_to_batch.extend(hunk_lines.lines)
            all_display_ids_to_batch.update(line.id for line in hunk_lines.lines if line.id is not None)
            patches_to_discard.append((patch_bytes_loop, patch_hash))
    else:
        # Just selected hunk (already loaded above)
        all_lines_to_batch = line_changes.lines
        all_display_ids_to_batch = {line.id for line in line_changes.lines if line.id is not None}
        patches_to_discard = [(patch_bytes, patch_hash)]

    # Prepare batch ownership update (handles stale source, translation, merge)

    metadata = read_batch_metadata(batch_name)
    existing_ownership = None
    current_batch_source = None

    if file_path in metadata.get("files", {}):
        # File already in batch - get existing ownership and batch source
        file_metadata = metadata["files"][file_path]
        existing_ownership = BatchOwnership.from_metadata_dict(file_metadata)
        current_batch_source = file_metadata.get("batch_source_commit")

    try:
        update = prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=current_batch_source,
            existing_ownership=existing_ownership,
            selected_lines=all_lines_to_batch
        )

        # Use the prepared ownership for persistence
        ownership = update.ownership_after

    except ValueError as e:
        # Remapping failed - fail loudly
        exit_with_error(
            _("Cannot discard to batch: batch source is stale and remapping failed.\n"
              "File: {file}\n"
              "Batch: {batch}\n"
              "Error: {error}").format(file=file_path, batch=batch_name, error=str(e))
        )

    # add_file_to_batch creates the batch source commit from this snapshot.
    snapshot_file_if_untracked(file_path)

    log_journal("discard_hunk_to_batch_before_add", batch_name=batch_name, file_path=file_path, num_patches=len(patches_to_discard))

    # Save to batch using batch source model (once, with all accumulated data)
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    log_journal("discard_hunk_to_batch_after_add", batch_name=batch_name, file_path=file_path)

    # Check if this is a new file (before applying patches)
    is_new_file = any(b"--- /dev/null" in patch_bytes_item for patch_bytes_item, _ in patches_to_discard)

    # Apply reverse patches to discard from working tree
    for patch_bytes_item, patch_hash in patches_to_discard:
        # Check if this is an empty file patch (@@ -0,0 +0,0 @@)
        # Empty file patches are synthetic and cannot be reversed with git apply
        is_empty_file_patch = b"@@ -0,0 +0,0 @@" in patch_bytes_item

        if not is_empty_file_patch:
            log_journal("discard_hunk_to_batch_before_git_apply", batch_name=batch_name, patch_hash=patch_hash, file_path=file_path, patch_content=patch_bytes_item.decode("utf-8", errors="replace"))

            # Use stream_command to apply reverse patch
            stderr_chunks = []
            exit_code = 0

            for event in stream_command(["git", "apply", "--reverse", "--unidiff-zero"], [patch_bytes_item]):
                if isinstance(event, ExitEvent):
                    exit_code = event.exit_code
                elif isinstance(event, OutputEvent):
                    if event.fd == 2:  # stderr
                        stderr_chunks.append(event.data)

            stderr_text = b"".join(stderr_chunks).decode('utf-8', errors='replace') if stderr_chunks else ""
            log_journal("discard_hunk_to_batch_after_git_apply", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code, stderr_len=len(stderr_text))

            if exit_code != 0:
                log_journal("discard_hunk_to_batch_git_apply_failed", batch_name=batch_name, patch_hash=patch_hash, exit_code=exit_code, stderr=stderr_text)
                exit_with_error(_("Failed to apply reverse patch: {error}").format(error=stderr_text))
        else:
            log_journal("discard_hunk_to_batch_skipping_empty_patch", batch_name=batch_name, patch_hash=patch_hash)
        # else: skip reverse for empty files - nothing to reverse, cleanup code below handles file removal

        # Mark this hunk as processed
        append_lines_to_file(blocklist_path, [patch_hash])

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

    log_journal("discard_hunk_to_batch_success", batch_name=batch_name, file_path=file_path, num_patches=len(patches_to_discard))

    # Print success message
    if not quiet:
        if file_only:
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}' and discarded",
                "✓ {count} hunks from {file} saved to batch '{name}' and discarded",
                len(patches_to_discard)
            ).format(count=len(patches_to_discard), file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
        else:
            print(_("✓ Hunk saved to batch '{name}' and discarded from working tree").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()
