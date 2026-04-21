"""Include command implementation."""

from __future__ import annotations

import sys

from ..batch import add_file_to_batch, create_batch
from ..batch.display import annotate_with_batch_source
from ..batch.ownership import BatchOwnership, translate_lines_to_batch_ownership
from ..batch.query import read_batch_metadata
from ..batch.semantic_selection import try_build_semantic_selection_for_selected_hunk
from ..batch.source_refresh import prepare_batch_ownership_update_for_selection
from ..batch.validation import batch_exists
from ..core.diff_parser import (
    build_line_changes_from_patch_bytes,
    get_first_matching_file_from_diff,
    parse_unified_diff_streaming,
)
from ..core.hashing import compute_stable_hunk_hash
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..core.models import BinaryFileChange
from ..data.hunk_tracking import (
    advance_to_and_show_next_change,
    advance_to_next_change,
    apply_line_level_batch_filter_to_cached_hunk,
    cache_file_as_single_hunk,
    clear_selected_change_state_files,
    fetch_next_change,
    get_selected_change_file_path,
    recalculate_selected_hunk_for_file,
    record_hunk_included,
    record_hunk_skipped,
    require_selected_hunk,
)
from ..data.consumed_selections import record_consumed_selection
from ..data.line_state import load_line_changes_from_state
from ..data.session import require_session_started, snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import NoMoreHunks, exit_with_error
from ..i18n import _, ngettext
from ..output import print_line_level_changes
from ..staging.operations import (
    build_target_index_content_bytes_with_replaced_lines,
    build_target_index_content_bytes_with_selected_lines,
    update_index_with_blob_content,
)
from ..utils.command import ExitEvent, OutputEvent, stream_command
from ..utils.file_io import append_lines_to_file, read_file_bytes, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command, stream_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_context_lines,
    get_line_changes_json_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_selected_binary_file_json_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
)


def command_include(*, quiet: bool = False) -> None:
    """Include (stage) the selected hunk or binary file."""

    log_journal("command_include_start", quiet=quiet)

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
    with undo_checkpoint("include"):
        # Read cached hash
        patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

        # Handle based on item type
        if isinstance(item, BinaryFileChange):
            # Binary file - use git add
            file_path = item.new_path if item.new_path != "/dev/null" else item.old_path

            # Stage the binary file using git add
            result = run_git_command(["add", "--", file_path], check=False)
            if result.returncode != 0:
                print(_("Failed to stage binary file: {}").format(result.stderr), file=sys.stderr)
                return

            # Add hash to blocklist
            blocklist_path = get_block_list_file_path()
            append_lines_to_file(blocklist_path, [patch_hash])

            # Record for progress tracking
            record_hunk_included(patch_hash)

            if not quiet:
                change_desc = "added" if item.is_new_file() else ("deleted" if item.is_deleted_file() else "modified")
                print(_("✓ Binary file {desc}: {file}").format(desc=change_desc, file=file_path), file=sys.stderr)

            if quiet:
                advance_to_next_change()
            else:
                advance_to_and_show_next_change()
            return

        # Text hunk - use git apply (item is LineLevelChange here)
        patch_bytes = read_file_bytes(get_selected_hunk_patch_file_path())

        # Extract filename for user feedback (we already have LineLevelChange in item)
        filename = item.path

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
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()


