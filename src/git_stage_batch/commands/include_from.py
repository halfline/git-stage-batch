"""Include from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from ..batch.merge import merge_batch
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.selection import (
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    select_batch_ownership_for_display_ids,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.validation import batch_exists
from ..data.hunk_tracking import render_batch_file_display
from ..data.undo import undo_checkpoint
from ..exceptions import exit_with_error, MergeError, CommandError, AtomicUnitError, BatchMetadataError
from ..i18n import _
from ..staging.operations import update_index_with_blob_content
from ..utils.git import require_git_repository, run_git_command


def command_include_from_batch(batch_name: str, line_ids: Optional[str] = None, file: Optional[str] = None) -> None:
    """Stage batch changes to index using structural merge.

    Args:
        batch_name: Name of batch to include from
        line_ids: Optional line IDs to include (requires single-file context)
        file: Optional file path to select from batch.
              If None, includes all files in batch.
    """
    require_git_repository()

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

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "include"
    )

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_include = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        # Use pure render helper to get gutter ID mapping (no side effects)
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        rendered = render_batch_file_display(batch_name, file_path_for_render)
        if rendered:
            # Translate gutter IDs (what user sees) to selection IDs (internal)
            selection_ids_to_include = set()
            for gutter_id in selected_ids:
                if gutter_id in rendered.gutter_to_selection_id:
                    selection_ids_to_include.add(rendered.gutter_to_selection_id[gutter_id])
                else:
                    exit_with_error(_("Line ID {id} not found or not individually mergeable").format(id=gutter_id))
    operation_parts = ["include", "--from", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        # Apply all files in batch
        failed_files = []

        for file_path, file_meta in files.items():
            try:
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
                if index_result.returncode == 0:
                    index_content = index_result.stdout
                else:
                    index_content = b""

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
                    continue

                # Perform structural merge
                merged_content = merge_batch(
                    batch_source_content,
                    ownership,
                    index_content
                )

                # Update index with merged content
                update_index_with_blob_content(file_path, merged_content)

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

    if line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
