"""Pathless line-action validation for file reviews."""

from __future__ import annotations

import shlex

from ...core.line_selection import parse_line_selection_ranges
from ...exceptions import CommandError
from ...i18n import _
from . import records as _records
from .action_commands import (
    line_action_command as _line_action_command,
    show_command_for_review_state as _show_command_for_review_state,
)
from .freshness import review_state_matches_action as _review_state_matches_action
from .selection_validation import (
    shown_review_selections_for_action as _shown_review_selections_for_action,
    validate_review_scoped_line_selection as _validate_review_scoped_line_selection,
)
from .state import read_last_file_review_state


class ReviewScopedSelectionError(CommandError):
    """Raised when a pathless line action is not valid for the current review."""


def raise_stale_or_mismatched_file_review(
    review_state: _records.FileReviewState,
) -> None:
    """Raise when a persisted file review no longer matches selected state."""
    show_command = _show_command_for_review_state(review_state)
    raise ReviewScopedSelectionError(
        _(
            "The file review for {file} no longer matches the selected file view.\n"
            "Line IDs may no longer match.\n\n"
            "Run:\n"
            "  {command}"
        ).format(file=review_state.file_path, command=show_command)
    )


def validate_pathless_review_line_action(
    action: _records.FileReviewAction | str,
    line_id_specification: str,
    *,
    source: _records.ReviewSource | str | None = None,
    batch_name: str | None = None,
) -> _records.FileReviewState | None:
    """Validate pathless --line against the last file review."""
    review_action = _records.coerce_review_action(action)
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if (
        source is not None
        and review_state.source != _records.coerce_review_source(source)
    ):
        raise_stale_or_mismatched_file_review(review_state)
    if batch_name is not None and review_state.batch_name != batch_name:
        raise_stale_or_mismatched_file_review(review_state)
    if not _review_state_matches_action(review_state, review_action):
        raise_stale_or_mismatched_file_review(review_state)
    if (
        review_state.source == _records.ReviewSource.BATCH
        and review_action in (
            _records.FileReviewAction.INCLUDE,
            _records.FileReviewAction.SKIP,
            _records.FileReviewAction.DISCARD,
            _records.FileReviewAction.INCLUDE_TO_BATCH,
            _records.FileReviewAction.DISCARD_TO_BATCH,
        )
        and not any(review_action in selection.actions for selection in review_state.selections)
    ):
        lines = [
            _(
                "The selected file view for {file} came from batch '{batch}', "
                "not the live working tree."
            ).format(
                file=review_state.file_path,
                batch=review_state.batch_name,
            )
        ]
        line_command = _line_action_command(
            review_action,
            review_state,
            line_spec=line_id_specification,
        )
        if line_command is not None:
            lines.extend(["", _("To act on the batch file:"), f"  {line_command}"])
        else:
            lines.extend(
                [
                    "",
                    _("Batch reviews do not support this action."),
                    _(
                        "If you meant to act on live working-tree changes, "
                        "open a live file review:"
                    ),
                    f"  git-stage-batch show --file {shlex.quote(review_state.file_path)}",
                ]
            )
        raise CommandError("\n".join(lines))

    try:
        requested_ids = parse_line_selection_ranges(line_id_specification)
    except ValueError as error:
        raise CommandError(str(error)) from error

    valid_selections = _shown_review_selections_for_action(
        review_state,
        review_action,
    )
    _validate_review_scoped_line_selection(requested_ids, valid_selections)
    return review_state