def command_include_file(file: str) -> None:
    """Include (stage) all hunks from the specified file.

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
        # --file with no arg: use selected hunk's file by finding first unblocked hunk
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
    else:
        # Explicit path provided
        target_file = file
    with undo_checkpoint(f"include --file {file}".rstrip()):
        # Load blocklist
        blocklist_path = get_block_list_file_path()
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())

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

                # Record for progress tracking
                record_hunk_included(patch_hash)

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
    advance_to_and_show_next_change()


def command_include_line(line_id_specification: str, file: str | None = None) -> None:
    """Stage only the specified lines to the index.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
        file: Optional file path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    operation_parts = ["include", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)):
        preserve_selected_state = False
        saved_selected_state = None

        if file is None:
            require_selected_hunk()
            line_changes = load_line_changes_from_state()
        else:
            if file == "":
                target_file = get_selected_change_file_path()
                if target_file is None:
                    exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
            else:
                target_file = file
                preserve_selected_state = True
                saved_selected_state = _snapshot_selected_change_state()

            line_changes = cache_file_as_single_hunk(target_file)
            if line_changes is None:
                exit_with_error(_("No changes in file '{file}'.").format(file=target_file))
            line_changes = annotate_with_batch_source(target_file, line_changes)

        requested_ids = parse_line_selection(line_id_specification)
        already_included_ids = set(read_line_ids_file(get_processed_include_ids_file_path()))
        combined_include_ids = already_included_ids | set(requested_ids)

        # Get the selected hunk's base/source snapshots captured when the hunk was loaded.
        hunk_base_content = read_file_bytes(get_index_snapshot_file_path())
        hunk_source_content = read_file_bytes(get_working_tree_snapshot_file_path())

        index_result = run_git_command(
            ["show", f":{line_changes.path}"],
            check=False,
            text_output=False,
        )
        current_index_content = index_result.stdout if index_result.returncode == 0 else b""

        semantic_attempt = try_build_semantic_selection_for_selected_hunk(
            line_changes=line_changes,
            selected_display_ids=combined_include_ids,
            selected_hunk_base_content=hunk_base_content,
            selected_hunk_source_content=hunk_source_content,
            current_index_content=current_index_content,
        )
        if semantic_attempt.used_semantic_staging:
            log_journal(
                "include_line_semantic_staging_used",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
                pairing_mode=semantic_attempt.realization.pairing_mode if semantic_attempt.realization else None,
            )
            target_index_content = semantic_attempt.realized_content or b""
        else:
            # Semantic partial staging is a best-effort enhancement over legacy line
            # staging. If semantic interpretation is ambiguous or unsupported, fall
            # back to the legacy raw staging path instead of failing. Staging
            # working-tree changes into the index should remain permissive.
            log_journal(
                "include_line_semantic_staging_declined",
                file_path=line_changes.path,
                selected_ids=sorted(combined_include_ids),
                reason=semantic_attempt.reason,
            )
            target_index_content = build_target_index_content_bytes_with_selected_lines(
                line_changes,
                combined_include_ids,
                hunk_base_content,
            )

        update_index_with_blob_content(line_changes.path, target_index_content)

        if preserve_selected_state:
            _restore_selected_change_state(saved_selected_state)
        else:
            # Update processed include IDs only when the selected display remains
            # current for incremental line inclusion.
            write_line_ids_file(get_processed_include_ids_file_path(), combined_include_ids)
            recalculate_selected_hunk_for_file(line_changes.path)

    print(_("✓ Included line(s): {lines}").format(lines=line_id_specification), file=sys.stderr)


def _expand_replacement_selection_ids(line_changes, requested_ids: set[int]) -> set[int]:
    """Expand a selection to the smallest adjacent mixed replacement run."""
    selected_indices = [
        index for index, line in enumerate(line_changes.lines)
        if line.id in requested_ids
    ]
    if not selected_indices:
        return requested_ids

    run_start = min(selected_indices)
    run_end = max(selected_indices)

    run_entries = line_changes.lines[run_start:run_end + 1]
    run_kinds = {line.kind for line in run_entries if line.kind in ("+", "-")}

    if run_kinds != {"+", "-"}:
        selected_kind = next(iter(run_kinds), None)
        opposite_kind = "-" if selected_kind == "+" else "+"

        left_index = run_start - 1
        while left_index >= 0 and line_changes.lines[left_index].kind == selected_kind:
            left_index -= 1
        if left_index >= 0 and line_changes.lines[left_index].kind == opposite_kind:
            run_start = left_index

        right_index = run_end + 1
        while right_index < len(line_changes.lines) and line_changes.lines[right_index].kind == selected_kind:
            right_index += 1
        if right_index < len(line_changes.lines) and line_changes.lines[right_index].kind == opposite_kind:
            run_end = right_index

        run_entries = line_changes.lines[run_start:run_end + 1]
        run_kinds = {line.kind for line in run_entries if line.kind in ("+", "-")}
        if run_kinds != {"+", "-"}:
            return requested_ids

    return {
        line.id
        for line in run_entries
        if line.id is not None
    }


