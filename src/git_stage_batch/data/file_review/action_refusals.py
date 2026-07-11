"""Refusal helpers for file-review action safety."""

from __future__ import annotations

import shlex

from ...core.line_selection import format_line_ids
from ...exceptions import CommandError
from ...i18n import _
from . import records as _records
from .action_commands import (
    line_action_command as _line_action_command,
    show_command_for_review_state as _show_command_for_review_state,
)
from .freshness import (
    review_state_matches_action as _review_state_matches_action,
    selected_change_kind_matches_review_source as _selected_change_kind_matches_review_source,
    selected_change_matches_review_state as _selected_change_matches_review_state,
)
from .line_action_validation import (
    raise_stale_or_mismatched_file_review as _raise_stale_or_mismatched_file_review,
)
from .state import clear_last_file_review_state, read_last_file_review_state
from ..selected_change.store import SelectedChangeKind, read_selected_change_kind


def _get_selected_change_file_path() -> str | None:
    from ..selected_change.paths import get_selected_change_file_path

    return get_selected_change_file_path()


def _format_pages(pages: set[int]) -> str:
    return format_line_ids(sorted(pages))


def refuse_live_action_for_batch_selection(action: _records.FileReviewAction | str) -> bool:
    """Refuse bare live actions when the current selection came from a batch view."""
    review_action = _records.coerce_review_action(action)
    if read_selected_change_kind() not in (
        SelectedChangeKind.BATCH_FILE,
        SelectedChangeKind.BATCH_BINARY,
        SelectedChangeKind.BATCH_GITLINK,
        SelectedChangeKind.BATCH_MODE,
    ):
        return False

    review_state = read_last_file_review_state()
    if review_state is not None:
        if review_state.source != _records.ReviewSource.BATCH:
            _raise_stale_or_mismatched_file_review(review_state)
        if not _selected_change_matches_review_state(review_state):
            _raise_stale_or_mismatched_file_review(review_state)

        lines = [
            _(
                "The selected file view for {file} came from batch '{batch}', "
                "not the live working tree."
            ).format(
                file=review_state.file_path,
                batch=review_state.batch_name,
            )
        ]
        if not review_state.entire_file_shown:
            lines.extend(
                [
                    "",
                    _("To review all pages from the batch:"),
                    f"  {_show_command_for_review_state(review_state, page='all')}",
                ]
            )

        whole_file_command = _line_action_command(
            review_action,
            review_state,
            whole_file=True,
        )
        if whole_file_command is not None:
            lines.extend(
                [
                    "",
                    _("To act on the batch file:"),
                    f"  {whole_file_command}",
                ]
            )
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

    file_path = _get_selected_change_file_path() or _("the selected file")
    raise CommandError(
        _(
            "The selected file view for {file} came from a batch, "
            "not the live working tree.\n"
            "Show the batch file again and use `include --from` or "
            "`discard --from`,\n"
            "or open a live file review with:\n"
            "  git-stage-batch show --file {file}"
        ).format(file=file_path)
    )


def refuse_ambiguous_bare_action_after_partial_file_review(
    action: _records.FileReviewAction | str,
) -> bool:
    """Refuse pathless whole-file actions after a partial file review."""
    review_action = _records.coerce_review_action(action)
    review_state = read_last_file_review_state()
    if review_state is None:
        return False

    selected_kind = read_selected_change_kind()
    if not _selected_change_kind_matches_review_source(selected_kind, review_state):
        if selected_kind in (
            SelectedChangeKind.FILE,
            SelectedChangeKind.BATCH_FILE,
            SelectedChangeKind.BATCH_BINARY,
            SelectedChangeKind.BATCH_GITLINK,
            SelectedChangeKind.BATCH_MODE,
        ):
            _raise_stale_or_mismatched_file_review(review_state)
        clear_last_file_review_state()
        return False

    if not _review_state_matches_action(review_state, review_action):
        _raise_stale_or_mismatched_file_review(review_state)

    if review_state.entire_file_shown:
        return False

    shown = set(review_state.shown_pages)
    missing = set(range(1, review_state.page_count + 1)) - shown
    complete_selections = [
        selection
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(shown)
    ]
    selection_specs = [
        _format_pages(set(selection.display_ids))
        for selection in complete_selections
    ]

    lines = [
        _("Only pages {shown} of {count} of {file} were shown.").format(
            shown=_format_pages(shown),
            count=review_state.page_count,
            file=review_state.file_path,
        )
    ]
    if missing:
        lines.append(
            _("Pages {pages} were not shown.").format(pages=_format_pages(missing))
        )
    if selection_specs:
        line_command = _line_action_command(
            review_action,
            review_state,
            line_spec=",".join(selection_specs),
        )
        if line_command is not None:
            lines.extend(
                [
                    "",
                    _("To act on complete changes shown here:"),
                    f"  {line_command}",
                ]
            )
    lines.extend(
        [
            "",
            _("To review all pages:"),
            f"  {_show_command_for_review_state(review_state, page='all')}",
        ]
    )

    whole_file_command = _line_action_command(
        review_action,
        review_state,
        whole_file=True,
    )
    if whole_file_command is not None:
        lines.extend(
            [
                "",
                _("To act on the whole file:"),
                f"  {whole_file_command}",
            ]
        )
    raise CommandError("\n".join(lines))
