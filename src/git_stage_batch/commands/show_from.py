"""Show from batch command implementation."""

from __future__ import annotations

from typing import Optional

from ..batch import get_batch_diff
from ..batch.validation import batch_exists
from ..output import print_line_level_changes
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import parse_line_selection
from ..core.models import LineLevelChange
from ..core.diff_parser import build_line_changes_from_patch_text, parse_unified_diff_into_single_hunk_patches
from ..utils.git import require_git_repository
from ..utils.paths import get_context_lines


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

    # Get batch diff
    context_lines = get_context_lines()
    diff = get_batch_diff(batch_name, context_lines)

    if not diff:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    # Parse diff into patches
    patches = parse_unified_diff_into_single_hunk_patches(diff)
    if not patches:
        exit_with_error(_("No patches found in batch '{name}'").format(name=batch_name))

    # Filter to specific file if requested
    if file:
        patches = [p for p in patches if p.new_path == file or p.old_path == file]
        if not patches:
            exit_with_error(_("No changes for file '{file}' in batch '{name}'.").format(
                file=file, name=batch_name))

    # Parse line selection
    selected_ids = None
    if line_ids:
        selected_ids = parse_line_selection(line_ids)

    # Display patches
    for patch in patches:
        patch_text = patch.to_patch_text()
        line_changes = build_line_changes_from_patch_text(patch_text)

        if selected_ids:
            # Filter to selected lines
            filtered_lines = [line for line in line_changes.lines if line.id in selected_ids]
            if not filtered_lines:
                continue

            filtered_line_changes = LineLevelChange(
                path=line_changes.path,
                lines=filtered_lines,
                header=line_changes.header
            )
            print_line_level_changes(filtered_line_changes)
        else:
            print_line_level_changes(line_changes)