def _apply_include_line_replacement(
    line_changes,
    *,
    line_id_specification: str,
    replacement_text: str,
    hunk_base_content: bytes,
    hunk_source_content: bytes,
) -> None:
    """Stage replacement text for selected lines and record session masking."""
    requested_ids = set(parse_line_selection(line_id_specification))
    effective_ids = _expand_replacement_selection_ids(line_changes, requested_ids)

    selected_lines = [line for line in line_changes.lines if line.id in effective_ids]
    if not selected_lines:
        exit_with_error(_("No matching lines found for selection: {ids}").format(ids=line_id_specification))

    ownership = translate_lines_to_batch_ownership(selected_lines)

    try:
        target_index_content = build_target_index_content_bytes_with_replaced_lines(
            line_changes,
            effective_ids,
            replacement_text,
            hunk_base_content,
        )
    except ValueError as error:
        exit_with_error(str(error))

    update_index_with_blob_content(line_changes.path, target_index_content)
    record_consumed_selection(
        line_changes.path,
        source_content=hunk_source_content,
        ownership=ownership,
        replacement_mask={
            "deleted_lines": replacement_text.splitlines(),
            "added_lines": [line.text for line in selected_lines if line.kind == "+"],
        },
    )


def _snapshot_selected_change_state() -> dict[str, bytes | None]:
    """Capture the current selected change cache so explicit file ops can restore it."""
    paths = {
        "patch": get_selected_hunk_patch_file_path(),
        "hash": get_selected_hunk_hash_file_path(),
        "line_state": get_line_changes_json_file_path(),
        "binary": get_selected_binary_file_json_path(),
        "index_snapshot": get_index_snapshot_file_path(),
        "working_snapshot": get_working_tree_snapshot_file_path(),
        "processed_include_ids": get_processed_include_ids_file_path(),
    }
    return {
        name: (read_file_bytes(path) if path.exists() else None)
        for name, path in paths.items()
    }


