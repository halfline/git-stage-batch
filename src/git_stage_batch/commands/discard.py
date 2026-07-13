"""Discard command implementation."""

from __future__ import annotations

from ..batch.state.batch_names import validate_batch_name
from ..core.replacement import (
    ReplacementPayload,
)
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ..data.selected_change.clear_reasons import (
    refuse_bare_action_after_auto_advance_disabled,
    refuse_bare_action_after_file_list,
)
from ..data.file_review.records import FileReviewAction, ReviewSource
from ..data.file_review.action_refusals import (
    refuse_ambiguous_bare_action_after_partial_file_review,
    refuse_live_action_for_batch_selection,
)
from ..data.file_review.action_scope import (
    resolve_live_line_action_scope,
    resolve_live_to_batch_action_scope,
)
from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .selection import discard_to_batch_action as _discard_to_batch_action
from .selection import discard_line_action as _discard_line_action
from .selection import (
    discard_line_replacement_action as _discard_line_replacement_action,
)
from .selection import selected_change_discarding as _selected_change_discarding
from .selection import selected_file_discarding as _selected_file_discarding
from .file_scope import discard_file as _file_scope_discard_file
from .file_scope import discard_file_replacement as _file_scope_discard_file_replacement


def command_discard(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Discard the selected hunk or binary file from the working tree."""

    log_journal("command_discard_start", quiet=quiet)

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
        return
    if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
        return
    refuse_bare_action_after_file_list("discard")
    refuse_bare_action_after_auto_advance_disabled("discard")

    if read_selected_change_kind() == SelectedChangeKind.FILE:
        _selected_file_discarding.discard_selected_file(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    _selected_change_discarding.discard_selected_change(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def command_discard_file(
    file: str,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard the entire specified file from the working tree.

    Args:
        file: File path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If explicit path, uses that file.
    """

    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
            return
        refuse_bare_action_after_file_list("discard --file")
        refuse_bare_action_after_auto_advance_disabled("discard --file")

    _file_scope_discard_file.discard_file_changes(
        file,
        auto_advance=auto_advance,
    )


def command_discard_file_as(
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Replace one live file-scoped working-tree file with explicit text."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    if file is None or file == "":
        if refuse_live_action_for_batch_selection(FileReviewAction.DISCARD):
            return
        if refuse_ambiguous_bare_action_after_partial_file_review(FileReviewAction.DISCARD):
            return
        refuse_bare_action_after_file_list("discard --file --as")
        refuse_bare_action_after_auto_advance_disabled("discard --file --as")

    _file_scope_discard_file_replacement.discard_file_as_replacement(
        replacement_text,
        file,
        auto_advance=auto_advance,
    )


def command_discard_line(
    line_id_specification: str,
    file: str | None = None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard only the specified lines from the working tree.

    Args:
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
        file: Optional file path for file-scoped operation.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.DISCARD,
        action_command=f"discard --line {line_id_specification}",
        line_id_specification=line_id_specification,
        file=file,
        validate_pathless_before_live_guard=True,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state
    _discard_line_action.discard_live_line_selection(
        line_id_specification,
        file,
        review_state=review_state,
        auto_advance=auto_advance,
    )


def command_discard_to_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Save to batch then discard from working tree.

    Args:
        batch_name: Name of batch to save to
        line_ids: Optional line IDs to discard
        file: Optional file path for file-scoped operations.
              If empty string, uses selected hunk's file.
              If None, uses selected hunk (cached state).
        quiet: Suppress output
        advance: When quiet, advance the selection after discarding this file.
        auto_advance: Whether to select the next hunk after this action.

    Returns:
        Number of hunks saved to the batch and discarded.
    """
    require_git_repository()
    validate_batch_name(batch_name)
    require_session_started()
    ensure_state_directory_exists()
    original_file_scope = file
    scope_resolution = resolve_live_to_batch_action_scope(
        FileReviewAction.DISCARD_TO_BATCH,
        command_name="discard",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
    )
    if scope_resolution.should_stop:
        return 0
    file = scope_resolution.file
    review_state = scope_resolution.review_state
    return _discard_to_batch_action.execute_discard_to_batch_action(
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        original_file_scope=original_file_scope,
        review_state=review_state,
        quiet=quiet,
        advance=advance,
        auto_advance=auto_advance,
    )


def command_discard_line_as_to_batch(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    file: str | None = None,
    *,
    no_edge_overlap: bool = False,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save replacement text to batch, then discard the original selection locally."""
    require_git_repository()
    validate_batch_name(batch_name)
    require_session_started()
    ensure_state_directory_exists()
    scope_resolution = resolve_live_line_action_scope(
        FileReviewAction.DISCARD_TO_BATCH,
        action_command=f"discard --to {batch_name} --line {line_id_specification} --as",
        line_id_specification=line_id_specification,
        file=file,
        source=ReviewSource.FILE_VS_HEAD,
    )
    if scope_resolution.should_stop:
        return
    review_state = scope_resolution.review_state

    _discard_line_replacement_action.discard_live_line_replacement_to_batch(
        batch_name,
        line_id_specification,
        replacement_text,
        file,
        review_state=review_state,
        no_edge_overlap=no_edge_overlap,
        quiet=quiet,
        auto_advance=auto_advance,
    )
