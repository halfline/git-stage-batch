"""Line-selection support for discard commands."""

from __future__ import annotations

import os

from ...batch.selection import require_line_selection_in_view
from ...core.line_selection import parse_line_selection
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import load_working_tree_file_as_buffer
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.content_buffers import build_target_working_tree_buffer_from_lines
from ...utils.git_repository import get_git_repository_root_path
from . import discard_file_selection as _discard_file_selection
from . import discard_line_publication as _discard_line_publication


def discard_worktree_line_selection(
    line_id_specification: str,
    *,
    file: str | None = None,
) -> str:
    """Discard selected line IDs from the working tree."""
    if file is None:
        require_selected_hunk()
        line_changes = load_line_changes_from_state()
    else:
        if file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(
                    _("No selected hunk. Run 'show' first or specify file path.")
                )
        else:
            target_file = file

        line_changes = _discard_file_selection.load_explicit_file_selection(
            target_file
        )

    requested_ids = parse_line_selection(line_id_specification)
    require_line_selection_in_view(
        line_changes,
        set(requested_ids),
        line_id_specification=line_id_specification,
    )

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not os.path.lexists(working_file_path):
        exit_with_error(
            _("File not found in working tree: {file}").format(
                file=line_changes.path
            )
        )

    with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
        target_working_buffer = build_target_working_tree_buffer_from_lines(
            line_changes,
            set(requested_ids),
            working_lines,
        )

    with target_working_buffer:
        _discard_line_publication.publish_worktree_line_discard(
            line_changes.path,
            working_file_path,
            target_working_buffer,
        )

    return line_changes.path