def _restore_selected_change_state(snapshot: dict[str, bytes | None]) -> None:
    """Restore a previously captured selected change cache."""
    paths = {
        "patch": get_selected_hunk_patch_file_path(),
        "hash": get_selected_hunk_hash_file_path(),
        "line_state": get_line_changes_json_file_path(),
        "binary": get_selected_binary_file_json_path(),
        "index_snapshot": get_index_snapshot_file_path(),
        "working_snapshot": get_working_tree_snapshot_file_path(),
        "processed_include_ids": get_processed_include_ids_file_path(),
    }
    for name, path in paths.items():
        data = snapshot.get(name)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def command_include_line_as(line_id_specification: str, replacement_text: str, file: str | None = None) -> None:
    """Stage a replacement for one contiguous selected line range and mask it."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    operation_parts = ["include", "--line", line_id_specification, "--as", replacement_text]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)):
        if file is None:
            require_selected_hunk()
            line_changes = load_line_changes_from_state()
            hunk_base_content = read_file_bytes(get_index_snapshot_file_path())
            hunk_source_content = read_file_bytes(get_working_tree_snapshot_file_path())

            _apply_include_line_replacement(
                line_changes,
                line_id_specification=line_id_specification,
                replacement_text=replacement_text,
                hunk_base_content=hunk_base_content,
                hunk_source_content=hunk_source_content,
            )

            write_line_ids_file(get_processed_include_ids_file_path(), set())
            recalculate_selected_hunk_for_file(line_changes.path)
        else:
            if file == "":
                target_file = get_selected_change_file_path()
                if target_file is None:
                    exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
                preserve_selected_state = False
            else:
                target_file = file
                preserve_selected_state = True

            saved_selected_state = _snapshot_selected_change_state() if preserve_selected_state else None

            cached_lines = cache_file_as_single_hunk(target_file)
            if cached_lines is None:
                exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

            annotated_changes = annotate_with_batch_source(target_file, cached_lines)
            hunk_base_result = run_git_command(
                ["show", f":{target_file}"],
                check=False,
                text_output=False,
            )
            hunk_base_content = hunk_base_result.stdout if hunk_base_result.returncode == 0 else b""
            hunk_source_content = read_file_bytes(get_git_repository_root_path() / target_file)

            _apply_include_line_replacement(
                annotated_changes,
                line_id_specification=line_id_specification,
                replacement_text=replacement_text,
                hunk_base_content=hunk_base_content,
                hunk_source_content=hunk_source_content,
            )

            if preserve_selected_state:
                _restore_selected_change_state(saved_selected_state)
            else:
                write_line_ids_file(get_processed_include_ids_file_path(), set())
                recalculate_selected_hunk_for_file(target_file)

    print(
        _("✓ Included line(s) as replacement: {lines}").format(lines=line_id_specification),
        file=sys.stderr,
    )


def command_include_to_batch(batch_name: str, line_ids: str | None = None, file: str | None = None, *, quiet: bool = False) -> None:
    """Save selected changes to batch instead of staging.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to include
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
    """
    require_git_repository()
    ensure_state_directory_exists()
    operation_parts = ["include", "--to", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
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
                # --file without --line: include entire file
                _command_include_file_to_batch(batch_name, target_file, quiet=quiet)
            else:
                # --file with --line: include specific lines from file
                _command_include_file_lines_to_batch(batch_name, target_file, line_ids, quiet=quiet)
        else:
            # Hunk-scoped operation (selected behavior)
            if line_ids is not None:
                _command_include_lines_to_batch(batch_name, line_ids, quiet=quiet)
            else:
                # Include entire selected hunk
                _command_include_hunk_to_batch(batch_name, file_only=False, quiet=quiet)


def _command_include_file_to_batch(batch_name: str, file_path: str, *, quiet: bool = False) -> None:
    """Include entire file to batch (internal helper for file-scoped operations)."""

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", file_path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Collect ALL hunks from this file (live working tree state)
    all_lines_to_batch = []

    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color", "HEAD", "--", file_path])):
        patch_bytes_loop = patch.to_patch_bytes()
        hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
        all_lines_to_batch.extend(hunk_lines.lines)

    if not all_lines_to_batch:
        if not quiet:
            print(_("No changes in file '{file}' to include.").format(file=file_path), file=sys.stderr)
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
            _("Cannot include file to batch: batch source is stale and remapping failed.\n"
              "File: {file}\nBatch: {batch}\nError: {error}").format(
                file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    if not quiet:
        print(_("Included file '{file}' to batch '{batch}'").format(file=file_path, batch=batch_name), file=sys.stderr)

    # Show next hunk
    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def _command_include_file_lines_to_batch(batch_name: str, file_path: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Include specific lines from a file to batch (file-scoped with line IDs)."""

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
            _("Cannot include lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\nBatch: {batch}\nError: {error}").format(
                file=file_path, batch=batch_name, error=str(e))
        )

    # Snapshot file before modifying
    snapshot_file_if_untracked(file_path)

    # Save to batch
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    if not quiet:
        print(_("Included line(s) from file '{file}' to batch '{batch}': {lines}").format(
            file=file_path,
            batch=batch_name,
            lines=line_id_specification
        ), file=sys.stderr)

    # Show next hunk
    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()


def _command_include_lines_to_batch(batch_name: str, line_id_specification: str, *, quiet: bool = False) -> None:
    """Save specific lines to batch (internal helper)."""

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
            _("Cannot include lines to batch: batch source is stale and remapping failed.\n"
              "File: {file}\nBatch: {batch}\nError: {error}").format(
                file=line_changes.path, batch=batch_name, error=str(e))
        )

    # Detect file mode
    ls_result = run_git_command(["ls-files", "-s", "--", line_changes.path], check=False)
    file_mode = "100644"  # default
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            file_mode = parts[0]

    # Save to batch using batch source model
    add_file_to_batch(batch_name, line_changes.path, ownership, file_mode)

    if not quiet:
        print(_("✓ Included line(s) to batch '{name}': {lines}").format(name=batch_name, lines=line_id_specification), file=sys.stderr)

    # Recalculate and show the updated hunk for this file with batched lines filtered out
    recalculate_selected_hunk_for_file(line_changes.path)


