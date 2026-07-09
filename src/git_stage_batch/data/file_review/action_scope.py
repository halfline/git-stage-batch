"""Action-scope validation for page-aware file reviews."""

from __future__ import annotations

import shlex

from ...core.line_selection import parse_line_selection_ranges
from ...exceptions import CommandError
from ...i18n import _
from . import records as _records
from .action_commands import (
    batch_source_action_command as _batch_source_action_command,
    line_action_command as _line_action_command,
    live_to_batch_action_command as _live_to_batch_action_command,
    show_command_for_review_state as _show_command_for_review_state,
)
from .freshness import (
    review_state_matches_action as _review_state_matches_action,
    selected_change_kind_matches_review_source as _selected_change_kind_matches_review_source,
    selected_change_matches_review_state as _selected_change_matches_review_state,
)
from .selection_validation import (
    shown_review_selections_for_action as _shown_review_selections_for_action,
    validate_review_scoped_line_selection as _validate_review_scoped_line_selection,
)
from .state import (
    clear_last_file_review_state,
    clear_last_file_review_state_if_file_matches,
    read_last_file_review_state,
)
from ..selected_change.store import SelectedChangeKind, read_selected_change_kind


class ReviewScopedSelectionError(CommandError):
    """Raised when a pathless line action is not valid for the current review."""


def _get_selected_change_file_path() -> str | None:
    from ..selected_change.paths import get_selected_change_file_path

    return get_selected_change_file_path()


def line_action_came_from_partial_review(review_state: _records.FileReviewState | None) -> bool:
    """Return whether a line action was validated by a partial file review."""
    return review_state is not None and not review_state.entire_file_shown


def finish_review_scoped_line_action(
    review_state: _records.FileReviewState | None,
    *,
    file_path: str | None = None,
) -> None:
    """Clear review state after a line action unless a partial review must guard follow-ups."""
    if line_action_came_from_partial_review(review_state):
        return
    if file_path is None:
        clear_last_file_review_state()
    else:
        clear_last_file_review_state_if_file_matches(file_path)


def resolve_batch_source_action_scope(
    action: _records.FileReviewAction | str,
    *,
    command_name: str,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
    patterns: list[str] | None,
    extra_action_parts: tuple[str, ...] = (),
) -> _records.ActionScopeResolution:
    """Resolve pathless and implicit-file batch actions against the last batch review."""
    from ..selected_change.clear_reasons import (
        refuse_bare_action_after_file_list,
        refuse_bare_action_after_stale_batch_selection,
    )

    review_action = _records.coerce_review_action(action)
    if patterns is not None:
        return _records.ActionScopeResolution(file=file)

    if file is None:
        action_command = _batch_source_action_command(
            command_name,
            batch_name,
            file_scope=False,
            line_ids=line_ids,
            extra_action_parts=extra_action_parts,
        )
        refuse_bare_action_after_file_list(
            action_command,
            open_command=f"git-stage-batch show --from {shlex.quote(batch_name)} --file PATH",
            source=_records.ReviewSource.BATCH.value,
            batch_name=batch_name,
        )
        refuse_bare_action_after_stale_batch_selection(action_command, batch_name=batch_name)

        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=_records.ReviewSource.BATCH,
                batch_name=batch_name,
            )
            return _records.ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)

        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=_records.ReviewSource.BATCH,
            batch_name=batch_name,
        )
        return _records.ActionScopeResolution(
            file=review_state.file_path if review_state is not None else file,
            review_state=review_state,
        )

    if file == "":
        action_command = _batch_source_action_command(
            command_name,
            batch_name,
            file_scope=True,
            line_ids=line_ids,
            extra_action_parts=extra_action_parts,
        )
        refuse_bare_action_after_file_list(
            action_command,
            open_command=f"git-stage-batch show --from {shlex.quote(batch_name)} --file PATH",
            source=_records.ReviewSource.BATCH.value,
            batch_name=batch_name,
        )
        refuse_bare_action_after_stale_batch_selection(action_command, batch_name=batch_name)

        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=_records.ReviewSource.BATCH,
                batch_name=batch_name,
            )
            return _records.ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)

        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=_records.ReviewSource.BATCH,
            batch_name=batch_name,
        )
        return _records.ActionScopeResolution(
            file=review_state.file_path if review_state is not None else file,
            review_state=review_state,
        )

    return _records.ActionScopeResolution(file=file)


