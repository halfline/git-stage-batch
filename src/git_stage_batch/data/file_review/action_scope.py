"""Action-scope validation for page-aware file reviews."""

from __future__ import annotations

import shlex

from . import records as _records
from .action_commands import (
    batch_source_action_command as _batch_source_action_command,
    live_to_batch_action_command as _live_to_batch_action_command,
)
from .action_refusals import (
    refuse_ambiguous_bare_action_after_partial_file_review as _refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection as _refuse_live_action_for_batch_selection,
)
from .line_action_validation import (
    validate_pathless_review_line_action as _validate_pathless_review_line_action,
)
from .state import (
    clear_last_file_review_state,
    clear_last_file_review_state_if_file_matches,
    read_last_file_review_state,
)


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

        review_state = _validate_pathless_review_line_action(
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

        review_state = _validate_pathless_review_line_action(
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

    if _refuse_ambiguous_bare_action_after_partial_file_review(action):
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
    if _refuse_live_action_for_batch_selection(review_action):
        return _records.ImplicitLiveToBatchFileActionResult(should_stop=True)
    review_state = _validate_pathless_review_line_action(
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
        if _refuse_live_action_for_batch_selection(review_action):
            return _records.ActionScopeResolution(file=file, should_stop=True)
        if line_ids is None:
            reviewed_file = resolve_review_file_for_bare_whole_file_action(
                review_action,
                source=_records.ReviewSource.FILE_VS_HEAD,
            )
            return _records.ActionScopeResolution(file=reviewed_file if reviewed_file is not None else file)
        review_state = _validate_pathless_review_line_action(
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
        review_state = _validate_pathless_review_line_action(
            review_action,
            line_id_specification,
            source=source,
            batch_name=batch_name,
        )
        if _refuse_live_action_for_batch_selection(review_action):
            return _records.ActionScopeResolution(file=file, review_state=review_state, should_stop=True)
        return _records.ActionScopeResolution(file=file, review_state=review_state)

    if _refuse_live_action_for_batch_selection(review_action):
        return _records.ActionScopeResolution(file=file, should_stop=True)

    review_state = _validate_pathless_review_line_action(
        review_action,
        line_id_specification,
        source=source,
        batch_name=batch_name,
    )
    return _records.ActionScopeResolution(file=file, review_state=review_state)