def _filter_selected_hunk_excluding_batched_lines(*, quiet: bool = False) -> None:
    """Filter the selected hunk to exclude lines that have been batched and display it."""

    # Apply filtering
    if apply_line_level_batch_filter_to_cached_hunk():
        # All lines were batched, advance to next hunk
        clear_selected_change_state_files()
        if not quiet:
            print(_("No more lines in this hunk."), file=sys.stderr)

        if quiet:
            advance_to_next_change()
        else:
            advance_to_and_show_next_change()
        return

    # Display filtered hunk
    if not quiet:
        line_changes = load_line_changes_from_state()
        if line_changes is not None:
            print_line_level_changes(line_changes)


def _command_include_hunk_to_batch(batch_name: str, file_only: bool = False, *, quiet: bool = False) -> None:
    """Save whole hunk or file to batch (internal helper)."""

    # Auto-create batch if it doesn't exist
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    # Load blocklist
    blocklist_path = get_block_list_file_path()
    blocklist_text = read_text_file_contents(blocklist_path)
    blocked_hashes = set(blocklist_text.splitlines())

    # Stream diff to find first non-blocked hunk
    selected_patch = None
    selected_hash = None
    for patch in parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])):
        patch_bytes = patch.to_patch_bytes()
        patch_hash = compute_stable_hunk_hash(patch_bytes)
        if patch_hash not in blocked_hashes:
            selected_patch = patch
            selected_hash = patch_hash
            break

    if selected_patch is None:
        print(_("No changes to process."), file=sys.stderr)
        return

    # Get the file path
    file_path = selected_patch.new_path

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
            line_changes = build_line_changes_from_patch_bytes(patch_bytes_loop, annotator=annotate_with_batch_source)
            all_lines_to_batch.extend(line_changes.lines)
            all_display_ids_to_batch.update(line.id for line in line_changes.lines if line.id is not None)
            patches_to_process.append((patch_bytes_loop, patch_hash))
    else:
        # Just selected hunk
        patch_bytes_selected = selected_patch.to_patch_bytes()
        line_changes = build_line_changes_from_patch_bytes(patch_bytes_selected, annotator=annotate_with_batch_source)
        all_lines_to_batch = line_changes.lines
        all_display_ids_to_batch = {line.id for line in line_changes.lines if line.id is not None}
        patches_to_process = [(patch_bytes_selected, selected_hash)]

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
            _("Cannot include to batch: batch source is stale and remapping failed.\n"
              "File: {file}\nBatch: {batch}\nError: {error}").format(
                file=file_path, batch=batch_name, error=str(e))
        )

    # Save to batch using batch source model (once, with all accumulated data)
    add_file_to_batch(batch_name, file_path, ownership, file_mode)

    # Mark hunks as processed
    for patch_bytes_item, patch_hash in patches_to_process:
        # Mark this hunk as processed
        append_lines_to_file(blocklist_path, [patch_hash])

        # Record hunk as skipped for progress tracking
        hunk_lines = build_line_changes_from_patch_bytes(patch_bytes_item)
        record_hunk_skipped(hunk_lines, patch_hash)

    # Print success message
    if not quiet:
        if file_only:
            msg = ngettext(
                "✓ {count} hunk from {file} saved to batch '{name}'",
                "✓ {count} hunks from {file} saved to batch '{name}'",
                len(patches_to_process)
            ).format(count=len(patches_to_process), file=file_path, name=batch_name)
            print(msg, file=sys.stderr)
        else:
            print(_("✓ Hunk saved to batch '{name}'").format(name=batch_name), file=sys.stderr)

    if quiet:
        advance_to_next_change()
    else:
        advance_to_and_show_next_change()
