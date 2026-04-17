"""Show from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.selection import (
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
)
from ..batch.validation import batch_exists
from ..data.hunk_tracking import cache_batch_as_single_hunk, cache_batch_files_generator, cache_rendered_batch_file_display
from ..output import print_line_level_changes
from ..exceptions import exit_with_error, BatchMetadataError
from ..i18n import _
from ..core.models import LineLevelChange
from ..utils.git import require_git_repository


def command_show_from_batch(batch_name: str, line_ids: Optional[str] = None, file: Optional[str] = None) -> None:
    """Show changes from a batch.

    Args:
        batch_name: Name of batch to show
        line_ids: Optional line IDs to filter (requires single-file context)
        file: Optional file path to show from batch.
              If None, shows all files in batch.
    """
    require_git_repository()

    # Check if batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    # Resolve file scope (for consistent --file handling across commands)
    files = resolve_batch_file_scope(batch_name, all_files, file)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "show"
    )

    # Display batch note if present
    if metadata.get("note"):
        print(f"# {metadata['note']}", file=sys.stderr)

    if len(files) == 1:
        # Show specific file from batch
        # Get the resolved file path
        file_path = list(files.keys())[0]

        # Cache that file from batch
        rendered = cache_batch_as_single_hunk(batch_name, file_path=file_path, metadata=metadata)
        if rendered is None:
            print(_("No changes for file '{file}' in batch '{name}'.").format(file=file_path, name=batch_name), file=sys.stderr)
            return

        # Filter by line IDs if specified (for display only)
        if selected_ids:
            # Translate gutter IDs (what user sees) to selection IDs (internal)
            selection_ids = set()
            for gutter_id in selected_ids:
                if gutter_id in rendered.gutter_to_selection_id:
                    selection_ids.add(rendered.gutter_to_selection_id[gutter_id])
                else:
                    exit_with_error(_("Line ID {id} not found or not individually mergeable").format(id=gutter_id))

            # Filter by selection IDs (not gutter IDs)
            filtered_lines = [line for line in rendered.line_changes.lines if line.id in selection_ids]
            if filtered_lines:
                filtered_line_changes = LineLevelChange(
                    path=rendered.line_changes.path,
                    lines=filtered_lines,
                    header=rendered.line_changes.header
                )
                print_line_level_changes(filtered_line_changes, gutter_to_selection_id=rendered.gutter_to_selection_id)
        else:
            print_line_level_changes(rendered.line_changes, gutter_to_selection_id=rendered.gutter_to_selection_id)

        return

    # Show all files in batch
    has_content = False
    first_rendered = None
    for rendered in cache_batch_files_generator(batch_name, metadata=metadata):
        if not has_content:
            has_content = True
            first_rendered = rendered
        else:
            # Print blank line separator between files
            print()

        print_line_level_changes(rendered.line_changes, gutter_to_selection_id=rendered.gutter_to_selection_id)

    # Cache first file for subsequent commands
    if first_rendered is not None:
        cache_rendered_batch_file_display(first_rendered.line_changes.path, first_rendered)
    else:
        # Empty batch
        print(_("Batch '{name}' is empty").format(name=batch_name), file=sys.stderr)
