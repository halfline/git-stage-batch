"""Show from batch command implementation."""

from __future__ import annotations

from typing import Optional

from ..batch import get_batch_diff
from ..output import print_annotated_hunk_with_aligned_gutter, print_colored_patch
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import parse_line_selection
from ..core.models import CurrentLines
from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_into_single_hunk_patches
from ..utils.git import require_git_repository
from ..utils.paths import get_context_lines


def command_show_from_batch(batch_name: str, line_ids: Optional[str] = None, file_only: bool = False) -> None:
    """Show changes from a batch."""
    require_git_repository()

    # Get batch diff
    context_lines = get_context_lines()
    diff = get_batch_diff(batch_name, context_lines)

    if not diff:
        exit_with_error(_("Batch '{name}' is empty or does not exist").format(name=batch_name))

    # If line_ids specified, filter to those lines
    if line_ids:
        # Parse diff into patches to get CurrentLines
        patches = parse_unified_diff_into_single_hunk_patches(diff)
        if not patches:
            exit_with_error(_("No patches found in batch '{name}'").format(name=batch_name))

        # Parse line selection
        selected_ids = parse_line_selection(line_ids)

        # Print each patch with line filtering
        for patch in patches:
            patch_text = patch.to_patch_text()
            current_lines = build_current_lines_from_patch_text(patch_text)

            # Filter to selected lines
            filtered_lines = [line for line in current_lines.lines if line.id in selected_ids]
            if not filtered_lines:
                continue

            # Create filtered CurrentLines with only selected lines
            filtered_current_lines = CurrentLines(
                path=current_lines.path,
                lines=filtered_lines,
                header=current_lines.header
            )

            # Display with annotations
            print_annotated_hunk_with_aligned_gutter(filtered_current_lines)
    else:
        # Show entire diff
        print_colored_patch(diff)