def _format_pages(pages: set[int]) -> str:
    from ...core.line_selection import format_line_ids

    return format_line_ids(sorted(pages))


def _print_stale_or_mismatched_file_review_help(action: str, review_state: _records.FileReviewState) -> None:
    show_command = _show_command_for_review_state(review_state)
    raise ReviewScopedSelectionError(
        _(
            "The file review for {file} no longer matches the selected file view.\n"
            "Line IDs may no longer match.\n\n"
            "Run:\n"
            "  {command}"
        ).format(file=review_state.file_path, command=show_command)
    )


def refuse_live_action_for_batch_selection(action: _records.FileReviewAction | str) -> bool:
    """Refuse bare live actions when the current selection came from a batch view."""
    review_action = _records.coerce_review_action(action)
    if read_selected_change_kind() not in (
        SelectedChangeKind.BATCH_FILE,
        SelectedChangeKind.BATCH_BINARY,
        SelectedChangeKind.BATCH_GITLINK,
    ):
        return False

    review_state = read_last_file_review_state()
    if review_state is not None:
        if review_state.source != _records.ReviewSource.BATCH:
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
        if not _selected_change_matches_review_state(review_state):
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)

        lines = [
            _("The selected file view for {file} came from batch '{batch}', not the live working tree.").format(
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

        whole_file_command = _line_action_command(review_action, review_state, whole_file=True)
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
                    _("If you meant to act on live working-tree changes, open a live file review:"),
                    f"  git-stage-batch show --file {shlex.quote(review_state.file_path)}",
                ]
            )
        raise CommandError("\n".join(lines))

    file_path = _get_selected_change_file_path() or _("the selected file")
    raise CommandError(
        _(
            "The selected file view for {file} came from a batch, not the live working tree.\n"
            "Show the batch file again and use `include --from` or `discard --from`,\n"
            "or open a live file review with:\n"
            "  git-stage-batch show --file {file}"
        ).format(file=file_path)
    )


