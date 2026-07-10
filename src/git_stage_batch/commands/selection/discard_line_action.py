"""Live discard line action orchestration."""

from __future__ import annotations

import sys

from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.undo import undo_checkpoint
from ...i18n import _
from . import discard_line_selection as _discard_line_selection
from .selected_hunk_refresh import refresh_selected_hunk_after_line_action


def discard_live_line_selection(
    line_id_specification: str,
    file: str | None = None,
    *,
    review_state,
    auto_advance: bool | None = None,
) -> None:
    """Discard selected lines from the live working-tree view."""
    operation_parts = ["discard", "--line", line_id_specification]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)):
        file_path = _discard_line_selection.discard_worktree_line_selection(
            line_id_specification,
            file=file,
        )
        print(
            _("✓ Discarded line(s): {lines} from {file}").format(
                lines=line_id_specification,
                file=file_path,
            ),
            file=sys.stderr,
        )
        refresh_selected_hunk_after_line_action(
            file_path,
            auto_advance=auto_advance,
        )
        finish_review_scoped_line_action(review_state)
