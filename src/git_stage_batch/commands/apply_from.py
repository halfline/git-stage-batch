"""Apply from batch command implementation."""

from __future__ import annotations

import stat
import sys
from typing import Optional

from ..batch.merge import merge_batch
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.query import get_batch_commit_sha
from ..batch.selection import (
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    select_batch_ownership_for_display_ids,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_target_change_type,
)
from ..data.hunk_tracking import render_batch_file_display
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, MergeError, CommandError, AtomicUnitError, BatchMetadataError
from ..i18n import _
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command


def _apply_working_tree_file_mode(full_path, file_mode: str | None) -> None:
    """Apply a normal Git file mode to a working-tree file."""
    if file_mode is None:
        return
    current_mode = full_path.stat().st_mode
    if file_mode == "100755":
        full_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    else:
        full_path.chmod(current_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _apply_binary_file_from_batch(batch_name: str, file_path: str, file_meta: dict) -> None:
    """Apply binary file from batch to working tree.

    Binary files are atomic units - whole file replacements stored in batch commit tree.
    This function reads the file from the batch commit tree and writes it to the working tree,
    or deletes the working tree file if the batch represents a deletion.

    Args:
        batch_name: Name of batch containing binary file
        file_path: Path to binary file
        file_meta: File metadata from batch

    Raises:
        RuntimeError: If batch commit not found or file cannot be read
    """
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    # Get batch commit SHA (canonical state for binaries)
    batch_commit = get_batch_commit_sha(batch_name)
    if not batch_commit:
        raise RuntimeError(f"Batch commit not found for batch '{batch_name}'")

    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        if full_path.exists():
            full_path.unlink()
            print(_("✓ Deleted binary file: {file}").format(file=file_path), file=sys.stderr)
        return

    # Read file from batch commit tree
    result = run_git_command(
        ["show", f"{batch_commit}:{file_path}"],
        check=False,
        text_output=False
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Binary file metadata for {file_path} says {change_type}, "
            "but the batch content is missing"
        )

    # File exists in batch commit - write to working tree
    binary_content = result.stdout
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(binary_content)

    _apply_working_tree_file_mode(full_path, str(file_meta.get("mode", "100644")))

    if change_type == "added":
        print(_("✓ Applied new binary file: {file}").format(file=file_path), file=sys.stderr)
    else:
        print(_("✓ Replaced binary file: {file}").format(file=file_path), file=sys.stderr)


def _write_text_file_from_batch(
    file_path: str,
    content: bytes | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Write one text batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        if full_path.exists():
            full_path.unlink()
        return

    if content is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    _apply_working_tree_file_mode(full_path, file_mode)


def command_apply_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Apply batch changes to working tree using structural merge.

    Args:
        batch_name: Name of batch to apply from
        line_ids: Optional line IDs to apply (requires single-file context)
        file: Optional file path to select from batch.
              If None, applies all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()

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

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "apply"
    )

    # Reject line selection for binary files (binary files are atomic units)
    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Binary files must be applied as complete units."))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_apply = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        # Use pure render helper to get gutter ID mapping (no side effects)
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        rendered = render_batch_file_display(batch_name, file_path_for_render)
        if rendered:
            # Translate gutter IDs (what user sees) to selection IDs (internal)
            selection_ids_to_apply = set()
            for gutter_id in selected_ids:
                if gutter_id in rendered.gutter_to_selection_id:
                    selection_ids_to_apply.add(rendered.gutter_to_selection_id[gutter_id])
                else:
                    exit_with_error(_("Line ID {id} not found or not individually mergeable").format(id=gutter_id))
    operation_parts = ["apply", "--from", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
        # Apply all files in batch
        repo_root = get_git_repository_root_path()
        failed_files = []

        for file_path, file_meta in files.items():
            try:
                # Snapshot file before modifying
                snapshot_file_if_untracked(file_path)

                # Binary files are atomic units - handle separately without ownership/merge logic
                if file_meta.get("file_type") == "binary":
                    _apply_binary_file_from_batch(batch_name, file_path, file_meta)
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

                # Get selected working tree content (as bytes)
                full_path = repo_root / file_path
                working_exists = full_path.exists()
                if working_exists:
                    working_content = full_path.read_bytes()
                else:
                    working_content = b""

                file_mode = mode_for_text_materialization(
                    str(file_meta.get("mode", "100644")),
                    selected_ids,
                    destination_exists=working_exists,
                )
                if selected_ids is None and text_change_type == TextFileChangeType.DELETED:
                    _write_text_file_from_batch(file_path, None, file_mode, text_change_type)
                    continue

                # Get ownership from metadata, filtered by selected selection IDs if specified
                try:
                    ownership = select_batch_ownership_for_display_ids(
                        file_meta, batch_source_content, selection_ids_to_apply
                    )
                except AtomicUnitError as e:
                    # Translate selection IDs to gutter IDs and exit with user-friendly error
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(e, rendered, "apply", batch_name)
                    # No rendered context - show original error
                    exit_with_error(_("Failed to apply batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e)
                    ))

                # If nothing selected for this file, skip it
                if ownership.is_empty():
                    if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                        merged_content = b""
                    else:
                        continue
                else:
                    # Perform structural merge
                    merged_content = merge_batch(
                        batch_source_content,
                        ownership,
                        working_content
                    )

                # Write merged content to working tree (bytes). A selected
                # deleted-text file still represents path absence once the
                # selected deletion leaves the destination empty.
                effective_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    merged_content,
                )
                _write_text_file_from_batch(
                    file_path,
                    merged_content,
                    file_mode,
                    effective_change_type,
                )

            except MergeError:
                # Merge conflict - batch created from different file version
                failed_files.append(file_path)
            except CommandError:
                # Re-raise user errors (e.g., partial atomic selection)
                raise
            except Exception as e:
                print(_("Error applying {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
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

    if line_ids:
        print(_("✓ Applied selected lines from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Applied changes for {file} from batch '{name}' to working tree").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Applied changes from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