def refuse_ambiguous_bare_action_after_partial_file_review(action: _records.FileReviewAction | str) -> bool:
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
        ):
            _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
        clear_last_file_review_state()
        return False

    if not _review_state_matches_action(review_state, review_action):
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)

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
        lines.append(_("Pages {pages} were not shown.").format(pages=_format_pages(missing)))
    if selection_specs:
        line_command = _line_action_command(
            review_action, review_state, line_spec=",".join(selection_specs)
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

    whole_file_command = _line_action_command(review_action, review_state, whole_file=True)
    if whole_file_command is not None:
        lines.extend(
            [
                "",
                _("To act on the whole file:"),
                f"  {whole_file_command}",
            ]
        )
    raise CommandError("\n".join(lines))


def resolve_review_file_for_bare_whole_file_action(
    action: _records.FileReviewAction | str,
    *,
    source: _records.ReviewSource | str,
    batch_name: str | None = None,
) -> str | None:
    """Return the reviewed file for a fresh full-file review, or refuse if partial."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return None

    if review_state.source != _records.coerce_review_source(source):
        return None
    if batch_name is not None and review_state.batch_name != batch_name:
        return None

    if refuse_ambiguous_bare_action_after_partial_file_review(action):
        return None
    if read_last_file_review_state() != review_state:
        return None
    return review_state.file_path


def validate_implicit_live_to_batch_file_action(
    action: _records.FileReviewAction | str,
    action_command: str,
    line_id_specification: str | None,
) -> _records.ImplicitLiveToBatchFileActionResult:
    """Validate `--to --file` with no path against the current live review.

    Returns the reviewed file for a full live-file review when the caller should
    use that explicit file path. The boolean is true when the caller should stop
    after a live-action guard handled the request.
    """
    from ..selected_change.clear_reasons import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    review_action = _records.coerce_review_action(action)
    refuse_bare_action_after_file_list(action_command)
    refuse_bare_action_after_auto_advance_disabled(action_command)
    if line_id_specification is None:
        return _records.ImplicitLiveToBatchFileActionResult(
            reviewed_file=resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=_records.ReviewSource.FILE_VS_HEAD,
            ),
        )
    if refuse_live_action_for_batch_selection(review_action):
        return _records.ImplicitLiveToBatchFileActionResult(should_stop=True)
    review_state = validate_pathless_review_line_action(
        review_action,
        line_id_specification,
        source=_records.ReviewSource.FILE_VS_HEAD,
    )
    return _records.ImplicitLiveToBatchFileActionResult(review_state=review_state)


def resolve_live_to_batch_action_scope(
    action: _records.FileReviewAction | str,
    *,
    command_name: str,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
) -> _records.ActionScopeResolution:
    """Resolve pathless and implicit-file live-to-batch actions against live reviews."""
    from ..selected_change.clear_reasons import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    review_action = _records.coerce_review_action(action)
    if file is None:
        action_command = _live_to_batch_action_command(
            command_name,
            batch_name,
            file_scope=False,
            line_ids=line_ids,
        )
        refuse_bare_action_after_file_list(action_command)
        refuse_bare_action_after_auto_advance_disabled(action_command)
        if refuse_live_action_for_batch_selection(review_action):
            return _records.ActionScopeResolution(file=file, should_stop=True)
        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=_records.ReviewSource.FILE_VS_HEAD,
            )
            return _records.ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)
        review_state = validate_pathless_review_line_action(
            review_action,
            line_ids,
            source=_records.ReviewSource.FILE_VS_HEAD,
        )
        return _records.ActionScopeResolution(file=file, review_state=review_state)

    if file == "":
        action_command = _live_to_batch_action_command(
            command_name,
            batch_name,
            file_scope=True,
            line_ids=line_ids,
        )
        action_result = validate_implicit_live_to_batch_file_action(
            review_action,
            action_command,
            line_ids,
        )
        if action_result.should_stop:
            return _records.ActionScopeResolution(file=file, should_stop=True)
        return _records.ActionScopeResolution(
            file=action_result.reviewed_file if action_result.reviewed_file is not None else file,
            review_state=action_result.review_state,
        )

    return _records.ActionScopeResolution(file=file)


def resolve_live_line_action_scope(
    action: _records.FileReviewAction | str,
    *,
    action_command: str,
    line_id_specification: str,
    file: str | None,
    source: _records.ReviewSource | str | None = None,
    batch_name: str | None = None,
    validate_pathless_before_live_guard: bool = False,
) -> _records.ActionScopeResolution:
    """Validate a pathless or implicit-file live line action against review state."""
    from ..selected_change.clear_reasons import (
        refuse_bare_action_after_auto_advance_disabled,
        refuse_bare_action_after_file_list,
    )

    if file not in (None, ""):
        return _records.ActionScopeResolution(file=file)

    review_action = _records.coerce_review_action(action)
    refuse_bare_action_after_file_list(action_command)
    refuse_bare_action_after_auto_advance_disabled(action_command)

    if file is None and validate_pathless_before_live_guard:
        review_state = validate_pathless_review_line_action(
            review_action,
            line_id_specification,
            source=source,
            batch_name=batch_name,
        )
        if refuse_live_action_for_batch_selection(review_action):
            return _records.ActionScopeResolution(file=file, review_state=review_state, should_stop=True)
        return _records.ActionScopeResolution(file=file, review_state=review_state)

    if refuse_live_action_for_batch_selection(review_action):
        return _records.ActionScopeResolution(file=file, should_stop=True)

    review_state = validate_pathless_review_line_action(
        review_action,
        line_id_specification,
        source=source,
        batch_name=batch_name,
    )
    return _records.ActionScopeResolution(file=file, review_state=review_state)


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
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
    if batch_name is not None and review_state.batch_name != batch_name:
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
    if not _review_state_matches_action(review_state, review_action):
        _print_stale_or_mismatched_file_review_help(review_action.value, review_state)
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
            _("The selected file view for {file} came from batch '{batch}', not the live working tree.").format(
                file=review_state.file_path,
                batch=review_state.batch_name,
            )
        ]
        line_command = _line_action_command(review_action, review_state, line_spec=line_id_specification)
        if line_command is not None:
            lines.extend(["", _("To act on the batch file:"), f"  {line_command}"])
        else:
            lines.extend(
                [
                    "",
                    _("Batch reviews do not support this action."),
                    _("If you meant to act on live working-tree changes, open a live file review:"),
                    f"  git-stage-batch show --file {shlex.quote(review_state.file_path)}",
                ]
            )
        raise CommandError("\n".join(lines))

    try:
        requested_ids = parse_line_selection_ranges(line_id_specification)
    except ValueError as error:
        raise CommandError(str(error)) from error

    valid_selections = _shown_review_selections_for_action(review_state, review_action)
    _validate_review_scoped_line_selection(requested_ids, valid_selections)
    return review_state
