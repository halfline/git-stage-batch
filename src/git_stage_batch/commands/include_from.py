"""Include from batch command implementation."""

from __future__ import annotations

import os
import sys
from typing import Optional

from ..batch.merge import merge_batch
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.query import get_batch_commit_sha
from ..batch.replacement import build_replacement_batch_view
from ..batch.selection import (
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    select_batch_ownership_for_display_ids,
    translate_batch_file_gutter_ids_to_selection_ids,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_target_change_type,
)
from ..data.file_review_state import (
    FileReviewAction,
    resolve_batch_source_action_scope,
)
from ..data.hunk_tracking import (
    render_batch_file_display,
)
from ..editor import (
    EditorBuffer,
    buffer_byte_chunks,
    write_buffer_to_working_tree_path,
)
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import (
    AtomicUnitError,
    BatchMetadataError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ..i18n import _
from ..staging.operations import update_index_with_blob_buffer
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    require_git_repository,
    run_git_command,
)


def _read_binary_file_from_batch(
    batch_name: str,
    file_path: str,
    file_meta: dict,
) -> bytes | None:
    """Read one binary batch target, or return None for a stored deletion."""
    batch_commit = get_batch_commit_sha(batch_name)
    if not batch_commit:
        raise RuntimeError(f"Batch commit not found for batch '{batch_name}'")

    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        return None

    result = run_git_command(
        ["show", f"{batch_commit}:{file_path}"],
        check=False,
        text_output=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Binary file not found in batch commit: {file_path}")

    return result.stdout


def _stage_binary_file_from_batch(
    file_path: str,
    file_meta: dict,
    batch_content: bytes | None,
) -> None:
    """Stage one binary batch target into the index."""
    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        result = run_git_command(["update-index", "--force-remove", "--", file_path], check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to stage binary deletion for {file_path}: {result.stderr}")
        return

    if batch_content is None:
        raise RuntimeError(f"Binary file not found in batch commit: {file_path}")

    blob_hash = create_git_blob([batch_content])
    file_mode = file_meta.get("mode", "100644")
    run_git_command(["update-index", "--add", "--cacheinfo", str(file_mode), blob_hash, file_path])


def _stage_text_file_from_batch(
    file_path: str,
    buffer: bytes | EditorBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Stage a text buffer, optionally forcing the batch target mode."""
    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        result = run_git_command(["update-index", "--force-remove", "--", file_path], check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to stage text deletion for {file_path}: {result.stderr}")
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    if file_mode is None:
        update_index_with_blob_buffer(file_path, buffer)
        return

    blob_hash = create_git_blob(buffer_byte_chunks(buffer))
    run_git_command(["update-index", "--add", "--cacheinfo", file_mode, blob_hash, file_path])


def _write_text_file_from_batch(
    file_path: str,
    buffer: bytes | EditorBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Write one text batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        if os.path.lexists(full_path):
            full_path.unlink()
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    write_buffer_to_working_tree_path(full_path, buffer, mode=file_mode)


def _write_binary_file_from_batch(
    file_path: str,
    file_meta: dict,
    batch_content: bytes | None,
) -> None:
    """Write one binary batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    change_type = file_meta.get("change_type", "modified")

    if change_type == "deleted":
        if os.path.lexists(full_path):
            full_path.unlink()
        return

    if batch_content is None:
        raise RuntimeError(f"Binary file not found in batch commit: {file_path}")

    write_buffer_to_working_tree_path(
        full_path,
        batch_content,
        mode=str(file_meta.get("mode", "100644")),
    )


def _require_contiguous_display_selection(selected_ids: set[int]) -> None:
    """Require one contiguous selected display range for replacement text."""
    if not selected_ids:
        return

    selected_range = list(range(min(selected_ids), max(selected_ids) + 1))
    if sorted(selected_ids) != selected_range:
        exit_with_error(_("Replacement selection must be one contiguous line range."))


def command_include_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    replacement_text: Optional[str] = None,
) -> None:
    """Stage batch changes to index and working tree using structural merge.

    Args:
        batch_name: Name of batch to include from
        line_ids: Optional line IDs to include (requires single-file context)
        file: Optional file path to select from batch.
              If None, includes all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        replacement_text: Optional replacement text for selected batch lines.
    """
    require_git_repository()
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.INCLUDE_FROM_BATCH,
        command_name="include",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    file = scope_resolution.file

    # Refresh index to ensure git's cached stat info is up-to-date
    run_git_command(["update-index", "--refresh"], check=False)

    # Check batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    if not all_files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "include"
    )
    if replacement_text is not None and not selected_ids:
        exit_with_error(_("`include --from --as` requires `--line`."))

    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Include the whole file instead."))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_include = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        if replacement_text is not None:
            _require_contiguous_display_selection(selected_ids)
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        selection_ids_to_include, rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path_for_render,
            selected_ids,
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
    operation_parts = ["include", "--from", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    if replacement_text is not None:
        operation_parts.extend(["--as", replacement_text])
    with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
        # Apply all files in batch
        repo_root = get_git_repository_root_path()
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                if file_meta.get("file_type") == "binary":
                    batch_content = _read_binary_file_from_batch(batch_name, file_path, file_meta)
                    snapshot_file_if_untracked(file_path)
                    _stage_binary_file_from_batch(file_path, file_meta, batch_content)
                    _write_binary_file_from_batch(file_path, file_meta, batch_content)
                    continue

                text_change_type = normalized_text_change_type(file_meta.get("change_type"))

                # Get batch source commit content (as bytes)
                batch_source_commit = file_meta["batch_source_commit"]
                batch_source_result = run_git_command(
                    ["show", f"{batch_source_commit}:{file_path}"],
                    check=False,
                    text_output=False
                )
                if batch_source_result.returncode != 0:
                    failed_files.append(file_path)
                    continue
                batch_source_content = batch_source_result.stdout

                # Get selected index content (as bytes)
                index_result = run_git_command(
                    ["show", f":{file_path}"],
                    check=False,
                    text_output=False
                )
                index_exists = index_result.returncode == 0
                if index_exists:
                    index_content = index_result.stdout
                else:
                    index_content = b""

                # Get selected working tree content (as bytes)
                full_path = repo_root / file_path
                working_exists = os.path.lexists(full_path)
                if working_exists:
                    if full_path.is_symlink():
                        working_content = os.readlink(os.fsencode(full_path))
                    else:
                        working_content = full_path.read_bytes()
                else:
                    working_content = b""

                batch_file_mode = str(file_meta.get("mode", "100644"))
                index_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=index_exists,
                )
                working_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=working_exists,
                )
                if selected_ids is None and text_change_type == TextFileChangeType.DELETED:
                    snapshot_file_if_untracked(file_path)
                    _stage_text_file_from_batch(file_path, None, index_file_mode, text_change_type)
                    _write_text_file_from_batch(file_path, None, working_file_mode, text_change_type)
                    continue

                # Get ownership from metadata, filtered by selected selection IDs if specified
                try:
                    ownership = select_batch_ownership_for_display_ids(
                        file_meta, batch_source_content, selection_ids_to_include
                    )
                except AtomicUnitError as e:
                    # Translate selection IDs to gutter IDs and exit with user-friendly error
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(e, rendered, "include from", batch_name)
                    # No rendered context - show original error
                    exit_with_error(_("Failed to include from batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e)
                    ))

                # If nothing selected for this file, skip it
                if ownership.is_empty():
                    if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                        merged_index_content = b""
                        merged_working_content = b""
                    else:
                        continue
                else:
                    if replacement_text is not None:
                        try:
                            batch_source_content, ownership = build_replacement_batch_view(
                                batch_source_content,
                                ownership,
                                replacement_text,
                            )
                        except ValueError as e:
                            exit_with_error(str(e))

                    # Perform structural merge against both destinations. include --from
                    # is the staged form of apply --from, so the working tree must
                    # receive the selected batch content too.
                    merged_index_content = merge_batch(
                        batch_source_content,
                        ownership,
                        index_content
                    )
                    merged_working_content = merge_batch(
                        batch_source_content,
                        ownership,
                        working_content
                    )

                snapshot_file_if_untracked(file_path)

                # Update index and working tree with their independently merged
                # targets. A selected deleted-text file still represents path
                # absence once the selected deletion leaves that destination empty.
                index_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    merged_index_content,
                )
                working_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    merged_working_content,
                )
                _stage_text_file_from_batch(
                    file_path,
                    merged_index_content,
                    index_file_mode,
                    index_change_type,
                )
                _write_text_file_from_batch(
                    file_path,
                    merged_working_content,
                    working_file_mode,
                    working_change_type,
                )

            except MergeError:
                # Merge conflict - batch created from different file version
                failed_files.append(file_path)
            except CommandError:
                # Re-raise user errors (e.g., partial atomic selection)
                raise
            except Exception as e:
                print(_("Error staging {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
                failed_files.append(file_path)

    if failed_files:
        if len(failed_files) == 1:
            # Check if there are individually mergeable lines to suggest --lines
            file_path = failed_files[0]
            rendered = render_batch_file_display(batch_name, file_path)
            has_mergeable_lines = rendered and len(rendered.gutter_to_selection_id) > 0

            if has_mergeable_lines:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch, "
                             "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    file=file_path
                )
            else:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                    batch=batch_name,
                    file=file_path
                )
            exit_with_error(error_msg)
        else:
            exit_with_error(
                _("Batch '{batch}' contains changes to one or more files that are incompatible with the current working tree. "
                  "Failed for: {files}. "
                  "Use 'git-stage-batch show --from {batch}' to review the batch, "
                  "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    files=', '.join(failed_files)
                )
            )

    if replacement_text is not None and line_ids:
        print(
            _("✓ Staged selected lines as replacement from batch '{name}'").format(name=batch_name),
            file=sys.stderr,
        )
    elif line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
